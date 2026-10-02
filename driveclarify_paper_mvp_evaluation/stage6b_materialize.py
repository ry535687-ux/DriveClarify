"""Materialize the frozen Stage 6B schedule from Stage 5/6A evidence.

This module is evaluator-side only.  It verifies the immutable scenario
catalog and the compiled Stage 6A runtime fixtures, then presents the minimal
identity view required by :mod:`driveclarify_paper_mvp_evaluation.scheduler`.
It never projects evaluator-private labels into a policy payload.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import ContractError
from .hashing import canonical_sha256, file_sha256
from .scheduler import EvaluationLifecycle, build_hash_bound_schedule


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"STAGE6B_JSON_READ_FAILED:{path}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"STAGE6B_JSON_OBJECT_REQUIRED:{path}")
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_stage6b_catalog_view(
    *,
    catalog_path: Path,
    stage6a_manifest_path: Path,
    hash_manifest_path: Path,
    generated_root: Path,
) -> dict[str, Any]:
    """Return a scheduler-compatible view after complete hash verification."""

    catalog = _load_object(catalog_path)
    manifest = _load_object(stage6a_manifest_path)
    hash_manifest = _load_object(hash_manifest_path)

    frozen_identity = manifest.get("frozen_identity")
    if not isinstance(frozen_identity, Mapping):
        raise ContractError("STAGE6A_FROZEN_IDENTITY_REQUIRED")
    actual_catalog_sha = file_sha256(catalog_path)
    if frozen_identity.get("catalog_file_sha256") != actual_catalog_sha:
        raise ContractError("STAGE5_CATALOG_FILE_HASH_MISMATCH")
    if frozen_identity.get("scenario_payload_sha256") != catalog.get(
        "scenario_payload_sha256"
    ):
        raise ContractError("STAGE5_SCENARIO_PAYLOAD_HASH_MISMATCH")

    records = manifest.get("records")
    scenarios = catalog.get("scenarios")
    artifacts = hash_manifest.get("artifacts")
    if not isinstance(records, list) or len(records) != 24:
        raise ContractError("EXACTLY_24_STAGE6A_MANIFEST_RECORDS_REQUIRED")
    if not isinstance(scenarios, list) or len(scenarios) != 24:
        raise ContractError("EXACTLY_24_STAGE5_SCENARIOS_REQUIRED")
    if not isinstance(artifacts, Mapping):
        raise ContractError("STAGE6A_HASH_MANIFEST_ARTIFACTS_REQUIRED")

    by_scenario: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ContractError("STAGE6A_MANIFEST_RECORD_OBJECT_REQUIRED")
        scenario_id = record.get("scenario_id")
        if not isinstance(scenario_id, str) or scenario_id in by_scenario:
            raise ContractError("STAGE6A_MANIFEST_SCENARIO_ID_INVALID_OR_DUPLICATE")
        by_scenario[scenario_id] = record

    adapted: list[dict[str, Any]] = []
    runtime_bindings: list[dict[str, Any]] = []
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            raise ContractError("STAGE5_SCENARIO_OBJECT_REQUIRED")
        scenario_id = scenario.get("scenario_id")
        record = by_scenario.get(str(scenario_id))
        if record is None:
            raise ContractError(f"STAGE6A_RUNTIME_BINDING_MISSING:{scenario_id}")
        for key in ("split", "seed"):
            if record.get(key) != scenario.get(key):
                raise ContractError(f"STAGE6A_RUNTIME_BINDING_{key.upper()}_MISMATCH")

        runtime_relative = record.get("runtime_manifest_path")
        route_relative = record.get("derived_route_path")
        if not isinstance(runtime_relative, str) or not isinstance(route_relative, str):
            raise ContractError("STAGE6A_RUNTIME_PATH_BINDING_REQUIRED")
        runtime_path = generated_root / runtime_relative
        route_path = generated_root / route_relative
        runtime_sha = file_sha256(runtime_path)
        route_sha = file_sha256(route_path)
        if runtime_sha != record.get("runtime_manifest_sha256"):
            raise ContractError(f"RUNTIME_MANIFEST_HASH_MISMATCH:{scenario_id}")
        if route_sha != record.get("derived_route_sha256"):
            raise ContractError(f"DERIVED_ROUTE_HASH_MISMATCH:{scenario_id}")
        for relative, observed in (
            (runtime_relative, runtime_sha),
            (route_relative, route_sha),
        ):
            indexed = artifacts.get(relative)
            if not isinstance(indexed, Mapping) or indexed.get("sha256") != observed:
                raise ContractError(f"HASH_MANIFEST_BINDING_MISMATCH:{relative}")

        executable_fixture_sha = canonical_sha256(
            {
                "runtime_fixture_id": record.get("runtime_fixture_id"),
                "runtime_manifest_sha256": runtime_sha,
                "derived_route_sha256": route_sha,
            }
        )
        instruction = scenario.get("instruction_text")
        if not isinstance(instruction, str) or not instruction:
            raise ContractError(f"STAGE5_INSTRUCTION_REQUIRED:{scenario_id}")
        adapted.append(
            {
                "scenario_id": scenario_id,
                "split": scenario.get("split"),
                "seed": scenario.get("seed"),
                "route_id": scenario.get("route_id"),
                "town": scenario.get("town"),
                "executable_fixture_sha256": executable_fixture_sha,
                "instruction_sha256": _sha256_text(instruction),
            }
        )
        runtime_bindings.append(
            {
                "scenario_id": scenario_id,
                "runtime_fixture_id": record.get("runtime_fixture_id"),
                "runtime_manifest_path": runtime_relative,
                "runtime_manifest_sha256": runtime_sha,
                "derived_route_path": route_relative,
                "derived_route_sha256": route_sha,
                "executable_fixture_sha256": executable_fixture_sha,
            }
        )

    if set(by_scenario) != {str(item["scenario_id"]) for item in scenarios}:
        raise ContractError("STAGE5_STAGE6A_SCENARIO_SET_MISMATCH")
    catalog_view = {
        "scenario_payload_sha256": catalog["scenario_payload_sha256"],
        "scenarios": adapted,
    }
    return {
        "schema_version": "driveclarify.paper_mvp_stage6b_catalog_view.v1",
        "source_catalog_file_sha256": actual_catalog_sha,
        "source_scenario_payload_sha256": catalog["scenario_payload_sha256"],
        "stage6a_manifest_file_sha256": file_sha256(stage6a_manifest_path),
        "stage6a_hash_manifest_file_sha256": file_sha256(hash_manifest_path),
        "runtime_bindings": sorted(
            runtime_bindings, key=lambda item: str(item["scenario_id"])
        ),
        "policy_annotation_projection_count": 0,
        "catalog_view": catalog_view,
        "catalog_view_sha256": canonical_sha256(catalog_view),
    }


def materialize_stage6b_schedule(
    *,
    catalog_path: Path,
    stage6a_manifest_path: Path,
    hash_manifest_path: Path,
    generated_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], EvaluationLifecycle]:
    """Build the catalog evidence, 768-slot schedule, and initial seal state."""

    evidence = build_stage6b_catalog_view(
        catalog_path=catalog_path,
        stage6a_manifest_path=stage6a_manifest_path,
        hash_manifest_path=hash_manifest_path,
        generated_root=generated_root,
    )
    schedule = build_hash_bound_schedule(evidence["catalog_view"])
    lifecycle = EvaluationLifecycle(
        schedule_sha256=schedule["schedule_sha256"],
        baseline_freeze_sha256=schedule["baseline_freeze_sha256"],
    )
    return evidence, schedule, lifecycle
