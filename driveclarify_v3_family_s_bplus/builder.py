"""Offline-only construction of immutable branch-plan schemas."""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

from .contracts import RoutePoint, SealedPDMBranchPlan


class SealedPDMBranchPlanBuilder(object):
    """Seal precomputed route semantics without invoking a map or planner."""

    @staticmethod
    def build(
            route_rows: Iterable[Tuple[Sequence[float], str]],
            command_route_indices: Iterable[int],
            transaction_id: str,
            branch_generation: int,
            anchor_identity: str,
            original_route_generation_identity: str,
            selected_route_generation_identity: str,
            original_route_hash: str,
            original_destination_xyz: Sequence[float],
            source_hashes: Iterable[str]) -> SealedPDMBranchPlan:
        route = tuple(
            RoutePoint(float(xyz[0]), float(xyz[1]), float(xyz[2]), road_option)
            for xyz, road_option in route_rows
        )
        return SealedPDMBranchPlan(
            transaction_id=transaction_id,
            branch_generation=branch_generation,
            anchor_identity=anchor_identity,
            original_route_generation_identity=original_route_generation_identity,
            selected_route_generation_identity=selected_route_generation_identity,
            original_route_hash=original_route_hash,
            route=route,
            command_route_indices=tuple(command_route_indices),
            original_destination_xyz=tuple(float(value) for value in original_destination_xyz),
            source_hashes=tuple(source_hashes),
        )
