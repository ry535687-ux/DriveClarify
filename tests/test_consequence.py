from driveclarify.native.route_consequences import (
    MaterialRouteConsequenceEvaluator,
)
from driveclarify.native.contracts import (
    AuthoritativeRoute,
    CandidateInterpretation,
    CandidateRoute,
    PolicyAction,
    RoutePoint,
)


def candidate(identifier, *, offset=0.0, connector="same", option="LANEFOLLOW"):
    interpretation = CandidateInterpretation(identifier, identifier, "evidence-" + identifier)
    route = AuthoritativeRoute(
        route_id="route-" + identifier,
        points=tuple(RoutePoint(float(x), offset, 0.0, option) for x in range(20)),
        target_point=(10.0, offset),
        road_option=option,
        destination_xyz=(19.0, 0.0, 0.0),
        source_frame=1,
        connector_id=connector,
    )
    return CandidateRoute(interpretation, route)


def test_semantic_ambiguity_with_equivalent_consequence_acts_without_asking():
    result = MaterialRouteConsequenceEvaluator().evaluate(
        (candidate("A"), candidate("B", offset=0.2))
    )
    assert result.action is PolicyAction.ACT
    assert result.selected_candidate_id == "A"


def test_material_geometry_or_connector_difference_asks_without_selecting_intent():
    evaluator = MaterialRouteConsequenceEvaluator()
    geometry = evaluator.evaluate((candidate("A"), candidate("B", offset=3.0)))
    connector = evaluator.evaluate(
        (candidate("A", connector="left"), candidate("B", connector="right"))
    )
    for result in (geometry, connector):
        assert result.action is PolicyAction.ASK
        assert result.selected_candidate_id is None


def test_unavailable_candidate_route_set_waits_without_selecting_intent():
    result = MaterialRouteConsequenceEvaluator().evaluate(())
    assert result.action is PolicyAction.WAIT
    assert result.selected_candidate_id is None
    assert result.reason_codes == ("INSUFFICIENT_CANDIDATE_ROUTES",)
