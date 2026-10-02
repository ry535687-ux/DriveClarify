"""Bubblewrap allowlist sandbox for future R1 prediction execution."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping, Sequence


FORBIDDEN_MOUNT_FRAGMENTS = ("gold", "oracle", "evaluator", "latent", "regret", "key")
FIXED_ENVIRONMENT = {"PATH": "/usr/bin:/bin", "PYTHONPATH": "/app", "CUDA_VISIBLE_DEVICES": ""}


def _safe_source(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    lowered = str(resolved).lower()
    if any(fragment in lowered for fragment in FORBIDDEN_MOUNT_FRAGMENTS):
        raise ValueError("R1_SANDBOX_FORBIDDEN_MOUNT_SOURCE")
    return resolved


def build_prediction_sandbox_command(
    *, bwrap: Path, executable_inside: str, arguments: Sequence[str],
    read_only_files: Mapping[str, Path], read_only_code_dirs: Mapping[str, Path],
    output_dir: Path,
) -> list[str]:
    command = [
        str(bwrap.resolve(strict=True)), "--unshare-all", "--die-with-parent", "--new-session",
        "--cap-drop", "ALL", "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--dir", "/inputs", "--dir", "/app", "--dir", "/outputs",
    ]
    for target, source in sorted(read_only_files.items()):
        if not target.startswith("/inputs/") or any(x in target.lower() for x in FORBIDDEN_MOUNT_FRAGMENTS):
            raise ValueError("R1_SANDBOX_INPUT_TARGET_NOT_ALLOWLISTED")
        command.extend(("--ro-bind", str(_safe_source(source)), target))
    for target, source in sorted(read_only_code_dirs.items()):
        if not target.startswith("/app/") or any(x in target.lower() for x in FORBIDDEN_MOUNT_FRAGMENTS):
            raise ValueError("R1_SANDBOX_CODE_TARGET_NOT_ALLOWLISTED")
        command.extend(("--ro-bind", str(_safe_source(source)), target))
    output = output_dir.resolve(strict=True)
    if any(fragment in str(output).lower() for fragment in FORBIDDEN_MOUNT_FRAGMENTS):
        raise ValueError("R1_SANDBOX_OUTPUT_TARGET_FORBIDDEN")
    command.extend(("--bind", str(output), "/outputs"))
    for key, value in FIXED_ENVIRONMENT.items():
        command.extend(("--setenv", key, value))
    command.extend(("--chdir", "/app", executable_inside, *arguments))
    lowered = "\n".join(command).lower()
    if any(fragment in lowered for fragment in ("sealed_evaluation_gold", "gold_key", "decryption_key")):
        raise ValueError("R1_SANDBOX_ARGV_GOLD_EXPOSURE")
    return command


def run_sandbox_command(command: Sequence[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    # An empty inherited environment is the compatibility equivalent of
    # bubblewrap --clearenv, which is unavailable in bubblewrap 0.4.0.
    return subprocess.run(list(command), env={}, text=True, capture_output=True,
                          timeout=timeout, check=False)


def environment_exposure_count(environment: Mapping[str, str]) -> int:
    return sum(any(fragment in f"{key}={value}".lower() for fragment in FORBIDDEN_MOUNT_FRAGMENTS)
               for key, value in environment.items())
