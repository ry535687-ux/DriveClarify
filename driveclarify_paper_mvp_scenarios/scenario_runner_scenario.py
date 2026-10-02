"""Receipt-aware ScenarioRunner handler for promoted Stage 6A fixtures.

This module is copied into a ScenarioRunner overlay.  Normal package imports do
not import it.  It refuses actor creation unless an opaque runtime manifest and
one content-addressed 24x4 live receipt pass every physical and semantic gate.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import carla
import py_trees

from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenariomanager.timer import GameTime
from srunner.scenarios.basic_scenario import BasicScenario


EXPECTED_TYPE = "DriveClarifyPaperMVPScenario"
GENERATED_ROOT_ENV = "DRIVECLARIFY_STAGE6A_GENERATED_ROOT"
PROMOTION_ROOT_ENV = "DRIVECLARIFY_STAGE6A_PROMOTION_ROOT"
SELECTED_SEED_ENV = "DRIVECLARIFY_STAGE6A_SELECTED_SEED"
AUTHORING_EXECUTION_ENV = "DRIVECLARIFY_STAGE6A_AUTHORING_EXECUTION"
AUTHORING_RECEIPT_ENV = "DRIVECLARIFY_STAGE6A_AUTHORING_RECEIPT"
AUTHORING_EXECUTION_TOKEN = "LIVE_CARLA_SCENARIO_RUNNER_EXECUTION"
LIVE_MANIFEST_NAME = "LIVE_PROMOTION_MANIFEST.json"
LIVE_MANIFEST_SCHEMA_VERSION = "driveclarify.paper_mvp_stage6a_live_manifest.v1"
LIVE_RECEIPT_SCHEMA_VERSION = "driveclarify.paper_mvp_stage6a_live_receipt.v1"
LIVE_EVIDENCE_ORIGIN = "LIVE_CARLA_EXISTING_SERVER"


class Stage6ALiveResolutionRequired(RuntimeError):
    """The static fixture or its live receipt is not safe to execute."""


def _reject_constant(value):
    raise Stage6ALiveResolutionRequired("STAGE6A_JSON_NONFINITE:" + value)


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Stage6ALiveResolutionRequired("STAGE6A_JSON_DUPLICATE_KEY:" + key)
        result[key] = value
    return result


def _load_json_bytes(payload, reason):
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Stage6ALiveResolutionRequired(reason) from exc
    if not isinstance(value, dict):
        raise Stage6ALiveResolutionRequired(reason + "_NOT_OBJECT")
    return value


def _canonical_sha256(value):
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _verify_content_address(value, field):
    digest = value.get(field)
    unsigned = dict(value)
    unsigned.pop(field, None)
    return isinstance(digest, str) and digest == _canonical_sha256(unsigned)


def _safe_file(root, relative, missing_reason):
    value = Path(relative)
    if value.is_absolute():
        raise Stage6ALiveResolutionRequired(missing_reason + "_ABSOLUTE")
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise Stage6ALiveResolutionRequired(missing_reason + "_ESCAPE") from exc
    if not path.is_file():
        raise Stage6ALiveResolutionRequired(missing_reason)
    return path


def _load_bound_runtime(config):
    fixture = getattr(config, "other_parameters", {}).get("fixture")
    if not isinstance(fixture, dict):
        raise Stage6ALiveResolutionRequired("STAGE6A_FIXTURE_XML_BINDING_MISSING")
    required = {
        "runtime_fixture_id",
        "runtime_manifest",
        "runtime_manifest_sha256",
        "binding_state",
    }
    if set(fixture) != required:
        raise Stage6ALiveResolutionRequired("STAGE6A_FIXTURE_XML_BINDING_INVALID")
    if fixture["binding_state"] != "STATIC_COMPILED_LIVE_RESOLUTION_REQUIRED":
        raise Stage6ALiveResolutionRequired("STAGE6A_FIXTURE_XML_BINDING_STATE_INVALID")
    root_value = os.environ.get(GENERATED_ROOT_ENV)
    if not root_value:
        raise Stage6ALiveResolutionRequired("STAGE6A_GENERATED_ROOT_NOT_EXPLICIT")
    root = Path(root_value).resolve()
    path = _safe_file(
        root, fixture["runtime_manifest"], "STAGE6A_RUNTIME_MANIFEST_MISSING"
    )
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != fixture["runtime_manifest_sha256"]:
        raise Stage6ALiveResolutionRequired("STAGE6A_RUNTIME_MANIFEST_SHA256_MISMATCH")
    runtime = _load_json_bytes(payload, "STAGE6A_RUNTIME_MANIFEST_JSON_INVALID")
    if runtime.get("runtime_fixture_id") != fixture["runtime_fixture_id"]:
        raise Stage6ALiveResolutionRequired("STAGE6A_RUNTIME_FIXTURE_ID_MISMATCH")
    if runtime.get("launch_gate", {}).get("launch_ready") is not False:
        raise Stage6ALiveResolutionRequired("STAGE6A_STATIC_LAUNCH_GATE_STATE_INVALID")
    return runtime, digest, root


def _load_live_receipt(runtime, runtime_sha256, generated_root):
    promotion_value = os.environ.get(PROMOTION_ROOT_ENV)
    seed_value = os.environ.get(SELECTED_SEED_ENV)
    if not promotion_value:
        raise Stage6ALiveResolutionRequired("STAGE6A_PROMOTION_ROOT_NOT_EXPLICIT")
    if seed_value is None:
        raise Stage6ALiveResolutionRequired("STAGE6A_SELECTED_SEED_NOT_EXPLICIT")
    try:
        selected_seed = int(seed_value)
    except ValueError as exc:
        raise Stage6ALiveResolutionRequired("STAGE6A_SELECTED_SEED_INVALID") from exc
    if selected_seed not in runtime.get("allowed_seed_values", []):
        raise Stage6ALiveResolutionRequired("STAGE6A_SELECTED_SEED_NOT_ALLOWED")
    root = Path(promotion_value).resolve()
    # A real execution receipt necessarily has to be authored before the final
    # 24x4 promotion manifest can say that the handler executed.  The normal
    # runtime path below therefore remains strict, while this narrowly-scoped
    # authoring path accepts exactly one preliminary, content-addressed LIVE
    # receipt.  It still requires physical and semantic verification and can
    # never accept a test double or an already-promoted/self-referential row.
    if os.environ.get(AUTHORING_EXECUTION_ENV) == AUTHORING_EXECUTION_TOKEN:
        relative = os.environ.get(AUTHORING_RECEIPT_ENV)
        if not relative:
            raise Stage6ALiveResolutionRequired(
                "STAGE6A_AUTHORING_RECEIPT_NOT_EXPLICIT"
            )
        receipt_path = _safe_file(
            root, relative, "STAGE6A_AUTHORING_RECEIPT_MISSING"
        )
        receipt = _load_json_bytes(
            receipt_path.read_bytes(), "STAGE6A_AUTHORING_RECEIPT_JSON_INVALID"
        )
        if receipt.get("schema_version") != LIVE_RECEIPT_SCHEMA_VERSION:
            raise Stage6ALiveResolutionRequired(
                "STAGE6A_AUTHORING_RECEIPT_SCHEMA_INVALID"
            )
        if not _verify_content_address(receipt, "receipt_payload_sha256"):
            raise Stage6ALiveResolutionRequired(
                "STAGE6A_AUTHORING_RECEIPT_CONTENT_HASH_INVALID"
            )
        if receipt_path.stem != receipt.get("receipt_payload_sha256"):
            raise Stage6ALiveResolutionRequired(
                "STAGE6A_AUTHORING_RECEIPT_PATH_HASH_INVALID"
            )
        route_path = _safe_file(
            generated_root,
            runtime["route_binding"]["derived_route_path"],
            "STAGE6A_DERIVED_ROUTE_MISSING",
        )
        if not (
            receipt.get("runtime_fixture_id") == runtime["runtime_fixture_id"]
            and receipt.get("runtime_manifest_sha256") == runtime_sha256
            and receipt.get("selected_seed") == selected_seed
            and receipt.get("derived_route_sha256")
            == hashlib.sha256(route_path.read_bytes()).hexdigest()
        ):
            raise Stage6ALiveResolutionRequired(
                "STAGE6A_AUTHORING_RECEIPT_RUNTIME_BINDING_INVALID"
            )
        if receipt.get("provenance", {}).get("evidence_origin") != LIVE_EVIDENCE_ORIGIN:
            raise Stage6ALiveResolutionRequired(
                "STAGE6A_AUTHORING_TEST_DOUBLE_RECEIPT_FORBIDDEN"
            )
        if _map_name(receipt.get("server_identity", {}).get("map_name", "")) != _map_name(
            runtime.get("route_binding", {}).get("town", "")
        ):
            raise Stage6ALiveResolutionRequired(
                "STAGE6A_AUTHORING_RECEIPT_TOWN_BINDING_INVALID"
            )
        if receipt.get("server_identity", {}).get("no_rendering_mode") is not False:
            raise Stage6ALiveResolutionRequired(
                "STAGE6A_AUTHORING_RECEIPT_RENDERING_DISABLED"
            )
        if not (
            receipt.get("physical_spawn_verified") is True
            and receipt.get("semantic_fidelity", {}).get("status") == "VERIFIED"
            and receipt.get("handler_execution_receipt", {}).get("verified") is False
            and receipt.get("promotion_ready") is False
            and receipt.get("final_status")
            == "BLOCKED_HANDLER_EXECUTION_NOT_VERIFIED"
        ):
            raise Stage6ALiveResolutionRequired(
                "STAGE6A_AUTHORING_RECEIPT_PRECONDITIONS_INVALID"
            )
        return receipt, selected_seed
    manifest_path = _safe_file(
        root, LIVE_MANIFEST_NAME, "STAGE6A_LIVE_PROMOTION_MANIFEST_MISSING"
    )
    manifest = _load_json_bytes(
        manifest_path.read_bytes(), "STAGE6A_LIVE_PROMOTION_MANIFEST_INVALID"
    )
    if manifest.get("schema_version") != LIVE_MANIFEST_SCHEMA_VERSION:
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_MANIFEST_SCHEMA_INVALID")
    if not _verify_content_address(manifest, "manifest_payload_sha256"):
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_MANIFEST_CONTENT_HASH_INVALID")
    records = manifest.get("records")
    if (
        not isinstance(records, list)
        or len(records) != 96
        or manifest.get("runtime_fixture_count") != 24
        or manifest.get("seed_configuration_count") != 96
    ):
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_MANIFEST_COUNTS_INVALID")
    matching = [
        record
        for record in records
        if isinstance(record, dict)
        if record.get("runtime_fixture_id") == runtime["runtime_fixture_id"]
        and record.get("selected_seed") == selected_seed
    ]
    if len(matching) != 1:
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_RECEIPT_BINDING_COUNT_INVALID")
    index_record = matching[0]
    receipt_path = _safe_file(
        root, index_record.get("receipt_path", ""), "STAGE6A_LIVE_RECEIPT_MISSING"
    )
    receipt = _load_json_bytes(
        receipt_path.read_bytes(), "STAGE6A_LIVE_RECEIPT_JSON_INVALID"
    )
    if receipt.get("schema_version") != LIVE_RECEIPT_SCHEMA_VERSION:
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_RECEIPT_SCHEMA_INVALID")
    if not _verify_content_address(receipt, "receipt_payload_sha256"):
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_RECEIPT_CONTENT_HASH_INVALID")
    if (
        receipt_path.stem != receipt["receipt_payload_sha256"]
        or receipt["receipt_payload_sha256"]
        != index_record.get("receipt_payload_sha256")
    ):
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_RECEIPT_PATH_HASH_INVALID")
    if (
        receipt.get("runtime_fixture_id") != runtime["runtime_fixture_id"]
        or receipt.get("runtime_manifest_sha256") != runtime_sha256
        or receipt.get("selected_seed") != selected_seed
    ):
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_RECEIPT_RUNTIME_BINDING_INVALID")
    route_path = _safe_file(
        generated_root,
        runtime["route_binding"]["derived_route_path"],
        "STAGE6A_DERIVED_ROUTE_MISSING",
    )
    if hashlib.sha256(route_path.read_bytes()).hexdigest() != receipt.get(
        "derived_route_sha256"
    ):
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_RECEIPT_ROUTE_BINDING_INVALID")
    if receipt.get("provenance", {}).get("evidence_origin") != LIVE_EVIDENCE_ORIGIN:
        raise Stage6ALiveResolutionRequired("STAGE6A_TEST_DOUBLE_RECEIPT_FORBIDDEN")
    if _map_name(receipt.get("server_identity", {}).get("map_name", "")) != _map_name(
        runtime.get("route_binding", {}).get("town", "")
    ):
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_RECEIPT_TOWN_BINDING_INVALID")
    if receipt.get("server_identity", {}).get("no_rendering_mode") is not False:
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_RECEIPT_RENDERING_DISABLED")
    gates = receipt.get("gate_receipts", {})
    if not all(
        gates.get(name) is True
        for name in (
            "live_blueprint_resolution",
            "live_map_navmesh_snap",
            "live_spawn_destroy",
            "live_semantic_scene_realization",
            "scenario_class_registration",
            "handler_spawn_tick_cleanup",
            "route_and_goal_nonleakage",
        )
    ):
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_RECEIPT_GATE_INCOMPLETE")
    if (
        receipt.get("physical_spawn_verified") is not True
        or receipt.get("semantic_fidelity", {}).get("status") != "VERIFIED"
        or receipt.get("handler_execution_receipt", {}).get("verified") is not True
        or receipt.get("promotion_ready") is not True
        or receipt.get("final_status") != "PASS_LIVE_PROMOTION_READY"
    ):
        raise Stage6ALiveResolutionRequired("STAGE6A_LIVE_RECEIPT_NOT_PROMOTED")
    return receipt, selected_seed


def _transform(value):
    return carla.Transform(
        carla.Location(x=float(value["x"]), y=float(value["y"]), z=float(value["z"])),
        carla.Rotation(
            yaw=float(value["yaw"]),
            pitch=float(value.get("pitch", 0.0)),
            roll=float(value.get("roll", 0.0)),
        ),
    )


def _map_name(value):
    result = str(value).rsplit("/", 1)[-1]
    return result[:-4] if result.endswith("_Opt") else result


class _DriveClarifyTimeline(py_trees.behaviour.Behaviour):
    def __init__(self, owner):
        super().__init__(name="DriveClarifyReceiptBoundTimeline")
        self._owner = owner
        self._start_time = None
        self._signal_delivered = False

    def initialise(self):
        self._start_time = GameTime.get_time()
        self._owner._timeline_initialised = True
        self._owner._activate_post_trigger()

    def update(self):
        self._owner._timeline_update_count += 1
        elapsed = GameTime.get_time() - self._start_time
        self._owner._deliver_due_runtime_signal(elapsed, self._signal_delivered)
        if self._owner._runtime_signal_delivered:
            self._signal_delivered = True
        if self._owner._required_actor_lost():
            self._owner._termination_reason = "REQUIRED_ACTOR_LOST"
            self._owner._cleanup_owned_actors()
            return py_trees.common.Status.FAILURE
        endpoint = self._owner._runtime_fixture["termination"]["success_condition"][
            "route_endpoint_world_xyz"
        ]
        ego_location = self._owner.ego_vehicles[0].get_location()
        distance = math.sqrt(
            (float(ego_location.x) - float(endpoint[0])) ** 2
            + (float(ego_location.y) - float(endpoint[1])) ** 2
            + (float(ego_location.z) - float(endpoint[2])) ** 2
        )
        if distance <= 3.0:
            self._owner._termination_reached = True
            self._owner._termination_reason = "ROUTE_ENDPOINT_REACHED"
            self._owner._cleanup_owned_actors()
            return py_trees.common.Status.SUCCESS
        timeout = float(
            self._owner._runtime_fixture["termination"]["timeout"][
                "scenario_hard_limit_seconds"
            ]
        )
        if elapsed >= timeout:
            self._owner._termination_reason = "SCENARIO_HARD_LIMIT_REACHED"
            self._owner._cleanup_owned_actors()
            return py_trees.common.Status.FAILURE
        return py_trees.common.Status.RUNNING

    def terminate(self, new_status):
        if new_status != py_trees.common.Status.RUNNING:
            self._owner._cleanup_owned_actors()
        super().terminate(new_status)


class DriveClarifyPaperMVPScenario(BasicScenario):
    """ScenarioRunner type activated only by a verified opaque live receipt."""

    def __init__(
        self,
        world,
        ego_vehicles,
        config,
        randomize=False,
        debug_mode=False,
        criteria_enable=True,
        timeout=10000,
    ):
        del randomize
        self.timeout = timeout
        self._world = world
        self._runtime_fixture, runtime_sha, generated_root = _load_bound_runtime(config)
        self._live_receipt, self._selected_seed = _load_live_receipt(
            self._runtime_fixture, runtime_sha, generated_root
        )
        if _map_name(world.get_map().name) != _map_name(
            self._live_receipt["server_identity"]["map_name"]
        ):
            raise Stage6ALiveResolutionRequired("STAGE6A_HANDLER_WORLD_MAP_MISMATCH")
        if bool(getattr(world.get_settings(), "no_rendering_mode", False)):
            raise Stage6ALiveResolutionRequired("STAGE6A_HANDLER_WORLD_RENDERING_DISABLED")
        self._receipt_bindings = {
            str(record["binding_id"]): record
            for record in self._live_receipt["actor_bindings"]
        }
        if len(self._receipt_bindings) != len(self._live_receipt["actor_bindings"]):
            raise Stage6ALiveResolutionRequired("STAGE6A_HANDLER_BINDING_IDS_DUPLICATE")
        ego_receipt = self._receipt_bindings.get("ego", {})
        expected_ego_type = ego_receipt.get("blueprint_resolution", {}).get(
            "resolved_blueprint_id"
        )
        if not ego_vehicles or ego_vehicles[0].type_id != expected_ego_type:
            raise Stage6ALiveResolutionRequired("STAGE6A_HANDLER_EGO_BLUEPRINT_MISMATCH")
        self._owned_actors = []
        self._actor_state = []
        self._cleanup_complete = False
        self._runtime_signal_delivered = False
        self._timeline_initialised = False
        self._timeline_update_count = 0
        self._termination_reached = False
        self._termination_reason = None
        self._lost_required_actor_evidence = []
        opaque_suffix = self._runtime_fixture["runtime_fixture_id"].replace("-", "_")
        self.raw_instruction_blackboard_key = "DriveClarifyRawInstruction_" + opaque_suffix
        self.runtime_signal_blackboard_key = "DriveClarifyRuntimeSignal_" + opaque_suffix
        self.observable_condition_blackboard_key = (
            "DriveClarifyObservableCondition_" + opaque_suffix
        )
        py_trees.blackboard.Blackboard().set(
            self.raw_instruction_blackboard_key,
            self._runtime_fixture["raw_instruction"],
            overwrite=True,
        )
        super().__init__(
            EXPECTED_TYPE,
            ego_vehicles,
            config,
            world,
            debug_mode,
            criteria_enable=criteria_enable,
        )

    def _spawn_receipt_actor(self, binding, role_name, physics, autopilot):
        if binding.get("physical_spawn_verified") is not True:
            raise Stage6ALiveResolutionRequired("STAGE6A_HANDLER_BINDING_NOT_PHYSICALLY_VERIFIED")
        resolution = binding.get("blueprint_resolution", {})
        transform_value = binding.get("snap", {}).get("world_transform")
        blueprint_id = resolution.get("resolved_blueprint_id")
        if not blueprint_id or transform_value is None:
            raise Stage6ALiveResolutionRequired("STAGE6A_HANDLER_BINDING_RESOLUTION_MISSING")
        blueprint = self._world.get_blueprint_library().find(blueprint_id)
        attributes = dict(binding.get("spawn_probe", {}).get("applied_attributes", {}))
        attributes["role_name"] = role_name
        for key, value in attributes.items():
            if blueprint.has_attribute(key):
                blueprint.set_attribute(key, str(value))
        actor = self._world.try_spawn_actor(blueprint, _transform(transform_value))
        if actor is None:
            raise Stage6ALiveResolutionRequired("STAGE6A_HANDLER_TRY_SPAWN_FAILED")
        # Take ownership before any post-spawn operation that can raise.  The
        # outer initialization guard can now always retry destruction.
        state = {
            "actor": actor,
            "binding_id": str(binding.get("binding_id", "UNKNOWN")),
            "actor_id": int(actor.id),
            "actor_type_id": str(actor.type_id),
            "physics_enabled": bool(physics),
            "autopilot_enabled": bool(autopilot),
        }
        self._owned_actors.append(actor)
        self._actor_state.append(state)
        if actor.type_id != blueprint_id:
            raise Stage6ALiveResolutionRequired("STAGE6A_HANDLER_SPAWNED_BLUEPRINT_MISMATCH")
        if hasattr(actor, "set_simulate_physics"):
            actor.set_simulate_physics(False)
        return actor

    def _initialize_actors(self, config):
        del config
        try:
            for entity in self._runtime_fixture["entities"]:
                binding = self._receipt_bindings.get(str(entity["entity_id"]))
                if binding is None:
                    raise Stage6ALiveResolutionRequired(
                        "STAGE6A_HANDLER_ENTITY_RECEIPT_MISSING"
                    )
                semantic = binding.get("semantic_fidelity", {})
                if semantic.get("status") != "VERIFIED":
                    raise Stage6ALiveResolutionRequired(
                        "STAGE6A_HANDLER_ENTITY_SEMANTIC_FIDELITY_NOT_VERIFIED"
                    )
                mode = semantic.get("realization_mode")
                if mode == "EXISTING_WORLD_FEATURE":
                    continue
                if mode in {
                    "COMPOSITE_MEASURED_CLEARANCE_GATEWAY",
                    "SPAWNED_ASSET_CLUSTER",
                }:
                    components = binding.get("composite_components")
                    if not isinstance(components, list) or not components:
                        raise Stage6ALiveResolutionRequired(
                            "STAGE6A_HANDLER_COMPONENT_REALIZATION_MISSING"
                        )
                    for component in components:
                        self._spawn_receipt_actor(
                            component,
                            entity["spawn_state"]["role_name"]
                            + "_"
                            + str(component["component_id"]).lower(),
                            False,
                            False,
                        )
                    continue
                if mode != "SPAWNED_BLUEPRINT_ACTOR":
                    raise Stage6ALiveResolutionRequired(
                        "STAGE6A_HANDLER_ENTITY_REALIZATION_MODE_INVALID"
                    )
                actor = self._spawn_receipt_actor(
                    binding,
                    entity["spawn_state"]["role_name"],
                    entity["spawn_state"]["physics_enabled"],
                    entity["spawn_state"]["autopilot_enabled"],
                )
                self.other_actors.append(actor)
            for slot in self._runtime_fixture["background"]["spawn_slots"]:
                binding = self._receipt_bindings.get(str(slot["slot_id"]))
                if binding is None:
                    raise Stage6ALiveResolutionRequired(
                        "STAGE6A_HANDLER_BACKGROUND_RECEIPT_MISSING"
                    )
                actor = self._spawn_receipt_actor(
                    binding,
                    "driveclarify_" + str(slot["slot_id"]).lower(),
                    True,
                    slot["kind"] == "background_vehicle",
                )
                self.other_actors.append(actor)
        except Exception:
            self._cleanup_owned_actors()
            raise

    def _activate_post_trigger(self):
        for state in self._actor_state:
            actor = state["actor"]
            if hasattr(actor, "set_simulate_physics"):
                actor.set_simulate_physics(state["physics_enabled"])
            if state["autopilot_enabled"] and isinstance(actor, carla.Vehicle):
                actor.set_autopilot(True, CarlaDataProvider.get_traffic_manager_port())
        py_trees.blackboard.Blackboard().set(
            self.observable_condition_blackboard_key,
            {
                "active": True,
                "physical_scene_contract": self._runtime_fixture[
                    "physical_scene_contract"
                ],
            },
            overwrite=True,
        )

    def _deliver_due_runtime_signal(self, elapsed, already_delivered):
        if already_delivered:
            return
        scheduled = next(
            (
                event
                for event in self._runtime_fixture["event_timeline"]
                if event["event_id"] == "EV04_SCHEDULED_RUNTIME_SIGNAL"
            ),
            None,
        )
        if scheduled is None:
            return
        payload = scheduled["delivery_by_allowed_seed"].get(str(self._selected_seed))
        if not isinstance(payload, dict):
            raise Stage6ALiveResolutionRequired(
                "STAGE6A_HANDLER_RUNTIME_SIGNAL_SEED_BINDING_MISSING"
            )
        due = float(payload["delivery_seconds_from_decision"])
        if elapsed < due:
            return
        py_trees.blackboard.Blackboard().set(
            self.runtime_signal_blackboard_key,
            {
                "delivered": True,
                "delivery_simulation_time": GameTime.get_time(),
                "payload_id": payload["payload_id"],
                "runtime_payload": payload["runtime_payload"],
                "signal_source": scheduled["signal_source"],
            },
            overwrite=True,
        )
        self._runtime_signal_delivered = True

    def _required_actor_lost(self):
        lost = [
            state
            for state in self._actor_state
            if not state["actor"].is_alive
        ]
        if lost and not self._lost_required_actor_evidence:
            self._lost_required_actor_evidence = [
                {
                    "binding_id": state["binding_id"],
                    "actor_id": state["actor_id"],
                    "actor_type_id": state["actor_type_id"],
                    "physics_enabled": state["physics_enabled"],
                    "autopilot_enabled": state["autopilot_enabled"],
                }
                for state in lost
            ]
        return bool(lost)

    def _cleanup_owned_actors(self):
        if getattr(self, "_cleanup_complete", True):
            return
        for state in getattr(self, "_actor_state", []):
            actor = state["actor"]
            try:
                if state["autopilot_enabled"] and actor.is_alive:
                    actor.set_autopilot(False, CarlaDataProvider.get_traffic_manager_port())
            except Exception:
                pass
        survivors = []
        owned = [
            actor
            for actor in reversed(getattr(self, "_owned_actors", []))
            if actor is not None and actor.is_alive
        ]
        client = None
        try:
            client = CarlaDataProvider.get_client()
        except Exception:
            client = None
        if client is not None and owned and hasattr(carla, "command"):
            try:
                responses = client.apply_batch_sync(
                    [carla.command.DestroyActor(int(actor.id)) for actor in owned],
                    False,
                )
                if len(responses) != len(owned):
                    survivors = list(reversed(owned))
                else:
                    survivors = [
                        actor
                        for response, actor in zip(responses, owned)
                        if response.has_error()
                    ]
                    survivors.reverse()
            except Exception:
                survivors = list(reversed(owned))
        else:
            for actor in owned:
                try:
                    destroyed = actor.destroy()
                    if destroyed is False:
                        survivors.append(actor)
                except Exception:
                    survivors.append(actor)
            survivors.reverse()
        survivors.reverse()
        survivor_ids = {id(actor) for actor in survivors}
        self._owned_actors = survivors
        self._actor_state = [
            state
            for state in getattr(self, "_actor_state", [])
            if id(state["actor"]) in survivor_ids
        ]
        self.other_actors = [
            actor
            for actor in getattr(self, "other_actors", [])
            if id(actor) in survivor_ids
        ]
        self._cleanup_failure_actor_ids = [
            int(getattr(actor, "id", -1)) for actor in survivors
        ]
        self._cleanup_complete = not survivors

    def _create_behavior(self):
        return _DriveClarifyTimeline(self)

    def _create_test_criteria(self):
        # RouteScenario owns the global collision/rule/route-completion criteria.
        return []

    def remove_all_actors(self):
        self._cleanup_owned_actors()

    def __del__(self):
        self._cleanup_owned_actors()
