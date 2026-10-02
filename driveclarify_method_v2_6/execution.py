"""V2.6 completion overlay; commitment and handover remain V2/V2.5-owned."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from driveclarify_method_revision_v2 import (
    BranchCommitmentContract,
    LiveManeuverObservation,
    ManeuverExecutionState,
    SelectedNavigationIdentity,
    project_point_to_polyline,
)
from driveclarify_method_v2_5 import CompletionHandoverManeuverExecution

from .topology import (
    DownstreamLandingEvidence,
    SelectedBranchDownstreamLandingIdentity,
)


class GenericDownstreamLandingManeuverExecution(
    CompletionHandoverManeuverExecution
):
    """Complete only on the pre-derived selected-branch downstream topology."""

    schema_version = "driveclarify.method_v2_6.execution.v1"

    def __init__(self) -> None:
        super().__init__()
        self.downstream_landing_identity: (
            SelectedBranchDownstreamLandingIdentity | None
        ) = None

    def bind_downstream_landing(
        self, identity: SelectedBranchDownstreamLandingIdentity
    ) -> None:
        if self.state is not ManeuverExecutionState.PENDING:
            raise RuntimeError("DOWNSTREAM_LANDING_BINDING_TOO_LATE")
        if not isinstance(identity, SelectedBranchDownstreamLandingIdentity):
            raise TypeError("DOWNSTREAM_LANDING_IDENTITY_INVALID")
        if self.downstream_landing_identity is not None:
            raise RuntimeError("DOWNSTREAM_LANDING_ALREADY_BOUND")
        self.downstream_landing_identity = identity

    @staticmethod
    def _owner_matches_execution_context(
        owner: SelectedBranchDownstreamLandingIdentity,
        identity: SelectedNavigationIdentity,
        contract: BranchCommitmentContract,
    ) -> bool:
        return bool(
            owner.selected_candidate_id == identity.candidate_id
            and owner.selected_obligation_identity == identity.obligation_identity
            and owner.selected_obligation_digest == identity.obligation_digest
            and owner.selected_branch_identity == identity.branch_identity
            and owner.selected_branch_digest == identity.branch_digest
            and owner.selected_navigation_context_identity
            == identity.navigation_context_identity
            and owner.global_destination_identity
            == identity.global_destination_identity
            and owner.selected_junction_identity
            == contract.selected_junction_identity
            and (
                owner.selected_lane_link_entry.road_id,
                owner.selected_lane_link_entry.lane_id,
            )
            in contract.selected_lane_links
            and owner.selected_lane_link_exit.road_id
            == contract.selected_exit_road_id
            and owner.selected_lane_link_exit.lane_id
            == contract.selected_exit_lane_id
        )

    def activate(
        self,
        identity: SelectedNavigationIdentity,
        contract: BranchCommitmentContract,
        **kwargs: Any,
    ) -> None:
        owner = self.downstream_landing_identity
        if owner is None:
            raise RuntimeError("DOWNSTREAM_LANDING_IDENTITY_MISSING_AT_ACTIVATION")
        if not self._owner_matches_execution_context(owner, identity, contract):
            raise RuntimeError("DOWNSTREAM_LANDING_EXECUTION_CONTEXT_MISMATCH")
        super().activate(identity, contract, **kwargs)

    def observe_with_downstream_landing(
        self,
        observation: LiveManeuverObservation,
        evidence: DownstreamLandingEvidence,
    ) -> ManeuverExecutionState:
        if self.state not in {
            ManeuverExecutionState.ACTIVE,
            ManeuverExecutionState.MANEUVER_COMMITTED,
        }:
            return super().observe(observation)
        if not isinstance(observation, LiveManeuverObservation):
            return super().observe(observation)
        if self.downstream_landing_identity is None:
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "DOWNSTREAM_LANDING_IDENTITY_MISSING",
                getattr(observation, "frame_id", None),
            )

        if not isinstance(evidence, DownstreamLandingEvidence):
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "DOWNSTREAM_LANDING_EVIDENCE_TYPE_INVALID",
                observation.frame_id,
            )
        owner = self.downstream_landing_identity
        assert self.identity is not None and self.contract is not None
        if not self._owner_matches_execution_context(
            owner, self.identity, self.contract
        ):
            return self._transition(
                ManeuverExecutionState.INVALIDATED,
                "DOWNSTREAM_LANDING_EXECUTION_CONTEXT_CHANGED",
                observation.frame_id,
            )
        if (
            evidence.observation_id != observation.observation_id
            or evidence.frame_id != observation.frame_id
        ):
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "DOWNSTREAM_LANDING_EVIDENCE_NOT_CURRENT",
                observation.frame_id,
            )
        if evidence.owner_identity_digest != owner.identity_digest:
            return self._transition(
                ManeuverExecutionState.INVALIDATED,
                "DOWNSTREAM_LANDING_OWNER_IDENTITY_CHANGED",
                observation.frame_id,
            )
        if evidence.evidence_status == "INVALID":
            return self._transition(
                ManeuverExecutionState.INVALIDATED,
                "DOWNSTREAM_LANDING_MAP_IDENTITY_CHANGED",
                observation.frame_id,
            )
        if evidence.evidence_status != "AVAILABLE":
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "DOWNSTREAM_LANDING_TOPOLOGY_UNKNOWN",
                observation.frame_id,
            )
        current_lane = evidence.current_lane
        matching_owner_lane = (
            None
            if current_lane is None
            else next(
                (
                    item
                    for item in owner.downstream_lanes
                    if item.lane_key == current_lane.lane_key
                ),
                None,
            )
        )
        expected_junction_exited = bool(
            current_lane is not None
            and not observation.is_junction
            and not current_lane.is_junction
        )
        expected_topology_match = bool(
            expected_junction_exited
            and matching_owner_lane is not None
            and evidence.map_name == owner.map_name
            and evidence.map_opendrive_sha256
            == owner.map_opendrive_sha256
        )
        expected_lane_compatible = bool(
            expected_topology_match
            and current_lane is not None
            and matching_owner_lane is not None
            and current_lane.lane_type == matching_owner_lane.lane_type == "Driving"
        )
        expected_direction_dot = (
            None
            if current_lane is None or matching_owner_lane is None
            else sum(
                left * right
                for left, right in zip(
                    current_lane.travel_direction_unit_xy,
                    matching_owner_lane.travel_direction_unit_xy,
                )
            )
        )
        expected_direction_compatible = bool(
            expected_direction_dot is not None
            and math.isfinite(expected_direction_dot)
            and expected_direction_dot > 0.0
        )
        expected_stable_landing = bool(
            expected_junction_exited
            and expected_topology_match
            and expected_lane_compatible
            and expected_direction_compatible
            and current_lane is not None
            and current_lane.road_id == observation.road_id
            and current_lane.lane_id == observation.lane_id
        )
        expected_matching_alternatives = tuple(
            sorted(
                branch.branch_digest
                for branch in owner.alternative_branches
                if current_lane is not None
                and current_lane.lane_key in branch.downstream_lane_keys
            )
        )
        expected_alternative_separated = bool(
            expected_stable_landing
            and len(owner.alternative_branches) > 0
            and not expected_matching_alternatives
        )
        direction_dot_consistent = bool(
            (evidence.travel_direction_dot is None and expected_direction_dot is None)
            or (
                evidence.travel_direction_dot is not None
                and expected_direction_dot is not None
                and math.isfinite(evidence.travel_direction_dot)
                and math.isclose(
                    evidence.travel_direction_dot,
                    expected_direction_dot,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )
        )
        evidence_conjuncts_consistent = bool(
            evidence.junction_exited == expected_junction_exited
            and evidence.topology_match == expected_topology_match
            and evidence.lane_semantics_compatible == expected_lane_compatible
            and evidence.travel_direction_compatible
            == expected_direction_compatible
            and evidence.stable_landing == expected_stable_landing
            and evidence.alternative_branch_count
            == len(owner.alternative_branches)
            and tuple(evidence.matching_alternative_branch_digests)
            == expected_matching_alternatives
            and evidence.alternative_topology_separated
            == expected_alternative_separated
            and direction_dot_consistent
        )
        if not evidence_conjuncts_consistent:
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "DOWNSTREAM_LANDING_EVIDENCE_CONJUNCTS_INCONSISTENT",
                observation.frame_id,
            )
        state_before = self.state
        try:
            _, current_branch_progress = project_point_to_polyline(
                (observation.ego_x_m, observation.ego_y_m),
                self.contract.branch_polyline_xy_m,
            )
        except ValueError:
            return self._transition(
                ManeuverExecutionState.EVIDENCE_UNKNOWN,
                "LIVE_BRANCH_PROJECTION_UNKNOWN",
                observation.frame_id,
            )
        alternative_non_executability_proven = bool(
            expected_alternative_separated
            and current_branch_progress
            >= self.contract.structural_divergence_branch_progress_m
        )
        # A current selected downstream match repairs the semantic branch's
        # road/lane representation. Predicate H is changed only when the same
        # frame has crossed the frozen structural divergence boundary and the
        # current lane is separated from every pre-bound alternative topology.
        effective_observation = (
            replace(
                observation,
                alternative_topologically_executable=False,
            )
            if alternative_non_executability_proven
            else observation
        )
        original_contract = self.contract
        assert original_contract is not None
        if (
            state_before is ManeuverExecutionState.ACTIVE
            and expected_stable_landing
        ):
            # Extend only the road/lane *representation* of the already-bound
            # selected branch for this current evidence transaction. Base V2's
            # byte-frozen conjunction, corridor, progress and thresholds remain
            # the sole commitment reader. The original contract is restored.
            self.contract = replace(
                original_contract,
                selected_lane_links=tuple(
                    dict.fromkeys(
                        original_contract.selected_lane_links
                        + ((int(observation.road_id), int(observation.lane_id)),)
                    )
                ),
            )
        elif state_before is ManeuverExecutionState.MANEUVER_COMMITTED:
            # Suppress only the historical fixed road/lane completion check;
            # V2.6 completion remains owned by the topology-derived landing.
            self.contract = replace(
                original_contract,
                selected_exit_road_id=int(observation.road_id),
                selected_exit_lane_id=int(observation.lane_id) + 1,
            )
        try:
            state = super().observe(effective_observation)
        finally:
            self.contract = original_contract
        if not (
            self.physical_tick_evidence
            and self.physical_tick_evidence[-1].get("frame_id")
            == observation.frame_id
        ):
            return state
        row = self.physical_tick_evidence[-1]
        row.update(
            {
                "selected_downstream_landing_identity": owner.identity_digest,
                "selected_downstream_topology_match": evidence.topology_match,
                "junction_exited": evidence.junction_exited,
                "downstream_lane_semantics_compatible": (
                    evidence.lane_semantics_compatible
                ),
                "downstream_travel_direction_dot": evidence.travel_direction_dot,
                "downstream_travel_direction_compatible": (
                    evidence.travel_direction_compatible
                ),
                "stable_landing_state": evidence.stable_landing,
                "stable_landing_fixed_tick_or_distance_dependency": False,
                "downstream_landing_evidence": evidence.to_dict(),
                "selected_branch_membership_owner": (
                    "V2_6_CURRENT_TOPOLOGY_DERIVED_SELECTED_BRANCH_CONTINUATION"
                    if expected_stable_landing
                    else "V2_EXACT_SELECTED_JUNCTION_LANE_LINK"
                ),
                "alternative_executability_owner": (
                    "V2_6_BOUND_ALTERNATIVE_TOPOLOGY_SEPARATION_AT_STRUCTURAL_DIVERGENCE"
                    if alternative_non_executability_proven
                    else "V2_EXACT_SELECTED_JUNCTION_LANE_LINK"
                ),
                "alternative_branch_count": len(owner.alternative_branches),
                "matching_alternative_branch_digests": list(
                    expected_matching_alternatives
                ),
                "alternative_topology_separated": (
                    expected_alternative_separated
                ),
                "alternative_non_executability_proven": (
                    alternative_non_executability_proven
                ),
            }
        )
        if (
            state_before is ManeuverExecutionState.MANEUVER_COMMITTED
            and state is ManeuverExecutionState.MANEUVER_COMMITTED
            and expected_stable_landing
        ):
            return self._transition(
                ManeuverExecutionState.MANEUVER_COMPLETED,
                "SELECTED_BRANCH_TOPOLOGY_DERIVED_DOWNSTREAM_LANDING_OBSERVED",
                observation.frame_id,
            )
        return state

    def summary(self) -> dict[str, Any]:
        value = super().summary()
        value.update(
            {
                "completion_predicate_owner": (
                    "METHOD_V2_6_PRE_DERIVED_SELECTED_BRANCH_DOWNSTREAM_TOPOLOGY"
                ),
                "selected_branch_downstream_landing_identity": (
                    None
                    if self.downstream_landing_identity is None
                    else self.downstream_landing_identity.to_dict()
                ),
                "stable_landing_semantics": (
                    "CURRENT_FINITE_AUDITABLE_NON_JUNCTION_WAYPOINT_MATCH"
                ),
                "stable_landing_fixed_tick_or_distance_dependency": False,
                "new_planner_count": 0,
            }
        )
        return value
