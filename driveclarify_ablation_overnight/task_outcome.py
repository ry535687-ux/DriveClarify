"""Offline task scoring from physical ego state and separately held passenger truth.

This module is never imported by the driving agent. It delegates physical stop
measurement to the unchanged, previously qualified V2 evaluator. Neither the
tested relation, selected route, ASK nor replan receipts are scoring inputs.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from driveclarify_rq3_paired_v2.task_evaluator import (
    VERSION as PHYSICAL_EVALUATOR_VERSION,
    extract_complete_episode,
    score_task_truth,
)

VERSION = "ABL_OVERNIGHT_PHYSICAL_STOP_AND_CONTINUATION_V1"
SAFETY_INFRACTIONS = (
    "collisions_pedestrian", "collisions_vehicle", "collisions_layout",
    "outside_route_lanes", "red_light", "stop_infraction",
)


def physical_relation(binding):
    """Ground truth from authored region bindings, never a method prediction."""
    mapping = binding.get("candidate_region_map", {})
    known = {x["region_id"] for x in binding.get("regions", [])}
    if set(mapping) != {"A", "B"} or any(x not in known for x in mapping.values()):
        return "UNKNOWN"
    return "TASK_EQUIVALENT" if mapping["A"] == mapping["B"] else "TASK_CRITICAL"


def evaluate_physical_task(trace, binding, truth, receipt, trace_sha256, official_record):
    """Score a complete episode without discarding a complete driving failure.

    The instruction has two obligations: a stop in the correct public bay, then
    continuation of the assigned route. We report the local obligation and native
    continuation independently; only their conjunction is full task completion.
    Missing terminal or safety evidence stays null, not a successful safe stop.
    """
    duration = (official_record or {}).get("meta", {}).get("duration_game")
    outcome = extract_complete_episode(trace, binding, receipt, trace_sha256, duration)
    truth_valid = isinstance(truth, dict) and truth.get("candidate_id") in {"A", "B"}
    relation = physical_relation(binding)
    if not truth_valid or relation == "UNKNOWN":
        goal = {"correct_goal": None, "wrong_goal": None, "status": "UNKNOWN"}
    else:
        goal = score_task_truth(outcome, binding, truth)
    status = (official_record or {}).get("status")
    scores = (official_record or {}).get("scores", {})
    score_route = scores.get("score_route")
    native = None
    if (isinstance(score_route, (int, float)) and not isinstance(score_route, bool)
            and math.isfinite(score_route) and 0 <= score_route <= 100 and status):
        native = bool(status == "Completed" and score_route >= 99.999)
    local = None if goal["correct_goal"] is None else bool(goal["correct_goal"])
    complete = None if local is None or native is None else bool(local and native)
    infractions = (official_record or {}).get("infractions", {})
    safety_known = all(isinstance(infractions.get(k), list) for k in SAFETY_INFRACTIONS)
    counts = {k: len(infractions[k]) if isinstance(infractions.get(k), list) else None
              for k in SAFETY_INFRACTIONS}
    safe = None if not safety_known else not any(counts.values())
    return {
        "evaluation_version": VERSION,
        "physical_evaluator_version": PHYSICAL_EVALUATOR_VERSION,
        "physical_task_relation_truth": relation,
        "physical_outcome": outcome,
        "truth_valid": truth_valid,
        "correct_local_task_obligation": local,
        "wrong_target_execution": goal["wrong_goal"],
        "native_route_complete": native,
        "native_route_completion_percent": score_route,
        "correct_task_complete": complete,
        "correct_safe_task_complete": None if complete is None or safe is None else bool(complete and safe),
        "safety_observation_complete": safety_known,
        "safety_infraction_counts": counts,
        "all_official_infraction_counts": {k: len(v) if isinstance(v, list) else None
                                           for k, v in infractions.items()},
        "offroad_and_wrong_lane_separation": "OFFICIAL_OUTSIDE_ROUTE_LANES_IS_COMBINED_ENDPOINT",
        "official_status": status,
        "task_metric_evaluable": complete is not None,
        "policy_prediction_used_for_evaluation": False,
        "ask_answer_replan_used_as_task_completion": False,
        "reason_codes": ([] if outcome["status"] == "KNOWN" else [outcome.get("reason", "PHYSICAL_OUTCOME_UNKNOWN")])
            + ([] if truth_valid else ["MISSING_OR_INVALID_EVALUATION_TRUTH"])
            + ([] if native is not None else ["NATIVE_CONTINUATION_UNKNOWN"])
            + ([] if safety_known else ["SAFETY_ENDPOINT_INCOMPLETE"]),
    }


def evaluate_run_directory(output, binding_path, truth):
    """Read only physical trace, terminal receipt, official endpoint and truth."""
    output = Path(output)
    owner = output / "owner_evidence"
    def read(path, default):
        return json.loads(path.read_text()) if path.is_file() else default
    binding = read(Path(binding_path), {})
    trace_path = owner / "V2_NATIVE_STATE_TRACE.jsonl"
    raw = trace_path.read_bytes() if trace_path.is_file() else b""
    parse_error = None
    try:
        trace = [json.loads(x) for x in raw.splitlines() if x.strip()]
    except (ValueError, TypeError) as exc:
        trace, parse_error = [], type(exc).__name__
    receipt = read(owner / "V2_TRACE_TERMINAL_RECEIPT.json", {})
    records = read(output / "official_checkpoint.json", {}).get("_checkpoint", {}).get("records", [])
    official = records[0] if len(records) == 1 else {}
    result = evaluate_physical_task(trace, binding, truth, receipt, hashlib.sha256(raw).hexdigest(), official)
    result["source_paths"] = {"trace": str(trace_path), "binding": str(binding_path),
                              "terminal": str(owner / "V2_TRACE_TERMINAL_RECEIPT.json"),
                              "official": str(output / "official_checkpoint.json")}
    result["trace_parse_error"] = parse_error
    return result
