"""Shared contracts for the authorized multi-unit offline A3/B3 capture.

This module is deliberately standard-library-only.  The GPU worker imports
torch and SimLingo lazily in its own process; CPU tests and batch orchestration
can therefore validate authority, packages, schedules, metrics, schemas, and
artifacts without creating a CUDA context.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .mapper import StaticBranchPlanMapperV1, evaluate_task_pair
from .observation_package import (
    atomic_create_bytes,
    atomic_create_json,
    atomic_replace_json,
    digest_value,
    sha256_path,
)


ROOT = Path("/home/buaa/wrh/DriveClarify")
SIMLINGO = Path("/home/buaa/wrh/simlingo")
AUTHORITY = ROOT / "reports/multi_topology_static_units_v1"
OBSERVATION_BATCH_ID = "DC-OBS-SCREEN-B1-20260803T113000Z"
OBSERVATION_BATCH = AUTHORITY / "observation_screening_runs" / OBSERVATION_BATCH_ID
BATCH_ID = "DC-MULTI-A3B3-C1-20260803T121500Z"
BATCH_ROOT = AUTHORITY / "offline_candidate_capture_runs" / BATCH_ID
RUNS_ROOT = BATCH_ROOT / "runs"
CHECKPOINT = SIMLINGO / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
CONFIG = SIMLINGO / "outputs/simlingo/.hydra/config.yaml"
THRESHOLDS = ROOT / "reports/static_maneuver_branch_primary_mvp_v1/MAPPING_THRESHOLD_PROVENANCE.json"
OLD_M1_SCHEMA = AUTHORITY / "M1_REAL_DATASET_V1_SCHEMA.json"
SELECTED_MANIFEST = AUTHORITY / "SELECTED_STATIC_UNITS_MANIFEST.json"
CHECKPOINT_SHA256 = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
CONFIG_SHA256 = "d56a7c1ebf3b6fd7ff6edff87071f1b2fe7269563e3ea626379994bef7807417"
THRESHOLD_FILE_SHA256 = "6a118500243116315ccd8e0943413fadd4845b171e2ea5b5f858aa1504a1e6fc"
THRESHOLD_SHA256 = "6dfeea8907eb987c512c2b180a824d78c38d7c57662385c24d0d884bf3a37553"
SIMLINGO_HEAD = "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
SIMLINGO_TRACKED_DIFF_BYTES = 7722
SIMLINGO_TRACKED_DIFF_SHA256 = "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34"
SCHEDULE = ("A1", "A2", "A3", "B1", "B2", "B3")
PLAN_FILES = tuple(item + "_PLAN.json" for item in SCHEDULE)

UNIT_RUNS: Tuple[Mapping[str, str], ...] = (
    {"unit_id": "TOWN03_JUNCTION_1221_UNIT01", "primary": "DC-A3B3-T03-J1221-A-20260803T121600Z", "recovery": "DC-A3B3-T03-J1221-B-20260803T123600Z"},
    {"unit_id": "TOWN04_JUNCTION_53_UNIT01", "primary": "DC-A3B3-T04-J53-A-20260803T121700Z", "recovery": "DC-A3B3-T04-J53-B-20260803T123700Z"},
    {"unit_id": "TOWN04_JUNCTION_278_UNIT01", "primary": "DC-A3B3-T04-J278-A-20260803T121800Z", "recovery": "DC-A3B3-T04-J278-B-20260803T123800Z"},
    {"unit_id": "TOWN04_JUNCTION_1452_UNIT01", "primary": "DC-A3B3-T04-J1452-A-20260803T121900Z", "recovery": "DC-A3B3-T04-J1452-B-20260803T123900Z"},
    {"unit_id": "TOWN05_JUNCTION_1574_UNIT01", "primary": "DC-A3B3-T05-J1574-A-20260803T122000Z", "recovery": "DC-A3B3-T05-J1574-B-20260803T124000Z"},
    {"unit_id": "TOWN05_JUNCTION_1722_UNIT01", "primary": "DC-A3B3-T05-J1722-A-20260803T122100Z", "recovery": "DC-A3B3-T05-J1722-B-20260803T124100Z"},
)
ALL_RUN_IDS = tuple(run_id for item in UNIT_RUNS for run_id in (item["primary"], item["recovery"]))


def _configure_m1_v2_from_environment() -> None:
    """Select the authorized V2 authority without changing the Pilot defaults.

    The legacy six-unit path remains byte-for-byte compatible when the V2
    environment is absent.  A V2 worker receives a frozen, campaign-authored
    run-plan path and derives every mutable runtime path from that plan.
    """

    global AUTHORITY, OBSERVATION_BATCH_ID, OBSERVATION_BATCH
    global BATCH_ID, BATCH_ROOT, RUNS_ROOT, OLD_M1_SCHEMA, SELECTED_MANIFEST
    global UNIT_RUNS, ALL_RUN_IDS

    authority_raw = os.environ.get("DRIVECLARIFY_M1_V2_AUTHORITY")
    run_plan_raw = os.environ.get("DRIVECLARIFY_M1_V2_RUN_PLAN")
    if not authority_raw and not run_plan_raw:
        return
    if not authority_raw or not run_plan_raw:
        raise RuntimeError("M1_V2_OFFLINE_CONFIGURATION_INCOMPLETE")
    plan_path = Path(run_plan_raw)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema_version") not in {
        "driveclarify.m1_v2_stage_b_run_plan.v1",
        "driveclarify.m1_v3_stage_b_run_plan.v1",
    }:
        raise RuntimeError("M1_V2_OFFLINE_RUN_PLAN_SCHEMA_MISMATCH")
    AUTHORITY = Path(authority_raw)
    OBSERVATION_BATCH_ID = str(plan["stage_a_batch_id"])
    OBSERVATION_BATCH = Path(plan["stage_a_root"])
    BATCH_ID = str(plan["stage_b_batch_id"])
    BATCH_ROOT = Path(plan["stage_b_root"])
    RUNS_ROOT = BATCH_ROOT / "runs"
    OLD_M1_SCHEMA = ROOT / "reports/multi_topology_static_units_v1/M1_REAL_DATASET_V1_SCHEMA.json"
    SELECTED_MANIFEST = Path(
        plan.get("selected_manifest", AUTHORITY / "M1_V2_SELECTED_UNITS_MANIFEST.json")
    )
    UNIT_RUNS = tuple(dict(item) for item in plan["unit_runs"])
    ALL_RUN_IDS = tuple(
        run_id for item in UNIT_RUNS for run_id in (item["primary"], item["recovery"])
    )


_configure_m1_v2_from_environment()

REQUIRED_RUN_FILES = (
    "AUTHORIZATION_RECEIPT.json", "RUN_RESULT.json", "INPUT_AUTHORITY.json",
    "OBSERVATION_PACKAGE_VERIFICATION.json", "MODEL_CHECKPOINT_IDENTITY.json",
    "CANDIDATE_PAYLOADS.json", "BASELINE_STATE.json", "CANDIDATE_SCHEDULE.json",
    *PLAN_FILES, "FAIRNESS_RESULT.json", "MAPPER_RESULTS.json", "RQ1_RESULT.json",
    "RQ2_RESULT.json", "RUNTIME_COUNTS.json", "GPU_RESOURCE_RECORD.json",
    "PROCESS_CLEANUP.json", "COMMAND_LOG.md", "ARTIFACT_INVENTORY.json",
)


class OfflineCaptureError(RuntimeError):
    """Stable fail-closed error for this authorized batch."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_embedded(path: Path, label: str) -> Mapping[str, Any]:
    value = load_json(path)
    unsigned = dict(value)
    recorded = unsigned.pop("sha256", None)
    if recorded != canonical_sha256(unsigned):
        raise OfflineCaptureError("EMBEDDED_SHA256_MISMATCH:{}:{}".format(label, path))
    return value


def unit_paths(unit_id: str) -> Mapping[str, Path]:
    root = AUTHORITY / "units" / unit_id
    return {
        "root": root,
        "manifest": root / "UNIT_MANIFEST.json",
        "task_binding": root / "TASK_BINDING.json",
        "topology": root / "BRANCH_TOPOLOGY_GROUND_TRUTH.json",
        "mapper_compatibility": root / "MAPPER_COMPATIBILITY.json",
        "eligibility": root / "OBSERVATION_ELIGIBILITY_CONTRACT.json",
        "fixture": root / "SCENARIO_FREE_ROUTE.xml",
    }


def unit_run(run_id: str) -> Tuple[int, Mapping[str, str], str]:
    for index, item in enumerate(UNIT_RUNS):
        if run_id == item["primary"]:
            return index, item, "primary"
        if run_id == item["recovery"]:
            return index, item, "recovery"
    raise OfflineCaptureError("RUN_ID_NOT_AUTHORIZED:" + run_id)


def verify_observation_package(
    manifest_path: Path,
    *,
    expected_unit_id: Optional[str] = None,
    expected_observation_hash: Optional[str] = None,
) -> Mapping[str, Any]:
    """Verify the immutable 14-file package without importing torch."""

    manifest = load_json(manifest_path)
    unsigned = dict(manifest)
    recorded = unsigned.pop("sha256", None)
    reasons: List[str] = []
    if recorded != canonical_sha256(unsigned):
        reasons.append("MANIFEST_EMBEDDED_SHA256_MISMATCH")
    if manifest.get("status") != "COMPLETE_FROZEN_MODEL_READY_OBSERVATION":
        reasons.append("PACKAGE_STATUS_NOT_COMPLETE")
    if manifest.get("file_count") != 14 or len(manifest.get("files", [])) != 14:
        reasons.append("PACKAGE_FILE_COUNT_NOT_14")
    if expected_unit_id is not None and manifest.get("unit_id") != expected_unit_id:
        reasons.append("PACKAGE_UNIT_ID_MISMATCH")
    if expected_observation_hash is not None and manifest.get("observation_hash") != expected_observation_hash:
        reasons.append("PACKAGE_OBSERVATION_HASH_MISMATCH")
    root = Path(str(manifest.get("package_directory", "")))
    rows = []
    seen = set()
    for row in manifest.get("files", []):
        relative = str(row.get("relative_path", ""))
        pure = PurePosixPath(relative)
        if not relative or pure.is_absolute() or ".." in pure.parts or relative in seen:
            reasons.append("PACKAGE_RELATIVE_PATH_INVALID:" + relative)
            continue
        seen.add(relative)
        path = root.joinpath(*pure.parts)
        actual = {
            "relative_path": relative,
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": sha256_path(path) if path.is_file() else None,
        }
        actual["match"] = (
            actual["exists"]
            and actual["bytes"] == row.get("bytes")
            and actual["sha256"] == row.get("sha256")
        )
        if not actual["match"]:
            reasons.append("PACKAGE_FILE_MISMATCH:" + relative)
        rows.append(actual)
    actual_names = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()) if root.is_dir() else []
    if actual_names != sorted(seen):
        reasons.append("PACKAGE_DIRECTORY_MEMBERSHIP_MISMATCH")
    if digest_value(sorted(manifest.get("files", []), key=lambda item: item["relative_path"])) != manifest.get("package_content_sha256"):
        reasons.append("PACKAGE_CONTENT_SHA256_MISMATCH")
    required_roles = {
        "PROCESSED_MODEL_READY_CAMERA_IMAGES", "CAMERA_IMAGE_SIZE_METADATA",
        "CAMERA_INTRINSICS", "CAMERA_EXTRINSICS", "EGO_SPEED_MODEL_INPUT",
        "ROUTE_NAVIGATION_TARGET_POINT", "NORMAL_SIMLINGO_LANGUAGE_INPUT",
        "NORMAL_SIMLINGO_LANGUAGE_INFERENCE_INPUT",
        "EGO_POSE_SPEED_KINEMATICS_AND_WORLD_TO_EGO_TRANSFORM",
        "MODEL_READY_INPUT_SCHEMA_AND_CONTENT_IDENTITIES",
        "ROUTE_NAVIGATION_HLC_AND_CLOSED_LOOP_STATE",
        "PREPROCESSING_CHECKPOINT_CONFIG_AND_AUTHORITY_PROVENANCE",
        "SOURCE_FRAME_SIMULATION_TIMESTAMP_AND_OBSERVATION_INDEX",
        "TOWN_ROUTE_JUNCTION_FRAME_AND_SOURCE_IDENTITY",
    }
    actual_roles = {str(item.get("semantic_role")) for item in manifest.get("files", [])}
    if actual_roles != required_roles:
        reasons.append("PACKAGE_SEMANTIC_ROLES_MISMATCH")
    return {
        "schema_version": "driveclarify.offline_observation_package_verification.v1",
        "status": "PASS" if not reasons else "FAIL",
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": sha256_path(manifest_path),
        "manifest_embedded_sha256": recorded,
        "unit_id": manifest.get("unit_id"),
        "observation_hash": manifest.get("observation_hash"),
        "source_frame": manifest.get("source_frame"),
        "file_count": len(rows),
        "all_bytes_hashes_roles_verified": not reasons,
        "rows": rows,
        "reason_codes": reasons,
    }


def candidate_payloads(task_binding: Mapping[str, Any], speed_mps: float) -> Mapping[str, Any]:
    bindings = task_binding.get("candidate_bindings", {})
    expected = {
        "A": ("STRAIGHT_BRANCH", "straight/lane-follow"),
        "B": ("RIGHT_TURN_BRANCH", "right-turn/branch-taking"),
    }
    prompts = {
        "A": "Current speed: {:.1f} m/s. Command: follow the lane straight through the next junction. What should the ego do next?".format(speed_mps),
        "B": "Current speed: {:.1f} m/s. Command: take the right branch at the next junction. What should the ego do next?".format(speed_mps),
    }
    payload_rows = {}
    for candidate_id, (branch, semantic) in expected.items():
        actual = bindings.get(candidate_id, {})
        if actual.get("required_branch") != branch or actual.get("semantic_payload") != semantic:
            raise OfflineCaptureError("TASK_BINDING_CANDIDATE_MISMATCH:" + candidate_id)
        semantic_payload = {
            "candidate_id": candidate_id,
            "required_branch": branch,
            "semantic_interpretation": semantic,
            "prompt": prompts[candidate_id],
            "injection_positions": ["DrivingInput.prompt", "DrivingInput.prompt_inference"],
            "target_point_policy": "UNCHANGED_FROM_FROZEN_NORMAL_SIMLINGO_INPUT",
        }
        payload_rows[candidate_id] = {
            "semantic_payload": semantic_payload,
            "canonical_serialization_utf8": canonical_bytes(semantic_payload).decode("utf-8"),
            "sha256": canonical_sha256(semantic_payload),
        }
    if payload_rows["A"]["sha256"] == payload_rows["B"]["sha256"]:
        raise OfflineCaptureError("CANDIDATE_PAYLOADS_NOT_DISTINCT")
    return {
        "schema_version": "driveclarify.offline_candidate_payloads.v1",
        "source_authority": "TASK_BINDING.json:candidate_bindings",
        "prompt_template_source": "FROZEN_GLOBAL_MULTI_UNIT_A3B3_TEMPLATE",
        "geometry_dependent_rewording": False,
        "output_dependent_adjustment": False,
        "candidate_swap_allowed": False,
        "normal_input_difference_contract": {
            "changed_fields": ["prompt", "prompt_inference"],
            "unchanged_fields": ["camera_images", "image_sizes", "camera_intrinsics", "camera_extrinsics", "vehicle_speed", "target_point"],
        },
        "candidates": payload_rows,
    }


def normal_prompt_speed(package_root: Path) -> float:
    """Read the already persisted normal prompt and recover its shown speed."""

    schema = load_json(package_root / "metadata/model_ready_input_schema.json")
    strings: List[str] = []
    def visit(value: Any) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
    visit(schema)
    matches = []
    for value in strings:
        found = re.search(r"Current speed:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*m/s", value)
        if found:
            matches.append(float(found.group(1)))
    if not matches or any(item != matches[0] for item in matches):
        raise OfflineCaptureError("NORMAL_PROMPT_SPEED_UNAVAILABLE_OR_INCONSISTENT")
    return matches[0]


def _flat_numeric(value: Any) -> List[float]:
    result: List[float] = []
    def visit(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise OfflineCaptureError("PLAN_NUMERIC_VALUE_INVALID")
        else:
            result.append(float(item))
    visit(value)
    return result


def rms_distance(left: Any, right: Any) -> float:
    a, b = _flat_numeric(left), _flat_numeric(right)
    if not a or len(a) != len(b):
        raise OfflineCaptureError("PLAN_DISTANCE_SHAPE_MISMATCH")
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))


def _within(records: Sequence[Mapping[str, Any]], field: str) -> float:
    return max(rms_distance(records[i][field], records[j][field]) for i in range(len(records)) for j in range(i + 1, len(records)))


def _between(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]], field: str) -> float:
    return min(rms_distance(a[field], b[field]) for a in left for b in right)


def analyze_rq1(plans: Sequence[Mapping[str, Any]], fairness_pass: bool) -> Mapping[str, Any]:
    by_group = {group: [item for item in plans if item.get("candidate_group") == group] for group in ("A", "B")}
    if not fairness_pass or any(len(by_group[group]) != 3 for group in by_group):
        return {
            "schema_version": "driveclarify.offline_rq1.v1", "status": "UNKNOWN",
            "reason_codes": ["FAIRNESS_OR_SIX_COMPLETE_PLANS_UNAVAILABLE"],
            "within": None, "between": None, "ratio": None, "margin": None,
        }
    a_route, b_route = _within(by_group["A"], "raw_route"), _within(by_group["B"], "raw_route")
    a_speed, b_speed = _within(by_group["A"], "raw_speed"), _within(by_group["B"], "raw_speed")
    between_route = _between(by_group["A"], by_group["B"], "raw_route")
    between_speed = _between(by_group["A"], by_group["B"], "raw_speed")
    within_route, within_speed = max(a_route, b_route), max(a_speed, b_speed)
    eps = 1e-12
    route_ratio = between_route / max(within_route, eps)
    speed_ratio = between_speed / max(within_speed, eps)
    distinguishable = between_route > within_route + eps or between_speed > within_speed + eps
    return {
        "schema_version": "driveclarify.offline_rq1.v1",
        "status": "DISTINGUISHABLE" if distinguishable else "NOT_DISTINGUISHABLE",
        "definition": "WITHIN_MAX_PAIRWISE_RMS; BETWEEN_MIN_CROSS_CANDIDATE_RMS",
        "within": {"A_route": a_route, "A_speed": a_speed, "B_route": b_route, "B_speed": b_speed, "route_max": within_route, "speed_max": within_speed},
        "between": {"route_min": between_route, "speed_min": between_speed},
        "ratio": {"route": route_ratio, "speed": speed_ratio},
        "margin": {"route": between_route - within_route, "speed": between_speed - within_speed},
        "repeat_exact_hash_equality": {
            group: {
                "route": len({item["route_plan_hash"] for item in by_group[group]}) == 1,
                "speed": len({item["speed_plan_hash"] for item in by_group[group]}) == 1,
                "combined": len({item["combined_plan_hash"] for item in by_group[group]}) == 1,
            } for group in ("A", "B")
        },
        "reason_codes": ["BETWEEN_EXCEEDS_WITHIN"] if distinguishable else ["BETWEEN_DOES_NOT_EXCEED_WITHIN"],
    }


def analyze_mapper_and_rq2(
    plans: Sequence[Mapping[str, Any]],
    topology: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    task_binding: Mapping[str, Any],
    ego_state: Mapping[str, Any],
    fairness_pass: bool,
) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
    mapper = StaticBranchPlanMapperV1(thresholds)
    pose = ego_state["pose"]
    transform = {
        "source_frame": "CARLA_WORLD",
        "target_frame": "EGO_LOCAL_X_FORWARD_Y_RIGHT",
        "location_xy_world_m": pose["location_xyz"][:2],
        "yaw_degrees": pose["rotation_roll_pitch_yaw_degrees"][2],
        "evidence_status": "VERIFIED",
    }
    results = []
    for item in plans:
        mapper_input = {
            "candidate_id": item["candidate_id"],
            "plan_frame": item["plan_frame"], "plan_unit": item["plan_unit"],
            "frame_evidence_status": item["frame_evidence_status"],
            "unit_evidence_status": item["unit_evidence_status"],
            "topology_sha256": item["topology_sha256"],
            "plan_points": item["plan_points"], "plan_sha256": item["plan_points_sha256"],
        }
        results.append(mapper.map_plan(topology, mapper_input, transform))
    consensus: Dict[str, str] = {}
    for group in ("A", "B"):
        labels = [item["mapping_label"] for item in results if str(item["candidate_id"]).startswith(group)]
        consensus[group] = labels[0] if len(labels) == 3 and len(set(labels)) == 1 else "UNKNOWN"
    mapping_by_candidate = {group: {"mapping_label": consensus[group]} for group in ("A", "B")}
    bindings = {group: task_binding["candidate_bindings"][group]["required_branch"] for group in ("A", "B")}
    if fairness_pass:
        pair = evaluate_task_pair(mapping_by_candidate, bindings, task_binding["branch_task_equivalence_classes"])
    else:
        pair = {"schema_version": "driveclarify.static_branch_task_pair.v1", "candidate_task_status": {"A": "UNKNOWN", "B": "UNKNOWN"}, "mapped_branches": {}, "pair_class": "UNKNOWN", "reason_codes": ["FAIRNESS_NOT_PASS"], "candidate_binding_used": True, "unknown_preserved": True, "control_authorized": False}
        pair["evidence_sha256"] = canonical_sha256(pair)
    mapper_payload = {
        "schema_version": "driveclarify.offline_mapper_results.v1",
        "mapper_name": "StaticBranchPlanMapperV1", "threshold_sha256": thresholds["sha256"],
        "topology_sha256": topology["sha256"], "per_plan": results,
        "candidate_consensus": consensus,
        "candidate_name_default_used": False, "mapper_invocation_count": len(results),
    }
    return mapper_payload, pair


def data_schema() -> Mapping[str, Any]:
    old = load_json(OLD_M1_SCHEMA)
    value = copy.deepcopy(old)
    value["$id"] = "driveclarify.m1_real_dataset_v1.data"
    value["title"] = "M1_REAL_DATASET_V1_DATA"
    value["description"] = "Derived schema for verified real frozen-plan capture; the preparation schema remains immutable."
    value["properties"]["schema_version"] = {"const": "driveclarify.m1_real_dataset_v1.data"}
    value["properties"]["dataset_status"] = {"const": "REAL_PLAN_DATA_CAPTURED"}
    value["$defs"]["record"]["properties"]["speed_plan"] = {
        "description": "Raw speed-head output with its model-emitted one- or two-dimensional numeric structure preserved.",
        "type": ["array", "null"],
        "items": {
            "anyOf": [
                {"type": "number"},
                {"type": "array", "items": {"type": "number"}},
            ]
        },
    }
    value["provenance"] = {"source_schema_path": str(OLD_M1_SCHEMA), "source_schema_sha256": sha256_path(OLD_M1_SCHEMA)}
    return value


def inventory(root: Path, *, exclude: Iterable[str] = ("ARTIFACT_INVENTORY.json",)) -> Mapping[str, Any]:
    excluded = set(exclude)
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_path(path)})
    return {
        "schema_version": "driveclarify.artifact_inventory.v1",
        "root": str(root), "file_count": len(rows),
        "total_bytes": sum(int(item["bytes"]) for item in rows),
        "aggregate_sha256": digest_value(rows), "artifacts": rows,
    }


def append_log(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line.rstrip() + "\n")
        stream.flush()
        os.fsync(stream.fileno())
