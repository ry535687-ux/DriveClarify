"""Standard-library-only lifecycle contract for one-shot run results.

Non-terminal orchestration state, the inner Stage-A runtime envelope, and the
formal terminal result intentionally use different filenames.  Formal terminal
publication uses a fully-fsynced same-directory temporary file followed by an
exclusive hard-link publish, so concurrent writers can never overwrite an
existing result or expose a partially written JSON document.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .observation_package import atomic_create_bytes, json_bytes, sha256_path


PRELAUNCH_STATE_FILE = "PRELAUNCH_STATE.json"
STAGE_A_RUNTIME_RESULT_FILE = "STAGE_A_RUNTIME_RESULT.json"
TERMINAL_RESULT_FILE = "RUN_RESULT.json"
LEGACY_PRELAUNCH_RESULT_FILE = "PRELAUNCH_FAILURE_RUN_RESULT.json"

NONTERMINAL_STATES = frozenset(("PRELAUNCH", "RESERVED", "IN_PROGRESS"))
TERMINAL_CATEGORIES = frozenset(
    ("TERMINAL_SUCCESS", "TERMINAL_EXCLUSION", "TERMINAL_BLOCKED")
)


class RunResultLifecycleError(RuntimeError):
    """Fail-closed lifecycle contract violation with a stable reason code."""

    def __init__(self, reason_code: str, path: Optional[Path] = None) -> None:
        self.reason_code = reason_code
        self.path = path
        message = reason_code if path is None else reason_code + ":" + str(path)
        super().__init__(message)


class DuplicateTerminalResultError(RunResultLifecycleError):
    """A formal terminal result already exists and must not be overwritten."""


def normalize_signed_station(waypoint_s: float, decision: Mapping[str, Any]) -> float:
    """Normalize either lane direction to negative predecision station."""

    direction = decision.get("station_direction_sign", 1)
    if direction not in (-1, 1):
        raise RunResultLifecycleError("STATION_DIRECTION_SIGN_INVALID")
    return float(direction) * (float(waypoint_s) - float(decision["incoming_s_m"]))


def _load_json_object(path: Path, reason_code: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunResultLifecycleError(reason_code, path) from exc
    if not isinstance(value, dict):
        raise RunResultLifecycleError(reason_code, path)
    return value


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(
        str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _exclusive_publish_json(path: Path, value: Mapping[str, Any]) -> None:
    """Publish complete JSON exactly once without a check-then-write race."""

    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json_bytes(value)
    nonce = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    temporary = path.with_name("." + path.name + "." + nonce + ".tmp")
    try:
        atomic_create_bytes(temporary, raw)
        try:
            os.link(str(temporary), str(path), follow_symlinks=False)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise DuplicateTerminalResultError(
                    "DUPLICATE_TERMINAL_RESULT", path
                ) from exc
            raise RunResultLifecycleError("TERMINAL_RESULT_ATOMIC_PUBLISH_FAILED", path) from exc
        _fsync_directory(path.parent)
        if path.stat().st_size != len(raw) or sha256_path(path) != hashlib.sha256(raw).hexdigest():
            raise RunResultLifecycleError("TERMINAL_RESULT_PERSISTENCE_MISMATCH", path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        if path.parent.is_dir():
            _fsync_directory(path.parent)


def _legacy_terminal_category(value: Mapping[str, Any]) -> Optional[str]:
    category = value.get("terminal_category")
    if category in TERMINAL_CATEGORIES:
        return str(category)
    if category in {
        "SCIENTIFIC_RESULT",
        "PROTOCOL_COMPLETE_VALID_RESULT",
        "SCIENTIFIC_OR_FROZEN_GATE_RESULT",
    }:
        return "TERMINAL_SUCCESS"
    if category == "ENGINEERING_FAILURE":
        return "TERMINAL_EXCLUSION"
    outcome = value.get("outcome")
    if outcome == "ELIGIBLE":
        return "TERMINAL_SUCCESS"
    if outcome in {"EVIDENCE_UNAVAILABLE", "RUNTIME_FAILURE"}:
        return "TERMINAL_EXCLUSION"
    final_status = value.get("final_status")
    if final_status in {"BLOCKED", "TERMINAL_BLOCKED"}:
        return "TERMINAL_BLOCKED"
    return None


def _is_legacy_prelaunch_placeholder(value: Mapping[str, Any]) -> bool:
    reasons = value.get("reason_codes", [])
    return (
        value.get("terminal") is True
        and isinstance(reasons, list)
        and "ATTRIBUTABLE_PRELAUNCH_ENGINEERING_FAILURE" in reasons
    )


def _inferred_nonterminal_state(run_directory: Path, default: str) -> str:
    if (run_directory / "REAL_LAUNCH_RECORD.json").is_file() or (
        run_directory / STAGE_A_RUNTIME_RESULT_FILE
    ).is_file():
        return "IN_PROGRESS"
    if (run_directory / "AUTHORIZATION_RECEIPT.json").is_file():
        return "RESERVED"
    return default


def validate_terminal_result(value: Mapping[str, Any]) -> str:
    if value.get("terminal") is not True:
        raise RunResultLifecycleError("TERMINAL_RESULT_FLAG_NOT_TRUE")
    category = _legacy_terminal_category(value)
    if category not in TERMINAL_CATEGORIES:
        raise RunResultLifecycleError("TERMINAL_RESULT_CATEGORY_INVALID")
    if category == "TERMINAL_EXCLUSION":
        exclusion = value.get("exclusion")
        if exclusion is not None:
            if not isinstance(exclusion, Mapping) or exclusion.get("category") not in {
                "ENGINEERING",
                "EVIDENCE",
            }:
                raise RunResultLifecycleError("TERMINAL_EXCLUSION_CATEGORY_INVALID")
            reasons = exclusion.get("reason_codes")
            if not isinstance(reasons, list) or not reasons:
                raise RunResultLifecycleError("TERMINAL_EXCLUSION_REASON_REQUIRED")
    return category


def publish_terminal_result(
    run_directory: Path, value: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Atomically publish the sole formal terminal result for one run."""

    category = value.get("terminal_category")
    if category not in TERMINAL_CATEGORIES:
        raise RunResultLifecycleError("TERMINAL_RESULT_CATEGORY_INVALID")
    if not isinstance(value.get("schema_version"), str) or not isinstance(
        value.get("run_id"), str
    ) or not value.get("run_id"):
        raise RunResultLifecycleError("TERMINAL_RESULT_IDENTITY_REQUIRED")
    reasons = value.get("reason_codes")
    if not isinstance(reasons, list) or not reasons:
        raise RunResultLifecycleError("TERMINAL_RESULT_REASON_REQUIRED")
    validate_terminal_result(value)
    if category == "TERMINAL_EXCLUSION" and value.get("exclusion") is None:
        raise RunResultLifecycleError("TERMINAL_EXCLUSION_STRUCTURE_REQUIRED")
    _exclusive_publish_json(run_directory / TERMINAL_RESULT_FILE, value)
    return value


def create_prelaunch_state(
    run_directory: Path, value: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Create an idempotent non-terminal prelaunch state in its own namespace."""

    if (
        value.get("terminal") is not False
        or value.get("lifecycle_state") not in NONTERMINAL_STATES
        or not isinstance(value.get("schema_version"), str)
        or not isinstance(value.get("run_id"), str)
        or not value.get("run_id")
    ):
        raise RunResultLifecycleError("PRELAUNCH_STATE_CONTRACT_INVALID")
    path = run_directory / PRELAUNCH_STATE_FILE
    try:
        _exclusive_publish_json(path, value)
    except DuplicateTerminalResultError as exc:
        existing = _load_json_object(path, "PRELAUNCH_STATE_INVALID_JSON")
        if json_bytes(existing) == json_bytes(value):
            return existing
        raise RunResultLifecycleError("PRELAUNCH_STATE_CONFLICT", path) from exc
    return value


def read_run_lifecycle(run_directory: Path) -> Mapping[str, Any]:
    """Read new and frozen legacy layouts without rewriting either one."""

    terminal_path = run_directory / TERMINAL_RESULT_FILE
    if terminal_path.is_file():
        value = _load_json_object(terminal_path, "TERMINAL_RESULT_INVALID_JSON")
        if _is_legacy_prelaunch_placeholder(value):
            return {
                "state": _inferred_nonterminal_state(run_directory, "PRELAUNCH"),
                "terminal": False,
                "terminal_category": None,
                "source_path": str(terminal_path),
                "payload": value,
                "legacy_compatibility": "LEGACY_RUN_RESULT_PRELAUNCH_PLACEHOLDER",
            }
        if (
            value.get("terminal") is not True
            and value.get("schema_version")
            == "driveclarify.observation_screening_runtime_result.v1"
        ):
            return {
                "state": "IN_PROGRESS",
                "terminal": False,
                "terminal_category": None,
                "source_path": str(terminal_path),
                "payload": value,
                "legacy_compatibility": "LEGACY_INNER_RUNTIME_RESULT_IN_TERMINAL_NAMESPACE",
            }
        category = validate_terminal_result(value)
        return {
            "state": category,
            "terminal": True,
            "terminal_category": category,
            "source_path": str(terminal_path),
            "payload": value,
            "legacy_compatibility": False,
        }

    state_path = run_directory / PRELAUNCH_STATE_FILE
    if state_path.is_file():
        value = _load_json_object(state_path, "PRELAUNCH_STATE_INVALID_JSON")
        recorded_state = value.get("lifecycle_state")
        if value.get("terminal") is not False or recorded_state not in NONTERMINAL_STATES:
            raise RunResultLifecycleError("PRELAUNCH_STATE_CONTRACT_INVALID", state_path)
        return {
            "state": _inferred_nonterminal_state(run_directory, str(recorded_state)),
            "terminal": False,
            "terminal_category": None,
            "source_path": str(state_path),
            "payload": value,
            "legacy_compatibility": False,
        }

    legacy_path = run_directory / LEGACY_PRELAUNCH_RESULT_FILE
    if legacy_path.is_file():
        return {
            "state": _inferred_nonterminal_state(run_directory, "PRELAUNCH"),
            "terminal": False,
            "terminal_category": None,
            "source_path": str(legacy_path),
            "payload": _load_json_object(legacy_path, "LEGACY_PRELAUNCH_RESULT_INVALID_JSON"),
            "legacy_compatibility": "LEGACY_PRELAUNCH_FAILURE_RUN_RESULT",
        }
    state = _inferred_nonterminal_state(
        run_directory, "PRELAUNCH" if run_directory.exists() else "ABSENT"
    )
    return {
        "state": state,
        "terminal": False,
        "terminal_category": None,
        "source_path": None,
        "payload": None,
        "legacy_compatibility": False,
    }
