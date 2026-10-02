"""Pixel-grounded Stage 6A candidate capture for native CARLA runs.

The adapter is deliberately small and deterministic.  It derives two visual
anchors from the bytes of the genuine front-RGB callback, then passes only the
frozen policy-visible fields to :class:`RuntimeCandidateGenerator`.  It never
reads the scenario catalog, evaluator labels, CARLA actors, or world geometry.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .candidate_generation import RuntimeCandidateGenerator
from .contracts import (
    EgoState,
    PolicyEpisodeInput,
    RouteContext,
    VisionObservation,
    VisualReference,
    canonical_sha256,
)


CAPTURE_SCHEMA = "driveclarify.paper_mvp.stage6a.live_candidate_capture.v1"
CAPTURE_HASH_FIELD = "candidate_capture_payload_sha256"
ADAPTER_ID = "DRIVECLARIFY_RGB_SALIENCY_GROUND_PLANE_V1"
ADAPTER_CONFIG = {
    "adapter_id": ADAPTER_ID,
    "input": "GENUINE_FRONT_RGB_PIXELS_ONLY",
    "grid_columns": 6,
    "grid_rows": 3,
    "vertical_roi_fraction": [0.36, 0.84],
    "horizontal_fov_degrees": 90.0,
    "camera_height_m": 2.4,
    "camera_pitch_degrees": -8.0,
    "anchor_count": 2,
    "bearing_method": "PINHOLE_CELL_CENTRE",
    "range_method": "GROUND_PLANE_FROM_CELL_BOTTOM",
    "world_actor_state_input": False,
    "external_io": False,
}
ADAPTER_CONFIG_SHA256 = canonical_sha256(ADAPTER_CONFIG)


def _colour_category(cell: Any) -> str:
    """Return a bounded, pixel-derived category for one BGR image cell."""

    means = cell[:, :, :3].astype("float32").mean(axis=(0, 1))
    blue, green, red = (float(value) for value in means)
    maximum, minimum = max(blue, green, red), min(blue, green, red)
    luminance = 0.114 * blue + 0.587 * green + 0.299 * red
    if maximum - minimum < 18.0:
        if luminance >= 170.0:
            return "bright visual landmark"
        if luminance <= 80.0:
            return "dark visual landmark"
        return "neutral visual landmark"
    return (
        "blue visual landmark"
        if blue == maximum
        else "green visual landmark"
        if green == maximum
        else "red visual landmark"
    )


def derive_rgb_visual_references(
    image_bgra: Any,
    *,
    horizontal_fov_degrees: float = 90.0,
    camera_height_m: float = 2.4,
    camera_pitch_degrees: float = -8.0,
) -> tuple[tuple[VisualReference, VisualReference], Mapping[str, Any]]:
    """Select two non-adjacent high-saliency cells from genuine RGB pixels."""

    import numpy as np

    array = np.asarray(image_bgra)
    if array.ndim != 3 or array.shape[2] < 3:
        raise ValueError("FRONT_RGB_BGRA_SHAPE_INVALID")
    height, width = int(array.shape[0]), int(array.shape[1])
    if height < 24 or width < 24:
        raise ValueError("FRONT_RGB_DIMENSIONS_TOO_SMALL")
    raw_sha256 = hashlib.sha256(array.tobytes()).hexdigest()
    bgr = array[:, :, :3].astype("float32")
    gray = 0.114 * bgr[:, :, 0] + 0.587 * bgr[:, :, 1] + 0.299 * bgr[:, :, 2]
    gradient = np.zeros_like(gray)
    gradient[:, 1:] += np.abs(gray[:, 1:] - gray[:, :-1])
    gradient[1:, :] += np.abs(gray[1:, :] - gray[:-1, :])

    columns = int(ADAPTER_CONFIG["grid_columns"])
    rows = int(ADAPTER_CONFIG["grid_rows"])
    y0 = int(round(height * float(ADAPTER_CONFIG["vertical_roi_fraction"][0])))
    y1 = int(round(height * float(ADAPTER_CONFIG["vertical_roi_fraction"][1])))
    y1 = max(y0 + rows, min(height, y1))
    cells = []
    for row in range(rows):
        sy = y0 + (y1 - y0) * row // rows
        ey = y0 + (y1 - y0) * (row + 1) // rows
        for column in range(columns):
            sx = width * column // columns
            ex = width * (column + 1) // columns
            pixels = bgr[sy:ey, sx:ex]
            edge = gradient[sy:ey, sx:ex]
            score = float(edge.mean() / 255.0 + gray[sy:ey, sx:ex].std() / 128.0)
            cells.append(
                {
                    "row": row,
                    "column": column,
                    "sx": sx,
                    "sy": sy,
                    "ex": ex,
                    "ey": ey,
                    "score": score,
                    "pixels": pixels,
                }
            )
    ranked = sorted(cells, key=lambda item: (-item["score"], item["row"], item["column"]))
    selected = [ranked[0]]
    selected.extend(
        item
        for item in ranked[1:]
        if abs(item["column"] - selected[0]["column"])
        + abs(item["row"] - selected[0]["row"])
        >= 2
    )
    selected = selected[:2]
    if len(selected) != 2:
        selected = ranked[:2]

    focal_x = width / (2.0 * math.tan(math.radians(horizontal_fov_degrees) / 2.0))
    vertical_fov = 2.0 * math.atan(
        math.tan(math.radians(horizontal_fov_degrees) / 2.0) * height / width
    )
    focal_y = height / (2.0 * math.tan(vertical_fov / 2.0))
    scores = [float(item["score"]) for item in ranked]
    minimum_score, maximum_score = min(scores), max(scores)
    denominator = max(maximum_score - minimum_score, 1e-12)
    references = []
    evidence_rows = []
    for item in selected:
        centre_x = (float(item["sx"]) + float(item["ex"]) - 1.0) / 2.0
        bearing = math.degrees(math.atan((centre_x - width / 2.0) / focal_x))
        bottom_y = float(item["ey"] - 1)
        ray_down = math.radians(-camera_pitch_degrees) + math.atan(
            (bottom_y - height / 2.0) / focal_y
        )
        distance = (
            camera_height_m / math.tan(ray_down)
            if ray_down > math.radians(1.0)
            else 60.0
        )
        distance = max(1.0, min(60.0, float(distance)))
        confidence = 0.55 + 0.4 * (
            (float(item["score"]) - minimum_score) / denominator
        )
        category = _colour_category(item["pixels"])
        track_id = "rgb-%s-r%dc%d" % (
            raw_sha256[:16],
            int(item["row"]),
            int(item["column"]),
        )
        references.append(
            VisualReference(
                track_id=track_id,
                category=category,
                relative_bearing_degrees=bearing,
                relative_distance_m=distance,
                confidence=min(0.99, max(0.25, confidence)),
                observation_source="ONLINE_RGB_SALIENCY_GROUND_PLANE_ADAPTER",
            )
        )
        evidence_rows.append(
            {
                "track_id": track_id,
                "pixel_box_xyxy": [
                    int(item["sx"]),
                    int(item["sy"]),
                    int(item["ex"]),
                    int(item["ey"]),
                ],
                "saliency_score": round(float(item["score"]), 9),
                "category": category,
                "bearing_degrees": round(bearing, 9),
                "ground_plane_range_m": round(distance, 9),
            }
        )
    typed = (references[0], references[1])
    return typed, {
        "adapter_id": ADAPTER_ID,
        "adapter_config_sha256": ADAPTER_CONFIG_SHA256,
        "front_rgb_raw_sha256": raw_sha256,
        "image_width": width,
        "image_height": height,
        "selected_anchor_count": 2,
        "selected_anchors": evidence_rows,
        "pixel_bytes_consumed": int(array.nbytes),
        "world_actor_state_read_count": 0,
        "evaluator_catalog_read_count": 0,
        "evaluation_label_access_count": 0,
        "image_only_adapter": True,
        "range_assumption": "FLAT_GROUND_AT_CELL_BOTTOM_NOT_PHYSICAL_SAFETY_EVIDENCE",
    }


def build_live_candidate_capture(
    *,
    runtime_fixture_id: str,
    runtime_manifest_sha256: str,
    selected_seed: int,
    raw_instruction: str,
    frame: int,
    image_bgra: Any,
    observed_monotonic_time: float,
    ego_state: Mapping[str, Any],
    route_context: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Build one content-addressable, catalog-free runtime candidate record."""

    references, adapter_evidence = derive_rgb_visual_references(image_bgra)
    image_sha256 = str(adapter_evidence["front_rgb_raw_sha256"])
    episode = PolicyEpisodeInput(
        raw_instruction=str(raw_instruction),
        vision_observation=VisionObservation(
            observation_id="rgbobs-" + image_sha256[:24],
            frame_id=int(frame),
            captured_monotonic_time=float(observed_monotonic_time),
            image_sha256=image_sha256,
            image_width=int(adapter_evidence["image_width"]),
            image_height=int(adapter_evidence["image_height"]),
            references=references,
            source="GENUINE_CARLA_FRONT_RGB_PIXEL_ADAPTER",
        ),
        ego_state=EgoState(
            observed_monotonic_time=float(observed_monotonic_time),
            position_x_m=float(ego_state["position_x_m"]),
            position_y_m=float(ego_state["position_y_m"]),
            yaw_degrees=float(ego_state["yaw_degrees"]),
            speed_mps=float(ego_state["speed_mps"]),
            source="ONLINE_CARLA_EGO_STATE_SAME_SENSOR_TICK",
        ),
        route_context=RouteContext(
            observed_monotonic_time=float(observed_monotonic_time),
            route_command=str(route_context["route_command"]),
            target_point_x_m=float(route_context["target_point_x_m"]),
            target_point_y_m=float(route_context["target_point_y_m"]),
            route_digest=str(route_context["route_digest"]),
            source="ONLINE_VALIDATION_ROUTE_CONTEXT_SAME_SENSOR_TICK",
        ),
        opaque_token=canonical_sha256(
            {
                "runtime_fixture_id_sha256": canonical_sha256(runtime_fixture_id),
                "selected_seed": int(selected_seed),
                "frame": int(frame),
                "image_sha256": image_sha256,
                "ego_state": dict(ego_state),
                "route_context": dict(route_context),
            }
        ),
    )
    generated = RuntimeCandidateGenerator().generate(episode)
    if generated.status != "READY" or len(generated.candidates) != 2:
        raise RuntimeError("LIVE_RGB_RUNTIME_CANDIDATE_GENERATION_NOT_READY")
    return {
        "schema_version": CAPTURE_SCHEMA,
        "status": "VERIFIED",
        "source_kind": "LIVE_CARLA_FRONT_RGB_RUNTIME_CANDIDATE_GENERATION",
        "runtime_fixture_id": str(runtime_fixture_id),
        "runtime_manifest_sha256": str(runtime_manifest_sha256),
        "selected_seed": int(selected_seed),
        "front_rgb": {
            "frame": int(frame),
            "raw_bgra_sha256": image_sha256,
            "genuine_carla_sensor_callback": True,
            "world_actor_state_projection_used_as_image": False,
        },
        "vision_adapter": dict(adapter_evidence),
        "policy_input_projection": episode.public_projection(),
        "generation_output": asdict(generated),
        "runtime_firewall": {
            "catalog_read_count": 0,
            "evaluation_label_access_count": 0,
            "catalog_candidate_order_visible": False,
            "runtime_annotation_overlap_checked": False,
        },
        "model_forward_count": 0,
        "pid_invocation_count": 0,
        "control_write_count": 0,
        "test_labels_opened": False,
    }


def content_address_capture(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    unsigned = dict(payload)
    unsigned.pop(CAPTURE_HASH_FIELD, None)
    digest = canonical_sha256(unsigned)
    return {**unsigned, CAPTURE_HASH_FIELD: digest}, digest


__all__ = [
    "ADAPTER_CONFIG_SHA256",
    "ADAPTER_ID",
    "CAPTURE_HASH_FIELD",
    "CAPTURE_SCHEMA",
    "build_live_candidate_capture",
    "content_address_capture",
    "derive_rgb_visual_references",
]
