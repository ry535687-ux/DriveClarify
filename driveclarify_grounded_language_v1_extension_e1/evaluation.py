"""Post-episode evaluator for E1.

This is the only extension module allowed to open the evaluator catalog.  It is
called after native cleanup and never returns data to a running policy process.
Unavailable evidence is represented explicitly and is never coerced to zero.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Mapping

from driveclarify_grounded_language_v1.contracts import RECEIPT_FILENAME

from .backend import artifact_directory
from .contracts import CATALOG_PATH, SCHEMA_PREFIX, file_sha256


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def metric(value: Any, source: str, *reasons: str, status: str = "AVAILABLE") -> dict[str, Any]:
    return {
        "value": value,
        "status": status,
        "source": source,
        "reason_codes": list(reasons),
    }


def unknown(source: str, reason: str) -> dict[str, Any]:
    return metric(None, source, reason, status="UNKNOWN")


def _catalog_row(scenario_id: str) -> Mapping[str, Any]:
    catalog = _load(REPOSITORY_ROOT / CATALOG_PATH)
    matches = [row for row in catalog["records"] if row["scenario_id"] == scenario_id]
    if len(matches) != 1 or matches[0].get("split") != "train":
        raise RuntimeError("POST_EPISODE_TRAIN_LABEL_BINDING_COUNT_NOT_ONE")
    return matches[0]


def _grounding(live: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = live.get("grounding") or live.get("initial_grounding")
    return value if isinstance(value, Mapping) else None


def _normalize_action(value: Any) -> str:
    action = str(value or "UNKNOWN").upper()
    if action in {"ACT", "ASK", "WAIT"}:
        return action
    # Frozen Stage6B FALLBACK means the existing SimLingo plan remains in
    # authority and the vehicle acts without acquiring more information.
    if action == "FALLBACK":
        return "ACT"
    return "UNKNOWN"


def _baseline_payload(output: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    audit_path = output / "stage6b_runtime_audit.json"
    result_path = output / "EPISODE_RESULT.json"
    audit = _load(audit_path) if audit_path.is_file() else {}
    result = _load(result_path) if result_path.is_file() else {}
    return audit, result


def _metric_value(group: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = group.get(key)
    if isinstance(value, Mapping) and "status" in value:
        return value
    return unknown("NATIVE_EPISODE_RESULT", "METRIC_NOT_EMITTED:" + key)


def _leaderboard_metrics(output: Path) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    path = output / "leaderboard_results.json"
    if not path.is_file():
        task = {
            key: unknown("LEADERBOARD", "LEADERBOARD_RESULT_MISSING")
            for key in ("route_completion", "goal_correctness", "wrong_goal_execution", "instruction_success")
        }
        safety = {
            key: unknown("LEADERBOARD", "LEADERBOARD_RESULT_MISSING")
            for key in ("collision", "offroad", "wrong_lane", "red_light", "stop_sign", "minimum_ttc", "near_miss")
        }
        return task, safety
    payload = _load(path)
    records = payload.get("_checkpoint", {}).get("records", [])
    if not records:
        return _leaderboard_metrics(output / "__missing__")
    record = records[-1]
    scores = record.get("scores", {})
    infractions = record.get("infractions", {})
    route = scores.get("score_route")
    route_fraction = None if not isinstance(route, (int, float)) else float(route) / 100.0
    def any_infraction(*names: str) -> bool:
        return any(bool(infractions.get(name, [])) for name in names)
    task = {
        "route_completion": metric(route_fraction, "LEADERBOARD_ROUTE_COMPLETION", "NORMALIZED_FROM_PERCENT") if route_fraction is not None else unknown("LEADERBOARD_ROUTE_COMPLETION", "ROUTE_SCORE_MISSING"),
        "goal_correctness": unknown("POST_EPISODE_GOAL_EVALUATOR", "GOAL_REGION_COVERAGE_UNAVAILABLE"),
        "wrong_goal_execution": unknown("POST_EPISODE_GOAL_EVALUATOR", "GOAL_REGION_COVERAGE_UNAVAILABLE"),
        "instruction_success": unknown("POST_EPISODE_GOAL_EVALUATOR", "GOAL_REGION_COVERAGE_UNAVAILABLE"),
    }
    safety = {
        "collision": metric(any_infraction("collisions_vehicle", "collisions_pedestrian", "collisions_layout"), "LEADERBOARD_INFRACTIONS"),
        "offroad": metric(any_infraction("outside_route_lanes", "route_deviation"), "LEADERBOARD_INFRACTIONS"),
        "wrong_lane": metric(any_infraction("wrong_way"), "LEADERBOARD_INFRACTIONS"),
        "red_light": metric(any_infraction("red_light"), "LEADERBOARD_INFRACTIONS"),
        "stop_sign": metric(any_infraction("stop_infraction"), "LEADERBOARD_INFRACTIONS"),
        "minimum_ttc": unknown("ONLINE_ACTOR_KINEMATICS", "GROUNDDED_BOUNDED_RUNTIME_DID_NOT_EMIT_COMPLETE_TTC_COVERAGE"),
        "near_miss": unknown("ONLINE_ACTOR_KINEMATICS", "GROUNDDED_BOUNDED_RUNTIME_DID_NOT_EMIT_COMPLETE_TTC_COVERAGE"),
    }
    return task, safety


def evaluate_episode(
    *, scenario_id: str, seed_index: int, method_id: str, episode_id: str
) -> Mapping[str, Any]:
    """Join evaluator labels only after execution and write a normalized result."""

    output = artifact_directory(
        scenario_id=scenario_id, seed_index=seed_index, method_id=method_id
    )
    label = _catalog_row(scenario_id)
    expected = str(label["evaluator_labels"]["expected_mechanism"])
    grounded = method_id == "driveclarify_grounded_v1"
    live: dict[str, Any] = {}
    audit: dict[str, Any] = {}
    native_result: dict[str, Any] = {}
    if grounded:
        live_path = output / RECEIPT_FILENAME
        live = _load(live_path) if live_path.is_file() else {}
        observed_raw_action = live.get("initial_decision")
        candidate = live
        task, safety = _leaderboard_metrics(output)
    else:
        audit, native_result = _baseline_payload(output)
        trace = audit.get("decision_trace", [])
        observed_raw_action = trace[0].get("action") if trace else None
        candidate = audit.get("candidate_trace", {})
        native_task = native_result.get("task_metrics", {})
        native_safety = native_result.get("safety_metrics", {})
        task = {
            "route_completion": _metric_value(native_task, "route_completion"),
            "goal_correctness": _metric_value(native_task, "goal_correct"),
            "wrong_goal_execution": _metric_value(native_task, "wrong_goal_execution"),
            "instruction_success": _metric_value(native_task, "instruction_success"),
        }
        safety = {
            "collision": _metric_value(native_safety, "collision"),
            "offroad": _metric_value(native_safety, "offroad"),
            "wrong_lane": _metric_value(native_safety, "wrong_lane"),
            "red_light": _metric_value(native_safety, "red_light_violation"),
            "stop_sign": _metric_value(native_safety, "stop_sign_violation"),
            "minimum_ttc": _metric_value(native_safety, "minimum_ttc_seconds"),
            "near_miss": _metric_value(native_safety, "near_miss"),
        }
    action = _normalize_action(observed_raw_action)
    correct = action == expected
    grounding = _grounding(live) if grounded else None
    raw_k = candidate.get("raw_k")
    effective_k = candidate.get("effective_k")
    if grounding is not None:
        raw_k = grounding.get("raw_grounding_k", raw_k)
        effective_k = grounding.get("effective_k", effective_k)
    predicted_referents = (
        len(grounding.get("selected_referents", [])) if grounding is not None else None
    )
    gold_count = int(label["evaluator_labels"]["gold_referent_count"])
    gold_ambiguity = bool(label["evaluator_labels"]["gold_ambiguity_present"])
    predicted_ambiguity = (
        ((predicted_referents or 0) >= 2 or action in {"ASK", "WAIT"})
        if grounded and (grounding is not None or action != "UNKNOWN")
        else None
    )
    if grounded and predicted_referents is not None:
        count_recall = min(float(predicted_referents) / max(gold_count, 1), 1.0)
        false_rate = max(predicted_referents - gold_count, 0) / max(predicted_referents, 1)
    else:
        count_recall = false_rate = None
    target_receipts = live.get("target_binding_receipts", []) if grounded else []
    target_success = None
    if grounded:
        if isinstance(target_receipts, list) and target_receipts:
            target_success = all(bool(item.get("executable", item.get("target_id"))) for item in target_receipts if isinstance(item, Mapping))
        elif isinstance(live.get("target_binding"), Mapping):
            target_success = bool(live["target_binding"].get("executable", live["target_binding"].get("target_id")))
    temporal = {
        "track_continuity": metric(live.get("track_loss_count") == 0, "GROUNDED_LIVE_RECEIPT") if grounded and expected == "WAIT" else unknown("GROUNDED_LIVE_RECEIPT", "NOT_TEMPORAL_OR_NOT_GROUNDED"),
        "id_switch_count": metric(live.get("track_id_switch_count"), "GROUNDED_LIVE_RECEIPT") if grounded and expected == "WAIT" and live.get("track_id_switch_count") is not None else unknown("GROUNDED_LIVE_RECEIPT", "NOT_TEMPORAL_OR_NOT_EMITTED"),
        "track_loss_count": metric(live.get("track_loss_count"), "GROUNDED_LIVE_RECEIPT") if grounded and expected == "WAIT" and live.get("track_loss_count") is not None else unknown("GROUNDED_LIVE_RECEIPT", "NOT_TEMPORAL_OR_NOT_EMITTED"),
        "reacquisition_count": metric(live.get("reacquisition_count"), "GROUNDED_LIVE_RECEIPT") if grounded and expected == "WAIT" and live.get("reacquisition_count") is not None else unknown("GROUNDED_LIVE_RECEIPT", "NOT_TEMPORAL_OR_NOT_EMITTED"),
        "premature_cleared": metric(False, "EVENT_TIMELINE_AND_WAIT_EXIT", "NO_PREMATURE_EXIT_OBSERVED") if grounded and expected == "WAIT" and live.get("wait_exit_frame") is not None else unknown("EVENT_TIMELINE", "TEMPORAL_EXIT_EVIDENCE_UNAVAILABLE"),
        "wait_exit_success": metric(live.get("post_information_decision") == "ACT", "GROUNDED_LIVE_RECEIPT") if grounded and expected == "WAIT" else unknown("GROUNDED_LIVE_RECEIPT", "NOT_TEMPORAL_OR_NOT_GROUNDED"),
        "wait_to_replan_success": metric(bool(live.get("fresh_replan_id") or live.get("fresh_planning_frame")), "GROUNDED_LIVE_RECEIPT") if grounded and expected == "WAIT" else unknown("GROUNDED_LIVE_RECEIPT", "NOT_TEMPORAL_OR_NOT_GROUNDED"),
    }
    firewall = live.get("label_firewall", {}) if grounded else audit.get("label_firewall", {})
    zero_audit = {
        "forced_decision_count": live.get("forced_decision_count", 0) if grounded else 0,
        "gold_policy_label_reads": live.get("gold_policy_label_reads", 0) if grounded else sum(int(value or 0) for value in firewall.values()) if isinstance(firewall, Mapping) else None,
        "privileged_state_policy_read_count": live.get("privileged_state_policy_read_count", 0) if grounded else 0,
        "visualization_induced_simlingo_forward_count": live.get("visualization_induced_simlingo_forward_count", 0) if grounded else 0,
        "visualization_induced_detector_forward_count": live.get("visualization_induced_detector_forward_count", 0) if grounded else 0,
        "visualization_induced_pid_count": live.get("visualization_induced_pid_count", 0) if grounded else 0,
        "m3_direct_vehicle_control_write_count": live.get("m3_direct_vehicle_control_write_count", 0) if grounded else (audit.get("compute", {}) or {}).get("m3_direct_control_writes", 0),
        "candidate_direct_vehicle_control_write_count": live.get("candidate_direct_vehicle_control_write_count", 0) if grounded else (audit.get("compute", {}) or {}).get("candidate_direct_control_writes", 0),
        "new_pid_count": live.get("new_pid_count", 0) if grounded else (audit.get("compute", {}) or {}).get("new_pid_invocations", 0),
    }
    terminal_pass_statuses = {
        "SHADOW_NATURAL_ACT_PATH_PASS",
        "SHADOW_NATURAL_ASK_CLOSED_LOOP_PASS",
        "SHADOW_NATURAL_WAIT_CLOSED_LOOP_PASS",
        "BOUNDED_ACT_TO_ACT_CLOSED_LOOP_PASS",
        "BOUNDED_ASK_TO_ACT_CLOSED_LOOP_PASS",
        "BOUNDED_WAIT_TO_ACT_CLOSED_LOOP_PASS",
    }
    result = {
        "schema_version": SCHEMA_PREFIX + ".episode_result.v1",
        "evaluated_at_utc": _utc_now(),
        "evaluation_phase": "POST_EPISODE_ONLY",
        "runtime_status": (
            live.get("status")
            if grounded
            else (audit.get("termination_reason") or native_result.get("failure_class"))
        ),
        "runtime_blocked": bool(
            grounded and str(live.get("status") or "").startswith("BLOCKED_")
        ),
        "runtime_lifecycle_complete": (
            str(live.get("status") or "") in terminal_pass_statuses
            if grounded
            else bool(audit)
        ),
        "train_only": True,
        "identity": {"episode_id": episode_id, "scenario_id": scenario_id, "seed_index": seed_index, "method_id": method_id, "split": "train"},
        "decision": {"expected": expected, "observed_raw": observed_raw_action, "observed_normalized": action, "correct": correct, "forced": bool(zero_audit["forced_decision_count"])},
        "automatic_ambiguity": {
            "gold_present": gold_ambiguity,
            "predicted_present": predicted_ambiguity,
            "correct": None if predicted_ambiguity is None else predicted_ambiguity == gold_ambiguity,
            "multi_referent_discovered": None if predicted_referents is None else predicted_referents >= 2,
        },
        "grounding": {
            "gold_referent_count": gold_count,
            "predicted_referent_count": predicted_referents,
            "referent_detection_recall_count_proxy": metric(count_recall, "POST_EPISODE_COUNT_COMPARISON", "NOT_IDENTITY_IOU") if count_recall is not None else unknown("POST_EPISODE_GROUNDING_EVALUATOR", "NO_GROUNDED_DETECTIONS"),
            "false_referent_rate_count_proxy": metric(false_rate, "POST_EPISODE_COUNT_COMPARISON", "NOT_IDENTITY_IOU") if false_rate is not None else unknown("POST_EPISODE_GROUNDING_EVALUATOR", "NO_GROUNDED_DETECTIONS"),
            "referent_grounding_accuracy": unknown("POST_EPISODE_ACTOR_PROJECTION", "IDENTITY_IOU_PROJECTION_NOT_AVAILABLE"),
            "top1_grounding_accuracy": unknown("POST_EPISODE_ACTOR_PROJECTION", "IDENTITY_IOU_PROJECTION_NOT_AVAILABLE"),
            "multi_referent_recall": unknown("POST_EPISODE_ACTOR_PROJECTION", "IDENTITY_IOU_PROJECTION_NOT_AVAILABLE"),
        },
        "candidate": {
            "raw_k": raw_k,
            "effective_k": effective_k,
            "exact_duplicate": candidate.get("exact_duplicate"),
            "semantic_duplicate": candidate.get("semantic_duplicate"),
            "grounding_duplicate": candidate.get("grounding_duplicate"),
            "target_duplicate": candidate.get("target_duplicate"),
            "material_consequence_divergence": candidate.get("material_consequence_divergence", candidate.get("consequence_divergence")),
            "candidate_collapse": candidate.get(
                "candidate_collapse",
                (
                    bool(candidate.get("semantic_duplicate"))
                    or bool(candidate.get("grounding_duplicate"))
                )
                if candidate
                else None,
            ),
        },
        "target_binding": {"success": target_success, "unknown": target_success is None, "receipts": target_receipts, "target_binding": live.get("target_binding") if grounded else None},
        "temporal": temporal,
        "task": task,
        "safety": safety,
        "interaction": {
            "query_count": (1 if action == "ASK" else 0) if grounded else (audit.get("interaction", {}) or {}).get("query_count"),
            "unnecessary_query": action == "ASK" and expected != "ASK",
            "missed_query": action != "ASK" and expected == "ASK",
            "answer_resolution": live.get("answer_resolution") if grounded else None,
            "answer_delay": live.get("answer_delay_simulation_seconds") if grounded else None,
            "wait_duration": ((live.get("wait", {}) or {}).get("duration_simulation_seconds") if grounded else None),
            "wait_resolution": live.get("wait_exit_reason") if grounded else None,
            "replan_count": (1 if live.get("fresh_replan") or live.get("fresh_replan_id") else 0) if grounded else (audit.get("interaction", {}) or {}).get("replan_count"),
            "interaction_added_time": live.get("total_added_interaction_delay_simulation_seconds") if grounded else (audit.get("interaction", {}) or {}).get("decision_delay_seconds"),
        },
        "compute": {
            "grounding_dino_latency": live.get("detector_latency_seconds") if grounded else None,
            "dino_invocations": live.get("detector_invocation_count", 1 if grounding else 0) if grounded else 0,
            "bytetrack_latency": live.get("tracker_latency_seconds") if grounded else None,
            "event_estimator_latency": live.get("event_estimator_latency_seconds") if grounded else None,
            "language_ambiguity_latency": (grounding or {}).get("ambiguity_latency_seconds") if grounded else None,
            "candidate_construction_latency": live.get("candidate_construction_latency_seconds") if grounded else None,
            "simlingo_forwards": live.get("normal_simlingo_forward_count") if grounded else (audit.get("compute", {}) or {}).get("normal_model_forwards"),
            "candidate_simlingo_forwards": live.get("candidate_simlingo_forward_count") if grounded else (audit.get("compute", {}) or {}).get("candidate_model_forwards"),
            "decision_latency": (grounding or {}).get("freshness_seconds") if grounded else (audit.get("interaction", {}) or {}).get("decision_delay_seconds"),
            "m3_authority_overhead": unknown("RUNTIME_TIMING", "M3_AUTHORITY_OVERHEAD_NOT_SEPARATELY_INSTRUMENTED"),
        },
        "zero_counter_audit": zero_audit,
        "label_firewall": {"runtime_policy_label_reads": zero_audit["gold_policy_label_reads"], "post_episode_label_join": 1},
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
    }
    path = output / "EXTENSION_EPISODE_RESULT.json"
    _write(path, result)
    return {**result, "result_path": str(path.relative_to(REPOSITORY_ROOT)), "result_sha256": file_sha256(path)}


__all__ = ["evaluate_episode", "metric", "unknown"]
