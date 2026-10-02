"""Single transaction-bound physical connector-phase owner for Method V3 R2.

The owner is deliberately independent of route projection.  Live CARLA topology
may establish entry, while only the already-bound V2.6 downstream landing owner
may establish physical exit.  Installed-route suffix rows are never operands.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


CONSOLIDATED_PHASE_OWNER_IDENTITY = "METHOD_V3_TRANSACTION_CONNECTOR_PHASE_R2"


class ConnectorPhysicalPhaseR2(str, Enum):
    BEFORE = "BEFORE"
    INSIDE = "INSIDE"
    PHYSICALLY_EXITED = "PHYSICALLY_EXITED"
    TERMINAL_FAIL_CLOSED = "TERMINAL_FAIL_CLOSED"


_FAIL_CLOSED_EXECUTION_STATES = frozenset(
    {
        "INVALIDATED",
        "SAFETY_BLOCKED",
        "RULE_BLOCKED",
        "TIME_BUDGET_EXCEEDED",
        "EVIDENCE_UNKNOWN",
    }
)
_RELEASE_EXECUTION_STATES = frozenset({"MANEUVER_COMMITTED", "MANEUVER_COMPLETED"})


@dataclass(frozen=True)
class ConnectorPhaseTransactionR2:
    route_transaction_identity: str
    installed_route_identity: str
    route_generation: int
    installed_route_catalog_digest: str
    selected_exit_waypoint_id: int
    selected_junction_identity: str
    downstream_landing_identity_digest: str

    def __post_init__(self) -> None:
        for name in (
            "route_transaction_identity",
            "installed_route_identity",
            "installed_route_catalog_digest",
            "selected_junction_identity",
            "downstream_landing_identity_digest",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"CONNECTOR_PHASE_{name.upper()}_INVALID")
        if int(self.route_generation) < 0:
            raise ValueError("CONNECTOR_PHASE_ROUTE_GENERATION_INVALID")
        if int(self.selected_exit_waypoint_id) < 0:
            raise ValueError("CONNECTOR_PHASE_SELECTED_EXIT_WAYPOINT_INVALID")


class ConnectorPhaseOwnerR2:
    """The only writer of transaction connector entry/physical-exit phase."""

    def __init__(self) -> None:
        self._transaction: ConnectorPhaseTransactionR2 | None = None
        self._phase: ConnectorPhysicalPhaseR2 | None = None
        self._entry_latched = False
        self._physical_exit_latched = False
        self._release_latched = False
        self._history: list[dict[str, Any]] = []

    @property
    def transaction(self) -> ConnectorPhaseTransactionR2 | None:
        return self._transaction

    @property
    def phase(self) -> ConnectorPhysicalPhaseR2 | None:
        return self._phase

    @property
    def entered(self) -> bool:
        return self._entry_latched

    @property
    def physically_exited(self) -> bool:
        return self._physical_exit_latched

    @property
    def fail_closed(self) -> bool:
        return self._phase is ConnectorPhysicalPhaseR2.TERMINAL_FAIL_CLOSED

    @property
    def release_latched(self) -> bool:
        return self._release_latched

    def bind(self, transaction: ConnectorPhaseTransactionR2) -> None:
        if not isinstance(transaction, ConnectorPhaseTransactionR2):
            raise TypeError("CONNECTOR_PHASE_TRANSACTION_INVALID")
        if self._transaction is not None:
            raise RuntimeError("CONNECTOR_PHASE_TRANSACTION_ALREADY_BOUND")
        self._transaction = transaction
        self._phase = ConnectorPhysicalPhaseR2.BEFORE
        self._history.append(
            {
                "from": None,
                "to": ConnectorPhysicalPhaseR2.BEFORE.value,
                "reason": "SELECTED_ROUTE_TRANSACTION_BOUND",
                "frame_id": None,
            }
        )

    def _assert_transaction(
        self,
        *,
        route_transaction_identity: str,
        installed_route_identity: str,
        route_generation: int,
        installed_route_catalog_digest: str,
    ) -> ConnectorPhaseTransactionR2:
        transaction = self._transaction
        if transaction is None or self._phase is None:
            raise RuntimeError("CONNECTOR_PHASE_TRANSACTION_NOT_BOUND")
        if str(route_transaction_identity) != transaction.route_transaction_identity:
            raise RuntimeError("CONNECTOR_PHASE_TRANSACTION_STALE")
        if str(installed_route_identity) != transaction.installed_route_identity:
            raise RuntimeError("CONNECTOR_PHASE_ROUTE_IDENTITY_STALE")
        if int(route_generation) != transaction.route_generation:
            raise RuntimeError("CONNECTOR_PHASE_ROUTE_GENERATION_STALE")
        if (
            str(installed_route_catalog_digest)
            != transaction.installed_route_catalog_digest
        ):
            raise RuntimeError("CONNECTOR_PHASE_CATALOG_STALE")
        return transaction

    def _transition(
        self,
        phase: ConnectorPhysicalPhaseR2,
        reason: str,
        frame_id: int | None,
    ) -> None:
        if self._phase is None:
            raise RuntimeError("CONNECTOR_PHASE_TRANSACTION_NOT_BOUND")
        allowed = {
            ConnectorPhysicalPhaseR2.BEFORE: {
                ConnectorPhysicalPhaseR2.INSIDE,
                ConnectorPhysicalPhaseR2.TERMINAL_FAIL_CLOSED,
            },
            ConnectorPhysicalPhaseR2.INSIDE: {
                ConnectorPhysicalPhaseR2.PHYSICALLY_EXITED,
                ConnectorPhysicalPhaseR2.TERMINAL_FAIL_CLOSED,
            },
            ConnectorPhysicalPhaseR2.PHYSICALLY_EXITED: {
                ConnectorPhysicalPhaseR2.TERMINAL_FAIL_CLOSED,
            },
            ConnectorPhysicalPhaseR2.TERMINAL_FAIL_CLOSED: set(),
        }
        if phase is self._phase:
            return
        if phase not in allowed[self._phase]:
            raise RuntimeError("CONNECTOR_PHASE_TRANSITION_INVALID")
        previous = self._phase
        self._phase = phase
        if phase is ConnectorPhysicalPhaseR2.INSIDE:
            self._entry_latched = True
        if phase is ConnectorPhysicalPhaseR2.PHYSICALLY_EXITED:
            self._physical_exit_latched = True
        self._history.append(
            {
                "from": previous.value,
                "to": phase.value,
                "reason": str(reason),
                "frame_id": None if frame_id is None else int(frame_id),
            }
        )

    def observe_live_selected_connector(
        self,
        *,
        selected_connector_membership: bool,
        frame_id: int | None,
        route_transaction_identity: str,
        installed_route_identity: str,
        route_generation: int,
        installed_route_catalog_digest: str,
    ) -> ConnectorPhysicalPhaseR2:
        self._assert_transaction(
            route_transaction_identity=route_transaction_identity,
            installed_route_identity=installed_route_identity,
            route_generation=route_generation,
            installed_route_catalog_digest=installed_route_catalog_digest,
        )
        if (
            selected_connector_membership
            and self._phase is ConnectorPhysicalPhaseR2.BEFORE
        ):
            self._transition(
                ConnectorPhysicalPhaseR2.INSIDE,
                "LIVE_CARLA_EXACT_SELECTED_CONNECTOR_MEMBERSHIP",
                frame_id,
            )
        assert self._phase is not None
        return self._phase

    def reconcile_v2_execution(
        self,
        *,
        execution_state: Any,
        physical_tick_evidence: Mapping[str, Any] | None,
        frame_id: int | None,
        route_transaction_identity: str,
        installed_route_identity: str,
        route_generation: int,
        installed_route_catalog_digest: str,
    ) -> ConnectorPhysicalPhaseR2:
        transaction = self._assert_transaction(
            route_transaction_identity=route_transaction_identity,
            installed_route_identity=installed_route_identity,
            route_generation=route_generation,
            installed_route_catalog_digest=installed_route_catalog_digest,
        )
        state = str(getattr(execution_state, "value", execution_state))
        # Terminal execution owns this call.  A simultaneous stale/late landing
        # row must not create an intermediate PHYSICALLY_EXITED latch/history
        # entry before the terminal transition.
        if state in _FAIL_CLOSED_EXECUTION_STATES:
            if not self.fail_closed:
                self._transition(
                    ConnectorPhysicalPhaseR2.TERMINAL_FAIL_CLOSED,
                    f"V2_EXECUTION_TERMINAL_{state}",
                    frame_id,
                )
            assert self._phase is not None
            return self._phase
        evidence = (
            None
            if not isinstance(physical_tick_evidence, Mapping)
            else physical_tick_evidence
        )
        if evidence is not None:
            evidence_frame = evidence.get("frame_id")
            current_frame = None if frame_id is None else int(frame_id)
            evidence_is_current = (
                current_frame is not None
                and evidence_frame is not None
                and int(evidence_frame) == current_frame
            )
            physical_exit = bool(
                evidence_is_current
                and evidence.get("selected_downstream_landing_identity")
                == transaction.downstream_landing_identity_digest
                and evidence.get("junction_exited") is True
                and evidence.get("selected_downstream_topology_match") is True
                and evidence.get("downstream_lane_semantics_compatible") is True
                and evidence.get("downstream_travel_direction_compatible") is True
                and evidence.get("stable_landing_state") is True
            )
            if physical_exit and self._phase is ConnectorPhysicalPhaseR2.INSIDE:
                self._transition(
                    ConnectorPhysicalPhaseR2.PHYSICALLY_EXITED,
                    "V2_6_TRANSACTION_BOUND_SELECTED_DOWNSTREAM_LANDING",
                    frame_id,
                )
        assert self._phase is not None
        return self._phase

    def release_authorized(self, execution_state: Any) -> bool:
        state = str(getattr(execution_state, "value", execution_state))
        return bool(
            state not in _FAIL_CLOSED_EXECUTION_STATES
            and not self.fail_closed
            and (self.physically_exited or state in _RELEASE_EXECUTION_STATES)
        )

    def acknowledge_release(self, execution_state: Any, frame_id: int | None) -> None:
        """Latch one R2 release event inside the sole consolidated owner."""

        if not self.release_authorized(execution_state):
            raise RuntimeError("CONNECTOR_PHASE_RELEASE_NOT_AUTHORIZED")
        if self._release_latched:
            return
        self._release_latched = True
        self._history.append(
            {
                "from": self._phase.value if self._phase is not None else None,
                "to": self._phase.value if self._phase is not None else None,
                "reason": "R2_TARGET_RELEASE_ACKNOWLEDGED",
                "frame_id": None if frame_id is None else int(frame_id),
            }
        )

    def summary(self) -> dict[str, Any]:
        transaction = self._transaction
        return {
            "owner_identity": CONSOLIDATED_PHASE_OWNER_IDENTITY,
            "phase": None if self._phase is None else self._phase.value,
            "entered": self.entered,
            "physically_exited": self.physically_exited,
            "terminal_fail_closed": self.fail_closed,
            "release_latched": self.release_latched,
            "transaction": (
                None
                if transaction is None
                else {
                    "route_transaction_identity": transaction.route_transaction_identity,
                    "installed_route_identity": transaction.installed_route_identity,
                    "route_generation": transaction.route_generation,
                    "installed_route_catalog_digest": (
                        transaction.installed_route_catalog_digest
                    ),
                    "selected_exit_waypoint_id": (
                        transaction.selected_exit_waypoint_id
                    ),
                    "selected_junction_identity": (
                        transaction.selected_junction_identity
                    ),
                    "downstream_landing_identity_digest": (
                        transaction.downstream_landing_identity_digest
                    ),
                }
            ),
            "history": list(self._history),
            "writer_count": 1,
            "suffix_geometry_is_phase_operand": False,
            "full_route_projection_is_phase_operand": False,
        }


def classify_selected_connector_membership_r2(
    installed_route_catalog: Sequence[Mapping[str, Any]],
    *,
    connector_start_index: int,
    connector_end_index: int,
    waypoint_id: int,
    road_id: int,
    section_id: int,
    lane_id: int,
    junction_id: int | None,
    is_junction: bool,
) -> tuple[str, bool]:
    """Classify only the transaction connector; suffix rows are out of scope."""

    catalog = tuple(installed_route_catalog)
    start, end = int(connector_start_index), int(connector_end_index)
    if not 0 <= start < end < len(catalog):
        raise ValueError("SELECTED_CONNECTOR_INTERVAL_INVALID")

    def matches(row: Mapping[str, Any]) -> bool:
        bound_junction_id = row.get("junction_id")
        return bool(
            int(waypoint_id) == int(row["waypoint_id"])
            or (
                (int(road_id), int(section_id), int(lane_id))
                == (
                    int(row["road_id"]),
                    int(row["section_id"]),
                    int(row["lane_id"]),
                )
                and bool(is_junction) is bool(row["is_junction"])
                and junction_id
                == (None if bound_junction_id is None else int(bound_junction_id))
            )
        )

    membership = any(matches(row) for row in catalog[start:end])
    before = any(matches(row) for row in catalog[:start])
    relation = (
        "SELECTED_CONNECTOR"
        if membership
        else "BEFORE_CONNECTOR" if before else "OUTSIDE_SELECTED_CONNECTOR_IDENTITY"
    )
    return relation, membership


__all__ = [
    "CONSOLIDATED_PHASE_OWNER_IDENTITY",
    "ConnectorPhaseOwnerR2",
    "ConnectorPhaseTransactionR2",
    "ConnectorPhysicalPhaseR2",
    "classify_selected_connector_membership_r2",
]
