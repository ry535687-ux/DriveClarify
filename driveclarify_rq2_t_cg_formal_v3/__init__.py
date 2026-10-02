"""Prospective Formal V3 evaluability and primary-gate contracts."""

from .evaluability import (
    EVALUABLE,
    INTEGRITY_INVALID,
    NON_EVALUABLE_NATIVE_NONCOMPLETION,
    ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE,
    classify_episode,
    primary_gate,
    prospective_gate_possible,
)

__all__ = (
    "EVALUABLE",
    "INTEGRITY_INVALID",
    "NON_EVALUABLE_NATIVE_NONCOMPLETION",
    "ZERO_EXPOSURE_INFRASTRUCTURE_FAILURE",
    "classify_episode",
    "primary_gate",
    "prospective_gate_possible",
)
