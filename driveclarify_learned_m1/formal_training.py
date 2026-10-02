"""Formal TRAIN/DEV-only Learned M1 training and reporting primitives.

This module has no TEST tensorization or evaluation entry point.  It accepts
already-separated TRAIN and DEV bundles and emits unit-level evidence,
checkpoint hashes, frozen-grid selection rows, and exact small-sample metrics.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import torch
import torch.nn.functional as F

from .checkpoint import state_dict_sha256
from .formal_data import MODEL_INPUT_KEYS
from .formal_protocol import UNKNOWN_THRESHOLD_GRID, validate_formal_checkpoint
from .model import LearnedM1, trainable_parameter_count


TASK_NAMES = ("TASK_EQUIVALENT", "TASK_CRITICAL")
FORMAL_SEEDS = (17, 29, 43, 59, 71)
TASK_CLASS_WEIGHTS = (0.5217391304347826, 1.4782608695652173)
UNKNOWN_CLASS_WEIGHTS = (0.23923444976076552, 1.7607655502392343)

VARIANTS: Dict[str, Dict[str, Any]] = {
    "MAIN_WEIGHTED_PILOT_NEIGHBORHOOD_V1": {
        "role": "main",
        "model_kwargs": {},
        "weighted": True,
        "abstention": True,
        "swap_invariant": True,
    },
    "BASELINE_TASK_AGNOSTIC_LEARNED_COMPARATOR_V1": {
        "role": "required_baseline",
        "model_kwargs": {"use_topology": False, "use_roles": False},
        "weighted": True,
        "abstention": True,
        "swap_invariant": True,
    },
    "BASELINE_NO_ABSTENTION_LEARNED_V1": {
        "role": "required_baseline_and_ablation",
        "model_kwargs": {},
        "weighted": True,
        "abstention": False,
        "swap_invariant": True,
    },
    "ABLATION_NO_TOPOLOGY_CONDITIONING_V1": {
        "role": "pre_registered_ablation",
        "model_kwargs": {"use_topology": False},
        "weighted": True,
        "abstention": True,
        "swap_invariant": True,
    },
    "ABLATION_REPEAT_1_ONLY_V1": {
        "role": "pre_registered_ablation",
        "model_kwargs": {"repeat_aggregation": "repeat_1"},
        "weighted": True,
        "abstention": True,
        "swap_invariant": True,
    },
    "ABLATION_MEAN_AGGREGATION_ONLY_V1": {
        "role": "pre_registered_ablation",
        "model_kwargs": {"repeat_aggregation": "mean_only"},
        "weighted": True,
        "abstention": True,
        "swap_invariant": True,
    },
    "ABLATION_ASYMMETRIC_COMPARATOR_V1": {
        "role": "pre_registered_diagnostic_ablation",
        "model_kwargs": {"comparator_mode": "asymmetric"},
        "weighted": True,
        "abstention": True,
        "swap_invariant": False,
    },
    "ABLATION_UNWEIGHTED_V1": {
        "role": "pre_registered_ablation",
        "model_kwargs": {},
        "weighted": False,
        "abstention": True,
        "swap_invariant": True,
    },
}


class FormalTrainingError(RuntimeError):
    """A numerical, integrity, or frozen-protocol condition failed closed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def set_cpu_determinism(seed: int) -> None:
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise FormalTrainingError("CUDA_VISIBLE_DEVICES_MUST_BE_EMPTY")
    if torch.cuda.is_initialized():
        raise FormalTrainingError("CUDA_CONTEXT_ALREADY_INITIALIZED")
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(int(seed))
    torch.manual_seed(int(seed))
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(2)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True


def slice_bundle(bundle: Mapping[str, Any], indices: torch.Tensor) -> Dict[str, Any]:
    return {
        "model_inputs": {name: bundle["model_inputs"][name].index_select(0, indices) for name in MODEL_INPUT_KEYS},
        "targets": {name: value.index_select(0, indices) for name, value in bundle["targets"].items()},
    }


def _finite_tensor(tensor: torch.Tensor, reason: str) -> None:
    if not bool(torch.isfinite(tensor).all().item()):
        raise FormalTrainingError(reason)


def compute_losses(
    outputs: Mapping[str, torch.Tensor],
    targets: Mapping[str, torch.Tensor],
    weighted: bool,
    abstention: bool,
) -> Dict[str, torch.Tensor]:
    known_mask = targets["task_known_mask"]
    if not bool(known_mask.any().item()):
        raise FormalTrainingError("TRAIN_BATCH_MISSING_KNOWN_TASK")
    task_weights = outputs["task_logits"].new_tensor(TASK_CLASS_WEIGHTS if weighted else (1.0, 1.0))
    unknown_weights = outputs["unknown_logits"].new_tensor(UNKNOWN_CLASS_WEIGHTS if weighted else (1.0, 1.0))
    task_loss = F.cross_entropy(outputs["task_logits"][known_mask], targets["task_target"][known_mask], weight=task_weights)
    if abstention:
        unknown_loss = F.cross_entropy(outputs["unknown_logits"], targets["unknown_target"], weight=unknown_weights)
    else:
        unknown_loss = outputs["unknown_logits"].sum() * 0.0
    repeat = outputs["repeat_embeddings"]
    repeat_mean = repeat.mean(dim=2, keepdim=True)
    repeat_loss = ((repeat - repeat_mean) ** 2).mean()
    total = task_loss + (0.5 * unknown_loss if abstention else 0.0) + 0.1 * repeat_loss
    for name, value in (("task", task_loss), ("abstention", unknown_loss), ("repeat", repeat_loss), ("total", total)):
        _finite_tensor(value, "NONFINITE_%s_LOSS" % name.upper())
    return {"task": task_loss, "abstention": unknown_loss, "repeat": repeat_loss, "total": total}


def infer(model: LearnedM1, bundle: Mapping[str, Any]) -> Dict[str, Any]:
    model.eval()
    with torch.no_grad():
        outputs = model(bundle["model_inputs"])
        task_prob = torch.softmax(outputs["task_logits"], dim=-1)
        unknown_prob = torch.softmax(outputs["unknown_logits"], dim=-1)[:, 1]
    for name in ("task_logits", "unknown_logits"):
        _finite_tensor(outputs[name], "NONFINITE_DEV_LOGITS")
    return {
        "task_logits": outputs["task_logits"].detach().cpu(),
        "unknown_logits": outputs["unknown_logits"].detach().cpu(),
        "task_probabilities": task_prob.detach().cpu(),
        "unknown_probabilities": unknown_prob.detach().cpu(),
    }


def _safe_ratio(numerator: int, denominator: int) -> Optional[float]:
    return float(numerator) / float(denominator) if denominator else None


def classification_metrics(truth: Sequence[int], prediction: Sequence[int]) -> Dict[str, Any]:
    matrix = [[0, 0], [0, 0]]
    for actual, predicted in zip(truth, prediction):
        matrix[int(actual)][int(predicted)] += 1
    per_class = []
    for index, name in enumerate(TASK_NAMES):
        tp = matrix[index][index]
        fp = matrix[1 - index][index]
        fn = matrix[index][1 - index]
        precision = _safe_ratio(tp, tp + fp) if tp + fp else 0.0
        recall = _safe_ratio(tp, tp + fn)
        f1 = None if recall is None else (0.0 if precision + recall == 0 else 2.0 * precision * recall / (precision + recall))
        per_class.append(
            {
                "class": name,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "true_positive": tp,
                "predicted_positive": tp + fp,
                "actual_positive": tp + fn,
            }
        )
    total = len(truth)
    correct = matrix[0][0] + matrix[1][1]
    recalls = [item["recall"] for item in per_class if item["recall"] is not None]
    f1s = [item["f1"] for item in per_class if item["f1"] is not None]
    return {
        "count": total,
        "correct": correct,
        "accuracy": _safe_ratio(correct, total),
        "balanced_accuracy": sum(recalls) / len(recalls) if len(recalls) == 2 else None,
        "macro_f1": sum(f1s) / len(f1s) if len(f1s) == 2 else None,
        "classes": per_class,
        "confusion_matrix_truth_rows_prediction_columns": matrix,
    }


def binary_metrics(truth: Sequence[int], prediction: Sequence[int]) -> Dict[str, Any]:
    tp = sum(int(a == 1 and p == 1) for a, p in zip(truth, prediction))
    fp = sum(int(a == 0 and p == 1) for a, p in zip(truth, prediction))
    tn = sum(int(a == 0 and p == 0) for a, p in zip(truth, prediction))
    fn = sum(int(a == 1 and p == 0) for a, p in zip(truth, prediction))
    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    f1 = None if precision is None or recall is None or precision + recall == 0 else 2.0 * precision * recall / (precision + recall)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "actual_positive": tp + fn,
        "predicted_positive": tp + fp,
        "confusion_matrix_truth_rows_prediction_columns": [[tn, fp], [fn, tp]],
    }


def binary_auroc(truth: Sequence[int], scores: Sequence[float]) -> Optional[float]:
    positives = [score for label, score in zip(truth, scores) if label == 1]
    negatives = [score for label, score in zip(truth, scores) if label == 0]
    if not positives or not negatives:
        return None
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            wins += 1.0 if positive > negative else (0.5 if positive == negative else 0.0)
    return wins / float(len(positives) * len(negatives))


def binary_auprc(truth: Sequence[int], scores: Sequence[float]) -> Optional[float]:
    positive_count = sum(truth)
    if positive_count == 0:
        return None
    order = sorted(range(len(scores)), key=lambda index: (-float(scores[index]), index))
    true_positive = 0
    precision_sum = 0.0
    for rank, index in enumerate(order, 1):
        if truth[index] == 1:
            true_positive += 1
            precision_sum += true_positive / float(rank)
    return precision_sum / float(positive_count)


def calibration_metrics(probabilities: Sequence[float], truth: Sequence[int]) -> Dict[str, Any]:
    rows = []
    weighted_error = 0.0
    total = len(probabilities)
    for bin_index in range(10):
        lower = bin_index / 10.0
        upper = (bin_index + 1) / 10.0
        indices = [
            index
            for index, probability in enumerate(probabilities)
            if probability >= lower and (probability < upper or (bin_index == 9 and probability <= upper))
        ]
        mean_confidence = sum(float(probabilities[index]) for index in indices) / len(indices) if indices else None
        empirical_rate = sum(int(truth[index]) for index in indices) / float(len(indices)) if indices else None
        if indices:
            weighted_error += len(indices) * abs(float(mean_confidence) - float(empirical_rate))
        rows.append(
            {
                "bin": bin_index,
                "lower_inclusive": lower,
                "upper_exclusive_except_final": upper,
                "count": len(indices),
                "mean_confidence": mean_confidence,
                "empirical_rate": empirical_rate,
            }
        )
    brier = sum((float(probability) - int(label)) ** 2 for probability, label in zip(probabilities, truth)) / float(total)
    return {"ece_10_bin": weighted_error / float(total), "brier_score": brier, "reliability_rows": rows, "count": total}


def metrics_from_probabilities(
    task_probabilities: torch.Tensor,
    unknown_probabilities: torch.Tensor,
    targets: Mapping[str, torch.Tensor],
    threshold: float,
    abstention_enabled: bool = True,
) -> Dict[str, Any]:
    task_true_all = [int(value) for value in targets["task_target"].detach().cpu().tolist()]
    known_mask = [bool(value) for value in targets["task_known_mask"].detach().cpu().tolist()]
    unknown_truth = [int(value) for value in targets["unknown_target"].detach().cpu().tolist()]
    task_prediction_all = [int(value) for value in task_probabilities.argmax(dim=-1).tolist()]
    unknown_scores = [float(value) for value in unknown_probabilities.tolist()]
    unknown_prediction = [int(score >= float(threshold)) if abstention_enabled else 0 for score in unknown_scores]
    known_indices = [index for index, known in enumerate(known_mask) if known]
    task_truth = [task_true_all[index] for index in known_indices]
    task_prediction = [task_prediction_all[index] for index in known_indices]
    task_head = classification_metrics(task_truth, task_prediction)
    binary = binary_metrics(unknown_truth, unknown_prediction)
    retained_known_indices = [index for index in known_indices if unknown_prediction[index] == 0]
    selective = classification_metrics(
        [task_true_all[index] for index in retained_known_indices],
        [task_prediction_all[index] for index in retained_known_indices],
    )
    known_coverage_count = len(retained_known_indices)
    known_total = len(known_indices)
    overall_coverage_count = sum(int(value == 0) for value in unknown_prediction)
    unknown_calibration = calibration_metrics(unknown_scores, unknown_truth)
    task_confidences = [float(task_probabilities[index, task_prediction_all[index]].item()) for index in known_indices]
    task_correct = [int(task_prediction_all[index] == task_true_all[index]) for index in known_indices]
    task_calibration = calibration_metrics(task_confidences, task_correct)
    final_predictions = [
        "UNKNOWN" if unknown_prediction[index] else TASK_NAMES[task_prediction_all[index]]
        for index in range(len(task_prediction_all))
    ]
    selective_accuracy = selective["accuracy"]
    return {
        "threshold": float(threshold),
        "task_head_known": task_head,
        "abstention": {
            **binary,
            "auroc": binary_auroc(unknown_truth, unknown_scores),
            "auprc": binary_auprc(unknown_truth, unknown_scores),
        },
        "coverage": {
            "overall_count": overall_coverage_count,
            "overall_denominator": len(unknown_prediction),
            "overall": _safe_ratio(overall_coverage_count, len(unknown_prediction)),
            "known_count": known_coverage_count,
            "known_denominator": known_total,
            "known": _safe_ratio(known_coverage_count, known_total),
        },
        "selective_known": {
            **selective,
            "selective_risk": None if selective_accuracy is None else 1.0 - selective_accuracy,
        },
        "calibration": {"unknown_probability": unknown_calibration, "task_confidence": task_calibration},
        "task_prediction_indices": task_prediction_all,
        "unknown_prediction_indices": unknown_prediction,
        "final_predictions": final_predictions,
    }


def selection_grid_rows(
    epoch: int,
    checkpoint_path: Path,
    checkpoint_sha256: str,
    inference: Mapping[str, Any],
    targets: Mapping[str, torch.Tensor],
    abstention_enabled: bool,
) -> List[Dict[str, Any]]:
    rows = []
    for threshold in UNKNOWN_THRESHOLD_GRID:
        metrics = metrics_from_probabilities(
            inference["task_probabilities"],
            inference["unknown_probabilities"],
            targets,
            threshold,
            abstention_enabled=abstention_enabled,
        )
        unknown = metrics["abstention"]
        known_coverage = metrics["coverage"]["known"]
        eligible = bool(
            abstention_enabled
            and unknown["recall"] is not None
            and unknown["precision"] is not None
            and known_coverage is not None
            and unknown["recall"] >= 0.5
            and unknown["precision"] >= 0.25
            and known_coverage >= 0.5
        )
        rows.append(
            {
                "epoch": int(epoch),
                "threshold": float(threshold),
                "checkpoint_path": str(checkpoint_path),
                "checkpoint_sha256": checkpoint_sha256,
                "eligible": eligible,
                "unknown_exact_counts": {
                    name: unknown[name]
                    for name in ("true_positive", "false_positive", "true_negative", "false_negative", "actual_positive", "predicted_positive")
                },
                "unknown_precision": unknown["precision"],
                "unknown_recall": unknown["recall"],
                "unknown_f1": unknown["f1"],
                "known_coverage": known_coverage,
                "known_coverage_count": metrics["coverage"]["known_count"],
                "known_coverage_denominator": metrics["coverage"]["known_denominator"],
                "coverage": metrics["coverage"]["overall"],
                "known_task_macro_f1": metrics["task_head_known"]["macro_f1"],
                "selective_risk": metrics["selective_known"]["selective_risk"],
                "unknown_brier_score": metrics["calibration"]["unknown_probability"]["brier_score"],
            }
        )
    return rows


def selection_rank(row: Mapping[str, Any]) -> Tuple[Any, ...]:
    if not row["eligible"]:
        raise FormalTrainingError("INELIGIBLE_ROW_HAS_NO_SELECTION_RANK")
    macro_f1 = row["known_task_macro_f1"]
    selective_risk = row["selective_risk"]
    return (
        -float(macro_f1 if macro_f1 is not None else -1.0),
        float(selective_risk if selective_risk is not None else 1.0),
        -float(row["coverage"] if row["coverage"] is not None else 0.0),
        float(row["unknown_brier_score"]),
        int(row["epoch"]),
        str(row["checkpoint_sha256"]),
    )


def _cpu_copy(value: Any) -> Any:
    if isinstance(value, torch.Tensor):
        return value.detach().cpu()
    if isinstance(value, dict):
        return {key: _cpu_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_cpu_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_cpu_copy(item) for item in value)
    return value


def save_formal_checkpoint(
    path: Path,
    model: LearnedM1,
    optimizer: torch.optim.Optimizer,
    seed: int,
    epoch: int,
    model_kwargs: Mapping[str, Any],
    loss_configuration: Mapping[str, Any],
    hashes: Mapping[str, str],
) -> Dict[str, Any]:
    payload = {
        "schema_version": "driveclarify.learned_m1_checkpoint.formal.v1",
        "model_class": "LearnedM1",
        "model_kwargs": dict(model_kwargs),
        "model_state_dict": _cpu_copy(model.state_dict()),
        "model_state_sha256": state_dict_sha256(model.state_dict()),
        "optimizer_state_dict": _cpu_copy(optimizer.state_dict()),
        "seed": int(seed),
        "epoch": int(epoch),
        "training_config_sha256": hashes["training_config_sha256"],
        "dataset_index_sha256": hashes["dataset_index_sha256"],
        "feature_contract_sha256": hashes["feature_contract_sha256"],
        "model_spec_sha256": hashes["model_spec_sha256"],
        "protocol_seal_sha256": hashes["protocol_seal_sha256"],
        "tensors_saved_on_cpu": True,
        "device_agnostic_state": True,
        "loss_configuration": dict(loss_configuration),
        "training_protocol_sha256": hashes["training_protocol_sha256"],
        "configuration_id": hashes["configuration_id"],
    }
    validate_formal_checkpoint(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, str(path))
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "state_dict_sha256": payload["model_state_sha256"],
        "epoch": int(epoch),
        "seed": int(seed),
    }


def load_formal_checkpoint(path: Path) -> Tuple[LearnedM1, Dict[str, Any]]:
    try:
        payload = torch.load(str(path), map_location="cpu", weights_only=False)
    except TypeError:
        payload = torch.load(str(path), map_location="cpu")
    validate_formal_checkpoint(payload)
    model = LearnedM1(**payload["model_kwargs"])
    model.load_state_dict(payload["model_state_dict"], strict=True)
    if state_dict_sha256(model.state_dict()) != payload["model_state_sha256"]:
        raise FormalTrainingError("CHECKPOINT_STATE_HASH_MISMATCH")
    return model, payload


def _loss_row(loss_totals: Mapping[str, float], count: int) -> Dict[str, float]:
    return {name: float(value) / float(count) for name, value in loss_totals.items()}


def train_learned_variant(
    configuration_id: str,
    seed: int,
    train_bundle: Mapping[str, Any],
    dev_bundle: Mapping[str, Any],
    train_unit_ids: Sequence[str],
    dev_unit_ids: Sequence[str],
    output_root: Path,
    hashes: Mapping[str, str],
    runtime_counters: Dict[str, int],
    maximum_epochs: int = 300,
    patience: int = 40,
) -> Dict[str, Any]:
    if configuration_id not in VARIANTS or seed not in FORMAL_SEEDS:
        raise FormalTrainingError("UNREGISTERED_CONFIGURATION_OR_SEED")
    spec = VARIANTS[configuration_id]
    set_cpu_determinism(seed)
    model_kwargs = {"hidden_dim": 64, "embedding_dim": 32, **spec["model_kwargs"]}
    model = LearnedM1(**model_kwargs)
    if configuration_id == "MAIN_WEIGHTED_PILOT_NEIGHBORHOOD_V1" and trainable_parameter_count(model) != 51684:
        raise FormalTrainingError("MAIN_PARAMETER_COUNT_NOT_51684")
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.0001)
    seed_root = Path(output_root) / configuration_id / ("seed_%d" % seed)
    epoch_root = seed_root / "epoch_checkpoints"
    epoch_root.mkdir(parents=True, exist_ok=False)
    loss_configuration = {
        "task_loss_weight": 1.0,
        "abstention_loss_weight": 0.5 if spec["abstention"] else 0.0,
        "repeat_loss_weight": 0.1,
        "weighted": bool(spec["weighted"]),
        "known_task_class_weights": list(TASK_CLASS_WEIGHTS if spec["weighted"] else (1.0, 1.0)),
        "abstention_class_weights": list(UNKNOWN_CLASS_WEIGHTS if spec["weighted"] else (1.0, 1.0)),
    }
    checkpoint_inventory = []
    curve = []
    grid_rows: List[Dict[str, Any]] = []
    dev_epoch_outputs = []
    best_eligible_rank: Optional[Tuple[Any, ...]] = None
    epochs_without_improvement = 0
    best_task_macro_f1 = -1.0
    initial_state_sha256 = state_dict_sha256(model.state_dict())
    run_started = time.monotonic()

    for epoch in range(1, int(maximum_epochs) + 1):
        epoch_started = time.monotonic()
        model.train()
        permutation = torch.randperm(len(train_unit_ids))
        loss_totals = {"task": 0.0, "abstention": 0.0, "repeat": 0.0, "total": 0.0}
        gradient_norms = []
        epoch_count = 0
        for start in range(0, len(train_unit_ids), 5):
            indices = permutation[start : start + 5]
            batch = slice_bundle(train_bundle, indices)
            optimizer.zero_grad(set_to_none=True)
            outputs = model(batch["model_inputs"])
            losses = compute_losses(outputs, batch["targets"], bool(spec["weighted"]), bool(spec["abstention"]))
            losses["total"].backward()
            runtime_counters["backward"] += 1
            for parameter in model.parameters():
                if parameter.grad is not None:
                    _finite_tensor(parameter.grad, "NONFINITE_GRADIENT")
            gradient_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            _finite_tensor(gradient_norm, "NONFINITE_GRADIENT_NORM")
            optimizer.step()
            runtime_counters["optimizer_step"] += 1
            runtime_counters["train_parameter_update_batches"] += 1
            batch_count = int(indices.numel())
            epoch_count += batch_count
            gradient_norms.append(float(gradient_norm.detach().cpu().item()))
            for name in loss_totals:
                loss_totals[name] += float(losses[name].detach().cpu().item()) * batch_count
            for parameter in model.parameters():
                _finite_tensor(parameter, "NONFINITE_PARAMETER")
        if epoch_count != 25:
            raise FormalTrainingError("TRAIN_EPOCH_UNIT_COUNT_NOT_25")

        checkpoint_path = epoch_root / ("epoch_%03d.pt" % epoch)
        checkpoint_hashes = dict(hashes)
        checkpoint_hashes["configuration_id"] = configuration_id
        checkpoint = save_formal_checkpoint(
            checkpoint_path,
            model,
            optimizer,
            seed,
            epoch,
            model_kwargs,
            loss_configuration,
            checkpoint_hashes,
        )
        checkpoint_inventory.append(checkpoint)
        runtime_counters["checkpoint_save"] += 1
        dev_inference = infer(model, dev_bundle)
        runtime_counters["dev_forward_epochs"] += 1
        epoch_grid = selection_grid_rows(
            epoch,
            checkpoint_path,
            checkpoint["sha256"],
            dev_inference,
            dev_bundle["targets"],
            bool(spec["abstention"]),
        )
        for row in epoch_grid:
            row["configuration_id"] = configuration_id
            row["seed"] = seed
        grid_rows.extend(epoch_grid)
        dev_epoch_outputs.append(
            {
                "epoch": epoch,
                "checkpoint_sha256": checkpoint["sha256"],
                "unit_outputs": [
                    {
                        "unit_id": dev_unit_ids[index],
                        "task_logits": [float(value) for value in dev_inference["task_logits"][index].tolist()],
                        "abstention_logits": [float(value) for value in dev_inference["unknown_logits"][index].tolist()],
                        "task_prediction": TASK_NAMES[int(dev_inference["task_probabilities"][index].argmax().item())],
                        "unknown_probability": float(dev_inference["unknown_probabilities"][index].item()),
                    }
                    for index in range(len(dev_unit_ids))
                ],
            }
        )
        task_macro_f1 = epoch_grid[0]["known_task_macro_f1"]
        curve.append(
            {
                "epoch": epoch,
                "train_losses": _loss_row(loss_totals, epoch_count),
                "gradient_norm_before_clip_mean": sum(gradient_norms) / len(gradient_norms),
                "gradient_norm_before_clip_max": max(gradient_norms),
                "parameter_finiteness": "PASS",
                "dev_task_macro_f1": task_macro_f1,
                "dev_unknown_brier_score": epoch_grid[0]["unknown_brier_score"],
                "epoch_wall_time_seconds": time.monotonic() - epoch_started,
            }
        )

        eligible = [row for row in epoch_grid if row["eligible"]]
        improved = False
        if spec["abstention"] and eligible:
            epoch_best_rank = min(selection_rank(row) for row in eligible)
            if best_eligible_rank is None or epoch_best_rank < best_eligible_rank:
                best_eligible_rank = epoch_best_rank
                improved = True
        elif not spec["abstention"]:
            current = float(task_macro_f1 if task_macro_f1 is not None else -1.0)
            if current > best_task_macro_f1:
                best_task_macro_f1 = current
                improved = True
        if improved:
            epochs_without_improvement = 0
        elif best_eligible_rank is not None or not spec["abstention"]:
            epochs_without_improvement += 1
        if epoch == 1 or epoch % 20 == 0:
            print(
                "PROGRESS configuration=%s seed=%d epoch=%d eligible=%d dev_macro_f1=%s"
                % (configuration_id, seed, epoch, len(eligible), str(task_macro_f1)),
                flush=True,
            )
        if epochs_without_improvement >= int(patience):
            break

    write_json(seed_root / "DEV_CHECKPOINT_THRESHOLD_GRID.json", grid_rows)
    write_json(seed_root / "DEV_EPOCH_OUTPUTS.json", dev_epoch_outputs)
    write_json(seed_root / "TRAINING_CURVE.json", curve)
    write_json(seed_root / "CHECKPOINT_INVENTORY.json", checkpoint_inventory)

    if spec["abstention"]:
        eligible_all = [row for row in grid_rows if row["eligible"]]
        selected_row = min(eligible_all, key=selection_rank) if eligible_all else None
    else:
        best_macro = max(float(row["known_task_macro_f1"] or -1.0) for row in grid_rows)
        selected_row = min(
            (row for row in grid_rows if float(row["known_task_macro_f1"] or -1.0) == best_macro),
            key=lambda row: (int(row["epoch"]), str(row["checkpoint_sha256"])),
        )
    selected = None
    train_metrics = None
    dev_metrics = None
    unit_predictions = None
    if selected_row is not None:
        source_path = Path(selected_row["checkpoint_path"])
        selected_path = seed_root / "selected_checkpoint.pt"
        shutil.copy2(str(source_path), str(selected_path))
        selected_model, selected_payload = load_formal_checkpoint(selected_path)
        if selected_payload["seed"] != seed or selected_payload["epoch"] != selected_row["epoch"]:
            raise FormalTrainingError("SELECTED_CHECKPOINT_SEED_EPOCH_MISMATCH")
        selected_sha = sha256_file(selected_path)
        train_inference = infer(selected_model, train_bundle)
        dev_inference = infer(selected_model, dev_bundle)
        threshold = float(selected_row["threshold"] if spec["abstention"] else 1.0)
        train_metrics = metrics_from_probabilities(
            train_inference["task_probabilities"],
            train_inference["unknown_probabilities"],
            train_bundle["targets"],
            threshold,
            abstention_enabled=bool(spec["abstention"]),
        )
        dev_metrics = metrics_from_probabilities(
            dev_inference["task_probabilities"],
            dev_inference["unknown_probabilities"],
            dev_bundle["targets"],
            threshold,
            abstention_enabled=bool(spec["abstention"]),
        )
        dev_metrics["risk_coverage_rows"] = [
            {
                "threshold": grid_threshold,
                "coverage": grid_metrics["coverage"]["overall"],
                "known_coverage": grid_metrics["coverage"]["known"],
                "selective_accuracy": grid_metrics["selective_known"]["accuracy"],
                "selective_risk": grid_metrics["selective_known"]["selective_risk"],
                "unknown_exact_counts": {
                    name: grid_metrics["abstention"][name]
                    for name in ("true_positive", "false_positive", "true_negative", "false_negative")
                },
            }
            for grid_threshold in UNKNOWN_THRESHOLD_GRID
            for grid_metrics in [
                metrics_from_probabilities(
                    dev_inference["task_probabilities"],
                    dev_inference["unknown_probabilities"],
                    dev_bundle["targets"],
                    grid_threshold,
                    abstention_enabled=bool(spec["abstention"]),
                )
            ]
        ]
        unit_predictions = [
            {
                "unit_id": dev_unit_ids[index],
                "seed": seed,
                "task_logits": [float(value) for value in dev_inference["task_logits"][index].tolist()],
                "abstention_logits": [float(value) for value in dev_inference["unknown_logits"][index].tolist()],
                "task_prediction": TASK_NAMES[dev_metrics["task_prediction_indices"][index]],
                "unknown_probability": float(dev_inference["unknown_probabilities"][index].item()),
                "unknown_threshold": threshold,
                "learned_unknown": bool(dev_metrics["unknown_prediction_indices"][index]),
                "hard_gate_unknown": False,
                "final_prediction": dev_metrics["final_predictions"][index],
            }
            for index in range(len(dev_unit_ids))
        ]
        selected = {
            **selected_row,
            "selected_checkpoint_path": str(selected_path),
            "selected_checkpoint_sha256": selected_sha,
            "state_dict_sha256": selected_payload["model_state_sha256"],
            "selection_status": (
                "ELIGIBLE_FROZEN_DEV_PAIR_SELECTED" if spec["abstention"] else "TASK_ONLY_DEV_CHECKPOINT_SELECTED_NO_SCIENTIFIC_UNKNOWN_OUTPUT"
            ),
        }
        write_json(seed_root / "SELECTED_CHECKPOINT.json", selected)

    return {
        "configuration_id": configuration_id,
        "role": spec["role"],
        "seed": seed,
        "model_kwargs": model_kwargs,
        "trainable_parameter_count": trainable_parameter_count(model),
        "weighted_losses": bool(spec["weighted"]),
        "abstention_enabled": bool(spec["abstention"]),
        "swap_invariance_expected": bool(spec["swap_invariant"]),
        "initial_state_sha256": initial_state_sha256,
        "final_state_sha256": state_dict_sha256(model.state_dict()),
        "epochs_completed": len(curve),
        "early_stopped": len(curve) < int(maximum_epochs),
        "checkpoint_count": len(checkpoint_inventory),
        "checkpoint_inventory_path": str(seed_root / "CHECKPOINT_INVENTORY.json"),
        "grid_path": str(seed_root / "DEV_CHECKPOINT_THRESHOLD_GRID.json"),
        "dev_epoch_outputs_path": str(seed_root / "DEV_EPOCH_OUTPUTS.json"),
        "curve_path": str(seed_root / "TRAINING_CURVE.json"),
        "eligible_pair_count": sum(int(row["eligible"]) for row in grid_rows),
        "selection": selected,
        "train_metrics": train_metrics,
        "dev_metrics": dev_metrics,
        "dev_unit_predictions": unit_predictions,
        "wall_time_seconds": time.monotonic() - run_started,
        "dev_backward_count": 0,
        "dev_optimizer_step_count": 0,
        "test_tensorization_count": 0,
        "test_prediction_count": 0,
        "test_metric_count": 0,
    }


def _summary_features(bundle: Mapping[str, Any]) -> torch.Tensor:
    route = bundle["model_inputs"]["route"]
    speed = bundle["model_inputs"]["speed"]
    route_mean = route.mean(dim=2)
    speed_mean = speed.mean(dim=2)
    route_difference = torch.sqrt(((route_mean[:, 0] - route_mean[:, 1]) ** 2).mean(dim=(1, 2)))
    speed_difference = torch.sqrt(((speed_mean[:, 0] - speed_mean[:, 1]) ** 2).mean(dim=(1, 2)))
    route_repeat = route.std(dim=2, unbiased=False).mean(dim=(1, 2, 3))
    speed_repeat = speed.std(dim=2, unbiased=False).mean(dim=(1, 2, 3))
    return torch.stack([route_difference, speed_difference, route_repeat, speed_repeat], dim=1)


def deterministic_baselines(
    train_bundle: Mapping[str, Any],
    dev_bundle: Mapping[str, Any],
    dev_unit_ids: Sequence[str],
) -> Dict[str, Any]:
    train_targets = train_bundle["targets"]
    dev_targets = dev_bundle["targets"]
    known_train = train_targets["task_known_mask"]
    task_train = train_targets["task_target"][known_train]
    majority = int(torch.bincount(task_train, minlength=2).argmax().item())
    unknown_prior = float(train_targets["unknown_target"].float().mean().item())
    dev_count = int(dev_targets["task_target"].numel())
    majority_task = torch.zeros((dev_count, 2), dtype=torch.float32)
    majority_task[:, majority] = 1.0
    majority_unknown = torch.full((dev_count,), unknown_prior, dtype=torch.float32)
    majority_metrics = metrics_from_probabilities(majority_task, majority_unknown, dev_targets, 0.5, True)

    train_features = _summary_features(train_bundle)
    dev_features = _summary_features(dev_bundle)
    mean = train_features.mean(dim=0)
    scale = train_features.std(dim=0, unbiased=False).clamp_min(1.0e-6)
    train_standard = (train_features - mean) / scale
    dev_standard = (dev_features - mean) / scale
    centroids = torch.stack([train_standard[(train_targets["task_target"] == index) & known_train].mean(dim=0) for index in (0, 1)])
    dev_distances = torch.cdist(dev_standard, centroids)
    task_probabilities = torch.softmax(-dev_distances, dim=1)
    known_train_distances = torch.cdist(train_standard[known_train], centroids).min(dim=1).values
    location = known_train_distances.median()
    spread = (known_train_distances - location).abs().median().clamp_min(1.0e-6)
    anomaly = dev_distances.min(dim=1).values
    unknown_probability = torch.sigmoid((anomaly - location) / spread)
    rule_rows = []
    for threshold in UNKNOWN_THRESHOLD_GRID:
        metrics = metrics_from_probabilities(task_probabilities, unknown_probability, dev_targets, threshold, True)
        eligible = bool(
            metrics["abstention"]["recall"] is not None
            and metrics["abstention"]["precision"] is not None
            and metrics["abstention"]["recall"] >= 0.5
            and metrics["abstention"]["precision"] >= 0.25
            and metrics["coverage"]["known"] >= 0.5
        )
        rule_rows.append({"threshold": threshold, "eligible": eligible, "metrics": metrics})
    eligible_rows = [row for row in rule_rows if row["eligible"]]
    if eligible_rows:
        rule_selected = min(
            eligible_rows,
            key=lambda row: (
                -float(row["metrics"]["task_head_known"]["macro_f1"] or -1.0),
                float(row["metrics"]["selective_known"]["selective_risk"] or 1.0),
                -float(row["metrics"]["coverage"]["overall"] or 0.0),
                float(row["metrics"]["calibration"]["unknown_probability"]["brier_score"]),
                float(row["threshold"]),
            ),
        )
    else:
        rule_selected = min(
            rule_rows,
            key=lambda row: (-float(row["metrics"]["task_head_known"]["macro_f1"] or -1.0), float(row["threshold"])),
        )
    rule_unit_predictions = [
        {
            "unit_id": dev_unit_ids[index],
            "task_prediction": TASK_NAMES[int(task_probabilities[index].argmax().item())],
            "unknown_probability": float(unknown_probability[index].item()),
            "final_prediction": rule_selected["metrics"]["final_predictions"][index],
        }
        for index in range(dev_count)
    ]
    return {
        "majority_prior": {
            "deterministic": True,
            "learned_checkpoint_created": False,
            "candidate_or_topology_read": False,
            "train_known_majority": TASK_NAMES[majority],
            "train_unknown_prior": unknown_prior,
            "dev_metrics_at_threshold_0_5": majority_metrics,
            "feasibility_constraints_satisfied": False,
        },
        "rule_based_deterministic_comparator": {
            "deterministic": True,
            "learned_checkpoint_created": False,
            "allowed_features": ["route_A_B_RMSE", "speed_A_B_RMSE", "route_repeat_std", "speed_repeat_std"],
            "forbidden_mapper_or_label_synonym_features": [],
            "method": "TRAIN-known standardized nearest-centroid task rule plus distance anomaly; DEV frozen threshold grid only",
            "train_feature_mean": [float(value) for value in mean.tolist()],
            "train_feature_scale": [float(value) for value in scale.tolist()],
            "train_known_centroids": [[float(value) for value in row] for row in centroids.tolist()],
            "threshold_grid": rule_rows,
            "selected_threshold": rule_selected["threshold"],
            "selected_metrics": rule_selected["metrics"],
            "feasibility_constraints_satisfied": rule_selected["eligible"],
            "dev_unit_predictions": rule_unit_predictions,
        },
    }


def mean_std(values: Sequence[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"mean": None, "std_population": None, "count": 0}
    mean = sum(float(value) for value in values) / len(values)
    variance = sum((float(value) - mean) ** 2 for value in values) / len(values)
    return {"mean": mean, "std_population": math.sqrt(variance), "count": len(values)}


def seed_stability(main_runs: Sequence[Mapping[str, Any]], dev_unit_ids: Sequence[str]) -> Dict[str, Any]:
    selected = [run for run in main_runs if run.get("selection") and run.get("dev_unit_predictions")]
    pairwise = []
    for first_index in range(len(selected)):
        for second_index in range(first_index + 1, len(selected)):
            first = selected[first_index]
            second = selected[second_index]
            first_predictions = [row["final_prediction"] for row in first["dev_unit_predictions"]]
            second_predictions = [row["final_prediction"] for row in second["dev_unit_predictions"]]
            agreement_count = sum(int(a == b) for a, b in zip(first_predictions, second_predictions))
            pairwise.append(
                {
                    "seed_a": first["seed"],
                    "seed_b": second["seed"],
                    "agreement_count": agreement_count,
                    "denominator": len(dev_unit_ids),
                    "agreement": agreement_count / float(len(dev_unit_ids)),
                }
            )
    five_way_count = 0
    if len(selected) == 5:
        for unit_index in range(len(dev_unit_ids)):
            predictions = {run["dev_unit_predictions"][unit_index]["final_prediction"] for run in selected}
            five_way_count += int(len(predictions) == 1)
    metrics = {
        "dev_task_macro_f1": mean_std(
            [run["dev_metrics"]["task_head_known"]["macro_f1"] for run in selected if run["dev_metrics"]["task_head_known"]["macro_f1"] is not None]
        ),
        "dev_selective_risk": mean_std(
            [run["dev_metrics"]["selective_known"]["selective_risk"] for run in selected if run["dev_metrics"]["selective_known"]["selective_risk"] is not None]
        ),
        "dev_unknown_recall": mean_std(
            [run["dev_metrics"]["abstention"]["recall"] for run in selected if run["dev_metrics"]["abstention"]["recall"] is not None]
        ),
        "dev_unknown_precision": mean_std(
            [run["dev_metrics"]["abstention"]["precision"] for run in selected if run["dev_metrics"]["abstention"]["precision"] is not None]
        ),
        "selected_epoch": mean_std([float(run["selection"]["epoch"]) for run in selected]),
        "selected_threshold": mean_std([float(run["selection"]["threshold"]) for run in selected]),
    }
    return {
        "selected_seed_count": len(selected),
        "metric_mean_std": metrics,
        "pairwise_agreement": pairwise,
        "five_way_agreement_count": five_way_count if len(selected) == 5 else None,
        "five_way_agreement_denominator": len(dev_unit_ids),
        "five_way_agreement": five_way_count / float(len(dev_unit_ids)) if len(selected) == 5 else None,
        "checkpoint_hashes": [run["selection"]["selected_checkpoint_sha256"] for run in selected],
    }
