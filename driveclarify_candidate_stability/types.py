"""Typed, immutable envelopes for recorded and future candidate execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple


Point = Tuple[float, float]
Trajectory = Tuple[Point, ...]


class StabilityClassification(str, Enum):
    STRONGLY_SEPARATED = "STRONGLY_SEPARATED"
    SEPARATED_BUT_NOISY = "SEPARATED_BUT_NOISY"
    NOISE_DOMINATED = "NOISE_DOMINATED"


class GateStatus(str, Enum):
    STABLE_AND_SEPARATED = "STABLE_AND_SEPARATED"
    STABLE_BUT_NOT_SEPARATED = "STABLE_BUT_NOT_SEPARATED"
    SEPARATED_BUT_UNSTABLE = "SEPARATED_BUT_UNSTABLE"
    UNSTABLE = "UNSTABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID_CANDIDATE = "INVALID_CANDIDATE"


class SemanticAction(str, Enum):
    ACT = "ACT"
    ASK = "ASK"
    WAIT = "WAIT"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class DeterministicSettings:
    """Observed settings plus the contract required by a future live backend."""

    model_eval_required: bool = True
    model_eval_observed: Optional[bool] = None
    inference_only_required: bool = True
    inference_only_observed: Optional[bool] = None
    restore_rng_before_each_repetition_required: bool = True
    rng_states_recorded: bool = False
    repetition_rng_state_equal: Optional[bool] = None
    isolate_mutable_cache_history_required: bool = True
    mutable_cache_history_isolated: Optional[bool] = None
    decoding_mode: str = "UNKNOWN"
    provenance: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateBatchContext:
    run_id: str
    observation_id: str
    observation_digest: str
    source_frame: Optional[int]
    closed_loop_state_identity: str
    ego_state_identity: Optional[str]
    navigation_identity: Optional[str]
    model_instance_identity: Optional[int]
    preprocessing_identity: str
    initial_rng_state_identities: Optional[Mapping[str, str]]
    cache_history_state_identity: Optional[str]
    freshness_token: str
    deterministic_settings: DeterministicSettings
    provenance: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateRequest:
    candidate_id: str
    interpretation: str
    expected_repetition_group: str


@dataclass(frozen=True)
class CandidateResult:
    candidate_id: str
    interpretation_id: str
    repetition_index: int
    observation_id: str
    observation_digest: str
    source_frame: Optional[int]
    freshness_token: str
    model_instance_identity: Optional[int]
    route: Trajectory
    speed: Trajectory
    language_output: Tuple[str, ...]
    language_token_ids: Optional[Tuple[int, ...]]
    route_hash: str
    speed_hash: str
    normalized_dtype: str
    normalized_device: str
    latency_seconds: Optional[float]
    valid: bool = True
    invalid_reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DifferenceMetrics:
    left_shape: Tuple[int, ...]
    right_shape: Tuple[int, ...]
    shape_equal: bool
    left_finite: bool
    right_finite: bool
    coordinate_count: int
    exact_equal: bool
    mean_absolute_difference: Optional[float]
    l2: Optional[float]
    rmse: Optional[float]
    maximum_absolute_difference: Optional[float]
    endpoint_l2: Optional[float]
    per_waypoint_l2: Tuple[float, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left_shape": list(self.left_shape),
            "right_shape": list(self.right_shape),
            "shape_equal": self.shape_equal,
            "left_finite": self.left_finite,
            "right_finite": self.right_finite,
            "coordinate_count": self.coordinate_count,
            "exact_equal": self.exact_equal,
            "mean_absolute_difference": self.mean_absolute_difference,
            "l2": self.l2,
            "rmse": self.rmse,
            "maximum_absolute_difference": self.maximum_absolute_difference,
            "endpoint_l2": self.endpoint_l2,
            "per_waypoint_l2": list(self.per_waypoint_l2),
        }


@dataclass(frozen=True)
class GateResult:
    status: GateStatus
    reason_codes: Tuple[str, ...]
    metrics: Mapping[str, Any]
    route_classification: Optional[StabilityClassification]
    speed_classification: Optional[StabilityClassification]
    candidate_mechanism_supported: bool
    consequence_diagnostics_allowed: bool
    decision_evaluation_allowed: bool
    act_ask_wait_allowed: bool
    thresholds: Mapping[str, Any]
    hash_evidence: Mapping[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
            "metrics": dict(self.metrics),
            "route_classification": (
                self.route_classification.value
                if self.route_classification is not None
                else None
            ),
            "speed_classification": (
                self.speed_classification.value
                if self.speed_classification is not None
                else None
            ),
            "candidate_mechanism_supported": self.candidate_mechanism_supported,
            "consequence_diagnostics_allowed": self.consequence_diagnostics_allowed,
            "decision_evaluation_allowed": self.decision_evaluation_allowed,
            "act_ask_wait_allowed": self.act_ask_wait_allowed,
            "thresholds": dict(self.thresholds),
            "hash_evidence": dict(self.hash_evidence),
        }
