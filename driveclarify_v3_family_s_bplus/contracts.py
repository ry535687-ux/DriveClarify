"""Immutable, production-independent branch-plan contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Tuple


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROAD_OPTIONS = frozenset((
    "LEFT",
    "RIGHT",
    "STRAIGHT",
    "LANEFOLLOW",
    "CHANGELANELEFT",
    "CHANGELANERIGHT",
))


class BranchPlanError(ValueError):
    """A sealed plan is malformed or violates a frozen invariant."""


def _require_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise BranchPlanError(label + "_MUST_BE_SHA256")


def _float_hex(value: float) -> str:
    return float(value).hex()


@dataclass(frozen=True)
class RoutePoint:
    """One authoritative world-coordinate route row and native command token."""

    x: float
    y: float
    z: float
    road_option: str

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.z)
        if not all(isinstance(value, (int, float)) for value in values):
            raise BranchPlanError("ROUTE_POINT_COORDINATE_INVALID")
        if not all(math.isfinite(float(value)) for value in values):
            raise BranchPlanError("ROUTE_POINT_COORDINATE_INVALID")
        if self.road_option not in _ROAD_OPTIONS:
            raise BranchPlanError("ROAD_OPTION_INVALID")

    def canonical(self) -> Dict[str, Any]:
        return {
            "xyz_hex": [_float_hex(self.x), _float_hex(self.y), _float_hex(self.z)],
            "road_option": self.road_option,
        }


@dataclass(frozen=True)
class SealedPDMBranchPlan:
    """Minimum immutable data from which both native PDM planners are rebuilt.

    ``route`` is the already-authoritative selected remaining route.  The
    command planner consumes the indexed subset; the privileged waypoint
    planner consumes every row.  The adapter never creates or edits geometry.
    """

    transaction_id: str
    branch_generation: int
    anchor_identity: str
    original_route_generation_identity: str
    selected_route_generation_identity: str
    original_route_hash: str
    route: Tuple[RoutePoint, ...]
    command_route_indices: Tuple[int, ...]
    original_destination_xyz: Tuple[float, float, float]
    source_hashes: Tuple[str, ...]

    def __post_init__(self) -> None:
        for value, label in (
            (self.transaction_id, "TRANSACTION_ID"),
            (self.anchor_identity, "ANCHOR_IDENTITY"),
            (self.original_route_generation_identity, "ORIGINAL_ROUTE_GENERATION_IDENTITY"),
            (self.selected_route_generation_identity, "SELECTED_ROUTE_GENERATION_IDENTITY"),
            (self.original_route_hash, "ORIGINAL_ROUTE_HASH"),
        ):
            _require_sha256(value, label)
        for value in self.source_hashes:
            _require_sha256(value, "SOURCE_HASH")
        if not isinstance(self.branch_generation, int) or self.branch_generation < 1:
            raise BranchPlanError("BRANCH_GENERATION_INVALID")
        if not isinstance(self.route, tuple) or len(self.route) < 3:
            raise BranchPlanError("ROUTE_REQUIRES_AT_LEAST_THREE_POINTS")
        if not all(isinstance(point, RoutePoint) for point in self.route):
            raise BranchPlanError("ROUTE_POINT_INVALID")
        if not isinstance(self.command_route_indices, tuple) or len(self.command_route_indices) < 3:
            raise BranchPlanError("COMMAND_ROUTE_REQUIRES_AT_LEAST_THREE_POINTS")
        if tuple(sorted(set(self.command_route_indices))) != self.command_route_indices:
            raise BranchPlanError("COMMAND_ROUTE_INDICES_NOT_STRICTLY_INCREASING")
        if self.command_route_indices[0] != 0:
            raise BranchPlanError("COMMAND_ROUTE_MUST_START_AT_SELECTED_ROUTE_ORIGIN")
        if self.command_route_indices[-1] != len(self.route) - 1:
            raise BranchPlanError("COMMAND_ROUTE_MUST_END_AT_SELECTED_DESTINATION")
        if any(index < 0 or index >= len(self.route) for index in self.command_route_indices):
            raise BranchPlanError("COMMAND_ROUTE_INDEX_OUT_OF_RANGE")
        if len(self.original_destination_xyz) != 3 or not all(
                isinstance(value, (int, float)) and math.isfinite(float(value))
                for value in self.original_destination_xyz):
            raise BranchPlanError("ORIGINAL_DESTINATION_INVALID")
        selected_destination = self.route[-1]
        if any(
                _float_hex(actual) != _float_hex(expected)
                for actual, expected in zip(
                    (selected_destination.x, selected_destination.y, selected_destination.z),
                    self.original_destination_xyz,
                )):
            raise BranchPlanError("DESTINATION_MISMATCH")
        if self.route_hash == self.original_route_hash:
            raise BranchPlanError("SELECTED_ROUTE_NOT_DISTINCT_FROM_OLD_ROUTE")

    @property
    def command_route(self) -> Tuple[RoutePoint, ...]:
        return tuple(self.route[index] for index in self.command_route_indices)

    @property
    def route_hash(self) -> str:
        return hashlib.sha256(json.dumps(
            [point.canonical() for point in self.route],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

    @property
    def command_route_hash(self) -> str:
        return hashlib.sha256(json.dumps(
            [point.canonical() for point in self.command_route],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

    @property
    def destination_hash(self) -> str:
        return hashlib.sha256(json.dumps(
            [_float_hex(value) for value in self.original_destination_xyz],
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

    @property
    def branch_plan_hash(self) -> str:
        payload = {
            "schema": "driveclarify.v3.sealed-pdm-branch-plan.v1",
            "transaction_id": self.transaction_id,
            "branch_generation": self.branch_generation,
            "anchor_identity": self.anchor_identity,
            "original_route_generation_identity": self.original_route_generation_identity,
            "selected_route_generation_identity": self.selected_route_generation_identity,
            "original_route_hash": self.original_route_hash,
            "route": [point.canonical() for point in self.route],
            "command_route_indices": list(self.command_route_indices),
            "original_destination_xyz_hex": [
                _float_hex(value) for value in self.original_destination_xyz
            ],
            "source_hashes": list(self.source_hashes),
        }
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
