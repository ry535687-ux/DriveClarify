"""Explicit, audited feature tensor construction."""

import hashlib
from typing import Any, Dict, Mapping, Sequence

import torch

from .data import FORBIDDEN_MODEL_INPUT_FIELDS


TASK_TO_INDEX = {"TASK_EQUIVALENT": 0, "TASK_CRITICAL": 1}
EVIDENCE_FEATURES = (
    "plans_available",
    "route_finite",
    "speed_finite",
    "frame_available",
    "observation_available",
    "topology_task_available",
)


FEATURE_SPEC: Dict[str, Any] = {
    "schema_version": "driveclarify.learned_m1_feature_spec.pilot.v1",
    "construction": "EXPLICIT_ALLOWLIST_ONLY",
    "features": [
        {"name": "route_sequence", "source": "candidates.*.plans.*.route_plan", "semantic": "raw ego-local path plan", "label_derived": False},
        {"name": "route_valid_mask", "source": "candidates.*.plans.*.route_valid_mask", "semantic": "route shape/availability", "label_derived": False},
        {"name": "speed_sequence", "source": "candidates.*.plans.*.speed_plan", "semantic": "raw speed-head plan", "label_derived": False},
        {"name": "speed_valid_mask", "source": "candidates.*.plans.*.speed_valid_mask", "semantic": "speed shape/availability", "label_derived": False},
        {"name": "candidate_semantic_role", "source": "candidates.*.semantic_role_one_hot", "semantic": "canonical straight/right role", "label_derived": False},
        {"name": "repeat_statistics", "source": "computed inside candidate aggregator", "semantic": "repeat embedding mean/std and within variation", "label_derived": False},
        {"name": "pair_difference", "source": "computed symmetrically from candidate embeddings", "semantic": "mean/absolute difference/product", "label_derived": False},
        {"name": "topology_centerlines", "source": "frozen_topology_task_context.*_centerline_ego_m", "semantic": "frozen straight/right geometry transformed by frozen ego pose", "label_derived": False},
        {"name": "topology_scalars", "source": "frozen_topology_task_context decision/evaluation/divergence geometry", "semantic": "decision point and evaluation interval geometry", "label_derived": False},
        {"name": "evidence_availability", "source": "low_level_evidence_mask", "semantic": "low-level input availability only; fairness verdict excluded", "label_derived": False},
    ],
    "targets_not_features": ["pair_target", "target_available"],
    "audit_only_not_features": ["unit_provenance", "observation.hash", "hard_evidence_gate", "label_provenance", "exclusion_provenance"],
    "fairness_contract": "FAIRNESS_RESULT_USED_ONLY_AS_PRE-TENSOR_HARD_VALIDITY_GATE",
    "forbidden_model_input_fields": sorted(FORBIDDEN_MODEL_INPUT_FIELDS),
}


def assert_feature_spec_leakage_free(spec: Mapping[str, Any] = FEATURE_SPEC) -> None:
    if spec.get("construction") != "EXPLICIT_ALLOWLIST_ONLY":
        raise ValueError("FEATURE_CONSTRUCTION_NOT_ALLOWLISTED")
    sources = " ".join(str(item.get("source", "")).lower() for item in spec["features"])
    for field in FORBIDDEN_MODEL_INPUT_FIELDS:
        if field.lower() in sources:
            raise ValueError("FORBIDDEN_FEATURE_SOURCE:%s" % field)
    if any(bool(item.get("label_derived")) for item in spec["features"]):
        raise ValueError("LABEL_DERIVED_FEATURE_DECLARED")
    if "fairness" in EVIDENCE_FEATURES:
        raise ValueError("FAIRNESS_VERDICT_ENCODED")


def _finite(tensor: torch.Tensor, name: str) -> None:
    if not bool(torch.isfinite(tensor).all().item()):
        raise ValueError("NONFINITE_FEATURE_FAIL_CLOSED:%s" % name)


def tensorize_samples(samples: Sequence[Mapping[str, Any]], device: torch.device = None) -> Dict[str, torch.Tensor]:
    assert_feature_spec_leakage_free()
    if not samples:
        raise ValueError("EMPTY_SAMPLE_SET")
    device = device or torch.device("cpu")
    routes = []
    route_masks = []
    speeds = []
    speed_masks = []
    roles = []
    topologies = []
    topology_masks = []
    topology_scalars = []
    evidence = []
    hard_gate = []
    task_targets = []
    task_known = []
    unknown_targets = []
    for sample in samples:
        candidate_routes = []
        candidate_route_masks = []
        candidate_speeds = []
        candidate_speed_masks = []
        candidate_roles = []
        for candidate_id in ("A", "B"):
            candidate = sample["candidates"][candidate_id]
            if len(candidate["plans"]) != 3:
                raise ValueError("MISSING_PLAN_FAIL_CLOSED")
            candidate_routes.append([plan["route_plan"] for plan in candidate["plans"]])
            candidate_route_masks.append([plan["route_valid_mask"] for plan in candidate["plans"]])
            candidate_speeds.append([plan["speed_plan"] for plan in candidate["plans"]])
            candidate_speed_masks.append([plan["speed_valid_mask"] for plan in candidate["plans"]])
            candidate_roles.append(candidate["semantic_role_one_hot"])
        context = sample["frozen_topology_task_context"]
        routes.append(candidate_routes)
        route_masks.append(candidate_route_masks)
        speeds.append(candidate_speeds)
        speed_masks.append(candidate_speed_masks)
        roles.append(candidate_roles)
        topologies.append([context["straight_centerline_ego_m"], context["right_centerline_ego_m"]])
        topology_masks.append([context["centerline_valid_mask"], context["centerline_valid_mask"]])
        topology_scalars.append(
            context["decision_point_ego_m"]
            + context["evaluation_interval_m"]
            + context["branch_relative_geometry_m"]
        )
        evidence.append([float(sample["low_level_evidence_mask"][name]) for name in EVIDENCE_FEATURES])
        gate_ok = sample.get("hard_evidence_gate", {}).get("status") == "PASS"
        hard_gate.append(gate_ok)
        target = sample["pair_target"]
        known = bool(sample["target_available"])
        task_targets.append(TASK_TO_INDEX.get(target, 0))
        task_known.append(known)
        unknown_targets.append(0 if known else 1)
    tensors = {
        "route": torch.tensor(routes, dtype=torch.float32, device=device),
        "route_mask": torch.tensor(route_masks, dtype=torch.bool, device=device),
        "speed": torch.tensor(speeds, dtype=torch.float32, device=device),
        "speed_mask": torch.tensor(speed_masks, dtype=torch.bool, device=device),
        "roles": torch.tensor(roles, dtype=torch.float32, device=device),
        "topology": torch.tensor(topologies, dtype=torch.float32, device=device),
        "topology_mask": torch.tensor(topology_masks, dtype=torch.bool, device=device),
        "topology_scalars": torch.tensor(topology_scalars, dtype=torch.float32, device=device),
        "evidence": torch.tensor(evidence, dtype=torch.float32, device=device),
        "hard_gate": torch.tensor(hard_gate, dtype=torch.bool, device=device),
        "task_target": torch.tensor(task_targets, dtype=torch.long, device=device),
        "task_known_mask": torch.tensor(task_known, dtype=torch.bool, device=device),
        "unknown_target": torch.tensor(unknown_targets, dtype=torch.long, device=device),
    }
    expected = {
        "route": (len(samples), 2, 3, 20, 2),
        "speed": (len(samples), 2, 3, 10, 2),
        "roles": (len(samples), 2, 2),
        "topology": (len(samples), 2, 25, 2),
        "topology_scalars": (len(samples), 8),
        "evidence": (len(samples), 6),
    }
    for name, shape in expected.items():
        if tuple(tensors[name].shape) != shape:
            raise ValueError("FEATURE_SHAPE_MISMATCH:%s:%r" % (name, tuple(tensors[name].shape)))
        _finite(tensors[name], name)
    return tensors


def swap_candidates(batch: Mapping[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    swapped = dict(batch)
    for name in ("route", "route_mask", "speed", "speed_mask", "roles"):
        swapped[name] = batch[name].flip(1)
    return swapped


def feature_tensor_sha256(batch: Mapping[str, torch.Tensor]) -> str:
    """Hash model inputs only; targets, gates, identities and provenance are excluded."""

    digest = hashlib.sha256()
    feature_names = (
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
    for name in feature_names:
        tensor = batch[name].detach().cpu().contiguous()
        digest.update(name.encode("ascii"))
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(str(tuple(tensor.shape)).encode("ascii"))
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()
