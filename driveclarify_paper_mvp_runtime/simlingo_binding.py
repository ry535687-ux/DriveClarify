"""DriveClarify-only live binding for the existing SimLingo probe seams.

The binding is inert unless selected by the existing DriveClarify factory.  It
does not import SimLingo, torch, or CARLA at module import time.  Live model and
CARLA access occurs only in explicit callbacks after the baseline tick/forward.
No controller or PID is created here.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from driveclarify_language.interaction_contracts import (
    BindingStatus,
    CandidateSpecificTaskBinding,
)
from driveclarify_m3_live_authority import (
    CandidateExecutionContext,
    CandidateLiveActAuthorityResolverV0,
    FinalExecutionAuthority,
    FinalExecutionAuthorityResolverV0,
)
from driveclarify_m3_offline_replay.serialization import (
    canonical_sha256 as m3_canonical_sha256,
)
from driveclarify_m3_runtime_shadow.counterfactual_evidence import (
    ShadowCounterfactualMappingContext,
)
from driveclarify_m3_runtime_shadow.speed_consequence import (
    evaluate_pid_desired_speed_v0,
)
from driveclarify_static_branch.mapper import VERIFIED_PLAN_FRAME, VERIFIED_PLAN_UNIT
from driveclarify_static_branch.topology import (
    TOPOLOGY_SCHEMA,
    deterministic_sha256,
    with_sha256,
)

from .authority import PersistentPrePidAuthority
from .candidate_generation import RuntimeCandidateGenerator
from .contracts import (
    CandidatePlan,
    ConsequenceEvaluation,
    EgoState,
    ExecutionBoundaryFacts,
    HardRuleEvidence,
    PolicyEpisodeInput,
    RouteContext,
    RuntimeCandidate,
    Stage6AContractError,
    VisionObservation,
    VisualReference,
    assert_no_evaluation_fields,
    assert_unprivileged_source,
    canonical_sha256,
)
from .orchestrator import Stage6AOrchestrationResult, Stage6AOrchestrator


STAGE6A_LIVE_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE"
STAGE6A_AUTHORITY_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6A_ACT_AUTHORITY"
STAGE6A_VISUALIZATION_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6A_VISUALIZATION"
STAGE6A_VISUALIZATION_STRIDE_ENV = (
    "DRIVECLARIFY_PAPER_MVP_STAGE6A_VISUALIZATION_STRIDE"
)
STAGE6A_SCHEMA = "driveclarify.paper_mvp.stage6a.live_binding_audit.v1"
STAGE6A_AUDIT_FILENAME = "stage6a_live_binding_audit.json"


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "value") and isinstance(value.value, (str, int, float, bool)):
        return value.value
    return repr(value)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            _jsonable(payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _to_python(value: Any) -> Any:
    current = value
    for method in ("detach", "cpu"):
        operation = getattr(current, method, None)
        if callable(operation):
            current = operation()
    tolist = getattr(current, "tolist", None)
    if callable(tolist):
        return tolist()
    return current


def _scalar(value: Any) -> float | None:
    current = _to_python(value)
    while isinstance(current, (list, tuple)) and len(current) == 1:
        current = current[0]
    try:
        number = float(current)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _xy(value: Any) -> tuple[float, float] | None:
    current = _to_python(value)
    while (
        isinstance(current, (list, tuple))
        and len(current) == 1
        and isinstance(current[0], (list, tuple))
    ):
        current = current[0]
    if not isinstance(current, (list, tuple)) or len(current) < 2:
        return None
    try:
        pair = (float(current[0]), float(current[1]))
    except (TypeError, ValueError):
        return None
    return pair if all(math.isfinite(item) for item in pair) else None


def _points(value: Any) -> tuple[tuple[float, float], ...] | None:
    current = _to_python(value)
    while (
        isinstance(current, (list, tuple))
        and len(current) == 1
        and isinstance(current[0], (list, tuple))
        and current[0]
        and isinstance(current[0][0], (list, tuple))
    ):
        current = current[0]
    if not isinstance(current, (list, tuple)):
        return None
    result: list[tuple[float, float]] = []
    for item in current:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        try:
            point = (float(item[0]), float(item[1]))
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(component) for component in point):
            return None
        result.append(point)
    return tuple(result)


def _safe_category(type_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", " ", type_id.replace(".", " "))
    cleaned = " ".join(cleaned.split())[:63]
    return cleaned or "runtime actor"


def _front_rgb(
    input_data: Any, expected_frame: Any
) -> tuple[Mapping[str, Any], Any]:
    if not isinstance(input_data, Mapping):
        raise Stage6AContractError("FRONT_RGB_INPUT_MAPPING_REQUIRED")
    selected_key = next(
        (key for key in ("rgb_0", "rgb", "front_rgb") if key in input_data),
        None,
    )
    if selected_key is None:
        raise Stage6AContractError("FRONT_RGB_SENSOR_MISSING")
    entry = input_data[selected_key]
    if not isinstance(entry, (list, tuple)) or len(entry) < 2:
        raise Stage6AContractError("FRONT_RGB_SENSOR_ENTRY_INVALID")
    sensor_frame, image = entry[0], entry[1]
    if expected_frame is None or int(sensor_frame) != int(expected_frame):
        raise Stage6AContractError("FRONT_RGB_FRAME_IDENTITY_MISMATCH")
    shape = getattr(image, "shape", None)
    if not isinstance(shape, tuple) or len(shape) < 2:
        try:
            shape = tuple(shape)
        except Exception as exc:
            raise Stage6AContractError("FRONT_RGB_SHAPE_UNAVAILABLE") from exc
    height, width = int(shape[0]), int(shape[1])
    if width <= 0 or height <= 0:
        raise Stage6AContractError("FRONT_RGB_DIMENSIONS_INVALID")
    tobytes = getattr(image, "tobytes", None)
    if not callable(tobytes):
        raise Stage6AContractError("FRONT_RGB_BYTES_UNAVAILABLE")
    raw = tobytes()
    if not isinstance(raw, bytes) or not raw:
        raise Stage6AContractError("FRONT_RGB_BYTES_EMPTY")
    metadata = {
        "sensor_key": selected_key,
        "sensor_frame": int(sensor_frame),
        "image_sha256": hashlib.sha256(raw).hexdigest(),
        "image_width": width,
        "image_height": height,
        "channels": int(shape[2]) if len(shape) > 2 else None,
        "dtype": str(getattr(image, "dtype", "UNKNOWN")),
        "byte_count": len(raw),
        "source": "ONLINE_FRONT_RGB_SENSOR_BYTES",
    }
    return metadata, image


@dataclass(frozen=True)
class LiveCandidateRouteOperand:
    """Authoritative route-sidecar evidence keyed by a runtime visual track."""

    visual_track_id: str
    route_identity_sha256: str
    connector_identity_sha256: str
    branch_identity_sha256: str
    branch_polyline_world: tuple[tuple[float, float], ...]
    road_options: tuple[str, ...]
    evidence_source: str

    def __post_init__(self) -> None:
        assert_unprivileged_source(self.visual_track_id, "ROUTE_OPERAND_TRACK_ID")
        for value, field in (
            (self.route_identity_sha256, "ROUTE_OPERAND_ROUTE_IDENTITY"),
            (self.connector_identity_sha256, "ROUTE_OPERAND_CONNECTOR_IDENTITY"),
            (self.branch_identity_sha256, "ROUTE_OPERAND_BRANCH_IDENTITY"),
        ):
            if (
                type(value) is not str
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise Stage6AContractError(field + "_MUST_BE_SHA256")
        if (
            not isinstance(self.branch_polyline_world, tuple)
            or len(self.branch_polyline_world) < 3
            or any(
                not isinstance(point, tuple)
                or len(point) != 2
                or not all(math.isfinite(float(value)) for value in point)
                for point in self.branch_polyline_world
            )
        ):
            raise Stage6AContractError("ROUTE_OPERAND_BRANCH_POLYLINE_INVALID")
        if (
            not isinstance(self.road_options, tuple)
            or not self.road_options
            or any(type(value) is not str or not value.strip() for value in self.road_options)
        ):
            raise Stage6AContractError("ROUTE_OPERAND_ROAD_OPTIONS_INVALID")
        assert_unprivileged_source(self.evidence_source, "ROUTE_OPERAND_EVIDENCE_SOURCE")
        assert_no_evaluation_fields(asdict(self))


@dataclass(frozen=True)
class LiveSceneObservation:
    """Read-only online scene evidence; no evaluator annotations are admitted."""

    frame_id: int
    visual_references: tuple[VisualReference, ...]
    ego_position_x_m: float | None
    ego_position_y_m: float | None
    ego_yaw_degrees: float | None
    branch_polylines_world: Mapping[str, tuple[tuple[float, float], ...]]
    track_world_locations: Mapping[str, tuple[float, float]]
    projection_source_kind: str
    image_only_detector: bool
    privileged_simulation_state: bool
    independent_safety_guard_active: bool | None
    evidence: Mapping[str, Any]
    candidate_route_operands: Mapping[str, LiveCandidateRouteOperand] | None = None

    def __post_init__(self) -> None:
        if type(self.frame_id) is not int or self.frame_id < 0:
            raise Stage6AContractError("LIVE_SCENE_FRAME_INVALID")
        if not isinstance(self.visual_references, tuple) or any(
            not isinstance(item, VisualReference) for item in self.visual_references
        ):
            raise Stage6AContractError("LIVE_SCENE_REFERENCES_INVALID")
        if type(self.image_only_detector) is not bool:
            raise Stage6AContractError("IMAGE_ONLY_DETECTOR_FLAG_REQUIRED")
        if type(self.privileged_simulation_state) is not bool:
            raise Stage6AContractError("PRIVILEGED_STATE_FLAG_REQUIRED")
        if self.independent_safety_guard_active is not None and type(
            self.independent_safety_guard_active
        ) is not bool:
            raise Stage6AContractError("SAFETY_GUARD_STATE_MUST_BE_BOOLEAN_OR_UNKNOWN")
        if self.candidate_route_operands is not None:
            if not isinstance(self.candidate_route_operands, Mapping):
                raise Stage6AContractError("CANDIDATE_ROUTE_OPERANDS_MUST_BE_MAPPING")
            for track_id, operand in self.candidate_route_operands.items():
                if not isinstance(operand, LiveCandidateRouteOperand):
                    raise Stage6AContractError("CANDIDATE_ROUTE_OPERAND_CONTRACT_REQUIRED")
                if str(track_id) != operand.visual_track_id:
                    raise Stage6AContractError("CANDIDATE_ROUTE_OPERAND_TRACK_KEY_MISMATCH")


@dataclass(frozen=True)
class CandidateForwardResult:
    plan: CandidatePlan
    raw_route: Any
    raw_speed: Any
    forward_evidence: Mapping[str, Any]
    model_forward_count: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.plan, CandidatePlan):
            raise Stage6AContractError("CANDIDATE_FORWARD_PLAN_CONTRACT_REQUIRED")
        if self.raw_route is None or self.raw_speed is None:
            raise Stage6AContractError("CANDIDATE_FORWARD_RAW_PLAN_REQUIRED")
        if self.model_forward_count != 1:
            raise Stage6AContractError("EACH_CANDIDATE_REQUIRES_ONE_MODEL_FORWARD")


def _branch_paths_from_carla_waypoint(waypoint: Any) -> dict[str, tuple[tuple[float, float], ...]]:
    if waypoint is None:
        return {}
    location = waypoint.transform.location
    states: list[tuple[Any, tuple[tuple[float, float], ...]]] = [
        (waypoint, ((float(location.x), float(location.y)),))
    ]
    # SimLingo emits a 20-point local route whose curved endpoint can lie just
    # beyond a coarse CARLA successor polyline.  Cover the same bounded 25 m
    # horizon at 2.5 m resolution: the denser points avoid chord error on the
    # turn while the frozen mapper threshold remains unchanged.  This is only
    # a read-only map query and does not tick the world or authorize control.
    for _ in range(10):
        expanded: list[tuple[Any, tuple[tuple[float, float], ...]]] = []
        for current, path in states:
            try:
                next_items = list(current.next(2.5))[:4]
            except Exception:
                next_items = []
            if not next_items:
                expanded.append((current, path))
                continue
            for next_item in next_items:
                point = next_item.transform.location
                expanded.append(
                    (
                        next_item,
                        path + ((float(point.x), float(point.y)),),
                    )
                )
        states = expanded[:16]
    unique: dict[str, tuple[tuple[float, float], ...]] = {}
    for _, path in states:
        if len(path) < 3:
            continue
        role = "runtime-branch-" + canonical_sha256(path)[:16]
        unique.setdefault(role, path)
    return unique


_INDEPENDENT_GUARD_STATE_ATTRIBUTES = (
    "independent_safety_guard_active",
    "safety_guard_active",
    "emergency_override_active",
    "control_override_active",
    "manual_override_active",
)


def _read_independent_safety_guard_state(
    agent: Any,
) -> tuple[bool | None, dict[str, Any]]:
    """Observe higher-priority control ownership without inventing safety.

    An explicit runtime guard flag always wins.  When no such interface exists,
    absence is treated as verified only for the exact single-writer SimLingo
    seam: one DriveClarify plan selection, one existing PID notification/call,
    one returned-control observation, and no direct ``apply_control`` writer.
    Any source-introspection failure remains UNKNOWN and therefore fail-closed.
    """

    explicit: dict[str, Any] = {}
    invalid_explicit: list[str] = []
    for name in _INDEPENDENT_GUARD_STATE_ATTRIBUTES:
        try:
            value = getattr(agent, name)
        except AttributeError:
            continue
        except Exception:
            invalid_explicit.append(name)
            continue
        if type(value) is bool:
            explicit[name] = value
        else:
            invalid_explicit.append(name)
    common = {
        "schema_version": "driveclarify.runtime_control_ownership_observation.v1",
        "guard_attributes_checked": list(_INDEPENDENT_GUARD_STATE_ATTRIBUTES),
        "explicit_guard_states": explicit,
        "invalid_explicit_guard_states": invalid_explicit,
        "read_only": True,
        "no_safety_pass_inferred": True,
    }
    if invalid_explicit:
        return None, {
            **common,
            "status": "UNKNOWN",
            "source": "RUNTIME_AGENT_GUARD_INTERFACE",
            "reason_codes": ["EXPLICIT_GUARD_STATE_INVALID_OR_UNREADABLE"],
        }
    if explicit:
        return any(explicit.values()), {
            **common,
            "status": "VERIFIED",
            "source": "RUNTIME_AGENT_GUARD_INTERFACE",
            "reason_codes": ["EXPLICIT_RUNTIME_GUARD_STATE_OBSERVED"],
        }

    try:
        run_step = inspect.getsource(type(agent).run_step)
        source_path = inspect.getsourcefile(type(agent))
    except Exception:
        return None, {
            **common,
            "status": "UNKNOWN",
            "source": "RUNTIME_SINGLE_WRITER_SOURCE_AUDIT",
            "reason_codes": ["RUN_STEP_SOURCE_UNAVAILABLE"],
        }
    checks = {
        "pre_pid_plan_selection_count": run_step.count(
            "self._dc_probe.select_plan_source("
        ),
        "existing_pid_notification_count": run_step.count(
            "self._dc_probe.on_pid_invocation("
        ),
        "existing_pid_call_count": run_step.count("self.control_pid("),
        "returned_control_observation_count": run_step.count(
            "self._dc_probe.on_baseline_control("
        ),
        "direct_apply_control_count": run_step.count("apply_control("),
    }
    verified_single_writer = checks == {
        "pre_pid_plan_selection_count": 1,
        "existing_pid_notification_count": 1,
        "existing_pid_call_count": 1,
        "returned_control_observation_count": 1,
        "direct_apply_control_count": 0,
    }
    evidence = {
        **common,
        "status": "VERIFIED" if verified_single_writer else "UNKNOWN",
        "source": "RUNTIME_SINGLE_WRITER_SOURCE_AUDIT",
        "agent_class": type(agent).__name__,
        "agent_module": type(agent).__module__,
        "run_step_source_path": source_path,
        "run_step_source_sha256": hashlib.sha256(
            run_step.encode("utf-8")
        ).hexdigest(),
        "control_topology_checks": checks,
        "reason_codes": [
            (
                "SINGLE_RETURNED_CONTROL_PATH_NO_INDEPENDENT_GUARD_CONFIGURED"
                if verified_single_writer
                else "SINGLE_WRITER_CONTROL_TOPOLOGY_NOT_VERIFIED"
            )
        ],
    }
    return (False if verified_single_writer else None), evidence


def read_live_carla_actor_projection(
    agent: Any,
    input_data: Any,
    tick_data: Any,
    frame: int,
    front_metadata: Mapping[str, Any],
) -> LiveSceneObservation:
    """Project CARLA actors into the camera FOV using privileged simulator state.

    This is explicitly *not* an image-only detector and must never be reported as
    one.  It is useful for simulator-bound runtime grounding, not a vision-only
    perception claim.
    """

    from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

    hero = CarlaDataProvider.get_hero_actor()
    world = CarlaDataProvider.get_world()
    if hero is None or world is None:
        raise Stage6AContractError("LIVE_CARLA_ACTOR_PROJECTION_UNAVAILABLE")
    transform = hero.get_transform()
    location = transform.location
    yaw_degrees = float(transform.rotation.yaw)
    yaw = math.radians(yaw_degrees)
    cosine, sine = math.cos(yaw), math.sin(yaw)
    width = int(front_metadata["image_width"])
    fov = float(getattr(getattr(agent, "config", None), "camera_fov", 110.0))
    half_fov = math.radians(fov / 2.0)
    focal = width / (2.0 * math.tan(half_fov))
    # Projection supplies simulator-bound task association only.  Require
    # independent local RGB structure at every proposed horizontal image
    # region so an actor outside/blank in the actual frame cannot become a
    # candidate merely because it exists in CARLA world state.  This remains a
    # hybrid diagnostic, not an image-only detector (Gate B owns that claim).
    try:
        import numpy as np

        entry = input_data[next(key for key in ("rgb_0", "rgb", "front_rgb") if key in input_data)]
        image = np.asarray(entry[1] if isinstance(entry, (tuple, list)) else entry)
        gray = (
            0.114 * image[:, :, 0].astype("float32")
            + 0.587 * image[:, :, 1].astype("float32")
            + 0.299 * image[:, :, 2].astype("float32")
        )
    except Exception as exc:
        raise Stage6AContractError("FRONT_RGB_REGION_SUPPORT_UNAVAILABLE") from exc

    rgb_support_threshold = 0.025
    proposals: list[Mapping[str, Any]] = []
    for actor in list(world.get_actors())[:128]:
        try:
            if int(actor.id) == int(hero.id):
                continue
            actor_location = actor.get_location()
            dx = float(actor_location.x) - float(location.x)
            dy = float(actor_location.y) - float(location.y)
            forward = cosine * dx + sine * dy
            right = -sine * dx + cosine * dy
            distance = math.hypot(forward, right)
            if forward <= 0.0 or distance > 60.0:
                continue
            bearing = math.atan2(right, forward)
            if abs(bearing) > half_fov:
                continue
            pixel_x = width / 2.0 + focal * math.tan(bearing)
            if not 0.0 <= pixel_x < width:
                continue
            track_id = "actor-" + str(int(actor.id))
            category = _safe_category(str(actor.type_id))
            extent = getattr(getattr(actor, "bounding_box", None), "extent", None)
            half_width_px = max(
                8,
                min(
                    width // 8,
                    int(round(focal * max(float(getattr(extent, "y", 0.7)), 0.35) / forward)),
                ),
            )
            x0 = max(0, int(round(pixel_x)) - half_width_px)
            x1 = min(width, int(round(pixel_x)) + half_width_px + 1)
            y0 = max(0, int(round(image.shape[0] * 0.34)))
            y1 = min(int(image.shape[0]), int(round(image.shape[0] * 0.88)))
            crop = gray[y0:y1, x0:x1]
            if crop.size == 0:
                continue
            horizontal_gradient = (
                float(np.abs(crop[:, 1:] - crop[:, :-1]).mean())
                if crop.shape[1] > 1
                else 0.0
            )
            vertical_gradient = (
                float(np.abs(crop[1:, :] - crop[:-1, :]).mean())
                if crop.shape[0] > 1
                else 0.0
            )
            rgb_support_score = (
                float(crop.std()) + horizontal_gradient + vertical_gradient
            ) / (3.0 * 255.0)
            role_name = str(getattr(actor, "attributes", {}).get("role_name", ""))
            proposals.append(
                {
                    "reference": VisualReference(
                        track_id=track_id,
                        category=category,
                        relative_bearing_degrees=math.degrees(bearing),
                        relative_distance_m=distance,
                        confidence=min(0.99, max(0.25, 0.5 + rgb_support_score)),
                        observation_source=(
                            "ONLINE_RGB_SUPPORTED_CARLA_ACTOR_PROJECTION"
                        ),
                    ),
                    "world": (float(actor_location.x), float(actor_location.y)),
                    "scenario_owned": role_name.startswith("driveclarify_e"),
                    "row": {
                        "track_id": track_id,
                        "pixel_x": round(pixel_x, 3),
                        "pixel_region_xyxy": [x0, y0, x1, y1],
                        "distance_m": round(distance, 3),
                        "bearing_degrees": round(math.degrees(bearing), 3),
                        "rgb_support_score": round(rgb_support_score, 9),
                        "rgb_support_pass": rgb_support_score >= rgb_support_threshold,
                        "scenario_owned_runtime_actor": role_name.startswith("driveclarify_e"),
                    },
                    "rgb_support_pass": rgb_support_score >= rgb_support_threshold,
                }
            )
        except Exception:
            continue
    supported = [item for item in proposals if item["rgb_support_pass"] is True]
    controlled = [item for item in supported if item["scenario_owned"] is True]
    selected = controlled if len(controlled) >= 2 else supported
    references = [item["reference"] for item in selected]
    track_world = {
        item["reference"].track_id: item["world"] for item in selected
    }
    projection_rows = [item["row"] for item in proposals]
    try:
        waypoint = world.get_map().get_waypoint(location)
        branches = _branch_paths_from_carla_waypoint(waypoint)
    except Exception:
        branches = {}
    guard_active, control_ownership_evidence = (
        _read_independent_safety_guard_state(agent)
    )
    return LiveSceneObservation(
        frame_id=int(frame),
        visual_references=tuple(references),
        ego_position_x_m=float(location.x),
        ego_position_y_m=float(location.y),
        ego_yaw_degrees=yaw_degrees,
        branch_polylines_world=branches,
        track_world_locations=track_world,
        projection_source_kind=(
            "CARLA_ACTOR_CAMERA_PROJECTION_WITH_GENUINE_RGB_REGION_SUPPORT"
        ),
        image_only_detector=False,
        privileged_simulation_state=True,
        independent_safety_guard_active=guard_active,
        evidence={
            "projection_claim": (
                "PRIVILEGED_CARLA_ACTOR_GEOMETRY_PROJECTED_TO_CAMERA_FOV_"
                "NOT_IMAGE_ONLY_DETECTOR"
            ),
            "camera_fov_degrees": fov,
            "projected_actor_count": len(references),
            "all_projected_actor_count": len(proposals),
            "rgb_supported_actor_count": len(supported),
            "scenario_owned_rgb_supported_actor_count": len(controlled),
            "candidate_pool": (
                "SCENARIO_OWNED_RUNTIME_ACTORS_WITH_RGB_SUPPORT"
                if len(controlled) >= 2
                else "ALL_RUNTIME_ACTORS_WITH_RGB_SUPPORT"
            ),
            "rgb_support_threshold": rgb_support_threshold,
            "genuine_front_rgb_bytes_consumed": True,
            "world_projection_not_reported_as_image_only_grounding": True,
            "control_ownership": control_ownership_evidence,
            "projection_rows_sha256": canonical_sha256(projection_rows),
            "branch_count": len(branches),
            "no_physical_safety_inference": True,
            "no_hard_rule_inference": True,
        },
    )


class SimLingoCandidateForwardProvider:
    """Run one isolated candidate-conditioned SimLingo forward per call."""

    _CACHE_ATTRIBUTE_NAMES = (
        "past_key_values",
        "_past_key_values",
        "kv_cache",
        "_kv_cache",
        "cache_mask",
    )

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self._event_calls = 0

    def begin_event(self) -> None:
        self._event_calls = 0

    @property
    def event_forward_count(self) -> int:
        return self._event_calls

    @staticmethod
    def _empty_cache_value(value: Any) -> bool:
        return value is None or (
            isinstance(value, (list, tuple, dict)) and len(value) == 0
        )

    @classmethod
    def _cache_state(cls, model: Any) -> tuple[tuple[Any, str, Any], ...]:
        result: list[tuple[Any, str, Any]] = []
        for module in model.modules():
            values = getattr(module, "__dict__", {})
            for name in cls._CACHE_ATTRIBUTE_NAMES:
                if name in values:
                    result.append((module, name, values[name]))
        return tuple(result)

    @classmethod
    def _restore_cache_state(
        cls,
        model: Any,
        before: tuple[tuple[Any, str, Any], ...],
    ) -> None:
        original = {(id(module), name): value for module, name, value in before}
        for module in model.modules():
            values = getattr(module, "__dict__", {})
            for name in cls._CACHE_ATTRIBUTE_NAMES:
                key = (id(module), name)
                if key in original:
                    values[name] = original[key]
                elif name in values:
                    values.pop(name, None)

    @staticmethod
    def _clone_value(torch: Any, value: Any) -> Any:
        if isinstance(value, torch.Tensor):
            return value.detach().clone()
        if isinstance(value, list):
            return [SimLingoCandidateForwardProvider._clone_value(torch, item) for item in value]
        if isinstance(value, tuple):
            return tuple(
                SimLingoCandidateForwardProvider._clone_value(torch, item)
                for item in value
            )
        if isinstance(value, dict):
            return {
                key: SimLingoCandidateForwardProvider._clone_value(torch, item)
                for key, item in value.items()
            }
        return value

    def __call__(
        self, episode: PolicyEpisodeInput, candidate: RuntimeCandidate
    ) -> CandidateForwardResult:
        if self._event_calls >= 2:
            raise RuntimeError("STAGE6A_MORE_THAN_TWO_CANDIDATE_FORWARDS_FORBIDDEN")
        import torch
        from simlingo_training.utils.custom_types import DrivingInput
        from driveclarify_m3_runtime_shadow.live_shadow_runtime import (
            _SimLingoLabelBuilder,
        )

        base = DrivingInput(**self.agent.DrivingInput)
        cloned_values = {
            name: self._clone_value(torch, value)
            for name, value in base._asdict().items()
        }
        speed = float(episode.ego_state.speed_mps)
        forwarded_prompt = (
            "<INSTRUCTION_FOLLOWING> Current speed: "
            + f"{speed:.1f}"
            + " m/s. "
            + candidate.prompt_text
            + " Predict the waypoints."
        )
        label = _SimLingoLabelBuilder(self.agent)(forwarded_prompt)
        cloned_values["prompt"] = label
        cloned_values["prompt_inference"] = label
        model_input = DrivingInput(**cloned_values)
        model = getattr(self.agent, "model")
        inner_model = getattr(model, "model", model)
        # The upstream SimLingo agent does not call ``eval()`` during setup, so
        # its ordinary inference object can still carry training=True flags.
        # Candidate-conditioned inference must nevertheless be deterministic
        # and dropout-free.  Scope eval mode to this one candidate forward and
        # restore every module's exact prior flag before the existing PID seam.
        training = tuple(
            (module, bool(module.training)) for module in model.modules()
        )
        cache_state = self._cache_state(inner_model)
        if any(
            not self._empty_cache_value(value)
            for _, _, value in cache_state
        ):
            raise RuntimeError("STAGE6A_PERSISTENT_MODEL_CACHE_PRESENT")
        output_state = {
            name: getattr(inner_model, name, None)
            for name in ("route", "speed_wps", "language")
        }
        cpu_rng = torch.random.get_rng_state()
        cuda_rng = torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
        started = time.monotonic()
        try:
            model.eval()
            if any(bool(module.training) for module, _ in training):
                raise RuntimeError("STAGE6A_SIMLINGO_TEMPORARY_MODEL_EVAL_FAILED")
            inner_model.route = None
            inner_model.speed_wps = None
            inner_model.language = []
            with torch.inference_mode():
                pred_speed, pred_route, language = model(model_input)
            self._event_calls += 1
            pred_route = (
                pred_route.float().detach().clone()
                if pred_route is not None
                else None
            )
            pred_speed = (
                pred_speed.float().detach().clone()
                if pred_speed is not None
                else None
            )
        finally:
            try:
                torch.random.set_rng_state(cpu_rng)
                if cuda_rng is not None:
                    torch.cuda.set_rng_state_all(cuda_rng)
            finally:
                try:
                    self._restore_cache_state(inner_model, cache_state)
                finally:
                    for module, was_training in training:
                        module.training = was_training
                    for name, value in output_state.items():
                        setattr(inner_model, name, value)
        ended = time.monotonic()
        route = _points(pred_route)
        speed_plan = _points(pred_speed)
        if route is None or speed_plan is None:
            raise RuntimeError("STAGE6A_SIMLINGO_CANDIDATE_OUTPUT_SHAPE_INVALID")
        plan = CandidatePlan(
            candidate_id=candidate.candidate_id,
            source_observation_id=episode.vision_observation.observation_id,
            source_frame_id=episode.vision_observation.frame_id,
            route=route,
            speed=speed_plan,
            language=(candidate.prompt_text,),
            model_forward_sequence_id=(
                "stage6a-forward-" + candidate.candidate_id
            ),
            latency_s=ended - started,
        )
        return CandidateForwardResult(
            plan=plan,
            raw_route=pred_route,
            raw_speed=pred_speed,
            forward_evidence={
                "candidate_id": candidate.candidate_id,
                "source_observation_id": episode.vision_observation.observation_id,
                "source_frame_id": episode.vision_observation.frame_id,
                "candidate_prompt_sha256": canonical_sha256(candidate.prompt_text),
                "forwarded_prompt_sha256": canonical_sha256(forwarded_prompt),
                # Exact text actually tokenized by the existing SimLingo
                # adapter.  This is receipt-only evidence; it is not consumed
                # by policy, planning, PID, or visualization.
                "forwarded_prompt_text": forwarded_prompt,
                "route_sha256": m3_canonical_sha256(route),
                "speed_sha256": evaluate_pid_desired_speed_v0(speed_plan).source_digest,
                "model_forward_count": 1,
                "fresh_candidate_conditioned_model_execution": True,
                "baseline_plan_used_as_candidate": False,
                "torch_inference_only": True,
                "rng_restored_after_forward": True,
                "candidate_input_cloned": True,
                "candidate_output_tensors_cloned": True,
                "persistent_generation_cache_empty_before": True,
                "persistent_generation_cache_restored_after": True,
                "model_eval_verified": True,
                "model_eval_scope": "ONE_CANDIDATE_FORWARD_ONLY",
                "model_training_state_before_sha256": canonical_sha256(
                    [was_training for _, was_training in training]
                ),
                "model_training_state_restored_after": all(
                    bool(module.training) is was_training
                    for module, was_training in training
                ),
                "started_monotonic": started,
                "ended_monotonic": ended,
            },
        )


class FailClosedPhysicalSafetyMonitor:
    """No physical PASS is synthesized from route, actors, or language."""

    blocker = "NO_INDEPENDENT_PHYSICAL_SAFETY_MONITOR_CONNECTED"

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        return None


class FailClosedHardRuleMonitor:
    """No rule PASS is synthesized from topology availability."""

    blocker = "NO_INDEPENDENT_HARD_RULE_MONITOR_CONNECTED"

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        return None


def _carla_actor_location_xy(actor: Any) -> tuple[float, float]:
    location = actor.get_location()
    return float(location.x), float(location.y)


def _carla_trigger_location(actor: Any) -> tuple[float, float] | None:
    volume = getattr(actor, "trigger_volume", None)
    local = getattr(volume, "location", None)
    if local is None:
        return None
    try:
        import carla

        point = carla.Location(
            x=float(local.x), y=float(local.y), z=float(local.z)
        )
        actor.get_transform().transform(point)
        return float(point.x), float(point.y)
    except Exception:
        return None


def _ego_relative_xy(
    world_point: tuple[float, float], episode: PolicyEpisodeInput
) -> tuple[float, float]:
    dx = world_point[0] - episode.ego_state.position_x_m
    dy = world_point[1] - episode.ego_state.position_y_m
    yaw = math.radians(episode.ego_state.yaw_degrees)
    cosine, sine = math.cos(yaw), math.sin(yaw)
    return cosine * dx + sine * dy, -sine * dx + cosine * dy


def _live_carla_boundary(
    episode: PolicyEpisodeInput,
) -> tuple[Any, Any, Any, float] | None:
    """Return a same-frame CARLA boundary without advancing the simulator."""

    try:
        from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

        hero = CarlaDataProvider.get_hero_actor()
        world = CarlaDataProvider.get_world()
        if hero is None or world is None or not bool(hero.is_alive):
            return None
        snapshot = world.get_snapshot()
        if int(snapshot.frame) != int(episode.vision_observation.frame_id):
            return None
        fixed_delta = world.get_settings().fixed_delta_seconds
        if fixed_delta is None:
            return None
        horizon = float(fixed_delta)
        if not math.isfinite(horizon) or not 0.0 < horizon <= 0.1:
            return None
        return hero, world, snapshot, horizon
    except Exception:
        return None


class CarlaOneTickHardRuleMonitor:
    """Independent same-frame rule envelope for exactly one existing PID tick."""

    blocker = "CARLA_ONE_TICK_HARD_RULE_NOT_VERIFIED"

    def __init__(self) -> None:
        self.last_evidence: Mapping[str, Any] | None = None

    @staticmethod
    def _unknown(
        episode: PolicyEpisodeInput,
        observed: float,
        reason: str,
    ) -> HardRuleEvidence:
        return HardRuleEvidence(
            status="UNKNOWN",
            availability="UNKNOWN",
            evidence_grade="NOT_CURRENTLY_AVAILABLE",
            source="CARLA_ONE_TICK_RULE_ENVELOPE_V1",
            source_kind="INDEPENDENT_TRAFFIC_RULE_MONITOR",
            source_observation_id=episode.vision_observation.observation_id,
            source_frame_id=episode.vision_observation.frame_id,
            observed_monotonic_time=observed,
            rule_critical_eligible=False,
            reason_codes=(reason,),
        )

    @staticmethod
    def _blocked(
        episode: PolicyEpisodeInput,
        observed: float,
        reason: str,
    ) -> HardRuleEvidence:
        return HardRuleEvidence(
            status="BLOCKED",
            availability="AVAILABLE_VERIFIED",
            evidence_grade="VERIFIED_FROM_CONTROLLED_PROBE",
            source="CARLA_ONE_TICK_RULE_ENVELOPE_V1",
            source_kind="INDEPENDENT_TRAFFIC_RULE_MONITOR",
            source_observation_id=episode.vision_observation.observation_id,
            source_frame_id=episode.vision_observation.frame_id,
            observed_monotonic_time=observed,
            rule_critical_eligible=False,
            reason_codes=(reason,),
        )

    def __call__(
        self,
        episode: PolicyEpisodeInput,
        candidates: tuple[RuntimeCandidate, RuntimeCandidate],
        plans: tuple[CandidatePlan, CandidatePlan],
        consequence: ConsequenceEvaluation,
        observed: float,
    ) -> HardRuleEvidence:
        boundary = _live_carla_boundary(episode)
        if boundary is None:
            result = self._unknown(
                episode, observed, "CARLA_SAME_FRAME_RULE_BOUNDARY_UNAVAILABLE"
            )
            self.last_evidence = _jsonable(result)
            return result
        hero, world, _, horizon = boundary
        try:
            import carla

            waypoint = world.get_map().get_waypoint(
                hero.get_location(),
                project_to_road=False,
                lane_type=carla.LaneType.Driving,
            )
            if waypoint is None:
                result = self._blocked(
                    episode, observed, "EGO_NOT_ON_LIVE_DRIVING_LANE"
                )
                self.last_evidence = _jsonable(result)
                return result
            hero_extent = hero.bounding_box.extent
            longitudinal = (
                episode.ego_state.speed_mps * horizon
                + 0.5 * 8.0 * horizon * horizon
                + 0.25
            )
            half_length = max(0.5, float(hero_extent.x))
            half_width = max(0.5, float(hero_extent.y))
            for actor in list(world.get_actors()):
                type_id = str(actor.type_id)
                if type_id not in {"traffic.traffic_light", "traffic.stop"}:
                    continue
                trigger = _carla_trigger_location(actor)
                if trigger is None:
                    result = self._unknown(
                        episode,
                        observed,
                        "LIVE_TRAFFIC_CONTROL_TRIGGER_GEOMETRY_UNAVAILABLE",
                    )
                    self.last_evidence = _jsonable(result)
                    return result
                forward, right = _ego_relative_xy(trigger, episode)
                extent = getattr(getattr(actor, "trigger_volume", None), "extent", None)
                trigger_x = max(0.25, float(getattr(extent, "x", 0.25)))
                trigger_y = max(0.25, float(getattr(extent, "y", 0.25)))
                intersects = bool(
                    -half_length <= forward <= longitudinal + half_length + trigger_x
                    and abs(right) <= half_width + trigger_y + 0.5
                )
                if not intersects:
                    continue
                if type_id == "traffic.stop":
                    result = self._blocked(
                        episode, observed, "ONE_TICK_SWEEP_INTERSECTS_STOP_TRIGGER"
                    )
                    self.last_evidence = _jsonable(result)
                    return result
                state = actor.get_state()
                if state in {carla.TrafficLightState.Red, carla.TrafficLightState.Yellow}:
                    result = self._blocked(
                        episode,
                        observed,
                        "ONE_TICK_SWEEP_INTERSECTS_NON_GREEN_LIGHT_TRIGGER",
                    )
                    self.last_evidence = _jsonable(result)
                    return result
            result = HardRuleEvidence(
                status="PASS",
                availability="AVAILABLE_VERIFIED",
                evidence_grade="VERIFIED_FROM_CONTROLLED_PROBE",
                source="CARLA_ONE_TICK_RULE_ENVELOPE_V1",
                source_kind="INDEPENDENT_TRAFFIC_RULE_MONITOR",
                source_observation_id=episode.vision_observation.observation_id,
                source_frame_id=episode.vision_observation.frame_id,
                observed_monotonic_time=observed,
                rule_critical_eligible=True,
                reason_codes=(
                    "CARLA_SNAPSHOT_FRAME_IDENTITY_VERIFIED",
                    "LIVE_DRIVING_LANE_VERIFIED",
                    "ONE_TICK_TRAFFIC_CONTROL_SWEEP_CLEAR",
                ),
            )
        except Exception:
            result = self._unknown(
                episode, observed, "CARLA_RULE_MONITOR_READ_FAILED"
            )
        self.last_evidence = {
            **_jsonable(result),
            "bounded_horizon_seconds": horizon,
            "model_forward_count": 0,
            "pid_invocation_count": 0,
            "control_write_count": 0,
            "formal_safety_guarantee": False,
        }
        return result


class CarlaOneTickPhysicalSafetyMonitor:
    """Conservative actor sweep for one tick; never claims general safety."""

    blocker = "CARLA_ONE_TICK_PHYSICAL_SAFETY_NOT_VERIFIED"

    def __init__(self) -> None:
        self.last_evidence: Mapping[str, Any] | None = None

    def __call__(
        self,
        episode: PolicyEpisodeInput,
        candidates: tuple[RuntimeCandidate, RuntimeCandidate],
        plans: tuple[CandidatePlan, CandidatePlan],
        consequence: ConsequenceEvaluation,
        observed: float,
    ) -> Mapping[str, Any] | None:
        boundary = _live_carla_boundary(episode)
        if boundary is None:
            self.last_evidence = {
                "status": "UNKNOWN",
                "reason_codes": ["CARLA_SAME_FRAME_PHYSICAL_BOUNDARY_UNAVAILABLE"],
            }
            return None
        hero, world, _, horizon = boundary
        try:
            import carla

            waypoint = world.get_map().get_waypoint(
                hero.get_location(),
                project_to_road=False,
                lane_type=carla.LaneType.Driving,
            )
            if waypoint is None:
                self.last_evidence = {
                    "status": "BLOCKED",
                    "reason_codes": ["EGO_NOT_ON_LIVE_DRIVING_LANE"],
                }
                return {
                    "safety_status": "BLOCKED",
                    "availability": "AVAILABLE_VERIFIED",
                    "evidence_grade": "VERIFIED_FROM_CONTROLLED_PROBE",
                    "source": "CARLA_ONE_TICK_ACTOR_SWEEP_V1",
                    "source_kind": "INDEPENDENT_PHYSICAL_SAFETY_MONITOR",
                    "source_observation_id": episode.vision_observation.observation_id,
                    "source_frame_id": str(episode.vision_observation.frame_id),
                    "observed_monotonic_time": observed,
                    "usage_purpose": "PHYSICAL_CONTROL_AUTHORIZATION",
                    "safety_critical_eligible": False,
                    "reason_codes": ["EGO_NOT_ON_LIVE_DRIVING_LANE"],
                }
            travel = (
                episode.ego_state.speed_mps * horizon
                + 0.5 * 8.0 * horizon * horizon
                + 0.25
            )
            sweep_points: list[tuple[float, float]] = [(0.0, 0.0)]
            for plan in plans:
                if not plan.route or math.hypot(*plan.route[0]) > 1.0:
                    self.last_evidence = {
                        "status": "UNKNOWN",
                        "reason_codes": ["CANDIDATE_PLAN_ORIGIN_NOT_BOUND_TO_EGO"],
                    }
                    return None
                for point in plan.route:
                    if math.hypot(point[0], point[1]) <= travel + 0.5:
                        sweep_points.append((float(point[0]), float(point[1])))
            hero_extent = hero.bounding_box.extent
            hero_radius = math.hypot(
                max(float(hero_extent.x), 0.5),
                max(float(hero_extent.y), 0.5),
            )
            minimum_clearance = float("inf")
            checked_actor_count = 0
            ignored_anonymous_mesh_count = 0
            closest_actor: Mapping[str, Any] | None = None
            for actor in list(world.get_actors()):
                if int(actor.id) == int(hero.id) or not bool(actor.is_alive):
                    continue
                type_id = str(actor.type_id)
                if not type_id.startswith(("vehicle.", "walker.", "static.prop.")):
                    continue
                # CARLA's anonymous map mesh container is not an independently
                # bounded obstacle actor.  Its coarse bounding box can overlap
                # a valid driving lane and must not become a false collision
                # operand.  Specific static props remain in the sweep.
                if type_id == "static.prop.mesh":
                    ignored_anonymous_mesh_count += 1
                    continue
                relative = _ego_relative_xy(_carla_actor_location_xy(actor), episode)
                if math.hypot(*relative) > 60.0:
                    continue
                extent = actor.bounding_box.extent
                actor_radius = math.hypot(
                    max(float(extent.x), 0.1),
                    max(float(extent.y), 0.1),
                )
                velocity = actor.get_velocity()
                actor_motion = math.hypot(
                    float(velocity.x), float(velocity.y)
                ) * horizon
                clearance = min(
                    math.hypot(relative[0] - point[0], relative[1] - point[1])
                    - hero_radius
                    - actor_radius
                    - actor_motion
                    for point in sweep_points
                )
                if clearance < minimum_clearance:
                    minimum_clearance = clearance
                    closest_actor = {
                        "actor_id": int(actor.id),
                        "type_id": type_id,
                        "role_name": str(actor.attributes.get("role_name", "")),
                        "relative_xy_m": [float(relative[0]), float(relative[1])],
                        "actor_radius_m": float(actor_radius),
                        "clearance_m": float(clearance),
                    }
                checked_actor_count += 1
            if checked_actor_count == 0 or not math.isfinite(minimum_clearance):
                self.last_evidence = {
                    "status": "UNKNOWN",
                    "reason_codes": ["LIVE_COLLISION_ACTOR_SET_UNAVAILABLE"],
                }
                return None
            clear = minimum_clearance >= 0.5
            signal = {
                "safety_status": "PASS" if clear else "BLOCKED",
                "availability": "AVAILABLE_VERIFIED",
                "evidence_grade": "VERIFIED_FROM_CONTROLLED_PROBE",
                "source": "CARLA_ONE_TICK_ACTOR_SWEEP_V1",
                "source_kind": "INDEPENDENT_PHYSICAL_SAFETY_MONITOR",
                "source_observation_id": episode.vision_observation.observation_id,
                "source_frame_id": str(episode.vision_observation.frame_id),
                "observed_monotonic_time": observed,
                "usage_purpose": "PHYSICAL_CONTROL_AUTHORIZATION",
                "safety_critical_eligible": clear,
                "reason_codes": [
                    "CARLA_SNAPSHOT_FRAME_IDENTITY_VERIFIED",
                    "LIVE_DRIVING_LANE_VERIFIED",
                    (
                        "ONE_TICK_ACTOR_SWEEP_CLEAR"
                        if clear
                        else "ONE_TICK_ACTOR_SWEEP_OCCUPIED"
                    ),
                ],
            }
            self.last_evidence = {
                **signal,
                "bounded_horizon_seconds": horizon,
                "maximum_ego_travel_m": travel,
                "minimum_actor_clearance_m": minimum_clearance,
                "checked_actor_count": checked_actor_count,
                "ignored_anonymous_mesh_count": ignored_anonymous_mesh_count,
                "closest_actor": closest_actor,
                "model_forward_count": 0,
                "pid_invocation_count": 0,
                "control_write_count": 0,
                "formal_safety_guarantee": False,
            }
            return signal
        except Exception:
            self.last_evidence = {
                "status": "UNKNOWN",
                "reason_codes": ["CARLA_PHYSICAL_MONITOR_READ_FAILED"],
            }
            return None


def _nearest_branch(
    point: tuple[float, float],
    branches: Mapping[str, tuple[tuple[float, float], ...]],
) -> tuple[str | None, float]:
    best_role = None
    best_distance = float("inf")
    for role, polyline in branches.items():
        for branch_point in polyline:
            distance = math.hypot(point[0] - branch_point[0], point[1] - branch_point[1])
            if distance < best_distance:
                best_role, best_distance = role, distance
    return best_role, best_distance


def _nearest_branch_with_margin(
    point: tuple[float, float],
    branches: Mapping[str, tuple[tuple[float, float], ...]],
) -> tuple[str | None, float, float]:
    """Return the nearest canonical branch and its separation from the runner-up."""

    distances: list[tuple[float, str]] = []
    for role, polyline in branches.items():
        if not polyline:
            continue
        distances.append(
            (
                min(
                    math.hypot(
                        point[0] - branch_point[0],
                        point[1] - branch_point[1],
                    )
                    for branch_point in polyline
                ),
                role,
            )
        )
    if not distances:
        return None, float("inf"), 0.0
    distances.sort(key=lambda item: (item[0], item[1]))
    nearest_distance, nearest_role = distances[0]
    margin = (
        distances[1][0] - nearest_distance
        if len(distances) > 1
        else float("inf")
    )
    return nearest_role, nearest_distance, margin


def _derive_frozen_branch_roles(
    scene: LiveSceneObservation,
) -> tuple[
    Mapping[str, str] | None,
    Mapping[str, tuple[tuple[float, float], ...]] | None,
    tuple[str, ...],
]:
    """Bind live topology to the two semantic roles required by the frozen mapper.

    The labels are derived from online geometry in the current ego frame.  If a
    distinct forward branch and right-turn branch cannot be established, the
    mapping stays UNKNOWN instead of assigning a convenient role.
    """

    if (
        scene.ego_position_x_m is None
        or scene.ego_position_y_m is None
        or scene.ego_yaw_degrees is None
    ):
        return None, None, ("EGO_WORLD_TRANSFORM_UNKNOWN",)
    yaw = math.radians(scene.ego_yaw_degrees)
    cosine, sine = math.cos(yaw), math.sin(yaw)
    rows: list[tuple[str, tuple[tuple[float, float], ...], float, float]] = []
    for source_role, polyline in sorted(scene.branch_polylines_world.items()):
        if len(polyline) < 3:
            continue
        dx = float(polyline[-1][0]) - scene.ego_position_x_m
        dy = float(polyline[-1][1]) - scene.ego_position_y_m
        forward = cosine * dx + sine * dy
        right = -sine * dx + cosine * dy
        if math.hypot(forward, right) <= 2.0:
            continue
        angle = math.degrees(math.atan2(right, forward))
        rows.append((source_role, polyline, angle, math.hypot(forward, right)))
    straight = sorted(
        (row for row in rows if abs(row[2]) <= 25.0 and row[3] >= 5.0),
        key=lambda row: (abs(row[2]), -row[3], row[0]),
    )
    right = sorted(
        (row for row in rows if 25.0 < row[2] <= 135.0 and row[3] >= 5.0),
        key=lambda row: (abs(90.0 - row[2]), -row[3], row[0]),
    )
    if not straight or not right or straight[0][0] == right[0][0]:
        return None, None, ("LIVE_STRAIGHT_RIGHT_BRANCH_ROLES_UNKNOWN",)
    role_by_source = {
        straight[0][0]: "STRAIGHT_BRANCH",
        right[0][0]: "RIGHT_TURN_BRANCH",
    }
    polylines = {
        "STRAIGHT_BRANCH": straight[0][1],
        "RIGHT_TURN_BRANCH": right[0][1],
    }
    return (
        role_by_source,
        polylines,
        ("LIVE_BRANCH_ROLES_DERIVED_FROM_EGO_RELATIVE_GEOMETRY",),
    )


def _instruction_task_role(
    episode: PolicyEpisodeInput,
    role_polylines: Mapping[str, tuple[tuple[float, float], ...]],
) -> tuple[str | None, tuple[str, ...]]:
    """Resolve the shared maneuver target without using reference placement.

    A visual landmark in an instruction such as ``turn after the bus`` is a
    temporal reference, not the destination lane.  Roadside placement can move
    that landmark away from the lane it governs, so treating its actor position
    as a task goal is incorrect.  Resolve only explicit maneuver language (or a
    standard runtime route command) against the two live topology roles; leave
    every other case unknown so the older geometric reference binding can make
    an independently checkable decision.
    """

    available = set(role_polylines)
    instruction = " " + re.sub(
        r"[^a-z0-9]+", " ", episode.raw_instruction.casefold()
    ).strip() + " "
    route_command = episode.route_context.route_command.upper()
    if " left " in instruction or route_command.endswith("_1"):
        return None, ("LEFT_MANEUVER_NOT_PRESENT_IN_LIVE_TWO_BRANCH_TOPOLOGY",)
    if (
        " right " in instruction
        or route_command.endswith("_2")
    ) and "RIGHT_TURN_BRANCH" in available:
        return "RIGHT_TURN_BRANCH", (
            "TASK_ROLE_FROM_EXPLICIT_RUNTIME_RIGHT_MANEUVER",
        )
    if (
        " straight " in instruction
        or route_command.endswith("_3")
    ) and "STRAIGHT_BRANCH" in available:
        return "STRAIGHT_BRANCH", (
            "TASK_ROLE_FROM_EXPLICIT_RUNTIME_STRAIGHT_MANEUVER",
        )
    if (
        " turn " in instruction
        and available == {"STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"}
    ):
        return "RIGHT_TURN_BRANCH", (
            "DIRECTIONLESS_TURN_RESOLVED_BY_ONLY_LIVE_TURN_BRANCH",
        )
    return None, ("EXPLICIT_RUNTIME_MANEUVER_TARGET_UNAVAILABLE",)


def _explicit_route_operand_consequence(
    episode: PolicyEpisodeInput,
    candidates: tuple[RuntimeCandidate, RuntimeCandidate],
    plans: tuple[CandidatePlan, CandidatePlan],
    scene: LiveSceneObservation,
) -> ConsequenceEvaluation | None:
    """Bind candidates to authoritative route consequences without ordering."""

    operands = scene.candidate_route_operands
    if operands is None:
        return None
    candidate_ids = tuple(item.candidate_id for item in candidates)

    def unknown(*reasons: str) -> ConsequenceEvaluation:
        return ConsequenceEvaluation(
            status="UNKNOWN",
            source_observation_id=episode.vision_observation.observation_id,
            source_frame_id=episode.vision_observation.frame_id,
            candidate_ids=candidate_ids,
            evaluator_id="STAGE6A_RUNTIME_CONSEQUENCE_MAPPING",
            mapping_context=None,
            reason_codes=tuple(reasons),
        )

    expected_tracks = {item.visual_track_id for item in candidates}
    if len(expected_tracks) != 2:
        return unknown("CANDIDATE_VISUAL_TRACK_IDENTITY_NOT_UNIQUE")
    if set(operands) != expected_tracks:
        return unknown("CANDIDATE_ROUTE_OPERAND_SET_MISMATCH")
    plans_by_id = {item.candidate_id: item for item in plans}
    if set(plans_by_id) != set(candidate_ids):
        return unknown("CANDIDATE_PLAN_IDENTITY_SET_MISMATCH")

    bindings: dict[str, CandidateSpecificTaskBinding] = {}
    task_by_role: dict[str, str] = {}
    topology_by_branch: dict[str, LiveCandidateRouteOperand] = {}
    for candidate in candidates:
        operand = operands.get(candidate.visual_track_id)
        if not isinstance(operand, LiveCandidateRouteOperand):
            return unknown("CANDIDATE_ROUTE_OPERAND_MISSING")
        task_id = "RUNTIME_TASK_" + operand.branch_identity_sha256[:16]
        semantic_role = "ROUTE_BRANCH_" + operand.branch_identity_sha256[:16]
        prior = task_by_role.setdefault(semantic_role, task_id)
        if prior != task_id:
            return unknown("BRANCH_TASK_IDENTITY_CONFLICT")
        topology_by_branch.setdefault(operand.branch_identity_sha256, operand)
        bindings[candidate.interpretation_id] = CandidateSpecificTaskBinding(
            source_candidate_id=candidate.candidate_id,
            task_family="EXPLICIT_ROUTE_CONNECTOR_TASK",
            symbolic_target_type="ROUTE_CONNECTOR_EQUIVALENCE_CLASS",
            symbolic_target_id=task_id,
            required_slots=(
                "runtime_visual_reference",
                "route_identity",
                "connector_identity",
                "branch_identity",
                "road_options",
            ),
            binding_status=BindingStatus.BOUND,
            binding_source="ONLINE_AUTHORITATIVE_ROUTE_OPERAND_BINDING",
            frame_or_semantic_domain="CARLA_WORLD_ROUTE_CONNECTOR_TASK",
            provenance=(
                operand.evidence_source,
                "RUNTIME_VISUAL_TRACK_TO_ROUTE_SIDECAR",
                "NO_CANDIDATE_ENUMERATION_BINDING",
                "NO_PHYSICAL_SAFETY_INFERENCE",
                "NO_HARD_RULE_INFERENCE",
            ),
            reason_codes=(
                "RUNTIME_TRACK_ROUTE_CONNECTOR_BRANCH_IDENTITIES_BOUND",
            ),
        )
    topology = with_sha256(
        {
            "schema_version": TOPOLOGY_SCHEMA,
            "branches": [
                {
                    "semantic_role": "ROUTE_BRANCH_" + branch_id[:16],
                    "route_identity_sha256": operand.route_identity_sha256,
                    "connector_identity_sha256": operand.connector_identity_sha256,
                    "branch_identity_sha256": branch_id,
                    "road_options": list(operand.road_options),
                    "evaluation_polyline_world_xyz": [
                        [point[0], point[1], 0.0]
                        for point in operand.branch_polyline_world
                    ],
                }
                for branch_id, operand in sorted(topology_by_branch.items())
            ],
        }
    )
    thresholds = {
        "schema_version": "driveclarify.mapping_threshold_provenance.v1",
        "distance_threshold_m": 1.75,
        "alignment_threshold_cosine": 0.7,
        "branch_score_margin": 0.1,
        "lane_width_reference_m": 3.5,
        "topology_separation_m": 1.75,
        "numerical_tolerance": 1e-9,
        "tail_point_count": 3,
    }
    thresholds["sha256"] = deterministic_sha256(thresholds)
    operand_projection = {
        track_id: {
            "route_identity_sha256": operand.route_identity_sha256,
            "connector_identity_sha256": operand.connector_identity_sha256,
            "branch_identity_sha256": operand.branch_identity_sha256,
            "road_options": list(operand.road_options),
        }
        for track_id, operand in sorted(operands.items())
    }
    context = ShadowCounterfactualMappingContext(
        source_observation_id=episode.vision_observation.observation_id,
        source_frame_id=episode.vision_observation.frame_id,
        frozen_topology=topology,
        mapping_threshold_contract=thresholds,
        ego_transform={
            "source_frame": "CARLA_WORLD",
            "target_frame": VERIFIED_PLAN_FRAME,
            "location_xy_world_m": [scene.ego_position_x_m, scene.ego_position_y_m],
            "yaw_degrees": scene.ego_yaw_degrees,
            "evidence_status": "VERIFIED",
            "source": "ONLINE_CARLA_EGO_TRANSFORM_READ_ONLY",
        },
        plan_frame=VERIFIED_PLAN_FRAME,
        plan_unit=VERIFIED_PLAN_UNIT,
        frame_evidence_status="VERIFIED",
        unit_evidence_status="VERIFIED",
        branch_task_equivalence_classes=task_by_role,
        interpretation_task_bindings=bindings,
        mapping_provenance=(
            "ONLINE_AUTHORITATIVE_ROUTE_OPERAND_BINDING",
            "EXPLICIT_CANDIDATE_ROUTE_OPERAND_BINDING",
            "RUNTIME_VISUAL_TRACK_TO_ROUTE_SIDECAR",
            "PRIVILEGED_SIMULATION_STATE_NOT_IMAGE_ONLY_DETECTOR",
        ),
        source_artifacts=(
            episode.vision_observation.image_sha256,
            canonical_sha256(episode.raw_instruction),
            episode.route_context.route_digest,
            canonical_sha256(operand_projection),
            canonical_sha256(
                [plans_by_id[item].output_digest for item in candidate_ids]
            ),
            str(topology["sha256"]),
            str(thresholds["sha256"]),
        ),
        symbolic_target_type="ROUTE_CONNECTOR_EQUIVALENCE_CLASS",
    )
    return ConsequenceEvaluation(
        status="AVAILABLE_VERIFIED",
        source_observation_id=episode.vision_observation.observation_id,
        source_frame_id=episode.vision_observation.frame_id,
        candidate_ids=candidate_ids,
        evaluator_id="STAGE6A_RUNTIME_CONSEQUENCE_MAPPING",
        mapping_context=context,
        reason_codes=(
            "RUNTIME_ROUTE_CONNECTOR_OPERANDS_AVAILABLE",
            "CANDIDATES_BOUND_BY_VISUAL_TRACK_ID_NOT_ENUMERATION",
            "NO_PHYSICAL_SAFETY_OR_RULE_PASS_INFERRED",
        ),
    )


def build_runtime_consequence_evaluation(
    episode: PolicyEpisodeInput,
    candidates: tuple[RuntimeCandidate, RuntimeCandidate],
    plans: tuple[CandidatePlan, CandidatePlan],
    *,
    scene: LiveSceneObservation,
) -> ConsequenceEvaluation:
    candidate_ids = tuple(item.candidate_id for item in candidates)
    if scene.frame_id != episode.vision_observation.frame_id:
        raise Stage6AContractError("CONSEQUENCE_SCENE_FRAME_IDENTITY_MISMATCH")
    explicit = _explicit_route_operand_consequence(
        episode, candidates, plans, scene
    )
    if explicit is not None:
        return explicit
    if not scene.branch_polylines_world:
        return ConsequenceEvaluation(
            status="UNKNOWN",
            source_observation_id=episode.vision_observation.observation_id,
            source_frame_id=episode.vision_observation.frame_id,
            candidate_ids=candidate_ids,
            evaluator_id="STAGE6A_RUNTIME_CONSEQUENCE_MAPPING",
            mapping_context=None,
            reason_codes=("LIVE_MAP_BRANCH_TOPOLOGY_UNAVAILABLE",),
        )
    if scene.image_only_detector and not scene.track_world_locations:
        return ConsequenceEvaluation(
            status="UNKNOWN",
            source_observation_id=episode.vision_observation.observation_id,
            source_frame_id=episode.vision_observation.frame_id,
            candidate_ids=candidate_ids,
            evaluator_id="STAGE6A_RUNTIME_CONSEQUENCE_MAPPING",
            mapping_context=None,
            reason_codes=("IMAGE_ONLY_DETECTOR_HAS_NO_WORLD_TASK_BINDING",),
        )
    role_by_source, role_polylines, role_reasons = _derive_frozen_branch_roles(scene)
    if role_by_source is None or role_polylines is None:
        return ConsequenceEvaluation(
            status="UNKNOWN",
            source_observation_id=episode.vision_observation.observation_id,
            source_frame_id=episode.vision_observation.frame_id,
            candidate_ids=candidate_ids,
            evaluator_id="STAGE6A_RUNTIME_CONSEQUENCE_MAPPING",
            mapping_context=None,
            reason_codes=role_reasons,
        )
    bindings: dict[str, CandidateSpecificTaskBinding] = {}
    task_by_role = {
        role: "RUNTIME_TASK_" + canonical_sha256(role)[:16]
        for role in role_polylines
    }
    instruction_role, instruction_role_reasons = _instruction_task_role(
        episode, role_polylines
    )
    for candidate in candidates:
        actor_point = scene.track_world_locations.get(candidate.visual_track_id)
        if actor_point is None:
            return ConsequenceEvaluation(
                status="UNKNOWN",
                source_observation_id=episode.vision_observation.observation_id,
                source_frame_id=episode.vision_observation.frame_id,
                candidate_ids=candidate_ids,
                evaluator_id="STAGE6A_RUNTIME_CONSEQUENCE_MAPPING",
                mapping_context=None,
                reason_codes=("VISUAL_REFERENCE_WORLD_BINDING_UNAVAILABLE",),
            )
        if instruction_role is not None:
            # The actor lookup above still proves that each candidate refers to
            # a live, RGB-supported CARLA object.  The task goal, however, comes
            # from the shared maneuver language and live branch topology.
            role = instruction_role
            binding_source = "ONLINE_RAW_INSTRUCTION_AND_LIVE_TOPOLOGY"
            binding_reasons = instruction_role_reasons
        else:
            # Bind against the two canonical roles derived above, rather than
            # first choosing from every CARLA successor path.  CARLA commonly
            # returns overlapping/duplicate successor polylines around a
            # junction.  We still require a bounded distance and real margin.
            role, distance, separation = _nearest_branch_with_margin(
                actor_point, role_polylines
            )
            if role is None or distance > 12.0 or separation < 1.75:
                return ConsequenceEvaluation(
                    status="UNKNOWN",
                    source_observation_id=episode.vision_observation.observation_id,
                    source_frame_id=episode.vision_observation.frame_id,
                    candidate_ids=candidate_ids,
                    evaluator_id="STAGE6A_RUNTIME_CONSEQUENCE_MAPPING",
                    mapping_context=None,
                    reason_codes=(
                        "VISUAL_REFERENCE_BRANCH_BINDING_UNKNOWN",
                        *instruction_role_reasons,
                    ),
                )
            binding_source = "ONLINE_CARLA_ACTOR_BRANCH_PROJECTION"
            binding_reasons = (
                "RUNTIME_VISUAL_REFERENCE_NEAREST_CANONICAL_BRANCH_BOUND",
            )
        bindings[candidate.interpretation_id] = CandidateSpecificTaskBinding(
            source_candidate_id=candidate.candidate_id,
            task_family="CANONICAL_BRANCH_TASK",
            symbolic_target_type="BRANCH_TASK_EQUIVALENCE_CLASS",
            symbolic_target_id=task_by_role[role],
            required_slots=("runtime_visual_reference",),
            binding_status=BindingStatus.BOUND,
            binding_source=binding_source,
            frame_or_semantic_domain="CARLA_WORLD_BRANCH_TASK",
            provenance=(
                "RUNTIME_ACTOR_CAMERA_PROJECTION_NOT_IMAGE_ONLY_DETECTOR",
                (
                    "RUNTIME_MANEUVER_SEMANTICS_AND_LIVE_TOPOLOGY"
                    if instruction_role is not None
                    else "RUNTIME_REFERENCE_TO_CANONICAL_BRANCH_GEOMETRY"
                ),
                "NO_PHYSICAL_SAFETY_INFERENCE",
                "NO_HARD_RULE_INFERENCE",
            ),
            reason_codes=binding_reasons,
        )
    topology = with_sha256(
        {
            "schema_version": TOPOLOGY_SCHEMA,
            "branches": [
                {
                    "semantic_role": role,
                    "evaluation_polyline_world_xyz": [
                        [point[0], point[1], 0.0] for point in polyline
                    ],
                }
                for role, polyline in sorted(role_polylines.items())
            ],
        }
    )
    thresholds = {
        "schema_version": "driveclarify.mapping_threshold_provenance.v1",
        "distance_threshold_m": 1.75,
        "alignment_threshold_cosine": 0.7,
        "branch_score_margin": 0.1,
        "lane_width_reference_m": 3.5,
        "topology_separation_m": 1.75,
        "numerical_tolerance": 1e-9,
        "tail_point_count": 3,
    }
    thresholds["sha256"] = deterministic_sha256(thresholds)
    context = ShadowCounterfactualMappingContext(
        source_observation_id=episode.vision_observation.observation_id,
        source_frame_id=episode.vision_observation.frame_id,
        frozen_topology=topology,
        mapping_threshold_contract=thresholds,
        ego_transform={
            "source_frame": "CARLA_WORLD",
            "target_frame": VERIFIED_PLAN_FRAME,
            "location_xy_world_m": [
                scene.ego_position_x_m,
                scene.ego_position_y_m,
            ],
            "yaw_degrees": scene.ego_yaw_degrees,
            "evidence_status": "VERIFIED",
            "source": "ONLINE_CARLA_EGO_TRANSFORM_READ_ONLY",
        },
        plan_frame=VERIFIED_PLAN_FRAME,
        plan_unit=VERIFIED_PLAN_UNIT,
        frame_evidence_status="VERIFIED",
        unit_evidence_status="VERIFIED",
        branch_task_equivalence_classes=task_by_role,
        interpretation_task_bindings=bindings,
        mapping_provenance=(
            "ONLINE_RUNTIME_CARLA_MAP_TOPOLOGY",
            (
                "ONLINE_RAW_INSTRUCTION_MANEUVER_BINDING"
                if instruction_role is not None
                else "ONLINE_CARLA_ACTOR_CAMERA_PROJECTION"
            ),
            "PRIVILEGED_SIMULATION_STATE_NOT_IMAGE_ONLY_DETECTOR",
        ),
        source_artifacts=(
            episode.vision_observation.image_sha256,
            canonical_sha256(episode.raw_instruction),
            episode.route_context.route_digest,
            str(topology["sha256"]),
            str(thresholds["sha256"]),
        ),
    )
    return ConsequenceEvaluation(
        status="AVAILABLE_VERIFIED",
        source_observation_id=episode.vision_observation.observation_id,
        source_frame_id=episode.vision_observation.frame_id,
        candidate_ids=candidate_ids,
        evaluator_id="STAGE6A_RUNTIME_CONSEQUENCE_MAPPING",
        mapping_context=context,
        reason_codes=(
            "RUNTIME_TOPOLOGY_AND_TASK_BINDINGS_AVAILABLE",
            *role_reasons,
            *instruction_role_reasons,
            "NO_PHYSICAL_SAFETY_OR_RULE_PASS_INFERRED",
        ),
    )


class Stage6ASimLingoBinding:
    """Live object consumed by the existing DriveClarify probe hook."""

    enabled = True

    def __init__(
        self,
        agent: Any,
        output_dir: str | os.PathLike[str],
        *,
        run_id: str,
        scene_provider: Callable[..., LiveSceneObservation] | None = None,
        candidate_forward_provider: Callable[
            [PolicyEpisodeInput, RuntimeCandidate], CandidateForwardResult
        ]
        | None = None,
        physical_safety_monitor: Callable[..., Mapping[str, Any] | None] | None = None,
        hard_rule_monitor: Callable[..., HardRuleEvidence | None] | None = None,
        clarification_monitor: Callable[..., Mapping[str, Any] | None] | None = None,
        holding_monitor: Callable[..., Mapping[str, Any] | None] | None = None,
        instruction_provider: Callable[..., str | None] | None = None,
        decision_event_detector: Callable[[PolicyEpisodeInput], bool] | None = None,
        authority_enabled: bool = False,
        binding_fn: Callable[..., Any] | None = None,
        activation_fn: Callable[..., Any] | None = None,
        visualizer: Any | None = None,
    ) -> None:
        self.agent = agent
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = str(run_id)
        self.scene_provider = scene_provider or read_live_carla_actor_projection
        self.forward_provider = candidate_forward_provider or SimLingoCandidateForwardProvider(agent)
        self.physical_safety_monitor = (
            physical_safety_monitor or FailClosedPhysicalSafetyMonitor()
        )
        self.hard_rule_monitor = hard_rule_monitor or FailClosedHardRuleMonitor()
        self.clarification_monitor = clarification_monitor
        self.holding_monitor = holding_monitor
        self.instruction_provider = instruction_provider
        self.decision_event_detector = decision_event_detector
        self.candidate_generator = RuntimeCandidateGenerator()
        self.authority_enabled = bool(authority_enabled)
        self.authority = PersistentPrePidAuthority(enabled=authority_enabled)
        self._binding_fn = binding_fn
        self._activation_fn = activation_fn
        self._latest_episode: PolicyEpisodeInput | None = None
        self._latest_scene: LiveSceneObservation | None = None
        self._latest_front: Mapping[str, Any] = {"status": "UNAVAILABLE"}
        self._latest_frame: int | None = None
        self._latest_observation_id: str | None = None
        self._handled_instruction_sha256: set[str] = set()
        self._event: dict[str, Any] | None = None
        self._result: Stage6AOrchestrationResult | None = None
        self._candidates: tuple[RuntimeCandidate, RuntimeCandidate] | None = None
        self._plans: tuple[CandidatePlan, CandidatePlan] | None = None
        self._forward_results: dict[str, CandidateForwardResult] = {}
        self._consequence: ConsequenceEvaluation | None = None
        self._plan_selection_done = False
        self._selected_plan_source = "CURRENT_VALID_BASELINE_PLAN"
        self._pid_invocation_count = 0
        self._control_observation_count = 0
        self._tick_errors: list[Mapping[str, Any]] = []
        self._visualizer = visualizer
        self._visualization_tick_count = 0
        self._visualization_render_count = 0
        self._visualization_errors: list[Mapping[str, Any]] = []
        self._visualization_last_render_ms: float | None = None
        self._visualization_stride = max(
            1,
            int(os.environ.get(STAGE6A_VISUALIZATION_STRIDE_ENV, "2")),
        )
        self._baseline_visual_route: Any = ()
        self._baseline_visual_speed: Any = ()
        self._latest_control_values: Mapping[str, Any] | None = None
        if self._visualizer is None and _truthy(
            os.environ.get(STAGE6A_VISUALIZATION_ENV)
        ):
            from driveclarify_m3_runtime_shadow.live_visualization import (
                LiveShadowVisualizerV0,
            )

            self._visualizer = LiveShadowVisualizerV0(
                self.output_dir / "stage6a_live_panel.png",
                open_window=True,
                window_title="DriveClarify Stage 6A/6B Live Runtime",
            )
        stage_output_configured = bool(
            os.environ.get("DRIVECLARIFY_PAPER_MVP_STAGE6A_OUTPUT_DIR")
        )
        self._live_hook_configuration = {
            "probe_enabled": _truthy(
                os.environ.get("DRIVECLARIFY_PROBE_ENABLED")
            ),
            "shadow_gateway_enabled": _truthy(
                os.environ.get("DRIVECLARIFY_SHADOW_V0")
            ),
            "stage6a_selector_enabled": _truthy(
                os.environ.get(STAGE6A_LIVE_ENV)
            ),
            "probe_output_configured": bool(
                os.environ.get("DRIVECLARIFY_PROBE_OUTPUT")
            ),
            "stage6a_output_configured": stage_output_configured,
            "act_authority_enabled": self.authority_enabled,
            "paths_and_secrets_omitted": True,
        }
        self._live_hook_configuration["dispatch_prerequisites_satisfied"] = all(
            self._live_hook_configuration[key]
            for key in (
                "probe_enabled",
                "shadow_gateway_enabled",
                "stage6a_selector_enabled",
                "probe_output_configured",
                "stage6a_output_configured",
            )
        )
        self._authority_event_clock_origin: float | None = None

    @staticmethod
    def _control_values(control: Any) -> Mapping[str, Any]:
        values: dict[str, Any] = {}
        for name in (
            "steer",
            "throttle",
            "brake",
            "hand_brake",
            "reverse",
            "manual_gear_shift",
            "gear",
        ):
            try:
                if hasattr(control, name):
                    values[name] = getattr(control, name)
            except Exception:
                continue
        return values

    def _candidate_visual_rows(self) -> list[Mapping[str, Any]]:
        if self._candidates is None or self._plans is None:
            return []
        matrix: Mapping[str, Any] = {}
        evidence_by_id: dict[str, Any] = {}
        result = self._result
        if result is not None and result.m2b_result is not None:
            raw_matrix = result.m2b_result.counterfactual_matrix
            if isinstance(raw_matrix, Mapping):
                matrix = raw_matrix
            evidence_by_id = {
                str(item.candidate_id): item
                for item in result.m2b_result.counterfactual_evidence
            }
        own_cells: dict[str, Mapping[str, Any]] = {}
        for cell in matrix.get("cells", ()) if isinstance(matrix, Mapping) else ():
            if not isinstance(cell, Mapping):
                continue
            action = str(cell.get("action_candidate_id", ""))
            hypothesis = str(cell.get("hypothesis_candidate_id", ""))
            if action and action == hypothesis:
                own_cells[action] = cell
        rows: list[Mapping[str, Any]] = []
        for candidate, plan in zip(self._candidates, self._plans):
            evidence = evidence_by_id.get(candidate.candidate_id)
            route_evidence = getattr(evidence, "route", None)
            route_semantic = getattr(route_evidence, "mapped_branch", None)
            cell = own_cells.get(candidate.candidate_id, {})
            rows.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "interpretation_id": candidate.interpretation_id,
                    "route_semantic": route_semantic or "UNKNOWN",
                    "stop_status": (
                        cell.get("longitudinal_task_outcome") or "N/A"
                    ),
                    "route": plan.route,
                    "language": list(plan.language),
                    "source_frame_id": plan.source_frame_id,
                    "generation_latency_ms": round(
                        float(plan.latency_s) * 1000.0, 3
                    ),
                    "candidate_output_digest": plan.output_digest,
                }
            )
        return rows

    def _visualization_record(self) -> Mapping[str, Any]:
        episode = self._latest_episode
        result = self._result
        producer = None if result is None else result.m2b_result
        activation = None if result is None else result.activation
        activation_dict = (
            activation.to_dict() if activation is not None else {}
        )
        m3_payload = activation_dict.get("m3_result", {})
        if not isinstance(m3_payload, Mapping):
            m3_payload = {}
        final_state = m3_payload.get("final_state", {})
        if not isinstance(final_state, Mapping):
            final_state = {}
        candidates = self._candidate_visual_rows()
        matrix = (
            producer.counterfactual_matrix
            if producer is not None
            and isinstance(producer.counterfactual_matrix, Mapping)
            else {"cells": []}
        )
        event = self._event or {}
        selection = event.get("plan_selection", {})
        if not isinstance(selection, Mapping):
            selection = {}
        arm = event.get("authority_arm", {})
        if not isinstance(arm, Mapping):
            arm = {}
        action = None if producer is None else producer.producer_action
        authority_decision = selection.get("authority_decision", {})
        if not isinstance(authority_decision, Mapping):
            authority_decision = {}
        selected_id = (
            None if producer is None else producer.selected_candidate_id
        ) or arm.get("candidate_id") or authority_decision.get("candidate_id")
        selected_candidate = next(
            (
                item
                for item in candidates
                if item.get("candidate_id") == selected_id
            ),
            {},
        )
        current_state = (
            final_state.get("lifecycle_state")
            or m3_payload.get("lifecycle_state")
            or "NOT_EXECUTED"
        )
        authority_owner = (
            authority_decision.get("owner")
            or selection.get("authority_status")
            or final_state.get("authority")
            or "NONE"
        )
        limited_enabled = action == "ACT"
        limited = {
            "enabled": limited_enabled,
            "stage": event.get("status", "WAITING_FOR_DECISION_EVENT"),
            "frozen_m3": {
                "exact_state": current_state,
                "frozen_m3_authority": final_state.get("authority", "UNKNOWN"),
            },
            "execution_authority": authority_owner,
            "receipt": {
                "issued": bool(arm.get("receipt_id")),
                "receipt_id": arm.get("receipt_id"),
            },
            "candidate_identity": {
                "candidate_id": selected_id,
                "source_frame_id": selected_candidate.get("source_frame_id"),
                "resolved_interpretation_id": next(
                    (
                        item.interpretation_id
                        for item in self._candidates or ()
                        if item.candidate_id == selected_id
                    ),
                    authority_decision.get("resolved_interpretation_id"),
                ),
            },
            "candidate_vehicle_control": dict(self._latest_control_values or {}),
            "candidate_control_window_count": int(
                self._selected_plan_source == "AUTHORIZED_CANDIDATE_PLAN"
            ),
            "candidate_control_writes": 0,
            "pid_invocations_on_act_tick": self._pid_invocation_count,
            "authority_receipts_issued": int(bool(arm.get("receipt_id"))),
            "authority_receipts_consumed": int(
                selection.get("authority_status") == "AUTHORIZED_CANDIDATE_PLAN"
            ),
            "ownership_returned_to_baseline": False,
            "final_execution_authority_decisions": (
                [selection.get("authority_decision")]
                if isinstance(selection.get("authority_decision"), Mapping)
                else []
            ),
            "next_carla_frame": self._latest_frame,
            "actual_control_actuator_match": (
                "OBSERVED_EXISTING_PID_OUTPUT_FOR_AUTHORIZED_PLAN"
                if self._control_observation_count == 1
                and self._selected_plan_source == "AUTHORIZED_CANDIDATE_PLAN"
                else "NOT_OBSERVED_IN_PRE_PID_GATE"
            ),
        }
        reason_codes = [] if producer is None else list(producer.reason_codes)
        candidate_prompts = [
            candidate.prompt_text for candidate in self._candidates or ()
        ]
        return {
            "dashboard_title": (
                "STAGE 6A/6B LIVE | RGB -> 2 CANDIDATES -> M2B -> "
                "ACTIVATION/M3 -> ONE EXISTING PID"
            ),
            "source_identity": {"source_frame_id": self._latest_frame},
            "instruction": {
                "raw": (
                    episode.raw_instruction
                    if episode is not None
                    else "WAITING_FOR_VALID_RUNTIME_INPUT"
                ),
                "interpretation_a": (
                    candidate_prompts[0]
                    if len(candidate_prompts) > 0
                    else "PENDING_RUNTIME_CANDIDATE_A"
                ),
                "interpretation_b": (
                    candidate_prompts[1]
                    if len(candidate_prompts) > 1
                    else "PENDING_RUNTIME_CANDIDATE_B"
                ),
            },
            "candidates": candidates,
            "baseline": {"route": self._baseline_visual_route},
            "counterfactual_matrix": matrix,
            "m2b": {
                "producer_action": action or "NOT_EXECUTED",
                "reason_codes": reason_codes,
            },
            "m3": {
                "current_state": current_state,
                "authority": authority_owner,
                "control_writes": 0,
            },
            "physical_wait_v0": {
                "status": action or "INACTIVE",
                "m3_lifecycle_state": current_state,
                "m3_authority": authority_owner,
                "candidate_commit_status": selection.get(
                    "authority_status", "PENDING"
                ),
                "control_actuator_equality_count": 0,
                "control_equality_observed_count": 0,
                "wait_executor_tick_overhead_ms": {"max": 0},
                "lease_elapsed_s": 0,
                "lease_remaining_s": 0,
                "wait_entry_frame": "N/A",
                "current_frame": self._latest_frame,
                "distance_travelled_m": 0,
                "carla_ticks_during_wait": 0,
            },
            "ask_replanning_v0": {"enabled": False},
            "limited_act_commit_v0": limited,
            "performance": {
                "candidate_model_forward_count": (
                    0
                    if self._event is None
                    else self._event.get("candidate_model_forward_count", 0)
                ),
                "existing_pid_invocation_count": self._pid_invocation_count,
                "visualization_render_ms": self._visualization_last_render_ms,
            },
        }

    def _update_visualization_front(
        self, input_data: Any, expected_frame: Any
    ) -> None:
        if self._visualizer is None:
            return
        from driveclarify_m3_runtime_shadow.live_visualization import (
            copy_front_rgb_for_display,
        )

        display_copy, metadata = copy_front_rgb_for_display(
            input_data, expected_frame=expected_frame
        )
        setter = getattr(self._visualizer, "set_front_rgb", None)
        if callable(setter):
            setter(display_copy, metadata)

    def _render_visualization(self, *, force: bool = False) -> None:
        if self._visualizer is None:
            return
        if not force and self._visualization_tick_count % self._visualization_stride:
            return
        try:
            render_ms = float(
                self._visualizer.render(self._visualization_record())
            )
            self._visualization_last_render_ms = render_ms
            self._visualization_render_count += 1
        except Exception as exc:
            self._visualization_errors.append(
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "frame_id": self._latest_frame,
                }
            )

    def _event_is_current(self) -> bool:
        return bool(
            self._event is not None
            and self._latest_frame is not None
            and str(self._event.get("source_frame_id")) == str(self._latest_frame)
        )

    def _event_monotonic_time(self, absolute_monotonic_time: float) -> float:
        if self._authority_event_clock_origin is None:
            raise Stage6AContractError("STAGE6A_EVENT_CLOCK_NOT_INITIALIZED")
        elapsed = float(absolute_monotonic_time) - self._authority_event_clock_origin
        if not math.isfinite(elapsed) or elapsed < 0.0:
            raise Stage6AContractError("STAGE6A_EVENT_CLOCK_INVALID")
        return elapsed

    def _record_plan_selection(
        self,
        *,
        action: str | None,
        resolved_source: str,
        authority_status: str,
        receipt_id: str | None = None,
        receipt_digest: str | None = None,
        authority_decision: Mapping[str, Any] | None = None,
        blocker: str | None = None,
    ) -> None:
        if self._event is None:
            return
        self._event["plan_selection"] = {
            "m2b_action": action,
            "resolved_source": resolved_source,
            "authority_status": authority_status,
            "receipt_id": receipt_id,
            "receipt_digest": receipt_digest,
            "authority_adapter_identity": id(self.authority),
            "authority_decision": (
                None if authority_decision is None else dict(authority_decision)
            ),
            "blocker": blocker,
            "single_existing_pid_seam": True,
        }

    def _instruction(self, input_data: Any, tick_data: Any) -> tuple[str, str]:
        if self.instruction_provider is not None:
            value = self.instruction_provider(self.agent, input_data, tick_data)
            if type(value) is str and value.strip():
                return value, "INJECTED_RUNTIME_INSTRUCTION_PROVIDER"
            raise Stage6AContractError("RUNTIME_INSTRUCTION_PROVIDER_RETURNED_EMPTY")
        for attribute in ("user_command", "custom_prompt"):
            value = getattr(self.agent, attribute, None)
            if type(value) is str and value.strip():
                return value, "SIMLINGO_AGENT_" + attribute.upper()
        value = os.environ.get("DRIVECLARIFY_STAGE6A_RAW_INSTRUCTION")
        if type(value) is str and value.strip():
            return value, "EXPLICIT_RUNTIME_ENV_INSTRUCTION"
        raise Stage6AContractError("RAW_RUNTIME_INSTRUCTION_UNAVAILABLE")

    def _route_context(
        self, tick_data: Any, observed: float
    ) -> tuple[RouteContext, Mapping[str, Any]]:
        target = None
        if isinstance(tick_data, Mapping):
            target = _xy(tick_data.get("target_point"))
        if target is None:
            target = _xy(getattr(self.agent, "target_points", None))
        if target is None:
            raise Stage6AContractError("ONLINE_ROUTE_TARGET_POINT_UNAVAILABLE")
        commands = list(getattr(self.agent, "commands", ()) or ())
        command_value = commands[-2] if len(commands) >= 2 else commands[-1] if commands else None
        if hasattr(command_value, "value"):
            command_value = command_value.value
        if command_value is None:
            raise Stage6AContractError("ONLINE_ROUTE_COMMAND_UNAVAILABLE")
        command = "ROAD_OPTION_" + re.sub(r"[^A-Za-z0-9_-]", "_", str(command_value))
        front_items: list[Mapping[str, Any]] = []
        planner = getattr(self.agent, "_route_planner", None)
        route = getattr(planner, "route", None)
        if route is not None:
            try:
                for index, item in enumerate(route):
                    if index >= 5:
                        break
                    location = item[0]
                    front_items.append(
                        {
                            "x": float(location.x),
                            "y": float(location.y),
                            "command": str(item[1]),
                        }
                    )
            except Exception:
                front_items = []
        digest_payload = {
            "route_command": command,
            "target_point": target,
            "front_items": front_items,
        }
        return (
            RouteContext(
                observed_monotonic_time=observed,
                route_command=command,
                target_point_x_m=target[0],
                target_point_y_m=target[1],
                route_digest=canonical_sha256(digest_payload),
                source="ONLINE_SIMLINGO_ROUTE_CONTEXT_READ_ONLY",
            ),
            digest_payload,
        )

    def on_tick(
        self,
        input_data: Any,
        tick_data: Any,
        timestamp: Any,
        frame: Any,
        observation_id: Any,
    ) -> None:
        observed = time.monotonic()
        self._latest_episode = None
        self._latest_scene = None
        try:
            if frame is None or observation_id is None:
                raise Stage6AContractError("LIVE_FRAME_AND_OBSERVATION_ID_REQUIRED")
            frame_id = int(frame)
            front, _ = _front_rgb(input_data, frame_id)
            scene = self.scene_provider(
                self.agent, input_data, tick_data, frame_id, front
            )
            if not isinstance(scene, LiveSceneObservation):
                raise Stage6AContractError("LIVE_SCENE_PROVIDER_CONTRACT_REQUIRED")
            if scene.frame_id != frame_id:
                raise Stage6AContractError("LIVE_SCENE_FRAME_IDENTITY_MISMATCH")
            speed = _scalar(tick_data.get("speed")) if isinstance(tick_data, Mapping) else None
            position = (
                (scene.ego_position_x_m, scene.ego_position_y_m)
                if scene.ego_position_x_m is not None and scene.ego_position_y_m is not None
                else _xy(tick_data.get("gps")) if isinstance(tick_data, Mapping) else None
            )
            yaw = scene.ego_yaw_degrees
            if yaw is None and isinstance(tick_data, Mapping):
                compass = _scalar(tick_data.get("compass"))
                yaw = math.degrees(compass) if compass is not None else None
            if speed is None or position is None or yaw is None:
                raise Stage6AContractError("ONLINE_EGO_STATE_INCOMPLETE")
            instruction, instruction_source = self._instruction(input_data, tick_data)
            route_context, route_evidence = self._route_context(tick_data, observed)
            opaque = canonical_sha256(
                {
                    "observation_id": str(observation_id),
                    "frame_id": frame_id,
                    "image_sha256": front["image_sha256"],
                    "instruction_sha256": canonical_sha256(instruction),
                    "ego": {"position": position, "yaw": yaw, "speed": speed},
                    "route_context": route_evidence,
                }
            )
            episode = PolicyEpisodeInput(
                raw_instruction=instruction,
                vision_observation=VisionObservation(
                    observation_id=str(observation_id),
                    frame_id=frame_id,
                    captured_monotonic_time=observed,
                    image_sha256=str(front["image_sha256"]),
                    image_width=int(front["image_width"]),
                    image_height=int(front["image_height"]),
                    references=scene.visual_references,
                    source=(
                        "ONLINE_CARLA_ACTOR_CAMERA_PROJECTION_NOT_IMAGE_ONLY_DETECTOR"
                        if not scene.image_only_detector
                        else "ONLINE_IMAGE_DETECTOR_OUTPUT"
                    ),
                ),
                ego_state=EgoState(
                    observed_monotonic_time=observed,
                    position_x_m=position[0],
                    position_y_m=position[1],
                    yaw_degrees=yaw,
                    speed_mps=speed,
                    source="ONLINE_CARLA_EGO_OR_SIMLINGO_FILTERED_STATE",
                ),
                route_context=route_context,
                opaque_token=opaque,
            )
            self._latest_episode = episode
            self._latest_scene = scene
            self._latest_front = {
                **dict(front),
                "instruction_source": instruction_source,
                "projection_source_kind": scene.projection_source_kind,
                "image_only_detector": scene.image_only_detector,
                "privileged_simulation_state": scene.privileged_simulation_state,
                "scene_evidence": dict(scene.evidence),
            }
            self._latest_frame = frame_id
            self._latest_observation_id = str(observation_id)
        except Exception as exc:
            self._latest_front = {
                "status": "BLOCKED_INPUT_CAPTURE",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "frame_id": frame,
                "observation_id": observation_id,
            }
            self._tick_errors.append(dict(self._latest_front))
        self._visualization_tick_count += 1
        try:
            self._update_visualization_front(input_data, frame)
        except Exception as exc:
            self._visualization_errors.append(
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "frame_id": frame,
                    "stage": "FRONT_RGB_DISPLAY_COPY",
                }
            )
        self._render_visualization()
        self._persist()

    def _decision_event(self, episode: PolicyEpisodeInput) -> bool:
        instruction_id = canonical_sha256(episode.raw_instruction)
        if instruction_id in self._handled_instruction_sha256:
            return False
        if self.decision_event_detector is not None:
            return bool(self.decision_event_detector(episode))
        return len(episode.vision_observation.references) >= 2

    def _invalidate(self, reason: str, exc: Exception | None = None) -> None:
        if self._event is None:
            self._event = {}
        self._event["valid_episode"] = False
        self._event["valid_live_act_authority"] = False
        self._event["status"] = "INVALID_STAGE6A_EPISODE"
        self._event.setdefault("blockers", []).append(reason)
        if exc is not None:
            self._event.setdefault("errors", []).append(
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )

    def on_model_output(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        model_start: float,
        model_end: float,
    ) -> None:
        self._baseline_visual_route = _points(baseline_route) or ()
        self._baseline_visual_speed = _points(baseline_speed) or ()
        episode = self._latest_episode
        scene = self._latest_scene
        if episode is None or scene is None or not self._decision_event(episode):
            return None
        instruction_id = canonical_sha256(episode.raw_instruction)
        self._handled_instruction_sha256.add(instruction_id)
        self._plan_selection_done = False
        self._selected_plan_source = "CURRENT_VALID_BASELINE_PLAN"
        self._pid_invocation_count = 0
        self._control_observation_count = 0
        self._result = None
        self._candidates = None
        self._plans = None
        self._forward_results = {}
        self._consequence = None
        self._event = {
            "schema_version": STAGE6A_SCHEMA,
            "status": "RUNNING",
            "valid_episode": False,
            "valid_live_act_authority": False,
            "source_observation_id": episode.vision_observation.observation_id,
            "source_frame_id": episode.vision_observation.frame_id,
            "policy_input_sha256": episode.input_digest,
            "opaque_token_sha256": canonical_sha256(episode.opaque_token),
            "front_rgb": dict(self._latest_front),
            "runtime_vision_references": [
                asdict(reference)
                for reference in episode.vision_observation.references
            ],
            "actor_projection_disclosure": {
                "projection_source_kind": scene.projection_source_kind,
                "image_only_detector": scene.image_only_detector,
                "privileged_simulation_state": scene.privileged_simulation_state,
                "claim": (
                    "CARLA_ACTOR_CAMERA_PROJECTION_IS_NOT_AN_IMAGE_ONLY_DETECTOR"
                    if not scene.image_only_detector
                    else "IMAGE_DETECTOR_OUTPUT_NO_ACTOR_GROUND_TRUTH_CLAIM"
                ),
            },
            "execution_guard_disclosure": {
                "reported_guard_active": scene.independent_safety_guard_active,
                "execution_facts_guard_active": (
                    scene.independent_safety_guard_active
                    if scene.independent_safety_guard_active is not None
                    else True
                ),
                "interpretation": (
                    "KNOWN_GUARD_STATE_USED"
                    if scene.independent_safety_guard_active is not None
                    else "UNKNOWN_TREATED_AS_ACTIVE_FAIL_CLOSED_NOT_A_SAFETY_PASS"
                ),
                "control_ownership_evidence": _jsonable(
                    scene.evidence.get("control_ownership")
                ),
            },
            "monitor_disclosure": {
                "physical_safety_monitor": type(
                    self.physical_safety_monitor
                ).__name__,
                "hard_rule_monitor": type(self.hard_rule_monitor).__name__,
                "default_physical_status": "UNKNOWN_FAIL_CLOSED",
                "default_hard_rule_status": "UNKNOWN_FAIL_CLOSED",
                "consequence_mapping_does_not_infer_safety_or_rules": True,
            },
            "baseline_forward": {
                "model_start": float(model_start),
                "model_end": float(model_end),
                "route_object_id": id(baseline_route),
                "speed_object_id": id(baseline_speed),
                "route_sha256": canonical_sha256(_points(baseline_route)),
                "speed_sha256": canonical_sha256(_points(baseline_speed)),
                "used_as_candidate": False,
            },
            "candidate_model_forward_count": 0,
            "candidate_forward_attempt_count": 0,
            "candidate_forwards": [],
            "pid_invocation_count": 0,
            "control_write_count": 0,
            "blockers": [],
            "errors": [],
        }
        try:
            generated = self.candidate_generator.generate(episode)
            if generated.status != "READY" or len(generated.candidates) != 2:
                raise RuntimeError("STAGE6A_RUNTIME_CANDIDATE_GENERATION_UNKNOWN")
            candidates = (generated.candidates[0], generated.candidates[1])
            begin = getattr(self.forward_provider, "begin_event", None)
            if callable(begin):
                begin()
            forward_results: dict[str, CandidateForwardResult] = {}
            for candidate in candidates:
                self._event["candidate_forward_attempt_count"] += 1
                output = self.forward_provider(episode, candidate)
                if not isinstance(output, CandidateForwardResult):
                    raise Stage6AContractError("CANDIDATE_FORWARD_RESULT_REQUIRED")
                if output.plan.candidate_id != candidate.candidate_id:
                    raise Stage6AContractError("CANDIDATE_FORWARD_IDENTITY_MISMATCH")
                if output.raw_route is baseline_route or output.raw_speed is baseline_speed:
                    raise RuntimeError("BASELINE_PLAN_OBJECT_REUSE_AS_CANDIDATE_FORBIDDEN")
                evidence = dict(output.forward_evidence)
                if (
                    evidence.get("fresh_candidate_conditioned_model_execution")
                    is not True
                    or evidence.get("baseline_plan_used_as_candidate") is not False
                    or evidence.get("candidate_id") != candidate.candidate_id
                    or evidence.get("model_forward_count") != 1
                ):
                    raise Stage6AContractError(
                        "CANDIDATE_FORWARD_FRESH_EXECUTION_EVIDENCE_INVALID"
                    )
                if candidate.candidate_id in forward_results:
                    raise RuntimeError("DUPLICATE_DYNAMIC_CANDIDATE_FORWARD")
                forward_results[candidate.candidate_id] = output
                self._event["candidate_forwards"].append(evidence)
                self._event["candidate_model_forward_count"] += 1
            if len(forward_results) != 2 or sum(
                item.model_forward_count for item in forward_results.values()
            ) != 2:
                raise RuntimeError("EXACTLY_TWO_DYNAMIC_CANDIDATE_FORWARDS_REQUIRED")
            provider_count = getattr(self.forward_provider, "event_forward_count", 2)
            if int(provider_count) != 2:
                raise RuntimeError("CANDIDATE_FORWARD_PROVIDER_COUNT_MISMATCH")
            plans = tuple(forward_results[item.candidate_id].plan for item in candidates)
            if len({item.model_forward_sequence_id for item in plans}) != 2:
                raise RuntimeError("CANDIDATE_FORWARD_SEQUENCE_ID_NOT_UNIQUE")
            self._candidates = candidates
            self._plans = (plans[0], plans[1])
            self._forward_results = forward_results
            self._event["candidate_generation_audit"] = generated.audit.to_dict()
            self._event["runtime_candidates"] = [
                asdict(candidate) for candidate in candidates
            ]
            self._event["candidate_plans"] = [
                {
                    **asdict(plan),
                    "output_digest": plan.output_digest,
                }
                for plan in plans
            ]
            self._event["candidate_model_forward_count"] = 2

            def cached_plan_provider(
                runtime_episode: PolicyEpisodeInput,
                runtime_candidate: RuntimeCandidate,
            ) -> CandidatePlan:
                if runtime_episode.input_digest != episode.input_digest:
                    raise Stage6AContractError("CACHED_PLAN_EPISODE_IDENTITY_MISMATCH")
                return forward_results[runtime_candidate.candidate_id].plan

            def consequence_callback(runtime_episode, runtime_candidates, runtime_plans):
                value = build_runtime_consequence_evaluation(
                    runtime_episode,
                    runtime_candidates,
                    runtime_plans,
                    scene=scene,
                )
                self._consequence = value
                self._event["consequence_evaluation"] = _jsonable(value)
                return value

            self._authority_event_clock_origin = time.monotonic()
            decision_time = 0.0
            self._event["authority_time_domain"] = {
                "domain": "EVENT_RELATIVE_MONOTONIC_SECONDS",
                "issuance_time": decision_time,
                "absolute_clock_origin_omitted": True,
                "purpose": (
                    "PRESERVE_SUBSECOND_RECEIPT_AND_LEASE_INTERVAL_PRECISION"
                ),
            }
            safety_known = scene.independent_safety_guard_active
            issuance_facts = ExecutionBoundaryFacts(
                current_monotonic_time=decision_time,
                candidate_freshness="FRESH",
                candidate_invalidated=False,
                active_query=False,
                active_holding_lease=False,
                independent_safety_guard_active=(
                    safety_known if safety_known is not None else True
                ),
                baseline_available=True,
                simulation_runtime="CARLA",
            )
            kwargs: dict[str, Any] = {}
            if self._binding_fn is not None:
                kwargs["binding_fn"] = self._binding_fn
            if self._activation_fn is not None:
                kwargs["activation_fn"] = self._activation_fn
            orchestrator = Stage6AOrchestrator(
                plan_provider=cached_plan_provider,
                consequence_evaluator=consequence_callback,
                physical_safety_provider=self.physical_safety_monitor,
                hard_rule_provider=self.hard_rule_monitor,
                clarification_provider=self.clarification_monitor,
                holding_provider=self.holding_monitor,
                candidate_generator=self.candidate_generator,
                pre_pid_authority=self.authority,
                **kwargs,
            )
            result = orchestrator.run(
                episode,
                decision_monotonic_time=decision_time,
                issuance_facts=issuance_facts,
            )
            self._result = result
            self._record_monitor_evidence()
            self._event["orchestration"] = result.to_audit_dict()
            self._event["activation_count"] = result.activation_v1_invocation_count
            self._event["persistent_authority_identity"] = id(self.authority)
            self._event["authority_arm"] = (
                None if result.authority_arm is None else result.authority_arm.to_dict()
            )
            if result.activation_v1_invocation_count != 1:
                blocker = (
                    "ACTIVATION_V1_NOT_REACHED:"
                    + result.status
                )
                self._event["blockers"].append(blocker)
                self._event["status"] = "BLOCKED_FAIL_CLOSED"
                self._event["valid_episode"] = False
            else:
                self._event["status"] = "DECISION_READY_PRE_PID"
                self._event["valid_episode"] = True
                if result.m2b_result is not None and result.m2b_result.producer_action == "ASK":
                    self._event["query"] = {
                        "query_id": result.m2b_result.producer_query_id,
                        "candidate_ids": list(result.m2b_result.candidate_ids),
                        "status": "RECORDED_BY_STAGE6A_BINDING",
                        "baseline_plan_preserved": True,
                    }
        except Exception as exc:
            self._invalidate("STAGE6A_ON_MODEL_OUTPUT_EXCEPTION", exc)
            self._record_monitor_evidence()
        self._render_visualization(force=True)
        self._persist()
        return None

    def _execution_facts(self, current_monotonic: float) -> ExecutionBoundaryFacts:
        scene = self._latest_scene
        result = self._result
        source_frame = (
            result.m2b_result.source_frame_id
            if result is not None and result.m2b_result is not None
            else None
        )
        invalidated = bool(
            self._latest_frame is None
            or source_frame is None
            or str(self._latest_frame) != str(source_frame)
        )
        safety = (
            scene.independent_safety_guard_active
            if scene is not None
            else None
        )
        return ExecutionBoundaryFacts(
            current_monotonic_time=float(current_monotonic),
            candidate_freshness="STALE" if invalidated else "FRESH",
            candidate_invalidated=invalidated,
            active_query=bool(
                result is not None
                and result.m2b_result is not None
                and result.m2b_result.producer_action == "ASK"
            ),
            active_holding_lease=bool(
                result is not None
                and result.m2b_result is not None
                and result.m2b_result.producer_action == "WAIT"
            ),
            independent_safety_guard_active=(safety if safety is not None else True),
            baseline_available=True,
            simulation_runtime="CARLA",
        )

    def _fresh_monitor_values(
        self, current_monotonic: float
    ) -> tuple[Mapping[str, Any] | None, HardRuleEvidence | None]:
        if (
            self._latest_episode is None
            or self._candidates is None
            or self._plans is None
            or self._consequence is None
        ):
            return None, None
        physical = self.physical_safety_monitor(
            self._latest_episode,
            self._candidates,
            self._plans,
            self._consequence,
            current_monotonic,
        )
        rule = self.hard_rule_monitor(
            self._latest_episode,
            self._candidates,
            self._plans,
            self._consequence,
            current_monotonic,
        )
        self._record_monitor_evidence()
        return physical, rule

    def _record_monitor_evidence(self) -> None:
        if self._event is None:
            return
        physical = getattr(self.physical_safety_monitor, "last_evidence", None)
        rule = getattr(self.hard_rule_monitor, "last_evidence", None)
        if physical is not None:
            self._event["physical_safety_monitor_evidence"] = _jsonable(physical)
        if rule is not None:
            self._event["hard_rule_monitor_evidence"] = _jsonable(rule)

    def _wait_authority(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        current_monotonic: float,
        facts: ExecutionBoundaryFacts,
    ) -> Mapping[str, Any]:
        result = self._result
        if result is None or result.activation is None or result.m2b_result is None:
            return {"owner": "BASELINE_CONTROL", "authorized": False, "reason_code": "WAIT_RESULT_MISSING"}
        route_points = _points(baseline_route)
        speed_points = _points(baseline_speed)
        if route_points is None or speed_points is None:
            return {"owner": "BASELINE_CONTROL", "authorized": False, "reason_code": "WAIT_BASELINE_PLAN_UNHASHABLE"}
        context = CandidateExecutionContext(
            current_candidate_id=result.m2b_result.candidate_ids[0],
            current_candidate_set_id=result.m2b_result.candidate_set_id,
            current_resolved_interpretation_id="WAIT_UNRESOLVED_RUNTIME",
            current_source_observation_id=result.m2b_result.source_observation_id,
            current_source_frame_id=str(result.m2b_result.source_frame_id),
            current_route_digest=m3_canonical_sha256(route_points),
            current_speed_digest=evaluate_pid_desired_speed_v0(speed_points).source_digest,
            candidate_freshness=facts.candidate_freshness,
            candidate_invalidated=facts.candidate_invalidated,
            active_query=True,
            active_holding_lease=True,
            independent_safety_guard_active=facts.independent_safety_guard_active,
            baseline_available=True,
            simulation_runtime="CARLA",
            current_monotonic_time=float(current_monotonic),
        )
        resolver = FinalExecutionAuthorityResolverV0(
            CandidateLiveActAuthorityResolverV0(enabled=False)
        )
        final = resolver.resolve(
            existing_m3_result=result.activation.m3_result,
            candidate_authority_receipt=None,
            current_execution_context=context,
        )
        return final.to_dict()

    def select_plan_source(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        current_monotonic: float,
    ) -> tuple[Any, Any]:
        if self._plan_selection_done:
            return baseline_route, baseline_speed
        self._plan_selection_done = True
        result = self._result
        if (
            self._event is None
            or result is None
            or result.m2b_result is None
            or result.activation is None
        ):
            self._selected_plan_source = "CURRENT_VALID_BASELINE_PLAN"
            self._record_plan_selection(
                action=None,
                resolved_source=self._selected_plan_source,
                authority_status="NO_STAGE6A_DECISION_BASELINE_PRESERVED",
                blocker="STAGE6A_DECISION_OR_ACTIVATION_UNAVAILABLE",
            )
            self._render_visualization(force=True)
            self._persist()
            return baseline_route, baseline_speed
        action = result.m2b_result.producer_action
        try:
            authority_time = self._event_monotonic_time(
                float(current_monotonic)
            )
            facts = self._execution_facts(authority_time)
            if (
                action == "ACT"
                and result.authority_arm is not None
                and result.authority_arm.armed
            ):
                selected_id = result.authority_arm.candidate_id
                forward = self._forward_results.get(str(selected_id))
                if forward is None:
                    raise RuntimeError("AUTHORIZED_CANDIDATE_RAW_PLAN_MISSING")
                physical, rule = self._fresh_monitor_values(authority_time)
                selection = self.authority.resolve_pre_pid(
                    candidate_set_id=result.m2b_result.candidate_set_id,
                    current_plan=forward.plan,
                    execution_facts=facts,
                    physical_safety_signal=physical,
                    hard_rule_evidence=rule,
                )
                selection_dict = selection.to_dict()
                self._event["pre_pid_authority"] = selection_dict
                if selection.plan_source == "AUTHORIZED_CANDIDATE_PLAN":
                    if (
                        _points(forward.raw_route) != forward.plan.route
                        or _points(forward.raw_speed) != forward.plan.speed
                    ):
                        raise RuntimeError("AUTHORIZED_RAW_PLAN_DIGEST_CHANGED")
                    self._selected_plan_source = "AUTHORIZED_CANDIDATE_PLAN"
                    self._event["selected_plan_source"] = self._selected_plan_source
                    self._event["selected_candidate_id"] = forward.plan.candidate_id
                    self._event["valid_live_act_authority"] = True
                    self._record_plan_selection(
                        action=action,
                        resolved_source="candidate",
                        authority_status="AUTHORIZED_CANDIDATE_PLAN",
                        receipt_id=selection.receipt_id,
                        receipt_digest=result.authority_arm.receipt_digest,
                        authority_decision=selection.final_authority,
                    )
                    self._render_visualization(force=True)
                    self._persist()
                    return forward.raw_route, forward.raw_speed
                self._record_plan_selection(
                    action=action,
                    resolved_source="baseline",
                    authority_status="ACT_FAIL_CLOSED_BASELINE_PRESERVED",
                    receipt_id=selection.receipt_id,
                    receipt_digest=result.authority_arm.receipt_digest,
                    authority_decision=selection.final_authority,
                    blocker=selection.fail_closed_reason,
                )
                self._event["valid_episode"] = False
                self._event["status"] = "BLOCKED_ACT_AUTHORITY_FAIL_CLOSED"
                self._event["blockers"].append(
                    selection.fail_closed_reason or "ACT_AUTHORITY_NOT_GRANTED"
                )
            elif action == "ACT":
                reason = (
                    "ACT_AUTHORITY_NOT_ARMED"
                    if result.authority_arm is None
                    else result.authority_arm.reason_code
                )
                self._record_plan_selection(
                    action=action,
                    resolved_source="baseline",
                    authority_status="ACT_FAIL_CLOSED_BASELINE_PRESERVED",
                    receipt_id=(
                        None
                        if result.authority_arm is None
                        else result.authority_arm.receipt_id
                    ),
                    receipt_digest=(
                        None
                        if result.authority_arm is None
                        else result.authority_arm.receipt_digest
                    ),
                    blocker=reason,
                )
                self._event["valid_episode"] = False
                self._event["status"] = "BLOCKED_ACT_AUTHORITY_FAIL_CLOSED"
                self._event["blockers"].append(reason)
            elif action == "WAIT":
                final = self._wait_authority(
                    baseline_route,
                    baseline_speed,
                    authority_time,
                    facts,
                )
                self._event["pre_pid_wait_authority"] = dict(final)
                if final.get("owner") == FinalExecutionAuthority.M3_HOLDING_CONTROL.value:
                    self._selected_plan_source = (
                        "EXISTING_BASELINE_PLAN_UNDER_M3_HOLDING_AUTHORITY"
                    )
                    self._event["selected_plan_source"] = self._selected_plan_source
                    self._record_plan_selection(
                        action=action,
                        resolved_source="existing_pid_holding_plan",
                        authority_status="M3_HOLDING_CONTROL_AUTHORIZED",
                        authority_decision=final,
                    )
                    self._render_visualization(force=True)
                    self._persist()
                    return baseline_route, baseline_speed
                self._record_plan_selection(
                    action=action,
                    resolved_source="baseline",
                    authority_status="WAIT_FAIL_CLOSED_BASELINE_PRESERVED",
                    authority_decision=final,
                    blocker=str(final.get("reason_code", "WAIT_AUTHORITY_UNKNOWN")),
                )
                self._event["valid_episode"] = False
                self._event["status"] = "BLOCKED_WAIT_AUTHORITY_FAIL_CLOSED"
                self._event["blockers"].append(
                    str(final.get("reason_code", "WAIT_AUTHORITY_UNKNOWN"))
                )
            elif action == "ASK":
                self._selected_plan_source = "CURRENT_VALID_BASELINE_PLAN_DURING_QUERY"
                self._event["selected_plan_source"] = self._selected_plan_source
                self._record_plan_selection(
                    action=action,
                    resolved_source="baseline",
                    authority_status="QUERY_RECORDED_BASELINE_PRESERVED",
                )
                self._render_visualization(force=True)
                self._persist()
                return baseline_route, baseline_speed
            self._selected_plan_source = "CURRENT_VALID_BASELINE_PLAN"
            self._event["selected_plan_source"] = self._selected_plan_source
            if "plan_selection" not in self._event:
                self._record_plan_selection(
                    action=action,
                    resolved_source="baseline",
                    authority_status="FAIL_CLOSED_BASELINE_PRESERVED",
                    blocker="NO_EXECUTION_AUTHORITY",
                )
        except Exception as exc:
            self._invalidate("STAGE6A_PRE_PID_EXCEPTION", exc)
            self._selected_plan_source = "CURRENT_VALID_BASELINE_PLAN"
            self._event["selected_plan_source"] = self._selected_plan_source
            self._record_plan_selection(
                action=action,
                resolved_source="baseline",
                authority_status="PRE_PID_EXCEPTION_BASELINE_PRESERVED",
                blocker=str(exc),
            )
        self._render_visualization(force=True)
        self._persist()
        return baseline_route, baseline_speed

    def on_pid_invocation(self, current_monotonic: float) -> None:
        if not self._plan_selection_done or not self._event_is_current():
            return
        self._pid_invocation_count += 1
        if self._event is not None:
            self._event["pid_invocation_count"] = self._pid_invocation_count
            self._event["pid"] = {
                "owner": "EXISTING_SIMLINGO_PID",
                "instance_count": 1,
                "invocation_observed_at": float(current_monotonic),
                "selected_plan_source": self._selected_plan_source,
                "new_pid_instance_count": 0,
            }
            if self._pid_invocation_count != 1:
                self._invalidate("PID_INVOCATION_COUNT_NOT_ONE")
        self._render_visualization(force=True)
        self._persist()

    def on_control(self, control: Any, gt_velocity: Any, current_monotonic: float) -> None:
        if not self._plan_selection_done or not self._event_is_current():
            return
        self._control_observation_count += 1
        self._latest_control_values = self._control_values(control)
        if self._event is not None:
            self._event["control"] = {
                "observed_at": float(current_monotonic),
                "selected_plan_source": self._selected_plan_source,
                "control_object_id": id(control),
                "candidate_control_write_count": 0,
                "m3_control_write_count": 0,
                "existing_pid_output_observation_count": self._control_observation_count,
            }
        self._render_visualization(force=True)
        self._persist()

    def commit(self) -> None:
        self._persist()

    def close(self) -> None:
        if self._visualizer is not None:
            try:
                self._visualizer.close()
            except Exception as exc:
                self._visualization_errors.append(
                    {
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "stage": "VISUALIZER_CLOSE",
                    }
                )
        self._persist()

    def _persist(self) -> None:
        _atomic_json(self.output_dir / STAGE6A_AUDIT_FILENAME, dict(self.summary()))

    def summary(self) -> Mapping[str, Any]:
        valid = bool(self._event is not None and self._event.get("valid_episode") is True)
        arm = None if self._event is None else self._event.get("authority_arm")
        selection = None if self._event is None else self._event.get("plan_selection")
        return {
            "schema_version": STAGE6A_SCHEMA,
            "enabled": True,
            "run_id": self.run_id,
            "status": (
                self._event.get("status")
                if self._event is not None
                else "WAITING_FOR_VALID_RUNTIME_INPUT"
            ),
            "valid_episode_count": 1 if valid else 0,
            "valid_live_act_authority_count": (
                1
                if self._event is not None
                and self._event.get("valid_live_act_authority") is True
                else 0
            ),
            "event": self._event,
            "latest_front_rgb": dict(self._latest_front),
            "tick_capture_errors": list(self._tick_errors),
            "candidate_model_forward_count": (
                0 if self._event is None else self._event.get("candidate_model_forward_count", 0)
            ),
            "pid_invocation_count": self._pid_invocation_count,
            "control_observation_count": self._control_observation_count,
            "candidate_control_write_count": 0,
            "m3_control_write_count": 0,
            "new_pid_instance_count": 0,
            "persistent_pre_pid_authority_identity": id(self.authority),
            "authority": {
                "authority_adapter_identity": id(self.authority),
                "persistence_verified": bool(
                    self._event is not None
                    and self._event.get("persistent_authority_identity")
                    == id(self.authority)
                    and (
                        selection is None
                        or selection.get("authority_adapter_identity")
                        == id(self.authority)
                    )
                ),
                "arm": arm,
                "plan_selection": selection,
            },
            "live_hook_configuration": dict(self._live_hook_configuration),
            "pending_candidate_set_ids": list(self.authority.pending_candidate_set_ids),
            "default_physical_safety_monitor": type(
                self.physical_safety_monitor
            ).__name__,
            "default_hard_rule_monitor": type(self.hard_rule_monitor).__name__,
            "physical_safety_default": "UNKNOWN_FAIL_CLOSED",
            "hard_rule_default": "UNKNOWN_FAIL_CLOSED",
            "actor_projection_disclosure": (
                None
                if self._latest_scene is None
                else {
                    "projection_source_kind": self._latest_scene.projection_source_kind,
                    "image_only_detector": self._latest_scene.image_only_detector,
                    "privileged_simulation_state": (
                        self._latest_scene.privileged_simulation_state
                    ),
                }
            ),
            "visualization": {
                "enabled": self._visualizer is not None,
                "native_window_requested": self._visualizer is not None,
                "layout": "FRONT_CAMERA_FIRST_EXISTING_LIVE_SHADOW_VISUALIZER_V0",
                "render_count": self._visualization_render_count,
                "tick_count": self._visualization_tick_count,
                "stride": self._visualization_stride,
                "last_render_ms": self._visualization_last_render_ms,
                "errors": list(self._visualization_errors),
                "probe_induced_model_forward_count": 0,
                "probe_induced_pid_invocation_count": 0,
                "probe_induced_planner_step_count": 0,
                "vehicle_control_mutation_count": 0,
                "labels": [
                    "RESEARCH DEBUG VIEW",
                    "SIMULATION ONLY",
                    "NO FORMAL SAFETY GUARANTEE",
                ],
                "visualizer_summary": (
                    dict(self._visualizer.summary())
                    if self._visualizer is not None
                    else None
                ),
            },
        }


def build_stage6a_simlingo_binding(
    agent: Any,
    output_dir: str | os.PathLike[str],
    *,
    run_id: str,
) -> Stage6ASimLingoBinding:
    authority_enabled = _truthy(os.environ.get(STAGE6A_AUTHORITY_ENV))
    return Stage6ASimLingoBinding(
        agent,
        output_dir,
        run_id=run_id,
        physical_safety_monitor=(
            CarlaOneTickPhysicalSafetyMonitor()
            if authority_enabled
            else None
        ),
        hard_rule_monitor=(
            CarlaOneTickHardRuleMonitor() if authority_enabled else None
        ),
        authority_enabled=authority_enabled,
    )


__all__ = [
    "CandidateForwardResult",
    "CarlaOneTickHardRuleMonitor",
    "CarlaOneTickPhysicalSafetyMonitor",
    "FailClosedHardRuleMonitor",
    "FailClosedPhysicalSafetyMonitor",
    "LiveSceneObservation",
    "STAGE6A_AUDIT_FILENAME",
    "STAGE6A_AUTHORITY_ENV",
    "STAGE6A_LIVE_ENV",
    "STAGE6A_VISUALIZATION_ENV",
    "STAGE6A_VISUALIZATION_STRIDE_ENV",
    "STAGE6A_SCHEMA",
    "SimLingoCandidateForwardProvider",
    "Stage6ASimLingoBinding",
    "build_runtime_consequence_evaluation",
    "build_stage6a_simlingo_binding",
    "read_live_carla_actor_projection",
]
