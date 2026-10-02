#!/usr/bin/env python3
"""Thin ENGINEERING_DISCOVERY_ONLY entry for the shared native runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from driveclarify_grounded_language_v1.contracts import CONTROL_ENV
from driveclarify_paper_mvp_stage6b import backend as native


ROOT = Path(__file__).resolve().parents[1]
EXECUTION_PURPOSE = "ENGINEERING_DISCOVERY_ONLY"
ENGINEERING_SPLIT = "engineering_discovery_only"
CONTROL_OWNER = "ORIGINAL_SIMLINGO_BASELINE"
CANDIDATE_TRANSACTION_SOURCE = (
    ROOT / "tools/r4_3_candidate_capture_site/sitecustomize.py"
)
CANDIDATE_TRANSACTION_SHA256 = (
    "a07da7949e3ab99bc405603939d636a768b574a0b562815c71bb4898603c2ae4"
)
CANDIDATE_CAPTURE_SITE = CANDIDATE_TRANSACTION_SOURCE.parent
REQUIRED_CAPTURE_ENV = (
    "DRIVECLARIFY_R43_CAPTURE_ROOT",
    "DRIVECLARIFY_R43_DISCOVERY_ATTEMPT_ID",
    "DRIVECLARIFY_R43_FAMILY_ID",
    "DRIVECLARIFY_R43_PROTOCOL_VERSION",
)
FORBIDDEN_LEDGER_KEYS = {
    "formal_ledger",
    "formal_ledger_path",
    "train_ledger",
    "dev_ledger",
    "test_ledger",
    "ledger_path",
}


class EngineeringNativeEntryError(RuntimeError):
    """Fail-closed engineering-entry contract error."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _require_contract(request: Mapping[str, Any]) -> Any:
    if request.get("execution_purpose") != EXECUTION_PURPOSE:
        raise EngineeringNativeEntryError("ENGINEERING_EXECUTION_PURPOSE_REQUIRED")
    if request.get("formal_split") != "NONE":
        raise EngineeringNativeEntryError("ENGINEERING_FORMAL_SPLIT_MUST_BE_NONE")
    if request.get("formal_train") is not False:
        raise EngineeringNativeEntryError("ENGINEERING_FORMAL_TRAIN_FORBIDDEN")
    if request.get("dev") is not False:
        raise EngineeringNativeEntryError("ENGINEERING_DEV_FORBIDDEN")
    if request.get("test") is not False:
        raise EngineeringNativeEntryError("ENGINEERING_TEST_FORBIDDEN")
    if any(request.get(key) is not None for key in FORBIDDEN_LEDGER_KEYS):
        raise EngineeringNativeEntryError("ENGINEERING_FORMAL_LEDGER_FORBIDDEN")
    if request.get("DriveClarify_scientific_authority") != "disabled":
        raise EngineeringNativeEntryError("ENGINEERING_SCIENTIFIC_AUTHORITY_MUST_BE_DISABLED")
    if request.get("vehicle_control_owner") != CONTROL_OWNER:
        raise EngineeringNativeEntryError("ENGINEERING_BASELINE_CONTROL_OWNER_REQUIRED")
    spec = request.get("episode_spec")
    if spec is None or getattr(spec, "split", None) != ENGINEERING_SPLIT:
        raise EngineeringNativeEntryError("ENGINEERING_EPISODE_SPLIT_REQUIRED")
    if _sha256(CANDIDATE_TRANSACTION_SOURCE) != CANDIDATE_TRANSACTION_SHA256:
        raise EngineeringNativeEntryError("CANDIDATE_TRANSACTION_HASH_MISMATCH")
    capture = request.get("candidate_capture_environment")
    if not isinstance(capture, Mapping) or any(
        not str(capture.get(key, "")).strip() for key in REQUIRED_CAPTURE_ENV
    ):
        raise EngineeringNativeEntryError("CANONICAL_CANDIDATE_CAPTURE_BINDING_REQUIRED")
    return spec


def run(
    request: Mapping[str, Any],
    *,
    runtime_runner: Callable[..., Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    """Validate engineering isolation and invoke the shared runtime once."""

    spec = _require_contract(request)
    output_dir = Path(request["output_dir"]).resolve()
    environment = {
        str(key): str(value)
        for key, value in dict(request.get("environment") or {}).items()
    }
    capture_environment = {
        str(key): str(value)
        for key, value in dict(request["candidate_capture_environment"]).items()
    }
    environment.update(capture_environment)
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(CANDIDATE_CAPTURE_SITE) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    environment.update(
        {
            CONTROL_ENV: "0",
            "DRIVECLARIFY_TEMPORAL_GROUNDING_V1_CONTROL": "0",
            "DRIVECLARIFY_CANDIDATE_LIVE_ACT_AUTHORITY_V0": "0",
            "DRIVECLARIFY_SCIENTIFIC_AUTHORITY": "0",
        }
    )
    selected_runtime = native.run_native_episode if runtime_runner is None else runtime_runner
    runtime_result = selected_runtime(
        spec,
        output_dir,
        command=tuple(str(value) for value in request["command"]),
        environment=environment,
        cwd=Path(request["cwd"]).resolve(),
        wall_timeout_seconds=float(request["timeout_seconds"]),
        wall_timeout_reason="ENGINEERING_NATIVE_WALL_TIMEOUT",
        poll_observer=request.get("poll_observer"),
        cleanup_writer=lambda value: _atomic_json(
            output_dir / "ENGINEERING_NATIVE_CLEANUP_RECEIPT.json", value
        ),
    )
    receipt = {
        "schema_version": "driveclarify.r4_3.engineering_native_entry.receipt.v1",
        "status": "ENGINEERING_NATIVE_RUNTIME_RETURNED",
        "execution_purpose": EXECUTION_PURPOSE,
        "formal_split": "NONE",
        "formal_train": False,
        "DEV": False,
        "TEST": False,
        "formal_ledger": None,
        "DriveClarify_scientific_authority": "disabled",
        "vehicle_control_owner": CONTROL_OWNER,
        "episode_split": getattr(spec, "split"),
        "candidate_transaction_owner": str(
            CANDIDATE_TRANSACTION_SOURCE.relative_to(ROOT)
        ),
        "candidate_transaction_sha256": CANDIDATE_TRANSACTION_SHA256,
        "runtime_primitive": "driveclarify_paper_mvp_stage6b.backend.run_native_episode",
        "runtime_result": dict(runtime_result),
    }
    _atomic_json(output_dir / "ENGINEERING_NATIVE_ENTRY_RECEIPT.json", receipt)
    return receipt


__all__ = [
    "CANDIDATE_TRANSACTION_SHA256",
    "CONTROL_OWNER",
    "ENGINEERING_SPLIT",
    "EXECUTION_PURPOSE",
    "EngineeringNativeEntryError",
    "run",
]
