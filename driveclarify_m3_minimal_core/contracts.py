"""Immutable contracts for the frozen M3 minimal executable core."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from collections.abc import Iterator
from typing import Any, Mapping, Optional, Tuple

from .serialization import (
    PublicInputError, canonical_sha256, to_json_compatible,
    validate_finite_json_numbers,
)


class LifecycleState(str, Enum):
    IDLE = "IDLE"
    DECISION_READY = "DECISION_READY"
    QUERY_ACTIVE = "QUERY_ACTIVE"
    REVALIDATING = "REVALIDATING"
    REPLANNING = "REPLANNING"
    RESUME_READY = "RESUME_READY"
    FALLBACK = "FALLBACK"
    TERMINATED = "TERMINATED"


class EventType(str, Enum):
    CANDIDATES_READY = "CANDIDATES_READY"
    DECISION_ACT = "DECISION_ACT"
    DECISION_ASK = "DECISION_ASK"
    DECISION_WAIT = "DECISION_WAIT"
    DECISION_FALLBACK = "DECISION_FALLBACK"
    ANSWER_ARRIVED = "ANSWER_ARRIVED"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    QUERY_CANCELLED = "QUERY_CANCELLED"
    WORLD_STATE_CHANGED = "WORLD_STATE_CHANGED"
    HOLDING_CAPABILITY_LOST = "HOLDING_CAPABILITY_LOST"
    SAFETY_PREEMPTED = "SAFETY_PREEMPTED"
    REVALIDATION_PASSED = "REVALIDATION_PASSED"
    REVALIDATION_FAILED = "REVALIDATION_FAILED"
    REPLAN_COMPLETE = "REPLAN_COMPLETE"
    CANDIDATE_STALE = "CANDIDATE_STALE"
    LEASE_EXPIRY_CHECK = "LEASE_EXPIRY_CHECK"


class ControlAuthority(str, Enum):
    BASELINE_CONTROL = "BASELINE_CONTROL"
    M3_HOLDING_CONTROL = "M3_HOLDING_CONTROL"
    INDEPENDENT_SAFETY_GUARD = "INDEPENDENT_SAFETY_GUARD"
    NO_M3_CONTROL_AUTHORITY = "NO_M3_CONTROL_AUTHORITY"


class EvidenceGrade(str, Enum):
    VERIFIED_FROM_CONTROLLED_PROBE = "VERIFIED_FROM_CONTROLLED_PROBE"
    SUPPORTED_BUT_INCOMPLETE = "SUPPORTED_BUT_INCOMPLETE"
    UNRESOLVED_REQUIRES_ADDITIONAL_PROBE = "UNRESOLVED_REQUIRES_ADDITIONAL_PROBE"
    INVALIDATED_BY_CONTROLLED_PROBE = "INVALIDATED_BY_CONTROLLED_PROBE"
    NOT_CURRENTLY_AVAILABLE = "NOT_CURRENTLY_AVAILABLE"


class ProcessingResult(str, Enum):
    PROCESSED = "PROCESSED"
    REJECTED_DUPLICATE = "REJECTED_DUPLICATE"
    REJECTED_INELIGIBLE = "REJECTED_INELIGIBLE"
    REJECTED_NO_UNIQUE_TRANSITION = "REJECTED_NO_UNIQUE_TRANSITION"
    REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN = "REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN"
    REJECTED_MALFORMED_EVENT = "REJECTED_MALFORMED_EVENT"
    REJECTED_INVALID_PAYLOAD_TYPE = "REJECTED_INVALID_PAYLOAD_TYPE"
    REJECTED_INVALID_ENUM = "REJECTED_INVALID_ENUM"
    REJECTED_QUERY_MISMATCH = "REJECTED_QUERY_MISMATCH"
    REJECTED_INVARIANT = "REJECTED_INVARIANT"


class AuditReason(str, Enum):
    CANDIDATES_ACCEPTED = "CANDIDATES_ACCEPTED"
    ACT_CONTRACT_ACCEPTED = "ACT_CONTRACT_ACCEPTED"
    ACT_FAIL_CLOSED = "ACT_FAIL_CLOSED"
    QUERY_CREATED = "QUERY_CREATED"
    SECOND_QUERY_REJECTED = "SECOND_QUERY_REJECTED"
    ABSTRACT_WAIT_GRANTED = "ABSTRACT_WAIT_GRANTED"
    WAIT_FAIL_CLOSED = "WAIT_FAIL_CLOSED"
    FALLBACK_ENTERED = "FALLBACK_ENTERED"
    ANSWER_REQUIRES_REVALIDATION = "ANSWER_REQUIRES_REVALIDATION"
    ANSWER_RECORDED_AFTER_WORLD_CHANGE = "ANSWER_RECORDED_AFTER_WORLD_CHANGE"
    QUERY_TIMED_OUT = "QUERY_TIMED_OUT"
    QUERY_CANCELLED = "QUERY_CANCELLED"
    WORLD_CHANGE_DOMINATES = "WORLD_CHANGE_DOMINATES"
    CACHE_INVALIDATED = "CACHE_INVALIDATED"
    STALE_REVALIDATION_REQUIRED = "STALE_REVALIDATION_REQUIRED"
    STALE_INTERRUPTS_REPLAN = "STALE_INTERRUPTS_REPLAN"
    REVALIDATION_PASSED_REPLAN_REQUIRED = "REVALIDATION_PASSED_REPLAN_REQUIRED"
    REVALIDATION_FAILED_CLOSED = "REVALIDATION_FAILED_CLOSED"
    FRESH_REPLAN_READY = "FRESH_REPLAN_READY"
    STALE_REPLAN_BLOCKED = "STALE_REPLAN_BLOCKED"
    CAPABILITY_LOSS_REVOKED = "CAPABILITY_LOSS_REVOKED"
    LEASE_EXPIRED_REVOKED = "LEASE_EXPIRED_REVOKED"
    TERMINATED_FAIL_CLOSED = "TERMINATED_FAIL_CLOSED"
    SAFETY_PREEMPTED = "SAFETY_PREEMPTED"
    DUPLICATE = "DUPLICATE"
    MISSING_EVENT_FIELD = "MISSING_EVENT_FIELD"
    UNKNOWN_EVENT_TYPE = "UNKNOWN_EVENT_TYPE"
    PAYLOAD_DIGEST_MISMATCH = "PAYLOAD_DIGEST_MISMATCH"
    IDEMPOTENCY_KEY_MISMATCH = "IDEMPOTENCY_KEY_MISMATCH"
    REJECTED_INVALID_JSON_NUMERIC_DOMAIN = "REJECTED_INVALID_JSON_NUMERIC_DOMAIN"
    OUT_OF_ORDER_MONOTONIC_TIME = "OUT_OF_ORDER_MONOTONIC_TIME"
    WRONG_OR_INACTIVE_QUERY_EPISODE = "WRONG_OR_INACTIVE_QUERY_EPISODE"
    LATE_ANSWER = "LATE_ANSWER"
    TIMEOUT_NOT_STRICTLY_AFTER_DEADLINE = "TIMEOUT_NOT_STRICTLY_AFTER_DEADLINE"
    NO_TRANSITION = "NO_TRANSITION"
    NONDETERMINISTIC_TRANSITION = "NONDETERMINISTIC_TRANSITION"
    INVALID_NONFINITE_MONOTONIC_TIME = "INVALID_NONFINITE_MONOTONIC_TIME"
    INVALID_TEMPORAL_TYPE = "INVALID_TEMPORAL_TYPE"
    INVALID_TEMPORAL_ORDER = "INVALID_TEMPORAL_ORDER"
    MALFORMED_EVENT = "MALFORMED_EVENT"
    INVALID_PAYLOAD_TYPE = "INVALID_PAYLOAD_TYPE"
    INVALID_ENUM = "INVALID_ENUM"
    INVARIANT_REJECTED = "INVARIANT_REJECTED"


def _input_error(processing: ProcessingResult, reason: AuditReason,
                 detail: str) -> PublicInputError:
    return PublicInputError(processing.value, reason.value, detail)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        processing = (ProcessingResult.REJECTED_INVALID_PAYLOAD_TYPE
                      if label == "payload" else ProcessingResult.REJECTED_MALFORMED_EVENT)
        reason = (AuditReason.INVALID_PAYLOAD_TYPE
                  if label == "payload" else AuditReason.MALFORMED_EVENT)
        raise _input_error(processing, reason, label + " must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                           AuditReason.MALFORMED_EVENT,
                           label + " keys must be strings")
    return value


def _required(value: Mapping[str, Any], names: tuple[str, ...], label: str) -> None:
    missing = [name for name in names if name not in value]
    if missing:
        raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                           AuditReason.MISSING_EVENT_FIELD,
                           label + " missing required fields: " + ",".join(missing))


def _exact(value: Any, expected: type, label: str) -> None:
    if type(value) is not expected:
        raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                           AuditReason.MALFORMED_EVENT,
                           label + " has invalid type")


def _optional_string(value: Any, label: str) -> None:
    if value is not None and type(value) is not str:
        raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                           AuditReason.MALFORMED_EVENT,
                           label + " must be string or null")


def _finite_time(value: Any, label: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _input_error(ProcessingResult.REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN,
                           AuditReason.INVALID_TEMPORAL_TYPE,
                           label + " must be a finite real")
    if isinstance(value, float) and not math.isfinite(value):
        raise _input_error(ProcessingResult.REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN,
                           AuditReason.INVALID_NONFINITE_MONOTONIC_TIME,
                           label + " must be finite")


def _enum(value: Any, enum_type: type[Enum], label: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise _input_error(ProcessingResult.REJECTED_INVALID_ENUM,
                           AuditReason.INVALID_ENUM, label + " has invalid enum type")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _input_error(ProcessingResult.REJECTED_INVALID_ENUM,
                           AuditReason.INVALID_ENUM, label + " has invalid enum value") from exc


def _validate_event_payload_semantics(payload: Mapping[str, Any]) -> None:
    for name in ("answer_present", "physical_mode_ready",
                 "reason_code_authorizes_control", "model_forward_requested",
                 "low_level_control_requested"):
        if name in payload:
            _exact(payload[name], bool, "payload." + name)
    if "baseline_authority_eligible" in payload and payload["baseline_authority_eligible"] is not None:
        _exact(payload["baseline_authority_eligible"], bool,
               "payload.baseline_authority_eligible")
    if "candidate_set_id" in payload:
        _optional_string(payload["candidate_set_id"], "payload.candidate_set_id")
    if ("candidate_freshness" in payload and
            payload["candidate_freshness"] not in {"FRESH", "STALE", "UNKNOWN"}):
        raise _input_error(ProcessingResult.REJECTED_INVALID_ENUM,
                           AuditReason.INVALID_ENUM, "invalid payload candidate_freshness")
    for name in ("act_evidence_grade", "holding_evidence_grade"):
        if name in payload:
            _enum(payload[name], EvidenceGrade, "payload." + name)
    for name in ("answer_deadline_monotonic", "decision_deadline_monotonic"):
        if name in payload:
            _finite_time(payload[name], "payload." + name)
    if "lease" in payload and payload["lease"] is not None:
        if isinstance(payload["lease"], HoldingLease):
            HoldingLease.from_dict(payload["lease"].to_dict())
        else:
            HoldingLease.from_dict(payload["lease"])


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _audit_reason(value: str | AuditReason) -> AuditReason:
    if isinstance(value, AuditReason):
        return value
    return _enum(value, AuditReason, "audit.result")


@dataclass(frozen=True)
class HoldingLease:
    lease_id: str
    issued_monotonic_time: Any
    next_reevaluation_monotonic_time: Any
    expires_monotonic_time: Any
    maximum_expiry_monotonic_time: Any
    evidence_grade: EvidenceGrade
    source_observation_id: str
    source_frame_id: str
    candidate_set_id: str
    revoked: bool
    revocation_reason: Optional[str]
    authority_on_exit: ControlAuthority

    def __post_init__(self) -> None:
        for name in ("lease_id", "source_observation_id", "source_frame_id",
                     "candidate_set_id"):
            _exact(getattr(self, name), str, "holding_lease." + name)
        for name in ("issued_monotonic_time", "next_reevaluation_monotonic_time",
                     "expires_monotonic_time", "maximum_expiry_monotonic_time"):
            _finite_time(getattr(self, name), "holding_lease." + name)
        if not (self.issued_monotonic_time < self.next_reevaluation_monotonic_time <=
                self.expires_monotonic_time <= self.maximum_expiry_monotonic_time):
            raise _input_error(ProcessingResult.REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN,
                               AuditReason.INVALID_TEMPORAL_ORDER,
                               "holding lease temporal order is invalid")
        if not isinstance(self.evidence_grade, EvidenceGrade):
            raise _input_error(ProcessingResult.REJECTED_INVALID_ENUM,
                               AuditReason.INVALID_ENUM, "invalid evidence_grade")
        _exact(self.revoked, bool, "holding_lease.revoked")
        _optional_string(self.revocation_reason, "holding_lease.revocation_reason")
        if not isinstance(self.authority_on_exit, ControlAuthority):
            raise _input_error(ProcessingResult.REJECTED_INVALID_ENUM,
                               AuditReason.INVALID_ENUM, "invalid authority_on_exit")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HoldingLease":
        value = _mapping(value, "holding_lease")
        _required(value, (
            "lease_id", "issued_monotonic_time", "next_reevaluation_monotonic_time",
            "expires_monotonic_time", "maximum_expiry_monotonic_time",
            "evidence_grade", "source_observation_id", "source_frame_id",
            "candidate_set_id", "revoked", "revocation_reason", "authority_on_exit",
        ), "holding_lease")
        return cls(
            lease_id=value["lease_id"],
            issued_monotonic_time=value["issued_monotonic_time"],
            next_reevaluation_monotonic_time=value["next_reevaluation_monotonic_time"],
            expires_monotonic_time=value["expires_monotonic_time"],
            maximum_expiry_monotonic_time=value["maximum_expiry_monotonic_time"],
            evidence_grade=_enum(value["evidence_grade"], EvidenceGrade,
                                 "holding_lease.evidence_grade"),
            source_observation_id=value["source_observation_id"],
            source_frame_id=value["source_frame_id"],
            candidate_set_id=value["candidate_set_id"],
            revoked=value["revoked"],
            revocation_reason=value["revocation_reason"],
            authority_on_exit=_enum(value["authority_on_exit"], ControlAuthority,
                                    "holding_lease.authority_on_exit"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {name: to_json_compatible(getattr(self, name)) for name in (
            "lease_id", "issued_monotonic_time", "next_reevaluation_monotonic_time",
            "expires_monotonic_time", "maximum_expiry_monotonic_time",
            "evidence_grade", "source_observation_id", "source_frame_id",
            "candidate_set_id", "revoked", "revocation_reason", "authority_on_exit",
        )}


@dataclass(frozen=True)
class AuditRecord:
    sequence: int
    event_id: Optional[str]
    event_type: Optional[str]
    idempotency_key: Optional[str]
    result: AuditReason
    transition_id: Optional[str]
    mutation_applied: bool
    concurrent_group_id: Optional[str]
    concurrent_disposition: str

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT, "invalid audit sequence")
        for name in ("event_id", "event_type", "idempotency_key",
                     "transition_id", "concurrent_group_id"):
            _optional_string(getattr(self, name), "audit." + name)
        if not isinstance(self.result, AuditReason):
            raise _input_error(ProcessingResult.REJECTED_INVALID_ENUM,
                               AuditReason.INVALID_ENUM, "invalid audit result")
        _exact(self.mutation_applied, bool, "audit.mutation_applied")
        _exact(self.concurrent_disposition, str, "audit.concurrent_disposition")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AuditRecord":
        value = _mapping(value, "audit_record")
        _required(value, (
            "sequence", "event_id", "event_type", "idempotency_key", "result",
            "transition_id", "mutation_applied", "concurrent_group_id",
            "concurrent_disposition",
        ), "audit_record")
        return cls(
            sequence=value["sequence"], event_id=value.get("event_id"),
            event_type=value.get("event_type"), idempotency_key=value.get("idempotency_key"),
            result=_audit_reason(value["result"]), transition_id=value.get("transition_id"),
            mutation_applied=value["mutation_applied"],
            concurrent_group_id=value.get("concurrent_group_id"),
            concurrent_disposition=value["concurrent_disposition"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence, "event_id": self.event_id,
            "event_type": self.event_type, "idempotency_key": self.idempotency_key,
            "result": self.result.value, "transition_id": self.transition_id,
            "mutation_applied": self.mutation_applied,
            "concurrent_group_id": self.concurrent_group_id,
            "concurrent_disposition": self.concurrent_disposition,
        }


@dataclass(frozen=True)
class MinimalM3State:
    lifecycle_state: LifecycleState = LifecycleState.IDLE
    query_episode_id: Optional[str] = None
    query_active: bool = False
    candidate_set_id: Optional[str] = None
    candidate_freshness: str = "UNKNOWN"
    answer_deadline_monotonic: Any = None
    decision_deadline_monotonic: Any = None
    authority: ControlAuthority = ControlAuthority.NO_M3_CONTROL_AUTHORITY
    safety_guard_active: bool = False
    baseline_authority_eligible: bool = True
    holding_lease: Optional[HoldingLease] = None
    revalidation_required: bool = False
    replan_required: bool = False
    last_event_sequence: int = 0
    audit_log_digest: str = field(default_factory=lambda: canonical_sha256([]))
    processed_idempotency_keys: Tuple[str, ...] = ()
    audit_log: Tuple[AuditRecord, ...] = ()
    current_time_monotonic: Any = 0.0
    model_forward_count: int = 0
    low_level_control_outputs: Tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.lifecycle_state, LifecycleState):
            raise _input_error(ProcessingResult.REJECTED_INVALID_ENUM,
                               AuditReason.INVALID_ENUM, "invalid lifecycle_state")
        if not isinstance(self.authority, ControlAuthority):
            raise _input_error(ProcessingResult.REJECTED_INVALID_ENUM,
                               AuditReason.INVALID_ENUM, "invalid authority")
        for name in ("query_episode_id", "candidate_set_id"):
            _optional_string(getattr(self, name), "state." + name)
        for name in ("query_active", "safety_guard_active",
                     "baseline_authority_eligible", "revalidation_required",
                     "replan_required"):
            _exact(getattr(self, name), bool, "state." + name)
        if self.candidate_freshness not in {"FRESH", "STALE", "UNKNOWN"}:
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT,
                               "invalid candidate_freshness")
        _finite_time(self.answer_deadline_monotonic,
                     "state.answer_deadline_monotonic", optional=True)
        _finite_time(self.decision_deadline_monotonic,
                     "state.decision_deadline_monotonic", optional=True)
        _finite_time(self.current_time_monotonic, "state.current_time_monotonic")
        if self.holding_lease is not None and not isinstance(self.holding_lease, HoldingLease):
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT, "invalid holding_lease")
        if type(self.last_event_sequence) is not int or self.last_event_sequence < 0:
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT, "invalid last_event_sequence")
        _exact(self.audit_log_digest, str, "state.audit_log_digest")
        if (not isinstance(self.processed_idempotency_keys, tuple) or
                any(type(item) is not str for item in self.processed_idempotency_keys)):
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT,
                               "invalid processed_idempotency_keys")
        if (not isinstance(self.audit_log, tuple) or
                any(not isinstance(item, AuditRecord) for item in self.audit_log)):
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT, "invalid audit_log")
        if type(self.model_forward_count) is not int or self.model_forward_count < 0:
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT, "invalid model_forward_count")
        if not isinstance(self.low_level_control_outputs, tuple):
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT,
                               "invalid low_level_control_outputs")
        validate_finite_json_numbers(self.low_level_control_outputs)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MinimalM3State":
        value = _mapping(value, "state")
        _required(value, (
            "lifecycle_state", "query_episode_id", "query_active", "candidate_set_id",
            "candidate_freshness", "answer_deadline_monotonic",
            "decision_deadline_monotonic", "authority", "safety_guard_active",
            "baseline_authority_eligible", "holding_lease", "revalidation_required",
            "replan_required", "last_event_sequence", "audit_log_digest",
            "processed_idempotency_keys", "audit_log", "current_time_monotonic",
            "model_forward_count", "low_level_control_outputs",
        ), "state")
        lease = value.get("holding_lease")
        if not isinstance(value["processed_idempotency_keys"], (list, tuple)):
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT,
                               "processed_idempotency_keys must be a sequence")
        if not isinstance(value["audit_log"], (list, tuple)):
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT,
                               "audit_log must be a sequence")
        if not isinstance(value["low_level_control_outputs"], (list, tuple)):
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.MALFORMED_EVENT,
                               "low_level_control_outputs must be a sequence")
        return cls(
            lifecycle_state=_enum(value["lifecycle_state"], LifecycleState,
                                  "state.lifecycle_state"),
            query_episode_id=value.get("query_episode_id"), query_active=value["query_active"],
            candidate_set_id=value.get("candidate_set_id"),
            candidate_freshness=value["candidate_freshness"],
            answer_deadline_monotonic=value.get("answer_deadline_monotonic"),
            decision_deadline_monotonic=value.get("decision_deadline_monotonic"),
            authority=_enum(value["authority"], ControlAuthority, "state.authority"),
            safety_guard_active=value["safety_guard_active"],
            baseline_authority_eligible=value["baseline_authority_eligible"],
            holding_lease=None if lease is None else HoldingLease.from_dict(lease),
            revalidation_required=value["revalidation_required"],
            replan_required=value["replan_required"],
            last_event_sequence=value["last_event_sequence"],
            audit_log_digest=value["audit_log_digest"],
            processed_idempotency_keys=tuple(value["processed_idempotency_keys"]),
            audit_log=tuple(AuditRecord.from_dict(item) for item in value["audit_log"]),
            current_time_monotonic=value["current_time_monotonic"],
            model_forward_count=value["model_forward_count"],
            low_level_control_outputs=tuple(_freeze_json(item) for item in value["low_level_control_outputs"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lifecycle_state": self.lifecycle_state.value,
            "query_episode_id": self.query_episode_id, "query_active": self.query_active,
            "candidate_set_id": self.candidate_set_id,
            "candidate_freshness": self.candidate_freshness,
            "answer_deadline_monotonic": self.answer_deadline_monotonic,
            "decision_deadline_monotonic": self.decision_deadline_monotonic,
            "authority": self.authority.value,
            "safety_guard_active": self.safety_guard_active,
            "baseline_authority_eligible": self.baseline_authority_eligible,
            "holding_lease": None if self.holding_lease is None else self.holding_lease.to_dict(),
            "revalidation_required": self.revalidation_required,
            "replan_required": self.replan_required,
            "last_event_sequence": self.last_event_sequence,
            "audit_log_digest": self.audit_log_digest,
            "processed_idempotency_keys": list(self.processed_idempotency_keys),
            "audit_log": [item.to_dict() for item in self.audit_log],
            "current_time_monotonic": self.current_time_monotonic,
            "model_forward_count": self.model_forward_count,
            "low_level_control_outputs": to_json_compatible(self.low_level_control_outputs),
        }


@dataclass(frozen=True)
class MinimalM3Event:
    event_id: str
    event_type: EventType
    query_episode_id: Optional[str]
    source_component: str
    observed_monotonic_time: Any
    source_simulation_time: Any
    calendar_utc: Optional[str]
    payload: Mapping[str, Any]
    payload_digest: str
    idempotency_key: str
    concurrent_group_id: Optional[str]

    def __post_init__(self) -> None:
        _exact(self.event_id, str, "event.event_id")
        if not isinstance(self.event_type, EventType):
            raise _input_error(ProcessingResult.REJECTED_INVALID_ENUM,
                               AuditReason.UNKNOWN_EVENT_TYPE, "invalid event_type")
        _optional_string(self.query_episode_id, "event.query_episode_id")
        _exact(self.source_component, str, "event.source_component")
        _finite_time(self.observed_monotonic_time,
                     "event.observed_monotonic_time")
        _finite_time(self.source_simulation_time,
                     "event.source_simulation_time", optional=True)
        _optional_string(self.calendar_utc, "event.calendar_utc")
        payload = _mapping(self.payload, "payload")
        validate_finite_json_numbers(payload)
        _validate_event_payload_semantics(payload)
        _exact(self.payload_digest, str, "event.payload_digest")
        _exact(self.idempotency_key, str, "event.idempotency_key")
        _optional_string(self.concurrent_group_id, "event.concurrent_group_id")
        actual_digest = canonical_sha256(payload)
        if actual_digest != self.payload_digest:
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.PAYLOAD_DIGEST_MISMATCH,
                               "payload digest mismatch")
        expected_key = canonical_sha256([
            self.query_episode_id, self.source_component, self.event_id, actual_digest])
        if expected_key != self.idempotency_key:
            raise _input_error(ProcessingResult.REJECTED_MALFORMED_EVENT,
                               AuditReason.IDEMPOTENCY_KEY_MISMATCH,
                               "idempotency key mismatch")
        object.__setattr__(self, "payload", _freeze_json(self.payload))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MinimalM3Event":
        value = _mapping(value, "event")
        _required(value, (
            "event_id", "event_type", "query_episode_id", "source_component",
            "observed_monotonic_time", "source_simulation_time", "calendar_utc",
            "payload", "payload_digest", "idempotency_key", "concurrent_group_id",
        ), "event")
        return cls(
            event_id=value["event_id"],
            event_type=_enum(value["event_type"], EventType, "event.event_type"),
            query_episode_id=value.get("query_episode_id"),
            source_component=value["source_component"],
            observed_monotonic_time=value["observed_monotonic_time"],
            source_simulation_time=value.get("source_simulation_time"),
            calendar_utc=value.get("calendar_utc"), payload=value["payload"],
            payload_digest=value["payload_digest"], idempotency_key=value["idempotency_key"],
            concurrent_group_id=value.get("concurrent_group_id"),
        )

    @classmethod
    def create(cls, *, event_id: str, event_type: EventType,
               query_episode_id: Optional[str], source_component: str,
               observed_monotonic_time: Any, source_simulation_time: Any = None,
               calendar_utc: Optional[str] = None, payload: Mapping[str, Any],
               concurrent_group_id: Optional[str] = None) -> "MinimalM3Event":
        payload = _mapping(payload, "payload")
        validate_finite_json_numbers(payload)
        payload_digest = canonical_sha256(payload)
        key = canonical_sha256([query_episode_id, source_component, event_id, payload_digest])
        return cls(event_id, event_type, query_episode_id, source_component,
                   observed_monotonic_time, source_simulation_time, calendar_utc,
                   payload, payload_digest, key, concurrent_group_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "event_type": self.event_type.value,
            "query_episode_id": self.query_episode_id,
            "source_component": self.source_component,
            "observed_monotonic_time": self.observed_monotonic_time,
            "source_simulation_time": to_json_compatible(self.source_simulation_time),
            "calendar_utc": self.calendar_utc, "payload": to_json_compatible(self.payload),
            "payload_digest": self.payload_digest, "idempotency_key": self.idempotency_key,
            "concurrent_group_id": self.concurrent_group_id,
        }


@dataclass(frozen=True)
class TraceRecord(Mapping[str, Any]):
    audit: AuditRecord
    transition_id: Optional[str]
    mutation_applied: bool
    processing_result: str
    invariants: Tuple[str, ...]
    business_state_unchanged: bool
    reducer_pipeline: Tuple[str, ...]
    concurrent_loser: bool

    def __post_init__(self) -> None:
        if not isinstance(self.audit, AuditRecord):
            raise TypeError("trace audit must be AuditRecord")
        _optional_string(self.transition_id, "trace.transition_id")
        _exact(self.mutation_applied, bool, "trace.mutation_applied")
        _exact(self.processing_result, str, "trace.processing_result")
        if not isinstance(self.invariants, tuple) or any(
                type(item) is not str for item in self.invariants):
            raise TypeError("trace invariants must be tuple[str, ...]")
        _exact(self.business_state_unchanged, bool,
               "trace.business_state_unchanged")
        if not isinstance(self.reducer_pipeline, tuple) or any(
                type(item) is not str for item in self.reducer_pipeline):
            raise TypeError("trace reducer_pipeline must be tuple[str, ...]")
        _exact(self.concurrent_loser, bool, "trace.concurrent_loser")

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit": self.audit.to_dict(), "transition_id": self.transition_id,
            "mutation_applied": self.mutation_applied,
            "processing_result": self.processing_result,
            "invariants": list(self.invariants),
            "business_state_unchanged": self.business_state_unchanged,
            "reducer_pipeline": list(self.reducer_pipeline),
            "concurrent_loser": self.concurrent_loser,
        }

    def __getitem__(self, key: str) -> Any:
        value = self.to_dict()[key]
        return _freeze_json(value)

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return 8


@dataclass(frozen=True)
class ReductionResult:
    state: Any
    audit: Tuple[AuditRecord, ...]
    transition_ids: Tuple[str, ...]
    processing_results: Tuple[ProcessingResult, ...]
    traces: Tuple[TraceRecord, ...]
    low_level_control_outputs: Tuple[Any, ...] = ()
    production_runtime: bool = False
    runtime_integration: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.audit, tuple) or any(
                not isinstance(item, AuditRecord) for item in self.audit):
            raise TypeError("result audit must be tuple[AuditRecord, ...]")
        if not isinstance(self.transition_ids, tuple) or any(
                type(item) is not str for item in self.transition_ids):
            raise TypeError("result transition_ids must be tuple[str, ...]")
        if not isinstance(self.processing_results, tuple) or any(
                not isinstance(item, ProcessingResult) for item in self.processing_results):
            raise TypeError("result processing_results must be tuple[ProcessingResult, ...]")
        if not isinstance(self.traces, tuple) or any(
                not isinstance(item, TraceRecord) for item in self.traces):
            raise TypeError("result traces must be tuple[TraceRecord, ...]")
        if not isinstance(self.low_level_control_outputs, tuple):
            raise TypeError("result control outputs must be tuple")
        validate_finite_json_numbers(self.low_level_control_outputs)
        _exact(self.production_runtime, bool, "result.production_runtime")
        _exact(self.runtime_integration, bool, "result.runtime_integration")

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_state": (self.state.to_dict()
                            if isinstance(self.state, MinimalM3State)
                            else to_json_compatible(self.state)),
            "audit": [item.to_dict() for item in self.audit],
            "transition_ids": list(self.transition_ids),
            "processing_results": [item.value for item in self.processing_results],
            "trace": to_json_compatible(self.traces),
            "control_output": to_json_compatible(self.low_level_control_outputs),
            "production_runtime": self.production_runtime,
            "runtime_integration": self.runtime_integration,
        }
