"""Observational metadata recorder for values supplied by the official runtime."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _is_safe_metadata(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, tuple):
        return all(_is_safe_metadata(item) for item in value)
    return False


@dataclass(frozen=True)
class ControlSnapshot:
    steer: float
    throttle: float
    brake: float

    def __post_init__(self) -> None:
        if not all(
                type(value) in (int, float) and math.isfinite(float(value))
                for value in (self.steer, self.throttle, self.brake)):
            raise ValueError("CONTROL_SNAPSHOT_INVALID")


@dataclass(frozen=True)
class OfficialRuntimeSample:
    frame_id: int
    simulation_timestamp_s: float
    input_hashes: Tuple[Tuple[str, str], ...]
    ego_xyz: Tuple[float, float, float]
    ego_yaw_deg: float
    speed_mps: float
    control: ControlSnapshot
    transaction_id: Optional[str]
    branch_generation: Optional[int]
    anchor_identity: Optional[str]
    planner_hashes: Tuple[str, str]
    safety_metadata: Tuple[Tuple[str, Any], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.frame_id, int) or self.frame_id < 0:
            raise ValueError("FRAME_ID_INVALID")
        if not isinstance(self.ego_xyz, tuple) or len(self.ego_xyz) != 3:
            raise ValueError("EGO_XYZ_MUST_BE_IMMUTABLE_TUPLE")
        numeric = self.ego_xyz + (
            self.simulation_timestamp_s, self.ego_yaw_deg, self.speed_mps,
        )
        if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in numeric):
            raise ValueError("RUNTIME_SAMPLE_NUMERIC_VALUE_INVALID")
        if not isinstance(self.control, ControlSnapshot):
            raise ValueError("CONTROL_MUST_BE_IMMUTABLE_SNAPSHOT")
        if not isinstance(self.input_hashes, tuple) or any(
                not isinstance(row, tuple) or len(row) != 2
                for row in self.input_hashes):
            raise ValueError("INPUT_HASHES_MUST_BE_IMMUTABLE_TUPLES")
        if not isinstance(self.planner_hashes, tuple):
            raise ValueError("PLANNER_HASHES_MUST_BE_IMMUTABLE_TUPLE")
        if not isinstance(self.safety_metadata, tuple) or any(
                not isinstance(row, tuple) or len(row) != 2
                for row in self.safety_metadata):
            raise ValueError("SAFETY_METADATA_MUST_BE_IMMUTABLE_TUPLES")
        for label, digest in self.input_hashes:
            if not isinstance(label, str) or _SHA256_RE.fullmatch(digest) is None:
                raise ValueError("INPUT_HASH_INVALID")
        if len(self.planner_hashes) != 2 or any(
                _SHA256_RE.fullmatch(value) is None for value in self.planner_hashes):
            raise ValueError("PLANNER_HASH_INVALID")
        for optional_hash in (self.transaction_id, self.anchor_identity):
            if optional_hash is not None and _SHA256_RE.fullmatch(optional_hash) is None:
                raise ValueError("PROVENANCE_HASH_INVALID")
        if self.branch_generation is not None and (
                not isinstance(self.branch_generation, int) or self.branch_generation < 1):
            raise ValueError("BRANCH_GENERATION_INVALID")
        if any(
                not isinstance(label, str) or not _is_safe_metadata(value)
                for label, value in self.safety_metadata):
            raise ValueError("SAFETY_METADATA_MUST_BE_HANDLE_FREE_SCALARS")


class ThinRecorder(object):
    """Copy immutable scalar/hash snapshots; never receive runtime handles."""

    def __init__(self) -> None:
        self._records = []  # type: list
        self._lock = threading.RLock()

    def record(self, sample: OfficialRuntimeSample) -> Dict[str, Any]:
        if not isinstance(sample, OfficialRuntimeSample):
            raise TypeError("OFFICIAL_RUNTIME_SAMPLE_REQUIRED")
        payload = asdict(sample)
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        receipt = {
            "schema": "driveclarify.v3.thin-runtime-record.v1",
            "frame_id": sample.frame_id,
            "sample_sha256": hashlib.sha256(encoded).hexdigest(),
            "payload": payload,
        }
        with self._lock:
            self._records.append(receipt)
        return dict(receipt)

    def records(self) -> Tuple[Dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(record) for record in self._records)
