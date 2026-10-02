"""Fail-closed numeric candidate stability gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Sequence, Tuple

from .metrics import analyze_candidate_set, trajectory_finite, trajectory_shape
from .types import (
    CandidateBatchContext,
    CandidateResult,
    GateResult,
    GateStatus,
    StabilityClassification,
)


@dataclass(frozen=True)
class GateConfig:
    """Explicit diagnostic settings; none are claimed as final paper thresholds."""

    epsilon: float = 1e-12
    strong_separation_ratio: float = 2.0
    route_min_between_l2: float = 0.10
    speed_min_between_l2: float = 0.05
    expected_route_shape: Tuple[int, int, int] = (1, 20, 2)
    expected_speed_shape: Tuple[int, int, int] = (1, 10, 2)
    minimum_repetitions_per_interpretation: int = 2
    threshold_scope: str = (
        "OFFLINE_DIAGNOSTIC_ONLY; absolute minima inherited from psplan13 "
        "model-local A/B diagnostics; ratio 2.0 means at-least-twice-noise "
        "and is not a final paper or safety threshold"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "epsilon": self.epsilon,
            "strong_separation_ratio": self.strong_separation_ratio,
            "route_min_between_l2": self.route_min_between_l2,
            "speed_min_between_l2": self.speed_min_between_l2,
            "expected_route_shape": list(self.expected_route_shape),
            "expected_speed_shape": list(self.expected_speed_shape),
            "minimum_repetitions_per_interpretation": (
                self.minimum_repetitions_per_interpretation
            ),
            "threshold_scope": self.threshold_scope,
        }


class CandidateStabilityGate:
    def __init__(self, config: GateConfig) -> None:
        self.config = config

    def evaluate(
        self,
        context: CandidateBatchContext,
        candidates: Sequence[CandidateResult],
    ) -> GateResult:
        invalid = self._invalid_reasons(context, candidates)
        if invalid:
            return GateResult(
                status=GateStatus.INVALID_CANDIDATE,
                reason_codes=tuple(sorted(set(invalid))),
                metrics={},
                route_classification=None,
                speed_classification=None,
                candidate_mechanism_supported=False,
                consequence_diagnostics_allowed=False,
                decision_evaluation_allowed=False,
                act_ask_wait_allowed=False,
                thresholds=self.config.to_dict(),
                hash_evidence=self._hash_evidence(candidates),
            )

        counts: Dict[str, int] = {}
        for candidate in candidates:
            counts[candidate.interpretation_id] = (
                counts.get(candidate.interpretation_id, 0) + 1
            )
        if (
            set(counts) != {"A", "B"}
            or any(
                value < self.config.minimum_repetitions_per_interpretation
                for value in counts.values()
            )
            or {candidate.candidate_id for candidate in candidates}
            != {"A1", "A2", "B1", "B2"}
        ):
            return GateResult(
                status=GateStatus.INSUFFICIENT_EVIDENCE,
                reason_codes=("REPEAT_EVIDENCE_INSUFFICIENT",),
                metrics={},
                route_classification=None,
                speed_classification=None,
                candidate_mechanism_supported=False,
                consequence_diagnostics_allowed=False,
                decision_evaluation_allowed=False,
                act_ask_wait_allowed=False,
                thresholds=self.config.to_dict(),
                hash_evidence=self._hash_evidence(candidates),
            )

        metrics = analyze_candidate_set(
            candidates,
            epsilon=self.config.epsilon,
            strong_separation_ratio=self.config.strong_separation_ratio,
        )
        route_class = StabilityClassification(
            metrics["channels"]["route"]["classification"]
        )
        speed_class = StabilityClassification(
            metrics["channels"]["speed"]["classification"]
        )
        route_separated = (
            metrics["channels"]["route"]["between"]
            >= self.config.route_min_between_l2
        )
        speed_separated = (
            metrics["channels"]["speed"]["between"]
            >= self.config.speed_min_between_l2
        )
        absolutely_separated = route_separated and speed_separated
        classifications = (route_class, speed_class)
        strongly_stable = all(
            item is StabilityClassification.STRONGLY_SEPARATED
            for item in classifications
        )
        zero_repeat_drift = all(
            max(
                metrics["channels"][channel]["within_A"],
                metrics["channels"][channel]["within_B"],
            )
            <= self.config.epsilon
            for channel in ("route", "speed")
        )
        stable = strongly_stable or zero_repeat_drift
        candidate_mechanism_supported = all(
            item
            in (
                StabilityClassification.STRONGLY_SEPARATED,
                StabilityClassification.SEPARATED_BUT_NOISY,
            )
            for item in classifications
        )

        reasons = []
        if route_class is StabilityClassification.NOISE_DOMINATED:
            reasons.append("ROUTE_REPEAT_NOISE_DOMINATES_SIGNAL")
        elif route_class is StabilityClassification.SEPARATED_BUT_NOISY:
            reasons.append("ROUTE_SEPARATED_BUT_REPEAT_NOISE_NONNEGLIGIBLE")
        if speed_class is StabilityClassification.NOISE_DOMINATED:
            reasons.append("SPEED_REPEAT_NOISE_DOMINATES_SIGNAL")
        elif speed_class is StabilityClassification.SEPARATED_BUT_NOISY:
            reasons.append("SPEED_SEPARATED_BUT_REPEAT_NOISE_NONNEGLIGIBLE")
        if not route_separated:
            reasons.append("ROUTE_BETWEEN_BELOW_RECORDED_DIAGNOSTIC_MINIMUM")
        if not speed_separated:
            reasons.append("SPEED_BETWEEN_BELOW_RECORDED_DIAGNOSTIC_MINIMUM")

        if stable and absolutely_separated:
            status = GateStatus.STABLE_AND_SEPARATED
            decision_allowed = True
        elif stable:
            status = GateStatus.STABLE_BUT_NOT_SEPARATED
            decision_allowed = True
        elif absolutely_separated:
            status = GateStatus.SEPARATED_BUT_UNSTABLE
            decision_allowed = False
        else:
            status = GateStatus.UNSTABLE
            decision_allowed = False

        if not reasons:
            reasons.append(status.value)
        return GateResult(
            status=status,
            reason_codes=tuple(reasons),
            metrics=metrics,
            route_classification=route_class,
            speed_classification=speed_class,
            candidate_mechanism_supported=candidate_mechanism_supported,
            consequence_diagnostics_allowed=True,
            decision_evaluation_allowed=decision_allowed,
            act_ask_wait_allowed=False,
            thresholds=self.config.to_dict(),
            hash_evidence=self._hash_evidence(candidates),
        )

    def _invalid_reasons(
        self,
        context: CandidateBatchContext,
        candidates: Sequence[CandidateResult],
    ) -> Tuple[str, ...]:
        reasons = []
        for candidate in candidates:
            prefix = candidate.candidate_id or "UNLABELED"
            if not candidate.valid:
                reasons.append(prefix + ":CANDIDATE_MARKED_INVALID")
            reasons.extend(prefix + ":" + reason for reason in candidate.invalid_reasons)
            if candidate.observation_id != context.observation_id:
                reasons.append(prefix + ":OBSERVATION_IDENTITY_MISMATCH")
            if candidate.observation_digest != context.observation_digest:
                reasons.append(prefix + ":OBSERVATION_DIGEST_MISMATCH")
            if candidate.source_frame != context.source_frame:
                reasons.append(prefix + ":SOURCE_FRAME_MISMATCH")
            if candidate.freshness_token != context.freshness_token:
                reasons.append(prefix + ":STALE_CANDIDATE")
            if (
                candidate.model_instance_identity
                != context.model_instance_identity
            ):
                reasons.append(prefix + ":MODEL_INSTANCE_IDENTITY_MISMATCH")
            if trajectory_shape(candidate.route) != self.config.expected_route_shape:
                reasons.append(prefix + ":ROUTE_SHAPE_INVALID")
            if trajectory_shape(candidate.speed) != self.config.expected_speed_shape:
                reasons.append(prefix + ":SPEED_SHAPE_INVALID")
            if not trajectory_finite(candidate.route):
                reasons.append(prefix + ":ROUTE_NONFINITE")
            if not trajectory_finite(candidate.speed):
                reasons.append(prefix + ":SPEED_NONFINITE")
        return tuple(reasons)

    @staticmethod
    def _hash_evidence(
        candidates: Sequence[CandidateResult],
    ) -> Dict[str, bool]:
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        return {
            "A1_A2_route_hash_equal": (
                by_id["A1"].route_hash == by_id["A2"].route_hash
                if {"A1", "A2"}.issubset(by_id)
                else False
            ),
            "A1_A2_speed_hash_equal": (
                by_id["A1"].speed_hash == by_id["A2"].speed_hash
                if {"A1", "A2"}.issubset(by_id)
                else False
            ),
            "B1_B2_route_hash_equal": (
                by_id["B1"].route_hash == by_id["B2"].route_hash
                if {"B1", "B2"}.issubset(by_id)
                else False
            ),
            "B1_B2_speed_hash_equal": (
                by_id["B1"].speed_hash == by_id["B2"].speed_hash
                if {"B1", "B2"}.issubset(by_id)
                else False
            ),
        }
