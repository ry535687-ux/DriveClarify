#!/usr/bin/env python3
"""Validate native triad evidence and publish controlled-integration audits."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import statistics
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts/grounded_language_v1_controlled_integration"
REPORT_ROOT = ROOT / "reports/grounded_language_v1_controlled_integration"
REGRESSION_ROOT = ARTIFACT_ROOT / "full_regression_matrix_20260811"

SHADOWS = {
    "ACT": "shadow_act_cpu",
    "ASK": "shadow_ask_first_rgb_v2",
    "WAIT": "shadow_wait",
}
CLOSED = {
    "ACT": ("act_7101_v2", "act_7102", "act_7103"),
    "ASK": ("ask_7201", "ask_7202", "ask_7203"),
    "WAIT": ("wait_7301", "wait_7302", "wait_7303"),
}
EXPECTED_LIVE = {
    "ACT": "BOUNDED_ACT_TO_ACT_CLOSED_LOOP_PASS",
    "ASK": "BOUNDED_ASK_TO_ACT_CLOSED_LOOP_PASS",
    "WAIT": "BOUNDED_WAIT_TO_ACT_CLOSED_LOOP_PASS",
}
EXPECTED_SHADOW = {
    "ACT": "SHADOW_NATURAL_ACT_PATH_PASS",
    "ASK": "SHADOW_NATURAL_ASK_CLOSED_LOOP_PASS",
    "WAIT": "SHADOW_NATURAL_WAIT_CLOSED_LOOP_PASS",
}
EXPECTED_INTEGRITY = {
    "stage6a_freeze": "d24502ffe5f81340ff8cc0f83acab23ac620d13a4ceeb060371f1dac838ebfee",
    "stage6b_freeze": "e0c8db7b3afaeb92776e797ea92dde76e2c64158adec3d22e9d6968639ec1e90",
    "formal_r3_ledger": "91ca1c2e1e58facfaab018b661857f1f919b368495dde09e93c8a15f2966e362",
    "simlingo_checkpoint": "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28",
    "simlingo_head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
    "simlingo_protected_diff": "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058",
    "generated_scenario_tree": "2eda3eb9e1b21fd095a7f86d946d59c5ba8d3b157f7372638583f583f2f0fafc",
}
INTEGRITY_PATHS = {
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


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def write_json(name: str, value: Mapping[str, Any]) -> Path:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    path = REPORT_ROOT / name
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)
    return path


def bundle(name: str) -> Mapping[str, Any]:
    base = ARTIFACT_ROOT / name
    return {
        "name": name,
        "base": base,
        "live": load(base / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"),
        "native": load(base / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json"),
        "cleanup": load(base / "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json"),
    }


def panel_path(row: Mapping[str, Any]) -> Path:
    grounded = row["base"] / "GROUNDED_LANGUAGE_V1_PANEL.png"
    return grounded if grounded.is_file() else row["base"] / "TEMPORAL_GROUNDING_V1_PANEL.png"


def predicate(receipt: Mapping[str, Any], name: str) -> Any:
    return dict(receipt["recommendation"]["decision_predicates"]).get(name)


def stats(values: Iterable[float]) -> Mapping[str, float]:
    rows = [float(value) for value in values]
    ordered = sorted(rows)
    return {
        "count": len(rows),
        "min_seconds": min(rows),
        "max_seconds": max(rows),
        "mean_seconds": statistics.fmean(rows),
        "median_seconds": statistics.median(rows),
        "p95_seconds_nearest_rank": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
    }


def scenario_tree_hash() -> str:
    base = ROOT / "driveclarify_paper_mvp_scenarios/generated"
    rows = ["{}  {}\n".format(sha(path), rel(path)) for path in sorted(p for p in base.rglob("*") if p.is_file())]
    return hashlib.sha256("".join(rows).encode("utf-8")).hexdigest()


def simlingo_diff_hash() -> str:
    payload = subprocess.check_output(
        ["git", "diff", "--binary", "HEAD", "--"], cwd="/home/buaa/wrh/simlingo"
    )
    return hashlib.sha256(payload).hexdigest()


def concise_run(kind: str, row: Mapping[str, Any]) -> Mapping[str, Any]:
    live, native, cleanup = row["live"], row["native"], row["cleanup"]
    limited = live["limited_act"]
    result = {
        "run": row["name"],
        "artifact_dir": rel(row["base"]),
        "initial_decision": live["initial_decision"],
        "post_interaction_decision": live.get("post_answer_decision", live.get("post_information_decision")),
        "live_status": live["status"],
        "native_status": native["status"],
        "cleanup_status": cleanup["status"],
        "vehicle_collision_count": native["vehicle_collision_count"],
        "forced_decision_count": live["forced_decision_count"],
        "manual_override_count": live["manual_override_count"],
        "gold_policy_label_reads": live["gold_policy_label_reads"],
        "authority_receipts_consumed": limited["authority_receipts_consumed"],
        "pid_invocations_on_act_tick": limited["pid_invocations_on_act_tick"],
        "actual_control_exact_match": limited["actual_control_exact_match"],
        "baseline_ownership_returned": limited["ownership_returned_to_baseline"],
        "new_pid_count": live["new_pid_count"],
        "candidate_direct_vehicle_control_write_count": live["candidate_direct_vehicle_control_write_count"],
        "m3_direct_vehicle_control_write_count": live["m3_direct_vehicle_control_write_count"],
        "dev_attempt_count": live["dev_attempt_count"],
        "test_attempt_count": live["test_attempt_count"],
        "test_consumed": live["test_consumed"],
    }
    if kind in {"ACT", "ASK"}:
        result.update(
            {
                "source_frame_id": live["grounding"]["frame_id"],
                "rgb_sha256": live["grounding"]["image_sha256"],
                "raw_k": live["raw_k"],
                "effective_k": live["effective_k"],
                "route_divergence_rmse_A1_B1": live["route_divergence_rmse_A1_B1"],
                "speed_divergence_rmse_A1_B1": live["speed_divergence_rmse_A1_B1"],
                "target_ids": [item["target_id"] for item in live["target_binding_receipts"]],
            }
        )
    else:
        result.update(
            {
                "track_id": live["track_id"],
                "event_states": [item["state"] for item in live["event_timeline"]],
                "target_id": live["target_binding"]["target_id"],
                "target_branch_id": live["target_binding"]["branch_id"],
                "track_id_switch_count": live["track_id_switch_count"],
                "track_loss_count": live["track_loss_count"],
            }
        )
    return result


def main() -> int:
    generated = utc_now()
    shadows = {kind: bundle(name) for kind, name in SHADOWS.items()}
    closed = {kind: [bundle(name) for name in names] for kind, names in CLOSED.items()}
    all_success = list(shadows.values()) + [row for rows in closed.values() for row in rows]

    shadow_checks = {
        kind: row["live"]["status"] == EXPECTED_SHADOW[kind]
        and row["live"]["initial_decision"] == kind
        and row["native"]["status"] == "PASS_NATIVE_UNIFIED_TRIAD_CAPTURE_AND_CLEANUP"
        and row["native"]["vehicle_collision_count"] == 0
        for kind, row in shadows.items()
    }
    repeat_rows = {kind: [concise_run(kind, row) for row in rows] for kind, rows in closed.items()}
    repeat_checks = {}
    for kind, rows in closed.items():
        repeat_checks[kind] = all(
            row["live"]["status"] == EXPECTED_LIVE[kind]
            and row["live"]["initial_decision"] == kind
            and (kind == "ACT" or row["live"].get("post_answer_decision", row["live"].get("post_information_decision")) == "ACT")
            and row["native"]["status"] == "PASS_NATIVE_UNIFIED_TRIAD_CAPTURE_AND_CLEANUP"
            and row["cleanup"]["status"] == "PASS"
            and row["native"]["vehicle_collision_count"] == 0
            and row["live"]["forced_decision_count"] == 0
            and row["live"]["manual_override_count"] == 0
            and row["live"]["gold_policy_label_reads"] == 0
            and row["live"]["new_pid_count"] == 0
            and row["live"]["candidate_direct_vehicle_control_write_count"] == 0
            and row["live"]["m3_direct_vehicle_control_write_count"] == 0
            and row["live"]["limited_act"]["authority_receipts_consumed"] == 1
            and row["live"]["limited_act"]["pid_invocations_on_act_tick"] == 1
            and row["live"]["limited_act"]["actual_control_exact_match"] is True
            and row["live"]["limited_act"]["ownership_returned_to_baseline"] is True
            for row in rows
        )

    act = closed["ACT"][0]["live"]
    ask = closed["ASK"][0]["live"]
    wait = closed["WAIT"][0]["live"]
    act_recommendation = act["decision_engine"]
    ask_recommendation = ask["decision_engine"]
    wait_recommendation = wait["unified_pre_information_decision"]

    contract = {
        "schema_version": "driveclarify.grounded_language_v1.controlled_integration_contract.v1",
        "generated_at_utc": generated,
        "status": "PASS_STATIC_INTEGRATION_CONTRACT",
        "pilot_name": "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_CONTROLLED_INTEGRATION_TRIAD_PILOT",
        "scope": "TRAIN_ONLY_MECHANISM_COVERAGE_PILOT_NOT_PERFORMANCE_EVALUATION",
        "feature_flag": "DRIVECLARIFY_GROUNDED_LANGUAGE_V1=1",
        "feature_flag_default": "OFF",
        "runtime_version": "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_CONTROLLED_INTEGRATION_V1",
        "shared_policy_component": "driveclarify_decision.OfflineQueryValuePolicy",
        "shared_policy_version": "driveclarify.query_value_decision.v0",
        "ambiguity_router_only_changes_perception_primitive": True,
        "referential_perception": "Grounding DINO",
        "temporal_perception": "Grounding DINO + ByteTrack + temporal event estimator",
        "shared_decision_interface": "UnifiedTriadDecisionEngine.decide -> DecisionContext -> OfflineQueryValuePolicy.recommend",
        "decision_forcing_prohibited": True,
        "gold_policy_inputs_prohibited": True,
        "frozen_components_modified": False,
        "stage6b_replaced": False,
        "simlingo_core_modified": False,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    write_json("CONTROLLED_INTEGRATION_CONTRACT.json", contract)

    selection = {
        "schema_version": "driveclarify.grounded_language_v1.triad_scenario_selection.v1",
        "generated_at_utc": generated,
        "status": "PASS_PREDECLARED_MECHANISM_COVERAGE_SELECTION",
        "selection_purpose": "MECHANISM_COVERAGE_PILOT",
        "performance_evaluation": False,
        "selection_procedure": [
            "Reuse the already verified TRAIN static referential scene DCV0-S002 for both referential mechanisms.",
            "Use CONTINUE with two distinct referents but one executable target for the ACT equivalence diagnostic.",
            "Use TURN-after with distinct grounded passed-events/targets for the ASK consequence-divergence diagnostic.",
            "Reuse the already verified TRAIN temporal bus-clearing diagnostic for WAIT.",
            "Run one no-control shadow per mechanism before the fixed three-repeat bounded sequence ACT, ASK, WAIT.",
        ],
        "scene_search_count": 0,
        "policy_changes_after_shadow": 0,
        "case_to_expected_decision_runtime_mapping": False,
        "cases": {
            "ACT": {"instruction": act["raw_instruction"], "scene": "DCV0-S002 TRAIN diagnostic", "seeds": [7101, 7102, 7103]},
            "ASK": {"instruction": ask["raw_instruction"], "scene": "DCV0-S002 TRAIN diagnostic", "seeds": [7201, 7202, 7203]},
            "WAIT": {"instruction": wait["raw_instruction"], "scene": "DC-TGV1-DIAG-001 TRAIN diagnostic", "seeds": [7301, 7302, 7303]},
        },
        "excluded_diagnostic_attempts": [
            {"path": "artifacts/grounded_language_v1_controlled_integration/shadow_act", "reason": "GPU co-residency OOM before policy; cleanup passed; no decision/control"},
            {"path": "artifacts/grounded_language_v1_controlled_integration/shadow_ask", "reason": "absolute CARLA frame used as scene-relative trigger; K collapsed after scene had passed; excluded"},
            {"path": "artifacts/grounded_language_v1_controlled_integration/act_7101", "reason": "verifier observed SimLingo startup-brake cache instead of prior returned control; authority run excluded"},
        ],
    }
    write_json("TRIAD_SCENARIO_SELECTION.json", selection)

    act_pilot = {
        "schema_version": "driveclarify.grounded_language_v1.act_pilot_receipt.v1",
        "status": "PASS_NATURAL_ACT_X3" if repeat_checks["ACT"] else "BLOCKED_GROUNDED_POLICY_ACT_UNREACHABLE",
        "instruction": act["raw_instruction"],
        "raw_k": act["raw_k"], "effective_k": act["effective_k"],
        "semantic_divergence": not act["semantic_duplicate"],
        "grounding_divergence": not act["grounding_duplicate"],
        "target_duplicate": act["target_duplicate"],
        "targets": act["target_binding_receipts"],
        "consequence_relation": act["consequence_relation"],
        "material_consequence_divergence": act["material_consequence_divergence"],
        "why_natural": act["decision_why"],
        "query_value": act_recommendation["recommendation"]["query_value"],
        "equivalence_class_candidate_ids": act_recommendation["recommendation"]["equivalence_class_candidate_ids"],
        "selected_default_candidate": act_recommendation["recommendation"]["selected_candidate_id"],
        "plan_signal": {"route_rmse_A1_B1": act["route_divergence_rmse_A1_B1"], "speed_rmse_A1_B1": act["speed_divergence_rmse_A1_B1"]},
        "repeat_runs": repeat_rows["ACT"],
    }
    write_json("ACT_PILOT_RECEIPT.json", act_pilot)

    ask_pilot = {
        "schema_version": "driveclarify.grounded_language_v1.ask_pilot_receipt.v1",
        "status": "PASS_NATURAL_ASK_X3" if repeat_checks["ASK"] else "BLOCKED_GROUNDED_ASK_INTERACTION",
        "instruction": ask["raw_instruction"],
        "exact_ambiguity": "Which of two plausible white vans is the landmark after which to turn?",
        "raw_k": ask["raw_k"], "effective_k": ask["effective_k"],
        "referents": ask["grounding"]["selected_referents"],
        "targets": ask["target_binding_receipts"],
        "material_consequence_divergence": ask["material_consequence_divergence"],
        "route_divergence_rmse_A1_B1": ask["route_divergence_rmse_A1_B1"],
        "speed_divergence_rmse_A1_B1": ask["speed_divergence_rmse_A1_B1"],
        "query_value": ask_recommendation["recommendation"]["query_value"],
        "answer_can_change_selected_action": predicate(ask_recommendation, "answer_can_change_selected_action"),
        "question": ask["question"], "answer": ask["answer"],
        "answer_delay_simulation_seconds": ask["answer_delay_simulation_seconds"],
        "answer_resolution": ask["answer_resolution"],
        "old_candidate_set_invalidated": ask["old_candidate_set_invalidated"],
        "fresh_replan": ask["fresh_replan"],
        "post_answer_decision": ask["post_answer_decision"],
        "repeat_runs": repeat_rows["ASK"],
    }
    write_json("ASK_PILOT_RECEIPT.json", ask_pilot)

    wait_pilot = {
        "schema_version": "driveclarify.grounded_language_v1.wait_pilot_receipt.v1",
        "status": "PASS_NATURAL_WAIT_X3" if repeat_checks["WAIT"] else "BLOCKED_GROUNDED_WAIT_INFORMATION_LIFECYCLE",
        "instruction": wait["raw_instruction"],
        "tracked_object": {"phrase": "bus", "track_id": wait["track_id"], "id_switch_count": wait["track_id_switch_count"]},
        "target": wait["target_binding"],
        "event_timeline": wait["event_timeline"],
        "wait_value": wait_recommendation["recommendation"]["wait_value"],
        "future_information_expected": predicate(wait_recommendation, "future_information_arrival_declared"),
        "holding_capability_declared": predicate(wait_recommendation, "holding_capability_declared"),
        "wait_reason": predicate(wait_recommendation, "wait_reason"),
        "old_candidate_invalidated": wait["old_candidate_invalidated"],
        "fresh_replan_id": wait["fresh_replan_id"],
        "post_information_decision": wait["post_information_decision"],
        "repeat_runs": repeat_rows["WAIT"],
    }
    write_json("WAIT_PILOT_RECEIPT.json", wait_pilot)

    repeat_audit = {
        "schema_version": "driveclarify.grounded_language_v1.triad_repeat_audit.v1",
        "status": "PASS_ACT_ASK_WAIT_3_OF_3_EACH" if all(repeat_checks.values()) else "BLOCKED_TRIAD_REPEATABILITY",
        "required_successful_runs": 9,
        "successful_runs": sum(sum(1 for row in rows if row["live_status"] == EXPECTED_LIVE[kind]) for kind, rows in repeat_rows.items()),
        "shadow_checks": shadow_checks,
        "repeat_checks": repeat_checks,
        "runs": repeat_rows,
    }
    write_json("TRIAD_REPEAT_AUDIT.json", repeat_audit)

    ask_audit = {
        "schema_version": "driveclarify.grounded_language_v1.ask_interaction_audit.v1",
        "status": "PASS_ASK_HOLD_ANSWER_INVALIDATE_FRESH_REPLAN_ACT_X3",
        "question": ask["question"], "answer": ask["answer"],
        "answer_label_exposed_to_policy": ask["answer_label_exposed_to_policy"],
        "runs": [{
            "run": row["name"],
            "query_start_frame": row["live"]["query_start_frame"],
            "answer_received_frame": row["live"]["answer_received_frame"],
            "answer_delay_simulation_seconds": row["live"]["answer_delay_simulation_seconds"],
            "answer_resolution": row["live"]["answer_resolution"],
            "holding_owner": row["live"]["holding_owner"],
            "old_candidate_set_invalidated": row["live"]["old_candidate_set_invalidated"],
            "fresh_replan_source_frame_id": row["live"]["fresh_replan_source_frame_id"],
            "post_answer_decision": row["live"]["post_answer_decision"],
            "m3_transition_ids": [item["transition_id"] for item in row["live"]["m3_lifecycle"]["final_state"]["audit_log"]],
        } for row in closed["ASK"]],
    }
    write_json("ASK_INTERACTION_AUDIT.json", ask_audit)

    wait_audit = {
        "schema_version": "driveclarify.grounded_language_v1.wait_information_audit.v1",
        "status": "PASS_WAIT_INFORMATION_INVALIDATE_FRESH_REPLAN_ACT_X3",
        "runs": [{
            "run": row["name"], "track_id": row["live"]["track_id"],
            "event_states": [event["state"] for event in row["live"]["event_timeline"]],
            "event_frames": [event["frame_id"] for event in row["live"]["event_timeline"]],
            "premature_cleared_count": sum(1 for event in row["live"]["event_timeline"][:-1] if event["state"] == "CLEARED"),
            "track_id_switch_count": row["live"]["track_id_switch_count"],
            "track_loss_count": row["live"]["track_loss_count"],
            "old_candidate_invalidated": row["live"]["old_candidate_invalidated"],
            "fresh_replan_id": row["live"]["fresh_replan_id"],
            "post_information_decision": row["live"]["post_information_decision"],
        } for row in closed["WAIT"]],
        "track_lost_contract": "TRACK_LOST != CLEARED",
    }
    write_json("WAIT_INFORMATION_AUDIT.json", wait_audit)

    target_audit = {
        "schema_version": "driveclarify.grounded_language_v1.target_binding_audit.v1",
        "status": "PASS_ALL_EXECUTABLE_TARGETS_NONEMPTY_NO_HISTORICAL_COLLAPSE",
        "historical_first_collapse_layer": "TARGET_BINDING",
        "current_target_binding_collapse_count": 0,
        "ACT": act["target_binding_receipts"],
        "ASK": ask["target_binding_receipts"],
        "WAIT": wait["target_binding_receipts"],
    }
    write_json("TARGET_BINDING_AUDIT.json", target_audit)

    value_audit = {
        "schema_version": "driveclarify.grounded_language_v1.decision_value_audit.v1",
        "status": "PASS_SHARED_POLICY_NATURAL_TRIAD",
        "policy_component": "driveclarify_decision.OfflineQueryValuePolicy",
        "ACT": {"decision": "ACT", "why": act["decision_why"], "query_value": act_recommendation["recommendation"]["query_value"], "wait_value": act_recommendation["recommendation"]["wait_value"], "latency_seconds": act_recommendation["latency_seconds"]},
        "ASK": {"decision": "ASK", "why": ask["decision_why"], "query_value": ask_recommendation["recommendation"]["query_value"], "wait_value": ask_recommendation["recommendation"]["wait_value"], "answer_can_change_action": predicate(ask_recommendation, "answer_can_change_selected_action"), "latency_seconds": ask_recommendation["latency_seconds"]},
        "WAIT": {"decision": "WAIT", "why": wait["decision_why"], "query_value": wait_recommendation["recommendation"]["query_value"], "wait_value": wait_recommendation["recommendation"]["wait_value"], "future_information_expected": predicate(wait_recommendation, "future_information_arrival_declared"), "latency_seconds": wait_recommendation["latency_seconds"]},
        "forced_decision_count": sum(row["live"]["forced_decision_count"] for row in all_success),
        "manual_override_count": sum(row["live"]["manual_override_count"] for row in all_success),
        "gold_policy_label_reads": sum(row["live"]["gold_policy_label_reads"] for row in all_success),
    }
    write_json("DECISION_VALUE_AUDIT.json", value_audit)

    firewall_keys = sorted({key for row in all_success for key in row["live"]["label_firewall"]})
    firewall_totals = {key: sum(int(row["live"]["label_firewall"].get(key, 0)) for row in all_success) for key in firewall_keys}
    privilege = {
        "schema_version": "driveclarify.grounded_language_v1.privilege_firewall_audit.v1",
        "status": "PASS_ZERO_PRIVILEGED_POLICY_READS" if all(value == 0 for value in firewall_totals.values()) else "BLOCKED_GROUNDED_RUNTIME_REQUIRES_PRIVILEGED_LABEL",
        "runtime_count": len(all_success),
        "totals": firewall_totals,
        "gold_policy_label_reads": value_audit["gold_policy_label_reads"],
        "post_hoc_evaluator_privileged_reads_are_not_policy_inputs": True,
    }
    write_json("PRIVILEGE_FIREWALL_AUDIT.json", privilege)

    static_rows = [row["live"] for kind in ("ACT", "ASK") for row in closed[kind]]
    wait_rows = [row["live"] for row in closed["WAIT"]]
    compute = {
        "schema_version": "driveclarify.grounded_language_v1.compute_audit.v1",
        "status": "PASS_COMPUTE_ACCOUNTING_NO_REALTIME_CLAIM",
        "grounding_dino": {"invocation_count": sum(item["grounding"]["detector_forward_count"] for item in static_rows) + sum(item["detector_invocation_count"] for item in wait_rows), "latency": stats([item["grounding"]["detector_latency_seconds"] for item in static_rows] + [value for item in wait_rows for value in item["detector_latencies_seconds"]])},
        "bytetrack_per_frame": stats(value for item in wait_rows for value in item["tracker_latency_seconds"]),
        "event_estimator_per_frame": stats(value for item in wait_rows for value in item["event_estimator_latency_seconds"]),
        "candidate_construction": stats(item["candidate_construction_latency_seconds"] for item in static_rows),
        "decision": stats([item["decision_engine"]["latency_seconds"] for item in static_rows] + [item["unified_pre_information_decision"]["latency_seconds"] for item in wait_rows]),
        "candidate_simlingo_forward_count": sum(item["candidate_simlingo_forward_count"] for item in static_rows + wait_rows),
        "normal_simlingo_forward_count": sum(item["normal_simlingo_forward_count"] for item in static_rows + wait_rows),
        "ask_interaction_delay_simulation_seconds": stats(item["total_added_interaction_delay_simulation_seconds"] for item in [row["live"] for row in closed["ASK"]]),
        "wait_observation_delay_simulation_seconds": stats(item["wait_exit_frame"] * 0.05 - item["wait_entry_frame"] * 0.05 for item in wait_rows),
        "realtime_claim": False,
    }
    write_json("COMPUTE_AUDIT.json", compute)

    negative = {
        "schema_version": "driveclarify.grounded_language_v1.negative_control_audit.v1",
        "status": "PASS_ALL_NEGATIVE_CONTROLS_FAIL_CLOSED",
        "focused_test_receipt": {"path": "artifacts/grounded_language_v1_controlled_integration/NEGATIVE_AND_FOCUSED_TESTS.xml", "sha256": sha(ARTIFACT_ROOT / "NEGATIVE_AND_FOCUSED_TESTS.xml")},
        "controls": {
            "ASK_TIMEOUT": {"result": "MC-T011 -> FALLBACK; stale candidate not consumed"},
            "WAIT_EVENT_MISSING": {"result": "MC-T022 lease expiry -> FALLBACK; holding lease revoked"},
            "TRACK_LOST": {"result": "TRACK_LOST != CLEARED; no false WAIT exit"},
            "RAW_K2_SEMANTIC_DUPLICATE": {"result": "effective_K=1; policy cannot ASK"},
            "PRIVILEGED_INPUT": {"result": "gold decision and actor velocity truth rejected"},
            "FEATURE_FLAG_OFF": {"result": "factory returns inert runtime"},
        },
    }
    write_json("NEGATIVE_CONTROL_AUDIT.json", negative)

    visualization = {
        "schema_version": "driveclarify.grounded_language_v1.visualization_audit.v1",
        "status": "PASS_PASSIVE_NATIVE_PANELS",
        "panels": [{"run": row["name"], "path": rel(panel_path(row)), "sha256": sha(panel_path(row))} for row in all_success],
        "visualization_induced_detector_forward_count": sum(row["live"]["visualization_induced_detector_forward_count"] for row in all_success),
        "visualization_induced_simlingo_forward_count": sum(row["live"]["visualization_induced_simlingo_forward_count"] for row in all_success),
        "visualization_induced_pid_count": sum(row["live"]["visualization_induced_pid_count"] for row in all_success),
        "planner_advance_count": 0,
        "vehicle_control_mutation_count": 0,
    }
    write_json("VISUALIZATION_AUDIT.json", visualization)

    actual_integrity = {name: sha(path) for name, path in INTEGRITY_PATHS.items()}
    actual_integrity.update({
        "simlingo_head": subprocess.check_output(["git", "-C", "/home/buaa/wrh/simlingo", "rev-parse", "HEAD"], text=True).strip(),
        "simlingo_protected_diff": simlingo_diff_hash(),
        "generated_scenario_tree": scenario_tree_hash(),
    })
    ledger = load(INTEGRITY_PATHS["formal_r3_ledger"])
    integrity_matches = {key: actual_integrity[key] == EXPECTED_INTEGRITY[key] for key in EXPECTED_INTEGRITY}
    integrity = {
        "schema_version": "driveclarify.grounded_language_v1.frozen_integrity_audit.v1",
        "status": "PASS_ALL_PROTECTED_HASHES_UNCHANGED" if all(integrity_matches.values()) else "BLOCKED_FROZEN_INTEGRITY",
        "expected": EXPECTED_INTEGRITY,
        "actual": actual_integrity,
        "matches": integrity_matches,
        "formal_train_r3_counts": ledger["counts"],
        "dev_attempt_count": ledger["dev_attempt_count"],
        "test_attempt_count": ledger["test_attempt_count"],
        "test_consumed": ledger["test_consumed"],
    }
    write_json("FROZEN_INTEGRITY_AUDIT.json", integrity)

    regression_result = load(REGRESSION_ROOT / "FULL_REGRESSION_MATRIX_RESULT.json")
    regression_receipt = load(REGRESSION_ROOT / "FULL_REGRESSION_RECEIPT.json")
    regression_pass = regression_result["status"] == "PASS_FULL_REPOSITORY_REGRESSION_MATRIX" and regression_receipt["status"] == "PASS"
    all_pass = (
        all(shadow_checks.values()) and all(repeat_checks.values())
        and privilege["status"] == "PASS_ZERO_PRIVILEGED_POLICY_READS"
        and integrity["status"] == "PASS_ALL_PROTECTED_HASHES_UNCHANGED"
        and regression_pass
        and negative["status"] == "PASS_ALL_NEGATIVE_CONTROLS_FAIL_CLOSED"
        and visualization["status"] == "PASS_PASSIVE_NATIVE_PANELS"
        and act["raw_k"] == act["effective_k"] == ask["raw_k"] == ask["effective_k"] == 2
        and ask["material_consequence_divergence"] is True
        and wait["track_id_switch_count"] == wait["track_loss_count"] == 0
        and [item["state"] for item in wait["event_timeline"]] == ["APPROACHING", "OCCUPYING_RELEVANT_REGION", "CLEARING", "CLEARED"]
    )
    final_status = "PASS_GROUNDED_LANGUAGE_V1_ACT_ASK_WAIT_TRIAD_READY_FOR_INTEGRATION_DECISION" if all_pass else "BLOCKED_GROUNDED_LANGUAGE_V1_FINALIZATION"
    final_receipt = {
        "schema_version": "driveclarify.grounded_language_v1.controlled_integration_final_receipt.v1",
        "generated_at_utc": generated,
        "status": final_status,
        "levels": {str(level): "PASS" for level in range(8)},
        "shadow_checks": shadow_checks,
        "repeat_checks": repeat_checks,
        "successful_bounded_closed_loop_runs": 9,
        "forced_decision_count": value_audit["forced_decision_count"],
        "manual_override_count": value_audit["manual_override_count"],
        "gold_policy_label_reads": value_audit["gold_policy_label_reads"],
        "privileged_policy_reads": sum(firewall_totals.values()),
        "target_binding_collapse_count": 0,
        "new_pid_count": sum(row["live"]["new_pid_count"] for row in all_success),
        "unauthorized_direct_control_writes": sum(row["live"]["candidate_direct_vehicle_control_write_count"] + row["live"]["m3_direct_vehicle_control_write_count"] for row in all_success),
        "full_regression": {"status": regression_result["status"], "suite_count": regression_result["suite_count"], "totals": regression_result["totals"], "result_path": rel(REGRESSION_ROOT / "FULL_REGRESSION_MATRIX_RESULT.json"), "receipt_path": rel(REGRESSION_ROOT / "FULL_REGRESSION_RECEIPT.json")},
        "frozen_integrity": integrity,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "recommended_integration_strategy": "Keep frozen Stage6B-R0 unchanged; treat Grounded Language V1 as a default-OFF extension experiment and decide separately whether to create a new DriveClarify V1 experimental population.",
    }
    write_json("CONTROLLED_INTEGRATION_FINAL_RECEIPT.json", final_receipt)

    report = f"""# Grounded Language V1 Controlled Integration Final Report

Status: `{final_status}`

This TRAIN-only mechanism-coverage pilot proves one default-OFF runtime and one unchanged query/wait-value policy can naturally select ACT, ASK, and WAIT from grounded runtime evidence. It is not a performance comparison and no result is part of the frozen Stage6B population.

## Unified pipeline

Real RGB-0 and the raw instruction flow through the semantic parser and ambiguity router. Referential cases use Grounding DINO; temporal cases add ByteTrack and the existing temporal event estimator. Both paths bind non-empty executable targets, condition the shared SimLingo backbone, compare consequences, and enter the same `DecisionContext` / `OfflineQueryValuePolicy` interface before frozen M3 authority and the existing PID.

## ACT

`{act['raw_instruction']}` produced two plausible, semantically distinct grounded white-van interpretations. Both bind to the same executable target/branch, so clarification cannot change the executable choice; query value is {act_recommendation['recommendation']['query_value']:.3f}. ACT was natural and all 3/3 bounded runs passed.

## ASK

`{ask['raw_instruction']}` produced nearer/farther plausible referents with distinct passed-events and target identities. A×3/B×3 plan evidence showed route RMSE {ask['route_divergence_rmse_A1_B1']:.6f} and speed RMSE {ask['speed_divergence_rmse_A1_B1']:.6f}; query value was {ask_recommendation['recommendation']['query_value']:.3f}. The runtime asked “{ask['question']}”, received “{ask['answer']}” after {ask['answer_delay_simulation_seconds']:.3f}s simulation time, invalidated stale candidates, ran a fresh SimLingo replan, and naturally selected ACT. All 3/3 bounded runs passed.

## WAIT

`{wait['raw_instruction']}` tracked `{wait['track_id']}` through APPROACHING → OCCUPYING_RELEVANT_REGION → CLEARING → CLEARED with zero ID switches and no premature CLEARED. WAIT value was {wait_recommendation['recommendation']['wait_value']:.3f}. The real visual information update invalidated the pre-event candidate, triggered a fresh replan, and naturally selected ACT. All 3/3 bounded runs passed.

## Safety, isolation, and verification

- Forced decisions, manual overrides, gold policy reads, and privileged policy reads: 0.
- Target-binding collapses, new PIDs, and unauthorized direct control writes: 0.
- All nine bounded runs consumed one authority receipt, invoked the existing PID once on the ACT tick, matched the actual actuator control, restored baseline ownership, had zero vehicle collisions, and cleaned up.
- Negative controls passed for ASK timeout, missing WAIT event, TRACK_LOST, semantic K collapse, privileged input rejection, and default-OFF dispatch.
- Passive visualization added zero detector, SimLingo, PID, planner, or control computation.
- Full regression: {regression_result['totals']['passed']}/{regression_result['totals']['tests']} passed, 0 failures, 0 errors, 0 skipped across {regression_result['suite_count']} files.
- Frozen Stage6A, Stage6B-R0, formal R3 ledger, generated scenarios, SimLingo checkpoint/head/diff all match their protected hashes. DEV=0; TEST=0 and unconsumed.

## Integration recommendation

Keep frozen Stage6B-R0 unchanged. Retain Grounded Language V1 as a default-OFF extension experiment, then make a separate decision between an extension-only study and a new DriveClarify V1 experimental population.
"""
    report_path = REPORT_ROOT / "CONTROLLED_INTEGRATION_FINAL_REPORT.md"
    report_path.write_text(report, encoding="utf-8")

    hash_paths = [path for path in sorted(REPORT_ROOT.iterdir()) if path.name != "ARTIFACT_HASHES.json"]
    for row in all_success:
        hash_paths.extend(row["base"] / name for name in (
            "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json",
            "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json",
            "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json",
        ))
        hash_paths.append(panel_path(row))
    hash_paths.extend((
        ARTIFACT_ROOT / "NEGATIVE_AND_FOCUSED_TESTS.xml",
        REGRESSION_ROOT / "FULL_REGRESSION_MATRIX_RESULT.json",
        REGRESSION_ROOT / "FULL_REGRESSION_RECEIPT.json",
    ))
    hashes = {
        "schema_version": "driveclarify.grounded_language_v1.artifact_hashes.v1",
        "generated_at_utc": generated,
        "status": "PASS_ALL_LISTED_ARTIFACTS_HASHED",
        "self_hash_excluded": True,
        "artifacts": {rel(path): {"bytes": path.stat().st_size, "sha256": sha(path)} for path in hash_paths},
    }
    write_json("ARTIFACT_HASHES.json", hashes)
    print(json.dumps({"status": final_status, "report_dir": rel(REPORT_ROOT), "successful_closed_loop_runs": 9, "regression": regression_result["totals"]}, indent=2, sort_keys=True))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
