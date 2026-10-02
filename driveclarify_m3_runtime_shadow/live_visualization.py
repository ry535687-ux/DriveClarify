"""Front-camera-first local research dashboard for live shadow / WAIT V0."""

from __future__ import annotations

import copy
import hashlib
import os
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np


PANEL_TITLE = "DriveClarify ASK / Physical WAIT Pilot V0"
FRONT_VIEW_LABEL = "MODEL INPUT - FRONT RGB"
FRONT_COPY_LABEL = "DISPLAY COPY OF MODEL RGB_0"


def _text(value: Any, default: str = "UNKNOWN") -> str:
    if value is None:
        return default
    result = str(value)
    return result if result else default


def _short(value: Any, limit: int = 88) -> str:
    result = _text(value)
    return result if len(result) <= limit else result[: limit - 3] + "..."


def _points(value: Any) -> list[tuple[float, float]]:
    current = value
    while (
        isinstance(current, (list, tuple))
        and len(current) == 1
        and isinstance(current[0], (list, tuple))
    ):
        current = current[0]
    result: list[tuple[float, float]] = []
    if not isinstance(current, (list, tuple)):
        return result
    for point in current:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return []
        try:
            result.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            return []
    return result


def copy_front_rgb_for_display(
    input_data: Any,
    *,
    expected_frame: Any = None,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Copy the exact SimLingo ``input_data['rgb_0']`` without mutating it.

    Leaderboard exposes this sensor as CARLA BGRA/BGR.  OpenCV also displays
    BGR, so only a copy and (when present) alpha-channel drop are needed.
    """

    metadata: dict[str, Any] = {
        "status": "UNAVAILABLE",
        "source": 'input_data["rgb_0"]',
        "view_label": FRONT_VIEW_LABEL,
        "copy_label": FRONT_COPY_LABEL,
        "used_by_baseline_model": True,
        "display_only": True,
        "written_back_to_model_input": False,
        "display_color_space": "BGR",
        "display_transform": "NONE",
    }
    try:
        entry = input_data["rgb_0"]
        sensor_frame = int(entry[0]) if isinstance(entry, (tuple, list)) else None
        raw = entry[1] if isinstance(entry, (tuple, list)) else entry
        array = np.asarray(raw)
        if array.ndim != 3 or array.shape[2] < 3:
            metadata["reason"] = "RGB_0_SHAPE_INVALID"
            return None, metadata
        contiguous = np.ascontiguousarray(array)
        digest_before = hashlib.sha256(contiguous.tobytes(order="C")).hexdigest()
        display = np.ascontiguousarray(array[:, :, :3], dtype=np.uint8).copy()
        digest_after = hashlib.sha256(
            np.ascontiguousarray(np.asarray(raw)).tobytes(order="C")
        ).hexdigest()
        metadata.update(
            {
                "status": "AVAILABLE",
                "sensor_frame": sensor_frame,
                "expected_baseline_frame": expected_frame,
                "frame_identity_matches_baseline": (
                    expected_frame is None or sensor_frame == int(expected_frame)
                ),
                "source_shape": list(array.shape),
                "display_shape": list(display.shape),
                "source_dtype": str(array.dtype),
                "source_sha256_before_copy": digest_before,
                "source_sha256_after_copy": digest_after,
                "model_input_unchanged": digest_before == digest_after,
                "display_transform": (
                    "BGRA_TO_BGR_ON_DISPLAY_COPY_ALPHA_DROPPED"
                    if array.shape[2] >= 4
                    else "BGR_DISPLAY_COPY_NO_COLOR_CONVERSION"
                ),
                "bgr_rgb_conversion": "NONE_OPEN_CV_DISPLAY_EXPECTS_BGR",
            }
        )
        return display, metadata
    except Exception as exc:
        metadata["reason"] = type(exc).__name__ + ":" + str(exc)
        return None, metadata


class LiveShadowVisualizerV0:
    """Render a front-camera-dominant panel without invoking driving compute."""

    def __init__(
        self,
        output_path: str | os.PathLike[str],
        *,
        open_window: bool = True,
        window_title: str = PANEL_TITLE,
    ) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.open_window = bool(open_window)
        self.window_title = window_title
        self.render_count = 0
        self.last_render_ms: float | None = None
        self.last_error: str | None = None
        self._window_created = False
        self._front_rgb: np.ndarray | None = None
        self._front_metadata: dict[str, Any] = {"status": "UNAVAILABLE"}
        self._stage_screenshots: dict[str, str] = {}

    def set_front_rgb(
        self,
        display_copy_bgr: np.ndarray | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._front_rgb = (
            None
            if display_copy_bgr is None
            else np.ascontiguousarray(display_copy_bgr).copy()
        )
        self._front_metadata = dict(metadata or {"status": "UNAVAILABLE"})

    @staticmethod
    def _put(
        canvas: np.ndarray,
        text: Any,
        x: int,
        y: int,
        *,
        scale: float = 0.43,
        color: tuple[int, int, int] = (225, 230, 235),
        thickness: int = 1,
        limit: int = 88,
    ) -> None:
        cv2.putText(
            canvas,
            _short(text, limit),
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            color,
            thickness,
            cv2.LINE_AA,
        )

    @staticmethod
    def _cell_color(status: str) -> tuple[int, int, int]:
        status = status.upper()
        if status == "PASS":
            return (55, 145, 70)
        if status == "FAIL":
            return (65, 65, 195)
        return (55, 125, 185)

    def _draw_front(self, canvas: np.ndarray, record: Mapping[str, Any]) -> None:
        x0, y0, x1, y1 = 18, 88, 1118, 682
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (105, 112, 120), 2)
        if self._front_rgb is None:
            cv2.rectangle(canvas, (x0 + 2, y0 + 2), (x1 - 2, y1 - 2), (42, 45, 49), -1)
            self._put(canvas, "FRONT RGB UNAVAILABLE", x0 + 390, y0 + 290, scale=0.8, color=(90, 180, 240), thickness=2)
        else:
            source = self._front_rgb
            available_w, available_h = x1 - x0 - 4, y1 - y0 - 4
            ratio = min(available_w / source.shape[1], available_h / source.shape[0])
            width = max(1, int(round(source.shape[1] * ratio)))
            height = max(1, int(round(source.shape[0] * ratio)))
            resized = cv2.resize(source, (width, height), interpolation=cv2.INTER_AREA)
            left = x0 + 2 + (available_w - width) // 2
            top = y0 + 2 + (available_h - height) // 2
            canvas[top : top + height, left : left + width] = resized

        overlay = canvas.copy()
        cv2.rectangle(overlay, (x0 + 10, y0 + 10), (x0 + 500, y0 + 72), (10, 13, 16), -1)
        cv2.rectangle(overlay, (x0 + 10, y1 - 62), (x1 - 10, y1 - 10), (10, 13, 16), -1)
        cv2.addWeighted(overlay, 0.72, canvas, 0.28, 0.0, canvas)
        self._put(canvas, FRONT_VIEW_LABEL, x0 + 22, y0 + 36, scale=0.65, color=(255, 255, 255), thickness=2)
        self._put(canvas, FRONT_COPY_LABEL, x0 + 22, y0 + 61, scale=0.42, color=(90, 235, 245), thickness=1)
        source = record.get("source_identity", {})
        frame = self._front_metadata.get("sensor_frame", source.get("source_frame_id"))
        instruction = record.get("instruction", {})
        self._put(canvas, f"Frame {frame} | Raw instruction: {_text(instruction.get('raw'))}", x0 + 22, y1 - 29, scale=0.46, color=(245, 245, 245), thickness=2, limit=118)

    def _draw_reasoning(self, canvas: np.ndarray, record: Mapping[str, Any]) -> None:
        x0, y0, x1, y1 = 1135, 88, 1582, 680
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (75, 82, 91), 1)
        self._put(canvas, "DRIVECLARIFY REASONING", x0 + 12, y0 + 27, scale=0.56, thickness=2)
        instruction = record.get("instruction", {})
        self._put(canvas, "INSTRUCTION", x0 + 12, y0 + 58, color=(145, 190, 235), thickness=2)
        self._put(canvas, "Raw: " + _text(instruction.get("raw")), x0 + 18, y0 + 82, limit=54)
        self._put(canvas, "A: " + _text(instruction.get("interpretation_a")), x0 + 18, y0 + 105, color=(40, 220, 235), limit=54)
        self._put(canvas, "B: " + _text(instruction.get("interpretation_b")), x0 + 18, y0 + 128, color=(220, 90, 220), limit=54)

        candidates = record.get("candidates", ())
        if not isinstance(candidates, (list, tuple)):
            candidates = ()
        semantic_audit = record.get("candidate_semantics")
        if isinstance(semantic_audit, Mapping):
            self._put(
                canvas,
                "CANDIDATE SEMANTICS | RAW K={} EFFECTIVE K={}".format(
                    _text(semantic_audit.get("raw_k"), "0"),
                    _text(semantic_audit.get("effective_k"), "0"),
                ),
                x0 + 12,
                y0 + 166,
                color=(145, 190, 235),
                thickness=2,
                limit=55,
            )
            canonical = semantic_audit.get("canonical_interpretations", ())
            if not isinstance(canonical, (list, tuple)):
                canonical = ()
            if not canonical:
                self._put(canvas, "No effective semantic candidates", x0 + 18, y0 + 190)
            for index, candidate in enumerate(canonical[:2]):
                color = (40, 220, 235) if index == 0 else (220, 90, 220)
                y = y0 + 190 + index * 43
                label = "A" if index == 0 else "B"
                self._put(
                    canvas,
                    "{} Referent={} Trigger={}".format(
                        label,
                        _text(candidate.get("referent_identity") or candidate.get("referent")),
                        _text(candidate.get("temporal_trigger")),
                    ),
                    x0 + 18,
                    y,
                    color=color,
                    thickness=2,
                    limit=55,
                )
                self._put(
                    canvas,
                    "Target={} Maneuver={}".format(
                        _text(candidate.get("target_branch") or candidate.get("target_landmark")),
                        _text(candidate.get("maneuver")),
                    ),
                    x0 + 26,
                    y + 19,
                    scale=0.36,
                    limit=60,
                )
            self._put(
                canvas,
                "SemDup={} GroundDup={} ActionDiv={}".format(
                    _text(semantic_audit.get("semantic_duplicate"), "False"),
                    _text(semantic_audit.get("grounding_duplicate"), "False"),
                    _text(semantic_audit.get("action_divergence")),
                ),
                x0 + 18,
                y0 + 280,
                scale=0.36,
                color=(90, 235, 245),
                limit=62,
            )
            self._put(
                canvas,
                "TrajectoryDiv={} ConsequenceDiv={}".format(
                    _text(semantic_audit.get("trajectory_divergence")),
                    _text(semantic_audit.get("consequence_divergence")),
                ),
                x0 + 18,
                y0 + 300,
                scale=0.36,
                color=(90, 235, 245),
                limit=62,
            )
        else:
            self._put(canvas, "CANDIDATES", x0 + 12, y0 + 166, color=(145, 190, 235), thickness=2)
            if not candidates:
                self._put(canvas, "No candidate data", x0 + 18, y0 + 190)
            for index, candidate in enumerate(candidates[:2]):
                color = (40, 220, 235) if index == 0 else (220, 90, 220)
                y = y0 + 190 + index * 55
                self._put(canvas, f"{_text(candidate.get('candidate_id'))}: {_text(candidate.get('route_semantic'))}", x0 + 18, y, color=color, thickness=2, limit=52)
                self._put(canvas, f"STOP={_text(candidate.get('stop_status'))} | control write=0", x0 + 26, y + 22, scale=0.39, limit=54)

        m2b = record.get("m2b", {})
        m3 = record.get("m3", {})
        wait = record.get("physical_wait_v0", {})
        ask = record.get("ask_replanning_v0", {})
        limited = record.get("limited_act_commit_v0", {})
        if isinstance(limited, Mapping) and limited.get("enabled"):
            candidate = limited.get("candidate_identity") or {}
            frozen = limited.get("frozen_m3") or {}
            receipt = limited.get("receipt") or {}
            decisions = limited.get("final_execution_authority_decisions") or []
            execution_owner = limited.get("execution_authority") or (
                decisions[-1].get("owner")
                if decisions
                else "CANDIDATE_RECEIPT_GRANTED"
                if receipt.get("issued")
                else "BASELINE_CONTROL"
            )
            control = limited.get("candidate_vehicle_control") or {}
            self._put(canvas, "LIMITED ACT COMMIT V0", x0 + 12, y0 + 322, color=(80, 235, 250), thickness=2)
            self._put(canvas, "M2B: {} | Stage: {}".format(_text(m2b.get("producer_action")), _text(limited.get("stage"))), x0 + 18, y0 + 349, color=(40, 220, 245), thickness=2, limit=58)
            self._put(canvas, "M3: " + _text(frozen.get("exact_state")), x0 + 18, y0 + 374, limit=58)
            self._put(canvas, "Frozen M3 Authority: " + _text(frozen.get("frozen_m3_authority")), x0 + 18, y0 + 398, limit=58)
            self._put(canvas, "Execution Authority: " + _text(execution_owner), x0 + 18, y0 + 422, color=(90, 235, 245), thickness=2, limit=58)
            self._put(canvas, "Receipt: " + _text(receipt.get("receipt_id")), x0 + 18, y0 + 446, scale=0.36, limit=60)
            self._put(canvas, "Candidate: " + _text(candidate.get("candidate_id")), x0 + 18, y0 + 469, limit=58)
            self._put(canvas, "Source frame: " + _text(candidate.get("source_frame_id")), x0 + 18, y0 + 492, limit=58)
            self._put(canvas, "Resolved interpretation: " + _text(candidate.get("resolved_interpretation_id")), x0 + 18, y0 + 515, limit=58)
            self._put(canvas, f"Control window: {_text(limited.get('candidate_control_window_count'), '0')} / 1", x0 + 18, y0 + 538, limit=58)
            self._put(canvas, f"PID: {_text(limited.get('pid_invocations_on_act_tick'), '0')} invocation", x0 + 18, y0 + 561, limit=58)
            self._put(canvas, "Actual control: s={} t={} b={}".format(_text(control.get("steer")), _text(control.get("throttle")), _text(control.get("brake"))), x0 + 18, y0 + 584, scale=0.36, limit=60)
            return
        if isinstance(ask, Mapping) and ask.get("enabled"):
            query = ask.get("query", {})
            oracle = ask.get("oracle", {})
            invalidation = ask.get("old_candidate_invalidation", {})
            latest = ask.get("latest_observation", {})
            lifecycle = ask.get("m3_lifecycle", {})
            replan = ask.get("replan", {})
            self._put(canvas, "ASK LIFECYCLE", x0 + 12, y0 + 322, color=(145, 190, 235), thickness=2)
            self._put(canvas, "ASK SENT | " + _text(ask.get("stage")), x0 + 18, y0 + 348, color=(40, 220, 245), thickness=2, limit=55)
            self._put(canvas, "Question: " + _text(query.get("question")), x0 + 18, y0 + 372, scale=0.37, limit=60)
            self._put(canvas, "Query ID: " + _text(query.get("query_id")), x0 + 18, y0 + 394, scale=0.37, limit=60)
            answer_text = "PENDING" if oracle.get("answer_status") is None else _text(oracle.get("answer_text"))
            self._put(canvas, "ORACLE ANSWER: " + answer_text, x0 + 18, y0 + 418, color=(90, 235, 245), thickness=2, limit=58)
            self._put(canvas, "Selected interpretation: " + _text(oracle.get("selected_interpretation_id")), x0 + 18, y0 + 440, scale=0.39, limit=58)
            self._put(canvas, "WAIT: HOLD_CURRENT_VALID_PLAN", x0 + 18, y0 + 465, color=(40, 220, 245), thickness=2)
            self._put(canvas, "Old candidates: " + _text(invalidation.get("old_candidate_status")), x0 + 18, y0 + 489, scale=0.38, limit=58)
            self._put(canvas, f"Old/answer/latest frame: {_text(latest.get('old_frame_id'))}/{_text(latest.get('answer_frame_id'))}/{_text(latest.get('new_frame_id'))}", x0 + 18, y0 + 511, scale=0.37, limit=60)
            inference = "COMPLETE" if replan.get("fresh_model_computation") else "RUNNING" if ask.get("stage") == "REPLANNING_FROM_LATEST_OBSERVATION" else "PENDING"
            self._put(canvas, "Fresh model inference: " + inference, x0 + 18, y0 + 533, scale=0.38, limit=58)
            self._put(canvas, "Post-answer plan: " + _text(replan.get("plan_id")), x0 + 18, y0 + 555, scale=0.36, limit=60)
            self._put(canvas, "Exact M3: " + _text(lifecycle.get("exact_state")) + " | " + _text(lifecycle.get("authority")), x0 + 18, y0 + 577, scale=0.37, color=(90, 235, 245), limit=60)
            return
        self._put(canvas, "M2B / M3", x0 + 12, y0 + 322, color=(145, 190, 235), thickness=2)
        self._put(canvas, "M2B natural: " + _text(m2b.get("producer_action")), x0 + 18, y0 + 347, limit=55)
        self._put(canvas, "Natural M3: " + _text(m3.get("current_state")), x0 + 18, y0 + 370, limit=55)
        self._put(canvas, "Pilot M3: " + _text(wait.get("m3_lifecycle_state")), x0 + 18, y0 + 393, color=(90, 235, 245), thickness=2, limit=55)
        self._put(canvas, "Authority: " + _text(wait.get("m3_authority")), x0 + 18, y0 + 416, limit=55)

        self._put(canvas, "WAIT LIFECYCLE", x0 + 12, y0 + 454, color=(145, 190, 235), thickness=2)
        active_text = "WAIT - HOLD_CURRENT_VALID_PLAN" if wait.get("status") == "ACTIVE" else "WAIT " + _text(wait.get("status"))
        active_color = (40, 220, 245) if wait.get("status") == "ACTIVE" else (170, 190, 210)
        self._put(canvas, active_text, x0 + 18, y0 + 481, scale=0.49, color=active_color, thickness=2, limit=54)
        self._put(canvas, f"Lease elapsed={_text(wait.get('lease_elapsed_s'))}s remaining={_text(wait.get('lease_remaining_s'))}s", x0 + 18, y0 + 505, scale=0.39, limit=58)
        self._put(canvas, f"Start/current frame={_text(wait.get('wait_entry_frame'))}/{_text(wait.get('current_frame'))}", x0 + 18, y0 + 527, scale=0.39)
        self._put(canvas, f"Distance={_text(wait.get('distance_travelled_m'))}m | ticks={_text(wait.get('carla_ticks_during_wait'))}", x0 + 18, y0 + 549, scale=0.39)
        self._put(canvas, "QUERY = INACTIVE | SOURCE = PHYSICAL_HOLDING_PILOT", x0 + 18, y0 + 571, scale=0.37, color=(90, 235, 245), limit=62)

    def _draw_routes(
        self,
        canvas: np.ndarray,
        baseline_route: Any,
        candidates: Sequence[Mapping[str, Any]],
    ) -> None:
        x0, y0, x1, y1 = 18, 706, 625, 982
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (75, 82, 91), 1)
        self._put(canvas, "AUXILIARY EGO-LOCAL BEV | X=forward Y=right metre", x0 + 10, y0 + 24, scale=0.43, thickness=2)
        origin = (int((x0 + x1) / 2), y1 - 28)
        cv2.circle(canvas, origin, 6, (235, 235, 235), -1)
        self._put(canvas, "EGO", origin[0] + 10, origin[1] + 4, scale=0.36)
        series: list[tuple[str, Any, tuple[int, int, int]]] = [
            ("BASELINE", baseline_route, (175, 175, 175)),
        ]
        colors = ((40, 220, 235), (220, 90, 220))
        for index, candidate in enumerate(candidates[:2]):
            series.append((_text(candidate.get("candidate_id")), candidate.get("route"), colors[index]))
        all_points = [point for _, route, _ in series for point in _points(route)]
        extent = max([20.0] + [abs(v) for point in all_points for v in point])
        scale = min((y1 - y0 - 70) / extent, (x1 - x0 - 50) / (2.0 * extent))
        for index, (label, route, color) in enumerate(series):
            pixels = [
                (int(round(origin[0] + right * scale)), int(round(origin[1] - forward * scale)))
                for forward, right in _points(route)
            ]
            if len(pixels) >= 2:
                cv2.polylines(canvas, [np.asarray(pixels, np.int32)], False, color, 3, cv2.LINE_AA)
            self._put(canvas, label, x0 + 12 + index * 185, y1 - 8, scale=0.38, color=color, thickness=2)

    def _draw_matrix(self, canvas: np.ndarray, matrix: Mapping[str, Any]) -> None:
        x0, y0, x1, y1 = 642, 706, 1118, 982
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (75, 82, 91), 1)
        self._put(canvas, "AUXILIARY COUNTERFACTUAL MATRIX", x0 + 10, y0 + 24, scale=0.46, thickness=2)
        cells = matrix.get("cells", ()) if isinstance(matrix, Mapping) else ()
        by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
        for cell in cells if isinstance(cells, (list, tuple)) else ():
            if isinstance(cell, Mapping):
                by_key[(_text(cell.get("action_candidate_id"), ""), _text(cell.get("hypothesis_candidate_id"), ""))] = cell
        ids: list[str] = []
        for key in by_key:
            for item in key:
                if item and item not in ids:
                    ids.append(item)
        ids = ids[:2]
        if not ids:
            self._put(canvas, "UNKNOWN / no matrix cells", x0 + 18, y0 + 64, color=(80, 175, 235))
            return
        cell_w, cell_h = 224, 92
        for row, action in enumerate(ids):
            for column, hypothesis in enumerate(ids):
                cell = by_key.get((action, hypothesis), {})
                outcome = _text(cell.get("task_outcome"))
                left = x0 + 10 + column * (cell_w + 6)
                top = y0 + 48 + row * (cell_h + 8)
                cv2.rectangle(canvas, (left, top), (left + cell_w, top + cell_h), self._cell_color(outcome), -1)
                self._put(canvas, f"{action} / {hypothesis}: {outcome}", left + 7, top + 25, scale=0.35, limit=31)
                route_outcome = cell.get("route_task_outcome")
                if route_outcome is None:
                    route_outcome = cell.get("task_outcome")
                self._put(canvas, "Route=" + _text(route_outcome), left + 7, top + 49, scale=0.34, limit=31)
                self._put(canvas, "STOP=" + _text(cell.get("longitudinal_task_outcome"), "N/A"), left + 7, top + 72, scale=0.34, limit=31)

    def _draw_ownership(self, canvas: np.ndarray, record: Mapping[str, Any]) -> None:
        x0, y0, x1, y1 = 1135, 706, 1582, 982
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (75, 82, 91), 1)
        wait = record.get("physical_wait_v0", {})
        ask = record.get("ask_replanning_v0", {})
        limited = record.get("limited_act_commit_v0", {})
        perf = record.get("performance", {})
        self._put(canvas, "TIMING / OWNERSHIP", x0 + 12, y0 + 25, scale=0.5, thickness=2)
        if isinstance(limited, Mapping) and limited.get("enabled"):
            decisions = limited.get("final_execution_authority_decisions") or []
            owner = limited.get("execution_authority") or (
                decisions[-1].get("owner") if decisions else "BASELINE_CONTROL"
            )
            self._put(canvas, "LIVE CONTROL MODE: LIMITED ACT COMMIT V0", x0 + 16, y0 + 55, scale=0.41, color=(90, 235, 245), thickness=2, limit=59)
            self._put(canvas, "Owner: " + _text(owner), x0 + 16, y0 + 80, scale=0.41, limit=59)
            self._put(canvas, "Receipt issued/consumed: {}/{}".format(_text(limited.get("authority_receipts_issued"), "0"), _text(limited.get("authority_receipts_consumed"), "0")), x0 + 16, y0 + 105, scale=0.4)
            self._put(canvas, "Candidate window/write: {}/{}".format(_text(limited.get("candidate_control_window_count"), "0"), _text(limited.get("candidate_control_writes"), "0")), x0 + 16, y0 + 130, scale=0.4)
            self._put(canvas, "PID on ACT tick: " + _text(limited.get("pid_invocations_on_act_tick"), "0"), x0 + 16, y0 + 155, scale=0.4)
            self._put(canvas, "Next CARLA frame: " + _text(limited.get("next_carla_frame")), x0 + 16, y0 + 180, scale=0.4)
            self._put(canvas, "Actual actuator match: " + _text(limited.get("actual_control_actuator_match")), x0 + 16, y0 + 204, scale=0.4)
            consumed = limited.get("authority_receipts_consumed") == 1
            returned = limited.get("ownership_returned_to_baseline") is True
            self._put(canvas, "RECEIPT CONSUMED" if consumed else "RECEIPT PENDING", x0 + 16, y0 + 230, scale=0.44, color=(90, 235, 245), thickness=2)
            self._put(canvas, "CONTROL RETURNED TO BASELINE" if returned else "CONTROL RETURN PENDING", x0 + 16, y0 + 256, scale=0.42, color=(90, 235, 245) if returned else (80, 175, 235), thickness=2, limit=61)
            return
        self._put(canvas, "LIVE CONTROL MODE: HOLD_CURRENT_VALID_PLAN", x0 + 16, y0 + 55, scale=0.41, color=(90, 235, 245), thickness=2, limit=59)
        self._put(canvas, "Owner: BASELINE CURRENT VALID PLAN", x0 + 16, y0 + 80, scale=0.41, limit=59)
        self._put(canvas, "Candidate commit: " + _text(wait.get("candidate_commit_status"), "BLOCKED / 0"), x0 + 16, y0 + 105, scale=0.41)
        self._put(canvas, f"Control equality: {_text(wait.get('control_actuator_equality_count'))}/{_text(wait.get('control_equality_observed_count'))}", x0 + 16, y0 + 130, scale=0.41)
        self._put(canvas, "DriveClarify/M3 writes: 0 / 0", x0 + 16, y0 + 155, scale=0.41)
        overhead = wait.get("wait_executor_tick_overhead_ms", {})
        self._put(canvas, f"WAIT overhead max={_text(overhead.get('max'))} ms", x0 + 16, y0 + 180, scale=0.4)
        if isinstance(ask, Mapping) and ask.get("enabled"):
            oracle = ask.get("oracle", {})
            replan = ask.get("replan", {})
            timeline = " -> ".join(str(item) for item in ask.get("timeline", []))
            self._put(canvas, f"Oracle delay={_text(oracle.get('actual_delay_s'))}/{_text(oracle.get('configured_delay_s'))} s", x0 + 16, y0 + 204, scale=0.38)
            self._put(canvas, f"Replan={_text(replan.get('replan_total_ms'))} ms | forwards={_text(replan.get('post_answer_model_forward_count'))}", x0 + 16, y0 + 228, scale=0.38)
            self._put(canvas, "Timeline: " + timeline, x0 + 16, y0 + 251, scale=0.34, color=(90, 235, 245), limit=62)
            self._put(canvas, "CANDIDATE ACT = FALSE", x0 + 16, y0 + 271, scale=0.38, color=(80, 175, 235), thickness=2)
        else:
            self._put(canvas, f"Viz={_text(perf.get('visualization_render_ms'))} ms", x0 + 16, y0 + 204, scale=0.4)
            self._put(canvas, f"Front frame identity={_text(self._front_metadata.get('frame_identity_matches_baseline'))}", x0 + 16, y0 + 228, scale=0.38)
            self._put(canvas, "FULL DRIVECLARIFY CONTROL = FALSE", x0 + 16, y0 + 254, scale=0.4, color=(80, 175, 235), thickness=2)

    def compose(self, record: Mapping[str, Any]) -> np.ndarray:
        canvas = np.full((1000, 1600, 3), (23, 28, 34), dtype=np.uint8)
        cv2.rectangle(canvas, (0, 0), (1599, 74), (34, 62, 90), -1)
        self._put(canvas, "RESEARCH DEBUG VIEW | SIMULATION ONLY | NO FORMAL SAFETY GUARANTEE", 20, 29, scale=0.62, color=(250, 250, 250), thickness=2, limit=120)
        ask = record.get("ask_replanning_v0", {})
        limited = record.get("limited_act_commit_v0", {})
        explicit_title = record.get("dashboard_title")
        title = (
            str(explicit_title)
            if isinstance(explicit_title, str) and explicit_title.strip()
            else "LIMITED ACT COMMIT V0 | ONE PLAN -> ONE EXISTING PID -> ONE CARLA CONTROL"
            if isinstance(limited, Mapping) and limited.get("enabled")
            else "ASK -> WAIT -> ANSWER -> LATEST OBSERVATION -> REPLAN | BASELINE CONTROL"
            if isinstance(ask, Mapping) and ask.get("enabled")
            else "PHYSICAL WAIT PILOT V0 | LIVE CONTROL MODE: HOLD_CURRENT_VALID_PLAN"
        )
        self._put(canvas, title, 20, 59, scale=0.59, color=(80, 235, 250), thickness=2, limit=120)
        self._draw_front(canvas, record)
        self._draw_reasoning(canvas, record)
        candidates = record.get("candidates", ())
        if not isinstance(candidates, (list, tuple)):
            candidates = ()
        self._draw_routes(canvas, record.get("baseline", {}).get("route"), candidates)
        self._draw_matrix(canvas, record.get("counterfactual_matrix", {}))
        self._draw_ownership(canvas, record)
        return canvas

    def render(self, record: Mapping[str, Any]) -> float:
        started = time.monotonic_ns()
        try:
            canvas = self.compose(record)
            if not cv2.imwrite(str(self.output_path), canvas):
                raise RuntimeError("LIVE_SHADOW_PANEL_WRITE_FAILED")
            ask = record.get("ask_replanning_v0", {})
            if isinstance(ask, Mapping) and ask.get("enabled"):
                stage = str(ask.get("stage", ""))
                stage_file = {
                    "ASK_SENT_WAIT_ACTIVE": "ask_sent_wait_active.png",
                    "ANSWER_RECEIVED_OLD_CANDIDATES_INVALIDATED": "answer_received_old_candidates_invalidated.png",
                    "LATEST_OBSERVATION_CAPTURED": "answer_received_old_candidates_invalidated.png",
                    "REPLANNING_FROM_LATEST_OBSERVATION": "replanning_from_latest_observation.png",
                    "REPLAN_COMPLETE_RESUME_READY": "replan_complete_resume_ready.png",
                }.get(stage)
                if stage_file is not None:
                    stage_path = self.output_path.with_name(stage_file)
                    if not cv2.imwrite(str(stage_path), canvas):
                        raise RuntimeError("ASK_STAGE_SCREENSHOT_WRITE_FAILED")
                    self._stage_screenshots[stage] = str(stage_path)
            limited = record.get("limited_act_commit_v0", {})
            if isinstance(limited, Mapping) and limited.get("enabled"):
                filenames = {
                    "CANDIDATE_AUTHORITY_GRANTED": "candidate_authority_granted.png",
                    "ACT_CONTROL_TICK_ACTIVE": "act_control_tick_active.png",
                    "ACT_CARLA_NEXT_FRAME_OBSERVED": "act_carla_next_frame_observed.png",
                    "CONTROL_RETURNED_TO_BASELINE": "control_returned_to_baseline.png",
                }
                for milestone in limited.get("milestones", ()):
                    stage = str(milestone.get("stage", ""))
                    stage_file = filenames.get(stage)
                    if stage_file is None or stage in self._stage_screenshots:
                        continue
                    staged_record = copy.deepcopy(dict(record))
                    staged_limited = dict(limited)
                    staged_limited.update(dict(milestone))
                    staged_limited["stage"] = stage
                    staged_record["limited_act_commit_v0"] = staged_limited
                    staged_canvas = self.compose(staged_record)
                    stage_path = self.output_path.with_name(stage_file)
                    if not cv2.imwrite(str(stage_path), staged_canvas):
                        raise RuntimeError("LIMITED_ACT_STAGE_SCREENSHOT_WRITE_FAILED")
                    self._stage_screenshots[stage] = str(stage_path)
            if self.open_window:
                if not self._window_created:
                    cv2.namedWindow(self.window_title, cv2.WINDOW_NORMAL)
                    width = int(os.environ.get("DRIVECLARIFY_VISUALIZER_WINDOW_WIDTH", "1280"))
                    height = int(os.environ.get("DRIVECLARIFY_VISUALIZER_WINDOW_HEIGHT", "800"))
                    x = int(os.environ.get("DRIVECLARIFY_VISUALIZER_WINDOW_X", "0"))
                    y = int(os.environ.get("DRIVECLARIFY_VISUALIZER_WINDOW_Y", "45"))
                    cv2.resizeWindow(self.window_title, width, height)
                    cv2.moveWindow(self.window_title, x, y)
                    self._window_created = True
                cv2.imshow(self.window_title, canvas)
                cv2.waitKey(1)
            self.render_count += 1
            self.last_render_ms = (time.monotonic_ns() - started) / 1_000_000.0
            return self.last_render_ms
        except Exception as exc:
            self.last_error = type(exc).__name__ + ":" + str(exc)
            raise

    def close(self) -> None:
        if self._window_created:
            try:
                cv2.destroyWindow(self.window_title)
                cv2.waitKey(1)
            finally:
                self._window_created = False

    def summary(self) -> dict[str, Any]:
        return {
            "window_title": self.window_title,
            "open_window": self.open_window,
            "render_count": self.render_count,
            "last_render_ms": self.last_render_ms,
            "last_error": self.last_error,
            "screenshot_path": str(self.output_path),
            "layout": "FRONT_CAMERA_FIRST",
            "main_view_source": 'REAL_SIMLINGO_INPUT_DATA_RGB_0',
            "main_view_label": FRONT_VIEW_LABEL,
            "display_copy_label": FRONT_COPY_LABEL,
            "front_camera": dict(self._front_metadata),
            "ask_lifecycle_stage_screenshots": dict(self._stage_screenshots),
            "limited_act_stage_screenshots": {
                key: value
                for key, value in self._stage_screenshots.items()
                if key in {
                    "CANDIDATE_AUTHORITY_GRANTED",
                    "ACT_CONTROL_TICK_ACTIVE",
                    "ACT_CARLA_NEXT_FRAME_OBSERVED",
                    "CONTROL_RETURNED_TO_BASELINE",
                }
            },
        }
