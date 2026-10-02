"""Canonical serialization and strict blind-package contracts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping


RUNTIME_FORBIDDEN_FRAGMENTS = (
    "gold",
    "latent_true",
    "expected_decision",
    "expected_action",
    "oracle",
    "wrong_goal",
    "regret",
    "baseline_answer",
    "evaluation_split",
)

PREDICTION_REQUIRED_FIELDS = frozenset(
    {
        "case_id", "track", "comparison_id", "selected_action",
        "selected_candidate_id", "act_subtype", "decision_reason_codes",
        "legal_action_mask", "r_act_a", "r_act_b", "r_ask", "r_wait",
        "v_ask", "v_wait", "posterior_summary", "query_episode_state",
        "matrix_sha256", "profile_sha256", "protocol_sha256",
        "authorization_eligible", "used_for_control", "control_authorized",
        "override_applied",
    }
)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_hash(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_once(path: Path, payload: bytes) -> None:
    """Publish immutable evidence with exclusive creation and directory fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def iter_key_paths(value: Any, prefix: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            yield path, str(key)
            yield from iter_key_paths(item, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_key_paths(item, f"{prefix}[{index}]")


def runtime_leak_paths(value: Any) -> list[str]:
    return sorted(
        path for path, key in iter_key_paths(value)
        if any(fragment in key.lower() for fragment in RUNTIME_FORBIDDEN_FRAGMENTS)
    )


def validate_runtime_package(package: Mapping[str, Any]) -> None:
    leaks = runtime_leak_paths(package)
    if leaks:
        raise ValueError("BLIND_RUNTIME_GOLD_FIELD_LEAK:" + ",".join(leaks[:10]))
    cases = package.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("BLIND_RUNTIME_CASES_MISSING")
    ids = [case.get("case_id") for case in cases if isinstance(case, Mapping)]
    if len(ids) != len(cases) or len(set(ids)) != len(ids):
        raise ValueError("BLIND_RUNTIME_CASE_IDS_INVALID")
    if ids != sorted(ids):
        raise ValueError("BLIND_RUNTIME_CANONICAL_ORDER_INVALID")


def validate_prediction_record(record: Mapping[str, Any]) -> None:
    missing = PREDICTION_REQUIRED_FIELDS.difference(record)
    if missing:
        raise ValueError("PREDICTION_REQUIRED_FIELDS_MISSING:" + ",".join(sorted(missing)))
    if record["authorization_eligible"] or record["used_for_control"] or record["control_authorized"]:
        raise ValueError("PREDICTION_CONTROL_AUTHORIZATION_FORBIDDEN")
    if record["override_applied"]:
        raise ValueError("LEARNED_OVERRIDE_FORBIDDEN")
    if runtime_leak_paths(record):
        raise ValueError("PREDICTION_GOLD_FIELD_FORBIDDEN")

