"""Bounded ASK -> delayed answer -> latest-observation replan orchestration V0.

This module composes the existing language reducers, frozen M3 minimal-core
transitions, and ``PhysicalWaitExecutorV0`` lease receipt.  It never constructs
or returns vehicle control.  The MC-T004 ASK state is the authoritative query
lifecycle; the same query identity is attached to the already verified MC-T006
holding receipt consumed by the physical WAIT executor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from driveclarify_language.interaction_contracts import (
    AnswerStatus,
    EpisodeState,
    GroundingStatus,
    RuntimeEpisodeInput,
    SymbolicSceneEntity,
)
from driveclarify_language.structured_interaction import (
    AnswerResolver,
    ClarificationQuestionPlanner,
    InteractionEpisodeReducer,
    StructuredAmbiguityParser,
    build_candidates,
)
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

from .physical_wait_v0 import (
    BoundedWaitPilotBinding,
    build_bounded_wait_pilot_binding,
)


ASK_REPLAN_SCHEMA = "driveclarify.ask_delayed_answer_latest_replan.v0"
ASK_ENTRY_SOURCE = "BOUNDED_ASK_DELAYED_ANSWER_REPLANNING_PILOT"
PILOT_CONFIGURATION_ID = "ASK_REPLANNING_V0_PILOT_CONFIGURATION"
DEFAULT_ORACLE_DELAY_S = 1.0
DEFAULT_HOLDING_LEASE_DURATION_S = 8.0
DEFAULT_ORACLE_ANSWER = "option one"
POST_ANSWER_REPLAN_MODE = "SINGLE_RESOLVED_INTERPRETATION_REPEAT_V0"
PASS_STATUS = (
    "PASS_ASK_DELAYED_ORACLE_ANSWER_LATEST_OBSERVATION_REPLANNING_V0_"
    "READY_FOR_LIMITED_CLOSED_LOOP_ACT_ASK_WAIT"
)
BLOCKED_REPLAN_LATENCY = "BLOCKED_REPLAN_LATENCY_EXCEEDS_CURRENT_HOLDING_LEASE"
BLOCKED_LATEST_OBSERVATION = "BLOCKED_POST_ANSWER_REPLAN_NOT_USING_LATEST_OBSERVATION"
BLOCKED_FRESH_REPLAN = "BLOCKED_POST_ANSWER_FRESH_MODEL_REPLAN_UNAVAILABLE"
BLOCKED_ANSWER_BINDING = "BLOCKED_ASK_ANSWER_LIFECYCLE_BINDING"
BLOCKED_SECOND_QUERY = "BLOCKED_SECOND_QUERY_INVARIANT"


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _payload(**changes: Any) -> dict[str, Any]:
    value = {
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
    value.update(changes)
    return value


def _event(
    event_id: str,
    event_type: EventType,
    observed_monotonic_time: float,
    *,
    query_id: str | None,
    simulation_time: float | None,
    **changes: Any,
) -> MinimalM3Event:
    return MinimalM3Event.create(
        event_id=event_id,
        event_type=event_type,
        query_episode_id=query_id,
        source_component=ASK_ENTRY_SOURCE,
        observed_monotonic_time=float(observed_monotonic_time),
        source_simulation_time=simulation_time,
        calendar_utc=None,
        payload=_payload(**changes),
    )


@dataclass
class DeterministicOracleDelaySchedulerV0:
    """One-shot monotonic-wall-clock scheduler; it owns no thread or process."""

    query_id: str
    started_monotonic: float
    delay_s: float
    delivered: bool = False

    def __post_init__(self) -> None:
        if type(self.query_id) is not str or not self.query_id:
            raise ValueError("ORACLE_SCHEDULER_QUERY_ID_REQUIRED")
        if not _finite(self.started_monotonic) or not _finite(self.delay_s):
            raise ValueError("ORACLE_SCHEDULER_FINITE_TIME_REQUIRED")
        if self.delay_s <= 0:
            raise ValueError("ORACLE_DELAY_MUST_BE_NONZERO_POSITIVE")

    @property
    def due_monotonic(self) -> float:
        return self.started_monotonic + self.delay_s

    def poll(self, current_monotonic: float) -> bool:
        if not _finite(current_monotonic):
            raise ValueError("ORACLE_SCHEDULER_CURRENT_TIME_INVALID")
        if self.delivered or float(current_monotonic) < self.due_monotonic:
            return False
        self.delivered = True
        return True


class AskDelayedAnswerReplanningV0:
    """Stateful runtime envelope over existing language and M3 transitions."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        oracle_delay_s: float = DEFAULT_ORACLE_DELAY_S,
        holding_lease_duration_s: float = DEFAULT_HOLDING_LEASE_DURATION_S,
        oracle_answer_text: str = DEFAULT_ORACLE_ANSWER,
    ) -> None:
        self.enabled = bool(enabled)
        self.oracle_delay_s = float(oracle_delay_s)
        self.holding_lease_duration_s = float(holding_lease_duration_s)
        self.oracle_answer_text = str(oracle_answer_text)
        if self.oracle_delay_s <= 0:
            raise ValueError("ASK_REPLAN_ORACLE_DELAY_MUST_BE_POSITIVE")
        if self.holding_lease_duration_s <= self.oracle_delay_s:
            raise ValueError("ASK_REPLAN_LEASE_MUST_EXCEED_ORACLE_DELAY")

        self._run_id: str | None = None
        self._record_source: dict[str, Any] = {}
        self._language_episode: Any = None
        self._language_candidates: tuple[Any, ...] = ()
        self._question: Any = None
        self._answer_resolution: Any = None
        self._resolved_instruction: Any = None
        self._m3_state: MinimalM3State | None = None
        self._m3_events: list[MinimalM3Event] = []
        self._m3_transition_ids: list[str] = []
        self._holding_binding: BoundedWaitPilotBinding | None = None
        self._scheduler: DeterministicOracleDelaySchedulerV0 | None = None
        self._stage = "OFF" if not enabled else "ARMED"
        self._final_status: str | None = None
        self._blockers: list[str] = []
        self._rejected_answer_count = 0
        self._second_query_attempt_count = 0
        self._old_candidate_invalidated_at: float | None = None
        self._old_candidate_invalidated_frame: Any = None
        self._old_candidate_authorized = True
        self._answer_received_monotonic: float | None = None
        self._answer_received_frame: Any = None
        self._answer_received_simulation_time: float | None = None
        self._latest_observation: dict[str, Any] | None = None
        self._replan_started_monotonic: float | None = None
        self._replan_finished_monotonic: float | None = None
        self._replan_result: dict[str, Any] | None = None
        self._sequence: list[str] = []

    @property
    def m3_state(self) -> MinimalM3State | None:
        return self._m3_state

    @property
    def active(self) -> bool:
        return bool(
            self.enabled
            and self._language_episode is not None
            and self._final_status is None
        )

    @property
    def ready_for_latest_observation(self) -> bool:
        return (
            self._answer_received_monotonic is not None
            and self._old_candidate_invalidated_at is not None
            and self._latest_observation is None
            and self._final_status is None
        )

    @property
    def ready_for_replan(self) -> bool:
        return (
            self._latest_observation is not None
            and self._replan_started_monotonic is None
            and self._final_status is None
        )

    @property
    def latest_observation(self) -> Mapping[str, Any] | None:
        return self._latest_observation

    @property
    def resolved_instruction(self) -> Any:
        return self._resolved_instruction

    @property
    def query_id(self) -> str | None:
        return None if self._question is None else self._question.query_id

    @property
    def old_candidate_set_id(self) -> str | None:
        return self._record_source.get("candidate_set_id")

    @staticmethod
    def _language_input(
        *,
        run_id: str,
        source_observation_id: str,
        raw_instruction: str,
        observed_monotonic: float,
        answer_deadline: float,
    ) -> RuntimeEpisodeInput:
        provenance = (ASK_ENTRY_SOURCE, "LIVE_FIXED_AMBIGUITY_FIXTURE_STOP_BRANCH_V0")
        entities = (
            SymbolicSceneEntity(
                entity_type="ROUTE_BRANCH",
                entity_id="interpretation_A_straight_branch",
                display_name="the next intersection after going straight",
                observable_attributes=(("interpretation_id", "A"),),
                ordinal=1,
                supports_slots=("ordered_trigger",),
                symbolic_target_type="ROUTE_BRANCH_ID",
                binding_source="ROUTE_BRANCH_SYMBOL",
                frame_or_semantic_domain="TOPOLOGY_BRANCH",
                grounding_status=GroundingStatus.GROUNDED_ORACLE_FIXTURE,
                provenance=provenance,
            ),
            SymbolicSceneEntity(
                entity_type="ROUTE_BRANCH",
                entity_id="interpretation_B_right_branch",
                display_name="the next intersection after turning right",
                observable_attributes=(("interpretation_id", "B"),),
                ordinal=2,
                supports_slots=("ordered_trigger",),
                symbolic_target_type="ROUTE_BRANCH_ID",
                binding_source="ROUTE_BRANCH_SYMBOL",
                frame_or_semantic_domain="TOPOLOGY_BRANCH",
                grounding_status=GroundingStatus.GROUNDED_ORACLE_FIXTURE,
                provenance=provenance,
            ),
        )
        return RuntimeEpisodeInput(
            episode_id=f"{run_id}:language-ambiguity",
            instruction_id=f"{run_id}:instruction",
            raw_instruction=raw_instruction,
            source_observation_id=source_observation_id,
            symbolic_scene_entities=entities,
            allowed_task_vocabulary=("MANEUVER_BRANCH",),
            observed_at_monotonic=float(observed_monotonic),
            answer_deadline_monotonic=float(answer_deadline),
            episode_metadata=(("pilot", PILOT_CONFIGURATION_ID),),
            provenance=provenance,
        )

    def start(
        self,
        *,
        run_id: str,
        record: Mapping[str, Any],
        start_monotonic: float,
        frame: Any,
        simulation_time: float | None,
        rgb_source_identity: Mapping[str, Any],
    ) -> BoundedWaitPilotBinding:
        if not self.enabled:
            raise RuntimeError("ASK_REPLAN_FEATURE_DISABLED")
        if self._language_episode is not None:
            self._second_query_attempt_count += 1
            raise RuntimeError(BLOCKED_SECOND_QUERY)
        source = dict(record.get("source_identity", {}))
        required = ("source_observation_id", "source_frame_id", "candidate_set_id")
        if any(type(source.get(name)) not in (str, int) for name in required):
            raise ValueError("ASK_REPLAN_SOURCE_IDENTITY_REQUIRED")
        if int(source["source_frame_id"]) != int(frame):
            raise ValueError("ASK_REPLAN_SOURCE_FRAME_MISMATCH")
        if not _finite(start_monotonic):
            raise ValueError("ASK_REPLAN_START_TIME_INVALID")

        self._run_id = run_id
        self._record_source = {
            **source,
            "rgb_source_identity": dict(rgb_source_identity),
            "natural_m2b_action": record.get("m2b", {}).get("producer_action"),
        }
        deadline = float(start_monotonic) + self.holding_lease_duration_s
        runtime_input = self._language_input(
            run_id=run_id,
            source_observation_id=str(source["source_observation_id"]),
            raw_instruction=str(record.get("instruction", {}).get("raw", "")),
            observed_monotonic=float(start_monotonic),
            answer_deadline=deadline,
        )
        reducer = InteractionEpisodeReducer()
        parsed = StructuredAmbiguityParser().parse(runtime_input)
        candidates = build_candidates(parsed, runtime_input)
        episode = reducer.parsed(reducer.new(runtime_input), parsed)
        episode = reducer.candidates_ready(episode, candidates)
        question = ClarificationQuestionPlanner().propose(episode, float(start_monotonic))
        episode = reducer.query_proposed(episode, question)
        episode = reducer.query_issued(episode, question)
        if episode.episode_state is not EpisodeState.QUERY_ISSUED:
            raise RuntimeError(BLOCKED_ANSWER_BINDING)
        if question.query_id is None or episode.active_query_id != question.query_id:
            raise RuntimeError(BLOCKED_ANSWER_BINDING)

        initial = MinimalM3State(
            baseline_authority_eligible=True,
            current_time_monotonic=float(start_monotonic),
        )
        ready_event = _event(
            f"{run_id}:ask-candidates-ready",
            EventType.CANDIDATES_READY,
            float(start_monotonic),
            query_id=None,
            simulation_time=simulation_time,
            candidate_set_id=str(source["candidate_set_id"]),
            candidate_freshness="FRESH",
            answer_deadline_monotonic=deadline,
            decision_deadline_monotonic=deadline,
        )
        ready = reduce_event(initial, ready_event, float(start_monotonic))
        ask_event = _event(
            f"{run_id}:bounded-pilot-decision-ask",
            EventType.DECISION_ASK,
            float(start_monotonic),
            query_id=question.query_id,
            simulation_time=simulation_time,
        )
        asked = reduce_event(ready.state, ask_event, float(start_monotonic))
        if (
            ready.transition_ids != ("MC-T001",)
            or asked.transition_ids != ("MC-T004",)
            or asked.processing_results != (ProcessingResult.PROCESSED,)
            or asked.state.lifecycle_state is not LifecycleState.QUERY_ACTIVE
            or asked.state.query_active is not True
            or asked.state.authority is not ControlAuthority.BASELINE_CONTROL
        ):
            raise RuntimeError(BLOCKED_ANSWER_BINDING)

        holding = build_bounded_wait_pilot_binding(
            run_id=run_id,
            source_observation_id=str(source["source_observation_id"]),
            source_frame_id=str(source["source_frame_id"]),
            candidate_set_id=str(source["candidate_set_id"]),
            start_monotonic_time=float(start_monotonic),
            duration_s=self.holding_lease_duration_s,
            source_simulation_time=simulation_time,
            query_episode_id=question.query_id,
        )
        if holding.query_episode_id != asked.state.query_episode_id:
            raise RuntimeError("ASK_WAIT_QUERY_IDENTITY_MISMATCH")

        self._language_episode = episode
        self._language_candidates = tuple(candidates)
        self._question = question
        self._m3_state = asked.state
        self._m3_events = [ready_event, ask_event]
        self._m3_transition_ids = [*ready.transition_ids, *asked.transition_ids]
        self._holding_binding = holding
        self._scheduler = DeterministicOracleDelaySchedulerV0(
            question.query_id,
            float(start_monotonic),
            self.oracle_delay_s,
        )
        self._stage = "ASK_SENT_WAIT_ACTIVE"
        self._sequence.extend(("AMBIGUITY", "ASK", "WAIT"))
        return holding

    def attempt_second_query(self) -> bool:
        """Exercise the existing single-active query guard without mutating state."""

        self._second_query_attempt_count += 1
        if self._m3_state is None or self._question is None:
            return False
        event = _event(
            f"{self._run_id}:second-ask-rejected:{self._second_query_attempt_count}",
            EventType.DECISION_ASK,
            self._m3_state.current_time_monotonic,
            query_id=f"{self._question.query_id}:second",
            simulation_time=None,
        )
        result = reduce_event(
            self._m3_state,
            event,
            self._m3_state.current_time_monotonic,
        )
        return (
            result.transition_ids == ("MC-T005",)
            and result.state.query_episode_id == self._question.query_id
            and result.state.query_active is True
        )

    def poll_oracle_answer(
        self,
        *,
        current_monotonic: float,
        frame: Any,
        simulation_time: float | None,
    ) -> bool:
        if self._scheduler is None or not self._scheduler.poll(current_monotonic):
            return False
        return self.accept_oracle_answer(
            query_id=self._scheduler.query_id,
            answer_text=self.oracle_answer_text,
            current_monotonic=float(current_monotonic),
            frame=frame,
            simulation_time=simulation_time,
        )

    def accept_oracle_answer(
        self,
        *,
        query_id: str,
        answer_text: str,
        current_monotonic: float,
        frame: Any,
        simulation_time: float | None,
    ) -> bool:
        if (
            self._language_episode is None
            or self._question is None
            or self._m3_state is None
            or query_id != self._language_episode.active_query_id
            or self._language_episode.episode_state is not EpisodeState.QUERY_ISSUED
        ):
            self._rejected_answer_count += 1
            return False
        lease = self._holding_binding.lease if self._holding_binding is not None else None
        if lease is None or float(current_monotonic) >= lease.expires_monotonic_time:
            # A missed answer deadline is fail-closed as well: C0 was only a
            # pre-answer clarification candidate set and must never become
            # actionable merely because the bounded holding lease elapsed.
            self._old_candidate_authorized = False
            self._old_candidate_invalidated_at = float(current_monotonic)
            self._old_candidate_invalidated_frame = frame
            self._sequence.append("LEASE_EXPIRED_INVALIDATE")
            self._rejected_answer_count += 1
            self._blockers.append(BLOCKED_REPLAN_LATENCY)
            self._final_status = BLOCKED_REPLAN_LATENCY
            self._stage = "ANSWER_REJECTED_AFTER_EXPIRY"
            return False

        resolution = AnswerResolver().resolve(
            self._question,
            answer_text,
            self._language_episode.episode_state,
            float(current_monotonic),
            self._language_episode.answer_deadline_monotonic,
        )
        if resolution.status is not AnswerStatus.RESOLVED:
            self._rejected_answer_count += 1
            self._blockers.append(BLOCKED_ANSWER_BINDING)
            self._final_status = BLOCKED_ANSWER_BINDING
            self._stage = "ANSWER_REJECTED"
            return False
        reduced_episode, resolved = InteractionEpisodeReducer.apply_answer(
            self._language_episode,
            resolution,
        )
        if (
            resolved is None
            or not resolved.requires_latest_observation_replan
            or resolved.cached_pre_answer_plan_reusable
        ):
            raise RuntimeError(BLOCKED_ANSWER_BINDING)

        # Highest-priority post-acceptance invariant: revoke C0 before any new
        # observation is read or any replan is started.
        self._answer_resolution = resolution
        self._language_episode = reduced_episode
        self._resolved_instruction = resolved
        self._old_candidate_authorized = False
        self._old_candidate_invalidated_at = float(current_monotonic)
        self._old_candidate_invalidated_frame = frame
        self._sequence.extend(("ANSWER", "INVALIDATE"))

        answer_event = _event(
            f"{self._run_id}:answer-arrived",
            EventType.ANSWER_ARRIVED,
            float(current_monotonic),
            query_id=query_id,
            simulation_time=simulation_time,
            answer_present=True,
            candidate_set_id=self.old_candidate_set_id,
            candidate_freshness="STALE",
        )
        answer = reduce_event(self._m3_state, answer_event, float(current_monotonic))
        if (
            answer.transition_ids != ("MC-T009",)
            or answer.processing_results != (ProcessingResult.PROCESSED,)
            or answer.state.lifecycle_state is not LifecycleState.REVALIDATING
            or answer.state.candidate_freshness != "STALE"
        ):
            raise RuntimeError(BLOCKED_ANSWER_BINDING)
        self._m3_state = answer.state
        self._m3_events.append(answer_event)
        self._m3_transition_ids.extend(answer.transition_ids)
        self._answer_received_monotonic = float(current_monotonic)
        self._answer_received_frame = frame
        self._answer_received_simulation_time = simulation_time
        self._stage = "ANSWER_RECEIVED_OLD_CANDIDATES_INVALIDATED"
        return True

    def capture_latest_observation(
        self,
        *,
        observation_id: str,
        frame: Any,
        simulation_time: float | None,
        captured_monotonic: float,
        rgb_source_identity: Mapping[str, Any],
        environment_digest: str | None,
    ) -> bool:
        if not self.ready_for_latest_observation:
            return False
        if self._old_candidate_authorized or self._old_candidate_invalidated_at is None:
            raise RuntimeError("OLD_CANDIDATE_INVALIDATION_MUST_PRECEDE_OBSERVATION")
        old_frame = int(self._record_source["source_frame_id"])
        if frame is None or int(frame) <= old_frame:
            return False
        if observation_id == self._record_source["source_observation_id"]:
            return False
        identity = dict(rgb_source_identity)
        if identity.get("status") != "AVAILABLE":
            return False
        self._latest_observation = {
            "observation_id": str(observation_id),
            "frame_id": int(frame),
            "simulation_time": simulation_time,
            "captured_monotonic": float(captured_monotonic),
            "rgb_source_identity": identity,
            "environment_digest": environment_digest,
            "captured_after_old_candidate_invalidation": (
                float(captured_monotonic) >= self._old_candidate_invalidated_at
            ),
        }
        self._stage = "LATEST_OBSERVATION_CAPTURED"
        self._sequence.append("LATEST_FRAME")
        return True

    def begin_replan(
        self,
        *,
        current_monotonic: float,
        simulation_time: float | None,
    ) -> bool:
        if not self.ready_for_replan or self._m3_state is None:
            return False
        lease = self._holding_binding.lease if self._holding_binding is not None else None
        if lease is None or float(current_monotonic) >= lease.expires_monotonic_time:
            self._blockers.append(BLOCKED_REPLAN_LATENCY)
            self._final_status = BLOCKED_REPLAN_LATENCY
            self._stage = "REPLAN_BLOCKED_LEASE_EXPIRED"
            return False
        event = _event(
            f"{self._run_id}:revalidation-passed",
            EventType.REVALIDATION_PASSED,
            float(current_monotonic),
            query_id=self.query_id,
            simulation_time=simulation_time,
            candidate_set_id=self.old_candidate_set_id,
            candidate_freshness="STALE",
        )
        result = reduce_event(self._m3_state, event, float(current_monotonic))
        if result.transition_ids != ("MC-T017",):
            raise RuntimeError(BLOCKED_ANSWER_BINDING)
        self._m3_state = result.state
        self._m3_events.append(event)
        self._m3_transition_ids.extend(result.transition_ids)
        self._replan_started_monotonic = float(current_monotonic)
        self._stage = "REPLANNING_FROM_LATEST_OBSERVATION"
        self._sequence.append("REPLAN")
        return True

    def complete_replan(
        self,
        result: Mapping[str, Any],
        *,
        finished_monotonic: float,
        simulation_time: float | None,
    ) -> bool:
        if self._m3_state is None or self._latest_observation is None:
            raise RuntimeError(BLOCKED_FRESH_REPLAN)
        self._replan_finished_monotonic = float(finished_monotonic)
        lease = self._holding_binding.lease if self._holding_binding is not None else None
        if lease is None or self._replan_finished_monotonic > lease.expires_monotonic_time:
            self._blockers.append(BLOCKED_REPLAN_LATENCY)
            self._final_status = BLOCKED_REPLAN_LATENCY
            self._stage = "REPLAN_COMPLETE_AFTER_LEASE_EXPIRY_BLOCKED"
            return False
        value = dict(result)
        if (
            value.get("source_observation_id")
            != self._latest_observation["observation_id"]
            or int(value.get("source_frame_id", -1))
            != int(self._latest_observation["frame_id"])
        ):
            self._blockers.append(BLOCKED_LATEST_OBSERVATION)
            self._final_status = BLOCKED_LATEST_OBSERVATION
            self._stage = "REPLAN_SOURCE_REJECTED"
            return False
        if (
            not value.get("fresh_model_computation")
            or int(value.get("post_answer_model_forward_count", 0)) <= 0
            or value.get("plan_id") == self.old_candidate_set_id
        ):
            self._blockers.append(BLOCKED_FRESH_REPLAN)
            self._final_status = BLOCKED_FRESH_REPLAN
            self._stage = "REPLAN_FRESHNESS_REJECTED"
            return False
        event = _event(
            f"{self._run_id}:fresh-replan-complete",
            EventType.REPLAN_COMPLETE,
            self._replan_finished_monotonic,
            query_id=self.query_id,
            simulation_time=simulation_time,
            candidate_set_id=str(value["plan_id"]),
            candidate_freshness="FRESH",
        )
        reduced = reduce_event(
            self._m3_state,
            event,
            self._replan_finished_monotonic,
        )
        if (
            reduced.transition_ids != ("MC-T019",)
            or reduced.state.lifecycle_state is not LifecycleState.RESUME_READY
            or reduced.state.authority is not ControlAuthority.BASELINE_CONTROL
        ):
            raise RuntimeError(BLOCKED_FRESH_REPLAN)
        self._m3_state = reduced.state
        self._m3_events.append(event)
        self._m3_transition_ids.extend(reduced.transition_ids)
        self._replan_result = value
        self._final_status = PASS_STATUS
        self._stage = "REPLAN_COMPLETE_RESUME_READY"
        self._sequence.append("READY")
        return True

    def fail_replan(self, blocker: str = BLOCKED_FRESH_REPLAN) -> None:
        if blocker not in self._blockers:
            self._blockers.append(blocker)
        self._final_status = blocker
        self._stage = "REPLAN_FAILED_CLOSED"

    def authorize_candidate_commit(self, candidate_set_id: str) -> bool:
        return bool(
            candidate_set_id != self.old_candidate_set_id
            and self._final_status == PASS_STATUS
            and False  # This V0 never authorizes candidate ACT.
        )

    def summary(self, *, current_monotonic: float | None = None) -> dict[str, Any]:
        now = float(current_monotonic) if _finite(current_monotonic) else None
        lease = self._holding_binding.lease if self._holding_binding is not None else None
        question = self._question
        resolved = self._resolved_instruction
        answer = self._answer_resolution
        latest = self._latest_observation
        old_rgb = dict(self._record_source.get("rgb_source_identity", {}))
        new_rgb = dict(latest.get("rgb_source_identity", {})) if latest else {}
        replan_ms = (
            None
            if self._replan_started_monotonic is None or self._replan_finished_monotonic is None
            else (self._replan_finished_monotonic - self._replan_started_monotonic) * 1000.0
        )
        actual_delay = (
            None
            if self._scheduler is None or self._answer_received_monotonic is None
            else self._answer_received_monotonic - self._scheduler.started_monotonic
        )
        return {
            "schema_version": ASK_REPLAN_SCHEMA,
            "enabled": self.enabled,
            "stage": self._stage,
            "final_status": self._final_status,
            "blockers": list(dict.fromkeys(self._blockers)),
            "pilot_configuration_id": PILOT_CONFIGURATION_ID,
            "pilot_parameter_semantics": "PILOT_CONFIGURATION_NOT_SAFETY_THRESHOLD",
            "ask_entry_source": ASK_ENTRY_SOURCE,
            "natural_m2b_action": self._record_source.get("natural_m2b_action"),
            "m2b_naturally_selected_ask": (
                self._record_source.get("natural_m2b_action") == "ASK"
            ),
            "query": {
                "query_id": None if question is None else question.query_id,
                "active_query": bool(
                    self._language_episode is not None
                    and self._language_episode.active_query_id is not None
                ),
                "active_query_count": int(
                    self._language_episode is not None
                    and self._language_episode.active_query_id is not None
                ),
                "question": None if question is None else question.question_text,
                "question_payload": None if question is None else question.to_dict(),
                "expected_answer_domain": (
                    [] if question is None else ["OPTION_ONE", "OPTION_TWO"]
                ),
                "query_start_frame": self._record_source.get("source_frame_id"),
                "query_start_observation_id": self._record_source.get("source_observation_id"),
                "candidate_set_c0": self.old_candidate_set_id,
                "answer_deadline_monotonic": None if lease is None else lease.expires_monotonic_time,
                "second_query_attempt_count": self._second_query_attempt_count,
                "second_query_created_count": 0,
                "rejected_answer_count": self._rejected_answer_count,
            },
            "oracle": {
                "configured_delay_s": self.oracle_delay_s,
                "actual_delay_s": actual_delay,
                "answer_text": None if answer is None else answer.normalized_answer,
                "answer_status": None if answer is None else answer.status.value,
                "selected_language_candidate_id": (
                    None if answer is None else answer.selected_candidate_id
                ),
                "selected_interpretation_id": (
                    None
                    if resolved is None
                    else dict(
                        resolved.candidate_specific_task_binding.provenance
                        and next(
                            candidate.grounding_evidence.observable_attributes
                            for candidate in self._language_candidates
                            if candidate.candidate_id == resolved.resolved_candidate_id
                        )
                    ).get("interpretation_id")
                ),
                "answer_received_monotonic": self._answer_received_monotonic,
                "answer_received_frame": self._answer_received_frame,
                "answer_received_simulation_time": self._answer_received_simulation_time,
            },
            "language_binding": {
                "episode_state": (
                    None if self._language_episode is None else self._language_episode.episode_state.value
                ),
                "candidate_interpretations": [
                    item.to_dict() for item in self._language_candidates
                ],
                "answer_resolution": None if answer is None else answer.to_dict(),
                "resolved_instruction": None if resolved is None else resolved.to_dict(),
                "cached_pre_answer_plan_reusable": (
                    None if resolved is None else resolved.cached_pre_answer_plan_reusable
                ),
            },
            "old_candidate_invalidation": {
                "old_candidate_set_id": self.old_candidate_set_id,
                "old_candidate_generation_frame": self._record_source.get("source_frame_id"),
                "old_candidate_invalidated_at": self._old_candidate_invalidated_at,
                "old_candidate_invalidated_frame": self._old_candidate_invalidated_frame,
                "old_candidate_authorized": self._old_candidate_authorized,
                "old_candidate_status": (
                    "STALE_NOT_AUTHORIZED_FOR_ACT"
                    if not self._old_candidate_authorized
                    else "PRE_ANSWER_ONLY"
                ),
                "invalidated_before_latest_observation": bool(
                    latest
                    and latest["captured_monotonic"] >= self._old_candidate_invalidated_at
                ) if self._old_candidate_invalidated_at is not None else False,
                "invalidated_before_replan": bool(
                    self._replan_started_monotonic is not None
                    and self._old_candidate_invalidated_at is not None
                    and self._old_candidate_invalidated_at <= self._replan_started_monotonic
                ),
                "post_answer_old_candidate_commit_count": 0,
                "post_answer_old_candidate_control_writes": 0,
            },
            "latest_observation": {
                "old_observation_id": self._record_source.get("source_observation_id"),
                "old_frame_id": self._record_source.get("source_frame_id"),
                "answer_frame_id": self._answer_received_frame,
                "new_observation_id": None if latest is None else latest["observation_id"],
                "new_frame_id": None if latest is None else latest["frame_id"],
                "old_rgb_source_identity": old_rgb,
                "new_rgb_source_identity": new_rgb,
                "rgb_source_identity_changed": bool(old_rgb and new_rgb and old_rgb != new_rgb),
                "freshness_verdict": (
                    "LATEST_POST_ANSWER_OBSERVATION"
                    if latest is not None
                    and int(latest["frame_id"]) > int(self._record_source["source_frame_id"])
                    else "NOT_YET_PROVEN"
                ),
            },
            "replan": {
                "mode": POST_ANSWER_REPLAN_MODE,
                "resolved_interpretation_id": (
                    None if self._replan_result is None else self._replan_result.get("resolved_interpretation_id")
                ),
                "post_answer_model_forward_count": (
                    0 if self._replan_result is None else self._replan_result.get("post_answer_model_forward_count", 0)
                ),
                "plan_id": None if self._replan_result is None else self._replan_result.get("plan_id"),
                "route_digest": None if self._replan_result is None else self._replan_result.get("route_digest"),
                "speed_digest": None if self._replan_result is None else self._replan_result.get("speed_digest"),
                "source_observation_id": None if self._replan_result is None else self._replan_result.get("source_observation_id"),
                "source_frame_id": None if self._replan_result is None else self._replan_result.get("source_frame_id"),
                "fresh_model_computation": bool(
                    self._replan_result and self._replan_result.get("fresh_model_computation")
                ),
                "replan_started_monotonic": self._replan_started_monotonic,
                "replan_finished_monotonic": self._replan_finished_monotonic,
                "replan_total_ms": replan_ms,
                "details": self._replan_result,
            },
            "lease_timing": {
                "lease": None if lease is None else lease.to_dict(),
                "lease_start_monotonic": None if lease is None else lease.issued_monotonic_time,
                "lease_expiry_monotonic": None if lease is None else lease.expires_monotonic_time,
                "remaining_lease_at_answer_s": (
                    None
                    if lease is None or self._answer_received_monotonic is None
                    else lease.expires_monotonic_time - self._answer_received_monotonic
                ),
                "replan_completed_before_expiry": bool(
                    lease is not None
                    and self._replan_finished_monotonic is not None
                    and self._replan_finished_monotonic <= lease.expires_monotonic_time
                ),
                "lease_renewal": False,
                "lease_extension": False,
                "current_remaining_s": (
                    None if lease is None or now is None else max(0.0, lease.expires_monotonic_time - now)
                ),
            },
            "m3_lifecycle": {
                "authoritative_query_path": "MC-T001->MC-T004->MC-T009->MC-T017->MC-T019",
                "physical_wait_receipt_path": "MC-T001->MC-T006",
                "composition": "SAME_QUERY_ID_EXISTING_TRANSITIONS_NO_M3_REDUCER_MODIFICATION",
                "transition_ids": list(self._m3_transition_ids),
                "events": [event.to_dict() for event in self._m3_events],
                "exact_state": None if self._m3_state is None else self._m3_state.lifecycle_state.value,
                "authority": None if self._m3_state is None else self._m3_state.authority.value,
                "query_active": None if self._m3_state is None else self._m3_state.query_active,
                "candidate_freshness": None if self._m3_state is None else self._m3_state.candidate_freshness,
                "holding_binding": None if self._holding_binding is None else self._holding_binding.to_dict(),
                "m3_minimal_core_modified": False,
            },
            "timeline": list(self._sequence),
            "control_isolation": {
                "driveclarify_low_level_control_writes": 0,
                "m3_control_writes": 0,
                "post_answer_candidate_control_writes": 0,
                "text_to_actuator_shortcut_count": 0,
                "candidate_act_authorized": False,
                "control_owner": "BASELINE_SIMLINGO_CURRENT_VALID_PLAN",
            },
            "invariants": {
                "single_active_query": self._second_query_attempt_count == 0,
                "old_candidate_invalidated_before_observation": bool(
                    latest and latest.get("captured_after_old_candidate_invalidation")
                ),
                "old_candidate_invalidated_before_replan": bool(
                    self._old_candidate_invalidated_at is not None
                    and self._replan_started_monotonic is not None
                    and self._old_candidate_invalidated_at <= self._replan_started_monotonic
                ),
                "old_candidate_cannot_act": not self._old_candidate_authorized,
                "new_frame_after_old_frame": bool(
                    latest
                    and int(latest["frame_id"]) > int(self._record_source["source_frame_id"])
                ),
                "fresh_inference_used": bool(
                    self._replan_result and self._replan_result.get("fresh_model_computation")
                ),
                "no_lease_renewal": True,
                "no_control_write": True,
            },
        }
