"""评价侧有向门事件提取器；不导入策略，不接收方法状态或候选选择。"""
from __future__ import annotations

import math

GATE_BOUNDARY_RULE = "NEGATIVE_TO_NONNEGATIVE_CLOSED_BOUNDARY"


def _number(x):
    return type(x) in (int, float) and math.isfinite(x)


def _point(p):
    return isinstance(p, list) and len(p) == 2 and all(_number(v) for v in p)


def validate_contract(contract):
    if contract.get("gate_boundary_rule", GATE_BOUNDARY_RULE) != GATE_BOUNDARY_RULE:
        raise ValueError("UNSUPPORTED_GATE_BOUNDARY_RULE")
    recorder_cadence(contract)
    branches = contract.get("branches")
    if not isinstance(branches, dict) or not branches:
        raise ValueError("BRANCH_CONTRACT_REQUIRED")
    if type(contract.get("max_frame_gap")) is not int or contract["max_frame_gap"] < 1:
        raise ValueError("SAMPLING_CONTRACT_REQUIRED")
    for branch, row in branches.items():
        if not isinstance(branch, str) or row.get("irreversible_for_task") is not True:
            raise ValueError("TASK_IRREVERSIBILITY_CONTRACT_REQUIRED")
        for kind in ("entry", "exit", "end"):
            gate = row.get(kind, {})
            if (not _point(gate.get("center")) or not _point(gate.get("normal"))
                    or not _number(gate.get("half_width_m")) or gate["half_width_m"] <= 0
                    or math.hypot(*gate["normal"]) == 0):
                raise ValueError("INVALID_ORIENTED_GATE")


def recorder_cadence(contract):
    """只接受记录器显式绑定；旧合同缺字段表示未绑定，不补真实数值。"""
    cadence = contract.get("recorder_cadence")
    if cadence is None:
        return {"binding": "UNBOUND", "recorder_contract_id": None, "frame_period_s": None, "tolerance_s": None}
    if not isinstance(cadence, dict):
        raise ValueError("INVALID_RECORDER_CADENCE_CONTRACT")
    if cadence.get("binding") == "UNBOUND":
        if any(cadence.get(k) is not None for k in ("frame_period_s", "tolerance_s")):
            raise ValueError("UNBOUND_CADENCE_CANNOT_CLAIM_NUMERIC_PERIOD")
        return dict(cadence)
    if (cadence.get("binding") != "FIXED_STEP_RECORDER_CONTRACT"
            or not isinstance(cadence.get("recorder_contract_id"), str) or not cadence["recorder_contract_id"].strip()
            or not isinstance(cadence.get("source"), str) or not cadence["source"].strip()
            or not _number(cadence.get("frame_period_s")) or cadence["frame_period_s"] <= 0
            or not _number(cadence.get("tolerance_s")) or cadence["tolerance_s"] < 0):
        raise ValueError("INVALID_RECORDER_CADENCE_CONTRACT")
    return dict(cadence)


def crossing(a, b, gate):
    """有限门宽内的负侧→闭边界到达（da<0<=db），包括仅接触门线后退。

    历史名称保留；此事件不要求后续穿入正侧，也不证明车辆物理不可恢复。
    """
    nx, ny = gate["normal"]
    norm = math.hypot(nx, ny)
    nx, ny = nx / norm, ny / norm
    cx, cy = gate["center"]
    da = (a["x"] - cx) * nx + (a["y"] - cy) * ny
    db = (b["x"] - cx) * nx + (b["y"] - cy) * ny
    if not da < 0 <= db:
        return None
    fraction = -da / (db - da)
    x = a["x"] + fraction * (b["x"] - a["x"])
    y = a["y"] + fraction * (b["y"] - a["y"])
    transverse = abs(-(x - cx) * ny + (y - cy) * nx)
    return fraction if transverse <= gate["half_width_m"] else None


def evaluate_trace(contract, trace, truth, coverage, safety):
    """truth 仅为评价侧允许的 branch 集；输入中没有策略标签或原生 RC。

    coverage 起止标记由独立记录器提供。缺帧处断开事件顺序，已确认的错误进入和
    已完成事件保留。安全监测完整性独立于轨迹完整性，任一已知违规使安全为 False。
    """
    validate_contract(contract)
    cadence = recorder_cadence(contract)
    cadence_bound = cadence["binding"] == "FIXED_STEP_RECORDER_CONTRACT"
    checked_intervals = cadence_mismatches = 0
    if set(truth) != {"allowed_branches"} or not truth["allowed_branches"]:
        raise ValueError("EVALUATION_TRUTH_REQUIRED")
    allowed = set(truth["allowed_branches"])
    if not allowed <= set(contract["branches"]):
        raise ValueError("TRUTH_OUTSIDE_CONTRACT")
    if set(coverage) != {"start_observed", "end_observed"} or any(type(v) is not bool for v in coverage.values()):
        raise ValueError("INVALID_COVERAGE")
    required_safety = set(contract.get("safety_endpoints", []))
    if not required_safety or set(safety) != required_safety:
        raise ValueError("SAFETY_ENDPOINT_CONTRACT_MISMATCH")
    for value in safety.values():
        if (set(value) != {"event_observed", "coverage_complete"}
                or not (value["event_observed"] is None or type(value["event_observed"]) is bool)
                or type(value["coverage_complete"]) is not bool):
            raise ValueError("INVALID_SAFETY_EVIDENCE")
    events, gaps = [], []
    stages = {b: 0 for b in contract["branches"]}
    completed = set()
    wrong = False
    previous = None
    valid_count = 0
    keys = {"frame", "sim_time_s", "x", "y"}
    for index, row in enumerate(trace):
        if isinstance(row, dict) and set(row) - keys:
            raise ValueError("TRACE_SCHEMA_FORBIDS_METHOD_FIELDS")
        valid = (isinstance(row, dict) and set(row) == keys and type(row["frame"]) is int
                 and row["frame"] >= 0 and all(_number(row[k]) for k in ("sim_time_s", "x", "y")))
        if not valid:
            gaps.append({"index": index, "reason": "MISSING_OR_INVALID_SAMPLE"})
            previous = None
            stages = {b: 0 for b in stages}
            continue
        valid_count += 1
        if previous is not None:
            delta = row["frame"] - previous["frame"]
            time_delta = row["sim_time_s"] - previous["sim_time_s"]
            segment_error = None
            if not 0 < delta <= contract["max_frame_gap"] or time_delta <= 0:
                segment_error = "FRAME_OR_CLOCK_GAP"
            elif cadence_bound:
                checked_intervals += 1
                if abs(time_delta - delta*cadence["frame_period_s"]) > cadence["tolerance_s"]:
                    cadence_mismatches += 1
                    segment_error = "RECORDER_CADENCE_MISMATCH"
            if segment_error:
                gaps.append({"index": index, "reason": segment_error})
                stages = {b: 0 for b in stages}
            else:
                segment_events = []
                for branch, conditions in contract["branches"].items():
                    for order, kind in enumerate(("entry", "exit", "end")):
                        fraction = crossing(previous, row, conditions[kind])
                        if fraction is not None:
                            segment_events.append((fraction, branch, order, kind))
                for fraction, branch, order, kind in sorted(segment_events):
                    event = {"branch": branch, "kind": kind, "frame_before": previous["frame"],
                             "frame_after": row["frame"], "fraction": fraction,
                             "boundary_rule": GATE_BOUNDARY_RULE,
                             "sim_time_s": previous["sim_time_s"] + fraction * (row["sim_time_s"] - previous["sim_time_s"])}
                    events.append(event)
                    if kind == "entry":
                        stages[branch] = 1
                        if branch not in allowed:
                            wrong = True  # 对任务不可逆；此后任何 RC/回绕都不能清除。
                    elif order == stages[branch]:
                        stages[branch] += 1
                        if kind == "end":
                            completed.add(branch)
        previous = row
    full = coverage["start_observed"] and coverage["end_observed"] and not gaps and valid_count >= 2
    wrong_endpoint = True if wrong else (False if full else None)
    correct_pass = bool(completed & allowed)
    # 正确通过已确证可保留；若其他时段缺失，不能据此宣称没有进入过错误分支。
    task = False if wrong else (correct_pass if full else None)
    safety_events = {k: v["event_observed"] if v["event_observed"] is True
                     else (False if v["coverage_complete"] and v["event_observed"] is False else None)
                     for k, v in safety.items()}
    safety_ok = (False if any(v is True for v in safety_events.values())
                 else (True if all(v is False for v in safety_events.values()) else None))
    safe_task = False if task is False or safety_ok is False else (True if task is True and safety_ok is True else None)
    status = "WRONG_BRANCH" if wrong else ("CORRECT_COMPLETE" if task is True
              else ("NOT_COMPLETED" if task is False else "INSUFFICIENT_RECORD"))
    return {"status": status, "correct_pass_observed": correct_pass,
            "correct_ordered_gate_arrival_observed": correct_pass,
            "gate_boundary_rule": GATE_BOUNDARY_RULE,
            "endpoint_semantics": "entry/exit/end 均为有向闭边界到达；旧 correct_pass_observed 表示按序到达这些门，非严格穿入正侧证明",
            "wrong_irreversible_branch": wrong_endpoint, "task_complete": task,
            "safety_events": safety_events, "safety_ok": safety_ok, "safe_task_complete": safe_task,
            "events": events, "gaps": gaps, "trace_coverage_complete": full,
            "cadence_check": {"contract": cadence, "checked_intervals": checked_intervals,
                              "mismatch_count": cadence_mismatches,
                              "observed_intervals_consistent": False if cadence_mismatches else
                              (True if cadence_bound and checked_intervals and not gaps else None)},
            "interpolation_model": "ADJACENT_OBSERVED_SAMPLES_LINEAR_CHORD"}
