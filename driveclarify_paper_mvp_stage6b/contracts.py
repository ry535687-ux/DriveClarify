"""Shared, strict contracts for Stage 6B policy and evaluator boundaries."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from driveclarify_paper_mvp_evaluation.contracts import (
    METHOD_ORDER,
    InteractionPhase,
    RuntimeAction,
)


class Stage6BContractError(ValueError):
    """Raised when a unified runtime boundary would become ambiguous."""


class PlanSource(str, Enum):
    ORIGINAL_SIMLINGO_PLAN = "ORIGINAL_SIMLINGO_PLAN"
    CURRENT_VALID_HOLDING_PLAN = "CURRENT_VALID_HOLDING_PLAN"
    CANDIDATE_CONDITIONED_PLAN = "CANDIDATE_CONDITIONED_PLAN"
    POLICY_STOP_SPEED_PLAN = "POLICY_STOP_SPEED_PLAN"
    BASELINE_FALLBACK_PLAN = "BASELINE_FALLBACK_PLAN"


class Freshness(str, Enum):
    FRESH = "FRESH"
    INVALIDATED = "INVALIDATED"
    STALE = "STALE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EvidenceStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    VALID_BUT_NOT_TRIGGERED = "VALID_BUT_NOT_TRIGGERED"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE_BY_DESIGN = "UNAVAILABLE_BY_DESIGN"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


class FailureClass(str, Enum):
    NONE = "NONE"
    AMBIGUITY_HANDLING_FAILURE = "A_AMBIGUITY_HANDLING_FAILURE"
    CANDIDATE_OR_CONSEQUENCE_FAILURE = "B_CANDIDATE_OR_CONSEQUENCE_FAILURE"
    DECISION_FAILURE = "C_DECISION_FAILURE"
    EXECUTION_OR_CONTROL_FAILURE = "D_EXECUTION_OR_CONTROL_FAILURE"
    ENVIRONMENT_OR_SIMULATOR_FAILURE = "E_ENVIRONMENT_OR_SIMULATOR_FAILURE"
    UNKNOWN_OR_INSUFFICIENT_EVIDENCE = "F_UNKNOWN_OR_INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class ForwardAccounting:
    """Exact event-level forward/PID accounting, separate from wall-clock ticks."""

    compute_budget_case: str
    required_candidate_forwards: int
    observed_candidate_forwards: int
    observed_normal_forwards: int
    candidate_forward_input_sha256: tuple[str, ...] = ()
    candidate_forward_output_sha256: tuple[str, ...] = ()
    candidate_forward_latencies_seconds: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if not self.compute_budget_case:
            raise Stage6BContractError("COMPUTE_BUDGET_CASE_REQUIRED")
        for name in (
            "required_candidate_forwards",
            "observed_candidate_forwards",
            "observed_normal_forwards",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise Stage6BContractError(name.upper() + "_NONNEGATIVE_INTEGER_REQUIRED")
        evidence_lengths = (
            len(self.candidate_forward_input_sha256),
            len(self.candidate_forward_output_sha256),
            len(self.candidate_forward_latencies_seconds),
        )
        if any(length not in {0, self.observed_candidate_forwards} for length in evidence_lengths):
            raise Stage6BContractError("CANDIDATE_FORWARD_EVIDENCE_COUNT_MISMATCH")
        for values in (
            self.candidate_forward_input_sha256,
            self.candidate_forward_output_sha256,
        ):
            if any(not _sha256(value) for value in values):
                raise Stage6BContractError("CANDIDATE_FORWARD_DIGEST_INVALID")
        for value in self.candidate_forward_latencies_seconds:
            if not _finite_nonnegative(value):
                raise Stage6BContractError("CANDIDATE_FORWARD_LATENCY_INVALID")

    @property
    def budget_compliant(self) -> bool:
        return self.observed_candidate_forwards == self.required_candidate_forwards


@dataclass(frozen=True)
class MethodOutput:
    """The sole output shape accepted from every frozen method adapter."""

    method_id: str
    action: RuntimeAction
    selected_candidate_id: str | None
    plan_source: PlanSource
    interaction_phase: InteractionPhase
    decision_reason: tuple[str, ...]
    runtime_evidence_ids: tuple[str, ...]
    freshness: Freshness
    forward_accounting: ForwardAccounting
    query_request: bool = False
    wait_request: bool = False
    stop_request: bool = False
    authority_receipt_id: str | None = None

    def __post_init__(self) -> None:
        if self.method_id not in METHOD_ORDER:
            raise Stage6BContractError("UNKNOWN_METHOD_ID:" + self.method_id)
        if not isinstance(self.action, RuntimeAction):
            raise Stage6BContractError("RUNTIME_ACTION_REQUIRED")
        if not self.decision_reason:
            raise Stage6BContractError("DECISION_REASON_REQUIRED")
        expected = {
            RuntimeAction.ASK: (True, False, False),
            RuntimeAction.WAIT: (False, True, False),
            RuntimeAction.STOP: (False, False, True),
        }.get(self.action, (False, False, False))
        if (self.query_request, self.wait_request, self.stop_request) != expected:
            raise Stage6BContractError("METHOD_OUTPUT_SIDE_EFFECT_FLAGS_INVALID")
        if self.action is RuntimeAction.ACT and self.method_id != "original_simlingo":
            if self.selected_candidate_id is None:
                raise Stage6BContractError("NON_ORIGINAL_ACT_REQUIRES_CANDIDATE")
        if self.action is not RuntimeAction.ACT and self.selected_candidate_id is not None:
            raise Stage6BContractError("NON_ACT_CANDIDATE_FORBIDDEN")
        expected_source = {
            RuntimeAction.ASK: PlanSource.CURRENT_VALID_HOLDING_PLAN,
            RuntimeAction.WAIT: PlanSource.CURRENT_VALID_HOLDING_PLAN,
            RuntimeAction.STOP: PlanSource.POLICY_STOP_SPEED_PLAN,
            RuntimeAction.FALLBACK: PlanSource.BASELINE_FALLBACK_PLAN,
        }.get(self.action)
        if expected_source is not None and self.plan_source is not expected_source:
            raise Stage6BContractError("ACTION_PLAN_SOURCE_MISMATCH")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class LabelFirewallCounters:
    """Mutable counters live at the boundary; policy-label reads must stay zero."""

    policy_expected_decision_reads: int = 0
    policy_gold_candidate_index_reads: int = 0
    policy_evaluator_annotation_reads: int = 0
    environment_passenger_intent_reads: int = 0
    post_episode_gold_intent_reads: int = 0

    def assert_runtime_clean(self) -> None:
        if any(
            value != 0
            for value in (
                self.policy_expected_decision_reads,
                self.policy_gold_candidate_index_reads,
                self.policy_evaluator_annotation_reads,
            )
        ):
            raise Stage6BContractError("POLICY_LABEL_FIREWALL_VIOLATION")

    def to_dict(self) -> dict[str, int]:
        self.assert_runtime_clean()
        return asdict(self)


@dataclass(frozen=True)
class EvidenceValue:
    status: EvidenceStatus
    value: Any
    source: str
    reason_codes: tuple[str, ...] = ()
    coverage: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source:
            raise Stage6BContractError("EVIDENCE_SOURCE_REQUIRED")
        if self.status in {
            EvidenceStatus.UNKNOWN,
            EvidenceStatus.UNAVAILABLE_BY_DESIGN,
            EvidenceStatus.INVALID_EVIDENCE,
        } and self.value is not None:
            raise Stage6BContractError("NONAVAILABLE_EVIDENCE_MUST_HAVE_NULL_VALUE")
        if self.status is EvidenceStatus.AVAILABLE and self.value is None:
            raise Stage6BContractError("AVAILABLE_EVIDENCE_REQUIRES_VALUE")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _finite_nonnegative(value: Any) -> bool:
    return bool(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "EvidenceStatus",
    "EvidenceValue",
    "FailureClass",
    "ForwardAccounting",
    "Freshness",
    "LabelFirewallCounters",
    "MethodOutput",
    "PlanSource",
    "Stage6BContractError",
]
