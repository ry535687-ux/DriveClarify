"""Exactly-once evidence wrapper around the existing PDM branch adapter."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from driveclarify_v3_family_s_bplus.adapter import PDMBranchAdapter
from driveclarify_v3_family_s_bplus.contracts import RoutePoint, SealedPDMBranchPlan


CANONICALIZATION_VERSION = "driveclarify.v3.route-point.float64hex-roadoption.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SingleBranchRouteSwitchError(RuntimeError):
    """A plan, route-domain, lifecycle, or evidence invariant failed closed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise SingleBranchRouteSwitchError(label + "_MUST_BE_SHA256")


def _require_hash_pair(value: Tuple[str, str], label: str) -> None:
    if not isinstance(value, tuple) or len(value) != 2:
        raise SingleBranchRouteSwitchError(label + "_MUST_BE_HASH_PAIR")
    for item in value:
        _require_hash(item, label)


def _require_route(value: Tuple[RoutePoint, ...], label: str) -> None:
    if not isinstance(value, tuple) or len(value) < 3:
        raise SingleBranchRouteSwitchError(label + "_REQUIRES_THREE_POINTS")
    if not all(isinstance(point, RoutePoint) for point in value):
        raise SingleBranchRouteSwitchError(label + "_POINT_INVALID")


def _route_rows(route: Tuple[RoutePoint, ...]) -> Tuple[Dict[str, Any], ...]:
    return tuple(point.canonical() for point in route)


def _route_hash(route: Tuple[RoutePoint, ...]) -> str:
    return _content_hash(list(_route_rows(route)))


def _plan_payload(plan: SealedPDMBranchPlan) -> Dict[str, Any]:
    return {
        "schema": "driveclarify.v3.sealed-pdm-branch-plan.v1",
        "transaction_id": plan.transaction_id,
        "branch_generation": plan.branch_generation,
        "anchor_identity": plan.anchor_identity,
        "original_route_generation_identity": plan.original_route_generation_identity,
        "selected_route_generation_identity": plan.selected_route_generation_identity,
        "original_route_hash": plan.original_route_hash,
        "route": list(_route_rows(plan.route)),
        "command_route_indices": list(plan.command_route_indices),
        "original_destination_xyz_hex": [
            float(value).hex() for value in plan.original_destination_xyz
        ],
        "source_hashes": list(plan.source_hashes),
    }


@dataclass(frozen=True)
class SealedSingleBranchRuntimePlan:
    """One plan plus independently sealed full-official-route provenance."""

    branch_plan: SealedPDMBranchPlan
    official_selected_full_route: Tuple[RoutePoint, ...]
    anchor_route_index: int
    expected_planner_pre_hashes: Tuple[str, str]
    expected_planner_post_hashes: Tuple[str, str]
    transaction_created_frame: int
    canonicalization_version: str = CANONICALIZATION_VERSION
    authorization_manifest_sha256: Optional[str] = None
    anchor_world_pose: Optional[Mapping[str, Any]] = None
    anchor_boundary_semantics: str = (
        "FIRST_SYNCHRONIZED_PRE_CONTROL_FRAME_AT_AUTHORIZED_ANCHOR"
    )
    anchor_inclusion_convention: str = "ANCHOR_INCLUDED_AS_REMAINING_ROUTE_INDEX_ZERO"

    def __post_init__(self) -> None:
        if not isinstance(self.branch_plan, SealedPDMBranchPlan):
            raise SingleBranchRouteSwitchError("SEALED_PDM_BRANCH_PLAN_REQUIRED")
        _require_route(self.official_selected_full_route, "OFFICIAL_SELECTED_FULL_ROUTE")
        _require_hash_pair(self.expected_planner_pre_hashes, "EXPECTED_PRE_HASHES")
        _require_hash_pair(self.expected_planner_post_hashes, "EXPECTED_POST_HASHES")
        if self.canonicalization_version != CANONICALIZATION_VERSION:
            raise SingleBranchRouteSwitchError("CANONICALIZATION_VERSION_MISMATCH")
        if not isinstance(self.anchor_route_index, int) or not (
                1 <= self.anchor_route_index < len(self.official_selected_full_route)):
            raise SingleBranchRouteSwitchError("ANCHOR_ROUTE_INDEX_INVALID")
        if not isinstance(self.transaction_created_frame, int) or self.transaction_created_frame < 0:
            raise SingleBranchRouteSwitchError("TRANSACTION_CREATED_FRAME_INVALID")
        if self.authorization_manifest_sha256 is not None:
            _require_hash(
                self.authorization_manifest_sha256,
                "AUTHORIZATION_MANIFEST_SHA256",
            )
        if self.anchor_world_pose is None:
            point = self.official_selected_full_route[self.anchor_route_index]
            object.__setattr__(self, "anchor_world_pose", {
                "x": point.x, "y": point.y, "z": point.z,
                "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
            })
        required_pose = {"x", "y", "z", "roll", "pitch", "yaw"}
        if (
                not isinstance(self.anchor_world_pose, Mapping)
                or set(self.anchor_world_pose) != required_pose
                or any(
                    not isinstance(self.anchor_world_pose[key], (int, float))
                    for key in required_pose
                )):
            raise SingleBranchRouteSwitchError("ANCHOR_WORLD_POSE_INVALID")
        if not isinstance(self.anchor_boundary_semantics, str) or not (
                self.anchor_boundary_semantics.strip()):
            raise SingleBranchRouteSwitchError("ANCHOR_BOUNDARY_SEMANTICS_INVALID")
        if not isinstance(self.anchor_inclusion_convention, str) or not (
                self.anchor_inclusion_convention.strip()):
            raise SingleBranchRouteSwitchError("ANCHOR_INCLUSION_CONVENTION_INVALID")
        official_remaining = self.official_selected_full_route[self.anchor_route_index:]
        if _route_rows(official_remaining) != _route_rows(self.branch_plan.route):
            raise SingleBranchRouteSwitchError("SELECTED_REMAINING_POINTWISE_MISMATCH")
        if self.official_selected_route_hash == self.official_selected_remaining_hash:
            raise SingleBranchRouteSwitchError("FULL_AND_REMAINING_ROUTE_DOMAINS_NOT_SEPARATED")

    @property
    def official_selected_route_hash(self) -> str:
        return _route_hash(self.official_selected_full_route)

    @property
    def official_selected_remaining_route(self) -> Tuple[RoutePoint, ...]:
        return self.official_selected_full_route[self.anchor_route_index:]

    @property
    def official_selected_remaining_hash(self) -> str:
        return _route_hash(self.official_selected_remaining_route)

    @property
    def pdm_selected_source_remaining_hash(self) -> str:
        return self.branch_plan.route_hash

    def sealed_plan_artifact(self) -> Dict[str, Any]:
        artifact = {
            "schema": "driveclarify.v3.sealed-single-branch-plan-artifact.v1",
            "canonicalization_version": self.canonicalization_version,
            "branch_plan": _plan_payload(self.branch_plan),
            "branch_plan_hash": self.branch_plan.branch_plan_hash,
            "official_selected_full_route": list(_route_rows(self.official_selected_full_route)),
            "official_selected_route_hash": self.official_selected_route_hash,
            "anchor_route_index": self.anchor_route_index,
            "anchor_identity": self.branch_plan.anchor_identity,
            "anchor_canonical_point": self.official_selected_full_route[
                self.anchor_route_index
            ].canonical(),
            "anchor_world_pose": dict(self.anchor_world_pose or {}),
            "anchor_boundary_semantics": self.anchor_boundary_semantics,
            "anchor_inclusion_convention": self.anchor_inclusion_convention,
            "official_selected_remaining_route": list(
                _route_rows(self.official_selected_remaining_route)
            ),
            "official_selected_remaining_hash": self.official_selected_remaining_hash,
            "pdm_selected_source_remaining_route": list(_route_rows(self.branch_plan.route)),
            "pdm_selected_source_remaining_hash": self.pdm_selected_source_remaining_hash,
        }
        return artifact


@dataclass(frozen=True)
class SingleBranchEvidenceBundle:
    """Immutable canonical JSON emitted by one consumed adapter transaction."""

    sealed_plan_artifact_json: bytes
    adapter_transaction_artifact_json: bytes
    wrapper_receipt_json: bytes

    def __post_init__(self) -> None:
        for value in (
                self.sealed_plan_artifact_json,
                self.adapter_transaction_artifact_json,
                self.wrapper_receipt_json):
            if not isinstance(value, bytes) or not value:
                raise SingleBranchRouteSwitchError("EVIDENCE_BUNDLE_REQUIRES_CANONICAL_BYTES")
            try:
                parsed = json.loads(value.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise SingleBranchRouteSwitchError("EVIDENCE_BUNDLE_JSON_INVALID") from error
            if _canonical_bytes(parsed) != value:
                raise SingleBranchRouteSwitchError("EVIDENCE_BUNDLE_NOT_CANONICAL")

    def artifacts(self) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        return tuple(
            json.loads(value.decode("utf-8"))
            for value in (
                self.sealed_plan_artifact_json,
                self.adapter_transaction_artifact_json,
                self.wrapper_receipt_json,
            )
        )  # type: ignore


class SingleBranchRouteSwitchWrapper(object):
    """Trigger one existing adapter and bind its receipt to exact route rows."""

    def __init__(
            self,
            adapter: PDMBranchAdapter,
            current_planner_hashes: Callable[[], Tuple[str, str]],
            installed_remaining_routes: Callable[
                [], Tuple[Tuple[RoutePoint, ...], Tuple[RoutePoint, ...]]
            ]) -> None:
        if not isinstance(adapter, PDMBranchAdapter):
            raise TypeError("EXISTING_PDM_BRANCH_ADAPTER_REQUIRED")
        if not callable(current_planner_hashes) or not callable(installed_remaining_routes):
            raise TypeError("READ_ONLY_EVIDENCE_SUPPLIERS_REQUIRED")
        self._adapter = adapter
        self._current_planner_hashes = current_planner_hashes
        self._installed_remaining_routes = installed_remaining_routes
        self._pending = None  # type: Optional[SealedSingleBranchRuntimePlan]
        self._active = None  # type: Optional[SealedSingleBranchRuntimePlan]
        self._install_receipt = None  # type: Optional[Dict[str, Any]]
        self._evidence_bundle = None  # type: Optional[SingleBranchEvidenceBundle]
        self._trigger_count = 0
        self._failed = False
        self._lock = threading.RLock()

    def queue_sealed_plan(self, sealed: SealedSingleBranchRuntimePlan) -> None:
        if not isinstance(sealed, SealedSingleBranchRuntimePlan):
            raise TypeError("SEALED_SINGLE_BRANCH_RUNTIME_PLAN_REQUIRED")
        with self._lock:
            if self._failed or self._pending is not None or self._active is not None:
                raise SingleBranchRouteSwitchError("TRANSACTION_ALREADY_PENDING_ACTIVE_OR_FAILED")
            self._pending = sealed

    def before_pdm_control(
            self,
            authoritative_frame: int,
            authoritative_anchor_identity: str) -> bool:
        if not isinstance(authoritative_frame, int) or authoritative_frame < 0:
            raise SingleBranchRouteSwitchError("AUTHORITATIVE_FRAME_INVALID")
        _require_hash(authoritative_anchor_identity, "AUTHORITATIVE_ANCHOR_IDENTITY")
        with self._lock:
            if self._failed:
                raise SingleBranchRouteSwitchError("WRAPPER_ALREADY_FAILED_CLOSED")
            if self._active is not None:
                if authoritative_anchor_identity == self._active.branch_plan.anchor_identity:
                    self._trigger_count += 1
                    self._failed = True
                    raise SingleBranchRouteSwitchError("ANCHOR_TRIGGERED_MORE_THAN_ONCE")
                return False
            sealed = self._pending
            if sealed is None or authoritative_anchor_identity != sealed.branch_plan.anchor_identity:
                return False
            if authoritative_frame < sealed.transaction_created_frame:
                self._failed = True
                raise SingleBranchRouteSwitchError("ANCHOR_PRECEDES_TRANSACTION_CREATION")
            actual_pre = self._current_planner_hashes()
            _require_hash_pair(actual_pre, "ACTUAL_PRE_HASHES")
            if actual_pre != sealed.expected_planner_pre_hashes:
                self._failed = True
                raise SingleBranchRouteSwitchError("SEALED_PRE_HASH_MISMATCH")
            receipt = self._adapter.install_branch(
                sealed.branch_plan, authoritative_anchor_frame=authoritative_frame
            )
            if (
                    receipt.get("transaction_id") != sealed.branch_plan.transaction_id
                    or receipt.get("branch_plan_hash") != sealed.branch_plan.branch_plan_hash
                    or tuple(receipt.get("planner_pre_hashes", ()))
                    != sealed.expected_planner_pre_hashes
                    or tuple(receipt.get("planner_post_hashes", ()))
                    != sealed.expected_planner_post_hashes
                    or receipt.get("old_route_residue_absent") is not True):
                self._failed = True
                raise SingleBranchRouteSwitchError("ADAPTER_INSTALL_RECEIPT_MISMATCH")
            self._trigger_count += 1
            if self._trigger_count != 1:
                self._failed = True
                raise SingleBranchRouteSwitchError("TRANSACTION_NOT_EXACTLY_ONCE")
            self._pending = None
            self._active = sealed
            self._install_receipt = dict(receipt)
            return True

    def after_pdm_control(
            self,
            authoritative_frame: int,
            control_snapshot: Mapping[str, Any]) -> SingleBranchEvidenceBundle:
        if not isinstance(authoritative_frame, int) or authoritative_frame < 0:
            raise SingleBranchRouteSwitchError("AUTHORITATIVE_FRAME_INVALID")
        if not isinstance(control_snapshot, Mapping):
            raise TypeError("CONTROL_SNAPSHOT_MAPPING_REQUIRED")
        with self._lock:
            if self._failed or self._active is None or self._install_receipt is None:
                raise SingleBranchRouteSwitchError("ANCHOR_NOT_AVAILABLE_FOR_CONSUMPTION")
            consumed_frame = self._install_receipt.get("anchor_frame")
            if authoritative_frame != consumed_frame:
                self._failed = True
                raise SingleBranchRouteSwitchError("FIRST_SELECTED_CONTROL_FRAME_MISMATCH")
            raw_receipt = self._adapter.mark_first_selected_control_consumed(control_snapshot)
            if raw_receipt.get("first_selected_control_consumed") is not True:
                self._failed = True
                raise SingleBranchRouteSwitchError("FIRST_SELECTED_CONTROL_NOT_CONSUMED")
            installed = self._installed_remaining_routes()
            if not isinstance(installed, tuple) or len(installed) != 2:
                self._failed = True
                raise SingleBranchRouteSwitchError("TWO_INSTALLED_ROUTE_PROJECTIONS_REQUIRED")
            waypoint_route, command_route = installed
            _require_route(waypoint_route, "WAYPOINT_INSTALLED_REMAINING_ROUTE")
            _require_route(command_route, "COMMAND_INSTALLED_REMAINING_ROUTE")
            sealed = self._active
            source_rows = _route_rows(sealed.branch_plan.route)
            if _route_rows(waypoint_route) != source_rows:
                self._failed = True
                raise SingleBranchRouteSwitchError("WAYPOINT_INSTALLED_REMAINING_MISMATCH")
            if _route_rows(command_route) != source_rows:
                self._failed = True
                raise SingleBranchRouteSwitchError("COMMAND_INSTALLED_REMAINING_MISMATCH")

            plan_artifact = sealed.sealed_plan_artifact()
            raw_receipt_hash = _content_hash(raw_receipt)
            adapter_artifact = {
                "schema": "driveclarify.v3.pdm-adapter-transaction-artifact.v1",
                "canonicalization_version": CANONICALIZATION_VERSION,
                "sealed_plan_artifact_sha256": _content_hash(plan_artifact),
                "authorized_manifest_sha256": sealed.authorization_manifest_sha256,
                "branch_plan_hash": sealed.branch_plan.branch_plan_hash,
                "transaction_id": sealed.branch_plan.transaction_id,
                "anchor_identity": sealed.branch_plan.anchor_identity,
                "anchor_route_index": sealed.anchor_route_index,
                "anchor_canonical_point": sealed.official_selected_full_route[
                    sealed.anchor_route_index
                ].canonical(),
                "anchor_world_pose": dict(sealed.anchor_world_pose or {}),
                "anchor_boundary_semantics": sealed.anchor_boundary_semantics,
                "anchor_inclusion_convention": sealed.anchor_inclusion_convention,
                "transaction_created_frame": sealed.transaction_created_frame,
                "transaction_consumed_frame": consumed_frame,
                "first_selected_pdm_control_frame": authoritative_frame,
                "pdm_selected_source_remaining_route": list(source_rows),
                "pdm_selected_source_remaining_hash": sealed.pdm_selected_source_remaining_hash,
                "waypoint_installed_selected_remaining_route": list(
                    _route_rows(waypoint_route)
                ),
                "waypoint_installed_selected_remaining_hash": _route_hash(waypoint_route),
                "command_installed_source_projection_remaining_route": list(
                    _route_rows(command_route)
                ),
                "command_installed_source_projection_remaining_hash": _route_hash(
                    command_route
                ),
                # Legacy names remain as byte-identical aliases for historical
                # readers.  Layered validation treats the source-projection
                # names above as authoritative and validates the sparse native
                # command domain separately.
                "command_installed_selected_remaining_route": list(
                    _route_rows(command_route)
                ),
                "command_installed_selected_remaining_hash": _route_hash(command_route),
                "command_native_sparse_route": list(
                    _route_rows(sealed.branch_plan.command_route)
                ),
                "command_native_sparse_route_hash": sealed.branch_plan.command_route_hash,
                "waypoint_planner_pre_hash": sealed.expected_planner_pre_hashes[0],
                "command_planner_pre_hash": sealed.expected_planner_pre_hashes[1],
                "waypoint_planner_post_hash": sealed.expected_planner_post_hashes[0],
                "command_planner_post_hash": sealed.expected_planner_post_hashes[1],
                "exactly_once_count": self._trigger_count,
                "failure_state": "NONE",
                "raw_adapter_receipt": raw_receipt,
                "raw_adapter_receipt_sha256": raw_receipt_hash,
            }
            wrapper_receipt = {
                "schema": "driveclarify.v3.single-branch-wrapper-receipt.v1",
                "canonicalization_version": CANONICALIZATION_VERSION,
                "sealed_plan_artifact_sha256": _content_hash(plan_artifact),
                "authorized_manifest_sha256": sealed.authorization_manifest_sha256,
                "adapter_transaction_artifact_sha256": _content_hash(adapter_artifact),
                "raw_adapter_receipt_sha256": raw_receipt_hash,
                "branch_plan_hash": sealed.branch_plan.branch_plan_hash,
                "transaction_id": sealed.branch_plan.transaction_id,
                "anchor_identity": sealed.branch_plan.anchor_identity,
                "anchor_route_index": sealed.anchor_route_index,
                "anchor_canonical_point": sealed.official_selected_full_route[
                    sealed.anchor_route_index
                ].canonical(),
                "anchor_world_pose": dict(sealed.anchor_world_pose or {}),
                "anchor_boundary_semantics": sealed.anchor_boundary_semantics,
                "anchor_inclusion_convention": sealed.anchor_inclusion_convention,
                "transaction_created_frame": sealed.transaction_created_frame,
                "transaction_consumed_frame": consumed_frame,
                "first_selected_pdm_control_frame": authoritative_frame,
                "official_selected_route_hash": sealed.official_selected_route_hash,
                "official_selected_remaining_hash": sealed.official_selected_remaining_hash,
                "pdm_selected_source_remaining_hash": sealed.pdm_selected_source_remaining_hash,
                "pdm_selected_source_remaining_route": list(source_rows),
                "pdm_installed_selected_remaining_hash": _route_hash(waypoint_route),
                "waypoint_planner_pre_hash": sealed.expected_planner_pre_hashes[0],
                "command_planner_pre_hash": sealed.expected_planner_pre_hashes[1],
                "waypoint_planner_post_hash": sealed.expected_planner_post_hashes[0],
                "command_planner_post_hash": sealed.expected_planner_post_hashes[1],
                "route_switch_active_start": 0,
                "route_switch_active_end": 15,
                "exactly_once_count": self._trigger_count,
                "failure_state": "NONE",
            }
            bundle = SingleBranchEvidenceBundle(
                sealed_plan_artifact_json=_canonical_bytes(plan_artifact),
                adapter_transaction_artifact_json=_canonical_bytes(adapter_artifact),
                wrapper_receipt_json=_canonical_bytes(wrapper_receipt),
            )
            self._evidence_bundle = bundle
            return bundle

    def route_switch_active(self, outer_forward_index: int) -> bool:
        if not isinstance(outer_forward_index, int):
            raise TypeError("OUTER_FORWARD_INDEX_MUST_BE_INT")
        with self._lock:
            return bool(
                not self._failed
                and self._evidence_bundle is not None
                and 0 <= outer_forward_index <= 15
            )

    def incomplete_evidence(self) -> Mapping[str, Any]:
        with self._lock:
            return MappingProxyType({
                "schema": "driveclarify.v3.single-branch-wrapper-incomplete.v1",
                "transaction_id": (
                    self._pending.branch_plan.transaction_id
                    if self._pending is not None else None
                ),
                "trigger_count": self._trigger_count,
                "eligible_candidate": False,
                "status": "FAILED_CLOSED" if self._failed else "INCOMPLETE",
            })
