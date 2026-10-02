"""Same-observation, language-only SimLingo candidate-forward diagnostic.

The runtime captures one genuine CARLA/SimLingo observation and then calls the
existing isolated candidate-forward adapter.  Every non-language field is
hashed immediately before each forward.  Candidate outputs are never returned
to the PID or vehicle control path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_language_grounding_v1.runtime import _EpisodeView, _EgoView, _VisionView
from driveclarify_paper_mvp_runtime.contracts import RuntimeCandidate, canonical_sha256
from driveclarify_paper_mvp_runtime.simlingo_binding import (
    SimLingoCandidateForwardProvider,
    _points,
    _scalar,
)
from driveclarify_grounded_language_v1_extension_e1_r1.runtime import _live_map
from driveclarify_grounded_language_v1_extension_e1_r1.topology import RuntimeMapTopologyEnumerator


FEATURE_FLAG = "DRIVECLARIFY_SIMLINGO_DISTINCT_LOCAL_CANDIDATE_DIAGNOSTIC"
RECEIPT_FILENAME = "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
WINDOW_TITLE = "DriveClarify Grounded V1 Live Demo"
PROMPT_A = "At the upcoming junction, turn right."
PROMPT_B = "Continue straight through the upcoming junction."


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _front(input_data: Any) -> tuple[int, Any]:
    import numpy as np

    entry = input_data["rgb_0"]
    frame = int(entry[0]) if isinstance(entry, (tuple, list)) else -1
    image = entry[1] if isinstance(entry, (tuple, list)) else entry
    value = np.asarray(image)
    if value.ndim != 3 or value.shape[2] < 3:
        raise ValueError("LOCAL_CANDIDATE_RGB_0_INVALID")
    return frame, np.ascontiguousarray(value[:, :, :3]).copy()


def _python_value(value: Any) -> Any:
    current = value
    for operation_name in ("detach", "cpu"):
        operation = getattr(current, operation_name, None)
        if callable(operation):
            current = operation()
    tolist = getattr(current, "tolist", None)
    if callable(tolist):
        return tolist()
    if isinstance(current, Mapping):
        return {str(key): _python_value(item) for key, item in current.items()}
    if isinstance(current, (list, tuple)):
        return [_python_value(item) for item in current]
    if isinstance(current, (str, int, float, bool)) or current is None:
        return current
    return repr(current)


def _tensor_receipt(value: Any) -> Mapping[str, Any]:
    current = value
    for operation_name in ("detach", "cpu", "contiguous"):
        operation = getattr(current, operation_name, None)
        if callable(operation):
            current = operation()
    raw = None
    numpy = getattr(current, "numpy", None)
    if callable(numpy):
        try:
            raw = numpy().tobytes(order="C")
        except Exception:
            raw = None
    python = _python_value(current)
    shape = list(getattr(current, "shape", ()) or ())
    element_count = math.prod(shape) if shape else 1
    receipt = {
        "shape": shape,
        "dtype": str(getattr(current, "dtype", type(current).__name__)),
        "bytes_sha256": (
            hashlib.sha256(raw).hexdigest()
            if isinstance(raw, bytes)
            else canonical_sha256(python)
        ),
    }
    # Navigation scalars/vectors remain human-auditable.  Large camera and
    # calibration tensors are bound by exact dtype/shape/bytes hashes without
    # duplicating hundreds of megabytes into every receipt.
    if element_count <= 256:
        receipt["value"] = python
    return receipt


def _rmse(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> float:
    values = [
        (float(a) - float(b)) ** 2
        for lrow, rrow in zip(left, right)
        for a, b in zip(lrow, rrow)
    ]
    return math.sqrt(sum(values) / len(values)) if values else 0.0


def _route_metrics(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> Mapping[str, Any]:
    distances = [
        math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))
        for a, b in zip(left, right)
    ]
    return {
        "route_rmse_m": _rmse(left, right),
        "max_geometric_separation_m": max(distances) if distances else 0.0,
        "fraction_waypoints_separated_ge_1m": (
            sum(value >= 1.0 for value in distances) / len(distances)
            if distances
            else 0.0
        ),
        "per_waypoint_separation_m": distances,
    }


def _arc(route: Sequence[Sequence[float]]) -> float:
    return sum(
        math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
        for a, b in zip(route, route[1:])
    )


def _extent(route: Sequence[Sequence[float]]) -> Mapping[str, Any]:
    xs = [float(row[0]) for row in route]
    ys = [float(row[1]) for row in route]
    return {
        "waypoint_count": len(route),
        "route_arc_length_m": _arc(route),
        "forward_extent_m": (max(xs) - min(xs)) if xs else 0.0,
        "lateral_extent_m": (max(ys) - min(ys)) if ys else 0.0,
        "endpoint": list(route[-1]) if route else None,
    }


def _candidate(
    prompt: str, label: str, observation_id: str, frame_id: int, image_sha: str
) -> RuntimeCandidate:
    semantic = canonical_sha256({"explicit_local_maneuver": label, "prompt": prompt})
    return RuntimeCandidate(
        candidate_id="local-candidate-" + label.casefold(),
        interpretation_id="explicit-local-" + label.casefold(),
        prompt_text=prompt,
        visual_track_id="same-real-rgb-0",
        visual_anchor_digest=image_sha,
        candidate_semantic_digest=semantic,
        candidate_input_digest=canonical_sha256(
            {
                "observation_id": observation_id,
                "frame_id": frame_id,
                "image_sha256": image_sha,
                "prompt": prompt,
            }
        ),
        source_observation_id=observation_id,
        source_frame_id=frame_id,
        generator_rule="explicit-local-maneuver-diagnostic-v1",
    )


class LocalCandidateDiagnosticRuntime:
    enabled = True
    prompt_a = PROMPT_A
    prompt_b = PROMPT_B
    forward_provider_class = SimLingoCandidateForwardProvider

    def __init__(self, agent: Any, output_dir: str) -> None:
        self.agent = agent
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.forward_provider = self.forward_provider_class(agent)
        self.topology_enumerator = RuntimeMapTopologyEnumerator()
        self.repetitions = int(os.environ.get("DRIVECLARIFY_LOCAL_CANDIDATE_REPETITIONS", "1"))
        if self.repetitions not in {1, 3}:
            raise RuntimeError("LOCAL_CANDIDATE_REPETITIONS_MUST_BE_1_OR_3")
        self.visualization = _truthy(os.environ.get("DRIVECLARIFY_GROUNDED_LANGUAGE_V1_VISUALIZATION"))
        self._frame: int | None = None
        self._observation_id: str | None = None
        self._image = None
        self._image_sha: str | None = None
        self._speed = 0.0
        self._gps = None
        self._simulation_time = 0.0
        self._done = False
        self._closed = False
        self._pid_count = 0
        self._control_count = 0
        self._dashboard_count = 0
        self._receipt: dict[str, Any] = {
            "schema_version": "driveclarify.simlingo_distinct_local_candidate.runtime.v1",
            "status": "WAITING_FOR_SAME_REAL_OBSERVATION",
            "enabled": True,
            "feature_flag": FEATURE_FLAG,
            "train_only": True,
            "formal_e3": False,
            "performance_evaluation": False,
            "initial_decision": "ACT",
            "candidate_control_executed": False,
            "candidate_plan_returned_to_pid": False,
            "candidate_direct_vehicle_control_write_count": 0,
            "m3_direct_vehicle_control_write_count": 0,
            "new_pid_count": 0,
            "grounding_dino_forward_count": 0,
            "visualization_induced_detector_forward_count": 0,
            "visualization_induced_simlingo_forward_count": 0,
            "visualization_induced_planner_advance_count": 0,
            "visualization_induced_pid_count": 0,
            "visualization_induced_vehicle_control_mutation_count": 0,
            "candidate_a_instruction": self.prompt_a,
            "candidate_b_instruction": self.prompt_b,
            "repeat_count_per_candidate": self.repetitions,
            "dev_attempt_count": 0,
            "test_attempt_count": 0,
            "test_consumed": False,
            "errors": [],
        }
        self._persist()

    def _persist(self) -> None:
        self._receipt.update(
            {
                "existing_pid_invocation_count": self._pid_count,
                "control_observation_count": self._control_count,
                "dashboard_refresh_count": self._dashboard_count,
                "dashboard_native_refresh_count": self._dashboard_count,
                "carla_tick_count": int(self._receipt.get("carla_tick_count", 0)),
            }
        )
        _atomic_json(self.output_dir / RECEIPT_FILENAME, self._receipt)

    def on_tick(self, input_data: Any, tick_data: Any, timestamp: Any, frame: Any, observation_id: Any) -> None:
        try:
            sensor_frame, image = _front(input_data)
            if sensor_frame != int(frame):
                raise RuntimeError("LOCAL_CANDIDATE_SENSOR_FRAME_MISMATCH")
            self._receipt["carla_tick_count"] = int(self._receipt.get("carla_tick_count", 0)) + 1
            if self._frame is None:
                self._frame = int(frame)
                self._observation_id = str(observation_id)
                self._image = image
                self._image_sha = hashlib.sha256(image.tobytes(order="C")).hexdigest()
                self._speed = float(_scalar(tick_data.get("speed")) or 0.0)
                self._gps = _python_value(tick_data.get("gps"))
                self._simulation_time = float(timestamp)
                try:
                    import cv2

                    cv2.imwrite(str(self.output_dir / "same_observation_rgb_0.png"), image)
                except Exception:
                    pass
                route = getattr(getattr(self.agent, "_route_planner", None), "route", None)
                route_rows = self.topology_enumerator._route_rows(route)
                opportunities = self.topology_enumerator.enumerate(route, _live_map())
                self._receipt.update(
                    {
                        "status": "SAME_REAL_OBSERVATION_CAPTURED",
                        "same_observation": {
                            "frame_id": self._frame,
                            "source_observation_id": self._observation_id,
                            "rgb_0_sha256": self._image_sha,
                            "ego_speed_mps": self._speed,
                            "ego_gps": self._gps,
                            "simulation_time": self._simulation_time,
                        },
                        "rgb_identities": [self._image_sha],
                        "topology_context": {
                            "route_polyline_world": [[x, y] for x, y, _ in route_rows[:80]],
                            "right_turn_opportunities": [item.to_dict() for item in opportunities],
                            "extra_planner_advance_count": 0,
                        },
                    }
                )
            if self.visualization and self._done:
                self._render()
            self._persist()
        except Exception as exc:
            self._receipt["errors"].append(
                {"stage": "ON_TICK", "type": type(exc).__name__, "message": str(exc)}
            )
            self._receipt["status"] = "BLOCKED_LOCAL_CANDIDATE_RUNTIME"
            self._done = True
            self._persist()

    def _input_audit(self) -> Mapping[str, Any]:
        driving = dict(getattr(self.agent, "DrivingInput", {}) or {})
        fields = {name: _tensor_receipt(value) for name, value in driving.items()}
        non_language = {
            name: receipt
            for name, receipt in fields.items()
            if name not in {"prompt", "prompt_inference"}
        }
        route = getattr(getattr(self.agent, "_route_planner", None), "route", None)
        route_rows = self.topology_enumerator._route_rows(route)
        nav = {
            "target_point": fields.get("target_point"),
            "target_points_agent": _python_value(getattr(self.agent, "target_points", None)),
            "eval_route_as": str(getattr(getattr(self.agent, "config", None), "eval_route_as", "UNKNOWN")),
            "route_deque_world": [[x, y, z] for x, y, z in route_rows[:80]],
        }
        return {
            "driving_input_fields": fields,
            "non_language_input_sha256": canonical_sha256(non_language),
            "navigation_context": nav,
            "navigation_context_sha256": canonical_sha256(nav),
        }

    def _forward(self, candidate: RuntimeCandidate, label: str) -> tuple[Any, Mapping[str, Any], Mapping[str, Any]]:
        before = self._input_audit()
        episode = _EpisodeView(
            ego_state=_EgoView(self._speed),
            vision_observation=_VisionView(str(self._observation_id), int(self._frame)),
        )
        self.forward_provider.begin_event()
        result = self.forward_provider(episode, candidate)
        after = self._input_audit()
        route = list(_points(result.raw_route) or ())
        speed = list(_points(result.raw_speed) or ())
        row = {
            "candidate_id": label,
            "source_observation_id": result.plan.source_observation_id,
            "source_frame_id": result.plan.source_frame_id,
            "route": route,
            "speed": speed,
            "route_sha256": canonical_sha256(route),
            "speed_sha256": canonical_sha256(speed),
            "forward_evidence": dict(result.forward_evidence),
            "forwarded_prompt_text": result.forward_evidence.get("forwarded_prompt_text"),
            "forwarded_prompt_sha256": result.forward_evidence.get("forwarded_prompt_sha256"),
            "prediction_horizon": _extent(route),
        }
        return row, before, after

    def on_model_output(self, baseline_route: Any, baseline_speed: Any, model_start: float, model_end: float) -> None:
        del baseline_route, baseline_speed, model_start, model_end
        if self._done or self._frame is None:
            return
        try:
            assert self._image_sha is not None and self._observation_id is not None
            candidate_a = _candidate(self.prompt_a, "A", self._observation_id, self._frame, self._image_sha)
            candidate_b = _candidate(self.prompt_b, "B", self._observation_id, self._frame, self._image_sha)
            rows: list[Mapping[str, Any]] = []
            input_rows: list[Mapping[str, Any]] = []
            for repetition in range(1, self.repetitions + 1):
                for label, candidate in (("A", candidate_a), ("B", candidate_b)):
                    row, before, after = self._forward(candidate, label + str(repetition))
                    rows.append(row)
                    input_rows.append(
                        {
                            "candidate_id": label + str(repetition),
                            "before": before,
                            "after": after,
                            "input_restored_after_forward": before == after,
                        }
                    )
            a_rows = [row for row in rows if str(row["candidate_id"]).startswith("A")]
            b_rows = [row for row in rows if str(row["candidate_id"]).startswith("B")]
            pair = _route_metrics(a_rows[0]["route"], b_rows[0]["route"])
            pair_speed = _rmse(a_rows[0]["speed"], b_rows[0]["speed"])
            nav_hashes = [row["before"]["navigation_context_sha256"] for row in input_rows]
            non_language_hashes = [row["before"]["non_language_input_sha256"] for row in input_rows]
            self._receipt.update(
                {
                    "status": "MECHANISM_DIAGNOSTIC_CAPTURE_READY",
                    "candidate_plan_repetitions": rows,
                    "candidate_simlingo_forward_count": len(rows),
                    "normal_simlingo_forward_count": 1,
                    "input_conditioning_rows": input_rows,
                    "same_rgb_state_navigation_context": len(set(nav_hashes)) == 1 and len(set(non_language_hashes)) == 1,
                    "navigation_context_hash_a": nav_hashes[0],
                    "navigation_context_hash_b": nav_hashes[1],
                    "non_language_input_hash_a": non_language_hashes[0],
                    "non_language_input_hash_b": non_language_hashes[1],
                    "route_divergence": dict(pair),
                    "speed_divergence_rmse": pair_speed,
                    "simlingo_effective_local_prediction_horizon": a_rows[0]["prediction_horizon"],
                    "candidate_a_horizon": a_rows[0]["prediction_horizon"],
                    "candidate_b_horizon": b_rows[0]["prediction_horizon"],
                    "candidate_trajectory_source": "REAL_SIMLINGO_PRED_ROUTE",
                }
            )
            self._done = True
            if self.visualization:
                self._render()
            self._persist()
        except Exception as exc:
            self._receipt["errors"].append(
                {"stage": "ON_MODEL_OUTPUT", "type": type(exc).__name__, "message": str(exc)}
            )
            self._receipt["status"] = "BLOCKED_LOCAL_CANDIDATE_RUNTIME"
            self._done = True
            self._persist()

    @staticmethod
    def _world_local(route_world: Sequence[Sequence[float]], point: Sequence[float]) -> tuple[float, float] | None:
        if len(route_world) < 2:
            return None
        origin = route_world[0]
        direction = next(
            ((row[0] - origin[0], row[1] - origin[1]) for row in route_world[1:] if math.hypot(row[0] - origin[0], row[1] - origin[1]) > 0.25),
            None,
        )
        if direction is None:
            return None
        norm = math.hypot(*direction)
        fx, fy = direction[0] / norm, direction[1] / norm
        dx, dy = float(point[0]) - origin[0], float(point[1]) - origin[1]
        return dx * fx + dy * fy, dx * fy - dy * fx

    def _render(self) -> None:
        import cv2
        import numpy as np

        rows = list(self._receipt.get("candidate_plan_repetitions") or ())
        if len(rows) < 2 or self._image is None:
            return
        a = next(row for row in rows if str(row["candidate_id"]).startswith("A"))
        b = next(row for row in rows if str(row["candidate_id"]).startswith("B"))
        route_a, route_b = a["route"], b["route"]
        canvas = np.full((1080, 1920, 3), (18, 23, 30), dtype=np.uint8)
        text_color, muted = (235, 239, 244), (148, 165, 183)
        color_a, color_b, green = (245, 176, 65), (80, 190, 255), (92, 214, 126)

        def put(text: str, x: int, y: int, color=text_color, scale=0.48, thickness=1) -> None:
            cv2.putText(canvas, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)

        cv2.rectangle(canvas, (0, 0), (1919, 128), (28, 36, 47), -1)
        put("SIMLINGO DISTINCT LOCAL CANDIDATE MECHANISM", 24, 38, (155, 205, 255), 0.72, 2)
        put("SAME REAL OBSERVATION + SAME NAVIGATION CONTEXT + FROZEN CHECKPOINT", 24, 78, text_color, 0.51, 2)
        put("A: TURN RIGHT", 1100, 42, color_a, 0.61, 2)
        put("B: GO STRAIGHT", 1100, 82, color_b, 0.61, 2)
        put("TRAIN-ONLY / DIAGNOSTIC-ONLY / NO CANDIDATE CONTROL", 24, 112, muted, 0.38, 1)

        image = np.asarray(self._image)[:, :, :3]
        scale = min(820 / image.shape[1], 445 / image.shape[0])
        resized = cv2.resize(image, (int(image.shape[1] * scale), int(image.shape[0] * scale)))
        cv2.rectangle(canvas, (18, 145), (870, 640), (37, 47, 60), 2)
        put("REAL RGB_0", 35, 177, (155, 205, 255), 0.52, 2)
        ix, iy = 34 + (820 - resized.shape[1]) // 2, 187 + (435 - resized.shape[0]) // 2
        canvas[iy : iy + resized.shape[0], ix : ix + resized.shape[1]] = resized

        cv2.rectangle(canvas, (890, 145), (1902, 640), (37, 47, 60), 2)
        put("EXACT SIMLINGO PROMPTS / INPUT IDENTITY", 910, 177, (155, 205, 255), 0.52, 2)
        y = 220
        for text, color in (
            ("Candidate A", color_a),
            (a.get("forwarded_prompt_text", self.prompt_a), text_color),
            ("Candidate B", color_b),
            (b.get("forwarded_prompt_text", self.prompt_b), text_color),
            ("Same observation: YES", green),
            ("Navigation context same: " + ("YES" if self._receipt.get("same_rgb_state_navigation_context") else "NO"), green if self._receipt.get("same_rgb_state_navigation_context") else (80, 80, 255)),
            ("A/B prompt different: YES", green),
        ):
            for chunk_start in range(0, len(str(text)), 86):
                put(str(text)[chunk_start : chunk_start + 86], 915, y, color, 0.39, 1 if color == text_color else 2)
                y += 25
            y += 9

        bev = (18, 660, 1320, 1055)
        cv2.rectangle(canvas, (bev[0], bev[1]), (bev[2], bev[3]), (37, 47, 60), 2)
        put("BEV: solid = REAL SIMLINGO pred_route; dashed = CARLA TOPOLOGY GUIDE", 35, 693, (155, 205, 255), 0.47, 2)
        px0, py0, px1, py1 = 55, 715, 1285, 1018
        cv2.rectangle(canvas, (px0, py0), (px1, py1), (23, 29, 37), -1)
        route_world = list((self._receipt.get("topology_context") or {}).get("route_polyline_world") or ())
        opportunities = list((self._receipt.get("topology_context") or {}).get("right_turn_opportunities") or ())
        guide = [self._world_local(route_world, row) for row in route_world]
        guide = [row for row in guide if row is not None]
        all_points = [tuple(row) for row in route_a + route_b] + guide
        max_forward = max([float(row[0]) for row in all_points] + [20.0])
        lateral = max([abs(float(row[1])) for row in all_points] + [7.0]) * 1.15

        def pixel(row: Sequence[float]) -> tuple[int, int]:
            return (
                int(round((px0 + px1) / 2 + float(row[1]) / lateral * (px1 - px0) * 0.45)),
                int(round(py1 - max(0.0, float(row[0])) / max_forward * (py1 - py0 - 6))),
            )

        guide_pixels = [pixel(row) for row in guide[:50]]
        for index, (left, right) in enumerate(zip(guide_pixels, guide_pixels[1:])):
            if index % 2 == 0:
                cv2.line(canvas, left, right, muted, 2, cv2.LINE_AA)
        if opportunities:
            first = opportunities[0]
            junction_index = int(first.get("route_opportunity_index") or 0)
            if 0 <= junction_index < len(guide):
                jp = pixel(guide[junction_index])
                anchor = self._world_local(route_world, first.get("anchor_xy") or ())
                if anchor is not None:
                    bp = pixel(anchor)
                    for index in range(10):
                        alpha0, alpha1 = index / 10.0, min(1.0, (index + 0.5) / 10.0)
                        p0 = (int(jp[0] + (bp[0] - jp[0]) * alpha0), int(jp[1] + (bp[1] - jp[1]) * alpha0))
                        p1 = (int(jp[0] + (bp[0] - jp[0]) * alpha1), int(jp[1] + (bp[1] - jp[1]) * alpha1))
                        cv2.line(canvas, p0, p1, muted, 2, cv2.LINE_AA)
                    cv2.circle(canvas, jp, 7, text_color, -1)
                    put("JUNCTION", jp[0] + 10, jp[1] - 7, text_color, 0.36, 1)
        projected = []
        for route, color, label in ((route_a, color_a, "A RIGHT"), (route_b, color_b, "B STRAIGHT")):
            pixels = [pixel(row) for row in route]
            projected.append(pixels)
            if len(pixels) >= 2:
                cv2.polylines(canvas, [np.asarray(pixels, dtype=np.int32)], False, color, 6, cv2.LINE_AA)
                put(label, pixels[-1][0] + 8, pixels[-1][1], color, 0.43, 2)
        ego = pixel((0.0, 0.0))
        cv2.drawMarker(canvas, ego, green, cv2.MARKER_TRIANGLE_UP, 22, 3)
        put("EGO", ego[0] - 18, ego[1] - 17, green, 0.38, 2)
        separations_px = [math.hypot(x[0] - y[0], x[1] - y[1]) for x, y in zip(projected[0], projected[1])]
        visual = {
            "max_pixel_separation": max(separations_px) if separations_px else 0.0,
            "sustained_points_ge_16px": sum(value >= 16.0 for value in separations_px),
            "common_projection_no_candidate_specific_transform": True,
            "real_simlingo_pred_route_only": True,
        }
        self._receipt["visual_bev"] = visual

        cv2.rectangle(canvas, (1340, 660), (1902, 1055), (37, 47, 60), 2)
        put("DIAGNOSTIC", 1360, 693, (155, 205, 255), 0.52, 2)
        metrics = self._receipt.get("route_divergence") or {}
        horizon = self._receipt.get("simlingo_effective_local_prediction_horizon") or {}
        first = opportunities[0] if opportunities else {}
        lines = [
            "Same observation: YES",
            "Navigation context same: " + ("YES" if self._receipt.get("same_rgb_state_navigation_context") else "NO"),
            "A repeat stability: PENDING" if self.repetitions == 1 else "A repeat stability: AUDITED",
            "B repeat stability: PENDING" if self.repetitions == 1 else "B repeat stability: AUDITED",
            "Route RMSE: {:.3f} m".format(float(metrics.get("route_rmse_m") or 0.0)),
            "Max separation: {:.3f} m".format(float(metrics.get("max_geometric_separation_m") or 0.0)),
            "Speed RMSE: {:.3f}".format(float(self._receipt.get("speed_divergence_rmse") or 0.0)),
            "Horizon arc: {:.3f} m".format(float(horizon.get("route_arc_length_m") or 0.0)),
            "Junction distance: {} m".format(first.get("distance_or_progress", "UNKNOWN")),
            "Topology-level maneuver divergence: AUDIT",
            "Visualization extra forward/PID/control: 0/0/0",
        ]
        y = 735
        for line in lines:
            put(line, 1360, y, text_color if "same: NO" not in line else (80, 80, 255), 0.40, 1)
            y += 28
        panel = self.output_dir / "DISTINCT_LOCAL_CANDIDATE_PANEL.png"
        cv2.imwrite(str(panel), canvas)
        cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_TITLE, 1240, 700)
        cv2.moveWindow(WINDOW_TITLE, 0, 40)
        cv2.imshow(WINDOW_TITLE, canvas)
        cv2.waitKey(50)
        self._dashboard_count += 1

    def select_plan_source(self, baseline_route: Any, baseline_speed: Any, current_monotonic: float) -> tuple[Any, Any]:
        del current_monotonic
        return baseline_route, baseline_speed

    def on_pid_invocation(self, current_monotonic: float) -> None:
        del current_monotonic
        self._pid_count += 1

    def on_control(self, control: Any, gt_velocity: Any, current_monotonic: float) -> None:
        del control, gt_velocity, current_monotonic
        self._control_count += 1
        self._persist()

    def commit(self) -> None:
        self._persist()

    def close(self) -> None:
        if self.visualization:
            try:
                import cv2

                cv2.destroyWindow(WINDOW_TITLE)
                cv2.waitKey(1)
            except Exception:
                pass
        self._closed = True
        self._receipt["closed"] = True
        self._persist()

    def summary(self) -> Mapping[str, Any]:
        return {
            "enabled": True,
            "status": self._receipt.get("status"),
            "decision": "DIAGNOSTIC_ONLY",
            "candidate_forward_count": self._receipt.get("candidate_simlingo_forward_count", 0),
            "new_pid": 0,
        }


__all__ = ["LocalCandidateDiagnosticRuntime", "PROMPT_A", "PROMPT_B"]
