"""Route-bound selected maneuver lifetime for Method V3."""

from __future__ import annotations

from typing import Any, Mapping

from driveclarify_method_v2_8 import TopologyLockedManeuverExecutionV28

from .bridge import SelectedRouteBindingReceiptV3, target_window_catalog_digest
from .native_route import (
    ResolvedSelectedLocalRoute,
    SelectedPlanAdmissibility,
    SelectedPlanAdmissibilityResult,
)


class RouteBoundManeuverExecutionV3(TopologyLockedManeuverExecutionV28):
    schema_version = "driveclarify.method_v3.execution.v1"

    def __init__(self) -> None:
        super().__init__()
        self.selected_route_binding: SelectedRouteBindingReceiptV3 | None = None
        self.resolved_selected_local_route: ResolvedSelectedLocalRoute | None = None
        self.selected_plan_admissibility: SelectedPlanAdmissibilityResult | None = None
        self.route_conditioned_refreshes: list[dict[str, Any]] = []
        self.connector_target_window_evidence: list[dict[str, Any]] = []
        self.native_route_target_owner_evidence: list[dict[str, Any]] = []

    def bind_selected_route(
        self, receipt: SelectedRouteBindingReceiptV3
    ) -> None:
        if not isinstance(receipt, SelectedRouteBindingReceiptV3):
            raise TypeError("METHOD_V3_SELECTED_ROUTE_RECEIPT_INVALID")
        if self.selected_route_binding is not None:
            raise RuntimeError("METHOD_V3_SELECTED_ROUTE_ALREADY_BOUND")
        catalog = receipt.target_window_route_catalog
        start = receipt.mandatory_connector_start_index
        end = receipt.mandatory_connector_end_index
        expected_roles = tuple(
            "PREFIX"
            if index < start
            else "SELECTED_CONNECTOR"
            if index <= end
            else "SELECTED_SUFFIX"
            for index in range(len(catalog))
        )
        if not (
            receipt.route_owner_acknowledged
            and receipt.route_install_attempted
            and receipt.route_install_accepted
            and receipt.post_install_mandatory_connector_present
            and receipt.selected_exit_terminal_lane_match
            and receipt.mandatory_connector_present
            and receipt.mandatory_connector_order_valid
            and not receipt.incompatible_connector_before_selected
            and receipt.connector_direction_valid
            and receipt.route_continuity_valid
            and receipt.route_commands_preserved
            and receipt.planning_effective_k >= 1
            and receipt.target_window_catalog_bound
            and receipt.installed_route_coordinate_domain
            == "SIMLINGO_ROUTE_PLANNER"
            and receipt.connector_projection_coordinate_domain
            == "SIMLINGO_ROUTE_PLANNER"
            and receipt.source_topology_coordinate_domain == "CARLA_WORLD"
            and all(
                row.coordinate_domain == "SIMLINGO_ROUTE_PLANNER"
                and row.carla_world_coordinate_domain == "CARLA_WORLD"
                for row in catalog
            )
            and len(catalog) == receipt.installed_route_row_count
            and 0 <= start < end < len(catalog)
            and tuple(row.topology_role for row in catalog) == expected_roles
            and receipt.target_window_compatible_suffix_original_indices
            == tuple(range(end + 1, len(catalog)))
            and receipt.target_window_connector_terminal_identity
            == catalog[end]
            and receipt.target_window_route_catalog_digest
            == target_window_catalog_digest(receipt.target_window_route_catalog)
            and receipt.new_global_planner_count == 0
            and receipt.synthetic_trajectory_point_count == 0
        ):
            raise RuntimeError("METHOD_V3_SELECTED_ROUTE_NOT_VALIDATED")
        self.selected_route_binding = receipt

    def bind_resolved_selected_local_route(
        self, route: ResolvedSelectedLocalRoute
    ) -> None:
        binding = self.selected_route_binding
        if binding is None:
            raise RuntimeError("METHOD_V3_SELECTED_ROUTE_MISSING")
        if self.resolved_selected_local_route is not None:
            raise RuntimeError("METHOD_V3_RESOLVED_SELECTED_ROUTE_ALREADY_BOUND")
        if not (
            isinstance(route, ResolvedSelectedLocalRoute)
            and route.selected_obligation_identity
            == binding.selected_obligation_identity
            and route.selected_branch_identity == binding.selected_branch_identity
            and route.route_transaction_identity
            == binding.route_transaction_identity
            and route.installed_route_identity == binding.installed_route_identity
            and route.route_generation_after == binding.route_generation_after
            and route.original_destination_identity
            == binding.global_destination_identity_before
            == binding.global_destination_identity_after
            and route.ordered_route_rows is binding.target_window_route_catalog
        ):
            raise RuntimeError("METHOD_V3_RESOLVED_SELECTED_ROUTE_MISMATCH")
        self.resolved_selected_local_route = route

    def record_native_route_consumption(
        self,
        *,
        active_route_identity: str,
        consumed_route_identity: str,
        route_generation: int,
        destination_identity: str,
    ) -> ResolvedSelectedLocalRoute:
        route = self.resolved_selected_local_route
        if route is None:
            raise RuntimeError("METHOD_V3_RESOLVED_SELECTED_ROUTE_MISSING")
        consumed = route.with_next_tick_consumption(consumed_route_identity)
        if not consumed.current_context_matches(
            active_route_identity=active_route_identity,
            route_generation=route_generation,
            route_transaction_identity=consumed.route_transaction_identity,
            selected_plan_transaction_identity=(
                consumed.selected_plan_transaction_identity
            ),
            destination_identity=destination_identity,
        ):
            raise RuntimeError("METHOD_V3_NATIVE_ROUTE_CONTEXT_MISMATCH")
        self.resolved_selected_local_route = consumed
        return consumed

    def native_route_target_passthrough(
        self,
        baseline_targets: tuple[Any, Any],
        *,
        active_route_identity: str,
        consumed_route_identity: str,
        route_generation: int,
        destination_identity: str,
        frame_id: int | None,
    ) -> tuple[Any, Any]:
        route = self.resolved_selected_local_route
        if route is None:
            raise RuntimeError("METHOD_V3_RESOLVED_SELECTED_ROUTE_MISSING")
        if not (
            active_route_identity == route.installed_route_identity
            and consumed_route_identity == route.installed_route_identity
            and route_generation == route.route_generation_after
            and destination_identity == route.original_destination_identity
        ):
            raise RuntimeError("METHOD_V3_NATIVE_ROUTE_TARGET_CONTEXT_MISMATCH")
        evidence = {
            "frame_id": frame_id,
            "mode": "NATIVE_ROUTE_PASSTHROUGH",
            "owner_identity": "LINGO_AGENT_TICK_EXISTING_ROUTE_PLANNER_RUN_STEP",
            "installed_route_identity": route.installed_route_identity,
            "next_tick_consumed_route_identity": consumed_route_identity,
            "route_transaction_identity": route.route_transaction_identity,
            "route_generation": route.route_generation_after,
            "destination_identity_unchanged": True,
            "baseline_target_objects_returned_by_identity": True,
            "special_target_window_reached": False,
            "target_search_count": 0,
            "route_mutated": False,
            "waypoint_synthesized": False,
            "new_planner_count": 0,
            "new_threshold_count": 0,
        }
        self.native_route_target_owner_evidence.append(evidence)
        return baseline_targets

    def record_selected_plan_admissibility(
        self, result: SelectedPlanAdmissibilityResult
    ) -> None:
        route = self.resolved_selected_local_route
        if route is None:
            raise RuntimeError("METHOD_V3_RESOLVED_SELECTED_ROUTE_MISSING")
        if not (
            isinstance(result, SelectedPlanAdmissibilityResult)
            and result.installed_route_identity == route.installed_route_identity
            and result.route_transaction_identity == route.route_transaction_identity
            and result.selected_plan_transaction_identity
            == route.selected_plan_transaction_identity
            and result.planner_call_count == 0
            and result.planner_construction_count == 0
            and result.model_forward_count == 0
            and result.pid_call_count == 0
            and result.route_mutation_count == 0
            and result.trajectory_mutation_count == 0
            and result.alternative_search_count == 0
            and result.controller_write_count == 0
        ):
            raise RuntimeError("METHOD_V3_SELECTED_PLAN_ADMISSIBILITY_MISMATCH")
        self.selected_plan_admissibility = result

    @property
    def selected_act_admissible(self) -> bool:
        result = self.selected_plan_admissibility
        return bool(
            result is not None
            and result.status is SelectedPlanAdmissibility.ADMISSIBLE
        )

    def activate(self, identity: Any, contract: Any, **kwargs: Any) -> None:
        route = self.selected_route_binding
        if route is None:
            raise RuntimeError("METHOD_V3_SELECTED_ROUTE_MISSING")
        if not (
            route.selected_obligation_identity == identity.obligation_identity
            and route.selected_branch_identity == identity.branch_identity
            and route.global_destination_identity_before
            == identity.global_destination_identity
            and route.global_destination_identity_after
            == identity.global_destination_identity
        ):
            raise RuntimeError("METHOD_V3_SELECTED_ROUTE_IDENTITY_MISMATCH")
        super().activate(identity, contract, **kwargs)

    def record_route_conditioned_refresh(
        self,
        *,
        observation_id: str,
        frame_id: int,
        active_route_identity: str,
        consumed_route_identity: str,
        projection_digest: str,
        refreshed_route_digest: str,
        refreshed_speed_digest: str,
    ) -> None:
        binding = self.selected_route_binding
        if binding is None:
            raise RuntimeError("METHOD_V3_SELECTED_ROUTE_MISSING")
        if not (
            active_route_identity == binding.installed_route_identity
            and consumed_route_identity == binding.installed_route_identity
        ):
            raise RuntimeError("METHOD_V3_INSTALLED_ROUTE_NOT_CONSUMED")
        self.route_conditioned_refreshes.append(
            {
                "observation_id": str(observation_id),
                "frame_id": int(frame_id),
                "installed_route_identity": binding.installed_route_identity,
                "active_route_identity": str(active_route_identity),
                "consumed_route_identity": str(consumed_route_identity),
                "projection_digest": str(projection_digest),
                "refreshed_route_digest": str(refreshed_route_digest),
                "refreshed_speed_digest": str(refreshed_speed_digest),
                "target_point_owner": (
                    str(
                        self.connector_target_window_evidence[-1].get(
                            "owner_identity",
                            "SELECTED_CONNECTOR_TARGET_WINDOW_R2",
                        )
                    )
                    if self.connector_target_window_evidence
                    and self.connector_target_window_evidence[-1].get("frame_id")
                    == int(frame_id)
                    and self.connector_target_window_evidence[-1].get("mode")
                    == "SPECIAL_WINDOW"
                    else "LINGO_AGENT_TICK_EXISTING_ROUTE_PLANNER_RUN_STEP"
                ),
                "fresh_vla_forward_count": 1,
                "synthetic_trajectory_point_count": 0,
                "planner_call_count_this_observation": 0,
            }
        )

    def record_connector_target_window(
        self, evidence: Mapping[str, Any]
    ) -> None:
        binding = self.selected_route_binding
        if binding is None:
            raise RuntimeError("METHOD_V3_SELECTED_ROUTE_MISSING")
        if not isinstance(evidence, Mapping):
            raise TypeError("METHOD_V3_CONNECTOR_TARGET_WINDOW_EVIDENCE_INVALID")
        row = dict(evidence)
        if not (
            row.get("installed_route_identity")
            == binding.installed_route_identity
            and row.get("route_transaction_identity")
            == binding.route_transaction_identity
            and row.get("installed_route_catalog_digest")
            == binding.target_window_route_catalog_digest
            and row.get("contract_identity")
            == binding.target_window_contract_identity
            and row.get("route_mutated") is False
            and row.get("waypoint_synthesized") is False
            and row.get("new_planner_count") == 0
            and row.get("new_threshold_count") == 0
        ):
            raise RuntimeError("METHOD_V3_CONNECTOR_TARGET_WINDOW_EVIDENCE_MISMATCH")
        self.connector_target_window_evidence.append(row)

    def summary(self) -> dict[str, Any]:
        value = super().summary()
        value.update(
            {
                "schema_version": self.schema_version,
                "method_version": "DriveClarify Method V3",
                "selected_route_binding": (
                    None
                    if self.selected_route_binding is None
                    else self.selected_route_binding.to_dict()
                ),
                "resolved_selected_local_route": (
                    None
                    if self.resolved_selected_local_route is None
                    else self.resolved_selected_local_route.to_dict()
                ),
                "selected_plan_admissibility": (
                    None
                    if self.selected_plan_admissibility is None
                    else self.selected_plan_admissibility.to_dict()
                ),
                "selected_act_requires_admissible": True,
                "route_conditioned_refresh_count": len(
                    self.route_conditioned_refreshes
                ),
                "route_conditioned_refreshes": list(
                    self.route_conditioned_refreshes
                ),
                "connector_target_window_evidence_count": len(
                    self.connector_target_window_evidence
                ),
                "connector_target_window_evidence": list(
                    self.connector_target_window_evidence
                ),
                "native_route_target_owner_evidence_count": len(
                    self.native_route_target_owner_evidence
                ),
                "native_route_target_owner_evidence": list(
                    self.native_route_target_owner_evidence
                ),
                "topology_locked_two_point_refresh_used": False,
                "existing_navigation_route_is_conditioning_owner": True,
            }
        )
        return value
