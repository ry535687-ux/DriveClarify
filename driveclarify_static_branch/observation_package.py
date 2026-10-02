"""Atomic persistence helpers for frozen model-ready observations.

This module is intentionally standard-library-only so the serializer's
persistence and manifest behavior can be tested without importing torch,
CARLA, SimLingo, or creating a CUDA context.  Runtime callers provide the
already-serialized tensor bytes together with exact shape/dtype metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence


class ObservationPackageError(RuntimeError):
    """Fail-closed package persistence error."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def atomic_create_bytes(path: Path, raw: bytes, mode: int = 0o600) -> None:
    """Create one new file durably without following a final symlink."""

    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags, mode)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise ObservationPackageError("OBSERVATION_PACKAGE_SHORT_WRITE:" + str(path))
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if path.stat().st_size != len(raw) or sha256_path(path) != hashlib.sha256(raw).hexdigest():
        raise ObservationPackageError("OBSERVATION_PACKAGE_PERSISTENCE_MISMATCH:" + str(path))


def atomic_create_json(path: Path, value: Any) -> None:
    atomic_create_bytes(path, json_bytes(value))


def atomic_replace_json(path: Path, value: Any) -> None:
    raw = json_bytes(value)
    temporary = path.with_name("." + path.name + ".observation-package.tmp")
    atomic_create_bytes(temporary, raw)
    os.replace(str(temporary), str(path))
    descriptor = os.open(str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validated_relative_path(value: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if (
        not value
        or relative.is_absolute()
        or ".." in relative.parts
        or "." in relative.parts
        or str(relative) != value
    ):
        raise ObservationPackageError("INVALID_OBSERVATION_PACKAGE_RELATIVE_PATH:" + value)
    return relative


class ObservationPackageWriter:
    """Write a frozen package and a content-addressed root manifest."""

    def __init__(
        self,
        *,
        package_root: Path,
        manifest_path: Path,
        run_id: str,
        unit_id: str,
        observation_hash: str,
        source_frame: int,
        source_identity: Mapping[str, Any],
        eligibility_contract_sha256: str,
        topology_sha256: str,
    ) -> None:
        if package_root.exists():
            raise ObservationPackageError("OBSERVATION_PACKAGE_ROOT_ALREADY_EXISTS")
        if manifest_path.exists():
            raise ObservationPackageError("OBSERVATION_PACKAGE_MANIFEST_ALREADY_EXISTS")
        package_root.mkdir(parents=False)
        self.package_root = package_root
        self.manifest_path = manifest_path
        self.run_id = run_id
        self.unit_id = unit_id
        self.observation_hash = observation_hash
        self.source_frame = int(source_frame)
        self.source_identity = dict(source_identity)
        self.eligibility_contract_sha256 = eligibility_contract_sha256
        self.topology_sha256 = topology_sha256
        self._rows: List[Dict[str, Any]] = []
        self._paths = set()
        self._finalized = False

    def add_bytes(
        self,
        relative_path: str,
        raw: bytes,
        *,
        semantic_role: str,
        shape: Optional[Sequence[int]] = None,
        dtype: Optional[str] = None,
        frame: Optional[str] = None,
        serialization: Optional[str] = None,
    ) -> Mapping[str, Any]:
        if self._finalized:
            raise ObservationPackageError("OBSERVATION_PACKAGE_ALREADY_FINALIZED")
        relative = _validated_relative_path(relative_path)
        if relative_path in self._paths:
            raise ObservationPackageError("DUPLICATE_OBSERVATION_PACKAGE_PATH:" + relative_path)
        if not semantic_role:
            raise ObservationPackageError("OBSERVATION_PACKAGE_SEMANTIC_ROLE_REQUIRED")
        target = self.package_root.joinpath(*relative.parts)
        atomic_create_bytes(target, raw)
        row: Dict[str, Any] = {
            "relative_path": relative_path,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "shape": list(shape) if shape is not None else None,
            "dtype": dtype,
            "semantic_role": semantic_role,
        }
        if frame is not None:
            row["frame"] = frame
        if serialization is not None:
            row["serialization"] = serialization
        self._rows.append(row)
        self._paths.add(relative_path)
        return row

    def add_json(
        self,
        relative_path: str,
        value: Any,
        *,
        semantic_role: str,
        shape: Optional[Sequence[int]] = None,
        dtype: str = "JSON",
        frame: Optional[str] = None,
    ) -> Mapping[str, Any]:
        return self.add_bytes(
            relative_path,
            json_bytes(value),
            semantic_role=semantic_role,
            shape=shape,
            dtype=dtype,
            frame=frame,
            serialization="UTF8_JSON",
        )

    def finalize(self, *, additional_metadata: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        if self._finalized:
            raise ObservationPackageError("OBSERVATION_PACKAGE_ALREADY_FINALIZED")
        if not self._rows:
            raise ObservationPackageError("OBSERVATION_PACKAGE_EMPTY")
        rows = sorted(self._rows, key=lambda item: item["relative_path"])
        manifest: Dict[str, Any] = {
            "schema_version": "driveclarify.observation_package_manifest.v1",
            "status": "COMPLETE_FROZEN_MODEL_READY_OBSERVATION",
            "run_id": self.run_id,
            "unit_id": self.unit_id,
            "package_directory": str(self.package_root),
            "observation_hash": self.observation_hash,
            "source_frame": self.source_frame,
            "source_identity": self.source_identity,
            "eligibility_contract_sha256": self.eligibility_contract_sha256,
            "topology_sha256": self.topology_sha256,
            "file_count": len(rows),
            "files": rows,
            "candidate_forward_count": 0,
            "second_observation_count": 0,
        }
        if additional_metadata:
            manifest["metadata"] = dict(additional_metadata)
        manifest["package_content_sha256"] = digest_value(rows)
        unsigned = dict(manifest)
        manifest["sha256"] = digest_value(unsigned)
        atomic_create_json(self.manifest_path, manifest)
        self._finalized = True
        return manifest


def unavailable_manifest(
    *,
    manifest_path: Path,
    run_id: str,
    unit_id: str,
    status: str,
    reason_codes: Sequence[str],
    eligibility_contract_sha256: str,
    topology_sha256: str,
) -> Mapping[str, Any]:
    value: Dict[str, Any] = {
        "schema_version": "driveclarify.observation_package_manifest.v1",
        "status": status,
        "run_id": run_id,
        "unit_id": unit_id,
        "package_directory": None,
        "observation_hash": None,
        "source_frame": None,
        "eligibility_contract_sha256": eligibility_contract_sha256,
        "topology_sha256": topology_sha256,
        "file_count": 0,
        "files": [],
        "reason_codes": list(reason_codes),
        "candidate_forward_count": 0,
        "second_observation_count": 0,
    }
    value["package_content_sha256"] = digest_value([])
    value["sha256"] = digest_value(value)
    atomic_create_json(manifest_path, value)
    return value
