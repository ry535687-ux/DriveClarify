"""Per-file/hash bubblewrap builder for the production prediction namespace."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .contracts import file_sha256


FIXED_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin", "PYTHONPATH": "/app", "PYTHONDONTWRITEBYTECODE": "1",
    "CUDA_VISIBLE_DEVICES": "",
}
FORBIDDEN_CODE_NAMES = frozenset({
    "evaluator.py", "r1_evaluator.py", "reference_solver.py", "independent_verifier.py",
})
FORBIDDEN_CODE_PATTERN = re.compile(
    r"(^|/)([^/]*(?:gold|evaluator|reference|verifier|metrics|bootstrap)[^/]*)$", re.IGNORECASE
)
FORBIDDEN_DIRECTORY_MOUNTS = frozenset({
    "/app", "/repo", "/workspace", "/app/driveclarify_m2b_blind",
    "/app/reports", "/inputs/reports",
})


def load_verified_allowlist(path: Path, repo_root: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "driveclarify.m2b_blind_r2_prediction_code_allowlist.v1":
        raise ValueError("R2_CODE_ALLOWLIST_SCHEMA_INVALID")
    rows = manifest.get("project_files")
    if not isinstance(rows, list) or not rows:
        raise ValueError("R2_CODE_ALLOWLIST_EMPTY")
    destinations: set[str] = set()
    sources: set[str] = set()
    for row in rows:
        required = {
            "canonical_relative_path", "bytes", "sha256", "semantic_purpose",
            "import_role", "read_only_mount_destination", "executable",
            "imported_directly", "transitive_dependency_parent",
        }
        if set(row) != required:
            raise ValueError("R2_CODE_ALLOWLIST_ROW_FIELDS_INVALID")
        relative = PurePosixPath(row["canonical_relative_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("R2_CODE_ALLOWLIST_SOURCE_NOT_CANONICAL")
        source = (repo_root / Path(*relative.parts)).resolve(strict=True)
        if not source.is_file() or repo_root.resolve() not in source.parents:
            raise ValueError("R2_CODE_ALLOWLIST_SOURCE_NOT_PROJECT_FILE")
        destination = str(PurePosixPath(row["read_only_mount_destination"]))
        if not destination.startswith("/app/") or destination in FORBIDDEN_DIRECTORY_MOUNTS:
            raise ValueError("R2_CODE_ALLOWLIST_DESTINATION_INVALID")
        if source.name in FORBIDDEN_CODE_NAMES or FORBIDDEN_CODE_PATTERN.search(relative.as_posix()):
            raise ValueError("R2_CODE_ALLOWLIST_FORBIDDEN_MODULE")
        if source.stat().st_size != row["bytes"] or file_sha256(source) != row["sha256"]:
            raise ValueError("R2_CODE_ALLOWLIST_FILE_HASH_MISMATCH")
        if relative.as_posix() in sources or destination in destinations:
            raise ValueError("R2_CODE_ALLOWLIST_DUPLICATE")
        sources.add(relative.as_posix())
        destinations.add(destination)
    return manifest


def _input_source(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("R2_SANDBOX_INPUT_MUST_BE_FILE")
    lowered = str(resolved).lower()
    if any(fragment in lowered for fragment in ("sealed_evaluation_gold", "gold_key", "decryption_key")):
        raise ValueError("R2_SANDBOX_FORBIDDEN_INPUT_SOURCE")
    return resolved


def build_prediction_sandbox_command(*, bwrap: Path, repo_root: Path, allowlist_path: Path,
                                     executable_inside: str, arguments: Sequence[str],
                                     read_only_inputs: Mapping[str, Path], output_dir: Path) -> list[str]:
    manifest = load_verified_allowlist(allowlist_path, repo_root)
    command = [
        str(bwrap.resolve(strict=True)), "--unshare-all", "--die-with-parent", "--new-session",
        "--cap-drop", "ALL", "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--dir", "/inputs", "--dir", "/app", "--dir", "/outputs",
        "--dir", "/app/driveclarify_m2b_prediction_runtime", "--dir", "/app/driveclarify_decision",
    ]
    for target, source in sorted(read_only_inputs.items()):
        pure_target = str(PurePosixPath(target))
        if not pure_target.startswith("/inputs/") or pure_target == "/inputs":
            raise ValueError("R2_SANDBOX_INPUT_TARGET_INVALID")
        command.extend(("--ro-bind", str(_input_source(source)), pure_target))
    for row in manifest["project_files"]:
        source = (repo_root / row["canonical_relative_path"]).resolve(strict=True)
        command.extend(("--ro-bind", str(source), row["read_only_mount_destination"]))
    output = output_dir.resolve(strict=True)
    if not output.is_dir():
        raise ValueError("R2_SANDBOX_OUTPUT_NOT_DIRECTORY")
    command.extend(("--bind", str(output), "/outputs"))
    for key, value in FIXED_ENVIRONMENT.items():
        command.extend(("--setenv", key, value))
    command.extend(("--chdir", "/app", executable_inside, *arguments))
    lowered = "\n".join(command).lower()
    if any(fragment in lowered for fragment in ("sealed_evaluation_gold", "gold_key", "decryption_key")):
        raise ValueError("R2_SANDBOX_ARGV_SECRET_EXPOSURE")
    return command


def run_sandbox_command(command: Sequence[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), env={}, text=True, capture_output=True,
                          timeout=timeout, check=False)


def inherited_environment_exposure_count(environment: Mapping[str, str]) -> int:
    return sum(any(fragment in f"{key}={value}".lower()
                   for fragment in ("gold", "oracle", "evaluator", "reference", "key"))
               for key, value in environment.items())

