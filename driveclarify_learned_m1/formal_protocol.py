"""Frozen, training-free Formal Learned M1 decision and TEST seal contracts."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Sequence


FORMAL_SEEDS = (17, 29, 43, 59, 71)
TASK_NAMES = ("TASK_EQUIVALENT", "TASK_CRITICAL")
UNKNOWN_THRESHOLD_GRID = tuple(round(0.30 + 0.05 * index, 2) for index in range(11))


class FormalProtocolError(RuntimeError):
    """A frozen protocol or lifecycle invariant failed closed."""


@dataclass(frozen=True)
class FormalTrainingConfig:
    model_variant: str = "V2_SMALL_SHARED_ENCODER_MLP"
    hidden_dim: int = 64
    embedding_dim: int = 32
    optimizer: str = "AdamW"
    learning_rate: float = 1.0e-3
    weight_decay: float = 1.0e-4
    unit_batch_size: int = 5
    maximum_epochs: int = 300
    early_stopping_patience: int = 40
    gradient_clip_norm: float = 1.0
    lambda_unknown: float = 0.5
    lambda_repeat: float = 0.1
    checkpoint_frequency_epochs: int = 1
    deterministic_algorithms: bool = True
    seeds: Sequence[int] = FORMAL_SEEDS
    test_use: str = "FORBIDDEN_DURING_TRAINING_SELECTION_AND_CALIBRATION"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def seal_payload(value: Mapping[str, Any]) -> Dict[str, Any]:
    sealed = dict(value)
    sealed.pop("sha256", None)
    sealed["sha256"] = canonical_sha256(sealed)
    return sealed


def verify_seal(value: Mapping[str, Any]) -> None:
    expected = str(value.get("sha256", ""))
    unsealed = dict(value)
    unsealed.pop("sha256", None)
    if len(expected) != 64 or canonical_sha256(unsealed) != expected:
        raise FormalProtocolError("PROTOCOL_SEAL_HASH_MISMATCH")


def assert_cpu_only(device: str = "cpu") -> None:
    """Fail before model work if this assessment could create a CUDA context."""

    if str(device) != "cpu":
        raise FormalProtocolError("FORMAL_ASSESSMENT_CPU_ONLY")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise FormalProtocolError("CUDA_VISIBLE_DEVICES_MUST_BE_EMPTY")
    import torch

    if torch.cuda.is_initialized():
        raise FormalProtocolError("CUDA_CONTEXT_ALREADY_INITIALIZED")


def _stable_softmax_pair(logits: Sequence[float]) -> Sequence[float]:
    if len(logits) != 2 or not all(math.isfinite(float(value)) for value in logits):
        raise FormalProtocolError("NONFINITE_OR_MISSING_LOGITS")
    maximum = max(float(value) for value in logits)
    exp_values = [math.exp(float(value) - maximum) for value in logits]
    total = sum(exp_values)
    if not math.isfinite(total) or total <= 0.0:
        raise FormalProtocolError("INVALID_SOFTMAX_DENOMINATOR")
    return [value / total for value in exp_values]


def combine_prediction(
    task_logits: Optional[Sequence[float]],
    unknown_logits: Optional[Sequence[float]],
    unknown_threshold: float,
    hard_evidence_gate: bool,
) -> Dict[str, Any]:
    """Combine dual heads; abstention vetoes task confidence.

    Missing or non-finite evidence returns UNKNOWN rather than fabricating a
    numeric input.  UNKNOWN is an evidence outcome, not a third task class.
    """

    if not hard_evidence_gate:
        return {"final_decision": "UNKNOWN", "decision_source": "HARD_EVIDENCE_GATE", "fail_closed": True}
    if not math.isfinite(float(unknown_threshold)) or not 0.0 <= float(unknown_threshold) <= 1.0:
        raise FormalProtocolError("INVALID_UNKNOWN_THRESHOLD")
    try:
        task_probability = _stable_softmax_pair(task_logits or ())
        unknown_probability = _stable_softmax_pair(unknown_logits or ())
    except (FormalProtocolError, TypeError, ValueError):
        return {"final_decision": "UNKNOWN", "decision_source": "NONFINITE_OR_MISSING_FAIL_CLOSED", "fail_closed": True}
    task_index = 0 if task_probability[0] >= task_probability[1] else 1
    if unknown_probability[1] >= float(unknown_threshold):
        final = "UNKNOWN"
        source = "LEARNED_ABSTENTION_THRESHOLD_VETO"
    else:
        final = TASK_NAMES[task_index]
        source = "TASK_HEAD"
    return {
        "final_decision": final,
        "decision_source": source,
        "fail_closed": False,
        "task_prediction": TASK_NAMES[task_index],
        "task_confidence": task_probability[task_index],
        "unknown_probability": unknown_probability[1],
        "unknown_threshold": float(unknown_threshold),
    }


def validate_formal_checkpoint(payload: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "model_class",
        "model_kwargs",
        "model_state_dict",
        "model_state_sha256",
        "optimizer_state_dict",
        "seed",
        "epoch",
        "training_config_sha256",
        "dataset_index_sha256",
        "feature_contract_sha256",
        "model_spec_sha256",
        "protocol_seal_sha256",
        "tensors_saved_on_cpu",
        "device_agnostic_state",
    }
    formal_training_extensions = {
        "loss_configuration",
        "training_protocol_sha256",
        "configuration_id",
    }
    allowed = required | formal_training_extensions
    if not required.issubset(payload) or not set(payload).issubset(allowed):
        raise FormalProtocolError("FORMAL_CHECKPOINT_SCHEMA_KEY_MISMATCH")
    if set(payload) & formal_training_extensions and not formal_training_extensions.issubset(payload):
        raise FormalProtocolError("FORMAL_CHECKPOINT_TRAINING_EXTENSION_INCOMPLETE")
    if payload["schema_version"] != "driveclarify.learned_m1_checkpoint.formal.v1":
        raise FormalProtocolError("FORMAL_CHECKPOINT_SCHEMA_VERSION_MISMATCH")
    for name in (
        "model_state_sha256",
        "training_config_sha256",
        "dataset_index_sha256",
        "feature_contract_sha256",
        "model_spec_sha256",
        "protocol_seal_sha256",
    ):
        if len(str(payload[name])) != 64:
            raise FormalProtocolError("FORMAL_CHECKPOINT_HASH_INVALID:%s" % name)
    if "training_protocol_sha256" in payload and len(str(payload["training_protocol_sha256"])) != 64:
        raise FormalProtocolError("FORMAL_CHECKPOINT_HASH_INVALID:training_protocol_sha256")
    if payload["tensors_saved_on_cpu"] is not True or payload["device_agnostic_state"] is not True:
        raise FormalProtocolError("FORMAL_CHECKPOINT_NOT_CPU_CUDA_PORTABLE")
    forbidden = {"test_metrics", "test_predictions", "test_accuracy", "test_f1"}
    if forbidden & set(payload):
        raise FormalProtocolError("TEST_INFORMATION_IN_FORMAL_CHECKPOINT")


def create_test_seal(protocol_sha256: str, dataset_index_sha256: str) -> Dict[str, Any]:
    for value in (protocol_sha256, dataset_index_sha256):
        if len(str(value)) != 64:
            raise FormalProtocolError("TEST_SEAL_INPUT_HASH_INVALID")
    return seal_payload(
        {
            "schema_version": "driveclarify.formal_m1_test_seal.v1",
            "status": "PROTOCOL_FROZEN_TEST_NOT_AUTHORIZED",
            "protocol_sha256": protocol_sha256,
            "dataset_index_sha256": dataset_index_sha256,
            "pretest_verifier_sha256": None,
            "test_evaluation_count": 0,
            "test_prediction_sha256": None,
            "test_prediction_record_count": 0,
            "test_prediction_completeness": "NONE",
            "publication_count": 0,
            "engineering_resume_allowed": False,
            "first_result_overwrite_allowed": False,
        }
    )


def mark_pretest_verified(seal: Mapping[str, Any], verifier_sha256: str) -> Dict[str, Any]:
    verify_seal(seal)
    if seal["status"] != "PROTOCOL_FROZEN_TEST_NOT_AUTHORIZED":
        raise FormalProtocolError("PRETEST_VERIFICATION_INVALID_STATE")
    if len(str(verifier_sha256)) != 64:
        raise FormalProtocolError("PRETEST_VERIFIER_HASH_INVALID")
    updated = dict(seal)
    updated["status"] = "PRETEST_VERIFIED_AWAITING_ONE_TIME_TEST_AUTHORIZATION"
    updated["pretest_verifier_sha256"] = verifier_sha256
    return seal_payload(updated)


def record_test_predictions(seal: Mapping[str, Any], prediction_bytes: bytes, record_count: int, complete: bool) -> Dict[str, Any]:
    verify_seal(seal)
    if seal["status"] != "PRETEST_VERIFIED_AWAITING_ONE_TIME_TEST_AUTHORIZATION":
        raise FormalProtocolError("TEST_EVALUATION_NOT_AUTHORIZED_OR_ALREADY_CONSUMED")
    if seal["test_evaluation_count"] != 0 or seal["test_prediction_sha256"] is not None:
        raise FormalProtocolError("DUPLICATE_TEST_EVALUATION_FORBIDDEN")
    if not prediction_bytes or int(record_count) <= 0:
        raise FormalProtocolError("EMPTY_TEST_PREDICTION_PUBLICATION")
    updated = dict(seal)
    updated["status"] = "TEST_PREDICTIONS_RECORDED_IMMUTABLE"
    updated["test_evaluation_count"] = 1
    updated["test_prediction_sha256"] = hashlib.sha256(prediction_bytes).hexdigest()
    updated["test_prediction_record_count"] = int(record_count)
    updated["test_prediction_completeness"] = "COMPLETE" if complete else "PARTIAL_PRESERVED"
    updated["engineering_resume_allowed"] = False
    return seal_payload(updated)


def record_engineering_interruption(seal: Mapping[str, Any], prediction_bytes: bytes = b"") -> Dict[str, Any]:
    verify_seal(seal)
    if seal["status"] != "PRETEST_VERIFIED_AWAITING_ONE_TIME_TEST_AUTHORIZATION":
        raise FormalProtocolError("ENGINEERING_INTERRUPTION_INVALID_STATE")
    updated = dict(seal)
    if prediction_bytes:
        updated["status"] = "PARTIAL_TEST_PREDICTIONS_PRESERVED_EVALUATION_CONSUMED"
        updated["test_evaluation_count"] = 1
        updated["test_prediction_sha256"] = hashlib.sha256(prediction_bytes).hexdigest()
        updated["test_prediction_completeness"] = "PARTIAL_PRESERVED"
        updated["engineering_resume_allowed"] = False
    else:
        updated["status"] = "ENGINEERING_INTERRUPTION_ZERO_PREDICTIONS_RESUMABLE_SAME_SEALED_RUN"
        updated["engineering_resume_allowed"] = True
    return seal_payload(updated)


def publish_test_result(seal: Mapping[str, Any], result_sha256: str) -> Dict[str, Any]:
    verify_seal(seal)
    if seal["status"] != "TEST_PREDICTIONS_RECORDED_IMMUTABLE" or seal["publication_count"] != 0:
        raise FormalProtocolError("DUPLICATE_OR_INVALID_TEST_PUBLICATION")
    if len(str(result_sha256)) != 64:
        raise FormalProtocolError("TEST_RESULT_HASH_INVALID")
    updated = dict(seal)
    updated["status"] = "FIRST_TEST_RESULT_PUBLISHED_IMMUTABLE"
    updated["publication_count"] = 1
    updated["test_result_sha256"] = result_sha256
    return seal_payload(updated)
