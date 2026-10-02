"""Runtime-only summaries for offline v1 outputs.

Evaluation labels are intentionally accepted nowhere in this module.  The
separate development evaluator owns label access after runtime outputs exist.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from .canonical_ontology import stable_sha256


def summarize_runtime_outputs(outputs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    decisions = Counter(
        str(item.get("decision_recommendation", {}).get("decision", "MISSING"))
        for item in outputs
    )
    summary = {
        "schema_version": "driveclarify.integrated_runtime_summary.v1",
        "case_count": len(outputs),
        "structured_output_count": sum(isinstance(item, Mapping) and bool(item.get("schema_version")) for item in outputs),
        "decision_distribution": dict(sorted(decisions.items())),
        "contract_failure_case_count": sum(bool(item.get("contract_failures")) for item in outputs),
        "control_authorization_count": sum(bool(item.get("control_authorized")) for item in outputs),
        "vehicle_control_generation_count": sum(bool(item.get("vehicle_control_generated")) for item in outputs),
        "inferred_matrix_case_count": sum(item.get("matrix_source") == "INFERRED_FROM_PLAN_SEMANTIC_EVIDENCE" for item in outputs),
        "declared_stress_matrix_case_count": sum(item.get("matrix_source") == "DECLARED_SYMBOLIC_STRESS_INPUT" for item in outputs),
    }
    return {**summary, "summary_sha256": stable_sha256(summary)}

