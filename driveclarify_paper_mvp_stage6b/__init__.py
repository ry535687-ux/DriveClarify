"""Unified Stage 6B runtime contracts for the eight frozen paper-MVP methods.

The package is deliberately split into policy-visible and environment/evaluator
modules.  Runtime policy code consumes only observations, natural-language
answers, and delivered information events.  Passenger intent and post-episode
gold joins stay behind explicit provider/evaluator interfaces.
"""

from .contracts import (
    EvidenceStatus,
    FailureClass,
    ForwardAccounting,
    Freshness,
    LabelFirewallCounters,
    MethodOutput,
    PlanSource,
)
from .method_adapter import UnifiedMethodAdapter

__all__ = [
    "EvidenceStatus",
    "FailureClass",
    "ForwardAccounting",
    "Freshness",
    "LabelFirewallCounters",
    "MethodOutput",
    "PlanSource",
    "UnifiedMethodAdapter",
]
