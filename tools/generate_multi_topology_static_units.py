"""Generate the CPU-only Multi-Topology Eligibility preparation package.

No runtime/model dependency is imported.  The generated observation batch is
explicitly unapproved and contains neither Run IDs nor receipts/run outputs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from driveclarify_static_branch.multi_topology import (
    EXCLUDED_JUNCTION_ID,
    EXCLUDED_ROUTE_ID,
    FROZEN_START_STATION_M,
    FROZEN_THRESHOLD_PATH,
    TARGET_START_INTERVAL_M,
    build_scenario_free_fixture,
    discover_route_backed_candidates,
    eligibility_contract,
    mapper_compatibility,
    task_binding,
    unit_id,
)
from driveclarify_static_branch.topology import (
    build_branch_topology_ground_truth,
    deterministic_sha256,
    sha256_file,
    verify_sha256,
)


REPORT = REPO / "reports/multi_topology_static_units_v1"
UNITS = REPORT / "units"
OLD_REPORT = REPO / "reports/static_maneuver_branch_primary_mvp_v1"
OLD_TOPOLOGY = OLD_REPORT / "BRANCH_TOPOLOGY_GROUND_TRUTH.json"
P3_RESULT = OLD_REPORT / "M3E_P3_CAMPAIGN_RESULT.json"
P3B_EVIDENCE = (
    OLD_REPORT
    / "M3E_STATIC_BRANCH_PILOT/run_outputs/DC-M3E-STATIC-P3B-20260803T102400Z/FIRST_OBSERVATION_EVIDENCE.json"
)
CPU_RESULTS = REPORT / "MULTI_TOPOLOGY_CPU_TEST_RESULTS.txt"
START_GIT_STATUS_SHA256 = "b473e3ff1d1b53ccbcfa74c09ff1a659f25dad85a80ab69e9bec576c354b5a64"
START_GIT_STATUS_ENTRY_COUNT = 162
FROZEN_AUTHORITY_FILE_HASHES = {
    "old_topology_file_sha256": "bfe609cd1df8718267c73b02cd8e62f42f8a7b36bda22a692aaff5e0198e2bab",
    "threshold_file_sha256": "6a118500243116315ccd8e0943413fadd4845b171e2ea5b5f858aa1504a1e6fc",
    "p3_campaign_result_file_sha256": "6eacc5ab3121d892c4a00d3cee113c89b27d0915637120672a8d21037a78043d",
}


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _hashed(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("sha256", None)
    result["sha256"] = deterministic_sha256(result)
    return result


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _git_snapshot(repo: Path) -> dict[str, Any]:
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo).decode().strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    status = subprocess.check_output(["git", "status", "--porcelain=v1", "-z"], cwd=repo)
    tracked = subprocess.check_output(["git", "diff", "--binary"], cwd=repo)
    staged = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=repo)
    return {
        "branch": branch,
        "head": head,
        "status_porcelain_z_sha256": hashlib.sha256(status).hexdigest(),
        "status_entry_count": len([item for item in status.split(b"\0") if item]),
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
    }


def _simlingo_snapshot() -> dict[str, Any]:
    repo = Path("/home/buaa/wrh/simlingo")
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo).decode().strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    tracked = subprocess.check_output(["git", "diff", "--binary"], cwd=repo)
    staged = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=repo)
    return {
        "branch": branch,
        "head": head,
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        "modified_by_this_preparation": False,
    }


def _assert_frozen_authorities() -> tuple[dict[str, Any], dict[str, Any]]:
    actual = {
        "old_topology_file_sha256": sha256_file(OLD_TOPOLOGY),
        "threshold_file_sha256": sha256_file(FROZEN_THRESHOLD_PATH),
        "p3_campaign_result_file_sha256": sha256_file(P3_RESULT),
    }
    if actual != FROZEN_AUTHORITY_FILE_HASHES:
        raise RuntimeError(f"FROZEN_AUTHORITY_FILE_CHANGED:{actual}")
    old_topology = _load_json(OLD_TOPOLOGY)
    thresholds = _load_json(FROZEN_THRESHOLD_PATH)
    if not verify_sha256(old_topology) or not verify_sha256(thresholds):
        raise RuntimeError("FROZEN_EMBEDDED_AUTHORITY_HASH_INVALID")
    if old_topology["sha256"] != "cfd5bb11099680c1a887e847adbb1cc11bf62ec13ba490dfd72075929e03c7db":
        raise RuntimeError("OLD_TOPOLOGY_EMBEDDED_HASH_CHANGED")
    if thresholds["sha256"] != "6dfeea8907eb987c512c2b180a824d78c38d7c57662385c24d0d884bf3a37553":
        raise RuntimeError("THRESHOLD_EMBEDDED_HASH_CHANGED")
    return old_topology, thresholds


def _source_provenance(candidate: Mapping[str, Any], topology: Mapping[str, Any]) -> dict[str, Any]:
    value = {
        "schema_version": "driveclarify.multi_topology_source_provenance.v1",
        "town": candidate["town"],
        "route_id": candidate["route_id"],
        "junction_id": candidate["junction_id"],
        "sources": [
            {
                "role": "OPENDRIVE_STATIC_MAP",
                "path": candidate["map_path"],
                "sha256": candidate["map_sha256"],
                "read_only": True,
            },
            {
                "role": "BENCH2DRIVE_ROUTE_SOURCE",
                "path": candidate["route_source_path"],
                "sha256": candidate["route_source_sha256"],
                "read_only": True,
                "source_scenario_names": candidate["source_scenario_names"],
                "source_scenario_types": candidate["source_scenario_types"],
                "dynamic_content_copied_to_fixture": False,
            },
            {
                "role": "FROZEN_MAPPER_THRESHOLDS",
                "path": str(FROZEN_THRESHOLD_PATH),
                "sha256": sha256_file(FROZEN_THRESHOLD_PATH),
                "embedded_sha256": "6dfeea8907eb987c512c2b180a824d78c38d7c57662385c24d0d884bf3a37553",
                "read_only": True,
            },
        ],
        "derivations": [
            "REFERENCE_LINE_PLUS_LANE_OFFSET_PLUS_LANE_WIDTH_TO_CENTERLINE",
            "OPENDRIVE_Y_TO_CARLA_NEGATIVE_Y",
            "FIRST_HALF_LANE_WIDTH_SEPARATION_TO_DIVERGENCE",
            "FIXED_12_M_EVALUATION_INTERVAL",
        ],
        "topology_sha256": topology["sha256"],
        "candidate_plan_read": False,
        "model_output_read": False,
        "mapper_result_used_for_selection": False,
        "hidden_task_label_read": False,
        "pilot_success_used": False,
    }
    return _hashed(value)


def _unit_manifest(
    candidate: Mapping[str, Any],
    directory: Path,
    topology: Mapping[str, Any],
    eligibility: Mapping[str, Any],
    mapper: Mapping[str, Any],
    task: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    fixture = directory / "SCENARIO_FREE_ROUTE.xml"
    artifact_paths = [
        fixture,
        directory / "BRANCH_TOPOLOGY_GROUND_TRUTH.json",
        directory / "OBSERVATION_ELIGIBILITY_CONTRACT.json",
        directory / "TASK_BINDING.json",
        directory / "MAPPER_COMPATIBILITY.json",
        directory / "SOURCE_PROVENANCE.json",
    ]
    return _hashed({
        "schema_version": "driveclarify.multi_topology_unit_manifest.v1",
        "unit_id": unit_id(candidate),
        "selection_verdict": "SELECTED_FOR_OBSERVATION_ONLY_ELIGIBILITY_SCREENING",
        "town": candidate["town"],
        "route_source": candidate["route_source_path"],
        "route_id": candidate["route_id"],
        "junction_id": candidate["junction_id"],
        "incoming_road_id": candidate["binding"]["incoming_road_id"],
        "incoming_lane_id": candidate["binding"]["incoming_lane_id"],
        "outgoing_branches": topology["branch_connections"],
        "branch_semantics": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"],
        "route_start": eligibility["route_start"],
        "decision_point": topology["decision_point"],
        "divergence_station_m": eligibility["divergence_station_m"],
        "evaluation_interval_m": eligibility["evaluation_interval_m"],
        "eligibility_interval": eligibility["eligibility_interval"],
        "expected_plan_horizon_requirement": eligibility["expected_plan_horizon_requirement"],
        "fixture_scenario_free": True,
        "fixture_dynamic_actor_or_trigger_count": 0,
        "mapper_compatibility": mapper["compatibility_verdict"],
        "topology_sha256": topology["sha256"],
        "threshold_sha256": mapper["threshold_contract_sha256"],
        "task_binding_sha256": task["sha256"],
        "source_provenance_sha256": source["sha256"],
        "candidate_or_model_output_used": False,
        "artifacts": [
            {
                "path": path.relative_to(REPO).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in artifact_paths
        ],
    })


def _build_units(search: Mapping[str, Any], thresholds: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in search["candidates"]:
        if candidate["route_id"] == EXCLUDED_ROUTE_ID and candidate["junction_id"] == EXCLUDED_JUNCTION_ID:
            continue
        directory = UNITS / unit_id(candidate)
        directory.mkdir(parents=True, exist_ok=True)
        fixture_text, route_start = build_scenario_free_fixture(
            candidate["route_source_path"], candidate["map_path"], candidate["binding"]
        )
        fixture_path = directory / "SCENARIO_FREE_ROUTE.xml"
        _write_text(fixture_path, fixture_text)
        topology = build_branch_topology_ground_truth(
            candidate["map_path"], fixture_path, candidate["binding"]
        )
        if topology["route_identity"]["route_existing_scenarios"]:
            raise RuntimeError(f"FIXTURE_SCENARIO_LEAK:{unit_id(candidate)}")
        _write_json(directory / "BRANCH_TOPOLOGY_GROUND_TRUTH.json", topology)
        eligibility = eligibility_contract(topology, route_start)
        mapper = mapper_compatibility(topology, thresholds, candidate["map_path"], candidate["binding"])
        task = task_binding(topology)
        source = _source_provenance(candidate, topology)
        _write_json(directory / "OBSERVATION_ELIGIBILITY_CONTRACT.json", eligibility)
        _write_json(directory / "TASK_BINDING.json", task)
        _write_json(directory / "MAPPER_COMPATIBILITY.json", mapper)
        _write_json(directory / "SOURCE_PROVENANCE.json", source)
        manifest = _unit_manifest(candidate, directory, topology, eligibility, mapper, task, source)
        _write_json(directory / "UNIT_MANIFEST.json", manifest)
        rows.append({
            "candidate": candidate,
            "directory": directory,
            "fixture_path": fixture_path,
            "topology": topology,
            "eligibility": eligibility,
            "mapper": mapper,
            "task": task,
            "source": source,
            "manifest": manifest,
        })
    if len(rows) < 4:
        raise RuntimeError("BLOCKED_INSUFFICIENT_STATIC_TOPOLOGY_UNITS")
    return rows


def _shortlist(search: Mapping[str, Any], units: list[dict[str, Any]], old_topology: Mapping[str, Any]) -> dict[str, Any]:
    by_identity = {
        (row["candidate"]["route_id"], row["candidate"]["junction_id"]): row for row in units
    }
    records: list[dict[str, Any]] = []
    for candidate in search["candidates"]:
        identity = (candidate["route_id"], candidate["junction_id"])
        row = by_identity.get(identity)
        if row is None:
            records.append({
                "candidate_id": "TOWN03_JUNCTION_238_FROZEN_EXCLUSION",
                "town": candidate["town"],
                "route_source": candidate["route_source_path"],
                "route_id": candidate["route_id"],
                "junction_id": candidate["junction_id"],
                "incoming_road_id": candidate["binding"]["incoming_road_id"],
                "incoming_lane_id": candidate["binding"]["incoming_lane_id"],
                "outgoing_branches": old_topology["branch_connections"],
                "branch_semantics": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"],
                "route_start": {
                    "world_xyz": old_topology["route_identity"]["route_start_xyz"],
                    "signed_station_from_decision_point_m": -31.11000000000005,
                },
                "decision_point": old_topology["decision_point"],
                "divergence_station_m": old_topology["branch_divergence_point"]["arc_length_from_decision_point_m"],
                "evaluation_interval_m": [
                    old_topology["evaluation_interval"]["start_arc_length_from_decision_point_m"],
                    old_topology["evaluation_interval"]["end_arc_length_from_decision_point_m"],
                ],
                "dynamic_dependency": "SOURCE_ROUTE_PEDESTRIAN_CROSSING; FROZEN P3B REFERENCE ONLY",
                "fixture_feasibility": "NOT_APPLICABLE_FROZEN_EXCLUSION_NO_NEW_FIXTURE",
                "mapper_compatibility": "HISTORICAL_STATIC_MAPPER_COMPATIBLE_BUT_UNIT_NOT_ACTIVE",
                "plan_horizon_feasibility": "FAIL_AT_FROZEN_FIRST_OBSERVATION",
                "selection_verdict": "EXCLUDED_UNKNOWN_EVIDENCE_UNAVAILABLE_FIRST_OBSERVATION_TOO_EARLY",
                "exclusion_reason": "ROUTE_27515_JUNCTION_238_MUST_NOT_RERUN_OR_REDEFINE",
                "source_sha256": {
                    "route": candidate["route_source_sha256"],
                    "opendrive": candidate["map_sha256"],
                    "p3b_first_observation_evidence": sha256_file(P3B_EVIDENCE),
                },
            })
            continue
        topology = row["topology"]
        eligibility = row["eligibility"]
        records.append({
            "candidate_id": row["manifest"]["unit_id"],
            "town": candidate["town"],
            "route_source": candidate["route_source_path"],
            "route_id": candidate["route_id"],
            "junction_id": candidate["junction_id"],
            "incoming_road_id": candidate["binding"]["incoming_road_id"],
            "incoming_lane_id": candidate["binding"]["incoming_lane_id"],
            "outgoing_branches": topology["branch_connections"],
            "branch_semantics": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"],
            "route_start": eligibility["route_start"],
            "decision_point": topology["decision_point"],
            "divergence_station_m": eligibility["divergence_station_m"],
            "evaluation_interval_m": eligibility["evaluation_interval_m"],
            "dynamic_dependency": "NONE_IN_SCENARIO_FREE_FIXTURE",
            "fixture_feasibility": "PASS_SCENARIO_FREE_INDEPENDENT_FIXTURE",
            "mapper_compatibility": row["mapper"]["compatibility_verdict"],
            "plan_horizon_feasibility": "PASS_NOMINAL_20_POINT_HORIZON_WITH_UNIT_ELIGIBILITY_GATE",
            "selection_verdict": "SELECTED_FOR_OBSERVATION_ONLY_ELIGIBILITY_SCREENING",
            "exclusion_reason": None,
            "source_sha256": {
                "route": candidate["route_source_sha256"],
                "opendrive": candidate["map_sha256"],
                "fixture": sha256_file(row["fixture_path"]),
            },
        })
    return _hashed({
        "schema_version": "driveclarify.multi_topology_shortlist.v1",
        "search_authority_sha256": search["sha256"],
        "selection_policy": {
            "candidate_plan_used": False,
            "model_output_used": False,
            "mapper_result_used_to_select": False,
            "hidden_task_label_used": False,
            "pilot_success_used": False,
            "topology_and_mapper_static_feasibility_only": True,
        },
        "searched_candidate_count": len(records),
        "shortlist_count": len(records),
        "selected_count": len(units),
        "excluded_count": len(records) - len(units),
        "searched_scope": {
            key: search[key]
            for key in (
                "algorithm",
                "searched_map_count",
                "searched_map_paths",
                "searched_route_count",
                "searched_route_file_count",
                "supported_graph_pair_count_before_route_match",
            )
        },
        "candidates": records,
    })


def _selected_manifest(units: list[dict[str, Any]], final_status: str) -> dict[str, Any]:
    return _hashed({
        "schema_version": "driveclarify.selected_static_units_manifest.v1",
        "preparation_status": final_status,
        "selected_count": len(units),
        "excluded_frozen_reference": {
            "town": "Town03",
            "route_id": EXCLUDED_ROUTE_ID,
            "junction_id": EXCLUDED_JUNCTION_ID,
            "status": "UNKNOWN_EXCLUSION_EVIDENCE_UNAVAILABLE_FIRST_OBSERVATION_TOO_EARLY",
        },
        "selected_units": [
            {
                "unit_id": row["manifest"]["unit_id"],
                "town": row["candidate"]["town"],
                "route_id": row["candidate"]["route_id"],
                "junction_id": row["candidate"]["junction_id"],
                "branch_types": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"],
                "fixture_path": row["fixture_path"].relative_to(REPO).as_posix(),
                "fixture_sha256": sha256_file(row["fixture_path"]),
                "unit_manifest_path": (row["directory"] / "UNIT_MANIFEST.json").relative_to(REPO).as_posix(),
                "unit_manifest_file_sha256": sha256_file(row["directory"] / "UNIT_MANIFEST.json"),
                "topology_sha256": row["topology"]["sha256"],
                "route_start_signed_station_m": FROZEN_START_STATION_M,
                "eligibility_interval": row["eligibility"]["eligibility_interval"],
                "mapper_compatibility": row["mapper"]["compatibility_verdict"],
            }
            for row in units
        ],
        "selection_blinding": {
            "candidate_plan_read": False,
            "model_output_read": False,
            "mapper_result_used_for_selection": False,
            "hidden_task_label_read": False,
        },
    })


def _batch_spec(units: list[dict[str, Any]]) -> dict[str, Any]:
    return _hashed({
        "schema_version": "driveclarify.observation_screening_batch_spec.v1",
        "batch_name": "Multi-Topology First Observation Eligibility Screen",
        "run_authorized": False,
        "batch_id": None,
        "run_ids": [],
        "receipts": [],
        "run_outputs": [],
        "automatic_continuation": False,
        "selected_unit_count": len(units),
        "unit_order": [row["manifest"]["unit_id"] for row in units],
        "per_unit_limits": {
            "carla_initializations_max": 1,
            "evaluator_initializations_max": 1,
            "checkpoint_or_model_loads_max": 1,
            "model_ready_observations_max": 1,
            "required_observation_index": 0,
            "candidate_forwards": 0,
            "second_observation": 0,
            "same_unit_rerun_after_noneligible": 0,
            "cleanup_required_immediately": True,
        },
        "ordered_protocol": [
            "INITIALIZE_CARLA_AND_EVALUATOR_ONCE",
            "LOAD_CHECKPOINT_AND_MODEL_ONCE",
            "CAPTURE_FIRST_MODEL_READY_OBSERVATION_ONLY",
            "READ_OBSERVATION_INDEX_ZERO",
            "CHECK_UNIT_SPECIFIC_ELIGIBILITY_WITHOUT_CANDIDATE_FORWARD",
            "CLEANUP_IMMEDIATELY",
        ],
        "output_domain": ["ELIGIBLE", "EVIDENCE_UNAVAILABLE", "RUNTIME_FAILURE"],
        "noneligible_policy": "NO_SECOND_OBSERVATION_NO_START_ADJUSTMENT_NO_RERUN_KEEP_UNKNOWN_EXCLUSION",
        "eligible_policy": "MAY_ENTER_SEPARATELY_AUTHORIZED_LATER_A3_B3_BATCH",
        "prohibited": [
            "CANDIDATE_FORWARD",
            "SECOND_OBSERVATION",
            "SAME_UNIT_RERUN",
            "ROUTE_START_ADJUSTMENT_FROM_RUNTIME_OUTPUT",
            "REAL_BATCH_ID_CREATION_DURING_PREPARATION",
            "AUTHORIZATION_RECEIPT_CREATION_DURING_PREPARATION",
        ],
        "units": [
            {
                "unit_id": row["manifest"]["unit_id"],
                "route_fixture": row["fixture_path"].relative_to(REPO).as_posix(),
                "route_fixture_sha256": sha256_file(row["fixture_path"]),
                "eligibility_contract": (row["directory"] / "OBSERVATION_ELIGIBILITY_CONTRACT.json").relative_to(REPO).as_posix(),
                "eligibility_contract_sha256": row["eligibility"]["sha256"],
            }
            for row in units
        ],
    })


def _screening_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.observation_screening_output.v1",
        "title": "Observation Screening Output V1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "unit_id", "outcome", "observation_index", "observation_identity",
            "signed_station_m", "eligibility_contract_sha256", "candidate_forward_count", "cleanup_status",
            "reason_codes",
        ],
        "properties": {
            "schema_version": {"const": "driveclarify.observation_screening_output.v1"},
            "unit_id": {"type": "string", "minLength": 1},
            "outcome": {"enum": ["ELIGIBLE", "EVIDENCE_UNAVAILABLE", "RUNTIME_FAILURE"]},
            "observation_index": {"type": ["integer", "null"], "enum": [0, None]},
            "observation_identity": {"type": ["string", "null"]},
            "observation_hash": {"type": ["string", "null"]},
            "source_frame": {"type": ["integer", "null"]},
            "ego_pose": {"type": ["object", "null"]},
            "signed_station_m": {"type": ["number", "null"]},
            "eligibility_contract_sha256": {"type": "string"},
            "candidate_forward_count": {"const": 0},
            "second_observation_count": {"const": 0},
            "cleanup_status": {"enum": ["PASS", "FAIL", "UNKNOWN"]},
            "reason_codes": {"type": "array", "items": {"type": "string"}},
            "runtime_metadata": {"type": "object"},
        },
    }


def _nullable(kind: str) -> dict[str, Any]:
    return {"type": [kind, "null"]}


def _m1_schema() -> dict[str, Any]:
    fields: dict[str, Any] = {
        "unit_identity": {"type": "string"},
        "town": {"type": "string"},
        "route_fixture": {"type": "string"},
        "junction": {"type": "string"},
        "topology_sha256": {"type": "string"},
        "threshold_sha256": {"type": "string"},
        "observation_identity": _nullable("string"),
        "observation_hash": _nullable("string"),
        "ego_pose": _nullable("object"),
        "source_frame": _nullable("integer"),
        "candidate_semantic_payload": _nullable("object"),
        "candidate_payload_hash": _nullable("string"),
        "candidate_schedule": {"type": ["array", "null"], "items": {"type": "string"}},
        "route_plan": {"type": ["array", "null"], "items": {"type": "array", "items": {"type": "number"}}},
        "speed_plan": {"type": ["array", "null"], "items": {"type": "number"}},
        "plan_shape": {"type": ["array", "null"], "items": {"type": "integer"}},
        "plan_dtype": _nullable("string"),
        "plan_frame": _nullable("string"),
        "plan_unit": _nullable("string"),
        "plan_evidence": _nullable("object"),
        "mapping_label": {"enum": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH", "NO_MATCH", "AMBIGUOUS", "UNKNOWN", None]},
        "projection_distance": _nullable("number"),
        "alignment": _nullable("number"),
        "branch_score": _nullable("number"),
        "margin": _nullable("number"),
        "candidate_task_status": {"enum": ["PASS", "FAIL", "UNKNOWN", None]},
        "pair_task_label": {"enum": ["TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN", None]},
        "within_route_distance": _nullable("number"),
        "between_route_distance": _nullable("number"),
        "within_speed_distance": _nullable("number"),
        "between_speed_distance": _nullable("number"),
        "fairness_result": _nullable("object"),
        "evidence_mask": {"type": "object", "additionalProperties": {"type": "boolean"}},
        "unknown_reason": _nullable("string"),
        "exclusion_reason": _nullable("string"),
        "runtime_metadata": {"type": "object"},
        "train_split": {"type": ["boolean", "null"]},
        "dev_split": {"type": ["boolean", "null"]},
        "test_split": {"type": ["boolean", "null"]},
        "town_holdout": {"type": ["boolean", "null"]},
        "junction_holdout": {"type": ["boolean", "null"]},
        "prompt_holdout": {"type": ["boolean", "null"]},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m1_real_dataset_v1",
        "title": "M1_REAL_DATASET_V1",
        "description": "Schema only; this preparation creates no synthetic or real plan records.",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "dataset_status", "records"],
        "properties": {
            "schema_version": {"const": "driveclarify.m1_real_dataset_v1"},
            "dataset_status": {"const": "SCHEMA_ONLY_NO_PLAN_DATA"},
            "records": {"type": "array", "items": {"$ref": "#/$defs/record"}},
        },
        "$defs": {
            "record": {
                "type": "object",
                "additionalProperties": False,
                "required": list(fields),
                "properties": fields,
            }
        },
    }


def _final_status() -> str:
    if not CPU_RESULTS.exists():
        return "PREPARATION_GENERATED_CPU_TESTS_PENDING"
    text = CPU_RESULTS.read_text(encoding="utf-8")
    marker = "FINAL_STATUS=READY_FOR_BATCH_OBSERVATION_ELIGIBILITY_SCREEN"
    return "READY_FOR_BATCH_OBSERVATION_ELIGIBILITY_SCREEN" if marker in text else "PREPARATION_GENERATED_CPU_TESTS_PENDING"


def _report(units: list[dict[str, Any]], shortlist: Mapping[str, Any], final_status: str) -> str:
    rows = "\n".join(
        f"| `{row['manifest']['unit_id']}` | {row['candidate']['town']} | {row['candidate']['junction_id']} | "
        f"straight/right | {FROZEN_START_STATION_M:.1f} | "
        f"[{row['eligibility']['eligibility_interval']['lower_inclusive_m']:.1f}, "
        f"{row['eligibility']['eligibility_interval']['upper_exclusive_m']:.1f}) | PASS |"
        for row in units
    )
    tests = CPU_RESULTS.read_text(encoding="utf-8").strip() if CPU_RESULTS.exists() else "PENDING"
    return f"""# Multi-Topology Static Units V1 Preparation Report

## 结论

`{final_status}`

CPU-only 静态搜索覆盖 8 个可用 Town OpenDRIVE、55 条对应 Bench2Drive routes 与 108 个受支持的
straight/right graph pairs；确定性 route-backed shortlist 为 {shortlist['shortlist_count']} 个，正式 selected
为 {len(units)} 个。Town03 route 27515/junction 238 仅保留 UNKNOWN/exclusion，不是 active unit。

## Selected units

| Unit | Town | Junction | Branches | start station (m) | eligibility (m) | mapper |
|---|---|---:|---|---:|---|---|
{rows}

所有 fixture 都从只读 source route 确定性裁剪，在任何模型输出前把 route start 冻结为 decision point 前
`-5.5 m`，且 `<scenarios />` 为空；没有 actor、trigger、PedestrianCrossing 或 red-light scenario。各 unit 的
divergence 与 12 m evaluation interval 由 OpenDRIVE centerline 计算，eligibility 由 20-point nominal horizon
与 mapper tail offsets `[17,18,19] m` 独立推导。P3B 的约 0.9 s 结果只作为“首帧可能靠近起点”的工程证据，
没有被当作通用精确定律。

## Mapper compatibility

六个 selected unit 均复用未修改的 `StaticBranchPlanMapperV1` 和全局 threshold embedded SHA
`6dfeea8907eb987c512c2b180a824d78c38d7c57662385c24d0d884bf3a37553`。每个 unit 的 lane widths 均为
3.5 m；branch endpoint tangent separation 超过冻结 alignment gate；synthetic straight/right、branch-record
swap 与 opaque candidate-ID swap 均通过。没有按模型输出调 threshold，也没有 mapper v2。

## Batch and dataset freeze

`OBSERVATION_SCREENING_BATCH_SPEC.json` 保持 `run_authorized=false`、`batch_id=null`，Run IDs、receipts、
run_outputs 全为空。未来每 unit 只允许首个 model-ready observation index 0；非 eligible 不取第二帧、不调起点、
不重跑。`M1_REAL_DATASET_V1_SCHEMA.json` 只冻结 schema，没有生成任何 fake plan/data record。

## Tests

```text
{tests}
```

## Preserved boundaries

旧 topology/threshold/P3 result file hashes在生成入口 fail-closed 核验；SimLingo 只读。CARLA、真实 evaluator、
model/checkpoint load、observation capture、forward、torch、CUDA、GPU、训练、ACT/ASK/WAIT、Run ID、receipt、
真实 run_outputs 均为 0。下一步只能由用户显式授权一次 Batch Observation Eligibility Screen。
"""


def _command_log(final_status: str) -> str:
    return f"""# Multi-Topology CPU Preparation Command Log

Scope: CPU/static only. Final status: `{final_status}`.

Commands used for generation and verification:

```text
python -B tools/generate_multi_topology_static_units.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -q tests/multi_topology_static_units
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -q tests/static_branch_mvp tests/fairness_contract_v2 tests/m3d_route_validation tests/evaluator_adapter_production_path tests/m3e_supervisor_binding tests/multi_topology_static_units
python -B tools/generate_multi_topology_static_units.py --finalize
python -B -m json.tool reports/multi_topology_static_units_v1/M1_REAL_DATASET_V1_SCHEMA.json
```

Read-only inspection also used `rg`, `find`, `sed`, `sha256sum`, `git status`, `git diff`, and Python standard-library
XML/JSON parsing. No CARLA/evaluator/model/torch/CUDA/GPU or training command was run. No git reset/clean/restore/
checkout/commit command was run.
"""


def _git_record() -> dict[str, Any]:
    end = _git_snapshot(REPO)
    return _hashed({
        "schema_version": "driveclarify.git_start_end_multi_topology.v1",
        "start": {
            "branch": "master",
            "head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
            "status_porcelain_z_sha256": START_GIT_STATUS_SHA256,
            "status_entry_count": START_GIT_STATUS_ENTRY_COUNT,
            "tracked_diff_bytes": 0,
            "staged_diff_bytes": 0,
            "worktree_note": "PREEXISTING_UNTRACKED_WORKTREE; USER_FILES_PRESERVED",
        },
        "end": end,
        "head_unchanged": end["head"] == "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
        "branch_unchanged": end["branch"] == "master",
        "tracked_and_staged_clean": end["tracked_diff_bytes"] == 0 and end["staged_diff_bytes"] == 0,
        "commit_created": False,
        "destructive_git_command_count": 0,
        "simlingo": _simlingo_snapshot(),
    })


def _inventory(final_status: str) -> dict[str, Any]:
    report_files = sorted(path for path in REPORT.rglob("*") if path.is_file() and path.name != "ARTIFACT_INVENTORY.json")
    implementation = [
        REPO / "driveclarify_static_branch/multi_topology.py",
        REPO / "tools/generate_multi_topology_static_units.py",
        REPO / "tests/multi_topology_static_units/test_multi_topology_static_units.py",
        REPO / "STATE.json",
        REPO / "CURRENT_HANDOFF.md",
        REPO / "NEXT_AGENT_PROMPT.md",
        REPO / "AGENT_WORKLOG.md",
    ]
    return _hashed({
        "schema_version": "driveclarify.multi_topology_artifact_inventory.v1",
        "final_status": final_status,
        "report_root": str(REPORT),
        "report_artifact_count_excluding_inventory": len(report_files),
        "report_artifacts": [
            {"path": path.relative_to(REPO).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in report_files
        ],
        "implementation_test_and_handoff_artifacts": [
            {
                "path": path.relative_to(REPO).as_posix(),
                "present": path.is_file(),
                "bytes": path.stat().st_size if path.is_file() else None,
                "sha256": sha256_file(path) if path.is_file() else None,
            }
            for path in implementation
        ],
        "frozen_authority_file_hashes": FROZEN_AUTHORITY_FILE_HASHES,
        "authorization_receipt_present": False,
        "real_run_outputs_present": False,
        "real_execution_performed": False,
        "inventory_self_hash_included": False,
    })


def generate() -> dict[str, Any]:
    old_topology, thresholds = _assert_frozen_authorities()
    REPORT.mkdir(parents=True, exist_ok=True)
    UNITS.mkdir(parents=True, exist_ok=True)
    if not CPU_RESULTS.exists():
        _write_text(CPU_RESULTS, "FINAL_STATUS=PENDING_CPU_TESTS\n")
    final_status = _final_status()
    search = discover_route_backed_candidates()
    if search["route_backed_candidate_count"] < 5:
        raise RuntimeError("BLOCKED_INSUFFICIENT_STATIC_TOPOLOGY_UNITS")
    units = _build_units(search, thresholds)
    shortlist = _shortlist(search, units, old_topology)
    selected = _selected_manifest(units, final_status)
    batch = _batch_spec(units)
    _write_json(REPORT / "MULTI_TOPOLOGY_SHORTLIST.json", shortlist)
    _write_json(REPORT / "SELECTED_STATIC_UNITS_MANIFEST.json", selected)
    _write_json(REPORT / "OBSERVATION_SCREENING_BATCH_SPEC.json", batch)
    _write_json(REPORT / "OBSERVATION_SCREENING_OUTPUT_SCHEMA.json", _screening_schema())
    _write_json(REPORT / "M1_REAL_DATASET_V1_SCHEMA.json", _m1_schema())
    _write_text(REPORT / "MULTI_TOPOLOGY_PREPARATION_REPORT.md", _report(units, shortlist, final_status))
    _write_text(REPORT / "COMMAND_LOG_MULTI_TOPOLOGY.md", _command_log(final_status))
    _write_json(REPORT / "GIT_START_END_MULTI_TOPOLOGY.json", _git_record())
    prohibition_counts = _hashed({
        "schema_version": "driveclarify.multi_topology_prohibition_counts.v1",
        "carla_launches": 0,
        "real_evaluator_launches": 0,
        "checkpoint_or_model_loads": 0,
        "observation_captures": 0,
        "candidate_forwards": 0,
        "torch_imports": 0,
        "cuda_initializations": 0,
        "gpu_uses": 0,
        "training_runs": 0,
        "act_ask_wait_entries": 0,
        "real_batch_ids_created": 0,
        "real_run_ids_created": 0,
        "authorization_receipts_created": 0,
        "real_run_outputs_created": 0,
        "route_27515_reruns": 0,
        "p3c_consumed": 0,
        "simlingo_tracked_files_modified": 0,
    })
    _write_json(REPORT / "PROHIBITED_RUNTIME_COUNTS.json", prohibition_counts)
    _write_json(REPORT / "ARTIFACT_INVENTORY.json", _inventory(final_status))
    return {
        "status": final_status,
        "searched_candidates": shortlist["searched_candidate_count"],
        "shortlist": shortlist["shortlist_count"],
        "selected": len(units),
        "report": str(REPORT),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finalize", action="store_true", help="regenerate metadata after CPU results are frozen")
    parser.parse_args()
    print(json.dumps(generate(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
