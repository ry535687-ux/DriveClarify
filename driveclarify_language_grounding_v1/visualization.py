"""Display-copy visualization; it never calls a detector or driving model."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional


def render_grounding_panel(
    output_dir: Path,
    image: Any,
    candidate_set: Any,
    *,
    plans: Optional[Mapping[str, Any]],
) -> Optional[str]:
    if image is None:
        return None
    import cv2
    import numpy as np

    front = np.ascontiguousarray(image[:, :, :3]).copy()
    height, width = front.shape[:2]
    for index, referent in enumerate(candidate_set.grounding.selected_referents[:2]):
        color = (40, 220, 235) if index == 0 else (220, 90, 220)
        x0, y0, x1, y1 = (int(round(item)) for item in referent.bbox_xyxy)
        cv2.rectangle(front, (x0, y0), (x1, y1), color, 2)
        cv2.putText(
            front,
            "Candidate {} {:.3f}".format("A" if index == 0 else "B", referent.detector_confidence),
            (x0, max(18, y0 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            color,
            2,
            cv2.LINE_AA,
        )
    panel_w = 650
    canvas = np.zeros((max(height, 620), width + panel_w, 3), dtype=np.uint8)
    canvas[:height, :width] = front
    x = width + 18
    y = 28

    def line(text: str, color=(230, 235, 240), scale=0.43, gap=24) -> None:
        nonlocal y
        cv2.putText(canvas, str(text)[:88], (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)
        y += gap

    line("SCENE GROUNDING / AMBIGUITY", (90, 235, 245), 0.58, 32)
    line("Instruction: " + candidate_set.parsed_slots.raw_instruction)
    line("Action: " + str(candidate_set.parsed_slots.maneuver))
    line("Relation: " + str(candidate_set.parsed_slots.temporal_relation or candidate_set.parsed_slots.spatial_relation))
    line("Referent: " + str(candidate_set.parsed_slots.referent_phrase or candidate_set.parsed_slots.landmark_phrase))
    line("RAW K: {} | EFFECTIVE K: {}".format(candidate_set.raw_k, candidate_set.effective_k), (145, 190, 235))
    line("AMBIGUITY: " + candidate_set.status.value, (145, 190, 235))
    for index, candidate in enumerate(candidate_set.candidates[:2]):
        color = (40, 220, 235) if index == 0 else (220, 90, 220)
        line("Candidate {}: {}".format("A" if index == 0 else "B", candidate.referring_expression), color)
        line("  Prompt: " + candidate.prompt_text, color, 0.36)
    line("Semantic duplicate: " + str(candidate_set.semantic_duplicate))
    line("Grounding duplicate: " + str(candidate_set.grounding_duplicate))
    line("SimLingo prompt divergence: " + str(not candidate_set.prompt_duplicate))
    if plans:
        line("A x {} | B x {}".format(len(plans.get("A", ())), len(plans.get("B", ()))), (90, 235, 245))
        metrics = plans.get("metrics", {})
        channels = metrics.get("channels", {}) if isinstance(metrics, Mapping) else {}
        route = channels.get("route", {}) if isinstance(channels, Mapping) else {}
        speed = channels.get("speed", {}) if isinstance(channels, Mapping) else {}
        if route:
            line(
                "Route divergence: between_min={:.4f}, within_max={:.4f}".format(
                    float(route.get("between_min", 0.0)),
                    float(route.get("within_all_max", 0.0)),
                ),
                (90, 235, 245),
            )
        if speed:
            line(
                "Speed divergence: between_min={:.4f}, within_max={:.4f}".format(
                    float(speed.get("between_min", 0.0)),
                    float(speed.get("within_all_max", 0.0)),
                ),
                (90, 235, 245),
            )
        line("Consequence divergence: NOT EVALUATED (shadow only)", (145, 190, 235))
        line("Visualization-induced forward: 0", (90, 235, 245))
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "LANGUAGE_GROUNDING_V1_PANEL.png"
    cv2.imwrite(str(path), canvas)
    return str(path)


__all__ = ["render_grounding_panel"]
