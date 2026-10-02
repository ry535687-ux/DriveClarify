"""Immutable in-memory contracts for no-output M3 shadow execution."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Mapping

from driveclarify_m3_offline_replay.contracts import (
    ReplayEpisode, ReplayStepResult, ReplayTrace,
)
from driveclarify_m3_offline_replay.serialization import (
    canonical_sha256,
    freeze_json,
    require_sha256,
    to_json_compatible,
)


SHADOW_INPUT_SCHEMA = "driveclarify.m3.shadow-input.v1"
SHADOW_STEP_SCHEMA = "driveclarify.m3.shadow-step.v1"
SHADOW_TRACE_SCHEMA = "driveclarify.m3.shadow-trace.v1"
SHADOW_REJECTION_SCHEMA = "driveclarify.m3.shadow-rejection.v1"

_DATACLASS_SLOT_KWARGS = {"slots": True} if sys.version_info >= (3, 10) else {}


def _nonempty_string(value: Any, label: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(label + " must be a nonempty string")


def _optional_string(value: Any, label: str) -> None:
    if value is not None and type(value) is not str:
        raise TypeError(label + " must be a string or null")


def _nonnegative_integer(value: Any, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(label + " must be a nonnegative integer")


@dataclass(frozen=True, **_DATACLASS_SLOT_KWARGS)
class ShadowInputEnvelope:
    """Hash-bound replay input accepted by the stateless bridge."""

    schema_version: str
    envelope_id: str
    episode: ReplayEpisode
    episode_sha256: str
    envelope_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != SHADOW_INPUT_SCHEMA:
            raise ValueError("shadow input schema mismatch")
        if not isinstance(self.episode, ReplayEpisode):
            raise TypeError("episode must be ReplayEpisode")
        for name in ("envelope_id", "episode_sha256", "envelope_sha256"):
            require_sha256(getattr(self, name), name)
        actual_episode_sha = canonical_sha256(self.episode.to_dict())
        if self.episode_sha256 != actual_episode_sha:
            raise ValueError("episode hash mismatch")
        projection = self._hash_projection()
        if self.envelope_id != canonical_sha256([
                SHADOW_INPUT_SCHEMA, self.episode.episode_id, actual_episode_sha]):
            raise ValueError("envelope identity mismatch")
        if self.envelope_sha256 != canonical_sha256(projection):
            raise ValueError("envelope hash mismatch")

    def _hash_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "envelope_id": self.envelope_id,
            "episode": self.episode.to_dict(),
            "episode_sha256": self.episode_sha256,
        }

    @classmethod
    def create(cls, episode: ReplayEpisode) -> "ShadowInputEnvelope":
        if not isinstance(episode, ReplayEpisode):
            raise TypeError("ShadowInputEnvelope.create requires ReplayEpisode")
        episode_sha = canonical_sha256(episode.to_dict())
        envelope_id = canonical_sha256([
            SHADOW_INPUT_SCHEMA, episode.episode_id, episode_sha])
        projection = {
            "schema_version": SHADOW_INPUT_SCHEMA,
            "envelope_id": envelope_id,
            "episode": episode.to_dict(),
            "episode_sha256": episode_sha,
        }
        return cls(
            schema_version=SHADOW_INPUT_SCHEMA,
            envelope_id=envelope_id,
            episode=episode,
            episode_sha256=episode_sha,
            envelope_sha256=canonical_sha256(projection),
        )

    @classmethod
    def from_episode(cls, episode: ReplayEpisode) -> "ShadowInputEnvelope":
        return cls.create(episode)

    def to_dict(self) -> dict[str, Any]:
        return {**self._hash_projection(), "envelope_sha256": self.envelope_sha256}


@dataclass(frozen=True, **_DATACLASS_SLOT_KWARGS)
class ShadowStepResult:
    """Deterministic, immutable projection of one replay step."""

    schema_version: str
    episode_id: str
    record_id: str
    sequence_index: int
    pre_state_sha256: str
    adapted_event_sha256: str | None
    adapter_result: str
    processing_result: str
    transition_ids: tuple[str, ...]
    post_state_sha256: str
    authority_before: str
    authority_after: str
    query_active_before: bool
    query_active_after: bool
    candidate_freshness_before: str
    candidate_freshness_after: str
    lease_status_before: Mapping[str, Any] | None
    lease_status_after: Mapping[str, Any] | None
    audit_records: tuple[Mapping[str, Any], ...]
    business_mutation: bool
    low_level_output_count: int
    replay_step_sha256: str
    shadow_step_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != SHADOW_STEP_SCHEMA:
            raise ValueError("shadow step schema mismatch")
        for name in ("episode_id", "record_id", "adapter_result",
                     "processing_result", "authority_before", "authority_after",
                     "candidate_freshness_before", "candidate_freshness_after"):
            _nonempty_string(getattr(self, name), name)
        _nonnegative_integer(self.sequence_index, "sequence_index")
        _nonnegative_integer(self.low_level_output_count, "low_level_output_count")
        for name in ("pre_state_sha256", "post_state_sha256",
                     "replay_step_sha256", "shadow_step_sha256"):
            require_sha256(getattr(self, name), name)
        if self.adapted_event_sha256 is not None:
            require_sha256(self.adapted_event_sha256, "adapted_event_sha256")
        if (not isinstance(self.transition_ids, tuple) or
                any(type(item) is not str for item in self.transition_ids)):
            raise TypeError("transition_ids must be tuple[str, ...]")
        for name in ("query_active_before", "query_active_after", "business_mutation"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(name + " must be boolean")
        if not isinstance(self.audit_records, tuple):
            raise TypeError("audit_records must be a tuple")
        object.__setattr__(self, "audit_records",
                           tuple(freeze_json(item) for item in self.audit_records))
        if self.lease_status_before is not None:
            object.__setattr__(self, "lease_status_before",
                               freeze_json(self.lease_status_before))
        if self.lease_status_after is not None:
            object.__setattr__(self, "lease_status_after",
                               freeze_json(self.lease_status_after))
        if self.shadow_step_sha256 != canonical_sha256(self._hash_projection()):
            raise ValueError("shadow step hash mismatch")

    def _hash_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "record_id": self.record_id,
            "sequence_index": self.sequence_index,
            "pre_state_sha256": self.pre_state_sha256,
            "adapted_event_sha256": self.adapted_event_sha256,
            "adapter_result": self.adapter_result,
            "processing_result": self.processing_result,
            "transition_ids": list(self.transition_ids),
            "post_state_sha256": self.post_state_sha256,
            "authority_before": self.authority_before,
            "authority_after": self.authority_after,
            "query_active_before": self.query_active_before,
            "query_active_after": self.query_active_after,
            "candidate_freshness_before": self.candidate_freshness_before,
            "candidate_freshness_after": self.candidate_freshness_after,
            "lease_status_before": to_json_compatible(self.lease_status_before),
            "lease_status_after": to_json_compatible(self.lease_status_after),
            "audit_records": to_json_compatible(self.audit_records),
            "business_mutation": self.business_mutation,
            "low_level_output_count": self.low_level_output_count,
            "replay_step_sha256": self.replay_step_sha256,
        }

    @classmethod
    def from_replay_step(cls, step: ReplayStepResult) -> "ShadowStepResult":
        if not isinstance(step, ReplayStepResult):
            raise TypeError("from_replay_step requires ReplayStepResult")
        values = {
            "schema_version": SHADOW_STEP_SCHEMA,
            "episode_id": step.episode_id,
            "record_id": step.record_id,
            "sequence_index": step.sequence_index,
            "pre_state_sha256": step.pre_state_sha256,
            "adapted_event_sha256": step.adapted_event_sha256,
            "adapter_result": step.adapter_result,
            "processing_result": step.reduction_processing_result,
            "transition_ids": step.executed_transition_ids,
            "post_state_sha256": step.post_state_sha256,
            "authority_before": step.authority_before,
            "authority_after": step.authority_after,
            "query_active_before": step.query_active_before,
            "query_active_after": step.query_active_after,
            "candidate_freshness_before": step.candidate_freshness_before,
            "candidate_freshness_after": step.candidate_freshness_after,
            "lease_status_before": to_json_compatible(step.lease_status_before),
            "lease_status_after": to_json_compatible(step.lease_status_after),
            "audit_records": tuple(to_json_compatible(item) for item in step.audit_records),
            "business_mutation": step.business_mutation,
            "low_level_output_count": step.low_level_control_output_count,
            "replay_step_sha256": step.step_trace_sha256,
        }
        return cls(**values, shadow_step_sha256=canonical_sha256(values))

    def to_dict(self) -> dict[str, Any]:
        return {**self._hash_projection(), "shadow_step_sha256": self.shadow_step_sha256}


@dataclass(frozen=True, **_DATACLASS_SLOT_KWARGS)
class ShadowTrace:
    """No-output shadow trace deterministically bound to a ReplayTrace."""

    schema_version: str
    shadow_trace_id: str
    envelope_id: str
    episode_id: str
    episode_records_sha256: str
    replay_trace_id: str
    replay_trace_sha256: str
    step_results: tuple[ShadowStepResult, ...]
    step_count: int
    accepted_record_count: int
    rejected_record_count: int
    transition_ids: tuple[str, ...]
    final_state: Mapping[str, Any]
    final_state_sha256: str
    authority_sequence: tuple[str, ...]
    query_lifecycle_summary: Mapping[str, Any]
    lease_lifecycle_summary: Mapping[str, Any]
    audit_records: tuple[Mapping[str, Any], ...]
    processing_result_sequence: tuple[str, ...]
    audit_reason_sequence: tuple[str, ...]
    invariant_violation_count: int
    partial_mutation_count: int
    second_query_count: int
    stale_unknown_act_count: int
    low_level_output_count: int
    m3_control_write_delta: int
    model_forward_delta: int
    planner_call_delta: int
    pid_call_delta: int
    terminal_disposition: str
    reducer_call_count: int
    shadow_trace_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != SHADOW_TRACE_SCHEMA:
            raise ValueError("shadow trace schema mismatch")
        for name in ("shadow_trace_id", "envelope_id", "episode_records_sha256",
                     "replay_trace_id", "replay_trace_sha256", "final_state_sha256",
                     "shadow_trace_sha256"):
            require_sha256(getattr(self, name), name)
        _nonempty_string(self.episode_id, "episode_id")
        _nonempty_string(self.terminal_disposition, "terminal_disposition")
        for name in ("step_count", "accepted_record_count", "rejected_record_count",
                     "invariant_violation_count", "partial_mutation_count",
                     "second_query_count", "stale_unknown_act_count",
                     "low_level_output_count", "m3_control_write_delta",
                     "model_forward_delta", "planner_call_delta", "pid_call_delta",
                     "reducer_call_count"):
            _nonnegative_integer(getattr(self, name), name)
        if (not isinstance(self.step_results, tuple) or
                any(not isinstance(item, ShadowStepResult) for item in self.step_results)):
            raise TypeError("step_results must be tuple[ShadowStepResult, ...]")
        if self.step_count != len(self.step_results):
            raise ValueError("shadow step count mismatch")
        if self.step_count != self.accepted_record_count + self.rejected_record_count:
            raise ValueError("shadow record accounting mismatch")
        for name in ("transition_ids", "authority_sequence",
                     "processing_result_sequence", "audit_reason_sequence"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or any(type(item) is not str for item in value):
                raise TypeError(name + " must be tuple[str, ...]")
        object.__setattr__(self, "final_state", freeze_json(self.final_state))
        object.__setattr__(self, "query_lifecycle_summary",
                           freeze_json(self.query_lifecycle_summary))
        object.__setattr__(self, "lease_lifecycle_summary",
                           freeze_json(self.lease_lifecycle_summary))
        if not isinstance(self.audit_records, tuple):
            raise TypeError("audit_records must be a tuple")
        object.__setattr__(self, "audit_records",
                           tuple(freeze_json(item) for item in self.audit_records))
        for name in ("m3_control_write_delta", "model_forward_delta",
                     "planner_call_delta", "pid_call_delta"):
            if getattr(self, name) != 0:
                raise ValueError(name + " must remain zero in shadow execution")
        if canonical_sha256(self.final_state) != self.final_state_sha256:
            raise ValueError("shadow final state hash mismatch")
        if self.shadow_trace_id != canonical_sha256([
                SHADOW_TRACE_SCHEMA, self.envelope_id, self.replay_trace_id,
                self.replay_trace_sha256]):
            raise ValueError("shadow trace identity mismatch")
        if self.shadow_trace_sha256 != canonical_sha256(self._hash_projection()):
            raise ValueError("shadow trace hash mismatch")

    def _hash_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "shadow_trace_id": self.shadow_trace_id,
            "envelope_id": self.envelope_id,
            "episode_id": self.episode_id,
            "episode_records_sha256": self.episode_records_sha256,
            "replay_trace_id": self.replay_trace_id,
            "replay_trace_sha256": self.replay_trace_sha256,
            "step_results": [item.to_dict() for item in self.step_results],
            "step_count": self.step_count,
            "accepted_record_count": self.accepted_record_count,
            "rejected_record_count": self.rejected_record_count,
            "transition_ids": list(self.transition_ids),
            "final_state": to_json_compatible(self.final_state),
            "final_state_sha256": self.final_state_sha256,
            "authority_sequence": list(self.authority_sequence),
            "query_lifecycle_summary": to_json_compatible(self.query_lifecycle_summary),
            "lease_lifecycle_summary": to_json_compatible(self.lease_lifecycle_summary),
            "audit_records": to_json_compatible(self.audit_records),
            "processing_result_sequence": list(self.processing_result_sequence),
            "audit_reason_sequence": list(self.audit_reason_sequence),
            "invariant_violation_count": self.invariant_violation_count,
            "partial_mutation_count": self.partial_mutation_count,
            "second_query_count": self.second_query_count,
            "stale_unknown_act_count": self.stale_unknown_act_count,
            "low_level_output_count": self.low_level_output_count,
            "m3_control_write_delta": self.m3_control_write_delta,
            "model_forward_delta": self.model_forward_delta,
            "planner_call_delta": self.planner_call_delta,
            "pid_call_delta": self.pid_call_delta,
            "terminal_disposition": self.terminal_disposition,
            "reducer_call_count": self.reducer_call_count,
        }

    @classmethod
    def from_replay_trace(cls, envelope: ShadowInputEnvelope,
                          trace: ReplayTrace) -> "ShadowTrace":
        if not isinstance(envelope, ShadowInputEnvelope):
            raise TypeError("envelope must be ShadowInputEnvelope")
        if not isinstance(trace, ReplayTrace):
            raise TypeError("trace must be ReplayTrace")
        trace_value = trace.to_dict()
        supplied_trace_sha = trace_value.pop("trace_sha256")
        if canonical_sha256(trace_value) != supplied_trace_sha:
            raise ValueError("replay trace hash mismatch")
        if trace.episode_id != envelope.episode.episode_id:
            raise ValueError("replay trace episode mismatch")
        if trace.episode_records_sha256 != envelope.episode.records_sha256:
            raise ValueError("replay trace records mismatch")
        step_results = tuple(ShadowStepResult.from_replay_step(item)
                             for item in trace.step_results)
        audit_records = tuple(
            to_json_compatible(audit)
            for step in trace.step_results
            for audit in step.audit_records
        )
        initial_forward_count = envelope.episode.initial_state.get("model_forward_count")
        final_forward_count = trace.final_state.get("model_forward_count")
        if type(initial_forward_count) is not int or type(final_forward_count) is not int:
            raise ValueError("model forward counters must be integers")
        trace_id = canonical_sha256([
            SHADOW_TRACE_SCHEMA, envelope.envelope_id, trace.trace_id,
            trace.trace_sha256])
        values = {
            "schema_version": SHADOW_TRACE_SCHEMA,
            "shadow_trace_id": trace_id,
            "envelope_id": envelope.envelope_id,
            "episode_id": trace.episode_id,
            "episode_records_sha256": trace.episode_records_sha256,
            "replay_trace_id": trace.trace_id,
            "replay_trace_sha256": trace.trace_sha256,
            "step_results": step_results,
            "step_count": trace.step_count,
            "accepted_record_count": trace.accepted_record_count,
            "rejected_record_count": trace.rejected_record_count,
            "transition_ids": trace.transition_ids,
            "final_state": to_json_compatible(trace.final_state),
            "final_state_sha256": trace.final_state_sha256,
            "authority_sequence": trace.authority_sequence,
            "query_lifecycle_summary": to_json_compatible(trace.query_lifecycle_summary),
            "lease_lifecycle_summary": to_json_compatible(trace.lease_lifecycle_summary),
            "audit_records": audit_records,
            "processing_result_sequence": trace.processing_result_sequence,
            "audit_reason_sequence": trace.audit_reason_sequence,
            "invariant_violation_count": trace.invariant_violation_count,
            "partial_mutation_count": trace.partial_mutation_count,
            "second_query_count": trace.second_query_count,
            "stale_unknown_act_count": trace.stale_unknown_act_count,
            "low_level_output_count": trace.low_level_control_output_count,
            "m3_control_write_delta": 0,
            "model_forward_delta": final_forward_count - initial_forward_count,
            "planner_call_delta": 0,
            "pid_call_delta": 0,
            "terminal_disposition": trace.terminal_disposition,
            "reducer_call_count": trace.reducer_call_count,
        }
        projection = {
            **values,
            "step_results": [item.to_dict() for item in step_results],
        }
        return cls(**values, shadow_trace_sha256=canonical_sha256(projection))

    def to_dict(self) -> dict[str, Any]:
        return {**self._hash_projection(), "shadow_trace_sha256": self.shadow_trace_sha256}


@dataclass(frozen=True, **_DATACLASS_SLOT_KWARGS)
class ShadowBridgeRejection:
    """Fail-closed result containing no partial shadow trace."""

    schema_version: str
    envelope_id: str | None
    episode_id: str | None
    category: str
    detail_code: str
    reducer_call_count: int
    partial_trace: bool
    rejection_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != SHADOW_REJECTION_SCHEMA:
            raise ValueError("shadow rejection schema mismatch")
        if self.envelope_id is not None:
            require_sha256(self.envelope_id, "envelope_id")
        _optional_string(self.episode_id, "episode_id")
        _nonempty_string(self.category, "category")
        _nonempty_string(self.detail_code, "detail_code")
        _nonnegative_integer(self.reducer_call_count, "reducer_call_count")
        if self.partial_trace is not False:
            raise ValueError("shadow rejection must not contain a partial trace")
        require_sha256(self.rejection_sha256, "rejection_sha256")
        if self.rejection_sha256 != canonical_sha256(self._hash_projection()):
            raise ValueError("shadow rejection hash mismatch")

    def _hash_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "envelope_id": self.envelope_id,
            "episode_id": self.episode_id,
            "category": self.category,
            "detail_code": self.detail_code,
            "reducer_call_count": self.reducer_call_count,
            "partial_trace": self.partial_trace,
        }

    @classmethod
    def create(cls, *, envelope_id: str | None, episode_id: str | None,
               category: str, detail_code: str,
               reducer_call_count: int = 0) -> "ShadowBridgeRejection":
        values = {
            "schema_version": SHADOW_REJECTION_SCHEMA,
            "envelope_id": envelope_id,
            "episode_id": episode_id,
            "category": category,
            "detail_code": detail_code,
            "reducer_call_count": reducer_call_count,
            "partial_trace": False,
        }
        return cls(**values, rejection_sha256=canonical_sha256(values))

    def to_dict(self) -> dict[str, Any]:
        return {**self._hash_projection(), "rejection_sha256": self.rejection_sha256}
