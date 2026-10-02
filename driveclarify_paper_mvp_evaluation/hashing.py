"""Canonical hashing helpers for the Stage 6A evaluation contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def canonical_bytes(value: Any) -> bytes:
    """Return the sole JSON encoding accepted by the evaluation freeze."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_hash_manifest(paths: Iterable[Path], *, root: Path) -> dict[str, Any]:
    """Hash an explicit source allowlist; directories and missing paths fail closed."""

    records: dict[str, dict[str, Any]] = {}
    for path in sorted(paths, key=lambda item: str(item)):
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"HASH_PATH_OUTSIDE_ROOT:{path}") from exc
        if not resolved.is_file():
            raise ValueError(f"HASH_SOURCE_FILE_REQUIRED:{path}")
        records[str(relative)] = {
            "bytes": resolved.stat().st_size,
            "sha256": file_sha256(resolved),
        }
    return {
        "schema_version": "driveclarify.paper_mvp_source_hashes.v1",
        "files": records,
        "aggregate_sha256": canonical_sha256(records),
    }
