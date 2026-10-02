#!/usr/bin/env python3
"""只读日志研究面板；无 CARLA、模型、PID、planner 或 runtime 写入。

先 --once 生成可回放 JSON/HTML；Tk 仅在 GUI 模式延迟 import。
所有来源的时钟/帧分开显示，历史 ASK 请求不冒充当前实际发问。
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import math
from pathlib import Path
import re
import time

LABELS = ("RESEARCH DEBUG VIEW", "NO FORMAL SAFETY GUARANTEE", "SIMULATION ONLY")
COORDINATE_FRAME = "CARLA_EGO_X_FORWARD_Y_RIGHT_METRES"
MAX_LOG_BYTES = 2 * 1024 * 1024


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def load_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def frame_of(row):
    for key in ("frame", "source_frame_id", "supervision_frame"):
        if type(row.get(key)) is int:
            return row[key]
    match = re.search(r"frame-(\d+)$", str(row.get("observation_id", "")))
    return int(match.group(1)) if match else None


def load_jsonl(path, until_frame=None):
    """容忍末尾尚未完成的一行；不把损坏或缺失行补成0。"""
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size > MAX_LOG_BYTES:
                stream.seek(size - MAX_LOG_BYTES)
                stream.readline()
            raw = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return [], False
    rows = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        frame = frame_of(row)
        if until_frame is not None and (frame is None or frame > until_frame):
            continue
        rows.append(row)
    return rows, size > MAX_LOG_BYTES


def candidate_view(row, now_s=None):
    trajectory = row.get("timed_trajectory") or {}
    xy = trajectory.get("xy_m")
    times = trajectory.get("relative_times_s")
    valid = (row.get("valid") is True and trajectory.get("valid") is True
             and trajectory.get("coordinate_frame") == COORDINATE_FRAME
             and isinstance(xy, list) and isinstance(times, list) and len(xy) == len(times) == 9
             and all(isinstance(point, list) and len(point) == 2 and all(finite(v) for v in point) for point in xy)
             and all(finite(v) and abs(v - i * .25) < 1e-7 for i, v in enumerate(times)))
    source_s = trajectory.get("source_time_s", row.get("source_sensor_time_s"))
    age = now_s - source_s if finite(now_s) and finite(source_s) and now_s >= source_s else None
    return {
        "candidate_id": row.get("candidate_id"), "slot": row.get("slot"),
        "valid": True if valid else None,
        "reason": "RECORDED_VALID_TIMED_PLAN" if valid else row.get("error", "UNKNOWN_OR_UNVERIFIED_COORDINATE_TIME"),
        "source_frame": trajectory.get("source_frame", row.get("source_sensor_frame")),
        "source_time_s": source_s, "supervision_frame": row.get("supervision_frame"),
        "cache_age_at_latest_control_simulation_s": age,
        "cache_status_at_latest_control": ("UNKNOWN" if age is None else
            "WITHIN_0_1S_RECORDED_CONTEXT" if age <= .100000001 and valid else
            "HISTORICAL_STALE_NOT_CURRENT_PLAN"),
        "context_digest": trajectory.get("nonlanguage_context_sha256"),
        "coordinate_frame": trajectory.get("coordinate_frame"),
        "xy_m": xy if valid else None, "relative_times_s": times if valid else None,
        "method_forward": row.get("method_forward"), "candidate_wall_s": row.get("wall_s"),
    }


def owner_directory(report, run_id):
    if not isinstance(run_id, str) or Path(run_id).name != run_id or run_id in ("", ".", ".."):
        return None
    for stage in ("development", "formal"):
        owner = report / stage / "native" / run_id / "owner_evidence"
        if owner.is_dir():
            return owner
    return None


def build_snapshot(report, run_id=None, until_frame=None):
    report = Path(report)
    state = load_json(report / "STATE.json")
    run_id = run_id or state.get("current_run")
    owner = owner_directory(report, run_id)
    result = {"schema": "driveclarify.passive-research-panel.v1", "labels": list(LABELS),
              "snapshot_wall_time": datetime.datetime.now().astimezone().isoformat(),
              "run_id": run_id, "batch_status": state.get("status"), "owner_path": str(owner) if owner else None,
              "until_frame": until_frame, "read_only": True, "new_model_pid_planner_control_calls": 0,
              "state": None, "decision": {}, "control": {}, "candidates": [], "questions": [],
              "answer": {}, "replan": {}, "timing": {}, "selected_route": {}, "holding_type": None,
              "holding_path": None, "selected_route_geometry": None, "query_budget": None,
              "consequences": {name: None for name in ("task", "safety", "rule", "irreversibility", "comfort", "D_G", "D_S", "D_R", "D_I", "Critical")},
              "timeline": [], "truncated_sources": [], "clock_alignment": "UNKNOWN", "source_files": []}
    if owner is None:
        return result
    sources = {}
    for key, filename in (("decision", "ABL_DECISION_TIMELINE.jsonl"),
                          ("control", "ABL_ACTUAL_CONTROL_TIMELINE.jsonl"),
                          ("candidates", "ABL_CANDIDATE_TIMELINE.jsonl"),
                          ("temporal", "RQ3_TEMPORAL_DECISION_TIMELINE.jsonl")):
        rows, truncated = load_jsonl(owner / filename, until_frame)
        sources[key] = rows
        result["source_files"].append(filename)
        if truncated:
            result["truncated_sources"].append(filename)
    decision = sources["decision"][-1] if sources["decision"] else {}
    control = sources["control"][-1] if sources["control"] else {}
    result["decision"] = decision
    result["control"] = control
    status = load_json(owner / "V11_AGENT_STATUS.json") if until_frame is None else {}
    result["state"] = status.get("status", control.get("supervisor_status"))
    df, cf = frame_of(decision), frame_of(control)
    if df is not None and cf is not None:
        result["clock_alignment"] = "SAME_FRAME" if df == cf else "LAST_DECISION_AND_LATEST_CONTROL_HAVE_DIFFERENT_FRAMES"
    latest_candidates = {}
    for row in sources["candidates"]:
        latest_candidates[row.get("candidate_id")] = row
    result["candidates"] = [candidate_view(row, control.get("simulation_time_s"))
                            for row in latest_candidates.values()]
    for path in sorted((owner / "oracle_exchange").glob("ask-*.json")):
        ask = load_json(path)
        frame = frame_of(ask)
        if ask.get("durable") is not True or ask.get("action") != "ASK":
            continue
        if until_frame is not None and (frame is None or frame > until_frame):
            continue
        result["questions"].append({"query_id": ask.get("query_id"), "question": ask.get("question"),
            "source_frame": frame, "written_epoch_ns": ask.get("written_epoch_ns"), "durable": True})
    lifecycle = load_json(owner / "RQ3_LIFECYCLE_TIMING_RECEIPT.json")
    answer = lifecycle.get("answer") or {}
    if result["questions"] and (until_frame is None or (type(answer.get("frame")) is int and answer["frame"] <= until_frame)):
        result["answer"] = answer
        # Route/replan receipts have no frame. Only display them for latest-view;
        # a frame-limited replay must not read future completion evidence.
        if until_frame is None:
            result["replan"] = lifecycle.get("full_replan") or {}
    temporal = sources["temporal"][-1] if sources["temporal"] else {}
    latency = load_json(owner / "RQ3_DECISION_LATENCY_RECEIPT.json") if until_frame is None else {}
    result["timing"] = {"T_branch_s": temporal.get("TTCmt_s"),
        "remaining_slack_s": temporal.get("remaining_decision_margin_s"),
        "clarification_deadline_simulation_s": temporal.get("clarification_deadline_simulation_time_s"),
        "decision_source_frame": df, "decision_simulation_s": decision.get("simulation_time_s"),
        "fast_loop_frame": cf, "fast_loop_simulation_s": control.get("simulation_time_s"),
        "consequence_latency_wall_s": latency.get("consequence_gate_latency_wall_s"),
        "decision_latency_wall_s": latency.get("total_decision_latency_wall_s"),
        "T_question_request_simulation_s": (lifecycle.get("ask") or {}).get("simulation_time_s") if result["questions"] else None,
        "response_latency_simulation_s": result["answer"].get("answer_latency_simulation_s"),
        "replan_latency_simulation_s": result["replan"].get("latency_simulation_s"),
        "replan_latency_wall_s": result["replan"].get("latency_wall_s"),
        "T_clarify": None, "safety_margin": None}
    if until_frame is None:
        route = load_json(owner / "ONLINE_ROUTE_INSTALL_RECEIPT.json")
        result["selected_route"] = {key: route.get(key) for key in
            ("committed", "installed_route_identity", "installed_route_coordinate_domain", "next_tick_consumed_route_identity")}
    for kind, rows in sources.items():
        for row in rows:
            result["timeline"].append({"kind": kind, "frame": frame_of(row),
                "simulation_time_s": row.get("simulation_time_s", row.get("source_sensor_time_s")),
                "candidate_id": row.get("candidate_id"), "valid": row.get("valid"),
                "action": row.get("requested_action_after_joint_timing", row.get("policy_decision", row.get("decision"))),
                "relation": row.get("relation"), "error": row.get("error")})
    result["timeline"].sort(key=lambda row: (row["frame"] is None, row["frame"] or 0, row["kind"]))
    result["consequences"]["task"] = decision.get("relation")
    return result


def display(value):
    if value is None or value == "" or value == [] or value == {}:
        return "UNKNOWN"
    if isinstance(value, float):
        return "%.4f" % value
    def visible(item):
        if item is None:
            return "UNKNOWN"
        if isinstance(item, dict):
            return {key: visible(v) for key, v in item.items()}
        if isinstance(item, list):
            return [visible(v) for v in item]
        return item
    return json.dumps(visible(value), ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def summary_text(snapshot):
    decision, control, timing = snapshot["decision"], snapshot["control"], snapshot["timing"]
    reasons = (decision.get("relation_details") or {}).get("reason_codes")
    if not reasons:
        reasons = (decision.get("relation_details") or {}).get("reason", decision.get("trajectory_metric_reason"))
    fields = [
        ("Run", snapshot["run_id"]), ("批次", snapshot["batch_status"]), ("状态机", snapshot["state"]),
        ("最近决策请求", decision.get("requested_action_after_joint_timing")), ("任务关系", decision.get("relation")),
        ("Reason", reasons), ("决策frame / sim-s", [timing.get("decision_source_frame"), timing.get("decision_simulation_s")] if timing else None),
        ("控制frame / sim-s", [timing.get("fast_loop_frame"), timing.get("fast_loop_simulation_s")] if timing else None),
        ("帧对齐", snapshot["clock_alignment"]), ("控制owner策略标签", control.get("policy_decision")),
        ("请求问题", decision.get("question_requested")), ("已观察durable问题数", len(snapshot["questions"]) if snapshot["questions"] else None),
        ("实际问题", snapshot["questions"][-1].get("question") if snapshot["questions"] else None),
        ("已接收回答", snapshot["answer"]), ("Query budget", snapshot["query_budget"]),
        ("重规划", snapshot["replan"]), ("Selected committed route", snapshot["selected_route"]),
        ("Selected route geometry", None), ("Holding type/path", None),
        ("D_G / D_S / D_R / D_I", None), ("Safety / Rule / Irreversibility / Comfort / Critical", None),
        ("时机(每项注明时钟)", timing),
        ("控制 steer/throttle/brake", [control.get(k) for k in ("steer", "throttle", "brake")] if control else None),
        ("结构轨迹分歧m (不代表安全)", decision.get("trajectory_metric_m")),
    ]
    return "\n".join(label + ": " + display(value) for label, value in fields)


def projection(candidates, width=330, height=230):
    points = [p for candidate in candidates for p in (candidate.get("xy_m") or [])]
    xmin = min([-2.] + [p[0] for p in points]); xmax = max([2.] + [p[0] for p in points])
    ymin = min([-2.] + [p[1] for p in points]); ymax = max([2.] + [p[1] for p in points])
    scale = min((width - 70) / (ymax - ymin), (height - 60) / (xmax - xmin))
    def project(point):
        return (35 + (point[1] - ymin) * scale, height - 30 - (point[0] - xmin) * scale)
    return project


def candidate_svg(candidate, all_candidates):
    project = projection(all_candidates)
    label = html.escape(display(candidate.get("candidate_id")))
    content = '<rect width="330" height="230" fill="#101c28"/>'
    ox, oy = project((0., 0.)); fx, fy = project((2., 0.)); rx, ry = project((0., 2.))
    content += '<path d="M %.1f %.1f L %.1f %.1f M %.1f %.1f L %.1f %.1f" fill="none" stroke="#8094a8"/>' % (ox, oy, fx, fy, ox, oy, rx, ry)
    content += '<text x="%.1f" y="%.1f" fill="#aebfcc" font-size="10">x +2m</text><text x="%.1f" y="%.1f" fill="#aebfcc" font-size="10">y +2m</text>' % (fx+3, fy, rx, ry+12)
    if candidate.get("xy_m"):
        points = " ".join("%.2f,%.2f" % project(p) for p in candidate["xy_m"])
        content += '<polyline points="%s" fill="none" stroke="#56d9dd" stroke-width="3"/>' % points
    else:
        content += '<text x="30" y="115" fill="#f1bb65">UNKNOWN</text>'
    content += '<text x="12" y="19" fill="white">%s: x forward ↑ / y right → / m</text>' % label
    return '<svg viewBox="0 0 330 230" xmlns="http://www.w3.org/2000/svg">' + content + '</svg>'


def export_once(snapshot, directory):
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    plots = "".join(candidate_svg(c, snapshot["candidates"]) for c in snapshot["candidates"])
    page = '<!doctype html><meta charset="utf-8"><title>DriveClarify passive snapshot</title>'
    page += '<style>body{background:#0c1520;color:#e4edf4;font:15px sans-serif;margin:24px}header{color:#ffd074}pre{white-space:pre-wrap;line-height:1.55}svg{width:330px;margin:8px}details{margin-top:24px}</style>'
    page += '<header>' + ' · '.join(LABELS) + '</header><h2>只读日志快照 / 非驾驶控制界面</h2>'
    page += '<pre>' + html.escape(summary_text(snapshot)) + '</pre>' + plots
    page += '<pre>' + html.escape(json.dumps(snapshot["candidates"], ensure_ascii=False, indent=2)) + '</pre>'
    page += '<details><summary>来源帧事件顺序（不是独立样本）</summary><pre>'
    page += html.escape(json.dumps(snapshot["timeline"], ensure_ascii=False, indent=2)) + '</pre></details>'
    (directory / "snapshot.html").write_text(page, encoding="utf-8")


def live(report, *, geometry="740x1270+1800+50", interval_ms=1000):
    import tkinter as tk
    root = tk.Tk(); root.title("DriveClarify — RESEARCH DEBUG VIEW")
    root.geometry(geometry); root.configure(bg="#101923")
    for text in LABELS:
        tk.Label(root, text=text, bg="#101923", fg="#ffd074", font=("DejaVu Sans", 11, "bold")).pack()
    tk.Label(root, text="仅只读日志 · 目标刷新1s · 非world叠加 · 缺失为UNKNOWN", fg="#adbdcc", bg="#101923").pack()
    plots = tk.Frame(root, bg="#101923"); plots.pack(fill="x")
    canvases = []
    for _ in range(2):
        canvas = tk.Canvas(plots, width=355, height=250, bg="#101c28", highlightthickness=0)
        canvas.pack(side="left", padx=6, pady=6); canvases.append(canvas)
    text_frame = tk.Frame(root, bg="#101923"); text_frame.pack(fill="both", expand=True, padx=10)
    scrollbar = tk.Scrollbar(text_frame); scrollbar.pack(side="right", fill="y")
    info = tk.Text(text_frame, bg="#101923", fg="#e4edf4", wrap="word", font=("DejaVu Sans", 10), relief="flat", yscrollcommand=scrollbar.set)
    info.pack(side="left", fill="both", expand=True); scrollbar.configure(command=info.yview)
    footer = tk.Label(root, bg="#101923", fg="#adbdcc"); footer.pack(fill="x")
    def refresh():
        started = time.perf_counter()
        try:
            snapshot = build_snapshot(report)
            candidates = snapshot["candidates"][:2]
            project = projection(candidates)
            for slot, canvas in enumerate(canvases):
                canvas.delete("all")
                candidate = candidates[slot] if slot < len(candidates) else {}
                canvas.create_text(10, 10, text="z%d / %s: x↑ y→ metres" % (slot + 1, display(candidate.get("candidate_id"))), fill="white", anchor="nw")
                if candidate.get("cache_status_at_latest_control") == "HISTORICAL_STALE_NOT_CURRENT_PLAN":
                    canvas.create_text(190, 30, text="历史 / STALE", fill="#ffd074", anchor="nw")
                origin = project((0., 0.)); forward = project((2., 0.)); right = project((0., 2.))
                canvas.create_line(*origin, *forward, fill="#8094a8", arrow="last")
                canvas.create_line(*origin, *right, fill="#8094a8", arrow="last")
                canvas.create_text(forward[0]+3, forward[1], text="x +2m", fill="#aebfcc", anchor="sw")
                canvas.create_text(right[0], right[1]+2, text="y +2m", fill="#aebfcc", anchor="nw")
                if candidate.get("xy_m"):
                    points = [coordinate for point in candidate["xy_m"] for coordinate in project(point)]
                    canvas.create_line(*points, fill="#56d9dd" if slot == 0 else "#efa674", width=3)
                    for point in candidate["xy_m"]:
                        x, y = project(point); canvas.create_oval(x-2, y-2, x+2, y+2, fill="white", outline="")
                else:
                    canvas.create_text(165, 110, text="UNKNOWN\n" + display(candidate.get("reason")), fill="#ffd074", width=300)
                canvas.create_text(10, 230, anchor="nw", fill="#aebfcc", text="frame %s / 合同2s Δ0.25s / age %ss" %
                    (display(candidate.get("source_frame")), display(candidate.get("cache_age_at_latest_control_simulation_s"))))
            info.configure(state="normal"); info.delete("1.0", "end")
            info.insert("end", summary_text(snapshot) + "\n\n候选源上下文：\n" + "\n".join(
                "%s: frame=%s sim=%s age=%s recorded_valid=%s cache=%s reason=%s" % tuple(display(c.get(k)) for k in
                    ("candidate_id", "source_frame", "source_time_s", "cache_age_at_latest_control_simulation_s", "valid", "cache_status_at_latest_control", "reason"))
                for c in candidates))
            info.configure(state="disabled")
            footer.configure(text="日志解析+刷新 %.1fms · wall %s" % ((time.perf_counter()-started)*1000, datetime.datetime.now().astimezone().isoformat(timespec="seconds")))
        except Exception as error:
            footer.configure(text="UNKNOWN / 只读刷新错误: " + repr(error))
        root.after(interval_ms, refresh)
    refresh(); root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--run")
    parser.add_argument("--until-frame", type=int)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--geometry", default="740x1270+1800+50")
    args = parser.parse_args()
    if args.once:
        snapshot = build_snapshot(args.report, args.run, args.until_frame)
        if args.output:
            export_once(snapshot, args.output)
            print(json.dumps({"json": str(args.output / "snapshot.json"), "html": str(args.output / "snapshot.html"), "run_id": snapshot["run_id"]}))
        else:
            print(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
    else:
        if args.run or args.until_frame:
            parser.error("指定历史run/frame请使用--once；live只跟随STATE.current_run")
        live(args.report, geometry=args.geometry)


if __name__ == "__main__":
    main()
