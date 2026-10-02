"""Post-episode visibility and candidate/actor assignment certification.

Privileged CARLA actor identities enter only this offline module.  Nothing in
the native E2 provider imports this file or receives its products.
"""

from __future__ import annotations

import hashlib
import json
import math
from itertools import permutations
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from driveclarify_rq2_t.measurement import canonical_sha256


def _rotation_carla(recorded_roll_pitch_yaw_deg: Sequence[float]) -> list[list[float]]:
    roll, pitch, yaw = (math.radians(float(value)) for value in recorded_roll_pitch_yaw_deg)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return [
        [cp * cy, cy * sp * sr - sy * cr, -cy * sp * cr - sy * sr],
        [cp * sy, sy * sp * sr + cy * cr, -sy * sp * cr + cy * sr],
        [sp, -cp * sr, cp * cr],
    ]


def _project_actor(world: Mapping[str, Any], actor_id: int) -> Mapping[str, Any]:
    actor = next(
        (row for row in world.get("actors", {}).get("actors", ()) if int(row.get("id", -1)) == int(actor_id)),
        None,
    )
    if actor is None:
        return {"status": "UNKNOWN", "actor_id": int(actor_id), "reason_code": "ACTOR_OUTSIDE_RECORDED_ROI"}
    camera = world["camera"]
    ego = world["ego"]
    width, height = int(camera["width"]), int(camera["height"])
    intrinsic = camera["K_3x3"]
    ego_matrix = ego["matrix_4x4"]
    # The exact SimLingo rgb_0 mount is recorded in the post-hoc row as
    # [-1.5, 0, 2].  Use the record, not a conventional front-camera guess.
    mount = [float(camera["extrinsics_4x4"][index][3]) for index in range(3)]
    camera_world = [
        float(ego_matrix[index][3])
        + sum(float(ego_matrix[index][axis]) * mount[axis] for axis in range(3))
        for index in range(3)
    ]
    world_to_ego_rotation = [
        [float(ego_matrix[axis][index]) for axis in range(3)] for index in range(3)
    ]
    rotation = _rotation_carla(actor["rotation_rpy_deg"])
    bbox = actor["bbox"]
    location = [float(value) for value in actor["location_xyz"]]
    offset = [float(value) for value in bbox["location_xyz"]]
    extent = [float(value) for value in bbox["extent_xyz"]]
    center = [
        location[index] + sum(rotation[index][axis] * offset[axis] for axis in range(3))
        for index in range(3)
    ]
    projected = []
    depths = []
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                local = [sx * extent[0], sy * extent[1], sz * extent[2]]
                corner = [
                    center[index] + sum(rotation[index][axis] * local[axis] for axis in range(3))
                    for index in range(3)
                ]
                camera_point = [
                    sum(
                        world_to_ego_rotation[index][axis] * (corner[axis] - camera_world[axis])
                        for axis in range(3)
                    )
                    for index in range(3)
                ]
                depths.append(camera_point[0])
                if camera_point[0] > 1e-6:
                    projected.append([
                        float(intrinsic[0][2]) + float(intrinsic[0][0]) * camera_point[1] / camera_point[0],
                        float(intrinsic[1][2]) - float(intrinsic[1][1]) * camera_point[2] / camera_point[0],
                    ])
    if not projected:
        return {
            "status": "AVAILABLE_POST_EPISODE_GOLD", "actor_id": int(actor_id),
            "in_frustum": False, "reason_code": "TARGET_BEHIND_CAMERA",
            "depth_range_m": [min(depths), max(depths)],
        }
    raw = [
        min(row[0] for row in projected), min(row[1] for row in projected),
        max(row[0] for row in projected), max(row[1] for row in projected),
    ]
    clipped = [
        max(0.0, min(float(width), raw[0])), max(0.0, min(float(height), raw[1])),
        max(0.0, min(float(width), raw[2])), max(0.0, min(float(height), raw[3])),
    ]
    raw_area = max(0.0, raw[2] - raw[0]) * max(0.0, raw[3] - raw[1])
    clipped_area = max(0.0, clipped[2] - clipped[0]) * max(0.0, clipped[3] - clipped[1])
    return {
        "status": "AVAILABLE_POST_EPISODE_GOLD", "actor_id": int(actor_id),
        "actor_transform_xyz_roll_pitch_yaw": location + [float(value) for value in actor["rotation_rpy_deg"]],
        "camera_world_location_xyz": camera_world,
        "camera_mount_extrinsics_4x4": camera["extrinsics_4x4"],
        "camera_intrinsics_3x3": intrinsic,
        "camera_width": width, "camera_height": height, "camera_fov_deg": camera.get("fov_deg"),
        "projected_bbox_xyxy": raw, "clipped_bbox_xyxy": clipped,
        "frustum_clipped_fraction": 0.0 if raw_area <= 0.0 else clipped_area / raw_area,
        "in_frustum": clipped_area > 0.0 and max(depths) > 0.0,
        "image_edge_clipped": raw != clipped,
        "pixel_width": max(0.0, clipped[2] - clipped[0]),
        "pixel_height": max(0.0, clipped[3] - clipped[1]),
        "pixel_area": clipped_area, "depth_range_m": [min(depths), max(depths)],
        "reason_code": None if clipped_area > 0.0 else "TARGET_OUTSIDE_CAMERA_FRUSTUM",
    }


def _intersection_fraction(gold_bbox: Sequence[float], observed_bbox: Sequence[float]) -> float:
    intersection = max(0.0, min(gold_bbox[2], observed_bbox[2]) - max(gold_bbox[0], observed_bbox[0])) * max(
        0.0, min(gold_bbox[3], observed_bbox[3]) - max(gold_bbox[1], observed_bbox[1])
    )
    gold_area = max(0.0, gold_bbox[2] - gold_bbox[0]) * max(0.0, gold_bbox[3] - gold_bbox[1])
    return 0.0 if gold_area <= 0.0 else min(1.0, intersection / gold_area)


def _best_distinct_matches(
    candidate_ids: Sequence[str], projections: Mapping[str, Mapping[str, Any]], detections: Sequence[Mapping[str, Any]],
) -> Mapping[str, Mapping[str, Any]]:
    if len(detections) < len(candidate_ids):
        return {}
    best_score = -1.0
    best: Dict[str, Mapping[str, Any]] = {}
    for indexes in permutations(range(len(detections)), len(candidate_ids)):
        current = {}
        score = 0.0
        for candidate_id, detection_index in zip(candidate_ids, indexes):
            projection = projections[candidate_id]
            observed = detections[detection_index]
            fraction = _intersection_fraction(projection.get("clipped_bbox_xyxy") or (0, 0, 0, 0), observed["bbox_xyxy"])
            score += fraction
            current[candidate_id] = {
                "detection_index": int(detection_index),
                "source_detection_id": observed.get("source_detection_id"),
                "source_observation_id": observed.get("source_observation_id"),
                "observed_bbox_xyxy": list(observed["bbox_xyxy"]),
                "detector_support_score": observed.get("detector_support_score"),
                "renderer_visible_fraction": fraction,
            }
        if score > best_score:
            best_score, best = score, current
    return best


def certify_episode(output_dir: Path) -> Mapping[str, Any]:
    output_dir = Path(output_dir)
    scenario = json.loads((output_dir / "E2_V3_SCENARIO_RECEIPT.json").read_text(encoding="utf-8"))
    world_rows = [json.loads(line) for line in (output_dir / "post_hoc_world_state.jsonl").read_text(encoding="utf-8").splitlines()]
    trace_rows = [json.loads(line) for line in (output_dir / "E2_V3_RUNTIME_TRACE.jsonl").read_text(encoding="utf-8").splitlines()]
    paired_rows = [json.loads(line) for line in (output_dir / "E2_V3_PAIRED_VIEW_EVIDENCE.jsonl").read_text(encoding="utf-8").splitlines()]
    world_by_frame = {int(row["gametime_frame"]): row for row in world_rows}
    target_rows = [row for row in scenario.get("spawn_rows", ()) if row.get("candidate_id_posthoc_only")]
    candidate_actor = {str(row["candidate_id_posthoc_only"]): int(row["actor_id"]) for row in target_rows}
    candidate_ids = sorted(candidate_actor)
    effective_reveal_frames = [
        int(row["first_verified_effective_frame"])
        for row in scenario.get("event_rows", ())
        if row.get("event") == "VISIBILITY_REVEAL" and row.get("first_verified_effective_frame") is not None
    ]
    effective_reveal_frame = min(effective_reveal_frames) if effective_reveal_frames else None
    frame_records = []
    renderer_anchor_frames = []
    for trace in trace_rows:
        frame = int(trace["frame_id"])
        world = world_by_frame.get(frame)
        if world is None:
            continue
        projections = {candidate_id: _project_actor(world, actor_id) for candidate_id, actor_id in candidate_actor.items()}
        matches = _best_distinct_matches(candidate_ids, projections, trace.get("detections") or ()) if trace.get("detector_invoked") else {}
        targets = {}
        all_renderer_certified = bool(matches) and len(matches) == len(candidate_ids)
        for candidate_id in candidate_ids:
            projection = projections[candidate_id]
            match = matches.get(candidate_id)
            renderer_fraction = None if match is None else float(match["renderer_visible_fraction"])
            pixel_gate = bool(
                projection.get("pixel_width", 0.0) >= 18.0
                and projection.get("pixel_height", 0.0) >= 12.0
                and projection.get("pixel_area", 0.0) >= 300.0
            )
            renderer_certified = bool(renderer_fraction is not None and renderer_fraction >= 0.65 and pixel_gate)
            all_renderer_certified = all_renderer_certified and renderer_certified
            targets[candidate_id] = {
                "candidate_id": candidate_id, "posthoc_actor_id": candidate_actor[candidate_id],
                "projection": projection, "renderer_match": match,
                "pixel_gate_pass": pixel_gate, "renderer_visibility_gate_pass": renderer_certified,
                "line_of_sight_or_occlusion": (
                    "RENDERER_DETECTOR_CONFIRMED_LINE_OF_SIGHT"
                    if renderer_certified else "NOT_RENDERER_CONFIRMED_AT_THIS_FRAME"
                ),
            }
        if all_renderer_certified:
            renderer_anchor_frames.append(frame)
        frame_records.append({
            "frame_id": frame, "simulation_time_s": float(trace["simulation_time_s"]),
            "observation_id": trace["source_observation_id"],
            "detector_invoked": bool(trace.get("detector_invoked")),
            "targets": targets, "all_targets_renderer_certified": all_renderer_certified,
            "rgb_archive_path": str(output_dir / "RGB_0_ARCHIVE" / ("rgb_0_frame_{:06d}.png".format(frame))),
        })
    sustained = None
    if renderer_anchor_frames:
        for start in renderer_anchor_frames:
            for end in reversed(renderer_anchor_frames):
                if end - start + 1 < 20:
                    continue
                interval = [row for row in frame_records if start <= row["frame_id"] <= end]
                geometry_pass = all(
                    all(
                        target["projection"].get("in_frustum") is True
                        and target["projection"].get("frustum_clipped_fraction", 0.0) >= 0.65
                        and target["pixel_gate_pass"]
                        for target in row["targets"].values()
                    )
                    for row in interval
                )
                if geometry_pass and interval:
                    sustained = {
                        "start_frame": start, "end_frame": end,
                        "sustained_frame_count": end - start + 1,
                        "start_simulation_time_s": interval[0]["simulation_time_s"],
                        "end_simulation_time_s": interval[-1]["simulation_time_s"],
                        "renderer_confirmed_endpoint_frames": [start, end],
                        "intermediate_line_of_sight_basis": "CONTINUOUS_GOLD_FRUSTUM_GEOMETRY_BRACKETED_BY_DISTINCT_RENDERER_DETECTIONS",
                    }
                    break
            if sustained is not None:
                break
    first_visible = min(renderer_anchor_frames) if renderer_anchor_frames else None
    pre_rows = [row for row in frame_records if effective_reveal_frame is not None and row["frame_id"] < effective_reveal_frame]
    pre_false_unique = any(row["all_targets_renderer_certified"] for row in pre_rows)
    e2_frames = [
        int(row["source_identity"]["source_frame_id"])
        for row in paired_rows
        if row["views"]["B1"]["evidence_vector"]["E2_GROUNDING"]["status"] == "AVAILABLE"
    ]
    commitment_states = sorted({str(row["views"]["B0"].get("commitment_state")) for row in paired_rows})
    certificate = {
        "schema_version": "driveclarify.e2_v3.visibility_certificate.v1",
        "identity": output_dir.parent.name, "scene_id": scenario.get("scene_config_id"),
        "privileged_post_episode_grading_only": True, "runtime_import_or_read_count": 0,
        "camera_sensor": "rgb_0", "camera_mount_source": "RECORDED_POST_HOC_EXTRINSICS",
        "candidate_actor_assignment_posthoc": candidate_actor,
        "effective_reveal_frame": effective_reveal_frame,
        "first_physically_visible_and_discriminable_frame": first_visible,
        "renderer_confirmed_anchor_frames": renderer_anchor_frames,
        "sustained_visible_interval": sustained,
        "pre_reveal_false_unique_binding": pre_false_unique,
        "pre_reveal_all_targets_renderer_certified_count": sum(row["all_targets_renderer_certified"] for row in pre_rows),
        "post_reveal_visibility_contract_pass": sustained is not None,
        "first_b1_e2_available_frame": min(e2_frames) if e2_frames else None,
        "e2_available_after_effective_reveal": bool(e2_frames and effective_reveal_frame is not None and min(e2_frames) >= effective_reveal_frame),
        "commitment_states_observed": commitment_states,
        "positive_transition_before_commitment": bool(e2_frames and commitment_states == ["PRECOMMITMENT_UNRESOLVED"]),
        "frame_records": frame_records,
    }
    certificate["visibility_certificate_status"] = (
        "PASS_VISIBILITY_CERTIFIED"
        if certificate["post_reveal_visibility_contract_pass"]
        and not certificate["pre_reveal_false_unique_binding"]
        else "FAIL_VISIBILITY_NOT_CERTIFIED"
    )
    certificate["certificate_status"] = (
        "PASS_VISIBILITY_CERTIFIED_AND_E2_JOINED"
        if certificate["visibility_certificate_status"] == "PASS_VISIBILITY_CERTIFIED"
        and certificate["e2_available_after_effective_reveal"]
        else "FAIL_VISIBILITY_E2_JOIN_NOT_QUALIFIED"
    )
    certificate["certificate_digest"] = canonical_sha256(certificate)
    return certificate


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = ["certify_episode", "sha256"]
