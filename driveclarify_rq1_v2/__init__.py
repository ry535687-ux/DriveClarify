"""RQ1-V2 consequence-selective clarification primitives."""

from .consequence import (
    AmbiguityStatus,
    ConsequenceRelation,
    GateAction,
    GateDecision,
    TaskSignature,
    compare_task_signatures,
    consequence_gate,
)

__all__ = [
    "AmbiguityStatus",
    "ConsequenceRelation",
    "GateAction",
    "GateDecision",
    "TaskSignature",
    "compare_task_signatures",
    "consequence_gate",
]
