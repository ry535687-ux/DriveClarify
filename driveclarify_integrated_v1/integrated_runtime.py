"""End-to-end CPU-only integrated offline runtime v1."""

from __future__ import annotations

import copy
import hashlib
import traceback
from typing import Any, Mapping

from .canonical_ontology import (
    ExternalToCanonicalAdapterV1,
    FailureKind,
    canonical_value,
    stable_sha256,
)
from .consequence_v1 import (
    CandidateSpecificConsequenceEngineV1,
    MatrixSource,
    declared_symbolic_matrix,
)
from .decision_v1 import DecisionRuntimeV1, control_eligibility, diagnostic_eligibility
from .language_v1 import CompositionalLanguageRuntimeV1
from .plan_semantic_evidence import plan_semantic_evidence_from_record
from .runtime_contracts import ContractFailure, RUNTIME_SCHEMA_VERSION, structured_fallback


def _bytes_sha256(value: Mapping[str, Any]) -> str:
    return stable_sha256(value)


class IntegratedRuntimeV1:
    def __init__(self) -> None:
        self.adapter = ExternalToCanonicalAdapterV1()
        self.language = CompositionalLanguageRuntimeV1()
        self.consequence = CandidateSpecificConsequenceEngineV1()
        self.decision = DecisionRuntimeV1()

    def run(self, raw_record: Mapping[str, Any]) -> dict[str, Any]:
        original = copy.deepcopy(dict(raw_record))
        input_hash = _bytes_sha256(original)
        canonical, traces = self.adapter.adapt(original)
        if canonical is None:
            failures = tuple(
                ContractFailure(
                    "canonicalization",
                    FailureKind.SCHEMA_TRANSLATION_FAILURE.value,
                    item.reason_codes,
                    "External schema translation failed closed.",
                )
                for item in traces
            )
            return self._failure_output(input_hash, traces, failures)
        try:
            language = self.language.parse(canonical)
            bindings = {item.candidate_id: item for item in language.candidate_bindings}
            plan_items = [item for item in canonical.get("candidate_plan_records", []) if isinstance(item, Mapping)]
            evidences = {
                str(item.get("candidate_id", "")): plan_semantic_evidence_from_record(item)
                for item in plan_items
                if item.get("candidate_id") is not None
            }
            ids = tuple(
                str(item)
                for item in (
                    canonical.get("counterfactual_runtime_inputs", {}).get("candidate_ids", [])
                    if isinstance(canonical.get("counterfactual_runtime_inputs"), Mapping)
                    else bindings.keys()
                )
            )
            if not ids:
                ids = tuple(bindings)
            wrong_cost_raw = canonical.get("declared_wrong_goal_costs", {})
            wrong_costs = {
                str(key): float(value)
                for key, value in wrong_cost_raw.items()
                if isinstance(wrong_cost_raw, Mapping) and isinstance(value, (int, float)) and not isinstance(value, bool)
            }
            inferred = self.consequence.build_matrix(ids, evidences, bindings, wrong_costs)
            declared = None
            declared_failures: tuple[ContractFailure, ...] = ()
            if "counterfactual_runtime_inputs" in canonical:
                declared, declared_failures = declared_symbolic_matrix(canonical, ids)
            decision_matrix = declared if declared is not None else inferred
            decision_failures = declared_failures if "counterfactual_runtime_inputs" in canonical else ()
            decision = self.decision.recommend(canonical, decision_matrix, decision_failures)
            contract_failures = list(decision.get("contract_failures", []))
            output = {
                "schema_version": RUNTIME_SCHEMA_VERSION,
                "runtime_input_sha256": input_hash,
                "canonicalization_trace": [item.to_dict() for item in traces],
                "language_result": language.to_dict(),
                "candidate_bindings": [item.to_dict() for item in language.candidate_bindings],
                "question_result": language.question_proposal.to_dict(),
                "plan_semantic_evidence": [evidences[key].to_dict() for key in sorted(evidences)],
                "consequence_result": {
                    "engine": "CandidateSpecificConsequenceEngineV1",
                    "legacy_learned_head_status": "LEGACY_RESEARCH_PROTOTYPE_NOT_DEPLOYED_IN_V1",
                    "inference_status": inferred.consequence_inference_status,
                    "matrix_source": inferred.matrix_source,
                    "matrix": inferred.to_dict(),
                    "declared_matrix_counted_as_inference": False,
                    "physical_safety_inference_performed": False,
                },
                "matrix_source": decision_matrix.matrix_source,
                "counterfactual_matrix": decision_matrix.to_dict(),
                "diagnostic_gate_result": diagnostic_eligibility(canonical).to_dict(),
                "control_gate_result": control_eligibility().to_dict(),
                "decision_recommendation": decision,
                "contract_failures": contract_failures,
                "control_authorized": False,
                "used_for_control": False,
                "vehicle_control_generated": False,
                "steering": None,
                "throttle": None,
                "brake": None,
            }
            if original != raw_record:
                raise RuntimeError("RUNTIME_INPUT_MUTATED")
            output["deterministic_output_sha256"] = stable_sha256(output)
            return output
        except Exception as exc:
            failure = ContractFailure(
                "integrated_runtime",
                "STRUCTURED_CONTRACT_FAILURE",
                (str(exc) or type(exc).__name__,),
                "Integrated runtime caught and structured an internal contract rejection.",
            )
            return self._failure_output(input_hash, traces, (failure,), traceback.format_exc())

    @staticmethod
    def _failure_output(input_hash: str, traces: Any, failures: tuple[ContractFailure, ...], traceback_text: str | None = None) -> dict[str, Any]:
        gate = None
        decision = structured_fallback(failures, gate=gate)
        output = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "runtime_input_sha256": input_hash,
            "canonicalization_trace": [item.to_dict() for item in traces],
            "language_result": None,
            "candidate_bindings": [],
            "question_result": None,
            "plan_semantic_evidence": [],
            "consequence_result": {"inference_status": "NOT_EVALUABLE", "matrix_source": MatrixSource.INFERRED.value},
            "matrix_source": None,
            "counterfactual_matrix": None,
            "diagnostic_gate_result": None,
            "control_gate_result": control_eligibility().to_dict(),
            "decision_recommendation": decision,
            "contract_failures": [item.to_dict() for item in failures],
            "runtime_exception_trace_sha256": None if traceback_text is None else hashlib.sha256(traceback_text.encode("utf-8")).hexdigest(),
            "control_authorized": False,
            "used_for_control": False,
            "vehicle_control_generated": False,
            "steering": None,
            "throttle": None,
            "brake": None,
        }
        output["deterministic_output_sha256"] = stable_sha256(output)
        return output


def run_integrated_runtime(raw_record: Mapping[str, Any]) -> dict[str, Any]:
    return IntegratedRuntimeV1().run(raw_record)
