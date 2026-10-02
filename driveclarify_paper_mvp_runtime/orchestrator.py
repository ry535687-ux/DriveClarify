"""Catalog-free Stage 6A candidate, consequence, decision, and authority path."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from driveclarify_m3_runtime_shadow.contracts import (
    ShadowCandidateResult,
    ShadowObservationSnapshot,
)
from driveclarify_m3_runtime_shadow.counterfactual_evidence import (
    ShadowCounterfactualMappingContext,
)
from driveclarify_m3_runtime_shadow.m2b_binding import (
    ShadowM2BDecisionResult,
    bind_candidates_to_m2b,
)
from driveclarify_m3_runtime_shadow.runtime_authority_evidence_v1 import (
    RuntimeDecisionAuthorityEvidenceV1,
    produce_runtime_decision_authority_evidence_v1,
)
from driveclarify_m3_runtime_shadow.runtime_decision_authority_v1 import (
    RuntimeDecisionAuthorityActivationV1,
    activate_runtime_decision_authority_v1,
)

from .authority import AuthorityArmResult, PersistentPrePidAuthority
from .candidate_generation import RuntimeCandidateGenerator
from .contracts import (
    CandidateGenerationResult,
    CandidatePlan,
    ConsequenceEvaluation,
    ConsequenceEvaluator,
    ExecutionBoundaryFacts,
    HardRuleEvidence,
    HardRuleProvider,
    PhysicalSafetyProvider,
    PlanProvider,
    PolicyEpisodeInput,
    RuntimeCandidate,
    RuntimeSignalProvider,
    Stage6AContractError,
    assert_no_evaluation_fields,
    assert_unprivileged_source,
)


@dataclass(frozen=True)
class Stage6AOrchestrationResult:
    status: str
    policy_input_sha256: str
    candidate_generation: CandidateGenerationResult
    plans: tuple[CandidatePlan, ...]
    consequence: ConsequenceEvaluation | None
    hard_rule_evidence: HardRuleEvidence | None
    runtime_authority_evidence: RuntimeDecisionAuthorityEvidenceV1 | None
    m2b_result: ShadowM2BDecisionResult | None
    activation: RuntimeDecisionAuthorityActivationV1 | None
    authority_arm: AuthorityArmResult | None
    binding_invocation_count: int
    activation_v1_invocation_count: int
    reason_codes: tuple[str, ...]

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "policy_input_sha256": self.policy_input_sha256,
            "candidate_generation_audit": self.candidate_generation.audit.to_dict(),
            "candidate_plan_output_sha256": [item.output_digest for item in self.plans],
            "consequence_status": (
                None if self.consequence is None else self.consequence.status
            ),
            "hard_rule_status": (
                None
                if self.hard_rule_evidence is None
                else self.hard_rule_evidence.status
            ),
            "physical_safety_status": (
                None
                if self.runtime_authority_evidence is None
                else self.runtime_authority_evidence.physical_safety.safety_status
            ),
            "m2b_action": (
                None if self.m2b_result is None else self.m2b_result.producer_action
            ),
            "activation_adapter": (
                None if self.activation is None else self.activation.adapter
            ),
            "authority_arm": (
                None if self.authority_arm is None else self.authority_arm.to_dict()
            ),
            "binding_invocation_count": self.binding_invocation_count,
            "activation_v1_invocation_count": self.activation_v1_invocation_count,
            "reason_codes": list(self.reason_codes),
            "control_write_count": 0,
            "pid_invocation_count": 0,
            "catalog_read_count": 0,
        }


def _shadow_snapshot(episode: PolicyEpisodeInput) -> ShadowObservationSnapshot:
    vision = episode.vision_observation
    ego = episode.ego_state
    route = episode.route_context
    return ShadowObservationSnapshot(
        observation_id=vision.observation_id,
        frame_id=vision.frame_id,
        simulation_time=vision.captured_monotonic_time,
        speed=(float(ego.speed_mps),),
        target_point={
            "x": float(route.target_point_x_m),
            "y": float(route.target_point_y_m),
        },
        route_context={
            "route_command": route.route_command,
            "route_digest": route.route_digest,
            "source": route.source,
        },
        instruction=episode.raw_instruction,
        model_input_snapshot={
            "vision_image_sha256": vision.image_sha256,
            "frame_id": vision.frame_id,
            "ego_source": ego.source,
            "route_digest": route.route_digest,
        },
        source_digest=episode.input_digest,
    )


def _validate_plan(
    episode: PolicyEpisodeInput,
    candidate: RuntimeCandidate,
    plan: CandidatePlan,
) -> None:
    if not isinstance(plan, CandidatePlan):
        raise Stage6AContractError("PLAN_PROVIDER_MUST_RETURN_CANDIDATE_PLAN")
    if plan.candidate_id != candidate.candidate_id:
        raise Stage6AContractError("PLAN_CANDIDATE_IDENTITY_MISMATCH")
    if plan.source_observation_id != episode.vision_observation.observation_id:
        raise Stage6AContractError("PLAN_OBSERVATION_IDENTITY_MISMATCH")
    if plan.source_frame_id != episode.vision_observation.frame_id:
        raise Stage6AContractError("PLAN_FRAME_IDENTITY_MISMATCH")


def _shadow_candidate(
    candidate: RuntimeCandidate,
    plan: CandidatePlan,
) -> ShadowCandidateResult:
    return ShadowCandidateResult(
        candidate_id=candidate.candidate_id,
        interpretation_id=candidate.interpretation_id,
        model_forward_sequence_id=plan.model_forward_sequence_id,
        source_observation_id=plan.source_observation_id,
        source_frame_id=plan.source_frame_id,
        route=plan.route,
        speed=plan.speed,
        language=plan.language,
        candidate_input_digest=candidate.candidate_input_digest,
        candidate_output_digest=plan.output_digest,
        latency=float(plan.latency_s),
    )


def _validate_consequence(
    episode: PolicyEpisodeInput,
    candidates: tuple[RuntimeCandidate, RuntimeCandidate],
    consequence: ConsequenceEvaluation,
) -> None:
    if not isinstance(consequence, ConsequenceEvaluation):
        raise Stage6AContractError(
            "CONSEQUENCE_CALLBACK_MUST_RETURN_CONSEQUENCE_EVALUATION"
        )
    expected_ids = tuple(item.candidate_id for item in candidates)
    if consequence.candidate_ids != expected_ids:
        raise Stage6AContractError("CONSEQUENCE_CANDIDATE_ORDER_IDENTITY_MISMATCH")
    if consequence.source_observation_id != episode.vision_observation.observation_id:
        raise Stage6AContractError("CONSEQUENCE_OBSERVATION_IDENTITY_MISMATCH")
    if consequence.source_frame_id != episode.vision_observation.frame_id:
        raise Stage6AContractError("CONSEQUENCE_FRAME_IDENTITY_MISMATCH")
    if consequence.status == "UNKNOWN":
        return
    context = consequence.mapping_context
    if not isinstance(context, ShadowCounterfactualMappingContext):
        raise Stage6AContractError("SHADOW_MAPPING_CONTEXT_CONTRACT_REQUIRED")
    if context.source_observation_id != consequence.source_observation_id:
        raise Stage6AContractError("MAPPING_OBSERVATION_IDENTITY_MISMATCH")
    if str(context.source_frame_id) != str(consequence.source_frame_id):
        raise Stage6AContractError("MAPPING_FRAME_IDENTITY_MISMATCH")
    expected_interpretations = {
        item.interpretation_id: item.candidate_id for item in candidates
    }
    if set(context.interpretation_task_bindings) != set(expected_interpretations):
        raise Stage6AContractError("MAPPING_INTERPRETATION_IDENTITIES_MISMATCH")
    for interpretation_id, candidate_id in expected_interpretations.items():
        binding = context.interpretation_task_bindings[interpretation_id]
        if binding.source_candidate_id != candidate_id:
            raise Stage6AContractError("MAPPING_TASK_BINDING_CANDIDATE_MISMATCH")
        assert_unprivileged_source(binding.binding_source, "TASK_BINDING_SOURCE")
        for source in binding.provenance:
            assert_unprivileged_source(source, "TASK_BINDING_PROVENANCE")
    for source in context.mapping_provenance:
        assert_unprivileged_source(source, "MAPPING_PROVENANCE")
    for source in context.source_artifacts:
        assert_unprivileged_source(source, "MAPPING_SOURCE_ARTIFACT")
    assert_no_evaluation_fields(
        {
            "frozen_topology": context.frozen_topology,
            "mapping_threshold_contract": context.mapping_threshold_contract,
            "ego_transform": context.ego_transform,
            "branch_task_equivalence_classes": (
                context.branch_task_equivalence_classes
            ),
        }
    )


def _validate_rule(
    episode: PolicyEpisodeInput,
    decision_monotonic_time: float,
    evidence: HardRuleEvidence,
) -> None:
    if not isinstance(evidence, HardRuleEvidence):
        raise Stage6AContractError("RULE_CALLBACK_MUST_RETURN_HARD_RULE_EVIDENCE")
    if evidence.source_observation_id != episode.vision_observation.observation_id:
        raise Stage6AContractError("HARD_RULE_OBSERVATION_IDENTITY_MISMATCH")
    if evidence.source_frame_id != episode.vision_observation.frame_id:
        raise Stage6AContractError("HARD_RULE_FRAME_IDENTITY_MISMATCH")
    if not math.isclose(
        float(evidence.observed_monotonic_time),
        float(decision_monotonic_time),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise Stage6AContractError("HARD_RULE_TIMESTAMP_NOT_CURRENT")


def _validate_physical_signal(
    episode: PolicyEpisodeInput,
    signal: Mapping[str, Any] | None,
) -> None:
    if signal is None:
        return
    assert_no_evaluation_fields(signal)
    if str(signal.get("source_observation_id", "")) != (
        episode.vision_observation.observation_id
    ):
        raise Stage6AContractError("PHYSICAL_SAFETY_OBSERVATION_IDENTITY_MISMATCH")
    if str(signal.get("source_frame_id", "")) != str(
        episode.vision_observation.frame_id
    ):
        raise Stage6AContractError("PHYSICAL_SAFETY_FRAME_IDENTITY_MISMATCH")
    if signal.get("source_kind") != "INDEPENDENT_PHYSICAL_SAFETY_MONITOR":
        raise Stage6AContractError("PHYSICAL_SAFETY_SOURCE_NOT_INDEPENDENT")
    assert_unprivileged_source(signal.get("source"), "PHYSICAL_SAFETY_SOURCE")


class Stage6AOrchestrator:
    """Run the Stage 6A path without model, simulator, PID, or file access."""

    def __init__(
        self,
        *,
        plan_provider: PlanProvider,
        consequence_evaluator: ConsequenceEvaluator,
        physical_safety_provider: PhysicalSafetyProvider | None = None,
        hard_rule_provider: HardRuleProvider | None = None,
        clarification_provider: RuntimeSignalProvider | None = None,
        holding_provider: RuntimeSignalProvider | None = None,
        candidate_generator: RuntimeCandidateGenerator | None = None,
        pre_pid_authority: PersistentPrePidAuthority | None = None,
        binding_fn: Callable[..., ShadowM2BDecisionResult] | None = None,
        activation_fn: Callable[
            [ShadowM2BDecisionResult], RuntimeDecisionAuthorityActivationV1
        ]
        | None = None,
    ) -> None:
        if not callable(plan_provider) or not callable(consequence_evaluator):
            raise TypeError("PLAN_AND_CONSEQUENCE_CALLBACKS_REQUIRED")
        self.plan_provider = plan_provider
        self.consequence_evaluator = consequence_evaluator
        self.physical_safety_provider = physical_safety_provider
        self.hard_rule_provider = hard_rule_provider
        self.clarification_provider = clarification_provider
        self.holding_provider = holding_provider
        self.candidate_generator = candidate_generator or RuntimeCandidateGenerator()
        self.pre_pid_authority = pre_pid_authority or PersistentPrePidAuthority()
        self._binding_fn = binding_fn or bind_candidates_to_m2b
        self._activation_fn = activation_fn or activate_runtime_decision_authority_v1

    def run(
        self,
        episode: PolicyEpisodeInput,
        *,
        decision_monotonic_time: float,
        issuance_facts: ExecutionBoundaryFacts,
    ) -> Stage6AOrchestrationResult:
        if not isinstance(episode, PolicyEpisodeInput):
            raise TypeError("POLICY_EPISODE_INPUT_CONTRACT_REQUIRED")
        if not math.isfinite(float(decision_monotonic_time)):
            raise Stage6AContractError("DECISION_TIME_MUST_BE_FINITE")
        if not isinstance(issuance_facts, ExecutionBoundaryFacts):
            raise TypeError("EXECUTION_BOUNDARY_FACTS_REQUIRED")
        if not math.isclose(
            float(decision_monotonic_time),
            float(issuance_facts.current_monotonic_time),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise Stage6AContractError("DECISION_AND_ISSUANCE_TIME_MISMATCH")

        generated = self.candidate_generator.generate(episode)
        if generated.status != "READY" or len(generated.candidates) != 2:
            return Stage6AOrchestrationResult(
                status="BLOCKED_CANDIDATE_GENERATION_UNKNOWN",
                policy_input_sha256=episode.input_digest,
                candidate_generation=generated,
                plans=(),
                consequence=None,
                hard_rule_evidence=None,
                runtime_authority_evidence=None,
                m2b_result=None,
                activation=None,
                authority_arm=None,
                binding_invocation_count=0,
                activation_v1_invocation_count=0,
                reason_codes=generated.reason_codes,
            )
        candidates = (generated.candidates[0], generated.candidates[1])
        plans_list: list[CandidatePlan] = []
        for candidate in candidates:
            plan = self.plan_provider(episode, candidate)
            _validate_plan(episode, candidate, plan)
            plans_list.append(plan)
        plans = (plans_list[0], plans_list[1])

        consequence = self.consequence_evaluator(episode, candidates, plans)
        _validate_consequence(episode, candidates, consequence)
        if consequence.status != "AVAILABLE_VERIFIED":
            return Stage6AOrchestrationResult(
                status="BLOCKED_CONSEQUENCE_UNKNOWN",
                policy_input_sha256=episode.input_digest,
                candidate_generation=generated,
                plans=plans,
                consequence=consequence,
                hard_rule_evidence=None,
                runtime_authority_evidence=None,
                m2b_result=None,
                activation=None,
                authority_arm=None,
                binding_invocation_count=0,
                activation_v1_invocation_count=0,
                reason_codes=("CONSEQUENCE_EVIDENCE_UNKNOWN",),
            )

        rule = (
            self.hard_rule_provider(
                episode, candidates, plans, consequence, decision_monotonic_time
            )
            if self.hard_rule_provider is not None
            else None
        )
        if rule is None:
            rule = HardRuleEvidence.unknown(episode, decision_monotonic_time)
        _validate_rule(episode, decision_monotonic_time, rule)
        if not rule.authorizes_progress:
            return Stage6AOrchestrationResult(
                status="BLOCKED_HARD_RULE_NOT_VERIFIED",
                policy_input_sha256=episode.input_digest,
                candidate_generation=generated,
                plans=plans,
                consequence=consequence,
                hard_rule_evidence=rule,
                runtime_authority_evidence=None,
                m2b_result=None,
                activation=None,
                authority_arm=None,
                binding_invocation_count=0,
                activation_v1_invocation_count=0,
                reason_codes=rule.reason_codes,
            )

        physical_signal = (
            self.physical_safety_provider(
                episode, candidates, plans, consequence, decision_monotonic_time
            )
            if self.physical_safety_provider is not None
            else None
        )
        _validate_physical_signal(episode, physical_signal)
        clarification_signal = (
            self.clarification_provider(
                episode, candidates, plans, consequence, decision_monotonic_time
            )
            if self.clarification_provider is not None
            else None
        )
        holding_signal = (
            self.holding_provider(
                episode, candidates, plans, consequence, decision_monotonic_time
            )
            if self.holding_provider is not None
            else None
        )
        if clarification_signal is not None:
            assert_no_evaluation_fields(clarification_signal)
        if holding_signal is not None:
            assert_no_evaluation_fields(holding_signal)

        snapshot = _shadow_snapshot(episode)
        runtime_evidence = produce_runtime_decision_authority_evidence_v1(
            snapshot,
            tuple(item.candidate_id for item in candidates),
            decision_monotonic_time=decision_monotonic_time,
            physical_safety_signal=physical_signal,
            clarification_signal=clarification_signal,
            holding_signal=holding_signal,
        )
        shadow_candidates = (
            _shadow_candidate(candidates[0], plans[0]),
            _shadow_candidate(candidates[1], plans[1]),
        )
        producer = self._binding_fn(
            snapshot,
            shadow_candidates[0],
            shadow_candidates[1],
            mapping_context=consequence.mapping_context,
            runtime_authority_evidence=runtime_evidence,
        )
        if not isinstance(producer, ShadowM2BDecisionResult):
            raise Stage6AContractError("M2B_BINDING_RESULT_CONTRACT_MISMATCH")
        activation = self._activation_fn(producer)
        if not isinstance(activation, RuntimeDecisionAuthorityActivationV1):
            raise Stage6AContractError("ACTIVATION_V1_RESULT_CONTRACT_MISMATCH")
        arm = self.pre_pid_authority.arm(
            producer=producer,
            activation=activation,
            runtime_evidence=runtime_evidence,
            hard_rule_evidence=rule,
            snapshot=snapshot,
            plans=plans,
            issuance_facts=issuance_facts,
        )
        return Stage6AOrchestrationResult(
            status="COMPLETED_FAIL_CLOSED_AUTHORITY_READY",
            policy_input_sha256=episode.input_digest,
            candidate_generation=generated,
            plans=plans,
            consequence=consequence,
            hard_rule_evidence=rule,
            runtime_authority_evidence=runtime_evidence,
            m2b_result=producer,
            activation=activation,
            authority_arm=arm,
            binding_invocation_count=1,
            activation_v1_invocation_count=1,
            reason_codes=producer.reason_codes,
        )


__all__ = ["Stage6AOrchestrationResult", "Stage6AOrchestrator"]
