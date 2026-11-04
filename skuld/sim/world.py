"""S5 simulated world (GATE_S5 §2): a pure function of WorldSpec(family, absence, seed).

A world is one 7-day episode; times are hours from its start and day d's night is [24d, 24d + 8).
- objects: the sources the user's work depends on, each its own channel, with a hazard class
  (changes per day) and a severity; some are asymptomatic (the user never mentions them);
- responsibilities: the user's own commitments, each bound to one object's observable, with a due time;
- events: when each object changes and to what (one Poisson process per object);
- presence: present / absent / asleep (H: one or two short absences a day; D: one 3-5 day trip);
- intents: what the user says, only while present (a paraphraser turns them into utterances);
- items: the hidden checklist the scorer uses. The agent never sees it.

Each change of a bound object before its due time asks for an updated artifact (PREPARE, the new
value as marker) within a severity-dependent window. If the user is away at that moment, it also
asks for a delivery (DELIVER) within DELIVER_WINDOW_HOURS, for severity-DELIVER_SEVERITY changes only and at most
DELIVER_PER_DAY a day (the earliest; CHANGE_NOTE_10)."""
import hashlib
import json
import math
import random
from dataclasses import dataclass

from skuld.app.loop_runtime import wake_interval

FAMILIES = ("F1_quote", "F2_calendar", "F3_files")
ABSENCES = ("H", "D")
EPISODE_HOURS = 168
NIGHT_HOURS = 8
OBJECTS = 15
RESPONSIBILITIES = (6, 8)
HAZARDS = (("volatile", 3.0), ("steady", 0.75), ("quiet", 0.15))   # changes per day: spread 20x
SEVERITIES = (5.0, 2.0, 1.0)                                       # spread 5x
PREPARE_WINDOW_HOURS = {5.0: 0.5, 2.0: 1.5, 1.0: 3.0}
DELIVER_WINDOW_HOURS = 3.0
URGENT_HOURS, DELIVER_CAPS = 24, {"URGENT": 2, "NORMAL": 3}
ROUND_HOURS = wake_interval(0.5) / 3600        # the runtime's cadence at the pre-registered tau 0.5: rounds at k x ROUND_HOURS   # D8 (skuld.app.loop_runtime): urgent / non-urgent daily caps
DELIVER_SEVERITY, DELIVER_PER_DAY = 5.0, 3      # CHANGE_NOTE_10 (a'): a delivery is asked only for the most severe changes, at most
                                               # 3 a day, so every item is feasible within the D8 caps (Claude: the world must be feasible)
ASYMPTOMATIC = 6                                                   # objects the user never mentions
MENTION_DELAY_HOURS, MENTION_WITHIN_HOURS = 0.5, 12.0
# F1 quotes are published through a calendar feed per supplier: the product allows exactly one HTTP server (S0 frozen
# test_single_http_server) and http_json fetches with module-level httpx, so the simulated world serves no HTTP.
CHANNEL = {"F1_quote": "ics_calendar", "F2_calendar": "ics_calendar", "F3_files": "local_directory"}
LETTERS = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰"


@dataclass(frozen=True)
class WorldSpec:
    family: str
    absence: str
    seed: int

    def __post_init__(self):
        if self.family not in FAMILIES or self.absence not in ABSENCES or type(self.seed) is not int:
            raise ValueError("WorldSpec(family in FAMILIES, absence in ABSENCES, integer seed)")


class World:
    def __init__(self, spec, objects, setup, responsibilities, events, presence, intents, items):
        self.spec, self.objects, self.setup, self.responsibilities = spec, objects, setup, responsibilities
        self.events, self.presence, self.intents, self.items = events, presence, intents, items

    def state_at(self, h):
        return next((b["state"] for b in self.presence if b["start"] <= h < b["end"]), "present")

    def object(self, object_id):
        return next(o for o in self.objects if o["object_id"] == object_id)

    def canonical(self):
        return json.dumps({"spec": [self.spec.family, self.spec.absence, self.spec.seed], "objects": self.objects, "setup": self.setup,
                           "responsibilities": self.responsibilities, "events": self.events, "presence": self.presence,
                           "intents": self.intents, "items": self.items}, ensure_ascii=False, sort_keys=True)


def _value(family, rng, name):
    """-> (observed value, marker): the marker is what an updated artifact has to carry."""
    if family == "F1_quote":
        v = str(rng.randint(1000, 9899))
        return f"{name}报价 {v} 元", v
    if family == "F2_calendar":
        room = f"{rng.choice('ABCDEFGH')}-{rng.randint(101, 989)}"
        return f"{name} · 会议室 {room}", room
    return f"{name}：第 {rng.randint(2, 99)} 稿，{rng.randint(1, 9)} 处修改", None   # F3: the marker is the file name (set below)


def _presence(absence, rng):
    hours = ["present"] * EPISODE_HOURS
    for d in range(EPISODE_HOURS // 24):
        for h in range(24 * d, 24 * d + NIGHT_HOURS):
            hours[h] = "asleep"
    if absence == "H":
        for d in range(EPISODE_HOURS // 24):
            placed = []
            for _ in range(rng.randint(1, 2)):
                for _try in range(20):
                    length, start = rng.randint(1, 3), 24 * d + rng.randint(NIGHT_HOURS + 1, 21)
                    end = start + length
                    if end <= 24 * d + 23 and all(end + 1 <= s or start >= e + 1 for s, e in placed):
                        placed.append((start, end))
                        break
            for s, e in placed:
                for h in range(s, e):
                    hours[h] = "absent"
    else:
        start = 24 * rng.randint(1, 2) + rng.randint(9, 15)
        lengths = [n for n in range(72, min(120, EPISODE_HOURS - 1 - start) + 1) if (start + n) % 24 >= NIGHT_HOURS]
        end = start + rng.choice(lengths)
        for h in range(start, end):
            hours[h] = "absent"
    blocks = []
    for h, s in enumerate(hours):
        if blocks and blocks[-1]["state"] == s:
            blocks[-1]["end"] = h + 1
        else:
            blocks.append({"start": h, "end": h + 1, "state": s})
    return blocks


def build(spec):
    rng = random.Random(f"skuld-s5-world:{spec.family}:{spec.absence}:{spec.seed}")
    fam, kind = spec.family, CHANNEL[spec.family]
    objects, setup = [], {}
    quiet = set(rng.sample(range(OBJECTS), ASYMPTOMATIC))
    for i in range(OBJECTS):
        hazard, rate = HAZARDS[i % len(HAZARDS)]
        sev = SEVERITIES[(i // len(HAZARDS)) % len(SEVERITIES)]
        oid = f"o{i:02d}"
        name = {"F1_quote": f"{LETTERS[i]}号供应商", "F2_calendar": f"{LETTERS[i]}项目评审会", "F3_files": f"{LETTERS[i]}组设计稿"}[fam]
        key = {"F1_quote": f"event:{oid}:summary", "F2_calendar": f"event:{oid}:summary", "F3_files": f"design_{oid}.md"}[fam]
        value, marker = _value(fam, rng, name)
        objects.append({"object_id": oid, "channel_kind": kind, "rate_per_day": rate, "severity": sev, "symptomatic": i not in quiet,
                        "hazard_class": hazard})
        setup[oid] = {"name": name, "key": key, "initial": value, "marker": marker or key}
    presence = _presence(spec.absence, rng)
    world = World(spec, objects, setup, [], [], presence, [], [])

    # the user's responsibilities: one object of each hazard class, the rest drawn at random
    n = rng.randint(*RESPONSIBILITIES)
    bound = [rng.choice(range(h, OBJECTS, len(HAZARDS))) for h in range(len(HAZARDS))]
    bound += rng.sample([i for i in range(OBJECTS) if i not in bound], n - len(bound))
    dues = {f"o{i:02d}": float(24 * rng.randint(1, 6) + rng.randint(10, 20)) for i in sorted(bound)}

    # changes: one Poisson process per object
    events = []
    for o in objects:
        t = 0.0
        while True:
            t += rng.expovariate(o["rate_per_day"] / 24)
            if t >= EPISODE_HOURS - 0.5:
                break
            value, marker = _value(fam, rng, setup[o["object_id"]]["name"])
            events.append({"at_h": round(t, 2), "object_id": o["object_id"], "kind": "change",
                           "payload": {"value": value, "marker": marker or setup[o["object_id"]]["key"]}})
    trip = next((b for b in presence if b["state"] == "absent"), None) if spec.absence == "D" else None
    if trip is not None:                                    # the trip must matter: one bound change inside it
        oid = max(dues, key=lambda k: (dues[k], k))
        if dues[oid] < trip["start"] + 6:
            dues[oid] = float(min(EPISODE_HOURS - 1, trip["start"] + 30))
        value, marker = _value(fam, rng, setup[oid]["name"])
        events.append({"at_h": float(trip["start"] + 2), "object_id": oid, "kind": "change",
                       "payload": {"value": value, "marker": marker or setup[oid]["key"]}})
    responsibilities = []
    for j, oid in enumerate(sorted(dues)):
        due, name, key = dues[oid], setup[oid]["name"], setup[oid]["key"]
        day, hour = int(due) // 24 + 1, int(due) % 24
        text = {"F1_quote": f"在第 {day} 天 {hour}:00 前，把{name}的最新报价整理进给客户的报价单",
                "F2_calendar": f"在第 {day} 天 {hour}:00 前，准备好{name}的会议安排和材料",
                "F3_files": f"在第 {day} 天 {hour}:00 前，跟进{name}（{key}）的修改并整理评审意见"}[fam]
        responsibilities.append({"key": f"r{j}_{oid}", "kind": {"F1_quote": "Commitment", "F2_calendar": "Task", "F3_files": "Goal"}[fam],
                                 "text": text, "due_at": due, "object_id": oid})
    world.responsibilities = responsibilities
    events.sort(key=lambda e: (e["at_h"], e["object_id"]))
    world.events = events

    # hidden items
    items, due_of, k, delivers = [], {r["object_id"]: r["due_at"] for r in responsibilities}, 0, {}
    for e in events:
        oid = e["object_id"]
        if oid not in due_of or e["at_h"] >= due_of[oid]:
            continue
        o, marker = world.object(oid), e["payload"]["marker"]
        asks = [("PREPARE", 2, PREPARE_WINDOW_HOURS[o["severity"]])]
        # CHANGE_NOTE_11: a delivery counts against the D8 cap of its urgency class (it may turn urgent within its window), on
        # the day of the first round that can deliver it (D8 counts by delivery day; a late-evening change is delivered after midnight)
        first_round = math.ceil(e["at_h"] / ROUND_HOURS - 1e-9) * ROUND_HOURS
        day = int(first_round // 24)
        urgency = "URGENT" if due_of[oid] - first_round <= URGENT_HOURS + DELIVER_WINDOW_HOURS else "NORMAL"
        if (world.state_at(e["at_h"]) == "absent" and o["severity"] == DELIVER_SEVERITY and delivers.get(day, 0) < DELIVER_PER_DAY
                and delivers.get((day, urgency), 0) < DELIVER_CAPS[urgency]):
            delivers[day] = delivers.get(day, 0) + 1
            delivers[(day, urgency)] = delivers.get((day, urgency), 0) + 1
            asks.append(("DELIVER", 0, DELIVER_WINDOW_HOURS))
        for action, level, window in asks:
            deadline = round(min(float(EPISODE_HOURS), e["at_h"] + window), 2)
            if deadline <= e["at_h"]:
                continue
            k += 1
            items.append({"item_id": "item-" + hashlib.sha1(f"{spec.family}:{spec.absence}:{spec.seed}:{k}".encode()).hexdigest()[:12],
                          "object_id": oid, "trigger_at": e["at_h"], "deadline": deadline, "action": action, "min_level": level,
                          "severity": o["severity"], "markers": [marker], "hazard_class": o["hazard_class"]})
    world.items = items

    # what the user says, only while present
    intents = []
    for d in range(EPISODE_HOURS // 24):
        for at in (24 * d + 9.5, 24 * d + 19.5):
            if world.state_at(at) == "present":
                intents.append({"at_h": at, "intent": "早上好，今天有什么需要我注意的吗？" if at % 24 < 12 else "今天的事情还有什么没跟上的吗？",
                                "object_id": None})
                break
    for e in events:
        o = world.object(e["object_id"])
        if not o["symptomatic"] or e["object_id"] not in due_of:
            continue
        at = e["at_h"] + MENTION_DELAY_HOURS
        while at < min(EPISODE_HOURS, e["at_h"] + MENTION_WITHIN_HOURS) and world.state_at(at) != "present":
            at = float(int(at) + 1) + 0.25
        if at < min(EPISODE_HOURS, e["at_h"] + MENTION_WITHIN_HOURS) and world.state_at(at) == "present":
            intents.append({"at_h": round(at, 2), "intent": f"我刚听说{setup[o['object_id']]['name']}那边有变化，你留意一下。",
                            "object_id": o["object_id"]})
    intents.sort(key=lambda i: (i["at_h"], i["intent"]))
    world.intents = intents
    return world


__all__ = ["WorldSpec", "World", "build", "FAMILIES", "ABSENCES", "EPISODE_HOURS"]
