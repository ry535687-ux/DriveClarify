"""CPU-only candidate-schedule identity and exact runtime gate.

This module is the single schedule-hash authority used by M3B-R2 package
preparation, source-manifest verification, the prelaunch gate, and the runtime
agent wrapper.  It imports neither CARLA nor SimLingo, torch, or CUDA-facing
modules.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_consequence.serialization import canonical_json


SCHEDULE_SCHEMA_VERSION = "driveclarify.maneuver_branch.candidate_schedule.v1"
SCHEDULE_GATE_SCHEMA_VERSION = "driveclarify.m3b_r2.exact_schedule_gate.v1"
SOURCE_GATE_SCHEMA_VERSION = "driveclarify.m3b_r2.source_manifest_gate.v1"
EXPECTED_CANDIDATE_IDS = frozenset({"A1", "A2", "A3", "B1", "B2", "B3"})
SCHEDULE_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "candidate_order",
        "randomization",
        "frozen_before_runtime",
        "candidate_outputs_read",
        "evidence_sha256",
    }
)
RANDOMIZATION_KEYS = frozenset(
    {
        "algorithm",
        "generated_at_preparation",
        "generated_before_candidate_outputs",
        "seed_hex",
    }
)
AUTHORITY_DESCRIPTION = (
    "SHA256_CANONICAL_JSON_OF_FULL_UNSIGNED_SCHEDULE_INCLUDING_RUN_ID"
)
SEMANTIC_DESCRIPTION = (
    "SHA256_CANONICAL_JSON_OF_UNSIGNED_SCHEDULE_EXCLUDING_NONSCIENTIFIC_RUN_ID"
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return _sha256(_canonical_bytes(value))


def _require_digest(value: Any, reason: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RuntimeError(reason)
    return value


def _load_json_object(path: Path, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(reason) from exc
    if not isinstance(value, dict):
        raise RuntimeError(reason)
    return value


def validate_candidate_schedule_object(schedule: Mapping[str, Any]) -> None:
    """Apply the exact schema and candidate-order checks used at runtime."""

    if set(schedule) != SCHEDULE_KEYS:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_SCHEMA_FIELDS_INVALID")
    if schedule.get("schema_version") != SCHEDULE_SCHEMA_VERSION:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_SCHEMA_VERSION_INVALID")
    run_id = schedule.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("DC-RPSM-M3B-"):
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_RUN_ID_INVALID")
    order = schedule.get("candidate_order")
    if not isinstance(order, list) or len(order) != 6:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_INVALID")
    if any(not isinstance(item, str) for item in order) or set(order) != EXPECTED_CANDIDATE_IDS:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_INVALID")
    if len(set(order)) != len(order):
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_DUPLICATE_CANDIDATE")
    if sorted(item for item in order if item.startswith("A")) != ["A1", "A2", "A3"]:
        raise RuntimeError("MANEUVER_BRANCH_A_SCHEDULE_INVALID")
    if sorted(item for item in order if item.startswith("B")) != ["B1", "B2", "B3"]:
        raise RuntimeError("MANEUVER_BRANCH_B_SCHEDULE_INVALID")
    randomization = schedule.get("randomization")
    if not isinstance(randomization, dict) or set(randomization) != RANDOMIZATION_KEYS:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_RNG_FIELDS_INVALID")
    if randomization.get("algorithm") != "PYTHON_RANDOM_SHUFFLE_WITH_256_BIT_OS_ENTROPY_SEED":
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_RNG_ALGORITHM_INVALID")
    if randomization.get("generated_at_preparation") is not True:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_RNG_PREPARATION_FLAG_INVALID")
    if randomization.get("generated_before_candidate_outputs") is not True:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_RNG_OUTPUT_ORDER_FLAG_INVALID")
    _require_digest(randomization.get("seed_hex"), "MANEUVER_BRANCH_SCHEDULE_RNG_SEED_INVALID")
    if schedule.get("frozen_before_runtime") is not True:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_NOT_FROZEN")
    if schedule.get("candidate_outputs_read") is not False:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_CANDIDATE_OUTPUTS_READ")
    _require_digest(schedule.get("evidence_sha256"), "MANEUVER_BRANCH_SCHEDULE_EVIDENCE_INVALID")


def build_candidate_schedule(
    *,
    run_id: str,
    candidate_order: Sequence[str],
    randomization: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact signed object used by preparation and runtime."""

    unsigned = {
        "schema_version": SCHEDULE_SCHEMA_VERSION,
        "run_id": run_id,
        "candidate_order": list(candidate_order),
        "randomization": copy.deepcopy(dict(randomization)),
        "frozen_before_runtime": True,
        "candidate_outputs_read": False,
    }
    result = copy.deepcopy(unsigned)
    result["evidence_sha256"] = _canonical_sha256(unsigned)
    validate_candidate_schedule_object(result)
    return result


def scientific_schedule_payload(schedule: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(schedule))
    payload.pop("evidence_sha256", None)
    payload.pop("run_id", None)
    return payload


def compute_candidate_schedule_identity(schedule_path: str | Path) -> dict[str, Any]:
    """Compute raw, canonical, run-bound, and scientific schedule identities."""

    path = Path(schedule_path)
    try:
        resolved = path.resolve(strict=True)
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_PATH_UNREADABLE") from exc
    if not path.is_file():
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_PATH_NOT_REGULAR_FILE")
    schedule = _load_json_object(path, "MANEUVER_BRANCH_SCHEDULE_JSON_INVALID")
    validate_candidate_schedule_object(schedule)
    unsigned = copy.deepcopy(schedule)
    recorded = unsigned.pop("evidence_sha256")
    authority = _canonical_sha256(unsigned)
    semantic = _canonical_sha256(scientific_schedule_payload(schedule))
    return {
        "schedule_path": str(path),
        "resolved_schedule_path": str(resolved),
        "file_size": len(raw),
        "raw_file_sha256": _sha256(raw),
        "canonical_json_sha256": _canonical_sha256(schedule),
        "canonical_payload_sha256": authority,
        "recorded_evidence_sha256": recorded,
        "recorded_evidence_matches_authority": recorded == authority,
        "semantic_payload_sha256": semantic,
        "schema_version": schedule["schema_version"],
        "run_id": schedule["run_id"],
        "candidate_order": copy.deepcopy(schedule["candidate_order"]),
        "entry_count": len(schedule["candidate_order"]),
        "randomization": copy.deepcopy(schedule["randomization"]),
        "hash_algorithm": "SHA-256",
        "canonical_representation": "UTF8_SORTED_KEYS_COMPACT_JSON_NO_TRAILING_NEWLINE",
        "authorization_integrity_authority": AUTHORITY_DESCRIPTION,
        "scientific_semantic_identity": SEMANTIC_DESCRIPTION,
    }


def load_and_validate_runtime_candidate_schedule(
    *,
    schedule_path: str | Path,
    expected_run_id: str,
    expected_authority_sha256: str,
    expected_semantic_sha256: str,
    expected_raw_file_sha256: str,
    expected_candidate_order: Sequence[str],
    expected_randomization: Mapping[str, Any],
) -> dict[str, Any]:
    """The real R2 wrapper loader and its exact fail-closed hash gate."""

    identity = compute_candidate_schedule_identity(schedule_path)
    schedule = _load_json_object(Path(schedule_path), "MANEUVER_BRANCH_SCHEDULE_JSON_INVALID")
    if identity["run_id"] != expected_run_id:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_RUN_ID_MISMATCH")
    if identity["candidate_order"] != list(expected_candidate_order):
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_ORDER_MISMATCH")
    if identity["randomization"] != dict(expected_randomization):
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_RNG_MISMATCH")
    if identity["raw_file_sha256"] != _require_digest(
        expected_raw_file_sha256, "MANEUVER_BRANCH_SCHEDULE_EXPECTED_RAW_HASH_INVALID"
    ):
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_RAW_FILE_HASH_MISMATCH")
    if identity["canonical_payload_sha256"] != _require_digest(
        expected_authority_sha256, "MANEUVER_BRANCH_SCHEDULE_EXPECTED_HASH_INVALID"
    ):
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_HASH_MISMATCH")
    if not identity["recorded_evidence_matches_authority"]:
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_RECORDED_HASH_MISMATCH")
    if identity["semantic_payload_sha256"] != _require_digest(
        expected_semantic_sha256, "MANEUVER_BRANCH_SCHEDULE_EXPECTED_SEMANTIC_HASH_INVALID"
    ):
        raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_SEMANTIC_HASH_MISMATCH")
    return {"schedule": schedule, "identity": identity}


def _validate_candidate_semantics(
    run_spec: Mapping[str, Any], capture_plan: Mapping[str, Any]
) -> str:
    run_semantics = run_spec.get("candidate_semantics")
    capture_semantics = capture_plan.get("candidate_semantics")
    if not isinstance(run_semantics, dict) or run_semantics != capture_semantics:
        raise RuntimeError("MANEUVER_BRANCH_CANDIDATE_SEMANTICS_MISMATCH")
    digest = _canonical_sha256(run_semantics)
    expected = run_spec.get("candidate_schedule_contract", {}).get(
        "candidate_semantics_sha256"
    )
    if digest != expected:
        raise RuntimeError("MANEUVER_BRANCH_CANDIDATE_SEMANTICS_HASH_MISMATCH")
    return digest


def validate_exact_runtime_schedule_gate(package_dir: str | Path) -> dict[str, Any]:
    """Validate the final package with the same loader called by the R2 wrapper."""

    package = Path(package_dir)
    try:
        resolved_package = package.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("M3B_R2_PACKAGE_PATH_INVALID") from exc
    if not package.is_dir():
        raise RuntimeError("M3B_R2_PACKAGE_PATH_INVALID")
    run_spec_path = package / "RUN_SPEC.json"
    manifest_path = package / "SOURCE_MANIFEST.json"
    capture_plan_path = package / "CAPTURE_PLAN.json"
    actual_schedule_path = package / "CANDIDATE_SCHEDULE.json"
    run_spec = _load_json_object(run_spec_path, "M3B_R2_RUN_SPEC_INVALID")
    manifest = _load_json_object(manifest_path, "M3B_R2_SOURCE_MANIFEST_INVALID")
    capture_plan = _load_json_object(capture_plan_path, "M3B_R2_CAPTURE_PLAN_INVALID")
    run_id = run_spec.get("run_id")
    if not isinstance(run_id, str) or run_id != capture_plan.get("run_id") or run_id != manifest.get("run_id"):
        raise RuntimeError("M3B_R2_PACKAGE_RUN_ID_MISMATCH")
    contract = run_spec.get("candidate_schedule_contract")
    if not isinstance(contract, dict):
        raise RuntimeError("M3B_R2_SCHEDULE_CONTRACT_MISSING")
    expected_path = Path(str(contract.get("schedule_path", "")))
    try:
        expected_resolved = expected_path.resolve(strict=True)
        actual_resolved = actual_schedule_path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("M3B_R2_SCHEDULE_PATH_RESOLUTION_FAILED") from exc
    runtime_environment_path = Path(
        str(
            run_spec.get("exact_command", {})
            .get("environment", {})
            .get("DRIVECLARIFY_MB_CANDIDATE_SCHEDULE", "")
        )
    )
    if expected_resolved != actual_resolved:
        raise RuntimeError("M3B_R2_EXPECTED_SCHEDULE_PATH_MISMATCH")
    try:
        if runtime_environment_path.resolve(strict=True) != actual_resolved:
            raise RuntimeError("M3B_R2_RUNTIME_SCHEDULE_PATH_MISMATCH")
    except OSError as exc:
        raise RuntimeError("M3B_R2_RUNTIME_SCHEDULE_PATH_MISMATCH") from exc
    capture_contract = capture_plan.get("candidate_schedule")
    manifest_contract = manifest.get("candidate_schedule_identity")
    if not isinstance(capture_contract, dict) or not isinstance(manifest_contract, dict):
        raise RuntimeError("M3B_R2_PACKAGE_SCHEDULE_PIN_MISSING")
    compared_fields = (
        "schedule_path",
        "raw_file_sha256",
        "canonical_payload_sha256",
        "semantic_payload_sha256",
        "candidate_semantics_sha256",
    )
    for field in compared_fields:
        if contract.get(field) != capture_contract.get(field) or contract.get(field) != manifest_contract.get(field):
            raise RuntimeError("M3B_R2_PACKAGE_SCHEDULE_PIN_DISAGREEMENT:" + field)
    if contract.get("candidate_order") != capture_contract.get("candidate_order"):
        raise RuntimeError("M3B_R2_PACKAGE_SCHEDULE_ORDER_PIN_DISAGREEMENT")
    if contract.get("randomization") != capture_contract.get("randomization"):
        raise RuntimeError("M3B_R2_PACKAGE_SCHEDULE_RNG_PIN_DISAGREEMENT")
    loaded = load_and_validate_runtime_candidate_schedule(
        schedule_path=actual_schedule_path,
        expected_run_id=run_id,
        expected_authority_sha256=str(contract.get("canonical_payload_sha256", "")),
        expected_semantic_sha256=str(contract.get("semantic_payload_sha256", "")),
        expected_raw_file_sha256=str(contract.get("raw_file_sha256", "")),
        expected_candidate_order=contract.get("candidate_order", []),
        expected_randomization=contract.get("randomization", {}),
    )
    semantics_sha256 = _validate_candidate_semantics(run_spec, capture_plan)
    if run_spec.get("repeat_count_per_candidate") != 3:
        raise RuntimeError("M3B_R2_REPEAT_COUNT_CONTRACT_MISMATCH")
    if run_spec.get("fresh_observation_count") != 1 or run_spec.get("frozen_observation_count") != 1:
        raise RuntimeError("M3B_R2_OBSERVATION_COUNT_CONTRACT_MISMATCH")
    if run_spec.get("same_loaded_model_instance") is not True:
        raise RuntimeError("M3B_R2_MODEL_INSTANCE_CONTRACT_MISMATCH")
    if run_spec.get("candidate_execution_mode") != "PLAN_ONLY":
        raise RuntimeError("M3B_R2_PLAN_ONLY_CONTRACT_MISMATCH")
    if run_spec.get("candidate_side_effect_ceiling") != {
        "pid": 0,
        "planner_advance": 0,
        "control": 0,
        "additional_world_tick": 0,
    }:
        raise RuntimeError("M3B_R2_SIDE_EFFECT_CONTRACT_MISMATCH")
    if run_spec.get("cleanup_mandatory") is not True:
        raise RuntimeError("M3B_R2_CLEANUP_CONTRACT_MISMATCH")
    if run_spec.get("launch_retry_resume") != "1/0/0":
        raise RuntimeError("M3B_R2_RETRY_CONTRACT_MISMATCH")
    return {
        "schema_version": SCHEDULE_GATE_SCHEMA_VERSION,
        "status": "PASS_EXACT_RUNTIME_SCHEDULE_GATE",
        "package_dir": str(package),
        "resolved_package_dir": str(resolved_package),
        "run_id": run_id,
        "run_authorized": run_spec.get("run_authorized"),
        "expected_schedule_path": str(expected_path),
        "actual_schedule_path": str(actual_schedule_path),
        "resolved_actual_schedule_path": str(actual_resolved),
        "runtime_environment_schedule_path": str(runtime_environment_path),
        "expected_hash": contract["canonical_payload_sha256"],
        "actual_hash": loaded["identity"]["canonical_payload_sha256"],
        "raw_file_sha256": loaded["identity"]["raw_file_sha256"],
        "semantic_payload_sha256": loaded["identity"]["semantic_payload_sha256"],
        "candidate_semantics_sha256": semantics_sha256,
        "candidate_order": loaded["identity"]["candidate_order"],
        "entry_count": loaded["identity"]["entry_count"],
        "hash_algorithm": "SHA-256",
        "hash_representation": AUTHORITY_DESCRIPTION,
        "runtime_loader": (
            "driveclarify_real_mapping_v1.candidate_schedule_identity."
            "load_and_validate_runtime_candidate_schedule"
        ),
        "forbidden_runtime_imports_or_launches": {
            "carla": 0,
            "evaluator": 0,
            "simlingo_model_or_checkpoint": 0,
            "torch": 0,
            "cuda": 0,
            "gpu": 0,
        },
    }


def verify_source_manifest(package_dir: str | Path) -> dict[str, Any]:
    package = Path(package_dir)
    manifest = _load_json_object(package / "SOURCE_MANIFEST.json", "M3B_R2_SOURCE_MANIFEST_INVALID")
    repository_root = Path(str(manifest.get("repository_root", "")))
    if not repository_root.is_dir():
        raise RuntimeError("M3B_R2_SOURCE_MANIFEST_ROOT_INVALID")
    run_spec_identity = manifest.get("run_spec_normalized_identity")
    if not isinstance(run_spec_identity, dict):
        raise RuntimeError("M3B_R2_RUN_SPEC_NORMALIZED_IDENTITY_MISSING")
    run_spec_path = Path(str(run_spec_identity.get("path", "")))
    resolved_run_spec = (
        run_spec_path if run_spec_path.is_absolute() else repository_root / run_spec_path
    )
    run_spec = _load_json_object(resolved_run_spec, "M3B_R2_RUN_SPEC_INVALID")
    normalized_run_spec = copy.deepcopy(run_spec)
    normalized_run_spec["run_authorized"] = False
    normalized_run_spec_sha256 = _canonical_sha256(normalized_run_spec)
    if run_spec_identity.get("ignored_runtime_mutable_fields") != ["run_authorized"]:
        raise RuntimeError("M3B_R2_RUN_SPEC_NORMALIZATION_SCOPE_INVALID")
    if normalized_run_spec_sha256 != run_spec_identity.get("canonical_sha256"):
        raise RuntimeError("M3B_R2_RUN_SPEC_NORMALIZED_HASH_MISMATCH")
    checked: list[dict[str, Any]] = []
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise RuntimeError("M3B_R2_SOURCE_MANIFEST_EMPTY")
    for item in sources:
        if not isinstance(item, dict):
            raise RuntimeError("M3B_R2_SOURCE_MANIFEST_ENTRY_INVALID")
        source_path = Path(str(item.get("path", "")))
        resolved = source_path if source_path.is_absolute() else repository_root / source_path
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            raise RuntimeError("M3B_R2_SOURCE_MISSING:" + str(source_path)) from exc
        actual = _sha256(raw)
        if actual != item.get("sha256"):
            raise RuntimeError("M3B_R2_SOURCE_HASH_MISMATCH:" + str(source_path))
        checked.append(
            {
                "path": str(source_path),
                "resolved_path": str(resolved.resolve()),
                "bytes": len(raw),
                "sha256": actual,
                "role": item.get("role"),
            }
        )
    return {
        "schema_version": SOURCE_GATE_SCHEMA_VERSION,
        "status": "PASS_SOURCE_MANIFEST_GATE",
        "run_id": manifest.get("run_id"),
        "source_count": len(checked),
        "run_spec_normalized_identity": {
            "path": str(run_spec_path),
            "resolved_path": str(resolved_run_spec.resolve()),
            "ignored_runtime_mutable_fields": ["run_authorized"],
            "canonical_sha256": normalized_run_spec_sha256,
        },
        "sources": checked,
    }


_OLD_RUNTIME_SCHEDULE_BLOCK = '''        self._capture_plan = json.loads(
            Path(os.environ["DRIVECLARIFY_MB_CAPTURE_PLAN"]).read_text(encoding="utf-8")
        )
        self._candidate_order = tuple(self._capture_plan["candidate_schedule"]["candidate_order"])
        if len(self._candidate_order) != 6 or {item[0] for item in self._candidate_order} != {"A", "B"}:
            raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_INVALID")
        if sorted(item for item in self._candidate_order if item[0] == "A") != ["A1", "A2", "A3"]:
            raise RuntimeError("MANEUVER_BRANCH_A_SCHEDULE_INVALID")
        if sorted(item for item in self._candidate_order if item[0] == "B") != ["B1", "B2", "B3"]:
            raise RuntimeError("MANEUVER_BRANCH_B_SCHEDULE_INVALID")
        schedule = with_hash(
            {
                "schema_version": "driveclarify.maneuver_branch.candidate_schedule.v1",
                "run_id": self._p0a_run_id,
                "candidate_order": list(self._candidate_order),
                "randomization": copy.deepcopy(self._capture_plan["candidate_schedule"]["randomization"]),
                "frozen_before_runtime": True,
                "candidate_outputs_read": False,
            }
        )
        if schedule["evidence_sha256"] != self._capture_plan["candidate_schedule"]["schedule_sha256"]:
            raise RuntimeError("MANEUVER_BRANCH_SCHEDULE_HASH_MISMATCH")
        atomic_write_json(Path(os.environ["DRIVECLARIFY_MB_CANDIDATE_SCHEDULE"]), schedule)
        self._schedule = schedule
'''

_NEW_RUNTIME_SCHEDULE_BLOCK = '''        self._capture_plan = json.loads(
            Path(os.environ["DRIVECLARIFY_MB_CAPTURE_PLAN"]).read_text(encoding="utf-8")
        )
        schedule_contract = self._capture_plan["candidate_schedule"]
        schedule_validation = load_and_validate_runtime_candidate_schedule(
            schedule_path=Path(os.environ["DRIVECLARIFY_MB_CANDIDATE_SCHEDULE"]),
            expected_run_id=self._p0a_run_id,
            expected_authority_sha256=schedule_contract["canonical_payload_sha256"],
            expected_semantic_sha256=schedule_contract["semantic_payload_sha256"],
            expected_raw_file_sha256=schedule_contract["raw_file_sha256"],
            expected_candidate_order=schedule_contract["candidate_order"],
            expected_randomization=schedule_contract["randomization"],
        )
        self._schedule = schedule_validation["schedule"]
        self._candidate_order = tuple(self._schedule["candidate_order"])
'''


def prepare_r2_runtime_schedule_loader_source(source: str) -> str:
    """Extract the V3 embedded gate into the shared read-only R2 loader."""

    if source.count(_OLD_RUNTIME_SCHEDULE_BLOCK) != 1:
        raise RuntimeError("M3B_R2_RUNTIME_SCHEDULE_BLOCK_PATTERN_MISMATCH")
    transformed = source.replace(
        _OLD_RUNTIME_SCHEDULE_BLOCK, _NEW_RUNTIME_SCHEDULE_BLOCK, 1
    )
    import_marker = (
        'CAPTURE_PROTOCOL = "DRIVECLARIFY_M3B_SINGLE_REAL_PLAN_SEMANTIC_MAPPING_PILOT_V1"'
    )
    runtime_import = (
        "from driveclarify_real_mapping_v1.candidate_schedule_identity import (  # noqa: E402\n"
        "    load_and_validate_runtime_candidate_schedule,\n"
        ")\n\n\n"
    )
    if transformed.count(import_marker) != 1:
        raise RuntimeError("M3B_R2_RUNTIME_IMPORT_MARKER_MISMATCH")
    return transformed.replace(import_marker, runtime_import + import_marker, 1)


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("schedule-gate", "source-manifest"))
    parser.add_argument("--package-dir", required=True)
    args = parser.parse_args()
    if args.mode == "schedule-gate":
        result = validate_exact_runtime_schedule_gate(args.package_dir)
    else:
        result = verify_source_manifest(args.package_dir)
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
