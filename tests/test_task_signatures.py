from driveclarify.core.task_signatures import (
    AmbiguityStatus,
    ConsequenceRelation,
    GateAction,
    TaskSignature,
    compare_task_signatures,
    consequence_gate,
)


def signature(candidate, terminal="REGION-A", *, certified=True, relevant=None):
    return TaskSignature.from_mapping(
        {
            "candidate_id": candidate,
            "certified": certified,
            "certificate_id": "cert-" + candidate,
            "relevant_components": relevant or ["terminal_task_region"],
            "terminal_task_region": terminal,
        }
    )


def test_equal_certified_tasks_act_with_rank_one_candidate():
    comparison = compare_task_signatures(signature("A"), signature("B"))
    assert comparison.relation is ConsequenceRelation.TASK_EQUIVALENT
    decision = consequence_gate(
        AmbiguityStatus.AMBIGUOUS,
        comparison.relation,
        deterministic_candidate_id="A",
    )
    assert decision.action is GateAction.ACT
    assert decision.selected_candidate_id == "A"


def test_changed_task_asks_without_true_intent_operand():
    comparison = compare_task_signatures(signature("A"), signature("B", "REGION-B"))
    assert comparison.relation is ConsequenceRelation.TASK_CRITICAL
    decision = consequence_gate(
        AmbiguityStatus.AMBIGUOUS,
        comparison.relation,
        deterministic_candidate_id="A",
    )
    assert decision.action is GateAction.ASK
    assert decision.selected_candidate_id is None


def test_unknown_and_incomplete_signatures_fail_closed():
    comparison = compare_task_signatures(signature("A"), signature("B", certified=False))
    assert comparison.relation is ConsequenceRelation.UNKNOWN
    assert consequence_gate(
        AmbiguityStatus.AMBIGUOUS,
        comparison.relation,
        deterministic_candidate_id="A",
    ).action is GateAction.UNKNOWN


def test_clear_always_acts_and_ambiguity_unknown_does_not():
    assert consequence_gate(
        AmbiguityStatus.CLEAR,
        ConsequenceRelation.UNKNOWN,
        deterministic_candidate_id="A",
    ).action is GateAction.ACT
    assert consequence_gate(
        AmbiguityStatus.UNKNOWN,
        ConsequenceRelation.TASK_EQUIVALENT,
        deterministic_candidate_id="A",
    ).action is GateAction.UNKNOWN


def test_raw_waypoint_geometry_is_not_an_operand():
    first = signature("A")
    second = signature("B")
    assert not hasattr(first, "route_points")
    assert compare_task_signatures(first, second).relation is ConsequenceRelation.TASK_EQUIVALENT
