"""Frozen R1 connector-phase target ownership over one installed route.

This module is deliberately CARLA-, torch-, planner-, and controller-free.  It
selects existing active-route rows and returns evidence; the caller owns phase
and live-topology operands.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence


CONTRACT_IDENTITY = "DriveClarify V3 Connector-Phase Target Ownership Contract R1"
OWNER_IDENTITY = "SELECTED_CONNECTOR_TARGET_WINDOW_R1"
ROUTE_COORDINATE_EQUIVALENCE_M = 1.0e-6
BRANCH_CORRIDOR_TOLERANCE_M = 1.2285785915074636


@dataclass(frozen=True)
class ConnectorTargetWindowState:
    method_phase: str
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
    topology_relation: str
    committed: bool
    release_latched: bool
    observed_selected_connector_membership: bool | None
    observed_topology_compatible_with_selected_exit_or_suffix: bool | None


@dataclass(frozen=True)
class ConnectorTargetWindowDecision:
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

    def evidence(self) -> dict[str, Any]:
        return {
            "contract_identity": CONTRACT_IDENTITY,
            "owner_identity": OWNER_IDENTITY,
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
            "installed_route_progress_m": self.installed_route_progress_m,
            "installed_route_projection_distance_m": (
                self.installed_route_projection_distance_m
            ),
            "installed_route_projection_segment_original_indices": (
                self.installed_route_projection_segment_original_indices
            ),
            "fallback_invoked": self.fallback_invoked,
            "release_latched": self.release_latched,
            "route_mutated": False,
            "waypoint_synthesized": False,
            "new_planner_count": 0,
            "new_threshold_count": 0,
        }


def _option_token(option: Any) -> tuple[str | None, Any]:
    name = getattr(option, "name", None)
    value = getattr(option, "value", None)
    if name is None and isinstance(option, Mapping):
        name = option.get("name")
        value = option.get("value")
    return (None if name is None else str(name), value)


def _active_xyz(row: Any) -> tuple[float, float, float]:
    try:
        values = tuple(float(value) for value in row[0][:3])
    except (IndexError, TypeError, ValueError) as error:
        raise ValueError("ACTIVE_ROUTE_ROW_INVALID") from error
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ValueError("ACTIVE_ROUTE_ROW_INVALID")
    return values


def _catalog_xyz(row: Mapping[str, Any]) -> tuple[float, float, float]:
    try:
        values = tuple(float(value) for value in row["xyz_m"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("INSTALLED_ROUTE_CATALOG_INVALID") from error
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ValueError("INSTALLED_ROUTE_CATALOG_INVALID")
    return values


def _fail(reason: str, *, offset: int | None = None) -> ConnectorTargetWindowDecision:
    return ConnectorTargetWindowDecision(
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
    )


def _baseline(
    reason: str,
    baseline_targets: tuple[Any, Any],
    *,
    release_latched: bool = False,
    offset: int | None = None,
) -> ConnectorTargetWindowDecision:
    return ConnectorTargetWindowDecision(
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
    )


def _coordinates_equivalent(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    return all(
        math.isclose(a, b, rel_tol=0.0, abs_tol=ROUTE_COORDINATE_EQUIVALENCE_M)
        for a, b in zip(_catalog_xyz(left), _catalog_xyz(right))
    )


def _semantically_equivalent(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    return bool(
        int(left["waypoint_id"]) == int(right["waypoint_id"])
        or _coordinates_equivalent(left, right)
    )


def _reconcile_active_suffix(
    catalog: Sequence[Mapping[str, Any]], active_route: Sequence[Any]
) -> int | None:
    offset = len(catalog) - len(active_route)
    if offset < 0:
        return None
    for expected, active in zip(catalog[offset:], active_route):
        try:
            index = int(expected["original_index"])
            expected_xyz = tuple(str(value) for value in expected["xyz_hex"])
            actual_xyz = tuple(value.hex() for value in _active_xyz(active))
            expected_option = (
                expected["road_option"].get("name"),
                expected["road_option"].get("value"),
            )
            actual_option = _option_token(active[1])
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return None
        if (
            index < offset
            or expected_xyz != actual_xyz
            or expected_option != actual_option
        ):
            return None
    return offset


def _connector_progress(
    connector: Sequence[Mapping[str, Any]],
) -> dict[int, float]:
    progress = {int(connector[0]["original_index"]): 0.0}
    cumulative = 0.0
    for left, right in zip(connector, connector[1:]):
        cumulative += math.dist(_catalog_xyz(left)[:2], _catalog_xyz(right)[:2])
        progress[int(right["original_index"])] = cumulative
    return progress


def _project_unique(
    point_xy: tuple[float, float], connector: Sequence[Mapping[str, Any]]
) -> tuple[float, float, int, int] | None:
    progress = _connector_progress(connector)
    candidates: list[tuple[float, float, int, int]] = []
    for left, right in zip(connector, connector[1:]):
        left_index = int(left["original_index"])
        right_index = int(right["original_index"])
        ax, ay = _catalog_xyz(left)[:2]
        bx, by = _catalog_xyz(right)[:2]
        vx, vy = bx - ax, by - ay
        length_sq = vx * vx + vy * vy
        ratio = (
            0.0
            if length_sq == 0.0
            else max(
                0.0,
                min(
                    1.0,
                    ((point_xy[0] - ax) * vx + (point_xy[1] - ay) * vy)
                    / length_sq,
                ),
            )
        )
        px, py = ax + ratio * vx, ay + ratio * vy
        distance_sq = (point_xy[0] - px) ** 2 + (point_xy[1] - py) ** 2
        route_progress = progress[left_index] + ratio * math.sqrt(length_sq)
        candidates.append((distance_sq, route_progress, left_index, right_index))
    if not candidates:
        return None
    minimum = min(row[0] for row in candidates)
    minima = [row for row in candidates if row[0] == minimum]
    if len({row[1] for row in minima}) != 1:
        return None
    chosen = min(minima, key=lambda row: (row[2], row[3]))
    return chosen[1], math.sqrt(chosen[0]), chosen[2], chosen[3]


def classify_bound_route_topology(
    installed_route_catalog: Sequence[Mapping[str, Any]],
    *,
    connector_start_index: int,
    connector_end_index: int,
    compatible_suffix_original_indices: Sequence[int],
    waypoint_id: int,
    road_id: int,
    section_id: int,
    lane_id: int,
    junction_id: int | None,
    is_junction: bool,
    entered_selected_connector: bool,
) -> tuple[str, bool, bool, bool]:
    """Classify only exact transaction-bound waypoint/lane topology identities."""

    catalog = tuple(installed_route_catalog)
    start, end = int(connector_start_index), int(connector_end_index)
    suffix_indices = tuple(int(value) for value in compatible_suffix_original_indices)
    if not 0 <= start < end < len(catalog):
        raise ValueError("SELECTED_CONNECTOR_INTERVAL_INVALID")

    def matches(row: Mapping[str, Any]) -> bool:
        bound_junction_id = row.get("junction_id")
        return bool(
            int(waypoint_id) == int(row["waypoint_id"])
            or (
                (int(road_id), int(section_id), int(lane_id))
                == (
                    int(row["road_id"]),
                    int(row["section_id"]),
                    int(row["lane_id"]),
                )
                and bool(is_junction) is bool(row["is_junction"])
                and junction_id
                == (
                    None
                    if bound_junction_id is None
                    else int(bound_junction_id)
                )
            )
        )

    membership = any(matches(row) for row in catalog[start:end])
    selected_exit = matches(catalog[end])
    downstream = any(matches(catalog[index]) for index in suffix_indices)
    before = any(matches(row) for row in catalog[:start])
    entered = bool(entered_selected_connector or membership)
    if membership:
        relation = "SELECTED_CONNECTOR"
    elif entered and selected_exit:
        relation = "COMPATIBLE_SELECTED_EXIT"
    elif entered and downstream:
        relation = "COMPATIBLE_DOWNSTREAM"
    elif not entered and before:
        relation = "BEFORE_CONNECTOR"
    else:
        relation = "INCOMPATIBLE"
    return relation, membership, selected_exit, downstream


def select_connector_target_window(
    active_route: Sequence[Any],
    installed_route_catalog: Sequence[Mapping[str, Any]],
    state: ConnectorTargetWindowState,
    *,
    connector_start_index: int,
    connector_end_index: int,
    compatible_suffix_original_indices: Sequence[int],
    ego_xyz_m: Sequence[float],
    baseline_targets: tuple[Any, Any],
) -> ConnectorTargetWindowDecision:
    """Apply frozen R1 without mutating route, phase, controller, or model ABI."""

    if state.method_phase != "SELECTED_ACTIVE_PRECOMMIT":
        return _baseline("OUTSIDE_SELECTED_ACTIVE_PRECOMMIT", baseline_targets)
    if state.planning_effective_k == 1:
        return _baseline("PLANNING_EFFECTIVE_K1_BASELINE_UNCHANGED", baseline_targets)
    if not state.selected_transaction_valid:
        return _fail("SELECTED_TRANSACTION_INVALID")
    if state.selected_transaction_identity != state.expected_transaction_identity:
        return _fail("SELECTED_TRANSACTION_STALE")
    if state.active_route_identity != state.installed_route_identity:
        return _fail("ACTIVE_ROUTE_IDENTITY_STALE")
    if state.active_route_generation != state.installed_route_generation:
        return _fail("ACTIVE_ROUTE_GENERATION_STALE")
    if state.current_destination_identity != state.original_destination_identity:
        return _fail("GLOBAL_DESTINATION_CHANGED")
    catalog = tuple(installed_route_catalog)
    route = tuple(active_route)
    if not catalog or not route:
        return _fail("ACTIVE_OR_INSTALLED_ROUTE_INVALID")
    if tuple(int(row.get("original_index", -1)) for row in catalog) != tuple(
        range(len(catalog))
    ):
        return _fail("INSTALLED_ROUTE_CATALOG_ORDER_INVALID")
    offset = _reconcile_active_suffix(catalog, route)
    if offset is None:
        return _fail("ACTIVE_ROUTE_SUFFIX_RECONCILIATION_FAILED")
    start, end = int(connector_start_index), int(connector_end_index)
    if not 0 <= start < end < len(catalog):
        return _fail("SELECTED_CONNECTOR_INTERVAL_INVALID", offset=offset)
    connector = catalog[start : end + 1]
    if any(row.get("topology_role") != "SELECTED_CONNECTOR" for row in connector):
        return _fail("SELECTED_CONNECTOR_CATALOG_INVALID", offset=offset)
    suffix_indices = tuple(int(index) for index in compatible_suffix_original_indices)
    expected_roles = tuple(
        "PREFIX"
        if index < start
        else "SELECTED_CONNECTOR"
        if index <= end
        else "SELECTED_SUFFIX"
        for index in range(len(catalog))
    )
    if tuple(row.get("topology_role") for row in catalog) != expected_roles:
        return _fail("INSTALLED_ROUTE_CATALOG_ROLE_PARTITION_INVALID", offset=offset)
    if suffix_indices != tuple(range(end + 1, len(catalog))):
        return _fail("SELECTED_SUFFIX_CATALOG_INVALID", offset=offset)
    if not math.isfinite(BRANCH_CORRIDOR_TOLERANCE_M) or BRANCH_CORRIDOR_TOLERANCE_M < 0.0:
        return _fail("BRANCH_CORRIDOR_TOLERANCE_INVALID", offset=offset)

    if state.release_latched:
        return _baseline(
            "AUTHORITATIVE_CONNECTOR_EXIT_RELEASE_LATCHED",
            baseline_targets,
            release_latched=True,
            offset=offset,
        )
    if state.committed:
        return _baseline(
            "COMMITTED_RELEASE", baseline_targets, release_latched=True, offset=offset
        )

    try:
        ego_xy = (float(ego_xyz_m[0]), float(ego_xyz_m[1]))
    except (IndexError, TypeError, ValueError):
        return _fail("ROUTE_PROJECTION_UNAVAILABLE", offset=offset)
    if not all(math.isfinite(value) for value in ego_xy):
        return _fail("ROUTE_PROJECTION_UNAVAILABLE", offset=offset)
    projection = _project_unique(ego_xy, connector)
    if state.topology_relation in {
        "COMPATIBLE_SELECTED_EXIT",
        "COMPATIBLE_DOWNSTREAM",
    }:
        installed_projection = _project_unique(ego_xy, catalog)
        installed_terminal_progress = _connector_progress(catalog)[end]
        exit_valid = bool(
            state.observed_selected_connector_membership is False
            and state.observed_topology_compatible_with_selected_exit_or_suffix is True
            and installed_projection is not None
            and installed_projection[0] >= installed_terminal_progress
            and installed_projection[1] <= BRANCH_CORRIDOR_TOLERANCE_M
        )
        if not exit_valid:
            return _fail("CONNECTOR_EXIT_EVIDENCE_INCOMPLETE", offset=offset)
        released = _baseline(
            "AUTHORITATIVE_CONNECTOR_EXIT_RELEASE",
            baseline_targets,
            release_latched=True,
            offset=offset,
        )
        return ConnectorTargetWindowDecision(
            **{
                **released.__dict__,
                "installed_route_progress_m": installed_projection[0],
                "installed_route_projection_distance_m": installed_projection[1],
                "installed_route_projection_segment_original_indices": (
                    installed_projection[2],
                    installed_projection[3],
                ),
            }
        )
    if state.topology_relation == "INCOMPATIBLE":
        return _fail("INCOMPATIBLE_EGO_TOPOLOGY", offset=offset)
    if state.topology_relation == "UNKNOWN":
        return _fail("CONNECTOR_TOPOLOGY_UNKNOWN", offset=offset)

    remaining_start = max(start, offset)
    if remaining_start > end:
        return _fail("NO_FORWARD_CONNECTOR_ROUTE_ROW", offset=offset)
    surviving_connector = connector[remaining_start - start :]
    progress = _connector_progress(connector)
    if state.topology_relation == "BEFORE_CONNECTOR":
        ego_progress = None
        projection_distance = None
        eligible = list(surviving_connector)
    elif state.topology_relation == "SELECTED_CONNECTOR":
        if projection is None:
            return _fail("ROUTE_PROJECTION_UNAVAILABLE_OR_AMBIGUOUS", offset=offset)
        ego_progress, projection_distance = projection[:2]
        if projection_distance > BRANCH_CORRIDOR_TOLERANCE_M:
            return _fail("ROUTE_PROJECTION_OUTSIDE_EXISTING_CORRIDOR", offset=offset)
        eligible = [
            row
            for row in surviving_connector
            if progress[int(row["original_index"])] > ego_progress
        ]
    else:
        return _fail("CONNECTOR_TOPOLOGY_UNKNOWN", offset=offset)
    if not eligible:
        return _fail("NO_FORWARD_CONNECTOR_ROUTE_ROW", offset=offset)

    tp1_catalog = eligible[0]
    terminal = connector[-1]
    fallback = False
    if (
        int(terminal["original_index"]) > int(tp1_catalog["original_index"])
        and not _semantically_equivalent(tp1_catalog, terminal)
    ):
        tp2_catalog = terminal
    else:
        fallback = True
        tp2_catalog = next(
            (
                catalog[index]
                for index in suffix_indices
                if index >= offset and not _semantically_equivalent(tp1_catalog, catalog[index])
            ),
            None,
        )
        if tp2_catalog is None:
            return _fail("ONLY_ONE_DISTINCT_FORWARD_ROUTE_ROW", offset=offset)

    tp1_index = int(tp1_catalog["original_index"])
    tp2_index = int(tp2_catalog["original_index"])
    if not tp1_index < tp2_index:
        return _fail("TARGET_ROUTE_ORDER_NONMONOTONIC", offset=offset)
    if _semantically_equivalent(tp1_catalog, tp2_catalog):
        return _fail("TARGET_SEMANTIC_WINDOW_DEGENERATE", offset=offset)
    tp1 = route[tp1_index - offset]
    tp2 = route[tp2_index - offset]
    return ConnectorTargetWindowDecision(
        mode="SPECIAL_WINDOW",
        reason=(
            "TERMINAL_CONNECTOR_PLUS_SUFFIX_FALLBACK"
            if fallback
            else "FORWARD_CONNECTOR_PLUS_SELECTED_EXIT_BOUNDARY"
        ),
        tp1=tp1,
        tp2=tp2,
        tp1_original_index=tp1_index,
        tp2_original_index=tp2_index,
        active_route_original_offset=offset,
        ego_connector_progress_m=ego_progress,
        ego_connector_projection_distance_m=projection_distance,
        tp1_connector_progress_m=progress[tp1_index],
        tp2_connector_progress_m=progress.get(tp2_index),
        installed_route_progress_m=None,
        installed_route_projection_distance_m=None,
        installed_route_projection_segment_original_indices=None,
        fallback_invoked=fallback,
        release_latched=False,
    )


__all__ = [
    "BRANCH_CORRIDOR_TOLERANCE_M",
    "CONTRACT_IDENTITY",
    "ConnectorTargetWindowDecision",
    "ConnectorTargetWindowState",
    "OWNER_IDENTITY",
    "ROUTE_COORDINATE_EQUIVALENCE_M",
    "classify_bound_route_topology",
    "select_connector_target_window",
]
