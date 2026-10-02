"""One-tick shared-equivalence plan selection through the existing PID seam."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from driveclarify_m3_live_authority import (
    ActAuthorityRequestV1,
    ActExecutionContextV1,
    ActiveAuthorityMember,
    AuthorityReason,
    FinalExecutionAuthority,
    FinalExecutionAuthorityResolverV1,
    LiveActAuthorityResolverV1,
    build_shared_subject,
    build_unique_subject,
)
from driveclarify_m3_minimal_core import canonical_sha256

from .limited_act_commit_v0 import _tensor_digest


class SharedActCommitV1:
    """Select a verified shared plan for one existing-PID control tick.

    This is the shared-subject variant of LimitedActCommitV0.  It never
    constructs VehicleControl and exposes only the same plan-selection hook.
    """

    enabled = True

    def __init__(self, *, authority_enabled: bool) -> None:
        self._authority = LiveActAuthorityResolverV1(enabled=authority_enabled)
        self._final = FinalExecutionAuthorityResolverV1(self._authority)
        self._subject = None
        self._receipt = None
        self._issued_receipt = None
        self._m3_ready = None
        self._m3_act = None
        self._route = None
        self._speed = None
        self._source_selected_frame = None
        self._pid_by_frame: dict[str, int] = {}
        self._control_observed = False
        self._next_frame_observed = False
        self._window_consumed = False
        self._baseline_restored = False
        self._decisions = []
        self._subject_history = []
        self._issued_total = 0
        self._consumed_total = 0
        self._status = "SHARED_ACT_COMMIT_V1_IDLE"

    def _context(
        self,
        *,
        current_monotonic: float,
        source_observation_id: str,
        source_frame_id: str,
        route_digest: str,
        speed_digest: str,
        route_version: str,
        environment_digest: str,
        active_query: bool,
        active_wait: bool,
        safety_active: bool,
        episode_unresolved: bool,
        active_member_set_matches: bool,
        current_action_relation: str,
        evidence_fresh: bool,
        plan_coverage_verified: bool,
        alignment_verified: bool,
        latest_safe_slack_positive: bool,
        recoverable: bool,
    ) -> ActExecutionContextV1:
        if self._subject is None:
            raise RuntimeError("SHARED_SUBJECT_NOT_ARMED")
        return ActExecutionContextV1(
            subject_identity_digest=self._subject.identity_digest,
            current_source_observation_id=source_observation_id,
            current_source_frame_id=source_frame_id,
            current_route_digest=route_digest,
            current_speed_digest=speed_digest,
            current_route_version=route_version,
            current_environment_digest=environment_digest,
            episode_unresolved=bool(episode_unresolved),
            active_member_set_digest=(
                self._subject.active_member_set_digest
                if active_member_set_matches
                else None
            ),
            current_action_relation=current_action_relation,
            evidence_fresh=bool(evidence_fresh),
            plan_coverage_verified=bool(plan_coverage_verified),
            alignment_verified=bool(alignment_verified),
            latest_safe_slack_positive=bool(latest_safe_slack_positive),
            recoverable=bool(recoverable),
            active_query=bool(active_query),
            active_holding_lease=bool(active_wait),
            independent_safety_guard_active=bool(safety_active),
            baseline_available=True,
            simulation_runtime="CARLA",
            current_monotonic_time=float(current_monotonic),
        )

    def _clear_unconsumed_subject(self) -> None:
        if self._subject is not None:
            self._subject_history.append(self._subject.to_dict())
        self._subject = None
        self._receipt = None
        self._m3_ready = None
        self._m3_act = None
        self._route = None
        self._speed = None
        self._source_selected_frame = None
        self._control_observed = False
        self._next_frame_observed = False
        self._window_consumed = False
        # No candidate plan was consumed, so existing baseline ownership never
        # left the original SimLingo control path.
        self._baseline_restored = True

    def arm_shared(
        self,
        *,
        ambiguity_episode_id: str,
        candidate_set_id: str,
        active_members: Tuple[ActiveAuthorityMember, ...],
        shared_action_window_id: str,
        shared_action_class: str,
        route: Any,
        speed: Any,
        source_observation_id: str,
        source_frame_id: str,
        route_version: str,
        environment_digest: str,
        current_action_equivalence_evidence_digest: str,
        decision_window_digest: str,
        current_monotonic: float,
        valid_until_monotonic: float,
        episode_unresolved: bool,
        active_member_set_matches: bool,
        current_action_relation: str,
        evidence_fresh: bool,
        plan_coverage_verified: bool,
        alignment_verified: bool,
        latest_safe_slack_positive: bool,
        recoverable: bool,
        existing_m3_act_result: Any,
    ) -> bool:
        if self._subject is not None or self._receipt is not None:
            return False
        route_digest = _tensor_digest(route)
        speed_digest = _tensor_digest(speed)
        if route_digest is None or speed_digest is None:
            self._status = "BLOCKED_SHARED_PLAN_DIGEST_UNAVAILABLE"
            return False
        self._m3_ready = None
        self._m3_act = existing_m3_act_result
        m3_digest = canonical_sha256(self._m3_act.to_dict())
        self._subject = build_shared_subject(
            ambiguity_episode_id=ambiguity_episode_id,
            candidate_set_id=candidate_set_id,
            active_members=active_members,
            shared_action_window_id=shared_action_window_id,
            shared_action_class=shared_action_class,
            source_observation_id=source_observation_id,
            source_frame_id=source_frame_id,
            route_digest=route_digest,
            speed_digest=speed_digest,
            issued_monotonic_time=float(current_monotonic),
            valid_until_monotonic=float(valid_until_monotonic),
            current_action_equivalence_evidence_digest=current_action_equivalence_evidence_digest,
            decision_window_digest=decision_window_digest,
            route_version=route_version,
            environment_digest=environment_digest,
            m3_act_contract_digest=m3_digest,
        )
        context = self._context(
            current_monotonic=current_monotonic,
            source_observation_id=source_observation_id,
            source_frame_id=source_frame_id,
            route_digest=route_digest,
            speed_digest=speed_digest,
            route_version=route_version,
            environment_digest=environment_digest,
            active_query=False,
            active_wait=False,
            safety_active=False,
            episode_unresolved=episode_unresolved,
            active_member_set_matches=active_member_set_matches,
            current_action_relation=current_action_relation,
            evidence_fresh=evidence_fresh,
            plan_coverage_verified=plan_coverage_verified,
            alignment_verified=alignment_verified,
            latest_safe_slack_positive=latest_safe_slack_positive,
            recoverable=recoverable,
        )
        issued = self._authority.issue(
            ActAuthorityRequestV1(self._subject), self._m3_act, context
        )
        if not issued.authorized or issued.receipt is None:
            self._status = "BLOCKED_SHARED_AUTHORITY_ISSUE:" + issued.reason_code.value
            self._clear_unconsumed_subject()
            return False
        self._receipt = issued.receipt
        self._issued_receipt = issued.receipt
        self._issued_total += 1
        self._route = route
        self._speed = speed
        self._status = "SHARED_ACT_AUTHORITY_ARMED"
        return True

    def arm_unique(
        self,
        *,
        candidate_set_id: str,
        candidate_id: str,
        resolved_interpretation_id: str,
        route: Any,
        speed: Any,
        source_observation_id: str,
        source_frame_id: str,
        route_version: str,
        environment_digest: str,
        current_monotonic: float,
        valid_until_monotonic: float,
        existing_m3_act_result: Any,
    ) -> bool:
        if self._subject is not None or self._receipt is not None:
            return False
        route_digest = _tensor_digest(route)
        speed_digest = _tensor_digest(speed)
        if route_digest is None or speed_digest is None:
            self._status = "BLOCKED_UNIQUE_PLAN_DIGEST_UNAVAILABLE"
            return False
        self._m3_ready = None
        self._m3_act = existing_m3_act_result
        m3_digest = canonical_sha256(self._m3_act.to_dict())
        self._subject = build_unique_subject(
            candidate_set_id=candidate_set_id,
            candidate_id=candidate_id,
            resolved_interpretation_id=resolved_interpretation_id,
            source_observation_id=source_observation_id,
            source_frame_id=source_frame_id,
            route_digest=route_digest,
            speed_digest=speed_digest,
            issued_monotonic_time=float(current_monotonic),
            valid_until_monotonic=float(valid_until_monotonic),
            route_version=route_version,
            environment_digest=environment_digest,
            m3_act_contract_digest=m3_digest,
        )
        context = self._context(
            current_monotonic=current_monotonic,
            source_observation_id=source_observation_id,
            source_frame_id=source_frame_id,
            route_digest=route_digest,
            speed_digest=speed_digest,
            route_version=route_version,
            environment_digest=environment_digest,
            active_query=False,
            active_wait=False,
            safety_active=False,
            episode_unresolved=False,
            active_member_set_matches=False,
            current_action_relation="UNIQUE_CANDIDATE",
            evidence_fresh=True,
            plan_coverage_verified=True,
            alignment_verified=True,
            latest_safe_slack_positive=True,
            recoverable=True,
        )
        issued = self._authority.issue(
            ActAuthorityRequestV1(self._subject), self._m3_act, context
        )
        if not issued.authorized or issued.receipt is None:
            self._status = "BLOCKED_UNIQUE_AUTHORITY_ISSUE:" + issued.reason_code.value
            self._clear_unconsumed_subject()
            return False
        self._receipt = issued.receipt
        self._issued_receipt = issued.receipt
        self._issued_total += 1
        self._route = route
        self._speed = speed
        self._status = "UNIQUE_ACT_AUTHORITY_ARMED"
        return True

    def select_plan_source(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        *,
        frame: Any,
        observation_id: Any,
        route_version: str,
        environment_digest: str,
        current_monotonic: float,
        active_query: bool = False,
        active_wait: bool = False,
        safety_active: bool = False,
        episode_unresolved: bool = False,
        active_member_set_matches: bool = False,
        current_action_relation: str = "UNKNOWN_OR_INSUFFICIENT_EVIDENCE",
        evidence_fresh: bool = False,
        plan_coverage_verified: bool = False,
        alignment_verified: bool = False,
        latest_safe_slack_positive: bool = False,
        recoverable: bool = False,
    ) -> tuple[Any, Any]:
        if self._subject is None or self._m3_act is None:
            return baseline_route, baseline_speed
        if self._receipt is None:
            self._baseline_restored = True
            return baseline_route, baseline_speed
        current_route_digest = _tensor_digest(self._route)
        current_speed_digest = _tensor_digest(self._speed)
        context = self._context(
            current_monotonic=current_monotonic,
            source_observation_id=str(observation_id),
            source_frame_id=str(frame),
            route_digest=str(current_route_digest),
            speed_digest=str(current_speed_digest),
            route_version=route_version,
            environment_digest=environment_digest,
            active_query=active_query,
            active_wait=active_wait,
            safety_active=safety_active,
            episode_unresolved=episode_unresolved,
            active_member_set_matches=active_member_set_matches,
            current_action_relation=current_action_relation,
            evidence_fresh=evidence_fresh,
            plan_coverage_verified=plan_coverage_verified,
            alignment_verified=alignment_verified,
            latest_safe_slack_positive=latest_safe_slack_positive,
            recoverable=recoverable,
        )
        decision = self._final.resolve(
            existing_m3_result=self._m3_act,
            live_authority_receipt=self._receipt,
            current_execution_context=context,
        )
        self._decisions.append(decision.to_dict())
        if (
            decision.authorized
            and decision.owner is FinalExecutionAuthority.DRIVECLARIFY_CANDIDATE_CONTROL
        ):
            self._source_selected_frame = str(frame)
            self._receipt = None
            self._window_consumed = True
            self._consumed_total += 1
            self._status = "SHARED_ACT_CONTROL_TICK_SELECTED"
            # Both subject variants use the existing tensor/PID/controller
            # seam; shared stores the normal baseline tensors, while unique
            # stores the fresh post-answer candidate tensors.
            return self._route, self._speed
        self.revoke(decision.reason_code)
        self._status = "BLOCKED_SHARED_PLAN_SELECTION:" + decision.reason_code.value
        return baseline_route, baseline_speed

    def revoke(self, reason: AuthorityReason) -> None:
        self._authority.revoke(self._receipt, reason)
        self._receipt = None
        if not self._window_consumed:
            self._clear_unconsumed_subject()
        self._status = "SHARED_ACT_REVOKED:" + reason.value

    def on_pid_invocation(self, *, frame: Any) -> None:
        key = str(frame)
        self._pid_by_frame[key] = self._pid_by_frame.get(key, 0) + 1
        if key == self._source_selected_frame and self._pid_by_frame[key] > 1:
            self._status = "BLOCKED_SHARED_ACT_REQUIRES_SECOND_PID"

    def on_control(self, *, frame: Any) -> None:
        if str(frame) == self._source_selected_frame:
            self._control_observed = True
            if self._pid_by_frame.get(str(frame), 0) != 1:
                self._status = "BLOCKED_SHARED_ACT_REQUIRES_SECOND_PID"

    def observe_tick(self, *, frame: Any) -> bool:
        if (
            not self._control_observed
            or self._next_frame_observed
            or str(frame) == self._source_selected_frame
        ):
            return False
        self._next_frame_observed = True
        self._baseline_restored = True
        self._status = "PASS_SHARED_ACT_ONE_TICK_EXISTING_PID_BASELINE_RESTORED"
        return True

    def prepare_next_window(self) -> None:
        if not (self._window_consumed and self._baseline_restored):
            raise RuntimeError("SHARED_WINDOW_NOT_READY_FOR_REFRESH")
        if self._subject is not None:
            self._subject_history.append(self._subject.to_dict())
        self._subject = None
        self._receipt = None
        self._issued_receipt = None
        self._m3_ready = None
        self._m3_act = None
        self._route = None
        self._speed = None
        self._source_selected_frame = None
        self._control_observed = False
        self._next_frame_observed = False
        self._window_consumed = False
        self._baseline_restored = False
        self._status = "SHARED_ACT_COMMIT_V1_WAITING_FOR_FRESH_BUNDLE"

    @property
    def window_consumed(self) -> bool:
        return self._window_consumed

    @property
    def baseline_restored(self) -> bool:
        return self._baseline_restored

    @property
    def subject(self) -> Any:
        return self._subject

    def summary(self) -> Mapping[str, Any]:
        subject = self._subject
        shared = subject is not None and subject.subject_type.value == "SHARED_EQUIVALENCE_CLASS"
        return {
            "schema_version": "driveclarify.shared_act_commit.v1",
            "status": self._status,
            "subject": None if self._subject is None else self._subject.to_dict(),
            "receipt": None if self._issued_receipt is None else self._issued_receipt.to_dict(),
            "subject_history": list(self._subject_history),
            "authority_receipts_issued": self._issued_total,
            "authority_receipts_consumed": self._consumed_total,
            "shared_control_windows": self._consumed_total,
            "existing_pid_invocation_count": sum(self._pid_by_frame.values()),
            "new_pid_count": 0,
            "direct_vehicle_control_write_count": 0,
            "candidate_id": None if subject is None else subject.candidate_id,
            "resolved_interpretation_id": (
                None if subject is None else subject.resolved_interpretation_id
            ),
            "preserve_unresolved_semantics": shared,
            "baseline_ownership_returned": self._baseline_restored,
            "final_authority_decisions": list(self._decisions),
        }


__all__ = ["SharedActCommitV1"]
