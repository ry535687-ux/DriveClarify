"""Build the non-final Method V1 R4 GPU-validated candidate identity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(cpu_path: Path, gpu_path: Path) -> dict[str, Any]:
    cpu = json.loads(cpu_path.read_text(encoding="utf-8"))
    gpu = json.loads(gpu_path.read_text(encoding="utf-8"))
    if cpu["status"] != "PASS" or gpu["status"] != "PASS":
        raise RuntimeError("CPU_OR_GPU_VALIDATION_NOT_PASS")
    evidence_paths = [
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/CPU_CONTRACT_VALIDATION.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/CPU_REGRESSION_RECEIPT.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/DECISION_OPPORTUNITY_CPU_CONTRACTS.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/FINAL_CPU_TEST_AND_COMPILE_RECEIPT.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/GPU_FORWARD_MATRIX_PREREGISTRATION.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/STATIC_GPU_DTYPE_DEVICE_AUDIT.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/GPU_STATIC_AUDIT_R0_DIAGNOSTIC_INTERPRETATION.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/STATIC_GPU_DTYPE_DEVICE_AUDIT_R1.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/GPU_STATIC_AUDIT_R1_DIAGNOSTIC_INTERPRETATION.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/STATIC_GPU_DTYPE_DEVICE_AUDIT_R2.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/NONCOUNTED_ENGINEERING_GPU_SMOKE_FORWARD.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/GPU_EXECUTION_IDENTITY_FREEZE.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/COUNTED_GPU_FORWARD_MATRIX.json",
        "reports/driveclarify_method_v1_r4_cpu_contract_completion_and_controlled_gpu_validation/CONTROLLED_GPU_VALIDATION_FINAL.json",
    ]
    implementation_paths = [
        "driveclarify_persistent_ambiguity_runtime_v1/runtime.py",
        "driveclarify_persistent_ambiguity_runtime_v1/m2b_adapter.py",
        "driveclarify_protocol_cpu_v1/candidate_certification.py",
        "driveclarify_protocol_cpu_v1/contracts.py",
        "driveclarify_protocol_cpu_v1/fixtures.py",
        "driveclarify_protocol_cpu_v1/cpu_validation.py",
        "driveclarify_protocol_cpu_v1/gpu_preregistration.py",
        "driveclarify_protocol_cpu_v1/gpu_validation_finalize.py",
        "driveclarify_protocol_cpu_v1/candidate_identity.py",
        "driveclarify_protocol_cpu_v1/opportunity_contracts.py",
        "driveclarify_protocol_gpu_v1/runner.py",
        "schemas/METHOD_V1_R4_GPU_FORWARD_RECORD.schema.json",
        "tests/method_v1_r4_cpu_contracts/test_cpu_contracts.py",
    ]
    implementation = [{"path": path, "sha256": _sha(ROOT / path)} for path in implementation_paths]
    evidence = [{"path": path, "sha256": _sha(ROOT / path)} for path in evidence_paths]
    value = {
        "schema_version": "driveclarify.method_v1_r4.gpu_validated_candidate_fileset.v1",
        "status": "R4_GPU_VALIDATED_CANDIDATE_NOT_FINAL_METHOD_FREEZE",
        "identity_is_not": [
            "METHOD_V1_R3",
            "FINAL_METHOD_FREEZE",
            "FORMAL_OR_SCIENTIFIC_ATTEMPT",
            "NATIVE_CARLA_CERTIFICATION",
        ],
        "historical_r3": {
            "status": "PERMANENT_PRE_REPAIR_DIAGNOSTIC_PRESERVED",
            "aggregate_sha256": cpu["hash_diff"]["old_aggregate_sha256"],
            "formal_campaign_data_concatenated": False,
            "slot60_resumed": False,
            "historical_ledger_modified": False,
        },
        "protected_64_file_candidate_identity": {
            "file_count": 64,
            "aggregate_sha256": cpu["hash_diff"]["new_aggregate_sha256"],
            "changed_from_r3_count": 2,
            "unchanged_from_r3_count": 62,
            "changed_paths": cpu["hash_diff"]["changed_paths"],
            "files": cpu["hash_diff"]["new_inventory"],
        },
        "implementation_artifacts": implementation,
        "validation_evidence_artifacts": evidence,
        "artifact_aggregate_sha256": canonical_sha256({
            "implementation": implementation, "validation_evidence": evidence
        }),
        "validation_summary": {
            "cpu_validation_sha256": cpu["result_sha256"],
            "gpu_validation_sha256": gpu["validation_sha256"],
            "cpu_focused_tests_passed": 8,
            "compileall_passed": True,
            "static_gpu_audit_terminal_revision": "R2_PASS",
            "engineering_smoke_forward_count": 1,
            "counted_gpu_forward_count": 48,
            "gpu_retry_forward_count": 0,
            "candidate_certifications": {
                key: row["classification"]
                for key, row in gpu["candidate_pair_certifications"].items()
            },
        },
        "prohibited_activity_counts": {
            "carla_launch": 0,
            "formal_attempt": 0,
            "scientific_attempt": 0,
            "dev_access": 0,
            "test_access": 0,
            "training": 0,
            "lora": 0,
            "model_parameter_update": 0,
            "a800_usage": 0,
        },
        "next_authority": "TARGETED_NATIVE_CARLA_CERTIFICATION_REQUIRES_SEPARATE_EXPLICIT_AUTHORIZATION",
    }
    value["candidate_fileset_identity_sha256"] = canonical_sha256(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-validation", type=Path, required=True)
    parser.add_argument("--gpu-validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = build(args.cpu_validation, args.gpu_validation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": value["status"], "identity_sha256": value["candidate_fileset_identity_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
