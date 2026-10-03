from pathlib import Path

import pytest

from driveclarify.native.contracts import (
    AskReceipt,
    AuthoritativeRoute,
    CandidateInterpretation,
    CandidateRoute,
    ConsequenceDecision,
    EgoState,
    GateInput,
    GroundingEvidence,
    ModelPlan,
    PassengerAnswer,
    PolicyAction,
    RoutePoint,
)
from driveclarify.native.oracle import DurableAskWriter
from driveclarify.native.replan import RouteTransitionManager
from driveclarify.native.supervisor import (
    ClearPassThroughSafeReplanSupervisor,
)


def current_route(route_id="native"):
    points = tuple(RoutePoint(float(x), 0.0) for x in range(41))
    return AuthoritativeRoute(
        route_id=route_id,
        points=points,
        target_point=(10.0, 0.0),
        road_option="LANEFOLLOW",
        destination_xyz=(40.0, 0.0, 0.0),
        source_frame=10,
        connector_id="connector",
        commitment_point_index=30,
    )


def gate_input(instruction, count):
    return GateInput(
        instruction=instruction,
        grounding=GroundingEvidence(
            reasonable_interpretation_count=count,
            evidence_status="VERIFIED",
            source="CURRENT_SCENE",
            observation_frame=10,
            current_frame=10,
        ),
    )


def candidate_generator(_):
    return (
        CandidateInterpretation("A", "reading A", "evidence-A"),
        CandidateInterpretation("B", "reading B", "evidence-B"),
    )


def route_binder(candidate):
    points = tuple(
        RoutePoint(float(x), 0.0 if x < 10 or x == 40 else 0.2)
        for x in range(41)
    )
    resolved = AuthoritativeRoute(
        route_id="resolved-" + candidate.candidate_id,
        points=points,
        target_point=(10.0, 0.2),
        road_option="LANEFOLLOW",
        destination_xyz=(40.0, 0.0, 0.0),
        source_frame=10,
        connector_id="connector",
        commitment_point_index=30,
    )
    return CandidateRoute(
        candidate,
        resolved,
        simlingo_preview_plan=ModelPlan(tuple((float(x), 0.0) for x in range(11))),
    )


def build_supervisor(*, action, tmp_path, answer="A", call_log=None):
    calls = call_log if call_log is not None else []

    def generated(value):
        calls.append("candidates")
        return candidate_generator(value)

    def bound(candidate):
        calls.append("route:" + candidate.candidate_id)
        return route_binder(candidate)

    def consequence(routes):
        calls.append("consequence")
        return ConsequenceDecision(
            action=action,
            selected_candidate_id="A" if action is PolicyAction.ACT else None,
            question="A or B?" if action is PolicyAction.ASK else None,
            reason_codes=("TEST_POLICY",),
        )

    writer = DurableAskWriter(tmp_path / "exchange")

    def ask(question, observation):
        calls.append("ask")
        return writer.write(question, observation)

    def answer_provider(receipt):
        calls.append("answer")
        return (
            None
            if answer is None
            else PassengerAnswer(receipt.query_id, answer)
        )

    def commit(route):
        calls.append("commit:" + route.route_id)
        return {"committed": True, "installed_route_identity": route.route_id}

    return (
        ClearPassThroughSafeReplanSupervisor(
            candidate_generator=generated,
            route_binder=bound,
            consequence_evaluator=consequence,
            ask_writer=ask,
            answer_provider=answer_provider,
            transition_manager=RouteTransitionManager(commit),
        ),
        calls,
    )


def test_clear_is_literal_zero_candidate_pass_through(tmp_path):
    supervisor, calls = build_supervisor(
        action=PolicyAction.ACT, tmp_path=tmp_path
    )
    native_input = object()
    result = supervisor.process(
        gate_input=gate_input("Continue straight.", None),
        native_input=native_input,
        ego=EgoState(5.0, 0.0, 0.0, 0.0, 4.0, 10),
        current_route=current_route(),
        observation_id="obs-1",
    )
    assert result.native_input is native_input
    assert result.pass_through is True
    assert result.authority_preserved is True
    assert calls == []
    assert result.counters == {
        "ambiguity_gate_invocations": 1,
        "candidate_interpretation_invocations": 0,
        "candidate_route_invocations": 0,
        "consequence_candidate_comparison_invocations": 0,
        "ask_receipts": 0,
        "passenger_answer_reads": 0,
        "replan_admissibility_invocations": 0,
        "route_transactions": 0,
        "a1_active_forwards": 0,
        "driveclarify_governor_active_ticks": 0,
    }


def test_unknown_preserves_authority_without_candidates(tmp_path):
    supervisor, calls = build_supervisor(
        action=PolicyAction.ACT, tmp_path=tmp_path
    )
    result = supervisor.process(
        gate_input=gate_input("Turn after the white van.", None),
        native_input=object(),
        ego=EgoState(5.0, 0.0, 0.0, 0.0, 4.0, 10),
        current_route=current_route(),
        observation_id="obs-1",
    )
    assert result.gate.decision.value == "UNKNOWN"
    assert result.authority_preserved is True
    assert calls == []


def test_ambiguous_act_commits_once_and_only_route_transaction_activates_a1(tmp_path):
    supervisor, calls = build_supervisor(
        action=PolicyAction.ACT, tmp_path=tmp_path
    )
    result = supervisor.process(
        gate_input=gate_input("Turn after the white van.", 2),
        native_input=object(),
        ego=EgoState(5.0, 0.0, 0.0, 0.0, 4.0, 10),
        current_route=current_route(),
        observation_id="obs-1",
    )
    assert result.transaction.committed is True
    assert result.transaction.a1_route_switch_active is True
    assert result.counters["candidate_interpretation_invocations"] == 1
    assert result.counters["candidate_route_invocations"] == 2
    assert result.counters["consequence_candidate_comparison_invocations"] == 1
    assert result.counters["route_transactions"] == 1
    assert result.counters["driveclarify_governor_active_ticks"] == 0
    assert calls == [
        "candidates",
        "route:A",
        "route:B",
        "consequence",
        "commit:resolved-A",
    ]


def test_ask_receipt_is_durable_before_answer_read_then_route_commit(tmp_path):
    supervisor, calls = build_supervisor(
        action=PolicyAction.ASK, tmp_path=tmp_path, answer="B"
    )
    result = supervisor.process(
        gate_input=gate_input("Turn after the white van.", 2),
        native_input=object(),
        ego=EgoState(5.0, 0.0, 0.0, 0.0, 4.0, 10),
        current_route=current_route(),
        observation_id="obs-ask",
    )
    assert calls.index("ask") < calls.index("answer") < calls.index("commit:resolved-B")
    assert len(list((tmp_path / "exchange").glob("ask-*.json"))) == 1
    assert result.selected_candidate_id == "B"
    assert result.counters["ask_receipts"] == 1
    assert result.counters["passenger_answer_reads"] == 1


def test_ask_without_answer_keeps_current_authority_and_does_not_commit(tmp_path):
    supervisor, calls = build_supervisor(
        action=PolicyAction.ASK, tmp_path=tmp_path, answer=None
    )
    result = supervisor.process(
        gate_input=gate_input("Turn after the white van.", 2),
        native_input=object(),
        ego=EgoState(5.0, 0.0, 0.0, 0.0, 4.0, 10),
        current_route=current_route(),
        observation_id="obs-ask",
    )
    assert result.authority_preserved is True
    assert result.transaction is None
    assert result.counters["route_transactions"] == 0
    assert not any(call.startswith("commit:") for call in calls)


def test_pending_ask_polls_answer_without_regenerating_candidates(tmp_path):
    answers = [None, "B"]
    calls = []

    def generated(_):
        calls.append("candidates")
        return candidate_generator(None)

    def bound(candidate):
        calls.append("route:" + candidate.candidate_id)
        return route_binder(candidate)

    writer = DurableAskWriter(tmp_path / "exchange")

    def ask(question, observation):
        calls.append("ask")
        return writer.write(question, observation)

    def answer_provider(receipt):
        calls.append("answer")
        selected = answers.pop(0)
        return None if selected is None else PassengerAnswer(receipt.query_id, selected)

    def commit(route):
        calls.append("commit:" + route.route_id)
        return {"committed": True}

    supervisor = ClearPassThroughSafeReplanSupervisor(
        candidate_generator=generated,
        route_binder=bound,
        consequence_evaluator=lambda _: ConsequenceDecision(
            PolicyAction.ASK, None, "A or B?", ("MATERIAL",)
        ),
        ask_writer=ask,
        answer_provider=answer_provider,
        transition_manager=RouteTransitionManager(commit),
    )
    arguments = dict(
        gate_input=gate_input("Turn after the white van.", 2),
        native_input=object(),
        ego=EgoState(5.0, 0.0, 0.0, 0.0, 4.0, 10),
        current_route=current_route(),
        observation_id="obs-ask",
    )
    assert supervisor.process(**arguments).transaction is None
    result = supervisor.process(**arguments)
    assert result.transaction is not None
    assert calls.count("candidates") == 1
    assert calls.count("route:A") == 1
    assert calls.count("route:B") == 1
    assert calls.count("ask") == 1
    assert calls.count("answer") == 2


def test_wait_is_semantically_ambiguous_but_never_installs_a_route(tmp_path):
    supervisor, calls = build_supervisor(
        action=PolicyAction.WAIT, tmp_path=tmp_path
    )
    result = supervisor.process(
        gate_input=gate_input("Turn after the white van.", 2),
        native_input=object(),
        ego=EgoState(5.0, 0.0, 0.0, 0.0, 4.0, 10),
        current_route=current_route(),
        observation_id="obs-wait",
    )
    assert result.policy_action is PolicyAction.WAIT
    assert result.authority_preserved is True
    assert result.counters["route_transactions"] == 0
    assert not any(call.startswith("commit:") for call in calls)
