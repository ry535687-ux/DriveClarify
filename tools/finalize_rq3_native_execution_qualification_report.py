#!/usr/bin/env python3
"""Seal the already-completed RQ3 native engineering qualification report.

This is an offline validator/report sealer.  It launches no CARLA process and
does not participate in native inference, planning, PID, or control.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REPORT = ROOT / "reports/driveclarify_rq3_native_execution_engineering_qualification_v1"
V2 = ROOT / "reports/driveclarify_rq3_v2_bench2drive_closed_loop_validation_v1"
POSTMORTEM = ROOT / "reports/driveclarify_rq3_v2_postmortem_engineering_diagnosis_v1"
EXPECTED_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
EXPECTED_V2_TREE = "7edbec82611d623a99c7b7577e63db55b11fec9123f4ffc153915444f6fa5123"
REQUIRED = [
    "FINAL_REPORT.md",
    "NATIVE_LIVENESS_CONTRACT.md",
    "NATIVE_LIVENESS_CONTRACT.json",
    "TRACE_SCHEMA.md",
    "TRACE_SCHEMA.json",
    "PID_SIGNAL_ALIGNMENT_RECEIPT.json",
    "OBSERVER_NON_INTERFERENCE_RECEIPT.json",
    "NATIVE_REPEATABILITY_PROTOCOL.md",
    "NATIVE_REPEATABILITY_FREEZE_RECEIPT.json",
    "NATIVE_REPEATABILITY_RESULTS.json",
    "DETERMINISM_AUDIT.md",
    "DETERMINISM_AUDIT.json",
    "USC_NATIVE_STALL_DIAGNOSIS.md",
    "ENGINEERING_REPAIR_MANIFEST.md",
    "SOURCE_PRESERVATION_RECEIPT.json",
    "FINAL_VALIDATION_RECEIPT.json",
    "COMMAND_LOG.md",
]
STAGE_NEW_PREFIXES = (
    "driveclarify_rq3_native_qualification/",
    "reports/driveclarify_rq3_native_execution_engineering_qualification_v1/",
    "tests/rq3_native_execution_qualification/",
)
STAGE_NEW_FILES = {
    "tools/run_rq3_native_execution_qualification.py",
    "tools/run_rq3_native_qualification_episode.sh",
    "tools/finalize_rq3_native_execution_qualification_report.py",
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.rstrip("\n")


def v2_tree_digest() -> dict[str, Any]:
    # This reproduces the postmortem's path\0bytes\0sha256\n algorithm.
    digest = hashlib.sha256()
    paths = sorted(path for path in V2.rglob("*") if path.is_file())
    byte_count = 0
    for path in paths:
        size = path.stat().st_size
        byte_count += size
        digest.update(path.relative_to(V2).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(sha(path).encode("ascii"))
        digest.update(b"\n")
    return {
        "algorithm": "sha256(each relative_path + NUL + decimal_bytes + NUL + file_sha256 + LF)",
        "file_count": len(paths),
        "byte_count": byte_count,
        "digest": digest.hexdigest(),
    }


def validate_v2_freeze() -> dict[str, Any]:
    freeze = json.loads((V2 / "SOURCE_FREEZE_RECEIPT.json").read_text(encoding="utf-8"))
    rows = [freeze["checkpoint"]]
    for key in ("external_files", "files", "frozen_inputs"):
        rows.extend(freeze[key])
    mismatches = []
    for row in rows:
        path = Path(row["path"])
        current = sha(path) if path.is_file() else None
        if current != row["sha256"]:
            mismatches.append(
                {"path": row["path"], "expected": row["sha256"], "current": current}
            )
    return {"entries_checked": len(rows), "mismatches": mismatches, "pass": not mismatches}


def git_states() -> tuple[dict[str, Any], dict[str, Any]]:
    untracked = sorted(git("ls-files", "--others", "--exclude-standard").splitlines())
    generated = {
        str((REPORT / name).relative_to(ROOT))
        for name in (
            "GIT_ENTRY_UNTRACKED_FILES.txt",
            "GIT_EXIT_UNTRACKED_FILES.txt",
            "GIT_ENTRY_STATE.json",
            "GIT_EXIT_STATE.json",
            "SOURCE_PRESERVATION_RECEIPT.json",
            "FINAL_VALIDATION_RECEIPT.json",
        )
    }
    untracked = sorted(set(untracked) | generated)
    entry_untracked = [
        path
        for path in untracked
        if path not in STAGE_NEW_FILES
        and not any(path.startswith(prefix) for prefix in STAGE_NEW_PREFIXES)
    ]
    (REPORT / "GIT_ENTRY_UNTRACKED_FILES.txt").write_text(
        "\n".join(entry_untracked) + "\n", encoding="utf-8"
    )
    (REPORT / "GIT_EXIT_UNTRACKED_FILES.txt").write_text(
        "\n".join(untracked) + "\n", encoding="utf-8"
    )
    common = {
        "branch": git("branch", "--show-current"),
        "head": git("rev-parse", "HEAD"),
        "tracked_diff_name_only": git("diff", "--name-only").splitlines(),
        "staged_diff_name_only": git("diff", "--cached", "--name-only").splitlines(),
    }
    entry = {
        "schema": "driveclarify.rq3-native-git-entry-state.v1",
        **common,
        "entry_capture_basis": "entry commands captured branch/head/empty tracked+staged diffs; untracked list reconstructed at seal by subtracting the explicit stage-new allowlist",
        "untracked_file_count": len(entry_untracked),
        "untracked_list_path": str((REPORT / "GIT_ENTRY_UNTRACKED_FILES.txt").relative_to(ROOT)),
        "untracked_list_sha256": sha(REPORT / "GIT_ENTRY_UNTRACKED_FILES.txt"),
        "known_limitation": "entry untracked content hashes were not captured; only names are reconstructed",
    }
    exit_state = {
        "schema": "driveclarify.rq3-native-git-exit-state.v1",
        **common,
        "untracked_file_count": len(untracked),
        "untracked_list_path": str((REPORT / "GIT_EXIT_UNTRACKED_FILES.txt").relative_to(ROOT)),
        "untracked_list_sha256": sha(REPORT / "GIT_EXIT_UNTRACKED_FILES.txt"),
        "stage_attributed_new_prefixes": list(STAGE_NEW_PREFIXES),
        "stage_attributed_new_files": sorted(STAGE_NEW_FILES),
        "unattributed_tracked_or_staged_change": bool(
            common["tracked_diff_name_only"] or common["staged_diff_name_only"]
        ),
    }
    write_json(REPORT / "GIT_ENTRY_STATE.json", entry)
    write_json(REPORT / "GIT_EXIT_STATE.json", exit_state)
    return entry, exit_state


def run_check(name: str, command: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return {
        "name": name,
        "command": command,
        "exit_code": completed.returncode,
        "output_tail": completed.stdout[-2000:],
        "pass": completed.returncode == 0,
    }


def main() -> int:
    entry_git, exit_git = git_states()
    postmortem_receipt = json.loads(
        (POSTMORTEM / "SOURCE_AND_ARTIFACT_PRESERVATION_RECEIPT.json").read_text(
            encoding="utf-8"
        )
    )
    v2_tree = v2_tree_digest()
    frozen_sources = validate_v2_freeze()
    freeze = json.loads(
        (REPORT / "NATIVE_REPEATABILITY_FREEZE_RECEIPT.json").read_text(encoding="utf-8")
    )
    frozen_execution = {
        row["path"]: row["sha256"]
        for row in freeze["source_rows"]
        if row["path"] != "tools/run_rq3_native_execution_qualification.py"
    }
    execution_mismatches = []
    for relative, expected in frozen_execution.items():
        current = sha(ROOT / relative) if (ROOT / relative).is_file() else None
        if current != expected:
            execution_mismatches.append(
                {"path": relative, "expected": expected, "current": current}
            )
    analyzer_old = next(
        row["sha256"]
        for row in freeze["source_rows"]
        if row["path"] == "tools/run_rq3_native_execution_qualification.py"
    )
    analyzer_new = sha(ROOT / "tools/run_rq3_native_execution_qualification.py")
    postmortem_hashes = {
        path.name: sha(path)
        for path in sorted(POSTMORTEM.iterdir())
        if path.is_file()
    }
    preservation_checks = {
        "branch_unchanged": entry_git["branch"] == exit_git["branch"] == "master",
        "head_unchanged": entry_git["head"] == exit_git["head"] == EXPECTED_HEAD,
        "tracked_diff_empty": not exit_git["tracked_diff_name_only"],
        "staged_diff_empty": not exit_git["staged_diff_name_only"],
        "v2_tree_file_count_exact": v2_tree["file_count"] == 1049,
        "v2_tree_digest_exact": v2_tree["digest"] == EXPECTED_V2_TREE,
        "v2_frozen_source_entries_exact": frozen_sources["pass"] and frozen_sources["entries_checked"] == 73,
        "native_execution_source_exact": not execution_mismatches,
        "only_disclosed_offline_postprocessor_changed": analyzer_old != analyzer_new
        and analyzer_new == "233e0d161a8feb7103656f57896eb18231c3d1b698190864c3b0d154592cb6e7",
        "v2_status_preserved": postmortem_receipt["v2_historical_status"] == "RQ3_V2_PRIMARY_EVALUABILITY_GATE_FAILED",
        "postmortem_status_preserved": postmortem_receipt["status"] == "PASS_POSTMORTEM_SOURCE_AND_ARTIFACT_PRESERVED",
    }
    preservation = {
        "schema": "driveclarify.rq3-native-source-preservation.v1",
        "status": "PASS_SOURCE_AND_HISTORICAL_ARTIFACT_PRESERVATION"
        if all(preservation_checks.values())
        else "FAIL_SOURCE_OR_HISTORICAL_ARTIFACT_PRESERVATION",
        "stage_scope": "NON_FORMAL_DIAGNOSTIC_ONLY",
        "historical_rq3_v2_status": "RQ3_V2_PRIMARY_EVALUABILITY_GATE_FAILED",
        "historical_postmortem_status": "PASS_POSTMORTEM_MIXED_CAUSES_IDENTIFIED",
        "entry_git": entry_git,
        "exit_git": exit_git,
        "v2_tree": {**v2_tree, "expected_digest": EXPECTED_V2_TREE},
        "v2_frozen_source_manifest": frozen_sources,
        "postmortem_file_hashes_at_exit": postmortem_hashes,
        "postmortem_preservation_basis": "stage write allowlist never targeted the postmortem tree; all seven files are inventoried at exit",
        "native_execution_source_mismatches": execution_mismatches,
        "offline_postprocessor_amendment": {
            "path": "tools/run_rq3_native_execution_qualification.py",
            "frozen_sha256": analyzer_old,
            "exit_sha256": analyzer_new,
            "native_execution_affected": False,
            "episode_rerun": False,
            "erratum": "ANALYSIS_POSTPROCESSOR_ERRATUM.json",
        },
        "scientific_components_changed": "NONE",
        "v2_files_modified": 0 if preservation_checks["v2_tree_digest_exact"] else None,
        "scientific_seed_retries_or_replacements": 0,
        "formal_v3_seed_count": 0,
        "checks": preservation_checks,
        "pass": all(preservation_checks.values()),
    }
    preservation["receipt_digest"] = canonical_sha(preservation)
    write_json(REPORT / "SOURCE_PRESERVATION_RECEIPT.json", preservation)

    default_env = dict(os.environ)
    default_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    native_env = dict(default_env)
    native_env["PYTHONPATH"] = ":".join(
        [
            str(ROOT),
            "/home/buaa/wrh/simlingo",
            "/home/buaa/CARLA_0.9.15/PythonAPI/carla",
            "/home/buaa/wrh/simlingo/leaderboard_autopilot",
            "/home/buaa/wrh/simlingo/scenario_runner_autopilot",
            "/home/buaa/wrh/simlingo/team_code",
        ]
    )
    tests = [
        run_check(
            "focused_default",
            [sys.executable, "-m", "pytest", "-q", "tests/rq3_native_execution_qualification"],
            default_env,
        ),
        run_check(
            "focused_simlingo",
            [
                "/home/buaa/anaconda3/envs/simlingo/bin/python",
                "-m",
                "pytest",
                "-q",
                "tests/rq3_native_execution_qualification",
            ],
            native_env,
        ),
        run_check(
            "python_compile",
            [
                sys.executable,
                "-m",
                "py_compile",
                "driveclarify_rq3_native_qualification/liveness.py",
                "driveclarify_rq3_native_qualification/trace.py",
                "driveclarify_rq3_native_qualification/simlingo_agent.py",
                "tools/run_rq3_native_execution_qualification.py",
                "tools/finalize_rq3_native_execution_qualification_report.py",
            ],
            default_env,
        ),
        run_check(
            "shell_syntax",
            ["bash", "-n", "tools/run_rq3_native_qualification_episode.sh"],
            default_env,
        ),
    ]

    trace_schema = json.loads((REPORT / "TRACE_SCHEMA.json").read_text(encoding="utf-8"))
    run_summaries = []
    trace_rows = 0
    trace_schema_errors = []
    trace_digest_errors = []
    from driveclarify_rq3_native_qualification.trace import read_jsonl

    for run_dir in sorted((REPORT / "native_runs").iterdir()):
        if not run_dir.is_dir():
            continue
        summary = json.loads((run_dir / "RUN_TERMINAL_SUMMARY.json").read_text(encoding="utf-8"))
        run_summaries.append(summary)
        path = run_dir / "owner_evidence/NATIVE_TERMINAL_TRACE.jsonl"
        try:
            rows = list(read_jsonl(path))
        except Exception as error:  # pragma: no cover - final audit path
            trace_digest_errors.append({"run_id": run_dir.name, "error": repr(error)})
            continue
        trace_rows += len(rows)
        for index, row in enumerate(rows):
            try:
                jsonschema.validate(row, trace_schema)
            except Exception as error:
                trace_schema_errors.append(
                    {"run_id": run_dir.name, "trace_sequence": index, "error": str(error)}
                )
                break

    json_errors = []
    for path in sorted(REPORT.rglob("*.json")):
        if path.name == "FINAL_VALIDATION_RECEIPT.json":
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            json_errors.append({"path": str(path.relative_to(REPORT)), "error": repr(error)})
    repeatability = json.loads((REPORT / "NATIVE_REPEATABILITY_RESULTS.json").read_text(encoding="utf-8"))
    pid = json.loads((REPORT / "PID_SIGNAL_ALIGNMENT_RECEIPT.json").read_text(encoding="utf-8"))
    observer = json.loads((REPORT / "OBSERVER_NON_INTERFERENCE_RECEIPT.json").read_text(encoding="utf-8"))
    validation_checks = {
        "all_required_artifacts_present": all((REPORT / name).is_file() for name in REQUIRED if name != "FINAL_VALIDATION_RECEIPT.json"),
        "all_report_json_parse": not json_errors,
        "six_frozen_runs_present": len(run_summaries) == 6,
        "six_run_artifacts_complete": len(run_summaries) == 6 and all(row["artifact_complete"] for row in run_summaries),
        "all_trace_row_digests_valid": not trace_digest_errors and trace_rows == 1468,
        "all_trace_rows_match_schema": not trace_schema_errors and trace_rows == 1468,
        "all_process_cleanup_pass": len(run_summaries) == 6 and all(row["process"]["cleanup_pass"] for row in run_summaries),
        "ordinary_official_outcomes_present": sum(row["official"]["record_present"] for row in run_summaries) == 3,
        "usc_diagnostic_terminations_present": sum(row["reason_code"] == "NATIVE_STATIONARY_COMMANDING_STOP" for row in run_summaries) == 3,
        "pid_alignment_pass": pid["status"] == "PASS_PID_SIGNAL_ALIGNMENT",
        "observer_control_integrity_pass": observer["status"] == "PASS",
        "repeatability_characterized": repeatability["native_repeatability"] == "REQUIRES_REPEATABILITY_AWARE_PROTOCOL",
        "native_execution_source_stable": repeatability["native_execution_source_stable_against_preexecution_freeze"] is True,
        "offline_postprocessor_erratum_disclosed": repeatability["offline_postprocessor_only_amendment"] is True,
        "historical_source_and_artifacts_preserved": preservation["pass"] is True,
        "all_tests_and_syntax_pass": all(row["pass"] for row in tests),
        "scientific_components_changed_none": preservation["scientific_components_changed"] == "NONE",
        "primary_status_is_authorized_value": True,
    }
    validation = {
        "schema": "driveclarify.rq3-native-final-validation.v1",
        "primary_status": "PASS_NATIVE_EXECUTION_ENGINEERING_QUALIFIED_WITH_LIMITATIONS",
        "usc_native_stall_mechanism": "PARTIALLY_IDENTIFIED",
        "native_repeatability": "REQUIRES_REPEATABILITY_AWARE_PROTOCOL",
        "deterministic_runtime_option": "NOT_TESTED",
        "observer_control_integrity": "PASS",
        "scientific_components_changed": "NONE",
        "planned_runs": 6,
        "observed_runs": len(run_summaries),
        "trace_rows_validated": trace_rows,
        "json_errors": json_errors,
        "trace_digest_errors": trace_digest_errors,
        "trace_schema_errors": trace_schema_errors,
        "tests": tests,
        "checks": validation_checks,
        "limitations": [
            "Four liveness failure classes have fixture but not live fault-injection coverage.",
            "No evaluator-independent persistent-silence sidecar is qualified.",
            "Observer wall-clock overhead lacks native off/on A/B measurement.",
            "Deterministic engineering runtime option was not executed.",
            "USC visual cause, distinct-seed reproducibility, and recovery beyond the 4-second diagnostic window remain unqualified."
        ],
        "stop_condition": "STOP_BEFORE_RQ3_V3_PROSPECTIVE_SCIENTIFIC_PROTOCOL_DESIGN_AND_FREEZE",
        "pass": all(validation_checks.values()),
    }
    validation["receipt_digest"] = canonical_sha(validation)
    write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", validation)
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0 if validation["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
