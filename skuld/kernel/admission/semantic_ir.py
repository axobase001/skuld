"""Strict parser and typed schema for V2.0.0 lossless Semantic Event IR."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from skuld.kernel.admission.semantic_prompt import SEMANTIC_EVENT_COMPILER_SYSTEM_PROMPT

SEMANTIC_EVENT_IR_SCHEMA_VERSION = "skuld-v200-semantic-event-ir/v2"
SEMANTIC_COMPILER_PROTOCOL_VERSION = "skuld-v201-semantic-event-compiler/v40"
SEMANTIC_EVENT_IR_PARSER_VERSION = "skuld-v200-semantic-event-ir-parser/v1"

FORBIDDEN_IR_FIELDS = frozenset(
    {
        "canonical_entity_id",
        "database_row_id",
        "belief_id",
        "prediction_id",
        "action_id",
        "commit_decision",
        "world_state_truth",
        "tool_name",
        "tool_parameters",
    }
)


class SemanticEventKind(StrEnum):
    ASSERTION = "ASSERTION"
    STATE_ASSERTION = "STATE_ASSERTION"
    SCHEDULE_ASSERTION = "SCHEDULE_ASSERTION"
    INTENTION = "INTENTION"
    COMMITMENT = "COMMITMENT"
    PREFERENCE = "PREFERENCE"
    GOAL_EXPRESSION = "GOAL_EXPRESSION"
    CONSTRAINT_EXPRESSION = "CONSTRAINT_EXPRESSION"
    OUTCOME_REPORT = "OUTCOME_REPORT"
    CORRECTION = "CORRECTION"
    WITHDRAWAL = "WITHDRAWAL"
    NEGATION = "NEGATION"
    QUOTE = "QUOTE"
    REPORTED_SPEECH = "REPORTED_SPEECH"
    HYPOTHETICAL = "HYPOTHETICAL"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    CONDITIONAL = "CONDITIONAL"
    QUESTION = "QUESTION"
    REQUEST = "REQUEST"
    APPROVAL_EXPRESSION = "APPROVAL_EXPRESSION"
    REJECTION_EXPRESSION = "REJECTION_EXPRESSION"
    CAUSAL_CLAIM = "CAUSAL_CLAIM"
    TEMPORAL_UPDATE = "TEMPORAL_UPDATE"
    QUANTITY_UPDATE = "QUANTITY_UPDATE"
    IDENTITY_ASSERTION = "IDENTITY_ASSERTION"
    RELATION_ASSERTION = "RELATION_ASSERTION"


SEMANTIC_EVENT_KINDS = tuple(item.value for item in SemanticEventKind)


class EventModality(StrEnum):
    ASSERTED = "ASSERTED"
    INTENDED = "INTENDED"
    COMMITTED = "COMMITTED"
    HYPOTHETICAL = "HYPOTHETICAL"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    CONDITIONAL = "CONDITIONAL"
    QUESTIONED = "QUESTIONED"
    QUOTED = "QUOTED"
    REPORTED = "REPORTED"
    UNRESOLVED = "UNRESOLVED"


class EventPolarity(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    DOUBLE_NEGATIVE = "DOUBLE_NEGATIVE"
    UNRESOLVED = "UNRESOLVED"


class SourceMode(StrEnum):
    DIRECT = "DIRECT"
    QUOTED = "QUOTED"
    REPORTED = "REPORTED"
    COPIED_THIRD_PARTY = "COPIED_THIRD_PARTY"
    CODE_OR_LOG = "CODE_OR_LOG"
    UNRESOLVED = "UNRESOLVED"


class TemporalExpressionKind(StrEnum):
    RELATIVE_DATETIME = "RELATIVE_DATETIME"
    RELATIVE_DATE = "RELATIVE_DATE"
    RELATIVE_TIME = "RELATIVE_TIME"
    ABSOLUTE_DATETIME_SURFACE = "ABSOLUTE_DATETIME_SURFACE"
    DATE_SURFACE = "DATE_SURFACE"
    TIME_SURFACE = "TIME_SURFACE"
    DURATION_SURFACE = "DURATION_SURFACE"
    INTERVAL_SURFACE = "INTERVAL_SURFACE"
    RECURRENCE_SURFACE = "RECURRENCE_SURFACE"
    UNRESOLVED = "UNRESOLVED"


class QuantityExpressionKind(StrEnum):
    COUNT = "COUNT"
    DURATION = "DURATION"
    CURRENCY = "CURRENCY"
    PERCENTAGE = "PERCENTAGE"
    RANGE = "RANGE"
    APPROXIMATE = "APPROXIMATE"
    ORDINAL = "ORDINAL"
    UNRESOLVED = "UNRESOLVED"


StrictText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
EntityLocalId = Annotated[str, StringConstraints(pattern=r"^E[1-9][0-9]*$")]
EventLocalId = Annotated[str, StringConstraints(pattern=r"^V[1-9][0-9]*$")]
TemporalLocalId = Annotated[str, StringConstraints(pattern=r"^T[1-9][0-9]*$")]
QuantityLocalId = Annotated[str, StringConstraints(pattern=r"^Q[1-9][0-9]*$")]
RelationLocalId = Annotated[str, StringConstraints(pattern=r"^R[1-9][0-9]*$")]
AgentReference = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:USER|UNKNOWN|UNRESOLVED|E[1-9][0-9]*|V[1-9][0-9]*)$"
    ),
]
SemanticValueReference = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:USER|UNKNOWN|UNRESOLVED|E[1-9][0-9]*|V[1-9][0-9]*|T[1-9][0-9]*|Q[1-9][0-9]*)$"
    ),
]
ParticipantReference = Annotated[
    str,
    StringConstraints(pattern=r"^(?:E|V|T|Q)[1-9][0-9]*$"),
]


class _FrozenIRModel(BaseModel):
    # JSON arrays decode as lists and enum tokens decode as strings. Pydantic
    # performs only those schema-directed conversions; all object boundaries
    # remain closed and every scalar field is explicitly string-constrained.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=False)


class EntityMentionIR(_FrozenIRModel):
    local_id: EntityLocalId
    surface: StrictText
    type_hint: Annotated[str, StringConstraints(min_length=1, max_length=80)]


class TemporalExpressionIR(_FrozenIRModel):
    local_id: TemporalLocalId
    surface: StrictText
    kind: TemporalExpressionKind


class QuantityExpressionIR(_FrozenIRModel):
    local_id: QuantityLocalId
    surface: StrictText
    kind: QuantityExpressionKind
    unit_surface: StrictText | None = None


class SemanticEventIR(_FrozenIRModel):
    local_id: EventLocalId
    kind: SemanticEventKind
    actor: AgentReference
    relation: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    object: SemanticValueReference
    temporal_refs: tuple[TemporalLocalId, ...] = Field(max_length=16)
    quantity_refs: tuple[QuantityLocalId, ...] = Field(max_length=16)
    modality: EventModality = Field(description="Must match kind: HYPOTHETICAL=HYPOTHETICAL, CONDITIONAL=CONDITIONAL, QUESTION=QUESTIONED, COUNTERFACTUAL=COUNTERFACTUAL, QUOTE=QUOTED, REPORTED_SPEECH=REPORTED. No automatic conversion.")
    polarity: EventPolarity
    source_mode: SourceMode
    speaker: AgentReference
    reporter: AgentReference | None
    target_ref: SemanticValueReference
    source_fragments: tuple[StrictText, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_semantic_modes(self) -> SemanticEventIR:
        requirements = {
            SemanticEventKind.QUOTE: (EventModality.QUOTED, SourceMode.QUOTED),
            SemanticEventKind.REPORTED_SPEECH: (
                EventModality.REPORTED,
                SourceMode.REPORTED,
            ),
            SemanticEventKind.HYPOTHETICAL: (
                EventModality.HYPOTHETICAL,
                None,
            ),
            SemanticEventKind.COUNTERFACTUAL: (
                EventModality.COUNTERFACTUAL,
                None,
            ),
            SemanticEventKind.CONDITIONAL: (EventModality.CONDITIONAL, None),
            SemanticEventKind.QUESTION: (EventModality.QUESTIONED, None),
        }
        expected = requirements.get(self.kind)
        if expected is not None:
            modality, source_mode = expected
            if self.modality is not modality:
                raise ValueError(f"{self.kind.value} requires {modality.value} modality")
            if source_mode is not None and self.source_mode is not source_mode:
                raise ValueError(
                    f"{self.kind.value} requires {source_mode.value} source mode"
                )
        if self.kind is SemanticEventKind.REPORTED_SPEECH and self.reporter is None:
            raise ValueError("REPORTED_SPEECH requires reporter")
        if self.source_mode is SourceMode.REPORTED and self.reporter is None:
            raise ValueError("REPORTED source mode requires reporter")
        if self.kind is SemanticEventKind.NEGATION and self.polarity not in {
            EventPolarity.NEGATIVE,
            EventPolarity.DOUBLE_NEGATIVE,
        }:
            raise ValueError("NEGATION requires negative polarity")
        return self


class RelationParticipantIR(_FrozenIRModel):
    role: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    ref: ParticipantReference


class SemanticRelationIR(_FrozenIRModel):
    local_id: RelationLocalId
    relation_type: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    participants: tuple[RelationParticipantIR, ...] = Field(min_length=2, max_length=16)
    source_fragments: tuple[StrictText, ...] = Field(min_length=1, max_length=16)


class SemanticEventIRDocument(_FrozenIRModel):
    turn_ref: Literal["TURN_LOCAL"]
    entities: tuple[EntityMentionIR, ...] = Field(max_length=64)
    events: tuple[SemanticEventIR, ...] = Field(max_length=64)
    temporals: tuple[TemporalExpressionIR, ...] = Field(max_length=64)
    quantities: tuple[QuantityExpressionIR, ...] = Field(max_length=64)
    relations: tuple[SemanticRelationIR, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def validate_local_graph(self) -> SemanticEventIRDocument:
        groups = (self.entities, self.events, self.temporals, self.quantities, self.relations)
        local_ids = [item.local_id for group in groups for item in group]
        if len(local_ids) != len(set(local_ids)):
            raise ValueError("duplicate local_id in SemanticEventIR")
        available = set(local_ids)
        special = {"USER", "UNKNOWN", "UNRESOLVED"}
        for event in self.events:
            references = (
                event.actor,
                event.object,
                event.speaker,
                event.target_ref,
                *((event.reporter,) if event.reporter else ()),
                *event.temporal_refs,
                *event.quantity_refs,
            )
            dangling = [ref for ref in references if ref not in available | special]
            if dangling:
                raise ValueError(f"dangling local reference: {dangling[0]}")
        for relation in self.relations:
            for participant in relation.participants:
                if participant.ref not in available:
                    raise ValueError(f"dangling local reference: {participant.ref}")
        return self


class ParsedSemanticEventIR(_FrozenIRModel):
    document: SemanticEventIRDocument
    raw_model_output_id: StrictText
    semantic_event_ir_schema_version: Literal[SEMANTIC_EVENT_IR_SCHEMA_VERSION]
    compiler_protocol_version: Literal[SEMANTIC_COMPILER_PROTOCOL_VERSION]
    parser_version: Literal[SEMANTIC_EVENT_IR_PARSER_VERSION]
    input_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    output_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_forbidden_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in FORBIDDEN_IR_FIELDS:
                raise ValueError(f"forbidden SemanticEventIR field: {key}")
            _reject_forbidden_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_fields(child)


def _decode_model_output(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 131_072:
            raise ValueError("SemanticEventIR output exceeds parser limit")
        try:
            decoded = json.loads(
                value,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
        except json.JSONDecodeError as exc:
            raise ValueError("SemanticEventIR output must be one JSON object") from exc
    elif isinstance(value, dict):
        decoded = deepcopy(value)
    else:
        raise TypeError("SemanticEventIR output must be one JSON object")
    if not isinstance(decoded, dict):
        raise TypeError("SemanticEventIR output must be one JSON object")
    _reject_forbidden_fields(decoded)
    return decoded


def _validate_source_surfaces(document: SemanticEventIRDocument, raw_text: str) -> None:
    surfaces = [item.surface for item in document.entities]
    surfaces.extend(item.surface for item in document.temporals)
    surfaces.extend(item.surface for item in document.quantities)
    for event in document.events:
        surfaces.extend(event.source_fragments)
    for relation in document.relations:
        surfaces.extend(relation.source_fragments)
    for surface in surfaces:
        if surface not in raw_text:
            raise ValueError(f"SOURCE_FRAGMENT_NOT_IN_RAW_MESSAGE: {surface}")


def parse_semantic_event_ir(
    value: Any,
    *,
    raw_text: str,
    raw_model_output_id: str,
) -> ParsedSemanticEventIR:
    """Parse once, validate strictly, and never repair or commit semantics."""

    if not raw_text:
        raise ValueError("raw human message is required")
    decoded = _decode_model_output(value)
    document = SemanticEventIRDocument.model_validate(decoded)
    _validate_source_surfaces(document, raw_text)
    document_json = _canonical_json(document.model_dump(mode="json"))
    input_sha256 = _sha256(
        _canonical_json(
            {
                "raw_text_sha256": _sha256(raw_text),
                "raw_model_output_id": raw_model_output_id,
                "document_sha256": _sha256(document_json),
                "semantic_event_ir_schema_version": SEMANTIC_EVENT_IR_SCHEMA_VERSION,
                "compiler_protocol_version": SEMANTIC_COMPILER_PROTOCOL_VERSION,
                "parser_version": SEMANTIC_EVENT_IR_PARSER_VERSION,
            }
        )
    )
    output_sha256 = _sha256(
        _canonical_json(
            {
                "document": document.model_dump(mode="json"),
                "raw_model_output_id": raw_model_output_id,
                "semantic_event_ir_schema_version": SEMANTIC_EVENT_IR_SCHEMA_VERSION,
                "compiler_protocol_version": SEMANTIC_COMPILER_PROTOCOL_VERSION,
                "parser_version": SEMANTIC_EVENT_IR_PARSER_VERSION,
                "input_sha256": input_sha256,
            }
        )
    )
    return ParsedSemanticEventIR(
        document=document,
        raw_model_output_id=raw_model_output_id,
        semantic_event_ir_schema_version=SEMANTIC_EVENT_IR_SCHEMA_VERSION,
        compiler_protocol_version=SEMANTIC_COMPILER_PROTOCOL_VERSION,
        parser_version=SEMANTIC_EVENT_IR_PARSER_VERSION,
        input_sha256=input_sha256,
        output_sha256=output_sha256,
    )


def provider_semantic_event_ir_schema() -> dict[str, Any]:
    """Return the exact closed schema exposed to the semantic model."""

    return deepcopy(SemanticEventIRDocument.model_json_schema(mode="validation"))


def semantic_compiler_prompt_sha256() -> str:
    return _sha256(SEMANTIC_EVENT_COMPILER_SYSTEM_PROMPT)


__all__ = [
    "FORBIDDEN_IR_FIELDS",
    "SEMANTIC_COMPILER_PROTOCOL_VERSION",
    "SEMANTIC_EVENT_IR_PARSER_VERSION",
    "SEMANTIC_EVENT_IR_SCHEMA_VERSION",
    "SEMANTIC_EVENT_KINDS",
    "EntityMentionIR",
    "EventModality",
    "EventPolarity",
    "ParsedSemanticEventIR",
    "QuantityExpressionIR",
    "QuantityExpressionKind",
    "RelationParticipantIR",
    "SemanticEventIR",
    "SemanticEventIRDocument",
    "SemanticEventKind",
    "SemanticRelationIR",
    "SourceMode",
    "TemporalExpressionIR",
    "TemporalExpressionKind",
    "parse_semantic_event_ir",
    "provider_semantic_event_ir_schema",
    "semantic_compiler_prompt_sha256",
]
