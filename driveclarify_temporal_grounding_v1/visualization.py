"""Temporal panel renderer over detached display copies only."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple


def render_temporal_panel(
    output_dir: Path,
    image: Any,
    *,
    instruction: str,
    track: Any,
    trail: Sequence[Tuple[float, float]],
    event: Any,
    region: Any,
    target: Any,
    information_changed: bool,
    replan_required: bool,
    decision: str,
) -> Optional[str]:
    if image is None:
        return None
    import cv2
    import numpy as np

    front = np.ascontiguousarray(image[:, :, :3]).copy()
    height, width = front.shape[:2]
    rx0, ry0, rx1, ry1 = (int(round(item)) for item in region.bbox_xyxy)
    cv2.rectangle(front, (rx0, ry0), (rx1, ry1), (80, 120, 250), 2)
    x0, y0, x1, y1 = (int(round(item)) for item in track.bbox_xyxy)
    color = (50, 230, 120) if event.state.value == "CLEARED" else (40, 220, 235)
    cv2.rectangle(front, (x0, y0), (x1, y1), color, 3)
    cv2.putText(
        front,
        "{} {}".format(track.track_id, event.state.value),
        (x0, max(20, y0 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        color,
        2,
        cv2.LINE_AA,
    )
    points = [(int(round(x)), int(round(y))) for x, y in trail[-12:]]
    for first, second in zip(points, points[1:]):
        cv2.line(front, first, second, (0, 180, 255), 2, cv2.LINE_AA)

    panel_width = 700
    canvas = np.zeros((max(650, height), width + panel_width, 3), dtype=np.uint8)
    canvas[:height, :width] = front
    x, y = width + 18, 30

    def line(text: str, color_value=(230, 235, 240), scale=0.43, gap=25) -> None:
        nonlocal y
        cv2.putText(
            canvas,
            str(text)[:96],
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            color_value,
            1,
            cv2.LINE_AA,
        )
        y += gap

    line("TEMPORAL GROUNDING", (90, 235, 245), 0.62, 34)
    line("Instruction: " + instruction)
    line("Grounded referent: " + track.phrase.upper())
    line("Track: " + track.track_id)
    line("Track lifecycle: " + track.lifecycle_state.value)
    line("State: " + event.state.value, color)
    line("Track confidence: {:.3f}".format(track.confidence))
    line("Event confidence: {:.3f}".format(event.confidence))
    line("Event overlap: {:.3f}".format(event.overlap_fraction))
    line("Event region: " + region.region_id)
    line("Target: " + target.branch_id)
    line("Target option: " + target.road_option)
    line("Information changed: " + str(bool(information_changed)), (145, 190, 235))
    line("Replan required: " + str(bool(replan_required)), (145, 190, 235))
    line("Decision: " + decision, (90, 235, 245))
    line("Extra detector forward (viz): 0")
    line("Extra SimLingo forward (viz): 0")
    line("Extra PID (viz): 0")
    line("Control mutation (viz): 0")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "TEMPORAL_GROUNDING_V1_PANEL.png"
    cv2.imwrite(str(path), canvas)
    return str(path)


__all__ = ["render_temporal_panel"]
