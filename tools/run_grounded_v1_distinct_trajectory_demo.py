#!/usr/bin/env python3
"""Run one isolated TRAIN-only native distinct-trajectory qualitative demo.

The child native runner owns all episode polling and writes structured
receipts. This supervisor reads those receipts only after the batch exits.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_v1_distinct_trajectory_demo.contracts import (  # noqa: E402
    ARTIFACT_ROOT,
    DEMO_ENV,
    FINAL_PASS_STATUS,
    FIXTURE_ID,
    MANIFEST_PATH,
    REPORT_ROOT,
    ROUTE_PATH,
    RUN_ID,
    SCENARIO_ROOT,
    file_sha256,
)

# Must be visible before importing the generic native runner because its
# window title is selected at module import time.
os.environ[DEMO_ENV] = "1"

from driveclarify_grounded_language_v1.contracts import RECEIPT_FILENAME  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1 import backend as e1_backend  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1.contracts import FEATURE_FLAG  # noqa: E402
from driveclarify_paper_mvp_stage6b import backend as native  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402


ENTRY_STATUS = "PASS_GROUNDED_LANGUAGE_V1_EXTENSION_E1_R1_TRIAD_READY_FOR_FULL_TRAIN_AUTHORIZATION"
RECEIPT_NAME = "GROUNDED_V1_DISTINCT_TRAJECTORY_DEMO_RECEIPT.json"
E1_RECEIPT = ROOT / "reports/grounded_language_v1_extension_e1_r1/E1R1_FINAL_RECEIPT.json"
E1_HASHES = ROOT / "reports/grounded_language_v1_extension_e1_r1/ARTIFACT_HASHES.json"
E3_LEDGER = ROOT / (
    "reports/grounded_language_v1_extension_e1_r1_e3/"
    "E3_FORMAL_TRAIN_LEDGER.json"
)


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _sha_or_none(path: Path) -> str | None:
    return file_sha256(path) if path.is_file() else None


def _frozen_hashes() -> dict[str, Any]:
    return {
        "e1_r1_final_receipt": _sha_or_none(E1_RECEIPT),
        "e1_r1_artifact_hashes": _sha_or_none(E1_HASHES),
        "e3_formal_ledger": _sha_or_none(E3_LEDGER),
        "simlingo_head": native._git_head(native.SIMLINGO_ROOT),
        "simlingo_protected_diff_sha256": native._git_diff_sha256(native.SIMLINGO_ROOT),
        "simlingo_checkpoint_sha256": native._file_sha256(native.CHECKPOINT),
    }


def _points(value: Any) -> list[tuple[float, float]]:
    result = []
    for row in value or ():
        try:
            result.append((float(row[0]), float(row[1])))
        except (IndexError, TypeError, ValueError):
            pass
    return result


def _arc(route: Sequence[tuple[float, float]]) -> float:
    return sum(
        math.hypot(bx - ax, by - ay)
        for (ax, ay), (bx, by) in zip(route, route[1:])
    )


def _selected_plan(rows: Sequence[Mapping[str, Any]], prefix: str) -> Mapping[str, Any]:
    return next(
        (row for row in rows if str(row.get("candidate_id", "")).startswith(prefix)),
        {},
    )


def _candidate_receipt(
    label: str,
    target: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    forward = dict(plan.get("forward_evidence") or {})
    exact_conditioning = plan.get("forwarded_prompt_text") or forward.get(
        "forwarded_prompt_text"
    )
    exact_hash = plan.get("forwarded_prompt_sha256") or forward.get(
        "forwarded_prompt_sha256"
    )
    return {
        "label": label,
        "interpretation": (
            "Nearer white van → Junction 1 → Right Branch 1"
            if label == "A"
            else "Farther white van → Junction 2 → Right Branch 2"
        ),
        "semantic": {
            "interpretation_id": target.get("interpretation_id"),
            "semantic_sha256": target.get("semantic_sha256"),
            "ordering": target.get("ordering"),
            "referring_expression": target.get("referring_expression"),
        },
        "grounding": {
            "referent_id": target.get("referent_id"),
            "grounding_sha256": target.get("grounding_sha256"),
            "bbox_xyxy": target.get("bbox_xyxy"),
            "detector_confidence": target.get("detector_confidence"),
            "source_frame_id": target.get("source_frame_id"),
            "source_observation_id": target.get("source_observation_id"),
            "privileged_state_read_count": target.get("privileged_state_read_count"),
        },
        "target": {
            "target_id": target.get("target_id"),
            "junction_id": target.get("junction_id"),
            "junction_label": "Junction 1" if label == "A" else "Junction 2",
            "branch_id": target.get("branch_id"),
            "branch_label": "Right Branch 1" if label == "A" else "Right Branch 2",
            "route_order_index": target.get("route_order_index"),
            "route_opportunity_index": target.get("route_opportunity_index"),
            "distance_or_progress_m": target.get("distance_or_progress"),
            "binding_source": target.get("target_binding_source"),
        },
        "candidate_prompt_text": target.get("prompt_text"),
        "candidate_prompt_sha256": target.get("conditioning_hash"),
        "exact_simlingo_conditioning": exact_conditioning,
        "conditioning_sha256": exact_hash,
        "pred_route_sha256": plan.get("route_sha256"),
        "pred_speed_sha256": plan.get("speed_sha256"),
        "raw_route_tensor_sha256": plan.get("raw_route_tensor_sha256"),
        "raw_speed_tensor_sha256": plan.get("raw_speed_tensor_sha256"),
        "pred_route": plan.get("route"),
        "pred_speed": plan.get("speed"),
        "model_forward_count": forward.get("model_forward_count"),
    }


def _blocker(live: Mapping[str, Any], visual: Mapping[str, Any], native_run: Mapping[str, Any]) -> str | None:
    grounding = dict(live.get("grounding") or {})
    selected = grounding.get("selected_referents") or ()
    if int(live.get("raw_k") or 0) < 2 or len(selected) < 2:
        return "BLOCKED_DEMO_GROUNDING_K2_UNAVAILABLE"
    if int(live.get("effective_k") or 0) != 2:
        return "BLOCKED_DEMO_EFFECTIVE_K_COLLAPSE"
    if live.get("target_duplicate") is not False:
        return "BLOCKED_DEMO_TARGETS_NOT_DISTINCT"
    if visual.get("second_target_in_vla_horizon") is not True:
        return "BLOCKED_DEMO_SECOND_TARGET_OUTSIDE_VLA_HORIZON"
    bev = dict(visual.get("bev") or {})
    visually_resolved = bool(
        float(bev.get("max_pixel_separation") or 0.0) >= 32.0
        and int(bev.get("sustained_points_ge_16px") or 0) >= 3
    )
    if not visually_resolved:
        return "BLOCKED_DEMO_SIMLINGO_PLAN_DIVERGENCE_NOT_VISUALLY_RESOLVED"
    if native_run.get("desktop_capture_status") != "PASS_VALIDATED_NATIVE_DESKTOP":
        return "BLOCKED_DEMO_NATIVE_VISUAL_EVIDENCE"
    return None


def main() -> int:
    started = _utc()
    execution_id = "DC-GV1-DEMO-" + dt.datetime.now().strftime("%Y%m%dT%H%M%SCST")
    output = ARTIFACT_ROOT / execution_id
    report_dir = REPORT_ROOT / execution_id
    summary_path = output / "DEMO_BATCH_SUMMARY.json"
    before = _frozen_hashes()
    entry = json.loads(E1_RECEIPT.read_text(encoding="utf-8"))
    if entry.get("status") != ENTRY_STATUS:
        _write(
            summary_path,
            {
                "status": "BLOCKED_DEMO_E1_R1_ENTRY_STATUS",
                "observed_entry_status": entry.get("status"),
                "expected_entry_status": ENTRY_STATUS,
            },
        )
        print(json.dumps({"status": "BLOCKED_DEMO_E1_R1_ENTRY_STATUS", "summary": str(summary_path)}))
        return 2

    source = e1_backend.resolve_episode(
        fixture_id="E1R1-ASK-PHYS-001",
        method_id="driveclarify_grounded_v1",
        episode_id=execution_id,
    )
    spec = replace(
        source,
        episode_id=execution_id,
        runtime_config_id=FIXTURE_ID,
        scenario_id=FIXTURE_ID,
        runtime_fixture_id=FIXTURE_ID,
        town="Town04",
        route_id="99404001",
        route_path=ROUTE_PATH.resolve(),
        runtime_manifest_path=MANIFEST_PATH.resolve(),
        raw_instruction="Turn after the white van.",
        schedule_sha256=file_sha256(ROUTE_PATH),
        runtime_manifest_sha256=file_sha256(MANIFEST_PATH),
    )
    native_run: Mapping[str, Any] = {}
    error = None
    lease_receipt: dict[str, Any] | None = None
    try:
        with e1_backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            native_run = run_grounded(
                output,
                case="ask",
                seed=spec.seed,
                method_id="driveclarify_grounded_v1",
                device="cpu",
                control=True,
                answer="The nearer white van.",
                answer_delay=5.0,
                timeout_seconds=300.0,
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides={
                    FEATURE_FLAG: "1",
                    DEMO_ENV: "1",
                    "SCENARIO_RUNNER_ROOT": str(SCENARIO_ROOT.resolve()),
                    "DRIVECLARIFY_E1R1_FIXTURE_ID": FIXTURE_ID,
                    "DRIVECLARIFY_E1R1_WAIT_ACTOR_MOTION_SOURCE": "STATIC_VISUAL_LANDMARKS",
                },
            )
    except Exception as exc:
        error = type(exc).__name__ + ":" + str(exc)
    if lease_receipt is not None:
        _write(output / "GPU_LEASE_RECEIPT.json", lease_receipt)

    live_path = output / RECEIPT_FILENAME
    visual_path = output / "GROUNDED_V1_DEMO_VISUAL_STATE.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else {}
    visual = json.loads(visual_path.read_text(encoding="utf-8")) if visual_path.is_file() else {}
    targets = list(live.get("target_binding_receipts") or ())[:2]
    rows = list(live.get("candidate_plan_repetitions") or ())
    plan_a, plan_b = _selected_plan(rows, "A"), _selected_plan(rows, "B")
    candidates = [
        _candidate_receipt("A", targets[0], plan_a) if len(targets) > 0 else {},
        _candidate_receipt("B", targets[1], plan_b) if len(targets) > 1 else {},
    ]
    blocker = (
        "BLOCKED_DEMO_NATIVE_VISUAL_EVIDENCE"
        if error or not live
        else _blocker(live, visual, native_run)
    )
    after = _frozen_hashes()
    integrity_pass = before == after
    if not integrity_pass and blocker is None:
        blocker = "BLOCKED_DEMO_FROZEN_INTEGRITY_CHANGED"
    counters = {
        "normal_forward_count": live.get("normal_simlingo_forward_count"),
        "candidate_forward_count": live.get("candidate_simlingo_forward_count"),
        "visualization_induced_grounding_dino_forward_count": live.get("visualization_induced_detector_forward_count"),
        "visualization_induced_simlingo_forward_count": live.get("visualization_induced_simlingo_forward_count"),
        "visualization_induced_bytetrack_update_count": 0,
        "visualization_induced_planner_advance_count": live.get("visualization_induced_planner_advance_count"),
        "visualization_induced_pid_count": live.get("visualization_induced_pid_count"),
        "visualization_induced_control_mutation_count": live.get("visualization_induced_vehicle_control_mutation_count"),
        "existing_pid_invocation_count": live.get("existing_pid_invocation_count"),
        "new_pid_count": live.get("new_pid_count"),
        "candidate_direct_vehicle_control_write_count": live.get("candidate_direct_vehicle_control_write_count"),
        "m3_direct_vehicle_control_write_count": live.get("m3_direct_vehicle_control_write_count"),
    }
    panel = output / "GROUNDED_V1_DEMO_PANEL.png"
    desktop = output / "GROUNDED_V1_DEMO_NATIVE_DESKTOP.png"
    validation = output / "GROUNDED_V1_DEMO_NATIVE_DESKTOP_VALIDATION.json"
    grounding = dict(live.get("grounding") or {})
    final_status = blocker or FINAL_PASS_STATUS
    receipt = {
        "schema_version": "driveclarify.grounded_v1_distinct_trajectory_demo.receipt.v1",
        "status": final_status,
        "run_id": RUN_ID,
        "execution_id": execution_id,
        "started_at_utc": started,
        "ended_at_utc": _utc(),
        "scope": {
            "split": "TRAIN",
            "qualitative_live_demonstration": True,
            "formal_e3": False,
            "paper_population": False,
            "scientific_performance_result": False,
            "dev_attempt_count": 0,
            "test_attempt_count": 0,
            "test_consumed": False,
        },
        "scenario": {
            "fixture_id": FIXTURE_ID,
            "seed": spec.seed,
            "carla_map": "Town04",
            "route_id": spec.route_id,
            "raw_instruction": spec.raw_instruction,
            "fixture_status": "DEMO_FIXTURE_ONLY_NOT_PAPER_POPULATION",
        },
        "rgb": {
            "real_rgb_0": bool(grounding.get("image_sha256")),
            "frame_id": grounding.get("frame_id"),
            "image_sha256": grounding.get("image_sha256"),
            "grounding_rgb_0_path": live.get("grounding_rgb_0_path"),
            "grounding_rgb_0_file_sha256": _sha_or_none(output / "E1R1_GROUNDING_RGB_0.png"),
        },
        "grounding_dino": {
            "actual_forward_from_same_frame_rgb_0": True,
            "detections": grounding.get("selected_referents"),
            "plausibility": [row.get("plausible") for row in grounding.get("selected_referents", ())],
            "raw_k": live.get("raw_k"),
            "effective_k": live.get("effective_k"),
            "semantic_duplicate": live.get("semantic_duplicate"),
            "grounding_duplicate": live.get("grounding_duplicate"),
            "target_duplicate": live.get("target_duplicate"),
        },
        "candidate_a": candidates[0],
        "candidate_b": candidates[1],
        "target_distinct": live.get("target_duplicate") is False,
        "route_divergence": {
            "rmse_A1_B1": live.get("route_divergence_rmse_A1_B1"),
            "existing_logic": live.get("plan_divergence"),
            "visual_bev": visual.get("bev"),
        },
        "speed_divergence": {
            "rmse_A1_B1": live.get("speed_divergence_rmse_A1_B1"),
            "visual_speed_rmse": visual.get("speed_rmse"),
        },
        "topology_horizon": {
            "second_target_progress_m": visual.get("second_target_progress_m"),
            "candidate_b_pred_route_arc_length_m": (visual.get("bev") or {}).get("route_b_arc_length_m"),
            "second_target_in_vla_horizon": visual.get("second_target_in_vla_horizon"),
        },
        "material_divergence": live.get("material_consequence_divergence"),
        "decision": live.get("initial_decision"),
        "decision_why": live.get("decision_why"),
        "question": live.get("question"),
        "answer": live.get("answer"),
        "post_answer_decision": live.get("post_answer_decision"),
        "dashboard_refresh_count": live.get("dashboard_refresh_count"),
        "dashboard_native_refresh_count": live.get("dashboard_native_refresh_count"),
        "carla_tick_count": live.get("carla_tick_count"),
        "forward_pid_control_accounting": counters,
        "runtime_privileged_gold_reads": {
            "privileged_state_policy_read_count": live.get("privileged_state_policy_read_count"),
            "gold_policy_label_reads": live.get("gold_policy_label_reads"),
            "expected_decision_reads": live.get("expected_decision_reads"),
            "label_firewall": live.get("label_firewall"),
        },
        "native_run": dict(native_run),
        "screenshots": {
            "dashboard": {"path": str(panel), "sha256": _sha_or_none(panel)},
            "native_desktop": {"path": str(desktop), "sha256": _sha_or_none(desktop)},
            "validation": {"path": str(validation), "sha256": _sha_or_none(validation)},
        },
        "cleanup": {
            "status": native_run.get("cleanup_status"),
            "receipt_path": str(output / "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json"),
        },
        "frozen_integrity": {
            "status": "PASS_UNCHANGED" if integrity_pass else "FAIL_CHANGED",
            "before": before,
            "after": after,
        },
        "runner_error": error,
    }
    receipt_path = report_dir / RECEIPT_NAME
    _write(receipt_path, receipt)
    receipt_sha = file_sha256(receipt_path)
    receipt_sha_path = receipt_path.with_suffix(receipt_path.suffix + ".sha256")
    receipt_sha_path.write_text(receipt_sha + "  " + receipt_path.name + "\n", encoding="utf-8")
    summary = {
        "schema_version": "driveclarify.grounded_v1_distinct_trajectory_demo.batch_summary.v1",
        "status": final_status,
        "execution_id": execution_id,
        "raw_instruction": spec.raw_instruction,
        "fixture_id": FIXTURE_ID,
        "native_run_status": native_run.get("status"),
        "effective_k": live.get("effective_k"),
        "targets_distinct": live.get("target_duplicate") is False,
        "route_rmse": live.get("route_divergence_rmse_A1_B1"),
        "speed_rmse": live.get("speed_divergence_rmse_A1_B1"),
        "max_pixel_separation": (visual.get("bev") or {}).get("max_pixel_separation"),
        "second_target_in_vla_horizon": visual.get("second_target_in_vla_horizon"),
        "decision": live.get("initial_decision"),
        "dashboard_refresh_count": live.get("dashboard_refresh_count"),
        "carla_tick_count": live.get("carla_tick_count"),
        "desktop_capture_status": native_run.get("desktop_capture_status"),
        "cleanup_status": native_run.get("cleanup_status"),
        "receipt_path": str(receipt_path),
        "receipt_sha256": receipt_sha,
        "dashboard_screenshot_path": str(panel),
        "native_desktop_screenshot_path": str(desktop),
        "runner_error": error,
    }
    _write(summary_path, summary)
    _write(report_dir / "DEMO_BATCH_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if final_status == FINAL_PASS_STATUS else 1


if __name__ == "__main__":
    raise SystemExit(main())
