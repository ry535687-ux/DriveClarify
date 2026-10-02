"""Source verification and unit-level pair dataset construction.

The source records contain both raw evidence and authority-only fields.  This
module deliberately copies model inputs through an allowlist instead of ever
tensorizing an entire source record.
"""

import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from .config import BATCH_ID, ENGINEERING_EXCLUSION, VALID_UNITS, default_batch_root, project_root


PLAN_IDS = ("A1", "A2", "A3", "B1", "B2", "B3")
KNOWN_TARGETS = ("TASK_EQUIVALENT", "TASK_CRITICAL")

# These names may occur in provenance/targets/audits, but never in tensors.
FORBIDDEN_MODEL_INPUT_FIELDS = frozenset(
    {
        "pair_task_label",
        "candidate_task_status",
        "mapping_label",
        "mapper_consensus",
        "rq1",
        "rq1_verdict",
        "rq2",
        "rq2_verdict",
        "unknown_reason",
        "exclusion_reason",
        "projection_distance",
        "alignment",
        "branch_score",
        "mapper_margin",
        "margin",
        "unit_id",
        "unit_identity",
        "junction",
        "junction_id",
        "route_id",
        "town",
        "file_path",
        "run_id",
        "record_hash",
        "source_record_path",
        "source_record_sha256",
        "plan_hash",
        "observation_hash",
        "topology_hash",
        "fairness_verdict",
    }
)


class DatasetIntegrityError(RuntimeError):
    """Raised when source evidence fails closed."""


class SplitLeakageError(ValueError):
    """Raised when a split would separate repeats from the same unit."""


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def self_seal_sha256(value: Mapping[str, Any]) -> str:
    unsealed = dict(value)
    unsealed.pop("sha256", None)
    return canonical_sha256(unsealed)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise DatasetIntegrityError(reason)


def _all_finite(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, Mapping):
        return all(_all_finite(v) for v in value.values())
    if isinstance(value, Sequence):
        return all(_all_finite(v) for v in value)
    return False


def _rms(values: Iterable[float]) -> float:
    items = list(values)
    return math.sqrt(sum(v * v for v in items) / len(items)) if items else 0.0


def _array_mean_std(arrays: Sequence[Sequence[Sequence[float]]]) -> Tuple[List[List[float]], List[List[float]]]:
    _require(len(arrays) == 3, "REPEAT_AGGREGATION_REQUIRES_THREE_ARRAYS")
    rows = len(arrays[0])
    cols = len(arrays[0][0])
    _require(all(len(a) == rows and all(len(row) == cols for row in a) for a in arrays), "REPEAT_SHAPE_MISMATCH")
    means: List[List[float]] = []
    stds: List[List[float]] = []
    for row_index in range(rows):
        mean_row: List[float] = []
        std_row: List[float] = []
        for column_index in range(cols):
            vals = [float(a[row_index][column_index]) for a in arrays]
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            mean_row.append(mean)
            std_row.append(math.sqrt(variance))
        means.append(mean_row)
        stds.append(std_row)
    return means, stds


def _world_to_ego_xy(points: Sequence[Sequence[float]], matrix: Sequence[Sequence[float]]) -> List[List[float]]:
    _require(len(matrix) == 4 and all(len(row) == 4 for row in matrix), "INVALID_WORLD_TO_EGO_MATRIX")
    converted: List[List[float]] = []
    for point in points:
        _require(len(point) >= 3, "INVALID_TOPOLOGY_POINT")
        hom = [float(point[0]), float(point[1]), float(point[2]), 1.0]
        out = [sum(float(matrix[i][j]) * hom[j] for j in range(4)) for i in range(4)]
        converted.append([out[0], out[1]])
    return converted


def _inventory_map(batch_root: Path) -> Dict[str, Dict[str, Any]]:
    inventory = load_json(batch_root / "ARTIFACT_INVENTORY.json")
    return {item["path"]: item for item in inventory["artifacts"]}


def _source_path(batch_root: Path, run_id: str, plan_id: str) -> Path:
    return batch_root / "runs" / run_id / (plan_id + "_PLAN.json")


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root().resolve()))
    except ValueError:
        return str(path.resolve())


def verify_source_dataset(batch_root: Path = None) -> Dict[str, Any]:
    """Verify the immutable batch and return a compact evidence report."""

    batch_root = Path(batch_root or default_batch_root())
    required = (
        "BATCH_RESULT.json",
        "BATCH_UNIT_SUMMARY.json",
        "VALID_UNITS_MANIFEST.json",
        "SCIENTIFIC_UNKNOWN_UNITS_MANIFEST.json",
        "ENGINEERING_FAILURE_UNITS_MANIFEST.json",
        "M1_REAL_DATASET_V1_DATA_SCHEMA.json",
        "M1_REAL_DATASET_V1.json",
        "M1_REAL_DATASET_V1_UNIT_SUMMARY.json",
        "RQ1_MULTI_UNIT_SUMMARY.json",
        "RQ2_MULTI_UNIT_SUMMARY.json",
        "MAPPER_MULTI_UNIT_SUMMARY.json",
        "FAIRNESS_MULTI_UNIT_SUMMARY.json",
        "BATCH_RUNTIME_COUNTS.json",
        "ARTIFACT_INVENTORY.json",
    )
    _require(batch_root.is_dir(), "BATCH_ROOT_MISSING")
    _require(all((batch_root / name).is_file() for name in required), "REQUIRED_BATCH_ARTIFACT_MISSING")

    batch_result = load_json(batch_root / "BATCH_RESULT.json")
    unit_summary = load_json(batch_root / "BATCH_UNIT_SUMMARY.json")
    valid_manifest = load_json(batch_root / "VALID_UNITS_MANIFEST.json")
    rq2_summary = load_json(batch_root / "RQ2_MULTI_UNIT_SUMMARY.json")
    fairness_summary = load_json(batch_root / "FAIRNESS_MULTI_UNIT_SUMMARY.json")
    runtime_counts = load_json(batch_root / "BATCH_RUNTIME_COUNTS.json")
    dataset = load_json(batch_root / "M1_REAL_DATASET_V1.json")
    schema = load_json(batch_root / "M1_REAL_DATASET_V1_DATA_SCHEMA.json")
    inventory = _inventory_map(batch_root)

    try:
        import jsonschema

        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(dataset, schema)
        schema_status = "PASS"
    except Exception as exc:
        raise DatasetIntegrityError("SOURCE_SCHEMA_VALIDATION_FAILED:%s" % (exc,))

    _require(batch_result["batch_id"] == BATCH_ID, "BATCH_ID_MISMATCH")
    _require(
        batch_result["final_status"] == "MULTI_UNIT_OFFLINE_A3B3_COMPLETE_READY_FOR_LEARNED_M1",
        "BATCH_STATUS_MISMATCH",
    )
    _require(batch_result["m1_record_count"] == 30, "BATCH_RECORD_COUNT_MISMATCH")
    _require(batch_result["valid_unit_count"] == 5, "BATCH_VALID_UNIT_COUNT_MISMATCH")
    _require(tuple(valid_manifest["units"]) == VALID_UNITS, "VALID_UNIT_MANIFEST_MISMATCH")
    _require(len(dataset["records"]) == 30, "SOURCE_RECORD_COUNT_NOT_30")
    _require(runtime_counts["totals"]["candidate_forward"] == 30, "SOURCE_FORWARD_COUNT_MISMATCH")
    _require(runtime_counts["totals"]["training"] == 0, "SOURCE_BATCH_UNEXPECTED_TRAINING")

    summary_by_unit = {item["unit_id"]: item for item in unit_summary["units"]}
    rq2_by_unit = {item["unit_id"]: item["rq2"] for item in rq2_summary["units"]}
    fairness_by_unit = {item["unit_id"]: item["fairness"] for item in fairness_summary["units"]}
    records_by_unit: MutableMapping[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in dataset["records"]:
        records_by_unit[record["unit_identity"]].append(record)
    _require(set(records_by_unit) == set(VALID_UNITS), "DATASET_UNIT_SET_MISMATCH")
    _require(ENGINEERING_EXCLUSION not in records_by_unit, "ENGINEERING_FAILURE_ENTERED_DATASET")

    source_records: List[Dict[str, Any]] = []
    checkpoint_hashes = set()
    config_hashes = set()
    for unit_id in VALID_UNITS:
        records = records_by_unit[unit_id]
        _require(len(records) == 6, "UNIT_RECORD_COUNT_NOT_SIX:%s" % unit_id)
        run_ids = summary_by_unit[unit_id]["used_run_ids"]
        _require(len(run_ids) == 1, "VALID_UNIT_RUN_ID_COUNT_MISMATCH:%s" % unit_id)
        run_id = run_ids[0]
        run_dir = batch_root / "runs" / run_id
        fairness = load_json(run_dir / "FAIRNESS_RESULT.json")
        _require(fairness.get("verdict") == "PASS", "FAIRNESS_GATE_FAILED:%s" % unit_id)
        _require(fairness_by_unit[unit_id] == "PASS", "FAIRNESS_SUMMARY_MISMATCH:%s" % unit_id)
        rq2 = load_json(run_dir / "RQ2_RESULT.json")
        _require(rq2.get("pair_class") == rq2_by_unit[unit_id], "RQ2_AUTHORITY_MISMATCH:%s" % unit_id)
        checkpoint_identity = load_json(run_dir / "MODEL_CHECKPOINT_IDENTITY.json")
        checkpoint_hashes.add(checkpoint_identity["checkpoint_sha256"])
        config_hashes.add(checkpoint_identity["config_sha256"])

        topology_path = project_root() / "reports" / "multi_topology_static_units_v1" / "units" / unit_id / "BRANCH_TOPOLOGY_GROUND_TRUTH.json"
        task_path = topology_path.parent / "TASK_BINDING.json"
        topology = load_json(topology_path)
        task_binding = load_json(task_path)
        _require(self_seal_sha256(topology) == topology["sha256"], "TOPOLOGY_SELF_SEAL_MISMATCH:%s" % unit_id)
        _require(self_seal_sha256(task_binding) == task_binding["sha256"], "TASK_BINDING_SELF_SEAL_MISMATCH:%s" % unit_id)
        _require(task_binding["topology_sha256"] == topology["sha256"], "TASK_TOPOLOGY_BINDING_MISMATCH:%s" % unit_id)

        seen_plan_ids = set()
        observation_ids = set()
        observation_hashes = set()
        payload_hashes: MutableMapping[str, set] = defaultdict(set)
        for record in records:
            group = record["candidate_semantic_payload"]["candidate_id"]
            repeat_index = int(record["runtime_metadata"]["repeat_index"])
            plan_id = "%s%d" % (group, repeat_index)
            seen_plan_ids.add(plan_id)
            source_path = _source_path(batch_root, run_id, plan_id)
            _require(source_path.is_file(), "SOURCE_PLAN_MISSING:%s" % plan_id)
            plan = load_json(source_path)
            relative_to_batch = str(source_path.relative_to(batch_root))
            _require(relative_to_batch in inventory, "SOURCE_PLAN_NOT_IN_INVENTORY:%s" % plan_id)
            source_sha = sha256_file(source_path)
            _require(source_sha == inventory[relative_to_batch]["sha256"], "SOURCE_PLAN_HASH_MISMATCH:%s" % plan_id)
            _require(plan["completion_status"] == "COMPLETE", "PLAN_NOT_COMPLETE:%s" % plan_id)
            _require(plan["candidate_id"] == plan_id, "PLAN_ID_MISMATCH:%s" % plan_id)
            _require(plan["candidate_group"] == group, "CANDIDATE_GROUP_MISMATCH:%s" % plan_id)
            _require(plan["repeat_index"] == repeat_index, "REPEAT_INDEX_MISMATCH:%s" % plan_id)
            _require(plan["fairness_status"] == "PASS", "PLAN_FAIRNESS_NOT_PASS:%s" % plan_id)
            _require(plan["frame_evidence_status"] == "VERIFIED", "PLAN_FRAME_NOT_VERIFIED:%s" % plan_id)
            _require(all(value == 0 for value in plan["finite_value_checks"].values()), "PLAN_NONFINITE:%s" % plan_id)
            _require(_all_finite(record["route_plan"]) and _all_finite(record["speed_plan"]), "DATASET_NONFINITE:%s" % plan_id)
            _require(record["route_plan"] == plan["plan_points"], "ROUTE_PLAN_PAYLOAD_MISMATCH:%s" % plan_id)
            _require(record["speed_plan"] == plan["raw_speed"][0], "SPEED_PLAN_PAYLOAD_MISMATCH:%s" % plan_id)
            _require(record["plan_evidence"]["combined_hash"] == plan["combined_plan_hash"], "COMBINED_PLAN_HASH_MISMATCH:%s" % plan_id)
            _require(record["plan_evidence"]["route_hash"] == plan["route_plan_hash"], "ROUTE_PLAN_HASH_MISMATCH:%s" % plan_id)
            _require(record["plan_evidence"]["speed_hash"] == plan["speed_plan_hash"], "SPEED_PLAN_HASH_MISMATCH:%s" % plan_id)
            _require(record["candidate_payload_hash"] == plan["semantic_payload_hash"], "PAYLOAD_HASH_MISMATCH:%s" % plan_id)
            _require(record["observation_hash"] == plan["observation_hash"], "OBSERVATION_HASH_MISMATCH:%s" % plan_id)
            _require(record["observation_identity"] == plan["observation_identity"], "OBSERVATION_IDENTITY_MISMATCH:%s" % plan_id)
            _require(record["topology_sha256"] == topology["sha256"], "RECORD_TOPOLOGY_MISMATCH:%s" % plan_id)
            _require(record["pair_task_label"] == rq2_by_unit[unit_id], "RECORD_TARGET_MISMATCH:%s" % plan_id)
            _require(plan["checkpoint_sha256"] == checkpoint_identity["checkpoint_sha256"], "CHECKPOINT_MISMATCH:%s" % plan_id)
            _require(plan["config_sha256"] == checkpoint_identity["config_sha256"], "CONFIG_MISMATCH:%s" % plan_id)
            observation_ids.add(plan["observation_identity"])
            observation_hashes.add(plan["observation_hash"])
            payload_hashes[group].add(plan["semantic_payload_hash"])
            source_records.append(
                {
                    "unit_id": unit_id,
                    "candidate_id": group,
                    "repeat_index": repeat_index,
                    "plan_id": plan_id,
                    "source_record_path": _repo_relative(source_path),
                    "source_record_sha256": source_sha,
                    "combined_plan_hash": plan["combined_plan_hash"],
                    "route_plan_hash": plan["route_plan_hash"],
                    "speed_plan_hash": plan["speed_plan_hash"],
                    "observation_hash": plan["observation_hash"],
                    "topology_hash": topology["sha256"],
                }
            )
        _require(seen_plan_ids == set(PLAN_IDS), "PLAN_COMPLETENESS_MISMATCH:%s" % unit_id)
        _require(len(observation_ids) == len(observation_hashes) == 1, "OBSERVATION_INCONSISTENT:%s" % unit_id)
        _require(set(payload_hashes) == {"A", "B"} and all(len(v) == 1 for v in payload_hashes.values()), "PAYLOAD_INCONSISTENT:%s" % unit_id)

    _require(len(checkpoint_hashes) == 1, "CROSS_UNIT_CHECKPOINT_MISMATCH")
    _require(len(config_hashes) == 1, "CROSS_UNIT_CONFIG_MISMATCH")
    source_dataset_path = batch_root / "M1_REAL_DATASET_V1.json"
    return {
        "status": "PASS",
        "batch_id": BATCH_ID,
        "batch_status": batch_result["final_status"],
        "source_dataset_path": _repo_relative(source_dataset_path),
        "source_dataset_sha256": sha256_file(source_dataset_path),
        "source_schema_path": _repo_relative(batch_root / "M1_REAL_DATASET_V1_DATA_SCHEMA.json"),
        "source_schema_sha256": sha256_file(batch_root / "M1_REAL_DATASET_V1_DATA_SCHEMA.json"),
        "schema_validation": schema_status,
        "record_count": len(source_records),
        "valid_unit_count": len(VALID_UNITS),
        "records_per_valid_unit": 6,
        "valid_units": list(VALID_UNITS),
        "candidate_schedule": list(PLAN_IDS),
        "label_distribution_valid_units": dict(Counter(rq2_by_unit[u] for u in VALID_UNITS)),
        "engineering_exclusion": ENGINEERING_EXCLUSION,
        "checkpoint_sha256": next(iter(checkpoint_hashes)),
        "config_sha256": next(iter(config_hashes)),
        "source_records": sorted(source_records, key=lambda x: (VALID_UNITS.index(x["unit_id"]), PLAN_IDS.index(x["plan_id"]))),
        "authority_checks": {
            "batch_status": "PASS",
            "inventory_hashes": "PASS",
            "source_schema": "PASS",
            "plan_payloads": "PASS",
            "candidate_payload_repeat_consistency": "PASS",
            "observation_identity_consistency": "PASS",
            "checkpoint_config_consistency": "PASS",
            "fairness_hard_gate": "PASS",
            "topology_task_self_seals": "PASS",
            "rq2_target_alignment": "PASS",
        },
    }


def _candidate_aggregate(plans: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    routes = [p["route_plan"] for p in plans]
    speeds = [p["speed_plan"] for p in plans]
    route_mean, route_std = _array_mean_std(routes)
    speed_mean, speed_std = _array_mean_std(speeds)
    return {
        "route_mean": route_mean,
        "route_std": route_std,
        "speed_mean": speed_mean,
        "speed_std": speed_std,
        "within_route_rms": _rms(value for row in route_std for value in row),
        "within_speed_rms": _rms(value for row in speed_std for value in row),
    }


def build_pair_dataset(batch_root: Path = None) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Construct exactly five pair samples plus verification/exclusion records."""

    batch_root = Path(batch_root or default_batch_root())
    verification = verify_source_dataset(batch_root)
    source = load_json(batch_root / "M1_REAL_DATASET_V1.json")
    unit_summary = load_json(batch_root / "BATCH_UNIT_SUMMARY.json")
    run_by_unit = {u["unit_id"]: u["used_run_ids"][0] for u in unit_summary["units"] if u["unit_id"] in VALID_UNITS}
    records_by_unit: MutableMapping[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in source["records"]:
        records_by_unit[record["unit_identity"]].append(record)
    source_provenance = defaultdict(list)
    for item in verification["source_records"]:
        source_provenance[item["unit_id"]].append(item)

    samples: List[Dict[str, Any]] = []
    for unit_id in VALID_UNITS:
        records = sorted(
            records_by_unit[unit_id],
            key=lambda r: (r["candidate_semantic_payload"]["candidate_id"], int(r["runtime_metadata"]["repeat_index"])),
        )
        run_id = run_by_unit[unit_id]
        run_dir = batch_root / "runs" / run_id
        topology_path = project_root() / "reports" / "multi_topology_static_units_v1" / "units" / unit_id / "BRANCH_TOPOLOGY_GROUND_TRUTH.json"
        task_path = topology_path.parent / "TASK_BINDING.json"
        topology = load_json(topology_path)
        task = load_json(task_path)
        world_to_ego = records[0]["ego_pose"]["world_to_ego_matrix_4x4"]
        branches = {branch["semantic_role"]: branch["evaluation_polyline_world_xyz"] for branch in topology["branches"]}
        straight_ego = _world_to_ego_xy(branches["STRAIGHT_BRANCH"], world_to_ego)
        right_ego = _world_to_ego_xy(branches["RIGHT_TURN_BRANCH"], world_to_ego)
        decision_ego = _world_to_ego_xy([topology["decision_point"]["xyz"]], world_to_ego)[0]
        candidates: Dict[str, Any] = {}
        aggregates: Dict[str, Any] = {}
        for group, role in (("A", [1.0, 0.0]), ("B", [0.0, 1.0])):
            group_records = [r for r in records if r["candidate_semantic_payload"]["candidate_id"] == group]
            plans = []
            for record in group_records:
                repeat_index = int(record["runtime_metadata"]["repeat_index"])
                provenance = next(p for p in source_provenance[unit_id] if p["candidate_id"] == group and p["repeat_index"] == repeat_index)
                plans.append(
                    {
                        "candidate_id": group,
                        "repeat_index": repeat_index,
                        "route_plan": record["route_plan"],
                        "speed_plan": record["speed_plan"],
                        "route_shape": [20, 2],
                        "speed_shape": [10, 2],
                        "route_valid_mask": [1] * 20,
                        "speed_valid_mask": [1] * 10,
                        "finite": True,
                        "source_record_path": provenance["source_record_path"],
                        "source_record_sha256": provenance["source_record_sha256"],
                        "combined_plan_hash": provenance["combined_plan_hash"],
                        "route_plan_hash": provenance["route_plan_hash"],
                        "speed_plan_hash": provenance["speed_plan_hash"],
                        "observation_hash": provenance["observation_hash"],
                        "topology_hash": provenance["topology_hash"],
                    }
                )
            candidates[group] = {
                "candidate_id": group,
                "semantic_role": task["candidate_bindings"][group]["required_branch"],
                "semantic_role_one_hot": role,
                "candidate_payload_hash": group_records[0]["candidate_payload_hash"],
                "plans": plans,
            }
            aggregates[group] = _candidate_aggregate(plans)
        between_route = _rms(
            aggregates["A"]["route_mean"][i][j] - aggregates["B"]["route_mean"][i][j]
            for i in range(20)
            for j in range(2)
        )
        between_speed = _rms(
            aggregates["A"]["speed_mean"][i][j] - aggregates["B"]["speed_mean"][i][j]
            for i in range(10)
            for j in range(2)
        )
        target = records[0]["pair_task_label"]
        sample = {
            "schema_version": "driveclarify.learned_m1_pair_sample.pilot.v1",
            "unit_provenance": {
                "source_unit": unit_id,
                "source_run_id": run_id,
                "source_batch_id": BATCH_ID,
                "source_record_count": 6,
                "source_record_paths": [p["source_record_path"] for p in source_provenance[unit_id]],
                "source_record_sha256": [p["source_record_sha256"] for p in source_provenance[unit_id]],
            },
            "observation": {
                "identity": records[0]["observation_identity"],
                "hash": records[0]["observation_hash"],
                "source_frame": records[0]["source_frame"],
                "plan_frame": records[0]["plan_frame"],
            },
            "candidates": candidates,
            "repeat_aggregation": aggregates,
            "between_candidate_variation": {
                "route_mean_rms": between_route,
                "speed_mean_rms": between_speed,
            },
            "frozen_topology_task_context": {
                "straight_centerline_ego_m": straight_ego,
                "right_centerline_ego_m": right_ego,
                "centerline_valid_mask": [1] * 25,
                "decision_point_ego_m": decision_ego,
                "evaluation_interval_m": [
                    float(topology["evaluation_interval"]["start_arc_length_from_decision_point_m"]),
                    float(topology["evaluation_interval"]["end_arc_length_from_decision_point_m"]),
                    float(topology["evaluation_interval"]["length_m"]),
                ],
                "branch_relative_geometry_m": [
                    float(topology["branch_divergence_point"]["arc_length_from_decision_point_m"]),
                    float(topology["branch_divergence_point"]["actual_centerline_separation_m"]),
                    float(topology["branch_divergence_point"]["topology_separation_threshold_m"]),
                ],
                "topology_source_path": _repo_relative(topology_path),
                "topology_source_sha256": sha256_file(topology_path),
                "topology_self_seal": topology["sha256"],
                "task_binding_source_path": _repo_relative(task_path),
                "task_binding_source_sha256": sha256_file(task_path),
                "task_binding_self_seal": task["sha256"],
            },
            # FAIRNESS_RESULT is used above only as a hard gate.  It is absent
            # from this learned evidence vector by construction.
            "low_level_evidence_mask": {
                "plans_available": 1.0,
                "route_finite": 1.0,
                "speed_finite": 1.0,
                "frame_available": 1.0,
                "observation_available": 1.0,
                "topology_task_available": 1.0,
            },
            "hard_evidence_gate": {
                "status": "PASS",
                "fairness_used_only_as_gate": True,
                "failure_reasons": [],
            },
            "pair_target": target,
            "target_available": target in KNOWN_TARGETS,
            "label_provenance": {
                "authority": _repo_relative(run_dir / "RQ2_RESULT.json"),
                "authority_sha256": sha256_file(run_dir / "RQ2_RESULT.json"),
                "batch_summary": _repo_relative(batch_root / "RQ2_MULTI_UNIT_SUMMARY.json"),
                "use": "TARGET_ONLY_NOT_MODEL_INPUT",
            },
            "exclusion_provenance": None,
        }
        _require(_all_finite(sample["frozen_topology_task_context"]["straight_centerline_ego_m"]), "NONFINITE_STRAIGHT_TOPOLOGY:%s" % unit_id)
        _require(_all_finite(sample["frozen_topology_task_context"]["right_centerline_ego_m"]), "NONFINITE_RIGHT_TOPOLOGY:%s" % unit_id)
        samples.append(sample)

    exclusion_source = load_json(batch_root / "ENGINEERING_FAILURE_UNITS_MANIFEST.json")
    exclusions = {
        "schema_version": "driveclarify.learned_m1_engineering_exclusions.pilot.v1",
        "count": 1,
        "units": [
            {
                "unit_id": ENGINEERING_EXCLUSION,
                "category": "ENGINEERING_FAILURE_EXCLUSION",
                "plan_record_count": 0,
                "candidate_forward_count": 0,
                "included_in_pair_dataset": False,
                "synthetic_plan_created": False,
                "treated_as_scientific_unknown": False,
                "source_manifest": _repo_relative(batch_root / "ENGINEERING_FAILURE_UNITS_MANIFEST.json"),
                "source_manifest_sha256": sha256_file(batch_root / "ENGINEERING_FAILURE_UNITS_MANIFEST.json"),
                "exclusion_provenance": exclusion_source["units"][0],
            }
        ],
    }
    pair_dataset = {
        "schema_version": "driveclarify.learned_m1_pair_dataset.pilot.v1",
        "source_batch_id": BATCH_ID,
        "sample_count": len(samples),
        "independent_scientific_sample_count": len(samples),
        "source_plan_record_count": 30,
        "label_distribution": dict(Counter(sample["pair_target"] for sample in samples)),
        "repeats_are_measurements_not_samples": True,
        "samples": samples,
    }
    pair_dataset["dataset_sha256"] = canonical_sha256(pair_dataset)
    _require(len(samples) == 5, "PAIR_DATASET_SAMPLE_COUNT_NOT_FIVE")
    return pair_dataset, exclusions, verification


def make_loou_manifest(samples: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    units = [sample["unit_provenance"]["source_unit"] for sample in samples]
    _require(len(units) == len(set(units)) == 5, "LOOU_REQUIRES_FIVE_UNIQUE_UNITS")
    folds = []
    for index, held_out in enumerate(units, start=1):
        train_units = [unit for unit in units if unit != held_out]
        held_target = next(s["pair_target"] for s in samples if s["unit_provenance"]["source_unit"] == held_out)
        train_targets = [s["pair_target"] for s in samples if s["unit_provenance"]["source_unit"] in train_units]
        missing = []
        for target in ("TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN"):
            if target not in train_targets:
                missing.append(target)
        status = "INSUFFICIENT_TRAIN_CLASS_COVERAGE" if missing else "DIAGNOSTIC_ONLY_NOT_PAPER_CLAIM"
        folds.append(
            {
                "fold_id": "LOOU_%02d" % index,
                "train_units": train_units,
                "held_out_unit": held_out,
                "held_out_target": held_target,
                "train_target_distribution": dict(Counter(train_targets)),
                "missing_train_classes": missing,
                "fold_status": status,
                "repeats_move_with_unit": True,
            }
        )
    manifest = {
        "schema_version": "driveclarify.learned_m1_split_manifest.pilot.v1",
        "mode_full_overfit": {"train_units": units, "evaluation_use": "OPTIMIZATION_PIPELINE_ONLY_NO_GENERALIZATION_CLAIM"},
        "mode_loou": {"fold_count": 5, "folds": folds, "use": "DIAGNOSTIC_ONLY_NOT_PAPER_CLAIM"},
        "record_level_random_split": False,
        "repeat_level_random_split": False,
    }
    manifest["split_sha256"] = canonical_sha256(manifest)
    return manifest


def validate_unit_grouped_split(assignments: Sequence[Mapping[str, Any]]) -> None:
    """Reject any assignment that puts records/repeats from one unit in multiple splits."""

    by_unit: MutableMapping[str, set] = defaultdict(set)
    for assignment in assignments:
        if "record_id" in assignment or "repeat_index" in assignment:
            raise SplitLeakageError("RECORD_OR_REPEAT_LEVEL_SPLIT_FORBIDDEN")
        by_unit[str(assignment["unit_id"])].add(str(assignment["split"]))
    leaking = [unit for unit, splits in by_unit.items() if len(splits) != 1]
    if leaking:
        raise SplitLeakageError("SAME_UNIT_SPLIT_LEAKAGE:%s" % ",".join(sorted(leaking)))
