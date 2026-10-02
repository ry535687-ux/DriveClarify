"""Builders for recorded-decision and scheduled lifecycle replay campaigns.

The builders in this module are deliberately pure.  They accept already
captured non-blind records and explicit, pre-frozen schedules; they never read
the wall clock, a model, a policy, an oracle, or the filesystem.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from driveclarify_m3_minimal_core import (
    ControlAuthority,
    EvidenceGrade,
    MinimalM3State,
    check_invariants,
    reduce_event_group,
)

from .adapters import ADAPTER_ID, ADAPTER_VERSION, AdapterRejection, adapt_record
from .contracts import (
    ORACLE_SCHEMA,
    ReplayEpisode,
    ReplayOracle,
    ReplayRecord,
    SourceTier,
    TimeMappingMode,
)
from .serialization import canonical_sha256, to_json_compatible


RECORDED_DECISION_EPISODE_CLASS = "RECORDED_DECISION_ONLY"
SCHEDULE_CLASS = "NONBLIND_SCHEDULED_LIFECYCLE_DEV"
SCHEDULE_SCHEMA = "driveclarify.m3.nonblind-lifecycle-schedule.v1"
SCHEDULE_SET_SCHEMA = "driveclarify.m3.nonblind-lifecycle-schedule-set.v1"
RECORDED_PROVENANCE = "RECORDED_NONBLIND_TRAIN_DEV"
SCHEDULED_PROVENANCE = "NONBLIND_SCHEDULED_LIFECYCLE_DEV"
SYNTHETIC_PROVENANCE = "NONBLIND_SYNTHETIC_CONTRACT_TEST_ONLY"
VERIFIED = EvidenceGrade.VERIFIED_FROM_CONTROLLED_PROBE.value


def _record_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): to_json_compatible(item) for key, item in value.items()}
    method = getattr(value, "to_dict", None)
    if not callable(method):
        raise TypeError("captured record must be a mapping or expose to_dict")
    result = method()
    if not isinstance(result, Mapping):
        raise TypeError("captured record to_dict must return a mapping")
    return {str(key): to_json_compatible(item) for key, item in result.items()}


def _standard_event_payload(record: Mapping[str, Any], *,
                            holding_evidence_grade: str | None = None,
                            holding_lease: Mapping[str, Any] | None = None,
                            act_evidence_grade: str | None = None,
                            candidate_freshness: str | None = None,
                            baseline_authority_eligible: bool | None = None,
                            **extra: Any) -> dict[str, Any]:
    initial = record["initial_m3_state"]
    payload = {
        "candidate_set_id": record["candidate_set_id"],
        "candidate_freshness": (candidate_freshness if candidate_freshness is not None
                                else record["candidate_freshness"]),
        "act_evidence_grade": (act_evidence_grade if act_evidence_grade is not None
                               else record["decision_evidence_grade"]),
        "holding_evidence_grade": (
            holding_evidence_grade if holding_evidence_grade is not None
            else record["holding_evidence_grade"]),
        "lease": (to_json_compatible(holding_lease) if holding_lease is not None
                  else to_json_compatible(record["holding_lease"])),
        "answer_present": False,
        "baseline_authority_eligible": (
            initial["baseline_authority_eligible"]
            if baseline_authority_eligible is None else baseline_authority_eligible),
        "physical_mode_ready": False,
        "reason_code_authorizes_control": False,
        "model_forward_requested": False,
        "low_level_control_requested": False,
        "decision_deadline_monotonic": record["decision_deadline_monotonic"],
        "answer_deadline_monotonic": record["answer_deadline_monotonic"],
    }
    payload.update(extra)
    return payload


def _decision_replay_record(
    captured: Any,
    *,
    episode_id: str,
    sequence_index: int,
    record_id: str | None = None,
    query_episode_id: str | None | object = ...,
    provenance_grade: str = RECORDED_PROVENANCE,
    holding_evidence_grade: str | None = None,
    holding_lease: Mapping[str, Any] | None = None,
    candidate_freshness: str | None = None,
    event_monotonic_time: int | float | None = None,
    baseline_authority_eligible: bool | None = None,
) -> ReplayRecord:
    record = _record_dict(captured)
    action = record["selected_action_canonical"]
    event_time = (record["decision_monotonic_time"]
                  if event_monotonic_time is None else event_monotonic_time)
    query = (record["query_episode_id"] if query_episode_id is ...
             else query_episode_id)
    event_payload = _standard_event_payload(
        record,
        holding_evidence_grade=holding_evidence_grade,
        holding_lease=holding_lease,
        candidate_freshness=candidate_freshness,
        baseline_authority_eligible=baseline_authority_eligible,
    )
    evidence = (event_payload["holding_evidence_grade"] if action == "WAIT"
                else event_payload["act_evidence_grade"])
    source_binding = (record["record_sha256"] if provenance_grade == RECORDED_PROVENANCE
                      else canonical_sha256({
                          "captured_record_sha256": record["record_sha256"],
                          "episode_id": episode_id,
                          "record_id": record_id,
                          "query_episode_id": query,
                          "event_payload": event_payload,
                          "provenance_grade": provenance_grade,
                      }))
    decision = {
        "decision_id": record_id or record["record_id"],
        "source_observation_id": record["source_observation_id"],
        "source_frame_id": record["source_frame_id"],
        "candidate_set_id": record["candidate_set_id"],
        "selected_action": action,
        "selected_candidate_id": record["selected_candidate_id"],
        "decision_monotonic_time": event_time,
        "decision_deadline_monotonic": record["decision_deadline_monotonic"],
        "answer_deadline_monotonic": record["answer_deadline_monotonic"],
        "query_episode_id": query,
        "query_identity": query,
        "query_budget": record["query_budget"],
        "candidate_freshness": (candidate_freshness if candidate_freshness is not None
                                else record["candidate_freshness"]),
        "evidence_grade": evidence,
        "holding_lease_payload": event_payload["lease"],
        "reason_codes": [
            "DIAGNOSTIC_ONLY",
            "SOURCE_RAW_ACTION:" + record["selected_action_raw"],
            "SOURCE_ACTION_MAPPING:" + record["action_mapping_kind"],
        ],
        "source_policy_version": record["source_policy_version"],
        "source_record_sha256": source_binding,
    }
    return ReplayRecord.create(
        record_id=record_id or record["record_id"],
        episode_id=episode_id,
        sequence_index=sequence_index,
        record_type="M2B_DECISION",
        source_component="M2B_LIFECYCLE_AWARE_CAPTURE",
        source_observation_id=record["source_observation_id"],
        source_frame_id=record["source_frame_id"],
        candidate_set_id=record["candidate_set_id"],
        query_episode_id=query,
        source_simulation_time=None,
        source_monotonic_time=event_time,
        replay_monotonic_time=event_time,
        calendar_utc=None,
        concurrent_group_id=None,
        payload={"decision": decision, "event_payload": event_payload},
        provenance_grade=provenance_grade,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
    )


def build_recorded_decision_episode(captured: Any, *,
                                    creation_utc: str) -> ReplayEpisode:
    """Build a one-record episode without reconstructing captured facts."""
    record = _record_dict(captured)
    episode_id = "recorded-decision-" + record["record_id"].lower()
    replay_record = _decision_replay_record(
        record, episode_id=episode_id, sequence_index=0)
    source_sha = record["source_artifact_sha256"]
    return ReplayEpisode.create(
        episode_id=episode_id,
        source_tier=SourceTier.TIER_C_RECORDED_NONBLIND_TRAIN_DEV,
        source_artifact_id=record["source_artifact_id"],
        source_artifact_sha256=source_sha,
        source_provenance={
            "source_identity": "LIFECYCLE_AWARE_M2B_TRAIN_DEV_CAPTURE",
            "origin": RECORDED_PROVENANCE,
            "record_id": record["record_id"],
            "record_sha256": record["record_sha256"],
            "recorded_episode_class": RECORDED_DECISION_EPISODE_CLASS,
            "verified_source_artifact_sha256": source_sha,
            "non_blind": True,
            "r3_excluded": True,
            "formal_m1_test_excluded": True,
        },
        initial_state=record["initial_m3_state"],
        time_mapping_mode=TimeMappingMode.EXPLICIT_REPLAY_MONOTONIC_TIMELINE,
        episode_start_source_time=record["decision_monotonic_time"],
        episode_start_replay_monotonic_time=record["decision_monotonic_time"],
        records=(replay_record,),
        creation_utc=creation_utc,
    )


def _schedule_entry(number: int, name: str, family: str,
                    seed_record: Mapping[str, Any], operations: Sequence[Mapping[str, Any]],
                    *, initial_state_overrides: Mapping[str, Any] | None = None,
                    synthetic_verified_abstract_lease: bool = False,
                    execute_seed_decision: bool = True) -> dict[str, Any]:
    base = {
        "schema_version": SCHEDULE_SCHEMA,
        "schedule_id": "LDEV-%02d-%s" % (number, name.upper().replace("_", "-")),
        "case_number": number,
        "case_name": name,
        "family": family,
        "schedule_class": SCHEDULE_CLASS,
        "seed_record_id": seed_record["record_id"],
        "seed_record_sha256": seed_record["record_sha256"],
        "seed_selected_action_raw": seed_record["selected_action_raw"],
        "seed_selected_action_canonical": seed_record["selected_action_canonical"],
        "initial_state_overrides": dict(initial_state_overrides or {}),
        "operations": [dict(item) for item in operations],
        "execute_seed_decision": execute_seed_decision,
        "synthetic_verified_abstract_lease": synthetic_verified_abstract_lease,
        "synthetic_contract_scope": (
            SYNTHETIC_PROVENANCE if synthetic_verified_abstract_lease else None),
        "not_recorded_passenger_behavior": True,
        "r3_excluded": True,
        "formal_m1_test_excluded": True,
        "runner_expected_values": [],
    }
    return {**base, "schedule_sha256": canonical_sha256(base)}


def build_lifecycle_dev_schedule_set(captured_records: Iterable[Any]) -> dict[str, Any]:
    """Create the fixed 32-case schedule without executing replay."""
    records = [_record_dict(item) for item in captured_records]
    by_action: dict[str, list[dict[str, Any]]] = {name: [] for name in (
        "ACT", "ASK", "WAIT", "FALLBACK")}
    for record in records:
        by_action[record["selected_action_canonical"]].append(record)
    for values in by_action.values():
        values.sort(key=lambda item: (item["split"], item["source_case_id"],
                                      item["source_profile_id"], item["record_id"]))
    missing = [action for action, values in by_action.items() if not values]
    if missing:
        raise ValueError("schedule requires all four actions: " + ",".join(missing))
    ask = [row for row in by_action["ASK"]
           if row["query_episode_id"] is not None and
           row["query_identity"] is not None]
    wait = [row for row in by_action["WAIT"]
            if row["holding_evidence_grade"] == "NOT_CURRENTLY_AVAILABLE" and
            row["holding_lease"] is None]
    act = [row for row in by_action["ACT"]
           if row["candidate_freshness"] == "FRESH"]
    fallback = by_action["FALLBACK"]
    if not ask or not wait or not act:
        raise ValueError(
            "schedule seed predicates require ASK identity, WAIT evidence gap, "
            "and fresh ACT")
    schedules: list[dict[str, Any]] = []

    def add(number: int, name: str, family: str, seed: Mapping[str, Any],
            operations: Sequence[Mapping[str, Any]], **options: Any) -> None:
        schedules.append(_schedule_entry(number, name, family, seed, operations,
                                         **options))

    # ASK family 1-12. Times are resolved from the captured decision/deadline
    # only when compiled, so the schedule contains no replay outcome.
    add(1, "ask_answer_before_deadline", "ASK", ask[0], [
        {"kind": "ANSWER", "time": "BEFORE_DEADLINE", "answer_present": True}])
    add(2, "ask_answer_at_deadline", "ASK", ask[1 % len(ask)], [
        {"kind": "ANSWER", "time": "AT_DEADLINE", "answer_present": True}])
    add(3, "ask_late_answer", "ASK", ask[2 % len(ask)], [
        {"kind": "ANSWER", "time": "AFTER_DEADLINE", "answer_present": True}])
    add(4, "ask_timeout", "ASK", ask[3 % len(ask)], [
        {"kind": "TIMEOUT", "time": "AFTER_DEADLINE"}])
    add(5, "ask_cancel", "ASK", ask[4 % len(ask)], [
        {"kind": "CANCEL", "time": "SOON"}])
    add(6, "ask_wrong_query_identity", "ASK", ask[5 % len(ask)], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": True,
         "query": "WRONG_QUERY"}])
    add(7, "ask_still_ambiguous_answer", "ASK", ask[6 % len(ask)], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": False}])
    add(8, "ask_answer_world_change", "ASK", ask[0], [
        {"kind": "WORLD_CHANGE", "time": "SOON", "group": "RACE"},
        {"kind": "ANSWER", "time": "SOON", "answer_present": True,
         "group": "RACE"}])
    add(9, "ask_answer_candidate_stale", "ASK", ask[1 % len(ask)], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": True},
        {"kind": "CANDIDATE_STALE", "time": "NEXT"}])
    add(10, "ask_answer_safety_preemption", "ASK", ask[2 % len(ask)], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": True,
         "group": "RACE"},
        {"kind": "SAFETY", "time": "SOON", "group": "RACE"}])
    add(11, "ask_active_query_second_ask", "ASK", ask[3 % len(ask)], [
        {"kind": "SECOND_ASK", "time": "SOON", "query": "SECOND_QUERY"}])
    add(12, "ask_revalidation_fail", "ASK", ask[4 % len(ask)], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": True},
        {"kind": "REVALIDATION_FAIL", "time": "NEXT"}])

    # Revalidation/replan family 13-20.
    add(13, "answer_revalidate_pass_replan_complete", "REVALIDATION_REPLAN", ask[5 % len(ask)], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": True},
        {"kind": "REVALIDATION_PASS", "time": "NEXT"},
        {"kind": "REPLAN_FRESH", "time": "LATER"}])
    add(14, "answer_revalidation_fail", "REVALIDATION_REPLAN", ask[6 % len(ask)], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": True},
        {"kind": "REVALIDATION_FAIL", "time": "NEXT"}])
    add(15, "replan_complete_resume", "REVALIDATION_REPLAN", ask[0], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": True},
        {"kind": "REVALIDATION_PASS", "time": "NEXT"},
        {"kind": "REPLAN_FRESH", "time": "LATER"}])
    add(16, "replan_complete_candidate_stale", "REVALIDATION_REPLAN", ask[1 % len(ask)], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": True},
        {"kind": "REVALIDATION_PASS", "time": "NEXT"},
        {"kind": "REPLAN_FRESH", "time": "LATER", "group": "RACE"},
        {"kind": "CANDIDATE_STALE", "time": "LATER", "group": "RACE"}])
    add(17, "world_change_revalidation", "REVALIDATION_REPLAN", ask[2 % len(ask)], [
        {"kind": "WORLD_CHANGE", "time": "SOON"},
        {"kind": "REVALIDATION_PASS", "time": "NEXT"}])
    add(18, "candidate_stale_replan", "REVALIDATION_REPLAN", act[0], [
        {"kind": "CANDIDATE_STALE", "time": "SOON"},
        {"kind": "REVALIDATION_PASS", "time": "NEXT"},
        {"kind": "REPLAN_FRESH", "time": "LATER"}],
        initial_state_overrides={"candidate_freshness": "FRESH"},
        execute_seed_decision=False)
    add(19, "safety_during_revalidation", "REVALIDATION_REPLAN", ask[3 % len(ask)], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": True},
        {"kind": "SAFETY", "time": "NEXT"}])
    add(20, "capability_loss_during_replan", "REVALIDATION_REPLAN", ask[4 % len(ask)], [
        {"kind": "ANSWER", "time": "SOON", "answer_present": True},
        {"kind": "REVALIDATION_PASS", "time": "NEXT"},
        {"kind": "HOLDING_LOSS", "time": "LATER"}])

    # WAIT family 21-26. Only 23-26 receive a schedule-declared verified
    # abstract lease, exactly as allowed by the contract-test boundary.
    add(21, "recorded_wait_unavailable_evidence", "WAIT", wait[0], [])
    add(22, "wait_missing_lease", "WAIT", wait[1 % len(wait)], [])
    add(23, "wait_synthetic_verified_abstract_lease", "WAIT", wait[2 % len(wait)], [],
        synthetic_verified_abstract_lease=True)
    add(24, "valid_abstract_lease_expiry", "WAIT", wait[3 % len(wait)], [
        {"kind": "LEASE_EXPIRY", "time": "LEASE_EXPIRY"}],
        synthetic_verified_abstract_lease=True)
    add(25, "holding_capability_loss", "WAIT", wait[4 % len(wait)], [
        {"kind": "HOLDING_LOSS", "time": "SOON"}],
        synthetic_verified_abstract_lease=True)
    add(26, "safety_preemption_revokes_lease", "WAIT", wait[5 % len(wait)], [
        {"kind": "SAFETY", "time": "SOON"}],
        synthetic_verified_abstract_lease=True)

    # ACT/FALLBACK family 27-32. Evidence remains exactly what was captured;
    # no synthetic VERIFIED ACT is introduced.
    add(27, "fresh_act", "ACT_FALLBACK", act[0], [])
    add(28, "stale_act_blocked", "ACT_FALLBACK", act[1 % len(act)], [],
        initial_state_overrides={"candidate_freshness": "STALE"})
    add(29, "unknown_act_blocked", "ACT_FALLBACK", act[2 % len(act)], [],
        initial_state_overrides={"candidate_freshness": "UNKNOWN"})
    add(30, "fallback_baseline_eligible", "ACT_FALLBACK", fallback[0], [],
        initial_state_overrides={
            "baseline_authority_eligible": True,
            "authority": ControlAuthority.BASELINE_CONTROL.value,
        })
    add(31, "fallback_without_baseline", "ACT_FALLBACK", fallback[1], [],
        initial_state_overrides={
            "baseline_authority_eligible": False,
            "authority": ControlAuthority.NO_M3_CONTROL_AUTHORITY.value,
        })
    add(32, "fallback_safety_active", "ACT_FALLBACK", fallback[2], [],
        initial_state_overrides={
            "safety_guard_active": True,
            "baseline_authority_eligible": False,
            "authority": ControlAuthority.INDEPENDENT_SAFETY_GUARD.value,
        })

    if len(schedules) != 32 or [row["case_number"] for row in schedules] != list(range(1, 33)):
        raise AssertionError("lifecycle schedule must contain cases 1 through 32")
    base = {
        "schema_version": SCHEDULE_SET_SCHEMA,
        "schedule_class": SCHEDULE_CLASS,
        "schedule_count": len(schedules),
        "schedules": schedules,
        "selection_rule": "STABLE_CAPTURED_ACTION_THEN_SOURCE_ID; INDEPENDENT_OF_REPLAY_OUTCOME",
        "frozen_before_replay": True,
        "runner_expected_values": [],
        "r3_excluded": True,
        "formal_m1_test_excluded": True,
    }
    return {**base, "schedule_set_sha256": canonical_sha256(base)}


def _event_time(record: Mapping[str, Any], token: str) -> float:
    now = float(record["decision_monotonic_time"])
    deadline = float(record["answer_deadline_monotonic"])
    if token == "AT_DEADLINE":
        return deadline
    if token == "AFTER_DEADLINE":
        return deadline + 1.0
    if token == "BEFORE_DEADLINE":
        return now + (deadline - now) * 0.5
    if token == "SOON":
        return now + min(0.25, max((deadline - now) * 0.1, 0.01))
    if token == "NEXT":
        return now + min(0.50, max((deadline - now) * 0.2, 0.02))
    if token == "LATER":
        return now + min(0.75, max((deadline - now) * 0.3, 0.03))
    if token == "LEASE_EXPIRY":
        return now + 1.0
    raise ValueError("unknown schedule time token: " + token)


_CONTINUATION_TYPES = {
    "ANSWER": "PASSENGER_ANSWER",
    "TIMEOUT": "QUERY_TIMEOUT",
    "CANCEL": "QUERY_CANCEL",
    "WORLD_CHANGE": "WORLD_STATE_CHANGE",
    "CANDIDATE_STALE": "CANDIDATE_STALE",
    "HOLDING_LOSS": "HOLDING_CAPABILITY_LOST",
    "SAFETY": "SAFETY_PREEMPTION",
    "LEASE_EXPIRY": "LEASE_EXPIRY_CHECK",
    "REVALIDATION_PASS": "REVALIDATION_PASS",
    "REVALIDATION_FAIL": "REVALIDATION_FAIL",
    "REPLAN_FRESH": "REPLAN_RESULT",
}


def _continuation_record(record: Mapping[str, Any], schedule: Mapping[str, Any],
                         operation: Mapping[str, Any], *, episode_id: str,
                         sequence_index: int, query_id: str | None,
                         synthetic_lease: Mapping[str, Any] | None) -> ReplayRecord:
    kind = operation["kind"]
    time_value = _event_time(record, operation["time"])
    actual_query = operation.get("query", query_id)
    if actual_query == "WRONG_QUERY":
        actual_query = "wrong-" + str(query_id)
    if actual_query == "SECOND_QUERY":
        actual_query = "second-" + str(query_id)
    event_id = schedule["schedule_id"].lower() + "-event-%02d" % sequence_index
    if kind == "SECOND_ASK":
        return _decision_replay_record(
            record,
            episode_id=episode_id,
            sequence_index=sequence_index,
            record_id=event_id,
            query_episode_id=actual_query,
            provenance_grade=SCHEDULED_PROVENANCE,
            event_monotonic_time=time_value,
        )
    payload = _standard_event_payload(record)
    if kind == "ANSWER":
        payload["answer_present"] = operation["answer_present"]
    if kind == "REPLAN_FRESH":
        payload.update(candidate_freshness="FRESH",
                       candidate_set_id=record["candidate_set_id"])
    if synthetic_lease is not None:
        payload.update(holding_evidence_grade=VERIFIED, lease=synthetic_lease)
    record_type = _CONTINUATION_TYPES[kind]
    group = operation.get("group")
    group_id = (None if group is None else
                schedule["schedule_id"].lower() + "-" + str(group).lower())
    return ReplayRecord.create(
        record_id=event_id,
        episode_id=episode_id,
        sequence_index=sequence_index,
        record_type=record_type,
        source_component="NONBLIND_LIFECYCLE_DEV_SCHEDULE",
        source_observation_id=record["source_observation_id"],
        source_frame_id=record["source_frame_id"],
        candidate_set_id=record["candidate_set_id"],
        query_episode_id=actual_query,
        source_simulation_time=None,
        source_monotonic_time=time_value,
        replay_monotonic_time=time_value,
        calendar_utc=None,
        concurrent_group_id=group_id,
        payload=payload,
        provenance_grade=SCHEDULED_PROVENANCE,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
    )


def _abstract_lease(record: Mapping[str, Any], schedule_id: str) -> dict[str, Any]:
    now = float(record["decision_monotonic_time"])
    return {
        "lease_id": schedule_id.lower() + "-abstract-lease",
        "issued_monotonic_time": now,
        "next_reevaluation_monotonic_time": now + 0.5,
        "expires_monotonic_time": now + 1.0,
        "maximum_expiry_monotonic_time": now + 1.0,
        "evidence_grade": VERIFIED,
        "source_observation_id": record["source_observation_id"],
        "source_frame_id": record["source_frame_id"],
        "candidate_set_id": record["candidate_set_id"],
        "revoked": False,
        "revocation_reason": None,
        "authority_on_exit": ControlAuthority.NO_M3_CONTROL_AUTHORITY.value,
    }


def compile_lifecycle_schedule(
    schedule: Mapping[str, Any], captured: Any, *, creation_utc: str,
) -> tuple[ReplayEpisode, ReplayOracle]:
    """Compile a frozen schedule and derive a pre-replay direct-core oracle."""
    record = _record_dict(captured)
    if schedule.get("schema_version") != SCHEDULE_SCHEMA:
        raise ValueError("lifecycle schedule schema mismatch")
    if schedule.get("schedule_class") != SCHEDULE_CLASS:
        raise ValueError("lifecycle schedule class mismatch")
    case_number = schedule.get("case_number")
    synthetic_allowed = case_number in {23, 24, 25, 26}
    if bool(schedule.get("synthetic_verified_abstract_lease")) != synthetic_allowed:
        raise ValueError("synthetic verified lease is restricted to cases 23-26")
    expected_scope = SYNTHETIC_PROVENANCE if synthetic_allowed else None
    if schedule.get("synthetic_contract_scope") != expected_scope:
        raise ValueError("synthetic lease provenance scope mismatch")
    if schedule.get("not_recorded_passenger_behavior") is not True:
        raise ValueError("scheduled events cannot claim recorded passenger behavior")
    if (schedule.get("r3_excluded") is not True or
            schedule.get("formal_m1_test_excluded") is not True or
            schedule.get("runner_expected_values") != []):
        raise ValueError("lifecycle schedule boundary fields mismatch")
    if schedule["seed_record_id"] != record["record_id"]:
        raise ValueError("schedule seed record identity mismatch")
    if schedule["seed_record_sha256"] != record["record_sha256"]:
        raise ValueError("schedule seed record hash mismatch")
    schedule_without_hash = {key: value for key, value in schedule.items()
                             if key != "schedule_sha256"}
    if canonical_sha256(schedule_without_hash) != schedule["schedule_sha256"]:
        raise ValueError("schedule hash mismatch")
    episode_id = "lifecycle-dev-" + schedule["schedule_id"].lower()
    initial = dict(record["initial_m3_state"])
    initial.update(schedule["initial_state_overrides"])
    state = MinimalM3State.from_dict(initial)
    failures = check_invariants(state)
    if failures:
        raise ValueError("scheduled initial state invariant failure: " + ",".join(failures))
    synthetic_lease = (_abstract_lease(record, schedule["schedule_id"])
                       if schedule["synthetic_verified_abstract_lease"] else None)
    seed_grade = (SYNTHETIC_PROVENANCE if synthetic_lease is not None
                  else RECORDED_PROVENANCE)
    seed = _decision_replay_record(
        record,
        episode_id=episode_id,
        sequence_index=0,
        provenance_grade=seed_grade,
        holding_evidence_grade=(VERIFIED if synthetic_lease is not None else None),
        holding_lease=synthetic_lease,
        candidate_freshness=initial["candidate_freshness"],
        baseline_authority_eligible=initial["baseline_authority_eligible"],
    )
    query_id = seed.query_episode_id
    replay_records = [seed] if schedule["execute_seed_decision"] else []
    first_operation_index = len(replay_records)
    for index, operation in enumerate(schedule["operations"], first_operation_index):
        replay_records.append(_continuation_record(
            record, schedule, operation, episode_id=episode_id,
            sequence_index=index, query_id=query_id,
            synthetic_lease=synthetic_lease))
    schedule_sha = schedule["schedule_sha256"]
    episode = ReplayEpisode.create(
        episode_id=episode_id,
        source_tier=SourceTier.TIER_D_NONBLIND_SCHEDULED_LIFECYCLE_DEV,
        source_artifact_id="NONBLIND_SCHEDULED_LIFECYCLE_DEV:" + schedule["schedule_id"],
        source_artifact_sha256=schedule_sha,
        source_provenance={
            "source_identity": SCHEDULE_CLASS,
            "origin": SCHEDULED_PROVENANCE,
            "seed_record_id": record["record_id"],
            "seed_record_sha256": record["record_sha256"],
            "schedule_sha256": schedule_sha,
            "not_recorded_passenger_behavior": True,
            "verified_source_artifact_sha256": schedule_sha,
            "non_blind": True,
            "r3_excluded": True,
            "formal_m1_test_excluded": True,
        },
        initial_state=state.to_dict(),
        time_mapping_mode=TimeMappingMode.EXPLICIT_REPLAY_MONOTONIC_TIMELINE,
        episode_start_source_time=record["decision_monotonic_time"],
        episode_start_replay_monotonic_time=record["decision_monotonic_time"],
        records=tuple(replay_records),
        creation_utc=creation_utc,
    )
    direct_transitions: list[str] = []
    direct_processing: list[str] = []
    cursor_state = state
    cursor = 0
    while cursor < len(replay_records):
        group_time = replay_records[cursor].replay_monotonic_time
        end = cursor + 1
        while (end < len(replay_records) and
               replay_records[end].replay_monotonic_time == group_time):
            end += 1
        adapted_group = []
        for replay_record in replay_records[cursor:end]:
            adapted = adapt_record(replay_record, cursor_state)
            if isinstance(adapted, AdapterRejection):
                raise ValueError(
                    "scheduled record failed adapter before oracle freeze: " +
                    adapted.category)
            adapted_group.append(adapted)
        direct_group = reduce_event_group(
            cursor_state, tuple(adapted_group), group_time)
        cursor_state = direct_group.state
        direct_transitions.extend(direct_group.transition_ids)
        direct_processing.extend(
            item.value for item in direct_group.processing_results)
        cursor = end
    oracle = ReplayOracle(
        oracle_id="oracle-" + episode_id,
        episode_id=episode_id,
        episode_records_sha256=episode.records_sha256,
        oracle_schema_version=ORACLE_SCHEMA,
        expected_transition_ids=tuple(direct_transitions),
        expected_final_state=cursor_state.to_dict(),
        expected_authority=cursor_state.authority.value,
        expected_query_active=cursor_state.query_active,
        expected_candidate_freshness=cursor_state.candidate_freshness,
        expected_lease_status=(None if cursor_state.holding_lease is None
                               else cursor_state.holding_lease.to_dict()),
        expected_processing_results=tuple(direct_processing),
        expected_audit_predicates=(),
        forbidden_paths=("partial_mutation_count", "second_query_count",
                         "stale_unknown_act_count", "low_level_control_output_count"),
        oracle_provenance={
            "origin": "PRE_REPLAY_DIRECT_FROZEN_MINIMAL_CORE_EXECUTION",
            "schedule_sha256": schedule_sha,
            "seed_record_sha256": record["record_sha256"],
            "runner_input_separate": True,
            "r3_excluded": True,
            "formal_m1_test_excluded": True,
        },
        non_blind_only=True,
        creation_utc=creation_utc,
    )
    return episode, oracle


def compile_lifecycle_schedule_set(
    schedule_set: Mapping[str, Any], captured_records: Iterable[Any], *,
    creation_utc: str,
) -> tuple[tuple[ReplayEpisode, ...], tuple[ReplayOracle, ...]]:
    base = {key: value for key, value in schedule_set.items()
            if key != "schedule_set_sha256"}
    if canonical_sha256(base) != schedule_set["schedule_set_sha256"]:
        raise ValueError("schedule set hash mismatch")
    if (schedule_set.get("schema_version") != SCHEDULE_SET_SCHEMA or
            schedule_set.get("schedule_class") != SCHEDULE_CLASS or
            schedule_set.get("schedule_count") != 32 or
            schedule_set.get("frozen_before_replay") is not True or
            schedule_set.get("runner_expected_values") != [] or
            schedule_set.get("r3_excluded") is not True or
            schedule_set.get("formal_m1_test_excluded") is not True):
        raise ValueError("schedule set closed boundary mismatch")
    schedules = schedule_set.get("schedules")
    if (not isinstance(schedules, list) or len(schedules) != 32 or
            [row.get("case_number") for row in schedules] != list(range(1, 33)) or
            len({row.get("schedule_id") for row in schedules}) != 32):
        raise ValueError("schedule set must contain unique cases 1 through 32")
    by_id = {_record_dict(item)["record_id"]: item for item in captured_records}
    episodes = []
    oracles = []
    for schedule in schedules:
        episode, oracle = compile_lifecycle_schedule(
            schedule, by_id[schedule["seed_record_id"]], creation_utc=creation_utc)
        episodes.append(episode)
        oracles.append(oracle)
    return tuple(episodes), tuple(oracles)


__all__ = [
    "RECORDED_DECISION_EPISODE_CLASS", "SCHEDULE_CLASS", "SCHEDULE_SCHEMA",
    "SCHEDULE_SET_SCHEMA", "build_recorded_decision_episode",
    "build_lifecycle_dev_schedule_set", "compile_lifecycle_schedule",
    "compile_lifecycle_schedule_set",
]
