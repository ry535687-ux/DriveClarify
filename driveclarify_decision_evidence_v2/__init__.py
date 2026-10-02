"""Versioned short-horizon decision evidence contract V2."""

from .evaluator import (
    DecisionEvidenceInputV2,
    LocalPlanV2,
    TimingCalibrationV2,
    evaluate_decision_evidence_v2,
)
from .m2b import DecisionContextV2, DecisionV2, decide_v2
from .types import (
    CandidateRelationshipV2,
    ClarificationUrgencyV2,
    DecisionEvidenceBundleV2,
    EvidenceAvailabilityV2,
    FutureObligationRowV2,
)

FEATURE_FLAG = "DRIVECLARIFY_DECISION_EVIDENCE_CONTRACT_V2"
CONTRACT_VERSION = "DECISION_EVIDENCE_CONTRACT_V2.0"

__all__ = [
    "CONTRACT_VERSION",
    "FEATURE_FLAG",
    "CandidateRelationshipV2",
    "ClarificationUrgencyV2",
    "DecisionContextV2",
    "DecisionEvidenceBundleV2",
    "DecisionEvidenceInputV2",
    "DecisionV2",
    "EvidenceAvailabilityV2",
    "FutureObligationRowV2",
    "LocalPlanV2",
    "TimingCalibrationV2",
    "decide_v2",
    "evaluate_decision_evidence_v2",
]

