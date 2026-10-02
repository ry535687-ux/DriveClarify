"""Fail-closed semantic/physical lifetime for selected navigation authority.

The lifetime in this module is deliberately not expressed as a number of ticks.
It starts after an answer-selected, latest-observation replan and ends only at a
same-frame physical commitment/completion predicate or an explicit defensive
termination.  It owns no planner, controller, or VehicleControl writer.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class ManeuverExecutionState(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    MANEUVER_COMMITTED = "MANEUVER_COMMITTED"
    MANEUVER_COMPLETED = "MANEUVER_COMPLETED"
    INVALIDATED = "INVALIDATED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    RULE_BLOCKED = "RULE_BLOCKED"
    TIME_BUDGET_EXCEEDED = "TIME_BUDGET_EXCEEDED"
    EVIDENCE_UNKNOWN = "EVIDENCE_UNKNOWN"


_FAIL_CLOSED_STATES = frozenset(
    {
        ManeuverExecutionState.INVALIDATED,
        ManeuverExecutionState.SAFETY_BLOCKED,
        ManeuverExecutionState.RULE_BLOCKED,
        ManeuverExecutionState.TIME_BUDGET_EXCEEDED,
        ManeuverExecutionState.EVIDENCE_UNKNOWN,
    }
)
_TERMINAL_STATES = _FAIL_CLOSED_STATES | {
    ManeuverExecutionState.MANEUVER_COMPLETED
}


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _canonical(value.to_dict())
    if hasattr(value, "__dataclass_fields__"):
        return _canonical(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def canonical_identity_digest(value: Any) -> str:
    encoded = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any, name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}_NOT_FINITE") from error
    if not math.isfinite(normalized):
        raise ValueError(f"{name}_NOT_FINITE")
    return normalized


def _identifier(value: Any, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name}_MISSING")
    return normalized


def project_point_to_polyline(
    point_xy_m: Sequence[float], polyline_xy_m: Sequence[Sequence[float]]
) -> tuple[float, float]:
    """Return nearest distance and arc progress along a non-degenerate polyline."""

    if len(point_xy_m) != 2 or len(polyline_xy_m) < 2:
        raise ValueError("POLYLINE_PROJECTION_INPUT_INVALID")
    point_x = _finite(point_xy_m[0], "POINT_X")
    point_y = _finite(point_xy_m[1], "POINT_Y")
    best_distance = math.inf
    best_progress = 0.0
    accumulated = 0.0
    usable_segment = False
    for left, right in zip(polyline_xy_m, polyline_xy_m[1:]):
        if len(left) < 2 or len(right) < 2:
            raise ValueError("POLYLINE_POINT_INVALID")
        left_x = _finite(left[0], "POLYLINE_X")
        left_y = _finite(left[1], "POLYLINE_Y")
        delta_x = _finite(right[0], "POLYLINE_X") - left_x
        delta_y = _finite(right[1], "POLYLINE_Y") - left_y
        squared = delta_x * delta_x + delta_y * delta_y
        if squared <= 1e-12:
            continue
        usable_segment = True
        length = math.sqrt(squared)
        fraction = max(
            0.0,
            min(1.0, ((point_x - left_x) * delta_x + (point_y - left_y) * delta_y) / squared),
        )
        nearest_x = left_x + fraction * delta_x
        nearest_y = left_y + fraction * delta_y
        distance = math.hypot(point_x - nearest_x, point_y - nearest_y)
        progress = accumulated + fraction * length
        if distance < best_distance:
            best_distance = distance
            best_progress = progress
        accumulated += length
    if not usable_segment:
        raise ValueError("POLYLINE_DEGENERATE")
    return float(best_distance), float(best_progress)


@dataclass(frozen=True)
class SelectedNavigationIdentity:
    candidate_id: str
    interpretation_id: str
    obligation_identity: str
    obligation_digest: str
    branch_identity: str
    branch_digest: str
    navigation_context_identity: str
    global_destination_identity: str
    mission_context_digest: str
    route_version: str
    environment_digest: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            object.__setattr__(self, name, _identifier(value, name.upper()))
        if self.obligation_identity != self.obligation_digest:
            raise ValueError("OBLIGATION_IDENTITY_DIGEST_MISMATCH")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BranchCommitmentContract:
    selected_junction_identity: str
    selected_lane_links: tuple[tuple[int, int], ...]
    selected_exit_road_id: int
    selected_exit_lane_id: int
    branch_polyline_xy_m: tuple[tuple[float, float], ...]
    structural_divergence_branch_progress_m: float
    lane_clearance_m: float
    calibrated_uncertainty_m: float
    local_horizon_m: float
    activation_deadline_monotonic_s: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "selected_junction_identity",
            _identifier(self.selected_junction_identity, "SELECTED_JUNCTION_IDENTITY"),
        )
        if not self.selected_lane_links:
            raise ValueError("SELECTED_LANE_LINKS_MISSING")
        normalized_links = tuple(
            (int(road_id), int(lane_id)) for road_id, lane_id in self.selected_lane_links
        )
        object.__setattr__(self, "selected_lane_links", normalized_links)
        object.__setattr__(self, "selected_exit_road_id", int(self.selected_exit_road_id))
        object.__setattr__(self, "selected_exit_lane_id", int(self.selected_exit_lane_id))
        normalized_polyline = tuple(
            (_finite(point[0], "BRANCH_X"), _finite(point[1], "BRANCH_Y"))
            for point in self.branch_polyline_xy_m
        )
        project_point_to_polyline(normalized_polyline[0], normalized_polyline)
        object.__setattr__(self, "branch_polyline_xy_m", normalized_polyline)
        for name in (
            "structural_divergence_branch_progress_m",
            "lane_clearance_m",
            "calibrated_uncertainty_m",
            "local_horizon_m",
            "activation_deadline_monotonic_s",
        ):
            object.__setattr__(self, name, _finite(getattr(self, name), name.upper()))
        if self.structural_divergence_branch_progress_m < 0.0:
            raise ValueError("STRUCTURAL_DIVERGENCE_PROGRESS_INVALID")
        if self.lane_clearance_m <= 0.0 or self.calibrated_uncertainty_m < 0.0:
            raise ValueError("BRANCH_CORRIDOR_BOUND_INVALID")
        if self.local_horizon_m <= 0.0:
            raise ValueError("LOCAL_HORIZON_INVALID")

    @property
    def branch_corridor_tolerance_m(self) -> float:
        return self.lane_clearance_m + self.calibrated_uncertainty_m

    @property
    def maximum_execution_distance_m(self) -> float:
        return self.local_horizon_m + self.calibrated_uncertainty_m

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["branch_corridor_tolerance_m"] = self.branch_corridor_tolerance_m
        value["maximum_execution_distance_m"] = self.maximum_execution_distance_m
        value["fixed_tick_count_used"] = False
        return value


@dataclass(frozen=True)
class LiveManeuverObservation:
    observation_id: str
    frame_id: int
    monotonic_s: float
    simulation_time_s: float
    ego_x_m: float
    ego_y_m: float
    road_id: int
    lane_id: int
    is_junction: bool
    junction_identity: str | None
    route_version: str
    environment_digest: str
    global_destination_identity: str
    navigation_context_identity: str
    safety_certificate_status: str
    rule_certificate_status: str
    alternative_topologically_executable: bool | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _identifier(self.observation_id, "OBSERVATION_ID"))
        if type(self.frame_id) is not int or self.frame_id < 0:
            raise ValueError("FRAME_ID_INVALID")
        for name in ("monotonic_s", "simulation_time_s", "ego_x_m", "ego_y_m"):
            object.__setattr__(self, name, _finite(getattr(self, name), name.upper()))
        object.__setattr__(self, "road_id", int(self.road_id))
        object.__setattr__(self, "lane_id", int(self.lane_id))
        if type(self.is_junction) is not bool:
            raise ValueError("IS_JUNCTION_INVALID")
        for name in (
            "route_version",
            "environment_digest",
            "global_destination_identity",
            "navigation_context_identity",
            "safety_certificate_status",
            "rule_certificate_status",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name.upper()))
        if self.is_junction and not str(self.junction_identity or "").strip():
            raise ValueError("JUNCTION_IDENTITY_MISSING")
        if self.alternative_topologically_executable not in (True, False, None):
            raise ValueError("ALTERNATIVE_FEASIBILITY_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BoundedSemanticManeuverExecution:
    """Semantic/physical state machine with distance and deadline fail-closes."""

    schema_version = "driveclarify.method_revision_v2.execution.v1"

    def __init__(self) -> None:
        self.state = ManeuverExecutionState.PENDING
        self.identity: SelectedNavigationIdentity | None = None
        self.contract: BranchCommitmentContract | None = None
        self.reason_code = "NOT_ACTIVATED"
        self.activation_frame_id: int | None = None
        self.commitment_frame_id: int | None = None
        self.completion_frame_id: int | None = None
        self.release_frame_id: int | None = None
        self.entered_selected_junction = False
        self.traveled_distance_m = 0.0
        self._last_position: tuple[float, float] | None = None
        self._last_frame_id: int | None = None
        self._last_observation_id: str | None = None
        self._last_navigation_frame_id: int | None = None
        self.navigation_tick_evidence: list[dict[str, Any]] = []
        self.physical_tick_evidence: list[dict[str, Any]] = []
        self.transitions: list[dict[str, Any]] = []

    @property
    def selected_navigation_required(self) -> bool:
        return self.state is ManeuverExecutionState.ACTIVE

    @property
    def fail_closed(self) -> bool:
        return self.state in _FAIL_CLOSED_STATES

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def _transition(
        self, state: ManeuverExecutionState, reason_code: str, frame_id: int | None
    ) -> ManeuverExecutionState:
        previous = self.state
        self.state = state
        self.reason_code = _identifier(reason_code, "REASON_CODE")
        self.transitions.append(
            {
                "from": previous.value,
                "to": state.value,
                "reason_code": self.reason_code,
                "frame_id": frame_id,
            }
        )
        if state is ManeuverExecutionState.MANEUVER_COMMITTED:
            self.commitment_frame_id = frame_id
            self.release_frame_id = frame_id
        elif state is ManeuverExecutionState.MANEUVER_COMPLETED:
            self.completion_frame_id = frame_id
        return state

    def activate(
        self,
        identity: SelectedNavigationIdentity,
        contract: BranchCommitmentContract,
        *,
        frame_id: int,
        ego_x_m: float,
        ego_y_m: float,
        current_monotonic_s: float,
    ) -> None:
        if self.state is not ManeuverExecutionState.PENDING:
            raise RuntimeError("EXECUTION_ALREADY_ACTIVATED")
        if not isinstance(identity, SelectedNavigationIdentity) or not isinstance(
            contract, BranchCommitmentContract
        ):
            raise TypeError("EXECUTION_ACTIVATION_CONTRACT_INVALID")
        now = _finite(current_monotonic_s, "ACTIVATION_MONOTONIC")
        if now >= contract.activation_deadline_monotonic_s:
            self.identity = identity
            self.contract = contract
            self.activation_frame_id = int(frame_id)
            self._last_position = (_finite(ego_x_m, "EGO_X"), _finite(ego_y_m, "EGO_Y"))
            self._transition(
                ManeuverExecutionState.TIME_BUDGET_EXCEEDED,
                "ACTIVATION_DEADLINE_ALREADY_EXPIRED",
                int(frame_id),
            )
            return
        self.identity = identity
        self.contract = contract
        self.activation_frame_id = int(frame_id)
        self._last_position = (_finite(ego_x_m, "EGO_X"), _finite(ego_y_m, "EGO_Y"))
        self._last_frame_id = int(frame_id)
        self._transition(
            ManeuverExecutionState.ACTIVE,
            "ANSWER_SELECTED_LATEST_REPLAN_AUTHORIZED",
            int(frame_id),
        )

    def terminate_unknown(self, reason_code: str, frame_id: int | None = None) -> None:
        if self.state in (
            ManeuverExecutionState.PENDING,
            ManeuverExecutionState.ACTIVE,
            ManeuverExecutionState.MANEUVER_COMMITTED,
        ):
            self._transition(ManeuverExecutionState.EVIDENCE_UNKNOWN, reason_code, frame_id)

    def invalidate(self, reason_code: str, frame_id: int | None = None) -> None:
        if self.state in (
            ManeuverExecutionState.PENDING,
            ManeuverExecutionState.ACTIVE,
            ManeuverExecutionState.MANEUVER_COMMITTED,
        ):
            self._transition(ManeuverExecutionState.INVALIDATED, reason_code, frame_id)

    def record_navigation_tick(
        self,
        *,
        observation_id: str,
        frame_id: int,
        navigation_context_identity: str,
        binding_obligation_digest: str,
        binding_branch_digest: str,
        binding_global_destination_identity: str,
        projection_digest: str,
        normal_forward_count: int,
        continued_candidate_comparison_forward_count: int,
    ) -> ManeuverExecutionState:
        if self.state is not ManeuverExecutionState.ACTIVE:
            return self.state
        assert self.identity is not None
        values = {
            "navigation_context_identity": navigation_context_identity,
            "binding_obligation_digest": binding_obligation_digest,
            "binding_branch_digest": binding_branch_digest,
            "binding_global_destination_identity": binding_global_destination_identity,
            "projection_digest": projection_digest,
        }
        try:
            normalized = {name: _identifier(value, name.upper()) for name, value in values.items()}
        except ValueError:
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "NORMAL_FORWARD_NAVIGATION_EVIDENCE_MALFORMED",
                frame_id,
            )
        if (
            normalized["navigation_context_identity"] != self.identity.navigation_context_identity
            or normalized["binding_obligation_digest"] != self.identity.obligation_digest
            or normalized["binding_branch_digest"] != self.identity.branch_digest
            or normalized["binding_global_destination_identity"]
            != self.identity.global_destination_identity
        ):
            return self._transition(
                ManeuverExecutionState.INVALIDATED,
                "SELECTED_NAVIGATION_IDENTITY_CHANGED",
                frame_id,
            )
        if normal_forward_count != 1 or continued_candidate_comparison_forward_count != 0:
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "POST_ANSWER_FORWARD_ACCOUNTING_VIOLATION",
                frame_id,
            )
        if self._last_navigation_frame_id is not None and frame_id == self._last_navigation_frame_id:
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "DUPLICATE_NORMAL_FORWARD_FOR_FRAME",
                frame_id,
            )
        row = {
            "observation_id": _identifier(observation_id, "OBSERVATION_ID"),
            "frame_id": int(frame_id),
            **normalized,
            "normal_forward_count": 1,
            "continued_candidate_comparison_forward_count": 0,
            "projection_digest_may_change_per_live_pose": True,
        }
        self.navigation_tick_evidence.append(row)
        self._last_navigation_frame_id = int(frame_id)
        return self.state

    def observe(self, observation: LiveManeuverObservation) -> ManeuverExecutionState:
        if self.state not in (
            ManeuverExecutionState.ACTIVE,
            ManeuverExecutionState.MANEUVER_COMMITTED,
        ):
            return self.state
        if not isinstance(observation, LiveManeuverObservation):
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "LIVE_OBSERVATION_TYPE_INVALID",
                None,
            )
        assert self.identity is not None and self.contract is not None
        if (
            self._last_frame_id is not None
            and observation.frame_id == self._last_frame_id
            and observation.observation_id == self._last_observation_id
        ):
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "DUPLICATE_LIVE_OBSERVATION",
                observation.frame_id,
            )
        if (
            observation.route_version != self.identity.route_version
            or observation.environment_digest != self.identity.environment_digest
            or observation.global_destination_identity
            != self.identity.global_destination_identity
            or observation.navigation_context_identity
            != self.identity.navigation_context_identity
        ):
            return self._transition(
                ManeuverExecutionState.INVALIDATED,
                "EXECUTION_CONTEXT_IDENTITY_CHANGED",
                observation.frame_id,
            )
        safety = observation.safety_certificate_status.strip().upper()
        rule = observation.rule_certificate_status.strip().upper()
        if safety in {"BLOCKED", "UNSAFE", "FALSE", "DENIED"}:
            return self._transition(
                ManeuverExecutionState.SAFETY_BLOCKED,
                "CURRENT_PHYSICAL_SAFETY_BLOCKED",
                observation.frame_id,
            )
        if rule in {"BLOCKED", "VIOLATION", "FALSE", "DENIED"}:
            return self._transition(
                ManeuverExecutionState.RULE_BLOCKED,
                "CURRENT_HARD_RULE_BLOCKED",
                observation.frame_id,
            )
        if safety not in {"AVAILABLE_TRUE", "PASS", "TRUE", "ALLOWED"} or rule not in {
            "AVAILABLE_TRUE",
            "PASS",
            "TRUE",
            "ALLOWED",
        }:
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "SAFETY_OR_RULE_CERTIFICATE_UNKNOWN",
                observation.frame_id,
            )
        previous_position = self._last_position
        current_position = (observation.ego_x_m, observation.ego_y_m)
        if previous_position is not None:
            self.traveled_distance_m += math.hypot(
                current_position[0] - previous_position[0],
                current_position[1] - previous_position[1],
            )
        self._last_position = current_position
        self._last_frame_id = observation.frame_id
        self._last_observation_id = observation.observation_id
        if self.state is ManeuverExecutionState.ACTIVE:
            if observation.monotonic_s > self.contract.activation_deadline_monotonic_s:
                return self._transition(
                    ManeuverExecutionState.TIME_BUDGET_EXCEEDED,
                    "FROZEN_COMMITMENT_DEADLINE_EXCEEDED",
                    observation.frame_id,
                )
            if self.traveled_distance_m > self.contract.maximum_execution_distance_m:
                return self._transition(
                    ManeuverExecutionState.TIME_BUDGET_EXCEEDED,
                    "LOCAL_HORIZON_DISTANCE_EXCEEDED",
                    observation.frame_id,
                )
        selected_junction_now = bool(
            observation.is_junction
            and observation.junction_identity == self.contract.selected_junction_identity
        )
        self.entered_selected_junction = bool(
            self.entered_selected_junction or selected_junction_now
        )
        lane_link_match = (
            (observation.road_id, observation.lane_id) in self.contract.selected_lane_links
            or (
                observation.road_id == self.contract.selected_exit_road_id
                and observation.lane_id == self.contract.selected_exit_lane_id
            )
        )
        try:
            branch_distance, branch_progress = project_point_to_polyline(
                current_position, self.contract.branch_polyline_xy_m
            )
        except ValueError:
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "LIVE_BRANCH_PROJECTION_UNKNOWN",
                observation.frame_id,
            )
        row = {
            **observation.to_dict(),
            "entered_selected_junction": self.entered_selected_junction,
            "selected_lane_link_match": lane_link_match,
            "branch_corridor_distance_m": branch_distance,
            "branch_progress_m": branch_progress,
            "required_structural_divergence_branch_progress_m": (
                self.contract.structural_divergence_branch_progress_m
            ),
            "traveled_distance_m": self.traveled_distance_m,
            "fixed_tick_count_predicate_used": False,
        }
        self.physical_tick_evidence.append(row)
        if self.state is ManeuverExecutionState.MANEUVER_COMMITTED:
            if (
                not observation.is_junction
                and observation.road_id == self.contract.selected_exit_road_id
                and observation.lane_id == self.contract.selected_exit_lane_id
            ):
                return self._transition(
                    ManeuverExecutionState.MANEUVER_COMPLETED,
                    "SELECTED_JUNCTION_EXIT_OBSERVED",
                    observation.frame_id,
                )
            return self.state
        if observation.alternative_topologically_executable is None:
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "ALTERNATIVE_TOPOLOGICAL_FEASIBILITY_UNKNOWN",
                observation.frame_id,
            )
        committed = bool(
            self.entered_selected_junction
            and lane_link_match
            and branch_distance <= self.contract.branch_corridor_tolerance_m
            and branch_progress
            >= self.contract.structural_divergence_branch_progress_m
            and observation.alternative_topologically_executable is False
        )
        if committed:
            return self._transition(
                ManeuverExecutionState.MANEUVER_COMMITTED,
                "SELECTED_BRANCH_STRUCTURAL_DIVERGENCE_COMMITTED",
                observation.frame_id,
            )
        return self.state

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "reason_code": self.reason_code,
            "selected_navigation_required": self.selected_navigation_required,
            "fail_closed": self.fail_closed,
            "terminal": self.terminal,
            "identity": None if self.identity is None else self.identity.to_dict(),
            "contract": None if self.contract is None else self.contract.to_dict(),
            "activation_frame_id": self.activation_frame_id,
            "commitment_frame_id": self.commitment_frame_id,
            "completion_frame_id": self.completion_frame_id,
            "release_frame_id": self.release_frame_id,
            "entered_selected_junction": self.entered_selected_junction,
            "traveled_distance_m": self.traveled_distance_m,
            "selected_navigation_tick_count": len(self.navigation_tick_evidence),
            "navigation_tick_evidence": list(self.navigation_tick_evidence),
            "physical_tick_evidence": list(self.physical_tick_evidence),
            "transitions": list(self.transitions),
            "fixed_n_tick_dependency": False,
            "new_planner_count": 0,
            "new_pid_count": 0,
            "direct_vehicle_control_write_count": 0,
        }
