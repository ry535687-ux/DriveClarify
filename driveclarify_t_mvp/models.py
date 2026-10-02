"""Immutable scientific objects and lifecycle states for RQ2 T-MVP."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Mapping, Sequence

from .canonical import canonical_bytes, canonical_sha256


NAVIGATION_PLAN_SCHEMA = "driveclarify.rq2.navigation_plan.v1"


class Availability(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE_BY_CONTRACT = "NOT_APPLICABLE_BY_CONTRACT"


@dataclass(frozen=True)
class UnknownValue:
    reason_code: str
    missing_source: str
    expected_owner: str
    affected_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.reason_code or not self.missing_source or not self.expected_owner:
            raise ValueError("UNKNOWN_PROVENANCE_INCOMPLETE")
        if not self.affected_fields:
            raise ValueError("UNKNOWN_AFFECTED_FIELDS_EMPTY")


@dataclass(frozen=True)
class RouteRow:
    x_m: float
    y_m: float
    z_m: float
    road_option: str
    distance_from_previous_m: float
    coordinate_domain: str = "SIMLINGO_ROUTE_PLANNER"
    unit: str = "m"

    def __post_init__(self) -> None:
        values = (self.x_m, self.y_m, self.z_m, self.distance_from_previous_m)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("ROUTE_ROW_NONFINITE")
        if self.distance_from_previous_m < 0.0:
            raise ValueError("ROUTE_ROW_DISTANCE_NEGATIVE")
        if not self.road_option or not self.coordinate_domain or self.unit != "m":
            raise ValueError("ROUTE_ROW_METADATA_INVALID")


@dataclass(frozen=True)
class GlobalTask:
    global_destination_identity: str
    destination_endpoint_digest: str
    endpoint_xyz_m: tuple[float, float, float]
    coordinate_domain: str = "SIMLINGO_ROUTE_PLANNER"

    @classmethod
    def bind(
        cls,
        global_destination_identity: str,
        endpoint_xyz_m: Sequence[float],
        *,
        coordinate_domain: str = "SIMLINGO_ROUTE_PLANNER",
    ) -> "GlobalTask":
        endpoint = tuple(float(value) for value in endpoint_xyz_m)
        if len(endpoint) != 3 or not all(math.isfinite(value) for value in endpoint):
            raise ValueError("GLOBAL_DESTINATION_ENDPOINT_INVALID")
        if not str(global_destination_identity).strip():
            raise ValueError("GLOBAL_DESTINATION_IDENTITY_INVALID")
        digest = canonical_sha256(
            {
                "schema_version": "driveclarify.rq2.global_task.v1",
                "coordinate_domain": coordinate_domain,
                "unit": "m",
                "endpoint_xyz_m": endpoint,
            }
        )
        return cls(
            global_destination_identity=str(global_destination_identity),
            destination_endpoint_digest=digest,
            endpoint_xyz_m=endpoint,
            coordinate_domain=coordinate_domain,
        )

    @property
    def identity(self) -> str:
        return canonical_sha256(
            {
                "schema_version": "driveclarify.rq2.global_task.v1",
                "global_destination_identity": self.global_destination_identity,
                "destination_endpoint_digest": self.destination_endpoint_digest,
            }
        )


class ObservableCommitmentState(str, Enum):
    BEFORE_OLD_EXCLUSIVITY = "BEFORE_OLD_EXCLUSIVITY"
    OLD_EXCLUSIVE_RECOVERABLE = "OLD_EXCLUSIVE_RECOVERABLE"
    NO_SAFE_CURRENT_OPPORTUNITY = "NO_SAFE_CURRENT_OPPORTUNITY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ObservableCommitment:
    state: ObservableCommitmentState
    source_frame: int
    source_sim_time_s: float
    source_route_generation: int
    allowed_signal_manifest: tuple[str, ...]
    reason_codes: tuple[str, ...]
    input_hashes: tuple[tuple[str, str], ...]
    commitment_confidence: str = "NOT_AVAILABLE"
    oracle_fields_present: bool = False

    def __post_init__(self) -> None:
        if (
            self.source_frame < 0
            or self.source_route_generation < 0
            or not math.isfinite(self.source_sim_time_s)
        ):
            raise ValueError("OBSERVABLE_COMMITMENT_SOURCE_INVALID")
        if self.commitment_confidence != "NOT_AVAILABLE":
            raise ValueError("COMMITMENT_CONFIDENCE_FORBIDDEN")
        if self.oracle_fields_present:
            raise ValueError("ORACLE_COMMITMENT_FIELD_PRESENT")
        if not self.reason_codes:
            raise ValueError("OBSERVABLE_COMMITMENT_REASON_REQUIRED")


@dataclass(frozen=True)
class POldSnapshot:
    authoritative_route_identity: str
    route_generation: int
    global_task: GlobalTask
    full_route_projection: tuple[RouteRow, ...]
    active_suffix_projection: tuple[RouteRow, ...]
    active_suffix_identity: str
    current_maneuver_identity: str
    current_branch_or_connector_identity: str
    route_owner_identity: str
    active_suffix_owner_identity: str
    planner_owner_identity: str
    control_owner_identity: str
    observable_commitment: ObservableCommitment
    source_frame: int
    source_sim_time_s: float
    boundary_identity: str
    last_consumed_route_identity: str | None
    component_hashes: tuple[tuple[str, str], ...]
    canonical_sha256: str
    schema_version: str = NAVIGATION_PLAN_SCHEMA
    authority_role: str = "ACTIVE_PRE_UPDATE"

    def __post_init__(self) -> None:
        if self.canonical_sha256:
            expected = canonical_sha256(self)
            if self.canonical_sha256 != expected:
                raise ValueError("P_OLD_HASH_MISMATCH")

    @property
    def canonical_serialization(self) -> bytes:
        return canonical_bytes(self)


@dataclass(frozen=True)
class BehavioralSnapshot:
    model_forward_identity: str
    planning_frame: int
    pred_route_xy_m: tuple[tuple[float, float], ...]
    pred_speed_wps_xy_m: tuple[tuple[float, float], ...]
    steer: float
    throttle: float
    brake: float
    ego_pose_xyz_yaw: tuple[float, float, float, float]
    ego_speed_mps: float
    pid_invocation_identity: str
    selected_plan_source_receipt: str
    coordinate_convention: str
    unit_manifest: tuple[tuple[str, str], ...]
    tensor_dtype: str = "float32"
    pred_route_shape: tuple[int, int] = field(init=False, default=(0, 0))
    pred_speed_wps_shape: tuple[int, int] = field(init=False, default=(0, 0))
    canonical_sha256: str = field(init=False, default="")

    def __post_init__(self) -> None:
        scalar_values = (
            self.steer,
            self.throttle,
            self.brake,
            self.ego_speed_mps,
            *self.ego_pose_xyz_yaw,
        )
        if not all(math.isfinite(float(value)) for value in scalar_values):
            raise ValueError("BEHAVIORAL_SNAPSHOT_NONFINITE")
        if any(
            not all(math.isfinite(float(value)) for value in row)
            for row in (*self.pred_route_xy_m, *self.pred_speed_wps_xy_m)
        ):
            raise ValueError("BEHAVIORAL_TENSOR_NONFINITE")
        object.__setattr__(
            self,
            "pred_route_shape",
            (len(self.pred_route_xy_m), 2),
        )
        object.__setattr__(
            self,
            "pred_speed_wps_shape",
            (len(self.pred_speed_wps_xy_m), 2),
        )
        object.__setattr__(self, "canonical_sha256", canonical_sha256(self))


class CandidateFeasibility(str, Enum):
    FEASIBLE_NOW = "FEASIBLE_NOW"
    FEASIBLE_AFTER_LEGAL_RECOVERY = "FEASIBLE_AFTER_LEGAL_RECOVERY"
    NO_SAFE_CURRENT_OPPORTUNITY = "NO_SAFE_CURRENT_OPPORTUNITY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LocalNavigationCondition:
    branch_or_connector_identity: str
    route_rows: tuple[RouteRow, ...]
    target_points_ego_local_xy_m: tuple[tuple[float, float], ...]
    first_road_option: str
    preparation_receipt_sha256: str


@dataclass(frozen=True)
class NavigationCandidate:
    candidate_id: str
    candidate_route_identity: str
    candidate_route_generation: int
    global_task: GlobalTask
    semantic_update_event_id: str
    updated_obligation_identity: str
    local_branch_or_connector_identity: str
    full_route_projection: tuple[RouteRow, ...] | str
    active_candidate_suffix: tuple[RouteRow, ...]
    local_navigation_condition: LocalNavigationCondition
    feasibility_state: CandidateFeasibility
    feasibility_reason_codes: tuple[str, ...]
    candidate_source_frame: int
    candidate_source_sim_time_s: float
    candidate_generator_owner: str
    preparation_call_receipts: tuple[str, ...]
    canonical_sha256: str
    schema_version: str = NAVIGATION_PLAN_SCHEMA
    installation_state: str = "UNINSTALLED"

    @classmethod
    def create(cls, **values: Any) -> "NavigationCandidate":
        if "canonical_sha256" in values:
            raise ValueError("CANDIDATE_HASH_CALLER_SUPPLIED")
        provisional = cls(canonical_sha256="", **values)
        digest = canonical_sha256(provisional)
        return cls(canonical_sha256=digest, **values)

    def __post_init__(self) -> None:
        if self.installation_state != "UNINSTALLED":
            raise ValueError("PREPARED_CANDIDATE_MUST_BE_UNINSTALLED")
        if self.candidate_route_generation < 0 or self.candidate_source_frame < 0:
            raise ValueError("CANDIDATE_SOURCE_OR_GENERATION_INVALID")
        if not self.feasibility_reason_codes:
            raise ValueError("CANDIDATE_FEASIBILITY_REASON_REQUIRED")
        if not math.isfinite(self.candidate_source_sim_time_s):
            raise ValueError("CANDIDATE_SOURCE_SIM_TIME_INVALID")
        if self.canonical_sha256:
            expected = canonical_sha256(self)
            if self.canonical_sha256 != expected:
                raise ValueError("CANDIDATE_HASH_MISMATCH")


class PlanState(str, Enum):
    CANDIDATE_PREPARED = "CANDIDATE_PREPARED"
    CANDIDATE_ADMITTED = "CANDIDATE_ADMITTED"
    CANDIDATE_COMMITTED = "CANDIDATE_COMMITTED"
    CANDIDATE_CONSUMED = "CANDIDATE_CONSUMED"


@dataclass(frozen=True)
class LifecycleEvent:
    state: PlanState
    frame: int
    reason_code: str
    route_identity: str
    route_generation: int


class CandidateLifecycle:
    """Append-only candidate lifecycle; installation never implies consumption."""

    def __init__(self, candidate: NavigationCandidate):
        self.candidate = candidate
        self._events = [
            LifecycleEvent(
                PlanState.CANDIDATE_PREPARED,
                candidate.candidate_source_frame,
                "CANDIDATE_BYTES_AND_FEASIBILITY_FROZEN",
                candidate.candidate_route_identity,
                candidate.candidate_route_generation,
            )
        ]

    @property
    def state(self) -> PlanState:
        return self._events[-1].state

    @property
    def events(self) -> tuple[LifecycleEvent, ...]:
        return tuple(self._events)

    def admit(self, *, frame: int, reason_code: str) -> LifecycleEvent:
        if self.state is not PlanState.CANDIDATE_PREPARED:
            raise RuntimeError("CANDIDATE_ADMISSION_ORDER_INVALID")
        return self._append(PlanState.CANDIDATE_ADMITTED, frame, reason_code)

    def commit(
        self,
        *,
        frame: int,
        installed_route_identity: str,
        installed_route_generation: int,
    ) -> LifecycleEvent:
        if self.state is not PlanState.CANDIDATE_ADMITTED:
            raise RuntimeError("CANDIDATE_COMMIT_REQUIRES_ADMISSION")
        if installed_route_identity != self.candidate.candidate_route_identity:
            raise RuntimeError("INSTALLED_CANDIDATE_IDENTITY_MISMATCH")
        if installed_route_generation != self.candidate.candidate_route_generation:
            raise RuntimeError("INSTALLED_CANDIDATE_GENERATION_MISMATCH")
        return self._append(
            PlanState.CANDIDATE_COMMITTED,
            frame,
            "EXACT_ADMITTED_CANDIDATE_INSTALLED",
        )

    def consume(
        self,
        *,
        frame: int,
        consumed_route_identity: str,
        consumed_route_generation: int,
    ) -> LifecycleEvent:
        if self.state is not PlanState.CANDIDATE_COMMITTED:
            raise RuntimeError("CANDIDATE_CONSUMPTION_REQUIRES_COMMIT")
        committed = self._events[-1]
        if frame <= committed.frame:
            raise RuntimeError("CONSUMPTION_NOT_LATER_THAN_INSTALL")
        if consumed_route_identity != self.candidate.candidate_route_identity:
            raise RuntimeError("CONSUMED_ROUTE_IDENTITY_MISMATCH")
        if consumed_route_generation != self.candidate.candidate_route_generation:
            raise RuntimeError("CONSUMED_ROUTE_GENERATION_MISMATCH")
        return self._append(
            PlanState.CANDIDATE_CONSUMED,
            frame,
            "NEXT_LEGITIMATE_NORMAL_CYCLE_CONSUMED",
        )

    def _append(self, state: PlanState, frame: int, reason: str) -> LifecycleEvent:
        if frame < self._events[-1].frame:
            raise RuntimeError("CANDIDATE_LIFECYCLE_FRAME_REGRESSION")
        event = LifecycleEvent(
            state,
            int(frame),
            reason,
            self.candidate.candidate_route_identity,
            self.candidate.candidate_route_generation,
        )
        self._events.append(event)
        return event


def candidate_payload(candidate: NavigationCandidate) -> Mapping[str, Any]:
    """Canonical, policy-safe material for receipts (never lifecycle inference)."""

    return {
        "candidate_id": candidate.candidate_id,
        "candidate_route_identity": candidate.candidate_route_identity,
        "candidate_route_generation": candidate.candidate_route_generation,
        "candidate_destination_identity": (
            candidate.global_task.global_destination_identity
        ),
        "candidate_destination_endpoint_digest": (
            candidate.global_task.destination_endpoint_digest
        ),
        "semantic_update_event_id": candidate.semantic_update_event_id,
        "updated_obligation_identity": candidate.updated_obligation_identity,
        "local_branch_or_connector_identity": (
            candidate.local_branch_or_connector_identity
        ),
        "full_route_projection": candidate.full_route_projection,
        "active_candidate_suffix": candidate.active_candidate_suffix,
        "candidate_local_navigation_condition": (
            candidate.local_navigation_condition
        ),
        "candidate_feasibility_state": candidate.feasibility_state,
        "feasibility_reason_codes": candidate.feasibility_reason_codes,
        "candidate_source_frame": candidate.candidate_source_frame,
        "candidate_source_sim_time_s": candidate.candidate_source_sim_time_s,
        "candidate_generator_owner": candidate.candidate_generator_owner,
        "preparation_call_receipts": candidate.preparation_call_receipts,
        "installation_state": "UNINSTALLED",
        "canonical_sha256": candidate.canonical_sha256,
    }
