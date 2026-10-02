"""Frozen post-batch extraction and descriptive analysis for RQ2 DEV.

The collector uses only prospectively written native/evaluator evidence.  It
preserves unavailable safety fields as ``UNKNOWN`` and runs aggregate analysis
only after all 72 legitimate episode terminals exist.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from driveclarify_t_mvp.admissibility import (
    analysis_eligible_from_receipt,
    complete_six_method_blocks,
    receipt_to_mapping,
)
from driveclarify_t_mvp.metrics import EgoSample, continuity_metrics
from driveclarify_t_mvp_native_qualification.admissibility_evidence import (
    build_cell_admissibility_from_evidence,
    write_cell_receipt_once,
)


METHODS = ("T-B1", "T-B2", "T-B3", "T-B4", "T-B5", "T-B6")
BUCKETS = (
    "T1_BEFORE_COMMITMENT",
    "T2_NEAR_COMMITMENT",
    "T3_POST_COMMIT_RECOVERABLE",
    "T4_NO_SAFE_CURRENT_OPPORTUNITY",
)
BINARY = (
    "local_semantic_adaptation_success",
    "final_global_task_completion",
    "commitment_violation",
    "recovery_success",
    "rejoin_success",
    "collision",
    "route_deviation",
    "off_road",
)
CONTINUOUS = (
    "max_steering_discontinuity",
    "max_heading_discontinuity_deg",
    "max_jerk_mps3",
    "recovery_time_s",
    "recovery_distance_m",
    "global_planner_calls",
    "local_planner_calls",
    "vla_forwards",
    "planning_latency_ms",
)
HIGHER_IS_BETTER = frozenset(
    {
        "local_semantic_adaptation_success",
        "final_global_task_completion",
        "recovery_success",
        "rejoin_success",
    }
)


def _load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _known(value: Any) -> bool:
    return value not in (None, "UNKNOWN", "NOT_APPLICABLE")


def _metric_value(result: Any) -> Any:
    if result is None:
        return "UNKNOWN"
    status = getattr(result, "status", None)
    token = getattr(status, "value", status)
    return getattr(result, "value", None) if token == "AVAILABLE" else "UNKNOWN"


def _boundaries(evidence: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((evidence / "boundaries").glob("boundary_*.json"))
    ]


def _controls(evidence: Path) -> dict[int, dict[str, Any]]:
    return {
        int(row["sim_frame"]): row
        for row in (
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((evidence / "controls").glob("control_*.json"))
        )
    }


def _scientific_global_terminal(root: Path, process: dict[str, Any]) -> bool | str:
    if process.get("terminal_class") == "SCIENTIFIC_NONCOMPLETION_TIMEOUT":
        return "UNKNOWN"
    checkpoint = _load(root / "official_checkpoint.json", {})
    records = checkpoint.get("_checkpoint", {}).get("records", [])
    if not records:
        return "UNKNOWN"
    record = records[-1]
    status = str(record.get("status", record.get("entry_status", ""))).casefold()
    if "complete" in status or "success" in status:
        return True
    if status:
        return False
    return "UNKNOWN"


def _transition_action(evidence: Path) -> tuple[bool, bool, str]:
    installed = (evidence / "baseline_consumption.json").exists() or (
        evidence / "t_b2_prepare_install.json"
    ).exists() or (evidence / "t_b1_dispatch.json").exists()
    local = (evidence / "t_b4_local_consumption.json").exists()
    decision = "UNKNOWN"
    b6 = _load(evidence / "t_b6_dispatch.json")
    if b6:
        decision = str(b6.get("decision", {}).get("outcome", "UNKNOWN"))
        installed = installed or b6.get("install_receipt") is not None
    b3 = _load(evidence / "t_b3_old_terminal.json")
    if b3:
        decision = str(b3.get("decision", {}).get("outcome", decision))
        installed = installed or b3.get("install_receipt") is not None
    return installed, local, decision


def _episode_metrics(root: Path, config: dict[str, Any], oracle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = root / "agent_evidence"
    process = _load(root / "process_job" / "PROCESS_RECEIPT.json", {})
    injection = _load(evidence / "evaluator" / "injection_receipt.json", {})
    receipt = injection.get("receipt", {})
    if not isinstance(receipt.get("actual_injection_frame"), int):
        raise RuntimeError("ELIGIBLE_CELL_INJECTION_FRAME_MISSING")
    if not isinstance(receipt.get("simulation_timestamp_s"), (int, float)):
        raise RuntimeError("ELIGIBLE_CELL_INJECTION_TIME_MISSING")
    update_frame = int(receipt["actual_injection_frame"])
    update_time = float(receipt["simulation_timestamp_s"])
    if update_frame < 0 or update_time < 0.0:
        raise RuntimeError("ELIGIBLE_CELL_INJECTION_EVIDENCE_INVALID")
    boundaries = _boundaries(evidence)
    controls = _controls(evidence)
    post = [row for row in boundaries if int(row["sim_frame"]) >= update_frame]
    saw_adjacent = any(
        int(row["road_id"]) == 39 and int(row["lane_id"]) == -2 for row in post
    )
    rejoin_rows: list[dict[str, Any]] = []
    adjacent_seen = False
    for row in post:
        adjacent_seen = adjacent_seen or (
            int(row["road_id"]) == 39 and int(row["lane_id"]) == -2
        )
        if adjacent_seen and int(row["road_id"]) == 39 and int(row["lane_id"]) == -1:
            rejoin_rows.append(row)
            break
    rejoin = bool(rejoin_rows)

    samples = []
    previous_frame = None
    for row in boundaries:
        frame = int(row["sim_frame"])
        control = controls.get(frame)
        ego = row["ego"]
        samples.append(
            EgoSample(
                frame=frame,
                sim_time_s=float(row["sim_time_s"]),
                position_xyz_m=tuple(float(x) for x in ego["location_xyz_m"]),
                yaw_degrees=float(ego["rotation_pitch_yaw_roll_deg"][1]),
                acceleration_world_mps2=tuple(float(x) for x in ego["acceleration_world_mps2"]),
                ego_right_unit_world=tuple(float(x) for x in ego["right_unit_world"]),
                steer_applied=None if control is None else float(control["steer"]),
                preceding_frame_gap_identified=(previous_frame is None or frame == previous_frame + 1),
                event_id=f"boundary-{frame}",
            )
        )
        previous_frame = frame
    continuity = continuity_metrics(samples, t_effect_s=update_time)
    installed, local_replacement, decision = _transition_action(evidence)
    bucket = str(oracle["timing_bucket"])
    if bucket in {"T3_POST_COMMIT_RECOVERABLE", "T4_NO_SAFE_CURRENT_OPPORTUNITY"}:
        commitment_violation: bool | str = bool(installed or local_replacement)
        if config["baseline_id"] == "T-B5":
            commitment_violation = bool(saw_adjacent)
    elif decision in {"MISSED_CURRENT_OPPORTUNITY", "REJECT_STALE_OR_INFEASIBLE"}:
        commitment_violation = True
    elif config["baseline_id"] == "T-B5":
        commitment_violation = "UNKNOWN"
    else:
        commitment_violation = False

    recovery_time: float | str = "UNKNOWN"
    recovery_distance: float | str = "UNKNOWN"
    if bucket == "T3_POST_COMMIT_RECOVERABLE" and rejoin_rows:
        terminal = rejoin_rows[0]
        recovery_time = float(terminal["sim_time_s"]) - update_time
        path = [row for row in post if int(row["sim_frame"]) <= int(terminal["sim_frame"])]
        recovery_distance = sum(
            math.dist(a["ego"]["location_xyz_m"], b["ego"]["location_xyz_m"])
            for a, b in zip(path, path[1:])
        )

    terminal = _load(evidence / "agent_terminal.json", {})
    accounting = terminal.get("planning_accounting")
    accounting = accounting if isinstance(accounting, dict) else {}
    planning = accounting.get("planning_latency_s")
    planning = planning if isinstance(planning, dict) else {}
    metrics = {
        "local_semantic_adaptation_success": bool(saw_adjacent),
        "final_global_task_completion": _scientific_global_terminal(root, process),
        "commitment_violation": commitment_violation,
        "recovery_success": bool(saw_adjacent and rejoin) if bucket == "T3_POST_COMMIT_RECOVERABLE" else "NOT_APPLICABLE",
        "rejoin_success": bool(rejoin) if bucket != "T4_NO_SAFE_CURRENT_OPPORTUNITY" else "NOT_APPLICABLE",
        "collision": "UNKNOWN",
        "route_deviation": "UNKNOWN",
        "off_road": "UNKNOWN",
        "max_steering_discontinuity": _metric_value(continuity.get("steering_discontinuity")),
        "max_heading_discontinuity_deg": _metric_value(continuity.get("heading_discontinuity")),
        "max_jerk_mps3": _metric_value(continuity.get("jerk")),
        "recovery_time_s": recovery_time,
        "recovery_distance_m": recovery_distance,
        "global_planner_calls": (
            int(accounting["global_planner_call_count"])
            if "global_planner_call_count" in accounting
            else "UNKNOWN"
        ),
        "local_planner_calls": (
            int(accounting["local_planner_call_count"])
            if "local_planner_call_count" in accounting
            else "UNKNOWN"
        ),
        "vla_forwards": len(controls),
        "planning_latency_ms": (
            1000.0 * float(planning["active_planning_sum_s"])
            if "active_planning_sum_s" in planning
            else "UNKNOWN"
        ),
    }
    if metrics["local_semantic_adaptation_success"] is False:
        if commitment_violation is True or decision in {"MISSED_CURRENT_OPPORTUNITY", "REJECT_STALE_OR_INFEASIBLE"}:
            failure = "transition-policy failure"
        elif process.get("terminal_class") == "POST_EXPOSURE_CARLA_PROCESS_TERMINAL":
            failure = "CARLA infrastructure failure"
        elif controls:
            failure = "base SimLingo execution failure"
        else:
            failure = "UNKNOWN"
    elif metrics["final_global_task_completion"] is False:
        failure = "base SimLingo execution failure"
    else:
        failure = "NONE"
    provenance = {
        "injection_frame": update_frame,
        "injection_sim_time_s": update_time,
        "post_update_boundary_count": len(post),
        "transition_installed": installed,
        "local_replacement_consumed": local_replacement,
        "transition_decision": decision,
        "failure_attribution": failure,
        "safety_metrics_unknown_reason": "FROZEN_OFFICIAL_CRITERIA_TERMINAL_OUTPUT_UNAVAILABLE",
    }
    return metrics, provenance


def _ineligible_metrics() -> dict[str, str]:
    return {metric: "UNKNOWN" for metric in (*BINARY, *CONTINUOUS)}


def collect(
    report: Path,
    roster_path: Path,
    runtime_path: Path,
    oracle_path: Path,
    *,
    admissibility_output_dir: Path,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    roster = _load(roster_path)
    runtime = _load(runtime_path)["cases"]
    oracle = _load(oracle_path)["cases"]
    repository_root = (
        Path(__file__).resolve().parents[1]
        if repository_root is None
        else repository_root.resolve()
    )
    rows = []
    for ordinal, original_item in enumerate(roster["episodes"], 1):
        item = dict(original_item)
        item["ordinal"] = int(item.get("ordinal", ordinal))
        case_id = item["case_id"]
        attempts = sorted((report / "raw" / "dev" / case_id).glob("attempt_*"))
        exposed = [path for path in attempts if _load(path / "process_job" / "PROCESS_RECEIPT.json", {}).get("agent_exposed")]
        if len(exposed) != 1:
            raise RuntimeError(f"{case_id}:EXPECTED_ONE_AGENT_EXPOSED_ATTEMPT_GOT_{len(exposed)}")
        root = exposed[0]
        receipt = build_cell_admissibility_from_evidence(
            roster_item=item,
            runtime_case=runtime[case_id],
            oracle_case=oracle[case_id],
            exposed_attempt=root,
            oracle_manifest_path=oracle_path,
            repository_root=repository_root,
        )
        receipt_path = admissibility_output_dir / f"{case_id}.json"
        write_cell_receipt_once(receipt_path, receipt)
        receipt_mapping = receipt_to_mapping(receipt)
        eligible = analysis_eligible_from_receipt(receipt_mapping)
        if eligible:
            metrics, provenance = _episode_metrics(root, runtime[case_id], oracle[case_id])
        else:
            metrics = _ineligible_metrics()
            provenance = {
                "failure_attribution": "protocol scientific noncompletion",
                "injection_frame": receipt.injection_frame,
                "injection_sim_time_s": receipt.injection_sim_time_s,
                "transition_installed": "UNKNOWN",
                "local_replacement_consumed": "UNKNOWN",
                "transition_decision": "UNKNOWN",
                "safety_metrics_unknown_reason": (
                    "ANALYSIS_INELIGIBLE_INTERVENTION_CHAIN_INCOMPLETE"
                ),
            }
        process_terminal = _load(root / "process_job" / "PROCESS_RECEIPT.json")
        rows.append(
            {
                **item,
                "method_id": runtime[case_id]["baseline_id"],
                "timing_bucket": oracle[case_id]["timing_bucket"],
                "terminal_status": receipt.terminal_class,
                "process_terminal_status": process_terminal["terminal_class"],
                "scientific_retry_count": 0,
                "analysis_eligible": eligible,
                "cell_admissibility_receipt": receipt_mapping,
                "cell_admissibility_receipt_path": str(receipt_path),
                "metrics": metrics,
                "provenance": provenance,
                "raw_attempt_path": str(root),
            }
        )
    return {
        "schema_version": "driveclarify.rq2.t_mvp_raw_results.v1",
        "episode_count": len(rows),
        "episodes": rows,
    }


def _summary(rows: Iterable[dict[str, Any]], metric: str) -> dict[str, Any]:
    planned = list(rows)
    eligible = [
        row
        for row in planned
        if analysis_eligible_from_receipt(row["cell_admissibility_receipt"])
    ]
    values = [row["metrics"][metric] for row in eligible]
    known = [value for value in values if _known(value)]
    result: dict[str, Any] = {
        "planned_n": len(planned),
        "analysis_eligible_n": len(eligible),
        "analysis_ineligible_n": len(planned) - len(eligible),
        "known_n": len(known),
        "unknown_n": len(values) - len(known),
    }
    if not known:
        result["value"] = "UNKNOWN"
    elif metric in BINARY:
        count = sum(bool(value) for value in known)
        result.update({"true_n": count, "false_n": len(known) - count, "rate": count / len(known)})
    else:
        numeric = [float(value) for value in known]
        result.update({"mean": mean(numeric), "median": median(numeric), "min": min(numeric), "max": max(numeric)})
    return result


def _paired(rows: list[dict[str, Any]], comparator: str) -> list[dict[str, Any]]:
    by_key = {(row["timing_bucket"], int(row["seed"]), row["method_id"]): row for row in rows}
    output = []
    for bucket in BUCKETS:
        for seed in sorted({int(row["seed"]) for row in rows}):
            b6, other = by_key[(bucket, seed, "T-B6")], by_key[(bucket, seed, comparator)]
            pair_eligible = analysis_eligible_from_receipt(
                b6["cell_admissibility_receipt"]
            ) and analysis_eligible_from_receipt(other["cell_admissibility_receipt"])
            values = {}
            for metric in (*BINARY, *CONTINUOUS):
                left, right = b6["metrics"][metric], other["metrics"][metric]
                if not pair_eligible or not (_known(left) and _known(right)):
                    values[metric] = "UNKNOWN"
                elif metric in HIGHER_IS_BETTER:
                    values[metric] = float(left) - float(right)
                else:
                    values[metric] = float(right) - float(left)
            output.append(
                {
                    "timing_bucket": bucket,
                    "seed": seed,
                    "comparator": comparator,
                    "pair_analysis_eligible": pair_eligible,
                    "b6_receipt_sha256": b6["cell_admissibility_receipt"][
                        "canonical_sha256"
                    ],
                    "comparator_receipt_sha256": other[
                        "cell_admissibility_receipt"
                    ]["canonical_sha256"],
                    "oriented_advantage_positive_favors_b6": values,
                }
            )
    return output


def analyze(raw: dict[str, Any]) -> dict[str, Any]:
    rows = raw["episodes"]
    if len(rows) != 72 or len({row["episode_id"] for row in rows}) != 72:
        raise RuntimeError("DEV_72_UNIQUE_EPISODES_REQUIRED")
    seeds = sorted({int(row["seed"]) for row in rows})
    expected = {(m, b, s) for m in METHODS for b in BUCKETS for s in seeds}
    observed = {(r["method_id"], r["timing_bucket"], int(r["seed"])) for r in rows}
    if len(seeds) != 3 or observed != expected:
        raise RuntimeError("DEV_FACTORIAL_OR_THREE_SEED_CONTRACT_FAILED")
    for row in rows:
        eligible = analysis_eligible_from_receipt(row["cell_admissibility_receipt"])
        if row.get("analysis_eligible") is not eligible:
            raise RuntimeError("ROW_ELIGIBILITY_DISAGREES_WITH_CANONICAL_RECEIPT")
    summaries = {
        method: {
            bucket: {
                metric: _summary(
                    [r for r in rows if r["method_id"] == method and (bucket == "ALL" or r["timing_bucket"] == bucket)],
                    metric,
                )
                for metric in (*BINARY, *CONTINUOUS)
            }
            for bucket in (*BUCKETS, "ALL")
        }
        for method in METHODS
    }
    paired_b2 = _paired(rows, "T-B2")
    paired_b5 = _paired(rows, "T-B5")
    transition_metrics = (
        "local_semantic_adaptation_success",
        "commitment_violation",
        "recovery_success",
        "rejoin_success",
    )
    relevant = [r for r in (*paired_b2, *paired_b5) if r["timing_bucket"] != "T1_BEFORE_COMMITMENT"]
    advantages = [
        v
        for row in relevant
        for name, v in row["oriented_advantage_positive_favors_b6"].items()
        if name in transition_metrics and _known(v)
    ]
    ineligible = [
        row
        for row in rows
        if not analysis_eligible_from_receipt(row["cell_admissibility_receipt"])
    ]
    if ineligible:
        verdict = "RQ2_PILOT_INCONCLUSIVE_ENGINEERING"
    elif advantages and max(advantages) > 0 and min(advantages) >= 0:
        verdict = "RQ2_PILOT_PROMISING"
    elif advantages and max(advantages) > 0:
        verdict = "RQ2_PILOT_MIXED"
    else:
        verdict = "RQ2_PILOT_NOT_SUPPORTED"
    b6_waits = any(
        row["method_id"] == "T-B6"
        and analysis_eligible_from_receipt(row["cell_admissibility_receipt"])
        and row["provenance"]["transition_decision"] == "DEFER_COMMIT"
        for row in rows
    )
    equal_wait = "YES" if b6_waits and verdict in {"RQ2_PILOT_PROMISING", "RQ2_PILOT_MIXED"} else "NO"
    return {
        "schema_version": "driveclarify.rq2.t_mvp_dev_analysis.v2",
        "development_pilot_only": True,
        "episode_count": 72,
        "seeds": seeds,
        "terminal_counts": dict(Counter(row["terminal_status"] for row in rows)),
        "process_terminal_counts": dict(
            Counter(row["process_terminal_status"] for row in rows)
        ),
        "analysis_eligible_count": len(rows) - len(ineligible),
        "analysis_ineligible_count": len(ineligible),
        "failure_attribution_counts": dict(Counter(row["provenance"]["failure_attribution"] for row in rows)),
        "summaries": summaries,
        "paired_t_b6_vs_t_b2": paired_b2,
        "paired_t_b6_vs_t_b5": paired_b5,
        "six_method_blocks": complete_six_method_blocks(rows),
        "equal_wait_ablation_required": equal_wait,
        "pilot_verdict": verdict,
        "inferential_claims": "NOT_PERFORMED_THREE_SEED_EXPLORATORY_PILOT",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--roster", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--oracle", required=True)
    parser.add_argument("--raw-output", required=True)
    parser.add_argument("--analysis-output", required=True)
    parser.add_argument("--admissibility-output-dir", required=True)
    args = parser.parse_args()
    raw = collect(
        Path(args.report),
        Path(args.roster),
        Path(args.runtime),
        Path(args.oracle),
        admissibility_output_dir=Path(args.admissibility_output_dir),
    )
    Path(args.raw_output).write_text(json.dumps(raw, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    result = analyze(raw)
    Path(args.analysis_output).write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
