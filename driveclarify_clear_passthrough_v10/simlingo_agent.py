#!/usr/bin/env python3
"""Native SimLingo integration for CLEAR-PassThrough Safe-Replan V10.

CLEAR and NATIVE_SIMLINGO call the unchanged LingoAgent tick, model, PID and
VehicleControl writer.  Only an AMBIGUOUS gate result constructs alternatives.
Resolved navigation is installed through SimLingoOnlineRouteUpdateOwner; this
module never synthesizes a motion trajectory or steering command.
"""

from __future__ import annotations

from dataclasses import asdict
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import time

import carla
import numpy as np
from agents.navigation.local_planner import RoadOption
from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

from agent_simlingo import LingoAgent

from driveclarify_clear_passthrough_v10.consequence import (
    MaterialRouteConsequenceEvaluator,
)
from driveclarify_clear_passthrough_v10.contracts import (
    AuthoritativeRoute,
    CandidateInterpretation,
    CandidateRoute,
    ConsequenceDecision,
    EgoState,
    GateInput,
    GroundingEvidence,
    ModelPlan,
    PassengerAnswer,
    PolicyAction,
    RoutePoint,
    TransitionDisposition,
    assert_runtime_label_firewall,
    canonical_sha256,
)
from driveclarify_clear_passthrough_v10.oracle import DurableAskWriter
from driveclarify_clear_passthrough_v10.plan_acceptance import accept_simlingo_plan
from driveclarify_clear_passthrough_v10.replan import RouteTransitionManager
from driveclarify_clear_passthrough_v10.supervisor import (
    ClearPassThroughSafeReplanSupervisor,
)


EXPECTED_A1_SHA256 = "cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044"
CONFIG_SCHEMA = "driveclarify.v10.native-runtime-config.v1"
VALID_MODES = frozenset({"NATIVE_SIMLINGO", "DRIVECLARIFY", "NO_CLARIFICATION"})
ROAD_OPTIONS = {
    "LEFT": RoadOption.LEFT,
    "RIGHT": RoadOption.RIGHT,
    "STRAIGHT": RoadOption.STRAIGHT,
    "LANEFOLLOW": RoadOption.LANEFOLLOW,
    "CHANGELANELEFT": RoadOption.CHANGELANELEFT,
    "CHANGELANERIGHT": RoadOption.CHANGELANERIGHT,
}


def get_entry_point():
    return "DriveClarifyV10SimLingoAgent"


def _sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    raw = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    with temporary.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def _input_frame(input_data):
    frames = {int(value[0]) for value in input_data.values()}
    if len(frames) != 1:
        raise RuntimeError("SENSOR_FRAME_NOT_SYNCHRONIZED")
    return next(iter(frames))


def _native_route_projection(route):
    return tuple(
        (
            float(transform.location.x),
            float(transform.location.y),
            float(transform.location.z),
            getattr(option, "name", str(option)),
        )
        for transform, option in route
    )


def _route_points(route):
    return tuple(
        RoutePoint(x=x, y=y, z=z, road_option=option)
        for x, y, z, option in _native_route_projection(route)
    )


def _native_route(points):
    return tuple(
        (
            carla.Transform(carla.Location(x=point.x, y=point.y, z=point.z)),
            ROAD_OPTIONS[point.road_option],
        )
        for point in points
    )


def _xyz_from_row(row):
    if isinstance(row, dict):
        values = row.get("xyz_hex", row.get("xyz"))
        option = row.get("road_option", "LANEFOLLOW")
    else:
        values = row[:3]
        option = row[3] if len(row) > 3 else "LANEFOLLOW"
    if values is None or len(values) != 3:
        raise ValueError("RUNTIME_ROUTE_ROW_XYZ_INVALID")
    xyz = tuple(
        float.fromhex(value) if isinstance(value, str) and value.startswith(("0x", "-0x")) else float(value)
        for value in values
    )
    return RoutePoint(xyz[0], xyz[1], xyz[2], str(option))


def _nearest_index(points, xyz):
    values = [
        math.sqrt(
            (point.x - float(xyz[0])) ** 2
            + (point.y - float(xyz[1])) ** 2
            + (point.z - float(xyz[2])) ** 2
        )
        for point in points
    ]
    return min(range(len(values)), key=values.__getitem__)


class DriveClarifyV10SimLingoAgent(LingoAgent):
    """One real SimLingo/controller path under the V10 supervisory contract."""

    def _load_v10_config(self):
        if hasattr(self, "_v10_config"):
            return
        path = Path(os.environ["DRIVECLARIFY_V10_CONFIG"]).resolve()
        expected = os.environ.get("DRIVECLARIFY_V10_CONFIG_SHA256")
        if expected is None or _sha_file(path) != expected:
            raise RuntimeError("V10_CONFIG_HASH_MISMATCH")
        config = json.loads(path.read_text(encoding="utf-8"))
        if config.get("schema") != CONFIG_SCHEMA:
            raise RuntimeError("V10_CONFIG_SCHEMA_INVALID")
        if config.get("mode") not in VALID_MODES:
            raise RuntimeError("V10_MODE_INVALID")
        checkpoint = Path(config["checkpoint"]).resolve()
        if _sha_file(checkpoint) != config.get("checkpoint_sha256"):
            raise RuntimeError("V10_CHECKPOINT_HASH_MISMATCH")
        if config["checkpoint_sha256"] != EXPECTED_A1_SHA256:
            raise RuntimeError("V10_A1_NOT_FROZEN")
        if config.get("a1_trainable_parameters") != 896:
            raise RuntimeError("V10_A1_PARAMETER_CONTRACT_CHANGED")
        if config.get("training_performed") is not False:
            raise RuntimeError("V10_TRAINING_FORBIDDEN")
        method_input = dict(config.get("method_input", {}))
        assert_runtime_label_firewall(method_input)
        if not isinstance(method_input.get("instruction"), str):
            raise RuntimeError("V10_INSTRUCTION_REQUIRED")
        self._v10_config_path = path
        self._v10_config = config
        self._method_input = method_input
        self._checkpoint_path = checkpoint
        source_path = method_input.get("route_source")
        self._route_source = (
            {}
            if source_path is None
            else json.loads(Path(source_path).resolve().read_text(encoding="utf-8"))
        )

    def set_global_plan(self, global_plan_gps, global_plan_world_coord):
        self._load_v10_config()
        self._official_global_plan_gps_object_id = id(global_plan_gps)
        self._official_global_plan_world_object_id = id(global_plan_world_coord)
        self._official_dense_world_route = tuple(global_plan_world_coord)
        self._official_dense_points = _route_points(global_plan_world_coord)
        self._official_input_route_sha256 = canonical_sha256(
            _native_route_projection(global_plan_world_coord)
        )
        # The standard evaluator supplies the dense plan but, unlike SimLingo's
        # local evaluator, does not populate the two dense aliases consumed by
        # LingoAgent._init.  Bind those aliases to the exact official objects;
        # this is compatibility plumbing, not a route reconstruction.
        self.org_dense_route_gps = global_plan_gps
        self.org_dense_route_world_coord = global_plan_world_coord
        # The CLEAR parity invariant lives here: no reconstruction, canonicalization,
        # alternative route lookup, target override, or RoadOption override precedes
        # the exact native implementation call.
        LingoAgent.set_global_plan(self, global_plan_gps, global_plan_world_coord)
        self._native_set_global_plan_called_once = True
        self._write_navigation_contract_if_ready()

    def setup(self, path_to_conf_file, route_index=None, traffic_manager=None):
        del traffic_manager
        self._load_v10_config()
        if Path(path_to_conf_file).resolve() != self._v10_config_path:
            raise RuntimeError("V10_AGENT_CONFIG_NOT_FROZEN_CONFIG")
        output = Path(os.environ["DRIVECLARIFY_V10_OWNER_DIR"]).resolve()
        if output.exists():
            raise RuntimeError("V10_OWNER_DIR_MUST_BE_FRESH")
        output.mkdir(parents=True)
        self._output = output
        self._mode = self._v10_config["mode"]
        self._status = "SETUP"
        self._supervisor = None
        self._supervisor_result = None
        self._gate_frame = None
        self._window_frame = None
        self._trajectory = []
        self._forward_rows = []
        self._pending_model_output = None
        self._criteria = None
        self._route_commit_receipt = None
        self._post_switch_plan_acceptance = None
        self._selected_route = None
        self._decision = None
        self._route_transaction_count = 0
        self._a1_active_forward_count = 0
        self._completed = False
        self._spawned_case_actors = []
        LingoAgent.setup(
            self,
            str(self._checkpoint_path) + "+v10_" + self._v10_config["run_id"],
            route_index=None,
        )
        self.custom_prompt = self._method_input["instruction"]
        self.user_flag = 1
        self._spawn_public_runtime_actors()
        self._write_status()
        self._write_navigation_contract_if_ready()

    def _spawn_public_runtime_actors(self):
        actors = tuple(self._method_input.get("runtime_actors", ()))
        if not actors:
            return
        world = CarlaDataProvider.get_world()
        if world is None:
            return
        for row in actors:
            blueprint = world.get_blueprint_library().find(row["blueprint"])
            role_name = "v10_runtime_" + str(row["runtime_track_id"])
            if blueprint.has_attribute("role_name"):
                blueprint.set_attribute("role_name", role_name)
            for key, value in row.get("attributes", {}).items():
                if blueprint.has_attribute(key):
                    blueprint.set_attribute(key, str(value))
            pose = row["pose"]
            location, rotation = pose["location"], pose["rotation"]
            actor = world.try_spawn_actor(
                blueprint,
                carla.Transform(
                    carla.Location(
                        float(location["x"]),
                        float(location["y"]),
                        float(location["z"]),
                    ),
                    carla.Rotation(
                        float(rotation.get("pitch", 0.0)),
                        float(rotation.get("yaw", 0.0)),
                        float(rotation.get("roll", 0.0)),
                    ),
                ),
            )
            if actor is not None:
                self._spawned_case_actors.append(actor)

    def _init(self):
        LingoAgent._init(self)
        if self._mode != "NATIVE_SIMLINGO":
            self._supervisor = ClearPassThroughSafeReplanSupervisor(
                candidate_generator=self._generate_interpretations,
                route_binder=self._bind_candidate_route,
                consequence_evaluator=self._evaluate_consequences,
                ask_writer=DurableAskWriter(
                    self._output / "oracle_exchange"
                ).write,
                answer_provider=self._read_passenger_answer,
                transition_manager=RouteTransitionManager(self._commit_resolved_route),
            )
        self._status = "NATIVE_SIMLINGO_INITIALIZED"
        self._write_status()

    def get_metric_info(self):
        hero = CarlaDataProvider.get_hero_actor()
        transform = hero.get_transform()

        def vector(value, rotation=False):
            return (
                [value.roll, value.pitch, value.yaw]
                if rotation
                else [value.x, value.y, value.z]
            )

        return {
            "acceleration": vector(hero.get_acceleration()),
            "angular_velocity": vector(hero.get_angular_velocity()),
            "forward_vector": vector(transform.get_forward_vector()),
            "right_vector": vector(transform.get_right_vector()),
            "location": vector(transform.location),
            "rotation": vector(transform.rotation, rotation=True),
        }

    def _hero_state(self, frame):
        hero = CarlaDataProvider.get_hero_actor()
        transform = hero.get_transform()
        velocity = hero.get_velocity()
        speed = math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2)
        return EgoState(
            x=float(transform.location.x),
            y=float(transform.location.y),
            z=float(transform.location.z),
            yaw_degrees=float(transform.rotation.yaw),
            speed_mps=float(speed),
            frame=int(frame),
        )

    def _at_gate_anchor(self):
        anchor = self._method_input.get("observation_anchor_xyz")
        if anchor is None:
            return True
        hero = CarlaDataProvider.get_hero_actor().get_location()
        distance = math.sqrt(
            (float(hero.x) - float(anchor[0])) ** 2
            + (float(hero.y) - float(anchor[1])) ** 2
            + (float(hero.z) - float(anchor[2])) ** 2
        )
        return distance <= float(self._method_input.get("anchor_capture_distance_m", 2.0))

    def _route_rows_for_option(self, option):
        if "route_points" in option:
            rows = option["route_points"]
        else:
            rows = self._route_source[option["route_source_field"]]
        points = tuple(_xyz_from_row(row) for row in rows)
        if len(points) < 2:
            raise RuntimeError("V10_CANDIDATE_ROUTE_TOO_SHORT")
        return points

    def _live_reasonable_option_count(self):
        options = tuple(self._method_input.get("alternatives", ()))
        valid = 0
        for option in options:
            if option.get("available", True) is not True:
                continue
            points = self._route_rows_for_option(option)
            if len(points) >= 2 and all(
                math.isfinite(value)
                for point in points
                for value in (point.x, point.y, point.z)
            ):
                valid += 1
        return valid

    def _gate_input(self, frame):
        grounding = GroundingEvidence(
            reasonable_interpretation_count=self._live_reasonable_option_count(),
            evidence_status=str(
                self._method_input.get("grounding_evidence_status", "UNKNOWN")
            ),
            source=str(
                self._method_input.get(
                    "grounding_evidence_source", "CURRENT_RUNTIME_SCENE"
                )
            ),
            observation_frame=int(frame),
            current_frame=int(frame),
            reason_codes=tuple(self._method_input.get("grounding_reason_codes", ())),
        )
        map_object = CarlaDataProvider.get_map()
        return GateInput(
            instruction=self._method_input["instruction"],
            grounding=grounding,
            runtime_evidence={
                "map_name": str(getattr(map_object, "name", "UNKNOWN_MAP")),
                "runtime_reasonable_option_count": grounding.reasonable_interpretation_count,
                "route_owner": "SIMLINGO",
            },
        )

    def _generate_interpretations(self, gate_input):
        del gate_input
        return tuple(
            CandidateInterpretation(
                candidate_id=str(option["candidate_id"]),
                description=str(option["description"]),
                evidence_id=str(option["evidence_id"]),
            )
            for option in self._method_input.get("alternatives", ())
        )

    def _route_from_points(self, route_id, points, source_frame, option, commitment_index=None):
        hero_xyz = self._hero_state(source_frame).xyz
        start = max(0, _nearest_index(points, hero_xyz) - 1)
        suffix = tuple(points[start:])
        if len(suffix) < 2:
            raise RuntimeError("V10_ROUTE_SUFFIX_TOO_SHORT")
        relative_commitment = None
        if commitment_index is not None:
            relative_commitment = max(0, int(commitment_index) - start)
            relative_commitment = min(relative_commitment, len(suffix) - 1)
        target_index = min(2, len(suffix) - 1)
        return AuthoritativeRoute(
            route_id=route_id,
            points=suffix,
            target_point=(suffix[target_index].x, suffix[target_index].y),
            road_option=str(suffix[target_index].road_option),
            destination_xyz=suffix[-1].xyz,
            source_frame=int(source_frame),
            connector_id=option.get("connector_id"),
            commitment_point_index=relative_commitment,
            full_route_owner="SIMLINGO_OFFICIAL_OR_PUBLIC_RUNTIME_ROUTE",
            active_suffix_owner="SIMLINGO_ONLINE_ROUTE_UPDATE_OWNER",
        )

    def _bind_candidate_route(self, candidate):
        option = next(
            row
            for row in self._method_input["alternatives"]
            if row["candidate_id"] == candidate.candidate_id
        )
        points = self._route_rows_for_option(option)
        route = self._route_from_points(
            "candidate-" + candidate.candidate_id + "-" + canonical_sha256(
                [point.xyz for point in points]
            )[:16],
            points,
            self._latest_supervision_frame,
            option,
            option.get("commitment_point_index"),
        )
        return CandidateRoute(candidate=candidate, route=route, simlingo_preview_plan=None)

    def _current_route(self, frame):
        points = self._official_dense_points
        option = {
            "connector_id": self._method_input.get("current_connector_id", "NATIVE"),
        }
        return self._route_from_points(
            "native-" + self._official_input_route_sha256[:20],
            points,
            frame,
            option,
            self._method_input.get("current_commitment_point_index"),
        )

    def _evaluate_consequences(self, routes):
        if self._mode == "NO_CLARIFICATION":
            return ConsequenceDecision(
                action=PolicyAction.ACT,
                selected_candidate_id=routes[0].candidate.candidate_id,
                question=None,
                reason_codes=("FROZEN_DETERMINISTIC_RANK_ONE_NO_CLARIFICATION",),
            )
        return MaterialRouteConsequenceEvaluator().evaluate(routes)

    def _read_passenger_answer(self, receipt):
        path = self._output / "oracle_exchange" / "ORACLE_ANSWER.json"
        if not path.is_file():
            return None
        answer = json.loads(path.read_text(encoding="utf-8"))
        if answer.get("query_id") != receipt.query_id:
            raise RuntimeError("V10_ORACLE_ANSWER_QUERY_ID_MISMATCH")
        if answer.get("ask_receipt_id") != receipt.receipt_id:
            raise RuntimeError("V10_ORACLE_ANSWER_RECEIPT_ID_MISMATCH")
        return PassengerAnswer(
            query_id=receipt.query_id,
            selected_candidate_id=str(answer["selected_candidate_id"]),
        )

    def _commit_resolved_route(self, route):
        destination = route.destination_xyz
        planner_destination = self._online_route_update_owner.world_to_route_planner_xyz(
            destination
        )
        self._online_route_update_owner.install_reconnected_route(
            _native_route(route.points),
            global_destination_identity=canonical_sha256(
                [float(value).hex() for value in destination]
            ),
            global_destination_planner_endpoint=planner_destination,
        )
        receipt = self._online_route_update_owner.last_installation_receipt
        self._route_commit_receipt = dict(receipt)
        self._route_transaction_count += 1
        self._selected_route = route
        _atomic_json(self._output / "ONLINE_ROUTE_INSTALL_RECEIPT.json", receipt)
        return receipt

    def _advance_supervision(self, frame):
        if self._mode == "NATIVE_SIMLINGO":
            if self._gate_frame is None and self._at_gate_anchor():
                self._gate_frame = int(frame)
                self._window_frame = int(frame)
                self._decision = "NATIVE_SIMLINGO"
                self._status = "NATIVE_SIMLINGO_WINDOW_STARTED"
            return
        if self._supervisor_result is not None:
            transaction = self._supervisor_result.transaction
            transition = self._supervisor_result.transition
            if transaction is not None or (
                transition is not None
                and transition.disposition is TransitionDisposition.DEFER_COMMIT
            ):
                if transaction is None:
                    self._supervisor_result = self._supervisor.reevaluate_pending(
                        native_input=self.DrivingInput,
                        ego=self._hero_state(frame),
                        gate_result=self._supervisor_result.gate,
                    )
                    self._persist_supervision()
                    if self._supervisor_result.transaction is not None:
                        self._window_frame = int(frame)
                return
            if self._supervisor_result.status.startswith("ASK_DURABLE_WAITING"):
                pass
            else:
                return
        if not self._at_gate_anchor():
            return
        if self._gate_frame is None:
            self._gate_frame = int(frame)
        self._latest_supervision_frame = int(frame)
        current = self._current_route(frame)
        self._supervisor_result = self._supervisor.process(
            gate_input=self._gate_input(frame),
            native_input=self.DrivingInput,
            ego=self._hero_state(frame),
            current_route=current,
            observation_id="v10-frame-" + str(frame),
        )
        self._decision = (
            None
            if self._supervisor_result.policy_action is None
            else self._supervisor_result.policy_action.value
        )
        self._persist_supervision()
        status = self._supervisor_result.status
        if not status.startswith("ASK_DURABLE_WAITING") and not status.endswith(
            "DEFER_COMMIT"
        ):
            self._window_frame = int(frame)
        self._status = status

    def _persist_supervision(self):
        result = self._supervisor_result
        if result is None:
            return
        payload = {
            "schema": "driveclarify.v10.runtime-supervision-receipt.v1",
            "run_id": self._v10_config["run_id"],
            "mode": self._mode,
            "status": result.status,
            "gate": asdict(result.gate),
            "pass_through": result.pass_through,
            "authority_preserved": result.authority_preserved,
            "policy_action": None if result.policy_action is None else result.policy_action.value,
            "selected_candidate_id": result.selected_candidate_id,
            "transition": None if result.transition is None else asdict(result.transition),
            "transaction": None if result.transaction is None else asdict(result.transaction),
            "counters": dict(result.counters),
            "a1_semantics": "AUTHORITATIVE_NAVIGATION_CONDITION_CHANGED_MID_EPISODE",
            "governor_active_ticks": 0,
        }
        _atomic_json(self._output / "V10_SUPERVISION_RECEIPT.json", payload)

    def tick(self, input_data):
        frame = _input_frame(input_data)
        if self.initialized:
            self._advance_supervision(frame)
        return LingoAgent.tick(self, input_data)

    def control_pid(self, route_waypoints, velocity, speed_waypoints):
        self._pending_model_output = {
            "pred_route": route_waypoints.detach().float().cpu().tolist(),
            "pred_speed_wps": speed_waypoints.detach().float().cpu().tolist(),
        }
        if self._route_commit_receipt is not None and self._post_switch_plan_acceptance is None:
            rows = route_waypoints.detach().float().cpu().tolist()
            xy = tuple((float(row[0]), float(row[1])) for row in rows[0])
            accepted, metrics, reasons = accept_simlingo_plan(ModelPlan(xy=xy))
            self._post_switch_plan_acceptance = {
                "accepted": accepted,
                "metrics": dict(metrics),
                "reason_codes": list(reasons),
                "role": "NARROW_POST_SWITCH_CONSISTENCY_CHECK_NO_PLAN_MUTATION",
            }
            _atomic_json(
                self._output / "POST_SWITCH_PLAN_ACCEPTANCE.json",
                self._post_switch_plan_acceptance,
            )
            if not accepted:
                raise RuntimeError("V10_POST_SWITCH_SIMLINGO_PLAN_NOT_ACCEPTED")
        # Exactly the native controller receives the unmodified SimLingo tensors.
        return LingoAgent.control_pid(self, route_waypoints, velocity, speed_waypoints)

    def _criterion_snapshot(self):
        hero = CarlaDataProvider.get_hero_actor()
        if self._criteria is None:
            self._criteria = []
            for value in gc.get_objects():
                try:
                    actor = getattr(value, "actor", None)
                    if type(value).__name__ in {
                        "CollisionTest",
                        "OutsideRouteLanesTest",
                        "InRouteTest",
                        "RouteCompletionTest",
                    } and getattr(actor, "id", None) == hero.id:
                        self._criteria.append(value)
                except Exception:
                    continue
        rows = []
        for value in self._criteria:
            try:
                event_rows = []
                for event in getattr(value, "events", []):
                    event_type = event.get_type()
                    event_dict = event.get_dict() or {}
                    other_actor = event_dict.get("other_actor")
                    location = event_dict.get("location")
                    event_rows.append(
                        {
                            "event_type": getattr(event_type, "name", str(event_type)),
                            "frame": int(event.get_frame()),
                            "message": str(event.get_message()),
                            "other_actor_id": (
                                None
                                if other_actor is None
                                else int(getattr(other_actor, "id", 0))
                            ),
                            "other_actor_type_id": (
                                None
                                if other_actor is None
                                else str(getattr(other_actor, "type_id", ""))
                            ),
                            "location_xyz": (
                                None
                                if location is None
                                else [
                                    float(location.x),
                                    float(location.y),
                                    float(location.z),
                                ]
                            ),
                        }
                    )
                rows.append(
                    {
                        "criterion": type(value).__name__,
                        "test_status": str(getattr(value, "test_status", None)),
                        "actual_value": float(getattr(value, "actual_value", 0)),
                        "event_count": len(event_rows),
                        "events": event_rows,
                    }
                )
            except Exception as error:
                rows.append(
                    {"criterion": type(value).__name__, "read_error": repr(error)}
                )
        return rows

    @staticmethod
    def _safety(rows):
        def clear(name):
            group = [row for row in rows if row.get("criterion") == name]
            return bool(group) and all(
                row.get("test_status") != "FAILURE"
                and row.get("event_count") == 0
                and row.get("actual_value", 0) == 0
                for row in group
            )

        return {
            "collision": not clear("CollisionTest"),
            "off_road": not clear("OutsideRouteLanesTest"),
            "wrong_lane": not clear("OutsideRouteLanesTest"),
            "route_deviation": not clear("InRouteTest"),
        }

    def _observe(self, frame, control):
        if self._window_frame is None:
            return
        relative = int(frame) - int(self._window_frame)
        hero = CarlaDataProvider.get_hero_actor()
        location = hero.get_location()
        velocity = hero.get_velocity()
        speed = math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2)
        target = self.DrivingInput.get("target_point")
        target_value = (
            None
            if target is None
            else target.detach().float().cpu().tolist()
        )
        marker = bool(
            self.DrivingInput.get("route_switch_active") is not None
            and self.DrivingInput["route_switch_active"].item()
        )
        if marker:
            self._a1_active_forward_count += 1
        criteria = self._criterion_snapshot()
        row = {
            "frame": int(frame),
            "relative_tick": relative,
            "xyz": [float(location.x), float(location.y), float(location.z)],
            "speed_mps": speed,
            "target_point": target_value,
            "road_option": getattr(self.last_command_tmp, "name", str(self.last_command_tmp)),
            "active_route_identity": self._online_route_update_owner.active_route_identity,
            "route_switch_active": marker,
            "model_output": self._pending_model_output,
            "control": {
                "steer": float(control.steer),
                "throttle": float(control.throttle),
                "brake": float(control.brake),
            },
            "official_criteria": criteria,
            "safety": self._safety(criteria),
        }
        self._trajectory.append(row)
        if relative >= int(self._v10_config.get("observation_window_ticks", 96)) - 1:
            self._complete()

    def _complete(self):
        if self._completed:
            return
        self._completed = True
        criteria = self._criterion_snapshot()
        counters = (
            {
                "ambiguity_gate_invocations": 0,
                "candidate_interpretation_invocations": 0,
                "candidate_route_invocations": 0,
                "consequence_candidate_comparison_invocations": 0,
                "ask_receipts": 0,
                "passenger_answer_reads": 0,
                "replan_admissibility_invocations": 0,
                "route_transactions": 0,
                "a1_active_forwards": 0,
                "driveclarify_governor_active_ticks": 0,
            }
            if self._supervisor is None
            else dict(self._supervisor.counters.snapshot())
        )
        counters["a1_active_forwards"] = self._a1_active_forward_count
        result = {
            "schema": "driveclarify.v10.native-model-window.v1",
            "classification": "NATIVE_MODEL_WINDOW_COMPLETE",
            "run_id": self._v10_config["run_id"],
            "mode": self._mode,
            "checkpoint_sha256": self._v10_config["checkpoint_sha256"],
            "decision": self._decision,
            "gate_decision": (
                None
                if self._supervisor_result is None
                else self._supervisor_result.gate.decision.value
            ),
            "pass_through": (
                self._mode == "NATIVE_SIMLINGO"
                or bool(
                    self._supervisor_result is not None
                    and self._supervisor_result.pass_through
                )
            ),
            "candidate_invocation_count": counters["candidate_interpretation_invocations"],
            "candidate_route_invocation_count": counters["candidate_route_invocations"],
            "consequence_comparison_invocation_count": counters[
                "consequence_candidate_comparison_invocations"
            ],
            "route_transaction_count": self._route_transaction_count,
            "a1_active_forward_count": self._a1_active_forward_count,
            "driveclarify_governor_active_ticks": 0,
            "counters": counters,
            "same_native_controller": True,
            "additional_trajectory_planners": 0,
            "additional_pid_controllers": 0,
            "additional_vehicle_control_writers": 0,
            "post_switch_plan_acceptance": self._post_switch_plan_acceptance,
            # Compatibility aliases consumed by the inherited exact-PGID
            # completion monitor.  They reference the same 96 observations.
            "window_final_relative_tick": int(self._trajectory[-1]["relative_tick"]),
            "window_forward_rows": self._trajectory,
            "trajectory": self._trajectory,
            "official_criteria_final_snapshot": criteria,
            **self._safety(criteria),
        }
        _atomic_json(self._output / "NATIVE_MODEL_WINDOW_COMPLETE.json", result)
        self._status = "COMPLETE_V10_NATIVE_MODEL_WINDOW"
        self._write_status()

    def _write_navigation_contract_if_ready(self):
        if not hasattr(self, "_output") or not hasattr(
            self, "_official_input_route_sha256"
        ):
            return
        payload = {
            "schema": "driveclarify.v10.native-navigation-input-contract.v1",
            "mode": self._mode,
            "official_input_route_sha256": self._official_input_route_sha256,
            "official_global_plan_gps_object_id": self._official_global_plan_gps_object_id,
            "official_global_plan_world_object_id": self._official_global_plan_world_object_id,
            "native_set_global_plan_called_once": self._native_set_global_plan_called_once,
            "clear_path_route_reconstruction_count": 0,
            "clear_path_target_point_override_count": 0,
            "clear_path_road_option_override_count": 0,
            "controller": "agent_simlingo.LingoAgent.control_pid",
            "checkpoint_sha256": self._v10_config["checkpoint_sha256"],
        }
        _atomic_json(self._output / "NATIVE_NAVIGATION_INPUT_CONTRACT.json", payload)

    def _write_status(self):
        if not hasattr(self, "_output"):
            return
        _atomic_json(
            self._output / "V10_AGENT_STATUS.json",
            {
                "schema": "driveclarify.v10.agent-status.v1",
                "run_id": self._v10_config["run_id"],
                "mode": self._mode,
                "status": self._status,
                "gate_frame": self._gate_frame,
                "window_frame": self._window_frame,
                "route_transaction_count": self._route_transaction_count,
                "a1_active_forward_count": self._a1_active_forward_count,
                "governor_active_ticks": 0,
                "completed": self._completed,
            },
        )

    def run_step(self, input_data, timestamp, sensors=None):
        frame = _input_frame(input_data)
        control = LingoAgent.run_step(self, input_data, timestamp, sensors=sensors)
        if self.initialized:
            self._observe(frame, control)
        if int(frame) % 20 == 0:
            self._write_status()
        return control

    def destroy(self, results=None):
        for actor in getattr(self, "_spawned_case_actors", ()):
            try:
                actor.destroy()
            except Exception:
                pass
        if hasattr(self, "_output"):
            if not self._completed:
                if (
                    self._v10_config.get("receipt_completion_mode")
                    == "NATURAL_EVALUATOR_DESTROY"
                    and self._trajectory
                ):
                    self._complete()
                else:
                    self._status = "INCOMPLETE_V10_NATIVE_MODEL_WINDOW"
            self._write_status()
        if hasattr(self, "model"):
            LingoAgent.destroy(self, results)


__all__ = ["DriveClarifyV10SimLingoAgent", "get_entry_point"]
