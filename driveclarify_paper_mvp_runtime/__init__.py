"""DriveClarify-only Stage 6A runtime seam.

This package performs no simulator, model, PID, control, catalog, or evaluator
I/O.  Runtime capabilities enter only through explicit callbacks.
"""

from .authority import (
    AuthorityArmResult,
    PersistentPrePidAuthority,
    PrePidPlanSelection,
)
from .candidate_generation import (
    GENERATOR_CONTRACT_SHA256,
    GENERATOR_ID,
    RuntimeCandidateGenerator,
)
from .contracts import (
    CandidateGenerationAudit,
    CandidateGenerationResult,
    CandidatePlan,
    ConsequenceEvaluation,
    EgoState,
    ExecutionBoundaryFacts,
    FORBIDDEN_RUNTIME_FIELDS,
    HardRuleEvidence,
    PolicyEpisodeInput,
    RouteContext,
    RuntimeCandidate,
    Stage6AContractError,
    VisionObservation,
    VisualReference,
)
from .orchestrator import Stage6AOrchestrationResult, Stage6AOrchestrator
from .simlingo_binding import (
    CandidateForwardResult,
    CarlaOneTickHardRuleMonitor,
    CarlaOneTickPhysicalSafetyMonitor,
    FailClosedHardRuleMonitor,
    FailClosedPhysicalSafetyMonitor,
    LiveCandidateRouteOperand,
    LiveSceneObservation,
    STAGE6A_AUDIT_FILENAME,
    STAGE6A_AUTHORITY_ENV,
    STAGE6A_LIVE_ENV,
    STAGE6A_SCHEMA,
    SimLingoCandidateForwardProvider,
    Stage6ASimLingoBinding,
    build_runtime_consequence_evaluation,
    build_stage6a_simlingo_binding,
    read_live_carla_actor_projection,
)


__all__ = [
    "AuthorityArmResult",
    "CandidateGenerationAudit",
    "CandidateGenerationResult",
    "CandidateForwardResult",
    "CandidatePlan",
    "CarlaOneTickHardRuleMonitor",
    "CarlaOneTickPhysicalSafetyMonitor",
    "ConsequenceEvaluation",
    "EgoState",
    "ExecutionBoundaryFacts",
    "FORBIDDEN_RUNTIME_FIELDS",
    "FailClosedHardRuleMonitor",
    "FailClosedPhysicalSafetyMonitor",
    "GENERATOR_CONTRACT_SHA256",
    "GENERATOR_ID",
    "HardRuleEvidence",
    "LiveCandidateRouteOperand",
    "LiveSceneObservation",
    "PersistentPrePidAuthority",
    "PolicyEpisodeInput",
    "PrePidPlanSelection",
    "RouteContext",
    "RuntimeCandidate",
    "RuntimeCandidateGenerator",
    "STAGE6A_AUDIT_FILENAME",
    "STAGE6A_AUTHORITY_ENV",
    "STAGE6A_LIVE_ENV",
    "STAGE6A_SCHEMA",
    "SimLingoCandidateForwardProvider",
    "Stage6AContractError",
    "Stage6AOrchestrationResult",
    "Stage6AOrchestrator",
    "Stage6ASimLingoBinding",
    "VisionObservation",
    "VisualReference",
    "build_runtime_consequence_evaluation",
    "build_stage6a_simlingo_binding",
    "read_live_carla_actor_projection",
]
