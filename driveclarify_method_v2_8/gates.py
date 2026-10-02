"""Candidate-selected activation gate for DriveClarify V2.8."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from driveclarify_method_v2_1 import EligibilityResult


@dataclass(frozen=True)
class SelectedMotionEligibilityEvidenceV28:
    physical_safety_pass: Optional[bool]
    candidate_selected_route_legal: Optional[bool]
    selected_navigation_valid: Optional[bool]
    positive_pre_activation_feasibility: Optional[bool]
    motion_valid: Optional[bool]


@dataclass(frozen=True)
class PlanningAmbiguityAdmissionEvidenceV28:
    semantic_k: int
    planning_effective_k: int
    distinct_current_plan_realizations: Optional[bool]


def assess_selected_motion_eligibility_v28(
    evidence: SelectedMotionEligibilityEvidenceV28,
) -> EligibilityResult:
    """Authorize only the resolved selected route, independent of baseline rule state."""

    reasons: list[str] = []
    complete = True
    for reason, value in (
        ("PHYSICAL_SAFETY_NOT_PASSED", evidence.physical_safety_pass),
        ("CANDIDATE_SELECTED_ROUTE_NOT_LEGAL", evidence.candidate_selected_route_legal),
        ("SELECTED_NAVIGATION_NOT_VALID", evidence.selected_navigation_valid),
        (
            "PRE_ACTIVATION_SELECTED_MANEUVER_NOT_FEASIBLE",
            evidence.positive_pre_activation_feasibility,
        ),
        ("MOTION_NOT_VALID", evidence.motion_valid),
    ):
        if value is not True:
            reasons.append(reason if value is False else "UNKNOWN_" + reason)
            if value is None:
                complete = False
    return EligibilityResult(
        eligible=not reasons,
        evidence_complete=complete,
        reason_codes=(
            ("V2_8_CANDIDATE_SELECTED_MOTION_ELIGIBLE",)
            if not reasons
            else tuple(reasons)
        ),
    )


def assess_planning_ambiguity_admission_v28(
    evidence: PlanningAmbiguityAdmissionEvidenceV28,
) -> EligibilityResult:
    """Admit positive current ambiguity only with two usable realizations."""

    reasons: list[str] = []
    if type(evidence.semantic_k) is not int or evidence.semantic_k < 2:
        reasons.append("SEMANTIC_K_BELOW_TWO")
    if (
        type(evidence.planning_effective_k) is not int
        or evidence.planning_effective_k < 2
    ):
        reasons.append("PLANNING_EFFECTIVE_K_BELOW_TWO")
    if evidence.distinct_current_plan_realizations is not True:
        reasons.append(
            "CURRENT_PLAN_REALIZATIONS_NOT_DISTINCT"
            if evidence.distinct_current_plan_realizations is False
            else "UNKNOWN_CURRENT_PLAN_REALIZATIONS_NOT_DISTINCT"
        )
    return EligibilityResult(
        eligible=not reasons,
        evidence_complete=(
            evidence.distinct_current_plan_realizations is not None
            and type(evidence.semantic_k) is int
            and type(evidence.planning_effective_k) is int
        ),
        reason_codes=(
            ("V2_8_CURRENT_PLANNING_AMBIGUITY_ADMITTED",)
            if not reasons
            else tuple(reasons)
        ),
    )
