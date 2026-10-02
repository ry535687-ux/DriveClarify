"""Contracts shared by the ACT/ASK/WAIT controlled-integration pilot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Mapping


FEATURE_FLAG = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1"
RUNTIME_VERSION = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_CONTROLLED_INTEGRATION_V1"
OUTPUT_ENV = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_OUTPUT_DIR"
CONTROL_ENV = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_CONTROL"
DEVICE_ENV = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_DETECTOR_DEVICE"
INSTRUCTION_ENV = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_RAW_INSTRUCTION"
TRIGGER_FRAME_ENV = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_TRIGGER_FRAME"
ANSWER_ENV = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_PASSENGER_ANSWER"
ANSWER_DELAY_ENV = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_ANSWER_DELAY_SIM_SECONDS"
VISUALIZATION_ENV = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_VISUALIZATION"
RECEIPT_FILENAME = "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"

FORBIDDEN_POLICY_KEYS = frozenset(
    {
        "actor_id",
        "actor_transform",
        "actor_velocity",
        "expected_decision",
        "gold_candidate_index",
        "gold_decision",
        "gold_intended_referent",
        "simulator_bbox_ground_truth",
        "evaluation_label",
    }
)


def canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {str(key): canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [canonical(item) for item in value]
    return value


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_policy_firewall(value: Any) -> None:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        overlap = {str(key).casefold() for key in value}.intersection(FORBIDDEN_POLICY_KEYS)
        if overlap:
            raise ValueError("GROUNDED_POLICY_FORBIDDEN_KEYS:" + ",".join(sorted(overlap)))
        for item in value.values():
            assert_policy_firewall(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            assert_policy_firewall(item)


__all__ = [
    "ANSWER_DELAY_ENV",
    "ANSWER_ENV",
    "CONTROL_ENV",
    "DEVICE_ENV",
    "FEATURE_FLAG",
    "INSTRUCTION_ENV",
    "OUTPUT_ENV",
    "RECEIPT_FILENAME",
    "RUNTIME_VERSION",
    "TRIGGER_FRAME_ENV",
    "VISUALIZATION_ENV",
    "assert_policy_firewall",
    "canonical_sha256",
]
