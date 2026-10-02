"""Prospective formal outcome normalization; production policy is untouched."""

from .adapter import (
    FALLBACK_NORMALIZATION_RULE,
    FALLBACK_WAIT_SUBTYPE,
    NORMALIZATION_VERSION,
    FormalOutcomeNormalizationError,
    fallback_wait_evidence_from_stage6a_audit,
    normalize_policy_outcome,
)

__all__ = [
    "FALLBACK_NORMALIZATION_RULE",
    "FALLBACK_WAIT_SUBTYPE",
    "NORMALIZATION_VERSION",
    "FormalOutcomeNormalizationError",
    "fallback_wait_evidence_from_stage6a_audit",
    "normalize_policy_outcome",
]
