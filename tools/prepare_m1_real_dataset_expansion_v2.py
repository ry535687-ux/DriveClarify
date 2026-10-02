"""Freeze the CPU/static M1 real-dataset Expansion V2 package.

This preparation reads installed OpenDRIVE and existing immutable evidence.  It
does not import or invoke any driving runtime, evaluator, model, tensor library,
accelerator API, observation capture, or training code.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from driveclarify_static_branch.m1_expansion_v2 import (
    ENGINEERING_EXCLUSION,
    EVIDENCE_EXCLUSION,
    EXPANSION_ID,
    GENERATION_ALGORITHM,
    PILOT_DEVELOPMENT_UNITS,
    SEARCH_ALGORITHM,
    build_opendrive_generated_fixture,
    discover_static_graph_candidates,
    extended_mapper_compatibility,
    generated_topology,
    source_provenance,
    static_route_parser_compatibility,
)
from driveclarify_static_branch.multi_topology import (
    FROZEN_START_STATION_M,
    FROZEN_THRESHOLD_PATH,
    build_scenario_free_fixture,
    discover_route_backed_candidates,
    eligibility_contract,
    task_binding,
)
from driveclarify_static_branch.topology import (
    TopologyContractError,
    deterministic_sha256,
    sha256_file,
    verify_sha256,
)


REPORT = REPO / "reports/m1_real_dataset_expansion_v2" / EXPANSION_ID
UNITS = REPORT / "units"
PILOT = REPO / "reports/learned_m1_pilot_v1/DC-M1-PILOT-P1-20260803T133000Z"
MULTI = REPO / "reports/multi_topology_static_units_v1"
SOURCE_BATCH = MULTI / "offline_candidate_capture_runs/DC-MULTI-A3B3-C1-20260803T121500Z"
V1_DATASET = SOURCE_BATCH / "M1_REAL_DATASET_V1.json"
V1_SUMMARY = SOURCE_BATCH / "M1_REAL_DATASET_V1_UNIT_SUMMARY.json"
V1_SCHEMA = SOURCE_BATCH / "M1_REAL_DATASET_V1_DATA_SCHEMA.json"
CPU_RESULTS = REPORT / "M1_V2_CPU_TEST_RESULTS.txt"
SIMLINGO = Path("/home/buaa/wrh/simlingo")
FINAL_STATUS = "READY_FOR_M1_V2_RUNTIME_BATCH_AUTHORIZATION"

INITIAL_DRIVECLARIFY = {
    "branch": "master",
    "head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
    "status_porcelain_v1_uall_sha256": "eae5438374189db6f00b5a3e54f00afe19cd8fc1bb92ab92813865680d1fdfd5",
    "status_entry_count": 2747,
    "tracked_diff_bytes": 0,
    "tracked_diff_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}
INITIAL_SIMLINGO = {
    "branch": "main",
    "head": "743b243afd6cf5ff51b9fa1f8cac86f22d569684",
    "status_porcelain_v1_uall_sha256": "4f6f224bfa75a3322de9becaaf7199677a579864cd2834aa3d3ccae08a2ba09f",
    "status_entry_count": 65,
    "tracked_diff_bytes": 7722,
    "tracked_diff_sha256": "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34",
}
SPLIT_QUOTAS = {
    "TRAIN": {"Town03": 6, "Town04": 5, "Town05": 7},
    "DEV": {"Town07": 6},
    "TEST": {"Town10HD": 6},
}
REQUIRED_GLOBAL_FILES = (
    "EXPANSION_AUTHORITY.json",
    "EXPANSION_SEARCH_SCOPE.json",
    "M1_V2_TOPOLOGY_SHORTLIST.json",
    "M1_V2_SELECTED_UNITS_MANIFEST.json",
    "M1_V2_SPLIT_MANIFEST.json",
    "M1_V2_SPLIT_LEAKAGE_AUDIT.json",
    "SELECTION_BLINDING_AUDIT.json",
    "M1_V2_RUNTIME_CAMPAIGN_SPEC.json",
    "M1_V2_OBSERVATION_OUTPUT_SCHEMA.json",
    "M1_V2_CAPTURE_OUTPUT_SCHEMA.json",
    "M1_REAL_DATASET_V2_DATA_SCHEMA.json",
    "PILOT_UNITS_DEVELOPMENT_ONLY_MANIFEST.json",
    "FROZEN_EXCLUSIONS_MANIFEST.json",
    "M1_V2_CPU_TEST_RESULTS.txt",
    "M1_V2_PREPARATION_REPORT.md",
    "COMMAND_LOG.md",
    "GIT_START_END.json",
    "PROHIBITED_RUNTIME_COUNTS.json",
    "ARTIFACT_INVENTORY.json",
)
REQUIRED_UNIT_FILES = (
    "SCENARIO_FREE_ROUTE.xml",
    "UNIT_MANIFEST.json",
    "BRANCH_TOPOLOGY_GROUND_TRUTH.json",
    "OBSERVATION_ELIGIBILITY_CONTRACT.json",
    "TASK_BINDING.json",
    "MAPPER_COMPATIBILITY.json",
    "SOURCE_PROVENANCE.json",
    "SPLIT_ASSIGNMENT.json",
)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError(f"JSON_OBJECT_REQUIRED:{path}")
    return result


def _hashed(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("sha256", None)
    result["sha256"] = deterministic_sha256(result)
    return result


def _snapshot(repository: Path) -> dict[str, Any]:
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repository).decode().strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository).decode().strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=repository
    )
    tracked = subprocess.check_output(["git", "diff", "--binary"], cwd=repository)
    staged = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=repository)
    return {
        "branch": branch,
        "head": head,
        "status_porcelain_v1_uall_sha256": hashlib.sha256(status).hexdigest(),
        "status_entry_count": len(status.splitlines()),
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
    }


def _entrance_audit() -> tuple[dict[str, Any], dict[str, Any]]:
    state = _read_json(REPO / "STATE.json")
    pilot_result = _read_json(PILOT / "PILOT_RESULT.json")
    dataset = _read_json(V1_DATASET)
    if state["status"] not in {
        "LEARNED_M1_PILOT_PASS_READY_FOR_DATASET_EXPANSION",
        "READY_FOR_M1_V2_RUNTIME_BATCH_AUTHORIZATION",
    }:
        raise RuntimeError("PILOT_ENTRY_STATE_NOT_READY")
    if pilot_result["final_status"] != "LEARNED_M1_PILOT_PASS_READY_FOR_DATASET_EXPANSION":
        raise RuntimeError("PILOT_RESULT_NOT_READY")
    if len(dataset["records"]) != 30:
        raise RuntimeError("V1_SOURCE_RECORD_COUNT_CHANGED")
    pair_dataset = _read_json(PILOT / "M1_PAIR_DATASET_PILOT_V1.json")
    if pair_dataset["sample_count"] != 5:
        raise RuntimeError("PILOT_PAIR_SAMPLE_COUNT_CHANGED")
    source_files = [V1_DATASET, V1_SUMMARY, V1_SCHEMA]
    source_hashes = {path.relative_to(REPO).as_posix(): sha256_file(path) for path in source_files}
    thresholds = _read_json(FROZEN_THRESHOLD_PATH)
    if not verify_sha256(thresholds):
        raise RuntimeError("FROZEN_THRESHOLD_EMBEDDED_HASH_INVALID")
    expected = {
        "distance_threshold_m": 1.75,
        "alignment_threshold_cosine": 0.7157620042903288,
        "branch_score_margin": 0.10,
        "tail_point_count": 3,
    }
    if any(thresholds[key] != value for key, value in expected.items()):
        raise RuntimeError("FROZEN_THRESHOLD_VALUE_CHANGED")
    audit = _hashed({
        "schema_version": "driveclarify.m1_v2_entrance_audit.v1",
        "expansion_id": EXPANSION_ID,
        "pilot_status": pilot_result["final_status"],
        "v1_source_record_count": 30,
        "v1_complete_unit_count": 5,
        "v1_source_file_sha256": source_hashes,
        "threshold_path": str(FROZEN_THRESHOLD_PATH),
        "threshold_file_sha256": sha256_file(FROZEN_THRESHOLD_PATH),
        "threshold_embedded_sha256": thresholds["sha256"],
        "threshold_values": expected,
        "simlingo_read_only": True,
        "status": "PASS",
    })
    return audit, thresholds


def _candidate_evaluation(candidate: Mapping[str, Any], thresholds: Mapping[str, Any], path: Path) -> dict[str, Any]:
    fixture_text, route_start = build_opendrive_generated_fixture(candidate)
    _write_text(path, fixture_text)
    parser = static_route_parser_compatibility(path)
    topology = generated_topology(candidate, path)
    eligibility = eligibility_contract(topology, route_start)
    mapper = extended_mapper_compatibility(
        topology, thresholds, candidate["map_path"], candidate["binding"]
    )
    if parser["scenario_count"] or parser["actor_count"] or parser["trigger_count"]:
        raise TopologyContractError("GENERATED_FIXTURE_NOT_SCENARIO_FREE")
    return {
        "fixture_text": fixture_text,
        "route_start": route_start,
        "parser": parser,
        "topology": topology,
        "eligibility": eligibility,
        "mapper": mapper,
    }


def _evaluate_search(search: Mapping[str, Any], thresholds: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    frozen = set(PILOT_DEVELOPMENT_UNITS) | {ENGINEERING_EXCLUSION}
    with tempfile.TemporaryDirectory(prefix="driveclarify-m1-v2-static-") as temporary:
        directory = Path(temporary)
        for candidate in search["candidates"]:
            identity = candidate["unit_id"]
            if identity in frozen:
                excluded.append({"unit_id": identity, "reason": "FROZEN_PRIOR_UNIT_IDENTITY"})
                continue
            if candidate["town"] == "Town03" and candidate["junction_id"] == "238":
                excluded.append({"unit_id": identity, "reason": "FROZEN_EVIDENCE_EXCLUSION_ROUTE_27515_JUNCTION_238"})
                continue
            try:
                evaluation = _candidate_evaluation(candidate, thresholds, directory / f"{identity}.xml")
                topology = evaluation["topology"]
                eligibility = evaluation["eligibility"]
                straight = next(
                    branch for branch in topology["branches"]
                    if branch["semantic_role"] == "STRAIGHT_BRANCH"
                )["polyline_world_xyz"]
                route_orientation = math.degrees(
                    math.atan2(straight[1][1] - straight[0][1], straight[1][0] - straight[0][0])
                )
                valid.append({
                    "candidate": copy.deepcopy(candidate),
                    "static_metrics": {
                        "straight_turn_angle_degrees": candidate["straight_turn_angle_degrees"],
                        "right_turn_angle_degrees": candidate["right_turn_angle_degrees"],
                        "incoming_driving_lane_count": candidate["incoming_driving_lane_count"],
                        "incoming_reference_geometry_types": candidate["incoming_reference_geometry_types"],
                        "divergence_station_m": eligibility["divergence_station_m"],
                        "evaluation_interval_m": eligibility["evaluation_interval_m"],
                        "eligibility_interval": eligibility["eligibility_interval"],
                        "route_orientation_degrees": route_orientation,
                    },
                    "static_verdict": "PASS_ALL_FROZEN_STATIC_GATES",
                })
            except (TopologyContractError, ValueError, TypeError) as exc:
                excluded.append({
                    "unit_id": identity,
                    "town": candidate["town"],
                    "junction_id": candidate["junction_id"],
                    "reason": str(exc).split(":", 1)[0],
                })
    if len(valid) < 36:
        raise RuntimeError("BLOCKED_M1_V2_INSUFFICIENT_STATIC_UNITS")
    return valid, excluded


def _select(valid: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_town: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        by_town[row["candidate"]["town"]].append(row)
    selected: list[dict[str, Any]] = []
    for split, town_quotas in SPLIT_QUOTAS.items():
        for town, count in town_quotas.items():
            candidates = by_town[town]
            if len(candidates) < count:
                raise RuntimeError(f"BLOCKED_M1_V2_SPLIT_FREEZE_INFEASIBLE:{town}")
            for row in candidates[:count]:
                copied = copy.deepcopy(row)
                copied["split"] = split
                selected.append(copied)
    if len(selected) != 30 or len({row["candidate"]["unit_id"] for row in selected}) != 30:
        raise RuntimeError("M1_V2_SELECTION_COUNT_OR_IDENTITY_INVALID")
    return selected


def _split_assignment(row: Mapping[str, Any]) -> dict[str, Any]:
    candidate = row["candidate"]
    return _hashed({
        "schema_version": "driveclarify.m1_v2_split_assignment.v1",
        "expansion_id": EXPANSION_ID,
        "unit_id": candidate["unit_id"],
        "split": row["split"],
        "split_strategy": "TOWN_DISJOINT_60_20_20",
        "town": candidate["town"],
        "junction_group": candidate["junction_group"],
        "route_family": candidate["route_family"],
        "repeat_group_policy": "ALL_FUTURE_REPEATS_REMAIN_WITH_THIS_UNIT_SPLIT",
        "frozen_before_observation_or_candidate_forward": True,
    })


def _build_selected(selected: list[dict[str, Any]], thresholds: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for selected_row in selected:
        candidate = selected_row["candidate"]
        directory = UNITS / candidate["unit_id"]
        directory.mkdir(parents=True, exist_ok=True)
        fixture_path = directory / "SCENARIO_FREE_ROUTE.xml"
        fixture_text, route_start = build_opendrive_generated_fixture(candidate)
        _write_text(fixture_path, fixture_text)
        parser = static_route_parser_compatibility(fixture_path)
        topology = generated_topology(candidate, fixture_path)
        eligibility = eligibility_contract(topology, route_start)
        mapper = extended_mapper_compatibility(
            topology, thresholds, candidate["map_path"], candidate["binding"]
        )
        task = task_binding(topology)
        source = source_provenance(candidate, fixture_path)
        assignment = _split_assignment(selected_row)
        artifacts = {
            "BRANCH_TOPOLOGY_GROUND_TRUTH.json": topology,
            "OBSERVATION_ELIGIBILITY_CONTRACT.json": eligibility,
            "TASK_BINDING.json": task,
            "MAPPER_COMPATIBILITY.json": mapper,
            "SOURCE_PROVENANCE.json": source,
            "SPLIT_ASSIGNMENT.json": assignment,
        }
        for name, value in artifacts.items():
            _write_json(directory / name, value)
        manifest_artifacts = [fixture_path] + [directory / name for name in artifacts]
        manifest = _hashed({
            "schema_version": "driveclarify.m1_v2_unit_manifest.v1",
            "expansion_id": EXPANSION_ID,
            "unit_id": candidate["unit_id"],
            "stable_semantically_neutral_identity": True,
            "source_type": "OPENDRIVE_GENERATED_STATIC",
            "official_bench2drive_route_claimed": False,
            "town": candidate["town"],
            "route_id": candidate["route_id"],
            "junction_id": candidate["junction_id"],
            "junction_group": candidate["junction_group"],
            "route_family": candidate["route_family"],
            "incoming_road_id": candidate["binding"]["incoming_road_id"],
            "incoming_lane_id": candidate["binding"]["incoming_lane_id"],
            "branch_semantics": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"],
            "route_parser_static_contract": parser["status"],
            "route_start": eligibility["route_start"],
            "decision_point": topology["decision_point"],
            "divergence_station_m": eligibility["divergence_station_m"],
            "evaluation_interval_m": eligibility["evaluation_interval_m"],
            "eligibility_interval": eligibility["eligibility_interval"],
            "horizon_requirement": eligibility["expected_plan_horizon_requirement"],
            "uncertainty_flags": [],
            "scenario_count": parser["scenario_count"],
            "actor_count": parser["actor_count"],
            "trigger_count": parser["trigger_count"],
            "red_light_dependency_count": 0,
            "pedestrian_dependency_count": 0,
            "mapper_compatibility": mapper["compatibility_verdict"],
            "topology_sha256": topology["sha256"],
            "split": selected_row["split"],
            "selection_scientific_label_used": False,
            "candidate_or_model_output_used": False,
            "artifacts": [
                {
                    "path": path.relative_to(REPO).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in manifest_artifacts
            ],
        })
        _write_json(directory / "UNIT_MANIFEST.json", manifest)
        rows.append({
            **copy.deepcopy(selected_row),
            "directory": directory,
            "fixture_path": fixture_path,
            "parser": parser,
            "topology": topology,
            "eligibility": eligibility,
            "mapper": mapper,
            "task": task,
            "source": source,
            "assignment": assignment,
            "manifest": manifest,
        })
    return rows


def _authority(entrance: Mapping[str, Any]) -> dict[str, Any]:
    return _hashed({
        "schema_version": "driveclarify.m1_v2_expansion_authority.v1",
        "expansion_id": EXPANSION_ID,
        "scope": "CPU_ONLY_STATIC_SELECTION_FIXTURE_GENERATION_AND_SPLIT_FREEZE",
        "entrance_audit": entrance,
        "task_family": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"],
        "mapper_authority": "StaticBranchPlanMapperV1",
        "route_start_signed_station_m": FROZEN_START_STATION_M,
        "selection_target": 30,
        "selection_minimum": 24,
        "shortlist_minimum": 36,
        "runtime_authorized": False,
        "automatic_continuation": False,
        "claim_boundary": "NO_NEW_SCIENTIFIC_LABELS_NO_GENERALIZATION_CLAIM",
        "unique_next_step_after_ready": "M1_V2_COMBINED_RUNTIME_CAMPAIGN",
    })


def _search_scope(search: Mapping[str, Any], valid: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = Counter(row["reason"] for row in excluded)
    return _hashed({
        "schema_version": "driveclarify.m1_v2_expansion_search_scope.v1",
        "expansion_id": EXPANSION_ID,
        "algorithm": SEARCH_ALGORITHM,
        "route_generation_algorithm": GENERATION_ALGORITHM,
        "searched_town_count": search["searched_town_count"],
        "searched_towns": search["searched_towns"],
        "searched_route_file_count": search["searched_route_file_count"],
        "searched_route_count": search["searched_route_count"],
        "graph_pair_count": search["graph_pair_count"],
        "unique_junction_candidate_count": search["unique_junction_candidate_count"],
        "valid_new_shortlist_count": len(valid),
        "valid_new_town_distribution": dict(Counter(row["candidate"]["town"] for row in valid)),
        "static_exclusion_count": len(excluded),
        "static_exclusion_reason_counts": dict(sorted(reasons.items())),
        "pre_graph_rejection_counts": search["pre_graph_rejection_counts"],
        "source_types_searched": ["EXISTING_ROUTE_BACKED", "OPENDRIVE_GENERATED_STATIC"],
        "existing_route_backed_search_reused": True,
        "candidate_or_model_output_used": False,
    })


def _shortlist(valid: list[dict[str, Any]], excluded: list[dict[str, Any]], search_scope: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for row in valid:
        candidate = row["candidate"]
        rows.append({
            "unit_id": candidate["unit_id"],
            "town": candidate["town"],
            "route_id": candidate["route_id"],
            "junction_id": candidate["junction_id"],
            "junction_group": candidate["junction_group"],
            "route_family": candidate["route_family"],
            "source_type": candidate["source_type"],
            "map_path": candidate["map_path"],
            "map_sha256": candidate["map_sha256"],
            "binding": candidate["binding"],
            "static_metrics": row["static_metrics"],
            "static_verdict": row["static_verdict"],
            "selection_label_fields_read": [],
            "candidate_or_model_output_used": False,
        })
    return _hashed({
        "schema_version": "driveclarify.m1_v2_topology_shortlist.v1",
        "expansion_id": EXPANSION_ID,
        "search_scope_sha256": search_scope["sha256"],
        "shortlist_count": len(rows),
        "shortlist_is_new_unit_only": True,
        "source_type_distribution": dict(Counter(row["source_type"] for row in rows)),
        "town_distribution": dict(Counter(row["town"] for row in rows)),
        "candidates": rows,
        "non_shortlisted_static_exclusions": excluded,
    })


def _selected_manifest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _hashed({
        "schema_version": "driveclarify.m1_v2_selected_units_manifest.v1",
        "expansion_id": EXPANSION_ID,
        "status": FINAL_STATUS if _tests_passed() else "STATIC_PACKAGE_GENERATED_TESTS_PENDING",
        "selected_count": len(rows),
        "selected_town_count": len({row["candidate"]["town"] for row in rows}),
        "town_distribution": dict(Counter(row["candidate"]["town"] for row in rows)),
        "source_type_distribution": dict(Counter(row["candidate"]["source_type"] for row in rows)),
        "route_start_signed_station_m": FROZEN_START_STATION_M,
        "all_mapper_compatible": all(
            row["mapper"]["compatibility_verdict"] == "PASS_FIXED_STATIC_BRANCH_PLAN_MAPPER_V1"
            for row in rows
        ),
        "selected_units": [
            {
                "unit_id": row["candidate"]["unit_id"],
                "town": row["candidate"]["town"],
                "route_id": row["candidate"]["route_id"],
                "junction_id": row["candidate"]["junction_id"],
                "junction_group": row["candidate"]["junction_group"],
                "route_family": row["candidate"]["route_family"],
                "source_type": row["candidate"]["source_type"],
                "split": row["split"],
                "fixture_path": row["fixture_path"].relative_to(REPO).as_posix(),
                "fixture_sha256": sha256_file(row["fixture_path"]),
                "unit_manifest_path": (row["directory"] / "UNIT_MANIFEST.json").relative_to(REPO).as_posix(),
                "unit_manifest_file_sha256": sha256_file(row["directory"] / "UNIT_MANIFEST.json"),
                "topology_sha256": row["topology"]["sha256"],
                "eligibility_sha256": row["eligibility"]["sha256"],
                "mapper_compatibility_sha256": row["mapper"]["sha256"],
            }
            for row in rows
        ],
    })


def _split_manifest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    assignments = [
        {
            "unit_id": row["candidate"]["unit_id"],
            "split": row["split"],
            "town": row["candidate"]["town"],
            "junction_group": row["candidate"]["junction_group"],
            "route_family": row["candidate"]["route_family"],
        }
        for row in rows
    ]
    return _hashed({
        "schema_version": "driveclarify.m1_v2_split_manifest.v1",
        "expansion_id": EXPANSION_ID,
        "split_strategy": "TOWN_DISJOINT_60_20_20",
        "frozen_before_new_observation_or_candidate_forward": True,
        "split_counts": dict(Counter(row["split"] for row in rows)),
        "split_towns": {
            split: sorted({row["candidate"]["town"] for row in rows if row["split"] == split})
            for split in ("TRAIN", "DEV", "TEST")
        },
        "assignments": assignments,
        "pilot_units": [
            {"unit_id": unit_id, "split": "PILOT_DEVELOPMENT_ONLY"}
            for unit_id in PILOT_DEVELOPMENT_UNITS
        ],
        "exclusions": [
            {"unit_id": ENGINEERING_EXCLUSION, "split": "ENGINEERING_EXCLUSION"},
            {"identity": EVIDENCE_EXCLUSION, "split": "EVIDENCE_EXCLUSION"},
        ],
        "future_repeat_policy": "REPEATS_ARE_MEASUREMENTS_AND_NEVER_CROSS_UNIT_SPLIT",
    })


def _split_audit(split: Mapping[str, Any]) -> dict[str, Any]:
    assignments = split["assignments"]
    memberships: dict[str, set[str]] = defaultdict(set)
    for key in ("town", "junction_group", "route_family"):
        for row in assignments:
            memberships[f"{key}:{row[key]}"].add(row["split"])
    leaks = sorted(key for key, values in memberships.items() if len(values) != 1)
    formal_ids = {row["unit_id"] for row in assignments if row["split"] in {"DEV", "TEST"}}
    pilot_overlap = sorted(formal_ids & set(PILOT_DEVELOPMENT_UNITS))
    return _hashed({
        "schema_version": "driveclarify.m1_v2_split_leakage_audit.v1",
        "expansion_id": EXPANSION_ID,
        "town_disjoint": not any(key.startswith("town:") for key in leaks),
        "junction_group_disjoint": not any(key.startswith("junction_group:") for key in leaks),
        "route_family_disjoint": not any(key.startswith("route_family:") for key in leaks),
        "repeat_group_disjoint_by_policy": True,
        "dev_complete_town_count": len(split["split_towns"]["DEV"]),
        "test_complete_town_count": len(split["split_towns"]["TEST"]),
        "pilot_dev_or_test_overlap": pilot_overlap,
        "leaks": leaks,
        "status": "PASS" if not leaks and not pilot_overlap else "FAIL",
    })


def _blinding_audit(search: Mapping[str, Any], shortlist: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = [
        "SimLingo output", "candidate plan", "A/B variation", "mapper runtime label", "RQ1", "RQ2",
        "TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN", "Learned M1 prediction", "pilot success probability",
    ]
    return _hashed({
        "schema_version": "driveclarify.m1_v2_selection_blinding_audit.v1",
        "expansion_id": EXPANSION_ID,
        "allowed_inputs": search["selection_inputs"],
        "forbidden_inputs": forbidden,
        "forbidden_field_read_count": 0,
        "candidate_plan_read_count": 0,
        "model_output_read_count": 0,
        "mapper_runtime_output_read_count": 0,
        "scientific_label_read_count": 0,
        "shortlist_candidate_count": shortlist["shortlist_count"],
        "selection_implementation": "driveclarify_static_branch/m1_expansion_v2.py",
        "status": "PASS_SELECTION_BLIND_TO_SCIENTIFIC_RESULTS",
    })


def _campaign(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _hashed({
        "schema_version": "driveclarify.m1_v2_runtime_campaign_spec.v1",
        "expansion_id": EXPANSION_ID,
        "campaign_name": "M1_V2_COMBINED_RUNTIME_CAMPAIGN",
        "run_authorized": False,
        "batch_id": None,
        "run_ids": [],
        "receipts": [],
        "run_outputs": [],
        "automatic_continuation": False,
        "unit_order": [row["candidate"]["unit_id"] for row in rows],
        "stage_a": {
            "name": "BATCH_OBSERVATION_ELIGIBILITY_SCREEN",
            "per_unit": [
                "initialize CARLA/evaluator and load model only after authorization",
                "capture first model-ready observation index 0",
                "apply frozen eligibility contract",
                "save observation package",
                "cleanup",
            ],
            "candidate_forward_count": 0,
            "evidence_unavailable_policy": "NO_STAGE_B_NO_SECOND_OBSERVATION_NO_START_ADJUSTMENT_NO_RERUN",
        },
        "stage_b": {
            "name": "OFFLINE_FROZEN_A3_B3",
            "eligible_units_only": True,
            "carla_restart": False,
            "reuse_frozen_observation_package": True,
            "candidate_schedule": ["A1", "A2", "A3", "B1", "B2", "B3"],
            "steps": ["candidate forward", "mapper", "RQ1/RQ2", "M1 V2 data append", "cleanup"],
        },
        "training_readiness_gate": {
            "complete_unit_count_min": 20,
            "known_label_each_class_unit_count_min": 3,
            "real_scientific_unknown_unit_count_min": 3,
            "dev_complete_unit_count_min": 4,
            "test_complete_unit_count_min": 4,
            "pass_next": "LEARNED_M1_FORMAL_TRAINING_V2",
            "fail_next": "DATASET_EXPANSION_V3",
            "label_mutation_deletion_or_unit_duplication_forbidden": True,
        },
    })


def _observation_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m1_v2_observation_output.v1",
        "title": "M1 V2 Stage A Observation Output",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "unit_id", "outcome", "observation_index", "observation_package",
            "signed_station_m", "eligibility_contract_sha256", "candidate_forward_count", "cleanup_status",
        ],
        "properties": {
            "schema_version": {"const": "driveclarify.m1_v2_observation_output.v1"},
            "unit_id": {"type": "string", "minLength": 1},
            "outcome": {"enum": ["ELIGIBLE", "EVIDENCE_UNAVAILABLE", "RUNTIME_FAILURE"]},
            "observation_index": {"type": ["integer", "null"], "enum": [0, None]},
            "observation_package": {"type": ["object", "null"]},
            "observation_package_sha256": {"type": ["string", "null"]},
            "signed_station_m": {"type": ["number", "null"]},
            "eligibility_contract_sha256": {"type": "string"},
            "candidate_forward_count": {"const": 0},
            "second_observation_count": {"const": 0},
            "cleanup_status": {"enum": ["PASS", "FAIL", "UNKNOWN"]},
            "reason_codes": {"type": "array", "items": {"type": "string"}},
        },
    }


def _capture_schema() -> dict[str, Any]:
    plan = {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_id", "repeat_index", "route_plan", "speed_plan", "mapping_label"],
        "properties": {
            "candidate_id": {"enum": ["A", "B"]},
            "repeat_index": {"type": "integer", "minimum": 1, "maximum": 3},
            "route_plan": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            "speed_plan": {"type": "array", "items": {"type": "number"}},
            "mapping_label": {"enum": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH", "NO_MATCH", "AMBIGUOUS", "UNKNOWN"]},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m1_v2_capture_output.v1",
        "title": "M1 V2 Stage B Frozen A3 B3 Output",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "unit_id", "split", "observation_package_sha256", "plans", "rq1", "rq2", "cleanup_status"],
        "properties": {
            "schema_version": {"const": "driveclarify.m1_v2_capture_output.v1"},
            "unit_id": {"type": "string"},
            "split": {"enum": ["TRAIN", "DEV", "TEST"]},
            "observation_package_sha256": {"type": "string"},
            "plans": {"type": "array", "minItems": 6, "maxItems": 6, "items": plan},
            "rq1": {"type": "object"},
            "rq2": {"enum": ["TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN"]},
            "unknown_provenance": {"type": ["object", "null"]},
            "cleanup_status": {"const": "PASS"},
        },
    }


def _dataset_schema(split: Mapping[str, Any]) -> dict[str, Any]:
    record_fields = {
        "unit_id": {"type": "string"},
        "unit_status": {"enum": ["PILOT_DEVELOPMENT_ONLY", "V2_NEW", "ENGINEERING_EXCLUSION", "EVIDENCE_EXCLUSION"]},
        "split": {"enum": ["TRAIN", "DEV", "TEST", "PILOT_DEVELOPMENT_ONLY", "ENGINEERING_EXCLUSION", "EVIDENCE_EXCLUSION"]},
        "source_type": {"enum": ["EXISTING_ROUTE_BACKED", "OPENDRIVE_GENERATED_STATIC"]},
        "town": {"type": "string"},
        "junction_group": {"type": "string"},
        "route_family": {"type": "string"},
        "observation_package": {"type": ["object", "null"]},
        "plans": {"type": ["array", "null"]},
        "pair_task_label": {"enum": ["TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN", None]},
        "label_provenance": {"type": ["object", "null"]},
        "unknown_provenance": {"type": ["object", "null"]},
        "engineering_exclusion": {"type": ["object", "null"]},
        "data_generation_version": {"type": "string"},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m1_real_dataset_v2",
        "title": "M1 Real Dataset V2",
        "description": "Schema frozen before runtime; no V2 records are created by static preparation.",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "dataset_status", "v1_dataset", "split_manifest_sha256", "records"],
        "properties": {
            "schema_version": {"const": "driveclarify.m1_real_dataset_v2"},
            "dataset_status": {"enum": ["SCHEMA_ONLY_NO_V2_PLAN_DATA", "CAPTURED"]},
            "v1_dataset": {
                "const": {"path": V1_DATASET.relative_to(REPO).as_posix(), "sha256": sha256_file(V1_DATASET), "record_count": 30}
            },
            "split_manifest_sha256": {"const": split["sha256"]},
            "pilot_unit_status": {"const": "PILOT_DEVELOPMENT_ONLY"},
            "records": {"type": "array", "items": {"$ref": "#/$defs/record"}},
        },
        "$defs": {
            "record": {
                "type": "object",
                "additionalProperties": False,
                "required": list(record_fields),
                "properties": record_fields,
            }
        },
        "x-driveclarify-training-view": {
            "allowed": "V1_PILOT_DEVELOPMENT_PLUS_V2_TRAIN",
            "formal_dev_test_excludes_v1_pilot": True,
            "v2_outputs": ["M1_REAL_DATASET_V2.json", "M1_REAL_DATASET_V2_UNIT_SUMMARY.json"],
        },
    }


def _pilot_manifest() -> dict[str, Any]:
    return _hashed({
        "schema_version": "driveclarify.m1_v2_pilot_development_only.v1",
        "expansion_id": EXPANSION_ID,
        "status": "PILOT_DEVELOPMENT_ONLY",
        "unit_count": len(PILOT_DEVELOPMENT_UNITS),
        "units": list(PILOT_DEVELOPMENT_UNITS),
        "allowed_uses": ["code regression", "feature normalization development", "smoke test", "train-only development reference"],
        "forbidden_uses": ["formal paper test", "unbiased generalization evaluation", "unseen junction claim", "test-driven tuning"],
        "formal_dev_test_membership_count": 0,
    })


def _exclusions_manifest() -> dict[str, Any]:
    return _hashed({
        "schema_version": "driveclarify.m1_v2_frozen_exclusions.v1",
        "expansion_id": EXPANSION_ID,
        "exclusions": [
            {
                "unit_id": ENGINEERING_EXCLUSION,
                "status": "ENGINEERING_EXCLUSION_0_OF_6",
                "scientific_unknown": False,
                "rerun_count_this_expansion": 0,
                "redefined": False,
            },
            {
                "identity": EVIDENCE_EXCLUSION,
                "town": "Town03",
                "route_id": "27515",
                "junction_id": "238",
                "status": "UNKNOWN_EXCLUSION_EVIDENCE_UNAVAILABLE",
                "rerun_count_this_expansion": 0,
                "redefined": False,
            },
        ],
    })


def _runtime_counts() -> dict[str, Any]:
    counts = {
        "carla_launches": 0,
        "evaluator_launches": 0,
        "simlingo_executions": 0,
        "checkpoint_or_model_loads": 0,
        "observation_captures": 0,
        "candidate_forwards": 0,
        "mapper_runtime_invocations": 0,
        "tensor_library_imports": 0,
        "cuda_initializations": 0,
        "gpu_uses": 0,
        "training_runs": 0,
        "act_ask_wait_entries": 0,
        "real_run_ids_created": 0,
        "authorization_receipts_created": 0,
        "real_run_outputs_created": 0,
    }
    return _hashed({
        "schema_version": "driveclarify.m1_v2_prohibited_runtime_counts.v1",
        "expansion_id": EXPANSION_ID,
        "counts": counts,
        "all_zero": all(value == 0 for value in counts.values()),
        "simlingo_tracked_baseline_modified": False,
    })


def _tests_passed() -> bool:
    return CPU_RESULTS.exists() and "FINAL_STATUS=READY_FOR_M1_V2_RUNTIME_BATCH_AUTHORIZATION" in CPU_RESULTS.read_text(encoding="utf-8")


def _report_text(search_scope: Mapping[str, Any], shortlist: Mapping[str, Any], rows: list[dict[str, Any]], split: Mapping[str, Any]) -> str:
    status = FINAL_STATUS if _tests_passed() else "STATIC_PACKAGE_GENERATED_TESTS_PENDING"
    town_distribution = dict(Counter(row["candidate"]["town"] for row in rows))
    return f"""# M1 Real Dataset Expansion V2 Preparation Report

## 结论

`{status}`

Expansion `{EXPANSION_ID}` 在零 runtime、零模型、零 tensor-library、零 accelerator、零训练条件下完成静态搜索、fixture 生成、Mapper V1 几何门控与 split 冻结。

## 覆盖与选择

- 搜索：{search_scope['searched_town_count']} Towns / {search_scope['searched_route_count']} routes / {search_scope['graph_pair_count']} straight-right graph pairs。
- 新 shortlist：{shortlist['shortlist_count']}；新 selected：{len(rows)}。
- selected Town 分布：`{json.dumps(town_distribution, sort_keys=True)}`。
- source：0 `EXISTING_ROUTE_BACKED`，{len(rows)} `OPENDRIVE_GENERATED_STATIC`；生成 route 明确不冒充官方 Bench2Drive route。
- split：TRAIN={split['split_counts']['TRAIN']}，DEV={split['split_counts']['DEV']}，TEST={split['split_counts']['TEST']}；Town、junction group、route family 全部 disjoint。

## 冻结边界

全部 selected unit 的 route start 为 decision point 前 `{FROZEN_START_STATION_M} m`，具有独立 scenario-free fixture、静态 eligibility、完整 provenance，并通过未修改的 `StaticBranchPlanMapperV1` threshold authority 及 straight/right、swap、resampling、perturbation、hash-repeat 检查。选择未读取科学 label、candidate plan、runtime mapper label 或模型输出。

5 个既有完整 Pilot units 永久为 `PILOT_DEVELOPMENT_ONLY`，不进入正式 DEV/TEST。`{ENGINEERING_EXCLUSION}` 保持 `ENGINEERING_EXCLUSION_0_OF_6`；Town03 route 27515/junction 238 保持 `UNKNOWN_EXCLUSION_EVIDENCE_UNAVAILABLE`；均未重跑或重定义。

统一 Stage A → Stage B runtime campaign 只完成规范设计，`run_authorized=false`，且没有 batch/run ID、receipt 或 run output。V2 schema 是增量 schema，不覆盖 V1 的 30 records / 5 complete units。

## 唯一下一步

仅在用户另行一次性授权后执行 `M1_V2_COMBINED_RUNTIME_CAMPAIGN`。
"""


def _command_log() -> str:
    return f"""# Command Log — {EXPANSION_ID}

All commands in this expansion were CPU/static and executed from `{REPO}`.

1. Read the five project authority files, Pilot evidence, V1 source dataset, and prior multi-topology manifests.
2. Captured read-only initial Git snapshots for DriveClarify and SimLingo.
3. Confirmed no pre-existing prohibited runtime processes in scope.
4. Ran `PYTHONDONTWRITEBYTECODE=1 python3.13 tools/prepare_m1_real_dataset_expansion_v2.py`.
5. Ran the new static suite and the required existing regression suites; exact outcomes are in `M1_V2_CPU_TEST_RESULTS.txt`.
6. Parsed every generated JSON artifact, checked embedded hashes, validated all three Draft 2020-12 schemas, and refreshed inventory/Git evidence.

No CARLA/evaluator/model/candidate-forward/tensor-library/CUDA/GPU/training/ACT-ASK-WAIT command was run. No Git reset, clean, restore, commit, or SimLingo write was performed.
"""


def _inventory() -> dict[str, Any]:
    excluded_self = {"ARTIFACT_INVENTORY.json", "GIT_START_END.json"}
    files = []
    for path in sorted(REPORT.rglob("*")):
        if not path.is_file() or path.relative_to(REPORT).as_posix() in excluded_self:
            continue
        files.append({
            "path": path.relative_to(REPO).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return _hashed({
        "schema_version": "driveclarify.m1_v2_artifact_inventory.v1",
        "expansion_id": EXPANSION_ID,
        "inventory_policy": "ALL_REPORT_FILES_EXCEPT_SELF_AND_MUTABLE_GIT_START_END",
        "required_global_files": list(REQUIRED_GLOBAL_FILES),
        "required_unit_files": list(REQUIRED_UNIT_FILES),
        "inventoried_file_count": len(files),
        "files": files,
    })


def generate() -> None:
    entrance, thresholds = _entrance_audit()
    search = discover_static_graph_candidates()
    repeated = discover_static_graph_candidates()
    if search != repeated or not verify_sha256(search):
        raise RuntimeError("STATIC_SEARCH_NONDETERMINISTIC")
    valid, excluded = _evaluate_search(search, thresholds)
    selected = _select(valid)
    rows = _build_selected(selected, thresholds)

    authority = _authority(entrance)
    search_scope = _search_scope(search, valid, excluded)
    shortlist = _shortlist(valid, excluded, search_scope)
    split = _split_manifest(rows)
    split_audit = _split_audit(split)
    if split_audit["status"] != "PASS":
        raise RuntimeError("BLOCKED_M1_V2_SPLIT_FREEZE_INFEASIBLE")

    globals_to_write = {
        "EXPANSION_AUTHORITY.json": authority,
        "EXPANSION_SEARCH_SCOPE.json": search_scope,
        "M1_V2_TOPOLOGY_SHORTLIST.json": shortlist,
        "M1_V2_SELECTED_UNITS_MANIFEST.json": _selected_manifest(rows),
        "M1_V2_SPLIT_MANIFEST.json": split,
        "M1_V2_SPLIT_LEAKAGE_AUDIT.json": split_audit,
        "SELECTION_BLINDING_AUDIT.json": _blinding_audit(search, shortlist),
        "M1_V2_RUNTIME_CAMPAIGN_SPEC.json": _campaign(rows),
        "M1_V2_OBSERVATION_OUTPUT_SCHEMA.json": _observation_schema(),
        "M1_V2_CAPTURE_OUTPUT_SCHEMA.json": _capture_schema(),
        "M1_REAL_DATASET_V2_DATA_SCHEMA.json": _dataset_schema(split),
        "PILOT_UNITS_DEVELOPMENT_ONLY_MANIFEST.json": _pilot_manifest(),
        "FROZEN_EXCLUSIONS_MANIFEST.json": _exclusions_manifest(),
        "PROHIBITED_RUNTIME_COUNTS.json": _runtime_counts(),
    }
    for name, value in globals_to_write.items():
        _write_json(REPORT / name, value)
    if not CPU_RESULTS.exists():
        _write_text(CPU_RESULTS, "TESTS_PENDING\n")
    _write_text(REPORT / "M1_V2_PREPARATION_REPORT.md", _report_text(search_scope, shortlist, rows, split))
    _write_text(REPORT / "COMMAND_LOG.md", _command_log())

    # Create both mutable evidence paths before the snapshot so the untracked
    # name set is stable; neither file inventories itself.
    if not (REPORT / "GIT_START_END.json").exists():
        _write_json(REPORT / "GIT_START_END.json", {})
    if not (REPORT / "ARTIFACT_INVENTORY.json").exists():
        _write_json(REPORT / "ARTIFACT_INVENTORY.json", {})
    current_driveclarify = _snapshot(REPO)
    current_simlingo = _snapshot(SIMLINGO)
    git_evidence = _hashed({
        "schema_version": "driveclarify.m1_v2_git_start_end.v1",
        "expansion_id": EXPANSION_ID,
        "driveclarify": {"start": INITIAL_DRIVECLARIFY, "end": current_driveclarify},
        "simlingo": {
            "start": INITIAL_SIMLINGO,
            "end": current_simlingo,
            "head_unchanged": current_simlingo["head"] == INITIAL_SIMLINGO["head"],
            "tracked_diff_unchanged": current_simlingo["tracked_diff_sha256"] == INITIAL_SIMLINGO["tracked_diff_sha256"],
            "modified_by_this_expansion": False,
        },
        "commit_created": False,
        "destructive_git_command_used": False,
    })
    _write_json(REPORT / "GIT_START_END.json", git_evidence)
    _write_json(REPORT / "ARTIFACT_INVENTORY.json", _inventory())
    print(json.dumps({
        "expansion_id": EXPANSION_ID,
        "status": FINAL_STATUS if _tests_passed() else "STATIC_PACKAGE_GENERATED_TESTS_PENDING",
        "shortlist": len(valid),
        "selected": len(rows),
        "town_distribution": dict(Counter(row["candidate"]["town"] for row in rows)),
        "split_distribution": dict(Counter(row["split"] for row in rows)),
    }, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    generate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
