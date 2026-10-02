"""Fail-closed evidence gate for the bounded v2 B4/B6 readiness smoke."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from driveclarify_t_mvp.firewall import BaselineId
from driveclarify_t_mvp_native_qualification.baseline_bindings import (
    EXPECTED_QUALNAMES,
)
from driveclarify_t_mvp_native_qualification.receipt_encoding import (
    reconstruct_frozen_admissibility_semantics,
    validate_frozen_admissibility_receipt,
)


def _load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _state_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"event_type": "MALFORMED_STATE_ROW"})
        else:
            rows.append(value)
    return rows


def _cp0_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            return []
    return rows


def _control_integrity(evidence: Path) -> dict[str, bool]:
    controls = [_load(path, {}) for path in sorted((evidence / "controls").glob("control_*.json"))]
    cp0 = _cp0_rows(evidence / "cp0_probe.jsonl")
    frames = [row.get("sim_frame") for row in controls]
    cp0_frames = [row.get("frame", {}).get("game_time_frame") for row in cp0]
    equivalence = _load(evidence / "cp0_equivalence.json", {})
    equivalence_present = bool(equivalence)
    return {
        "control_receipts_present": bool(controls),
        "one_inner_step_per_wrapper_cycle": bool(controls)
        and all(
            type(row.get("inner_step_before")) is int
            and row.get("inner_step_after") == row.get("inner_step_before") + 1
            for row in controls
        ),
        "zero_wrapper_control_computation_or_write": bool(controls)
        and all(
            row.get("wrapper_control_computation_count") == 0
            and row.get("wrapper_control_write_count") == 0
            for row in controls
        ),
        "one_control_receipt_per_frame": bool(frames)
        and None not in frames
        and len(frames) == len(set(frames)),
        "one_probe_record_per_control_frame": bool(cp0)
        and len(cp0_frames) == len(set(cp0_frames))
        and set(frames) == set(cp0_frames),
        "probe_uses_same_single_forward": bool(cp0)
        and all(
            row.get("model_output", {}).get("generated_from_same_forward") is True
            and row.get("model_output", {}).get("pred_route", {}).get("used_by_baseline_pid") is True
            for row in cp0
        ),
        "probe_induced_calls_zero_if_finalized": (
            not equivalence_present
            or all(
                equivalence.get(name) == 0
                for name in (
                    "probe_induced_model_calls",
                    "probe_induced_pid_calls",
                    "probe_induced_route_planner_steps",
                )
            )
        ),
        "single_forward_and_pid_if_finalized": (
            not equivalence_present
            or (
                bool(equivalence.get("forward_call_count_per_tick"))
                and set(equivalence["forward_call_count_per_tick"]) == {1}
                and bool(equivalence.get("pid_call_count_per_tick"))
                and set(equivalence["pid_call_count_per_tick"]) == {1}
            )
        ),
    }


def _truth_join(
    case_id: str, oracle: Mapping[str, Any], injection: Mapping[str, Any]
) -> tuple[bool, str | None]:
    reference = oracle.get("evaluator_truth_artifact")
    if not isinstance(reference, Mapping):
        return False, None
    path = Path(str(reference.get("absolute_path", "")))
    truth = _load(path, {})
    actual = injection.get("evaluator_truth_artifact", {})
    expected_class = (
        "OLD_EXCLUSIVE_RECOVERABLE"
        if oracle.get("timing_bucket") == "T3_POST_COMMIT_RECOVERABLE"
        else "NO_SAFE_CURRENT_OPPORTUNITY"
    )
    valid = (
        path.is_file()
        and reference.get("sha256") == _sha256(path)
        and truth.get("case_id") == case_id
        and truth.get("seed") is not None
        and truth.get("status") == "PASS"
        and truth.get("truth_class") == expected_class
        and truth.get("prospective_assertions", {}).get("frozen_before_native_launch") is True
        and truth.get("prospective_assertions", {}).get("result_dependent_relabeling_allowed") is False
        and actual.get("absolute_path") == str(path)
        and actual.get("sha256") == reference.get("sha256")
        and actual.get("truth_class") == expected_class
        and actual.get("frozen_before_native_launch") is True
    )
    return valid, truth.get("truth_class")


def _b4_checks(evidence: Path, expected: str) -> dict[str, bool]:
    dispatch = _load(evidence / "t_b4_dispatch.json", {})
    consumed = _load(evidence / "t_b4_local_consumption.json", {})
    return {
        "b4_receipt_serialized": bool(dispatch),
        "b4_exact_local_only_instance": dispatch.get("resolved_instance_type") == expected,
        "b4_zero_global_planner_calls": dispatch.get("global_planner_call_count") == 0,
        "b4_zero_reconnects": dispatch.get("reconnect_count") == 0,
        "b4_global_route_identity_unchanged": bool(dispatch)
        and dispatch.get("global_route_identity_before")
        == dispatch.get("global_route_identity_after"),
        "b4_global_route_generation_unchanged": bool(dispatch)
        and dispatch.get("global_route_generation_before")
        == dispatch.get("global_route_generation_after"),
        "b4_local_replacement_consumed_by_normal_forward": bool(consumed)
        and consumed.get("normal_forward_count_added") == 0
        and consumed.get("control_writer_count_added") == 0
        and consumed.get("target_interface")
        == "team_code.agent_simlingo.LingoAgent.tick target_point/next_target_point",
    }


def _b6_checks(evidence: Path, expected: str) -> tuple[dict[str, bool], dict[str, Any]]:
    dispatch = _load(evidence / "t_b6_dispatch.json", {})
    encoded = dispatch.get("frozen_admissibility")
    valid_encoding = False
    semantics: dict[str, Any] = {}
    if isinstance(encoded, Mapping):
        try:
            validate_frozen_admissibility_receipt(encoded)
            semantics = reconstruct_frozen_admissibility_semantics(encoded)
            valid_encoding = True
        except (KeyError, TypeError, ValueError):
            pass
    checks = {
        "b6_receipt_serialized": bool(dispatch),
        "b6_exact_frozen_instance": dispatch.get("resolved_instance_type") == expected,
        "b6_canonical_admissibility_encoding_valid": valid_encoding,
        "b6_all_fields_and_unknowns_preserved": valid_encoding
        and len(encoded.get("field_names", [])) == 10
        and bool(semantics.get("reason_codes")),
        "b6_actual_decision_present": isinstance(dispatch.get("decision"), Mapping)
        and bool(dispatch["decision"].get("outcome")),
        "b6_oracle_fields_absent": dispatch.get("oracle_fields_present") is False
        and semantics.get("oracle_fields_present") is False,
        "b6_runtime_bucket_absent": dispatch.get("runtime_config_has_t_bucket") is False,
        "b6_no_authored_commitment_index": dispatch.get("authored_commitment_point_index") is None,
        "b6_default_thresholds_only": dispatch.get("default_thresholds_only") is True,
    }
    return checks, semantics


def _retained_v1_checks(prior: Mapping[str, Any]) -> dict[str, bool]:
    gate = prior.get("gate_checks", {})
    return {
        "retained_b1_native_dispatch": gate.get("t_b1_dispatch") is True,
        "retained_b2_native_dispatch": gate.get("prior_t_b2_valid") is True,
        "retained_b3_native_dispatch": gate.get("t_b3_dispatch") is True,
        "retained_b5_native_dispatch_and_leak_zero": gate.get(
            "prior_t_b5_valid_and_leak_zero"
        )
        is True,
        "retained_t1_native_injection": gate.get("t1_native_injection") is True,
        "retained_t2_native_injection": gate.get("t2_native_injection") is True,
        "retained_t3_native_injection": gate.get("t3_native_injection") is True,
        "retained_t4_native_injection": gate.get("t4_native_injection") is True,
        "retained_p_old_atomic": gate.get("p_old_atomic") is True,
        "retained_p_new_detached": gate.get("p_new_prepare_detached") is True,
        "retained_b2_full_global_plus_local": gate.get(
            "t_b2_genuine_full_global_plus_local_replan"
        )
        is True,
        "retained_oracle_leakage_zero": gate.get("oracle_leakage_zero") is True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--prior-report", required=True)
    args = parser.parse_args()
    report = Path(args.report).resolve()
    prior_report = Path(args.prior_report).resolve()
    runtime = _load(report / "smoke/runtime_cases.json", {}).get("cases", {})
    oracles = _load(report / "smoke/oracle_injection_manifest.json", {}).get(
        "cases", {}
    )
    state = _state_rows(report / "raw/smoke_execution_state.jsonl")
    stability = _load(report / "CARLA_STABILITY_RULE.json", {})
    rows = []
    total_exposures = 0
    total_failures = 0
    all_attempts_cleanup = True
    all_failures_allowed = True
    no_post_agent_retry = True
    for case_id, config in runtime.items():
        oracle = oracles.get(case_id, {})
        attempts = sorted((report / "raw/smoke" / case_id).glob("attempt_*"))
        exposed = []
        failure_rows = [
            row
            for row in state
            if row.get("engineering_case_id") == case_id
            and row.get("event_type") == "PRE_AGENT_INFRA_FAILURE"
        ]
        total_failures += len(failure_rows)
        allowed = set(stability.get("allowed_pre_agent_failure_classes", []))
        all_failures_allowed = all_failures_allowed and all(
            row.get("reason_code") in allowed for row in failure_rows
        )
        seen_exposure = False
        per_attempt = []
        for root in attempts:
            process = _load(root / "process_job/PROCESS_RECEIPT.json", {})
            exposed_here = process.get("agent_exposed") is True
            if seen_exposure:
                no_post_agent_retry = False
            if exposed_here:
                seen_exposure = True
                exposed.append((root, process))
            cleanup_ok = (
                process.get("cleanup_pass") is True
                and process.get("ports_released") is True
                and process.get("owned_process_residue") == []
            )
            all_attempts_cleanup = all_attempts_cleanup and cleanup_ok
            per_attempt.append(
                {
                    "attempt": process.get("attempt_number"),
                    "agent_exposed": exposed_here,
                    "cleanup_pass": cleanup_ok,
                    "terminal_class": process.get("terminal_class"),
                    "reservation_id": process.get("reservation_id"),
                }
            )
        total_exposures += len(exposed)
        checks: dict[str, bool] = {
            "attempt_count_within_ceiling": 1 <= len(attempts)
            <= int(stability.get("maximum_identical_pre_agent_attempts_per_case", 0)),
            "exactly_one_agent_exposed_attempt": len(exposed) == 1,
            "all_attempts_cleanup_pass": all(row["cleanup_pass"] for row in per_attempt),
        }
        evidence_values: dict[str, Any] = {}
        if len(exposed) == 1:
            root, process = exposed[0]
            evidence = root / "agent_evidence"
            setup = _load(evidence / "agent_setup.json", {})
            dispatch = _load(evidence / "native_dispatch_exercised.json", {})
            injection = _load(evidence / "evaluator/injection_receipt.json", {})
            obligation = _load(evidence / "native_obligation_complete.json", {})
            expected = EXPECTED_QUALNAMES[BaselineId(config["baseline_id"])]
            checks.update(
                {
                    "five_consecutive_carla_readiness_checks": len(
                        _load(
                            root / "process_job/carla_readiness.json", {}
                        ).get("consecutive_rpc_world_checks", [])
                    )
                    == 5,
                    "exposed_attempt_cleanup_pass": process.get("cleanup_pass") is True,
                    "setup_exact_frozen_binding": setup.get("resolved_baseline_binding")
                    == expected,
                    "dispatch_exact_frozen_instance": dispatch.get("exact_instance_type")
                    == expected,
                    "dispatch_no_fallback": dispatch.get("fallback_used") is False,
                    "prospective_injection_before_policy": injection.get("receipt", {}).get(
                        "injected_before_policy"
                    )
                    is True,
                    "injection_bucket_join": injection.get("receipt", {}).get("bucket")
                    == oracle.get("timing_bucket"),
                    "injection_event_join": injection.get("receipt", {}).get(
                        "injection_event_id"
                    )
                    == oracle.get("injection_event_id"),
                    "update_event_join": injection.get("receipt", {}).get(
                        "update_event_id"
                    )
                    == oracle.get("update_event_id"),
                    "native_obligation_complete": obligation.get("baseline_id")
                    == config["baseline_id"],
                }
            )
            controls = _control_integrity(evidence)
            checks.update(controls)
            if config["baseline_id"] == "T-B4":
                checks.update(_b4_checks(evidence, expected))
            elif config["baseline_id"] == "T-B6":
                truth_ok, truth_class = _truth_join(case_id, oracle, injection)
                b6_checks, semantics = _b6_checks(evidence, expected)
                checks.update(b6_checks)
                checks["prospective_evaluator_truth_join"] = truth_ok
                evidence_values["evaluator_truth_class"] = truth_class
                evidence_values["frozen_admissibility_semantics"] = semantics
                evidence_values["decision"] = _load(evidence / "t_b6_dispatch.json", {}).get(
                    "decision"
                )
        status = "PASS" if checks and all(checks.values()) else "FAIL"
        rows.append(
            {
                "case_id": case_id,
                "seed": config.get("seed"),
                "baseline_id": config.get("baseline_id"),
                "timing_bucket": oracle.get("timing_bucket"),
                "attempt_count": len(attempts),
                "agent_exposure_count": len(exposed),
                "pre_agent_infrastructure_failure_count": len(failure_rows),
                "attempts": per_attempt,
                "checks": checks,
                "evidence": evidence_values,
                "status": status,
            }
        )
    prior = _load(prior_report / "NATIVE_READINESS_RECEIPT.json", {})
    retained = _retained_v1_checks(prior)
    freeze_check = subprocess.run(
        [
            "/home/buaa/anaconda3/envs/simlingo/bin/python",
            str(
                Path(__file__).resolve().parent / "verify_source_freeze.py"
            ),
            "--manifest",
            str(report / "PRACTICAL_RUNTIME_FREEZE.json"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    freeze_result = (
        json.loads(freeze_check.stdout)
        if freeze_check.returncode == 0 and freeze_check.stdout.strip()
        else {"status": "FAIL", "stderr": freeze_check.stderr}
    )
    case_checks = {
        "exact_three_fresh_smoke_cases": len(runtime) == 3 and len(rows) == 3,
        "only_targeted_b4_b6_exposed": {row["baseline_id"] for row in rows}
        == {"T-B4", "T-B6"},
        "required_buckets_present": {row["timing_bucket"] for row in rows}
        == {
            "T1_BEFORE_COMMITMENT",
            "T3_POST_COMMIT_RECOVERABLE",
            "T4_NO_SAFE_CURRENT_OPPORTUNITY",
        },
        "all_new_cases_pass": len(rows) == 3
        and all(row["status"] == "PASS" for row in rows),
        "exactly_three_total_agent_exposures": total_exposures == 3,
        "no_post_agent_retry": no_post_agent_retry,
        "all_attempts_cleanup_and_ports_released": all_attempts_cleanup,
        "pre_agent_failure_classes_allowed": all_failures_allowed,
        "pre_agent_failures_within_total_ceiling": total_failures
        <= int(stability.get("maximum_total_pre_agent_failures", -1)),
        "source_freeze_unchanged_after_smoke": freeze_result.get("status") == "PASS",
    }
    gate_checks = {**retained, **case_checks}
    passed = all(gate_checks.values())
    result = {
        "schema_version": "driveclarify.rq2.final_native_readiness.v2",
        "status": "PASS" if passed else "FAIL",
        "final_status": (
            "PASS_NATIVE_READINESS_AUTHORIZE_72_EPISODE_DEV"
            if passed
            else "FAIL_NATIVE_READINESS_STOP_BEFORE_DEV"
        ),
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=report.parents[1], text=True
        ).strip(),
        "gate_checks": gate_checks,
        "new_smoke_case_count": len(rows),
        "native_startup_attempt_count": sum(row["attempt_count"] for row in rows),
        "agent_exposed_smoke_episode_count": total_exposures,
        "pre_agent_infrastructure_failures": total_failures,
        "pre_agent_infrastructure_retries": total_failures,
        "scientific_retries": 0,
        "post_agent_retries": 0,
        "post_exposure_core_changes": 0,
        "oracle_leakage_count": 0 if all(
            row["checks"].get("b6_oracle_fields_absent", True) for row in rows
        ) else 1,
        "core_freeze_verification_after_smoke": freeze_result,
        "cases": rows,
        "dev_authorized": passed,
        "dev_seeds_selected": False,
        "dev_roster_instantiated": False,
        "dev_started": False,
    }
    _write_once(report / "NATIVE_READINESS_RECEIPT.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
