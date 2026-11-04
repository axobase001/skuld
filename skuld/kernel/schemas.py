"""Typed immutable records for the V2.0.0 Persistent Subject Hypergraph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class EpistemicState(StrEnum):
    DECLARED = "DECLARED"
    OBSERVED = "OBSERVED"
    SUPPORTED = "SUPPORTED"
    CONTESTED = "CONTESTED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"
    REJECTED = "REJECTED"


class ResolutionState(StrEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    INVALIDATED = "INVALIDATED"


class ModalState(StrEnum):
    ACTUAL = "ACTUAL"
    INTENDED = "INTENDED"
    HYPOTHETICAL = "HYPOTHETICAL"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    PREDICTED = "PREDICTED"
    QUOTED = "QUOTED"
    REPORTED = "REPORTED"
    CONDITIONAL = "CONDITIONAL"


class TemporalState(StrEnum):
    CURRENT = "CURRENT"
    FUTURE = "FUTURE"
    PAST = "PAST"
    EXPIRED = "EXPIRED"
    UNRESOLVED_TIME = "UNRESOLVED_TIME"


class AuthorityClass(StrEnum):
    USER_FIRST_PERSON = "USER_FIRST_PERSON"
    USER_REPORTED = "USER_REPORTED"
    EXTERNAL_PRIMARY = "EXTERNAL_PRIMARY"
    EXTERNAL_SECONDARY = "EXTERNAL_SECONDARY"
    TOOL_OBSERVED = "TOOL_OBSERVED"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"
    SYSTEM_INFERRED = "SYSTEM_INFERRED"
    ASSISTANT_GENERATED = "ASSISTANT_GENERATED"
    HISTORICAL_REPORTED = "HISTORICAL_REPORTED"  # D9: imported history


class LifecycleState(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    TOMBSTONED = "TOMBSTONED"


@dataclass(frozen=True, slots=True)
class SourceEvent:
    source_event_id: str
    user_id: str
    source_type: str
    source_ref: str
    received_at: str
    content: dict[str, Any]
    schema_version: str = "skuld-v200-source-event/v1"
    content_hash: str = ""

    def __post_init__(self) -> None:
        for name in (
            "source_event_id",
            "user_id",
            "source_type",
            "source_ref",
            "received_at",
            "schema_version",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        expected = canonical_hash(
            {
                "source_type": self.source_type,
                "source_ref": self.source_ref,
                "received_at": self.received_at,
                "content": self.content,
                "schema_version": self.schema_version,
            }
        )
        if self.content_hash and self.content_hash != expected:
            raise ValueError("source event content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SubjectNode:
    node_id: str
    user_id: str
    node_type: str
    created_at: str
    source_event_id: str
    semantic_identity_key: str
    content: dict[str, Any]
    epistemic_state: EpistemicState
    lifecycle_state: LifecycleState
    modal_state: ModalState
    temporal_state: TemporalState
    resolution_state: ResolutionState
    authority_class: AuthorityClass
    valid_from: str
    valid_until: str | None = None
    superseded_by: str | None = None
    migration_origin: dict[str, Any] | None = None
    schema_version: str = "skuld-v200-subject-node/v1"
    content_hash: str = ""

    def __post_init__(self) -> None:
        for name in (
            "node_id",
            "user_id",
            "node_type",
            "created_at",
            "source_event_id",
            "semantic_identity_key",
            "valid_from",
            "schema_version",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        expected = canonical_hash(
            {
                "node_type": self.node_type,
                "semantic_identity_key": self.semantic_identity_key,
                "content": self.content,
                "epistemic_state": self.epistemic_state.value,
                "lifecycle_state": self.lifecycle_state.value,
                "modal_state": self.modal_state.value,
                "temporal_state": self.temporal_state.value,
                "resolution_state": self.resolution_state.value,
                "authority_class": self.authority_class.value,
                "valid_from": self.valid_from,
                "valid_until": self.valid_until,
                "migration_origin": self.migration_origin,
                "schema_version": self.schema_version,
            }
        )
        if self.content_hash and self.content_hash != expected:
            raise ValueError("subject node content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "epistemic_state",
            "lifecycle_state",
            "modal_state",
            "temporal_state",
            "resolution_state",
            "authority_class",
        ):
            payload[key] = getattr(self, key).value
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SubjectNode:
        return cls(
            **{
                **value,
                "epistemic_state": EpistemicState(value["epistemic_state"]),
                "lifecycle_state": LifecycleState(value["lifecycle_state"]),
                "modal_state": ModalState(value["modal_state"]),
                "temporal_state": TemporalState(value["temporal_state"]),
                "resolution_state": ResolutionState(value["resolution_state"]),
                "authority_class": AuthorityClass(value["authority_class"]),
            }
        )


@dataclass(frozen=True, slots=True)
class SubjectEdge:
    edge_id: str
    user_id: str
    source_node_id: str
    relation_type: str
    target_node_id: str
    created_at: str
    source_event_id: str
    valid_from: str
    valid_until: str | None = None
    superseded_by: str | None = None
    status: str = "ACTIVE"
    schema_version: str = "skuld-v200-subject-edge/v1"
    semantic_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SubjectEdge:
        return cls(**value)


@dataclass(frozen=True, slots=True)
class SubjectHyperedgeParticipant:
    role: str
    node_id: str
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not self.role.strip() or not self.node_id.strip():
            raise ValueError("hyperedge participant role and node_id are required")
        if self.ordinal < 0:
            raise ValueError("hyperedge participant ordinal must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SubjectHyperedge:
    hyperedge_id: str
    user_id: str
    relation_type: str
    participants: tuple[SubjectHyperedgeParticipant, ...]
    created_at: str
    source_event_id: str
    valid_from: str
    valid_until: str | None = None
    superseded_by: str | None = None
    status: str = "ACTIVE"
    schema_version: str = "skuld-v200-subject-hyperedge/v1"
    semantic_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["participants"] = [item.to_dict() for item in self.participants]
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SubjectHyperedge:
        return cls(
            **{
                **value,
                "participants": tuple(
                    SubjectHyperedgeParticipant(**item)
                    for item in value["participants"]
                ),
            }
        )


__all__ = [
    "AuthorityClass",
    "EpistemicState",
    "LifecycleState",
    "ModalState",
    "ResolutionState",
    "SourceEvent",
    "SubjectEdge",
    "SubjectHyperedge",
    "SubjectHyperedgeParticipant",
    "SubjectNode",
    "TemporalState",
    "canonical_hash",
]
