"""Probabilistic transition head.

The transition head is a small regularised logistic model.  It is deliberately
outside the LLM: language organs may propose mechanisms, but the frozen state,
probability and learning history remain canonical SKULD state.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from skuld.kernel.primitives import utc_now


def _sigmoid(value: np.ndarray | float) -> np.ndarray | float:
    clipped = np.clip(value, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


@dataclass(frozen=True)
class ModelSnapshot:
    version: str
    domain: str
    feature_names: list[str]
    feature_means: list[float]
    feature_scales: list[float]
    coefficients: list[float]
    intercept: float
    calibration_alpha: float
    calibration_beta: float
    training_row_ids: list[str]
    created_at: str

    @property
    def hash(self) -> str:
        payload = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class BinaryTransitionModel:
    domain: str
    feature_names: list[str]
    regularisation: float = 0.02
    learning_rate: float = 0.08
    iterations: int = 900
    coefficients: np.ndarray = field(init=False)
    feature_means: np.ndarray = field(init=False)
    feature_scales: np.ndarray = field(init=False)
    intercept: float = field(init=False, default=0.0)
    calibration_alpha: float = field(init=False, default=1.0)
    calibration_beta: float = field(init=False, default=1.0)
    training_row_ids: list[str] = field(init=False, default_factory=list)
    version: str = field(init=False, default="wm-0")

    def __post_init__(self) -> None:
        if not self.feature_names:
            raise ValueError("transition model requires features")
        self.coefficients = np.zeros(len(self.feature_names), dtype=float)
        self.feature_means = np.zeros(len(self.feature_names), dtype=float)
        self.feature_scales = np.ones(len(self.feature_names), dtype=float)

    def fit(
        self,
        features: list[list[float]],
        outcomes: list[int],
        *,
        row_ids: list[str],
        version: str,
    ) -> None:
        if len(features) != len(outcomes) or len(features) != len(row_ids):
            raise ValueError("training rows are not aligned")
        if len(features) < max(12, len(self.feature_names) * 2):
            raise ValueError("transition model has insufficient history")
        matrix = np.asarray(features, dtype=float)
        target = np.asarray(outcomes, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != len(self.feature_names):
            raise ValueError("feature matrix shape does not match schema")
        if not np.isfinite(matrix).all() or not np.isfinite(target).all():
            raise ValueError("training rows must be finite")
        if set(int(value) for value in target) - {0, 1}:
            raise ValueError("binary transition outcomes must be zero or one")
        self.feature_means = matrix.mean(axis=0)
        scales = matrix.std(axis=0)
        self.feature_scales = np.where(scales < 1e-9, 1.0, scales)
        matrix = (matrix - self.feature_means) / self.feature_scales
        # Deterministic full-batch optimisation keeps evaluation reproducible.
        weights = np.zeros(matrix.shape[1], dtype=float)
        intercept = math.log((target.sum() + 1.0) / (len(target) - target.sum() + 1.0))
        for _ in range(self.iterations):
            probability = _sigmoid(matrix @ weights + intercept)
            residual = probability - target
            gradient = matrix.T @ residual / len(target) + self.regularisation * weights
            intercept_gradient = float(residual.mean())
            weights -= self.learning_rate * gradient
            intercept -= self.learning_rate * intercept_gradient
        self.coefficients = weights
        self.intercept = float(intercept)
        self.training_row_ids = list(row_ids)
        self.version = str(version)
        self.calibration_alpha = 1.0 + float(target.sum())
        self.calibration_beta = 1.0 + float(len(target) - target.sum())

    def raw_probability(self, features: list[float]) -> float:
        vector = np.asarray(features, dtype=float)
        if vector.shape != self.coefficients.shape or not np.isfinite(vector).all():
            raise ValueError("prediction feature vector is invalid")
        normalised = (vector - self.feature_means) / self.feature_scales
        return float(
            _sigmoid(float(normalised @ self.coefficients + self.intercept))
        )

    def predict_probability(self, features: list[float]) -> float:
        raw = self.raw_probability(features)
        evidence_mass = self.calibration_alpha + self.calibration_beta - 2.0
        base_rate = self.calibration_alpha / (self.calibration_alpha + self.calibration_beta)
        reliability = evidence_mass / (evidence_mass + 12.0)
        return max(1e-6, min(1.0 - 1e-6, 0.5 + (raw - 0.5) * reliability + (base_rate - 0.5) * (1.0 - reliability) * 0.2))

    def online_update(
        self,
        features: list[float],
        outcome: int,
        *,
        learning_rate: float = 0.05,
        new_version: str,
    ) -> dict[str, Any]:
        vector = np.asarray(features, dtype=float)
        if vector.shape != self.coefficients.shape or not np.isfinite(vector).all():
            raise ValueError("prediction feature vector is invalid")
        vector = (vector - self.feature_means) / self.feature_scales
        before = self.predict_probability(features)
        residual = before - int(outcome)
        previous = self.snapshot()
        self.coefficients -= learning_rate * (
            residual * vector + self.regularisation * self.coefficients
        )
        self.intercept -= learning_rate * residual
        self.calibration_alpha = self.calibration_alpha * 0.995 + int(outcome)
        self.calibration_beta = self.calibration_beta * 0.995 + (1 - int(outcome))
        self.version = str(new_version)
        after = self.predict_probability(features)
        return {
            "before_probability": before,
            "after_probability": after,
            "outcome": int(outcome),
            "coefficient_delta": (
                self.coefficients - np.asarray(previous.coefficients)
            ).tolist(),
            "intercept_delta": self.intercept - previous.intercept,
            "prior_snapshot_hash": previous.hash,
        }

    def snapshot(self) -> ModelSnapshot:
        return ModelSnapshot(
            version=self.version,
            domain=self.domain,
            feature_names=list(self.feature_names),
            feature_means=self.feature_means.astype(float).tolist(),
            feature_scales=self.feature_scales.astype(float).tolist(),
            coefficients=self.coefficients.astype(float).tolist(),
            intercept=float(self.intercept),
            calibration_alpha=float(self.calibration_alpha),
            calibration_beta=float(self.calibration_beta),
            training_row_ids=list(self.training_row_ids),
            created_at=utc_now(),
        )

    @classmethod
    def from_snapshot(cls, snapshot: ModelSnapshot) -> "BinaryTransitionModel":
        model = cls(domain=snapshot.domain, feature_names=list(snapshot.feature_names))
        model.coefficients = np.asarray(snapshot.coefficients, dtype=float)
        model.feature_means = np.asarray(snapshot.feature_means, dtype=float)
        model.feature_scales = np.asarray(snapshot.feature_scales, dtype=float)
        model.intercept = float(snapshot.intercept)
        model.calibration_alpha = float(snapshot.calibration_alpha)
        model.calibration_beta = float(snapshot.calibration_beta)
        model.training_row_ids = list(snapshot.training_row_ids)
        model.version = snapshot.version
        return model


__all__ = ["BinaryTransitionModel", "ModelSnapshot"]
