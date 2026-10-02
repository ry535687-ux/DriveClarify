"""Execution-only atomic candidate-evidence transaction hook.

The official provider is called exactly once. A successful return is committed
as one self-contained, non-overwritable record before downstream classification.
JSONL is only a rebuildable derivative index.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import itertools
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping

CAPTURE_ROOT_ENV = "DRIVECLARIFY_R43_CAPTURE_ROOT"
ATTEMPT_ENV = "DRIVECLARIFY_R43_DISCOVERY_ATTEMPT_ID"
FAMILY_ENV = "DRIVECLARIFY_R43_FAMILY_ID"
PROTOCOL_ENV = "DRIVECLARIFY_R43_PROTOCOL_VERSION"
INSTRUCTION_IDENTITY_ENV = "DRIVECLARIFY_R43_INSTRUCTION_IDENTITY"
MODEL_IDENTITY_ENV = "DRIVECLARIFY_R43_MODEL_IDENTITY"
CHECKPOINT_IDENTITY_ENV = "DRIVECLARIFY_R43_CHECKPOINT_IDENTITY"
SCHEMA_VERSION = "driveclarify.r4_3.candidate_evidence_record.v1"
COMMIT_CONTRACT_VERSION = "R4.3-CANDIDATE-EVIDENCE-TRANSACTION-V1"
INDEX_FILENAME = "CANDIDATE_FORWARD_EVIDENCE.jsonl"
RECORDS_DIRECTORY = "candidate_records"
RECORD_FILENAME = "RECORD.json"
ROUTE_FILENAME = "pred_route.npy"
SPEED_FILENAME = "pred_speed_wps.npy"
_ORDINALS: dict[str, itertools.count] = {}
_ORDINAL_LOCK = threading.Lock()


class CandidateEvidenceTransactionError(RuntimeError):
    """Fail-closed transaction or validation error."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _raw_array(value: Any):
    import numpy as np
    current = value
    if hasattr(current, "detach"):
        current = current.detach()
    if hasattr(current, "cpu"):
        current = current.cpu()
    if hasattr(current, "numpy"):
        current = current.numpy()
    array = np.asarray(current)
    if array.dtype.hasobject:
        raise CandidateEvidenceTransactionError("OBJECT_ARRAY_FORBIDDEN")
    return array


def _write_npy(path: Path, value: Any) -> Mapping[str, Any]:
    import numpy as np
    array = _raw_array(value)
    with path.open("xb") as handle:
        np.save(handle, array, allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())
    digest = _sha256(path)
    loaded = np.load(path, allow_pickle=False)
    if loaded.dtype != array.dtype or tuple(loaded.shape) != tuple(array.shape):
        raise CandidateEvidenceTransactionError("ARRAY_ROUNDTRIP_IDENTITY_MISMATCH")
    return {"relative_path": path.name, "sha256": digest, "dtype": str(array.dtype), "shape": [int(v) for v in array.shape]}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("xb") as handle:
        handle.write(_canonical_json(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Linux atomic rename with RENAME_NOREPLACE; never replace a final record."""
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    if renameat2 is None:
        raise CandidateEvidenceTransactionError("RENAME_NOREPLACE_UNAVAILABLE")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in (errno.EEXIST, errno.ENOTEMPTY):
        raise CandidateEvidenceTransactionError("RECORD_ID_COLLISION")
    raise OSError(error, os.strerror(error), str(destination))


def next_forward_ordinal(attempt_id: str) -> int:
    with _ORDINAL_LOCK:
        return next(_ORDINALS.setdefault(str(attempt_id), itertools.count(1)))


def record_identity_tuple(*, attempt_id: str, source_observation_id: str, source_frame_id: Any,
                          candidate_forward_ordinal: int, candidate_slot: str,
                          candidate_semantic_id: str) -> Mapping[str, Any]:
    return {
        "attempt_id": str(attempt_id),
        "source_observation_id": str(source_observation_id),
        "source_frame_id": _json_scalar(source_frame_id),
        "candidate_forward_ordinal": int(candidate_forward_ordinal),
        "candidate_slot": str(candidate_slot),
        "candidate_semantic_id": str(candidate_semantic_id),
    }


def record_id_for(identity: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def _fault(name: str | None, expected: str) -> None:
    if name == expected:
        raise CandidateEvidenceTransactionError("FAULT_INJECTION_" + expected.upper())


def commit_candidate_evidence(capture_root: Path | str, *, identity: Mapping[str, Any],
                              route_value: Any, speed_value: Any,
                              record_fields: Mapping[str, Any],
                              fault_at: str | None = None) -> Mapping[str, Any]:
    """Commit one canonical record or leave only recognizable .tmp evidence."""
    root = Path(capture_root).resolve()
    records = root / RECORDS_DIRECTORY
    records.mkdir(parents=True, exist_ok=True)
    _fsync_directory(root)
    record_id = record_id_for(identity)
    final = records / record_id
    temporary = Path(tempfile.mkdtemp(prefix=f".tmp-{record_id}-", dir=str(records)))
    try:
        route = _write_npy(temporary / ROUTE_FILENAME, route_value)
        _fault(fault_at, "after_route")
        speed = _write_npy(temporary / SPEED_FILENAME, speed_value)
        _fault(fault_at, "after_speed")
        if _sha256(temporary / ROUTE_FILENAME) != route["sha256"] or _sha256(temporary / SPEED_FILENAME) != speed["sha256"]:
            raise CandidateEvidenceTransactionError("PRECOMMIT_HASH_VERIFICATION_FAILED")
        _fault(fault_at, "before_record")
        record = dict(record_fields)
        record.update(identity)
        record.update({
            "schema_version": SCHEMA_VERSION,
            "record_id": record_id,
            "commit_contract_version": COMMIT_CONTRACT_VERSION,
            "route_artifact": route,
            "speed_artifact": speed,
            "canonical_record_relative_path": f"{RECORDS_DIRECTORY}/{record_id}",
            "evidence_claim": "CANDIDATE_FORWARD_OUTPUT_DURABLY_CAPTURED_ONLY",
        })
        _write_json(temporary / RECORD_FILENAME, record)
        _fsync_directory(temporary)
        _fault(fault_at, "before_rename")
        lock_path = records / ".transaction.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                _rename_noreplace(temporary, final)
                _fsync_directory(records)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return validate_committed_record(final)
    except Exception:
        # Retain .tmp as INCOMPLETE_NOT_EVIDENCE for forensic diagnosis.
        raise


def validate_committed_record(directory: Path | str) -> Mapping[str, Any]:
    directory = Path(directory)
    if directory.name.startswith(".tmp-") or not directory.is_dir():
        raise CandidateEvidenceTransactionError("NOT_COMMITTED_EVIDENCE")
    path = directory / RECORD_FILENAME
    if not path.is_file():
        raise CandidateEvidenceTransactionError("RECORD_JSON_MISSING")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise CandidateEvidenceTransactionError("RECORD_JSON_INVALID") from error
    required = {
        "schema_version", "record_id", "commit_contract_version", "attempt_id", "family_id",
        "protocol_version", "source_observation_id", "source_frame_id", "world_identity",
        "map_identity", "route_identity", "instruction_identity", "candidate_forward_ordinal",
        "candidate_slot", "candidate_semantic_id", "physical_identity", "target_digest",
        "branch_digest", "obligation_digest", "model_identity", "checkpoint_identity",
        "route_artifact", "speed_artifact", "coordinate_frame_declaration",
        "capture_timestamp", "clock_domain", "capture_hook_source_location",
    }
    if not required.issubset(record):
        raise CandidateEvidenceTransactionError("RECORD_SCHEMA_FIELDS_MISSING")
    if record["schema_version"] != SCHEMA_VERSION or record["commit_contract_version"] != COMMIT_CONTRACT_VERSION:
        raise CandidateEvidenceTransactionError("RECORD_SCHEMA_VERSION_MISMATCH")
    if record["record_id"] != directory.name:
        raise CandidateEvidenceTransactionError("RECORD_DIRECTORY_ID_MISMATCH")
    identity = record_identity_tuple(
        attempt_id=record["attempt_id"], source_observation_id=record["source_observation_id"],
        source_frame_id=record["source_frame_id"], candidate_forward_ordinal=record["candidate_forward_ordinal"],
        candidate_slot=record["candidate_slot"], candidate_semantic_id=record["candidate_semantic_id"],
    )
    if record_id_for(identity) != record["record_id"]:
        raise CandidateEvidenceTransactionError("RECORD_IDENTITY_DIGEST_MISMATCH")
    for key in ("route_artifact", "speed_artifact"):
        artifact = record[key]
        artifact_path = directory / artifact["relative_path"]
        if not artifact_path.is_file() or _sha256(artifact_path) != artifact["sha256"]:
            raise CandidateEvidenceTransactionError("ARTIFACT_HASH_MISMATCH:" + key)
        import numpy as np
        array = np.load(artifact_path, allow_pickle=False)
        if str(array.dtype) != artifact["dtype"] or [int(v) for v in array.shape] != artifact["shape"]:
            raise CandidateEvidenceTransactionError("ARTIFACT_DTYPE_SHAPE_MISMATCH:" + key)
    forbidden = {"SHARED", "DISTINCT", "ASK-qualified", "WAIT-qualified", "FALLBACK-qualified"}
    if any(value in forbidden for value in record.values() if isinstance(value, str)):
        raise CandidateEvidenceTransactionError("FORBIDDEN_QUALIFICATION_CLAIM")
    return record


def rebuild_candidate_index(capture_root: Path | str, *, fault_at: str | None = None) -> int:
    """Rebuild derivative JSONL solely from hash-valid committed records."""
    root = Path(capture_root).resolve()
    records = root / RECORDS_DIRECTORY
    records.mkdir(parents=True, exist_ok=True)
    committed = [p for p in sorted(records.iterdir(), key=lambda p: p.name)
                 if p.is_dir() and not p.name.startswith(".tmp-")]
    rows = []
    for directory in committed:
        record = dict(validate_committed_record(directory))
        record["index_role"] = "DERIVATIVE_REBUILDABLE_INDEX"
        rows.append(record)
    descriptor, name = tempfile.mkstemp(prefix=".tmp-candidate-index-", suffix=".jsonl", dir=str(root))
    temporary = Path(name)
    with os.fdopen(descriptor, "wb") as handle:
        for row in rows:
            handle.write(_canonical_json(row) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    _fault(fault_at, "index_write")
    os.replace(temporary, root / INDEX_FILENAME)
    _fsync_directory(root)
    return len(rows)


def _identity_from_runtime(episode: Any, candidate: Any, ordinal: int) -> Mapping[str, Any]:
    vision = episode.vision_observation
    semantic = str(candidate.candidate_id)
    return record_identity_tuple(
        attempt_id=os.environ[ATTEMPT_ENV], source_observation_id=str(vision.observation_id),
        source_frame_id=getattr(vision, "frame_id", None), candidate_forward_ordinal=ordinal,
        candidate_slot=semantic, candidate_semantic_id=semantic,
    )


def _record_fields(episode: Any, candidate: Any, result: Any) -> Mapping[str, Any]:
    evidence = dict(getattr(result, "forward_evidence", {}) or {})
    instruction = getattr(episode, "raw_instruction", None)
    instruction_identity = os.environ.get(INSTRUCTION_IDENTITY_ENV)
    if instruction_identity is None and instruction is not None:
        instruction_identity = hashlib.sha256(str(instruction).encode("utf-8")).hexdigest()
    return {
        "family_id": os.environ.get(FAMILY_ENV, "UNKNOWN_NOT_EXPOSED"),
        "protocol_version": os.environ.get(PROTOCOL_ENV, "UNKNOWN_NOT_EXPOSED"),
        "world_identity": os.environ.get("DRIVECLARIFY_R43_WORLD_IDENTITY"),
        "map_identity": os.environ.get("DRIVECLARIFY_R43_MAP_IDENTITY"),
        "route_identity": os.environ.get("DRIVECLARIFY_R43_ROUTE_IDENTITY"),
        "instruction_identity": instruction_identity,
        "instruction_identity_reason": "EXPLICIT_OR_RAW_INSTRUCTION_SHA256" if instruction_identity else "NOT_EXPOSED_AT_FORWARD_BOUNDARY",
        "physical_identity": {
            "actor_id": _json_scalar(getattr(candidate, "actor_id", None)),
            "track_id": _json_scalar(getattr(candidate, "track_id", None)),
            "map_landmark_id": _json_scalar(getattr(candidate, "map_landmark_id", None)),
            "topology_target_id": _json_scalar(getattr(candidate, "topology_target_id", None)),
            "visual_track_id": _json_scalar(getattr(candidate, "visual_track_id", None)),
        },
        "target_digest": _json_scalar(getattr(candidate, "target_digest", None)),
        "branch_digest": _json_scalar(getattr(candidate, "branch_digest", None)),
        "obligation_digest": _json_scalar(getattr(candidate, "obligation_digest", None)),
        "candidate_semantic_digest": _json_scalar(getattr(candidate, "candidate_semantic_digest", None)),
        "model_identity": evidence.get("model_identity") or os.environ.get(MODEL_IDENTITY_ENV, "UNKNOWN_NOT_EXPOSED"),
        "checkpoint_identity": evidence.get("checkpoint_identity") or os.environ.get(CHECKPOINT_IDENTITY_ENV, "UNKNOWN_NOT_EXPOSED"),
        "coordinate_frame_declaration": {
            "route": "RAW_OFFICIAL_PROVIDER_ROUTE_FRAME_UNMODIFIED",
            "speed": "RAW_OFFICIAL_PROVIDER_SPEED_WAYPOINT_FRAME_UNMODIFIED",
            "units": "AS_RETURNED_BY_OFFICIAL_PROVIDER_NO_CONVENIENCE_CAST",
        },
        "capture_timestamp": time.time_ns(),
        "clock_domain": "CLOCK_REALTIME_UTC_UNIX_NS",
        "capture_hook_source_location": f"{Path(__file__).resolve()}:wrapped:after_original_return_before_scheduler",
        "model_forward_count_for_record": 1,
    }


def capture_forward_result(episode: Any, candidate: Any, result: Any, *,
                           candidate_forward_ordinal: int | None = None,
                           fault_at: str | None = None) -> Mapping[str, Any]:
    root = Path(os.environ[CAPTURE_ROOT_ENV]).resolve()
    attempt = os.environ[ATTEMPT_ENV]
    ordinal = next_forward_ordinal(attempt) if candidate_forward_ordinal is None else int(candidate_forward_ordinal)
    identity = _identity_from_runtime(episode, candidate, ordinal)
    record = commit_candidate_evidence(
        root, identity=identity,
        route_value=getattr(result, "raw_route", None),
        speed_value=getattr(result, "raw_speed", None),
        record_fields=_record_fields(episode, candidate, result), fault_at=fault_at,
    )
    try:
        rebuild_candidate_index(root)
    except Exception:
        # Canonical commit survives a derivative-index failure.
        pass
    return record


def _install() -> None:
    if not os.environ.get(CAPTURE_ROOT_ENV) or not os.environ.get(ATTEMPT_ENV):
        return
    from driveclarify_official_dreaming_adapter.adapter import OfficialDreamingCandidateForwardProvider
    original = OfficialDreamingCandidateForwardProvider.__call__
    if getattr(original, "_driveclarify_r43_capture", False):
        return

    def wrapped(self: Any, episode: Any, candidate: Any):
        result = original(self, episode, candidate)
        capture_forward_result(episode, candidate, result)
        return result

    wrapped._driveclarify_r43_capture = True  # type: ignore[attr-defined]
    wrapped._driveclarify_r43_original = original  # type: ignore[attr-defined]
    OfficialDreamingCandidateForwardProvider.__call__ = wrapped


_install()
