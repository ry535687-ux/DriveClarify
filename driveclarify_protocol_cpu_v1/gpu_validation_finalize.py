"""Pure-CPU finalizer for preregistered GPU output artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .candidate_certification import certify_candidate_pair
from .contracts import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _without_hash(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def finalize(
    *, preregistration: Path, audit: Path, smoke: Path, freeze: Path,
    matrix: Path, cpu_validation: Path, opportunity_contracts: Path,
) -> dict[str, Any]:
    prereg = _load(preregistration)
    audit_value = _load(audit)
    smoke_value = _load(smoke)
    freeze_value = _load(freeze)
    matrix_value = _load(matrix)
    cpu_value = _load(cpu_validation)
    opportunity_value = _load(opportunity_contracts)
    schema = _load(ROOT / "schemas/METHOD_V1_R4_GPU_FORWARD_RECORD.schema.json")
    validator = Draft202012Validator(schema)
    records = matrix_value["records"]
    schedule = prereg["schedule"]
    record_errors: list[dict[str, Any]] = []
    record_hashes_exact = True
    output_hashes_exact = True
    for record in records:
        errors = sorted(error.message for error in validator.iter_errors(record))
        if errors:
            record_errors.append({"ordinal": record.get("ordinal"), "errors": errors})
        record_hashes_exact &= record["record_sha256"] == canonical_sha256(
            _without_hash(record, "record_sha256")
        )
        output_hashes_exact &= (
            record["route_output_sha256"] == canonical_sha256(record["route"])
            and record["speed_output_sha256"] == canonical_sha256(record["speed"])
            and record["combined_output_sha256"] == canonical_sha256(
                {"route": record["route"], "speed": record["speed"]}
            )
        )
    fixture_map = {row["fixture_id"]: row for row in prereg["fixtures"]}
    certifications = {}
    for fixture_id, metadata in fixture_map.items():
        fixture_records = [row for row in records if row["fixture_id"] == fixture_id]
        # R4.1 deliberately refuses to infer certification from a fixture name
        # or preregistered verdict.  Historical final artifacts remain immutable;
        # new certification is produced by the R4.1 evidence replay runner.
        certifications[fixture_id] = certify_candidate_pair(
            fixture_id, fixture_records, evidence=metadata.get("certification_evidence")
        )
    expected_classes = {fixture_id: {"UNKNOWN_INSUFFICIENT_EVIDENCE"} for fixture_id in fixture_map}
    class_checks = {
        fixture_id: certification["classification"] in expected_classes[fixture_id]
        for fixture_id, certification in certifications.items()
    }
    schedule_exact = len(records) == len(schedule) == 48 and all(
        int(record["ordinal"]) == int(row["ordinal"])
        and record["preregistered_schedule_row_sha256"] == row["schedule_row_sha256"]
        and record["candidate_id"] == row["candidate_id"]
        and record["semantic_digest"] == row["semantic_digest"]
        and record["input_bundle_sha256"] == row["input_bundle_sha256"]
        for record, row in zip(records, schedule)
    )
    checks = {
        "cpu_contract_validation_pass": cpu_value["status"] == "PASS",
        "decision_opportunity_cpu_contracts_pass": opportunity_value["status"] == "PASS",
        "decision_opportunity_contract_count_31": len(opportunity_value["records"]) == 31,
        "static_gpu_audit_pass": audit_value["status"] == "PASS",
        "one_noncounted_smoke_pass": smoke_value["status"] == "PASS" and smoke_value["engineering_smoke_forward_count"] == 1 and smoke_value["counted_matrix_forward_count"] == 0,
        "gpu_identity_frozen": freeze_value["status"] == "FROZEN_FOR_EXACT_48_COUNTED_MATRIX",
        "matrix_pass": matrix_value["status"] == "PASS",
        "exact_48_no_retry": matrix_value["actual_counted_forward_count"] == 48 and matrix_value["retry_forward_count"] == 0,
        "schedule_exact": schedule_exact,
        "all_forward_records_schema_valid": not record_errors,
        "all_record_hashes_exact": record_hashes_exact,
        "all_output_hashes_exact": output_hashes_exact,
        "all_candidate_classes_allowed": all(row["classification_allowed"] for row in certifications.values()),
        "all_fixture_expected_classes": all(class_checks.values()),
        "repeat_stability_closed": certifications["REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY"]["checks"]["repeat_stability"] == "PASS",
        "candidate_pair_certification_closed": all(class_checks.values()) and all(row["record_count"] == 8 for row in certifications.values()),
        "protected_hash_diff_exactly_two": cpu_value["checks"]["hash_diff_exactly_two_authorized"] is True,
        "historical_r3_identity_preserved": cpu_value["checks"]["historical_r3_aggregate_exact"] is True,
        "checkpoint_hash_unchanged_after": matrix_value["checks"]["checkpoint_hash_unchanged_after"] is True,
        "parameter_versions_unchanged": matrix_value["checks"]["parameter_versions_unchanged"] is True,
    }
    value = {
        "schema_version": "driveclarify.method_v1_r4.controlled_gpu_validation_final.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "classification_checks": class_checks,
        "candidate_pair_certifications": certifications,
        "record_schema_errors": record_errors,
        "forward_accounting": {
            "static_audit_forward_count": audit_value["official_model_forward_count"],
            "noncounted_engineering_smoke_forward_count": smoke_value["engineering_smoke_forward_count"],
            "counted_matrix_forward_count": matrix_value["actual_counted_forward_count"],
            "retry_forward_count": matrix_value["retry_forward_count"],
            "total_official_gpu_forward_count": smoke_value["engineering_smoke_forward_count"] + matrix_value["actual_counted_forward_count"],
            "carla_episode_count": 0,
            "scientific_attempt_count": 0,
            "dev_access_count": 0,
            "test_access_count": 0,
        },
        "identities": {
            "cpu_validation_sha256": cpu_value["result_sha256"],
            "decision_opportunity_contract_bundle_sha256": opportunity_value["contract_bundle_sha256"],
            "preregistration_sha256": prereg["preregistration_sha256"],
            "static_audit_sha256": audit_value["audit_sha256"],
            "smoke_sha256": smoke_value["smoke_sha256"],
            "gpu_execution_freeze_sha256": freeze_value["freeze_sha256"],
            "matrix_log_sha256": matrix_value["matrix_log_sha256"],
            "matrix_output_hash_sequence_sha256": matrix_value["output_hash_sequence_sha256"],
            "historical_r3_aggregate_sha256": cpu_value["hash_diff"]["old_aggregate_sha256"],
            "r4_candidate_64_file_aggregate_sha256": cpu_value["hash_diff"]["new_aggregate_sha256"],
        },
        "cpu_finalizer_scope": {
            "checkpoint_open_or_load_count": 0,
            "official_model_forward_count": 0,
            "cuda_context_creation_count": 0,
        },
    }
    value["validation_sha256"] = canonical_sha256(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--cpu-validation", type=Path, required=True)
    parser.add_argument("--opportunity-contracts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = finalize(
        preregistration=args.preregistration, audit=args.audit, smoke=args.smoke,
        freeze=args.freeze, matrix=args.matrix, cpu_validation=args.cpu_validation,
        opportunity_contracts=args.opportunity_contracts,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": value["status"], "validation_sha256": value["validation_sha256"], "classifications": {key: row["classification"] for key, row in value["candidate_pair_certifications"].items()}}))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
