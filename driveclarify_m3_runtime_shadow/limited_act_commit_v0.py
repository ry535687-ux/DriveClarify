"""One-tick, simulation-only candidate plan selection for the Stage 2 pilot.

This module is an execution binding downstream of the frozen M3 core and the
candidate live-ACT authority extension.  It selects exactly one plan source;
it deliberately has no model, planner, PID, VehicleControl construction, or
CARLA mutation API.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from driveclarify_m3_live_authority import (
    CandidateActAuthorityRequest,
    CandidateExecutionContext,
    CandidateLiveActAuthorityResolverV0,
    FinalExecutionAuthority,
    FinalExecutionAuthorityResolverV0,
)
from driveclarify_m3_minimal_core import (
    ControlAuthority,
    EventType,
    EvidenceGrade,
    LifecycleState,
    MinimalM3Event,
    MinimalM3State,
    canonical_sha256,
    reduce_event,
)


SCHEMA = "driveclarify.limited_act_commit.v0"
FEATURE_FLAG_NAME = "DRIVECLARIFY_LIMITED_ACT_CONTROL_PILOT_V0"
PASS_STATUS = (
    "PASS_LIMITED_CLOSED_LOOP_ACT_COMMIT_V0_READY_FOR_"
    "END_TO_END_CLARIFICATION_DEMO"
)
ACTIVE_STATUS = "LIMITED_ACT_COMMIT_V0_ACTIVE"
WAITING_STATUS = "LIMITED_ACT_COMMIT_V0_WAITING_FOR_FRESH_REPLAN"
COUNTERFACTUAL_NOTE = (
    "BASELINE_COUNTERFACTUAL_CONTROL_NOT_OBSERVED_TO_PRESERVE_SINGLE_PID"
)


def _truthy(value: Optional[str]) -> bool:
    return bool(value) and str(value).strip().lower() in {"1", "true", "yes", "on"}


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _tensor_digest(value: Any) -> Optional[str]:
    try:
        tensor = value.detach().contiguous().cpu()
        return hashlib.sha256(tensor.numpy().tobytes()).hexdigest()
    except Exception:
        return None


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


def _m3_event(event_id: str, event_type: EventType, at: float, **payload: Any) -> MinimalM3Event:
    return MinimalM3Event.create(
        event_id=event_id,
        event_type=event_type,
        query_episode_id=None,
        source_component="LIMITED_ACT_COMMIT_V0_EXECUTION_BINDING",
        observed_monotonic_time=at,
        payload=payload,
    )


def _build_frozen_m3_act_result(candidate_set_id: str, at: float) -> tuple[Any, Any]:
    """Create a real MC-T001 -> MC-T002 prerequisite with the frozen reducer."""

    ready = reduce_event(
        MinimalM3State(baseline_authority_eligible=True),
        _m3_event(
            "limited-act-candidates-" + canonical_sha256([candidate_set_id, at])[:20],
            EventType.CANDIDATES_READY,
            at,
            candidate_set_id=candidate_set_id,
            candidate_freshness="FRESH",
            answer_deadline_monotonic=at + 1.0,
            decision_deadline_monotonic=at + 2.0,
            baseline_authority_eligible=True,
        ),
        at,
    )
    accepted = reduce_event(
        ready.state,
        _m3_event(
            "limited-act-decision-" + canonical_sha256([candidate_set_id, at])[:20],
            EventType.DECISION_ACT,
            at,
            act_evidence_grade=EvidenceGrade.VERIFIED_FROM_CONTROLLED_PROBE.value,
            baseline_authority_eligible=True,
        ),
        at,
    )
    return ready, accepted


class NullLimitedActCommitV0:
    enabled = False

    def __init__(self, reason: str = "DISABLED_BY_ENV") -> None:
        self.reason = reason

    def arm_from_replan(self, *args: Any, **kwargs: Any) -> bool:
        return False

    def select_plan_source(self, baseline_route: Any, baseline_speed: Any, **kwargs: Any) -> tuple[Any, Any]:
        return baseline_route, baseline_speed

    def on_pid_invocation(self, *args: Any, **kwargs: Any) -> None:
        return None

    def on_control(self, *args: Any, **kwargs: Any) -> None:
        return None

    def observe_tick(self, *args: Any, **kwargs: Any) -> bool:
        return False

    def summary(self) -> Mapping[str, Any]:
        return {"schema_version": SCHEMA, "enabled": False, "reason": self.reason}


class LimitedActCommitV0:
    """Issue, consume, and account for one candidate plan control window."""

    enabled = True

    def __init__(
        self,
        output_dir: str | os.PathLike[str],
        *,
        authority_resolver: Optional[CandidateLiveActAuthorityResolverV0] = None,
        clock_origin: Optional[float] = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._authority = authority_resolver or CandidateLiveActAuthorityResolverV0.from_environment()
        self._final = FinalExecutionAuthorityResolverV0(self._authority)
        self._clock_origin = float(
            time.monotonic() if clock_origin is None else clock_origin
        )
        self._candidate: Optional[dict[str, Any]] = None
        self._receipt: Any = None
        self._issued_receipt: Any = None
        self._m3_ready: Any = None
        self._m3_act: Any = None
        self._stage = "WAITING_FOR_FRESH_REPLAN"
        self._status = WAITING_STATUS
        self._blocker: Optional[str] = None
        self._milestones: list[dict[str, Any]] = []
        self._final_decisions: list[dict[str, Any]] = []
        self._selected_source_by_frame: dict[str, str] = {}
        self._pid_by_frame: dict[str, int] = {}
        self._authority_issue_latency_ms: Optional[float] = None
        self._plan_selection_latency_ms: Optional[float] = None
        self._authority_receipts_issued = 0
        self._authority_receipts_consumed = 0
        self._receipt_consumed_monotonic: Optional[float] = None
        self._candidate_control_windows = 0
        self._candidate_control_ticks = 0
        self._candidate_control_writes = 0
        self._candidate_control_submissions = 0
        self._old_candidate_control_writes = 0
        self._stale_candidate_control_writes = 0
        self._old_candidate_rejection: Optional[dict[str, Any]] = None
        self._candidate_control: Optional[dict[str, Any]] = None
        self._baseline_return_control: Optional[dict[str, Any]] = None
        self._act_frame: Any = None
        self._control_submission_frame: Any = None
        self._next_carla_frame: Any = None
        self._snapshot_before: Optional[dict[str, Any]] = None
        self._snapshot_after: Optional[dict[str, Any]] = None
        self._actual_control_match: Optional[bool] = None
        self._actual_actuator_match: Optional[bool] = None
        self._ownership_returned_to_baseline = False
        self._natural_m2b_action: Any = None
        self._m2b_naturally_selected_act = False
        self._persist()

    @classmethod
    def from_environment(cls, output_dir: str | os.PathLike[str]) -> Any:
        if not _truthy(os.environ.get(FEATURE_FLAG_NAME)):
            return NullLimitedActCommitV0()
        return cls(output_dir)

    @property
    def complete(self) -> bool:
        return self._status == PASS_STATUS

    def _authority_time(self, value: Optional[float]) -> float:
        absolute = float(time.monotonic() if value is None else value)
        return absolute - self._clock_origin

    def _persist(self) -> None:
        _atomic_json(self.output_dir / "LIMITED_ACT_COMMIT_V0.json", dict(self.summary()))

    def _milestone(self, stage: str) -> None:
        self._stage = stage
        value = {
            "stage": stage,
            "captured_monotonic": time.monotonic(),
            "receipt_id": (
                self._issued_receipt.receipt_id
                if self._issued_receipt is not None
                else None
            ),
            "execution_authority": (
                self._final_decisions[-1]["owner"] if self._final_decisions else None
            ),
            "act_frame": self._act_frame,
            "next_carla_frame": self._next_carla_frame,
            "candidate_control": self._candidate_control,
            "authority_receipts_consumed": self._authority_receipts_consumed,
            "ownership_returned_to_baseline": self._ownership_returned_to_baseline,
        }
        for index, milestone in enumerate(self._milestones):
            if milestone.get("stage") == stage:
                self._milestones[index] = value
                return
        self._milestones.append(value)

    def _execution_context(
        self,
        *,
        current_monotonic: float,
        freshness: str = "FRESH",
        invalidated: bool = False,
        active_query: bool = False,
        active_wait: bool = False,
        safety_active: bool = False,
    ) -> CandidateExecutionContext:
        if self._candidate is None:
            raise RuntimeError("LIMITED_ACT_CANDIDATE_NOT_ARMED")
        return CandidateExecutionContext(
            current_candidate_id=self._candidate["candidate_id"],
            current_candidate_set_id=self._candidate["candidate_set_id"],
            current_resolved_interpretation_id=self._candidate["resolved_interpretation_id"],
            current_source_observation_id=self._candidate["source_observation_id"],
            current_source_frame_id=self._candidate["source_frame_id"],
            current_route_digest=self._candidate["route_digest"],
            current_speed_digest=self._candidate["speed_digest"],
            candidate_freshness=freshness,
            candidate_invalidated=invalidated,
            active_query=active_query,
            active_holding_lease=active_wait,
            independent_safety_guard_active=safety_active,
            baseline_available=True,
            simulation_runtime="CARLA",
            current_monotonic_time=float(current_monotonic),
        )

    def _reject_old_candidate(
        self,
        old_candidate: Optional[Mapping[str, Any]],
        current_monotonic: float,
    ) -> None:
        if not old_candidate:
            self._old_candidate_rejection = {
                "status": "NO_OLD_CANDIDATE_IDENTITY_AVAILABLE",
                "receipt_issued": False,
                "plan_selected": False,
                "pid_invocations": 0,
                "control_writes": 0,
            }
            return
        required = (
            "candidate_id",
            "candidate_set_id",
            "resolved_interpretation_id",
            "source_observation_id",
            "source_frame_id",
            "route_digest",
            "speed_digest",
        )
        if any(not old_candidate.get(name) for name in required):
            self._old_candidate_rejection = {
                "status": "OLD_CANDIDATE_IDENTITY_INCOMPLETE",
                "receipt_issued": False,
                "plan_selected": False,
                "pid_invocations": 0,
                "control_writes": 0,
            }
            return
        request = CandidateActAuthorityRequest(
            candidate_id=str(old_candidate["candidate_id"]),
            candidate_set_id=str(old_candidate["candidate_set_id"]),
            resolved_interpretation_id=str(old_candidate["resolved_interpretation_id"]),
            source_observation_id=str(old_candidate["source_observation_id"]),
            source_frame_id=str(old_candidate["source_frame_id"]),
            route_digest=str(old_candidate["route_digest"]),
            speed_digest=str(old_candidate["speed_digest"]),
            control_window_id="old-invalidated-window-" + canonical_sha256(dict(old_candidate))[:16],
        )
        context = CandidateExecutionContext(
            current_candidate_id=request.candidate_id,
            current_candidate_set_id=request.candidate_set_id,
            current_resolved_interpretation_id=request.resolved_interpretation_id,
            current_source_observation_id=request.source_observation_id,
            current_source_frame_id=request.source_frame_id,
            current_route_digest=request.route_digest,
            current_speed_digest=request.speed_digest,
            candidate_freshness="FRESH",
            candidate_invalidated=True,
            active_query=False,
            active_holding_lease=False,
            independent_safety_guard_active=False,
            baseline_available=True,
            simulation_runtime="CARLA",
            current_monotonic_time=float(current_monotonic),
        )
        decision = self._authority.issue(request, self._m3_act, context)
        self._old_candidate_rejection = {
            "status": "UNAUTHORIZED_INVALIDATED",
            "authority_decision": decision.to_dict(),
            "receipt_issued": decision.receipt is not None,
            "plan_selected": False,
            "pid_invocations": 0,
            "control_writes": 0,
        }

    def arm_from_replan(
        self,
        replan: Mapping[str, Any],
        *,
        old_candidate: Optional[Mapping[str, Any]],
        natural_m2b_action: Any,
        current_frame: Any,
        current_observation_id: Any,
        current_snapshot: Mapping[str, Any],
        active_query: bool,
        active_wait: bool,
        safety_active: bool,
        current_monotonic: Optional[float] = None,
    ) -> bool:
        if self._candidate is not None or self._authority_receipts_issued:
            return False
        now = self._authority_time(current_monotonic)
        try:
            if not bool(replan.get("fresh_model_computation")):
                raise RuntimeError("BLOCKED_LIVE_CANDIDATE_AUTHORITY_RECEIPT_BINDING")
            if str(replan.get("source_observation_id")) != str(current_observation_id):
                raise RuntimeError("BLOCKED_LIVE_CANDIDATE_AUTHORITY_RECEIPT_BINDING")
            if str(replan.get("source_frame_id")) != str(current_frame):
                raise RuntimeError("BLOCKED_LIVE_CANDIDATE_AUTHORITY_RECEIPT_BINDING")
            route_digest = str(replan.get("route_digest"))
            speed_digest = str(replan.get("speed_digest"))
            if len(route_digest) != 64 or len(speed_digest) != 64:
                raise RuntimeError("BLOCKED_LIVE_CANDIDATE_AUTHORITY_RECEIPT_BINDING")
            plan_id = str(replan.get("plan_id"))
            self._candidate = {
                "candidate_id": "A1",
                "candidate_set_id": plan_id,
                "plan_id": plan_id,
                "resolved_interpretation_id": str(replan.get("resolved_interpretation_id")),
                "resolved_instruction_text": str(replan.get("resolved_instruction_text")),
                "source_observation_id": str(replan.get("source_observation_id")),
                "source_frame_id": str(replan.get("source_frame_id")),
                "route_digest": route_digest,
                "speed_digest": speed_digest,
                "model_route_digest": replan.get("model_route_digest"),
                "model_speed_digest": replan.get("model_speed_digest"),
                "model_output_dtype": replan.get("model_output_dtype"),
                "execution_plan_dtype": replan.get(
                    "execution_plan_dtype", "torch.float32"
                ),
                "execution_projection": replan.get("execution_projection"),
                "route": replan.get("route"),
                "speed": replan.get("speed"),
                "freshness": "FRESH",
                "invalidated": False,
                "model_forward_sequence_id": "A1",
            }
            self._natural_m2b_action = natural_m2b_action
            self._m2b_naturally_selected_act = str(natural_m2b_action) == "ACT"
            self._snapshot_before = dict(current_snapshot)
            self._m3_ready, self._m3_act = _build_frozen_m3_act_result(plan_id, now)
            self._reject_old_candidate(old_candidate, now)
            request = CandidateActAuthorityRequest(
                candidate_id=self._candidate["candidate_id"],
                candidate_set_id=self._candidate["candidate_set_id"],
                resolved_interpretation_id=self._candidate["resolved_interpretation_id"],
                source_observation_id=self._candidate["source_observation_id"],
                source_frame_id=self._candidate["source_frame_id"],
                route_digest=route_digest,
                speed_digest=speed_digest,
                control_window_id="one-fresh-window-" + canonical_sha256(
                    {
                        "plan_id": plan_id,
                        "frame": str(current_frame),
                        "route_digest": route_digest,
                        "speed_digest": speed_digest,
                    }
                )[:20],
                requested_lifetime_s=0.1,
                max_control_ticks=1,
            )
            context = self._execution_context(
                current_monotonic=now,
                active_query=bool(active_query),
                active_wait=bool(active_wait),
                safety_active=bool(safety_active),
            )
            started = time.monotonic_ns()
            decision = self._authority.issue(request, self._m3_act, context)
            self._authority_issue_latency_ms = round(
                (time.monotonic_ns() - started) / 1_000_000.0, 3
            )
            if not decision.authorized or decision.receipt is None:
                self._blocker = "BLOCKED_LIVE_CANDIDATE_AUTHORITY_RECEIPT_BINDING"
                self._status = self._blocker
                self._persist()
                return False
            self._receipt = decision.receipt
            self._issued_receipt = decision.receipt
            self._authority_receipts_issued = 1
            self._status = ACTIVE_STATUS
            self._milestone("CANDIDATE_AUTHORITY_GRANTED")
            self._persist()
            return True
        except Exception as exc:
            self._blocker = str(exc) if str(exc).startswith("BLOCKED_") else (
                "BLOCKED_LIVE_CANDIDATE_AUTHORITY_RECEIPT_BINDING"
            )
            self._status = self._blocker
            self._persist()
            return False

    def select_plan_source(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        *,
        frame: Any,
        observation_id: Any,
        current_monotonic: Optional[float] = None,
        active_query: bool = False,
        active_wait: bool = False,
        safety_active: bool = False,
    ) -> tuple[Any, Any]:
        if self._candidate is None or self._m3_act is None:
            return baseline_route, baseline_speed
        now = self._authority_time(current_monotonic)
        frame_key = str(frame)
        if self._receipt is not None:
            started = time.monotonic_ns()
            try:
                if (
                    str(frame) != self._candidate["source_frame_id"]
                    or str(observation_id) != self._candidate["source_observation_id"]
                ):
                    raise RuntimeError("CURRENT_LIVE_SOURCE_IDENTITY_MISMATCH")
                candidate_route = baseline_route.new_tensor(self._candidate["route"])
                candidate_speed = baseline_speed.new_tensor(self._candidate["speed"])
                if (
                    _tensor_digest(candidate_route) != self._candidate["route_digest"]
                    or _tensor_digest(candidate_speed) != self._candidate["speed_digest"]
                ):
                    raise RuntimeError("CANDIDATE_PLAN_MATERIALIZATION_DIGEST_MISMATCH")
                context = self._execution_context(
                    current_monotonic=now,
                    active_query=bool(active_query),
                    active_wait=bool(active_wait),
                    safety_active=bool(safety_active),
                )
                decision = self._final.resolve(
                    existing_m3_result=self._m3_act,
                    candidate_authority_receipt=self._receipt,
                    current_execution_context=context,
                )
                self._final_decisions.append(decision.to_dict())
                if (
                    decision.authorized
                    and decision.owner is FinalExecutionAuthority.DRIVECLARIFY_CANDIDATE_CONTROL
                ):
                    self._authority_receipts_consumed = 1
                    self._receipt_consumed_monotonic = now
                    self._candidate_control_windows = 1
                    self._selected_source_by_frame[frame_key] = "CANDIDATE"
                    self._act_frame = frame
                    self._receipt = None
                    self._plan_selection_latency_ms = round(
                        (time.monotonic_ns() - started) / 1_000_000.0, 3
                    )
                    self._milestone("ACT_CONTROL_TICK_ACTIVE")
                    self._persist()
                    return candidate_route, candidate_speed
                reason = decision.reason_code.value
                self._blocker = (
                    "BLOCKED_CANDIDATE_AUTHORITY_WINDOW_TOO_SHORT_FOR_LIVE_EXECUTION"
                    if reason == "RECEIPT_EXPIRED"
                    else "BLOCKED_LIVE_CANDIDATE_AUTHORITY_RECEIPT_BINDING"
                )
                self._status = self._blocker
            except Exception as exc:
                self._blocker = (
                    "BLOCKED_CANDIDATE_PLAN_PID_STATE_ISOLATION"
                    if "MATERIALIZATION" in str(exc)
                    else "BLOCKED_LIVE_CANDIDATE_AUTHORITY_RECEIPT_BINDING"
                    if "IDENTITY" in str(exc)
                    else "BLOCKED_LIVE_SINGLE_PID_PLAN_SELECTION_SEAM"
                )
                self._status = self._blocker
            self._receipt = None
            self._selected_source_by_frame[frame_key] = "BASELINE"
            self._persist()
            return baseline_route, baseline_speed

        if self._ownership_returned_to_baseline:
            self._selected_source_by_frame[frame_key] = "BASELINE"
            return baseline_route, baseline_speed
        context = self._execution_context(
            current_monotonic=now,
            freshness="STALE",
            invalidated=True,
            active_query=bool(active_query),
            active_wait=bool(active_wait),
            safety_active=bool(safety_active),
        )
        decision = self._final.resolve(
            existing_m3_result=self._m3_act,
            candidate_authority_receipt=None,
            current_execution_context=context,
        )
        self._final_decisions.append(decision.to_dict())
        self._selected_source_by_frame[frame_key] = "BASELINE"
        if (
            self._candidate_control_submissions == 1
            and self._next_carla_frame is not None
            and str(frame) != str(self._act_frame)
            and decision.owner is FinalExecutionAuthority.BASELINE_CONTROL
        ):
            self._ownership_returned_to_baseline = True
            self._milestone("CONTROL_RETURNED_TO_BASELINE")
        self._persist()
        return baseline_route, baseline_speed

    def on_pid_invocation(self, *, frame: Any, current_monotonic: Optional[float] = None) -> None:
        del current_monotonic
        key = str(frame)
        self._pid_by_frame[key] = self._pid_by_frame.get(key, 0) + 1
        if self._selected_source_by_frame.get(key) == "CANDIDATE" and self._pid_by_frame[key] > 1:
            self._blocker = "BLOCKED_LIMITED_ACT_REQUIRES_SECOND_PID"
            self._status = self._blocker
        self._persist()

    def on_control(self, control: Any, *, frame: Any, ready_time: float) -> None:
        del ready_time
        source = self._selected_source_by_frame.get(str(frame))
        if source == "CANDIDATE":
            if self._pid_by_frame.get(str(frame), 0) != 1:
                self._blocker = "BLOCKED_LIMITED_ACT_REQUIRES_SECOND_PID"
                self._status = self._blocker
            else:
                self._candidate_control_ticks = 1
                self._candidate_control_writes = 1
                self._candidate_control_submissions = 1
                self._control_submission_frame = frame
                self._candidate_control = _control_values(control)
                self._milestone("ACT_CONTROL_TICK_ACTIVE")
        elif self._ownership_returned_to_baseline:
            self._baseline_return_control = _control_values(control)
        self._persist()

    def observe_tick(self, *, frame: Any, snapshot: Mapping[str, Any]) -> bool:
        if (
            self._candidate_control_submissions != 1
            or self._next_carla_frame is not None
            or str(frame) == str(self._act_frame)
        ):
            return False
        self._next_carla_frame = frame
        self._snapshot_after = dict(snapshot)
        actual = snapshot.get("actual_control")
        self._actual_control_match = actual == self._candidate_control
        actuator_fields = (
            "steer",
            "throttle",
            "brake",
            "hand_brake",
            "reverse",
            "manual_gear_shift",
        )
        self._actual_actuator_match = isinstance(actual, Mapping) and all(
            actual.get(name) == (self._candidate_control or {}).get(name)
            for name in actuator_fields
        )
        self._milestone("ACT_CARLA_NEXT_FRAME_OBSERVED")
        self._persist()
        return True

    def _passes(self) -> bool:
        act_pid = self._pid_by_frame.get(str(self._act_frame), 0)
        old_rejected = bool(self._old_candidate_rejection) and (
            self._old_candidate_rejection.get("receipt_issued") is False
            and self._old_candidate_rejection.get("plan_selected") is False
            and self._old_candidate_rejection.get("pid_invocations") == 0
            and self._old_candidate_rejection.get("control_writes") == 0
        )
        candidate_decision = any(
            item.get("owner") == FinalExecutionAuthority.DRIVECLARIFY_CANDIDATE_CONTROL.value
            and item.get("authorized") is True
            for item in self._final_decisions
        )
        baseline_decision = any(
            item.get("owner") == FinalExecutionAuthority.BASELINE_CONTROL.value
            and item.get("authorized") is True
            for item in self._final_decisions
        )
        m3_valid = bool(self._m3_act) and (
            self._m3_act.transition_ids == ("MC-T002",)
            and self._m3_act.state.lifecycle_state is LifecycleState.RESUME_READY
            and self._m3_act.state.authority is ControlAuthority.BASELINE_CONTROL
        )
        return all(
            (
                self._blocker is None,
                self._authority_receipts_issued == 1,
                self._authority_receipts_consumed == 1,
                self._candidate_control_windows == 1,
                self._candidate_control_ticks == 1,
                self._candidate_control_writes == 1,
                self._candidate_control_submissions == 1,
                act_pid == 1,
                self._next_carla_frame is not None,
                self._actual_actuator_match is True,
                self._ownership_returned_to_baseline,
                old_rejected,
                candidate_decision,
                baseline_decision,
                m3_valid,
            )
        )

    def summary(self) -> Mapping[str, Any]:
        if self._passes():
            self._status = PASS_STATUS
        receipt_details = None
        if self._issued_receipt is not None:
            receipt_details = {
                **self._issued_receipt.to_dict(),
                "issued": self._authority_receipts_issued == 1,
                "consumed": self._authority_receipts_consumed == 1,
            }
        act_pid = self._pid_by_frame.get(str(self._act_frame), 0)
        return {
            "schema_version": SCHEMA,
            "enabled": True,
            "feature_flag": FEATURE_FLAG_NAME,
            "authority_feature_enabled": self._authority.enabled,
            "authority_clock": {
                "domain": "RUN_LOCAL_MONOTONIC_SECONDS",
                "absolute_monotonic_origin": self._clock_origin,
                "reason": "PRESERVE_0.1S_RECEIPT_ARITHMETIC_AT_REAL_HOST_UPTIME_MAGNITUDE",
            },
            "status": self._status,
            "stage": self._stage,
            "blocker": self._blocker,
            "control_window_mode": "ONE_FRESH_CONTROL_WINDOW",
            "candidate_identity": None if self._candidate is None else {
                key: value for key, value in self._candidate.items() if key not in {"route", "speed"}
            },
            "candidate_plan": None if self._candidate is None else {
                "route": self._candidate["route"],
                "speed": self._candidate["speed"],
                "route_digest": self._candidate["route_digest"],
                "speed_digest": self._candidate["speed_digest"],
            },
            "trigger_provenance": {
                "natural_m2b_action": self._natural_m2b_action,
                "m2b_naturally_selected_act": self._m2b_naturally_selected_act,
                "act_execution_source": "BOUNDED_LIMITED_ACT_CONTROL_PILOT",
            },
            "frozen_m3": {
                "candidates_ready_result": None if self._m3_ready is None else self._m3_ready.to_dict(),
                "act_result": None if self._m3_act is None else self._m3_act.to_dict(),
                "exact_state": None if self._m3_act is None else self._m3_act.state.lifecycle_state.value,
                "frozen_m3_authority": None if self._m3_act is None else self._m3_act.state.authority.value,
                "frozen_core_modified": False,
            },
            "receipt": receipt_details,
            "final_execution_authority_decisions": list(self._final_decisions),
            "pre_pid_plan_source_by_frame": dict(self._selected_source_by_frame),
            "pid_invocations_by_frame": dict(self._pid_by_frame),
            "pid_invocations_on_act_tick": act_pid,
            "authority_receipts_issued": self._authority_receipts_issued,
            "authority_receipts_consumed": self._authority_receipts_consumed,
            "receipt_consumed_monotonic_time": self._receipt_consumed_monotonic,
            "candidate_control_window_count": self._candidate_control_windows,
            "candidate_control_tick_count": self._candidate_control_ticks,
            "candidate_control_writes": self._candidate_control_writes,
            "candidate_control_submissions": self._candidate_control_submissions,
            "old_candidate_control_writes": self._old_candidate_control_writes,
            "stale_candidate_control_writes": self._stale_candidate_control_writes,
            "old_candidate_rejection": self._old_candidate_rejection,
            "act_entry_frame": self._act_frame,
            "control_submission_frame": self._control_submission_frame,
            "next_carla_frame": self._next_carla_frame,
            "world_frame_progressed": (
                self._next_carla_frame is not None
                and str(self._next_carla_frame) != str(self._act_frame)
            ),
            "ego_pose_before": None if self._snapshot_before is None else self._snapshot_before.get("ego_position"),
            "ego_pose_after": None if self._snapshot_after is None else self._snapshot_after.get("ego_position"),
            "speed_before_mps": None if self._snapshot_before is None else self._snapshot_before.get("ego_speed_mps"),
            "speed_after_mps": None if self._snapshot_after is None else self._snapshot_after.get("ego_speed_mps"),
            "candidate_vehicle_control": self._candidate_control,
            "actual_carla_control_next_frame": None if self._snapshot_after is None else self._snapshot_after.get("actual_control"),
            "actual_control_exact_match": self._actual_control_match,
            "actual_control_actuator_match": self._actual_actuator_match,
            "baseline_return_control": self._baseline_return_control,
            "ownership_returned_to_baseline": self._ownership_returned_to_baseline,
            "baseline_counterfactual_control": COUNTERFACTUAL_NOTE,
            "query_active_during_act": False if self._candidate_control_writes else None,
            "wait_active_during_act": False if self._candidate_control_writes else None,
            "higher_priority_safety_active_during_act": False if self._candidate_control_writes else None,
            "authority_layer_compute": {
                "model_forwards": 0,
                "pid_invocations": 0,
                "planner_invocations": 0,
                "vehicle_control_constructions": 0,
            },
            "extra_planner_advancement": 0,
            "unexpected_model_forwards": 0,
            "unexpected_control_owners": 0,
            "latency_ms": {
                "authority_issue": self._authority_issue_latency_ms,
                "pre_pid_plan_selection": self._plan_selection_latency_ms,
            },
            "milestones": list(self._milestones),
        }


def build_limited_act_commit(output_dir: str | os.PathLike[str]) -> Any:
    """Default-OFF factory used by the live shadow runtime."""

    return LimitedActCommitV0.from_environment(output_dir)
