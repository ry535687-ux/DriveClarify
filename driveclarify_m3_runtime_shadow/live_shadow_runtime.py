"""One-event real-SimLingo, no-control DriveClarify live shadow runtime V0."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from driveclarify_candidate_stability.determinism import (
    RNGCoordinator,
    canonical_digest,
)
from driveclarify_candidate_stability.simlingo_live_adapter import (
    SimLingoCandidateSource,
    SimLingoTensorInputIsolation,
)
from driveclarify_candidate_stability.simlingo_sensitivity_pilot import (
    SimLingoSensitivityPilotBackend,
    explicit_sensitivity_pilot_config,
)
from .live_visualization import (
    LiveShadowVisualizerV0,
    copy_front_rgb_for_display,
)
from .ask_replanning_v0 import (
    AskDelayedAnswerReplanningV0,
    BLOCKED_FRESH_REPLAN,
    DEFAULT_HOLDING_LEASE_DURATION_S,
    DEFAULT_ORACLE_ANSWER,
    DEFAULT_ORACLE_DELAY_S,
    PASS_STATUS as ASK_REPLAN_PASS_STATUS,
)
from .physical_wait_v0 import (
    DEFAULT_PILOT_DURATION_S,
    OLD_CANDIDATES_EXIT_STATUS,
    PhysicalWaitExecutorV0,
    build_bounded_wait_pilot_binding,
)
from .limited_act_commit_v0 import (
    PASS_STATUS as LIMITED_ACT_PASS_STATUS,
    build_limited_act_commit,
)


LIVE_SHADOW_SCHEMA = "driveclarify.live_shadow_runtime.v0"
PASS_STATUS = (
    "PASS_BOUNDED_CARLA_NO_CONTROL_SHADOW_WITH_LIVE_VISUALIZATION_V0_"
    "READY_FOR_PHYSICAL_WAIT_HOLDING"
)
PHYSICAL_WAIT_PASS_STATUS = (
    "PASS_PHYSICAL_WAIT_HOLDING_V0_HOLD_CURRENT_VALID_PLAN_"
    "READY_FOR_ASK_DELAYED_ANSWER_REPLANNING"
)
PHYSICAL_WAIT_ACTIVE_STATUS = "PHYSICAL_WAIT_V0_HOLD_CURRENT_VALID_PLAN_ACTIVE"
PHYSICAL_WAIT_BLOCKED_STATUS = "BLOCKED_PHYSICAL_WAIT_M3_HOLDING_LEASE_BINDING"
PHYSICAL_WAIT_VIZ_PARTIAL_STATUS = (
    "PARTIAL_PASS_PHYSICAL_WAIT_V0_FRONT_CAMERA_VISUALIZATION_BLOCKED"
)
ASK_REPLAN_ACTIVE_STATUS = "ASK_REPLAN_V0_WAITING_FOR_DELAYED_ORACLE_ANSWER"
ASK_REPLAN_ANSWER_STATUS = "ASK_REPLAN_V0_ANSWER_RECEIVED_OLD_CANDIDATES_INVALIDATED"
ASK_REPLAN_RUNNING_STATUS = "ASK_REPLAN_V0_REPLANNING_FROM_LATEST_OBSERVATION"
ASK_REPLAN_VIZ_PARTIAL_STATUS = (
    "PARTIAL_PASS_ASK_WAIT_ANSWER_REPLAN_FRONT_CAMERA_VISUALIZATION_BLOCKED"
)
SOURCE_MISMATCH = "BLOCKED_LIVE_SHADOW_CANDIDATE_SOURCE_IDENTITY_MISMATCH"
CONTROL_BOUNDARY = "BLOCKED_SHADOW_OUTPUT_REACHED_VEHICLE_CONTROL"
RAW_INSTRUCTION = "Stop at the next branch."
INTERPRETATION_A = "Go straight at the next intersection, then stop."
INTERPRETATION_B = "Turn right at the next intersection, then stop."


def _truthy(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"} if value else False


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _tensor_digest(value: Any) -> str | None:
    if value is None:
        return None
    try:
        tensor = value.detach().contiguous().cpu()
        raw = tensor.numpy().tobytes()
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        return canonical_digest(repr(value))


def _tensor_points(value: Any) -> tuple[tuple[float, float], ...]:
    current = value
    if hasattr(current, "detach"):
        current = current.detach().float().cpu().tolist()
    while (
        isinstance(current, (list, tuple))
        and len(current) == 1
        and isinstance(current[0], (list, tuple))
    ):
        current = current[0]
    if not isinstance(current, (list, tuple)):
        raise ValueError("SIMLINGO_LIVE_ROUTE_OR_SPEED_NOT_SEQUENCE")
    points = []
    for point in current:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("SIMLINGO_LIVE_ROUTE_OR_SPEED_POINT_INVALID")
        points.append((float(point[0]), float(point[1])))
    return tuple(points)


def _command_value(value: Any) -> Any:
    try:
        return int(getattr(value, "value", value))
    except (TypeError, ValueError):
        return str(value)


def _small_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)) and len(value) <= 16:
        return [_small_value(item) for item in value]
    try:
        if hasattr(value, "tolist"):
            return value.tolist()
    except Exception:
        pass
    return repr(value)[:160]


def _controller_snapshot(controller: Any) -> Mapping[str, Any]:
    if controller is None:
        return {"status": "NOT_OBSERVABLE"}
    result: dict[str, Any] = {
        "type": type(controller).__module__ + "." + type(controller).__qualname__,
    }
    for name, value in getattr(controller, "__dict__", {}).items():
        if name in {
            "_window",
            "window",
            "_error_buffer",
            "error_buffer",
            "_saved_error",
            "_saved_input",
            "integral",
            "previous_error",
        }:
            try:
                result[name] = _small_value(list(value) if hasattr(value, "__iter__") and not isinstance(value, str) else value)
            except Exception:
                result[name] = repr(value)[:160]
    return result


def _route_planner_snapshot(agent: Any) -> Mapping[str, Any]:
    planner = getattr(agent, "_route_planner", None)
    if planner is None:
        return {"status": "NOT_OBSERVABLE"}
    route = getattr(planner, "route", None)
    front = []
    try:
        for index, item in enumerate(route):
            if index >= 6:
                break
            location = item[0]
            if hasattr(location, "x") and hasattr(location, "y"):
                x_value = float(location.x)
                y_value = float(location.y)
            else:
                x_value = float(location[0])
                y_value = float(location[1])
            front.append(
                [
                    x_value,
                    y_value,
                    str(item[1]) if len(item) > 1 else None,
                ]
            )
    except Exception:
        front = []
    try:
        route_len = len(route)
    except Exception:
        route_len = None
    return {
        "status": "OBSERVED",
        "planner_identity": id(planner),
        "route_identity": id(route),
        "route_len": route_len,
        "route_front": front,
        "is_last": getattr(planner, "is_last", None),
    }


def _model_training_snapshot(model: Any) -> Mapping[str, Any]:
    rows = []
    for name, module in model.named_modules():
        rows.append((str(name), bool(module.training)))
    return {
        "root_training": bool(model.training),
        "module_count": len(rows),
        "training_state_sha256": canonical_digest(rows),
    }


def _model_output_snapshot(model: Any) -> Mapping[str, Any]:
    return {
        name: {
            "object_identity": id(getattr(model, name, None)),
            "digest": _tensor_digest(getattr(model, name, None))
            if name != "language"
            else canonical_digest(copy.deepcopy(getattr(model, name, None))),
        }
        for name in ("route", "speed_wps", "language")
    }


def _torch_flags(torch_module: Any) -> Mapping[str, Any]:
    warn_only = None
    getter = getattr(torch_module, "is_deterministic_algorithms_warn_only_enabled", None)
    if callable(getter):
        warn_only = bool(getter())
    return {
        "deterministic_algorithms_enabled": bool(torch_module.are_deterministic_algorithms_enabled()),
        "deterministic_warn_only": warn_only,
        "cuda_matmul_allow_tf32": bool(torch_module.backends.cuda.matmul.allow_tf32),
        "cudnn_allow_tf32": bool(torch_module.backends.cudnn.allow_tf32),
        "cudnn_deterministic": bool(torch_module.backends.cudnn.deterministic),
        "cudnn_benchmark": bool(torch_module.backends.cudnn.benchmark),
    }


def _restore_torch_flags(torch_module: Any, flags: Mapping[str, Any]) -> None:
    try:
        torch_module.use_deterministic_algorithms(
            bool(flags["deterministic_algorithms_enabled"]),
            warn_only=bool(flags.get("deterministic_warn_only", False)),
        )
    except TypeError:
        torch_module.use_deterministic_algorithms(bool(flags["deterministic_algorithms_enabled"]))
    torch_module.backends.cuda.matmul.allow_tf32 = bool(flags["cuda_matmul_allow_tf32"])
    torch_module.backends.cudnn.allow_tf32 = bool(flags["cudnn_allow_tf32"])
    torch_module.backends.cudnn.deterministic = bool(flags["cudnn_deterministic"])
    torch_module.backends.cudnn.benchmark = bool(flags["cudnn_benchmark"])


def _ownership_snapshot(agent: Any, model: Any, source_digest: str | None) -> Mapping[str, Any]:
    ukf = getattr(agent, "ukf", None)
    ukf_state = {"status": "NOT_OBSERVABLE"}
    if ukf is not None:
        ukf_state = {
            "status": "OBSERVED",
            "x_sha256": hashlib.sha256(getattr(ukf, "x").tobytes()).hexdigest(),
            "P_sha256": hashlib.sha256(getattr(ukf, "P").tobytes()).hexdigest(),
        }
    image_buffer = getattr(agent, "image_buffer", None)
    state_log = getattr(agent, "state_log", None)
    value = {
        "model_training": _model_training_snapshot(model),
        "model_outputs": _model_output_snapshot(model),
        "planner": _route_planner_snapshot(agent),
        "speed_pid": _controller_snapshot(getattr(agent, "speed_controller", None)),
        "turn_pid": _controller_snapshot(getattr(agent, "turn_controller", None)),
        "commands": [_command_value(item) for item in list(getattr(agent, "commands", ()))],
        "last_command": _command_value(getattr(agent, "last_command", None)),
        "last_command_tmp": _command_value(getattr(agent, "last_command_tmp", None)),
        "ukf": ukf_state,
        "observation_cache": {
            "model_input_source_digest": source_digest,
            "driving_input_identity": id(getattr(agent, "DrivingInput", None)),
            "image_buffer_len": len(image_buffer) if image_buffer is not None else None,
            "image_buffer_item_ids": [id(item) for item in list(image_buffer or ())],
            "state_log_len": len(state_log) if state_log is not None else None,
            "target_point_prev": _small_value(getattr(agent, "target_point_prev", None)),
        },
        "agent_step": getattr(agent, "step", None),
    }
    return {**value, "canonical_sha256": canonical_digest(value)}


class _SimLingoLabelBuilder:
    """Candidate-local copy of the already validated SimLingo label construction."""

    def __init__(self, agent: Any) -> None:
        self.cfg = agent.cfg
        self.tokenizer = agent.tokenizer
        self.device = agent.device
        self._tmp_config = None
        self._num_image_token = None

    def __call__(self, prompt: str) -> Any:
        from hydra.utils import to_absolute_path
        from simlingo_training.utils.custom_types import LanguageLabel
        from transformers import AutoConfig

        cache_dir = "pretrained/{}".format(self.cfg.model.vision_model.variant.split("/")[1])
        model_path = Path(to_absolute_path(cache_dir)) / "conversation.py"
        if not model_path.exists():
            raise RuntimeError("LIVE_SHADOW_CONVERSATION_TEMPLATE_MISSING")
        module_name = "_driveclarify_live_conversation_" + hashlib.sha256(
            str(model_path).encode("utf-8")
        ).hexdigest()[:16]
        specification = importlib.util.spec_from_file_location(module_name, str(model_path))
        if specification is None or specification.loader is None:
            raise RuntimeError("LIVE_SHADOW_CONVERSATION_TEMPLATE_IMPORT_FAILED")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)

        if self._tmp_config is None:
            self._tmp_config = AutoConfig.from_pretrained(
                self.cfg.model.vision_model.variant,
                trust_remote_code=True,
            )
            image_size = self._tmp_config.force_image_size or self._tmp_config.vision_config.image_size
            patch_size = self._tmp_config.vision_config.patch_size
            self._num_image_token = int(
                (image_size // patch_size) ** 2 * (self._tmp_config.downsample_ratio ** 2)
            )
        conversation = [
            {"role": "user", "content": "<image>\n" + prompt},
            {"role": "assistant", "content": "Waypoints:"},
        ]
        template = module.get_conv_template("internlm2-chat")
        for part in conversation:
            if part["role"] == "user":
                template.append_message(template.roles[0], part["content"])
            else:
                template.append_message(template.roles[1], None)
        query = template.get_prompt()
        system_prompt = template.system_template.replace(
            "{system_message}", template.system_message
        ) + template.sep
        query = query.replace(system_prompt, "")
        image_tokens = "<img>" + "<IMG_CONTEXT>" * int(self._num_image_token) * 2 + "</img>"
        query = query.replace("<image>", image_tokens, 1)
        tokenized = self.tokenizer(
            [query],
            padding=True,
            return_tensors="pt",
            return_offsets_mapping=True,
            add_special_tokens=False,
        )
        tokenized_ids = tokenized["input_ids"]
        tokenized_valid = tokenized_ids != self.tokenizer.pad_token_id
        return LanguageLabel(
            phrase_ids=tokenized_ids.to(self.device),
            phrase_valid=tokenized_valid.to(self.device),
            phrase_mask=tokenized_valid.to(self.device),
            placeholder_values=[],
            language_string=[query],
            loss_masking=None,
        )


def _run_isolated_postprocess(
    output_dir: Path,
    payload: Mapping[str, Any],
) -> tuple[Mapping[str, Any], float]:
    """Invoke the existing consequence/M2B/M3 APIs outside the torch process."""

    project_root = Path(__file__).resolve().parents[1]
    input_path = output_dir / "LIVE_SHADOW_POSTPROCESS_INPUT.json"
    output_path = output_dir / "LIVE_SHADOW_POSTPROCESS_OUTPUT.json"
    log_path = output_dir / "LIVE_SHADOW_POSTPROCESS_PROCESS.json"
    _atomic_json(input_path, payload)
    python_executable = os.environ.get(
        "DRIVECLARIFY_POSTPROCESS_PYTHON",
        "/home/buaa/anaconda3/bin/python",
    )
    if not Path(python_executable).is_file():
        raise RuntimeError("LIVE_SHADOW_POSTPROCESS_PYTHON_NOT_FOUND")
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(project_root) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    command = [
        python_executable,
        "-m",
        "driveclarify_m3_runtime_shadow.live_shadow_postprocess",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    ]
    started = time.monotonic_ns()
    completed = subprocess.run(
        command,
        cwd=str(project_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=60.0,
        check=False,
    )
    elapsed_ms = (time.monotonic_ns() - started) / 1_000_000.0
    _atomic_json(
        log_path,
        {
            "schema_version": "driveclarify.live_shadow_postprocess_process.v0",
            "command": command,
            "cwd": str(project_root),
            "returncode": completed.returncode,
            "elapsed_ms": round(elapsed_ms, 3),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "torch_free_process_boundary": True,
        },
    )
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()
        detail = message[-1] if message else "NO_STDERR"
        raise RuntimeError("LIVE_SHADOW_POSTPROCESS_FAILED:" + detail[:500])
    if not output_path.is_file():
        raise RuntimeError("LIVE_SHADOW_POSTPROCESS_OUTPUT_MISSING")
    with output_path.open("r", encoding="utf-8") as handle:
        result = json.load(handle)
    if result.get("status") != "PASS_EXISTING_CONSEQUENCE_M2B_TRANSLATION_M3_SHADOW_ISOLATED":
        raise RuntimeError("LIVE_SHADOW_POSTPROCESS_STATUS_INVALID")
    return result, elapsed_ms


def _control_values(control: Any) -> dict[str, Any]:
    return {
        name: getattr(control, name)
        for name in (
            "steer",
            "throttle",
            "brake",
            "hand_brake",
            "reverse",
            "manual_gear_shift",
            "gear",
        )
        if hasattr(control, name)
    }


def _live_carla_snapshot() -> dict[str, Any]:
    """Bounded read-only ego/environment observation from the active native session."""

    result: dict[str, Any] = {
        "native_carla_session_available": False,
        "ego_position": None,
        "ego_speed_mps": None,
        "actual_control": None,
        "environment_digest": None,
    }
    try:
        from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

        hero = CarlaDataProvider.get_hero_actor()
        world = CarlaDataProvider.get_world()
        if hero is None or world is None:
            return result
        location = hero.get_location()
        velocity = hero.get_velocity()
        actual = hero.get_control()
        actor_rows = []
        for actor in list(world.get_actors())[:64]:
            try:
                actor_location = actor.get_location()
                actor_rows.append(
                    [
                        int(actor.id),
                        str(actor.type_id),
                        round(float(actor_location.x), 3),
                        round(float(actor_location.y), 3),
                    ]
                )
            except Exception:
                continue
        result.update(
            {
                "native_carla_session_available": True,
                "ego_position": [
                    float(location.x),
                    float(location.y),
                    float(location.z),
                ],
                "ego_speed_mps": float(
                    (velocity.x * velocity.x + velocity.y * velocity.y + velocity.z * velocity.z)
                    ** 0.5
                ),
                "actual_control": _control_values(actual),
                "environment_digest": canonical_digest(actor_rows),
                "nearby_actor_count_digest_input": len(actor_rows),
            }
        )
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result


class NullLiveShadowRuntimeV0:
    enabled = False

    def __init__(self, reason: str = "DISABLED_BY_ENV") -> None:
        self.reason = reason

    def on_tick(self, *args: Any, **kwargs: Any) -> None:
        return None

    def on_model_output(self, *args: Any, **kwargs: Any) -> None:
        return None

    def select_plan_source(
        self, baseline_route: Any, baseline_speed: Any, *args: Any, **kwargs: Any
    ) -> tuple[Any, Any]:
        return baseline_route, baseline_speed

    def on_pid_invocation(self, *args: Any, **kwargs: Any) -> None:
        return None

    def on_control(self, *args: Any, **kwargs: Any) -> None:
        return None

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None

    def summary(self) -> Mapping[str, Any]:
        return {"enabled": False, "reason": self.reason}


class LiveShadowRuntimeV0:
    """Fail-open shadow runtime with one separately gated pre-PID plan seam."""

    enabled = True

    def __init__(
        self,
        agent: Any,
        output_dir: str | os.PathLike[str],
        *,
        run_id: str,
        trigger_after_baseline_forwards: int = 3,
        visualizer: Any | None = None,
        event_executor: Callable[[Any, Any, Any, Any], Mapping[str, Any]] | None = None,
        physical_wait_executor: PhysicalWaitExecutorV0 | None = None,
        physical_wait_pilot_duration_s: float = DEFAULT_PILOT_DURATION_S,
        ask_replanning_executor: AskDelayedAnswerReplanningV0 | None = None,
        ask_oracle_delay_s: float = DEFAULT_ORACLE_DELAY_S,
        ask_holding_lease_duration_s: float = DEFAULT_HOLDING_LEASE_DURATION_S,
        ask_oracle_answer_text: str = DEFAULT_ORACLE_ANSWER,
        carla_snapshot_reader: Callable[[], Mapping[str, Any]] | None = None,
        limited_act_executor: Any | None = None,
    ) -> None:
        self.agent = agent
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.trigger_after_baseline_forwards = max(1, int(trigger_after_baseline_forwards))
        self.visualizer = visualizer or LiveShadowVisualizerV0(
            self.output_dir / "live_shadow_panel.png",
            open_window=True,
        )
        self._event_executor = event_executor
        ask_enabled = _truthy(os.environ.get("DRIVECLARIFY_ASK_REPLAN_V0"))
        self.physical_wait = physical_wait_executor or PhysicalWaitExecutorV0(
            enabled=(
                _truthy(os.environ.get("DRIVECLARIFY_PHYSICAL_WAIT_V0"))
                or ask_enabled
            )
        )
        self.physical_wait_pilot_duration_s = float(physical_wait_pilot_duration_s)
        if self.physical_wait_pilot_duration_s <= 0:
            raise ValueError("PHYSICAL_WAIT_PILOT_DURATION_MUST_BE_POSITIVE")
        self.ask_replanning = ask_replanning_executor or AskDelayedAnswerReplanningV0(
            enabled=ask_enabled,
            oracle_delay_s=ask_oracle_delay_s,
            holding_lease_duration_s=ask_holding_lease_duration_s,
            oracle_answer_text=ask_oracle_answer_text,
        )
        self._carla_snapshot_reader = carla_snapshot_reader or _live_carla_snapshot
        self.limited_act = limited_act_executor or build_limited_act_commit(
            self.output_dir
        )
        self._frame = None
        self._timestamp = None
        self._tick_data = None
        self._observation_id = None
        self._baseline_forward_count = 0
        self._event_count = 0
        self._backend_invocations = 0
        self._candidate_forwards = 0
        self._record: dict[str, Any] | None = None
        self._error: dict[str, Any] | None = None
        self._disabled_after_error = False
        self._visualization_disabled = False
        self._last_returned_control: dict[str, Any] | None = None
        self._last_returned_control_frame: Any = None
        self._actual_control_comparisons: list[dict[str, Any]] = []
        self._latest_front_metadata: dict[str, Any] = {"status": "UNAVAILABLE"}
        self._latest_carla_snapshot: dict[str, Any] = {
            "native_carla_session_available": False,
            "ego_position": None,
            "ego_speed_mps": None,
            "actual_control": None,
            "environment_digest": None,
        }
        self._physical_wait_binding: Mapping[str, Any] | None = None
        self._ask_answer_rendered = False
        self._ask_replan_running_rendered = False
        self._limited_next_frame_rendered = False
        self._baseline_model_end_times: list[float] = []
        self._baseline_control_ready_times: list[float] = []

    def on_tick(
        self,
        input_data: Any,
        tick_data: Any,
        timestamp: Any,
        frame: Any,
        observation_id: Any,
    ) -> None:
        self._frame = frame
        self._timestamp = timestamp
        self._tick_data = tick_data
        self._observation_id = observation_id
        tick_monotonic = time.monotonic()
        answer_arrived = False
        if self._record is not None and self.ask_replanning.active:
            try:
                answer_arrived = self.ask_replanning.poll_oracle_answer(
                    current_monotonic=tick_monotonic,
                    frame=frame,
                    simulation_time=(float(timestamp) if timestamp is not None else None),
                )
            except Exception:
                self.ask_replanning.fail_replan("BLOCKED_ASK_ANSWER_LIFECYCLE_BINDING")
        front_copy, front_metadata = copy_front_rgb_for_display(
            input_data,
            expected_frame=frame,
        )
        self._latest_front_metadata = front_metadata
        setter = getattr(self.visualizer, "set_front_rgb", None)
        if callable(setter):
            setter(front_copy, front_metadata)
        try:
            self._latest_carla_snapshot = dict(self._carla_snapshot_reader())
        except Exception as exc:
            self._latest_carla_snapshot = {
                "native_carla_session_available": False,
                "ego_position": None,
                "ego_speed_mps": None,
                "actual_control": None,
                "environment_digest": None,
                "error": type(exc).__name__,
            }
        if self._last_returned_control is not None:
            comparison: dict[str, Any] = {
                "returned_control_source_frame": self._last_returned_control_frame,
                "observed_at_frame": frame,
                "returned_control": self._last_returned_control,
                "actual_carla_control": None,
                "equal": None,
                "exact_equal": None,
                "actuator_equal": None,
                "status": "NOT_OBSERVABLE",
            }
            try:
                actual = self._latest_carla_snapshot.get("actual_control")
                comparison["actual_carla_control"] = actual
                comparison["equal"] = actual == self._last_returned_control
                comparison["exact_equal"] = comparison["equal"]
                actuator_fields = (
                    "steer",
                    "throttle",
                    "brake",
                    "hand_brake",
                    "reverse",
                    "manual_gear_shift",
                )
                comparison["actuator_equal"] = actual is not None and all(
                    actual.get(name) == self._last_returned_control.get(name)
                    for name in actuator_fields
                )
                comparison["status"] = "OBSERVED"
                self.physical_wait.observe_actual_carla_control(
                    source_frame=self._last_returned_control_frame,
                    actual_control=actual,
                )
            except Exception as exc:
                comparison["error"] = type(exc).__name__
            self._actual_control_comparisons.append(comparison)
            self._last_returned_control = None
        limited_next_frame = self.limited_act.observe_tick(
            frame=frame,
            snapshot=self._latest_carla_snapshot,
        )
        if self.physical_wait.active:
            observed = [
                item
                for item in self._actual_control_comparisons
                if item.get("status") == "OBSERVED"
            ]
            baseline_healthy = bool(observed) and all(
                item.get("actuator_equal") is True for item in observed
            )
            self.physical_wait.observe_tick(
                frame=frame,
                current_monotonic_time=tick_monotonic,
                ego_position=self._latest_carla_snapshot.get("ego_position"),
                ego_speed_mps=self._latest_carla_snapshot.get("ego_speed_mps"),
                baseline_control_path_healthy=baseline_healthy,
                source_runtime_valid=True,
                m3_state=(
                    self.ask_replanning.m3_state
                    if answer_arrived and self.ask_replanning.enabled
                    else None
                ),
                environment_digest=self._latest_carla_snapshot.get("environment_digest"),
                source_simulation_time=(float(timestamp) if timestamp is not None else None),
            )
        if self.ask_replanning.ready_for_latest_observation:
            try:
                self.ask_replanning.capture_latest_observation(
                    observation_id=str(observation_id),
                    frame=frame,
                    simulation_time=(float(timestamp) if timestamp is not None else None),
                    captured_monotonic=time.monotonic(),
                    rgb_source_identity=front_metadata,
                    environment_digest=self._latest_carla_snapshot.get("environment_digest"),
                )
            except Exception:
                self.ask_replanning.fail_replan(
                    "BLOCKED_POST_ANSWER_REPLAN_NOT_USING_LATEST_OBSERVATION"
                )
        if self._record is not None:
            self._record["baseline"]["actual_control_comparisons"] = list(
                self._actual_control_comparisons
            )
            self._sync_physical_wait_record()
            self._sync_ask_replanning_record()
            self._sync_limited_act_record()
            _atomic_json(self.output_dir / "LIVE_SHADOW_EVENT.json", self._record)
            if answer_arrived and not self._ask_answer_rendered:
                self._render_ask_stage_fail_open()
                self._ask_answer_rendered = True
            if limited_next_frame and not self._limited_next_frame_rendered:
                self._render_ask_stage_fail_open()
                self._limited_next_frame_rendered = True
        self._persist_summary()

    def on_model_output(
        self,
        pred_route: Any,
        pred_speed_wps: Any,
        model_start: float,
        model_end: float,
    ) -> None:
        self._baseline_forward_count += 1
        self._baseline_model_end_times.append(float(model_end))
        if self._record is not None:
            if self.ask_replanning.ready_for_replan:
                self._run_post_answer_replan()
            return None
        if (
            self._disabled_after_error
            or self._baseline_forward_count < self.trigger_after_baseline_forwards
        ):
            return None
        try:
            executor = self._event_executor or self._execute_real_event
            record = dict(executor(pred_route, pred_speed_wps, model_start, model_end))
            self._record = record
            self._event_count = 1
            self._backend_invocations = int(record.get("performance", {}).get("shadow_backend_invocation_count", 0))
            self._candidate_forwards = int(record.get("performance", {}).get("candidate_model_forward_count", 0))
            record["front_camera"] = dict(self._latest_front_metadata)
            if self.ask_replanning.enabled:
                self._start_ask_replanning_pilot(record)
            elif self.physical_wait.enabled:
                self._start_physical_wait_pilot(record)
            self._sync_physical_wait_record()
            self._sync_ask_replanning_record()
            self._sync_limited_act_record()
            try:
                first_render = float(self.visualizer.render(record))
                record.setdefault("performance", {})["visualization_render_ms"] = round(first_render, 3)
                record["visualization"] = {
                    "status": "VISIBLE_LOCAL_WINDOW_UPDATED",
                    "world_route_overlay": "LIVE_WORLD_ROUTE_OVERLAY_NOT_ENABLED",
                    "ego_local_bev": True,
                    **dict(self.visualizer.summary()),
                }
                self.visualizer.render(record)
            except Exception as visualization_exc:
                self._visualization_disabled = True
                record["status"] = (
                    PHYSICAL_WAIT_VIZ_PARTIAL_STATUS
                    if self.physical_wait.enabled
                    else "PARTIAL_PASS_CARLA_NO_CONTROL_SHADOW_VISUALIZATION_BLOCKED"
                )
                record["visualization"] = {
                    "status": "BLOCKED",
                    "error_type": type(visualization_exc).__name__,
                    "error_message": str(visualization_exc),
                    "world_route_overlay": "LIVE_WORLD_ROUTE_OVERLAY_NOT_ENABLED",
                    "ego_local_bev": False,
                }
            _atomic_json(self.output_dir / "LIVE_SHADOW_EVENT.json", record)
            self._persist_summary()
        except Exception as exc:
            self._disabled_after_error = True
            self._error = {
                "schema_version": LIVE_SHADOW_SCHEMA,
                "status": "BLOCKED_REAL_CARLA_SHADOW_CANDIDATE_EXECUTION",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "source_frame_id": self._frame,
                "source_observation_id": self._observation_id,
                "baseline_continues": True,
                "shadow_authority": "NONE",
                "automatic_retry": False,
            }
            _atomic_json(self.output_dir / "LIVE_SHADOW_ERROR.json", self._error)
            self._persist_summary()
        return None

    def _start_ask_replanning_pilot(self, record: dict[str, Any]) -> None:
        now = time.monotonic()
        try:
            binding = self.ask_replanning.start(
                run_id=self.run_id,
                record=record,
                start_monotonic=now,
                frame=self._frame,
                simulation_time=(
                    float(self._timestamp) if self._timestamp is not None else None
                ),
                rgb_source_identity=self._latest_front_metadata,
            )
            self._physical_wait_binding = binding.to_dict()
            observed = [
                item
                for item in self._actual_control_comparisons
                if item.get("status") == "OBSERVED"
            ]
            baseline_healthy = bool(observed) and all(
                item.get("actuator_equal") is True for item in observed
            )
            entered = self.physical_wait.enter(
                binding,
                current_monotonic_time=now,
                frame=self._frame,
                ego_position=self._latest_carla_snapshot.get("ego_position"),
                ego_speed_mps=self._latest_carla_snapshot.get("ego_speed_mps"),
                baseline_control_path_healthy=baseline_healthy,
                no_control_ownership_conflict=(
                    record.get("isolation", {}).get("candidate_control_writes", 0) == 0
                    and record.get("isolation", {}).get("m3_control_writes", 0) == 0
                    and record.get("isolation", {}).get("baseline_control_rewrite_count", 0) == 0
                ),
                native_carla_session_available=bool(
                    self._latest_carla_snapshot.get("native_carla_session_available")
                ),
                source_identity_recordable=True,
                environment_digest=self._latest_carla_snapshot.get("environment_digest"),
            )
            if not entered:
                self.ask_replanning.fail_replan(
                    "BLOCKED_PHYSICAL_WAIT_M3_HOLDING_LEASE_BINDING"
                )
        except Exception as exc:
            self.ask_replanning.fail_replan("BLOCKED_ASK_ANSWER_LIFECYCLE_BINDING")
            record["ask_replanning_setup_error"] = {
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }

    def _render_ask_stage_fail_open(self) -> None:
        if self._record is None or self._visualization_disabled:
            return
        try:
            render_ms = float(self.visualizer.render(self._record))
            self._record.setdefault("performance", {})[
                "visualization_render_ms"
            ] = round(render_ms, 3)
            self._record["visualization"] = {
                "status": "VISIBLE_LOCAL_WINDOW_UPDATED",
                "world_route_overlay": "LIVE_WORLD_ROUTE_OVERLAY_NOT_ENABLED",
                "ego_local_bev": True,
                **dict(self.visualizer.summary()),
            }
            _atomic_json(self.output_dir / "LIVE_SHADOW_EVENT.json", self._record)
        except Exception as exc:
            self._visualization_disabled = True
            self._record["status"] = ASK_REPLAN_VIZ_PARTIAL_STATUS
            self._record["visualization"] = {
                "status": "BLOCKED_FAIL_OPEN_BASELINE_CONTINUES",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }

    def _run_post_answer_replan(self) -> None:
        if self._record is None:
            return
        started = time.monotonic()
        if not self.ask_replanning.begin_replan(
            current_monotonic=started,
            simulation_time=(
                float(self._timestamp) if self._timestamp is not None else None
            ),
        ):
            self._sync_ask_replanning_record()
            return
        self._sync_ask_replanning_record()
        if not self._ask_replan_running_rendered:
            self._render_ask_stage_fail_open()
            self._ask_replan_running_rendered = True
        try:
            result = self._execute_fresh_resolved_replan()
            self._backend_invocations += int(
                result.get("post_answer_model_forward_count", 0)
            )
            self._candidate_forwards += int(
                result.get("post_answer_model_forward_count", 0)
            )
            completed = self.ask_replanning.complete_replan(
                result,
                finished_monotonic=time.monotonic(),
                simulation_time=(
                    float(self._timestamp) if self._timestamp is not None else None
                ),
            )
            if not completed:
                raise RuntimeError(BLOCKED_FRESH_REPLAN)
            old_record = (
                self._record.get("candidate_runtime", {}).get("records", [{}])[0]
                if self._record is not None
                else {}
            )
            old_source = (
                self._record.get("source_identity", {})
                if self._record is not None
                else {}
            )
            old_candidate = {
                "candidate_id": old_record.get("candidate_id"),
                "candidate_set_id": old_source.get("candidate_set_id"),
                "resolved_interpretation_id": old_record.get("interpretation_id"),
                "source_observation_id": old_record.get("source_observation_id"),
                "source_frame_id": str(old_record.get("source_frame")),
                "route_digest": old_record.get("route_sha256"),
                "speed_digest": old_record.get("speed_sha256"),
            }
            m3_state = self.ask_replanning.m3_state
            self.limited_act.arm_from_replan(
                result,
                old_candidate=old_candidate,
                natural_m2b_action=(
                    self._record.get("m2b", {}).get("producer_action")
                    if self._record is not None
                    else None
                ),
                current_frame=self._frame,
                current_observation_id=self._observation_id,
                current_snapshot=self._latest_carla_snapshot,
                active_query=bool(m3_state.query_active) if m3_state is not None else True,
                active_wait=bool(self.physical_wait.active),
                safety_active=(
                    bool(m3_state.safety_guard_active) if m3_state is not None else True
                ),
                current_monotonic=time.monotonic(),
            )
        except Exception as exc:
            self.ask_replanning.fail_replan(BLOCKED_FRESH_REPLAN)
            self._record["post_answer_replan_error"] = {
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "baseline_continues": True,
                "candidate_control_writes": 0,
            }
        self._sync_ask_replanning_record()
        self._sync_limited_act_record()
        _atomic_json(self.output_dir / "LIVE_SHADOW_EVENT.json", self._record)

    def select_plan_source(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        current_monotonic: float,
    ) -> tuple[Any, Any]:
        m3_state = self.ask_replanning.m3_state
        return self.limited_act.select_plan_source(
            baseline_route,
            baseline_speed,
            frame=self._frame,
            observation_id=self._observation_id,
            current_monotonic=float(current_monotonic),
            active_query=bool(m3_state.query_active) if m3_state is not None else False,
            active_wait=bool(self.physical_wait.active),
            safety_active=(
                bool(m3_state.safety_guard_active) if m3_state is not None else False
            ),
        )

    def on_pid_invocation(self, current_monotonic: float) -> None:
        self.limited_act.on_pid_invocation(
            frame=self._frame,
            current_monotonic=float(current_monotonic),
        )

    def _execute_fresh_resolved_replan(self) -> Mapping[str, Any]:
        """Run two isolated repeats for one resolved interpretation on O1."""

        import torch
        from simlingo_training.utils.custom_types import DrivingInput

        latest = self.ask_replanning.latest_observation
        resolved = self.ask_replanning.resolved_instruction
        if latest is None or resolved is None:
            raise RuntimeError("POST_ANSWER_RESOLVED_OBSERVATION_REQUIRED")
        if (
            int(latest["frame_id"]) != int(self._frame)
            or str(latest["observation_id"]) != str(self._observation_id)
        ):
            raise RuntimeError("POST_ANSWER_LATEST_OBSERVATION_NO_LONGER_CURRENT")

        base_input = DrivingInput(**self.agent.DrivingInput)
        speed_scalar = 0.0
        try:
            speed_scalar = float(self._tick_data["speed"][0].detach().cpu().item())
        except Exception:
            pass
        resolved_interpretation_id = "A"
        for candidate in self.ask_replanning.summary().get(
            "language_binding", {}
        ).get("candidate_interpretations", []):
            if candidate.get("candidate_id") == resolved.resolved_candidate_id:
                resolved_interpretation_id = dict(
                    candidate.get("grounding_evidence", {}).get(
                        "observable_attributes", []
                    )
                ).get("interpretation_id", "A")
                break
        prompt = (
            "Current speed: {:.1f} m/s. Command: {} What should the ego do next?"
        ).format(speed_scalar, resolved.explicit_instruction_text)
        source = SimLingoCandidateSource.from_driving_input(
            base_input,
            prompt_a=prompt,
            prompt_b=prompt,
        )
        input_isolation = SimLingoTensorInputIsolation(torch)
        source_digest = input_isolation.source_digest(source)
        model = getattr(self.agent.model, "model", self.agent.model)
        training_refs = tuple((module, bool(module.training)) for module in model.modules())
        output_refs = {
            name: getattr(model, name, None)
            for name in ("route", "speed_wps", "language")
        }
        flags_before = _torch_flags(torch)
        ownership_before = _ownership_snapshot(self.agent, model, source_digest)
        rng_coordinator = None
        rng_before = None
        frame_before = self._frame
        baseline_forward_before = self._baseline_forward_count
        world_before = dict(self._latest_carla_snapshot)
        gpu_before = {
            "allocated_bytes": int(torch.cuda.memory_allocated()),
            "reserved_bytes": int(torch.cuda.memory_reserved()),
            "max_allocated_bytes": int(torch.cuda.max_memory_allocated()),
        }
        paths = {
            "candidates": self.output_dir / "POST_ANSWER_REPLAN_RECORDS.json",
            "runtime_audit": self.output_dir / "POST_ANSWER_REPLAN_RUNTIME_AUDIT.json",
            "rng": self.output_dir / "POST_ANSWER_REPLAN_RNG_EVIDENCE.json",
            "isolation": self.output_dir / "POST_ANSWER_REPLAN_INPUT_ISOLATION.json",
            "failure": self.output_dir / "POST_ANSWER_REPLAN_FAILURE.json",
        }
        backend = SimLingoSensitivityPilotBackend(
            agent=self.agent,
            driving_input_factory=DrivingInput,
            label_builder=_SimLingoLabelBuilder(self.agent),
            generation_config=explicit_sensitivity_pilot_config(),
            candidates_path=paths["candidates"],
            runtime_audit_path=paths["runtime_audit"],
            rng_evidence_path=paths["rng"],
            isolation_evidence_path=paths["isolation"],
            failure_evidence_path=paths["failure"],
            run_id=self.run_id + ":post-answer-replan",
            expected_order=("A1", "A2"),
        )
        records = []
        total_started_ns = time.monotonic_ns()
        try:
            rng_coordinator = RNGCoordinator(backend.rng_backends())
            rng_before = rng_coordinator.capture_base()
            backend.run_read_only_runtime_audit()
            linkage = {
                "observation_id": str(latest["observation_id"]),
                "observation_digest": source_digest,
                "source_frame": int(latest["frame_id"]),
                "freshness_token": canonical_digest(
                    {
                        "observation_id": latest["observation_id"],
                        "source_digest": source_digest,
                        "source_frame": int(latest["frame_id"]),
                        "resolved_candidate_id": resolved.resolved_candidate_id,
                    }
                ),
                "state_digest": ownership_before["canonical_sha256"],
            }
            for candidate_id in ("A1", "A2"):
                records.append(
                    backend.run_candidate(
                        candidate_id=candidate_id,
                        interpretation_id="A",
                        interpretation_text=prompt,
                        source=source,
                        input_isolation=input_isolation,
                        linkage=linkage,
                        candidate_metadata={
                            "post_answer_replan": True,
                            "resolved_language_candidate_id": resolved.resolved_candidate_id,
                            "resolved_interpretation_id": resolved_interpretation_id,
                            "control_authority": False,
                        },
                    )
                )
            backend.finalize_evidence()
        finally:
            for module, training in training_refs:
                module.training = training
            for name, value in output_refs.items():
                setattr(model, name, value)
            _restore_torch_flags(torch, flags_before)
            if rng_coordinator is not None and rng_before is not None:
                _, restored, reasons = rng_coordinator.restore_and_verify(rng_before)
                if not restored:
                    raise RuntimeError(
                        "POST_ANSWER_REPLAN_RNG_RESTORE_FAILED:" + ",".join(reasons)
                    )
        total_finished_ns = time.monotonic_ns()
        if [item["candidate_id"] for item in records] != ["A1", "A2"]:
            raise RuntimeError("POST_ANSWER_REPLAN_REPEAT_SCHEDULE_CHANGED")
        if any(
            item["source_observation_id"] != latest["observation_id"]
            or int(item["source_frame"]) != int(latest["frame_id"])
            for item in records
        ):
            raise RuntimeError("POST_ANSWER_REPLAN_SOURCE_IDENTITY_MISMATCH")
        ownership_after = _ownership_snapshot(self.agent, model, source_digest)
        if ownership_before["canonical_sha256"] != ownership_after["canonical_sha256"]:
            raise RuntimeError("POST_ANSWER_REPLAN_BASELINE_OWNERSHIP_MUTATED")
        if flags_before != _torch_flags(torch):
            raise RuntimeError("POST_ANSWER_REPLAN_TORCH_FLAGS_NOT_RESTORED")

        first = records[0]
        repeat_equal = (
            records[0]["route_sha256"] == records[1]["route_sha256"]
            and records[0]["speed_sha256"] == records[1]["speed_sha256"]
        )
        # SimLingo's ordinary path explicitly casts both model outputs to FP32
        # before its existing PID.  Bind authority to that exact execution-plan
        # representation while preserving the BF16 model-output digests below.
        execution_route = torch.as_tensor(first["route"], dtype=torch.float32)
        execution_speed = torch.as_tensor(first["speed"], dtype=torch.float32)
        execution_route_digest = _tensor_digest(execution_route)
        execution_speed_digest = _tensor_digest(execution_speed)
        if execution_route_digest is None or execution_speed_digest is None:
            raise RuntimeError("POST_ANSWER_EXECUTION_PLAN_DIGEST_UNAVAILABLE")
        plan_id = "POST-ANSWER-PLAN-" + canonical_digest(
            {
                "source_observation_id": latest["observation_id"],
                "source_frame_id": latest["frame_id"],
                "resolved_candidate_id": resolved.resolved_candidate_id,
                "model_route_digest": first["route_sha256"],
                "model_speed_digest": first["speed_sha256"],
                "execution_route_digest": execution_route_digest,
                "execution_speed_digest": execution_speed_digest,
                "execution_dtype": "torch.float32",
            }
        )
        gpu_after = {
            "allocated_bytes": int(torch.cuda.memory_allocated()),
            "reserved_bytes": int(torch.cuda.memory_reserved()),
            "max_allocated_bytes": int(torch.cuda.max_memory_allocated()),
        }
        world_after = dict(self._carla_snapshot_reader())
        initial_model_identity = (
            self._record.get("candidate_runtime", {})
            .get("records", [{}])[0]
            .get("model_identity")
            if self._record is not None
            else None
        )
        return {
            "schema_version": "driveclarify.post_answer_real_simlingo_replan.v0",
            "post_answer_replan_mode": "SINGLE_RESOLVED_INTERPRETATION_REPEAT_V0",
            "source_observation_id": str(latest["observation_id"]),
            "source_frame_id": int(latest["frame_id"]),
            "source_simulation_time": latest.get("simulation_time"),
            "source_digest": source_digest,
            "resolved_language_candidate_id": resolved.resolved_candidate_id,
            "resolved_interpretation_id": resolved_interpretation_id,
            "resolved_instruction_text": resolved.explicit_instruction_text,
            "fresh_model_computation": True,
            "old_cache_reused": False,
            "post_answer_model_forward_count": len(records),
            "actual_schedule": [item["candidate_id"] for item in records],
            "repeat_outputs_equal": repeat_equal,
            "plan_id": plan_id,
            "route_digest": execution_route_digest,
            "speed_digest": execution_speed_digest,
            "model_route_digest": first["route_sha256"],
            "model_speed_digest": first["speed_sha256"],
            "model_output_dtype": {
                "route": first.get("route_dtype"),
                "speed": first.get("speed_dtype"),
            },
            "execution_plan_dtype": "torch.float32",
            "execution_projection": "VALUE_PRESERVING_BF16_TO_EXISTING_PID_FP32_BOUNDARY",
            "route": first["route"],
            "speed": first["speed"],
            "generated_language": first["generated_language"],
            "model_records": records,
            "model_compute_ms": round(
                sum(float(item["latency_seconds"]) for item in records) * 1000.0,
                3,
            ),
            "replan_total_ms": round(
                (total_finished_ns - total_started_ns) / 1_000_000.0,
                3,
            ),
            "same_model_instance_as_pre_ask": all(
                item.get("model_identity") == initial_model_identity for item in records
            ),
            "model_instance_identity": first.get("model_identity"),
            "baseline_owned_state_unchanged": True,
            "baseline_forward_count_before_compute": baseline_forward_before,
            "baseline_forward_count_after_compute": self._baseline_forward_count,
            "baseline_forward_during_replan_compute": (
                self._baseline_forward_count - baseline_forward_before
            ),
            "frame_before_compute": frame_before,
            "frame_after_compute": self._frame,
            "world_before_compute": world_before,
            "world_after_compute": world_after,
            "replan_compute_blocks_simulation_progress": (
                frame_before == self._frame
                and self._baseline_forward_count == baseline_forward_before
            ),
            "gpu_before": gpu_before,
            "gpu_after": gpu_after,
            "oom": False,
            "candidate_control_writes": 0,
            "m3_control_writes": 0,
            "driveclarify_low_level_control_writes": 0,
        }

    def _start_physical_wait_pilot(self, record: dict[str, Any]) -> None:
        source = record.get("source_identity", {})
        now = time.monotonic()
        try:
            binding = build_bounded_wait_pilot_binding(
                run_id=self.run_id,
                source_observation_id=str(source["source_observation_id"]),
                source_frame_id=str(source["source_frame_id"]),
                candidate_set_id=str(source["candidate_set_id"]),
                start_monotonic_time=now,
                duration_s=self.physical_wait_pilot_duration_s,
                source_simulation_time=(
                    float(self._timestamp) if self._timestamp is not None else None
                ),
            )
            self._physical_wait_binding = binding.to_dict()
            observed = [
                item
                for item in self._actual_control_comparisons
                if item.get("status") == "OBSERVED"
            ]
            baseline_healthy = bool(observed) and all(
                item.get("actuator_equal") is True for item in observed
            )
            entered = self.physical_wait.enter(
                binding,
                current_monotonic_time=now,
                frame=self._frame,
                ego_position=self._latest_carla_snapshot.get("ego_position"),
                ego_speed_mps=self._latest_carla_snapshot.get("ego_speed_mps"),
                baseline_control_path_healthy=baseline_healthy,
                no_control_ownership_conflict=(
                    record.get("isolation", {}).get("candidate_control_writes", 0) == 0
                    and record.get("isolation", {}).get("m3_control_writes", 0) == 0
                    and record.get("isolation", {}).get("baseline_control_rewrite_count", 0) == 0
                ),
                native_carla_session_available=bool(
                    self._latest_carla_snapshot.get("native_carla_session_available")
                ),
                source_identity_recordable=all(
                    source.get(name) is not None
                    for name in (
                        "source_observation_id",
                        "source_frame_id",
                        "candidate_set_id",
                    )
                ),
                environment_digest=self._latest_carla_snapshot.get("environment_digest"),
            )
            if not entered:
                record["status"] = PHYSICAL_WAIT_BLOCKED_STATUS
        except Exception as exc:
            record["status"] = PHYSICAL_WAIT_BLOCKED_STATUS
            record["physical_wait_setup_error"] = {
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }

    def _sync_physical_wait_record(self) -> None:
        if self._record is None:
            return
        wait = self.physical_wait.summary(current_monotonic_time=time.monotonic())
        self._record["physical_wait_v0"] = wait
        self._record["physical_wait_pilot_binding"] = self._physical_wait_binding
        self._record["wait_trigger_provenance"] = {
            "source": "BOUNDED_PHYSICAL_HOLDING_PILOT",
            "m2b_naturally_selected_wait": False,
            "natural_m2b_action": self._record.get("m2b", {}).get("producer_action"),
            "claim": "PHYSICAL_WAIT_EXECUTION_WORKS_NOT_M2B_SELECTED_WAIT",
        }
        self._record["front_camera"] = dict(self._latest_front_metadata)
        self._record.setdefault("performance", {})["physical_wait_executor_tick_overhead_ms"] = wait.get(
            "wait_executor_tick_overhead_ms"
        )
        self._record.setdefault("safety", {})["physical_wait"] = self.physical_wait.enabled
        self._record["safety"]["physical_wait_mode"] = "HOLD_CURRENT_VALID_PLAN"
        self._record["safety"]["physical_safety_guarantee"] = False
        if self.ask_replanning.enabled:
            return
        if wait.get("status") == "ACTIVE":
            self._record["status"] = (
                PHYSICAL_WAIT_VIZ_PARTIAL_STATUS
                if self._visualization_disabled
                else PHYSICAL_WAIT_ACTIVE_STATUS
            )
            self._record.setdefault("baseline", {})["control_source"] = (
                "BASELINE_SIMLINGO_CURRENT_VALID_PLAN"
            )
        elif wait.get("status") == "EXITED":
            complete = (
                wait.get("exit_reason") == "LEASE_EXPIRED"
                and (wait.get("carla_ticks_during_wait") or 0) >= 2
                and (wait.get("frames_observed_during_wait") or 0) >= 2
                and (wait.get("distance_travelled_m") or 0.0) > 0.0
                and wait.get("candidate_commit_count_during_wait") == 0
                and wait.get("all_observed_actuators_equal") is True
                and wait.get("old_candidate_status_at_exit") == OLD_CANDIDATES_EXIT_STATUS
                and self._latest_front_metadata.get("status") == "AVAILABLE"
                and self._latest_front_metadata.get("model_input_unchanged") is True
                and self._latest_front_metadata.get("frame_identity_matches_baseline") is True
            )
            if complete and not self._visualization_disabled:
                self._record["status"] = PHYSICAL_WAIT_PASS_STATUS
            elif self._visualization_disabled:
                self._record["status"] = PHYSICAL_WAIT_VIZ_PARTIAL_STATUS
            else:
                self._record["status"] = "BLOCKED_PHYSICAL_WAIT_V0_INCOMPLETE_EVIDENCE"

    def _sync_ask_replanning_record(self) -> None:
        if self._record is None or not self.ask_replanning.enabled:
            return
        ask = self.ask_replanning.summary(current_monotonic=time.monotonic())
        wait = self.physical_wait.summary(current_monotonic_time=time.monotonic())
        self._record["ask_replanning_v0"] = ask
        _atomic_json(self.output_dir / "ASK_REPLAN_EVENT.json", ask)

        answer_frame = ask.get("oracle", {}).get("answer_received_frame")
        post_answer_controls = [
            item
            for item in self._actual_control_comparisons
            if item.get("status") == "OBSERVED"
            and answer_frame is not None
            and item.get("returned_control_source_frame") is not None
            and int(item["returned_control_source_frame"]) >= int(answer_frame)
        ]
        ask.setdefault("control_isolation", {}).update(
            {
                "post_answer_actual_control_comparisons": post_answer_controls,
                "post_answer_control_equality_observed_count": len(post_answer_controls),
                "post_answer_all_observed_actuators_equal": bool(post_answer_controls)
                and all(item.get("actuator_equal") is True for item in post_answer_controls),
                "baseline_forward_count": self._baseline_forward_count,
                "baseline_control_ready_count": len(self._baseline_control_ready_times),
                "baseline_model_end_intervals_s": [
                    right - left
                    for left, right in zip(
                        self._baseline_model_end_times,
                        self._baseline_model_end_times[1:],
                    )
                ],
            }
        )
        self._record["ask_trigger_provenance"] = {
            "ask_entry_source": ask.get("ask_entry_source"),
            "natural_m2b_action": ask.get("natural_m2b_action"),
            "m2b_naturally_selected_ask": ask.get("m2b_naturally_selected_ask"),
            "claim": "BOUNDED_PILOT_ASK_NOT_NATURAL_M2B_ASK",
        }
        self._record.setdefault("safety", {})["live_ask"] = True
        self._record["safety"]["live_act"] = False
        self._record["safety"]["candidate_control_authorized"] = False
        self._record["safety"]["text_to_actuator_shortcut_count"] = 0
        self._record.setdefault("performance", {})["post_answer_model_forward_count"] = ask.get(
            "replan", {}
        ).get("post_answer_model_forward_count", 0)
        self._record["performance"]["answer_to_replan_start_ms"] = (
            None
            if ask.get("oracle", {}).get("answer_received_monotonic") is None
            or ask.get("replan", {}).get("replan_started_monotonic") is None
            else round(
                (
                    ask["replan"]["replan_started_monotonic"]
                    - ask["oracle"]["answer_received_monotonic"]
                )
                * 1000.0,
                3,
            )
        )
        self._record["performance"]["replan_model_compute_ms"] = (
            ask.get("replan", {}).get("details") or {}
        ).get("model_compute_ms")
        self._record["performance"]["replan_total_ms"] = ask.get("replan", {}).get(
            "replan_total_ms"
        )
        self._record["performance"]["baseline_model_end_monotonic_times"] = list(
            self._baseline_model_end_times
        )
        self._record["performance"]["baseline_control_ready_monotonic_times"] = list(
            self._baseline_control_ready_times
        )
        self._record["performance"]["replan_compute_blocks_simulation_progress"] = (
            (ask.get("replan", {}).get("details") or {}).get(
                "replan_compute_blocks_simulation_progress"
            )
        )

        final = ask.get("final_status")
        stage = ask.get("stage")
        if final == ASK_REPLAN_PASS_STATUS:
            latest = ask.get("latest_observation", {})
            invalidation = ask.get("old_candidate_invalidation", {})
            lifecycle = ask.get("m3_lifecycle", {})
            replan = ask.get("replan", {})
            complete = (
                wait.get("status") == "EXITED"
                and wait.get("exit_reason") == "M3_LEFT_WAIT"
                and (wait.get("carla_ticks_during_wait") or 0) >= 1
                and (wait.get("frames_observed_during_wait") or 0) >= 2
                and wait.get("all_observed_actuators_equal") is True
                and invalidation.get("invalidated_before_replan") is True
                and invalidation.get("post_answer_old_candidate_commit_count") == 0
                and invalidation.get("post_answer_old_candidate_control_writes") == 0
                and latest.get("freshness_verdict") == "LATEST_POST_ANSWER_OBSERVATION"
                and latest.get("rgb_source_identity_changed") is True
                and replan.get("fresh_model_computation") is True
                and (replan.get("post_answer_model_forward_count") or 0) > 0
                and lifecycle.get("exact_state") == "RESUME_READY"
                and lifecycle.get("authority") == "BASELINE_CONTROL"
                and ask.get("control_isolation", {}).get(
                    "post_answer_all_observed_actuators_equal"
                ) is True
                and not self._visualization_disabled
                and self._latest_front_metadata.get("status") == "AVAILABLE"
                and self._latest_front_metadata.get("model_input_unchanged") is True
                and self._latest_front_metadata.get("frame_identity_matches_baseline") is True
                and self._record.get("isolation", {}).get("candidate_control_writes", 0) == 0
                and self._record.get("isolation", {}).get("m3_control_writes", 0) == 0
            )
            self._record["status"] = (
                ASK_REPLAN_PASS_STATUS
                if complete
                else ASK_REPLAN_VIZ_PARTIAL_STATUS
                if self._visualization_disabled
                else "BLOCKED_ASK_REPLAN_V0_INCOMPLETE_LIVE_EVIDENCE"
            )
        elif final is not None:
            self._record["status"] = (
                ASK_REPLAN_VIZ_PARTIAL_STATUS
                if self._visualization_disabled
                else str(final)
            )
        elif stage == "ASK_SENT_WAIT_ACTIVE":
            self._record["status"] = ASK_REPLAN_ACTIVE_STATUS
        elif stage in {
            "ANSWER_RECEIVED_OLD_CANDIDATES_INVALIDATED",
            "LATEST_OBSERVATION_CAPTURED",
        }:
            self._record["status"] = ASK_REPLAN_ANSWER_STATUS
        elif stage == "REPLANNING_FROM_LATEST_OBSERVATION":
            self._record["status"] = ASK_REPLAN_RUNNING_STATUS
        _atomic_json(self.output_dir / "ASK_REPLAN_EVENT.json", ask)

    def _sync_limited_act_record(self) -> None:
        if self._record is None:
            return
        limited = dict(self.limited_act.summary())
        self._record["limited_act_commit_v0"] = limited
        if not limited.get("enabled"):
            return
        self._record.setdefault("safety", {})["live_act"] = (
            limited.get("candidate_control_writes", 0) == 1
        )
        self._record["safety"]["candidate_control_authorized"] = (
            limited.get("authority_receipts_issued", 0) == 1
        )
        self._record.setdefault("isolation", {})["candidate_control_writes"] = (
            limited.get("candidate_control_writes", 0)
        )
        self._record["isolation"]["extra_baseline_pid"] = 0
        self._record["isolation"]["planner_advancement"] = 0
        limited_status = limited.get("status")
        if limited_status == LIMITED_ACT_PASS_STATUS or str(limited_status).startswith(
            "BLOCKED_"
        ):
            self._record["status"] = limited_status
        else:
            self._record["status"] = "LIMITED_ACT_COMMIT_V0_ACTIVE"

    def _execute_real_event(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        model_start: float,
        model_end: float,
    ) -> Mapping[str, Any]:
        import torch
        from simlingo_training.utils.custom_types import DrivingInput

        event_started = time.monotonic_ns()
        base_input = DrivingInput(**self.agent.DrivingInput)
        speed_scalar = 0.0
        try:
            speed_scalar = float(self._tick_data["speed"][0].detach().cpu().item())
        except Exception:
            pass
        interpretation_a = "Current speed: {:.1f} m/s. Command: {} What should the ego do next?".format(
            speed_scalar, INTERPRETATION_A
        )
        interpretation_b = "Current speed: {:.1f} m/s. Command: {} What should the ego do next?".format(
            speed_scalar, INTERPRETATION_B
        )
        source = SimLingoCandidateSource.from_driving_input(
            base_input,
            prompt_a=interpretation_a,
            prompt_b=interpretation_b,
        )
        input_isolation = SimLingoTensorInputIsolation(torch)
        source_digest = input_isolation.source_digest(source)
        observation_id = str(self._observation_id or f"{self.run_id}:{self._frame}")
        snapshot_payload = {
            "observation_id": observation_id,
            "frame_id": int(self._frame),
            "simulation_time": float(self._timestamp),
            "speed": [speed_scalar, 0.0],
            "target_point": {"status": "BOUND_IN_MODEL_INPUT"},
            "route_context": {"status": "BASELINE_ROUTE_CONTEXT_READ_ONLY"},
            "instruction": RAW_INSTRUCTION,
            "model_input_snapshot": {
                "status": "SAME_LIVE_SIMLINGO_DRIVING_INPUT_JSON_BOUNDARY",
                "source_digest": source_digest,
            },
            "source_digest": source_digest,
        }
        model = getattr(self.agent.model, "model", self.agent.model)
        training_refs = tuple((module, bool(module.training)) for module in model.modules())
        output_refs = {
            name: getattr(model, name, None) for name in ("route", "speed_wps", "language")
        }
        flags_before = _torch_flags(torch)
        rng_coordinator = None
        rng_before = None
        baseline_route_digest_before = _tensor_digest(baseline_route)
        baseline_speed_digest_before = _tensor_digest(baseline_speed)
        ownership_before = _ownership_snapshot(self.agent, model, source_digest)

        paths = {
            "candidates": self.output_dir / "CANDIDATE_BACKEND_RECORDS.json",
            "runtime_audit": self.output_dir / "CANDIDATE_RUNTIME_AUDIT.json",
            "rng": self.output_dir / "CANDIDATE_RNG_EVIDENCE.json",
            "isolation": self.output_dir / "CANDIDATE_INPUT_ISOLATION.json",
            "failure": self.output_dir / "CANDIDATE_FAILURE.json",
        }
        backend = SimLingoSensitivityPilotBackend(
            agent=self.agent,
            driving_input_factory=DrivingInput,
            label_builder=_SimLingoLabelBuilder(self.agent),
            generation_config=explicit_sensitivity_pilot_config(),
            candidates_path=paths["candidates"],
            runtime_audit_path=paths["runtime_audit"],
            rng_evidence_path=paths["rng"],
            isolation_evidence_path=paths["isolation"],
            failure_evidence_path=paths["failure"],
            run_id=self.run_id,
        )
        records = []
        candidate_stage_started = time.monotonic_ns()
        try:
            rng_coordinator = RNGCoordinator(backend.rng_backends())
            rng_before = rng_coordinator.capture_base()
            backend.run_read_only_runtime_audit()
            linkage = {
                "observation_id": observation_id,
                "observation_digest": source_digest,
                "source_frame": int(self._frame),
                "freshness_token": canonical_digest(
                    {
                        "observation_id": observation_id,
                        "source_digest": source_digest,
                        "source_frame": int(self._frame),
                    }
                ),
                "state_digest": ownership_before["canonical_sha256"],
            }
            for candidate_id, group in (("A1", "A"), ("B1", "B"), ("A2", "A"), ("B2", "B")):
                records.append(
                    backend.run_candidate(
                        candidate_id=candidate_id,
                        interpretation_id=group,
                        interpretation_text=(interpretation_a if group == "A" else interpretation_b),
                        source=source,
                        input_isolation=input_isolation,
                        linkage=linkage,
                        candidate_metadata={"live_shadow": True, "control_authority": False},
                    )
                )
            backend.finalize_evidence()
        finally:
            for module, training in training_refs:
                module.training = training
            for name, value in output_refs.items():
                setattr(model, name, value)
            _restore_torch_flags(torch, flags_before)
            if rng_coordinator is not None and rng_before is not None:
                _, restored, reasons = rng_coordinator.restore_and_verify(rng_before)
                if not restored:
                    raise RuntimeError("REAL_SIMLINGO_SHADOW_RNG_RESTORE_FAILED:" + ",".join(reasons))
        candidate_stage_end = time.monotonic_ns()

        if [record["candidate_id"] for record in records] != ["A1", "B1", "A2", "B2"]:
            raise RuntimeError("LIVE_SHADOW_CANDIDATE_SCHEDULE_CHANGED")
        identities = {
            (
                record["source_observation_id"],
                int(record["source_frame"]),
                record["source_observation_digest"],
            )
            for record in records
        }
        if identities != {(observation_id, int(self._frame), source_digest)}:
            raise RuntimeError(SOURCE_MISMATCH)

        first_by_group = {record["interpretation_id"]: record for record in records if record["candidate_id"] in {"A1", "B1"}}
        candidates: list[dict[str, Any]] = []
        for semantic_id, group in (("candidate_A", "A"), ("candidate_B", "B")):
            record = first_by_group[group]
            route = _tensor_points(record["route"])
            speed = _tensor_points(record["speed"])
            candidates.append(
                {
                    "candidate_id": semantic_id,
                    "interpretation_id": group,
                    "model_forward_sequence_id": record["candidate_id"],
                    "source_observation_id": record["source_observation_id"],
                    "source_frame_id": int(record["source_frame"]),
                    "route": [list(point) for point in route],
                    "speed": [list(point) for point in speed],
                    "language": [str(item) for item in record["generated_language"]],
                    "candidate_input_digest": record["candidate_input_digest"],
                    "candidate_output_digest": canonical_digest(
                        {
                            "route_sha256": record["route_sha256"],
                            "speed_sha256": record["speed_sha256"],
                            "language": record["generated_language"],
                        }
                    ),
                    "latency": float(record["latency_seconds"]),
                }
            )
        if any(
            candidate["source_observation_id"] != observation_id
            or candidate["source_frame_id"] != int(self._frame)
            for candidate in candidates
        ):
            raise RuntimeError(SOURCE_MISMATCH)

        postprocess, postprocess_elapsed_ms = _run_isolated_postprocess(
            self.output_dir,
            {
                "schema_version": "driveclarify.live_shadow_postprocess_input.v0",
                "snapshot": snapshot_payload,
                "candidates": candidates,
            },
        )
        matrix_dict = postprocess["counterfactual_matrix"]
        annotations = {
            item["candidate_id"]: item
            for item in postprocess["candidate_annotations"]
        }

        source_after_digest = input_isolation.source_digest(source)
        ownership_after = _ownership_snapshot(self.agent, model, source_after_digest)
        baseline_output_integrity = {
            "route_digest_before_shadow": baseline_route_digest_before,
            "route_digest_after_shadow": _tensor_digest(baseline_route),
            "speed_digest_before_shadow": baseline_speed_digest_before,
            "speed_digest_after_shadow": _tensor_digest(baseline_speed),
        }
        baseline_output_integrity["equal"] = (
            baseline_output_integrity["route_digest_before_shadow"]
            == baseline_output_integrity["route_digest_after_shadow"]
            and baseline_output_integrity["speed_digest_before_shadow"]
            == baseline_output_integrity["speed_digest_after_shadow"]
        )
        torch_flags_after = _torch_flags(torch)
        ownership_equal = ownership_before["canonical_sha256"] == ownership_after["canonical_sha256"]
        if not ownership_equal or not baseline_output_integrity["equal"] or flags_before != torch_flags_after:
            raise RuntimeError("BLOCKED_REAL_SIMLINGO_SHADOW_ISOLATION")

        core_end = time.monotonic_ns()
        candidate_rows = []
        for candidate, record in zip(candidates, (records[0], records[1])):
            annotation = annotations[candidate["candidate_id"]]
            candidate_rows.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "interpretation_id": candidate["interpretation_id"],
                    "backend_first_execution_id": record["candidate_id"],
                    "source_observation_id": candidate["source_observation_id"],
                    "source_frame_id": candidate["source_frame_id"],
                    "route": candidate["route"],
                    "speed": candidate["speed"],
                    "route_semantic": annotation["route_semantic"],
                    "stop_status": annotation["stop_status"],
                    "pid_desired_speed_mps": annotation["pid_desired_speed_mps"],
                    "generation_latency_ms": round(candidate["latency"] * 1000.0, 3),
                    "generation_age_ms_at_core_end": round((core_end - candidate_stage_end) / 1_000_000.0, 3),
                    "language": candidate["language"],
                    "candidate_input_digest": candidate["candidate_input_digest"],
                    "candidate_output_digest": candidate["candidate_output_digest"],
                }
            )

        return {
            "schema_version": LIVE_SHADOW_SCHEMA,
            "status": PASS_STATUS,
            "run_id": self.run_id,
            "source_identity": {
                "source_observation_id": observation_id,
                "source_frame_id": int(self._frame),
                "candidate_set_id": postprocess["candidate_set_id"],
                "source_digest": source_digest,
                "candidate_source_identity_consistent": True,
                "candidate_execution_identities": [
                    {
                        "candidate_id": record["candidate_id"],
                        "source_observation_id": record["source_observation_id"],
                        "source_frame_id": record["source_frame"],
                    }
                    for record in records
                ],
            },
            "instruction": {
                "fixture_id": "LIVE_FIXED_AMBIGUITY_FIXTURE_STOP_BRANCH_V0",
                "raw": RAW_INSTRUCTION,
                "interpretation_a": interpretation_a,
                "interpretation_b": interpretation_b,
                "external_language_model_calls": 0,
            },
            "candidate_runtime": {
                "semantic_candidate_count": 2,
                "backend_invocation_count": 4,
                "candidate_model_forward_count": 4,
                "forward_count_observability": "DIRECT_BACKEND_COMPLETION_RECORDS",
                "actual_schedule": ["A1", "B1", "A2", "B2"],
                "records": records,
            },
            "candidates": candidate_rows,
            "counterfactual_matrix": matrix_dict,
            "m2b": postprocess["m2b"],
            "m3": postprocess["m3"],
            "baseline": {
                "route": [list(point) for point in _tensor_points(baseline_route)],
                "speed": [list(point) for point in _tensor_points(baseline_speed)],
                "normal_forward_latency_ms": round((model_end - model_start) * 1000.0, 3),
                "model_output_integrity": baseline_output_integrity,
                "control_source": "BASELINE_SIMLINGO_PID_ONLY",
                "shadow_output_returned_to_control": False,
            },
            "performance": {
                "baseline_forward_count": self._baseline_forward_count,
                "shadow_backend_invocation_count": 4,
                "candidate_model_forward_count": 4,
                "candidate_generation_ms": round((candidate_stage_end - candidate_stage_started) / 1_000_000.0, 3),
                "candidate_each_ms": {
                    record["candidate_id"]: round(float(record["latency_seconds"]) * 1000.0, 3)
                    for record in records
                },
                "consequence_ms": postprocess["performance"]["consequence_ms"],
                "m2b_ms": postprocess["performance"]["m2b_ms"],
                "m2b_binding_total_ms": postprocess["performance"]["m2b_binding_total_ms"],
                "translation_ms": postprocess["performance"]["translation_ms"],
                "m3_bridge_ms": postprocess["performance"]["m3_bridge_ms"],
                "postprocess_subprocess_ms": round(postprocess_elapsed_ms, 3),
                "total_core_shadow_ms": round((core_end - event_started) / 1_000_000.0, 3),
                "visualization_render_ms": None,
            },
            "isolation": {
                "baseline_owned_mutation_delta": 0,
                "ownership_before_sha256": ownership_before["canonical_sha256"],
                "ownership_after_sha256": ownership_after["canonical_sha256"],
                "ownership_equal": ownership_equal,
                "model_training_before": ownership_before["model_training"],
                "model_training_after": ownership_after["model_training"],
                "model_outputs_before": ownership_before["model_outputs"],
                "model_outputs_after": ownership_after["model_outputs"],
                "planner_before": ownership_before["planner"],
                "planner_after": ownership_after["planner"],
                "pid_before": {
                    "speed": ownership_before["speed_pid"],
                    "turn": ownership_before["turn_pid"],
                },
                "pid_after": {
                    "speed": ownership_after["speed_pid"],
                    "turn": ownership_after["turn_pid"],
                },
                "command_history_before": ownership_before["commands"],
                "command_history_after": ownership_after["commands"],
                "ukf_before": ownership_before["ukf"],
                "ukf_after": ownership_after["ukf"],
                "observation_cache_before": ownership_before["observation_cache"],
                "observation_cache_after": ownership_after["observation_cache"],
                "torch_flags_before": flags_before,
                "torch_flags_after": torch_flags_after,
                "rng_restored": True,
                "postprocess": postprocess["execution_isolation"],
                "baseline_control_rewrite_count": 0,
                "m3_control_writes": 0,
                "candidate_control_writes": 0,
                "extra_baseline_pid": 0,
                "planner_advancement": 0,
            },
            "safety": {
                "banner": [
                    "RESEARCH DEBUG VIEW",
                    "SIMULATION ONLY",
                    "NO FORMAL SAFETY GUARANTEE",
                    "BASELINE CONTROL ONLY",
                    "DRIVECLARIFY CONTROL WRITES = 0",
                ],
                "physical_wait": False,
                "live_act": False,
                "live_ask": False,
                "ttc": "NOT_AVAILABLE",
                "collision_risk": "NOT_AVAILABLE",
                "physical_safe_stop": "NOT_AVAILABLE",
            },
        }

    def on_control(self, control: Any, gt_velocity: Any, ready_time: float) -> None:
        self._baseline_control_ready_times.append(float(ready_time))
        before = _control_values(control)
        passthrough = self.physical_wait.observe_baseline_control(
            control,
            frame=self._frame,
            ready_time=ready_time,
        )
        if passthrough is not control:
            self._disabled_after_error = True
            raise RuntimeError(CONTROL_BOUNDARY)
        self._last_returned_control = dict(before)
        self._last_returned_control_frame = self._frame
        after = _control_values(control)
        if before != after:
            self._disabled_after_error = True
            raise RuntimeError(CONTROL_BOUNDARY)
        self.limited_act.on_control(
            control,
            frame=self._frame,
            ready_time=float(ready_time),
        )
        if self._record is not None:
            self._record["baseline"]["returned_control"] = before
            self._record["baseline"]["control_ready_monotonic_s"] = float(ready_time)
            self._sync_physical_wait_record()
            self._sync_ask_replanning_record()
            self._sync_limited_act_record()
            _atomic_json(self.output_dir / "LIVE_SHADOW_EVENT.json", self._record)

    def commit(self) -> None:
        if self._record is not None and not self._visualization_disabled:
            try:
                self._record["performance"]["baseline_forward_count"] = self._baseline_forward_count
                self._sync_physical_wait_record()
                self._sync_ask_replanning_record()
                self._sync_limited_act_record()
                render_ms = float(self.visualizer.render(self._record))
                self._record["performance"]["visualization_render_ms"] = round(render_ms, 3)
                self._record["visualization"] = {
                    "status": "VISIBLE_LOCAL_WINDOW_UPDATED",
                    "world_route_overlay": "LIVE_WORLD_ROUTE_OVERLAY_NOT_ENABLED",
                    "ego_local_bev": True,
                    **dict(self.visualizer.summary()),
                }
                _atomic_json(self.output_dir / "LIVE_SHADOW_EVENT.json", self._record)
            except Exception as exc:
                self._visualization_disabled = True
                self._record["status"] = (
                    ASK_REPLAN_VIZ_PARTIAL_STATUS
                    if self.ask_replanning.enabled
                    else
                    PHYSICAL_WAIT_VIZ_PARTIAL_STATUS
                    if self.physical_wait.enabled
                    else "PARTIAL_PASS_CARLA_NO_CONTROL_SHADOW_VISUALIZATION_BLOCKED"
                )
                self._record["visualization"] = {
                    "status": "BLOCKED",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
                _atomic_json(self.output_dir / "LIVE_SHADOW_EVENT.json", self._record)
        self._persist_summary()

    def _persist_summary(self) -> None:
        _atomic_json(self.output_dir / "LIVE_SHADOW_SUMMARY.json", dict(self.summary()))

    def close(self) -> None:
        try:
            self.visualizer.close()
        finally:
            self._persist_summary()

    def summary(self) -> Mapping[str, Any]:
        actual_observed = [item for item in self._actual_control_comparisons if item["status"] == "OBSERVED"]
        event_frame = (
            self._record.get("source_identity", {}).get("source_frame_id")
            if self._record is not None
            else None
        )
        event_observed = [
            item
            for item in actual_observed
            if item["returned_control_source_frame"] == event_frame
        ]
        return {
            "schema_version": LIVE_SHADOW_SCHEMA,
            "enabled": True,
            "run_id": self.run_id,
            "status": self._record.get("status") if self._record is not None else self._error.get("status") if self._error else "WAITING_FOR_EVENT",
            "ambiguity_event_count": self._event_count,
            "baseline_forward_count": self._baseline_forward_count,
            "shadow_backend_invocation_count": self._backend_invocations,
            "candidate_model_forward_count": self._candidate_forwards,
            "control_writes": dict(self.limited_act.summary()).get(
                "candidate_control_writes", 0
            ),
            "m3_control_writes": 0,
            "extra_baseline_pid": 0,
            "planner_advancement": 0,
            "limited_act_commit_v0": dict(self.limited_act.summary()),
            "physical_wait_v0": self.physical_wait.summary(
                current_monotonic_time=time.monotonic()
            ),
            "ask_replanning_v0": self.ask_replanning.summary(
                current_monotonic=time.monotonic()
            ),
            "front_camera": dict(self._latest_front_metadata),
            "actual_control_comparisons": self._actual_control_comparisons,
            "actual_control_match": bool(actual_observed) and all(item["actuator_equal"] for item in actual_observed),
            "actual_control_exact_match": bool(actual_observed) and all(item["exact_equal"] for item in actual_observed),
            "event_control_match": bool(event_observed) and all(item["actuator_equal"] for item in event_observed),
            "event_control_exact_match": bool(event_observed) and all(item["exact_equal"] for item in event_observed),
            "control_comparison_note": "automatic gear may change inside CARLA after the returned baseline control; actuator_equal excludes gear only",
            "visualizer": dict(self.visualizer.summary()),
            "error": self._error,
            "automatic_retry": False,
        }


def build_live_shadow_runtime(agent: Any) -> Any:
    """Default-OFF factory; setup failure disables only the shadow branch."""

    if _truthy(os.environ.get("DRIVECLARIFY_METHOD_V2_2_HOLD_CALIBRATION")):
        try:
            from driveclarify_method_v2_2.holding_calibration import (
                build_holding_calibration_runtime,
            )

            return build_holding_calibration_runtime(agent)
        except Exception as exc:
            return NullLiveShadowRuntimeV0(
                "METHOD_V2_2_HOLD_CALIBRATION_SETUP_FAILED:" + type(exc).__name__
            )

    if _truthy(os.environ.get("DRIVECLARIFY_GROUNDED_LANGUAGE_V1")):
        if any(
            _truthy(os.environ.get(name))
            for name in (
                "DRIVECLARIFY_LANGUAGE_GROUNDING_V1",
                "DRIVECLARIFY_TEMPORAL_GROUNDING_V1",
                "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE",
                "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE",
            )
        ):
            return NullLiveShadowRuntimeV0(
                "GROUNDED_LANGUAGE_V1_EXISTING_OR_FROZEN_RUNTIME_CONFLICT"
            )
        try:
            from driveclarify_grounded_language_v1.runtime import (
                build_grounded_language_v1_runtime,
            )

            return build_grounded_language_v1_runtime(agent)
        except Exception as exc:
            return NullLiveShadowRuntimeV0(
                "GROUNDED_LANGUAGE_V1_SETUP_FAILED:" + type(exc).__name__
            )
    if _truthy(os.environ.get("DRIVECLARIFY_TEMPORAL_GROUNDING_V1")):
        if any(
            _truthy(os.environ.get(name))
            for name in (
                "DRIVECLARIFY_LANGUAGE_GROUNDING_V1",
                "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE",
                "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE",
            )
        ):
            return NullLiveShadowRuntimeV0(
                "TEMPORAL_GROUNDING_V1_FROZEN_OR_STATIC_RUNTIME_CONFLICT"
            )
        try:
            from driveclarify_temporal_grounding_v1.runtime import (
                build_temporal_grounding_v1_runtime,
            )

            return build_temporal_grounding_v1_runtime(agent)
        except Exception as exc:
            return NullLiveShadowRuntimeV0(
                "TEMPORAL_GROUNDING_V1_SETUP_FAILED:" + type(exc).__name__
            )
    if _truthy(os.environ.get("DRIVECLARIFY_LANGUAGE_GROUNDING_V1")):
        if _truthy(os.environ.get("DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE")) or _truthy(
            os.environ.get("DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE")
        ):
            return NullLiveShadowRuntimeV0(
                "LANGUAGE_GROUNDING_V1_FROZEN_STAGE_RUNTIME_CONFLICT"
            )
        try:
            from driveclarify_language_grounding_v1.runtime import (
                build_language_grounding_v1_runtime,
            )

            return build_language_grounding_v1_runtime(agent)
        except Exception as exc:
            return NullLiveShadowRuntimeV0(
                "LANGUAGE_GROUNDING_V1_SETUP_FAILED:" + type(exc).__name__
            )
    if _truthy(os.environ.get("DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE")):
        output = os.environ.get(
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_OUTPUT_DIR"
        ) or os.environ.get("DRIVECLARIFY_SHADOW_OUTPUT_DIR")
        if not output:
            return NullLiveShadowRuntimeV0(
                "STAGE6B_ENABLED_BUT_OUTPUT_DIR_MISSING"
            )
        try:
            from driveclarify_paper_mvp_stage6b.runtime_binding import (
                build_stage6b_simlingo_binding,
            )

            return build_stage6b_simlingo_binding(agent, output)
        except Exception as exc:
            path = Path(output) / "STAGE6B_LIVE_SETUP_ERROR.json"
            try:
                _atomic_json(
                    path,
                    {
                        "schema_version": (
                            "driveclarify.paper_mvp.stage6b.live_setup_error.v1"
                        ),
                        "status": (
                            "STAGE6B_SETUP_DISABLED_BASELINE_CONTINUES"
                        ),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            except Exception:
                pass
            return NullLiveShadowRuntimeV0(
                "STAGE6B_SETUP_FAILED:" + type(exc).__name__
            )
    if _truthy(os.environ.get("DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE")):
        output = os.environ.get(
            "DRIVECLARIFY_PAPER_MVP_STAGE6A_OUTPUT_DIR"
        ) or os.environ.get("DRIVECLARIFY_SHADOW_OUTPUT_DIR")
        if not output:
            return NullLiveShadowRuntimeV0(
                "STAGE6A_ENABLED_BUT_OUTPUT_DIR_MISSING"
            )
        try:
            from driveclarify_paper_mvp_runtime.simlingo_binding import (
                build_stage6a_simlingo_binding,
            )

            return build_stage6a_simlingo_binding(
                agent,
                output,
                run_id=os.environ.get(
                    "DRIVECLARIFY_PAPER_MVP_STAGE6A_RUN_ID",
                    "DC-PAPER-MVP-STAGE6A-LIVE",
                ),
            )
        except Exception as exc:
            path = Path(output) / "STAGE6A_LIVE_SETUP_ERROR.json"
            try:
                _atomic_json(
                    path,
                    {
                        "schema_version": (
                            "driveclarify.paper_mvp.stage6a.live_setup_error.v1"
                        ),
                        "status": (
                            "STAGE6A_SETUP_DISABLED_BASELINE_CONTINUES"
                        ),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            except Exception:
                pass
            return NullLiveShadowRuntimeV0(
                "STAGE6A_SETUP_FAILED:" + type(exc).__name__
            )
    if not _truthy(os.environ.get("DRIVECLARIFY_SHADOW_V0")):
        return NullLiveShadowRuntimeV0()
    output = os.environ.get("DRIVECLARIFY_SHADOW_OUTPUT_DIR")
    if not output:
        return NullLiveShadowRuntimeV0("ENABLED_BUT_OUTPUT_DIR_MISSING")
    try:
        return LiveShadowRuntimeV0(
            agent,
            output,
            run_id=os.environ.get("DRIVECLARIFY_SHADOW_RUN_ID", "DC-LIVE-SHADOW-V0"),
            trigger_after_baseline_forwards=int(
                os.environ.get("DRIVECLARIFY_SHADOW_TRIGGER_FORWARD", "3")
            ),
            physical_wait_pilot_duration_s=float(
                os.environ.get(
                    "DRIVECLARIFY_PHYSICAL_WAIT_PILOT_DURATION_S",
                    str(DEFAULT_PILOT_DURATION_S),
                )
            ),
            ask_oracle_delay_s=float(
                os.environ.get(
                    "DRIVECLARIFY_ASK_ORACLE_DELAY_S",
                    str(DEFAULT_ORACLE_DELAY_S),
                )
            ),
            ask_holding_lease_duration_s=float(
                os.environ.get(
                    "DRIVECLARIFY_ASK_HOLDING_LEASE_DURATION_S",
                    str(DEFAULT_HOLDING_LEASE_DURATION_S),
                )
            ),
            ask_oracle_answer_text=os.environ.get(
                "DRIVECLARIFY_ASK_ORACLE_ANSWER",
                DEFAULT_ORACLE_ANSWER,
            ),
        )
    except Exception as exc:
        path = Path(output) / "LIVE_SHADOW_ERROR.json"
        try:
            _atomic_json(
                path,
                {
                    "schema_version": LIVE_SHADOW_SCHEMA,
                    "status": "LIVE_SHADOW_SETUP_DISABLED_BASELINE_CONTINUES",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
        except Exception:
            pass
        return NullLiveShadowRuntimeV0("SETUP_FAILED:" + type(exc).__name__)
