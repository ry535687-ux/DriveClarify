"""Feature Dependency Gate (CP2 skeleton, D03 dual-tier / CAP-1 fix).

The single chokepoint that makes unverified physical consequences impossible to emit. It is now
PURPOSE-AWARE: a caller MUST state WHY it wants the value (a UsagePurpose). The gate then enforces
BOTH:
  (a) the capability's own allowlist of purposes (`allowed_usage_purposes`), and
  (b) the per-purpose evidence threshold:
        AUTHORIZATION / SAFETY_CRITICAL  -> requires all deps VERIFIED_FROM_CONTROLLED_PROBE
        DIAGNOSTIC_ONLY / LOGGING_ONLY   -> allows VERIFIED or SUPPORTED_BUT_INCOMPLETE
      UNRESOLVED / INVALIDATED / NOT_AVAILABLE never produce a value for ANY purpose.
There is NO default purpose: a missing/None purpose is rejected. The gate never computes a value.
"""

from __future__ import annotations

from .types import (
    AUTHORIZING_PURPOSES,
    EvidenceGrade,
    Result,
    TriValue,
    UsagePurpose,
    unknown,
)

# Grades that permit a value for an AUTHORIZING purpose (fully verified evidence only).
_VERIFIED_ONLY = {EvidenceGrade.VERIFIED}
# Grades that permit a value for a NON-authorizing (diagnostic/logging) purpose.
_DIAGNOSTIC_OK = {EvidenceGrade.VERIFIED, EvidenceGrade.SUPPORTED}

# CP1 frozen grades (evidence_grade, corrected per errata E4/E5).
CP1_GRADES: dict[str, EvidenceGrade] = {
    "F1": EvidenceGrade.SUPPORTED,
    "F2": EvidenceGrade.UNRESOLVED,
    "F3": EvidenceGrade.SUPPORTED,
    "F4": EvidenceGrade.UNRESOLVED,
    "F5": EvidenceGrade.SUPPORTED,
    "F6": EvidenceGrade.UNRESOLVED,
    "T1": EvidenceGrade.VERIFIED,
    "T2": EvidenceGrade.VERIFIED,
    "T3": EvidenceGrade.VERIFIED,
    "T4": EvidenceGrade.SUPPORTED,
    "D1": EvidenceGrade.VERIFIED,
}

_GRADE_ORDER = {
    EvidenceGrade.INVALIDATED: 0,
    EvidenceGrade.NOT_AVAILABLE: 1,
    EvidenceGrade.UNRESOLVED: 2,
    EvidenceGrade.SUPPORTED: 3,
    EvidenceGrade.VERIFIED: 4,
}

# Capability -> the purposes it is EVER allowed to serve. Kept in lockstep with
# reports/consequence_adapter_v0/CAPABILITY_GATE_V0.json `allowed_usage_purposes`.
#   - Physical / metric capabilities: AUTHORIZATION+SAFETY_CRITICAL only (double lock: not even
#     diagnostic, so an unstable metre/TTC value cannot leak as a "diagnostic"), and they need
#     VERIFIED deps they do not have -> always blocked in v0.
#   - Raw-geometry / integrity / clock / latency: DIAGNOSTIC_ONLY (+LOGGING_ONLY) only -> never
#     authorizing, even when their deps are VERIFIED.
_D = (UsagePurpose.DIAGNOSTIC_ONLY, UsagePurpose.LOGGING_ONLY)
_AS = (UsagePurpose.AUTHORIZATION, UsagePurpose.SAFETY_CRITICAL)
CAPABILITY_ALLOWED_PURPOSES: dict[str, tuple[UsagePurpose, ...]] = {
    # diagnostics / integrity / raw geometry / timing diagnostics (non-authorizing)
    "schema_valid": _D, "source_linkage_valid": _D, "shape_valid": _D,
    "candidate_freshness": _D, "route_point_count": _D, "speed_waypoint_count": _D,
    "raw_route_length": _D, "raw_adjacent_spacing": _D,
    "pairwise_route_divergence_raw": _D, "terminal_divergence_raw": _D, "plan_continuity": _D,
    "plan_age_sim": _D, "adapter_latency_monotonic": _D,
    # task / safety / rule / recoverability / comfort / metric timing (authorizing tiers only)
    "candidate_target_identity": _AS, "target_topology": _AS, "route_branch_identity": _AS,
    "goal_equivalence": _AS, "wrong_goal_risk": _AS,
    "collision_status": _AS, "min_clearance": _AS, "ttc": _AS, "TTC": _AS,
    "required_deceleration": _AS, "near_miss": _AS, "actor_conflict": _AS,
    "red_light_conflict": _AS, "stop_sign_conflict": _AS, "lane_boundary_conflict": _AS,
    "off_road": _AS, "wrong_way": _AS, "camera_projection": _AS,
    "time_to_decision": _AS, "answer_deadline": _AS, "remaining_action_window": _AS,
    "recoverable_after_wait": _AS, "missed_opportunity": _AS, "alternative_available": _AS,
    "acceleration_cost": _AS, "jerk_cost": _AS, "steering_smoothness": _AS,
    # hard state-machine gate fields (authorizing)
    "hard_safety_violation": _AS, "hard_rule_violation": _AS,
}


def weakest_grade(dependencies: tuple[str, ...], grades: dict[str, EvidenceGrade] | None = None) -> EvidenceGrade:
    """Return the weakest (lowest) evidence grade across the dependencies. Empty deps => VERIFIED."""
    g = grades or CP1_GRADES
    if not dependencies:
        return EvidenceGrade.VERIFIED
    return min((g.get(dep, EvidenceGrade.NOT_AVAILABLE) for dep in dependencies),
               key=lambda grade: _GRADE_ORDER[grade])


def _threshold_for(purpose: UsagePurpose) -> set[EvidenceGrade]:
    return _VERIFIED_ONLY if purpose in AUTHORIZING_PURPOSES else _DIAGNOSTIC_OK


def gate(feature: str, dependencies: tuple[str, ...], requested_purpose: UsagePurpose,
         grades: dict[str, EvidenceGrade] | None = None,
         allowed_purposes: tuple[UsagePurpose, ...] | None = None,
         ) -> tuple[bool, EvidenceGrade, str | None]:
    """Return (may_compute, weakest_grade, blocking_reason_code). PURPOSE IS REQUIRED.

    Order: (1) purpose is a real UsagePurpose; (2) purpose ∈ capability allowlist;
    (3) evidence meets the per-purpose threshold. Any failure => may_compute False.
    """
    if requested_purpose is None:
        raise ValueError("GATE_REQUIRES_EXPLICIT_PURPOSE")
    if not isinstance(requested_purpose, UsagePurpose):
        raise ValueError("GATE_PURPOSE_MUST_BE_USAGEPURPOSE")

    allow = allowed_purposes if allowed_purposes is not None else CAPABILITY_ALLOWED_PURPOSES.get(feature)
    if allow is None:
        # Unknown capability with no explicit allowlist: fail closed.
        return False, weakest_grade(dependencies, grades), "BLOCKED_UNKNOWN_CAPABILITY"
    if requested_purpose not in allow:
        return False, weakest_grade(dependencies, grades), f"BLOCKED_PURPOSE_NOT_ALLOWED_{requested_purpose.value}"

    wg = weakest_grade(dependencies, grades)
    if wg in _threshold_for(requested_purpose):
        return True, wg, None
    g = grades or CP1_GRADES
    threshold = _threshold_for(requested_purpose)
    blocking = sorted(dep for dep in dependencies
                      if g.get(dep, EvidenceGrade.NOT_AVAILABLE) not in threshold)
    reason = "BLOCKED_BY_" + "_".join(blocking) if blocking else "BLOCKED_UNVERIFIED"
    return False, wg, reason


def gated_unknown(feature: str, dependencies: tuple[str, ...], requested_purpose: UsagePurpose,
                  unit: str | None = None, frame: str | None = None,
                  grades: dict[str, EvidenceGrade] | None = None,
                  allowed_purposes: tuple[UsagePurpose, ...] | None = None) -> Result | None:
    """If the gate blocks the feature for the requested purpose, return the fail-closed UNKNOWN
    Result; else None ("gate open" — caller may compute a real value). PURPOSE IS REQUIRED."""
    may, wg, reason = gate(feature, dependencies, requested_purpose, grades, allowed_purposes)
    if may:
        return None
    return unknown(reason_code=reason or "BLOCKED_UNVERIFIED", dependencies=dependencies,
                   evidence_grade=wg, unit=unit, frame=frame, usage_purpose=requested_purpose)


def tri_from_gate(feature: str, dependencies: tuple[str, ...],
                  requested_purpose: UsagePurpose = UsagePurpose.SAFETY_CRITICAL,
                  grades: dict[str, EvidenceGrade] | None = None) -> TriValue:
    """Map a gated feature to a TriValue for the state-machine mapper. Blocked => UNKNOWN.

    Hard state-machine fields are SAFETY_CRITICAL by nature, so the default reflects that. Even when
    the gate is open, this skeleton returns UNKNOWN because no real extractor exists in the design
    freeze; real TRUE/FALSE requires a future verified extractor.
    """
    gate(feature, dependencies, requested_purpose, grades)  # raises if purpose invalid
    return TriValue.UNKNOWN
