"""Passive, human-readable native dashboard for Grounded Language V1.

The renderer accepts detached copies of evidence already computed by the
runtime.  It has no detector, model, planner, PID, or control dependency.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import VISUALIZATION_ENV


DEMO_ENV = "DRIVECLARIFY_GROUNDED_V1_DISTINCT_TRAJECTORY_DEMO"
WINDOW_TITLE = (
    "DriveClarify Grounded V1 Live Demo"
    if str(os.environ.get(DEMO_ENV, "")).strip().casefold() in {"1", "true", "yes", "on"}
    else "DriveClarify Grounded V1 Runtime"
)
A_COLOR = (245, 176, 65)  # BGR blue
B_COLOR = (80, 190, 255)  # BGR amber


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _short(value: Any, limit: int = 30) -> str:
    text = "UNKNOWN" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _lines(text: Any, width: int = 58) -> list[str]:
    words = str(text or "UNKNOWN").split()
    result: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if len(candidate) > width and current:
            result.append(current)
            current = word
        else:
            current = candidate
    if current:
        result.append(current)
    return result or ["UNKNOWN"]


def _candidate_color(index: int) -> tuple[int, int, int]:
    return A_COLOR if index == 0 else B_COLOR


def _route_points(row: Mapping[str, Any]) -> Sequence[Any]:
    """Read either the legacy plan schema or the production R4.4 schema."""

    return row.get("model_predicted_local_route") or row.get("route") or ()


def _select_candidate_plans(
    plans: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Select one existing plan for each displayed semantic candidate.

    Production candidate identifiers are content-derived (for example
    ``r44-cand-*``), so their first character has no A/B semantics.  Exact
    candidate identity is authoritative.  The prefix fallback only preserves
    compatibility with the older A1/B1 repetition schema.
    """

    selected: list[Mapping[str, Any]] = []
    candidate_ids = [
        str(row.get("candidate_id"))
        for row in candidates[:2]
        if row.get("candidate_id") is not None
    ]
    for candidate_id in candidate_ids:
        row = next(
            (
                item
                for item in plans
                if str(item.get("candidate_id", "")) == candidate_id
                and _route_points(item)
            ),
            None,
        )
        if row is not None:
            selected.append(row)
    if selected:
        return selected
    for prefix in ("A", "B"):
        row = next(
            (
                item
                for item in plans
                if str(item.get("candidate_id", "")).startswith(prefix)
                and _route_points(item)
            ),
            None,
        )
        if row is not None:
            selected.append(row)
    return selected


def _box(canvas: Any, rect: tuple[int, int, int, int], title: str) -> None:
    import cv2

    x0, y0, x1, y1 = rect
    cv2.rectangle(canvas, (x0, y0), (x1, y1), (51, 60, 72), 1)
    cv2.rectangle(canvas, (x0, y0), (x1, y0 + 34), (30, 37, 48), -1)
    cv2.putText(
        canvas,
        title,
        (x0 + 13, y0 + 23),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (196, 210, 224),
        1,
        cv2.LINE_AA,
    )


def _put(
    canvas: Any,
    text: Any,
    x: int,
    y: int,
    *,
    color: tuple[int, int, int] = (224, 229, 235),
    scale: float = 0.43,
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


def _decision_fields(decision_receipt: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not decision_receipt:
        return {}
    recommendation = decision_receipt.get("recommendation", {})
    predicates = dict(recommendation.get("decision_predicates", []))
    return {
        "act_value": recommendation.get("act_value"),
        "query_value": recommendation.get("query_value"),
        "wait_value": recommendation.get("wait_value"),
        "consequence_difference": predicates.get("consequence_difference"),
        "answer_changes_action": predicates.get("answer_can_change_selected_action"),
        "future_information": predicates.get("future_information_arrival_declared"),
        "information_source": (
            "ENVIRONMENT"
            if predicates.get("future_information_arrival_declared")
            else "PASSENGER"
            if predicates.get("answer_can_change_selected_action")
            else "NONE"
        ),
    }


def _bev(
    canvas: Any,
    rect: tuple[int, int, int, int],
    plans: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    topology: Sequence[Mapping[str, Any]] = (),
) -> None:
    import cv2
    import numpy as np

    x0, y0, x1, y1 = rect
    plot = (x0 + 18, y0 + 48, x1 - 18, y1 - 50)
    px0, py0, px1, py1 = plot
    cv2.rectangle(canvas, (px0, py0), (px1, py1), (25, 31, 39), -1)
    cv2.line(canvas, ((px0 + px1) // 2, py1), ((px0 + px1) // 2, py0), (75, 86, 99), 1)
    _put(canvas, "EGO ▲", (px0 + px1) // 2 - 27, py1 - 8, color=(120, 235, 155), scale=0.4)
    _put(canvas, "forward", px0 + 7, py0 + 18, color=(128, 143, 158), scale=0.35)
    _put(canvas, "~5 m display scale", px1 - 122, py1 - 8, color=(128, 143, 158), scale=0.34)
    # Abstract route-topology markers are drawn from detached opportunity
    # receipts.  They are deliberately not a second map/planner query.
    ordered_topology = sorted(
        (dict(item) for item in topology if isinstance(item, Mapping)),
        key=lambda item: int(item.get("route_order_index", item.get("route_opportunity_index", 0)) or 0),
    )[:3]
    for index, opportunity in enumerate(ordered_topology):
        marker_y = py1 - int((index + 1) * (py1 - py0) / (len(ordered_topology) + 1))
        cv2.circle(canvas, ((px0 + px1) // 2, marker_y), 7, (184, 196, 210), -1)
        direction = str(opportunity.get("maneuver_direction") or opportunity.get("road_option") or "TURN")
        if direction.upper() == "RIGHT":
            cv2.line(canvas, ((px0 + px1) // 2, marker_y), (px1 - 28, marker_y), (82, 98, 115), 2)
        elif direction.upper() == "LEFT":
            cv2.line(canvas, ((px0 + px1) // 2, marker_y), (px0 + 28, marker_y), (82, 98, 115), 2)
        _put(
            canvas,
            "J{} {}".format(opportunity.get("route_order_index", index + 1), direction),
            (px0 + px1) // 2 + 12,
            marker_y - 8,
            color=(155, 169, 184),
            scale=0.34,
        )
    selected = _select_candidate_plans(plans, candidates)
    points = []
    for row in selected:
        route = _route_points(row)
        clean = [item for item in route if isinstance(item, (list, tuple)) and len(item) >= 2]
        points.extend(clean)
    if points:
        arr = np.asarray(points, dtype=float)
        forward = arr[:, 0]
        lateral = arr[:, 1]
        fmin, fmax = min(0.0, float(forward.min())), max(1.0, float(forward.max()))
        lbound = max(1.5, float(np.abs(lateral).max()) * 1.2)
        for index, row in enumerate(selected):
            route = [item for item in _route_points(row) if isinstance(item, (list, tuple)) and len(item) >= 2]
            pixels = []
            for forward_value, lateral_value, *_ in route:
                u = int(round((px0 + px1) / 2 + float(lateral_value) / lbound * (px1 - px0) * 0.42))
                v = int(round(py1 - (float(forward_value) - fmin) / (fmax - fmin) * (py1 - py0 - 10)))
                pixels.append((u, v))
            if len(pixels) >= 2:
                cv2.polylines(canvas, [np.asarray(pixels, dtype=np.int32)], False, _candidate_color(index), 3, cv2.LINE_AA)
    for index, candidate in enumerate(candidates[:2]):
        label = chr(ord("A") + index)
        descriptor = candidate.get("display_descriptor") or candidate.get("semantic_constraint") or candidate.get("referring_expression") or candidate.get("event_state") or candidate.get("referent_phrase") or candidate.get("referent_id")
        _put(canvas, label + " — " + _short(descriptor, 33), x0 + 18 + index * 260, y1 - 22, color=_candidate_color(index), scale=0.4, thickness=2)


def render_panel(
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
    decision_window: Mapping[str, Any] | None = None,
    runtime_status: str | None = None,
    frame_id: int | None = None,
) -> Mapping[str, Any]:
    """Render already-computed evidence and optionally refresh one native window."""

    if _truthy(os.environ.get(DEMO_ENV)):
        from driveclarify_grounded_v1_distinct_trajectory_demo.visualization import (
            render_demo_panel,
        )

        return render_demo_panel(
            output_dir,
            image,
            decision=decision,
            why=why,
            instruction=instruction,
            ambiguity_type=ambiguity_type,
            candidates=candidates,
            decision_receipt=decision_receipt,
            interaction=interaction,
            plans=plans,
            grounding=grounding,
            topology=topology,
            topology_context=topology_context,
            authority=authority,
            runtime_status=runtime_status,
            frame_id=frame_id,
            window_title=WINDOW_TITLE,
        )

    if not _truthy(os.environ.get(VISUALIZATION_ENV)):
        return {"rendered": False, "native_refreshed": False, "error": None, "disabled": True}

    import cv2
    import numpy as np

    if image is None:
        return {"rendered": False, "native_refreshed": False, "error": "IMAGE_NONE"}
    front = np.asarray(image).copy()
    if front.ndim != 3:
        return {"rendered": False, "native_refreshed": False, "error": "IMAGE_RANK_INVALID"}
    front = np.ascontiguousarray(front[:, :, :3])
    height, width = front.shape[:2]
    for index, candidate in enumerate(candidates[:2]):
        bbox = candidate.get("bbox_xyxy")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = (int(round(float(item))) for item in bbox)
        color = _candidate_color(index)
        cv2.rectangle(front, (x0, y0), (x1, y1), color, 3)
        label = "{}  {}  {}  {:.2f}".format(
            chr(ord("A") + index),
            _short(candidate.get("referent_phrase") or candidate.get("phrase") or "referent", 16),
            _short(candidate.get("ordering") or candidate.get("relative_image_location") or candidate.get("event_state") or "", 12),
            float(candidate.get("detector_confidence") or candidate.get("confidence") or 0.0),
        )
        cv2.rectangle(front, (x0, max(0, y0 - 23)), (min(width - 1, x0 + 270), y0), color, -1)
        _put(front, label, x0 + 4, max(16, y0 - 6), color=(16, 22, 28), scale=0.42, thickness=1)

    canvas = np.full((900, 1600, 3), (19, 24, 31), dtype=np.uint8)
    decision_color = {"ACT": (98, 226, 134), "ASK": (65, 197, 255), "WAIT": (255, 184, 71)}.get(decision, (165, 176, 188))
    cv2.rectangle(canvas, (0, 0), (1599, 105), (25, 31, 40), -1)
    _put(canvas, "DRIVECLARIFY GROUNDED V1", 24, 30, color=(152, 169, 188), scale=0.55, thickness=2)
    _put(canvas, "DECISION: " + str(decision), 24, 76, color=decision_color, scale=1.12, thickness=3)
    for line_index, line in enumerate(_lines(why, 92)[:2]):
        _put(canvas, "WHY: " + line if line_index == 0 else "     " + line, 350, 55 + line_index * 26, color=(231, 235, 239), scale=0.52, thickness=1)
    _put(canvas, "SIMULATION ONLY  •  RESEARCH DEBUG VIEW  •  NO FORMAL SAFETY GUARANTEE", 1040, 30, color=(107, 123, 139), scale=0.36)
    _put(canvas, "Frame {}  |  {}".format(frame_id if frame_id is not None else "UNKNOWN", _short(runtime_status, 42)), 1040, 73, color=(151, 164, 178), scale=0.42)

    rect_rgb = (18, 120, 805, 545)
    rect_language = (820, 120, 1582, 360)
    rect_target = (820, 375, 1582, 545)
    rect_bev = (18, 560, 805, 882)
    rect_value = (820, 560, 1190, 882)
    rect_authority = (1205, 560, 1582, 882)
    _box(canvas, rect_rgb, "WHAT DOES THE CAR SEE?  —  REAL MODEL RGB_0")
    _box(canvas, rect_language, "WHAT ARE THE POSSIBLE MEANINGS?  —  LANGUAGE / GROUNDING")
    _box(canvas, rect_target, "TOPOLOGY / TARGET GROUNDING  —  REFERENT → JUNCTION → BRANCH")
    _box(canvas, rect_bev, "HOW WOULD EACH MEANING CHANGE THE DRIVE?  —  CANDIDATE PLAN BEV")
    _box(canvas, rect_value, "WHY ACT / ASK / WAIT?  —  CONSEQUENCE / VALUE")
    _box(canvas, rect_authority, "WHO IS ACTUALLY CONTROLLING THE CAR?  —  AUTHORITY")

    available_h = rect_rgb[3] - rect_rgb[1] - 48
    available_w = rect_rgb[2] - rect_rgb[0] - 24
    scale = min(available_w / width, available_h / height)
    resized = cv2.resize(front, (max(1, int(width * scale)), max(1, int(height * scale))))
    iy = rect_rgb[1] + 41 + (available_h - resized.shape[0]) // 2
    ix = rect_rgb[0] + 12 + (available_w - resized.shape[1]) // 2
    canvas[iy : iy + resized.shape[0], ix : ix + resized.shape[1]] = resized

    gx, gy = rect_language[0] + 15, rect_language[1] + 55
    _put(canvas, "Raw instruction:", gx, gy, color=(142, 158, 176), scale=0.4)
    for line in _lines(instruction, 78)[:2]:
        gy += 23
        _put(canvas, line, gx, gy, scale=0.46, thickness=1)
    gy += 29
    raw_k = (grounding or {}).get("raw_grounding_k", len(candidates))
    effective_k = (
        (decision_window or {}).get("effective_k")
        if (decision_window or {}).get("method_v1_dashboard")
        else (grounding or {}).get("effective_k", len(candidates))
    )
    _put(canvas, "Ambiguity: {}     RAW K: {}     EFFECTIVE K: {}".format(ambiguity_type, raw_k, effective_k), gx, gy, color=decision_color, scale=0.43, thickness=2)
    gy += 28
    for index, candidate in enumerate(candidates[:2]):
        label = chr(ord("A") + index)
        descriptor = candidate.get("display_descriptor") or candidate.get("semantic_constraint") or candidate.get("referring_expression") or candidate.get("referent_phrase") or candidate.get("referent_id")
        _put(canvas, "{}  {}   conf={}   track={}".format(label, _short(descriptor, 37), _short(candidate.get("detector_confidence") or candidate.get("confidence"), 7), _short(candidate.get("track_id"), 13)), gx, gy, color=_candidate_color(index), scale=0.42, thickness=2)
        gy += 24
    _put(canvas, "Semantic duplicate: {}    Grounding duplicate: {}".format(str((grounding or {}).get("semantic_duplicate", False)).upper(), str((grounding or {}).get("grounding_duplicate", False)).upper()), gx, rect_language[3] - 17, color=(145, 159, 174), scale=0.38)

    tx, ty = rect_target[0] + 15, rect_target[1] + 51
    opportunity_text = "   ".join(
        "J{}:{}".format(
            item.get("route_order_index", item.get("route_opportunity_index", "?")),
            _short(item.get("maneuver_direction") or item.get("road_option"), 8),
        )
        for item in topology[:3]
        if isinstance(item, Mapping)
    )
    if opportunity_text:
        _put(canvas, "LEGAL OPPORTUNITIES  " + opportunity_text, tx, ty, color=(156, 171, 188), scale=0.35)
        ty += 20
    for index, candidate in enumerate(candidates[:2]):
        col = tx + index * 375
        _put(canvas, "Candidate " + chr(ord("A") + index), col, ty, color=_candidate_color(index), scale=0.45, thickness=2)
        fields = (
            ("Semantic", candidate.get("semantic_constraint")),
            ("Referent", candidate.get("referent_id")),
            ("Junction", candidate.get("junction_id")),
            ("Target", candidate.get("target_id")),
            ("Branch", candidate.get("branch_id")),
        )
        for row_index, (name, value) in enumerate(fields, start=1):
            unresolved = value in (None, "", "UNKNOWN") and name == "Target"
            _put(canvas, name + ": " + ("TARGET UNRESOLVED" if unresolved else _short(value, 29)), col, ty + row_index * 21, color=(65, 88, 255) if unresolved else (207, 215, 224), scale=0.37)

    _bev(canvas, rect_bev, plans, candidates, topology)
    fields = _decision_fields(decision_receipt)
    decision_window = dict(decision_window or {})
    vx, vy = rect_value[0] + 15, rect_value[1] + 56
    if decision_window.get("method_v1_dashboard"):
        value_lines = (
            "Run: " + _short(decision_window.get("run_id"), 24),
            "Planning: " + _short(decision_window.get("planning_event_id"), 25),
            "K raw/effective: {}/{}  state={}".format(
                _short(decision_window.get("raw_k"), 4),
                _short(decision_window.get("effective_k"), 4),
                _short(decision_window.get("ambiguity_state"), 13),
            ),
            "Relationship: " + _short(decision_window.get("current_relation"), 25),
            "Decision/reason: {}/{}".format(
                _short(decision_window.get("decision"), 10),
                _short(decision_window.get("decision_reason"), 18),
            ),
            "Subject: " + _short(decision_window.get("decision_subject"), 28),
            "Bundle: " + _short(decision_window.get("bundle_version"), 29),
            "Plan: " + _short(decision_window.get("plan_id"), 31),
            "Control: " + _short(decision_window.get("control_source"), 28),
            "Authority: " + _short(decision_window.get("authority_subject"), 26),
            "Freshness: " + _short(decision_window.get("freshness"), 27),
            "Forwards: " + _short(decision_window.get("forward_counters"), 31),
        )
    elif decision_window:
        value_lines = (
            "Run: " + _short(decision_window.get("run_id"), 24),
            "Ego progress: " + _short(decision_window.get("ego_route_progress"), 18) + " m_route",
            "Plan arc A/B: {}/{} m".format(
                _short(decision_window.get("plan_A_arc_m"), 9),
                _short(decision_window.get("plan_B_arc_m"), 9),
            ),
            "Coverage: " + _short(decision_window.get("plan_coverage"), 25),
            "Shared corridor: " + _short(decision_window.get("shared_corridor"), 20),
            "Maneuver onset: " + _short(decision_window.get("maneuver_onset"), 20),
            "Decision point: " + _short(decision_window.get("decision_point"), 20),
            "Commitment: " + _short(decision_window.get("commitment_boundary"), 23),
            "Recoverability: " + _short(decision_window.get("recoverability"), 20),
            "TTD / latest-safe: {} / {}".format(
                _short(decision_window.get("time_to_divergence"), 10),
                _short(decision_window.get("latest_safe_clarification"), 10),
            ),
            "Current relation: " + _short(decision_window.get("current_relation"), 20),
            "Reason: " + _short(decision_window.get("reason_code"), 27),
        )
    else:
        consequence_a = "same executable outcome" if decision == "ACT" else "candidate-specific target/plan"
        consequence_b = "same executable outcome" if decision == "ACT" else "alternative target/plan"
        value_lines = (
            "A consequence: " + consequence_a,
            "B consequence: " + consequence_b,
            "Material divergence: " + ("YES" if fields.get("consequence_difference") else "NO"),
            "Answer changes action: " + str(bool(fields.get("answer_changes_action"))).upper(),
            "Future scene info expected: " + str(bool(fields.get("future_information"))).upper(),
            "Information source: " + str(fields.get("information_source", "UNKNOWN")),
            "ACT value: " + _short(fields.get("act_value"), 12),
            "ASK/query value: " + _short(fields.get("query_value"), 12),
            "WAIT value: " + _short(fields.get("wait_value"), 12),
            "FINAL DECISION: " + decision,
        )
    for text in value_lines:
        _put(canvas, text, vx, vy, color=decision_color if text.startswith("FINAL") else (213, 220, 228), scale=0.39, thickness=2 if text.startswith("FINAL") else 1)
        vy += 21 if decision_window else 26
    if interaction:
        for text in (
            "QUESTION: " + _short(interaction.get("question"), 44),
            "ANSWER: " + _short(interaction.get("answer", "PENDING"), 44),
            "STATE: " + _short(interaction.get("state"), 44),
        ):
            _put(canvas, text, vx, min(rect_value[3] - 12, vy), color=(171, 190, 212), scale=0.34)
            vy += 20

    authority = dict(authority or {})
    ax, ay = rect_authority[0] + 15, rect_authority[1] + 57
    for text in (
        *( (
            "RUN ID: " + _short(decision_window.get("run_id"), 28),
            "SOURCE FRAME: " + _short(decision_window.get("source_frame"), 20),
            "FORWARDS: " + _short(decision_window.get("forward_counters"), 34),
        ) if decision_window else () ),
        "PLAN SOURCE: " + _short(authority.get("plan_source", "CURRENT / AUTHORIZED PLAN"), 31),
        "CONTROL OWNER: " + _short(authority.get("control_owner", "EXISTING SIMLINGO PID"), 31),
        "Receipt: " + _short(authority.get("receipt_status", runtime_status), 35),
        "Existing PID count: " + _short(authority.get("existing_pid_count"), 12),
        "New PID count: " + _short(authority.get("new_pid_count", 0), 12),
        "Candidate direct writes: " + _short(authority.get("candidate_direct_writes", 0), 12),
        "M3 direct writes: " + _short(authority.get("m3_direct_writes", 0), 12),
        "Actual steer: " + _short(authority.get("steer"), 12),
        "Throttle: " + _short(authority.get("throttle"), 12),
        "Brake: " + _short(authority.get("brake"), 12),
        "Visualization extra forwards/PID/control: 0 / 0 / 0",
    ):
        _put(canvas, text, ax, ay, color=(211, 219, 227), scale=0.38)
        ay += 25

    output_dir.mkdir(parents=True, exist_ok=True)
    panel_path = output_dir / "GROUNDED_LANGUAGE_V1_PANEL.png"
    if not cv2.imwrite(str(panel_path), canvas):
        return {"rendered": False, "native_refreshed": False, "error": "PANEL_WRITE_FAILED"}
    state_name = re.sub(r"[^A-Z0-9_-]+", "_", str(runtime_status or decision).upper()).strip("_")[:80]
    state_dir = output_dir / "visual_timeline"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / (state_name + ".png")
    if not state_path.exists():
        cv2.imwrite(str(state_path), canvas)

    refreshed = False
    cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
    # Physical 2560x1440 layout: dashboard left, Carla native window right.
    # The saved evidence panel retains the full 1600x900 resolution.
    cv2.resizeWindow(WINDOW_TITLE, 1240, 700)
    cv2.moveWindow(WINDOW_TITLE, 0, 40)
    cv2.imshow(WINDOW_TITLE, canvas)
    cv2.waitKey(50)
    refreshed = True
    return {
        "rendered": True,
        "native_refreshed": refreshed,
        "error": None,
        "panel_path": str(panel_path),
        "timeline_path": str(state_path),
    }


def close_native_window() -> None:
    if not _truthy(os.environ.get(VISUALIZATION_ENV)):
        return
    try:
        import cv2

        cv2.destroyWindow(WINDOW_TITLE)
        cv2.waitKey(1)
    except Exception:
        pass


__all__ = ["WINDOW_TITLE", "close_native_window", "render_panel"]
