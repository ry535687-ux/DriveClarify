"""Pure contracts for candidate-local navigation and fixture reconnection.

The module owns no map search, controller, simulator, or model behavior.  A
caller supplies an already-existing route-trace capability and local branches
whose scene/map grounding is a trusted external provenance boundary pending
native validation.  Candidate qualification retains only reachability status
and an optional route cost; the qualification trace is deliberately discarded.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from driveclarify_paper_mvp_runtime.contracts import RuntimeCandidate


CAUSE_OF_DIFFERENCE = "PASSENGER_INTERPRETATION"
TARGET_POINT_PROMPT_FRAGMENT = "Target waypoint: <TARGET_POINT><TARGET_POINT>."
DEFAULT_MAXIMUM_LOCAL_HORIZON_M = 100.0
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class CandidateLocalNavigationContractError(ValueError):
    """Raised when a candidate-local navigation contract fails closed."""


class RecoverabilityStatus(str, Enum):
    RECOVERABLE_TO_GLOBAL_GOAL = "RECOVERABLE_TO_GLOBAL_GOAL"
    UNRECOVERABLE_TO_GLOBAL_GOAL = "UNRECOVERABLE_TO_GLOBAL_GOAL"
    UNKNOWN_GLOBAL_RECOVERABILITY = "UNKNOWN_GLOBAL_RECOVERABILITY"


class QualificationStatus(str, Enum):
    QUALIFIED = "QUALIFIED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    UNRECOVERABLE = "UNRECOVERABLE"
    UNKNOWN = "UNKNOWN"


class GlobalRouteState(str, Enum):
    ROUTE_CURRENT = "ROUTE_CURRENT"
    ROUTE_STALE_RECONNECTION_REQUIRED = "ROUTE_STALE_RECONNECTION_REQUIRED"
    ROUTE_RECONNECTED = "ROUTE_RECONNECTED"
    ROUTE_RECONNECTION_FAILED = "ROUTE_RECONNECTION_FAILED"


def _require_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise CandidateLocalNavigationContractError(field_name + "_INVALID")
    return value


def _require_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _HEX_DIGEST.fullmatch(value):
        raise CandidateLocalNavigationContractError(field_name + "_INVALID")
    return value


def _require_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CandidateLocalNavigationContractError(field_name + "_INVALID")
    converted = float(value)
    if not math.isfinite(converted):
        raise CandidateLocalNavigationContractError(field_name + "_INVALID")
    return converted


def _canonical(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, NavigationPoint):
        return [value.x_m, value.y_m, value.z_m]
    if isinstance(value, LocalRouteElement):
        return {"point": _canonical(value.point), "road_option": value.road_option}
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise CandidateLocalNavigationContractError("NON_CANONICAL_DIGEST_INPUT")


def canonical_navigation_digest(value: object) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


TARGET_POINT_ABI = "SIMLINGO_DUAL_TARGET_POINT_EGO_LOCAL_XY_M_V1"


def canonical_navigation_projection(
    points: object,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Canonicalize exactly the two ego-local points that cross the ABI.

    Shape, ordering, and numeric representation are fixed here so that the
    digest below is a function of the consumed content alone.  ``+ 0.0``
    collapses ``-0.0`` onto ``0.0`` because the two encode the same coordinate
    but do not share a JSON spelling.  Non-finite values fail closed.
    """

    sequence = _route_sequence(points)
    if sequence is None or len(sequence) != 2:
        raise CandidateLocalNavigationContractError(
            "TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH"
        )
    normalized: list[tuple[float, float]] = []
    for row in sequence:
        pair = _route_sequence(row)
        if pair is None or len(pair) != 2:
            raise CandidateLocalNavigationContractError(
                "TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH"
            )
        normalized.append(
            (
                _require_finite(pair[0], "TARGET_POINT_X") + 0.0,
                _require_finite(pair[1], "TARGET_POINT_Y") + 0.0,
            )
        )
    return (normalized[0], normalized[1])


def navigation_projection_digest(points: object) -> str:
    """Digest the exact navigation content consumed by one model forward.

    Only the two projected target points and the ABI identifier participate.
    Candidate/interpretation identifiers, prompt text, and ordinals are
    deliberately excluded so that the digest cannot stand in for language or
    bookkeeping identity.
    """

    return canonical_navigation_digest(
        {
            "target_point_abi": TARGET_POINT_ABI,
            "target_points_ego_local_xy_m": canonical_navigation_projection(points),
        }
    )


def _planner_endpoint_snapshot(value: object, *, _depth: int = 0) -> object:
    """Create a deterministic, import-free identity for planner endpoints."""

    if _depth > 6:
        raise CandidateLocalNavigationContractError("PLANNER_ENDPOINT_NESTING_INVALID")
    if value is None:
        raise CandidateLocalNavigationContractError("PLANNER_ENDPOINT_MISSING")
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return _require_finite(value, "PLANNER_ENDPOINT_SCALAR")
    if isinstance(value, Mapping):
        return {
            str(key): _planner_endpoint_snapshot(item, _depth=_depth + 1)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    sequence = _route_sequence(value)
    if sequence is not None:
        return [
            _planner_endpoint_snapshot(item, _depth=_depth + 1)
            for item in sequence
        ]
    location = getattr(value, "location", None)
    if location is not None and location is not value:
        return _planner_endpoint_snapshot(location, _depth=_depth + 1)
    transform = getattr(value, "transform", None)
    if transform is not None and transform is not value:
        transform_location = getattr(transform, "location", None)
        if transform_location is not None:
            return _planner_endpoint_snapshot(
                transform_location, _depth=_depth + 1
            )
    try:
        x_value = getattr(value, "x")
        y_value = getattr(value, "y")
    except AttributeError as error:
        raise CandidateLocalNavigationContractError(
            "PLANNER_ENDPOINT_IDENTITY_UNAVAILABLE"
        ) from error
    z_value = getattr(value, "z", 0.0)
    return {
        "x": _require_finite(x_value, "PLANNER_ENDPOINT_X"),
        "y": _require_finite(y_value, "PLANNER_ENDPOINT_Y"),
        "z": _require_finite(z_value, "PLANNER_ENDPOINT_Z"),
    }


def _planner_endpoint_digest(value: object) -> str:
    return canonical_navigation_digest(
        {"planner_endpoint": _planner_endpoint_snapshot(value)}
    )


@dataclass(frozen=True)
class NavigationPoint:
    """A point in the declared CARLA-world metric frame."""

    x_m: float
    y_m: float
    z_m: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "x_m", _require_finite(self.x_m, "POINT_X"))
        object.__setattr__(self, "y_m", _require_finite(self.y_m, "POINT_Y"))
        object.__setattr__(self, "z_m", _require_finite(self.z_m, "POINT_Z"))


@dataclass(frozen=True)
class EgoPose2D:
    """Latest ego pose in world metres with yaw in radians."""

    x_m: float
    y_m: float
    yaw_rad: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x_m", _require_finite(self.x_m, "EGO_X"))
        object.__setattr__(self, "y_m", _require_finite(self.y_m, "EGO_Y"))
        object.__setattr__(self, "yaw_rad", _require_finite(self.yaw_rad, "EGO_YAW"))


@dataclass(frozen=True)
class EgoPoseWorldXY:
    """Runtime-facing spelling of the latest world pose."""

    x_m: float
    y_m: float
    yaw_radians: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x_m", _require_finite(self.x_m, "EGO_X"))
        object.__setattr__(self, "y_m", _require_finite(self.y_m, "EGO_Y"))
        object.__setattr__(
            self, "yaw_radians", _require_finite(self.yaw_radians, "EGO_YAW")
        )

    @property
    def yaw_rad(self) -> float:
        return self.yaw_radians


@dataclass(frozen=True)
class LocalRouteElement:
    point: NavigationPoint
    road_option: str

    def __post_init__(self) -> None:
        if not isinstance(self.point, NavigationPoint):
            raise CandidateLocalNavigationContractError("LOCAL_ROUTE_POINT_INVALID")
        _require_identifier(self.road_option, "ROAD_OPTION")


@dataclass(frozen=True)
class MissionNavigationContext:
    """Digest-pinned owner reference for the one unchanged global destination."""

    global_destination_identity: str
    global_destination_planner_endpoint: object
    nominal_global_route_identity: str
    global_destination_endpoint_digest: str = field(init=False)
    mission_context_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identifier(
            self.global_destination_identity, "GLOBAL_DESTINATION_IDENTITY"
        )
        _require_identifier(
            self.nominal_global_route_identity, "NOMINAL_GLOBAL_ROUTE_IDENTITY"
        )
        if self.global_destination_planner_endpoint is None:
            raise CandidateLocalNavigationContractError(
                "GLOBAL_DESTINATION_PLANNER_ENDPOINT_MISSING"
            )
        endpoint_digest = _planner_endpoint_digest(
            self.global_destination_planner_endpoint
        )
        object.__setattr__(
            self, "global_destination_endpoint_digest", endpoint_digest
        )
        object.__setattr__(
            self,
            "mission_context_digest",
            canonical_navigation_digest(
                {
                    "global_destination_identity": self.global_destination_identity,
                    "global_destination_endpoint_digest": endpoint_digest,
                    "nominal_global_route_identity": (
                        self.nominal_global_route_identity
                    ),
                }
            ),
        )

    def assert_endpoint_unchanged(self) -> None:
        if (
            _planner_endpoint_digest(self.global_destination_planner_endpoint)
            != self.global_destination_endpoint_digest
        ):
            raise CandidateLocalNavigationContractError(
                "GLOBAL_DESTINATION_ENDPOINT_CHANGED"
            )


@dataclass(frozen=True)
class ResolvedPassengerInterpretation:
    candidate_id: str
    interpretation_id: str
    source_observation_id: str
    anchor_identity: str
    maneuver_family: str
    maneuver_direction: str
    local_branch_identity: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.candidate_id, "CANDIDATE_ID"),
            (self.interpretation_id, "INTERPRETATION_ID"),
            (self.source_observation_id, "SOURCE_OBSERVATION_ID"),
            (self.anchor_identity, "ANCHOR_IDENTITY"),
            (self.maneuver_family, "MANEUVER_FAMILY"),
            (self.maneuver_direction, "MANEUVER_DIRECTION"),
            (self.local_branch_identity, "LOCAL_BRANCH_IDENTITY"),
        ):
            _require_identifier(value, name)


@dataclass(frozen=True)
class SceneGroundedLocalBranch:
    """Direction-neutral, externally discovered bounded branch descriptor."""

    local_branch_identity: str
    availability: bool
    local_target: NavigationPoint | None
    local_route_segment: tuple[LocalRouteElement, ...]
    model_target_points_world: tuple[NavigationPoint, ...]
    changes_nominal_route: bool
    local_horizon_m: float
    branch_exit_planner_endpoint: object

    def __post_init__(self) -> None:
        _require_identifier(self.local_branch_identity, "LOCAL_BRANCH_IDENTITY")
        if type(self.availability) is not bool:
            raise CandidateLocalNavigationContractError("BRANCH_AVAILABILITY_INVALID")
        if self.local_target is not None and not isinstance(
            self.local_target, NavigationPoint
        ):
            raise CandidateLocalNavigationContractError("LOCAL_TARGET_INVALID")
        if not isinstance(self.local_route_segment, tuple) or any(
            not isinstance(item, LocalRouteElement)
            for item in self.local_route_segment
        ):
            raise CandidateLocalNavigationContractError(
                "LOCAL_ROUTE_SEGMENT_INVALID"
            )
        if not isinstance(self.model_target_points_world, tuple) or any(
            not isinstance(item, NavigationPoint)
            for item in self.model_target_points_world
        ):
            raise CandidateLocalNavigationContractError("MODEL_TARGET_POINTS_INVALID")
        if type(self.changes_nominal_route) is not bool:
            raise CandidateLocalNavigationContractError(
                "CHANGES_NOMINAL_ROUTE_FLAG_INVALID"
            )
        object.__setattr__(
            self,
            "local_horizon_m",
            _require_finite(self.local_horizon_m, "LOCAL_HORIZON"),
        )


@dataclass(frozen=True)
class ExistingPlannerTraceResult:
    """Optional explicit envelope accepted from an injected trace adapter."""

    route: tuple[object, ...]
    route_cost: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.route, tuple):
            raise CandidateLocalNavigationContractError("PLANNER_ROUTE_INVALID")
        if self.route_cost is not None:
            cost = _require_finite(self.route_cost, "ROUTE_COST")
            if cost < 0.0:
                raise CandidateLocalNavigationContractError("ROUTE_COST_INVALID")
            object.__setattr__(self, "route_cost", cost)


@runtime_checkable
class ExistingPlannerTraceCapability(Protocol):
    """Port for the shared, already-created global route planner."""

    def trace_route(self, origin: object, destination: object) -> object:
        ...


@runtime_checkable
class FixtureRouteOwner(Protocol):
    """Fixture-only route acknowledgement port; not a live-agent adapter."""

    def invalidate_nominal_route(self, nominal_route_identity: str) -> bool:
        ...

    def install_reconnected_route(
        self,
        fresh_route: object,
        *,
        global_destination_identity: str,
        global_destination_planner_endpoint: object,
    ) -> bool:
        ...


@dataclass(frozen=True)
class RecoverabilityAssessment:
    status: RecoverabilityStatus
    reason_code: str
    planner_provenance: str
    route_cost_after_reconnect: float | None
    planner_call_count: int
    # Set only when the single planner call itself raised.  Evidence for telling
    # an engineering exception apart from a genuine planner-side unknown; never
    # read for control flow and never a licence to upgrade the status.
    planner_call_error_type: str | None = None

    def __post_init__(self) -> None:
        # Exactly one planner call per assessment, or zero when the existing
        # capability was unavailable/malformed and the answer is UNKNOWN.
        if self.planner_call_count not in (0, 1):
            raise CandidateLocalNavigationContractError(
                "RECOVERABILITY_PLANNER_CALL_COUNT_INVALID"
            )
        if (
            self.planner_call_count == 0
            and self.status is not RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY
        ):
            raise CandidateLocalNavigationContractError(
                "RECOVERABILITY_PLANNER_CALL_COUNT_INVALID"
            )
        _require_identifier(self.reason_code, "RECOVERABILITY_REASON")
        if not isinstance(self.planner_provenance, str) or not self.planner_provenance:
            raise CandidateLocalNavigationContractError("PLANNER_PROVENANCE_INVALID")
        if self.route_cost_after_reconnect is not None:
            cost = _require_finite(
                self.route_cost_after_reconnect, "ROUTE_COST_AFTER_RECONNECT"
            )
            if cost < 0.0:
                raise CandidateLocalNavigationContractError(
                    "ROUTE_COST_AFTER_RECONNECT_INVALID"
                )


@dataclass(frozen=True)
class _NormalizedPlannerTrace:
    status: RecoverabilityStatus
    route_object: object | None
    route_cost: float | None
    reason_code: str


def _route_sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return value
    return None


def _normalize_planner_trace(raw: object) -> _NormalizedPlannerTrace:
    route_object: object
    cost: object = None
    if isinstance(raw, ExistingPlannerTraceResult):
        route_object = raw.route
        cost = raw.route_cost
    elif isinstance(raw, Mapping):
        if "route" not in raw:
            return _NormalizedPlannerTrace(
                RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY,
                None,
                None,
                "MALFORMED_PLANNER_RESULT",
            )
        route_object = raw["route"]
        cost = raw.get("route_cost", raw.get("cost"))
    elif hasattr(raw, "route"):
        route_object = getattr(raw, "route")
        cost = getattr(raw, "route_cost", getattr(raw, "cost", None))
    else:
        route_object = raw

    route = _route_sequence(route_object)
    if route is None:
        return _NormalizedPlannerTrace(
            RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY,
            None,
            None,
            "MALFORMED_PLANNER_RESULT",
        )
    if cost is not None:
        try:
            normalized_cost = _require_finite(cost, "ROUTE_COST")
        except CandidateLocalNavigationContractError:
            return _NormalizedPlannerTrace(
                RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY,
                None,
                None,
                "MALFORMED_PLANNER_RESULT",
            )
        if normalized_cost < 0.0:
            return _NormalizedPlannerTrace(
                RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY,
                None,
                None,
                "MALFORMED_PLANNER_RESULT",
            )
    else:
        normalized_cost = None
    if len(route) == 0:
        return _NormalizedPlannerTrace(
            RecoverabilityStatus.UNRECOVERABLE_TO_GLOBAL_GOAL,
            route_object,
            normalized_cost,
            "UNRECOVERABLE_TO_GLOBAL_GOAL",
        )
    return _NormalizedPlannerTrace(
        RecoverabilityStatus.RECOVERABLE_TO_GLOBAL_GOAL,
        route_object,
        normalized_cost,
        "RECOVERABLE_TO_GLOBAL_GOAL",
    )


class PlannerCapabilityStatus(str, Enum):
    PLANNER_CAPABILITY_AVAILABLE = "PLANNER_CAPABILITY_AVAILABLE"
    PLANNER_CAPABILITY_UNAVAILABLE = "PLANNER_CAPABILITY_UNAVAILABLE"
    PLANNER_CAPABILITY_MALFORMED = "PLANNER_CAPABILITY_MALFORMED"


class ExistingPlannerCapability:
    """Read-only handle on one already-existing route-trace planner object.

    The class never constructs a planner.  ``from_carla_data_provider`` only
    reads the existing ``CarlaDataProvider._grp`` owner, so a missing owner is
    reported as unavailable instead of being silently replaced.
    """

    PROVENANCE_CARLA_DATA_PROVIDER_GRP = (
        "srunner.scenariomanager.carla_data_provider.CarlaDataProvider._grp"
    )

    def __init__(
        self,
        planner_object: object,
        *,
        status: PlannerCapabilityStatus = (
            PlannerCapabilityStatus.PLANNER_CAPABILITY_AVAILABLE
        ),
        provenance: str | None = None,
    ) -> None:
        if not isinstance(status, PlannerCapabilityStatus):
            raise CandidateLocalNavigationContractError(
                "PLANNER_CAPABILITY_STATUS_INVALID"
            )
        if status is PlannerCapabilityStatus.PLANNER_CAPABILITY_AVAILABLE:
            if planner_object is None:
                status = PlannerCapabilityStatus.PLANNER_CAPABILITY_UNAVAILABLE
            elif not callable(getattr(planner_object, "trace_route", None)):
                status = PlannerCapabilityStatus.PLANNER_CAPABILITY_MALFORMED
        self._planner_object = planner_object
        self._status = status
        if provenance is not None:
            resolved_provenance = str(provenance)
        else:
            explicit = getattr(planner_object, "planner_provenance", None)
            resolved_provenance = (
                str(explicit)
                if explicit
                else type(planner_object).__module__
                + "."
                + type(planner_object).__qualname__
            )
        self._provenance = resolved_provenance

    @classmethod
    def from_carla_data_provider(cls) -> "ExistingPlannerCapability":
        """Lazily resolve the existing planner owner; never instantiate one."""

        try:
            from srunner.scenariomanager.carla_data_provider import (  # noqa: PLC0415
                CarlaDataProvider,
            )
        except Exception:
            return cls(
                None,
                status=PlannerCapabilityStatus.PLANNER_CAPABILITY_UNAVAILABLE,
                provenance=cls.PROVENANCE_CARLA_DATA_PROVIDER_GRP,
            )
        planner = getattr(CarlaDataProvider, "_grp", None)
        if planner is None:
            accessor = getattr(CarlaDataProvider, "get_global_route_planner", None)
            if callable(accessor):
                try:
                    planner = accessor()
                except Exception:
                    planner = None
        if planner is None:
            return cls(
                None,
                status=PlannerCapabilityStatus.PLANNER_CAPABILITY_UNAVAILABLE,
                provenance=cls.PROVENANCE_CARLA_DATA_PROVIDER_GRP,
            )
        return cls(planner, provenance=cls.PROVENANCE_CARLA_DATA_PROVIDER_GRP)

    @classmethod
    def coerce(cls, value: object) -> "ExistingPlannerCapability":
        if isinstance(value, ExistingPlannerCapability):
            return value
        if value is None:
            raise CandidateLocalNavigationContractError("EXISTING_PLANNER_MISSING")
        return cls(value)

    @property
    def planner_object(self) -> object:
        return self._planner_object

    @property
    def status(self) -> PlannerCapabilityStatus:
        return self._status

    @property
    def available(self) -> bool:
        return self._status is PlannerCapabilityStatus.PLANNER_CAPABILITY_AVAILABLE

    @property
    def provenance(self) -> str:
        return self._provenance

    def trace_route(self, origin: object, destination: object) -> object:
        if not self.available:
            raise CandidateLocalNavigationContractError(self._status.value)
        return self._planner_object.trace_route(origin, destination)


class GlobalRecoverabilityEvaluator:
    """One-call reachability adapter that never exposes the returned route."""

    def __init__(
        self,
        existing_planner: ExistingPlannerTraceCapability | ExistingPlannerCapability,
    ) -> None:
        if existing_planner is None:
            raise CandidateLocalNavigationContractError("EXISTING_PLANNER_MISSING")
        capability = ExistingPlannerCapability.coerce(existing_planner)
        self._planner_capability = capability
        self._existing_planner = capability.planner_object
        self._planner_call_count = 0
        self._planner_provenance = capability.provenance

    @property
    def planner_call_count(self) -> int:
        return self._planner_call_count

    @property
    def planner_capability(self) -> ExistingPlannerCapability:
        return self._planner_capability

    @property
    def planner_object(self) -> object:
        return self._planner_capability.planner_object

    @property
    def planner_capability_status(self) -> PlannerCapabilityStatus:
        return self._planner_capability.status

    def assess(
        self, branch_exit_planner_endpoint: object, mission: MissionNavigationContext
    ) -> RecoverabilityAssessment:
        if branch_exit_planner_endpoint is None:
            raise CandidateLocalNavigationContractError("BRANCH_EXIT_ENDPOINT_MISSING")
        if not isinstance(mission, MissionNavigationContext):
            raise CandidateLocalNavigationContractError("MISSION_CONTEXT_INVALID")
        mission.assert_endpoint_unchanged()
        if not self._planner_capability.available:
            # A missing or malformed existing planner is unknown, never a claim
            # that the branch cannot reach the destination.
            return RecoverabilityAssessment(
                status=RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY,
                reason_code=self._planner_capability.status.value,
                planner_provenance=self._planner_provenance,
                route_cost_after_reconnect=None,
                planner_call_count=0,
            )
        self._planner_call_count += 1
        error_type: str | None = None
        try:
            # Same capability object, same route semantics as reconnection.
            raw = self._planner_capability.trace_route(
                branch_exit_planner_endpoint,
                mission.global_destination_planner_endpoint,
            )
            normalized = _normalize_planner_trace(raw)
        except Exception as error:
            # Still UNKNOWN and still fail-closed, but no longer indistinguishable
            # from a genuine planner-side unknown: a raising call is its own
            # reason code, mirroring the reconnection path, so an engineering
            # exception cannot masquerade as scene evidence in the receipts.
            error_type = type(error).__name__
            normalized = _NormalizedPlannerTrace(
                RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY,
                None,
                None,
                "PLANNER_TRACE_CALL_RAISED",
            )
        # Only scalar status/cost/provenance cross this boundary.  The local
        # variable containing the candidate-specific trace is not retained.
        return RecoverabilityAssessment(
            status=normalized.status,
            reason_code=normalized.reason_code,
            planner_provenance=self._planner_provenance,
            route_cost_after_reconnect=normalized.route_cost,
            planner_call_count=1,
            planner_call_error_type=error_type,
        )


@dataclass(frozen=True)
class CandidateLocalNavigationObligation:
    obligation_identity: str
    obligation_digest: str
    candidate_id: str
    interpretation_id: str
    source_observation_id: str
    global_destination_identity: str
    nominal_global_route_identity: str
    mission_context_digest: str
    anchor_identity: str
    maneuver_family: str
    maneuver_direction: str
    local_branch_identity: str
    changes_nominal_route: bool
    branch_digest: str
    local_target: NavigationPoint | None
    local_route_segment: tuple[LocalRouteElement, ...]
    model_target_points_world: tuple[NavigationPoint, ...]
    target_digest: str
    model_target_projection_digest: str | None
    local_horizon_m: float
    global_goal_recoverability: RecoverabilityStatus
    route_cost_after_reconnect: float | None
    qualification_status: QualificationStatus
    reason_code: str
    cause_of_difference: str
    planner_provenance: str | None
    qualification_planner_call_count: int

    def __post_init__(self) -> None:
        _require_digest(self.obligation_identity, "OBLIGATION_IDENTITY")
        _require_digest(self.obligation_digest, "OBLIGATION_DIGEST")
        if self.obligation_identity != self.obligation_digest:
            raise CandidateLocalNavigationContractError(
                "OBLIGATION_IDENTITY_DIGEST_MISMATCH"
            )
        _require_digest(self.branch_digest, "BRANCH_DIGEST")
        _require_digest(self.target_digest, "TARGET_DIGEST")
        _require_digest(self.mission_context_digest, "MISSION_CONTEXT_DIGEST")
        if self.model_target_projection_digest is not None:
            _require_digest(
                self.model_target_projection_digest, "MODEL_TARGET_PROJECTION_DIGEST"
            )
        for value, name in (
            (self.candidate_id, "CANDIDATE_ID"),
            (self.interpretation_id, "INTERPRETATION_ID"),
            (self.source_observation_id, "SOURCE_OBSERVATION_ID"),
            (self.global_destination_identity, "GLOBAL_DESTINATION_IDENTITY"),
            (self.nominal_global_route_identity, "NOMINAL_GLOBAL_ROUTE_IDENTITY"),
            (self.anchor_identity, "ANCHOR_IDENTITY"),
            (self.maneuver_family, "MANEUVER_FAMILY"),
            (self.maneuver_direction, "MANEUVER_DIRECTION"),
            (self.local_branch_identity, "LOCAL_BRANCH_IDENTITY"),
            (self.reason_code, "QUALIFICATION_REASON"),
        ):
            _require_identifier(value, name)
        if self.cause_of_difference != CAUSE_OF_DIFFERENCE:
            raise CandidateLocalNavigationContractError("CAUSE_OF_DIFFERENCE_INVALID")
        if type(self.changes_nominal_route) is not bool:
            raise CandidateLocalNavigationContractError(
                "CHANGES_NOMINAL_ROUTE_FLAG_INVALID"
            )
        if not isinstance(self.global_goal_recoverability, RecoverabilityStatus):
            raise CandidateLocalNavigationContractError(
                "GLOBAL_RECOVERABILITY_STATUS_INVALID"
            )
        if not isinstance(self.qualification_status, QualificationStatus):
            raise CandidateLocalNavigationContractError(
                "QUALIFICATION_STATUS_INVALID"
            )
        if self.local_target is not None and not isinstance(
            self.local_target, NavigationPoint
        ):
            raise CandidateLocalNavigationContractError("LOCAL_TARGET_INVALID")
        if not isinstance(self.local_route_segment, tuple) or any(
            not isinstance(item, LocalRouteElement)
            for item in self.local_route_segment
        ):
            raise CandidateLocalNavigationContractError(
                "LOCAL_ROUTE_SEGMENT_INVALID"
            )
        if not isinstance(self.model_target_points_world, tuple) or any(
            not isinstance(item, NavigationPoint)
            for item in self.model_target_points_world
        ):
            raise CandidateLocalNavigationContractError(
                "MODEL_TARGET_POINTS_INVALID"
            )
        local_horizon = _require_finite(self.local_horizon_m, "LOCAL_HORIZON")
        object.__setattr__(self, "local_horizon_m", local_horizon)
        if self.route_cost_after_reconnect is not None:
            route_cost = _require_finite(
                self.route_cost_after_reconnect, "ROUTE_COST_AFTER_RECONNECT"
            )
            if route_cost < 0.0:
                raise CandidateLocalNavigationContractError(
                    "ROUTE_COST_AFTER_RECONNECT_INVALID"
                )
            object.__setattr__(self, "route_cost_after_reconnect", route_cost)
        if (
            type(self.qualification_planner_call_count) is not int
            or self.qualification_planner_call_count < 0
        ):
            raise CandidateLocalNavigationContractError(
                "QUALIFICATION_PLANNER_CALL_COUNT_INVALID"
            )
        expected_branch_digest = canonical_navigation_digest(
            {
                "local_branch_identity": self.local_branch_identity,
                "changes_nominal_route": self.changes_nominal_route,
                "local_target": self.local_target,
                "local_route_segment": self.local_route_segment,
                "model_target_points_world": self.model_target_points_world,
                "local_horizon_m": self.local_horizon_m,
            }
        )
        if self.branch_digest != expected_branch_digest:
            raise CandidateLocalNavigationContractError(
                "BRANCH_DIGEST_CONTENT_MISMATCH"
            )
        expected_target_digest = canonical_navigation_digest(
            {"world_target_points": self.model_target_points_world}
        )
        if self.target_digest != expected_target_digest:
            raise CandidateLocalNavigationContractError(
                "TARGET_DIGEST_CONTENT_MISMATCH"
            )
        expected_obligation_digest = canonical_navigation_digest(
            {
                "candidate_id": self.candidate_id,
                "interpretation_id": self.interpretation_id,
                "source_observation_id": self.source_observation_id,
                "global_destination_identity": self.global_destination_identity,
                "nominal_global_route_identity": (
                    self.nominal_global_route_identity
                ),
                "mission_context_digest": self.mission_context_digest,
                "anchor_identity": self.anchor_identity,
                "maneuver_family": self.maneuver_family,
                "maneuver_direction": self.maneuver_direction,
                "local_branch_identity": self.local_branch_identity,
                "changes_nominal_route": self.changes_nominal_route,
                "branch_digest": self.branch_digest,
                "local_target": self.local_target,
                "local_route_segment": self.local_route_segment,
                "model_target_points_world": self.model_target_points_world,
                "target_digest": self.target_digest,
                "model_target_projection_digest": (
                    self.model_target_projection_digest
                ),
                "local_horizon_m": self.local_horizon_m,
                "global_goal_recoverability": self.global_goal_recoverability,
                "route_cost_after_reconnect": self.route_cost_after_reconnect,
                "qualification_status": self.qualification_status,
                "reason_code": self.reason_code,
                "cause_of_difference": self.cause_of_difference,
                "planner_provenance": self.planner_provenance,
                "qualification_planner_call_count": (
                    self.qualification_planner_call_count
                ),
            }
        )
        if self.obligation_digest != expected_obligation_digest:
            raise CandidateLocalNavigationContractError(
                "OBLIGATION_DIGEST_CONTENT_MISMATCH"
            )
        if self.qualification_status is QualificationStatus.QUALIFIED:
            if self.global_goal_recoverability is not RecoverabilityStatus.RECOVERABLE_TO_GLOBAL_GOAL:
                raise CandidateLocalNavigationContractError(
                    "QUALIFIED_RECOVERABILITY_MISMATCH"
                )
            if self.local_target is None or len(self.local_route_segment) < 2:
                raise CandidateLocalNavigationContractError(
                    "QUALIFIED_LOCAL_REPRESENTATION_INVALID"
                )
            if len(self.model_target_points_world) != 2:
                raise CandidateLocalNavigationContractError(
                    "QUALIFIED_TARGET_POINT_COUNT_INVALID"
                )
            if self.qualification_planner_call_count != 1:
                raise CandidateLocalNavigationContractError(
                    "QUALIFIED_PLANNER_CALL_COUNT_INVALID"
                )
        elif self.qualification_planner_call_count not in (0, 1):
            raise CandidateLocalNavigationContractError(
                "QUALIFICATION_PLANNER_CALL_COUNT_INVALID"
            )


def _branch_digest(branch: SceneGroundedLocalBranch) -> str:
    return canonical_navigation_digest(
        {
            "local_branch_identity": branch.local_branch_identity,
            "changes_nominal_route": branch.changes_nominal_route,
            "local_target": branch.local_target,
            "local_route_segment": branch.local_route_segment,
            "model_target_points_world": branch.model_target_points_world,
            "local_horizon_m": branch.local_horizon_m,
        }
    )


def _target_digest(points: tuple[NavigationPoint, ...]) -> str:
    return canonical_navigation_digest({"world_target_points": points})


def _branch_geometry_consistent(branch: SceneGroundedLocalBranch) -> bool:
    if not branch.local_route_segment:
        return False
    segment_points = tuple(item.point for item in branch.local_route_segment)
    if branch.local_target != segment_points[-1]:
        return False
    if any(point not in segment_points for point in branch.model_target_points_world):
        return False
    polyline_length = sum(
        math.hypot(
            current.x_m - previous.x_m,
            current.y_m - previous.y_m,
        )
        for previous, current in zip(segment_points, segment_points[1:])
    )
    return polyline_length <= branch.local_horizon_m + 1e-9


class CandidateLocalNavigationMapper:
    """Packages supplied branches and qualifies them against one mission."""

    def __init__(
        self,
        mission_context: MissionNavigationContext,
        recoverability_evaluator: GlobalRecoverabilityEvaluator,
        *,
        maximum_local_horizon_m: float = DEFAULT_MAXIMUM_LOCAL_HORIZON_M,
    ) -> None:
        if not isinstance(mission_context, MissionNavigationContext):
            raise CandidateLocalNavigationContractError("MISSION_CONTEXT_INVALID")
        if not isinstance(recoverability_evaluator, GlobalRecoverabilityEvaluator):
            raise CandidateLocalNavigationContractError(
                "RECOVERABILITY_EVALUATOR_INVALID"
            )
        maximum = _require_finite(
            maximum_local_horizon_m, "MAXIMUM_LOCAL_HORIZON"
        )
        if maximum <= 0.0:
            raise CandidateLocalNavigationContractError(
                "MAXIMUM_LOCAL_HORIZON_INVALID"
            )
        self._mission = mission_context
        self._evaluator = recoverability_evaluator
        self._maximum_local_horizon_m = maximum

    @property
    def mission_context(self) -> MissionNavigationContext:
        return self._mission

    def map(
        self,
        interpretation: ResolvedPassengerInterpretation,
        branch: SceneGroundedLocalBranch,
    ) -> CandidateLocalNavigationObligation:
        if interpretation.local_branch_identity != branch.local_branch_identity:
            return self._build(
                interpretation,
                branch,
                RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY,
                QualificationStatus.UNKNOWN,
                "INTERPRETATION_BRANCH_IDENTITY_MISMATCH",
                None,
                None,
                0,
            )
        if not branch.availability:
            return self._build(
                interpretation,
                branch,
                RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY,
                QualificationStatus.NOT_AVAILABLE,
                "BRANCH_NOT_AVAILABLE",
                None,
                None,
                0,
            )
        representation_valid = (
            branch.local_target is not None
            and len(branch.local_route_segment) >= 2
            and len(branch.model_target_points_world) == 2
            and branch.local_horizon_m > 0.0
            and branch.local_horizon_m <= self._maximum_local_horizon_m
            and branch.branch_exit_planner_endpoint is not None
            and _branch_geometry_consistent(branch)
        )
        if not representation_valid:
            return self._build(
                interpretation,
                branch,
                RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY,
                QualificationStatus.UNKNOWN,
                "LOCAL_NAVIGATION_REPRESENTATION_UNAVAILABLE",
                None,
                None,
                0,
            )
        assessment = self._evaluator.assess(
            branch.branch_exit_planner_endpoint, self._mission
        )
        if assessment.status is RecoverabilityStatus.RECOVERABLE_TO_GLOBAL_GOAL:
            qualification = QualificationStatus.QUALIFIED
            reason = "QUALIFIED_SAME_DESTINATION_RECOVERABLE"
        elif assessment.status is RecoverabilityStatus.UNRECOVERABLE_TO_GLOBAL_GOAL:
            qualification = QualificationStatus.UNRECOVERABLE
            reason = "UNRECOVERABLE_TO_GLOBAL_GOAL"
        else:
            qualification = QualificationStatus.UNKNOWN
            # Carry the evaluator's own reason instead of flattening every
            # unknown to one string: a raising planner call, a malformed result
            # and a missing capability are different facts about the run.
            reason = assessment.reason_code
        return self._build(
            interpretation,
            branch,
            assessment.status,
            qualification,
            reason,
            assessment.route_cost_after_reconnect,
            assessment.planner_provenance,
            assessment.planner_call_count,
        )

    def _build(
        self,
        interpretation: ResolvedPassengerInterpretation,
        branch: SceneGroundedLocalBranch,
        recoverability: RecoverabilityStatus,
        qualification: QualificationStatus,
        reason_code: str,
        route_cost: float | None,
        planner_provenance: str | None,
        planner_call_count: int,
    ) -> CandidateLocalNavigationObligation:
        branch_digest = _branch_digest(branch)
        target_digest = _target_digest(branch.model_target_points_world)
        payload = {
            "candidate_id": interpretation.candidate_id,
            "interpretation_id": interpretation.interpretation_id,
            "source_observation_id": interpretation.source_observation_id,
            "global_destination_identity": self._mission.global_destination_identity,
            "nominal_global_route_identity": self._mission.nominal_global_route_identity,
            "mission_context_digest": self._mission.mission_context_digest,
            "anchor_identity": interpretation.anchor_identity,
            "maneuver_family": interpretation.maneuver_family,
            "maneuver_direction": interpretation.maneuver_direction,
            "local_branch_identity": branch.local_branch_identity,
            "changes_nominal_route": branch.changes_nominal_route,
            "branch_digest": branch_digest,
            "local_target": branch.local_target,
            "local_route_segment": branch.local_route_segment,
            "model_target_points_world": branch.model_target_points_world,
            "target_digest": target_digest,
            "model_target_projection_digest": None,
            "local_horizon_m": branch.local_horizon_m,
            "global_goal_recoverability": recoverability,
            "route_cost_after_reconnect": route_cost,
            "qualification_status": qualification,
            "reason_code": reason_code,
            "cause_of_difference": CAUSE_OF_DIFFERENCE,
            "planner_provenance": planner_provenance,
            "qualification_planner_call_count": planner_call_count,
        }
        digest = canonical_navigation_digest(payload)
        return CandidateLocalNavigationObligation(
            obligation_identity=digest,
            obligation_digest=digest,
            candidate_id=interpretation.candidate_id,
            interpretation_id=interpretation.interpretation_id,
            source_observation_id=interpretation.source_observation_id,
            global_destination_identity=self._mission.global_destination_identity,
            nominal_global_route_identity=self._mission.nominal_global_route_identity,
            mission_context_digest=self._mission.mission_context_digest,
            anchor_identity=interpretation.anchor_identity,
            maneuver_family=interpretation.maneuver_family,
            maneuver_direction=interpretation.maneuver_direction,
            local_branch_identity=branch.local_branch_identity,
            changes_nominal_route=branch.changes_nominal_route,
            branch_digest=branch_digest,
            local_target=branch.local_target,
            local_route_segment=branch.local_route_segment,
            model_target_points_world=branch.model_target_points_world,
            target_digest=target_digest,
            model_target_projection_digest=None,
            local_horizon_m=branch.local_horizon_m,
            global_goal_recoverability=recoverability,
            route_cost_after_reconnect=route_cost,
            qualification_status=qualification,
            reason_code=reason_code,
            cause_of_difference=CAUSE_OF_DIFFERENCE,
            planner_provenance=planner_provenance,
            qualification_planner_call_count=planner_call_count,
        )


@dataclass(frozen=True)
class CandidateForwardNavigationBinding:
    candidate_id: str
    interpretation_id: str
    planning_observation_id: str
    planning_frame_id: int
    obligation_identity: str
    obligation_digest: str
    branch_digest: str
    target_digest: str
    global_destination_identity: str
    mission_context_digest: str
    target_points_ego_local_xy_m: tuple[tuple[float, float], tuple[float, float]]
    projection_digest: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.candidate_id, "CANDIDATE_ID"),
            (self.interpretation_id, "INTERPRETATION_ID"),
            (self.planning_observation_id, "PLANNING_OBSERVATION_ID"),
            (self.global_destination_identity, "GLOBAL_DESTINATION_IDENTITY"),
        ):
            _require_identifier(value, name)
        if type(self.planning_frame_id) is not int or self.planning_frame_id < 0:
            raise CandidateLocalNavigationContractError("PLANNING_FRAME_ID_INVALID")
        for value, name in (
            (self.obligation_identity, "OBLIGATION_IDENTITY"),
            (self.obligation_digest, "OBLIGATION_DIGEST"),
            (self.branch_digest, "BRANCH_DIGEST"),
            (self.target_digest, "TARGET_DIGEST"),
            (self.mission_context_digest, "MISSION_CONTEXT_DIGEST"),
            (self.projection_digest, "PROJECTION_DIGEST"),
        ):
            _require_digest(value, name)
        if self.obligation_identity != self.obligation_digest:
            raise CandidateLocalNavigationContractError(
                "OBLIGATION_IDENTITY_DIGEST_MISMATCH"
            )
        if len(self.target_points_ego_local_xy_m) != 2:
            raise CandidateLocalNavigationContractError(
                "TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH"
            )
        for point in self.target_points_ego_local_xy_m:
            if not isinstance(point, tuple) or len(point) != 2:
                raise CandidateLocalNavigationContractError(
                    "TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH"
                )
        normalized = canonical_navigation_projection(
            self.target_points_ego_local_xy_m
        )
        object.__setattr__(self, "target_points_ego_local_xy_m", normalized)
        # The digest must be a function of the consumed point content, so it is
        # recomputed here rather than trusted from the constructor argument.
        if self.projection_digest != navigation_projection_digest(normalized):
            raise CandidateLocalNavigationContractError(
                "PROJECTION_DIGEST_CONTENT_MISMATCH"
            )


def _project_world_point(
    point: NavigationPoint, pose: EgoPose2D | EgoPoseWorldXY
) -> tuple[float, float]:
    delta_x = point.x_m - pose.x_m
    delta_y = point.y_m - pose.y_m
    cosine = math.cos(pose.yaw_rad)
    sine = math.sin(pose.yaw_rad)
    # Exact rigid inverse: R(yaw)^T * (world_point - ego_translation).
    return (
        cosine * delta_x + sine * delta_y,
        -sine * delta_x + cosine * delta_y,
    )


def materialize_forward_binding(
    obligation: CandidateLocalNavigationObligation,
    ego_pose: EgoPose2D | EgoPoseWorldXY,
    planning_observation_id: str,
    planning_frame_id: int,
) -> CandidateForwardNavigationBinding:
    """Project exactly two world points for one latest-observation forward."""

    if obligation.qualification_status is not QualificationStatus.QUALIFIED:
        raise CandidateLocalNavigationContractError("OBLIGATION_NOT_QUALIFIED")
    if len(obligation.model_target_points_world) != 2:
        raise CandidateLocalNavigationContractError(
            "TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH"
        )
    if not isinstance(ego_pose, (EgoPose2D, EgoPoseWorldXY)):
        raise CandidateLocalNavigationContractError("EGO_POSE_INVALID")
    _require_identifier(planning_observation_id, "PLANNING_OBSERVATION_ID")
    if type(planning_frame_id) is not int or planning_frame_id < 0:
        raise CandidateLocalNavigationContractError("PLANNING_FRAME_ID_INVALID")
    projected = canonical_navigation_projection(
        tuple(
            _project_world_point(point, ego_pose)
            for point in obligation.model_target_points_world
        )
    )
    # Digest the projection that will actually be consumed, computed after
    # materialization and never from an interpretation string.
    projection_digest = navigation_projection_digest(projected)
    return CandidateForwardNavigationBinding(
        candidate_id=obligation.candidate_id,
        interpretation_id=obligation.interpretation_id,
        planning_observation_id=planning_observation_id,
        planning_frame_id=planning_frame_id,
        obligation_identity=obligation.obligation_identity,
        obligation_digest=obligation.obligation_digest,
        branch_digest=obligation.branch_digest,
        target_digest=obligation.target_digest,
        global_destination_identity=obligation.global_destination_identity,
        mission_context_digest=obligation.mission_context_digest,
        target_points_ego_local_xy_m=projected,  # type: ignore[arg-type]
        projection_digest=projection_digest,
    )


@dataclass(frozen=True)
class CandidateLocalNavigationRuntimeCandidate(RuntimeCandidate):
    """Capture-visible immutable subtype passed through the real provider.

    ``candidate_id`` keeps carrying the per-forward repetition identity its
    producer assigns, so capture records keep their existing form.  The semantic
    candidate identity this forward is for is carried explicitly instead of being
    inferred from that field, because the two are different naming spaces.
    """

    navigation_binding: CandidateForwardNavigationBinding
    target_digest: str
    branch_digest: str
    obligation_digest: str
    semantic_candidate_id: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.navigation_binding, CandidateForwardNavigationBinding):
            raise CandidateLocalNavigationContractError("NAVIGATION_BINDING_INVALID")
        _require_identifier(self.semantic_candidate_id, "SEMANTIC_CANDIDATE_ID")
        if self.semantic_candidate_id != self.navigation_binding.candidate_id:
            raise CandidateLocalNavigationContractError("CANDIDATE_BINDING_ID_MISMATCH")
        for value, name in (
            (self.target_digest, "TARGET_DIGEST"),
            (self.branch_digest, "BRANCH_DIGEST"),
            (self.obligation_digest, "OBLIGATION_DIGEST"),
        ):
            _require_digest(value, name)
        if (
            self.target_digest != self.navigation_binding.target_digest
            or self.branch_digest != self.navigation_binding.branch_digest
            or self.obligation_digest != self.navigation_binding.obligation_digest
        ):
            raise CandidateLocalNavigationContractError(
                "RUNTIME_CANDIDATE_NAVIGATION_DIGEST_MISMATCH"
            )


def enrich_runtime_candidate(
    runtime_candidate: RuntimeCandidate,
    binding: CandidateForwardNavigationBinding,
    *,
    semantic_candidate_id: str | None = None,
) -> CandidateLocalNavigationRuntimeCandidate:
    """Attach one installed binding to the runtime candidate it belongs to.

    ``semantic_candidate_id`` states which semantic candidate this forward is
    for.  It is a separate naming space from ``RuntimeCandidate.candidate_id``,
    which producers may legitimately use for a per-forward repetition identity,
    so the caller states it rather than the bridge guessing a convention.  When
    omitted it defaults to ``candidate_id``, which is what callers whose two
    identities coincide were relying on.
    """

    if not isinstance(runtime_candidate, RuntimeCandidate):
        raise CandidateLocalNavigationContractError("RUNTIME_CANDIDATE_INVALID")
    if not isinstance(binding, CandidateForwardNavigationBinding):
        raise CandidateLocalNavigationContractError("NAVIGATION_BINDING_INVALID")
    stated_candidate_id = (
        runtime_candidate.candidate_id
        if semantic_candidate_id is None
        else str(semantic_candidate_id)
    )
    if stated_candidate_id != binding.candidate_id:
        raise CandidateLocalNavigationContractError("CANDIDATE_BINDING_ID_MISMATCH")
    if runtime_candidate.interpretation_id != binding.interpretation_id:
        raise CandidateLocalNavigationContractError(
            "INTERPRETATION_BINDING_ID_MISMATCH"
        )
    if runtime_candidate.source_observation_id != binding.planning_observation_id:
        raise CandidateLocalNavigationContractError(
            "PLANNING_OBSERVATION_BINDING_MISMATCH"
        )
    if runtime_candidate.source_frame_id != binding.planning_frame_id:
        raise CandidateLocalNavigationContractError("PLANNING_FRAME_BINDING_MISMATCH")
    base_values = {
        field.name: getattr(runtime_candidate, field.name)
        for field in fields(RuntimeCandidate)
    }
    return CandidateLocalNavigationRuntimeCandidate(
        **base_values,
        navigation_binding=binding,
        target_digest=binding.target_digest,
        branch_digest=binding.branch_digest,
        obligation_digest=binding.obligation_digest,
        semantic_candidate_id=stated_candidate_id,
    )


@dataclass(frozen=True)
class FreshLocalReplanRequest:
    request_digest: str
    candidate_id: str
    selected_obligation_identity: str
    selected_obligation_digest: str
    latest_observation_id: str
    latest_frame_id: int
    old_candidate_bundle_invalidated: bool
    uses_selected_local_navigation_obligation: bool
    global_planner_call_count: int
    global_route_mutation: bool
    forward_navigation_binding: CandidateForwardNavigationBinding
    navigation_projection_digest: str

    def __post_init__(self) -> None:
        _require_digest(self.request_digest, "FRESH_LOCAL_REPLAN_REQUEST_DIGEST")
        # The request owns the exact projection object the forward must consume.
        if not isinstance(
            self.forward_navigation_binding, CandidateForwardNavigationBinding
        ):
            raise CandidateLocalNavigationContractError(
                "FRESH_LOCAL_REPLAN_FORWARD_BINDING_INVALID"
            )
        binding = self.forward_navigation_binding
        _require_digest(
            self.navigation_projection_digest, "NAVIGATION_PROJECTION_DIGEST"
        )
        if self.navigation_projection_digest != binding.projection_digest:
            raise CandidateLocalNavigationContractError(
                "FRESH_LOCAL_REPLAN_PROJECTION_DIGEST_MISMATCH"
            )
        if (
            binding.candidate_id != self.candidate_id
            or binding.obligation_digest != self.selected_obligation_digest
            or binding.obligation_identity != self.selected_obligation_identity
        ):
            raise CandidateLocalNavigationContractError(
                "FRESH_LOCAL_REPLAN_FORWARD_BINDING_IDENTITY_MISMATCH"
            )
        if (
            binding.planning_observation_id != self.latest_observation_id
            or binding.planning_frame_id != self.latest_frame_id
        ):
            raise CandidateLocalNavigationContractError(
                "FRESH_LOCAL_REPLAN_FORWARD_BINDING_SOURCE_MISMATCH"
            )
        _require_digest(
            self.selected_obligation_identity, "OBLIGATION_IDENTITY"
        )
        _require_digest(self.selected_obligation_digest, "OBLIGATION_DIGEST")
        _require_identifier(self.candidate_id, "CANDIDATE_ID")
        _require_identifier(self.latest_observation_id, "LATEST_OBSERVATION_ID")
        if self.selected_obligation_identity != self.selected_obligation_digest:
            raise CandidateLocalNavigationContractError(
                "OBLIGATION_IDENTITY_DIGEST_MISMATCH"
            )
        if type(self.latest_frame_id) is not int or self.latest_frame_id < 0:
            raise CandidateLocalNavigationContractError("LATEST_FRAME_ID_INVALID")
        if self.old_candidate_bundle_invalidated is not True:
            raise CandidateLocalNavigationContractError(
                "OLD_AMBIGUITY_BUNDLE_NOT_INVALIDATED"
            )
        if self.uses_selected_local_navigation_obligation is not True:
            raise CandidateLocalNavigationContractError(
                "SELECTED_LOCAL_NAVIGATION_OBLIGATION_NOT_USED"
            )
        if type(self.global_planner_call_count) is not int or self.global_planner_call_count != 0:
            raise CandidateLocalNavigationContractError(
                "LOCAL_REPLAN_GLOBAL_PLANNER_CALL_FORBIDDEN"
            )
        if self.global_route_mutation is not False:
            raise CandidateLocalNavigationContractError(
                "LOCAL_REPLAN_GLOBAL_ROUTE_MUTATION_FORBIDDEN"
            )
        expected = _fresh_local_replan_digest(
            candidate_id=self.candidate_id,
            selected_obligation_digest=self.selected_obligation_digest,
            latest_observation_id=self.latest_observation_id,
            latest_frame_id=self.latest_frame_id,
            navigation_projection_digest=self.navigation_projection_digest,
        )
        if self.request_digest != expected:
            raise CandidateLocalNavigationContractError(
                "FRESH_LOCAL_REPLAN_REQUEST_DIGEST_CONTENT_MISMATCH"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "request_digest": self.request_digest,
            "candidate_id": self.candidate_id,
            "selected_obligation_identity": self.selected_obligation_identity,
            "selected_obligation_digest": self.selected_obligation_digest,
            "latest_observation_id": self.latest_observation_id,
            "latest_frame_id": self.latest_frame_id,
            "old_candidate_bundle_invalidated": (
                self.old_candidate_bundle_invalidated
            ),
            "uses_selected_local_navigation_obligation": (
                self.uses_selected_local_navigation_obligation
            ),
            "global_planner_call_count": self.global_planner_call_count,
            "global_route_mutation": self.global_route_mutation,
            "navigation_projection_digest": self.navigation_projection_digest,
            "target_points_ego_local_xy_m": [
                list(row)
                for row in self.forward_navigation_binding.target_points_ego_local_xy_m
            ],
        }


def _fresh_local_replan_digest(
    *,
    candidate_id: str,
    selected_obligation_digest: str,
    latest_observation_id: str,
    latest_frame_id: int,
    navigation_projection_digest: str,
) -> str:
    return canonical_navigation_digest(
        {
            "candidate_id": candidate_id,
            "selected_obligation_digest": selected_obligation_digest,
            "latest_observation_id": latest_observation_id,
            "latest_frame_id": latest_frame_id,
            "old_candidate_bundle_invalidated": True,
            "global_planner_call_count": 0,
            "global_route_mutation": False,
            "navigation_projection_digest": navigation_projection_digest,
        }
    )


def build_fresh_local_replan_request(
    obligation: CandidateLocalNavigationObligation,
    forward_navigation_binding: CandidateForwardNavigationBinding,
    *,
    old_candidate_bundle_invalidated: bool,
) -> FreshLocalReplanRequest:
    """Wrap the already-materialized projection that the forward must consume.

    The observation/frame identity is taken from the binding rather than from
    separate arguments so that the request cannot describe a different forward
    than the one it carries.
    """

    if obligation.qualification_status is not QualificationStatus.QUALIFIED:
        raise CandidateLocalNavigationContractError("OBLIGATION_NOT_QUALIFIED")
    if old_candidate_bundle_invalidated is not True:
        raise CandidateLocalNavigationContractError(
            "OLD_AMBIGUITY_BUNDLE_NOT_INVALIDATED"
        )
    if not isinstance(forward_navigation_binding, CandidateForwardNavigationBinding):
        raise CandidateLocalNavigationContractError(
            "FRESH_LOCAL_REPLAN_FORWARD_BINDING_INVALID"
        )
    if (
        forward_navigation_binding.obligation_digest != obligation.obligation_digest
        or forward_navigation_binding.candidate_id != obligation.candidate_id
        or forward_navigation_binding.interpretation_id
        != obligation.interpretation_id
    ):
        raise CandidateLocalNavigationContractError(
            "FRESH_LOCAL_REPLAN_FORWARD_BINDING_IDENTITY_MISMATCH"
        )
    latest_observation_id = forward_navigation_binding.planning_observation_id
    latest_frame_id = forward_navigation_binding.planning_frame_id
    projection_digest = forward_navigation_binding.projection_digest
    digest = _fresh_local_replan_digest(
        candidate_id=obligation.candidate_id,
        selected_obligation_digest=obligation.obligation_digest,
        latest_observation_id=latest_observation_id,
        latest_frame_id=latest_frame_id,
        navigation_projection_digest=projection_digest,
    )
    return FreshLocalReplanRequest(
        request_digest=digest,
        candidate_id=obligation.candidate_id,
        selected_obligation_identity=obligation.obligation_identity,
        selected_obligation_digest=obligation.obligation_digest,
        latest_observation_id=latest_observation_id,
        latest_frame_id=latest_frame_id,
        old_candidate_bundle_invalidated=True,
        uses_selected_local_navigation_obligation=True,
        global_planner_call_count=0,
        global_route_mutation=False,
        forward_navigation_binding=forward_navigation_binding,
        navigation_projection_digest=projection_digest,
    )


@dataclass(frozen=True)
class GlobalRouteReconnectionRequest:
    request_digest: str
    selected_obligation_identity: str
    selected_obligation_digest: str
    latest_ego_planner_endpoint: object
    latest_ego_endpoint_digest: str
    global_destination_identity: str
    global_destination_planner_endpoint: object
    global_destination_endpoint_digest: str
    mission_context_digest: str
    old_nominal_global_route_identity: str
    old_route_invalidated: bool

    def __post_init__(self) -> None:
        for value, name in (
            (self.request_digest, "RECONNECTION_REQUEST_DIGEST"),
            (self.selected_obligation_identity, "OBLIGATION_IDENTITY"),
            (self.selected_obligation_digest, "OBLIGATION_DIGEST"),
            (self.latest_ego_endpoint_digest, "LATEST_EGO_ENDPOINT_DIGEST"),
            (
                self.global_destination_endpoint_digest,
                "GLOBAL_DESTINATION_ENDPOINT_DIGEST",
            ),
            (self.mission_context_digest, "MISSION_CONTEXT_DIGEST"),
        ):
            _require_digest(value, name)
        _require_identifier(
            self.global_destination_identity, "GLOBAL_DESTINATION_IDENTITY"
        )
        _require_identifier(
            self.old_nominal_global_route_identity,
            "OLD_NOMINAL_GLOBAL_ROUTE_IDENTITY",
        )
        if self.selected_obligation_identity != self.selected_obligation_digest:
            raise CandidateLocalNavigationContractError(
                "OBLIGATION_IDENTITY_DIGEST_MISMATCH"
            )
        if self.old_route_invalidated is not True:
            raise CandidateLocalNavigationContractError(
                "OLD_NOMINAL_ROUTE_NOT_INVALIDATED"
            )
        if (
            _planner_endpoint_digest(self.latest_ego_planner_endpoint)
            != self.latest_ego_endpoint_digest
            or _planner_endpoint_digest(self.global_destination_planner_endpoint)
            != self.global_destination_endpoint_digest
        ):
            raise CandidateLocalNavigationContractError(
                "RECONNECTION_ENDPOINT_DIGEST_MISMATCH"
            )
        expected = canonical_navigation_digest(
            {
                "selected_obligation_digest": self.selected_obligation_digest,
                "latest_ego_endpoint_digest": self.latest_ego_endpoint_digest,
                "global_destination_identity": self.global_destination_identity,
                "global_destination_endpoint_digest": (
                    self.global_destination_endpoint_digest
                ),
                "mission_context_digest": self.mission_context_digest,
                "old_nominal_global_route_identity": (
                    self.old_nominal_global_route_identity
                ),
                "event": "LOCAL_BRANCH_COMMITTED_AND_NOMINAL_ROUTE_CHANGED",
            }
        )
        if self.request_digest != expected:
            raise CandidateLocalNavigationContractError(
                "RECONNECTION_REQUEST_DIGEST_CONTENT_MISMATCH"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "request_digest": self.request_digest,
            "selected_obligation_identity": self.selected_obligation_identity,
            "selected_obligation_digest": self.selected_obligation_digest,
            "latest_ego_planner_endpoint_present": (
                self.latest_ego_planner_endpoint is not None
            ),
            "latest_ego_endpoint_digest": self.latest_ego_endpoint_digest,
            "global_destination_identity": self.global_destination_identity,
            "global_destination_planner_endpoint_present": (
                self.global_destination_planner_endpoint is not None
            ),
            "global_destination_endpoint_digest": (
                self.global_destination_endpoint_digest
            ),
            "mission_context_digest": self.mission_context_digest,
            "old_nominal_global_route_identity": (
                self.old_nominal_global_route_identity
            ),
            "old_route_invalidated": self.old_route_invalidated,
        }


@dataclass(frozen=True)
class GlobalRouteReconnectionReceipt:
    request_digest: str
    state: GlobalRouteState
    global_destination_identity: str
    mission_context_digest: str
    old_route_invalidated: bool
    planner_call_count: int
    route_owner_acknowledged: bool
    fresh_route_length: int
    failure_reason: str | None

    def __post_init__(self) -> None:
        _require_digest(self.request_digest, "RECONNECTION_REQUEST_DIGEST")
        _require_digest(self.mission_context_digest, "MISSION_CONTEXT_DIGEST")
        _require_identifier(
            self.global_destination_identity, "GLOBAL_DESTINATION_IDENTITY"
        )
        if not isinstance(self.state, GlobalRouteState):
            raise CandidateLocalNavigationContractError(
                "GLOBAL_ROUTE_STATE_INVALID"
            )
        if type(self.old_route_invalidated) is not bool:
            raise CandidateLocalNavigationContractError(
                "OLD_ROUTE_INVALIDATED_FLAG_INVALID"
            )
        if type(self.route_owner_acknowledged) is not bool:
            raise CandidateLocalNavigationContractError(
                "ROUTE_OWNER_ACKNOWLEDGED_FLAG_INVALID"
            )
        if type(self.planner_call_count) is not int or self.planner_call_count < 0:
            raise CandidateLocalNavigationContractError(
                "RECONNECTION_PLANNER_CALL_COUNT_INVALID"
            )
        if type(self.fresh_route_length) is not int or self.fresh_route_length < 0:
            raise CandidateLocalNavigationContractError(
                "FRESH_ROUTE_LENGTH_INVALID"
            )
        if self.failure_reason is not None and not isinstance(
            self.failure_reason, str
        ):
            raise CandidateLocalNavigationContractError(
                "RECONNECTION_FAILURE_REASON_INVALID"
            )
        if self.old_route_invalidated is not True:
            raise CandidateLocalNavigationContractError(
                "RECONNECTION_OLD_ROUTE_NOT_INVALIDATED"
            )
        if self.state is GlobalRouteState.ROUTE_RECONNECTED:
            if not (
                self.planner_call_count == 1
                and self.route_owner_acknowledged is True
                and self.fresh_route_length > 0
                and self.failure_reason is None
            ):
                raise CandidateLocalNavigationContractError(
                    "RECONNECTED_RECEIPT_COHERENCE_INVALID"
                )
        elif self.state is GlobalRouteState.ROUTE_RECONNECTION_FAILED:
            # A failure after tracing made exactly one call.  A failure caused
            # by an absent/malformed existing capability made none, because no
            # planner was ever invoked and none was created to replace it.
            expected_calls = (
                0
                if self.failure_reason
                in (
                    PlannerCapabilityStatus.PLANNER_CAPABILITY_UNAVAILABLE.value,
                    PlannerCapabilityStatus.PLANNER_CAPABILITY_MALFORMED.value,
                )
                else 1
            )
            if not (
                self.planner_call_count == expected_calls
                and self.route_owner_acknowledged is False
                and self.fresh_route_length == 0
                and isinstance(self.failure_reason, str)
                and bool(self.failure_reason)
            ):
                raise CandidateLocalNavigationContractError(
                    "FAILED_RECONNECTION_RECEIPT_COHERENCE_INVALID"
                )
        else:
            raise CandidateLocalNavigationContractError(
                "TERMINAL_RECONNECTION_RECEIPT_STATE_INVALID"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "request_digest": self.request_digest,
            "state": self.state.value,
            "global_destination_identity": self.global_destination_identity,
            "mission_context_digest": self.mission_context_digest,
            "old_route_invalidated": self.old_route_invalidated,
            "planner_call_count": self.planner_call_count,
            "route_owner_acknowledged": self.route_owner_acknowledged,
            "fresh_route_length": self.fresh_route_length,
            "failure_reason": self.failure_reason,
            "native_live_route_installation_validated": False,
        }


class GlobalRouteReconnectionBridge:
    """One-shot fixture state machine around injected existing-route ports."""

    def __init__(
        self,
        mission_context: MissionNavigationContext,
        recoverability_evaluator: GlobalRecoverabilityEvaluator,
        route_owner: FixtureRouteOwner,
    ) -> None:
        if not isinstance(mission_context, MissionNavigationContext):
            raise CandidateLocalNavigationContractError("MISSION_CONTEXT_INVALID")
        mission_context.assert_endpoint_unchanged()
        # The capability is taken from the qualification evaluator rather than
        # injected separately, so reconnection cannot use a second planner.
        if not isinstance(recoverability_evaluator, GlobalRecoverabilityEvaluator):
            raise CandidateLocalNavigationContractError(
                "RECOVERABILITY_EVALUATOR_INVALID"
            )
        if route_owner is None:
            raise CandidateLocalNavigationContractError("ROUTE_OWNER_MISSING")
        self._mission = mission_context
        self._evaluator = recoverability_evaluator
        self._planner_capability = recoverability_evaluator.planner_capability
        self._existing_planner = self._planner_capability.planner_object
        self._route_owner = route_owner
        self._state = GlobalRouteState.ROUTE_CURRENT
        self._pending_request: GlobalRouteReconnectionRequest | None = None
        self._receipt: GlobalRouteReconnectionReceipt | None = None
        self._planner_call_count = 0
        self._old_route_invalidated = False

    @property
    def state(self) -> GlobalRouteState:
        return self._state

    @property
    def mission(self) -> MissionNavigationContext:
        return self._mission

    @property
    def planner_call_count(self) -> int:
        return self._planner_call_count

    @property
    def recoverability_evaluator(self) -> GlobalRecoverabilityEvaluator:
        return self._evaluator

    @property
    def planner_capability(self) -> ExistingPlannerCapability:
        return self._planner_capability

    @property
    def planner_object(self) -> object:
        return self._planner_capability.planner_object

    @property
    def planner_capability_status(self) -> PlannerCapabilityStatus:
        return self._planner_capability.status

    @property
    def planner_provenance(self) -> str:
        return self._planner_capability.provenance

    @property
    def pending_request(self) -> GlobalRouteReconnectionRequest | None:
        return self._pending_request

    def commit_selected_local_branch(
        self,
        obligation: CandidateLocalNavigationObligation,
        latest_ego_planner_endpoint: object,
        *,
        nominal_route_changed: bool,
    ) -> GlobalRouteReconnectionRequest | None:
        if not isinstance(obligation, CandidateLocalNavigationObligation):
            raise CandidateLocalNavigationContractError(
                "LOCAL_NAVIGATION_OBLIGATION_TYPE_INVALID"
            )
        if obligation.qualification_status is not QualificationStatus.QUALIFIED:
            raise CandidateLocalNavigationContractError("OBLIGATION_NOT_QUALIFIED")
        self._mission.assert_endpoint_unchanged()
        if obligation.global_destination_identity != self._mission.global_destination_identity:
            raise CandidateLocalNavigationContractError(
                "GLOBAL_DESTINATION_IDENTITY_CHANGED"
            )
        if obligation.mission_context_digest != self._mission.mission_context_digest:
            raise CandidateLocalNavigationContractError(
                "MISSION_CONTEXT_IDENTITY_CHANGED"
            )
        if (
            obligation.nominal_global_route_identity
            != self._mission.nominal_global_route_identity
        ):
            raise CandidateLocalNavigationContractError(
                "NOMINAL_GLOBAL_ROUTE_IDENTITY_CHANGED"
            )
        if (
            obligation.planner_provenance is not None
            and obligation.planner_provenance != self._planner_capability.provenance
        ):
            raise CandidateLocalNavigationContractError(
                "QUALIFICATION_PLANNER_CAPABILITY_MISMATCH"
            )
        if latest_ego_planner_endpoint is None:
            raise CandidateLocalNavigationContractError("LATEST_EGO_ENDPOINT_MISSING")
        latest_endpoint_digest = _planner_endpoint_digest(
            latest_ego_planner_endpoint
        )
        if type(nominal_route_changed) is not bool:
            raise CandidateLocalNavigationContractError(
                "NOMINAL_ROUTE_CHANGED_FLAG_INVALID"
            )
        if nominal_route_changed != obligation.changes_nominal_route:
            raise CandidateLocalNavigationContractError(
                "NOMINAL_ROUTE_CHANGED_OBLIGATION_MISMATCH"
            )
        if not nominal_route_changed:
            if self._pending_request is not None:
                raise CandidateLocalNavigationContractError(
                    "RECONNECTION_EVENT_CONFLICT"
                )
            return None
        if self._pending_request is not None:
            if (
                self._pending_request.selected_obligation_digest
                != obligation.obligation_digest
                or self._pending_request.latest_ego_endpoint_digest
                != latest_endpoint_digest
            ):
                raise CandidateLocalNavigationContractError(
                    "RECONNECTION_EVENT_CONFLICT"
                )
            return self._pending_request
        if self._state is not GlobalRouteState.ROUTE_CURRENT:
            raise CandidateLocalNavigationContractError(
                "RECONNECTION_COMMIT_STATE_INVALID"
            )
        try:
            acknowledged = self._route_owner.invalidate_nominal_route(
                self._mission.nominal_global_route_identity
            )
        except Exception:
            acknowledged = False
        if acknowledged is not True:
            self._state = GlobalRouteState.ROUTE_RECONNECTION_FAILED
            raise CandidateLocalNavigationContractError(
                "OLD_NOMINAL_ROUTE_INVALIDATION_FAILED"
            )
        self._old_route_invalidated = True
        request_digest = canonical_navigation_digest(
            {
                "selected_obligation_digest": obligation.obligation_digest,
                "latest_ego_endpoint_digest": latest_endpoint_digest,
                "global_destination_identity": self._mission.global_destination_identity,
                "global_destination_endpoint_digest": (
                    self._mission.global_destination_endpoint_digest
                ),
                "mission_context_digest": self._mission.mission_context_digest,
                "old_nominal_global_route_identity": self._mission.nominal_global_route_identity,
                "event": "LOCAL_BRANCH_COMMITTED_AND_NOMINAL_ROUTE_CHANGED",
            }
        )
        self._pending_request = GlobalRouteReconnectionRequest(
            request_digest=request_digest,
            selected_obligation_identity=obligation.obligation_identity,
            selected_obligation_digest=obligation.obligation_digest,
            latest_ego_planner_endpoint=latest_ego_planner_endpoint,
            latest_ego_endpoint_digest=latest_endpoint_digest,
            global_destination_identity=self._mission.global_destination_identity,
            global_destination_planner_endpoint=self._mission.global_destination_planner_endpoint,
            global_destination_endpoint_digest=(
                self._mission.global_destination_endpoint_digest
            ),
            mission_context_digest=self._mission.mission_context_digest,
            old_nominal_global_route_identity=self._mission.nominal_global_route_identity,
            old_route_invalidated=True,
        )
        self._state = GlobalRouteState.ROUTE_STALE_RECONNECTION_REQUIRED
        return self._pending_request

    def reconnect(
        self, request: GlobalRouteReconnectionRequest | None = None
    ) -> GlobalRouteReconnectionReceipt:
        if self._receipt is not None:
            if request is not None and request != self._pending_request:
                raise CandidateLocalNavigationContractError(
                    "RECONNECTION_REQUEST_MISMATCH"
                )
            return self._receipt
        selected = request or self._pending_request
        if selected is None:
            raise CandidateLocalNavigationContractError("RECONNECTION_REQUEST_MISSING")
        if (
            self._pending_request is None
            or selected != self._pending_request
        ):
            raise CandidateLocalNavigationContractError(
                "RECONNECTION_REQUEST_MISMATCH"
            )
        if self._state is not GlobalRouteState.ROUTE_STALE_RECONNECTION_REQUIRED:
            raise CandidateLocalNavigationContractError(
                "RECONNECTION_STATE_INVALID"
            )
        self._mission.assert_endpoint_unchanged()
        if (
            selected.mission_context_digest != self._mission.mission_context_digest
            or selected.global_destination_endpoint_digest
            != self._mission.global_destination_endpoint_digest
            or _planner_endpoint_digest(selected.latest_ego_planner_endpoint)
            != selected.latest_ego_endpoint_digest
        ):
            raise CandidateLocalNavigationContractError(
                "RECONNECTION_ENDPOINT_IDENTITY_CHANGED"
            )
        if not self._planner_capability.available:
            # No existing planner capability means reconnection cannot proceed;
            # it never silently creates one and never claims unrecoverability.
            return self._fail_receipt(
                selected, self._planner_capability.status.value
            )
        self._planner_call_count += 1
        try:
            # Same capability object, same route semantics as qualification.
            raw = self._planner_capability.trace_route(
                selected.latest_ego_planner_endpoint,
                selected.global_destination_planner_endpoint,
            )
            normalized = _normalize_planner_trace(raw)
        except Exception:
            normalized = _NormalizedPlannerTrace(
                RecoverabilityStatus.UNKNOWN_GLOBAL_RECOVERABILITY,
                None,
                None,
                "PLANNER_TRACE_FAILED",
            )
        if normalized.status is not RecoverabilityStatus.RECOVERABLE_TO_GLOBAL_GOAL:
            return self._fail_receipt(selected, normalized.reason_code)
        route = _route_sequence(normalized.route_object)
        if route is None or len(route) == 0:
            return self._fail_receipt(selected, "FRESH_ROUTE_MISSING")
        try:
            acknowledged = self._route_owner.install_reconnected_route(
                normalized.route_object,
                global_destination_identity=selected.global_destination_identity,
                global_destination_planner_endpoint=selected.global_destination_planner_endpoint,
            )
        except Exception:
            acknowledged = False
        if acknowledged is not True:
            return self._fail_receipt(selected, "ROUTE_OWNER_INSTALLATION_FAILED")
        self._state = GlobalRouteState.ROUTE_RECONNECTED
        self._receipt = GlobalRouteReconnectionReceipt(
            request_digest=selected.request_digest,
            state=self._state,
            global_destination_identity=selected.global_destination_identity,
            mission_context_digest=selected.mission_context_digest,
            old_route_invalidated=self._old_route_invalidated,
            planner_call_count=self._planner_call_count,
            route_owner_acknowledged=True,
            fresh_route_length=len(route),
            failure_reason=None,
        )
        return self._receipt

    def _fail_receipt(
        self, request: GlobalRouteReconnectionRequest, reason: str
    ) -> GlobalRouteReconnectionReceipt:
        self._state = GlobalRouteState.ROUTE_RECONNECTION_FAILED
        self._receipt = GlobalRouteReconnectionReceipt(
            request_digest=request.request_digest,
            state=self._state,
            global_destination_identity=request.global_destination_identity,
            mission_context_digest=request.mission_context_digest,
            old_route_invalidated=self._old_route_invalidated,
            planner_call_count=self._planner_call_count,
            route_owner_acknowledged=False,
            fresh_route_length=0,
            failure_reason=reason,
        )
        return self._receipt


__all__ = [
    "CAUSE_OF_DIFFERENCE",
    "TARGET_POINT_ABI",
    "TARGET_POINT_PROMPT_FRAGMENT",
    "CandidateForwardNavigationBinding",
    "CandidateLocalNavigationContractError",
    "CandidateLocalNavigationMapper",
    "CandidateLocalNavigationObligation",
    "CandidateLocalNavigationRuntimeCandidate",
    "EgoPose2D",
    "EgoPoseWorldXY",
    "ExistingPlannerCapability",
    "ExistingPlannerTraceCapability",
    "ExistingPlannerTraceResult",
    "FixtureRouteOwner",
    "FreshLocalReplanRequest",
    "GlobalRecoverabilityEvaluator",
    "GlobalRouteReconnectionBridge",
    "GlobalRouteReconnectionReceipt",
    "GlobalRouteReconnectionRequest",
    "GlobalRouteState",
    "LocalRouteElement",
    "MissionNavigationContext",
    "NavigationPoint",
    "PlannerCapabilityStatus",
    "QualificationStatus",
    "RecoverabilityAssessment",
    "RecoverabilityStatus",
    "ResolvedPassengerInterpretation",
    "SceneGroundedLocalBranch",
    "build_fresh_local_replan_request",
    "canonical_navigation_digest",
    "canonical_navigation_projection",
    "enrich_runtime_candidate",
    "materialize_forward_binding",
    "navigation_projection_digest",
]
