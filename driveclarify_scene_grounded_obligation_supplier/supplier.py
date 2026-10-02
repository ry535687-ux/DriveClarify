"""The ambiguity-generic supplier itself.

One already-identified semantic interpretation in, at most one
``CandidateLocalNavigationObligation`` out.  The supplier owns no planner, no
path search, no ambiguity detection and no language model, and it never decides
K: it answers per interpretation and the caller counts.
"""

from __future__ import annotations

from typing import Optional

from driveclarify_candidate_local_navigation_bridge import (
    CandidateLocalNavigationMapper,
    MissionNavigationContext,
    QualificationStatus,
    ResolvedPassengerInterpretation,
    SceneGroundedLocalBranch,
)

from .contracts import (
    ObligationType,
    SUPPLIER_IMPLEMENTATION_ID,
    SceneGroundingContext,
    SemanticInterpretation,
    SupplierContractError,
    SupplyResult,
    SupplyStatus,
)
from .interpretations import DIRECTION_VALUE_SPACE, ORDINAL_TO_ROUTE_ORDER
from .scene_grounding import branch_identity, local_branch


_QUALIFICATION_TO_SUPPLY = {
    QualificationStatus.QUALIFIED: SupplyStatus.QUALIFIED,
    QualificationStatus.NOT_AVAILABLE: SupplyStatus.NOT_AVAILABLE,
    QualificationStatus.UNRECOVERABLE: SupplyStatus.UNRECOVERABLE,
    QualificationStatus.UNKNOWN: SupplyStatus.UNKNOWN,
}


class SceneGroundedCandidateObligationSupplier:
    """Ground one semantic interpretation against the existing scene.

    Selection happens only among branches the caller already discovered with the
    existing topology reader; qualification is delegated to the injected
    ``CandidateLocalNavigationMapper``, which uses the one existing planner
    capability.
    """

    implementation_id = SUPPLIER_IMPLEMENTATION_ID

    def __init__(self, mapper: CandidateLocalNavigationMapper) -> None:
        if not isinstance(mapper, CandidateLocalNavigationMapper):
            raise SupplierContractError("CANDIDATE_LOCAL_NAVIGATION_MAPPER_INVALID")
        self._mapper = mapper
        self._supply_count = 0

    @property
    def mapper(self) -> CandidateLocalNavigationMapper:
        return self._mapper

    @property
    def supply_count(self) -> int:
        return self._supply_count

    def supply(
        self,
        mission_context: MissionNavigationContext,
        interpretation: SemanticInterpretation,
        grounding_context: SceneGroundingContext,
    ) -> SupplyResult:
        if not isinstance(mission_context, MissionNavigationContext):
            raise SupplierContractError("MISSION_CONTEXT_INVALID")
        if not isinstance(interpretation, SemanticInterpretation):
            raise SupplierContractError("SEMANTIC_INTERPRETATION_INVALID")
        if not isinstance(grounding_context, SceneGroundingContext):
            raise SupplierContractError("SCENE_GROUNDING_CONTEXT_INVALID")
        if mission_context is not self._mapper.mission_context:
            raise SupplierContractError("SUPPLIER_MISSION_CONTEXT_MISMATCH")
        self._supply_count += 1

        obligation_type = interpretation.obligation_type
        grounder = self._GROUNDERS.get(obligation_type)
        if grounder is None:
            return SupplyResult(
                status=SupplyStatus.UNSUPPORTED_OBLIGATION_TYPE,
                interpretation_id=interpretation.interpretation_id,
                candidate_id=interpretation.candidate_id,
                obligation_type=obligation_type,
                semantic_constraint=interpretation.semantic_constraint,
                reason_code="OBLIGATION_TYPE_NOT_REALIZED_BY_CURRENT_BRIDGE",
            )

        branch = grounder(self, interpretation, grounding_context)
        if branch is None:
            # The semantic constraint has no matching scene branch.  It is never
            # re-pointed at the nearest other branch and never replaced by the
            # nominal global route.
            return SupplyResult(
                status=SupplyStatus.NOT_AVAILABLE,
                interpretation_id=interpretation.interpretation_id,
                candidate_id=interpretation.candidate_id,
                obligation_type=obligation_type,
                semantic_constraint=interpretation.semantic_constraint,
                reason_code="SEMANTIC_CONSTRAINT_NOT_SCENE_GROUNDED",
            )

        resolved = ResolvedPassengerInterpretation(
            candidate_id=interpretation.candidate_id,
            interpretation_id=interpretation.interpretation_id,
            source_observation_id=interpretation.source_observation_id,
            anchor_identity=interpretation.anchor_identity,
            maneuver_family=obligation_type,
            # The frozen bridge type spells this field maneuver_direction; it
            # carries the type-neutral semantic constraint of any family.
            maneuver_direction=interpretation.semantic_constraint,
            local_branch_identity=branch.local_branch_identity,
        )
        obligation = self._mapper.map(resolved, branch)
        return SupplyResult(
            status=_QUALIFICATION_TO_SUPPLY[obligation.qualification_status],
            interpretation_id=interpretation.interpretation_id,
            candidate_id=interpretation.candidate_id,
            obligation_type=obligation_type,
            semantic_constraint=interpretation.semantic_constraint,
            reason_code=obligation.reason_code,
            obligation=obligation,
        )

    def _ground_direction(
        self,
        interpretation: SemanticInterpretation,
        grounding_context: SceneGroundingContext,
    ) -> Optional[SceneGroundedLocalBranch]:
        direction = interpretation.semantic_constraint
        if direction not in DIRECTION_VALUE_SPACE:
            return None
        matching = grounding_context.branches_for_direction(direction)
        if len(matching) != 1:
            # Zero matches is an unavailable branch; several matches for one
            # semantic direction is ambiguous scene evidence, not a licence to
            # choose the closest one.
            return None
        branch = matching[0]
        return local_branch(
            branch_identity_value=branch.branch_identity,
            entry_xy=branch.entry_xy,
            exit_xy=branch.exit_xy,
            exit_planner_endpoint=branch.exit_planner_endpoint,
            road_option=direction,
            changes_nominal_route=branch.changes_nominal_route,
        )

    def _ground_execution_location(
        self,
        interpretation: SemanticInterpretation,
        grounding_context: SceneGroundingContext,
    ) -> Optional[SceneGroundedLocalBranch]:
        constraint = interpretation.semantic_constraint
        if constraint not in ORDINAL_TO_ROUTE_ORDER:
            return None
        order = ORDINAL_TO_ROUTE_ORDER[constraint]
        if order is None:
            if grounding_context.opportunity_count == 0:
                return None
            order = grounding_context.opportunity_count
        row = grounding_context.opportunity_for_order(int(order))
        if row is None:
            return None
        anchor = row.get("anchor_xy")
        try:
            exit_xy = (float(anchor[0]), float(anchor[1]))
            opportunity_index = int(row["route_opportunity_index"])
        except (IndexError, KeyError, TypeError, ValueError):
            return None
        rows = grounding_context.route_rows
        if not rows or opportunity_index < 0 or opportunity_index >= len(rows):
            return None
        entry_row = rows[opportunity_index]
        entry_xy = (float(entry_row[0]), float(entry_row[1]))
        maneuver_direction = str(
            row.get("maneuver_direction") or "UNKNOWN"
        ).upper().rsplit(".", 1)[-1]
        nominal_route_option = str(entry_row[2]).upper().rsplit(".", 1)[-1]
        identity = branch_identity(
            "branch-execution-location",
            {
                "route_order_index": int(order),
                "branch_id": str(row.get("branch_id") or ""),
                "anchor": [round(exit_xy[0], 3), round(exit_xy[1], 3)],
            },
        )
        # Receipt serialization intentionally strips live CARLA objects.  The
        # numeric fallback therefore has to be re-spelled in the native type
        # required by the already-existing GlobalRoutePlanner, exactly as the
        # unchanged mission destination is.  No coordinate or planner changes.
        from .mission_context import _planner_native_endpoint  # noqa: PLC0415

        planner_endpoint = row.get("branch_exit_planner_endpoint")
        if planner_endpoint is None:
            planner_endpoint = _planner_native_endpoint(
                (round(exit_xy[0], 3), round(exit_xy[1], 3), 0.0)
            )
        return local_branch(
            branch_identity_value=identity,
            entry_xy=entry_xy,
            exit_xy=exit_xy,
            exit_planner_endpoint=planner_endpoint,
            road_option=maneuver_direction,
            # Route change is a geometric/navigation fact, never an ordinal
            # property.  The opportunity enumerator and route snapshot already
            # publish both sides of this comparison.  In particular, FIRST
            # does not imply that the branch is the nominal route.
            changes_nominal_route=(nominal_route_option != maneuver_direction),
        )

    # Open registry: dispatch is on obligation_type, never on a direction value.
    _GROUNDERS = {
        ObligationType.MANEUVER_DIRECTION.value: _ground_direction,
        ObligationType.EXECUTION_LOCATION.value: _ground_execution_location,
    }


__all__ = ["SceneGroundedCandidateObligationSupplier"]
