"""Candidate-specific semantic-plan alignment adapter to the existing M1 rule mapper.

The M1 learned comparator is deliberately not called: its v0 contract accepts one shared Task
specification, which is invalid for pre-clarification candidate-specific bindings.  Each candidate
is instead evaluated against only its own symbolic binding through the existing deterministic Task
atom mapper/evaluator.  No geometry or physical safety inference occurs here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from driveclarify_consequence.task_atoms import evaluate_task_atom, map_candidate_plan_to_task_atoms

from .interaction_contracts import (
    BindingStatus,
    CandidateSpecificTaskBinding,
    EVIDENCE_DESIGNATION,
    SerializableContract,
)


@dataclass(frozen=True)
class CandidatePlanRecord(SerializableContract):
    candidate_id: str
    source_observation_id: str | None
    mapped_symbolic_target_type: str | None
    mapped_symbolic_target_id: str | None
    plan_frame: str | None
    plan_unit: str | None
    provenance: tuple[str, ...]
    source_artifacts: tuple[str, ...]
    schema_version: str = "driveclarify.candidate_plan_semantics.v0"


@dataclass(frozen=True)
class CandidateSemanticPlanAlignment(SerializableContract):
    candidate_id: str
    task_binding_candidate_id: str
    alignment_status: str
    symbolic_target_type: str | None
    symbolic_target_id: str | None
    mapped_symbolic_target_type: str | None
    mapped_symbolic_target_id: str | None
    m1_rule_mapper_evaluation: Mapping[str, Any] | None
    provenance: tuple[str, ...]
    reason_trace: tuple[str, ...]
    schema_version: str = "driveclarify.candidate_semantic_plan_alignment.v0"


@dataclass(frozen=True)
class SemanticPlanBridgeResult(SerializableContract):
    candidate_a_alignment: CandidateSemanticPlanAlignment
    candidate_b_alignment: CandidateSemanticPlanAlignment
    symbolic_target_relation: str
    pair_consequence_relation: str
    m1_learned_pair_comparator_status: str
    m1_rule_mapper_mode: str
    reason_trace: tuple[str, ...]
    mode: str
    authorization_eligible: bool
    safety_critical_eligible: bool
    control_authorized: bool
    physical_safety_inference_performed: bool
    schema_version: str = "driveclarify.candidate_specific_semantic_plan_bridge.v0"


_PLAN_PROVENANCE = frozenset(
    {
        "SYNTHETIC_PLAN_TEST_ONLY",
        "REAL_RECORDED_UNLABELED",
        "HAND_AUTHORED_ORACLE_FIXTURE",
    }
)


def _task_atom_contract(binding: CandidateSpecificTaskBinding) -> tuple[str, str, tuple[str, ...]]:
    if binding.task_family == "REFERENCE_GOAL":
        return "LANGUAGE_REFERENCE", "IDENTIFIER", ("target_evidence",)
    if binding.task_family == "DESTINATION_GOAL":
        return "REGISTERED_TOPOLOGY", "REGION_ID", ("plan_to_goal_transform", "target_evidence")
    if binding.task_family == "MANEUVER_BRANCH":
        return "TOPOLOGY_BRANCH", "BRANCH_ID", ("plan_to_maneuver_mapping", "target_evidence")
    return "UNKNOWN", "UNKNOWN", ()


class CandidateSpecificSemanticPlanBridge:
    """Align A-plan→A-binding and B-plan→B-binding, with hard UNKNOWN propagation."""

    def align_pair(
        self,
        candidate_a_plan: CandidatePlanRecord,
        candidate_a_task_binding: CandidateSpecificTaskBinding,
        candidate_b_plan: CandidatePlanRecord,
        candidate_b_task_binding: CandidateSpecificTaskBinding,
        evidence_masks: Mapping[str, Mapping[str, bool]],
        provenance: tuple[str, ...],
    ) -> SemanticPlanBridgeResult:
        alignment_a = self._align_one(
            candidate_a_plan,
            candidate_a_task_binding,
            evidence_masks.get(candidate_a_plan.candidate_id),
        )
        alignment_b = self._align_one(
            candidate_b_plan,
            candidate_b_task_binding,
            evidence_masks.get(candidate_b_plan.candidate_id),
        )
        target_relation = self._target_relation(
            candidate_a_task_binding,
            candidate_b_task_binding,
        )
        statuses = (alignment_a.alignment_status, alignment_b.alignment_status)
        if "UNKNOWN" in statuses:
            pair = "UNKNOWN"
            pair_reason = "CANDIDATE_ALIGNMENT_UNKNOWN_PROPAGATED"
        elif statuses[0] != statuses[1]:
            pair = "TASK_CRITICAL"
            pair_reason = "CANDIDATE_SPECIFIC_ALIGNMENT_STATES_DIFFER"
        else:
            pair = "TASK_EQUIVALENT"
            pair_reason = "CANDIDATE_SPECIFIC_ALIGNMENT_STATES_MATCH"
        reasons = tuple(
            sorted(
                {
                    "M1_LEARNED_SHARED_TASK_CONTRACT_NOT_APPLICABLE",
                    "NO_SHARED_TASK_ANCHOR_CONSTRUCTED",
                    "SYMBOLIC_TARGET_RELATION_IS_NOT_PHYSICAL_RISK",
                    pair_reason,
                    *alignment_a.reason_trace,
                    *alignment_b.reason_trace,
                }
            )
        )
        return SemanticPlanBridgeResult(
            candidate_a_alignment=alignment_a,
            candidate_b_alignment=alignment_b,
            symbolic_target_relation=target_relation,
            pair_consequence_relation=pair,
            m1_learned_pair_comparator_status="NOT_APPLICABLE",
            m1_rule_mapper_mode="CANDIDATE_SPECIFIC_DETERMINISTIC_TASK_ATOM_EVALUATION",
            reason_trace=(*reasons, *provenance, *EVIDENCE_DESIGNATION),
            mode="DIAGNOSTIC_ONLY",
            authorization_eligible=False,
            safety_critical_eligible=False,
            control_authorized=False,
            physical_safety_inference_performed=False,
        )

    @staticmethod
    def _align_one(
        plan: CandidatePlanRecord,
        binding: CandidateSpecificTaskBinding,
        evidence_mask: Mapping[str, bool] | None,
    ) -> CandidateSemanticPlanAlignment:
        reasons: list[str] = []
        mask = evidence_mask if isinstance(evidence_mask, Mapping) else {}
        required_mask = ("plan", "task_binding", "provenance", "alignment")
        missing = [field for field in required_mask if mask.get(field) is not True]
        if missing:
            reasons.extend(f"EVIDENCE_{field.upper()}_UNAVAILABLE" for field in missing)
        if plan.candidate_id != binding.source_candidate_id:
            reasons.append("PLAN_TASK_BINDING_CANDIDATE_ID_MISMATCH")
        if binding.binding_status is not BindingStatus.BOUND:
            reasons.append("CANDIDATE_TASK_BINDING_NOT_AVAILABLE")
        if not binding.symbolic_target_id:
            reasons.append("CANDIDATE_SYMBOLIC_TARGET_MISSING")
        if not plan.mapped_symbolic_target_id or not plan.mapped_symbolic_target_type:
            reasons.append("PLAN_SYMBOLIC_ALIGNMENT_NOT_AVAILABLE")
        if plan.plan_frame != "SYMBOLIC_PLAN_TARGET" or plan.plan_unit != "IDENTIFIER":
            reasons.append("PLAN_SEMANTIC_PROVENANCE_NOT_ESTABLISHED")
        if not _PLAN_PROVENANCE.intersection(plan.provenance):
            reasons.append("PLAN_PROVENANCE_INVALID_OR_MISSING")
        if reasons:
            return CandidateSemanticPlanAlignment(
                candidate_id=plan.candidate_id,
                task_binding_candidate_id=binding.source_candidate_id,
                alignment_status="UNKNOWN",
                symbolic_target_type=binding.symbolic_target_type,
                symbolic_target_id=binding.symbolic_target_id,
                mapped_symbolic_target_type=plan.mapped_symbolic_target_type,
                mapped_symbolic_target_id=plan.mapped_symbolic_target_id,
                m1_rule_mapper_evaluation=None,
                provenance=tuple((*plan.provenance, *binding.provenance)),
                reason_trace=tuple(sorted(set(reasons))),
            )

        frame, unit, dependencies = _task_atom_contract(binding)
        registry = [
            {
                "atom_id": f"candidate_specific_target::{binding.source_candidate_id}",
                "atom_type": binding.task_family,
                "target_id": binding.symbolic_target_id,
                "target_class": None,
                "frame": frame,
                "unit": unit,
                "dependencies": list(dependencies),
            }
        ]
        candidate_record = {
            "candidate_id": plan.candidate_id,
            "source_observation_id": plan.source_observation_id,
            "task_bindings": [
                {
                    "atom_id": registry[0]["atom_id"],
                    "mapped_target_id": plan.mapped_symbolic_target_id,
                    "mapped_target_class": None,
                    "evidence_grade": "SUPPORTED_BUT_INCOMPLETE",
                    "allowed_usage_purposes": ["DIAGNOSTIC_ONLY"],
                    "dependency_statuses": {key: True for key in dependencies},
                    "status": "AVAILABLE",
                    "reason_code": None,
                    "source_artifacts": list(plan.source_artifacts),
                }
            ],
        }
        atoms = map_candidate_plan_to_task_atoms(candidate_record, registry, str(plan.source_observation_id or ""))
        evaluation = evaluate_task_atom(atoms[0])
        status = str(evaluation["status"])
        type_matches = plan.mapped_symbolic_target_type == binding.symbolic_target_type
        if status in {"PASS", "FAIL"} and not type_matches:
            status = "FAIL"
            reasons.append("SYMBOLIC_TARGET_TYPE_MISMATCH")
        reasons.append(str(evaluation["reason_code"]))
        reasons.append("M1_RULE_MAPPER_CALLED_WITH_CANDIDATE_SPECIFIC_TARGET_ONLY")
        return CandidateSemanticPlanAlignment(
            candidate_id=plan.candidate_id,
            task_binding_candidate_id=binding.source_candidate_id,
            alignment_status=status,
            symbolic_target_type=binding.symbolic_target_type,
            symbolic_target_id=binding.symbolic_target_id,
            mapped_symbolic_target_type=plan.mapped_symbolic_target_type,
            mapped_symbolic_target_id=plan.mapped_symbolic_target_id,
            m1_rule_mapper_evaluation=evaluation,
            provenance=tuple((*plan.provenance, *binding.provenance)),
            reason_trace=tuple(sorted(set(reasons))),
        )

    @staticmethod
    def _target_relation(
        left: CandidateSpecificTaskBinding,
        right: CandidateSpecificTaskBinding,
    ) -> str:
        if (
            left.binding_status is not BindingStatus.BOUND
            or right.binding_status is not BindingStatus.BOUND
            or not left.symbolic_target_id
            or not right.symbolic_target_id
        ):
            return "UNKNOWN"
        return (
            "SAME_TARGET"
            if (left.symbolic_target_type, left.symbolic_target_id)
            == (right.symbolic_target_type, right.symbolic_target_id)
            else "DISTINCT_TARGET"
        )


def plan_record_for_candidate(
    candidate_id: str,
    source_observation_id: str | None,
    plan_fixture: Mapping[str, Any] | None,
) -> CandidatePlanRecord:
    fixture = plan_fixture if isinstance(plan_fixture, Mapping) else {}
    return CandidatePlanRecord(
        candidate_id=candidate_id,
        source_observation_id=source_observation_id,
        mapped_symbolic_target_type=fixture.get("mapped_symbolic_target_type"),
        mapped_symbolic_target_id=fixture.get("mapped_symbolic_target_id"),
        plan_frame=fixture.get("plan_frame"),
        plan_unit=fixture.get("plan_unit"),
        provenance=tuple(fixture.get("provenance", ())),
        source_artifacts=tuple(fixture.get("source_artifacts", ())),
    )
