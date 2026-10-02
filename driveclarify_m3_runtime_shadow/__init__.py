"""Public API for shadow-only runtime candidate wrapper contracts.

The package is intentionally lazy.  SimLingo's production environment uses
Python 3.8, while the existing M3 implementation requires newer dataclass
features when it is actually invoked.  Lazy exports let the Python-3.8 live
candidate hook load without importing M3; the unchanged M2B/M3 chain is then
executed by the repository's compatible Python runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


def _exports(module: str, names: tuple[str, ...], target: dict[str, str]) -> None:
    for name in names:
        target[name] = module


_EXPORTS: dict[str, str] = {}
_exports(
    ".candidate_wrapper",
    (
        "BLOCKED_SHADOW_CANDIDATE_BACKEND_NOT_ISOLATABLE",
        "BLOCKED_SHADOW_CANDIDATE_BACKEND_SIGNATURE_UNRESOLVED",
        "BLOCKED_SHADOW_CANDIDATE_EXECUTION_FAILED",
        "run_candidates",
    ),
    _EXPORTS,
)
_exports(
    ".m2b_binding",
    (
        "BLOCKED_EXISTING_M2B_BINDING_REQUIRES_MODEL_EXECUTION",
        "BLOCKED_EXISTING_M2B_BINDING_SIGNATURE_UNRESOLVED",
        "BLOCKED_M2B_PRODUCER_ACTION_UNSUPPORTED",
        "EXISTING_M2B_COMPONENT",
        "EXISTING_M2B_VERSION",
        "M2B_INPUT_INCOMPLETE",
        "ShadowM2BDecisionResult",
        "bind_candidates_to_m2b",
    ),
    _EXPORTS,
)
_exports(
    ".m2b_to_m3_translation",
    (
        "BLOCKED_EXISTING_M3_SHADOW_BRIDGE_REJECTED_TRANSLATED_INPUT",
        "BLOCKED_M2B_TO_M3_SHADOW_CONTROL_BOUNDARY_VIOLATION",
        "BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_ACTION_UNSUPPORTED",
        "BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID",
        "EXISTING_M3_SHADOW_BRIDGE_COMPONENT",
        "EXPLICIT_M2B_TO_M3_ACTION_MAPPING",
        "M2B_PRODUCER_ACTION_NAMESPACE",
        "M2B_TO_M3_ACTION_TRANSLATION_SCHEMA",
        "M2B_TO_M3_SHADOW_TRANSLATION_SCHEMA",
        "M2B_TO_M3_SHADOW_TRANSLATOR_VERSION",
        "M3_LIFECYCLE_ACTION_NAMESPACE",
        "PASS_EXPLICIT_M2B_TO_M3_SHADOW_TRANSLATION_BOUND_TO_EXISTING_BRIDGE",
        "ExplicitM2BToM3ActionTranslation",
        "M2BToM3ShadowExecutionResult",
        "M2BToM3ShadowTranslation",
        "bind_m2b_decision_to_existing_m3_shadow_bridge",
        "run_m2b_to_m3_shadow",
        "translate_m2b_decision_to_m3_shadow_input",
        "translate_m2b_to_m3_shadow_input",
    ),
    _EXPORTS,
)
_exports(
    ".counterfactual_evidence",
    (
        "BLOCKED_SHADOW_CANDIDATE_ROUTE_FRAME_UNIT_NOT_VERIFIED",
        "BLOCKED_SHADOW_INTERPRETATION_TASK_BINDING_NOT_AVAILABLE",
        "BLOCKED_SHADOW_ROUTE_MAPPING_CONTEXT_INCOMPLETE",
        "PID_DESIRED_SPEED_FRAME",
        "PID_DESIRED_SPEED_SOURCE",
        "PID_DESIRED_SPEED_TEMPORAL_BASIS",
        "PID_DESIRED_SPEED_UNIT",
        "SHADOW_ROUTE_PROVENANCE",
        "SHADOW_SPEED_PROVENANCE",
        "ShadowCounterfactualEvidenceResult",
        "ShadowCounterfactualMappingContext",
        "ShadowRouteSemanticEvidence",
        "ShadowSpeedSemanticEvidence",
        "bind_candidate_to_counterfactual_evidence",
    ),
    _EXPORTS,
)
_exports(
    ".contracts",
    (
        "BLOCKED_SHADOW_CANDIDATE_EXECUTION_NOT_ISOLATABLE",
        "PASS_SHADOW_ONLY_ONLINE_CANDIDATE_M2B_M3_PIPELINE_READY_FOR_BOUNDED_CARLA_SHADOW",
        "BLOCKED_ONLINE_CANDIDATE_TO_M2B_INTERFACE_GAP",
        "DriveClarifyShadowDecisionResult",
        "ShadowCandidateResult",
        "ShadowObservationSnapshot",
    ),
    _EXPORTS,
)
_exports(
    ".runtime_authority_evidence_v1",
    (
        "ClarificationOpportunityEvidenceV1",
        "HoldingCapabilityDeclarationV1",
        "RUNTIME_AUTHORITY_EVIDENCE_PRODUCER",
        "RUNTIME_AUTHORITY_EVIDENCE_SCHEMA",
        "RuntimeDecisionAuthorityEvidenceV1",
        "produce_runtime_decision_authority_evidence_v1",
    ),
    _EXPORTS,
)
_exports(
    ".runtime_decision_authority_v1",
    (
        "RUNTIME_DECISION_AUTHORITY_ADAPTER",
        "RUNTIME_DECISION_AUTHORITY_SCHEMA",
        "RuntimeDecisionAuthorityActivationV1",
        "activate_runtime_decision_authority_v1",
    ),
    _EXPORTS,
)
_exports(
    ".longitudinal_task_satisfaction",
    (
        "CONTINUE_TASK_SATISFACTION_UNRESOLVED",
        "MIRROR_OF_EXISTING_SIMLINGO_STOP_EVALUATION_CONTRACT",
        "SIMLINGO_STOP_EVALUATION_CONTRACT_V0",
        "LongitudinalTaskSatisfactionOutcome",
        "LongitudinalTaskSatisfactionResultV0",
        "SimLingoStopEvaluationContractV0",
        "evaluate_stop_task_satisfaction_v0",
    ),
    _EXPORTS,
)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = list(_EXPORTS)
from .ask_replanning_v0 import (
    ASK_ENTRY_SOURCE,
    ASK_REPLAN_SCHEMA,
    AskDelayedAnswerReplanningV0,
    DeterministicOracleDelaySchedulerV0,
)

__all__ = [
    "ASK_ENTRY_SOURCE",
    "ASK_REPLAN_SCHEMA",
    "AskDelayedAnswerReplanningV0",
    "DeterministicOracleDelaySchedulerV0",
]
