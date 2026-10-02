"""Pure record-to-MinimalM3Event adapters; no oracle or runtime dependency."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Union

from driveclarify_m3_minimal_core import EventType, MinimalM3Event, MinimalM3State

from .contracts import AdapterRejection, M2BDecisionEnvelope, ReplayRecord
from .serialization import to_json_compatible


ADAPTER_ID = "driveclarify.m3.default-replay-adapter"
ADAPTER_VERSION = "v1"

_LITERAL_MAPPING = {
    "CANDIDATES_READY": EventType.CANDIDATES_READY,
    "PASSENGER_ANSWER": EventType.ANSWER_ARRIVED,
    "QUERY_TIMEOUT": EventType.QUERY_TIMEOUT,
    "QUERY_CANCEL": EventType.QUERY_CANCELLED,
    "WORLD_STATE_CHANGE": EventType.WORLD_STATE_CHANGED,
    "CANDIDATE_STALE": EventType.CANDIDATE_STALE,
    "HOLDING_CAPABILITY_LOST": EventType.HOLDING_CAPABILITY_LOST,
    "SAFETY_PREEMPTION": EventType.SAFETY_PREEMPTED,
    "LEASE_EXPIRY_CHECK": EventType.LEASE_EXPIRY_CHECK,
    "REVALIDATION_PASS": EventType.REVALIDATION_PASSED,
    "REVALIDATION_FAIL": EventType.REVALIDATION_FAILED,
    "REPLAN_RESULT": EventType.REPLAN_COMPLETE,
}
_ACTION_MAPPING = {
    "ACT": EventType.DECISION_ACT,
    "ASK": EventType.DECISION_ASK,
    "WAIT": EventType.DECISION_WAIT,
    "FALLBACK": EventType.DECISION_FALLBACK,
}


def _reject(record: ReplayRecord, category: str, detail: str) -> AdapterRejection:
    return AdapterRejection(record.record_id, record.adapter_id,
                            record.adapter_version, category, detail)


def adapt_record(replay_record: ReplayRecord,
                 current_minimal_state: MinimalM3State) -> MinimalM3Event | AdapterRejection:
    """Map a validated record without decisions, defaults, repair, or state mutation."""
    if not isinstance(replay_record, ReplayRecord):
        raise TypeError("adapt_record requires ReplayRecord")
    if not isinstance(current_minimal_state, MinimalM3State):
        return _reject(replay_record, "INVALID_STATE", "STATE_TYPE")
    if (replay_record.adapter_id != ADAPTER_ID or
            replay_record.adapter_version != ADAPTER_VERSION):
        return _reject(replay_record, "MISSING_REQUIRED_FIELD",
                       "UNREGISTERED_ADAPTER_VERSION")
    payload: Mapping[str, Any] = replay_record.payload
    try:
        if replay_record.record_type == "M2B_DECISION":
            if set(payload) != {"decision", "event_payload"}:
                return _reject(replay_record, "INVALID_PAYLOAD",
                               "M2B_PAYLOAD_NOT_CLOSED")
            decision = M2BDecisionEnvelope.from_dict(payload["decision"])
            event_type = _ACTION_MAPPING[decision.selected_action.value]
            if decision.query_episode_id != replay_record.query_episode_id:
                return _reject(replay_record, "INVALID_QUERY_IDENTITY",
                               "QUERY_IDENTITY_CHANGED")
            if decision.candidate_set_id != replay_record.candidate_set_id:
                return _reject(replay_record, "INVALID_PAYLOAD",
                               "CANDIDATE_IDENTITY_CHANGED")
            event_payload = payload["event_payload"]
            if not isinstance(event_payload, Mapping):
                return _reject(replay_record, "INVALID_PAYLOAD",
                               "EVENT_PAYLOAD_NOT_OBJECT")
            # Evidence and lease come only from the record. No state-derived defaults.
            recorded_evidence = (event_payload.get("holding_evidence_grade")
                                 if decision.selected_action.value == "WAIT" else
                                 event_payload.get("act_evidence_grade"))
            if decision.evidence_grade != recorded_evidence:
                return _reject(replay_record, "LEASE_OR_EVIDENCE_INVALID",
                               "EVIDENCE_NOT_PRESERVED")
            if decision.selected_action.value == "WAIT" and (
                    to_json_compatible(decision.holding_lease_payload) !=
                    to_json_compatible(event_payload.get("lease"))):
                return _reject(replay_record, "LEASE_OR_EVIDENCE_INVALID",
                               "LEASE_NOT_PRESERVED")
            payload_out = to_json_compatible(event_payload)
        elif replay_record.record_type == "REVALIDATION_RESULT":
            if payload.get("result") == "PASSED":
                event_type = EventType.REVALIDATION_PASSED
            elif payload.get("result") == "FAILED":
                event_type = EventType.REVALIDATION_FAILED
            else:
                return _reject(replay_record, "INVALID_ENUM",
                               "INVALID_REVALIDATION_RESULT")
            payload_out = to_json_compatible(payload)
        else:
            event_type = _LITERAL_MAPPING.get(replay_record.record_type)
            if event_type is None:
                return _reject(replay_record, "UNSUPPORTED_RECORD_TYPE",
                               "NO_FROZEN_MAPPING")
            payload_out = to_json_compatible(payload)
        return MinimalM3Event.create(
            event_id=replay_record.record_id,
            event_type=event_type,
            query_episode_id=replay_record.query_episode_id,
            source_component=replay_record.source_component,
            observed_monotonic_time=replay_record.replay_monotonic_time,
            source_simulation_time=replay_record.source_simulation_time,
            calendar_utc=replay_record.calendar_utc,
            payload=payload_out,
            concurrent_group_id=replay_record.concurrent_group_id,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _reject(replay_record, "INVALID_PAYLOAD", type(exc).__name__)


Adapter = Callable[
    [ReplayRecord, MinimalM3State],
    Union[MinimalM3Event, AdapterRejection],
]
DEFAULT_ADAPTER_REGISTRY: Mapping[tuple[str, str], Adapter] = {
    (ADAPTER_ID, ADAPTER_VERSION): adapt_record,
}
