"""Deterministic CPU-only replay runner over the existing minimal-core reducer."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import replace
from typing import Any, Mapping

from driveclarify_m3_minimal_core import (
    CORE_ID, MinimalM3Event, MinimalM3State, ProcessingResult, canonical_sha256,
    check_invariants, reduce_event, reduce_event_group,
)

from .adapters import DEFAULT_ADAPTER_REGISTRY, Adapter
from .contracts import (
    AdapterRejection, EpisodeRejection, ReplayEpisode, ReplayRecord,
    ReplayStepResult, ReplayTrace, SourceTier, TimeMappingMode,
)


RUNNER_VERSION = "driveclarify.m3.offline-replay.runner.v1"
MINIMAL_CORE_PACKAGE_SHA256 = "383004ed95d5b180b59380b3ff18c5381d28ca8f43b263a2430e7f6012a0968f"
FORBIDDEN_SOURCE_TOKENS = (
    "DC-M2B-BLIND-R3-EXEC-20260804T154418Z", "R3_BLIND", "R3 BLIND",
    "FORMAL_M1_TEST", "FORMAL M1 TEST", "BLIND_GOLD", "SEALED_GOLD",
)
FORBIDDEN_SOURCE_HASHES = frozenset({
    "fa9daa275ab165916d10e6ce6f4481891cc39a60e7069f7940f8ddea2beab4f6",
    "458a43cc3f5eb8e2861585972d6817c503f8e38f373373e54a8ad9834ba868a2",
    "416e4c65c8d10ebd519e1d0583584fc90fe7f830fbf01bfa09b9f5b6252f62b3",
})


def _reject(value: Any, category: str, detail: str) -> EpisodeRejection:
    episode_id = getattr(value, "episode_id", None)
    if episode_id is None and isinstance(value, Mapping):
        episode_id = value.get("episode_id")
    return EpisodeRejection(episode_id, category, detail)


def _validate_provenance(episode: ReplayEpisode) -> None:
    if not episode.non_blind_confirmation:
        raise ValueError("NONBLIND_CONFIRMATION_MISSING")
    if not episode.anti_overfitting_confirmation:
        raise ValueError("ANTI_OVERFITTING_CONFIRMATION_MISSING")
    if episode.source_tier not in {
            SourceTier.TIER_A_FROZEN_CONFORMANCE,
            SourceTier.TIER_B_NONBLIND_SYNTHETIC_DEV,
            SourceTier.TIER_C_RECORDED_NONBLIND_TRAIN_DEV,
            SourceTier.TIER_D_NONBLIND_SCHEDULED_LIFECYCLE_DEV}:
        raise ValueError("UNKNOWN_SOURCE_TIER")
    identity_fields = (
        episode.source_artifact_id,
        episode.source_provenance.get("source_identity", ""),
        episode.source_provenance.get("origin", ""),
    )
    serialized = " ".join(str(item) for item in identity_fields).upper()
    if any(token in serialized for token in FORBIDDEN_SOURCE_TOKENS):
        raise ValueError("R3_OR_FORMAL_TEST_PROVENANCE")
    if (episode.source_artifact_sha256 in FORBIDDEN_SOURCE_HASHES or
            any(item in str(value).lower() for item in FORBIDDEN_SOURCE_HASHES
                for value in episode.source_provenance.values())):
        raise ValueError("R3_OR_FORMAL_TEST_PROVENANCE")
    verified = episode.source_provenance.get("verified_source_artifact_sha256")
    if verified != episode.source_artifact_sha256:
        raise ValueError("SOURCE_ARTIFACT_SHA_MISMATCH")
    if episode.source_provenance.get("non_blind") is not True:
        raise ValueError("NONBLIND_CONFIRMATION_MISSING")
    if episode.source_provenance.get("r3_excluded") is not True:
        raise ValueError("R3_OR_FORMAL_TEST_PROVENANCE")
    if episode.source_provenance.get("formal_m1_test_excluded") is not True:
        raise ValueError("R3_OR_FORMAL_TEST_PROVENANCE")


def _mapped_records(episode: ReplayEpisode) -> tuple[ReplayRecord, ...]:
    mapped: list[ReplayRecord] = []
    prior: int | float | None = None
    first_source = (episode.records[0].source_monotonic_time
                    if episode.records else episode.episode_start_source_time)
    if episode.time_mapping_mode is TimeMappingMode.PRESERVE_RELATIVE_MONOTONIC_DELTAS:
        if not episode.records or episode.episode_start_source_time != first_source:
            raise ValueError("PRESERVE_SOURCE_ANCHOR_MISMATCH")
    for record in episode.records:
        if episode.time_mapping_mode is TimeMappingMode.PRESERVE_RELATIVE_MONOTONIC_DELTAS:
            if record.source_monotonic_time is None:
                raise ValueError("MISSING_SOURCE_TIME")
            if record.replay_monotonic_time is not None:
                raise ValueError("PRESERVE_MODE_REPLAY_TIME_MUST_BE_NULL")
            delta = record.source_monotonic_time - episode.episode_start_source_time
            if delta < 0:
                raise ValueError("NEGATIVE_SOURCE_DELTA")
            effective = episode.episode_start_replay_monotonic_time + delta
        else:
            if record.replay_monotonic_time is None:
                raise ValueError("MISSING_EXPLICIT_REPLAY_TIME")
            effective = record.replay_monotonic_time
        if prior is not None and effective < prior:
            raise ValueError("REPLAY_TIME_REGRESSION")
        prior = effective
        mapped.append(replace(record, replay_monotonic_time=effective,
                              source_record_sha256=canonical_sha256({
                                  **record._hash_projection(),
                                  "replay_monotonic_time": effective,
                              })))
    groups: dict[str, list[ReplayRecord]] = {}
    for record in mapped:
        if record.concurrent_group_id is not None:
            groups.setdefault(record.concurrent_group_id, []).append(record)
    for identity, records in groups.items():
        indices = [item.sequence_index for item in records]
        times = [item.replay_monotonic_time for item in records]
        if indices != list(range(min(indices), max(indices) + 1)) or len(set(times)) != 1:
            raise ValueError("INCONSISTENT_CONCURRENT_GROUP:" + identity)
    return tuple(mapped)


def _lease(state: MinimalM3State) -> dict[str, Any] | None:
    return None if state.holding_lease is None else state.holding_lease.to_dict()


def _business(state: MinimalM3State) -> dict[str, Any]:
    return {key: value for key, value in state.to_dict().items()
            if key not in {"audit_log", "audit_log_digest", "last_event_sequence",
                           "processed_idempotency_keys"}}


def _adapter_output_violation(record: ReplayRecord,
                              event: MinimalM3Event) -> str | None:
    if (event.event_id != record.record_id or
            event.query_episode_id != record.query_episode_id or
            event.source_component != record.source_component or
            event.observed_monotonic_time != record.replay_monotonic_time):
        return "IDENTITY_REWRITE_DETECTED"
    expected_payload = (record.payload.get("event_payload")
                        if record.record_type == "M2B_DECISION" else record.payload)
    if canonical_sha256(event.payload) != canonical_sha256(expected_payload):
        return "PAYLOAD_OR_EVIDENCE_REWRITE_DETECTED"
    if record.record_type != "M2B_DECISION":
        return None
    expected = {
        "ACT": "DECISION_ACT", "ASK": "DECISION_ASK",
        "WAIT": "DECISION_WAIT", "FALLBACK": "DECISION_FALLBACK",
    }
    try:
        action = record.payload["decision"]["selected_action"]
        return (None if event.event_type.value == expected[action]
                else "ACTION_REWRITE_DETECTED")
    except (KeyError, TypeError):
        return "ACTION_REWRITE_DETECTED"


def _step_values(episode_id: str, record: ReplayRecord, before: MinimalM3State,
                 after: MinimalM3State, event: MinimalM3Event | None,
                 adapter_result: str, processing: str,
                 transitions: tuple[str, ...], audit: tuple[Mapping[str, Any], ...],
                 business_mutation: bool, control_count: int) -> ReplayStepResult:
    return ReplayStepResult.create(
        episode_id=episode_id, record_id=record.record_id,
        sequence_index=record.sequence_index,
        pre_state_sha256=canonical_sha256(before.to_dict()),
        adapted_event_sha256=(None if event is None else canonical_sha256(event.to_dict())),
        adapter_result=adapter_result, reduction_processing_result=processing,
        executed_transition_ids=transitions,
        post_state_sha256=canonical_sha256(after.to_dict()),
        authority_before=before.authority.value, authority_after=after.authority.value,
        query_active_before=before.query_active, query_active_after=after.query_active,
        candidate_freshness_before=before.candidate_freshness,
        candidate_freshness_after=after.candidate_freshness,
        lease_status_before=_lease(before), lease_status_after=_lease(after),
        audit_records=audit, business_mutation=business_mutation,
        low_level_control_output_count=control_count)


def run_episode(episode: ReplayEpisode | Mapping[str, Any],
                adapter_registry: Mapping[tuple[str, str], Adapter] | None = None
                ) -> ReplayTrace | EpisodeRejection:
    """Validate, deterministically execute, and publish an immutable trace."""
    try:
        normalized = episode if isinstance(episode, ReplayEpisode) else ReplayEpisode.from_dict(episode)
        _validate_provenance(normalized)
        if normalized.runner_visible_oracle_fields:
            raise ValueError("ORACLE_LEAKAGE")
        state = MinimalM3State.from_dict(normalized.initial_state)
        if check_invariants(state):
            raise ValueError("INVALID_INITIAL_STATE")
        if state.model_forward_count != 0 or state.low_level_control_outputs:
            raise ValueError("FORBIDDEN_INITIAL_SIDE_EFFECT")
        records = _mapped_records(normalized)
    except (KeyError, TypeError, ValueError) as exc:
        return _reject(episode, "REJECT_BEFORE_START", str(exc))

    registry = DEFAULT_ADAPTER_REGISTRY if adapter_registry is None else adapter_registry
    steps: list[ReplayStepResult] = []
    transitions: list[str] = []
    processing_sequence: list[str] = []
    audit_sequence: list[str] = []
    authority_sequence = [state.authority.value]
    reducer_calls = 0
    partial_mutations = 0
    invariant_violations = 0
    low_level_outputs = 0
    terminal = "COMPLETE"
    cursor = 0
    while cursor < len(records):
        effective = records[cursor].replay_monotonic_time
        end = cursor + 1
        while end < len(records) and records[end].replay_monotonic_time == effective:
            end += 1
        bucket = records[cursor:end]
        bucket_before = state
        adapted: list[tuple[ReplayRecord, MinimalM3Event]] = []
        for record in bucket:
            adapter = registry.get((record.adapter_id, record.adapter_version))
            adapted_value = (AdapterRejection(
                record.record_id, record.adapter_id, record.adapter_version,
                "ADAPTER_REGISTRY_VERSION_MISSING", "UNREGISTERED_ADAPTER_VERSION")
                if adapter is None else adapter(record, state))
            if isinstance(adapted_value, AdapterRejection):
                audit = ({"result": adapted_value.category,
                          "detail_code": adapted_value.detail_code,
                          "record_id": record.record_id,
                          "mutation_applied": False},)
                steps.append(_step_values(
                    normalized.episode_id, record, state, state, None,
                    "STRUCTURED_ADAPTER_REJECTION", "NOT_CALLED_ADAPTER_REJECTION",
                    (), audit, False, 0))
                processing_sequence.append("NOT_CALLED_ADAPTER_REJECTION")
                audit_sequence.append(adapted_value.category)
            else:
                output_violation = _adapter_output_violation(record, adapted_value)
                if output_violation is not None:
                    rejection = AdapterRejection(
                        record.record_id, record.adapter_id, record.adapter_version,
                        output_violation, "ADAPTER_OUTPUT_MISMATCH")
                    audit = ({"result": rejection.category,
                              "detail_code": rejection.detail_code,
                              "record_id": record.record_id,
                              "mutation_applied": False},)
                    steps.append(_step_values(
                        normalized.episode_id, record, state, state, None,
                        "STRUCTURED_ADAPTER_REJECTION", "NOT_CALLED_ADAPTER_REJECTION",
                        (), audit, False, 0))
                    processing_sequence.append("NOT_CALLED_ADAPTER_REJECTION")
                    audit_sequence.append(rejection.category)
                else:
                    adapted.append((record, adapted_value))
        if adapted:
            reducer_calls += 1
            result = (reduce_event(state, adapted[0][1], effective)
                      if len(adapted) == 1 else
                      reduce_event_group(state, tuple(item[1] for item in adapted), effective))
            state = result.state
            low_level_outputs += len(result.low_level_control_outputs) + len(
                state.low_level_control_outputs)
            by_event = defaultdict(deque)
            for index, trace in enumerate(result.traces):
                by_event[trace.audit.event_id].append(
                    (trace, result.processing_results[index]))
            transitions.extend(result.transition_ids)
            processing_sequence.extend(item.value for item in result.processing_results)
            audit_sequence.extend(trace.audit.result.value for trace in result.traces)
            for record, event in adapted:
                trace, processing = by_event[record.record_id].popleft()
                rejected = processing is not ProcessingResult.PROCESSED
                # The frozen core records an ineligible event's idempotency key.
                # Replay treats that key as rejection bookkeeping, not business
                # lifecycle mutation; invariant/malformed atomicity still gates.
                partial = (rejected and not trace.business_state_unchanged and
                           processing in {ProcessingResult.REJECTED_INVARIANT,
                                          ProcessingResult.REJECTED_MALFORMED_EVENT,
                                          ProcessingResult.REJECTED_INVALID_TEMPORAL_NUMERIC_DOMAIN})
                partial_mutations += int(partial)
                invariant_violations += len(trace.invariants)
                step = _step_values(
                    normalized.episode_id, record, bucket_before, state, event,
                    "ADAPTED", processing.value,
                    (() if trace.transition_id is None else (trace.transition_id,)),
                    (trace.audit.to_dict(),), trace.mutation_applied,
                    len(result.low_level_control_outputs))
                steps.append(step)
            if partial_mutations or invariant_violations or low_level_outputs:
                terminal = "TERMINATED_INTEGRITY_FAILURE"
                break
        authority_sequence.append(state.authority.value)
        cursor = end

    steps.sort(key=lambda item: item.sequence_index)
    accepted = sum(item.reduction_processing_result == ProcessingResult.PROCESSED.value
                   for item in steps)
    rejected = len(steps) - accepted
    first_rejection = next((item.sequence_index for item in steps
                            if item.reduction_processing_result != ProcessingResult.PROCESSED.value), None)
    successful_query_creations = sum(
        transition == "MC-T004" for transition in transitions)
    second_query = max(0, successful_query_creations - 1)
    stale_act = sum(
        item.candidate_freshness_before in {"STALE", "UNKNOWN"} and
        "MC-T002" in item.executed_transition_ids for item in steps)
    final_state = state.to_dict()
    adapter_versions = tuple(sorted({record.adapter_id + "@" + record.adapter_version
                                     for record in records}))
    source_binding = canonical_sha256({
        "episode_id": normalized.episode_id,
        "source_artifact_sha256": normalized.source_artifact_sha256,
        "records_sha256": normalized.records_sha256,
    })
    trace_id = canonical_sha256([
        normalized.episode_id, normalized.records_sha256, RUNNER_VERSION,
        adapter_versions, MINIMAL_CORE_PACKAGE_SHA256,
    ])
    return ReplayTrace.create(
        trace_id=trace_id, episode_id=normalized.episode_id,
        episode_records_sha256=normalized.records_sha256,
        runner_version=RUNNER_VERSION, adapter_versions=adapter_versions,
        minimal_core_package_sha256=MINIMAL_CORE_PACKAGE_SHA256,
        initial_state_sha256=normalized.initial_state_sha256,
        step_results=tuple(steps), step_count=len(steps),
        accepted_record_count=accepted, rejected_record_count=rejected,
        transition_ids=tuple(transitions), final_state=final_state,
        final_state_sha256=canonical_sha256(final_state),
        authority_sequence=tuple(authority_sequence),
        query_lifecycle_summary={"final_active": state.query_active,
                                 "final_episode_id": state.query_episode_id,
                                 "successful_query_creations": successful_query_creations},
        lease_lifecycle_summary={"final_lease": _lease(state),
                                 "holding_authority": state.authority.value ==
                                 "M3_HOLDING_CONTROL"},
        invariant_violation_count=invariant_violations,
        partial_mutation_count=partial_mutations,
        second_query_count=second_query, stale_unknown_act_count=stale_act,
        low_level_control_output_count=low_level_outputs,
        time_mapping_mode=normalized.time_mapping_mode.value,
        ordered_record_ids=tuple(item.record_id for item in records),
        processing_result_sequence=tuple(processing_sequence),
        audit_reason_sequence=tuple(audit_sequence),
        first_rejection_index=first_rejection,
        terminal_disposition=terminal, source_binding_sha256=source_binding,
        reducer_call_count=reducer_calls)
