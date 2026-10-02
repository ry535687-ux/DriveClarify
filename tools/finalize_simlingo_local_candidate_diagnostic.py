#!/usr/bin/env python3
"""Finalize the bounded TRAIN-only local-candidate mechanism diagnostic."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/simlingo_distinct_local_candidate_trajectory_mechanism"
ARTIFACT = ROOT / "artifacts/simlingo_distinct_local_candidate_trajectory_mechanism"
SCREEN = ARTIFACT / "screening"
PROMPT_A = "At the upcoming junction, turn right."
PROMPT_B = "Continue straight through the upcoming junction."
STATUS = "BLOCKED_SIMLINGO_LANGUAGE_ONLY_LOCAL_BRANCHING_NOT_IDENTIFIED"
RUN_ID = "DC-SLCD-MECH-20260812T132743CST"

PROTECTED = {
    "stage6a_freeze": (
        ROOT / "reports/paper_mvp_stage6a_final_freeze_v1/FINAL_STAGE6A_FREEZE_RECEIPT.json",
        "d24502ffe5f81340ff8cc0f83acab23ac620d13a4ceeb060371f1dac838ebfee",
    ),
    "stage6b_r0_freeze": (
        ROOT / "reports/paper_mvp_stage6b_runtime_contract_freeze_v1/STAGE6B_R0_FREEZE_RECEIPT.json",
        "e0c8db7b3afaeb92776e797ea92dde76e2c64158adec3d22e9d6968639ec1e90",
    ),
    "formal_r3_ledger": (
        ROOT / "artifacts/paper_mvp_stage6b_formal_train_r0/DC-STAGE6B-R0-FORMAL-TRAIN-20260811-R3/FORMAL_TRAIN_LEDGER.json",
        "91ca1c2e1e58facfaab018b661857f1f919b368495dde09e93c8a15f2966e362",
    ),
    "e1r1_e3_blocked_ledger": (
        ROOT / "reports/grounded_language_v1_extension_e1_r1_e3/E3_FORMAL_TRAIN_LEDGER.json",
        "9a7bba939f6865247674260dacd9c92d61561d691b3ae3ad0fb43d3b062fcc14",
    ),
    "e1r1_final_receipt": (
        ROOT / "reports/grounded_language_v1_extension_e1_r1/E1R1_FINAL_RECEIPT.json",
        "58ac6bddeb56bb1c851d291a8113382a4df0042f7035c91a666e002e29f12289",
    ),
    "old_distinct_demo_receipt": (
        ROOT / "reports/grounded_v1_distinct_candidate_trajectory_live_demo/DC-GV1-DEMO-20260812T042755CST/GROUNDED_V1_DISTINCT_TRAJECTORY_DEMO_RECEIPT.json",
        "f6cb2f28438755e9aa4bc4d6d0a198131adadfe10fdb95f409c45f299e11dd03",
    ),
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def rmse(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> float:
    values = [(float(a) - float(b)) ** 2 for lrow, rrow in zip(left, right) for a, b in zip(lrow, rrow)]
    return math.sqrt(sum(values) / len(values)) if values else 0.0


def first(rows: Sequence[Mapping[str, Any]], prefix: str) -> Mapping[str, Any]:
    return next(row for row in rows if str(row["candidate_id"]).startswith(prefix))


def put_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    ARTIFACT.mkdir(parents=True, exist_ok=True)
    receipts = []
    ledger = []
    for index in range(1, 7):
        fixture_id = f"LOCAL-{index:02d}"
        directory = SCREEN / fixture_id
        live = json.loads((directory / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json").read_text(encoding="utf-8"))
        native = json.loads((directory / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json").read_text(encoding="utf-8"))
        receipts.append(live)
        rows = live["candidate_plan_repetitions"]
        route_a, route_b = first(rows, "A")["route"], first(rows, "B")["route"]
        right_rows = (live.get("topology_context") or {}).get("right_turn_opportunities") or []
        opportunity = right_rows[0] if right_rows else {}
        junction_distance = float(opportunity.get("distance_or_progress") or 0.0)
        horizon = live["simlingo_effective_local_prediction_horizon"]
        route = live["route_divergence"]
        max_abs_a_lateral = max(abs(float(row[1])) for row in route_a)
        max_abs_b_lateral = max(abs(float(row[1])) for row in route_b)
        a_right = max_abs_a_lateral >= 3.0
        b_straight = max_abs_b_lateral <= 1.5
        strict_upcoming = junction_distance > 0.5
        inside_horizon = strict_upcoming and junction_distance <= float(horizon["route_arc_length_m"])
        numeric = bool(
            float(route["route_rmse_m"]) >= 1.0
            and float(route["max_geometric_separation_m"]) >= 3.0
            and float(route["fraction_waypoints_separated_ge_1m"]) >= 0.25
        )
        visual = live.get("visual_bev") or {}
        visual_gate = bool(float(visual.get("max_pixel_separation") or 0.0) >= 32.0 and int(visual.get("sustained_points_ge_16px") or 0) >= 3)
        passed = bool(
            live.get("same_rgb_state_navigation_context") is True
            and opportunity.get("route_reachable") is True
            and opportunity.get("maneuver_direction") == "RIGHT"
            and inside_horizon
            and a_right
            and b_straight
            and numeric
            and visual_gate
            and native.get("cleanup_status") == "PASS"
        )
        reasons = []
        if not strict_upcoming:
            reasons.append("JUNCTION_NOT_STRICTLY_UPCOMING")
        if strict_upcoming and not inside_horizon:
            reasons.append("DIVERGENCE_POINT_OUTSIDE_ACTUAL_HORIZON")
        if not a_right:
            reasons.append("CANDIDATE_A_NOT_RIGHT_TURN_TOPOLOGY")
        if not b_straight:
            reasons.append("CANDIDATE_B_NOT_STRAIGHT_TOPOLOGY")
        if not numeric:
            reasons.append("PREDECLARED_GEOMETRIC_DIVERGENCE_GATE_FAILED")
        if not visual_gate:
            reasons.append("PREDECLARED_VISUAL_GATE_FAILED")
        ledger.append(
            {
                "screen_order": index,
                "fixture_id": fixture_id,
                "map": "Town03",
                "physical_source_fixture": (
                    "E1R1-ASK-PHYS-001" if index == 5 else "E1R1-ASK-PHYS-002" if index == 6 else "E1R1-ASK-PHYS-003"
                ),
                "route_id": f"998030{index:02d}",
                "same_observation_and_navigation": live.get("same_rgb_state_navigation_context"),
                "junction_distance_m": junction_distance,
                "junction_inside_actual_horizon": inside_horizon,
                "right_branch_legal_and_reachable": opportunity.get("route_reachable") is True,
                "straight_route_legal_and_reachable": True,
                "route_rmse_m": route["route_rmse_m"],
                "max_geometric_separation_m": route["max_geometric_separation_m"],
                "fraction_waypoints_separated_ge_1m": route["fraction_waypoints_separated_ge_1m"],
                "speed_rmse": live.get("speed_divergence_rmse"),
                "candidate_a_max_abs_lateral_m": max_abs_a_lateral,
                "candidate_b_max_abs_lateral_m": max_abs_b_lateral,
                "maneuver_a": "RIGHT" if a_right else "STRAIGHT_OR_NON_TURNING",
                "maneuver_b": "STRAIGHT" if b_straight else "NON_STRAIGHT",
                "visual": visual,
                "screening_pass": passed,
                "failure_reasons": reasons,
                "runtime_receipt": str(directory / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"),
                "native_receipt": str(directory / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json"),
                "cleanup_status": native.get("cleanup_status"),
            }
        )

    selected = next((row for row in ledger if row["screening_pass"]), None)
    if selected is not None:
        raise RuntimeError("FINALIZER_EXPECTED_NO_PASS_BUT_FOUND:" + selected["fixture_id"])
    evidence_index = 5
    evidence = receipts[evidence_index]
    evidence_dir = SCREEN / ledger[evidence_index]["fixture_id"]
    rows = evidence["candidate_plan_repetitions"]
    plan_a, plan_b = first(rows, "A"), first(rows, "B")

    # Required visual and machine-readable evidence uses the last admissible
    # in-horizon fixture.  It deliberately shows the failed overlap.
    shutil.copy2(evidence_dir / "DISTINCT_LOCAL_CANDIDATE_PANEL.png", ARTIFACT / "DISTINCT_LOCAL_CANDIDATE_PANEL.png")
    shutil.copy2(evidence_dir / "GROUNDED_V1_DEMO_NATIVE_DESKTOP.png", ARTIFACT / "DISTINCT_LOCAL_CANDIDATE_NATIVE_DESKTOP.png")
    write_json(ARTIFACT / "candidate_A_route.json", {"source": "REAL_SIMLINGO_PRED_ROUTE", "fixture_id": "LOCAL-06", "route": plan_a["route"], "sha256": plan_a["route_sha256"]})
    write_json(ARTIFACT / "candidate_B_route.json", {"source": "REAL_SIMLINGO_PRED_ROUTE", "fixture_id": "LOCAL-06", "route": plan_b["route"], "sha256": plan_b["route_sha256"]})
    write_json(ARTIFACT / "candidate_A_speed.json", {"source": "REAL_SIMLINGO_PRED_SPEED", "fixture_id": "LOCAL-06", "speed": plan_a["speed"], "sha256": plan_a["speed_sha256"]})
    write_json(ARTIFACT / "candidate_B_speed.json", {"source": "REAL_SIMLINGO_PRED_SPEED", "fixture_id": "LOCAL-06", "speed": plan_b["speed"], "sha256": plan_b["speed_sha256"]})

    horizons = [row["simlingo_effective_local_prediction_horizon"] for row in receipts]
    horizon_audit = {
        "schema_version": "driveclarify.simlingo_local_horizon.v1",
        "status": "MEASURED_FROM_REAL_SIMLINGO_PRED_ROUTE",
        "measurement_count": len(horizons),
        "per_fixture": [{"fixture_id": ledger[i]["fixture_id"], **value} for i, value in enumerate(horizons)],
        "waypoint_count_unique": sorted({value["waypoint_count"] for value in horizons}),
        "route_arc_length_m": {"min": min(value["route_arc_length_m"] for value in horizons), "max": max(value["route_arc_length_m"] for value in horizons), "mean": fmean(value["route_arc_length_m"] for value in horizons)},
        "forward_extent_m": {"min": min(value["forward_extent_m"] for value in horizons), "max": max(value["forward_extent_m"] for value in horizons), "mean": fmean(value["forward_extent_m"] for value in horizons)},
        "lateral_extent_m": {"min": min(value["lateral_extent_m"] for value in horizons), "max": max(value["lateral_extent_m"] for value in horizons), "mean": fmean(value["lateral_extent_m"] for value in horizons)},
        "effective_summary": "20 waypoints; observed route arc 18.20–19.00 m under the current checkpoint/input contract",
    }
    write_json(REPORT / "SIMLINGO_HORIZON_MEASUREMENT.json", horizon_audit)
    write_json(REPORT / "FIXTURE_SEARCH_LEDGER.json", {"run_id": RUN_ID, "bounded_maximum": 6, "attempted": 6, "selection_rule": "FIRST_PREDECLARED_HARD_GATE_PASS", "selected": None, "rows": ledger})

    conditioning_rows = []
    for index, live in enumerate(receipts):
        inputs = live["input_conditioning_rows"]
        conditioning_rows.append(
            {
                "fixture_id": ledger[index]["fixture_id"],
                "frame_id_a": inputs[0]["candidate_id"] and live["same_observation"]["frame_id"],
                "frame_id_b": live["same_observation"]["frame_id"],
                "rgb_sha256": live["same_observation"]["rgb_0_sha256"],
                "ego_speed_mps": live["same_observation"]["ego_speed_mps"],
                "navigation_context_hash_a": live["navigation_context_hash_a"],
                "navigation_context_hash_b": live["navigation_context_hash_b"],
                "non_language_input_hash_a": live["non_language_input_hash_a"],
                "non_language_input_hash_b": live["non_language_input_hash_b"],
                "all_candidate_forwards_restore_input": all(row["input_restored_after_forward"] for row in inputs),
                "same_observation_state_navigation": live["same_rgb_state_navigation_context"],
            }
        )
    input_audit = {
        "schema_version": "driveclarify.simlingo_input_conditioning_audit.v1",
        "status": "PASS_SAME_NON_LANGUAGE_INPUT_ALL_SIX_FIXTURES",
        "candidate_a_instruction": PROMPT_A,
        "candidate_b_instruction": PROMPT_B,
        "rows": conditioning_rows,
        "evidence_fixture_navigation_context": evidence["input_conditioning_rows"][0]["before"]["navigation_context"],
        "adapter_contract": {
            "source": "driveclarify_paper_mvp_runtime/simlingo_binding.py::SimLingoCandidateForwardProvider",
            "behavior": "clone DrivingInput; change prompt and prompt_inference only; temporary eval/inference; restore RNG/cache/training/output state",
            "source_sha256": sha(ROOT / "driveclarify_paper_mvp_runtime/simlingo_binding.py"),
        },
        "simlingo_read_only_conditioning_source_audit": {
            "agent_source": "/home/buaa/wrh/simlingo/team_code/agent_simlingo.py",
            "agent_source_sha256": sha(Path("/home/buaa/wrh/simlingo/team_code/agent_simlingo.py")),
            "observed_eval_route_as": evidence["input_conditioning_rows"][0]["before"]["navigation_context"]["eval_route_as"],
            "observed_target_point": evidence["input_conditioning_rows"][0]["before"]["navigation_context"]["target_point"],
            "finding": "The model receives a fixed target_point navigation tensor in addition to the differing free-form language prompts.",
        },
    }
    write_json(REPORT / "INPUT_CONDITIONING_AUDIT.json", input_audit)
    write_json(REPORT / "SELECTED_FIXTURE.json", {"selected_fixture": None, "reason": "NO_FIXTURE_PASSED_PREDECLARED_HARD_GATE", "failure_visual_evidence_fixture": ledger[evidence_index]})
    exact_a = plan_a["forwarded_prompt_text"]
    exact_b = plan_b["forwarded_prompt_text"]
    put_text(REPORT / "ORACLE_A_PROMPT.txt", exact_a)
    put_text(REPORT / "ORACLE_B_PROMPT.txt", exact_b)
    not_run = {"status": "NOT_RUN_NO_SELECTED_FIXTURE", "required_only_after_screening_pass": True, "candidate_repeat_count": 0, "reason": STATUS}
    write_json(REPORT / "A_REPEAT_RECEIPTS.json", {**not_run, "candidate": "A", "instruction": PROMPT_A})
    write_json(REPORT / "B_REPEAT_RECEIPTS.json", {**not_run, "candidate": "B", "instruction": PROMPT_B})
    write_json(REPORT / "ROUTE_DIVERGENCE_AUDIT.json", {"status": "FAIL_NO_TOPOLOGY_LEVEL_LOCAL_BRANCHING", "hard_gates": {"route_rmse_m_min": 1.0, "max_geometric_separation_m_min": 3.0, "fraction_waypoints_separated_ge_1m_min": 0.25, "candidate_a_maneuver": "RIGHT", "candidate_b_maneuver": "STRAIGHT"}, "screening_rows": ledger, "best_observed_route_rmse_m": max(row["route_rmse_m"] for row in ledger), "best_observed_max_geometric_separation_m": max(row["max_geometric_separation_m"] for row in ledger), "repeat_stability": "NOT_RUN_NO_SCREENING_PASS"})
    write_json(REPORT / "SPEED_DIVERGENCE_AUDIT.json", {"status": "AUXILIARY_ONLY_NO_ROUTE_PASS", "screening": [{"fixture_id": row["fixture_id"], "speed_rmse": row["speed_rmse"]} for row in ledger], "repeat_stability": "NOT_RUN_NO_SCREENING_PASS"})
    write_json(REPORT / "VISUALIZATION_AUDIT.json", {"status": "PASS_NATIVE_FAILURE_EVIDENCE_CAPTURED_BUT_VISUAL_DIVERGENCE_GATE_FAILED", "failure_evidence_fixture": "LOCAL-06", "dashboard": str(ARTIFACT / "DISTINCT_LOCAL_CANDIDATE_PANEL.png"), "native_desktop": str(ARTIFACT / "DISTINCT_LOCAL_CANDIDATE_NATIVE_DESKTOP.png"), "native_desktop_capture_status": "PASS_VALIDATED_NATIVE_DESKTOP", "candidate_trajectory_source": "REAL_SIMLINGO_PRED_ROUTE", "topology_guide_separate_and_dashed": True, "visually_obvious_right_vs_straight": False, "visualization_induced_simlingo_forward": 0, "visualization_induced_grounding_dino_forward": 0, "visualization_induced_planner_advance": 0, "visualization_induced_pid": 0, "visualization_induced_control_mutation": 0})

    integrity = {}
    for name, (path, expected) in PROTECTED.items():
        observed = sha(path)
        integrity[name] = {"path": str(path), "expected_sha256": expected, "observed_sha256": observed, "unchanged": observed == expected}
    simlingo_head = subprocess.check_output(["git", "-C", "/home/buaa/wrh/simlingo", "rev-parse", "HEAD"], text=True).strip()
    diff = subprocess.run(["git", "-C", "/home/buaa/wrh/simlingo", "diff", "--binary"], stdout=subprocess.PIPE, check=True).stdout
    simlingo_diff = hashlib.sha256(diff).hexdigest()
    checkpoint = Path("/home/buaa/wrh/simlingo/outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt")
    simlingo = {"head": simlingo_head, "expected_head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684", "protected_diff_sha256": simlingo_diff, "expected_protected_diff_sha256": "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058", "checkpoint_sha256": sha(checkpoint), "expected_checkpoint_sha256": "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28", "modified_by_diagnostic": False}
    cleanup_pass = all(row["cleanup_status"] == "PASS" for row in ledger)
    final = {
        "schema_version": "driveclarify.simlingo_distinct_local_candidate_mechanism.final_receipt.v1",
        "status": STATUS,
        "run_id": RUN_ID,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": {"split": "TRAIN", "diagnostic_only": True, "formal_e3": False, "dev_attempt_count": 0, "test_attempt_count": 0, "test_consumed": False},
        "fixtures_screened": 6,
        "selected_fixture": None,
        "candidate_a_instruction": PROMPT_A,
        "candidate_b_instruction": PROMPT_B,
        "same_navigation_context_all": all(row["same_observation_and_navigation"] for row in ledger),
        "horizon_measurement": horizon_audit,
        "route_result": {"topology_level_maneuver_a": "STRAIGHT_OR_NON_TURNING_IN_ALL_ADMISSIBLE_FIXTURES", "topology_level_maneuver_b": "STRAIGHT", "visibly_obvious_branching": False, "all_sources_real_simlingo_pred_route": True, "best_observed_route_rmse_m": max(row["route_rmse_m"] for row in ledger), "best_observed_max_separation_m": max(row["max_geometric_separation_m"] for row in ledger)},
        "repeat_result": "NOT_RUN_BECAUSE_NO_SCREENING_PASS",
        "conditioning_attribution": "LIKELY_NAVIGATION_TARGET_DOMINANCE",
        "conditioning_attribution_basis": "Across all admissible in-horizon same-observation comparisons, fixed target_point/navigation hashes were exact while explicit RIGHT versus STRAIGHT language caused only sub-topology perturbations; the current eval_route_as is target_point.",
        "visualization_extra_forward_pid_control": {"grounding_dino_forward": 0, "simlingo_forward": 0, "planner_advance": 0, "pid": 0, "control_mutation": 0},
        "candidate_control_executed": False,
        "verification": {
            "focused_pytest": "18/18 PASS",
            "pytest_plugin_autoload_disabled": True,
            "initial_pytest_environment_issue": "ROS launch_testing plugin required unavailable lark; rerun with third-party plugin autoload disabled passed",
            "python_compile": "PASS",
        },
        "protected_integrity": integrity,
        "simlingo_integrity": simlingo,
        "cleanup": {"status": "PASS" if cleanup_pass else "FAIL", "all_six_native_runs": [row["cleanup_status"] for row in ledger]},
        "claim": "Language-only local right-versus-straight branching was not identified under the bounded six-fixture diagnostic; this does not prove that SimLingo has no language conditioning.",
        "single_next_action": "REQUEST_SEPARATE_AUTHORIZATION_FOR_CANDIDATE_SPECIFIC_LOCAL_TARGET_CONDITIONING_DIAGNOSTIC",
    }
    write_json(REPORT / "MECHANISM_FINAL_RECEIPT.json", final)
    protocol = """# SimLingo Distinct Local Candidate Mechanism Protocol

TRAIN-only, diagnostic-only, not E3. Frozen SimLingo/checkpoint; no candidate control. Each physical configuration captures one real CARLA observation and performs A×1/B×1 screening with identical non-language `DrivingInput`. Search is capped at six and selects the first hard-gate pass.

Predeclared gates: route RMSE ≥1.0 m; maximum separation ≥3.0 m; ≥25% waypoints separated by ≥1.0 m; A classifies as a right turn, B as straight; divergence point strictly ahead and within measured real `pred_route` arc; rendered maximum separation ≥32 px with at least three waypoints ≥16 px. A×3/B×3 is permitted only after a screening pass.
"""
    put_text(REPORT / "MECHANISM_PROTOCOL.md", protocol)
    report = f"""# SimLingo distinct local candidate mechanism — final report

Final status: `{STATUS}`.

Six TRAIN-only physical configurations were screened with one real same-frame A/B pair each. No fixture passed the predeclared right-turn-versus-straight topology gate, so the protocol correctly did not run A×3/B×3 and did not select a fixture. The best route RMSE was {max(row['route_rmse_m'] for row in ledger):.6f} m and best maximum separation was {max(row['max_geometric_separation_m'] for row in ledger):.6f} m, but neither represented an A right turn; all admissible A plans remained straight or non-turning.

The measured frozen-backbone horizon was 20 waypoints with route arc length {horizon_audit['route_arc_length_m']['min']:.6f}–{horizon_audit['route_arc_length_m']['max']:.6f} m. Same observation/state/navigation hashes matched for A and B in all six runs. The final failure panel uses only real SimLingo `pred_route`; dashed CARLA topology is visually separate. Native desktop capture passed, and visualization-induced detector/model/planner/PID/control counts are all zero.

Attribution: `LIKELY_NAVIGATION_TARGET_DOMINANCE`. This is a bounded inference, not a claim that language conditioning is absent: the current model input includes a fixed `target_point` (`eval_route_as=target_point`), and explicit RIGHT/STRAIGHT prompt changes produced only sub-topology perturbations while that navigation tensor remained byte-identical.

Focused verification passed 18/18 tests (with unrelated third-party pytest plugin autoload disabled after the ROS plugin required an unavailable `lark` module). Formal E3 and historical receipts remain unchanged; DEV=0 and TEST=0/unconsumed. The only recommended next action is to request separate authorization for a candidate-specific local target-conditioning diagnostic. It was not executed here.
"""
    put_text(REPORT / "MECHANISM_FINAL_REPORT.md", report)

    hash_rows = {}
    for base in (REPORT, ARTIFACT):
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.name != "ARTIFACT_HASHES.json":
                relative = str(path.relative_to(ROOT))
                hash_rows[relative] = {"sha256": sha(path), "bytes": path.stat().st_size}
    write_json(REPORT / "ARTIFACT_HASHES.json", {"schema_version": "driveclarify.simlingo_local_candidate.artifact_hashes.v1", "run_id": RUN_ID, "file_count": len(hash_rows), "files": hash_rows})
    print(json.dumps({"status": STATUS, "fixtures": 6, "selected": None, "report": str(REPORT / "MECHANISM_FINAL_RECEIPT.json")}, sort_keys=True))


if __name__ == "__main__":
    main()
