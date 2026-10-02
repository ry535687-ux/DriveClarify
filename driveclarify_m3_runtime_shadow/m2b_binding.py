"""Bind shadow candidate outputs to the existing M2B runtime policy."""

from __future__ import annotations

import copy
import hashlib
import sys
import time
from dataclasses import dataclass
from typing import Any, Mapping

from driveclarify_decision.decision_contracts import (
    SCHEMA_VERSION as EXISTING_M2B_VERSION,
    stable_sha256,
)
from driveclarify_m2b_formal_offline.integration import run_policy_cases
from driveclarify_m3_offline_replay.serialization import canonical_sha256

from .contracts import ShadowCandidateResult, ShadowObservationSnapshot
from .counterfactual_evidence import (
    SHADOW_ROUTE_PROVENANCE,
    ShadowCounterfactualEvidenceResult,
    ShadowCounterfactualMappingContext,
    bind_candidate_to_counterfactual_evidence,
    build_longitudinal_task_outcomes_v0,
)
from .runtime_authority_evidence_v1 import (
    RuntimeDecisionAuthorityEvidenceV1,
    produce_runtime_decision_authority_evidence_v1,
)

M2B_INPUT_INCOMPLETE = "M2B_INPUT_INCOMPLETE"
BLOCKED_EXISTING_M2B_BINDING_SIGNATURE_UNRESOLVED = "BLOCKED_EXISTING_M2B_BINDING_SIGNATURE_UNRESOLVED"
BLOCKED_EXISTING_M2B_BINDING_REQUIRES_MODEL_EXECUTION = "BLOCKED_EXISTING_M2B_BINDING_REQUIRES_MODEL_EXECUTION"
BLOCKED_M2B_PRODUCER_ACTION_UNSUPPORTED = "BLOCKED_M2B_PRODUCER_ACTION_UNSUPPORTED"

EXISTING_M2B_COMPONENT = "query_value_decision"


def _snapshot_case_id(snapshot: ShadowObservationSnapshot, candidate_a: ShadowCandidateResult, candidate_b: ShadowCandidateResult) -> str:
    digest = hashlib.sha256(
        f"{snapshot.observation_id}|{snapshot.source_digest}|{candidate_a.candidate_id}|{candidate_b.candidate_id}".encode(
            "utf-8",
        ),
    ).hexdigest()[:20]
    return f"DECISION-SHADOW-M2B-{digest}"


def _require_nonempty(value: Any) -> bool:
    return bool(isinstance(value, str) and value)


def _validate_producer_action(decision: str) -> str:
    """Preserve the existing M2B producer action without translating it for M3."""

    if decision in {"ACT", "ASK", "WAIT", "FALLBACK_RECOMMENDED"}:
        return decision
    raise RuntimeError(BLOCKED_M2B_PRODUCER_ACTION_UNSUPPORTED)


def _ambient_model_runtime_state() -> Mapping[str, bool]:
    """Observe pre-existing runtime state without importing or initializing it."""

    torch_module = sys.modules.get("torch")
    cuda = getattr(torch_module, "cuda", None) if torch_module is not None else None
    is_initialized = getattr(cuda, "is_initialized", None)
    cuda_initialized = False
    if callable(is_initialized):
        try:
            cuda_initialized = bool(is_initialized())
        except Exception:
            cuda_initialized = False
    return {
        "torch_loaded": torch_module is not None,
        "cuda_initialized": cuda_initialized,
    }


def _ensure_no_model_execution(
    output: Mapping[str, Any],
    *,
    before: Mapping[str, bool],
    after: Mapping[str, bool],
) -> None:
    # ``run_runtime_record`` historically reports whether torch is present in
    # the *process*, not whether this call loaded it.  In a live SimLingo
    # process torch/CUDA are necessarily pre-existing.  Preserve the frozen
    # no-model rule by rejecting state transitions and every explicit execution
    # counter, while allowing an unchanged ambient runtime.
    if dict(before) != dict(after):
        raise RuntimeError(BLOCKED_EXISTING_M2B_BINDING_REQUIRES_MODEL_EXECUTION)
    if output.get("cuda_initialized") is not False:
        raise RuntimeError(BLOCKED_EXISTING_M2B_BINDING_REQUIRES_MODEL_EXECUTION)
    for field in (
        "vehicle_control_generated",
        "live_wait_controller_invoked",
        "m3_invoked",
    ):
        if bool(output.get(field, False)):
            raise RuntimeError(BLOCKED_EXISTING_M2B_BINDING_REQUIRES_MODEL_EXECUTION)
    for field in (
        "simlingo_checkpoint_or_model_load_count",
        "evaluator_launch_count",
        "carla_launch_count",
        "control_write_count",
        "planner_invocation_count",
        "pid_invocation_count",
        "m3_invocation_count",
    ):
        if int(output.get(field, 0)) > 0:
            raise RuntimeError(BLOCKED_EXISTING_M2B_BINDING_REQUIRES_MODEL_EXECUTION)


def _candidate_plan(
    candidate: ShadowCandidateResult,
    evidence: ShadowCounterfactualEvidenceResult,
) -> dict[str, Any]:
    result = evidence.counterfactual_plan_evidence.to_dict()
    result.update(
        {
            "route_semantic_status": evidence.route.status,
            "speed_semantic_status": evidence.speed.status,
            "route_plan": copy.deepcopy(candidate.route),
            "speed_plan": copy.deepcopy(candidate.speed),
            "route_plan_sha256": evidence.candidate_route_digest,
            "speed_plan_sha256": evidence.candidate_speed_digest,
        }
    )
    return result


def _candidate_binding(evidence: ShadowCounterfactualEvidenceResult) -> dict[str, Any]:
    return evidence.interpretation_task_binding.to_dict()


def _pair_relation(
    evidence_a: ShadowCounterfactualEvidenceResult,
    evidence_b: ShadowCounterfactualEvidenceResult,
) -> str:
    targets = (
        evidence_a.counterfactual_plan_evidence.mapped_symbolic_target_id,
        evidence_b.counterfactual_plan_evidence.mapped_symbolic_target_id,
    )
    if any(target is None for target in targets):
        return "UNKNOWN"
    return "TASK_EQUIVALENT" if targets[0] == targets[1] else "TASK_CRITICAL"


def _build_runtime_case(
    snapshot: ShadowObservationSnapshot,
    candidate_a: ShadowCandidateResult,
    candidate_b: ShadowCandidateResult,
    evidence_a: ShadowCounterfactualEvidenceResult,
    evidence_b: ShadowCounterfactualEvidenceResult,
    runtime_authority_evidence: RuntimeDecisionAuthorityEvidenceV1,
) -> dict[str, Any]:
    candidate_ids = (evidence_a.candidate_id, evidence_b.candidate_id)
    if candidate_ids != (candidate_a.candidate_id, candidate_b.candidate_id):
        raise RuntimeError(M2B_INPUT_INCOMPLETE)
    decision_id = _snapshot_case_id(snapshot, candidate_a, candidate_b)
    source_digest = str(snapshot.source_digest)
    route_evidence_available = (
        evidence_a.route.status == "AVAILABLE"
        and evidence_b.route.status == "AVAILABLE"
    )
    speed_evidence_available = (
        evidence_a.speed.status == "AVAILABLE"
        and evidence_b.speed.status == "AVAILABLE"
    )
    hypothesis_bindings = {
        candidate_a.candidate_id: evidence_a.interpretation_task_binding,
        candidate_b.candidate_id: evidence_b.interpretation_task_binding,
    }
    has_longitudinal_requirement = any(
        binding.longitudinal_task_target is not None
        for binding in hypothesis_bindings.values()
    )
    longitudinal_task_outcomes = (
        build_longitudinal_task_outcomes_v0(
            candidate_ids,
            {
                candidate_a.candidate_id: candidate_a.speed,
                candidate_b.candidate_id: candidate_b.speed,
            },
            hypothesis_bindings,
        )
        if has_longitudinal_requirement
        else {}
    )
    clarification = runtime_authority_evidence.clarification
    holding = runtime_authority_evidence.holding
    now = runtime_authority_evidence.decision_monotonic_time
    deadline = (
        clarification.answer_deadline_monotonic
        if clarification is not None
        else holding.decision_deadline_monotonic
        if holding is not None
        else now + 5.0
    )
    if clarification is None:
        answer_channel = {
            "answer_resolution_probability": 1.0,
            "no_answer_probability": 0.0,
            "answer_confusion_matrix": [
                [candidate_a.candidate_id, [["OPTION_ONE", 0.5], ["OPTION_TWO", 0.5]]],
                [candidate_b.candidate_id, [["OPTION_ONE", 0.5], ["OPTION_TWO", 0.5]]],
            ],
            "delay_distribution": [[0.5, 1.0]],
            "channel_status": "AVAILABLE_UNINFORMATIVE",
            "source": "SHADOW_CHANNEL",
            "provenance": ("SHADOW_CHANNEL",),
            "reason_codes": ("IDENTICAL_LIKELIHOOD_ROWS_UNINFORMATIVE",),
        }
    else:
        answer_channel = {
            "answer_resolution_probability": clarification.answer_resolution_probability,
            "no_answer_probability": clarification.no_answer_probability,
            "answer_confusion_matrix": [
                [candidate_id, [[label, value] for label, value in row]]
                for candidate_id, row in clarification.answer_confusion_matrix
            ],
            "delay_distribution": [list(item) for item in clarification.delay_distribution],
            "channel_status": "AVAILABLE_VERIFIED",
            "source": clarification.source,
            "provenance": (
                "RUNTIME_DECISION_AUTHORITY_EVIDENCE_V1",
                clarification.evidence_grade,
            ),
            "reason_codes": clarification.reason_codes,
        }
    if holding is None:
        wait_opportunity = {
            "wait_mode": "NOT_AVAILABLE",
            "future_information_expected": False,
            "expected_information_arrival_time": None,
            "active_query_pending": False,
            "holding_capability_status": "NOT_AVAILABLE",
            "decision_deadline_status": "OPEN",
            "missed_opportunity_cost": 0.0,
            "wait_cost": 0.0,
            "information_resolution_probability": 0.0,
            "provenance": ("SHADOW_WAIT_PROFILE",),
        }
    else:
        wait_opportunity = {
            "wait_mode": "AWAIT_EXPECTED_OBSERVATION",
            "future_information_expected": True,
            "expected_information_arrival_time": holding.expected_information_arrival_time,
            "active_query_pending": False,
            "holding_capability_status": holding.holding_capability_status,
            "decision_deadline_status": "OPEN",
            "missed_opportunity_cost": holding.missed_opportunity_cost,
            "wait_cost": holding.wait_cost,
            "information_resolution_probability": holding.information_resolution_probability,
            "provenance": (
                "RUNTIME_DECISION_AUTHORITY_EVIDENCE_V1",
                holding.evidence_grade,
            ),
            "reason_codes": holding.reason_codes,
            "wait_reason": holding.wait_reason,
            "closed_loop_behavior": holding.closed_loop_behavior,
            "emergency_stop_semantics": holding.emergency_stop_semantics,
            "low_level_controller_owner": holding.low_level_controller_owner,
        }
    return {
        "schema_version": "driveclarify.decision_runtime_fixture.v0",
        "formal_adapter_version": "SHADOW_COUNTERFACTUAL_EVIDENCE_PRODUCER_V1",
        "decision_id": f"DECISION-{decision_id}",
        "episode_id": decision_id,
        "source_observation_id": str(snapshot.observation_id),
        "m2b_unit_key": f"M2B-UNIT-SHADOW-{decision_id}",
        "operating_profile_id": "SHADOW_ONLINE_M2B_V1",
        "candidate_ids": list(candidate_ids),
        "candidate_roles": {
            candidate_a.candidate_id: evidence_a.route.mapped_branch or "UNKNOWN",
            candidate_b.candidate_id: evidence_b.route.mapped_branch or "UNKNOWN",
        },
        "candidate_pair_relation": _pair_relation(evidence_a, evidence_b),
        "symbolic_plans": {
            candidate_a.candidate_id: _candidate_plan(candidate_a, evidence_a),
            candidate_b.candidate_id: _candidate_plan(candidate_b, evidence_b),
        },
        "hypothesis_bindings": {
            candidate_a.candidate_id: _candidate_binding(evidence_a),
            candidate_b.candidate_id: _candidate_binding(evidence_b),
        },
        **(
            {
                "longitudinal_task_outcomes": [
                    longitudinal_task_outcomes[(action_id, hypothesis_id)].to_dict()
                    for action_id in candidate_ids
                    for hypothesis_id in candidate_ids
                ]
            }
            if longitudinal_task_outcomes
            else {}
        ),
        "declared_wrong_goal_costs": {candidate_a.candidate_id: 1.0, candidate_b.candidate_id: 1.0},
        "intent_belief": {
            "candidate_probabilities": [
                [candidate_a.candidate_id, 0.5],
                [candidate_b.candidate_id, 0.5],
            ],
            "belief_status": "AVAILABLE",
            "belief_source": "RULE_DERIVED",
            "normalization_status": "NORMALIZED_PREDEFINED_PROFILE",
            "provenance": ("SHADOW_ROUTE_SPEED_PROFILE", "DYNAMIC_PROFILE"),
            "reason_codes": ("SHADOW_PROFILE_PRESERVED",),
        },
        "query_proposal": {
            "proposal_status": "QUESTION_PROPOSAL",
            "query_id": f"QUERY-{decision_id}",
            "template_id": "SHADOW_EXPLICIT_INTERPRETATION_PAIR_V0",
            "question_text": "Which interpretation did you intend?",
            "candidate_partition": [
                ["OPTION_ONE", [candidate_a.candidate_id]],
                ["OPTION_TWO", [candidate_b.candidate_id]],
            ],
            "natural_language_generalization_claimed": False,
        },
        "answer_channel": answer_channel,
        "query_budget": 1,
        "active_query_id": None,
        "monotonic_now": now,
        "answer_deadline_monotonic": deadline,
        "time_to_decision_status": "OPEN",
        "wait_opportunity": wait_opportunity,
        "hard_gate_envelope": {
            "hard_safety_status": runtime_authority_evidence.physical_safety.safety_status,
            "hard_rule_status": "PASS" if route_evidence_available else "UNKNOWN",
            "evidence_gate_status": "PASS" if route_evidence_available else "UNKNOWN",
            "query_episode_status": "NO_ACTIVE_QUERY",
            "cache_status": "FRESH",
            "candidate_freshness_status": "FRESH",
            "control_authorized": False,
            "reason_codes": (
                (
                    "SHADOW_ROUTE_SYMBOLIC_EVIDENCE_AVAILABLE"
                    if route_evidence_available
                    else "SHADOW_ROUTE_MAPPING_NOT_AVAILABLE"
                ),
                (
                    "SHADOW_PID_DESIRED_SPEED_EVIDENCE_AVAILABLE_DIAGNOSTIC_ONLY"
                    if speed_evidence_available
                    else "SHADOW_SPEED_SEMANTICS_NOT_AVAILABLE"
                ),
                *runtime_authority_evidence.physical_safety.reason_codes,
            ),
            "physical_safety_evidence": runtime_authority_evidence.physical_safety.to_dict(),
        },
        "evidence_masks": {
            "unknown_causes": ["PASSENGER_INTENT_AMBIGUITY"],
            "passenger_resolvable_unknown": True,
            "ambiguity_present": True,
            "rule_matrix_authoritative": False,
            "learned_authorization_eligible": False,
            "reason_codes": ("SHADOW_PROFILE",),
        },
        "operating_parameters": {
            "query_cost": clarification.query_cost if clarification is not None else 0.01,
            "delay_cost_per_second": (
                clarification.delay_cost_per_second if clarification is not None else 0.01
            ),
            "no_answer_penalty": (
                clarification.no_answer_penalty if clarification is not None else 0.1
            ),
            "unknown_task_loss_policy": "FAIL_CLOSED_NO_SCALAR_IMPUTATION",
            "strict_value_epsilon": 1e-9,
            "provenance": ("SHADOW_RUNTIME_OPERATION",),
        },
        "source_hashes": {"observation_package_manifest_sha256": source_digest},
        "provenance": [
            "OFFLINE_DECISION_DEVELOPMENT",
            SHADOW_ROUTE_PROVENANCE,
            "M2B_SHADOW_COUNTERFACTUAL_EVIDENCE_V1",
            "RUNTIME_DECISION_AUTHORITY_EVIDENCE_V1",
            "DIAGNOSTIC_ONLY",
        ],
    }


@dataclass(frozen=True)
class ShadowM2BDecisionResult:
    """Single producer-native M2B result; no M2B-to-M3 action translation is applied."""

    source_observation_id: str
    source_frame_id: int | str | None
    source_simulation_time: float | int
    decision_id: str
    candidate_ids: tuple[str, str]
    candidate_set_id: str
    candidate_input_digests: tuple[str, str]
    candidate_output_digests: tuple[str, str]
    consequence_input_digest: str
    counterfactual_evidence: tuple[
        ShadowCounterfactualEvidenceResult,
        ShadowCounterfactualEvidenceResult,
    ]
    counterfactual_matrix_status: str
    counterfactual_matrix_sha256: str
    decision_context_sha256: str
    producer_recommendation_sha256: str
    producer_action: str
    selected_candidate_id: str | None
    producer_query_id: str | None
    producer_wait_mode: str
    query_budget: int
    decision_monotonic_time: float
    answer_deadline_monotonic: float | None
    candidate_freshness: str
    reason_codes: tuple[str, ...]
    shadow_only: bool
    used_for_control: bool
    m2b_latency_ns: int
    existing_m2b_component: str
    existing_m2b_version: str
    candidate_rewrite_count: int
    candidate_fabrication_count: int
    model_forward_count: int
    model_load_count: int
    cuda_initialization_count: int
    control_write_count: int
    planner_invocation_count: int
    pid_invocation_count: int
    m3_invocation_count: int
    carla_invocation_count: int
    runtime_authority_evidence: Mapping[str, Any] | None = None
    decision_audit: Mapping[str, Any] | None = None
    holding_lease: Mapping[str, Any] | None = None
    counterfactual_matrix: Mapping[str, Any] | None = None
    execution_isolation: Mapping[str, Any] | None = None


def bind_candidates_to_m2b(
    snapshot: ShadowObservationSnapshot,
    candidate_a: ShadowCandidateResult,
    candidate_b: ShadowCandidateResult,
    *,
    mapping_context: ShadowCounterfactualMappingContext,
    runtime_authority_evidence: RuntimeDecisionAuthorityEvidenceV1 | None = None,
) -> ShadowM2BDecisionResult:
    if (
        not _require_nonempty(snapshot.source_digest)
        or not _require_nonempty(snapshot.observation_id)
        or not _require_nonempty(candidate_a.candidate_id)
        or not _require_nonempty(candidate_b.candidate_id)
        or not _require_nonempty(candidate_a.candidate_input_digest)
        or not _require_nonempty(candidate_b.candidate_input_digest)
        or not _require_nonempty(candidate_a.candidate_output_digest)
        or not _require_nonempty(candidate_b.candidate_output_digest)
    ):
        raise RuntimeError(M2B_INPUT_INCOMPLETE)

    if (
        snapshot.observation_id != mapping_context.source_observation_id
        or snapshot.frame_id != mapping_context.source_frame_id
    ):
        raise RuntimeError(M2B_INPUT_INCOMPLETE)
    evidence_a = bind_candidate_to_counterfactual_evidence(candidate_a, mapping_context)
    evidence_b = bind_candidate_to_counterfactual_evidence(candidate_b, mapping_context)
    authority_evidence = runtime_authority_evidence or (
        produce_runtime_decision_authority_evidence_v1(
            snapshot,
            (candidate_a.candidate_id, candidate_b.candidate_id),
        )
    )
    if (
        authority_evidence.source_observation_id != snapshot.observation_id
        or authority_evidence.source_frame_id
        != (None if snapshot.frame_id is None else str(snapshot.frame_id))
    ):
        raise RuntimeError(M2B_INPUT_INCOMPLETE)
    case = _build_runtime_case(
        snapshot,
        candidate_a,
        candidate_b,
        evidence_a,
        evidence_b,
        authority_evidence,
    )

    ambient_before = _ambient_model_runtime_state()
    started_ns = time.perf_counter_ns()
    try:
        policy_outputs = run_policy_cases([case])
    except TypeError as exc:
        raise RuntimeError(BLOCKED_EXISTING_M2B_BINDING_SIGNATURE_UNRESOLVED) from exc
    latency_ns = time.perf_counter_ns() - started_ns

    output = policy_outputs[0]
    ambient_after = _ambient_model_runtime_state()
    _ensure_no_model_execution(
        output,
        before=ambient_before,
        after=ambient_after,
    )

    recommendation = output.get("recommendation", {})
    decision = str(recommendation.get("decision", ""))
    producer_action = _validate_producer_action(decision)
    selected_candidate_id = recommendation.get("selected_candidate_id")
    if producer_action != "ACT":
        selected_candidate_id = None

    reason_codes = tuple(
        str(code)
        for code in recommendation.get("reason_trace", ())
    )
    context = dict(output.get("context", {}))
    matrix = context.get("counterfactual_outcome_matrix", {})
    hard_gate = context.get("hard_gate_envelope", {})
    if not isinstance(hard_gate, Mapping):
        raise RuntimeError(BLOCKED_EXISTING_M2B_BINDING_SIGNATURE_UNRESOLVED)
    producer_query_id = recommendation.get("query_id") if producer_action == "ASK" else None
    if producer_action == "WAIT":
        producer_query_id = context.get("active_query_id")
        if producer_query_id is None and authority_evidence.holding is not None:
            producer_query_id = authority_evidence.holding.expected_information_id
    candidate_set_sha256 = stable_sha256(
        {
            "candidate_ids": (candidate_a.candidate_id, candidate_b.candidate_id),
            "candidate_input_digests": (
                candidate_a.candidate_input_digest,
                candidate_b.candidate_input_digest,
            ),
            "candidate_output_digests": (
                candidate_a.candidate_output_digest,
                candidate_b.candidate_output_digest,
            ),
            "counterfactual_matrix": matrix,
        }
    )
    candidate_set_id = "M2B-SHADOW-CANDIDATE-SET-" + candidate_set_sha256
    holding_lease = (
        authority_evidence.holding.build_holding_lease(
            decision_monotonic_time=authority_evidence.decision_monotonic_time,
            source_observation_id=str(snapshot.observation_id),
            source_frame_id=str(snapshot.frame_id),
            candidate_set_id=candidate_set_id,
        )
        if producer_action == "WAIT" and authority_evidence.holding is not None
        else None
    )
    decision_predicates = dict(recommendation.get("decision_predicates", ()))
    decision_audit = {
        "candidate_A": {
            "candidate_id": candidate_a.candidate_id,
            "interpretation_id": candidate_a.interpretation_id,
        },
        "candidate_B": {
            "candidate_id": candidate_b.candidate_id,
            "interpretation_id": candidate_b.interpretation_id,
        },
        "current_preferred_action": decision_predicates.get(
            "current_preferred_action"
        ),
        "post_answer_possible_action": decision_predicates.get(
            "post_answer_possible_actions", ()
        ),
        "query_value": recommendation.get("query_value"),
        "wait_value": recommendation.get("wait_value"),
        "decision_predicates": decision_predicates,
        "selected_action": producer_action,
        "selected_candidate_id": selected_candidate_id,
        "act_target_type": recommendation.get("act_target_type"),
        "equivalence_class_candidate_ids": recommendation.get(
            "equivalence_class_candidate_ids", ()
        ),
        "decision_confidence_status": recommendation.get(
            "decision_confidence_status"
        ),
    }
    if "DIAGNOSTIC_ONLY" not in tuple(str(code) for code in reason_codes):
        if any(str(code).upper() == "DIAGNOSTIC_ONLY" for code in context.get("provenance", ())):
            reason_codes = reason_codes + ("DIAGNOSTIC_ONLY",)

    if (
        output.get("used_for_control") is not False
        or output.get("authorization_eligible") is not False
    ):
        raise RuntimeError(BLOCKED_EXISTING_M2B_BINDING_SIGNATURE_UNRESOLVED)

    return ShadowM2BDecisionResult(
        source_observation_id=str(snapshot.observation_id),
        source_frame_id=snapshot.frame_id,
        source_simulation_time=snapshot.simulation_time,
        decision_id=str(context.get("decision_id", case["decision_id"])),
        candidate_ids=(candidate_a.candidate_id, candidate_b.candidate_id),
        candidate_set_id=candidate_set_id,
        candidate_input_digests=(candidate_a.candidate_input_digest, candidate_b.candidate_input_digest),
        candidate_output_digests=(candidate_a.candidate_output_digest, candidate_b.candidate_output_digest),
        consequence_input_digest=canonical_sha256(
            (evidence_a.to_dict(), evidence_b.to_dict())
        ),
        counterfactual_evidence=(evidence_a, evidence_b),
        counterfactual_matrix_status=str(
            matrix.get("matrix_status", "INCOMPLETE_OR_UNKNOWN")
        ),
        counterfactual_matrix_sha256=stable_sha256(matrix),
        decision_context_sha256=stable_sha256(context),
        producer_recommendation_sha256=stable_sha256(recommendation),
        producer_action=producer_action,
        selected_candidate_id=(
            str(selected_candidate_id) if isinstance(selected_candidate_id, str) else None
        ),
        producer_query_id=(
            str(producer_query_id) if isinstance(producer_query_id, str) else None
        ),
        producer_wait_mode=str(recommendation.get("wait_mode", "NOT_AVAILABLE")),
        query_budget=int(context.get("query_budget", case["query_budget"])),
        decision_monotonic_time=float(
            context.get("monotonic_now", case["monotonic_now"])
        ),
        answer_deadline_monotonic=(
            float(context["answer_deadline_monotonic"])
            if context.get("answer_deadline_monotonic") is not None
            else None
        ),
        candidate_freshness=str(
            hard_gate.get("candidate_freshness_status", "UNKNOWN")
        ),
        reason_codes=tuple(str(code) for code in reason_codes),
        shadow_only=True,
        used_for_control=False,
        m2b_latency_ns=int(latency_ns),
        existing_m2b_component=EXISTING_M2B_COMPONENT,
        existing_m2b_version=EXISTING_M2B_VERSION,
        candidate_rewrite_count=0,
        candidate_fabrication_count=0,
        model_forward_count=0,
        model_load_count=0,
        cuda_initialization_count=0,
        control_write_count=0,
        planner_invocation_count=0,
        pid_invocation_count=0,
        m3_invocation_count=0,
        carla_invocation_count=0,
        runtime_authority_evidence=authority_evidence.to_dict(),
        decision_audit=decision_audit,
        holding_lease=holding_lease,
        counterfactual_matrix=matrix,
        execution_isolation={
            "ambient_runtime_before": dict(ambient_before),
            "ambient_runtime_after": dict(ambient_after),
            "ambient_runtime_unchanged": ambient_before == ambient_after,
            "m2b_induced_model_forward_count": 0,
            "m2b_induced_model_load_count": 0,
            "m2b_induced_cuda_initialization_count": 0,
            "m2b_induced_control_write_count": 0,
            "measurement_semantics": "CALL_DELTA_NOT_PROCESS_AMBIENT_STATE",
        },
    )
