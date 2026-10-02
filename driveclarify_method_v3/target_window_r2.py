"""R2 connector target owner consuming only consolidated physical phase.

Historical R1 remains frozen in :mod:`driveclarify_method_v3.target_window`.
R2 reuses its existing-connector target-window math only after replacing R1's
suffix-based exit inference with the transaction-bound phase owner.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from .connector_phase_r2 import ConnectorPhysicalPhaseR2
from .target_window import (
    BRANCH_CORRIDOR_TOLERANCE_M,
    ConnectorTargetWindowDecision as R1Decision,
    ConnectorTargetWindowState as R1State,
    _reconcile_active_suffix,
    select_connector_target_window as select_r1_connector_target_window,
)


CONTRACT_IDENTITY = "DriveClarify V3 Connector-Phase Target Ownership Contract R2"
OWNER_IDENTITY = "SELECTED_CONNECTOR_TARGET_WINDOW_R2"
SUPERSEDED_CONTRACT_IDENTITY = (
    "DriveClarify V3 Connector-Phase Target Ownership Contract R1"
)
SUPERSEDED_OWNER_IDENTITY = "SELECTED_CONNECTOR_TARGET_WINDOW_R1"


@dataclass(frozen=True)
class ConnectorTargetWindowStateR2:
    method_phase: str
    connector_phase: str
    live_connector_relation: str
    planning_effective_k: int
    selected_transaction_valid: bool
    selected_transaction_identity: str
    expected_transaction_identity: str
    active_route_identity: str
    installed_route_identity: str
    active_route_generation: int
    installed_route_generation: int
    current_destination_identity: str
    original_destination_identity: str
    committed: bool
    terminal_fail_closed: bool
    release_latched: bool


@dataclass(frozen=True)
class ConnectorContextValidationR2:
    """Pure hard-gate result shared by phase mutation and target selection."""

    valid: bool
    reason: str | None
    active_route_original_offset: int | None


@dataclass(frozen=True)
class ConnectorTargetWindowDecisionR2:
    mode: str
    reason: str
    tp1: Any | None
    tp2: Any | None
    tp1_original_index: int | None
    tp2_original_index: int | None
    active_route_original_offset: int | None
    ego_connector_progress_m: float | None
    ego_connector_projection_distance_m: float | None
    tp1_connector_progress_m: float | None
    tp2_connector_progress_m: float | None
    installed_route_progress_m: float | None
    installed_route_projection_distance_m: float | None
    installed_route_projection_segment_original_indices: tuple[int, int] | None
    fallback_invoked: bool
    release_latched: bool
    physical_exit_observation_tick: bool

    def evidence(self) -> dict[str, Any]:
        return {
            "contract_identity": CONTRACT_IDENTITY,
            "owner_identity": OWNER_IDENTITY,
            "superseded_contract_identity": SUPERSEDED_CONTRACT_IDENTITY,
            "superseded_owner_identity": SUPERSEDED_OWNER_IDENTITY,
            "mode": self.mode,
            "reason": self.reason,
            "tp1_original_index": self.tp1_original_index,
            "tp2_original_index": self.tp2_original_index,
            "active_route_original_offset": self.active_route_original_offset,
            "ego_connector_progress_m": self.ego_connector_progress_m,
            "ego_connector_projection_distance_m": (
                self.ego_connector_projection_distance_m
            ),
            "tp1_connector_progress_m": self.tp1_connector_progress_m,
            "tp2_connector_progress_m": self.tp2_connector_progress_m,
            "installed_route_progress_m": None,
            "installed_route_projection_distance_m": None,
            "installed_route_projection_segment_original_indices": None,
            "fallback_invoked": self.fallback_invoked,
            "release_latched": self.release_latched,
            "physical_exit_observation_tick": (self.physical_exit_observation_tick),
            "route_mutated": False,
            "waypoint_synthesized": False,
            "new_planner_count": 0,
            "new_threshold_count": 0,
            "suffix_geometry_consulted_for_release": False,
            "full_route_projection_consulted_for_release": False,
            "road_option_consulted_for_release": False,
            "filtered_gps_consulted_for_physical_phase": False,
        }


def _decision_from_r1(
    decision: R1Decision, *, reason: str | None = None
) -> ConnectorTargetWindowDecisionR2:
    return ConnectorTargetWindowDecisionR2(
        mode=decision.mode,
        reason=decision.reason if reason is None else reason,
        tp1=decision.tp1,
        tp2=decision.tp2,
        tp1_original_index=decision.tp1_original_index,
        tp2_original_index=decision.tp2_original_index,
        active_route_original_offset=decision.active_route_original_offset,
        ego_connector_progress_m=decision.ego_connector_progress_m,
        ego_connector_projection_distance_m=(
            decision.ego_connector_projection_distance_m
        ),
        tp1_connector_progress_m=decision.tp1_connector_progress_m,
        tp2_connector_progress_m=decision.tp2_connector_progress_m,
        installed_route_progress_m=None,
        installed_route_projection_distance_m=None,
        installed_route_projection_segment_original_indices=None,
        fallback_invoked=decision.fallback_invoked,
        release_latched=decision.release_latched,
        physical_exit_observation_tick=False,
    )


def _fail(reason: str, offset: int | None = None) -> ConnectorTargetWindowDecisionR2:
    return ConnectorTargetWindowDecisionR2(
        mode="FAIL_CLOSED",
        reason=reason,
        tp1=None,
        tp2=None,
        tp1_original_index=None,
        tp2_original_index=None,
        active_route_original_offset=offset,
        ego_connector_progress_m=None,
        ego_connector_projection_distance_m=None,
        tp1_connector_progress_m=None,
        tp2_connector_progress_m=None,
        installed_route_progress_m=None,
        installed_route_projection_distance_m=None,
        installed_route_projection_segment_original_indices=None,
        fallback_invoked=False,
        release_latched=False,
        physical_exit_observation_tick=False,
    )


def _baseline(
    reason: str,
    baseline_targets: tuple[Any, Any],
    *,
    release_latched: bool = False,
    offset: int | None = None,
) -> ConnectorTargetWindowDecisionR2:
    return ConnectorTargetWindowDecisionR2(
        mode="NORMAL_PASSTHROUGH",
        reason=reason,
        tp1=baseline_targets[0],
        tp2=baseline_targets[1],
        tp1_original_index=None,
        tp2_original_index=None,
        active_route_original_offset=offset,
        ego_connector_progress_m=None,
        ego_connector_projection_distance_m=None,
        tp1_connector_progress_m=None,
        tp2_connector_progress_m=None,
        installed_route_progress_m=None,
        installed_route_projection_distance_m=None,
        installed_route_projection_segment_original_indices=None,
        fallback_invoked=False,
        release_latched=release_latched,
        physical_exit_observation_tick=False,
    )


def _physical_exit_observation_baseline(
    baseline_targets: tuple[Any, Any], *, offset: int
) -> ConnectorTargetWindowDecisionR2:
    """Emit one navigation-only tick without granting target release."""

    return ConnectorTargetWindowDecisionR2(
        mode="PHYSICAL_EXIT_OBSERVATION_WINDOW",
        reason="AWAITING_CURRENT_V2_6_PHYSICAL_EXIT_OBSERVATION",
        tp1=baseline_targets[0],
        tp2=baseline_targets[1],
        tp1_original_index=None,
        tp2_original_index=None,
        active_route_original_offset=offset,
        ego_connector_progress_m=None,
        ego_connector_projection_distance_m=None,
        tp1_connector_progress_m=None,
        tp2_connector_progress_m=None,
        installed_route_progress_m=None,
        installed_route_projection_distance_m=None,
        installed_route_projection_segment_original_indices=None,
        fallback_invoked=False,
        release_latched=False,
        physical_exit_observation_tick=True,
    )


def validate_connector_context_r2(
    active_route: Sequence[Any],
    installed_route_catalog: Sequence[Mapping[str, Any]],
    state: ConnectorTargetWindowStateR2,
    *,
    connector_start_index: int,
    connector_end_index: int,
    compatible_suffix_original_indices: Sequence[int],
) -> ConnectorContextValidationR2:
    """Validate every transaction/route gate before any R2 phase mutation."""

    def invalid(reason: str, offset: int | None = None) -> ConnectorContextValidationR2:
        return ConnectorContextValidationR2(False, reason, offset)

    if not state.selected_transaction_valid:
        return invalid("SELECTED_TRANSACTION_INVALID")
    if state.selected_transaction_identity != state.expected_transaction_identity:
        return invalid("SELECTED_TRANSACTION_STALE")
    if state.active_route_identity != state.installed_route_identity:
        return invalid("ACTIVE_ROUTE_IDENTITY_STALE")
    if state.active_route_generation != state.installed_route_generation:
        return invalid("ACTIVE_ROUTE_GENERATION_STALE")
    if state.current_destination_identity != state.original_destination_identity:
        return invalid("GLOBAL_DESTINATION_CHANGED")
    catalog = tuple(installed_route_catalog)
    route = tuple(active_route)
    if not catalog or not route:
        return invalid("ACTIVE_OR_INSTALLED_ROUTE_INVALID")
    if tuple(int(row.get("original_index", -1)) for row in catalog) != tuple(
        range(len(catalog))
    ):
        return invalid("INSTALLED_ROUTE_CATALOG_ORDER_INVALID")
    offset = _reconcile_active_suffix(catalog, route)
    if offset is None:
        return invalid("ACTIVE_ROUTE_SUFFIX_RECONCILIATION_FAILED")
    start, end = int(connector_start_index), int(connector_end_index)
    if not 0 <= start < end < len(catalog):
        return invalid("SELECTED_CONNECTOR_INTERVAL_INVALID", offset)
    if any(
        row.get("topology_role") != "SELECTED_CONNECTOR"
        for row in catalog[start : end + 1]
    ):
        return invalid("SELECTED_CONNECTOR_CATALOG_INVALID", offset)
    expected_roles = tuple(
        (
            "PREFIX"
            if index < start
            else "SELECTED_CONNECTOR" if index <= end else "SELECTED_SUFFIX"
        )
        for index in range(len(catalog))
    )
    if tuple(row.get("topology_role") for row in catalog) != expected_roles:
        return invalid("INSTALLED_ROUTE_CATALOG_ROLE_PARTITION_INVALID", offset)
    suffix_indices = tuple(int(index) for index in compatible_suffix_original_indices)
    if suffix_indices != tuple(range(end + 1, len(catalog))):
        return invalid("SELECTED_SUFFIX_CATALOG_INVALID", offset)
    if not math.isfinite(BRANCH_CORRIDOR_TOLERANCE_M) or (
        BRANCH_CORRIDOR_TOLERANCE_M < 0.0
    ):
        return invalid("BRANCH_CORRIDOR_TOLERANCE_INVALID", offset)
    return ConnectorContextValidationR2(True, None, offset)


def select_connector_target_window_r2(
    active_route: Sequence[Any],
    installed_route_catalog: Sequence[Mapping[str, Any]],
    state: ConnectorTargetWindowStateR2,
    *,
    connector_start_index: int,
    connector_end_index: int,
    compatible_suffix_original_indices: Sequence[int],
    ego_xyz_m: Sequence[float],
    baseline_targets: tuple[Any, Any],
) -> ConnectorTargetWindowDecisionR2:
    """Select targets without granting any installed suffix physical authority."""

    if state.planning_effective_k == 1:
        return _baseline("PLANNING_EFFECTIVE_K1_BASELINE_UNCHANGED", baseline_targets)
    catalog = tuple(installed_route_catalog)
    route = tuple(active_route)
    validation = validate_connector_context_r2(
        route,
        catalog,
        state,
        connector_start_index=connector_start_index,
        connector_end_index=connector_end_index,
        compatible_suffix_original_indices=compatible_suffix_original_indices,
    )
    if not validation.valid:
        assert validation.reason is not None
        return _fail(validation.reason, validation.active_route_original_offset)
    offset = validation.active_route_original_offset
    assert offset is not None
    start, end = int(connector_start_index), int(connector_end_index)
    suffix_indices = tuple(int(index) for index in compatible_suffix_original_indices)
    if state.terminal_fail_closed or (
        state.connector_phase == ConnectorPhysicalPhaseR2.TERMINAL_FAIL_CLOSED.value
    ):
        return _fail("CONSOLIDATED_PHASE_TERMINAL_FAIL_CLOSED", offset)
    if state.committed:
        return _baseline(
            (
                "CONSOLIDATED_RELEASE_LATCHED"
                if state.release_latched
                else "V2_COMMITTED_RELEASE"
            ),
            baseline_targets,
            release_latched=True,
            offset=offset,
        )
    if state.connector_phase == ConnectorPhysicalPhaseR2.PHYSICALLY_EXITED.value:
        return _baseline(
            (
                "CONSOLIDATED_RELEASE_LATCHED"
                if state.release_latched
                else "CONSOLIDATED_PHYSICALLY_EXITED_RELEASE"
            ),
            baseline_targets,
            release_latched=True,
            offset=offset,
        )
    if state.method_phase != "SELECTED_ACTIVE_PRECOMMIT":
        return _baseline("OUTSIDE_SELECTED_ACTIVE_PRECOMMIT", baseline_targets)
    if state.connector_phase not in {
        ConnectorPhysicalPhaseR2.BEFORE.value,
        ConnectorPhysicalPhaseR2.INSIDE.value,
    }:
        return _fail("CONSOLIDATED_CONNECTOR_PHASE_UNKNOWN", offset)
    if state.connector_phase == ConnectorPhysicalPhaseR2.INSIDE.value and offset > end:
        return _physical_exit_observation_baseline(
            baseline_targets,
            offset=offset,
        )
    if (
        state.connector_phase == ConnectorPhysicalPhaseR2.BEFORE.value
        and state.live_connector_relation != "BEFORE_CONNECTOR"
    ):
        return _fail("INCOMPATIBLE_EGO_TOPOLOGY", offset)

    # R1 is retained only as the frozen existing-connector target-window math.
    # Its suffix/full-route release branch is unreachable because R2 supplies
    # only BEFORE_CONNECTOR or SELECTED_CONNECTOR after phase authorization.
    r1_state = R1State(
        method_phase="SELECTED_ACTIVE_PRECOMMIT",
        planning_effective_k=state.planning_effective_k,
        selected_transaction_valid=state.selected_transaction_valid,
        selected_transaction_identity=state.selected_transaction_identity,
        expected_transaction_identity=state.expected_transaction_identity,
        active_route_identity=state.active_route_identity,
        installed_route_identity=state.installed_route_identity,
        active_route_generation=state.active_route_generation,
        installed_route_generation=state.installed_route_generation,
        current_destination_identity=state.current_destination_identity,
        original_destination_identity=state.original_destination_identity,
        topology_relation=(
            "BEFORE_CONNECTOR"
            if state.connector_phase == ConnectorPhysicalPhaseR2.BEFORE.value
            else "SELECTED_CONNECTOR"
        ),
        committed=False,
        release_latched=False,
        observed_selected_connector_membership=(
            state.connector_phase == ConnectorPhysicalPhaseR2.INSIDE.value
        ),
        observed_topology_compatible_with_selected_exit_or_suffix=False,
    )
    decision = select_r1_connector_target_window(
        route,
        catalog,
        r1_state,
        connector_start_index=start,
        connector_end_index=end,
        compatible_suffix_original_indices=suffix_indices,
        ego_xyz_m=ego_xyz_m,
        baseline_targets=baseline_targets,
    )
    return _decision_from_r1(decision)


__all__ = [
    "CONTRACT_IDENTITY",
    "OWNER_IDENTITY",
    "SUPERSEDED_CONTRACT_IDENTITY",
    "SUPERSEDED_OWNER_IDENTITY",
    "ConnectorTargetWindowDecisionR2",
    "ConnectorContextValidationR2",
    "ConnectorTargetWindowStateR2",
    "select_connector_target_window_r2",
    "validate_connector_context_r2",
]
