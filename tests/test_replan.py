import math

import pytest

from driveclarify.native.contracts import (
    AuthoritativeRoute,
    EgoState,
    ModelPlan,
    RoutePoint,
    TransitionDisposition,
)
from driveclarify.native.replan import (
    ReplanAdmissibility,
    RouteTransitionManager,
)


def route(route_id, *, y=0.0, source_frame=100, destination=(40.0, 0.0, 0.0), commitment=20):
    points = tuple(
        RoutePoint(float(x), float(y), 0.0, "LANEFOLLOW" if x < 20 else "RIGHT")
        for x in range(0, 41)
    )
    return AuthoritativeRoute(
        route_id=route_id,
        points=points,
        target_point=(10.0, float(y)),
        road_option="LANEFOLLOW",
        destination_xyz=destination,
        source_frame=source_frame,
        connector_id="connector-1",
        commitment_point_index=commitment,
    )


def ego(*, x=5.0, y=0.0, yaw=0.0, speed=4.0, frame=100):
    return EgoState(x=x, y=y, z=0.0, yaw_degrees=yaw, speed_mps=speed, frame=frame)


def valid_plan():
    return ModelPlan(tuple((float(x), 0.0) for x in range(0, 11)))


def test_admissible_route_commits_exactly_once_and_activates_a1():
    commits = []

    def commit(value):
        commits.append(value.route_id)
        return {"committed": True, "installed_route_identity": value.route_id}

    manager = RouteTransitionManager(commit)
    assessment, receipt = manager.propose(
        ego(), route("native"), route("resolved"), preview_plan=valid_plan()
    )
    assert assessment.disposition is TransitionDisposition.COMMIT_NOW
    assert receipt.committed is True
    assert receipt.a1_route_switch_active is True
    assert receipt.transaction_count == 1
    assert commits == ["resolved"]
    with pytest.raises(RuntimeError, match="ALREADY_COMMITTED"):
        manager.propose(ego(), route("native"), route("resolved"))


def test_join_distance_defers_without_commit_or_emergency_action():
    manager = RouteTransitionManager(lambda _: pytest.fail("must not commit"))
    assessment, receipt = manager.propose(
        ego(y=0.0), route("native"), route("resolved", y=5.0), preview_plan=valid_plan()
    )
    assert assessment.disposition is TransitionDisposition.DEFER_COMMIT
    assert receipt is None
    assert manager.pending_route_id == "resolved"
    assert "EGO_TO_RESOLVED_ROUTE_JOIN_TOO_FAR" in assessment.reason_codes


def test_deferred_route_is_reevaluated_and_committed_when_ego_reaches_join():
    commits = []
    manager = RouteTransitionManager(
        lambda value: commits.append(value) or {"committed": True}
    )
    assessment, receipt = manager.propose(
        ego(y=0.0), route("native"), route("resolved", y=5.0), preview_plan=valid_plan()
    )
    assert receipt is None
    assessment, receipt = manager.reevaluate(ego(y=5.0))
    assert assessment.disposition is TransitionDisposition.COMMIT_NOW
    assert receipt is not None
    assert [value.route_id for value in commits] == ["resolved"]
    assert commits[0].points[0].x == 4.0
    assert commits[0].target_point == (6.0, 5.0)
    assert commits[0].destination_xyz == (40.0, 0.0, 0.0)


def test_deferred_reconnect_preserves_exact_destination_and_one_transaction():
    installed = []
    manager = RouteTransitionManager(
        lambda value: installed.append(value) or {"committed": True}
    )
    current = route("native")
    resolved = route("resolved", y=5.0)
    first, receipt = manager.propose(ego(x=1.0), current, resolved)
    assert first.disposition is TransitionDisposition.DEFER_COMMIT
    final, receipt = manager.reevaluate(ego(x=18.0, y=5.0, frame=110))
    assert final.disposition is TransitionDisposition.COMMIT_NOW
    assert receipt.transaction_count == 1
    assert manager.transaction_count == 1
    assert installed[0].points[0].x == 17.0
    assert installed[0].points[-1] == resolved.points[-1]
    assert installed[0].destination_xyz == resolved.destination_xyz


def test_deferred_route_can_become_stale_and_is_rejected_without_commit():
    commits = []
    manager = RouteTransitionManager(
        lambda value: commits.append(value.route_id) or {"committed": True}
    )
    assessment, receipt = manager.propose(
        ego(y=0.0), route("native"), route("resolved", y=5.0), preview_plan=valid_plan()
    )
    assert assessment.disposition is TransitionDisposition.DEFER_COMMIT
    assert receipt is None
    assessment, receipt = manager.reevaluate(ego(y=0.0, frame=300))
    assert assessment.disposition is TransitionDisposition.REJECT_STALE_OR_INFEASIBLE
    assert receipt is None
    assert manager.pending_route_id is None
    assert commits == []


def test_stale_or_changed_destination_is_rejected():
    checker = ReplanAdmissibility()
    stale = checker.assess(
        ego(frame=300), route("native"), route("resolved", source_frame=100)
    )
    assert stale.disposition is TransitionDisposition.REJECT_STALE_OR_INFEASIBLE
    assert "RESOLVED_ROUTE_STALE" in stale.reason_codes
    changed = checker.assess(
        ego(),
        route("native"),
        route("resolved", destination=(41.0, 0.0, 0.0)),
    )
    assert changed.disposition is TransitionDisposition.REJECT_STALE_OR_INFEASIBLE
    assert "SAME_DESTINATION_CONTRACT_FAILED" in changed.reason_codes


def test_missed_commitment_is_recorded_instead_of_forcing_turn():
    result = ReplanAdmissibility().assess(
        ego(x=30.0), route("native"), route("resolved", commitment=20)
    )
    assert result.disposition is TransitionDisposition.REJECT_STALE_OR_INFEASIBLE
    assert result.missed_replan_opportunity is True
    assert "MISSED_REPLAN_OPPORTUNITY" in result.reason_codes


def test_invalid_simlingo_preview_defers_and_never_synthesizes_a_plan():
    result = ReplanAdmissibility().assess(
        ego(),
        route("native"),
        route("resolved"),
        preview_plan=ModelPlan(((50.0, 50.0), (60.0, 60.0), (70.0, 70.0))),
    )
    assert result.disposition is TransitionDisposition.DEFER_COMMIT
    assert "PLAN_GROSSLY_DISCONTINUOUS_WITH_EGO" in result.reason_codes
