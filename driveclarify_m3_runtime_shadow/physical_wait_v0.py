"""Bounded physical WAIT V0: keep the current baseline plan and block commits.

This module is deliberately not a controller.  It consumes the frozen M3
``HoldingLease`` contract, records physical progress, and gates
interpretation-specific candidate commitment while returning no replacement
route, PID result, or ``VehicleControl``.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Mapping

from driveclarify_m3_minimal_core import (
    ControlAuthority,
    EventType,
    EvidenceGrade,
    HoldingLease,
    LifecycleState,
    MinimalM3Event,
    MinimalM3State,
    ProcessingResult,
    lease_temporally_valid,
    reduce_event,
)


PHYSICAL_WAIT_SCHEMA = "driveclarify.physical_wait_holding.v0"
PHYSICAL_WAIT_MODE = "HOLD_CURRENT_VALID_PLAN"
WAIT_ENTRY_SOURCE = "BOUNDED_PHYSICAL_HOLDING_PILOT"
PILOT_CONFIGURATION_ID = "PHYSICAL_WAIT_V0_PILOT_4S"
DEFAULT_PILOT_DURATION_S = 4.0
ACTIVE_M3_TRANSITION = "MC-T006"
EXPIRY_M3_TRANSITION = "MC-T022"
CONTROL_OWNER = "BASELINE_SIMLINGO_CURRENT_VALID_PLAN"
OLD_CANDIDATES_EXIT_STATUS = "OLD_CANDIDATES_NOT_AUTHORIZED_AFTER_WAIT_EXIT"

_VERIFIED = EvidenceGrade.VERIFIED_FROM_CONTROLLED_PROBE.value


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
        "baseline_authority_eligible": None,
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
    query_episode_id: str | None,
    source_simulation_time: float | None,
    **payload_changes: Any,
) -> MinimalM3Event:
    return MinimalM3Event.create(
        event_id=event_id,
        event_type=event_type,
        query_episode_id=query_episode_id,
        source_component=WAIT_ENTRY_SOURCE,
        observed_monotonic_time=observed_monotonic_time,
        source_simulation_time=source_simulation_time,
        calendar_utc=None,
        payload=_payload(**payload_changes),
    )


@dataclass(frozen=True)
class BoundedWaitPilotBinding:
    """Existing M3 lifecycle result used by the runtime execution envelope."""

    state: MinimalM3State
    lease: HoldingLease
    query_episode_id: str
    transition_ids: tuple[str, ...]
    events: tuple[MinimalM3Event, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_source": WAIT_ENTRY_SOURCE,
            "query_episode_id": self.query_episode_id,
            "transition_ids": list(self.transition_ids),
            "lease": self.lease.to_dict(),
            "m3_state": self.state.to_dict(),
            "events": [event.to_dict() for event in self.events],
        }


def build_bounded_wait_pilot_binding(
    *,
    run_id: str,
    source_observation_id: str,
    source_frame_id: str,
    candidate_set_id: str,
    start_monotonic_time: float,
    duration_s: float = DEFAULT_PILOT_DURATION_S,
    source_simulation_time: float | None = None,
    query_episode_id: str | None = None,
) -> BoundedWaitPilotBinding:
    """Create a marked pilot through the existing MC-T001/MC-T006 reducer path."""

    if not all(
        type(value) is str and bool(value)
        for value in (run_id, source_observation_id, source_frame_id, candidate_set_id)
    ):
        raise ValueError("PHYSICAL_WAIT_PILOT_IDENTITY_REQUIRED")
    if not _finite(start_monotonic_time) or not _finite(duration_s) or duration_s <= 0:
        raise ValueError("PHYSICAL_WAIT_PILOT_FINITE_POSITIVE_DURATION_REQUIRED")
    start = float(start_monotonic_time)
    duration = float(duration_s)
    expiry = start + duration
    if not math.isfinite(expiry):
        raise ValueError("PHYSICAL_WAIT_PILOT_EXPIRY_NOT_FINITE")
    query_episode_id = query_episode_id or f"{run_id}:physical-wait-pilot"
    if type(query_episode_id) is not str or not query_episode_id:
        raise ValueError("PHYSICAL_WAIT_PILOT_QUERY_ID_REQUIRED")
    lease = HoldingLease(
        lease_id=f"{run_id}:holding-lease-v0",
        issued_monotonic_time=start,
        next_reevaluation_monotonic_time=start + duration / 2.0,
        expires_monotonic_time=expiry,
        maximum_expiry_monotonic_time=expiry,
        evidence_grade=EvidenceGrade.VERIFIED_FROM_CONTROLLED_PROBE,
        source_observation_id=source_observation_id,
        source_frame_id=source_frame_id,
        candidate_set_id=candidate_set_id,
        revoked=False,
        revocation_reason=None,
        authority_on_exit=ControlAuthority.BASELINE_CONTROL,
    )
    ready_event = _event(
        f"{run_id}:pilot-candidates-ready",
        EventType.CANDIDATES_READY,
        start,
        query_episode_id=None,
        source_simulation_time=source_simulation_time,
        candidate_set_id=candidate_set_id,
        candidate_freshness="FRESH",
        answer_deadline_monotonic=expiry,
        decision_deadline_monotonic=expiry,
        baseline_authority_eligible=True,
    )
    initial = MinimalM3State(
        baseline_authority_eligible=True,
        current_time_monotonic=start,
    )
    ready = reduce_event(initial, ready_event, start)
    if ready.transition_ids != ("MC-T001",) or ready.processing_results != (
        ProcessingResult.PROCESSED,
    ):
        raise RuntimeError("PHYSICAL_WAIT_PILOT_CANDIDATES_READY_REJECTED")
    wait_event = _event(
        f"{run_id}:pilot-decision-wait",
        EventType.DECISION_WAIT,
        start,
        query_episode_id=query_episode_id,
        source_simulation_time=source_simulation_time,
        holding_evidence_grade=_VERIFIED,
        lease=lease.to_dict(),
        physical_mode_ready=True,
        baseline_authority_eligible=True,
    )
    waiting = reduce_event(ready.state, wait_event, start)
    if waiting.transition_ids != (ACTIVE_M3_TRANSITION,) or waiting.processing_results != (
        ProcessingResult.PROCESSED,
    ):
        raise RuntimeError("PHYSICAL_WAIT_PILOT_EXISTING_M3_WAIT_REJECTED")
    if waiting.state.holding_lease != lease:
        raise RuntimeError("PHYSICAL_WAIT_PILOT_LEASE_BINDING_MISMATCH")
    return BoundedWaitPilotBinding(
        state=waiting.state,
        lease=lease,
        query_episode_id=query_episode_id,
        transition_ids=ready.transition_ids + waiting.transition_ids,
        events=(ready_event, wait_event),
    )


def _control_values(control: Any) -> dict[str, Any]:
    return {
        name: getattr(control, name)
        for name in (
            "steer",
            "throttle",
            "brake",
            "hand_brake",
            "reverse",
            "manual_gear_shift",
            "gear",
        )
        if hasattr(control, name)
    }


def _position(value: Any) -> tuple[float, float, float] | None:
    if value is None:
        return None
    try:
        if isinstance(value, Mapping):
            result = (
                float(value["x"]),
                float(value["y"]),
                float(value.get("z", 0.0)),
            )
        else:
            result = (float(value[0]), float(value[1]), float(value[2]))
        return result if all(math.isfinite(axis) for axis in result) else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


class PhysicalWaitExecutorV0:
    """Lease manager and commitment gate with zero low-level control writes."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = bool(enabled)
        self.active = False
        self._state: MinimalM3State | None = None
        self._binding: BoundedWaitPilotBinding | None = None
        self._entry_rejection: str | None = None
        self._exit_reason: str | None = None
        self._entry_time: float | None = None
        self._exit_time: float | None = None
        self._entry_frame: Any = None
        self._exit_frame: Any = None
        self._entry_position: tuple[float, float, float] | None = None
        self._exit_position: tuple[float, float, float] | None = None
        self._last_position: tuple[float, float, float] | None = None
        self._entry_speed: float | None = None
        self._exit_speed: float | None = None
        self._speeds: list[float] = []
        self._frames: list[Any] = []
        self._distance_m = 0.0
        self._baseline_controls: list[dict[str, Any]] = []
        self._actual_control_comparisons: list[dict[str, Any]] = []
        self._candidate_commit_attempt_count = 0
        self._candidate_commit_blocked_count = 0
        self._candidate_commit_count = 0
        self._outside_wait_authorized_commit_count = 0
        self._unauthorized_candidate_sets: set[str] = set()
        self._tick_overheads_ms: list[float] = []
        self._m3_lookup_overheads_ms: list[float] = []
        self._entry_environment_digest: str | None = None
        self._exit_environment_digest: str | None = None
        self._lifecycle_transition_ids: list[str] = []

    @property
    def state(self) -> MinimalM3State | None:
        return self._state

    def enter(
        self,
        binding: BoundedWaitPilotBinding,
        *,
        current_monotonic_time: float,
        frame: Any,
        ego_position: Any,
        ego_speed_mps: Any,
        baseline_control_path_healthy: bool,
        no_control_ownership_conflict: bool,
        native_carla_session_available: bool,
        source_identity_recordable: bool,
        environment_digest: str | None = None,
    ) -> bool:
        started = time.monotonic_ns()
        rejection = None
        state = binding.state
        lease = state.holding_lease
        position = _position(ego_position)
        speed = self._safe_speed(ego_speed_mps)
        if not self.enabled:
            rejection = "PHYSICAL_WAIT_FEATURE_DISABLED"
        elif self.active:
            rejection = "PHYSICAL_WAIT_ALREADY_ACTIVE"
        elif ACTIVE_M3_TRANSITION not in binding.transition_ids:
            rejection = "EXISTING_M3_WAIT_TRANSITION_REQUIRED"
        elif state.lifecycle_state is not LifecycleState.QUERY_ACTIVE:
            rejection = "M3_NOT_IN_WAIT_LIFECYCLE_STATE"
        elif state.authority is not ControlAuthority.M3_HOLDING_CONTROL:
            rejection = "M3_HOLDING_AUTHORITY_NOT_GRANTED"
        elif not isinstance(lease, HoldingLease):
            rejection = "VALID_HOLDING_LEASE_REQUIRED"
        elif not lease_temporally_valid(lease, current_monotonic_time):
            rejection = "HOLDING_LEASE_NOT_CURRENTLY_VALID"
        elif state.candidate_freshness != "FRESH":
            rejection = "SOURCE_CANDIDATES_NOT_FRESH_AT_ENTRY"
        elif not baseline_control_path_healthy:
            rejection = "BASELINE_CONTROL_PATH_NOT_HEALTHY"
        elif not no_control_ownership_conflict:
            rejection = "CONTROL_OWNERSHIP_CONFLICT"
        elif not native_carla_session_available:
            rejection = "NATIVE_CARLA_SESSION_NOT_AVAILABLE"
        elif not source_identity_recordable:
            rejection = "SOURCE_IDENTITY_NOT_RECORDABLE"
        elif position is None or speed is None or frame is None:
            rejection = "PHYSICAL_ENTRY_OBSERVATION_NOT_RECORDABLE"
        if rejection is not None:
            self._entry_rejection = rejection
            self._m3_lookup_overheads_ms.append(
                (time.monotonic_ns() - started) / 1_000_000.0
            )
            return False

        self._binding = binding
        self._state = state
        self.active = True
        self._entry_time = float(current_monotonic_time)
        self._entry_frame = frame
        self._entry_position = position
        self._exit_position = position
        self._last_position = position
        self._entry_speed = speed
        self._exit_speed = speed
        self._speeds = [speed]
        self._frames = [frame]
        self._entry_environment_digest = environment_digest
        self._exit_environment_digest = environment_digest
        self._lifecycle_transition_ids = list(binding.transition_ids)
        self._m3_lookup_overheads_ms.append(
            (time.monotonic_ns() - started) / 1_000_000.0
        )
        return True

    def observe_tick(
        self,
        *,
        frame: Any,
        current_monotonic_time: float,
        ego_position: Any,
        ego_speed_mps: Any,
        baseline_control_path_healthy: bool = True,
        source_runtime_valid: bool = True,
        m3_state: MinimalM3State | None = None,
        environment_digest: str | None = None,
        source_simulation_time: float | None = None,
    ) -> None:
        started = time.monotonic_ns()
        if not self.active:
            return
        position = _position(ego_position)
        speed = self._safe_speed(ego_speed_mps)
        if frame not in self._frames:
            self._frames.append(frame)
        if position is not None:
            if self._last_position is not None:
                self._distance_m += math.hypot(
                    position[0] - self._last_position[0],
                    position[1] - self._last_position[1],
                )
            self._last_position = position
            self._exit_position = position
        if speed is not None:
            self._speeds.append(speed)
            self._exit_speed = speed
        self._exit_environment_digest = environment_digest

        lookup_started = time.monotonic_ns()
        if m3_state is not None:
            self._state = m3_state
        state = self._state
        lease = state.holding_lease if state is not None else None
        self._m3_lookup_overheads_ms.append(
            (time.monotonic_ns() - lookup_started) / 1_000_000.0
        )
        if state is None or state.lifecycle_state is not LifecycleState.QUERY_ACTIVE:
            self._exit("M3_LEFT_WAIT", current_monotonic_time, frame)
        elif not isinstance(lease, HoldingLease) or lease.revoked:
            self._exit("HOLDING_LEASE_INVALIDATED", current_monotonic_time, frame)
        elif state.candidate_freshness != "FRESH" or not source_runtime_valid:
            self._transition_and_exit(
                EventType.WORLD_STATE_CHANGED,
                "SOURCE_OR_FRESHNESS_INVALIDATED",
                current_monotonic_time,
                frame,
                source_simulation_time,
            )
        elif not baseline_control_path_healthy:
            self._transition_and_exit(
                EventType.HOLDING_CAPABILITY_LOST,
                "BASELINE_CONTROL_PATH_FAILURE",
                current_monotonic_time,
                frame,
                source_simulation_time,
            )
        elif current_monotonic_time >= lease.expires_monotonic_time:
            self._transition_and_exit(
                EventType.LEASE_EXPIRY_CHECK,
                "LEASE_EXPIRED",
                current_monotonic_time,
                frame,
                source_simulation_time,
            )
        self._tick_overheads_ms.append(
            (time.monotonic_ns() - started) / 1_000_000.0
        )

    def cancel(
        self,
        *,
        current_monotonic_time: float,
        frame: Any,
        source_simulation_time: float | None = None,
    ) -> None:
        if self.active:
            self._transition_and_exit(
                EventType.QUERY_CANCELLED,
                "EXPLICIT_CANCEL_OR_TEST_COMPLETION",
                current_monotonic_time,
                frame,
                source_simulation_time,
            )

    def authorize_candidate_commit(self, candidate_set_id: str) -> bool:
        self._candidate_commit_attempt_count += 1
        if self.active or candidate_set_id in self._unauthorized_candidate_sets:
            self._candidate_commit_blocked_count += 1
            return False
        self._outside_wait_authorized_commit_count += 1
        return True

    def observe_baseline_control(self, control: Any, *, frame: Any, ready_time: float) -> Any:
        """Record the return-boundary passthrough and return the identical object."""

        if not self.active:
            return control
        before_id = id(control)
        before = _control_values(control)
        after = _control_values(control)
        self._baseline_controls.append(
            {
                "frame": frame,
                "control_ready_monotonic_s": float(ready_time),
                "baseline_control": before,
                "actual_submitted_control": after,
                "object_identity_preserved": before_id == id(control),
                "exact_equal_at_return_boundary": before == after,
                "control_owner": CONTROL_OWNER,
            }
        )
        return control

    def observe_actual_carla_control(self, *, source_frame: Any, actual_control: Any) -> None:
        expected = next(
            (
                item["baseline_control"]
                for item in reversed(self._baseline_controls)
                if item["frame"] == source_frame
            ),
            None,
        )
        if expected is None:
            return
        actual = (
            dict(actual_control)
            if isinstance(actual_control, Mapping)
            else _control_values(actual_control)
        )
        actuator_fields = (
            "steer",
            "throttle",
            "brake",
            "hand_brake",
            "reverse",
            "manual_gear_shift",
        )
        self._actual_control_comparisons.append(
            {
                "returned_control_source_frame": source_frame,
                "baseline_control": expected,
                "actual_carla_control": actual,
                "exact_equal": expected == actual,
                "actuator_equal": all(
                    expected.get(name) == actual.get(name) for name in actuator_fields
                ),
            }
        )

    def _transition_and_exit(
        self,
        event_type: EventType,
        reason: str,
        current_monotonic_time: float,
        frame: Any,
        source_simulation_time: float | None,
    ) -> None:
        if self._state is None or self._binding is None:
            self._exit(reason, current_monotonic_time, frame)
            return
        event = _event(
            f"{self._binding.query_episode_id}:{event_type.value.lower()}",
            event_type,
            float(current_monotonic_time),
            query_episode_id=self._binding.query_episode_id,
            source_simulation_time=source_simulation_time,
        )
        result = reduce_event(self._state, event, float(current_monotonic_time))
        if result.processing_results != (ProcessingResult.PROCESSED,):
            self._exit(reason + "_M3_EVENT_REJECTED", current_monotonic_time, frame)
            return
        self._state = result.state
        self._lifecycle_transition_ids.extend(result.transition_ids)
        if event_type is EventType.LEASE_EXPIRY_CHECK and result.transition_ids != (
            EXPIRY_M3_TRANSITION,
        ):
            self._exit(reason + "_M3_EXPIRY_TRANSITION_MISMATCH", current_monotonic_time, frame)
            return
        self._exit(reason, current_monotonic_time, frame)

    def _exit(self, reason: str, current_monotonic_time: float, frame: Any) -> None:
        self.active = False
        self._exit_reason = reason
        self._exit_time = float(current_monotonic_time)
        self._exit_frame = frame
        if self._binding is not None:
            self._unauthorized_candidate_sets.add(self._binding.lease.candidate_set_id)

    @staticmethod
    def _safe_speed(value: Any) -> float | None:
        try:
            if hasattr(value, "item"):
                value = value.item()
            if isinstance(value, (list, tuple)) and value:
                value = value[0]
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    def summary(self, *, current_monotonic_time: float | None = None) -> dict[str, Any]:
        state = self._state
        lease = (
            state.holding_lease
            if state is not None and state.holding_lease is not None
            else self._binding.lease if self._binding is not None else None
        )
        now = (
            float(current_monotonic_time)
            if _finite(current_monotonic_time)
            else self._exit_time if self._exit_time is not None else time.monotonic()
        )
        elapsed = (
            None
            if self._entry_time is None
            else max(0.0, (self._exit_time if self._exit_time is not None else now) - self._entry_time)
        )
        remaining = (
            None
            if lease is None
            else max(0.0, float(lease.expires_monotonic_time) - now)
        )
        observed = len(self._actual_control_comparisons)
        actuator_equal = sum(
            bool(item["actuator_equal"]) for item in self._actual_control_comparisons
        )
        exact_equal = sum(
            bool(item["exact_equal"]) for item in self._actual_control_comparisons
        )
        return {
            "schema_version": PHYSICAL_WAIT_SCHEMA,
            "enabled": self.enabled,
            "status": (
                "ACTIVE"
                if self.active
                else "EXITED"
                if self._exit_reason is not None
                else "REJECTED"
                if self._entry_rejection is not None
                else "ARMED"
                if self.enabled
                else "OFF"
            ),
            "mode": PHYSICAL_WAIT_MODE,
            "wait_entry_source": WAIT_ENTRY_SOURCE,
            "pilot_configuration_id": PILOT_CONFIGURATION_ID,
            "pilot_parameter_semantics": "PILOT_EXECUTION_PARAMETER_NOT_SAFETY_CONTRACT",
            "query": "INACTIVE_NO_PASSENGER_QUERY_IN_THIS_PILOT",
            "control_owner": CONTROL_OWNER,
            "entry_rejection": self._entry_rejection,
            "exit_reason": self._exit_reason,
            "lease": None if lease is None else lease.to_dict(),
            "lease_duration_s": (
                None
                if lease is None
                else float(lease.expires_monotonic_time - lease.issued_monotonic_time)
            ),
            "lease_elapsed_s": elapsed,
            "lease_remaining_s": remaining,
            "wait_entry_frame": self._entry_frame,
            "wait_exit_frame": self._exit_frame,
            "current_frame": self._frames[-1] if self._frames else None,
            "wait_entry_monotonic_time": self._entry_time,
            "wait_exit_monotonic_time": self._exit_time,
            "actual_wait_duration_s": elapsed,
            "frames_observed_during_wait": len(self._frames),
            "frame_ids_observed": list(self._frames),
            "carla_ticks_during_wait": len(self._baseline_controls),
            "ego_start_position": self._entry_position,
            "ego_end_position": self._exit_position,
            "distance_travelled_m": self._distance_m,
            "speed_entry_mps": self._entry_speed,
            "speed_exit_mps": self._exit_speed,
            "speed_min_mps": min(self._speeds) if self._speeds else None,
            "speed_max_mps": max(self._speeds) if self._speeds else None,
            "environment_start_digest": self._entry_environment_digest,
            "environment_end_digest": self._exit_environment_digest,
            "environment_continued": bool(
                self._entry_environment_digest
                and self._exit_environment_digest
                and self._entry_environment_digest != self._exit_environment_digest
            ),
            "baseline_controls": list(self._baseline_controls),
            "actual_control_comparisons": list(self._actual_control_comparisons),
            "control_equality_observed_count": observed,
            "control_actuator_equality_count": actuator_equal,
            "control_exact_equality_count": exact_equal,
            "all_observed_actuators_equal": observed > 0 and observed == actuator_equal,
            "candidate_commit_attempt_count": self._candidate_commit_attempt_count,
            "candidate_commit_blocked_count": self._candidate_commit_blocked_count,
            "candidate_commit_count_during_wait": self._candidate_commit_count,
            "outside_wait_authorized_commit_count": self._outside_wait_authorized_commit_count,
            "candidate_commit_status": "BLOCKED / 0" if self.active else "0 COMMITTED",
            "old_candidate_status_at_exit": (
                OLD_CANDIDATES_EXIT_STATUS if self._exit_reason is not None else "NOT_YET_EXITED"
            ),
            "unauthorized_candidate_set_ids": sorted(self._unauthorized_candidate_sets),
            "m3_lifecycle_state": None if state is None else state.lifecycle_state.value,
            "m3_query_slot_active": None if state is None else state.query_active,
            "m3_authority": None if state is None else state.authority.value,
            "m3_candidate_freshness": None if state is None else state.candidate_freshness,
            "m3_transition_ids": list(self._lifecycle_transition_ids),
            "driveclarify_low_level_control_writes": 0,
            "m3_control_writes": 0,
            "extra_model_forwards": 0,
            "extra_pid_calls": 0,
            "extra_planner_calls": 0,
            "wait_executor_tick_overhead_ms": {
                "count": len(self._tick_overheads_ms),
                "last": self._tick_overheads_ms[-1] if self._tick_overheads_ms else None,
                "max": max(self._tick_overheads_ms) if self._tick_overheads_ms else None,
                "mean": (
                    sum(self._tick_overheads_ms) / len(self._tick_overheads_ms)
                    if self._tick_overheads_ms
                    else None
                ),
            },
            "m3_state_lookup_overhead_ms": {
                "count": len(self._m3_lookup_overheads_ms),
                "max": max(self._m3_lookup_overheads_ms) if self._m3_lookup_overheads_ms else None,
                "mean": (
                    sum(self._m3_lookup_overheads_ms) / len(self._m3_lookup_overheads_ms)
                    if self._m3_lookup_overheads_ms
                    else None
                ),
            },
            "invariants": {
                "current_plan_passthrough_only": True,
                "no_candidate_route_used_for_control": True,
                "candidate_commit_count_zero": self._candidate_commit_count == 0,
                "no_low_level_control_writes": True,
                "no_second_model_forward": True,
                "no_second_pid": True,
                "no_planner_advancement": True,
                "old_candidates_not_authorized_after_exit": (
                    self._exit_reason is None
                    or bool(self._unauthorized_candidate_sets)
                ),
            },
        }
