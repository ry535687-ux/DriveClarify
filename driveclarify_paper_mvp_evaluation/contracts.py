"""Strict, label-firewalled contracts for the paper-MVP method suite.

The module intentionally contains no CARLA, SimLingo, Torch, or catalog import.
It accepts only runtime-produced fields.  Evaluation labels are represented by a
separate post-episode record in :mod:`metrics`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class ContractError(ValueError):
    """Raised when a runtime or frozen configuration contract is incomplete."""


class MethodId(str, Enum):
    ORIGINAL_SIMLINGO = "original_simlingo"
    DRIVECLARIFY = "driveclarify"
    ALWAYS_ASK = "always_ask"
    ALWAYS_STOP = "always_stop"
    ALWAYS_WAIT = "always_wait"
    NEVER_ASK = "never_ask"
    LANGUAGE_ONLY_UNCERTAINTY = "language_only_uncertainty"
    RISK_ONLY = "risk_only"


METHOD_ORDER: tuple[str, ...] = tuple(item.value for item in MethodId)


# A method decision declares required compute; live execution records observed
# invocations separately in ``EpisodeMetricRecord``.  Keeping these concepts
# separate prevents a failed/missing candidate forward from being reported as a
# scientifically valid zero-cost decision.
NORMAL_SIMLINGO_FORWARD_BUDGET = 1
CANDIDATE_FORWARD_BUDGETS: dict[str, dict[str, int]] = {
    MethodId.ORIGINAL_SIMLINGO.value: {
        "NORMAL_AMBIGUOUS_INSTRUCTION_FORWARD": 0,
    },
    MethodId.DRIVECLARIFY.value: {
        "INITIAL_K2_CANDIDATE_DECISION": 2,
        "POST_ANSWER_SINGLE_CANDIDATE_REPLAN": 1,
        "POST_INFORMATION_SINGLE_CANDIDATE_REPLAN": 1,
        "QUERY_HOLD_OR_TERMINAL_NO_REPLAN": 0,
    },
    MethodId.ALWAYS_ASK.value: {
        "INITIAL_CONSTANT_ASK_NO_CANDIDATE_PLAN": 0,
        "POST_ANSWER_SINGLE_CANDIDATE_REPLAN": 1,
        "QUERY_HOLD_OR_TERMINAL_NO_REPLAN": 0,
    },
    MethodId.ALWAYS_STOP.value: {
        "CONSTANT_STOP_NO_CANDIDATE_PLAN": 0,
    },
    MethodId.ALWAYS_WAIT.value: {
        "WAIT_OR_TERMINAL_NO_REPLAN": 0,
        "POST_INFORMATION_SINGLE_CANDIDATE_REPLAN": 1,
    },
    MethodId.NEVER_ASK.value: {
        "K2_RUNTIME_RANK_DECISION": 2,
    },
    MethodId.LANGUAGE_ONLY_UNCERTAINTY.value: {
        "INITIAL_LANGUAGE_GATE_NO_CANDIDATE_PLAN": 0,
        "INITIAL_LOW_UNCERTAINTY_K2_RUNTIME_RANK": 2,
        "POST_ANSWER_SINGLE_CANDIDATE_REPLAN": 1,
        "QUERY_HOLD_OR_TERMINAL_NO_REPLAN": 0,
    },
    MethodId.RISK_ONLY.value: {
        "INITIAL_K2_RISK_COMPARISON": 2,
        "POST_ANSWER_SINGLE_CANDIDATE_REPLAN": 1,
        "QUERY_HOLD_OR_TERMINAL_NO_REPLAN": 0,
    },
}


def candidate_forward_budget(method_id: str, compute_budget_case: str) -> int:
    """Return the exact candidate-conditioned forward budget for one decision."""

    cases = CANDIDATE_FORWARD_BUDGETS.get(method_id)
    if cases is None:
        raise ContractError(f"UNKNOWN_METHOD_ID:{method_id}")
    if compute_budget_case not in cases:
        raise ContractError(
            f"UNKNOWN_COMPUTE_BUDGET_CASE:{method_id}:{compute_budget_case}"
        )
    return cases[compute_budget_case]


class GoldDecision(str, Enum):
    ACT = "ACT"
    ASK = "ASK"
    WAIT = "WAIT"


class RuntimeAction(str, Enum):
    ACT = "ACT"
    ASK = "ASK"
    WAIT = "WAIT"
    STOP = "STOP"
    FALLBACK = "FALLBACK"


class InteractionPhase(str, Enum):
    INITIAL = "INITIAL"
    QUERY_PENDING = "QUERY_PENDING"
    ANSWER_RECEIVED = "ANSWER_RECEIVED"
    FUTURE_INFORMATION_ARRIVED = "FUTURE_INFORMATION_ARRIVED"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RuntimeCandidate:
    """One runtime-generated candidate; no annotation/catalog ID is allowed."""

    candidate_id: str
    rank_score: float | None
    risk_score: float | None
    source: str = "RUNTIME_GENERATED"

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ContractError("EMPTY_RUNTIME_CANDIDATE_ID")
        if self.source != "RUNTIME_GENERATED":
            raise ContractError("ANNOTATION_CANDIDATE_SOURCE_REJECTED")
        for name, value in (
            ("rank_score", self.rank_score),
            ("risk_score", self.risk_score),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ContractError(f"NONFINITE_{name.upper()}")
        if self.risk_score is not None and not 0.0 <= float(self.risk_score) <= 1.0:
            raise ContractError("RISK_SCORE_OUT_OF_NORMALIZED_RANGE")


@dataclass(frozen=True)
class BaselineRuntimeInput:
    """Label-free input shared by all methods for one method episode."""

    episode_id: str
    raw_instruction: str
    observation_id: str
    candidates: tuple[RuntimeCandidate, ...]
    phase: InteractionPhase = InteractionPhase.INITIAL
    language_uncertainty: float | None = None
    answer_candidate_id: str | None = None
    future_information_candidate_id: str | None = None
    holding_verified: bool = False
    future_information_before_deadline: bool = False
    hard_safety_status: GateStatus = GateStatus.UNKNOWN
    hard_rule_status: GateStatus = GateStatus.UNKNOWN
    original_simlingo_plan_available: bool = True
    authoritative_driveclarify_action: RuntimeAction | None = None
    authoritative_driveclarify_candidate_id: str | None = None
    authority_resolver_applied: bool = False
    runtime_provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.episode_id.strip():
            raise ContractError("EMPTY_EPISODE_ID")
        if not self.raw_instruction.strip():
            raise ContractError("EMPTY_RAW_INSTRUCTION")
        if not self.observation_id.strip():
            raise ContractError("EMPTY_OBSERVATION_ID")
        ids = [item.candidate_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ContractError("DUPLICATE_RUNTIME_CANDIDATE_ID")
        if self.language_uncertainty is not None and (
            isinstance(self.language_uncertainty, bool)
            or not isinstance(self.language_uncertainty, (int, float))
            or not math.isfinite(float(self.language_uncertainty))
            or not 0.0 <= float(self.language_uncertainty) <= 1.0
        ):
            raise ContractError("LANGUAGE_UNCERTAINTY_OUT_OF_RANGE")
        for name in (
            "holding_verified",
            "future_information_before_deadline",
            "original_simlingo_plan_available",
            "authority_resolver_applied",
        ):
            if type(getattr(self, name)) is not bool:
                raise ContractError(f"{name.upper()}_BOOLEAN_REQUIRED")
        if not isinstance(self.runtime_provenance, Mapping):
            raise ContractError("RUNTIME_PROVENANCE_OBJECT_REQUIRED")

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(item.candidate_id for item in self.candidates)


@dataclass(frozen=True)
class MethodDecision:
    """A method output with an exact, phase-specific compute budget.

    The reducer does not execute a model.  It declares how many normal and
    candidate-conditioned model invocations the live method must account for.
    Observed counts and latencies are recorded separately and compared with this
    budget in the interaction metrics.
    """

    method_id: str
    action: RuntimeAction
    selected_candidate_id: str | None
    reason_codes: tuple[str, ...]
    interaction_phase: InteractionPhase
    compute_budget_case: str
    query_requested: bool = False
    holding_requested: bool = False
    stop_requested: bool = False
    existing_pid_only: bool = True
    catalog_annotation_read_count: int = 0

    def __post_init__(self) -> None:
        if self.method_id not in METHOD_ORDER:
            raise ContractError(f"UNKNOWN_METHOD_ID:{self.method_id}")
        if not self.reason_codes:
            raise ContractError("DECISION_REASON_REQUIRED")
        if not isinstance(self.interaction_phase, InteractionPhase):
            raise ContractError("INTERACTION_PHASE_CONTRACT_REQUIRED")
        candidate_forward_budget(self.method_id, self.compute_budget_case)
        if self.catalog_annotation_read_count != 0:
            raise ContractError("CATALOG_ANNOTATION_READ_FORBIDDEN")
        expected_flags = {
            RuntimeAction.ASK: (True, False, False),
            RuntimeAction.WAIT: (False, True, False),
            RuntimeAction.STOP: (False, False, True),
        }.get(self.action, (False, False, False))
        actual_flags = (
            self.query_requested,
            self.holding_requested,
            self.stop_requested,
        )
        if actual_flags != expected_flags:
            raise ContractError("ACTION_SIDE_EFFECT_FLAG_MISMATCH")
        if self.action is RuntimeAction.ACT and self.selected_candidate_id is None:
            if self.method_id != MethodId.ORIGINAL_SIMLINGO.value:
                raise ContractError("ACT_CANDIDATE_REQUIRED")
        elif self.action is not RuntimeAction.ACT and self.selected_candidate_id is not None:
            raise ContractError("NON_ACT_CANDIDATE_FORBIDDEN")

    @property
    def normal_model_forward_budget(self) -> int:
        return NORMAL_SIMLINGO_FORWARD_BUDGET

    @property
    def candidate_model_forward_budget(self) -> int:
        return candidate_forward_budget(self.method_id, self.compute_budget_case)

    @property
    def total_model_forward_budget(self) -> int:
        return self.normal_model_forward_budget + self.candidate_model_forward_budget

    @property
    def scored_decision(self) -> str | None:
        """Return an exact scoring label, never a STOP/FALLBACK substitution."""

        if self.action in {RuntimeAction.ACT, RuntimeAction.ASK, RuntimeAction.WAIT}:
            return self.action.value
        return None


def require_exact_keys(
    value: Mapping[str, Any], expected: Sequence[str] | set[str], context: str
) -> None:
    actual = set(value)
    wanted = set(expected)
    if actual != wanted:
        raise ContractError(
            f"{context}_KEY_MISMATCH:MISSING={sorted(wanted-actual)}:EXTRA={sorted(actual-wanted)}"
        )
