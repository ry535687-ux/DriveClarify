"""RQ2-T 2A 前瞻补充合同、共享前缀控制所有者与终止分类器。"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


OWNER_ID = "SHARED_PREFIX_LONGITUDINAL_OWNER_V1"
OWNER_VERSION = "1.0.0"
MAX_2A_SIMULATED_HORIZON_S = 35.0
FIXED_DELTA_SECONDS = 0.05
EARLY_ACTIONABLE_LOWER_S = 3.0
LATE_ACTIONABLE_LOWER_S = 1.20
PASS_STATUS = "PASS_RQ2_T_2A_ADDENDUM_CLOSED_READY_FOR_FINAL_SEED_AUTHORIZATION"

_BINDINGS_PATH = Path(__file__).with_name("owner_bindings_v1.json")
_VALID_NATURAL_STATES = frozenset(
    {
        "PY_TREES_STATUS_SUCCESS",
        "PY_TREES_STATUS_FAILURE",
    }
)


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        .encode("utf-8")
    ).hexdigest()


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def load_owner_bindings(path: Path = _BINDINGS_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("owner_id") != OWNER_ID:
        raise ValueError("RQ2_T_2A_OWNER_ID_MISMATCH")
    if value.get("max_2a_simulated_horizon_s") != MAX_2A_SIMULATED_HORIZON_S:
        raise ValueError("RQ2_T_2A_HORIZON_MISMATCH")
    bindings = value.get("bindings")
    expected = {"REF-01", "REF-02", "LMK-01", "LMK-02", "ORD-01", "ORD-02", "USC-02", "USC-03"}
    if not isinstance(bindings, dict) or set(bindings) != expected:
        raise ValueError("RQ2_T_2A_EXACT_EIGHT_OWNER_BINDINGS_REQUIRED")
    if "TBD_ENGINEERING_QUERY" in json.dumps(value, sort_keys=True):
        raise ValueError("RQ2_T_2A_OWNER_BINDING_UNRESOLVED")
    return value


class TTCmtStratum(str, Enum):
    EARLY_ACTIONABLE = "EARLY_ACTIONABLE"
    LATE_ACTIONABLE = "LATE_ACTIONABLE"
    TOO_LATE_NONACTIONABLE = "TOO_LATE_NONACTIONABLE"
    COMMITMENT_BOUNDARY = "COMMITMENT_BOUNDARY"


def classify_ttcmt_stratum(ttcmt_simulation_s: float) -> TTCmtStratum:
    if not _finite(ttcmt_simulation_s) or float(ttcmt_simulation_s) < 0.0:
        raise ValueError("RQ2_T_TTCMT_STRATUM_INPUT_INVALID")
    ttcmt = float(ttcmt_simulation_s)
    if ttcmt >= EARLY_ACTIONABLE_LOWER_S:
        return TTCmtStratum.EARLY_ACTIONABLE
    if ttcmt >= LATE_ACTIONABLE_LOWER_S:
        return TTCmtStratum.LATE_ACTIONABLE
    if ttcmt > 0.0:
        return TTCmtStratum.TOO_LATE_NONACTIONABLE
    return TTCmtStratum.COMMITMENT_BOUNDARY


class ExecutionTerminalClass(str, Enum):
    COMMITMENT_OBSERVED = "COMMITMENT_OBSERVED"
    NATURAL_SCENARIO_TREE_SUCCESS = "NATURAL_SCENARIO_TREE_SUCCESS"
    NATURAL_SCENARIO_TREE_FAILURE = "NATURAL_SCENARIO_TREE_FAILURE"
    MAX_SIMULATED_HORIZON_REACHED = "MAX_SIMULATED_HORIZON_REACHED"
    INVALID_ENGINEERING_TERMINATION = "INVALID_ENGINEERING_TERMINATION"


class H1CensoringStatus(str, Enum):
    EVENT_TIME_OBSERVED = "EVENT_TIME_OBSERVED"
    RIGHT_CENSORED_AT_NATURAL_TERMINAL = "RIGHT_CENSORED_AT_NATURAL_TERMINAL"
    RIGHT_CENSORED_AT_ADMINISTRATIVE_HORIZON = "RIGHT_CENSORED_AT_ADMINISTRATIVE_HORIZON"
    NOT_ESTIMABLE_ENGINEERING_INVALID = "NOT_ESTIMABLE_ENGINEERING_INVALID"


class H2PrimaryCategory(str, Enum):
    WINDOW_OBSERVED = "WINDOW_OBSERVED"
    NO_WINDOW_EVIDENCE_TOO_LATE = "NO_WINDOW_EVIDENCE_TOO_LATE"
    NO_WINDOW_EVIDENCE_NEVER_SUFFICIENT = "NO_WINDOW_EVIDENCE_NEVER_SUFFICIENT"
    RIGHT_CENSORED = "RIGHT_CENSORED"
    COMMITMENT_NOT_REACHED = "COMMITMENT_NOT_REACHED"
    INVALID_ENGINEERING_EVIDENCE = "INVALID_ENGINEERING_EVIDENCE"


@dataclass(frozen=True)
class TerminalClassification:
    execution_terminal_class: str
    commitment_observed: bool
    commitment_time_simulation_s: Optional[float]
    ttcmt_simulation_s: Optional[float]
    opportunity_duration_simulation_s: Optional[float]
    h1_censoring_status: str
    h2_primary_category: str
    denominator_eligible: bool
    natural_terminal_state: Optional[str]
    wall_time_has_scientific_authority: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["classification_digest"] = _canonical_sha256(value)
        return value


def classify_primary_terminal(
    *,
    engineering_integrity_valid: bool,
    commitment_time_simulation_s: Optional[float],
    first_epistemic_sufficient_time_simulation_s: Optional[float],
    clarification_deadline_simulation_s: Optional[float],
    simulation_elapsed_s: float,
    natural_terminal_state: Optional[str] = None,
    wall_timeout_observed: bool = False,
) -> TerminalClassification:
    """按冻结优先级分类；wall 信号只能使工程证据失效。"""

    if not _finite(simulation_elapsed_s) or float(simulation_elapsed_s) < 0.0:
        raise ValueError("RQ2_T_SIMULATION_ELAPSED_INVALID")
    engineering_valid = bool(engineering_integrity_valid) and not wall_timeout_observed
    if natural_terminal_state is not None and natural_terminal_state not in _VALID_NATURAL_STATES:
        engineering_valid = False
    if not engineering_valid:
        return TerminalClassification(
            execution_terminal_class=ExecutionTerminalClass.INVALID_ENGINEERING_TERMINATION.value,
            commitment_observed=False,
            commitment_time_simulation_s=None,
            ttcmt_simulation_s=None,
            opportunity_duration_simulation_s=None,
            h1_censoring_status=H1CensoringStatus.NOT_ESTIMABLE_ENGINEERING_INVALID.value,
            h2_primary_category=H2PrimaryCategory.INVALID_ENGINEERING_EVIDENCE.value,
            denominator_eligible=False,
            natural_terminal_state=natural_terminal_state,
        )

    if commitment_time_simulation_s is not None:
        if not _finite(commitment_time_simulation_s):
            raise ValueError("RQ2_T_COMMITMENT_TIME_INVALID")
        commitment = float(commitment_time_simulation_s)
        sufficient = first_epistemic_sufficient_time_simulation_s
        deadline = clarification_deadline_simulation_s
        if sufficient is not None and not _finite(sufficient):
            raise ValueError("RQ2_T_SUFFICIENCY_TIME_INVALID")
        if deadline is not None and not _finite(deadline):
            raise ValueError("RQ2_T_DEADLINE_INVALID")
        ttcmt = (
            None
            if sufficient is None or float(sufficient) > commitment
            else commitment - float(sufficient)
        )
        if sufficient is not None and deadline is not None and float(sufficient) <= float(deadline):
            category = H2PrimaryCategory.WINDOW_OBSERVED
            duration = max(0.0, float(deadline) - float(sufficient))
        elif sufficient is not None and deadline is not None and float(deadline) < float(sufficient) <= commitment:
            category = H2PrimaryCategory.NO_WINDOW_EVIDENCE_TOO_LATE
            duration = None
        else:
            category = H2PrimaryCategory.NO_WINDOW_EVIDENCE_NEVER_SUFFICIENT
            duration = None
        return TerminalClassification(
            execution_terminal_class=ExecutionTerminalClass.COMMITMENT_OBSERVED.value,
            commitment_observed=True,
            commitment_time_simulation_s=commitment,
            ttcmt_simulation_s=ttcmt,
            opportunity_duration_simulation_s=duration,
            h1_censoring_status=H1CensoringStatus.EVENT_TIME_OBSERVED.value,
            h2_primary_category=category.value,
            denominator_eligible=True,
            natural_terminal_state=natural_terminal_state,
        )

    if natural_terminal_state in _VALID_NATURAL_STATES and float(simulation_elapsed_s) < MAX_2A_SIMULATED_HORIZON_S:
        terminal = (
            ExecutionTerminalClass.NATURAL_SCENARIO_TREE_SUCCESS
            if natural_terminal_state == "PY_TREES_STATUS_SUCCESS"
            else ExecutionTerminalClass.NATURAL_SCENARIO_TREE_FAILURE
        )
        return TerminalClassification(
            execution_terminal_class=terminal.value,
            commitment_observed=False,
            commitment_time_simulation_s=None,
            ttcmt_simulation_s=None,
            opportunity_duration_simulation_s=None,
            h1_censoring_status=H1CensoringStatus.RIGHT_CENSORED_AT_NATURAL_TERMINAL.value,
            h2_primary_category=H2PrimaryCategory.RIGHT_CENSORED.value,
            denominator_eligible=True,
            natural_terminal_state=natural_terminal_state,
        )

    if float(simulation_elapsed_s) >= MAX_2A_SIMULATED_HORIZON_S:
        return TerminalClassification(
            execution_terminal_class=ExecutionTerminalClass.MAX_SIMULATED_HORIZON_REACHED.value,
            commitment_observed=False,
            commitment_time_simulation_s=None,
            ttcmt_simulation_s=None,
            opportunity_duration_simulation_s=None,
            h1_censoring_status=H1CensoringStatus.RIGHT_CENSORED_AT_ADMINISTRATIVE_HORIZON.value,
            h2_primary_category=H2PrimaryCategory.COMMITMENT_NOT_REACHED.value,
            denominator_eligible=True,
            natural_terminal_state=natural_terminal_state,
        )
    raise ValueError("RQ2_T_EPISODE_NOT_AT_SCIENTIFIC_TERMINAL")


def guard_formal_seed_materialization(
    *, addendum_status: str, final_seed_authorization: bool
) -> None:
    if addendum_status != PASS_STATUS or final_seed_authorization is not True:
        raise PermissionError("RQ2_T_2A_FORMAL_SEED_MATERIALIZATION_NOT_AUTHORIZED")


class SharedPrefixLongitudinalOwnerV1:
    """只从冻结场景路线、车辆状态与原生交通灯安全状态生成单一 PID 输入。"""

    enabled = True
    scientific_control_owner = True
    fail_closed = True

    def __init__(
        self,
        agent: Any,
        *,
        scene_key: str,
        route_sha256: str,
        scenario_configuration_sha256: str,
        receipt_path: Optional[str] = None,
    ) -> None:
        document = load_owner_bindings()
        binding = document["bindings"].get(scene_key)
        if binding is None:
            raise ValueError("RQ2_T_2A_UNKNOWN_SCENE_BINDING")
        if route_sha256 != binding["route_sha256"]:
            raise ValueError("RQ2_T_2A_ROUTE_BINDING_MISMATCH")
        if scenario_configuration_sha256 != binding["scenario_configuration_sha256"]:
            raise ValueError("RQ2_T_2A_SCENARIO_CONFIGURATION_MISMATCH")
        self._agent = agent
        self._scene_key = scene_key
        self._binding = binding
        self._receipt_path = receipt_path
        self._latest_simulation_s: Optional[float] = None
        self._latest_local_route: Any = None
        self._native_signal_stop = False
        self._plan_selection_count = 0
        self._pid_observation_count = 0
        self._normal_forward_observation_count = 0
        self._control_observation_count = 0
        self._terminal_event: Optional[dict[str, Any]] = None
        self._best_boundary_distance_m: Optional[float] = None
        self._closed = False

    @property
    def binding(self) -> Mapping[str, Any]:
        return self._binding

    def prepare_model_input(self, model_input: Any) -> Any:
        return model_input

    def on_tick(
        self,
        input_data: Any,
        tick_data: Any,
        timestamp: Any,
        frame_id: Any,
        observation_id: Any,
    ) -> None:
        del input_data, tick_data, frame_id, observation_id
        if not _finite(timestamp):
            raise RuntimeError("RQ2_T_2A_SIMULATION_CLOCK_INVALID")
        self._latest_simulation_s = float(timestamp)
        self._native_signal_stop = self._read_native_signal_stop()
        self._latest_local_route = self._build_shared_local_route()
        self._observe_commitment_boundary()

    def on_model_output(self, *args: Any, **kwargs: Any) -> None:
        # 不读取正常 VLA 输出；仅记录同一次既有 forward 的回调次数。
        del args, kwargs
        self._normal_forward_observation_count += 1

    def select_plan_source(
        self, baseline_route: Any, baseline_speed: Any, current_monotonic: float
    ) -> tuple[Any, Any]:
        # 科学控制与 wall clock、VLA 输出值均无关。
        del baseline_route, baseline_speed, current_monotonic
        if self._latest_local_route is None:
            raise RuntimeError("RQ2_T_2A_OWNER_ROUTE_NOT_READY")
        try:
            import torch
        except Exception as exc:  # pragma: no cover - 正式环境必须有 torch
            raise RuntimeError("RQ2_T_2A_TORCH_UNAVAILABLE") from exc
        device = getattr(self._agent, "device", None)
        route = torch.as_tensor(
            self._latest_local_route,
            dtype=torch.float32,
            device=device,
        ).unsqueeze(0)
        target_speed = 0.0 if self._native_signal_stop else float(self._binding["target_speed_mps"])
        speed_rows = [[index * target_speed / 4.0, 0.0] for index in range(10)]
        speed = torch.as_tensor(speed_rows, dtype=torch.float32, device=device).unsqueeze(0)
        self._plan_selection_count += 1
        return route, speed

    def on_pid_invocation(self, current_monotonic: float) -> None:
        del current_monotonic
        self._pid_observation_count += 1

    def on_control(self, control: Any, gt_velocity: Any, current_monotonic: float) -> None:
        del control, gt_velocity, current_monotonic
        self._control_observation_count += 1

    def commit(self) -> None:
        return None

    def should_end_scientific_horizon(self, simulation_elapsed_s: float) -> bool:
        if not _finite(simulation_elapsed_s):
            raise RuntimeError("RQ2_T_2A_SIMULATION_CLOCK_INVALID")
        return bool(
            (
                self._terminal_event is not None
                and self._terminal_event.get("state") == "COMMITMENT_OBSERVED"
            )
            or float(simulation_elapsed_s) >= MAX_2A_SIMULATED_HORIZON_S
        )

    def record_execution_terminal(
        self, *, state: str, simulation_elapsed_s: float
    ) -> None:
        if state not in _VALID_NATURAL_STATES and state not in {
            "COMMITMENT_OBSERVED",
            "MAX_2A_SIMULATED_HORIZON_REACHED",
        }:
            raise RuntimeError("RQ2_T_2A_EXECUTION_TERMINAL_STATE_INVALID")
        if (
            self._terminal_event is not None
            and self._terminal_event.get("state") == "COMMITMENT_OBSERVED"
            and state == "MAX_2A_SIMULATED_HORIZON_REACHED"
        ):
            return
        self._terminal_event = {
            "state": state,
            "simulation_elapsed_s": float(simulation_elapsed_s),
            "wall_time_has_scientific_authority": False,
        }

    def summary(self) -> dict[str, Any]:
        value = {
            "schema_version": "driveclarify.rq2_t.shared_prefix_owner_runtime_receipt.v1",
            "owner_id": OWNER_ID,
            "owner_version": OWNER_VERSION,
            "scene_key": self._scene_key,
            "scene_id": self._binding["scene_id"],
            "route_sha256": self._binding["route_sha256"],
            "scenario_configuration_sha256": self._binding["scenario_configuration_sha256"],
            "latest_simulation_s": self._latest_simulation_s,
            "max_2a_simulated_horizon_s": MAX_2A_SIMULATED_HORIZON_S,
            "plan_selection_count": self._plan_selection_count,
            "normal_forward_observation_count": self._normal_forward_observation_count,
            "pid_observation_count": self._pid_observation_count,
            "control_observation_count": self._control_observation_count,
            "observer_control_write_count": 0,
            "owner_induced_model_forward_count": 0,
            "owner_induced_pid_count": 0,
            "owner_induced_route_planner_advance_count": 0,
            "best_boundary_distance_m": self._best_boundary_distance_m,
            "wall_time_has_scientific_authority": False,
            "terminal_event": self._terminal_event,
        }
        value["receipt_digest"] = _canonical_sha256(value)
        return value

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._receipt_path:
            target = Path(self._receipt_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self.summary(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, target)

    def _read_native_signal_stop(self) -> bool:
        if self._scene_key != "ORD-02":
            return False
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            hero = CarlaDataProvider.get_hero_actor()
            if hero is None or not hero.is_at_traffic_light():
                return False
            state = str(hero.get_traffic_light_state()).upper()
            return "RED" in state or "YELLOW" in state
        except Exception as exc:
            raise RuntimeError("RQ2_T_2A_NATIVE_SIGNAL_STATE_UNAVAILABLE") from exc

    def _observe_commitment_boundary(self) -> None:
        if self._terminal_event is not None:
            return
        state_log = getattr(self._agent, "state_log", None)
        translation = getattr(
            self._agent, "_world_to_route_planner_translation_xyz_m", None
        )
        if not state_log or translation is None or self._latest_simulation_s is None:
            raise RuntimeError("RQ2_T_2A_NATIVE_ROUTE_STATE_UNAVAILABLE")
        state = list(state_log[-1])
        if len(state) < 2 or not all(_finite(value) for value in state[:2]):
            raise RuntimeError("RQ2_T_2A_EGO_STATE_INVALID")
        boundary_world = self._binding["boundary_world_xyz"]
        boundary = [
            float(boundary_world[0]) + float(translation[0]),
            float(boundary_world[1]) + float(translation[1]),
        ]
        distance_m = math.dist([float(state[0]), float(state[1])], boundary)
        self._best_boundary_distance_m = (
            distance_m
            if self._best_boundary_distance_m is None
            else min(self._best_boundary_distance_m, distance_m)
        )
        if distance_m <= 0.75:
            self.record_execution_terminal(
                state="COMMITMENT_OBSERVED",
                simulation_elapsed_s=self._latest_simulation_s,
            )

    def _build_shared_local_route(self) -> list[list[float]]:
        planner = getattr(self._agent, "_route_planner", None)
        route = getattr(planner, "route", None)
        state_log = getattr(self._agent, "state_log", None)
        translation = getattr(self._agent, "_world_to_route_planner_translation_xyz_m", None)
        if planner is None or route is None or not route or not state_log or translation is None:
            raise RuntimeError("RQ2_T_2A_NATIVE_ROUTE_STATE_UNAVAILABLE")
        state = list(state_log[-1])
        if len(state) < 3 or not all(_finite(value) for value in state[:3]):
            raise RuntimeError("RQ2_T_2A_EGO_STATE_INVALID")
        ego = [float(state[0]), float(state[1])]
        yaw = float(state[2])
        boundary_world = self._binding["boundary_world_xyz"]
        boundary = [
            float(boundary_world[0]) + float(translation[0]),
            float(boundary_world[1]) + float(translation[1]),
        ]
        points = [ego]
        best_boundary_distance = math.dist(ego, boundary)
        for row in route:
            position = row[0]
            point = [float(position[0]), float(position[1])]
            if not all(_finite(value) for value in point):
                raise RuntimeError("RQ2_T_2A_ROUTE_POINT_INVALID")
            boundary_distance = math.dist(point, boundary)
            if boundary_distance > best_boundary_distance + 0.75:
                break
            if math.dist(points[-1], point) > 1e-6:
                points.append(point)
            best_boundary_distance = min(best_boundary_distance, boundary_distance)
            if boundary_distance <= 0.75:
                break
        if len(points) < 2:
            points.append(boundary)
        if math.dist(points[-1], boundary) <= 2.0 and math.dist(points[-1], boundary) > 1e-6:
            points.append(boundary)
        samples = _sample_polyline(points, count=20, maximum_distance_m=20.0)
        cosine = math.cos(yaw)
        sine = math.sin(yaw)
        local = []
        for point in samples:
            dx, dy = point[0] - ego[0], point[1] - ego[1]
            local.append([cosine * dx + sine * dy, -sine * dx + cosine * dy])
        return local


def _sample_polyline(
    points: Sequence[Sequence[float]], *, count: int, maximum_distance_m: float
) -> list[list[float]]:
    distances = [0.0]
    for left, right in zip(points, points[1:]):
        distances.append(distances[-1] + math.dist(left[:2], right[:2]))
    total = min(float(maximum_distance_m), distances[-1])
    if total <= 1e-6:
        return [[0.0, 0.0] for _ in range(count)]
    result = []
    segment = 1
    for index in range(1, count + 1):
        target = total * index / count
        while segment < len(distances) - 1 and distances[segment] < target:
            segment += 1
        left_distance, right_distance = distances[segment - 1], distances[segment]
        ratio = 0.0 if right_distance == left_distance else (target - left_distance) / (right_distance - left_distance)
        left, right = points[segment - 1], points[segment]
        result.append(
            [
                float(left[0]) + ratio * (float(right[0]) - float(left[0])),
                float(left[1]) + ratio * (float(right[1]) - float(left[1])),
            ]
        )
    return result


def build_shared_prefix_owner(agent: Any) -> SharedPrefixLongitudinalOwnerV1:
    scene_key = os.environ.get("DRIVECLARIFY_RQ2_T_2A_SCENE_KEY", "")
    route_sha256 = os.environ.get("DRIVECLARIFY_RQ2_T_2A_ROUTE_SHA256", "")
    config_sha256 = os.environ.get("DRIVECLARIFY_RQ2_T_2A_SCENARIO_CONFIG_SHA256", "")
    return SharedPrefixLongitudinalOwnerV1(
        agent,
        scene_key=scene_key,
        route_sha256=route_sha256,
        scenario_configuration_sha256=config_sha256,
        receipt_path=os.environ.get("DRIVECLARIFY_RQ2_T_2A_OWNER_RECEIPT"),
    )


__all__ = [
    "EARLY_ACTIONABLE_LOWER_S",
    "ExecutionTerminalClass",
    "H1CensoringStatus",
    "H2PrimaryCategory",
    "LATE_ACTIONABLE_LOWER_S",
    "MAX_2A_SIMULATED_HORIZON_S",
    "OWNER_ID",
    "OWNER_VERSION",
    "PASS_STATUS",
    "SharedPrefixLongitudinalOwnerV1",
    "TTCmtStratum",
    "build_shared_prefix_owner",
    "classify_primary_terminal",
    "classify_ttcmt_stratum",
    "guard_formal_seed_materialization",
    "load_owner_bindings",
]
