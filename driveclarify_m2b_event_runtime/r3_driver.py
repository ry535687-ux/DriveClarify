"""Host-side R3 production prediction lifecycle driver.

The Event API is called outside the prediction namespace. The sandbox receives
only the nine allowlisted prediction files, runtime-only inputs, and one writable
staging directory.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from driveclarify_m2b_prediction_runtime.contracts import canonical_bytes, file_sha256

from .r3_api import (
    create_event,
    mark_prediction_started,
    mark_predictions_immutable,
    publish_predictions,
)


R3_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "PYTHONPATH": "/app",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "CUDA_VISIBLE_DEVICES": "",
    "LC_ALL": "C.UTF-8",
}


def _load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "driveclarify.m2b_blind_r3_prediction_bundle_manifest.v1":
        raise ValueError("R3_PREDICTION_BUNDLE_MANIFEST_SCHEMA_INVALID")
    rows = manifest.get("files")
    if not isinstance(rows, list) or len(rows) != 9:
        raise ValueError("R3_PREDICTION_BUNDLE_FILE_COUNT_INVALID")
    return manifest


def build_production_prediction_command(*, bwrap: Path, bundle_root: Path,
                                        bundle_manifest_path: Path,
                                        read_only_inputs: Mapping[str, Path],
                                        output_dir: Path, arguments: Sequence[str],
                                        probe_code: str | None = None) -> list[str]:
    manifest = _load_manifest(bundle_manifest_path)
    command = [
        str(bwrap.resolve(strict=True)),
        "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--dir", "/inputs", "--dir", "/outputs", "--dir", "/app",
        "--dir", "/app/driveclarify_m2b_prediction_runtime",
        "--dir", "/app/driveclarify_decision",
    ]
    for target, source in sorted(read_only_inputs.items()):
        canonical_target = str(PurePosixPath(target))
        resolved = source.resolve(strict=True)
        if not canonical_target.startswith("/inputs/") or not resolved.is_file():
            raise ValueError("R3_PRODUCTION_INPUT_MOUNT_INVALID")
        lowered = f"{canonical_target}\n{resolved}".lower()
        if any(fragment in lowered for fragment in ("gold", "key", "evaluator", "reference", "verifier")):
            raise ValueError("R3_PRODUCTION_INPUT_FORBIDDEN")
        command.extend(("--ro-bind", str(resolved), canonical_target))
    destinations: set[str] = set()
    for row in manifest["files"]:
        source = (bundle_root / row["bundle_relative_path"]).resolve(strict=True)
        if bundle_root.resolve() not in source.parents or not source.is_file():
            raise ValueError("R3_BUNDLE_SOURCE_INVALID")
        if source.stat().st_size != row["bytes"] or file_sha256(source) != row["sha256"]:
            raise ValueError("R3_BUNDLE_SOURCE_HASH_MISMATCH")
        destination = str(PurePosixPath(row["mount_destination"]))
        if not destination.startswith("/app/") or destination in destinations:
            raise ValueError("R3_BUNDLE_DESTINATION_INVALID")
        destinations.add(destination)
        command.extend(("--ro-bind", str(source), destination))
    output = output_dir.resolve(strict=True)
    if not output.is_dir():
        raise ValueError("R3_PRODUCTION_OUTPUT_DIRECTORY_INVALID")
    command.extend(("--bind", str(output), "/outputs"))
    for key, value in R3_ENVIRONMENT.items():
        command.extend(("--setenv", key, value))
    command.extend(("--chdir", "/app", "/usr/bin/python3"))
    if probe_code is None:
        command.extend(("/app/driveclarify_m2b_prediction_runtime/entrypoint.py", *arguments))
    else:
        command.extend(("-c", probe_code))
    lowered = "\n".join(command).lower()
    if any(fragment in lowered for fragment in ("sealed_evaluation_gold", "gold_key", "decryption_key")):
        raise ValueError("R3_PRODUCTION_ARGV_GOLD_OR_KEY_EXPOSURE")
    return command


def run_production_prediction(command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), env={}, stdin=subprocess.DEVNULL, text=True,
        capture_output=True, timeout=timeout, check=False,
        close_fds=True, pass_fds=(),
    )


def _recover_partial_envelope(*, journal_path: Path, envelope_path: Path,
                              design_id: str, prediction_event_id: str,
                              expected_record_count: int,
                              hashes: Mapping[str, str], crash_reason: str) -> dict[str, Any]:
    if envelope_path.exists():
        return json.loads(envelope_path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    if journal_path.exists():
        for line in journal_path.read_text(encoding="utf-8").splitlines():
            if line:
                records.append(json.loads(line))
    envelope = {
        "schema_version": "driveclarify.m2b_blind_raw_prediction.r3",
        "design_id": design_id,
        "prediction_event_id": prediction_event_id,
        "completeness": "PARTIAL",
        "expected_record_count": expected_record_count,
        "record_count": len(records),
        "canonical_record_order": ["canonical_case_index", "canonical_comparison_index"],
        "config_hashes": dict(sorted(hashes.items())),
        "crash_reason": crash_reason,
        "records": records,
    }
    fd = os.open(envelope_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        payload = canonical_bytes(envelope)
        written = 0
        while written < len(payload):
            written += os.write(fd, payload[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(envelope_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return envelope


def execute_production_event(*, state_path: Path, event_manifest_path: Path,
                             tombstone_path: Path, audit_path: Path,
                             immutable_prediction_path: Path,
                             blind_execution_id: str, prediction_event_id: str,
                             config: Mapping[str, Any], bwrap: Path,
                             bundle_root: Path, bundle_manifest_path: Path,
                             read_only_inputs: Mapping[str, Path], output_dir: Path,
                             sandbox_arguments: Sequence[str], hashes: Mapping[str, str],
                             explicit_authorization: bool, timeout: float = 3600.0) -> dict[str, Any]:
    """Execute the single formal prediction event; never used by the R3 reseal itself."""
    create_event(
        state_path, event_manifest_path, tombstone_path, audit_path,
        blind_execution_id=blind_execution_id, prediction_event_id=prediction_event_id,
        config=config, explicit_authorization=explicit_authorization,
    )
    mark_prediction_started(state_path, event_manifest_path, audit_path, config=config)
    command = build_production_prediction_command(
        bwrap=bwrap, bundle_root=bundle_root, bundle_manifest_path=bundle_manifest_path,
        read_only_inputs=read_only_inputs, output_dir=output_dir,
        arguments=sandbox_arguments,
    )
    completed = run_production_prediction(command, timeout=timeout)
    envelope_path = output_dir / "envelope.json"
    journal_path = output_dir / "journal.jsonl"
    if not envelope_path.exists():
        envelope = _recover_partial_envelope(
            journal_path=journal_path, envelope_path=envelope_path,
            design_id=config["design_id"], prediction_event_id=prediction_event_id,
            expected_record_count=config["expected_record_count"], hashes=hashes,
            crash_reason="SANDBOX_PROCESS_TERMINATED",
        )
    else:
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    payload = canonical_bytes(envelope)
    if envelope_path.read_bytes() != payload:
        raise RuntimeError("R3_SANDBOX_ENVELOPE_NOT_CANONICAL")
    publish_predictions(
        state_path, audit_path, immutable_prediction_path, payload,
        completeness=envelope["completeness"], record_count=envelope["record_count"],
        expected_record_count=envelope["expected_record_count"],
    )
    state = mark_predictions_immutable(state_path, audit_path, immutable_prediction_path)
    return {"state": state, "sandbox_returncode": completed.returncode,
            "sandbox_stdout": completed.stdout, "sandbox_stderr": completed.stderr,
            "envelope": envelope}
