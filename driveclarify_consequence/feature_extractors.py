"""Feature extractors — produce a Result envelope per consequence field.

Split by category:
  - Integrity + Raw Geometry + plan_age_sim + adapter_latency_monotonic: DIAGNOSTIC/LOGGING purpose;
    computable now => may be AVAILABLE (values are RAW / diagnostic, never authorizing).
  - Task / Safety / Rule / Recoverability / Comfort + metric timing: AUTHORIZATION/SAFETY_CRITICAL
    purpose; deps include unresolved F2/F4/F5/F6 => the dependency gate blocks them => UNKNOWN.

Every physical UNKNOWN comes from the shared dependency gate (not per-extractor free text). Values
only ever come from an AVAILABLE Result built by driveclarify_consequence.types.available(). Pure;
no CARLA/SimLingo/GPU.
"""

from __future__ import annotations

from typing import Any

from . import raw_geometry as rg
from .dependency_gate import gate
from .feature_registry import get_spec
from .frame_binding import RAW_FRAME, RAW_INDEX, RAW_UNIT
from .types import (
    EvidenceGrade,
    Result,
    ResultStatus,
    UsagePurpose,
    available,
    unknown,
)

# Grade string (from contract_snapshot / matrix) -> EvidenceGrade enum.
_GRADE_MAP = {g.value: g for g in EvidenceGrade}

# Diagnostic/logging capabilities use DIAGNOSTIC_ONLY; physical capabilities use SAFETY_CRITICAL.
_DIAGNOSTIC = UsagePurpose.DIAGNOSTIC_ONLY


def grades_from_snapshot(contract_snapshot: dict[str, str]) -> dict[str, EvidenceGrade]:
    """Build a {contract_id -> EvidenceGrade} map from an input record's contract_snapshot."""
    out: dict[str, EvidenceGrade] = {}
    for key, val in contract_snapshot.items():
        cid = key[:-6] if key.endswith("_grade") else key
        out[cid] = _GRADE_MAP.get(val, EvidenceGrade.NOT_AVAILABLE)
    return out


def _diagnostic_available(value: Any, unit: str | None, frame: str | None,
                          feature: str, grades: dict[str, EvidenceGrade],
                          source_artifacts: tuple[str, ...] = (),
                          clock_domain: str | None = None,
                          limitations: str | None = None) -> Result:
    """Build an AVAILABLE diagnostic Result, but only if the gate permits the feature for a
    DIAGNOSTIC_ONLY purpose at the current evidence. Otherwise return the gated UNKNOWN."""
    spec = get_spec(feature)
    deps = spec.dependencies
    may, wg, reason = gate(feature, deps, _DIAGNOSTIC, grades, spec.allowed_usage_purposes)
    if not may:
        return unknown(reason_code=reason or "BLOCKED_UNVERIFIED", dependencies=deps,
                       evidence_grade=wg, unit=unit, frame=frame, usage_purpose=_DIAGNOSTIC)
    return available(value=value, unit=unit, frame=frame, evidence_grade=wg,
                     dependencies=deps, source_artifacts=source_artifacts,
                     usage_purpose=_DIAGNOSTIC, clock_domain=clock_domain, limitations=limitations)


def _physical_unknown(feature: str, grades: dict[str, EvidenceGrade], unit: str | None) -> Result:
    """Physical/authorizing features: run the gate for SAFETY_CRITICAL; in v0 the unresolved deps
    always block => structured UNKNOWN with the blocking-contract reason. Never returns a value."""
    spec = get_spec(feature)
    deps = spec.dependencies
    purpose = UsagePurpose.SAFETY_CRITICAL
    may, wg, reason = gate(feature, deps, purpose, grades, spec.allowed_usage_purposes)
    if may:
        # Even if the gate opened (future verified evidence), no verified extractor exists in CP3A v0
        # for physical consequences; fail closed to UNKNOWN with an explicit design-only reason.
        return unknown(reason_code="BLOCKED_PHYSICAL_CONSEQUENCE_DESIGN_ONLY", dependencies=deps,
                       evidence_grade=wg, unit=unit, usage_purpose=purpose)
    return unknown(reason_code=reason or "BLOCKED_UNVERIFIED", dependencies=deps,
                   evidence_grade=wg, unit=unit, usage_purpose=purpose)


# ---------------------------------------------------------------------------
# Integrity extractors
# ---------------------------------------------------------------------------

def integrity_results(candidate: dict[str, Any], record: dict[str, Any],
                      grades: dict[str, EvidenceGrade], is_stale: bool, stale_reason: str | None,
                      linkage_ok: bool, shape_ok: bool, shape_reason: str | None,
                      source_artifacts: tuple[str, ...]) -> dict[str, Result]:
    """Build the five integrity Results. These are diagnostics (DIAGNOSTIC_ONLY)."""
    obs = record.get("metadata", {}).get("observation_id")
    cand_obs = candidate.get("source_observation_id")

    # candidate_valid: composite of validity flag + linkage + shape.
    validity = candidate.get("validity")
    candidate_valid_bool = (validity == "VALID") and linkage_ok and shape_ok and not is_stale
    candidate_valid = _diagnostic_available(
        candidate_valid_bool, "boolean", None, "shape_valid", grades, source_artifacts)

    if linkage_ok and cand_obs == obs:
        source_linkage_valid = _diagnostic_available(True, "boolean", None, "source_linkage_valid",
                                                      grades, source_artifacts)
    else:
        source_linkage_valid = _invalid(ResultStatus.INVALID, "LINKAGE_OBSERVATION_MISMATCH",
                                        ("source_linkage_valid",))

    if shape_ok:
        shape_valid = _diagnostic_available(True, "boolean", None, "shape_valid", grades,
                                            source_artifacts)
    else:
        shape_valid = _invalid(ResultStatus.INVALID, shape_reason or "SHAPE_INVALID",
                               ("shape_valid",))

    if is_stale:
        stale = _invalid(ResultStatus.STALE, "CANDIDATE_STALE", ("candidate_freshness",))
    else:
        stale = _diagnostic_available(False, "boolean", None, "candidate_freshness", grades,
                                      source_artifacts)

    # missing_dependencies: the blocking contracts for the physical categories (always present in v0).
    blocking = _blocking_contracts(grades)
    missing_dependencies = _diagnostic_available(blocking, "identifier", None,
                                                 "source_linkage_valid", grades, source_artifacts)
    return {
        "candidate_valid": candidate_valid,
        "source_linkage_valid": source_linkage_valid,
        "shape_valid": shape_valid,
        "stale": stale,
        "missing_dependencies": missing_dependencies,
    }


def _invalid(status: ResultStatus, reason: str, deps: tuple[str, ...]) -> Result:
    r = Result(status=status, value=None, reason_code=reason, dependencies=deps,
               evidence_grade=EvidenceGrade.NOT_AVAILABLE, usage_purpose=_DIAGNOSTIC)
    r.validate()
    return r


def _blocking_contracts(grades: dict[str, EvidenceGrade]) -> list[str]:
    """Contracts whose grade is not VERIFIED (block authorizing physical features). Sorted, unique."""
    blocked = sorted({cid for cid, g in grades.items() if g is not EvidenceGrade.VERIFIED})
    return blocked


# ---------------------------------------------------------------------------
# Raw geometry extractors
# ---------------------------------------------------------------------------

def raw_geometry_results(candidate: dict[str, Any], grades: dict[str, EvidenceGrade],
                         pairwise: dict[str, Any] | None, terminal: float | None,
                         source_artifacts: tuple[str, ...]) -> dict[str, Result]:
    """Build the seven raw-geometry Results for one candidate. RAW units only."""
    route = candidate.get("pred_route_raw")
    speed = candidate.get("pred_speed_wps_raw")

    _RAW_NOTE = "RAW diagnostic; NOT metres; NOT a physical consequence."

    def _num(feature: str, value, reason, unit, frame, limitations=_RAW_NOTE):
        if reason:
            return unknown(reason_code=reason, dependencies=get_spec(feature).dependencies,
                           evidence_grade=EvidenceGrade.SUPPORTED, unit=unit, frame=frame,
                           usage_purpose=_DIAGNOSTIC)
        return _diagnostic_available(value, unit, frame, feature, grades, source_artifacts,
                                     limitations=limitations)

    rpc_v, rpc_r = rg.route_point_count(route)
    swc_v, swc_r = rg.route_point_count(speed)
    rrl_v, rrl_r = rg.raw_route_length(route)
    ras_v, ras_r = rg.raw_adjacent_spacing(route)   # dict {min,max,mean,count} or None
    cont_v, cont_r = rg.plan_continuity(route)       # dict {max_adjacent_gap,n_points,continuous}

    # The frozen schema restricts `value` to number/int/bool/string/array/null (no object). Composite
    # raw stats are therefore reduced to a scalar/array value with the detail carried in limitations.
    ras_value = ras_v["mean"] if ras_v else None
    ras_lim = (f"{_RAW_NOTE} adjacent-spacing stats min={ras_v['min']} max={ras_v['max']} "
               f"mean={ras_v['mean']} count={ras_v['count']}") if ras_v else _RAW_NOTE
    cont_value = cont_v["max_adjacent_gap"] if cont_v else None
    cont_lim = (f"{_RAW_NOTE} max_adjacent_gap over n_points={cont_v['n_points']}; "
                f"continuous={cont_v['continuous']} (no frozen threshold)") if cont_v else _RAW_NOTE

    results = {
        "route_point_count": _num("route_point_count", rpc_v, rpc_r, RAW_INDEX, RAW_FRAME),
        "speed_waypoint_count": _num("speed_waypoint_count", swc_v, swc_r, RAW_INDEX, RAW_FRAME),
        "raw_route_length": _num("raw_route_length", rrl_v, rrl_r, RAW_UNIT, RAW_FRAME),
        "raw_adjacent_spacing": _num("raw_adjacent_spacing", ras_value, ras_r, RAW_UNIT, RAW_FRAME, ras_lim),
        "plan_continuity": _num("plan_continuity", cont_value, cont_r, RAW_UNIT, RAW_FRAME, cont_lim),
    }
    # pairwise / terminal are set-level (need a second candidate); None => single-candidate set.
    if pairwise is None:
        results["pairwise_route_divergence_raw"] = unknown(
            reason_code="GEOMETRY_SINGLE_CANDIDATE",
            dependencies=get_spec("pairwise_route_divergence_raw").dependencies,
            evidence_grade=EvidenceGrade.SUPPORTED, unit=RAW_UNIT, frame=RAW_FRAME,
            usage_purpose=_DIAGNOSTIC)
    else:
        pw_lim = (f"{_RAW_NOTE} pairwise raw L2 vs stable ref: mean={pairwise['mean']} "
                  f"max={pairwise['max']} max_index={pairwise['max_index']} "
                  f"compared_points={pairwise['compared_points']} algo={pairwise['algorithm_version']}")
        results["pairwise_route_divergence_raw"] = _num(
            "pairwise_route_divergence_raw", pairwise["max"], None, RAW_UNIT, RAW_FRAME, pw_lim)
    if terminal is None:
        results["terminal_divergence_raw"] = unknown(
            reason_code="GEOMETRY_SINGLE_CANDIDATE",
            dependencies=get_spec("terminal_divergence_raw").dependencies,
            evidence_grade=EvidenceGrade.SUPPORTED, unit=RAW_UNIT, frame=RAW_FRAME,
            usage_purpose=_DIAGNOSTIC)
    else:
        results["terminal_divergence_raw"] = _num(
            "terminal_divergence_raw", terminal, None, RAW_UNIT, RAW_FRAME)
    return results


# ---------------------------------------------------------------------------
# Physical categories (all UNKNOWN in v0 via the dependency gate)
# ---------------------------------------------------------------------------

_TASK_FIELDS = ("candidate_target_identity", "target_topology", "route_branch_identity",
                "goal_equivalence", "wrong_goal_risk")
_SAFETY_FIELDS = ("collision_status", "min_clearance", "ttc", "required_deceleration",
                  "near_miss", "actor_conflict")
_SAFETY_FEATURE = {"ttc": "TTC"}  # matrix capability id differs from the schema field name
_RULE_FIELDS = ("red_light_conflict", "stop_sign_conflict", "lane_boundary_conflict",
                "off_road", "wrong_way", "camera_projection")
_RECOVERABILITY_FIELDS = ("recoverable_after_wait", "missed_opportunity", "alternative_available")
_COMFORT_FIELDS = ("acceleration_cost", "jerk_cost", "steering_smoothness")


def _physical_group(fields: tuple[str, ...], grades: dict[str, EvidenceGrade]) -> dict[str, Result]:
    out: dict[str, Result] = {}
    for field in fields:
        feature = _SAFETY_FEATURE.get(field, field)
        spec = get_spec(feature)
        out[field] = _physical_unknown(feature, grades, spec.unit)
    return out


def task_results(grades):        return _physical_group(_TASK_FIELDS, grades)
def safety_results(grades):      return _physical_group(_SAFETY_FIELDS, grades)
def rule_results(grades):        return _physical_group(_RULE_FIELDS, grades)
def recoverability_results(grades): return _physical_group(_RECOVERABILITY_FIELDS, grades)
def comfort_results(grades):     return _physical_group(_COMFORT_FIELDS, grades)


# ---------------------------------------------------------------------------
# Timing category: plan_age_sim (SIM diagnostic) + adapter_latency_monotonic (MONOTONIC diagnostic).
# The three metric-timing fields depend on F5 => UNKNOWN.
# ---------------------------------------------------------------------------

def timing_results(grades: dict[str, EvidenceGrade], plan_age_sim: float | None,
                   adapter_latency_s: float | None,
                   source_artifacts: tuple[str, ...]) -> dict[str, Result]:
    out: dict[str, Result] = {}
    # metric timing (blocked by F5)
    for field in ("time_to_decision", "answer_deadline", "remaining_action_window"):
        out[field] = _physical_unknown(field, grades, get_spec(field).unit)

    # plan_age_sim: SIM-domain diagnostic (deps T1/T2, both VERIFIED) -> AVAILABLE if computable.
    if plan_age_sim is None:
        out["plan_age_sim"] = unknown(
            reason_code="PLAN_AGE_SIM_UNCOMPUTABLE",
            dependencies=get_spec("plan_age_sim").dependencies,
            evidence_grade=EvidenceGrade.VERIFIED, unit="SECOND_SIM",
            usage_purpose=_DIAGNOSTIC, clock_domain="SIM")
    else:
        out["plan_age_sim"] = _diagnostic_available(
            plan_age_sim, "SECOND_SIM", None, "plan_age_sim", grades, source_artifacts,
            clock_domain="SIM", limitations="SIM-domain plan age; not a wall-clock deadline.")

    # adapter_latency_monotonic: MONOTONIC diagnostic (dep T4=SUPPORTED). DIAGNOSTIC_ONLY, never
    # a time_to_decision / answer_deadline / safety deadline.
    if adapter_latency_s is None:
        out["adapter_latency_monotonic"] = unknown(
            reason_code="ADAPTER_LATENCY_UNCOMPUTABLE",
            dependencies=get_spec("adapter_latency_monotonic").dependencies,
            evidence_grade=EvidenceGrade.SUPPORTED, unit="SECOND_MONOTONIC",
            usage_purpose=_DIAGNOSTIC, clock_domain="MONOTONIC")
    else:
        out["adapter_latency_monotonic"] = _diagnostic_available(
            adapter_latency_s, "SECOND_MONOTONIC", None, "adapter_latency_monotonic", grades,
            source_artifacts, clock_domain="MONOTONIC",
            limitations="Monotonic diagnostic latency; NOT time_to_decision, answer_deadline, or a safety deadline.")
    return out
