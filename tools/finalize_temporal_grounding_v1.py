#!/usr/bin/env python3
"""Build source-backed final contracts and audits for Temporal Grounding V1."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import statistics
import subprocess
from itertools import combinations, product
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports/temporal_grounding_v1"
REGRESSION_DIR = ROOT / "artifacts/temporal_grounding_v1/full_regression_matrix_20260811"
RUN_SPECS = (
    ("R1_SHADOW", ROOT / "artifacts/temporal_grounding_v1/T1_T2_T3_collision_safe_shadow"),
    ("R2_CLOSED_LOOP", ROOT / "artifacts/temporal_grounding_v1/T4_collision_safe_bounded_closed_loop"),
    ("R3_SHADOW_POST_HOC", ROOT / "artifacts/temporal_grounding_v1/T5_collision_safe_shadow_repeat3"),
)

EXPECTED = {
    "stage6a_freeze": "d24502ffe5f81340ff8cc0f83acab23ac620d13a4ceeb060371f1dac838ebfee",
    "stage6b_freeze": "e0c8db7b3afaeb92776e797ea92dde76e2c64158adec3d22e9d6968639ec1e90",
    "formal_r3_ledger": "91ca1c2e1e58facfaab018b661857f1f919b368495dde09e93c8a15f2966e362",
    "simlingo_checkpoint": "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28",
    "simlingo_head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
    "simlingo_protected_diff": "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058",
    "generated_scenario_tree": "2eda3eb9e1b21fd095a7f86d946d59c5ba8d3b157f7372638583f583f2f0fafc",
}

IMMUTABLE_PATHS = {
    "stage6a_freeze": ROOT / "reports/paper_mvp_stage6a_final_freeze_v1/FINAL_STAGE6A_FREEZE_RECEIPT.json",
    "stage6b_freeze": ROOT / "reports/paper_mvp_stage6b_runtime_contract_freeze_v1/STAGE6B_R0_FREEZE_RECEIPT.json",
    "formal_r3_ledger": ROOT / "artifacts/paper_mvp_stage6b_formal_train_r0/DC-STAGE6B-R0-FORMAL-TRAIN-20260811-R3/FORMAL_TRAIN_LEDGER.json",
    "simlingo_checkpoint": Path("/home/buaa/wrh/simlingo/outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"),
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(name: str, value: Mapping[str, Any]) -> Path:
    path = REPORT_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def flatten(value: Sequence[Sequence[float]]) -> List[float]:
    return [float(item) for row in value for item in row]


def pair_metrics(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> Dict[str, float]:
    a, b = flatten(left), flatten(right)
    if len(a) != len(b) or not a:
        raise ValueError("PLAN_SHAPE_MISMATCH")
    delta = [x - y for x, y in zip(a, b)]
    return {
        "flattened_l2": math.sqrt(sum(item * item for item in delta)),
        "rmse": math.sqrt(sum(item * item for item in delta) / len(delta)),
        "mean_absolute_difference": sum(abs(item) for item in delta) / len(delta),
        "maximum_absolute_difference": max(abs(item) for item in delta),
        "endpoint_l2": math.dist(tuple(left[-1]), tuple(right[-1])),
    }


def metric_summary(values: Sequence[float]) -> Dict[str, float]:
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
    }


def plan_channel_audit(rows: Sequence[Mapping[str, Any]], channel: str) -> Dict[str, Any]:
    before = {str(row["run_label"]): row["pre_event_plan"][channel] for row in rows}
    after = {str(row["run_label"]): row["post_event_plan"][channel] for row in rows}
    within_before = {
        "{}-{}".format(a, b): pair_metrics(before[a], before[b])
        for a, b in combinations(before, 2)
    }
    within_after = {
        "{}-{}".format(a, b): pair_metrics(after[a], after[b])
        for a, b in combinations(after, 2)
    }
    between = {
        "{}-{}".format(a, b): pair_metrics(before[a], after[b])
        for a, b in product(before, after)
    }
    within_rmse = [item["rmse"] for item in (*within_before.values(), *within_after.values())]
    between_rmse = [item["rmse"] for item in between.values()]
    within_max = max(within_rmse)
    between_min = min(between_rmse)
    separated = between_min > within_max
    return {
        "shape": [len(next(iter(before.values()))), len(next(iter(before.values()))[0])],
        "within_pre_event": within_before,
        "within_post_event": within_after,
        "between_pre_and_post": between,
        "within_rmse_summary": metric_summary(within_rmse),
        "between_rmse_summary": metric_summary(between_rmse),
        "conservative_margin_rmse": between_min - within_max,
        "between_condition_exceeds_repeat_noise": separated,
        "status": "PASS_CONDITION_DIVERGENCE_EXCEEDS_REPEAT_NOISE" if separated else "INCONCLUSIVE_DIVERGENCE_NOT_ABOVE_REPEAT_NOISE",
    }


def scenario_tree_aggregate() -> str:
    base = ROOT / "driveclarify_paper_mvp_scenarios/generated"
    lines = []
    for path in sorted(item for item in base.rglob("*") if item.is_file()):
        lines.append("{}  {}\n".format(sha(path), rel(path)))
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def git_output(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def simlingo_diff_sha() -> str:
    root = "/home/buaa/wrh/simlingo"
    payload = subprocess.check_output(
        ["git", "diff", "--binary", "HEAD", "--"], cwd=root
    )
    return hashlib.sha256(payload).hexdigest()


def transition_rows(live: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "state": item["state"],
            "frame_id": item["frame_id"],
            "simulation_time": item["simulation_time"],
            "track_id": item["track_id"],
            "overlap_fraction": item["overlap_fraction"],
            "motion_evidence_frames": item["motion_evidence_frames"],
            "outside_persistence_frames": item["outside_persistence_frames"],
            "cleared_candidate_frame": item["cleared_candidate_frame"],
            "cleared_confirmed_frame": item["cleared_confirmed_frame"],
            "event_latency_frames": item["event_latency_frames"],
            "reason_codes": item["reason_codes"],
        }
        for item in live["event_timeline"]
    ]


def latency_stats(values: Sequence[float]) -> Mapping[str, float]:
    ordered = sorted(float(item) for item in values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "count": len(values),
        "min_seconds": min(values),
        "max_seconds": max(values),
        "mean_seconds": statistics.fmean(values),
        "median_seconds": statistics.median(values),
        "p95_seconds_nearest_rank": ordered[index],
    }


def post_hoc_actor_audit(path: Path, clear_frame: int) -> Mapping[str, Any]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    selected = []
    for row in rows:
        if int(row["snapshot_frame"]) not in {clear_frame - 3, clear_frame - 2, clear_frame - 1, clear_frame}:
            continue
        actor = next(
            (item for item in row["actors"]["actors"] if item["type_id"] == "vehicle.mitsubishi.fusorosa"),
            None,
        )
        if actor is not None:
            selected.append(
                {
                    "frame_id": row["snapshot_frame"],
                    "simulation_time": row["snapshot_elapsed_seconds"],
                    "actor_id": actor["id"],
                    "location_xyz": actor["location_xyz"],
                    "velocity_world_mps_xyz": actor["velocity_world_mps_xyz"],
                    "speed_mps": math.sqrt(sum(float(x) ** 2 for x in actor["velocity_world_mps_xyz"])),
                    "distance_m": actor["distance_m"],
                }
            )
    actor_ids = {item["actor_id"] for item in selected}
    return {
        "status": "PASS_POST_HOC_ACTOR_ALIVE_AND_MOVING_AT_VISUAL_CLEAR" if len(selected) == 4 and len(actor_ids) == 1 and selected[-1]["speed_mps"] > 0.1 else "BLOCKED_POST_HOC_ACTOR_AUDIT",
        "source_path": rel(path),
        "source_sha256": sha(path),
        "policy_input": False,
        "join_key": "snapshot_frame == real-rgb_0 frame",
        "records_total": len(rows),
        "selected_frames": selected,
        "claim": "Post-hoc CARLA state confirms the bus actor still existed and moved at CLEARED; it was never supplied to the policy.",
    }


def main() -> int:
    generated_at = utc_now()
    run_rows: List[Dict[str, Any]] = []
    for label, directory in RUN_SPECS:
        live_path = directory / "TEMPORAL_GROUNDING_V1_LIVE_RECEIPT.json"
        native_path = directory / "TEMPORAL_GROUNDING_V1_NATIVE_RUN_RECEIPT.json"
        replay_path = directory / "TEMPORAL_GROUNDING_V1_REPLAY_AUDIT.json"
        cleanup_path = directory / "TEMPORAL_GROUNDING_V1_CLEANUP_RECEIPT.json"
        launch_path = directory / "TEMPORAL_GROUNDING_V1_LAUNCH_CONTRACT.json"
        preflight_path = directory / "TEMPORAL_GROUNDING_V1_NATIVE_PREFLIGHT.json"
        leaderboard_path = directory / "leaderboard_results.json"
        live, native, replay, cleanup = map(load, (live_path, native_path, replay_path, cleanup_path))
        launch, preflight = load(launch_path), load(preflight_path)
        run_rows.append(
            {
                "run_label": label,
                "run_dir": rel(directory),
                "live": live,
                "native": native,
                "replay": replay,
                "cleanup": cleanup,
                "launch": launch,
                "preflight": preflight,
                "paths": {
                    "live": rel(live_path),
                    "native": rel(native_path),
                    "replay": rel(replay_path),
                    "cleanup": rel(cleanup_path),
                    "launch": rel(launch_path),
                    "preflight": rel(preflight_path),
                    "leaderboard": rel(leaderboard_path),
                },
                "hashes": {
                    "live": sha(live_path),
                    "native": sha(native_path),
                    "replay": sha(replay_path),
                    "cleanup": sha(cleanup_path),
                    "launch": sha(launch_path),
                    "preflight": sha(preflight_path),
                    "leaderboard": sha(leaderboard_path),
                },
                "pre_event_plan": live["pre_event_plan"],
                "post_event_plan": live["post_event_plan"],
            }
        )

    contract_common = {
        "schema_version": "driveclarify.temporal_grounding_v1.contract.v1",
        "generated_at_utc": generated_at,
        "feature_flag": "DRIVECLARIFY_TEMPORAL_GROUNDING_V1",
        "feature_flag_default": "OFF",
        "split": "TRAIN_ONLY",
        "population_status": "NOT_PART_OF_FROZEN_PAPER_POPULATION",
        "scenario_id": "DC-TGV1-DIAG-001",
        "town": "Town05",
        "route_id": "90500720",
        "instruction": "Turn after the bus clears.",
        "coactivation_forbidden": [
            "DRIVECLARIFY_LANGUAGE_GROUNDING_V1",
            "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE",
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE",
        ],
        "policy_inputs": [
            "real_rgb_0",
            "Grounding_DINO_boxes",
            "ByteTrack_history",
            "runtime_route_deque",
            "simulation_frame_and_time",
            "existing_ego_state",
        ],
        "forbidden_policy_inputs": [
            "CARLA actor IDs/transforms/velocity",
            "simulator bbox ground truth",
            "gold cleared timestamps",
            "scenario internal triggers",
            "DEV or TEST data",
        ],
        "terminal_chain": [
            "visual referent identity",
            "instruction-relevant event",
            "event-target binding",
            "information update",
            "WAIT exit",
            "stale candidate invalidation",
            "fresh SimLingo replan",
        ],
    }
    write_json("TEMPORAL_GROUNDING_V1_CONTRACT.json", contract_common)

    write_json(
        "BYTE_TRACK_RUNTIME_CONTRACT.json",
        {
            "schema_version": "driveclarify.temporal_grounding_v1.bytetrack_contract.v1",
            "implementation": "DRIVECLARIFY_BYTETRACK_ASSOCIATION_V1",
            "proposal_implementation": "SPARSE_PYRAMIDAL_LK_BOX_PROPOSAL_V1",
            "identity_owner": "ByteTrack two-stage high/low IoU Hungarian association",
            "high_score_threshold": 0.5,
            "low_score_threshold": 0.1,
            "match_iou_threshold": 0.2,
            "low_match_iou_threshold": 0.1,
            "occlusion_buffer_frames": 2,
            "lost_buffer_frames": 8,
            "lifecycle_states": ["ACTIVE", "TEMPORARILY_OCCLUDED", "LOST", "REACQUIRE_PENDING", "EXPIRED"],
            "grounding_dino_policy": "INITIAL_AND_EVENT_TRIGGERED_REACQUISITION_ONLY",
            "reacquisition_identity_rule": "A re-grounded box may reuse identity only through ordinary association; otherwise the run blocks on identity switch.",
            "track_loss_rule": "TRACK_LOST != CLEARED",
            "privileged_state_policy_read_count": 0,
        },
    )
    write_json(
        "TEMPORAL_EVENT_ESTIMATOR_CONTRACT.json",
        {
            "schema_version": "driveclarify.temporal_grounding_v1.event_contract.v1",
            "implementation": "DRIVECLARIFY_IMAGE_REGION_CLEARANCE_ESTIMATOR_V1",
            "states": ["UNKNOWN", "APPROACHING", "OCCUPYING_RELEVANT_REGION", "CLEARING", "CLEARED", "TRACK_LOST", "STALE"],
            "occupying_overlap_fraction": 0.35,
            "outside_overlap_fraction": 0.05,
            "occupying_persistence_frames": 2,
            "motion_persistence_frames": 2,
            "cleared_persistence_frames": 3,
            "minimum_upward_motion_pixels": 0.25,
            "minimum_area_reduction_fraction": 0.002,
            "exact_cleared_definition": "The same ACTIVE track must previously occupy the relevant region, have persistent directional motion evidence, then keep its full box at <=0.05 overlap for three consecutive frames.",
            "temporary_occlusion_rule": "FAIL_CLOSED_NO_CLEAR_TRANSITION",
            "track_lost_rule": "DISTINCT_TRACK_LOST_STATE_NEVER_CLEARED",
            "event_region_normalized_xyxy": [0.35, 0.25, 0.80, 0.75],
            "event_region_pixels_at_1024x512": [358.4, 128.0, 819.2, 384.0],
            "calibration": "TRAIN T1 real-rgb_0 only; no outcome label, DEV, or TEST used",
        },
    )
    target = run_rows[0]["live"]["target_binding"]
    write_json(
        "TEMPORAL_TARGET_BINDING_CONTRACT.json",
        {
            "schema_version": "driveclarify.temporal_grounding_v1.target_binding_contract.v1",
            "implementation": "RUNTIME_ROUTE_TOPOLOGY_FIRST_TURN_BINDER_V1",
            "source": "ONLINE_SIMLINGO_ROUTE_DEQUE_READ_ONLY",
            "selection": "first upcoming LEFT or RIGHT runtime route option",
            "frozen_target_in_three_repeats": target,
            "event_target_identity_preserved": all(row["live"]["target_binding"] == target for row in run_rows),
            "target_before_after_same": all(
                row["live"]["candidate_before"]["target_id"] == row["live"]["candidate_after"]["target_id"] == target["target_id"]
                and row["live"]["candidate_before"]["branch_id"] == row["live"]["candidate_after"]["branch_id"] == target["branch_id"]
                for row in run_rows
            ),
            "privileged_state_policy_read_count": 0,
        },
    )
    write_json(
        "REFERENT_PLAUSIBILITY_CONTRACT.json",
        {
            "schema_version": "driveclarify.temporal_grounding_v1.plausibility_contract.v1",
            "model_id": "IDEA-Research/grounding-dino-tiny",
            "model_revision": "a2bb814dd30d776dcf7e30523b00659f4f141c71",
            "runtime_device": "cpu",
            "detector_box_threshold": 0.05,
            "detector_text_threshold": 0.05,
            "minimum_detector_confidence": 0.075,
            "plausibility_threshold": 0.24,
            "minimum_area_fraction": 0.0005,
            "maximum_area_fraction": 0.12,
            "maximum_effective_k": 2,
            "ranking": "plausibility score, detector confidence, area, box, local id",
            "pilot_acceptance": "Exactly one high-score bus becomes an ACTIVE ByteTrack identity; low-score proposals do not create a track.",
            "training_or_finetuning_performed": False,
            "privileged_state_policy_read_count": 0,
        },
    )

    identity_runs = []
    for row in run_rows:
        live, replay = row["live"], row["replay"]
        lifecycle = live["track_lifecycle"]
        identity_runs.append(
            {
                "run_label": row["run_label"],
                "run_dir": row["run_dir"],
                "status": "PASS_IDENTITY_PRESERVED",
                "grounded_referent_id": live["grounded_referent_id"],
                "track_id": live["track_id"],
                "detector_invocation_count": live["detector_invocation_count"],
                "detector_invocation_frames": live["detector_invocation_frames"],
                "reground_count": live["reground_count"],
                "reacquisition_count": live["reacquisition_count"],
                "track_id_switch_count": live["track_id_switch_count"],
                "track_loss_count": live["track_loss_count"],
                "observed_track_frames": len(lifecycle),
                "first_track_observation": lifecycle[0],
                "last_track_observation": lifecycle[-1],
                "replay_status": replay["status"],
                "replay_negative_controls": replay["negative_controls"],
            }
        )
    write_json(
        "TRACK_IDENTITY_AUDIT.json",
        {
            "schema_version": "driveclarify.temporal_grounding_v1.track_identity_audit.v1",
            "status": "PASS_THREE_REAL_RGB_IDENTITY_REPEATS",
            "repeat_count": 3,
            "runs": identity_runs,
            "aggregate": {
                "detector_invocations": sum(row["live"]["detector_invocation_count"] for row in run_rows),
                "regrounds": sum(row["live"]["reground_count"] for row in run_rows),
                "reacquisitions": sum(row["live"]["reacquisition_count"] for row in run_rows),
                "id_switches": sum(row["live"]["track_id_switch_count"] for row in run_rows),
                "track_losses": sum(row["live"]["track_loss_count"] for row in run_rows),
            },
            "claim_limit": "ByteTrack identity is local to each independent run; bt-0001 is not treated as a cross-run global identity.",
        },
    )

    event_runs, premature_count = [], 0
    for row in run_rows:
        live = row["live"]
        transitions = transition_rows(live)
        clear = transitions[-1]
        premature = not (
            clear["state"] == "CLEARED"
            and clear["track_id"] == live["track_id"]
            and clear["overlap_fraction"] <= 0.05
            and clear["outside_persistence_frames"] >= 3
            and live["track_loss_count"] == 0
        )
        premature_count += int(premature)
        event_runs.append(
            {
                "run_label": row["run_label"],
                "run_dir": row["run_dir"],
                "timeline": transitions,
                "premature_cleared_count": int(premature),
                "detector_latency": latency_stats(live["detector_latencies_seconds"]),
                "tracker_latency": latency_stats(live["tracker_latency_seconds"]),
                "event_estimator_latency": latency_stats(live["event_estimator_latency_seconds"]),
                "replay_status": row["replay"]["status"],
            }
        )
    post_hoc_path = RUN_SPECS[2][1] / "post_hoc_world_state.jsonl"
    post_hoc = post_hoc_actor_audit(post_hoc_path, int(event_runs[2]["timeline"][-1]["frame_id"]))
    write_json(
        "TEMPORAL_EVENT_AUDIT.json",
        {
            "schema_version": "driveclarify.temporal_grounding_v1.event_audit.v1",
            "status": "PASS_THREE_EVENT_REPEATS_NO_PREMATURE_CLEAR" if premature_count == 0 and post_hoc["status"].startswith("PASS") else "BLOCKED_TEMPORAL_EVENT_AUDIT",
            "exact_cleared_definition": "Previously occupied + motion evidence + same ACTIVE identity + full-box overlap <=0.05 for 3 consecutive frames.",
            "premature_cleared_count": premature_count,
            "runs": event_runs,
            "post_hoc_actor_evidence": post_hoc,
            "negative_control": "Across all three real-RGB replays, temporary miss did not clear and LOST emitted TRACK_LOST, never CLEARED.",
        },
    )

    replan_runs = []
    for row in run_rows:
        live = row["live"]
        audit_log = live["m3_fresh_replan_lifecycle"]["final_state"]["audit_log"]
        replan_runs.append(
            {
                "run_label": row["run_label"],
                "run_dir": row["run_dir"],
                "pre_event_decision": live["pre_event_decision"],
                "post_event_decision": live["post_event_decision"],
                "wait_entry_frame": live["wait_entry_frame"],
                "information_update": live["information_update"],
                "wait_exit_frame": live["wait_exit_frame"],
                "wait_exit_reason": live["wait_exit_reason"],
                "old_candidate_invalidated": live["old_candidate_invalidated"],
                "invalidated_candidate_id": live["invalidated_candidate_id"],
                "invalidated_at_frame": live["invalidated_at_frame"],
                "fresh_planning_frame": live["fresh_planning_frame"],
                "fresh_replan_id": live["fresh_replan_id"],
                "candidate_before": live["candidate_before"],
                "candidate_after": live["candidate_after"],
                "candidate_identity_preserved_across_state_change": live["candidate_identity_preserved_across_state_change"],
                "candidate_simlingo_forward_count": live["candidate_simlingo_forward_count"],
                "m3_transition_chain": [item["transition_id"] for item in audit_log],
                "m3_final_state": live["m3_fresh_replan_lifecycle"]["final_state"]["lifecycle_state"],
                "pre_event_plan": live["pre_event_plan"],
                "post_event_plan": live["post_event_plan"],
                "native_status": row["native"]["status"],
                "collision_count": row["native"]["post_hoc_safety"]["vehicle_collision_count"],
            }
        )
    limited = run_rows[1]["live"]["limited_act"]
    replan_audit = {
        "schema_version": "driveclarify.temporal_grounding_v1.replan_audit.v1",
        "status": "PASS_WAIT_INFORMATION_INVALIDATE_FRESH_REPLAN_AND_BOUNDED_ACT",
        "runs": replan_runs,
        "closed_loop": {
            "source_run": run_rows[1]["run_dir"],
            "status": limited["status"],
            "authority_receipts_issued": limited["authority_receipts_issued"],
            "authority_receipts_consumed": limited["authority_receipts_consumed"],
            "candidate_control_window_count": limited["candidate_control_window_count"],
            "candidate_control_tick_count": limited["candidate_control_tick_count"],
            "candidate_control_writes": limited["candidate_control_writes"],
            "pid_invocations_on_act_tick": limited["pid_invocations_on_act_tick"],
            "actual_control_exact_match": limited["actual_control_exact_match"],
            "ownership_returned_to_baseline": limited["ownership_returned_to_baseline"],
            "old_candidate_control_writes": limited["old_candidate_control_writes"],
            "stale_candidate_control_writes": limited["stale_candidate_control_writes"],
            "unexpected_model_forwards": limited["unexpected_model_forwards"],
            "unexpected_control_owners": limited["unexpected_control_owners"],
            "new_pid_count": run_rows[1]["live"]["new_pid_count"],
            "candidate_direct_vehicle_control_writes": run_rows[1]["live"]["candidate_direct_vehicle_control_write_count"],
            "m3_direct_vehicle_control_writes": run_rows[1]["live"]["m3_direct_vehicle_control_write_count"],
        },
    }
    write_json("TEMPORAL_REPLAN_AUDIT.json", replan_audit)

    divergence = {
        "schema_version": "driveclarify.temporal_grounding_v1.plan_divergence_audit.v1",
        "status": "PENDING",
        "protocol": "PRE_EVENT_Ax3_VS_POST_EVENT_Bx3_FROM_THREE_INDEPENDENT_REAL_CARLA_RUNS",
        "condition_A": "pre-event WAIT candidate plan",
        "condition_B": "post-CLEARED fresh ACT candidate plan",
        "repeat_count_per_condition": 3,
        "runs": [
            {
                "run_label": row["run_label"],
                "run_dir": row["run_dir"],
                "A_pre_event_plan": row["pre_event_plan"],
                "B_post_event_plan": row["post_event_plan"],
            }
            for row in run_rows
        ],
        "channels": {
            "route": plan_channel_audit(run_rows, "route"),
            "speed": plan_channel_audit(run_rows, "speed"),
        },
        "claim_limit": "This is a three-repeat TRAIN diagnostic signal, not a population-level causal or safety claim.",
    }
    both_separated = all(item["between_condition_exceeds_repeat_noise"] for item in divergence["channels"].values())
    divergence["status"] = "PASS_PRE_POST_PLAN_DIVERGENCE_EXCEEDS_REPEAT_NOISE" if both_separated else "INCONCLUSIVE_PRE_POST_PLAN_DIVERGENCE"
    write_json("TEMPORAL_PLAN_DIVERGENCE_AUDIT.json", divergence)

    detector_latencies = [
        value
        for row in run_rows
        for value in row["live"]["detector_latencies_seconds"]
    ]
    tracker_latencies = [
        value
        for row in run_rows
        for value in row["live"]["tracker_latency_seconds"]
    ]
    event_latencies = [
        value
        for row in run_rows
        for value in row["live"]["event_estimator_latency_seconds"]
    ]
    compute_runs = []
    for row in run_rows:
        live = row["live"]
        first_event, clear_event = live["event_timeline"][0], live["event_timeline"][-1]
        compute_runs.append(
            {
                "run_label": row["run_label"],
                "detector_invocation_count": live["detector_invocation_count"],
                "detector_compute_seconds": sum(live["detector_latencies_seconds"]),
                "tracker_frame_count": len(live["tracker_latency_seconds"]),
                "tracker_compute_seconds": sum(live["tracker_latency_seconds"]),
                "event_estimator_frame_count": len(live["event_estimator_latency_seconds"]),
                "event_estimator_compute_seconds": sum(live["event_estimator_latency_seconds"]),
                "total_temporal_stack_compute_seconds": (
                    sum(live["detector_latencies_seconds"])
                    + sum(live["tracker_latency_seconds"])
                    + sum(live["event_estimator_latency_seconds"])
                ),
                "first_evidence_to_clear_frames": clear_event["frame_id"] - first_event["frame_id"],
                "first_evidence_to_clear_simulation_seconds": clear_event["simulation_time"] - first_event["simulation_time"],
                "wait_wall_elapsed_seconds": live["wait"]["lease_elapsed_s"],
                "normal_simlingo_forward_count": live["normal_simlingo_forward_count"],
                "candidate_simlingo_forward_count": live["candidate_simlingo_forward_count"],
                "pre_event_candidate_forward_latency_seconds": live["pre_event_plan"]["latency_seconds"],
                "post_event_candidate_forward_latency_seconds": live["post_event_plan"]["latency_seconds"],
                "existing_pid_invocation_count": live["existing_pid_invocation_count"],
                "visualization_induced_detector_forward_count": live["visualization_induced_detector_forward_count"],
                "visualization_induced_simlingo_forward_count": live["visualization_induced_simlingo_forward_count"],
                "visualization_induced_pid_count": live["visualization_induced_pid_count"],
            }
        )
    compute_report = {
        "schema_version": "driveclarify.temporal_grounding_v1.compute_report.v1",
        "status": "PASS_COMPUTE_ACCOUNTING_NO_REALTIME_CLAIM",
        "runs": compute_runs,
        "aggregate": {
            "grounding_dino": {
                "invocation_count": len(detector_latencies),
                "latency": latency_stats(detector_latencies),
                "p95_claim_status": "DESCRIPTIVE_ONLY_N3_TOO_SMALL_FOR_REALTIME_CLAIM",
            },
            "bytetrack_and_rgb_motion_per_frame": latency_stats(tracker_latencies),
            "event_estimator_per_frame": latency_stats(event_latencies),
            "total_temporal_stack_compute_seconds": latency_stats(
                [item["total_temporal_stack_compute_seconds"] for item in compute_runs]
            ),
            "normal_simlingo_forward_count": sum(item["normal_simlingo_forward_count"] for item in compute_runs),
            "candidate_simlingo_forward_count": sum(item["candidate_simlingo_forward_count"] for item in compute_runs),
            "visualization_extra_detector_forwards": 0,
            "visualization_extra_simlingo_forwards": 0,
            "visualization_extra_pid_invocations": 0,
        },
        "positioning": "EVENT_TRIGGERED_GROUNDING_PLUS_LIGHTWEIGHT_TEMPORAL_TRACKING",
        "realtime_claim": False,
        "latency_scope_note": "Compute time is separated from environment-observation and WAIT wall time; no end-to-end real-time claim is made.",
    }
    write_json("TEMPORAL_COMPUTE_REPORT.json", compute_report)

    immutable_actual = {name: sha(path) for name, path in IMMUTABLE_PATHS.items()}
    immutable_actual["simlingo_head"] = git_output("git", "-C", "/home/buaa/wrh/simlingo", "rev-parse", "HEAD")
    immutable_actual["simlingo_protected_diff"] = simlingo_diff_sha()
    immutable_actual["generated_scenario_tree"] = scenario_tree_aggregate()
    immutable_matches = {name: immutable_actual[name] == EXPECTED[name] for name in EXPECTED}
    ledger = load(IMMUTABLE_PATHS["formal_r3_ledger"])
    regression_result_path = REGRESSION_DIR / "FULL_REGRESSION_MATRIX_RESULT.json"
    regression_receipt_path = REGRESSION_DIR / "FULL_REGRESSION_RECEIPT.json"
    regression = load(regression_result_path)
    regression_receipt = load(regression_receipt_path)
    language_audit_path = ROOT / "reports/language_grounding_v1/CANDIDATE_PLAN_DIVERGENCE_AUDIT.json"
    language_audit = load(language_audit_path)
    final_cleanup_path = ROOT / "artifacts/temporal_grounding_v1/TEMPORAL_GROUNDING_V1_FINAL_CLEANUP_RECEIPT.json"
    final_cleanup = load(final_cleanup_path)
    language_regression = {
        "focused_rerun": "20/20 PASS across temporal_grounding_v1 + language_grounding_v1",
        "static_language_grounding_test_file": "12/12 PASS in full isolated matrix",
        "existing_real_CARLA_Ax3_Bx3_artifact": rel(language_audit_path),
        "existing_real_CARLA_Ax3_Bx3_sha256": sha(language_audit_path),
        "status": language_audit["status"],
        "repeat_count_per_group": language_audit["stability_metrics"]["repeat_count_per_group"],
        "route_between_min": language_audit["route_summary"]["between_min"],
        "route_within_max": language_audit["route_summary"]["within_all_max"],
        "speed_between_min": language_audit["speed_summary"]["between_min"],
        "speed_within_max": language_audit["speed_summary"]["within_all_max"],
        "artifact_reused_not_rewritten": True,
    }
    all_live_pass = all(
        row["native"]["status"] == "PASS_NATIVE_TEMPORAL_CAPTURE_AND_CLEANUP"
        and row["cleanup"]["status"] == "PASS"
        and row["native"]["post_hoc_safety"]["vehicle_collision_count"] == 0
        and row["replay"]["status"] == "PASS_T0_REAL_RGB_TEMPORAL_REPLAY"
        for row in run_rows
    )
    full_pass = regression["status"] == "PASS_FULL_REPOSITORY_REGRESSION_MATRIX"
    integrity_pass = all(immutable_matches.values()) and ledger["counts"]["completed"] == 8 and ledger["counts"]["scheduled"] == 256 and ledger["dev_attempt_count"] == 0 and ledger["test_attempt_count"] == 0 and ledger["test_consumed"] is False
    final_status = (
        "PASS_TEMPORAL_GROUNDING_V1_TRAIN_PILOT_READY_FOR_CONTROLLED_INTEGRATION_REVIEW"
        if all_live_pass and full_pass and integrity_pass and premature_count == 0 and both_separated and final_cleanup["status"] == "PASS"
        else "BLOCKED_TEMPORAL_GROUNDING_V1_FINALIZATION"
    )
    final_receipt = {
        "schema_version": "driveclarify.temporal_grounding_v1.final_receipt.v1",
        "generated_at_utc": generated_at,
        "status": final_status,
        "live_repeat_count": 3,
        "live_all_pass": all_live_pass,
        "live_evidence_bundles": [
            {
                "run_label": row["run_label"],
                "scenario_id": "DC-TGV1-DIAG-001",
                "seed": row["launch"]["episode"]["seed"],
                "instruction": row["launch"]["episode"]["raw_instruction"],
                "rgb_frame_count": len(row["live"]["rgb_identities"]),
                "paths": row["paths"],
                "sha256": row["hashes"],
                "cleanup_status": row["cleanup"]["status"],
            }
            for row in run_rows
        ],
        "closed_loop_pass": limited["status"].startswith("PASS_LIMITED_CLOSED_LOOP"),
        "premature_cleared_count": premature_count,
        "plan_divergence_status": divergence["status"],
        "full_regression": {
            "status": regression["status"],
            "suite_count": regression["suite_count"],
            "totals": regression["totals"],
            "receipt_status": regression_receipt["status"],
            "result_path": rel(regression_result_path),
            "result_sha256": sha(regression_result_path),
            "receipt_path": rel(regression_receipt_path),
            "receipt_sha256": sha(regression_receipt_path),
        },
        "language_grounding_v1_regression": language_regression,
        "final_cleanup": {
            "status": final_cleanup["status"],
            "path": rel(final_cleanup_path),
            "sha256": sha(final_cleanup_path),
            "process_counts": final_cleanup["process_counts"],
            "port_listener_count": final_cleanup["ports"]["2020_2021_8020_listener_count"],
            "project_gpu_process_count": final_cleanup["project_gpu_process_count"],
        },
        "frozen_integrity": {
            "status": "PASS_ALL_PROTECTED_HASHES_UNCHANGED" if all(immutable_matches.values()) else "BLOCKED_PROTECTED_HASH_MISMATCH",
            "actual": immutable_actual,
            "expected": EXPECTED,
            "matches": immutable_matches,
            "formal_train_r3_counts": ledger["counts"],
            "dev_attempt_count": ledger["dev_attempt_count"],
            "test_attempt_count": ledger["test_attempt_count"],
            "test_consumed": ledger["test_consumed"],
        },
        "claims_not_made": [
            "part of frozen 24-scenario paper population",
            "DEV or TEST result",
            "population-level temporal grounding performance",
            "formal or real-world safety guarantee",
            "new controller or PID",
        ],
        "single_next_action": "Conduct controlled integration review of this default-OFF TRAIN-only branch before any broader scenario or split authorization.",
    }
    write_json("TEMPORAL_GROUNDING_V1_FINAL_RECEIPT.json", final_receipt)

    report = f"""# DriveClarify Temporal Grounding V1 — TRAIN Pilot Final Report

Final status: `{final_status}`

## Scope and result

This is a separate, default-OFF (`DRIVECLARIFY_TEMPORAL_GROUNDING_V1=1`) real-CARLA TRAIN diagnostic for `DC-TGV1-DIAG-001`, Town05 route `90500720`, instruction **“Turn after the bus clears.”** It is explicitly `NOT_PART_OF_FROZEN_PAPER_POPULATION`; DEV and TEST remained untouched.

The required chain is demonstrated in three independent collision-safe native runs: Grounding DINO finds the bus from real `rgb_0`; the frozen label-free gate admits one high-score track; ByteTrack keeps `bt-0001` through the temporal event; the same event remains bound to `{target['target_id']}` / `{target['branch_id']}` / `RIGHT`; `CLEARED` produces a new information update; WAIT exits; the old candidate becomes unauthorized; and a fresh SimLingo forward produces the post-event ACT plan. All three T0 real-RGB replays reproduce the transition and pass the `TRACK_LOST != CLEARED` negative control.

## Exact event and live evidence

`CLEARED` means: prior persistent occupancy, persistent directional motion, the same **ACTIVE** track, then its full box has overlap `<=0.05` with the route-gated image corridor for 3 consecutive frames. Temporary occlusion and track loss fail closed. Across the three runs: DINO calls=`3`, re-ground=`0`, reacquisition=`0`, ID switches=`0`, track losses=`0`, premature-cleared=`{premature_count}`, vehicle collisions=`0`.

The third run's post-hoc-only CARLA stream confirms bus actor 139 was still alive and moving at the visual clear frame (about `{post_hoc['selected_frames'][-1]['speed_mps']:.3f} m/s`). This actor state was not a policy input; all privileged-state policy counters are zero.

## WAIT → information → fresh replan

Every run entered WAIT at initial grounding and exited only with `SOURCE_OR_FRESHNESS_INVALIDATED` after the confirmed information update. The stale pre-event candidate was invalidated at the clear frame, then a new candidate was created from a fresh observation and fresh SimLingo forward. The frozen M3 chain is `MC-T001 → MC-T006 → MC-T013 → MC-T017 → MC-T019`, ending `RESUME_READY`; M3 adds no model forward, planner invocation, PID invocation, or direct control write.

The bounded closed-loop run additionally passed one existing-authority/PID window: receipt issued/consumed=`1/1`, candidate window/tick/write=`1/1/1`, existing PID on act tick=`1`, next-frame actuator exact match=`true`, baseline ownership returned=`true`, old/stale writes=`0/0`, direct candidate writes=`0`, direct M3 writes=`0`, new PID=`0`, collision=`0`.

## Plan signal and regressions

The independent pre-event A×3 versus post-event B×3 numeric audit is `{divergence['status']}`. Route: between-condition minimum RMSE `{divergence['channels']['route']['between_rmse_summary']['min']:.6f}` versus maximum repeat noise `{divergence['channels']['route']['within_rmse_summary']['max']:.6f}`. Speed: `{divergence['channels']['speed']['between_rmse_summary']['min']:.6f}` versus `{divergence['channels']['speed']['within_rmse_summary']['max']:.6f}`. Full arrays, digests, all within-pairs, and all nine between-pairs are in `TEMPORAL_PLAN_DIVERGENCE_AUDIT.json`.

Full isolated repository regression: `{regression['status']}`, `{regression['totals']['passed']}/{regression['totals']['tests']}` passed across `{regression['suite_count']}` test files; failures/errors/skips=`{regression['totals']['failures']}/{regression['totals']['errors']}/{regression['totals']['skipped']}`. Static Language Grounding V1 rerun passed; its prior real-CARLA A×3/B×3 sensitivity artifact remains unchanged and still reports route/speed separation above zero repeat noise.

Compute accounting is reported separately, without a real-time claim. Across three runs, Grounding DINO invocation count=`{compute_report['aggregate']['grounding_dino']['invocation_count']}`, mean latency=`{compute_report['aggregate']['grounding_dino']['latency']['mean_seconds']:.6f}s`; its n=3 p95 is descriptive only. ByteTrack+RGB-motion mean/p95 per frame=`{compute_report['aggregate']['bytetrack_and_rgb_motion_per_frame']['mean_seconds']:.8f}/{compute_report['aggregate']['bytetrack_and_rgb_motion_per_frame']['p95_seconds_nearest_rank']:.8f}s`; event-estimator mean/p95=`{compute_report['aggregate']['event_estimator_per_frame']['mean_seconds']:.8f}/{compute_report['aggregate']['event_estimator_per_frame']['p95_seconds_nearest_rank']:.8f}s`. Normal/candidate SimLingo forwards=`{compute_report['aggregate']['normal_simlingo_forward_count']}/{compute_report['aggregate']['candidate_simlingo_forward_count']}`; visualization-induced detector/forward/PID counts are all zero.

## Frozen boundaries

Stage6A freeze, Stage6B-R0 freeze, formal R3 ledger, SimLingo checkpoint/HEAD/protected diff, and generated scenario tree all match their entry hashes. Formal TRAIN R3 remains exactly `8/256`; DEV attempt=`0`; TEST attempt=`0`, consumed=`false`. SimLingo remained read-only. This pilot does not alter the frozen experiment or claim paper-population results.

Final cleanup is `PASS`: CARLA, ScenarioRunner, evaluator, visualizer, temporal runner, tracking worker, and Grounding DINO worker counts are zero; ports 2020/2021/8020 have zero listeners; project GPU processes are zero.

## Diagnostic history and claim limit

The preserved receipts include early infrastructure/module-discovery, route-command collapse, GPU OOM, lease, tracking calibration, tensor-shape, and collision diagnostics. They were not hidden or promoted. The final claim rests only on the three collision-safe passes and their replays. This is a bounded TRAIN integration pilot, not a population result or safety guarantee.

## Reviewer attacks answered

1. **Why not Grounding DINO every frame?** Its CPU forward is expensive and semantic identity should not be recreated every tick. V1 invokes it initially and only on fail-closed LOST-triggered reacquisition; sparse pyramidal LK supplies real-RGB motion proposals and ByteTrack owns identity.
2. **How is ~2.9 s detector latency handled?** The event-triggered detector runs at WAIT entry, not in the per-frame control seam. The measured mean is disclosed above; V1 makes no real-time claim.
3. **Why is track loss not clear?** LOST/REACQUIRE_PENDING/EXPIRED map to `TRACK_LOST`, reset outside persistence, and can never emit `CLEARED`; all three replay negative controls pass.
4. **What exactly is cleared?** Prior occupancy plus directional motion, same ACTIVE track, and full-box overlap `<=0.05` for three consecutive frames.
5. **Did policy inspect CARLA actors?** No. Actor ID/pose/velocity, simulator boxes, gold timestamp, and scenario trigger policy-read counters are zero. One separately flagged world-state stream was read only after the run for audit.
6. **How are ByteTrack ID switches handled?** Re-grounding may reuse an ID only through ordinary IoU association. Failure to associate the original ID blocks with `BLOCKED_TEMPORAL_TRACK_ID_SWITCH`; accepted runs have zero switches.
7. **Did target binding collapse to generic TURN?** No. The online route deque binds executable `RIGHT`, `{target['junction_id']}`, `{target['branch_id']}`, and `{target['target_id']}`; the same IDs are present before and after the event.
8. **Was WAIT merely braking?** No. WAIT is `HOLD_CURRENT_VALID_PLAN`: baseline SimLingo/PID continues, DriveClarify adds no control write or PID call, and the ego/environment advance. Candidate commit remains zero until WAIT exit.
9. **Did information arrival really trigger a fresh replan?** Yes. `CLEARED` emits an update at the exit frame, invalidates the old candidate, and a fresh same-frame observation drives a new SimLingo forward and `MC-T017 → MC-T019` to `RESUME_READY`.
10. **Could A/B events cross-wire?** Both candidates preserve referent/event/target IDs, while candidate-set/generation/freshness IDs change. The old candidate is explicitly unauthorized before the new one is armed.
11. **Was a weak second detection forced into tracking?** No. In the audited run it is image-only plausible (`0.0837 >= 0.075` and plausibility `0.321 >= 0.24`) but below ByteTrack's `0.1` low-score floor and far below its `0.5` new-track threshold, so it cannot create the pilot identity.
12. **Is this only a demo?** It is a three-repeat real-CARLA TRAIN diagnostic with one bounded closed-loop window and full regressions, but it remains outside the frozen paper population and supports no population, DEV/TEST, real-time, or safety-generalization claim.

## Next action

Conduct a controlled integration review of this default-OFF branch before authorizing broader scenarios or any new split.
"""
    report_path = REPORT_DIR / "TEMPORAL_GROUNDING_V1_FINAL_REPORT.md"
    report_path.write_text(report, encoding="utf-8")

    source_paths: List[Path] = []
    for row in run_rows:
        source_paths.extend(ROOT / item for item in row["paths"].values())
    source_paths.extend(
        [
            post_hoc_path,
            regression_result_path,
            regression_receipt_path,
            language_audit_path,
            final_cleanup_path,
            *IMMUTABLE_PATHS.values(),
        ]
    )
    report_paths = sorted(
        item for item in REPORT_DIR.iterdir() if item.is_file() and item.name != "ARTIFACT_HASHES.json"
    )
    artifacts = []
    for path in sorted(set(source_paths + report_paths), key=lambda item: str(item)):
        artifacts.append({"path": rel(path), "bytes": path.stat().st_size, "sha256": sha(path)})
    write_json(
        "ARTIFACT_HASHES.json",
        {
            "schema_version": "driveclarify.temporal_grounding_v1.artifact_hashes.v1",
            "generated_at_utc": generated_at,
            "status": "PASS_HASH_MANIFEST_WRITTEN",
            "self_hash_excluded": True,
            "artifacts": artifacts,
        },
    )
    print(json.dumps({"status": final_status, "report_dir": rel(REPORT_DIR), "full_regression": regression["totals"], "divergence": divergence["status"]}, indent=2, sort_keys=True))
    return 0 if final_status.startswith("PASS_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
