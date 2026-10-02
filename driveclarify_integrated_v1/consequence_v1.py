"""Candidate-specific authoritative symbolic consequence engine v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .canonical_ontology import CanonicalTaskBinding, canonical_value
from .plan_semantic_evidence import PlanSemanticEvidence
from .runtime_contracts import ContractFailure, RUNTIME_SCHEMA_VERSION, finite_number


class MatrixSource(str, Enum):
    INFERRED = "INFERRED_FROM_PLAN_SEMANTIC_EVIDENCE"
    DECLARED = "DECLARED_SYMBOLIC_STRESS_INPUT"


@dataclass(frozen=True)
class CounterfactualOutcomeCellV1:
    action_candidate_id: str
    hypothesis_candidate_id: str
    task_outcome: str
    evidence_status: str
    task_error_cost: float | None
    wrong_goal_indicator: bool | None
    matrix_source: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = RUNTIME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.task_outcome not in {"PASS", "FAIL", "UNKNOWN"}:
            raise ValueError("COUNTERFACTUAL_TASK_OUTCOME_INVALID")
        if self.task_outcome == "UNKNOWN":
            if self.task_error_cost is not None or self.wrong_goal_indicator is not None:
                raise ValueError("UNKNOWN_COUNTERFACTUAL_CELL_MUST_HAVE_NULL_SCALARS")
        elif self.task_error_cost is None or not isinstance(self.wrong_goal_indicator, bool):
            raise ValueError("KNOWN_COUNTERFACTUAL_CELL_REQUIRES_SCALARS")

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


@dataclass(frozen=True)
class CounterfactualOutcomeMatrixV1:
    candidate_ids: tuple[str, ...]
    cells: tuple[CounterfactualOutcomeCellV1, ...]
    matrix_source: str
    matrix_status: str
    consequence_inference_status: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = RUNTIME_SCHEMA_VERSION

    def cell(self, action_id: str, hypothesis_id: str) -> CounterfactualOutcomeCellV1 | None:
        return next((item for item in self.cells if item.action_candidate_id == action_id and item.hypothesis_candidate_id == hypothesis_id), None)

    @property
    def complete_and_known(self) -> bool:
        return len(self.cells) == len(self.candidate_ids) ** 2 and all(item.task_outcome != "UNKNOWN" for item in self.cells)

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


def _unknown_cell(action_id: str, hypothesis_id: str, source: MatrixSource, reasons: Sequence[str], provenance: Sequence[str] = ()) -> CounterfactualOutcomeCellV1:
    return CounterfactualOutcomeCellV1(
        action_id,
        hypothesis_id,
        "UNKNOWN",
        "INSUFFICIENT_SYMBOLIC_TASK_EVIDENCE",
        None,
        None,
        source.value,
        tuple(provenance),
        tuple(sorted(set((*reasons, "NO_PHYSICAL_SAFETY_INFERENCE")))),
    )


class CandidateSpecificConsequenceEngineV1:
    """Compare action plan i with hypothesis binding z for every ordered pair."""

    def evaluate_cell(
        self,
        action_id: str,
        hypothesis_id: str,
        evidence: PlanSemanticEvidence | None,
        binding: CanonicalTaskBinding | None,
        wrong_goal_cost: float | None,
    ) -> CounterfactualOutcomeCellV1:
        reasons: list[str] = []
        provenance: list[str] = []
        if evidence is None:
            reasons.append("PLAN_SEMANTIC_EVIDENCE_MISSING")
        else:
            provenance.extend(evidence.mapping_provenance)
            if evidence.candidate_id != action_id:
                reasons.append("ACTION_CANDIDATE_IDENTITY_MISMATCH")
            if evidence.mapping_status != "MAPPED_SYMBOLIC_TARGET":
                reasons.extend(evidence.reason_codes)
        if binding is None:
            reasons.append("CANDIDATE_SPECIFIC_TASK_BINDING_MISSING")
        else:
            provenance.extend(binding.provenance)
            if binding.candidate_id != hypothesis_id:
                reasons.append("HYPOTHESIS_CANDIDATE_IDENTITY_MISMATCH")
            if binding.binding_status != "BOUND":
                reasons.extend(binding.reason_codes)
        if wrong_goal_cost is None:
            reasons.append("DECLARED_TASK_ERROR_COST_MISSING")
        else:
            try:
                finite_number(wrong_goal_cost, "DECLARED_TASK_ERROR_COST_INVALID", minimum=0.0)
            except ValueError as exc:
                reasons.append(str(exc))
        if reasons:
            return _unknown_cell(action_id, hypothesis_id, MatrixSource.INFERRED, reasons, provenance)
        assert evidence is not None and binding is not None and wrong_goal_cost is not None
        matches = (
            evidence.symbolic_plan_target_type,
            evidence.symbolic_plan_target_id,
        ) == (binding.symbolic_target_type, binding.symbolic_target_id)
        return CounterfactualOutcomeCellV1(
            action_id,
            hypothesis_id,
            "PASS" if matches else "FAIL",
            "SUFFICIENT_SYMBOLIC_TASK_EVIDENCE",
            0.0 if matches else float(wrong_goal_cost),
            not matches,
            MatrixSource.INFERRED.value,
            tuple((*provenance, "CANDIDATE_SPECIFIC_SYMBOLIC_CROSS_EVALUATION")),
            (
                "SYMBOLIC_TASK_TARGET_MATCH" if matches else "SYMBOLIC_TASK_TARGET_MISMATCH",
                "TASK_MISMATCH_IS_NOT_PHYSICAL_SAFETY_RISK",
                "RAW_UNIT_NOT_INTERPRETED_AS_METRE",
            ),
        )

    def build_matrix(
        self,
        candidate_ids: Sequence[str],
        evidences: Mapping[str, PlanSemanticEvidence],
        bindings: Mapping[str, CanonicalTaskBinding],
        wrong_goal_costs: Mapping[str, float],
    ) -> CounterfactualOutcomeMatrixV1:
        ids = tuple(str(item) for item in candidate_ids)
        cells = tuple(
            self.evaluate_cell(action_id, hypothesis_id, evidences.get(action_id), bindings.get(hypothesis_id), wrong_goal_costs.get(hypothesis_id))
            for action_id in ids
            for hypothesis_id in ids
        )
        unknown = sum(item.task_outcome == "UNKNOWN" for item in cells)
        return CounterfactualOutcomeMatrixV1(
            ids,
            cells,
            MatrixSource.INFERRED.value,
            "COMPLETE_KNOWN" if unknown == 0 else "INCOMPLETE_OR_UNKNOWN",
            "EVALUABLE" if unknown == 0 else "NOT_EVALUABLE",
            ("AUTHORITATIVE_CANDIDATE_SPECIFIC_CONSEQUENCE_ENGINE_V1",),
            ("COUNTERFACTUAL_MATRIX_COMPLETE",) if unknown == 0 else (f"COUNTERFACTUAL_MATRIX_UNKNOWN_CELL_COUNT_{unknown}",),
        )


def declared_symbolic_matrix(record: Mapping[str, Any], candidate_ids: Sequence[str]) -> tuple[CounterfactualOutcomeMatrixV1 | None, tuple[ContractFailure, ...]]:
    """Validate a decision-only stress matrix without rebranding it as inference."""

    ids = tuple(str(item) for item in candidate_ids)
    raw = record.get("counterfactual_runtime_inputs")
    if not isinstance(raw, Mapping):
        return None, (ContractFailure("consequence", "STRUCTURED_CONTRACT_FAILURE", ("COUNTERFACTUAL_MATRIX_MISSING",), "Declared matrix is absent."),)
    raw_ids = raw.get("candidate_ids")
    if not isinstance(raw_ids, list) or tuple(str(item) for item in raw_ids) != ids:
        return None, (ContractFailure("consequence", "STRUCTURED_CONTRACT_FAILURE", ("COUNTERFACTUAL_MATRIX_CANDIDATE_SET_MISMATCH",), "Declared matrix candidate order does not match."),)
    failures: list[ContractFailure] = []
    cells: list[CounterfactualOutcomeCellV1] = []
    seen: set[tuple[str, str]] = set()
    for item in raw.get("cells", []) if isinstance(raw.get("cells"), list) else []:
        if not isinstance(item, Mapping):
            failures.append(ContractFailure("consequence", "STRUCTURED_CONTRACT_FAILURE", ("COUNTERFACTUAL_MATRIX_CELL_INVALID",), "Matrix cell is not an object."))
            continue
        action_id = str(item.get("action_candidate_id", ""))
        hypothesis_id = str(item.get("hypothesis_candidate_id", ""))
        identity = (action_id, hypothesis_id)
        if identity in seen or action_id not in ids or hypothesis_id not in ids:
            failures.append(ContractFailure("consequence", "STRUCTURED_CONTRACT_FAILURE", ("COUNTERFACTUAL_MATRIX_CELL_IDENTITY_INVALID",), "Matrix cell identity is invalid."))
            continue
        seen.add(identity)
        outcome = str(item.get("task_outcome", "UNKNOWN"))
        try:
            if outcome == "UNKNOWN":
                cost = None
                wrong = None
            elif outcome in {"PASS", "FAIL"}:
                cost = finite_number(item.get("task_error_cost"), "COUNTERFACTUAL_MATRIX_TASK_COST_INVALID", minimum=0.0)
                wrong_raw = item.get("wrong_goal_indicator")
                if not isinstance(wrong_raw, bool):
                    raise ValueError("COUNTERFACTUAL_MATRIX_WRONG_GOAL_INVALID")
                wrong = wrong_raw
            else:
                raise ValueError("COUNTERFACTUAL_MATRIX_OUTCOME_INVALID")
            cells.append(CounterfactualOutcomeCellV1(
                action_id,
                hypothesis_id,
                outcome,
                str(item.get("evidence_status", "DECLARED_SYMBOLIC")),
                cost,
                wrong,
                MatrixSource.DECLARED.value,
                tuple(str(value) for value in item.get("provenance", ("DECLARED_SYMBOLIC_STRESS_CONTRACT",))),
                tuple(str(value) for value in item.get("reason_codes", ("DECLARED_DECISION_STRESS_CELL",))),
            ))
        except (TypeError, ValueError) as exc:
            failures.append(ContractFailure("consequence", "STRUCTURED_CONTRACT_FAILURE", (str(exc),), "Declared matrix cell failed validation."))
    expected = {(left, right) for left in ids for right in ids}
    if seen != expected:
        failures.append(ContractFailure("consequence", "STRUCTURED_CONTRACT_FAILURE", ("COUNTERFACTUAL_MATRIX_INCOMPLETE",), "Declared matrix does not cover K by K identities."))
    if failures:
        return None, tuple(failures)
    unknown = sum(item.task_outcome == "UNKNOWN" for item in cells)
    return CounterfactualOutcomeMatrixV1(
        ids,
        tuple(cells),
        MatrixSource.DECLARED.value,
        "COMPLETE_KNOWN" if unknown == 0 else "INCOMPLETE_OR_UNKNOWN",
        "NOT_CONSEQUENCE_INFERENCE_DECISION_STRESS_ONLY",
        ("DECLARED_SYMBOLIC_STRESS_INPUT", "NOT_M1_PREDICTION", "NOT_PRIMARY_EVIDENCE"),
        ("DECLARED_MATRIX_VALIDATED",) if unknown == 0 else (f"DECLARED_MATRIX_UNKNOWN_CELL_COUNT_{unknown}",),
    ), ()

