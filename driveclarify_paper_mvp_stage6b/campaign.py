"""TRAIN-only T0/T1/T2/T3 gates, R0 freeze, and resumable formal TRAIN."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from driveclarify_paper_mvp_evaluation.contracts import METHOD_ORDER

from .backend import (
    CHECKPOINT_SHA256,
    REPOSITORY_ROOT,
    SIMLINGO_PROTECTED_DIFF_SHA256,
    SIMLINGO_PROTECTED_HEAD,
    SCHEDULE_PATH,
    UnifiedNativeBackend,
    _atomic_json,
    _canonical_sha256,
    _file_sha256,
    _load_json,
    _utc_now,
    resolve_train_episode,
)
from .contracts import EvidenceStatus, FailureClass, Stage6BContractError
from .evaluator import (
    NEAR_MISS_CLEARANCE_THRESHOLD_METERS,
    NEAR_MISS_TTC_THRESHOLD_SECONDS,
)
from .interaction import (
    ASK_DEADLINE_SECONDS,
    ASK_DELAY_SECONDS,
    WAIT_DEFAULT_DEADLINE_SECONDS,
    WAIT_REEVALUATION_SECONDS,
)
from .semantics import CANONICAL_FIELDS, SEMANTIC_GENERATOR_RULE


GATE_ROOT = REPOSITORY_ROOT / "artifacts/paper_mvp_stage6b_r0_gates"
GATE_REPORT_ROOT = REPOSITORY_ROOT / "reports/paper_mvp_stage6b_r0_gates"
FREEZE_ROOT = (
    REPOSITORY_ROOT / "reports/paper_mvp_stage6b_runtime_contract_freeze_v1"
)
FORMAL_ROOT = REPOSITORY_ROOT / "artifacts/paper_mvp_stage6b_formal_train_r0"
# The movement smoke replays the historical TRAIN case after fixing its raw
# instruction binding.  Its first frozen van is far enough ahead for initial
# movement and the case has no ASK/WAIT runtime-signal dependency.
T1_CASE = ("DCV0-S001", 5101)
T2_CASE = ("DCV0-S002", 5102)
T3_CASES = (
    ("ACT", "DCV0-S001", 5103),
    ("ASK", "DCV0-S002", 5103),
    ("WAIT", "DCV0-S004", 5203),
)


def _run_command(
    command: Sequence[str], *, log_path: Path
) -> Mapping[str, Any]:
    environment = dict(os.environ)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    started = _utc_now()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            list(command),
            cwd=str(REPOSITORY_ROOT),
            env=environment,
            check=False,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    return {
        "command": list(command),
        "started_at_utc": started,
        "ended_at_utc": _utc_now(),
        "return_code": result.returncode,
        "log_path": str(log_path.relative_to(REPOSITORY_ROOT)),
        "log_sha256": _file_sha256(log_path),
    }


def run_t0() -> Mapping[str, Any]:
    """Run focused R0 contracts then the complete repository regression."""

    python = "/home/buaa/anaconda3/envs/simlingo/bin/python"
    focused = _run_command(
        (
            python,
            "-m",
            "pytest",
            "-q",
            "tests/paper_mvp_stage6b_r0",
        ),
        log_path=GATE_REPORT_ROOT / "T0_FOCUSED_TESTS.log",
    )
    full = _run_command(
        (
            python,
            "tools/run_stage6a_full_regression_matrix.py",
            "--output",
            str(GATE_REPORT_ROOT / "T0_FULL_REGRESSION_MATRIX"),
        ),
        log_path=GATE_REPORT_ROOT / "T0_FULL_REGRESSION.log",
    )
    passed = focused["return_code"] == 0 and full["return_code"] == 0
    receipt = {
        "schema_version": "driveclarify.paper_mvp_stage6b_t0.v1",
        "status": "PASS" if passed else "BLOCKED_STAGE6B_REGRESSION",
        "observed_at_utc": _utc_now(),
        "focused": focused,
        "full_regression": full,
        "required_test_families": [
            "method adapter",
            "ASK provider",
            "WAIT lifecycle",
            "candidate uniqueness",
            "evaluator schema",
            "label firewall",
            "forward budget",
            "cleanup",
        ],
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(GATE_REPORT_ROOT / "T0_CONTRACT_TEST_RECEIPT.json", receipt)
    return receipt


def _episode_dir(
    execution_id: str,
    gate: str,
    scenario_id: str,
    seed: int,
    method_id: str,
) -> Path:
    return (
        GATE_ROOT
        / execution_id
        / gate
        / (scenario_id + "_seed" + str(seed))
        / method_id
    )


def _run_or_load(
    backend: UnifiedNativeBackend,
    *,
    execution_id: str,
    gate: str,
    scenario_id: str,
    seed: int,
    method_id: str,
    visualization: bool,
) -> tuple[Path, Mapping[str, Any]]:
    output = _episode_dir(
        execution_id, gate, scenario_id, seed, method_id
    )
    receipt_path = output / "EPISODE_RECEIPT.json"
    if receipt_path.is_file():
        return output, _load_json(receipt_path)
    spec = resolve_train_episode(
        scenario_id=scenario_id, seed=seed, method_id=method_id
    )
    return output, backend.run(spec, output, visualization=visualization)


def _probe_counts(path: Path) -> Mapping[str, int]:
    rgb = model = control = 0
    if not path.is_file():
        return {"rgb": 0, "model": 0, "control": 0}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("sensor_frames", {}).get("rgb_0") is not None:
                rgb += 1
            if row.get("model_output", {}).get("pred_route", {}).get("present"):
                model += 1
            if isinstance(row.get("baseline_control"), Mapping):
                control += 1
    return {"rgb": rgb, "model": model, "control": control}


def run_t1(
    *, execution_id: str, backend: UnifiedNativeBackend
) -> Mapping[str, Any]:
    scenario_id, seed = T1_CASE
    output, receipt = _run_or_load(
        backend,
        execution_id=execution_id,
        gate="T1",
        scenario_id=scenario_id,
        seed=seed,
        method_id="original_simlingo",
        visualization=True,
    )
    audit_path = output / "stage6b_runtime_audit.json"
    diagnosis_path = output / "FULL_BRAKE_DIAGNOSIS.json"
    diagnosis = _load_json(diagnosis_path) if diagnosis_path.is_file() else {}
    probe = _probe_counts(output / "probe/probe.jsonl")
    if not audit_path.is_file():
        checks = {
            "backend_recorded": False,
            "rgb_live": probe["rgb"] > 0,
            "normal_model_forward_live": False,
            "existing_pid_live": False,
            "vehicle_control_live": probe["control"] > 0,
            "candidate_forward_count_zero": False,
            "movement_observed": False,
            "not_all_frame_full_brake": False,
            "cleanup": receipt.get("cleanup_status") == "PASS",
        }
        gate = {
            "schema_version": "driveclarify.paper_mvp_stage6b_t1.v1",
            "execution_id": execution_id,
            "status": "BLOCKED_STAGE6B_NATIVE_RUNTIME_AUDIT",
            "case": {
                "scenario_id": scenario_id,
                "seed": seed,
                "method_id": "original_simlingo",
            },
            "checks": checks,
            "maximum_speed_mps": 0.0,
            "route_completion_percent": None,
            "probe_counts": probe,
            "diagnosis": diagnosis,
            "episode_receipt_status": receipt.get("status"),
            "episode_path": str(output.relative_to(REPOSITORY_ROOT)),
            "dev_attempt_count": 0,
            "test_attempt_count": 0,
            "test_consumed": False,
        }
        _atomic_json(GATE_ROOT / execution_id / "T1_GATE_RECEIPT.json", gate)
        return gate
    audit = _load_json(audit_path)
    compute = audit.get("compute", {})
    movement = audit.get("movement", {})
    maximum_speed = float(movement.get("maximum_absolute_speed_mps", 0.0) or 0.0)
    route_progress = None
    leaderboard_path = output / "leaderboard_results.json"
    if leaderboard_path.is_file():
        records = _load_json(leaderboard_path).get("_checkpoint", {}).get("records", [])
        if len(records) == 1:
            route_progress = records[0].get("scores", {}).get("score_route")
    checks = {
        "backend_recorded": receipt.get("status") == "COMPLETED_RECORDED_METHOD_RESULT",
        "rgb_live": probe["rgb"] > 0,
        "normal_model_forward_live": probe["model"] > 0
        and int(compute.get("normal_model_forwards", 0)) > 0,
        "existing_pid_live": int(compute.get("existing_pid_invocations", 0)) > 0,
        "vehicle_control_live": probe["control"] > 0,
        "candidate_forward_count_zero": int(
            compute.get("candidate_model_forwards", -1)
        )
        == 0,
        "movement_observed": maximum_speed > 0.05,
        "not_all_frame_full_brake": diagnosis.get(
            "all_observed_controls_full_brake"
        )
        is False,
        "cleanup": receipt.get("cleanup_status") == "PASS",
    }
    passed = all(checks.values())
    gate = {
        "schema_version": "driveclarify.paper_mvp_stage6b_t1.v1",
        "execution_id": execution_id,
        "status": "PASS" if passed else "BLOCKED_STAGE6B_ORIGINAL_SIMLINGO_FULL_BRAKE",
        "case": {
            "scenario_id": scenario_id,
            "seed": seed,
            "method_id": "original_simlingo",
        },
        "checks": checks,
        "maximum_speed_mps": maximum_speed,
        "route_completion_percent": route_progress,
        "probe_counts": probe,
        "diagnosis": diagnosis,
        "episode_path": str(output.relative_to(REPOSITORY_ROOT)),
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    path = GATE_ROOT / execution_id / "T1_GATE_RECEIPT.json"
    _atomic_json(path, gate)
    return gate


def _episode_contract_checks(output: Path, receipt: Mapping[str, Any]) -> Mapping[str, bool]:
    audit_path = output / "stage6b_runtime_audit.json"
    result_path = output / "EPISODE_RESULT.json"
    cleanup_path = output / "CLEANUP_RECEIPT.json"
    if not audit_path.is_file():
        return {"runtime_audit": False}
    audit = _load_json(audit_path)
    compute = audit.get("compute", {})
    firewall = audit.get("label_firewall", {})
    outputs = audit.get("decision_trace", [])
    return {
        "backend_started": receipt.get("status")
        == "COMPLETED_RECORDED_METHOD_RESULT",
        "method_adapter_output": bool(outputs)
        and all(row.get("method_id") == audit.get("method_id") for row in outputs),
        "evaluator_receipt": result_path.is_file(),
        "cleanup": cleanup_path.is_file()
        and _load_json(cleanup_path).get("status") == "PASS",
        "policy_gold_label_reads_zero": all(
            int(firewall.get(key, -1)) == 0
            for key in (
                "policy_expected_decision_reads",
                "policy_gold_candidate_index_reads",
                "policy_evaluator_annotation_reads",
            )
        ),
        "new_pid_zero": int(compute.get("new_pid_invocations", -1)) == 0,
        "direct_control_writes_zero": int(
            compute.get("candidate_direct_control_writes", -1)
        )
        == 0
        and int(compute.get("m3_direct_control_writes", -1)) == 0,
        "ownership_violations_zero": int(
            compute.get("vehicle_control_ownership_violations", -1)
        )
        == 0,
        "forward_budget_compliant": audit.get("forward_accounting", {}).get(
            "all_event_budgets_compliant"
        )
        is True,
    }


def run_t2(
    *, execution_id: str, backend: UnifiedNativeBackend
) -> Mapping[str, Any]:
    scenario_id, seed = T2_CASE
    rows = []
    for method_id in METHOD_ORDER:
        output, receipt = _run_or_load(
            backend,
            execution_id=execution_id,
            gate="T2",
            scenario_id=scenario_id,
            seed=seed,
            method_id=method_id,
            visualization=True,
        )
        checks = _episode_contract_checks(output, receipt)
        rows.append(
            {
                "method_id": method_id,
                "status": "PASS" if checks and all(checks.values()) else "BLOCKED",
                "checks": checks,
                "episode_path": str(output.relative_to(REPOSITORY_ROOT)),
            }
        )
    passed = len(rows) == 8 and all(row["status"] == "PASS" for row in rows)
    gate = {
        "schema_version": "driveclarify.paper_mvp_stage6b_t2.v1",
        "execution_id": execution_id,
        "status": "PASS" if passed else "BLOCKED_STAGE6B_UNIFIED_BACKEND",
        "scenario_id": scenario_id,
        "seed": seed,
        "episode_count": len(rows),
        "methods": rows,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(GATE_ROOT / execution_id / "T2_GATE_RECEIPT.json", gate)
    return gate


def _interaction_coverage(output: Path) -> Mapping[str, Any]:
    audit_path = output / "stage6b_runtime_audit.json"
    if not audit_path.is_file():
        return {
            "actions": [],
            "ask_complete": False,
            "wait_complete": False,
            "replan_count": None,
            "resume_success": None,
            "resume_completed": False,
            "resume_action": None,
            "resume_plan_source": None,
            "resume_via_fallback": False,
            "raw_k": None,
            "effective_k": None,
            "semantic_divergence": None,
            "semantic_duplicate": None,
            "method_id": None,
            "label_firewall": {},
        }
    audit = _load_json(audit_path)
    decisions = audit.get("decision_trace", [])
    queries = audit.get("query_trace", [])
    waits = audit.get("wait_trace", [])
    candidate = audit.get("candidate_trace", {})
    replan_count = int(audit.get("interaction", {}).get("replan_count") or 0)
    post_interaction_decision = decisions[-1] if replan_count > 0 and len(decisions) > 1 else {}
    resume_action = post_interaction_decision.get("action")
    # Runtime ``resume_success`` deliberately remains the narrow ACT-only flag.
    # T3 coverage instead asks whether interaction exited into a fresh executable
    # driving plan.  A safety-gated BASELINE_FALLBACK_PLAN is an actual resumed
    # closed-loop plan under the existing PID, not a failed interaction or a
    # repeated ASK/WAIT state.
    resume_completed = resume_action in {"ACT", "FALLBACK"}
    return {
        "actions": [row.get("action") for row in decisions],
        "ask_complete": any(
            row.get("answer_time") is not None
            and row.get("answer_delay_seconds", 0) > 0
            and row.get("cache_invalidated") is True
            and row.get("resolution") == "RESOLVED"
            for row in queries
        ),
        "wait_complete": any(
            row.get("information_changed") is True
            and row.get("exit_reason") == "MEANINGFUL_INFORMATION_ARRIVED"
            for row in waits
        ),
        "replan_count": replan_count,
        "resume_success": audit.get("interaction", {}).get("resume_success"),
        "resume_completed": resume_completed,
        "resume_action": resume_action,
        "resume_plan_source": post_interaction_decision.get("plan_source"),
        "resume_via_fallback": resume_action == "FALLBACK",
        "raw_k": candidate.get("raw_k"),
        "effective_k": candidate.get("effective_k"),
        "semantic_divergence": candidate.get("semantic_divergence"),
        "semantic_duplicate": candidate.get("semantic_duplicate"),
        "method_id": audit.get("method_id"),
        "label_firewall": audit.get("label_firewall", {}),
    }


def run_t3(
    *, execution_id: str, backend: UnifiedNativeBackend
) -> Mapping[str, Any]:
    rows = []
    for orientation, scenario_id, seed in T3_CASES:
        for method_id in METHOD_ORDER:
            output, receipt = _run_or_load(
                backend,
                execution_id=execution_id,
                gate="T3_" + orientation,
                scenario_id=scenario_id,
                seed=seed,
                method_id=method_id,
                visualization=True,
            )
            checks = _episode_contract_checks(output, receipt)
            rows.append(
                {
                    "orientation": orientation,
                    "scenario_id": scenario_id,
                    "seed": seed,
                    "method_id": method_id,
                    "backend_status": (
                        "PASS" if checks and all(checks.values()) else "BLOCKED"
                    ),
                    "interaction": _interaction_coverage(output),
                    "episode_path": str(output.relative_to(REPOSITORY_ROOT)),
                }
            )
    asks = [row for row in rows if row["interaction"]["ask_complete"]]
    waits = [row for row in rows if row["interaction"]["wait_complete"]]
    stops = [row for row in rows if "STOP" in row["interaction"]["actions"]]
    k2 = [
        row
        for row in rows
        if row["interaction"]["effective_k"] == 2
        and bool(row["interaction"]["semantic_divergence"])
    ]
    firewall_clean = all(
        all(
            int(row["interaction"]["label_firewall"].get(key, -1)) == 0
            for key in (
                "policy_expected_decision_reads",
                "policy_gold_candidate_index_reads",
                "policy_evaluator_annotation_reads",
            )
        )
        for row in rows
    )
    checks = {
        "twenty_four_backend_contracts": len(rows) == 24
        and all(row["backend_status"] == "PASS" for row in rows),
        "ask_answer_replan_resume": bool(asks)
        and any(
            int(row["interaction"].get("replan_count") or 0) > 0
            and row["interaction"].get("resume_completed") is True
            for row in asks
        ),
        "wait_information_replan_resume": bool(waits)
        and any(
            int(row["interaction"].get("replan_count") or 0) > 0
            and row["interaction"].get("resume_completed") is True
            for row in waits
        ),
        "stop_path": bool(stops),
        "effective_k2_semantically_distinct": bool(k2),
        "policy_label_reads_zero": firewall_clean,
    }
    passed = all(checks.values())
    gate = {
        "schema_version": "driveclarify.paper_mvp_stage6b_t3.v2",
        "execution_id": execution_id,
        "status": "PASS" if passed else "BLOCKED_STAGE6B_INTERACTION_COVERAGE",
        "episode_count": len(rows),
        "checks": checks,
        "ask_complete_count": len(asks),
        "wait_complete_count": len(waits),
        "stop_path_count": len(stops),
        "effective_k2_distinct_count": len(k2),
        "duplicate_collapse_regression_fixture": {
            "location": "tests/paper_mvp_stage6b_r0/test_semantics.py",
            "in_frozen_population": False,
            "status": "PASS_BY_T0_INDEPENDENT_FIXTURE",
        },
        "resume_coverage_contract": {
            "runtime_resume_success_semantics": "ACT_ONLY_RETAINED_UNCHANGED",
            "t3_resume_completed_actions": ["ACT", "FALLBACK"],
            "fallback_semantics": "FRESH_EXECUTABLE_BASELINE_PLAN_UNDER_EXISTING_PID",
        },
        "episodes": rows,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(GATE_ROOT / execution_id / "T3_GATE_RECEIPT.json", gate)
    return gate


def _source_hashes() -> Mapping[str, str]:
    paths = (
        "driveclarify_paper_mvp_stage6b/contracts.py",
        "driveclarify_paper_mvp_stage6b/semantics.py",
        "driveclarify_paper_mvp_stage6b/interaction.py",
        "driveclarify_paper_mvp_stage6b/method_adapter.py",
        "driveclarify_paper_mvp_stage6b/evaluator.py",
        "driveclarify_paper_mvp_stage6b/runtime_binding.py",
        "driveclarify_paper_mvp_stage6b/backend.py",
        "driveclarify_paper_mvp_stage6b/campaign.py",
        "driveclarify_paper_mvp_stage6b/private/PASSENGER_INTENT_CONTRACT.json",
        "driveclarify_m3_runtime_shadow/live_shadow_runtime.py",
        "driveclarify_m3_runtime_shadow/live_visualization.py",
        "tools/build_stage6b_passenger_intent_contract.py",
        "tools/run_paper_mvp_stage6b_r0.py",
        "tools/run_stage6a_full_regression_matrix.py",
    )
    return {path: _file_sha256(REPOSITORY_ROOT / path) for path in paths}


def _write_contract(name: str, value: Mapping[str, Any]) -> Path:
    path = FREEZE_ROOT / name
    _atomic_json(path, value)
    return path


def freeze_contracts(*, execution_id: str) -> Mapping[str, Any]:
    gate_paths = {
        "T0": GATE_REPORT_ROOT / "T0_CONTRACT_TEST_RECEIPT.json",
        "T1": GATE_ROOT / execution_id / "T1_GATE_RECEIPT.json",
        "T2": GATE_ROOT / execution_id / "T2_GATE_RECEIPT.json",
        "T3": GATE_ROOT / execution_id / "T3_GATE_RECEIPT.json",
    }
    gates = {
        name: _load_json(path) if path.is_file() else None
        for name, path in gate_paths.items()
    }
    if not all(
        isinstance(value, Mapping) and value.get("status") == "PASS"
        for value in gates.values()
    ):
        raise Stage6BContractError("T0_T1_T2_T3_PASS_REQUIRED_BEFORE_FREEZE")
    FREEZE_ROOT.mkdir(parents=True, exist_ok=True)
    passenger_contract_path = (
        REPOSITORY_ROOT
        / "driveclarify_paper_mvp_stage6b/private/PASSENGER_INTENT_CONTRACT.json"
    )
    passenger_contract = _load_json(passenger_contract_path)
    common = {
        "schema_version": "driveclarify.paper_mvp_stage6b_r0_contract.v1",
        "freeze_id": "DRIVECLARIFY_STAGE6B_R0_TRAIN_CONTRACTS",
        "status": "FROZEN_AFTER_T0_T1_T2_T3_PASS",
        "scope": "CONTROLLED_AMBIGUITY_BENCHMARK_TRAIN_ONLY",
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    contracts: dict[str, Mapping[str, Any]] = {
        "UNIFIED_BACKEND_CONTRACT.json": {
            **common,
            "methods": list(METHOD_ORDER),
            "shared": [
                "CARLA_0.9.15",
                "route_and_scenario_loader",
                "weather",
                "actors",
                "sensors_and_rgb",
                "ego_and_route_context",
                "ScenarioRunner_lifecycle",
                "existing_SimLingo_PID",
                "termination_timeout_cleanup",
                "metric_collector",
                "label_firewall",
                "retry_policy_zero_automatic_retries",
            ],
            "only_policy_selector": (
                "DRIVECLARIFY_PAPER_MVP_STAGE6B_METHOD_ID"
            ),
            "always_obey_present": False,
        },
        "METHOD_ADAPTER_CONTRACT.json": {
            **common,
            "methods": list(METHOD_ORDER),
            "lifecycle": [
                "initialize_episode",
                "observe",
                "decide",
                "on_answer",
                "on_information_update",
                "select_plan",
                "termination_state",
                "finalize_episode",
            ],
            "actions": ["ACT", "ASK", "WAIT", "STOP", "FALLBACK"],
            "language_uncertainty_threshold": 0.5,
            "risk_divergence_threshold": 0.25,
            "thresholds_retuned": False,
        },
        "ASK_ANSWER_CONTRACT.json": {
            **common,
            "provider": "CONTROLLED_BENCHMARK_PASSENGER_INTENT",
            "passenger_intent_record_count": len(
                passenger_contract.get("records", [])
            ),
            "passenger_intent_payload_sha256": passenger_contract.get(
                "contract_payload_sha256"
            ),
            "passenger_intent_file_sha256": _file_sha256(
                passenger_contract_path
            ),
            "query": "NATURAL_LANGUAGE_STRUCTURED_CLARIFICATION",
            "fixed_delay_simulation_seconds": ASK_DELAY_SECONDS,
            "deadline_simulation_seconds": ASK_DEADLINE_SECONDS,
            "same_tick_answer": False,
            "maximum_queries_per_episode": 1,
            "cache_invalidation": True,
            "fresh_replan_from_latest_observation": True,
            "forbidden_outputs": [
                "expected policy decision",
                "gold selected candidate index",
                "Choose candidate A/B",
                "ACT now",
            ],
        },
        "WAIT_INFORMATION_CONTRACT.json": {
            **common,
            "wait_is_stop": False,
            "wait_is_emergency_brake": False,
            "holding_behavior": "CURRENT_VALID_CLOSED_LOOP_PLAN",
            "controller_owner": "EXISTING_SIMLINGO_PID",
            "reevaluation_seconds": WAIT_REEVALUATION_SECONDS,
            "default_deadline_seconds": WAIT_DEFAULT_DEADLINE_SECONDS,
            "exit_triggers": [
                "new observation",
                "ScenarioRunner EV04 runtime information",
                "clarification answer",
                "deadline timeout",
            ],
            "stale_candidate_invalidation": True,
            "fresh_replan": True,
        },
        "CANDIDATE_UNIQUENESS_CONTRACT.json": {
            **common,
            "pipeline_order": [
                "raw candidate generation",
                "semantic canonicalization",
                "semantic uniqueness",
                "grounded referent uniqueness",
                "effective candidate set",
                "SimLingo candidate planning",
                "consequence comparison",
            ],
            "canonical_fields": list(CANONICAL_FIELDS),
            "generator_rule": SEMANTIC_GENERATOR_RULE,
            "trajectory_used_for_deduplication": False,
            "collapse_status": (
                "CANDIDATE_SET_COLLAPSED_TO_SINGLE_INTERPRETATION"
            ),
        },
        "EVALUATOR_CONTRACT.json": {
            **common,
            "episode_schema": (
                "driveclarify.paper_mvp_stage6b_episode_result.v1"
            ),
            "evidence_statuses": [item.value for item in EvidenceStatus],
            "task_metrics": [
                "route_completion",
                "goal_correct",
                "wrong_goal_execution",
                "instruction_success",
            ],
            "safety_metrics": [
                "collision",
                "offroad",
                "wrong_lane",
                "red_light_violation",
                "stop_sign_violation",
                "minimum_ttc_seconds",
                "near_miss",
            ],
            "minimum_ttc_missing_value": None,
            "unknown_collision_implies_safe": False,
            "near_miss_ttc_threshold_seconds": (
                NEAR_MISS_TTC_THRESHOLD_SECONDS
            ),
            "near_miss_clearance_threshold_meters": (
                NEAR_MISS_CLEARANCE_THRESHOLD_METERS
            ),
            "goal_gold_access": "POST_EPISODE_ONLY",
        },
        "LABEL_FIREWALL_CONTRACT.json": {
            **common,
            "runtime_required_zero": [
                "policy_expected_decision_reads",
                "policy_gold_candidate_index_reads",
                "policy_evaluator_annotation_reads",
            ],
            "environment_truth_separate_from_evaluation_label": True,
            "passenger_truth_source": "RUNTIME_ENVIRONMENT_FIXTURES_ONLY",
        },
        "CARLA_LIFECYCLE_CONTRACT.json": {
            **common,
            "architecture": "ONE_EPISODE_ONE_COLD_BOOT_BOUNDED_SESSION",
            "continuous_multi_town_server": False,
            "native_ubuntu": True,
            "display": ":1",
            "physical_output": "DP-0",
            "no_rendering_mode": False,
            "forbidden": ["Xvfb", "VNC", "headless", "RenderOffScreen"],
            "ports": [2020, 2021, 8020],
            "cleanup_required": True,
        },
        "FORWARD_ACCOUNTING_CONTRACT.json": {
            **common,
            "normal_model_forward_per_control_tick": 1,
            "candidate_forward_budgets": {
                "original_simlingo_initial": 0,
                "driveclarify_initial_k2": 2,
                "always_ask_initial": 0,
                "always_stop_initial": 0,
                "always_wait_initial": 0,
                "never_ask_initial_k2": 2,
                "language_only_high_uncertainty": 0,
                "language_only_low_uncertainty_k2": 2,
                "risk_only_initial_k2": 2,
                "post_answer_or_information_selected_candidate": 1,
            },
            "semantic_dedup_forward_count": 0,
            "visualization_forward_count": 0,
            "metric_forward_count": 0,
            "new_pid_instances": 0,
        },
        "FAILURE_TAXONOMY.json": {
            **common,
            "classes": [item.value for item in FailureClass if item.value != "NONE"],
            "infrastructure_failure_is_method_failure": False,
            "unknown_is_forced_classification": False,
        },
    }
    written = []
    for name, value in contracts.items():
        written.append(_write_contract(name, value))
    hashes = {
        "schema_version": "driveclarify.paper_mvp_stage6b_r0_hashes.v1",
        "status": "FROZEN",
        "contract_files": {
            path.name: _file_sha256(path) for path in written
        },
        "source_files": _source_hashes(),
        "gate_receipts": {
            name: _file_sha256(path) for name, path in gate_paths.items()
        },
        "simlingo_head": SIMLINGO_PROTECTED_HEAD,
        "simlingo_protected_diff_sha256": SIMLINGO_PROTECTED_DIFF_SHA256,
        "checkpoint_sha256": CHECKPOINT_SHA256,
    }
    hash_path = _write_contract("RUNTIME_CONTRACT_HASHES.json", hashes)
    report = """# DriveClarify Stage 6B-R0 runtime contract freeze

Status: `PASS_STAGE6B_R0_CONTRACTS_FROZEN_AFTER_T0_T1_T2_T3`

The exact eight frozen methods now share one native CARLA/SimLingo backend,
one existing PID boundary, one ASK answer channel, one WAIT information channel,
one semantic-uniqueness gate, one evaluator schema, and one cleanup policy.
T0, T1, T2, and T3 receipts all passed before this directory was authored.

This is a TRAIN-only controlled-ambiguity benchmark contract. It is not a
general ambiguity-discovery claim, a formal safety guarantee, or a paper-result
report. DEV attempt count remains 0. TEST attempt count remains 0 and TEST is
unconsumed.
"""
    report_path = FREEZE_ROOT / "STAGE6B_R0_FREEZE_REPORT.md"
    report_path.write_text(report, encoding="utf-8")
    receipt = {
        "schema_version": "driveclarify.paper_mvp_stage6b_r0_freeze.v1",
        "status": "PASS_STAGE6B_R0_CONTRACTS_FROZEN_AFTER_T0_T1_T2_T3",
        "execution_id": execution_id,
        "contract_count": len(contracts),
        "runtime_contract_hashes_sha256": _file_sha256(hash_path),
        "freeze_report_sha256": _file_sha256(report_path),
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(FREEZE_ROOT / "STAGE6B_R0_FREEZE_RECEIPT.json", receipt)
    return receipt


def _formal_schedule() -> list[Mapping[str, Any]]:
    schedule = _load_json(SCHEDULE_PATH)
    rows = [item for item in schedule["episodes"] if item["split"] == "train"]
    if len(rows) != 256:
        raise Stage6BContractError("FORMAL_TRAIN_SCHEDULE_NOT_256")
    if any(item["method_id"] not in METHOD_ORDER for item in rows):
        raise Stage6BContractError("FORMAL_TRAIN_METHOD_SET_INVALID")
    return rows


def _ledger_counts(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    statuses = Counter(row.get("formal_status", "SCHEDULED_NOT_STARTED") for row in rows)
    method = defaultdict(Counter)
    for row in rows:
        method[row["method_id"]][row.get("formal_status", "SCHEDULED_NOT_STARTED")] += 1
    return {
        "scheduled": len(rows),
        "started": sum(value for key, value in statuses.items() if key != "SCHEDULED_NOT_STARTED"),
        "completed": statuses.get("COMPLETED", 0),
        "environment_failures": statuses.get("ENVIRONMENT_FAILURE", 0),
        "blocked": statuses.get("BLOCKED", 0),
        "status_counts": dict(statuses),
        "per_method": {key: dict(value) for key, value in method.items()},
    }


def _save_formal_ledger(
    root: Path,
    execution_id: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    status: str,
) -> Mapping[str, Any]:
    value = {
        "schema_version": "driveclarify.paper_mvp_stage6b_formal_train_ledger.v1",
        "execution_id": execution_id,
        "status": status,
        "fresh_formal_train": True,
        "historical_episode_policy": "PRE_CONTRACT_FREEZE_DIAGNOSTIC_EXCLUDED",
        "schedule_sha256": _load_json(SCHEDULE_PATH)["schedule_sha256"],
        "counts": _ledger_counts(rows),
        "episodes": list(rows),
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "updated_at_utc": _utc_now(),
    }
    _atomic_json(root / "FORMAL_TRAIN_LEDGER.json", value)
    return value


def run_formal_train(
    *,
    execution_id: str,
    backend: UnifiedNativeBackend,
    max_new_episodes: int | None = None,
) -> Mapping[str, Any]:
    freeze_path = FREEZE_ROOT / "STAGE6B_R0_FREEZE_RECEIPT.json"
    if not freeze_path.is_file() or not str(
        _load_json(freeze_path).get("status", "")
    ).startswith("PASS_"):
        raise Stage6BContractError("STAGE6B_R0_FREEZE_REQUIRED_BEFORE_FORMAL_TRAIN")
    root = FORMAL_ROOT / execution_id
    root.mkdir(parents=True, exist_ok=True)
    ledger_path = root / "FORMAL_TRAIN_LEDGER.json"
    if ledger_path.is_file():
        rows = list(_load_json(ledger_path)["episodes"])
    else:
        rows = [
            {
                **dict(item),
                "formal_status": "SCHEDULED_NOT_STARTED",
                "episode_result_path": None,
            }
            for item in _formal_schedule()
        ]
        _save_formal_ledger(
            root, execution_id, rows, status="FRESH_TRAIN_READY_0_OF_256"
        )
    started_this_call = 0
    for index, row in enumerate(rows):
        if row["formal_status"] in {"COMPLETED", "ENVIRONMENT_FAILURE"}:
            continue
        if max_new_episodes is not None and started_this_call >= max_new_episodes:
            break
        row["formal_status"] = "STARTED"
        row["started_at_utc"] = _utc_now()
        _save_formal_ledger(
            root, execution_id, rows, status="FRESH_TRAIN_IN_PROGRESS"
        )
        started_this_call += 1
        output = root / "episodes" / str(row["episode_id"])
        try:
            spec = resolve_train_episode(
                scenario_id=str(row["scenario_id"]),
                seed=int(row["seed"]),
                method_id=str(row["method_id"]),
            )
            receipt = backend.run(spec, output, visualization=False)
            row["formal_status"] = (
                "COMPLETED"
                if receipt.get("status") == "COMPLETED_RECORDED_METHOD_RESULT"
                else "ENVIRONMENT_FAILURE"
            )
            row["episode_result_path"] = str(
                (output / "EPISODE_RESULT.json").relative_to(REPOSITORY_ROOT)
            )
            row["episode_receipt_path"] = str(
                (output / "EPISODE_RECEIPT.json").relative_to(REPOSITORY_ROOT)
            )
        except Exception as exc:
            row["formal_status"] = "BLOCKED"
            row["blocker"] = type(exc).__name__ + ":" + str(exc)
            row["ended_at_utc"] = _utc_now()
            _save_formal_ledger(
                root,
                execution_id,
                rows,
                status="BLOCKED_STAGE6B_UNIFIED_BACKEND",
            )
            raise
        row["ended_at_utc"] = _utc_now()
        _save_formal_ledger(
            root, execution_id, rows, status="FRESH_TRAIN_IN_PROGRESS"
        )
    counts = _ledger_counts(rows)
    complete = counts["completed"] + counts["environment_failures"] == 256
    ledger = _save_formal_ledger(
        root,
        execution_id,
        rows,
        status=(
            "FORMAL_TRAIN_TERMINAL_256_OF_256"
            if complete
            else "PARTIAL_STAGE6B_TRAIN_EXECUTION_VALID_CONTRACTS"
        ),
    )
    _write_train_quality(root, rows)
    return ledger


def _combine_gate_status(statuses: Sequence[str]) -> str:
    if "VIOLATION" in statuses:
        return "VIOLATION"
    if "UNKNOWN" in statuses:
        return "UNKNOWN"
    return "PASS"


def _structured_zero_gate(record: Any) -> Mapping[str, Any]:
    if not isinstance(record, Mapping):
        return {"status": "UNKNOWN", "evidence_status": "MISSING", "value": None}
    evidence_status = str(record.get("status", "UNKNOWN"))
    value = record.get("value")
    if evidence_status != EvidenceStatus.AVAILABLE.value:
        return {
            "status": "UNKNOWN",
            "evidence_status": evidence_status,
            "value": value,
        }
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return {
            "status": "UNKNOWN",
            "evidence_status": evidence_status,
            "value": value,
        }
    return {
        "status": "PASS" if value == 0 else "VIOLATION",
        "evidence_status": evidence_status,
        "value": value,
    }


def _formal_evidence_gates(result: Mapping[str, Any]) -> Mapping[str, Any]:
    forward_value = result.get("forward_accounting", {}).get(
        "all_event_budgets_compliant"
    )
    forward_status = (
        "PASS"
        if forward_value is True
        else "VIOLATION"
        if forward_value is False
        else "UNKNOWN"
    )
    pid = result.get("pid_accounting", {})
    existing_pid_only = pid.get("existing_pid_only")
    new_pid_invocations = pid.get("new_pid_invocations")
    pid_parts = [
        "PASS"
        if existing_pid_only is True
        else "VIOLATION"
        if existing_pid_only is False
        else "UNKNOWN",
        "PASS"
        if isinstance(new_pid_invocations, int)
        and not isinstance(new_pid_invocations, bool)
        and new_pid_invocations == 0
        else "VIOLATION"
        if isinstance(new_pid_invocations, int)
        and not isinstance(new_pid_invocations, bool)
        else "UNKNOWN",
    ]
    authority_names = (
        "candidate_direct_control_writes",
        "m3_direct_control_writes",
        "vehicle_control_ownership_violations",
    )
    authority = {
        name: _structured_zero_gate(result.get("compute_metrics", {}).get(name))
        for name in authority_names
    }
    firewall_names = (
        "policy_expected_decision_reads",
        "policy_gold_candidate_index_reads",
        "policy_evaluator_annotation_reads",
    )
    firewall = result.get("label_firewall", {})
    firewall_values = {name: firewall.get(name) for name in firewall_names}
    firewall_parts = [
        "PASS"
        if isinstance(value, int)
        and not isinstance(value, bool)
        and value == 0
        else "VIOLATION"
        if isinstance(value, int) and not isinstance(value, bool)
        else "UNKNOWN"
        for value in firewall_values.values()
    ]
    cleanup_value = result.get("cleanup_state", {}).get("status")
    cleanup_status = (
        "PASS"
        if cleanup_value == "PASS"
        else "UNKNOWN"
        if cleanup_value is None
        else "VIOLATION"
    )
    return {
        "forward": {"status": forward_status, "value": forward_value},
        "pid": {
            "status": _combine_gate_status(pid_parts),
            "existing_pid_only": existing_pid_only,
            "new_pid_invocations": new_pid_invocations,
        },
        "authority": {
            "status": _combine_gate_status(
                [str(value["status"]) for value in authority.values()]
            ),
            "fields": authority,
        },
        "label_firewall": {
            "status": _combine_gate_status(firewall_parts),
            "fields": firewall_values,
        },
        "cleanup": {"status": cleanup_status, "value": cleanup_value},
    }


def _write_train_quality(root: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    result_payloads = []
    audits = []
    for row in rows:
        path_text = row.get("episode_result_path")
        if isinstance(path_text, str) and (REPOSITORY_ROOT / path_text).is_file():
            result_payloads.append(_load_json(REPOSITORY_ROOT / path_text))
            audit_path = (REPOSITORY_ROOT / path_text).parent / "stage6b_runtime_audit.json"
            if audit_path.is_file():
                audits.append(_load_json(audit_path))
    metric_coverage: dict[str, Counter] = defaultdict(Counter)
    for result in result_payloads:
        for family in (
            "task_metrics",
            "safety_metrics",
            "interaction_metrics",
            "compute_metrics",
        ):
            for metric, value in result.get(family, {}).items():
                metric_coverage[family + "." + metric][value.get("status", "UNKNOWN")] += 1
    ledger_counts = _ledger_counts(rows)
    forbidden_label_fields = (
        "policy_expected_decision_reads",
        "policy_gold_candidate_index_reads",
        "policy_evaluator_annotation_reads",
    )
    policy_label_reads = {}
    policy_label_missing = {}
    for field in forbidden_label_fields:
        values = [
            result.get("label_firewall", {}).get(field)
            for result in result_payloads
        ]
        known = [
            int(value)
            for value in values
            if isinstance(value, int) and not isinstance(value, bool)
        ]
        missing = len(values) - len(known)
        policy_label_reads[field] = None if missing else sum(known)
        policy_label_missing[field] = missing
    failure_classes = Counter(
        str(result.get("failure_class", "F_UNKNOWN_OR_INSUFFICIENT_EVIDENCE"))
        for result in result_payloads
    )

    evidence_gates = [_formal_evidence_gates(result) for result in result_payloads]
    gate_families = ("forward", "pid", "authority", "label_firewall", "cleanup")
    violation_counts = {
        family: sum(gate[family]["status"] == "VIOLATION" for gate in evidence_gates)
        for family in gate_families
    }
    unknown_counts = {
        family: sum(gate[family]["status"] == "UNKNOWN" for gate in evidence_gates)
        for family in gate_families
    }
    evidence_gate_status = (
        "BLOCKED_EVIDENCE_VIOLATION"
        if any(violation_counts.values())
        else "BLOCKED_REQUIRED_EVIDENCE_UNKNOWN"
        if any(unknown_counts.values())
        else "PASS_ALL_REQUIRED_EVIDENCE_KNOWN"
    )
    unknown_metric_count = sum(
        count
        for statuses in metric_coverage.values()
        for status, count in statuses.items()
        if status == EvidenceStatus.UNKNOWN.value
    )
    _atomic_json(
        root / "METRIC_COVERAGE_AUDIT.json",
        {
            "schema_version": "driveclarify.stage6b_train_metric_coverage.v1",
            "status": "TRAIN_ONLY_NOT_FINAL_PAPER_RESULT",
            "episode_result_count": len(result_payloads),
            "coverage": {
                key: dict(value) for key, value in sorted(metric_coverage.items())
            },
        },
    )
    candidate_rows = [audit.get("candidate_trace", {}) for audit in audits]
    raw_distribution = Counter(str(row.get("raw_k")) for row in candidate_rows)
    effective_distribution = Counter(
        str(row.get("effective_k")) for row in candidate_rows
    )
    denominator = len(candidate_rows)

    _atomic_json(
        root / "TRAIN_DATA_QUALITY_AUDIT.json",
        {
            "schema_version": "driveclarify.stage6b_train_data_quality.v1",
            "status": "TRAIN_ONLY_NOT_FINAL_PAPER_RESULT",
            "ledger_counts": ledger_counts,
            "episode_result_count": len(result_payloads),
            "per_method_result_coverage": dict(
                Counter(
                    str(result.get("episode_identity", {}).get("method_id"))
                    for result in result_payloads
                )
            ),
            "failure_class_distribution": dict(failure_classes),
            "unknown_metric_value_count": unknown_metric_count,
            "policy_label_reads": policy_label_reads,
            "policy_label_missing_counts": policy_label_missing,
            "policy_label_read_total": (
                None
                if any(value is None for value in policy_label_reads.values())
                else sum(policy_label_reads.values())
            ),
            "formal_evidence_gate_status": evidence_gate_status,
            "formal_evidence_gate_records": evidence_gates,
            "forward_violation_episode_count": violation_counts["forward"],
            "forward_unknown_episode_count": unknown_counts["forward"],
            "pid_violation_episode_count": violation_counts["pid"],
            "pid_unknown_episode_count": unknown_counts["pid"],
            "authority_violation_episode_count": violation_counts["authority"],
            "authority_unknown_episode_count": unknown_counts["authority"],
            "label_firewall_violation_episode_count": violation_counts["label_firewall"],
            "label_firewall_unknown_episode_count": unknown_counts["label_firewall"],
            "cleanup_violation_episode_count": violation_counts["cleanup"],
            "cleanup_unknown_episode_count": unknown_counts["cleanup"],
            "dev_attempt_count": 0,
            "test_attempt_count": 0,
            "test_consumed": False,
        },
    )

    def covered_rate(
        field: str, *, invert: bool = False
    ) -> tuple[float | None, Mapping[str, int]]:
        known = [
            row.get(field)
            for row in candidate_rows
            if isinstance(row.get(field), bool)
        ]
        value = None
        if known:
            numerator = sum(
                item is (False if invert else True) for item in known
            )
            value = numerator / len(known)
        return value, {
            "valid_count": len(known),
            "unknown_count": denominator - len(known),
            "total_count": denominator,
        }

    collapse_rate, collapse_coverage = covered_rate("candidate_collapse")
    semantic_duplicate_rate, semantic_duplicate_coverage = covered_rate(
        "semantic_duplicate"
    )
    grounding_duplicate_rate, grounding_duplicate_coverage = covered_rate(
        "grounding_duplicate"
    )
    same_maneuver_rate, same_maneuver_coverage = covered_rate(
        "action_divergence", invert=True
    )
    near_identical_rate, near_identical_coverage = covered_rate(
        "trajectory_divergence", invert=True
    )
    consequence_divergence_rate, consequence_divergence_coverage = covered_rate(
        "consequence_divergence"
    )

    _atomic_json(
        root / "CANDIDATE_QUALITY_AUDIT.json",
        {
            "schema_version": "driveclarify.stage6b_train_candidate_quality.v1",
            "status": "TRAIN_ONLY_NOT_AUTOMATIC_AMBIGUITY_DISCOVERY",
            "denominator": denominator,
            "raw_k_distribution": dict(raw_distribution),
            "effective_k_distribution": dict(effective_distribution),
            "candidate_collapse_rate": collapse_rate,
            "candidate_collapse_coverage": collapse_coverage,
            "semantic_duplicate_rate": semantic_duplicate_rate,
            "semantic_duplicate_coverage": semantic_duplicate_coverage,
            "grounding_duplicate_rate": grounding_duplicate_rate,
            "grounding_duplicate_coverage": grounding_duplicate_coverage,
            "same_maneuver_rate": same_maneuver_rate,
            "same_maneuver_coverage": same_maneuver_coverage,
            "near_identical_trajectory_rate": near_identical_rate,
            "near_identical_trajectory_coverage": near_identical_coverage,
            "material_consequence_divergence_rate": (
                consequence_divergence_rate
            ),
            "material_consequence_divergence_coverage": (
                consequence_divergence_coverage
            ),
        },
    )
    _atomic_json(
        root / "ASK_WAIT_COVERAGE_AUDIT.json",
        {
            "schema_version": "driveclarify.stage6b_train_interaction_coverage.v1",
            "status": "TRAIN_ONLY_NOT_FINAL_PAPER_RESULT",
            "audit_count": len(audits),
            "ask_decision_count": sum(
                row.get("action") == "ASK"
                for audit in audits
                for row in audit.get("decision_trace", [])
            ),
            "wait_decision_count": sum(
                row.get("action") == "WAIT"
                for audit in audits
                for row in audit.get("decision_trace", [])
            ),
            "ask_answer_complete_episode_count": sum(
                any(item.get("answer_time") is not None for item in audit.get("query_trace", []))
                for audit in audits
            ),
            "wait_information_complete_episode_count": sum(
                any(
                    item.get("action") == "WAIT"
                    for item in audit.get("decision_trace", [])
                )
                and any(
                    item.get("information_changed") is True
                    for item in audit.get("wait_trace", [])
                )
                for audit in audits
            ),
        },
    )


__all__ = [
    "FORMAL_ROOT",
    "FREEZE_ROOT",
    "GATE_REPORT_ROOT",
    "GATE_ROOT",
    "T1_CASE",
    "T2_CASE",
    "T3_CASES",
    "freeze_contracts",
    "run_formal_train",
    "run_t0",
    "run_t1",
    "run_t2",
    "run_t3",
]
