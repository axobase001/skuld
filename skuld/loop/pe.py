"""Canonical multi-label prediction-error cognition for product and research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
import json

from skuld.kernel.pe_schemas import PEClassification, PEType


PredictionErrorClass = PEType


class PredictiveContractStage(StrEnum):
    NONE = "NONE"
    CANDIDATE = "CANDIDATE"
    COMPILED = "COMPILED"
    CALIBRATED = "CALIBRATED"
    ACTIONABLE = "ACTIONABLE"


@dataclass(frozen=True)
class PredictionErrorEvidence:
    prediction_error_id: str
    domain: str
    observable_contract: str
    horizon: str
    regime: str
    intervention_status: str
    irreducible_noise_score: float = 0.0
    missing_evidence_ids: tuple[str, ...] = ()
    mechanism_mismatch: bool = False
    contract_violation: bool = False
    affected_goal_ids: tuple[str, ...] = ()
    consequence_severity: float = 0.0
    prior_attention_count: int = 0
    predictive_contract_stage: PredictiveContractStage = PredictiveContractStage.NONE
    user_id: str = ""
    evidence_reliability: float = 0.5
    independent_evidence_count: int = 0
    correctability: float = 0.5
    identifiability: float = 0.5
    calibration_mismatch: bool = False
    parameter_uncertainty: bool = False
    source_reliability_failure: bool = False
    prediction_type: str = ""

    @property
    def feature_hash(self) -> str:
        return sha256(
            json.dumps(
                asdict(self),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()


class CanonicalPEClassifier:
    VERSION = "canonical-pe-classifier/v1.7.0"
    ALEATORIC_THRESHOLD = 0.75
    GOAL_SEVERITY_THRESHOLD = 0.60

    def labels_for(self, evidence: PredictionErrorEvidence) -> tuple[PEType, ...]:
        labels: list[PEType] = []
        if evidence.irreducible_noise_score >= self.ALEATORIC_THRESHOLD:
            labels.append(PEType.ALEATORIC)
        if (
            evidence.missing_evidence_ids
            or evidence.calibration_mismatch
            or evidence.parameter_uncertainty
            or evidence.source_reliability_failure
        ):
            labels.append(PEType.EPISTEMIC)
        if evidence.mechanism_mismatch or evidence.contract_violation:
            labels.append(PEType.STRUCTURAL)
        if (
            evidence.affected_goal_ids
            and evidence.consequence_severity >= self.GOAL_SEVERITY_THRESHOLD
        ):
            labels.append(PEType.GOAL_RELEVANT)
        return tuple(labels)

    def classify(self, evidence: PredictionErrorEvidence) -> PEClassification:
        if not evidence.user_id:
            raise ValueError("product PE classification requires user_id")
        labels = self.labels_for(evidence)
        classification_indeterminate = False
        if not labels:
            # The v22 schema deliberately contains only the four scientific
            # PE classes.  An indeterminate class is epistemic uncertainty,
            # never invented aleatoric noise and never revision authority.
            labels = (PEType.EPISTEMIC,)
            probabilities = {PEType.EPISTEMIC.value: 0.5}
            classification_indeterminate = True
        else:
            probabilities = {}
            for label in labels:
                if label is PEType.ALEATORIC:
                    value = evidence.irreducible_noise_score
                elif label is PEType.EPISTEMIC:
                    value = min(1.0, 0.5 + 0.1 * len(evidence.missing_evidence_ids))
                elif label is PEType.STRUCTURAL:
                    value = 0.9 if evidence.contract_violation else 0.75
                else:
                    value = evidence.consequence_severity
                probabilities[label.value] = value
        priority = (
            evidence.consequence_severity
            * evidence.correctability
            * evidence.identifiability
        )
        return PEClassification(
            user_id=evidence.user_id,
            prediction_error_id=evidence.prediction_error_id,
            labels=list(labels),
            label_probabilities=probabilities,
            evidence_reliability=evidence.evidence_reliability,
            independent_evidence_count=evidence.independent_evidence_count,
            subject_consequence=evidence.consequence_severity,
            correctability=evidence.correctability,
            identifiability=evidence.identifiability,
            priority=priority,
            classifier_version=self.VERSION,
            provenance={
                "feature_hash": evidence.feature_hash,
                "raw_pe_magnitude_is_not_priority": True,
                "multi_label": True,
                "classification_indeterminate": classification_indeterminate,
                "affected_goal_ids": list(evidence.affected_goal_ids),
                "prediction_type": evidence.prediction_type,
            },
        )


__all__ = [
    "CanonicalPEClassifier",
    "PredictionErrorClass",
    "PredictionErrorEvidence",
    "PredictiveContractStage",
]
