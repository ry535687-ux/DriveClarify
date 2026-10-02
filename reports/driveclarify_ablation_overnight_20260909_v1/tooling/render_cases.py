#!/usr/bin/env python3
"""Static phase-labelled figures from saved evidence; no simulator/model/controller.

Run with PYTHONDONTWRITEBYTECODE=1 and the existing SimLingo Python environment.
Only this report's visualization/cases output directory is written.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
import numpy as np

BASE = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from driveclarify_ablation_overnight.normalize import normalize_run

BLUE, ORANGE, INK, GREY = "#3478B7", "#D68032", "#252A30", "#7A828B"
ARMS = ("ABL_FULL", "ABL_TRAJ_ONLY")
COLORS = dict(zip(ARMS, (BLUE, ORANGE)))
STYLES = dict(zip(ARMS, ("-", "--")))
SOURCES = {}
READ_ERRORS = {}


def read(path, lines=False):
    path = Path(path)
    raw = path.read_bytes()
    SOURCES[str(path)] = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return [json.loads(x) for x in raw.splitlines() if x.strip()] if lines else json.loads(raw)


def optional(path, default):
    if not Path(path).is_file():
        return default
    try:
        return read(path)
    except (ValueError, OSError) as exc:
        READ_ERRORS[str(path)] = str(exc)
        return default


def optional_lines(path):
    if not Path(path).is_file():
        return []
    try:
        return read(path, lines=True)
    except (ValueError, OSError) as exc:
        READ_ERRORS[str(path)] = str(exc)
        return []


def phase_label(case):
    return "DEV ONLY" if case["row"]["phase"] == "DEVELOPMENT" else "FORMAL LOG REPLAY"


def yes_no(value):
    return "unknown" if value is None else "yes" if value else "no"


def number(value, digits=3):
    return "unknown" if value is None else ("%.*f" % (digits, value))


def manifest_rows(document):
    rows = document if isinstance(document, list) else document.get("runs", document.get("rows", []))
    output = []
    for source in rows:
        row = dict(source)
        row.setdefault("condition", row.get("condition_id", row.get("template_id")))
        row.setdefault("variant", row.get("configuration_id"))
        phase = row.get("phase", row.get("stage", ""))
        row["phase"] = "DEVELOPMENT" if str(phase).startswith("DEV") else phase
        if row["phase"] not in ("DEVELOPMENT", "FORMAL"):
            raise ValueError("EXPLICIT_DEVELOPMENT_OR_FORMAL_PHASE_REQUIRED")
        if row["variant"] not in ARMS:
            raise ValueError("UNSUPPORTED_ARM_FOR_THIS_RELATION_CASE_RENDERER")
        output.append(row)
    if not output or len({r["run_id"] for r in output}) != len(output):
        raise ValueError("EMPTY_OR_DUPLICATE_RUN_MANIFEST")
    return output


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def style_axes(ax):
    ax.set_facecolor("white")
    ax.grid(True, color="#E4E7EB", linewidth=.6, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(colors=INK, labelsize=9)


def save(fig, out, stem):
    fig.savefig(str(out / (stem + ".png")), dpi=200, facecolor="white")
    fig.savefig(str(out / (stem + ".pdf")), facecolor="white")
    plt.close(fig)


def corners(region):
    theta = math.radians(region["yaw_degrees"])
    x, y = region["center_xyz"][:2]
    return [(x + a * math.cos(theta) - b * math.sin(theta), y + a * math.sin(theta) + b * math.cos(theta))
            for a, b in [(-region["half_length_m"], -region["half_width_m"]),
                         (-region["half_length_m"], region["half_width_m"]),
                         (region["half_length_m"], region["half_width_m"]),
                         (region["half_length_m"], -region["half_width_m"])]]


def load_case(row):
    owner = Path(row["output"]) / "owner_evidence"
    trace = optional_lines(owner / "V2_NATIVE_STATE_TRACE.jsonl")
    terminal = optional(owner / "V2_TRACE_TERMINAL_RECEIPT.json", {})
    raw_trace = (owner / "V2_NATIVE_STATE_TRACE.jsonl").read_bytes() if trace else b""
    trace_verified = bool(trace and hashlib.sha256(raw_trace).hexdigest() == terminal.get("trace_sha256")
                          and terminal.get("destroy_observed") and not terminal.get("trace_errors"))
    controls = optional_lines(owner / "ABL_ACTUAL_CONTROL_TIMELINE.jsonl")
    candidate = optional_lines(owner / "ABL_CANDIDATE_TIMELINE.jsonl")
    decisions = optional_lines(owner / "ABL_DECISION_TIMELINE.jsonl")
    asks = [optional(p, {}) for p in sorted((owner / "oracle_exchange").glob("ask-*.json"))]
    asks = [x for x in asks if x.get("action") == "ASK" and x.get("durable")]
    lifecycle = optional(owner / "RQ3_LIFECYCLE_TIMING_RECEIPT.json", {})
    binding = read(row["task_binding_path"])
    config = read(row["config_path"])
    records = optional(Path(row["output"]) / "official_checkpoint.json", {}).get("_checkpoint", {}).get("records", [])
    official = records[0] if len(records) == 1 else {}
    normalized, index = normalize_run(row)
    for source in index["files"]:
        SOURCES[source["path"]] = source
    # The first recorded batch is used prospectively; never search for a later
    # successful-looking candidate pair or discard an invalid first batch.
    plans = [x.get("timed_trajectory") for x in candidate[:2]]
    plans_verified = bool(len(plans) == 2 and all(p and p.get("valid") for p in plans)
                          and [c.get("candidate_id") for c in candidate[:2]] == ["A", "B"])
    if plans_verified:
        plans_verified = (plans[0]["source_frame"] == plans[1]["source_frame"]
            and plans[0]["nonlanguage_context_sha256"] == plans[1]["nonlanguage_context_sha256"]
            and all(p["coordinate_frame"] == "CARLA_EGO_X_FORWARD_Y_RIGHT_METRES" for p in plans)
            and all(p["relative_times_s"] == [i * .25 for i in range(9)] for p in plans)
            and all(np.asarray(p["xy_m"]).shape == (9, 2) and np.isfinite(p["xy_m"]).all() for p in plans))
    distance = float(np.linalg.norm(np.asarray(plans[0]["xy_m"]) - np.asarray(plans[1]["xy_m"]), axis=1).max()) if plans_verified else None
    return {"row": row, "trace": trace, "controls": controls, "candidates": candidate,
            "plans": plans, "decisions": decisions, "asks": asks, "lifecycle": lifecycle,
            "binding": binding, "config": config, "official": official, "normalized": normalized,
            "origin_s": trace[0]["simulation_time_s"] if trace else None,
            "max_candidate_separation_m": distance, "plans_verified": plans_verified,
            "physical_trace_verified": trace_verified, "raw_log_index": index}


def predecision_separation(first, second):
    initial_distance = math.dist(first["trace"][0]["xyz"], second["trace"][0]["xyz"]) if first["trace"] and second["trace"] else None
    if not all(c["trace"] and c["decisions"] for c in (first, second)):
        return {"initial_xyz_difference_m": initial_distance, "window_end_relative_s": None,
                "maximum_predecision_xy_separation_m": None, "reason": "MISSING_TRACE_OR_FIRST_DECISION"}
    first_decisions = [c["decisions"][0]["simulation_time_s"] - c["origin_s"] for c in (first, second)]
    end = min(first_decisions)
    grid = np.arange(0, end + .0001, .05)
    aligned = []
    for case in (first, second):
        t = np.asarray([r["simulation_time_s"] - case["origin_s"] for r in case["trace"]])
        xy = np.asarray([r["xyz"][:2] for r in case["trace"]])
        aligned.append(np.stack([np.interp(grid, t, xy[:, j]) for j in range(2)], axis=1))
    return {"alignment": "0.05 s grid, piecewise-linear interpolation of actual episode-relative timestamps",
            "window_end_relative_s": end,
            "initial_xyz_difference_m": math.dist(first["trace"][0]["xyz"], second["trace"][0]["xyz"]),
            "maximum_predecision_xy_separation_m": float(np.linalg.norm(aligned[0] - aligned[1], axis=1).max())}


def world_pair(cases, pair_id, out):
    pair = sorted(cases, key=lambda x: ARMS.index(x["row"]["variant"]))
    full, traj = pair
    condition = full["row"]["condition"]
    repeatability = predecision_separation(full, traj)
    fig = plt.figure(figsize=(13.2, 8.2))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.0, 1.48], left=.065, right=.98, bottom=.13, top=.80, wspace=.38)
    axs = [fig.add_subplot(gs[0, i]) for i in range(3)]
    title = {"TASK_CRITICAL": "Critical task: two distinct stop regions", "TASK_EQUIVALENT": "Equivalent task: both interpretations share one stop region"}.get(full["normalized"].get("physical_relation_truth"), "Independent physical relation: unavailable")
    fig.text(.055, .956, phase_label(full) + "  |  " + condition + " paired real-driving trajectories", fontsize=17, weight="bold", color=INK)
    fig.text(.055, .914, title + "  |  one preregistered pair; CARLA Town03 world coordinates in metres", fontsize=11, color=INK)
    fig.text(.055, .874, pair_id + " | Separate episodes; native repeatability must be considered in interpreting path differences.", fontsize=10.0, color=GREY)
    binding = full["binding"]
    truth_id = full["row"]["evaluation_truth_candidate_id"]
    expected = binding["candidate_region_map"][truth_id]
    for ax in axs[:2]:
        style_axes(ax)
        for region in binding["regions"]:
            true = region["region_id"] == expected
            ax.add_patch(Polygon(corners(region), closed=True, facecolor="#E0E3E6" if true else "white",
                                 edgecolor=INK, linewidth=1.1, hatch="///" if true else None, zorder=1))
        for case in pair:
            arm = case["row"]["variant"]
            if not case["trace"]:
                ax.text(.03, .93 - .06 * ARMS.index(arm), arm + ": no recorded trace", transform=ax.transAxes, fontsize=9, color=COLORS[arm])
                continue
            xy = np.asarray([r["xyz"][:2] for r in case["trace"]])
            ax.plot(xy[:, 0], xy[:, 1], STYLES[arm], color=COLORS[arm], lw=1.9, label=arm, zorder=3)
            ax.scatter(*xy[-1], marker="s" if arm == "ABL_FULL" else "X", color=COLORS[arm], s=37, zorder=4)
            if case["decisions"]:
                decision = case["decisions"][0]
                point = min(case["trace"], key=lambda r: abs(r["simulation_time_s"] - decision["simulation_time_s"]))
                ax.scatter(*point["xyz"][:2], marker="D", edgecolor=COLORS[arm], facecolor="white", s=42, zorder=4)
        ax.set_xlabel("World x (m)", fontsize=10)
        ax.set_ylabel("World y (m)", fontsize=10)
        ax.set_aspect("equal", adjustable="box")
    if full["trace"]:
        axs[0].scatter(*full["trace"][0]["xyz"][:2], marker="o", color=INK, s=32, zorder=4)
    complete_points = [point for c in pair for point in [r["xyz"][:2] for r in c["trace"]]]
    complete_points += [point for r in binding["regions"] for point in corners(r)]
    points = np.asarray(complete_points)
    axs[0].set_xlim(points[:, 0].min() - 3, points[:, 0].max() + 3)
    axs[0].set_ylim(points[:, 1].min() - 4, points[:, 1].max() + 4)
    axs[0].set_title("All recorded states", loc="left", fontsize=11, pad=10)
    ys = [r["center_xyz"][1] for r in binding["regions"]]
    region_points = np.asarray([point for r in binding["regions"] for point in corners(r)])
    axs[1].set_xlim(region_points[:, 0].min() - 3, region_points[:, 0].max() + 5)
    axs[1].set_ylim(min(ys) - 13, max(ys) + 13)
    axs[1].set_title("Task-region detail", loc="left", fontsize=11, pad=10)
    for region in binding["regions"]:
        tags = [candidate for candidate, region_id in binding["candidate_region_map"].items() if region_id == region["region_id"]]
        label = "/".join(tags) + ("  [true " + truth_id + "]" if region["region_id"] == expected else "")
        axs[1].text(region_points[:, 0].min() - 2.5, region["center_xyz"][1] + 7.4, label, fontsize=9, weight="bold", color=INK)
    ax = axs[2]
    ax.axis("off")
    handles, labels = axs[0].get_legend_handles_labels()
    ax.legend(handles, labels, loc="upper left", bbox_to_anchor=(-.025, 1.03), frameon=False, fontsize=10)
    lines = ["Preserved outcomes", ""]
    for case in pair:
        norm = case["normalized"]
        inf = case["official"].get("infractions", {})
        collisions = sum(len(inf.get(k, [])) for k in ("collisions_layout", "collisions_pedestrian", "collisions_vehicle")) if inf else None
        lines += [case["row"]["variant"],
                  "Status: " + norm["status"],
                  "Relation: " + (case["decisions"][0]["relation"] if case["decisions"] else "not observed"),
                  "ASK requested / emitted: %s / %s" % (norm["ask_requested_count"], norm["ask_emitted_count"]),
                  "Correct local stop / full task: %s / %s" % (yes_no(norm["task_outcome"]["correct_local_task_obligation"]), yes_no(norm["language_task_complete"])),
                  "Native route completion: %s%%" % number(case["official"].get("scores", {}).get("score_route"), 0),
                  "Recorded collisions: %s" % number(collisions, 0), ""]
    lines += ["Initial ego separation: %s m" % number(repeatability["initial_xyz_difference_m"], 6),
              "Pre-decision max separation: %s m" % number(repeatability["maximum_predecision_xy_separation_m"]),
              "(common prefix through %s sim-s)" % number(repeatability["window_end_relative_s"], 2), "",
              "Hatched bay: offline passenger truth " + truth_id + ".",
              "Diamond: first relation decision.",
              "Square / X: last recorded state."]
    ax.text(0, .89, "\n".join(lines), transform=ax.transAxes, va="top", fontsize=9.4, linespacing=1.30, color=INK)
    partial = [c for c in pair if c["normalized"]["status"] == "TECHNICAL_INTERRUPTION"]
    preservation_note = "Every manifest pair is displayed without outcome selection. DEV remains DEV; formal performance comes from the frozen statistics."
    if partial:
        preservation_note = "Technical interruption retained: " + "; ".join(c["row"]["variant"] + " has %s saved states / %s native control returns; full outcome remains unknown." % (len(c["trace"]), len(c["controls"])) for c in partial)
    fig.text(.055, .060, preservation_note, fontsize=9.8, color=INK)
    fig.text(.055, .032, "Reconstructed from saved state logs, not a recorded RGB video. Post-decision differences include policy effects and native noise.", fontsize=9.5, color=GREY)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", pair_id) + "_world_xy_pair"
    save(fig, out, stem)
    return stem, repeatability


def events(case):
    origin = case["origin_s"]
    trace = case["trace"]
    rows = []
    if origin is None:
        return rows
    if case["plans_verified"] and case["plans"][0].get("source_time_s") is not None:
        rows.append((6, case["plans"][0]["source_time_s"] - origin, "cached sensor context"))
    for decision in case["decisions"]:
        when = decision.get("simulation_time_s")
        if when is None:
            continue
        rows.append((5, when - origin, decision.get("relation", "UNKNOWN").replace("TASK_", "")))
        if decision.get("requested_action_after_joint_timing") == "ASK":
            rows.append((4, when - origin, "request"))
    for ask in case["asks"]:
        try:
            frame = int(ask["observation_id"].replace("v11-frame-", ""))
        except (ValueError, KeyError, AttributeError):
            continue
        state = next((r for r in trace if r["frame"] == frame), None)
        if state is not None:
            rows.append((3, state["simulation_time_s"] - origin, "durable receipt"))
    answer = case["lifecycle"].get("answer")
    if answer and answer.get("simulation_time_s") is not None:
        rows.append((2, answer["simulation_time_s"] - origin, "bound answer"))
    replan = case["lifecycle"].get("full_replan")
    if replan and replan.get("committed") and replan.get("completed_simulation_time_s") is not None:
        t = replan["completed_simulation_time_s"]
        rows.append((1, t - origin, "route " + str(replan.get("selected_candidate_id"))))
        control = next((r for r in case["controls"] if r["simulation_time_s"] >= t - 1e-8), None)
        if control:
            rows.append((0, control["simulation_time_s"] - origin,
                         "steer %.2f | gas %.2f | brake %.2f" % (control["steer"], control["throttle"], control["brake"])))
    return rows


def candidate_and_event_figure(cases, out, stem):
    ordered = sorted(cases, key=lambda c: (c["row"]["pair_id"], ARMS.index(c["row"]["variant"])))
    count = len(ordered)
    fig, axes = plt.subplots(count, 2, figsize=(16.5, 9.5 if count < 4 else 3.6 * count + 1.1), squeeze=False,
                             gridspec_kw={"width_ratios": [1.0, 1.3]})
    fig.subplots_adjust(top=.80 if count < 4 else .90, bottom=.145 if count < 4 else .080,
                        left=.075, right=.97, hspace=.76, wspace=.47)
    phases = {c["row"]["phase"] for c in ordered}
    tag = phase_label(ordered[0]) if len(phases) == 1 else "PHASE LABELS PER CASE"
    fig.text(.055, .95 if count < 4 else .973, tag + "  |  Candidate plans and actual execution events", fontsize=19, weight="bold", color=INK)
    fig.text(.055, .917 if count < 4 else .946,
             "%s manifest cases shown. Saved-log reconstruction; rendering adds no model, PID, planner or simulator calls." % count,
             fontsize=10.8, color=INK)
    fig.text(.055, .887 if count < 4 else .922,
             "Left: same-context candidate predictions, 0–2 s at 0.25 s intervals. Right: recorded episode-relative simulation time.",
             fontsize=10.5, color=GREY)
    all_events = [events(c) for c in ordered]
    event_times = [t for rows in all_events for _, t, _ in rows]
    lo = min(event_times) - .10 if event_times else 0
    hi = max(event_times) + .75 if event_times else 1
    all_xy = [p["xy_m"] for c in ordered if c["plans_verified"] for p in c["plans"]]
    xy_all = np.concatenate([np.asarray(points) for points in all_xy], axis=0) if all_xy else np.asarray([[0, 0], [1, 1]])
    x_bounds = (min(0, xy_all[:, 0].min()) - .7, xy_all[:, 0].max() + .9)
    y_bounds = (min(-.5, xy_all[:, 1].min()) - .7, max(.5, xy_all[:, 1].max()) + .6)
    timeline_labels = ["Post-install control", "Replan installed", "Answer received", "ASK emitted", "ASK requested", "Relation", "Candidate context"]
    exported = []
    for i, case in enumerate(ordered):
        ax, timeline = axes[i]
        style_axes(ax)
        if case["plans_verified"]:
            for j, plan in enumerate(case["plans"]):
                xy = np.asarray(plan["xy_m"])
                ax.plot(xy[:, 0], xy[:, 1], color=(BLUE, ORANGE)[j], linestyle=("-", "--")[j],
                        marker=("o", "x")[j], markerfacecolor="white", markersize=4.5, linewidth=1.6,
                        label="Candidate " + ("A", "B")[j])
            ax.legend(loc="upper right", frameon=False, fontsize=9)
            annotation = "Source frame %s | same-context hash: match\n9 points per candidate" % case["plans"][0]["source_frame"]
        else:
            ax.text(.04, .50, "UNKNOWN\nNo valid first same-context candidate batch", transform=ax.transAxes,
                    fontsize=10, color=GREY, va="center")
            annotation = "Missing / invalid first batch preserved\nNo later batch substitution"
        ax.set_xlim(*x_bounds)
        ax.set_ylim(*y_bounds)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("Ego x forward (m)", fontsize=10)
        ax.set_ylabel("Ego y right (m)", fontsize=10)
        label = case["row"]["condition"] + "  |  " + case["row"]["variant"] + "  |  " + phase_label(case)
        ax.set_title(label + "\nMax aligned A–B separation: %s m" % number(case["max_candidate_separation_m"]),
                     loc="left", fontsize=10.2, pad=12)
        ax.text(.015, .025, annotation, transform=ax.transAxes, fontsize=7.8, color=GREY, va="bottom")
        style_axes(timeline)
        timeline.set_yticks(range(7))
        timeline.set_yticklabels(timeline_labels, fontsize=9.5)
        timeline.set_ylim(-.65, 6.65)
        timeline.set_xlim(lo, hi)
        timeline.set_xlabel("Seconds since first recorded native state (simulation clock)", fontsize=9.5)
        timeline.set_title(case["row"]["pair_id"] + " | " + case["normalized"]["status"] + "\n%s native control returns recorded; first after install plotted" % len(case["controls"]), loc="left", fontsize=9.3, pad=12)
        rows = all_events[i]
        occupied = {y for y, _, _ in rows}
        for y, t, detail in rows:
            timeline.scatter(t, y, s=42, marker="o", color=COLORS[case["row"]["variant"]], zorder=4)
            timeline.text(t + .025, y + .08, "%.2f s  %s" % (t, detail), fontsize=8.5, va="center", color=INK)
        for y in range(7):
            if y not in occupied:
                timeline.text(lo + .025, y, "not observed / missing time / not emitted", fontsize=8.3, va="center", color=GREY)
        exported.append({"run_id": case["row"]["run_id"], "phase": case["row"]["phase"],
                         "events": [{"event": timeline_labels[y], "simulation_s_relative": t, "detail": detail} for y, t, detail in rows]})
    fig.text(.055, .038 if count < 4 else .032,
             "Decisions: relation/request; receipts: emitted ASK; lifecycle: answer/replan. Only first post-install control is plotted; missing times are unknown.",
             fontsize=9.6, color=INK)
    fig.text(.055, .017, "These are log-replay figures, not recorded RGB video. DEV remains DEV; formal estimates use the frozen statistics.", fontsize=9.6, color=GREY)
    save(fig, out, stem)
    return exported


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=BASE / "development/DEV_COMPARISON_MANIFEST.json")
    parser.add_argument("--output", type=Path, required=True, help="New directory beneath this report's visualization/cases")
    args = parser.parse_args()
    allowed = (BASE / "visualization/cases").resolve()
    if allowed not in args.output.resolve().parents:
        raise ValueError("OUTPUT_MUST_BE_A_NEW_SUBDIRECTORY_OF_REPORT_VISUALIZATION_CASES")
    document = read(args.manifest)
    rows = manifest_rows(document)
    groups = defaultdict(list)
    for row in rows:
        groups[(row["phase"], row["pair_id"])].append(row)
    for key, pair in groups.items():
        if len(pair) != 2 or {r["variant"] for r in pair} != set(ARMS):
            raise ValueError("PAIRED_CASE_FIGURES_REQUIRE_BOTH_MANIFEST_ARMS: %s; no rows were omitted" % (key,))
        if len({r["condition"] for r in pair}) != 1 or len({r["seed"] for r in pair}) != 1:
            raise ValueError("PAIR_CONDITION_OR_SEED_MISMATCH: %s" % (key,))
        if len({r["evaluation_truth_candidate_id"] for r in pair}) != 1:
            raise ValueError("PAIR_PRIVATE_TRUTH_MISMATCH: %s" % (key,))
    args.output.mkdir(parents=True, exist_ok=False)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": INK, "pdf.fonttype": 42, "ps.fonttype": 42})
    contract = {"scope": "EVERY_RUN_AND_PAIR_IN_SUPPLIED_MANIFEST_WITH_EXPLICIT_PHASE",
        "question": "How do saved candidate predictions, relation decisions and actual driving compare within every planned pair?",
        "takeaway": "Mechanism differences and preserved failures are displayed without attributing all path differences to policy.",
        "renderer": "matplotlib static PNG and PDF; saved-log reconstruction, not recorded RGB video",
        "families": ["spatial line trajectories", "faceted timed candidate paths", "event dot timeline"],
        "grain": "one independently driven episode; points are not independent experimental samples",
        "expected_cases": len(rows), "expected_pairs": len(groups), "phases": sorted({r["phase"] for r in rows}),
        "expected_points_in_valid_candidate_batch": 18, "invalid_or_missing_candidates": "UNKNOWN; no later-batch selection",
        "palette_policy": "two roots: blue and orange, neutral geometry/ground-truth hatch",
        "noncolor_encodings": "solid/dashed line, open-circle/x marker, region hatch, explicit labels",
        "truth_scope": "offline private candidate A or B maps to independently authored public stop region",
        "causal_limitation": "same initial pose and paired seed do not remove native repeatability noise"}
    dump(args.output / "CHART_CONTRACT.json", contract)
    scorer_paths = [ROOT / "driveclarify_ablation_overnight/normalize.py", ROOT / "driveclarify_ablation_overnight/task_outcome.py", ROOT / "driveclarify_rq3_paired_v2/task_evaluator.py"]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in scorer_paths}
    cases = [load_case(row) for row in rows]
    grouped_cases = defaultdict(list)
    for case in cases:
        grouped_cases[(case["row"]["phase"], case["row"]["pair_id"])].append(case)
    pair_metadata, figures = {}, []
    for (phase, pair_id), pair in grouped_cases.items():
        stem, metadata = world_pair(pair, pair_id, args.output)
        pair_metadata[phase + "/" + pair_id] = metadata
        figures.append({"stem": stem, "kind": "paired_world_trajectories", "phase": phase,
                        "pair_id": pair_id, "runs": [c["row"]["run_id"] for c in pair]})
    timeline = []
    if len(cases) <= 4 and len({r["phase"] for r in rows}) == 1:
        tag = "DEV" if rows[0]["phase"] == "DEVELOPMENT" else "FORMAL_LOG_REPLAY"
        stem = tag + "_all_manifest_cases_candidates_and_events"
        timeline += candidate_and_event_figure(cases, args.output, stem)
        figures.append({"stem": stem, "kind": "candidates_and_events", "phase": rows[0]["phase"], "runs": [r["run_id"] for r in rows]})
    else:
        for (phase, pair_id), pair in grouped_cases.items():
            stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", pair_id) + "_candidates_and_events"
            timeline += candidate_and_event_figure(pair, args.output, stem)
            figures.append({"stem": stem, "kind": "candidates_and_events", "phase": phase,
                            "pair_id": pair_id, "runs": [c["row"]["run_id"] for c in pair]})
    dump(args.output / "PLOT_DATA.json", {"cases": [{"run_id": c["row"]["run_id"], "condition": c["row"]["condition"],
          "phase": c["row"]["phase"], "pair_id": c["row"]["pair_id"], "status": c["normalized"]["status"],
          "configuration_id": c["row"]["variant"], "trajectory": c["trace"], "plans": c["plans"],
          "evaluation_regions": c["binding"], "evaluation_truth_candidate_id": c["row"]["evaluation_truth_candidate_id"],
          "plans_verified": c["plans_verified"], "physical_trace_verified": c["physical_trace_verified"],
          "max_candidate_separation_m": c["max_candidate_separation_m"]} for c in cases],
          "timelines": timeline, "paired_initial_and_predecision_comparison": pair_metadata})
    dump(args.output / "RAW_LOG_INDEX.json", {"manifest": str(args.manifest), "runs": [
        {"run_id": c["row"]["run_id"], "pair_id": c["row"]["pair_id"], "phase": c["row"]["phase"],
         "source_output": c["row"]["output"], "normalized_status": c["normalized"]["status"],
         "raw_log_index": c["raw_log_index"]} for c in cases]})
    summary = []
    for c in cases:
        n = c["normalized"]
        summary.append({"run_id": c["row"]["run_id"], "pair_id": c["row"]["pair_id"], "phase": c["row"]["phase"],
            "condition": c["row"]["condition"], "arm": c["row"]["variant"], "status": n["status"],
            "relation": c["decisions"][0].get("relation", "UNKNOWN") if c["decisions"] else None,
            "max_candidate_separation_m": c["max_candidate_separation_m"],
            "ask_requested": n["ask_requested_count"], "ask_emitted": n["ask_emitted_count"],
            "correct_local_stop": n["task_outcome"]["correct_local_task_obligation"], "correct_full_task": n["language_task_complete"],
            "native_route_complete": n["native_route_complete"], "collision": n["collision"], "source_output": c["row"]["output"],
            "prior_technical_attempt": c["row"].get("prior_technical_attempt")})
    with (args.output / "CASE_SUMMARY.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0])); writer.writeheader(); writer.writerows(summary)
    notes = ["# 全 manifest 真实案例日志重放图", "", "逐一展示输入 manifest 的全部 %s 个运行、%s 个配对，不按结果挑选。DEV 图片永久标为 DEV；正式图标为 FORMAL LOG REPLAY，统计推断以冻结统计表为准。这里是日志重放图，不是事前保存的 RGB 视频。" % (len(rows), len(groups)), ""]
    for f in figures:
        label = f["phase"] + " | " + f.get("pair_id", "all manifest cases") + " | " + f["kind"]
        notes.append("- [%s](%s.png)（[PDF](%s.pdf)）。" % (label, f["stem"], f["stem"]))
    notes += ["", "停车区来自独立公共物理绑定；灰色斜线标记该配对离线私人意图对应区域（A 或 B，逐对读取）。世界图保持 x/y 等比例，候选图为本车 x 向前、y 向右、米单位。候选只用首个保存批次的 pred_speed_wps 时间化输出：0–2 秒、0.25 秒间隔。无效或缺失批次为 UNKNOWN，不查找后续成功批次。", "",
              "时间线原点为本 episode 首条 native state。ASK requested 与 durable emitted 分列；无时间证据不推测时点。无 ASK 的 ACT 路线安装也可能出现，不能叫回答后重规划。实际控制点来自安装后返回的既有控制日志。", "",
              "| Run ID | Phase | Status | Arm | Relation | Requested / emitted ASK | Local stop / full task |",
              "|---|---|---|---|---|---:|---|"]
    for r in summary:
        notes.append("| [%s](%s) | %s | %s | %s | %s | %s / %s | %s / %s |" %
                     (r["run_id"], r["source_output"], r["phase"], r["status"], r["arm"], r["relation"], r["ask_requested"], r["ask_emitted"], r["correct_local_stop"], r["correct_full_task"]))
    notes += ["", "共享初态和种子不保证原生仿真/推理完全重复；首次决策前的轨迹差异单独测量。后续差异混合策略效应和重复性噪声，图不作纯因果归因。对齐方法和逐帧来源保留在 PLOT_DATA.json。", "",
              "各 manifest 已记录的 prior_technical_attempt 原样保留在 CASE_SUMMARY.csv；原始失败记录没有替换或删除。未启动、技术中断或未完成的本行仍出图，缺失部分明确占位。", "",
              "工具只读原日志并调用 CPU matplotlib 与已有独立离线 evaluator，无模型、PID、planner、CARLA 调用或 control 写入。SOURCE_HASHES.json 保存全部来源摘要，RAW_LOG_INDEX.json 逐实例索引原始日志，render_cases_source.py 保存本次实际绘图源码。"]
    (args.output / "真实case索引.md").write_text("\n".join(notes) + "\n")
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in scorer_paths}
    for p in scorer_paths:
        SOURCES[str(p)] = {"path": str(p), "sha256": before[str(p)], "bytes": p.stat().st_size}
    script = Path(__file__)
    source = script.read_bytes()
    (args.output / "render_cases_source.py").write_bytes(source)
    SOURCES[str(script)] = {"path": str(script), "sha256": hashlib.sha256(source).hexdigest(), "bytes": len(source)}
    dump(args.output / "SOURCE_HASHES.json", {"manifest": str(args.manifest), "sources": list(SOURCES.values()),
         "source_mutations": 0, "model_calls": 0, "PID_calls": 0, "planner_calls": 0, "CARLA_calls": 0,
         "scorer_hashes_unchanged": True, "all_manifest_cases_displayed": True, "optional_log_read_errors": READ_ERRORS})
    dump(args.output / "FIGURE_INDEX.json", figures)
    dump(args.output / "STRUCTURAL_QA.json", {"case_count": len(cases), "pair_count": len(groups),
         "png_count": len(list(args.output.glob("*.png"))), "pdf_count": len(list(args.output.glob("*.pdf"))),
         "all_manifest_run_ids_displayed": {r["run_id"] for r in rows} == {r["run_id"] for r in summary},
         "valid_same_context_candidate_case_count": sum(c["plans_verified"] for c in cases),
         "unknown_candidate_case_count": sum(not c["plans_verified"] for c in cases),
         "verified_complete_physical_trace_count": sum(c["physical_trace_verified"] for c in cases),
         "phases": sorted({r["phase"] for r in rows}), "visual_inspection": "PENDING"})
    print(json.dumps({"output": str(args.output), "cases": len(cases), "pairs": len(groups), "figures": len(figures), "source_count": len(SOURCES)}))


if __name__ == "__main__":
    main()
