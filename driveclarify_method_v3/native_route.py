"""Native-route facade and pure selected-plan admissibility for Method V3.

The values in this module are immutable evidence.  They neither own a planner
nor mutate a route, trajectory, model input, controller, or vehicle control.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Mapping, Sequence

from .bridge import InstalledRouteRowV3, SelectedRouteBindingReceiptV3


def _digest(value: Any) -> str:
    def normalized(item: Any) -> Any:
        if hasattr(item, "__dataclass_fields__"):
            return normalized(asdict(item))
        if isinstance(item, Mapping):
            return {
                str(key): normalized(member)
                for key, member in sorted(item.items(), key=lambda row: str(row[0]))
            }
        if isinstance(item, (tuple, list)):
            return [normalized(member) for member in item]
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        return repr(item)

    return hashlib.sha256(
        json.dumps(
            normalized(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _endpoint_xyz(endpoint: Any) -> tuple[float, float, float]:
    current = getattr(endpoint, "location", endpoint)
    current = getattr(getattr(current, "transform", None), "location", current)
    if isinstance(current, Mapping):
        values = (current.get("x"), current.get("y"), current.get("z", 0.0))
    elif isinstance(current, (tuple, list)):
        values = tuple(current[:3]) if len(current) >= 3 else tuple(current) + (0.0,)
    else:
        values = (
            getattr(current, "x", None),
            getattr(current, "y", None),
            getattr(current, "z", 0.0),
        )
    try:
        result = tuple(float(value) for value in values)
    except (TypeError, ValueError) as error:
        raise RuntimeError("METHOD_V3_DESTINATION_ENDPOINT_INVALID") from error
    if len(result) != 3 or not all(math.isfinite(value) for value in result):
        raise RuntimeError("METHOD_V3_DESTINATION_ENDPOINT_INVALID")
    return result


@dataclass(frozen=True)
class RouteTopologyIdentityV3:
    waypoint_id: int
    road_id: int
    section_id: int
    lane_id: int
    junction_id: int | None

    @classmethod
    def from_row(cls, row: InstalledRouteRowV3) -> "RouteTopologyIdentityV3":
        return cls(
            waypoint_id=int(row.waypoint_id),
            road_id=int(row.road_id),
            section_id=int(row.section_id),
            lane_id=int(row.lane_id),
            junction_id=None if row.junction_id is None else int(row.junction_id),
        )


@dataclass(frozen=True)
class ResolvedSelectedLocalRoute:
    """Target-window-independent view of one already-installed selected route."""

    candidate_generation_identity: str
    selected_plan_transaction_identity: str
    clarification_query_identity: str | None
    clarification_answer_identity: str | None
    selected_obligation_identity: str
    selected_branch_identity: str
    route_transaction_identity: str
    source_map_topology_identity: str
    planner_capability_identity: str
    nominal_route_identity: str
    route_generation_before: int
    selected_entry_identity: RouteTopologyIdentityV3
    ordered_selected_connector_waypoint_identities: tuple[RouteTopologyIdentityV3, ...]
    selected_exit_identity: RouteTopologyIdentityV3
    ordered_route_rows: tuple[InstalledRouteRowV3, ...]
    selected_connector_interval_identity: str
    downstream_suffix_identity: str
    original_destination_identity: str
    original_destination_endpoint_xyz_m: tuple[float, float, float]
    mission_context_digest: str
    installed_route_identity: str
    route_generation_after: int
    next_tick_consumed_route_identity: str | None
    route_installation_receipt_identity: str
    geometry_evidence_digest: str

    schema_version = "driveclarify.v3.resolved_selected_local_route.v1"

    @classmethod
    def resolve(
        cls,
        *,
        obligation: Any,
        mission: Any,
        receipt: SelectedRouteBindingReceiptV3,
    ) -> "ResolvedSelectedLocalRoute":
        if not isinstance(receipt, SelectedRouteBindingReceiptV3):
            raise TypeError("METHOD_V3_SELECTED_ROUTE_RECEIPT_INVALID")
        rows = receipt.target_window_route_catalog
        connector_rows = tuple(
            row for row in rows if row.topology_role == "SELECTED_CONNECTOR"
        )
        suffix_rows = tuple(
            row for row in rows if row.topology_role == "SELECTED_SUFFIX"
        )
        if not connector_rows or not suffix_rows:
            raise RuntimeError("METHOD_V3_RESOLVED_ROUTE_TOPOLOGY_INCOMPLETE")
        generation_before = receipt.route_generation_before
        generation_after = receipt.route_generation_after
        candidate_generation = receipt.candidate_generation_identity
        selected_plan_transaction = receipt.selected_plan_transaction_identity
        if not (
            isinstance(generation_before, int)
            and isinstance(generation_after, int)
            and generation_after == generation_before + 1
            and candidate_generation
            and selected_plan_transaction
            and receipt.route_install_accepted
            and receipt.route_owner_acknowledged
            and receipt.global_destination_identity_before
            == receipt.global_destination_identity_after
            == obligation.global_destination_identity
            == mission.global_destination_identity
            and obligation.obligation_identity == receipt.selected_obligation_identity
            and obligation.local_branch_identity == receipt.selected_branch_identity
            and obligation.nominal_global_route_identity
            == mission.nominal_global_route_identity
            and obligation.mission_context_digest == mission.mission_context_digest
        ):
            raise RuntimeError("METHOD_V3_RESOLVED_ROUTE_IDENTITY_MISMATCH")
        entry = RouteTopologyIdentityV3.from_row(connector_rows[0])
        exit_identity = RouteTopologyIdentityV3.from_row(connector_rows[-1])
        connector_identities = tuple(
            RouteTopologyIdentityV3.from_row(row) for row in connector_rows
        )
        geometry_digest = _digest(
            [
                {
                    "xyz_m": row.xyz_m,
                    "road_option": asdict(row.road_option),
                    "topology": asdict(RouteTopologyIdentityV3.from_row(row)),
                    "role": row.topology_role,
                }
                for row in rows
            ]
        )
        installation_identity = _digest(
            {
                "route_transaction_identity": receipt.route_transaction_identity,
                "installed_route_identity": receipt.installed_route_identity,
                "route_generation_before": generation_before,
                "route_generation_after": generation_after,
                "destination_identity": receipt.global_destination_identity_after,
                "geometry_evidence_digest": geometry_digest,
            }
        )
        return cls(
            candidate_generation_identity=str(candidate_generation),
            selected_plan_transaction_identity=str(selected_plan_transaction),
            clarification_query_identity=receipt.query_identity,
            clarification_answer_identity=receipt.answer_identity,
            selected_obligation_identity=receipt.selected_obligation_identity,
            selected_branch_identity=receipt.selected_branch_identity,
            route_transaction_identity=receipt.route_transaction_identity,
            source_map_topology_identity=receipt.selected_junction_identity,
            planner_capability_identity=(
                receipt.existing_planner_provenance
                + ":"
                + receipt.existing_planner_object_identity
            ),
            nominal_route_identity=obligation.nominal_global_route_identity,
            route_generation_before=generation_before,
            selected_entry_identity=entry,
            ordered_selected_connector_waypoint_identities=connector_identities,
            selected_exit_identity=exit_identity,
            ordered_route_rows=rows,
            selected_connector_interval_identity=_digest(connector_identities),
            downstream_suffix_identity=_digest(suffix_rows),
            original_destination_identity=receipt.global_destination_identity_before,
            original_destination_endpoint_xyz_m=_endpoint_xyz(
                mission.global_destination_planner_endpoint
            ),
            mission_context_digest=obligation.mission_context_digest,
            installed_route_identity=receipt.installed_route_identity,
            route_generation_after=generation_after,
            next_tick_consumed_route_identity=receipt.next_tick_consumed_route_identity,
            route_installation_receipt_identity=installation_identity,
            geometry_evidence_digest=geometry_digest,
        )

    def with_next_tick_consumption(
        self, consumed_route_identity: str
    ) -> "ResolvedSelectedLocalRoute":
        if consumed_route_identity != self.installed_route_identity:
            raise RuntimeError("METHOD_V3_RESOLVED_ROUTE_NOT_CONSUMED")
        return replace(
            self, next_tick_consumed_route_identity=consumed_route_identity
        )

    def current_context_matches(
        self,
        *,
        active_route_identity: str,
        route_generation: int,
        route_transaction_identity: str,
        selected_plan_transaction_identity: str,
        destination_identity: str,
    ) -> bool:
        return bool(
            self.next_tick_consumed_route_identity == self.installed_route_identity
            and active_route_identity == self.installed_route_identity
            and route_generation == self.route_generation_after
            and route_transaction_identity == self.route_transaction_identity
            and selected_plan_transaction_identity
            == self.selected_plan_transaction_identity
            and destination_identity == self.original_destination_identity
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = self.schema_version
        value["target_window_independent"] = True
        value["new_planner_count"] = 0
        value["synthetic_trajectory_point_count"] = 0
        return value


class SelectedPlanAdmissibility(str, Enum):
    ADMISSIBLE = "ADMISSIBLE"
    NOT_ADMISSIBLE = "NOT_ADMISSIBLE"
    UNKNOWN = "UNKNOWN"


class SelectedConnectorRelation(str, Enum):
    BEFORE = "BEFORE"
    ENTERED = "ENTERED"
    TRAVERSED = "TRAVERSED"
    EXIT_OR_SUFFIX = "EXIT_OR_SUFFIX"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class SelectedPlanAdmissibilityInput:
    resolved_selected_local_route: ResolvedSelectedLocalRoute
    active_route_identity: str
    route_generation: int
    route_transaction_identity: str
    selected_plan_transaction_identity: str
    destination_identity: str
    current_phase: str
    phase_allows_selected_execution: bool
    ego_route_planner_xy_m: tuple[float, float]
    ego_heading_radians: float
    predicted_trajectory_ego_local_xy_m: tuple[tuple[float, float], ...]
    geometry_uncertainty_envelope_m: float
    geometry_evidence_digest: str
    incompatible_branch_observed: bool | None = None


@dataclass(frozen=True)
class SelectedPlanAdmissibilityResult:
    status: SelectedPlanAdmissibility
    reason_code: str
    route_transaction_identity: str
    selected_plan_transaction_identity: str
    installed_route_identity: str
    active_route_identity: str
    route_generation: int
    phase: str
    selected_connector_relation: SelectedConnectorRelation
    monotone_progress_verified: bool | None
    continuous_route_realization_verified: bool | None
    incompatible_branch_observed: bool | None
    geometry_evidence_digest: str | None
    observed_connector_entry: bool
    observed_connector_exit_or_suffix: bool
    planner_call_count: int = 0
    planner_construction_count: int = 0
    model_forward_count: int = 0
    pid_call_count: int = 0
    route_mutation_count: int = 0
    trajectory_mutation_count: int = 0
    alternative_search_count: int = 0
    controller_write_count: int = 0

    schema_version = "driveclarify.v3.selected_plan_admissibility.v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = self.schema_version
        value["status"] = self.status.value
        value["selected_connector_relation"] = self.selected_connector_relation.value
        return value


def _project_to_polyline(
    point: tuple[float, float], polyline: Sequence[tuple[float, float]]
) -> tuple[float, float, int]:
    cumulative = 0.0
    best: tuple[float, float, int] | None = None
    for index, (left, right) in enumerate(zip(polyline, polyline[1:])):
        dx = right[0] - left[0]
        dy = right[1] - left[1]
        squared = dx * dx + dy * dy
        if squared == 0.0:
            fraction = 0.0
        else:
            fraction = max(
                0.0,
                min(
                    1.0,
                    ((point[0] - left[0]) * dx + (point[1] - left[1]) * dy)
                    / squared,
                ),
            )
        projected = (left[0] + fraction * dx, left[1] + fraction * dy)
        distance = math.dist(point, projected)
        progress = cumulative + fraction * math.sqrt(squared)
        candidate = (distance, progress, index)
        if best is None or candidate < best:
            best = candidate
        cumulative += math.sqrt(squared)
    if best is None:
        raise ValueError("ROUTE_POLYLINE_TOO_SHORT")
    return best[1], best[0], best[2]


def _point_to_segment_distance(
    point: tuple[float, float],
    left: tuple[float, float],
    right: tuple[float, float],
) -> float:
    dx = right[0] - left[0]
    dy = right[1] - left[1]
    squared = dx * dx + dy * dy
    if squared == 0.0:
        return math.dist(point, left)
    fraction = max(
        0.0,
        min(
            1.0,
            ((point[0] - left[0]) * dx + (point[1] - left[1]) * dy)
            / squared,
        ),
    )
    return math.dist(
        point,
        (left[0] + fraction * dx, left[1] + fraction * dy),
    )


def _result(
    evidence: SelectedPlanAdmissibilityInput,
    status: SelectedPlanAdmissibility,
    reason: str,
    relation: SelectedConnectorRelation,
    *,
    monotone: bool | None,
    continuous: bool | None = None,
    incompatible: bool | None,
    entry: bool,
    exit_or_suffix: bool,
    geometry_digest: str | None = None,
) -> SelectedPlanAdmissibilityResult:
    route = evidence.resolved_selected_local_route
    return SelectedPlanAdmissibilityResult(
        status=status,
        reason_code=reason,
        route_transaction_identity=evidence.route_transaction_identity,
        selected_plan_transaction_identity=evidence.selected_plan_transaction_identity,
        installed_route_identity=route.installed_route_identity,
        active_route_identity=evidence.active_route_identity,
        route_generation=evidence.route_generation,
        phase=evidence.current_phase,
        selected_connector_relation=relation,
        monotone_progress_verified=monotone,
        continuous_route_realization_verified=continuous,
        incompatible_branch_observed=incompatible,
        geometry_evidence_digest=geometry_digest,
        observed_connector_entry=entry,
        observed_connector_exit_or_suffix=exit_or_suffix,
    )


def verify_selected_plan_admissibility(
    evidence: SelectedPlanAdmissibilityInput,
) -> SelectedPlanAdmissibilityResult:
    """Verify one returned trajectory against one resolved route, without planning."""

    route = evidence.resolved_selected_local_route
    if not route.current_context_matches(
        active_route_identity=evidence.active_route_identity,
        route_generation=evidence.route_generation,
        route_transaction_identity=evidence.route_transaction_identity,
        selected_plan_transaction_identity=evidence.selected_plan_transaction_identity,
        destination_identity=evidence.destination_identity,
    ):
        return _result(
            evidence,
            SelectedPlanAdmissibility.UNKNOWN,
            "STALE_OR_MISMATCHED_ROUTE_TRANSACTION",
            SelectedConnectorRelation.UNRESOLVED,
            monotone=None,
            incompatible=None,
            entry=False,
            exit_or_suffix=False,
        )
    if not evidence.phase_allows_selected_execution:
        return _result(
            evidence,
            SelectedPlanAdmissibility.UNKNOWN,
            "CURRENT_PHASE_NOT_SELECTED_EXECUTION",
            SelectedConnectorRelation.UNRESOLVED,
            monotone=None,
            incompatible=None,
            entry=False,
            exit_or_suffix=False,
        )
    points = evidence.predicted_trajectory_ego_local_xy_m
    try:
        envelope = float(evidence.geometry_uncertainty_envelope_m)
        ego = tuple(float(value) for value in evidence.ego_route_planner_xy_m)
        yaw = float(evidence.ego_heading_radians)
        normalized_points = tuple(
            (float(point[0]), float(point[1])) for point in points
        )
    except (IndexError, TypeError, ValueError):
        normalized_points = ()
        envelope = math.nan
        ego = ()
        yaw = math.nan
    if (
        len(normalized_points) < 2
        or len(ego) != 2
        or not math.isfinite(envelope)
        or envelope <= 0.0
        or not math.isfinite(yaw)
        or not evidence.geometry_evidence_digest
        or not all(math.isfinite(value) for value in ego)
        or not all(math.isfinite(value) for point in normalized_points for value in point)
    ):
        return _result(
            evidence,
            SelectedPlanAdmissibility.UNKNOWN,
            "PLAN_OR_GEOMETRY_EVIDENCE_INCOMPLETE",
            SelectedConnectorRelation.UNRESOLVED,
            monotone=None,
            incompatible=None,
            entry=False,
            exit_or_suffix=False,
        )
    route_xy = tuple((float(row.xyz_m[0]), float(row.xyz_m[1])) for row in route.ordered_route_rows)
    if len(route_xy) < 2 or len(set(route_xy)) < 2:
        return _result(
            evidence,
            SelectedPlanAdmissibility.UNKNOWN,
            "SELECTED_ROUTE_GEOMETRY_UNAVAILABLE",
            SelectedConnectorRelation.UNRESOLVED,
            monotone=None,
            incompatible=None,
            entry=False,
            exit_or_suffix=False,
        )
    connector_indices = tuple(
        index
        for index, row in enumerate(route.ordered_route_rows)
        if row.topology_role == "SELECTED_CONNECTOR"
    )
    if not connector_indices:
        return _result(
            evidence,
            SelectedPlanAdmissibility.UNKNOWN,
            "SELECTED_CONNECTOR_GEOMETRY_UNAVAILABLE",
            SelectedConnectorRelation.UNRESOLVED,
            monotone=None,
            incompatible=None,
            entry=False,
            exit_or_suffix=False,
        )
    cumulative = [0.0]
    for left, right in zip(route_xy, route_xy[1:]):
        cumulative.append(cumulative[-1] + math.dist(left, right))
    connector_start_progress = cumulative[connector_indices[0]]
    connector_exit_progress = cumulative[connector_indices[-1]]
    cosine = math.cos(yaw)
    sine = math.sin(yaw)
    world_points = tuple(
        (
            ego[0] + cosine * point[0] - sine * point[1],
            ego[1] + sine * point[0] + cosine * point[1],
        )
        for point in normalized_points
    )
    projections = tuple(_project_to_polyline(point, route_xy) for point in (ego, *world_points))
    progress = tuple(row[0] for row in projections)
    lateral = tuple(row[1] for row in projections)
    geometry_digest = _digest(
        {
            "route_geometry": route.geometry_evidence_digest,
            "corridor_geometry": evidence.geometry_evidence_digest,
            "ego": ego,
            "yaw": yaw,
            "trajectory": normalized_points,
            "progress": progress,
            "lateral": lateral,
            "envelope": envelope,
        }
    )
    if evidence.incompatible_branch_observed is True:
        return _result(
            evidence,
            SelectedPlanAdmissibility.NOT_ADMISSIBLE,
            "INCOMPATIBLE_BRANCH_POSITIVELY_OBSERVED",
            SelectedConnectorRelation.INCOMPATIBLE,
            monotone=None,
            incompatible=True,
            entry=max(progress) + envelope >= connector_start_progress,
            exit_or_suffix=False,
            geometry_digest=geometry_digest,
        )
    monotone = all(
        following + envelope >= preceding
        for preceding, following in zip(progress, progress[1:])
    )
    entered = max(progress) + envelope >= connector_start_progress
    reached_exit = max(progress) + envelope >= connector_exit_progress
    contradictory_lateral = any(
        point_progress + envelope >= connector_start_progress
        and distance > envelope
        for point_progress, distance in zip(progress, lateral)
    )
    route_coverage_continuous = bool(monotone)
    if route_coverage_continuous:
        trajectory_with_ego = (ego, *world_points)
        for (
            left_point,
            right_point,
            left_progress,
            right_progress,
        ) in zip(
            trajectory_with_ego,
            trajectory_with_ego[1:],
            progress,
            progress[1:],
        ):
            if right_progress < left_progress:
                continue
            covered_vertices = (
                route_point
                for route_point, route_progress in zip(route_xy, cumulative)
                if left_progress <= route_progress <= right_progress
            )
            if any(
                _point_to_segment_distance(
                    route_point, left_point, right_point
                )
                > envelope
                for route_point in covered_vertices
            ):
                route_coverage_continuous = False
                break
    if contradictory_lateral or not monotone or not route_coverage_continuous:
        return _result(
            evidence,
            SelectedPlanAdmissibility.NOT_ADMISSIBLE,
            (
                "SELECTED_CORRIDOR_LEFT_AFTER_ENTRY"
                if contradictory_lateral
                else "MATERIAL_NON_MONOTONE_ROUTE_PROGRESS"
                if not monotone
                else "PLAN_SEGMENT_NOT_CONTINUOUS_WITH_SELECTED_ROUTE"
            ),
            SelectedConnectorRelation.INCOMPATIBLE,
            monotone=monotone,
            continuous=route_coverage_continuous,
            incompatible=True,
            entry=entered,
            exit_or_suffix=False,
            geometry_digest=geometry_digest,
        )
    if not reached_exit:
        return _result(
            evidence,
            SelectedPlanAdmissibility.UNKNOWN,
            "PREDICTION_HORIZON_INSUFFICIENT",
            SelectedConnectorRelation.ENTERED if entered else SelectedConnectorRelation.BEFORE,
            monotone=monotone,
            continuous=route_coverage_continuous,
            incompatible=False,
            entry=entered,
            exit_or_suffix=False,
            geometry_digest=geometry_digest,
        )
    return _result(
        evidence,
        SelectedPlanAdmissibility.ADMISSIBLE,
        "ORDERED_SELECTED_ROUTE_REALIZATION_VERIFIED",
        SelectedConnectorRelation.EXIT_OR_SUFFIX,
        monotone=True,
        continuous=True,
        incompatible=False,
        entry=True,
        exit_or_suffix=True,
        geometry_digest=geometry_digest,
    )


__all__ = [
    "ResolvedSelectedLocalRoute",
    "RouteTopologyIdentityV3",
    "SelectedConnectorRelation",
    "SelectedPlanAdmissibility",
    "SelectedPlanAdmissibilityInput",
    "SelectedPlanAdmissibilityResult",
    "verify_selected_plan_admissibility",
]
