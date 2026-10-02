"""Default-off temporal evidence measurement for prospective RQ2-T.

This package is observational.  It has no model, planner, controller, PID,
route-planner mutation, or VehicleControl API.
"""

from .measurement import (
    DeadlineContract,
    adapt_production_history_row,
    clarification_actionable,
    epistemic_evidence_sufficient,
    finalize_episode,
)
from .observer import FEATURE_FLAG, RuntimeTemporalEvidenceObserver
from .types import EvidenceStatus, OpportunityWindowStatus, QueryNecessityGold

__all__ = [
    "DeadlineContract",
    "EvidenceStatus",
    "FEATURE_FLAG",
    "OpportunityWindowStatus",
    "QueryNecessityGold",
    "RuntimeTemporalEvidenceObserver",
    "adapt_production_history_row",
    "clarification_actionable",
    "epistemic_evidence_sufficient",
    "finalize_episode",
]
