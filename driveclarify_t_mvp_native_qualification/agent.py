"""Composition-only native qualification agent for the frozen SimLingo VLA.

The wrapper never subclasses ``LingoAgent``.  It installs the exact hash-bound
Bench2Drive metric interface on only the inner qualification instance, delegates
exactly one normal ``run_step`` call, and returns the exact ``VehicleControl``
object produced by that call.  Evaluator-only timing labels never enter this
module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Mapping

import carla
import numpy as np
from agents.navigation.local_planner import RoadOption
from leaderboard.autoagents import autonomous_agent
from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

from driveclarify_t_mvp.accounting import CallKind, PlanningAccounting
from driveclarify_t_mvp.adapters import (
    AuthorityMetadataExport,
    SimLingoReadOnlyAuthorityAdapter,
)
from driveclarify_t_mvp.authority import CandidateIsolationGuard, capture_p_old
from driveclarify_t_mvp.baselines import (
    AlwaysFullReplanBaseline,
    DriveClarifyTransitionBaseline,
    EgoPlanningSnapshot,
    FinishOldFirstBaseline,
    FrozenAdmissibility,
    FrozenHistoryOnlySerializer,
    FrozenVLAIdentity,
    HistoryOnlyBaseline,
    HistoryPromptBindingReceipt,
    InstallReceipt,
    InstantOverwriteBaseline,
    LocalReplanOnlyBaseline,
    PlanningBoundary,
    PureBoundedLocalPreparer,
    TransitionOutcome,
    UpdatedObligation,
    assess_with_existing_oracle_free_admissibility_owner,
)
from driveclarify_t_mvp.canonical import canonical_bytes, canonical_sha256
from driveclarify_t_mvp.commitment import ObservableSignals, export_observable_commitment
from driveclarify_t_mvp.firewall import (
    BASELINE_ALLOWED_FIELDS,
    BaselineId,
    assert_no_oracle_fields,
)
from driveclarify_t_mvp.full_replan import (
    ExactBranchLocalNavigationPreparer,
    TopologyEdge,
    TopologyGraphTaskConditionedGlobalPlanner,
)
from driveclarify_t_mvp.g_binding_v2 import (
    load_terminal_region_registry,
    resolve_terminal_region,
)
from driveclarify_t_mvp.injector import RuntimeSemanticUpdate
from driveclarify_t_mvp.metrics import (
    CONTINUITY_FORMULA_VERSION,
    GLOBAL_TASK_FORMULA_VERSION,
    MetricResult,
)
from driveclarify_t_mvp.models import (
    CandidateFeasibility,
    CandidateLifecycle,
    GlobalTask,
    LocalNavigationCondition,
    NavigationCandidate,
    ObservableCommitmentState,
    RouteRow,
)
from driveclarify_t_mvp.receipt import (
    EventType,
    SourceIdentity,
    TransitionEventStream,
    TransitionReceiptFinalizer,
)
from driveclarify_t_mvp_native_qualification.baseline_bindings import (
    resolve_all_bindings,
    resolve_baseline_class,
)
from driveclarify_t_mvp_native_qualification.metric_info_compatibility import (
    install_exact_metric_info_compatibility,
)
from driveclarify_t_mvp_native_qualification.preexecution_guard import (
    claim_agent_exposure,
)
from driveclarify_t_mvp_native_qualification.receipt_encoding import (
    encode_frozen_admissibility,
)
from driveclarify_clear_passthrough_v11.contracts import (
    AuthoritativeRoute,
    EgoState,
    RoutePoint,
)
from driveclarify_clear_passthrough_v11.replan import ReplanAdmissibility
from team_code.agent_simlingo import LingoAgent


def get_entry_point() -> str:
    return "DriveClarifyTMVPNativeQualificationAgent"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _plain(value: Any) -> Any:
    if type(value) is FrozenAdmissibility:
        return encode_frozen_admissibility(value)
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def _write_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(
                json.dumps(
                    _plain(value),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _frame_from_input(input_data: Mapping[str, Any]) -> int:
    values = []
    for item in input_data.values():
        if isinstance(item, (tuple, list)) and item:
            try:
                values.append(int(item[0]))
            except (TypeError, ValueError):
                pass
    if not values:
        raise RuntimeError("NATIVE_SIMULATOR_FRAME_UNAVAILABLE")
    return max(values)


def _right_driving_lane(waypoint: Any) -> Mapping[str, Any]:
    right = waypoint.get_right_lane()
    if right is None:
        return {"present": False, "road_id": None, "lane_id": None, "lane_type": None}
    lane_type = str(getattr(right, "lane_type", ""))
    return {
        "present": "Driving" in lane_type,
        "road_id": int(right.road_id),
        "lane_id": int(right.lane_id),
        "lane_type": lane_type,
    }


def _topology_boundary(frame: int, sim_time_s: float) -> Mapping[str, Any]:
    hero = CarlaDataProvider.get_hero_actor()
    world = CarlaDataProvider.get_world()
    if hero is None or world is None:
        raise RuntimeError("NATIVE_EGO_OR_WORLD_UNAVAILABLE")
    transform = hero.get_transform()
    location = transform.location
    waypoint = world.get_map().get_waypoint(
        location, project_to_road=True, lane_type=carla.LaneType.Driving
    )
    if waypoint is None:
        raise RuntimeError("NATIVE_MAP_WAYPOINT_UNAVAILABLE")
    velocity = hero.get_velocity()
    acceleration = hero.get_acceleration()
    right = transform.get_right_vector()
    return {
        "schema_version": "driveclarify.rq2.native_boundary.v1",
        "sim_frame": frame,
        "sim_time_s": float(sim_time_s),
        "captured_before_policy": True,
        "boundary_phase": "AFTER_CURRENT_CONTROL_BEFORE_NEXT_POLICY",
        "road_id": int(waypoint.road_id),
        "section_id": int(waypoint.section_id),
        "lane_id": int(waypoint.lane_id),
        "junction_id": int(waypoint.junction_id) if waypoint.is_junction else None,
        "is_junction": bool(waypoint.is_junction),
        "right_driving_lane": _right_driving_lane(waypoint),
        "ego": {
            "location_xyz_m": [float(location.x), float(location.y), float(location.z)],
            "rotation_pitch_yaw_roll_deg": [
                float(transform.rotation.pitch),
                float(transform.rotation.yaw),
                float(transform.rotation.roll),
            ],
            "velocity_world_mps": [float(velocity.x), float(velocity.y), float(velocity.z)],
            "acceleration_world_mps2": [
                float(acceleration.x), float(acceleration.y), float(acceleration.z)
            ],
            "right_unit_world": [float(right.x), float(right.y), float(right.z)],
        },
    }


class _StartupFullProjectionView:
    """Read-only RoutePlanner view for startup routes with no saved copy.

    At the first prospective boundary, the still-active startup deque is the
    complete authoritative projection.  The proxy binds ``saved_route`` reads
    to that same deque under the owner's existing lock; all other attributes
    delegate without writes.
    """

    def __init__(self, planner: Any):
        self._planner = planner
        self._route_lock = planner._route_lock

    @property
    def saved_route(self) -> Any:
        return self._planner.saved_route or self._planner.route

    @property
    def saved_route_distances(self) -> Any:
        return self._planner.saved_route_distances or self._planner.route_distances

    def __getattr__(self, name: str) -> Any:
        return getattr(self._planner, name)


def _route_rows(values: list[Mapping[str, Any]]) -> tuple[RouteRow, ...]:
    result: list[RouteRow] = []
    previous: tuple[float, float, float] | None = None
    for value in values:
        xyz = tuple(float(item) for item in value["xyz"])
        result.append(
            RouteRow(
                x_m=xyz[0],
                y_m=xyz[1],
                z_m=xyz[2],
                road_option=str(value["road_option"]),
                distance_from_previous_m=(
                    0.0 if previous is None else math.dist(previous, xyz)
                ),
                coordinate_domain="CARLA_WORLD",
            )
        )
        previous = xyz
    return tuple(result)


def _nearest_row_index(rows: tuple[RouteRow, ...], xyz: tuple[float, float, float]) -> int:
    return min(
        range(len(rows)),
        key=lambda index: math.dist(
            (rows[index].x_m, rows[index].y_m, rows[index].z_m), xyz
        ),
    )


def _ego_local_xy(
    rows: tuple[RouteRow, ...], topology: Mapping[str, Any]
) -> tuple[tuple[float, float], ...]:
    ego = topology["ego"]
    ego_x, ego_y = (float(value) for value in ego["location_xyz_m"][:2])
    yaw = math.radians(float(ego["rotation_pitch_yaw_roll_deg"][1]))
    cosine, sine = math.cos(yaw), math.sin(yaw)
    values: list[tuple[float, float]] = []
    for row in rows[:2]:
        dx, dy = row.x_m - ego_x, row.y_m - ego_y
        values.append((cosine * dx + sine * dy, -sine * dx + cosine * dy))
    return tuple(values)


class _LocalNavigationProbeProxy:
    """One-cycle B4 target-slot adapter around the existing read-only probe.

    It first invokes the frozen probe hook, then substitutes only the two native
    target slots for the next normal LingoAgent forward.  It never steps the
    planner, invokes the model, computes control, or mutates global route state.
    """

    def __init__(self, wrapped: Any, owner: Any, output: Path) -> None:
        self._wrapped = wrapped
        self._owner = owner
        self._output = output
        self._pending: tuple[Any, tuple[RouteRow, RouteRow], int, str] | None = None

    def stage(
        self,
        candidate: NavigationCandidate,
        rows: tuple[RouteRow, RouteRow],
        update_frame: int,
        update_id: str,
    ) -> None:
        if self._pending is not None:
            raise RuntimeError("T_B4_LOCAL_CONDITION_ALREADY_PENDING")
        self._pending = (candidate, rows, int(update_frame), str(update_id))

    def select_connector_target_window(
        self,
        waypoint_route: Any,
        ego_endpoint: Any,
        default_window: Any,
        **kwargs: Any,
    ) -> Any:
        observed = self._wrapped.select_connector_target_window(
            waypoint_route, ego_endpoint, default_window, **kwargs
        )
        if self._pending is None:
            return observed
        candidate, rows, update_frame, update_id = self._pending
        self._pending = None
        commands = {
            "LANEFOLLOW": RoadOption.LANEFOLLOW,
            "LEFT": RoadOption.LEFT,
            "RIGHT": RoadOption.RIGHT,
            "STRAIGHT": RoadOption.STRAIGHT,
            "CHANGELANELEFT": RoadOption.CHANGELANELEFT,
            "CHANGELANERIGHT": RoadOption.CHANGELANERIGHT,
        }
        selected = []
        for row in rows:
            planner_xyz = self._owner.world_to_route_planner_xyz(
                (row.x_m, row.y_m, row.z_m)
            )
            option = commands.get(str(row.road_option).split(".")[-1])
            if option is None:
                raise RuntimeError("T_B4_ROAD_OPTION_UNSUPPORTED")
            selected.append((np.asarray(planner_xyz, dtype=np.float64), option))
        _write_once(
            self._output / "t_b4_local_consumption.json",
            {
                "update_boundary_frame": update_frame,
                "update_event_id": update_id,
                "candidate_sha256": candidate.canonical_sha256,
                "candidate_route_identity": candidate.candidate_route_identity,
                "global_route_identity_unchanged": (
                    candidate.candidate_route_identity
                    == self._owner.active_route_identity
                ),
                "target_rows": rows,
                "target_interface": (
                    "team_code.agent_simlingo.LingoAgent.tick target_point/next_target_point"
                ),
                "normal_forward_count_added": 0,
                "control_writer_count_added": 0,
            },
        )
        return tuple(selected)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


@dataclass
class _SemanticPhysicalBinding:
    candidate_sha256: str
    semantic_route_identity: str
    physical_route_identity: str
    physical_generation: int
    owner_receipt: Mapping[str, Any]
    binding_sha256: str


class _NativeCandidateInstaller:
    def __init__(
        self,
        agent: LingoAgent,
        global_task: GlobalTask,
        accounting: PlanningAccounting,
    ) -> None:
        self.agent = agent
        self.global_task = global_task
        self.accounting = accounting
        self.binding: _SemanticPhysicalBinding | None = None

    def install_candidate(
        self,
        candidate: Any,
        *,
        expected_old_generation: int,
        frame: int,
    ) -> InstallReceipt:
        planner = self.agent._route_planner
        owner = self.agent._online_route_update_owner
        if int(planner.online_update_generation) != int(expected_old_generation):
            raise RuntimeError("NATIVE_EXPECTED_OLD_GENERATION_MISMATCH")
        commands = {
            "LANEFOLLOW": RoadOption.LANEFOLLOW,
            "LEFT": RoadOption.LEFT,
            "RIGHT": RoadOption.RIGHT,
            "STRAIGHT": RoadOption.STRAIGHT,
            "CHANGELANELEFT": RoadOption.CHANGELANELEFT,
            "CHANGELANERIGHT": RoadOption.CHANGELANERIGHT,
        }
        if not isinstance(candidate.full_route_projection, tuple):
            raise RuntimeError("NATIVE_GLOBAL_INSTALL_REQUIRES_FULL_ROUTE_PROJECTION")
        fresh_route = []
        # The immutable candidate retains its complete semantic projection.  The
        # native owner receives its already-frozen active suffix so a candidate
        # prepared after route start does not reinstall a passed prefix.
        for row in candidate.active_candidate_suffix:
            command = commands.get(str(row.road_option).split(".")[-1])
            if command is None:
                raise RuntimeError("NATIVE_ROAD_OPTION_UNSUPPORTED:" + str(row.road_option))
            fresh_route.append(
                (
                    carla.Transform(carla.Location(x=row.x_m, y=row.y_m, z=row.z_m)),
                    command,
                )
            )
        old_identity = str(planner.active_route_identity)
        owner_result = self.accounting.invoke(
            CallKind.ROUTE_CONVERSION,
            "NATIVE_EXACT_ROUTE_ROW_CONVERSION_AND_PUBLIC_OWNER_INSTALL",
            owner.install_reconnected_route,
            fresh_route,
            global_destination_identity=self.global_task.global_destination_identity,
            global_destination_planner_endpoint=owner.world_to_route_planner_xyz(
                self.global_task.endpoint_xyz_m
            ),
        )
        if owner_result is not True:
            raise RuntimeError("NATIVE_ONLINE_OWNER_DID_NOT_COMMIT")
        native = owner.last_installation_receipt
        physical = str(planner.active_route_identity)
        generation = int(planner.online_update_generation)
        if not native or native.get("installed_route_identity") != physical:
            raise RuntimeError("NATIVE_OWNER_RECEIPT_IDENTITY_MISMATCH")
        if generation != expected_old_generation + 1:
            raise RuntimeError("NATIVE_OWNER_GENERATION_NOT_INCREMENTED_ONCE")
        binding_payload = {
            "schema_version": "driveclarify.rq2.semantic_physical_route_binding.v1",
            "candidate_sha256": candidate.canonical_sha256,
            "semantic_route_identity": candidate.candidate_route_identity,
            "physical_route_identity": physical,
            "physical_generation": generation,
            "source_coordinate_domain": "CARLA_WORLD",
            "installed_coordinate_domain": "SIMLINGO_ROUTE_PLANNER",
            "owner_receipt": native,
            "semantic_full_route_sha256": canonical_sha256(
                candidate.full_route_projection
            ),
            "installed_active_suffix_sha256": canonical_sha256(
                candidate.active_candidate_suffix
            ),
        }
        self.binding = _SemanticPhysicalBinding(
            candidate_sha256=candidate.canonical_sha256,
            semantic_route_identity=candidate.candidate_route_identity,
            physical_route_identity=physical,
            physical_generation=generation,
            owner_receipt=native,
            binding_sha256=canonical_sha256(binding_payload),
        )
        return InstallReceipt(
            committed=True,
            frame=frame,
            route_identity_before=old_identity,
            route_generation_before=expected_old_generation,
            installed_route_identity=candidate.candidate_route_identity,
            installed_route_generation=generation,
            global_task_before=self.global_task,
            global_task_after=self.global_task,
            installed_candidate_sha256=candidate.canonical_sha256,
            reason_code="NATIVE_PUBLIC_OWNER_COMMIT_WITH_EXPLICIT_IDENTITY_DOMAIN_BINDING",
        )


class DriveClarifyTMVPNativeQualificationAgent(autonomous_agent.AutonomousAgent):
    """One-control-authority composition wrapper used only by this qualification."""

    def __init__(self, carla_host: str, carla_port: int, debug: bool = False):
        super().__init__(carla_host, carla_port, debug)
        self._inner = LingoAgent(carla_host, carla_port, debug)
        self._metric_info_compatibility = install_exact_metric_info_compatibility(
            self._inner
        )
        self._config: Mapping[str, Any] = {}
        self._output = Path(".")
        self._boundary_dir = Path(".")
        self._runtime_update_path = Path(".")
        self._update_processed = False
        self._candidate: Any = None
        self._lifecycle: CandidateLifecycle | None = None
        self._installer: _NativeCandidateInstaller | None = None
        self._accounting = PlanningAccounting()
        self._stream: TransitionEventStream | None = None
        self._p_old: Any = None
        self._global_task: GlobalTask | None = None
        self._last_control: Any = None
        self._forward_frames: list[int] = []
        self._destroyed = False
        self._baseline: Any = None
        self._resolved_baseline_type: type[Any] | None = None
        self._dispatch_binding: Mapping[str, Any] | None = None
        self._local_probe_proxy: _LocalNavigationProbeProxy | None = None
        self._update_topology: Mapping[str, Any] | None = None
        self._update: RuntimeSemanticUpdate | None = None
        self._last_decision: Any = None
        self._terminal_written = False

    def set_global_plan(self, global_plan_gps: Any, global_plan_world_coord: Any) -> None:
        super().set_global_plan(global_plan_gps, global_plan_world_coord)
        self._inner.org_dense_route_gps = global_plan_gps
        self._inner.org_dense_route_world_coord = global_plan_world_coord
        self._inner.set_global_plan(global_plan_gps, global_plan_world_coord)

    def setup(self, path_to_conf_file: str) -> None:
        config_path = Path(os.environ["DRIVECLARIFY_TMVP_RUNTIME_CONFIG"]).resolve()
        all_configs = json.loads(config_path.read_text(encoding="utf-8"))
        self._config = all_configs["cases"][os.environ["DRIVECLARIFY_TMVP_CASE_ID"]]
        assert_no_oracle_fields(self._config)
        if os.environ.get("DRIVECLARIFY_RQ2_NATIVE_ENGINEERING_ENABLE") != "1":
            raise RuntimeError("NATIVE_ENGINEERING_DEFAULT_OFF")
        # Selector resolution is deliberately before the exposure claim: an
        # unknown/fallback baseline can never consume a scientific episode.
        resolved_baseline = resolve_baseline_class(str(self._config["baseline_id"]))
        dispatch_rows = {
            row["baseline_id"]: row for row in resolve_all_bindings()
        }
        self._dispatch_binding = dispatch_rows[str(self._config["baseline_id"])]
        self._resolved_baseline_type = resolved_baseline
        claim_agent_exposure(
            state_log=Path(os.environ["DRIVECLARIFY_TMVP_PROBE_STATE_LOG"]).resolve(),
            reservation_id=os.environ["DRIVECLARIFY_TMVP_RESERVATION_ID"],
            case_id=str(self._config["case_id"]),
            seed=int(self._config["seed"]),
        )
        self._output = Path(os.environ["DRIVECLARIFY_TMVP_NATIVE_OUTPUT"]).resolve()
        self._output.mkdir(parents=True, exist_ok=False)
        self._boundary_dir = self._output / "boundaries"
        self._boundary_dir.mkdir()
        self._runtime_update_path = self._output / "exchange" / "runtime_update.json"
        self._runtime_update_path.parent.mkdir()
        run_id = str(self._config["run_id"])
        self._inner.setup(str(Path(path_to_conf_file).resolve()) + "+" + run_id)
        self.track = self._inner.track
        baseline_id = str(self._config["baseline_id"])
        if baseline_id == BaselineId.T_B1.value:
            self._baseline = resolved_baseline(self._accounting)
        elif baseline_id == BaselineId.T_B3.value:
            self._baseline = resolved_baseline(self._accounting)
        elif baseline_id == BaselineId.T_B5.value:
            self._baseline = resolved_baseline(FrozenHistoryOnlySerializer())
        elif baseline_id == BaselineId.T_B6.value:
            self._baseline = resolved_baseline(self._accounting)
        # B2 and B4 require a frame-bound authority adapter and are instantiated
        # in their dispatch branch with those exact native dependencies.
        self._stream = TransitionEventStream(
            episode_id=str(self._config["episode_id"]),
            attempt_id=str(self._config["attempt_id"]),
            case_id=str(self._config["case_id"]),
            scene_id=str(self._config["scene_id"]),
            seed=int(self._config["seed"]),
            baseline_id=baseline_id,
        )
        visible = tuple(sorted(BASELINE_ALLOWED_FIELDS[BaselineId(baseline_id)]))
        self._stream.append(
            EventType.EPISODE_SETUP,
            source_object=SourceIdentity(
                owner_id=type(self).__module__ + "." + type(self).__qualname__,
                owner_generation=None,
                source_frame=None,
                source_clock="SETUP_WALLCLOCK",
            ),
            reason_codes=("QUALIFICATION_ONLY_COMPOSITION_WRAPPER_READY",),
            evidence_grade="engineering",
            payload={
                "firewall_verified": True,
                "baseline_visible_fields": visible,
                "visible_field_manifest_sha256": canonical_sha256(
                    {"baseline_id": baseline_id, "visible_fields": visible}
                ),
                "inner_agent_owner": type(self._inner).__module__
                + "."
                + type(self._inner).__qualname__,
                "control_authority_count": 1,
            },
        )
        _write_once(
            self._output / "agent_setup.json",
            {
                "schema_version": "driveclarify.rq2.native_agent_setup.v1",
                "runtime_config_sha256": canonical_sha256(self._config),
                "inner_exact_type": type(self._inner).__module__
                + "."
                + type(self._inner).__qualname__,
                "resolved_baseline_binding": resolved_baseline.__module__
                + "."
                + resolved_baseline.__qualname__,
                "native_dispatch_receipt": self._dispatch_binding,
                "resolved_baseline_instance_type": (
                    None
                    if self._baseline is None
                    else type(self._baseline).__module__
                    + "."
                    + type(self._baseline).__qualname__
                ),
                "frame_bound_instantiation": baseline_id
                in {BaselineId.T_B2.value, BaselineId.T_B4.value},
                "composition_not_subclass": not isinstance(self, LingoAgent),
                "model_changes": False,
                "controller_changes": False,
                "scientific_method_changes": False,
            },
        )

    def sensors(self) -> Any:
        sensors = self._inner.sensors()
        self.track = self._inner.track
        return sensors

    def _source(self, frame: int, sim_time_s: float, topology: Mapping[str, Any]) -> Any:
        owner = self._inner._online_route_update_owner
        endpoint = tuple(float(value) for value in owner.global_destination_xyz)
        self._global_task = GlobalTask.bind(
            str(self._config["global_destination_identity"]),
            endpoint,
            coordinate_domain="CARLA_WORLD",
        )
        planner = self._inner._route_planner
        view = _StartupFullProjectionView(planner)

        def metadata() -> AuthorityMetadataExport:
            generation = int(planner.online_update_generation)
            branch = (
                f"road={topology['road_id']};section={topology['section_id']};"
                f"lane={topology['lane_id']}"
            )
            return AuthorityMetadataExport(
                source_frame=frame,
                source_sim_time_s=sim_time_s,
                current_maneuver_identity=branch,
                current_branch_or_connector_identity=branch,
                route_owner_write_count=int(owner.online_writer_count),
                route_switch_lifecycle={
                    "receipt_json": str(
                        getattr(self._inner, "driveclarify_route_switch_adapter_receipt_json", "")
                    )
                },
                cache_state={
                    "last_command": str(getattr(self._inner, "last_command", "")),
                    "last_command_tmp": str(getattr(self._inner, "last_command_tmp", "")),
                    "commands": [str(value) for value in getattr(self._inner, "commands", ())],
                    "target_point_prev": [
                        float(value)
                        for value in getattr(self._inner, "target_point_prev", ())
                    ],
                },
                controller_pid_state={
                    "speed_controller_owner": type(self._inner.speed_controller).__module__
                    + "."
                    + type(self._inner.speed_controller).__qualname__,
                    "turn_controller_owner": type(self._inner.turn_controller).__module__
                    + "."
                    + type(self._inner.turn_controller).__qualname__,
                },
                component_versions=tuple(
                    (name, generation)
                    for name in (
                        "mission_context",
                        "maneuver_owner",
                        "controller_owner",
                        "cache_owner",
                        "route_switch_owner",
                        "control_owner",
                    )
                ),
            )

        return SimLingoReadOnlyAuthorityAdapter(
            view,
            global_task=self._global_task,
            metadata_export=metadata,
            control_owner_identity="team_code.agent_simlingo.LingoAgent.run_step",
        )

    def _observable(
        self,
        frame: int,
        sim_time_s: float,
        topology: Mapping[str, Any],
    ) -> Any:
        planner = self._inner._route_planner
        branch = (
            f"road={topology['road_id']};section={topology['section_id']};"
            f"lane={topology['lane_id']}"
        )
        road_id, lane_id = int(topology["road_id"]), int(topology["lane_id"])
        common = road_id in {48, 811, 791} and lane_id == 1
        old_exclusive = road_id == 39 and lane_id == -1
        right_present = bool(topology["right_driving_lane"]["present"])
        source_hash = canonical_sha256(topology)
        return export_observable_commitment(
            ObservableSignals(
                source_frame=frame,
                source_sim_time_s=sim_time_s,
                connector_phase=("BEFORE" if common else "INSIDE" if old_exclusive else None),
                structural_divergence_relation=(
                    "COMMON" if common else "OLD_EXCLUSIVE" if old_exclusive else None
                ),
                alternative_topological_executable=(True if common else False if old_exclusive else None),
                recovery_topological_executable=(right_present if old_exclusive else None),
                current_opportunity_available=(True if common or right_present else None),
                # No new numeric gate is introduced.  ``_topology_boundary``
                # obtains this waypoint through CARLA's Driving-lane filter and
                # fails if the shared native world/ego owner is unavailable.
                rule_valid=True,
                # The frozen native stack has no transition-specific safety
                # override.  Reaching this post-control boundary means the shared
                # RouteScenario safety owner supplied no transition denial.
                hard_safety_valid=True,
                active_route_identity=str(planner.active_route_identity),
                route_generation=int(planner.online_update_generation),
                current_maneuver_identity=branch,
                current_branch_or_connector_identity=branch,
                global_task_identity_G=self._global_task.identity,
                source_hashes=tuple(
                    (name, source_hash)
                    for name in (
                        "active_route",
                        "topology",
                        "maneuver_owner",
                        "rule_owner",
                        "hard_safety_owner",
                        "global_task_owner",
                    )
                ),
            )
        )

    def _poll_runtime_update(self, frame: int) -> RuntimeSemanticUpdate | None:
        deadline = time.monotonic() + 0.5
        ack = self._output / "exchange" / "acks" / f"ack_{frame:08d}.json"
        while time.monotonic() < deadline:
            if self._runtime_update_path.exists():
                raw = json.loads(self._runtime_update_path.read_text(encoding="utf-8"))
                assert_no_oracle_fields(raw)
                update = RuntimeSemanticUpdate(**raw)
                if update.actual_injection_frame != frame:
                    raise RuntimeError("RUNTIME_UPDATE_FRAME_BOUNDARY_MISMATCH")
                if update.case_id != self._config["case_id"]:
                    raise RuntimeError("RUNTIME_UPDATE_CASE_MISMATCH")
                return update
            if ack.exists():
                return None
            time.sleep(0.01)
        return None

    def _append(
        self,
        event_type: EventType,
        *,
        frame: int,
        sim_time_s: float,
        update_id: str,
        reason: str,
        payload: Mapping[str, Any],
        **values: Any,
    ) -> None:
        assert self._stream is not None
        generation = int(self._inner._route_planner.online_update_generation)
        self._stream.append(
            event_type,
            source_object=SourceIdentity(
                owner_id=type(self).__module__ + "." + type(self).__qualname__,
                owner_generation=generation,
                source_frame=frame,
                source_clock="CARLA_SIM_TIME",
            ),
            reason_codes=(reason,),
            evidence_grade="runtime",
            payload=payload,
            update_event_id=update_id,
            sim_frame=frame,
            sim_time_s=sim_time_s,
            monotonic_ns=time.monotonic_ns(),
            wall_time_utc=_utc_now(),
            **values,
        )

    def _load_alternative_route(
        self, topology: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], tuple[RouteRow, ...], tuple[RouteRow, ...]]:
        graph_path = Path(os.environ["DRIVECLARIFY_TMVP_GRAPH_SOURCE"]).resolve()
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        values = [*graph["adjacent_corridor_delayed"], dict(graph["destination"])]
        full = _route_rows(values)
        ego_xyz = tuple(float(value) for value in topology["ego"]["location_xyz_m"])
        nearest = _nearest_row_index(full, ego_xyz)
        return graph, full, full[nearest:]

    def _obligation(self, update: RuntimeSemanticUpdate) -> UpdatedObligation:
        return UpdatedObligation(
            identity=canonical_sha256(
                {
                    "update_event_id": update.update_event_id,
                    "branch_or_connector_identity": self._config[
                        "updated_branch_identity"
                    ],
                    "instruction_new": update.instruction_new,
                }
            ),
            semantic_update_event_id=update.update_event_id,
            branch_or_connector_identity=str(self._config["updated_branch_identity"]),
            instruction_new=update.instruction_new,
        )

    def _prepare_common_candidate(
        self,
        update: RuntimeSemanticUpdate,
        frame: int,
        sim_time_s: float,
        topology: Mapping[str, Any],
        source: Any,
    ) -> NavigationCandidate:
        """Prepare the experiment-owned detached P_new shared by B1/B3/B6."""

        assert self._p_old is not None and self._global_task is not None
        graph, full, active = self._load_alternative_route(topology)
        if len(active) < 2:
            raise RuntimeError("COMMON_CANDIDATE_ACTIVE_SUFFIX_TOO_SHORT")
        obligation = self._obligation(update)
        local_rows = active[: min(28, len(active))]
        local = LocalNavigationCondition(
            branch_or_connector_identity=obligation.branch_or_connector_identity,
            route_rows=local_rows,
            target_points_ego_local_xy_m=_ego_local_xy(local_rows, topology),
            first_road_option=local_rows[0].road_option,
            preparation_receipt_sha256=canonical_sha256(
                {
                    "owner": "RQ2_COMMON_DETACHED_CANDIDATE_PREPARER",
                    "source_graph_sha256": canonical_sha256(graph),
                    "active_suffix": active,
                    "ego": topology["ego"],
                    "model_forward_count": 0,
                }
            ),
        )
        branch = obligation.branch_or_connector_identity.casefold()
        if "current-opportunity" in branch and int(topology["road_id"]) == 39:
            feasibility = CandidateFeasibility.NO_SAFE_CURRENT_OPPORTUNITY
            reasons = ("SEMANTIC_BRANCH_ENTRY_ALREADY_BEHIND_CURRENT_TOPOLOGY",)
        elif int(topology["road_id"]) == 39 and int(topology["lane_id"]) == -1:
            feasibility = CandidateFeasibility.FEASIBLE_AFTER_LEGAL_RECOVERY
            reasons = ("CURRENT_TOPOLOGY_REQUIRES_LATER_LEGAL_RECOVERY",)
        else:
            feasibility = CandidateFeasibility.FEASIBLE_NOW
            reasons = ("CURRENT_TOPOLOGY_SUPPORTS_ALTERNATIVE_ROUTE_NOW",)

        def create() -> NavigationCandidate:
            return NavigationCandidate.create(
                candidate_id="rq2-common-"
                + canonical_sha256(
                    {
                        "update": update.update_event_id,
                        "route": full,
                        "frame": frame,
                    }
                )[:24],
                candidate_route_identity="rq2-common-route-"
                + canonical_sha256(
                    {"route_rows": full, "global_task": self._global_task.identity}
                )[:24],
                candidate_route_generation=self._p_old.route_generation + 1,
                global_task=self._global_task,
                semantic_update_event_id=update.update_event_id,
                updated_obligation_identity=obligation.identity,
                local_branch_or_connector_identity=obligation.branch_or_connector_identity,
                full_route_projection=full,
                active_candidate_suffix=active,
                local_navigation_condition=local,
                feasibility_state=feasibility,
                feasibility_reason_codes=reasons,
                candidate_source_frame=frame,
                candidate_source_sim_time_s=sim_time_s,
                candidate_generator_owner=(
                    "driveclarify_t_mvp_native_qualification."
                    "RQ2CommonDetachedCandidatePreparer"
                ),
                preparation_call_receipts=(
                    canonical_sha256(graph),
                    local.preparation_receipt_sha256,
                ),
            )

        candidate = self._accounting.invoke(
            CallKind.CANDIDATE_GENERATION,
            "RQ2_COMMON_B1_B3_B6_DETACHED_CANDIDATE",
            lambda: CandidateIsolationGuard(source).prepare(create).prepared,
        )
        _write_once(
            self._output / "common_detached_candidate.json",
            {
                "candidate": candidate,
                "shared_method_set": ["T-B1", "T-B3", "T-B6"],
                "authority_unchanged": source.export_atomic().route_identity
                == self._p_old.authoritative_route_identity,
            },
        )
        return candidate

    def _v11_routes(
        self, candidate: NavigationCandidate
    ) -> tuple[AuthoritativeRoute, AuthoritativeRoute]:
        assert self._p_old is not None and self._global_task is not None
        owner = self._inner._online_route_update_owner

        def old_point(row: RouteRow) -> RoutePoint:
            xyz = owner.route_planner_to_world_xyz((row.x_m, row.y_m, row.z_m))
            return RoutePoint(*xyz, road_option=row.road_option)

        current_points = tuple(old_point(row) for row in self._p_old.active_suffix_projection)
        if len(current_points) < 2:
            current_points = tuple(old_point(row) for row in self._p_old.full_route_projection)
        resolved_points = tuple(
            RoutePoint(row.x_m, row.y_m, row.z_m, row.road_option)
            for row in candidate.active_candidate_suffix
        )
        current = AuthoritativeRoute(
            route_id=self._p_old.authoritative_route_identity,
            points=current_points,
            target_point=current_points[min(1, len(current_points) - 1)].xyz[:2],
            road_option=current_points[min(1, len(current_points) - 1)].road_option,
            destination_xyz=self._global_task.endpoint_xyz_m,
            source_frame=self._p_old.source_frame,
        )
        resolved = AuthoritativeRoute(
            route_id=candidate.candidate_route_identity,
            points=resolved_points,
            target_point=resolved_points[min(1, len(resolved_points) - 1)].xyz[:2],
            road_option=resolved_points[min(1, len(resolved_points) - 1)].road_option,
            destination_xyz=self._global_task.endpoint_xyz_m,
            source_frame=candidate.candidate_source_frame,
            connector_id=candidate.local_branch_or_connector_identity,
            commitment_point_index=None,
            full_route_owner=candidate.candidate_generator_owner,
            active_suffix_owner=candidate.candidate_generator_owner,
        )
        return current, resolved

    @staticmethod
    def _v11_ego(frame: int, topology: Mapping[str, Any]) -> EgoState:
        ego = topology["ego"]
        xyz = tuple(float(value) for value in ego["location_xyz_m"])
        velocity = tuple(float(value) for value in ego["velocity_world_mps"])
        return EgoState(
            x=xyz[0],
            y=xyz[1],
            z=xyz[2],
            yaw_degrees=float(ego["rotation_pitch_yaw_roll_deg"][1]),
            speed_mps=math.sqrt(sum(value * value for value in velocity)),
            frame=int(frame),
        )

    def _prepare_t_b2(
        self,
        update: RuntimeSemanticUpdate,
        frame: int,
        sim_time_s: float,
        topology: Mapping[str, Any],
        source: Any,
    ) -> None:
        assert self._p_old is not None and self._global_task is not None
        graph_path = Path(os.environ["DRIVECLARIFY_TMVP_GRAPH_SOURCE"]).resolve()
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        rows = graph["adjacent_corridor_delayed"]
        destination = tuple(float(value) for value in graph["destination"]["xyz"])
        final = dict(graph["destination"])
        ego_xyz = tuple(float(value) for value in topology["ego"]["location_xyz_m"])
        graph_rows = _route_rows([*rows, final])
        nearest = _nearest_row_index(graph_rows, ego_xyz)
        branch_start = max(13, nearest)
        prefix_values = rows[nearest:branch_start]
        if not prefix_values:
            prefix_values = [
                {
                    "xyz": list(ego_xyz),
                    "road_option": "LANEFOLLOW",
                }
            ]
        branch_values = rows[branch_start:62]
        if not branch_values:
            branch_values = rows[max(0, min(nearest, len(rows) - 2)) : -1]
        edges = (
            TopologyEdge(
                "ego-to-updated",
                "ego",
                "updated",
                _route_rows(prefix_values),
                float(len(prefix_values)),
            ),
            TopologyEdge(
                "updated-branch",
                "updated",
                "suffix",
                _route_rows(branch_values),
                float(len(branch_values)),
            ),
            TopologyEdge(
                "suffix-to-G", "suffix", "G", _route_rows([*rows[62:], final]), 10.0
            ),
        )
        obligation = self._obligation(update)
        terminal_registry = load_terminal_region_registry(
            Path(os.environ["DRIVECLARIFY_TMVP_G_REGION_REGISTRY"]).resolve()
        )
        terminal_region = resolve_terminal_region(
            registry=terminal_registry,
            global_task=self._global_task,
            graph_destination=graph["destination"],
            graph_town=str(graph["town"]),
            opendrive_path=Path(
                os.environ["DRIVECLARIFY_TMVP_OPENDRIVE_SOURCE"]
            ).resolve(),
        )
        planner = TopologyGraphTaskConditionedGlobalPlanner(
            edges=edges,
            obligation_branch_edges={obligation.branch_or_connector_identity: ("updated-branch",)},
            global_task_destination_nodes={self._global_task.identity: "G"},
            global_task_terminal_regions={self._global_task.identity: terminal_region},
            source_graph_sha256=canonical_sha256(graph),
        )
        baseline = self._resolved_baseline_type(
            source,
            planner,
            ExactBranchLocalNavigationPreparer(),
            self._accounting,
            owner_identity=type(self).__module__ + ".T_B2_NATIVE_PREPARER",
        )
        if type(baseline) is not AlwaysFullReplanBaseline:
            raise RuntimeError("T_B2_NATIVE_DISPATCH_TYPE_MISMATCH")
        self._baseline = baseline
        ego = topology["ego"]
        yaw = float(ego["rotation_pitch_yaw_roll_deg"][1])
        velocity = ego["velocity_world_mps"]
        snapshot = EgoPlanningSnapshot(
            frame=frame,
            sim_time_s=sim_time_s,
            pose_xyz_yaw=tuple(float(value) for value in (*ego["location_xyz_m"], yaw)),
            speed_mps=math.sqrt(sum(float(value) ** 2 for value in velocity)),
            source_sha256=canonical_sha256(ego),
            current_topology_node_identity="ego",
        )
        self._append(
            EventType.CANDIDATE_PREPARATION_STARTED,
            frame=frame,
            sim_time_s=sim_time_s,
            update_id=update.update_event_id,
            reason="T_B2_GENUINE_FULL_REPLAN_STARTED",
            payload={"source_graph_sha256": canonical_sha256(graph)},
        )
        before = source.export_atomic()
        candidate = baseline.prepare_full_replan(
            ego=snapshot, updated_obligation=obligation, global_task=self._global_task
        )
        after = source.export_atomic()
        self._candidate = candidate
        self._lifecycle = CandidateLifecycle(candidate)
        authority_unchanged = (
            before.route_identity == after.route_identity
            and before.route_generation == after.route_generation
            and canonical_sha256(before) == canonical_sha256(after)
        )
        self._append(
            EventType.CANDIDATE_PREPARATION_TERMINAL,
            frame=frame,
            sim_time_s=sim_time_s,
            update_id=update.update_event_id,
            reason="T_B2_DETACHED_CANDIDATE_PREPARED",
            payload={"authority_unchanged": authority_unchanged, "installation_state": "UNINSTALLED"},
            object_identity=candidate.candidate_id,
            object_sha256=candidate.canonical_sha256,
            route_identity=candidate.candidate_route_identity,
            route_generation=candidate.candidate_route_generation,
            global_task_identity_G=self._global_task.identity,
            p_new_sha256=candidate.canonical_sha256,
        )
        self._append(
            EventType.TRANSITION_DECIDED,
            frame=frame,
            sim_time_s=sim_time_s,
            update_id=update.update_event_id,
            reason="T_B2_UNCONDITIONAL_ADMISSION",
            payload={
                "candidate_state": "CANDIDATE_ADMITTED",
                "admitted_candidate_sha256": candidate.canonical_sha256,
            },
            p_new_sha256=candidate.canonical_sha256,
        )
        self._append(
            EventType.ROUTE_INSTALL_REQUESTED,
            frame=frame,
            sim_time_s=sim_time_s,
            update_id=update.update_event_id,
            reason="T_B2_EXACT_PREPARED_CANDIDATE_REQUESTED",
            payload={"public_owner_only": True},
            p_new_sha256=candidate.canonical_sha256,
        )
        self._installer = _NativeCandidateInstaller(
            self._inner, self._global_task, self._accounting
        )
        receipt = baseline.install_full_replan_candidate(
            lifecycle=self._lifecycle,
            installer=self._installer,
            boundary=PlanningBoundary(frame, True, False, True, True),
            expected_old_generation=self._p_old.route_generation,
        )
        binding = self._installer.binding
        if binding is None:
            raise RuntimeError("NATIVE_IDENTITY_BINDING_MISSING")
        self._append(
            EventType.ROUTE_INSTALL_TERMINAL,
            frame=frame,
            sim_time_s=sim_time_s,
            update_id=update.update_event_id,
            reason="T_B2_NATIVE_PUBLIC_OWNER_COMMITTED",
            payload={
                "installed_candidate_sha256": candidate.canonical_sha256,
                "semantic_physical_binding_sha256": binding.binding_sha256,
                "physical_route_identity": binding.physical_route_identity,
                "installed_not_consumed": True,
            },
            object_identity=candidate.candidate_id,
            object_sha256=candidate.canonical_sha256,
            route_identity=receipt.installed_route_identity,
            route_generation=receipt.installed_route_generation,
            global_task_identity_G=self._global_task.identity,
            p_new_sha256=candidate.canonical_sha256,
        )
        _write_once(
            self._output / "t_b2_prepare_install.json",
            {
                "p_old": self._p_old,
                "candidate": candidate,
                "candidate_lifecycle": self._lifecycle.events,
                "install_receipt": receipt,
                "identity_binding": binding,
                "accounting_after_install": self._accounting.snapshot(),
                "destination_semantic_identity_preserved": (
                    candidate.global_task.global_destination_identity
                    == self._global_task.global_destination_identity
                ),
                "destination_terminal_region_equivalent": True,
                "destination_terminal_region": terminal_region,
                "destination_terminal_equivalence_sha256": (
                    terminal_region.assert_equivalent(
                        global_task=self._global_task,
                        planner_terminal=candidate.full_route_projection[-1],
                    )
                ),
                "authoritative_G_endpoint_xyz_m": self._global_task.endpoint_xyz_m,
                "planner_graph_terminal_xyz_m": destination,
            },
        )

    def _prepare_t_b5(
        self, update: RuntimeSemanticUpdate, frame: int, sim_time_s: float
    ) -> None:
        identity = FrozenVLAIdentity.from_loaded_agent(self._inner)
        before_user_flag = self._inner.user_flag
        if type(self._baseline) is not HistoryOnlyBaseline:
            raise RuntimeError("T_B5_NATIVE_DISPATCH_TYPE_MISMATCH")
        decision, payload = self._baseline.prepare_update(
            old_instruction=update.instruction_old,
            new_instruction=update.instruction_new,
            tokenizer=self._inner.tokenizer,
        )
        self._inner.custom_prompt = payload.prompt_utf8
        binding = HistoryPromptBindingReceipt(
            prompt_sha256=payload.prompt_sha256,
            target_interface="team_code.agent_simlingo.LingoAgent.custom_prompt",
            custom_prompt_installed=self._inner.custom_prompt == payload.prompt_utf8,
            user_flag_unchanged=self._inner.user_flag == before_user_flag,
        )
        _write_once(
            self._output / "t_b5_pre_forward.json",
            {
                "sim_frame": frame,
                "sim_time_s": sim_time_s,
                "update_event_id": update.update_event_id,
                "decision": decision,
                "history_payload": payload,
                "binding": binding,
                "frozen_vla_identity": identity,
                "actual_tokenizer_owner": type(self._inner.tokenizer).__module__
                + "."
                + type(self._inner.tokenizer).__qualname__,
                "model_object_identity": id(self._inner.model),
            },
        )

    def _prepare_t_b1(
        self,
        update: RuntimeSemanticUpdate,
        frame: int,
        sim_time_s: float,
        topology: Mapping[str, Any],
        source: Any,
    ) -> None:
        if type(self._baseline) is not InstantOverwriteBaseline:
            raise RuntimeError("T_B1_NATIVE_DISPATCH_TYPE_MISMATCH")
        assert self._p_old is not None and self._global_task is not None
        self._candidate = self._prepare_common_candidate(
            update, frame, sim_time_s, topology, source
        )
        self._lifecycle = CandidateLifecycle(self._candidate)
        self._installer = _NativeCandidateInstaller(
            self._inner, self._global_task, self._accounting
        )
        receipt = self._baseline.commit_or_defer(
            lifecycle=self._lifecycle,
            boundary=PlanningBoundary(frame, True, False, True, True),
            installer=self._installer,
            expected_old_generation=self._p_old.route_generation,
        )
        _write_once(
            self._output / "t_b1_dispatch.json",
            {
                "resolved_instance_type": type(self._baseline).__module__
                + "."
                + type(self._baseline).__qualname__,
                "candidate": self._candidate,
                "decision_owner": "InstantOverwriteBaseline.commit_or_defer",
                "install_receipt": receipt,
                "lifecycle": self._lifecycle.events,
                "accounting": self._accounting.snapshot(),
            },
        )

    def _prepare_t_b3(
        self,
        update: RuntimeSemanticUpdate,
        frame: int,
        sim_time_s: float,
        topology: Mapping[str, Any],
        source: Any,
    ) -> None:
        if type(self._baseline) is not FinishOldFirstBaseline:
            raise RuntimeError("T_B3_NATIVE_DISPATCH_TYPE_MISMATCH")
        assert self._p_old is not None
        self._candidate = self._prepare_common_candidate(
            update, frame, sim_time_s, topology, source
        )
        self._lifecycle = CandidateLifecycle(self._candidate)
        decision = self._baseline.prepare_update(
            p_old=self._p_old, candidate=self._candidate
        )
        self._last_decision = decision
        _write_once(
            self._output / "t_b3_dispatch.json",
            {
                "resolved_instance_type": type(self._baseline).__module__
                + "."
                + type(self._baseline).__qualname__,
                "decision": decision,
                "p_old_preserved": source.export_atomic().route_identity
                == self._p_old.authoritative_route_identity,
                "pending_candidate_sha256": self._baseline.pending_candidate.canonical_sha256,
                "no_rescue_search": True,
            },
        )

    def _prepare_t_b4(
        self,
        update: RuntimeSemanticUpdate,
        frame: int,
        sim_time_s: float,
        topology: Mapping[str, Any],
        source: Any,
    ) -> None:
        if self._resolved_baseline_type is not LocalReplanOnlyBaseline:
            raise RuntimeError("T_B4_NATIVE_DISPATCH_TYPE_MISMATCH")
        assert self._p_old is not None and self._local_probe_proxy is not None
        _, _, active = self._load_alternative_route(topology)
        local_rows = active[: min(28, len(active))]
        if len(local_rows) < 2:
            raise RuntimeError("T_B4_NATIVE_LOCAL_ROWS_TOO_SHORT")
        baseline = self._resolved_baseline_type(
            source, PureBoundedLocalPreparer(), self._accounting
        )
        if type(baseline) is not LocalReplanOnlyBaseline:
            raise RuntimeError("T_B4_NATIVE_INSTANCE_TYPE_MISMATCH")
        self._baseline = baseline
        obligation = self._obligation(update)
        before = source.export_atomic()
        self._candidate = baseline.prepare_update(
            ego_state={
                "frame": frame,
                "sim_time_s": sim_time_s,
                "pose_xyz_yaw": [
                    *topology["ego"]["location_xyz_m"],
                    topology["ego"]["rotation_pitch_yaw_roll_deg"][1],
                ],
            },
            new_instruction=update.instruction_new,
            semantic_update_event_id=update.update_event_id,
            updated_obligation_identity=obligation.identity,
            local_branch_or_connector_identity=obligation.branch_or_connector_identity,
            bounded_local_route_rows=local_rows,
            target_points_ego_local_xy_m=_ego_local_xy(local_rows, topology),
        )
        after = source.export_atomic()
        self._lifecycle = CandidateLifecycle(self._candidate)
        self._local_probe_proxy.stage(
            self._candidate,
            (local_rows[0], local_rows[1]),
            frame,
            update.update_event_id,
        )
        _write_once(
            self._output / "t_b4_dispatch.json",
            {
                "resolved_instance_type": type(baseline).__module__
                + "."
                + type(baseline).__qualname__,
                "candidate": self._candidate,
                "global_route_identity_before": before.route_identity,
                "global_route_identity_after": after.route_identity,
                "global_route_generation_before": before.route_generation,
                "global_route_generation_after": after.route_generation,
                "global_planner_call_count": self._accounting.count(
                    CallKind.GLOBAL_PLANNER
                ),
                "reconnect_count": self._accounting.count(CallKind.RECONNECT),
                "next_normal_forward_target_replacement_staged": True,
            },
        )

    def _b6_decision(
        self,
        *,
        frame: int,
        topology: Mapping[str, Any],
        observable: Any,
    ) -> tuple[Any, Any]:
        if type(self._baseline) is not DriveClarifyTransitionBaseline:
            raise RuntimeError("T_B6_NATIVE_DISPATCH_TYPE_MISMATCH")
        assert self._p_old is not None
        assert self._candidate is not None
        assert self._global_task is not None
        current, resolved = self._v11_routes(self._candidate)
        admissibility = assess_with_existing_oracle_free_admissibility_owner(
            ReplanAdmissibility(),
            ego=self._v11_ego(frame, topology),
            current_route=current,
            resolved_route=resolved,
            preview_plan=None,
            same_G=True,
            hard_safety_allows=True,
        )
        decision = self._baseline.decide_transition(
            p_old=self._p_old,
            p_new=self._candidate,
            ego_state={
                "frame": frame,
                "location_xyz_m": topology["ego"]["location_xyz_m"],
                "yaw_degrees": topology["ego"]["rotation_pitch_yaw_roll_deg"][1],
                "velocity_world_mps": topology["ego"]["velocity_world_mps"],
            },
            observable_commitment=observable,
            global_task=self._global_task,
            admissibility=admissibility,
            transition_state={
                "pending_candidate_sha256": self._candidate.canonical_sha256
            },
        )
        return decision, admissibility

    def _prepare_t_b6(
        self,
        update: RuntimeSemanticUpdate,
        frame: int,
        sim_time_s: float,
        topology: Mapping[str, Any],
        source: Any,
        observable: Any,
    ) -> None:
        assert self._global_task is not None and self._p_old is not None
        self._candidate = self._prepare_common_candidate(
            update, frame, sim_time_s, topology, source
        )
        self._lifecycle = CandidateLifecycle(self._candidate)
        self._installer = _NativeCandidateInstaller(
            self._inner, self._global_task, self._accounting
        )
        decision, admissibility = self._b6_decision(
            frame=frame, topology=topology, observable=observable
        )
        self._last_decision = decision
        receipt = self._baseline.commit_or_defer(
            decision=decision,
            lifecycle=self._lifecycle,
            boundary=PlanningBoundary(frame, True, False, True, True),
            installer=self._installer,
            expected_old_generation=self._p_old.route_generation,
        )
        _write_once(
            self._output / "t_b6_dispatch.json",
            {
                "resolved_instance_type": type(self._baseline).__module__
                + "."
                + type(self._baseline).__qualname__,
                "candidate": self._candidate,
                "observable_commitment": observable,
                "frozen_admissibility": admissibility,
                "decision": decision,
                "install_receipt": receipt,
                "authored_commitment_point_index": None,
                "oracle_fields_present": False,
                "runtime_config_has_t_bucket": False,
                "default_thresholds_only": True,
            },
        )

    def _process_update(
        self,
        update: RuntimeSemanticUpdate,
        frame: int,
        sim_time_s: float,
        topology: Mapping[str, Any],
        source: Any,
        observable: Any,
        p_old_capture: Any,
    ) -> None:
        if self._update_processed:
            raise RuntimeError("DUPLICATE_RUNTIME_UPDATE")
        self._update_processed = True
        self._update = update
        self._update_topology = topology
        self._append(
            EventType.UPDATE_RECEIVED,
            frame=frame,
            sim_time_s=sim_time_s,
            update_id=update.update_event_id,
            reason="ORACLE_FREE_RUNTIME_SEMANTIC_UPDATE_RECEIVED",
            payload={
                "instruction_old": update.instruction_old,
                "instruction_new": update.instruction_new,
                "runtime_update_sha256": canonical_sha256(update),
            },
        )
        if p_old_capture.snapshot is None:
            raise RuntimeError("NATIVE_P_OLD_UNAVAILABLE:" + str(p_old_capture.unknown))
        self._p_old = p_old_capture.snapshot
        self._append(
            EventType.P_OLD_FROZEN,
            frame=frame,
            sim_time_s=sim_time_s,
            update_id=update.update_event_id,
            reason="ATOMIC_PRE_POLL_AUTHORITY_SNAPSHOT_FROZEN",
            payload={"captured_before_runtime_update_poll": True},
            object_identity=self._p_old.authoritative_route_identity,
            object_sha256=self._p_old.canonical_sha256,
            route_identity=self._p_old.authoritative_route_identity,
            route_generation=self._p_old.route_generation,
            global_task_identity_G=self._p_old.global_task.identity,
            p_old_sha256=self._p_old.canonical_sha256,
        )
        observable_payload = {
            "state": observable.state.value,
            "oracle_fields_present": False,
            "reason_codes": observable.reason_codes,
        }
        self._append(
            EventType.OBSERVABLE_COMMITMENT_CAPTURED,
            frame=frame,
            sim_time_s=sim_time_s,
            update_id=update.update_event_id,
            reason="OBSERVABLE_ALLOWLIST_ONLY_CAPTURED",
            payload=observable_payload,
            object_identity="observable-commitment",
            object_sha256=canonical_sha256(observable),
            availability="AVAILABLE",
        )
        _write_once(
            self._output / "native_p_old.json",
            {"p_old": self._p_old, "observable_commitment": observable, "topology": topology},
        )
        baseline = str(self._config["baseline_id"])
        if baseline == BaselineId.T_B1.value:
            self._prepare_t_b1(update, frame, sim_time_s, topology, source)
        elif baseline == BaselineId.T_B2.value:
            self._prepare_t_b2(update, frame, sim_time_s, topology, source)
        elif baseline == BaselineId.T_B3.value:
            self._prepare_t_b3(update, frame, sim_time_s, topology, source)
        elif baseline == BaselineId.T_B4.value:
            self._prepare_t_b4(update, frame, sim_time_s, topology, source)
        elif baseline == BaselineId.T_B5.value:
            self._prepare_t_b5(update, frame, sim_time_s)
        elif baseline == BaselineId.T_B6.value:
            self._prepare_t_b6(
                update, frame, sim_time_s, topology, source, observable
            )
        else:  # resolve_baseline_class already makes this unreachable.
            raise RuntimeError("NATIVE_BASELINE_DISPATCH_UNREACHABLE:" + baseline)
        _write_once(
            self._output / "native_dispatch_exercised.json",
            {
                "baseline_id": baseline,
                "resolved_class": self._resolved_baseline_type.__qualname__,
                "module": self._resolved_baseline_type.__module__,
                "exact_instance_type": type(self._baseline).__module__
                + "."
                + type(self._baseline).__qualname__,
                "dispatch_binding": self._dispatch_binding,
                "update_event_id": update.update_event_id,
                "sim_frame": frame,
                "fallback_used": False,
            },
        )

    def _maybe_finalize_global_candidate(
        self, frame: int, sim_time_s: float, update_id: str
    ) -> None:
        if self._lifecycle is None or self._installer is None or self._candidate is None:
            return
        if self._lifecycle.state.value != "CANDIDATE_COMMITTED":
            return
        if frame <= self._lifecycle.events[-1].frame:
            return
        binding = self._installer.binding
        planner = self._inner._route_planner
        if binding is None or str(planner.last_consumed_route_identity) != binding.physical_route_identity:
            return
        self._lifecycle.consume(
            frame=frame,
            consumed_route_identity=self._candidate.candidate_route_identity,
            consumed_route_generation=int(planner.online_update_generation),
        )
        self._append(
            EventType.NEXT_LEGITIMATE_PLANNER_CONSUMPTION,
            frame=frame,
            sim_time_s=sim_time_s,
            update_id=update_id,
            reason="NEXT_NORMAL_INNER_RUN_STEP_CONSUMED_PHYSICAL_BOUND_ROUTE",
            payload={
                "normal_cycle": True,
                "active_equals_installed": str(planner.active_route_identity)
                == binding.physical_route_identity,
                "consumed_equals_installed": str(planner.last_consumed_route_identity)
                == binding.physical_route_identity,
                "physical_route_identity": binding.physical_route_identity,
                "semantic_physical_binding_sha256": binding.binding_sha256,
                "vla_forward_id": "normal-forward-" + canonical_sha256(
                    {"frame": frame, "owner": "team_code.agent_simlingo.LingoAgent.run_step"}
                )[:24],
            },
            route_identity=self._candidate.candidate_route_identity,
            route_generation=int(planner.online_update_generation),
            global_task_identity_G=self._global_task.identity,
            p_new_sha256=self._candidate.canonical_sha256,
        )
        self._append(
            EventType.EPISODE_TERMINAL,
            frame=frame,
            sim_time_s=sim_time_s,
            update_id=update_id,
            reason="BOUNDED_ENGINEERING_CHAIN_COMPLETE",
            payload={"terminal": "COMPLETED"},
        )
        baseline_id = str(self._config["baseline_id"])
        common_payload = {
            "baseline_id": baseline_id,
            "frame": frame,
            "candidate_lifecycle": self._lifecycle.events,
            "physical_active_route_identity": planner.active_route_identity,
            "physical_consumed_route_identity": planner.last_consumed_route_identity,
            "identity_binding": binding,
            "accounting": self._accounting.snapshot(),
            "one_normal_forward_per_wrapper_cycle": True,
            "wrapper_control_writes": 0,
        }
        if baseline_id != BaselineId.T_B2.value:
            _write_once(self._output / "baseline_consumption.json", common_payload)
            _write_once(
                self._output / "native_obligation_complete.json",
                {
                    "baseline_id": baseline_id,
                    "completion_kind": "NEXT_NORMAL_FORWARD_CONSUMED_INSTALLED_CANDIDATE",
                    "sim_frame": frame,
                    "update_event_id": update_id,
                },
            )
            return
        assert self._stream is not None
        metrics = {
            "continuity_hook": MetricResult.unknown_result(
                unit="mixed",
                formula_version=CONTINUITY_FORMULA_VERSION,
                reason_code="ENGINEERING_QUALIFICATION_NO_EFFECT_ANALYSIS",
                missing_source="post-effect scientific window intentionally not evaluated",
                expected_owner="future formal measurement stage",
                affected_fields=("effect_metrics",),
            ),
            "global_task_hook": MetricResult.unknown_result(
                unit="task_status",
                formula_version=GLOBAL_TASK_FORMULA_VERSION,
                reason_code="ENGINEERING_QUALIFICATION_NO_EFFECT_ANALYSIS",
                missing_source="episode-level scientific terminal intentionally not evaluated",
                expected_owner="future formal measurement stage",
                affected_fields=("global_task_success",),
            ),
        }
        accounting = self._accounting.snapshot()
        accounting["native_normal_forward_frames"] = list(self._forward_frames)
        receipt = TransitionReceiptFinalizer().finalize(
            self._stream,
            method_freeze={
                "qualification_only": True,
                "threshold_changes": False,
                "model_changes": False,
                "controller_changes": False,
                "scientific_method_changes": False,
            },
            planning_accounting=accounting,
            metric_results=metrics,
        )
        self._stream.write_once(self._output / "transition_events.jsonl")
        _write_once(self._output / "transition_receipt.json", receipt)
        _write_once(
            self._output / "t_b2_consumption.json",
            {
                "frame": frame,
                "candidate_lifecycle": self._lifecycle.events,
                "physical_active_route_identity": planner.active_route_identity,
                "physical_consumed_route_identity": planner.last_consumed_route_identity,
                "identity_binding": binding,
                "accounting": accounting,
                "metric_hooks": metrics,
            },
        )
        _write_once(
            self._output / "native_obligation_complete.json",
            {
                "baseline_id": baseline_id,
                "completion_kind": "NEXT_NORMAL_FORWARD_CONSUMED_INSTALLED_CANDIDATE",
                "sim_frame": frame,
                "update_event_id": update_id,
            },
        )

    def _maybe_advance_t_b3(
        self,
        frame: int,
        sim_time_s: float,
        topology: Mapping[str, Any],
    ) -> None:
        if type(self._baseline) is not FinishOldFirstBaseline:
            return
        if self._baseline.pending_candidate is None or self._update_topology is None:
            return
        start = self._update_topology
        start_common = int(start["road_id"]) in {48, 811, 791}
        old_terminal = (
            start_common
            and int(topology["road_id"]) == 39
            and int(topology["lane_id"]) == -1
        ) or (
            int(start["road_id"]) == 39
            and bool(start["right_driving_lane"]["present"])
            and not bool(topology["right_driving_lane"]["present"])
        )
        if not old_terminal:
            return
        assert self._candidate is not None and self._p_old is not None
        ego_xyz = tuple(float(value) for value in topology["ego"]["location_xyz_m"])
        first = self._candidate.active_candidate_suffix[0]
        still_continuous = math.dist(
            ego_xyz, (first.x_m, first.y_m, first.z_m)
        ) <= float(self._inner._online_route_update_owner.continuity_threshold_m)
        semantic_expired = "current-opportunity" in str(
            self._config["updated_branch_identity"]
        ).casefold()
        opportunity_still_valid = still_continuous and not semantic_expired
        decision = self._baseline.on_old_terminal(
            opportunity_still_valid=opportunity_still_valid,
            terminal_frame=frame,
        )
        receipt = None
        if decision.outcome is TransitionOutcome.COMMIT_NOW:
            assert self._global_task is not None and self._lifecycle is not None
            self._installer = _NativeCandidateInstaller(
                self._inner, self._global_task, self._accounting
            )
            receipt = self._baseline.commit_or_record(
                lifecycle=self._lifecycle,
                installer=self._installer,
                boundary=PlanningBoundary(frame, True, False, True, True),
                expected_old_generation=self._p_old.route_generation,
            )
        else:
            assert self._lifecycle is not None
            self._baseline.commit_or_record(
                lifecycle=self._lifecycle,
                installer=_NativeCandidateInstaller(
                    self._inner, self._global_task, self._accounting
                ),
                boundary=PlanningBoundary(frame, True, False, True, True),
                expected_old_generation=self._p_old.route_generation,
            )
        _write_once(
            self._output / "t_b3_old_terminal.json",
            {
                "sim_frame": frame,
                "sim_time_s": sim_time_s,
                "opportunity_still_valid": opportunity_still_valid,
                "decision": decision,
                "install_receipt": receipt,
                "no_rescue_search": True,
            },
        )

    def _maybe_advance_t_b6(
        self,
        frame: int,
        sim_time_s: float,
        topology: Mapping[str, Any],
        observable: Any,
    ) -> None:
        if type(self._baseline) is not DriveClarifyTransitionBaseline:
            return
        if self._last_decision is None or self._lifecycle is None:
            return
        if self._last_decision.outcome is not TransitionOutcome.DEFER_COMMIT:
            return
        assert self._p_old is not None and self._installer is not None
        decision, admissibility = self._b6_decision(
            frame=frame, topology=topology, observable=observable
        )
        receipt = self._baseline.commit_or_defer(
            decision=decision,
            lifecycle=self._lifecycle,
            boundary=PlanningBoundary(frame, True, False, True, True),
            installer=self._installer,
            expected_old_generation=self._p_old.route_generation,
        )
        self._last_decision = decision
        _write_once(
            self._output / "t_b6_reevaluations" / f"reevaluation_{frame:08d}.json",
            {
                "sim_frame": frame,
                "sim_time_s": sim_time_s,
                "same_pending_candidate_sha256": self._candidate.canonical_sha256,
                "observable_commitment": observable,
                "frozen_admissibility": admissibility,
                "decision": decision,
                "install_receipt": receipt,
            },
        )

    def _maybe_mark_noninstall_obligation_complete(self, frame: int) -> None:
        if self._update is None or frame <= self._update.actual_injection_frame:
            return
        target = self._output / "native_obligation_complete.json"
        if target.exists():
            return
        baseline = str(self._config["baseline_id"])
        evidence: str | None = None
        if baseline == BaselineId.T_B3.value and (
            self._output / "t_b3_dispatch.json"
        ).exists():
            evidence = "FROZEN_FINISH_OLD_FIRST_WAIT_ACTIVE"
        elif baseline == BaselineId.T_B4.value and (
            self._output / "t_b4_local_consumption.json"
        ).exists():
            evidence = "NEXT_NORMAL_FORWARD_CONSUMED_LOCAL_REPLACEMENT"
        elif baseline == BaselineId.T_B5.value and (
            self._output / "t_b5_post_forward.json"
        ).exists():
            evidence = "NEXT_NORMAL_FORWARD_CONSUMED_HISTORY_ONLY_PROMPT"
        elif baseline == BaselineId.T_B6.value and (
            self._output / "t_b6_dispatch.json"
        ).exists():
            evidence = "FROZEN_B6_DECISION_PATH_AND_REEVALUATION_REACHED"
        if evidence is not None:
            _write_once(
                target,
                {
                    "baseline_id": baseline,
                    "completion_kind": evidence,
                    "sim_frame": frame,
                    "update_event_id": self._update.update_event_id,
                },
            )

    def _bind_b4_probe_after_inner_initialization(self) -> None:
        if str(self._config["baseline_id"]) != BaselineId.T_B4.value:
            return
        if self._local_probe_proxy is not None:
            if self._inner._dc_probe is not self._local_probe_proxy:
                raise RuntimeError("T_B4_LOCAL_PROBE_PROXY_OWNERSHIP_CHANGED")
            return
        if not self._inner.initialized:
            return
        wrapped = getattr(self._inner, "_dc_probe", None)
        owner = getattr(self._inner, "_online_route_update_owner", None)
        if wrapped is None or owner is None:
            raise RuntimeError("T_B4_INNER_LAZY_INITIALIZATION_INCOMPLETE")
        self._local_probe_proxy = _LocalNavigationProbeProxy(
            wrapped, owner, self._output
        )
        self._inner._dc_probe = self._local_probe_proxy

    def run_step(self, input_data: Mapping[str, Any], timestamp: float, sensors: Any = None) -> Any:
        if not self._inner.initialized:
            control = self._inner.run_step(input_data, timestamp, sensors)
            self._bind_b4_probe_after_inner_initialization()
            self._last_control = control
            return control
        self._bind_b4_probe_after_inner_initialization()
        frame = _frame_from_input(input_data)
        sim_time_s = float(timestamp)
        inner_step_before = int(self._inner.step)
        control = self._inner.run_step(input_data, timestamp, sensors)
        if int(self._inner.step) != inner_step_before + 1:
            raise RuntimeError("INNER_RUN_STEP_COUNT_NOT_EXACTLY_ONE")
        self._forward_frames.append(frame)
        _write_once(
            self._output / "controls" / f"control_{frame:08d}.json",
            {
                "sim_frame": frame,
                "inner_step_before": inner_step_before,
                "inner_step_after": int(self._inner.step),
                "exact_returned_control_object_id": id(control),
                "steer": float(control.steer),
                "throttle": float(control.throttle),
                "brake": float(control.brake),
                "wrapper_control_computation_count": 0,
                "wrapper_control_write_count": 0,
            },
        )
        if self._update_processed and self._update is not None:
            self._maybe_finalize_global_candidate(
                frame, sim_time_s, self._update.update_event_id
            )
        t_b5_pre = self._output / "t_b5_pre_forward.json"
        t_b5_post = self._output / "t_b5_post_forward.json"
        if (
            self._update_processed
            and self._config["baseline_id"] == BaselineId.T_B5.value
            and t_b5_pre.exists()
            and not t_b5_post.exists()
        ):
            pre = json.loads(t_b5_pre.read_text())
            if frame > int(pre["sim_frame"]):
                _write_once(
                    t_b5_post,
                    {
                        "update_boundary_frame": int(pre["sim_frame"]),
                        "normal_forward_frame": frame,
                        "normal_forward_owner": "team_code.agent_simlingo.LingoAgent.run_step",
                        "normal_forward_count_this_wrapper_cycle": 1,
                        "model_object_identity_unchanged": pre["model_object_identity"]
                        == id(self._inner.model),
                        "custom_prompt_still_exact": self._inner.custom_prompt
                        == pre["history_payload"]["prompt_utf8"],
                        "custom_prompt_embedded_in_inner_prompt": self._inner.custom_prompt
                        in self._inner.prompt,
                        "returned_control_object_id": id(control),
                    },
                )
        topology = _topology_boundary(frame, sim_time_s)
        boundary_path = self._boundary_dir / f"boundary_{frame:08d}.json"
        _write_once(boundary_path, topology)
        source = self._source(frame, sim_time_s, topology)
        observable = self._observable(frame, sim_time_s, topology)
        p_old_capture = capture_p_old(source, observable)
        update = None if self._update_processed else self._poll_runtime_update(frame)
        if update is not None:
            self._process_update(
                update, frame, sim_time_s, topology, source, observable, p_old_capture
            )
        elif self._update_processed:
            self._maybe_advance_t_b3(frame, sim_time_s, topology)
            self._maybe_advance_t_b6(frame, sim_time_s, topology, observable)
            self._maybe_mark_noninstall_obligation_complete(frame)
        self._last_control = control
        return control

    def destroy(self, results: Any = None) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        status = {
            "schema_version": "driveclarify.rq2.native_agent_terminal.v1",
            "update_processed": self._update_processed,
            "baseline_id": self._config.get("baseline_id"),
            "candidate_lifecycle": (
                None if self._lifecycle is None else _plain(self._lifecycle.events)
            ),
            "normal_wrapper_cycles": len(self._forward_frames),
            "normal_forward_frames": self._forward_frames,
            "seed": self._config.get("seed"),
            "exact_inner_type": type(self._inner).__module__
            + "."
            + type(self._inner).__qualname__,
            "one_control_authority": True,
            "planning_accounting": self._accounting.snapshot(),
            "last_transition_decision": self._last_decision,
            "runtime_update": self._update,
            "resolved_baseline_type": (
                None
                if self._resolved_baseline_type is None
                else self._resolved_baseline_type.__module__
                + "."
                + self._resolved_baseline_type.__qualname__
            ),
            "semantic_physical_binding": (
                None if self._installer is None else self._installer.binding
            ),
            "global_route_generation": (
                None
                if not hasattr(self._inner, "_route_planner")
                else int(self._inner._route_planner.online_update_generation)
            ),
        }
        try:
            _write_once(self._output / "agent_terminal.json", status)
        except FileExistsError:
            pass
        self._inner.destroy(results)
