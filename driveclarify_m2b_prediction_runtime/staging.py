"""Append-only first-evidence journal used inside the prediction namespace."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .contracts import canonical_bytes, validate_prediction_envelope, validate_raw_prediction_record


def create_journal_once(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def append_record_fsync(path: Path, record: Mapping[str, Any]) -> None:
    validate_raw_prediction_record(record)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        payload = canonical_bytes(dict(record))
        written = 0
        while written < len(payload):
            written += os.write(fd, payload[written:])
        os.fsync(fd)
    finally:
        os.close(fd)


def read_journal(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.endswith("\n"):
                raise ValueError(f"R2_EVIDENCE_JOURNAL_TORN_LINE:{line_number}")
            row = json.loads(line)
            validate_raw_prediction_record(row)
            rows.append(row)
    order = [(row["canonical_case_index"], row["canonical_comparison_index"]) for row in rows]
    if order != sorted(order) or len(order) != len(set(order)):
        raise ValueError("R2_EVIDENCE_JOURNAL_ORDER_INVALID")
    return rows


def seal_journal_read_only(path: Path) -> None:
    os.chmod(path, 0o400)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def write_envelope_once(path: Path, envelope: Mapping[str, Any]) -> None:
    validate_prediction_envelope(envelope)
    payload = canonical_bytes(dict(envelope))
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
