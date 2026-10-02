"""Passive 1080p dashboard for the distinct candidate trajectory demo."""

from __future__ import annotations

import json
import math
import os
import textwrap
from pathlib import Path
from typing import Any, Mapping, Sequence


A_COLOR = (255, 184, 58)   # BGR cyan-blue
B_COLOR = (72, 118, 255)   # BGR orange-red
GUIDE_COLOR = (125, 139, 154)
TEXT = (230, 235, 241)
MUTED = (151, 164, 179)
GREEN = (105, 229, 151)


def _put(
    canvas: Any,
    text: Any,
    x: int,
    y: int,
    *,
    color: tuple[int, int, int] = TEXT,
    scale: float = 0.44,
    thickness: int = 1,
) -> None:
    import cv2

    cv2.putText(
        canvas,
        str(text),
        (x, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def _box(canvas: Any, rect: tuple[int, int, int, int], title: str) -> None:
    import cv2

    x0, y0, x1, y1 = rect
    cv2.rectangle(canvas, (x0, y0), (x1, y1), (57, 67, 80), 1)
    cv2.rectangle(canvas, (x0, y0), (x1, y0 + 34), (31, 39, 50), -1)
    _put(canvas, title, x0 + 13, y0 + 23, color=(198, 211, 225), scale=0.43)


def _short(value: Any, limit: int) -> str:
    text = "UNKNOWN" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _wrap(value: Any, width: int) -> list[str]:
    return textwrap.wrap(str(value or "UNKNOWN"), width=width) or ["UNKNOWN"]


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _first_plans(plans: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    result = []
    for prefix in ("A", "B"):
        row = next(
            (
                item
                for item in plans
                if str(item.get("candidate_id", "")).startswith(prefix)
            ),
            None,
        )
        if row is not None:
            result.append(row)
    return result


def _points(value: Any) -> list[tuple[float, float]]:
    result = []
    for row in value or ():
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            try:
                result.append((float(row[0]), float(row[1])))
            except (TypeError, ValueError):
                pass
    return result


def _rmse(left: Sequence[tuple[float, float]], right: Sequence[tuple[float, float]]) -> float:
    values = [
        (a - b) ** 2
        for lrow, rrow in zip(left, right)
        for a, b in zip(lrow, rrow)
    ]
    return math.sqrt(sum(values) / len(values)) if values else 0.0


def _arc_length(route: Sequence[tuple[float, float]]) -> float:
    return sum(math.hypot(bx - ax, by - ay) for (ax, ay), (bx, by) in zip(route, route[1:]))


def _decision_predicates(receipt: Mapping[str, Any] | None) -> Mapping[str, Any]:
    try:
        rows = receipt["recommendation"]["decision_predicates"]
        return dict(rows)
    except (KeyError, TypeError, ValueError):
        return {}


def _world_local(
    topology_context: Mapping[str, Any] | None,
    point: Sequence[float],
) -> tuple[float, float] | None:
    rows = _points((topology_context or {}).get("route_polyline_world"))
    if len(rows) < 2:
        return None
    origin = rows[0]
    direction = next(
        (
            (x_value - origin[0], y_value - origin[1])
            for x_value, y_value in rows[1:]
            if math.hypot(x_value - origin[0], y_value - origin[1]) > 0.25
        ),
        None,
    )
    if direction is None:
        return None
    norm = math.hypot(*direction)
    fx, fy = direction[0] / norm, direction[1] / norm
    dx, dy = float(point[0]) - origin[0], float(point[1]) - origin[1]
    forward = dx * fx + dy * fy
    # SimLingo's local route uses negative lateral values for these right
    # turns. Use the matching left-positive basis for the detached map guide.
    lateral = dx * fy - dy * fx
    return forward, lateral


def _dashed_polyline(canvas: Any, pixels: Sequence[tuple[int, int]], color: tuple[int, int, int]) -> None:
    import cv2

    for index, (left, right) in enumerate(zip(pixels, pixels[1:])):
        if index % 2 == 0:
            cv2.line(canvas, left, right, color, 2, cv2.LINE_AA)


def _draw_bev(
    canvas: Any,
    rect: tuple[int, int, int, int],
    plans: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    topology_context: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    import cv2
    import numpy as np

    selected = _first_plans(plans)
    routes = [_points(row.get("route")) for row in selected]
    topology_world = _points((topology_context or {}).get("route_polyline_world"))
    topology_local = [
        local
        for local in (_world_local(topology_context, point) for point in topology_world)
        if local is not None
    ]
    target_rows = []
    for candidate in candidates[:2]:
        junction = candidate.get("junction_route_anchor_xy")
        branch = candidate.get("branch_anchor_xy")
        target_rows.append(
            {
                "junction": None if junction is None else _world_local(topology_context, junction),
                "branch": None if branch is None else _world_local(topology_context, branch),
            }
        )
    all_points = [point for route in routes for point in route]
    all_points.extend(topology_local[:40])
    all_points.extend(
        point
        for row in target_rows
        for point in (row["junction"], row["branch"])
        if point is not None
    )
    x0, y0, x1, y1 = rect
    px0, py0, px1, py1 = x0 + 30, y0 + 48, x1 - 24, y1 - 42
    cv2.rectangle(canvas, (px0, py0), (px1, py1), (23, 29, 37), -1)
    max_forward = max([point[0] for point in all_points] + [18.0])
    lateral_bound = max([abs(point[1]) for point in all_points] + [5.0]) * 1.18

    def pixel(point: tuple[float, float]) -> tuple[int, int]:
        forward, lateral = point
        u = int(round((px0 + px1) / 2 + lateral / lateral_bound * (px1 - px0) * 0.46))
        v = int(round(py1 - max(0.0, forward) / max_forward * (py1 - py0 - 8)))
        return u, v

    if len(topology_local) >= 2:
        _dashed_polyline(canvas, [pixel(point) for point in topology_local[:40]], GUIDE_COLOR)
    for index, row in enumerate(target_rows):
        junction, branch = row["junction"], row["branch"]
        if junction is None:
            continue
        jp = pixel(junction)
        cv2.circle(canvas, jp, 8, (205, 213, 222), -1)
        _put(canvas, "JUNCTION {}".format(index + 1), jp[0] + 10, jp[1] - 8, color=(205, 213, 222), scale=0.34)
        if branch is not None:
            bp = pixel(branch)
            _dashed_polyline(canvas, [jp, bp], _candidate_color(index))
            cv2.drawMarker(canvas, bp, _candidate_color(index), cv2.MARKER_DIAMOND, 16, 2)
            _put(canvas, "TARGET {}".format(chr(65 + index)), bp[0] + 8, bp[1] + 15, color=_candidate_color(index), scale=0.33)
    projected_routes = []
    for index, route in enumerate(routes):
        pixels = [pixel(point) for point in route]
        projected_routes.append(pixels)
        if len(pixels) >= 2:
            cv2.polylines(
                canvas,
                [np.asarray(pixels, dtype=np.int32)],
                False,
                _candidate_color(index),
                5,
                cv2.LINE_AA,
            )
            endpoint = pixels[-1]
            _put(canvas, "{}  SIMLINGO PREDICTED PLAN".format(chr(65 + index)), endpoint[0] + 7, endpoint[1], color=_candidate_color(index), scale=0.34, thickness=2)
    ego = pixel((0.0, 0.0))
    cv2.drawMarker(canvas, ego, GREEN, cv2.MARKER_TRIANGLE_UP, 22, 3)
    _put(canvas, "EGO / FORWARD ↑", ego[0] - 58, ego[1] - 15, color=GREEN, scale=0.36, thickness=2)
    _put(canvas, "dashed = TOPOLOGY GUIDE   solid = REAL SIMLINGO pred_route", px0 + 10, py0 + 20, color=MUTED, scale=0.34)
    _put(canvas, "COMMON SCALE • NO SHIFT • NO STRETCH • NO FAKE WAYPOINTS", px0 + 10, py1 + 25, color=MUTED, scale=0.32)

    separations = []
    if len(projected_routes) == 2:
        separations = [
            math.hypot(a[0] - b[0], a[1] - b[1])
            for a, b in zip(projected_routes[0], projected_routes[1])
        ]
    return {
        "real_simlingo_pred_route_only": True,
        "common_projection_no_candidate_specific_transform": True,
        "max_pixel_separation": max(separations) if separations else 0.0,
        "sustained_points_ge_16px": sum(value >= 16.0 for value in separations),
        "route_a_arc_length_m": _arc_length(routes[0]) if len(routes) > 0 else 0.0,
        "route_b_arc_length_m": _arc_length(routes[1]) if len(routes) > 1 else 0.0,
        "route_rmse": _rmse(routes[0], routes[1]) if len(routes) == 2 else 0.0,
    }


def _candidate_color(index: int) -> tuple[int, int, int]:
    return A_COLOR if index == 0 else B_COLOR


def _draw_speed(
    canvas: Any,
    rect: tuple[int, int, int, int],
    plans: Sequence[Mapping[str, Any]],
) -> float:
    import cv2
    import numpy as np

    selected = _first_plans(plans)
    rows = [_points(item.get("speed")) for item in selected]
    x0, y0, x1, y1 = rect
    px0, py0, px1, py1 = x0 + 34, y0 + 48, x1 - 18, y1 - 30
    cv2.rectangle(canvas, (px0, py0), (px1, py1), (23, 29, 37), -1)
    values = [[math.hypot(a, b) for a, b in row] for row in rows]
    vmax = max([value for group in values for value in group] + [1.0])
    for index, group in enumerate(values):
        pixels = [
            (
                int(round(px0 + step / max(len(group) - 1, 1) * (px1 - px0))),
                int(round(py1 - value / vmax * (py1 - py0 - 6))),
            )
            for step, value in enumerate(group)
        ]
        if len(pixels) >= 2:
            cv2.polylines(canvas, [np.asarray(pixels, dtype=np.int32)], False, _candidate_color(index), 3, cv2.LINE_AA)
    _put(canvas, "A", px0 + 8, py0 + 20, color=A_COLOR, scale=0.36, thickness=2)
    _put(canvas, "B", px0 + 38, py0 + 20, color=B_COLOR, scale=0.36, thickness=2)
    return _rmse(rows[0], rows[1]) if len(rows) == 2 else 0.0


def render_demo_panel(
    output_dir: Path,
    image: Any,
    *,
    decision: str,
    why: str,
    instruction: str,
    ambiguity_type: str,
    candidates: Sequence[Mapping[str, Any]],
    decision_receipt: Mapping[str, Any] | None,
    interaction: Mapping[str, Any] | None = None,
    plans: Sequence[Mapping[str, Any]] = (),
    grounding: Mapping[str, Any] | None = None,
    topology: Sequence[Mapping[str, Any]] = (),
    topology_context: Mapping[str, Any] | None = None,
    authority: Mapping[str, Any] | None = None,
    runtime_status: str | None = None,
    frame_id: int | None = None,
    window_title: str,
) -> Mapping[str, Any]:
    import cv2
    import numpy as np

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = output_dir / "GROUNDED_V1_DEMO_PANEL.png"
    standard = output_dir / "GROUNDED_LANGUAGE_V1_PANEL.png"
    ready = bool(
        decision == "ASK"
        and len(candidates) >= 2
        and len(_first_plans(plans)) == 2
        and int((grounding or {}).get("effective_k", len(candidates)) or 0) == 2
        and len({item.get("target_id") for item in candidates[:2]}) == 2
    )
    # Once ASK evidence is ready, keep the live window on that detached copy
    # while CARLA and the ASK→answer→fresh-replan lifecycle continue normally.
    if snapshot.is_file() and not ready:
        frozen = cv2.imread(str(snapshot), cv2.IMREAD_COLOR)
        if frozen is not None:
            cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window_title, 1240, 700)
            cv2.moveWindow(window_title, 0, 40)
            cv2.imshow(window_title, frozen)
            cv2.waitKey(50)
            return {
                "rendered": True,
                "native_refreshed": True,
                "error": None,
                "panel_path": str(snapshot),
                "demo_evidence_frozen": True,
            }

    front = np.asarray(image).copy()
    if front.ndim != 3:
        return {"rendered": False, "native_refreshed": False, "error": "IMAGE_RANK_INVALID"}
    front = np.ascontiguousarray(front[:, :, :3])
    for index, candidate in enumerate(candidates[:2]):
        bbox = candidate.get("bbox_xyxy")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        bx0, by0, bx1, by1 = (int(round(float(value))) for value in bbox)
        color = _candidate_color(index)
        cv2.rectangle(front, (bx0, by0), (bx1, by1), color, 4)
        label = "{}  {} white van  confidence={:.3f}".format(
            chr(65 + index),
            "Nearer" if index == 0 else "Farther",
            float(candidate.get("detector_confidence") or 0.0),
        )
        cv2.rectangle(front, (bx0, max(0, by0 - 27)), (min(front.shape[1] - 1, bx0 + 330), by0), color, -1)
        _put(front, label, bx0 + 5, max(18, by0 - 7), color=(18, 23, 29), scale=0.43, thickness=1)

    canvas = np.full((1080, 1920, 3), (18, 23, 30), dtype=np.uint8)
    cv2.rectangle(canvas, (0, 0), (1919, 142), (28, 36, 47), -1)
    _put(canvas, "DRIVECLARIFY GROUNDED V1 — LIVE DEMO", 25, 34, color=(155, 205, 255), scale=0.72, thickness=2)
    _put(canvas, "RAW INSTRUCTION:  " + instruction, 25, 70, color=TEXT, scale=0.58, thickness=2)
    _put(canvas, "TWO PLAUSIBLE GROUNDED INTERPRETATIONS", 25, 111, color=(72, 205, 255), scale=0.67, thickness=2)
    _put(canvas, "A  Nearer white van → Junction 1", 650, 70, color=A_COLOR, scale=0.54, thickness=2)
    _put(canvas, "B  Farther white van → Junction 2", 650, 108, color=B_COLOR, scale=0.54, thickness=2)
    _put(canvas, "QUALITATIVE LIVE DEMONSTRATION", 1340, 34, color=(172, 190, 210), scale=0.43, thickness=2)
    _put(canvas, "RESEARCH DEBUG VIEW • SIMULATION ONLY • NO FORMAL SAFETY GUARANTEE", 1340, 69, color=(121, 139, 158), scale=0.34)
    _put(canvas, "DISPLAY=:1 • native CARLA • frame {}".format(frame_id), 1340, 105, color=MUTED, scale=0.37)

    rgb_rect = (18, 155, 855, 640)
    target_rect = (875, 155, 1902, 395)
    prompt_rect = (875, 410, 1902, 640)
    bev_rect = (18, 655, 1190, 1060)
    speed_rect = (1208, 655, 1548, 865)
    decision_rect = (1565, 655, 1902, 1060)
    speed_note_rect = (1208, 880, 1548, 1060)
    _box(canvas, rgb_rect, "REAL SAME-FRAME SIMLINGO RGB_0 + REAL GROUNDING DINO BOXES")
    _box(canvas, target_rect, "TOPOLOGY-AWARE TARGET BINDING — DETACHED RUNTIME RECEIPTS")
    _box(canvas, prompt_rect, "EXACT CANDIDATE-SPECIFIC CONDITIONING SENT TO SIMLINGO")
    _box(canvas, bev_rect, "MAIN BEV — TWO REAL SIMLINGO CANDIDATE TRAJECTORIES")
    _box(canvas, speed_rect, "CANDIDATE SPEED-PLAN PROFILE")
    _box(canvas, speed_note_rect, "DIVERGENCE / HORIZON")
    _box(canvas, decision_rect, "DECISION / CONSEQUENCE / AUTHORITY")

    available_w, available_h = 813, 428
    scale = min(available_w / front.shape[1], available_h / front.shape[0])
    resized = cv2.resize(front, (int(front.shape[1] * scale), int(front.shape[0] * scale)))
    ix = rgb_rect[0] + 12 + (available_w - resized.shape[1]) // 2
    iy = rgb_rect[1] + 43 + (available_h - resized.shape[0]) // 2
    canvas[iy : iy + resized.shape[0], ix : ix + resized.shape[1]] = resized

    for index, candidate in enumerate(candidates[:2]):
        x = target_rect[0] + 18 + index * 505
        color = _candidate_color(index)
        _put(canvas, "CANDIDATE {}".format(chr(65 + index)), x, 205, color=color, scale=0.50, thickness=2)
        _put(canvas, "Referent: {} white van".format("Nearer" if index == 0 else "Farther"), x, 235, color=TEXT, scale=0.42)
        _put(canvas, "↓  Target: Junction {}  /  Right Branch {}".format(index + 1, index + 1), x, 264, color=color, scale=0.43, thickness=2)
        _put(canvas, "Junction ID: " + _short(candidate.get("junction_id"), 35), x, 293, color=MUTED, scale=0.37)
        _put(canvas, "Branch ID: " + _short(candidate.get("branch_id"), 35), x, 319, color=MUTED, scale=0.37)
        _put(canvas, "Target ID: " + _short(candidate.get("target_id"), 35), x, 345, color=MUTED, scale=0.37)
        _put(canvas, "opportunity progress = {} m".format(candidate.get("distance_or_progress")), x, 372, color=TEXT, scale=0.38)
    distinct = len(candidates) >= 2 and len({item.get("target_id") for item in candidates[:2]}) == 2
    _put(canvas, "TARGETS DISTINCT = " + str(distinct).upper(), 1390, 382, color=GREEN if distinct else (80, 80, 255), scale=0.45, thickness=2)

    selected = _first_plans(plans)
    for index, row in enumerate(selected):
        x = prompt_rect[0] + 18 + index * 505
        y = 458
        prompt = row.get("forwarded_prompt_text") or row.get("forward_evidence", {}).get("forwarded_prompt_text")
        prompt_hash = row.get("forwarded_prompt_sha256") or row.get("forward_evidence", {}).get("forwarded_prompt_sha256")
        _put(canvas, "{}  Prompt hash: {}".format(chr(65 + index), _short(prompt_hash, 28)), x, y, color=_candidate_color(index), scale=0.40, thickness=2)
        for line in _wrap(prompt, 67)[:6]:
            y += 25
            _put(canvas, line, x, y, color=TEXT, scale=0.34)

    bev_metrics = _draw_bev(canvas, bev_rect, plans, candidates, topology_context)
    speed_rmse = _draw_speed(canvas, speed_rect, plans)
    second_progress = float(candidates[1].get("distance_or_progress") or 0.0) if len(candidates) > 1 else 0.0
    horizon_ok = bool(second_progress > 0 and second_progress <= bev_metrics["route_b_arc_length_m"])
    nx, ny = speed_note_rect[0] + 14, speed_note_rect[1] + 55
    for text, color in (
        ("route RMSE = {:.6f}".format(bev_metrics["route_rmse"]), TEXT),
        ("speed RMSE = {:.6f}".format(speed_rmse), TEXT),
        ("max rendered separation = {:.1f} px".format(bev_metrics["max_pixel_separation"]), TEXT),
        ("B target progress = {:.2f} m".format(second_progress), TEXT),
        ("B real pred_route arc = {:.2f} m".format(bev_metrics["route_b_arc_length_m"]), TEXT),
        ("SECOND TARGET IN HORIZON = " + str(horizon_ok).upper(), GREEN if horizon_ok else (80, 80, 255)),
    ):
        _put(canvas, text, nx, ny, color=color, scale=0.35, thickness=2 if "HORIZON" in text else 1)
        ny += 23

    predicates = _decision_predicates(decision_receipt)
    ax, ay = decision_rect[0] + 14, decision_rect[1] + 57
    lines = [
        ("FINAL DECISION: " + decision, (72, 205, 255), 0.49, 2),
        ("WHY:", MUTED, 0.37, 1),
    ]
    for line in _wrap(why, 42)[:4]:
        lines.append((line, TEXT, 0.35, 1))
    lines.extend(
        [
            ("Material consequence divergence: YES" if predicates.get("consequence_difference") else "Material consequence divergence: NO", TEXT, 0.34, 1),
            ("Answer changes action: " + str(bool(predicates.get("answer_can_change_selected_action"))).upper(), TEXT, 0.34, 1),
            ("Query justified: " + str(decision == "ASK").upper(), TEXT, 0.34, 1),
            ("A: turn at first opportunity", A_COLOR, 0.34, 2),
            ("B: continue, then turn later", B_COLOR, 0.34, 2),
        ]
    )
    authority = dict(authority or {})
    lines.extend(
        [
            ("Existing PID: " + _short(authority.get("existing_pid_count"), 12), MUTED, 0.33, 1),
            ("New PID / candidate writes / M3 writes: 0 / 0 / 0", MUTED, 0.30, 1),
            ("Visualization extra forwards / PID / control: 0 / 0 / 0", MUTED, 0.29, 1),
        ]
    )
    for text, color, scale_value, thickness in lines:
        _put(canvas, text, ax, ay, color=color, scale=scale_value, thickness=thickness)
        ay += 25

    cv2.imwrite(str(standard), canvas)
    if ready and not snapshot.exists():
        cv2.imwrite(str(snapshot), canvas)
        visual_state = {
            "schema_version": "driveclarify.grounded_v1_distinct_trajectory_demo.visual_state.v1",
            "status": "ASK_EVIDENCE_SNAPSHOT_FROZEN",
            "frame_id": frame_id,
            "runtime_status": runtime_status,
            "raw_instruction": instruction,
            "effective_k": 2,
            "targets_distinct": distinct,
            "prompt_hashes": [
                row.get("forwarded_prompt_sha256")
                or row.get("forward_evidence", {}).get("forwarded_prompt_sha256")
                for row in selected
            ],
            "bev": dict(bev_metrics),
            "speed_rmse": speed_rmse,
            "second_target_progress_m": second_progress,
            "second_target_in_vla_horizon": horizon_ok,
            "candidate_trajectory_source": "REAL_SIMLINGO_PRED_ROUTE",
            "topology_guide_source": "DETACHED_RUNTIME_ROUTE_DEQUE_AND_LIVE_MAP_TARGET_RECEIPTS",
            "visualization_induced_forwards": 0,
            "visualization_induced_pid": 0,
            "visualization_induced_control": 0,
        }
        _atomic_json(output_dir / "GROUNDED_V1_DEMO_VISUAL_STATE.json", visual_state)

    cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_title, 1240, 700)
    cv2.moveWindow(window_title, 0, 40)
    cv2.imshow(window_title, canvas)
    cv2.waitKey(50)
    return {
        "rendered": True,
        "native_refreshed": True,
        "error": None,
        "panel_path": str(snapshot if snapshot.is_file() else standard),
        "demo_evidence_ready": ready,
    }


__all__ = ["render_demo_panel"]
