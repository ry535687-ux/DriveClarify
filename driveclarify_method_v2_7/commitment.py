"""Generic semantic/topological commitment owners for Method V2.7."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence, Tuple

from .contracts import (
    CommitmentFamilyV27,
    ContinuationAssessmentV27,
    ContinuationStateV27,
    SemanticCommitmentBoundaryV27,
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _finite_nonnegative(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("SEMANTIC_COMMITMENT_PROGRESS_INVALID")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError("SEMANTIC_COMMITMENT_PROGRESS_INVALID")
    return normalized


def derive_maneuver_direction_boundary(
    *, candidate_set_digest: str, candidate_branches: Sequence[Mapping[str, Any]],
    route_version: str, environment_digest: str,
) -> SemanticCommitmentBoundaryV27:
    """Bind the earliest candidate-exclusive branch-entry topology cut."""

    rows = tuple(candidate_branches)
    candidate_ids = tuple(sorted(str(row.get("candidate_id") or "") for row in rows))
    if len(rows) < 2 or len(set(candidate_ids)) != len(rows) or not all(candidate_ids):
        raise ValueError("MD_COMMITMENT_CANDIDATE_KEYSET_INVALID")
    identities = tuple(str(row.get("exclusive_branch_identity") or "") for row in rows)
    if not all(identities) or len(set(identities)) < 2:
        raise ValueError("MD_EXCLUSIVE_BRANCH_TOPOLOGY_UNAVAILABLE")
    progress = tuple(
        _finite_nonnegative(row.get("exclusive_entry_progress_m")) for row in rows
    )
    payload = {
        "family": CommitmentFamilyV27.MANEUVER_DIRECTION.value,
        "candidate_set_digest": candidate_set_digest,
        "candidate_ids": candidate_ids,
        "exclusive_branch_identities": identities,
        "exclusive_entry_progress_m": progress,
        "route_version": route_version,
        "environment_digest": environment_digest,
    }
    return SemanticCommitmentBoundaryV27(
        family=CommitmentFamilyV27.MANEUVER_DIRECTION,
        candidate_set_digest=str(candidate_set_digest),
        candidate_ids=candidate_ids,
        route_version=str(route_version),
        environment_digest=str(environment_digest),
        boundary_progress_m=min(progress),
        topology_event_kind="CANDIDATE_EXCLUSIVE_BRANCH_ENTRY_CUT",
        topology_event_identity=_digest(payload),
    )


def derive_execution_location_boundary(
    *, candidate_set_digest: str, candidate_ordinals: Mapping[str, int],
    ordered_opportunities: Sequence[Mapping[str, Any]], route_version: str,
    environment_digest: str,
) -> SemanticCommitmentBoundaryV27:
    """Bind the first opportunity whose consumption distinguishes survivors."""

    ordinals = {str(key): int(value) for key, value in candidate_ordinals.items()}
    if len(ordinals) < 2 or len(set(ordinals.values())) < 2:
        raise ValueError("EL_ORDERED_SEMANTIC_OBLIGATIONS_NOT_DISTINCT")
    opportunities = sorted(
        tuple(ordered_opportunities), key=lambda row: int(row["route_order_index"])
    )
    if not opportunities:
        raise ValueError("EL_ORDERED_OPPORTUNITIES_UNAVAILABLE")
    by_order = {int(row["route_order_index"]): row for row in opportunities}
    if not set(ordinals.values()).issubset(set(by_order)):
        raise ValueError("EL_QUALIFYING_OPPORTUNITY_OWNER_MISSING")
    decisive_ordinal = min(ordinals.values())
    decisive = by_order[decisive_ordinal]
    progress = _finite_nonnegative(
        decisive.get("junction_entry_progress_m", decisive.get("distance_or_progress"))
    )
    identities = tuple(
        str(row.get("target_id") or row.get("topology_event_identity") or "")
        for row in opportunities
        if int(row["route_order_index"]) in set(ordinals.values())
    )
    if not all(identities) or len(identities) < len(set(ordinals.values())):
        raise ValueError("EL_OPPORTUNITY_IDENTITY_INVALID")
    payload = {
        "family": CommitmentFamilyV27.EXECUTION_LOCATION.value,
        "candidate_set_digest": candidate_set_digest,
        "candidate_ordinals": sorted(ordinals.items()),
        "ordered_opportunity_identities": identities,
        "decisive_ordinal": decisive_ordinal,
        "progress_m": progress,
        "route_version": route_version,
        "environment_digest": environment_digest,
    }
    return SemanticCommitmentBoundaryV27(
        family=CommitmentFamilyV27.EXECUTION_LOCATION,
        candidate_set_digest=str(candidate_set_digest),
        candidate_ids=tuple(sorted(ordinals)),
        route_version=str(route_version),
        environment_digest=str(environment_digest),
        boundary_progress_m=progress,
        topology_event_kind="ORDERED_QUALIFYING_OPPORTUNITY_CONSUMPTION",
        topology_event_identity=_digest(payload),
        ordered_opportunity_identities=identities,
        decisive_opportunity_ordinal=decisive_ordinal,
        nominal_coincident_supported=True,
        reason_code="EL_NOMINAL_COINCIDENT_FIRST_COMMITMENT_AVAILABLE",
    )


def assess_latest_reversible_baseline_continuation(
    *, boundary: SemanticCommitmentBoundaryV27, current_progress_m: Any,
    next_observation_progress_upper_m: Any, calibrated_uncertainty_m: Any,
    baseline_motion_admissible: Any, lawful_holding_available: Any,
) -> ContinuationAssessmentV27:
    """Classify the boundary without using candidate-polyline L2 distance."""

    try:
        current = _finite_nonnegative(current_progress_m)
        next_progress = _finite_nonnegative(next_observation_progress_upper_m)
        uncertainty = _finite_nonnegative(calibrated_uncertainty_m)
        latest = _finite_nonnegative(boundary.boundary_progress_m) - uncertainty
    except ValueError:
        return ContinuationAssessmentV27(
            ContinuationStateV27.UNKNOWN, None, None, None, None, None,
            ("LATEST_REVERSIBLE_BOUNDARY_EVIDENCE_INVALID",),
        )
    if latest < 0.0 or type(baseline_motion_admissible) is not bool or type(lawful_holding_available) is not bool:
        return ContinuationAssessmentV27(
            ContinuationStateV27.UNKNOWN, current, next_progress, latest,
            baseline_motion_admissible if type(baseline_motion_admissible) is bool else None,
            lawful_holding_available if type(lawful_holding_available) is bool else None,
            ("LATEST_REVERSIBLE_BOUNDARY_DEPENDENCY_UNKNOWN",),
        )
    if current >= latest:
        state = ContinuationStateV27.COMMITMENT_CROSSED
        reasons = ("UNRESOLVED_SEMANTIC_COMMITMENT_CROSSED",)
    elif not baseline_motion_admissible or next_progress >= latest:
        state = ContinuationStateV27.COMMITMENT_IMMINENT
        reasons = ("BASELINE_CONTINUATION_WOULD_CROSS_COMMITMENT",)
    else:
        state = ContinuationStateV27.REVERSIBLE
        reasons = ("BASELINE_CONTINUATION_SEMANTICALLY_REVERSIBLE",)
    return ContinuationAssessmentV27(
        state, current, next_progress, latest, baseline_motion_admissible,
        lawful_holding_available, reasons,
    )


def bind_semantic_connector_targets(
    candidate_rows: Sequence[Mapping[str, Any]],
    opportunities: Sequence[Mapping[str, Any]],
) -> Tuple[Mapping[str, Any], ...]:
    """Resolve current semantic candidates to ordered opportunity owners.

    This is the deterministic EL stale-target repair. It is deliberately
    separate from the commitment functions above.
    """

    available = tuple(opportunities)
    by_target = {str(row.get("target_id")): row for row in available}
    by_order = {}
    for row in available:
        try:
            by_order[int(row["route_order_index"])] = row
        except (KeyError, TypeError, ValueError):
            continue
    ordinal = {"FIRST": 1, "SECOND": 2, "THIRD": 3}
    resolved = []
    for candidate in candidate_rows:
        constraint = str(candidate.get("r4_4_semantic_constraint") or "").upper()
        obligation_type = str(candidate.get("r4_4_obligation_type") or "")
        opportunity = None
        owner = "BOUND_TARGET_ID"
        if obligation_type == "EXECUTION_LOCATION" and constraint in ordinal:
            opportunity = by_order.get(ordinal[constraint])
            owner = "SEMANTIC_ORDERED_QUALIFYING_OPPORTUNITY"
        if opportunity is None:
            opportunity = by_target.get(str(candidate.get("target_id")))
        resolved.append({
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "opportunity": opportunity,
            "target_owner": owner,
            "semantic_constraint": constraint,
        })
    return tuple(resolved)


__all__ = [
    "assess_latest_reversible_baseline_continuation",
    "bind_semantic_connector_targets",
    "derive_execution_location_boundary",
    "derive_maneuver_direction_boundary",
]
