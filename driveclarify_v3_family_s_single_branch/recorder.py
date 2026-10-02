"""Passive atomic publication of one complete Family-S evidence chain."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Mapping

from .evidence import (
    LayeredEvidenceBundle,
    TrustedValidationContext,
    content_hash as layered_content_hash,
)
from .validator import (
    FROZEN_SCHEMA_SHA256,
    FamilySRecordValidator,
    LayeredFamilySRecordValidator,
)
from .wrapper import SingleBranchEvidenceBundle


class ArtifactCommitError(RuntimeError):
    """A candidate chain could not be atomically published."""


def _is_passive_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_passive_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_passive_json_value(item)
            for key, item in value.items()
        )
    return False


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


class PassiveFamilySRecorder(object):
    """Copy one wrapper bundle and record; never receive runtime handles."""

    def __init__(self, validator: FamilySRecordValidator) -> None:
        if not isinstance(validator, FamilySRecordValidator):
            raise TypeError("FAMILY_S_RECORD_VALIDATOR_REQUIRED")
        self._validator = validator
        self._lock = threading.RLock()

    @staticmethod
    def _write_json(path: Path, value: Mapping[str, Any]) -> None:
        payload = _canonical_bytes(value) + b"\n"
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def commit(
            self,
            record: Mapping[str, Any],
            evidence_bundle: SingleBranchEvidenceBundle,
            artifact_root: Path) -> Dict[str, Any]:
        if not isinstance(record, Mapping):
            raise TypeError("FAMILY_S_RECORD_MAPPING_REQUIRED")
        if not isinstance(evidence_bundle, SingleBranchEvidenceBundle):
            raise TypeError("WRAPPER_EVIDENCE_BUNDLE_REQUIRED")
        if not _is_passive_json_value(dict(record)):
            raise ArtifactCommitError("RUNTIME_HANDLE_OR_NONFINITE_VALUE_REJECTED")
        record_copy = json.loads(_canonical_bytes(record).decode("utf-8"))
        plan_artifact, adapter_artifact, wrapper_receipt = evidence_bundle.artifacts()
        preflight = self._validator.validate_bundle(record_copy, evidence_bundle)
        if not preflight.valid:
            raise ArtifactCommitError(
                "CANDIDATE_CHAIN_VALIDATION_FAILED:"
                + ",".join(preflight.exclusion_reason_codes)
            )
        record_id = record_copy.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise ArtifactCommitError("RECORD_ID_REQUIRED")
        objects = {
            "sealed_plan.json": plan_artifact,
            "adapter_transaction.json": adapter_artifact,
            "wrapper_receipt.json": wrapper_receipt,
            "record.json": record_copy,
        }
        artifact_hashes = {
            name: _content_hash(value) for name, value in objects.items()
        }
        identity_payload = {
            "record_id": record_id,
            "transaction_id": wrapper_receipt.get("transaction_id"),
            "artifact_sha256": artifact_hashes,
        }
        artifact_set_id = _content_hash(identity_payload)
        name = "artifact-" + artifact_set_id
        manifest = {
            "schema": "driveclarify.v3.family-s-commit-manifest.v1",
            "artifact_set_id": artifact_set_id,
            "artifact_root_name": name,
            "record_id": record_id,
            "transaction_id": wrapper_receipt.get("transaction_id"),
            "artifact_sha256": artifact_hashes,
            "frozen_schema_sha256": FROZEN_SCHEMA_SHA256,
            "candidate_complete": True,
            "atomic_publish": "DIRECTORY_RENAME",
        }
        root = Path(artifact_root)
        root.mkdir(parents=True, exist_ok=True)
        destination = root / name
        with self._lock:
            if destination.exists():
                raise ArtifactCommitError("ARTIFACT_SET_ALREADY_COMMITTED")
            temporary = Path(tempfile.mkdtemp(prefix="." + name + ".partial-", dir=str(root)))
            try:
                for filename in self._validator._ARTIFACT_FILES:
                    self._write_json(temporary / filename, objects[filename])
                self._write_json(temporary / "commit_manifest.json", manifest)
                directory_fd = os.open(str(temporary), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                os.replace(str(temporary), str(destination))
                root_fd = os.open(str(root), os.O_RDONLY)
                try:
                    os.fsync(root_fd)
                finally:
                    os.close(root_fd)
            except Exception as error:
                if temporary.exists():
                    shutil.rmtree(str(temporary))
                raise ArtifactCommitError(
                    "ATOMIC_ARTIFACT_COMMIT_FAILED:" + type(error).__name__
                ) from error
        result = self._validator.validate_artifact(destination)
        if not result.valid:
            rejected = root / (name + ".rejected")
            os.replace(str(destination), str(rejected))
            raise ArtifactCommitError(
                "POST_COMMIT_VALIDATION_FAILED_RETAINED_INELIGIBLE:" + str(rejected)
            )
        return {
            "artifact_directory": str(destination),
            "artifact_set_id": artifact_set_id,
            "record_id": record_id,
            "transaction_id": wrapper_receipt.get("transaction_id"),
            "record_sha256": artifact_hashes["record.json"],
            "a1_training_eligible": result.a1_training_eligible,
        }


class LayeredPassiveFamilySRecorder(PassiveFamilySRecorder):
    """Publish candidate copies only after every external owner is selected."""

    def __init__(self, validator: LayeredFamilySRecordValidator) -> None:
        if not isinstance(validator, LayeredFamilySRecordValidator):
            raise TypeError("LAYERED_FAMILY_S_RECORD_VALIDATOR_REQUIRED")
        super().__init__(validator)
        self._layered_validator = validator

    def commit_layered(
            self, record: Mapping[str, Any], evidence: LayeredEvidenceBundle,
            trusted_context: TrustedValidationContext,
            artifact_root: Path) -> Dict[str, Any]:
        if not isinstance(record, Mapping):
            raise TypeError("FAMILY_S_RECORD_MAPPING_REQUIRED")
        if not isinstance(evidence, LayeredEvidenceBundle):
            raise TypeError("LAYERED_EVIDENCE_BUNDLE_REQUIRED")
        if not isinstance(trusted_context, TrustedValidationContext):
            raise TypeError("EXTERNAL_TRUST_CONTEXT_REQUIRED")
        if not _is_passive_json_value(dict(record)):
            raise ArtifactCommitError("RUNTIME_HANDLE_OR_NONFINITE_VALUE_REJECTED")
        record_copy = json.loads(_canonical_bytes(record).decode("utf-8"))
        preflight = self._layered_validator.validate_layered_bundle(
            record_copy, evidence, trusted_context
        )
        if not preflight.record_valid:
            raise ArtifactCommitError(
                "LAYERED_CANDIDATE_VALIDATION_FAILED:"
                + ",".join(preflight.exclusion_reason_codes)
            )
        objects = evidence.artifacts()
        objects["record.json"] = record_copy
        trusted = trusted_context.load()
        authorization_hash = layered_content_hash(
            trusted["authorization_manifest"]
        )
        trusted_plan_hash = layered_content_hash(trusted["trusted_sealed_plan"])
        run_hash = layered_content_hash(trusted["run_manifest"])
        artifact_hashes = {
            name: layered_content_hash(value) for name, value in objects.items()
        }
        identity = {
            "authorization_manifest_sha256": authorization_hash,
            "run_manifest_sha256": run_hash,
            "record_id": record_copy.get("record_id"),
            "artifact_sha256": artifact_hashes,
        }
        artifact_set_id = layered_content_hash(identity)
        name = "artifact-" + artifact_set_id
        manifest = {
            "schema": "driveclarify.v3.layered-family-s-commit-manifest.v1",
            "artifact_set_id": artifact_set_id,
            "artifact_root_name": name,
            "authorization_manifest_sha256": authorization_hash,
            "trusted_sealed_plan_sha256": trusted_plan_hash,
            "run_manifest_sha256": run_hash,
            "record_id": record_copy.get("record_id"),
            "episode_id": record_copy.get("episode_id"),
            "artifact_names": sorted(objects),
            "artifact_sha256": artifact_hashes,
            "frozen_schema_sha256": FROZEN_SCHEMA_SHA256,
            "record_eligibility_input_set": sorted(objects),
            "candidate_complete": True,
            "atomic_publish": "DIRECTORY_RENAME",
        }
        root = Path(artifact_root)
        root.mkdir(parents=True, exist_ok=True)
        destination = root / name
        with self._lock:
            if destination.exists():
                raise ArtifactCommitError("ARTIFACT_SET_ALREADY_COMMITTED")
            temporary = Path(tempfile.mkdtemp(
                prefix="." + name + ".partial-", dir=str(root)
            ))
            try:
                for filename in sorted(objects):
                    self._write_json(temporary / filename, objects[filename])
                self._write_json(temporary / "commit_manifest.json", manifest)
                directory_fd = os.open(str(temporary), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                os.replace(str(temporary), str(destination))
                root_fd = os.open(str(root), os.O_RDONLY)
                try:
                    os.fsync(root_fd)
                finally:
                    os.close(root_fd)
            except Exception as error:
                if temporary.exists():
                    shutil.rmtree(str(temporary))
                raise ArtifactCommitError(
                    "ATOMIC_LAYERED_ARTIFACT_COMMIT_FAILED:"
                    + type(error).__name__
                ) from error
        result = self._layered_validator.validate_layered_artifact(
            destination, trusted_context
        )
        if not result.record_valid or not result.run_valid:
            rejected = root / (name + ".rejected")
            os.replace(str(destination), str(rejected))
            raise ArtifactCommitError(
                "POST_COMMIT_LAYERED_VALIDATION_FAILED_RETAINED_INELIGIBLE:"
                + str(rejected)
            )
        return {
            "artifact_directory": str(destination),
            "artifact_set_id": artifact_set_id,
            "record_id": record_copy.get("record_id"),
            "episode_id": record_copy.get("episode_id"),
            "record_sha256": artifact_hashes["record.json"],
            "record_valid": result.record_valid,
            "run_valid": result.run_valid,
            "family_s_positive_eligible": result.family_s_positive_eligible,
            "a1_training_eligible": False,
            "a1_dev_eligible": False,
            "a1_test_eligible": False,
        }
