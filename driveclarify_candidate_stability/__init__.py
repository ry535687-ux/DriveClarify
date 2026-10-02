"""CPU-only candidate stability and offline/shadow interfaces.

This package intentionally has no CARLA, SimLingo, torch, CUDA, PID or control
dependency.  It consumes recorded candidate arrays only.
"""

from .determinism import (
    DeterministicInferenceConfig,
    PythonRandomBackend,
    StandardLibraryInputIsolation,
    StandardLibraryStateIsolation,
    audit_module_training_state,
)
from .gate import CandidateStabilityGate, GateConfig
from .live_protocol import (
    DeterministicCandidateRunner,
    LiveCandidateBackend,
    PlanOnlyOutput,
)
from .runner import FixtureInterpretationGenerator, RecordedCandidateRunner
from .types import (
    CandidateBatchContext,
    CandidateRequest,
    CandidateResult,
    GateStatus,
    StabilityClassification,
)

__all__ = [
    "CandidateBatchContext",
    "CandidateRequest",
    "CandidateResult",
    "CandidateStabilityGate",
    "DeterministicCandidateRunner",
    "DeterministicInferenceConfig",
    "FixtureInterpretationGenerator",
    "GateConfig",
    "GateStatus",
    "LiveCandidateBackend",
    "PlanOnlyOutput",
    "PythonRandomBackend",
    "RecordedCandidateRunner",
    "StandardLibraryInputIsolation",
    "StandardLibraryStateIsolation",
    "StabilityClassification",
    "audit_module_training_state",
]
