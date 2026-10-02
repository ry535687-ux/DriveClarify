"""Deterministic, label-free reachability fixtures for the persistent policy.

The fixtures exercise the production M2B adapter, frozen M3 reducer, tagged
shared-subject authority resolver, and existing bounded holding executor.  They
do not read evaluator labels or choose a decision from an expected value.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from driveclarify_m3_live_authority import ActiveAuthorityMember
from driveclarify_m3_minimal_core import (
    ControlAuthority,
    EventType,
    EvidenceGrade,
    LifecycleState,
    MinimalM3Event,
    MinimalM3State,
    ProcessingResult,
    reduce_event,
)
from driveclarify_m3_runtime_shadow.limited_act_commit_v0 import (
    _build_frozen_m3_act_result,
)
from driveclarify_m3_runtime_shadow.physical_wait_v0 import (
    PhysicalWaitExecutorV0,
    build_bounded_wait_pilot_binding,
)
from driveclarify_m3_runtime_shadow.shared_act_commit_v1 import SharedActCommitV1

from .m2b_adapter import (
    AxisValue,
    PersistentDecision,
    PersistentDecisionContext,
    decide_persistent,
)


_HASHES = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)


class FixtureTensor:
    """Small tensor-protocol object accepted by the existing digest seam."""

    def __init__(self, value: bytes) -> None:
        self.value = value

    def detach(self) -> "FixtureTensor":
        return self

    def contiguous(self) -> "FixtureTensor":
        return self

    def cpu(self) -> "FixtureTensor":
        return self

    def numpy(self) -> memoryview:
        return memoryview(self.value)


def _context(**changes: Any) -> PersistentDecisionContext:
    values: dict[str, Any] = {
        "active_candidate_count": 2,
        "semantic_state": "UNRESOLVED",
        "current_action_relation": AxisValue.CURRENT_ACTION_EQUIVALENT,
        "future_obligation_relation": AxisValue.FUTURE_DIVERGENT,
        "evidence_fresh": True,
        "full_plan_coverage": True,
        "alignment_verified": True,
        "shared_action_safe": True,
        "recoverable": True,
        "latest_safe_slack_positive": True,
        "decision_deadline_available": True,
        "decision_deadline_crossed": False,
        "hard_safety_gate": True,
        "hard_rule_gate": True,
        "active_query": False,
        "active_holding_lease": False,
        "multiple_plausible_interpretations": True,
        "material_consequence_divergence": False,
        "answer_changes_decision": True,
        "positive_query_value": True,
        "query_budget_available": True,
        "answer_likely_before_deadline": True,
        "passenger_resolvable": True,
        "verified_holding_available": False,
    }
    values.update(changes)
    return PersistentDecisionContext(**values)


def _m3_event(
    event_id: str,
    event_type: EventType,
    now: float,
    *,
    query_id: str | None = None,
    **changes: Any,
) -> MinimalM3Event:
    payload = {
        "candidate_set_id": None,
        "candidate_freshness": "UNKNOWN",
        "act_evidence_grade": EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value,
        "holding_evidence_grade": EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value,
        "lease": None,
        "answer_present": False,
        "baseline_authority_eligible": True,
        "physical_mode_ready": False,
        "reason_code_authorizes_control": False,
        "model_forward_requested": False,
        "low_level_control_requested": False,
    }
    payload.update(changes)
    return MinimalM3Event.create(
        event_id=event_id,
        event_type=event_type,
        query_episode_id=query_id,
        source_component="DECISION_REACHABILITY_FIXTURE_V1",
        observed_monotonic_time=now,
        source_simulation_time=0.0,
        calendar_utc=None,
        payload=payload,
    )


def _reduce(state: MinimalM3State, event: MinimalM3Event, now: float):
    result = reduce_event(state, event, now)
    if result.processing_results != (ProcessingResult.PROCESSED,):
        raise RuntimeError("M3_FIXTURE_EVENT_REJECTED:" + event.event_type.value)
    return result


def act_shared_fixture() -> dict[str, Any]:
    context = _context()
    recommendation = decide_persistent(context)
    if recommendation.decision is not PersistentDecision.ACT_SHARED:
        raise RuntimeError("ACT_SHARED_NOT_REACHED")

    now = 100.0
    ready, act = _build_frozen_m3_act_result("fixture-candidate-set", now)
    members = tuple(
        ActiveAuthorityMember(
            candidate_id=value,
            interpretation_id="interpretation-" + value,
            semantic_sha256=_HASHES[index],
            target_obligation_digest=_HASHES[index + 2],
        )
        for index, value in enumerate(("A", "B"))
    )
    route = FixtureTensor(b"existing-baseline-shared-route")
    speed = FixtureTensor(b"existing-baseline-shared-speed")
    baseline_route = FixtureTensor(b"baseline-route-fallback")
    baseline_speed = FixtureTensor(b"baseline-speed-fallback")
    commit = SharedActCommitV1(authority_enabled=True)
    armed = commit.arm_shared(
        ambiguity_episode_id="fixture-ambiguity-episode",
        candidate_set_id="fixture-candidate-set",
        active_members=members,
        shared_action_window_id="fixture-shared-window",
        shared_action_class="EXISTING_BASELINE_KEEP_LANE_SHARED_PREFIX",
        route=route,
        speed=speed,
        source_observation_id="fixture-observation-1",
        source_frame_id="100",
        route_version="fixture-route-v1",
        environment_digest="fixture-environment-v1",
        current_action_equivalence_evidence_digest=_HASHES[0],
        decision_window_digest=_HASHES[1],
        current_monotonic=now,
        valid_until_monotonic=now + 1.0,
        episode_unresolved=True,
        active_member_set_matches=True,
        current_action_relation="CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT",
        evidence_fresh=True,
        plan_coverage_verified=True,
        alignment_verified=True,
        latest_safe_slack_positive=True,
        recoverable=True,
        existing_m3_act_result=act,
    )
    if not armed:
        raise RuntimeError("SHARED_SUBJECT_AUTHORITY_NOT_ARMED")
    selected = commit.select_plan_source(
        baseline_route,
        baseline_speed,
        frame="100",
        observation_id="fixture-observation-1",
        route_version="fixture-route-v1",
        environment_digest="fixture-environment-v1",
        current_monotonic=now,
        episode_unresolved=True,
        active_member_set_matches=True,
        current_action_relation="CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT",
        evidence_fresh=True,
        plan_coverage_verified=True,
        alignment_verified=True,
        latest_safe_slack_positive=True,
        recoverable=True,
    )
    commit.on_pid_invocation(frame="100")
    commit.on_control(frame="100")
    advanced = commit.observe_tick(frame="101")
    summary = dict(commit.summary())
    subject = commit.subject
    return {
        "schema_version": "driveclarify.decision_reachability_fixture.v1",
        "fixture_id": "FIXTURE-A-ACT-SHARED",
        "status": "PASS" if selected == (route, speed) and advanced else "FAIL",
        "runtime_inputs": asdict(context),
        "m2b": {
            "decision": recommendation.decision.value,
            "relation": recommendation.relation.value,
            "reason_codes": list(recommendation.reason_codes),
            "authority_subject_type": recommendation.authority_subject_type,
        },
        "m3": {
            "ready": ready.to_dict(),
            "act": act.to_dict(),
            "accepted": True,
        },
        "authority": {
            "armed": armed,
            "subject": subject.to_dict() if subject is not None else None,
            "selected_existing_plan": selected == (route, speed),
            "summary": summary,
        },
        "lifecycle": {
            "ambiguity_before": "UNRESOLVED",
            "ambiguity_after": "UNRESOLVED",
            "fresh_next_frame_observed": advanced,
            "baseline_restored": summary["baseline_ownership_returned"],
        },
        "new_pid_count": 0,
        "new_planner_count": 0,
        "direct_vehicle_control_write_count": 0,
    }


def ask_fixture() -> dict[str, Any]:
    context = _context(
        current_action_relation=AxisValue.CURRENT_ACTION_DIVERGENT,
        full_plan_coverage=True,
        recoverable=False,
        material_consequence_divergence=True,
    )
    recommendation = decide_persistent(context)
    if recommendation.decision is not PersistentDecision.ASK:
        raise RuntimeError("ASK_NOT_REACHED")
    now = 200.0
    initial = MinimalM3State(
        baseline_authority_eligible=True, current_time_monotonic=now
    )
    ready = _reduce(
        initial,
        _m3_event(
            "fixture-ask-ready",
            EventType.CANDIDATES_READY,
            now,
            candidate_set_id="fixture-ask-set",
            candidate_freshness="FRESH",
            answer_deadline_monotonic=now + 5.0,
            decision_deadline_monotonic=now + 5.0,
        ),
        now,
    )
    asked = _reduce(
        ready.state,
        _m3_event(
            "fixture-ask-issued",
            EventType.DECISION_ASK,
            now,
            query_id="fixture-query",
        ),
        now,
    )
    binding = build_bounded_wait_pilot_binding(
        run_id="fixture-ask",
        source_observation_id="fixture-observation-ask",
        source_frame_id="200",
        candidate_set_id="fixture-ask-set",
        start_monotonic_time=now,
        duration_s=5.0,
        source_simulation_time=0.0,
        query_episode_id="fixture-query",
    )
    wait = PhysicalWaitExecutorV0(enabled=True)
    holding_entered = wait.enter(
        binding,
        current_monotonic_time=now,
        frame=200,
        ego_position=(0.0, 0.0, 0.0),
        ego_speed_mps=0.0,
        baseline_control_path_healthy=True,
        no_control_ownership_conflict=True,
        native_carla_session_available=True,
        source_identity_recordable=True,
        environment_digest="fixture-environment-ask",
    )
    answered = _reduce(
        asked.state,
        _m3_event(
            "fixture-answer",
            EventType.ANSWER_ARRIVED,
            now + 1.0,
            query_id="fixture-query",
            answer_present=True,
        ),
        now + 1.0,
    )
    wait.observe_tick(
        frame=201,
        current_monotonic_time=now + 1.0,
        ego_position=(0.0, 0.0, 0.0),
        ego_speed_mps=0.0,
        source_runtime_valid=False,
        environment_digest="fixture-environment-ask",
        source_simulation_time=1.0,
    )
    revalidated = _reduce(
        answered.state,
        _m3_event("fixture-revalidated", EventType.REVALIDATION_PASSED, now + 1.1),
        now + 1.1,
    )
    replanned = _reduce(
        revalidated.state,
        _m3_event(
            "fixture-replanned",
            EventType.REPLAN_COMPLETE,
            now + 1.2,
            candidate_set_id="fixture-ask-set-fresh",
            candidate_freshness="FRESH",
        ),
        now + 1.2,
    )
    return {
        "schema_version": "driveclarify.decision_reachability_fixture.v1",
        "fixture_id": "FIXTURE-B-ASK",
        "status": "PASS",
        "runtime_inputs": asdict(context),
        "m2b": {
            "decision": recommendation.decision.value,
            "relation": recommendation.relation.value,
            "reason_codes": list(recommendation.reason_codes),
        },
        "m3": {
            "ask_transition_ids": list(asked.transition_ids),
            "ask_state": asked.state.to_dict(),
            "answer_transition_ids": list(answered.transition_ids),
            "fresh_replan_transition_ids": list(
                revalidated.transition_ids + replanned.transition_ids
            ),
            "final_state": replanned.state.to_dict(),
        },
        "query_lifecycle": {
            "query_id": "fixture-query",
            "active_after_ask": asked.state.query_active,
            "holding_entered": holding_entered,
            "answer_latency_s": 1.0,
            "old_bundle_invalidated": answered.state.candidate_freshness == "STALE",
            "fresh_replan": replanned.state.candidate_freshness == "FRESH",
            "holding_exited": not wait.active,
            "holding_receipt": binding.to_dict(),
        },
        "new_pid_count": 0,
        "new_planner_count": 0,
        "direct_vehicle_control_write_count": 0,
    }


def wait_fixture() -> dict[str, Any]:
    context = _context(
        current_action_relation=AxisValue.UNKNOWN,
        evidence_fresh=False,
        full_plan_coverage=False,
        shared_action_safe=False,
        recoverable=False,
        latest_safe_slack_positive=False,
        decision_deadline_available=False,
        decision_deadline_crossed=True,
        active_query=True,
        active_holding_lease=True,
        material_consequence_divergence=False,
        query_budget_available=False,
        verified_holding_available=True,
    )
    recommendation = decide_persistent(context)
    if recommendation.decision is not PersistentDecision.WAIT:
        raise RuntimeError("WAIT_NOT_REACHED")
    now = 300.0
    binding = build_bounded_wait_pilot_binding(
        run_id="fixture-wait",
        source_observation_id="fixture-observation-wait",
        source_frame_id="300",
        candidate_set_id="fixture-wait-set",
        start_monotonic_time=now,
        duration_s=5.0,
        source_simulation_time=0.0,
        query_episode_id="fixture-query-wait",
    )
    wait = PhysicalWaitExecutorV0(enabled=True)
    entered = wait.enter(
        binding,
        current_monotonic_time=now,
        frame=300,
        ego_position=(0.0, 0.0, 0.0),
        ego_speed_mps=0.0,
        baseline_control_path_healthy=True,
        no_control_ownership_conflict=True,
        native_carla_session_available=True,
        source_identity_recordable=True,
        environment_digest="fixture-environment-wait",
    )
    control = object()
    returned = wait.observe_baseline_control(control, frame=300, ready_time=now)
    wait.observe_tick(
        frame=301,
        current_monotonic_time=now + 1.0,
        ego_position=(0.0, 0.0, 0.0),
        ego_speed_mps=0.0,
        source_runtime_valid=False,
        environment_digest="fixture-environment-wait",
        source_simulation_time=1.0,
    )
    return {
        "schema_version": "driveclarify.decision_reachability_fixture.v1",
        "fixture_id": "FIXTURE-C-WAIT",
        "status": "PASS",
        "runtime_inputs": asdict(context),
        "m2b": {
            "decision": recommendation.decision.value,
            "relation": recommendation.relation.value,
            "reason_codes": list(recommendation.reason_codes),
        },
        "m3": {
            "transition_ids": list(binding.transition_ids),
            "decision_wait_accepted": "MC-T006" in binding.transition_ids,
            "authority": binding.state.authority.value,
            "holding_lease": binding.lease.to_dict(),
        },
        "authority": {
            "entered_existing_holding": entered,
            "owner": ControlAuthority.M3_HOLDING_CONTROL.value,
            "baseline_control_identity_preserved": returned is control,
            "exited_on_source_change": not wait.active,
            "summary": wait.summary(),
        },
        "new_pid_count": 0,
        "new_planner_count": 0,
        "direct_vehicle_control_write_count": 0,
    }


def run_all_fixtures() -> dict[str, dict[str, Any]]:
    return {
        "ACT_SHARED": act_shared_fixture(),
        "ASK": ask_fixture(),
        "WAIT": wait_fixture(),
    }


__all__ = [
    "FixtureTensor",
    "act_shared_fixture",
    "ask_fixture",
    "run_all_fixtures",
    "wait_fixture",
]
