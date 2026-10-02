"""RQ2 T-MVP experiment infrastructure.

This package is deliberately unreachable from the production agent unless an
experiment harness imports and wires it.  Importing it performs no simulator,
model, planner, controller, filesystem, or GPU work.
"""

from .models import (
    CandidateFeasibility,
    CandidateLifecycle,
    GlobalTask,
    NavigationCandidate,
    ObservableCommitment,
    ObservableCommitmentState,
    PlanState,
)

__all__ = [
    "CandidateFeasibility",
    "CandidateLifecycle",
    "GlobalTask",
    "NavigationCandidate",
    "ObservableCommitment",
    "ObservableCommitmentState",
    "PlanState",
]
