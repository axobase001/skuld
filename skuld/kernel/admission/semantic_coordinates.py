"""Orthogonal, single-pass semantic coordinates for V2.0.1 Phase 5.

The V40 schema remains in :mod:`semantic_ir` so historical Gold, comparator
receipts, and frozen model outputs stay reproducible.  This module is the new
production schema.  Legacy conversion is explicit and offline-only.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from copy import deepcopy
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from skuld.kernel.admission.semantic_coordinate_prompt import SEMANTIC_COORDINATE_COMPILER_SYSTEM_PROMPT
from skuld.kernel.admission.semantic_ir import (
    EntityMentionIR,
    QuantityExpressionIR,
    SemanticRelationIR,
    TemporalExpressionIR,
)

SEMANTIC_COORDINATE_SCHEMA_VERSION = "skuld-v201-semantic-coordinate-system/v1"
SEMANTIC_COORDINATE_COMPILER_PROTOCOL_VERSION = (
    "skuld-v201-single-pass-semantic-compiler/v1"
)
SEMANTIC_COORDINATE_PARSER_VERSION = "skuld-v201-semantic-coordinate-parser/v1"
PROVIDER_SCHEMA_ADAPTER_VERSION = "skuld-v201-provider-schema-adapter/v1"

FORBIDDEN_COORDINATE_FIELDS = frozenset(
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


class EventModality(StrEnum):
    ACTUAL = "ACTUAL"
    INTENDED = "INTENDED"
    HYPOTHETICAL = "HYPOTHETICAL"
    COUNTERFACTUAL = "COUNTERFACTUAL"
    CONDITIONAL = "CONDITIONAL"
    PREDICTED = "PREDICTED"
    UNCERTAIN = "UNCERTAIN"


class EventPolarity(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class SourceMode(StrEnum):
    DIRECT = "DIRECT"
    REPORTED = "REPORTED"
    QUOTED = "QUOTED"
    EXTERNAL = "EXTERNAL"


class SemanticCoordinateEventKind(StrEnum):
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
    QUOTE = "QUOTE"
    REPORTED_SPEECH = "REPORTED_SPEECH"
    QUESTION = "QUESTION"
    REQUEST = "REQUEST"
    APPROVAL_EXPRESSION = "APPROVAL_EXPRESSION"
    REJECTION_EXPRESSION = "REJECTION_EXPRESSION"
    CAUSAL_CLAIM = "CAUSAL_CLAIM"
    TEMPORAL_UPDATE = "TEMPORAL_UPDATE"
    QUANTITY_UPDATE = "QUANTITY_UPDATE"
    IDENTITY_ASSERTION = "IDENTITY_ASSERTION"
    RELATION_ASSERTION = "RELATION_ASSERTION"


StrictText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
EventLocalId = Annotated[str, StringConstraints(pattern=r"^V[1-9][0-9]*$")]
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
TemporalLocalId = Annotated[str, StringConstraints(pattern=r"^T[1-9][0-9]*$")]
QuantityLocalId = Annotated[str, StringConstraints(pattern=r"^Q[1-9][0-9]*$")]


class _FrozenCoordinateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=False)


class SemanticCoordinateEventIR(_FrozenCoordinateModel):
    local_id: EventLocalId
    kind: SemanticCoordinateEventKind
    actor: AgentReference
    relation: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    object: SemanticValueReference
    temporal_refs: tuple[TemporalLocalId, ...] = Field(max_length=16)
    quantity_refs: tuple[QuantityLocalId, ...] = Field(max_length=16)
    modality: EventModality
    polarity: EventPolarity
    source_mode: SourceMode
    speaker: AgentReference
    reporter: AgentReference | None
    target_ref: SemanticValueReference
    source_fragments: tuple[StrictText, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_attribution(self) -> SemanticCoordinateEventIR:
        if self.source_mode is SourceMode.REPORTED and self.reporter is None:
            raise ValueError("REPORTED source mode requires reporter")
        if self.source_mode is not SourceMode.REPORTED and self.reporter is not None:
            raise ValueError("reporter is reserved for REPORTED source content")
        if len(self.source_fragments) != len(set(self.source_fragments)):
            raise ValueError("duplicate source fragment")
        return self


class SemanticCoordinateDocument(_FrozenCoordinateModel):
    turn_ref: Literal["TURN_LOCAL"]
    entities: tuple[EntityMentionIR, ...] = Field(max_length=64)
    events: tuple[SemanticCoordinateEventIR, ...] = Field(max_length=64)
    temporals: tuple[TemporalExpressionIR, ...] = Field(max_length=64)
    quantities: tuple[QuantityExpressionIR, ...] = Field(max_length=64)
    relations: tuple[SemanticRelationIR, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def validate_local_graph(self) -> SemanticCoordinateDocument:
        groups = (
            self.entities,
            self.events,
            self.temporals,
            self.quantities,
            self.relations,
        )
        local_ids = [item.local_id for group in groups for item in group]
        if len(local_ids) != len(set(local_ids)):
            raise ValueError("duplicate local_id in semantic coordinate document")
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
        signatures = [
            (
                event.kind.value,
                event.actor,
                normalize_relation(event.relation),
                event.object,
                event.modality.value,
                event.polarity.value,
                event.source_mode.value,
                event.source_fragments,
            )
            for event in self.events
        ]
        if len(signatures) != len(set(signatures)):
            raise ValueError("duplicate semantic event")
        return self


class ParsedSemanticCoordinateDocument(_FrozenCoordinateModel):
    document: SemanticCoordinateDocument
    raw_model_output_id: StrictText
    schema_version: Literal[SEMANTIC_COORDINATE_SCHEMA_VERSION]
    compiler_protocol_version: Literal[SEMANTIC_COORDINATE_COMPILER_PROTOCOL_VERSION]
    parser_version: Literal[SEMANTIC_COORDINATE_PARSER_VERSION]
    input_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    output_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ProviderSchemaAdapterReceipt(_FrozenCoordinateModel):
    adapter_version: Literal[PROVIDER_SCHEMA_ADAPTER_VERSION]
    provider_profile: StrictText
    canonical_schema_sha256: Annotated[
        str, StringConstraints(pattern=r"^[0-9a-f]{64}$")
    ]
    wire_schema_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    removed_keywords: tuple[StrictText, ...]
    ontology_fields_removed: Literal[False]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def normalize_relation(value: str) -> str:
    """Normalize representation without converting the predicate's meaning."""

    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    normalized = normalized.strip("，。！？、:：;；'\"` ")
    # These are grammatical polarity/aspect operators, not predicate identity.
    prefixes = ("并没有", "没有", "已经", "仍然", "仍", "尚未", "未", "没", "不")
    suffixes = ("过", "着", "了")
    changed = True
    while changed and normalized:
        changed = False
        for prefix in prefixes:
            if normalized.startswith(prefix) and len(normalized) > len(prefix):
                normalized = normalized[len(prefix) :].strip()
                changed = True
                break
    for suffix in suffixes:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            normalized = normalized[: -len(suffix)].strip()
            break
    if not normalized:
        raise ValueError("relation normalizes to empty predicate")
    return normalized.casefold()


def _decode_model_output(value: Any) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, child in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = child
        return result

    if isinstance(value, str):
        if len(value.encode("utf-8")) > 131_072:
            raise ValueError("semantic coordinate output exceeds parser limit")
        try:
            decoded = json.loads(
                value,
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=lambda item: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON constant: {item}")
                ),
            )
        except json.JSONDecodeError as exc:
            raise ValueError("semantic coordinate output must be one JSON object") from exc
    elif isinstance(value, dict):
        decoded = deepcopy(value)
    else:
        raise TypeError("semantic coordinate output must be one JSON object")
    if not isinstance(decoded, dict):
        raise TypeError("semantic coordinate output must be one JSON object")

    def reject_forbidden(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key.casefold() in FORBIDDEN_COORDINATE_FIELDS:
                    raise ValueError(f"forbidden semantic coordinate field: {key}")
                reject_forbidden(child)
        elif isinstance(item, list):
            for child in item:
                reject_forbidden(child)

    reject_forbidden(decoded)
    return decoded


def _validate_source_support(document: SemanticCoordinateDocument, raw_text: str) -> None:
    surfaces = [item.surface for item in document.entities]
    surfaces.extend(item.surface for item in document.temporals)
    surfaces.extend(item.surface for item in document.quantities)
    for relation in document.relations:
        surfaces.extend(relation.source_fragments)
    for event in document.events:
        surfaces.extend(event.source_fragments)
        if event.relation not in raw_text:
            raise ValueError(f"RELATION_NOT_SOURCE_GROUNDED: {event.relation}")
    for surface in surfaces:
        if surface not in raw_text:
            raise ValueError(f"SOURCE_FRAGMENT_NOT_IN_RAW_MESSAGE: {surface}")


def parse_semantic_coordinate_document(
    value: Any,
    *,
    raw_text: str,
    raw_model_output_id: str,
) -> ParsedSemanticCoordinateDocument:
    """Strictly validate one primary model output; never repair or retry."""

    if not raw_text:
        raise ValueError("raw human message is required")
    decoded = _decode_model_output(value)
    document = SemanticCoordinateDocument.model_validate(decoded)
    _validate_source_support(document, raw_text)
    document_json = _canonical_json(document.model_dump(mode="json"))
    input_sha256 = _sha256_json(
        {
            "raw_text_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "raw_model_output_id": raw_model_output_id,
            "document_sha256": hashlib.sha256(document_json.encode("utf-8")).hexdigest(),
            "schema_version": SEMANTIC_COORDINATE_SCHEMA_VERSION,
            "compiler_protocol_version": SEMANTIC_COORDINATE_COMPILER_PROTOCOL_VERSION,
            "parser_version": SEMANTIC_COORDINATE_PARSER_VERSION,
        }
    )
    output_sha256 = _sha256_json(
        {
            "document": document.model_dump(mode="json"),
            "raw_model_output_id": raw_model_output_id,
            "schema_version": SEMANTIC_COORDINATE_SCHEMA_VERSION,
            "compiler_protocol_version": SEMANTIC_COORDINATE_COMPILER_PROTOCOL_VERSION,
            "parser_version": SEMANTIC_COORDINATE_PARSER_VERSION,
            "input_sha256": input_sha256,
        }
    )
    return ParsedSemanticCoordinateDocument(
        document=document,
        raw_model_output_id=raw_model_output_id,
        schema_version=SEMANTIC_COORDINATE_SCHEMA_VERSION,
        compiler_protocol_version=SEMANTIC_COORDINATE_COMPILER_PROTOCOL_VERSION,
        parser_version=SEMANTIC_COORDINATE_PARSER_VERSION,
        input_sha256=input_sha256,
        output_sha256=output_sha256,
    )


def canonical_semantic_coordinate_schema() -> dict[str, Any]:
    return deepcopy(SemanticCoordinateDocument.model_json_schema(mode="validation"))


class ProviderSchemaAdapter:
    """Project the canonical schema onto a provider wire subset.

    The adapter can remove only JSON-Schema validation keywords.  It never
    removes properties, enum values, required fields, or local validation.
    """

    _PROFILE_KEYWORDS: ClassVar[dict[str, frozenset[str]]] = {
        "canonical": frozenset(),
        "openai": frozenset(),
        "openrouter": frozenset({"maxItems", "minItems"}),
        "alibaba": frozenset({"maxItems", "minItems"}),
        "generic": frozenset({"maxItems", "minItems"}),
    }
    _ONTOLOGY_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"properties", "required", "enum", "const", "$ref"}
    )

    @classmethod
    def profile_for_provider(cls, provider_name: str) -> str:
        normalized = "".join(ch for ch in provider_name.casefold() if ch.isalnum())
        if "openrouter" in normalized:
            return "openrouter"
        if "alibaba" in normalized or "dashscope" in normalized:
            return "alibaba"
        if normalized in {"openai", "azureopenai"}:
            return "openai"
        return "generic"

    @classmethod
    def adapt(
        cls, canonical_schema: dict[str, Any], *, provider_profile: str
    ) -> tuple[dict[str, Any], ProviderSchemaAdapterReceipt]:
        profile = provider_profile.casefold().strip()
        if profile not in cls._PROFILE_KEYWORDS:
            raise ValueError(f"unknown provider schema profile: {provider_profile}")
        removals = cls._PROFILE_KEYWORDS[profile]
        if removals & cls._ONTOLOGY_KEYS:
            raise AssertionError("provider adapter attempted ontology mutation")
        wire = deepcopy(canonical_schema)
        removed: list[str] = []

        def walk(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for key in tuple(value):
                    child_path = f"{path}/{key}"
                    if key in removals:
                        del value[key]
                        removed.append(child_path)
                    else:
                        walk(value[key], child_path)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{path}/{index}")

        walk(wire, "$")
        receipt = ProviderSchemaAdapterReceipt(
            adapter_version=PROVIDER_SCHEMA_ADAPTER_VERSION,
            provider_profile=profile,
            canonical_schema_sha256=_sha256_json(canonical_schema),
            wire_schema_sha256=_sha256_json(wire),
            removed_keywords=tuple(sorted(removed)),
            ontology_fields_removed=False,
        )
        return wire, receipt


def semantic_coordinate_prompt_sha256() -> str:
    return hashlib.sha256(
        SEMANTIC_COORDINATE_COMPILER_SYSTEM_PROMPT.encode("utf-8")
    ).hexdigest()


__all__ = [
    "SEMANTIC_COORDINATE_COMPILER_PROTOCOL_VERSION",
    "SEMANTIC_COORDINATE_PARSER_VERSION",
    "SEMANTIC_COORDINATE_SCHEMA_VERSION",
    "EventModality",
    "EventPolarity",
    "ParsedSemanticCoordinateDocument",
    "ProviderSchemaAdapter",
    "ProviderSchemaAdapterReceipt",
    "SemanticCoordinateDocument",
    "SemanticCoordinateEventIR",
    "SemanticCoordinateEventKind",
    "SourceMode",
    "canonical_semantic_coordinate_schema",
    "normalize_relation",
    "parse_semantic_coordinate_document",
    "semantic_coordinate_prompt_sha256",
]
