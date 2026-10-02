"""Topology-locked, geometry-adaptive refresh materialization for V2.8."""

from __future__ import annotations

import math
from dataclasses import dataclass

from driveclarify_candidate_local_navigation_bridge import (
    CandidateForwardNavigationBinding,
    CandidateLocalNavigationContractError,
    CandidateLocalNavigationObligation,
    EgoPose2D,
    EgoPoseWorldXY,
    NavigationPoint,
    QualificationStatus,
    canonical_navigation_digest,
    canonical_navigation_projection,
    navigation_projection_digest,
)


@dataclass(frozen=True)
class TopologyLockedRefreshMaterializationV28:
    """One fresh VLA input bound to a monotone point on one selected branch."""

    binding: CandidateForwardNavigationBinding
    segment_target_index: int
    segment_point_count: int
    world_target_pair_digest: str
    geometry_changed_from_admission_pair: bool


def _project(
    point: NavigationPoint, pose: EgoPose2D | EgoPoseWorldXY
) -> tuple[float, float]:
    delta_x = point.x_m - pose.x_m
    delta_y = point.y_m - pose.y_m
    cosine = math.cos(pose.yaw_rad)
    sine = math.sin(pose.yaw_rad)
    return (
        cosine * delta_x + sine * delta_y,
        -sine * delta_x + cosine * delta_y,
    )


def materialize_topology_locked_refresh_v28(
    obligation: CandidateLocalNavigationObligation,
    ego_pose: EgoPose2D | EgoPoseWorldXY,
    planning_observation_id: str,
    planning_frame_id: int,
    *,
    minimum_segment_target_index: int = 0,
) -> TopologyLockedRefreshMaterializationV28:
    """Advance geometry on the immutable selected branch without planning.

    The selected branch segment was already supplied and qualified before this
    call.  This function performs no map search and creates no trajectory.  It
    chooses the next discrete point on that same segment by nearest monotone
    progress, retains the sealed branch exit as the second target, and projects
    those two world points into the current ego frame for the existing SimLingo
    forward.
    """

    if obligation.qualification_status is not QualificationStatus.QUALIFIED:
        raise CandidateLocalNavigationContractError("OBLIGATION_NOT_QUALIFIED")
    if not isinstance(ego_pose, (EgoPose2D, EgoPoseWorldXY)):
        raise CandidateLocalNavigationContractError("EGO_POSE_INVALID")
    if not isinstance(planning_observation_id, str) or not planning_observation_id:
        raise CandidateLocalNavigationContractError("PLANNING_OBSERVATION_ID_INVALID")
    if type(planning_frame_id) is not int or planning_frame_id < 0:
        raise CandidateLocalNavigationContractError("PLANNING_FRAME_ID_INVALID")
    if (
        type(minimum_segment_target_index) is not int
        or minimum_segment_target_index < 0
    ):
        raise CandidateLocalNavigationContractError(
            "MINIMUM_SEGMENT_TARGET_INDEX_INVALID"
        )

    points = tuple(row.point for row in obligation.local_route_segment)
    if len(points) < 2 or obligation.local_target != points[-1]:
        raise CandidateLocalNavigationContractError(
            "SELECTED_BRANCH_SEGMENT_INVALID"
        )
    if minimum_segment_target_index > len(points) - 2:
        raise CandidateLocalNavigationContractError(
            "SEGMENT_TARGET_INDEX_AFTER_PRECOMMIT_SUPPORT"
        )

    # Nearest-point lookup is only an index into an already-qualified immutable
    # branch.  It is not a planner and introduces no metric acceptance threshold.
    search_start = max(0, minimum_segment_target_index - 1)
    nearest_index = min(
        range(search_start, len(points)),
        key=lambda index: (
            (points[index].x_m - ego_pose.x_m) ** 2
            + (points[index].y_m - ego_pose.y_m) ** 2,
            index,
        ),
    )
    segment_target_index = max(
        minimum_segment_target_index,
        min(nearest_index + 1, len(points) - 2),
    )
    world_pair = (points[segment_target_index], points[-1])
    projected = canonical_navigation_projection(
        tuple(_project(point, ego_pose) for point in world_pair)
    )
    binding = CandidateForwardNavigationBinding(
        candidate_id=obligation.candidate_id,
        interpretation_id=obligation.interpretation_id,
        planning_observation_id=planning_observation_id,
        planning_frame_id=planning_frame_id,
        obligation_identity=obligation.obligation_identity,
        obligation_digest=obligation.obligation_digest,
        branch_digest=obligation.branch_digest,
        # The target digest remains the sealed selected-target identity.  The
        # exact per-frame geometry is separately content-digested below.
        target_digest=obligation.target_digest,
        global_destination_identity=obligation.global_destination_identity,
        mission_context_digest=obligation.mission_context_digest,
        target_points_ego_local_xy_m=projected,
        projection_digest=navigation_projection_digest(projected),
    )
    return TopologyLockedRefreshMaterializationV28(
        binding=binding,
        segment_target_index=segment_target_index,
        segment_point_count=len(points),
        world_target_pair_digest=canonical_navigation_digest(
            {"selected_branch_world_target_pair": world_pair}
        ),
        geometry_changed_from_admission_pair=(
            world_pair != obligation.model_target_points_world
        ),
    )


__all__ = [
    "TopologyLockedRefreshMaterializationV28",
    "materialize_topology_locked_refresh_v28",
]
