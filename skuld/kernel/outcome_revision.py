"""Independent outcome compatibility, prediction error, and local graph revision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from skuld.kernel.schemas import (
    AuthorityClass,
    EpistemicState,
    LifecycleState,
    ModalState,
    ResolutionState,
    SubjectEdge,
    SubjectNode,
    TemporalState,
    canonical_hash,
)
from skuld.kernel.graph_store import PersistentSubjectHypergraphStore

OUTCOME_REVISION_GRAPH_VERSION = "skuld-v200-outcome-revision-graph/v1"


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("outcome timestamps require timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class OutcomeLearningReceipt:
    user_id: str
    prediction_node_id: str
    outcome_report_node_id: str
    compatibility_receipt_node_id: str
    compatible: bool
    reason_codes: tuple[str, ...]
    prediction_outcome_node_id: str | None
    prediction_error_node_id: str | None
    attribution_node_ids: tuple[str, ...]
    schema_version: str = OUTCOME_REVISION_GRAPH_VERSION


@dataclass(frozen=True, slots=True)
class GraphRevisionReceipt:
    user_id: str
    revision_node_id: str
    prediction_error_node_id: str
    predecessor_node_ids: tuple[str, ...]
    successor_node_ids: tuple[str, ...]
    schema_version: str = OUTCOME_REVISION_GRAPH_VERSION


class SubjectGraphOutcomeRevisionBridge:
    def __init__(self, graph: PersistentSubjectHypergraphStore) -> None:
        if not isinstance(graph, PersistentSubjectHypergraphStore):
            raise TypeError(
                "SubjectGraphOutcomeRevisionBridge requires "
                "PersistentSubjectHypergraphStore"
            )
        self.graph = graph

    def _node(
        self,
        *,
        node_id: str,
        user_id: str,
        node_type: str,
        source_event_id: str,
        created_at: str,
        semantic_identity_key: str,
        content: dict[str, Any],
        modal_state: ModalState = ModalState.ACTUAL,
        temporal_state: TemporalState = TemporalState.CURRENT,
        authority_class: AuthorityClass = AuthorityClass.SYSTEM_DERIVED,
    ) -> SubjectNode:
        return self.graph.append_node(
            SubjectNode(
                node_id=node_id,
                user_id=user_id,
                node_type=node_type,
                created_at=created_at,
                source_event_id=source_event_id,
                semantic_identity_key=semantic_identity_key,
                content=content,
                epistemic_state=EpistemicState.SUPPORTED,
                lifecycle_state=LifecycleState.ACTIVE,
                modal_state=modal_state,
                temporal_state=temporal_state,
                resolution_state=ResolutionState.RESOLVED,
                authority_class=authority_class,
                valid_from=created_at,
            )
        )

    def _edge(
        self,
        *,
        user_id: str,
        source: SubjectNode,
        relation: str,
        target: SubjectNode,
        source_event_id: str,
        created_at: str,
    ) -> SubjectEdge:
        digest = canonical_hash(
            {
                "source": source.node_id,
                "relation": relation,
                "target": target.node_id,
                "source_event_id": source_event_id,
            }
        )
        return self.graph.append_edge(
            SubjectEdge(
                edge_id="outcome_edge_v200_" + digest[:40],
                user_id=user_id,
                source_node_id=source.node_id,
                relation_type=relation,
                target_node_id=target.node_id,
                created_at=created_at,
                source_event_id=source_event_id,
                valid_from=created_at,
            )
        )

    @staticmethod
    def _compatibility(
        contract: dict[str, Any], observation: dict[str, Any]
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        comparisons = (
            ("observable_key", "OBSERVABLE_MISMATCH"),
            ("data_type", "DATA_TYPE_MISMATCH"),
            ("unit", "UNIT_MISMATCH"),
            ("modeled_subject_type", "SUBJECT_TYPE_MISMATCH"),
            ("modeled_subject_id", "SUBJECT_ID_MISMATCH"),
            ("intervention_context", "INTERVENTION_CONTEXT_MISMATCH"),
        )
        for field, reason in comparisons:
            if observation.get(field) != contract.get(field):
                reasons.append(reason)
        try:
            observed_at = _instant(str(observation["observed_at"]))
            if not (
                _instant(str(contract["window_start"]))
                <= observed_at
                <= _instant(str(contract["window_end"]))
            ):
                reasons.append("OBSERVATION_WINDOW_MISMATCH")
        except (KeyError, TypeError, ValueError):
            reasons.append("OBSERVATION_TIME_INVALID")
        allowed_sources = contract.get("allowed_observation_sources", [])
        if observation.get("observation_source") not in allowed_sources:
            reasons.append("OBSERVATION_SOURCE_NOT_ALLOWED")
        if observation.get("data_type") == "boolean" and not isinstance(
            observation.get("observed_value"), bool
        ):
            reasons.append("OBSERVED_VALUE_TYPE_MISMATCH")
        return tuple(sorted(set(reasons)))

    def observe(
        self,
        *,
        user_id: str,
        prediction_node_id: str,
        observation: dict[str, Any],
        attribution_node_ids: tuple[str, ...],
        source_event_id: str,
        created_at: str,
    ) -> OutcomeLearningReceipt:
        source = self.graph.get_source_event(user_id, source_event_id)
        if source is None or source.source_type not in {
            "UserOutcomeReport",
            "ConnectorObservation",
            "ExternalObservation",
        }:
            raise ValueError("independent outcome source event is required")
        prediction = self.graph.get_node(user_id, prediction_node_id)
        if prediction is None or prediction.node_type != "Prediction":
            raise ValueError("Prediction is missing")
        contract = prediction.content.get("observable")
        if not isinstance(contract, dict):
            raise TypeError("Prediction observable contract is missing")
        reasons = self._compatibility(contract, observation)
        seed = canonical_hash(
            {
                "prediction_node_id": prediction_node_id,
                "observation": observation,
                "source_event_id": source_event_id,
            }
        )
        report = self._node(
            node_id="outcome_report_v200_" + seed[:40],
            user_id=user_id,
            node_type="OutcomeReport",
            source_event_id=source_event_id,
            created_at=created_at,
            semantic_identity_key="outcome-report:" + seed,
            content={"observation": observation, "independent": True},
            authority_class=(
                AuthorityClass.USER_FIRST_PERSON
                if source.source_type == "UserOutcomeReport"
                else AuthorityClass.TOOL_OBSERVED
            ),
        )
        compatibility = self._node(
            node_id="outcome_compatibility_v200_" + seed[:40],
            user_id=user_id,
            node_type="OutcomePredictionCompatibilityReceipt",
            source_event_id=source_event_id,
            created_at=created_at,
            semantic_identity_key="outcome-compatibility:" + seed,
            content={
                "prediction_node_id": prediction_node_id,
                "outcome_report_node_id": report.node_id,
                "compatible": not reasons,
                "reason_codes": list(reasons),
                "contract": contract,
                "observation": observation,
            },
        )
        self._edge(
            user_id=user_id,
            source=compatibility,
            relation="ASSESSES",
            target=prediction,
            source_event_id=source_event_id,
            created_at=created_at,
        )
        self._edge(
            user_id=user_id,
            source=compatibility,
            relation="ASSESSES",
            target=report,
            source_event_id=source_event_id,
            created_at=created_at,
        )
        if reasons:
            return OutcomeLearningReceipt(
                user_id=user_id,
                prediction_node_id=prediction_node_id,
                outcome_report_node_id=report.node_id,
                compatibility_receipt_node_id=compatibility.node_id,
                compatible=False,
                reason_codes=reasons,
                prediction_outcome_node_id=None,
                prediction_error_node_id=None,
                attribution_node_ids=(),
            )
        if not attribution_node_ids:
            raise ValueError("Prediction Error requires graph attribution targets")
        attribution_nodes: list[SubjectNode] = []
        for node_id in sorted(set(attribution_node_ids)):
            node = self.graph.get_node(user_id, node_id)
            if node is None or node.node_type not in {
                "Belief",
                "CompiledMechanism",
                "Binding",
                "SemanticTargetBindingReceipt",
                "HypergraphRelation",
            }:
                raise ValueError("Prediction Error attribution target is invalid")
            attribution_nodes.append(node)
        prediction_outcome = self._node(
            node_id="prediction_outcome_v200_" + seed[:40],
            user_id=user_id,
            node_type="PredictionOutcome",
            source_event_id=source_event_id,
            created_at=created_at,
            semantic_identity_key="prediction-outcome:" + seed,
            content={
                "observation": observation,
                "compatibility_receipt_node_id": compatibility.node_id,
                "action_outcome": False,
            },
        )
        self._edge(
            user_id=user_id,
            source=prediction_outcome,
            relation="OBSERVED_FROM",
            target=report,
            source_event_id=source_event_id,
            created_at=created_at,
        )
        self._edge(
            user_id=user_id,
            source=prediction_outcome,
            relation="RESOLVES",
            target=prediction,
            source_event_id=source_event_id,
            created_at=created_at,
        )
        observed_numeric = float(observation["observed_value"])
        probability = float(prediction.content["probability"])
        prediction_error_value = round(observed_numeric - probability, 12)
        error = self._node(
            node_id="prediction_error_v200_" + seed[:40],
            user_id=user_id,
            node_type="PredictionError",
            source_event_id=source_event_id,
            created_at=created_at,
            semantic_identity_key="prediction-error:" + seed,
            content={
                "prediction_node_id": prediction_node_id,
                "prediction_outcome_node_id": prediction_outcome.node_id,
                "compatibility_receipt_node_id": compatibility.node_id,
                "predicted_probability": probability,
                "observed_numeric": observed_numeric,
                "error": prediction_error_value,
            },
        )
        for target in (prediction, prediction_outcome):
            self._edge(
                user_id=user_id,
                source=error,
                relation="COMPARES",
                target=target,
                source_event_id=source_event_id,
                created_at=created_at,
            )
        for target in attribution_nodes:
            self._edge(
                user_id=user_id,
                source=error,
                relation="ATTRIBUTED_TO",
                target=target,
                source_event_id=source_event_id,
                created_at=created_at,
            )
        return OutcomeLearningReceipt(
            user_id=user_id,
            prediction_node_id=prediction_node_id,
            outcome_report_node_id=report.node_id,
            compatibility_receipt_node_id=compatibility.node_id,
            compatible=True,
            reason_codes=(),
            prediction_outcome_node_id=prediction_outcome.node_id,
            prediction_error_node_id=error.node_id,
            attribution_node_ids=tuple(node.node_id for node in attribution_nodes),
        )

    def revise(
        self,
        *,
        user_id: str,
        prediction_error_node_id: str,
        revisions: dict[str, dict[str, Any]],
        source_event_id: str,
        created_at: str,
    ) -> GraphRevisionReceipt:
        error = self.graph.get_node(user_id, prediction_error_node_id)
        if error is None or error.node_type != "PredictionError":
            raise ValueError("PredictionError is missing")
        if not revisions:
            raise ValueError("Revision must be local and non-empty")
        predecessors: list[SubjectNode] = []
        for predecessor_id in sorted(revisions):
            predecessor = self.graph.get_node(user_id, predecessor_id)
            if predecessor is None or predecessor.node_type not in {
                "Belief",
                "CompiledMechanism",
                "Binding",
                "CurrentState",
                "Constraint",
            }:
                raise ValueError("Revision target is invalid")
            predecessors.append(predecessor)
        seed = canonical_hash(
            {
                "prediction_error_node_id": prediction_error_node_id,
                "revisions": revisions,
                "source_event_id": source_event_id,
            }
        )
        revision = self._node(
            node_id="revision_v200_" + seed[:40],
            user_id=user_id,
            node_type="Revision",
            source_event_id=source_event_id,
            created_at=created_at,
            semantic_identity_key="revision:" + seed,
            content={
                "prediction_error_node_id": prediction_error_node_id,
                "predecessor_node_ids": [node.node_id for node in predecessors],
                "selective": True,
            },
        )
        self._edge(
            user_id=user_id,
            source=revision,
            relation="CAUSED_BY",
            target=error,
            source_event_id=source_event_id,
            created_at=created_at,
        )
        successors: list[SubjectNode] = []
        for predecessor in predecessors:
            successor_seed = canonical_hash(
                {
                    "revision": revision.node_id,
                    "predecessor_semantic_key": predecessor.semantic_identity_key,
                    "content": revisions[predecessor.node_id],
                }
            )
            successor = self._node(
                node_id="revised_state_v200_" + successor_seed[:40],
                user_id=user_id,
                node_type=predecessor.node_type,
                source_event_id=source_event_id,
                created_at=created_at,
                semantic_identity_key=predecessor.semantic_identity_key,
                content=revisions[predecessor.node_id],
                modal_state=predecessor.modal_state,
                temporal_state=predecessor.temporal_state,
                authority_class=AuthorityClass.SYSTEM_DERIVED,
            )
            successors.append(successor)
            self._edge(
                user_id=user_id,
                source=revision,
                relation="SUPERSEDES",
                target=predecessor,
                source_event_id=source_event_id,
                created_at=created_at,
            )
            self._edge(
                user_id=user_id,
                source=revision,
                relation="CREATES",
                target=successor,
                source_event_id=source_event_id,
                created_at=created_at,
            )
        return GraphRevisionReceipt(
            user_id=user_id,
            revision_node_id=revision.node_id,
            prediction_error_node_id=prediction_error_node_id,
            predecessor_node_ids=tuple(node.node_id for node in predecessors),
            successor_node_ids=tuple(node.node_id for node in successors),
        )

    def validate_closure(
        self, *, user_id: str, receipt: OutcomeLearningReceipt
    ) -> dict[str, Any]:
        reasons: list[str] = []
        expected = (
            (
                receipt.outcome_report_node_id,
                "OutcomeReport",
                "OUTCOME_REPORT_MISSING",
            ),
            (
                receipt.compatibility_receipt_node_id,
                "OutcomePredictionCompatibilityReceipt",
                "COMPATIBILITY_RECEIPT_MISSING",
            ),
        )
        for node_id, node_type, reason in expected:
            node = self.graph.get_node(user_id, node_id)
            if node is None or node.node_type != node_type:
                reasons.append(reason)
        if receipt.compatible:
            for node_id, node_type, reason in (
                (
                    receipt.prediction_outcome_node_id,
                    "PredictionOutcome",
                    "PREDICTION_OUTCOME_MISSING",
                ),
                (
                    receipt.prediction_error_node_id,
                    "PredictionError",
                    "PREDICTION_ERROR_MISSING",
                ),
            ):
                node = self.graph.get_node(user_id, str(node_id))
                if node is None or node.node_type != node_type:
                    reasons.append(reason)
        return {"valid": not reasons, "reason_codes": reasons}


__all__ = [
    "OUTCOME_REVISION_GRAPH_VERSION",
    "GraphRevisionReceipt",
    "OutcomeLearningReceipt",
    "SubjectGraphOutcomeRevisionBridge",
]
