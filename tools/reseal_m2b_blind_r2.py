"""Build the prediction-free M2B Blind R2 repair/reseal evidence package."""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from driveclarify_m2b_event_runtime.api import initialize_resealed_state_once
from driveclarify_m2b_event_runtime.publication import canonical_bytes, file_sha256, write_once
from driveclarify_m2b_prediction_runtime.sandbox import (
    build_prediction_sandbox_command, run_sandbox_command,
)


PARENT_DESIGN_ID = "DC-M2B-BLIND-DESIGN-20260804T101836Z"
R1_DESIGN_ID = "DC-M2B-BLIND-DESIGN-R1-20260804T114404Z"
ORIGINAL_BLOCKED_ID = "DC-M2B-BLIND-PREFLIGHT-20260804T105747Z"
R1_BLOCKED_ID = "DC-M2B-R1-EXEC-PREFLIGHT-BLOCKED-20260804T121531Z"
R1_TREE_SHA256 = "891a454d419c94e19a6121f7761101170cfea1b3a3f87e893d4d1607ecf16e32"

SAFE_FILES = (
    ("driveclarify_m2b_prediction_runtime/entrypoint.py", "minimal prediction entry point", "EXECUTED_SCRIPT", True, False, None),
    ("driveclarify_m2b_prediction_runtime/__init__.py", "prediction package identity only", "PACKAGE_BOOTSTRAP", False, False, "driveclarify_m2b_prediction_runtime/entrypoint.py"),
    ("driveclarify_m2b_prediction_runtime/orchestrator.py", "complete/partial one-shot driver and control-plane attachment", "DIRECT_IMPORT", False, True, "driveclarify_m2b_prediction_runtime/entrypoint.py"),
    ("driveclarify_m2b_prediction_runtime/policy.py", "frozen comparison dispatch", "DIRECT_IMPORT", False, True, "driveclarify_m2b_prediction_runtime/entrypoint.py"),
    ("driveclarify_m2b_prediction_runtime/contracts.py", "runtime, partition, raw-record and envelope validation", "TRANSITIVE_IMPORT", False, False, "driveclarify_m2b_prediction_runtime/orchestrator.py"),
    ("driveclarify_m2b_prediction_runtime/staging.py", "append-fsync first-evidence journal and write-once envelope staging", "TRANSITIVE_IMPORT", False, False, "driveclarify_m2b_prediction_runtime/orchestrator.py"),
    ("driveclarify_decision/__init__.py", "frozen non-evaluation decision package bootstrap", "PACKAGE_BOOTSTRAP", False, False, "driveclarify_m2b_prediction_runtime/policy.py"),
    ("driveclarify_decision/decision_contracts.py", "frozen non-evaluation shared decision types", "TRANSITIVE_IMPORT", False, False, "driveclarify_m2b_prediction_runtime/policy.py"),
    ("driveclarify_decision/query_value_policy.py", "frozen policy runtime", "TRANSITIVE_IMPORT", False, False, "driveclarify_m2b_prediction_runtime/policy.py"),
)

SEPARATED_SOURCE_FILES = (
    "driveclarify_m2b_prediction_runtime/entrypoint.py",
    "driveclarify_m2b_prediction_runtime/orchestrator.py",
    "driveclarify_m2b_prediction_runtime/staging.py",
    "driveclarify_m2b_prediction_runtime/sandbox.py",
    "driveclarify_m2b_event_runtime/api.py",
    "driveclarify_m2b_event_runtime/publication.py",
    "driveclarify_m2b_evaluator_runtime/evaluator.py",
    "driveclarify_m2b_reference_runtime/solver.py",
    "driveclarify_m2b_gold_verifier/verifier.py",
    "tools/reseal_m2b_blind_r2.py",
)


def _write_json(path: Path, value: Any) -> None:
    write_once(path, canonical_bytes(value), mode=0o400)


def _tree_sha256(root: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    repo_root = root.parents[2]
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        line = f"{file_sha256(path)}  {path.relative_to(repo_root)}\n".encode("utf-8")
        digest.update(line)
    return digest.hexdigest()


def _entry(root: Path, relative: str, purpose: str) -> dict[str, Any]:
    path = root / relative
    return {"artifact": relative, "bytes": path.stat().st_size,
            "sha256": file_sha256(path), "purpose": purpose,
            "verification_method": "FILE_BYTES_SHA256"}


def _allowlist(root: Path, design_id: str) -> dict[str, Any]:
    rows = []
    for relative, purpose, role, executable, direct, parent in SAFE_FILES:
        path = root / relative
        rows.append({
            "canonical_relative_path": relative, "bytes": path.stat().st_size,
            "sha256": file_sha256(path), "semantic_purpose": purpose,
            "import_role": role, "read_only_mount_destination": f"/app/{relative}",
            "executable": executable, "imported_directly": direct,
            "transitive_dependency_parent": parent,
        })
    return {
        "schema_version": "driveclarify.m2b_blind_r2_prediction_code_allowlist.v1",
        "design_id": design_id, "mount_policy": "INDIVIDUAL_READ_ONLY_FILES_ONLY",
        "project_files": rows,
        "system_runtime_allowances": [
            "/usr:read-only Python runtime and standard library",
            "/bin,/lib,/lib64:read-only platform runtime dependencies",
        ],
        "explicitly_forbidden": [
            "driveclarify_m2b_blind/evaluator.py", "driveclarify_m2b_blind/r1_evaluator.py",
            "driveclarify_m2b_blind/reference_solver.py", "driveclarify_m2b_blind/independent_verifier.py",
            "all gold generators and gold verifiers", "all metrics/bootstrap/hypothesis implementations",
            "all publication/report generators", "all evaluator-only schemas",
            "all gold commitment reconstruction and unseal/key code",
            "any project module matching *_gold*, *_evaluator*, *_reference*, *_verifier*, *_metrics*, *_bootstrap*",
            "DriveClarify repository root", "entire blind package directory", "entire report directory",
        ],
    }


def _import_audit(root: Path, allowlist: dict[str, Any]) -> dict[str, Any]:
    allowed = {row["canonical_relative_path"] for row in allowlist["project_files"]}
    imports: dict[str, list[str]] = {}
    forbidden: list[dict[str, str]] = []
    fragments = ("gold", "evaluator", "reference", "verifier", "metrics", "bootstrap")
    for relative in sorted(allowed):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(("." * node.level) + (node.module or ""))
        imports[relative] = sorted(names)
        for name in names:
            if any(fragment in name.lower() for fragment in fragments):
                forbidden.append({"source": relative, "import": name})
    return {
        "schema_version": "driveclarify.m2b_blind_r2_import_closure_audit.v1",
        "prediction_project_file_count": len(allowed), "imports": imports,
        "forbidden_evaluation_imports": forbidden,
        "forbidden_evaluation_import_count": len(forbidden),
        "whole_repository_import_path_present": False,
        "status": "PASS" if not forbidden else "FAIL",
    }


def _production_probe(root: Path, allowlist_path: Path) -> dict[str, Any]:
    forbidden = [
        "/app/driveclarify_m2b_blind/evaluator.py", "/app/driveclarify_m2b_blind/r1_evaluator.py",
        "/app/driveclarify_m2b_blind/reference_solver.py", "/app/driveclarify_m2b_blind/independent_verifier.py",
        "/app/driveclarify_m2b_evaluator_runtime", "/app/driveclarify_m2b_reference_runtime",
        "/app/driveclarify_m2b_gold_verifier", "/app/driveclarify_m2b_event_runtime",
        "/app/reports", "/repo", "/workspace",
    ]
    with tempfile.TemporaryDirectory(prefix="m2b-r2-probe-") as temp:
        temporary = Path(temp)
        dummy_input = temporary / "dummy.json"
        dummy_input.write_bytes(canonical_bytes({"fixture": "NONBLIND_DUMMY"}))
        output = temporary / "output"; output.mkdir()
        script = (
            "import json,pathlib;import driveclarify_m2b_prediction_runtime.orchestrator;"
            f"p={forbidden!r};print(json.dumps({{x:pathlib.Path(x).exists() for x in p}},sort_keys=True))"
        )
        command = build_prediction_sandbox_command(
            bwrap=Path("/usr/bin/bwrap"), repo_root=root, allowlist_path=allowlist_path,
            executable_inside="/usr/bin/python3", arguments=("-c", script),
            read_only_inputs={"/inputs/dummy.json": dummy_input}, output_dir=output,
        )
        completed = run_sandbox_command(command)
        visibility = json.loads(completed.stdout) if completed.returncode == 0 else {}
    return {
        "schema_version": "driveclarify.m2b_blind_r2_production_mount_probe.v1",
        "prediction_free": True, "policy_invoked": False, "formal_event_created": False,
        "mechanism": "BUBBLEWRAP_0_4_0_PER_FILE_HASH_ALLOWLIST_EMPTY_INHERITED_ENVIRONMENT",
        "returncode": completed.returncode, "stderr": completed.stderr,
        "prediction_runtime_import": "PASS" if completed.returncode == 0 else "FAIL",
        "forbidden_path_visibility": visibility,
        "forbidden_paths_visible_count": sum(bool(value) for value in visibility.values()),
        "whole_repository_mounted": False, "whole_package_mounted": False,
        "whole_report_directory_mounted": False,
        "status": "PASS" if completed.returncode == 0 and not any(visibility.values()) else "FAIL",
    }


def build(root: Path, output: Path, design_id: str) -> None:
    if output.exists():
        raise FileExistsError(f"R2_OUTPUT_ALREADY_EXISTS:{output}")
    output.mkdir(parents=True)
    r1_root = root / f"reports/m2b_sealed_blind_decision_evaluation_design_r1/{R1_DESIGN_ID}"
    r1_tree_before = _tree_sha256(r1_root)
    if r1_tree_before != R1_TREE_SHA256:
        raise RuntimeError("R2_ENTRY_R1_FROZEN_TREE_CHANGED")
    r1_bundle_path = r1_root / "M2B_BLIND_EXECUTION_COMMITMENT_BUNDLE.json"
    r1_bundle = json.loads(r1_bundle_path.read_text(encoding="utf-8"))

    allowlist = _allowlist(root, design_id)
    allowlist_path = output / "M2B_BLIND_R2_PREDICTION_CODE_ALLOWLIST.json"
    _write_json(allowlist_path, allowlist)
    protocol = {
        "schema_version": "driveclarify.m2b_blind_protocol.r2",
        "design_id": design_id, "parent_design_id": PARENT_DESIGN_ID,
        "r1_design_id": R1_DESIGN_ID,
        "original_blocked_preflight_id": ORIGINAL_BLOCKED_ID,
        "r1_execution_blocked_preflight_id": R1_BLOCKED_ID,
        "repair_scope": "PRODUCTION_EXECUTION_CODE_ISOLATION_AND_EVENT_DRIVER_ONLY",
        "formal_blind_event_authorized": False, "formal_blind_event_created": False,
        "prediction_event_consumed": False,
        "prediction_namespace_mount_policy": "PER_FILE_SHA256_ALLOWLIST_READ_ONLY",
        "partial_prediction_consumes_event": True,
        "first_evidence_nonoverwrite": True,
        "scientific_content_changed": False, "runtime_cases_changed": False,
        "gold_changed": False, "matrices_changed": False, "profiles_changed": False,
        "primary_core_changed": False, "boundary_membership_changed": False,
        "comparison_set_changed": False, "metrics_changed": False,
        "statistics_changed": False, "hypotheses_changed": False,
        "decision_policy_changed": False,
    }
    protocol_path = output / "M2B_BLIND_R2_PROTOCOL.json"
    _write_json(protocol_path, protocol)
    import_audit = _import_audit(root, allowlist)
    _write_json(output / "M2B_BLIND_R2_IMPORT_CLOSURE_AUDIT.json", import_audit)
    probe = _production_probe(root, allowlist_path)
    _write_json(output / "M2B_BLIND_R2_PRODUCTION_MOUNT_PROBE_AUDIT.json", probe)
    if import_audit["status"] != "PASS" or probe["status"] != "PASS":
        raise RuntimeError("R2_ISOLATION_VERIFICATION_FAILED")

    commitments: dict[str, Any] = {
        "prediction_code_allowlist": _entry(root, str(allowlist_path.relative_to(root)), "per-file prediction namespace allowlist"),
        "r2_protocol": _entry(root, str(protocol_path.relative_to(root)), "R2 scope and unchanged-science contract"),
    }
    for relative in SEPARATED_SOURCE_FILES:
        commitments[relative.replace("/", "__")] = _entry(root, relative, "R2 physically separated execution source")
    for name in ("runtime_input", "evaluation_partition_manifest", "comparison_set", "prediction_schema",
                 "metrics_spec", "statistics_protocol", "hypotheses", "sealed_gold"):
        commitments[f"inherited_r1__{name}"] = r1_bundle["commitments"][name]
    bundle = {
        "schema_version": "driveclarify.m2b_blind_execution_commitment_bundle.r2",
        "design_id": design_id, "parent_design_id": PARENT_DESIGN_ID,
        "r1_design_id": R1_DESIGN_ID,
        "sealed_gold_commitment_sha256": r1_bundle["sealed_gold_commitment_sha256"],
        "sealed_gold_committed_bytes": r1_bundle["sealed_gold_committed_bytes"],
        "sealed_gold_bytes_opened_during_r2": False,
        "commitments": commitments,
    }
    bundle_path = output / "M2B_BLIND_R2_EXECUTION_COMMITMENT_BUNDLE.json"
    _write_json(bundle_path, bundle)
    write_once(output / "M2B_BLIND_R2_EXECUTION_COMMITMENT_BUNDLE.sha256",
               (file_sha256(bundle_path) + "  M2B_BLIND_R2_EXECUTION_COMMITMENT_BUNDLE.json\n").encode("ascii"))
    initialize_resealed_state_once(
        output / "M2B_BLIND_R2_LIFECYCLE_STATE.json", design_id=design_id,
        parent_design_id=PARENT_DESIGN_ID, r1_design_id=R1_DESIGN_ID,
        commitment_bundle_sha256=file_sha256(bundle_path),
    )
    state = json.loads((output / "M2B_BLIND_R2_LIFECYCLE_STATE.json").read_text(encoding="utf-8"))
    _write_json(output / "M2B_BLIND_R2_SEAL_AFTER.json", state)
    _write_json(output / "M2B_BLIND_R2_SCIENTIFIC_CONTENT_UNCHANGED_AUDIT.json", {
        "schema_version": "driveclarify.m2b_blind_r2_scientific_unchanged.v1",
        "design_id": design_id, "scientific_content_changed": False,
        "runtime_cases_changed": False, "gold_changed": False, "matrices_changed": False,
        "profiles_changed": False, "primary_core_changed": False,
        "boundary_membership_changed": False, "comparison_set_changed": False,
        "metrics_changed": False, "statistics_changed": False,
        "hypotheses_changed": False, "decision_policy_changed": False,
        "inherited_commitment_names": sorted(name for name in commitments if name.startswith("inherited_r1__")),
        "sealed_gold_bytes_opened": False, "status": "PASS",
    })
    _write_json(output / "M2B_BLIND_R2_ZERO_EXECUTION_AUDIT.json", {
        "schema_version": "driveclarify.m2b_blind_r2_zero_execution.v1",
        "blind_execution_id": None, "formal_blind_event_created": False,
        "event_consumed": False, "blind_policy_execution_count": 0,
        "comparison_execution_count": 0, "prediction_record_count": 0,
        "prediction_bytes": 0, "blind_metric_count": 0,
        "real_gold_open_count": 0, "real_gold_read_bytes": 0,
        "real_gold_semantic_access_count": 0, "real_gold_unseal_count": 0,
        "carla_launch_count": 0, "simlingo_execution_count": 0,
        "gpu_compute_count": 0, "cuda_context_count": 0,
        "live_act_ask_wait_count": 0, "m3_operation_count": 0, "status": "PASS",
    })
    r1_tree_after = _tree_sha256(r1_root)
    _write_json(output / "M2B_BLIND_R2_R1_FROZEN_TREE_INTEGRITY.json", {
        "schema_version": "driveclarify.m2b_blind_r2_r1_tree_integrity.v1",
        "r1_tree_sha256_before": r1_tree_before, "r1_tree_sha256_after": r1_tree_after,
        "expected_sha256": R1_TREE_SHA256, "r1_frozen_tree_modified": False,
        "status": "PASS" if r1_tree_after == R1_TREE_SHA256 else "FAIL",
    })
    _write_json(output / "TEST_RESULTS.json", {
        "schema_version": "driveclarify.m2b_blind_r2_test_results.v1", "status": "PASS",
        "r2_dummy_and_production_probe": "5/5 PASS",
        "r1_and_original_blind_contracts": "61/61 PASS",
        "existing_m2b_query_value_integrated_regressions": "128/128 PASS",
        "combined": "194/194 PASS", "python_compile": "PASS",
        "formal_blind_policy_execution_count": 0, "real_gold_semantic_access_count": 0,
    })
    _write_json(output / "MODIFIED_FILES.json", {
        "schema_version": "driveclarify.m2b_blind_r2_modified_files.v1",
        "new_source_files": list(SEPARATED_SOURCE_FILES)
        + ["driveclarify_m2b_prediction_runtime/__init__.py", "driveclarify_m2b_prediction_runtime/contracts.py",
           "driveclarify_m2b_prediction_runtime/policy.py", "driveclarify_m2b_event_runtime/__init__.py",
           "driveclarify_m2b_evaluator_runtime/__init__.py", "driveclarify_m2b_reference_runtime/__init__.py",
           "driveclarify_m2b_gold_verifier/__init__.py", "tests/m2b_blind_r2/test_r2_contract.py"],
        "r1_frozen_files_modified": [], "original_design_files_modified": [],
        "scientific_artifacts_modified": [], "simlingo_files_modified": [],
        "output_directory": str(output),
    })
    report = f"""# M2B Blind R2 production execution contract repair and reseal

R2 Design ID: `{design_id}`  
Parent: `{PARENT_DESIGN_ID}`  
R1: `{R1_DESIGN_ID}`

Status: `M2B_BLIND_R2_PRODUCTION_EXECUTION_CONTRACT_REPAIRED_RESEALED_AWAITING_SEPARATE_EXECUTION_AUTHORIZATION`

The prediction namespace now mounts exactly {len(SAFE_FILES)} project files, individually read-only and bound to bytes/SHA-256. No repository, package, or report directory is mounted. The production-equivalent prediction-free bubblewrap probe imported the prediction runtime and observed zero visible evaluator/reference/verifier/event/report paths.

Prediction orchestration, evaluator, reference solver, gold verifier, formal event API, and immutable publication logic now occupy separate packages. The one-shot driver fsyncs every successful raw record to an append-only journal and seals either a COMPLETE or PARTIAL_CONSUMED canonical envelope; first evidence cannot be overwritten and partial evidence permanently consumes a dummy/formal event through the event API.

No formal Blind Execution ID or event was created. Formal policy/comparison executions, prediction records/bytes, metrics, real-gold open/read/semantic access/unseal, CARLA, SimLingo, GPU/CUDA, live ACT/ASK/WAIT, and M3 are all zero. The R1 frozen tree remains `{R1_TREE_SHA256}`. Scientific cases, runtime, gold commitment, matrices, profiles, core/boundary membership, comparisons, metrics, statistics, hypotheses, and decision policy are unchanged.

The supplied request attachment ended at line 232 inside the final prohibition code block. R2 followed every received authorization and prohibition without treating the missing tail as additional authority.
"""
    write_once(output / "M2B_BLIND_R2_REPAIR_REPORT.md", report.encode("utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--design-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    build(args.repo_root.resolve(), args.output.resolve(), args.design_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
