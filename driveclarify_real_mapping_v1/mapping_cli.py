"""CPU-only post-capture CLI for prepared M3B artifacts."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_consequence.maneuver_branch import validate_frozen_package

from .maneuver_branch_mapper_v1 import ManeuverBranchPlanMapperV1, transform_point


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _branch_polylines_ego(package: Mapping[str, Any]) -> dict[str, list[list[float]]]:
    matrix = package["coordinate_contract"]["world_to_ego"]
    result: dict[str, list[list[float]]] = {}
    role_names = {"STRAIGHT_LANE_FOLLOW": "STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH": "RIGHT_TURN_BRANCH"}
    for branch in package["branch_evidence"]["branches"]:
        name = role_names.get(branch.get("semantic_role"))
        if name is None:
            continue
        result[name] = [list(transform_point(matrix, point[:2])) for point in branch["centerline_world_xyz"]]
    return result


def map_captured_candidates(package: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    package_errors = validate_frozen_package(package)
    m3b_contract = package.get("m3b_plan_semantic_mapping_contract")
    if not isinstance(m3b_contract, Mapping):
        package_errors = tuple((*package_errors, "M3B_MAPPING_CONTRACT_MISSING"))
    branches = {} if package_errors else _branch_polylines_ego(package)
    mapper = ManeuverBranchPlanMapperV1()
    mappings = []
    for candidate in candidates:
        if package_errors:
            mapped = mapper._unknown(("UPSTREAM_FROZEN_EVIDENCE_INVALID", *package_errors))
        else:
            mapped = mapper.map_plan(
                candidate.get("raw_route", ()),
                plan_frame=str(candidate.get("plan_semantic_frame", "")),
                plan_unit=str(candidate.get("plan_semantic_unit", "")),
                branch_polylines_ego=branches,
                mapping_provenance=(
                    str(package.get("package_sha256", "")),
                    str(candidate.get("evidence_sha256", "")),
                    "M3B_SAME_OBSERVATION_ACTOR_FRAME_PROJECTION",
                ),
            )
        mappings.append({"candidate_id": candidate.get("candidate_id"), "mapping": mapped})
    return {
        "schema_version": "driveclarify.m3b.mapping_output.v1",
        "run_id": package.get("run_id"),
        "upstream_evidence_status": "PASS" if not package_errors else "FAIL_CLOSED",
        "upstream_reason_codes": list(package_errors),
        "mappings": mappings,
        "control_authorized": False,
        "physical_safety_inference": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-package", required=True)
    parser.add_argument("--candidate", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    package = json.loads(Path(args.frozen_package).read_text(encoding="utf-8"))
    candidates = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.candidate]
    _write_json(Path(args.output), map_captured_candidates(package, candidates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
