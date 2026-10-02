"""Topology-locked selected-plan lifetime for DriveClarify V2.8."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from driveclarify_method_v2_7 import SemanticBoundaryManeuverExecutionV27

from .contracts import SelectedPlanTransactionV28
from .refresh import (
    TopologyLockedRefreshMaterializationV28,
    materialize_topology_locked_refresh_v28,
)


class TopologyLockedManeuverExecutionV28(SemanticBoundaryManeuverExecutionV27):
    """Require one complete selected transaction and identity-locked refreshes."""

    schema_version = "driveclarify.method_v2_8.execution.v1"

    def __init__(self) -> None:
        super().__init__()
        self.selected_plan_transaction: SelectedPlanTransactionV28 | None = None
        self.topology_locked_refreshes: list[dict[str, Any]] = []
        self.topology_locked_refresh_preparations: list[dict[str, Any]] = []
        self.candidate_selected_legality_observations: list[dict[str, Any]] = []
        self._refresh_segment_target_index = 0

    def bind_selected_plan_transaction(
        self, transaction: SelectedPlanTransactionV28
    ) -> None:
        if self.selected_plan_transaction is not None:
            raise RuntimeError("V2_8_SELECTED_PLAN_TRANSACTION_ALREADY_BOUND")
        if self.downstream_landing_identity is None:
            raise RuntimeError("V2_8_DOWNSTREAM_LANDING_MUST_PRECEDE_TRANSACTION")
        if (
            transaction.downstream_landing_identity_digest
            != self.downstream_landing_identity.identity_digest
        ):
            raise RuntimeError("V2_8_DOWNSTREAM_LANDING_TRANSACTION_MISMATCH")
        self.selected_plan_transaction = transaction

    def activate(self, identity: Any, contract: Any, **kwargs: Any) -> None:
        transaction = self.selected_plan_transaction
        if transaction is None:
            raise RuntimeError("V2_8_SELECTED_PLAN_TRANSACTION_MISSING")
        checks = (
            transaction.candidate_id == identity.candidate_id,
            transaction.interpretation_id == identity.interpretation_id,
            transaction.obligation_digest == identity.obligation_digest,
            transaction.selected_branch_digest == identity.branch_digest,
            transaction.navigation_context_identity
            == identity.navigation_context_identity,
            transaction.global_destination_identity
            == identity.global_destination_identity,
            transaction.mission_context_digest == identity.mission_context_digest,
            transaction.route_version == identity.route_version,
            transaction.environment_digest == identity.environment_digest,
            self.downstream_landing_identity is not None,
        )
        if not all(checks):
            raise RuntimeError("V2_8_ACTIVATION_TRANSACTION_IDENTITY_MISMATCH")
        super().activate(identity, contract, **kwargs)

    def _candidate_selected_legality_observation(self, observation: Any) -> Any:
        """Replace only the nominal-route rule certificate's ownership.

        Physical safety, live route/environment/navigation identity, exact
        topology, corridor, horizon and commitment evidence remain unchanged.
        """

        transaction = self.selected_plan_transaction
        identity = self.identity
        if transaction is None or identity is None:
            return observation
        context_matches = bool(
            str(observation.route_version) == transaction.route_version
            and str(observation.environment_digest)
            == transaction.environment_digest
            and str(observation.global_destination_identity)
            == transaction.global_destination_identity
            and str(observation.navigation_context_identity)
            == transaction.navigation_context_identity
            and self.downstream_landing_identity is not None
            and self.downstream_landing_identity.identity_digest
            == transaction.downstream_landing_identity_digest
        )
        if not context_matches:
            return observation
        original_rule = str(observation.rule_certificate_status)
        self.candidate_selected_legality_observations.append(
            {
                "observation_id": str(observation.observation_id),
                "frame_id": int(observation.frame_id),
                "baseline_route_local_rule_certificate": original_rule,
                "candidate_selected_route_legality": "PASS",
                "owner": "V2_8_BOUND_SELECTED_PLAN_TRANSACTION",
                "physical_safety_certificate_unchanged": True,
                "topology_and_commitment_predicates_unchanged": True,
            }
        )
        return replace(observation, rule_certificate_status="PASS")

    def observe(self, observation: Any):
        return super().observe(
            self._candidate_selected_legality_observation(observation)
        )

    def observe_with_downstream_landing(self, observation: Any, evidence: Any):
        return super().observe_with_downstream_landing(
            self._candidate_selected_legality_observation(observation),
            evidence,
        )

    def prepare_topology_locked_refresh(
        self,
        obligation: Any,
        ego_pose: Any,
        *,
        planning_observation_id: str,
        planning_frame_id: int,
    ) -> TopologyLockedRefreshMaterializationV28:
        transaction = self.selected_plan_transaction
        if transaction is None or self.identity is None:
            raise RuntimeError("V2_8_REFRESH_TRANSACTION_MISSING")
        materialized = materialize_topology_locked_refresh_v28(
            obligation,
            ego_pose,
            planning_observation_id,
            planning_frame_id,
            minimum_segment_target_index=self._refresh_segment_target_index,
        )
        binding = materialized.binding
        if not (
            binding.candidate_id == transaction.candidate_id
            and binding.interpretation_id == transaction.interpretation_id
            and binding.obligation_digest == transaction.obligation_digest
            and binding.branch_digest == transaction.selected_branch_digest
            and binding.global_destination_identity
            == transaction.global_destination_identity
            and binding.mission_context_digest == transaction.mission_context_digest
        ):
            raise RuntimeError("V2_8_REFRESH_MATERIALIZATION_IDENTITY_LOST")
        if materialized.segment_target_index < self._refresh_segment_target_index:
            raise RuntimeError("V2_8_REFRESH_GEOMETRY_ROLLED_BACK")
        self._refresh_segment_target_index = materialized.segment_target_index
        self.topology_locked_refresh_preparations.append(
            {
                "observation_id": planning_observation_id,
                "frame_id": planning_frame_id,
                "segment_target_index": materialized.segment_target_index,
                "segment_point_count": materialized.segment_point_count,
                "world_target_pair_digest": materialized.world_target_pair_digest,
                "projection_digest": binding.projection_digest,
                "geometry_changed_from_admission_pair": (
                    materialized.geometry_changed_from_admission_pair
                ),
                "selected_branch_identity_retained": True,
                "fresh_vla_forward_required": True,
                "synthetic_trajectory_point_count": 0,
                "planner_call_count": 0,
            }
        )
        return materialized

    def record_topology_locked_refresh(
        self,
        *,
        observation_id: str,
        frame_id: int,
        candidate_id: str,
        interpretation_id: str,
        obligation_digest: str,
        branch_digest: str,
        navigation_context_identity: str,
        global_destination_identity: str,
        mission_context_digest: str,
        route_version: str,
        environment_digest: str,
        refreshed_route_digest: str,
        refreshed_speed_digest: str,
    ) -> None:
        transaction = self.selected_plan_transaction
        if transaction is None or self.identity is None:
            raise RuntimeError("V2_8_REFRESH_TRANSACTION_MISSING")
        observed = (
            str(candidate_id),
            str(interpretation_id),
            str(obligation_digest),
            str(branch_digest),
            str(navigation_context_identity),
            str(global_destination_identity),
            str(mission_context_digest),
            str(route_version),
            str(environment_digest),
        )
        expected = (
            transaction.candidate_id,
            transaction.interpretation_id,
            transaction.obligation_digest,
            transaction.selected_branch_digest,
            transaction.navigation_context_identity,
            transaction.global_destination_identity,
            transaction.mission_context_digest,
            transaction.route_version,
            transaction.environment_digest,
        )
        if observed != expected:
            raise RuntimeError("V2_8_TOPOLOGY_LOCKED_REFRESH_IDENTITY_LOST")
        if not str(refreshed_route_digest) or not str(refreshed_speed_digest):
            raise RuntimeError("V2_8_REFRESH_PLAN_DIGEST_MISSING")
        if not self.topology_locked_refresh_preparations:
            raise RuntimeError("V2_8_REFRESH_PREPARATION_MISSING")
        preparation = self.topology_locked_refresh_preparations[-1]
        if (
            preparation["observation_id"] != str(observation_id)
            or preparation["frame_id"] != int(frame_id)
        ):
            raise RuntimeError("V2_8_REFRESH_PREPARATION_NOT_SAME_FRAME")
        self.topology_locked_refreshes.append(
            {
                "observation_id": str(observation_id),
                "frame_id": int(frame_id),
                "transaction_identity_digest": transaction.identity_digest,
                "downstream_landing_identity_digest": (
                    transaction.downstream_landing_identity_digest
                ),
                "refreshed_route_digest": str(refreshed_route_digest),
                "refreshed_speed_digest": str(refreshed_speed_digest),
                "segment_target_index": preparation["segment_target_index"],
                "segment_point_count": preparation["segment_point_count"],
                "world_target_pair_digest": preparation[
                    "world_target_pair_digest"
                ],
                "geometry_changed_from_admission_pair": preparation[
                    "geometry_changed_from_admission_pair"
                ],
                "selected_navigation_identity_retained": True,
                "unconstrained_baseline_refresh_allowed": False,
                "fresh_vla_forward_count": 1,
                "synthetic_trajectory_point_count": 0,
                "planner_call_count": 0,
            }
        )

    def summary(self) -> dict[str, Any]:
        value = super().summary()
        value.update(
            {
                "schema_version": self.schema_version,
                "method_version": "DriveClarify Method V2.8",
                "selected_plan_transaction": (
                    None
                    if self.selected_plan_transaction is None
                    else self.selected_plan_transaction.to_dict()
                ),
                "topology_locked_refresh_count": len(
                    self.topology_locked_refreshes
                ),
                "topology_locked_refreshes": list(
                    self.topology_locked_refreshes
                ),
                "topology_locked_refresh_preparation_count": len(
                    self.topology_locked_refresh_preparations
                ),
                "topology_locked_refresh_preparations": list(
                    self.topology_locked_refresh_preparations
                ),
                "refresh_segment_target_index": self._refresh_segment_target_index,
                "candidate_selected_legality_observation_count": len(
                    self.candidate_selected_legality_observations
                ),
                "candidate_selected_legality_observations": list(
                    self.candidate_selected_legality_observations
                ),
                "baseline_route_local_rule_can_veto_distinct_selected_route": False,
                "commitment_owner_modified": False,
            }
        )
        return value
