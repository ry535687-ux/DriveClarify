"""Layered, caller-selected evidence artifacts for Family-S supervision.

This module only canonicalizes and records facts already produced by their
runtime owners.  It has no simulator, model, planner, controller, subprocess,
or retry API.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


AUTH_SCHEMA = "driveclarify.v3.trusted-branch-authorization-manifest.v1"
OBSERVATION_SCHEMA = "driveclarify.v3.synchronized-observation-expert-receipt.v1"
EVALUATOR_SCHEMA = "driveclarify.v3.evaluator-result-receipt.v1"
CLEANUP_SCHEMA = "driveclarify.v3.cleanup-process-receipt.v1"
RUN_SCHEMA = "driveclarify.v3.layered-run-manifest.v1"
FROZEN_TICKS = tuple(range(0, 100, 5))
EXPERT_OWNER = "OFFICIAL_PDM_LITE_AUTOPILOT_DATAAGENT_REALIZED_ROLLOUT"


class EvidenceContractError(RuntimeError):
    """A layered evidence artifact is malformed or internally inconsistent."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_copy(value: Any) -> Any:
    return json.loads(canonical_bytes(value).decode("utf-8"))


def _passive(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (list, tuple)):
        return all(_passive(item) for item in value)
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str) and _passive(item)
            for key, item in value.items()
        )
    return False


def seal_envelope(schema: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(schema, str) or not schema:
        raise EvidenceContractError("ENVELOPE_SCHEMA_REQUIRED")
    if not isinstance(payload, Mapping) or not _passive(payload):
        raise EvidenceContractError("PASSIVE_JSON_PAYLOAD_REQUIRED")
    detached = json_copy(payload)
    return {
        "schema": schema,
        "payload": detached,
        "payload_sha256": content_hash(detached),
    }


def verify_envelope(value: Mapping[str, Any], schema: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
            "schema", "payload", "payload_sha256"}:
        raise EvidenceContractError("ENVELOPE_FIELD_SET_INVALID")
    if value.get("schema") != schema:
        raise EvidenceContractError("ENVELOPE_SCHEMA_INVALID")
    payload = value.get("payload")
    if not isinstance(payload, Mapping) or not _passive(payload):
        raise EvidenceContractError("ENVELOPE_PAYLOAD_INVALID")
    if value.get("payload_sha256") != content_hash(payload):
        raise EvidenceContractError("ENVELOPE_PAYLOAD_HASH_MISMATCH")
    return payload


def load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceContractError("TRUSTED_ARTIFACT_READ_FAILED") from error
    if not isinstance(value, dict):
        raise EvidenceContractError("TRUSTED_ARTIFACT_OBJECT_REQUIRED")
    return value


@dataclass(frozen=True)
class TrustedValidationContext:
    """Explicit external roots; candidate JSON is never allowed to select these."""

    authorization_manifest_path: Path
    trusted_sealed_plan_path: Path
    adapter_transaction_path: Path
    wrapper_receipt_path: Path
    observation_expert_receipt_path: Path
    official_result_artifact_path: Path
    evaluator_result_receipt_path: Path
    process_receipt_path: Path
    cleanup_receipt_path: Path
    run_manifest_path: Path

    def paths(self) -> Tuple[Path, ...]:
        return tuple(Path(value).resolve() for value in (
            self.authorization_manifest_path,
            self.trusted_sealed_plan_path,
            self.adapter_transaction_path,
            self.wrapper_receipt_path,
            self.observation_expert_receipt_path,
            self.official_result_artifact_path,
            self.evaluator_result_receipt_path,
            self.process_receipt_path,
            self.cleanup_receipt_path,
            self.run_manifest_path,
        ))

    def load(self) -> Dict[str, Dict[str, Any]]:
        names = (
            "authorization_manifest", "trusted_sealed_plan",
            "adapter_transaction", "wrapper_receipt",
            "observation_expert_receipt", "official_result_artifact",
            "evaluator_result_receipt", "process_receipt",
            "cleanup_receipt", "run_manifest",
        )
        return {
            name: load_json(path)
            for name, path in zip(names, self.paths())
        }


@dataclass(frozen=True)
class LayeredEvidenceBundle:
    """Canonical candidate copies of independently selected owner artifacts."""

    sealed_plan_artifact_json: bytes
    adapter_transaction_artifact_json: bytes
    wrapper_receipt_json: bytes
    observation_expert_receipt_json: bytes
    evaluator_result_receipt_json: bytes
    cleanup_receipt_json: bytes
    run_manifest_json: bytes

    FILES = (
        "sealed_plan.json",
        "adapter_transaction.json",
        "wrapper_receipt.json",
        "observation_expert_receipt.json",
        "evaluator_result_receipt.json",
        "cleanup_receipt.json",
        "run_manifest.json",
    )

    def __post_init__(self) -> None:
        for value in self._bytes():
            if not isinstance(value, bytes) or not value:
                raise EvidenceContractError("LAYERED_BUNDLE_CANONICAL_BYTES_REQUIRED")
            try:
                parsed = json.loads(value.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise EvidenceContractError("LAYERED_BUNDLE_JSON_INVALID") from error
            if canonical_bytes(parsed) != value:
                raise EvidenceContractError("LAYERED_BUNDLE_NOT_CANONICAL")

    def _bytes(self) -> Tuple[bytes, ...]:
        return (
            self.sealed_plan_artifact_json,
            self.adapter_transaction_artifact_json,
            self.wrapper_receipt_json,
            self.observation_expert_receipt_json,
            self.evaluator_result_receipt_json,
            self.cleanup_receipt_json,
            self.run_manifest_json,
        )

    def artifacts(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: json.loads(value.decode("utf-8"))
            for name, value in zip(self.FILES, self._bytes())
        }


def build_authorization_manifest(
        *, episode_id: str, record_id: str,
        sealed_plan_artifact_sha256: str,
        canonicalization_version: str,
        provenance: Mapping[str, Any], route_identity: Mapping[str, Any],
        anchor_identity: str, anchor_route_index: int,
        anchor_canonical_point: Mapping[str, Any],
        anchor_world_pose: Mapping[str, Any],
        anchor_boundary_semantics: str,
        anchor_inclusion_convention: str,
        old_route_identity: str, selected_route_identity: str,
        selected_source_remaining_route_identity: str,
        official_selected_route_identity: str,
        expected_runtime_owners: Mapping[str, str]) -> Dict[str, Any]:
    payload = {
        "protocol_version": "A1_ENGINEERING_CAPABILITY_CORPUS_V1",
        "episode_id": episode_id,
        "record_id": record_id,
        "sealed_plan_artifact_sha256": sealed_plan_artifact_sha256,
        "canonicalization_version": canonicalization_version,
        "sampling_ticks": list(FROZEN_TICKS),
        "route_switch_active_start": 0,
        "route_switch_active_end": 15,
        "provenance": json_copy(provenance),
        "route_identity": json_copy(route_identity),
        "anchor_identity": anchor_identity,
        "anchor_route_index": anchor_route_index,
        "anchor_canonical_point": json_copy(anchor_canonical_point),
        "anchor_world_pose": json_copy(anchor_world_pose),
        "anchor_boundary_semantics": anchor_boundary_semantics,
        "anchor_inclusion_convention": anchor_inclusion_convention,
        "old_route_identity": old_route_identity,
        "selected_route_identity": selected_route_identity,
        "selected_source_remaining_route_identity": (
            selected_source_remaining_route_identity
        ),
        "official_selected_route_identity": official_selected_route_identity,
        "expected_runtime_owners": json_copy(expected_runtime_owners),
    }
    return seal_envelope(AUTH_SCHEMA, payload)


def build_observation_expert_receipt(
        *, episode_id: str, record_id: str,
        authorization_manifest_sha256: str,
        sealed_plan_artifact_sha256: str,
        adapter_transaction_artifact_sha256: str,
        wrapper_receipt_sha256: str,
        anchor_identity: str, anchor_route_index: int,
        anchor_canonical_point: Mapping[str, Any],
        anchor_world_pose: Mapping[str, Any],
        anchor_boundary_semantics: str,
        anchor_inclusion_convention: str,
        source_frame: int, sensor_payloads: Mapping[str, Any],
        rgb_source_hashes: Sequence[str], sensor_source_hashes: Mapping[str, str],
        ego_state: Mapping[str, Any], navigation_targets: Sequence[Any],
        expert_route_sha256: str, expert_future_trajectory: Sequence[Any],
        expert_speed_targets: Sequence[float],
        expert_control_targets: Sequence[Mapping[str, Any]],
        expert_owner_identity: str = EXPERT_OWNER) -> Dict[str, Any]:
    trajectories = json_copy(expert_future_trajectory)
    speeds = json_copy(expert_speed_targets)
    controls = json_copy(expert_control_targets)
    ticks = [row.get("relative_tick") for row in trajectories]
    frames = [row.get("official_frame") for row in trajectories]
    if (
            ticks != list(FROZEN_TICKS)
            or frames != [source_frame + tick for tick in FROZEN_TICKS]
            or len(speeds) != 20 or len(controls) != 20):
        raise EvidenceContractError("FROZEN_EXPERT_TICKS_REQUIRED")
    navigation = json_copy(navigation_targets)
    payload = {
        "episode_id": episode_id,
        "record_id": record_id,
        "authorization_manifest_sha256": authorization_manifest_sha256,
        "sealed_plan_artifact_sha256": sealed_plan_artifact_sha256,
        "adapter_transaction_artifact_sha256": adapter_transaction_artifact_sha256,
        "wrapper_receipt_sha256": wrapper_receipt_sha256,
        "anchor_identity": anchor_identity,
        "anchor_route_index": anchor_route_index,
        "anchor_canonical_point": json_copy(anchor_canonical_point),
        "anchor_world_pose": json_copy(anchor_world_pose),
        "anchor_boundary_semantics": anchor_boundary_semantics,
        "anchor_inclusion_convention": anchor_inclusion_convention,
        "source_frame": source_frame,
        "sensor_payloads": json_copy(sensor_payloads),
        "rgb_source_hashes": list(rgb_source_hashes),
        "sensor_source_hashes": json_copy(sensor_source_hashes),
        "ego_state": json_copy(ego_state),
        "navigation_targets": navigation,
        "navigation_targets_sha256": content_hash(navigation),
        "expert_route_sha256": expert_route_sha256,
        "expert_owner_identity": expert_owner_identity,
        "expert_future_trajectory": trajectories,
        "expert_speed_targets": speeds,
        "expert_control_targets": controls,
        "sample_tick_offsets": list(FROZEN_TICKS),
        "additional_world_ticks": 0,
        "additional_sensors": 0,
        "additional_callbacks": 0,
        "additional_planners": 0,
        "additional_pid_controllers": 0,
        "additional_vehicle_control_writers": 0,
        "additional_expert_forwards": 0,
    }
    return seal_envelope(OBSERVATION_SCHEMA, payload)


def _official_record(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    owner = artifact.get("_checkpoint")
    if not isinstance(owner, Mapping):
        raise EvidenceContractError("OFFICIAL_CHECKPOINT_OWNER_MISSING")
    records = owner.get("records")
    if not isinstance(records, list) or len(records) != 1 or not isinstance(
            records[0], Mapping):
        raise EvidenceContractError("OFFICIAL_CHECKPOINT_RECORD_REQUIRED")
    return records[0]


def official_result_facts(artifact: Mapping[str, Any]) -> Dict[str, Any]:
    record = _official_record(artifact)
    scores = record.get("scores") if isinstance(record.get("scores"), Mapping) else {}
    infractions = (
        record.get("infractions")
        if isinstance(record.get("infractions"), Mapping) else {}
    )
    collision_keys = (
        "collisions_layout", "collisions_pedestrian", "collisions_vehicle"
    )
    traffic_keys = ("red_light", "stop_infraction", "scenario_timeouts")
    traffic_events = []
    for key in traffic_keys:
        rows = infractions.get(key)
        if isinstance(rows, list):
            traffic_events.extend(key + ":" + str(row) for row in rows)
    return {
        "official_evaluator_result": record.get("status"),
        "route_completion": scores.get("score_route"),
        "route_deviation": bool(infractions.get("route_dev")),
        "collision": any(bool(infractions.get(key)) for key in collision_keys),
        "off_road": bool(infractions.get("outside_route_lanes")),
        "wrong_lane": bool(infractions.get("wrong_way")),
        "traffic_rule_events": traffic_events,
        "termination_reason": record.get("status"),
    }


def build_evaluator_result_receipt(
        *, episode_id: str, authorization_manifest_sha256: str,
        official_result_artifact: Mapping[str, Any],
        selected_goal_realization: str,
        selected_connector_entry: str) -> Dict[str, Any]:
    payload = official_result_facts(official_result_artifact)
    payload.update({
        "episode_id": episode_id,
        "authorization_manifest_sha256": authorization_manifest_sha256,
        "official_result_artifact_sha256": content_hash(official_result_artifact),
        "selected_goal_realization": selected_goal_realization,
        "selected_connector_entry": selected_connector_entry,
        "result_parser": "OFFICIAL_LEADERBOARD_CHECKPOINT_V1",
        "route_deviation_is_failure": True,
    })
    return seal_envelope(EVALUATOR_SCHEMA, payload)


def cleanup_facts(process_receipt: Mapping[str, Any]) -> Dict[str, Any]:
    residue = process_receipt.get("process_residue")
    gpu_residue = process_receipt.get("owned_gpu_residue")
    pass_cleanup = bool(
        process_receipt.get("cleanup_exit") == 0
        and process_receipt.get("ports_released") is True
        and not residue and not gpu_residue
        and isinstance(process_receipt.get("server_exit"), int)
        and isinstance(process_receipt.get("evaluator_exit"), int)
    )
    return {
        "carla_exit": process_receipt.get("server_exit"),
        "evaluator_exit": process_receipt.get("evaluator_exit"),
        "pdm_dataagent_exit": process_receipt.get("evaluator_exit"),
        "residual_processes": json_copy(residue or []),
        "residual_gpu_processes": json_copy(gpu_residue or []),
        "ports_released": process_receipt.get("ports_released"),
        "cleanup_result": "PASS" if pass_cleanup else "FAIL",
    }


def build_cleanup_receipt(
        *, episode_id: str, authorization_manifest_sha256: str,
        process_receipt: Mapping[str, Any]) -> Dict[str, Any]:
    payload = cleanup_facts(process_receipt)
    payload.update({
        "episode_id": episode_id,
        "authorization_manifest_sha256": authorization_manifest_sha256,
        "process_receipt_sha256": content_hash(process_receipt),
        "lifecycle_owner": "ONE_SHOT_OFFICIAL_CARLA_JOB",
    })
    return seal_envelope(CLEANUP_SCHEMA, payload)


def build_run_manifest(
        *, episode_id: str, record_id: str,
        authorization_manifest: Mapping[str, Any],
        trusted_sealed_plan: Mapping[str, Any],
        adapter_transaction: Mapping[str, Any],
        wrapper_receipt: Mapping[str, Any],
        observation_expert_receipt: Mapping[str, Any],
        official_result_artifact: Mapping[str, Any],
        evaluator_result_receipt: Mapping[str, Any],
        process_receipt: Mapping[str, Any],
        cleanup_receipt: Mapping[str, Any], record: Mapping[str, Any]) -> Dict[str, Any]:
    payload = {
        "episode_id": episode_id,
        "record_id": record_id,
        "authorization_manifest_sha256": content_hash(authorization_manifest),
        "trusted_sealed_plan_sha256": content_hash(trusted_sealed_plan),
        "adapter_transaction_sha256": content_hash(adapter_transaction),
        "wrapper_receipt_sha256": content_hash(wrapper_receipt),
        "observation_expert_receipt_sha256": content_hash(
            observation_expert_receipt
        ),
        "official_result_artifact_sha256": content_hash(official_result_artifact),
        "evaluator_result_receipt_sha256": content_hash(evaluator_result_receipt),
        "process_receipt_sha256": content_hash(process_receipt),
        "cleanup_receipt_sha256": content_hash(cleanup_receipt),
        "record_sha256": content_hash(record),
        "record_valid_candidate": True,
        "runtime_split_assignment": None,
        "runtime_a1_eligibility": False,
    }
    return seal_envelope(RUN_SCHEMA, payload)
