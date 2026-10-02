"""Offline-only ACT/ASK/WAIT recommendation scaffold and diagnostic baselines."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .task_atoms import PairClass
from .types import UsagePurpose


class _StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class Recommendation(_StrEnum):
    ACT_RECOMMENDED = "ACT_RECOMMENDED"
    ASK_RECOMMENDED = "ASK_RECOMMENDED"
    WAIT_RECOMMENDED = "WAIT_RECOMMENDED"
    FALLBACK_RECOMMENDED = "FALLBACK_RECOMMENDED"


class BaselinePolicy(_StrEnum):
    ALWAYS_ACT = "ALWAYS_ACT"
    ALWAYS_ASK = "ALWAYS_ASK"
    ALWAYS_WAIT = "ALWAYS_WAIT"
    AMBIGUITY_ONLY = "AMBIGUITY_ONLY"
    TASK_EQUIVALENCE_AWARE = "TASK_EQUIVALENCE_AWARE"


_BOOL_FIELDS = (
    "ambiguity_present",
    "query_can_change_decision",
    "query_deadline_feasible",
    "option_preserving_control_available",
    "holding_available",
    "active_query",
    "cache_fresh",
    "evidence_eligible",
)


@dataclass(frozen=True)
class DecisionContext:
    pair_class: PairClass
    ambiguity_present: bool | None
    query_can_change_decision: bool | None
    query_deadline_feasible: bool | None
    option_preserving_control_available: bool | None
    holding_available: bool | None
    active_query: bool | None
    cache_fresh: bool | None
    evidence_eligible: bool | None

    @classmethod
    def from_dict(cls, pair_class: str, value: Mapping[str, Any] | None) -> "DecisionContext":
        source = value if isinstance(value, Mapping) else {}
        parsed: dict[str, bool | None] = {}
        for field in _BOOL_FIELDS:
            raw = source.get(field)
            parsed[field] = raw if isinstance(raw, bool) else None
        try:
            pair = PairClass(pair_class)
        except (TypeError, ValueError):
            pair = PairClass.UNKNOWN
        return cls(pair_class=pair, **parsed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair_class": self.pair_class.value,
            **{field: getattr(self, field) for field in _BOOL_FIELDS},
        }


def _envelope(
    recommendation: Recommendation,
    reason_code: str,
    *,
    policy: str,
    unknown_fields: tuple[str, ...] = (),
    baseline_only: bool = False,
    contract_checks_bypassed: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": "driveclarify.offline_decision_recommendation.v1",
        "policy": policy,
        "recommendation": recommendation.value,
        "reason_code": reason_code,
        "unknown_fields": list(unknown_fields),
        "mode": UsagePurpose.DIAGNOSTIC_ONLY.value,
        "baseline_only": baseline_only,
        "contract_checks_bypassed": contract_checks_bypassed,
        "authorization_eligible": False,
        "safety_critical_eligible": False,
        "control_authorized": False,
        "used_for_control": False,
        "act_ask_wait_validated": False,
        "fallback_is_emergency_stop": False,
        "wait_is_emergency_stop": False,
    }


def recommend_offline(context: DecisionContext) -> dict[str, Any]:
    """Apply the explicit fail-closed diagnostic recommendation contract."""

    unknown = tuple(field for field in _BOOL_FIELDS if getattr(context, field) is None)
    if context.pair_class is PairClass.UNKNOWN:
        return _envelope(
            Recommendation.FALLBACK_RECOMMENDED,
            "TASK_PAIR_CLASS_UNKNOWN",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
            unknown_fields=unknown,
        )
    if unknown:
        return _envelope(
            Recommendation.FALLBACK_RECOMMENDED,
            "DECISION_INPUT_UNKNOWN",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
            unknown_fields=unknown,
        )
    if context.evidence_eligible is not True:
        return _envelope(
            Recommendation.FALLBACK_RECOMMENDED,
            "DECISION_EVIDENCE_INELIGIBLE",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
        )
    if context.cache_fresh is not True:
        return _envelope(
            Recommendation.FALLBACK_RECOMMENDED,
            "CANDIDATE_CACHE_STALE",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
        )
    if context.active_query is True:
        return _envelope(
            Recommendation.FALLBACK_RECOMMENDED,
            "ACTIVE_QUERY_CONFLICT",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
        )
    if context.pair_class is PairClass.TASK_EQUIVALENT:
        return _envelope(
            Recommendation.ACT_RECOMMENDED,
            "TASK_EQUIVALENT_EVIDENCE_ELIGIBLE_CACHE_FRESH",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
        )
    if context.ambiguity_present is not True:
        return _envelope(
            Recommendation.FALLBACK_RECOMMENDED,
            "TASK_CRITICAL_WITHOUT_AMBIGUITY_CONFLICT",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
        )
    if context.holding_available is not True:
        return _envelope(
            Recommendation.FALLBACK_RECOMMENDED,
            "HOLDING_UNAVAILABLE",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
        )
    if context.query_can_change_decision is not True:
        return _envelope(
            Recommendation.FALLBACK_RECOMMENDED,
            "QUERY_CANNOT_CHANGE_DECISION",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
        )
    if context.query_deadline_feasible is True:
        return _envelope(
            Recommendation.ASK_RECOMMENDED,
            "TASK_CRITICAL_QUERY_FEASIBLE_HOLDING_AVAILABLE",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
        )
    if context.option_preserving_control_available is True:
        return _envelope(
            Recommendation.WAIT_RECOMMENDED,
            "QUERY_TOO_LATE_OPTION_PRESERVABLE",
            policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
        )
    return _envelope(
        Recommendation.FALLBACK_RECOMMENDED,
        "OPTION_PRESERVING_CONTROL_UNAVAILABLE",
        policy=BaselinePolicy.TASK_EQUIVALENCE_AWARE.value,
    )


def evaluate_baselines(context: DecisionContext) -> dict[str, dict[str, Any]]:
    """Return five diagnostic-only policy comparators.

    ALWAYS_* deliberately bypass feasibility checks so they remain recognizable baselines.  Their
    output is still non-authorizing and explicitly marked ``contract_checks_bypassed``.
    """

    out = {
        BaselinePolicy.ALWAYS_ACT.value: _envelope(
            Recommendation.ACT_RECOMMENDED,
            "BASELINE_ALWAYS_ACT",
            policy=BaselinePolicy.ALWAYS_ACT.value,
            baseline_only=True,
            contract_checks_bypassed=True,
        ),
        BaselinePolicy.ALWAYS_ASK.value: _envelope(
            Recommendation.ASK_RECOMMENDED,
            "BASELINE_ALWAYS_ASK",
            policy=BaselinePolicy.ALWAYS_ASK.value,
            baseline_only=True,
            contract_checks_bypassed=True,
        ),
        BaselinePolicy.ALWAYS_WAIT.value: _envelope(
            Recommendation.WAIT_RECOMMENDED,
            "BASELINE_ALWAYS_WAIT",
            policy=BaselinePolicy.ALWAYS_WAIT.value,
            baseline_only=True,
            contract_checks_bypassed=True,
        ),
    }
    # This scaffold reduces exactly two candidates.  The frozen ambiguity-only comparator asks on
    # candidate multiplicity alone; it deliberately ignores Task equivalence and feasibility.
    ambiguity = _envelope(
        Recommendation.ASK_RECOMMENDED,
        "BASELINE_MULTIPLE_CANDIDATES_ASK",
        policy=BaselinePolicy.AMBIGUITY_ONLY.value,
        baseline_only=True,
        contract_checks_bypassed=True,
    )
    out[BaselinePolicy.AMBIGUITY_ONLY.value] = ambiguity
    aware = recommend_offline(context)
    aware["baseline_only"] = True
    out[BaselinePolicy.TASK_EQUIVALENCE_AWARE.value] = aware
    return out
