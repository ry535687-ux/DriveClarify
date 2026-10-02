"""Atomic, exclusive publication helpers for native RQ2 process handoffs.

Writers in the native qualification stack communicate through JSON files.  A
consumer must never be able to observe the final pathname before the complete
payload is durable.  Writing directly to an ``O_EXCL`` final path does not
provide that property: the directory entry becomes visible as soon as
``open(2)`` succeeds.

This module writes and fsyncs a private file in the destination directory,
then publishes it with an atomic hard link.  ``link(2)`` also preserves the
write-once/no-overwrite contract because it fails when the final path exists.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any


def write_json_once(path: Path, value: Any) -> None:
    """Publish one complete canonical JSON value without replacing ``path``."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    temporary = path.parent / (
        f".{path.name}.tmp.{os.getpid()}.{time.monotonic_ns()}"
    )
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o644,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
