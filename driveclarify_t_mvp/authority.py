"""Atomic authority capture and candidate-isolation enforcement."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Generic, Protocol, TypeVar

from .canonical import canonical_sha256
from .models import (
    Availability,
    GlobalTask,
    ObservableCommitment,
    POldSnapshot,
    RouteRow,
    UnknownValue,
)


class AtomicityError(RuntimeError):
    pass


class CandidateIsolationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthorityExport:
    """One lock-consistent read of every authoritative identity and byte set."""

    route_identity: str
    route_generation: int
    global_task: GlobalTask
    full_route: tuple[RouteRow, ...]
    active_suffix: tuple[RouteRow, ...]
    active_suffix_identity: str
    current_maneuver_identity: str
    current_branch_or_connector_identity: str
    route_owner_identity: str
    active_suffix_owner_identity: str
    planner_owner_identity: str
    control_owner_identity: str
    source_frame: int
    source_sim_time_s: float
    boundary_identity: str
    component_versions: tuple[tuple[str, int], ...]
    last_consumed_route_identity: str | None
    saved_route_state_sha256: str
    active_route_distance_state_sha256: str
    route_owner_write_count: int
    route_switch_lifecycle_sha256: str
    cache_generation_sha256: str
    controller_pid_state_sha256: str

    def assert_atomic(self) -> None:
        if not self.component_versions:
            raise AtomicityError("P_OLD_COMPONENT_VERSIONS_MISSING")
        mismatches = tuple(
            name
            for name, version in self.component_versions
            if int(version) != int(self.route_generation)
        )
        if mismatches:
            raise AtomicityError(
                "P_OLD_AUTHORITY_VERSION_MISMATCH:" + ",".join(mismatches)
            )
        if not self.full_route or not self.active_suffix:
            raise AtomicityError("P_OLD_ROUTE_PROJECTION_MISSING")
        if self.source_frame < 0 or not math.isfinite(self.source_sim_time_s):
            raise AtomicityError("P_OLD_SOURCE_FRAME_INVALID")
        required_identities = (
            self.route_identity,
            self.active_suffix_identity,
            self.current_maneuver_identity,
            self.current_branch_or_connector_identity,
            self.route_owner_identity,
            self.active_suffix_owner_identity,
            self.planner_owner_identity,
            self.control_owner_identity,
            self.boundary_identity,
        )
        if any(not str(value).strip() for value in required_identities):
            raise AtomicityError("P_OLD_REQUIRED_IDENTITY_OR_OWNER_MISSING")
        if self.route_generation < 0 or self.route_owner_write_count < 0:
            raise AtomicityError("P_OLD_AUTHORITY_COUNTER_INVALID")
        required_hashes = (
            self.saved_route_state_sha256,
            self.active_route_distance_state_sha256,
            self.route_switch_lifecycle_sha256,
            self.cache_generation_sha256,
            self.controller_pid_state_sha256,
        )
        if any(len(str(value)) != 64 for value in required_hashes):
            raise AtomicityError("P_OLD_COMPONENT_HASH_INVALID")


class AtomicAuthoritySource(Protocol):
    def export_atomic(self) -> AuthorityExport:
        """Capture under the authoritative owner's lock or equivalent seam."""


@dataclass(frozen=True)
class SnapshotCapture:
    availability: Availability
    snapshot: POldSnapshot | None
    unknown: UnknownValue | None


def capture_p_old(
    source: AtomicAuthoritySource,
    observable_commitment: ObservableCommitment,
) -> SnapshotCapture:
    """Capture one complete pre-update authority boundary, fail closed."""

    try:
        exported = source.export_atomic()
        exported.assert_atomic()
    except (AtomicityError, AttributeError, TypeError, ValueError) as error:
        return SnapshotCapture(
            availability=Availability.UNKNOWN,
            snapshot=None,
            unknown=UnknownValue(
                reason_code="UNKNOWN_INCOMPLETE_AUTHORITY_SNAPSHOT",
                missing_source=str(error),
                expected_owner="AtomicAuthoritySource.export_atomic",
                affected_fields=(
                    "authoritative_route_identity",
                    "route_generation",
                    "full_route_projection",
                    "active_suffix_projection",
                    "owners",
                ),
            ),
        )

    if (
        observable_commitment.source_frame != exported.source_frame
        or observable_commitment.source_sim_time_s != exported.source_sim_time_s
        or observable_commitment.source_route_generation
        != exported.route_generation
    ):
        return SnapshotCapture(
            availability=Availability.UNKNOWN,
            snapshot=None,
            unknown=UnknownValue(
                reason_code="UNKNOWN_INCOMPLETE_AUTHORITY_SNAPSHOT",
                missing_source="OBSERVABLE_COMMITMENT_AUTHORITY_BOUNDARY_MISMATCH",
                expected_owner="pre-update authority boundary",
                affected_fields=(
                    "observable_commitment",
                    "source_frame",
                    "source_sim_time_s",
                    "source_route_generation",
                ),
            ),
        )

    component_hashes = (
        ("full_route_hash", canonical_sha256(exported.full_route)),
        ("active_suffix_hash", canonical_sha256(exported.active_suffix)),
        ("saved_route_state_hash", exported.saved_route_state_sha256),
        ("active_route_distance_state_hash", exported.active_route_distance_state_sha256),
        ("route_switch_lifecycle_hash", exported.route_switch_lifecycle_sha256),
        ("cache_generation_hash", exported.cache_generation_sha256),
        ("controller_pid_state_hash", exported.controller_pid_state_sha256),
    )
    values = {
        "authoritative_route_identity": exported.route_identity,
        "route_generation": exported.route_generation,
        "global_task": exported.global_task,
        "full_route_projection": exported.full_route,
        "active_suffix_projection": exported.active_suffix,
        "active_suffix_identity": exported.active_suffix_identity,
        "current_maneuver_identity": exported.current_maneuver_identity,
        "current_branch_or_connector_identity": (
            exported.current_branch_or_connector_identity
        ),
        "route_owner_identity": exported.route_owner_identity,
        "active_suffix_owner_identity": exported.active_suffix_owner_identity,
        "planner_owner_identity": exported.planner_owner_identity,
        "control_owner_identity": exported.control_owner_identity,
        "observable_commitment": observable_commitment,
        "source_frame": exported.source_frame,
        "source_sim_time_s": exported.source_sim_time_s,
        "boundary_identity": exported.boundary_identity,
        "last_consumed_route_identity": exported.last_consumed_route_identity,
        "component_hashes": component_hashes,
    }
    provisional = POldSnapshot(canonical_sha256="", **values)
    snapshot = POldSnapshot(canonical_sha256=canonical_sha256(provisional), **values)
    return SnapshotCapture(Availability.AVAILABLE, snapshot, None)


@dataclass(frozen=True)
class AuthorityFingerprint:
    route_identity: str
    route_generation: int
    full_route_sha256: str
    active_suffix_sha256: str
    active_suffix_identity: str
    current_maneuver_identity: str
    current_branch_or_connector_identity: str
    route_owner_identity: str
    active_suffix_owner_identity: str
    planner_owner_identity: str
    control_owner_identity: str
    saved_route_state_sha256: str
    active_route_distance_state_sha256: str
    last_consumed_route_identity: str | None
    route_owner_write_count: int
    route_switch_lifecycle_sha256: str
    cache_generation_sha256: str
    controller_pid_state_sha256: str
    global_task_identity: str

    @classmethod
    def from_export(cls, value: AuthorityExport) -> "AuthorityFingerprint":
        value.assert_atomic()
        return cls(
            route_identity=value.route_identity,
            route_generation=value.route_generation,
            full_route_sha256=canonical_sha256(value.full_route),
            active_suffix_sha256=canonical_sha256(value.active_suffix),
            active_suffix_identity=value.active_suffix_identity,
            current_maneuver_identity=value.current_maneuver_identity,
            current_branch_or_connector_identity=(
                value.current_branch_or_connector_identity
            ),
            route_owner_identity=value.route_owner_identity,
            active_suffix_owner_identity=value.active_suffix_owner_identity,
            planner_owner_identity=value.planner_owner_identity,
            control_owner_identity=value.control_owner_identity,
            saved_route_state_sha256=value.saved_route_state_sha256,
            active_route_distance_state_sha256=(
                value.active_route_distance_state_sha256
            ),
            last_consumed_route_identity=value.last_consumed_route_identity,
            route_owner_write_count=value.route_owner_write_count,
            route_switch_lifecycle_sha256=value.route_switch_lifecycle_sha256,
            cache_generation_sha256=value.cache_generation_sha256,
            controller_pid_state_sha256=value.controller_pid_state_sha256,
            global_task_identity=value.global_task.identity,
        )

    @property
    def canonical_sha256(self) -> str:
        return canonical_sha256(self)


T = TypeVar("T")


@dataclass(frozen=True)
class IsolationProof(Generic[T]):
    prepared: T
    authority_before: AuthorityFingerprint
    authority_after: AuthorityFingerprint
    authority_unchanged: bool


class CandidateIsolationGuard:
    """Reject any preparation-time authority mutation, including rollback."""

    def __init__(self, source: AtomicAuthoritySource):
        self._source = source

    def prepare(self, function: Callable[[], T]) -> IsolationProof[T]:
        before = AuthorityFingerprint.from_export(self._source.export_atomic())
        preparation_error: BaseException | None = None
        prepared: T | None = None
        try:
            prepared = function()
        except BaseException as error:  # compare state before propagating errors
            preparation_error = error
        after = AuthorityFingerprint.from_export(self._source.export_atomic())
        if before != after:
            raise CandidateIsolationError(
                "CANDIDATE_ISOLATION_VIOLATION:"
                + before.canonical_sha256
                + ":"
                + after.canonical_sha256
            ) from preparation_error
        if preparation_error is not None:
            raise preparation_error
        return IsolationProof(
            prepared=prepared,  # type: ignore[arg-type]
            authority_before=before,
            authority_after=after,
            authority_unchanged=True,
        )
