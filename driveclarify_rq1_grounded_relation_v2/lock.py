"""预测落盘锁：评分前固定预测字节身份。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


class PredictionLockError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    if path.exists():
        raise PredictionLockError("PREDICTION_PATH_ALREADY_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temporary.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
            count += 1
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path), "row_count": count}


def lock_predictions(path: Path, receipt_path: Path) -> dict[str, Any]:
    if receipt_path.exists():
        raise PredictionLockError("LOCK_RECEIPT_ALREADY_EXISTS")
    receipt = {"schema_version": "driveclarify.rq1_prediction_lock.v1", **write_identity(path), "labels_read_before_lock": False}
    temporary = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, receipt_path)
    return receipt


def write_identity(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PredictionLockError("PREDICTIONS_MISSING")
    return {"prediction_path": str(path), "prediction_bytes": path.stat().st_size, "prediction_sha256": sha256_file(path)}


def verify_lock(path: Path, receipt_path: Path) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    current = write_identity(path)
    if any(receipt.get(key) != value for key, value in current.items()):
        raise PredictionLockError("PREDICTION_LOCK_MISMATCH")
    return receipt
