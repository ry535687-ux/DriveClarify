"""Candidate consequence-equivalence protocol.

The protocol deliberately separates semantic identity, grounded target identity,
current executable plans, and future consequences.  Numeric route similarity is
descriptive evidence only: it never proves future consequence equivalence by
itself.  Missing evidence remains UNKNOWN.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


class TriState(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class CandidateRelationship(str, Enum):
    CONSEQUENCE_EQUIVALENT = "CONSEQUENCE_EQUIVALENT"
    MATERIAL_DIVERGENCE_NOW = "MATERIAL_DIVERGENCE_NOW"
    CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT = (
        "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT"
    )
    UNKNOWN_OR_INSUFFICIENT_EVIDENCE = "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class ClassificationEvidence:
    raw_k: int
    effective_k: int
    semantic_distinct: TriState
    semantic_duplicate: TriState
    target_duplicate: TriState
    current_executable_maneuver_equivalent: TriState
    immediate_material_divergence: TriState
    future_target_or_topology_divergent: TriState
    future_maneuver_divergent: TriState
    answer_changes_action: TriState
    relevant_decision_window_covered: TriState

    def validate(self) -> None:
        if self.raw_k < 0 or self.effective_k < 0:
            raise ValueError("CANDIDATE_K_MUST_BE_NONNEGATIVE")
        if self.effective_k > self.raw_k:
            raise ValueError("EFFECTIVE_K_CANNOT_EXCEED_RAW_K")
        if self.semantic_duplicate is TriState.TRUE and self.effective_k > 1:
            raise ValueError("SEMANTIC_DUPLICATE_MUST_NOT_MASQUERADE_AS_EFFECTIVE_K2")


@dataclass(frozen=True)
class ClassificationResult:
    relationship: CandidateRelationship
    semantic_distinct: TriState
    consequence_equivalent: TriState
    candidate_collapse_allowed: bool
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "relationship": self.relationship.value,
            "semantic_distinct": self.semantic_distinct.value,
            "consequence_equivalent": self.consequence_equivalent.value,
            "candidate_collapse_allowed": self.candidate_collapse_allowed,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class DecisionEvidence:
    shared_current_action_safe: TriState
    hard_rule_pass: TriState
    answer_changes_action: TriState
    query_budget_available: TriState
    no_active_query: TriState
    timing_allows_answer: TriState
    decision_deadline_near: TriState
    recoverability: TriState
    future_information_expected: TriState = TriState.FALSE
    persistent_ambiguity_runtime_supported: bool = False


@dataclass(frozen=True)
class DecisionResult:
    decision: str
    reason_codes: Tuple[str, ...]
    forced: bool = False
    unresolved_ambiguity_must_persist: bool = False
    runtime_lifecycle_status: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "forced": self.forced,
            "unresolved_ambiguity_must_persist": self.unresolved_ambiguity_must_persist,
            "runtime_lifecycle_status": self.runtime_lifecycle_status,
        }


def _future_divergent(evidence: ClassificationEvidence) -> TriState:
    values = (
        evidence.future_target_or_topology_divergent,
        evidence.future_maneuver_divergent,
    )
    if TriState.TRUE in values:
        return TriState.TRUE
    if all(value is TriState.FALSE for value in values):
        return TriState.FALSE
    return TriState.UNKNOWN


def classify_candidate_relationship(
    evidence: ClassificationEvidence,
) -> ClassificationResult:
    """Classify without turning missing evidence into false, safe, or zero."""

    evidence.validate()
    if evidence.effective_k < 2 or evidence.semantic_duplicate is TriState.TRUE:
        return ClassificationResult(
            CandidateRelationship.UNKNOWN_OR_INSUFFICIENT_EVIDENCE,
            TriState.FALSE if evidence.semantic_duplicate is TriState.TRUE else evidence.semantic_distinct,
            TriState.UNKNOWN,
            True,
            ("EFFECTIVE_SEMANTIC_K_NOT_TWO",),
        )
    if evidence.semantic_distinct is not TriState.TRUE:
        return ClassificationResult(
            CandidateRelationship.UNKNOWN_OR_INSUFFICIENT_EVIDENCE,
            evidence.semantic_distinct,
            TriState.UNKNOWN,
            False,
            ("SEMANTIC_DISTINCTNESS_NOT_PROVEN",),
        )
    if evidence.immediate_material_divergence is TriState.TRUE:
        return ClassificationResult(
            CandidateRelationship.MATERIAL_DIVERGENCE_NOW,
            TriState.TRUE,
            TriState.FALSE,
            False,
            ("IMMEDIATE_EXECUTABLE_CONSEQUENCES_MATERIALLY_DIVERGE",),
        )

    future = _future_divergent(evidence)
    if evidence.current_executable_maneuver_equivalent is TriState.TRUE:
        if future is TriState.TRUE:
            return ClassificationResult(
                CandidateRelationship.CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT,
                TriState.TRUE,
                TriState.FALSE,
                False,
                (
                    "CURRENT_EXECUTABLE_MANEUVER_SHARED",
                    "FUTURE_TARGET_TOPOLOGY_OR_MANEUVER_DIVERGES",
                    "SHORT_HORIZON_SIMILARITY_DOES_NOT_COLLAPSE_CANDIDATES",
                ),
            )
        if (
            future is TriState.FALSE
            and evidence.answer_changes_action is TriState.FALSE
            and evidence.relevant_decision_window_covered is TriState.TRUE
        ):
            return ClassificationResult(
                CandidateRelationship.CONSEQUENCE_EQUIVALENT,
                TriState.TRUE,
                TriState.TRUE,
                False,
                (
                    "DISTINCT_SEMANTICS_RETAINED",
                    "EXECUTABLE_CONSEQUENCES_EQUIVALENT_IN_RELEVANT_WINDOW",
                    "ANSWER_DOES_NOT_CHANGE_ACTION",
                ),
            )

    unknown_reasons = []
    fields = {
        "CURRENT_MANEUVER_EQUIVALENCE_UNKNOWN": evidence.current_executable_maneuver_equivalent,
        "IMMEDIATE_DIVERGENCE_UNKNOWN": evidence.immediate_material_divergence,
        "FUTURE_DIVERGENCE_UNKNOWN": future,
        "ANSWER_UTILITY_UNKNOWN": evidence.answer_changes_action,
        "DECISION_WINDOW_COVERAGE_UNKNOWN": evidence.relevant_decision_window_covered,
    }
    unknown_reasons.extend(name for name, value in fields.items() if value is TriState.UNKNOWN)
    if not unknown_reasons:
        unknown_reasons.append("EVIDENCE_COMBINATION_DOES_NOT_ESTABLISH_A_RELATION")
    return ClassificationResult(
        CandidateRelationship.UNKNOWN_OR_INSUFFICIENT_EVIDENCE,
        TriState.TRUE,
        TriState.UNKNOWN,
        False,
        tuple(unknown_reasons),
    )


def _all_true(*values: TriState) -> bool:
    return all(value is TriState.TRUE for value in values)


def evaluate_decision_contract(
    relationship: CandidateRelationship,
    evidence: DecisionEvidence,
) -> DecisionResult:
    """Evaluate ACT/ASK/WAIT/UNKNOWN from consequence and ordinary gates.

    This is a frozen logical diagnostic contract, not a new control path.  It
    never selects ASK from K alone or from route inequality alone.
    """

    if relationship is CandidateRelationship.UNKNOWN_OR_INSUFFICIENT_EVIDENCE:
        return DecisionResult(
            "UNKNOWN",
            ("CANDIDATE_RELATIONSHIP_UNKNOWN_FAIL_CLOSED",),
        )
    if evidence.hard_rule_pass is TriState.FALSE:
        return DecisionResult("FALLBACK", ("HARD_RULE_GATE_FAILED",))
    if evidence.shared_current_action_safe is TriState.FALSE:
        return DecisionResult("FALLBACK", ("SHARED_CURRENT_ACTION_UNSAFE",))

    if relationship is CandidateRelationship.CONSEQUENCE_EQUIVALENT:
        if _all_true(evidence.shared_current_action_safe, evidence.hard_rule_pass):
            return DecisionResult(
                "ACT",
                (
                    "CONSEQUENCES_EQUIVALENT",
                    "ANSWER_CANNOT_CHANGE_EXECUTABLE_ACTION",
                    "UNNECESSARY_QUERY_AVOIDED",
                ),
            )
        return DecisionResult("UNKNOWN", ("ACT_GATES_NOT_AFFIRMATIVELY_PROVEN",))

    ask_gates = _all_true(
        evidence.answer_changes_action,
        evidence.query_budget_available,
        evidence.no_active_query,
        evidence.timing_allows_answer,
        evidence.decision_deadline_near,
        evidence.hard_rule_pass,
    )
    if relationship is CandidateRelationship.MATERIAL_DIVERGENCE_NOW:
        if ask_gates:
            return DecisionResult(
                "ASK",
                (
                    "MATERIAL_DIVERGENCE_NOW",
                    "ANSWER_CHANGES_ACTION",
                    "QUERY_SAFETY_TIMING_AND_BUDGET_GATES_PASS",
                ),
            )
        return DecisionResult(
            "UNKNOWN",
            ("MATERIAL_DIVERGENCE_PRESENT_BUT_ASK_GATES_NOT_ALL_PROVEN",),
        )

    # Currently shared action, distinct future obligation.  ACT is valid only
    # if that obligation remains represented for later reassessment.
    if evidence.decision_deadline_near is TriState.TRUE and ask_gates:
        return DecisionResult(
            "ASK",
            (
                "FUTURE_DIVERGENCE_ENTERED_DECISION_WINDOW",
                "ANSWER_CHANGES_ACTION",
                "QUERY_GATES_PASS",
            ),
            unresolved_ambiguity_must_persist=True,
        )
    if _all_true(
        evidence.shared_current_action_safe,
        evidence.hard_rule_pass,
        evidence.recoverability,
    ) and evidence.decision_deadline_near is TriState.FALSE:
        supported = evidence.persistent_ambiguity_runtime_supported
        return DecisionResult(
            "ACT",
            (
                "CURRENT_SHARED_ACTION_SAFE_AND_RECOVERABLE",
                "DECISION_POINT_NOT_NEAR",
                "FUTURE_AMBIGUITY_REQUIRES_REASSESSMENT",
            ),
            unresolved_ambiguity_must_persist=True,
            runtime_lifecycle_status=(
                "SUPPORTED"
                if supported
                else "SUPPORTED_CONCEPTUALLY_BUT_RUNTIME_LIFECYCLE_NOT_YET_IMPLEMENTED"
            ),
        )
    if (
        evidence.future_information_expected is TriState.TRUE
        and evidence.shared_current_action_safe is TriState.TRUE
    ):
        return DecisionResult(
            "WAIT",
            ("VALUABLE_FUTURE_INFORMATION_EXPECTED",),
            unresolved_ambiguity_must_persist=True,
        )
    return DecisionResult(
        "UNKNOWN",
        ("FUTURE_DIVERGENCE_TIMING_OR_RECOVERABILITY_UNKNOWN",),
        unresolved_ambiguity_must_persist=True,
        runtime_lifecycle_status=(
            "SUPPORTED_CONCEPTUALLY_BUT_RUNTIME_LIFECYCLE_NOT_YET_IMPLEMENTED"
            if not evidence.persistent_ambiguity_runtime_supported
            else "SUPPORTED"
        ),
    )


def enum_value(value: Any) -> Any:
    """JSON helper retained here so artifact writers share one convention."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): enum_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [enum_value(item) for item in value]
    return value
