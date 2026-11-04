"""Approval-gated agency represented entirely inside the subject graph."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from skuld.kernel.schemas import (
    AuthorityClass,
    EpistemicState,
    LifecycleState,
    ModalState,
    ResolutionState,
    SourceEvent,
    SubjectEdge,
    SubjectNode,
    TemporalState,
    canonical_hash,
)
from skuld.kernel.graph_store import PersistentSubjectHypergraphStore

AGENCY_GRAPH_BRIDGE_VERSION = "skuld-v200-agency-graph-bridge/v1"
_INVALIDATING_RELATIONS = {"INVALIDATES", "SUPERSEDES", "WITHDRAWS"}


@dataclass(frozen=True, slots=True)
class ConnectorExecutionReceipt:
    action_attempt_node_id: str
    external_effect_node_id: str
    action_outcome_node_id: str
    action_node_id: str
    approval_node_id: str
    idempotency_key: str
    schema_version: str = AGENCY_GRAPH_BRIDGE_VERSION


class SubjectGraphAgencyBridge:
    def __init__(self, graph: PersistentSubjectHypergraphStore) -> None:
        if not isinstance(graph, PersistentSubjectHypergraphStore):
            raise TypeError(
                "SubjectGraphAgencyBridge requires PersistentSubjectHypergraphStore"
            )
        self.graph = graph

    def _append_node(
        self,
        *,
        node_id: str,
        user_id: str,
        node_type: str,
        source_event_id: str,
        created_at: str,
        semantic_identity_key: str,
        content: dict[str, Any],
        modal_state: ModalState = ModalState.INTENDED,
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
                temporal_state=TemporalState.CURRENT,
                resolution_state=ResolutionState.RESOLVED,
                authority_class=AuthorityClass.SYSTEM_DERIVED,
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
                edge_id="agency_edge_v200_" + digest[:40],
                user_id=user_id,
                source_node_id=source.node_id,
                relation_type=relation,
                target_node_id=target.node_id,
                created_at=created_at,
                source_event_id=source_event_id,
                valid_from=created_at,
            )
        )

    def propose_intent(
        self,
        *,
        user_id: str,
        intent: dict[str, Any],
        source_event_id: str,
        created_at: str,
    ) -> SubjectNode:
        digest = canonical_hash(
            {
                "user_id": user_id,
                "intent": intent,
                "source_event_id": source_event_id,
            }
        )
        return self._append_node(
            node_id="action_intent_v200_" + digest[:40],
            user_id=user_id,
            node_type="ActionIntent",
            source_event_id=source_event_id,
            created_at=created_at,
            semantic_identity_key="action-intent:" + digest,
            content={"intent": intent, "executable": False},
        )

    @staticmethod
    def _action_material(content: dict[str, Any]) -> dict[str, Any]:
        return {
            "connector": content["connector"],
            "operation": content["operation"],
            "parameters": content["parameters"],
            "prediction_node_id": content["prediction_node_id"],
            "goal_node_id": content["goal_node_id"],
            "parameter_sources": content["parameter_sources"],
        }

    def _is_active(self, user_id: str, node: SubjectNode) -> bool:
        if node.lifecycle_state is not LifecycleState.ACTIVE:
            return False
        return not any(
            edge.relation_type in _INVALIDATING_RELATIONS
            and edge.status == "ACTIVE"
            for edge in self.graph.list_edges(
                user_id,
                target_node_id=node.node_id,
                status="ACTIVE",
                limit=64,
            )
        )

    def ground_action(
        self,
        *,
        user_id: str,
        intent_node_id: str,
        prediction_node_id: str,
        goal_node_id: str,
        connector: str,
        operation: str,
        parameters: dict[str, Any],
        parameter_sources: dict[str, dict[str, str]],
        source_event_id: str,
        created_at: str,
    ) -> SubjectNode:
        intent = self.graph.get_node(user_id, intent_node_id)
        prediction = self.graph.get_node(user_id, prediction_node_id)
        goal = self.graph.get_node(user_id, goal_node_id)
        if intent is None or intent.node_type != "ActionIntent":
            raise ValueError("ActionIntent is missing")
        if prediction is None or prediction.node_type != "Prediction":
            raise ValueError("active Prediction is required")
        if not self._is_active(user_id, prediction):
            raise ValueError("active Prediction is required")
        if prediction.content.get("actionability") is not True:
            raise ValueError("Prediction is not actionable")
        if goal is None or goal.node_type not in ("Goal", "Task", "Commitment"):
            raise ValueError("the served responsibility (Goal, Task or Commitment) is missing")
        if not parameters or set(parameters) != set(parameter_sources):
            raise ValueError("parameter provenance must cover every parameter")
        for parameter, value in parameters.items():
            source = parameter_sources[parameter]
            source_node = self.graph.get_node(user_id, source.get("node_id", ""))
            source_field = source.get("field", "")
            if source_node is None or source_field not in source_node.content:
                raise ValueError("parameter provenance source is missing")
            if source_node.content[source_field] != value:
                raise ValueError("parameter provenance does not match action value")
        content: dict[str, Any] = {
            "connector": connector,
            "operation": operation,
            "parameters": dict(sorted(parameters.items())),
            "prediction_node_id": prediction_node_id,
            "goal_node_id": goal_node_id,
            "parameter_sources": {
                key: parameter_sources[key] for key in sorted(parameter_sources)
            },
        }
        action_hash = canonical_hash(self._action_material(content))
        content.update(
            {
                "action_hash": action_hash,
                "approval_required": True,
                "executable_without_approval": False,
            }
        )
        digest = canonical_hash(
            {
                "action_hash": action_hash,
                "source_event_id": source_event_id,
                "intent_node_id": intent_node_id,
            }
        )
        action = self._append_node(
            node_id="grounded_action_v200_" + digest[:40],
            user_id=user_id,
            node_type="GroundedAction",
            source_event_id=source_event_id,
            created_at=created_at,
            semantic_identity_key="grounded-action:" + action_hash,
            content=content,
        )
        relations = [
            ("PROPOSED_FROM", intent),
            ("JUSTIFIED_BY", prediction),
            ("SERVES", goal),
        ]
        relations.extend(
            (
                "GROUNDED_FROM",
                self.graph.get_node(user_id, parameter_sources[key]["node_id"]),
            )
            for key in sorted(parameter_sources)
        )
        seen: set[tuple[str, str]] = set()
        for relation, target in relations:
            assert target is not None
            identity = (relation, target.node_id)
            if identity in seen:
                continue
            seen.add(identity)
            self._edge(
                user_id=user_id,
                source=action,
                relation=relation,
                target=target,
                source_event_id=source_event_id,
                created_at=created_at,
            )
        return action

    def approve(
        self,
        *,
        user_id: str,
        action_node_id: str,
        approved_action_hash: str,
        source_event_id: str,
        created_at: str,
    ) -> SubjectNode:
        source = self.graph.get_source_event(user_id, source_event_id)
        if source is None or source.source_type != "ExactActionApproval":
            raise ValueError("exact Action approval source event is required")
        action = self.graph.get_node(user_id, action_node_id)
        if action is None or action.node_type != "GroundedAction":
            raise ValueError("GroundedAction is missing")
        if approved_action_hash != action.content.get("action_hash"):
            raise ValueError("approval action hash mismatch")
        digest = canonical_hash(
            {
                "action_node_id": action_node_id,
                "action_hash": approved_action_hash,
                "source_event_id": source_event_id,
            }
        )
        approval = self._append_node(
            node_id="approval_v200_" + digest[:40],
            user_id=user_id,
            node_type="Approval",
            source_event_id=source_event_id,
            created_at=created_at,
            semantic_identity_key="approval:" + digest,
            content={
                "action_node_id": action_node_id,
                "approved_action_hash": approved_action_hash,
                "exact": True,
            },
            modal_state=ModalState.ACTUAL,
        )
        self._edge(
            user_id=user_id,
            source=approval,
            relation="APPROVES",
            target=action,
            source_event_id=source_event_id,
            created_at=created_at,
        )
        self._edge(
            user_id=user_id,
            source=action,
            relation="AUTHORIZED_BY",
            target=approval,
            source_event_id=source_event_id,
            created_at=created_at,
        )
        return approval

    def validate_approval_content(
        self, *, approval: SubjectNode, action_content: dict[str, Any]
    ) -> bool:
        if approval.node_type != "Approval":
            return False
        try:
            current_hash = canonical_hash(self._action_material(action_content))
        except KeyError:
            return False
        return approval.content.get("approved_action_hash") == current_hash

    def validate_executable(
        self,
        *,
        user_id: str,
        action_node_id: str,
        approval_node_id: str | None = None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        action = self.graph.get_node(user_id, action_node_id)
        if action is None or action.node_type != "GroundedAction":
            return {"valid": False, "reason_codes": ["GROUNDED_ACTION_MISSING"]}
        prediction_edges = self.graph.list_edges(
            user_id,
            source_node_id=action.node_id,
            relation_type="JUSTIFIED_BY",
            status="ACTIVE",
            limit=2,
        )
        if len(prediction_edges) != 1:
            reasons.append("PREDICTION_LINEAGE_INVALID")
        else:
            prediction = self.graph.get_node(
                user_id, prediction_edges[0].target_node_id
            )
            if (
                prediction is None
                or prediction.node_type != "Prediction"
                or not self._is_active(user_id, prediction)
            ):
                reasons.append("PREDICTION_INVALID")
        approval = (
            self.graph.get_node(user_id, approval_node_id)
            if approval_node_id
            else None
        )
        if approval is None or approval.node_type != "Approval":
            reasons.append("EXACT_APPROVAL_MISSING")
        elif not self._is_active(user_id, approval):
            reasons.append("APPROVAL_INVALID")
        elif approval.content.get("action_node_id") != action.node_id:
            reasons.append("APPROVAL_ACTION_MISMATCH")
        elif not self.validate_approval_content(
            approval=approval, action_content=action.content
        ):
            reasons.append("APPROVAL_HASH_MISMATCH")
        for parameter, source in action.content.get("parameter_sources", {}).items():
            node = self.graph.get_node(user_id, source.get("node_id", ""))
            if (
                node is None
                or source.get("field") not in node.content
                or node.content[source["field"]]
                != action.content.get("parameters", {}).get(parameter)
            ):
                reasons.append("ACTION_PARAMETER_PROVENANCE_INVALID")
                break
        return {"valid": not reasons, "reason_codes": reasons}

    def execute(
        self,
        *,
        user_id: str,
        action_node_id: str,
        approval_node_id: str,
        idempotency_key: str,
        connector_call: Callable[[dict[str, Any]], dict[str, Any]],
        created_at: str,
    ) -> ConnectorExecutionReceipt:
        validation = self.validate_executable(
            user_id=user_id,
            action_node_id=action_node_id,
            approval_node_id=approval_node_id,
        )
        if not validation["valid"]:
            raise ValueError(
                "action is not executable:" + ",".join(validation["reason_codes"])
            )
        execution_identity = canonical_hash(
            {"user_id": user_id, "idempotency_key": idempotency_key}
        )
        attempt_id = "action_attempt_v200_" + execution_identity[:40]
        effect_id = "external_effect_v200_" + execution_identity[:40]
        outcome_id = "action_outcome_v200_" + execution_identity[:40]
        existing_outcome = self.graph.get_node(user_id, outcome_id)
        if existing_outcome is not None:
            return ConnectorExecutionReceipt(
                action_attempt_node_id=attempt_id,
                external_effect_node_id=effect_id,
                action_outcome_node_id=outcome_id,
                action_node_id=action_node_id,
                approval_node_id=approval_node_id,
                idempotency_key=idempotency_key,
            )
        existing_attempt = self.graph.get_node(user_id, attempt_id)
        if existing_attempt is not None:
            raise ValueError("connector execution is in doubt; replay is fail-closed")
        action = self.graph.get_node(user_id, action_node_id)
        approval = self.graph.get_node(user_id, approval_node_id)
        assert action is not None and approval is not None
        attempt_source_id = "source-action-attempt-" + execution_identity[:40]
        self.graph.append_source_event(
            SourceEvent(
                source_event_id=attempt_source_id,
                user_id=user_id,
                source_type="ActionAttempt",
                source_ref="idempotency:" + idempotency_key,
                received_at=created_at,
                content={
                    "action_node_id": action_node_id,
                    "approval_node_id": approval_node_id,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        attempt = self._append_node(
            node_id=attempt_id,
            user_id=user_id,
            node_type="ActionAttempt",
            source_event_id=attempt_source_id,
            created_at=created_at,
            semantic_identity_key="action-attempt:" + idempotency_key,
            content={
                "action_node_id": action_node_id,
                "approval_node_id": approval_node_id,
                "idempotency_key": idempotency_key,
                "state": "DISPATCHED",
            },
            modal_state=ModalState.ACTUAL,
        )
        self._edge(
            user_id=user_id,
            source=attempt,
            relation="ATTEMPTS",
            target=action,
            source_event_id=attempt_source_id,
            created_at=created_at,
        )
        result = connector_call(
            {**action.content["parameters"], "idempotency_key": idempotency_key}
        )
        observation_source_id = "source-connector-result-" + execution_identity[:40]
        self.graph.append_source_event(
            SourceEvent(
                source_event_id=observation_source_id,
                user_id=user_id,
                source_type="ConnectorObservation",
                source_ref="idempotency:" + idempotency_key,
                received_at=created_at,
                content={"result": result, "idempotency_key": idempotency_key},
            )
        )
        effect = self._append_node(
            node_id=effect_id,
            user_id=user_id,
            node_type="ExternalEffect",
            source_event_id=observation_source_id,
            created_at=created_at,
            semantic_identity_key="external-effect:" + idempotency_key,
            content={"connector_result": result, "idempotency_key": idempotency_key},
            modal_state=ModalState.ACTUAL,
        )
        outcome = self._append_node(
            node_id=outcome_id,
            user_id=user_id,
            node_type="ActionOutcome",
            source_event_id=observation_source_id,
            created_at=created_at,
            semantic_identity_key="action-outcome:" + idempotency_key,
            content={
                "connector_result": result,
                "idempotency_key": idempotency_key,
                "prediction_resolved": False,
                "status": "SUCCEEDED" if not isinstance(result, dict) or result.get("ok", True) else "FAILED",
            },
            modal_state=ModalState.ACTUAL,
        )
        for source_node, relation, target_node in (
            (outcome, "OUTCOME_OF", action),
            (outcome, "REPORTS_EFFECT", effect),
            (effect, "AUTHORIZED_BY", approval),
            (effect, "CAUSED_BY", action),
        ):
            self._edge(
                user_id=user_id,
                source=source_node,
                relation=relation,
                target=target_node,
                source_event_id=observation_source_id,
                created_at=created_at,
            )
        return ConnectorExecutionReceipt(
            action_attempt_node_id=attempt.node_id,
            external_effect_node_id=effect.node_id,
            action_outcome_node_id=outcome.node_id,
            action_node_id=action_node_id,
            approval_node_id=approval_node_id,
            idempotency_key=idempotency_key,
        )


__all__ = [
    "AGENCY_GRAPH_BRIDGE_VERSION",
    "ConnectorExecutionReceipt",
    "SubjectGraphAgencyBridge",
]
