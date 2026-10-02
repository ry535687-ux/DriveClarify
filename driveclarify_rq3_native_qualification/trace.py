"""Machine-readable trace helpers for native engineering qualification."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _finite_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def jsonable(value: Any) -> Any:
    """Convert common sensor/tensor values without changing the source object."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return _finite_float(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        try:
            return jsonable(value.tolist())
        except Exception:
            pass
    if hasattr(value, "numpy"):
        value = value.numpy()
    scalar = _finite_float(value)
    return scalar if scalar is not None else repr(value)


def array_signature(value: Any, include_values: bool = False) -> Dict[str, Any]:
    """Hash the exact CPU byte representation of an array/tensor-like value."""

    source_type = type(value).__name__
    if hasattr(value, "detach"):
        tensor = value.detach().contiguous().cpu()
        try:
            raw_array = tensor.view(getattr(__import__("torch"), "uint8")).reshape(-1).numpy()
            result = {
                "source_type": source_type,
                "shape": [int(item) for item in tensor.shape],
                "dtype": str(tensor.dtype).replace("torch.", ""),
                "byte_count": int(raw_array.nbytes),
                "sha256": hashlib.sha256(raw_array.tobytes()).hexdigest(),
            }
            if include_values:
                result["values"] = jsonable(tensor)
            return result
        except Exception:
            value = tensor
    if hasattr(value, "numpy"):
        value = value.numpy()
    try:
        import numpy as np

        array = np.ascontiguousarray(value)
        raw = memoryview(array).cast("B")
        result: Dict[str, Any] = {
            "source_type": source_type,
            "shape": [int(item) for item in array.shape],
            "dtype": str(array.dtype),
            "byte_count": int(array.nbytes),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if include_values:
            result["values"] = jsonable(array)
        return result
    except Exception as error:  # observer remains fail-open
        fallback = jsonable(value)
        return {
            "source_type": source_type,
            "shape": None,
            "dtype": None,
            "byte_count": None,
            "sha256": canonical_sha256(fallback),
            "values": fallback if include_values else None,
            "fallback_reason": type(error).__name__,
        }


def sensor_signatures(input_data: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for sensor_id, packet in sorted(input_data.items()):
        frame = None
        value = packet
        if isinstance(packet, (tuple, list)) and len(packet) == 2:
            frame, value = packet
        timestamp = getattr(value, "timestamp", None)
        if isinstance(value, dict):
            signature = {
                "source_type": type(value).__name__,
                "sha256": canonical_sha256(jsonable(value)),
                "values": jsonable(value),
            }
        else:
            signature = array_signature(
                value, include_values=not str(sensor_id).startswith("rgb")
            )
        signature.update(
            {
                "sensor_id": str(sensor_id),
                "frame_id": None if frame is None else int(frame),
                "sensor_timestamp_s": _finite_float(timestamp),
                "is_image": str(sensor_id).startswith("rgb"),
            }
        )
        result[str(sensor_id)] = signature
    return result


class JsonlTraceWriter:
    """Append-only writer with bounded fsync cadence and per-row digests."""

    def __init__(self, path: Path, fsync_interval: int = 20):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("ab")
        self._sequence = 0
        self._fsync_interval = max(1, int(fsync_interval))

    @property
    def sequence(self) -> int:
        return self._sequence

    def append(self, row: Dict[str, Any]) -> Dict[str, Any]:
        value = dict(row)
        value["trace_sequence"] = self._sequence
        value["record_digest"] = canonical_sha256(value)
        self._stream.write((canonical_json(value) + "\n").encode("utf-8"))
        self._stream.flush()
        self._sequence += 1
        if self._sequence % self._fsync_interval == 0:
            os.fsync(self._stream.fileno())
        return value

    def close(self) -> None:
        if self._stream.closed:
            return
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if raw.strip():
                value = json.loads(raw)
                expected = value.pop("record_digest", None)
                if expected != canonical_sha256(value):
                    raise ValueError("TRACE_DIGEST_MISMATCH_LINE_%d" % line_number)
                value["record_digest"] = expected
                yield value


__all__ = [
    "JsonlTraceWriter",
    "array_signature",
    "canonical_json",
    "canonical_sha256",
    "jsonable",
    "read_jsonl",
    "sensor_signatures",
]
