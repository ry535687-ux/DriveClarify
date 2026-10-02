"""Aggregate E1 TRAIN episode artifacts without making DEV/TEST claims."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping

from .contracts import EXTENSION_METHODS, LEDGER_PATH, REPORT_ROOT, SCHEMA_PREFIX, file_sha256


ROOT = Path(__file__).resolve().parents[1]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        if isinstance(value, str):
            handle.write(value)
        else:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(tmp), str(path))


def _wilson(success: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.96
    p = success / total
    den = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / den
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return [max(0.0, centre - half), min(1.0, centre + half)]


def _value(metric: Any) -> Any:
    if isinstance(metric, Mapping):
        return metric.get("value") if metric.get("status") == "AVAILABLE" else None
    return metric


def _mean(values: Iterable[Any]) -> dict[str, Any]:
    numeric = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not numeric:
        return {"mean": None, "n": 0, "ci95_normal": None, "status": "UNKNOWN"}
    mean = fmean(numeric)
    if len(numeric) < 2:
        ci = None
    else:
        variance = sum((value - mean) ** 2 for value in numeric) / (len(numeric) - 1)
        half = 1.96 * math.sqrt(variance / len(numeric))
        ci = [mean - half, mean + half]
    return {"mean": mean, "n": len(numeric), "ci95_normal": ci, "status": "AVAILABLE"}


def _decision(rows: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    classes = ("ACT", "ASK", "WAIT")
    confusion = {gold: {pred: 0 for pred in classes} for gold in classes}
    unknown = 0
    for row in rows:
        gold = row["decision"]["expected"]
        pred = row["decision"]["observed_normalized"]
        if gold in classes and pred in classes:
            confusion[gold][pred] += 1
        else:
            unknown += 1
    per_class = {}
    for name in classes:
        tp = confusion[name][name]
        fp = sum(confusion[gold][name] for gold in classes if gold != name)
        fn = sum(confusion[name][pred] for pred in classes if pred != name)
        support = tp + fn
        precision = tp / (tp + fp) if tp + fp else (0.0 if support else None)
        recall = tp / support if support else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else 0.0 if support else None
        )
        per_class[name] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
            "recall_ci95_wilson": _wilson(tp, tp + fn),
        }
    f1s = [row["f1"] for row in per_class.values() if row["f1"] is not None]
    return {
        "confusion_matrix_3x3": confusion,
        "unknown_observed_count": unknown,
        "per_class": per_class,
        "macro_f1": fmean(f1s) if f1s else None,
        "accuracy": sum(confusion[name][name] for name in classes) / max(sum(sum(row.values()) for row in confusion.values()), 1),
    }


def _method_summary(rows: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    decision = _decision(rows)
    correct = sum(bool(row["decision"]["correct"]) for row in rows)
    ambiguity_available = [row for row in rows if row["automatic_ambiguity"]["predicted_present"] is not None]
    ambiguity_correct = sum(row["automatic_ambiguity"]["correct"] is True for row in ambiguity_available)
    ground_count = [
        _value(row["grounding"]["referent_detection_recall_count_proxy"])
        for row in rows
    ]
    false_count = [
        _value(row["grounding"]["false_referent_rate_count_proxy"])
        for row in rows
    ]
    target_known = [row["target_binding"]["success"] for row in rows if row["target_binding"]["success"] is not None]
    return {
        "episode_count": len(rows),
        "decision": decision,
        "decision_correct_rate": correct / len(rows) if rows else None,
        "decision_correct_ci95_wilson": _wilson(correct, len(rows)),
        "automatic_ambiguity": {
            "accuracy": ambiguity_correct / len(ambiguity_available) if ambiguity_available else None,
            "n": len(ambiguity_available),
            "ci95_wilson": _wilson(ambiguity_correct, len(ambiguity_available)),
        },
        "grounding": {
            "referent_detection_recall_count_proxy": _mean(ground_count),
            "false_referent_rate_count_proxy": _mean(false_count),
            "identity_iou_metrics_status": "UNKNOWN_UNLESS_POSTHOC_PROJECTION_AVAILABLE",
        },
        "candidate": {
            "raw_k": _mean(row["candidate"].get("raw_k") for row in rows),
            "effective_k": _mean(row["candidate"].get("effective_k") for row in rows),
            "collapse_rate": (sum(row["candidate"].get("candidate_collapse") is True for row in rows) / len(rows)) if rows else None,
        },
        "target_binding": {
            "success_rate": sum(value is True for value in target_known) / len(target_known) if target_known else None,
            "unknown_rate": 1 - len(target_known) / len(rows) if rows else None,
        },
        "task": {key: _mean(_value(row["task"][key]) for row in rows) for key in ("route_completion",)},
        "task_boolean": {
            key: {
                "true": sum(_value(row["task"][key]) is True for row in rows),
                "false": sum(_value(row["task"][key]) is False for row in rows),
                "unknown": sum(_value(row["task"][key]) is None for row in rows),
            }
            for key in ("goal_correctness", "wrong_goal_execution", "instruction_success")
        },
        "safety": {
            key: {
                "true": sum(_value(row["safety"][key]) is True for row in rows),
                "false": sum(_value(row["safety"][key]) is False for row in rows),
                "unknown": sum(_value(row["safety"][key]) is None for row in rows),
            }
            for key in ("collision", "offroad", "wrong_lane", "red_light", "stop_sign", "near_miss")
        } | {"minimum_ttc": _mean(_value(row["safety"]["minimum_ttc"]) for row in rows)},
        "interaction": {
            key: _mean(row["interaction"].get(key) for row in rows)
            for key in ("query_count", "answer_delay", "wait_duration", "replan_count", "interaction_added_time")
        },
        "compute": {
            key: _mean(row["compute"].get(key) for row in rows)
            for key in ("grounding_dino_latency", "dino_invocations", "bytetrack_latency", "event_estimator_latency", "language_ambiguity_latency", "candidate_construction_latency", "simlingo_forwards", "candidate_simlingo_forwards", "decision_latency")
        },
        "zero_counter_violations": sum(
            any(value not in (0, None, False) for value in row["zero_counter_audit"].values())
            for row in rows
        ),
    }


def aggregate() -> Mapping[str, Any]:
    ledger = _load(ROOT / LEDGER_PATH)
    completed_slots = [row for row in ledger["rows"] if row["status"] == "COMPLETED_RECORDED"]
    results: list[Mapping[str, Any]] = []
    missing = []
    for slot in completed_slots:
        path = ROOT / str(slot["artifact_dir"]) / "EXTENSION_EPISODE_RESULT.json"
        if path.is_file() and file_sha256(path) == slot.get("episode_result_sha256"):
            results.append(_load(path))
        else:
            missing.append(slot["episode_id"])
    by_method = {
        method: [row for row in results if row["identity"]["method_id"] == method]
        for method in EXTENSION_METHODS
    }
    method_summary = {method: _method_summary(rows) for method, rows in by_method.items()}
    by_mechanism = {
        mechanism: _method_summary([row for row in results if row["decision"]["expected"] == mechanism])
        for mechanism in ("ACT", "ASK", "WAIT")
    }
    status_counts = Counter(row["status"] for row in ledger["rows"])
    completed = len(completed_slots)
    failed = status_counts.get("FAILED_RECORDED", 0)
    full = completed + failed == 216 and completed == 216
    grounded_rows = by_method["driveclarify_grounded_v1"]
    natural = Counter(
        row["decision"]["observed_normalized"]
        for row in grounded_rows
        if not row["decision"]["forced"]
        and bool(row.get("runtime_lifecycle_complete"))
        and row["decision"]["observed_normalized"] in {"ACT", "ASK", "WAIT"}
    )
    natural_counts = {name: natural[name] for name in ("ACT", "ASK", "WAIT")}
    coverage_pass = all(natural[name] >= 3 for name in ("ACT", "ASK", "WAIT"))
    status = (
        "PASS_GROUNDED_LANGUAGE_V1_EXTENSION_TRAIN_READY_FOR_DEV_AUTHORIZATION"
        if full and coverage_pass
        else "PARTIAL_GROUNDED_LANGUAGE_V1_EXTENSION_TRAIN_VALID_CONTRACTS"
    )
    high_consequence = {
        "available_wrong_goal_true": sum(
            _value(row["task"]["wrong_goal_execution"]) is True for row in results
        ),
        "wrong_goal_unknown": sum(
            _value(row["task"]["wrong_goal_execution"]) is None for row in results
        ),
        "collision_true": sum(_value(row["safety"]["collision"]) is True for row in results),
        "claim": "NO_ZERO_ERROR_CLAIM_WHERE_WRONG_GOAL_EVIDENCE_IS_UNKNOWN",
    }
    runtime_blocked = sum(bool(row.get("runtime_blocked")) for row in results)
    common = {
        "schema_version": SCHEMA_PREFIX + ".train_results.v1",
        "generated_at_utc": _now(),
        "status": status,
        "train_only": True,
        "not_final_paper_result": True,
        "scheduled": 216,
        "completed": completed,
        "failed_recorded": failed,
        "runtime_blocked_result_count": runtime_blocked,
        "not_started": status_counts.get("SCHEDULED_NOT_STARTED", 0),
        "running": status_counts.get("RUNNING", 0),
        "result_hash_missing_or_mismatch": missing,
        "method_order": list(EXTENSION_METHODS),
        "method_summary": method_summary,
        "mechanism_summary_all_methods": by_mechanism,
        "grounded_natural_decision_counts": natural_counts,
        "grounded_e2_coverage_pass": coverage_pass,
        "high_consequence_errors": high_consequence,
        "uncertainty_policy": "Wilson 95% intervals for rates; normal 95% intervals for descriptive means when n>=2; no significance claims on TRAIN.",
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
    }
    root = ROOT / REPORT_ROOT
    _write(root / "EXTENSION_TRAIN_RESULTS.json", common)
    _write(root / "EXTENSION_ACT_ASK_WAIT_AUDIT.json", {
        "schema_version": SCHEMA_PREFIX + ".act_ask_wait_audit.v1", "status": status,
        "natural_grounded_counts": natural_counts, "gate_at_least_three_each": common["grounded_e2_coverage_pass"],
        "decision_by_method": {key: value["decision"] for key, value in method_summary.items()},
        "forced_decision_total": sum(int(row["zero_counter_audit"].get("forced_decision_count") or 0) for row in grounded_rows),
        "train_only": True, "extension_dev_attempt_count": 0, "extension_test_attempt_count": 0, "extension_test_consumed": False,
    })
    _write(root / "EXTENSION_AMBIGUITY_AUDIT.json", {
        "schema_version": SCHEMA_PREFIX + ".ambiguity_audit.v1", "status": status,
        "method_metrics": {key: value["automatic_ambiguity"] for key, value in method_summary.items()},
        "limitation": "Automatic scene-grounded ambiguity metrics are available only for Grounded V1; frozen baselines did not emit commensurate grounding evidence.",
        "train_only": True,
    })
    _write(root / "EXTENSION_GROUNDING_AUDIT.json", {
        "schema_version": SCHEMA_PREFIX + ".grounding_audit.v1", "status": status,
        "method_metrics": {key: value["grounding"] for key, value in method_summary.items()},
        "identity_metric_policy": "UNKNOWN is preserved when independent actor projection/IoU is unavailable; count proxies are explicitly not identity accuracy.",
        "train_only": True,
    })
    _write(root / "EXTENSION_TARGET_BINDING_AUDIT.json", {
        "schema_version": SCHEMA_PREFIX + ".target_binding_audit.v1", "status": status,
        "method_metrics": {key: value["target_binding"] for key, value in method_summary.items()},
        "train_only": True,
    })
    _write(root / "EXTENSION_COMPUTE_AUDIT.json", {
        "schema_version": SCHEMA_PREFIX + ".compute_audit.v1", "status": status,
        "method_metrics": {key: value["compute"] for key, value in method_summary.items()},
        "visualization_added_inference_control_total": sum(value["zero_counter_violations"] for value in method_summary.values()),
        "train_only": True,
    })
    _write(root / "EXTENSION_TRAIN_DATA_QUALITY_AUDIT.json", {
        "schema_version": SCHEMA_PREFIX + ".data_quality_audit.v1", "status": ("PASS_COMPLETE_HASH_BOUND" if full and not missing else "PARTIAL_INCOMPLETE"),
        "ledger_status_counts": dict(status_counts), "runtime_blocked_result_count": runtime_blocked,
        "result_hash_missing_or_mismatch": missing,
        "unknowns_are_not_zero": True, "runtime_evaluator_separation": True,
        "extension_dev_attempt_count": 0, "extension_test_attempt_count": 0, "extension_test_consumed": False,
    })
    report = f"""# Grounded Language V1 Extension E1 — TRAIN Report

Status: `{status}`

TRAIN ONLY. NOT A FINAL PAPER RESULT.

The frozen TRAIN ledger contains 216 episode slots. At this snapshot, {completed}
completed with hash-bound normalized results, {failed} recorded a native failure,
and {status_counts.get('SCHEDULED_NOT_STARTED', 0)} remain unstarted.

Grounded V1 natural decisions (forced decisions excluded): ACT {natural['ACT']},
ASK {natural['ASK']}, WAIT {natural['WAIT']}. The E2 >=3-each gate is
`{'PASS' if common['grounded_e2_coverage_pass'] else 'NOT_YET_PASS'}`.

This controlled distribution covers Town03/Town05 and white-van/bus language-scene
families only. Shared physical TRAIN fixtures and paraphrased language limit
independence and external validity. Identity-level grounding, goal correctness,
wrong-goal execution, TTC, or other fields remain UNKNOWN wherever the independent
post-hoc evidence was unavailable; UNKNOWN is never counted as safe or zero.

No H1/H2/H3 verdict, significance claim, DEV conclusion, TEST conclusion, or
universal language-understanding claim is made from this TRAIN evidence.
"""
    _write(root / "EXTENSION_TRAIN_REPORT.md", report)
    return common


__all__ = ["aggregate"]
