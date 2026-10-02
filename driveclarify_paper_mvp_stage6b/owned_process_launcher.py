#!/usr/bin/env python3
"""Durably identify an owned process group, then exec the requested command."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _proc_starttime(pid: int) -> int:
    raw = Path("/proc/{}/stat".format(pid)).read_text(encoding="utf-8")
    right = raw.rfind(")")
    if right < 0:
        raise RuntimeError("PROC_STAT_FORMAT")
    return int(raw[right + 2 :].split()[19])


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp." + str(os.getpid()))
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))
    _fsync_directory(path.parent)


def main() -> int:
    if len(sys.argv) < 5 or sys.argv[3] != "--":
        raise SystemExit(
            "usage: owned_process_launcher.py HANDSHAKE IDENTITY_SHA256 -- COMMAND..."
        )
    handshake = Path(sys.argv[1]).resolve()
    identity_sha256 = sys.argv[2]
    command = sys.argv[4:]
    pid = os.getpid()
    pgid = os.getpgrp()
    if pid <= 1 or pgid != pid:
        raise SystemExit("OWNED_LAUNCHER_REQUIRES_NEW_SESSION_GROUP")
    _atomic_json(
        handshake,
        {
            "schema_version": "driveclarify.formal_execution.child_identity_handshake.v1",
            "status": "PASS_CHILD_IDENTITY_DURABLE_BEFORE_EXEC",
            "attempt_identity_sha256": identity_sha256,
            "pid": pid,
            "pgid": pgid,
            "starttime_ticks": _proc_starttime(pid),
            "command_argv": command,
        },
    )
    os.execvpe(command[0], command, dict(os.environ))
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
