"""Backward-compatible M2B adapter with separate diagnostic/control gates."""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence

from driveclarify_decision.decision_contracts import (
    AnswerChannelModel,
    BeliefSource,
    BeliefStatus,
    CandidateOutcomeEvidence,
    CounterfactualOutcomeCell,
    CounterfactualOutcomeMatrix,
    DecisionContext,
    HardGateEnvelope,
    IntentBelief,
    OperatingParameters,
    TaskOutcome,
    WaitMode,
    WaitOpportunityModel,
)
from driveclarify_decision.query_value_policy import OfflineQueryValuePolicy

from .consequence_v1 import CounterfactualOutcomeMatrixV1
from .runtime_contracts import (
    ContractFailure,
    ControlAuthorizationEligibility,
    DiagnosticDecisionEligibility,
    RUNTIME_SCHEMA_VERSION,
    finite_number,
    structured_fallback,
)


_DIAGNOSTIC_SAFETY_ALLOWED = {"PASS", "CLEAR", "AVAILABLE_CONTRACT_ONLY", "NOT_VERIFIED_FOR_CONTROL"}
_EXPLICIT_BLOCK = {"FAIL", "BLOCKED"}


def _failure(code: str, message: str, *, layer: str = "decision") -> ContractFailure:
    return ContractFailure(layer, "STRUCTURED_CONTRACT_FAILURE", (code,), message)


def diagnostic_eligibility(record: Mapping[str, Any]) -> DiagnosticDecisionEligibility:
    gate = record.get("hard_gate_envelope", {})
    if not isinstance(gate, Mapping):
        return DiagnosticDecisionEligibility(False, "STRUCTURED_CONTRACT_FAILURE", False, ("HARD_GATE_ENVELOPE_INVALID",))
    safety = str(gate.get("hard_safety_status", ""))
    rule = str(gate.get("hard_rule_status", ""))
    evidence = str(gate.get("evidence_gate_status", ""))
    cache = str(gate.get("cache_status", ""))
    freshness = str(gate.get("candidate_freshness_status", ""))
    candidate_set = str(gate.get("candidate_set_status", "COMPLETE"))
    reasons: list[str] = []
    if safety in _EXPLICIT_BLOCK:
        reasons.append("EXPLICIT_HARD_SAFETY_BLOCK")
    elif safety not in _DIAGNOSTIC_SAFETY_ALLOWED:
        reasons.append("HARD_SAFETY_STATUS_NOT_DIAGNOSTIC_ELIGIBLE")
    if rule in _EXPLICIT_BLOCK:
        reasons.append("EXPLICIT_HARD_RULE_BLOCK")
    if evidence != "PASS":
        reasons.append("EVIDENCE_GATE_NOT_PASSED")
    if cache != "FRESH":
        reasons.append("CACHE_STALE")
    if freshness != "FRESH":
        reasons.append("CANDIDATE_STALE")
    if candidate_set != "COMPLETE":
        reasons.append("CANDIDATE_SET_INVALID")
    if gate.get("control_authorized") is True:
        reasons.append("INPUT_CONTROL_AUTHORIZATION_FORBIDDEN")
    physical_verified = safety in {"PASS", "CLEAR"}
    if not physical_verified:
        reasons.append("PHYSICAL_CONTROL_NOT_VERIFIED")
    blocking = [item for item in reasons if item != "PHYSICAL_CONTROL_NOT_VERIFIED"]
    return DiagnosticDecisionEligibility(
        not blocking,
        "DIAGNOSTIC_ELIGIBLE" if not blocking else "DIAGNOSTIC_NOT_ELIGIBLE",
        physical_verified,
        tuple(sorted(set(reasons or ("DIAGNOSTIC_CONTRACT_VALID",)))),
    )


def control_eligibility() -> ControlAuthorizationEligibility:
    return ControlAuthorizationEligibility()


def _belief(record: Mapping[str, Any], ids: tuple[str, ...]) -> tuple[IntentBelief | None, list[ContractFailure], list[str]]:
    raw = record.get("intent_belief")
    if not isinstance(raw, Mapping):
        return None, [_failure("INTENT_BELIEF_MISSING", "Intent belief is absent.")], []
    status = str(raw.get("belief_status", ""))
    if status not in {"AVAILABLE", "AVAILABLE_NORMALIZED"}:
        return None, [_failure("INVALID_BELIEF_STATUS", "Belief status is not available and normalized.")], []
    values = raw.get("candidate_probabilities")
    if not isinstance(values, list):
        return None, [_failure("BELIEF_CANDIDATE_PROBABILITIES_MISSING", "Candidate probabilities are absent.")], []
    parsed: list[tuple[str, float]] = []
    try:
        for item in values:
            if not isinstance(item, Mapping):
                raise ValueError("BELIEF_CANDIDATE_PROBABILITY_INVALID")
            parsed.append((str(item.get("candidate_id", "")), finite_number(item.get("probability"), "BELIEF_INVALID_PROBABILITY", minimum=0.0, maximum=1.0)))
    except (TypeError, ValueError) as exc:
        return None, [_failure(str(exc), "Intent belief probability is invalid.")], []
    parsed_ids = tuple(item[0] for item in parsed)
    if set(parsed_ids) != set(ids) or len(parsed_ids) != len(ids):
        return None, [_failure("BELIEF_CANDIDATE_SET_MISMATCH", "Belief candidate set does not match runtime candidates.")], []
    if parsed_ids != ids:
        return None, [_failure("INTENT_BELIEF_CANDIDATE_ORDER_MISMATCH", "Belief order is contractual and does not match.")], []
    if not math.isclose(sum(item[1] for item in parsed), 1.0, abs_tol=1e-9):
        return None, [_failure("BELIEF_NOT_NORMALIZED", "Belief probabilities do not sum to one.")], []
    source_text = str(raw.get("belief_source", "NOT_AVAILABLE"))
    try:
        source = BeliefSource(source_text)
    except ValueError:
        return None, [_failure("BELIEF_SOURCE_INVALID", "Belief source is not supported.")], []
    if source is BeliefSource.DECLARED_UNINFORMATIVE:
        uniform = 1.0 / len(parsed)
        if any(not math.isclose(value, uniform, abs_tol=1e-9) for _, value in parsed):
            return None, [_failure("DECLARED_UNINFORMATIVE_BELIEF_NOT_UNIFORM", "Declared uninformative belief is not uniform.")], []
    belief = IntentBelief(
        tuple(parsed),
        BeliefStatus.AVAILABLE,
        source,
        str(raw.get("normalization_status", "NORMALIZED")),
        tuple(str(item) for item in raw.get("provenance", ("V1_BELIEF_ADAPTER",))),
        ("V1_BELIEF_VALIDATED",),
    )
    return belief, [], []


def _channel(record: Mapping[str, Any], ids: tuple[str, ...]) -> tuple[AnswerChannelModel | None, list[ContractFailure], list[str]]:
    raw = record.get("question_channel_contract")
    if not isinstance(raw, Mapping):
        return None, [_failure("ANSWER_CHANNEL_MISSING", "Answer channel is absent.")], []
    if str(raw.get("channel_status", "")) != "VALID":
        return None, [_failure("ANSWER_CHANNEL_STATUS_INVALID", "Answer channel status is invalid.")], []
    rows_raw = raw.get("answer_confusion_matrix")
    if not isinstance(rows_raw, list):
        return None, [_failure("ANSWER_CHANNEL_CONFUSION_MATRIX_MISSING", "Answer confusion matrix is absent.")], []
    row_map: dict[str, Mapping[str, Any]] = {}
    for row in rows_raw:
        if not isinstance(row, Mapping):
            return None, [_failure("ANSWER_CHANNEL_ROW_INVALID", "Answer channel row is invalid.")], []
        candidate_id = str(row.get("hypothesis_candidate_id", ""))
        if not candidate_id or candidate_id in row_map:
            return None, [_failure("ANSWER_CHANNEL_ROW_ID_INVALID", "Answer channel row identity is invalid.")], []
        row_map[candidate_id] = row
    if set(row_map) != set(ids):
        return None, [_failure("ANSWER_CHANNEL_CANDIDATE_SET_MISMATCH", "Answer channel does not cover the candidate set.")], []
    traces = ["ANSWER_CHANNEL_ROWS_CANONICALIZED_BY_CANDIDATE_ID"] if tuple(row_map) != ids else []
    parsed_rows: list[tuple[str, tuple[tuple[str, float], ...]]] = []
    label_set: set[str] | None = None
    try:
        for candidate_id in ids:
            probabilities = row_map[candidate_id].get("answer_probabilities")
            if not isinstance(probabilities, list) or not probabilities:
                raise ValueError("ANSWER_CHANNEL_ROW_MISSING")
            values: dict[str, float] = {}
            for item in probabilities:
                if not isinstance(item, Mapping):
                    raise ValueError("ANSWER_CHANNEL_INVALID_PROBABILITY")
                label = str(item.get("answer_label", "")).upper()
                if not label or label in values:
                    raise ValueError("ANSWER_CHANNEL_LABEL_INVALID")
                values[label] = finite_number(item.get("probability"), "ANSWER_CHANNEL_INVALID_PROBABILITY", minimum=0.0, maximum=1.0)
            if not math.isclose(sum(values.values()), 1.0, abs_tol=1e-9):
                raise ValueError("ANSWER_CHANNEL_ROW_NOT_NORMALIZED")
            if label_set is None:
                label_set = set(values)
            elif set(values) != label_set:
                raise ValueError("ANSWER_CHANNEL_LABEL_SET_MISMATCH")
            parsed_rows.append((candidate_id, tuple((label, values[label]) for label in sorted(values))))
        delays_raw = raw.get("delay_distribution")
        if not isinstance(delays_raw, list) or not delays_raw:
            raise ValueError("ANSWER_DELAY_DISTRIBUTION_MISSING")
        delays = tuple(
            (
                finite_number(item.get("seconds"), "ANSWER_DELAY_INVALID", minimum=0.0),
                finite_number(item.get("probability"), "ANSWER_DELAY_INVALID_PROBABILITY", minimum=0.0, maximum=1.0),
            )
            for item in delays_raw
            if isinstance(item, Mapping)
        )
        if len(delays) != len(delays_raw) or not math.isclose(sum(item[1] for item in delays), 1.0, abs_tol=1e-9):
            raise ValueError("ANSWER_DELAY_DISTRIBUTION_NOT_NORMALIZED")
        resolution = finite_number(raw.get("answer_resolution_probability"), "ANSWER_RESOLUTION_PROBABILITY_INVALID", minimum=0.0, maximum=1.0)
        no_answer = finite_number(raw.get("no_answer_probability"), "NO_ANSWER_PROBABILITY_INVALID", minimum=0.0, maximum=1.0)
    except (TypeError, ValueError) as exc:
        return None, [_failure(str(exc), "Answer channel failed validation.")], traces
    return AnswerChannelModel(
        resolution,
        no_answer,
        tuple(parsed_rows),
        delays,
        "VALID",
        str(raw.get("source", "V1_DECLARED_ANSWER_CHANNEL")),
        tuple(str(item) for item in raw.get("provenance", ("V1_CHANNEL_ADAPTER",))),
        tuple((*traces, "V1_ANSWER_CHANNEL_VALIDATED")),
    ), [], traces


def _proposal(record: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = record.get("question_channel_contract", {})
    if not isinstance(raw, Mapping):
        return {}
    partition: list[tuple[str, tuple[str, ...]]] = []
    for item in raw.get("candidate_partition", []) if isinstance(raw.get("candidate_partition"), list) else []:
        if isinstance(item, Mapping):
            partition.append((str(item.get("answer_label", "")).upper(), tuple(str(value) for value in item.get("candidate_ids", []))))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            partition.append((str(item[0]).upper(), tuple(str(value) for value in item[1])))
    return {
        "proposal_status": str(raw.get("question_status", "QUESTION_NOT_REALIZABLE")),
        "query_id": raw.get("query_id"),
        "target_slot": raw.get("target_slot"),
        "question_text": raw.get("question_text"),
        "candidate_partition": partition,
        "option_descriptions": list(raw.get("option_descriptions", [])),
    }


def _context(
    record: Mapping[str, Any],
    matrix: CounterfactualOutcomeMatrixV1,
) -> tuple[DecisionContext | None, tuple[ContractFailure, ...], tuple[str, ...]]:
    ids = matrix.candidate_ids
    belief, belief_failures, belief_traces = _belief(record, ids)
    channel, channel_failures, channel_traces = _channel(record, ids)
    failures = [*belief_failures, *channel_failures]
    if failures or belief is None or channel is None:
        return None, tuple(failures), tuple((*belief_traces, *channel_traces))
    raw_wait = record.get("wait_opportunity")
    raw_gate = record.get("hard_gate_envelope")
    raw_cost = record.get("declared_cost_parameters")
    if not isinstance(raw_wait, Mapping):
        failures.append(_failure("WAIT_OPPORTUNITY_MISSING", "Wait opportunity is absent."))
    if not isinstance(raw_gate, Mapping):
        failures.append(_failure("HARD_GATE_ENVELOPE_INVALID", "Hard gate envelope is absent."))
    if not isinstance(raw_cost, Mapping):
        failures.append(_failure("OPERATING_PARAMETERS_MISSING", "Operating parameters are absent."))
    if failures:
        return None, tuple(failures), tuple((*belief_traces, *channel_traces))
    assert isinstance(raw_wait, Mapping) and isinstance(raw_gate, Mapping) and isinstance(raw_cost, Mapping)
    try:
        wait_mode = WaitMode(str(raw_wait.get("wait_mode", "NOT_AVAILABLE")))
        wait = WaitOpportunityModel(
            wait_mode,
            bool(raw_wait.get("future_information_expected", False)),
            None if raw_wait.get("expected_information_arrival_monotonic") is None else finite_number(raw_wait.get("expected_information_arrival_monotonic"), "WAIT_INFORMATION_ARRIVAL_INVALID"),
            bool(raw_wait.get("active_query_pending", False)),
            str(raw_wait.get("holding_capability_status", "NOT_AVAILABLE")),
            str(raw_wait.get("decision_window_status", "UNKNOWN")),
            finite_number(raw_wait.get("missed_opportunity_cost", 0.0), "WAIT_MISSED_OPPORTUNITY_COST_INVALID", minimum=0.0),
            finite_number(raw_wait.get("wait_cost", 0.0), "WAIT_COST_INVALID", minimum=0.0),
            finite_number(raw_wait.get("information_resolution_probability", 0.0), "WAIT_RESOLUTION_PROBABILITY_INVALID", minimum=0.0, maximum=1.0),
            tuple(str(item) for item in raw_wait.get("provenance", ("V1_WAIT_ADAPTER",))),
            ("V1_WAIT_VALIDATED",),
        )
        if record.get("answer_deadline_monotonic") is None:
            raise ValueError("ANSWER_DEADLINE_MISSING")
        deadline = finite_number(record.get("answer_deadline_monotonic"), "ANSWER_DEADLINE_INVALID")
        now = finite_number(record.get("monotonic_now"), "MONOTONIC_NOW_INVALID")
        operating = OperatingParameters(
            finite_number(raw_cost.get("query_cost"), "QUERY_COST_INVALID", minimum=0.0),
            finite_number(raw_cost.get("delay_cost_per_second"), "DELAY_COST_INVALID", minimum=0.0),
            finite_number(raw_cost.get("no_answer_penalty"), "NO_ANSWER_PENALTY_INVALID", minimum=0.0),
            str(raw_cost.get("unknown_task_loss_policy", "")),
            finite_number(raw_cost.get("strict_value_epsilon", 1e-9), "STRICT_VALUE_EPSILON_INVALID", minimum=0.0),
            ("V1_OPERATING_PARAMETERS_ADAPTER",),
        )
    except (TypeError, ValueError) as exc:
        return None, (_failure(str(exc), "Decision timing, wait, or cost contract is invalid."),), tuple((*belief_traces, *channel_traces))
    active_raw = record.get("active_query_state", {})
    active_id = None
    if isinstance(active_raw, Mapping) and str(active_raw.get("status")) == "ACTIVE":
        active_id = active_raw.get("query_id")
        if not active_id:
            return None, (_failure("ACTIVE_QUERY_ID_MISSING", "Active query state has no query ID."),), tuple((*belief_traces, *channel_traces))
    gate_active = str(raw_gate.get("query_episode_status", "")) == "ACTIVE_QUERY_PENDING"
    if (active_id is not None) != gate_active:
        return None, (_failure("ACTIVE_QUERY_CONFLICT", "Active query state conflicts with hard gate envelope."),), tuple((*belief_traces, *channel_traces))
    proposal_value = _proposal(record)
    if active_id is not None and proposal_value.get("query_id") not in {None, active_id}:
        return None, (_failure("ACTIVE_QUERY_CONFLICT", "Active query ID conflicts with the declared question."),), tuple((*belief_traces, *channel_traces))
    v0_cells = tuple(
        CounterfactualOutcomeCell(
            item.action_candidate_id,
            item.hypothesis_candidate_id,
            TaskOutcome(item.task_outcome),
            item.evidence_status,
            item.task_error_cost,
            item.wrong_goal_indicator,
            item.provenance,
            item.reason_codes,
        )
        for item in matrix.cells
    )
    v0_matrix = CounterfactualOutcomeMatrix(ids, v0_cells, matrix.matrix_status, matrix.provenance, matrix.reason_codes)
    outcomes = tuple(
        CandidateOutcomeEvidence(
            candidate_id,
            v0_matrix.cell(candidate_id, candidate_id).task_outcome if v0_matrix.cell(candidate_id, candidate_id) else TaskOutcome.UNKNOWN,
            v0_matrix.cell(candidate_id, candidate_id).evidence_status if v0_matrix.cell(candidate_id, candidate_id) else "INSUFFICIENT",
            ("V1_MATRIX_DIAGONAL",),
            ("CANDIDATE_SPECIFIC_DIAGONAL",),
        )
        for candidate_id in ids
    )
    ambiguity = record.get("ambiguity_metadata_without_gold", {})
    unknown_class = str(ambiguity.get("unknown_cause_class", "NONE")) if isinstance(ambiguity, Mapping) else "NONE"
    unknown_cause = str(ambiguity.get("unknown_cause", "NONE")) if isinstance(ambiguity, Mapping) else "NONE"
    matrix_raw = record.get("counterfactual_runtime_inputs", {})
    matrix_cause = str(matrix_raw.get("unknown_cause", "NONE")) if isinstance(matrix_raw, Mapping) else "NONE"
    ambiguity_type = str(ambiguity.get("ambiguity_type", "UNSUPPORTED")) if isinstance(ambiguity, Mapping) else "UNSUPPORTED"
    safety = str(raw_gate.get("hard_safety_status", ""))
    diagnostic_safety = "PASS" if safety in _DIAGNOSTIC_SAFETY_ALLOWED else safety
    rule = str(raw_gate.get("hard_rule_status", ""))
    diagnostic_rule = "PASS" if rule not in _EXPLICIT_BLOCK else rule
    try:
        context = DecisionContext(
            decision_id=str(record.get("runtime_record_id", record.get("challenge_id", "runtime-decision"))),
            episode_id=str(record.get("group_id", record.get("episode_id", "runtime-episode"))),
            source_observation_id=next((str(item.get("source_observation_id")) for item in record.get("candidate_plan_records", []) if isinstance(item, Mapping) and item.get("source_observation_id") is not None), None),
            candidate_ids=ids,
            candidate_pair_relation=str(matrix_raw.get("pair_relation", "UNKNOWN")) if isinstance(matrix_raw, Mapping) else "UNKNOWN",
            candidate_outcomes=outcomes,
            counterfactual_outcome_matrix=v0_matrix,
            intent_belief=belief,
            query_proposal=proposal_value,
            answer_channel=channel,
            query_budget=int(record.get("query_budget", 0)),
            active_query_id=None if active_id is None else str(active_id),
            monotonic_now=now,
            answer_deadline_monotonic=deadline,
            time_to_decision_status=str(raw_wait.get("decision_window_status", "UNKNOWN")),
            wait_opportunity=wait,
            hard_gate_envelope=HardGateEnvelope(
                diagnostic_safety,
                diagnostic_rule,
                str(raw_gate.get("evidence_gate_status", "")),
                str(raw_gate.get("query_episode_status", "")),
                str(raw_gate.get("cache_status", "")),
                str(raw_gate.get("candidate_freshness_status", "")),
                False,
                tuple((*[str(item) for item in raw_gate.get("reason_codes", ())], "V1_DIAGNOSTIC_GATE_ADAPTER")),
            ),
            evidence_masks=tuple(sorted({
                "ambiguity_present": ambiguity_type not in {"UNAMBIGUOUS", "UNSUPPORTED"},
                "passenger_resolvable_unknown": unknown_class == "PASSENGER_RESOLVABLE",
                "unknown_causes": tuple(sorted({unknown_cause, matrix_cause} - {"NONE"})),
            }.items())),
            operating_parameters=operating,
            provenance=("M2B_BACKWARD_COMPATIBLE_V1_ADAPTER", "DIAGNOSTIC_ONLY"),
        )
    except (TypeError, ValueError) as exc:
        return None, (_failure(str(exc), "M2B context construction failed closed."),), tuple((*belief_traces, *channel_traces))
    return context, (), tuple((*belief_traces, *channel_traces))


class DecisionRuntimeV1:
    def recommend(self, record: Mapping[str, Any], matrix: CounterfactualOutcomeMatrixV1 | None, upstream_failures: Sequence[ContractFailure] = ()) -> dict[str, Any]:
        original = copy.deepcopy(record)
        control = control_eligibility()
        gate = diagnostic_eligibility(record)
        failures = list(upstream_failures)
        if matrix is None:
            failures.append(_failure("COUNTERFACTUAL_MATRIX_MISSING", "No decision matrix is available."))
        if failures:
            return {**structured_fallback(failures, gate=gate), "adapter_trace": [], "control_gate": control.to_dict(), "contract_failures": [item.to_dict() for item in failures]}
        if not gate.eligible:
            blocked = ContractFailure("decision", "DIAGNOSTIC_GATE_BLOCKED", tuple(gate.reason_codes), "Diagnostic gate is blocked.")
            return {**structured_fallback((blocked,), gate=gate), "adapter_trace": [], "control_gate": control.to_dict(), "contract_failures": [blocked.to_dict()]}
        assert matrix is not None
        if not matrix.complete_and_known:
            failure = _failure("COUNTERFACTUAL_MATRIX_INCOMPLETE", "Decision requires a complete known task-loss matrix.")
            return {**structured_fallback((failure,), gate=gate), "adapter_trace": [], "control_gate": control.to_dict(), "contract_failures": [failure.to_dict()]}
        context, context_failures, traces = _context(record, matrix)
        if context_failures or context is None:
            return {**structured_fallback(context_failures, gate=gate), "adapter_trace": list(traces), "control_gate": control.to_dict(), "contract_failures": [item.to_dict() for item in context_failures]}
        try:
            recommendation = OfflineQueryValuePolicy().recommend(context).to_dict()
        except Exception as exc:
            failure = _failure(str(exc) or type(exc).__name__, "M2B policy rejected the validated adapter context.")
            return {**structured_fallback((failure,), gate=gate), "adapter_trace": list(traces), "control_gate": control.to_dict(), "contract_failures": [failure.to_dict()]}
        if record != original:
            failure = _failure("RUNTIME_INPUT_MUTATED", "Decision adapter mutated its input.")
            return {**structured_fallback((failure,), gate=gate), "adapter_trace": list(traces), "control_gate": control.to_dict(), "contract_failures": [failure.to_dict()]}
        return {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            **recommendation,
            "diagnostic_gate": gate.to_dict(),
            "control_gate": control.to_dict(),
            "adapter_trace": list(traces),
            "contract_failures": [],
            "authorization_eligible": False,
            "control_authorized": False,
            "used_for_control": False,
            "vehicle_control_generated": False,
            "steering": None,
            "throttle": None,
            "brake": None,
        }
