"""Evidence-aware, task-conditioned pairwise consequence method prototype.

This module is deliberately offline and CPU-only.  It consumes MODEL_LOCAL_RAW route/speed
sequences without assigning a physical unit or CARLA world meaning to them.  It does not compute
TTC, collision, lane safety, or physical left/right.  Every output is diagnostic-only and the
``RuleBridge`` never replaces the existing rule/state-machine result.

The small learnable heads are NumPy softmax classifiers.  This keeps the prototype
backbone-agnostic and makes its training path available in the repository's CPU environment where
PyTorch is not installed.  A future route/speed backbone can replace ``TaskConditionedPlanEncoder``
without changing the outcome, comparator, abstention, or bridge contracts.
"""

from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from .offline_decision import DecisionContext, recommend_offline


CANDIDATE_LABELS = ("PASS", "FAIL", "UNKNOWN")
PAIR_LABELS = ("TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN")
DATA_PROVENANCE = (
    "REAL_RECORDED_UNLABELED",
    "HAND_AUTHORED_ORACLE_FIXTURE",
    "SYNTHETIC_METHOD_TEST_ONLY",
)
METHOD_DESIGNATION = (
    "METHOD_PROTOTYPE",
    "OFFLINE_SYNTHETIC_VALIDATION",
    "NOT_PRIMARY_EVIDENCE",
    "NOT_PAPER_RESULT",
)
TASK_FAMILIES = ("REFERENCE_GOAL", "DESTINATION_GOAL", "MANEUVER_BRANCH")
_ANCHOR_FIELDS = (
    "endpoint_delta_0_raw",
    "endpoint_delta_1_raw",
    "curvature_like_raw",
    "speed_mean_raw",
)
_REQUIRED_AVAILABILITY = ("route", "speed", "task_anchor", "provenance")


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _matrix(value: Any, *, min_rows: int) -> np.ndarray | None:
    """Parse a JSON-like numeric sequence without modifying it."""

    raw = value
    if isinstance(raw, (list, tuple)) and len(raw) == 1:
        nested = raw[0]
        if isinstance(nested, (list, tuple)) and nested and isinstance(nested[0], (list, tuple)):
            raw = nested
    if not isinstance(raw, (list, tuple)) or len(raw) < min_rows:
        return None
    rows: list[list[float]] = []
    width: int | None = None
    for row in raw:
        if not isinstance(row, (list, tuple)) or not row:
            return None
        parsed = [_finite_float(item) for item in row]
        if any(item is None for item in parsed):
            return None
        if width is None:
            width = len(parsed)
        if len(parsed) != width:
            return None
        rows.append([float(item) for item in parsed if item is not None])
    result = np.asarray(rows, dtype=np.float64)
    if result.ndim != 2 or not np.isfinite(result).all():
        return None
    return result


def _route_features(value: Any) -> np.ndarray | None:
    route = _matrix(value, min_rows=2)
    if route is None or route.shape[1] != 2:
        return None
    deltas = np.diff(route, axis=0)
    step_norm = np.linalg.norm(deltas, axis=1)
    endpoint = route[-1] - route[0]
    if len(deltas) >= 2:
        cross = deltas[:-1, 0] * deltas[1:, 1] - deltas[:-1, 1] * deltas[1:, 0]
        denom = np.linalg.norm(deltas[:-1], axis=1) * np.linalg.norm(deltas[1:], axis=1)
        turn = cross / np.maximum(denom, 1e-9)
        signed_turn = float(np.mean(turn))
        absolute_turn = float(np.mean(np.abs(turn)))
    else:
        signed_turn = 0.0
        absolute_turn = 0.0
    span = np.ptp(route, axis=0)
    return np.asarray(
        [
            min(len(route), 100) / 100.0,
            endpoint[0],
            endpoint[1],
            np.linalg.norm(endpoint),
            np.mean(deltas[:, 0]),
            np.mean(deltas[:, 1]),
            np.mean(step_norm),
            np.std(step_norm),
            signed_turn,
            absolute_turn,
            span[0],
            span[1],
            np.sum(step_norm),
        ],
        dtype=np.float64,
    )


def _speed_features(value: Any) -> np.ndarray | None:
    speed = _matrix(value, min_rows=1)
    if speed is None:
        return None
    first = speed[:, 0]
    second = speed[:, 1] if speed.shape[1] > 1 else np.zeros(len(speed), dtype=np.float64)
    mean_vector = np.mean(speed, axis=0)
    if len(speed) >= 2:
        deltas = np.diff(speed, axis=0)
        mean_delta_norm = float(np.mean(np.linalg.norm(deltas, axis=1)))
        endpoint = speed[-1] - speed[0]
    else:
        mean_delta_norm = 0.0
        endpoint = np.zeros(speed.shape[1], dtype=np.float64)
    endpoint_0 = endpoint[0]
    endpoint_1 = endpoint[1] if len(endpoint) > 1 else 0.0
    return np.asarray(
        [
            min(len(speed), 100) / 100.0,
            np.mean(first),
            np.mean(second),
            np.linalg.norm(mean_vector),
            np.std(speed),
            endpoint_0,
            endpoint_1,
            mean_delta_norm,
            np.max(np.abs(speed)),
            np.linalg.norm(speed[-1]),
        ],
        dtype=np.float64,
    )


def raw_task_anchor_from_record(candidate: Mapping[str, Any]) -> dict[str, float]:
    """Build an explicit RAW-unit task anchor from a designated reference record.

    This helper does not decide whether another candidate passes the task.  Fixture authors choose
    the reference record and gold labels independently; the function only serializes the four raw
    descriptors consumed by the encoder.
    """

    route = _route_features(candidate.get("pred_route_raw"))
    speed = _speed_features(candidate.get("pred_speed_wps_raw"))
    if route is None or speed is None:
        raise ValueError("TASK_REFERENCE_RECORD_INVALID")
    return {
        "endpoint_delta_0_raw": float(route[1]),
        "endpoint_delta_1_raw": float(route[2]),
        "curvature_like_raw": float(route[8]),
        "speed_mean_raw": float(speed[3]),
    }


def _token_features(token: Any) -> np.ndarray:
    if not isinstance(token, str) or not token.strip():
        return np.zeros(4, dtype=np.float64)
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return np.asarray([(digest[index] / 127.5) - 1.0 for index in range(4)], dtype=np.float64)


def _task_components(task_spec: Mapping[str, Any] | None) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    if not isinstance(task_spec, Mapping):
        return None
    family = task_spec.get("task_family")
    token = task_spec.get("task_token")
    anchor = task_spec.get("anchor")
    tolerance = task_spec.get("tolerance")
    if family not in TASK_FAMILIES or not isinstance(token, str) or not token.strip():
        return None
    if not isinstance(anchor, Mapping) or not isinstance(tolerance, Mapping):
        return None
    anchor_values = [_finite_float(anchor.get(field)) for field in _ANCHOR_FIELDS]
    tolerance_values = [_finite_float(tolerance.get(field)) for field in _ANCHOR_FIELDS]
    if any(value is None for value in anchor_values + tolerance_values):
        return None
    if any(float(value) <= 0.0 for value in tolerance_values if value is not None):
        return None
    family_vector = np.asarray([1.0 if family == item else 0.0 for item in TASK_FAMILIES])
    task = np.concatenate(
        [
            family_vector,
            _token_features(token),
            np.asarray(anchor_values, dtype=np.float64),
            np.log1p(np.asarray(tolerance_values, dtype=np.float64)),
        ]
    )
    return task, np.asarray(anchor_values, dtype=np.float64), np.asarray(tolerance_values, dtype=np.float64)


@dataclass(frozen=True)
class EvidenceAssessment:
    sufficient: bool
    reasons: tuple[str, ...]
    availability: tuple[tuple[str, bool], ...]
    provenance: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sufficient": self.sufficient,
            "reason_trace": list(self.reasons),
            "availability": dict(self.availability),
            "provenance": self.provenance,
        }


class EvidenceAwareAbstentionHead:
    """Hard fail-closed checks that sit above probabilistic classification."""

    def assess(
        self,
        candidate: Mapping[str, Any],
        task_spec: Mapping[str, Any] | None,
        evidence: Mapping[str, Any] | None,
    ) -> EvidenceAssessment:
        source = evidence if isinstance(evidence, Mapping) else {}
        availability_raw = source.get("availability")
        availability_source = availability_raw if isinstance(availability_raw, Mapping) else {}
        availability = tuple(
            (field, availability_source.get(field) is True) for field in _REQUIRED_AVAILABILITY
        )
        reasons: list[str] = []
        for field, present in availability:
            if not present:
                reasons.append(f"EVIDENCE_{field.upper()}_UNAVAILABLE")

        if _route_features(candidate.get("pred_route_raw")) is None:
            reasons.append("REQUIRED_ROUTE_FIELD_INVALID_OR_MISSING")
        if _speed_features(candidate.get("pred_speed_wps_raw")) is None:
            reasons.append("REQUIRED_SPEED_FIELD_INVALID_OR_MISSING")
        if candidate.get("plan_frame") != "MODEL_LOCAL_RAW":
            reasons.append("MODEL_LOCAL_RAW_PROVENANCE_NOT_ESTABLISHED")
        if _task_components(task_spec) is None:
            reasons.append("TASK_ANCHOR_INVALID_OR_MISSING")

        provenance = source.get("provenance")
        if provenance not in DATA_PROVENANCE:
            reasons.append("DATA_PROVENANCE_INVALID_OR_MISSING")
            provenance_value = None
        else:
            provenance_value = str(provenance)
        unique = tuple(sorted(set(reasons)))
        return EvidenceAssessment(
            sufficient=not unique,
            reasons=unique or ("EVIDENCE_CONTRACT_SATISFIED",),
            availability=availability,
            provenance=provenance_value,
        )


@dataclass(frozen=True)
class EncodedPlan:
    embedding: np.ndarray
    task_embedding: np.ndarray
    raw_features: np.ndarray
    evidence_mask: np.ndarray


class TaskConditionedPlanEncoder:
    """Extract raw structural descriptors and explicit task-relative interactions.

    Descriptors remain in ``RAW_UNIT`` / ``MODEL_LOCAL_RAW``.  Signed curvature-like values are
    numeric sequence descriptors only and never receive physical left/right semantics.
    """

    def __init__(self, *, task_conditioning: bool = True) -> None:
        self.task_conditioning = task_conditioning

    def encode(
        self,
        candidate: Mapping[str, Any],
        task_spec: Mapping[str, Any] | None,
        evidence: Mapping[str, Any] | None,
    ) -> EncodedPlan:
        route = _route_features(candidate.get("pred_route_raw"))
        speed = _speed_features(candidate.get("pred_speed_wps_raw"))
        raw = np.concatenate(
            [
                route if route is not None else np.zeros(13, dtype=np.float64),
                speed if speed is not None else np.zeros(10, dtype=np.float64),
            ]
        )
        components = _task_components(task_spec)
        if components is None or not self.task_conditioning:
            task = np.zeros(15, dtype=np.float64)
            interactions = np.zeros(13, dtype=np.float64)
        else:
            task, anchor, tolerance = components
            selected = np.asarray([raw[1], raw[2], raw[8], raw[16]], dtype=np.float64)
            delta = (selected - anchor) / np.maximum(tolerance, 1e-9)
            interactions = np.concatenate(
                [delta, np.abs(delta), np.square(delta), [float(np.max(np.abs(delta)))]],
            )
        source = evidence if isinstance(evidence, Mapping) else {}
        availability = source.get("availability")
        availability = availability if isinstance(availability, Mapping) else {}
        mask = np.asarray(
            [1.0 if availability.get(field) is True else 0.0 for field in _REQUIRED_AVAILABILITY],
            dtype=np.float64,
        )
        embedding = np.concatenate([raw, task, interactions, mask])
        if not np.isfinite(embedding).all():
            raise ValueError("NONFINITE_PLAN_EMBEDDING")
        return EncodedPlan(embedding=embedding, task_embedding=task, raw_features=raw, evidence_mask=mask)


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=1, keepdims=True)


class _LinearSoftmax:
    def __init__(self, labels: Sequence[str], *, seed: int) -> None:
        self.labels = tuple(labels)
        self.seed = int(seed)
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None
        self.weights: np.ndarray | None = None
        self.bias: np.ndarray | None = None

    def fit(
        self,
        features: Sequence[np.ndarray],
        targets: Sequence[str],
        *,
        epochs: int = 700,
        learning_rate: float = 0.06,
        l2: float = 1e-3,
    ) -> None:
        if not features or len(features) != len(targets):
            raise ValueError("EMPTY_OR_MISMATCHED_TRAINING_BATCH")
        x = np.stack(features).astype(np.float64)
        if not np.isfinite(x).all():
            raise ValueError("NONFINITE_TRAINING_FEATURE")
        try:
            y = np.asarray([self.labels.index(target) for target in targets], dtype=np.int64)
        except ValueError as exc:
            raise ValueError("UNKNOWN_TRAINING_TARGET") from exc
        self.mean = np.mean(x, axis=0)
        self.scale = np.std(x, axis=0)
        self.scale[self.scale < 1e-8] = 1.0
        z = (x - self.mean) / self.scale
        rng = np.random.default_rng(self.seed)
        self.weights = rng.normal(0.0, 0.01, size=(z.shape[1], len(self.labels)))
        self.bias = np.zeros(len(self.labels), dtype=np.float64)
        counts = np.bincount(y, minlength=len(self.labels)).astype(np.float64)
        class_weight = len(y) / (len(self.labels) * np.maximum(counts, 1.0))
        sample_weight = class_weight[y]
        one_hot = np.eye(len(self.labels), dtype=np.float64)[y]
        for epoch in range(epochs):
            logits = z @ self.weights + self.bias
            probabilities = _softmax(logits)
            error = (probabilities - one_hot) * sample_weight[:, None]
            denominator = float(np.sum(sample_weight))
            gradient_w = z.T @ error / denominator + l2 * self.weights
            gradient_b = np.sum(error, axis=0) / denominator
            rate = learning_rate / math.sqrt(1.0 + epoch / 200.0)
            self.weights -= rate * gradient_w
            self.bias -= rate * gradient_b

    def probabilities(self, feature: np.ndarray) -> np.ndarray:
        if self.mean is None or self.scale is None or self.weights is None or self.bias is None:
            raise RuntimeError("HEAD_NOT_FITTED")
        z = (np.asarray(feature, dtype=np.float64) - self.mean) / self.scale
        return _softmax((z @ self.weights + self.bias)[None, :])[0]


class CandidateOutcomeHead:
    def __init__(self, *, seed: int = 0) -> None:
        self.classifier = _LinearSoftmax(CANDIDATE_LABELS, seed=seed)

    def fit(self, embeddings: Sequence[np.ndarray], labels: Sequence[str]) -> None:
        self.classifier.fit(embeddings, labels)

    def probabilities(self, embedding: np.ndarray) -> dict[str, float]:
        values = self.classifier.probabilities(embedding)
        return {label: float(values[index]) for index, label in enumerate(CANDIDATE_LABELS)}


class SymmetricPairComparator:
    """A pair head whose feature map is exactly invariant to A/B ordering."""

    def __init__(self, *, seed: int = 1, pairwise_interaction: bool = True) -> None:
        self.pairwise_interaction = pairwise_interaction
        self.classifier = _LinearSoftmax(PAIR_LABELS, seed=seed)

    def pair_features(
        self, left: EncodedPlan, right: EncodedPlan, task_embedding: np.ndarray
    ) -> np.ndarray:
        mean = (left.embedding + right.embedding) * 0.5
        if not self.pairwise_interaction:
            return np.concatenate([mean, task_embedding])
        absolute = np.abs(left.embedding - right.embedding)
        product = left.embedding * right.embedding
        return np.concatenate([mean, absolute, product, task_embedding])

    def fit(self, features: Sequence[np.ndarray], labels: Sequence[str]) -> None:
        self.classifier.fit(features, labels)

    def probabilities(
        self, left: EncodedPlan, right: EncodedPlan, task_embedding: np.ndarray
    ) -> dict[str, float]:
        values = self.classifier.probabilities(self.pair_features(left, right, task_embedding))
        return {label: float(values[index]) for index, label in enumerate(PAIR_LABELS)}


def _prediction_envelope(
    *,
    label: str,
    probabilities: Mapping[str, float],
    abstained: bool,
    reasons: Sequence[str],
    evidence: EvidenceAssessment,
) -> dict[str, Any]:
    confidence = float(probabilities.get(label, 1.0 if label == "UNKNOWN" else 0.0))
    return {
        "label": label,
        "confidence": confidence,
        "probabilities": {key: float(probabilities[key]) for key in sorted(probabilities)},
        "abstained": bool(abstained),
        "reason_trace": list(reasons),
        "evidence": evidence.to_dict(),
        "frame": "MODEL_LOCAL_RAW",
        "unit": "RAW_UNIT",
        "raw_unit_interpreted_as_metre": False,
        "carla_world_assumed": False,
        "physical_left_right_claimed": False,
        "safety_inference_performed": False,
        "mode": "DIAGNOSTIC_ONLY",
        "authorization_eligible": False,
        "safety_critical_eligible": False,
        "control_authorized": False,
        "evidence_designation": list(METHOD_DESIGNATION),
    }


class EvidenceAwarePairwiseModel:
    """Trainable method envelope with deterministic hard abstention and reason traces."""

    def __init__(
        self,
        *,
        seed: int = 17,
        task_conditioning: bool = True,
        pairwise_interaction: bool = True,
        unknown_head: bool = True,
        confidence_threshold: float = 0.0,
        lambda_pair: float = 1.0,
        lambda_swap: float = 0.25,
        lambda_selective: float = 0.5,
    ) -> None:
        self.seed = int(seed)
        self.encoder = TaskConditionedPlanEncoder(task_conditioning=task_conditioning)
        self.candidate_head = CandidateOutcomeHead(seed=seed)
        self.pair_comparator = SymmetricPairComparator(
            seed=seed + 1, pairwise_interaction=pairwise_interaction
        )
        self.abstention_head = EvidenceAwareAbstentionHead()
        self.unknown_head = bool(unknown_head)
        self.confidence_threshold = float(confidence_threshold)
        self.lambda_pair = float(lambda_pair)
        self.lambda_swap = float(lambda_swap)
        self.lambda_selective = float(lambda_selective)

    @staticmethod
    def _parts(case: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        candidates = case.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 2:
            raise ValueError("PAIR_CASE_REQUIRES_EXACTLY_TWO_CANDIDATES")
        if not all(isinstance(item, Mapping) for item in candidates):
            raise ValueError("PAIR_CASE_CANDIDATE_INVALID")
        return candidates[0], candidates[1]

    def fit(self, cases: Sequence[Mapping[str, Any]]) -> "EvidenceAwarePairwiseModel":
        candidate_features: list[np.ndarray] = []
        candidate_targets: list[str] = []
        pair_features: list[np.ndarray] = []
        pair_targets: list[str] = []
        for case in cases:
            left, right = self._parts(case)
            task = case.get("task_spec")
            encoded_left = self.encoder.encode(left, task, left.get("evidence"))
            encoded_right = self.encoder.encode(right, task, right.get("evidence"))
            labels = case.get("candidate_labels")
            if not isinstance(labels, Mapping):
                raise ValueError("CANDIDATE_LABELS_MISSING")
            for candidate, encoded in ((left, encoded_left), (right, encoded_right)):
                candidate_id = str(candidate.get("candidate_id", ""))
                label = labels.get(candidate_id)
                if label not in CANDIDATE_LABELS:
                    raise ValueError("CANDIDATE_LABEL_INVALID")
                candidate_features.append(encoded.embedding)
                candidate_targets.append(str(label))
            pair_label = case.get("pair_label")
            if pair_label not in PAIR_LABELS:
                raise ValueError("PAIR_LABEL_INVALID")
            pair_features.append(
                self.pair_comparator.pair_features(
                    encoded_left, encoded_right, encoded_left.task_embedding
                )
            )
            pair_targets.append(str(pair_label))
        self.candidate_head.fit(candidate_features, candidate_targets)
        self.pair_comparator.fit(pair_features, pair_targets)
        return self

    @staticmethod
    def _select_label(
        probabilities: Mapping[str, float], labels: Sequence[str]
    ) -> tuple[str, float]:
        label = max(labels, key=lambda item: (probabilities.get(item, -1.0), -labels.index(item)))
        return label, float(probabilities[label])

    def _candidate_prediction(
        self,
        candidate: Mapping[str, Any],
        task: Mapping[str, Any] | None,
        encoded: EncodedPlan,
    ) -> dict[str, Any]:
        assessment = self.abstention_head.assess(candidate, task, candidate.get("evidence"))
        probabilities = self.candidate_head.probabilities(encoded.embedding)
        if self.unknown_head and not assessment.sufficient:
            probabilities = dict(probabilities)
            probabilities["UNKNOWN"] = max(probabilities["UNKNOWN"], 1.0)
            total = sum(probabilities.values())
            probabilities = {key: value / total for key, value in probabilities.items()}
            return _prediction_envelope(
                label="UNKNOWN",
                probabilities=probabilities,
                abstained=True,
                reasons=("EVIDENCE_AWARE_ABSTENTION", *assessment.reasons),
                evidence=assessment,
            )
        eligible = CANDIDATE_LABELS if self.unknown_head else CANDIDATE_LABELS[:2]
        label, confidence = self._select_label(probabilities, eligible)
        if self.unknown_head and label != "UNKNOWN" and confidence < self.confidence_threshold:
            label = "UNKNOWN"
            abstained = True
            reasons = ("SELECTIVE_CONFIDENCE_ABSTENTION",)
        else:
            abstained = label == "UNKNOWN"
            reasons = (
                "CANDIDATE_HEAD_UNKNOWN" if label == "UNKNOWN" else f"CANDIDATE_HEAD_{label}",
            )
        return _prediction_envelope(
            label=label,
            probabilities=probabilities,
            abstained=abstained,
            reasons=reasons,
            evidence=assessment,
        )

    def predict(self, case: Mapping[str, Any]) -> dict[str, Any]:
        left, right = self._parts(case)
        task = case.get("task_spec")
        task_mapping = task if isinstance(task, Mapping) else None
        encoded_left = self.encoder.encode(left, task_mapping, left.get("evidence"))
        encoded_right = self.encoder.encode(right, task_mapping, right.get("evidence"))
        left_prediction = self._candidate_prediction(left, task_mapping, encoded_left)
        right_prediction = self._candidate_prediction(right, task_mapping, encoded_right)
        assessments = (
            self.abstention_head.assess(left, task_mapping, left.get("evidence")),
            self.abstention_head.assess(right, task_mapping, right.get("evidence")),
        )
        pair_probabilities = self.pair_comparator.probabilities(
            encoded_left, encoded_right, encoded_left.task_embedding
        )
        insufficient = not all(item.sufficient for item in assessments)
        if self.unknown_head and insufficient:
            reasons = tuple(
                sorted(
                    {
                        "PAIR_EVIDENCE_AWARE_ABSTENTION",
                        *(reason for item in assessments for reason in item.reasons),
                    }
                )
            )
            pair_probabilities = dict(pair_probabilities)
            pair_probabilities["UNKNOWN"] = max(pair_probabilities["UNKNOWN"], 1.0)
            total = sum(pair_probabilities.values())
            pair_probabilities = {key: value / total for key, value in pair_probabilities.items()}
            pair_label = "UNKNOWN"
            pair_abstained = True
        else:
            eligible = PAIR_LABELS if self.unknown_head else PAIR_LABELS[:2]
            pair_label, pair_confidence = self._select_label(pair_probabilities, eligible)
            if (
                self.unknown_head
                and pair_label != "UNKNOWN"
                and pair_confidence < self.confidence_threshold
            ):
                pair_label = "UNKNOWN"
                pair_abstained = True
                reasons = ("PAIR_SELECTIVE_CONFIDENCE_ABSTENTION",)
            else:
                pair_abstained = pair_label == "UNKNOWN"
                reasons = (
                    "PAIR_HEAD_UNKNOWN"
                    if pair_label == "UNKNOWN"
                    else f"PAIR_HEAD_{pair_label}",
                )
        pair_assessment = EvidenceAssessment(
            sufficient=not insufficient,
            reasons=tuple(sorted(set(reason for item in assessments for reason in item.reasons))),
            availability=tuple(
                (field, all(dict(item.availability)[field] for item in assessments))
                for field in _REQUIRED_AVAILABILITY
            ),
            provenance=(
                assessments[0].provenance
                if assessments[0].provenance == assessments[1].provenance
                else None
            ),
        )
        pair_prediction = _prediction_envelope(
            label=pair_label,
            probabilities=pair_probabilities,
            abstained=pair_abstained,
            reasons=reasons,
            evidence=pair_assessment,
        )
        return {
            "schema_version": "driveclarify.task_conditioned_pairwise_consequence.v0",
            "case_id": case.get("case_id"),
            "candidate_outcomes": {
                str(left.get("candidate_id")): left_prediction,
                str(right.get("candidate_id")): right_prediction,
            },
            "pair": pair_prediction,
            "pair_swap_invariant_by_construction": True,
            "backbone_agnostic": True,
            "mode": "DIAGNOSTIC_ONLY",
            "authorization_eligible": False,
            "safety_critical_eligible": False,
            "control_authorized": False,
            "evidence_designation": list(METHOD_DESIGNATION),
        }

    def compute_loss(
        self,
        cases: Sequence[Mapping[str, Any]],
        *,
        optimization_mask: Sequence[bool] | None = None,
    ) -> dict[str, float]:
        if optimization_mask is None:
            selected = list(cases)
        else:
            if len(optimization_mask) != len(cases):
                raise ValueError("OPTIMIZATION_MASK_LENGTH_MISMATCH")
            selected = [case for case, enabled in zip(cases, optimization_mask) if enabled]
        if not selected:
            raise ValueError("NO_OPTIMIZABLE_SUPERVISED_BATCH")

        candidate_losses: list[float] = []
        pair_losses: list[float] = []
        swap_losses: list[float] = []
        selective_losses: list[float] = []
        for case in selected:
            left, right = self._parts(case)
            task = case.get("task_spec")
            task_mapping = task if isinstance(task, Mapping) else None
            encoded_left = self.encoder.encode(left, task_mapping, left.get("evidence"))
            encoded_right = self.encoder.encode(right, task_mapping, right.get("evidence"))
            labels = case.get("candidate_labels")
            if not isinstance(labels, Mapping):
                raise ValueError("CANDIDATE_LABELS_MISSING")
            for candidate, encoded in ((left, encoded_left), (right, encoded_right)):
                target = labels.get(str(candidate.get("candidate_id")))
                if target not in CANDIDATE_LABELS:
                    raise ValueError("CANDIDATE_LABEL_INVALID")
                probabilities = self.candidate_head.probabilities(encoded.embedding)
                candidate_losses.append(-math.log(max(probabilities[str(target)], 1e-12)))
                assessment = self.abstention_head.assess(
                    candidate, task_mapping, candidate.get("evidence")
                )
                if not assessment.sufficient:
                    selective_losses.append(-math.log(max(probabilities["UNKNOWN"], 1e-12)))
            forward = self.pair_comparator.probabilities(
                encoded_left, encoded_right, encoded_left.task_embedding
            )
            swapped = self.pair_comparator.probabilities(
                encoded_right, encoded_left, encoded_left.task_embedding
            )
            target_pair = case.get("pair_label")
            if target_pair not in PAIR_LABELS:
                raise ValueError("PAIR_LABEL_INVALID")
            pair_losses.append(-math.log(max(forward[str(target_pair)], 1e-12)))
            swap_losses.append(
                sum((forward[label] - swapped[label]) ** 2 for label in PAIR_LABELS)
            )
            if not all(
                self.abstention_head.assess(candidate, task_mapping, candidate.get("evidence")).sufficient
                for candidate in (left, right)
            ):
                selective_losses.append(-math.log(max(forward["UNKNOWN"], 1e-12)))

        candidate_loss = float(np.mean(candidate_losses))
        pair_loss = float(np.mean(pair_losses))
        swap_loss = float(np.mean(swap_losses))
        selective_loss = float(np.mean(selective_losses)) if selective_losses else 0.0
        total = (
            candidate_loss
            + self.lambda_pair * pair_loss
            + self.lambda_swap * swap_loss
            + self.lambda_selective * selective_loss
        )
        return {
            "candidate": candidate_loss,
            "pair": pair_loss,
            "swap": swap_loss,
            "selective": selective_loss,
            "total": float(total),
        }


class RuleBridge:
    """Expose a learned diagnostic suggestion while preserving the authoritative rule result."""

    @staticmethod
    def bridge(
        method_prediction: Mapping[str, Any],
        authoritative_rule_output: Mapping[str, Any],
        *,
        decision_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        authoritative_copy = copy.deepcopy(dict(authoritative_rule_output))
        pair_label = method_prediction.get("pair", {}).get("label")
        context = DecisionContext.from_dict(str(pair_label), decision_context)
        diagnostic_recommendation = recommend_offline(context)
        hard_safety = authoritative_rule_output.get("hard_safety_violation")
        hard_rule = authoritative_rule_output.get("hard_rule_violation")
        frozen_pair = authoritative_rule_output.get("pair_class")
        frozen_recommendation = authoritative_rule_output.get("recommendation")
        preservation_reasons = ["AUTHORITATIVE_RULE_OUTPUT_PRESERVED"]
        if hard_safety in (True, "TRUE", "UNKNOWN", None):
            preservation_reasons.append("HARD_SAFETY_GATE_NOT_OVERRIDDEN")
        if hard_rule in (True, "TRUE", "UNKNOWN", None):
            preservation_reasons.append("HARD_RULE_GATE_NOT_OVERRIDDEN")
        if frozen_pair == "UNKNOWN" or frozen_recommendation == "FALLBACK_RECOMMENDED":
            preservation_reasons.append("FROZEN_UNKNOWN_OR_FALLBACK_NOT_OVERRIDDEN")
        preservation_reasons.extend(
            [
                "CACHE_CONTRACT_NOT_OVERRIDDEN",
                "QUERY_EPISODE_NOT_OVERRIDDEN",
                "TIMEOUT_NOT_OVERRIDDEN",
                "FALLBACK_SEMANTICS_NOT_OVERRIDDEN",
            ]
        )
        return {
            "schema_version": "driveclarify.rule_bridge.v0",
            "authoritative_output": authoritative_copy,
            "diagnostic_suggestion": {
                "pair_label": pair_label,
                "candidate_outcomes": copy.deepcopy(method_prediction.get("candidate_outcomes")),
                "offline_recommendation": diagnostic_recommendation,
            },
            "override_applied": False,
            "used_for_control": False,
            "reason_trace": sorted(set(preservation_reasons)),
            "mode": "DIAGNOSTIC_ONLY",
            "authorization_eligible": False,
            "safety_critical_eligible": False,
            "control_authorized": False,
        }
