"""Prospective episode-level ablation summaries; never consumes frame labels as n.

Inputs are normalized, source-backed terminal records and the original planned
matrix. Missing records remain visible. This module does not adjudicate driving
truth, generate seeds, dispatch runs, or mutate its inputs. Python >= 3.8; stdlib.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import random
from collections import Counter, defaultdict


SCHEMA = "driveclarify.overnight.statistics.v1"
EXPERIMENT_ARMS = {
    "ABL_TASK_RELATION": ("ABL_FULL", "ABL_TRAJ_ONLY"),
    "ABL_ACTIVE_WAIT": ("ABL_FULL", "ABL_NO_LOOKAHEAD"),
}
IDENTITY = (
    "experiment_id", "pair_id", "run_id", "template_id", "condition_id", "seed",
    "configuration_id", "phase", "protocol_id", "order_in_pair",
)
SAFETY = ("collision", "offroad", "wrong_lane", "traffic_violation")
BOOL_METRICS = (
    "language_task_complete", "correct_safe_complete", "wrong_target_execution",
    "native_route_complete", *SAFETY, "offroad_or_wrong_lane", "timeout", "nonprogress", "fallback",
    "unnecessary_question", "timely_question", "missed_question",
    "critical_episode_no_question", "late_question", "missed_window",
)
NUMERIC_METRICS = (
    "ask_requested_count", "ask_emitted_count", "answer_received_count",
    "fresh_replan_count", "actual_control_count", "relation_equivalent_count",
    "relation_divergent_count", "relation_unknown_count",
    "preask_active_wait_sim_s", "postask_answer_wait_sim_s",
    "first_evidence_sufficient_sim_s", "ask_remaining_margin_sim_s",
    "answer_remaining_margin_sim_s", "runtime_wall_s", "runtime_sim_s",
    "model_forward_count", "candidate_forward_count", "diagnostic_forward_count",
    "peak_gpu_memory_mib", "peak_rss_mib", "artifact_bytes",
)
RISK_METRICS = {
    "wrong_target_execution", *SAFETY, "offroad_or_wrong_lane", "timeout", "nonprogress", "fallback",
    "unnecessary_question", "missed_question", "late_question", "missed_window",
    "critical_episode_no_question",
}
PARTIAL_RUN_COST_METRICS = {
    "runtime_wall_s", "runtime_sim_s", "model_forward_count", "candidate_forward_count",
    "diagnostic_forward_count", "peak_gpu_memory_mib", "peak_rss_mib", "artifact_bytes",
}
SUCCESS_METRICS = {
    "language_task_complete", "correct_safe_complete", "native_route_complete",
    "timely_question",
}
STATES = {
    "PLANNED", "PRE_AGENT_FAILED", "RUNNING", "COMPLETE", "DRIVING_FAILURE",
    "TECHNICAL_INTERRUPTION",
}
META = (
    "status", "started", "agent_exposed", "attempt_count", "terminal_complete",
    "driving_failure", "technical_interruption", "termination_reason",
    "primary_missing_reason", "config_sha256", "config_identity_basis", "code_sha256", "checkpoint_id",
    "adapter_sha256", "scene_sha256", "scorer_sha256", "log_index",
    "terminal_record_source", "task_judgment_source", "safety_judgment_source",
    "missing_reasons_json", "attempts_json",
)
EPISODE_COLUMNS = (*IDENTITY, *META, *BOOL_METRICS, *NUMERIC_METRICS)


def _bool(value, name):
    if value is None or type(value) is bool:
        return value
    raise ValueError("%s must be a JSON boolean or null" % name)


def _number(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("%s must be a finite JSON number or null" % name)
    if name.endswith("_count") or name == "artifact_bytes":
        if int(value) != value or value < 0:
            raise ValueError("%s must be a nonnegative integer" % name)
    elif "margin" not in name and value < 0:
        raise ValueError("%s cannot be negative" % name)
    return value


def _validate_manifest(rows):
    pairs = defaultdict(list)
    keys = set()
    seen_runs = {}
    for original in rows:
        row = dict(original)
        missing = [k for k in IDENTITY if k not in row or row[k] in (None, "")]
        if missing:
            raise ValueError("manifest identity missing: " + ",".join(missing))
        if row["experiment_id"] not in EXPERIMENT_ARMS:
            raise ValueError("unsupported experiment: " + str(row["experiment_id"]))
        if row["configuration_id"] not in EXPERIMENT_ARMS[row["experiment_id"]]:
            raise ValueError("configuration does not belong to experiment")
        if row["phase"] not in ("DEVELOPMENT", "FORMAL"):
            raise ValueError("phase must be DEVELOPMENT or FORMAL")
        if type(row["order_in_pair"]) is not int or row["order_in_pair"] not in (1, 2):
            raise ValueError("order_in_pair must be 1 or 2")
        if type(row["seed"]) is not int:
            raise ValueError("seed must be an integer")
        key = (row["experiment_id"], row["pair_id"], row["configuration_id"])
        if key in keys:
            raise ValueError("duplicate planned arm: " + str(key))
        keys.add(key)
        # Full can be shared across experiments only with explicit frozen proof.
        previous = seen_runs.get(row["run_id"])
        if previous:
            common = ("template_id", "condition_id", "seed", "configuration_id",
                      "phase", "protocol_id", "shared_run_contract_sha256")
            if (row["configuration_id"] != "ABL_FULL"
                    or row["experiment_id"] == previous["experiment_id"]
                    or not row.get("shared_run_contract_sha256")
                    or any(row.get(k) != previous.get(k) for k in common)):
                raise ValueError("unlicensed or inconsistent reused run_id")
        seen_runs[row["run_id"]] = row
        pairs[(row["experiment_id"], row["pair_id"])].append(row)
    for key, arms in pairs.items():
        if len(arms) == 1 and arms[0]["phase"] == "DEVELOPMENT":
            # Engineering probes need not invent a counterpart that was never planned.
            continue
        if len(arms) != 2 or {r["configuration_id"] for r in arms} != set(EXPERIMENT_ARMS[key[0]]):
            raise ValueError("planned pair must contain exactly both arms: " + str(key))
        common = ("template_id", "condition_id", "seed", "phase", "protocol_id")
        if any(arms[0][k] != arms[1][k] for k in common):
            raise ValueError("planned pair identity differs: " + str(key))
        if {r["order_in_pair"] for r in arms} != {1, 2}:
            raise ValueError("planned pair must freeze both order positions")
    return list(rows)


def _validate_attempts(record):
    attempts = record.get("attempts", [])
    if not isinstance(attempts, list) or len(attempts) > 3:
        raise ValueError("attempts must be a list of at most 3 pre-agent starts")
    exposed_seen = False
    for index, attempt in enumerate(attempts):
        if type(attempt.get("agent_exposed")) is not bool:
            raise ValueError("each attempt requires boolean agent_exposed")
        if exposed_seen:
            raise ValueError("a run cannot retry after agent exposure")
        if index and not attempts[index - 1].get("infrastructure_failure_evidence"):
            raise ValueError("pre-agent retry requires preceding failure evidence")
        exposed_seen = attempt["agent_exposed"]
    if attempts and any(a["agent_exposed"] for a in attempts) != record.get("agent_exposed", False):
        raise ValueError("attempt exposure disagrees with result")
    return attempts


def normalize_episodes(manifest, results):
    """One row per planned experiment arm. An absent result is still PLANNED.

    Terminal TECHNICAL_INTERRUPTION leaves primary outcome null, even when some
    safety incidents were observed. Those observed incidents remain in columns.
    Driving failures with complete termination always stay in the primary n.
    """
    manifest = _validate_manifest(manifest)
    by_run = {}
    for result in results:
        run_id = result.get("run_id")
        if not run_id or run_id in by_run:
            raise ValueError("missing or duplicate terminal run_id; cannot select best rerun")
        by_run[run_id] = result
    if set(by_run) - {r["run_id"] for r in manifest}:
        raise ValueError("results contain runs outside frozen manifest")
    normalized = []
    for planned in manifest:
        result = by_run.get(planned["run_id"], {})
        for key in IDENTITY:
            # A licensed shared run has experiment-specific membership only in manifest.
            if key in result and key not in ("experiment_id", "pair_id", "order_in_pair"):
                if result[key] != planned[key]:
                    raise ValueError("result identity mismatch for " + key)
        row = {k: planned[k] for k in IDENTITY}
        row.update({k: result.get(k) for k in META})
        row["status"] = result.get("status", "PLANNED")
        if row["status"] not in STATES:
            raise ValueError("unknown terminal status")
        row["started"] = result.get("started", bool(result))
        row["agent_exposed"] = result.get("agent_exposed", False)
        _bool(row["started"], "started")
        _bool(row["agent_exposed"], "agent_exposed")
        row["terminal_complete"] = row["status"] in ("COMPLETE", "DRIVING_FAILURE")
        row["driving_failure"] = row["status"] == "DRIVING_FAILURE"
        row["technical_interruption"] = row["status"] in ("PRE_AGENT_FAILED", "TECHNICAL_INTERRUPTION")
        if row["agent_exposed"] and not row["started"]:
            raise ValueError("exposure requires start")
        if row["terminal_complete"] and not row["agent_exposed"]:
            raise ValueError("driving terminal outcome requires agent exposure")
        if row["status"] in ("PLANNED", "PRE_AGENT_FAILED") and row["agent_exposed"]:
            raise ValueError("pre-agent state cannot hide exposure")
        attempts = _validate_attempts(result)
        row["attempt_count"] = len(attempts) if attempts else (1 if row["started"] else 0)
        row["attempts_json"] = json.dumps(attempts, sort_keys=True)
        missing = result.get("missing_reasons", {})
        if not isinstance(missing, dict):
            raise ValueError("missing_reasons must map field to reason")
        row["missing_reasons_json"] = json.dumps(missing, sort_keys=True)
        for name in BOOL_METRICS:
            row[name] = _bool(result.get(name), name)
        for name in NUMERIC_METRICS:
            row[name] = _number(result.get(name), name)
        # Primary derived only here from independent adjudication and safety.
        reason = None
        if not row["terminal_complete"]:
            primary = None
            reason = "NO_COMPLETE_TERMINAL_OUTCOME:" + row["status"]
        elif row["driving_failure"]:
            primary = False
        elif row["language_task_complete"] is False or any(row[k] is True for k in (*SAFETY, "offroad_or_wrong_lane")):
            primary = False
        elif (row["language_task_complete"] is None or row["collision"] is None
              or row["traffic_violation"] is None
              or (row["offroad_or_wrong_lane"] is not False
                  and (row["offroad"] is None or row["wrong_lane"] is None))):
            primary = None
            reason = "INDEPENDENT_TASK_OR_SAFETY_JUDGMENT_MISSING"
        else:
            primary = True
        if row["offroad_or_wrong_lane"] is False and any(row[k] is True for k in ("offroad", "wrong_lane")):
            raise ValueError("roadway safety composite contradicts component endpoint")
        if row["offroad_or_wrong_lane"] is True and row["offroad"] is False and row["wrong_lane"] is False:
            raise ValueError("roadway safety composite contradicts both negative components")
        if "correct_safe_complete" in result and result["correct_safe_complete"] != primary:
            raise ValueError("supplied primary conflicts with independent task and safety outcome")
        row["correct_safe_complete"] = primary
        row["primary_missing_reason"] = reason
        if row["driving_failure"] and row["language_task_complete"] is None:
            # Do not invent task labels; primary 0 retains this failure in n.
            row["primary_missing_reason"] = None
        normalized.append(row)
    return normalized


def paired_rows(episodes):
    groups = defaultdict(dict)
    for episode in episodes:
        groups[(episode["experiment_id"], episode["pair_id"])][episode["configuration_id"]] = episode
    pairs = []
    for (experiment, pair_id), arms in sorted(groups.items()):
        full_arm, comparator_arm = EXPERIMENT_ARMS[experiment]
        def unplanned(arm):
            return {"run_id": None, "configuration_id": arm, "status": "NOT_PLANNED",
                    "terminal_complete": False, "primary_missing_reason": "COUNTERPART_NOT_PLANNED_DEVELOPMENT_PROBE",
                    **{k: None for k in (*BOOL_METRICS, *NUMERIC_METRICS)}}
        full = arms.get(full_arm, unplanned(full_arm))
        comparator = arms.get(comparator_arm, unplanned(comparator_arm))
        reference = next(iter(arms.values()))
        out = {k: reference[k] for k in ("experiment_id", "pair_id", "template_id", "condition_id", "seed", "phase", "protocol_id")}
        out.update(full_run_id=full["run_id"], comparator_run_id=comparator["run_id"],
                   comparator_id=comparator["configuration_id"], full_status=full["status"],
                   comparator_status=comparator["status"],
                   pair_planning_complete=len(arms) == 2,
                   terminal_pair_complete=full["terminal_complete"] and comparator["terminal_complete"])
        for metric in (*BOOL_METRICS, *NUMERIC_METRICS):
            a, b = full[metric], comparator[metric]
            out[metric + "_full"] = a
            out[metric + "_comparator"] = b
            eligible = (metric in PARTIAL_RUN_COST_METRICS or out["terminal_pair_complete"])
            out[metric + "_difference_full_minus_comparator"] = (float(a) - float(b) if eligible and a is not None and b is not None else None)
        missing = [a["configuration_id"] + ":" + (a["primary_missing_reason"] or "UNKNOWN")
                   for a in (full, comparator) if a["correct_safe_complete"] is None]
        out["primary_pair_complete"] = not missing
        out["incomplete_pair_reason"] = ";".join(missing) or None
        pairs.append(out)
    return pairs


def _percentile(values, probability):
    ordered = sorted(values)
    rank = probability * (len(ordered) - 1)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def clustered_estimate(rows, value_key, repetitions=5000, seed=20260909):
    """Pairs/episodes are nested intact within resampled template clusters.

    The estimand is the arithmetic mean over observed eligible units, not an
    equal-template mean. A resampled template brings every eligible unit with
    it, preserving within-template correlation and the actual pair counts.
    """
    if type(repetitions) is not int or repetitions < 100:
        raise ValueError("bootstrap repetitions must be at least 100")
    clusters = defaultdict(list)
    for row in rows:
        if row[value_key] is not None:
            clusters[row["template_id"]].append(float(row[value_key]))
    values = [v for cluster in clusters.values() for v in cluster]
    n, k = len(values), len(clusters)
    estimate = sum(values) / n if n else None
    output = {"estimate": estimate, "n": n, "template_count": k,
              "ci95_low": None, "ci95_high": None,
              "ci_method": "template-cluster-percentile-bootstrap",
              "bootstrap_repetitions": repetitions, "bootstrap_seed": seed,
              "warning": "NO_ELIGIBLE_UNITS" if not n else "FEWER_THAN_TWO_TEMPLATES_NO_INTERVAL" if k < 2 else "SMALL_TEMPLATE_COUNT_INTERVAL_UNSTABLE" if k < 8 else None}
    if k >= 2:
        rng = random.Random(seed)
        sums_sizes = [(sum(v), len(v)) for v in clusters.values()]
        samples = []
        for _ in range(repetitions):
            sampled = [rng.choice(sums_sizes) for _ in range(k)]
            samples.append(sum(s for s, _ in sampled) / sum(c for _, c in sampled))
        output["ci95_low"] = _percentile(samples, .025)
        output["ci95_high"] = _percentile(samples, .975)
    return output


def _direction(metric):
    if metric in SUCCESS_METRICS:
        return "POSITIVE_FAVORS_ABL_FULL"
    if metric in RISK_METRICS:
        return "POSITIVE_MEANS_MORE_RISK_WITH_ABL_FULL"
    return "DESCRIPTIVE_NO_GLOBAL_FAVORABLE_DIRECTION"


def summarize(episodes, pairs, repetitions=5000, seed=20260909):
    output = {"schema": SCHEMA, "primary_metric": "correct_safe_complete",
              "difference_orientation": "ABL_FULL_MINUS_COMPARATOR",
              "independent_unit": "EPISODE_AND_MATCHED_PAIR_NOT_FRAME",
              "unique_planned_run_count": len({r["run_id"] for r in episodes}),
              "experiment_arm_membership_count": len(episodes), "groups": []}
    group_keys = sorted({(r["experiment_id"], r["phase"], r["protocol_id"]) for r in episodes})
    for experiment, phase, protocol in group_keys:
        rr = [r for r in episodes if (r["experiment_id"], r["phase"], r["protocol_id"]) == (experiment, phase, protocol)]
        pp = [p for p in pairs if (p["experiment_id"], p["phase"], p["protocol_id"]) == (experiment, phase, protocol)]
        g = {"experiment_id": experiment, "phase": phase, "protocol_id": protocol,
             "planned_pair_count": sum(p["pair_planning_complete"] for p in pp),
             "unpaired_development_probe_count": sum(not p["pair_planning_complete"] for p in pp),
             "complete_terminal_pairs": sum(p["terminal_pair_complete"] for p in pp),
             "primary_complete_pairs": sum(p["primary_pair_complete"] for p in pp),
             "incomplete_primary_pair_ids": [p["pair_id"] for p in pp if not p["primary_pair_complete"]],
             "arms": {}, "paired_effects": {}}
        for arm in EXPERIMENT_ARMS[experiment]:
            aa = [r for r in rr if r["configuration_id"] == arm]
            primary = [r["correct_safe_complete"] for r in aa if r["correct_safe_complete"] is not None]
            missing_n = len(aa) - len(primary)
            arm_summary = {
                "planned": len(aa), "started": sum(r["started"] for r in aa),
                "agent_exposed": sum(r["agent_exposed"] for r in aa),
                "complete_terminal": sum(r["terminal_complete"] for r in aa),
                "driving_failure": sum(r["driving_failure"] for r in aa),
                "technical_interruption": sum(r["technical_interruption"] for r in aa),
                "status_counts": dict(Counter(r["status"] for r in aa)),
                "observed_incidents_in_technical_runs": {metric: sum(r["technical_interruption"] and r[metric] is True for r in aa)
                                                        for metric in (*SAFETY, "offroad_or_wrong_lane", "wrong_target_execution")},
                "primary_observed": len(primary), "primary_missing": missing_n,
                "primary_successes": sum(primary),
                "planned_primary_success_bound_low": sum(primary) / len(aa) if aa else None,
                "planned_primary_success_bound_high": (sum(primary) + missing_n) / len(aa) if aa else None,
                "full_first_order_count": sum(r["order_in_pair"] == 1 for r in aa) if arm == "ABL_FULL" else None,
                "metrics": {},
            }
            for metric in (*BOOL_METRICS, *NUMERIC_METRICS):
                eligible_rows = aa if metric in PARTIAL_RUN_COST_METRICS else [r for r in aa if r["terminal_complete"]]
                estimate = clustered_estimate(eligible_rows, metric, repetitions, seed)
                estimate["missing"] = len(aa) - estimate["n"]
                estimate["eligibility_rule"] = "OBSERVED_COST_INCLUDING_PARTIAL_RUNS" if metric in PARTIAL_RUN_COST_METRICS else "COMPLETE_TERMINAL_AND_METRIC_OBSERVED"
                if metric in BOOL_METRICS:
                    estimate["positive_count"] = sum(r[metric] is True for r in eligible_rows)
                arm_summary["metrics"][metric] = estimate
            g["arms"][arm] = arm_summary
        for metric in (*BOOL_METRICS, *NUMERIC_METRICS):
            field = metric + "_difference_full_minus_comparator"
            effect = clustered_estimate(pp, field, repetitions, seed)
            effect["planned_pairs"] = g["planned_pair_count"]
            effect["missing_pairs"] = g["planned_pair_count"] - effect["n"]
            effect["direction"] = _direction(metric)
            if metric in BOOL_METRICS:
                effect["full_positive_comparator_negative"] = sum(p[field] == 1 for p in pp)
                effect["full_negative_comparator_positive"] = sum(p[field] == -1 for p in pp)
            g["paired_effects"][metric] = effect
        output["groups"].append(g)
    output["limitations"] = [
        "No p-value, superiority, equivalence, or noninferiority claim is generated.",
        "Intervals condition on observed eligible units; technical missingness can be informative.",
        "Driving failures remain in the primary denominator; technical primary missingness is null.",
        "Planned-denominator bounds treat unknown outcomes as all failure versus all success, not imputation.",
        "All paired effects are FULL minus comparator; positive risk differences mean more FULL risk.",
        "Nonterminal behavior counts and incident flags remain raw; outcome rates use complete terminals. Technical-run incidents are separately counted.",
        "Small template counts and degenerate empirical bootstrap intervals limit inference.",
        "Secondary mechanism metrics are descriptive and not multiplicity-adjusted confirmatory endpoints.",
    ]
    return output


def _read(path, kind):
    path = Path(path)
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    obj = json.loads(path.read_text())
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and kind in obj:
        return obj[kind]
    raise ValueError("expected JSON list, JSONL, or object containing " + kind)


def _write_csv(path, rows, fields):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("null" if row.get(k) is None else "true" if row[k] is True else "false" if row[k] is False else row[k]) for k in fields})


def _fmt(value):
    return "null" if value is None else "%.4f" % value


def write_outputs(output_dir, episodes, pairs, summary, sources):
    directory = Path(output_dir)
    # Explicitly non-overwriting: refresh summaries into a new snapshot directory.
    directory.mkdir(parents=True, exist_ok=False)
    _write_csv(directory / "episode_results.csv", episodes, EPISODE_COLUMNS)
    pair_fields = list(pairs[0]) if pairs else ["experiment_id", "pair_id", "primary_pair_complete", "incomplete_pair_reason"]
    _write_csv(directory / "paired_results.csv", pairs, pair_fields)
    (directory / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n")
    paper = []
    lines = ["# Episode and matched-pair summary", "", "Primary: independently judged correct language task and safe completion. Difference: ABL_FULL minus comparator.", ""]
    for group in summary["groups"]:
        lines += ["| Experiment / phase / protocol | Arm | Planned | Started | Terminal | Driving failures | Technical | Primary successes / eligible | Missing |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
        for arm, counts in group["arms"].items():
            lines.append("| %s / %s / %s | %s | %d | %d | %d | %d | %d | %d / %d | %d |" % (
                group["experiment_id"], group["phase"], group["protocol_id"], arm,
                counts["planned"], counts["started"], counts["complete_terminal"],
                counts["driving_failure"], counts["technical_interruption"],
                counts["primary_successes"], counts["primary_observed"], counts["primary_missing"]))
        lines += ["", "%s: planned pairs %d; terminal pairs %d; primary eligible pairs %d. Incomplete primary pairs: %s." % (
            group["experiment_id"], group["planned_pair_count"], group["complete_terminal_pairs"],
            group["primary_complete_pairs"], ", ".join(group["incomplete_primary_pair_ids"]) or "none"), ""]
        if group["unpaired_development_probe_count"]:
            lines += ["Unpaired development probes: %d (counterparts were never planned; these are not matched pairs)." % group["unpaired_development_probe_count"], ""]
        for metric, effect in group["paired_effects"].items():
            paper.append({"experiment_id": group["experiment_id"], "phase": group["phase"],
                          "protocol_id": group["protocol_id"], "metric": metric, **effect})
        effect = group["paired_effects"]["correct_safe_complete"]
        lines += ["Primary paired difference %s, template-cluster bootstrap 95%% interval [%s, %s], eligible pairs %d across %d templates. %s" % (
            _fmt(effect["estimate"]), _fmt(effect["ci95_low"]), _fmt(effect["ci95_high"]),
            effect["n"], effect["template_count"], effect["warning"] or ""), ""]
    lines += ["All metrics, denominators, paired differences, and uncertainty are in summary.json and paper_table.csv.", "", *["- " + s for s in summary["limitations"]]]
    (directory / "summary_tables.md").write_text("\n".join(lines) + "\n")
    paper_fields = list(dict.fromkeys(k for row in paper for k in row)) or ["experiment_id", "metric", "estimate", "n"]
    _write_csv(directory / "paper_table.csv", paper, paper_fields)
    provenance = {"schema": SCHEMA, "sources": [{"path": str(Path(p).resolve()), "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in sources],
                  "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "unique_planned_run_count": summary["unique_planned_run_count"], "inputs_modified": False}
    (directory / "ANALYSIS_PROVENANCE.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--results", required=True, nargs="+")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--bootstrap-repetitions", type=int, default=5000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260909)
    args = parser.parse_args(argv)
    manifest = _read(args.manifest, "runs")
    results = [row for path in args.results for row in _read(path, "results")]
    episodes = normalize_episodes(manifest, results)
    pairs = paired_rows(episodes)
    summary = summarize(episodes, pairs, args.bootstrap_repetitions, args.bootstrap_seed)
    write_outputs(args.output_dir, episodes, pairs, summary, [args.manifest, *args.results])
    print(json.dumps({"output_dir": args.output_dir, "planned_memberships": len(episodes), "unique_planned_runs": summary["unique_planned_run_count"],
                      "planned_pairs": sum(p["pair_planning_complete"] for p in pairs),
                      "unpaired_development_probes": sum(not p["pair_planning_complete"] for p in pairs)}, sort_keys=True))


if __name__ == "__main__":
    main()
