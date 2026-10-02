"""Concrete CPU-testable task-conditioned global+local detached planner.

The graph and required branch binding are supplied by legitimate runtime map and
semantic-grounding owners.  This module performs the actual constrained search;
it accepts no oracle bucket, future outcome, or expected decision.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
from typing import Mapping, Sequence

from .baselines import (
    EgoPlanningSnapshot,
    FullGlobalPlan,
    UpdatedObligation,
)
from .canonical import canonical_sha256
from .g_binding_v2 import DestinationTerminalRegion
from .models import GlobalTask, LocalNavigationCondition, RouteRow


@dataclass(frozen=True)
class TopologyEdge:
    edge_identity: str
    source_node_identity: str
    target_node_identity: str
    route_rows: tuple[RouteRow, ...]
    cost_m: float

    def __post_init__(self) -> None:
        if not all(
            str(value).strip()
            for value in (
                self.edge_identity,
                self.source_node_identity,
                self.target_node_identity,
            )
        ):
            raise ValueError("T_B2_TOPOLOGY_EDGE_IDENTITY_INVALID")
        if not self.route_rows or not math.isfinite(self.cost_m) or self.cost_m < 0.0:
            raise ValueError("T_B2_TOPOLOGY_EDGE_CONTENT_INVALID")


class TopologyGraphTaskConditionedGlobalPlanner:
    """Shortest legal current-ego→required-branch→G path over a frozen graph."""

    def __init__(
        self,
        *,
        edges: Sequence[TopologyEdge],
        obligation_branch_edges: Mapping[str, Sequence[str]],
        global_task_destination_nodes: Mapping[str, str],
        global_task_terminal_regions: Mapping[str, DestinationTerminalRegion],
        source_graph_sha256: str,
    ) -> None:
        if len(source_graph_sha256) != 64:
            raise ValueError("T_B2_SOURCE_GRAPH_HASH_INVALID")
        self._edges = {edge.edge_identity: edge for edge in edges}
        if len(self._edges) != len(tuple(edges)):
            raise ValueError("T_B2_TOPOLOGY_EDGE_IDENTITY_DUPLICATE")
        self._outgoing: dict[str, list[TopologyEdge]] = {}
        for edge in edges:
            self._outgoing.setdefault(edge.source_node_identity, []).append(edge)
        self._obligation_edges = {
            identity: tuple(edge_ids)
            for identity, edge_ids in obligation_branch_edges.items()
        }
        self._destination_nodes = dict(global_task_destination_nodes)
        self._terminal_regions = dict(global_task_terminal_regions)
        self._source_graph_sha256 = source_graph_sha256

    def trace_task_conditioned_route(
        self,
        ego: EgoPlanningSnapshot,
        updated_obligation: UpdatedObligation,
        global_task: GlobalTask,
    ) -> FullGlobalPlan:
        start = ego.current_topology_node_identity
        if not start:
            raise RuntimeError("T_B2_CURRENT_EGO_TOPOLOGY_NODE_MISSING")
        destination = self._destination_nodes.get(global_task.identity)
        if not destination:
            raise RuntimeError("T_B2_GLOBAL_TASK_DESTINATION_NODE_MISSING")
        terminal_region = self._terminal_regions.get(global_task.identity)
        if terminal_region is None:
            raise RuntimeError("T_B2_GLOBAL_TASK_TERMINAL_REGION_MISSING")
        required_ids = self._obligation_edges.get(
            updated_obligation.branch_or_connector_identity
        )
        if not required_ids:
            raise RuntimeError("T_B2_UPDATED_BRANCH_GRAPH_BINDING_MISSING")
        try:
            required = tuple(self._edges[edge_id] for edge_id in required_ids)
        except KeyError as error:
            raise RuntimeError("T_B2_UPDATED_BRANCH_EDGE_MISSING") from error
        if any(
            first.target_node_identity != second.source_node_identity
            for first, second in zip(required, required[1:])
        ):
            raise RuntimeError("T_B2_UPDATED_BRANCH_EDGE_SEQUENCE_DISCONNECTED")
        prefix = self._shortest_edges(start, required[0].source_node_identity)
        suffix = self._shortest_edges(required[-1].target_node_identity, destination)
        selected_edges = (*prefix, *required, *suffix)
        route_rows = _join_edge_rows(selected_edges)
        branch_rows = _join_edge_rows(required)
        terminal = route_rows[-1]
        terminal_equivalence = terminal_region.assert_equivalent(
            global_task=global_task,
            planner_terminal=terminal,
        )
        route_identity = "t-b2-full-route-" + canonical_sha256(
            {"route_rows": route_rows, "global_task": global_task.identity}
        )[:24]
        proof = canonical_sha256(
            {
                "updated_obligation_identity": updated_obligation.identity,
                "updated_branch_identity": updated_obligation.branch_or_connector_identity,
                "updated_branch_route_rows": branch_rows,
                "full_route_identity": route_identity,
                "global_task_identity": global_task.identity,
            }
        )
        return FullGlobalPlan(
            route_identity=route_identity,
            route_rows=route_rows,
            active_suffix=route_rows,
            updated_branch_identity=updated_obligation.branch_or_connector_identity,
            updated_branch_route_rows=branch_rows,
            updated_branch_proof_sha256=proof,
            planner_receipt_sha256=canonical_sha256(
                {
                    "source_graph_sha256": self._source_graph_sha256,
                    "origin_node": start,
                    "destination_node": destination,
                    "selected_edge_identities": tuple(
                        edge.edge_identity for edge in selected_edges
                    ),
                    "updated_obligation_identity": updated_obligation.identity,
                    "terminal_region_certificate_sha256": (
                        terminal_region.certificate_sha256
                    ),
                    "terminal_region_equivalence_sha256": terminal_equivalence,
                }
            ),
            destination_terminal_region=terminal_region,
            destination_terminal_equivalence_sha256=terminal_equivalence,
        )

    def _shortest_edges(
        self, start: str, destination: str
    ) -> tuple[TopologyEdge, ...]:
        if start == destination:
            return ()
        queue: list[tuple[float, str, tuple[str, ...]]] = [(0.0, start, ())]
        best = {start: 0.0}
        while queue:
            cost, node, path = heapq.heappop(queue)
            if cost != best.get(node):
                continue
            if node == destination:
                return tuple(self._edges[edge_id] for edge_id in path)
            for edge in sorted(
                self._outgoing.get(node, ()), key=lambda item: item.edge_identity
            ):
                new_cost = cost + edge.cost_m
                if new_cost < best.get(edge.target_node_identity, math.inf):
                    best[edge.target_node_identity] = new_cost
                    heapq.heappush(
                        queue,
                        (
                            new_cost,
                            edge.target_node_identity,
                            (*path, edge.edge_identity),
                        ),
                    )
        raise RuntimeError("T_B2_TASK_CONDITIONED_ROUTE_NOT_FOUND")


def _join_edge_rows(edges: Sequence[TopologyEdge]) -> tuple[RouteRow, ...]:
    joined: list[RouteRow] = []
    for edge in edges:
        joined.extend(edge.route_rows)
    if not joined:
        raise RuntimeError("T_B2_ROUTE_ROWS_EMPTY")
    return tuple(joined)


class ExactBranchLocalNavigationPreparer:
    """Prepare the exact selected branch condition without a VLA forward."""

    def prepare_local_navigation(
        self,
        global_plan: FullGlobalPlan,
        ego: EgoPlanningSnapshot,
        updated_obligation: UpdatedObligation,
    ) -> LocalNavigationCondition:
        if global_plan.updated_branch_identity != updated_obligation.branch_or_connector_identity:
            raise RuntimeError("T_B2_LOCAL_PREPARER_BRANCH_IDENTITY_MISMATCH")
        rows = global_plan.updated_branch_route_rows
        if len(rows) < 2:
            raise RuntimeError("T_B2_LOCAL_PREPARER_BRANCH_ROUTE_TOO_SHORT")
        yaw = math.radians(float(ego.pose_xyz_yaw[3]))
        cosine = math.cos(yaw)
        sine = math.sin(yaw)
        ego_x, ego_y = ego.pose_xyz_yaw[:2]
        targets: list[tuple[float, float]] = []
        for row in rows[:2]:
            dx = row.x_m - ego_x
            dy = row.y_m - ego_y
            targets.append((cosine * dx + sine * dy, -sine * dx + cosine * dy))
        return LocalNavigationCondition(
            branch_or_connector_identity=global_plan.updated_branch_identity,
            route_rows=rows,
            target_points_ego_local_xy_m=tuple(targets),
            first_road_option=rows[0].road_option,
            preparation_receipt_sha256=canonical_sha256(
                {
                    "full_route_identity": global_plan.route_identity,
                    "updated_obligation_identity": updated_obligation.identity,
                    "branch_route_rows": rows,
                    "ego_source_sha256": ego.source_sha256,
                    "target_points_ego_local_xy_m": tuple(targets),
                    "model_forward_count": 0,
                }
            ),
        )
