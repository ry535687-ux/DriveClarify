"""Truthful shadow candidate-to-counterfactual evidence producer V0.

This module is deliberately limited to diagnostic task evidence.  It reuses
the verified static branch mapper for route semantics and the verified
SimLingo PID desired-speed contract for longitudinal semantics.  It does not
infer physical safety or authorization from model output.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from driveclarify_decision.counterfactual_matrix import (
    CounterfactualPlanEvidence,
    build_counterfactual_outcome_matrix,
)
from driveclarify_decision.decision_contracts import (
    CounterfactualOutcomeMatrix,
    LongitudinalTaskEvidence,
    LongitudinalTaskOutcome,
    LongitudinalTaskOutcomeEvidence,
)
from driveclarify_language.interaction_contracts import (
    BindingStatus,
    CandidateSpecificTaskBinding,
)
from driveclarify_m3_offline_replay.serialization import canonical_sha256
from driveclarify_static_branch.mapper import (
    VERIFIED_PLAN_FRAME,
    VERIFIED_PLAN_UNIT,
    StaticBranchPlanMapperV1,
)
from driveclarify_static_branch.topology import deterministic_sha256, verify_sha256

from .contracts import ShadowCandidateResult
from .longitudinal_task_satisfaction import (
    LongitudinalTaskSatisfactionOutcome,
    evaluate_stop_task_satisfaction_v0,
)
from .speed_consequence import (
    AVAILABLE as SPEED_CONSEQUENCE_AVAILABLE,
    SIMLINGO_SPEED_REPRESENTATION_CONTRACT_V0,
    SpeedRepresentationContractV0,
    evaluate_pid_desired_speed_v0,
)


SHADOW_ROUTE_PROVENANCE = "SHADOW_STATIC_BRANCH_MAPPED_CANDIDATE_DIAGNOSTIC_V0"
SHADOW_SPEED_PROVENANCE = "SHADOW_PID_DESIRED_SPEED_CANDIDATE_DIAGNOSTIC_V0"
SHADOW_EVIDENCE_GRADE = "SUPPORTED_BUT_INCOMPLETE"
NOT_AVAILABLE_EVIDENCE_GRADE = "NOT_AVAILABLE"
DIAGNOSTIC_USAGE = ("DIAGNOSTIC_ONLY", "LOGGING_ONLY")
PID_DESIRED_SPEED_FRAME = "EGO_LOCAL_X_FORWARD_Y_RIGHT"
PID_DESIRED_SPEED_UNIT = "MPS"
PID_DESIRED_SPEED_TEMPORAL_BASIS = "Q0_TO_Q2_0P5S"
PID_DESIRED_SPEED_SOURCE = "ShadowCandidateResult.speed"
SIMLINGO_STOP_SUCCESS_V0 = "SIMLINGO_STOP_SUCCESS_V0"

BLOCKED_SHADOW_ROUTE_MAPPING_CONTEXT_INCOMPLETE = (
    "BLOCKED_SHADOW_ROUTE_MAPPING_CONTEXT_INCOMPLETE"
)
BLOCKED_SHADOW_CANDIDATE_ROUTE_FRAME_UNIT_NOT_VERIFIED = (
    "BLOCKED_SHADOW_CANDIDATE_ROUTE_FRAME_UNIT_NOT_VERIFIED"
)
BLOCKED_SHADOW_INTERPRETATION_TASK_BINDING_NOT_AVAILABLE = (
    "BLOCKED_SHADOW_INTERPRETATION_TASK_BINDING_NOT_AVAILABLE"
)


@dataclass(frozen=True)
class ShadowCounterfactualMappingContext:
    """Frozen, caller-supplied evidence required by the existing route mapper."""

    source_observation_id: str
    source_frame_id: int | str
    frozen_topology: Mapping[str, Any]
    mapping_threshold_contract: Mapping[str, Any]
    ego_transform: Mapping[str, Any]
    plan_frame: str
    plan_unit: str
    frame_evidence_status: str
    unit_evidence_status: str
    branch_task_equivalence_classes: Mapping[str, str]
    interpretation_task_bindings: Mapping[str, CandidateSpecificTaskBinding]
    mapping_provenance: tuple[str, ...]
    source_artifacts: tuple[str, ...]
    symbolic_target_type: str = "BRANCH_TASK_EQUIVALENCE_CLASS"


@dataclass(frozen=True)
class ShadowRouteSemanticEvidence:
    status: str
    mapped_branch: str | None
    mapped_symbolic_target_type: str | None
    mapped_symbolic_target_id: str | None
    evidence_grade: str
    allowed_usage_purposes: tuple[str, ...]
    provenance: tuple[str, ...]
    mapper_identity: str
    mapper_schema_version: str | None
    mapper_evidence_sha256: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class ShadowSpeedSemanticEvidence:
    status: str
    semantic_type: str | None
    semantic_value: float | None
    frame: str | None
    unit: str | None
    temporal_basis: str | None
    evidence_grade: str
    allowed_usage_purposes: tuple[str, ...]
    provenance: tuple[str, ...]
    raw_speed_available: bool
    raw_speed_digest: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class ShadowCounterfactualEvidenceResult:
    """Producer envelope around the existing M2B plan-evidence contract."""

    candidate_id: str
    interpretation_id: str
    source_observation_id: str
    source_frame_id: int | str
    interpretation_task_binding: CandidateSpecificTaskBinding
    route: ShadowRouteSemanticEvidence
    speed: ShadowSpeedSemanticEvidence
    counterfactual_plan_evidence: CounterfactualPlanEvidence
    candidate_route_digest: str
    candidate_speed_digest: str
    diagnostic_only: bool
    authorization_eligible: bool
    safety_critical_eligible: bool
    mapper_invocation_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "interpretation_id": self.interpretation_id,
            "source_observation_id": self.source_observation_id,
            "source_frame_id": self.source_frame_id,
            "interpretation_task_binding": self.interpretation_task_binding.to_dict(),
            "route": {
                "status": self.route.status,
                "mapped_branch": self.route.mapped_branch,
                "mapped_symbolic_target_type": self.route.mapped_symbolic_target_type,
                "mapped_symbolic_target_id": self.route.mapped_symbolic_target_id,
                "evidence_grade": self.route.evidence_grade,
                "allowed_usage_purposes": list(self.route.allowed_usage_purposes),
                "provenance": list(self.route.provenance),
                "mapper_identity": self.route.mapper_identity,
                "mapper_schema_version": self.route.mapper_schema_version,
                "mapper_evidence_sha256": self.route.mapper_evidence_sha256,
                "reason_codes": list(self.route.reason_codes),
            },
            "speed": {
                "status": self.speed.status,
                "semantic_type": self.speed.semantic_type,
                "semantic_value": self.speed.semantic_value,
                "frame": self.speed.frame,
                "unit": self.speed.unit,
                "temporal_basis": self.speed.temporal_basis,
                "evidence_grade": self.speed.evidence_grade,
                "allowed_usage_purposes": list(self.speed.allowed_usage_purposes),
                "provenance": list(self.speed.provenance),
                "raw_speed_available": self.speed.raw_speed_available,
                "raw_speed_digest": self.speed.raw_speed_digest,
                "reason_codes": list(self.speed.reason_codes),
            },
            "counterfactual_plan_evidence": self.counterfactual_plan_evidence.to_dict(),
            "candidate_route_digest": self.candidate_route_digest,
            "candidate_speed_digest": self.candidate_speed_digest,
            "diagnostic_only": self.diagnostic_only,
            "authorization_eligible": self.authorization_eligible,
            "safety_critical_eligible": self.safety_critical_eligible,
            "mapper_invocation_count": self.mapper_invocation_count,
        }


def build_longitudinal_task_outcomes_v0(
    candidate_ids: tuple[str, ...],
    candidate_speed_waypoints: Mapping[str, Any],
    hypothesis_bindings: Mapping[str, CandidateSpecificTaskBinding],
    *,
    speed_contract: SpeedRepresentationContractV0 = SIMLINGO_SPEED_REPRESENTATION_CONTRACT_V0,
) -> dict[tuple[str, str], LongitudinalTaskOutcomeEvidence]:
    """Evaluate every action×hypothesis longitudinal component in runtime-shadow.

    Raw future speed waypoints remain in the producer/runtime layer.  Only the
    structured diagnostic outcome crosses into the decision package, keeping
    the dependency direction decision <- runtime-shadow.
    """

    outcomes: dict[tuple[str, str], LongitudinalTaskOutcomeEvidence] = {}
    for action_id in candidate_ids:
        raw_speed = candidate_speed_waypoints.get(action_id)
        for hypothesis_id in candidate_ids:
            binding = hypothesis_bindings.get(hypothesis_id)
            target = binding.longitudinal_task_target if binding is not None else None
            result = evaluate_stop_task_satisfaction_v0(
                candidate_id=action_id,
                hypothesis_id=hypothesis_id,
                longitudinal_task_target=target,
                candidate_speed_waypoints=raw_speed,
                representation_contract=speed_contract,
            )
            outcome = LongitudinalTaskOutcome(result.outcome.value)
            outcomes[(action_id, hypothesis_id)] = LongitudinalTaskOutcomeEvidence(
                action_candidate_id=action_id,
                hypothesis_candidate_id=hypothesis_id,
                target_type=result.target_type,
                outcome=outcome,
                metric_identity=(
                    SIMLINGO_STOP_SUCCESS_V0
                    if result.target_type == "STOP"
                    else None
                ),
                evidence_status=(
                    "AVAILABLE"
                    if result.outcome
                    in {
                        LongitudinalTaskSatisfactionOutcome.SATISFIED,
                        LongitudinalTaskSatisfactionOutcome.CONTRADICTED,
                    }
                    else "NOT_APPLICABLE"
                    if result.outcome
                    is LongitudinalTaskSatisfactionOutcome.NOT_APPLICABLE
                    else "INSUFFICIENT"
                ),
                raw_speed_digest=result.raw_speed_digest,
                source=result.contract_source,
                evidence_grade=(
                    SHADOW_EVIDENCE_GRADE
                    if result.outcome
                    in {
                        LongitudinalTaskSatisfactionOutcome.SATISFIED,
                        LongitudinalTaskSatisfactionOutcome.CONTRADICTED,
                    }
                    else NOT_AVAILABLE_EVIDENCE_GRADE
                ),
                allowed_usage_purposes=result.allowed_usage_purposes,
                provenance=tuple(
                    dict.fromkeys(
                        (
                            *result.evidence_basis,
                            "SHADOW_SIMLINGO_STOP_TASK_OUTCOME_DIAGNOSTIC_V0",
                        )
                    )
                ),
                authorization_eligible=result.authorization_eligible,
                safety_critical_eligible=result.safety_critical_eligible,
                reason_codes=result.reason_codes,
            )
    return outcomes


def build_route_stop_counterfactual_outcome_matrix_v0(
    candidate_ids: tuple[str, ...],
    plans: Mapping[str, CounterfactualPlanEvidence],
    hypothesis_bindings: Mapping[str, CandidateSpecificTaskBinding],
    declared_wrong_goal_costs: Mapping[str, float],
    candidate_speed_waypoints: Mapping[str, Any],
    *,
    provenance: tuple[str, ...] = (),
    speed_contract: SpeedRepresentationContractV0 = SIMLINGO_SPEED_REPRESENTATION_CONTRACT_V0,
) -> CounterfactualOutcomeMatrix:
    """Build the composed matrix without importing runtime code from decision core."""

    longitudinal_outcomes = build_longitudinal_task_outcomes_v0(
        candidate_ids,
        candidate_speed_waypoints,
        hypothesis_bindings,
        speed_contract=speed_contract,
    )
    return build_counterfactual_outcome_matrix(
        candidate_ids,
        plans,
        hypothesis_bindings,
        declared_wrong_goal_costs,
        longitudinal_task_outcomes=longitudinal_outcomes,
        provenance=provenance,
    )


def _require_mapping_context(
    candidate: ShadowCandidateResult,
    context: ShadowCounterfactualMappingContext,
) -> CandidateSpecificTaskBinding:
    if (
        not context.source_observation_id
        or context.source_frame_id is None
        or candidate.source_observation_id != context.source_observation_id
        or candidate.source_frame_id != context.source_frame_id
        or not verify_sha256(context.frozen_topology)
        or not context.mapping_threshold_contract
        or not context.ego_transform
        or not context.branch_task_equivalence_classes
        or not context.mapping_provenance
        or not context.source_artifacts
        or not context.symbolic_target_type
    ):
        raise RuntimeError(BLOCKED_SHADOW_ROUTE_MAPPING_CONTEXT_INCOMPLETE)
    topology_roles = {
        str(branch.get("semantic_role"))
        for branch in context.frozen_topology.get("branches", ())
        if isinstance(branch, Mapping) and branch.get("semantic_role")
    }
    if topology_roles != set(context.branch_task_equivalence_classes):
        raise RuntimeError(BLOCKED_SHADOW_ROUTE_MAPPING_CONTEXT_INCOMPLETE)
    if (
        context.plan_frame != VERIFIED_PLAN_FRAME
        or context.plan_unit != VERIFIED_PLAN_UNIT
        or context.frame_evidence_status != "VERIFIED"
        or context.unit_evidence_status != "VERIFIED"
    ):
        raise RuntimeError(BLOCKED_SHADOW_CANDIDATE_ROUTE_FRAME_UNIT_NOT_VERIFIED)
    binding = context.interpretation_task_bindings.get(candidate.interpretation_id)
    if (
        not isinstance(binding, CandidateSpecificTaskBinding)
        or binding.source_candidate_id != candidate.candidate_id
        or binding.binding_status is not BindingStatus.BOUND
        or not binding.symbolic_target_type
        or not binding.symbolic_target_id
        or not binding.binding_source
        or not binding.provenance
    ):
        raise RuntimeError(BLOCKED_SHADOW_INTERPRETATION_TASK_BINDING_NOT_AVAILABLE)
    return binding


def _plan_points(route: Any) -> list[list[float]]:
    if not isinstance(route, (list, tuple)):
        return []
    points: list[list[float]] = []
    for point in route:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return []
        points.append([copy.deepcopy(point[0]), copy.deepcopy(point[1])])
    return points


def bind_candidate_to_counterfactual_evidence(
    candidate: ShadowCandidateResult,
    context: ShadowCounterfactualMappingContext,
    *,
    speed_contract: SpeedRepresentationContractV0 = SIMLINGO_SPEED_REPRESENTATION_CONTRACT_V0,
) -> ShadowCounterfactualEvidenceResult:
    """Map one candidate route and bind route plus desired speed evidence.

    Missing or unverified external context is rejected.  A mapper-level
    UNKNOWN/NO_MATCH remains structured unavailable evidence rather than being
    converted to a false negative or a safety claim.
    """

    binding = _require_mapping_context(candidate, context)
    original_route = copy.deepcopy(candidate.route)
    original_speed = copy.deepcopy(candidate.speed)
    points = _plan_points(candidate.route)
    plan = {
        "candidate_id": candidate.candidate_id,
        "plan_frame": context.plan_frame,
        "plan_unit": context.plan_unit,
        "frame_evidence_status": context.frame_evidence_status,
        "unit_evidence_status": context.unit_evidence_status,
        "topology_sha256": context.frozen_topology.get("sha256"),
        "plan_points": points,
        "plan_sha256": deterministic_sha256(points),
    }
    explicit_route_operand = (
        "EXPLICIT_CANDIDATE_ROUTE_OPERAND_BINDING" in context.mapping_provenance
    )
    if explicit_route_operand:
        matching_roles = tuple(
            role
            for role, target in context.branch_task_equivalence_classes.items()
            if str(target) == binding.symbolic_target_id
        )
        if len(matching_roles) != 1:
            raise RuntimeError(BLOCKED_SHADOW_ROUTE_MAPPING_CONTEXT_INCOMPLETE)
        mapped = {
            "schema_version": "driveclarify.explicit-route-operand-mapping.v1",
            "mapping_label": matching_roles[0],
            "evidence_sha256": deterministic_sha256(
                {
                    "candidate_id": candidate.candidate_id,
                    "interpretation_id": candidate.interpretation_id,
                    "semantic_role": matching_roles[0],
                    "symbolic_target_id": binding.symbolic_target_id,
                    "topology_sha256": context.frozen_topology.get("sha256"),
                }
            ),
            "reason_codes": (
                "AUTHORITATIVE_CANDIDATE_ROUTE_OPERAND_USED",
                "NO_IMMEDIATE_PLAN_DIVERGENCE_INFERRED",
            ),
        }
        mapper_identity = "ExplicitCandidateRouteOperandBindingV1"
        mapper_invocation_count = 0
    else:
        mapper = StaticBranchPlanMapperV1(context.mapping_threshold_contract)
        mapped = mapper.map_plan(context.frozen_topology, plan, context.ego_transform)
        mapper_identity = "StaticBranchPlanMapperV1"
        mapper_invocation_count = 1
    if candidate.route != original_route or candidate.speed != original_speed:
        raise RuntimeError("SHADOW_COUNTERFACTUAL_PRODUCER_INPUT_MUTATION_DETECTED")

    mapped_branch = str(mapped.get("mapping_label"))
    known = mapped_branch in context.branch_task_equivalence_classes
    mapped_target = (
        str(context.branch_task_equivalence_classes[mapped_branch]) if known else None
    )
    route_provenance = (
        SHADOW_ROUTE_PROVENANCE,
        mapper_identity,
        *tuple(str(item) for item in context.mapping_provenance),
        "DIAGNOSTIC_ONLY",
    )
    route_reasons = tuple(str(item) for item in mapped.get("reason_codes", ()))
    if known:
        route_status = "AVAILABLE"
        route_grade = SHADOW_EVIDENCE_GRADE
        target_type: str | None = context.symbolic_target_type
    else:
        route_status = "NOT_CURRENTLY_AVAILABLE"
        route_grade = NOT_AVAILABLE_EVIDENCE_GRADE
        target_type = None
        route_reasons = tuple(
            sorted(set((*route_reasons, "SHADOW_ROUTE_MAPPING_NOT_AVAILABLE")))
        )
    route_evidence = ShadowRouteSemanticEvidence(
        status=route_status,
        mapped_branch=mapped_branch if known else None,
        mapped_symbolic_target_type=target_type,
        mapped_symbolic_target_id=mapped_target,
        evidence_grade=route_grade,
        allowed_usage_purposes=DIAGNOSTIC_USAGE,
        provenance=route_provenance,
        mapper_identity=mapper_identity,
        mapper_schema_version=(
            str(mapped.get("schema_version")) if mapped.get("schema_version") else None
        ),
        mapper_evidence_sha256=str(mapped.get("evidence_sha256", "")),
        reason_codes=route_reasons,
    )
    speed_consequence = evaluate_pid_desired_speed_v0(
        candidate.speed,
        contract=speed_contract,
    )
    speed_available = speed_consequence.status == SPEED_CONSEQUENCE_AVAILABLE
    speed_grade = (
        SHADOW_EVIDENCE_GRADE if speed_available else NOT_AVAILABLE_EVIDENCE_GRADE
    )
    speed_provenance = (
        SHADOW_ROUTE_PROVENANCE,
        SHADOW_SPEED_PROVENANCE,
        "DIAGNOSTIC_ONLY",
    )
    speed_reason_codes = tuple(
        dict.fromkeys(
            (
                *speed_consequence.reason_codes,
                "NO_PHYSICAL_SAFETY_INFERENCE",
                "NO_SPEED_THRESHOLD_TTC_OR_COLLISION_HEURISTIC",
            )
        )
    )
    longitudinal_evidence = LongitudinalTaskEvidence(
        action_candidate_id=candidate.candidate_id,
        status=speed_consequence.status,
        semantic_type=speed_consequence.semantic_type if speed_available else None,
        semantic_value=(
            float(speed_consequence.semantic_value)
            if speed_available and speed_consequence.semantic_value is not None
            else None
        ),
        frame=PID_DESIRED_SPEED_FRAME if speed_available else None,
        unit=PID_DESIRED_SPEED_UNIT if speed_available else None,
        temporal_basis=(
            PID_DESIRED_SPEED_TEMPORAL_BASIS if speed_available else None
        ),
        source=PID_DESIRED_SPEED_SOURCE,
        evidence_grade=speed_grade,
        allowed_usage_purposes=DIAGNOSTIC_USAGE,
        provenance=speed_provenance,
        authorization_eligible=False,
        safety_critical_eligible=False,
        reason_codes=speed_reason_codes,
    )
    speed_digest = speed_consequence.source_digest
    speed_evidence = ShadowSpeedSemanticEvidence(
        status=longitudinal_evidence.status,
        semantic_type=longitudinal_evidence.semantic_type,
        semantic_value=longitudinal_evidence.semantic_value,
        frame=longitudinal_evidence.frame,
        unit=longitudinal_evidence.unit,
        temporal_basis=longitudinal_evidence.temporal_basis,
        evidence_grade=longitudinal_evidence.evidence_grade,
        allowed_usage_purposes=DIAGNOSTIC_USAGE,
        provenance=speed_provenance,
        raw_speed_available=candidate.speed is not None,
        raw_speed_digest=speed_digest,
        reason_codes=speed_reason_codes,
    )
    source_artifacts = tuple(
        dict.fromkeys(
            (
                *tuple(str(item) for item in context.source_artifacts),
                str(candidate.candidate_input_digest),
                str(candidate.candidate_output_digest),
                str(context.frozen_topology.get("sha256")),
                str(context.mapping_threshold_contract.get("sha256")),
                str(mapped.get("evidence_sha256", "")),
            )
        )
    )
    counterfactual = CounterfactualPlanEvidence(
        action_candidate_id=candidate.candidate_id,
        source_observation_id=candidate.source_observation_id,
        mapped_symbolic_target_type=target_type,
        mapped_symbolic_target_id=mapped_target,
        plan_frame="SYMBOLIC_PLAN_TARGET" if known else None,
        plan_unit="IDENTIFIER" if known else None,
        provenance=route_provenance,
        source_artifacts=source_artifacts,
        reason_codes=tuple(
            sorted(
                set(
                    (
                        *route_reasons,
                        *speed_reason_codes,
                    )
                )
            )
        ),
        longitudinal_task_evidence=longitudinal_evidence,
    )
    return ShadowCounterfactualEvidenceResult(
        candidate_id=candidate.candidate_id,
        interpretation_id=candidate.interpretation_id,
        source_observation_id=candidate.source_observation_id,
        source_frame_id=context.source_frame_id,
        interpretation_task_binding=binding,
        route=route_evidence,
        speed=speed_evidence,
        counterfactual_plan_evidence=counterfactual,
        candidate_route_digest=canonical_sha256(candidate.route),
        candidate_speed_digest=speed_digest,
        diagnostic_only=True,
        authorization_eligible=False,
        safety_critical_eligible=False,
        mapper_invocation_count=mapper_invocation_count,
    )


__all__ = [
    "BLOCKED_SHADOW_CANDIDATE_ROUTE_FRAME_UNIT_NOT_VERIFIED",
    "BLOCKED_SHADOW_INTERPRETATION_TASK_BINDING_NOT_AVAILABLE",
    "BLOCKED_SHADOW_ROUTE_MAPPING_CONTEXT_INCOMPLETE",
    "DIAGNOSTIC_USAGE",
    "NOT_AVAILABLE_EVIDENCE_GRADE",
    "PID_DESIRED_SPEED_FRAME",
    "PID_DESIRED_SPEED_SOURCE",
    "PID_DESIRED_SPEED_TEMPORAL_BASIS",
    "PID_DESIRED_SPEED_UNIT",
    "SHADOW_EVIDENCE_GRADE",
    "SHADOW_ROUTE_PROVENANCE",
    "SHADOW_SPEED_PROVENANCE",
    "SIMLINGO_STOP_SUCCESS_V0",
    "ShadowCounterfactualEvidenceResult",
    "ShadowCounterfactualMappingContext",
    "ShadowRouteSemanticEvidence",
    "ShadowSpeedSemanticEvidence",
    "bind_candidate_to_counterfactual_evidence",
    "build_longitudinal_task_outcomes_v0",
    "build_route_stop_counterfactual_outcome_matrix_v0",
]
