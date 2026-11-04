"""Predictive user modeling (D3, GATE_S2): host keys, prediction contracts, window outcomes and the
frozen confidence table. Pure functions; the runtime wires them to the graph.

An inferred belief about the user is written only together with a Prediction holding its
contract: which channel, which key, what would confirm it, over what window. Confidence is never
stored per round; it is recomputed at read time from the channel's samples, window by window.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timedelta

KINDS = ("Fact", "Preference", "Relationship", "Hypothesis")
INITIAL_MAX = 0.8
CONFIRM_STEP, CONFIDENCE_CAP = 0.10, 0.95
MISS_FACTOR = 0.7
PRUNE_BELOW, PRUNE_MIN_MISSED = 0.2, 3
WINDOW_MIN, WINDOW_MAX = 3600, 2_592_000
PREDICATES = ("equals", "present")


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def key(slots: dict) -> str:
    """The host's identity of an inference: subject, predicate and object, normalized and hashed.
    The model only fills the slots; it never names the key."""
    if not isinstance(slots, dict) or any(not isinstance(slots.get(s), str) or not normalize(slots[s]) for s in ("subject", "predicate", "object")):
        raise ValueError("inference slots must be non-empty subject, predicate and object")
    parts = [normalize(slots[s]) for s in ("subject", "predicate", "object")]
    return "inf_" + hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:32]


def contract(value) -> dict:
    """Validate a prediction contract; -> its canonical form, or ValueError."""
    if not isinstance(value, dict):
        raise ValueError("an inference needs a prediction contract")
    missing = [f for f in ("channel_id", "observable_key", "predicate", "expected_value", "window_seconds") if f not in value]
    if missing:
        raise ValueError("prediction contract lacks " + ", ".join(missing))
    if not isinstance(value["channel_id"], str) or not isinstance(value["observable_key"], str) or not value["observable_key"]:
        raise ValueError("prediction contract needs a channel and an observable key")
    if value["predicate"] not in PREDICATES:
        raise ValueError("prediction predicate must be equals or present")
    if value["predicate"] == "equals" and value["expected_value"] is None:
        raise ValueError("an equals contract needs an expected value")
    window = value["window_seconds"]
    if type(window) is not int or not WINDOW_MIN <= window <= WINDOW_MAX:
        raise ValueError("prediction window must be 1 hour to 30 days")
    return {f: value[f] for f in ("channel_id", "observable_key", "predicate", "expected_value", "window_seconds")}


def outcomes(spec: dict, start: datetime, now: datetime, samples: list[tuple[datetime, dict]], absent_is_unobserved: bool = False) -> list[str]:
    """One outcome per fully elapsed window since start; stops after the first REFUTED.
    samples are (sampled_at, {key: value}) of the contract's channel; a null value means the key
    was gone, which counts as absence, not as a contrary observation. On an event channel such as
    conversation (absent_is_unobserved) absence is never MISSED: the user not mentioning something
    is not an observation that it is false (Claude's GATE_S4 review ①); it only adds staleness."""
    width = timedelta(seconds=spec["window_seconds"])
    out, lo = [], start
    while lo + width <= now:
        hi = lo + width
        inside = [values for at, values in samples if lo <= at < hi]
        seen = [values[spec["observable_key"]] for values in inside if values.get(spec["observable_key"]) is not None]
        if not inside:
            out.append("UNOBSERVED")
        elif not seen:
            out.append("UNOBSERVED" if absent_is_unobserved else "MISSED")
        elif spec["predicate"] == "present" or any(v == spec["expected_value"] for v in seen):
            out.append("CONFIRMED")
        else:
            out.append("REFUTED")
            break
        lo = hi
    return out


def confidence(initial: float, results: list[str]) -> dict:
    """The frozen table applied in window order."""
    value, staleness = initial, 0
    for r in results:
        if r == "CONFIRMED":
            value = round(min(CONFIDENCE_CAP, value + CONFIRM_STEP), 4)
        elif r == "MISSED":
            value = round(value * MISS_FACTOR, 4)
        elif r == "UNOBSERVED":
            staleness += 1
    missed = results.count("MISSED")
    return {"confidence": value, "staleness": staleness, "refuted": "REFUTED" in results, "missed": missed,
            "prunable": "REFUTED" not in results and value < PRUNE_BELOW and missed >= PRUNE_MIN_MISSED}


__all__ = ["KINDS", "INITIAL_MAX", "PRUNE_BELOW", "PRUNE_MIN_MISSED", "confidence", "contract", "key", "normalize", "outcomes"]
