"""DriveClarify persistent ambiguity runtime v1.

G0 exports are pure contract/state/evaluator components only.  Runtime control
integration remains absent until the CARLA evidence gate authorizes it.
"""

from .contracts import ContractValidator, ContractViolation
from .evidence_adapter import (
    CandidateCorridorInput,
    CommitmentBoundaryInput,
    CoordinateCalibrationInput,
    DecisionWindowEvidenceAdapter,
    RouteProjection,
)
from .evaluators import (
    ActionEligibility,
    CandidateReachability,
    CurrentActionEquivalenceInput,
    ManeuverOnsetInput,
    PlanCoverageInput,
    classify_candidate_relationship,
    evaluate_action_eligibility,
    evaluate_current_action_equivalence,
    evaluate_commitment_boundary_state,
    evaluate_decision_point,
    evaluate_latest_safe_clarification,
    evaluate_maneuver_onset,
    evaluate_plan_coverage,
    evaluate_recoverability,
    evaluate_route_distance_timing,
    evaluate_time_to_divergence,
)
from .store import (
    CandidateIdentity,
    PersistentAmbiguityStore,
    SharedActionReference,
    StoreEvent,
)
from .method_v1_decision import (
    CandidateConvergenceEvidence,
    ConvergenceEvidenceKind,
    MethodAuthorizationStatus,
    MethodControlSource,
    MethodDecisionEnvelope,
    MethodDecisionLabel,
    build_method_m3_receipt,
    build_method_m3_transaction,
    method_m3_receipt_from_results,
    m3_event_for_decision,
)

__all__ = [
    "ActionEligibility",
    "CandidateIdentity",
    "CandidateConvergenceEvidence",
    "CandidateCorridorInput",
    "CandidateReachability",
    "CommitmentBoundaryInput",
    "ConvergenceEvidenceKind",
    "ContractValidator",
    "ContractViolation",
    "CurrentActionEquivalenceInput",
    "CoordinateCalibrationInput",
    "DecisionWindowEvidenceAdapter",
    "ManeuverOnsetInput",
    "MethodAuthorizationStatus",
    "MethodControlSource",
    "MethodDecisionEnvelope",
    "MethodDecisionLabel",
    "PersistentAmbiguityStore",
    "PlanCoverageInput",
    "RouteProjection",
    "SharedActionReference",
    "StoreEvent",
    "classify_candidate_relationship",
    "build_method_m3_receipt",
    "build_method_m3_transaction",
    "method_m3_receipt_from_results",
    "evaluate_action_eligibility",
    "evaluate_current_action_equivalence",
    "evaluate_commitment_boundary_state",
    "evaluate_decision_point",
    "evaluate_latest_safe_clarification",
    "evaluate_maneuver_onset",
    "evaluate_plan_coverage",
    "evaluate_recoverability",
    "evaluate_route_distance_timing",
    "evaluate_time_to_divergence",
    "m3_event_for_decision",
]
