"""Immutable runtime contracts shared by the integrated v1 components."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .canonical_ontology import ONTOLOGY_SCHEMA_VERSION, canonical_value


RUNTIME_SCHEMA_VERSION = "driveclarify.integrated_runtime.v1"


@dataclass(frozen=True)
class ContractFailure:
    layer: str
    failure_type: str
    reason_codes: tuple[str, ...]
    message: str
    recoverable_for_diagnostic: bool = False
    schema_version: str = RUNTIME_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


@dataclass(frozen=True)
class DiagnosticDecisionEligibility:
    eligible: bool
    status: str
    physical_control_verified: bool
    reason_codes: tuple[str, ...]
    schema_version: str = RUNTIME_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


@dataclass(frozen=True)
class ControlAuthorizationEligibility:
    authorization_eligible: bool = False
    control_authorized: bool = False
    used_for_control: bool = False
    vehicle_control_generated: bool = False
    steering: None = None
    throttle: None = None
    brake: None = None
    reason_codes: tuple[str, ...] = ("OFFLINE_V1_CONTROL_CEILING", "PHYSICAL_CONTROL_NOT_VERIFIED")
    schema_version: str = RUNTIME_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


@dataclass(frozen=True)
class QuestionProposalV1:
    proposal_status: str
    query_id: str | None
    target_slot: str | None
    question_text: str | None
    candidate_partition: tuple[tuple[str, tuple[str, ...]], ...]
    option_descriptions: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = RUNTIME_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


@dataclass(frozen=True)
class AnswerResolutionV1:
    resolution_status: str
    selected_candidate_id: str | None
    normalized_answer: str
    requires_latest_observation_replan: bool = True
    cached_pre_answer_plan_reusable: bool = False
    reason_codes: tuple[str, ...] = ()
    schema_version: str = RUNTIME_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


def finite_number(value: Any, reason_code: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(reason_code)
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(reason_code)
    if maximum is not None and result > maximum:
        raise ValueError(reason_code)
    return result


def candidate_ids_from_record(record: Mapping[str, Any]) -> tuple[str, ...]:
    matrix = record.get("counterfactual_runtime_inputs")
    if isinstance(matrix, Mapping) and isinstance(matrix.get("candidate_ids"), Sequence):
        values = tuple(str(item) for item in matrix["candidate_ids"])
    else:
        bindings = record.get("candidate_specific_task_bindings", ())
        values = tuple(str(item.get("candidate_id")) for item in bindings if isinstance(item, Mapping))
    if not values or len(values) != len(set(values)) or any(not item for item in values):
        raise ValueError("CANDIDATE_SET_INVALID")
    return values


def structured_fallback(failures: Sequence[ContractFailure], *, gate: DiagnosticDecisionEligibility | None = None) -> dict[str, Any]:
    reasons = sorted({code for item in failures for code in item.reason_codes})
    if not reasons:
        reasons = ["STRUCTURED_CONTRACT_FAILURE"]
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "decision": "FALLBACK_RECOMMENDED",
        "selected_candidate_id": None,
        "act_target_type": "NONE",
        "equivalence_class_candidate_ids": [],
        "query_id": None,
        "wait_mode": "NOT_AVAILABLE",
        "query_value": None,
        "wait_value": None,
        "decision_confidence_status": "STRUCTURED_CONTRACT_FAILURE",
        "reason_trace": ["STRUCTURED_CONTRACT_FAILURE", *reasons, "FALLBACK_IS_NOT_EMERGENCY_STOP"],
        "diagnostic_gate": None if gate is None else gate.to_dict(),
        "authorization_eligible": False,
        "control_authorized": False,
        "used_for_control": False,
        "vehicle_control_generated": False,
        "steering": None,
        "throttle": None,
        "brake": None,
    }

