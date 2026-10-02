"""Failure-atomic dual-planner publication for the official PDM agent seam."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Set, Tuple

from .contracts import SealedPDMBranchPlan


class BranchTransactionError(RuntimeError):
    """Preparation, validation, lifecycle, or commit failed closed."""


class BranchLifecycle(Enum):
    PREPARED = "PREPARED"
    COMMITTED = "COMMITTED"
    FIRST_SELECTED_CONTROL_PENDING = "FIRST_SELECTED_CONTROL_PENDING"
    CONSUMED = "CONSUMED"


@dataclass(frozen=True)
class PreparedNativePlanner:
    """One fresh native planner plus independently computed state evidence."""

    planner: Any
    selected_route_hash: str
    command_route_hash: str
    destination_hash: str
    state_hash: str
    old_route_absent: bool
    native_route: Tuple[Any, ...]
    native_gps_route: Tuple[Any, ...]
    first_command_value: int


_ROAD_OPTION_VALUES = {
    "LEFT": 1,
    "RIGHT": 2,
    "STRAIGHT": 3,
    "LANEFOLLOW": 4,
    "CHANGELANELEFT": 5,
    "CHANGELANERIGHT": 6,
}


def _cache_state(first_command_value: int) -> Dict[str, Any]:
    return {
        "commands": deque((first_command_value, first_command_value), maxlen=2),
        "next_commands": deque((first_command_value, first_command_value), maxlen=2),
        "target_point_prev": (1.0e5, 1.0e5, 1.0e5),
        "remaining_route": None,
        "remaining_route_original": None,
        "aim_wp": None,
    }


class ObjectPlannerHost(object):
    """Explicitly bounded view of the planner-bearing official agent object.

    Publication uses one ``dict.update`` while the official agent thread is
    between control calls.  Official consumers run on that same thread; the
    lock also serializes DriveClarify writers.  No runtime object is retained.
    """

    _ROUTE_FIELDS = (
        "_waypoint_planner",
        "_command_planner",
        "commands",
        "next_commands",
        "target_point_prev",
        "remaining_route",
        "remaining_route_original",
        "aim_wp",
        "org_dense_route_world_coord",
        "org_dense_route_gps",
        "_global_plan_world_coord",
        "_global_plan",
    )

    def __init__(self, agent: Any) -> None:
        if not hasattr(agent, "__dict__"):
            raise TypeError("PLANNER_HOST_REQUIRES_OBJECT_DICT")
        self._agent = agent
        self._lock = threading.RLock()

    def planner_hashes(self, hash_planner: Callable[[Any], str]) -> Tuple[str, str]:
        return (
            hash_planner(self._agent.__dict__["_waypoint_planner"]),
            hash_planner(self._agent.__dict__["_command_planner"]),
        )

    def publish(
            self,
            waypoint_planner: Any,
            command_planner: Any,
            dense_native_route: Tuple[Any, ...],
            dense_native_gps_route: Tuple[Any, ...],
            command_native_route: Tuple[Any, ...],
            command_native_gps_route: Tuple[Any, ...],
            route_caches: Mapping[str, Any],
            validate_after: Callable[[], None]) -> None:
        with self._lock:
            snapshot = {
                field: self._agent.__dict__.get(field)
                for field in self._ROUTE_FIELDS
            }
            replacement = dict(route_caches)
            replacement.update({
                "_waypoint_planner": waypoint_planner,
                "_command_planner": command_planner,
                "org_dense_route_world_coord": dense_native_route,
                "org_dense_route_gps": dense_native_gps_route,
                "_global_plan_world_coord": command_native_route,
                "_global_plan": command_native_gps_route,
            })
            if set(replacement) != set(self._ROUTE_FIELDS):
                raise BranchTransactionError("ROUTE_DERIVED_FIELD_SET_MISMATCH")
            try:
                self._agent.__dict__.update(replacement)
                validate_after()
            except Exception:
                self._agent.__dict__.update(snapshot)
                raise


class PDMBranchAdapter(object):
    """Prepare both native planners, then publish them at the anchor boundary."""

    def __init__(
            self,
            host: ObjectPlannerHost,
            prepare_waypoint_planner: Callable[[SealedPDMBranchPlan], PreparedNativePlanner],
            prepare_command_planner: Callable[[SealedPDMBranchPlan], PreparedNativePlanner],
            hash_planner: Callable[[Any], str]) -> None:
        if not all(callable(value) for value in (
                prepare_waypoint_planner, prepare_command_planner, hash_planner)):
            raise TypeError("PLANNER_PREPARERS_MUST_BE_CALLABLE")
        self._host = host
        self._prepare_waypoint_planner = prepare_waypoint_planner
        self._prepare_command_planner = prepare_command_planner
        self._hash_planner = hash_planner
        self._committed_transactions = set()  # type: Set[str]
        self._active_transaction_id = None  # type: Optional[str]
        self._lifecycle = None  # type: Optional[BranchLifecycle]
        self._receipt = None  # type: Optional[Dict[str, Any]]
        self._lock = threading.RLock()

    @property
    def lifecycle(self) -> Optional[BranchLifecycle]:
        return self._lifecycle

    @property
    def receipt(self) -> Optional[Dict[str, Any]]:
        return None if self._receipt is None else dict(self._receipt)

    def install_branch(
            self,
            plan: SealedPDMBranchPlan,
            authoritative_anchor_frame: int) -> Dict[str, Any]:
        if not isinstance(plan, SealedPDMBranchPlan):
            raise BranchTransactionError("SEALED_BRANCH_PLAN_REQUIRED")
        if not isinstance(authoritative_anchor_frame, int) or authoritative_anchor_frame < 0:
            raise BranchTransactionError("AUTHORITATIVE_ANCHOR_FRAME_INVALID")
        with self._lock:
            if self._lifecycle in (
                    BranchLifecycle.PREPARED,
                    BranchLifecycle.COMMITTED,
                    BranchLifecycle.FIRST_SELECTED_CONTROL_PENDING):
                raise BranchTransactionError("PREVIOUS_TRANSACTION_NOT_CONSUMED")
            if plan.transaction_id in self._committed_transactions:
                raise BranchTransactionError("TRANSACTION_ALREADY_COMMITTED")
            pre_hashes = self._host.planner_hashes(self._hash_planner)
            try:
                waypoint = self._prepare_waypoint_planner(plan)
                self._validate_prepared(waypoint, plan, "WAYPOINT")
                command = self._prepare_command_planner(plan)
                self._validate_prepared(command, plan, "COMMAND")
                if waypoint.selected_route_hash != command.selected_route_hash:
                    raise BranchTransactionError("DUAL_PLANNER_ROUTE_DISAGREEMENT")
                if waypoint.destination_hash != command.destination_hash:
                    raise BranchTransactionError("DUAL_PLANNER_DESTINATION_DISAGREEMENT")
                if waypoint.first_command_value != command.first_command_value:
                    raise BranchTransactionError("DUAL_PLANNER_FIRST_COMMAND_DISAGREEMENT")
                expected_command_value = _ROAD_OPTION_VALUES[
                    plan.command_route[min(1, len(plan.command_route) - 1)].road_option
                ]
                if command.first_command_value != expected_command_value:
                    raise BranchTransactionError("FIRST_COMMAND_NOT_FROM_SEALED_ROUTE")
                caches = _cache_state(command.first_command_value)
                self._lifecycle = BranchLifecycle.PREPARED

                expected_hashes = (waypoint.state_hash, command.state_hash)

                def validate_after() -> None:
                    actual = self._host.planner_hashes(self._hash_planner)
                    if actual != expected_hashes:
                        raise BranchTransactionError("POST_COMMIT_PLANNER_HASH_MISMATCH")

                self._host.publish(
                    waypoint.planner,
                    command.planner,
                    waypoint.native_route,
                    waypoint.native_gps_route,
                    command.native_route,
                    command.native_gps_route,
                    caches,
                    validate_after,
                )
            except Exception as error:
                self._lifecycle = None
                if isinstance(error, BranchTransactionError):
                    raise
                raise BranchTransactionError(
                    "BRANCH_PREPARATION_OR_COMMIT_FAILED: " + type(error).__name__
                ) from error

            self._committed_transactions.add(plan.transaction_id)
            self._active_transaction_id = plan.transaction_id
            self._lifecycle = BranchLifecycle.COMMITTED
            post_hashes = self._host.planner_hashes(self._hash_planner)
            self._lifecycle = BranchLifecycle.FIRST_SELECTED_CONTROL_PENDING
            self._receipt = {
                "schema": "driveclarify.v3.pdm-branch-transaction-receipt.v1",
                "transaction_id": plan.transaction_id,
                "branch_plan_hash": plan.branch_plan_hash,
                "original_route_generation_identity": plan.original_route_generation_identity,
                "selected_route_generation_identity": plan.selected_route_generation_identity,
                "destination_hash": plan.destination_hash,
                "anchor_identity": plan.anchor_identity,
                "anchor_frame": authoritative_anchor_frame,
                "branch_generation": plan.branch_generation,
                "commit_state": self._lifecycle.value,
                "planner_pre_hashes": list(pre_hashes),
                "planner_post_hashes": list(post_hashes),
                "old_route_residue_absent": (
                    waypoint.old_route_absent and command.old_route_absent
                ),
                "first_selected_control_pending": True,
                "first_selected_control_consumed": False,
            }
            return dict(self._receipt)

    @staticmethod
    def _validate_prepared(
            prepared: PreparedNativePlanner,
            plan: SealedPDMBranchPlan,
            label: str) -> None:
        if not isinstance(prepared, PreparedNativePlanner):
            raise BranchTransactionError(label + "_PREPARATION_RESULT_INVALID")
        if prepared.selected_route_hash != plan.route_hash:
            raise BranchTransactionError(label + "_SELECTED_ROUTE_HASH_MISMATCH")
        if prepared.command_route_hash != plan.command_route_hash:
            raise BranchTransactionError(label + "_COMMAND_ROUTE_HASH_MISMATCH")
        if prepared.destination_hash != plan.destination_hash:
            raise BranchTransactionError(label + "_DESTINATION_HASH_MISMATCH")
        if not prepared.old_route_absent:
            raise BranchTransactionError(label + "_OLD_ROUTE_RESIDUE")
        if not isinstance(prepared.native_route, tuple) or not prepared.native_route:
            raise BranchTransactionError(label + "_NATIVE_ROUTE_INVALID")
        if not isinstance(prepared.native_gps_route, tuple) or not prepared.native_gps_route:
            raise BranchTransactionError(label + "_NATIVE_GPS_ROUTE_INVALID")
        if not isinstance(prepared.first_command_value, int):
            raise BranchTransactionError(label + "_FIRST_COMMAND_VALUE_INVALID")
        if not isinstance(prepared.state_hash, str) or len(prepared.state_hash) != 64:
            raise BranchTransactionError(label + "_STATE_HASH_INVALID")

    def mark_first_selected_control_consumed(self, control_payload: Mapping[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if self._lifecycle is not BranchLifecycle.FIRST_SELECTED_CONTROL_PENDING:
                raise BranchTransactionError("FIRST_SELECTED_CONTROL_NOT_PENDING")
            encoded = json.dumps(
                dict(control_payload), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            self._lifecycle = BranchLifecycle.CONSUMED
            assert self._receipt is not None
            self._receipt.update({
                "commit_state": self._lifecycle.value,
                "first_selected_control_pending": False,
                "first_selected_control_consumed": True,
                "first_selected_control_hash": hashlib.sha256(encoded).hexdigest(),
            })
            return dict(self._receipt)
