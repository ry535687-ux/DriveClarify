"""Conditional V11 supervisor: exact CLEAR bypass, AMBIGUOUS reasoning only."""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Optional, Sequence

from .ambiguity_gate import AmbiguityGate
from .contracts import (
    AskReceipt,
    AuthoritativeRoute,
    CandidateInterpretation,
    CandidateRoute,
    ConsequenceDecision,
    EgoState,
    GateDecision,
    GateInput,
    InvocationCounters,
    PassengerAnswer,
    PolicyAction,
    SupervisorResult,
    TransitionDisposition,
)
from .replan import RouteTransitionManager


class ClearPassThroughSafeReplanSupervisor:
    """Coordinate policy components while keeping SimLingo authoritative.

    ``native_input`` is returned by identity on CLEAR.  The supervisor never
    invokes SimLingo or a controller itself; the caller performs the unchanged
    native forward/PID path after this method returns.
    """

    def __init__(
        self,
        *,
        candidate_generator: Callable[[GateInput], Sequence[CandidateInterpretation]],
        route_binder: Callable[[CandidateInterpretation], CandidateRoute],
        consequence_evaluator: Callable[[Sequence[CandidateRoute]], ConsequenceDecision],
        ask_writer: Callable[[str, str], AskReceipt],
        answer_provider: Callable[[AskReceipt], Optional[PassengerAnswer]],
        transition_manager: RouteTransitionManager,
        gate: Optional[AmbiguityGate] = None,
    ):
        for callback, label in (
            (candidate_generator, "CANDIDATE_GENERATOR"),
            (route_binder, "ROUTE_BINDER"),
            (consequence_evaluator, "CONSEQUENCE_EVALUATOR"),
            (ask_writer, "ASK_WRITER"),
            (answer_provider, "ANSWER_PROVIDER"),
        ):
            if not callable(callback):
                raise TypeError(label + "_CALLABLE_REQUIRED")
        self.gate = gate or AmbiguityGate()
        self.candidate_generator = candidate_generator
        self.route_binder = route_binder
        self.consequence_evaluator = consequence_evaluator
        self.ask_writer = ask_writer
        self.answer_provider = answer_provider
        self.transition_manager = transition_manager
        self.counters = InvocationCounters()
        self._pending_ask = None
        self._pending_ask_routes = None
        self._pending_gate = None

    def process(
        self,
        *,
        gate_input: GateInput,
        native_input: Any,
        ego: EgoState,
        current_route: AuthoritativeRoute,
        observation_id: str,
    ) -> SupervisorResult:
        self.counters.ambiguity_gate_invocations += 1
        gate = self.gate.evaluate(gate_input)

        if self._pending_ask is not None:
            if gate.decision is not GateDecision.AMBIGUOUS:
                self._pending_ask = None
                self._pending_ask_routes = None
                self._pending_gate = None
            else:
                return self._poll_pending_answer(
                    gate=gate,
                    native_input=native_input,
                    ego=ego,
                    current_route=current_route,
                )

        if gate.decision is GateDecision.CLEAR:
            return self._result(
                gate=gate,
                native_input=native_input,
                pass_through=True,
                authority_preserved=True,
                status="CLEAR_NATIVE_SIMLINGO_PASSTHROUGH",
            )
        if gate.decision is GateDecision.UNKNOWN:
            return self._result(
                gate=gate,
                native_input=native_input,
                pass_through=False,
                authority_preserved=True,
                status="UNKNOWN_PRESERVE_CURRENT_NATIVE_AUTHORITY",
            )

        self.counters.candidate_interpretation_invocations += 1
        candidates = tuple(self.candidate_generator(gate_input))
        if len(candidates) < 2:
            return self._result(
                gate=gate,
                native_input=native_input,
                pass_through=False,
                authority_preserved=True,
                status="AMBIGUOUS_CANDIDATE_GENERATION_UNAVAILABLE",
            )
        identifiers = [candidate.candidate_id for candidate in candidates]
        if len(set(identifiers)) != len(identifiers):
            raise RuntimeError("CANDIDATE_IDENTITIES_NOT_UNIQUE")

        routes = []
        for candidate in candidates:
            self.counters.candidate_route_invocations += 1
            route = self.route_binder(candidate)
            if route.candidate.candidate_id != candidate.candidate_id:
                raise RuntimeError("CANDIDATE_ROUTE_BINDING_ID_MISMATCH")
            routes.append(route)

        self.counters.consequence_candidate_comparison_invocations += 1
        decision = self.consequence_evaluator(tuple(routes))
        selected_id = decision.selected_candidate_id

        if decision.action is PolicyAction.WAIT:
            return self._result(
                gate=gate,
                native_input=native_input,
                pass_through=False,
                authority_preserved=True,
                policy_action=PolicyAction.WAIT,
                status="AMBIGUOUS_WAIT_CURRENT_NATIVE_AUTHORITY",
            )
        if decision.action is PolicyAction.ASK:
            receipt = self.ask_writer(decision.question, observation_id)
            if not isinstance(receipt, AskReceipt) or not receipt.durable:
                raise RuntimeError("ANSWER_LOCKED_UNTIL_DURABLE_ASK_RECEIPT")
            self.counters.ask_receipts += 1
            self._pending_ask = receipt
            self._pending_ask_routes = tuple(routes)
            self._pending_gate = gate
            return self._poll_pending_answer(
                gate=gate,
                native_input=native_input,
                ego=ego,
                current_route=current_route,
            )

        route_by_id = {route.candidate.candidate_id: route for route in routes}
        if selected_id not in route_by_id:
            raise RuntimeError("SELECTED_CANDIDATE_NOT_IN_GENERATED_SET")
        selected = route_by_id[selected_id]
        if self._navigation_equivalent(current_route, selected.route):
            return self._result(
                gate=gate,
                native_input=native_input,
                pass_through=False,
                authority_preserved=True,
                policy_action=decision.action,
                selected_candidate_id=selected_id,
                status="AMBIGUOUS_RESOLVED_NAVIGATION_ALREADY_AUTHORITATIVE",
            )
        self.counters.replan_admissibility_invocations += 1
        assessment, transaction = self.transition_manager.propose(
            ego,
            current_route,
            selected.route,
            preview_plan=selected.simlingo_preview_plan,
        )
        if transaction is not None:
            self.counters.route_transactions += 1
        return self._result(
            gate=gate,
            native_input=native_input,
            pass_through=False,
            authority_preserved=transaction is None,
            policy_action=decision.action,
            selected_candidate_id=selected_id,
            transition=assessment,
            transaction=transaction,
            status=(
                "AMBIGUOUS_ROUTE_TRANSACTION_COMMITTED_SIMLINGO_REPLAN"
                if transaction is not None
                else "AMBIGUOUS_RESOLVED_ROUTE_NOT_COMMITTED_" + assessment.disposition.value
            ),
        )

    def _poll_pending_answer(self, *, gate, native_input, ego, current_route):
        receipt = self._pending_ask
        routes = self._pending_ask_routes
        if receipt is None or routes is None:
            raise RuntimeError("PENDING_ASK_STATE_INCOMPLETE")
        answer = self.answer_provider(receipt)
        if answer is None:
            return self._result(
                gate=gate,
                native_input=native_input,
                pass_through=False,
                authority_preserved=True,
                policy_action=PolicyAction.ASK,
                status="ASK_DURABLE_WAITING_FOR_PASSENGER_ANSWER",
            )
        if not isinstance(answer, PassengerAnswer) or answer.query_id != receipt.query_id:
            raise RuntimeError("PASSENGER_ANSWER_QUERY_BINDING_INVALID")
        self.counters.passenger_answer_reads += 1
        selected_id = answer.selected_candidate_id
        route_by_id = {route.candidate.candidate_id: route for route in routes}
        if selected_id not in route_by_id:
            raise RuntimeError("SELECTED_CANDIDATE_NOT_IN_GENERATED_SET")
        selected = route_by_id[selected_id]
        self._pending_ask = None
        self._pending_ask_routes = None
        self._pending_gate = None
        if self._navigation_equivalent(current_route, selected.route):
            return self._result(
                gate=gate,
                native_input=native_input,
                pass_through=False,
                authority_preserved=True,
                policy_action=PolicyAction.ASK,
                selected_candidate_id=selected_id,
                status="AMBIGUOUS_RESOLVED_NAVIGATION_ALREADY_AUTHORITATIVE",
            )
        self.counters.replan_admissibility_invocations += 1
        assessment, transaction = self.transition_manager.propose(
            ego,
            current_route,
            selected.route,
            preview_plan=selected.simlingo_preview_plan,
        )
        if transaction is not None:
            self.counters.route_transactions += 1
        return self._result(
            gate=gate,
            native_input=native_input,
            pass_through=False,
            authority_preserved=transaction is None,
            policy_action=PolicyAction.ASK,
            selected_candidate_id=selected_id,
            transition=assessment,
            transaction=transaction,
            status=(
                "AMBIGUOUS_ROUTE_TRANSACTION_COMMITTED_SIMLINGO_REPLAN"
                if transaction is not None
                else "AMBIGUOUS_RESOLVED_ROUTE_NOT_COMMITTED_" + assessment.disposition.value
            ),
        )

    @staticmethod
    def _navigation_equivalent(first, second):
        if len(first.points) != len(second.points):
            return False
        geometry_equal = all(
            math.sqrt(
                (float(a.x) - float(b.x)) ** 2
                + (float(a.y) - float(b.y)) ** 2
                + (float(a.z) - float(b.z)) ** 2
            )
            <= 0.05
            and a.road_option == b.road_option
            for a, b in zip(first.points, second.points)
        )
        return (
            geometry_equal
            and math.dist(first.destination_xyz, second.destination_xyz) <= 0.001
            and math.dist(first.target_point, second.target_point) <= 0.05
            and first.road_option == second.road_option
        )

    def reevaluate_pending(self, *, native_input: Any, ego: EgoState, gate_result):
        self.counters.replan_admissibility_invocations += 1
        assessment, transaction = self.transition_manager.reevaluate(ego)
        if assessment is None:
            return self._result(
                gate=gate_result,
                native_input=native_input,
                pass_through=False,
                authority_preserved=True,
                status="AMBIGUOUS_NO_DEFERRED_ROUTE_PENDING",
            )
        if transaction is not None:
            self.counters.route_transactions += 1
        if transaction is not None:
            status = "AMBIGUOUS_DEFERRED_ROUTE_TRANSACTION_COMMITTED_SIMLINGO_REPLAN"
        elif assessment.disposition is TransitionDisposition.DEFER_COMMIT:
            status = "AMBIGUOUS_DEFERRED_ROUTE_STILL_PENDING"
        else:
            status = "AMBIGUOUS_DEFERRED_ROUTE_REJECTED_" + assessment.disposition.value
        return self._result(
            gate=gate_result,
            native_input=native_input,
            pass_through=False,
            authority_preserved=transaction is None,
            transition=assessment,
            transaction=transaction,
            status=status,
        )

    def _result(
        self,
        *,
        gate,
        native_input,
        pass_through,
        authority_preserved,
        status,
        policy_action=None,
        selected_candidate_id=None,
        transition=None,
        transaction=None,
    ):
        return SupervisorResult(
            gate=gate,
            native_input=native_input,
            pass_through=pass_through,
            authority_preserved=authority_preserved,
            policy_action=policy_action,
            selected_candidate_id=selected_candidate_id,
            transition=transition,
            transaction=transaction,
            counters=self.counters.snapshot(),
            status=status,
        )


__all__ = ["ClearPassThroughSafeReplanSupervisor"]
