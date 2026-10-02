"""Runtime-only categorical observable commitment export."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .canonical import canonical_sha256
from .firewall import assert_no_oracle_fields
from .models import ObservableCommitment, ObservableCommitmentState


ALLOWED_OBSERVABLE_SIGNALS = frozenset(
    {
        "ego_pose",
        "ego_yaw",
        "ego_speed",
        "road_lane_junction_membership",
        "active_route_identity",
        "route_generation",
        "active_suffix",
        "road_options",
        "current_maneuver_identity",
        "current_branch_or_connector_identity",
        "connector_phase",
        "branch_progress",
        "structural_divergence_relation",
        "alternative_topological_executable",
        "recovery_topological_executable",
        "candidate_feasibility_state",
        "current_local_connectivity",
        "current_local_geometry",
        "rule_valid",
        "hard_safety_valid",
        "current_opportunity_available",
        "global_task_identity_G",
    }
)


@dataclass(frozen=True)
class ObservableSignals:
    source_frame: int
    source_sim_time_s: float
    connector_phase: str | None
    structural_divergence_relation: str | None
    alternative_topological_executable: bool | None
    recovery_topological_executable: bool | None
    current_opportunity_available: bool | None
    rule_valid: bool | None
    hard_safety_valid: bool | None
    active_route_identity: str | None
    route_generation: int | None
    current_maneuver_identity: str | None
    current_branch_or_connector_identity: str | None
    global_task_identity_G: str | None
    source_hashes: tuple[tuple[str, str], ...]


def export_observable_commitment(
    signals: ObservableSignals | Mapping[str, object],
) -> ObservableCommitment:
    """Map allowlisted same-frame evidence to the frozen four-state enum.

    There are no numeric thresholds and no confidence value.  Missing or
    unrecognized evidence returns ``UNKNOWN``.
    """

    assert_no_oracle_fields(signals)
    if isinstance(signals, Mapping):
        extra = set(signals) - {
            field_name for field_name in ObservableSignals.__dataclass_fields__
        }
        if extra:
            raise ValueError("OBSERVABLE_SIGNAL_NOT_ALLOWLISTED:" + ",".join(sorted(extra)))
        signals = ObservableSignals(**signals)  # type: ignore[arg-type]

    manifest = (
        "active_route_identity",
        "route_generation",
        "current_maneuver_identity",
        "current_branch_or_connector_identity",
        "connector_phase",
        "structural_divergence_relation",
        "alternative_topological_executable",
        "recovery_topological_executable",
        "current_opportunity_available",
        "rule_valid",
        "hard_safety_valid",
        "global_task_identity_G",
    )
    required_values = {
        "active_route_identity": signals.active_route_identity,
        "route_generation": signals.route_generation,
        "current_maneuver_identity": signals.current_maneuver_identity,
        "current_branch_or_connector_identity": (
            signals.current_branch_or_connector_identity
        ),
        "connector_phase": signals.connector_phase,
        "structural_divergence_relation": signals.structural_divergence_relation,
        "alternative_topological_executable": (
            signals.alternative_topological_executable
        ),
        "current_opportunity_available": signals.current_opportunity_available,
        "rule_valid": signals.rule_valid,
        "hard_safety_valid": signals.hard_safety_valid,
        "global_task_identity_G": signals.global_task_identity_G,
    }
    provenance = dict(signals.source_hashes)
    required_provenance = {
        "active_route",
        "topology",
        "maneuver_owner",
        "rule_owner",
        "hard_safety_owner",
        "global_task_owner",
    }
    invalid_provenance = tuple(
        name
        for name in sorted(required_provenance)
        if len(str(provenance.get(name, ""))) != 64
    )
    missing = tuple(name for name, value in required_values.items() if value is None)
    state = ObservableCommitmentState.UNKNOWN
    reasons: tuple[str, ...]
    if missing or invalid_provenance:
        reasons = (
            "OBSERVABLE_COMMITMENT_REQUIRED_SIGNAL_OR_PROVENANCE_MISSING",
            *missing,
            *invalid_provenance,
        )
    elif (
        signals.current_opportunity_available is False
        or signals.structural_divergence_relation == "OPPORTUNITY_PASSED"
    ):
        state = ObservableCommitmentState.NO_SAFE_CURRENT_OPPORTUNITY
        reasons = ("CURRENT_OPPORTUNITY_PASSED_OR_UNAVAILABLE",)
    elif signals.rule_valid is False or signals.hard_safety_valid is False:
        state = ObservableCommitmentState.NO_SAFE_CURRENT_OPPORTUNITY
        reasons = ("CURRENT_RULE_OR_HARD_SAFETY_DENIAL",)
    elif (
        signals.connector_phase == "BEFORE"
        and signals.structural_divergence_relation in {"COMMON", "NOT_DIVERGED"}
        and signals.alternative_topological_executable is True
    ):
        state = ObservableCommitmentState.BEFORE_OLD_EXCLUSIVITY
        reasons = ("CURRENT_TOPOLOGY_DUAL_FEASIBLE_BEFORE_EXCLUSIVITY",)
    elif (
        signals.connector_phase in {"INSIDE", "PHYSICALLY_EXITED"}
        and signals.structural_divergence_relation == "OLD_EXCLUSIVE"
        and signals.alternative_topological_executable is False
        and signals.recovery_topological_executable is True
    ):
        state = ObservableCommitmentState.OLD_EXCLUSIVE_RECOVERABLE
        reasons = ("CURRENT_OLD_EXCLUSIVE_TOPOLOGY_WITH_LEGAL_RECOVERY",)
    else:
        reasons = ("OBSERVABLE_COMMITMENT_RELATION_NOT_CERTIFIED",)

    input_hashes = tuple(signals.source_hashes) + (
        ("observable_inputs", canonical_sha256(required_values)),
    )
    return ObservableCommitment(
        state=state,
        source_frame=signals.source_frame,
        source_sim_time_s=signals.source_sim_time_s,
        source_route_generation=int(signals.route_generation or 0),
        allowed_signal_manifest=manifest,
        reason_codes=reasons,
        input_hashes=input_hashes,
    )
