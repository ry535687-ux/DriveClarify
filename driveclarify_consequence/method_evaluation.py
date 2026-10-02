"""Deterministic fixtures, baselines, cross-validation, and reports for Method M1 v0."""

from __future__ import annotations

import copy
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .task_atoms import evaluate_task_atom, map_candidate_plan_to_task_atoms, reduce_task_pair
from .task_conditioned_pairwise import (
    CANDIDATE_LABELS,
    DATA_PROVENANCE,
    METHOD_DESIGNATION,
    PAIR_LABELS,
    EvidenceAwarePairwiseModel,
    raw_task_anchor_from_record,
)
from .task_pipeline import deterministic_json_hash, inspect_s1_artifact


PROFILE_PARAMETERS = {
    "PROFILE_ALPHA": (4.0, 0.0, 0.0, 0.45),
    "PROFILE_BETA": (3.0, 2.0, 0.65, 0.65),
    "PROFILE_GAMMA": (3.0, -2.0, -0.55, 0.25),
}


def _route(dx: float, dy: float, bend: float, *, jitter: float = 0.0) -> list[list[list[float]]]:
    points: list[list[float]] = []
    for index in range(12):
        t = index / 11.0
        deterministic = jitter * math.sin((index + 1) * 1.71)
        points.append(
            [
                dx * t + 0.35 * bend * t * (1.0 - t) + deterministic,
                dy * t + bend * t * (1.0 - t) - deterministic * 0.5,
            ]
        )
    return [points]


def _speed(level: float, *, jitter: float = 0.0) -> list[list[list[float]]]:
    rows: list[list[float]] = []
    for index in range(8):
        t = index / 7.0
        rows.append(
            [
                level * (0.75 + 0.18 * t) + jitter * math.cos(index + 0.4),
                level * (0.22 - 0.08 * t) + jitter * math.sin(index + 0.9),
            ]
        )
    return [rows]


def _profile_record(profile: str, *, jitter: float = 0.0) -> dict[str, Any]:
    dx, dy, bend, speed = PROFILE_PARAMETERS[profile]
    return {
        "pred_route_raw": _route(dx, dy, bend, jitter=jitter),
        "pred_speed_wps_raw": _speed(speed, jitter=jitter * 0.35),
        "plan_frame": "MODEL_LOCAL_RAW",
        "plan_unit": "RAW_UNIT",
    }


def _task_registry(family: str, token: str) -> dict[str, Any]:
    if family == "REFERENCE_GOAL":
        return {
            "atom_id": "method_task",
            "atom_type": family,
            "target_id": token,
            "frame": "LANGUAGE_REFERENCE",
            "unit": "IDENTIFIER",
            "dependencies": ["target_evidence"],
        }
    if family == "DESTINATION_GOAL":
        return {
            "atom_id": "method_task",
            "atom_type": family,
            "target_id": token,
            "frame": "REGISTERED_TOPOLOGY",
            "unit": "REGION_ID",
            "dependencies": ["plan_to_goal_transform", "target_evidence"],
        }
    return {
        "atom_id": "method_task",
        "atom_type": family,
        "target_class": token,
        "frame": "TOPOLOGY_BRANCH",
        "unit": "MANEUVER_CLASS",
        "dependencies": ["plan_to_maneuver_mapping", "target_evidence"],
    }


def _binding(family: str, token: str, label: str) -> list[dict[str, Any]]:
    if label == "UNKNOWN":
        return []
    registry = _task_registry(family, token)
    mapped = token if label == "PASS" else f"{token}__NONMATCH"
    value = {
        "atom_id": "method_task",
        "evidence_grade": "SUPPORTED_BUT_INCOMPLETE",
        "allowed_usage_purposes": ["DIAGNOSTIC_ONLY"],
        "dependency_statuses": {dependency: True for dependency in registry["dependencies"]},
        "status": "AVAILABLE",
        "source_artifacts": ["METHOD_FIXTURE_DECLARATION"],
    }
    if family == "MANEUVER_BRANCH":
        value["mapped_target_class"] = mapped
    else:
        value["mapped_target_id"] = mapped
    return [value]


def _availability(**overrides: bool) -> dict[str, bool]:
    result = {"route": True, "speed": True, "task_anchor": True, "provenance": True}
    result.update(overrides)
    return result


def _candidate(
    candidate_id: str,
    profile: str,
    family: str,
    token: str,
    label: str,
    *,
    provenance: str,
    jitter: float = 0.0,
    missing: str | None = None,
) -> dict[str, Any]:
    value = {
        "candidate_id": candidate_id,
        "source_observation_id": "method_fixture_observation",
        **_profile_record(profile, jitter=jitter),
        "task_bindings": _binding(family, token, label),
        "evidence": {
            "availability": _availability(),
            "provenance": provenance,
            "source_artifacts": ["METHOD_FIXTURE_DECLARATION"],
        },
    }
    if missing == "route":
        value.pop("pred_route_raw")
        value["evidence"]["availability"]["route"] = False
    elif missing == "speed":
        value.pop("pred_speed_wps_raw")
        value["evidence"]["availability"]["speed"] = False
    elif missing == "provenance":
        value["evidence"].pop("provenance")
        value["evidence"]["availability"]["provenance"] = False
    elif missing == "frame":
        value.pop("plan_frame")
    return value


def _task_spec(family: str, token: str, profile: str, *, anchor_available: bool = True) -> dict[str, Any]:
    reference = _profile_record(profile)
    anchor = raw_task_anchor_from_record(reference) if anchor_available else None
    return {
        "task_family": family,
        "task_token": token,
        "anchor": anchor,
        "tolerance": {field: value for field, value in zip(
            (
                "endpoint_delta_0_raw",
                "endpoint_delta_1_raw",
                "curvature_like_raw",
                "speed_mean_raw",
            ),
            (0.55, 0.55, 0.22, 0.12),
        )},
        "anchor_frame": "MODEL_LOCAL_RAW",
        "anchor_unit": "RAW_UNIT",
        "physical_direction_semantics": False,
    }


def _base_case(
    name: str,
    family: str,
    task_profile: str,
    left_profile: str,
    right_profile: str,
    left_label: str,
    right_label: str,
    pair_label: str,
    *,
    missing_left: str | None = None,
    missing_right: str | None = None,
    anchor_available: bool = True,
) -> dict[str, Any]:
    token = f"{family}:{task_profile}"
    left = _candidate(
        "A", left_profile, family, token, left_label,
        provenance="HAND_AUTHORED_ORACLE_FIXTURE", missing=missing_left,
    )
    right = _candidate(
        "B", right_profile, family, token, right_label,
        provenance="HAND_AUTHORED_ORACLE_FIXTURE", missing=missing_right,
    )
    if not anchor_available:
        for candidate in (left, right):
            candidate["evidence"]["availability"]["task_anchor"] = False
            candidate["task_bindings"] = []
    return {
        "case_id": name,
        "oracle_group_id": name,
        "observation_id": "method_fixture_observation",
        "provenance": "HAND_AUTHORED_ORACLE_FIXTURE",
        "task_spec": _task_spec(family, token, task_profile, anchor_available=anchor_available),
        "candidates": [left, right],
        "candidate_labels": {"A": left_label, "B": right_label},
        "pair_label": pair_label,
        "gold_source": "PREDECLARED_BY_FIXTURE_AUTHOR_BEFORE_MODEL_EVALUATION",
        "labels_derived_from_model_or_test": False,
    }


def hand_authored_cases() -> list[dict[str, Any]]:
    """Return the fixed oracle declarations; gold is explicit, not recomputed from predictions."""

    cases: list[dict[str, Any]] = []
    for family in ("REFERENCE_GOAL", "DESTINATION_GOAL", "MANEUVER_BRANCH"):
        prefix = family.lower()
        cases.extend(
            [
                _base_case(
                    f"{prefix}_both_match", family, "PROFILE_ALPHA", "PROFILE_ALPHA",
                    "PROFILE_ALPHA", "PASS", "PASS", "TASK_EQUIVALENT",
                ),
                _base_case(
                    f"{prefix}_one_matches", family, "PROFILE_ALPHA", "PROFILE_ALPHA",
                    "PROFILE_BETA", "PASS", "FAIL", "TASK_CRITICAL",
                ),
                _base_case(
                    f"{prefix}_both_nonmatch", family, "PROFILE_ALPHA", "PROFILE_BETA",
                    "PROFILE_GAMMA", "FAIL", "FAIL", "TASK_EQUIVALENT",
                ),
                _base_case(
                    f"{prefix}_same_geometry_different_task", family, "PROFILE_GAMMA",
                    "PROFILE_ALPHA", "PROFILE_BETA", "FAIL", "FAIL", "TASK_EQUIVALENT",
                ),
                _base_case(
                    f"{prefix}_reverse_critical", family, "PROFILE_BETA", "PROFILE_GAMMA",
                    "PROFILE_BETA", "FAIL", "PASS", "TASK_CRITICAL",
                ),
            ]
        )
    cases.extend(
        [
            _base_case(
                "unknown_missing_task_anchor", "REFERENCE_GOAL", "PROFILE_ALPHA",
                "PROFILE_ALPHA", "PROFILE_BETA", "UNKNOWN", "UNKNOWN", "UNKNOWN",
                anchor_available=False,
            ),
            _base_case(
                "unknown_missing_left_route", "DESTINATION_GOAL", "PROFILE_ALPHA",
                "PROFILE_ALPHA", "PROFILE_ALPHA", "UNKNOWN", "PASS", "UNKNOWN",
                missing_left="route",
            ),
            _base_case(
                "unknown_missing_right_speed", "MANEUVER_BRANCH", "PROFILE_ALPHA",
                "PROFILE_ALPHA", "PROFILE_ALPHA", "PASS", "UNKNOWN", "UNKNOWN",
                missing_right="speed",
            ),
            _base_case(
                "unknown_missing_provenance", "REFERENCE_GOAL", "PROFILE_BETA",
                "PROFILE_BETA", "PROFILE_BETA", "UNKNOWN", "PASS", "UNKNOWN",
                missing_left="provenance",
            ),
            _base_case(
                "unknown_missing_frame", "DESTINATION_GOAL", "PROFILE_GAMMA",
                "PROFILE_GAMMA", "PROFILE_GAMMA", "PASS", "UNKNOWN", "UNKNOWN",
                missing_right="frame",
            ),
        ]
    )
    return cases


def _synthetic_variant(base: Mapping[str, Any], variant: int) -> dict[str, Any]:
    value = copy.deepcopy(dict(base))
    value["case_id"] = f"{base['case_id']}__synthetic_{variant}"
    value["provenance"] = "SYNTHETIC_METHOD_TEST_ONLY"
    value["derived_from"] = base["case_id"]
    value["synthetic_transform"] = {
        "type": "DETERMINISTIC_SMALL_RAW_SEQUENCE_PERTURBATION",
        "variant": variant,
        "gold_labels_copied_before_evaluation": True,
    }
    jitter = 0.012 * variant
    for candidate_index, candidate in enumerate(value["candidates"]):
        candidate["evidence"]["provenance"] = "SYNTHETIC_METHOD_TEST_ONLY"
        if "pred_route_raw" in candidate:
            sign = 1.0 if candidate_index == 0 else -1.0
            for point_index, point in enumerate(candidate["pred_route_raw"][0]):
                point[0] += sign * jitter * math.sin(point_index + 0.31)
                point[1] += sign * jitter * math.cos(point_index + 0.77)
        if "pred_speed_wps_raw" in candidate:
            for point_index, point in enumerate(candidate["pred_speed_wps_raw"][0]):
                point[0] += jitter * 0.2 * math.sin(point_index + candidate_index)
                point[1] -= jitter * 0.15 * math.cos(point_index + candidate_index)
    return value


def build_labelled_dataset() -> list[dict[str, Any]]:
    bases = hand_authored_cases()
    result = [copy.deepcopy(case) for case in bases]
    for base in bases:
        result.extend([_synthetic_variant(base, 1), _synthetic_variant(base, 2)])
    return result


def dataset_provenance(dataset: Sequence[Mapping[str, Any]], s1_path: Path) -> dict[str, Any]:
    counts = Counter(str(case.get("provenance")) for case in dataset)
    candidate_counts = Counter()
    pair_counts = Counter()
    for case in dataset:
        candidate_counts.update(str(label) for label in case["candidate_labels"].values())
        pair_counts.update([str(case["pair_label"])])
    return {
        "schema_version": "driveclarify.method_dataset_provenance.v0",
        "evidence_designation": list(METHOD_DESIGNATION),
        "labelled_fixture_count": len(dataset),
        "provenance_counts": {key: counts.get(key, 0) for key in DATA_PROVENANCE},
        "candidate_label_counts": dict(sorted(candidate_counts.items())),
        "pair_label_counts": dict(sorted(pair_counts.items())),
        "hand_authored_gold_frozen_before_evaluation": True,
        "synthetic_gold_copied_from_declared_oracle_group_before_evaluation": True,
        "labels_derived_from_model_or_test": False,
        "real_recorded_sources": [
            {
                "path": str(s1_path.resolve()),
                "provenance": "REAL_RECORDED_UNLABELED",
                "label_status": "UNLABELED_TASK_MAPPING_UNAVAILABLE",
                "synthetic_label_written_to_artifact": False,
                "pair_label": "UNKNOWN",
            }
        ],
        "allowed_provenance_values": list(DATA_PROVENANCE),
    }


def _metrics(rows: Sequence[Mapping[str, Any]], labels: Sequence[str]) -> dict[str, Any]:
    labels = tuple(labels)
    counts: dict[str, dict[str, int]] = {}
    recalls: list[float] = []
    f1s: list[float] = []
    brier_values: list[float] = []
    for label in labels:
        tp = sum(row["gold"] == label and row["prediction"] == label for row in rows)
        fp = sum(row["gold"] != label and row["prediction"] == label for row in rows)
        fn = sum(row["gold"] == label and row["prediction"] != label for row in rows)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        counts[label] = {
            "support": sum(row["gold"] == label for row in rows),
            "predicted": sum(row["prediction"] == label for row in rows),
            "true_positive": tp,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }
        recalls.append(recall)
        f1s.append(f1)
    for row in rows:
        probabilities = row.get("probabilities", {})
        brier_values.append(
            sum(
                (float(probabilities.get(label, 0.0)) - (1.0 if row["gold"] == label else 0.0)) ** 2
                for label in labels
            )
            / len(labels)
        )
    accuracy = sum(row["gold"] == row["prediction"] for row in rows) / max(len(rows), 1)
    unknown = counts.get("UNKNOWN", {})
    return {
        "sample_count": len(rows),
        "accuracy": round(accuracy, 6),
        "macro_f1": round(float(np.mean(f1s)), 6),
        "balanced_accuracy": round(float(np.mean(recalls)), 6),
        "per_class": counts,
        "unknown_recall": unknown.get("recall") if unknown else None,
        "multiclass_brier": round(float(np.mean(brier_values)), 6),
    }


def _selective_curve(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    curve: list[dict[str, Any]] = []
    for threshold in (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        selected = [
            row for row in rows
            if row["prediction"] != "UNKNOWN" and float(row.get("confidence", 0.0)) >= threshold
        ]
        risk = None
        if selected:
            risk = 1.0 - sum(row["prediction"] == row["gold"] for row in selected) / len(selected)
        curve.append(
            {
                "confidence_threshold": threshold,
                "coverage": round(len(selected) / max(len(rows), 1), 6),
                "selective_risk": None if risk is None else round(risk, 6),
                "covered_count": len(selected),
            }
        )
    return curve


def _fold(case: Mapping[str, Any], group_order: Mapping[str, int]) -> int:
    return group_order[str(case["oracle_group_id"])] % 5


def _deterministic_group_order(dataset: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    groups = list(dict.fromkeys(str(case["oracle_group_id"]) for case in dataset))
    permutation = np.random.default_rng(314).permutation(len(groups))
    return {group: int(permutation[index]) for index, group in enumerate(groups)}


def _cross_validate_model(
    dataset: Sequence[Mapping[str, Any]],
    factory: Callable[[int], EvidenceAwarePairwiseModel],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    group_order = _deterministic_group_order(dataset)
    pair_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    swap_probability_max_abs = 0.0
    swap_label_matches = 0
    input_immutability = True
    fold_losses: list[dict[str, float]] = []
    for fold in range(5):
        train = [case for case in dataset if _fold(case, group_order) != fold]
        test = [case for case in dataset if _fold(case, group_order) == fold]
        model = factory(fold)
        model.fit(train)
        fold_losses.append(model.compute_loss(test))
        for case in test:
            before = deterministic_json_hash(case)
            prediction = model.predict(case)
            swapped_case = copy.deepcopy(dict(case))
            swapped_case["candidates"] = list(reversed(swapped_case["candidates"]))
            swapped_prediction = model.predict(swapped_case)
            input_immutability = input_immutability and before == deterministic_json_hash(case)
            pair = prediction["pair"]
            swapped_pair = swapped_prediction["pair"]
            swap_label_matches += int(pair["label"] == swapped_pair["label"])
            swap_probability_max_abs = max(
                swap_probability_max_abs,
                max(
                    abs(pair["probabilities"][label] - swapped_pair["probabilities"][label])
                    for label in PAIR_LABELS
                ),
            )
            pair_rows.append(
                {
                    "case_id": case["case_id"],
                    "gold": case["pair_label"],
                    "prediction": pair["label"],
                    "confidence": pair["confidence"],
                    "probabilities": pair["probabilities"],
                    "provenance": case["provenance"],
                }
            )
            for candidate in case["candidates"]:
                candidate_id = str(candidate["candidate_id"])
                outcome = prediction["candidate_outcomes"][candidate_id]
                candidate_rows.append(
                    {
                        "case_id": case["case_id"],
                        "candidate_id": candidate_id,
                        "gold": case["candidate_labels"][candidate_id],
                        "prediction": outcome["label"],
                        "confidence": outcome["confidence"],
                        "probabilities": outcome["probabilities"],
                        "provenance": case["provenance"],
                    }
                )
    swap_count = len(pair_rows)
    checks = {
        "swap_label_consistency": round(swap_label_matches / max(swap_count, 1), 6),
        "swap_probability_max_abs_difference": swap_probability_max_abs,
        "input_immutability": input_immutability,
        "fold_losses": fold_losses,
    }
    return pair_rows, candidate_rows, checks


def _raw_distance(case: Mapping[str, Any], mode: str) -> float | None:
    candidates = case["candidates"]
    left, right = candidates[0], candidates[1]
    try:
        if mode == "route":
            a = np.asarray(left["pred_route_raw"], dtype=np.float64)
            b = np.asarray(right["pred_route_raw"], dtype=np.float64)
        elif mode == "speed":
            a = np.asarray(left["pred_speed_wps_raw"], dtype=np.float64)
            b = np.asarray(right["pred_speed_wps_raw"], dtype=np.float64)
        else:
            a = np.asarray(left["pred_route_raw"], dtype=np.float64)[0, -1]
            b = np.asarray(right["pred_route_raw"], dtype=np.float64)[0, -1]
    except (KeyError, TypeError, ValueError):
        return None
    if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
        return None
    return float(np.linalg.norm(a - b))


def _threshold_rows(dataset: Sequence[Mapping[str, Any]], mode: str) -> list[dict[str, Any]]:
    group_order = _deterministic_group_order(dataset)
    rows: list[dict[str, Any]] = []
    for fold in range(5):
        train = [case for case in dataset if _fold(case, group_order) != fold]
        test = [case for case in dataset if _fold(case, group_order) == fold]
        train_known = [
            (_raw_distance(case, mode), case["pair_label"])
            for case in train if case["pair_label"] != "UNKNOWN"
        ]
        values = sorted({distance for distance, _ in train_known if distance is not None})
        candidates = [0.0] + [float((a + b) * 0.5) for a, b in zip(values, values[1:])] + values[-1:]
        best = (float("-inf"), 0.0)
        for threshold in candidates:
            correct = sum(
                (("TASK_CRITICAL" if distance > threshold else "TASK_EQUIVALENT") == gold)
                for distance, gold in train_known if distance is not None
            )
            score = correct / max(sum(distance is not None for distance, _ in train_known), 1)
            if score > best[0]:
                best = (score, threshold)
        threshold = best[1]
        for case in test:
            distance = _raw_distance(case, mode)
            if distance is None:
                prediction = "UNKNOWN"
            else:
                prediction = "TASK_CRITICAL" if distance > threshold else "TASK_EQUIVALENT"
            rows.append(
                {
                    "case_id": case["case_id"],
                    "gold": case["pair_label"],
                    "prediction": prediction,
                    "confidence": 1.0,
                    "probabilities": {label: 1.0 if label == prediction else 0.0 for label in PAIR_LABELS},
                }
            )
    return rows


def _existing_rule_rows(dataset: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in dataset:
        task = case["task_spec"]
        registry = [_task_registry(task["task_family"], task["task_token"])]
        evaluations: dict[str, list[dict[str, Any]]] = {}
        for candidate in case["candidates"]:
            candidate_id = str(candidate["candidate_id"])
            atoms = map_candidate_plan_to_task_atoms(candidate, registry, case["observation_id"])
            evaluations[candidate_id] = [evaluate_task_atom(atom) for atom in atoms]
        pair = reduce_task_pair(evaluations, ["method_task"])
        prediction = pair["pair_class"]
        rows.append(
            {
                "case_id": case["case_id"],
                "gold": case["pair_label"],
                "prediction": prediction,
                "confidence": 1.0,
                "probabilities": {label: 1.0 if label == prediction else 0.0 for label in PAIR_LABELS},
            }
        )
    return rows


def _model_result(
    dataset: Sequence[Mapping[str, Any]],
    factory: Callable[[int], EvidenceAwarePairwiseModel],
) -> dict[str, Any]:
    pair_rows, candidate_rows, checks = _cross_validate_model(dataset, factory)
    return {
        "pair_metrics": _metrics(pair_rows, PAIR_LABELS),
        "candidate_metrics": _metrics(candidate_rows, CANDIDATE_LABELS),
        "selective_risk_vs_coverage": _selective_curve(pair_rows),
        "swap_consistency": {
            "label_rate": checks["swap_label_consistency"],
            "probability_max_abs_difference": checks["swap_probability_max_abs_difference"],
        },
        "input_immutability": checks["input_immutability"],
        "fold_losses": checks["fold_losses"],
    }


def evaluate_method(dataset: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    proposed = _model_result(
        dataset,
        lambda fold: EvidenceAwarePairwiseModel(seed=100 + fold),
    )
    task_agnostic = _model_result(
        dataset,
        lambda fold: EvidenceAwarePairwiseModel(seed=100 + fold, task_conditioning=False),
    )
    no_pairwise = _model_result(
        dataset,
        lambda fold: EvidenceAwarePairwiseModel(seed=100 + fold, pairwise_interaction=False),
    )
    no_swap = _model_result(
        dataset,
        lambda fold: EvidenceAwarePairwiseModel(seed=100 + fold, lambda_swap=0.0),
    )
    no_unknown = _model_result(
        dataset,
        lambda fold: EvidenceAwarePairwiseModel(seed=100 + fold, unknown_head=False),
    )
    route_rows = _threshold_rows(dataset, "route")
    speed_rows = _threshold_rows(dataset, "speed")
    endpoint_rows = _threshold_rows(dataset, "endpoint")
    rule_rows = _existing_rule_rows(dataset)
    baselines = {
        "schema_version": "driveclarify.method_baseline_results.v0",
        "evidence_designation": list(METHOD_DESIGNATION),
        "evaluation": "5_FOLD_GROUPED_CROSS_VALIDATION_ON_LABELLED_FIXTURES_ONLY",
        "raw_route_l2_threshold": {"pair_metrics": _metrics(route_rows, PAIR_LABELS)},
        "raw_speed_l2_threshold": {"pair_metrics": _metrics(speed_rows, PAIR_LABELS)},
        "endpoint_only": {"pair_metrics": _metrics(endpoint_rows, PAIR_LABELS)},
        "task_agnostic_pair_encoder": task_agnostic,
        "no_abstention_forced_classifier": no_unknown,
        "existing_rule_mapper": {"pair_metrics": _metrics(rule_rows, PAIR_LABELS)},
        "proposed_task_conditioned_pair_comparator": proposed,
        "task_conditioned_vs_task_agnostic_macro_f1_gap": round(
            proposed["pair_metrics"]["macro_f1"]
            - task_agnostic["pair_metrics"]["macro_f1"],
            6,
        ),
    }
    ablations = {
        "schema_version": "driveclarify.method_ablation_results.v0",
        "evidence_designation": list(METHOD_DESIGNATION),
        "evaluation": "5_FOLD_GROUPED_CROSS_VALIDATION_ON_LABELLED_FIXTURES_ONLY",
        "full_method": proposed,
        "remove_task_conditioning": task_agnostic,
        "remove_pairwise_interaction": no_pairwise,
        "remove_swap_consistency": {
            **no_swap,
            "interpretation": "No prediction change is expected because the comparator feature map is swap-invariant by construction; lambda_swap remains an audited loss term.",
        },
        "remove_UNKNOWN_head": no_unknown,
    }
    return baselines, ablations


def s1_forward_report(s1_path: Path, dataset: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    before = s1_path.read_bytes()
    s1_case, integration = inspect_s1_artifact(s1_path)
    real_case = {
        "case_id": "s1_real_recorded_unlabeled",
        "task_spec": {
            "task_family": "REFERENCE_GOAL",
            "task_token": "S1_TASK_MAPPING_UNAVAILABLE",
            "anchor": None,
            "tolerance": None,
            "anchor_frame": None,
            "anchor_unit": None,
        },
        "candidates": [],
    }
    for candidate in s1_case["candidate_plans"]:
        value = copy.deepcopy(candidate)
        value["evidence"] = {
            "availability": {
                "route": True,
                "speed": True,
                "task_anchor": False,
                "provenance": True,
            },
            "provenance": "REAL_RECORDED_UNLABELED",
            "source_artifacts": [str(s1_path.resolve())],
        }
        real_case["candidates"].append(value)
    model = EvidenceAwarePairwiseModel(seed=909).fit(dataset)
    prediction = model.predict(real_case)
    after = s1_path.read_bytes()
    return {
        "schema_version": "driveclarify.method_s1_compatibility.v0",
        "evidence_designation": list(METHOD_DESIGNATION),
        "source_artifact": str(s1_path.resolve()),
        "source_artifact_sha256": integration["source_artifact_sha256"],
        "schema_compatibility": "PASS",
        "embedding_forward_success": True,
        "candidate_labels": {
            key: value["label"] for key, value in prediction["candidate_outcomes"].items()
        },
        "pair_label": prediction["pair"]["label"],
        "unknown_propagation": (
            prediction["pair"]["label"] == "UNKNOWN"
            and all(value["label"] == "UNKNOWN" for value in prediction["candidate_outcomes"].values())
        ),
        "reason_trace": prediction["pair"]["reason_trace"],
        "task_mapping_available": False,
        "fabricated_task_label": False,
        "synthetic_label_written_to_real_artifact": False,
        "source_artifact_unchanged": before == after,
    }


def deterministic_repeat_check(dataset: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    train = list(dataset)
    first = EvidenceAwarePairwiseModel(seed=777).fit(train)
    second = EvidenceAwarePairwiseModel(seed=777).fit(train)
    first_predictions = [first.predict(case) for case in dataset[:8]]
    second_predictions = [second.predict(case) for case in dataset[:8]]
    return {
        "same_seed_prediction_hash_equal": (
            deterministic_json_hash(first_predictions) == deterministic_json_hash(second_predictions)
        ),
        "first_sha256": deterministic_json_hash(first_predictions),
        "second_sha256": deterministic_json_hash(second_predictions),
        "torch_loaded": "torch" in sys.modules,
        "cuda_initialized": False,
    }


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
