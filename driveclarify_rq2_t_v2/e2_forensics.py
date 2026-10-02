"""Post-episode-only E2 forensic helpers.

This module is intentionally not imported by the runtime provider.  Privileged
actor geometry is accepted only by functions in this file and is never emitted
as a runtime signal or provider payload.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence


def _matmul(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> list[list[float]]:
    return [
        [sum(float(left[i][k]) * float(right[k][j]) for k in range(4)) for j in range(4)]
        for i in range(4)
    ]


def _rigid_inverse(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    rotation = [[float(matrix[i][j]) for j in range(3)] for i in range(3)]
    translation = [float(matrix[i][3]) for i in range(3)]
    inverse = [[0.0] * 4 for _ in range(4)]
    inverse[3][3] = 1.0
    for i in range(3):
        for j in range(3):
            inverse[i][j] = rotation[j][i]
        inverse[i][3] = -sum(rotation[j][i] * translation[j] for j in range(3))
    return inverse


def _transform_point(matrix: Sequence[Sequence[float]], point: Sequence[float]) -> list[float]:
    value = [float(point[0]), float(point[1]), float(point[2]), 1.0]
    return [sum(float(matrix[i][j]) * value[j] for j in range(4)) for i in range(4)]


def _rotation_matrix(rotation_rpy_deg: Sequence[float]) -> list[list[float]]:
    pitch, yaw, roll = [math.radians(float(value)) for value in rotation_rpy_deg]
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cr, sr = math.cos(roll), math.sin(roll)
    return [
        [cp * cy, cy * sp * sr - sy * cr, -cy * sp * cr - sy * sr],
        [cp * sy, sy * sp * sr + cy * cr, -sy * sp * cr + cy * sr],
        [sp, -cp * sr, cp * cr],
    ]


def camera_world_matrix(
    ego_matrix: Sequence[Sequence[float]],
    *,
    mount_x_m: float = 1.5,
    mount_y_m: float = 0.0,
    mount_z_m: float = 2.0,
) -> list[list[float]]:
    """Return CARLA camera-to-world using the recorded ego transform and mount."""

    mount = [
        [1.0, 0.0, 0.0, float(mount_x_m)],
        [0.0, 1.0, 0.0, float(mount_y_m)],
        [0.0, 0.0, 1.0, float(mount_z_m)],
        [0.0, 0.0, 0.0, 1.0],
    ]
    return _matmul(ego_matrix, mount)


def actor_bbox_world_corners(actor: Mapping[str, Any]) -> list[list[float]]:
    """Expand a recorded CARLA actor bounding box into eight world-space corners."""

    location = [float(value) for value in actor["location_xyz"]]
    bbox = actor["bbox"]
    offset = [float(value) for value in bbox["location_xyz"]]
    extent = [float(value) for value in bbox["extent_xyz"]]
    rotation = _rotation_matrix(actor["rotation_rpy_deg"])

    def rotate(vector: Sequence[float]) -> list[float]:
        return [sum(rotation[i][j] * float(vector[j]) for j in range(3)) for i in range(3)]

    center_offset = rotate(offset)
    center = [location[i] + center_offset[i] for i in range(3)]
    corners = []
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                local = [extent[0] * sx, extent[1] * sy, extent[2] * sz]
                rotated = rotate(local)
                corners.append([center[i] + rotated[i] for i in range(3)])
    return corners


def project_actor_bbox(
    world_row: Mapping[str, Any], actor_id: int
) -> Mapping[str, Any]:
    """Project recorded gold geometry without exposing it to the E2 runtime."""

    actors = world_row.get("actors", {}).get("actors", ())
    actor = next((value for value in actors if int(value.get("id", -1)) == int(actor_id)), None)
    if actor is None:
        return {
            "status": "UNKNOWN",
            "reason_code": "TARGET_ACTOR_NOT_IN_RECORDED_ROI",
            "actor_id": int(actor_id),
            "bbox_xyxy": None,
            "in_frustum": None,
        }
    camera = world_row["camera"]
    width, height = int(camera["width"]), int(camera["height"])
    intrinsic = camera["K_3x3"]
    world_to_camera = _rigid_inverse(camera_world_matrix(world_row["ego"]["matrix_4x4"]))
    projected = []
    depths = []
    for corner in actor_bbox_world_corners(actor):
        cam_x, cam_y, cam_z, _ = _transform_point(world_to_camera, corner)
        depths.append(cam_x)
        if cam_x > 1e-6:
            projected.append(
                [
                    float(intrinsic[0][2]) + float(intrinsic[0][0]) * cam_y / cam_x,
                    float(intrinsic[1][2]) - float(intrinsic[1][1]) * cam_z / cam_x,
                ]
            )
    if not projected:
        return {
            "status": "AVAILABLE_POST_EPISODE_GOLD",
            "reason_code": "TARGET_BEHIND_CAMERA",
            "actor_id": int(actor_id),
            "bbox_xyxy": None,
            "in_frustum": False,
            "depth_range_m": [min(depths), max(depths)],
        }
    bbox = [
        min(value[0] for value in projected),
        min(value[1] for value in projected),
        max(value[0] for value in projected),
        max(value[1] for value in projected),
    ]
    clipped = [
        max(0.0, min(float(width), bbox[0])),
        max(0.0, min(float(height), bbox[1])),
        max(0.0, min(float(width), bbox[2])),
        max(0.0, min(float(height), bbox[3])),
    ]
    pixel_width = max(0.0, clipped[2] - clipped[0])
    pixel_height = max(0.0, clipped[3] - clipped[1])
    in_frustum = bool(
        max(depths) > 0.0
        and clipped[2] > clipped[0]
        and clipped[3] > clipped[1]
        and bbox[2] >= 0.0
        and bbox[0] <= width
        and bbox[3] >= 0.0
        and bbox[1] <= height
    )
    return {
        "status": "AVAILABLE_POST_EPISODE_GOLD",
        "reason_code": None if in_frustum else "TARGET_OUTSIDE_CAMERA_FRUSTUM",
        "actor_id": int(actor_id),
        "bbox_xyxy": bbox,
        "clipped_bbox_xyxy": clipped,
        "in_frustum": in_frustum,
        "image_edge_clipped": bbox != clipped,
        "pixel_width": pixel_width,
        "pixel_height": pixel_height,
        "pixel_area": pixel_width * pixel_height,
        "depth_range_m": [min(depths), max(depths)],
    }


def top_two(scores: Sequence[Optional[float]]) -> Mapping[str, Optional[float]]:
    values = sorted((float(value) for value in scores if value is not None), reverse=True)
    top1 = values[0] if values else None
    top2 = values[1] if len(values) > 1 else None
    return {
        "top1": top1,
        "top2": top2,
        "margin": None if top1 is None or top2 is None else top1 - top2,
    }


def null_candidate_track_matrix(
    candidate_ids: Sequence[str], track_ids: Sequence[str], *, reason_code: str
) -> Mapping[str, Any]:
    """Represent an unavailable association matrix without inventing zero scores."""

    return {
        "candidate_ids": [str(value) for value in candidate_ids],
        "track_ids": [str(value) for value in track_ids],
        "scores": {
            str(candidate_id): {str(track_id): None for track_id in track_ids}
            for candidate_id in candidate_ids
        },
        "status": "UNKNOWN",
        "reason_code": str(reason_code),
    }


def confidence_provenance(native_runtime_source: str) -> Mapping[str, Any]:
    substitution = '"identity_confidence": detected.get("detector_confidence")' in native_runtime_source
    return {
        "variable_compared_to_0_80": "candidate_groundings[*].identity_confidence",
        "runtime_assignment": "selected_referent.detector_confidence" if substitution else None,
        "detector_confidence_substituted_for_identity_confidence": substitution,
        "calibrated_identity_probability": False if substitution else None,
        "classification": "IDENTITY_CONFIDENCE_PROVENANCE_INVALID" if substitution else "UNKNOWN",
    }


def first_failing_stage(row: Mapping[str, Any]) -> str:
    """Return the first explicit failure while preserving null as UNKNOWN."""

    if row.get("camera_frame_present") is False:
        return "CAMERA_FRAME_MISSING"
    if row.get("camera_frame_present") is None:
        return "UNKNOWN_AT_STAGE:camera_frame_present"
    if row.get("target_gold_in_camera_frustum_posthoc") is False:
        return "TARGET_OUTSIDE_CAMERA_FRUSTUM"
    if row.get("target_gold_in_camera_frustum_posthoc") is None:
        return "UNKNOWN_AT_STAGE:target_gold_in_camera_frustum_posthoc"
    if row.get("target_gold_occluded_posthoc") is True:
        return "TARGET_PHYSICALLY_OCCLUDED"
    if row.get("target_gold_occluded_posthoc") is None:
        return "UNKNOWN_AT_STAGE:target_gold_occluded_posthoc"
    ordered = (
        ("raw_target_detection_hit", "RAW_TARGET_DETECTOR_MISS"),
        ("persistent_track_present", "TRACK_NOT_CREATED"),
        ("language_parse_success", "LANGUAGE_CANDIDATE_PARSE_FAILURE"),
        ("association_generated", "CANDIDATE_TO_TRACK_ASSOCIATION_FAILURE"),
        ("binding_unique", "ASSOCIATION_NOT_UNIQUE"),
        ("identity_confidence_valid", "IDENTITY_CONFIDENCE_PROVENANCE_INVALID"),
        ("frozen_threshold_pass", "FROZEN_CONFIDENCE_THRESHOLD_NOT_MET"),
        ("lineage_complete", "LINEAGE_INCOMPLETE"),
        ("provider_input_complete", "PROVIDER_DEPENDENCY_MISSING"),
        ("provider_available", "PROVIDER_REJECTED"),
        ("adapter_authorized", "ADAPTER_AUTHORIZATION_REJECTION"),
        ("serialized_available", "SERIALIZATION_DEFECT"),
    )
    for key, reason in ordered:
        value = row.get(key)
        if value is False:
            return reason
        if value is None:
            return "UNKNOWN_AT_STAGE:" + key
    return "AVAILABLE"


def runtime_payload_is_gold_free(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold()
            if normalized.endswith("_gold") or "reveal" in normalized or "true_interpretation" in normalized:
                return False
            if not runtime_payload_is_gold_free(child):
                return False
    elif isinstance(value, (list, tuple)):
        return all(runtime_payload_is_gold_free(child) for child in value)
    return True


def is_unrelated_vscode_copilot_only(process_rows: Sequence[str]) -> bool:
    """Recognize only the exact non-renderer process that caused audit preflight noise."""

    rows = [str(value) for value in process_rows]
    return bool(rows) and all(
        ".vscode-server" in row and "@github/copilot" in row and "--headless" in row
        for row in rows
    )


__all__ = [
    "actor_bbox_world_corners",
    "camera_world_matrix",
    "confidence_provenance",
    "first_failing_stage",
    "null_candidate_track_matrix",
    "is_unrelated_vscode_copilot_only",
    "project_actor_bbox",
    "runtime_payload_is_gold_free",
    "top_two",
]
