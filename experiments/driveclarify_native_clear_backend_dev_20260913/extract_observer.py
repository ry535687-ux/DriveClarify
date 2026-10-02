"""整理已有 CP1 输出；任务事件只读取独立世界坐标和时钟，不读取方法真值。"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

# 只复用已验收的纯 CPU 门数学，不导入策略、原生 agent 或任何驾驶入口。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.driveclarify_three_policy_dev.evaluator import crossing, recorder_cadence


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def task_events(contract, rows):
    """预声明三个闭边界门；缺帧/时钟异常打断当前顺序，已观察的正事件保留。"""
    cadence = recorder_cadence(contract)
    if contract["gate_boundary_rule"] != "NEGATIVE_TO_NONNEGATIVE_CLOSED_BOUNDARY":
        raise ValueError("UNSUPPORTED_GATE_BOUNDARY")
    events, gaps = [], []
    previous, stage, complete, checked = None, 0, False, 0
    for index, raw in enumerate(rows):
        ego = raw.get("ego")
        xyz = ego.get("location_xyz") if isinstance(ego, dict) else None
        frame, sim_time = raw.get("snapshot_frame"), raw.get("snapshot_elapsed_seconds")
        valid = (type(frame) is int and frame >= 0 and finite(sim_time)
                 and isinstance(xyz, list) and len(xyz) == 3 and all(finite(v) for v in xyz))
        if not valid:
            gaps.append({"index": index, "reason": "INVALID_WORLD_SAMPLE"})
            previous, stage = None, 0
            continue
        row = {"frame": frame, "sim_time_s": sim_time, "x": xyz[0], "y": xyz[1]}
        delta = raw.get("snapshot_delta_seconds")
        if not finite(delta) or abs(delta - cadence["frame_period_s"]) > cadence["tolerance_s"]:
            gaps.append({"index": index, "reason": "SNAPSHOT_STEP_MISMATCH_OR_MISSING"})
            previous, stage = None, 0
            continue
        if previous is not None:
            df = frame - previous["frame"]
            dt = sim_time - previous["sim_time_s"]
            if not 0 < df <= contract["max_frame_gap"] or dt <= 0:
                gaps.append({"index": index, "reason": "FRAME_OR_CLOCK_GAP"})
                stage = 0
            elif abs(dt - df * cadence["frame_period_s"]) > cadence["tolerance_s"]:
                gaps.append({"index": index, "reason": "CADENCE_MISMATCH"})
                stage = 0
            else:
                checked += 1
                hits = [(fraction, order, gate["name"]) for order, gate in enumerate(contract["gates"])
                        if (fraction := crossing(previous, row, gate)) is not None]
                for fraction, order, name in sorted(hits):
                    events.append({"name": name, "frame_before": previous["frame"], "frame_after": frame,
                                   "fraction": fraction, "sim_time_s": previous["sim_time_s"] + fraction * dt})
                    if order == 0:
                        stage = 1
                    elif order == stage:
                        stage += 1
                    if stage == 3:
                        complete = True
        previous = row
    return {"ordered_closed_gate_arrival_observed": complete, "events": events, "gaps": gaps,
            "checked_intervals": checked, "observed_intervals_cadence_consistent": False if gaps else (True if checked else None),
            "whole_mission_coverage_complete": None, "whole_mission_success": None,
            "wrong_branch_classification": "NOT_DECLARED", "safety_success": None,
            "meaning": "False 仅表示本文件未观察到连续按序三门正事件，不是任务失败；不以 RC 或方法字段补齐。"}


def summarize(case, contract, rows, route_receipt=None, native_statistics=None):
    if any(not isinstance(row, dict) or row.get("run_id") != case["case_id"] for row in rows):
        raise ValueError("WORLD_STATE_RUN_ID_MISMATCH_OR_INVALID_ROW")
    if contract["public_task_id"] != case["task_id"] or contract["route_sha256"] != case["route_source"]["sha256"]:
        raise ValueError("PUBLIC_CONTRACT_IDENTITY_MISMATCH")
    if route_receipt is not None and route_receipt.get("run_id") != case["case_id"]:
        raise ValueError("ROUTE_RECEIPT_RUN_ID_MISMATCH")
    calls, controls = [], []
    seen_observations = set()
    for row in rows:
        oid = row.get("observation_id")
        t0, t1, t2 = (row.get(k) for k in ("model_start_monotonic_s", "model_end_monotonic_s", "control_ready_monotonic_s"))
        route_values, speed_values = row.get("pred_route_values"), row.get("pred_speed_wps_values")
        expected_oid = f"{case['case_id']}:{row.get('carla_snapshot_frame')}:simlingo_agent_v0"
        if (oid == expected_oid and oid not in seen_observations
                and row.get("carla_snapshot_frame") == row.get("snapshot_frame")
                and finite(t0) and finite(t1) and t1 >= t0
                and isinstance(route_values, list) and route_values and isinstance(speed_values, list) and speed_values):
            seen_observations.add(oid)
            evidence = {"observation_id": oid, "snapshot_frame": row["snapshot_frame"],
                        "model_start_monotonic_s": t0, "model_end_monotonic_s": t1,
                        "pred_route_values": route_values, "pred_speed_wps_values": speed_values}
            calls.append(evidence)
            control = row.get("baseline_control")
            if (isinstance(control, dict) and all(finite(control.get(k)) for k in ("steer", "throttle", "brake"))
                    and finite(t2) and t2 >= t1):
                controls.append({**evidence, "control_ready_monotonic_s": t2, "returned_control": control})
    records = (native_statistics or {}).get("_checkpoint", {}).get("records", [])
    return {"protocol_id": case["protocol_id"], "case_id": case["case_id"], "world_state_rows": len(rows),
            "task_event_evaluation": task_events(contract, rows),
            "model_observation": {"records_with_call_times_output_and_observation_binding": len(calls),
                                  "first_record": calls[0] if calls else None,
                                  "distinct_numeric_plan_required": False},
            "control_observation": {"records_with_bound_returned_control": len(controls),
                                    "first_record": controls[0] if controls else None,
                                    "carla_apply_control_receipt": None},
            "initial_route_receipt": route_receipt,
            "native_host_records_uninterpreted": [{k: rec.get(k) for k in ("route_id", "status", "scores", "infractions", "meta")} for rec in records],
            "native_host_records_bound_to_this_case": None,
            "process_exit_reason": None,
            "limitations": ["运行身份还需与启动回执/源摘要核对", "宿主统计不能替代独立任务门", "无 apply_control 持久回执则不认证实际控制采用",
                            "正常裁剪/重采样允许同一初始化路线派生身份；不要求每 tick 数组摘要不变", "日志缺失不补零、不补成功"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("A", "B"), required=True)
    parser.add_argument("--world-state", type=Path, required=True)
    parser.add_argument("--route-receipt", type=Path)
    parser.add_argument("--native-statistics", type=Path)
    args = parser.parse_args(argv)
    try:
        configs = Path(__file__).resolve().parent / "configs"
        case = json.loads((configs / f"case_{args.case}.json").read_text())
        contract = json.loads((configs / case["evaluation_file"]).read_text())
        rows = [json.loads(line) for line in args.world_state.read_text().splitlines() if line.strip()]
        receipt = json.loads(args.route_receipt.read_text()) if args.route_receipt else None
        stats = json.loads(args.native_statistics.read_text()) if args.native_statistics else None
        result = summarize(case, contract, rows, receipt, stats)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"status": "CONTROLLED_INPUT_ERROR", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
