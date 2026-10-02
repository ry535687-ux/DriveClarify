"""Pure contracts for the scene-grounded candidate obligation supplier.

This module owns types and constants only.  It deliberately imports nothing from
the language layer, the scene layer or the runtime, so a contract change cannot
smuggle in behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from driveclarify_candidate_local_navigation_bridge import (
    CandidateLocalNavigationObligation,
)


SUPPLIER_IMPLEMENTATION_ID = "R4_4_SCENE_GROUNDED_OBLIGATION_SUPPLIER_V1"
FEATURE_FLAG = "DRIVECLARIFY_R4_4_SCENE_GROUNDED_LOCAL_NAVIGATION"
ROUTE_OWNER_ATTRIBUTE = "driveclarify_candidate_local_route_owner"

# Same convention as the existing RuntimeMapTopologyEnumerator: CARLA/Unreal
# positive yaw delta is the right-hand branch.
STRAIGHT_YAW_TOLERANCE_DEG = 35.0
DEFAULT_LOCAL_HORIZON_MARGIN_M = 1.0


class SupplierContractError(ValueError):
    """Raised when a supplier input fails closed."""


class ObligationType(str, Enum):
    """Open registry of behavioral realizations this supplier can ground."""

    MANEUVER_DIRECTION = "MANEUVER_DIRECTION"
    EXECUTION_LOCATION = "EXECUTION_LOCATION"


class SupplyStatus(str, Enum):
    QUALIFIED = "QUALIFIED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    UNRECOVERABLE = "UNRECOVERABLE"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED_OBLIGATION_TYPE = "UNSUPPORTED_OBLIGATION_TYPE"


@dataclass(frozen=True)
class SemanticInterpretation:
    """One language-derived reading of an unresolved instruction slot."""

    interpretation_id: str
    candidate_id: str
    source_observation_id: str
    anchor_identity: str
    obligation_type: str
    semantic_constraint: str
    semantic_source: str
    grounded_anchor_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in (
            (self.interpretation_id, "INTERPRETATION_ID"),
            (self.candidate_id, "CANDIDATE_ID"),
            (self.source_observation_id, "SOURCE_OBSERVATION_ID"),
            (self.anchor_identity, "ANCHOR_IDENTITY"),
            (self.obligation_type, "OBLIGATION_TYPE"),
            (self.semantic_constraint, "SEMANTIC_CONSTRAINT"),
            (self.semantic_source, "SEMANTIC_SOURCE"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise SupplierContractError(
                    "SEMANTIC_INTERPRETATION_" + name + "_INVALID"
                )
        if not isinstance(self.grounded_anchor_ids, tuple):
            raise SupplierContractError("SEMANTIC_INTERPRETATION_ANCHOR_IDS_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "interpretation_id": self.interpretation_id,
            "candidate_id": self.candidate_id,
            "source_observation_id": self.source_observation_id,
            "anchor_identity": self.anchor_identity,
            "obligation_type": self.obligation_type,
            "semantic_constraint": self.semantic_constraint,
            "semantic_source": self.semantic_source,
            "grounded_anchor_ids": list(self.grounded_anchor_ids),
        }


@dataclass(frozen=True)
class DirectionBranchCandidate:
    """A junction exit already discovered by the existing topology reader."""

    branch_identity: str
    direction: str
    entry_xy: tuple[float, float]
    exit_xy: tuple[float, float]
    exit_planner_endpoint: object
    changes_nominal_route: bool = True


@dataclass(frozen=True)
class SceneGroundingContext:
    """Existing route/topology evidence for one source observation.

    Nothing here is discovered by this package: ``route_rows`` and
    ``maneuver_opportunities`` are the values production already computed, and
    ``direction_branches`` comes from the existing map reader.
    """

    route_rows: tuple[tuple[float, float, str], ...] = ()
    maneuver_opportunities: tuple[Mapping[str, Any], ...] = ()
    direction_branches: tuple[DirectionBranchCandidate, ...] = ()
    nominal_direction: Optional[str] = None

    def branches_for_direction(
        self, direction: str
    ) -> tuple[DirectionBranchCandidate, ...]:
        return tuple(
            branch
            for branch in self.direction_branches
            if branch.direction == direction
        )

    def opportunity_for_order(self, order: int) -> Optional[Mapping[str, Any]]:
        for row in self.maneuver_opportunities:
            try:
                if int(row["route_order_index"]) == int(order):
                    return row
            except (KeyError, TypeError, ValueError):
                continue
        return None

    @property
    def opportunity_count(self) -> int:
        return len(self.maneuver_opportunities)


@dataclass(frozen=True)
class SupplyResult:
    """Per-interpretation answer; never an aggregate decision about K."""

    status: SupplyStatus
    interpretation_id: str
    candidate_id: str
    obligation_type: str
    semantic_constraint: str
    reason_code: str
    obligation: Optional[CandidateLocalNavigationObligation] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, SupplyStatus):
            raise SupplierContractError("SUPPLY_STATUS_INVALID")
        if self.status is SupplyStatus.QUALIFIED and self.obligation is None:
            raise SupplierContractError("QUALIFIED_SUPPLY_REQUIRES_OBLIGATION")
        if self.obligation is not None and not isinstance(
            self.obligation, CandidateLocalNavigationObligation
        ):
            raise SupplierContractError("SUPPLY_OBLIGATION_TYPE_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "interpretation_id": self.interpretation_id,
            "candidate_id": self.candidate_id,
            "obligation_type": self.obligation_type,
            "semantic_constraint": self.semantic_constraint,
            "reason_code": self.reason_code,
            "obligation_digest": (
                None
                if self.obligation is None
                else self.obligation.obligation_digest
            ),
        }


__all__ = [
    "DEFAULT_LOCAL_HORIZON_MARGIN_M",
    "DirectionBranchCandidate",
    "FEATURE_FLAG",
    "ObligationType",
    "ROUTE_OWNER_ATTRIBUTE",
    "SUPPLIER_IMPLEMENTATION_ID",
    "STRAIGHT_YAW_TOLERANCE_DEG",
    "SceneGroundingContext",
    "SemanticInterpretation",
    "SupplierContractError",
    "SupplyResult",
    "SupplyStatus",
]
