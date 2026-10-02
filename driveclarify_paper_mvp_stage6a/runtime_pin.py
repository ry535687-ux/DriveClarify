"""Stage 6A SimLingo versioned runtime pin capture and verification.

The historical DriveClarify contract calls the SHA-256 of the raw output of
``git diff --binary`` the protected SimLingo fingerprint.  This module keeps
that definition intact and adds an aggregate state hash for the wider runtime
state needed by Stage 6B.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


HISTORICAL_PROTECTED_DIFF_SHA256 = (
    "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34"
)
EXPECTED_STAGE6A_PROTECTED_DIFF_SHA256 = (
    "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058"
)
EXPECTED_STAGE6A_PROTECTED_DIFF_BYTES = 9276
EXPECTED_SIMLINGO_HEAD = "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class RuntimePinError(RuntimeError):
    """Raised when the SimLingo runtime no longer matches the frozen state."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _git(root: Path, *args: str) -> bytes:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        env=env,
        stderr=subprocess.PIPE,
    )


def _split_z(payload: bytes) -> List[str]:
    return [item.decode("utf-8", errors="surrogateescape") for item in payload.split(b"\0") if item]


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _file_record(path: Path, logical_path: str) -> Dict[str, Any]:
    mode = stat.S_IMODE(path.stat().st_mode)
    return {
        "path": logical_path,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "working_tree_mode": format(mode, "04o"),
        "mtime_utc": _iso_mtime(path),
    }


def _untracked_provenance(path: str) -> str:
    if path == "team_code/driveclarify_probe_hook.py":
        return "VERSIONED_DRIVECLARIFY_RUNTIME_PROBE_HOOK"
    if path.startswith("metric_info_") or "/metric_info_" in path:
        return "PREEXISTING_SIMLINGO_RUNTIME_OUTPUT"
    if path in {".vscode/launch.json", "DATAFLOW.md", "simlingo.json"}:
        return "PREEXISTING_LOCAL_CONFIGURATION_OR_DOCUMENTATION"
    return "PREEXISTING_UNTRACKED_NONPROTECTED_FILE"


def _tracked_record(root: Path, relative_path: str) -> Dict[str, Any]:
    path = root / relative_path
    record = _file_record(path, relative_path)
    index_line = _git(root, "ls-files", "-s", "--", relative_path).decode().strip()
    if index_line:
        parts = index_line.split()
        record["index_mode"] = parts[0]
        record["index_blob"] = parts[1]
    base = _git(root, "show", "HEAD:" + relative_path)
    record["head_blob_bytes"] = len(base)
    record["head_blob_sha256"] = sha256_bytes(base)
    return record


def _existing_file_records(root: Path, paths: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for relative in paths:
        path = root / relative
        if not path.is_file():
            raise RuntimePinError("缺少运行时关键文件: {}".format(path))
        records[relative] = _file_record(path, relative)
    return records


def capture_simlingo_runtime(
    simlingo_root: Path,
    checkpoint_path: Path,
    generated_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture the current SimLingo state without modifying that repository."""

    root = simlingo_root.resolve()
    if not (root / ".git").exists():
        raise RuntimePinError("SimLingo 根目录不是 Git 仓库: {}".format(root))

    before_status = _git(root, "status", "--porcelain=v1", "-z")
    before_diff = _git(root, "diff", "--binary")
    before_cached = _git(root, "diff", "--cached", "--binary")

    branch = _git(root, "branch", "--show-current").decode().strip()
    head = _git(root, "rev-parse", "HEAD").decode().strip()
    status_v2 = _git(root, "status", "--porcelain=v2", "-z")
    modified_paths = sorted(_split_z(_git(root, "diff", "--name-only", "-z")))
    untracked_paths = sorted(_split_z(_git(root, "ls-files", "--others", "--exclude-standard", "-z")))
    untracked_newline_bytes = ("\n".join(untracked_paths) + ("\n" if untracked_paths else "")).encode("utf-8")

    tracked_records = [_tracked_record(root, path) for path in modified_paths]
    untracked_records = []
    for relative in untracked_paths:
        record = _file_record(root / relative, relative)
        record["provenance_class"] = _untracked_provenance(relative)
        untracked_records.append(record)

    key_files = _existing_file_records(
        root,
        [
            "team_code/agent_simlingo.py",
            "team_code/driveclarify_probe_hook.py",
            "team_code/lateral_controller.py",
            "team_code/nav_planner.py",
            "team_code/transfuser_utils.py",
            "simlingo_training/models/adaptors/adaptors.py",
            "simlingo_training/models/driving.py",
            "Bench2Drive/leaderboard/leaderboard/leaderboard_evaluator.py",
            "Bench2Drive/leaderboard/scripts/run_evaluation_debug.sh",
            "start_eval_simlingo.py",
            "setup_carla.sh",
        ],
    )
    checkpoint = _file_record(checkpoint_path.resolve(), str(checkpoint_path.resolve()))

    key_hash_map = {path: data["sha256"] for path, data in sorted(key_files.items())}
    aggregate_inputs = {
        "schema_version": "driveclarify.simlingo.runtime_state_aggregate_inputs.v1",
        "branch": branch,
        "head": head,
        "protected_diff_bytes": len(before_diff),
        "protected_diff_sha256": sha256_bytes(before_diff),
        "staged_diff_bytes": len(before_cached),
        "staged_diff_sha256": sha256_bytes(before_cached),
        "porcelain_v1_z_sha256": sha256_bytes(before_status),
        "porcelain_v2_z_sha256": sha256_bytes(status_v2),
        "untracked_inventory_count": len(untracked_paths),
        "untracked_inventory_sorted_newline_sha256": sha256_bytes(untracked_newline_bytes),
        "runtime_key_file_sha256": key_hash_map,
        "checkpoint_sha256": checkpoint["sha256"],
    }
    aggregate_sha = sha256_bytes(canonical_json_bytes(aggregate_inputs))

    after_status = _git(root, "status", "--porcelain=v1", "-z")
    after_diff = _git(root, "diff", "--binary")
    after_cached = _git(root, "diff", "--cached", "--binary")
    read_only_unchanged = (
        before_status == after_status
        and before_diff == after_diff
        and before_cached == after_cached
    )
    if not read_only_unchanged:
        raise RuntimePinError("只读 capture 前后 SimLingo 状态发生变化")

    generated_at = generated_at_utc or datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "driveclarify.simlingo.runtime_fingerprint.v1",
        "generated_at_utc": generated_at,
        "capture_mode": "READ_ONLY_GIT_OPTIONAL_LOCKS_0",
        "classification": {
            "primary": "C.LEGITIMATE_VERSIONED_RUNTIME_EVOLUTION",
            "entry_state_relationship": "A.VERIFIED_PREEXISTING_PROTECTED_SIMLINGO_DIRTY_STATE",
            "algorithm_drift": False,
            "unattributed_or_unauthorized_drift": False,
        },
        "pin_contract": {
            "algorithm": "sha256(raw stdout bytes of `git diff --binary`)",
            "historical_pin": HISTORICAL_PROTECTED_DIFF_SHA256,
            "current_runtime_pin": sha256_bytes(before_diff),
            "historical_pin_not_equal_current_runtime_pin": (
                HISTORICAL_PROTECTED_DIFF_SHA256 != sha256_bytes(before_diff)
            ),
            "runtime_state_aggregate_sha256": aggregate_sha,
            "runtime_state_aggregate_inputs": aggregate_inputs,
        },
        "repository": {
            "root": str(root),
            "branch": branch,
            "head": head,
            "protected_status": "DIRTY_VERSIONED_AND_PINNED",
            "protected_tracked_diff_bytes": len(before_diff),
            "protected_tracked_diff_sha256": sha256_bytes(before_diff),
            "staged_diff_bytes": len(before_cached),
            "staged_diff_sha256": sha256_bytes(before_cached),
            "porcelain_v1_z_bytes": len(before_status),
            "porcelain_v1_z_sha256": sha256_bytes(before_status),
            "porcelain_v2_z_bytes": len(status_v2),
            "porcelain_v2_z_sha256": sha256_bytes(status_v2),
            "modified_tracked_file_count": len(tracked_records),
            "modified_tracked_files": tracked_records,
            "untracked_inventory": {
                "count": len(untracked_records),
                "sorted_newline_bytes": len(untracked_newline_bytes),
                "sorted_newline_sha256": sha256_bytes(untracked_newline_bytes),
                "files": untracked_records,
            },
        },
        "runtime_key_files": key_files,
        "checkpoint": checkpoint,
        "read_only_capture_audit": {
            "before_after_identical": read_only_unchanged,
            "simlingo_files_written": 0,
            "git_mutating_commands": 0,
        },
    }


def assert_expected_stage6a_capture(capture: Mapping[str, Any]) -> None:
    repository = capture["repository"]
    pin_contract = capture["pin_contract"]
    checks = {
        "head": (repository["head"], EXPECTED_SIMLINGO_HEAD),
        "protected_diff_sha256": (
            repository["protected_tracked_diff_sha256"],
            EXPECTED_STAGE6A_PROTECTED_DIFF_SHA256,
        ),
        "protected_diff_bytes": (
            repository["protected_tracked_diff_bytes"],
            EXPECTED_STAGE6A_PROTECTED_DIFF_BYTES,
        ),
        "staged_diff_sha256": (repository["staged_diff_sha256"], EMPTY_SHA256),
        "current_runtime_pin": (
            pin_contract["current_runtime_pin"],
            EXPECTED_STAGE6A_PROTECTED_DIFF_SHA256,
        ),
    }
    mismatches = {
        name: {"actual": actual, "expected": expected}
        for name, (actual, expected) in checks.items()
        if actual != expected
    }
    if mismatches:
        raise RuntimePinError(
            "Stage6A SimLingo capture 不匹配冻结状态: "
            + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
        )


def verify_frozen_runtime(
    manifest_path: Path,
    simlingo_root: Optional[Path] = None,
    checkpoint_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Verify both the frozen manifest and the live Stage 6A runtime state."""

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = simlingo_root or Path(manifest["repository"]["root"])
    checkpoint = checkpoint_path or Path(manifest["checkpoint"]["path"])
    live = capture_simlingo_runtime(root, checkpoint, generated_at_utc="VERIFY_ONLY")
    fields = {
        "branch": (live["repository"]["branch"], manifest["repository"]["branch"]),
        "head": (live["repository"]["head"], manifest["repository"]["head"]),
        "protected_tracked_diff_bytes": (
            live["repository"]["protected_tracked_diff_bytes"],
            manifest["repository"]["protected_tracked_diff_bytes"],
        ),
        "protected_tracked_diff_sha256": (
            live["repository"]["protected_tracked_diff_sha256"],
            manifest["repository"]["protected_tracked_diff_sha256"],
        ),
        "staged_diff_sha256": (
            live["repository"]["staged_diff_sha256"],
            manifest["repository"]["staged_diff_sha256"],
        ),
        "porcelain_v1_z_sha256": (
            live["repository"]["porcelain_v1_z_sha256"],
            manifest["repository"]["porcelain_v1_z_sha256"],
        ),
        "porcelain_v2_z_sha256": (
            live["repository"]["porcelain_v2_z_sha256"],
            manifest["repository"]["porcelain_v2_z_sha256"],
        ),
        "untracked_inventory_sorted_newline_sha256": (
            live["repository"]["untracked_inventory"]["sorted_newline_sha256"],
            manifest["repository"]["untracked_inventory"]["sorted_newline_sha256"],
        ),
        "runtime_state_aggregate_sha256": (
            live["pin_contract"]["runtime_state_aggregate_sha256"],
            manifest["pin_contract"]["runtime_state_aggregate_sha256"],
        ),
    }
    mismatches = {
        name: {"actual": actual, "expected": expected}
        for name, (actual, expected) in fields.items()
        if actual != expected
    }
    historical_integrity = (
        manifest["pin_contract"]["historical_pin"]
        == HISTORICAL_PROTECTED_DIFF_SHA256
    )
    current_pin_contract_integrity = (
        manifest["pin_contract"]["current_runtime_pin"]
        == EXPECTED_STAGE6A_PROTECTED_DIFF_SHA256
        and manifest["pin_contract"]["historical_pin_not_equal_current_runtime_pin"] is True
    )
    passed = not mismatches and historical_integrity and current_pin_contract_integrity
    return {
        "schema_version": "driveclarify.simlingo.runtime_pin_verification.v1",
        "status": "PASS" if passed else "FAIL",
        "historical_pin_integrity": historical_integrity,
        "current_pin_contract_integrity": current_pin_contract_integrity,
        "current_runtime_integrity": not mismatches,
        "mismatches": mismatches,
        "live_runtime_state_aggregate_sha256": live["pin_contract"]["runtime_state_aggregate_sha256"],
    }
