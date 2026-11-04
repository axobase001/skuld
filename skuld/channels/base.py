"""World channels (D2): every observation of the user's world enters through one interface.

A channel only observes; the runtime validates what it returns once, at this boundary, and
materializes it. Polled channels return a full sample on each observe (a key that disappeared is
reported once with value null); event channels return what happened since their cursor.
"""
from __future__ import annotations

from skuld.kernel.primitives import instant

class ReadBudgetExhausted(Exception):
    """No acquisition was attempted. Caller must keep the job pending."""


DESCRIBE_KEYS = frozenset({"channel_id", "kind", "risk_class", "cost"})
OBSERVATION_KEYS = frozenset({"source_id", "key", "value", "effective_at", "known_at", "fresh", "provenance"})


class Channel:
    kind = ""
    cost = 0
    polled = False  # full samples, materialized as source-report Beliefs

    def __init__(self, runtime, user_id, channel_id, config):
        self.runtime, self.user_id, self.channel_id, self.config = runtime, user_id, channel_id, config

    @classmethod
    def check_config(cls, runtime, config) -> dict:
        raise NotImplementedError

    OPERATIONS = {"READ": 0}  # D6: every write operation declares its risk class

    def describe(self) -> dict:
        return {"channel_id": self.channel_id, "kind": self.kind, "risk_class": 0, "cost": self.cost}

    def operations(self) -> dict:
        """{operation: risk_class} this channel declares."""
        return dict(self.OPERATIONS)

    def check_operation(self, operation, parameters) -> None:
        """Validate a write and its parameters before anything is dispatched; raises ValueError."""
        raise ValueError(f"{self.kind} declares no write operation {operation}")

    def perform(self, operation, parameters, content) -> dict:
        """Carry out a checked write. -> {ok, effect}; ok False means it did not happen. Raising
        means the effect is unknown (IN_DOUBT). The runtime calls this only after the ladder and an
        exact approval or a live mandate allowed it."""
        raise ValueError(f"{self.kind} declares no write operation {operation}")

    def observe(self, cursor) -> tuple[list[dict], object]:
        raise NotImplementedError

    def observation(self, key, value, effective_at, provenance=None) -> dict:
        return {"source_id": self.channel_id, "key": key, "value": value, "effective_at": effective_at,
                "known_at": self.runtime.clock(), "fresh": True, "provenance": provenance or {}}


class PolledChannel(Channel):
    polled = True

    def sample(self) -> dict:
        """-> {key: (value, effective_at, provenance)} for everything the source reports now."""
        raise NotImplementedError

    def observe(self, cursor):
        current = self.sample()
        out = [self.observation(k, v, at, prov) for k, (v, at, prov) in sorted(current.items())]
        gone = sorted(set((cursor or {}).get("keys", [])) - set(current))
        out += [self.observation(k, None, self.runtime.clock(), {"reason": "no longer reported"}) for k in gone]
        return out, {"keys": sorted(current)}


def checked(observations, channel_id) -> list[dict]:
    """Boundary validation of a channel's output; anything malformed rejects the whole batch."""
    if not isinstance(observations, list):
        raise ValueError("a channel returns a list of observations")
    keys = set()
    for o in observations:
        if not isinstance(o, dict) or set(o) != OBSERVATION_KEYS:
            raise ValueError("observation has the wrong fields")
        if o["source_id"] != channel_id or not isinstance(o["key"], str) or not o["key"] or o["key"] in keys:
            raise ValueError("observation key or source is invalid")
        if not isinstance(o["fresh"], bool) or not isinstance(o["provenance"], dict):
            raise ValueError("observation freshness or provenance is invalid")
        instant(o["effective_at"])
        instant(o["known_at"])
        keys.add(o["key"])
    return observations


__all__ = ["Channel", "PolledChannel", "checked", "DESCRIBE_KEYS", "OBSERVATION_KEYS"]
