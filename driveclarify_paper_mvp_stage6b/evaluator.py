"""Unified post-episode evaluator with explicit evidence availability."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .contracts import (
    EvidenceStatus,
    EvidenceValue,
    FailureClass,
    LabelFirewallCounters,
    Stage6BContractError,
)


TTC_CLOSING_SPEED_EPSILON_MPS = 0.05
NEAR_MISS_TTC_THRESHOLD_SECONDS = 1.5
NEAR_MISS_CLEARANCE_THRESHOLD_METERS = 2.0


@dataclass(frozen=True)
class EpisodeIdentity:
    episode_id: str
    runtime_config_id: str
    scenario_id: str
    runtime_fixture_id: str
    seed: int
    method_id: str
    split: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.episode_id,
                self.runtime_config_id,
                self.scenario_id,
                self.runtime_fixture_id,
                self.method_id,
                self.split,
            )
        ):
            raise Stage6BContractError("EPISODE_IDENTITY_TEXT_REQUIRED")
        if self.split not in {"train", "dev", "test"}:
            raise Stage6BContractError("EPISODE_SPLIT_INVALID")


@dataclass(frozen=True)
class GoalEventEvidence:
    detector_coverage_complete: bool
    intended_goal_triggered: bool
    wrong_goal_triggered: bool
    intended_goal_evidence_ids: tuple[str, ...] = ()
    wrong_goal_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.intended_goal_triggered and self.wrong_goal_triggered:
            raise Stage6BContractError("INTENDED_AND_WRONG_GOAL_CANNOT_BOTH_TRIGGER")


@dataclass
class OnlineSafetyAccumulator:
    """CARLA-state TTC/clearance evidence; no missing event becomes infinity."""

    expected_tick_count: int = 0
    covered_tick_count: int = 0
    actor_pair_count: int = 0
    closing_pair_count: int = 0
    minimum_ttc_seconds: float | None = None
    minimum_clearance_meters: float | None = None
    minimum_ttc_clearance_meters: float | None = None
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OnlineSafetyAccumulator":
        """Restore only the persisted primitive accumulator state."""

        fields = {
            "expected_tick_count",
            "covered_tick_count",
            "actor_pair_count",
            "closing_pair_count",
            "minimum_ttc_seconds",
            "minimum_clearance_meters",
            "minimum_ttc_clearance_meters",
            "errors",
        }
        return cls(**{key: value[key] for key in fields if key in value})

    def expect_tick(self) -> None:
        self.expected_tick_count += 1

    def observe_tick(
        self,
        *,
        ego_position_xy: tuple[float, float],
        ego_velocity_xy: tuple[float, float],
        ego_radius_m: float,
        actors: Sequence[Mapping[str, Any]],
    ) -> None:
        try:
            local_min_ttc: float | None = None
            local_ttc_clearance: float | None = None
            local_min_clearance: float | None = None
            for actor in actors:
                position = actor["position_xy"]
                velocity = actor["velocity_xy"]
                radius = float(actor["radius_m"])
                rx = float(position[0]) - float(ego_position_xy[0])
                ry = float(position[1]) - float(ego_position_xy[1])
                distance = math.hypot(rx, ry)
                if distance <= 1e-9:
                    clearance = 0.0
                else:
                    clearance = max(0.0, distance - float(ego_radius_m) - radius)
                local_min_clearance = (
                    clearance
                    if local_min_clearance is None
                    else min(local_min_clearance, clearance)
                )
                self.actor_pair_count += 1
                rvx = float(velocity[0]) - float(ego_velocity_xy[0])
                rvy = float(velocity[1]) - float(ego_velocity_xy[1])
                if distance <= 1e-9:
                    closing_speed = float("inf")
                else:
                    closing_speed = -(rx * rvx + ry * rvy) / distance
                if not math.isfinite(closing_speed) or (
                    closing_speed <= TTC_CLOSING_SPEED_EPSILON_MPS
                ):
                    continue
                self.closing_pair_count += 1
                ttc = clearance / closing_speed
                if ttc < 0.0 or not math.isfinite(ttc):
                    continue
                if local_min_ttc is None or ttc < local_min_ttc:
                    local_min_ttc = ttc
                    local_ttc_clearance = clearance
            if local_min_clearance is not None:
                self.minimum_clearance_meters = (
                    local_min_clearance
                    if self.minimum_clearance_meters is None
                    else min(self.minimum_clearance_meters, local_min_clearance)
                )
            if local_min_ttc is not None and (
                self.minimum_ttc_seconds is None
                or local_min_ttc < self.minimum_ttc_seconds
            ):
                self.minimum_ttc_seconds = local_min_ttc
                self.minimum_ttc_clearance_meters = local_ttc_clearance
            self.covered_tick_count += 1
        except Exception as exc:
            self.errors.append(type(exc).__name__ + ":" + str(exc))

    @property
    def coverage_complete(self) -> bool:
        return bool(
            self.expected_tick_count > 0
            and self.covered_tick_count == self.expected_tick_count
            and not self.errors
        )

    def metric_values(
        self, *, collision: EvidenceValue
    ) -> dict[str, EvidenceValue]:
        coverage = {
            "expected_tick_count": self.expected_tick_count,
            "covered_tick_count": self.covered_tick_count,
            "actor_pair_count": self.actor_pair_count,
            "closing_pair_count": self.closing_pair_count,
            "errors": list(self.errors),
        }
        if not self.coverage_complete:
            unknown = EvidenceValue(
                EvidenceStatus.UNKNOWN,
                None,
                "ONLINE_CARLA_ACTOR_KINEMATICS_V1",
                ("TTC_COVERAGE_INCOMPLETE",),
                coverage,
            )
            return {"minimum_ttc_seconds": unknown, "near_miss": unknown}
        if self.minimum_ttc_seconds is None:
            ttc = EvidenceValue(
                EvidenceStatus.VALID_BUT_NOT_TRIGGERED,
                None,
                "ONLINE_CARLA_ACTOR_KINEMATICS_V1",
                ("NO_POSITIVE_CLOSING_PAIR_OBSERVED",),
                coverage,
            )
        else:
            ttc = EvidenceValue(
                EvidenceStatus.AVAILABLE,
                self.minimum_ttc_seconds,
                "ONLINE_CARLA_ACTOR_KINEMATICS_V1",
                ("MINIMUM_POSITIVE_CLOSING_TTC_OBSERVED",),
                coverage,
            )
        if collision.status is not EvidenceStatus.AVAILABLE:
            near_miss = EvidenceValue(
                EvidenceStatus.UNKNOWN,
                None,
                "ONLINE_CARLA_ACTOR_KINEMATICS_V1",
                ("COLLISION_COVERAGE_REQUIRED_FOR_NEAR_MISS",),
                coverage,
            )
        elif collision.value is True:
            near_miss = EvidenceValue(
                EvidenceStatus.AVAILABLE,
                False,
                "ONLINE_CARLA_ACTOR_KINEMATICS_V1",
                ("COLLISION_IS_NOT_CLASSIFIED_AS_NEAR_MISS",),
                coverage,
            )
        elif self.minimum_ttc_seconds is None:
            near_miss = EvidenceValue(
                EvidenceStatus.VALID_BUT_NOT_TRIGGERED,
                False,
                "ONLINE_CARLA_ACTOR_KINEMATICS_V1",
                ("NO_NEAR_MISS_TRIGGER",),
                coverage,
            )
        else:
            triggered = bool(
                self.minimum_ttc_seconds <= NEAR_MISS_TTC_THRESHOLD_SECONDS
                and self.minimum_ttc_clearance_meters is not None
                and self.minimum_ttc_clearance_meters
                <= NEAR_MISS_CLEARANCE_THRESHOLD_METERS
            )
            near_miss = EvidenceValue(
                EvidenceStatus.AVAILABLE,
                triggered,
                "ONLINE_CARLA_ACTOR_KINEMATICS_V1",
                (
                    "NEAR_MISS_THRESHOLD_TRIGGERED"
                    if triggered
                    else "NEAR_MISS_THRESHOLD_NOT_TRIGGERED"
                ,),
                coverage,
            )
        return {"minimum_ttc_seconds": ttc, "near_miss": near_miss}

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["coverage_complete"] = self.coverage_complete
        return value


@dataclass(frozen=True)
class EpisodeResult:
    identity: EpisodeIdentity
    runtime_fingerprint: Mapping[str, Any]
    checkpoint: Mapping[str, Any]
    start_end: Mapping[str, Any]
    termination: Mapping[str, Any]
    decision_trace: tuple[Mapping[str, Any], ...]
    query_trace: tuple[Mapping[str, Any], ...]
    wait_trace: tuple[Mapping[str, Any], ...]
    candidate_trace: Mapping[str, Any]
    task_metrics: Mapping[str, EvidenceValue]
    safety_metrics: Mapping[str, EvidenceValue]
    interaction_metrics: Mapping[str, EvidenceValue]
    compute_metrics: Mapping[str, EvidenceValue]
    forward_accounting: Mapping[str, Any]
    pid_accounting: Mapping[str, Any]
    label_firewall: Mapping[str, Any]
    failure_class: FailureClass
    failure_reasons: tuple[str, ...]
    cleanup_state: Mapping[str, Any]
    schema_version: str = "driveclarify.paper_mvp_stage6b_episode_result.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "episode_identity": asdict(self.identity),
            "runtime_fingerprint": dict(self.runtime_fingerprint),
            "checkpoint": dict(self.checkpoint),
            "start_end": dict(self.start_end),
            "termination": dict(self.termination),
            "decision_trace": [dict(item) for item in self.decision_trace],
            "query_trace": [dict(item) for item in self.query_trace],
            "wait_trace": [dict(item) for item in self.wait_trace],
            "candidate_trace": dict(self.candidate_trace),
            "task_metrics": {
                key: value.to_dict() for key, value in self.task_metrics.items()
            },
            "safety_metrics": {
                key: value.to_dict() for key, value in self.safety_metrics.items()
            },
            "interaction_metrics": {
                key: value.to_dict()
                for key, value in self.interaction_metrics.items()
            },
            "compute_metrics": {
                key: value.to_dict() for key, value in self.compute_metrics.items()
            },
            "forward_accounting": dict(self.forward_accounting),
            "pid_accounting": dict(self.pid_accounting),
            "label_firewall": dict(self.label_firewall),
            "failure_class": self.failure_class.value,
            "failure_reasons": list(self.failure_reasons),
            "evidence_availability": _availability_summary(
                self.task_metrics,
                self.safety_metrics,
                self.interaction_metrics,
                self.compute_metrics,
            ),
            "cleanup_state": dict(self.cleanup_state),
        }


def _criterion_metric(
    infractions: Mapping[str, Any] | None,
    key: str,
    *,
    metric_name: str,
) -> EvidenceValue:
    if not isinstance(infractions, Mapping) or key not in infractions:
        return EvidenceValue(
            EvidenceStatus.UNKNOWN,
            None,
            "LEADERBOARD_ROUTE_CRITERIA",
            (metric_name.upper() + "_CRITERION_COVERAGE_UNPROVEN",),
        )
    values = infractions[key]
    if not isinstance(values, list):
        return EvidenceValue(
            EvidenceStatus.INVALID_EVIDENCE,
            None,
            "LEADERBOARD_ROUTE_CRITERIA",
            (metric_name.upper() + "_INFRACTION_LIST_INVALID",),
        )
    return EvidenceValue(
        EvidenceStatus.AVAILABLE,
        bool(values),
        "LEADERBOARD_ROUTE_CRITERIA",
        ("COMPLETE_ROUTE_CRITERION_COVERAGE",),
        {"infraction_count": len(values)},
    )


def _task_metrics(
    record: Mapping[str, Any] | None,
    goal: GoalEventEvidence | None,
) -> dict[str, EvidenceValue]:
    score = None
    if isinstance(record, Mapping):
        score = record.get("scores", {}).get("score_route")
    if isinstance(score, (int, float)) and math.isfinite(float(score)):
        route = EvidenceValue(
            EvidenceStatus.AVAILABLE,
            max(0.0, min(1.0, float(score) / 100.0)),
            "LEADERBOARD_ROUTE_COMPLETION_TEST",
            ("ROUTE_COMPLETION_CRITERION_AVAILABLE",),
        )
    else:
        route = EvidenceValue(
            EvidenceStatus.UNKNOWN,
            None,
            "LEADERBOARD_ROUTE_COMPLETION_TEST",
            ("ROUTE_COMPLETION_CRITERION_UNAVAILABLE",),
        )
    if goal is None or not goal.detector_coverage_complete:
        unknown = EvidenceValue(
            EvidenceStatus.UNKNOWN,
            None,
            "POST_EPISODE_INDEPENDENT_GOAL_EVENT_EVALUATOR",
            ("GOAL_REGION_OR_TRAJECTORY_COVERAGE_UNAVAILABLE",),
        )
        return {
            "route_completion": route,
            "goal_correct": unknown,
            "wrong_goal_execution": unknown,
            "instruction_success": unknown,
        }
    coverage = {
        "detector_coverage_complete": True,
        "intended_goal_evidence_ids": list(goal.intended_goal_evidence_ids),
        "wrong_goal_evidence_ids": list(goal.wrong_goal_evidence_ids),
    }
    if goal.intended_goal_triggered:
        correct = EvidenceValue(
            EvidenceStatus.AVAILABLE,
            True,
            "POST_EPISODE_INDEPENDENT_GOAL_EVENT_EVALUATOR",
            ("INTENDED_GOAL_TRIGGERED",),
            coverage,
        )
        wrong = EvidenceValue(
            EvidenceStatus.VALID_BUT_NOT_TRIGGERED,
            False,
            "POST_EPISODE_INDEPENDENT_GOAL_EVENT_EVALUATOR",
            ("WRONG_GOAL_NOT_TRIGGERED",),
            coverage,
        )
        success = EvidenceValue(
            EvidenceStatus.AVAILABLE,
            True,
            "POST_EPISODE_INDEPENDENT_GOAL_EVENT_EVALUATOR",
            ("PASSENGER_INTENT_GOAL_COMPLETED",),
            coverage,
        )
    elif goal.wrong_goal_triggered:
        correct = EvidenceValue(
            EvidenceStatus.AVAILABLE,
            False,
            "POST_EPISODE_INDEPENDENT_GOAL_EVENT_EVALUATOR",
            ("WRONG_GOAL_TRIGGERED",),
            coverage,
        )
        wrong = EvidenceValue(
            EvidenceStatus.AVAILABLE,
            True,
            "POST_EPISODE_INDEPENDENT_GOAL_EVENT_EVALUATOR",
            ("WRONG_GOAL_TRIGGERED",),
            coverage,
        )
        success = EvidenceValue(
            EvidenceStatus.AVAILABLE,
            False,
            "POST_EPISODE_INDEPENDENT_GOAL_EVENT_EVALUATOR",
            ("PASSENGER_INTENT_GOAL_NOT_COMPLETED",),
            coverage,
        )
    else:
        correct = EvidenceValue(
            EvidenceStatus.VALID_BUT_NOT_TRIGGERED,
            None,
            "POST_EPISODE_INDEPENDENT_GOAL_EVENT_EVALUATOR",
            ("NO_GOAL_EVENT_TRIGGERED",),
            coverage,
        )
        wrong = EvidenceValue(
            EvidenceStatus.VALID_BUT_NOT_TRIGGERED,
            False,
            "POST_EPISODE_INDEPENDENT_GOAL_EVENT_EVALUATOR",
            ("WRONG_GOAL_NOT_TRIGGERED",),
            coverage,
        )
        success = EvidenceValue(
            EvidenceStatus.AVAILABLE,
            False,
            "POST_EPISODE_INDEPENDENT_GOAL_EVENT_EVALUATOR",
            ("INTENDED_GOAL_NOT_COMPLETED",),
            coverage,
        )
    return {
        "route_completion": route,
        "goal_correct": correct,
        "wrong_goal_execution": wrong,
        "instruction_success": success,
    }


def _availability_summary(*families: Mapping[str, EvidenceValue]) -> dict[str, int]:
    counts = {status.value: 0 for status in EvidenceStatus}
    for family in families:
        for value in family.values():
            counts[value.status.value] += 1
    return counts


class UnifiedPostEpisodeEvaluator:
    """Build one EPISODE_RESULT shape for every method and termination type."""

    def evaluate(
        self,
        *,
        identity: EpisodeIdentity,
        leaderboard_payload: Mapping[str, Any] | None,
        runtime_audit: Mapping[str, Any],
        runtime_fingerprint: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
        start_end: Mapping[str, Any],
        cleanup_state: Mapping[str, Any],
        firewall: LabelFirewallCounters,
        goal_evidence: GoalEventEvidence | None = None,
        safety_accumulator: OnlineSafetyAccumulator | None = None,
    ) -> EpisodeResult:
        terminal = bool(runtime_audit.get("terminal", True))
        if goal_evidence is not None:
            if not terminal:
                raise Stage6BContractError("POST_EPISODE_GOLD_JOIN_BEFORE_TERMINAL")
            firewall.post_episode_gold_intent_reads += 1
        firewall.assert_runtime_clean()
        records = None
        if isinstance(leaderboard_payload, Mapping):
            records = leaderboard_payload.get("_checkpoint", {}).get("records")
        record = records[0] if isinstance(records, list) and len(records) == 1 else None
        infractions = record.get("infractions") if isinstance(record, Mapping) else None
        collision = _criterion_metric(
            infractions,
            "collisions_layout",
            metric_name="collision_layout",
        )
        # A collision is true if any complete collision family fired.  Do not
        # infer false until all three lists are present and valid.
        collision_parts = [
            _criterion_metric(infractions, key, metric_name=key)
            for key in (
                "collisions_layout",
                "collisions_pedestrian",
                "collisions_vehicle",
            )
        ]
        if all(item.status is EvidenceStatus.AVAILABLE for item in collision_parts):
            collision = EvidenceValue(
                EvidenceStatus.AVAILABLE,
                any(item.value is True for item in collision_parts),
                "LEADERBOARD_COLLISION_TEST_COMPLETE_FAMILIES",
                ("ALL_COLLISION_CRITERIA_AVAILABLE",),
            )
        else:
            collision = EvidenceValue(
                EvidenceStatus.UNKNOWN,
                None,
                "LEADERBOARD_COLLISION_TEST_COMPLETE_FAMILIES",
                ("COLLISION_CRITERIA_COVERAGE_INCOMPLETE",),
            )
        safety = {
            "collision": collision,
            "offroad": _criterion_metric(
                infractions, "outside_route_lanes", metric_name="offroad"
            ),
            "wrong_lane": _criterion_metric(
                infractions, "outside_route_lanes", metric_name="wrong_lane"
            ),
            "red_light_violation": _criterion_metric(
                infractions, "red_light", metric_name="red_light"
            ),
            "stop_sign_violation": _criterion_metric(
                infractions, "stop_infraction", metric_name="stop_sign"
            ),
        }
        if safety_accumulator is None:
            unknown = EvidenceValue(
                EvidenceStatus.UNKNOWN,
                None,
                "ONLINE_CARLA_ACTOR_KINEMATICS_V1",
                ("ONLINE_KINEMATIC_ACCUMULATOR_MISSING",),
            )
            safety.update({"minimum_ttc_seconds": unknown, "near_miss": unknown})
        else:
            safety.update(safety_accumulator.metric_values(collision=collision))

        interaction_summary = runtime_audit.get("interaction", {})
        interaction = {
            "query_count": _available_number(
                interaction_summary.get("query_count"), "RUNTIME_METHOD_ADAPTER"
            ),
            "decision_delay_seconds": _available_number(
                interaction_summary.get("decision_delay_seconds"),
                "RUNTIME_METHOD_ADAPTER",
            ),
            "replan_count": _available_number(
                interaction_summary.get("replan_count"), "RUNTIME_METHOD_ADAPTER"
            ),
            "resume_success": _available_boolean(
                interaction_summary.get("resume_success"), "RUNTIME_METHOD_ADAPTER"
            ),
            "timeout": _available_boolean(
                interaction_summary.get("timeout"), "RUNTIME_METHOD_ADAPTER"
            ),
            "fallback": _available_boolean(
                interaction_summary.get("fallback"), "RUNTIME_METHOD_ADAPTER"
            ),
        }
        compute_summary = runtime_audit.get("compute", {})
        compute = {
            key: _available_number(compute_summary.get(key), "RUNTIME_HOOK_ACCOUNTING")
            for key in (
                "normal_model_forwards",
                "candidate_model_forwards",
                "existing_pid_invocations",
                "new_pid_invocations",
                "planner_advances",
                "authority_receipts",
                "candidate_commits",
                "candidate_direct_control_writes",
                "m3_direct_control_writes",
                "vehicle_control_ownership_violations",
            )
        }
        failure_class, failure_reasons = _classify_failure(record, runtime_audit)
        return EpisodeResult(
            identity=identity,
            runtime_fingerprint=runtime_fingerprint,
            checkpoint=checkpoint,
            start_end=start_end,
            termination={
                "terminal": terminal,
                "leaderboard_status": (
                    record.get("status") if isinstance(record, Mapping) else None
                ),
                "runtime_reason": runtime_audit.get("termination_reason"),
            },
            decision_trace=tuple(runtime_audit.get("decision_trace", ())),
            query_trace=tuple(runtime_audit.get("query_trace", ())),
            wait_trace=tuple(runtime_audit.get("wait_trace", ())),
            candidate_trace=dict(runtime_audit.get("candidate_trace", {})),
            task_metrics=_task_metrics(record, goal_evidence),
            safety_metrics=safety,
            interaction_metrics=interaction,
            compute_metrics=compute,
            forward_accounting=dict(runtime_audit.get("forward_accounting", {})),
            pid_accounting=dict(runtime_audit.get("pid_accounting", {})),
            label_firewall=firewall.to_dict(),
            failure_class=failure_class,
            failure_reasons=failure_reasons,
            cleanup_state=cleanup_state,
        )


def _available_number(value: Any, source: str) -> EvidenceValue:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return EvidenceValue(
            EvidenceStatus.UNKNOWN,
            None,
            source,
            ("NUMERIC_EVIDENCE_UNAVAILABLE",),
        )
    return EvidenceValue(EvidenceStatus.AVAILABLE, value, source)


def _available_boolean(value: Any, source: str) -> EvidenceValue:
    if type(value) is not bool:
        return EvidenceValue(
            EvidenceStatus.UNKNOWN,
            None,
            source,
            ("BOOLEAN_EVIDENCE_UNAVAILABLE",),
        )
    return EvidenceValue(EvidenceStatus.AVAILABLE, value, source)


def _classify_failure(
    record: Mapping[str, Any] | None,
    runtime_audit: Mapping[str, Any],
) -> tuple[FailureClass, tuple[str, ...]]:
    status = str(record.get("status", "")) if isinstance(record, Mapping) else ""
    if any(
        marker in status.casefold()
        for marker in ("simulation crashed", "agent couldn't be set up", "agent crashed")
    ):
        return (
            FailureClass.ENVIRONMENT_OR_SIMULATOR_FAILURE,
            ("LEADERBOARD_INFRASTRUCTURE_TERMINATION",),
        )
    compute = runtime_audit.get("compute", {})
    if any(
        int(compute.get(key, 0) or 0) > 0
        for key in (
            "new_pid_invocations",
            "candidate_direct_control_writes",
            "m3_direct_control_writes",
            "vehicle_control_ownership_violations",
        )
    ):
        return (
            FailureClass.EXECUTION_OR_CONTROL_FAILURE,
            ("CONTROL_OWNERSHIP_CONTRACT_VIOLATION",),
        )
    if runtime_audit.get("candidate_failure") is True:
        return (
            FailureClass.CANDIDATE_OR_CONSEQUENCE_FAILURE,
            ("RUNTIME_CANDIDATE_OR_CONSEQUENCE_FAILURE",),
        )
    if runtime_audit.get("decision_failure") is True:
        return FailureClass.DECISION_FAILURE, ("RUNTIME_DECISION_FAILURE",)
    if status == "Completed":
        return FailureClass.NONE, ("NO_CROSS_LAYER_FAILURE_CLASSIFIED",)
    return (
        FailureClass.UNKNOWN_OR_INSUFFICIENT_EVIDENCE,
        ("TERMINATION_NOT_CAUSALLY_ATTRIBUTABLE",),
    )


__all__ = [
    "EpisodeIdentity",
    "EpisodeResult",
    "GoalEventEvidence",
    "NEAR_MISS_CLEARANCE_THRESHOLD_METERS",
    "NEAR_MISS_TTC_THRESHOLD_SECONDS",
    "OnlineSafetyAccumulator",
    "TTC_CLOSING_SPEED_EPSILON_MPS",
    "UnifiedPostEpisodeEvaluator",
]
