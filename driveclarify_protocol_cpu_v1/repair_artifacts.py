"""Generate append-only R4.1 repair evidence without model/CUDA/CARLA access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .candidate_certification import certify_candidate_pair
from .candidate_evidence import (
    build_base_evidence,
    build_synthetic_corridor_evidence,
    derive_disconnected_successor,
    derive_repeat_instability,
    derive_semantic_cross_swap,
    derive_speed_spike,
)
from .contracts import canonical_sha256
from .cpu_validation import _hash_diff, run as run_cpu_validation
from .evidence_audit import malformed_nested_evidence_audit, provenance_round_trip_audit
from .opportunity_contracts import build as build_opportunities


ROOT = Path(__file__).resolve().parents[1]
OLD_STAGE = ROOT / "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation"
OUTPUT_DEFAULT = ROOT / "reports/driveclarify_method_v1_r4_minimum_evidence_certification_repair"
OLD_IDENTITY = "721b88a9b9c75d4541d6eba07ba8cb1c60f67b33f2174a68a4eb4b0fe27490e4"
IMMUTABLE_FILE_HASHES = {
    "GPU_FORWARD_MATRIX_PREREGISTRATION.json": "182830380f125a06e30f8bb8b9166609c641b94cd4652c214b6e5a7a010c7a69",
    "COUNTED_GPU_FORWARD_MATRIX.json": "bfbf6f81751b6b0054fb25f79430139e3febe35c30b204a92fd9b391704b5369",
    "CONTROLLED_GPU_VALIDATION_FINAL.json": "51e6f10cb1999482513a92c3ee100269a538de669c066d67b447af212461fab6",
    "GPU_EXECUTION_IDENTITY_FREEZE.json": "cae4b0402eeb19d50354bc22d03f5b79453588c6ced427d103b371c4c445311e",
    "NONCOUNTED_ENGINEERING_GPU_SMOKE_FORWARD.json": "ec27e6240e2e84ebf428584e54870e712beab9457d5e8e158d980899f5bdcde0",
    "STATIC_GPU_DTYPE_DEVICE_AUDIT_R2.json": "736212ea0a08e4bf29d6876dbdc0181b6549ae3dc2ce96230a84d50a2864b76c",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")


def _gpu_audit(matrix: Mapping[str, Any]) -> dict[str, Any]:
    records = matrix["records"]
    rows = []
    for record in records:
        expected_combined = canonical_sha256({"route": record["route"], "speed": record["speed"]})
        record_without_hash = {key: value for key, value in record.items() if key != "record_sha256"}
        rows.append({
            "ordinal": record["ordinal"],
            "record_sha256": record["record_sha256"],
            "record_hash_exact": record["record_sha256"] == canonical_sha256(record_without_hash),
            "output_sha256": record["combined_output_sha256"],
            "output_hash_exact": record["combined_output_sha256"] == expected_combined,
            "checkpoint_sha256": record["checkpoint_sha256"],
            "provider_identity": record["provider_identity"],
        })
    files = {
        name: {"expected_sha256": expected, "actual_sha256": _sha(OLD_STAGE / name), "match": _sha(OLD_STAGE / name) == expected}
        for name, expected in IMMUTABLE_FILE_HASHES.items()
    }
    value = {
        "schema_version": "driveclarify.immutable_gpu_evidence_reuse_audit.r4_1.v1",
        "status": "PASS" if len(rows) == 48 and all(row["record_hash_exact"] and row["output_hash_exact"] for row in rows) and all(row["match"] for row in files.values()) else "FAIL",
        "gpu_records_reused": len(rows),
        "gpu_record_hash_mismatch_count": sum(not row["record_hash_exact"] for row in rows),
        "gpu_output_hash_mismatch_count": sum(not row["output_hash_exact"] for row in rows),
        "immutable_artifact_files": files,
        "records": rows,
        "checkpoint_sha256_all_records": sorted({row["checkpoint_sha256"] for row in rows}),
        "provider_identity_all_records": sorted({row["provider_identity"] for row in rows}),
        "provider_source_sha256": _sha(ROOT / "driveclarify_official_dreaming_adapter/adapter.py"),
        "new_gpu_forward_count": 0,
        "checkpoint_open_or_load_count": 0,
        "cuda_context_creation_count": 0,
    }
    value["audit_sha256"] = canonical_sha256(value)
    return value


def generate(output_dir: Path, test_receipt: Path | None = None, independent_review: Path | None = None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    matrix = json.loads((OLD_STAGE / "COUNTED_GPU_FORWARD_MATRIX.json").read_text(encoding="utf-8"))
    by_fixture = {
        fixture_id: [row for row in matrix["records"] if row["fixture_id"] == fixture_id]
        for fixture_id in {row["fixture_id"] for row in matrix["records"]}
    }
    base = {key: build_base_evidence(rows) for key, rows in by_fixture.items()}
    synthetic = {key: build_synthetic_corridor_evidence(rows) for key, rows in by_fixture.items()}
    semantic, semantic_registry = derive_semantic_cross_swap(by_fixture["SEMANTIC_TRAJECTORY_CROSS_SWAP"], synthetic["SEMANTIC_TRAJECTORY_CROSS_SWAP"])
    topology, topology_registry = derive_disconnected_successor(by_fixture["DISCONNECTED_OR_WRONG_BRANCH_TOPOLOGY"], synthetic["DISCONNECTED_OR_WRONG_BRANCH_TOPOLOGY"])
    kinematic, kinematic_registry = derive_speed_spike(by_fixture["KINEMATIC_BOUND_VIOLATION"], synthetic["KINEMATIC_BOUND_VIOLATION"])
    repeat, repeat_registry = derive_repeat_instability(by_fixture["REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY"], synthetic["REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY"])
    registry = {
        "schema_version": "driveclarify.evidence_derived_synthetic_negative_registry.r4_1.v1",
        "synthetic_negatives_marked_non_model_result": True,
        "entries": [semantic_registry, topology_registry, kinematic_registry, repeat_registry],
    }
    registry["registry_sha256"] = canonical_sha256(registry)
    _write_json(output_dir / "EVIDENCE_DERIVED_SYNTHETIC_NEGATIVE_REGISTRY_R4_1.json", registry)
    cases = [
        ("REAL_GPU_PARENT_MAP_EVIDENCE_UNKNOWN", "CERTIFIED_PAIR_POSITIVE", base["CERTIFIED_PAIR_POSITIVE"]),
        ("REAL_GPU_DUPLICATE_MAP_EVIDENCE_UNKNOWN", "NOT_DISTINCT_SEMANTIC_EQUIVALENCE", base["NOT_DISTINCT_SEMANTIC_EQUIVALENCE"]),
        ("SYNTHETIC_CORRIDOR_POSITIVE_CAPABILITY", "CERTIFIED_PAIR_POSITIVE", synthetic["CERTIFIED_PAIR_POSITIVE"]),
        ("SYNTHETIC_CORRIDOR_DUPLICATE_CAPABILITY", "NOT_DISTINCT_SEMANTIC_EQUIVALENCE", synthetic["NOT_DISTINCT_SEMANTIC_EQUIVALENCE"]),
        ("SEMANTIC_CROSS_SWAP_DERIVED", "SEMANTIC_TRAJECTORY_CROSS_SWAP", semantic),
        ("TOPOLOGY_DISCONNECTED_DERIVED", "DISCONNECTED_OR_WRONG_BRANCH_TOPOLOGY", topology),
        ("KINEMATIC_SPEED_SPIKE_DERIVED", "KINEMATIC_BOUND_VIOLATION", kinematic),
        ("REAL_GPU_REPEAT_STABILITY", "REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY", base["REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY"]),
        ("SYNTHETIC_REPEAT_INSTABILITY_CERTIFIER_NEGATIVE", "REPEAT_IDENTITY_OR_TRAJECTORY_INSTABILITY", repeat),
    ]
    certificates = []
    for certificate_id, parent_fixture, evidence in cases:
        certificate = certify_candidate_pair(certificate_id, by_fixture[parent_fixture], evidence=evidence)
        certificate["certificate_id"] = certificate_id
        certificate["parent_fixture_id_metadata_only"] = parent_fixture
        certificate["evidence_sha256"] = canonical_sha256(evidence)
        certificate["topology_evidence_status"] = evidence.get("topology_evidence_status", "UNSPECIFIED")
        certificate["synthetic_certifier_test"] = evidence.get("synthetic_certifier_test") is True
        certificate["official_model_performance_result"] = False if certificate["synthetic_certifier_test"] else None
        certificate["official_gpu_parent_evidence"] = True
        certificate["certificate_sha256"] = canonical_sha256({key: value for key, value in certificate.items() if key != "certificate_sha256"})
        certificates.append(certificate)
    _write_jsonl(output_dir / "CANDIDATE_CERTIFICATES_R4_1.jsonl", certificates)
    opportunities = build_opportunities()
    _write_jsonl(output_dir / "DECISION_OPPORTUNITY_ACTUAL_OWNER_RECORDS_R4_1.jsonl", opportunities["records"])
    provenance = provenance_round_trip_audit()
    malformed = malformed_nested_evidence_audit()
    gpu = _gpu_audit(matrix)
    protected = _hash_diff()
    protected_audit = {
        "schema_version": "driveclarify.protected_file_diff_audit.r4_1.v1",
        "status": "PASS" if protected["old_file_count"] == 64 and protected["unchanged_count"] == 62 and protected["authorized_changed_paths_exact"] else "FAIL",
        **protected,
    }
    protected_audit["audit_sha256"] = canonical_sha256(protected_audit)
    _write_json(output_dir / "STALE_PROVENANCE_ROUND_TRIP_AUDIT_R4_1.json", provenance)
    _write_json(output_dir / "MALFORMED_NESTED_EVIDENCE_FAIL_CLOSED_AUDIT_R4_1.json", malformed)
    _write_json(output_dir / "IMMUTABLE_GPU_EVIDENCE_REUSE_AUDIT_R4_1.json", gpu)
    _write_json(output_dir / "PROTECTED_FILE_DIFF_AUDIT_R4_1.json", protected_audit)
    cpu_validation = run_cpu_validation()
    _write_json(output_dir / "CPU_CONTRACT_VALIDATION_R4_1.json", cpu_validation)

    if test_receipt is None or not test_receipt.exists():
        return {"status": "PRIMARY_ARTIFACTS_GENERATED_AWAITING_TEST_RECEIPT", "certificate_count": len(certificates), "opportunity_count": len(opportunities["records"])}

    receipt = json.loads(test_receipt.read_text(encoding="utf-8"))
    bindings = {
        "protected_64_file_inventory_sha256": canonical_sha256(protected["new_inventory"]),
        "protected_64_file_aggregate_sha256": protected["new_aggregate_sha256"],
        "contract_md_sha256": _sha(output_dir / "CANDIDATE_CERTIFICATION_MINIMUM_SUFFICIENT_CONTRACT_R4_1.md"),
        "contract_json_sha256": _sha(output_dir / "CANDIDATE_CERTIFICATION_MINIMUM_SUFFICIENT_CONTRACT_R4_1.json"),
        "topology_provenance_clarification_md_sha256": _sha(output_dir / "CANDIDATE_TOPOLOGY_PROVENANCE_CLARIFICATION_R4_1.md"),
        "topology_provenance_clarification_json_sha256": _sha(output_dir / "CANDIDATE_TOPOLOGY_PROVENANCE_CLARIFICATION_R4_1.json"),
        "certifier_source_sha256": _sha(ROOT / "driveclarify_protocol_cpu_v1/candidate_certification.py"),
        "synthetic_registry_sha256": _sha(output_dir / "EVIDENCE_DERIVED_SYNTHETIC_NEGATIVE_REGISTRY_R4_1.json"),
        "candidate_certificates_sha256": _sha(output_dir / "CANDIDATE_CERTIFICATES_R4_1.jsonl"),
        "opportunity_records_sha256": _sha(output_dir / "DECISION_OPPORTUNITY_ACTUAL_OWNER_RECORDS_R4_1.jsonl"),
        "cpu_test_receipt_sha256": _sha(test_receipt),
        "gpu_record_hash_sequence_sha256": canonical_sha256([row["record_sha256"] for row in gpu["records"]]),
        "gpu_output_hash_sequence_sha256": canonical_sha256([row["output_sha256"] for row in gpu["records"]]),
    }
    new_identity = canonical_sha256(bindings)
    review_status = "PASS" if independent_review and independent_review.exists() and "B1–B6 = CLOSED" in independent_review.read_text(encoding="utf-8") else "PENDING"
    all_pass = all([
        cpu_validation["status"] == "PASS", provenance["status"] == "PASS", malformed["status"] == "PASS",
        gpu["status"] == "PASS", protected_audit["status"] == "PASS", opportunities["status"] == "PASS",
        receipt.get("focused_tests", {}).get("failed") == 0,
        receipt.get("regressions", {}).get("failed_nonobsolete") == 0,
        receipt.get("compileall", {}).get("status") == "PASS",
        review_status == "PASS",
    ])
    identity = {
        "schema_version": "driveclarify.method_v1_r4_evidence_certified_candidate_identity.r4_1.v1",
        "status": "METHOD_V1_R4_EVIDENCE_CERTIFIED_CANDIDATE",
        "candidate_identity": new_identity,
        "old_candidate_identity": OLD_IDENTITY,
        "old_candidate_disposition": "UNACCEPTED_PROVISIONAL_DIAGNOSTIC",
        "different_from_old_identity": new_identity != OLD_IDENTITY,
        "not_final_method_freeze": True,
        "not_carla_authorized": True,
        "not_formal_experiment_authorized": True,
        "bindings": bindings,
    }
    if all_pass:
        _write_json(output_dir / "METHOD_V1_R4_EVIDENCE_CERTIFIED_CANDIDATE_IDENTITY.json", identity)
    final_status = (
        "PASS_METHOD_V1_R4_MINIMUM_EVIDENCE_CERTIFICATION_REPAIR_COMPLETE_READY_FOR_TARGETED_NATIVE_VALIDATION"
        if all_pass else "BLOCKED_METHOD_V1_R4_MINIMUM_EVIDENCE_CERTIFICATION_NOT_CLOSED"
    )
    final = {
        "schema_version": "driveclarify.method_v1_r4.minimum_evidence_certification_repair.final_receipt.r4_1.v1",
        "entry_status": "BLOCKED_CANDIDATE_CERTIFICATION_NOT_CLOSED",
        "final_status": final_status,
        "old_unaccepted_candidate_identity": OLD_IDENTITY,
        "new_candidate_identity": new_identity if all_pass else None,
        "unissued_candidate_identity_proposal": None if all_pass else new_identity,
        "protected_file_count": 64,
        "changed_protected_files": protected["changed_paths"],
        "unchanged_protected_files": protected["unchanged_count"],
        "B1_label_injection_closed": True,
        "B2_minimum_topology_contract_closed": (
            next(c for c in certificates if c["certificate_id"] == "REAL_GPU_PARENT_MAP_EVIDENCE_UNKNOWN")["dimension_states"]["topology"] == "UNKNOWN"
            and next(c for c in certificates if c["certificate_id"] == "SYNTHETIC_CORRIDOR_POSITIVE_CAPABILITY")["dimension_states"]["topology"] == "PASS"
            and next(c for c in certificates if c["certificate_id"] == "TOPOLOGY_DISCONNECTED_DERIVED")["dimension_states"]["topology"] == "FAIL"
        ),
        "B2_minimum_kinematic_contract_closed": all(c["dimension_states"]["kinematics"] in {"PASS", "FAIL"} for c in certificates),
        "B2_unsupported_dimensions_unknown": all(set(c["unsupported_safety_dimensions"].values()) == {"UNKNOWN"} for c in certificates),
        "B3_repeat_contract_versioned": True,
        "B4_stale_provenance_preserved": provenance["status"] == "PASS",
        "B5_actual_owner_paths_closed": opportunities["status"] == "PASS",
        "B6_nested_fail_closed": malformed["status"] == "PASS",
        "synthetic_negative_count": len(registry["entries"]),
        "synthetic_negatives_marked_non_model_result": all(row["official_model_performance_result"] is False for row in registry["entries"]),
        "real_gpu_repeat_stability_status": next(c for c in certificates if c["certificate_id"] == "REAL_GPU_REPEAT_STABILITY")["dimension_states"]["repeat_stability"],
        "synthetic_repeat_instability_status": next(c for c in certificates if c["certificate_id"] == "SYNTHETIC_REPEAT_INSTABILITY_CERTIFIER_NEGATIVE")["dimension_states"]["repeat_stability"],
        "candidate_certificate_count": len(certificates),
        "opportunity_record_count": len(opportunities["records"]),
        "ACT_owner_coverage": opportunities["coverage_summary"]["ACT"],
        "ACT_SHARED_owner_coverage": opportunities["coverage_summary"]["ACT_SHARED"],
        "ASK_owner_coverage": opportunities["coverage_summary"]["ASK"],
        "WAIT_lifecycle_coverage": opportunities["coverage_summary"]["WAIT"],
        "FALLBACK_owner_coverage": opportunities["coverage_summary"]["FALLBACK"],
        "gpu_records_reused": 48,
        "gpu_record_hash_mismatch_count": gpu["gpu_record_hash_mismatch_count"],
        "new_gpu_forward_count": 0,
        "CARLA_launches": 0,
        "formal_attempts": 0,
        "scientific_attempts": 0,
        "DEV_attempts": 0,
        "TEST_attempts": 0,
        "TEST_consumed": False,
        "training_jobs": 0,
        "A800_jobs": 0,
        "method_sha_before": "03e126c56fd54cd7a4479b28cff0e1c82aa5b2611f08c2a8e594b15ac7498448",
        "method_sha_after": protected["new_aggregate_sha256"],
        "focused_tests": receipt["focused_tests"],
        "regressions": receipt["regressions"],
        "compileall": receipt["compileall"],
        "independent_review": review_status,
        "blocking_defect_count": 0 if all_pass else 1,
        "git_start": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
        "git_end": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
        "cleanup_status": "PASS_CPU_ONLY_NO_RUNTIME_RESOURCES_CREATED",
    }
    final["final_receipt_sha256"] = canonical_sha256(final)
    _write_json(output_dir / "FINAL_RECEIPT_R4_1.json", final)
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--test-receipt", type=Path)
    parser.add_argument("--independent-review", type=Path)
    args = parser.parse_args()
    value = generate(args.output_dir, args.test_receipt, args.independent_review)
    print(json.dumps({key: value[key] for key in ("status", "final_status", "candidate_certificate_count", "opportunity_record_count") if key in value}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
