#!/usr/bin/env python3
"""Run one command and append an execution receipt to the Stage 6 command log."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = REPO_ROOT / "COMMAND_LOG.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_receipt(path: Path) -> str:
    if not path.exists():
        return f"MISSING `{path}`"
    if path.is_file():
        return f"FILE `{path}` bytes={path.stat().st_size} sha256={_sha256_file(path)}`"
    if not path.is_dir():
        return f"OTHER `{path}`"

    rows = []
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        rows.append(f"{child.relative_to(path).as_posix()}\0{child.stat().st_size}\0{_sha256_file(child)}\n")
    payload = "".join(rows).encode("utf-8")
    return f"DIR `{path}` files={len(rows)} tree_sha256={_sha256_bytes(payload)}`"


def _format_command(command: Iterable[str]) -> str:
    return shlex.join(list(command))


def _append_receipt(
    log_path: Path,
    *,
    started_at: str,
    finished_at: str,
    cwd: Path,
    command: list[str],
    returncode: int,
    stdout: bytes,
    stderr: bytes,
    artifacts: list[Path],
) -> None:
    lines = [
        f"## {started_at} — `{_format_command(command)}`",
        "",
        f"- 结束时间：`{finished_at}`",
        f"- 工作目录：`{cwd}`",
        f"- 退出码：`{returncode}`",
        f"- stdout：`{len(stdout)} bytes / sha256={_sha256_bytes(stdout)}`",
        f"- stderr：`{len(stderr)} bytes / sha256={_sha256_bytes(stderr)}`",
    ]
    if artifacts:
        lines.extend(["- 工件：", ""])
        lines.extend(f"  - {_artifact_receipt(path)}" for path in artifacts)
    lines.append("")
    with log_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--cwd", type=Path, default=REPO_ROOT)
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    return args


def main() -> int:
    args = _parse_args()
    cwd = args.cwd.resolve()
    log_path = args.log.resolve()
    artifacts = [path if path.is_absolute() else cwd / path for path in args.artifact]
    started_at = _utc_now()
    try:
        completed = subprocess.run(args.command, cwd=cwd, capture_output=True, check=False)
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except OSError as error:
        returncode = 127
        stdout = b""
        stderr = f"{type(error).__name__}: {error}\n".encode("utf-8", errors="replace")
    finished_at = _utc_now()
    _append_receipt(
        log_path,
        started_at=started_at,
        finished_at=finished_at,
        cwd=cwd,
        command=args.command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        artifacts=artifacts,
    )
    sys.stdout.buffer.write(stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(stderr)
    sys.stderr.buffer.flush()
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
