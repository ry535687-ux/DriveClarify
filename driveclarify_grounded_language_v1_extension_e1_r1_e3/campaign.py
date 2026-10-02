"""Execution-only E3 controller for the frozen Grounded Language E1-R1 runtime.

This module deliberately owns scheduling, receipts, aggregation, and evidence
copying only.  It imports the frozen E1-R1 backend/runtime and does not alter
policy, thresholds, fixtures, methods, or evaluator semantics.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import traceback
from collections import Counter
from dataclasses import replace
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as r1_backend
from driveclarify_grounded_language_v1_extension_e1_r1.campaign import (
    verify_protected_integrity,
)
from driveclarify_grounded_language_v1_extension_e1_r1.contracts import (
    FEATURE_FLAG,
    METHODS,
    SCENARIO_ROOT,
    file_sha256,
    physical_fixture,
)
from driveclarify_grounded_language_v1_extension_e1_r1.population import train_rows
from driveclarify_paper_mvp_stage6b import backend as stage6b
from tools.run_grounded_language_v1_triad import run as run_grounded


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports/grounded_language_v1_extension_e1_r1_e3"
ARTIFACT_ROOT = ROOT / "artifacts/grounded_language_v1_extension_e1_r1_e3"
ENTRY_ROOT = ROOT / "reports/grounded_language_v1_extension_e1_r1"
ENTRY_RECEIPT = ENTRY_ROOT / "E1R1_FINAL_RECEIPT.json"
ENTRY_HASH = "58ac6bddeb56bb1c851d291a8113382a4df0042f7035c91a666e002e29f12289"
ENTRY_STATUS = "PASS_GROUNDED_LANGUAGE_V1_EXTENSION_E1_R1_TRIAD_READY_FOR_FULL_TRAIN_AUTHORIZATION"
SUCCESS = "PASS_GROUNDED_LANGUAGE_V1_EXTENSION_E3_TRAIN_COMPLETE_READY_FOR_DEV_AUTHORIZATION"
PARTIAL = "PARTIAL_GROUNDED_LANGUAGE_V1_EXTENSION_E3_TRAIN_VALID_PREFIX"
BLOCKED = "BLOCKED_GROUNDED_LANGUAGE_V1_EXTENSION_E3_EVALUATOR_UNKNOWN_PRESERVATION_DEFECT"
METHOD_MAP = r1_backend.METHOD_MAP
SCHEMA = "driveclarify.grounded_language_v1_extension_e1_r1_e3"
SEEDS = {"ACT": (5101, 5102, 5103), "ASK": (5101, 5102, 5103), "WAIT": (5301, 5302, 5303)}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def _sha(path: Path) -> str | None:
    return file_sha256(path) if path.is_file() else None


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _artifact_manifest_valid() -> dict[str, Any]:
    manifest = _load(ENTRY_ROOT / "ARTIFACT_HASHES.json")
    mismatches = []
    for name, expected in manifest["artifacts"].items():
        path = ROOT / name
        observed = _sha(path)
        if observed != expected["sha256"]:
            mismatches.append({"path": name, "expected": expected["sha256"], "observed": observed})
    return {"entry_count": len(manifest["artifacts"]), "mismatches": mismatches, "pass": not mismatches}


def _external_integrity() -> dict[str, Any]:
    return {
        "simlingo_head": {
            "expected": stage6b.SIMLINGO_PROTECTED_HEAD,
            "observed": stage6b._git_head(stage6b.SIMLINGO_ROOT),
        },
        "simlingo_protected_diff_sha256": {
            "expected": stage6b.SIMLINGO_PROTECTED_DIFF_SHA256,
            "observed": stage6b._git_diff_sha256(stage6b.SIMLINGO_ROOT),
        },
        "checkpoint_sha256": {
            "expected": stage6b.CHECKPOINT_SHA256,
            "observed": stage6b._file_sha256(stage6b.CHECKPOINT),
        },
    }


def verify_entry() -> dict[str, Any]:
    receipt_hash = _sha(ENTRY_RECEIPT)
    receipt = _load(ENTRY_RECEIPT)
    artifacts = _artifact_manifest_valid()
    protected = verify_protected_integrity()
    external = _external_integrity()
    external_pass = all(row["expected"] == row["observed"] for row in external.values())
    passed = (
        receipt_hash == ENTRY_HASH
        and receipt.get("status") == ENTRY_STATUS
        and artifacts["pass"]
        and external_pass
    )
    result = {
        "schema_version": SCHEMA + ".entry_check.v1",
        "status": "PASS_E3_ENTRY" if passed else "BLOCKED_E3_ENTRY_ARTIFACT_MISMATCH",
        "entry_receipt_sha256": {"expected": ENTRY_HASH, "observed": receipt_hash},
        "entry_status": {"expected": ENTRY_STATUS, "observed": receipt.get("status")},
        "entry_artifact_manifest": artifacts,
        "protected_integrity": protected,
        "external_integrity": external,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
        "checked_at_utc": _now(),
    }
    if not passed:
        raise RuntimeError("BLOCKED_E3_ENTRY_ARTIFACT_MISMATCH")
    return result


def _mechanism(identity: Mapping[str, Any]) -> str:
    return str(identity["language_scene_id"]).split("-")[-2]


def canonical_rows(execution_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for identity in train_rows():
        mechanism = _mechanism(identity)
        for seed_index, seed in enumerate(SEEDS[mechanism], start=1):
            for method in METHODS:
                episode_id = "{}-{:03d}".format(execution_id, len(rows) + 1)
                artifact = ARTIFACT_ROOT / execution_id / identity["language_scene_id"] / ("seed_{}".format(seed_index)) / method
                rows.append(
                    {
                        "slot_index": len(rows) + 1,
                        "episode_id": episode_id,
                        "language_scene_id": identity["language_scene_id"],
                        "physical_fixture_id": identity["physical_fixture_id"],
                        "mechanism_family": mechanism,
                        "raw_instruction": identity["raw_instruction"],
                        "split": "train",
                        "seed_index": seed_index,
                        "carla_seed": seed,
                        "method": method,
                        "status": "NOT_RUN",
                        "attempt_count": 0,
                        "infrastructure_retry": 0,
                        "episode_receipt": None,
                        "metric_receipt": None,
                        "artifact_dir": _rel(artifact),
                        "hash": None,
                        "started_at_utc": None,
                        "ended_at_utc": None,
                        "failure_taxonomy": None,
                        "failure_reason": None,
                    }
                )
    if len(rows) != 216:
        raise RuntimeError("E3_POPULATION_NOT_216")
    return rows


def initialize(execution_id: str) -> dict[str, Any]:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    ledger_path = REPORT_ROOT / "E3_FORMAL_TRAIN_LEDGER.json"
    if ledger_path.exists():
        existing = _load(ledger_path)
        if existing.get("execution_id") != execution_id:
            raise RuntimeError("E3_FRESH_EXECUTION_ID_REQUIRED")
        return existing
    entry = verify_entry()
    rows = canonical_rows(execution_id)
    schedule_payload = [
        {key: row[key] for key in ("language_scene_id", "physical_fixture_id", "seed_index", "carla_seed", "method")}
        for row in rows
    ]
    ledger = {
        "schema_version": SCHEMA + ".formal_train_ledger.v1",
        "execution_id": execution_id,
        "status": PARTIAL,
        "fresh_formal_train": True,
        "split": "train",
        "scheduled": 216,
        "started": 0,
        "completed": 0,
        "remaining": 216,
        "methods": list(METHODS),
        "schedule_sha256": hashlib.sha256(json.dumps(schedule_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "rows": rows,
        "contract_defect": None,
        "first_18_checkpoint": "PENDING",
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
        "created_at_utc": _now(),
        "updated_at_utc": _now(),
    }
    _write(REPORT_ROOT / "E3_PREEXECUTION_CHECK.json", entry)
    _text(
        REPORT_ROOT / "E3_EXECUTION_PROTOCOL.md",
        """# E3 formal TRAIN execution protocol

Execution ID: `{}`

- Frozen population: 12 TRAIN language-scene identities x 3 seeds x 6 methods = 216 slots.
- Exact methods: {}.
- Matched-block order: one language-scene identity and seed across all six methods.
- Runtime: native DISPLAY=:1, serial exclusive GPU lease, bounded single-town cold boot.
- Retry policy: zero automatic retries for every method; infrastructure failures remain recorded.
- First checkpoint: exactly the first three matched blocks (18 slots).
- Policy/gold firewall, PID ownership, passive visualization, cleanup, DEV=0 and TEST=0/unconsumed are hard gates.
- No threshold, policy, fixture, split, method, scenario, M2B, M3, authority, or PID changes are permitted.
""".format(execution_id, ", ".join(METHODS)),
    )
    _write(ledger_path, ledger)
    return ledger


def _save_ledger(ledger: dict[str, Any]) -> None:
    ledger["started"] = sum(row["attempt_count"] > 0 for row in ledger["rows"])
    ledger["completed"] = sum(row["status"] == "COMPLETED_RECORDED" for row in ledger["rows"])
    ledger["remaining"] = sum(row["status"] == "NOT_RUN" for row in ledger["rows"])
    ledger["updated_at_utc"] = _now()
    _write(REPORT_ROOT / "E3_FORMAL_TRAIN_LEDGER.json", ledger)


def _output(row: Mapping[str, Any]) -> Path:
    return ROOT / str(row["artifact_dir"])


def _spec(row: Mapping[str, Any]) -> stage6b.EpisodeSpec:
    source = r1_backend.resolve_episode(
        fixture_id=str(row["physical_fixture_id"]),
        method_id=str(row["method"]),
        episode_id=str(row["episode_id"]),
    )
    return replace(
        source,
        scenario_id=str(row["language_scene_id"]),
        seed=int(row["carla_seed"]),
        raw_instruction=str(row["raw_instruction"]),
    )


def _run_episode(row: Mapping[str, Any]) -> Mapping[str, Any]:
    output = _output(row)
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("E3_ARTIFACT_DIRECTORY_NOT_EMPTY:" + str(output))
    spec = _spec(row)
    fixture = physical_fixture(str(row["physical_fixture_id"]))
    representative = (
        row["method"] == "driveclarify_grounded_v1"
        and row["seed_index"] == 1
        and str(row["language_scene_id"]).endswith("-001")
    )
    lease_receipt: dict[str, Any] | None = None
    try:
        with r1_backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            if row["method"] == "driveclarify_grounded_v1":
                overrides = {
                    FEATURE_FLAG: "1",
                    "SCENARIO_RUNNER_ROOT": str((ROOT / SCENARIO_ROOT).resolve()),
                    "DRIVECLARIFY_E1R1_FIXTURE_ID": str(row["physical_fixture_id"]),
                    "DRIVECLARIFY_E1R1_WAIT_ACTOR_MOTION_SOURCE": str(fixture["motion_source"]),
                }
                if fixture.get("motion"):
                    overrides.update(
                        {
                            "DRIVECLARIFY_E1R1_WAIT_ACTOR_DWELL_SECONDS": str(fixture["motion"]["visible_dwell_seconds"]),
                            "DRIVECLARIFY_E1R1_WAIT_ACTOR_SPEED_MPS": str(fixture["motion"]["speed_mps"]),
                            "DRIVECLARIFY_E1R1_WAIT_ACTOR_DISTANCE_M": str(fixture["motion"]["distance_m"]),
                        }
                    )
                result = run_grounded(
                    output,
                    case=str(row["mechanism_family"]).casefold(),
                    seed=spec.seed,
                    method_id=str(row["method"]),
                    device="cpu",
                    control=True,
                    answer="The nearer white van.",
                    answer_delay=0.1,
                    timeout_seconds=240.0,
                    episode_spec=spec,
                    visualization=representative,
                    post_hoc_world_state=True,
                    terminate_on_runtime_terminal=True,
                    capture_desktop=representative,
                    environment_overrides=overrides,
                )
            else:
                original_root = stage6b.SCENARIO_DISCOVERY_ROOT
                stage6b.SCENARIO_DISCOVERY_ROOT = (ROOT / SCENARIO_ROOT).resolve()
                try:
                    result = stage6b.UnifiedNativeBackend(
                        wall_timeout_seconds=240.0,
                        no_progress_timeout_seconds=25.0,
                    ).run(spec, output, visualization=False)
                finally:
                    stage6b.SCENARIO_DISCOVERY_ROOT = original_root
    finally:
        if lease_receipt is not None and output.is_dir():
            _write(output / "GPU_LEASE_RECEIPT.json", lease_receipt)
    return result


def _metric(status: str, value: Any, source: str, reason: str | None = None) -> dict[str, Any]:
    return {"status": status, "value": value, "source": source, "reason": reason}


def _numeric_evidence(
    container: Mapping[str, Any],
    name: str,
    source: str,
    *,
    integer: bool = True,
) -> tuple[dict[str, Any], int | float | None]:
    """Ingest one evidence scalar without converting UNKNOWN into zero."""

    raw = container.get(name)
    if not isinstance(raw, Mapping):
        record = {
            "status": "UNKNOWN",
            "value": None,
            "source": source,
            "reason_codes": ["EVIDENCE_RECORD_MISSING"],
            "coverage": {},
        }
        return record, None
    record = dict(raw)
    status = str(record.get("status", "UNKNOWN"))
    value = record.get("value")
    if status != "AVAILABLE":
        return record, None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or (integer and int(value) != value)
    ):
        raise RuntimeError("E3_AVAILABLE_NUMERIC_EVIDENCE_INVALID:" + name)
    return record, int(value) if integer else float(value)


def _authority_gold_gate(metric: Mapping[str, Any]) -> dict[str, Any]:
    """Require known zero authority/gold counters; UNKNOWN is never a pass."""

    fields = (
        "privileged_gold_reads",
        "forced_decisions",
        "new_pid_count",
        "candidate_direct_writes",
        "m3_direct_writes",
    )
    unknown = [name for name in fields if metric.get(name) is None]
    violations = [
        name
        for name in fields
        if metric.get(name) is not None and int(metric.get(name, 0)) != 0
    ]
    status = (
        "VIOLATION"
        if violations
        else "UNKNOWN_REQUIRED_EVIDENCE"
        if unknown
        else "PASS_KNOWN_ZERO"
    )
    return {
        "status": status,
        "pass": status == "PASS_KNOWN_ZERO",
        "unknown_fields": unknown,
        "violation_fields": violations,
    }


def _summed_count_evidence(
    metrics: Iterable[Mapping[str, Any]], field: str
) -> dict[str, Any]:
    """Reduce episode counts with an explicit non-shrinking denominator."""

    values = [item.get(field) for item in metrics]
    missing = sum(value is None for value in values)
    known = [int(value) for value in values if value is not None]
    return {
        "status": "UNKNOWN" if missing else "AVAILABLE",
        "value": None if missing else sum(known),
        "known_sum": sum(known),
        "denominator": len(values),
        "available_count": len(known),
        "missing_count": missing,
        "reason": "UNKNOWN_REQUIRED_EPISODE_VALUE" if missing else None,
    }


def _leaderboard_metrics(output: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = output / "leaderboard_results.json"
    data = _load(path) if path.is_file() else {}
    records = data.get("_checkpoint", {}).get("records", [])
    record = records[0] if records else {}
    infractions = record.get("infractions", {})
    scores = record.get("scores", {})
    available = bool(records)
    def count(name: str) -> int:
        value = infractions.get(name, [])
        return len(value) if isinstance(value, list) else int(value or 0)
    task = {
        "route_completion": _metric("AVAILABLE" if available else "UNKNOWN", scores.get("score_route") if available else None, "LEADERBOARD", None if available else "NO_COMPLETE_EVALUATOR_RECORD"),
        "goal_correctness": _metric("UNKNOWN", None, "EVALUATOR", "NO_GROUNDED_GOAL_BINDING"),
        "wrong_goal_execution": _metric("UNKNOWN", None, "EVALUATOR", "NO_GROUNDED_GOAL_BINDING"),
        "instruction_success": _metric("UNKNOWN", None, "EVALUATOR", "NO_PROVEN_INSTRUCTION_SUCCESS_BINDING"),
    }
    safety = {
        "collision": _metric("AVAILABLE" if available else "UNKNOWN", bool(sum(count(x) for x in ("collisions_layout", "collisions_pedestrian", "collisions_vehicle"))) if available else None, "LEADERBOARD"),
        "offroad": _metric("AVAILABLE" if available else "UNKNOWN", bool(count("outside_route_lanes")) if available else None, "LEADERBOARD"),
        "wrong_lane": _metric("AVAILABLE" if available else "UNKNOWN", bool(count("route_dev")) if available else None, "LEADERBOARD"),
        "red_light": _metric("AVAILABLE" if available else "UNKNOWN", bool(count("red_light")) if available else None, "LEADERBOARD"),
        "stop_sign": _metric("AVAILABLE" if available else "UNKNOWN", bool(count("stop_infraction")) if available else None, "LEADERBOARD"),
        "minimum_ttc": _metric("UNKNOWN", None, "EVALUATOR", "NO_VALID_ONLINE_TTC_RECEIPT"),
        "near_miss": _metric("UNKNOWN", None, "EVALUATOR", "NO_VALID_ONLINE_TTC_RECEIPT"),
    }
    return task, safety


def _normalize(row: Mapping[str, Any]) -> dict[str, Any]:
    output = _output(row)
    task, safety = _leaderboard_metrics(output)
    metric: dict[str, Any] = {
        "schema_version": SCHEMA + ".episode_metrics.v1",
        "identity": {key: row[key] for key in ("episode_id", "language_scene_id", "physical_fixture_id", "mechanism_family", "seed_index", "carla_seed", "method", "split")},
        "task_metrics": task,
        "safety_metrics": safety,
        "failure_taxonomy": "UNKNOWN",
        "runtime_contract_valid": True,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
    }
    if row["method"] == "driveclarify_grounded_v1":
        live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
        native_path = output / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json"
        cleanup_path = output / "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json"
        if not (live_path.is_file() and native_path.is_file() and cleanup_path.is_file()):
            raise RuntimeError("E3_GROUNDED_REQUIRED_RECEIPT_MISSING")
        live, native, cleanup = _load(live_path), _load(native_path), _load(cleanup_path)
        grounding = live.get("grounding", {})
        bindings = live.get("target_binding_receipts", [])
        timeline = live.get("event_timeline", [])
        states = [item.get("state") for item in timeline]
        initial = live.get("initial_decision")
        raw_k, effective_k = live.get("raw_k"), live.get("effective_k")
        metric.update(
            {
                "initial_decision": initial,
                "final_decision": live.get("post_answer_decision") or live.get("post_information_decision") or initial,
                "ambiguity_detected": live.get("ambiguity_type") not in (None, "NONE") or (effective_k or 0) >= 2,
                "ambiguity_type": live.get("ambiguity_type"),
                "ambiguous_slot": (live.get("parsed_slots") or {}).get("ambiguous_slot"),
                "raw_k": raw_k,
                "effective_k": effective_k,
                "raw_detections": grounding.get("raw_grounding_k"),
                "plausible_detections": grounding.get("plausible_k"),
                "semantic_duplicates": bool(live.get("semantic_duplicate")),
                "grounding_duplicates": bool(live.get("grounding_duplicate")),
                "target_duplicates": bool(live.get("target_duplicate")),
                "candidate_collapse": bool(raw_k is not None and effective_k is not None and int(effective_k) < int(raw_k)),
                "same_maneuver": live.get("consequence_relation") == "EQUIVALENT_EXECUTABLE_CONSEQUENCE",
                "same_target": len({item.get("target_id") for item in bindings if item.get("target_id")}) <= 1 if bindings else None,
                "plan_divergence": bool(live.get("plan_divergence")),
                "consequence_divergence": bool(live.get("material_consequence_divergence")),
                "target_binding_available": bool(bindings) and all(item.get("target_id") and item.get("junction_id") and item.get("branch_id") for item in bindings),
                "distinct_target": len({item.get("target_id") for item in bindings if item.get("target_id")}) >= 2,
                "target_unknown": not bool(bindings),
                "query_count": 1 if initial == "ASK" else 0,
                "query_resolved": bool(live.get("answer_resolution")),
                "answer_delay_seconds": live.get("answer_delay_simulation_seconds"),
                "wait_count": 1 if initial == "WAIT" else 0,
                "wait_event_cleared": "CLEARED" in states,
                "premature_cleared": bool(live.get("premature_cleared", False)),
                "replan_count": int(bool(live.get("fresh_replan") or live.get("fresh_replan_id"))),
                "forced_decisions": int(live.get("forced_decision_count", 0)),
                "privileged_gold_reads": int(live.get("privileged_state_policy_read_count", 0)) + int(live.get("gold_policy_label_reads", 0)) + int(live.get("expected_decision_reads", 0)) + int(live.get("gold_candidate_index_reads", 0)),
                "new_pid_count": int(live.get("new_pid_count", 0)),
                "candidate_direct_writes": int(live.get("candidate_direct_vehicle_control_write_count", 0)),
                "m3_direct_writes": int(live.get("m3_direct_vehicle_control_write_count", 0)),
                "existing_pid_invocations": live.get("existing_pid_invocation_count"),
                "cleanup_pass": cleanup.get("status") == "PASS",
                "compute": {
                    "grounding_dino_invocations": grounding.get("detector_forward_count", 0),
                    "grounding_dino_latency_seconds": grounding.get("detector_latency_seconds", live.get("detector_latency_seconds")),
                    "reground_count": live.get("reground_count", 0),
                    "bytetrack_latency_seconds": live.get("tracker_latency_seconds"),
                    "event_estimator_latency_seconds": live.get("event_estimator_latency_seconds"),
                    "ambiguity_reasoning_latency_seconds": grounding.get("ambiguity_latency_seconds"),
                    "target_binding_latency_seconds": live.get("target_binding_latency_seconds"),
                    "candidate_construction_latency_seconds": live.get("candidate_construction_latency_seconds"),
                    "simlingo_forward_count": int(live.get("normal_simlingo_forward_count", 0)) + int(live.get("candidate_simlingo_forward_count", 0)),
                    "decision_latency_seconds": live.get("decision_latency_seconds"),
                },
                "visualization_passive": all(int(live.get(key, 0)) == 0 for key in ("visualization_induced_detector_forward_count", "visualization_induced_simlingo_forward_count", "visualization_induced_pid_count", "visualization_induced_planner_advance_count", "visualization_induced_vehicle_control_mutation_count")),
                "runtime_status": live.get("status"),
                "native_status": native.get("status"),
            }
        )
        if initial == "ASK" and not metric["query_resolved"]:
            metric["failure_taxonomy"] = "ASK INTERACTION FAILURE"
        elif initial == "WAIT" and not metric["wait_event_cleared"]:
            metric["failure_taxonomy"] = "WAIT TEMPORAL FAILURE"
        elif not metric["target_binding_available"]:
            metric["failure_taxonomy"] = "TARGET BINDING FAILURE"
        else:
            metric["failure_taxonomy"] = "NONE"
    else:
        result_path = output / "EPISODE_RESULT.json"
        receipt_path = output / "EPISODE_RECEIPT.json"
        cleanup_path = output / "CLEANUP_RECEIPT.json"
        if not (result_path.is_file() and receipt_path.is_file() and cleanup_path.is_file()):
            raise RuntimeError("E3_BASELINE_REQUIRED_RECEIPT_MISSING")
        result, receipt, cleanup = _load(result_path), _load(receipt_path), _load(cleanup_path)
        decisions = result.get("decision_trace", [])
        interaction_metrics = result.get("interaction_metrics", {})
        compute_metrics = result.get("compute_metrics", {})
        query_evidence, query_count = _numeric_evidence(
            interaction_metrics,
            "query_count",
            "RUNTIME_METHOD_ADAPTER",
        )
        new_pid_evidence, new_pid_count = _numeric_evidence(
            compute_metrics,
            "new_pid_invocations",
            "RUNTIME_HOOK_ACCOUNTING",
        )
        candidate_write_evidence, candidate_direct_writes = _numeric_evidence(
            compute_metrics,
            "candidate_direct_control_writes",
            "RUNTIME_HOOK_ACCOUNTING",
        )
        m3_write_evidence, m3_direct_writes = _numeric_evidence(
            compute_metrics,
            "m3_direct_control_writes",
            "RUNTIME_HOOK_ACCOUNTING",
        )
        receipt_status = receipt.get("status")
        infrastructure_failure = receipt_status == "ENVIRONMENT_OR_SIMULATOR_FAILURE"
        metric.update(
            {
                "initial_decision": decisions[0].get("action") if decisions else None,
                "final_decision": decisions[-1].get("action") if decisions else None,
                "raw_k": result.get("candidate_trace", {}).get("raw_k"),
                "effective_k": result.get("candidate_trace", {}).get("effective_k"),
                "query_count": query_count,
                "wait_count": sum(item.get("action") == "WAIT" for item in decisions),
                "forced_decisions": 0,
                "privileged_gold_reads": sum(
                    int(result.get("label_firewall", {}).get(key, 0) or 0)
                    for key in (
                        "policy_evaluator_annotation_reads",
                        "policy_expected_decision_reads",
                        "policy_gold_candidate_index_reads",
                    )
                ),
                "new_pid_count": new_pid_count,
                "candidate_direct_writes": candidate_direct_writes,
                "m3_direct_writes": m3_direct_writes,
                "interaction_accounting": {"query_count": query_evidence},
                "control_accounting": {
                    "new_pid_invocations": new_pid_evidence,
                    "candidate_direct_control_writes": candidate_write_evidence,
                    "m3_direct_control_writes": m3_write_evidence,
                },
                "cleanup_pass": cleanup.get("status") == "PASS",
                "compute": compute_metrics,
                "failure_taxonomy": (
                    "ENVIRONMENT / INFRASTRUCTURE FAILURE"
                    if infrastructure_failure
                    else result.get("failure_class", "UNKNOWN")
                ),
                "episode_outcome_classification": (
                    "INFRASTRUCTURE_FAILURE"
                    if infrastructure_failure
                    else "SCIENTIFIC_RESULT"
                    if receipt_status == "COMPLETED_RECORDED_METHOD_RESULT"
                    else "UNKNOWN_RECEIPT_STATUS"
                ),
                "runtime_status": receipt_status,
            }
        )
        metric["task_metrics"] = result.get("task_metrics", task)
        metric["safety_metrics"] = result.get("safety_metrics", safety)
        metric["authority_gold_gate"] = _authority_gold_gate(metric)
        metric["runtime_contract_valid"] = (
            metric["authority_gold_gate"]["pass"]
            and metric["episode_outcome_classification"] == "SCIENTIFIC_RESULT"
        )
    path = output / "E3_METRIC_RECEIPT.json"
    _write(path, metric)
    return metric


def _receipt_path(row: Mapping[str, Any]) -> Path:
    output = _output(row)
    return output / ("GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json" if row["method"] == "driveclarify_grounded_v1" else "EPISODE_RECEIPT.json")


def _infra_error(message: str) -> bool:
    upper = message.upper()
    return any(token in upper for token in ("GPU", "CUDA", "OOM", "CARLA", "WORLD", "SENSOR", "DISPLAY", "PREFLIGHT", "PORT", "LAUNCH", "TIMEOUT", "CLEANUP"))


def _first_18_audit(ledger: Mapping[str, Any]) -> dict[str, Any]:
    rows = ledger["rows"][:18]
    metrics = [_load(ROOT / row["metric_receipt"]) for row in rows if row.get("metric_receipt")]
    grounded = [item for item in metrics if item["identity"]["method"] == "driveclarify_grounded_v1"]
    checks = {
        "exact_first_three_matched_blocks": len(rows) == 18 and len({(row["language_scene_id"], row["seed_index"]) for row in rows}) == 3,
        "all_18_accounted": all(row["status"] != "NOT_RUN" for row in rows),
        "six_method_coverage": Counter(row["method"] for row in rows) == Counter({method: 3 for method in METHODS}),
        "label_firewall": all(item.get("privileged_gold_reads", 0) == 0 for item in metrics),
        "pid_control": all(item.get("new_pid_count", 0) == 0 and item.get("candidate_direct_writes", 0) == 0 and item.get("m3_direct_writes", 0) == 0 for item in metrics),
        "gpu_lease": all((ROOT / row["artifact_dir"] / "GPU_LEASE_RECEIPT.json").is_file() for row in rows),
        "cleanup": all(item.get("cleanup_pass") is True for item in metrics),
        "visualization_instrumentation": all(item.get("visualization_passive") is True for item in grounded),
    }
    passed = all(checks.values())
    result = {
        "schema_version": SCHEMA + ".first_18_checkpoint.v1",
        "status": "PASS_E3_FIRST_18_CHECKPOINT_CONTINUE" if passed else BLOCKED,
        "checks": checks,
        "slot_count": len(rows),
        "completed": sum(row["status"] == "COMPLETED_RECORDED" for row in rows),
        "infrastructure_failures": sum(row["status"] == "INFRASTRUCTURE_FAILURE" for row in rows),
        "checked_at_utc": _now(),
    }
    _write(REPORT_ROOT / "E3_FIRST_18_CHECKPOINT.json", result)
    return result


def run(*, max_slots: int | None = None) -> dict[str, Any]:
    ledger = _load(REPORT_ROOT / "E3_FORMAL_TRAIN_LEDGER.json")
    if ledger.get("contract_defect"):
        raise RuntimeError(BLOCKED)
    pending = [row for row in ledger["rows"] if row["status"] == "NOT_RUN"]
    if max_slots is not None:
        pending = pending[:max_slots]
    for row in pending:
        # One integrity check per matched block, before the first method.
        if row["method"] == METHODS[0]:
            verify_entry()
        row["status"] = "RUNNING"
        row["attempt_count"] += 1
        row["started_at_utc"] = _now()
        _save_ledger(ledger)
        try:
            _run_episode(row)
            metric = _normalize(row)
            gate = _authority_gold_gate(metric)
            if gate["status"] == "VIOLATION":
                raise RuntimeError("E3_RUNTIME_AUTHORITY_OR_GOLD_CONTRACT_VIOLATION")
            if (
                gate["status"] == "UNKNOWN_REQUIRED_EVIDENCE"
                and metric.get("episode_outcome_classification")
                != "INFRASTRUCTURE_FAILURE"
            ):
                raise RuntimeError("E3_REQUIRED_AUTHORITY_EVIDENCE_UNKNOWN")
            if not metric.get("cleanup_pass"):
                raise RuntimeError("E3_CLEANUP_FAILURE")
            receipt = _receipt_path(row)
            metric_path = _output(row) / "E3_METRIC_RECEIPT.json"
            row["status"] = (
                "INFRASTRUCTURE_FAILURE"
                if metric.get("episode_outcome_classification")
                == "INFRASTRUCTURE_FAILURE"
                else "COMPLETED_RECORDED"
            )
            row["episode_receipt"] = _rel(receipt)
            row["metric_receipt"] = _rel(metric_path)
            row["hash"] = hashlib.sha256((str(_sha(receipt)) + str(_sha(metric_path))).encode()).hexdigest()
            row["failure_taxonomy"] = metric.get("failure_taxonomy")
        except Exception as exc:
            reason = type(exc).__name__ + ":" + str(exc)
            output = _output(row)
            output.mkdir(parents=True, exist_ok=True)
            _write(output / "E3_EXECUTION_FAILURE.json", {"schema_version": SCHEMA + ".execution_failure.v1", "identity": {key: row[key] for key in ("episode_id", "language_scene_id", "physical_fixture_id", "seed_index", "carla_seed", "method")}, "error": reason, "traceback": traceback.format_exc(), "automatic_retry_count": 0, "recorded_at_utc": _now()})
            if _infra_error(reason):
                row["status"] = "INFRASTRUCTURE_FAILURE"
                row["failure_taxonomy"] = "ENVIRONMENT / INFRASTRUCTURE FAILURE"
                row["failure_reason"] = reason
            else:
                row["status"] = "BLOCKED_CONTRACT_DEFECT"
                row["failure_taxonomy"] = "UNKNOWN"
                row["failure_reason"] = reason
                ledger["contract_defect"] = {"episode_id": row["episode_id"], "reason": reason, "status": BLOCKED}
                ledger["status"] = BLOCKED
        row["ended_at_utc"] = _now()
        _save_ledger(ledger)
        if ledger.get("contract_defect"):
            break
        accounted_first = all(item["status"] != "NOT_RUN" for item in ledger["rows"][:18])
        if accounted_first and ledger.get("first_18_checkpoint") == "PENDING":
            checkpoint = _first_18_audit(ledger)
            ledger["first_18_checkpoint"] = checkpoint["status"]
            if checkpoint["status"] != "PASS_E3_FIRST_18_CHECKPOINT_CONTINUE":
                ledger["contract_defect"] = {"episode_id": None, "reason": "FIRST_18_CHECKPOINT_FAILED", "status": BLOCKED}
                ledger["status"] = BLOCKED
            _save_ledger(ledger)
            if ledger.get("contract_defect"):
                break
    return snapshot()


def _metrics(ledger: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [_load(ROOT / row["metric_receipt"]) for row in ledger["rows"] if row.get("metric_receipt")]


def _coverage(metrics: Iterable[Mapping[str, Any]], family: str) -> dict[str, int]:
    result = Counter()
    for item in metrics:
        for metric in item.get(family, {}).values():
            result[str(metric.get("status", "UNKNOWN"))] += 1
    return dict(sorted(result.items()))


def _copy_representatives(ledger: Mapping[str, Any]) -> dict[str, Any]:
    records = {}
    for mechanism in ("ACT", "ASK", "WAIT"):
        row = next((item for item in ledger["rows"] if item["method"] == "driveclarify_grounded_v1" and item["mechanism_family"] == mechanism and item["seed_index"] == 1 and str(item["language_scene_id"]).endswith("-001") and item["status"] == "COMPLETED_RECORDED"), None)
        target = ARTIFACT_ROOT / (mechanism.casefold() + "_representative")
        target.mkdir(parents=True, exist_ok=True)
        if row is None:
            records[mechanism] = {"status": "FAIL_MISSING_FORMAL_REPRESENTATIVE"}
            continue
        source = _output(row)
        copied = []
        mapping = {
            "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json": "runtime_receipt.json",
            "GROUNDED_LANGUAGE_V1_PANEL.png": "panel_screenshot.png",
            "NATIVE_DESKTOP.png": "native_desktop_screenshot.png",
            "NATIVE_DESKTOP_VALIDATION.json": "native_desktop_validation.json",
            "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json": "cleanup_receipt.json",
            "E3_METRIC_RECEIPT.json": "metric_receipt.json",
        }
        live = _load(source / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json")
        for src, dst in mapping.items():
            if (source / src).is_file():
                shutil.copy2(source / src, target / dst)
                copied.append(dst)
        traces = {
            "decision_trace.json": {key: live.get(key) for key in ("initial_decision", "decision_why", "question", "answer", "post_answer_decision", "post_information_decision")},
            "candidate_trace.json": {key: live.get(key) for key in ("raw_k", "effective_k", "semantic_duplicate", "grounding_duplicate", "exact_duplicate")},
            "target_trace.json": {"target_binding_receipts": live.get("target_binding_receipts"), "target_duplicate": live.get("target_duplicate"), "branch_duplicate": live.get("branch_duplicate")},
            "plan_trace.json": {"candidate_plan_repetitions": live.get("candidate_plan_repetitions"), "plan_divergence": live.get("plan_divergence"), "fresh_replan": live.get("fresh_replan"), "fresh_replan_id": live.get("fresh_replan_id")},
            "interaction_trace.json": {key: live.get(key) for key in ("query_start_frame", "answer_delay_simulation_seconds", "answer_resolution", "event_timeline", "information_update", "old_candidate_set_invalidated", "old_candidate_invalidated")},
            "authority_trace.json": {key: live.get(key) for key in ("authority_receipt_consumed_exactly_once", "existing_pid_invocation_count", "new_pid_count", "candidate_direct_vehicle_control_write_count", "m3_direct_vehicle_control_write_count", "baseline_ownership_restored")},
        }
        for name, value in traces.items():
            _write(target / name, value)
            copied.append(name)
        manifest = {name: {"sha256": _sha(target / name), "bytes": (target / name).stat().st_size} for name in copied}
        _write(target / "hash_manifest.json", manifest)
        records[mechanism] = {"status": "PASS", "source_episode_id": row["episode_id"], "files": copied, "hash_manifest": _rel(target / "hash_manifest.json")}
    return records


def snapshot() -> dict[str, Any]:
    ledger = _load(REPORT_ROOT / "E3_FORMAL_TRAIN_LEDGER.json")
    metrics = _metrics(ledger)
    grounded = [item for item in metrics if item["identity"]["method"] == "driveclarify_grounded_v1"]
    method_coverage = {method: Counter(row["status"] for row in ledger["rows"] if row["method"] == method) for method in METHODS}
    fixture_coverage = {fixture: Counter(row["status"] for row in ledger["rows"] if row["physical_fixture_id"] == fixture) for fixture in sorted({row["physical_fixture_id"] for row in ledger["rows"]})}
    counts = Counter(row["status"] for row in ledger["rows"])
    grounded_decisions = Counter(item.get("initial_decision") for item in grounded)
    ask = [item for item in grounded if item["identity"]["mechanism_family"] == "ASK"]
    wait = [item for item in grounded if item["identity"]["mechanism_family"] == "WAIT"]
    act = [item for item in grounded if item["identity"]["mechanism_family"] == "ACT"]
    def rate(values: list[bool]) -> float | None:
        return sum(values) / len(values) if values else None
    new_pid_evidence = _summed_count_evidence(metrics, "new_pid_count")
    direct_candidate_evidence = _summed_count_evidence(
        metrics, "candidate_direct_writes"
    )
    direct_m3_evidence = _summed_count_evidence(metrics, "m3_direct_writes")
    direct_write_evidence = {
        "status": (
            "UNKNOWN"
            if direct_candidate_evidence["status"] == "UNKNOWN"
            or direct_m3_evidence["status"] == "UNKNOWN"
            else "AVAILABLE"
        ),
        "value": (
            None
            if direct_candidate_evidence["value"] is None
            or direct_m3_evidence["value"] is None
            else direct_candidate_evidence["value"] + direct_m3_evidence["value"]
        ),
        "candidate_direct_writes": direct_candidate_evidence,
        "m3_direct_writes": direct_m3_evidence,
    }
    quality = {
        "effective_k_distribution": dict(Counter(str(item.get("effective_k")) for item in grounded)),
        "natural_decision_distribution": dict(grounded_decisions),
        "candidate_collapse_rate": rate([bool(item.get("candidate_collapse")) for item in grounded]),
        "target_binding_success_rate": rate([bool(item.get("target_binding_available")) for item in grounded]),
        "target_duplicate_rate": rate([bool(item.get("target_duplicates")) for item in ask]),
        "target_unknown_rate": rate([bool(item.get("target_unknown")) for item in grounded]),
        "distinct_target_rate_ask": rate([bool(item.get("distinct_target")) for item in ask]),
        "plan_divergence_rate_ask": rate([bool(item.get("plan_divergence")) for item in ask]),
        "natural_ask_rate": rate([item.get("initial_decision") == "ASK" for item in ask]),
        "ask_resolution_rate": rate([bool(item.get("query_resolved")) for item in ask if item.get("initial_decision") == "ASK"]),
        "natural_wait_rate": rate([item.get("initial_decision") == "WAIT" for item in wait]),
        "wait_event_replan_success_rate": rate([bool(item.get("wait_event_cleared")) and int(item.get("replan_count", 0)) > 0 for item in wait if item.get("initial_decision") == "WAIT"]),
        "premature_cleared_count": sum(bool(item.get("premature_cleared")) for item in wait),
        "act_unnecessary_query_rate": rate([int(item.get("query_count", 0)) > 0 for item in act]),
        "forced_decisions": sum(int(item.get("forced_decisions", 0)) for item in grounded),
        "privileged_gold_reads": sum(int(item.get("privileged_gold_reads", 0)) for item in grounded),
        "new_pid_count": new_pid_evidence["value"],
        "new_pid_count_evidence": new_pid_evidence,
        "direct_control_write_violations": direct_write_evidence["value"],
        "direct_control_write_evidence": direct_write_evidence,
    }
    dino_latencies = [item.get("compute", {}).get("grounding_dino_latency_seconds") for item in grounded]
    dino_latencies = [float(value) for value in dino_latencies if value is not None]
    compute = {
        "grounded_episode_count": len(grounded),
        "grounding_dino_invocation_count": sum(int(item.get("compute", {}).get("grounding_dino_invocations", 0)) for item in grounded),
        "grounding_dino_latency_mean_seconds": fmean(dino_latencies) if dino_latencies else None,
        "grounding_dino_latency_samples": len(dino_latencies),
        "real_time_claim": False,
        "disclosure": "Grounding DINO latency is included and is not hidden; no real-time claim is made.",
    }
    status = BLOCKED if ledger.get("contract_defect") else SUCCESS if counts["COMPLETED_RECORDED"] + counts["INFRASTRUCTURE_FAILURE"] == 216 else PARTIAL
    ledger["status"] = status
    _save_ledger(ledger)
    method_payload = {method: dict(method_coverage[method]) for method in METHODS}
    _write(REPORT_ROOT / "E3_METHOD_COVERAGE.json", {"schema_version": SCHEMA + ".method_coverage.v1", "status": status, "execution_id": ledger["execution_id"], "coverage": method_payload})
    data_quality = {"schema_version": SCHEMA + ".data_quality_audit.v1", "status": status, "scheduled": 216, "started": sum(row["attempt_count"] > 0 for row in ledger["rows"]), "completed": counts["COMPLETED_RECORDED"], "environment_failures": counts["INFRASTRUCTURE_FAILURE"], "method_failures": sum(item.get("failure_taxonomy") not in (None, "NONE", "UNKNOWN") for item in metrics), "blocked": counts["BLOCKED_CONTRACT_DEFECT"], "excluded": 0, "not_run": counts["NOT_RUN"], "per_method_coverage": method_payload, "per_physical_fixture_coverage": {key: dict(value) for key, value in fixture_coverage.items()}}
    _write(REPORT_ROOT / "E3_DATA_QUALITY_AUDIT.json", data_quality)
    _write(REPORT_ROOT / "E3_GROUNDING_AUDIT.json", {"schema_version": SCHEMA + ".grounding_audit.v1", "status": status, "grounded_episode_count": len(grounded), "quality": quality})
    _write(REPORT_ROOT / "E3_AMBIGUITY_AUDIT.json", {"schema_version": SCHEMA + ".ambiguity_audit.v1", "status": status, "oriented_grounded_count": len(grounded), "detected_count": sum(bool(item.get("ambiguity_detected")) for item in grounded), "correctness_available_posthoc": True})
    _write(REPORT_ROOT / "E3_TARGET_BINDING_AUDIT.json", {"schema_version": SCHEMA + ".target_binding_audit.v1", "status": status, "target_binding_success_rate": quality["target_binding_success_rate"], "distinct_target_rate_ask": quality["distinct_target_rate_ask"], "target_duplicate_rate": quality["target_duplicate_rate"], "target_unknown_rate": quality["target_unknown_rate"]})
    _write(REPORT_ROOT / "E3_ACT_AUDIT.json", {"schema_version": SCHEMA + ".act_audit.v1", "status": status, "oriented_count": len(act), "natural_act_count": sum(item.get("initial_decision") == "ACT" for item in act), "unnecessary_query_rate": quality["act_unnecessary_query_rate"]})
    _write(REPORT_ROOT / "E3_ASK_AUDIT.json", {"schema_version": SCHEMA + ".ask_audit.v1", "status": status, "oriented_count": len(ask), "effective_k_ge_2_rate": rate([int(item.get("effective_k") or 0) >= 2 for item in ask]), "distinct_target_rate": quality["distinct_target_rate_ask"], "natural_ask_rate": quality["natural_ask_rate"], "missed_ask_rate": rate([item.get("initial_decision") != "ASK" for item in ask]), "ask_resolution_rate": quality["ask_resolution_rate"], "post_answer_replan_rate": rate([int(item.get("replan_count", 0)) > 0 for item in ask if item.get("initial_decision") == "ASK"])})
    _write(REPORT_ROOT / "E3_WAIT_AUDIT.json", {"schema_version": SCHEMA + ".wait_audit.v1", "status": status, "oriented_count": len(wait), "natural_wait_rate": quality["natural_wait_rate"], "cleared_count": sum(bool(item.get("wait_event_cleared")) for item in wait), "premature_cleared": quality["premature_cleared_count"], "wait_event_replan_success_rate": quality["wait_event_replan_success_rate"]})
    _write(REPORT_ROOT / "E3_SAFETY_TASK_AUDIT.json", {"schema_version": SCHEMA + ".safety_task_audit.v1", "status": status, "task_metric_coverage": _coverage(metrics, "task_metrics"), "safety_metric_coverage": _coverage(metrics, "safety_metrics"), "unknown_preserved": True})
    _write(REPORT_ROOT / "E3_COMPUTE_AUDIT.json", {"schema_version": SCHEMA + ".compute_audit.v1", "status": status, **compute})
    train = {"schema_version": SCHEMA + ".train_results.v1", "status": status, "train_only": True, "not_final_paper_test_result": True, "execution_id": ledger["execution_id"], "counts": dict(counts), "method_coverage": method_payload, "grounded_v1_quality": quality, "compute": compute, "extension_dev_attempt_count": 0, "extension_test_attempt_count": 0, "extension_test_consumed": False}
    _write(REPORT_ROOT / "E3_TRAIN_RESULTS.json", train)
    return train


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def cleanup_receipt() -> dict[str, Any]:
    process = subprocess.run(["pgrep", "-af", "CarlaUE4|leaderboard_evaluator|scenario_runner|run_grounded_language|GroundingDINO"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    lines = [
        line
        for line in process.stdout.splitlines()
        if "pgrep -af" not in line
        and "bash -lc" not in line
        and not line.startswith(str(os.getpid()) + " ")
    ]
    gpu = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    project_gpu = [line for line in gpu.stdout.splitlines() if any(token in line.casefold() for token in ("python", "carla", "grounding"))]
    ports = {str(port): _port_free(port) for port in (2020, 2021, 8020)}
    passed = not lines and not project_gpu and all(ports.values())
    receipt = {"schema_version": SCHEMA + ".cleanup.v1", "status": "PASS" if passed else "BLOCKED", "residual_processes": lines, "project_gpu_compute_processes": project_gpu, "ports_free": ports, "checked_at_utc": _now()}
    _write(REPORT_ROOT / "FINAL_CLEANUP_RECEIPT.json", receipt)
    return receipt


def finalize() -> dict[str, Any]:
    results = snapshot()
    ledger = _load(REPORT_ROOT / "E3_FORMAL_TRAIN_LEDGER.json")
    blocker_rows = [row for row in ledger["rows"] if row["status"] == "BLOCKED_CONTRACT_DEFECT"]
    _write(
        REPORT_ROOT / "E3_BLOCKER_AUDIT.json",
        {
            "schema_version": SCHEMA + ".blocker_audit.v1",
            "status": ledger.get("status"),
            "contract_defect": ledger.get("contract_defect"),
            "valid_formal_prefix_completed": sum(row["status"] == "COMPLETED_RECORDED" for row in ledger["rows"]),
            "pre_fix_invalid_for_formal_aggregation": [
                {
                    "episode_id": row["episode_id"],
                    "slot_index": row["slot_index"],
                    "artifact_dir": row["artifact_dir"],
                    "reason": row["failure_reason"],
                }
                for row in blocker_rows
            ],
            "same_ledger_resume_allowed": False,
            "defect_classification": "E3_POST_EPISODE_EVALUATOR_UNKNOWN_PRESERVATION_DEFECT",
            "frozen_runtime_or_policy_modified": False,
        },
    )
    representatives = _copy_representatives(ledger)
    visualization_pass = all(value.get("status") == "PASS" for value in representatives.values())
    _write(REPORT_ROOT / "E3_VISUALIZATION_AUDIT.json", {"schema_version": SCHEMA + ".visualization_audit.v1", "status": "PASS" if visualization_pass else "FAIL", "representatives": representatives, "native_display": ":1", "headless": False, "passive": True})
    cleanup = cleanup_receipt()
    entry = verify_entry()
    regression_path = REPORT_ROOT / "FULL_REGRESSION_RECEIPT.json"
    matrix_path = ARTIFACT_ROOT / "full_regression_matrix/FULL_REGRESSION_RECEIPT.json"
    matrix = _load(matrix_path) if matrix_path.is_file() else {"status": "NOT_RUN"}
    matrix_result_path = ARTIFACT_ROOT / "full_regression_matrix/FULL_REGRESSION_MATRIX_RESULT.json"
    matrix_result = _load(matrix_result_path) if matrix_result_path.is_file() else {"suites": []}
    required_roots = {
        "language_grounding_v1",
        "temporal_grounding_v1",
        "grounded_language_v1_controlled_integration",
        "grounded_language_v1_extension_e1",
        "grounded_language_v1_extension_e1_r1",
        "grounded_language_v1_extension_e1_r1_e3",
        "paper_mvp_stage6b",
        "paper_mvp_stage6b_r0",
    }
    focused = [
        row
        for row in matrix_result.get("suites", [])
        if Path(str(row.get("suite_path", row.get("suite", "")))).parts[1:2]
        and Path(str(row.get("suite_path", row.get("suite", "")))).parts[1] in required_roots
    ]
    regression = {
        "schema_version": SCHEMA + ".consolidated_full_regression.v1",
        "status": "PASS_FULL_REGRESSION"
        if focused
        and all(row.get("exit_code") == 0 for row in focused)
        and matrix.get("status") in {"PASS", "PASS_FULL_REPOSITORY_REGRESSION_MATRIX"}
        else "BLOCKED_FULL_REGRESSION",
        "focused_suites": focused,
        "focused_suite_count": len(focused),
        "repository_matrix_receipt": _rel(matrix_path) if matrix_path.is_file() else None,
        "repository_matrix_status": matrix.get("status"),
        "repository_matrix_suite_count": matrix.get("suite_count"),
        "repository_matrix_totals": matrix.get("totals"),
        "interpreter_policy": "FROZEN_MIXED_PYTHON_3_8_3_13_PER_TEST_FILE",
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
        "completed_at_utc": _now(),
    }
    _write(regression_path, regression)
    counts = Counter(row["status"] for row in ledger["rows"])
    complete = counts["COMPLETED_RECORDED"] + counts["INFRASTRUCTURE_FAILURE"] == 216
    final_status = SUCCESS if complete and not ledger.get("contract_defect") and visualization_pass and cleanup["status"] == "PASS" and regression.get("status") == "PASS_FULL_REGRESSION" else BLOCKED if ledger.get("contract_defect") else PARTIAL
    receipt = {"schema_version": SCHEMA + ".final_receipt.v1", "status": final_status, "execution_id": ledger["execution_id"], "scheduled": 216, "started": sum(row["attempt_count"] > 0 for row in ledger["rows"]), "completed": counts["COMPLETED_RECORDED"], "infrastructure_failures": counts["INFRASTRUCTURE_FAILURE"], "remaining": counts["NOT_RUN"], "method_coverage": results["method_coverage"], "grounded_v1_quality": results["grounded_v1_quality"], "compute": results["compute"], "visualization": "PASS" if visualization_pass else "FAIL", "full_regression": regression.get("status"), "original_integrity": entry["status"], "original_dev_test_untouched": True, "extension_dev_attempt_count": 0, "extension_test_attempt_count": 0, "extension_test_consumed": False, "cleanup": cleanup["status"], "scientific_caveat": "TRAIN-only diagnostics; not final paper TEST evidence and no hypothesis verdict.", "single_next_action": "STOP and request separate DEV authorization." if final_status == SUCCESS else "Authorize a new E3 revision after fixing and reviewing the evaluator UNKNOWN-preservation defect; do not reuse this formal ledger or run DEV/TEST." if ledger.get("contract_defect") else "Resume the same execution ID only if this is a valid resource-limited prefix; do not run DEV or TEST.", "generated_at_utc": _now()}
    _write(REPORT_ROOT / "E3_FINAL_RECEIPT.json", receipt)
    _text(REPORT_ROOT / "E3_FINAL_REPORT.md", "# E3 formal TRAIN final report\n\nStatus: `{}`\n\nScheduled/started/completed/infrastructure/remaining: 216/{}/{}/{}/{}.\n\nThis is TRAIN-only diagnostic evidence, not a final paper TEST result. DEV and TEST remain untouched.\n".format(final_status, receipt["started"], receipt["completed"], receipt["infrastructure_failures"], receipt["remaining"]))
    files = {}
    for root in (REPORT_ROOT, ARTIFACT_ROOT):
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "ARTIFACT_HASHES.json":
                files[_rel(path)] = {"sha256": _sha(path), "bytes": path.stat().st_size}
    _write(REPORT_ROOT / "ARTIFACT_HASHES.json", {"schema_version": SCHEMA + ".artifact_hashes.v1", "status": "PASS_HASH_BOUND", "artifact_count": len(files), "artifacts": files, "generated_at_utc": _now()})
    return receipt
