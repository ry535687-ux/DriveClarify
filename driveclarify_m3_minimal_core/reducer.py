"""Pure reducer for the frozen finite-domain M3 minimal-core semantics."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .contracts import (
    AuditReason,
    AuditRecord,
    ControlAuthority,
    EventType,
    EvidenceGrade,
    HoldingLease,
    LifecycleState,
    MinimalM3Event,
    MinimalM3State,
    ProcessingResult,
    ReductionResult,
    TraceRecord,
)
from .serialization import (
    PublicInputError, canonical_sha256, to_json_compatible,
    validate_finite_json_numbers,
)


CORE_ID = "DC-M3-MINCORE-FINITE-R1-20260805T055659Z"
VERIFIED = EvidenceGrade.VERIFIED_FROM_CONTROLLED_PROBE
_AUDIT_EXCLUDED = {"audit_log", "audit_log_digest", "last_event_sequence"}
_LEASE_TIME_FIELDS = (
    "issued_monotonic_time", "next_reevaluation_monotonic_time",
    "expires_monotonic_time", "maximum_expiry_monotonic_time",
)
_PRIORITY = {
    EventType.SAFETY_PREEMPTED: 0,
    EventType.QUERY_CANCELLED: 1,
    EventType.HOLDING_CAPABILITY_LOST: 2,
    EventType.WORLD_STATE_CHANGED: 3,
    EventType.CANDIDATE_STALE: 3,
    EventType.ANSWER_ARRIVED: 4,
    EventType.QUERY_TIMEOUT: 5,
}


def is_finite_real(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _temporal_reason(value: Any) -> Optional[AuditReason]:
    if isinstance(value, float) and not math.isfinite(value):
        return AuditReason.INVALID_NONFINITE_MONOTONIC_TIME
    if not is_finite_real(value):
        return AuditReason.INVALID_TEMPORAL_TYPE
    return None


def validate_lease_temporal_domain(lease: Any) -> tuple[bool, AuditReason | None]:
    if isinstance(lease, HoldingLease):
        values = lease.to_dict()
    elif isinstance(lease, Mapping):
        values = lease
    else:
        return False, AuditReason.INVALID_TEMPORAL_TYPE
    if any(name not in values for name in _LEASE_TIME_FIELDS):
        return False, AuditReason.INVALID_TEMPORAL_TYPE
    for name in _LEASE_TIME_FIELDS:
        reason = _temporal_reason(values[name])
        if reason is not None:
            return False, reason
    if not (values["issued_monotonic_time"] <
            values["next_reevaluation_monotonic_time"] <=
            values["expires_monotonic_time"] <=
            values["maximum_expiry_monotonic_time"]):
        return False, AuditReason.INVALID_TEMPORAL_ORDER
    return True, None


def lease_temporally_valid(lease: Any, current_monotonic_time: Any) -> bool:
    ok, _ = validate_lease_temporal_domain(lease)
    if not ok or not is_finite_real(current_monotonic_time):
        return False
    if isinstance(lease, HoldingLease):
        revoked = lease.revoked
        expiry = lease.expires_monotonic_time
        evidence = lease.evidence_grade
    else:
        required = {
            "lease_id", "evidence_grade", "source_observation_id", "source_frame_id",
            "candidate_set_id", "revoked", "revocation_reason", "authority_on_exit",
        }
        if required - set(lease):
            return False
        revoked = lease["revoked"]
        expiry = lease["expires_monotonic_time"]
        try:
            evidence = EvidenceGrade(lease["evidence_grade"])
        except ValueError:
            return False
    return not revoked and current_monotonic_time < expiry and evidence is VERIFIED


def resolve_authority(*, safety_guard_active: bool,
                      baseline_authority_eligible: bool,
                      holding_requested: bool,
                      holding_evidence_eligible: bool,
                      holding_lease: HoldingLease | None,
                      current_monotonic_time: Any,
                      entering_fallback_or_terminated: bool) -> ControlAuthority:
    """Resolve the single authority without reading or mutating external state."""
    if safety_guard_active:
        return ControlAuthority.INDEPENDENT_SAFETY_GUARD
    if entering_fallback_or_terminated:
        return (ControlAuthority.BASELINE_CONTROL if baseline_authority_eligible else
                ControlAuthority.NO_M3_CONTROL_AUTHORITY)
    if (holding_requested and holding_evidence_eligible and
            holding_lease is not None and
            lease_temporally_valid(holding_lease, current_monotonic_time)):
        return ControlAuthority.M3_HOLDING_CONTROL
    return (ControlAuthority.BASELINE_CONTROL if baseline_authority_eligible else
            ControlAuthority.NO_M3_CONTROL_AUTHORITY)


def event_idempotency_key(event: MinimalM3Event) -> str:
    validate_finite_json_numbers(event.payload)
    return canonical_sha256([
        event.query_episode_id, event.source_component,
        event.event_id, event.payload_digest,
    ])


def default_state(**changes: Any) -> MinimalM3State:
    state = MinimalM3State()
    if not changes:
        return state
    return replace(state, **changes)


@dataclass(frozen=True)
class _Transition:
    transition_id: str
    source: Optional[LifecycleState]
    event_type: EventType
    target: LifecycleState
    audit_reason: AuditReason


_TRANSITIONS = (
    _Transition("MC-T001", LifecycleState.IDLE, EventType.CANDIDATES_READY, LifecycleState.DECISION_READY, AuditReason.CANDIDATES_ACCEPTED),
    _Transition("MC-T002", LifecycleState.DECISION_READY, EventType.DECISION_ACT, LifecycleState.RESUME_READY, AuditReason.ACT_CONTRACT_ACCEPTED),
    _Transition("MC-T003", LifecycleState.DECISION_READY, EventType.DECISION_ACT, LifecycleState.FALLBACK, AuditReason.ACT_FAIL_CLOSED),
    _Transition("MC-T004", LifecycleState.DECISION_READY, EventType.DECISION_ASK, LifecycleState.QUERY_ACTIVE, AuditReason.QUERY_CREATED),
    _Transition("MC-T005", LifecycleState.QUERY_ACTIVE, EventType.DECISION_ASK, LifecycleState.QUERY_ACTIVE, AuditReason.SECOND_QUERY_REJECTED),
    _Transition("MC-T006", LifecycleState.DECISION_READY, EventType.DECISION_WAIT, LifecycleState.QUERY_ACTIVE, AuditReason.ABSTRACT_WAIT_GRANTED),
    _Transition("MC-T007", LifecycleState.DECISION_READY, EventType.DECISION_WAIT, LifecycleState.FALLBACK, AuditReason.WAIT_FAIL_CLOSED),
    _Transition("MC-T008", LifecycleState.DECISION_READY, EventType.DECISION_FALLBACK, LifecycleState.FALLBACK, AuditReason.FALLBACK_ENTERED),
    _Transition("MC-T009", LifecycleState.QUERY_ACTIVE, EventType.ANSWER_ARRIVED, LifecycleState.REVALIDATING, AuditReason.ANSWER_REQUIRES_REVALIDATION),
    _Transition("MC-T010", LifecycleState.REVALIDATING, EventType.ANSWER_ARRIVED, LifecycleState.REVALIDATING, AuditReason.ANSWER_RECORDED_AFTER_WORLD_CHANGE),
    _Transition("MC-T011", LifecycleState.QUERY_ACTIVE, EventType.QUERY_TIMEOUT, LifecycleState.FALLBACK, AuditReason.QUERY_TIMED_OUT),
    _Transition("MC-T012", LifecycleState.QUERY_ACTIVE, EventType.QUERY_CANCELLED, LifecycleState.FALLBACK, AuditReason.QUERY_CANCELLED),
    _Transition("MC-T013", LifecycleState.QUERY_ACTIVE, EventType.WORLD_STATE_CHANGED, LifecycleState.REVALIDATING, AuditReason.WORLD_CHANGE_DOMINATES),
    _Transition("MC-T014", LifecycleState.DECISION_READY, EventType.WORLD_STATE_CHANGED, LifecycleState.REVALIDATING, AuditReason.CACHE_INVALIDATED),
    _Transition("MC-T015", LifecycleState.DECISION_READY, EventType.CANDIDATE_STALE, LifecycleState.REVALIDATING, AuditReason.STALE_REVALIDATION_REQUIRED),
    _Transition("MC-T016", LifecycleState.REPLANNING, EventType.CANDIDATE_STALE, LifecycleState.REVALIDATING, AuditReason.STALE_INTERRUPTS_REPLAN),
    _Transition("MC-T017", LifecycleState.REVALIDATING, EventType.REVALIDATION_PASSED, LifecycleState.REPLANNING, AuditReason.REVALIDATION_PASSED_REPLAN_REQUIRED),
    _Transition("MC-T018", LifecycleState.REVALIDATING, EventType.REVALIDATION_FAILED, LifecycleState.FALLBACK, AuditReason.REVALIDATION_FAILED_CLOSED),
    _Transition("MC-T019", LifecycleState.REPLANNING, EventType.REPLAN_COMPLETE, LifecycleState.RESUME_READY, AuditReason.FRESH_REPLAN_READY),
    _Transition("MC-T020", LifecycleState.REPLANNING, EventType.REPLAN_COMPLETE, LifecycleState.REVALIDATING, AuditReason.STALE_REPLAN_BLOCKED),
    _Transition("MC-T021", LifecycleState.QUERY_ACTIVE, EventType.HOLDING_CAPABILITY_LOST, LifecycleState.FALLBACK, AuditReason.CAPABILITY_LOSS_REVOKED),
    _Transition("MC-T022", LifecycleState.QUERY_ACTIVE, EventType.LEASE_EXPIRY_CHECK, LifecycleState.FALLBACK, AuditReason.LEASE_EXPIRED_REVOKED),
    _Transition("MC-T023", LifecycleState.FALLBACK, EventType.DECISION_FALLBACK, LifecycleState.TERMINATED, AuditReason.TERMINATED_FAIL_CLOSED),
    _Transition("MC-T024", None, EventType.SAFETY_PREEMPTED, LifecycleState.FALLBACK, AuditReason.SAFETY_PREEMPTED),
)
TRANSITION_IDS = tuple(item.transition_id for item in _TRANSITIONS)


def _payload(event: MinimalM3Event, name: str, default: Any = None) -> Any:
    return event.payload[name] if name in event.payload else default


def _act_valid(state: MinimalM3State, event: MinimalM3Event) -> bool:
    return (state.candidate_freshness == "FRESH" and
            _payload(event, "act_evidence_grade") == VERIFIED.value and
            not state.revalidation_required and not state.replan_required)


def _wait_valid(state: MinimalM3State, event: MinimalM3Event,
                current_monotonic_time: Any) -> bool:
    return (_payload(event, "holding_evidence_grade") == VERIFIED.value and
            lease_temporally_valid(_payload(event, "lease"), current_monotonic_time) and
            not state.safety_guard_active)


def _guard(transition: _Transition, state: MinimalM3State,
           event: MinimalM3Event, current_monotonic_time: Any) -> bool:
    tid = transition.transition_id
    if tid == "MC-T001": return _payload(event, "candidate_set_id") is not None
    if tid == "MC-T002": return _act_valid(state, event)
    if tid == "MC-T003": return not _act_valid(state, event)
    if tid == "MC-T004":
        return (not state.query_active and event.query_episode_id is not None and
                state.answer_deadline_monotonic is not None)
    if tid == "MC-T005": return state.query_active
    if tid == "MC-T006": return _wait_valid(state, event, current_monotonic_time)
    if tid == "MC-T007": return not _wait_valid(state, event, current_monotonic_time)
    if tid in {"MC-T008", "MC-T014", "MC-T015", "MC-T016", "MC-T023", "MC-T024"}: return True
    if tid in {"MC-T009", "MC-T010"}:
        return state.query_active and _payload(event, "answer_present") is True
    if tid == "MC-T011": return event.observed_monotonic_time > state.answer_deadline_monotonic
    if tid == "MC-T012": return state.query_active
    if tid == "MC-T013": return state.query_active
    if tid in {"MC-T017", "MC-T018"}: return state.revalidation_required
    if tid == "MC-T019": return _payload(event, "candidate_freshness") == "FRESH"
    if tid == "MC-T020": return _payload(event, "candidate_freshness") != "FRESH"
    if tid == "MC-T021": return state.holding_lease is not None
    if tid == "MC-T022":
        return (state.holding_lease is not None and
                state.holding_lease.expires_monotonic_time <= event.observed_monotonic_time)
    raise AssertionError("unknown transition " + tid)


def _validate_temporal_operands(state: MinimalM3State, event: MinimalM3Event,
                                current_monotonic_time: Any) -> tuple[bool, AuditReason | None]:
    for value in (state.current_time_monotonic, current_monotonic_time,
                  event.observed_monotonic_time):
        reason = _temporal_reason(value)
        if reason is not None:
            return False, reason
    for value in (state.answer_deadline_monotonic, state.decision_deadline_monotonic):
        if value is not None:
            reason = _temporal_reason(value)
            if reason is not None:
                return False, reason
    if state.holding_lease is not None:
        ok, reason = validate_lease_temporal_domain(state.holding_lease)
        if not ok:
            return False, reason
    for name in ("answer_deadline_monotonic", "decision_deadline_monotonic"):
        if name in event.payload:
            reason = _temporal_reason(event.payload[name])
            if reason is not None:
                return False, reason
    if event.event_type is EventType.DECISION_WAIT or _payload(event, "lease") is not None:
        ok, reason = validate_lease_temporal_domain(_payload(event, "lease"))
        if not ok:
            return False, reason
    return True, None


def _validate_envelope(event: MinimalM3Event) -> tuple[bool, AuditReason | None]:
    try:
        validate_finite_json_numbers(event.payload)
        if canonical_sha256(event.payload) != event.payload_digest:
            return False, AuditReason.PAYLOAD_DIGEST_MISMATCH
        if event_idempotency_key(event) != event.idempotency_key:
            return False, AuditReason.IDEMPOTENCY_KEY_MISMATCH
    except (PublicInputError, TypeError, ValueError):
        return False, AuditReason.REJECTED_INVALID_JSON_NUMERIC_DOMAIN
    return True, None


def _event_eligibility(state: MinimalM3State, event: MinimalM3Event,
                       current_monotonic_time: Any) -> tuple[bool, AuditReason | None]:
    ok, reason = _validate_temporal_operands(state, event, current_monotonic_time)
    if not ok:
        return ok, reason
    ok, reason = _validate_envelope(event)
    if not ok:
        return ok, reason
    if event.observed_monotonic_time < state.current_time_monotonic:
        return False, AuditReason.OUT_OF_ORDER_MONOTONIC_TIME
    if event.event_type in {EventType.ANSWER_ARRIVED, EventType.QUERY_TIMEOUT,
                            EventType.QUERY_CANCELLED}:
        if not state.query_active or event.query_episode_id != state.query_episode_id:
            return False, AuditReason.WRONG_OR_INACTIVE_QUERY_EPISODE
    if event.event_type is EventType.ANSWER_ARRIVED:
        semantic_relevance_owner = bool(
            event.payload.get("answer_expiry_owner")
            == "V2_8_SEMANTIC_QUERY_RELEVANCE"
            and event.payload.get("semantic_query_relevance_valid") is True
        )
        if (not semantic_relevance_owner and
                (state.answer_deadline_monotonic is None or
                 event.observed_monotonic_time > state.answer_deadline_monotonic)):
            return False, AuditReason.LATE_ANSWER
    if event.event_type is EventType.QUERY_TIMEOUT:
        if (state.answer_deadline_monotonic is None or
                event.observed_monotonic_time <= state.answer_deadline_monotonic):
            return False, AuditReason.TIMEOUT_NOT_STRICTLY_AFTER_DEADLINE
    return True, None


def _append_audit(state: MinimalM3State, event: MinimalM3Event,
                  reason: AuditReason, transition_id: Optional[str],
                  mutation_applied: bool, disposition: str) -> tuple[MinimalM3State, AuditRecord]:
    record = AuditRecord(
        sequence=state.last_event_sequence + 1,
        event_id=event.event_id, event_type=event.event_type.value,
        idempotency_key=event.idempotency_key, result=reason,
        transition_id=transition_id, mutation_applied=mutation_applied,
        concurrent_group_id=event.concurrent_group_id,
        concurrent_disposition=disposition,
    )
    audit_log = state.audit_log + (record,)
    return replace(
        state, last_event_sequence=record.sequence, audit_log=audit_log,
        audit_log_digest=canonical_sha256([item.to_dict() for item in audit_log]),
    ), record


def _revoke_lease(lease: HoldingLease | None, reason: str) -> HoldingLease | None:
    if lease is None:
        return None
    return replace(lease, revoked=True, revocation_reason=reason)


def _apply_transition(state: MinimalM3State, event: MinimalM3Event,
                      transition: _Transition, current_monotonic_time: Any) -> MinimalM3State:
    changes: dict[str, Any] = {"lifecycle_state": transition.target}
    baseline = _payload(event, "baseline_authority_eligible")
    if baseline is not None:
        changes["baseline_authority_eligible"] = baseline
    if event.event_type is EventType.CANDIDATES_READY:
        changes.update(
            candidate_set_id=_payload(event, "candidate_set_id"),
            candidate_freshness=_payload(event, "candidate_freshness"),
            decision_deadline_monotonic=_payload(event, "decision_deadline_monotonic", 10.0),
            answer_deadline_monotonic=_payload(event, "answer_deadline_monotonic", 5.0),
        )
    if event.event_type is EventType.REPLAN_COMPLETE and _payload(event, "candidate_freshness") == "FRESH":
        changes.update(
            candidate_set_id=_payload(event, "candidate_set_id") or state.candidate_set_id,
            candidate_freshness="FRESH", revalidation_required=False,
            replan_required=False,
        )
    if event.event_type is EventType.SAFETY_PREEMPTED:
        changes["safety_guard_active"] = True

    tid = transition.transition_id
    if tid in {"MC-T004", "MC-T006"}:
        changes.update(query_active=True, query_episode_id=event.query_episode_id)
    if tid in {"MC-T009", "MC-T010", "MC-T011", "MC-T012", "MC-T021", "MC-T022", "MC-T024"}:
        changes["query_active"] = False
    if tid in {"MC-T009", "MC-T010", "MC-T013", "MC-T014", "MC-T015", "MC-T016", "MC-T020"}:
        changes["candidate_freshness"] = "STALE"
        changes["revalidation_required"] = True
    if tid == "MC-T017":
        changes.update(revalidation_required=False, replan_required=True)
    if tid == "MC-T006":
        changes["holding_lease"] = HoldingLease.from_dict(_payload(event, "lease"))
    if tid in {"MC-T003", "MC-T007", "MC-T008", "MC-T009", "MC-T010", "MC-T011",
               "MC-T012", "MC-T018", "MC-T021", "MC-T022", "MC-T023", "MC-T024"}:
        changes["holding_lease"] = _revoke_lease(state.holding_lease, event.event_type.value)

    next_state = replace(state, **changes)
    entering = next_state.lifecycle_state in {LifecycleState.FALLBACK, LifecycleState.TERMINATED}
    if entering:
        next_state = replace(next_state, holding_lease=_revoke_lease(
            next_state.holding_lease, next_state.lifecycle_state.value))
    active_lease = (next_state.holding_lease if next_state.holding_lease is not None and
                    not next_state.holding_lease.revoked else None)
    next_time = max(state.current_time_monotonic, current_monotonic_time,
                    event.observed_monotonic_time)
    authority = resolve_authority(
        safety_guard_active=next_state.safety_guard_active,
        baseline_authority_eligible=next_state.baseline_authority_eligible,
        holding_requested=active_lease is not None,
        holding_evidence_eligible=bool(active_lease and active_lease.evidence_grade is VERIFIED),
        holding_lease=active_lease, current_monotonic_time=current_monotonic_time,
        entering_fallback_or_terminated=entering,
    )
    return replace(next_state, authority=authority, current_time_monotonic=next_time)


def check_invariants(state: MinimalM3State) -> tuple[str, ...]:
    failures: list[str] = []
    if not isinstance(state.query_active, bool): failures.append("QUERY_ACTIVE_NOT_BOOLEAN")
    if state.candidate_freshness not in {"FRESH", "STALE", "UNKNOWN"}: failures.append("INVALID_CANDIDATE_FRESHNESS")
    for value in (state.current_time_monotonic,):
        reason = _temporal_reason(value)
        if reason is not None: failures.append("STATE_TEMPORAL_DOMAIN:" + reason.value)
    if state.holding_lease is not None:
        ok, reason = validate_lease_temporal_domain(state.holding_lease)
        if not ok: failures.append("LEASE_TEMPORAL_RELATION:" + reason.value)
    active = state.holding_lease if state.holding_lease and not state.holding_lease.revoked else None
    valid = bool(active and lease_temporally_valid(active, state.current_time_monotonic))
    if (state.authority is ControlAuthority.M3_HOLDING_CONTROL) != valid:
        failures.append("HOLDING_AUTHORITY_LEASE_BIJECTION")
    if state.safety_guard_active and state.authority is not ControlAuthority.INDEPENDENT_SAFETY_GUARD:
        failures.append("SAFETY_AUTHORITY_DOWNGRADE")
    if state.lifecycle_state in {LifecycleState.FALLBACK, LifecycleState.TERMINATED} and active:
        failures.append("ACTIVE_LEASE_IN_FALLBACK_OR_TERMINATED")
    if state.low_level_control_outputs: failures.append("LOW_LEVEL_CONTROL_OUTPUT")
    if state.model_forward_count != 0: failures.append("MODEL_FORWARD_CALLED")
    return tuple(failures)


def _business_snapshot(state: MinimalM3State) -> dict[str, Any]:
    return {key: value for key, value in state.to_dict().items() if key not in _AUDIT_EXCLUDED}


def _trace(record: AuditRecord, transition_id: Optional[str], mutation: bool,
           result: ProcessingResult, unchanged: bool, loser: bool,
           invariants: Sequence[str] = ()) -> TraceRecord:
    return TraceRecord(
        audit=record, transition_id=transition_id,
        mutation_applied=mutation, processing_result=result.value,
        invariants=tuple(invariants), business_state_unchanged=unchanged,
        reducer_pipeline=tuple(
            ["temporal_numeric_domain_validation", "rejection_audit_return"]
            if result is ProcessingResult.REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN else
            ["temporal_numeric_domain_validation", "idempotency_check", "event_eligibility",
             "effective_monotonic_time_ordering", "tie_priority", "transition_guard",
             "mutation", "authority_resolution", "invariant_check", "audit_append"]
        ),
        concurrent_loser=loser,
    )


def _normalize_state(value: Any) -> MinimalM3State:
    try:
        if isinstance(value, MinimalM3State):
            lease = value.holding_lease
            raw = {
                "lifecycle_state": (value.lifecycle_state.value
                                    if isinstance(value.lifecycle_state, LifecycleState)
                                    else value.lifecycle_state),
                "query_episode_id": value.query_episode_id,
                "query_active": value.query_active,
                "candidate_set_id": value.candidate_set_id,
                "candidate_freshness": value.candidate_freshness,
                "answer_deadline_monotonic": value.answer_deadline_monotonic,
                "decision_deadline_monotonic": value.decision_deadline_monotonic,
                "authority": (value.authority.value
                              if isinstance(value.authority, ControlAuthority)
                              else value.authority),
                "safety_guard_active": value.safety_guard_active,
                "baseline_authority_eligible": value.baseline_authority_eligible,
                "holding_lease": (lease.to_dict()
                                  if isinstance(lease, HoldingLease) else lease),
                "revalidation_required": value.revalidation_required,
                "replan_required": value.replan_required,
                "last_event_sequence": value.last_event_sequence,
                "audit_log_digest": value.audit_log_digest,
                "processed_idempotency_keys": value.processed_idempotency_keys,
                "audit_log": tuple(item.to_dict() if isinstance(item, AuditRecord) else item
                                   for item in value.audit_log),
                "current_time_monotonic": value.current_time_monotonic,
                "model_forward_count": value.model_forward_count,
                "low_level_control_outputs": value.low_level_control_outputs,
            }
            return MinimalM3State.from_dict(raw)
        return MinimalM3State.from_dict(value)
    except PublicInputError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PublicInputError(
            ProcessingResult.REJECTED_MALFORMED_EVENT.value,
            AuditReason.MALFORMED_EVENT.value,
            "malformed state: " + type(exc).__name__) from exc


def _normalize_event(value: Any) -> MinimalM3Event:
    try:
        if isinstance(value, MinimalM3Event):
            # Validate the complete public shape without trusting construction,
            # but preserve supplied digest/key for the later ordered envelope check.
            MinimalM3Event.create(
                event_id=value.event_id, event_type=value.event_type,
                query_episode_id=value.query_episode_id,
                source_component=value.source_component,
                observed_monotonic_time=value.observed_monotonic_time,
                source_simulation_time=value.source_simulation_time,
                calendar_utc=value.calendar_utc, payload=value.payload,
                concurrent_group_id=value.concurrent_group_id,
            )
            return value
        return MinimalM3Event.from_dict(value)
    except PublicInputError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PublicInputError(
            ProcessingResult.REJECTED_MALFORMED_EVENT.value,
            AuditReason.MALFORMED_EVENT.value,
            "malformed event: " + type(exc).__name__) from exc


def _safe_event_field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _input_rejection(state: Any, event: Any, error: PublicInputError,
                     *, group_detail: str | None = None) -> ReductionResult:
    base_audit = (state.audit_log if isinstance(state, MinimalM3State) else ())
    last_sequence = (state.last_event_sequence
                     if isinstance(state, MinimalM3State) and
                     type(state.last_event_sequence) is int else 0)
    event_type = _safe_event_field(event, "event_type")
    if isinstance(event_type, EventType):
        event_type = event_type.value
    record = AuditRecord(
        sequence=last_sequence + 1,
        event_id=(_safe_event_field(event, "event_id")
                  if isinstance(_safe_event_field(event, "event_id"), str) else None),
        event_type=event_type if isinstance(event_type, str) else None,
        idempotency_key=(_safe_event_field(event, "idempotency_key")
                         if isinstance(_safe_event_field(event, "idempotency_key"), str)
                         else None),
        result=AuditReason(error.audit_reason), transition_id=None,
        mutation_applied=False,
        concurrent_group_id=(_safe_event_field(event, "concurrent_group_id")
                             if isinstance(_safe_event_field(event, "concurrent_group_id"), str)
                             else None),
        concurrent_disposition=group_detail or "INPUT_REJECTION_AUDIT_ONLY",
    )
    processing = ProcessingResult(error.processing_result)
    trace = TraceRecord(
        audit=record, transition_id=None, mutation_applied=False,
        processing_result=processing.value, invariants=(),
        business_state_unchanged=True,
        reducer_pipeline=("public_boundary_validation", "rejection_audit_return"),
        concurrent_loser=False,
    )
    return ReductionResult(
        state=state, audit=base_audit + (record,), transition_ids=(),
        processing_results=(processing,), traces=(trace,))


def _apply_one(state: MinimalM3State, event: MinimalM3Event,
               current_monotonic_time: Any, *, concurrent_loser: bool = False,
               precheck: Any = None) -> tuple[MinimalM3State, Mapping[str, Any], ProcessingResult]:
    before = _business_snapshot(state)
    temporal_ok, temporal_reason = _validate_temporal_operands(state, event, current_monotonic_time)
    if not temporal_ok:
        record = AuditRecord(
            sequence=state.last_event_sequence + 1,
            event_id=event.event_id, event_type=event.event_type.value,
            idempotency_key=event.idempotency_key, result=temporal_reason,
            transition_id=None, mutation_applied=False,
            concurrent_group_id=event.concurrent_group_id,
            concurrent_disposition="TEMPORAL_REJECTION_AUDIT_ONLY",
        )
        result = ProcessingResult.REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN
        return state, _trace(record, None, False, result,
                             before == _business_snapshot(state), concurrent_loser), result

    envelope_ok, envelope_reason = _validate_envelope(event)
    if not envelope_ok:
        record = AuditRecord(
            sequence=state.last_event_sequence + 1,
            event_id=event.event_id, event_type=event.event_type.value,
            idempotency_key=(event.idempotency_key
                             if isinstance(event.idempotency_key, str) else None),
            result=envelope_reason, transition_id=None, mutation_applied=False,
            concurrent_group_id=event.concurrent_group_id,
            concurrent_disposition="ENVELOPE_REJECTION_AUDIT_ONLY",
        )
        result = ProcessingResult.REJECTED_MALFORMED_EVENT
        return state, _trace(record, None, False, result,
                             before == _business_snapshot(state), concurrent_loser), result

    key = event.idempotency_key
    if precheck == "DUPLICATE" or (precheck is None and key in state.processed_idempotency_keys):
        state, record = _append_audit(state, event, AuditReason.DUPLICATE, None, False,
                                      "DUPLICATE_AUDIT_ONLY")
        result = ProcessingResult.REJECTED_DUPLICATE
        return state, _trace(record, None, False, result,
                             before == _business_snapshot(state), concurrent_loser), result

    eligible, reason = precheck if isinstance(precheck, tuple) else _event_eligibility(
        state, event, current_monotonic_time)
    if not eligible:
        envelope_ok, _ = _validate_envelope(event)
        if envelope_ok and key not in state.processed_idempotency_keys:
            state = replace(state, processed_idempotency_keys=state.processed_idempotency_keys + (key,))
        state, record = _append_audit(
            state, event, reason, None, False,
            "RACE_LOSER" if concurrent_loser else "NONE")
        result = ProcessingResult.REJECTED_INELIGIBLE
        return state, _trace(record, None, False, result,
                             before == _business_snapshot(state), concurrent_loser), result

    candidates = [item for item in _TRANSITIONS
                  if item.event_type is event.event_type and
                  (item.source is None or item.source is state.lifecycle_state)]
    passing = [item for item in candidates if _guard(item, state, event, current_monotonic_time)]
    if len(passing) != 1:
        reason = AuditReason.NO_TRANSITION if not passing else AuditReason.NONDETERMINISTIC_TRANSITION
        state, record = _append_audit(
            state, event, reason, None, False,
            "RACE_LOSER" if concurrent_loser else "NONE")
        result = ProcessingResult.REJECTED_NO_UNIQUE_TRANSITION
        return state, _trace(record, None, False, result,
                             before == _business_snapshot(state), concurrent_loser), result

    transition = passing[0]
    candidate = state
    if key not in candidate.processed_idempotency_keys:
        candidate = replace(candidate, processed_idempotency_keys=
                            candidate.processed_idempotency_keys + (key,))
    candidate = _apply_transition(candidate, event, transition, current_monotonic_time)
    failures = check_invariants(candidate)
    if failures:
        record = AuditRecord(
            sequence=state.last_event_sequence + 1,
            event_id=event.event_id, event_type=event.event_type.value,
            idempotency_key=event.idempotency_key,
            result=AuditReason.INVARIANT_REJECTED, transition_id=None,
            mutation_applied=False,
            concurrent_group_id=event.concurrent_group_id,
            concurrent_disposition="INVARIANT_REJECTION_AUDIT_ONLY",
        )
        result = ProcessingResult.REJECTED_INVARIANT
        return state, _trace(record, None, False, result, True,
                             concurrent_loser, failures), result
    disposition = (("CO_MUTATION_RETAINED" if concurrent_loser else "WINNER")
                   if event.concurrent_group_id is not None else "NONE")
    state, record = _append_audit(candidate, event, transition.audit_reason,
                                  transition.transition_id, True, disposition)
    result = ProcessingResult.PROCESSED
    return state, _trace(record, transition.transition_id, True, result, False,
                         concurrent_loser), result


def _result(state: MinimalM3State, traces: Sequence[TraceRecord],
            processing: Sequence[ProcessingResult],
            audit: Sequence[AuditRecord] | None = None) -> ReductionResult:
    transition_ids = tuple(item["transition_id"] for item in traces if item["transition_id"])
    return ReductionResult(
        state=state, audit=tuple(state.audit_log if audit is None else audit),
        transition_ids=transition_ids,
        processing_results=tuple(processing), traces=tuple(traces),
    )


def reduce_event(state: MinimalM3State, event: MinimalM3Event,
                 current_monotonic_time: Any) -> ReductionResult:
    original_state = state
    try:
        state = _normalize_state(state)
        event = _normalize_event(event)
    except PublicInputError as error:
        return _input_rejection(original_state, event, error)
    next_state, trace, processing = _apply_one(state, event, current_monotonic_time)
    if processing in {
            ProcessingResult.REJECTED_INVARIANT,
            ProcessingResult.REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN,
            ProcessingResult.REJECTED_MALFORMED_EVENT}:
        return _result(next_state, (trace,), (processing,),
                       audit=next_state.audit_log + (trace.audit,))
    return _result(next_state, (trace,), (processing,))


def reduce_event_group(state: MinimalM3State, events: Iterable[MinimalM3Event],
                       current_monotonic_time: Any) -> ReductionResult:
    original_state = state
    try:
        state = _normalize_state(state)
        if events is None or isinstance(events, (str, bytes, Mapping)):
            raise PublicInputError(
                ProcessingResult.REJECTED_MALFORMED_EVENT.value,
                AuditReason.MALFORMED_EVENT.value,
                "concurrent group must be an event iterable")
        incoming_raw = tuple(events)
        incoming = tuple(_normalize_event(event) for event in incoming_raw)
    except PublicInputError as error:
        offending = locals().get("incoming_raw", (None,))
        return _input_rejection(original_state, offending[0] if offending else None,
                                error, group_detail="MALFORMED_GROUP_REJECTED_ATOMICALLY")
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        error = PublicInputError(
            ProcessingResult.REJECTED_MALFORMED_EVENT.value,
            AuditReason.MALFORMED_EVENT.value,
            "malformed concurrent group: " + type(exc).__name__)
        return _input_rejection(original_state, None, error,
                                group_detail="MALFORMED_GROUP_REJECTED_ATOMICALLY")
    if not incoming:
        return _result(state, (), ())
    prepared: list[tuple[int, MinimalM3Event, Any]] = []
    keys_seen = set(state.processed_idempotency_keys)
    for index, event in enumerate(incoming):
        temporal_ok, temporal_reason = _validate_temporal_operands(
            state, event, current_monotonic_time)
        if not temporal_ok:
            status: Any = (False, temporal_reason)
        elif event.idempotency_key in keys_seen:
            status = "DUPLICATE"
        else:
            envelope_ok, envelope_reason = _validate_envelope(event)
            status = (_event_eligibility(state, event, current_monotonic_time)
                      if envelope_ok else (False, envelope_reason))
            if envelope_ok:
                keys_seen.add(event.idempotency_key)
        prepared.append((index, event, status))
    prepared.sort(key=lambda item: (_PRIORITY.get(item[1].event_type, 6), item[0]))
    traces: list[Mapping[str, Any]] = []
    processing: list[ProcessingResult] = []
    external_audit: list[AuditRecord] = []
    seen_groups: set[str] = set()
    current = state
    for _, event, status in prepared:
        group = event.concurrent_group_id
        loser = bool(group is not None and group in seen_groups)
        current, trace, result = _apply_one(
            current, event, current_monotonic_time,
            concurrent_loser=loser, precheck=status)
        if group is not None:
            seen_groups.add(group)
        traces.append(trace)
        processing.append(result)
        if result in {
                ProcessingResult.REJECTED_INVARIANT,
                ProcessingResult.REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN,
                ProcessingResult.REJECTED_MALFORMED_EVENT}:
            external_audit.append(trace.audit)
    return _result(current, traces, processing,
                   audit=current.audit_log + tuple(external_audit))


def reduce_events(initial_state: MinimalM3State,
                  events: Iterable[MinimalM3Event]) -> ReductionResult:
    """Reduce contiguous equal-observed-time groups deterministically."""
    try:
        state = _normalize_state(initial_state)
        if events is None or isinstance(events, (str, bytes, Mapping)):
            raise TypeError("events must be an iterable of event values")
        raw_incoming = tuple(events)
        incoming = tuple(_normalize_event(event) for event in raw_incoming)
    except PublicInputError as error:
        offending = locals().get("raw_incoming", (None,))
        return _input_rejection(initial_state, offending[0] if offending else None,
                                error, group_detail="MALFORMED_EVENT_STREAM_REJECTED_ATOMICALLY")
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        error = PublicInputError(
            ProcessingResult.REJECTED_MALFORMED_EVENT.value,
            AuditReason.MALFORMED_EVENT.value,
            "malformed event stream: " + type(exc).__name__)
        return _input_rejection(initial_state, None, error,
                                group_detail="MALFORMED_EVENT_STREAM_REJECTED_ATOMICALLY")
    traces: list[TraceRecord] = []
    processing: list[ProcessingResult] = []
    cursor = 0
    while cursor < len(incoming):
        event = incoming[cursor]
        if not is_finite_real(event.observed_monotonic_time):
            group = (event,)
            group_time = event.observed_monotonic_time
            cursor += 1
        else:
            group_time = event.observed_monotonic_time
            end = cursor + 1
            while (end < len(incoming) and
                   is_finite_real(incoming[end].observed_monotonic_time) and
                   incoming[end].observed_monotonic_time == group_time):
                end += 1
            group = incoming[cursor:end]
            cursor = end
        result = reduce_event_group(state, group, group_time)
        state = result.state
        traces.extend(result.traces)
        processing.extend(result.processing_results)
    external_audit = tuple(
        trace.audit for trace, result in zip(traces, processing)
        if result in {
            ProcessingResult.REJECTED_INVARIANT,
            ProcessingResult.REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN,
            ProcessingResult.REJECTED_MALFORMED_EVENT})
    return _result(state, traces, processing,
                   audit=state.audit_log + external_audit)
