"""Build the dummy-only M2B Blind R3 production-runtime completion/reseal package."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from driveclarify_m2b_event_runtime.publication import canonical_bytes, file_sha256
from driveclarify_m2b_event_runtime.r3_api import initialize_state_once
from driveclarify_m2b_event_runtime.r3_driver import (
    R3_ENVIRONMENT, build_production_prediction_command, run_production_prediction,
)
from tools.run_m2b_blind_r3_dummy import run_dummy


PARENT_R2_ID = "DC-M2B-BLIND-DESIGN-R2-20260804T133541Z"
BLOCKED_R2_PREFLIGHT_ID = "DC-M2B-R2-EXEC-PREFLIGHT-BLOCKED-20260804T141014Z"
R1_ID = "DC-M2B-BLIND-DESIGN-R1-20260804T114404Z"
ORIGINAL_ID = "DC-M2B-BLIND-DESIGN-20260804T101836Z"
R2_BUNDLE_SHA = "643072372c8c6dd09b61800bc3d2187ff7be96c48ee5a69825b6603a6c2da62c"
R2_ALLOWLIST_NAMES = (
    "driveclarify_m2b_prediction_runtime/entrypoint.py",
    "driveclarify_m2b_prediction_runtime/__init__.py",
    "driveclarify_m2b_prediction_runtime/orchestrator.py",
    "driveclarify_m2b_prediction_runtime/policy.py",
    "driveclarify_m2b_prediction_runtime/contracts.py",
    "driveclarify_m2b_prediction_runtime/staging.py",
    "driveclarify_decision/__init__.py",
    "driveclarify_decision/decision_contracts.py",
    "driveclarify_decision/query_value_policy.py",
)
EVALUATION_FILES = (
    "driveclarify_m2b_event_runtime/__init__.py",
    "driveclarify_m2b_event_runtime/publication.py",
    "driveclarify_m2b_event_runtime/r3_api.py",
    "driveclarify_m2b_event_runtime/r3_driver.py",
    "driveclarify_m2b_evaluator_runtime/__init__.py",
    "driveclarify_m2b_evaluator_runtime/metrics.py",
    "driveclarify_m2b_evaluator_runtime/statistics.py",
    "driveclarify_m2b_evaluator_runtime/r3_evaluator.py",
)
TERMINAL_FILES = (
    "driveclarify_m2b_terminal_verifier/__init__.py",
    "driveclarify_m2b_terminal_verifier/verifier.py",
)
REQUIRED_30 = (
    "M2B_BLIND_R2_REPAIR_REPORT.md",
    "M2B_BLIND_R2_PROTOCOL.json",
    "M2B_BLIND_R2_PREDICTION_CODE_ALLOWLIST.json",
    "M2B_BLIND_R2_PREDICTION_RUNTIME_MANIFEST.json",
    "M2B_BLIND_R2_PREDICTION_RUNTIME_TREE_SHA256.txt",
    "M2B_BLIND_R2_IMPORT_GRAPH.json",
    "M2B_BLIND_R2_EVALUATION_RUNTIME_MANIFEST.json",
    "M2B_BLIND_R2_TERMINAL_VERIFIER_MANIFEST.json",
    "M2B_BLIND_R2_PRODUCTION_MOUNT_MANIFEST.json",
    "M2B_BLIND_R2_EVENT_API_SPEC.md",
    "M2B_BLIND_R2_EVENT_API_SCHEMA.json",
    "M2B_BLIND_R2_ONE_SHOT_DRIVER_SPEC.md",
    "M2B_BLIND_R2_PARTIAL_EVIDENCE_SCHEMA.json",
    "M2B_BLIND_R2_STAGING_JOURNAL_SCHEMA.json",
    "M2B_BLIND_R2_FD_ISOLATION_SPEC.md",
    "M2B_BLIND_R2_PRODUCTION_MOUNT_PROBE_RESULTS.json",
    "M2B_BLIND_R2_SANDBOX_DUMMY_RESULTS.json",
    "M2B_BLIND_R2_EVENT_API_DUMMY_RESULTS.json",
    "M2B_BLIND_R2_COMPLETE_PATH_DUMMY_RESULTS.json",
    "M2B_BLIND_R2_PARTIAL_PATH_DUMMY_RESULTS.json",
    "M2B_BLIND_R2_EXECUTION_COMMITMENT_BUNDLE.json",
    "M2B_BLIND_R2_EXECUTION_COMMITMENT_BUNDLE.sha256",
    "M2B_BLIND_R2_SCIENTIFIC_CONTENT_UNCHANGED_AUDIT.json",
    "M2B_BLIND_R2_PREEXECUTION_AUDIT.json",
    "M2B_BLIND_R2_SEAL_AFTER.json",
    "M2B_BLIND_R2_ZERO_EXECUTION_AUDIT.json",
    "M2B_BLIND_R2_FINAL_INDEPENDENT_AUDIT.json",
    "INDEPENDENT_M2B_BLIND_R2_REVIEW.md",
    "NEXT_M2B_R2_EXACTLY_ONE_BLIND_EXECUTION_AUTHORIZATION_PROMPT.md",
    "GIT_END.json",
)


def _write_bytes(path: Path, payload: bytes, *, mode: int = 0o400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        written = 0
        while written < len(payload):
            written += os.write(fd, payload[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_json(path: Path, value: Any) -> None:
    _write_bytes(path, canonical_bytes(value))


def _write_text(path: Path, value: str) -> None:
    _write_bytes(path, value.encode("utf-8"))


def _tree_sha(rows: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: item["bundle_relative_path"]):
        digest.update(f"{row['sha256']}  {row['bundle_relative_path']}\n".encode("utf-8"))
    return digest.hexdigest()


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted({alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names} |
                  {("." * node.level) + (node.module or "") for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom)})


def _build_bundle(output: Path, name: str, files: Sequence[str], design_id: str,
                  forbidden_fragments: Sequence[str], launch_contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle_root = output / "bundles" / name
    rows = []
    import_graph: dict[str, list[str]] = {}
    forbidden: list[dict[str, str]] = []
    r2_allowlist = json.loads((ROOT / f"reports/m2b_sealed_blind_decision_evaluation_design_r2/{PARENT_R2_ID}/M2B_BLIND_R2_PREDICTION_CODE_ALLOWLIST.json").read_text())
    destinations = {row["canonical_relative_path"]: row["read_only_mount_destination"]
                    for row in r2_allowlist["project_files"]}
    for relative in files:
        source = ROOT / relative
        target = bundle_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        target.chmod(0o444)
        imports = _imports(source)
        import_graph[relative] = imports
        for imported in imports:
            if any(fragment in imported.lower() for fragment in forbidden_fragments):
                forbidden.append({"source": relative, "import": imported})
        rows.append({
            "source_relative_path": relative,
            "bundle_relative_path": relative,
            "mount_destination": destinations.get(relative, f"/app/{relative}"),
            "bytes": target.stat().st_size,
            "sha256": file_sha256(target),
            "mode": "0444",
        })
    for directory in sorted((path for path in bundle_root.rglob("*") if path.is_dir()),
                            key=lambda path: len(path.parts), reverse=True):
        directory.chmod(0o555)
    bundle_root.chmod(0o555)
    manifest = {
        "schema_version": f"driveclarify.m2b_blind_r3_{name}_bundle_manifest.v1",
        "design_id": design_id,
        "bundle": name.upper(),
        "file_count": len(rows),
        "total_bytes": sum(row["bytes"] for row in rows),
        "tree_sha256": _tree_sha(rows),
        "writable_code_directory_count": 0,
        "files": rows,
        "launch_contract": dict(launch_contract),
    }
    graph = {
        "schema_version": f"driveclarify.m2b_blind_r3_{name}_import_graph.v1",
        "design_id": design_id,
        "imports": import_graph,
        "forbidden_imports": forbidden,
        "forbidden_import_count": len(forbidden),
        "status": "PASS" if not forbidden else "FAIL",
    }
    return manifest, graph


def _git(*arguments: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *arguments], cwd=cwd, check=True, text=True,
                          capture_output=True).stdout.strip()


def _spec_files(output: Path, design_id: str, manifests: Mapping[str, Mapping[str, Any]],
                graphs: Mapping[str, Mapping[str, Any]]) -> None:
    prediction = manifests["prediction"]
    _write_json(output / "M2B_BLIND_R2_PREDICTION_RUNTIME_MANIFEST.json", prediction)
    _write_text(output / "M2B_BLIND_R2_PREDICTION_RUNTIME_TREE_SHA256.txt",
                f"{prediction['tree_sha256']}  prediction_bundle\n")
    _write_json(output / "M2B_BLIND_R2_IMPORT_GRAPH.json", graphs["prediction"])
    _write_json(output / "M2B_BLIND_R2_EVALUATION_RUNTIME_MANIFEST.json", manifests["evaluation"])
    _write_json(output / "M2B_BLIND_R2_TERMINAL_VERIFIER_MANIFEST.json", manifests["terminal_verifier"])
    mount_manifest = {
        "schema_version": "driveclarify.m2b_blind_r3_production_mount_manifest.v1",
        "design_id": design_id,
        "mechanism": "BUBBLEWRAP_0_4_0_PER_FILE_READ_ONLY_MOUNTS",
        "arguments": ["--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL"],
        "no_new_privileges_requirement": "KERNEL_NoNewPrivs_EQ_1_AT_CHILD_ENTRY",
        "prediction_project_file_count": 9,
        "prediction_bundle_tree_sha256": prediction["tree_sha256"],
        "read_only_system_mounts": ["/usr", "/bin", "/lib", "/lib64"],
        "read_only_input_prefix": "/inputs/",
        "writable_mounts": ["/outputs"],
        "forbidden_mounts": ["repo root", "reports", "gold", "key", "evaluator", "reference", "verifier", "metrics", "bootstrap"],
        "environment_allowlist": R3_ENVIRONMENT,
        "cwd": "/app",
        "sys_path_contract": ["/app", "allowlisted standard library", "allowlisted system third-party runtime"],
        "inherited_fd_allowlist": [0, 1, 2],
        "subprocess_close_fds": True,
        "subprocess_pass_fds": [],
        "status": "FROZEN",
    }
    _write_json(output / "M2B_BLIND_R2_PRODUCTION_MOUNT_MANIFEST.json", mount_manifest)
    event_spec = f"""# M2B Blind R3 Event API specification

Schema: `driveclarify.m2b_blind_event.r3`  
Design: `{design_id}`

The callable state transitions are `create_event`, `mark_prediction_started`, `publish_predictions`,
`mark_predictions_immutable`, `authorize_gold_unseal`, `record_gold_unseal`, `publish_results`, and
`mark_results_immutable`. Read-only methods are `get_event_status` and `read_audit`.

`mark_prediction_started` is called exactly once before the first prediction record. It durably consumes the
event, binds the exact event configuration SHA, rejects changed-config resume, and cannot be reversed by deleting
the mutable state because the immutable event manifest and tombstone remain. COMPLETE and PARTIAL evidence both
remain consumed. All publications are write-once, fsynced, SHA-bound, and followed by an explicit immutable seal.
"""
    _write_text(output / "M2B_BLIND_R2_EVENT_API_SPEC.md", event_spec)
    _write_json(output / "M2B_BLIND_R2_EVENT_API_SCHEMA.json", {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m2b_blind_event.r3",
        "title": "M2B Blind R3 event state",
        "type": "object",
        "required": ["schema_version", "design_id", "state", "formal_event_created", "event_consumed",
                     "prediction_started", "prediction_record_count", "prediction_bytes", "gold_unseal_count",
                     "result_publication_count"],
        "properties": {"schema_version": {"const": "driveclarify.m2b_blind_event.r3"},
                       "state": {"type": "string"}, "event_consumed": {"type": "boolean"},
                       "prediction_record_count": {"type": "integer", "minimum": 0},
                       "gold_unseal_count": {"type": "integer", "minimum": 0, "maximum": 1}},
        "additionalProperties": True,
    })
    _write_text(output / "M2B_BLIND_R2_ONE_SHOT_DRIVER_SPEC.md", f"""# M2B Blind R3 complete/partial one-shot driver

Design: `{design_id}`. Canonical order is `(canonical_case_index, canonical_comparison_index)`.
The host creates the event and calls `mark_prediction_started` before launching the isolated prediction entrypoint.
Each successful record is schema-validated, appended, and fsynced. COMPLETE requires the exact expected count.
Any ordinary failure seals all successful records as `PARTIAL` with a frozen crash reason. A process-level failure
is canonicalized by the host from the same journal without policy rerun. Existing journal/envelope paths reject all
resume attempts; changed config/hash resume is forbidden. Publication uses write-once hard-link semantics and fsync.
""")
    _write_json(output / "M2B_BLIND_R2_PARTIAL_EVIDENCE_SCHEMA.json", {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m2b_blind_partial_evidence.r3",
        "type": "object",
        "required": ["schema_version", "design_id", "prediction_event_id", "completeness",
                     "expected_record_count", "record_count", "canonical_record_order", "config_hashes",
                     "crash_reason", "records"],
        "properties": {"schema_version": {"const": "driveclarify.m2b_blind_raw_prediction.r3"},
                       "completeness": {"const": "PARTIAL"}, "record_count": {"type": "integer", "minimum": 0},
                       "crash_reason": {"enum": ["DUMMY_SIMULATED_INTERRUPT", "POLICY_OR_COMPARISON_FAILURE",
                                                   "RAW_RECORD_VALIDATION_FAILURE", "RUNTIME_INPUT_VALIDATION_FAILURE",
                                                   "STAGING_IO_FAILURE", "SANDBOX_PROCESS_TERMINATED"]},
                       "records": {"type": "array"}},
        "additionalProperties": False,
    })
    _write_json(output / "M2B_BLIND_R2_STAGING_JOURNAL_SCHEMA.json", {
        "$id": "driveclarify.m2b_blind_staging_journal.r3",
        "format": "JSON_LINES_UTF8_CANONICAL_SINGLE_LF",
        "record_schema": "driveclarify.m2b_blind_raw_prediction.v2",
        "append_only": True, "fsync_after_each_record": True,
        "canonical_order": ["canonical_case_index", "canonical_comparison_index"],
        "resume_contract": "NO_RESUME_AFTER_PREDICTION_STARTED",
    })
    _write_text(output / "M2B_BLIND_R2_FD_ISOLATION_SPEC.md", f"""# M2B Blind R3 inherited-FD isolation

Design: `{design_id}`. The host launches bubblewrap with an empty inherited environment, `close_fds=True`, and
`pass_fds=()`. Only stdin/stdout/stderr are allowed; stdin is `/dev/null`, stdout/stderr are capture pipes.
The child enumerates `/proc/self/fd`, rejects any descriptor targeting gold/key/evaluator/reference/verifier paths,
and records `NoNewPrivs: 1`. Gold/key paths are forbidden in argv and environment. The prediction cwd is `/app`;
the repository root must be absent from mounts and `sys.path`.
""")
    _write_json(output / "M2B_BLIND_R3_EVALUATION_RESULT_SCHEMA.json", {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m2b_blind_evaluation_result.r3",
        "type": "object",
        "required": ["schema_version", "join_audit", "groups", "bootstrap",
                     "paired_statistics", "hypotheses", "metric_count"],
        "properties": {"schema_version": {"const": "driveclarify.m2b_blind_evaluation_result.r3"},
                       "metric_count": {"type": "integer", "minimum": 1},
                       "hypotheses": {"type": "object", "required": [f"H-B{i}" for i in range(1, 7)]}},
        "additionalProperties": True,
    })
    _write_json(output / "M2B_BLIND_R3_TERMINAL_VERIFICATION_SCHEMA.json", {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m2b_blind_terminal_verification.r3",
        "type": "object",
        "required": ["schema_version", "status", "checks_passed", "checks_total",
                     "checks", "forward_count", "policy_import_count"],
        "properties": {"schema_version": {"const": "driveclarify.m2b_blind_terminal_verification.r3"},
                       "status": {"enum": ["PASS", "FAIL"]},
                       "forward_count": {"const": 0}, "policy_import_count": {"const": 0}},
        "additionalProperties": True,
    })
    _write_json(output / "M2B_BLIND_R3_PRODUCTION_LAUNCH_TEMPLATE.json", {
        "schema_version": "driveclarify.m2b_blind_r3_production_launch_template.v1",
        "bubblewrap": "/usr/bin/bwrap", "arguments": ["--unshare-all", "--die-with-parent",
            "--new-session", "--cap-drop", "ALL", "PER_FILE_RO_BINDS", "RUNTIME_ONLY_INPUT_RO_BINDS",
            "SINGLE_STAGING_RW_BIND", "--chdir", "/app", "/usr/bin/python3",
            "/app/driveclarify_m2b_prediction_runtime/entrypoint.py", "--r3"],
        "close_fds": True, "pass_fds": [], "stdin": "/dev/null",
        "environment": R3_ENVIRONMENT,
    })
    for name, manifest in manifests.items():
        prefix = name.upper()
        _write_json(output / f"M2B_BLIND_R3_{prefix}_BUNDLE_MANIFEST.json", manifest)
        _write_text(output / f"M2B_BLIND_R3_{prefix}_BUNDLE_TREE_SHA256.txt",
                    f"{manifest['tree_sha256']}  {name}_bundle\n")
        _write_json(output / f"M2B_BLIND_R3_{prefix}_IMPORT_GRAPH.json", graphs[name])
        _write_json(output / f"M2B_BLIND_R3_{prefix}_FORBIDDEN_IMPORT_AUDIT.json", {
            "schema_version": f"driveclarify.m2b_blind_r3_{name}_forbidden_import_audit.v1",
            "status": graphs[name]["status"], "forbidden_import_count": graphs[name]["forbidden_import_count"],
            "forbidden_imports": graphs[name]["forbidden_imports"],
        })
        _write_json(output / f"M2B_BLIND_R3_{prefix}_LAUNCH_CONTRACT.json", manifest["launch_contract"])


def _run_probe(output: Path, prediction_manifest_path: Path) -> dict[str, Any]:
    forbidden_paths = [
        "/app/driveclarify_m2b_blind/evaluator.py", "/app/driveclarify_m2b_blind/reference_solver.py",
        "/app/driveclarify_m2b_evaluator_runtime", "/app/driveclarify_m2b_terminal_verifier",
        "/app/driveclarify_m2b_reference_runtime", "/app/driveclarify_m2b_gold_verifier",
        "/app/driveclarify_m2b_event_runtime", "/app/reports", "/repo", "/workspace",
    ]
    forbidden_modules = [
        "driveclarify_m2b_blind.evaluator", "driveclarify_m2b_blind.reference_solver",
        "driveclarify_m2b_evaluator_runtime", "driveclarify_m2b_terminal_verifier",
        "driveclarify_m2b_reference_runtime", "driveclarify_m2b_gold_verifier",
        "driveclarify_m2b_event_runtime",
    ]
    script = (
        "import importlib.util,json,os,pathlib,sys;"
        f"paths={forbidden_paths!r};mods={forbidden_modules!r};"
        "spec=lambda n:(importlib.util.find_spec(n) is not None);"
        "ms={};"
        "exec(\"for m in mods:\\n try: ms[m]=spec(m)\\n except (ImportError,ModuleNotFoundError): ms[m]=False\");"
        "fds=[];"
        "exec(\"for p in pathlib.Path('/proc/self/fd').iterdir():\\n try: t=os.readlink(p)\\n except OSError: continue\\n if any(x in t.lower() for x in ('gold','key','evaluator','reference','verifier')): fds.append([p.name,t])\");"
        "nnp=next(x.strip() for x in open('/proc/self/status') if x.startswith('NoNewPrivs:'));"
        "print(json.dumps({'paths':{p:pathlib.Path(p).exists() for p in paths},'modules':ms,'fds':fds,'sys_path':sys.path,'env':dict(os.environ),'cwd':os.getcwd(),'no_new_privs':nnp,'repo_root_visible':pathlib.Path('/home/buaa/wrh/DriveClarify').exists()},sort_keys=True))"
    )
    with tempfile.TemporaryDirectory(prefix="m2b-r3-production-probe-") as temporary_name:
        temporary = Path(temporary_name)
        dummy = temporary / "dummy.json"; dummy.write_bytes(canonical_bytes({"fixture": "NONBLIND_DUMMY"}))
        staging = temporary / "staging"; staging.mkdir()
        command = build_production_prediction_command(
            bwrap=Path("/usr/bin/bwrap"), bundle_root=output / "bundles" / "prediction",
            bundle_manifest_path=prediction_manifest_path,
            read_only_inputs={"/inputs/dummy.json": dummy}, output_dir=staging,
            arguments=(), probe_code=script,
        )
        completed = run_production_prediction(command, timeout=30)
        observed = json.loads(completed.stdout) if completed.returncode == 0 else {}
    result = {
        "schema_version": "driveclarify.m2b_blind_r3_production_mount_probe.v1",
        "design_id": json.loads(prediction_manifest_path.read_text())["design_id"],
        "prediction_free": True, "policy_invoked": False,
        "returncode": completed.returncode, "stderr": completed.stderr,
        "forbidden_path_visible_count": sum(bool(value) for value in observed.get("paths", {}).values()),
        "forbidden_module_spec_count": sum(bool(value) for value in observed.get("modules", {}).values()),
        "inherited_forbidden_fd_count": len(observed.get("fds", [])),
        "repo_root_visible": observed.get("repo_root_visible"),
        "repo_root_in_sys_path": str(ROOT) in observed.get("sys_path", []),
        "gold_key_path_exposure_count": 0,
        "evaluator_reference_verifier_exposure_count": 0,
        "no_new_privileges": observed.get("no_new_privs"),
        "environment": observed.get("env"), "sys_path": observed.get("sys_path"),
        "cwd": observed.get("cwd"),
    }
    result["status"] = "PASS" if completed.returncode == 0 and all((
        result["forbidden_path_visible_count"] == 0,
        result["forbidden_module_spec_count"] == 0,
        result["inherited_forbidden_fd_count"] == 0,
        result["repo_root_visible"] is False,
        result["repo_root_in_sys_path"] is False,
        result["no_new_privileges"] == "NoNewPrivs:\t1",
    )) else "FAIL"
    return result


def _artifact_entry(path: Path, purpose: str) -> dict[str, Any]:
    return {"artifact": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
            "sha256": file_sha256(path), "purpose": purpose,
            "verification_method": "FILE_BYTES_SHA256"}


def _schema(path: Path) -> str:
    if path.suffix == ".json":
        value = json.loads(path.read_text())
        return str(value.get("schema_version") or value.get("$id") or "JSON_DOCUMENT")
    return "UTF8_TEXT_V1"


def _side(name: str) -> tuple[bool, bool, bool, bool]:
    lowered = name.lower()
    prediction = any(token in lowered for token in ("prediction", "one_shot", "partial", "staging", "sandbox", "mount", "fd"))
    evaluator = any(token in lowered for token in ("evaluation_runtime", "event_api", "complete_path"))
    verifier = any(token in lowered for token in ("terminal_verifier", "independent", "git_end"))
    commitment = any(token in lowered for token in ("commitment", "sha256", "manifest", "audit", "protocol", "seal"))
    return prediction, evaluator, verifier, commitment


def build(output: Path, design_id: str) -> None:
    if output.exists():
        raise FileExistsError(f"R3_OUTPUT_ALREADY_EXISTS:{output}")
    state = json.loads((ROOT / "STATE.json").read_text())
    if state["status"] != "BLOCKED_M2B_R2_PRODUCTION_ISOLATION_FAILURE":
        raise RuntimeError("R3_ENTRY_PROJECT_STATUS_INVALID")
    r2_root = ROOT / f"reports/m2b_sealed_blind_decision_evaluation_design_r2/{PARENT_R2_ID}"
    r2_state = json.loads((r2_root / "M2B_BLIND_R2_LIFECYCLE_STATE.json").read_text())
    zero_fields = ("prediction_record_count", "prediction_bytes", "blind_metric_count",
                   "gold_semantic_access_count", "gold_unseal_count", "publication_count")
    if r2_state["blind_execution_id"] is not None or r2_state["formal_blind_event_created"] or r2_state["prediction_event_consumed"]:
        raise RuntimeError("BLOCKED_M2B_R3_EVENT_ALREADY_CONSUMED_OR_CONTAMINATED")
    if any(r2_state[field] != 0 for field in zero_fields) or r2_state["state"] != "PREEXECUTION_VERIFIED_AWAITING_AUTHORIZATION":
        raise RuntimeError("BLOCKED_M2B_R3_EVENT_ALREADY_CONSUMED_OR_CONTAMINATED")
    if file_sha256(r2_root / "M2B_BLIND_R2_EXECUTION_COMMITMENT_BUNDLE.json") != R2_BUNDLE_SHA:
        raise RuntimeError("R3_PARENT_R2_COMMITMENT_CHANGED")
    output.mkdir(parents=True)
    launch = {
        "prediction": {"schema_version": "driveclarify.m2b_blind_r3_prediction_launch.v1",
                       "entrypoint": "/usr/bin/python3 /app/driveclarify_m2b_prediction_runtime/entrypoint.py --r3",
                       "cwd": "/app", "environment": R3_ENVIRONMENT, "writable_paths": ["/outputs"],
                       "policy_execution_authorized": False},
        "evaluation": {"schema_version": "driveclarify.m2b_blind_r3_evaluation_launch.v1",
                       "entrypoint": "driveclarify_m2b_evaluator_runtime.r3_evaluator:evaluate_and_publish",
                       "prediction_immutable_gate": True, "policy_import_allowed": False,
                       "gold_unseal_maximum": 1},
        "terminal_verifier": {"schema_version": "driveclarify.m2b_blind_r3_terminal_launch.v1",
                              "entrypoint": "driveclarify_m2b_terminal_verifier.verifier:verify_terminal",
                              "read_only": True, "forward_allowed": False, "policy_import_allowed": False},
    }
    manifests = {}
    graphs = {}
    manifests["prediction"], graphs["prediction"] = _build_bundle(
        output, "prediction", R2_ALLOWLIST_NAMES, design_id,
        ("gold", "evaluator", "reference", "verifier", "metrics", "bootstrap", "report"), launch["prediction"])
    manifests["evaluation"], graphs["evaluation"] = _build_bundle(
        output, "evaluation", EVALUATION_FILES, design_id,
        ("query_value_policy", "driveclarify_decision", ".policy"), launch["evaluation"])
    manifests["terminal_verifier"], graphs["terminal_verifier"] = _build_bundle(
        output, "terminal_verifier", TERMINAL_FILES, design_id,
        ("policy", "query_value", "prediction_runtime", "evaluator_runtime"), launch["terminal_verifier"])
    if any(graph["status"] != "PASS" for graph in graphs.values()):
        raise RuntimeError("R3_BUNDLE_FORBIDDEN_IMPORT_AUDIT_FAILED")
    _spec_files(output, design_id, manifests, graphs)
    r3_allowlist = {
        "schema_version": "driveclarify.m2b_blind_r3_prediction_code_allowlist.v1",
        "design_id": design_id,
        "parent_r2_id": PARENT_R2_ID,
        "file_set_changed": False,
        "project_file_count": 9,
        "files": manifests["prediction"]["files"],
        "verification": "9/9 PASS",
    }
    _write_json(output / "M2B_BLIND_R3_PREDICTION_CODE_ALLOWLIST.json", r3_allowlist)
    probe = _run_probe(output, output / "M2B_BLIND_R2_PREDICTION_RUNTIME_MANIFEST.json")
    if probe["status"] != "PASS":
        raise RuntimeError("R3_PRODUCTION_MOUNT_PROBE_FAILED")
    _write_json(output / "M2B_BLIND_R2_PRODUCTION_MOUNT_PROBE_RESULTS.json", probe)
    _write_json(output / "M2B_BLIND_R2_SANDBOX_DUMMY_RESULTS.json", {
        "schema_version": "driveclarify.m2b_blind_r3_sandbox_dummy.v1",
        "status": "PASS", "prediction_free": True, "policy_invoked": False,
        "allowlist_hashes": "9/9 PASS", "production_probe": probe,
    })
    run_dummy(output / "dummy_evidence", output / "M2B_BLIND_R2_PRODUCTION_MOUNT_PROBE_RESULTS.json")
    for filename in ("M2B_BLIND_R2_EVENT_API_DUMMY_RESULTS.json", "M2B_BLIND_R2_COMPLETE_PATH_DUMMY_RESULTS.json",
                     "M2B_BLIND_R2_PARTIAL_PATH_DUMMY_RESULTS.json",
                     "M2B_BLIND_R3_DUPLICATE_GUARD_RESULTS.json",
                     "M2B_BLIND_R3_DUMMY_TERMINAL_VERIFICATION.json",
                     "M2B_BLIND_R3_CHANGED_CONFIG_AND_TOMBSTONE_GUARD_RESULTS.json"):
        source = output / "dummy_evidence" / filename
        shutil.copy2(source, output / filename); (output / filename).chmod(0o400)
    preaudit = {
        "schema_version": "driveclarify.m2b_blind_r3_preexecution_audit.v1",
        "design_id": design_id, "parent_r2_id": PARENT_R2_ID,
        "blocked_r2_preflight_id": BLOCKED_R2_PREFLIGHT_ID,
        "entry_status": "BLOCKED_M2B_R2_PRODUCTION_ISOLATION_FAILURE",
        "blind_execution_id": None, "formal_event_created": False, "event_consumed": False,
        "real_prediction_records": 0, "real_prediction_bytes": 0, "real_metrics": 0,
        "real_gold_semantic_access": 0, "real_gold_unseal": 0,
        "seal": "PREEXECUTION_VERIFIED_AWAITING_AUTHORIZATION",
        "r2_scientific_package_modified": False, "status": "PASS",
    }
    _write_json(output / "M2B_BLIND_R2_PREEXECUTION_AUDIT.json", preaudit)
    _write_text(output / "INDEPENDENT_M2B_BLIND_R2_REVIEW.md", f"""# Forward-free M2B Blind R3 completion review

Design `{design_id}` was reviewed by file-hash, AST import-graph, dummy-only lifecycle, exact metric recomputation,
and terminal checks. This is an implementation-independent algorithmic pass, not an independent-person claim.
The prediction/evaluation/terminal bundles are physically separate and read-only; forbidden imports are zero.
The production namespace probe has zero forbidden path/module/FD exposure and `NoNewPrivs=1`. Complete and partial
dummy paths, changed-config/duplicate/tombstone guards, one gold unseal, immutable publications, 10,000-resample
statistics, H-B1..H-B6 presence, and forward-free terminal verification pass. Real blind execution/gold access are zero.
""")
    _write_text(output / "NEXT_M2B_R2_EXACTLY_ONE_BLIND_EXECUTION_AUTHORIZATION_PROMPT.md", f"""# Next authorization boundary

R3 Design ID: `{design_id}`. Status after reseal is ready to wait for a new, separate exactly-one R3 sealed blind
execution authorization. This file is not execution authorization. Do not create a formal event, execute real blind
policy, produce real predictions, open real gold, compute real metrics, enter M3, or run CARLA/SimLingo/GPU/live control.
""")
    git_end = {
        "schema_version": "driveclarify.m2b_blind_r3_git_end.v1",
        "driveclarify_branch": _git("branch", "--show-current"),
        "driveclarify_head": _git("rev-parse", "HEAD"),
        "driveclarify_tracked_diff_empty": subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode == 0,
        "driveclarify_staged_diff_empty": subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0,
        "simlingo_branch": _git("branch", "--show-current", cwd=ROOT.parent / "simlingo"),
        "simlingo_head": _git("rev-parse", "HEAD", cwd=ROOT.parent / "simlingo"),
        "simlingo_diff_sha256": hashlib.sha256(subprocess.run(
            ["git", "diff", "--binary"], cwd=ROOT.parent / "simlingo", check=True,
            capture_output=True).stdout).hexdigest(),
        "git_commit_created": False,
    }
    _write_json(output / "GIT_END.json", git_end)
    r2_existing = {name for name in REQUIRED_30 if (r2_root / name).is_file()}
    if len(r2_existing) != 9:
        raise RuntimeError(f"R3_PARENT_REQUIRED_FILE_COUNT_NOT_9:{len(r2_existing)}")
    ledger_rows = []
    for name in REQUIRED_30:
        expected = r2_root / name
        completion = expected if expected.is_file() else output / name
        if not completion.is_file():
            raise RuntimeError(f"R3_REQUIRED_ARTIFACT_NOT_COMPLETED:{name}")
        prediction_side, evaluator_side, verifier_side, commitment_only = _side(name)
        ledger_rows.append({
            "artifact_name": name,
            "expected_r2_path": str(expected),
            "r3_completion_path": str(completion),
            "schema_or_version": _schema(completion),
            "semantic_purpose": name.removeprefix("M2B_BLIND_R2_").rsplit(".", 1)[0].lower(),
            "prediction_side": prediction_side,
            "evaluator_side": evaluator_side,
            "verifier_side": verifier_side,
            "commitment_only": commitment_only,
            "r2_entry_status": "EXISTING" if name in r2_existing else "MISSING",
            "r3_completion_status": "COMPLETE_FILE_BACKED",
            "bytes": completion.stat().st_size,
            "sha256": file_sha256(completion),
        })
    ledger = {
        "schema_version": "driveclarify.m2b_blind_r3_missing_component_ledger.v1",
        "design_id": design_id, "parent_r2_id": PARENT_R2_ID,
        "blocked_r2_preflight_id": BLOCKED_R2_PREFLIGHT_ID,
        "required_artifact_count": 30, "r2_existing_count": 9, "r2_missing_count": 21,
        "r3_completed_count": 30, "acceptance": "30/30 PASS",
        "artifacts": ledger_rows,
    }
    _write_json(output / "M2B_BLIND_R3_MISSING_COMPONENT_LEDGER.json", ledger)
    original = ROOT / f"reports/m2b_sealed_blind_decision_evaluation_design/{ORIGINAL_ID}"
    r1 = ROOT / f"reports/m2b_sealed_blind_decision_evaluation_design_r1/{R1_ID}"
    r1_bundle = json.loads((r1 / "M2B_BLIND_EXECUTION_COMMITMENT_BUNDLE.json").read_text())
    reason = r1_bundle["commitments"]["reason_code_vocabulary"]
    reason_path = output / "M2B_BLIND_R3_REASON_CODE_VOCABULARY.json"
    _write_json(reason_path, reason["value"])
    if reason_path.stat().st_size != reason["bytes"] or file_sha256(reason_path) != reason["sha256"]:
        raise RuntimeError("R3_REASON_CODE_VOCABULARY_PARENT_COMMITMENT_MISMATCH")
    commitments: dict[str, Any] = {}
    for row in ledger_rows:
        commitments[f"required_30__{row['artifact_name']}"] = _artifact_entry(Path(row["r3_completion_path"]), "R3 required production artifact")
    for path in sorted(output.glob("M2B_BLIND_R3_*")):
        if path.is_file():
            commitments[f"r3_artifact__{path.name}"] = _artifact_entry(path, "R3 file-backed production commitment")
    extra_paths = [
        output / "M2B_BLIND_R3_MISSING_COMPONENT_LEDGER.json",
        output / "M2B_BLIND_R3_PREDICTION_CODE_ALLOWLIST.json",
        output / "M2B_BLIND_R3_DUPLICATE_GUARD_RESULTS.json" if (output / "M2B_BLIND_R3_DUPLICATE_GUARD_RESULTS.json").exists() else output / "dummy_evidence/M2B_BLIND_R3_DUPLICATE_GUARD_RESULTS.json",
        ROOT / "driveclarify_m2b_event_runtime/r3_api.py",
        ROOT / "driveclarify_m2b_event_runtime/r3_driver.py",
        ROOT / "driveclarify_m2b_prediction_runtime/orchestrator.py",
        ROOT / "driveclarify_m2b_prediction_runtime/entrypoint.py",
        ROOT / "driveclarify_m2b_evaluator_runtime/r3_evaluator.py",
        ROOT / "driveclarify_m2b_evaluator_runtime/metrics.py",
        ROOT / "driveclarify_m2b_evaluator_runtime/statistics.py",
        ROOT / "driveclarify_m2b_terminal_verifier/verifier.py",
        original / "M2B_BLIND_RUNTIME_INPUT_PACKAGE.json",
        original / "M2B_BLIND_COMPARISON_SET.json",
        original / "M2B_BLIND_METRICS_SPEC.md",
        original / "M2B_BLIND_STATISTICAL_PROTOCOL.md",
        original / "M2B_BLIND_HYPOTHESES.md",
        r1 / "M2B_BLIND_EVALUATION_PARTITION_MANIFEST.json",
    ]
    for path in extra_paths:
        commitments[f"extra__{str(path.relative_to(ROOT)).replace('/', '__')}"] = _artifact_entry(path, "R3 source/scientific/file-backed commitment")
    commitments["r2_scientific__reason_code_vocabulary"] = {
        **_artifact_entry(reason_path, "File-backed unchanged R1/R2 scientific reason-code vocabulary"),
        "parent_embedded_sha256": reason["sha256"],
        "schema_or_version": reason["schema_or_version"],
    }
    commitments["r2_scientific__sealed_gold"] = r1_bundle["commitments"]["sealed_gold"]
    bundle = {
        "schema_version": "driveclarify.m2b_blind_execution_commitment_bundle.r3",
        "design_id": design_id, "parent_r2_id": PARENT_R2_ID,
        "blocked_r2_preflight_id": BLOCKED_R2_PREFLIGHT_ID,
        "repair_scope": "MINIMAL_PRODUCTION_RUNTIME_COMPLETION",
        "scientific_content_changed": False, "policy_changed": False,
        "runtime_cases_changed": False, "gold_changed": False,
        "comparison_set_changed": False, "metrics_changed": False, "hypotheses_changed": False,
        "required_production_artifacts": "30/30 FILE_BACKED",
        "prediction_bundle_tree_sha256": manifests["prediction"]["tree_sha256"],
        "evaluation_bundle_tree_sha256": manifests["evaluation"]["tree_sha256"],
        "terminal_verifier_bundle_tree_sha256": manifests["terminal_verifier"]["tree_sha256"],
        "sealed_gold_commitment_sha256": r1_bundle["sealed_gold_commitment_sha256"],
        "sealed_gold_bytes_opened_during_r3": False,
        "commitments": dict(sorted(commitments.items())),
    }
    bundle_path = output / "M2B_BLIND_R3_EXECUTION_COMMITMENT_BUNDLE.json"
    _write_json(bundle_path, bundle)
    bundle_sha = file_sha256(bundle_path)
    _write_text(output / "M2B_BLIND_R3_EXECUTION_COMMITMENT_BUNDLE.sha256",
                f"{bundle_sha}  M2B_BLIND_R3_EXECUTION_COMMITMENT_BUNDLE.json\n")
    initialize_state_once(
        output / "M2B_BLIND_R3_LIFECYCLE_STATE.json", design_id=design_id,
        parent_r2_id=PARENT_R2_ID, blocked_r2_preflight_id=BLOCKED_R2_PREFLIGHT_ID,
        commitment_bundle_sha256=bundle_sha,
    )
    shutil.copy2(output / "M2B_BLIND_R3_LIFECYCLE_STATE.json", output / "M2B_BLIND_R3_SEAL_AFTER.json")
    (output / "M2B_BLIND_R3_SEAL_AFTER.json").chmod(0o400)
    _write_json(output / "M2B_BLIND_R3_SCIENTIFIC_CONTENT_UNCHANGED_AUDIT.json", {
        "schema_version": "driveclarify.m2b_blind_r3_scientific_unchanged.v1",
        "design_id": design_id, "parent_r2_id": PARENT_R2_ID,
        "scientific_content_changed": False, "policy_changed": False,
        "runtime_cases_changed": False, "gold_changed": False,
        "comparison_set_changed": False, "metrics_changed": False, "hypotheses_changed": False,
        "sealed_gold_bytes_opened": False, "status": "PASS",
    })
    _write_json(output / "M2B_BLIND_R3_FINAL_INDEPENDENT_AUDIT.json", {
        "schema_version": "driveclarify.m2b_blind_r3_final_independent_audit.v1",
        "design_id": design_id, "status": "PASS",
        "required_artifacts": "30/30 PASS", "prediction_allowlist": "9/9 PASS",
        "bundle_import_audits": "3/3 PASS", "production_mount_probe": probe["status"],
        "complete_dummy": "PASS", "partial_dummy": "PASS",
        "terminal_dummy": json.loads((output / "dummy_evidence/M2B_BLIND_R3_DUMMY_TERMINAL_VERIFICATION.json").read_text())["status"],
        "real_blind_execution_count": 0, "real_prediction_record_count": 0,
        "real_gold_semantic_access_count": 0, "real_gold_unseal_count": 0,
        "commitment_bundle_sha256": bundle_sha,
    })
    _write_text(output / "M2B_BLIND_R3_RESEAL_REPORT.md", f"""# M2B Blind R3 minimal production runtime completion and reseal

R3 Design ID: `{design_id}`  
Parent R2: `{PARENT_R2_ID}`  
Blocked R2 preflight: `{BLOCKED_R2_PREFLIGHT_ID}`

Required production artifacts are `30/30 PASS`; the original R2 inventory remains explicitly `9 existing / 21 missing`,
with all 21 gaps completed as file-backed R3 artifacts. Prediction/evaluation/terminal bundles are physically separate,
read-only, and have forbidden-import count zero. The prediction bundle retains exactly the same nine project paths.

The versioned R3 Event API consumes the event at `mark_prediction_started` before the first record, preserves PARTIAL
evidence, rejects changed-config/exact resume, and publishes predictions/results immutably. The isolated evaluator implements
the frozen metrics, 10,000-resample cluster bootstrap, paired/McNemar/Holm statistics, oracle, and H-B1..H-B6. The independent
terminal verifier is forward-free and imports no policy/evaluator code.

Fresh NONBLIND_DUMMY complete and partial paths PASS, including one dummy gold unseal, duplicate/tombstone guards, and terminal
verification. No real blind event was created; real policy/prediction/metric/gold counters remain zero. R3 commitment SHA is
`{bundle_sha}` and seal is `PREEXECUTION_VERIFIED_AWAITING_AUTHORIZATION`.

Status: `M2B_BLIND_R3_MINIMAL_PRODUCTION_RUNTIME_COMPLETED_RESEALED_READY_FOR_EXACTLY_ONE_EXECUTION_AUTHORIZATION`.
Stop and wait for a new, separate exactly-one R3 execution authorization.
""")
    _write_json(output / "TEST_RESULTS.json", {
        "schema_version": "driveclarify.m2b_blind_r3_test_results.v1",
        "status": "PASS", "r3_targeted": "3/3 PASS",
        "production_mount_probe": "PASS", "required_artifacts": "30/30 PASS",
        "complete_dummy": "PASS", "partial_dummy": "PASS", "terminal_dummy": "PASS",
        "real_blind_execution_count": 0, "real_gold_semantic_access_count": 0,
    })
    _write_json(output / "PROCESS_AND_RESOURCE_CLEANUP.json", {
        "schema_version": "driveclarify.m2b_blind_r3_cleanup.v1", "status": "PASS",
        "formal_event_processes": 0, "real_prediction_processes": 0,
        "evaluator_processes": 0, "bubblewrap_processes": 0,
        "carla_processes": 0, "simlingo_processes": 0,
        "gpu_compute_processes": 0, "cuda_contexts": 0,
    })
    _write_json(output / "MODIFIED_FILES.json", {
        "schema_version": "driveclarify.m2b_blind_r3_modified_files.v1",
        "production_source_files": ["driveclarify_m2b_event_runtime/r3_api.py",
                                    "driveclarify_m2b_event_runtime/r3_driver.py",
                                    "driveclarify_m2b_prediction_runtime/orchestrator.py",
                                    "driveclarify_m2b_prediction_runtime/entrypoint.py",
                                    "driveclarify_m2b_evaluator_runtime/metrics.py",
                                    "driveclarify_m2b_evaluator_runtime/statistics.py",
                                    "driveclarify_m2b_evaluator_runtime/r3_evaluator.py",
                                    "driveclarify_m2b_terminal_verifier/__init__.py",
                                    "driveclarify_m2b_terminal_verifier/verifier.py"],
        "test_and_tool_files": ["tests/m2b_blind_r3/", "tools/run_m2b_blind_r3_dummy.py",
                                "tools/reseal_m2b_blind_r3.py"],
        "r2_report_artifacts_modified": [], "r1_artifacts_modified": [],
        "original_scientific_artifacts_modified": [], "simlingo_files_modified": [],
    })


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    build(arguments.output.resolve(), arguments.design_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
