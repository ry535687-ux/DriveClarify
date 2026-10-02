"""运行结束后只读整理原生证据，十层结果分别判定，不反推方法真值。"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

from owned_runtime import proc_row
from resource_preflight import collect as collect_resources

REPORT = Path(__file__).resolve().parents[1]
ROOT = REPORT.parents[1]
DEV = ROOT / "experiments/driveclarify_native_clear_backend_dev_20260913"


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def result(status, reason, evidence=None):
    return {"status": status, "reason": reason, "evidence": evidence}


def load_optional(path):
    if not path.exists():
        return None, "FILE_NOT_PRESENT"
    try:
        return json.loads(path.read_text()), None
    except (ValueError, OSError) as exc:
        return None, str(exc)


def summarize(letter):
    case = json.loads((DEV / f"configs/case_{letter}.json").read_text())
    contract = json.loads((DEV / "configs" / case["evaluation_file"]).read_text())
    output = Path(case["output_dir"])
    receipt = json.loads((REPORT / f"{letter}_RUN_RECEIPT.json").read_text())
    resources = collect_resources("AFTER_" + letter)
    owned = receipt["owned_process_birth_identities"]
    alive = []
    for row in owned:
        current = proc_row(row["pid"])
        if current and current["starttime_ticks"] == row["starttime_ticks"]:
            alive.append(current)
    compute = resources["commands"]["compute"]
    gpu_pids = {int(line.split(",")[0].strip()) for line in compute["stdout_text"].splitlines() if line.split(",")[0].strip().isdigit()}
    owned_gpu = sorted(gpu_pids & {r["pid"] for r in owned})
    closed = (receipt["cleanup_status"] == "PASS" and not alive and not owned_gpu and
              not resources["requested_port_occupancy"] and not resources["conflicting_native_processes"] and
              all(resources["commands"][k]["exit_code"] == 0 for k in ("processes", "ports", "compute")))
    cleanup = {"status": "PASS" if closed else "FAIL", "residual_owned_pids": alive, "owned_gpu_compute_pids": owned_gpu,
               "requested_ports_2020_2021_2022_8020_free": not resources["requested_port_occupancy"],
               "non_owned_compute_preserved": compute["stdout_text"], "native_conflicts": resources["conflicting_native_processes"],
               "owned_runtime_cleanup": receipt["cleanup_status"], "scope": "仅清理本轮出生身份绑定的子进程；其他进程保留"}
    save(REPORT / f"{letter}_CLEANUP_VERIFICATION.json", cleanup)
    rows, parse_errors = [], []
    world = output / "world_state.jsonl"
    if world.exists():
        for number, line in enumerate(world.read_text().splitlines(), 1):
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                parse_errors.append({"line": number, "reason": str(exc)})
    route, route_error = load_optional(output / "ROUTE_BINDING_RUNTIME.json")
    stats, stats_error = load_optional(output / "leaderboard_results.json")
    spec = importlib.util.spec_from_file_location("stage3b_extract", DEV / "extract_observer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    summary = module.summarize(case, contract, rows, route, stats)
    summary.update(raw_parse_errors=parse_errors, route_receipt_read_error=route_error, native_statistics_read_error=stats_error,
                   raw_world_state_sha256=hashlib.sha256(world.read_bytes()).hexdigest() if world.exists() else None,
                   actual_vehicle_application_independently_verified=None)
    save(REPORT / f"{letter}_ROUTE_PLAN_CONTROL_SUMMARY.json", summary)
    event = summary["task_event_evaluation"]
    event.update(case_id=case["case_id"], contract_id=contract["contract_id"],
                 contract_sha256=hashlib.sha256((DEV / "configs" / case["evaluation_file"]).read_bytes()).hexdigest(),
                 method_truth_used=False, RC_used_for_gate_events=False, parse_errors=parse_errors)
    save(REPORT / f"{letter}_TASK_EVENT_RESULT.json", event)
    native_records = (stats or {}).get("_checkpoint", {}).get("records", [])
    stdout = (output / "stdout.log").read_text(errors="replace")
    stderr = (output / "stderr.log").read_text(errors="replace")
    infractions = {k: v for record in native_records for k, v in record.get("infractions", {}).items()}
    statuses = [record.get("status") for record in native_records]
    route_completion = [record.get("scores", {}).get("score_route") for record in native_records]
    indicators = [line for line in (stdout + "\n" + stderr).splitlines()
                  if re.search(r"out of memory|CUDA.*error|Traceback|Exception|Error|FAILURE|TIMEOUT|blocked|off.route|collision", line, re.I)]
    oom_lines = [line for line in indicators if "out of memory" in line.lower()]
    native_summary = {"case_id": case["case_id"], "status": statuses or None, "route_completion": route_completion or None,
                      "records": native_records, "infractions": infractions or None,
                      "process_exit_code": receipt["exit_code"], "watchdog": receipt["watchdog"], "process_stop_reason": receipt["stop_reason"],
                      "native_end_reason": statuses if statuses else None, "missing_native_end_reason": None if statuses else "NO_FINAL_NATIVE_RECORD",
                      "diagnostic_log_lines": indicators, "oom_marker_observed": bool(oom_lines), "oom_lines": oom_lines,
                      "actual_process_start_or_init_success_not_inferred_from_exit_code": True}
    save(REPORT / f"{letter}_NATIVE_RESULT_SUMMARY.json", native_summary)
    model_count = summary["model_observation"]["records_with_call_times_output_and_observation_binding"]
    control_count = summary["control_observation"]["records_with_bound_returned_control"]
    installed = route.get("agent_route_planner_installed_route", []) if isinstance(route, dict) else []
    dense = route.get("agent_dense_global_plan_world", []) if isinstance(route, dict) else []
    cadence = event["observed_intervals_cadence_consistent"]
    completed = bool(statuses) and all(s == "Completed" for s in statuses)
    failures = bool(statuses) and any(isinstance(s, str) and s.startswith("Failed") for s in statuses)
    known_infraction = any(bool(v) for v in infractions.values())
    layers = {
        "NATIVE_INITIALIZATION": result("PASS" if rows and installed else ("FAIL" if "Failed - Agent" in stdout or "Agent couldn't be set up" in stdout else "UNKNOWN"), "已有初始化后世界状态与原生安装路线回执" if rows and installed else "缺完整初始化后回执"),
        "ROUTE_ACCEPTANCE": result("PASS" if installed and dense else "UNKNOWN", "初次原生安装路线与dense实际回执" if installed and dense else "无实际完整接受回执", {"installed_points": len(installed), "dense_points": len(dense)}),
        "MODEL_FORWARD_OBSERVED": result("PASS" if model_count else "UNKNOWN", "原生同观测调用时间及输出回执" if model_count else "无完整调用证据", model_count),
        "PLAN_OUTPUT_OBSERVED": result("PASS" if model_count else "UNKNOWN", "实际pred_route/speed值" if model_count else "无有效计划输出", model_count),
        "BASELINE_CONTROL_RETURNED": result("PASS" if control_count else "UNKNOWN", "同观测返回控制值和control_ready" if control_count else "无绑定返回控制", control_count),
        "ACTUAL_APPLY_CONTROL_INDEPENDENTLY_VERIFIED": result("UNKNOWN", "现有接口无逐次apply_control持久回执"),
        "RECORDER_CADENCE_VALID": result("FAIL" if cadence is False else ("PASS" if cadence is True and not parse_errors else "UNKNOWN"), "只评价已记录区间的frame/time/snapshot_delta一致性；不推出全程覆盖", {"intervals": event["checked_intervals"], "gaps": len(event["gaps"]), "parse_errors": len(parse_errors)}),
        "LOCAL_TASK_GATES": result("PASS" if event["ordered_closed_gate_arrival_observed"] else "UNKNOWN", "观察到连续按序三门闭边界到达" if event["ordered_closed_gate_arrival_observed"] else "没有正事件，全程观测覆盖尚未认证，不由RC或exit补判"),
        "NATIVE_ROUTE_COMPLETION": result("PASS" if completed and all(v == 100 for v in route_completion) else ("FAIL" if failures or any(isinstance(v, (int, float)) and v < 100 for v in route_completion) else "UNKNOWN"), "原生最终route记录单独判读", {"statuses": statuses, "RC": route_completion}),
        "NATIVE_SAFETY_ENDPOINT": result("FAIL" if known_infraction else "UNKNOWN", "已有明确原生违规事件可判FAIL；空infractions不单独认证安全，缺独立完整安全端点时保持UNKNOWN", infractions or None),
    }
    save(REPORT / f"{letter}_TEN_LAYER_RESULTS.json", {"case_id": case["case_id"], "results": layers, "method_effect_claim": False})
    print(json.dumps({"case": letter, "cleanup": cleanup["status"], "rows": len(rows), "layers": {k: v["status"] for k, v in layers.items()}, "native_statuses": statuses, "RC": route_completion, "process_stop": receipt["stop_reason"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("A", "B"), required=True)
    summarize(parser.parse_args().case)
