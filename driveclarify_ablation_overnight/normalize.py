"""Read-only normalization of every planned run, including missing/failed arms.

No statistics, dispatch, retry selection or policy scoring is performed here.
Physical task scoring is delegated to the independent frozen-region evaluator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from collections import defaultdict

from driveclarify_ablation_overnight.task_outcome import evaluate_physical_task

VERSION = "ABL_OVERNIGHT_NORMALIZATION_V4_POLICY_TIMING_DIAGNOSTICS_ONLY"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def adapt_manifest(document, protocol_id):
    rows = document if isinstance(document, list) else document.get("runs", document.get("rows", []))
    groups = defaultdict(list)
    output = []
    for index, original in enumerate(rows):
        row = dict(original)
        phase = row.get("phase", row.get("stage", ""))
        phase = "DEVELOPMENT" if str(phase).startswith("DEV") else phase
        row.update(experiment_id=row.get("experiment_id", row.get("experiment")),
                   condition_id=row.get("condition_id", row.get("condition")),
                   configuration_id=row.get("configuration_id", row.get("variant")),
                   phase=phase, protocol_id=row.get("protocol_id", protocol_id))
        groups[(row["experiment_id"], row["pair_id"])].append((row.get("schedule_position", index), row))
        output.append(row)
    for group in groups.values():
        for order, (_, row) in enumerate(sorted(group, key=lambda x: x[0]), 1):
            row.setdefault("order_in_pair", order)
    return output


class EvidenceReader:
    def __init__(self):
        self.index = []
        self.errors = {}

    def raw(self, path):
        path = Path(path)
        if not path.is_file():
            return None
        before = path.stat().st_size
        raw = path.read_bytes()
        after = path.stat().st_size
        self.index.append({"path": str(path), "bytes_read": len(raw), "sha256": sha(raw),
                           "stable_size_during_read": before == after == len(raw)})
        return raw

    def json(self, path, default=None):
        raw = self.raw(path)
        if raw is None:
            return {} if default is None else default
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("EXPECTED_JSON_OBJECT")
            return value
        except (ValueError, UnicodeDecodeError) as exc:
            self.errors[str(path)] = type(exc).__name__
            return {} if default is None else default

    def lines(self, path):
        raw = self.raw(path)
        if raw is None:
            return [], b""
        rows = []
        for n, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("EXPECTED_JSON_OBJECT_ROW")
                rows.append(value)
            except (ValueError, UnicodeDecodeError) as exc:
                self.errors[str(path) + ":" + str(n)] = type(exc).__name__
        return rows, raw


def _event_time(ask, trace):
    match = re.fullmatch(r"v11-frame-(\d+)", str(ask.get("observation_id", "")))
    frame = int(match.group(1)) if match else None
    found = next((r for r in trace if r.get("frame") == frame), {})
    return found.get("simulation_time_s")


def normalize_run(planned):
    """Return one result and evidence index, without removing incomplete data."""
    reader = EvidenceReader()
    out = Path(planned["output"])
    owner = out / "owner_evidence"
    process = reader.json(out / "process_job/PROCESS_RECEIPT.json")
    official_all = reader.json(out / "official_checkpoint.json")
    records = official_all.get("_checkpoint", {}).get("records", [])
    official = records[0] if len(records) == 1 else {}
    trace, raw_trace = reader.lines(owner / "V2_NATIVE_STATE_TRACE.jsonl")
    terminal = reader.json(owner / "V2_TRACE_TERMINAL_RECEIPT.json")
    control, _ = reader.lines(owner / "ABL_ACTUAL_CONTROL_TIMELINE.jsonl")
    decisions, _ = reader.lines(owner / "ABL_DECISION_TIMELINE.jsonl")
    candidates, _ = reader.lines(owner / "ABL_CANDIDATE_TIMELINE.jsonl")
    temporal, _ = reader.lines(owner / "RQ3_TEMPORAL_DECISION_TIMELINE.jsonl")
    temporal_receipt = reader.json(owner / "RQ3_TEMPORAL_DECISION_RECEIPT.json")
    lifecycle = reader.json(owner / "RQ3_LIFECYCLE_TIMING_RECEIPT.json")
    supervision = reader.json(owner / "V11_SUPERVISION_RECEIPT.json")
    counter = supervision.get("counters", {})
    agent_status = reader.json(owner / "V11_AGENT_STATUS.json")
    heartbeat = reader.json(owner / "V11_RUNTIME_HEARTBEAT.json")
    runtime_identity = reader.json(owner / "V2_RUNTIME_IDENTITY.json")
    runtime_config = reader.json(owner / "ABL_RUNTIME_CONFIG.json")
    config = reader.json(Path(planned["config_path"])) if planned.get("config_path") else {}
    binding = reader.json(Path(planned["task_binding_path"])) if planned.get("task_binding_path") else {}
    truth_id = planned.get("evaluation_truth_candidate_id")
    truth = {"candidate_id": truth_id} if truth_id is not None else {}
    task = evaluate_physical_task(trace, binding, truth, terminal, sha(raw_trace), official)
    asks = []
    for path in sorted((owner / "oracle_exchange").glob("ask-*.json")):
        ask = reader.json(path)
        if ask.get("durable") is True and ask.get("action") == "ASK":
            asks.append(ask)
        else:
            reader.errors[str(path)] = "NON_DURABLE_OR_INVALID_ASK"
    answer = reader.json(owner / "oracle_exchange/ORACLE_ANSWER.json")
    release = reader.json(owner / "oracle_exchange/FORMAL_ANSWER_RELEASE_RECEIPT.json")
    answer_binding_ok = bool(answer and any(answer.get("query_id") == a.get("query_id")
                            and answer.get("ask_receipt_id") == a.get("receipt_id") for a in asks))
    if answer and not answer_binding_ok:
        reader.errors[str(owner / "oracle_exchange/ORACLE_ANSWER.json")] = "ANSWER_WITHOUT_MATCHING_DURABLE_QUESTION"
    if release and release.get("answer_released_after_durable_ask") is not True:
        reader.errors[str(owner / "oracle_exchange/FORMAL_ANSWER_RELEASE_RECEIPT.json")] = "ANSWER_RELEASE_ORDER_NOT_CONFIRMED"
    if terminal.get("raw_prompt_mismatches", 0) or terminal.get("observer_ego_control_writes", 0):
        reader.errors[str(owner / "V2_TRACE_TERMINAL_RECEIPT.json")] = "NATIVE_INPUT_OR_OBSERVER_CONTROL_INTEGRITY"
    replan = reader.json(owner / "ONLINE_ROUTE_INSTALL_RECEIPT.json")
    reader.json(owner / "RQ1_V2_FULL_REPLAN_RECEIPT.json")
    resource, _ = reader.lines(out / "process_job/RESOURCE_USAGE.jsonl")
    reader.raw(out / "process_job/evaluator.log")
    reader.raw(out / "process_job/answer_broker.log")
    reader.json(owner / "ABL_FATAL_INTEGRITY.json")

    official_status = str(official.get("status", ""))
    setup_failure = "couldn't be set up" in official_status or "Invalid sensors" in official_status
    endpoint_technical = any(x in official_status.lower() for x in ("crashed", "simulation crashed", "agent error"))
    duration = official.get("meta", {}).get("duration_game")
    exposed = bool(trace or control or candidates or terminal.get("model_forward_count", 0)
                   or agent_status.get("a1_active_forward_count", 0)
                   or heartbeat.get("model_forward_return_count", 0)
                   or heartbeat.get("vehicle_control_return_count", 0)
                   or (not setup_failure and finite(duration) and duration > 0))
    started = out.exists() and any(out.iterdir())
    ended = bool(process)
    complete_trace = task["physical_outcome"]["status"] == "KNOWN"
    critical_names = ("V2_NATIVE_STATE_TRACE.jsonl", "V2_TRACE_TERMINAL_RECEIPT.json",
                      "official_checkpoint.json", "ORACLE_ANSWER.json",
                      "FORMAL_ANSWER_RELEASE_RECEIPT.json", "oracle_exchange/ask-", "ABL_FATAL_INTEGRITY.json")
    critical_errors = {k: v for k, v in reader.errors.items() if any(name in k for name in critical_names)}
    if not started:
        status = "PLANNED"
    elif not ended:
        status = "RUNNING"
    elif not exposed:
        status = "PRE_AGENT_FAILED"
    elif (setup_failure or endpoint_technical or not complete_trace or critical_errors
          or process.get("evaluator_exit") not in (None, 0)):
        status = "TECHNICAL_INTERRUPTION"
    else:
        major = task["safety_infraction_counts"]
        failed = (task["correct_task_complete"] is False or any(x for x in major.values() if x is not None)
                  or official_status.startswith("Failed"))
        status = "DRIVING_FAILURE" if failed else "COMPLETE"
    complete = status in ("COMPLETE", "DRIVING_FAILURE")

    result = {"run_id": planned["run_id"], "status": status, "started": bool(started),
              "agent_exposed": exposed, "terminal_complete": complete,
              "driving_failure": status == "DRIVING_FAILURE",
              "technical_interruption": status in ("PRE_AGENT_FAILED", "TECHNICAL_INTERRUPTION"),
              "termination_reason": official_status or ("PROCESS_ENDED_WITHOUT_EPISODE_TERMINAL" if ended else status),
              "normalization_version": VERSION, "task_outcome": task,
              "language_task_complete": task["correct_task_complete"] if complete else None,
              "wrong_target_execution": (None if task["wrong_target_execution"] is None else bool(task["wrong_target_execution"])),
              "native_route_complete": task["native_route_complete"] if exposed else None,
              "offroad": None, "wrong_lane": None,
              "physical_relation_truth": task["physical_task_relation_truth"],
              "missing_reasons": {"offroad": "ONLY_COMBINED_OFFICIAL_ENDPOINT_AVAILABLE",
                                  "wrong_lane": "ONLY_COMBINED_OFFICIAL_ENDPOINT_AVAILABLE"},
              "raw_parse_errors": reader.errors, "critical_integrity_errors": critical_errors}
    missing = result["missing_reasons"]
    if not complete:
        missing["language_task_complete"] = "NO_COMPLETE_INDEPENDENT_TASK_ENDPOINT:" + status
    inf = official.get("infractions", {})
    def incident(keys):
        if not exposed or not all(isinstance(inf.get(k), list) for k in keys):
            return None
        positive = any(inf[k] for k in keys)
        return bool(positive) if positive or complete else None
    result["collision"] = incident(("collisions_pedestrian", "collisions_vehicle", "collisions_layout"))
    result["traffic_violation"] = incident(("red_light", "stop_infraction"))
    result["offroad_or_wrong_lane"] = incident(("outside_route_lanes",))
    result["timeout"] = incident(("route_timeout", "scenario_timeouts"))
    result["nonprogress"] = incident(("vehicle_blocked",))
    result["fallback"] = (any("FALLBACK" in str(x.get("policy_decision", x.get("requested_action_after_joint_timing", "")))
                               for x in control + decisions) if exposed else None)
    result["ask_requested_count"] = sum(r.get("requested_action_after_joint_timing") == "ASK" for r in decisions) if exposed else None
    result["ask_emitted_count"] = len(asks) if exposed else None
    result["answer_released_count"] = 1 if release else (0 if exposed else None)
    result["answer_binding_valid"] = answer_binding_ok if answer else None
    result["answer_received_count"] = counter.get("passenger_answer_reads", 1 if lifecycle.get("answer") else (0 if complete and not answer else None))
    result["fresh_replan_count"] = counter.get("route_transactions", 1 if replan.get("committed") else (0 if complete else None))
    result["actual_control_count"] = terminal.get("control_return_count", len(control) if control else (0 if ended and not exposed else None))
    for label, field in (("TASK_EQUIVALENT", "relation_equivalent_count"), ("TASK_CRITICAL", "relation_divergent_count"), ("UNKNOWN", "relation_unknown_count")):
        result[field] = sum(r.get("relation") == label for r in decisions) if exposed else None
    result["relation_predictions"] = [{k: r.get(k) for k in ("frame", "simulation_time_s", "relation", "trajectory_metric_m", "trajectory_metric_reason")} for r in decisions]
    result["question_requests"] = [{k: r.get(k) for k in ("frame", "simulation_time_s", "question_requested", "requested_action_after_joint_timing")} for r in decisions if r.get("question_requested")]
    result["actual_question_receipts"] = asks

    origin = trace[0].get("simulation_time_s") if trace else None
    ask_time = _event_time(asks[0], trace) if asks else None
    ask_timing = temporal_receipt.get("ask", lifecycle.get("ask")) or {}
    answer_timing = lifecycle.get("answer") or {}
    sufficient = temporal_receipt.get("first_evidence_sufficiency", lifecycle.get("first_evidence_sufficiency")) or {}
    if not sufficient:
        sufficient = next((r for r in temporal if r.get("EpistemicEvidenceSufficient") is True), {})
    suff_time = sufficient.get("simulation_time_s")
    answer_time = answer_timing.get("simulation_time_s")
    result["first_evidence_sufficient_sim_s"] = suff_time - origin if finite(suff_time) and finite(origin) else None
    result["postask_answer_wait_sim_s"] = answer_time - ask_time if finite(answer_time) and finite(ask_time) and answer_time >= ask_time else None
    margin = ask_timing.get("remaining_margin_s") if asks else None
    result["ask_remaining_margin_sim_s"] = margin if finite(margin) else None
    result["answer_remaining_margin_sim_s"] = margin - (answer_time - ask_time) if all(finite(x) for x in (margin, answer_time, ask_time)) else None
    result["preask_active_wait_sim_s"] = None
    result["episode_clock_origin_simulation_s"] = origin
    result["episode_clock_origin_definition"] = "FIRST_RECORDED_NATIVE_STATE_FRAME_NOT_WALL_CLOCK"
    result["actual_ask_simulation_s"] = ask_time
    result["actual_answer_received_simulation_s"] = answer_time
    # A separately named descriptive window from public geometry. It does not
    # claim physical TTC, safe ASK eligibility, or replace missing timely labels.
    anchor = config.get("method_input", {}).get("observation_anchor_xyz")
    radius = config.get("method_input", {}).get("anchor_capture_distance_m")
    anchor_time = None
    if isinstance(anchor, list) and len(anchor) == 3 and all(finite(v) for v in anchor) and finite(radius) and radius >= 0:
        for row in trace:
            xyz = row.get("xyz")
            if isinstance(xyz, list) and len(xyz) == 3 and all(finite(v) for v in xyz):
                if math.sqrt(sum((a - b) ** 2 for a, b in zip(xyz, anchor))) <= radius:
                    anchor_time = row.get("simulation_time_s")
                    break
    nominal_end = anchor_time + 1.8 if finite(anchor_time) else None
    result["nominal_window_question"] = (bool(anchor_time <= ask_time <= nominal_end)
        if finite(anchor_time) and finite(ask_time) else (False if finite(anchor_time) and complete and not asks else None))
    result["nominal_window_definition"] = {
        "name": "SCRIPTED_ANCHOR_WINDOW", "source": "PUBLIC_CONFIG_GEOMETRY_AND_PHYSICAL_STATE_TRACE",
        "coordinate_system": "CARLA_WORLD_XYZ_METERS_3D_EUCLIDEAN_ANCHOR_CAPTURE",
        "anchor_time_simulation_s": anchor_time, "nominal_deadline_simulation_s": nominal_end,
        "nominal_horizon_s": 3.0, "nominal_reserve_s": 1.2,
        "actual_ask_margin_s": nominal_end - ask_time if finite(nominal_end) and finite(ask_time) else None,
        "physical_TTC_or_safe_ASK_eligibility_proven": False,
        "policy_relation_or_RQ3_timing_operand_used": False,
    }
    eligible = any(r.get("ClarificationOpportunity") is True for r in temporal)
    critical = task["physical_task_relation_truth"] == "TASK_CRITICAL"
    equivalent = task["physical_task_relation_truth"] == "TASK_EQUIVALENT"
    result["unnecessary_question"] = bool(asks) if equivalent and (complete or asks) else None
    # The policy's opportunity predicate contains the ablated relation. Using it
    # as an evaluator eligibility filter would hide TRAJ false-equivalent misses.
    result["critical_episode_no_question"] = not bool(asks) if critical and complete else None
    result["policy_reported_eligible_opportunity"] = eligible if temporal else None
    result["policy_reported_timely_question"] = bool(asks and finite(margin) and margin >= 0) if critical and eligible and complete else None
    result["timely_question"] = None
    result["missed_question"] = None
    result["policy_reported_late_question"] = bool(margin < 0) if asks and finite(margin) else (False if complete and not asks else None)
    result["policy_reported_missed_window"] = any(r.get("decision") == "MISSED_ACTIONABLE_WINDOW" for r in temporal) if temporal else None
    result["late_question"] = None
    result["missed_window"] = None
    for name in ("preask_active_wait_sim_s", "postask_answer_wait_sim_s", "first_evidence_sufficient_sim_s", "ask_remaining_margin_sim_s", "answer_remaining_margin_sim_s", "timely_question", "missed_question"):
        if result[name] is None:
            missing[name] = "ACTIVE_WAIT_REASON_NOT_INSTRUMENTED" if name == "preask_active_wait_sim_s" else "EVENT_OR_CERTIFIED_ELIGIBLE_OPPORTUNITY_NOT_OBSERVED"
    for name in ("timely_question", "missed_question", "late_question", "missed_window"):
        missing[name] = "INDEPENDENT_OPPORTUNITY_NOT_AVAILABLE_POLICY_PREDICATE_CONTAINS_ABLATED_RELATION"

    native_forwards = terminal.get("model_forward_count", max([r.get("native_model_forward_count", 0) for r in control], default=0) if exposed else None)
    method_forwards = max([r.get("method_forward_count_total", 0) for r in candidates] + [r.get("method_candidate_forward_count", 0) for r in control], default=0) if exposed else None
    result.update(runtime_wall_s=process.get("total_wall_s"),
                  runtime_sim_s=official.get("meta", {}).get("duration_game") if exposed else None,
                  model_forward_count=native_forwards + method_forwards if native_forwards is not None and method_forwards is not None else None,
                  native_model_forward_count=native_forwards, candidate_forward_count=method_forwards,
                  diagnostic_forward_count=sum(r.get("diagnostic_forward") is True for r in candidates) if candidates else None,
                  peak_gpu_memory_mib=max([r["gpu_memory_mib"] for r in resource if finite(r.get("gpu_memory_mib"))], default=None),
                  peak_rss_mib=max([r["rss_mib"] for r in resource if finite(r.get("rss_mib"))], default=None),
                  artifact_bytes=sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) if started else 0)
    for key in ("peak_gpu_memory_mib", "peak_rss_mib"):
        if result[key] is None:
            missing[key] = "RESOURCE_SAMPLER_FILE_OR_FIELD_ABSENT"
    result["config_sha256"] = runtime_identity.get("config_sha256") or planned.get("config_sha256")
    result["config_identity_basis"] = "RUNTIME_RECEIPT" if runtime_identity.get("config_sha256") else "PLANNED_CONFIG_ONLY"
    result["code_sha256"] = planned.get("code_sha256")
    result["checkpoint_id"] = runtime_config.get("checkpoint_sha256", config.get("checkpoint_sha256"))
    result["adapter_sha256"] = planned.get("adapter_sha256")
    result["scene_sha256"] = planned.get("route_sha256", planned.get("scene_sha256"))
    result["scorer_sha256"] = sha(Path(__file__).read_bytes())
    result["physical_scorer_sha256"] = sha((Path(__file__).parent / "task_outcome.py").read_bytes())
    result["frozen_physical_reducer_sha256"] = sha((Path(__file__).resolve().parents[1] / "driveclarify_rq3_paired_v2/task_evaluator.py").read_bytes())
    result["terminal_record_source"] = str(out / "official_checkpoint.json")
    result["task_judgment_source"] = str(planned.get("task_binding_path"))
    result["safety_judgment_source"] = str(out / "official_checkpoint.json")
    starts = int(process.get("server_start_attempts", 1 if started else 0))
    attempts = []
    for n in range(starts):
        last = n == starts - 1
        log = out / "process_job" / ("carla_server_start_%02d.log" % (n + 1))
        reader.raw(log)
        attempts.append({"attempt": n + 1, "agent_exposed": exposed if last else False,
                         "infrastructure_failure_evidence": str(log) if not last else None,
                         "source": str(out / "process_job/PROCESS_RECEIPT.json")})
    result["attempts"] = attempts
    result["attempt_count"] = len(attempts)
    # Optional mechanism/resource corruption reduces the relevant metric's
    # coverage. It must not discard an intact independent driving endpoint.
    if any("ABL_DECISION_TIMELINE.jsonl" in p for p in reader.errors):
        for name in ("ask_requested_count", "relation_equivalent_count", "relation_divergent_count", "relation_unknown_count"):
            result[name] = None
            missing[name] = "DECISION_LOG_CORRUPT_PARTIAL_ROWS_RETAINED_ONLY_AS_DIAGNOSTIC"
    if any("RESOURCE_USAGE.jsonl" in p for p in reader.errors):
        for name in ("peak_gpu_memory_mib", "peak_rss_mib"):
            result[name] = None
            missing[name] = "RESOURCE_LOG_CORRUPT"
    return result, {"run_id": planned["run_id"], "files": reader.index,
                    "parse_errors": reader.errors, "snapshot_complete_terminal": complete}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--protocol-id")
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    document = json.loads(raw)
    protocol = args.protocol_id or (document.get("protocol_id") if isinstance(document, dict) else None) or "MANIFEST-" + sha(raw)[:16]
    planned = adapt_manifest(document, protocol)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    source_dir = args.output_dir / "evaluator_source"
    source_dir.mkdir()
    for path in (Path(__file__), Path(__file__).parent / "task_outcome.py",
                 Path(__file__).resolve().parents[1] / "driveclarify_rq3_paired_v2/task_evaluator.py"):
        (source_dir / path.name).write_bytes(path.read_bytes())
    results, indices = [], []
    for row in planned:
        result, index = normalize_run(row)
        index_path = args.output_dir / (row["run_id"] + "_RAW_LOG_INDEX.json")
        index_path.write_text(json.dumps(index, indent=2, allow_nan=False) + "\n")
        result["log_index"] = str(index_path)
        results.append(result)
        indices.append(index)
    (args.output_dir / "STATISTICS_MANIFEST.json").write_text(json.dumps({"runs": planned}, indent=2, allow_nan=False) + "\n")
    (args.output_dir / "NORMALIZED_RESULTS.jsonl").write_text("".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in results))
    receipt = {"normalization_version": VERSION, "source_manifest": str(args.manifest),
               "source_manifest_sha256": sha(raw), "planned_runs": len(planned),
               "result_rows": len(results), "statistics_executed": False,
               "source_mutations": 0, "states": {r["run_id"]: r["status"] for r in results}}
    (args.output_dir / "NORMALIZATION_RECEIPT.json").write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
