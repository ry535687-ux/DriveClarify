"""Evaluator-only Stage 6A candidate leakage audit."""

from .contracts import (
    AUDIT_FILENAME,
    AUDIT_SCHEMA_VERSION,
    CandidateGenerationAuditReport,
    REQUIRED_SCENARIO_COUNT,
    RecordedCandidateGeneration,
    ScenarioCandidateLeakageAudit,
)
from .evaluator import (
    evaluate_candidate_generation_records,
    run_candidate_generation_audit,
)


__all__ = [
    "AUDIT_FILENAME",
    "AUDIT_SCHEMA_VERSION",
    "CandidateGenerationAuditReport",
    "REQUIRED_SCENARIO_COUNT",
    "RecordedCandidateGeneration",
    "ScenarioCandidateLeakageAudit",
    "evaluate_candidate_generation_records",
    "run_candidate_generation_audit",
]
