"""Prediction-error classification types and the JSON round-trip mixin they use.

Copied verbatim in S1 from skuld/legacy when that package was removed (last present at c112d83):
JsonModel, T, _decode and _encode from skuld/legacy/personal_model/schemas.py; PEType,
PEClassification, _probability and _required from skuld/legacy/world_subject_loop/v170_schemas.py.
They are the only legacy definitions skuld.loop.pe needs (provenance/s1_row_models.md).
"""

from __future__ import annotations

import copy
import math
import types
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, TypeVar, Union, get_args, get_origin, get_type_hints

from skuld.kernel.primitives import ensure_utc_iso, new_id, utc_now


T = TypeVar("T", bound="JsonModel")


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return ensure_utc_iso(value)
    if is_dataclass(value):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_encode(v) for v in value]
    return copy.deepcopy(value)


def _decode(annotation: Any, value: Any) -> Any:
    if value is None or annotation is Any:
        return value
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        for option in args:
            if option is type(None):
                continue
            try:
                return _decode(option, value)
            except (TypeError, ValueError):
                continue
        return value
    if origin is list:
        subtype = args[0] if args else Any
        return [_decode(subtype, item) for item in value]
    if origin is tuple:
        subtype = args[0] if args else Any
        return tuple(_decode(subtype, item) for item in value)
    if origin is set:
        subtype = args[0] if args else Any
        return {_decode(subtype, item) for item in value}
    if origin is dict:
        key_type, val_type = args if len(args) == 2 else (Any, Any)
        return {_decode(key_type, key): _decode(val_type, item) for key, item in value.items()}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if isinstance(annotation, type) and issubclass(annotation, JsonModel):
        return annotation.from_dict(value)
    return value


class JsonModel:
    """Small dependency-free JSON round-trip mixin for dataclasses."""

    def to_dict(self) -> dict[str, Any]:
        return _encode(self)

    @classmethod
    def from_dict(cls: type[T], data: dict[str, Any]) -> T:
        if not isinstance(data, dict):
            raise TypeError(f"{cls.__name__}.from_dict expects a dict")
        hints = get_type_hints(cls)
        known = {f.name for f in fields(cls)}
        kwargs = {
            key: _decode(hints.get(key, Any), value)
            for key, value in data.items()
            if key in known
        }
        return cls(**kwargs)


def _required(value: str, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _probability(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be a finite probability")
    return result


class PEType(StrEnum):
    ALEATORIC = "ALEATORIC"
    EPISTEMIC = "EPISTEMIC"
    STRUCTURAL = "STRUCTURAL"
    GOAL_RELEVANT = "GOAL_RELEVANT"


@dataclass(frozen=True)
class PEClassification(JsonModel):
    user_id: str
    prediction_error_id: str
    labels: list[PEType]
    label_probabilities: dict[str, float]
    evidence_reliability: float
    independent_evidence_count: int
    subject_consequence: float
    correctability: float
    identifiability: float
    priority: float
    classifier_version: str
    id: str = field(default_factory=lambda: new_id("pe_classification"))
    created_at: str = field(default_factory=utc_now)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("user_id", "prediction_error_id", "classifier_version"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        labels = list(dict.fromkeys(PEType(item) for item in self.labels))
        if not labels:
            raise ValueError("PE classification requires at least one label")
        probabilities = {
            PEType(key).value: _probability(value, key)
            for key, value in self.label_probabilities.items()
        }
        if any(label.value not in probabilities for label in labels):
            raise ValueError("every PE label requires a probability")
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "label_probabilities", probabilities)
        object.__setattr__(self, "evidence_reliability", _probability(self.evidence_reliability, "evidence_reliability"))
        object.__setattr__(self, "subject_consequence", _probability(self.subject_consequence, "subject_consequence"))
        object.__setattr__(self, "correctability", _probability(self.correctability, "correctability"))
        object.__setattr__(self, "identifiability", _probability(self.identifiability, "identifiability"))
        expected_priority = self.subject_consequence * self.correctability * self.identifiability
        if abs(float(self.priority) - expected_priority) > 1e-6:
            raise ValueError("PE priority must equal consequence * correctability * identifiability")
        object.__setattr__(self, "priority", expected_priority)
        object.__setattr__(self, "independent_evidence_count", max(0, int(self.independent_evidence_count)))
        object.__setattr__(self, "created_at", ensure_utc_iso(self.created_at, default_now=True))
