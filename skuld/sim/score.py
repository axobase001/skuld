"""S5 scoring (scope §5.4; prereg H1-H5): deterministic, reads only the hidden items and the trace.

A trace entry is {at_h, kind, level, text}: an artifact prepared (PREPARE), a delivery sent (DELIVER)
or an execution (EXECUTE). An action label is the pair (kind, level)."""
from collections import Counter

from skuld.sim.world import EPISODE_HOURS

MISSED_HOURS = EPISODE_HOURS


def matches(item, entry):
    """An entry meets an item: same kind, level >= the item's minimum, at or after its trigger, every marker in its text."""
    return (entry["kind"] == item["action"] and entry["level"] >= item["min_level"] and entry["at_h"] >= item["trigger_at"]
            and all(m in entry["text"] for m in item["markers"]))


def regret(items, trace):
    """CUMULATIVE_RESPONSIBILITY_REGRET: lateness hours x severity per item; a missed item costs MISSED_HOURS x severity."""
    total = 0.0
    for item in items:
        times = [e["at_h"] for e in trace if matches(item, e)]
        total += (max(0.0, min(times) - item["deadline"]) if times else MISSED_HOURS) * item["severity"]
    return total


def useful(items, delivery):
    """A delivery is useful when it carries every marker of some item, at or after that item's trigger. A late but
    relevant delivery is useful: lateness is the regret's business (Claude's GATE_S5 review)."""
    return any(delivery["at_h"] >= i["trigger_at"] and all(m in delivery["text"] for m in i["markers"]) for i in items)


def interruption(items, trace):
    """Share of deliveries that are useful to no item; 0.0 without deliveries."""
    deliveries = [e for e in trace if e["kind"] == "DELIVER"]
    return sum(not useful(items, d) for d in deliveries) / len(deliveries) if deliveries else 0.0


def brier(forecasts, split_h):
    """(at_h, probability, outcome) forecasts -> (Brier before split_h, Brier from split_h); None for an empty half."""
    halves = ([(p - o) ** 2 for at, p, o in forecasts if at < split_h], [(p - o) ** 2 for at, p, o in forecasts if at >= split_h])
    return tuple(sum(h) / len(h) if h else None for h in halves)


def label(x):
    return tuple(x) if isinstance(x, (list, tuple)) else x


def tvd(a, b):
    """Total variation distance between the label distributions of two action sequences."""
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    pa, pb = Counter(map(label, a)), Counter(map(label, b))
    return 0.5 * sum(abs(pa[k] / len(a) - pb[k] / len(b)) for k in set(pa) | set(pb))


__all__ = ["regret", "interruption", "useful", "brier", "tvd", "matches", "MISSED_HOURS"]
