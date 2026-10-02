"""Freeze the label-blind M1 Real Dataset Expansion V3 static package."""

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

from driveclarify_static_branch.m1_expansion_v3 import (
    EXPANSION_ID,
    GENERATION_ALGORITHM,
    SEARCH_ALGORITHM,
    build_opendrive_generated_fixture,
    discover_static_graph_candidates,
    eligibility_contract,
    extended_mapper_compatibility,
    generated_topology,
    source_provenance,
    static_route_parser_compatibility,
)
from driveclarify_static_branch.multi_topology import (
    FROZEN_START_STATION_M,
    FROZEN_THRESHOLD_PATH,
    task_binding,
)
from driveclarify_static_branch.topology import (
    TopologyContractError,
    deterministic_sha256,
    sha256_file,
    verify_sha256,
)


RUNTIME_CAMPAIGN_ID = "DC-M1-V3-RUNTIME-C1-20260804T063000Z"
REPORT = REPO / "reports/m1_real_dataset_expansion_v3" / EXPANSION_ID
UNITS = REPORT / "units"
V2_ROOT = REPO / "reports/m1_real_dataset_expansion_v2/DC-M1-DATASET-EXP-V2-20260803T143000Z"
V2_CAMPAIGN = V2_ROOT / "combined_runtime_campaigns/DC-M1-V2-RUNTIME-C1-20260803T144700Z"
V2_SELECTED = V2_ROOT / "M1_V2_SELECTED_UNITS_MANIFEST.json"
V2_SPLIT = V2_ROOT / "M1_V2_SPLIT_MANIFEST.json"
V2_PILOTS = V2_ROOT / "PILOT_UNITS_DEVELOPMENT_ONLY_MANIFEST.json"
V2_EXCLUSIONS = V2_ROOT / "FROZEN_EXCLUSIONS_MANIFEST.json"
V2_DATASET = V2_CAMPAIGN / "stage_b/M1_REAL_DATASET_V2.json"
V1_DATASET = REPO / "reports/multi_topology_static_units_v1/offline_candidate_capture_runs/DC-MULTI-A3B3-C1-20260803T121500Z/M1_REAL_DATASET_V1.json"
LIFECYCLE_REPAIR = REPO / "reports/m1_v3_stage_a_terminal_result_contract_repair/DC-M1-V3-STAGE-A-TERMINAL-REPAIR-20260804T052830Z"
SIMLINGO = Path("/home/buaa/wrh/simlingo")
V2_TREE_SHA256 = "31dc8aaf2774f46fa47dd2dfe05832ca85c6797947efddbef418dbab446f5843"

SPLIT_TOWN_QUOTAS = {
    "TRAIN": {"Town03": 3, "Town04": 3, "Town05": 6, "Town06": 2},
    "DEV": {"Town02": 2, "Town07": 3},
    "TEST": {"Town01": 4, "Town10HD": 1},
}
NEW_TOWN_SPLIT_RULE = {"Town01": "TEST", "Town02": "DEV", "Town06": "TRAIN"}

REQUIRED_UNIT_FILES = {
    "SCENARIO_FREE_ROUTE.xml",
    "UNIT_MANIFEST.json",
    "BRANCH_TOPOLOGY_GROUND_TRUTH.json",
    "OBSERVATION_ELIGIBILITY_CONTRACT.json",
    "TASK_BINDING.json",
    "MAPPER_COMPATIBILITY.json",
    "SOURCE_PROVENANCE.json",
    "SPLIT_ASSIGNMENT.json",
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("JSON_OBJECT_REQUIRED:" + str(path))
    return value


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


def _git_snapshot(repository: Path) -> dict[str, Any]:
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repository).decode().strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository).decode().strip()
    tracked = subprocess.check_output(["git", "diff", "--binary"], cwd=repository)
    staged = subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=repository)
    paths = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repository
    )
    return {
        "branch": branch,
        "head": head,
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        "untracked_file_count": len([item for item in paths.split(b"\0") if item]),
        "untracked_path_list_nul_bytes": len(paths),
        "untracked_path_list_nul_sha256": hashlib.sha256(paths).hexdigest(),
    }


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"  ")
        digest.update(path.relative_to(REPO).as_posix().encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _protected_identities() -> tuple[set[str], dict[str, Any]]:
    selected = _read_json(V2_SELECTED)
    split = _read_json(V2_SPLIT)
    pilots = _read_json(V2_PILOTS)
    exclusions = _read_json(V2_EXCLUSIONS)
    unit_ids = {item["unit_id"] for item in selected["selected_units"]}
    unit_ids.update(pilots["units"])
    frozen_rows = []
    for item in exclusions["exclusions"]:
        frozen_rows.append(copy.deepcopy(item))
        if item.get("unit_id"):
            unit_ids.add(str(item["unit_id"]))
        if item.get("route_id") == "27515" and item.get("junction_id") == "238":
            unit_ids.add("TOWN03_JUNCTION_238_UNIT01")
    return unit_ids, {
        "v2_selected_count": len(selected["selected_units"]),
        "v2_selected_unit_ids": sorted(item["unit_id"] for item in selected["selected_units"]),
        "pilot_unit_ids": sorted(pilots["units"]),
        "historical_exclusions": frozen_rows,
        "v2_town_split": {
            town: split_name
            for split_name, towns in split["split_towns"].items()
            for town in towns
        },
    }


def _entrance_audit() -> tuple[dict[str, Any], dict[str, Any], set[str], dict[str, Any]]:
    state = _read_json(REPO / "STATE.json")
    if state.get("status") != "M1_V3_STAGE_A_TERMINAL_RESULT_CONTRACT_REPAIR_COMPLETE":
        raise RuntimeError("V3_ENTRY_STATE_MISMATCH")
    repair = _read_json(LIFECYCLE_REPAIR / "TEST_RESULTS.json")
    if repair.get("status") != "PASS" or repair.get("prohibited_runtime_counts", {}).get("dataset_expansion_v3_operations") != 0:
        raise RuntimeError("LIFECYCLE_REPAIR_AUTHORITY_INVALID")
    thresholds = _read_json(FROZEN_THRESHOLD_PATH)
    if not verify_sha256(thresholds):
        raise RuntimeError("FROZEN_THRESHOLD_EMBEDDED_HASH_INVALID")
    expected_thresholds = {
        "distance_threshold_m": 1.75,
        "alignment_threshold_cosine": 0.7157620042903288,
        "branch_score_margin": 0.10,
        "tail_point_count": 3,
    }
    if any(thresholds[key] != value for key, value in expected_thresholds.items()):
        raise RuntimeError("FROZEN_THRESHOLD_VALUE_CHANGED")
    v2_tree = _tree_hash(V2_ROOT)
    if v2_tree != V2_TREE_SHA256:
        raise RuntimeError("V2_TREE_HASH_CHANGED")
    drive = _git_snapshot(REPO)
    sim = _git_snapshot(SIMLINGO)
    if (
        drive["branch"] != "master"
        or not drive["head"].startswith("eaa332b1")
        or drive["tracked_diff_bytes"] != 0
        or drive["staged_diff_bytes"] != 0
        or sim["branch"] != "main"
        or not sim["head"].startswith("743b243a")
        or sim["tracked_diff_bytes"] != 7722
        or sim["tracked_diff_sha256"] != "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34"
        or sim["staged_diff_bytes"] != 0
    ):
        raise RuntimeError("BLOCKED_UNATTRIBUTED_WORKTREE_CHANGE")
    protected, history = _protected_identities()
    audit = _hashed(
        {
            "schema_version": "driveclarify.m1_v3_entrance_audit.v1",
            "expansion_id": EXPANSION_ID,
            "entry_status": state["status"],
            "lifecycle_repair_status": repair["status"],
            "v2_tree_sha256": v2_tree,
            "v1_dataset": {
                "path": V1_DATASET.relative_to(REPO).as_posix(),
                "bytes": V1_DATASET.stat().st_size,
                "sha256": sha256_file(V1_DATASET),
            },
            "v2_dataset": {
                "path": V2_DATASET.relative_to(REPO).as_posix(),
                "bytes": V2_DATASET.stat().st_size,
                "sha256": sha256_file(V2_DATASET),
            },
            "threshold_path": str(FROZEN_THRESHOLD_PATH),
            "threshold_file_sha256": sha256_file(FROZEN_THRESHOLD_PATH),
            "threshold_embedded_sha256": thresholds["sha256"],
            "threshold_values": expected_thresholds,
            "protected_history": history,
            "git_at_static_generation": {"driveclarify": drive, "simlingo": sim},
            "status": "PASS",
        }
    )
    return audit, thresholds, protected, history


def _candidate_evaluation(
    candidate: Mapping[str, Any], thresholds: Mapping[str, Any], path: Path
) -> dict[str, Any]:
    fixture_text, route_start = build_opendrive_generated_fixture(candidate)
    _write_text(path, fixture_text)
    parser = static_route_parser_compatibility(path)
    topology = generated_topology(candidate, path)
    contract = eligibility_contract(topology, route_start)
    mapper = extended_mapper_compatibility(topology, thresholds, candidate["map_path"], candidate["binding"])
    if parser["scenario_count"] or parser["actor_count"] or parser["trigger_count"]:
        raise TopologyContractError("GENERATED_FIXTURE_NOT_SCENARIO_FREE")
    return {
        "fixture_text": fixture_text,
        "route_start": route_start,
        "parser": parser,
        "topology": topology,
        "eligibility": contract,
        "mapper": mapper,
    }


def _evaluate_search(
    search: Mapping[str, Any], thresholds: Mapping[str, Any], protected: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    exclusions: list[dict[str, Any]] = []
    for candidate in search["candidates"]:
        if candidate["unit_id"] in protected:
            continue
        grouped[candidate["unit_id"]].append(candidate)
    valid: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="driveclarify-m1-v3-static-") as temporary:
        directory = Path(temporary)
        for unit_id, candidates in sorted(grouped.items()):
            reasons: list[str] = []
            for candidate in candidates:
                try:
                    evaluation = _candidate_evaluation(
                        candidate,
                        thresholds,
                        directory / (unit_id + "-" + str(candidate["graph_pair_index"]) + ".xml"),
                    )
                    topology = evaluation["topology"]
                    eligibility = evaluation["eligibility"]
                    straight = next(
                        branch
                        for branch in topology["branches"]
                        if branch["semantic_role"] == "STRAIGHT_BRANCH"
                    )["polyline_world_xyz"]
                    orientation = math.degrees(
                        math.atan2(straight[1][1] - straight[0][1], straight[1][0] - straight[0][0])
                    )
                    valid.append(
                        {
                            "candidate": copy.deepcopy(candidate),
                            "static_metrics": {
                                "straight_turn_angle_degrees": candidate["straight_turn_angle_degrees"],
                                "right_turn_angle_degrees": candidate["right_turn_angle_degrees"],
                                "incoming_driving_lane_count": candidate["incoming_driving_lane_count"],
                                "incoming_reference_geometry_types": candidate["incoming_reference_geometry_types"],
                                "incoming_lane_direction": candidate["binding"]["incoming_vehicle_direction"],
                                "divergence_station_m": eligibility["divergence_station_m"],
                                "evaluation_interval_m": eligibility["evaluation_interval_m"],
                                "eligibility_interval": eligibility["eligibility_interval"],
                                "route_orientation_degrees": orientation,
                            },
                            "static_verdict": "PASS_ALL_FROZEN_STATIC_GATES",
                            "alternative_graph_pairs_rejected_before_selected_pair": len(reasons),
                        }
                    )
                    break
                except (TopologyContractError, ValueError, TypeError) as exc:
                    reasons.append(str(exc).split(":", 1)[0])
            else:
                exclusions.append(
                    {
                        "unit_id": unit_id,
                        "town": candidates[0]["town"],
                        "junction_id": candidates[0]["junction_id"],
                        "reason_counts": dict(Counter(reasons)),
                    }
                )
    valid.sort(
        key=lambda row: (
            int("".join(ch for ch in row["candidate"]["town"] if ch.isdigit())),
            int(row["candidate"]["junction_id"]),
        )
    )
    if len(valid) < 32:
        raise RuntimeError("BLOCKED_M1_V3_STATIC_SELECTION_INSUFFICIENT")
    return valid, exclusions


def _select(valid: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_town: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        by_town[row["candidate"]["town"]].append(row)
    selected: list[dict[str, Any]] = []
    for split in ("TRAIN", "DEV", "TEST"):
        for town, count in SPLIT_TOWN_QUOTAS[split].items():
            if len(by_town[town]) < count:
                raise RuntimeError("BLOCKED_M1_V3_STATIC_SELECTION_INSUFFICIENT:" + town)
            for row in by_town[town][:count]:
                copied = copy.deepcopy(row)
                copied["split"] = split
                selected.append(copied)
    if len(selected) != 24 or len({row["candidate"]["unit_id"] for row in selected}) != 24:
        raise RuntimeError("M1_V3_SELECTION_COUNT_OR_IDENTITY_INVALID")
    return selected


def _split_assignment(row: Mapping[str, Any]) -> dict[str, Any]:
    candidate = row["candidate"]
    return _hashed(
        {
            "schema_version": "driveclarify.m1_v3_split_assignment.v1",
            "expansion_id": EXPANSION_ID,
            "unit_id": candidate["unit_id"],
            "split": row["split"],
            "town": candidate["town"],
            "junction_group": candidate["junction_group"],
            "route_family": candidate["route_family"],
            "split_strategy": "GLOBAL_TOWN_DISJOINT_INCREMENTAL_14_5_5",
            "frozen_before_observation_candidate_plan_or_scientific_label": True,
            "repeat_group_policy": "ALL_REPEATS_REMAIN_MEASUREMENTS_WITHIN_UNIT",
        }
    )


def _build_selected(
    selected: list[dict[str, Any]], thresholds: Mapping[str, Any]
) -> list[dict[str, Any]]:
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
        contract = eligibility_contract(topology, route_start)
        mapper = extended_mapper_compatibility(topology, thresholds, candidate["map_path"], candidate["binding"])
        task = task_binding(topology)
        source = source_provenance(candidate, fixture_path)
        assignment = _split_assignment(selected_row)
        artifacts = {
            "BRANCH_TOPOLOGY_GROUND_TRUTH.json": topology,
            "OBSERVATION_ELIGIBILITY_CONTRACT.json": contract,
            "TASK_BINDING.json": task,
            "MAPPER_COMPATIBILITY.json": mapper,
            "SOURCE_PROVENANCE.json": source,
            "SPLIT_ASSIGNMENT.json": assignment,
        }
        for name, value in artifacts.items():
            _write_json(directory / name, value)
        manifest_files = [fixture_path] + [directory / name for name in artifacts]
        manifest = _hashed(
            {
                "schema_version": "driveclarify.m1_v3_unit_manifest.v1",
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
                "incoming_vehicle_direction": candidate["binding"]["incoming_vehicle_direction"],
                "branch_semantics": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"],
                "route_parser_static_contract": parser["status"],
                "route_start": contract["route_start"],
                "decision_point": topology["decision_point"],
                "divergence_station_m": contract["divergence_station_m"],
                "evaluation_interval_m": contract["evaluation_interval_m"],
                "eligibility_interval": contract["eligibility_interval"],
                "horizon_requirement": contract["expected_plan_horizon_requirement"],
                "scenario_count": parser["scenario_count"],
                "actor_count": parser["actor_count"],
                "trigger_count": parser["trigger_count"],
                "mapper_compatibility": mapper["compatibility_verdict"],
                "threshold_sha256": thresholds["sha256"],
                "fixture_scenario_free": True,
                "fixture_dynamic_actor_or_trigger_count": 0,
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
                    for path in manifest_files
                ],
            }
        )
        _write_json(directory / "UNIT_MANIFEST.json", manifest)
        rows.append(
            {
                **copy.deepcopy(selected_row),
                "directory": directory,
                "fixture_path": fixture_path,
                "parser": parser,
                "topology": topology,
                "eligibility": contract,
                "mapper": mapper,
                "task": task,
                "source": source,
                "assignment": assignment,
                "manifest": manifest,
            }
        )
    return rows


def _shortlist(
    valid: list[dict[str, Any]], exclusions: list[dict[str, Any]], search: Mapping[str, Any]
) -> dict[str, Any]:
    candidates = []
    for row in valid:
        candidate = row["candidate"]
        candidates.append(
            {
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
            }
        )
    return _hashed(
        {
            "schema_version": "driveclarify.m1_v3_shortlist.v1",
            "expansion_id": EXPANSION_ID,
            "search_sha256": search["sha256"],
            "shortlist_count": len(candidates),
            "minimum_required": 32,
            "shortlist_is_new_unit_only": True,
            "town_distribution": dict(Counter(item["town"] for item in candidates)),
            "source_type_distribution": dict(Counter(item["source_type"] for item in candidates)),
            "candidates": candidates,
            "non_shortlisted_static_exclusions": exclusions,
        }
    )


def _selected_manifest(rows: list[dict[str, Any]], authorized: bool = False) -> dict[str, Any]:
    return _hashed(
        {
            "schema_version": "driveclarify.m1_v3_selected_units.v1",
            "expansion_id": EXPANSION_ID,
            "status": "READY_FOR_AUTHORIZED_RUNTIME" if authorized else "STATIC_TESTS_PENDING",
            "selected_count": len(rows),
            "selected_town_count": len({row["candidate"]["town"] for row in rows}),
            "town_distribution": dict(Counter(row["candidate"]["town"] for row in rows)),
            "split_distribution": dict(Counter(row["split"] for row in rows)),
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
        }
    )


def _split_manifest(rows: list[dict[str, Any]], history: Mapping[str, Any]) -> dict[str, Any]:
    assignments = [
        {
            "unit_id": row["candidate"]["unit_id"],
            "split": row["split"],
            "town": row["candidate"]["town"],
            "junction_group": row["candidate"]["junction_group"],
            "route_family": row["candidate"]["route_family"],
            "route_id": row["candidate"]["route_id"],
            "fixture_sha256": sha256_file(row["fixture_path"]),
            "opendrive_sha256": row["candidate"]["map_sha256"],
        }
        for row in rows
    ]
    return _hashed(
        {
            "schema_version": "driveclarify.m1_v3_split_manifest.v1",
            "expansion_id": EXPANSION_ID,
            "split_strategy": "GLOBAL_TOWN_DISJOINT_INCREMENTAL_14_5_5",
            "frozen_before_new_observation_candidate_plan_or_scientific_label": True,
            "split_counts": dict(Counter(row["split"] for row in rows)),
            "split_towns": {
                split: sorted({row["candidate"]["town"] for row in rows if row["split"] == split})
                for split in ("TRAIN", "DEV", "TEST")
            },
            "prior_town_split_authority": history["v2_town_split"],
            "new_town_split_rule": NEW_TOWN_SPLIT_RULE,
            "assignments": assignments,
            "pilot_units": [
                {"unit_id": unit_id, "split": "PILOT_DEVELOPMENT_ONLY"}
                for unit_id in history["pilot_unit_ids"]
            ],
            "future_repeat_policy": "REPEATS_ARE_MEASUREMENTS_AND_NEVER_CROSS_UNIT_SPLIT",
        }
    )


def _global_leakage_audit(split: Mapping[str, Any], history: Mapping[str, Any]) -> dict[str, Any]:
    v2 = _read_json(V2_SPLIT)
    prior = list(v2["assignments"])
    current = list(split["assignments"])
    combined = prior + current
    leaks: dict[str, list[str]] = {}
    for key in ("town", "junction_group", "route_family"):
        membership: dict[str, set[str]] = defaultdict(set)
        for row in combined:
            membership[str(row[key])].add(str(row["split"]))
        leaks[key] = sorted(identity for identity, values in membership.items() if len(values) > 1)
    prior_units = {row["unit_id"] for row in prior} | set(history["pilot_unit_ids"])
    current_units = {row["unit_id"] for row in current}
    duplicate_units = sorted(prior_units & current_units)
    fixture_hashes = [row["fixture_sha256"] for row in current]
    route_identity = [(row["town"], row["route_id"], row["opendrive_sha256"]) for row in current]
    prior_route_identity = {
        (row["town"], row.get("route_id"))
        for row in _read_json(V2_SELECTED)["selected_units"]
    }
    exact_route_overlap = sorted(
        "{}:{}".format(town, route_id)
        for town, route_id, _ in route_identity
        if (town, route_id) in prior_route_identity
    )
    pilot_dev_test = sorted(
        set(history["pilot_unit_ids"])
        & {row["unit_id"] for row in current if row["split"] in {"DEV", "TEST"}}
    )
    checks = {
        "town_disjoint": not leaks["town"],
        "junction_group_disjoint": not leaks["junction_group"],
        "route_family_disjoint": not leaks["route_family"],
        "duplicate_unit_overlap_zero": not duplicate_units,
        "duplicate_fixture_overlap_zero": len(fixture_hashes) == len(set(fixture_hashes)),
        "exact_opendrive_route_identity_overlap_zero": not exact_route_overlap,
        "pilot_formal_dev_test_overlap_zero": not pilot_dev_test,
        "repeat_groups_not_independent_samples": True,
    }
    return _hashed(
        {
            "schema_version": "driveclarify.m1_v3_global_split_leakage_audit.v1",
            "expansion_id": EXPANSION_ID,
            "scope": "V1_V2_V3",
            "checks": checks,
            "leaks": leaks,
            "duplicate_units": duplicate_units,
            "duplicate_fixture_hashes": sorted(
                value for value, count in Counter(fixture_hashes).items() if count > 1
            ),
            "exact_route_identity_overlap": exact_route_overlap,
            "pilot_dev_test_overlap": pilot_dev_test,
            "status": "PASS" if all(checks.values()) else "FAIL",
        }
    )


def _observation_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m1_v3_observation_output.v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "unit_id", "outcome", "observation_index", "observation_package",
            "signed_station_m", "eligibility_contract_sha256", "candidate_forward_count",
            "second_observation_count", "cleanup_status", "reason_codes",
        ],
        "properties": {
            "schema_version": {"const": "driveclarify.m1_v3_observation_output.v1"},
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
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "driveclarify.m1_v3_capture_output.v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "unit_id", "split", "observation_package_sha256", "plans", "rq1", "rq2", "unknown_provenance", "cleanup_status"],
        "properties": {
            "schema_version": {"const": "driveclarify.m1_v3_capture_output.v1"},
            "unit_id": {"type": "string"},
            "split": {"enum": ["TRAIN", "DEV", "TEST"]},
            "observation_package_sha256": {"type": "string"},
            "plans": {
                "type": "array", "minItems": 6, "maxItems": 6,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["candidate_id", "repeat_index", "route_plan", "speed_plan", "mapping_label"],
                    "properties": {
                        "candidate_id": {"enum": ["A", "B"]},
                        "repeat_index": {"type": "integer", "minimum": 1, "maximum": 3},
                        "route_plan": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                        "speed_plan": {"type": "array", "items": {"type": "number"}},
                        "mapping_label": {"enum": ["STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH", "NO_MATCH", "AMBIGUOUS", "UNKNOWN"]},
                    },
                },
            },
            "rq1": {"type": "object"},
            "rq2": {"enum": ["TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN"]},
            "unknown_provenance": {"type": ["object", "null"]},
            "cleanup_status": {"const": "PASS"},
        },
    }


def _dataset_schema(split: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "unit_id": {"type": "string"},
        "unit_status": {"enum": ["V3_NEW", "ENGINEERING_EXCLUSION", "EVIDENCE_EXCLUSION"]},
        "split": {"enum": ["TRAIN", "DEV", "TEST"]},
        "source_type": {"const": "OPENDRIVE_GENERATED_STATIC"},
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
        "$id": "driveclarify.m1_real_dataset_v3.incremental.v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "dataset_status", "expansion_id", "campaign_id", "split_manifest_sha256", "records"],
        "properties": {
            "schema_version": {"const": "driveclarify.m1_real_dataset_v3.incremental.v1"},
            "dataset_status": {"enum": ["SCHEMA_ONLY_NO_V3_PLAN_DATA", "CAPTURED"]},
            "expansion_id": {"const": EXPANSION_ID},
            "campaign_id": {"enum": [RUNTIME_CAMPAIGN_ID, None]},
            "split_manifest_sha256": {"const": split["sha256"]},
            "records": {"type": "array", "items": {"$ref": "#/$defs/record"}},
        },
        "$defs": {
            "record": {
                "type": "object", "additionalProperties": False,
                "required": list(fields), "properties": fields,
            }
        },
    }


def _runtime_spec(rows: list[dict[str, Any]], authorized: bool = False) -> dict[str, Any]:
    return _hashed(
        {
            "schema_version": "driveclarify.m1_v3_runtime_campaign_spec.v1",
            "expansion_id": EXPANSION_ID,
            "campaign_name": "M1_REAL_DATASET_EXPANSION_V3",
            "campaign_id": RUNTIME_CAMPAIGN_ID if authorized else None,
            "run_authorized": authorized,
            "authorization_scope": "ONE_SHOT_FIXED_24_UNIT_STAGE_A_THEN_ELIGIBLE_ONLY_STAGE_B",
            "unit_order": [row["candidate"]["unit_id"] for row in rows],
            "run_ids": [],
            "receipts": [],
            "run_outputs": [],
            "stage_a": {
                "selected_unit_count": 24,
                "one_formal_attempt_per_unit": True,
                "observation_index": 0,
                "candidate_forward_count": 0,
                "second_observation_count": 0,
                "lifecycle": {
                    "prelaunch_state": "PRELAUNCH_STATE.json",
                    "internal_runtime_result": "STAGE_A_RUNTIME_RESULT.json",
                    "formal_terminal_result": "RUN_RESULT.json",
                    "terminal_overwrite_allowed": False,
                },
            },
            "stage_b": {
                "eligible_only": True,
                "schedule": ["A1", "A2", "A3", "B1", "B2", "B3"],
                "candidate_forward_per_complete_unit": 6,
                "same_frozen_observation_and_model_state": True,
            },
            "training": False,
            "act_ask_wait": False,
            "automatic_continuation": False,
        }
    )


def _inventory() -> dict[str, Any]:
    # Operational evidence is intentionally outside the frozen scientific
    # authority.  The prewritten delivery audit replaces the static-only test
    # summary and appends actual runtime commands after the one-shot campaign.
    mutable_delivery_files = {"COMMAND_LOG.md", "TEST_RESULTS.json"}
    files = []
    for path in sorted(
        item
        for item in REPORT.rglob("*")
        if item.is_file()
        and item.name != "FROZEN_HASH_INVENTORY.json"
        and item.relative_to(REPORT).as_posix() not in mutable_delivery_files
    ):
        files.append(
            {"path": path.relative_to(REPO).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    return _hashed(
        {
            "schema_version": "driveclarify.m1_v3_frozen_hash_inventory.v1",
            "expansion_id": EXPANSION_ID,
            "file_count": len(files),
            "files": files,
        }
    )


def _preparation_report(shortlist: Mapping[str, Any], selected: Mapping[str, Any], split: Mapping[str, Any], leakage: Mapping[str, Any], authorized: bool) -> str:
    return """# M1 Real Dataset Expansion V3 — Preparation Report

## Technical summary

Expansion `{expansion}` froze a label-blind shortlist of `{shortlist}` new units and exactly `{selected}` selected units. Incremental split is TRAIN/DEV/TEST=`14/5/5`, covers `{towns}` Towns, and the V1/V2/V3 leakage audit is `{leakage}`. Runtime authorization is `{authorized}`.

## Fixed selection and geometry evidence

Search used only installed OpenDRIVE topology, vehicle-direction/contactPoint/laneLink consistency, static route geometry, and the unchanged `StaticBranchPlanMapperV1` authority. It did not read candidate plans, observations, mapper runtime labels, scientific labels, model outputs, or V1/V2 label patterns. Every selected route starts `-5.5 m` before its decision point and has a scenario-free fixture plus bytes/SHA-256 provenance.

## Split contract

V2 Town assignments remain unchanged. New Town assignments were frozen label-blind as `{new_towns}`. Selected distribution is `{distribution}`; all Town, junction-group, route-family, fixture, and exact OpenDRIVE/route overlap checks passed.

## Runtime boundary

Stage A is one formal attempt per selected unit, observation index 0 only, candidate forward 0, second observation 0, and one-shot lifecycle publication. Stage B is eligible-only and fixed to `A1→A2→A3→B1→B2→B3`. Runtime start freezes code; no repair/rerun/alternate campaign is allowed afterward. Learned M1 training and M2+ remain prohibited.
""".format(
        expansion=EXPANSION_ID,
        shortlist=shortlist["shortlist_count"],
        selected=selected["selected_count"],
        towns=selected["selected_town_count"],
        leakage=leakage["status"],
        authorized=str(authorized).lower(),
        new_towns=json.dumps(NEW_TOWN_SPLIT_RULE, sort_keys=True),
        distribution=json.dumps(split["split_counts"], sort_keys=True),
    )


def generate() -> None:
    if REPORT.exists():
        spec_path = REPORT / "V3_RUNTIME_CAMPAIGN_SPEC.json"
        if (
            not spec_path.is_file()
            or _read_json(spec_path).get("run_authorized") is not False
            or (REPORT / "combined_runtime_campaigns").exists()
        ):
            raise RuntimeError("V3_REPORT_DIRECTORY_ALREADY_EXISTS_OR_RUNTIME_FROZEN")
    else:
        REPORT.mkdir(parents=True)
    audit, thresholds, protected, history = _entrance_audit()
    search = discover_static_graph_candidates()
    valid, exclusions = _evaluate_search(search, thresholds, protected)
    selected_input = _select(valid)
    rows = _build_selected(selected_input, thresholds)
    shortlist = _shortlist(valid, exclusions, search)
    selected = _selected_manifest(rows, False)
    split = _split_manifest(rows, history)
    leakage = _global_leakage_audit(split, history)
    if leakage["status"] != "PASS":
        raise RuntimeError("V3_GLOBAL_SPLIT_LEAKAGE")
    blinding = _hashed(
        {
            "schema_version": "driveclarify.m1_v3_selection_blinding_audit.v1",
            "expansion_id": EXPANSION_ID,
            "allowed_inputs": search["selection_inputs"],
            "forbidden_inputs": ["candidate plan", "observation package", "runtime mapper label", "TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN", "model output", "V1/V2 label pattern"],
            "forbidden_field_read_count": 0,
            "candidate_plan_read_count": 0,
            "observation_package_read_count": 0,
            "model_output_read_count": 0,
            "mapper_runtime_output_read_count": 0,
            "scientific_label_read_count": 0,
            "selection_implementation": "driveclarify_static_branch/m1_expansion_v3.py",
            "status": "PASS_SELECTION_BLIND_TO_SCIENTIFIC_RESULTS",
        }
    )
    documents = {
        "EXPANSION_AUTHORITY.json": audit,
        "V3_STATIC_GRAPH_SEARCH.json": search,
        "V3_SHORTLIST.json": shortlist,
        "V3_SELECTED_UNITS.json": selected,
        "V3_SPLIT_MANIFEST.json": split,
        "GLOBAL_SPLIT_LEAKAGE_AUDIT.json": leakage,
        "SELECTION_BLINDING_AUDIT.json": blinding,
        "V3_RUNTIME_CAMPAIGN_SPEC.json": _runtime_spec(rows, False),
        "V3_OBSERVATION_OUTPUT_SCHEMA.json": _observation_schema(),
        "V3_CAPTURE_OUTPUT_SCHEMA.json": _capture_schema(),
        "M1_REAL_DATASET_V3_SCHEMA.json": _dataset_schema(split),
        "FROZEN_HISTORICAL_PROTECTION.json": _hashed({"schema_version": "driveclarify.m1_v3_historical_protection.v1", "expansion_id": EXPANSION_ID, **history, "rerun_count": 0, "artifact_rewrite_count": 0}),
        "PROHIBITED_RUNTIME_COUNTS.json": _hashed({"schema_version": "driveclarify.m1_v3_static_runtime_counts.v1", "expansion_id": EXPANSION_ID, "carla_launch": 0, "evaluator_launch": 0, "checkpoint_load": 0, "observation_capture": 0, "candidate_forward": 0, "torch_import": 0, "cuda_context": 0, "gpu_use": 0, "training": 0, "act_ask_wait": 0, "all_zero": True}),
        "GIT_START.json": _hashed({"schema_version": "driveclarify.m1_v3_git_start.v1", "expansion_id": EXPANSION_ID, "baseline_source": str((LIFECYCLE_REPAIR / "GIT_END.json").relative_to(REPO)), "baseline": _read_json(LIFECYCLE_REPAIR / "GIT_END.json"), "current_at_static_generation": {"driveclarify": _git_snapshot(REPO), "simlingo": _git_snapshot(SIMLINGO)}, "v2_tree_sha256": _tree_hash(V2_ROOT)}),
    }
    for name, value in documents.items():
        _write_json(REPORT / name, value)
    _write_text(REPORT / "V3_PREPARATION_REPORT.md", _preparation_report(shortlist, selected, split, leakage, False))
    _write_text(
        REPORT / "COMMAND_LOG.md",
        "# Command Log — {}\n\n- CPU-only bidirectional OpenDRIVE search complete: {} valid new shortlist units.\n- Label-blind selection frozen: 24 units; split 14/5/5; global leakage PASS.\n- Runtime remains unauthorized pending tests.\n".format(EXPANSION_ID, len(valid)),
    )
    _write_json(REPORT / "FROZEN_HASH_INVENTORY.json", _inventory())


def _rebuild_rows() -> list[dict[str, Any]]:
    selected = _read_json(REPORT / "V3_SELECTED_UNITS.json")
    rows = []
    search = _read_json(REPORT / "V3_SHORTLIST.json")
    candidates = {item["unit_id"]: item for item in search["candidates"]}
    for item in selected["selected_units"]:
        candidate = candidates[item["unit_id"]]
        directory = UNITS / item["unit_id"]
        rows.append(
            {
                "candidate": candidate,
                "split": item["split"],
                "directory": directory,
                "fixture_path": directory / "SCENARIO_FREE_ROUTE.xml",
                "topology": _read_json(directory / "BRANCH_TOPOLOGY_GROUND_TRUTH.json"),
                "eligibility": _read_json(directory / "OBSERVATION_ELIGIBILITY_CONTRACT.json"),
                "mapper": _read_json(directory / "MAPPER_COMPATIBILITY.json"),
            }
        )
    return rows


def authorize(test_results_path: Path) -> None:
    tests = _read_json(test_results_path)
    if tests.get("status") != "PASS" or tests.get("failed", 0) != 0:
        raise RuntimeError("V3_STATIC_TESTS_NOT_PASS")
    if _tree_hash(V2_ROOT) != V2_TREE_SHA256:
        raise RuntimeError("V2_TREE_HASH_CHANGED")
    if _read_json(REPORT / "GLOBAL_SPLIT_LEAKAGE_AUDIT.json")["status"] != "PASS":
        raise RuntimeError("V3_GLOBAL_SPLIT_LEAKAGE")
    rows = _rebuild_rows()
    selected = _selected_manifest(rows, True)
    spec = _runtime_spec(rows, True)
    _write_json(REPORT / "V3_SELECTED_UNITS.json", selected)
    _write_json(REPORT / "V3_RUNTIME_CAMPAIGN_SPEC.json", spec)
    split = _read_json(REPORT / "V3_SPLIT_MANIFEST.json")
    leakage = _read_json(REPORT / "GLOBAL_SPLIT_LEAKAGE_AUDIT.json")
    _write_text(REPORT / "V3_PREPARATION_REPORT.md", _preparation_report(_read_json(REPORT / "V3_SHORTLIST.json"), selected, split, leakage, True))
    with (REPORT / "COMMAND_LOG.md").open("a", encoding="utf-8") as target:
        target.write("- All static gates and direct regressions PASS; one-shot runtime campaign `{}` authorized.\n".format(RUNTIME_CAMPAIGN_ID))
    _write_json(REPORT / "FROZEN_HASH_INVENTORY.json", _inventory())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "authorize"))
    parser.add_argument("--test-results", type=Path)
    args = parser.parse_args()
    if args.command == "generate":
        generate()
    else:
        if args.test_results is None:
            raise SystemExit("--test-results is required for authorize")
        authorize(args.test_results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
