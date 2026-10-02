"""在新报告树中重建 prospective TEST 的静态构建 authority。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from driveclarify_static_branch.m1_expansion_v3 import (
    build_opendrive_generated_fixture,
    eligibility_contract,
    extended_mapper_compatibility,
    generated_topology,
    source_provenance,
    static_route_parser_compatibility,
)
from driveclarify_static_branch.topology import sha256_file, verify_sha256

from .contracts import digest
from .manifest import ManifestError, read_json

AUTHORITY_FILES = (
    "SCENARIO_FREE_ROUTE.xml",
    "BRANCH_TOPOLOGY_CONSTRUCTION_AUTHORITY.json",
    "OBSERVATION_ELIGIBILITY_CONTRACT.json",
    "MAPPER_COMPATIBILITY.json",
    "SOURCE_PROVENANCE.json",
)


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def _verify_existing(directory: Path, expected_unit_id: str) -> dict[str, Any]:
    manifest = read_json(directory / "UNIT_AUTHORITY_MANIFEST.json")
    if manifest.get("unit_id") != expected_unit_id or manifest.get("sha256") != digest({key: value for key, value in manifest.items() if key != "sha256"}):
        raise ManifestError("UNIT_AUTHORITY_MANIFEST_INVALID:" + expected_unit_id)
    for row in manifest.get("artifacts", []):
        path = directory / row["name"]
        if not path.is_file() or sha256_file(path) != row["sha256"]:
            raise ManifestError("UNIT_AUTHORITY_ARTIFACT_MISMATCH:" + expected_unit_id)
    return manifest


def build_units(split_path: Path, shortlist_path: Path, threshold_path: Path, output_root: Path) -> dict[str, Any]:
    split = read_json(split_path)
    shortlist = read_json(shortlist_path)
    thresholds = read_json(threshold_path)
    if not verify_sha256(thresholds):
        raise ManifestError("THRESHOLD_EMBEDDED_HASH_INVALID")
    source = {row["unit_id"]: row for row in shortlist.get("candidates", [])}
    selected = split.get("test", [])
    if len(selected) != 24 or any(row.get("qualification_status") != "PENDING_FAMILY_QUALIFICATION" for row in selected):
        raise ManifestError("TEST_CANDIDATE_MANIFEST_INVALID")
    output_root.mkdir(parents=True, exist_ok=True)
    manifests = []
    for selection in selected:
        unit_id = selection["unit_id"]
        candidate = source.get(unit_id)
        if candidate is None:
            raise ManifestError("SHORTLIST_CANDIDATE_MISSING:" + unit_id)
        directory = output_root / unit_id
        if directory.exists():
            manifests.append(_verify_existing(directory, unit_id)); continue
        temporary = output_root / ("." + unit_id + ".tmp")
        if temporary.exists():
            raise ManifestError("STALE_UNIT_TEMPORARY:" + unit_id)
        temporary.mkdir()
        fixture_path = temporary / "SCENARIO_FREE_ROUTE.xml"
        fixture_text, route_start = build_opendrive_generated_fixture(candidate)
        _write(fixture_path, fixture_text)
        parser = static_route_parser_compatibility(fixture_path)
        topology = generated_topology(candidate, fixture_path)
        eligibility = eligibility_contract(topology, route_start)
        mapper = extended_mapper_compatibility(topology, thresholds, candidate["map_path"], candidate["binding"])
        provenance = source_provenance(candidate, fixture_path)
        if any(parser.get(key) for key in ("scenario_count", "actor_count", "trigger_count")):
            raise ManifestError("PROSPECTIVE_ROUTE_NOT_SCENARIO_FREE:" + unit_id)
        values = {
            "BRANCH_TOPOLOGY_CONSTRUCTION_AUTHORITY.json": topology,
            "OBSERVATION_ELIGIBILITY_CONTRACT.json": eligibility,
            "MAPPER_COMPATIBILITY.json": mapper,
            "SOURCE_PROVENANCE.json": provenance,
        }
        for name, value in values.items():
            _write_json(temporary / name, value)
        artifacts = [{"name": name, "bytes": (temporary / name).stat().st_size, "sha256": sha256_file(temporary / name)} for name in AUTHORITY_FILES]
        manifest = {
            "schema_version": "driveclarify.rq1_prospective_unit_authority.v1",
            "unit_id": unit_id,
            "town": candidate["town"],
            "junction_id": candidate["junction_id"],
            "route_id": candidate["route_id"],
            "qualification_status": "PENDING_FAMILY_QUALIFICATION",
            "method_input_exported": False,
            "candidate_text_created": False,
            "relation_label_read": False,
            "construction_authority_not_method_input": True,
            "route_parser_status": parser["status"],
            "topology_sha256": topology["sha256"],
            "eligibility_sha256": eligibility["sha256"],
            "mapper_sha256": mapper["sha256"],
            "artifacts": artifacts,
        }
        manifest["sha256"] = digest(manifest)
        _write_json(temporary / "UNIT_AUTHORITY_MANIFEST.json", manifest)
        os.replace(temporary, directory)
        manifests.append(manifest)
    summary = {
        "schema_version": "driveclarify.rq1_prospective_authority_summary.v1",
        "status": "STATIC_AUTHORITY_REBUILT_PENDING_FAMILY_QUALIFICATION",
        "unit_count": len(manifests),
        "all_scenario_free": len(manifests) == 24,
        "method_input_export_count": 0,
        "relation_label_read": False,
        "units": [{"unit_id": row["unit_id"], "manifest_sha256": row["sha256"], "topology_sha256": row["topology_sha256"]} for row in manifests],
    }
    summary["sha256"] = digest(summary)
    return summary
