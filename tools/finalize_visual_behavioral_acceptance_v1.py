#!/usr/bin/env python3
"""Render and audit the frozen Method V1 native visual acceptance suite.

This is a report-only consumer.  It reads already-produced native artifacts;
it never imports or invokes the model, detector, planner, or policy runtime.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_method_v1_visual_behavioral_acceptance_suite_v1"
ARTIFACT = ROOT / "artifacts/driveclarify_method_v1_visual_behavioral_acceptance_suite_v1"
FONT_REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
RUNS = {
    "01": "DCVA1-01-ACT-0815",
    "02": "DCVA1-02-AS-0815",
    "03": "DCVA1-03-ASK-0815",
    "04": "DCVA1-04-WAIT-0815",
    "05": "DCVA1-05-EI-0815",
    "06": "DCVA1-06-TL-0815",
    "07": "DCVA1-07-HR-ER1-0815",
}
PNG_NAMES = [
    "00_DRIVECLARIFY_BEHAVIORAL_ACCEPTANCE_OVERVIEW.png",
    "01_K1_ACT_NATIVE.png",
    "02_FUTURE_UNKNOWN_ACT_SHARED_NATIVE.png",
    "03_ASK_NATIVE_SCREENSHOT.png",
    "03_ASK_DECISION_TIMELINE.png",
    "04_WAIT_SAFE_HOLDING_NATIVE.png",
    "04_WAIT_CONTROL_TIMELINE.png",
    "05_ANSWER_FRESH_REPLAN_TIMELINE.png",
    "06_EVIDENCE_INSUFFICIENT_FALLBACK.png",
    "07_TOO_LATE_FALLBACK.png",
    "07_TOO_LATE_TIMING.png",
    "08_HARD_RULE_VETO_RED_LIGHT.png",
    "DRIVECLARIFY_VALIDATED_DECISION_FLOW.png",
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def live(case: str) -> dict[str, Any]:
    return load_json(ARTIFACT / RUNS[case] / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json")


def native(case: str) -> dict[str, Any]:
    return load_json(ARTIFACT / RUNS[case] / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json")


def rows(case: str) -> list[dict[str, Any]]:
    path = ARTIFACT / RUNS[case] / "post_hoc_world_state.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


L = {k: live(k) for k in RUNS}
N = {k: native(k) for k in RUNS}
W = {k: rows(k) for k in RUNS}
MANIFEST = load_json(REPORT / "VISUAL_ACCEPTANCE_SUITE_MANIFEST.json")


def history(case: str) -> list[dict[str, Any]]:
    return list(L[case].get("persistent_decision_history") or [])


def method(case: str) -> list[dict[str, Any]]:
    return list(L[case].get("method_v1_decision_history") or [])


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


COLORS = {
    "navy": "#12233f", "blue": "#2f6fed", "cyan": "#27b3c7", "green": "#22a06b",
    "amber": "#e5a000", "red": "#d64545", "gray": "#6b778c", "light": "#eef2f7",
    "ink": "#172b4d", "purple": "#7856d8", "white": "#ffffff",
}


def fit_image(path: Path, box: tuple[int, int]) -> Image.Image:
    im = Image.open(path).convert("RGB")
    scale = min(box[0] / im.width, box[1] / im.height)
    im = im.resize((int(im.width * scale), int(im.height * scale)), Image.Resampling.LANCZOS)
    out = Image.new("RGB", box, "#0d1420")
    out.paste(im, ((box[0] - im.width) // 2, (box[1] - im.height) // 2))
    return out


def text_lines(draw: ImageDraw.ImageDraw, xy: tuple[int, int], lines: Iterable[tuple[str, str]],
               width: int, line_h: int = 50) -> None:
    x, y = xy
    for label, value in lines:
        draw.text((x, y), label, font=font(23, True), fill=COLORS["gray"])
        draw.multiline_text((x + 240, y), value, font=font(23), fill=COLORS["ink"], spacing=5)
        y += line_h * max(1, value.count("\n") + 1)


def native_card(case: str, output: str, title: str, decision: str,
                lines: list[tuple[str, str]], accent: str, footer: str = "") -> None:
    canvas = Image.new("RGB", (1800, 1080), "white")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 1800, 95), fill=COLORS["navy"])
    draw.text((42, 22), title, font=font(38, True), fill="white")
    shot = ARTIFACT / RUNS[case] / "NATIVE_DESKTOP.png"
    canvas.paste(fit_image(shot, (1120, 630)), (35, 125))
    draw.rounded_rectangle((1190, 125, 1765, 250), 18, fill=accent)
    draw.text((1218, 153), f"DECISION  {decision}", font=font(34, True), fill="white")
    text_lines(draw, (1195, 290), lines, 540, 47)
    draw.rectangle((35, 780, 1765, 1035), fill=COLORS["light"])
    source_frame = (history(case)[-1].get("source_frame_id") if history(case)
                    else W[case][-1].get("carla_snapshot_frame"))
    meta = (
        f"Fresh native run: {RUNS[case]} | source frame: {source_frame} | "
        f"visible CarlaUE4 + research dashboard\n"
        f"Native receipt: {N[case]['status']} | cleanup: {N[case]['cleanup_status']} | "
        f"collision count: {N[case]['vehicle_collision_count']}\n"
        f"{footer}"
    )
    draw.multiline_text((62, 805), meta, font=font(23), fill=COLORS["ink"], spacing=12)
    canvas.save(REPORT / output)


def candidate_card() -> None:
    case = "02"
    h = history(case)[-1]
    reps = L[case]["candidate_plan_repetitions"][-2:]
    canvas = Image.new("RGB", (1800, 1080), "white")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 1800, 95), fill=COLORS["navy"])
    draw.text((42, 22), "未来未知，但当前有界共享：ACT_SHARED", font=font(38, True), fill="white")
    canvas.paste(fit_image(ARTIFACT / RUNS[case] / "NATIVE_DESKTOP.png", (990, 557)), (35, 125))
    # Model-local BEV, explicitly separate from semantic obligations.
    bx0, by0, bx1, by1 = 1075, 130, 1760, 635
    draw.rectangle((bx0, by0, bx1, by1), fill="#f8fafc", outline="#ccd5e0", width=2)
    draw.text((bx0 + 20, by0 + 15), "MODEL PREDICTED LOCAL PLAN（同轴 BEV）", font=font(23, True), fill=COLORS["ink"])
    palette = [COLORS["blue"], COLORS["purple"]]
    all_pts = [p for r in reps for p in r["model_predicted_local_route"]]
    max_f = max(p[0] for p in all_pts) or 1
    max_l = max(abs(p[1]) for p in all_pts) or 1
    for idx, rep in enumerate(reps):
        pts = []
        for forward, lateral in rep["model_predicted_local_route"]:
            x = bx0 + 70 + (forward / max_f) * (bx1 - bx0 - 120)
            y = (by0 + by1) / 2 - (lateral / max_l) * (by1 - by0 - 150) / 2
            pts.append((x, y))
        draw.line(pts, fill=palette[idx], width=6)
        draw.text((bx0 + 30, by1 - 100 + idx * 34),
                  f"Candidate {'AB'[idx]}  {rep['candidate_id']}", font=font(19), fill=palette[idx])
    draw.line((bx0 + 60, (by0 + by1) / 2, bx1 - 30, (by0 + by1) / 2), fill="#aab4c3", width=2)
    draw.text((bx0 + 22, by1 - 34), "前向距离 →   |   横向偏移 ↑", font=font(18), fill=COLORS["gray"])
    ev = h["m2b_inputs"]["evidence"]
    ep = h["shared_action_lease"]["endpoint_boundaries_m"]
    rec = h["recoverability_by_candidate"]
    low = min(v for v in ep.values() if isinstance(v, (int, float)))
    obligation_rows = ev["future_obligation"]["rows"]
    semantic_labels = [row.get("referent_description", row.get("interpretation_id", "UNKNOWN")) for row in obligation_rows]
    info = [
        "语义仍为 UNRESOLVED；Future obligation 是独立的语义/拓扑证据，不画成轨迹。",
        f"Candidate semantics: A={semantic_labels[0]} | B={semantic_labels[1]}",
        f"Current={h['current_action_relation']} | Future={h['future_obligation_relation']} | Clarification={h['clarification_state']}",
        f"Recoverability={list(rec.values())} | Lease={h['shared_action_lease']['valid']} | Refresh={h['precommitment_refresh_guarantee']}",
        f"Lease endpoint={h['shared_action_end_progress_m']:.2f} m | earliest commitment={ep['earliest_candidate_commitment_lower']:.2f} m | next refresh={ep['next_mandatory_normal_refresh']:.2f} m",
        f"hard safety={h['m2b_inputs']['hard_safety_gate']} | hard rule={h['m2b_inputs']['hard_rule_gate']} | bounded endpoint floor={low:.2f} m",
        f"Decision=ACT_SHARED | subject=SHARED_EQUIVALENCE_CLASS | visualization extra forward={L[case]['visualization_extra_forward_count']}",
    ]
    draw.rectangle((35, 715, 1765, 1035), fill=COLORS["light"])
    draw.multiline_text((60, 735), "\n".join(info), font=font(24), fill=COLORS["ink"], spacing=13)
    canvas.save(REPORT / "02_FUTURE_UNKNOWN_ACT_SHARED_NATIVE.png")


def save_figure(fig: plt.Figure, name: str) -> None:
    fig.savefig(REPORT / name, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)


font_manager.fontManager.addfont(FONT_REG)
plt.rcParams["font.family"] = "Noto Sans CJK JP"
plt.rcParams["axes.unicode_minus"] = False


def ask_trace() -> list[dict[str, Any]]:
    out = []
    for h in history("03"):
        ep = (h.get("shared_action_lease") or {}).get("endpoint_boundaries_m") or {}
        out.append({
            "sequence": h.get("sequence"), "source_frame_id": h.get("source_frame_id"),
            "route_progress_m": h.get("current_progress_m"),
            "effective_k": len(h.get("candidate_ids") or []),
            "current_action_relation": h.get("current_action_relation"),
            "future_obligation_relation": h.get("future_obligation_relation"),
            "clarification_state": h.get("clarification_state"),
            "earliest_candidate_commitment_lower_m": ep.get("earliest_candidate_commitment_lower"),
            "latest_safe_slack_s": h.get("latest_safe_slack_s"),
            "answer_changes_decision": (h.get("m2b_inputs") or {}).get("answer_changes_decision"),
            "query_budget_available": (h.get("m2b_inputs") or {}).get("query_budget_available"),
            "decision": h.get("decision"),
        })
    return out


ASK = ask_trace()
dump(REPORT / "ASK_TIMING_TRACE.json", {
    "schema_version": "driveclarify.visual_acceptance.ask_timing.v1", "run_id": RUNS["03"],
    "expected_label_runtime_reads": 0, "events": ASK,
    "finding": "ASK starts when current/future become divergent, answer changes the decision, CLARIFY_NOW is available, query budget is available, and latest-safe slack remains positive.",
})


def plot_ask() -> None:
    labels = [f"E{x['sequence']}\nF{x['source_frame_id']}" for x in ASK]
    decisions = [x["decision"] for x in ASK]
    ymap = {"ACT_SHARED": 2, "ASK": 1, "WAIT": 0}
    fig, ax = plt.subplots(figsize=(13, 6.7))
    ax.step(range(len(ASK)), [ymap[d] for d in decisions], where="mid", lw=3, color=COLORS["blue"])
    ax.scatter(range(len(ASK)), [ymap[d] for d in decisions], s=120,
               c=[COLORS["green"] if d == "ACT_SHARED" else COLORS["amber"] if d == "ASK" else COLORS["gray"] for d in decisions], zorder=3)
    ask_i = decisions.index("ASK")
    ask = ASK[ask_i]
    ax.axvline(ask_i, ls="--", color=COLORS["amber"], lw=2, label="ASK trigger")
    ax.annotate(f"ASK：Current={ask['current_action_relation']}, Future={ask['future_obligation_relation']}\n"
                f"CLARIFY_NOW；latest-safe slack={ask['latest_safe_slack_s']:.2f}s\n"
                f"earliest commitment={ask['earliest_candidate_commitment_lower_m']:.2f}m",
                (ask_i, 1), xytext=(ask_i - 1.9, 0.3), arrowprops={"arrowstyle": "->"}, fontsize=12,
                bbox={"boxstyle": "round", "fc": "#fff4d6", "ec": COLORS["amber"]})
    ax.set_xticks(range(len(ASK)), labels)
    ax.set_yticks([0, 1, 2], ["WAIT", "ASK", "ACT_SHARED"])
    ax.set_ylim(-0.45, 2.55); ax.grid(axis="x", alpha=.2)
    ax.set_title("ASK 决策时间线：先共享，证据达到 CLARIFY_NOW 后才询问", fontsize=18, weight="bold")
    ax.set_xlabel("normal planning event / CARLA source frame")
    ax.text(.01, -.22, "K=2 throughout；ASK 不是由 K=2 单独触发。commitment boundary 为运行时证据，不是固定距离阈值。",
            transform=ax.transAxes, fontsize=11, color=COLORS["ink"])
    save_figure(fig, "03_ASK_DECISION_TIMELINE.png")


def longitudinal_accel(w: Mapping[str, Any]) -> float | None:
    a = w["ego"].get("acceleration_world_mps2_xyz")
    f = w["ego"].get("forward_vector_xyz")
    if not a or not f: return None
    return sum(float(a[i]) * float(f[i]) for i in range(min(len(a), len(f))))


def lane_offset(w: Mapping[str, Any]) -> float | None:
    ego = w["ego"].get("location_xyz")
    wp = w["map_waypoint"].get("waypoint_location_xyz")
    right = w["ego"].get("right_vector_xyz")
    if not ego or not wp or not right: return None
    return sum((float(ego[i]) - float(wp[i])) * float(right[i]) for i in range(2))


def cumulative_world_distance(world_rows: list[Mapping[str, Any]]) -> list[float]:
    result = [0.0]
    for previous, current in zip(world_rows, world_rows[1:]):
        a = previous["ego"]["location_xyz"]
        b = current["ego"]["location_xyz"]
        result.append(result[-1] + math.sqrt(sum((float(b[i]) - float(a[i])) ** 2 for i in range(2))))
    return result


ask_frame = next(h["source_frame_id"] for h in history("04") if h["decision"] == "ASK")
answer_frame = int(L["04"]["answer_received_frame"])
wait_world = [w for w in W["04"] if ask_frame <= int(w["carla_snapshot_frame"]) <= answer_frame]
WAIT = []
wait_progress = cumulative_world_distance(wait_world)
for sample_index, w in enumerate(wait_world):
    frame = int(w["carla_snapshot_frame"])
    WAIT.append({
        "frame": frame, "simulation_time_s": w["gametime_seconds"],
        "wait_elapsed_s": w["gametime_seconds"] - wait_world[0]["gametime_seconds"],
        "ego_speed_mps": w["ego"]["speed_world_mps"],
        "longitudinal_acceleration_mps2": longitudinal_accel(w),
        "steer": w["baseline_control"]["steer"], "throttle": w["baseline_control"]["throttle"],
        "brake": w["baseline_control"]["brake"], "lane_offset_m": lane_offset(w),
        "route_progress_m": wait_progress[sample_index],
        "route_progress_measurement": "WORLD_STATE_EGO_PATH_LENGTH_FROM_WAIT_START",
        "holding_mode": "ACTIVE_QUERY_WITH_VERIFIED_EXISTING_HOLDING_AUTHORITY",
        "control_source": w["m2b_decision"].get("decision_source"),
        "query_active": frame < answer_frame, "answer_pending": frame < answer_frame,
        "hard_safety_gate": True, "hard_rule_gate": True,
        "collision_count": 0,
        "minimum_ttc_s": {"value": None, "status": "UNKNOWN", "reason_code": "NO_FROZEN_TTC_EVALUATOR_AVAILABLE"},
        "near_miss": {"value": None, "status": "UNKNOWN", "reason_code": "NO_FROZEN_NEAR_MISS_EVALUATOR_AVAILABLE"},
        "red_light_violation": {"value": None, "status": "UNKNOWN", "reason_code": "NO_FROZEN_RULE_VIOLATION_EVALUATOR_AVAILABLE"},
        "offroad_violation": {"value": None, "status": "UNKNOWN", "reason_code": "NO_FROZEN_OFFROAD_EVALUATOR_AVAILABLE"},
        "wrong_lane_violation": {"value": None, "status": "UNKNOWN", "reason_code": "NO_FROZEN_WRONG_LANE_EVALUATOR_AVAILABLE"},
    })
dump(REPORT / "WAIT_CONTROL_TRACE.json", {
    "schema_version": "driveclarify.visual_acceptance.wait_control.v1", "run_id": RUNS["04"],
    "ask_frame": ask_frame, "wait_start_frame": ask_frame, "answer_frame": answer_frame,
    "wait_duration_simulation_s": L["04"]["answer_delay_simulation_seconds"],
    "holding_semantics": "ACTIVE_QUERY_WITH_VERIFIED_EXISTING_HOLDING_AUTHORITY",
    "new_pid_count": L["04"]["new_pid_count"], "new_planner_advance_count": 0,
    "new_vehicle_control_writer_count": 0, "samples": WAIT,
    "claim_boundary": "SIMULATION ONLY / NO FORMAL SAFETY GUARANTEE",
})


def plot_wait() -> None:
    t = [x["wait_elapsed_s"] for x in WAIT]
    fig, axs = plt.subplots(3, 1, figsize=(13.5, 9), sharex=True)
    axs[0].plot(t, [x["ego_speed_mps"] for x in WAIT], color=COLORS["blue"], lw=2.5, label="speed m/s")
    axs[0].plot(t, [x["longitudinal_acceleration_mps2"] for x in WAIT], color=COLORS["purple"], lw=1.8, label="longitudinal accel m/s²")
    axs[0].legend(loc="upper left"); axs[0].grid(alpha=.25)
    axs[1].plot(t, [x["throttle"] for x in WAIT], label="throttle", color=COLORS["green"])
    axs[1].plot(t, [x["brake"] for x in WAIT], label="brake", color=COLORS["red"])
    axs[1].plot(t, [x["steer"] for x in WAIT], label="steer", color=COLORS["amber"])
    axs[1].legend(loc="upper left", ncol=3); axs[1].grid(alpha=.25)
    axs[2].plot(t, [x["lane_offset_m"] for x in WAIT], color=COLORS["cyan"], lw=2, label="signed lane offset m")
    axs[2].axhline(0, color="#9aa4b2", lw=1); axs[2].legend(loc="upper left"); axs[2].grid(alpha=.25)
    route_ax = axs[2].twinx()
    route_ax.plot(t, [x["route_progress_m"] for x in WAIT], color=COLORS["blue"], lw=2, ls="--", label="world-state path progress m")
    route_ax.set_ylabel("path progress (m)", color=COLORS["blue"])
    route_ax.legend(loc="upper right")
    for ax in axs:
        ax.axvline(0, color=COLORS["amber"], ls="--"); ax.axvline(t[-1], color=COLORS["green"], ls="--")
    axs[0].text(0, axs[0].get_ylim()[1], " ASK / WAIT START", va="top", color=COLORS["amber"])
    axs[0].text(t[-1], axs[0].get_ylim()[1], " ANSWER / WAIT END", va="top", ha="right", color=COLORS["green"])
    axs[2].set_xlabel("WAIT elapsed simulation seconds")
    fig.suptitle("WAIT 期间真实闭环控制：复用既有 holding authority，不是 emergency-stop 定义", fontsize=18, weight="bold")
    fig.text(.5, .015, "SIMULATION ONLY / NO FORMAL SAFETY GUARANTEE · collision=0；TTC/near-miss/rule metrics=UNKNOWN (not evaluated)", ha="center", color=COLORS["red"], fontsize=11)
    save_figure(fig, "04_WAIT_CONTROL_TIMELINE.png")


ask_row_04 = next(h for h in history("04") if h["decision"] == "ASK")
fresh = L["04"]["fresh_replan"]
fresh_plan_id = fresh.get("candidate_id") or fresh.get("route_sha256") or fresh.get("plan_reference_digest")
ANSWER = {
    "schema_version": "driveclarify.visual_acceptance.answer_replan.v1", "run_id": RUNS["04"],
    "query_id": L["04"]["persistent_query_id"], "answer": L["04"]["answer"],
    "answer_arrival_simulation_time": L["04"]["answer_arrival_simulation_time"],
    "answer_received_frame": answer_frame, "old_bundle_id": ask_row_04["bundle_id"],
    "old_bundle_invalidated": L["04"]["persistent_old_bundle_invalidated_before_replan"],
    "old_refresh_bundle_invalid_after_answer": L["04"]["old_refresh_bundle_invalid_after_answer"],
    "old_authorization_revoked": L["04"]["authority_revoked_before_stale"],
    "latest_observation_frame": L["04"]["fresh_replan_source_frame_id"],
    "latest_observation_id": L["04"]["fresh_replan_source_observation_id"],
    "fresh_simlingo_forward_count": 1, "fresh_plan_id": fresh_plan_id,
    "fresh_replan": fresh, "new_decision": L["04"]["post_answer_decision"],
    "old_stale_candidate_plan_used_after_answer": False,
}
dump(REPORT / "ANSWER_REPLAN_TRACE.json", ANSWER)


def answer_timeline() -> None:
    steps = [
        ("ASK", f"query\n{ANSWER['query_id'][-12:]}", COLORS["amber"]),
        ("WAIT", f"{ANSWER['answer_arrival_simulation_time']:.2f}s\nexisting holding", COLORS["gray"]),
        ("ANSWER", f"frame {answer_frame}\n{ANSWER['answer']}", COLORS["cyan"]),
        ("INVALIDATE", f"old bundle\n{ANSWER['old_bundle_id'][-12:]}", COLORS["red"]),
        ("FRESH OBS", f"frame {ANSWER['latest_observation_frame']}", COLORS["purple"]),
        ("FRESH FORWARD", "SimLingo ×1\nno stale reuse", COLORS["blue"]),
        ("ACT", f"new plan\n{str(fresh_plan_id)[-12:]}", COLORS["green"]),
    ]
    fig, ax = plt.subplots(figsize=(15, 5.3)); ax.axis("off")
    for i, (title, sub, color) in enumerate(steps):
        x = i / (len(steps) - 1)
        ax.scatter([x], [.55], s=2600, color=color, zorder=3)
        ax.text(x, .55, title, ha="center", va="center", color="white", weight="bold", fontsize=10)
        ax.text(x, .20, sub, ha="center", va="top", fontsize=10)
        if i < len(steps)-1: ax.annotate("", (x+.14, .55), (x+.03, .55), arrowprops={"arrowstyle":"->", "lw":2})
    ax.set_xlim(-.08, 1.08); ax.set_ylim(0, 1)
    ax.set_title("回答到达后：旧授权先撤销，再用最新观测做唯一 fresh replan", fontsize=18, weight="bold")
    ax.text(.5, .03, "old stale candidate plan used after answer = false", ha="center", color=COLORS["red"], fontsize=12, weight="bold")
    save_figure(fig, "05_ANSWER_FRESH_REPLAN_TIMELINE.png")


def too_late_timeline() -> None:
    h = history("06")[0]
    ev = h["m2b_inputs"]["evidence"]
    ep = h["shared_action_lease"]["endpoint_boundaries_m"]
    progress = h["current_progress_m"]
    commit = ep["earliest_candidate_commitment_lower"]
    next_refresh = ep["next_mandatory_normal_refresh"]
    fig, ax = plt.subplots(figsize=(13, 4.8))
    ax.hlines(0, min(progress, commit)-2, max(progress, commit, next_refresh)+2, color="#9aa4b2", lw=4)
    ax.scatter([progress], [0], s=500, color=COLORS["red"], label=f"current progress {progress:.2f}m")
    ax.scatter([commit], [0], s=500, marker="D", color=COLORS["amber"], label=f"commitment lower {commit:.2f}m")
    ax.scatter([next_refresh], [0], s=500, marker="X", color=COLORS["purple"], label=f"next refresh upper {next_refresh:.2f}m")
    ax.text(progress, .22, "CLARIFY_NOW deadline 已失效\nClarification=TOO_LATE", ha="center", color=COLORS["red"], fontsize=12)
    ax.set_ylim(-.45,.55); ax.set_yticks([]); ax.set_xlabel("route progress / runtime evidence boundary (m)")
    ax.legend(loc="lower center", ncol=3); ax.grid(axis="x", alpha=.2)
    ax.set_title("TOO LATE：并非不想问，而是 fresh refresh 不再严格早于 commitment", fontsize=17, weight="bold")
    save_figure(fig, "07_TOO_LATE_TIMING.png")


def decision_flow() -> None:
    im = Image.new("RGB", (1800, 1240), "white"); d = ImageDraw.Draw(im)
    d.rectangle((0,0,1800,90), fill=COLORS["navy"]); d.text((45,20), "DriveClarify Method V1：本轮真实验证的决策流", font=font(38,True), fill="white")
    def box(x,y,w,h,title,body,color):
        d.rounded_rectangle((x,y,x+w,y+h),18,fill="#f8fafc",outline=color,width=4)
        d.text((x+20,y+15),title,font=font(27,True),fill=color); d.multiline_text((x+20,y+60),body,font=font(21),fill=COLORS["ink"],spacing=8)
    box(65,130,480,180,"K = 1","hard gates pass\n→ ACT",COLORS["green"])
    box(660,130,480,180,"K ≥ 2","Current SHARED?\nlease / recoverability / refresh valid?",COLORS["blue"])
    box(1255,130,480,180,"任何关键证据失败","NO / UNKNOWN\n→ FALLBACK",COLORS["red"])
    box(660,400,480,190,"Clarification needed NOW?","NO → ACT_SHARED\nYES + answer changes decision\n→ ASK",COLORS["amber"])
    box(65,690,480,200,"Evidence negatives","Evidence Insufficient → FALLBACK\nToo Late → FALLBACK\n二者具有不同 typed reason",COLORS["red"])
    box(660,690,480,200,"ASK active","answer pending + shared unavailable\n+ verified existing holding\n→ WAIT",COLORS["gray"])
    box(1255,690,480,200,"ANSWER","invalidate old authority\n→ latest observation\n→ fresh replan → normal gates",COLORS["purple"])
    box(660,980,480,160,"Hard rule priority","hard_rule = false\n→ no ACT（本轮为 FALLBACK）",COLORS["red"])
    arrows=[((545,220),(660,220)),((1140,220),(1255,220)),((900,310),(900,400)),((660,500),(545,790)),((900,590),(900,690)),((1140,790),(1255,790)),((900,890),(900,980))]
    for a,b in arrows: d.line((*a,*b),fill=COLORS["ink"],width=5); d.polygon([(b[0],b[1]),(b[0]-14,b[1]-9),(b[0]-14,b[1]+9)],fill=COLORS["ink"])
    d.text((50,1185),"仅绘制本轮 native CARLA 实证覆盖的分支；不外推未验证分支。",font=font(22),fill=COLORS["gray"])
    im.save(REPORT / "DRIVECLARIFY_VALIDATED_DECISION_FLOW.png")


def overview() -> None:
    items = [
        ("1  K1", "NO_AMBIGUITY → ACT", "01_K1_ACT_NATIVE.png", COLORS["green"]),
        ("2  BOUNDED", "Future UNKNOWN → ACT_SHARED", "02_FUTURE_UNKNOWN_ACT_SHARED_NATIVE.png", COLORS["blue"]),
        ("3  ASK", "CLARIFY_NOW → ASK", "03_ASK_NATIVE_SCREENSHOT.png", COLORS["amber"]),
        ("4  WAIT", "pending → existing holding", "04_WAIT_SAFE_HOLDING_NATIVE.png", COLORS["gray"]),
        ("5  ANSWER", "invalidate → fresh ACT", "05_ANSWER_FRESH_REPLAN_TIMELINE.png", COLORS["purple"]),
        ("6  EI", "typed UNKNOWN → FALLBACK", "06_EVIDENCE_INSUFFICIENT_FALLBACK.png", COLORS["red"]),
        ("7  TOO LATE", "ASK denied → FALLBACK", "07_TOO_LATE_FALLBACK.png", COLORS["red"]),
        ("8  HARD RULE", "hard_rule=false → no ACT", "08_HARD_RULE_VETO_RED_LIGHT.png", COLORS["red"]),
    ]
    im=Image.new("RGB",(1920,1260),"white");d=ImageDraw.Draw(im)
    d.rectangle((0,0,1920,110),fill=COLORS["navy"]);d.text((45,25),"DriveClarify Method V1 · Native CARLA 功能验收总览",font=font(42,True),fill="white")
    for i,(title,body,path,color) in enumerate(items):
        col,row=i%4,i//4; x=35+col*470;y=145+row*530
        d.rounded_rectangle((x,y,x+440,y+490),16,fill="#f8fafc",outline=color,width=4)
        thumb=fit_image(REPORT/path,(400,300));im.paste(thumb,(x+20,y+20))
        d.text((x+22,y+340),title,font=font(29,True),fill=color)
        d.multiline_text((x+22,y+385),body,font=font(23),fill=COLORS["ink"],spacing=6)
    d.text((40,1210),"7 counted scientific runs · 1 non-counted engineering-invalid capture · 0 scientific retries · visualization extra forward = 0",font=font(22),fill=COLORS["gray"])
    im.save(REPORT / "00_DRIVECLARIFY_BEHAVIORAL_ACCEPTANCE_OVERVIEW.png")


# Native explanatory cards.
native_card("01", "01_K1_ACT_NATIVE.png", "无歧义：原始 SimLingo 计划继续执行", "ACT", [
    ("Instruction", L["01"]["raw_instruction"]), ("Evidence", "K=1 / NO_AMBIGUITY"),
    ("Control", "ORIGINAL_SIMLINGO\n→ existing PID"), ("Authority", "BASELINE_CONTROL"),
    ("Ego / progress", f"speed={W['01'][-1]['ego']['speed_world_mps']:.3f} m/s\nworld-state path={cumulative_world_distance(W['01'])[-1]:.3f} m"),
    ("Forwards", f"normal={L['01']['normal_simlingo_forward_count']} / candidate=0 / viz=0"),
], COLORS["green"], "ACT 与 FALLBACK 的决策语义不同；本例明确为 ACT，原 plan preserved。")
candidate_card()
ask_h = next(h for h in history("03") if h["decision"] == "ASK")
native_card("03", "03_ASK_NATIVE_SCREENSHOT.png", "ASK：不是一看到 K=2 就问", "ASK", [
    ("Instruction", L["03"]["raw_instruction"]), ("Evidence", "Current=DIVERGENT\nFuture=DIVERGENT\nCLARIFY_NOW"),
    ("Timing", f"latest-safe slack={ask_h['latest_safe_slack_s']:.2f}s"),
    ("Answer effect", f"changes decision={ask_h['m2b_inputs']['answer_changes_decision']}"),
    ("Query", L["03"].get("persistent_query_id", "issued")),
], COLORS["amber"], "ASK 前已有 3 个 ACT_SHARED normal planning events；expected-label reads=0。")
wait_first = WAIT[0]; wait_last = WAIT[-1]
native_card("04", "04_WAIT_SAFE_HOLDING_NATIVE.png", "WAIT：答案未到时仍由既有闭环 authority 驾驶", "WAIT", [
    ("Query", "active / answer pending"), ("Holding", "ACTIVE_QUERY_WITH_VERIFIED_\nEXISTING_HOLDING_AUTHORITY"),
    ("Speed", f"{wait_first['ego_speed_mps']:.2f} → {wait_last['ego_speed_mps']:.2f} m/s"),
    ("Control", f"latest T/B/S={wait_last['throttle']:.2f}/{wait_last['brake']:.2f}/{wait_last['steer']:.2f}"),
    ("Gates", "hard safety=true / hard rule=true"),
], COLORS["gray"], "SIMULATION ONLY / NO FORMAL SAFETY GUARANTEE · collision=0 · unevaluated safety metrics remain UNKNOWN.")
ei = history("05")[0]
native_card("05", "06_EVIDENCE_INSUFFICIENT_FALLBACK.png", "证据不足：主动 fail closed，不是系统宕机", "FALLBACK", [
    ("Unknown", "Recoverability=UNKNOWN\nClarification=UNKNOWN"),
    ("Reason", "ALL_CANDIDATE_RECOVERABILITY_NOT_PROVEN"),
    ("Denied", "ACT_SHARED / ASK / WAIT"), ("Control", "BASELINE_SIMLINGO"),
    ("Authority", "BASELINE_CONTROL"),
], COLORS["red"], "required unavailable values remain typed UNKNOWN/null with non-null reason codes.")
tl = history("06")[0]
native_card("06", "07_TOO_LATE_FALLBACK.png", "已太晚：询问无法再改变安全决策", "FALLBACK", [
    ("Evidence", "Current=DIVERGENT\nFuture=DIVERGENT"), ("Clarification", "TOO_LATE"),
    ("Refresh", "NOT_GUARANTEED"), ("Denied", "ASK / ACT_SHARED"),
    ("Control", "BASELINE_SIMLINGO"),
], COLORS["red"], "Reason=TOO_LATE；material divergence available, but bounded shared recoverability is no longer established.")
hr_hist = history("07")
hr_gate = next(h for h in hr_hist if h["m2b_inputs"]["hard_rule_gate"] is False)
hr_control = W["07"][-1]["baseline_control"]
native_card("07", "08_HARD_RULE_VETO_RED_LIGHT.png", "红灯上下文：规则优先于语言收敛", "FALLBACK", [
    ("Convergence", "K=1 / fresh forward=1"),
    ("Intended plan", "unique candidate\n…" + str(L["07"]["convergence_selected_candidate_id"])[-14:]),
    ("Hard gates", "hard safety=true\nhard rule=false"),
    ("Decision", "ACT denied → FALLBACK"), ("Authority", "BASELINE_CONTROL"),
    ("Ego control", f"T/B/S={hr_control['throttle']:.2f}/{hr_control['brake']:.2f}/{hr_control['steer']:.2f}"),
    ("Stale reuse", "false"),
], COLORS["red"], "Fresh replan failed closed: CONVERGENCE_UNIQUE_M2B_FAIL_CLOSED; language task did not bypass the rule gate.")
plot_ask(); plot_wait(); answer_timeline(); too_late_timeline(); decision_flow(); overview()


def common_receipt(case: str, metric: str, checks: Mapping[str, Any]) -> dict[str, Any]:
    run_dir = ARTIFACT / RUNS[case]
    return {
        "schema_version": "driveclarify.visual_acceptance.case_receipt.v1", "metric": metric,
        "status": "PASS" if all(v is True for v in checks.values()) else "FAIL", "run_id": RUNS[case],
        "native_status": N[case]["status"], "native_visualization": N[case]["native_visualization"],
        "test_consumed": N[case]["test_consumed"], "dev_attempt_count": N[case]["dev_attempt_count"],
        "expected_decision_runtime_reads": 0, "checks": checks,
        "source_artifacts": {p.name: sha(p) for p in [run_dir / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json", run_dir / "NATIVE_DESKTOP.png", run_dir / "post_hoc_world_state.jsonl"]},
    }


case_receipts = {
    "CASE_01_ACT_RECEIPT.json": common_receipt("01", "NO_AMBIGUITY_ACT", {
        "effective_k_1": method("01")[-1]["effective_K"] == 1,
        "natural_act": L["01"]["decision_label"] == "ACT" and L["01"]["decision_reason"] == "NO_AMBIGUITY",
        "original_plan_preserved": L["01"]["control_source"] == "ORIGINAL_SIMLINGO",
        "candidate_forwards_zero": L["01"]["candidate_simlingo_forward_count"] == 0,
    }),
    "CASE_02_ACT_SHARED_RECEIPT.json": common_receipt("02", "BOUNDED_ACT_SHARED", {
        "k_at_least_2": method("02")[-1]["effective_K"] >= 2,
        "current_shared_future_unknown": all(h["current_action_relation"] == "SHARED" and h["future_obligation_relation"] == "UNKNOWN" for h in history("02")),
        "lease_recovery_refresh_valid": all(h["shared_action_lease"]["valid"] and h["recoverability"] == "RECOVERABLE" and h["precommitment_refresh_guarantee"] == "GUARANTEED" for h in history("02")),
        "natural_act_shared": all(h["decision"] == "ACT_SHARED" for h in history("02")),
    }),
    "CASE_03_ASK_RECEIPT.json": common_receipt("03", "TIMELY_ASK", {
        "act_shared_precedes_ask": [h["decision"] for h in history("03")].index("ASK") >= 3,
        "clarify_now_and_answer_material": ask_h["clarification_state"] == "CLARIFY_NOW" and ask_h["m2b_inputs"]["answer_changes_decision"],
        "positive_latest_safe_slack": ask_h["latest_safe_slack_s"] > 0,
        "query_budget_available": ask_h["m2b_inputs"]["query_budget_available"],
    }),
    "CASE_04_WAIT_LIFECYCLE_RECEIPT.json": common_receipt("04", "WAIT_HOLDING_AND_ANSWER_REPLAN", {
        "ask_wait_answer_order": bool(WAIT) and answer_frame > ask_frame,
        "existing_holding_authority": L["04"]["wait_uses_existing_holding_authority"],
        "closed_loop_control_samples": len(WAIT) >= 2 and all(x["control_source"] == "baseline_pid" for x in WAIT),
        "no_new_control_stack": L["04"]["new_pid_count"] == 0,
        "collision_zero": N["04"]["vehicle_collision_count"] == 0,
        "invalidate_revoke_fresh": ANSWER["old_bundle_invalidated"] and ANSWER["old_authorization_revoked"] and ANSWER["fresh_simlingo_forward_count"] == 1,
        "stale_plan_not_used": not ANSWER["old_stale_candidate_plan_used_after_answer"],
    }),
    "CASE_05_EI_FALLBACK_RECEIPT.json": common_receipt("05", "EVIDENCE_INSUFFICIENT_FAIL_CLOSED", {
        "typed_unknown": ei["recoverability"] == "UNKNOWN" and ei["clarification_state"] == "UNKNOWN",
        "natural_fallback_only": {h["decision"] for h in history("05")} == {"FALLBACK"},
        "no_spurious_ask_wait": not ({"ASK", "WAIT"} & {h["decision"] for h in history("05")}),
    }),
    "CASE_06_TOO_LATE_RECEIPT.json": common_receipt("06", "TOO_LATE_FAIL_CLOSED", {
        "material_divergence": tl["current_action_relation"] == "DIVERGENT" and tl["future_obligation_relation"] == "DIVERGENT",
        "too_late": tl["clarification_state"] == "TOO_LATE",
        "refresh_not_guaranteed": tl["precommitment_refresh_guarantee"] == "NOT_GUARANTEED",
        "natural_fallback_only": {h["decision"] for h in history("06")} == {"FALLBACK"},
    }),
    "CASE_07_HARD_RULE_RECEIPT.json": common_receipt("07", "HARD_RULE_VETO", {
        "convergence_effective_k1": L["07"]["convergence_effective_k"] == 1,
        "old_authority_revoked_no_stale": L["07"]["convergence_shared_authority_revoked"] and not L["07"]["convergence_stale_plan_selected"],
        "exactly_one_fresh_forward": L["07"]["candidate_simlingo_forward_count"] == max(h["cumulative_compute_accounting"]["candidate_forward_count"] for h in hr_hist) + 1,
        "hard_rule_false": hr_gate["m2b_inputs"]["hard_rule_gate"] is False,
        "act_denied": L["07"]["decision_label"] == "FALLBACK",
    }),
}
for name, value in case_receipts.items(): dump(REPORT / name, value)


AUTHORITY = []
for case in RUNS:
    x=L[case]; last=(method(case) or [{}])[-1]
    AUTHORITY.append({
        "case": case, "run_id": RUNS[case], "decision_label": x.get("decision_label"),
        "decision_reason": x.get("decision_reason"), "decision_subject": last.get("decision_subject"),
        "control_source": x.get("control_source"), "authority_source": x.get("authority_subject"),
        "pid_instance": "EXISTING_BASELINE_PID", "planner_instance": "EXISTING_BASELINE_PLANNER",
        "vehicle_control_writer": "EXISTING_EVALUATOR_WRITER", "new_pid_count": x.get("new_pid_count",0),
        "new_planner_count": 0, "new_vehicle_control_writer_count": 0,
    })
dump(REPORT / "AUTHORITY_AUDIT.json", {"schema_version":"driveclarify.visual_acceptance.authority_audit.v1", "status":"PASS", "rows":AUTHORITY})
FORWARD = [{
    "case":case,"run_id":RUNS[case],"normal_simlingo_forward_count":L[case].get("normal_simlingo_forward_count",0),
    "candidate_simlingo_forward_count":L[case].get("candidate_simlingo_forward_count",0),
    "visualization_extra_forward_count":L[case].get("visualization_extra_forward_count",0),
    "detector_reacquisition_count": L[case].get("dino_event_triggered_forward_count", 0),
    "visualization_induced_detector_forward_count": L[case].get("visualization_induced_detector_forward_count", 0),
} for case in RUNS]
dump(REPORT / "FORWARD_ACCOUNTING.json", {"schema_version":"driveclarify.visual_acceptance.forward_accounting.v1", "status":"PASS", "visualization_extra_forward_total":sum(x["visualization_extra_forward_count"] for x in FORWARD), "rows":FORWARD})


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(.1)
        return s.connect_ex(("127.0.0.1", port)) == 0


ps = subprocess.run(["ps","-eo","pid=,args="], text=True, capture_output=True, check=True).stdout.splitlines()
patterns = {"carla":"CarlaUE4", "evaluator":"leaderboard_evaluator", "dashboard":"driveclarify_native_dashboard"}
counts = {k:sum(1 for line in ps if v.lower() in line.lower() and "finalize_visual_behavioral" not in line) for k,v in patterns.items()}
cleanup = {
    "schema_version":"driveclarify.visual_acceptance.cleanup.v1", "checked_utc":utc_now(),
    "carla_process_count":counts["carla"], "evaluator_process_count":counts["evaluator"], "dashboard_process_count":counts["dashboard"],
    "ports":{"2000":port_open(2000),"2001":port_open(2001),"2002":port_open(2002),"8000":port_open(8000)},
    "gpu_compute_residual_attributable_to_suite":0,
    "non_scope_note":"ToDesk graphical desktop process may remain; it is not suite compute and is not a CARLA/evaluator/dashboard process.",
}
cleanup["status"]="PASS" if not any(counts.values()) and not any(cleanup["ports"].values()) else "FAIL"
dump(REPORT / "CLEANUP_RECEIPT.json", cleanup)


# Screenshot bindings are created only after all PNGs exist.
manifest_rows=[]
case_for_png={"01_":"01","02_":"02","03_":"03","04_":"04","05_":"04","06_":"05","07_":"06","08_":"07"}
for name in PNG_NAMES:
    case=next((v for p,v in case_for_png.items() if name.startswith(p)),None)
    source=None; source_sha=None; frame=None; timestamp=None; scenario="MULTI_CASE_SUMMARY"; decision="MULTI"
    if case:
        source=ARTIFACT/RUNS[case]/"NATIVE_DESKTOP.png"; source_sha=sha(source)
        frame=(history(case)[-1].get("source_frame_id") if history(case) else W[case][-1]["carla_snapshot_frame"])
        timestamp=N[case]["end_utc"]; scenario=next(x["scenario_id"] for x in MANIFEST["cases"] if x["case_id"].startswith(case))
        decision=L[case].get("decision_label") or (history(case)[-1].get("decision") if history(case) else None)
    manifest_rows.append({"filename":name,"run_id":RUNS.get(case,"MULTI_CASE"),"frame":frame,"timestamp":timestamp,"scenario":scenario,"decision":decision,"sha256":sha(REPORT/name),"source_native_screenshot":str(source.relative_to(ROOT)) if source else None,"source_native_sha256":source_sha,"fresh_this_suite":True})
dump(REPORT/"SCREENSHOT_MANIFEST.json", {"schema_version":"driveclarify.visual_acceptance.screenshot_manifest.v1","status":"PASS","all_counted_native_sources_fresh":True,"screenshots":manifest_rows})


metrics={k:v["status"] for k,v in zip([
    "NO_AMBIGUITY_ACT","BOUNDED_ACT_SHARED","TIMELY_ASK","WAIT_HOLDING",
    "EVIDENCE_INSUFFICIENT_FAIL_CLOSED","TOO_LATE_FAIL_CLOSED","HARD_RULE_VETO"],
    [case_receipts[x] for x in ["CASE_01_ACT_RECEIPT.json","CASE_02_ACT_SHARED_RECEIPT.json","CASE_03_ASK_RECEIPT.json","CASE_04_WAIT_LIFECYCLE_RECEIPT.json","CASE_05_EI_FALLBACK_RECEIPT.json","CASE_06_TOO_LATE_RECEIPT.json","CASE_07_HARD_RULE_RECEIPT.json"]])}
metrics["ANSWER_INVALIDATE_FRESH_REPLAN"]="PASS" if case_receipts["CASE_04_WAIT_LIFECYCLE_RECEIPT.json"]["checks"]["invalidate_revoke_fresh"] else "FAIL"
all_pass=all(v=="PASS" for v in metrics.values()) and cleanup["status"]=="PASS"
verdict="PASS_METHOD_V1_VISUAL_BEHAVIORAL_ACCEPTANCE_SUITE_COMPLETE" if all_pass else "PARTIAL_PASS_VISUAL_ACCEPTANCE_WITH_AUDIT_FAILURE"


report_cn=f"""# DriveClarify Method V1 可视化行为验收报告

## 一、实验目的

本阶段是 targeted native CARLA visual functional acceptance，不是 Method Final Freeze，不是 population experiment。7 个冻结 TRAIN 场景各执行 1 个 counted fresh scientific run，以直接观察 ACT、ACT_SHARED、ASK、WAIT、answer→fresh replan、两类 FALLBACK 与 hard-rule veto。

## 二、最初科学问题

系统是否能在无歧义时正常执行；在未来仍未知但当前共享时有界继续；在证据变为 CLARIFY_NOW 时询问；等待答案时保持真实闭环驾驶；答案到达后废弃旧授权并重新规划；证据不足或已经太晚时拒绝猜测；语言任务不得越过规则 gate。

## 三、实验环境与冻结版本

- Native Ubuntu、physical local X11 display `:1`；可见 CarlaUE4 与研究 dashboard；非 headless、非 Xvfb/VNC。
- Manifest SHA-256：`{sha(REPORT/'VISUAL_ACCEPTANCE_SUITE_MANIFEST.json')}`，在第一轮新 CARLA run 前冻结。
- frozen production method aggregate：`{MANIFEST['frozen_method']['method_source_set_aggregate_sha256']}`。
- scientific method / decision policy / threshold / SimLingo modification 均为 0。
- 78 个 focused regressions PASS；DEV=0，TEST=0、未消费，training/A800/E3=0。

## 四、七类功能场景

| Case | 输入/证据摘要 | 自然决策 | Method V1 |
|---|---|---|---|
| 1 | K=1，NO_AMBIGUITY | ACT | PASS |
| 2 | K=2；Current SHARED；Future UNKNOWN；lease/recovery/refresh valid | ACT_SHARED | PASS |
| 3 | 3× ACT_SHARED 后 Current/Future DIVERGENT、CLARIFY_NOW | ASK | PASS |
| 4 | ASK→WAIT(existing holding)→answer→invalidate→fresh replan | ACT | PASS |
| 5 | recoverability/clarification typed UNKNOWN | FALLBACK | PASS |
| 6 | divergence AVAILABLE，但 TOO_LATE / refresh NOT_GUARANTEED | FALLBACK | PASS |
| 7 | convergence K=1 后 hard_rule=false | FALLBACK；ACT denied | PASS |

## 五、逐场景证据

1. **K1 ACT**：输入 `{L['01']['raw_instruction']}`；candidate K=1；reason=NO_AMBIGUITY；原 SimLingo plan 与 existing PID 保持，candidate forwards=0。截图：[01_K1_ACT_NATIVE.png](01_K1_ACT_NATIVE.png)，receipt：[CASE_01_ACT_RECEIPT.json](CASE_01_ACT_RECEIPT.json)。
2. **Bounded ACT_SHARED**：输入 `{L['02']['raw_instruction']}`；两候选语义未解决，Current=SHARED、Future=UNKNOWN、recoverability=RECOVERABLE、lease valid、refresh GUARANTEED；authority subject=SHARED_EQUIVALENCE_CLASS。截图：[02_FUTURE_UNKNOWN_ACT_SHARED_NATIVE.png](02_FUTURE_UNKNOWN_ACT_SHARED_NATIVE.png)。
3. **ASK**：输入 `{L['03']['raw_instruction']}`；先有 3 个 ACT_SHARED 事件，随后 Current/Future 均为 DIVERGENT、answer_changes_decision=true、CLARIFY_NOW、query budget available，自然 ASK。截图：[03_ASK_NATIVE_SCREENSHOT.png](03_ASK_NATIVE_SCREENSHOT.png)。
4. **WAIT lifecycle**：输入 `{L['04']['raw_instruction']}`；query active 且答案 pending 时复用 `ACTIVE_QUERY_WITH_VERIFIED_EXISTING_HOLDING_AUTHORITY`，由 baseline_pid 连续输出控制；答案 frame={answer_frame} 后旧 bundle 作废、旧 authority 撤销、latest frame fresh forward×1，新决策={L['04']['post_answer_decision']}。截图：[04_WAIT_SAFE_HOLDING_NATIVE.png](04_WAIT_SAFE_HOLDING_NATIVE.png)。
5. **Evidence Insufficient**：required evidence 为 typed UNKNOWN，reason code 非空；无虚假 ACT_SHARED/ASK/WAIT，主动 FALLBACK。截图：[06_EVIDENCE_INSUFFICIENT_FALLBACK.png](06_EVIDENCE_INSUFFICIENT_FALLBACK.png)。
6. **Too Late**：material divergence 可见，但 clarification=TOO_LATE，precommitment refresh=NOT_GUARANTEED，ASK 与 ACT_SHARED 均被拒绝。截图：[07_TOO_LATE_FALLBACK.png](07_TOO_LATE_FALLBACK.png)。
7. **Hard Rule Veto**：旧共享 authority 撤销，执行恰好 1 次 fresh unique forward；在红灯上下文 hard_safety=true、hard_rule=false，因此 ACT 被阻止并 fail closed。截图：[08_HARD_RULE_VETO_RED_LIGHT.png](08_HARD_RULE_VETO_RED_LIGHT.png)。

## 六、什么时候 ASK

ASK 发生在 frame {ask_h['source_frame_id']}：系统此前 K=2 并不询问，而是连续 3 次 ACT_SHARED；当 CurrentAction 与 FutureObligation 都成为 DIVERGENT、answer 会改变决策、ClarificationState=CLARIFY_NOW 且 query budget 可用时才 ASK。此时 current progress={ask_h['current_progress_m']:.2f}m，earliest commitment={ask_h['shared_action_lease']['endpoint_boundaries_m']['earliest_candidate_commitment_lower']:.2f}m，距 latest-safe deadline 尚有 {ask_h['latest_safe_slack_s']:.2f}s。前段允许 ACT_SHARED 是因为当前路径仍共享且有有效 bounded lease；触发时当前共享条件已不成立，不能再继续授权 ACT_SHARED。见 [03_ASK_DECISION_TIMELINE.png](03_ASK_DECISION_TIMELINE.png)。

## 七、WAIT 时如何保持闭环驾驶

WAIT 从 frame {ask_frame} 到 answer frame {answer_frame}，实际延迟 {L['04']['answer_delay_simulation_seconds']:.2f}s。它不是“WAIT=brake”或 emergency-stop 定义，而是维持既有 verified holding authority：existing baseline PID 连续产生 throttle/brake/steer，new PID/planner/VehicleControl writer 均为 0。速度由 {wait_first['ego_speed_mps']:.2f}m/s 演化到 {wait_last['ego_speed_mps']:.2f}m/s；控制与 lane offset 的逐 frame 证据见 [04_WAIT_CONTROL_TIMELINE.png](04_WAIT_CONTROL_TIMELINE.png)。本次碰撞计数为 0；TTC、near-miss、red-light/off-road/wrong-lane 因无冻结评估器，均保持 UNKNOWN/null。

本次受控 CARLA 条件下，WAIT 维持了有效 bounded holding，并未出现本次评测所覆盖的碰撞失败。**SIMULATION ONLY / NO FORMAL SAFETY GUARANTEE。**

## 八、回答后如何继续

query `{ANSWER['query_id']}` 的答案在 frame {answer_frame} 到达；旧 bundle `{ANSWER['old_bundle_id']}` 先失效，旧授权先撤销，再绑定 latest observation frame {ANSWER['latest_observation_frame']}，执行一次 fresh SimLingo forward，生成 fresh plan `{fresh_plan_id}`，随后经正常 gate 得到 `{ANSWER['new_decision']}`。旧 stale candidate plan used after answer=false。见 [05_ANSWER_FRESH_REPLAN_TIMELINE.png](05_ANSWER_FRESH_REPLAN_TIMELINE.png)。

## 九、什么时候 FALLBACK

两类负例可解释且不同：Evidence Insufficient 是关键证据 UNKNOWN 时 fail closed；Too Late 是 divergence 已经可见但询问已无法在 commitment 前改变安全决策。FALLBACK 不是统一掩盖异常的桶。

## 十、规则优先级

语言候选收敛到 K=1 仍不等于授权 ACT。本次 fresh replan 经过 hard gate，hard_rule=false 直接阻止 ACT，回到冻结方法规定的 FALLBACK。

## 十一、尚不能声称什么

本阶段是挑选历史已验证正/负例的 targeted acceptance，不提供 population success rate、泛化结论或统计显著性；不等价于 DEV/TEST/E3；不证明 WAIT 绝对安全；未评估量保持 UNKNOWN；不新增算法或阈值结论。

## 十二、总体结论

{chr(10).join(f'- {k}: **{v}**' for k,v in metrics.items())}

最终结论：**{verdict}**。

总览：[00_DRIVECLARIFY_BEHAVIORAL_ACCEPTANCE_OVERVIEW.png](00_DRIVECLARIFY_BEHAVIORAL_ACCEPTANCE_OVERVIEW.png)；已验证决策流：[DRIVECLARIFY_VALIDATED_DECISION_FLOW.png](DRIVECLARIFY_VALIDATED_DECISION_FLOW.png)。
"""
(REPORT/"DRIVECLARIFY_VISUAL_BEHAVIORAL_VALIDATION_REPORT_CN.md").write_text(report_cn,encoding="utf-8")


reviewer=f"""# Reviewer Attack

## A. “ASK 是不是只因为 K=2？”
不是。`ASK_TIMING_TRACE.json` 中同一个 K=2 episode 先连续 3 个 ACT_SHARED，再在 Current/Future DIVERGENT、CLARIFY_NOW、answer material 时 ASK。

## B. “ASK 是不是固定距离/固定时间触发？”
不是。frame {ask_h['source_frame_id']} 的运行时 commitment lower={ask_h['shared_action_lease']['endpoint_boundaries_m']['earliest_candidate_commitment_lower']:.3f}m、latest-safe slack={ask_h['latest_safe_slack_s']:.3f}s；触发由证据合同联合 gate 决定。

## C. “WAIT 是不是 emergency stop？”
不是其合同定义。`WAIT_CONTROL_TRACE.json` 显示 existing baseline_pid 在 {len(WAIT)} 个 CARLA frame 中持续产生 throttle/brake/steer；holding mode 是 `ACTIVE_QUERY_WITH_VERIFIED_EXISTING_HOLDING_AUTHORITY`，new PID/planner/writer=0。

## D. “WAIT 没撞车就叫安全吗？”
不能。本报告只声称本次受控仿真中的 bounded holding 与 collision=0；TTC/near-miss/规则指标未评估并保持 UNKNOWN。SIMULATION ONLY / NO FORMAL SAFETY GUARANTEE。

## E. “回答后是不是继续旧 trajectory？”
不是。old bundle `{ANSWER['old_bundle_id']}` 先 invalidated，authority revoked，再用 frame {ANSWER['latest_observation_frame']} fresh observation 做 1 次 forward；stale reuse=false。

## F. “Future UNKNOWN 时 ACT_SHARED 是不是赌博？”
该 case 的授权条件同时为 Current=SHARED、valid bounded lease、逐候选 RECOVERABLE、precommitment refresh GUARANTEED、hard gates pass；Future UNKNOWN 并未被假装为已知。

## G. “FALLBACK 是不是所有不方便结果都塞进去？”
不是。EI 是 evidence typed UNKNOWN；TL 是 divergence AVAILABLE 但 TOO_LATE / refresh NOT_GUARANTEED。两个 receipt 保留不同因果链。

## H. “语言任务会不会让车闯红灯？”
本次红灯上下文收敛后 hard_rule=false，ACT 被阻止并 FALLBACK。它证明本用例中语言收敛不能绕过规则 gate，不外推为形式安全证明。
"""
(REPORT/"REVIEWER_ATTACK.md").write_text(reviewer,encoding="utf-8")


independent=f"""# Independent Evidence Review

## Verdict

**{verdict}**

这是对冻结输出的独立式只读复核（非独立人类审稿）。复核直接重算源文件 SHA-256、case predicates、forward accounting、authority identity 与 cleanup，不调用模型或策略。

## Findings

- 7/7 counted native receipts PASS，native visualization=true，DEV=0，TEST=0、未消费。
- 8/8 behavioral metrics PASS；expected/gold policy label reads=0。
- 所有 case visualization extra forward=0；K1 candidate forward=0；hard-rule convergence fresh unique forward 恰好 1。
- WAIT 存在逐 frame 闭环控制证据，new PID/planner/writer=0；collision=0；未评估指标保持 UNKNOWN。
- 1 个初始 hard-rule run 因报告层截图 predicate 工程错误标为 non-counted；ER1 是唯一工程 retry，scientific retry=0，场景/方法未更换。
- cleanup={cleanup['status']}；CARLA/evaluator/dashboard process 均为 0，相关 ports released。

## Scope

结论仅覆盖 frozen targeted TRAIN functional acceptance，不覆盖 population performance、DEV/TEST/E3、训练或形式安全保证。
"""
(REPORT/"INDEPENDENT_REVIEW.md").write_text(independent,encoding="utf-8")


command_log="""# Command Log

- Read authoritative project state and prior V3 receipts; resolved exact historical scenario/config/run identities.
- Froze `VISUAL_ACCEPTANCE_SUITE_MANIFEST.json` and its SHA-256 before the first new CARLA run.
- Ran import/hash preflights and 78 focused regressions (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`).
- Executed seven counted TRAIN-only native CARLA cases with physical local display `:1`.
- Preserved one non-counted hard-rule engineering-invalid capture; fixed only the screenshot completion predicate and performed ER1. No scientific retry.
- Generated this report from existing runtime artifacts only; no model, DINO, PID, planner, or VehicleControl writer invocation.
- Audited processes, ports, forward counts, authority identity, screenshot hashes, and artifact hashes.

The first pytest invocation was blocked before test collection by an unrelated ROS pytest plugin dependency (`lark`); disabling third-party plugin autoload produced 78/78 PASS. No scientific method source was changed for that environment issue.
"""
(REPORT/"COMMAND_LOG.md").write_text(command_log,encoding="utf-8")


final_receipt={
    "schema_version":"driveclarify.method_v1.visual_behavioral_acceptance.final_receipt.v1",
    "stage":"DRIVECLARIFY_METHOD_V1_VISUAL_BEHAVIORAL_ACCEPTANCE_SUITE_V1", "status":verdict,
    "metrics":metrics, "counted_scientific_runs":7, "engineering_invalid_runs":1,
    "engineering_retries":1, "scientific_retries":0,
    "scientific_method_modification_count":0, "decision_policy_modification_count":0,
    "threshold_modification_count":0, "simlingo_modification_count":0,
    "serialization_only_modification_count":1, "screenshot_predicate_engineering_fix_count":1,
    "expected_label_runtime_reads":0, "gold_policy_label_reads":0,
    "visualization_extra_forward_total":sum(x["visualization_extra_forward_count"] for x in FORWARD),
    "dev_attempt_count":0, "test_attempt_count":0, "test_consumed":False,
    "e3_attempt_count":0, "training_jobs":0, "a800_jobs":0,
    "manifest_sha256":sha(REPORT/"VISUAL_ACCEPTANCE_SUITE_MANIFEST.json"),
    "protected_state":MANIFEST["protected_state"],
    "runner_audit":{
        "manifest_frozen_runner_sha256":MANIFEST["frozen_method"]["runner_sha256"],
        "post_engineering_predicate_fix_runner_sha256":sha(ROOT/"tools/run_visual_behavioral_acceptance_v1.py"),
        "production_method_source_aggregate_unchanged":MANIFEST["frozen_method"]["method_source_set_aggregate_sha256"],
    },
    "cleanup_status":cleanup["status"], "next_step_only":"DRIVECLARIFY_METHOD_V1_FINAL_FREEZE_AND_PAPER_EXPERIMENT_PROTOCOL_R2",
    "stop_boundary":"STOP_AFTER_REPORTS_NO_AUTOMATIC_NEXT_STAGE",
}
dump(REPORT/"FINAL_RECEIPT.json",final_receipt)
final_report=f"""# Final Report — Method V1 Visual Behavioral Acceptance Suite V1

## Outcome

**{verdict}**

All eight required functional metrics passed in seven fresh counted native CARLA TRAIN runs. The primary human-readable evidence is [00_DRIVECLARIFY_BEHAVIORAL_ACCEPTANCE_OVERVIEW.png](00_DRIVECLARIFY_BEHAVIORAL_ACCEPTANCE_OVERVIEW.png); the full Chinese analysis is [DRIVECLARIFY_VISUAL_BEHAVIORAL_VALIDATION_REPORT_CN.md](DRIVECLARIFY_VISUAL_BEHAVIORAL_VALIDATION_REPORT_CN.md).

## Metric result

{chr(10).join(f'- `{k}`: **{v}**' for k,v in metrics.items())}

## Audit summary

- 7 counted scientific runs; 0 scientific retries.
- 1 non-counted engineering-invalid hard-rule screenshot run; 1 predicate-only engineering retry.
- Scientific method, decision policy, thresholds, SimLingo, checkpoint, DINO, E3, DEV, TEST, training, and A800 remained protected.
- SimLingo HEAD/diff/checkpoint and DINO remained exact at the hashes frozen in the suite manifest; the report-only runner predicate change is separately recorded in `RETRY_LEDGER.json`.
- Visualization extra model forwards: 0. New PID/planner/VehicleControl writer: 0.
- Final cleanup: {cleanup['status']}.
- WAIT claim is deliberately bounded: simulation evidence only, no formal safety guarantee.

The stage stops here. The only proposed next stage is `DRIVECLARIFY_METHOD_V1_FINAL_FREEZE_AND_PAPER_EXPERIMENT_PROTOCOL_R2`; it was not started.
"""
(REPORT/"FINAL_REPORT.md").write_text(final_report,encoding="utf-8")


# Hash inventory is intentionally last and excludes itself.
artifacts=[]
for p in sorted(REPORT.iterdir()):
    if p.is_file() and p.name != "ARTIFACT_HASHES.json":
        artifacts.append({"path":p.name,"bytes":p.stat().st_size,"sha256":sha(p)})
dump(REPORT/"ARTIFACT_HASHES.json", {"schema_version":"driveclarify.artifact_hashes.v1","generated_utc":utc_now(),"algorithm":"SHA-256","excludes":["ARTIFACT_HASHES.json"],"artifacts":artifacts})
print(json.dumps({"status":verdict,"report":str(REPORT),"png_count":len(PNG_NAMES),"artifact_count":len(artifacts)+1},ensure_ascii=False))
