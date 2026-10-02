#!/usr/bin/env python3
"""Independent, forward-free verifier for the one-time Formal M1 TEST event."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from driveclarify_learned_m1.formal_protocol import FormalProtocolError, publish_test_result, record_test_predictions, verify_seal
from driveclarify_learned_m1.formal_training import classification_metrics, sha256_file


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSESSMENT = REPO_ROOT / "reports/formal_learned_m1_assessment/DC-FORMAL-M1-ASSESS-20260804T072031Z"
TEST_SEAL = ASSESSMENT / "FORMAL_M1_TEST_SEAL.json"
EXPECTED_VERIFIER_SHA256 = "b0964285931040b15269218faee20a024b172e9233c95a84a244697162f99315"
EXPECTED_SEEDS = [17, 29, 43, 59, 71]
EXPECTED_EPOCHS = {17: 73, 29: 52, 43: 41, 59: 81, 71: 116}
EXPECTED_THRESHOLDS = {17: 0.30, 29: 0.30, 43: 0.30, 59: 0.30, 71: 0.60}
EXPECTED_CHECKPOINTS = {
    17: "4991935d69273e605c1d115ed3315c9c15b5bd185c9b22773e02954946229a9d",
    29: "dae6d2699f8ae109432ebc6a7e385df704f38ed0976ec735e712d2cc0a982127",
    43: "00189fa20da313b25147e43f4d85cb98ff2ba5fd377453a9e5742e2522720df9",
    59: "c5822798271566f455eaaad50c90324fd725814fc2388800e70c70286bb6fb91",
    71: "5847aeccc0cf9a49669d2cfcacf8747fd7fa4a5ba0c12a15bb5177ff02e45a8e",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def canonical_evidence_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def check(checks: Dict[str, bool], name: str, condition: bool) -> None:
    checks[name] = bool(condition)


def no_relevant_processes() -> bool:
    output = subprocess.check_output(["ps", "-eo", "pid,args"]).decode("utf-8", errors="replace")
    forbidden = ["CarlaUE4", "leaderboard_evaluator.py", "run_formal_learned_m1_training.py"]
    return not any(token in line for line in output.splitlines() for token in forbidden)


def verify(event_root: Path) -> Dict[str, Any]:
    checks: Dict[str, bool] = {}
    required = [
        "FORMAL_M1_TEST_REPORT.md", "TEST_EVENT_RESULT.json", "TEST_EVENT_MANIFEST.json", "FORMAL_TEST_COMPARISON_SET.json",
        "TEST_INPUT_INTEGRITY_AUDIT.json", "TEST_UNIT_LEVEL_PREDICTIONS.json", "TEST_RAW_PREDICTION_BYTES.sha256",
        "TEST_MAIN_MODEL_METRICS.json", "TEST_SEED_METRICS.json", "TEST_SEED_STABILITY.json", "TEST_CONFUSION_MATRICES.json",
        "TEST_CALIBRATION.json", "TEST_RISK_COVERAGE.json", "TEST_BOOTSTRAP_INTERVALS.json", "TEST_BASELINE_COMPARISON.json",
        "TEST_UNDEFINED_METRICS.json", "TEST_CLAIM_BOUNDARY.md", "TEST_SEAL_BEFORE.json", "TEST_SEAL_AFTER.json",
        "TEST_SEAL_TRANSITION_AUDIT.json", "FIRST_EVIDENCE_PUBLICATION.json", "TEST_ACCESS_AND_FORWARD_AUDIT.json",
        "COMMAND_LOG.md", "TEST_RESULTS.json", "MODIFIED_FILES.json", "GIT_START.json", "GIT_END.json",
        "PROCESS_AND_RESOURCE_CLEANUP.json", "NEXT_PROJECT_STAGE_RECOMMENDATION.md",
    ]
    check(checks, "required_deliverables_present", all((event_root / name).is_file() for name in required))
    before = load_json(event_root / "TEST_SEAL_BEFORE.json")
    after = load_json(event_root / "TEST_SEAL_AFTER.json")
    live = load_json(TEST_SEAL)
    verify_seal(before)
    verify_seal(after)
    verify_seal(live)
    check(checks, "entry_pretest_seal_status", before["status"] == "PRETEST_VERIFIED_AWAITING_ONE_TIME_TEST_AUTHORIZATION")
    check(checks, "entry_verifier_sha", before["pretest_verifier_sha256"] == EXPECTED_VERIFIER_SHA256)
    check(checks, "entry_counts_zero", before["test_evaluation_count"] == before["test_prediction_record_count"] == before["publication_count"] == 0)
    check(checks, "terminal_seal_exact", after == live and after["status"] == "FIRST_TEST_RESULT_PUBLISHED_IMMUTABLE" and after["test_evaluation_count"] == after["publication_count"] == 1)

    manifest = load_json(event_root / "TEST_EVENT_MANIFEST.json")
    comparison = load_json(event_root / "FORMAL_TEST_COMPARISON_SET.json")
    comparison_unsealed = dict(comparison)
    stored_comparison_hash = comparison_unsealed.pop("comparison_set_sha256")
    check(checks, "comparison_set_frozen_and_hash_correct", comparison["frozen_before_test_data_plane"] is True and canonical_sha256(comparison_unsealed) == stored_comparison_hash == manifest["comparison_set_sha256"])
    check(checks, "comparison_set_main_only", comparison["test_baselines_authorized"] is False and comparison["test_ablations_authorized"] is False and len(comparison["comparison_ids"]) == 5)
    check(checks, "event_identity_consistent", manifest["event_id"] == event_root.name == load_json(event_root / "TEST_EVENT_RESULT.json")["event_id"])

    evidence_path = event_root / "TEST_UNIT_LEVEL_PREDICTIONS.json"
    evidence_bytes = evidence_path.read_bytes()
    evidence = json.loads(evidence_bytes.decode("utf-8"))
    evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
    check(checks, "prediction_canonical_serialization", evidence_bytes == canonical_evidence_bytes(evidence))
    check(checks, "prediction_sha_bound", evidence_sha == after["test_prediction_sha256"] and evidence_sha in (event_root / "TEST_RAW_PREDICTION_BYTES.sha256").read_text())
    check(checks, "prediction_read_only", (evidence_path.stat().st_mode & 0o222) == 0)
    records = evidence["records"]
    check(checks, "prediction_record_count_55", evidence["record_count"] == len(records) == after["test_prediction_record_count"] == 55)
    check(checks, "record_order_exact", [row["record_order"] for row in records] == list(range(55)))
    units = []
    for row in records:
        if row["test_unit_identity"] not in units:
            units.append(row["test_unit_identity"])
    check(checks, "test_units_exact_11", len(units) == 11 and all(sum(row["test_unit_identity"] == unit for row in records) == 5 for unit in units))
    check(checks, "unit_then_seed_order", all([row["seed"] for row in records[index * 5 : index * 5 + 5]] == EXPECTED_SEEDS for index in range(11)))
    check(checks, "seed_epoch_threshold_checkpoint_binding", all(
        row["selected_epoch"] == EXPECTED_EPOCHS[row["seed"]]
        and float(row["frozen_unknown_threshold"]) == EXPECTED_THRESHOLDS[row["seed"]]
        and row["checkpoint_sha256"] == EXPECTED_CHECKPOINTS[row["seed"]]
        for row in records
    ))
    check(checks, "all_logits_and_probabilities_finite", all(
        all(math.isfinite(float(value)) for value in row["model_output"]["task_logits"] + row["model_output"]["task_probabilities"] + row["model_output"]["unknown_logits"] + row["model_output"]["unknown_probabilities_known_unknown"])
        for row in records
    ))
    check(checks, "targets_physically_separate", all("target" in row and "ground_truth_label" in row["target"] and "ground_truth_label" not in row["model_output"] and row["targets_passed_to_forward"] is False for row in records))
    check(checks, "test_label_counts_6_5_0", CounterLike(row["target"]["ground_truth_label"] for row in records[::5]) == {"TASK_EQUIVALENT": 6, "TASK_CRITICAL": 5})

    access = load_json(event_root / "TEST_ACCESS_AND_FORWARD_AUDIT.json")
    check(checks, "main_forwards_exact_55", access["model_forward_count"] == 55 and access["checkpoint_load_count"] == 5)
    check(checks, "no_training_tuning_or_ensemble", access["optimizer_step_count"] == access["backward_count"] == access["best_seed_selection_count"] == access["ensemble_count"] == 0)
    check(checks, "forbidden_tensor_fields_zero", access["forbidden_field_tensor_count"] == access["target_passed_to_forward_count"] == 0)

    first = load_json(event_root / "FIRST_EVIDENCE_PUBLICATION.json")
    publication = load_json(event_root / "TEST_RESULTS.json")
    result = load_json(event_root / "TEST_EVENT_RESULT.json")
    publication_sha = sha256_file(event_root / "TEST_RESULTS.json")
    check(checks, "first_evidence_before_metrics", first["summary_metrics_computed_before_publication"] == 0 and publication["metrics_started_after_first_evidence"] is True)
    check(checks, "publication_hash_bound", publication_sha == after["test_result_sha256"] == result["publication_artifact_sha256"])
    check(checks, "metric_hashes_reproduce", all(sha256_file(event_root / name) == digest for name, digest in publication["metric_artifact_hashes"].items()))

    seed_metrics = load_json(event_root / "TEST_SEED_METRICS.json")
    label_index = {"TASK_EQUIVALENT": 0, "TASK_CRITICAL": 1}
    metric_consistent = True
    for seed in EXPECTED_SEEDS:
        rows = [row for row in records if row["seed"] == seed]
        truth = [label_index[row["target"]["ground_truth_label"]] for row in rows]
        prediction = [max(range(2), key=lambda index: row["model_output"]["task_probabilities"][index]) for row in rows]
        recomputed = classification_metrics(truth, prediction)
        stored = seed_metrics[str(seed)]["task_head_known"]
        metric_consistent = metric_consistent and all(stored[name] == recomputed[name] for name in ("count", "correct", "accuracy", "balanced_accuracy", "macro_f1", "confusion_matrix_truth_rows_prediction_columns"))
        predicted_unknown = sum(float(row["model_output"]["unknown_probability"]) >= EXPECTED_THRESHOLDS[seed] for row in rows)
        metric_consistent = metric_consistent and seed_metrics[str(seed)]["abstention"]["predicted_positive"] == predicted_unknown
    check(checks, "metrics_recomputed_from_predictions", metric_consistent)
    undefined = load_json(event_root / "TEST_UNDEFINED_METRICS.json")
    check(checks, "undefined_unknown_metrics_exact", undefined["ground_truth_unknown_count"] == 0 and undefined["status"] == "NOT_ESTIMABLE_NO_POSITIVE_UNKNOWN" and all(seed_metrics[str(seed)]["abstention"][name] == "NOT_ESTIMABLE_NO_POSITIVE_UNKNOWN" for seed in EXPECTED_SEEDS for name in ("recall", "f1", "auroc", "auprc")))
    check(checks, "exact_numerators_denominators_present", all(seed_metrics[str(seed)]["task_head_known"]["count"] == 11 and seed_metrics[str(seed)]["coverage"]["overall_denominator"] == 11 for seed in EXPECTED_SEEDS))

    stability = load_json(event_root / "TEST_SEED_STABILITY.json")
    vectors = {seed: [row["model_output"]["final_prediction"] for row in records if row["seed"] == seed] for seed in EXPECTED_SEEDS}
    five_way = sum(len({vectors[seed][index] for seed in EXPECTED_SEEDS}) == 1 for index in range(11))
    check(checks, "seed_stability_consistent", stability["five_way_agreement_count"] == five_way and stability["no_best_seed_selected"] is True and stability["ensemble_used"] is False and len(stability["pairwise_prediction_agreement"]) == 10)
    bootstrap = load_json(event_root / "TEST_BOOTSTRAP_INTERVALS.json")
    check(checks, "bootstrap_protocol", bootstrap["resamples"] == 10000 and bootstrap["evaluation_seed"] == int(EXPECTED_VERIFIER_SHA256[:8], 16) and bootstrap["held_out_town_groups"] == 2 and len(bootstrap["intervals_by_seed"]) == 5)
    baseline = load_json(event_root / "TEST_BASELINE_COMPARISON.json")
    check(checks, "baseline_scope_and_claim", baseline["test_baseline_evaluation_performed"] is False and baseline["main_model_consistently_outperforms_every_baseline"] is False)
    claim = (event_root / "TEST_CLAIM_BOUNDARY.md").read_text(encoding="utf-8")
    check(checks, "claim_boundary", "FEASIBILITY_AND_PRELIMINARY_TOWN_DISJOINT_GENERALIZATION" in claim and "does not establish" in claim)

    transition = load_json(event_root / "TEST_SEAL_TRANSITION_AUDIT.json")
    check(checks, "duplicate_evaluation_fail_closed", transition["duplicate_evaluation_failed_closed"] is True)
    check(checks, "duplicate_publication_fail_closed", transition["duplicate_publication_failed_closed"] is True)
    cleanup = load_json(event_root / "PROCESS_AND_RESOURCE_CLEANUP.json")
    check(checks, "cpu_only_no_cuda_gpu", cleanup["cuda_visible_devices"] == "" and cleanup["torch_cuda_initialized"] is False and cleanup["gpu_compute_process_count"] == 0)
    check(checks, "process_cleanup", cleanup["cleanup_status"] == "PASS" and no_relevant_processes())
    git_start = load_json(event_root / "GIT_START.json")
    git_end = load_json(event_root / "GIT_END.json")
    check(checks, "git_history_integrity", git_start["driveclarify"]["head"] == git_end["driveclarify"]["head"] == "eaa332b1bb994279b59ea5af786fdb5de96adc1b" and git_end["driveclarify"]["tracked_diff_bytes"] == git_end["driveclarify"]["staged_diff_bytes"] == 0)
    check(checks, "simlingo_unchanged", git_start["simlingo"]["head"] == git_end["simlingo"]["head"] and git_start["simlingo"]["tracked_diff_sha256"] == git_end["simlingo"]["tracked_diff_sha256"])

    failures = [name for name, passed in checks.items() if not passed]
    return {
        "schema_version": "driveclarify.formal_m1_independent_test_review.v1",
        "event_id": event_root.name,
        "forward_free": True,
        "test_source_data_read": False,
        "required_check_count": len(checks),
        "required_pass_count": sum(checks.values()),
        "checks": checks,
        "failures": failures,
        "verdict": "PASS" if not failures else "FAIL",
    }


def CounterLike(values: Sequence[str]) -> Dict[str, int]:
    output: Dict[str, int] = {}
    for value in values:
        output[value] = output.get(value, 0) + 1
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("event_root")
    args = parser.parse_args()
    event_root = Path(args.event_root).resolve()
    result = verify(event_root)
    review_json = event_root / "INDEPENDENT_TEST_REVIEW.json"
    review_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    review_md = "# Independent Formal Learned M1 TEST Review\n\nResult: `%s` (`%d/%d` checks PASS).\n\nThis verifier was forward-free, read no TEST source data, recomputed the canonical prediction hash and registered metrics from immutable unit records, verified seal transitions and duplicate guards, and checked CPU/Git/SimLingo/cleanup boundaries.\n" % (result["verdict"], result["required_pass_count"], result["required_check_count"])
    (event_root / "INDEPENDENT_TEST_REVIEW.md").write_text(review_md, encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "passed": result["required_pass_count"], "total": result["required_check_count"], "failures": result["failures"]}, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
