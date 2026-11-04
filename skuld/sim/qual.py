"""S5 world-qualification arms (GATE_S5 §4). No runtime and no model call; the same limits as the
runtime: a round every wake_interval(TAU), at most SELECT_MAX_CHANNELS observations and one action a
round, the D8 daily delivery caps (urgent = a responsibility due within URGENT_HOURS).
- ORACLE sees the hidden list: each round it does the most severe open item (earliest deadline first)
  with the right markers, and delivers only when a delivery is asked for.
- TYPE_ORACLE knows each object's hazard class (rate, severity), not its events: it observes by
  severity x rate x hours since last seen (bound objects only), acts on what it has seen.
- COVERAGE observes every object in fixed rotation, acts on what it has seen.
TYPE_ORACLE and COVERAGE deliver every action while the caps allow, as the runtime does, and know
only an object's current value, so an item superseded by a later change can no longer be met."""
from skuld.app.loop_runtime import NON_URGENT_DAILY_MAX, SELECT_MAX_CHANNELS, URGENT_DAILY_MAX, URGENT_HOURS, wake_interval
from skuld.sim import score

WORLD_ARMS = ("ORACLE", "TYPE_ORACLE", "COVERAGE")
TAU = 0.5


def _marker(world, oid, event):
    """The marker an object shows after `event` (None: its initial state), as the world's items define it."""
    return event["payload"]["marker"] if event else world.setup[oid]["marker"]


def run(arm, world, hours):
    """-> (trace, action labels) of one world-level arm."""
    step = wake_interval(TAU) / 3600
    due_of = {r["object_id"]: r["due_at"] for r in world.responsibilities}
    known = {o["object_id"]: 0.0 for o in world.objects}            # time of the change last seen
    marker = {oid: _marker(world, oid, None) for oid in known}       # the marker that change shows
    seen_at = {o["object_id"]: 0.0 for o in world.objects}
    events = world.events
    trace, labels, sent, t, done, handled = [], [], {}, 0.0, set(), set()
    while t + step <= hours + 1e-9:
        t = round(t + step, 6)
        latest, last_event = {}, {}
        for e in events:
            if e["at_h"] <= t:
                latest[e["object_id"]], last_event[e["object_id"]] = e["at_h"], e
        if arm != "ORACLE":
            if arm == "TYPE_ORACLE":
                pool = [o for o in world.objects if o["object_id"] in due_of]
                pool.sort(key=lambda o: (-(o["severity"] * o["rate_per_day"] * (t - seen_at[o["object_id"]])), o["object_id"]))
            else:
                pool = sorted(world.objects, key=lambda o: (seen_at[o["object_id"]], o["object_id"]))
            for o in pool[:SELECT_MAX_CHANNELS]:
                oid = o["object_id"]
                seen_at[oid] = t
                known[oid] = latest.get(oid, 0.0)
                marker[oid] = _marker(world, oid, last_event.get(oid))
        open_items = [i for i in world.items if i["trigger_at"] <= t and i["item_id"] not in done
                      and (arm == "ORACLE" or (known[i["object_id"]] >= i["trigger_at"] and i["markers"] == [marker[i["object_id"]]]))]
        day = int(t // 24)

        def capped(i):
            urgency = "URGENT" if due_of.get(i["object_id"], 1e9) - t <= URGENT_HOURS else "NORMAL"
            return sent.get((day, urgency), 0) >= (URGENT_DAILY_MAX if urgency == "URGENT" else NON_URGENT_DAILY_MAX)

        # CHANGE_NOTE_10 (draft): never re-pick an item the arm cannot advance (the cap-blocked livelock)
        if arm == "ORACLE":
            choices = [i for i in open_items if not (i["action"] == "DELIVER" and capped(i))]
        else:
            choices = [i for i in open_items if (i["object_id"], i["trigger_at"]) not in handled]
        if not choices:
            continue
        item = min(choices, key=lambda i: (-i["severity"], i["deadline"], i["item_id"]))
        handled.add((item["object_id"], item["trigger_at"]))
        text = "更新：" + "；".join(item["markers"])
        trace.append({"at_h": t, "kind": "PREPARE", "level": 2, "text": text})
        labels.append(["PREPARE", 2])
        oid = item["object_id"]
        wants = any(i["action"] == "DELIVER" and i["object_id"] == oid and i["trigger_at"] == item["trigger_at"] for i in open_items)
        if arm != "ORACLE" or wants:
            urgency = "URGENT" if due_of.get(oid, 1e9) - t <= URGENT_HOURS else "NORMAL"
            cap = URGENT_DAILY_MAX if urgency == "URGENT" else NON_URGENT_DAILY_MAX
            if sent.get((day, urgency), 0) < cap:
                sent[(day, urgency)] = sent.get((day, urgency), 0) + 1
                trace.append({"at_h": t, "kind": "DELIVER", "level": 0, "text": text})
        done.update(i["item_id"] for i in open_items if any(score.matches(i, e) for e in trace[-2:]))
    return trace, labels


__all__ = ["run", "WORLD_ARMS"]
