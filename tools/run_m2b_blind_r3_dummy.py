"""Generate persistent NONBLIND_DUMMY complete/partial R3 lifecycle evidence."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from driveclarify_m2b_event_runtime.publication import canonical_bytes, file_sha256
from driveclarify_m2b_event_runtime.r3_api import (
    authorize_gold_unseal,
    create_event,
    initialize_state_once,
    mark_prediction_started,
    mark_predictions_immutable,
    mark_results_immutable,
    publish_predictions,
    publish_results,
    record_gold_unseal,
)
from driveclarify_m2b_evaluator_runtime.r3_evaluator import evaluate_and_publish
from driveclarify_m2b_prediction_runtime.orchestrator import execute_r3_one_shot
from driveclarify_m2b_terminal_verifier.verifier import verify_terminal
from tests.m2b_blind_r3.test_r3_contract import COMPARISONS, _fixtures, _initialize_event, _predict, _write


def _expect_failure(name: str, function: Callable[[], Any], failures: list[dict[str, Any]]) -> None:
    try:
        function()
    except Exception as exc:
        failures.append({"guard": name, "status": "PASS", "exception": type(exc).__name__,
                         "message": str(exc)})
    else:
        failures.append({"guard": name, "status": "FAIL", "exception": None, "message": "NO_EXCEPTION"})


def _write_result(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value))
    path.chmod(0o400)


def run_dummy(output: Path, sandbox_audit_path: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="m2b-r3-dummy-complete-") as temporary_name:
        temporary = Path(temporary_name)
        fixture = _fixtures(temporary / "fixture")
        paths = _initialize_event(temporary / "event", fixture)
        outcome = execute_r3_one_shot(
            runtime_path=fixture["paths"]["runtime"], partition_path=fixture["paths"]["partition"],
            comparison_set_path=fixture["paths"]["comparisons"], hashes=fixture["hashes"],
            design_id="DUMMY-R3-COMPLETE", prediction_event_id="DUMMY-PRED-COMPLETE",
            journal_path=paths["journal.jsonl"], envelope_path=paths["envelope.json"],
            predictor=_predict,
            mark_prediction_started=lambda: mark_prediction_started(
                paths["state.json"], paths["event.json"], paths["audit.jsonl"], config=fixture["config"]),
        )
        # The fixture helper uses DUMMY-R3 identities in its state/config; the driver identity
        # remains the event identity and is intentionally not inferred from scientific data.
        publish_predictions(
            paths["state.json"], paths["audit.jsonl"], paths["predictions.json"],
            paths["envelope.json"].read_bytes(), completeness="COMPLETE",
            record_count=outcome["envelope"]["record_count"],
            expected_record_count=fixture["config"]["expected_record_count"],
        )
        mark_predictions_immutable(paths["state.json"], paths["audit.jsonl"], paths["predictions.json"])
        evaluated = evaluate_and_publish(
            state_path=paths["state.json"], audit_path=paths["audit.jsonl"],
            prediction_path=paths["predictions.json"], sealed_gold_path=fixture["paths"]["gold"],
            comparison_set_path=fixture["paths"]["comparisons"], runtime_path=fixture["paths"]["runtime"],
            result_path=paths["results.json"],
        )
        failures: list[dict[str, Any]] = []
        _expect_failure("duplicate_create_event", lambda: create_event(
            paths["state.json"], paths["event.json"], paths["tombstone.json"], paths["audit.jsonl"],
            blind_execution_id="DUMMY-SECOND", prediction_event_id="DUMMY-SECOND",
            config=fixture["config"], explicit_authorization=False, dummy_fixture=True), failures)
        _expect_failure("duplicate_mark_prediction_started", lambda: mark_prediction_started(
            paths["state.json"], paths["event.json"], paths["audit.jsonl"], config=fixture["config"]), failures)
        _expect_failure("duplicate_prediction_publication", lambda: publish_predictions(
            paths["state.json"], paths["audit.jsonl"], temporary / "other-prediction.json",
            paths["envelope.json"].read_bytes(), completeness="COMPLETE",
            record_count=outcome["envelope"]["record_count"],
            expected_record_count=fixture["config"]["expected_record_count"]), failures)
        _expect_failure("duplicate_prediction_immutable", lambda: mark_predictions_immutable(
            paths["state.json"], paths["audit.jsonl"], paths["predictions.json"]), failures)
        _expect_failure("duplicate_gold_authorization", lambda: authorize_gold_unseal(
            paths["state.json"], paths["audit.jsonl"], paths["predictions.json"]), failures)
        _expect_failure("duplicate_gold_unseal", lambda: record_gold_unseal(
            paths["state.json"], paths["audit.jsonl"], gold_sha256=file_sha256(fixture["paths"]["gold"]),
            gold_read_bytes=fixture["paths"]["gold"].stat().st_size), failures)
        _expect_failure("duplicate_result_publication", lambda: publish_results(
            paths["state.json"], paths["audit.jsonl"], temporary / "other-result.json",
            evaluated["results"]), failures)
        _expect_failure("duplicate_result_immutable", lambda: mark_results_immutable(
            paths["state.json"], paths["audit.jsonl"], paths["results.json"]), failures)
        _expect_failure("prediction_exact_resume", lambda: execute_r3_one_shot(
            runtime_path=fixture["paths"]["runtime"], partition_path=fixture["paths"]["partition"],
            comparison_set_path=fixture["paths"]["comparisons"], hashes=fixture["hashes"],
            design_id="DUMMY-R3", prediction_event_id="DUMMY-PRED",
            journal_path=paths["journal.jsonl"], envelope_path=paths["envelope.json"],
            predictor=_predict, prediction_start_precommitted=True), failures)
        duplicate_result = {
            "schema_version": "driveclarify.m2b_blind_r3_duplicate_guard_results.v1",
            "status": "PASS" if all(row["status"] == "PASS" for row in failures) else "FAIL",
            "failed_closed_count": sum(row["status"] == "PASS" for row in failures),
            "guards": failures,
        }
        duplicate_path = temporary / "duplicate.json"; _write(duplicate_path, duplicate_result)
        terminal = verify_terminal(
            state_path=paths["state.json"], event_manifest_path=paths["event.json"],
            audit_path=paths["audit.jsonl"], prediction_path=paths["predictions.json"],
            result_path=paths["results.json"], sandbox_audit_path=sandbox_audit_path,
            duplicate_guard_path=duplicate_path, gold_path=fixture["paths"]["gold"],
            expected={"blind_execution_id": "DUMMY-EXEC",
                      "expected_record_count": fixture["config"]["expected_record_count"],
                      "expected_comparison_count": len(COMPARISONS),
                      "track_fields": {"track_r": "track_r", "track_s_full_pool": "track_s_full_pool",
                                       "track_s_primary_core": "track_s_primary_core", "boundary_stress": "boundary_stress"},
                      "track_counts": {"track_r": 2, "track_s_full_pool": 2,
                                       "track_s_primary_core": 1, "boundary_stress": 2},
                      "git_history_integrity": True, "cleanup_status": "PASS"},
        )
        final_state = evaluated["state"]
        complete_result = {
            "schema_version": "driveclarify.m2b_blind_r3_complete_path_dummy.v1",
            "status": "PASS" if terminal["status"] == "PASS" else "FAIL",
            "fixture": "NONBLIND_DUMMY",
            "case_count": 4,
            "runtime_comparison_count": len(COMPARISONS),
            "prediction_record_count": outcome["envelope"]["record_count"],
            "prediction_completeness": outcome["envelope"]["completeness"],
            "prediction_sha256": final_state["prediction_sha256"],
            "prediction_bytes": final_state["prediction_bytes"],
            "event_consumed": final_state["event_consumed"],
            "gold_unseal_count": final_state["gold_unseal_count"],
            "metric_count": final_state["metric_count"],
            "result_sha256": final_state["result_sha256"],
            "terminal_verification": f"{terminal['checks_passed']}/{terminal['checks_total']} {terminal['status']}",
            "hypothesis_verdicts": {name: row["verdict"] for name, row in evaluated["results"]["hypotheses"].items()},
            "policy_execution_is_dummy_only": True,
            "real_blind_execution_count": 0,
            "real_gold_access_count": 0,
        }
        event_api_result = {
            "schema_version": "driveclarify.m2b_blind_r3_event_api_dummy.v1",
            "status": duplicate_result["status"],
            "event_create_count": final_state["event_create_count"],
            "prediction_start_count": final_state["prediction_start_count"],
            "prediction_publication_count": final_state["prediction_publication_count"],
            "gold_authorization_count": final_state["gold_authorization_count"],
            "gold_unseal_count": final_state["gold_unseal_count"],
            "result_publication_count": final_state["result_publication_count"],
            "results_immutable_count": final_state["results_immutable_count"],
            "duplicate_failed_closed_count": duplicate_result["failed_closed_count"],
        }
        _write_result(output / "M2B_BLIND_R2_COMPLETE_PATH_DUMMY_RESULTS.json", complete_result)
        _write_result(output / "M2B_BLIND_R2_EVENT_API_DUMMY_RESULTS.json", event_api_result)
        _write_result(output / "M2B_BLIND_R3_DUPLICATE_GUARD_RESULTS.json", duplicate_result)
        _write_result(output / "M2B_BLIND_R3_DUMMY_TERMINAL_VERIFICATION.json", terminal)

    with tempfile.TemporaryDirectory(prefix="m2b-r3-dummy-partial-") as temporary_name:
        temporary = Path(temporary_name)
        fixture = _fixtures(temporary / "fixture")
        paths = _initialize_event(temporary / "event", fixture)
        outcome = execute_r3_one_shot(
            runtime_path=fixture["paths"]["runtime"], partition_path=fixture["paths"]["partition"],
            comparison_set_path=fixture["paths"]["comparisons"], hashes=fixture["hashes"],
            design_id="DUMMY-R3-PARTIAL", prediction_event_id="DUMMY-PRED-PARTIAL",
            journal_path=paths["journal.jsonl"], envelope_path=paths["envelope.json"],
            predictor=_predict, interrupt_after_records=3,
            mark_prediction_started=lambda: mark_prediction_started(
                paths["state.json"], paths["event.json"], paths["audit.jsonl"], config=fixture["config"]),
        )
        publish_predictions(
            paths["state.json"], paths["audit.jsonl"], paths["predictions.json"],
            paths["envelope.json"].read_bytes(), completeness="PARTIAL",
            record_count=outcome["envelope"]["record_count"],
            expected_record_count=fixture["config"]["expected_record_count"],
        )
        state = mark_predictions_immutable(paths["state.json"], paths["audit.jsonl"], paths["predictions.json"])
        failures: list[dict[str, Any]] = []
        _expect_failure("partial_gold_gate", lambda: authorize_gold_unseal(
            paths["state.json"], paths["audit.jsonl"], paths["predictions.json"]), failures)
        _expect_failure("partial_exact_resume", lambda: execute_r3_one_shot(
            runtime_path=fixture["paths"]["runtime"], partition_path=fixture["paths"]["partition"],
            comparison_set_path=fixture["paths"]["comparisons"], hashes=fixture["hashes"],
            design_id="DUMMY-R3", prediction_event_id="DUMMY-PRED",
            journal_path=paths["journal.jsonl"], envelope_path=paths["envelope.json"],
            predictor=_predict, prediction_start_precommitted=True), failures)
        partial_result = {
            "schema_version": "driveclarify.m2b_blind_r3_partial_path_dummy.v1",
            "status": "PASS" if all(row["status"] == "PASS" for row in failures) else "FAIL",
            "fixture": "NONBLIND_DUMMY",
            "completeness": "PARTIAL",
            "record_count": outcome["envelope"]["record_count"],
            "expected_record_count": fixture["config"]["expected_record_count"],
            "event_consumed": state["event_consumed"],
            "prediction_sha256": state["prediction_sha256"],
            "prediction_bytes": state["prediction_bytes"],
            "gold_unseal_count": state["gold_unseal_count"],
            "crash_reason": outcome["envelope"]["crash_reason"],
            "records_preserved": outcome["envelope"]["record_count"] == 3,
            "guards": failures,
            "real_blind_execution_count": 0,
            "real_gold_access_count": 0,
        }
        _write_result(output / "M2B_BLIND_R2_PARTIAL_PATH_DUMMY_RESULTS.json", partial_result)
    with tempfile.TemporaryDirectory(prefix="m2b-r3-dummy-guards-") as temporary_name:
        temporary = Path(temporary_name)
        fixture = _fixtures(temporary / "fixture")
        paths = _initialize_event(temporary / "event", fixture)
        guards: list[dict[str, Any]] = []
        changed = dict(fixture["config"])
        changed["case_order_sha256"] = "f" * 64
        _expect_failure("changed_config_start", lambda: mark_prediction_started(
            paths["state.json"], paths["event.json"], paths["audit.jsonl"], config=changed), guards)
        mark_prediction_started(paths["state.json"], paths["event.json"], paths["audit.jsonl"],
                                config=fixture["config"])
        paths["state.json"].unlink()
        initialize_state_once(paths["state.json"], design_id="DUMMY-R3", parent_r2_id="DUMMY-R2",
                              blocked_r2_preflight_id="DUMMY-BLOCKED",
                              commitment_bundle_sha256="3" * 64)
        _expect_failure("deleted_state_tombstone_recreate", lambda: create_event(
            paths["state.json"], paths["event.json"], paths["tombstone.json"], paths["audit.jsonl"],
            blind_execution_id="DUMMY-RECREATE", prediction_event_id="DUMMY-RECREATE",
            config=fixture["config"], explicit_authorization=False, dummy_fixture=True), guards)
        guard_result = {
            "schema_version": "driveclarify.m2b_blind_r3_changed_config_tombstone_guard.v1",
            "status": "PASS" if all(row["status"] == "PASS" for row in guards) else "FAIL",
            "guards": guards, "real_event_count": 0,
        }
        _write_result(output / "M2B_BLIND_R3_CHANGED_CONFIG_AND_TOMBSTONE_GUARD_RESULTS.json", guard_result)
    return {"complete": complete_result, "partial": partial_result,
            "event_api": event_api_result, "duplicate": duplicate_result,
            "terminal": terminal, "changed_config_tombstone": guard_result}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sandbox-audit", type=Path, required=True)
    arguments = parser.parse_args()
    summary = run_dummy(arguments.output, arguments.sandbox_audit)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
