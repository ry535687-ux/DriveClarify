#!/usr/bin/env python3
"""One-shot, observation-only SimLingo agent for the frozen batch screen.

The agent loads the normal SimLingo checkpoint through the existing production
agent setup, captures observation index zero, persists a replayable package
when eligible, and raises the established intentional-stop signal before any
model/candidate forward, PID call, planner advance, or vehicle control return.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import math
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


ROOT = Path("/home/buaa/wrh/DriveClarify")
BASE_AGENT = (
    ROOT
    / "reports/driveclarify_manual_phase0a_tick_guard_prestop_identity_fix"
    / "PHASE0A_EXPLANATION_PAIR_AGENT.py"
)
BASE_AGENT_SHA256 = "af978b24a3cf6a29a07bcf396b239a65bd4c569cb9f3a9f103cc7395a8c7393b"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_path(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest(value: Any) -> str:
    return _sha_bytes(_canonical(value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_base() -> Any:
    raw = BASE_AGENT.read_bytes()
    if _sha_bytes(raw) != BASE_AGENT_SHA256:
        raise RuntimeError("OBS_SCREEN_BASE_AGENT_SHA256_MISMATCH")
    name = "_driveclarify_observation_screen_base"
    spec = importlib.util.spec_from_file_location(name, str(BASE_AGENT))
    if spec is None or spec.loader is None:
        raise RuntimeError("OBS_SCREEN_BASE_AGENT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


_base = _load_base()

from driveclarify_static_branch.observation_package import (  # noqa: E402
    ObservationPackageWriter,
    atomic_create_json,
    atomic_replace_json,
    unavailable_manifest,
)
from driveclarify_static_branch.run_result_lifecycle import normalize_signed_station  # noqa: E402


_RUNTIME_ROUTE_SCENARIO = None


def _install_route_scenario_observer() -> None:
    from leaderboard.scenarios import route_scenario as route_scenario_module

    route_scenario_class = route_scenario_module.RouteScenario
    original_init = route_scenario_class.__init__
    if getattr(original_init, "_driveclarify_observation_screen_observer", False):
        raise RuntimeError("OBS_SCREEN_ROUTE_SCENARIO_OBSERVER_ALREADY_INSTALLED")

    def observed_init(instance: Any, *args: Any, **kwargs: Any) -> None:
        original_init(instance, *args, **kwargs)
        global _RUNTIME_ROUTE_SCENARIO
        if _RUNTIME_ROUTE_SCENARIO is not None:
            raise RuntimeError("OBS_SCREEN_MULTIPLE_ROUTE_SCENARIO_INSTANCES")
        _RUNTIME_ROUTE_SCENARIO = instance

    observed_init._driveclarify_observation_screen_observer = True  # type: ignore[attr-defined]
    route_scenario_class.__init__ = observed_init


_install_route_scenario_observer()


def get_entry_point() -> str:
    return "ObservationScreeningAgent"


def _embedded_hash(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("sha256", None)
    return _digest(unsigned)


def _load_embedded(path: Path, label: str) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    recorded = value.get("sha256")
    if not isinstance(recorded, str) or _embedded_hash(value) != recorded:
        raise RuntimeError("OBS_SCREEN_{}_EMBEDDED_HASH_MISMATCH".format(label))
    return value


def _transform_record(transform: Any) -> Dict[str, Any]:
    location = transform.location
    rotation = transform.rotation
    return {
        "location_xyz": [float(location.x), float(location.y), float(location.z)],
        "rotation_roll_pitch_yaw_degrees": [
            float(rotation.roll),
            float(rotation.pitch),
            float(rotation.yaw),
        ],
        "ego_to_world_matrix_4x4": [list(row) for row in transform.get_matrix()],
        "world_to_ego_matrix_4x4": [list(row) for row in transform.get_inverse_matrix()],
        "frame": "CARLA_WORLD",
        "translation_unit": "METRE",
        "rotation_unit": "DEGREE",
    }


def _vector(value: Any) -> Sequence[float]:
    return [float(value.x), float(value.y), float(value.z)]


def _route_runtime_evidence(route_scenario: Any) -> Dict[str, Any]:
    configurations = list(getattr(route_scenario, "scenario_configurations", ()) or ())
    scenarios = list(getattr(route_scenario, "list_scenarios", ()) or ())
    other_actors = list(getattr(route_scenario, "other_actors", ()) or ())
    return {
        "scenario_configuration_count": len(configurations),
        "scenario_instance_count": len(scenarios),
        "scenario_actor_count": len(other_actors),
        "scenario_configuration_names": [str(getattr(item, "name", "")) for item in configurations],
        "scenario_free_runtime_verified": not configurations and not scenarios and not other_actors,
    }


def _input_digest(value: Any) -> Mapping[str, Any]:
    """Type-preserving identity for the complete normal SimLingo input tree."""

    if value is None:
        return {"kind": "none", "value": None}
    if isinstance(value, _base.torch.Tensor):
        device = str(value.device)
        tensor = value.detach().contiguous().cpu()
        raw = tensor.view(_base.torch.uint8).numpy().tobytes(order="C")
        return {
            "kind": "tensor",
            "shape": list(tensor.shape),
            "dtype": str(tensor.dtype),
            "source_device": device,
            "sha256": _sha_bytes(raw),
        }
    if isinstance(value, _base.np.ndarray):
        array = _base.np.ascontiguousarray(value)
        return {
            "kind": "numpy.ndarray",
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "sha256": _sha_bytes(array.tobytes(order="C")),
        }
    if isinstance(value, _base.np.generic):
        array = _base.np.ascontiguousarray(value)
        return {
            "kind": "numpy.scalar",
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "sha256": _sha_bytes(array.tobytes(order="C")),
        }
    if isinstance(value, bool):
        return {"kind": "bool", "value": value}
    if isinstance(value, int):
        return {"kind": "int", "value": value}
    if isinstance(value, float):
        return {"kind": "float", "value_hex": value.hex()}
    if isinstance(value, str):
        return {"kind": "str", "value": value}
    if isinstance(value, bytes):
        return {"kind": "bytes", "length": len(value), "sha256": _sha_bytes(value)}
    if isinstance(value, list):
        return {"kind": "list", "items": [_input_digest(item) for item in value]}
    if isinstance(value, tuple):
        return {
            "kind": "namedtuple" if hasattr(value, "_fields") else "tuple",
            "type": type(value).__module__ + "." + type(value).__qualname__,
            "items": [_input_digest(item) for item in value],
        }
    if isinstance(value, dict):
        return {
            "kind": "dict",
            "items": [
                {"key": _input_digest(key), "value": _input_digest(item)}
                for key, item in value.items()
            ],
        }
    value_type = type(value)
    raise TypeError(
        "OBS_SCREEN_UNSUPPORTED_INPUT_DIGEST_TYPE:{}.{}".format(
            value_type.__module__, value_type.__qualname__
        )
    )


def _value_metadata(value: Any) -> Tuple[Optional[Sequence[int]], str, Mapping[str, Any]]:
    digest = _input_digest(value)
    if isinstance(value, _base.torch.Tensor):
        return list(value.shape), str(value.dtype), digest
    if value is None:
        return None, "NONE", digest
    return None, "PYTORCH_NESTED_CONTAINER", digest


def _cpu_frozen_copy(value: Any) -> Any:
    if isinstance(value, _base.torch.Tensor):
        return value.detach().contiguous().cpu().clone()
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        return type(value)(*[_cpu_frozen_copy(item) for item in value])
    if isinstance(value, tuple):
        return tuple(_cpu_frozen_copy(item) for item in value)
    if isinstance(value, list):
        return [_cpu_frozen_copy(item) for item in value]
    if isinstance(value, dict):
        return {key: _cpu_frozen_copy(item) for key, item in value.items()}
    return value


def _torch_bytes(value: Any) -> bytes:
    buffer = io.BytesIO()
    _base.torch.save(_cpu_frozen_copy(value), buffer)
    return buffer.getvalue()


def _persist_observation_package(
    *,
    agent: Any,
    model_input: Any,
    observation_hash: str,
    observation_members: Mapping[str, Any],
    source_frame: int,
    source_identity: Mapping[str, Any],
    ego_state: Mapping[str, Any],
    route_state: Mapping[str, Any],
    runtime_metadata: Mapping[str, Any],
) -> Mapping[str, Any]:
    package_root = Path(os.environ["DRIVECLARIFY_OBS_PACKAGE_ROOT"])
    manifest_path = Path(os.environ["DRIVECLARIFY_OBS_PACKAGE_MANIFEST"])
    writer = ObservationPackageWriter(
        package_root=package_root,
        manifest_path=manifest_path,
        run_id=agent._obs_run_id,
        unit_id=agent._unit["unit_id"],
        observation_hash=observation_hash,
        source_frame=source_frame,
        source_identity=source_identity,
        eligibility_contract_sha256=agent._contract["sha256"],
        topology_sha256=agent._topology["sha256"],
    )
    field_roles = {
        "camera_images": "PROCESSED_MODEL_READY_CAMERA_IMAGES",
        "image_sizes": "CAMERA_IMAGE_SIZE_METADATA",
        "camera_intrinsics": "CAMERA_INTRINSICS",
        "camera_extrinsics": "CAMERA_EXTRINSICS",
        "vehicle_speed": "EGO_SPEED_MODEL_INPUT",
        "target_point": "ROUTE_NAVIGATION_TARGET_POINT",
        "prompt": "NORMAL_SIMLINGO_LANGUAGE_INPUT",
        "prompt_inference": "NORMAL_SIMLINGO_LANGUAGE_INFERENCE_INPUT",
    }
    schema_fields = []
    for name in model_input._fields:
        value = getattr(model_input, name)
        shape, dtype, member_digest = _value_metadata(value)
        relative = "model_ready/{}.pt".format(name)
        writer.add_bytes(
            relative,
            _torch_bytes(value),
            semantic_role=field_roles[name],
            shape=shape,
            dtype=dtype,
            frame="MODEL_LOCAL_RAW" if name in {"camera_images", "image_sizes", "camera_intrinsics", "camera_extrinsics"} else "EGO_LOCAL",
            serialization="PYTORCH_SAVE_CPU_OR_METADATA_OBJECT",
        )
        schema_fields.append(
            {
                "name": name,
                "relative_path": relative,
                "shape": list(shape) if shape is not None else None,
                "dtype": dtype,
                "content_identity": member_digest,
            }
        )
    writer.add_json(
        "metadata/model_ready_input_schema.json",
        {
            "schema_version": "driveclarify.simlingo_model_ready_input_schema.v1",
            "named_tuple_type": type(model_input).__module__ + "." + type(model_input).__qualname__,
            "field_order": list(model_input._fields),
            "fields": schema_fields,
            "observation_members": observation_members,
        },
        semantic_role="MODEL_READY_INPUT_SCHEMA_AND_CONTENT_IDENTITIES",
    )
    writer.add_json(
        "metadata/ego_state.json",
        ego_state,
        semantic_role="EGO_POSE_SPEED_KINEMATICS_AND_WORLD_TO_EGO_TRANSFORM",
        frame="CARLA_WORLD",
    )
    writer.add_json(
        "metadata/navigation_state.json",
        route_state,
        semantic_role="ROUTE_NAVIGATION_HLC_AND_CLOSED_LOOP_STATE",
        frame="CARLA_WORLD_AND_MODEL_ROUTE_STATE",
    )
    writer.add_json(
        "metadata/source_identity.json",
        source_identity,
        semantic_role="TOWN_ROUTE_JUNCTION_FRAME_AND_SOURCE_IDENTITY",
        frame="CARLA_WORLD",
    )
    writer.add_json(
        "metadata/runtime_metadata.json",
        runtime_metadata,
        semantic_role="SOURCE_FRAME_SIMULATION_TIMESTAMP_AND_OBSERVATION_INDEX",
        frame="CARLA_WORLD",
    )
    writer.add_json(
        "metadata/preprocessing_provenance.json",
        {
            "schema_version": "driveclarify.observation_preprocessing_provenance.v1",
            "normal_simlingo_tick_path_used": True,
            "model_ready_input_constructed_as": "DrivingInput(**self.DrivingInput)",
            "base_agent_path": str(BASE_AGENT),
            "base_agent_sha256": BASE_AGENT_SHA256,
            "runtime_agent_path": str(Path(__file__).resolve()),
            "runtime_agent_sha256": _sha_path(Path(__file__).resolve()),
            "checkpoint_path": os.environ["DRIVECLARIFY_OBS_CHECKPOINT_PATH"],
            "checkpoint_sha256": os.environ["DRIVECLARIFY_CKPT_HASH"],
            "config_path": os.environ["DRIVECLARIFY_OBS_CONFIG_PATH"],
            "config_sha256": os.environ["DRIVECLARIFY_CONFIG_HASH"],
            "route_fixture_path": os.environ["ROUTES"],
            "route_fixture_sha256": source_identity["route_fixture_sha256"],
            "eligibility_contract_path": os.environ["DRIVECLARIFY_OBS_ELIGIBILITY_CONTRACT"],
            "eligibility_contract_sha256": agent._contract["sha256"],
            "topology_path": os.environ["DRIVECLARIFY_OBS_TOPOLOGY"],
            "topology_sha256": agent._topology["sha256"],
            "candidate_semantic_payload_created": False,
            "candidate_forward_count": 0,
        },
        semantic_role="PREPROCESSING_CHECKPOINT_CONFIG_AND_AUTHORITY_PROVENANCE",
    )
    return writer.finalize(
        additional_metadata={
            "normal_simlingo_input_path_replayable": True,
            "torch_save_values_moved_to_cpu": True,
            "candidate_semantic_payload_included": False,
        }
    )


class ObservationScreeningAgent(_base.Phase0AExplanationPairAgent):
    def setup(self, path_to_conf_file: str, route_index: Any = None) -> None:
        required = (
            "DRIVECLARIFY_OBS_RUN_ID",
            "DRIVECLARIFY_OBS_UNIT_MANIFEST",
            "DRIVECLARIFY_OBS_ELIGIBILITY_CONTRACT",
            "DRIVECLARIFY_OBS_TOPOLOGY",
            "DRIVECLARIFY_OBS_FIRST_EVIDENCE",
            "DRIVECLARIFY_OBS_SCREENING_RESULT",
            "DRIVECLARIFY_OBS_RUNTIME_COUNTS",
            "DRIVECLARIFY_OBS_PACKAGE_ROOT",
            "DRIVECLARIFY_OBS_PACKAGE_MANIFEST",
            "DRIVECLARIFY_OBS_CHECKPOINT_PATH",
            "DRIVECLARIFY_OBS_CONFIG_PATH",
            "DRIVECLARIFY_CKPT_HASH",
            "DRIVECLARIFY_CONFIG_HASH",
        )
        missing = [name for name in required if name not in os.environ]
        if missing:
            raise RuntimeError("OBS_SCREEN_REQUIRED_ENVIRONMENT_MISSING:" + ",".join(missing))
        self._obs_run_id = os.environ["DRIVECLARIFY_OBS_RUN_ID"]
        if os.environ.get("DRIVECLARIFY_PHASE0A_RUN_ID") != self._obs_run_id:
            raise RuntimeError("OBS_SCREEN_RUN_ID_MISMATCH")
        os.environ.pop("DRIVECLARIFY_STRICT_DETERMINISTIC_CUMSUM", None)
        _base.Phase0AExplanationPairAgent.setup(self, path_to_conf_file, route_index=route_index)
        self._unit = _load_embedded(Path(os.environ["DRIVECLARIFY_OBS_UNIT_MANIFEST"]), "UNIT")
        self._contract = _load_embedded(
            Path(os.environ["DRIVECLARIFY_OBS_ELIGIBILITY_CONTRACT"]), "ELIGIBILITY"
        )
        self._topology = _load_embedded(Path(os.environ["DRIVECLARIFY_OBS_TOPOLOGY"]), "TOPOLOGY")
        if (
            self._unit["unit_id"] not in self._obs_run_id
            and os.environ.get("DRIVECLARIFY_OBS_UNIT_ID") != self._unit["unit_id"]
        ):
            raise RuntimeError("OBS_SCREEN_UNIT_ID_BINDING_MISMATCH")
        if (
            self._contract["topology_sha256"] != self._topology["sha256"]
            or self._unit["topology_sha256"] != self._topology["sha256"]
            or self._unit["eligibility_interval"] != self._contract["eligibility_interval"]
        ):
            raise RuntimeError("OBS_SCREEN_FROZEN_AUTHORITY_CROSS_BINDING_MISMATCH")
        self._model_ready_observation_count = 0

    def _persist_failure(self, exc: BaseException) -> None:
        result = {
            "schema_version": "driveclarify.observation_screening_runtime_result.v1",
            "run_id": self._obs_run_id,
            "unit_id": self._unit.get("unit_id"),
            "outcome": "RUNTIME_FAILURE",
            "first_error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "repr": repr(exc),
                "traceback": traceback.format_exc(),
            },
            "candidate_forward_count": int(getattr(self.model, "forward_count", 0)),
            "second_observation_count": max(0, self._model_ready_observation_count - 1),
            "automatic_continuation": False,
        }
        path = self._p0a_output
        if path.exists():
            atomic_replace_json(path, result)
        else:
            atomic_create_json(path, result)

    def _intentional_stop(self, payload: Mapping[str, Any], prestop_refs: Any, prestop_record: Any) -> Any:
        atomic_create_json(self._p0a_output, payload)
        prestop = _base._validate_prestop_identity(prestop_refs, prestop_record)
        frame = int(prestop["actual"]["snapshot"]["frame"])
        updated = dict(payload)
        updated["prestop_guard"] = {"status": "PASS", "identity_inventory": prestop}
        updated["intentional_stop"] = {
            "reason": self._p0a_stop_reason,
            "raised_in_same_run_step": True,
            "vehicle_control_returned_after_observation": False,
            "post_observation_tick_guard_installed_before_stop": True,
            "post_observation_tick_guard_activated_after_durable_write": True,
            "simulator_frame_at_stop": frame,
        }
        atomic_replace_json(self._p0a_output, updated)
        _base._p0a_tick_guard.mark_candidate_durable(self._p0a_output)
        _base._p0a_tick_guard.activate(frame)
        self._p0a_terminal = True
        type(self)._p0a_global_intentional_stop_raises += 1
        raise _base.Phase0AIntentionalStop(self._p0a_stop_reason)

    def run_step(self, input_data: Any, timestamp: Any, sensors: Any = None) -> Any:
        type(self)._p0a_global_run_step_entries += 1
        if self._p0a_terminal:
            raise RuntimeError("OBS_SCREEN_REENTRY_AFTER_INTENTIONAL_STOP")
        if not self.initialized:
            ordinary_parent = super(_base.Phase0AExplanationPairAgent, self)
            return self._return_vehicle_control(
                ordinary_parent.run_step(input_data, timestamp, sensors=sensors)
            )
        if self._model_ready_observation_count != 0:
            raise RuntimeError("OBS_SCREEN_SECOND_MODEL_READY_OBSERVATION_FORBIDDEN")
        self._model_ready_observation_count += 1

        try:
            if int(getattr(self.model, "forward_count", 0)) != 0:
                raise RuntimeError("OBS_SCREEN_MODEL_FORWARD_BEFORE_OBSERVATION_GATE")
            self.step += 1
            self.tick(input_data)
            model_input = _base.DrivingInput(**self.DrivingInput)
            observation_members = {
                name: _input_digest(getattr(model_input, name))
                for name in model_input._fields
            }
            observation_hash = _digest(observation_members)
            state_digest, state_members = self._selected_state_digest()
            prestop_refs, prestop_record = _base._capture_prestop_identity(
                "BEFORE_OBSERVATION_SCREEN_INTENTIONAL_STOP"
            )
            world = _base.CarlaDataProvider.get_world()
            hero = _base.CarlaDataProvider.get_hero_actor()
            if world is None or hero is None or _RUNTIME_ROUTE_SCENARIO is None:
                raise RuntimeError("OBS_SCREEN_WORLD_HERO_OR_ROUTE_SCENARIO_UNAVAILABLE")
            snapshot = world.get_snapshot()
            carla_map = world.get_map()
            transform = hero.get_transform()
            location = transform.location
            waypoint = carla_map.get_waypoint(location, project_to_road=True)
            waypoint_location = waypoint.transform.location
            projection_distance = math.sqrt(
                (float(location.x) - float(waypoint_location.x)) ** 2
                + (float(location.y) - float(waypoint_location.y)) ** 2
                + (float(location.z) - float(waypoint_location.z)) ** 2
            )
            route_hash = _sha_path(Path(os.environ["ROUTES"]))
            map_hash = _sha_bytes(carla_map.to_opendrive().encode("utf-8"))
            decision = self._contract["decision_point"]
            identity_checks = {
                "unit_id_match": os.environ["DRIVECLARIFY_OBS_UNIT_ID"] == self._unit["unit_id"],
                "town_match": self._unit["town"] in str(carla_map.name),
                "route_fixture_hash_match": route_hash
                == os.environ["DRIVECLARIFY_OBS_ROUTE_FIXTURE_SHA256"],
                "opendrive_hash_match": map_hash == self._topology["map_identity"]["opendrive_sha256"],
                "incoming_road_match": int(waypoint.road_id) == int(decision["incoming_road_id"]),
                "incoming_lane_match": int(waypoint.lane_id) == int(decision["incoming_lane_id"]),
                "road_projection_within_threshold": projection_distance <= 1.75,
            }
            identity_ok = all(identity_checks.values())
            signed_station = normalize_signed_station(waypoint.s, decision) if identity_ok else None
            interval = self._contract["eligibility_interval"]
            lower = float(interval["lower_inclusive_m"])
            upper = float(interval["upper_exclusive_m"])
            input_complete = all(
                getattr(model_input, name) is not None
                for name in (
                    "camera_images",
                    "camera_intrinsics",
                    "camera_extrinsics",
                    "vehicle_speed",
                    "target_point",
                    "prompt",
                    "prompt_inference",
                )
            )
            route_state_verifiable = state_members.get("route") is not None
            scenario_runtime = _route_runtime_evidence(_RUNTIME_ROUTE_SCENARIO)
            reason_codes = []
            if not identity_ok:
                reason_codes.append("UNIT_TOWN_ROUTE_JUNCTION_OR_POSITION_IDENTITY_UNVERIFIED")
            if not input_complete:
                reason_codes.append("MODEL_READY_INPUT_INCOMPLETE")
            if not route_state_verifiable:
                reason_codes.append("ROUTE_NAVIGATION_STATE_UNVERIFIABLE")
            if not scenario_runtime["scenario_free_runtime_verified"]:
                reason_codes.append("SCENARIO_FREE_RUNTIME_IDENTITY_MISMATCH")
            if signed_station is not None and signed_station < lower:
                reason_codes.append("FIRST_OBSERVATION_TOO_EARLY_FOR_FROZEN_ELIGIBILITY")
            if signed_station is not None and signed_station >= upper:
                reason_codes.append("FIRST_OBSERVATION_AT_OR_AFTER_FROZEN_UPPER_BOUND")
            eligible = (
                not reason_codes
                and signed_station is not None
                and lower <= signed_station < upper
            )
            outcome = "ELIGIBLE" if eligible else "EVIDENCE_UNAVAILABLE"
            if eligible:
                reason_codes = ["FIRST_MODEL_READY_OBSERVATION_SATISFIES_FROZEN_CONTRACT"]

            ego_pose = _transform_record(transform)
            ego_state = {
                "pose": ego_pose,
                "velocity_xyz_mps": _vector(hero.get_velocity()),
                "angular_velocity_xyz_dps": _vector(hero.get_angular_velocity()),
                "acceleration_xyz_mps2": _vector(hero.get_acceleration()),
                "map_waypoint": {
                    "road_id": int(waypoint.road_id),
                    "lane_id": int(waypoint.lane_id),
                    "section_id": int(waypoint.section_id),
                    "s_m": float(waypoint.s),
                    "projection_distance_m": projection_distance,
                },
            }
            source_identity = {
                "unit_id": self._unit["unit_id"],
                "town": self._unit["town"],
                "route_id": self._unit["route_id"],
                "junction_id": self._unit["junction_id"],
                "route_fixture_sha256": route_hash,
                "opendrive_sha256": map_hash,
                "topology_sha256": self._topology["sha256"],
                "eligibility_contract_sha256": self._contract["sha256"],
                "identity_checks": identity_checks,
            }
            runtime_metadata = {
                "run_id": self._obs_run_id,
                "observation_index": 0,
                "source_frame": int(snapshot.frame),
                "simulation_timestamp_seconds": float(snapshot.timestamp.elapsed_seconds),
                "source_timestamp_argument": float(timestamp),
                "signed_station_m": signed_station,
                "eligibility_interval": interval,
            }
            first_evidence = {
                "schema_version": "driveclarify.first_observation_evidence.v1",
                "run_id": self._obs_run_id,
                "unit_id": self._unit["unit_id"],
                "observation_identity": "{}:frame:{}:sim_time:{:.9f}".format(
                    self._obs_run_id,
                    int(snapshot.frame),
                    float(snapshot.timestamp.elapsed_seconds),
                ),
                "observation_index": 0,
                "prior_model_ready_observation_count": 0,
                "skipped_model_ready_observation_count": 0,
                "source_frame": int(snapshot.frame),
                "simulation_timestamp_seconds": float(snapshot.timestamp.elapsed_seconds),
                "observation_hash": observation_hash,
                "observation_members": observation_members,
                "ego_pose": ego_pose,
                "ego_state": ego_state,
                "route_state_digest": state_digest,
                "route_state": state_members,
                "source_identity": source_identity,
                "scenario_free_runtime_evidence": scenario_runtime,
                "signed_station_m": signed_station,
                "eligibility_interval": interval,
                "outcome_at_observation_gate": outcome,
                "reason_codes": reason_codes,
                "candidate_forward_count": 0,
                "second_observation_count": 0,
                "captured_at_utc": _utc_now(),
            }
            atomic_create_json(Path(os.environ["DRIVECLARIFY_OBS_FIRST_EVIDENCE"]), first_evidence)

            if eligible:
                package_manifest = _persist_observation_package(
                    agent=self,
                    model_input=model_input,
                    observation_hash=observation_hash,
                    observation_members=observation_members,
                    source_frame=int(snapshot.frame),
                    source_identity=source_identity,
                    ego_state=ego_state,
                    route_state={"digest": state_digest, "members": state_members},
                    runtime_metadata=runtime_metadata,
                )
                package_path: Optional[str] = package_manifest["package_directory"]
            else:
                unavailable_manifest(
                    manifest_path=Path(os.environ["DRIVECLARIFY_OBS_PACKAGE_MANIFEST"]),
                    run_id=self._obs_run_id,
                    unit_id=self._unit["unit_id"],
                    status="NOT_CREATED_EVIDENCE_UNAVAILABLE",
                    reason_codes=reason_codes,
                    eligibility_contract_sha256=self._contract["sha256"],
                    topology_sha256=self._topology["sha256"],
                )
                package_path = None

            if int(getattr(self.model, "forward_count", 0)) != 0:
                raise RuntimeError("OBS_SCREEN_MODEL_FORWARD_COUNT_NOT_ZERO")
            counts = {
                "schema_version": "driveclarify.observation_screening_runtime_counts.v1",
                "run_id": self._obs_run_id,
                "model_ready_observation": 1,
                "observation_index_zero_reads": 1,
                "second_observation": 0,
                "candidate_forward": 0,
                "candidate_semantic_payload": 0,
                "mapper_invocation": 0,
                "training": 0,
                "act_ask_wait": 0,
                "pid": 0,
                "planner_advance": 0,
                "control_send_after_observation": 0,
            }
            atomic_create_json(Path(os.environ["DRIVECLARIFY_OBS_RUNTIME_COUNTS"]), counts)
            screening = {
                "schema_version": "driveclarify.observation_screening_output.v1",
                "unit_id": self._unit["unit_id"],
                "outcome": outcome,
                "observation_index": 0,
                "observation_identity": first_evidence["observation_identity"],
                "signed_station_m": signed_station,
                "eligibility_contract_sha256": self._contract["sha256"],
                "candidate_forward_count": 0,
                "second_observation_count": 0,
                "cleanup_status": "UNKNOWN",
                "reason_codes": reason_codes,
                "observation_hash": observation_hash,
                "source_frame": int(snapshot.frame),
                "ego_pose": ego_pose,
                "runtime_metadata": dict(
                    runtime_metadata,
                    package_path=package_path,
                ),
            }
            atomic_create_json(Path(os.environ["DRIVECLARIFY_OBS_SCREENING_RESULT"]), screening)
            runtime_result = {
                "schema_version": "driveclarify.observation_screening_runtime_result.v1",
                "run_id": self._obs_run_id,
                "unit_id": self._unit["unit_id"],
                "outcome": outcome,
                "observation_screening": screening,
                "candidate_forward_count": 0,
                "second_observation_count": 0,
                "automatic_continuation": False,
                "prestop_guard": {"status": "PENDING"},
            }
            return self._intentional_stop(runtime_result, prestop_refs, prestop_record)
        except _base.Phase0AIntentionalStop:
            raise
        except BaseException as exc:
            self._persist_failure(exc)
            raise
