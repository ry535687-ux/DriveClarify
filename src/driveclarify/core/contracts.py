"""core.contracts implementation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class ContractError(ValueError):
    pass


class TaskRelation(str, Enum):
    EQUIVALENT = "TASK_EQUIVALENT"
    DIVERGENT = "TASK_DIVERGENT"
    UNKNOWN = "UNKNOWN"


class AskDecision(str, Enum):
    ASK = "ASK_RECOMMENDED"
    NO_QUERY = "NO_QUERY_NEEDED"
    UNRESOLVED = "UNRESOLVED"


FORBIDDEN_METHOD_KEYS = frozenset({
    "answer", "correct_binding", "expected_decision", "gold", "gold_candidate_index",
    "ground_truth", "high_low", "label", "relation_label", "task_relation_truth",
    "task_signature", "task_signatures", "true_intent", "true_relation",
    "privileged_task_signature", "future_trajectory_truth", "carla_object_location_truth",
})
FORBIDDEN_VALUE_TOKENS = frozenset({"HIGH", "LOW", "TASK_EQUIVALENT", "TASK_DIVERGENT"})


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def assert_label_firewall(value: Any, path: str = "$") -> None:
    """拒绝方法输入中的答案字段或答案化字符串。"""
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold()
            if normalized in FORBIDDEN_METHOD_KEYS:
                raise ContractError(f"LABEL_FIREWALL_FORBIDDEN_KEY:{path}.{key}")
            assert_label_firewall(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_label_firewall(child, f"{path}[{index}]")
    elif isinstance(value, str) and value.upper() in FORBIDDEN_VALUE_TOKENS:
        raise ContractError(f"LABEL_FIREWALL_FORBIDDEN_VALUE:{path}")


def require_finite_number(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ContractError(f"NONFINITE:{name}")
    return number


@dataclass(frozen=True)
class MethodResult:
    method: str
    sample_id: str
    relation: TaskRelation
    ask: AskDecision
    reason_codes: tuple[str, ...]
    evidence: Mapping[str, Any]
    inference_seconds: float = 0.0
    candidate_forward_count: int = 0
    grounding_forward_count: int = 0

    def __post_init__(self) -> None:
        require_finite_number(self.inference_seconds, "inference_seconds")
        if self.inference_seconds < 0 or self.candidate_forward_count < 0 or self.grounding_forward_count < 0:
            raise ContractError("NEGATIVE_COST")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["relation"] = self.relation.value
        value["ask"] = self.ask.value
        value["reason_codes"] = list(self.reason_codes)
        return value


def validate_method_input(value: Mapping[str, Any]) -> None:
    assert_label_firewall(value)
    required = {"sample_id", "observation", "candidates", "runtime_context"}
    missing = required - set(value)
    if missing:
        raise ContractError("METHOD_INPUT_MISSING:" + ",".join(sorted(missing)))
    candidates = value["candidates"]
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)) or len(candidates) != 2:
        raise ContractError("EXACTLY_TWO_CANDIDATES_REQUIRED")
    if len({str(item.get("candidate_id")) for item in candidates}) != 2:
        raise ContractError("CANDIDATE_IDS_NOT_DISTINCT")
