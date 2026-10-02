#!/usr/bin/env python3
"""Execute the frozen recorded, lifecycle, and no-control shadow campaigns."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_m3_minimal_core import ControlAuthority, MinimalM3State, check_invariants
from driveclarify_m3_offline_replay import (
    EpisodeRejection, ReplayTrace, evaluate_trace, run_episode,
    strict_json_dumps, strict_json_loads,
)
from driveclarify_m3_offline_replay.lifecycle_campaign import (
    build_lifecycle_dev_schedule_set, build_recorded_decision_episode,
    compile_lifecycle_schedule_set,
)
from driveclarify_m3_offline_replay.recorded_capture import (
    LifecycleAwareM2BDecisionRecordV1, admit_lifecycle_aware_record,
)
from driveclarify_m3_shadow_bridge import (
    M3ShadowBridge, ShadowBridgeRejection, ShadowInputEnvelope,
)


ARTIFACT_SCHEMA = "driveclarify.m3.lifecycle-milestone-artifacts.v1"


def canonical_bytes(value: Any) -> bytes:
    return strict_json_dumps(value).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_canonical(path: Path, value: Any) -> dict[str, Any]:
    raw = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return {"path": str(path), "bytes": len(raw), "sha256": sha256_bytes(raw)}


def load_strict(path: Path) -> Any:
    return strict_json_loads(path.read_text(encoding="utf-8"))


def _counter(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _trace_dict(trace: ReplayTrace) -> dict[str, Any]:
    if isinstance(trace, EpisodeRejection):
        raise RuntimeError("valid frozen episode was rejected: " + trace.detail)
    return trace.to_dict()


def _double_run(episodes: Sequence[Any]) -> tuple[tuple[ReplayTrace, ...], dict[str, Any]]:
    first = tuple(run_episode(episode) for episode in episodes)
    second = tuple(run_episode(episode) for episode in episodes)
    if any(isinstance(item, EpisodeRejection) for item in (*first, *second)):
        rejected = [item.to_dict() for item in (*first, *second)
                    if isinstance(item, EpisodeRejection)]
        raise RuntimeError("episode rejection in frozen campaign: " + str(rejected[:2]))
    first_raw = canonical_bytes([item.to_dict() for item in first])
    second_raw = canonical_bytes([item.to_dict() for item in second])
    return first, {
        "status": "PASS" if first_raw == second_raw else "FAIL",
        "trace_byte_equality": first_raw == second_raw,
        "trace_sha_equality": sha256_bytes(first_raw) == sha256_bytes(second_raw),
        "first_bytes": len(first_raw),
        "second_bytes": len(second_raw),
        "first_sha256": sha256_bytes(first_raw),
        "second_sha256": sha256_bytes(second_raw),
    }


def _final_state_failures(trace: ReplayTrace) -> tuple[str, ...]:
    return check_invariants(MinimalM3State.from_dict(trace.final_state))


def _common_invariants(episodes: Sequence[Any], traces: Sequence[ReplayTrace]) -> dict[str, int]:
    silent_drop = sum(episode.source_record_count - trace.step_count
                      for episode, trace in zip(episodes, traces))
    old_candidate_act = 0
    safety_downgrade = 0
    fallback_bypass = 0
    evidence_bypass = 0
    duplicate_second_mutation = 0
    for episode, trace in zip(episodes, traces):
        record_by_id = {record.record_id: record for record in episode.records}
        safety_seen = bool(episode.initial_state["safety_guard_active"])
        if (safety_seen and episode.initial_state["authority"] !=
                ControlAuthority.INDEPENDENT_SAFETY_GUARD.value):
            safety_downgrade += 1
        for step in trace.step_results:
            record = record_by_id[step.record_id]
            decision = (record.payload.get("decision")
                        if record.record_type == "M2B_DECISION" else None)
            action = decision.get("selected_action") if decision else None
            if action == "ACT" and "MC-T002" in step.executed_transition_ids:
                event_payload = record.payload.get("event_payload", {})
                old_candidate_act += int(
                    decision.get("candidate_set_id") != record.candidate_set_id or
                    event_payload.get("candidate_set_id") != record.candidate_set_id)
            if (step.reduction_processing_result == "REJECTED_DUPLICATE" and
                    step.business_mutation):
                duplicate_second_mutation += 1
            if record.record_type == "SAFETY_PREEMPTION":
                safety_seen = True
            if (safety_seen and step.authority_after !=
                    ControlAuthority.INDEPENDENT_SAFETY_GUARD.value):
                safety_downgrade += 1
            if action == "FALLBACK":
                initial = episode.initial_state
                expected = (
                    ControlAuthority.INDEPENDENT_SAFETY_GUARD.value
                    if initial["safety_guard_active"] else
                    ControlAuthority.BASELINE_CONTROL.value
                    if initial["baseline_authority_eligible"] else
                    ControlAuthority.NO_M3_CONTROL_AUTHORITY.value)
                fallback_bypass += int(step.authority_after != expected)
            if step.authority_after == ControlAuthority.M3_HOLDING_CONTROL.value:
                lease = step.lease_status_after
                evidence_bypass += int(
                    lease is None or lease.get("evidence_grade") !=
                    "VERIFIED_FROM_CONTROLLED_PROBE")
    return {
        "invariant_violation_count": sum(
            trace.invariant_violation_count for trace in traces),
        "partial_mutation_count": sum(trace.partial_mutation_count for trace in traces),
        "silent_record_drop_count": silent_drop,
        "second_query_count": sum(trace.second_query_count for trace in traces),
        "stale_unknown_act_count": sum(trace.stale_unknown_act_count for trace in traces),
        "old_candidate_act_count": old_candidate_act,
        "safety_downgrade_count": safety_downgrade,
        "fallback_bypass_count": fallback_bypass,
        "evidence_bypass_count": evidence_bypass,
        "duplicate_second_mutation_count": duplicate_second_mutation,
        "low_level_control_output_count": sum(
            trace.low_level_control_output_count for trace in traces),
        "final_state_invariant_failure_count": sum(
            len(_final_state_failures(trace)) for trace in traces),
    }


def _recorded_results(records: Sequence[Any], episodes: Sequence[Any],
                      traces: Sequence[ReplayTrace], determinism: Mapping[str, Any],
                      trace_artifact: Mapping[str, Any]) -> dict[str, Any]:
    rows = [record.to_dict() for record in records]
    action_by_episode = {
        episode.episode_id: row["selected_action_canonical"]
        for row, episode in zip(rows, episodes)
    }
    transitions = Counter(transition for trace in traces
                          for transition in trace.transition_ids)
    action_transitions: dict[str, Counter[str]] = {
        action: Counter() for action in ("ACT", "ASK", "WAIT", "FALLBACK")}
    for trace in traces:
        action_transitions[action_by_episode[trace.episode_id]].update(trace.transition_ids)
    accepted = sum(trace.accepted_record_count for trace in traces)
    rejected = sum(trace.rejected_record_count for trace in traces)
    wait_traces = [trace for trace in traces
                   if action_by_episode[trace.episode_id] == "WAIT"]
    rewrite = sum(
        episode.records[0].payload["decision"]["selected_action"] !=
        row["selected_action_canonical"]
        for row, episode in zip(rows, episodes))
    query_rewrite = sum(
        episode.records[0].payload["decision"]["query_episode_id"] !=
        row["query_episode_id"]
        for row, episode in zip(rows, episodes))
    candidate_fabrication = sum(
        episode.records[0].candidate_set_id != row["candidate_set_id"]
        for row, episode in zip(rows, episodes))
    lease_fabrication = sum(
        episode.records[0].payload["decision"]["holding_lease_payload"] !=
        row["holding_lease"]
        for row, episode in zip(rows, episodes))
    evidence_fabrication = sum(
        episode.records[0].payload["decision"]["evidence_grade"] !=
        (row["holding_evidence_grade"] if row["selected_action_canonical"] == "WAIT"
         else row["decision_evidence_grade"])
        for row, episode in zip(rows, episodes))
    initial_states = Counter(episode.initial_state["lifecycle_state"] for episode in episodes)
    final_states = Counter(trace.final_state["lifecycle_state"] for trace in traces)
    invariants = _common_invariants(episodes, traces)
    return {
        "schema_version": "driveclarify.m3.recorded-decision-replay-results.v1",
        "episode_class": "RECORDED_DECISION_ONLY",
        "episode_count": len(episodes),
        "step_count": sum(trace.step_count for trace in traces),
        "accepted_record_count": accepted,
        "rejected_record_count": rejected,
        "source_action_raw_distribution": _counter(
            row["selected_action_raw"] for row in rows),
        "source_action_canonical_distribution": _counter(
            row["selected_action_canonical"] for row in rows),
        "transition_coverage": dict(sorted(transitions.items())),
        "state_coverage": {
            "initial": dict(sorted(initial_states.items())),
            "final": dict(sorted(final_states.items())),
        },
        "action_specific_transition_coverage": {
            action: dict(sorted(counts.items()))
            for action, counts in action_transitions.items()},
        "authority_outcomes": _counter(
            trace.final_state["authority"] for trace in traces),
        "structured_rejection_categories": _counter(
            result for trace in traces
            for result in trace.processing_result_sequence
            if result != "PROCESSED"),
        "wait": {
            "source_count": len(wait_traces),
            "accepted_count": sum(trace.accepted_record_count for trace in wait_traces),
            "rejected_count": sum(trace.rejected_record_count for trace in wait_traces),
            "not_currently_available_evidence_count": sum(
                row["selected_action_canonical"] == "WAIT" and
                row["holding_evidence_grade"] == "NOT_CURRENTLY_AVAILABLE"
                for row in rows),
            "missing_lease_count": sum(
                row["selected_action_canonical"] == "WAIT" and
                row["holding_lease"] is None for row in rows),
        },
        "action_rewrite_count": rewrite,
        "evidence_fabrication_count": evidence_fabrication,
        "lease_fabrication_count": lease_fabrication,
        "candidate_fabrication_count": candidate_fabrication,
        "query_identity_rewrite_count": query_rewrite,
        "invariants": invariants,
        "determinism": dict(determinism),
        "trace_artifact": dict(trace_artifact),
        "status": ("PASS" if accepted + rejected == len(rows) and
                   determinism["status"] == "PASS" and
                   rewrite == evidence_fabrication == lease_fabrication ==
                   candidate_fabrication == query_rewrite == 0 and
                   all(value == 0 for value in invariants.values())
                   else "FAIL"),
    }


def _operation_count(schedules: Sequence[Mapping[str, Any]], kind: str) -> int:
    return sum(any(operation["kind"] == kind for operation in row["operations"])
               for row in schedules)


def _lifecycle_results(schedule_set: Mapping[str, Any], captured_records: Sequence[Any],
                       episodes: Sequence[Any], oracles: Sequence[Any],
                       traces: Sequence[ReplayTrace], determinism: Mapping[str, Any],
                       artifacts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    evaluations = [evaluate_trace(trace, oracle)
                   for trace, oracle in zip(traces, oracles)]
    schedules = schedule_set["schedules"]
    captured_by_id = {record.record_id: record.to_dict()
                      for record in captured_records}
    transitions = Counter(transition for trace in traces
                          for transition in trace.transition_ids)
    initial_states = Counter(episode.initial_state["lifecycle_state"] for episode in episodes)
    final_states = Counter(trace.final_state["lifecycle_state"] for trace in traces)
    invariants = _common_invariants(episodes, traces)
    integrity = Counter()
    per_case = []
    synthetic_case_ids = []
    for schedule, episode, trace, evaluation in zip(
            schedules, episodes, traces, evaluations):
        source = captured_by_id[schedule["seed_record_id"]]
        if schedule["synthetic_verified_abstract_lease"]:
            synthetic_case_ids.append(schedule["case_number"])
        integrity["source_binding_mismatch_count"] += int(
            schedule["seed_record_sha256"] != source["record_sha256"] or
            schedule["seed_selected_action_raw"] != source["selected_action_raw"] or
            schedule["seed_selected_action_canonical"] !=
            source["selected_action_canonical"])
        first_decision_seen = False
        for replay_record in episode.records:
            payload = replay_record.payload
            event_payload = (payload.get("event_payload", {})
                             if replay_record.record_type == "M2B_DECISION"
                             else payload)
            integrity["candidate_fabrication_count"] += int(
                replay_record.candidate_set_id != source["candidate_set_id"] or
                event_payload.get("candidate_set_id") != source["candidate_set_id"])
            integrity["authority_eligibility_rewrite_count"] += int(
                event_payload.get("baseline_authority_eligible") !=
                episode.initial_state["baseline_authority_eligible"])
            if not schedule["synthetic_verified_abstract_lease"]:
                integrity["evidence_fabrication_count"] += int(
                    event_payload.get("act_evidence_grade") !=
                    source["decision_evidence_grade"] or
                    event_payload.get("holding_evidence_grade") !=
                    source["holding_evidence_grade"])
                integrity["lease_fabrication_count"] += int(
                    event_payload.get("lease") != source["holding_lease"])
            if replay_record.record_type == "M2B_DECISION":
                decision = payload["decision"]
                integrity["action_rewrite_count"] += int(
                    decision["selected_action"] !=
                    source["selected_action_canonical"])
                if not schedule["synthetic_verified_abstract_lease"]:
                    expected_evidence = (
                        source["holding_evidence_grade"]
                        if decision["selected_action"] == "WAIT" else
                        source["decision_evidence_grade"])
                    integrity["decision_evidence_fabrication_count"] += int(
                        decision["evidence_grade"] != expected_evidence)
                    integrity["decision_lease_fabrication_count"] += int(
                        decision["holding_lease_payload"] !=
                        source["holding_lease"])
                if not first_decision_seen:
                    integrity["query_identity_rewrite_count"] += int(
                        decision["query_episode_id"] != source["query_episode_id"])
                    first_decision_seen = True
        per_case.append({
            "case_number": schedule["case_number"],
            "case_name": schedule["case_name"],
            "family": schedule["family"],
            "seed_record_id": schedule["seed_record_id"],
            "seed_action": schedule["seed_selected_action_canonical"],
            "synthetic_verified_abstract_lease": schedule[
                "synthetic_verified_abstract_lease"],
            "step_count": trace.step_count,
            "accepted_step_count": trace.accepted_record_count,
            "rejected_step_count": trace.rejected_record_count,
            "transition_ids": list(trace.transition_ids),
            "processing_results": list(trace.processing_result_sequence),
            "final_lifecycle_state": trace.final_state["lifecycle_state"],
            "final_authority": trace.final_state["authority"],
            "final_query_active": trace.final_state["query_active"],
            "final_lease_present": trace.final_state["holding_lease"] is not None,
            "oracle_match": evaluation.accepted,
        })
    # The decision and event-envelope checks are separately counted, then
    # folded into the public evidence/lease totals without hiding either source.
    evidence_fabrication = (
        integrity["evidence_fabrication_count"] +
        integrity["decision_evidence_fabrication_count"])
    lease_fabrication = (
        integrity["lease_fabrication_count"] +
        integrity["decision_lease_fabrication_count"])
    case_11 = episodes[10]
    case_11_time_fidelity = (
        len(case_11.records) == 2 and
        case_11.records[1].replay_monotonic_time >
        case_11.records[0].replay_monotonic_time and
        case_11.records[1].payload["decision"]["decision_monotonic_time"] ==
        case_11.records[1].replay_monotonic_time)
    family_distribution = _counter(row["family"] for row in schedules)
    result = {
        "schema_version": "driveclarify.m3.lifecycle-dev-replay-results.v1",
        "schedule_class": schedule_set["schedule_class"],
        "schedule_count": schedule_set["schedule_count"],
        "schedule_set_sha256": schedule_set["schedule_set_sha256"],
        "episode_count": len(episodes),
        "execution_completed_count": len(traces),
        "step_count": sum(trace.step_count for trace in traces),
        "accepted_step_count": sum(trace.accepted_record_count for trace in traces),
        "rejected_step_count": sum(trace.rejected_record_count for trace in traces),
        "oracle_accepted_count": sum(item.accepted for item in evaluations),
        "oracle_rejected_count": sum(not item.accepted for item in evaluations),
        "family_distribution": family_distribution,
        "answer_case_count": _operation_count(schedules, "ANSWER"),
        "timeout_case_count": _operation_count(schedules, "TIMEOUT"),
        "cancel_case_count": _operation_count(schedules, "CANCEL"),
        "world_change_case_count": _operation_count(schedules, "WORLD_CHANGE"),
        "candidate_stale_case_count": _operation_count(schedules, "CANDIDATE_STALE"),
        "revalidation_case_count": sum(
            any(operation["kind"] in {"REVALIDATION_PASS", "REVALIDATION_FAIL"}
                for operation in row["operations"]) for row in schedules),
        "replan_case_count": _operation_count(schedules, "REPLAN_FRESH"),
        "wait_case_count": sum(row["family"] == "WAIT" for row in schedules),
        "safety_preemption_case_count": _operation_count(schedules, "SAFETY"),
        "complete_lifecycle_case_count": sum(
            trace.final_state["lifecycle_state"] == "RESUME_READY"
            for trace in traces),
        "synthetic_verified_abstract_lease_case_count": sum(
            row["synthetic_verified_abstract_lease"] for row in schedules),
        "synthetic_verified_abstract_lease_case_ids": synthetic_case_ids,
        "recorded_passenger_behavior_claim_count": sum(
            row["not_recorded_passenger_behavior"] is not True
            for row in schedules),
        "case_11_scheduled_time_fidelity": case_11_time_fidelity,
        "transition_coverage": dict(sorted(transitions.items())),
        "state_coverage": {
            "initial": dict(sorted(initial_states.items())),
            "final": dict(sorted(final_states.items())),
        },
        "structured_rejection_categories": _counter(
            value for trace in traces for value in trace.processing_result_sequence
            if value != "PROCESSED"),
        "action_rewrite_count": integrity["action_rewrite_count"],
        "evidence_fabrication_count": evidence_fabrication,
        "lease_fabrication_count": lease_fabrication,
        "candidate_fabrication_count": integrity[
            "candidate_fabrication_count"],
        "query_identity_rewrite_count": integrity[
            "query_identity_rewrite_count"],
        "source_binding_mismatch_count": integrity[
            "source_binding_mismatch_count"],
        "authority_eligibility_rewrite_count": integrity[
            "authority_eligibility_rewrite_count"],
        "oracle_leakage_count": sum(bool(episode.runner_visible_oracle_fields)
                                     for episode in episodes),
        "runner_expected_value_count": sum(
            len(row["runner_expected_values"]) for row in schedules),
        "schedule_readback_verified": True,
        "wait": {
            "case_count": sum(row["family"] == "WAIT" for row in schedules),
            "accepted_step_count": sum(
                trace.accepted_record_count for row, trace in zip(schedules, traces)
                if row["family"] == "WAIT"),
            "rejected_step_count": sum(
                trace.rejected_record_count for row, trace in zip(schedules, traces)
                if row["family"] == "WAIT"),
            "recorded_evidence_gap_case_ids": [21, 22],
            "synthetic_contract_only_case_ids": [23, 24, 25, 26],
        },
        "case_results": per_case,
        "invariants": invariants,
        "determinism": dict(determinism),
        "artifacts": {key: dict(value) for key, value in artifacts.items()},
    }
    result["status"] = (
        "PASS" if len(episodes) == len(traces) == len(oracles) == 32 and
        result["oracle_accepted_count"] == 32 and
        family_distribution == {
            "ACT_FALLBACK": 6, "ASK": 12,
            "REVALIDATION_REPLAN": 8, "WAIT": 6} and
        synthetic_case_ids == [23, 24, 25, 26] and
        result["recorded_passenger_behavior_claim_count"] == 0 and
        result["runner_expected_value_count"] == 0 and
        case_11_time_fidelity and
        all(integrity[name] == 0 for name in (
            "source_binding_mismatch_count", "action_rewrite_count",
            "evidence_fabrication_count", "decision_evidence_fabrication_count",
            "lease_fabrication_count", "decision_lease_fabrication_count",
            "candidate_fabrication_count", "query_identity_rewrite_count",
            "authority_eligibility_rewrite_count")) and
        determinism["status"] == "PASS" and
        result["oracle_leakage_count"] == 0 and
        all(value == 0 for value in invariants.values()) else "FAIL")
    return result


def _shadow_equivalence(episodes: Sequence[Any], replay_traces: Sequence[ReplayTrace],
                        shadows: Sequence[Any],
                        shadow_artifact: Mapping[str, Any]) -> dict[str, Any]:
    mismatch = Counter()
    for episode, replay, shadow in zip(episodes, replay_traces, shadows):
        mismatch["transition_sequence"] += shadow.transition_ids != replay.transition_ids
        mismatch["final_state"] += dict(shadow.final_state) != dict(replay.final_state)
        mismatch["authority"] += shadow.authority_sequence != replay.authority_sequence
        mismatch["query_state"] += dict(shadow.query_lifecycle_summary) != dict(
            replay.query_lifecycle_summary)
        mismatch["lease_state"] += dict(shadow.lease_lifecycle_summary) != dict(
            replay.lease_lifecycle_summary)
        mismatch["audit"] += shadow.audit_reason_sequence != replay.audit_reason_sequence
        mismatch["processing"] += (shadow.processing_result_sequence !=
                                    replay.processing_result_sequence)
        mismatch["trace_hash_mapping"] += shadow.replay_trace_sha256 != replay.trace_sha256
    delta_fields = ("m3_control_write_delta", "model_forward_delta",
                    "planner_call_delta", "pid_call_delta")
    deltas = {field: sum(int(getattr(shadow, field)) for shadow in shadows)
              for field in delta_fields}
    result = {
        "schema_version": "driveclarify.m3.shadow-bridge-results.v1",
        "shadow_bridge_created": True,
        "input_episode_count": len(episodes),
        "shadow_trace_count": len(shadows),
        "equivalence_mismatch_counts": dict(sorted(mismatch.items())),
        **deltas,
        "low_level_control_output_count": sum(
            shadow.low_level_output_count for shadow in shadows),
        "delta_measurement_provenance": {
            "m3_control_write_delta": (
                "REPLAY_TRACE_LOW_LEVEL_OUTPUT_COUNT_PLUS_NO_CONTROL_CALLBACK_CLOSURE"),
            "model_forward_delta": "MINIMAL_STATE_COUNTER_PRE_POST_DIFFERENCE",
            "planner_call_delta": "STATICALLY_UNREACHABLE_FROM_BRIDGE_CALL_GRAPH",
            "pid_call_delta": "STATICALLY_UNREACHABLE_FROM_BRIDGE_CALL_GRAPH",
        },
        "shadow_trace_artifact": dict(shadow_artifact),
        "production_integration": False,
        "live_control": False,
        "physical_holding": False,
    }
    result["status"] = (
        "PASS" if len(shadows) == len(episodes) and
        all(value == 0 for value in mismatch.values()) and
        all(value == 0 for value in deltas.values()) and
        result["low_level_control_output_count"] == 0 else "FAIL")
    return {"result": result, "shadows": tuple(shadows)}


def _dataset_manifest(dataset_path: Path, determinism_peer_path: Path,
                      dataset: Mapping[str, Any], records: Sequence[Any],
                      admissions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [record.to_dict() for record in records]
    admitted = sum(row.get("admitted") is True for row in admissions)
    exclusions = Counter(reason for row in admissions for reason in row.get("reasons", ()))
    first_raw = dataset_path.read_bytes()
    second_raw = determinism_peer_path.read_bytes()
    capture_determinism = {
        "status": "PASS" if first_raw == second_raw else "FAIL",
        "campaign_count": 2,
        "first_path": str(dataset_path),
        "second_path": str(determinism_peer_path),
        "first_bytes": len(first_raw),
        "second_bytes": len(second_raw),
        "first_sha256": sha256_bytes(first_raw),
        "second_sha256": sha256_bytes(second_raw),
        "byte_equality": first_raw == second_raw,
        "sha_equality": sha256_bytes(first_raw) == sha256_bytes(second_raw),
    }
    return {
        "schema_version": "driveclarify.m3.lifecycle-aware-dataset-manifest.v1",
        "dataset_path": str(dataset_path),
        "dataset_bytes": dataset_path.stat().st_size,
        "dataset_sha256": file_sha256(dataset_path),
        "record_count": len(rows),
        "admitted_record_count": admitted,
        "excluded_record_count": len(rows) - admitted,
        "exclusion_reason_counts": dict(sorted(exclusions.items())),
        "split_distribution": _counter(row["split"] for row in rows),
        "raw_action_distribution": _counter(row["selected_action_raw"] for row in rows),
        "canonical_action_distribution": _counter(
            row["selected_action_canonical"] for row in rows),
        "frame_identity_kind_distribution": _counter(
            row["frame_identity_kind"] for row in rows),
        "candidate_set_identity_method": "CANONICAL_ORDERED_SEMANTIC_CANDIDATE_PAYLOAD_DIGEST",
        "decision_deadline_provenance": _counter(
            row["creation_provenance"]["decision_deadline_provenance"]
            for row in rows),
        "decision_evidence_grade_distribution": _counter(
            row["decision_evidence_grade"] for row in rows),
        "holding_evidence_grade_distribution": _counter(
            row["holding_evidence_grade"] for row in rows),
        "initial_state_hash_unique_count": len({row["initial_m3_state_sha256"]
                                                 for row in rows}),
        "legacy_fallback_mapping_verdict": (
            "NOT_PROVEN_OLD_232_EXCLUDED_NEW_VERSIONED_RECORDER_EXPLICITLY_EMITS_"
            "CANONICAL_FALLBACK_WITHOUT_PRODUCER_ALIAS_CLAIM"),
        "capture_determinism": capture_determinism,
        "policy_execution_count_per_campaign": len(rows),
        "policy_execution_count_total": len(rows) * 2,
        "model_forward_count": 0,
        "action_rewrite_count": 0,
        "evidence_fabrication_count": 0,
        "status": ("PASS" if admitted == len(rows) and len(rows) >= 259 and
                   capture_determinism["status"] == "PASS" else "FAIL"),
    }


def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset).resolve()
    capture_determinism_peer = Path(args.capture_determinism_peer).resolve()
    artifact_dir = Path(args.artifact_dir).resolve()
    report_dir = Path(args.report_dir).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=False)
    report_dir.mkdir(parents=True, exist_ok=True)
    dataset = load_strict(dataset_path)
    unsealed_dataset = dict(dataset)
    declared_dataset_sha = unsealed_dataset.pop("dataset_sha256", None)
    if declared_dataset_sha != sha256_bytes(canonical_bytes(unsealed_dataset)):
        raise ValueError("capture dataset self-hash mismatch")
    raw_records = dataset.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("capture dataset records must be an array")
    if dataset.get("records_sha256") != sha256_bytes(canonical_bytes(raw_records)):
        raise ValueError("capture record-set hash mismatch")
    records = tuple(LifecycleAwareM2BDecisionRecordV1.from_dict(row)
                    for row in raw_records)
    admissions = tuple(admit_lifecycle_aware_record(record) for record in records)
    if not all(row.get("admitted") is True for row in admissions):
        raise RuntimeError("lifecycle-aware dataset admission failed")

    dataset_manifest = _dataset_manifest(
        dataset_path, capture_determinism_peer, dataset, records, admissions)
    write_canonical(report_dir / "LIFECYCLE_AWARE_DATASET_MANIFEST.json",
                    dataset_manifest)

    recorded_episodes = tuple(build_recorded_decision_episode(
        record, creation_utc=args.creation_utc) for record in records)
    recorded_episode_artifact = write_canonical(
        artifact_dir / "RECORDED_DECISION_EPISODES.json", {
            "schema_version": ARTIFACT_SCHEMA,
            "episode_class": "RECORDED_DECISION_ONLY",
            "episode_count": len(recorded_episodes),
            "episodes": [episode.to_dict() for episode in recorded_episodes],
        })
    recorded_traces, recorded_determinism = _double_run(recorded_episodes)
    recorded_trace_artifact = write_canonical(
        artifact_dir / "RECORDED_DECISION_TRACES.json", {
            "schema_version": ARTIFACT_SCHEMA,
            "episode_class": "RECORDED_DECISION_ONLY",
            "trace_count": len(recorded_traces),
            "traces": [trace.to_dict() for trace in recorded_traces],
        })
    recorded_results = _recorded_results(
        records, recorded_episodes, recorded_traces, recorded_determinism,
        recorded_trace_artifact)
    recorded_results["episode_artifact"] = recorded_episode_artifact
    write_canonical(report_dir / "RECORDED_DECISION_REPLAY_RESULTS.json",
                    recorded_results)

    schedule_set = build_lifecycle_dev_schedule_set(records)
    schedule_artifact = write_canonical(
        artifact_dir / "LIFECYCLE_DEV_SCHEDULES.json", schedule_set)
    frozen_schedule_path = Path(schedule_artifact["path"])
    if file_sha256(frozen_schedule_path) != schedule_artifact["sha256"]:
        raise RuntimeError("published lifecycle schedule hash mismatch")
    schedule_set = load_strict(frozen_schedule_path)
    lifecycle_episodes, lifecycle_oracles = compile_lifecycle_schedule_set(
        schedule_set, records, creation_utc=args.creation_utc)
    lifecycle_episode_artifact = write_canonical(
        artifact_dir / "LIFECYCLE_DEV_EPISODES.json", {
            "schema_version": ARTIFACT_SCHEMA,
            "episode_class": schedule_set["schedule_class"],
            "episode_count": len(lifecycle_episodes),
            "episodes": [episode.to_dict() for episode in lifecycle_episodes],
        })
    # Oracle is published separately and before the replay runner is called.
    oracle_artifact = write_canonical(
        artifact_dir / "LIFECYCLE_DEV_ORACLES.json", {
            "schema_version": ARTIFACT_SCHEMA,
            "oracle_count": len(lifecycle_oracles),
            "oracles": [oracle.to_dict() for oracle in lifecycle_oracles],
        })
    lifecycle_traces, lifecycle_determinism = _double_run(lifecycle_episodes)
    lifecycle_trace_artifact = write_canonical(
        artifact_dir / "LIFECYCLE_DEV_TRACES.json", {
            "schema_version": ARTIFACT_SCHEMA,
            "trace_count": len(lifecycle_traces),
            "traces": [trace.to_dict() for trace in lifecycle_traces],
        })
    lifecycle_results = _lifecycle_results(
        schedule_set, records, lifecycle_episodes, lifecycle_oracles, lifecycle_traces,
        lifecycle_determinism, {
            "schedule": schedule_artifact,
            "episode": lifecycle_episode_artifact,
            "oracle": oracle_artifact,
            "trace": lifecycle_trace_artifact,
        })
    write_canonical(report_dir / "LIFECYCLE_DEV_REPLAY_RESULTS.json",
                    lifecycle_results)

    all_episodes = (*recorded_episodes, *lifecycle_episodes)
    all_replay_traces = (*recorded_traces, *lifecycle_traces)
    bridge = M3ShadowBridge()
    shadow_values = []
    for episode in all_episodes:
        value = bridge.run(ShadowInputEnvelope.create(episode))
        if isinstance(value, ShadowBridgeRejection):
            raise RuntimeError("shadow bridge rejected episode: " + value.detail_code)
        shadow_values.append(value)
    shadow_artifact = write_canonical(
        artifact_dir / "SHADOW_TRACES.json", {
            "schema_version": ARTIFACT_SCHEMA,
            "shadow_trace_count": len(shadow_values),
            "traces": [trace.to_dict() for trace in shadow_values],
        })
    shadow_outcome = _shadow_equivalence(
        all_episodes, all_replay_traces, shadow_values, shadow_artifact)
    shadow_results = shadow_outcome["result"]
    write_canonical(report_dir / "SHADOW_BRIDGE_RESULTS.json", shadow_results)

    overall = {
        "milestone_id": args.milestone_id,
        "dataset_manifest": dataset_manifest,
        "recorded_results": recorded_results,
        "lifecycle_results": lifecycle_results,
        "shadow_results": shadow_results,
        "artifacts": {
            "recorded_episodes": recorded_episode_artifact,
            "recorded_traces": recorded_trace_artifact,
            "lifecycle_schedule": schedule_artifact,
            "lifecycle_episodes": lifecycle_episode_artifact,
            "lifecycle_oracles": oracle_artifact,
            "lifecycle_traces": lifecycle_trace_artifact,
            "shadow_traces": shadow_artifact,
        },
        "status": ("PASS" if all(section["status"] == "PASS" for section in (
            dataset_manifest, recorded_results, lifecycle_results, shadow_results))
                   else "FAIL"),
    }
    write_canonical(artifact_dir / "CAMPAIGN_EXECUTION_SUMMARY.json", overall)
    return overall


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--milestone-id", required=True)
    parser.add_argument("--creation-utc", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--capture-determinism-peer", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--report-dir", required=True)
    args = parser.parse_args(argv)
    result = execute(args)
    print(strict_json_dumps({
        "milestone_id": args.milestone_id,
        "status": result["status"],
        "record_count": result["dataset_manifest"]["record_count"],
        "recorded_episode_count": result["recorded_results"]["episode_count"],
        "lifecycle_episode_count": result["lifecycle_results"]["episode_count"],
        "shadow_trace_count": result["shadow_results"]["shadow_trace_count"],
    }))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
