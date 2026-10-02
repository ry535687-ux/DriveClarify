"""Frozen CPU-only metric formulas and UNKNOWN-preserving hooks."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .canonical import canonical_sha256
from .frozen_registry import (
    FROZEN_EVALUATOR_CRITERIA_SOURCE,
    FROZEN_EVALUATOR_OWNER_MODULE,
    FROZEN_SAFETY_OWNER_CLASSES,
    assert_frozen_evaluator_registry,
)
from .models import Availability, GlobalTask, UnknownValue


CONTINUITY_FORMULA_VERSION = "driveclarify.rq2.continuity.v1"
COMMITMENT_FORMULA_VERSION = "driveclarify.rq2.commitment_violation.v1"
RECOVERY_FORMULA_VERSION = "driveclarify.rq2.recovery.v1"
GLOBAL_TASK_FORMULA_VERSION = "driveclarify.rq2.global_task.v1"
SAFETY_FORMULA_VERSION = "driveclarify.rq2.safety.v1"
LOCAL_TASK_FORMULA_VERSION = "driveclarify.rq2.local_task.v1"
EFFICIENCY_FORMULA_VERSION = "driveclarify.rq2.efficiency.v1"

LOCAL_TERMINALS = frozenset(
    {
        "UPDATED_MANEUVER_COMPLETED",
        "MISSED_CURRENT_OPPORTUNITY",
        "NO_SAFE_CURRENT_OPPORTUNITY_ACKNOWLEDGED",
        "COMMITMENT_VIOLATION_TERMINAL",
        "SAFETY_BLOCKED",
        "RULE_BLOCKED",
        "SCIENTIFIC_NONCOMPLETION_TIMEOUT",
        "FAILED_OTHER",
        "UNKNOWN",
    }
)


@dataclass(frozen=True)
class MetricResult:
    value: float | bool | str | None
    unit: str
    status: Availability
    source_events: tuple[str, ...]
    formula_version: str
    unknown: UnknownValue | None = None

    @classmethod
    def unknown_result(
        cls,
        *,
        unit: str,
        formula_version: str,
        reason_code: str,
        missing_source: str,
        expected_owner: str,
        affected_fields: Sequence[str],
        source_events: Sequence[str] = (),
    ) -> "MetricResult":
        return cls(
            value=None,
            unit=unit,
            status=Availability.UNKNOWN,
            source_events=tuple(source_events),
            formula_version=formula_version,
            unknown=UnknownValue(
                reason_code,
                missing_source,
                expected_owner,
                tuple(affected_fields),
            ),
        )


@dataclass(frozen=True, init=False)
class VerifiedSafetyEvidence:
    metric_name: str
    value: bool | float
    official_owner_class: str
    criterion_status: str
    source_frame: int
    evaluator_registry_sha256: str
    output_receipt_sha256: str
    source_event_id: str
    binding_sha256: str

    @classmethod
    def from_official_criterion(
        cls,
        criterion: object,
        *,
        metric_name: str,
        source_frame: int,
    ) -> "VerifiedSafetyEvidence":
        """Read a value directly from the exact pre-frozen evaluator owner."""

        expected_class = FROZEN_SAFETY_OWNER_CLASSES.get(metric_name)
        owner_type = type(criterion)
        owner_module = owner_type.__module__
        owner_class = owner_type.__name__
        source_file = inspect.getsourcefile(owner_type)
        if (
            expected_class is None
            or owner_module != FROZEN_EVALUATOR_OWNER_MODULE
            or owner_class != expected_class
            or source_file is None
            or Path(source_file).resolve()
            != FROZEN_EVALUATOR_CRITERIA_SOURCE.resolve()
        ):
            raise RuntimeError("SAFETY_CRITERION_OWNER_NOT_FROZEN")
        registry_sha256 = assert_frozen_evaluator_registry()
        if source_frame < 0:
            raise ValueError("SAFETY_SOURCE_FRAME_INVALID")
        value = getattr(criterion, "actual_value", None)
        status = getattr(criterion, "test_status", None)
        traffic_events = getattr(criterion, "list_traffic_events", None)
        if (
            not isinstance(value, (bool, int, float))
            or not math.isfinite(float(value))
            or status is None
            or not isinstance(traffic_events, list)
        ):
            raise RuntimeError("SAFETY_CRITERION_OUTPUT_INCOMPLETE")
        output_payload = {
            "schema_version": "driveclarify.rq2.verified_safety_owner_output.v1",
            "metric_name": metric_name,
            "value": value,
            "official_owner_module": owner_module,
            "official_owner_class": owner_class,
            "criterion_status": str(status),
            "source_frame": source_frame,
            "traffic_event_count": len(traffic_events),
            "evaluator_registry_sha256": registry_sha256,
        }
        output_receipt_sha256 = canonical_sha256(output_payload)
        source_event_id = "tmvp-safety-" + output_receipt_sha256[:24]
        values = {
            "metric_name": metric_name,
            "value": value,
            "official_owner_class": owner_class,
            "criterion_status": str(status),
            "source_frame": source_frame,
            "evaluator_registry_sha256": registry_sha256,
            "output_receipt_sha256": output_receipt_sha256,
            "source_event_id": source_event_id,
        }
        instance = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(instance, name, value)
        object.__setattr__(instance, "binding_sha256", canonical_sha256(values))
        return instance


@dataclass(frozen=True)
class EgoSample:
    frame: int
    sim_time_s: float
    position_xyz_m: tuple[float, float, float] | None
    yaw_degrees: float | None
    acceleration_world_mps2: tuple[float, float, float] | None
    ego_right_unit_world: tuple[float, float, float] | None
    steer_applied: float | None
    applied_control_verified: bool = True
    acceleration_frame_and_units_verified: bool = True
    coordinate_convention_verified: bool = True
    preceding_frame_gap_identified: bool = True
    event_id: str = ""


def _unknown(name: str, unit: str, reason: str, fields: Sequence[str]) -> MetricResult:
    return MetricResult.unknown_result(
        unit=unit,
        formula_version=CONTINUITY_FORMULA_VERSION,
        reason_code=reason,
        missing_source="ego_tick_trace." + name,
        expected_owner="verified per-simulator-tick trace",
        affected_fields=fields,
    )


def _wrap_degrees(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


def _wrap_radians(value: float) -> float:
    return (float(value) + math.pi) % (2.0 * math.pi) - math.pi


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _available(
    value: float | bool | str,
    unit: str,
    formula: str,
    events: Iterable[str],
) -> MetricResult:
    return MetricResult(
        value=value,
        unit=unit,
        status=Availability.AVAILABLE,
        source_events=tuple(event for event in events if event),
        formula_version=formula,
    )


def continuity_metrics(
    samples: Sequence[EgoSample], *, t_effect_s: float
) -> Mapping[str, MetricResult]:
    ordered = sorted(samples, key=lambda row: row.frame)
    window = [
        row for row in ordered if t_effect_s - 1.0 <= row.sim_time_s <= t_effect_s + 2.0
    ]
    before = [row for row in window if row.sim_time_s < t_effect_s]
    after = [row for row in window if row.sim_time_s >= t_effect_s]
    result: dict[str, MetricResult] = {}

    if not before or not after or before[-1].frame >= after[0].frame:
        result["heading_discontinuity"] = _unknown(
            "heading_discontinuity",
            "degrees",
            "HEADING_BRACKETING_SAMPLES_MISSING_OR_UNORDERED",
            ("yaw_before", "yaw_after", "frame_order"),
        )
        result["steering_discontinuity"] = _unknown(
            "steering_discontinuity",
            "normalized_steer_magnitude_difference",
            "STEERING_BRACKETING_SAMPLES_MISSING_OR_UNORDERED",
            ("steer_before", "steer_after", "frame_order"),
        )
    else:
        minus, plus = before[-1], after[0]
        if minus.yaw_degrees is None or plus.yaw_degrees is None:
            result["heading_discontinuity"] = _unknown(
                "heading_discontinuity",
                "degrees",
                "HEADING_BRACKETING_YAW_MISSING",
                ("yaw_before", "yaw_after"),
            )
        else:
            result["heading_discontinuity"] = _available(
                abs(_wrap_degrees(plus.yaw_degrees - minus.yaw_degrees)),
                "degrees",
                CONTINUITY_FORMULA_VERSION,
                (minus.event_id, plus.event_id),
            )
        if (
            minus.steer_applied is None
            or plus.steer_applied is None
            or not minus.applied_control_verified
            or not plus.applied_control_verified
        ):
            result["steering_discontinuity"] = _unknown(
                "steering_discontinuity",
                "normalized_steer_magnitude_difference",
                "APPLIED_CONTROL_PROVENANCE_MISSING",
                ("steer_before", "steer_after", "control_provenance"),
            )
        else:
            result["steering_discontinuity"] = _available(
                abs(plus.steer_applied - minus.steer_applied),
                "normalized_steer_magnitude_difference",
                CONTINUITY_FORMULA_VERSION,
                (minus.event_id, plus.event_id),
            )

    result["curvature_discontinuity"] = _curvature_discontinuity(
        before[-3:], after[:3]
    )
    result["jerk"] = _jerk(window)
    result["lateral_acceleration"] = _lateral_acceleration(window)
    return result


def _three_point_curvature(rows: Sequence[EgoSample]) -> float | None:
    if len(rows) != 3:
        return None
    if any(row.position_xyz_m is None or row.yaw_degrees is None for row in rows):
        return None
    first, middle, last = rows
    if not (first.frame < middle.frame < last.frame):
        return None
    if not (first.sim_time_s < middle.sim_time_s < last.sim_time_s):
        return None
    assert first.position_xyz_m is not None
    assert middle.position_xyz_m is not None
    assert last.position_xyz_m is not None
    arc = _distance(first.position_xyz_m, middle.position_xyz_m) + _distance(
        middle.position_xyz_m, last.position_xyz_m
    )
    if arc <= 1.0e-6:
        return None
    assert first.yaw_degrees is not None and last.yaw_degrees is not None
    return _wrap_radians(
        math.radians(last.yaw_degrees) - math.radians(first.yaw_degrees)
    ) / arc


def _curvature_discontinuity(
    before: Sequence[EgoSample], after: Sequence[EgoSample]
) -> MetricResult:
    pre = _three_point_curvature(before)
    post = _three_point_curvature(after)
    if pre is None or post is None:
        return _unknown(
            "curvature_discontinuity",
            "m^-1",
            "CURVATURE_THREE_SAME_SIDE_SAMPLES_UNAVAILABLE",
            ("three_pre_positions_yaws", "three_post_positions_yaws", "positive_arc"),
        )
    return _available(
        abs(post - pre),
        "m^-1",
        CONTINUITY_FORMULA_VERSION,
        tuple(row.event_id for row in (*before, *after)),
    )


def _jerk(rows: Sequence[EgoSample]) -> MetricResult:
    if len(rows) < 2:
        return _unknown(
            "jerk",
            "m/s^3",
            "JERK_INSUFFICIENT_ACCELERATION_SAMPLES",
            ("acceleration_samples",),
        )
    values: list[float] = []
    for first, second in zip(rows, rows[1:]):
        if (
            first.acceleration_world_mps2 is None
            or second.acceleration_world_mps2 is None
            or not first.acceleration_frame_and_units_verified
            or not second.acceleration_frame_and_units_verified
            or not second.preceding_frame_gap_identified
        ):
            return _unknown(
                "jerk",
                "m/s^3",
                "JERK_ACCELERATION_OR_GAP_PROVENANCE_INVALID",
                ("acceleration", "frame_gap_identified", "units"),
            )
        delta_t = second.sim_time_s - first.sim_time_s
        if delta_t <= 0.0:
            return _unknown(
                "jerk",
                "m/s^3",
                "JERK_SIMULATOR_TIME_NONMONOTONIC",
                ("sim_time_s",),
            )
        jerk_value = (
            _distance(second.acceleration_world_mps2, first.acceleration_world_mps2)
            / delta_t
        )
        if not math.isfinite(jerk_value):
            return _unknown(
                "jerk",
                "m/s^3",
                "JERK_ACCELERATION_NONFINITE",
                ("acceleration",),
            )
        values.append(jerk_value)
    return _available(
        max(values),
        "m/s^3",
        CONTINUITY_FORMULA_VERSION,
        (row.event_id for row in rows),
    )


def _lateral_acceleration(rows: Sequence[EgoSample]) -> MetricResult:
    if not rows:
        return _unknown(
            "lateral_acceleration",
            "m/s^2",
            "LATERAL_ACCELERATION_WINDOW_EMPTY",
            ("window_samples",),
        )
    values: list[float] = []
    for row in rows:
        if (
            row.acceleration_world_mps2 is None
            or row.ego_right_unit_world is None
            or not row.acceleration_frame_and_units_verified
            or not row.coordinate_convention_verified
        ):
            return _unknown(
                "lateral_acceleration",
                "m/s^2",
                "LATERAL_ACCELERATION_SOURCE_UNVERIFIED",
                ("acceleration", "ego_right_unit", "coordinate_convention"),
            )
        norm = math.sqrt(sum(value * value for value in row.ego_right_unit_world))
        if not math.isfinite(norm) or norm <= 1.0e-12:
            return _unknown(
                "lateral_acceleration",
                "m/s^2",
                "EGO_RIGHT_VECTOR_NOT_UNIT_NORMALIZABLE",
                ("ego_right_unit",),
            )
        right = tuple(value / norm for value in row.ego_right_unit_world)
        lateral = abs(sum(a * b for a, b in zip(row.acceleration_world_mps2, right)))
        if not math.isfinite(lateral):
            return _unknown(
                "lateral_acceleration",
                "m/s^2",
                "LATERAL_ACCELERATION_NONFINITE",
                ("acceleration", "ego_right_unit"),
            )
        values.append(lateral)
    return _available(
        max(values),
        "m/s^2",
        CONTINUITY_FORMULA_VERSION,
        (row.event_id for row in rows),
    )


def recovery_metrics(
    samples: Sequence[EgoSample],
    *,
    update_sim_time_s: float,
    recovery_terminal_frame: int | None,
    recovery_terminal_verified: bool,
    exact_updated_obligation_completed: bool,
    legal_same_g_continuation: bool,
    installed_route_consumed: bool,
) -> Mapping[str, MetricResult]:
    if not all(
        (
            recovery_terminal_verified,
            exact_updated_obligation_completed,
            legal_same_g_continuation,
            installed_route_consumed,
            recovery_terminal_frame is not None,
        )
    ):
        unknown = {
            name: MetricResult.unknown_result(
                unit=unit,
                formula_version=RECOVERY_FORMULA_VERSION,
                reason_code="RECOVERY_NOT_ACHIEVED",
                missing_source="independent recovery terminal",
                expected_owner="frozen local-task and same-G evaluator",
                affected_fields=(
                    "updated_obligation_completed",
                    "same_g_continuation",
                    "installed_route_consumed",
                ),
            )
            for name, unit in (
                ("recovery_time", "s"),
                ("recovery_distance", "m"),
            )
        }
        return unknown
    rows = sorted(
        (
            row
            for row in samples
            if row.sim_time_s >= update_sim_time_s
            and row.frame <= int(recovery_terminal_frame)
        ),
        key=lambda row: row.frame,
    )
    if (
        not rows
        or rows[-1].frame != recovery_terminal_frame
        or rows[0].sim_time_s != update_sim_time_s
    ):
        reason = "RECOVERY_TRACE_TERMINAL_SAMPLE_MISSING"
        return {
            name: MetricResult.unknown_result(
                unit=unit,
                formula_version=RECOVERY_FORMULA_VERSION,
                reason_code=reason,
                missing_source="ego trace",
                expected_owner="per-simulator-tick trace",
                affected_fields=("terminal_frame", "position", "sim_time_s"),
            )
            for name, unit in (
                ("recovery_time", "s"),
                ("recovery_distance", "m"),
            )
        }
    distance = 0.0
    for first, second in zip(rows, rows[1:]):
        if (
            first.position_xyz_m is None
            or second.position_xyz_m is None
            or second.frame <= first.frame
            or second.frame != first.frame + 1
            or second.sim_time_s <= first.sim_time_s
            or not second.preceding_frame_gap_identified
        ):
            return {
                name: MetricResult.unknown_result(
                    unit=unit,
                    formula_version=RECOVERY_FORMULA_VERSION,
                    reason_code="RECOVERY_TRACE_GAPPED_OR_NONMONOTONIC",
                    missing_source="ego trace",
                    expected_owner="per-simulator-tick trace",
                    affected_fields=("frame", "sim_time_s", "position"),
                )
                for name, unit in (
                    ("recovery_time", "s"),
                    ("recovery_distance", "m"),
                )
            }
        distance += _distance(first.position_xyz_m, second.position_xyz_m)
    events = tuple(row.event_id for row in rows)
    return {
        "recovery_time": _available(
            rows[-1].sim_time_s - update_sim_time_s,
            "s",
            RECOVERY_FORMULA_VERSION,
            events,
        ),
        "recovery_distance": _available(
            distance, "m", RECOVERY_FORMULA_VERSION, events
        ),
    }


def independent_commitment_violation(
    independent_evaluator_value: bool | None,
    *,
    evaluator_source_hash: str | None,
) -> MetricResult:
    if independent_evaluator_value is None or not evaluator_source_hash:
        return MetricResult.unknown_result(
            unit="boolean",
            formula_version=COMMITMENT_FORMULA_VERSION,
            reason_code="INDEPENDENT_COMMITMENT_GROUND_TRUTH_MISSING",
            missing_source="oracle topology and executed occupancy evaluator",
            expected_owner="independent frozen commitment evaluator",
            affected_fields=("commitment_violation", "evaluator_source_hash"),
        )
    return _available(
        bool(independent_evaluator_value),
        "boolean",
        COMMITMENT_FORMULA_VERSION,
        (evaluator_source_hash,),
    )


def independent_local_task_metrics(
    *,
    local_adaptation_success: bool | None,
    local_maneuver_terminal: str | None,
    completion_frame: int | None,
    evaluator_source_hash: str | None,
) -> Mapping[str, MetricResult]:
    """Bind already independently evaluated local-task truth without inference."""

    if (
        local_adaptation_success is None
        or local_maneuver_terminal not in LOCAL_TERMINALS
        or not evaluator_source_hash
    ):
        unknown = MetricResult.unknown_result(
            unit="local_task",
            formula_version=LOCAL_TASK_FORMULA_VERSION,
            reason_code="INDEPENDENT_LOCAL_TASK_EVIDENCE_MISSING",
            missing_source="scene task/topology evaluator",
            expected_owner="frozen independent local-task evaluator",
            affected_fields=(
                "local_adaptation_success",
                "local_maneuver_terminal",
                "completion_frame",
            ),
        )
        return {
            "local_adaptation_success": unknown,
            "local_maneuver_terminal": unknown,
            "completion_frame": unknown,
        }
    success = _available(
        bool(local_adaptation_success),
        "boolean",
        LOCAL_TASK_FORMULA_VERSION,
        (evaluator_source_hash,),
    )
    terminal = _available(
        local_maneuver_terminal,
        "terminal_enum",
        LOCAL_TASK_FORMULA_VERSION,
        (evaluator_source_hash,),
    )
    if local_maneuver_terminal == "UPDATED_MANEUVER_COMPLETED":
        if completion_frame is None or completion_frame < 0:
            completion = MetricResult.unknown_result(
                unit="simulator_frame",
                formula_version=LOCAL_TASK_FORMULA_VERSION,
                reason_code="LOCAL_COMPLETION_FRAME_MISSING",
                missing_source="independent completion predicate",
                expected_owner="frozen independent local-task evaluator",
                affected_fields=("completion_frame",),
            )
        else:
            completion = _available(
                float(completion_frame),
                "simulator_frame",
                LOCAL_TASK_FORMULA_VERSION,
                (evaluator_source_hash,),
            )
    else:
        completion = MetricResult(
            value="NOT_APPLICABLE_BY_TERMINAL",
            unit="simulator_frame",
            status=Availability.NOT_APPLICABLE_BY_CONTRACT,
            source_events=(evaluator_source_hash,),
            formula_version=LOCAL_TASK_FORMULA_VERSION,
        )
    return {
        "local_adaptation_success": success,
        "local_maneuver_terminal": terminal,
        "completion_frame": completion,
    }


def independently_verified_global_metric(
    metric_name: str,
    value: bool | None,
    *,
    evaluator_source_hash: str | None,
) -> MetricResult:
    if metric_name not in {"rejoin_success", "final_task_completion"}:
        raise ValueError("GLOBAL_TASK_METRIC_NAME_INVALID")
    if value is None or not evaluator_source_hash:
        return MetricResult.unknown_result(
            unit="boolean",
            formula_version=GLOBAL_TASK_FORMULA_VERSION,
            reason_code="VERIFIED_GLOBAL_TASK_SOURCE_MISSING",
            missing_source=metric_name,
            expected_owner="official/topology global-task evaluator",
            affected_fields=(metric_name,),
        )
    return _available(
        bool(value),
        "boolean",
        GLOBAL_TASK_FORMULA_VERSION,
        (evaluator_source_hash,),
    )


def unnecessary_global_replan(
    *,
    global_planner_call_count: int,
    independent_local_sufficiency_proof: bool | None,
    evaluator_source_hash: str | None,
) -> MetricResult:
    if global_planner_call_count < 0:
        raise ValueError("GLOBAL_PLANNER_CALL_COUNT_INVALID")
    if global_planner_call_count == 0:
        return _available(
            False,
            "boolean",
            EFFICIENCY_FORMULA_VERSION,
            (evaluator_source_hash or "planning-accounting",),
        )
    if independent_local_sufficiency_proof is None or not evaluator_source_hash:
        return MetricResult.unknown_result(
            unit="boolean",
            formula_version=EFFICIENCY_FORMULA_VERSION,
            reason_code="GLOBAL_REPLAN_NECESSITY_PROOF_MISSING",
            missing_source="prospective topology/task proof",
            expected_owner="independent unnecessary-replan evaluator",
            affected_fields=("unnecessary_global_replan",),
        )
    return _available(
        bool(independent_local_sufficiency_proof),
        "boolean",
        EFFICIENCY_FORMULA_VERSION,
        (evaluator_source_hash,),
    )


def same_destination_preservation(
    tasks: Sequence[GlobalTask | None], *, endpoint_delta_m: float | None
) -> MetricResult:
    if any(task is None for task in tasks) or endpoint_delta_m is None:
        return MetricResult.unknown_result(
            unit="boolean",
            formula_version=GLOBAL_TASK_FORMULA_VERSION,
            reason_code="SAME_DESTINATION_EVIDENCE_MISSING",
            missing_source="G/install/reconnect/final evaluator binding",
            expected_owner="mission context and route transaction owners",
            affected_fields=("G_identity", "endpoint_digest", "endpoint_delta_m"),
        )
    concrete = [task for task in tasks if task is not None]
    identities = {task.global_destination_identity for task in concrete}
    digests = {task.destination_endpoint_digest for task in concrete}
    return _available(
        len(identities) == 1 and len(digests) == 1 and endpoint_delta_m <= 0.001,
        "boolean",
        GLOBAL_TASK_FORMULA_VERSION,
        (),
    )


def verified_safety_metric(
    evidence: VerifiedSafetyEvidence | None,
    *,
    metric_name: str,
) -> MetricResult:
    expected_owner = FROZEN_SAFETY_OWNER_CLASSES.get(metric_name)
    evidence_values = None if evidence is None else {
        "metric_name": evidence.metric_name,
        "value": evidence.value,
        "official_owner_class": evidence.official_owner_class,
        "criterion_status": evidence.criterion_status,
        "source_frame": evidence.source_frame,
        "evaluator_registry_sha256": evidence.evaluator_registry_sha256,
        "output_receipt_sha256": evidence.output_receipt_sha256,
        "source_event_id": evidence.source_event_id,
    }
    if (
        evidence is None
        or expected_owner is None
        or evidence.metric_name != metric_name
        or evidence.official_owner_class != expected_owner
        or evidence.evaluator_registry_sha256 != assert_frozen_evaluator_registry()
        or canonical_sha256(evidence_values) != evidence.binding_sha256
    ):
        return MetricResult.unknown_result(
            unit="official_evaluator_field",
            formula_version=SAFETY_FORMULA_VERSION,
            reason_code="VERIFIED_OFFICIAL_SAFETY_SOURCE_MISSING",
            missing_source=metric_name,
            expected_owner="verified official evaluator criterion",
            affected_fields=(metric_name,),
        )
    return _available(
        evidence.value,
        "official_evaluator_field",
        SAFETY_FORMULA_VERSION,
        (
            evidence.source_event_id,
            evidence.evaluator_registry_sha256,
            evidence.output_receipt_sha256,
        ),
    )
