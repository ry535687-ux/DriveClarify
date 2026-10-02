"""Consequence-aware candidate relationship and decision contracts."""

from .protocol import (
    CandidateRelationship,
    ClassificationEvidence,
    ClassificationResult,
    DecisionEvidence,
    DecisionResult,
    TriState,
    classify_candidate_relationship,
    evaluate_decision_contract,
)

__all__ = [
    "CandidateRelationship",
    "ClassificationEvidence",
    "ClassificationResult",
    "DecisionEvidence",
    "DecisionResult",
    "TriState",
    "classify_candidate_relationship",
    "evaluate_decision_contract",
]
