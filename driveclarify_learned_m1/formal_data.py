"""Read-only Formal Learned M1 V2+V3 dataset adapter and integrity audit.

This module deliberately separates model inputs from targets and control-plane
metadata.  Identifiers, splits, labels, paths, mapper results, and provenance
may be used to validate or locate frozen evidence, but they are never copied
into ``model_inputs``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

import torch
from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = REPO_ROOT / "reports/m1_real_dataset_expansion_v2/DC-M1-DATASET-EXP-V2-20260803T143000Z"
V3_ROOT = REPO_ROOT / "reports/m1_real_dataset_expansion_v3/DC-M1-DATASET-EXP-V3-20260804T055600Z"
V2_DATASET = V2_ROOT / "combined_runtime_campaigns/DC-M1-V2-RUNTIME-C1-20260803T144700Z/stage_b/M1_REAL_DATASET_V2.json"
V3_DATASET = V3_ROOT / "M1_REAL_DATASET_V3.json"
V2_SCHEMA = V2_ROOT / "M1_REAL_DATASET_V2_DATA_SCHEMA.json"
V3_SCHEMA = V3_ROOT / "M1_REAL_DATASET_V3_SCHEMA.json"
PILOT_MANIFEST = V2_ROOT / "PILOT_UNITS_DEVELOPMENT_ONLY_MANIFEST.json"
GLOBAL_LEAKAGE_AUDIT = V3_ROOT / "GLOBAL_SPLIT_LEAKAGE_AUDIT.json"
PLAN_IDS = ("A1", "A2", "A3", "B1", "B2", "B3")
FORMAL_STATUSES = {"V2": "V2_NEW", "V3": "V3_NEW"}
FORMAL_SPLITS = ("TRAIN", "DEV", "TEST")
TASK_TO_INDEX = {"TASK_EQUIVALENT": 0, "TASK_CRITICAL": 1}
EXPECTED_V2_TREE_SHA256 = "31dc8aaf2774f46fa47dd2dfe05832ca85c6797947efddbef418dbab446f5843"

MODEL_INPUT_KEYS = (
    "route",
    "route_mask",
    "speed",
    "speed_mask",
    "roles",
    "topology",
    "topology_mask",
    "topology_scalars",
    "evidence",
)

# Exact control-plane/source paths that are permitted to influence model input
# values.  Derived masks are true only after shape and finiteness validation.
MODEL_INPUT_SOURCE_PATHS = (
    "records[].plans[].plan_points",
    "records[].plans[].raw_speed[0]",
    "records[].plans[].semantic_payload.required_branch",
    "units/<unit>/BRANCH_TOPOLOGY_GROUND_TRUTH.json:branches[].evaluation_polyline_world_xyz",
    "units/<unit>/BRANCH_TOPOLOGY_GROUND_TRUTH.json:decision_point.xyz",
    "units/<unit>/BRANCH_TOPOLOGY_GROUND_TRUTH.json:evaluation_interval",
    "units/<unit>/BRANCH_TOPOLOGY_GROUND_TRUTH.json:branch_divergence_point",
    "observation_package/metadata/ego_state.json:pose.world_to_ego_matrix_4x4",
    "derived:validated_route_mask",
    "derived:validated_speed_mask",
    "derived:validated_topology_mask",
    "derived:low_level_evidence_availability",
)

FORMAL_FORBIDDEN_MODEL_INPUT_FIELDS = frozenset(
    {
        "pair_task_label",
        "task_equivalent",
        "task_critical",
        "unknown_label",
        "expected_label",
        "final_label",
        "scientific_decision",
        "oracle_decision",
        "mapper_consensus",
        "mapper_result",
        "mapping_label",
        "rq1",
        "rq1_result",
        "rq1_verdict",
        "rq2",
        "rq2_result",
        "rq2_verdict",
        "split",
        "test_split",
        "unit_id",
        "unit_identity",
        "town",
        "junction",
        "junction_id",
        "junction_group",
        "route_id",
        "route_family",
        "file_path",
        "source_path",
        "checkpoint_path",
        "config_path",
        "package_directory",
        "exclusion_reason",
        "engineering_exclusion",
        "experiment_id",
        "campaign_id",
        "run_id",
        "report_verdict",
        "test_identity",
        "label_provenance",
        "unknown_provenance",
        "data_generation_version",
    }
)


class FormalDatasetIntegrityError(RuntimeError):
    """A frozen formal-data invariant failed and must stop downstream work."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise FormalDatasetIntegrityError(reason)


def _json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in Path(root).rglob("*") if item.is_file()):
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"  ")
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _self_seal_sha256(value: Mapping[str, Any]) -> str:
    unsealed = dict(value)
    unsealed.pop("sha256", None)
    return canonical_sha256(unsealed)


def _all_finite(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, Mapping):
        return all(_all_finite(item) for item in value.values())
    if isinstance(value, Sequence):
        return all(_all_finite(item) for item in value)
    return False


def _exact_matrix(value: Any, rows: int, columns: int, reason: str) -> None:
    _require(isinstance(value, list) and len(value) == rows, reason)
    _require(all(isinstance(row, list) and len(row) == columns for row in value), reason)
    _require(_all_finite(value), reason)


def _bounded_matrix(value: Any, minimum_rows: int, maximum_rows: int, columns: int, reason: str) -> None:
    _require(isinstance(value, list) and minimum_rows <= len(value) <= maximum_rows, reason)
    _require(all(isinstance(row, list) and len(row) == columns for row in value), reason)
    _require(_all_finite(value), reason)


def _masked_pad_points(points: Sequence[Sequence[float]], size: int) -> Tuple[List[List[float]], List[bool]]:
    """Pad with the final valid point; the mask makes padding non-evidence."""

    _require(bool(points) and len(points) <= size, "TOPOLOGY_PADDING_INPUT_INVALID")
    copied = [[float(value) for value in point] for point in points]
    mask = [True] * len(copied) + [False] * (size - len(copied))
    copied.extend([list(copied[-1]) for _ in range(size - len(copied))])
    return copied, mask


def _repo_contained(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        raise FormalDatasetIntegrityError("PATH_OUTSIDE_REPOSITORY")
    return resolved


def _world_to_ego_xy(points: Sequence[Sequence[float]], matrix: Sequence[Sequence[float]]) -> List[List[float]]:
    _exact_matrix(matrix, 4, 4, "INVALID_WORLD_TO_EGO_MATRIX")
    converted: List[List[float]] = []
    for point in points:
        _require(isinstance(point, list) and len(point) >= 3 and _all_finite(point), "INVALID_TOPOLOGY_POINT")
        hom = [float(point[0]), float(point[1]), float(point[2]), 1.0]
        out = [sum(float(matrix[i][j]) * hom[j] for j in range(4)) for i in range(4)]
        converted.append([out[0], out[1]])
    return converted


def _version_root(version: str) -> Path:
    if version == "V2":
        return V2_ROOT
    if version == "V3":
        return V3_ROOT
    raise FormalDatasetIntegrityError("UNKNOWN_FORMAL_DATASET_VERSION")


def load_formal_records() -> List[Dict[str, Any]]:
    """Load all 47 formal records; Pilot and exclusions remain excluded."""

    loaded: List[Dict[str, Any]] = []
    for version, path in (("V2", V2_DATASET), ("V3", V3_DATASET)):
        dataset = _json(path)
        for record in dataset["records"]:
            if record.get("unit_status") == FORMAL_STATUSES[version]:
                copied = dict(record)
                copied["_formal_source_version"] = version
                loaded.append(copied)
    _require(len(loaded) == 47, "FORMAL_UNIT_COUNT_NOT_47")
    _require(len({item["unit_id"] for item in loaded}) == 47, "FORMAL_UNIT_ID_NOT_UNIQUE")
    return loaded


def load_formal_train_dev_records() -> List[Dict[str, Any]]:
    """Load only TRAIN/DEV records for the authorized training stage.

    The frozen datasets are monolithic JSON documents, so their bytes are
    parsed once, but TEST records are discarded before copying, validation,
    feature construction, or tensorization.  Callers cannot request TEST
    through this entry point.
    """

    loaded: List[Dict[str, Any]] = []
    for version, path in (("V2", V2_DATASET), ("V3", V3_DATASET)):
        dataset = _json(path)
        for record in dataset["records"]:
            if record.get("unit_status") != FORMAL_STATUSES[version]:
                continue
            if record.get("split") not in {"TRAIN", "DEV"}:
                continue
            copied = dict(record)
            copied["_formal_source_version"] = version
            loaded.append(copied)
    _require(len(loaded) == 36, "FORMAL_TRAIN_DEV_UNIT_COUNT_NOT_36")
    counts = Counter(item["split"] for item in loaded)
    _require(counts == Counter({"TRAIN": 25, "DEV": 11}), "FORMAL_TRAIN_DEV_SPLIT_MISMATCH")
    return loaded


def _validated_unit_parts(record: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return a sanitized input sample and a physically separate target record."""

    version = str(record.get("_formal_source_version"))
    _require(record.get("unit_status") == FORMAL_STATUSES.get(version), "FORMAL_STATUS_VERSION_MISMATCH")
    _require(record.get("split") in FORMAL_SPLITS, "INVALID_FORMAL_SPLIT")
    _require(record.get("plans") is not None, "FORMAL_PLANS_MISSING_FAIL_CLOSED")
    plans = list(record["plans"])
    _require(len(plans) == 6, "FORMAL_PLAN_COUNT_NOT_SIX")
    _require([item.get("candidate_id") for item in plans] == list(PLAN_IDS), "FORMAL_PLAN_SCHEDULE_MISMATCH")

    candidates: Dict[str, Any] = {}
    for group, role, one_hot in (
        ("A", "STRAIGHT_BRANCH", [1.0, 0.0]),
        ("B", "RIGHT_TURN_BRANCH", [0.0, 1.0]),
    ):
        group_plans = [item for item in plans if item.get("candidate_group") == group]
        _require([item.get("repeat_index") for item in group_plans] == [1, 2, 3], "FORMAL_REPEAT_SET_INCOMPLETE")
        sanitized_plans = []
        for plan in group_plans:
            _require(plan.get("completion_status") == "COMPLETE", "FORMAL_PLAN_NOT_COMPLETE")
            _require(plan.get("frame_evidence_status") == "VERIFIED", "FORMAL_PLAN_FRAME_UNVERIFIED")
            _require(plan.get("fairness_status") == "PASS", "FORMAL_PLAN_FAIRNESS_GATE_FAILED")
            _require(plan.get("plan_frame") == "EGO_LOCAL_X_FORWARD_Y_RIGHT", "FORMAL_PLAN_FRAME_MISMATCH")
            _require(plan.get("plan_unit") == "METRE", "FORMAL_PLAN_UNIT_MISMATCH")
            _require(plan.get("route_shape") == [1, 20, 2], "FORMAL_ROUTE_DECLARED_SHAPE_MISMATCH")
            _require(plan.get("speed_shape") == [1, 10, 2], "FORMAL_SPEED_DECLARED_SHAPE_MISMATCH")
            _require(plan.get("route_original_dtype") == "torch.bfloat16", "FORMAL_ROUTE_DTYPE_MISMATCH")
            _require(plan.get("speed_original_dtype") == "torch.bfloat16", "FORMAL_SPEED_DTYPE_MISMATCH")
            _require(plan.get("route_persisted_dtype") == "JSON_NUMBER_EXACT_FROM_TORCH_TOLIST", "FORMAL_ROUTE_PERSISTED_DTYPE_MISMATCH")
            _require(plan.get("speed_persisted_dtype") == "JSON_NUMBER_EXACT_FROM_TORCH_TOLIST", "FORMAL_SPEED_PERSISTED_DTYPE_MISMATCH")
            _require(plan.get("semantic_payload", {}).get("required_branch") == role, "FORMAL_CANDIDATE_ROLE_MISMATCH")
            route = plan.get("plan_points")
            raw_speed = plan.get("raw_speed")
            _exact_matrix(route, 20, 2, "FORMAL_ROUTE_PAYLOAD_INVALID")
            _require(isinstance(raw_speed, list) and len(raw_speed) == 1, "FORMAL_SPEED_BATCH_SHAPE_INVALID")
            _exact_matrix(raw_speed[0], 10, 2, "FORMAL_SPEED_PAYLOAD_INVALID")
            _require(all(int(value) == 0 for value in plan.get("finite_value_checks", {}).values()), "FORMAL_NONFINITE_COUNTER")
            sanitized_plans.append(
                {
                    "route_plan": route,
                    "route_valid_mask": [True] * 20,
                    "speed_plan": raw_speed[0],
                    "speed_valid_mask": [True] * 10,
                }
            )
        candidates[group] = {"semantic_role_one_hot": one_hot, "plans": sanitized_plans}

    unit_id = str(record["unit_id"])
    unit_root = _version_root(version) / "units" / unit_id
    topology_path = unit_root / "BRANCH_TOPOLOGY_GROUND_TRUTH.json"
    topology = _json(topology_path)
    _require(_self_seal_sha256(topology) == topology.get("sha256"), "FORMAL_TOPOLOGY_SELF_SEAL_MISMATCH")
    observation = record.get("observation_package") or {}
    _require(observation.get("topology_sha256") == topology.get("sha256"), "FORMAL_OBSERVATION_TOPOLOGY_BINDING_MISMATCH")
    package_directory = _repo_contained(Path(str(observation.get("package_directory", ""))))
    ego_state_path = package_directory / "metadata/ego_state.json"
    _require(ego_state_path.is_file(), "FORMAL_EGO_STATE_MISSING")
    ego_state = _json(ego_state_path)
    matrix = ego_state.get("pose", {}).get("world_to_ego_matrix_4x4")

    branch_by_role = {branch["semantic_role"]: branch for branch in topology.get("branches", [])}
    _require(set(branch_by_role) == {"STRAIGHT_BRANCH", "RIGHT_TURN_BRANCH"}, "FORMAL_TOPOLOGY_ROLE_SET_MISMATCH")
    straight_world = branch_by_role["STRAIGHT_BRANCH"].get("evaluation_polyline_world_xyz")
    right_world = branch_by_role["RIGHT_TURN_BRANCH"].get("evaluation_polyline_world_xyz")
    _bounded_matrix(straight_world, 2, 25, 3, "FORMAL_STRAIGHT_TOPOLOGY_SHAPE_MISMATCH")
    _bounded_matrix(right_world, 2, 25, 3, "FORMAL_RIGHT_TOPOLOGY_SHAPE_MISMATCH")
    decision_world = topology.get("decision_point", {}).get("xyz")
    _require(isinstance(decision_world, list) and len(decision_world) == 3, "FORMAL_DECISION_POINT_MISSING")
    straight_ego_raw = _world_to_ego_xy(straight_world, matrix)
    right_ego_raw = _world_to_ego_xy(right_world, matrix)
    straight_ego, straight_mask = _masked_pad_points(straight_ego_raw, 25)
    right_ego, right_mask = _masked_pad_points(right_ego_raw, 25)
    decision_ego = _world_to_ego_xy([decision_world], matrix)[0]
    interval = topology.get("evaluation_interval", {})
    divergence = topology.get("branch_divergence_point", {})
    topology_scalars = decision_ego + [
        float(interval["start_arc_length_from_decision_point_m"]),
        float(interval["end_arc_length_from_decision_point_m"]),
        float(interval["length_m"]),
        float(divergence["arc_length_from_decision_point_m"]),
        float(divergence["actual_centerline_separation_m"]),
        float(divergence["topology_separation_threshold_m"]),
    ]
    _require(len(topology_scalars) == 8 and _all_finite(topology_scalars), "FORMAL_TOPOLOGY_SCALARS_INVALID")

    sanitized = {
        "candidates": candidates,
        "topology": [straight_ego, right_ego],
        "topology_mask": [straight_mask, right_mask],
        "topology_source_point_counts": [len(straight_ego_raw), len(right_ego_raw)],
        "topology_scalars": topology_scalars,
        "evidence": [1.0] * 6,
    }
    label = record.get("pair_task_label")
    _require(label in {"TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN"}, "FORMAL_LABEL_INVALID")
    target = {
        "task_target": TASK_TO_INDEX.get(str(label), -1),
        "task_known_mask": label in TASK_TO_INDEX,
        "unknown_target": 0 if label in TASK_TO_INDEX else 1,
    }
    return sanitized, target


def tensorize_formal_records(records: Sequence[Mapping[str, Any]], device: torch.device = None) -> Dict[str, Any]:
    """Tensorize TRAIN/DEV only; TEST tensor construction is intentionally sealed."""

    _require(bool(records), "EMPTY_FORMAL_TENSOR_REQUEST")
    _require(all(item.get("split") in {"TRAIN", "DEV"} for item in records), "TEST_TENSORIZATION_FORBIDDEN")
    device = device or torch.device("cpu")
    _require(device.type == "cpu", "FORMAL_ASSESSMENT_CPU_ONLY")
    parts = [_validated_unit_parts(record) for record in records]
    samples = [item[0] for item in parts]
    targets = [item[1] for item in parts]

    routes = []
    route_masks = []
    speeds = []
    speed_masks = []
    roles = []
    for sample in samples:
        routes.append([[plan["route_plan"] for plan in sample["candidates"][group]["plans"]] for group in ("A", "B")])
        route_masks.append([[plan["route_valid_mask"] for plan in sample["candidates"][group]["plans"]] for group in ("A", "B")])
        speeds.append([[plan["speed_plan"] for plan in sample["candidates"][group]["plans"]] for group in ("A", "B")])
        speed_masks.append([[plan["speed_valid_mask"] for plan in sample["candidates"][group]["plans"]] for group in ("A", "B")])
        roles.append([sample["candidates"][group]["semantic_role_one_hot"] for group in ("A", "B")])

    inputs = {
        "route": torch.tensor(routes, dtype=torch.float32, device=device),
        "route_mask": torch.tensor(route_masks, dtype=torch.bool, device=device),
        "speed": torch.tensor(speeds, dtype=torch.float32, device=device),
        "speed_mask": torch.tensor(speed_masks, dtype=torch.bool, device=device),
        "roles": torch.tensor(roles, dtype=torch.float32, device=device),
        "topology": torch.tensor([item["topology"] for item in samples], dtype=torch.float32, device=device),
        "topology_mask": torch.tensor([item["topology_mask"] for item in samples], dtype=torch.bool, device=device),
        "topology_scalars": torch.tensor([item["topology_scalars"] for item in samples], dtype=torch.float32, device=device),
        "evidence": torch.tensor([item["evidence"] for item in samples], dtype=torch.float32, device=device),
    }
    target_tensors = {
        "task_target": torch.tensor([item["task_target"] for item in targets], dtype=torch.long, device=device),
        "task_known_mask": torch.tensor([item["task_known_mask"] for item in targets], dtype=torch.bool, device=device),
        "unknown_target": torch.tensor([item["unknown_target"] for item in targets], dtype=torch.long, device=device),
    }
    expected = {
        "route": (len(records), 2, 3, 20, 2),
        "route_mask": (len(records), 2, 3, 20),
        "speed": (len(records), 2, 3, 10, 2),
        "speed_mask": (len(records), 2, 3, 10),
        "roles": (len(records), 2, 2),
        "topology": (len(records), 2, 25, 2),
        "topology_mask": (len(records), 2, 25),
        "topology_scalars": (len(records), 8),
        "evidence": (len(records), 6),
    }
    for name, shape in expected.items():
        _require(tuple(inputs[name].shape) == shape, "FORMAL_TENSOR_SHAPE_MISMATCH:%s" % name)
        if inputs[name].dtype != torch.bool:
            _require(bool(torch.isfinite(inputs[name]).all().item()), "FORMAL_NONFINITE_TENSOR:%s" % name)
    _require(tuple(inputs) == MODEL_INPUT_KEYS, "FORMAL_MODEL_INPUT_KEY_DRIFT")
    return {
        "model_inputs": inputs,
        "targets": target_tensors,
        "audit": {
            "unit_count": len(records),
            "model_input_keys": list(inputs),
            "target_keys_physically_separate": list(target_tensors),
            "model_input_source_paths": list(MODEL_INPUT_SOURCE_PATHS),
            "forbidden_field_tensor_count": 0,
            "test_tensorized": False,
        },
    }


def model_input_sha256(inputs: Mapping[str, torch.Tensor]) -> str:
    digest = hashlib.sha256()
    for name in MODEL_INPUT_KEYS:
        tensor = inputs[name].detach().cpu().contiguous()
        digest.update(name.encode("ascii"))
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(str(tuple(tensor.shape)).encode("ascii"))
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


def _split_overlap(values: Iterable[Tuple[str, str]]) -> List[Dict[str, Any]]:
    memberships: MutableMapping[str, set] = defaultdict(set)
    for value, split in values:
        memberships[str(value)].add(str(split))
    return [
        {"value": value, "splits": sorted(splits)}
        for value, splits in sorted(memberships.items())
        if len(splits) > 1
    ]


def audit_formal_dataset() -> Dict[str, Any]:
    """Perform the complete, prediction-free V2+V3 formal-data audit."""

    v2 = _json(V2_DATASET)
    v3 = _json(V3_DATASET)
    for dataset, schema_path in ((v2, V2_SCHEMA), (v3, V3_SCHEMA)):
        schema = _json(schema_path)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(dataset)
    records = load_formal_records()
    by_split = Counter(item["split"] for item in records)
    by_label = Counter(item["pair_task_label"] for item in records)
    by_split_label = {
        split: dict(Counter(item["pair_task_label"] for item in records if item["split"] == split))
        for split in FORMAL_SPLITS
    }
    _require(by_split == Counter({"TRAIN": 25, "DEV": 11, "TEST": 11}), "FORMAL_SPLIT_DISTRIBUTION_MISMATCH")
    _require(by_label == Counter({"TASK_EQUIVALENT": 31, "TASK_CRITICAL": 12, "UNKNOWN": 4}), "FORMAL_LABEL_DISTRIBUTION_MISMATCH")
    _require(all(set(by_split_label[split]) == {"TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN"} for split in ("TRAIN", "DEV")), "FORMAL_TRAIN_DEV_CLASS_COVERAGE_MISSING")

    unit_rows = []
    dimensions: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    topology_complete = 0
    for record in records:
        sanitized, _ = _validated_unit_parts(record)
        topology_complete += int(len(sanitized["topology"]) == 2)
        version = record["_formal_source_version"]
        assignment_path = _version_root(version) / "units" / record["unit_id"] / "SPLIT_ASSIGNMENT.json"
        assignment = _json(assignment_path)
        _require(assignment["unit_id"] == record["unit_id"], "FORMAL_ASSIGNMENT_UNIT_MISMATCH")
        _require(assignment["split"] == record["split"], "FORMAL_ASSIGNMENT_SPLIT_MISMATCH")
        source_identity = record["observation_package"]["source_identity"]
        fixture_sha256 = source_identity["route_fixture_sha256"]
        route_id = source_identity["route_id"]
        for name, value in (
            ("town", record["town"]),
            ("junction_group", record["junction_group"]),
            ("route_family", record["route_family"]),
                ("fixture_sha256", fixture_sha256),
                ("route_identity", "%s:%s" % (record["town"], route_id)),
        ):
            dimensions[name].append((str(value), str(record["split"])))
        unit_rows.append(
            {
                "unit_id": record["unit_id"],
                "source_version": version,
                "split": record["split"],
                "label": record["pair_task_label"],
                "town": record["town"],
                "junction_group": record["junction_group"],
                "route_family": record["route_family"],
                "fixture_sha256": fixture_sha256,
                "plan_record_count": len(record["plans"]),
                "candidate_schedule": [item["candidate_id"] for item in record["plans"]],
                "topology_complete": True,
                "topology_source_point_counts": sanitized["topology_source_point_counts"],
                "topology_padding_policy": "REPEAT_FINAL_VALID_POINT_WITH_FALSE_MASK_NEVER_ZERO_IMPUTE",
                "feature_complete": True,
            }
        )

    overlap = {name: _split_overlap(values) for name, values in dimensions.items()}
    _require(all(not items for items in overlap.values()), "FORMAL_SPLIT_LEAKAGE")
    pilot_manifest = _json(PILOT_MANIFEST)
    pilot_ids = {
        item["unit_id"] if isinstance(item, Mapping) else str(item)
        for item in pilot_manifest.get("units", pilot_manifest.get("pilot_units", []))
    }
    if not pilot_ids:
        pilot_ids = {item["unit_id"] for item in v2["records"] if item.get("unit_status") == "PILOT_DEVELOPMENT_ONLY"}
    formal_ids = {item["unit_id"] for item in records}
    pilot_overlap = sorted(formal_ids & pilot_ids)
    _require(not pilot_overlap, "FORMAL_PILOT_OVERLAP")
    leakage_authority = _json(GLOBAL_LEAKAGE_AUDIT)
    _require(leakage_authority.get("status") == "PASS" and all(leakage_authority.get("checks", {}).values()), "GLOBAL_LEAKAGE_AUTHORITY_NOT_PASS")
    v2_tree = tree_sha256(V2_ROOT)
    _require(v2_tree == EXPECTED_V2_TREE_SHA256, "V2_TREE_SHA256_MISMATCH")
    plan_count = sum(len(item["plans"]) for item in records)
    _require(plan_count == 282, "FORMAL_PLAN_RECORD_COUNT_NOT_282")

    exclusions = [
        {"source_version": version, "unit_id": item["unit_id"], "split": item["split"], "unit_status": item["unit_status"]}
        for version, dataset in (("V2", v2), ("V3", v3))
        for item in dataset["records"]
        if item.get("unit_status") in {"ENGINEERING_EXCLUSION", "EVIDENCE_EXCLUSION"}
    ]
    _require(not any(item["unit_id"] in formal_ids for item in exclusions), "ENGINEERING_EXCLUSION_ENTERED_FORMAL_DATA")
    forbidden_sources = [
        source
        for source in MODEL_INPUT_SOURCE_PATHS
        for field in FORMAL_FORBIDDEN_MODEL_INPUT_FIELDS
        if field.lower() in source.lower()
    ]
    _require(not forbidden_sources, "FORMAL_FEATURE_SOURCE_BLACKLIST_COLLISION")
    return {
        "schema_version": "driveclarify.formal_m1_dataset_audit.v1",
        "status": "PASS",
        "assessment_only": True,
        "test_predictions_computed": 0,
        "test_performance_metrics_computed": 0,
        "datasets": {
            "V2": {"path": str(V2_DATASET.relative_to(REPO_ROOT)), "bytes": V2_DATASET.stat().st_size, "sha256": sha256_file(V2_DATASET)},
            "V3": {"path": str(V3_DATASET.relative_to(REPO_ROOT)), "bytes": V3_DATASET.stat().st_size, "sha256": sha256_file(V3_DATASET)},
        },
        "complete_unit_count": len(records),
        "plan_record_count": plan_count,
        "records_per_unit": 6,
        "repeats_are_measurements_not_samples": True,
        "split_distribution": dict(by_split),
        "label_distribution": dict(by_label),
        "split_label_coverage": by_split_label,
        "pilot_unit_count": len(pilot_ids),
        "pilot_overlap": pilot_overlap,
        "topology_complete_unit_count": topology_complete,
        "engineering_or_evidence_exclusion_count": len(exclusions),
        "exclusions": exclusions,
        "split_overlap": overlap,
        "global_leakage_authority_status": leakage_authority["status"],
        "feature_leakage": {
            "construction": "EXPLICIT_ALLOWLIST_ONLY",
            "model_input_source_paths": list(MODEL_INPUT_SOURCE_PATHS),
            "forbidden_field_tensor_count": 0,
            "forbidden_source_collisions": forbidden_sources,
            "targets_physically_separate_from_model_inputs": True,
            "missing_policy": "FAIL_CLOSED_TO_UNKNOWN_NEVER_ZERO_IMPUTE",
        },
        "shape_dtype_frame_contract": {
            "route": {"shape_per_repeat": [20, 2], "source_dtype": "torch.bfloat16", "persisted_dtype": "JSON_NUMBER_EXACT_FROM_TORCH_TOLIST", "tensor_dtype": "torch.float32", "frame": "EGO_LOCAL_X_FORWARD_Y_RIGHT", "unit": "METRE"},
            "speed": {"shape_per_repeat": [10, 2], "source_dtype": "torch.bfloat16", "persisted_dtype": "JSON_NUMBER_EXACT_FROM_TORCH_TOLIST", "tensor_dtype": "torch.float32", "frame": "EGO_LOCAL_X_FORWARD_Y_RIGHT", "unit": "METRE"},
            "topology": {"shape": [2, 25, 2], "tensor_dtype": "torch.float32", "frame": "EGO_LOCAL_X_FORWARD_Y_RIGHT", "unit": "METRE"},
        },
        "historical_protection": {
            "v2_tree_sha256": v2_tree,
            "v2_tree_matches_frozen": True,
            "v3_tree_sha256_at_audit": tree_sha256(V3_ROOT),
        },
        "units": unit_rows,
    }
