"""Default-off P0/P1 persistent ambiguity integration for grounded runtime."""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from driveclarify_candidate_consequence_equivalence.renderer import (
    ConsequenceAwareGroundedSemantic,
    ConsequenceAwareOfficialDreamingRenderer,
)
from driveclarify_candidate_local_navigation_bridge import (
    CandidateLocalNavigationObligation,
    EgoPose2D,
    ExistingPlannerCapability,
    FreshLocalReplanRequest,
    GlobalRouteReconnectionBridge,
    MissionNavigationContext,
    QualificationStatus,
    build_fresh_local_replan_request,
    enrich_runtime_candidate,
    materialize_forward_binding,
)
from driveclarify_decision_evidence_v2 import (
    DecisionContextV2,
    DecisionV2,
    decide_v2,
)
from driveclarify_decision_evidence_v2.runtime_adapter import (
    evaluate_runtime_decision_evidence_v2,
    runtime_referent_route_order_authorization,
)
from driveclarify_decision_evidence_v3 import (
    DecisionContextV3,
    DecisionV3,
    decide_v3,
)
from driveclarify_decision_evidence_v3.runtime_adapter import (
    evaluate_runtime_decision_evidence_v3,
)
from driveclarify_grounded_language_v1.static_runtime import _control_values, _m3_event
from driveclarify_language_grounding_v1.runtime import (
    _EpisodeView,
    _EgoView,
    _VisionView,
    _runtime_candidate,
)
from driveclarify_grounded_language_v1_extension_e1_r1.runtime import (
    TopologyAwareReferentialRuntime,
    _live_map,
)
from driveclarify_m3_live_authority import ActiveAuthorityMember, AuthorityReason
from driveclarify_m3_minimal_core import (
    EventType,
    EvidenceGrade as M3EvidenceGrade,
    MinimalM3Event,
)
from driveclarify_m3_runtime_shadow.physical_wait_v0 import (
    build_bounded_wait_pilot_binding,
)
from driveclarify_m3_runtime_shadow.limited_act_commit_v0 import (
    NullLimitedActCommitV0,
    _build_frozen_m3_act_result,
    _tensor_digest,
)
from driveclarify_m3_runtime_shadow.shared_act_commit_v1 import SharedActCommitV1
from driveclarify_method_revision_v2 import (
    BoundedSemanticManeuverExecution,
    BranchCommitmentContract,
    LiveManeuverObservation,
    ManeuverExecutionState,
    SelectedNavigationIdentity,
    canonical_identity_digest,
    project_point_to_polyline,
)
from driveclarify_method_v2_5 import CompletionHandoverManeuverExecution
from driveclarify_method_v2_6 import (
    GenericDownstreamLandingManeuverExecution,
    build_live_downstream_landing_evidence,
    derive_alternative_branch_topology,
    derive_selected_branch_downstream_landing,
)
from driveclarify_method_v2_7 import (
    FEATURE_FLAG as METHOD_V2_7_ASK_BASELINE_WAIT_ENV,
    ContinuationStateV27,
    DecisionContextV27,
    assess_execution_location_plan_realization,
    assess_latest_reversible_baseline_continuation,
    bind_semantic_connector_targets,
    decide_v27,
    derive_execution_location_boundary,
    derive_maneuver_direction_boundary,
    SemanticBoundaryManeuverExecutionV27,
    derive_v27_selected_branch_downstream_landing,
)
from driveclarify_method_v2_8 import (
    FEATURE_FLAG as METHOD_V2_8_TOPOLOGY_LOCKED_HANDOVER_ENV,
    SelectedMotionEligibilityEvidenceV28,
    SelectedPlanTransactionV28,
    TopologyLockedManeuverExecutionV28,
    assess_selected_motion_eligibility_v28,
)
from driveclarify_method_v3 import (
    ConnectorPhaseOwnerR2,
    ConnectorPhaseTransactionR2,
    ConnectorPhysicalPhaseR2,
    FEATURE_FLAG as METHOD_V3_EXISTING_ROUTE_BINDING_ENV,
    MethodV3Phase,
    MethodV3PhaseOwner,
    RouteBoundManeuverExecutionV3,
    ResolvedSelectedLocalRoute,
    SelectedPlanAdmissibility,
    SelectedPlanAdmissibilityInput,
    SelectedBranchRouteBindingBridgeV3,
    classify_selected_connector_membership_r2,
    materialize_route_derived_forward_binding_v3,
    resolve_selected_opportunity_equivalence_v3,
    verify_selected_plan_admissibility,
)
from driveclarify_method_v3.target_window_r2 import (
    CONTRACT_IDENTITY as METHOD_V3_TARGET_WINDOW_R2_CONTRACT,
    ConnectorTargetWindowStateR2,
    select_connector_target_window_r2,
    validate_connector_context_r2,
)
from driveclarify_method_v3.bridge import target_window_catalog_digest


HIGH_FIDELITY_TIMING_FILENAME = "HIGH_FIDELITY_PER_STEP_TIMING.jsonl"
HIGH_FIDELITY_LEDGER_FILENAME = "HIGH_FIDELITY_CANDIDATE_FORWARD_LEDGER.json"
HIGH_FIDELITY_TRAJECTORY_FILENAME = (
    "HIGH_FIDELITY_CANDIDATE_TRAJECTORY_HISTORY.json"
)


def _atomic_high_fidelity_json(path: Path, value: Any) -> None:
    """Publish an audit artifact once, outside the per-tick control path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _atomic_high_fidelity_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Publish buffered timing rows once at shutdown, never once per tick."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))
from driveclarify_method_v2_1 import (
    EligibilityResult,
    ManeuverExecutionBudgetLedger,
    MotionEligibilityEvidence,
    PlanActivationGate,
    PlanActivationState,
    PlanIdentitySnapshot,
    PreActivationFeasibilityEvidence,
    PlanReadyHoldingEvidence,
    assess_motion_eligibility,
    assess_pre_activation_feasibility,
    assess_plan_ready_holding,
)
from driveclarify_official_dreaming_adapter import (
    OfficialDreamingCandidateForwardProvider,
)
from driveclarify_phase_b_evidence_completion_v1.runtime import (
    _candidate_connector_evidence,
    _conservative_commitment_progress,
    _detached_dense_route,
    _first_structural_divergence_progress,
    _trace_waypoint_connector,
)
from driveclarify_paper_mvp_runtime.simlingo_binding import _points, _scalar

from .contracts import ContractValidator, canonical_sha256
from .convergence_observer import (
    CONVERGENCE_OBSERVER_ENV,
    RuntimeGroundingConvergenceObserver,
)
from .evidence_adapter import DecisionWindowEvidenceAdapter
from .m2b_adapter import (
    AxisValue,
    ConvergedUniqueDecisionContext,
    PersistentDecision,
    PersistentDecisionContext,
    UniqueDecisionContext,
    decide_unique_after_convergence,
    decide_unique_after_answer,
    decide_persistent,
)
from .method_v1_decision import (
    CandidateConvergenceEvidence,
    MethodAuthorizationStatus,
    MethodControlSource,
    MethodDecisionEnvelope,
    MethodDecisionLabel,
    build_method_m3_receipt,
    build_method_m3_transaction,
    method_m3_receipt_from_results,
)
from .runtime_evaluator import RuntimeWindowObservation, evaluate_runtime_decision_window
from .scheduler import (
    CandidateEvidenceRefreshScheduler,
    RefreshCandidate,
)
from .store import (
    CandidateIdentity,
    EvidenceState,
    PersistentAmbiguityStore,
    SharedActionReference,
    StoreEvent,
)


FEATURE_FLAG = "DRIVECLARIFY_PERSISTENT_AMBIGUITY_RUNTIME_V1"
RUNTIME_VERSION = "DRIVECLARIFY_PERSISTENT_AMBIGUITY_RUNTIME_V1"
PASS_STATUS = (
    "PASS_PERSISTENT_AMBIGUITY_RUNTIME_V1_IMPLEMENTED_READY_FOR_"
    "CARLA_LIFECYCLE_VALIDATION"
)
PHASE_B_FINAL_RECEIPT = (
    Path(__file__).resolve().parents[1]
    / "reports/driveclarify_persistent_ambiguity_runtime_v1_continuous_completion"
    / "PHASE_B_FINAL_EVIDENCE_RECEIPT.json"
)
DECISION_EVIDENCE_V2_FEATURE_FLAG = "DRIVECLARIFY_DECISION_EVIDENCE_CONTRACT_V2"
DECISION_EVIDENCE_V3_FEATURE_FLAG = (
    "DRIVECLARIFY_DECISION_EVIDENCE_ARCHITECTURE_V3"
)
ONLINE_ROUTE_UPDATE_NATIVE_VALIDATION_ENV = (
    "DRIVECLARIFY_R4_4_ONLINE_ROUTE_UPDATE_NATIVE_VALIDATION"
)
ANSWER_CONDITIONED_RECONNECT_ENV = (
    "DRIVECLARIFY_R4_5_ANSWER_CONDITIONED_RECONNECT"
)
METHOD_REVISION_V2_ENV = "DRIVECLARIFY_METHOD_REVISION_V2"
METHOD_V2_1_RULE_GATE_DECOUPLING_ENV = (
    "DRIVECLARIFY_METHOD_V2_1_RULE_GATE_DECOUPLED_CLARIFICATION"
)
METHOD_V2_3_PRE_ACTIVATION_FEASIBILITY_ENV = (
    "DRIVECLARIFY_METHOD_V2_3_PRE_ACTIVATION_FEASIBILITY"
)
METHOD_V2_4_SIMULATION_EXECUTION_OPPORTUNITY_ENV = (
    "DRIVECLARIFY_METHOD_V2_4_SIMULATION_EXECUTION_OPPORTUNITY"
)
METHOD_V2_5_COMPLETION_HANDOVER_ENV = (
    "DRIVECLARIFY_METHOD_V2_5_COMPLETION_HANDOVER"
)
METHOD_V2_6_GENERIC_DOWNSTREAM_LANDING_ENV = (
    "DRIVECLARIFY_METHOD_V2_6_GENERIC_DOWNSTREAM_LANDING"
)
METHOD_V2_4_FROZEN_EXECUTION_ALLOWANCE_S = 28.52439178845816


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


# Surface wording for a language semantic constraint.  Direction constraints are
# already English words; execution-location ordinals are not directions, so they
# keep the map-independent nominal wording the frozen renderer expects.
_R4_4_PROMPT_DIRECTION = {
    "LEFT": "left",
    "RIGHT": "right",
    "STRAIGHT": "straight",
}


def _act_shared_alignment_verified(
    *, window: Any, observation: RuntimeWindowObservation,
    runtime_route_version: str, runtime_environment_digest: str,
    decision_evidence_v3_enabled: bool,
) -> bool:
    """Map the active evidence contract to the frozen authority alignment gate."""
    if decision_evidence_v3_enabled:
        checks = getattr(
            getattr(getattr(window, "v3_bundle", None), "current_action", None),
            "source_identity_checks", {},
        )
        contract_alignment = bool(
            checks and all(value is True for value in checks.values())
        )
    else:
        contract_alignment = bool(observation.alignment_verified)
    return bool(
        contract_alignment
        and observation.route_version == runtime_route_version
        and observation.environment_digest == runtime_environment_digest
    )


@dataclass(frozen=True)
class _DecisionValue:
    value: str


class _PersistentDecisionView:
    def __init__(self, decision: str, query_id: Optional[str] = None) -> None:
        self.recommendation = SimpleNamespace(
            decision=_DecisionValue(decision), query_id=query_id
        )
        self._decision = decision
        self._query_id = query_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self._decision,
            "query_id": self._query_id,
            "adapter": "PERSISTENT_TWO_AXIS_M2B_V1",
        }


class HardGateCertificateStatus(str, Enum):
    """Typed, provenance-bearing statuses accepted by hard-rule authority."""

    VERIFIED_PHYSICAL_SAFETY_PASS = "VERIFIED_PHYSICAL_SAFETY_PASS"
    VERIFIED_PHYSICAL_SAFETY_BLOCKED = "VERIFIED_PHYSICAL_SAFETY_BLOCKED"
    VERIFIED_ROUTE_LOCAL_PASS = "VERIFIED_ROUTE_LOCAL_PASS"
    VERIFIED_ROUTE_LOCAL_BLOCKED = "VERIFIED_ROUTE_LOCAL_BLOCKED"
    UNKNOWN = "UNKNOWN"


class EvidenceContractError(ValueError):
    """Base class for expected external evidence-contract failures."""


class EvidenceNormalizationError(EvidenceContractError):
    pass


class EvidenceValidationError(EvidenceContractError):
    pass


class EvidenceProvenanceError(EvidenceContractError):
    pass


class EvidenceSerializationError(EvidenceContractError):
    pass


# Non-serializable process-local capability used only by the lifecycle owner.
# Static fixture mappings cannot mint this identity by copying JSON fields.
_LIFECYCLE_DERIVATION_TOKEN = object()


@dataclass(frozen=True)
class HardGateCertificate:
    certificate_id: Optional[str]
    certificate_sha256: Optional[str]
    source_frame_id: Optional[int]
    status: HardGateCertificateStatus
    source_timestamp: Optional[float] = None

    def identity_verified(self) -> bool:
        digest = self.certificate_sha256
        return bool(
            self.certificate_id
            and isinstance(digest, str)
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
            and isinstance(self.source_frame_id, int)
            and self.source_frame_id >= 0
        )


@dataclass(frozen=True)
class HardGateEvidenceEnvelope:
    """Separate physical and route-local certificates from one source frame."""

    source_observation_id: str
    source_frame_id: Optional[int]
    physical_safety: HardGateCertificate
    route_local_hard_rule: HardGateCertificate
    source_timestamp: Optional[float] = None
    current_frame_id: Optional[int] = None
    current_timestamp: Optional[float] = None
    producer_id: Optional[str] = None
    candidate_bundle_id: Optional[str] = None
    provider_id: Optional[str] = None
    provider_object_identity: Optional[str] = None
    world_id: Optional[str] = None
    route_id: Optional[str] = None
    traffic_control_evidence_source: Optional[Mapping[str, Any]] = None
    topology_evidence_source: Optional[Mapping[str, Any]] = None
    construction_provenance: tuple[str, ...] = ()

    @classmethod
    def unknown(
        cls,
        *,
        source_observation_id: str,
        source_frame_id: Optional[int],
        current_frame_id: Optional[int] = None,
        current_timestamp: Optional[float] = None,
        producer_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        provider_object_identity: Optional[str] = None,
        world_id: Optional[str] = None,
        route_id: Optional[str] = None,
        traffic_control_evidence_source: Optional[Mapping[str, Any]] = None,
        topology_evidence_source: Optional[Mapping[str, Any]] = None,
        construction_provenance: Sequence[str] = (),
    ) -> "HardGateEvidenceEnvelope":
        unknown = HardGateCertificate(
            None, None, source_frame_id, HardGateCertificateStatus.UNKNOWN
        )
        return cls(
            source_observation_id=str(source_observation_id),
            source_frame_id=source_frame_id,
            physical_safety=unknown,
            route_local_hard_rule=unknown,
            current_frame_id=current_frame_id,
            current_timestamp=current_timestamp,
            producer_id=producer_id,
            provider_id=provider_id,
            provider_object_identity=provider_object_identity,
            world_id=world_id,
            route_id=route_id,
            traffic_control_evidence_source=traffic_control_evidence_source,
            topology_evidence_source=topology_evidence_source,
            construction_provenance=tuple(construction_provenance),
        )

    @staticmethod
    def _certificate_from_dict(value: Any, field: str) -> HardGateCertificate:
        if not isinstance(value, Mapping):
            raise EvidenceNormalizationError(field + "_WRONG_NESTED_TYPE")
        required = {"certificate_id", "certificate_sha256", "source_frame_id", "status"}
        if not required.issubset(value):
            raise EvidenceNormalizationError(field + "_MISSING_REQUIRED_NESTED_FIELD")
        try:
            status = HardGateCertificateStatus(value["status"])
        except (TypeError, ValueError) as error:
            raise EvidenceNormalizationError(field + "_INVALID_NESTED_ENUM") from error
        return HardGateCertificate(
            certificate_id=value["certificate_id"],
            certificate_sha256=value["certificate_sha256"],
            source_frame_id=value["source_frame_id"],
            status=status,
            source_timestamp=value.get("source_timestamp"),
        )

    @classmethod
    def from_dict(cls, value: Any) -> "HardGateEvidenceEnvelope":
        if not isinstance(value, Mapping):
            raise EvidenceNormalizationError("HARD_GATE_ENVELOPE_WRONG_TOP_LEVEL_TYPE")
        required = {"source_observation_id", "source_frame_id", "physical_safety", "route_local_hard_rule"}
        if not required.issubset(value):
            raise EvidenceNormalizationError("HARD_GATE_ENVELOPE_MISSING_REQUIRED_FIELD")
        provenance = value.get("construction_provenance", ())
        if not isinstance(provenance, (list, tuple)):
            raise EvidenceNormalizationError(
                "CONSTRUCTION_PROVENANCE_WRONG_NESTED_TYPE"
            )
        envelope = cls(
            source_observation_id=value["source_observation_id"],
            source_frame_id=value["source_frame_id"],
            physical_safety=cls._certificate_from_dict(value["physical_safety"], "PHYSICAL_SAFETY"),
            route_local_hard_rule=cls._certificate_from_dict(value["route_local_hard_rule"], "ROUTE_LOCAL_HARD_RULE"),
            source_timestamp=value.get("source_timestamp"),
            current_frame_id=value.get("current_frame_id"),
            current_timestamp=value.get("current_timestamp"),
            producer_id=value.get("producer_id"),
            candidate_bundle_id=value.get("candidate_bundle_id"),
            provider_id=value.get("provider_id"),
            provider_object_identity=value.get("provider_object_identity"),
            world_id=value.get("world_id"),
            route_id=value.get("route_id"),
            traffic_control_evidence_source=value.get(
                "traffic_control_evidence_source"
            ),
            topology_evidence_source=value.get("topology_evidence_source"),
            construction_provenance=tuple(provenance),
        )
        envelope.validate(check_provenance=False)
        return envelope

    def validate(
        self,
        *,
        expected_source_observation_id: Optional[str] = None,
        expected_current_frame_id: Any = None,
        expected_candidate_bundle_id: Optional[str] = None,
        expected_provider_binding: Optional[Mapping[str, Any]] = None,
        check_provenance: bool = True,
    ) -> None:
        if not isinstance(self.source_observation_id, str) or not self.source_observation_id:
            raise EvidenceValidationError("SOURCE_OBSERVATION_ID_INVALID")
        if self.source_frame_id is not None and (
            not isinstance(self.source_frame_id, int) or self.source_frame_id < 0
        ):
            raise EvidenceValidationError("SOURCE_FRAME_ID_INVALID")
        if self.current_frame_id is not None and (
            not isinstance(self.current_frame_id, int) or self.current_frame_id < 0
        ):
            raise EvidenceValidationError("CURRENT_FRAME_ID_INVALID")
        for name, value in (("source_timestamp", self.source_timestamp), ("current_timestamp", self.current_timestamp)):
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value))
            ):
                raise EvidenceValidationError(name.upper() + "_INVALID")
        if any(
            not isinstance(item, str) or not item
            for item in self.construction_provenance
        ):
            raise EvidenceValidationError("CONSTRUCTION_PROVENANCE_INVALID")
        for name, value in (
            ("TRAFFIC_CONTROL_EVIDENCE_SOURCE", self.traffic_control_evidence_source),
            ("TOPOLOGY_EVIDENCE_SOURCE", self.topology_evidence_source),
        ):
            if value is not None and not isinstance(value, Mapping):
                raise EvidenceValidationError(name + "_WRONG_NESTED_TYPE")
        if check_provenance and self.source_timestamp is not None and self.current_timestamp is not None and float(self.source_timestamp) > float(self.current_timestamp):
            raise EvidenceProvenanceError("SOURCE_TIMESTAMP_NEWER_THAN_CURRENT")
        for name, certificate in (("PHYSICAL_SAFETY", self.physical_safety), ("ROUTE_LOCAL_HARD_RULE", self.route_local_hard_rule)):
            if not isinstance(certificate, HardGateCertificate):
                raise EvidenceValidationError(name + "_WRONG_NESTED_TYPE")
            if not isinstance(certificate.status, HardGateCertificateStatus):
                raise EvidenceValidationError(name + "_INVALID_NESTED_ENUM")
            if certificate.source_frame_id is not None and (
                not isinstance(certificate.source_frame_id, int) or certificate.source_frame_id < 0
            ):
                raise EvidenceValidationError(name + "_SOURCE_FRAME_INVALID")
            if certificate.source_timestamp is not None and (
                not isinstance(certificate.source_timestamp, (int, float))
                or isinstance(certificate.source_timestamp, bool)
                or not math.isfinite(float(certificate.source_timestamp))
            ):
                raise EvidenceValidationError(name + "_SOURCE_TIMESTAMP_INVALID")
        if expected_source_observation_id is not None and self.source_observation_id != str(expected_source_observation_id):
            raise EvidenceProvenanceError("MISMATCHED_OBSERVATION_ID")
        if expected_candidate_bundle_id is not None and self.candidate_bundle_id != expected_candidate_bundle_id:
            raise EvidenceProvenanceError("MISMATCHED_CANDIDATE_BUNDLE_ID")
        if expected_provider_binding is not None:
            identity_fields = (
                "provider_id",
                "provider_object_identity",
                "world_id",
                "route_id",
            )
            for field in identity_fields:
                if getattr(self, field) != expected_provider_binding.get(field):
                    raise EvidenceProvenanceError(
                        "MISMATCHED_PROVIDER_" + field.upper()
                    )
            source_fields = (
                "traffic_control_evidence_source",
                "topology_evidence_source",
            )
            for field in source_fields:
                value = getattr(self, field)
                if not isinstance(value, Mapping):
                    raise EvidenceProvenanceError(
                        "MISSING_PROVIDER_" + field.upper()
                    )
                if value.get("source_id") != expected_provider_binding.get(field):
                    raise EvidenceProvenanceError(
                        "MISMATCHED_PROVIDER_" + field.upper()
                    )
                for identity_field, expected_value in (
                    ("provider_id", expected_provider_binding.get("provider_id")),
                    ("world_id", expected_provider_binding.get("world_id")),
                    ("route_id", expected_provider_binding.get("route_id")),
                    ("source_observation_id", self.source_observation_id),
                    ("source_frame_id", self.source_frame_id),
                ):
                    if value.get(identity_field) != expected_value:
                        raise EvidenceProvenanceError(
                            "MISMATCHED_"
                            + field.upper()
                            + "_"
                            + identity_field.upper()
                        )
            if tuple(self.construction_provenance) != tuple(
                expected_provider_binding.get("construction_provenance", ())
            ):
                raise EvidenceProvenanceError(
                    "MISMATCHED_PROVIDER_CONSTRUCTION_PROVENANCE"
                )
        if expected_current_frame_id is not None:
            try:
                expected_frame = int(expected_current_frame_id)
            except (TypeError, ValueError) as error:
                raise EvidenceProvenanceError("EXPECTED_CURRENT_FRAME_INVALID") from error
            if self.current_frame_id is not None and self.current_frame_id != expected_frame:
                raise EvidenceProvenanceError("MISMATCHED_CURRENT_FRAME_ID")

    def authorization_eligible(
        self,
        *,
        expected_source_observation_id: str,
        expected_current_frame_id: Any,
        expected_candidate_bundle_id: Optional[str] = None,
    ) -> bool:
        try:
            self.validate(
                expected_source_observation_id=expected_source_observation_id,
                expected_current_frame_id=expected_current_frame_id,
                expected_candidate_bundle_id=expected_candidate_bundle_id,
            )
            expected_frame = int(expected_current_frame_id)
        except EvidenceContractError:
            return False
        return bool(
            self.source_frame_id == expected_frame
            and self.physical_safety.source_frame_id == expected_frame
            and self.route_local_hard_rule.source_frame_id == expected_frame
        )

    def authority_gates(self, expected_source_frame_id: Any) -> tuple[bool, bool]:
        try:
            self.validate(expected_current_frame_id=expected_source_frame_id)
            expected_frame = int(expected_source_frame_id)
        except (EvidenceContractError, TypeError, ValueError):
            return False, False
        same_frame = bool(
            self.source_frame_id == expected_frame
            and self.physical_safety.source_frame_id == expected_frame
            and self.route_local_hard_rule.source_frame_id == expected_frame
        )
        physical_verified = bool(
            same_frame
            and self.physical_safety.identity_verified()
            and self.physical_safety.status
            in {
                HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_PASS,
                HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_BLOCKED,
            }
        )
        rule_verified = bool(
            same_frame
            and self.route_local_hard_rule.identity_verified()
            and self.route_local_hard_rule.status
            in {
                HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_PASS,
                HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_BLOCKED,
            }
        )
        return (
            bool(
                physical_verified
                and self.physical_safety.status
                is HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_PASS
            ),
            bool(
                rule_verified
                and self.route_local_hard_rule.status
                is HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_PASS
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        self.validate(check_provenance=False)
        value = {
            "source_observation_id": self.source_observation_id,
            "source_frame_id": self.source_frame_id,
            "source_timestamp": self.source_timestamp,
            "current_frame_id": self.current_frame_id,
            "current_timestamp": self.current_timestamp,
            "producer_id": self.producer_id,
            "candidate_bundle_id": self.candidate_bundle_id,
            "provider_id": self.provider_id,
            "provider_object_identity": self.provider_object_identity,
            "world_id": self.world_id,
            "route_id": self.route_id,
            "traffic_control_evidence_source": self.traffic_control_evidence_source,
            "topology_evidence_source": self.topology_evidence_source,
            "construction_provenance": list(self.construction_provenance),
            "physical_safety": {
                "certificate_id": self.physical_safety.certificate_id,
                "certificate_sha256": self.physical_safety.certificate_sha256,
                "source_frame_id": self.physical_safety.source_frame_id,
                "source_timestamp": self.physical_safety.source_timestamp,
                "status": self.physical_safety.status.value,
            },
            "route_local_hard_rule": {
                "certificate_id": self.route_local_hard_rule.certificate_id,
                "certificate_sha256": self.route_local_hard_rule.certificate_sha256,
                "source_frame_id": self.route_local_hard_rule.source_frame_id,
                "source_timestamp": self.route_local_hard_rule.source_timestamp,
                "status": self.route_local_hard_rule.status.value,
            },
        }
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise EvidenceSerializationError("HARD_GATE_EVIDENCE_NOT_SERIALIZABLE") from error
        return value


@runtime_checkable
class HardGateEvidenceProvider(Protocol):
    def resolve_hard_gate_evidence(
        self,
        *,
        source_observation_id: str,
        source_frame_id: Any,
        route_version: Optional[str],
        environment_digest: Optional[str],
    ) -> HardGateEvidenceEnvelope:
        """Return independently source-traceable certificates for this frame."""


class _FailClosedHardGateEvidenceProvider:
    def resolve_hard_gate_evidence(
        self,
        *,
        source_observation_id: str,
        source_frame_id: Any,
        route_version: Optional[str],
        environment_digest: Optional[str],
    ) -> HardGateEvidenceEnvelope:
        del route_version, environment_digest
        try:
            frame = int(source_frame_id)
        except (TypeError, ValueError):
            frame = None
        return HardGateEvidenceEnvelope.unknown(
            source_observation_id=source_observation_id,
            source_frame_id=None,
            current_frame_id=frame,
            producer_id="FAIL_CLOSED_DEFAULT_PROVIDER",
        )


def _provider_object_identity(provider: Any) -> str:
    return (
        type(provider).__module__
        + "."
        + type(provider).__qualname__
        + "@"
        + format(id(provider), "x")
    )


def _production_provider_binding(provider: Any) -> Optional[dict[str, Any]]:
    binding_method = getattr(provider, "production_binding", None)
    if not callable(binding_method):
        return None
    try:
        value = binding_method()
        if not isinstance(value, Mapping):
            return None
        required_strings = (
            "provider_id",
            "provider_object_identity",
            "world_id",
            "route_id",
            "traffic_control_evidence_source",
            "topology_evidence_source",
        )
        if any(
            not isinstance(value.get(field), str) or not value.get(field)
            for field in required_strings
        ):
            return None
        provenance = value.get("construction_provenance")
        if (
            not isinstance(provenance, (list, tuple))
            or not provenance
            or any(not isinstance(item, str) or not item for item in provenance)
        ):
            return None
        normalized = {
            field: str(value[field]) for field in required_strings
        }
        normalized["construction_provenance"] = list(provenance)
        json.dumps(normalized, sort_keys=True, allow_nan=False)
        return normalized
    except (EvidenceContractError, TypeError, ValueError, OverflowError):
        return None


class NativeCarlaRouteLocalEvidenceProvider:
    """Production route-local evidence source bound to one native world/route.

    The object captures the concrete CARLA world, hero actor, map, and
    agent-owned dense route at construction.  Resolution only reads that bound
    source; it does not consult a DriveClarify registry, alias, or mutable
    provider default.
    """

    IMPLEMENTATION_ID = "NATIVE_CARLA_ROUTE_LOCAL_HARD_GATE_PROVIDER_R4_2_V1"
    TRAFFIC_SOURCE_ID = "CARLA_WORLD_COMPLETE_TRAFFIC_CONTROL_CENSUS_R4_2_V1"
    TOPOLOGY_SOURCE_ID = "CARLA_HD_MAP_AGENT_OWNED_DENSE_ROUTE_JOIN_R4_2_V1"
    CONSTRUCTION_PROVENANCE = (
        "driveclarify_m3_runtime_shadow.live_shadow_runtime.build_live_shadow_runtime",
        "driveclarify_grounded_language_v1.runtime.build_grounded_language_v1_runtime",
        "NativeCarlaRouteLocalEvidenceProvider.from_native_agent",
        "driveclarify_persistent_ambiguity_runtime_v1.runtime.build_persistent_ambiguity_runtime",
        "PersistentAmbiguityReferentialRuntime.__init__",
    )
    FORWARD_HORIZON_M = 30.0

    def __init__(
        self,
        *,
        agent: Any,
        world: Any,
        hero: Any,
        map_object: Any,
        route_points_world_xy_m: Sequence[Sequence[float]],
        route_source_attribute: str,
    ) -> None:
        if world is None or hero is None or map_object is None:
            raise EvidenceValidationError("NATIVE_WORLD_HERO_OR_MAP_MISSING")
        points = tuple(
            (float(row[0]), float(row[1])) for row in route_points_world_xy_m
        )
        if len(points) < 2 or any(
            not all(math.isfinite(value) for value in point) for point in points
        ):
            raise EvidenceValidationError("NATIVE_ROUTE_POINTS_INVALID")
        self._agent = agent
        self._world = world
        self._hero = hero
        self._map = map_object
        self._route_points = points
        self._route_source_attribute = str(route_source_attribute)
        self._route_id = DecisionWindowEvidenceAdapter.route_version_id(
            points,
            source="AGENT_OWNED_DENSE_CARLA_WORLD_ROUTE",
        )
        map_name = str(getattr(map_object, "name", ""))
        if not map_name:
            raise EvidenceValidationError("NATIVE_MAP_ID_MISSING")
        self._world_id = "native-carla-world-" + canonical_sha256(
            {
                "map_name": map_name,
                "world_object_identity": format(id(world), "x"),
            }
        )[:24]
        self._provider_object_identity = _provider_object_identity(self)
        self._provider_id = "native-route-local-provider-" + canonical_sha256(
            {
                "implementation": self.IMPLEMENTATION_ID,
                "world_id": self._world_id,
                "route_id": self._route_id,
                "route_source_attribute": self._route_source_attribute,
                "traffic_source": self.TRAFFIC_SOURCE_ID,
                "topology_source": self.TOPOLOGY_SOURCE_ID,
            }
        )[:24]

    @classmethod
    def from_native_agent(cls, agent: Any) -> "NativeCarlaRouteLocalEvidenceProvider":
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            world = CarlaDataProvider.get_world()
            hero = CarlaDataProvider.get_hero_actor()
            map_object = world.get_map()
            route, route_source_attribute = _detached_dense_route(agent)
            route_rows = tuple(
                (float(item[0][0]), float(item[0][1])) for item in route
            )
        except (AttributeError, ImportError, IndexError, RuntimeError, TypeError, ValueError) as error:
            raise EvidenceValidationError(
                "NATIVE_ROUTE_LOCAL_PROVIDER_CONSTRUCTION_SOURCE_UNAVAILABLE"
            ) from error
        return cls(
            agent=agent,
            world=world,
            hero=hero,
            map_object=map_object,
            route_points_world_xy_m=route_rows,
            route_source_attribute=route_source_attribute,
        )

    def production_binding(self) -> dict[str, Any]:
        return {
            "provider_id": self._provider_id,
            "provider_object_identity": self._provider_object_identity,
            "world_id": self._world_id,
            "route_id": self._route_id,
            "traffic_control_evidence_source": self.TRAFFIC_SOURCE_ID,
            "topology_evidence_source": self.TOPOLOGY_SOURCE_ID,
            "construction_provenance": list(self.CONSTRUCTION_PROVENANCE),
        }

    @staticmethod
    def _lane_key(waypoint: Any) -> tuple[int, int, int, Optional[int]]:
        is_junction = bool(getattr(waypoint, "is_junction", False))
        return (
            int(waypoint.road_id),
            int(waypoint.section_id),
            int(waypoint.lane_id),
            int(waypoint.junction_id) if is_junction else None,
        )

    @staticmethod
    def _forward_xy(waypoint: Any) -> tuple[float, float]:
        vector = waypoint.transform.get_forward_vector()
        magnitude = math.hypot(float(vector.x), float(vector.y))
        if not math.isfinite(magnitude) or magnitude <= 1e-9:
            raise EvidenceValidationError("WAYPOINT_DIRECTION_INVALID")
        return float(vector.x) / magnitude, float(vector.y) / magnitude

    @staticmethod
    def _location_like(template: Any, x: float, y: float, z: float = 0.0) -> Any:
        try:
            return type(template)(x=float(x), y=float(y), z=float(z))
        except (TypeError, ValueError):
            try:
                import carla

                return carla.Location(x=float(x), y=float(y), z=float(z))
            except (AttributeError, ImportError, TypeError, ValueError) as error:
                raise EvidenceValidationError("CARLA_LOCATION_CONSTRUCTION_FAILED") from error

    def _route_topology(self, template_location: Any) -> tuple[list[dict[str, Any]], dict[tuple[int, int, int, Optional[int]], tuple[float, float]]]:
        rows: list[dict[str, Any]] = []
        directions: dict[
            tuple[int, int, int, Optional[int]], tuple[float, float]
        ] = {}
        for index, (x_value, y_value) in enumerate(self._route_points):
            location = self._location_like(template_location, x_value, y_value)
            waypoint = self._map.get_waypoint(location)
            lane_key = self._lane_key(waypoint)
            direction = self._forward_xy(waypoint)
            directions[lane_key] = direction
            rows.append(
                {
                    "route_index": index,
                    "world_xy_m": [x_value, y_value],
                    "road_id": lane_key[0],
                    "section_id": lane_key[1],
                    "lane_id": lane_key[2],
                    "junction_id": lane_key[3],
                    "travel_direction_unit_xy": list(direction),
                }
            )
        return rows, directions

    @staticmethod
    def _actor_state(actor: Any) -> str:
        state = actor.get_state() if callable(getattr(actor, "get_state", None)) else getattr(actor, "state", "UNKNOWN")
        normalized = str(state).upper().rsplit(".", 1)[-1]
        return normalized if normalized in {"RED", "YELLOW", "GREEN", "OFF", "UNKNOWN"} else "UNKNOWN"

    @staticmethod
    def _trigger_world_location(actor: Any) -> tuple[Any, Any, Any]:
        trigger = actor.trigger_volume
        transform = actor.get_transform()
        local_location = trigger.location
        world_location = transform.transform(local_location)
        extent = trigger.extent
        return world_location, extent, transform

    def _control_waypoints(self, actor: Any, trigger_location: Any) -> tuple[Any, ...]:
        getter = getattr(actor, "get_stop_waypoints", None)
        if callable(getter):
            waypoints = tuple(getter())
            if waypoints:
                return waypoints
        return (self._map.get_waypoint(trigger_location),)

    def _native_sources(
        self,
        *,
        frame: int,
        source_observation_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str, str, float]:
        snapshot = self._world.get_snapshot()
        snapshot_frame = int(snapshot.frame)
        timestamp = float(snapshot.timestamp.elapsed_seconds)
        if snapshot_frame != frame:
            raise EvidenceProvenanceError("NATIVE_WORLD_SNAPSHOT_FRAME_MISMATCH")
        if getattr(self._hero, "is_alive", True) is not True:
            raise EvidenceValidationError("NATIVE_HERO_NOT_ALIVE")
        hero_location = self._hero.get_location()
        hero_waypoint = self._map.get_waypoint(hero_location)
        hero_lane = self._lane_key(hero_waypoint)
        hero_direction = self._forward_xy(hero_waypoint)
        hero_projection = DecisionWindowEvidenceAdapter.project_to_route(
            (float(hero_location.x), float(hero_location.y)), self._route_points
        )
        topology_rows, route_directions = self._route_topology(hero_location)
        topology_payload = {
            "source_id": self.TOPOLOGY_SOURCE_ID,
            "provider_id": self._provider_id,
            "world_id": self._world_id,
            "route_id": self._route_id,
            "source_observation_id": source_observation_id,
            "source_frame_id": frame,
            "map_id": str(self._map.name),
            "route_source_attribute": self._route_source_attribute,
            "route_point_count": len(self._route_points),
            "route_lane_rows": topology_rows,
        }
        topology_payload["evidence_sha256"] = canonical_sha256(topology_payload)

        actors = list(self._world.get_actors())
        controls = sorted(
            (
                actor
                for actor in actors
                if "traffic_light" in str(getattr(actor, "type_id", ""))
                or "traffic.stop" in str(getattr(actor, "type_id", ""))
            ),
            key=lambda actor: int(actor.id),
        )
        evaluated = []
        restrictive = []
        unknown = []
        route_lane_keys = set(route_directions)
        for actor in controls:
            actor_id = str(actor.id)
            type_id = str(actor.type_id)
            trigger_location, extent, actor_transform = self._trigger_world_location(actor)
            affected_waypoints = self._control_waypoints(actor, trigger_location)
            affected_lanes = [self._lane_key(waypoint) for waypoint in affected_waypoints]
            affected_directions = [self._forward_xy(waypoint) for waypoint in affected_waypoints]
            route_join = any(lane in route_lane_keys for lane in affected_lanes)
            same_direction = any(
                left[0] * hero_direction[0] + left[1] * hero_direction[1] > 0.9
                for left in affected_directions
            )
            yaw_rad = math.radians(
                float(getattr(getattr(actor_transform, "rotation", None), "yaw", 0.0))
            )
            cosine, sine = math.cos(yaw_rad), math.sin(yaw_rad)
            hero_half_width = float(self._hero.bounding_box.extent.y)
            hero_half_length = float(self._hero.bounding_box.extent.x)
            oriented_sweep_intersection = any(
                abs(
                    cosine * (route_x - float(trigger_location.x))
                    + sine * (route_y - float(trigger_location.y))
                )
                <= float(extent.x) + hero_half_length
                and abs(
                    -sine * (route_x - float(trigger_location.x))
                    + cosine * (route_y - float(trigger_location.y))
                )
                <= float(extent.y) + hero_half_width
                for route_x, route_y in self._route_points
            )
            control_projection = DecisionWindowEvidenceAdapter.project_to_route(
                (float(trigger_location.x), float(trigger_location.y)),
                self._route_points,
            )
            forward_distance = float(control_projection.progress_m) - float(
                hero_projection.progress_m
            )
            within_horizon = 0.0 <= forward_distance <= self.FORWARD_HORIZON_M
            applicable = bool(
                route_join
                and same_direction
                and within_horizon
                and oriented_sweep_intersection
            )
            state = "STOP" if "traffic.stop" in type_id else self._actor_state(actor)
            state_known = state in {"RED", "YELLOW", "GREEN", "STOP"}
            restriction = state in {"RED", "YELLOW", "STOP"}
            if applicable and not state_known:
                unknown.append(actor_id)
            elif applicable and restriction:
                restrictive.append(actor_id)
            rotation = getattr(actor_transform, "rotation", None)
            evaluated.append(
                {
                    "actor_id": actor_id,
                    "type_id": type_id,
                    "state": state,
                    "trigger_world_xyz_m": [
                        float(trigger_location.x),
                        float(trigger_location.y),
                        float(trigger_location.z),
                    ],
                    "trigger_extent_xyz_m": [
                        float(extent.x), float(extent.y), float(extent.z)
                    ],
                    "trigger_yaw_deg": float(getattr(rotation, "yaw", 0.0)),
                    "affected_lane_keys": [list(lane) for lane in affected_lanes],
                    "route_join_proven": route_join,
                    "same_travel_direction": same_direction,
                    "forward_distance_m": forward_distance,
                    "within_horizon": within_horizon,
                    "oriented_sweep_intersection": oriented_sweep_intersection,
                    "route_local_applicable": applicable,
                    "restriction_status": (
                        "RESTRICTIVE"
                        if restriction
                        else "CLEAR"
                        if state_known
                        else "UNKNOWN"
                    ),
                }
            )
        traffic_payload = {
            "source_id": self.TRAFFIC_SOURCE_ID,
            "provider_id": self._provider_id,
            "world_id": self._world_id,
            "route_id": self._route_id,
            "source_observation_id": source_observation_id,
            "source_frame_id": frame,
            "world_snapshot_id": self._world_id + ":frame:" + str(frame),
            "census_complete": True,
            "actor_count": len(evaluated),
            "evaluated_controls": evaluated,
            "applicable_restrictive_control_ids": restrictive,
            "unknown_control_ids": unknown,
            "ego_lane_key": list(hero_lane),
        }
        traffic_payload["evidence_sha256"] = canonical_sha256(traffic_payload)
        physical_status = "PASS"
        route_status = (
            "BLOCKED" if restrictive else "UNKNOWN" if unknown else "PASS"
        )
        return (
            traffic_payload,
            topology_payload,
            physical_status,
            route_status,
            timestamp,
        )

    def resolve_hard_gate_evidence(
        self,
        *,
        source_observation_id: str,
        source_frame_id: Any,
        route_version: Optional[str],
        environment_digest: Optional[str],
    ) -> HardGateEvidenceEnvelope:
        del environment_digest
        try:
            frame = int(source_frame_id)
            if frame < 0:
                raise EvidenceValidationError("SOURCE_FRAME_ID_INVALID")
            if route_version is not None and str(route_version) != self._route_id:
                raise EvidenceProvenanceError("NATIVE_ROUTE_ID_MISMATCH")
            traffic, topology, physical, route, timestamp = self._native_sources(
                frame=frame,
                source_observation_id=str(source_observation_id),
            )
            physical_status = {
                "PASS": HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_PASS,
                "BLOCKED": HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_BLOCKED,
            }[physical]
            route_status = {
                "PASS": HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_PASS,
                "BLOCKED": HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_BLOCKED,
            }[route]

            def certificate(
                kind: str,
                status: HardGateCertificateStatus,
                source: Mapping[str, Any],
            ) -> HardGateCertificate:
                payload = {
                    "kind": kind,
                    "status": status.value,
                    "provider_id": self._provider_id,
                    "world_id": self._world_id,
                    "route_id": self._route_id,
                    "source_observation_id": source_observation_id,
                    "source_frame_id": frame,
                    "source_evidence_sha256": source["evidence_sha256"],
                }
                return HardGateCertificate(
                    certificate_id=kind.lower() + "-" + canonical_sha256(payload)[:24],
                    certificate_sha256=canonical_sha256(payload),
                    source_frame_id=frame,
                    source_timestamp=timestamp,
                    status=status,
                )

            return HardGateEvidenceEnvelope(
                source_observation_id=str(source_observation_id),
                source_frame_id=frame,
                source_timestamp=timestamp,
                current_frame_id=frame,
                current_timestamp=timestamp,
                physical_safety=certificate(
                    "PHYSICAL_SAFETY", physical_status, topology
                ),
                route_local_hard_rule=certificate(
                    "ROUTE_LOCAL_HARD_RULE", route_status, traffic
                ),
                producer_id=self.IMPLEMENTATION_ID,
                provider_id=self._provider_id,
                provider_object_identity=self._provider_object_identity,
                world_id=self._world_id,
                route_id=self._route_id,
                traffic_control_evidence_source=traffic,
                topology_evidence_source=topology,
                construction_provenance=self.CONSTRUCTION_PROVENANCE,
            )
        except EvidenceContractError:
            raise
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError) as error:
            raise EvidenceNormalizationError(
                "MALFORMED_NESTED_NATIVE_PROVIDER_EVIDENCE"
            ) from error


def _persistent_decision_window_dashboard(
    *,
    run_id: str,
    raw_instruction: str,
    bound_candidates: Any,
    bundle: Any,
    window: Any,
    observation: RuntimeWindowObservation,
    episode: Any,
    decision: str,
    authority_subject_type: Optional[str],
    dino_forward_count: int,
) -> dict[str, Any]:
    """Project current P1 evidence into the passive research dashboard.

    This is a display-only projection of already-computed runtime evidence.  It
    performs no model, planner, PID, policy, or VehicleControl operation.
    """

    candidate_ids = tuple(bundle.requested_candidate_ids)
    coverage = dict(window.plan_coverage_evidence)
    arc_by_candidate: dict[str, Any] = {}
    coverage_by_plan: dict[str, Any] = {}
    for plan_id, row in coverage.items():
        result = row.get("result", {}) if isinstance(row, Mapping) else {}
        raw_value = result.get("value") if isinstance(result, Mapping) else None
        value = raw_value if isinstance(raw_value, Mapping) else {}
        diagnostics = (
            row.get("projection_and_validity", {})
            if isinstance(row, Mapping)
            else {}
        )
        coverage_by_plan[str(plan_id)] = value.get("coverage_status", "UNKNOWN")
        if plan_id in candidate_ids:
            arc_by_candidate[str(plan_id)] = diagnostics.get(
                "local_plan_arc_length_m", "UNKNOWN"
            )

    commitments = {
        str(key): float(value)
        for key, value in observation.candidate_commitment_progress_m.items()
    }
    earliest_commitment = min(commitments.values()) if commitments else None
    shared_end = window.shared_action_end_progress_m
    current_coverage_available = bool(
        getattr(window, "current_executable_coverage", window.full_plan_coverage)
    )
    shared_corridor = (
        "{:.3f}..{:.3f} m_route VERIFIED".format(
            float(window.current_progress_m), float(shared_end)
        )
        if current_coverage_available and shared_end is not None
        else "UNKNOWN"
    )
    candidate_rows = []
    by_id = {
        str(row.get("candidate_id")): row
        for row in bound_candidates
        if isinstance(row, Mapping) and row.get("candidate_id") is not None
    }
    for index, candidate_id in enumerate(candidate_ids):
        row = by_id.get(str(candidate_id), {})
        candidate_rows.append(
            {
                "display_label": "Candidate " + chr(ord("A") + index),
                "candidate_id": str(candidate_id),
                "interpretation_id": row.get("interpretation_id"),
                "referring_expression": row.get("referring_expression"),
                "target_id": row.get("target_id"),
                "junction_id": row.get("junction_id"),
                "branch_id": row.get("branch_id"),
                "route_order_index": row.get("route_order_index"),
            }
        )

    accounting = asdict(episode.compute_accounting)
    plan_a = arc_by_candidate.get(candidate_ids[0], "UNKNOWN") if candidate_ids else "UNKNOWN"
    plan_b = (
        arc_by_candidate.get(candidate_ids[1], "UNKNOWN")
        if len(candidate_ids) > 1
        else "UNKNOWN"
    )
    reason_code = (
        window.reason_codes[0]
        if window.reason_codes
        else "RUNTIME_DECISION_WINDOW_AVAILABLE"
    )
    dashboard = {
        "run_id": str(run_id),
        "planning_event_id": bundle.normal_planning_event_id,
        "bundle_version": bundle.bundle_id,
        "raw_instruction": str(raw_instruction),
        "candidates": candidate_rows,
        "ego_route_progress": float(window.current_progress_m),
        "ego_route_progress_unit": "m_route",
        "plan_A_arc_m": plan_a,
        "plan_B_arc_m": plan_b,
        "plan_coverage": "COVERED" if current_coverage_available else "UNKNOWN",
        "plan_coverage_by_plan": coverage_by_plan,
        "shared_corridor": shared_corridor,
        "shared_corridor_end_progress_m": shared_end,
        "maneuver_onset": float(observation.maneuver_onset_progress_m),
        "decision_point": earliest_commitment if earliest_commitment is not None else "UNKNOWN",
        "decision_point_definition": "EARLIEST_CONSERVATIVE_CANDIDATE_COMMITMENT",
        "commitment_boundary": (
            " | ".join(
                "{} {:.3f}".format(chr(ord("A") + index), commitments[candidate_id])
                for index, candidate_id in enumerate(candidate_ids)
                if candidate_id in commitments
            )
            or "UNKNOWN"
        ),
        "commitment_boundary_by_candidate_m_route": commitments,
        "recoverability": window.recoverability,
        "recoverability_by_candidate": {
            candidate_id: window.recoverability for candidate_id in candidate_ids
        },
        "time_to_divergence": window.time_to_divergence_lower_bound_s,
        "time_to_divergence_unit": "s_lower_bound",
        "latest_safe_clarification": window.latest_safe_slack_s,
        "latest_safe_clarification_unit": "s_slack",
        "latest_safe_clarification_monotonic": (
            window.latest_safe_clarification_monotonic
        ),
        "current_relation": window.candidate_relationship,
        "current_action_relation": window.current_action_relation,
        "future_obligation_relation": window.future_obligation_relation,
        "decision": str(decision),
        "episode_state": episode.semantic_state.value,
        "freshness": episode.evidence_state.value,
        "forward_accounting": accounting,
        "forward_counters": (
            "DINO {} | normal {} | candidates {} | viz {}".format(
                int(dino_forward_count),
                accounting["normal_forward_count"],
                accounting["candidate_forward_count"],
                accounting["visualization_extra_forward_count"],
            )
        ),
        "authority_subject": authority_subject_type,
        "reason_code": reason_code,
        "reason_codes": list(window.reason_codes),
        "source_observation_id": window.source_observation_id,
        "source_frame": window.source_frame_id,
        "research_debug_view": True,
        "simulation_only": True,
        "formal_safety_guarantee": False,
    }
    if hasattr(window, "v2_bundle"):
        dashboard.update(
            {
                "decision_evidence_contract_version": "2.0",
                "current_executable_coverage": current_coverage_available,
                "full_future_plan_coverage_v1_diagnostic": bool(
                    window.full_plan_coverage
                ),
                "answer_deadline_monotonic": window.answer_deadline_monotonic,
            }
        )
    if hasattr(window, "v3_bundle"):
        lease = window.v3_bundle.shared_action_lease
        dashboard.update(
            {
                "decision_evidence_contract_version": window.v3_bundle.contract_version,
                "CurrentActionRelation": window.current_action_relation,
                "FutureObligationRelation": window.future_obligation_relation,
                "ClarificationState": window.clarification_state,
                "SharedActionLease": {
                    "valid": lease.valid,
                    "lease_id": lease.lease_id,
                    "lease_start": lease.lease_start_progress_m,
                    "lease_end": lease.lease_end_progress_m,
                    "lease_expiry": lease.lease_expiry_monotonic,
                    "lease_reason": lease.lease_reason,
                },
                "Recoverability": window.recoverability,
                "recoverability_by_candidate": dict(
                    window.recoverability_by_candidate
                ),
                "RefreshGuarantee": (
                    window.precommitment_refresh_guarantee
                ),
                "commitment": commitments,
                "subject": authority_subject_type,
                "bundle_id": bundle.bundle_id,
                "plan_id": canonical_sha256(
                    [
                        {
                            "candidate_id": row.candidate_id,
                            "route_digest": row.route_digest,
                            "speed_digest": row.speed_digest,
                        }
                        for row in bundle.evidence
                    ]
                ),
                "control_source": (
                    "ORIGINAL_SIMLINGO_SHARED_PREFIX"
                    if str(decision) == "ACT_SHARED"
                    else "EXISTING_HOLDING"
                    if str(decision) == "WAIT"
                    else "BASELINE_SIMLINGO"
                ),
                "authority_source": authority_subject_type,
                "current_executable_coverage": current_coverage_available,
                "full_future_plan_coverage_v1_diagnostic": bool(
                    window.full_plan_coverage
                ),
                "answer_deadline_monotonic": window.answer_deadline_monotonic,
            }
        )
    return dashboard


def _persistent_decision_history_row(
    *, bundle: Any, window: Any, recommendation: Any, episode: Any,
    m2b_context: Any = None,
) -> dict[str, Any]:
    """Create one append-only, policy-passive P1 decision audit row."""

    row = {
        "planning_event_id": bundle.normal_planning_event_id,
        "bundle_id": bundle.bundle_id,
        "source_observation_id": bundle.source_observation_id,
        "source_frame_id": bundle.source_frame_id,
        "candidate_ids": list(bundle.requested_candidate_ids),
        "candidate_plan_references": [
            {
                "candidate_id": row.candidate_id,
                "route_digest": row.route_digest,
                "speed_digest": row.speed_digest,
                "plan_reference_digest": row.plan_reference_digest,
            }
            for row in bundle.evidence
        ],
        "bundle_complete": bool(bundle.complete),
        "bundle_latency_seconds": float(bundle.latency_seconds),
        "window_status": window.status,
        "decision_window_digest": window.decision_window_digest,
        "current_progress_m": float(window.current_progress_m),
        "shared_action_end_progress_m": window.shared_action_end_progress_m,
        "current_action_relation": window.current_action_relation,
        "future_obligation_relation": window.future_obligation_relation,
        "candidate_relationship": window.candidate_relationship,
        "full_plan_coverage": bool(window.full_plan_coverage),
        "recoverability": window.recoverability,
        "time_to_divergence_lower_bound_s": (
            window.time_to_divergence_lower_bound_s
        ),
        "latest_safe_slack_s": window.latest_safe_slack_s,
        "latest_safe_clarification_monotonic": (
            window.latest_safe_clarification_monotonic
        ),
        "window_reason_codes": list(window.reason_codes),
        "decision": recommendation.decision.value,
        "decision_relation": recommendation.relation.value,
        "decision_reason_codes": list(recommendation.reason_codes),
        "m2b_inputs": None if m2b_context is None else asdict(m2b_context),
        "authority_subject_type": recommendation.authority_subject_type,
        "semantic_state": episode.semantic_state.value,
        "evidence_state": episode.evidence_state.value,
        "cumulative_compute_accounting": asdict(episode.compute_accounting),
    }
    if hasattr(window, "v2_bundle"):
        row.update(
            {
                "decision_evidence_contract_version": "2.0",
                "current_executable_coverage": bool(
                    window.current_executable_coverage
                ),
                "answer_deadline_monotonic": window.answer_deadline_monotonic,
                "future_obligation_evidence_digest": (
                    window.future_obligation_evidence_digest
                ),
            }
        )
    if hasattr(window, "v3_bundle"):
        row.update(
            {
                "decision_evidence_contract_version": window.v3_bundle.contract_version,
                "clarification_state": window.clarification_state,
                "precommitment_refresh_guarantee": (
                    window.precommitment_refresh_guarantee
                ),
                "recoverability_by_candidate": dict(
                    window.recoverability_by_candidate
                ),
                "shared_action_lease": asdict(
                    window.v3_bundle.shared_action_lease
                ),
                "refresh_guarantee_evidence_digest": (
                    window.refresh_guarantee_evidence_digest
                ),
                "recoverability_evidence_digest": (
                    window.recoverability_evidence_digest
                ),
                "future_obligation_evidence_digest": (
                    window.future_obligation_evidence_digest
                ),
            }
        )
    return row


class PersistentAmbiguityReferentialRuntime(TopologyAwareReferentialRuntime):
    """P1 integration that is mutually exclusive with the legacy A×3/B×3 path."""

    @staticmethod
    def evaluate_decision_opportunity_lifecycle(evidence: Any) -> dict[str, Any]:
        """Replay ASK-to-WAIT state transitions before invoking the M2B owner.

        A caller cannot inject the final WAIT reducer booleans.  The lifecycle
        state is produced only by the ordered events accepted below; missing,
        reordered, duplicate, or unknown events fail closed.
        """
        lifecycle = {
            "ask_emitted": False,
            "active_query": False,
            "answer_pending": False,
            "holding_valid": False,
            "lease_valid": False,
            "query_bound": False,
        }
        transitions = []
        event_contract = (
            ("ASK_EMITTED", None, "ask_emitted"),
            ("QUERY_ACTIVATED", "ask_emitted", "active_query"),
            ("ANSWER_REMAINS_PENDING", "active_query", "answer_pending"),
            ("HOLDING_GRANTED", "answer_pending", "holding_valid"),
            ("LEASE_VALIDATED", "holding_valid", "lease_valid"),
            ("QUERY_BOUND_TO_HOLDING", "lease_valid", "query_bound"),
        )
        try:
            if not isinstance(evidence, Mapping):
                raise EvidenceNormalizationError("DECISION_EVIDENCE_WRONG_TOP_LEVEL_TYPE")
            if "query_lifecycle" in evidence:
                raise EvidenceNormalizationError("STATIC_QUERY_LIFECYCLE_CONTEXT_FORBIDDEN")
            events = evidence.get("lifecycle_events")
            if not isinstance(events, (list, tuple)):
                raise EvidenceNormalizationError("LIFECYCLE_EVENTS_WRONG_TYPE")
            expected_names = [row[0] for row in event_contract]
            for index, event in enumerate(events):
                if not isinstance(event, str):
                    raise EvidenceNormalizationError("LIFECYCLE_EVENT_WRONG_TYPE")
                if index >= len(event_contract) or event != event_contract[index][0]:
                    raise EvidenceValidationError(
                        "LIFECYCLE_EVENT_ORDER_INVALID:{}".format(event)
                    )
                name, prerequisite, state_key = event_contract[index]
                if prerequisite is not None and lifecycle[prerequisite] is not True:
                    raise EvidenceValidationError(
                        "LIFECYCLE_PREREQUISITE_MISSING:{}".format(prerequisite)
                    )
                before = dict(lifecycle)
                lifecycle[state_key] = True
                transitions.append({
                    "sequence": index + 1,
                    "event": name,
                    "state_before": before,
                    "state_after": dict(lifecycle),
                })
            if list(events) != expected_names:
                missing = expected_names[len(events):]
                raise EvidenceValidationError(
                    "LIFECYCLE_INCOMPLETE:" + ",".join(missing)
                )
            normalized = dict(evidence)
            normalized.pop("lifecycle_events", None)
            normalized["query_lifecycle"] = lifecycle
            normalized["_lifecycle_derivation_token"] = _LIFECYCLE_DERIVATION_TOKEN
            result = PersistentAmbiguityReferentialRuntime.evaluate_decision_opportunity_evidence(normalized)
            downstream_owner = {
                "owner_module": result["owner_module"],
                "owner_class": result["owner_class"],
                "owner_function": result["owner_function"],
            }
            result["owner_module"] = "driveclarify_persistent_ambiguity_runtime_v1.runtime"
            result["owner_class"] = "PersistentAmbiguityReferentialRuntime"
            result["owner_function"] = "evaluate_decision_opportunity_lifecycle"
            result["downstream_decision_owner"] = downstream_owner
            result["gate_trace"]["lifecycle_events_executed"] = list(events)
            result["gate_trace"]["lifecycle_transition_trace"] = transitions
            result["gate_trace"]["lifecycle_derived_not_injected"] = True
            return result
        except EvidenceContractError as error:
            return {
                "decision": PersistentDecision.FALLBACK.value,
                "reason_codes": ["LIFECYCLE_CONTRACT_ERROR_FAIL_CLOSED", str(error)],
                "owner_module": "driveclarify_persistent_ambiguity_runtime_v1.runtime",
                "owner_class": "PersistentAmbiguityReferentialRuntime",
                "owner_function": "evaluate_decision_opportunity_lifecycle",
                "downstream_decision_owner": None,
                "gate_trace": {
                    "lifecycle_events_executed": [row["event"] for row in transitions],
                    "lifecycle_transition_trace": transitions,
                    "query_lifecycle": lifecycle,
                    "lifecycle_derived_not_injected": True,
                    "lifecycle_contract_error": str(error),
                },
                "authorization_eligible": False,
                "evidence_contract_error": type(error).__name__,
            }

    @staticmethod
    def evaluate_decision_opportunity_evidence(evidence: Any) -> dict[str, Any]:
        """Evaluate sealed CPU evidence through the production M2B owner.

        The input has no desired/expected decision field.  Malformed evidence
        is an expected contract failure and therefore returns structured
        FALLBACK; unexpected implementation defects still propagate.
        """

        def optional_bool(value: Any, name: str) -> Optional[bool]:
            if value is None or isinstance(value, bool):
                return value
            raise EvidenceNormalizationError(name + "_MUST_BE_BOOL_OR_NULL")

        try:
            if not isinstance(evidence, Mapping):
                raise EvidenceNormalizationError("DECISION_EVIDENCE_WRONG_TOP_LEVEL_TYPE")
            count = evidence.get("active_candidate_count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise EvidenceNormalizationError("ACTIVE_CANDIDATE_COUNT_INVALID")
            try:
                current_relation = AxisValue(evidence.get("current_action_relation", "UNKNOWN"))
                future_relation = AxisValue(evidence.get("future_obligation_relation", "UNKNOWN"))
            except (TypeError, ValueError) as error:
                raise EvidenceNormalizationError("INVALID_NESTED_RELATION_ENUM") from error
            lifecycle = evidence.get("query_lifecycle", {})
            if not isinstance(lifecycle, Mapping):
                raise EvidenceNormalizationError("QUERY_LIFECYCLE_WRONG_NESTED_TYPE")
            if lifecycle and evidence.get("_lifecycle_derivation_token") is not _LIFECYCLE_DERIVATION_TOKEN:
                raise EvidenceProvenanceError("STATIC_QUERY_LIFECYCLE_CONTEXT_FORBIDDEN")
            active_query = optional_bool(lifecycle.get("active_query", False), "ACTIVE_QUERY") is True
            answer_pending = optional_bool(lifecycle.get("answer_pending", False), "ANSWER_PENDING") is True
            holding_valid = optional_bool(lifecycle.get("holding_valid", False), "HOLDING_VALID") is True
            lease_valid = optional_bool(lifecycle.get("lease_valid", False), "LEASE_VALID") is True
            query_bound = optional_bool(lifecycle.get("query_bound", False), "QUERY_BOUND") is True
            ask_emitted = optional_bool(lifecycle.get("ask_emitted", False), "ASK_EMITTED") is True
            verified_holding = bool(
                ask_emitted and active_query and answer_pending and holding_valid and lease_valid and query_bound
            )
            context = PersistentDecisionContext(
                active_candidate_count=count,
                semantic_state=str(evidence.get("semantic_state", "UNKNOWN")),
                current_action_relation=current_relation,
                future_obligation_relation=future_relation,
                evidence_fresh=optional_bool(evidence.get("evidence_fresh"), "EVIDENCE_FRESH"),
                full_plan_coverage=optional_bool(evidence.get("full_plan_coverage"), "FULL_PLAN_COVERAGE"),
                alignment_verified=optional_bool(evidence.get("alignment_verified"), "ALIGNMENT_VERIFIED"),
                shared_action_safe=optional_bool(evidence.get("shared_action_safe"), "SHARED_ACTION_SAFE"),
                recoverable=optional_bool(evidence.get("recoverable"), "RECOVERABLE"),
                latest_safe_slack_positive=optional_bool(evidence.get("lease_valid"), "LEASE_VALID"),
                decision_deadline_available=optional_bool(evidence.get("decision_deadline_available"), "DECISION_DEADLINE_AVAILABLE"),
                decision_deadline_crossed=optional_bool(evidence.get("decision_deadline_crossed"), "DECISION_DEADLINE_CROSSED"),
                hard_safety_gate=optional_bool(evidence.get("hard_safety_gate"), "HARD_SAFETY_GATE"),
                hard_rule_gate=optional_bool(evidence.get("hard_rule_gate"), "HARD_RULE_GATE"),
                active_query=active_query,
                active_holding_lease=active_query and lease_valid,
                multiple_plausible_interpretations=optional_bool(evidence.get("multiple_plausible_interpretations", False), "MULTIPLE_PLAUSIBLE") is True,
                material_consequence_divergence=optional_bool(evidence.get("material_consequence_divergence"), "MATERIAL_DIVERGENCE"),
                answer_changes_decision=optional_bool(evidence.get("answer_changes_decision", False), "ANSWER_CHANGES_DECISION") is True,
                positive_query_value=optional_bool(evidence.get("positive_query_value"), "POSITIVE_QUERY_VALUE"),
                query_budget_available=optional_bool(evidence.get("query_budget_available", False), "QUERY_BUDGET_AVAILABLE") is True,
                answer_likely_before_deadline=optional_bool(evidence.get("answer_likely_before_deadline"), "ANSWER_LIKELY_BEFORE_DEADLINE"),
                passenger_resolvable=optional_bool(evidence.get("passenger_resolvable", False), "PASSENGER_RESOLVABLE") is True,
                verified_holding_available=verified_holding,
            )
            recommendation = decide_persistent(context)
            return {
                "decision": recommendation.decision.value,
                "reason_codes": list(recommendation.reason_codes),
                "owner_module": "driveclarify_persistent_ambiguity_runtime_v1.m2b_adapter",
                "owner_class": None,
                "owner_function": "decide_persistent",
                "gate_trace": {
                    "active_candidate_count": count,
                    "current_action_relation": current_relation.value,
                    "future_obligation_relation": future_relation.value,
                    "hard_safety_gate": context.hard_safety_gate,
                    "hard_rule_gate": context.hard_rule_gate,
                    "recoverable": context.recoverable,
                    "refresh_guaranteed": context.evidence_fresh,
                    "lease_valid": context.latest_safe_slack_positive,
                    "material_divergence": context.material_consequence_divergence,
                    "answer_timely": context.answer_likely_before_deadline,
                    "query_lifecycle": dict(lifecycle),
                    "verified_holding_available": verified_holding,
                },
                "authorization_eligible": recommendation.decision is not PersistentDecision.FALLBACK,
                "evidence_contract_error": None,
            }
        except EvidenceContractError as error:
            return {
                "decision": PersistentDecision.FALLBACK.value,
                "reason_codes": ["EVIDENCE_CONTRACT_ERROR_FAIL_CLOSED", str(error)],
                "owner_module": "driveclarify_persistent_ambiguity_runtime_v1.runtime",
                "owner_class": "PersistentAmbiguityReferentialRuntime",
                "owner_function": "evaluate_decision_opportunity_evidence",
                "gate_trace": {"malformed_evidence": True},
                "authorization_eligible": False,
                "evidence_contract_error": type(error).__name__,
            }

    def __init__(
        self,
        agent: Any,
        output_dir: str,
        *,
        raw_instruction: str,
        hard_gate_evidence_provider: Optional[HardGateEvidenceProvider] = None,
    ) -> None:
        self._dense_route_source_attribute = None
        self._persistent_initializing = True
        self._hard_gate_evidence_provider: HardGateEvidenceProvider = (
            hard_gate_evidence_provider
            if hard_gate_evidence_provider is not None
            else _FailClosedHardGateEvidenceProvider()
        )
        self._hard_gate_provider_object_identity = _provider_object_identity(
            self._hard_gate_evidence_provider
        )
        self._hard_gate_provider_binding = _production_provider_binding(
            self._hard_gate_evidence_provider
        )
        super().__init__(
            agent,
            output_dir,
            raw_instruction=raw_instruction,
            forward_provider_class=OfficialDreamingCandidateForwardProvider,
        )
        if not isinstance(
            self.forward_provider, OfficialDreamingCandidateForwardProvider
        ):
            raise RuntimeError("PERSISTENT_PRODUCTION_OFFICIAL_PROVIDER_BINDING_FAILED")
        # The legacy unique/A1 pilot is disabled in this branch.  The versioned
        # tagged-subject commit below owns the same sole plan-selection seam.
        self.limited_act = NullLimitedActCommitV0("PERSISTENT_PATH_MUTUALLY_EXCLUSIVE")
        self.shared_act = SharedActCommitV1(authority_enabled=self.control_enabled)
        self.persistent_store = PersistentAmbiguityStore()
        self.refresh_scheduler = CandidateEvidenceRefreshScheduler()
        self._episode_id: Optional[str] = None
        self._target_obligation_digests: dict[str, str] = {}
        self._candidate_local_navigation_mode = False
        self._candidate_local_navigation_mission: Optional[
            MissionNavigationContext
        ] = None
        self._candidate_local_navigation_obligations: dict[
            str, CandidateLocalNavigationObligation
        ] = {}
        self._candidate_local_navigation_forward_receipts: list[
            dict[str, Any]
        ] = []
        self._global_route_reconnection_bridge: Optional[
            GlobalRouteReconnectionBridge
        ] = None
        self._latest_navigation_compass_radians: Optional[float] = None
        self._candidate_local_navigation_resolver = getattr(
            agent, "driveclarify_candidate_local_navigation_resolver", None
        )
        self._runtime_route_version: Optional[str] = None
        self._runtime_environment_digest: Optional[str] = None
        self._candidate_connectors: list[dict[str, Any]] = []
        self._latest_bundle = None
        self._latest_window = None
        self._latest_unique_observation = None
        self._shared_window_store_consumed = False
        self._material_context_invalidated = False
        self._authority_mode: Optional[str] = None
        self._active_persistent_query_id: Optional[str] = None
        self._refresh_sequence = 0
        self._legacy_initial_candidate_plan_calls = 0
        self._legacy_m2b_calls = 0
        self._legacy_arm_calls = 0
        self._m3_act_transactions: list[dict[str, Any]] = []
        self._persistent_decision_history: list[dict[str, Any]] = []
        self._method_decision_history: list[dict[str, Any]] = []
        self._method_m3_transactions: list[dict[str, Any]] = []
        self._method_decision_envelope: Optional[MethodDecisionEnvelope] = None
        self._internal_lifecycle_state: Optional[str] = None
        self._initial_k1_pending = False
        self._initial_k1_emitted = False
        self._convergence_replan_pending = False
        self._convergence_evidence: Optional[CandidateConvergenceEvidence] = None
        self._convergence_selected_candidate_id: Optional[str] = None
        self._convergence_old_bundle_version: Optional[str] = None
        self._consumed_convergence_evidence_ids: set[str] = set()
        self._convergence_observer_enabled = _truthy(
            os.environ.get(CONVERGENCE_OBSERVER_ENV)
        )
        self._convergence_observer: Optional[
            RuntimeGroundingConvergenceObserver
        ] = None
        self._decision_evidence_v2_enabled = _truthy(
            os.environ.get(DECISION_EVIDENCE_V2_FEATURE_FLAG)
        )
        self._decision_evidence_v3_enabled = _truthy(
            os.environ.get(DECISION_EVIDENCE_V3_FEATURE_FLAG)
        )
        # RQ2-T is an observational, default-off sidecar.  Its constructor and
        # calls are intentionally lazy so the frozen production policy has no
        # new dependency or behavior when the feature flag is absent.  Any
        # instrumentation failure is recorded and fails open with respect to
        # driving; it never changes a decision or a control value.
        self._rq2_t_temporal_observer = None
        self._rq2_t_temporal_observer_errors: list[dict[str, str]] = []
        if _truthy(os.environ.get("DRIVECLARIFY_RQ2_T_TEMPORAL_OBSERVER")):
            try:
                from driveclarify_rq2_t.observer import (  # noqa: PLC0415
                    RuntimeTemporalEvidenceObserver,
                )

                if not self._decision_evidence_v3_enabled:
                    raise RuntimeError("RQ2_T_REQUIRES_DECISION_EVIDENCE_V3")
                self._rq2_t_temporal_observer = (
                    RuntimeTemporalEvidenceObserver.from_environment(
                        default_output_dir=self.output_dir
                    )
                )
            except (ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
                self._rq2_t_temporal_observer_errors.append(
                    {"type": type(error).__name__, "message": str(error)}
                )
        self._answer_conditioned_reconnect_enabled = _truthy(
            os.environ.get(ANSWER_CONDITIONED_RECONNECT_ENV)
        )
        self._method_revision_v2_enabled = _truthy(
            os.environ.get(METHOD_REVISION_V2_ENV)
        )
        self._method_v2_1_enabled = bool(
            self._method_revision_v2_enabled
            and self._decision_evidence_v3_enabled
            and _truthy(os.environ.get(METHOD_V2_1_RULE_GATE_DECOUPLING_ENV))
        )
        self._method_v2_3_enabled = bool(
            self._method_v2_1_enabled
            and _truthy(
                os.environ.get(METHOD_V2_3_PRE_ACTIVATION_FEASIBILITY_ENV)
            )
        )
        self._method_v2_4_enabled = bool(
            self._method_v2_3_enabled
            and _truthy(
                os.environ.get(
                    METHOD_V2_4_SIMULATION_EXECUTION_OPPORTUNITY_ENV
                )
            )
        )
        self._method_v2_5_enabled = bool(
            self._method_v2_4_enabled
            and _truthy(os.environ.get(METHOD_V2_5_COMPLETION_HANDOVER_ENV))
        )
        self._method_v2_6_enabled = bool(
            self._method_v2_5_enabled
            and _truthy(
                os.environ.get(METHOD_V2_6_GENERIC_DOWNSTREAM_LANDING_ENV)
            )
        )
        self._method_v2_7_enabled = bool(
            self._method_v2_6_enabled
            and _truthy(os.environ.get(METHOD_V2_7_ASK_BASELINE_WAIT_ENV))
        )
        self._method_v2_8_enabled = bool(
            self._method_v2_7_enabled
            and _truthy(
                os.environ.get(METHOD_V2_8_TOPOLOGY_LOCKED_HANDOVER_ENV)
            )
        )
        self._method_v3_enabled = bool(
            self._method_v2_8_enabled
            and _truthy(os.environ.get(METHOD_V3_EXISTING_ROUTE_BINDING_ENV))
        )
        self._method_v2_7_boundary = None
        self._method_v2_7_continuation = None
        self._method_v2_7_semantic_deadline_monotonic = None
        self._method_v2_7_current_progress_m = None
        self._method_v2_7_provisional_ambiguity: Optional[dict[str, str]] = None
        self._method_v2_execution = (
            RouteBoundManeuverExecutionV3()
            if self._method_v3_enabled
            else TopologyLockedManeuverExecutionV28()
            if self._method_v2_8_enabled
            else SemanticBoundaryManeuverExecutionV27()
            if self._method_v2_7_enabled
            else GenericDownstreamLandingManeuverExecution()
            if self._method_v2_6_enabled
            else (
                CompletionHandoverManeuverExecution()
                if self._method_v2_5_enabled
                else BoundedSemanticManeuverExecution()
            )
        )
        self._method_v3_phase_owner = MethodV3PhaseOwner()
        self._method_v3_connector_phase_owner = ConnectorPhaseOwnerR2()
        self._method_v3_selected_route_binding = None
        self._method_v3_resolved_selected_local_route = None
        self._method_v3_connector_target_window_transaction_identity = None
        self._method_v2_selected_candidate: Any = None
        self._method_v2_selected_obligation: Optional[
            CandidateLocalNavigationObligation
        ] = None
        self._method_v2_selected_prompt: Optional[str] = None
        self._method_v2_pending_binding: Any = None
        self._method_v2_frozen_commitment_deadline_monotonic: Optional[float] = None
        self._method_v2_3_active_execution_deadline_monotonic: Optional[
            float
        ] = None
        self._method_v2_reconnect_frame: Optional[int] = None
        self._method_v2_timing: dict[str, Any] = {}
        self._method_v2_1_plan_gate = PlanActivationGate()
        self._method_v2_1_staged_plan: Optional[dict[str, Any]] = None
        self._method_v2_1_execution_budget: Optional[
            ManeuverExecutionBudgetLedger
        ] = None
        self._method_v2_1_timeline: list[dict[str, Any]] = []
        self._method_v2_3_pre_activation_feasibility_history: list[
            dict[str, Any]
        ] = []
        self._method_v2_3_active_last_world_xy_m: Optional[list[float]] = None
        self._method_v2_3_activation_frame_id: Optional[int] = None
        self._high_fidelity_step_timing_by_observation: dict[
            str, dict[str, Any]
        ] = {}
        self._high_fidelity_step_timing_rows: list[dict[str, Any]] = []
        self._high_fidelity_active_model_observation_id: Optional[str] = None
        self._candidate_forward_ledger: list[dict[str, Any]] = []
        self._candidate_trajectory_history: list[dict[str, Any]] = []
        self._gpu_oom_count = 0
        self._online_route_update_native_validation_enabled = bool(
            _truthy(os.environ.get(ONLINE_ROUTE_UPDATE_NATIVE_VALIDATION_ENV))
            or self._answer_conditioned_reconnect_enabled
        )
        self._phase_b_contract = self._load_phase_b_contract()
        self._receipt.update(
            {
                "schema_version": "driveclarify.persistent_ambiguity_runtime.v1",
                "runtime_version": RUNTIME_VERSION,
                "persistent_ambiguity_runtime_v1": True,
                "persistent_feature_flag": FEATURE_FLAG,
                "persistent_feature_flag_default": "OFF",
                "phase_b_gate_status": self._phase_b_contract["status"],
                "phase_b_contract_only_reuse": True,
                "historical_rgb_or_plan_authority_reuse": False,
                "legacy_initial_candidate_plan_call_count": 0,
                "legacy_candidate_repeat_count": 0,
                "legacy_m2b_call_count": 0,
                "legacy_arm_call_count": 0,
                "persistent_runtime_insertion_point": (
                    "P1_ON_MODEL_OUTPUT_BEFORE_LEGACY_CANDIDATE_TERMINAL_CONSUME"
                ),
                "authority_subject_type": None,
                "semantic_resolution_from_act_shared": False,
                "new_planner_count": 0,
                "new_pid_count": 0,
                "direct_vehicle_control_write_count": 0,
                "visualization_extra_forward_count": 0,
                "candidate_local_navigation_bridge_available": True,
                "candidate_local_navigation_mode": False,
                "candidate_local_navigation_binding_policy": (
                    "EXPLICIT_ATOMIC_ALL_OR_NOTHING_DEFAULT_OFF"
                ),
                "candidate_local_navigation_forward_receipts": [],
                "global_route_reconnection_live_installation_attempted": False,
                "global_route_reconnection_native_installation_validated": False,
                "online_route_update_native_validation_enabled": (
                    self._online_route_update_native_validation_enabled
                ),
                "online_route_update_native_validation_default": "OFF",
                "online_route_update_native_validation_selection_stimulus_count": 0,
                "online_route_update_native_validation_scientific_selection_claimed": (
                    False
                ),
                "answer_conditioned_reconnect_enabled": (
                    self._answer_conditioned_reconnect_enabled
                ),
                "answer_conditioned_reconnect_feature_flag": (
                    ANSWER_CONDITIONED_RECONNECT_ENV
                ),
                "answer_conditioned_reconnect_default": "OFF",
                "method_revision_v2_enabled": self._method_revision_v2_enabled,
                "method_revision_v2_feature_flag": METHOD_REVISION_V2_ENV,
                "method_revision_v2_feature_flag_default": "OFF",
                "method_revision_v2_execution": self._method_v2_execution.summary(),
                "method_revision_v2_timing": {},
                "method_revision_v2_low_level_owner": (
                    "ONE_NORMAL_SIMLINGO_FORWARD_PER_TICK_PLUS_EXISTING_CONTROL_PID"
                ),
                "method_revision_v2_fixed_n_tick_dependency": False,
                "method_v2_1_enabled": self._method_v2_1_enabled,
                "method_v2_1_feature_flag": METHOD_V2_1_RULE_GATE_DECOUPLING_ENV,
                "method_v2_1_feature_flag_default": "OFF",
                "method_v2_1_revision": (
                    "INFORMATION_ACTION_AUTHORITY_NOT_MOTION_EXECUTION_AUTHORITY"
                ),
                "method_v2_3_enabled": self._method_v2_3_enabled,
                "method_v2_3_feature_flag": (
                    METHOD_V2_3_PRE_ACTIVATION_FEASIBILITY_ENV
                ),
                "method_v2_3_feature_flag_default": "OFF",
                "method_v2_3_revision": (
                    "PRE_ACTIVATION_FEASIBILITY_SEPARATE_FROM_ACTIVE_EXECUTION_BUDGET"
                ),
                "method_v2_4_enabled": self._method_v2_4_enabled,
                "method_v2_4_feature_flag": (
                    METHOD_V2_4_SIMULATION_EXECUTION_OPPORTUNITY_ENV
                ),
                "method_v2_4_feature_flag_default": "OFF",
                "method_v2_4_execution_clock_owner": (
                    "FINITE_ORDERED_CARLA_SIMULATION_TIMESTAMP_DELTA_"
                    "DURING_ACTIVE_MOTION_ELIGIBILITY"
                    if self._method_v2_4_enabled
                    else "V2_3_LEGACY_MONOTONIC_WALL_CLOCK"
                ),
                "method_v2_5_enabled": self._method_v2_5_enabled,
                "method_v2_5_feature_flag": METHOD_V2_5_COMPLETION_HANDOVER_ENV,
                "method_v2_5_feature_flag_default": "OFF",
                "method_v2_5_handover_trigger": (
                    "MANEUVER_COMPLETED"
                    if self._method_v2_5_enabled
                    else "LEGACY_MANEUVER_COMMITTED"
                ),
                "method_v2_6_enabled": self._method_v2_6_enabled,
                "method_v2_6_feature_flag": (
                    METHOD_V2_6_GENERIC_DOWNSTREAM_LANDING_ENV
                ),
                "method_v2_6_feature_flag_default": "OFF",
                "method_v2_6_completion_owner": (
                    "PRE_DERIVED_SELECTED_BRANCH_DOWNSTREAM_TOPOLOGY"
                    if self._method_v2_6_enabled
                    else "DISABLED"
                ),
                "method_v2_3_pre_activation_feasibility_history": [],
                "method_v2_1_plan_activation": (
                    self._method_v2_1_plan_gate.summary()
                ),
                "method_v2_1_timeline": [],
                "candidate_inference_mode": (
                    "HIGH_FIDELITY_EVERY_OBSERVATION_K_WAY_REFERENCE"
                ),
                "event_triggered_candidate_scheduler_enabled": False,
                "candidate_consequence_cache_enabled": False,
                "every_unresolved_k_gt_1_observation_requires_fresh_bundle": True,
                "high_fidelity_timing_storage": "BUFFERED_UNTIL_RUNTIME_CLOSE",
                "high_fidelity_per_step_timing_path": (
                    HIGH_FIDELITY_TIMING_FILENAME
                ),
                "high_fidelity_per_step_timing_row_count": 0,
                "candidate_forward_ledger_path": HIGH_FIDELITY_LEDGER_FILENAME,
                "candidate_forward_ledger_row_count": 0,
                "candidate_trajectory_history_path": (
                    HIGH_FIDELITY_TRAJECTORY_FILENAME
                ),
                "candidate_trajectory_history_row_count": 0,
                "timing_instrumentation_extra_model_forward_count": 0,
                "timing_instrumentation_extra_pid_count": 0,
                "timing_instrumentation_extra_planner_count": 0,
                "timing_instrumentation_extra_vehicle_control_writer_count": 0,
                "persistent_decision_history": [],
                "method_v1_decision_envelope": None,
                "method_v1_decision_history": [],
                "method_v1_m3_transactions": [],
                "production_candidate_provider": (
                    type(self.forward_provider).__module__
                    + "."
                    + type(self.forward_provider).__qualname__
                ),
                "production_candidate_provider_object_identity": (
                    type(self.forward_provider).__module__
                    + "."
                    + type(self.forward_provider).__qualname__
                    + "@"
                    + format(id(self.forward_provider), "x")
                ),
                "production_candidate_adapter": (
                    self.forward_provider.adapter.implementation_id
                ),
                "production_candidate_provider_construction_path": (
                    "PersistentAmbiguityReferentialRuntime.__init__"
                    "->TopologyAwareReferentialRuntime.__init__"
                    "->ReferentialGroundedRuntime.__init__"
                ),
                "production_candidate_forward_call_site": (
                    "PersistentAmbiguityReferentialRuntime._forward[R4_4_ACTIVE]"
                    "|ReferentialGroundedRuntime._forward[LEGACY_DEFAULT_OFF]"
                    "->OfficialDreamingCandidateForwardProvider.__call__"
                ),
                "production_candidate_r4_4_active_forward_call_site": (
                    "PersistentAmbiguityReferentialRuntime._forward"
                    "->OfficialDreamingCandidateForwardProvider.__call__"
                ),
                "official_adapter_on_production_path": True,
                "internal_lifecycle_state": None,
                "control_source_receipts": [],
                "convergence_observer_feature_flag": CONVERGENCE_OBSERVER_ENV,
                "convergence_observer_feature_flag_default": "OFF",
                "convergence_observer_enabled": self._convergence_observer_enabled,
                "hard_gate_evidence_provider": (
                    type(self._hard_gate_evidence_provider).__module__
                    + "."
                    + type(self._hard_gate_evidence_provider).__qualname__
                ),
                "hard_gate_evidence_provider_object_identity": (
                    self._hard_gate_provider_object_identity
                ),
                "hard_gate_evidence_provider_identity": (
                    None
                    if self._hard_gate_provider_binding is None
                    else self._hard_gate_provider_binding["provider_id"]
                ),
                "hard_gate_evidence_provider_binding": (
                    self._hard_gate_provider_binding
                ),
                "production_provider_binding_verified": bool(
                    self._hard_gate_provider_binding is not None
                    and self._hard_gate_provider_binding[
                        "provider_object_identity"
                    ]
                    == self._hard_gate_provider_object_identity
                ),
                "hard_gate_evidence_history": [],
            }
        )
        if self._decision_evidence_v2_enabled:
            self._receipt.update(
                {
                    "schema_version": (
                        "driveclarify.persistent_ambiguity_runtime.v2"
                    ),
                    "runtime_version": (
                        "DRIVECLARIFY_DECISION_EVIDENCE_CONTRACT_V2.0"
                    ),
                    "decision_evidence_contract_version": "2.0",
                    "decision_evidence_v2_enabled": True,
                    "decision_evidence_v2_feature_flag": (
                        DECISION_EVIDENCE_V2_FEATURE_FLAG
                    ),
                }
            )
        if getattr(self, "_decision_evidence_v3_enabled", False):
            self._receipt.update(
                {
                    "schema_version": (
                        "driveclarify.persistent_ambiguity_runtime.v3"
                    ),
                    "runtime_version": (
                        "DRIVECLARIFY_DECISION_EVIDENCE_ARCHITECTURE_V3.1"
                    ),
                    "decision_evidence_contract_version": "3.1",
                    "decision_evidence_v3_enabled": True,
                    "decision_evidence_v3_feature_flag": (
                        DECISION_EVIDENCE_V3_FEATURE_FLAG
                    ),
                    "decision_evidence_v3_feature_flag_default": "OFF",
                    "v3_precedence_over_v2_when_both_enabled": True,
                }
            )
        self._persistent_initializing = False
        self._persist()

    @staticmethod
    def _load_phase_b_contract() -> dict[str, Any]:
        value = json.loads(PHASE_B_FINAL_RECEIPT.read_text(encoding="utf-8"))
        if value.get("status") != "PASS_PHASE_B_DECISION_WINDOW_PHYSICAL_EVIDENCE_COMPLETE":
            raise RuntimeError("PHASE_B_FINAL_GATE_NOT_PASSED")
        uncertainty = max(value["stability"]["coordinate_uncertainty_m"].values())
        coordinate_rows = []
        plan_point_counts = []
        for run_id in value.get("accepted_run_ids", ()):
            run_root = PHASE_B_FINAL_RECEIPT.parent / str(run_id)
            coordinate = json.loads(
                (run_root / "COORDINATE_FRAME_VALIDATION.json").read_text(
                    encoding="utf-8"
                )
            )
            coverage = json.loads(
                (run_root / "PLAN_COVERAGE_EVIDENCE.json").read_text(
                    encoding="utf-8"
                )
            )
            if coordinate.get("status") != "AVAILABLE" or coverage.get("status") != "AVAILABLE":
                raise RuntimeError("PHASE_B_ACCEPTED_RUN_COVERAGE_CONTRACT_UNAVAILABLE")
            coordinate_rows.append(coordinate["value"])
            plan_point_counts.extend(
                len(row["ego_local_forward_right_m"])
                for row in coverage.get("projected_plans", {}).values()
            )
        if not coordinate_rows or not plan_point_counts:
            raise RuntimeError("PHASE_B_COVERAGE_CALIBRATION_METADATA_MISSING")
        model_contract_ids = {
            str(row["model_coordinate_contract_id"]) for row in coordinate_rows
        }
        expected_spacings = {
            float(row["expected_checkpoint_spacing_m"]) for row in coordinate_rows
        }
        spacing_tolerances = {
            float(row["checkpoint_spacing_tolerance_m"]) for row in coordinate_rows
        }
        expected_point_counts = set(plan_point_counts)
        if not (
            len(model_contract_ids)
            == len(expected_spacings)
            == len(spacing_tolerances)
            == len(expected_point_counts)
            == 1
        ):
            raise RuntimeError("PHASE_B_COVERAGE_CALIBRATION_METADATA_UNSTABLE")
        return {
            "status": value["status"],
            "route_version": next(iter(value["stability"]["route_versions"].values())),
            "calibrated_uncertainty_upper_m": float(uncertainty),
            "model_coordinate_contract_id": next(iter(model_contract_ids)),
            "expected_plan_checkpoint_spacing_m": next(iter(expected_spacings)),
            "plan_checkpoint_spacing_tolerance_m": next(iter(spacing_tolerances)),
            "expected_plan_point_count": next(iter(expected_point_counts)),
            "basis_orthonormal_error_upper": max(
                float(row["basis_orthonormal_error"]) for row in coordinate_rows
            ),
            # These are the predeclared Phase B operating-contract bounds, not
            # a copied historical plan or physical state.
            "full_bundle_wall_timeout_s": 2.0,
            "m2b_m3_authority_upper_bound_s": 0.1,
            "control_response_upper_bound_frames": 2,
            "safety_margin_frames": 1,
            "normal_planning_interval_frames": 1,
        }

    def _route(self) -> Any:
        route, source_attribute = _detached_dense_route(self.agent)
        self._dense_route_source_attribute = source_attribute
        return route

    def _minimum_grounded_candidate_count(self, *, is_turn: bool) -> int:
        # Method V1 explicitly treats a single valid interpretation as ACT /
        # NO_AMBIGUITY.  Parent E1-R1 remains unchanged for all other users.
        del is_turn
        return 1

    def _ground(self, image: Any) -> None:
        super()._ground(image)
        effective_k = int(self._receipt.get("effective_k") or 0)
        if effective_k == 0:
            self._emit_zero_candidate_fallback()
            if _truthy(os.environ.get("DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED")):
                self._receipt.update(
                    {
                        "rq2_t_evidence_unavailable_reason": (
                            "CERTIFIED_AMBIGUITY_RUNTIME_GROUNDING_EFFECTIVE_K_LT_2"
                        ),
                        "rq2_t_measurement_remains_scientifically_valid": True,
                        "rq2_t_unknown_evidence_is_not_zero_imputed": True,
                    }
                )
            return
        if self._terminal or self._candidate_set is None:
            return
        if self._candidate_set.effective_k == 1 and len(self._bound_candidates) == 1:
            self._initial_k1_pending = True
            self._receipt.update(
                {
                    "status": "METHOD_V1_K1_WAITING_FOR_NORMAL_SIMLINGO_PLAN",
                    "ambiguity_state": "NO_AMBIGUITY",
                    "effective_k": 1,
                    "candidate_forward_count_for_initial_k1": 0,
                    "initial_k1_preserves_original_plan_ownership": True,
                }
            )
            if _truthy(os.environ.get("DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED")):
                self._receipt.update(
                    {
                        "rq2_t_evidence_unavailable_reason": (
                            "CERTIFIED_AMBIGUITY_RUNTIME_GROUNDING_EFFECTIVE_K_LT_2"
                        ),
                        "rq2_t_measurement_remains_scientifically_valid": True,
                        "rq2_t_unknown_evidence_is_not_zero_imputed": True,
                    }
                )
            return
        if self._candidate_set.effective_k < 2 or len(self._bound_candidates) < 2:
            self._emit_zero_candidate_fallback()
            return

        # R4.4 arbitration: when the language-driven semantic authority is
        # active it, and only it, decides candidate semantics.  The inherited
        # E1-R1 body above stays the topology/opportunity provider, but its
        # ordinal candidate<->opportunity semantic pairing and its map-derived
        # maneuver direction must not reach the prompt or the obligation.
        r4_4_semantic_authority_active = self._r4_4_semantic_authority_enabled()
        if r4_4_semantic_authority_active:
            self._apply_r4_4_semantic_authority()
            if self._candidate_set is None or len(self._bound_candidates) < 2:
                self._emit_zero_candidate_fallback()
                return

        # Reuse the Phase B-authorized consequence-aware renderer.  At this
        # point no numeric target has entered DrivingInput; the optional R4.4
        # exact-set resolver may arm candidate-local target binding only after
        # this existing K>=2 grounding path has completed.
        renderer = ConsequenceAwareOfficialDreamingRenderer()
        opportunities = list(self._receipt.get("maneuver_opportunities", ()))[:2]
        revised = []
        for index, (candidate, target) in enumerate(
            zip(
                ()
                if r4_4_semantic_authority_active
                else self._candidate_set.candidates[:2],
                opportunities,
            ),
            start=1,
        ):
            behavior = (
                "TURN_AT_UPCOMING_OPPORTUNITY"
                if index == 1
                else "CONTINUE_TO_LATER_OPPORTUNITY"
            )
            semantic = ConsequenceAwareGroundedSemantic(
                relation="AFTER",
                referring_expression=candidate.referring_expression,
                maneuver_direction=str(target["maneuver_direction"]),
                route_order_index=int(target["route_order_index"]),
                current_behavior=behavior,
                persistent_target_id=str(target["target_id"]),
                persistent_branch_id=str(target["branch_id"]),
            )
            prompt = renderer.render(semantic)
            revised.append(
                replace(
                    candidate,
                    prompt_text=prompt,
                    prompt_sha256=canonical_sha256(prompt),
                )
            )
            self._bound_candidates[index - 1].update(
                {
                    "prompt_text": prompt,
                    "conditioning_hash": canonical_sha256(prompt),
                    "current_behavior": behavior,
                    "candidate_specific_numeric_target": False,
                }
            )
        if not r4_4_semantic_authority_active:
            self._candidate_set = replace(
                self._candidate_set, candidates=tuple(revised)
            )

        route_rows = self.topology_enumerator._route_rows(self._route())
        self._runtime_route_version = DecisionWindowEvidenceAdapter.route_version_id(
            [[float(x), float(y)] for x, y, _ in route_rows],
            source="AGENT_OWNED_DENSE_CARLA_WORLD_ROUTE",
        )
        map_object = _live_map()
        map_name = str(getattr(map_object, "name", "UNKNOWN_LIVE_MAP"))
        self._runtime_environment_digest = canonical_sha256(
            {"map": map_name, "route_version": self._runtime_route_version}
        )
        if r4_4_semantic_authority_active:
            # R4.4 connector authority: the candidate set is owned by the
            # qualified semantic candidates, never by how many map opportunities
            # the legacy enumerator happened to publish.  Each semantic candidate
            # is bound to the connector of the opportunity IT acts at, keyed by
            # its own semantic identity -- no ordinal pairing, and no
            # len(opportunities) == len(candidates) legality condition.
            self._candidate_connectors = self._r4_4_semantic_candidate_connectors(
                map_object, route_rows, opportunities
            )
        else:
            self._candidate_connectors = _candidate_connector_evidence(
                map_object, route_rows, opportunities
            )
            for connector, candidate in zip(
                self._candidate_connectors, self._bound_candidates[:2]
            ):
                connector["candidate_id"] = str(candidate["candidate_id"])
        route_order_eligible, route_order_reason = (
            runtime_referent_route_order_authorization(
                self._candidate_set.grounding.selected_referents
            )
        )
        if not route_order_eligible:
            for connector in self._candidate_connectors:
                connector["status"] = "UNKNOWN"
                connector["reason_code"] = route_order_reason
                connector["route_order_authorization_eligible"] = False
        else:
            for connector in self._candidate_connectors:
                connector["route_order_authorization_eligible"] = True
        self._receipt["referent_route_order_authorization"] = {
            "value": True if route_order_eligible else None,
            "reason_code": route_order_reason,
            "authorization_eligible": route_order_eligible,
            "source": "RUNTIME_RGB_0_CATEGORICAL_LOCATION_AND_APPARENT_SIZE_RANK",
            "privileged_actor_truth_reads": 0,
        }
        self._receipt["candidate_executable_connector_evidence"] = self._candidate_connectors
        if r4_4_semantic_authority_active:
            # Durable D2 falsification evidence: the legacy opportunity count may
            # differ from the semantic candidate count without making the
            # candidate set illegal.
            self._receipt["r4_4_connector_authority"] = {
                "authority": "QUALIFIED_SEMANTIC_CANDIDATES",
                "binding_key": "SEMANTIC_CANDIDATE_ID",
                "legacy_opportunity_count": len(opportunities),
                "legacy_opportunity_role": "DIAGNOSTIC_HISTORICAL_TOPOLOGY_EVIDENCE",
                "legacy_opportunity_bounded_candidate_set": False,
                "legacy_ordinal_pairing_applied": False,
                "semantic_candidate_count": len(self._bound_candidates[:2]),
                "connector_row_count": len(self._candidate_connectors),
                "connector_candidate_ids": [
                    str(row.get("candidate_id")) for row in self._candidate_connectors
                ],
                "connector_semantic_constraints": [
                    str(row.get("r4_4_semantic_constraint") or "")
                    for row in self._candidate_connectors
                ],
                "cardinality_equality_gate_applied": False,
            }
        self._receipt["dense_route_source_attribute"] = self._dense_route_source_attribute
        self._receipt["runtime_route_version"] = self._runtime_route_version
        self._receipt["runtime_environment_digest"] = self._runtime_environment_digest

        candidates = []
        for row in self._bound_candidates[:2]:
            obligation = {
                "target_id": row["target_id"],
                "branch_id": row["branch_id"],
                "junction_id": row["junction_id"],
                "route_order_index": row["route_order_index"],
                "maneuver": row["maneuver"],
            }
            if r4_4_semantic_authority_active:
                # Under R4.4 two readings can act at the same opportunity and
                # differ only by their semantic constraint.  An obligation
                # identity blind to the constraint would collide, so the semantic
                # part of the identity is carried explicitly.  The legacy
                # projection above is left byte-identical when the flag is off.
                obligation = dict(obligation)
                obligation.update(
                    {
                        "r4_4_semantic_constraint": str(
                            row.get("r4_4_semantic_constraint") or ""
                        ),
                        "r4_4_obligation_type": str(
                            row.get("r4_4_obligation_type") or ""
                        ),
                        "semantic_sha256": str(row.get("semantic_sha256") or ""),
                    }
                )
            obligation_digest = canonical_sha256(obligation)
            self._target_obligation_digests[row["candidate_id"]] = obligation_digest
            candidates.append(
                CandidateIdentity(
                    candidate_id=str(row["candidate_id"]),
                    interpretation_id=str(row["interpretation_id"]),
                    semantic_sha256=str(row["semantic_sha256"]),
                    referent_lineage_id=str(row["referent_id"]),
                    referent_description=str(row["referring_expression"]),
                    target_obligation_id="obligation-" + obligation_digest[:20],
                    target_obligation_digest=obligation_digest,
                    topology_target_id=str(row["target_id"]),
                    topology_junction_id=str(row["junction_id"]),
                    topology_branch_id=str(row["branch_id"]),
                    topology_route_order=int(row["route_order_index"]),
                    source_observation_id=str(row["source_observation_id"]),
                    source_frame_id=row["source_frame_id"],
                    obligation_type="TURN_AFTER_REFERENT",
                    maneuver=str(row["maneuver"]),
                    event_relation="AFTER",
                    semantic_target_id="semantic-target-" + obligation_digest[:20],
                    referent_track_id=row.get("track_id"),
                    referent_identity_status=(
                        "VERIFIED_CURRENT" if row.get("track_id") else "SUPPORTED_CURRENT"
                    ),
                    provenance=(
                        "RUNTIME_RGB_0_GROUNDING",
                        "LIVE_CARLA_HD_MAP_TOPOLOGY",
                        "OFFICIAL_DREAMING_TEXT_ADAPTER",
                    ),
                )
            )
        self._episode_id = "ambiguity-episode-" + canonical_sha256(
            {
                "instruction": self.raw_instruction,
                "observation": self._latest_observation_id,
                "candidates": [row.semantic_sha256 for row in candidates],
            }
        )[:24]
        now = time.monotonic()
        self.persistent_store = self.persistent_store.apply(
            StoreEvent(
                episode_id=self._episode_id,
                event_id=self._episode_id + ":grounded",
                event_type="AMBIGUITY_GROUNDED",
                observed_monotonic_time=now,
                source_frame_id=self._latest_frame,
                reason_code="AMBIGUITY_K2_GROUNDED",
                payload={
                    "instruction_id": "instruction-" + canonical_sha256(self.raw_instruction)[:20],
                    "raw_instruction": self.raw_instruction,
                    "candidate_set_id": "semantic-set-" + canonical_sha256(
                        [row.semantic_sha256 for row in candidates]
                    )[:20],
                    "candidates": tuple(candidates),
                    "unresolved_slots": ("referent", "target_obligation"),
                    "source_observation_id": self._latest_observation_id,
                    "route_version": self._runtime_route_version,
                    "environment_digest": self._runtime_environment_digest,
                    "provenance": (
                        "P0_RUNTIME_GROUNDING",
                        "NO_GOLD_OR_EVALUATOR_READS",
                    ),
                },
            )
        )
        self._receipt.update(
            {
                "status": "PERSISTENT_AMBIGUITY_GROUNDED_REFRESH_REQUIRED",
                "ambiguity_episode_id": self._episode_id,
                "candidate_specific_numeric_target": False,
                "target_embedding_injected": False,
                "semantic_state": "UNRESOLVED",
            }
        )
        self._activate_candidate_local_navigation_resolver()
        if self._convergence_observer_enabled:
            phrase = str(self._candidate_set.candidates[0].referent_phrase)
            self._convergence_observer = RuntimeGroundingConvergenceObserver(
                detector=self.detector,
                phrase=phrase,
            )
            self._convergence_observer.initialize(
                candidate_rows=self._bound_candidates[:2],
                selected_referents=(
                    self._candidate_set.grounding.selected_referents[:2]
                ),
                image=image,
                frame_id=int(self._latest_frame),
                simulation_time=float(self._latest_simulation_time),
            )
            observer_audit = self._convergence_observer.audit()
            self._receipt["runtime_grounding_convergence_observer"] = observer_audit
            candidate_by_track = observer_audit.get("candidate_by_track", {})
            track_by_candidate = {
                str(candidate_id): str(track_id)
                for track_id, candidate_id in candidate_by_track.items()
            }
            for row in self._bound_candidates[:2]:
                row["track_id"] = track_by_candidate.get(str(row["candidate_id"]))

    def _runtime_window_observation(self, now: float) -> RuntimeWindowObservation:
        if self._runtime_route_version is None or self._runtime_environment_digest is None:
            raise RuntimeError("PERSISTENT_ROUTE_CONTEXT_MISSING")
        route_rows = self.topology_enumerator._route_rows(self._route())
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            hero = CarlaDataProvider.get_hero_actor()
            world = CarlaDataProvider.get_world()
            transform = hero.get_transform()
            location = transform.location
            forward = transform.get_forward_vector()
            right = transform.get_right_vector()
            velocity = hero.get_velocity()
            speed = math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2)
            speed_limit = float(hero.get_speed_limit()) / 3.6
            settings = world.get_settings()
            fixed_delta = float(settings.fixed_delta_seconds)
            waypoint = world.get_map().get_waypoint(location)
            lane_clearance = float(waypoint.lane_width) / 2.0 - float(
                hero.bounding_box.extent.y
            )
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as error:
            raise RuntimeError("RUNTIME_WINDOW_LIVE_CARLA_STATE_UNAVAILABLE") from error
        route_world_xy_m = tuple(
            (float(route_x), float(route_y)) for route_x, route_y, _ in route_rows
        )
        try:
            projection = DecisionWindowEvidenceAdapter.project_to_route(
                (float(location.x), float(location.y)), route_world_xy_m
            )
        except (TypeError, ValueError) as error:
            raise RuntimeError("RUNTIME_EGO_ROUTE_PROJECTION_UNKNOWN") from error
        if not math.isfinite(float(projection.progress_m)):
            raise RuntimeError("RUNTIME_EGO_ROUTE_PROJECTION_UNKNOWN")
        uncertainty = float(self._phase_b_contract["calibrated_uncertainty_upper_m"])
        commitments: dict[str, float] = {}
        onset_rows = []
        for connector in self._candidate_connectors[:2]:
            candidate_id = str(connector["candidate_id"])
            onset = _first_structural_divergence_progress(
                connector,
                lane_clearance_m=lane_clearance,
                calibrated_uncertainty_m=uncertainty,
                hysteresis_samples=3,
            )
            if (
                onset.get("status") != "AVAILABLE"
                and getattr(self, "_method_v2_7_enabled", False)
                and connector.get("junction_entry_progress_m") is not None
            ):
                # V2.7 uses the already-bound topology event when semantic
                # commitment is real but connector-vs-nominal geometry is not
                # divergent (notably execution-location FIRST).
                onset = {
                    "status": "AVAILABLE",
                    "progress_m": float(connector["junction_entry_progress_m"]),
                    "derivation": "V2_7_BOUND_SEMANTIC_TOPOLOGY_EVENT",
                    "raw_trajectory_divergence_used": False,
                }
            if onset.get("status") != "AVAILABLE":
                raise RuntimeError("RUNTIME_MANEUVER_ONSET_UNKNOWN:" + candidate_id)
            onset_rows.append(float(onset["progress_m"]))
            commitment = _conservative_commitment_progress(
                onset_progress_m=float(onset["progress_m"]),
                speed_upper_bound_mps=max(speed, speed_limit),
                fixed_delta_seconds=fixed_delta,
                control_response_upper_bound_frames=int(
                    self._phase_b_contract["control_response_upper_bound_frames"]
                ),
                safety_margin_distance_m=lane_clearance,
            )
            if commitment.get("status") != "AVAILABLE":
                raise RuntimeError("RUNTIME_COMMITMENT_UNKNOWN:" + candidate_id)
            commitments[candidate_id] = float(commitment["progress_m"])
        # The comparison is between two structures keyed by the SAME semantic
        # candidate identity, never between a candidate count and a legacy map
        # opportunity count.  Under R4.4 the connector rows are built per
        # semantic candidate, so a mismatch here means a genuinely missing or
        # unbound semantic candidate rather than a cardinality accident.
        if set(commitments) != set(self._target_obligation_digests):
            self._receipt["r4_4_connector_identity_mismatch"] = {
                "connector_candidate_ids": sorted(commitments),
                "obligation_candidate_ids": sorted(self._target_obligation_digests),
                "binding_key": "SEMANTIC_CANDIDATE_ID",
                "legacy_opportunity_count": len(
                    self._receipt.get("maneuver_opportunities") or ()
                ),
                "legacy_opportunity_bounded_candidate_set": False,
            }
            raise RuntimeError("RUNTIME_CONNECTOR_CANDIDATE_SET_MISMATCH")
        runtime_latency = (
            float(self._phase_b_contract["full_bundle_wall_timeout_s"])
            + float(self._phase_b_contract["m2b_m3_authority_upper_bound_s"])
            + fixed_delta * float(self._phase_b_contract["safety_margin_frames"])
        )
        connector_safety = bool(
            len(self._candidate_connectors) >= 2
            and all(
                row.get("status") == "AVAILABLE"
                and row.get("exit_reached") is True
                and row.get("live_map_pair_match") is True
                for row in self._candidate_connectors[:2]
            )
        )
        physical_projection_available = bool(
            getattr(hero, "is_alive", False)
            and lane_clearance > uncertainty
            and fixed_delta > 0.0
            and speed_limit > 0.0
        )
        hard_gate_evidence = self._resolve_hard_gate_evidence(
            decision_point="PERSISTENT_K_GE_2_RUNTIME_WINDOW"
        )
        current_physical_safety, hard_rule = hard_gate_evidence.authority_gates(
            self._latest_frame
        )
        # Preserve the V2 gate exactly.  V3 consumes the separately logged
        # physical projection so future route-order authorization cannot pose
        # as a current-motion safety predicate.
        dynamic_safety = bool(current_physical_safety and connector_safety)
        self._receipt["physical_projection_available_diagnostic"] = (
            physical_projection_available
        )
        maximum_planning_interval = fixed_delta * float(
            int(self._phase_b_contract["normal_planning_interval_frames"])
            + int(self._phase_b_contract["control_response_upper_bound_frames"])
            + int(self._phase_b_contract["safety_margin_frames"])
        )
        if getattr(self, "_method_v2_7_enabled", False):
            candidate_set_digest = canonical_sha256(
                dict(sorted(self._target_obligation_digests.items()))
            )
            family = str(
                self._candidate_connectors[0].get("r4_4_obligation_type") or ""
            )
            if family == "EXECUTION_LOCATION":
                ordinal_values = {"FIRST": 1, "SECOND": 2, "THIRD": 3}
                candidate_ordinals = {
                    str(row["candidate_id"]): ordinal_values[
                        str(row.get("r4_4_semantic_constraint") or "").upper()
                    ]
                    for row in self._candidate_connectors[:2]
                    if str(row.get("r4_4_semantic_constraint") or "").upper()
                    in ordinal_values
                }
                self._method_v2_7_boundary = derive_execution_location_boundary(
                    candidate_set_digest=candidate_set_digest,
                    candidate_ordinals=candidate_ordinals,
                    ordered_opportunities=tuple(
                        self._receipt.get("maneuver_opportunities") or ()
                    ),
                    route_version=self._runtime_route_version,
                    environment_digest=self._runtime_environment_digest,
                )
            else:
                self._method_v2_7_boundary = derive_maneuver_direction_boundary(
                    candidate_set_digest=candidate_set_digest,
                    candidate_branches=tuple(
                        {
                            "candidate_id": row["candidate_id"],
                            "exclusive_branch_identity": getattr(
                                self._candidate_local_navigation_obligations.get(
                                    str(row["candidate_id"])
                                ),
                                "local_branch_identity",
                                row.get("branch_id"),
                            ),
                            "exclusive_entry_progress_m": row.get(
                                "junction_entry_progress_m"
                            ),
                        }
                        for row in self._candidate_connectors[:2]
                    ),
                    route_version=self._runtime_route_version,
                    environment_digest=self._runtime_environment_digest,
                )
            next_progress_upper = (
                float(projection.progress_m)
                + max(speed, speed_limit) * maximum_planning_interval
                + uncertainty
            )
            lawful_holding = bool(
                getattr(self.wait, "enabled", False)
                and self.control_enabled
                and self._latest_frame is not None
                and self._latest_observation_id is not None
                and self._runtime_environment_digest is not None
            )
            self._method_v2_7_continuation = (
                assess_latest_reversible_baseline_continuation(
                    boundary=self._method_v2_7_boundary,
                    current_progress_m=float(projection.progress_m),
                    next_observation_progress_upper_m=next_progress_upper,
                    calibrated_uncertainty_m=uncertainty,
                    baseline_motion_admissible=bool(
                        current_physical_safety and hard_rule
                    ),
                    lawful_holding_available=lawful_holding,
                )
            )
            self._method_v2_7_current_progress_m = float(projection.progress_m)
            remaining = max(
                0.0,
                float(
                    self._method_v2_7_continuation.latest_reversible_progress_m
                )
                - float(projection.progress_m),
            )
            self._method_v2_7_semantic_deadline_monotonic = (
                float(now) + remaining / max(speed, speed_limit)
            )
            self._receipt["method_v2_7_semantic_commitment"] = {
                "boundary": self._method_v2_7_boundary.to_dict(),
                "continuation_state": (
                    self._method_v2_7_continuation.state.value
                ),
                "latest_reversible_progress_m": (
                    self._method_v2_7_continuation.latest_reversible_progress_m
                ),
                "raw_trajectory_divergence_is_sole_owner": False,
            }
        forward_xy = (float(forward.x), float(forward.y))
        right_xy = (float(right.x), float(right.y))
        basis_error = max(
            abs(math.hypot(*forward_xy) - 1.0),
            abs(math.hypot(*right_xy) - 1.0),
            abs(forward_xy[0] * right_xy[0] + forward_xy[1] * right_xy[1]),
        )
        model_local_transform_verified = bool(
            len(route_world_xy_m) >= 2
            and basis_error
            <= float(self._phase_b_contract["basis_orthonormal_error_upper"])
            + 1e-6
            and float(projection.projection_error_m) + uncertainty < lane_clearance
        )
        coordinate_transform_verified = bool(
            model_local_transform_verified
            and self._runtime_route_version == self._phase_b_contract["route_version"]
        )
        execution_latency_distance = max(speed, speed_limit) * fixed_delta * float(
            int(self._phase_b_contract["control_response_upper_bound_frames"])
            + int(self._phase_b_contract["safety_margin_frames"])
        )
        return RuntimeWindowObservation(
            source_observation_id=str(self._latest_observation_id),
            source_frame_id=self._latest_frame,
            observed_monotonic_time=float(now),
            route_version=self._runtime_route_version,
            environment_digest=self._runtime_environment_digest,
            current_progress_m=float(projection.progress_m),
            current_speed_mps=float(speed),
            speed_limit_mps=float(speed_limit),
            fixed_delta_seconds=fixed_delta,
            maximum_normal_planning_interval_s=maximum_planning_interval,
            calibrated_uncertainty_m=uncertainty,
            lane_clearance_m=lane_clearance,
            maneuver_onset_progress_m=min(onset_rows),
            candidate_commitment_progress_m=commitments,
            runtime_latency_upper_bound_s=runtime_latency,
            dynamic_safety_gate=dynamic_safety,
            hard_rule_gate=hard_rule,
            alignment_verified=(self._runtime_route_version == self._phase_b_contract["route_version"]),
            route_world_xy_m=route_world_xy_m,
            ego_location_xy_m=(float(location.x), float(location.y)),
            ego_forward_xy=forward_xy,
            ego_right_xy=right_xy,
            model_coordinate_contract_id=str(
                self._phase_b_contract["model_coordinate_contract_id"]
            ),
            expected_plan_checkpoint_spacing_m=float(
                self._phase_b_contract["expected_plan_checkpoint_spacing_m"]
            ),
            plan_checkpoint_spacing_tolerance_m=float(
                self._phase_b_contract["plan_checkpoint_spacing_tolerance_m"]
            ),
            expected_plan_point_count=int(
                self._phase_b_contract["expected_plan_point_count"]
            ),
            execution_latency_distance_m=float(execution_latency_distance),
            coordinate_transform_verified=coordinate_transform_verified,
            current_physical_safety_gate=current_physical_safety,
            model_local_transform_verified=model_local_transform_verified,
            model_local_planar_basis_error=float(basis_error),
            model_local_planar_basis_error_upper=float(
                self._phase_b_contract["basis_orthonormal_error_upper"]
            ) + 1e-6,
            route_projection_error_m=float(projection.projection_error_m),
            route_projection_segment_index=int(projection.segment_index),
            route_projection_segment_fraction=float(projection.segment_fraction),
        )

    def _refresh_candidates(self) -> tuple[RefreshCandidate, ...]:
        assert self._candidate_set is not None
        by_id = {row.candidate_id: row for row in self._candidate_set.candidates}
        episode = self.persistent_store.get(str(self._episode_id))
        return tuple(
            RefreshCandidate(
                candidate_id=row.candidate_id,
                interpretation_id=row.interpretation_id,
                semantic_sha256=row.semantic_sha256,
                target_obligation_digest=row.target_obligation_digest,
                payload=by_id[row.candidate_id],
            )
            for row in episode.candidates
            if row.candidate_id in episode.active_candidate_ids
        )

    def bind_candidate_local_navigation_obligations(
        self,
        mission: MissionNavigationContext,
        obligations: Sequence[CandidateLocalNavigationObligation],
        *,
        reconnection_bridge: Optional[GlobalRouteReconnectionBridge] = None,
    ) -> None:
        """Atomically enable R4.4 navigation for the exact active candidate set."""

        if self._candidate_local_navigation_mode:
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_ALREADY_ACTIVE")
        if self._candidate_forwards != 0:
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_AFTER_CANDIDATE_FORWARD")
        if self._candidate_set is None or self._latest_observation_id is None:
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_BEFORE_GROUNDING")
        if not isinstance(mission, MissionNavigationContext):
            raise TypeError("LOCAL_NAVIGATION_MISSION_CONTEXT_INVALID")
        mission.assert_endpoint_unchanged()
        rows = tuple(obligations)
        if not rows:
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_INCOMPLETE")
        by_id: dict[str, CandidateLocalNavigationObligation] = {}
        for obligation in rows:
            if not isinstance(obligation, CandidateLocalNavigationObligation):
                raise TypeError("LOCAL_NAVIGATION_OBLIGATION_TYPE_INVALID")
            if obligation.candidate_id in by_id:
                raise RuntimeError("LOCAL_NAVIGATION_BINDING_DUPLICATE_CANDIDATE")
            by_id[obligation.candidate_id] = obligation

        expected = {
            str(candidate.candidate_id): str(candidate.interpretation_id)
            for candidate in self._candidate_set.candidates
        }
        if set(by_id) != set(expected):
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_INCOMPLETE")
        for candidate_id, obligation in by_id.items():
            if obligation.interpretation_id != expected[candidate_id]:
                raise RuntimeError("LOCAL_NAVIGATION_INTERPRETATION_MISMATCH")
            if obligation.qualification_status is not QualificationStatus.QUALIFIED:
                raise RuntimeError("LOCAL_NAVIGATION_OBLIGATION_NOT_QUALIFIED")
            if (
                obligation.global_destination_identity
                != mission.global_destination_identity
            ):
                raise RuntimeError("LOCAL_NAVIGATION_DESTINATION_MISMATCH")
            if (
                obligation.nominal_global_route_identity
                != mission.nominal_global_route_identity
            ):
                raise RuntimeError("LOCAL_NAVIGATION_NOMINAL_ROUTE_MISMATCH")
            if obligation.mission_context_digest != mission.mission_context_digest:
                raise RuntimeError("LOCAL_NAVIGATION_MISSION_CONTEXT_MISMATCH")
            if str(obligation.source_observation_id) != str(
                self._candidate_set.candidates[
                    next(
                        index
                        for index, candidate in enumerate(
                            self._candidate_set.candidates
                        )
                        if candidate.candidate_id == candidate_id
                    )
                ].source_observation_id
            ):
                raise RuntimeError("LOCAL_NAVIGATION_SOURCE_IDENTITY_MISMATCH")
        planner_capability_status: Optional[str] = None
        planner_capability_provenance: Optional[str] = None
        planner_capability_identity_proven = False
        if reconnection_bridge is not None:
            if not isinstance(reconnection_bridge, GlobalRouteReconnectionBridge):
                raise TypeError("GLOBAL_RECONNECTION_BRIDGE_INVALID")
            if reconnection_bridge.mission != mission:
                raise RuntimeError("GLOBAL_RECONNECTION_MISSION_MISMATCH")
            # Reconnection takes its capability from the qualification
            # evaluator, so record that identity rather than a class name.
            capability = reconnection_bridge.planner_capability
            if (
                capability
                is not reconnection_bridge.recoverability_evaluator.planner_capability
                or reconnection_bridge.planner_object
                is not reconnection_bridge.recoverability_evaluator.planner_object
            ):
                raise RuntimeError("GLOBAL_RECONNECTION_PLANNER_IDENTITY_MISMATCH")
            for obligation in by_id.values():
                if (
                    obligation.planner_provenance is not None
                    and obligation.planner_provenance != capability.provenance
                ):
                    raise RuntimeError(
                        "GLOBAL_RECONNECTION_PLANNER_PROVENANCE_MISMATCH"
                    )
            planner_capability_status = capability.status.value
            planner_capability_provenance = capability.provenance
            planner_capability_identity_proven = True

        # Mutate only after the entire candidate set has passed validation.
        self._candidate_local_navigation_mission = mission
        self._candidate_local_navigation_obligations = dict(by_id)
        self._global_route_reconnection_bridge = reconnection_bridge
        self._candidate_local_navigation_mode = True
        for candidate_row in self._bound_candidates:
            obligation = by_id.get(str(candidate_row.get("candidate_id")))
            if obligation is not None:
                candidate_row.update(
                    {
                        "candidate_specific_numeric_target": True,
                        "local_navigation_obligation_digest": (
                            obligation.obligation_digest
                        ),
                        "local_navigation_branch_digest": obligation.branch_digest,
                        "local_navigation_target_digest": obligation.target_digest,
                    }
                )
        self._receipt.update(
            {
                "candidate_local_navigation_mode": True,
                "candidate_local_navigation_candidate_ids": sorted(by_id),
                "candidate_local_navigation_obligation_digests": {
                    key: value.obligation_digest
                    for key, value in sorted(by_id.items())
                },
                "candidate_local_navigation_destination_identity": (
                    mission.global_destination_identity
                ),
                "candidate_local_navigation_nominal_route_identity": (
                    mission.nominal_global_route_identity
                ),
                "candidate_local_navigation_no_nominal_fallback": True,
                "candidate_local_navigation_binding_complete": True,
                "candidate_specific_numeric_target": True,
                "target_embedding_injection_armed": True,
                "global_route_reconnection_bridge_bound": (
                    reconnection_bridge is not None
                ),
                "existing_planner_capability_status": planner_capability_status,
                "existing_planner_capability_provenance": (
                    planner_capability_provenance
                ),
                "qualification_and_reconnect_share_one_planner_capability": (
                    planner_capability_identity_proven
                ),
            }
        )

    def resolve_existing_global_route_planner_capability(
        self,
    ) -> ExistingPlannerCapability:
        """Resolve the one existing planner owner; never construct a planner.

        This is the production entry point: it reads the existing
        ``CarlaDataProvider._grp`` owner and reports UNAVAILABLE/MALFORMED
        instead of substituting a new planner.  The same returned capability is
        meant to serve both candidate qualification and post-local reconnection.
        """

        capability = ExistingPlannerCapability.from_carla_data_provider()
        planner_object = capability.planner_object
        self._receipt.update(
            {
                "existing_planner_capability_resolver": (
                    ExistingPlannerCapability.PROVENANCE_CARLA_DATA_PROVIDER_GRP
                ),
                "existing_planner_capability_resolved_status": (
                    capability.status.value
                ),
                "existing_grp_object_identity": (
                    None
                    if planner_object is None
                    else type(planner_object).__module__
                    + "."
                    + type(planner_object).__qualname__
                    + "@"
                    + format(id(planner_object), "x")
                ),
                "new_planner_count": 0,
            }
        )
        return capability

    def _r4_4_semantic_authority_enabled(self) -> bool:
        """True when this episode runs under the R4.4 semantic authority."""

        from driveclarify_scene_grounded_obligation_supplier import (  # noqa: PLC0415
            FEATURE_FLAG as SCENE_GROUNDED_FEATURE_FLAG,
        )

        if self._candidate_local_navigation_resolver is not None:
            # An explicitly injected resolver owns its own semantics; the
            # default arbitration must not silently rewrite its candidates.
            return False
        return _truthy(os.environ.get(SCENE_GROUNDED_FEATURE_FLAG))

    def _r4_4_semantic_candidate_connectors(
        self, map_object: Any, route_rows: Any, opportunities: Any
    ) -> list:
        """One connector per semantic candidate, keyed by semantic identity.

        The legacy ``_candidate_connector_evidence`` stays the single owner of the
        live-map lane-connector trace and is reused unchanged; only which rows are
        produced and how they are keyed changes.  A semantic candidate is bound to
        the opportunity it acts at, looked up by that candidate's own bound
        topology target rather than by ordinal position, so reordering the
        opportunities or the candidates cannot move a binding.  Legacy opportunity
        count is recorded as topology evidence and never bounds this set.
        """

        rows: list = []
        available = [row for row in (opportunities or []) if isinstance(row, Mapping)]
        by_target = {}
        for opportunity in available:
            key = str(opportunity.get("target_id"))
            if key not in by_target:
                by_target[key] = opportunity
        semantic_bindings = None
        if getattr(self, "_method_v2_7_enabled", False):
            semantic_bindings = bind_semantic_connector_targets(
                self._bound_candidates[:2], available
            )
        for candidate_index, candidate_row in enumerate(self._bound_candidates[:2]):
            candidate_id = str(candidate_row["candidate_id"])
            constraint = str(candidate_row.get("r4_4_semantic_constraint") or "")
            binding = (
                None if semantic_bindings is None else semantic_bindings[candidate_index]
            )
            opportunity = (
                by_target.get(str(candidate_row.get("target_id")))
                if binding is None
                else binding["opportunity"]
            )
            if opportunity is None:
                # No topology evidence for the opportunity this candidate acts
                # at.  That is incomplete evidence, never a licence to borrow
                # another candidate's connector.
                rows.append(
                    {
                        "candidate_id": candidate_id,
                        "status": "UNKNOWN",
                        "reason_code": "R4_4_SEMANTIC_CANDIDATE_OPPORTUNITY_UNRESOLVED",
                        "r4_4_semantic_constraint": constraint,
                        "r4_4_connector_authority": "QUALIFIED_SEMANTIC_CANDIDATES",
                        "legacy_opportunity_count": len(available),
                        "legacy_ordinal_pairing_applied": False,
                    }
                )
                continue
            traced = _candidate_connector_evidence(
                map_object, route_rows, [opportunity]
            )
            connector = dict(traced[0]) if traced else {}
            connector.update(
                {
                    "candidate_id": candidate_id,
                    "r4_4_semantic_constraint": constraint,
                    "r4_4_obligation_type": str(
                        candidate_row.get("r4_4_obligation_type") or ""
                    ),
                    "r4_4_semantic_sha256": str(
                        candidate_row.get("semantic_sha256") or ""
                    ),
                    "r4_4_connector_authority": "QUALIFIED_SEMANTIC_CANDIDATES",
                    "legacy_opportunity_count": len(available),
                    "legacy_opportunity_bounded_candidate_set": False,
                    "legacy_ordinal_pairing_applied": False,
                    "binding_key": "SEMANTIC_CANDIDATE_ID",
                    "target_owner": (
                        "BOUND_TARGET_ID" if binding is None
                        else binding["target_owner"]
                    ),
                }
            )
            if not traced:
                connector.setdefault("status", "UNKNOWN")
                connector.setdefault(
                    "reason_code", "R4_4_SEMANTIC_CANDIDATE_CONNECTOR_TRACE_EMPTY"
                )
            rows.append(connector)
        return rows

    def _apply_r4_4_semantic_authority(self) -> None:
        """Make the language pipeline the only semantic authority for R4.4.

        The inherited E1-R1 grounding body has already run and left two useful
        things behind: grounded referents and route-ordered map opportunities.
        It also left two things this path must not accept: a candidate identity
        derived from the map opportunity it happened to be paired with by
        ordinal, and a maneuver direction taken from the map.

        This seam keeps the topology evidence and replaces exactly the semantic
        part: the constraint each candidate carries, the identity that encodes
        it, and the language the frozen model will read.  Legacy E1-R1 source is
        untouched and keeps its historical behavior everywhere else.
        """

        from driveclarify_scene_grounded_obligation_supplier.interpretations import (  # noqa: PLC0415
            OBLIGATION_TYPE_FIELD,
            SEMANTIC_CONSTRAINT_FIELD,
            language_semantic_authority,
        )

        if self._candidate_set is None or not self._bound_candidates:
            return
        parsed_slots = self._receipt.get("parsed_slots") or {}
        authority = language_semantic_authority(parsed_slots)
        if authority is None:
            self._receipt["r4_4_semantic_authority"] = {
                "status": "NO_LANGUAGE_DERIVED_VALUE_SPACE",
                "semantic_candidate_writer_count": 0,
                "semantic_identity_depends_on_map": False,
            }
            return
        obligation_type, value_space, semantic_source = authority

        # The renderer needs a direction word.  For a direction ambiguity the
        # constraint itself supplies it.  For an execution-location ambiguity the
        # direction is not the unresolved slot, so it must come from the
        # instruction; if the words never state one, this seam refuses rather
        # than borrowing the map's direction.
        prompt_direction_from_language: Optional[str] = None
        if obligation_type != "MANEUVER_DIRECTION":
            from driveclarify_scene_grounded_obligation_supplier.interpretations import (  # noqa: PLC0415
                resolved_direction_from_language,
            )

            stated = resolved_direction_from_language(parsed_slots)
            if stated is None:
                self._receipt["r4_4_semantic_authority"] = {
                    "status": "LANGUAGE_STATES_NO_DIRECTION_FOR_NON_DIRECTION_FAMILY",
                    "obligation_type": obligation_type,
                    "semantic_candidate_writer_count": 0,
                    "semantic_identity_depends_on_map": False,
                    "map_direction_borrowed": False,
                }
                return
            prompt_direction_from_language = stated

        rows = self._bound_candidates
        stamped_rows: list[dict[str, Any]] = []
        identity_by_position: list[tuple[str, str, str, str]] = []
        for index, row in enumerate(rows):
            if index >= len(value_space):
                # The language admits fewer values than there are candidate
                # rows; surplus rows get no semantic stamp and therefore no
                # obligation.  K stays derived.
                break
            constraint = value_space[index]
            # Identity is derived from the words, the referent and the
            # observation only.  No junction/branch/target/order field enters
            # this projection, so reordering the map opportunities cannot move a
            # semantic identity.
            projection = {
                "obligation_type": obligation_type,
                "semantic_constraint": constraint,
                "semantic_source": semantic_source,
                "maneuver": str(row.get("maneuver") or ""),
                "event_relation": str(row.get("event_relation") or ""),
                "referring_expression": str(row.get("referring_expression") or ""),
                "referent_id": str(row.get("referent_id") or ""),
                "source_observation_id": str(row.get("source_observation_id") or ""),
            }
            semantic_sha = canonical_sha256(projection)
            candidate_id = "r44-cand-" + canonical_sha256(
                {
                    "semantic": semantic_sha,
                    "observation": str(row.get("source_observation_id") or ""),
                    "referent": str(row.get("referent_id") or ""),
                }
            )[:20]
            interpretation_id = "r44-meaning-" + semantic_sha[:20]
            identity_by_position.append(
                (candidate_id, interpretation_id, semantic_sha, constraint)
            )
            stamped_rows.append(row)

        if not stamped_rows:
            self._receipt["r4_4_semantic_authority"] = {
                "status": "NO_ROW_WITHIN_LANGUAGE_VALUE_SPACE",
                "semantic_candidate_writer_count": 0,
                "semantic_identity_depends_on_map": False,
            }
            return

        previous_ids = [str(row.get("candidate_id")) for row in stamped_rows]
        renderer = ConsequenceAwareOfficialDreamingRenderer()
        audit_rows: list[dict[str, Any]] = []
        for position, row in enumerate(stamped_rows):
            candidate_id, interpretation_id, semantic_sha, constraint = (
                identity_by_position[position]
            )
            if obligation_type == "MANEUVER_DIRECTION":
                # Both readings act at the same upcoming opportunity and differ
                # by direction, so neither reading is a "continue to a later
                # opportunity" reading.
                behavior = "TURN_AT_UPCOMING_OPPORTUNITY"
            else:
                behavior = (
                    "TURN_AT_UPCOMING_OPPORTUNITY"
                    if position == 0
                    else "CONTINUE_TO_LATER_OPPORTUNITY"
                )
            # The direction handed to the frozen renderer is the language
            # constraint, never the map's published maneuver_direction.
            prompt = renderer.render(
                ConsequenceAwareGroundedSemantic(
                    relation=str(row.get("event_relation") or "AFTER"),
                    referring_expression=str(row.get("referring_expression") or ""),
                    maneuver_direction=_R4_4_PROMPT_DIRECTION[
                        constraint
                        if prompt_direction_from_language is None
                        else prompt_direction_from_language
                    ],
                    route_order_index=int(row.get("route_order_index") or 1),
                    current_behavior=behavior,
                    persistent_target_id=str(row.get("target_id") or ""),
                    persistent_branch_id=str(row.get("branch_id") or ""),
                )
            )
            row.update(
                {
                    SEMANTIC_CONSTRAINT_FIELD: constraint,
                    OBLIGATION_TYPE_FIELD: obligation_type,
                    "candidate_id": candidate_id,
                    "interpretation_id": interpretation_id,
                    "semantic_sha256": semantic_sha,
                    "prompt_text": prompt,
                    "conditioning_hash": canonical_sha256(prompt),
                    "current_behavior": behavior,
                    "candidate_specific_numeric_target": False,
                    "semantic_authority": "LANGUAGE_SEMANTIC_PIPELINE",
                    "semantic_identity_source": (
                        "LANGUAGE_CONSTRAINT_PLUS_REFERENT_PLUS_OBSERVATION"
                    ),
                    "map_derived_maneuver_direction_used_for_semantics": False,
                    "legacy_ordinal_semantic_pairing_applied": False,
                    "topology_evidence_role": "EVIDENCE_ONLY_NOT_SEMANTIC_IDENTITY",
                }
            )
            audit_rows.append(
                {
                    "position": position,
                    "candidate_id": candidate_id,
                    "interpretation_id": interpretation_id,
                    "semantic_sha256": semantic_sha,
                    "obligation_type": obligation_type,
                    "semantic_constraint": constraint,
                    "semantic_source": semantic_source,
                    "current_behavior": behavior,
                    "prompt_sha256": canonical_sha256(prompt),
                    "superseded_legacy_candidate_id": previous_ids[position],
                    "map_published_maneuver_direction": row.get("maneuver_direction"),
                    "topology_junction_id": row.get("junction_id"),
                    "topology_branch_id": row.get("branch_id"),
                }
            )

        # Drop rows the language never admitted rather than leaving them bound
        # with stale legacy semantics.
        self._bound_candidates = stamped_rows
        by_previous_id = {
            previous_ids[position]: identity_by_position[position]
            for position in range(len(stamped_rows))
        }
        revised_candidates = []
        for candidate in self._candidate_set.candidates:
            identity = by_previous_id.get(str(candidate.candidate_id))
            if identity is None:
                continue
            candidate_id, interpretation_id, semantic_sha, _constraint = identity
            position = previous_ids.index(str(candidate.candidate_id))
            prompt = str(stamped_rows[position]["prompt_text"])
            revised_candidates.append(
                replace(
                    candidate,
                    candidate_id=candidate_id,
                    interpretation_id=interpretation_id,
                    semantic_sha256=semantic_sha,
                    prompt_text=prompt,
                    prompt_sha256=canonical_sha256(prompt),
                )
            )
        self._candidate_set = replace(
            self._candidate_set,
            candidates=tuple(revised_candidates),
            raw_candidates=tuple(revised_candidates),
            effective_k=len(revised_candidates),
        )
        self._receipt["r4_4_semantic_authority"] = {
            "status": "LANGUAGE_SEMANTIC_AUTHORITY_APPLIED",
            "authority": "LANGUAGE_SEMANTIC_PIPELINE",
            "obligation_type": obligation_type,
            "language_value_space": list(value_space),
            "semantic_source": semantic_source,
            "semantic_candidate_writer_count": 1,
            "semantic_writer": (
                "PersistentAmbiguityReferentialRuntime._apply_r4_4_semantic_authority"
            ),
            "legacy_ordinal_semantic_pairing_active": False,
            "legacy_e1_r1_role": "TOPOLOGY_OPPORTUNITY_PROVIDER_ONLY",
            "semantic_identity_depends_on_map": False,
            "map_can_override_language_direction": False,
            "k_derived_not_forced": True,
            "effective_k_after_arbitration": len(revised_candidates),
            "candidates": audit_rows,
        }

    def _default_scene_grounded_navigation_resolution(self) -> Any:
        """Production default: scene-ground language-derived interpretations.

        Only runs when the agent injected no explicit resolver and the
        default-off feature flag is enabled.  Returning ``None`` leaves
        candidate-local navigation disarmed, exactly as before.
        """

        from driveclarify_scene_grounded_obligation_supplier import (
            FEATURE_FLAG as SCENE_GROUNDED_FEATURE_FLAG,
            resolve_scene_grounded_candidate_local_navigation,
        )

        if not _truthy(os.environ.get(SCENE_GROUNDED_FEATURE_FLAG)):
            return None
        return resolve_scene_grounded_candidate_local_navigation(self)

    def _activate_candidate_local_navigation_resolver(self) -> None:
        """Bind trusted external obligations after the existing K>=2 gate."""

        resolver = self._candidate_local_navigation_resolver
        if resolver is None:
            # The explicitly injected resolver keeps priority; the production
            # scene-grounded supplier only takes over when none was injected.
            resolved = self._default_scene_grounded_navigation_resolution()
            if resolved is None:
                return
            self.bind_candidate_local_navigation_obligations(
                resolved.get("mission"),
                resolved.get("obligations", ()),
                reconnection_bridge=resolved.get("reconnection_bridge"),
            )
            self._receipt[
                "candidate_local_navigation_default_supplier_activated"
            ] = True
            return
        if callable(resolver):
            resolved = resolver(self)
        else:
            method = getattr(resolver, "resolve", None)
            if not callable(method):
                raise TypeError("LOCAL_NAVIGATION_RESOLVER_INVALID")
            resolved = method(self)
        if not isinstance(resolved, Mapping):
            raise TypeError("LOCAL_NAVIGATION_RESOLVER_RESULT_INVALID")
        self.bind_candidate_local_navigation_obligations(
            resolved.get("mission"),
            resolved.get("obligations", ()),
            reconnection_bridge=resolved.get("reconnection_bridge"),
        )
        self._receipt["candidate_local_navigation_resolver_activated"] = True

    def _latest_ego_navigation_pose(self) -> EgoPose2D:
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            hero = CarlaDataProvider.get_hero_actor()
            transform = hero.get_transform()
            self._receipt["candidate_navigation_ego_pose_source"] = (
                "CURRENT_LIVE_CARLA_WORLD_TRANSFORM"
            )
            return EgoPose2D(
                x_m=float(transform.location.x),
                y_m=float(transform.location.y),
                yaw_rad=math.radians(float(transform.rotation.yaw)),
            )
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as error:
            if getattr(self, "_method_v2_7_enabled", False):
                raise RuntimeError("LOCAL_NAVIGATION_EGO_WORLD_POSE_UNAVAILABLE") from error
        position = self._latest_position
        yaw = self._latest_navigation_compass_radians
        try:
            if position is not None and yaw is not None:
                return EgoPose2D(
                    x_m=float(position[0]),
                    y_m=float(position[1]),
                    yaw_rad=float(yaw),
                )
        except (IndexError, TypeError, ValueError):
            pass
        raise RuntimeError("LOCAL_NAVIGATION_EGO_POSE_UNAVAILABLE")

    def _forward(
        self,
        candidate: Any,
        repetition_id: str,
        *,
        latest: bool = False,
        fresh_local_replan_request: Any = None,
    ) -> Any:
        # Some existing fixture/harness construction paths intentionally use
        # ``__new__`` to exercise the legacy seam without running ``__init__``.
        # Absence of the opt-in field must therefore mean the documented
        # default-off behavior.
        if not getattr(self, "_candidate_local_navigation_mode", False):
            if fresh_local_replan_request is not None:
                raise RuntimeError("LOCAL_NAVIGATION_BINDING_NOT_ACTIVE")
            return super()._forward(candidate, repetition_id, latest=latest)
        obligation = self._candidate_local_navigation_obligations.get(
            str(candidate.candidate_id)
        )
        if obligation is None:
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_INCOMPLETE")
        if obligation.interpretation_id != str(candidate.interpretation_id):
            raise RuntimeError("LOCAL_NAVIGATION_INTERPRETATION_MISMATCH")
        mission = self._candidate_local_navigation_mission
        if mission is None:
            raise RuntimeError("LOCAL_NAVIGATION_MISSION_CONTEXT_MISSING")
        mission.assert_endpoint_unchanged()
        if obligation.mission_context_digest != mission.mission_context_digest:
            raise RuntimeError("LOCAL_NAVIGATION_MISSION_CONTEXT_MISMATCH")
        if self._latest_observation_id is None or self._latest_frame is None:
            raise RuntimeError("LOCAL_NAVIGATION_SOURCE_IDENTITY_MISMATCH")
        if fresh_local_replan_request is not None:
            # The prepared local-replan request owns the projection; the forward
            # consumes that exact object instead of re-deriving its own.
            if not isinstance(fresh_local_replan_request, FreshLocalReplanRequest):
                raise RuntimeError("LOCAL_NAVIGATION_FRESH_REPLAN_REQUEST_INVALID")
            binding = fresh_local_replan_request.forward_navigation_binding
            if (
                fresh_local_replan_request.candidate_id
                != str(candidate.candidate_id)
                or fresh_local_replan_request.selected_obligation_digest
                != obligation.obligation_digest
                or binding.obligation_digest != obligation.obligation_digest
                or binding.interpretation_id != str(candidate.interpretation_id)
            ):
                raise RuntimeError("LOCAL_NAVIGATION_FRESH_REPLAN_REQUEST_MISMATCH")
            if (
                binding.planning_observation_id != str(self._latest_observation_id)
                or binding.planning_frame_id != int(self._latest_frame)
            ):
                raise RuntimeError("LOCAL_NAVIGATION_SOURCE_IDENTITY_MISMATCH")
            if (
                fresh_local_replan_request.navigation_projection_digest
                != binding.projection_digest
            ):
                raise RuntimeError("LOCAL_NAVIGATION_PROJECTION_DIGEST_MISMATCH")
        else:
            binding = materialize_forward_binding(
                obligation,
                self._latest_ego_navigation_pose(),
                planning_observation_id=str(self._latest_observation_id),
                planning_frame_id=int(self._latest_frame),
            )
        runtime_candidate = _runtime_candidate(candidate, repetition_id)
        if latest:
            runtime_candidate = replace(
                runtime_candidate,
                source_observation_id=str(self._latest_observation_id),
                source_frame_id=int(self._latest_frame),
                candidate_input_digest=canonical_sha256(
                    {
                        "resolved": repetition_id,
                        "observation": self._latest_observation_id,
                    }
                ),
            )
        runtime_candidate = replace(
            runtime_candidate,
            candidate_input_digest=canonical_sha256(
                {
                    "base_candidate_input_digest": (
                        runtime_candidate.candidate_input_digest
                    ),
                    "local_navigation_obligation_digest": (
                        binding.obligation_digest
                    ),
                    "local_navigation_projection_digest": (
                        binding.projection_digest
                    ),
                }
            ),
        )
        # candidate.candidate_id is the SEMANTIC identity (the same string used
        # above to look the obligation up).  runtime_candidate.candidate_id is the
        # per-forward repetition identity, so the semantic one is stated here
        # instead of being inferred from a field that does not carry it.
        runtime_candidate = enrich_runtime_candidate(
            runtime_candidate,
            binding,
            semantic_candidate_id=str(candidate.candidate_id),
        )
        episode = _EpisodeView(
            ego_state=_EgoView(self._latest_speed),
            vision_observation=_VisionView(
                str(self._latest_observation_id), int(self._latest_frame)
            ),
        )
        self.forward_provider.begin_event()
        with self.forward_provider.candidate_local_navigation(binding):
            result = self.forward_provider(episode, runtime_candidate)
            scoped_forward_count = int(
                getattr(self.forward_provider, "event_forward_count", 1)
            )
        # Exactly one forward per installed binding, and the binding must be
        # released on scope exit so the next candidate cannot inherit it.
        if scoped_forward_count != 1:
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_FORWARD_COUNT_NOT_ONE")
        if (
            getattr(
                self.forward_provider,
                "_candidate_local_navigation_binding",
                None,
            )
            is not None
        ):
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_NOT_RELEASED")
        evidence = dict(getattr(result, "forward_evidence", {}) or {})
        try:
            evidence_target_points = tuple(
                (float(row[0]), float(row[1]))
                for row in evidence.get("target_points_ego_local_xy_m", ())
            )
        except (IndexError, TypeError, ValueError):
            evidence_target_points = ()
        if not (
            evidence.get("candidate_specific_target_point") is True
            and evidence.get("target_point_embedding_injected") is True
            and int(evidence.get("target_point_placeholder_count", 0)) == 2
            and str(evidence.get("candidate_id")) == binding.candidate_id
            and str(evidence.get("source_observation_id"))
            == binding.planning_observation_id
            and str(evidence.get("source_frame_id"))
            == str(binding.planning_frame_id)
            and str(evidence.get("local_navigation_obligation_digest"))
            == binding.obligation_digest
            and str(evidence.get("local_navigation_branch_digest"))
            == binding.branch_digest
            and str(evidence.get("local_navigation_target_digest"))
            == binding.target_digest
            and str(evidence.get("global_destination_identity"))
            == binding.global_destination_identity
            and str(evidence.get("mission_context_digest"))
            == binding.mission_context_digest
            and str(evidence.get("target_point_projection_digest"))
            == binding.projection_digest
            and str(evidence.get("planning_observation_id"))
            == str(self._latest_observation_id)
            and str(evidence.get("planning_frame_id")) == str(self._latest_frame)
            and evidence_target_points == binding.target_points_ego_local_xy_m
            and evidence.get("cause_of_difference")
            == "PASSENGER_INTERPRETATION"
            and evidence.get("candidate_specific_full_global_route_input") is False
        ):
            raise RuntimeError("LOCAL_NAVIGATION_FORWARD_EVIDENCE_MISMATCH")
        self._candidate_forwards += 1
        forward_receipt = {
            "candidate_id": binding.candidate_id,
            "interpretation_id": binding.interpretation_id,
            "planning_observation_id": binding.planning_observation_id,
            "planning_frame_id": binding.planning_frame_id,
            "obligation_digest": binding.obligation_digest,
            "branch_digest": binding.branch_digest,
            "target_digest": binding.target_digest,
            "projection_digest": binding.projection_digest,
            "navigation_projection_digest": binding.projection_digest,
            "target_points_ego_local_xy_m": [
                list(row) for row in binding.target_points_ego_local_xy_m
            ],
            "latest_replan": bool(latest),
            "global_planner_calls": 0,
            "cause_of_difference": "PASSENGER_INTERPRETATION",
            "fresh_local_replan_request_digest": (
                None
                if fresh_local_replan_request is None
                else fresh_local_replan_request.request_digest
            ),
            "forward_consumed_fresh_local_replan_request_binding": (
                fresh_local_replan_request is not None
                and fresh_local_replan_request.forward_navigation_binding is binding
            ),
            "binding_released_after_forward": True,
            "scoped_forward_count": scoped_forward_count,
        }
        if str(obligation.maneuver_family) == "MANEUVER_DIRECTION":
            predicted_points = _points(getattr(result, "raw_route", None))
            target_lateral_m = float(binding.target_points_ego_local_xy_m[-1][1])
            predicted_terminal_lateral_m = (
                None
                if not predicted_points
                else float(predicted_points[-1][1])
            )
            semantic_plan_realized = bool(
                target_lateral_m != 0.0
                and predicted_terminal_lateral_m is not None
                and predicted_terminal_lateral_m * target_lateral_m > 0.0
            )
            forward_receipt.update(
                {
                    "candidate_plan_semantics_realized": semantic_plan_realized,
                    "candidate_plan_semantics_owner": (
                        "MD_TARGET_AND_PREDICTED_TERMINAL_LATERAL_HALF_PLANE"
                    ),
                    "target_terminal_lateral_m": target_lateral_m,
                    "predicted_terminal_lateral_m": predicted_terminal_lateral_m,
                    "new_numeric_threshold_count": 0,
                }
            )
        else:
            predicted_points = _points(getattr(result, "raw_route", None))
            realization = assess_execution_location_plan_realization(
                target_points_ego_local_xy_m=(
                    binding.target_points_ego_local_xy_m
                ),
                predicted_route_ego_local_xy_m=predicted_points,
            )
            forward_receipt.update(
                {
                    "candidate_plan_semantics_realized": bool(
                        realization["realized"]
                    ),
                    "candidate_plan_semantics_owner": (
                        "EL_RAW_PLAN_REACHES_OWN_BOUND_TOPOLOGY_EVENT"
                    ),
                    "execution_location_plan_realization": realization,
                    "new_numeric_threshold_count": 0,
                }
            )
        self._candidate_local_navigation_forward_receipts.append(forward_receipt)
        self._receipt["candidate_local_navigation_forward_receipts"] = list(
            self._candidate_local_navigation_forward_receipts
        )
        self._receipt["target_embedding_injected"] = True
        return result

    def _prepare_candidate_local_fresh_replan_request(
        self,
        candidate: Any,
        *,
        old_bundle_invalidation_proven: bool,
    ) -> Any:
        """Record the distinct local-replan semantic before a latest forward."""

        if not getattr(self, "_candidate_local_navigation_mode", False):
            return None
        obligation = self._candidate_local_navigation_obligations.get(
            str(candidate.candidate_id)
        )
        if obligation is None:
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_INCOMPLETE")
        if obligation.interpretation_id != str(candidate.interpretation_id):
            raise RuntimeError("LOCAL_NAVIGATION_INTERPRETATION_MISMATCH")
        if self._latest_observation_id is None or self._latest_frame is None:
            raise RuntimeError("LOCAL_NAVIGATION_SOURCE_IDENTITY_MISMATCH")
        physically_invalidated = bool(
            old_bundle_invalidation_proven is True
            and self._latest_bundle is None
            and self._latest_window is None
        )
        # Materialize the candidate navigation first, then wrap that exact
        # projection so the request and the forward cannot diverge.
        binding = materialize_forward_binding(
            obligation,
            self._latest_ego_navigation_pose(),
            planning_observation_id=str(self._latest_observation_id),
            planning_frame_id=int(self._latest_frame),
        )
        request = build_fresh_local_replan_request(
            obligation,
            binding,
            old_candidate_bundle_invalidated=physically_invalidated,
        )
        rows = list(
            self._receipt.get(
                "candidate_local_navigation_fresh_local_replan_requests", ()
            )
        )
        rows.append(request.to_dict())
        self._receipt[
            "candidate_local_navigation_fresh_local_replan_requests"
        ] = rows
        return request

    def notify_candidate_local_branch_committed(
        self,
        candidate_id: str,
        latest_ego_planner_endpoint: Any,
        *,
        nominal_route_changed: bool,
    ) -> Any:
        """Emit the selected-only reconnect request at the active method handover."""

        if not self._candidate_local_navigation_mode:
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_NOT_ACTIVE")
        bridge = self._global_route_reconnection_bridge
        if bridge is None:
            raise RuntimeError("GLOBAL_RECONNECTION_BRIDGE_NOT_BOUND")
        obligation = self._candidate_local_navigation_obligations.get(
            str(candidate_id)
        )
        if obligation is None:
            raise RuntimeError("LOCAL_NAVIGATION_CANDIDATE_MISMATCH")
        resolved_index = getattr(self, "_resolved_index", None)
        candidate_set = getattr(self, "_candidate_set", None)
        if (
            type(resolved_index) is not int
            or candidate_set is None
            or resolved_index < 0
            or resolved_index >= len(candidate_set.candidates)
        ):
            raise RuntimeError("GLOBAL_RECONNECTION_SELECTED_CANDIDATE_UNPROVEN")
        selected_candidate_id = str(
            candidate_set.candidates[resolved_index].candidate_id
        )
        if selected_candidate_id != str(candidate_id):
            raise RuntimeError("GLOBAL_RECONNECTION_SELECTED_CANDIDATE_MISMATCH")
        request = bridge.commit_selected_local_branch(
            obligation,
            latest_ego_planner_endpoint,
            nominal_route_changed=nominal_route_changed,
        )
        self._receipt.update(
            {
                "global_route_reconnection_trigger": (
                    (
                        "LOCAL_MANEUVER_COMPLETED_AND_NOMINAL_ROUTE_CHANGED"
                        if getattr(self, "_method_v2_5_enabled", False)
                        else "LOCAL_BRANCH_COMMITTED_AND_NOMINAL_ROUTE_CHANGED"
                    )
                    if nominal_route_changed
                    else "KEEP_CURRENT_GLOBAL_ROUTE"
                ),
                "global_route_reconnection_request": (
                    None if request is None else request.to_dict()
                ),
                "global_route_state": bridge.state.value,
                "global_route_reconnection_live_installation_attempted": False,
                "global_route_reconnection_native_installation_validated": False,
            }
        )
        return request

    def perform_global_route_reconnection(self, request: Any = None) -> Any:
        """Compute with the existing GRP and install through the agent's one owner."""

        bridge = self._global_route_reconnection_bridge
        if bridge is None:
            raise RuntimeError("GLOBAL_RECONNECTION_BRIDGE_NOT_BOUND")
        receipt = bridge.reconnect(request)
        from driveclarify_scene_grounded_obligation_supplier.contracts import (  # noqa: PLC0415
            ROUTE_OWNER_ATTRIBUTE,
        )

        owner = getattr(getattr(self, "agent", None), ROUTE_OWNER_ATTRIBUTE, None)
        installation = getattr(owner, "last_installation_receipt", None)
        installation_error = getattr(owner, "last_installation_error", None)
        fresh_first_xyz = None
        if getattr(self, "_method_v2_5_enabled", False) and isinstance(
            installation, Mapping
        ):
            try:
                route = getattr(getattr(owner, "_route_planner"), "route")
                first_position = next(iter(route))[0]
                fresh_first_xyz = [float(value) for value in first_position[:3]]
            except (AttributeError, IndexError, StopIteration, TypeError, ValueError):
                fresh_first_xyz = None
        native_installation = bool(
            isinstance(installation, Mapping)
            and installation.get("committed") is True
            and installation.get("installed_route_identity")
            == installation.get("fresh_route_computed_identity")
            and installation.get("global_destination_identity_before")
            == installation.get("global_destination_identity_after")
        )
        self._receipt.update(
            {
                "global_route_reconnection_receipt": receipt.to_dict(),
                "global_route_state": bridge.state.value,
                "global_route_reconnection_existing_planner_call_count": (
                    bridge.planner_call_count
                ),
                "global_route_reconnection_live_installation_attempted": (
                    isinstance(installation, Mapping)
                ),
                "global_route_reconnection_native_installation_validated": (
                    native_installation
                ),
                "simlingo_online_route_update_receipt": (
                    None if installation is None else dict(installation)
                ),
                "simlingo_online_route_update_error": (
                    None if installation_error is None else dict(installation_error)
                ),
                "method_v2_5_fresh_route_first_xyz_m": fresh_first_xyz,
            }
        )
        return receipt

    def refresh_global_route_consumption_evidence(self) -> Any:
        """Record that a subsequent normal SimLingo tick consumed the new route."""

        from driveclarify_scene_grounded_obligation_supplier.contracts import (  # noqa: PLC0415
            ROUTE_OWNER_ATTRIBUTE,
        )

        owner = getattr(getattr(self, "agent", None), ROUTE_OWNER_ATTRIBUTE, None)
        reader = getattr(owner, "active_consumption_receipt", None)
        if not callable(reader):
            raise RuntimeError("ONLINE_ROUTE_CONSUMPTION_OWNER_UNAVAILABLE")
        evidence = reader()
        if not isinstance(evidence, Mapping):
            raise RuntimeError("ONLINE_ROUTE_CONSUMPTION_EVIDENCE_UNAVAILABLE")
        self._receipt["simlingo_online_route_update_receipt"] = dict(evidence)
        self._receipt["global_route_reconnection_next_tick_consumed"] = bool(
            evidence.get("installed_equals_next_tick_consumed") is True
        )
        return dict(evidence)

    @staticmethod
    def _current_live_ego_planner_endpoint() -> Any:
        """Read the simulator-owned live ego location used as reconnect origin."""

        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            hero = CarlaDataProvider.get_hero_actor()
            endpoint = hero.get_location()
            float(endpoint.x)
            float(endpoint.y)
            float(endpoint.z)
            return endpoint
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as error:
            raise RuntimeError("GLOBAL_RECONNECTION_LIVE_EGO_ENDPOINT_UNAVAILABLE") from error

    def _exercise_online_route_update_native_validation(self) -> None:
        """Exercise the production reconnect seam under an explicit native-only flag.

        The frozen engineering case has no natural semantic winner.  This hook
        therefore waits until the simulator-owned live ego position is within
        the existing route planner's continuity threshold of exactly one newly
        derived branch terminal.  That physically observed branch is an openly
        labelled validation stimulus.  It does not grant candidate control
        authority, does not alter the scientific decision, and restores the
        runtime's selection field immediately after the reconnect request.
        """

        if not getattr(
            self, "_online_route_update_native_validation_enabled", False
        ):
            return
        if self._receipt.get("global_route_reconnection_native_installation_validated"):
            return
        if not self._candidate_local_navigation_mode:
            return
        if self._global_route_reconnection_bridge is None:
            return
        if self._candidate_set is None:
            return
        if self._candidate_forwards < len(self._candidate_set.candidates):
            return

        answer_conditioned = bool(
            getattr(self, "_answer_conditioned_reconnect_enabled", False)
        )
        if answer_conditioned and not (
            self._authority_mode == "UNIQUE"
            and self._resolved_index is not None
            and self._receipt.get("persistent_answer_matched_active_query") is True
            and self._receipt.get("post_answer_decision") == "ACT"
        ):
            # A pre-answer proximity observation must never choose a semantic
            # candidate in the R4.5 scientific mode.
            return

        changed = [
            obligation
            for obligation in self._candidate_local_navigation_obligations.values()
            if obligation.changes_nominal_route is True
        ]
        if answer_conditioned:
            selected_candidate_id = str(
                self._candidate_set.candidates[self._resolved_index].candidate_id
            )
            changed = [
                obligation
                for obligation in changed
                if obligation.candidate_id == selected_candidate_id
            ]
        if not changed:
            self._receipt.update(
                {
                    "status": (
                        "BLOCKED_R4_4_NATIVE_VALIDATION_CHANGED_OBLIGATION_"
                        "UNAVAILABLE"
                    ),
                    "online_route_update_native_validation_derived_changed_count": (
                        len(changed)
                    ),
                }
            )
            return

        from driveclarify_scene_grounded_obligation_supplier.contracts import (  # noqa: PLC0415
            ROUTE_OWNER_ATTRIBUTE,
        )

        owner = getattr(getattr(self, "agent", None), ROUTE_OWNER_ATTRIBUTE, None)
        threshold = getattr(owner, "continuity_threshold_m", None)
        try:
            threshold = float(threshold)
        except (TypeError, ValueError) as error:
            raise RuntimeError("ONLINE_ROUTE_CONTINUITY_THRESHOLD_UNAVAILABLE") from error
        if not math.isfinite(threshold) or threshold <= 0.0:
            raise RuntimeError("ONLINE_ROUTE_CONTINUITY_THRESHOLD_INVALID")
        endpoint = self._current_live_ego_planner_endpoint()
        distances = {
            obligation.candidate_id: math.hypot(
                float(endpoint.x) - float(obligation.local_target.x_m),
                float(endpoint.y) - float(obligation.local_target.y_m),
            )
            for obligation in changed
            if obligation.local_target is not None
        }
        nearby = [
            obligation
            for obligation in changed
            if distances.get(obligation.candidate_id, math.inf) <= threshold
        ]
        self._receipt.update(
            {
                "online_route_update_native_validation_derived_changed_count": (
                    len(changed)
                ),
                "online_route_update_native_validation_branch_terminal_distances_m": (
                    dict(sorted(distances.items()))
                ),
                "online_route_update_native_validation_branch_commit_threshold_m": (
                    threshold
                ),
                "online_route_update_native_validation_nearby_branch_count": (
                    len(nearby)
                ),
            }
        )
        if len(nearby) != 1:
            return

        selected = nearby[0]
        by_id = {
            str(candidate.candidate_id): index
            for index, candidate in enumerate(self._candidate_set.candidates)
        }
        if selected.candidate_id not in by_id:
            raise RuntimeError("GLOBAL_RECONNECTION_SELECTED_CANDIDATE_MISMATCH")
        old_resolved_index = self._resolved_index
        try:
            if answer_conditioned:
                if self._resolved_index != by_id[selected.candidate_id]:
                    raise RuntimeError(
                        "ANSWER_CONDITIONED_RECONNECT_SELECTED_CANDIDATE_MISMATCH"
                    )
            else:
                self._resolved_index = by_id[selected.candidate_id]
            request = self.notify_candidate_local_branch_committed(
                selected.candidate_id,
                endpoint,
                nominal_route_changed=True,
            )
            reconnect_receipt = self.perform_global_route_reconnection(request)
        finally:
            self._resolved_index = old_resolved_index

        self._receipt.update(
            {
                "online_route_update_native_validation_selection_stimulus_count": 1,
                "online_route_update_native_validation_scientific_selection_claimed": (
                    answer_conditioned
                ),
                "online_route_update_native_validation_selection_source": (
                    "PASSENGER_ANSWER_RESOLVED_CANDIDATE_PLUS_LIVE_BRANCH_COMMITMENT"
                    if answer_conditioned
                    else "LIVE_EGO_UNIQUE_DERIVED_BRANCH_TERMINAL_PROXIMITY"
                ),
                "answer_conditioned_reconnect_selected_candidate_matches_answer": (
                    answer_conditioned
                    and str(self._receipt.get("answer_resolved_candidate_id"))
                    == selected.candidate_id
                ),
                "online_route_update_native_validation_selected_candidate_id": (
                    selected.candidate_id
                ),
                "online_route_update_native_validation_selected_obligation_digest": (
                    selected.obligation_digest
                ),
                "online_route_update_native_validation_reconnect_origin_source": (
                    "CURRENT_LIVE_EGO_LOCATION_FROM_CARLA_DATA_PROVIDER_HERO"
                ),
                "online_route_update_native_validation_reconnect_origin_xyz": [
                    float(endpoint.x),
                    float(endpoint.y),
                    float(endpoint.z),
                ],
                "online_route_update_native_validation_reconnect_state": (
                    reconnect_receipt.state.value
                ),
            }
        )

    def perform_fixture_global_route_reconnection(self, request: Any = None) -> Any:
        """Backward-compatible alias; production capability is now explicit."""

        return self.perform_global_route_reconnection(request)

    @staticmethod
    def _result_route_digest(result: Any) -> Optional[str]:
        return _tensor_digest(getattr(result, "raw_route", None))

    @staticmethod
    def _result_speed_digest(result: Any) -> Optional[str]:
        return _tensor_digest(getattr(result, "raw_speed", None))

    def _set_method_decision(self, envelope: MethodDecisionEnvelope) -> None:
        self._internal_lifecycle_state = None
        self._method_decision_envelope = envelope
        row = envelope.to_dict()
        row["sequence"] = len(self._method_decision_history) + 1
        self._method_decision_history.append(row)
        self._receipt.update(
            {
                "method_v1_decision_envelope": envelope.to_dict(),
                "method_v1_decision_history": list(self._method_decision_history),
                "decision_label": envelope.decision_label.value,
                "decision_reason": envelope.decision_reason,
                "control_source": envelope.control_source.value,
                "authority_subject": envelope.authority_subject,
                "authorization_status": envelope.authorization_status.value,
                "decision_why": envelope.decision_reason,
                "internal_lifecycle_state": None,
                "query_active": envelope.query_active,
                "answer_pending": envelope.answer_pending,
                "valid_holding_authorization": (
                    envelope.valid_holding_authorization
                ),
            }
        )

    def _set_internal_lifecycle_state(self, state: str) -> None:
        if type(state) is not str or not state:
            raise ValueError("METHOD_INTERNAL_LIFECYCLE_STATE_INVALID")
        self._internal_lifecycle_state = state
        self._method_decision_envelope = None
        self._decision = None
        dashboard = dict(self._receipt.get("decision_window_dashboard") or {})
        dashboard.update(
            {
                "decision": None,
                "decision_reason": None,
                "internal_lifecycle_state": state,
                "refresh_required": True,
            }
        )
        self._receipt.update(
            {
                "method_v1_decision_envelope": None,
                "decision_label": None,
                "decision_reason": None,
                "authorization_status": None,
                "decision_why": state,
                "internal_lifecycle_state": state,
                "query_active": False,
                "answer_pending": False,
                "valid_holding_authorization": False,
                "decision_window_dashboard": dashboard,
            }
        )

    def _refresh_method_dashboard(self, *, plan_id: Optional[str] = None) -> None:
        envelope = self._method_decision_envelope
        if envelope is None:
            return
        dashboard = dict(self._receipt.get("decision_window_dashboard") or {})
        grounding = self._receipt.get("grounding", {})
        raw_k = (
            grounding.get("raw_grounding_k")
            if isinstance(grounding, Mapping)
            else None
        )
        if raw_k is None:
            raw_k = self._receipt.get("raw_k")
        dashboard.update(
            {
                "method_v1_dashboard": True,
                "run_id": os.environ.get("DRIVECLARIFY_PROBE_RUN_ID")
                or str(self._episode_id or "METHOD-V1-K1"),
                "planning_event_id": envelope.source_planning_event,
                "raw_k": raw_k,
                "effective_k": envelope.effective_K,
                "ambiguity_state": envelope.ambiguity_state,
                "current_relation": envelope.relationship,
                "decision": envelope.decision_label.value,
                "decision_reason": envelope.decision_reason,
                "decision_subject": envelope.decision_subject,
                "bundle_version": envelope.candidate_bundle_version,
                "plan_id": plan_id
                or envelope.candidate_bundle_version
                or envelope.source_planning_event,
                "control_source": envelope.control_source.value,
                "authority_subject": envelope.authority_subject,
                "freshness": envelope.freshness,
                "source_frame": self._latest_frame,
                "forward_counters": (
                    "DINO {} | normal {} | candidates {} | fresh-unique {} | viz 0".format(
                        int(
                            self._receipt.get("dino_event_triggered_forward_count")
                            or 0
                        ),
                        int(getattr(self, "_normal_forwards", 0)),
                        int(getattr(self, "_candidate_forwards", 0)),
                        int(
                            self._receipt.get(
                                "convergence_fresh_unique_forward_count"
                            )
                            or 0
                        ),
                    )
                ),
            }
        )
        if self._receipt.get("candidate_convergence_evidence"):
            dashboard.update(
                {
                    "convergence_event": self._receipt[
                        "candidate_convergence_evidence"
                    ].get("evidence_id"),
                    "old_bundle_invalidated": self._receipt.get(
                        "convergence_old_bundle_invalidated"
                    ),
                    "refresh_required": envelope.freshness == "REFRESH_REQUIRED",
                    "fresh_plan_id": (
                        self._receipt.get("convergence_fresh_bundle_version")
                    ),
                }
            )
        self._receipt["decision_window_dashboard"] = dashboard

    def _repaint_method_dashboard(self) -> None:
        if (
            _truthy(
                os.environ.get(
                    "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_VISUALIZATION"
                )
            )
            and getattr(self, "_latest_image", None) is not None
        ):
            self._render()

    def _resolve_hard_gate_evidence(
        self, *, decision_point: str
    ) -> HardGateEvidenceEnvelope:
        observation_id = str(getattr(self, "_latest_observation_id", "UNKNOWN"))
        source_frame = getattr(self, "_latest_frame", None)
        try:
            provider = getattr(
                self, "_hard_gate_evidence_provider", _FailClosedHardGateEvidenceProvider()
            )
            provider_binding = getattr(
                self,
                "_hard_gate_provider_binding",
                _production_provider_binding(provider),
            )
            provider_object_identity = getattr(
                self,
                "_hard_gate_provider_object_identity",
                _provider_object_identity(provider),
            )
            evidence = provider.resolve_hard_gate_evidence(
                source_observation_id=observation_id,
                source_frame_id=source_frame,
                route_version=getattr(self, "_runtime_route_version", None),
                environment_digest=getattr(self, "_runtime_environment_digest", None),
            )
            if not isinstance(evidence, HardGateEvidenceEnvelope):
                raise EvidenceNormalizationError("HARD_GATE_PROVIDER_RESULT_TYPE_INVALID")
            evidence.validate(
                expected_source_observation_id=observation_id,
                expected_current_frame_id=source_frame,
                expected_provider_binding=provider_binding,
            )
            safety, rule = evidence.authority_gates(source_frame)
            row = evidence.to_dict()
        except EvidenceContractError as error:
            try:
                frame = int(source_frame)
            except (TypeError, ValueError):
                frame = None
            evidence = HardGateEvidenceEnvelope.unknown(
                source_observation_id=observation_id,
                source_frame_id=None,
                current_frame_id=frame,
                current_timestamp=getattr(self, "_latest_simulation_time", None),
                producer_id="RUNTIME_EVIDENCE_BOUNDARY",
                provider_id=(
                    None
                    if provider_binding is None
                    else provider_binding["provider_id"]
                ),
                provider_object_identity=(
                    provider_object_identity
                ),
                world_id=(
                    None
                    if provider_binding is None
                    else provider_binding["world_id"]
                ),
                route_id=(
                    None
                    if provider_binding is None
                    else provider_binding["route_id"]
                ),
                traffic_control_evidence_source=(
                    None
                    if provider_binding is None
                    else {
                        "source_id": provider_binding[
                            "traffic_control_evidence_source"
                        ],
                        "status": "UNKNOWN_FAIL_CLOSED",
                    }
                ),
                topology_evidence_source=(
                    None
                    if provider_binding is None
                    else {
                        "source_id": provider_binding[
                            "topology_evidence_source"
                        ],
                        "status": "UNKNOWN_FAIL_CLOSED",
                    }
                ),
                construction_provenance=(
                    ()
                    if provider_binding is None
                    else provider_binding[
                        "construction_provenance"
                    ]
                ),
            )
            provider_error = type(error).__name__
            safety, rule = False, False
            row = evidence.to_dict()
            row["evidence_error_reason_code"] = str(error)
            row["authorization_eligible"] = False
        except Exception as error:
            # Unknown programmer defects are never converted into evidence PASS
            # or silently swallowed as a routine provider failure.
            raise RuntimeError("BLOCKED_INTERNAL_IMPLEMENTATION_DEFECT") from error
        else:
            provider_error = None
            row["authorization_eligible"] = evidence.authorization_eligible(
                expected_source_observation_id=observation_id,
                expected_current_frame_id=source_frame,
            )
        row.update(
            {
                "decision_point": decision_point,
                "physical_safety_authority_gate": safety,
                "route_local_hard_rule_authority_gate": rule,
                "provider_error": provider_error,
                "provider_object_identity": (
                    provider_object_identity
                ),
                "provider_identity": (
                    None
                    if provider_binding is None
                    else provider_binding["provider_id"]
                ),
                "runtime_provider_identity_match": bool(
                    provider_binding is not None
                    and row.get("provider_id")
                    == provider_binding["provider_id"]
                    and row.get("provider_object_identity")
                    == provider_object_identity
                ),
            }
        )
        history = list(self._receipt.get("hard_gate_evidence_history", ()))
        history.append(row)
        self._receipt["hard_gate_evidence_history"] = history
        self._receipt["latest_hard_gate_evidence"] = row
        return evidence

    def _baseline_method_hard_gates(self) -> tuple[bool, bool]:
        evidence = self._resolve_hard_gate_evidence(decision_point="INITIAL_K1")
        return evidence.authority_gates(self._latest_frame)

    def _emit_zero_candidate_fallback(self) -> None:
        event_id = "grounding-{}-{}".format(
            self._latest_observation_id, self._latest_frame
        )
        fallback_m3 = build_method_m3_receipt(
            label=MethodDecisionLabel.FALLBACK,
            candidate_set_id=event_id,
            observed_monotonic_time=time.monotonic(),
        )
        self._method_m3_transactions.append(fallback_m3)
        envelope = MethodDecisionEnvelope.create(
            decision_label=MethodDecisionLabel.FALLBACK,
            decision_reason="NO_VALID_INTERPRETATION",
            ambiguity_state="NO_VALID_INTERPRETATION",
            effective_K=0,
            relationship="UNKNOWN_OR_INSUFFICIENT_EVIDENCE",
            decision_subject="BASELINE_SIMLINGO_PLAN",
            control_source=MethodControlSource.BASELINE_SIMLINGO,
            authorization_status=MethodAuthorizationStatus.FAIL_CLOSED,
            freshness="GROUNDING_INVALID",
            source_planning_event=event_id,
            candidate_bundle_version=None,
            authority_subject="BASELINE_CONTROL",
        )
        self._set_method_decision(envelope)
        self._decision = _PersistentDecisionView(MethodDecisionLabel.FALLBACK.value)
        self._receipt.update(
            {
                "status": "METHOD_V1_NO_VALID_INTERPRETATION_FALLBACK",
                "initial_decision": MethodDecisionLabel.FALLBACK.value,
                "effective_k": 0,
                "method_v1_m3_transactions": list(
                    self._method_m3_transactions
                ),
            }
        )
        self._refresh_method_dashboard(plan_id=event_id)
        self._repaint_method_dashboard()
        self._terminal = True

    def _emit_initial_k1_decision(
        self, baseline_route: Any, baseline_speed: Any
    ) -> None:
        if not self._initial_k1_pending or self._initial_k1_emitted:
            return
        now = time.monotonic()
        event_id = "k1-normal-plan-{}-{}".format(
            self._latest_observation_id, self._latest_frame
        )
        route_digest = _tensor_digest(baseline_route)
        speed_digest = _tensor_digest(baseline_speed)
        plan_valid = route_digest is not None and speed_digest is not None
        hard_safety, hard_rule = self._baseline_method_hard_gates()
        authorized = bool(plan_valid and hard_safety and hard_rule)
        label = MethodDecisionLabel.ACT if authorized else MethodDecisionLabel.FALLBACK
        reason = (
            "NO_AMBIGUITY"
            if authorized
            else "HARD_SAFETY_GATE_BLOCKED"
            if not hard_safety
            else "HARD_RULE_GATE_BLOCKED"
            if not hard_rule
            else "ORIGINAL_SIMLINGO_PLAN_INVALID"
        )
        candidate_set_id = "k1-original-plan-" + canonical_sha256(
            {
                "observation": self._latest_observation_id,
                "frame": self._latest_frame,
                "route": route_digest,
                "speed": speed_digest,
            }
        )[:20]
        m3_receipt = build_method_m3_receipt(
            label=label,
            candidate_set_id=candidate_set_id,
            observed_monotonic_time=now,
        )
        self._method_m3_transactions.append(m3_receipt)
        envelope = MethodDecisionEnvelope.create(
            decision_label=label,
            decision_reason=reason,
            ambiguity_state="NO_AMBIGUITY",
            effective_K=1,
            relationship="UNIQUE_VALID_INTERPRETATION",
            decision_subject="ORIGINAL_SIMLINGO_PLAN",
            # ACT and FALLBACK can share a physical source.  The label records
            # authorization; neither branch creates a second plan or PID path.
            control_source=MethodControlSource.ORIGINAL_SIMLINGO,
            authorization_status=(
                MethodAuthorizationStatus.AUTHORIZED
                if authorized
                else MethodAuthorizationStatus.FAIL_CLOSED
            ),
            freshness="FRESH_NORMAL_PLANNING_EVENT",
            source_planning_event=event_id,
            candidate_bundle_version=None,
            authority_subject="BASELINE_CONTROL",
        )
        self._set_method_decision(envelope)
        self._decision = _PersistentDecisionView(label.value)
        self._initial_k1_pending = False
        self._initial_k1_emitted = True
        self._receipt.update(
            {
                "status": (
                    "METHOD_V1_INITIAL_K1_ACT_ORIGINAL_PLAN"
                    if authorized
                    else "METHOD_V1_INITIAL_K1_FAIL_CLOSED_FALLBACK"
                ),
                "initial_decision": label.value,
                "initial_k1_reason": reason,
                "initial_k1_original_route_digest": route_digest,
                "initial_k1_original_speed_digest": speed_digest,
                "initial_k1_plan_object_preserved": True,
                "candidate_forward_count_for_initial_k1": 0,
                "method_v1_m3_transactions": list(
                    self._method_m3_transactions
                ),
                "initial_k1_plan_id": candidate_set_id,
            }
        )
        self._refresh_method_dashboard(plan_id=candidate_set_id)
        self._repaint_method_dashboard()
        # Method processing is complete; the original SimLingo/PID path keeps
        # running through select_plan_source without any second plan path.
        self._terminal = True

    @staticmethod
    def _fallback_reason_for_window(window: Any) -> str:
        relationship = str(window.candidate_relationship)
        if relationship == "UNKNOWN_OR_INSUFFICIENT_EVIDENCE":
            return "EVIDENCE_INSUFFICIENT"
        urgency = getattr(
            getattr(window, "v2_bundle", None), "clarification_window", None
        )
        if urgency is not None and str(urgency.urgency) == "TOO_LATE":
            return "TOO_LATE"
        v3_clarification = getattr(
            getattr(window, "v3_bundle", None), "clarification", None
        )
        if v3_clarification is not None and str(v3_clarification.state) == "TOO_LATE":
            return "TOO_LATE"
        return "DECISION_EVIDENCE_GATES_FAIL_CLOSED"

    def _record_recommendation_envelope(
        self, *, recommendation: Any, bundle: Any, window: Any, episode: Any
    ) -> None:
        label = MethodDecisionLabel(recommendation.decision.value)
        if label is MethodDecisionLabel.ACT_SHARED:
            source = MethodControlSource.ORIGINAL_SIMLINGO_SHARED_PREFIX
            status = MethodAuthorizationStatus.AUTHORIZED
            subject = "SHARED_EQUIVALENCE_CLASS"
            decision_subject = "SHARED_EQUIVALENCE_CLASS"
            reason = str(recommendation.reason_codes[0])
        elif label is MethodDecisionLabel.ASK:
            source = MethodControlSource.BASELINE_SIMLINGO
            status = MethodAuthorizationStatus.QUERY_AUTHORIZED
            subject = "CLARIFICATION_QUERY"
            decision_subject = "CLARIFICATION_QUERY"
            reason = str(recommendation.reason_codes[0])
        elif label is MethodDecisionLabel.WAIT:
            source = MethodControlSource.EXISTING_HOLDING
            status = MethodAuthorizationStatus.HOLD_AUTHORIZED
            subject = "M3_HOLDING_CONTROL"
            decision_subject = "ACTIVE_QUERY_HOLDING"
            reason = str(recommendation.reason_codes[0])
        elif (
            getattr(self, "_method_v2_7_enabled", False)
            and label is MethodDecisionLabel.ACT
            and getattr(getattr(recommendation, "v27", None), "lifecycle_state", "")
            in {
                "PROVISIONAL_AMBIGUITY_BASELINE_OBSERVATION",
                "NO_CURRENT_PLANNING_HORIZON_AMBIGUITY",
            }
        ):
            source = MethodControlSource.BASELINE_SIMLINGO
            status = MethodAuthorizationStatus.AUTHORIZED
            subject = "BASELINE_CONTROL"
            decision_subject = "BASELINE_SIMLINGO_PLAN"
            reason = str(recommendation.reason_codes[0])
        else:
            source = MethodControlSource.BASELINE_SIMLINGO
            status = MethodAuthorizationStatus.FAIL_CLOSED
            subject = "BASELINE_CONTROL"
            decision_subject = "BASELINE_SIMLINGO_PLAN"
            reason = self._fallback_reason_for_window(window)
        v27_pending_ask = bool(
            getattr(self, "_method_v2_7_enabled", False)
            and label is MethodDecisionLabel.ASK
            and episode.active_query is not None
        )
        self._set_method_decision(
            MethodDecisionEnvelope.create(
                decision_label=label,
                decision_reason=reason,
                ambiguity_state=episode.semantic_state.value,
                effective_K=(
                    1
                    if getattr(
                        getattr(recommendation, "v27", None),
                        "lifecycle_state",
                        "",
                    )
                    == "NO_CURRENT_PLANNING_HORIZON_AMBIGUITY"
                    else len(episode.active_candidate_ids)
                ),
                relationship=str(window.candidate_relationship),
                decision_subject=decision_subject,
                control_source=source,
                authorization_status=status,
                freshness=episode.evidence_state.value,
                source_planning_event=str(bundle.normal_planning_event_id),
                candidate_bundle_version=str(bundle.bundle_id),
                authority_subject=subject,
                query_active=(
                    label is MethodDecisionLabel.WAIT or v27_pending_ask
                ),
                answer_pending=(
                    (label is MethodDecisionLabel.WAIT or v27_pending_ask)
                    and episode.active_query is not None
                    and self._answer_received_sim_time is None
                ),
                valid_holding_authorization=(
                    label is MethodDecisionLabel.WAIT
                    and self.wait.active
                    and self.wait.state is not None
                    and self.wait.state.query_active
                ),
            )
        )

    def observe_candidate_convergence_evidence(
        self, evidence: CandidateConvergenceEvidence
    ) -> None:
        if self._episode_id is None or self._terminal:
            raise RuntimeError("CONVERGENCE_REQUIRES_ACTIVE_EPISODE")
        if not isinstance(evidence, CandidateConvergenceEvidence):
            raise TypeError("CONVERGENCE_EVIDENCE_TYPE_INVALID")
        if evidence.evidence_id in self._consumed_convergence_evidence_ids:
            return
        if (
            evidence.source_observation_id != str(self._latest_observation_id)
            or str(evidence.source_frame_id) != str(self._latest_frame)
        ):
            raise RuntimeError("CONVERGENCE_SOURCE_IDENTITY_NOT_CURRENT")
        episode = self.persistent_store.get(self._episode_id)
        active_before = tuple(episode.active_candidate_ids)
        if len(active_before) < 2 or episode.active_query is not None:
            raise RuntimeError("CONVERGENCE_REQUIRES_QUERY_FREE_ACTIVE_K2")
        rejected = tuple(evidence.rejected_candidate_ids)
        if any(value not in active_before for value in rejected):
            raise RuntimeError("CONVERGENCE_REJECTED_CANDIDATE_NOT_ACTIVE")
        old_bundle = self._latest_bundle
        self._convergence_old_bundle_version = (
            None if old_bundle is None else str(old_bundle.bundle_id)
        )
        if self.shared_act.subject is not None:
            self.shared_act.revoke(AuthorityReason.CANDIDATE_INVALIDATED)
        self.persistent_store = self.persistent_store.apply(
            StoreEvent(
                episode_id=self._episode_id,
                event_id=evidence.evidence_id,
                event_type="CANDIDATE_REJECTED_BY_EVIDENCE",
                observed_monotonic_time=evidence.observed_monotonic_time,
                source_frame_id=evidence.source_frame_id,
                reason_code="RUNTIME_EVIDENCE_CANDIDATE_REJECTED",
                payload={"candidate_ids": rejected},
            )
        )
        self._consumed_convergence_evidence_ids.add(evidence.evidence_id)
        self._latest_bundle = None
        self._latest_window = None
        self._authority_mode = None
        updated = self.persistent_store.get(self._episode_id)
        remaining = tuple(updated.active_candidate_ids)
        self._receipt.update(
            {
                "candidate_convergence_evidence": evidence.to_dict(),
                "convergence_old_bundle_version": (
                    self._convergence_old_bundle_version
                ),
                "convergence_old_bundle_invalidated": True,
                "convergence_shared_authority_revoked": True,
                "convergence_effective_k": len(remaining),
                "hidden_convergence_forward_count": 0,
            }
        )
        if not remaining:
            fallback_m3 = build_method_m3_receipt(
                label=MethodDecisionLabel.FALLBACK,
                candidate_set_id="convergence-zero-" + evidence.evidence_digest[:20],
                observed_monotonic_time=evidence.observed_monotonic_time,
            )
            self._method_m3_transactions.append(fallback_m3)
            envelope = MethodDecisionEnvelope.create(
                decision_label=MethodDecisionLabel.FALLBACK,
                decision_reason="NO_VALID_INTERPRETATION",
                ambiguity_state="NO_VALID_INTERPRETATION",
                effective_K=0,
                relationship="UNKNOWN_OR_INSUFFICIENT_EVIDENCE",
                decision_subject="BASELINE_SIMLINGO_PLAN",
                control_source=MethodControlSource.BASELINE_SIMLINGO,
                authorization_status=MethodAuthorizationStatus.FAIL_CLOSED,
                freshness="INVALID",
                source_planning_event=evidence.evidence_id,
                candidate_bundle_version=None,
                authority_subject="BASELINE_CONTROL",
            )
            self._set_method_decision(envelope)
            self._decision = _PersistentDecisionView("FALLBACK")
            self._receipt["status"] = "CONVERGENCE_TO_ZERO_FAIL_CLOSED_FALLBACK"
            self._receipt["method_v1_m3_transactions"] = list(
                self._method_m3_transactions
            )
            self._terminal = True
            return
        if len(remaining) != 1:
            self._receipt["status"] = "CONVERGENCE_EVIDENCE_RETAINS_AMBIGUITY"
            return
        self._convergence_evidence = evidence
        self._convergence_selected_candidate_id = remaining[0]
        assert self._candidate_set is not None
        by_id = {
            candidate.candidate_id: index
            for index, candidate in enumerate(self._candidate_set.candidates)
        }
        self._resolved_index = by_id[remaining[0]]
        self._convergence_replan_pending = True
        self._replan_pending = True
        self._set_internal_lifecycle_state("FRESH_REPLAN_REQUIRED")
        self._receipt.update(
            {
                "status": "CONVERGENCE_DETECTED_FRESH_REPLAN_REQUIRED",
                "convergence_selected_candidate_id": remaining[0],
                "convergence_replan_pending": True,
                "convergence_fake_wait_count": 0,
            }
        )
        self._refresh_method_dashboard()
        self._repaint_method_dashboard()

    def _ingest_tick_convergence_evidence(self, tick_data: Any) -> None:
        if not isinstance(tick_data, Mapping):
            return
        value = tick_data.get("driveclarify_candidate_convergence_evidence")
        if value is None:
            return
        self.observe_candidate_convergence_evidence(
            CandidateConvergenceEvidence.from_mapping(value)
        )

    def _method_v2_1_safe_holding_available(
        self, observation: RuntimeWindowObservation
    ) -> bool:
        """Preflight the existing holding owner without consulting motion rules."""

        position = self._latest_position
        try:
            position_valid = bool(
                position is not None
                and len(position) >= 2
                and all(math.isfinite(float(value)) for value in position[:2])
            )
            speed_valid = math.isfinite(float(self._latest_speed))
        except (TypeError, ValueError):
            position_valid = False
            speed_valid = False
        eligible = bool(
            getattr(self, "_method_v2_1_enabled", False)
            and observation.current_physical_safety_gate is True
            and getattr(self.wait, "enabled", False)
            and not self.wait.active
            and self.control_enabled
            and self._latest_frame is not None
            and self._latest_observation_id is not None
            and self._runtime_environment_digest is not None
            and position_valid
            and speed_valid
        )
        self._receipt["method_v2_1_holding_eligibility"] = {
            "eligible": eligible,
            "current_physical_safety_gate": (
                observation.current_physical_safety_gate
            ),
            "motion_hard_rule_gate_not_used": True,
            "existing_wait_owner_enabled": getattr(self.wait, "enabled", False),
            "query_already_holding": self.wait.active,
            "baseline_control_path_healthy": self.control_enabled,
            "source_identity_recordable": bool(
                self._latest_frame is not None
                and self._latest_observation_id is not None
                and self._runtime_environment_digest is not None
            ),
            "entry_observation_recordable": position_valid and speed_valid,
        }
        return eligible

    def _method_v2_1_decision_kwargs(
        self, window: Any, observation: RuntimeWindowObservation
    ) -> dict[str, Any]:
        if not getattr(self, "_method_v2_1_enabled", False):
            return {}
        evidence = getattr(window, "v3_bundle", None)
        clarification = getattr(evidence, "clarification", None)
        commitment = getattr(
            clarification, "commitment_time_lower_bound_monotonic", None
        )
        now = float(observation.observed_monotonic_time)
        t_available = (
            None if commitment is None else float(commitment) - now
        )
        kwargs = {
            "rule_gate_decoupled_clarification": True,
            "safe_holding_available": (
                self._method_v2_1_safe_holding_available(observation)
            ),
            "clarification_t_available_s": t_available,
            "clarification_answer_budget_s": getattr(
                clarification, "answer_latency_upper_bound_s", None
            ),
            "clarification_fresh_replan_budget_s": getattr(
                clarification, "post_answer_latency_upper_bound_s", None
            ),
            "clarification_activation_margin_s": float(
                self._phase_b_contract["m2b_m3_authority_upper_bound_s"]
            ),
            "clarification_safety_margin_s": float(
                observation.fixed_delta_seconds
            )
            * float(self._phase_b_contract["safety_margin_frames"]),
        }
        self._receipt["method_v2_1_clarification_timing_inputs"] = {
            "T_available_s": kwargs["clarification_t_available_s"],
            "T_answer_budget_s": kwargs["clarification_answer_budget_s"],
            "T_fresh_replan_budget_s": kwargs[
                "clarification_fresh_replan_budget_s"
            ],
            "T_activation_or_execution_margin_s": kwargs[
                "clarification_activation_margin_s"
            ],
            "T_safety_margin_s": kwargs["clarification_safety_margin_s"],
            "motion_hard_rule_gate": observation.hard_rule_gate,
            "motion_hard_rule_gate_used_for_ask": False,
        }
        return kwargs

    @staticmethod
    def _method_v2_1_navigation_identity(
        obligation: CandidateLocalNavigationObligation,
    ) -> str:
        return canonical_identity_digest(
            {
                "candidate_id": obligation.candidate_id,
                "interpretation_id": obligation.interpretation_id,
                "obligation_digest": obligation.obligation_digest,
                "branch_identity": obligation.local_branch_identity,
                "branch_digest": obligation.branch_digest,
                "global_destination_identity": (
                    obligation.global_destination_identity
                ),
                "mission_context_digest": obligation.mission_context_digest,
            }
        )

    def _method_v2_1_plan_snapshot(
        self,
        candidate: Any,
        obligation: CandidateLocalNavigationObligation,
        *,
        route_version: str,
        environment_digest: str,
    ) -> PlanIdentitySnapshot:
        scene_evidence = self._method_v2_1_live_selected_scene_evidence(
            obligation,
            route_version=route_version,
        )
        return PlanIdentitySnapshot(
            candidate_id=str(candidate.candidate_id),
            interpretation_id=str(candidate.interpretation_id),
            obligation_identity=obligation.obligation_identity,
            obligation_digest=obligation.obligation_digest,
            branch_identity=obligation.local_branch_identity,
            branch_digest=obligation.branch_digest,
            execution_location_identity=obligation.target_digest,
            navigation_context_identity=(
                self._method_v2_1_navigation_identity(obligation)
            ),
            global_destination_identity=obligation.global_destination_identity,
            mission_context_digest=obligation.mission_context_digest,
            route_version=str(route_version),
            environment_digest=str(environment_digest),
            scene_compatibility_digest=str(scene_evidence["digest"]),
            instruction=self.raw_instruction,
        )

    def _method_v2_1_live_selected_scene_evidence(
        self,
        obligation: CandidateLocalNavigationObligation,
        *,
        route_version: str,
    ) -> dict[str, Any]:
        """Reconstruct cheap live topology/branch evidence without a VLA forward."""

        connector, opportunity = self._method_v2_selected_connector(
            obligation,
            calibrated_uncertainty_m=float(
                self._phase_b_contract["calibrated_uncertainty_upper_m"]
            ),
        )
        map_name = str(getattr(_live_map(), "name", "UNKNOWN_LIVE_MAP"))
        live = {
            "map_name": map_name,
            "route_version": str(route_version),
            "junction_identity": str(opportunity.get("junction_id")),
            "execution_location_anchor_xy": list(opportunity.get("anchor_xy") or ()),
            "maneuver_direction": str(opportunity.get("maneuver_direction")),
            "exit_road_id": connector.get("exit_road_id"),
            "exit_lane_id": connector.get("exit_lane_id"),
            "connector_polyline_xy_m": list(connector.get("polyline_xy_m") or ()),
            "selected_target_match_distance_m": connector.get(
                "selected_target_match_distance_m"
            ),
            "selected_obligation_digest": obligation.obligation_digest,
            "selected_branch_identity": obligation.local_branch_identity,
            "selected_branch_digest": obligation.branch_digest,
            "selected_execution_location_identity": obligation.target_digest,
            "selected_target_id": str(opportunity.get("target_id")),
            "selected_branch_id": str(opportunity.get("branch_id")),
            "selected_route_order_index": opportunity.get("route_order_index"),
            "selected_route_opportunity_index": opportunity.get(
                "route_opportunity_index"
            ),
            "selected_opportunity_route_reachable": opportunity.get(
                "route_reachable"
            ),
        }
        return {
            "digest": canonical_identity_digest(live),
            "live": live,
            "connector": connector,
            "opportunity": opportunity,
        }

    def _method_v2_1_current_snapshot(
        self, candidate: Any
    ) -> PlanIdentitySnapshot:
        obligation = self._candidate_local_navigation_obligations.get(
            str(candidate.candidate_id)
        )
        if obligation is None:
            raise RuntimeError("METHOD_V2_1_SELECTED_OBLIGATION_MISSING")
        mission = self._candidate_local_navigation_mission
        if mission is None:
            raise RuntimeError("METHOD_V2_1_MISSION_CONTEXT_MISSING")
        mission.assert_endpoint_unchanged()
        if not (
            mission.global_destination_identity
            == obligation.global_destination_identity
            and mission.mission_context_digest == obligation.mission_context_digest
        ):
            raise RuntimeError("METHOD_V2_1_GLOBAL_DESTINATION_CONTEXT_CHANGED")
        route_version, environment_digest = self._method_v2_live_route_context()
        snapshot = self._method_v2_1_plan_snapshot(
            candidate,
            obligation,
            route_version=route_version,
            environment_digest=environment_digest,
        )
        return replace(
            snapshot,
            instruction=str(
                getattr(self.agent, "custom_prompt", self.raw_instruction)
            ),
        )

    @staticmethod
    def _method_v2_3_selected_approach_topology(
        live_origin: Any, connector: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Trace current directed topology to the exact selected branch entry.

        This reuses the existing CARLA waypoint-successor connector tracer.  It
        is deliberately not a global route planner and does not mutate either
        the baseline navigation route or the answer-conditioned local plan.
        """

        try:
            import carla

            entry_xy = connector["polyline_xy_m"][0]
            map_object = _live_map()
            live_waypoint = map_object.get_waypoint(
                carla.Location(
                    x=float(live_origin.x),
                    y=float(live_origin.y),
                    z=float(getattr(live_origin, "z", 0.0)),
                ),
                project_to_road=True,
                lane_type=carla.LaneType.Driving,
            )
            selected_entry_waypoint = map_object.get_waypoint(
                carla.Location(
                    x=float(entry_xy[0]), y=float(entry_xy[1]), z=0.0
                ),
                project_to_road=True,
                lane_type=carla.LaneType.Driving,
            )
            trace = dict(
                _trace_waypoint_connector(
                    live_waypoint, selected_entry_waypoint
                )
            )
            exact_selected_entry = bool(
                int(trace.get("exit_road_id"))
                == int(connector["entry_road_id"])
                and int(trace.get("exit_lane_id"))
                == int(connector["entry_lane_id"])
            )
            verified = bool(
                trace.get("status") == "AVAILABLE"
                and trace.get("exit_reached") is True
                and exact_selected_entry
            )
            trace.update(
                {
                    "verified": verified,
                    "exact_selected_entry": exact_selected_entry,
                    "owner": "SELECTED_APPROACH_DIRECTED_CARLA_WAYPOINT_CHAIN",
                    "selected_entry_road_id": int(connector["entry_road_id"]),
                    "selected_entry_lane_id": int(connector["entry_lane_id"]),
                    "new_planner_count": 0,
                    "read_only": True,
                }
            )
            trace["identity"] = canonical_sha256(
                {
                    "owner": trace["owner"],
                    "polyline_xy_m": trace.get("polyline_xy_m"),
                    "selected_entry_road_id": trace[
                        "selected_entry_road_id"
                    ],
                    "selected_entry_lane_id": trace[
                        "selected_entry_lane_id"
                    ],
                }
            )
            return trace
        except (
            AttributeError,
            ImportError,
            IndexError,
            KeyError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            return {
                "status": "UNKNOWN",
                "reason_code": "SELECTED_APPROACH_DIRECTED_TOPOLOGY_UNKNOWN",
                "error_type": type(error).__name__,
                "verified": False,
                "exact_selected_entry": False,
                "owner": "SELECTED_APPROACH_DIRECTED_CARLA_WAYPOINT_CHAIN",
                "identity": None,
                "new_planner_count": 0,
                "read_only": True,
            }

    def _method_v2_1_plan_ready_physical_baseline(
        self,
        obligation: CandidateLocalNavigationObligation,
        observation: RuntimeWindowObservation,
    ) -> dict[str, Any]:
        target = obligation.local_target
        if target is None:
            raise RuntimeError("METHOD_V2_1_SELECTED_LOCAL_TARGET_MISSING")
        live_origin = self._current_live_ego_planner_endpoint()
        route_version, _environment = self._method_v2_live_route_context()
        scene = self._method_v2_1_live_selected_scene_evidence(
            obligation,
            route_version=route_version,
        )
        polyline = tuple(
            (float(row[0]), float(row[1]))
            for row in scene["connector"]["polyline_xy_m"]
        )
        branch_distance, branch_progress = project_point_to_polyline(
            (float(live_origin.x), float(live_origin.y)),
            polyline,
        )
        selected_approach = None
        if getattr(self, "_method_v2_3_enabled", False):
            selected_approach = self._method_v2_3_selected_approach_topology(
                live_origin, scene["connector"]
            )
        route_progress = float(observation.current_progress_m)
        selected_commitment_progress = float(
            observation.candidate_commitment_progress_m[obligation.candidate_id]
        )
        route_projection_segment_index = getattr(
            observation, "route_projection_segment_index", None
        )
        route_projection_error_m = getattr(
            observation, "route_projection_error_m", None
        )
        baseline_route_projection_valid = bool(
            observation.alignment_verified is True
            and isinstance(route_projection_segment_index, int)
            and not isinstance(route_projection_segment_index, bool)
            and route_projection_segment_index >= 0
            and isinstance(route_projection_error_m, (int, float))
            and not isinstance(route_projection_error_m, bool)
            and math.isfinite(float(route_projection_error_m))
            and float(route_projection_error_m)
            + float(observation.calibrated_uncertainty_m)
            < float(observation.lane_clearance_m)
        )
        v2_7_current_route_alignment_verified = bool(
            getattr(self, "_method_v2_7_enabled", False)
            and str(observation.route_version) == str(self._runtime_route_version)
            and str(observation.environment_digest)
            == str(self._runtime_environment_digest)
        )
        v2_7_shared_baseline_approach_valid = bool(
            v2_7_current_route_alignment_verified
            and isinstance(route_projection_segment_index, int)
            and not isinstance(route_projection_segment_index, bool)
            and route_projection_segment_index >= 0
            and isinstance(route_projection_error_m, (int, float))
            and not isinstance(route_projection_error_m, bool)
            and math.isfinite(float(route_projection_error_m))
            and float(route_projection_error_m)
            + float(observation.calibrated_uncertainty_m)
            < float(observation.lane_clearance_m)
            and route_progress + float(observation.calibrated_uncertainty_m)
            < selected_commitment_progress
        )
        route_projection_valid = bool(
            observation.alignment_verified is True
            and (
                selected_approach is not None
                and selected_approach.get("verified") is True
                if getattr(self, "_method_v2_3_enabled", False)
                else baseline_route_projection_valid
            )
        )
        if v2_7_shared_baseline_approach_valid:
            route_projection_valid = True
        planning_capability_valid = bool(
            getattr(obligation, "qualification_status", None)
            is QualificationStatus.QUALIFIED
            and obligation.local_target is not None
            and bool(obligation.local_route_segment)
            and math.isfinite(float(obligation.local_horizon_m))
            and float(obligation.local_horizon_m) > 0.0
        )
        values = (
            float(live_origin.x),
            float(live_origin.y),
            route_progress,
            selected_commitment_progress,
            branch_distance,
            branch_progress,
            float(target.x_m),
            float(target.y_m),
        )
        if not all(math.isfinite(value) for value in values):
            raise RuntimeError("METHOD_V2_1_PHYSICAL_BASELINE_NONFINITE")
        return {
            "last_world_xy_m": [values[0], values[1]],
            "last_route_progress_m": route_progress,
            "selected_commitment_progress_m": selected_commitment_progress,
            "last_selected_target_distance_m": math.hypot(
                values[0] - values[6], values[1] - values[7]
            ),
            "last_branch_corridor_distance_m": branch_distance,
            "last_branch_progress_m": branch_progress,
            "last_route_projection_segment_index": (
                route_projection_segment_index
            ),
            "last_route_projection_error_m": route_projection_error_m,
            "live_route_projection_valid": route_projection_valid,
            "live_route_projection_owner": (
                "V2_7_SHARED_BASELINE_ROUTE_BEFORE_SEMANTIC_COMMITMENT"
                if v2_7_shared_baseline_approach_valid
                else (
                    "SELECTED_APPROACH_DIRECTED_CARLA_WAYPOINT_CHAIN"
                    if getattr(self, "_method_v2_3_enabled", False)
                    else "BASELINE_AGENT_OWNED_DENSE_ROUTE"
                )
            ),
            "v2_7_shared_baseline_approach_verified": (
                v2_7_shared_baseline_approach_valid
            ),
            "v2_7_current_route_alignment_verified": (
                v2_7_current_route_alignment_verified
            ),
            "baseline_route_projection_valid_diagnostic": (
                baseline_route_projection_valid
            ),
            "baseline_route_projection_error_m": route_projection_error_m,
            "baseline_route_projection_lane_clearance_m": float(
                observation.lane_clearance_m
            ),
            "baseline_route_projection_uncertainty_m": float(
                observation.calibrated_uncertainty_m
            ),
            "selected_approach_topology_verified": (
                None
                if selected_approach is None
                else selected_approach.get("verified")
            ),
            "selected_approach_topology_identity": (
                None
                if selected_approach is None
                else selected_approach.get("identity")
            ),
            "selected_approach_topology_trace": selected_approach,
            "selected_target_world_xy_m": [values[6], values[7]],
            "selected_branch_polyline_xy_m": [list(row) for row in polyline],
            "selected_junction_identity": scene["live"][
                "junction_identity"
            ],
            "selected_target_id": scene["live"]["selected_target_id"],
            "selected_branch_id": scene["live"]["selected_branch_id"],
            "selected_route_order_index": scene["live"][
                "selected_route_order_index"
            ],
            "selected_route_opportunity_index": scene["live"][
                "selected_route_opportunity_index"
            ],
            "selected_connector_topology_verified": bool(
                scene["opportunity"].get("availability") is True
                and scene["opportunity"].get("route_reachable") is True
                and scene["connector"].get("status") == "AVAILABLE"
                and scene["connector"].get("exit_reached") is True
                and scene["connector"].get("exact_selected_lane_pair_match")
                is True
                and scene["connector"].get(
                    "live_exit_anchor_within_existing_uncertainty"
                )
                is True
            ),
            "selected_planning_capability_valid": planning_capability_valid,
        }

    def _method_v2_1_plan_ready_physical_progress(
        self,
        staged: dict[str, Any],
        observation: RuntimeWindowObservation,
    ) -> dict[str, Any]:
        obligation = self._candidate_local_navigation_obligations.get(
            str(staged["candidate"].candidate_id)
        )
        if obligation is None:
            raise RuntimeError("METHOD_V2_1_SELECTED_OBLIGATION_MISSING")
        current = self._method_v2_1_plan_ready_physical_baseline(
            obligation, observation
        )
        previous_xy = tuple(float(value) for value in staged["last_world_xy_m"])
        current_xy = tuple(float(value) for value in current["last_world_xy_m"])
        displacement = math.hypot(
            current_xy[0] - previous_xy[0], current_xy[1] - previous_xy[1]
        )
        previous_route = float(staged["last_route_progress_m"])
        current_route = float(current["last_route_progress_m"])
        selected_commitment = float(current["selected_commitment_progress_m"])
        previous_target_distance = float(staged["last_selected_target_distance_m"])
        current_target_distance = float(current["last_selected_target_distance_m"])
        previous_branch_progress = float(staged["last_branch_progress_m"])
        current_branch_progress = float(current["last_branch_progress_m"])
        current_branch_distance = float(current["last_branch_corridor_distance_m"])
        current_speed_mps = float(observation.current_speed_mps)
        if not math.isfinite(current_speed_mps):
            raise RuntimeError("METHOD_V2_1_CURRENT_SPEED_UNKNOWN")
        corridor_tolerance = float(observation.lane_clearance_m) + float(
            observation.calibrated_uncertainty_m
        )
        route_progress_toward = bool(
            current_route > previous_route and previous_route < selected_commitment
        )
        target_distance_toward = bool(
            current_speed_mps > 0.0
            and current_target_distance < previous_target_distance
        )
        selected_branch_progress_toward = bool(
            current_branch_progress > previous_branch_progress
            and current_branch_distance <= corridor_tolerance
        )
        toward_commitment = bool(
            route_progress_toward
            or target_distance_toward
            or selected_branch_progress_toward
        )
        # Use the existing exact-zero runtime speed fact together with route
        # and semantic-progress evidence. This is not a new low-speed threshold;
        # CARLA pose quantization remains recorded but cannot alone relabel a
        # verified zero-speed HOLD as motion.
        physically_stationary = bool(
            current_speed_mps == 0.0
            and current_route == previous_route
            and not toward_commitment
        )
        v2_3_fields: dict[str, Any] = {}
        if getattr(self, "_method_v2_3_enabled", False):
            previous_segment_index = staged.get(
                "last_route_projection_segment_index"
            )
            current_segment_index = current.get(
                "last_route_projection_segment_index"
            )
            previous_max_route_progress = float(
                staged.get("max_observed_route_progress_m", previous_route)
            )
            route_projection_uncertainty = float(
                observation.calibrated_uncertainty_m
            )
            directed_route_progress_continuous = bool(
                current["live_route_projection_valid"] is True
                and current_route + route_projection_uncertainty
                >= previous_max_route_progress
            )
            max_observed_route_progress = max(
                previous_max_route_progress, current_route
            )
            selected_opportunity_identity_valid = bool(
                all(
                    current.get(name) == staged.get("frozen_" + name)
                    for name in (
                        "selected_junction_identity",
                        "selected_target_id",
                        "selected_branch_id",
                        "selected_route_order_index",
                        "selected_route_opportunity_index",
                    )
                )
            )
            frozen_commitment_boundary = staged.get(
                "frozen_selected_commitment_boundary_progress_m"
            )
            previous_effective_commitment_boundary = staged.get(
                "minimum_observed_commitment_boundary_progress_m",
                frozen_commitment_boundary,
            )
            if not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in (
                    frozen_commitment_boundary,
                    previous_effective_commitment_boundary,
                    selected_commitment,
                )
            ):
                raise RuntimeError(
                    "METHOD_V2_3_LIVE_COMMITMENT_BOUNDARY_UNKNOWN"
                )
            effective_commitment_boundary = min(
                float(frozen_commitment_boundary),
                float(previous_effective_commitment_boundary),
                float(selected_commitment),
            )
            selected_branch_topology_reachable = bool(
                current.get("selected_connector_topology_verified") is True
                and (
                    current.get("selected_approach_topology_verified") is True
                    or current.get(
                        "v2_7_shared_baseline_approach_verified"
                    ) is True
                )
                and current.get("live_route_projection_valid") is True
                and max_observed_route_progress + route_projection_uncertainty
                < effective_commitment_boundary
            )
            staged.update(current)
            staged["max_observed_route_progress_m"] = max_observed_route_progress
            staged["minimum_observed_commitment_boundary_progress_m"] = (
                effective_commitment_boundary
            )
            v2_3_fields = {
                "live_route_projection_valid": current[
                    "live_route_projection_valid"
                ],
                "live_route_projection_owner": current[
                    "live_route_projection_owner"
                ],
                "baseline_route_projection_valid_diagnostic": current[
                    "baseline_route_projection_valid_diagnostic"
                ],
                "selected_approach_topology_verified": current[
                    "selected_approach_topology_verified"
                ],
                "selected_approach_topology_identity": current[
                    "selected_approach_topology_identity"
                ],
                "selected_approach_topology_trace": current.get(
                    "selected_approach_topology_trace"
                ),
                "baseline_route_projection_error_m": current.get(
                    "baseline_route_projection_error_m"
                ),
                "baseline_route_projection_lane_clearance_m": current.get(
                    "baseline_route_projection_lane_clearance_m"
                ),
                "baseline_route_projection_uncertainty_m": current.get(
                    "baseline_route_projection_uncertainty_m"
                ),
                "v2_7_shared_baseline_approach_verified": current.get(
                    "v2_7_shared_baseline_approach_verified", False
                ),
                "v2_7_current_route_alignment_verified": current.get(
                    "v2_7_current_route_alignment_verified", False
                ),
                "directed_route_progress_continuous": (
                    directed_route_progress_continuous
                ),
                "selected_opportunity_identity_valid": (
                    selected_opportunity_identity_valid
                ),
                "selected_branch_topology_reachable": (
                    selected_branch_topology_reachable
                ),
                "selected_planning_capability_valid": current[
                    "selected_planning_capability_valid"
                ],
                "current_route_projection_segment_index": current_segment_index,
                "previous_route_projection_segment_index": previous_segment_index,
                "max_observed_route_progress_m": max_observed_route_progress,
                "previous_max_observed_route_progress_m": (
                    previous_max_route_progress
                ),
                "route_projection_continuity_uncertainty_m": (
                    route_projection_uncertainty
                ),
                "live_selected_commitment_boundary_progress_m": (
                    selected_commitment
                ),
                "effective_selected_commitment_boundary_progress_m": (
                    effective_commitment_boundary
                ),
            }
        else:
            staged.update(current)
        return {
            "evidence_complete": True,
            "physical_progress_toward_commitment": toward_commitment,
            "physically_stationary": physically_stationary,
            "ego_displacement_m": displacement,
            "current_speed_mps": current_speed_mps,
            "stationary_speed_predicate": "EXISTING_RUNTIME_SPEED_EXACT_ZERO",
            "route_progress_toward_commitment": route_progress_toward,
            "selected_target_distance_decreased": target_distance_toward,
            "selected_branch_corridor_progress": selected_branch_progress_toward,
            "current_branch_corridor_distance_m": current_branch_distance,
            "branch_corridor_tolerance_m": corridor_tolerance,
            **v2_3_fields,
        }

    def _method_v2_3_pre_activation_feasibility(
        self,
        staged: dict[str, Any],
        observation: RuntimeWindowObservation,
        *,
        selected_state_valid: Optional[bool],
        progress_evidence: Optional[Mapping[str, Any]],
    ) -> Any:
        """Evaluate remaining feasibility without creating a second geometry stack."""

        obligation = self._candidate_local_navigation_obligations.get(
            str(staged["candidate"].candidate_id)
        )
        if obligation is None:
            assessment = assess_pre_activation_feasibility(
                PreActivationFeasibilityEvidence(
                    live_plan_identity_valid=selected_state_valid,
                    physical_progress_evidence_complete=None,
                    live_route_projection_valid=None,
                    directed_route_progress_continuous=None,
                    selected_opportunity_identity_valid=None,
                    selected_branch_topology_reachable=None,
                    selected_planning_capability_valid=None,
                    current_route_progress_m=None,
                    frozen_selected_commitment_boundary_progress_m=None,
                    effective_selected_commitment_boundary_progress_m=None,
                    route_projection_uncertainty_m=None,
                )
            )
        else:
            assessment = assess_pre_activation_feasibility(
                PreActivationFeasibilityEvidence(
                    live_plan_identity_valid=selected_state_valid,
                    physical_progress_evidence_complete=(
                        None
                        if progress_evidence is None
                        else progress_evidence.get("evidence_complete")
                    ),
                    live_route_projection_valid=(
                        None
                        if progress_evidence is None
                        else progress_evidence.get("live_route_projection_valid")
                    ),
                    directed_route_progress_continuous=(
                        None
                        if progress_evidence is None
                        else progress_evidence.get(
                            "directed_route_progress_continuous"
                        )
                    ),
                    selected_opportunity_identity_valid=(
                        None
                        if progress_evidence is None
                        else progress_evidence.get(
                            "selected_opportunity_identity_valid"
                        )
                    ),
                    selected_branch_topology_reachable=(
                        None
                        if progress_evidence is None
                        else progress_evidence.get(
                            "selected_branch_topology_reachable"
                        )
                    ),
                    selected_planning_capability_valid=(
                        None
                        if progress_evidence is None
                        else progress_evidence.get(
                            "selected_planning_capability_valid"
                        )
                    ),
                    current_route_progress_m=staged.get(
                        "max_observed_route_progress_m"
                    ),
                    frozen_selected_commitment_boundary_progress_m=staged.get(
                        "frozen_selected_commitment_boundary_progress_m"
                    ),
                    effective_selected_commitment_boundary_progress_m=staged.get(
                        "minimum_observed_commitment_boundary_progress_m"
                    ),
                    route_projection_uncertainty_m=(
                        None
                        if progress_evidence is None
                        else progress_evidence.get(
                            "route_projection_continuity_uncertainty_m"
                        )
                    ),
                )
            )
        row = {
            "frame_id": int(self._latest_frame),
            "observation_id": str(self._latest_observation_id),
            "simulation_time_s": self._latest_simulation_time,
            "baseline_progress_classification": (
                "BASELINE_PRE_ACTIVATION_PROGRESS"
                if progress_evidence is not None
                and progress_evidence.get(
                    "physical_progress_toward_commitment"
                )
                is True
                else "BASELINE_PRE_ACTIVATION_STATIONARY"
                if progress_evidence is not None
                and progress_evidence.get("physically_stationary") is True
                else "BASELINE_PRE_ACTIVATION_PROGRESS_UNKNOWN"
            ),
            "current_route_progress_m": staged.get(
                "max_observed_route_progress_m"
            ),
            "live_dynamic_commitment_boundary_progress_m": staged.get(
                "selected_commitment_progress_m"
            ),
            "frozen_selected_commitment_boundary_progress_m": staged.get(
                "frozen_selected_commitment_boundary_progress_m"
            ),
            "effective_nonexpanding_commitment_boundary_progress_m": staged.get(
                "minimum_observed_commitment_boundary_progress_m"
            ),
            "selected_execution_location_distance_m": staged.get(
                "last_selected_target_distance_m"
            ),
            "selected_execution_location_distance_role": (
                "DIAGNOSTIC_ONLY_NOT_A_FEASIBILITY_THRESHOLD"
            ),
            "selected_branch_corridor_distance_m": staged.get(
                "last_branch_corridor_distance_m"
            ),
            "selected_branch_progress_m": staged.get(
                "last_branch_progress_m"
            ),
            "assessment": assessment.to_dict(),
            "selected_execution_budget_armed": False,
            "selected_execution_budget_consumed_s": 0.0,
            "new_geometry_or_planner_stack": False,
        }
        history = getattr(
            self, "_method_v2_3_pre_activation_feasibility_history", None
        )
        if history is None:
            history = []
            self._method_v2_3_pre_activation_feasibility_history = history
        history.append(row)
        self._receipt[
            "method_v2_3_pre_activation_feasibility_history"
        ] = list(history)
        self._receipt["method_v2_3_pre_activation_feasibility"] = row
        return assessment

    def _method_v2_3_update_active_execution_budget(
        self, observation: LiveManeuverObservation
    ) -> Optional[dict[str, Any]]:
        """Charge the selected execution ledger only after ACTIVE authority."""

        if not getattr(self, "_method_v2_3_enabled", False):
            return None
        if self._method_v2_execution.state is not ManeuverExecutionState.ACTIVE:
            return None
        budget = self._method_v2_1_execution_budget
        if budget is None:
            raise RuntimeError("METHOD_V2_3_ACTIVE_EXECUTION_BUDGET_MISSING")
        current_xy = [float(observation.ego_x_m), float(observation.ego_y_m)]
        previous_xy = getattr(
            self, "_method_v2_3_active_last_world_xy_m", None
        )
        if previous_xy is None or len(previous_xy) != 2:
            raise RuntimeError("METHOD_V2_3_ACTIVE_POSITION_BASELINE_MISSING")
        displacement = math.hypot(
            current_xy[0] - float(previous_xy[0]),
            current_xy[1] - float(previous_xy[1]),
        )
        self._method_v2_3_active_last_world_xy_m = current_xy
        method_v2_4 = getattr(self, "_method_v2_4_enabled", False)
        safety = observation.safety_certificate_status.strip().upper()
        rule = observation.rule_certificate_status.strip().upper()
        passing = {"AVAILABLE_TRUE", "PASS", "TRUE", "ALLOWED"}
        blocking_safety = {"BLOCKED", "UNSAFE", "FALSE", "DENIED"}
        blocking_rule = {"BLOCKED", "VIOLATION", "FALSE", "DENIED"}
        if not method_v2_4:
            motion_execution_eligible: Optional[bool] = True
        elif safety in passing and rule in passing:
            motion_execution_eligible = True
        elif safety in blocking_safety or rule in blocking_rule:
            motion_execution_eligible = False
        else:
            motion_execution_eligible = None
        try:
            row = budget.update(
                observed_monotonic_s=float(observation.monotonic_s),
                observed_simulation_time_s=(
                    float(observation.simulation_time_s)
                    if method_v2_4
                    else None
                ),
                frame_id=(int(observation.frame_id) if method_v2_4 else None),
                motion_execution_eligible=motion_execution_eligible,
                physical_progress_toward_commitment=bool(displacement > 0.0),
            )
        except (TypeError, ValueError) as error:
            if method_v2_4:
                self._method_v2_execution.terminate_unknown(
                    "METHOD_V2_4_SIMULATION_TIMESTAMP_OR_ELIGIBILITY_INVALID",
                    int(observation.frame_id),
                )
                self._receipt["method_v2_4_execution_clock_error"] = {
                    "type": type(error).__name__,
                    "message": str(error),
                    "frame_id": int(observation.frame_id),
                    "simulation_time_s": observation.simulation_time_s,
                }
                return None
            raise
        row = {
            **row,
            "frame_id": int(observation.frame_id),
            "observation_id": str(observation.observation_id),
            "active_ego_displacement_m": displacement,
            "selected_execution_state": "ACTIVE",
            "wall_elapsed_since_active_s": (
                float(observation.monotonic_s)
                - float(budget.started_monotonic_s)
            ),
            "execution_opportunity_elapsed_s": budget.consumed_budget_s,
        }
        self._receipt["method_v2_3_active_execution_budget_update"] = row
        self._receipt["method_v2_1_execution_budget"] = budget.summary()
        return row

    def _method_v2_1_try_activate_plan_ready(self) -> None:
        staged = self._method_v2_1_staged_plan
        if (
            staged is None
            or self._method_v2_1_plan_gate.state
            is not PlanActivationState.SELECTED_PLAN_READY
        ):
            return
        method_v2_3 = getattr(self, "_method_v2_3_enabled", False)
        candidate = staged["candidate"]
        try:
            current_snapshot = self._method_v2_1_current_snapshot(candidate)
        except Exception as error:
            destination_changed = "GLOBAL_DESTINATION" in str(error)
            if method_v2_3:
                self._method_v2_1_plan_gate.invalidate_pre_activation(
                    "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE:"
                    "LIVE_ROUTE_SCENE_OR_TOPOLOGY_EVIDENCE_UNKNOWN:"
                    + type(error).__name__,
                    frame_id=int(self._latest_frame),
                )
            elif destination_changed:
                self._method_v2_1_plan_gate.invalidate(
                    "CURRENT_PLAN_CONTEXT_UNKNOWN:" + type(error).__name__,
                    frame_id=int(self._latest_frame),
                )
            else:
                self._method_v2_1_plan_gate.require_replan(
                    "CURRENT_PLAN_CONTEXT_UNKNOWN:" + type(error).__name__,
                    frame_id=int(self._latest_frame),
                )
            self._receipt["method_v2_1_plan_revalidation_error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            current_snapshot = None
        revalidation = (
            None
            if current_snapshot is None
            else self._method_v2_1_plan_gate.revalidate(
                current_snapshot, frame_id=int(self._latest_frame)
            )
        )
        if revalidation is None or not revalidation.eligible:
            state = self._method_v2_1_plan_gate.state
            if (
                method_v2_3
                and state is not PlanActivationState.INVALIDATED
            ):
                self._method_v2_1_plan_gate.invalidate_pre_activation(
                    "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE:"
                    "LIVE_PLAN_IDENTITY_OR_TOPOLOGY_REVALIDATION_FAILED",
                    frame_id=int(self._latest_frame),
                )
                state = self._method_v2_1_plan_gate.state
            self._receipt.update(
                {
                    "method_v2_1_plan_activation": (
                        self._method_v2_1_plan_gate.summary()
                    ),
                    "method_v2_1_stale_revalidation": (
                        None if revalidation is None else revalidation.to_dict()
                    ),
                }
            )
            self._method_v2_1_staged_plan = None
            self._fresh_forward = None
            if method_v2_3:
                self._material_context_invalidated = True
                self._terminal = True
                self._receipt["status"] = (
                    "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE"
                )
                self._receipt["selected_execution_budget_armed"] = False
            elif state is PlanActivationState.FRESH_REPLAN_REQUIRED:
                self._replan_pending = True
                self._receipt["status"] = "METHOD_V2_1_FRESH_REPLAN_REQUIRED"
            else:
                self._material_context_invalidated = True
                self._terminal = True
                self._receipt["status"] = "METHOD_V2_1_PLAN_INVALIDATED"
            return
        try:
            observation = self._runtime_window_observation(time.monotonic())
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
            if not method_v2_3:
                raise
            self._method_v2_1_plan_gate.invalidate_pre_activation(
                "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE:"
                "LIVE_PROJECTION_OR_COMMITMENT_EVIDENCE_UNKNOWN:"
                + type(error).__name__,
                frame_id=int(self._latest_frame),
            )
            self._method_v2_1_staged_plan = None
            self._fresh_forward = None
            self._material_context_invalidated = True
            self._terminal = True
            self._receipt.update(
                {
                    "status": "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE",
                    "selected_execution_budget_armed": False,
                    "method_v2_3_pre_activation_error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                }
            )
            return
        motion = assess_motion_eligibility(
            MotionEligibilityEvidence(
                physical_safety_pass=observation.current_physical_safety_gate,
                hard_rule_pass=observation.hard_rule_gate,
                motion_valid=bool(self.control_enabled and not self.wait.active),
                selected_navigation_valid=revalidation.eligible,
            )
        )
        try:
            progress = self._method_v2_1_plan_ready_physical_progress(
                staged, observation
            )
            progress_evidence_complete = progress["evidence_complete"] is True
            physical_progress_toward_commitment = progress[
                "physical_progress_toward_commitment"
            ]
            physically_stationary = progress["physically_stationary"]
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            progress = None
            progress_evidence_complete = False
            physical_progress_toward_commitment = None
            physically_stationary = None
        safe_holding = (
            self._method_v2_1_safe_holding_available(observation)
            if motion.eligible is False
            and physical_progress_toward_commitment is False
            else True
        )
        hold_validity = assess_plan_ready_holding(
            PlanReadyHoldingEvidence(
                selected_state_valid=revalidation.eligible,
                physical_safety_pass=observation.current_physical_safety_gate,
                hard_rule_evidence_available=(
                    observation.hard_rule_gate in (True, False)
                ),
                safe_holding_valid=safe_holding,
                physical_progress_evidence_complete=progress_evidence_complete,
                physical_progress_toward_commitment=(
                    physical_progress_toward_commitment
                ),
                motion_execution_eligible=(
                    motion.eligible if motion.evidence_complete else None
                ),
            )
        )
        pre_activation_feasibility = (
            self._method_v2_3_pre_activation_feasibility(
                staged,
                observation,
                selected_state_valid=revalidation.eligible,
                progress_evidence=progress,
            )
            if method_v2_3
            else None
        )
        candidate_selected_route_legal = None
        if getattr(self, "_method_v2_8_enabled", False):
            candidate_selected_route_legal = bool(
                revalidation.eligible
                and pre_activation_feasibility is not None
                and pre_activation_feasibility.evidence_complete
                and pre_activation_feasibility.feasible
            )
            motion = assess_selected_motion_eligibility_v28(
                SelectedMotionEligibilityEvidenceV28(
                    physical_safety_pass=(
                        observation.current_physical_safety_gate
                    ),
                    candidate_selected_route_legal=(
                        candidate_selected_route_legal
                    ),
                    selected_navigation_valid=revalidation.eligible,
                    positive_pre_activation_feasibility=(
                        None
                        if pre_activation_feasibility is None
                        else pre_activation_feasibility.feasible
                    ),
                    motion_valid=bool(
                        self.control_enabled and not self.wait.active
                    ),
                )
            )
        budget = self._method_v2_1_execution_budget
        if budget is None and not method_v2_3:
            hold_validity = EligibilityResult(
                False,
                False,
                ("EXECUTION_BUDGET_LEDGER_MISSING",),
            )
        budget_row = None
        if hold_validity.eligible and budget is not None and not method_v2_3:
            try:
                budget_row = budget.update(
                    observed_monotonic_s=float(
                        observation.observed_monotonic_time
                    ),
                    motion_execution_eligible=motion.eligible,
                    physical_progress_toward_commitment=(
                        physical_progress_toward_commitment
                    ),
                )
            except (TypeError, ValueError):
                hold_validity = EligibilityResult(
                    False,
                    False,
                    ("EXECUTION_BUDGET_EVIDENCE_UNKNOWN",),
                )
        self._receipt.update(
            {
                "method_v2_1_motion_eligibility": motion.to_dict(),
                "method_v2_1_stale_revalidation": revalidation.to_dict(),
                "method_v2_1_plan_ready_hold_validity": hold_validity.to_dict(),
                "method_v2_1_physical_progress_toward_commitment": (
                    physical_progress_toward_commitment
                ),
                "method_v2_1_physical_progress_evidence": progress,
                "method_v2_1_execution_budget_update": budget_row,
                "method_v2_1_execution_budget": (
                    None if budget is None else budget.summary()
                ),
                "method_v2_3_pre_activation_feasibility": (
                    None
                    if pre_activation_feasibility is None
                    else pre_activation_feasibility.to_dict()
                ),
                "selected_execution_budget_armed": budget is not None,
                "selected_execution_budget_consumed_pre_active_s": (
                    0.0
                    if method_v2_3
                    else None
                    if budget is None
                    else budget.consumed_budget_s
                ),
                "method_v2_1_motion_gate_at_plan_ready": (
                    observation.hard_rule_gate
                ),
                "method_v2_8_candidate_selected_route_legal": (
                    candidate_selected_route_legal
                ),
                "method_v2_8_baseline_route_local_hard_rule_observed": (
                    observation.hard_rule_gate
                )
                if getattr(self, "_method_v2_8_enabled", False)
                else None,
                "method_v2_8_baseline_route_local_hard_rule_is_activation_owner": (
                    False
                    if getattr(self, "_method_v2_8_enabled", False)
                    else None
                ),
            }
        )
        if (
            pre_activation_feasibility is not None
            and not pre_activation_feasibility.feasible
        ):
            self._method_v2_1_plan_gate.invalidate(
                "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE:"
                + ",".join(pre_activation_feasibility.reason_codes),
                frame_id=int(self._latest_frame),
            )
            self._method_v2_1_staged_plan = None
            self._fresh_forward = None
            self._material_context_invalidated = True
            self._terminal = True
            self._receipt["status"] = (
                "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE"
            )
            self._receipt["selected_execution_budget_armed"] = False
            return
        if pre_activation_feasibility is not None:
            self._method_v2_1_plan_gate.record_pre_activation_feasibility(
                pre_activation_feasibility,
                frame_id=int(self._latest_frame),
            )
        if not hold_validity.eligible:
            self._method_v2_1_plan_gate.invalidate(
                "PLAN_READY_STATE_VALIDITY_FAILED:"
                + ",".join(hold_validity.reason_codes),
                frame_id=int(self._latest_frame),
            )
            self._method_v2_1_staged_plan = None
            self._fresh_forward = None
            self._material_context_invalidated = True
            self._terminal = True
            self._receipt["status"] = "METHOD_V2_1_PLAN_INVALIDATED_FAIL_CLOSED"
            return
        if not method_v2_3 and budget is not None and budget.exhausted:
            self._method_v2_1_plan_gate.invalidate(
                "MANEUVER_EXECUTION_OPPORTUNITY_BUDGET_EXHAUSTED",
                frame_id=int(self._latest_frame),
            )
            self._method_v2_1_staged_plan = None
            self._fresh_forward = None
            self._material_context_invalidated = True
            self._terminal = True
            self._receipt["status"] = "METHOD_V2_1_EXECUTION_BUDGET_EXHAUSTED"
            return
        if not motion.eligible:
            self._decision = _PersistentDecisionView("WAIT")
            self._receipt.update(
                {
                    "status": (
                        "METHOD_V2_3_PRE_ACTIVATION_MONITORING_MOTION_BLOCKED"
                        if method_v2_3
                        else "METHOD_V2_1_SELECTED_PLAN_READY_MOTION_BLOCKED"
                    ),
                    "selected_plan_consumed_while_motion_blocked": False,
                    "selected_execution_budget_armed": budget is not None,
                    "selected_execution_budget_consumed_pre_active_s": (
                        0.0 if method_v2_3 else None
                    ),
                    "method_v2_1_plan_activation": (
                        self._method_v2_1_plan_gate.summary()
                    ),
                }
            )
            self._method_v2_1_record_timeline(
                "PLAN_READY_MOTION_BLOCKED", observation=observation
            )
            return

        fresh_set_id = str(staged["fresh_set_id"])
        result = staged["result"]
        now = time.monotonic()
        if method_v2_3:
            execution_allowance_s = staged.get(
                "selected_execution_budget_allowance_s"
            )
            if not (
                isinstance(execution_allowance_s, (int, float))
                and not isinstance(execution_allowance_s, bool)
                and math.isfinite(float(execution_allowance_s))
                and float(execution_allowance_s) > 0.0
            ):
                self._method_v2_1_plan_gate.invalidate(
                    "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE:"
                    "SELECTED_EXECUTION_BUDGET_ALLOWANCE_UNKNOWN",
                    frame_id=int(self._latest_frame),
                )
                self._method_v2_1_staged_plan = None
                self._fresh_forward = None
                self._material_context_invalidated = True
                self._terminal = True
                self._receipt["status"] = (
                    "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE"
                )
                return
            execution_allowance_s = float(execution_allowance_s)
            active_execution_deadline = now + execution_allowance_s
        else:
            assert budget is not None
            execution_allowance_s = budget.remaining_budget_s
            # Convert the frozen pre-hold execution opportunity allowance into
            # a fresh absolute owner only after revalidation and motion release.
            self._method_v2_frozen_commitment_deadline_monotonic = (
                now + execution_allowance_s
            )
        m3_ready, m3_act = _build_frozen_m3_act_result(fresh_set_id, now)
        if not method_v2_3:
            self._m3_act_transactions.append(
                {
                    "decision_source": "METHOD_V2_1_MOTION_GATED_PLAN_ACTIVATION",
                    "candidate_set_id": fresh_set_id,
                    "ready_result": m3_ready.to_dict(),
                    "accepted_act_result": m3_act.to_dict(),
                    "accepted_act_digest": canonical_sha256(m3_act.to_dict()),
                }
            )
        if method_v2_3:
            try:
                self._method_v2_1_plan_gate.activate(
                    frame_id=int(self._latest_frame),
                    require_pre_activation_feasibility=True,
                )
            except Exception as error:
                if (
                    self._method_v2_1_plan_gate.state
                    is PlanActivationState.EXECUTION_ACTIVE
                ):
                    self._method_v2_1_plan_gate.invalidate_active_transition(
                        "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE:"
                        "PLAN_ACTIVE_TRANSITION_FAILED",
                        frame_id=int(self._latest_frame),
                    )
                elif (
                    self._method_v2_1_plan_gate.state
                    is PlanActivationState.SELECTED_PLAN_READY
                ):
                    self._method_v2_1_plan_gate.invalidate_pre_activation(
                        "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE:"
                        "PLAN_ACTIVE_TRANSITION_FAILED",
                        frame_id=int(self._latest_frame),
                    )
                self._method_v2_1_execution_budget = None
                self._method_v2_3_active_execution_deadline_monotonic = None
                self._method_v2_1_staged_plan = None
                self._fresh_forward = None
                self._authority_mode = None
                self._decision = _PersistentDecisionView("WAIT")
                self._material_context_invalidated = True
                self._terminal = True
                self._receipt.update(
                    {
                        "status": "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE",
                        "selected_execution_budget_armed": False,
                        "method_v2_3_activation_error": {
                            "type": type(error).__name__,
                            "message": str(error),
                        },
                        "method_v2_1_plan_activation": (
                            self._method_v2_1_plan_gate.summary()
                        ),
                    }
                )
                return
            # Establish semantic ACTIVE first without installing the stale
            # PLAN_READY-frame trajectory.  The current activation frame keeps
            # baseline control; the next normal SimLingo forward is rebound to
            # the selected obligation from that frame's live ego pose.
            try:
                self._activate_method_revision_v2(
                    candidate,
                    observation,
                    now,
                    execution_deadline_monotonic=(
                        active_execution_deadline
                    ),
                )
            except Exception as error:
                if (
                    self._method_v2_1_plan_gate.state
                    is PlanActivationState.EXECUTION_ACTIVE
                ):
                    self._method_v2_1_plan_gate.invalidate_active_transition(
                        "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE:"
                        "BOUNDED_EXECUTION_ACTIVATION_RAISED",
                        frame_id=int(self._latest_frame),
                    )
                self._method_v2_execution.terminate_unknown(
                    "METHOD_V2_3_ACTIVATION_EXCEPTION",
                    int(self._latest_frame),
                )
                self._method_v2_1_execution_budget = None
                self._method_v2_3_active_execution_deadline_monotonic = None
                self._method_v2_1_staged_plan = None
                self._fresh_forward = None
                self._authority_mode = None
                self._decision = _PersistentDecisionView("WAIT")
                self._material_context_invalidated = True
                self._terminal = True
                self._receipt.update(
                    {
                        "status": "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE",
                        "selected_execution_budget_armed": False,
                        "method_v2_3_activation_error": {
                            "type": type(error).__name__,
                            "message": str(error),
                        },
                        "method_v2_1_plan_activation": (
                            self._method_v2_1_plan_gate.summary()
                        ),
                    }
                )
                return
            armed = (
                self._method_v2_execution.state
                is ManeuverExecutionState.ACTIVE
            )
            self._receipt.update(
                {
                    "method_v2_3_plan_ready_trajectory_installed": False,
                    "method_v2_3_activation_frame_control_owner": (
                        "BASELINE_SIMLINGO"
                    ),
                    "method_v2_3_selected_control_begins": (
                        "NEXT_CURRENT_POSE_SELECTED_NORMAL_FORWARD"
                    ),
                }
            )
        else:
            armed = self.shared_act.arm_unique(
                candidate_set_id=fresh_set_id,
                candidate_id=candidate.candidate_id,
                resolved_interpretation_id=candidate.interpretation_id,
                route=result.raw_route,
                speed=result.raw_speed,
                source_observation_id=str(self._latest_observation_id),
                source_frame_id=str(self._latest_frame),
                route_version=self._runtime_route_version,
                environment_digest=self._runtime_environment_digest,
                current_monotonic=now,
                valid_until_monotonic=now
                + float(self._phase_b_contract["full_bundle_wall_timeout_s"]),
                existing_m3_act_result=m3_act,
            )
        if not armed:
            if method_v2_3:
                if (
                    self._method_v2_1_plan_gate.state
                    is PlanActivationState.EXECUTION_ACTIVE
                ):
                    self._method_v2_1_plan_gate.invalidate_active_transition(
                        "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE:"
                        "BOUNDED_EXECUTION_ACTIVATION_EVIDENCE_UNKNOWN",
                        frame_id=int(self._latest_frame),
                    )
                self._method_v2_1_execution_budget = None
                self._method_v2_3_active_execution_deadline_monotonic = None
                self._method_v2_1_staged_plan = None
                self._fresh_forward = None
                self._material_context_invalidated = True
                self._terminal = True
                self._receipt.update(
                    {
                        "status": "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE",
                        "selected_execution_budget_armed": False,
                        "method_v2_1_plan_activation": (
                            self._method_v2_1_plan_gate.summary()
                        ),
                    }
                )
                return
            raise RuntimeError("METHOD_V2_1_UNIQUE_PLAN_ACTIVATION_ARM_FAILED")
        if not method_v2_3:
            self._activate_method_revision_v2(candidate, observation, now)
        if self._method_v2_execution.state is ManeuverExecutionState.ACTIVE:
            if method_v2_3:
                activation_xy = self._method_v2_timing.get(
                    "execution_activation_world_xy_m"
                )
                if not (
                    isinstance(activation_xy, list)
                    and len(activation_xy) == 2
                    and all(
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(float(value))
                        for value in activation_xy
                    )
                ):
                    self._method_v2_execution.invalidate(
                        "METHOD_V2_3_ACTIVE_POSITION_BASELINE_MISSING",
                        int(self._latest_frame),
                    )
                    self._method_v2_1_plan_gate.invalidate_active_transition(
                        "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE:"
                        "ACTIVE_POSITION_BASELINE_UNKNOWN",
                        frame_id=int(self._latest_frame),
                    )
                    self._method_v2_1_execution_budget = None
                    self._method_v2_3_active_execution_deadline_monotonic = None
                    self._method_v2_1_staged_plan = None
                    self._fresh_forward = None
                    self._authority_mode = None
                    self._decision = _PersistentDecisionView("WAIT")
                    self._material_context_invalidated = True
                    self._terminal = True
                    self._receipt.update(
                        {
                            "status": "PRE_ACTIVATION_SELECTED_MANEUVER_INFEASIBLE",
                            "selected_execution_budget_armed": False,
                            "method_v2_1_plan_activation": (
                                self._method_v2_1_plan_gate.summary()
                            ),
                        }
                    )
                    return
                self._method_v2_1_execution_budget = (
                    ManeuverExecutionBudgetLedger(
                        execution_allowance_s,
                        started_monotonic_s=float(now),
                        initial_motion_execution_eligible=True,
                        clock_domain=(
                            ManeuverExecutionBudgetLedger.SIMULATION_CLOCK_DOMAIN
                            if getattr(self, "_method_v2_4_enabled", False)
                            else ManeuverExecutionBudgetLedger.WALL_CLOCK_DOMAIN
                        ),
                        started_simulation_time_s=(
                            float(self._latest_simulation_time)
                            if getattr(self, "_method_v2_4_enabled", False)
                            else None
                        ),
                        started_frame_id=(
                            int(self._latest_frame)
                            if getattr(self, "_method_v2_4_enabled", False)
                            else None
                        ),
                    )
                )
                self._method_v2_3_active_execution_deadline_monotonic = (
                    active_execution_deadline
                )
                self._method_v2_3_active_last_world_xy_m = [
                    float(activation_xy[0]),
                    float(activation_xy[1]),
                ]
                self._method_v2_3_activation_frame_id = int(
                    self._latest_frame
                )
                self._m3_act_transactions.append(
                    {
                        "decision_source": "METHOD_V2_3_ACTIVE_TRANSITION",
                        "candidate_set_id": fresh_set_id,
                        "ready_result": m3_ready.to_dict(),
                        "accepted_act_result": m3_act.to_dict(),
                        "accepted_act_digest": canonical_sha256(m3_act.to_dict()),
                    }
                )
            else:
                self._method_v2_1_plan_gate.activate(
                    frame_id=int(self._latest_frame),
                    require_pre_activation_feasibility=False,
                )
            self._latest_unique_observation = observation
            self._authority_mode = "BOUNDED_UNIQUE" if method_v2_3 else "UNIQUE"
            self._decision = _PersistentDecisionView(MethodDecisionLabel.ACT.value)
            self._set_method_decision(
                MethodDecisionEnvelope.create(
                    decision_label=MethodDecisionLabel.ACT,
                    decision_reason="ACT_AFTER_PLAN_READY_MOTION_GATE_RELEASE",
                    ambiguity_state="RESOLVED_AFTER_ANSWER",
                    effective_K=1,
                    relationship="UNIQUE_AFTER_PASSENGER_ANSWER",
                    decision_subject="FRESH_UNIQUE_CANDIDATE_PLAN",
                    control_source=MethodControlSource.FRESH_UNIQUE_CANDIDATE,
                    authorization_status=MethodAuthorizationStatus.AUTHORIZED,
                    freshness="FRESH_REVALIDATED_BEFORE_ACTIVATION",
                    source_planning_event=fresh_set_id + ":normal-planning-event",
                    candidate_bundle_version=fresh_set_id,
                    authority_subject="UNIQUE_CANDIDATE",
                )
            )
            self._method_v2_1_timing_update_on_activation(now)
        elif method_v2_3:
            self._method_v2_1_execution_budget = None
        self._receipt.update(
            {
                "status": (
                    "METHOD_V2_1_EXECUTION_ACTIVE_AFTER_MOTION_GATE_RELEASE"
                    if self._method_v2_execution.state
                    is ManeuverExecutionState.ACTIVE
                    else "METHOD_V2_1_FROZEN_V2_ACTIVATION_FAILED"
                ),
                "selected_plan_consumed_while_motion_blocked": False,
                "selected_execution_budget_armed": bool(
                    method_v2_3
                    and self._method_v2_execution.state
                    is ManeuverExecutionState.ACTIVE
                    and self._method_v2_1_execution_budget is not None
                )
                if method_v2_3
                else budget is not None,
                "selected_execution_budget_armed_frame_id": (
                    int(self._latest_frame)
                    if method_v2_3
                    and self._method_v2_execution.state
                    is ManeuverExecutionState.ACTIVE
                    else None
                ),
                "method_v2_1_execution_budget": (
                    None
                    if self._method_v2_1_execution_budget is None
                    else self._method_v2_1_execution_budget.summary()
                ),
                "method_v2_1_plan_activation": (
                    self._method_v2_1_plan_gate.summary()
                ),
            }
        )
        self._method_v2_1_staged_plan = None
        self._method_v2_1_record_timeline(
            "SELECTED_EXECUTION_ACTIVATED", observation=observation
        )

    def _method_v2_1_timing_update_on_activation(self, now: float) -> None:
        ready = self._method_v2_1_plan_gate.ready_frame_id
        self._method_v2_timing.update(
            {
                "method_v2_1_plan_ready_frame_id": ready,
                "method_v2_1_motion_gate_release_frame_id": self._latest_frame,
                "method_v2_1_execution_activation_frame_id": self._latest_frame,
                "method_v2_1_execution_activation_monotonic_s": float(now),
            }
        )

    def _method_v2_1_record_timeline(
        self, event: str, *, observation: Optional[RuntimeWindowObservation] = None
    ) -> None:
        if not getattr(self, "_method_v2_1_enabled", False):
            return
        episode = None
        episode_id = getattr(self, "_episode_id", None)
        if episode_id is not None:
            try:
                episode = self.persistent_store.get(episode_id)
            except Exception:
                episode = None
        if observation is None and self._latest_frame is not None:
            try:
                observation = self._runtime_window_observation(time.monotonic())
            except Exception:
                observation = None
        decision = getattr(getattr(self, "_decision", None), "recommendation", None)
        decision_value = getattr(getattr(decision, "decision", None), "value", None)
        window = getattr(self, "_latest_window", None)
        execution = getattr(self, "_method_v2_execution", None)
        plan_gate = getattr(self, "_method_v2_1_plan_gate", None)
        timeline = getattr(self, "_method_v2_1_timeline", None)
        if timeline is None:
            timeline = []
            self._method_v2_1_timeline = timeline
        row = {
            "sequence": len(timeline) + 1,
            "frame": self._latest_frame,
            "simulation_time_s": getattr(
                self, "_latest_simulation_time", None
            ),
            "monotonic_s": time.monotonic(),
            "ambiguity_state": (
                None if episode is None else episode.semantic_state.value
            ),
            "effective_k": (
                None if episode is None else len(episode.active_candidate_ids)
            ),
            "decision_relevance": (
                None
                if window is None
                else getattr(window, "future_obligation_relation", None)
                in {"DIVERGENT", "FUTURE_DIVERGENT"}
            ),
            "clarification_eligibility": (
                self._receipt.get("initial_decision") == "ASK"
                or self._receipt.get("current_persistent_decision") == "WAIT"
            ),
            "motion_eligibility": (
                None
                if observation is None
                else bool(
                    observation.current_physical_safety_gate is True
                    and observation.hard_rule_gate is True
                    and getattr(self, "control_enabled", False)
                )
            ),
            "hard_rule_state": (
                None if observation is None else observation.hard_rule_gate
            ),
            "safety_state": (
                None
                if observation is None
                else observation.current_physical_safety_gate
            ),
            "holding_eligibility_or_active": bool(
                getattr(getattr(self, "wait", None), "active", False)
                or self._receipt.get("method_v2_1_holding_eligibility", {}).get(
                    "eligible"
                )
            ),
            "query_outstanding": bool(
                episode is not None and episode.active_query is not None
            ),
            "decision_or_event": event if event else decision_value,
            "policy_decision": decision_value,
            "answer_state": (
                "RECEIVED"
                if getattr(self, "_answer_received_sim_time", None) is not None
                else "PENDING"
                if episode is not None and episode.active_query is not None
                else "NONE"
            ),
            "fresh_plan_state": (
                None if plan_gate is None else plan_gate.state.value
            ),
            "selected_execution_state": (
                None if execution is None else execution.state.value
            ),
            "pre_activation_feasibility": self._receipt.get(
                "method_v2_3_pre_activation_feasibility"
            ),
            "selected_execution_budget_armed": self._receipt.get(
                "selected_execution_budget_armed"
            ),
            "selected_execution_budget_consumed_pre_active_s": (
                self._receipt.get(
                    "selected_execution_budget_consumed_pre_active_s"
                )
            ),
            "commitment_state": (
                None
                if execution is None
                else execution.state.value
                if execution.state
                in {
                    ManeuverExecutionState.MANEUVER_COMMITTED,
                    ManeuverExecutionState.MANEUVER_COMPLETED,
                }
                else "NOT_COMMITTED"
            ),
        }
        timeline.append(row)
        self._receipt["method_v2_1_timeline"] = list(
            self._method_v2_1_timeline
        )

    def _observe_rq2_t_unknown_evidence(self, reason_code: str) -> None:
        """Preserve an observational row when production evidence is unavailable."""
        if (
            self._rq2_t_temporal_observer is None
            or not _truthy(
                os.environ.get("DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED")
            )
        ):
            return
        unavailable_row = {
            "candidate_ids": [],
            "current_progress_m": None,
            "full_plan_coverage": False,
            "decision": None,
            "cumulative_compute_accounting": {
                "normal_forward_count": 1,
                "candidate_forward_count": 0,
                "detector_forward_count": 0,
            },
            "m2b_inputs": {
                "semantic_state": "UNKNOWN",
                "active_candidate_count": 0,
                "multiple_plausible_interpretations": False,
                "hard_safety_gate": None,
                "hard_rule_gate": None,
                "safe_holding_available": None,
                "answer_changes_decision": None,
                "evidence": {
                    "source": {
                        "source_frame_id": self._latest_frame,
                        "source_observation_id": str(
                            self._latest_observation_id
                        ),
                    },
                    "current_action": {
                        "availability": "UNKNOWN",
                        "reason_codes": [reason_code],
                    },
                    "future_obligation": {
                        "availability": "UNKNOWN",
                        "reason_codes": [reason_code],
                    },
                    "recoverability": {
                        "status": "UNKNOWN",
                        "reason_codes": [reason_code],
                    },
                    "shared_action_lease": {"valid": False},
                },
            },
        }
        try:
            self._rq2_t_temporal_observer.observe(
                unavailable_row,
                simulation_time_s=float(self._latest_simulation_time),
            )
            self._receipt["rq2_t_evidence_unavailable_reason"] = reason_code
            self._receipt.update(
                {
                    "rq2_t_2a_observational_only": True,
                    "rq2_t_2a_policy_enactment_count": 0,
                    "rq2_t_2a_query_enactment_count": 0,
                    "control_owner": "SHARED_PREFIX_LONGITUDINAL_OWNER_V1",
                }
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._rq2_t_temporal_observer_errors.append(
                {
                    "type": type(error).__name__,
                    "message": str(error),
                    "stage": "RQ2_T_UNKNOWN_EVIDENCE_OBSERVATION",
                }
            )

    def on_model_output(
        self, baseline_route: Any, baseline_speed: Any, model_start: float, model_end: float
    ) -> None:
        self._normal_forwards += 1
        if getattr(self, "_method_v3_enabled", False):
            native_target_rows = getattr(
                self._method_v2_execution,
                "native_route_target_owner_evidence",
                (),
            )
            target_rows = native_target_rows or getattr(
                self._method_v2_execution,
                "connector_target_window_evidence",
                (),
            )
            if (
                target_rows
                and target_rows[-1].get("frame_id") == self._latest_frame
            ):
                target_rows[-1].update(
                    {
                        "fresh_normal_forward_count": 1,
                        "fresh_route_output_digest": _tensor_digest(
                            baseline_route
                        ),
                        "fresh_speed_output_digest": _tensor_digest(
                            baseline_speed
                        ),
                        "model_start_monotonic_s": float(model_start),
                        "model_end_monotonic_s": float(model_end),
                    }
                )
                latest = self._receipt.get(
                    "method_v3_connector_target_window"
                )
                if isinstance(latest, dict):
                    latest["latest"] = dict(target_rows[-1])
        timing_observation_id = str(self._latest_observation_id)
        timing_row = getattr(
            self, "_high_fidelity_step_timing_by_observation", {}
        ).get(timing_observation_id)
        if timing_row is not None:
            timing_row.update(
                {
                    "normal_forward_start_monotonic_s": float(model_start),
                    "normal_forward_end_monotonic_s": float(model_end),
                    "normal_forward_latency_s": float(model_end) - float(model_start),
                    "decision_stage_start_monotonic_s": float(model_end),
                    "normal_forward_count": 1,
                    # Set true only at the scheduler-authoritative branch after
                    # all higher-priority answer/replan/execution branches.
                    "k_way_required_at_model_stage_start": False,
                }
            )
        self._high_fidelity_active_model_observation_id = (
            timing_observation_id if timing_row is not None else None
        )
        try:
            self._method_v2_1_record_timeline("NORMAL_MODEL_OUTPUT")
            if (
                getattr(self, "_method_v2_1_enabled", False)
                and self._method_v2_1_plan_gate.state
                is PlanActivationState.SELECTED_PLAN_READY
            ):
                self._method_v2_1_try_activate_plan_ready()
                self._persist()
                return
            if (
                getattr(self, "_method_revision_v2_enabled", False)
                and self._method_v2_execution.selected_navigation_required
            ):
                binding = self._method_v2_pending_binding
                identity = self._method_v2_execution.identity
                if binding is None or identity is None:
                    self._method_v2_execution.terminate_unknown(
                        "METHOD_V2_ACTIVE_NORMAL_FORWARD_BINDING_MISSING",
                        self._latest_frame,
                    )
                    self.shared_act.revoke(
                        AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED
                    )
                    self._terminal = True
                else:
                    self._method_v2_execution.record_navigation_tick(
                        observation_id=str(self._latest_observation_id),
                        frame_id=int(self._latest_frame),
                        navigation_context_identity=(
                            identity.navigation_context_identity
                        ),
                        binding_obligation_digest=binding.obligation_digest,
                        binding_branch_digest=binding.branch_digest,
                        binding_global_destination_identity=(
                            binding.global_destination_identity
                        ),
                        projection_digest=binding.projection_digest,
                        normal_forward_count=1,
                        continued_candidate_comparison_forward_count=0,
                    )
                    if getattr(self, "_method_v3_enabled", False):
                        route_binding = self._method_v3_selected_route_binding
                        owner = getattr(
                            self._global_route_reconnection_bridge,
                            "_route_owner",
                            None,
                        )
                        if route_binding is None or owner is None:
                            raise RuntimeError(
                                "METHOD_V3_SELECTED_ROUTE_BINDING_MISSING_AT_REFRESH"
                            )
                        active_route_identity = str(
                            getattr(owner, "active_route_identity", "")
                        )
                        consumed_route_identity = str(
                            getattr(
                                owner, "next_tick_consumed_route_identity", ""
                            )
                        )
                        self._method_v2_execution.record_route_conditioned_refresh(
                            observation_id=str(self._latest_observation_id),
                            frame_id=int(self._latest_frame),
                            active_route_identity=active_route_identity,
                            consumed_route_identity=consumed_route_identity,
                            projection_digest=binding.projection_digest,
                            refreshed_route_digest=_tensor_digest(baseline_route),
                            refreshed_speed_digest=_tensor_digest(baseline_speed),
                        )
                        route_binding = replace(
                            route_binding,
                            next_tick_consumed_route_identity=(
                                consumed_route_identity
                            ),
                        )
                        self._method_v3_selected_route_binding = route_binding
                        self._method_v2_execution.selected_route_binding = (
                            route_binding
                        )
                        self._receipt["method_v3_selected_route_binding"] = (
                            route_binding.to_dict()
                        )
                        planner = getattr(self.agent, "_route_planner", None)
                        if planner is None:
                            raise RuntimeError(
                                "METHOD_V3_NATIVE_ROUTE_PLANNER_OWNER_MISSING"
                            )
                        resolved_route = (
                            self._method_v2_execution.record_native_route_consumption(
                                active_route_identity=active_route_identity,
                                consumed_route_identity=consumed_route_identity,
                                route_generation=int(
                                    getattr(planner, "online_update_generation", -1)
                                ),
                                destination_identity=str(
                                    getattr(owner, "global_destination_identity", "")
                                ),
                            )
                        )
                        self._method_v3_resolved_selected_local_route = (
                            resolved_route
                        )
                        trajectory = _points(baseline_route) or ()
                        position = self._latest_position
                        compass = self._latest_navigation_compass_radians
                        try:
                            ego_xy = (
                                float(position[0]),
                                float(position[1]),
                            )
                            ego_heading = float(compass)
                        except (IndexError, TypeError, ValueError):
                            ego_xy = (math.nan, math.nan)
                            ego_heading = math.nan
                        geometry_envelope = float(
                            self._phase_b_contract[
                                "calibrated_uncertainty_upper_m"
                            ]
                        )
                        geometry_source_digest = canonical_sha256(
                            {
                                "resolved_route_geometry_evidence_digest": (
                                    resolved_route.geometry_evidence_digest
                                ),
                                "existing_calibration_owner": (
                                    "PHASE_B_CALIBRATED_UNCERTAINTY_UPPER_M"
                                ),
                                "existing_calibration_value_m": geometry_envelope,
                            }
                        )
                        phase = self._method_v3_phase_owner.phase
                        admissibility = verify_selected_plan_admissibility(
                            SelectedPlanAdmissibilityInput(
                                resolved_selected_local_route=resolved_route,
                                active_route_identity=active_route_identity,
                                route_generation=int(
                                    getattr(planner, "online_update_generation", -1)
                                ),
                                route_transaction_identity=(
                                    resolved_route.route_transaction_identity
                                ),
                                selected_plan_transaction_identity=(
                                    resolved_route.selected_plan_transaction_identity
                                ),
                                destination_identity=str(
                                    getattr(owner, "global_destination_identity", "")
                                ),
                                current_phase=phase.value,
                                phase_allows_selected_execution=phase
                                in {
                                    MethodV3Phase.SELECTED_ACTIVE_PRECOMMIT,
                                    MethodV3Phase.COMMITTED,
                                },
                                ego_route_planner_xy_m=ego_xy,
                                ego_heading_radians=ego_heading,
                                predicted_trajectory_ego_local_xy_m=trajectory,
                                geometry_uncertainty_envelope_m=(
                                    geometry_envelope
                                ),
                                geometry_evidence_digest=(
                                    geometry_source_digest
                                ),
                                incompatible_branch_observed=None,
                            )
                        )
                        self._method_v2_execution.record_selected_plan_admissibility(
                            admissibility
                        )
                        self._receipt[
                            "method_v3_resolved_selected_local_route"
                        ] = resolved_route.to_dict()
                        self._receipt[
                            "method_v3_selected_plan_admissibility"
                        ] = admissibility.to_dict()
                    elif getattr(self, "_method_v2_8_enabled", False):
                        obligation = self._method_v2_selected_obligation
                        if obligation is None:
                            raise RuntimeError(
                                "METHOD_V2_8_SELECTED_OBLIGATION_MISSING_AT_REFRESH"
                            )
                        self._method_v2_execution.record_topology_locked_refresh(
                            observation_id=str(self._latest_observation_id),
                            frame_id=int(self._latest_frame),
                            candidate_id=identity.candidate_id,
                            interpretation_id=identity.interpretation_id,
                            obligation_digest=binding.obligation_digest,
                            branch_digest=binding.branch_digest,
                            navigation_context_identity=(
                                identity.navigation_context_identity
                            ),
                            global_destination_identity=(
                                binding.global_destination_identity
                            ),
                            mission_context_digest=(
                                obligation.mission_context_digest
                            ),
                            route_version=str(self._runtime_route_version),
                            environment_digest=str(
                                self._runtime_environment_digest
                            ),
                            refreshed_route_digest=_tensor_digest(
                                baseline_route
                            ),
                            refreshed_speed_digest=_tensor_digest(
                                baseline_speed
                            ),
                        )
                    rows = self._receipt.setdefault(
                        "method_revision_v2_normal_forward_consumption", []
                    )
                    rows.append(
                        {
                            "observation_id": str(self._latest_observation_id),
                            "frame_id": int(self._latest_frame),
                            "model_start_monotonic_s": float(model_start),
                            "model_end_monotonic_s": float(model_end),
                            "model_latency_s": float(model_end) - float(model_start),
                            "route_digest": _tensor_digest(baseline_route),
                            "speed_digest": _tensor_digest(baseline_speed),
                            "projection_digest": binding.projection_digest,
                            "normal_simlingo_forward_count": 1,
                            "continued_k2_candidate_forward_count": 0,
                            "new_pid_count": 0,
                            "direct_vehicle_control_write_count": 0,
                        }
                    )
                self._method_v2_pending_binding = None
                self._receipt.update(
                    {
                        "status": (
                            "METHOD_V2_BOUNDED_SELECTED_NAVIGATION_ACTIVE"
                            if self._method_v2_execution.selected_navigation_required
                            else "METHOD_V2_FAIL_CLOSED"
                        ),
                        "method_revision_v2_status": (
                            self._method_v2_execution.state.value
                        ),
                        "method_revision_v2_execution": (
                            self._method_v2_execution.summary()
                        ),
                    }
                )
                self._persist()
                return
            if (
                getattr(self, "_method_revision_v2_enabled", False)
                and self._method_v2_execution.activation_frame_id is not None
            ):
                # Completion releases the selected local obligation before this
                # tick's sole PID. Do not resume K=2 comparison or mint another
                # candidate forward while reconnect evidence is being consumed.
                self._receipt["method_revision_v2_execution"] = (
                    self._method_v2_execution.summary()
                )
                self._persist()
                return
            if self._initial_k1_pending and not self._initial_k1_emitted:
                self._emit_initial_k1_decision(baseline_route, baseline_speed)
                self._persist()
                return
            if self._terminal or self._episode_id is None:
                if (
                    self._rq2_t_temporal_observer is not None
                    and _truthy(
                        os.environ.get("DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED")
                    )
                    and self._receipt.get("rq2_t_evidence_unavailable_reason")
                    in {
                        "CERTIFIED_INSTRUCTION_HAS_NO_VISUAL_REFERENT_PHRASE",
                        "CERTIFIED_AMBIGUITY_RUNTIME_GROUNDING_EFFECTIVE_K_LT_2",
                        "RUNTIME_P1_TRANSACTION_FAILED_EVIDENCE_UNKNOWN",
                    }
                ):
                    self._observe_rq2_t_unknown_evidence(
                        str(
                            self._receipt.get(
                                "rq2_t_evidence_unavailable_reason"
                            )
                        )
                    )
                self._persist()
                return
            episode = self.persistent_store.get(self._episode_id)
            if self._material_context_invalidated:
                self._receipt["status"] = (
                    "PERSISTENT_MATERIAL_CONTEXT_INVALIDATED_FAIL_CLOSED"
                )
                self._persist()
                return
            if self._convergence_replan_pending:
                try:
                    self._run_convergence_fresh_replan()
                except Exception as convergence_error:
                    self._fail_convergence_replan(convergence_error)
                self._persist()
                return
            if self._replan_pending and self._fresh_forward is None:
                if self._decision_evidence_v2_enabled or self._decision_evidence_v3_enabled:
                    post_answer_observation = self._runtime_window_observation(
                        time.monotonic()
                    )
                    post_answer_safety = (
                        post_answer_observation.current_physical_safety_gate
                        if self._decision_evidence_v3_enabled
                        else post_answer_observation.dynamic_safety_gate
                    )
                    if not post_answer_safety or (
                        not getattr(self, "_method_v2_1_enabled", False)
                        and not post_answer_observation.hard_rule_gate
                    ):
                        deferrals = int(
                            self._receipt.get(
                                "post_answer_hard_gate_deferral_count", 0
                            )
                        ) + 1
                        self._receipt.update(
                            {
                                "status": (
                                    "POST_ANSWER_REPLAN_DEFERRED_BY_EXISTING_HARD_GATE"
                                ),
                                "post_answer_hard_gate_deferral_count": deferrals,
                                "post_answer_dynamic_safety_gate": (
                                    post_answer_safety
                                ),
                                "post_answer_hard_rule_gate": (
                                    post_answer_observation.hard_rule_gate
                                ),
                                "post_answer_deferred_model_forward_count": 0,
                                "existing_safety_architecture_preserved": True,
                            }
                        )
                        self._persist()
                        return
                    if (
                        getattr(self, "_method_v2_1_enabled", False)
                        and post_answer_observation.hard_rule_gate is not True
                    ):
                        self._receipt.update(
                            {
                                "status": (
                                    "METHOD_V2_1_FRESH_REPLAN_DURING_MOTION_BLOCKED_HOLD"
                                ),
                                "post_answer_hard_rule_gate": (
                                    post_answer_observation.hard_rule_gate
                                ),
                                "fresh_replan_grants_motion_authority": False,
                            }
                        )
                self._run_persistent_fresh_replan()
                self._persist()
                return
            every_observation_k_way = bool(
                episode.semantic_state.value == "UNRESOLVED"
                and len(episode.active_candidate_ids) > 1
            )
            if timing_row is not None:
                timing_row["k_way_required_at_model_stage_start"] = (
                    every_observation_k_way
                )
            if not every_observation_k_way and episode.evidence_state not in (
                EvidenceState.REFRESH_REQUIRED,
                EvidenceState.STALE,
                EvidenceState.INVALID,
            ):
                self._persist()
                return
            self._refresh_sequence += 1
            now = time.monotonic()
            event_prefix = "{}:refresh-{}".format(self._episode_id, self._refresh_sequence)
            self.persistent_store = self.persistent_store.apply(
                StoreEvent(
                    episode_id=self._episode_id,
                    event_id=event_prefix + ":started",
                    event_type="REFRESH_BUNDLE_STARTED",
                    observed_monotonic_time=now,
                    source_frame_id=self._latest_frame,
                    reason_code="NORMAL_PLANNING_EVENT_REFRESH_STARTED",
                    payload={},
                )
            )
            candidates = self._refresh_candidates()
            bundle = self.refresh_scheduler.refresh(
                normal_planning_event_id=event_prefix,
                source_observation_id=str(self._latest_observation_id),
                source_frame_id=self._latest_frame,
                candidates=candidates,
                forward=lambda row, label: self._forward(row.payload, label, latest=True),
                route_digest=self._result_route_digest,
                speed_digest=self._result_speed_digest,
                max_candidate_forwards=len(candidates),
                detector_forward_count=0,
            )
            self._latest_bundle = bundle
            if any(
                "out of memory" in row.detail.casefold()
                for row in bundle.failures
            ):
                self._gpu_oom_count += 1
            successful_candidate_timing_rows = [
                {
                    "candidate_index": list(bundle.requested_candidate_ids).index(
                        row.candidate_id
                    ),
                    "candidate_id": row.candidate_id,
                    "interpretation_id": row.interpretation_id,
                    "source_observation_id": row.source_observation_id,
                    "source_frame_id": row.source_frame_id,
                    "outcome": "SUCCEEDED",
                    "forward_start_monotonic_s": row.forward_start_monotonic_s,
                    "forward_end_monotonic_s": row.forward_end_monotonic_s,
                    "forward_latency_s": row.forward_latency_s,
                }
                for row in bundle.evidence
            ]
            failed_candidate_timing_rows = [
                {
                    "candidate_index": list(bundle.requested_candidate_ids).index(
                        row.candidate_id
                    ),
                    "candidate_id": row.candidate_id,
                    "interpretation_id": row.interpretation_id,
                    "source_observation_id": bundle.source_observation_id,
                    "source_frame_id": bundle.source_frame_id,
                    "outcome": "FAILED",
                    "error_type": row.error_type,
                    "reason_code": row.reason_code,
                    "forward_start_monotonic_s": row.forward_start_monotonic_s,
                    "forward_end_monotonic_s": row.forward_end_monotonic_s,
                    "forward_latency_s": row.forward_latency_s,
                }
                for row in bundle.failures
            ]
            candidate_timing_rows = sorted(
                successful_candidate_timing_rows + failed_candidate_timing_rows,
                key=lambda row: int(row["candidate_index"]),
            )
            if timing_row is not None:
                timing_row["candidate_forwards"] = candidate_timing_rows
                timing_row["candidate_forward_count"] = bundle.executed_count
                timing_row["candidate_forward_succeeded_count"] = (
                    bundle.succeeded_count
                )
                timing_row["candidate_forward_failed_count"] = bundle.failed_count
                timing_row["total_candidate_latency_s"] = sum(
                    float(row["forward_latency_s"] or 0.0)
                    for row in candidate_timing_rows
                )
            ledger_row = {
                "sequence": len(self._candidate_forward_ledger) + 1,
                "normal_planning_event_id": bundle.normal_planning_event_id,
                "source_observation_id": bundle.source_observation_id,
                "source_frame_id": bundle.source_frame_id,
                "candidate_set_digest": bundle.candidate_set_digest,
                "candidate_ids": list(bundle.requested_candidate_ids),
                "effective_k": len(episode.active_candidate_ids),
                "semantic_state": episode.semantic_state.value,
                "query_pending_at_refresh_start": episode.active_query is not None,
                "active_query_id_at_refresh_start": (
                    None
                    if episode.active_query is None
                    else episode.active_query.query_id
                ),
                "normal_forward_count": bundle.normal_forward_count,
                "candidate_forward_count": bundle.executed_count,
                "candidate_forward_succeeded_count": bundle.succeeded_count,
                "candidate_forward_failed_count": bundle.failed_count,
                "candidate_forward_attempt_timing_complete": bool(
                    len(candidate_timing_rows) == bundle.attempted_count
                    and all(
                        row.get("forward_start_monotonic_s") is not None
                        and row.get("forward_end_monotonic_s") is not None
                        and row.get("forward_latency_s") is not None
                        for row in candidate_timing_rows
                    )
                ),
                "a_b_same_observation_identity": all(
                    row["source_observation_id"] == bundle.source_observation_id
                    for row in candidate_timing_rows
                ),
                "a_b_same_frame_identity": all(
                    row["source_frame_id"] == bundle.source_frame_id
                    for row in candidate_timing_rows
                ),
                "candidate_forwards": candidate_timing_rows,
                "bundle_latency_s": bundle.latency_seconds,
            }
            self._candidate_forward_ledger.append(ledger_row)
            self._receipt["candidate_forward_ledger_row_count"] = len(
                self._candidate_forward_ledger
            )
            if not bundle.complete:
                self.persistent_store = self.persistent_store.apply(
                    StoreEvent(
                        episode_id=self._episode_id,
                        event_id=event_prefix + ":partial",
                        event_type="REFRESH_BUNDLE_PARTIAL",
                        observed_monotonic_time=time.monotonic(),
                        source_frame_id=self._latest_frame,
                        reason_code="REFRESH_BUNDLE_PARTIAL",
                        payload={
                            "normal_forward_count": bundle.normal_forward_count,
                            "candidate_forward_requested_count": bundle.requested_count,
                            "candidate_forward_attempted_count": bundle.attempted_count,
                            "candidate_forward_count": bundle.executed_count,
                            "candidate_forward_succeeded_count": bundle.succeeded_count,
                            "detector_forward_count": bundle.detector_forward_count,
                            "candidate_forward_skipped_count": bundle.skipped_count,
                            "candidate_forward_failed_count": bundle.failed_count,
                        },
                    )
                )
                self._receipt.update(
                    {
                        "status": "PERSISTENT_REFRESH_PARTIAL_FAIL_CLOSED",
                        "persistent_refresh_bundle": bundle.to_dict(),
                        "partial_refresh_compute_accounting_recorded": True,
                    }
                )
                self._persist()
                return
            observation = self._runtime_window_observation(time.monotonic())
            v1_diagnostic_window = evaluate_runtime_decision_window(
                bundle,
                observation,
                target_obligation_digests=self._target_obligation_digests,
                authorized_plan_points=_points(baseline_route),
                authorized_plan_digest=str(_tensor_digest(baseline_route)),
                authorized_speed_points=_points(baseline_speed),
                authorized_speed_digest=str(_tensor_digest(baseline_speed)),
            )
            if self._decision_evidence_v3_enabled:
                window = evaluate_runtime_decision_evidence_v3(
                    bundle=bundle,
                    observation=observation,
                    episode=episode,
                    connectors=self._candidate_connectors,
                    qualified_obligations=(
                        self._candidate_local_navigation_obligations
                        if self._candidate_local_navigation_mode
                        else None
                    ),
                    authorized_plan_points=_points(baseline_route) or (),
                    authorized_plan_digest=str(_tensor_digest(baseline_route)),
                    authorized_speed_points=_points(baseline_speed) or (),
                    authorized_speed_digest=str(_tensor_digest(baseline_speed)),
                    answer_latency_simulation_s=self.answer_delay_sim_seconds,
                    full_plan_coverage_v1=v1_diagnostic_window.full_plan_coverage,
                )
            elif self._decision_evidence_v2_enabled:
                window = evaluate_runtime_decision_evidence_v2(
                    bundle=bundle,
                    observation=observation,
                    episode=episode,
                    connectors=self._candidate_connectors,
                    authorized_plan_points=_points(baseline_route) or (),
                    authorized_plan_digest=str(_tensor_digest(baseline_route)),
                    authorized_speed_points=_points(baseline_speed) or (),
                    authorized_speed_digest=str(_tensor_digest(baseline_speed)),
                    answer_latency_simulation_s=self.answer_delay_sim_seconds,
                    full_plan_coverage_v1=v1_diagnostic_window.full_plan_coverage,
                )
            else:
                window = v1_diagnostic_window
            self._latest_window = window
            relationship = window.candidate_relationship
            consequence = (
                "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT"
                if relationship == "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT"
                else "CURRENTLY_DIVERGENT"
                if relationship in ("CURRENTLY_DIVERGENT", "CURRENT_ACTION_DIVERGENT")
                else "NO_MATERIAL_DIVERGENCE"
                if relationship
                in ("NO_MATERIAL_DIVERGENCE", "CURRENT_AND_FUTURE_EQUIVALENT")
                else "CURRENT_ACTION_SHARED_FUTURE_UNKNOWN"
                if relationship == "CURRENT_ACTION_SHARED_FUTURE_UNKNOWN"
                else "UNKNOWN"
            )
            self.persistent_store = self.persistent_store.apply(
                StoreEvent(
                    episode_id=self._episode_id,
                    event_id=event_prefix + ":complete",
                    event_type="REFRESH_BUNDLE_COMPLETE_VALID",
                    observed_monotonic_time=time.monotonic(),
                    source_frame_id=self._latest_frame,
                    reason_code="FRESH_SAME_FRAME_CANDIDATE_BUNDLE_PUBLISHED",
                    payload={
                        "active_candidate_ids": bundle.requested_candidate_ids,
                        "source_observation_id": self._latest_observation_id,
                        "route_version": observation.route_version,
                        "environment_digest": observation.environment_digest,
                        "freshness_deadline_monotonic": (
                            window.valid_until_monotonic
                            if window.valid_until_monotonic is not None
                            else time.monotonic()
                        ),
                        "candidate_set_id": self.persistent_store.get(self._episode_id).candidate_set_id,
                        "consequence_state": consequence,
                        "relationship": relationship,
                        "future_divergence_evidence_digest": (
                            window.future_obligation_evidence_digest
                            if (
                                self._decision_evidence_v2_enabled
                                or self._decision_evidence_v3_enabled
                            )
                            else canonical_sha256(
                                dict(self._target_obligation_digests)
                            )
                        ),
                        "decision_window_evidence_digest": window.decision_window_digest,
                        "decision_deadline_evidence_digest": canonical_sha256(
                            window.latest_safe_clarification_monotonic
                        ),
                        "normal_forward_count": bundle.normal_forward_count,
                        "candidate_forward_requested_count": bundle.requested_count,
                        "candidate_forward_attempted_count": bundle.attempted_count,
                        "candidate_forward_count": bundle.executed_count,
                        "candidate_forward_succeeded_count": bundle.succeeded_count,
                        "detector_forward_count": bundle.detector_forward_count,
                        "candidate_forward_skipped_count": bundle.skipped_count,
                        "candidate_forward_failed_count": bundle.failed_count,
                    },
                )
            )
            current_episode = self.persistent_store.get(self._episode_id)
            active_candidate_count = len(current_episode.active_candidate_ids)
            multiple_plausible = bool(
                active_candidate_count >= 2
                and len(
                    {
                        row.semantic_sha256
                        for row in current_episode.candidates
                        if row.candidate_id in current_episode.active_candidate_ids
                    }
                )
                >= 2
            )
            material_divergence = (
                None
                if window.candidate_relationship
                == "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"
                else (
                    window.future_obligation_relation in {"FUTURE_DIVERGENT", "DIVERGENT"}
                    and window.candidate_relationship
                    in {
                        "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT",
                        "CURRENTLY_DIVERGENT",
                        "CURRENT_ACTION_DIVERGENT",
                    }
                )
            )
            answer_changes_decision = bool(
                multiple_plausible
                and len(set(self._target_obligation_digests.values())) >= 2
            )
            positive_query_value = (
                None
                if window.latest_safe_slack_s is None
                else bool(
                    answer_changes_decision
                    and window.latest_safe_slack_s > self.answer_delay_sim_seconds
                )
            )
            query_budget_available = bool(
                current_episode.active_query is None
                and len(current_episode.query_history) == 0
            )
            passenger_resolvable = self._passenger_resolvable_candidates(
                current_episode.active_candidate_ids
            )
            v1_context = PersistentDecisionContext(
                active_candidate_count=active_candidate_count,
                semantic_state=current_episode.semantic_state.value,
                current_action_relation=(
                    AxisValue.CURRENT_ACTION_EQUIVALENT
                    if window.current_action_relation == "CURRENT_ACTION_EQUIVALENT"
                    else AxisValue.CURRENT_ACTION_DIVERGENT
                    if relationship == "CURRENTLY_DIVERGENT"
                    else AxisValue.UNKNOWN
                ),
                future_obligation_relation=(
                    AxisValue.FUTURE_DIVERGENT
                    if window.future_obligation_relation == "FUTURE_DIVERGENT"
                    else AxisValue.NO_MATERIAL_DIVERGENCE
                    if window.future_obligation_relation == "NO_MATERIAL_DIVERGENCE"
                    else AxisValue.UNKNOWN
                ),
                evidence_fresh=(
                    True
                    if bundle.complete
                    and not any(
                        reason in window.reason_codes
                        for reason in (
                            "REFRESH_BUNDLE_INCOMPLETE",
                            "SOURCE_IDENTITY_NOT_ALIGNED",
                            "REFRESH_BUNDLE_LATENCY_BOUND_EXCEEDED",
                        )
                    )
                    else None
                ),
                full_plan_coverage=(True if window.full_plan_coverage else None),
                alignment_verified=observation.alignment_verified,
                shared_action_safe=(
                    observation.dynamic_safety_gate and observation.hard_rule_gate
                ),
                recoverable=(
                    True if window.recoverability == "RECOVERABLE" else None
                ),
                latest_safe_slack_positive=(
                    None
                    if window.latest_safe_slack_s is None
                    else window.latest_safe_slack_s > 0.0
                ),
                decision_deadline_available=(
                    True
                    if window.latest_safe_clarification_monotonic is not None
                    else None
                ),
                decision_deadline_crossed=(
                    None
                    if window.latest_safe_clarification_monotonic is None
                    else time.monotonic()
                    >= window.latest_safe_clarification_monotonic
                ),
                hard_safety_gate=observation.dynamic_safety_gate,
                hard_rule_gate=observation.hard_rule_gate,
                active_query=current_episode.active_query is not None,
                active_holding_lease=self.wait.active,
                multiple_plausible_interpretations=multiple_plausible,
                material_consequence_divergence=material_divergence,
                answer_changes_decision=answer_changes_decision,
                positive_query_value=positive_query_value,
                query_budget_available=query_budget_available,
                answer_likely_before_deadline=(
                    None
                    if window.latest_safe_slack_s is None
                    else self.answer_delay_sim_seconds < window.latest_safe_slack_s
                ),
                passenger_resolvable=passenger_resolvable,
                verified_holding_available=bool(
                    self.wait.active
                    and self.wait.state is not None
                    and self.wait.state.query_active
                ),
            )
            if self._decision_evidence_v3_enabled:
                context = DecisionContextV3(
                    evidence=window.v3_bundle,
                    semantic_state=current_episode.semantic_state.value,
                    active_candidate_count=active_candidate_count,
                    multiple_plausible_interpretations=multiple_plausible,
                    answer_changes_decision=answer_changes_decision,
                    query_budget_available=query_budget_available,
                    passenger_resolvable=passenger_resolvable,
                    active_query=current_episode.active_query is not None,
                    verified_holding_available=bool(
                        self.wait.active
                        and self.wait.state is not None
                        and self.wait.state.query_active
                    ),
                    hard_safety_gate=observation.current_physical_safety_gate,
                    hard_rule_gate=observation.hard_rule_gate,
                    **self._method_v2_1_decision_kwargs(window, observation),
                )
            elif self._decision_evidence_v2_enabled:
                context = DecisionContextV2(
                    evidence=window.v2_bundle,
                    semantic_state=current_episode.semantic_state.value,
                    active_candidate_count=active_candidate_count,
                    multiple_plausible_interpretations=multiple_plausible,
                    answer_changes_decision=answer_changes_decision,
                    query_budget_available=query_budget_available,
                    passenger_resolvable=passenger_resolvable,
                    active_query=current_episode.active_query is not None,
                    verified_holding_available=bool(
                        self.wait.active
                        and self.wait.state is not None
                        and self.wait.state.query_active
                    ),
                    hard_safety_gate=observation.dynamic_safety_gate,
                    hard_rule_gate=observation.hard_rule_gate,
                )
            else:
                context = v1_context
            if getattr(self, "_method_v2_7_enabled", False):
                if self._method_v2_7_continuation is None:
                    raise RuntimeError("METHOD_V2_7_CONTINUATION_EVIDENCE_MISSING")
                current_semantic_plan_rows = [
                    row
                    for row in self._candidate_local_navigation_forward_receipts
                    if str(row.get("planning_observation_id"))
                    == str(observation.source_observation_id)
                    and str(row.get("candidate_id"))
                    in set(current_episode.active_candidate_ids)
                ]
                candidate_plan_semantics_realized = bool(
                    {
                        str(row.get("candidate_id"))
                        for row in current_semantic_plan_rows
                    }
                    == set(current_episode.active_candidate_ids)
                    and all(
                        row.get("candidate_plan_semantics_realized") is True
                        for row in current_semantic_plan_rows
                    )
                )
                ambiguity_confirmation_fingerprint = canonical_sha256(
                    {
                        "semantic_candidates": sorted(
                            (
                                str(row.get("candidate_id")),
                                str(row.get("interpretation_id")),
                                str(row.get("semantic_sha256")),
                                str(row.get("referent_id")),
                                str(row.get("target_id")),
                                str(
                                    self._target_obligation_digests.get(
                                        str(row.get("candidate_id")), ""
                                    )
                                ),
                            )
                            for row in self._bound_candidates
                        ),
                        "topology_event_identity": str(
                            self._method_v2_7_boundary.topology_event_identity
                        ),
                        "route_version": str(self._runtime_route_version),
                        "environment_digest": str(
                            self._runtime_environment_digest
                        ),
                    }
                )
                observation_identity = str(observation.source_observation_id)
                provisional = self._method_v2_7_provisional_ambiguity
                ambiguity_confirmed = bool(
                    provisional is not None
                    and provisional.get("fingerprint")
                    == ambiguity_confirmation_fingerprint
                    and provisional.get("source_observation_id")
                    != observation_identity
                )
                if provisional is None or provisional.get(
                    "fingerprint"
                ) != ambiguity_confirmation_fingerprint:
                    self._method_v2_7_provisional_ambiguity = {
                        "fingerprint": ambiguity_confirmation_fingerprint,
                        "source_observation_id": observation_identity,
                    }
                    ambiguity_confirmed = False
                if current_episode.active_query is not None:
                    ambiguity_confirmed = True
                self._receipt["method_v2_7_ambiguity_confirmation"] = {
                    "status": (
                        "CONFIRMED_ACROSS_FRESH_OBSERVATIONS"
                        if ambiguity_confirmed
                        else "PROVISIONAL_FIRST_VALID_OBSERVATION"
                    ),
                    "fingerprint": ambiguity_confirmation_fingerprint,
                    "first_source_observation_id": self._method_v2_7_provisional_ambiguity[
                        "source_observation_id"
                    ],
                    "current_source_observation_id": observation_identity,
                    "candidate_trajectory_geometry_used_as_confirmation": False,
                    "candidate_plan_semantics_realized": (
                        candidate_plan_semantics_realized
                    ),
                    "candidate_plan_semantic_evidence": [
                        {
                            key: row.get(key)
                            for key in (
                                "candidate_id",
                                "candidate_plan_semantics_realized",
                                "candidate_plan_semantics_owner",
                                "target_terminal_lateral_m",
                                "predicted_terminal_lateral_m",
                                "execution_location_plan_realization",
                            )
                        }
                        for row in current_semantic_plan_rows
                    ],
                    "new_numeric_threshold_count": 0,
                }
                v27_result = decide_v27(
                    DecisionContextV27(
                        semantic_state=current_episode.semantic_state.value,
                        effective_k=active_candidate_count,
                        plausible_interpretations=(
                            active_candidate_count if multiple_plausible else 0
                        ),
                        future_obligation_relation=(
                            window.future_obligation_relation
                        ),
                        future_obligation_evidence_valid=bool(
                            window.v3_bundle.future_obligation.authorization_eligible
                        ),
                        answer_can_resolve_or_reduce=answer_changes_decision,
                        query_transaction_valid=bool(
                            bundle.complete and query_budget_available
                            or current_episode.active_query is not None
                        ),
                        candidate_identity_keyset_valid=bool(
                            set(bundle.requested_candidate_ids)
                            == set(current_episode.active_candidate_ids)
                        ),
                        source_identity_fresh=bool(
                            bundle.source_observation_id
                            == observation.source_observation_id
                            and str(bundle.source_frame_id)
                            == str(observation.source_frame_id)
                        ),
                        topology_valid=bool(
                            window.v3_bundle.future_obligation.authorization_eligible
                        ),
                        information_safety_valid=bool(
                            observation.current_physical_safety_gate
                        ),
                        query_active=current_episode.active_query is not None,
                        answer_pending=bool(
                            current_episode.active_query is not None
                            and self._answer_received_sim_time is None
                        ),
                        resolved_answer_exists=False,
                        continuation=self._method_v2_7_continuation,
                        ambiguity_evidence_confirmed_across_observations=(
                            ambiguity_confirmed
                        ),
                        candidate_plan_semantics_realized=(
                            candidate_plan_semantics_realized
                        ),
                        current_action_relation=window.current_action_relation,
                        all_candidate_recoverability=window.recoverability,
                        shared_action_lease_valid=bool(
                            window.v3_bundle.shared_action_lease.valid
                        ),
                        candidate_geometry_similar=(
                            window.v3_bundle.current_action.geometry_control_compatible
                        ),
                        route_timing_calibrated=bool(
                            window.v3_bundle.clarification.authorization_eligible
                        ),
                    )
                )
                recommendation = SimpleNamespace(
                    decision=v27_result.decision,
                    relation=window.v3_bundle.compatibility_relationship,
                    target_type=v27_result.target_type,
                    authority_subject_type=v27_result.authority_subject_type,
                    reason_codes=v27_result.reason_codes,
                    v27=v27_result,
                )
                self._receipt["method_v2_7_decision"] = {
                    "decision": v27_result.decision.value,
                    "lifecycle_state": v27_result.lifecycle_state,
                    "control_owner": v27_result.control_owner,
                    "ask_grants_vehicle_motion_authority": False,
                    "candidate_vehicle_control_authority": False,
                    "act_shared_external_action_emitted": False,
                    "current_planning_ambiguity_active": bool(
                        candidate_plan_semantics_realized
                    ),
                    "planning_effective_k": (
                        active_candidate_count
                        if candidate_plan_semantics_realized
                        else 1
                    ),
                }
            else:
                recommendation = (
                    decide_v3(context)
                    if self._decision_evidence_v3_enabled
                    else decide_v2(context)
                    if self._decision_evidence_v2_enabled
                    else decide_persistent(context)
                )
            self._decision = _PersistentDecisionView(recommendation.decision.value)
            if (
                getattr(self, "_method_v2_7_enabled", False)
                and recommendation.decision.value == "WAIT"
            ):
                # The historical envelope requires holding authority to be
                # active when WAIT is serialized. Enter the existing owner
                # first; this creates no controller or VehicleControl writer.
                self._enter_method_v2_7_wait(window)
            self._record_recommendation_envelope(
                recommendation=recommendation,
                bundle=bundle,
                window=window,
                episode=current_episode,
            )
            decision_history_row = _persistent_decision_history_row(
                bundle=bundle,
                window=window,
                recommendation=recommendation,
                episode=current_episode,
                m2b_context=context,
            )
            decision_history_row["sequence"] = len(self._persistent_decision_history) + 1
            if getattr(self, "_method_v2_7_enabled", False):
                decision_history_row["current_planning_ambiguity_active"] = bool(
                    candidate_plan_semantics_realized
                )
                decision_history_row["planning_effective_k"] = (
                    active_candidate_count
                    if candidate_plan_semantics_realized
                    else 1
                )
            self._persistent_decision_history.append(decision_history_row)
            if self._rq2_t_temporal_observer is not None:
                # Reuse the just-computed V3 evidence row and simulator clock.
                # No model/planner/PID/control owner is called by this hook.
                self._rq2_t_temporal_observer.observe(
                    decision_history_row,
                    simulation_time_s=float(self._latest_simulation_time),
                )
            self._receipt["persistent_decision_history"] = list(
                self._persistent_decision_history
            )
            dashboard_run_id = os.environ.get("DRIVECLARIFY_PROBE_RUN_ID") or str(
                self._episode_id
            )
            self._receipt["decision_window_dashboard"] = (
                _persistent_decision_window_dashboard(
                    run_id=dashboard_run_id,
                    raw_instruction=self.raw_instruction,
                    bound_candidates=self._bound_candidates,
                    bundle=bundle,
                    window=window,
                    observation=observation,
                    episode=current_episode,
                    decision=recommendation.decision.value,
                    authority_subject_type=recommendation.authority_subject_type,
                    dino_forward_count=int(
                        self._receipt.get("dino_event_triggered_forward_count")
                        or (
                            self._receipt.get("grounding", {}).get(
                                "detector_forward_count"
                            )
                            if isinstance(self._receipt.get("grounding"), Mapping)
                            else 0
                        )
                        or 0
                    ),
                )
            )
            self._receipt.update(
                {
                    "persistent_refresh_bundle": bundle.to_dict(),
                    "persistent_decision_window": window.to_dict(),
                    "persistent_m2b_recommendation": {
                        "decision": recommendation.decision.value,
                        "relation": recommendation.relation.value,
                        "target_type": recommendation.target_type,
                        "authority_subject_type": recommendation.authority_subject_type,
                        "reason_codes": list(recommendation.reason_codes),
                    },
                    "initial_decision": recommendation.decision.value,
                    "decision_why": recommendation.relation.value,
                    "candidate_plan_repetitions": [
                        {
                            "candidate_id": row.candidate_id,
                            "interpretation_id": row.interpretation_id,
                            "source_observation_id": row.source_observation_id,
                            "route_digest": row.route_digest,
                            "speed_digest": row.speed_digest,
                            "plan_reference_digest": row.plan_reference_digest,
                            "source_frame_id": row.source_frame_id,
                            # Passive receipt serialization only: these arrays are
                            # taken from the candidate forward that already ran for
                            # this refresh bundle.  Rendering must consume these
                            # values and must never trigger another model forward.
                            "model_predicted_local_route": _points(
                                getattr(row.result, "raw_route", None)
                            ),
                            "model_predicted_speed_waypoints": _points(
                                getattr(row.result, "raw_speed", None)
                            ),
                            "plan_frame": "MODEL_LOCAL_EGO_BEV",
                            "plan_dimension_order": ["forward_m", "lateral_m"],
                            "visualization_source": "EXISTING_RUNTIME_FORWARD_RESULT",
                        }
                        for row in bundle.evidence
                    ],
                    "legacy_initial_candidate_plan_call_count": self._legacy_initial_candidate_plan_calls,
                    "legacy_candidate_repeat_count": 0,
                    "legacy_m2b_call_count": self._legacy_m2b_calls,
                    "legacy_arm_call_count": self._legacy_arm_calls,
                }
            )
            trajectory_row = {
                "sequence": len(self._candidate_trajectory_history) + 1,
                "source_observation_id": bundle.source_observation_id,
                "source_frame_id": bundle.source_frame_id,
                "candidate_set_digest": bundle.candidate_set_digest,
                "decision": recommendation.decision.value,
                "current_action_relation": window.current_action_relation,
                "future_obligation_relation": window.future_obligation_relation,
                "candidates": list(self._receipt["candidate_plan_repetitions"]),
            }
            self._candidate_trajectory_history.append(trajectory_row)
            self._receipt["candidate_trajectory_history_row_count"] = len(
                self._candidate_trajectory_history
            )
            rq2_t_2a_observational_only = _truthy(
                os.environ.get("DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED")
            )
            if rq2_t_2a_observational_only:
                self._receipt.update(
                    {
                        "status": "RQ2_T_2A_DESCRIPTIVE_RECOMMENDATION_ONLY",
                        "rq2_t_2a_observational_only": True,
                        "rq2_t_2a_policy_enactment_count": 0,
                        "rq2_t_2a_query_enactment_count": 0,
                        "control_owner": "SHARED_PREFIX_LONGITUDINAL_OWNER_V1",
                    }
                )
            elif recommendation.decision.value == "ACT_SHARED":
                self._act_shared(
                    baseline_route, baseline_speed, bundle, window, observation
                )
            elif recommendation.decision.value == "ASK":
                if (
                    getattr(self, "_method_v2_7_enabled", False)
                    and current_episode.active_query is not None
                ):
                    self._receipt.update(
                        {
                            "status": (
                                "METHOD_V2_7_ASK_PENDING_BASELINE_CONTINUATION"
                            ),
                            "query_active": True,
                            "answer_pending": True,
                            "control_owner_during_ask": "BASELINE_SIMLINGO",
                            "ask_grants_vehicle_motion_authority": False,
                        }
                    )
                else:
                    self._enter_persistent_ask(window)
            elif recommendation.decision.value == "WAIT":
                if getattr(self, "_method_v2_7_enabled", False):
                    self._receipt["status"] = (
                        "METHOD_V2_7_WAIT_BEFORE_UNRESOLVED_COMMITMENT"
                    )
                else:
                    self._receipt["status"] = "PERSISTENT_WAIT_ELIGIBLE"
            elif (
                getattr(self, "_method_v2_7_enabled", False)
                and recommendation.decision.value == "ACT"
                and getattr(getattr(recommendation, "v27", None), "lifecycle_state", "")
                in {
                    "PROVISIONAL_AMBIGUITY_BASELINE_OBSERVATION",
                    "NO_CURRENT_PLANNING_HORIZON_AMBIGUITY",
                }
            ):
                lifecycle = getattr(recommendation.v27, "lifecycle_state", "")
                self._receipt.update(
                    {
                        "status": (
                            "METHOD_V2_7_NO_CURRENT_PLANNING_AMBIGUITY_BASELINE_CONTINUES"
                            if lifecycle
                            == "NO_CURRENT_PLANNING_HORIZON_AMBIGUITY"
                            else "METHOD_V2_7_PROVISIONAL_AMBIGUITY_BASELINE_CONTINUES"
                        ),
                        "control_owner": "BASELINE_SIMLINGO",
                        "query_active": False,
                        "candidate_vehicle_control_authority": False,
                    }
                )
            else:
                self._receipt["status"] = "PERSISTENT_DECISION_FAIL_CLOSED_FALLBACK"
                fallback_m3 = build_method_m3_receipt(
                    label=MethodDecisionLabel.FALLBACK,
                    candidate_set_id=current_episode.candidate_set_id,
                    observed_monotonic_time=time.monotonic(),
                )
                self._method_m3_transactions.append(fallback_m3)
                self._receipt["method_v1_m3_transactions"] = list(
                    self._method_m3_transactions
                )
            self._refresh_method_dashboard()
            self._repaint_method_dashboard()
            self._persist()
        except Exception as exc:
            try:
                if self._episode_id is not None:
                    self._material_invalidate(
                        "MATERIAL_INVALIDATION", "P1_TRANSACTION_FAILED"
                    )
            except Exception as invalidation_error:
                self._errors.append(
                    {
                        "stage": "PERSISTENT_P1_FAIL_CLOSED_INVALIDATION",
                        "frame": self._latest_frame,
                        "type": type(invalidation_error).__name__,
                        "message": str(invalidation_error),
                    }
                )
            self._errors.append(
                {
                    "stage": "PERSISTENT_P1_ON_MODEL_OUTPUT",
                    "frame": self._latest_frame,
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            self._observe_rq2_t_unknown_evidence(
                "RUNTIME_P1_TRANSACTION_FAILED_EVIDENCE_UNKNOWN"
            )
            self._receipt["status"] = "BLOCKED_PERSISTENT_AMBIGUITY_RUNTIME_V1"
            self._terminal = True
            self._persist()

    def _act_shared(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        bundle: Any,
        window: Any,
        observation: RuntimeWindowObservation,
    ) -> None:
        assert self._episode_id is not None
        if window.valid_until_monotonic is None:
            raise RuntimeError("ACT_SHARED_VALID_UNTIL_UNKNOWN")
        episode = self.persistent_store.get(self._episode_id)
        consequence_equivalent = (
            episode.current_candidate_relationship
            == "CURRENT_AND_FUTURE_EQUIVALENT"
        )
        v3_refresh_guaranteed = bool(
            getattr(window, "precommitment_refresh_guarantee", None)
            == "GUARANTEED"
        )
        window_id = "shared-window-" + window.decision_window_digest[:20]
        action = SharedActionReference(
            action_class="EXISTING_BASELINE_KEEP_LANE_SHARED_PREFIX",
            shared_action_window_id=window_id,
            source_observation_id=str(self._latest_observation_id),
            source_frame_id=self._latest_frame,
            plan_reference_digest=canonical_sha256(
                {
                    "route_digest": _tensor_digest(baseline_route),
                    "speed_digest": _tensor_digest(baseline_speed),
                }
            ),
            valid_until_monotonic=float(window.valid_until_monotonic),
            active_member_ids=tuple(sorted(episode.active_candidate_ids)),
            preserve_unresolved_semantics=True,
        )
        by_id = {row.candidate_id: row for row in episode.candidates}
        members = tuple(
            ActiveAuthorityMember(
                candidate_id=candidate_id,
                interpretation_id=by_id[candidate_id].interpretation_id,
                semantic_sha256=by_id[candidate_id].semantic_sha256,
                target_obligation_digest=by_id[candidate_id].target_obligation_digest,
            )
            for candidate_id in sorted(episode.active_candidate_ids)
        )
        m3_ready, m3_act = _build_frozen_m3_act_result(
            episode.candidate_set_id, time.monotonic()
        )
        self._method_m3_transactions.append(
            method_m3_receipt_from_results(
                label=MethodDecisionLabel.ACT_SHARED,
                ready=m3_ready,
                final=m3_act,
            )
        )
        self._m3_act_transactions.append(
            {
                "decision_source": "PERSISTENT_TWO_AXIS_M2B_ACT_SHARED",
                "candidate_set_id": episode.candidate_set_id,
                "ready_result": m3_ready.to_dict(),
                "accepted_act_result": m3_act.to_dict(),
                "accepted_act_digest": canonical_sha256(m3_act.to_dict()),
            }
        )
        armed = self.shared_act.arm_shared(
            ambiguity_episode_id=self._episode_id,
            candidate_set_id=episode.candidate_set_id,
            active_members=members,
            shared_action_window_id=window_id,
            shared_action_class=action.action_class,
            route=baseline_route,
            speed=baseline_speed,
            source_observation_id=str(self._latest_observation_id),
            source_frame_id=str(self._latest_frame),
            route_version=observation.route_version,
            environment_digest=observation.environment_digest,
            current_action_equivalence_evidence_digest=(
                window.current_action_equivalence_evidence_digest
            ),
            decision_window_digest=window.decision_window_digest,
            current_monotonic=time.monotonic(),
            valid_until_monotonic=float(window.valid_until_monotonic),
            episode_unresolved=(episode.semantic_state.value == "UNRESOLVED"),
            active_member_set_matches=(
                tuple(sorted(episode.active_candidate_ids))
                == tuple(row.candidate_id for row in members)
            ),
            current_action_relation=episode.current_candidate_relationship,
            evidence_fresh=(episode.evidence_state is EvidenceState.FRESH),
            plan_coverage_verified=bool(
                getattr(
                    window,
                    "current_executable_coverage",
                    window.full_plan_coverage,
                )
            ),
            alignment_verified=_act_shared_alignment_verified(
                window=window,
                observation=observation,
                runtime_route_version=self._runtime_route_version,
                runtime_environment_digest=self._runtime_environment_digest,
                decision_evidence_v3_enabled=self._decision_evidence_v3_enabled,
            ),
            latest_safe_slack_positive=bool(
                consequence_equivalent
                or v3_refresh_guaranteed
                or (
                    window.latest_safe_clarification_monotonic is not None
                    and time.monotonic()
                    < float(window.latest_safe_clarification_monotonic)
                )
            ),
            recoverable=(window.recoverability == "RECOVERABLE"),
            existing_m3_act_result=m3_act,
        )
        if not armed:
            raise RuntimeError("ACT_SHARED_AUTHORITY_ARM_FAILED")
        try:
            self.persistent_store = self.persistent_store.apply(
                StoreEvent(
                    episode_id=self._episode_id,
                    event_id=window_id + ":act-shared",
                    event_type="DECISION_ACT_SHARED",
                    observed_monotonic_time=time.monotonic(),
                    source_frame_id=self._latest_frame,
                    reason_code="ACT_SHARED_ALL_GATES_VERIFIED",
                    payload={"shared_action": action},
                )
            )
        except Exception:
            self.shared_act.revoke(AuthorityReason.CANDIDATE_INVALIDATED)
            raise
        self._shared_window_store_consumed = False
        self._authority_mode = "SHARED"
        self._receipt.update(
            {
                "status": "NATURAL_ACT_SHARED_AUTHORITY_ARMED",
                "authority_subject_type": "SHARED_EQUIVALENCE_CLASS",
                "semantic_state": "UNRESOLVED",
                "method_v1_m3_transactions": list(
                    self._method_m3_transactions
                ),
                "semantic_resolution_from_act_shared": False,
                "act_shared_retains_all_candidate_obligations": True,
                "shared_act": dict(self.shared_act.summary()),
            }
        )

    def _enter_persistent_ask(self, window: Any) -> None:
        assert self._episode_id is not None
        v27_enabled = getattr(self, "_method_v2_7_enabled", False)
        if (
            not v27_enabled
            and window.latest_safe_clarification_monotonic is None
        ):
            raise RuntimeError("PERSISTENT_ASK_DEADLINE_UNKNOWN")
        query_id = "persistent-query-" + canonical_sha256(
            {
                "episode": self._episode_id,
                "observation": self._latest_observation_id,
                "frame": self._latest_frame,
                "decision_window": window.decision_window_digest,
            }
        )[:24]
        self._decision = _PersistentDecisionView("ASK", query_id=query_id)
        # A new normal-planning decision transaction gets a fresh frozen M3
        # cycle; the persistent episode remains the cross-cycle source of
        # semantic truth.
        self._m3_state = None
        question = self._build_question("white van")
        now = time.monotonic()
        if getattr(self, "_method_revision_v2_enabled", False):
            evidence_bundle = getattr(window, "v3_bundle", None)
            if evidence_bundle is None:
                evidence_bundle = getattr(window, "v2_bundle", None)
            clarification = getattr(evidence_bundle, "clarification", None)
            commitment_deadline = getattr(
                clarification,
                "commitment_time_lower_bound_monotonic",
                None,
            )
            deadline_source = "FROZEN_DECISION_EVIDENCE_CLARIFICATION"
            if commitment_deadline is None:
                time_to_divergence = getattr(
                    window, "time_to_divergence_lower_bound_s", None
                )
                source_observed = getattr(
                    getattr(evidence_bundle, "source", None),
                    "observed_monotonic_time",
                    now,
                )
                if time_to_divergence is not None:
                    commitment_deadline = float(source_observed) + float(
                        time_to_divergence
                    )
                    deadline_source = (
                        "FROZEN_DECISION_WINDOW_TIME_TO_DIVERGENCE_LOWER_BOUND"
                    )
            self._method_v2_frozen_commitment_deadline_monotonic = (
                None
                if commitment_deadline is None
                else float(commitment_deadline)
            )
            self._method_v2_timing.update(
                {
                    "ambiguity_first_observed_frame_id": (
                        getattr(self._candidate_set, "source_frame_id", None)
                    ),
                    "question_issued_monotonic_s": now,
                    "question_issued_frame_id": self._latest_frame,
                    "question_issued_simulation_time_s": (
                        self._latest_simulation_time
                    ),
                    "frozen_commitment_deadline_monotonic_s": (
                        self._method_v2_frozen_commitment_deadline_monotonic
                    ),
                    "commitment_deadline_source": deadline_source,
                    "T_available_s": (
                        None
                        if commitment_deadline is None
                        else float(commitment_deadline) - now
                    ),
                    "T_safety_margin_s": float(
                        self._phase_b_contract["m2b_m3_authority_upper_bound_s"]
                    ),
                }
            )
        answer_deadline = (
            self._method_v2_7_semantic_deadline_monotonic
            if v27_enabled
            else getattr(
                window,
                "answer_deadline_monotonic",
                window.latest_safe_clarification_monotonic,
            )
        )
        if answer_deadline is None:
            raise RuntimeError("PERSISTENT_ASK_DEADLINE_UNKNOWN")
        duration = float(answer_deadline) - now
        if duration <= 0.0 and not v27_enabled:
            raise RuntimeError("PERSISTENT_ASK_DEADLINE_CROSSED")
        episode = self.persistent_store.get(self._episode_id)
        candidate_set_id = episode.candidate_set_id
        ready_payload = {
            "candidate_set_id": candidate_set_id,
            "candidate_freshness": "FRESH",
            "answer_deadline_monotonic": float(
                answer_deadline
            ),
            "decision_deadline_monotonic": float(
                answer_deadline
                if v27_enabled
                else window.latest_safe_clarification_monotonic
            ),
            "act_evidence_grade": M3EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value,
            "holding_evidence_grade": M3EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value,
            "lease": None,
            "answer_present": False,
            "baseline_authority_eligible": True,
            "physical_mode_ready": False,
            "reason_code_authorizes_control": False,
            "model_forward_requested": False,
            "low_level_control_requested": False,
        }
        self._reduce_m3(
            MinimalM3Event.create(
                event_id=candidate_set_id + ":ready",
                event_type=EventType.CANDIDATES_READY,
                query_episode_id=None,
                source_component=RUNTIME_VERSION,
                observed_monotonic_time=now,
                source_simulation_time=self._latest_simulation_time,
                payload=ready_payload,
            )
        )
        self._reduce_m3(
            _m3_event(
                event_id=query_id + ":ask",
                event_type=EventType.DECISION_ASK,
                now=now,
                query_id=query_id,
                simulation_time=self._latest_simulation_time,
            )
        )
        entered = False
        if not v27_enabled:
            binding = build_bounded_wait_pilot_binding(
                run_id=candidate_set_id,
                source_observation_id=str(self._latest_observation_id),
                source_frame_id=str(self._latest_frame),
                candidate_set_id=candidate_set_id,
                start_monotonic_time=now,
                duration_s=duration,
                source_simulation_time=self._latest_simulation_time,
                query_episode_id=query_id,
            )
            entered = self.wait.enter(
                binding,
                current_monotonic_time=now,
                frame=self._latest_frame,
                ego_position=self._latest_position,
                ego_speed_mps=self._latest_speed,
                baseline_control_path_healthy=True,
                no_control_ownership_conflict=True,
                native_carla_session_available=True,
                source_identity_recordable=True,
                environment_digest=self._runtime_environment_digest,
            )
            if not entered:
                raise RuntimeError("PERSISTENT_ASK_HOLDING_ENTRY_FAILED")
        if getattr(self, "_method_v2_1_enabled", False):
            gate = self._resolve_hard_gate_evidence(
                decision_point="METHOD_V2_1_INFORMATION_ACTION_AT_ASK"
            )
            ask_safety, ask_motion_rule = gate.authority_gates(
                self._latest_frame
            )
            self._method_v2_timing.update(
                {
                    "method_v2_1_first_clarification_eligible_frame_id": (
                        self._latest_frame
                    ),
                    "method_v2_1_first_clarification_eligible_monotonic_s": now,
                    "method_v2_1_ask_frame_id": self._latest_frame,
                    "method_v2_1_ask_monotonic_s": now,
                    "method_v2_1_hold_start_frame_id": self._latest_frame,
                    "method_v2_1_hold_start_monotonic_s": now,
                }
            )
            if ask_motion_rule is not True:
                self._method_v2_timing.update(
                    {
                        "method_v2_1_first_motion_blocked_frame_id": (
                            self._latest_frame
                        ),
                        "method_v2_1_first_motion_blocked_monotonic_s": now,
                    }
                )
            self._receipt["method_v2_1_ask_authority"] = {
                "information_action": "ASK",
                "motion_execution_eligible": bool(
                    ask_safety is True and ask_motion_rule is True
                ),
                "physical_safety_gate": ask_safety,
                "motion_hard_rule_gate": ask_motion_rule,
                "holding_entered": entered,
                "ask_grants_vehicle_motion_authority": False,
            }
        self._query_started_sim_time = self._latest_simulation_time
        self._query_started_frame = self._latest_frame
        self.persistent_store = self.persistent_store.apply(
            StoreEvent(
                episode_id=self._episode_id,
                event_id=query_id + ":issued",
                event_type="ASK_ISSUED",
                observed_monotonic_time=time.monotonic(),
                source_frame_id=self._latest_frame,
                reason_code="M2B_ASK_ALL_GATES_VERIFIED",
                payload={
                    "query_id": query_id,
                    "candidate_set_id": candidate_set_id,
                    "answer_deadline_monotonic": float(
                        answer_deadline
                    ),
                    "question_digest": canonical_sha256(question),
                    "lifecycle_owner": (
                        "SEMANTIC_COMMITMENT_V2_7"
                        if v27_enabled else "WALL_DEADLINE_V1"
                    ),
                },
            )
        )
        self._active_persistent_query_id = query_id
        self._receipt.update(
            {
                "status": (
                    "METHOD_V2_7_ASK_QUERY_EMITTED_BASELINE_CONTINUES"
                    if v27_enabled
                    else "PERSISTENT_ASK_QUERY_EMITTED_HOLDING"
                ),
                "persistent_query_id": query_id,
                "persistent_query_candidate_set_id": candidate_set_id,
                "persistent_query_deadline_monotonic": (
                    answer_deadline
                ),
                "question": question,
                "question_digest": canonical_sha256(question),
                "old_refresh_bundle_invalid_after_answer": True,
                "control_owner_during_ask": "BASELINE_SIMLINGO",
                "ask_grants_vehicle_motion_authority": False,
                "query_lifecycle_owner": (
                    "SEMANTIC_COMMITMENT_V2_7"
                    if v27_enabled else "WALL_DEADLINE_V1"
                ),
            }
        )

    def _enter_method_v2_7_wait(self, window: Any) -> None:
        """Enter the existing holding owner only for an already-active query."""

        if not getattr(self, "_method_v2_7_enabled", False):
            raise RuntimeError("METHOD_V2_7_WAIT_FEATURE_DISABLED")
        if self._episode_id is None:
            raise RuntimeError("METHOD_V2_7_WAIT_EPISODE_MISSING")
        episode = self.persistent_store.get(self._episode_id)
        if episode.active_query is None:
            raise RuntimeError("METHOD_V2_7_WAIT_REQUIRES_ACTIVE_QUERY")
        if self._method_v2_7_continuation is None or (
            self._method_v2_7_continuation.state
            is not ContinuationStateV27.COMMITMENT_IMMINENT
        ):
            raise RuntimeError("METHOD_V2_7_WAIT_REQUIRES_IMMINENT_COMMITMENT")
        if self._method_v2_7_continuation.lawful_holding_available is not True:
            raise RuntimeError("METHOD_V2_7_WAIT_LAWFUL_HOLDING_UNAVAILABLE")
        if not self.wait.active:
            now = time.monotonic()
            binding = build_bounded_wait_pilot_binding(
                run_id=episode.candidate_set_id,
                source_observation_id=str(self._latest_observation_id),
                source_frame_id=str(self._latest_frame),
                candidate_set_id=episode.candidate_set_id,
                start_monotonic_time=now,
                duration_s=METHOD_V2_4_FROZEN_EXECUTION_ALLOWANCE_S,
                source_simulation_time=self._latest_simulation_time,
                query_episode_id=episode.active_query.query_id,
            )
            entered = self.wait.enter(
                binding,
                current_monotonic_time=now,
                frame=self._latest_frame,
                ego_position=self._latest_position,
                ego_speed_mps=self._latest_speed,
                baseline_control_path_healthy=True,
                no_control_ownership_conflict=True,
                native_carla_session_available=True,
                source_identity_recordable=True,
                environment_digest=self._runtime_environment_digest,
            )
            if not entered:
                raise RuntimeError("METHOD_V2_7_WAIT_EXISTING_HOLDING_ENTRY_FAILED")
            # The existing bounded-wait binding already carries the verified
            # M3 DECISION_WAIT transition and lease. Adopt that state so the
            # later answer is evaluated against the holding lease rather than
            # the superseded pre-WAIT diagnostic deadline.
            self._m3_state = binding.state
        self._receipt.update(
            {
                "status": "METHOD_V2_7_WAIT_BEFORE_UNRESOLVED_COMMITMENT",
                "query_active": True,
                "answer_pending": True,
                "wait_entered_after_ask": True,
                "wait_control_owner": "BASELINE_SIMLINGO_CURRENT_VALID_PLAN",
                "new_controller_count": 0,
                "new_pid_count": 0,
                "direct_vehicle_control_write_count": 0,
            }
        )

    def _build_question(self, phrase: str) -> str:
        semantic = self._semantic_query_candidates()
        if semantic is not None:
            obligation_type, rows = semantic
            options = [str(row[2]).casefold() for row in rows]
            if obligation_type == "MANEUVER_DIRECTION":
                question = "For '{}', do you mean turn {} or turn {}?".format(
                    self.raw_instruction, *options
                )
            else:
                question = (
                    "For '{}', do you mean the {} or {} qualifying execution "
                    "location?"
                ).format(self.raw_instruction, *options)
            self._receipt["semantic_question_contract"] = {
                "obligation_type": obligation_type,
                "candidate_ids": [str(row[1].get("candidate_id")) for row in rows],
                "semantic_options": [str(row[2]) for row in rows],
                "question_derived_from_candidate_semantics": True,
                "expected_decision_label_used": False,
                "question_text_used_as_policy_input": False,
            }
            return question
        del phrase
        rows = sorted(
            self._bound_candidates,
            key=lambda row: int(row.get("route_order_index", 0)),
        )[:2]
        if len(rows) != 2:
            raise RuntimeError("PERSISTENT_QUERY_REQUIRES_TWO_RUNTIME_CANDIDATES")
        descriptions = [str(row.get("referring_expression", "")).strip() for row in rows]
        if not all(descriptions) or len({value.casefold() for value in descriptions}) != 2:
            raise RuntimeError("PERSISTENT_QUERY_CANDIDATES_NOT_PASSENGER_DISTINGUISHABLE")
        return "Which do you mean: {} or {}?".format(*descriptions)

    def _semantic_query_candidates(
        self,
        active_candidate_ids: Optional[Sequence[str]] = None,
    ) -> Optional[tuple[str, list[tuple[int, Mapping[str, Any], str]]]]:
        """Return two already-derived semantic choices, or the legacy sentinel.

        This is an interaction adapter only.  It neither derives K nor changes
        an interpretation: it reads the single R4.4 language-authority fields
        already stamped on active candidate rows.
        """

        authority = self._receipt.get("r4_4_semantic_authority") or {}
        obligation_type = str(authority.get("obligation_type") or "")
        if obligation_type not in {"MANEUVER_DIRECTION", "EXECUTION_LOCATION"}:
            return None
        active = None if active_candidate_ids is None else {
            str(value) for value in active_candidate_ids
        }
        rows: list[tuple[int, Mapping[str, Any], str]] = []
        for index, row in enumerate(self._bound_candidates):
            candidate_id = str(row.get("candidate_id") or "")
            if active is not None and candidate_id not in active:
                continue
            row_type = str(row.get("r4_4_obligation_type") or "")
            constraint = str(row.get("r4_4_semantic_constraint") or "").upper()
            if row_type != obligation_type or not constraint:
                return None
            rows.append((index, row, constraint))
        if len(rows) != 2 or len({row[2] for row in rows}) != 2:
            return None
        return obligation_type, rows

    def _passenger_resolvable_candidates(
        self, active_candidate_ids: Sequence[str]
    ) -> bool:
        semantic = self._semantic_query_candidates(active_candidate_ids)
        if semantic is not None:
            obligation_type, rows = semantic
            admissible = (
                {"LEFT", "RIGHT", "STRAIGHT"}
                if obligation_type == "MANEUVER_DIRECTION"
                else {"FIRST", "SECOND", "THIRD", "NEAREST", "FARTHEST"}
            )
            return all(row[2] in admissible for row in rows)
        descriptions = {
            str(row.get("referring_expression", "")).strip().casefold()
            for row in self._bound_candidates
            if row.get("candidate_id") in active_candidate_ids
        }
        return bool(
            len(descriptions) == len(active_candidate_ids)
            and len(active_candidate_ids) >= 2
            and all(descriptions)
        )

    def _unique_authorization_safety_gate(
        self, observation: RuntimeWindowObservation
    ) -> bool:
        """Select the safety evidence owned by the active decision architecture.

        V3 deliberately separates current physical safety from the legacy V2
        dynamic gate, which also contains K>=2 connector-safety evidence.  A
        valid answer has already reduced the semantic set to one candidate, so
        its unique-plan authorization must consume the same current-physical
        gate used by V3 before the question.  The V2 path remains unchanged.
        """

        if getattr(self, "_decision_evidence_v3_enabled", False):
            return bool(observation.current_physical_safety_gate)
        return bool(observation.dynamic_safety_gate)

    def _resolve_answer_index(self) -> int:
        semantic = self._semantic_query_candidates()
        if semantic is None:
            return super()._resolve_answer_index()
        obligation_type, rows = semantic
        tokens = set(re.findall(r"[a-z]+", self.answer_text.casefold()))
        aliases = {
            "LEFT": {"left"},
            "RIGHT": {"right"},
            "STRAIGHT": {"straight", "ahead"},
            "FIRST": {"first"},
            "SECOND": {"second"},
            "THIRD": {"third"},
            "NEAREST": {"nearest", "first"},
            "FARTHEST": {"farthest", "last"},
        }
        matched = [row for row in rows if tokens.intersection(aliases.get(row[2], set()))]
        if len(matched) != 1:
            raise RuntimeError("LABEL_FREE_ANSWER_DID_NOT_RESOLVE_SEMANTIC_CANDIDATE")
        index, row, constraint = matched[0]
        self._receipt["semantic_answer_contract"] = {
            "obligation_type": obligation_type,
            "resolved_candidate_id": str(row.get("candidate_id")),
            "resolved_semantic_constraint": constraint,
            "answer_digest": canonical_sha256(self.answer_text),
            "answer_label_exposed_to_policy": False,
            "expected_decision_label_used": False,
        }
        return index

    def _reduce_m3(self, event: MinimalM3Event) -> None:
        """Carry V2.8 semantic relevance into the legacy M3 transition."""

        if (
            getattr(self, "_method_v2_8_enabled", False)
            and event.event_type is EventType.ANSWER_ARRIVED
        ):
            episode = (
                None
                if self._episode_id is None
                else self.persistent_store.get(self._episode_id)
            )
            query = None if episode is None else episode.active_query
            relevance_valid = bool(
                query is not None
                and getattr(query, "lifecycle_owner", "")
                == "SEMANTIC_COMMITMENT_V2_7"
                and query.query_id == event.query_episode_id
                and self._active_persistent_query_id == event.query_episode_id
                and self._method_v2_7_continuation is not None
                and self._method_v2_7_continuation.state
                is not ContinuationStateV27.COMMITMENT_CROSSED
            )
            payload = dict(event.payload)
            payload.update(
                {
                    "answer_expiry_owner": (
                        "V2_8_SEMANTIC_QUERY_RELEVANCE"
                    ),
                    "semantic_query_relevance_valid": relevance_valid,
                }
            )
            event = MinimalM3Event.create(
                event_id=event.event_id,
                event_type=event.event_type,
                query_episode_id=event.query_episode_id,
                source_component=event.source_component,
                observed_monotonic_time=event.observed_monotonic_time,
                source_simulation_time=event.source_simulation_time,
                calendar_utc=event.calendar_utc,
                payload=payload,
                concurrent_group_id=event.concurrent_group_id,
            )
            self._receipt["method_v2_8_semantic_answer_expiry"] = {
                "owner": "V2_8_SEMANTIC_QUERY_RELEVANCE",
                "semantic_query_relevance_valid": relevance_valid,
                "wall_deadline_extended": False,
                "query_episode_id": event.query_episode_id,
            }
        super()._reduce_m3(event)

    def _poll_answer(self) -> None:
        if self._episode_id is None or self._active_persistent_query_id is None:
            super()._poll_answer()
            return
        episode = self.persistent_store.get(self._episode_id)
        query = episode.active_query
        if query is None:
            return
        now = time.monotonic()
        semantic_lifecycle = bool(
            getattr(self, "_method_v2_7_enabled", False)
            and getattr(query, "lifecycle_owner", "")
            == "SEMANTIC_COMMITMENT_V2_7"
        )
        semantic_relevance_lost = bool(
            semantic_lifecycle
            and not self.wait.active
            and self._method_v2_7_continuation is not None
            and self._method_v2_7_continuation.state
            is ContinuationStateV27.COMMITMENT_CROSSED
        )
        if (
            (not semantic_lifecycle and now > query.answer_deadline_monotonic)
            or semantic_relevance_lost
        ):
            self._reduce_m3(
                _m3_event(
                    event_id=query.query_id + ":m3-timeout",
                    event_type=EventType.QUERY_TIMEOUT,
                    now=now,
                    query_id=query.query_id,
                    simulation_time=self._latest_simulation_time,
                )
            )
            self.wait.observe_tick(
                frame=self._latest_frame,
                current_monotonic_time=now,
                ego_position=self._latest_position,
                ego_speed_mps=self._latest_speed,
                baseline_control_path_healthy=True,
                source_runtime_valid=False,
                environment_digest=self._runtime_environment_digest,
                source_simulation_time=self._latest_simulation_time,
            )
            self.persistent_store = self.persistent_store.apply(
                StoreEvent(
                    episode_id=self._episode_id,
                    event_id=query.query_id + ":timeout",
                    event_type="QUERY_TIMEOUT",
                    observed_monotonic_time=now,
                    source_frame_id=self._latest_frame,
                    reason_code=(
                        "UNRESOLVED_SEMANTIC_COMMITMENT_CROSSED"
                        if semantic_lifecycle else "ANSWER_TOO_LATE"
                    ),
                    payload={},
                )
            )
            self._receipt.update(
                {
                    "status": "PERSISTENT_QUERY_TIMED_OUT_UNRESOLVED",
                    "semantic_state": "UNRESOLVED",
                    "late_answer_default_candidate_selected": False,
                    "m3_query_active": bool(self._m3_state.query_active),
                    "holding_active": self.wait.active,
                }
            )
            self._query_started_sim_time = None
            self._active_persistent_query_id = None
            return
        before = self._answer_received_sim_time
        super()._poll_answer()
        if before is None and self._answer_received_sim_time is not None:
            if getattr(self, "_method_revision_v2_enabled", False):
                answer_now = time.monotonic()
                question_now = self._method_v2_timing.get(
                    "question_issued_monotonic_s"
                )
                self._method_v2_timing.update(
                    {
                        "answer_received_monotonic_s": answer_now,
                        "answer_received_frame_id": self._latest_frame,
                        "answer_received_simulation_time_s": (
                            self._answer_received_sim_time
                        ),
                        "T_answer_s": (
                            None
                            if question_now is None
                            else answer_now - float(question_now)
                        ),
                    }
                )
            semantic = self._semantic_query_candidates()
            if semantic is not None and self._resolved_index is not None:
                _obligation_type, rows = semantic
                resolved = next(
                    (row for row in rows if row[0] == self._resolved_index), None
                )
                if resolved is not None:
                    self._receipt["answer_resolution"] = resolved[2]
                    self._receipt["answer_resolved_candidate_id"] = str(
                        resolved[1].get("candidate_id")
                    )
            old_bundle = getattr(self, "_latest_bundle", None)
            old_bundle_id = (
                None if old_bundle is None else str(old_bundle.bundle_id)
            )
            # Match the convergence path's real invalidation semantics.  A
            # receipt flag alone must never leave stale bundle/window objects
            # available to the answer-driven latest-observation replan.
            self._latest_bundle = None
            self._latest_window = None
            self.persistent_store = self.persistent_store.apply(
                StoreEvent(
                    episode_id=self._episode_id,
                    event_id=query.query_id + ":answer-valid",
                    event_type="ANSWER_ARRIVED_VALID",
                    observed_monotonic_time=time.monotonic(),
                    source_frame_id=self._latest_frame,
                    reason_code="MATCHED_ANSWER_OLD_PLAN_INVALIDATED",
                    payload={
                        "query_id": query.query_id,
                        "answer_digest": canonical_sha256(self.answer_text),
                    },
                )
            )
            self._receipt.update(
                {
                    "persistent_answer_matched_active_query": True,
                    "persistent_old_bundle_invalidated_before_replan": True,
                    "persistent_invalidated_bundle_id": old_bundle_id,
                    "persistent_old_bundle_object_cleared": True,
                    "persistent_old_window_object_cleared": True,
                    "semantic_state": "UNRESOLVED",
                }
            )

    def _answer_query_identity(self) -> Any:
        """Use the store-owned active query across intervening WAIT decisions."""

        if self._episode_id is not None:
            episode = self.persistent_store.get(self._episode_id)
            if episode.active_query is not None:
                return episode.active_query.query_id
        return super()._answer_query_identity()

    def _run_convergence_fresh_replan(self) -> None:
        assert (
            self._episode_id is not None
            and self._convergence_replan_pending
            and self._convergence_evidence is not None
            and self._convergence_selected_candidate_id is not None
            and self._candidate_set is not None
            and self._resolved_index is not None
            and self._latest_observation_id is not None
            and self._runtime_route_version is not None
            and self._runtime_environment_digest is not None
        )
        episode_before = self.persistent_store.get(self._episode_id)
        if episode_before.active_candidate_ids != (
            self._convergence_selected_candidate_id,
        ):
            raise RuntimeError("CONVERGENCE_SURVIVOR_IDENTITY_CHANGED")
        candidate = self._candidate_set.candidates[self._resolved_index]
        if candidate.candidate_id != self._convergence_selected_candidate_id:
            raise RuntimeError("CONVERGENCE_SURVIVOR_CANDIDATE_MISMATCH")
        fresh_local_replan_request = (
            self._prepare_candidate_local_fresh_replan_request(
                candidate,
                old_bundle_invalidation_proven=bool(
                    self._receipt.get("convergence_old_bundle_invalidated")
                ),
            )
        )
        # Only R4.4 navigation mode produces a request; the default-off path
        # keeps the original forward signature untouched.
        result = self._forward(
            candidate,
            "PERSISTENT-CONVERGED",
            latest=True,
            **(
                {"fresh_local_replan_request": fresh_local_replan_request}
                if fresh_local_replan_request is not None
                else {}
            ),
        )
        receipt = self._plan_receipt(result, "PERSISTENT-CONVERGED")
        now = time.monotonic()
        forward_evidence = receipt.get("forward_evidence", {})
        latest_source_aligned = bool(
            str(forward_evidence.get("source_observation_id"))
            == str(self._latest_observation_id)
            and str(forward_evidence.get("source_frame_id"))
            == str(self._latest_frame)
        )
        fresh_model_computation = bool(
            forward_evidence.get("fresh_candidate_conditioned_model_execution")
            is True
        )
        unique_observation = self._runtime_window_observation(now)
        unique_context = ConvergedUniqueDecisionContext(
            semantic_state=episode_before.semantic_state.value,
            active_candidate_count=len(episode_before.active_candidate_ids),
            convergence_evidence_valid=True,
            old_bundle_invalidated=bool(
                self._receipt.get("convergence_old_bundle_invalidated")
            ),
            latest_observation_replan=(
                latest_source_aligned and fresh_model_computation
            ),
            candidate_fresh=fresh_model_computation,
            source_identity_aligned=latest_source_aligned,
            hard_safety_gate=self._unique_authorization_safety_gate(
                unique_observation
            ),
            hard_rule_gate=unique_observation.hard_rule_gate,
            active_query=False,
            active_holding_lease=self.wait.active,
        )
        recommendation = decide_unique_after_convergence(unique_context)
        if recommendation.decision is not PersistentDecision.ACT:
            raise RuntimeError("CONVERGENCE_UNIQUE_M2B_FAIL_CLOSED")
        fresh_set_id = "persistent-converged-set-" + canonical_sha256(
            {
                "episode": self._episode_id,
                "evidence": self._convergence_evidence.evidence_digest,
                "observation": self._latest_observation_id,
                "frame": self._latest_frame,
                "candidate": candidate.candidate_id,
                "plan": canonical_sha256(receipt),
            }
        )[:20]
        m3_receipt, m3_act_result = build_method_m3_transaction(
            label=MethodDecisionLabel.ACT,
            candidate_set_id=fresh_set_id,
            observed_monotonic_time=now,
        )
        armed = self.shared_act.arm_unique(
            candidate_set_id=fresh_set_id,
            candidate_id=candidate.candidate_id,
            resolved_interpretation_id=candidate.interpretation_id,
            route=result.raw_route,
            speed=result.raw_speed,
            source_observation_id=str(self._latest_observation_id),
            source_frame_id=str(self._latest_frame),
            route_version=self._runtime_route_version,
            environment_digest=self._runtime_environment_digest,
            current_monotonic=now,
            valid_until_monotonic=now
            + float(self._phase_b_contract["full_bundle_wall_timeout_s"]),
            existing_m3_act_result=m3_act_result,
        )
        if not armed:
            raise RuntimeError("CONVERGENCE_UNIQUE_AUTHORITY_ARM_FAILED")
        try:
            self.persistent_store = self.persistent_store.apply(
                StoreEvent(
                    episode_id=self._episode_id,
                    event_id=fresh_set_id + ":unique-from-evidence",
                    event_type="LATEST_REPLAN_UNIQUE_FROM_EVIDENCE",
                    observed_monotonic_time=now,
                    source_frame_id=self._latest_frame,
                    reason_code="EVIDENCE_CONVERGENCE_FRESH_UNIQUE_REPLAN",
                    payload={
                        "selected_candidate_id": candidate.candidate_id,
                        "candidate_set_id": fresh_set_id,
                        "source_observation_id": self._latest_observation_id,
                        "freshness_deadline_monotonic": now
                        + float(
                            self._phase_b_contract[
                                "full_bundle_wall_timeout_s"
                            ]
                        ),
                        "fresh_replan_digest": canonical_sha256(receipt),
                        "normal_forward_count": 1,
                        "candidate_forward_requested_count": 1,
                        "candidate_forward_attempted_count": 1,
                        "candidate_forward_count": 1,
                        "candidate_forward_succeeded_count": 1,
                    },
                )
            )
        except Exception:
            self.shared_act.revoke(AuthorityReason.CANDIDATE_INVALIDATED)
            raise
        self._method_m3_transactions.append(m3_receipt)
        self._m3_act_transactions.append(
            {
                "decision_source": "METHOD_V1_ACT_AFTER_EVIDENCE_CONVERGENCE",
                "candidate_set_id": fresh_set_id,
                "method_m3_receipt": m3_receipt,
            }
        )
        self._fresh_forward = result
        self._latest_unique_observation = unique_observation
        self._replan_pending = False
        self._convergence_replan_pending = False
        self._receipt["convergence_replan_pending"] = False
        self._authority_mode = "UNIQUE"
        self._decision = _PersistentDecisionView(MethodDecisionLabel.ACT.value)
        envelope = MethodDecisionEnvelope.create(
            decision_label=MethodDecisionLabel.ACT,
            decision_reason="ACT_AFTER_CONVERGENCE",
            ambiguity_state="RESOLVED_AFTER_CONVERGENCE",
            effective_K=1,
            relationship="UNIQUE_AFTER_RUNTIME_EVIDENCE",
            decision_subject="FRESH_UNIQUE_CANDIDATE_PLAN",
            control_source=MethodControlSource.FRESH_UNIQUE_CANDIDATE,
            authorization_status=MethodAuthorizationStatus.AUTHORIZED,
            freshness="FRESH_LATEST_OBSERVATION_REPLAN",
            source_planning_event=fresh_set_id + ":normal-planning-event",
            candidate_bundle_version=fresh_set_id,
            authority_subject="UNIQUE_CANDIDATE",
        )
        self._set_method_decision(envelope)
        self._receipt.update(
            {
                "status": "METHOD_V1_CONVERGENCE_FRESH_ACT_AUTHORITY_ARMED",
                "convergence_fresh_replan": receipt,
                "convergence_fresh_replan_source_observation_id": (
                    self._latest_observation_id
                ),
                "convergence_fresh_replan_source_frame_id": self._latest_frame,
                "convergence_fresh_bundle_version": fresh_set_id,
                "convergence_old_and_fresh_bundle_distinct": (
                    self._convergence_old_bundle_version != fresh_set_id
                ),
                "convergence_fresh_unique_forward_count": 1,
                "hidden_convergence_forward_count": 0,
                "convergence_m2b_recommendation": {
                    "decision": recommendation.decision.value,
                    "paper_decision": MethodDecisionLabel.ACT.value,
                    "authority_subject_type": (
                        recommendation.authority_subject_type
                    ),
                    "reason_codes": list(recommendation.reason_codes),
                },
                "method_v1_m3_transactions": list(
                    self._method_m3_transactions
                ),
                "semantic_state": "RESOLVED",
            }
        )
        self._refresh_method_dashboard(plan_id=fresh_set_id)
        self._repaint_method_dashboard()

    def _fail_convergence_replan(self, error: Exception) -> None:
        now = time.monotonic()
        if self._episode_id is not None:
            episode = self.persistent_store.get(self._episode_id)
            if episode.evidence_state is EvidenceState.REFRESH_REQUIRED:
                self.persistent_store = self.persistent_store.apply(
                    StoreEvent(
                        episode_id=self._episode_id,
                        event_id="{}:convergence-replan-failed:{}".format(
                            self._episode_id, self._latest_frame
                        ),
                        event_type="LATEST_REPLAN_FAILED",
                        observed_monotonic_time=now,
                        source_frame_id=self._latest_frame,
                        reason_code="FRESH_REPLAN_FAILED",
                        payload={},
                    )
                )
        self.shared_act.revoke(AuthorityReason.CANDIDATE_INVALIDATED)
        fallback_set_id = "convergence-fallback-" + canonical_sha256(
            [self._episode_id, self._latest_observation_id, self._latest_frame, str(error)]
        )[:20]
        m3_receipt = build_method_m3_receipt(
            label=MethodDecisionLabel.FALLBACK,
            candidate_set_id=fallback_set_id,
            observed_monotonic_time=now,
        )
        self._method_m3_transactions.append(m3_receipt)
        effective_k = 0
        if self._episode_id is not None:
            effective_k = len(
                self.persistent_store.get(self._episode_id).active_candidate_ids
            )
        self._set_method_decision(
            MethodDecisionEnvelope.create(
                decision_label=MethodDecisionLabel.FALLBACK,
                decision_reason="FRESH_REPLAN_FAILED",
                ambiguity_state="CONVERGENCE_REPLAN_FAILED",
                effective_K=effective_k,
                relationship="UNIQUE_AFTER_RUNTIME_EVIDENCE",
                decision_subject="BASELINE_SIMLINGO_PLAN",
                control_source=MethodControlSource.BASELINE_SIMLINGO,
                authorization_status=MethodAuthorizationStatus.FAIL_CLOSED,
                freshness="INVALID",
                source_planning_event=fallback_set_id,
                candidate_bundle_version=None,
                authority_subject="BASELINE_CONTROL",
            )
        )
        self._decision = _PersistentDecisionView("FALLBACK")
        self._fresh_forward = None
        self._replan_pending = False
        self._convergence_replan_pending = False
        self._receipt["convergence_replan_pending"] = False
        self._authority_mode = None
        self._errors.append(
            {
                "stage": "METHOD_V1_CONVERGENCE_FRESH_REPLAN",
                "frame": self._latest_frame,
                "type": type(error).__name__,
                "message": str(error),
            }
        )
        self._receipt.update(
            {
                "status": "METHOD_V1_CONVERGENCE_REPLAN_FAILED_FALLBACK",
                "convergence_stale_plan_selected": False,
                "method_v1_m3_transactions": list(
                    self._method_m3_transactions
                ),
            }
        )
        self._terminal = True

    def _method_v2_1_stage_fresh_plan(
        self,
        *,
        candidate: Any,
        result: Any,
        receipt: Mapping[str, Any],
        fresh_set_id: str,
        unique_context: UniqueDecisionContext,
        unique_observation: RuntimeWindowObservation,
        ready_monotonic: float,
    ) -> bool:
        """Stage a fresh plan without granting selected motion authority."""

        if not getattr(self, "_method_v2_1_enabled", False):
            return False
        non_motion_gates = (
            unique_context.semantic_state
            == "ANSWER_RECEIVED_PENDING_LATEST_REPLAN",
            unique_context.active_candidate_count == 1,
            unique_context.matched_answer,
            unique_context.old_bundle_invalidated,
            unique_context.latest_observation_replan,
            unique_context.candidate_fresh,
            unique_context.source_identity_aligned,
            unique_context.hard_safety_gate,
            not unique_context.active_query,
            not unique_context.active_holding_lease,
        )
        if not all(non_motion_gates):
            raise RuntimeError("METHOD_V2_1_PLAN_READY_NON_MOTION_GATES_FAIL_CLOSED")
        obligation = self._candidate_local_navigation_obligations.get(
            str(candidate.candidate_id)
        )
        if obligation is None:
            raise RuntimeError("METHOD_V2_1_PLAN_READY_OBLIGATION_MISSING")
        snapshot = self._method_v2_1_plan_snapshot(
            candidate,
            obligation,
            route_version=str(self._runtime_route_version),
            environment_digest=str(self._runtime_environment_digest),
        )
        if getattr(self, "_method_v2_4_enabled", False):
            # V2.4 changes what one execution second means, not how many
            # seconds the frozen V2.3 case receives.  Do not rederive a
            # hardware-speed-dependent allowance from this run's wall timing.
            execution_budget_s = METHOD_V2_4_FROZEN_EXECUTION_ALLOWANCE_S
        else:
            deadline = self._method_v2_frozen_commitment_deadline_monotonic
            if deadline is None or not math.isfinite(float(deadline)):
                raise RuntimeError("METHOD_V2_1_EXECUTION_BUDGET_SOURCE_UNKNOWN")
            execution_budget_s = float(deadline) - float(ready_monotonic)
        if execution_budget_s <= 0.0:
            raise RuntimeError("METHOD_V2_1_EXECUTION_BUDGET_NOT_POSITIVE")
        try:
            physical_baseline = self._method_v2_1_plan_ready_physical_baseline(
                obligation,
                unique_observation,
            )
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
            raise RuntimeError("METHOD_V2_1_READY_PHYSICAL_EVIDENCE_UNKNOWN") from error
        initial_motion_eligible = bool(
            unique_observation.current_physical_safety_gate is True
            and unique_observation.hard_rule_gate is True
            and self.control_enabled
            and not self.wait.active
        )
        method_v2_3 = getattr(self, "_method_v2_3_enabled", False)
        if method_v2_3:
            self._method_v2_3_pre_activation_feasibility_history = []
            self._method_v2_3_active_last_world_xy_m = None
            self._method_v2_3_activation_frame_id = None
            self._method_v2_3_active_execution_deadline_monotonic = None
        self._method_v2_1_execution_budget = (
            None
            if method_v2_3
            else ManeuverExecutionBudgetLedger(
                execution_budget_s,
                started_monotonic_s=float(ready_monotonic),
                initial_motion_execution_eligible=initial_motion_eligible,
            )
        )
        self._method_v2_1_plan_gate.stage(
            snapshot, frame_id=int(self._latest_frame)
        )
        staged_plan = {
            "candidate": candidate,
            "result": result,
            "receipt": dict(receipt),
            "fresh_set_id": fresh_set_id,
            "ready_observation": unique_observation,
            "ready_frame_id": int(self._latest_frame),
            "ready_monotonic_s": float(ready_monotonic),
            "selected_execution_budget_allowance_s": execution_budget_s,
            **physical_baseline,
        }
        if method_v2_3:
            required_v2_3 = (
                "selected_commitment_progress_m",
                "last_route_progress_m",
                "selected_junction_identity",
                "selected_target_id",
                "selected_branch_id",
                "selected_route_order_index",
                "selected_route_opportunity_index",
            )
            if any(name not in physical_baseline for name in required_v2_3):
                raise RuntimeError(
                    "METHOD_V2_3_READY_FEASIBILITY_EVIDENCE_UNKNOWN"
                )
            identity_values = tuple(
                physical_baseline[name]
                for name in (
                    "selected_junction_identity",
                    "selected_target_id",
                    "selected_branch_id",
                )
            )
            index_values = tuple(
                physical_baseline[name]
                for name in (
                    "selected_route_order_index",
                    "selected_route_opportunity_index",
                )
            )
            commitment_boundary = physical_baseline[
                "selected_commitment_progress_m"
            ]
            initial_progress = physical_baseline["last_route_progress_m"]
            if not (
                all(
                    isinstance(value, str) and bool(value.strip())
                    for value in identity_values
                )
                and all(
                    isinstance(value, int) and not isinstance(value, bool)
                    and value >= 0
                    for value in index_values
                )
                and all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    and float(value) >= 0.0
                    for value in (commitment_boundary, initial_progress)
                )
            ):
                raise RuntimeError(
                    "METHOD_V2_3_READY_FEASIBILITY_IDENTITY_OR_BOUNDARY_INVALID"
                )
            staged_plan.update(
                {
                    "frozen_selected_commitment_boundary_progress_m": (
                        commitment_boundary
                    ),
                    "minimum_observed_commitment_boundary_progress_m": (
                        commitment_boundary
                    ),
                    "max_observed_route_progress_m": physical_baseline[
                        "last_route_progress_m"
                    ],
                    "frozen_selected_junction_identity": physical_baseline[
                        "selected_junction_identity"
                    ],
                    "frozen_selected_target_id": physical_baseline[
                        "selected_target_id"
                    ],
                    "frozen_selected_branch_id": physical_baseline[
                        "selected_branch_id"
                    ],
                    "frozen_selected_route_order_index": physical_baseline[
                        "selected_route_order_index"
                    ],
                    "frozen_selected_route_opportunity_index": physical_baseline[
                        "selected_route_opportunity_index"
                    ],
                }
            )
        self._method_v2_1_staged_plan = staged_plan
        self._replan_pending = False
        self._authority_mode = None
        self._decision = _PersistentDecisionView("WAIT")
        self._method_v2_timing.update(
            {
                "method_v2_1_plan_ready_monotonic_s": float(ready_monotonic),
                "method_v2_1_plan_ready_frame_id": self._latest_frame,
                "method_v2_1_plan_ready_simulation_time_s": (
                    self._latest_simulation_time
                ),
            }
        )
        self._receipt.update(
            {
                "status": "METHOD_V2_1_SELECTED_PLAN_READY_NO_MOTION_AUTHORITY",
                "fresh_replan": dict(receipt),
                "fresh_replan_source_observation_id": self._latest_observation_id,
                "fresh_replan_source_frame_id": self._latest_frame,
                "post_answer_decision": "PLAN_READY",
                "selected_plan_ready": True,
                "selected_plan_execution_active": False,
                "selected_plan_consumed_while_motion_blocked": False,
                "plan_ready_hard_rule_gate": unique_context.hard_rule_gate,
                "plan_ready_hard_rule_gate_required": False,
                "fresh_replan_grants_motion_authority": False,
                "T_hold_max_core_method_parameter": False,
                "execution_budget_source": (
                    "FROZEN_ALLOWANCE_QUANTITY_ARMED_ONLY_AT_ACTIVE"
                    if method_v2_3
                    else "FROZEN_PRE_HOLD_COMMITMENT_ALLOWANCE_REMAINING_AT_PLAN_READY"
                ),
                "execution_budget_s": execution_budget_s,
                "execution_budget_consumes_only_when": (
                    "ACTIVE_SELECTED_MANEUVER_EXECUTION"
                    if method_v2_3
                    else "MOTION_ELIGIBLE_OR_PHYSICAL_PROGRESS_TOWARD_COMMITMENT"
                ),
                "selected_execution_budget_armed": not method_v2_3,
                "selected_execution_budget_consumed_pre_active_s": 0.0,
                "pre_activation_progress_owner": (
                    "PRE_ACTIVATION_COMMITMENT_SLACK_AND_FEASIBILITY"
                    if method_v2_3
                    else "MANEUVER_EXECUTION_OPPORTUNITY_BUDGET"
                ),
                "clarification_clock_paused_during_hold": False,
                "post_answer_selected_fresh_replan_forward_count": 1,
                "post_answer_continued_k2_forward_count": 0,
                "method_v2_1_plan_activation": (
                    self._method_v2_1_plan_gate.summary()
                ),
            }
        )
        return True

    def _run_persistent_fresh_replan(self) -> None:
        assert (
            self._episode_id is not None
            and self._resolved_index is not None
            and self._candidate_set is not None
            and self._latest_observation_id is not None
            and self._runtime_route_version is not None
            and self._runtime_environment_digest is not None
        )
        candidate = self._candidate_set.candidates[self._resolved_index]
        fresh_local_replan_request = (
            self._prepare_candidate_local_fresh_replan_request(
                candidate,
                old_bundle_invalidation_proven=bool(
                    self._receipt.get(
                        "persistent_old_bundle_invalidated_before_replan"
                    )
                ),
            )
        )
        method_v2_replan_started = time.monotonic()
        if getattr(self, "_method_revision_v2_enabled", False):
            self._method_v2_timing.update(
                {
                    "fresh_replan_started_monotonic_s": method_v2_replan_started,
                    "fresh_replan_started_frame_id": self._latest_frame,
                    "fresh_replan_started_simulation_time_s": (
                        self._latest_simulation_time
                    ),
                }
            )
        result = self._forward(
            candidate,
            "PERSISTENT-RESOLVED",
            latest=True,
            **(
                {"fresh_local_replan_request": fresh_local_replan_request}
                if fresh_local_replan_request is not None
                else {}
            ),
        )
        self._fresh_forward = result
        receipt = self._plan_receipt(result, "PERSISTENT-RESOLVED")
        now = time.monotonic()
        if getattr(self, "_method_revision_v2_enabled", False):
            self._method_v2_timing.update(
                {
                    "fresh_replan_completed_monotonic_s": now,
                    "fresh_replan_completed_frame_id": self._latest_frame,
                    "fresh_replan_completed_simulation_time_s": (
                        self._latest_simulation_time
                    ),
                    "T_fresh_replan_s": now - method_v2_replan_started,
                }
            )
        fresh_set_id = "persistent-fresh-set-" + canonical_sha256(
            {
                "episode": self._episode_id,
                "observation": self._latest_observation_id,
                "candidate": candidate.candidate_id,
            }
        )[:20]
        self._reduce_m3(
            _m3_event(
                event_id=fresh_set_id + ":revalidated",
                event_type=EventType.REVALIDATION_PASSED,
                now=now,
                query_id=None,
                simulation_time=self._latest_simulation_time,
            )
        )
        self._reduce_m3(
            _m3_event(
                event_id=fresh_set_id + ":replan",
                event_type=EventType.REPLAN_COMPLETE,
                now=now,
                query_id=None,
                simulation_time=self._latest_simulation_time,
                candidate_set_id=fresh_set_id,
                candidate_freshness="FRESH",
            )
        )
        self.persistent_store = self.persistent_store.apply(
            StoreEvent(
                episode_id=self._episode_id,
                event_id=fresh_set_id + ":unique",
                event_type="LATEST_REPLAN_UNIQUE",
                observed_monotonic_time=now,
                source_frame_id=self._latest_frame,
                reason_code="ANSWER_SELECTED_UNIQUE_LATEST_REPLAN",
                payload={
                    "selected_candidate_id": candidate.candidate_id,
                    "source_observation_id": self._latest_observation_id,
                    "freshness_deadline_monotonic": now
                    + float(self._phase_b_contract["full_bundle_wall_timeout_s"]),
                    "fresh_replan_digest": canonical_sha256(receipt),
                },
            )
        )
        unique_observation = self._runtime_window_observation(now)
        self._latest_unique_observation = unique_observation
        forward_evidence = receipt.get("forward_evidence", {})
        latest_source_aligned = bool(
            str(forward_evidence.get("source_observation_id"))
            == str(self._latest_observation_id)
            and str(forward_evidence.get("source_frame_id"))
            == str(self._latest_frame)
        )
        fresh_model_computation = bool(
            forward_evidence.get("fresh_candidate_conditioned_model_execution")
            is True
        )
        unique_context = UniqueDecisionContext(
                semantic_state="ANSWER_RECEIVED_PENDING_LATEST_REPLAN",
                active_candidate_count=1,
                matched_answer=bool(
                    self._receipt.get("persistent_answer_matched_active_query")
                ),
                old_bundle_invalidated=bool(
                    self._receipt.get("persistent_old_bundle_invalidated_before_replan")
                    and self._latest_bundle is None
                    and self._latest_window is None
                ),
                latest_observation_replan=(
                    latest_source_aligned and fresh_model_computation
                ),
                candidate_fresh=fresh_model_computation,
                source_identity_aligned=latest_source_aligned,
                hard_safety_gate=self._unique_authorization_safety_gate(
                    unique_observation
                ),
                hard_rule_gate=unique_observation.hard_rule_gate,
                active_query=False,
                active_holding_lease=self.wait.active,
            )
        if self._method_v2_1_stage_fresh_plan(
            candidate=candidate,
            result=result,
            receipt=receipt,
            fresh_set_id=fresh_set_id,
            unique_context=unique_context,
            unique_observation=unique_observation,
            ready_monotonic=now,
        ):
            return
        unique_decision = decide_unique_after_answer(unique_context)
        self._receipt["post_answer_unique_m2b_context"] = asdict(unique_context)
        self._receipt["post_answer_unique_m2b_recommendation"] = {
            "decision": unique_decision.decision.value,
            "authority_subject_type": unique_decision.authority_subject_type,
            "reason_codes": list(unique_decision.reason_codes),
        }
        self._receipt["post_answer_unique_safety_evidence_source"] = (
            "CURRENT_PHYSICAL_SAFETY_GATE_V3"
            if getattr(self, "_decision_evidence_v3_enabled", False)
            else "DYNAMIC_SAFETY_GATE_V2"
        )
        if self._decision_evidence_v2_enabled:
            self._receipt["post_answer_unique_m2b_context_v2_diagnostic"] = asdict(
                unique_context
            )
            self._receipt["post_answer_unique_m2b_recommendation_v2_diagnostic"] = {
                "decision": unique_decision.decision.value,
                "reason_codes": list(unique_decision.reason_codes),
            }
        if unique_decision.decision is not PersistentDecision.ACT_UNIQUE:
            raise RuntimeError("POST_ANSWER_UNIQUE_M2B_FAIL_CLOSED")
        m3_ready, m3_act = _build_frozen_m3_act_result(fresh_set_id, now)
        self._m3_act_transactions.append(
            {
                "decision_source": "PERSISTENT_TWO_AXIS_M2B_ACT_UNIQUE",
                "candidate_set_id": fresh_set_id,
                "ready_result": m3_ready.to_dict(),
                "accepted_act_result": m3_act.to_dict(),
                "accepted_act_digest": canonical_sha256(m3_act.to_dict()),
            }
        )
        armed = self.shared_act.arm_unique(
            candidate_set_id=fresh_set_id,
            candidate_id=candidate.candidate_id,
            resolved_interpretation_id=candidate.interpretation_id,
            route=result.raw_route,
            speed=result.raw_speed,
            source_observation_id=str(self._latest_observation_id),
            source_frame_id=str(self._latest_frame),
            route_version=self._runtime_route_version,
            environment_digest=self._runtime_environment_digest,
            current_monotonic=now,
            valid_until_monotonic=now
            + float(self._phase_b_contract["full_bundle_wall_timeout_s"]),
            existing_m3_act_result=m3_act,
        )
        if not armed:
            raise RuntimeError("UNIQUE_POST_ANSWER_AUTHORITY_ARM_FAILED")
        self._replan_pending = False
        self._authority_mode = "UNIQUE"
        self._decision = _PersistentDecisionView(MethodDecisionLabel.ACT.value)
        self._set_method_decision(
            MethodDecisionEnvelope.create(
                decision_label=MethodDecisionLabel.ACT,
                decision_reason="ACT_AFTER_PASSENGER_ANSWER",
                ambiguity_state="RESOLVED_AFTER_ANSWER",
                effective_K=1,
                relationship="UNIQUE_AFTER_PASSENGER_ANSWER",
                decision_subject="FRESH_UNIQUE_CANDIDATE_PLAN",
                control_source=MethodControlSource.FRESH_UNIQUE_CANDIDATE,
                authorization_status=MethodAuthorizationStatus.AUTHORIZED,
                freshness="FRESH_LATEST_OBSERVATION_REPLAN",
                source_planning_event=fresh_set_id + ":normal-planning-event",
                candidate_bundle_version=fresh_set_id,
                authority_subject="UNIQUE_CANDIDATE",
            )
        )
        if getattr(self, "_method_revision_v2_enabled", False):
            self._activate_method_revision_v2(candidate, unique_observation, now)
        self._receipt.update(
            {
                "status": "PERSISTENT_POST_ANSWER_UNIQUE_AUTHORITY_ARMED",
                "fresh_replan": receipt,
                "fresh_replan_source_observation_id": self._latest_observation_id,
                "fresh_replan_source_frame_id": self._latest_frame,
                "post_answer_decision": "ACT",
                "authority_subject_type": "UNIQUE_CANDIDATE",
                "post_answer_m2b_recommendation": {
                    "decision": unique_decision.decision.value,
                    "authority_subject_type": unique_decision.authority_subject_type,
                    "reason_codes": list(unique_decision.reason_codes),
                },
                "semantic_state": "RESOLVED",
                "resolution_requires_latest_fresh_replan": True,
                "post_answer_selected_fresh_replan_forward_count": 1,
                "post_answer_continued_k2_forward_count": 0,
            }
        )

    def _method_v2_selected_connector(
        self,
        obligation: CandidateLocalNavigationObligation,
        *,
        calibrated_uncertainty_m: float,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Retrace the selected obligation's connector by its own world target.

        The R4.4 candidate-set connector receipt remains immutable.  This method
        does not trust its historical ordinal pairing; it matches the selected
        obligation's qualified local target to an existing opportunity and then
        reuses the existing read-only CARLA connector tracer.
        """

        target = obligation.local_target
        if target is None:
            raise RuntimeError("METHOD_V2_SELECTED_LOCAL_TARGET_MISSING")
        opportunities = [
            row
            for row in self._receipt.get("maneuver_opportunities", ())
            if isinstance(row, Mapping)
        ]
        if not opportunities:
            raise RuntimeError("METHOD_V2_MANEUVER_OPPORTUNITIES_MISSING")
        route_rows = self.topology_enumerator._route_rows(self._route())

        def target_distance(row: Mapping[str, Any]) -> float:
            anchor = row.get("anchor_xy")
            try:
                return math.hypot(
                    float(anchor[0]) - float(target.x_m),
                    float(anchor[1]) - float(target.y_m),
                )
            except (AttributeError, IndexError, TypeError, ValueError):
                return math.inf

        uncertainty = float(calibrated_uncertainty_m)
        if not math.isfinite(uncertainty) or uncertainty < 0.0:
            raise RuntimeError("METHOD_V2_SELECTED_OPPORTUNITY_UNCERTAINTY_UNKNOWN")
        matches = [
            (target_distance(row), row)
            for row in opportunities
            if math.isfinite(target_distance(row))
            and target_distance(row) <= uncertainty
        ]
        if (
            len(matches) == 0
            and getattr(self, "_method_v2_7_enabled", False)
            and str(obligation.maneuver_family) == "MANEUVER_DIRECTION"
        ):
            semantic_rows = [
                row for row in self._bound_candidates
                if str(row.get("candidate_id")) == str(obligation.candidate_id)
            ]
            if len(semantic_rows) != 1:
                raise RuntimeError("METHOD_V2_7_MD_SEMANTIC_OWNER_CARDINALITY_INVALID")
            junction_identity = str(
                semantic_rows[0].get("topology_junction_id")
                or semantic_rows[0].get("junction_id")
                or ""
            )
            same_junction = [
                row for row in opportunities
                if str(row.get("junction_id") or "") == junction_identity
            ]
            if len(same_junction) != 1:
                raise RuntimeError("METHOD_V2_7_MD_JUNCTION_OWNER_CARDINALITY_INVALID")
            route_index = int(same_junction[0].get("route_opportunity_index", -1))
            if route_index < 0 or route_index >= len(route_rows):
                raise RuntimeError("METHOD_V2_7_MD_ROUTE_INDEX_INVALID")
            try:
                from driveclarify_scene_grounded_obligation_supplier.scene_grounding import (  # noqa: PLC0415
                    read_direction_branch_evidence,
                )

                branch_evidence = read_direction_branch_evidence(
                    _live_map(), route_rows, route_index
                )
            except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as error:
                raise RuntimeError(
                    "METHOD_V2_7_MD_LIVE_JUNCTION_TOPOLOGY_UNKNOWN"
                ) from error
            selected_groups = [
                group
                for group in branch_evidence.get("semantic_branch_groups", ())
                if str(group.get("branch_identity"))
                == str(obligation.local_branch_identity)
            ]
            if len(selected_groups) != 1:
                raise RuntimeError("METHOD_V2_7_MD_SELECTED_EXIT_CARDINALITY_INVALID")
            representative = selected_groups[0].get("representative") or {}
            selected_exit_xy = representative.get("exit_xy") or ()
            try:
                selected_exit_distance = math.hypot(
                    float(selected_exit_xy[0]) - float(target.x_m),
                    float(selected_exit_xy[1]) - float(target.y_m),
                )
            except (IndexError, TypeError, ValueError) as error:
                raise RuntimeError(
                    "METHOD_V2_7_MD_SELECTED_EXIT_GEOMETRY_UNKNOWN"
                ) from error
            if selected_exit_distance > uncertainty:
                raise RuntimeError("METHOD_V2_7_MD_SELECTED_EXIT_TARGET_MISMATCH")
            # Direction alternatives share the same upcoming junction event,
            # but their selected exit anchor comes from the qualified current
            # semantic obligation. Rebind the existing live-map junction pair
            # without a new route search or planner.
            semantic_opportunity = dict(same_junction[0])
            semantic_opportunity.update(
                {
                    "anchor_xy": [float(target.x_m), float(target.y_m)],
                    "branch_id": obligation.local_branch_identity,
                    "maneuver_direction": obligation.maneuver_direction,
                    "target_id": "semantic-target-" + obligation.target_digest[:20],
                    "target_owner": "QUALIFIED_SEMANTIC_OBLIGATION",
                    "entry_road_id": int(representative["entry_road_id"]),
                    "entry_lane_id": int(representative["entry_lane_id"]),
                    "exit_road_id": int(representative["exit_road_id"]),
                    "exit_lane_id": int(representative["exit_lane_id"]),
                }
            )
            matches = [(0.0, semantic_opportunity)]
        if getattr(self, "_method_v3_enabled", False):
            resolution = resolve_selected_opportunity_equivalence_v3(
                matches,
                selected_obligation_identity=obligation.obligation_identity,
                selected_maneuver_direction=obligation.maneuver_direction,
            )
            opportunity = resolution.representative
            # Distance is evidence, not the resolver. Use the conservative
            # class envelope after semantic/topological uniqueness is proven.
            match_distance = max(float(distance) for distance, _ in matches)
            self._receipt["method_v3_selected_opportunity_equivalence"] = (
                resolution.to_dict()
            )
        else:
            if len(matches) != 1:
                raise RuntimeError("METHOD_V2_SELECTED_OPPORTUNITY_CARDINALITY_INVALID")
            match_distance, opportunity = matches[0]
        if not (
            opportunity.get("availability") is True
            and opportunity.get("route_reachable") is True
        ):
            raise RuntimeError("METHOD_V2_SELECTED_OPPORTUNITY_UNREACHABLE")
        identity_fields = (
            "junction_id",
            "target_id",
            "branch_id",
        )
        index_fields = (
            "route_order_index",
            "route_opportunity_index",
        )
        lane_fields = (
            "entry_road_id",
            "entry_lane_id",
            "exit_road_id",
            "exit_lane_id",
        )
        if not (
            all(
                isinstance(opportunity.get(name), str)
                and bool(str(opportunity[name]).strip())
                and str(opportunity[name]).strip().upper()
                not in {"NONE", "UNKNOWN"}
                for name in identity_fields
            )
            and all(
                isinstance(opportunity.get(name), int)
                and not isinstance(opportunity.get(name), bool)
                and int(opportunity[name]) >= 0
                for name in index_fields
            )
            and all(
                isinstance(opportunity.get(name), int)
                and not isinstance(opportunity.get(name), bool)
                for name in lane_fields
            )
        ):
            raise RuntimeError("METHOD_V2_SELECTED_OPPORTUNITY_IDENTITY_INVALID")
        traced = _candidate_connector_evidence(
            _live_map(), route_rows, [opportunity]
        )
        if len(traced) != 1:
            raise RuntimeError("METHOD_V2_SELECTED_CONNECTOR_CARDINALITY_INVALID")
        connector = dict(traced[0])
        connector["candidate_id"] = obligation.candidate_id
        connector["selected_obligation_digest"] = obligation.obligation_digest
        connector["selected_branch_identity"] = obligation.local_branch_identity
        connector["selected_target_match_distance_m"] = match_distance
        connector["selection_binding"] = "OBLIGATION_LOCAL_TARGET_TO_EXISTING_OPPORTUNITY"
        exact_lane_pair_match = all(
            isinstance(connector.get(name), int)
            and not isinstance(connector.get(name), bool)
            and int(connector[name]) == int(opportunity[name])
            for name in lane_fields
        )
        try:
            live_exit_xy = connector["polyline_xy_m"][-1]
            selected_anchor_xy = opportunity["anchor_xy"]
            live_exit_anchor_distance_m = math.hypot(
                float(live_exit_xy[0]) - float(selected_anchor_xy[0]),
                float(live_exit_xy[1]) - float(selected_anchor_xy[1]),
            )
        except (IndexError, KeyError, TypeError, ValueError):
            live_exit_anchor_distance_m = math.inf
        connector["exact_selected_lane_pair_match"] = exact_lane_pair_match
        connector["live_exit_anchor_distance_m"] = live_exit_anchor_distance_m
        connector["live_exit_anchor_within_existing_uncertainty"] = bool(
            math.isfinite(live_exit_anchor_distance_m)
            and live_exit_anchor_distance_m <= uncertainty
        )
        if not (
            connector.get("status") == "AVAILABLE"
            and connector.get("exit_reached") is True
            and exact_lane_pair_match
            and connector["live_exit_anchor_within_existing_uncertainty"] is True
        ):
            raise RuntimeError("METHOD_V2_SELECTED_CONNECTOR_EVIDENCE_UNKNOWN")
        return connector, dict(opportunity)

    @staticmethod
    def _method_v2_lane_links(
        map_object: Any, connector: Mapping[str, Any]
    ) -> tuple[tuple[int, int], ...]:
        try:
            import carla

            links: set[tuple[int, int]] = set()
            for point in connector.get("polyline_xy_m", ()):
                waypoint = map_object.get_waypoint(
                    carla.Location(
                        x=float(point[0]), y=float(point[1]), z=0.0
                    ),
                    project_to_road=True,
                    lane_type=carla.LaneType.Driving,
                )
                links.add((int(waypoint.road_id), int(waypoint.lane_id)))
            links.add(
                (
                    int(connector["exit_road_id"]),
                    int(connector["exit_lane_id"]),
                )
            )
        except (AttributeError, ImportError, KeyError, RuntimeError, TypeError, ValueError) as error:
            raise RuntimeError("METHOD_V2_SELECTED_LANE_LINK_EVIDENCE_UNKNOWN") from error
        if not links:
            raise RuntimeError("METHOD_V2_SELECTED_LANE_LINK_EVIDENCE_UNKNOWN")
        return tuple(sorted(links))

    def _activate_method_revision_v2(
        self,
        candidate: Any,
        unique_observation: RuntimeWindowObservation,
        now: float,
        *,
        execution_deadline_monotonic: Optional[float] = None,
    ) -> None:
        """Freeze one semantic navigation identity after the authorized replan."""

        try:
            if getattr(self, "_method_v3_enabled", False):
                phase_owner = self._method_v3_phase_owner
                if phase_owner.phase is MethodV3Phase.UNRESOLVED_AMBIGUITY:
                    phase_owner.transition(
                        MethodV3Phase.QUERY_PENDING,
                        "ACTIVE_QUERY_PRECEDED_ACCEPTED_ANSWER",
                    )
                if phase_owner.phase is MethodV3Phase.QUERY_PENDING:
                    phase_owner.transition(
                        MethodV3Phase.ANSWER_RESOLVED,
                        "PASSENGER_ANSWER_ACCEPTED_BY_SEMANTIC_LIFECYCLE",
                    )
            obligation = self._candidate_local_navigation_obligations.get(
                str(candidate.candidate_id)
            )
            if obligation is None:
                raise RuntimeError("METHOD_V2_SELECTED_OBLIGATION_MISSING")
            if obligation.interpretation_id != str(candidate.interpretation_id):
                raise RuntimeError("METHOD_V2_SELECTED_INTERPRETATION_MISMATCH")
            mission = self._candidate_local_navigation_mission
            if mission is None:
                raise RuntimeError("METHOD_V2_MISSION_CONTEXT_MISSING")
            mission.assert_endpoint_unchanged()
            deadline = (
                execution_deadline_monotonic
                if execution_deadline_monotonic is not None
                else self._method_v2_frozen_commitment_deadline_monotonic
            )
            if deadline is None or not math.isfinite(float(deadline)):
                raise RuntimeError("METHOD_V2_FROZEN_COMMITMENT_DEADLINE_UNKNOWN")
            connector, opportunity = self._method_v2_selected_connector(
                obligation,
                calibrated_uncertainty_m=(
                    unique_observation.calibrated_uncertainty_m
                ),
            )
            selected_opportunity_equivalence = self._receipt.get(
                "method_v3_selected_opportunity_equivalence"
            )
            onset = _first_structural_divergence_progress(
                connector,
                lane_clearance_m=unique_observation.lane_clearance_m,
                calibrated_uncertainty_m=(
                    unique_observation.calibrated_uncertainty_m
                ),
                hysteresis_samples=3,
            )
            if (
                onset.get("status") != "AVAILABLE"
                and not getattr(self, "_method_v3_enabled", False)
            ):
                raise RuntimeError("METHOD_V2_STRUCTURAL_DIVERGENCE_UNKNOWN")
            entry_progress = float(connector["junction_entry_progress_m"])
            divergence_branch_progress = (
                0.0
                if getattr(self, "_method_v3_enabled", False)
                and onset.get("status") != "AVAILABLE"
                else float(onset["progress_m"]) - entry_progress
            )
            if divergence_branch_progress < 0.0:
                raise RuntimeError("METHOD_V2_STRUCTURAL_DIVERGENCE_FRAME_MISMATCH")
            if getattr(self, "_method_v3_enabled", False):
                onset = dict(onset)
                onset.update(
                    {
                        "post_answer_activation_owner": False,
                        "phase_owner": "UNRESOLVED_AMBIGUITY_PRE_ANSWER_ONLY",
                        "selected_execution_separation_owner": (
                            "CURRENT_SELECTED_VS_ALTERNATIVE_TOPOLOGY"
                        ),
                        "effective_selected_branch_progress_m": (
                            divergence_branch_progress
                        ),
                    }
                )
            navigation_context_identity = canonical_identity_digest(
                {
                    "candidate_id": obligation.candidate_id,
                    "interpretation_id": obligation.interpretation_id,
                    "obligation_digest": obligation.obligation_digest,
                    "branch_identity": obligation.local_branch_identity,
                    "branch_digest": obligation.branch_digest,
                    "global_destination_identity": (
                        obligation.global_destination_identity
                    ),
                    "mission_context_digest": obligation.mission_context_digest,
                }
            )
            identity = SelectedNavigationIdentity(
                candidate_id=obligation.candidate_id,
                interpretation_id=obligation.interpretation_id,
                obligation_identity=obligation.obligation_identity,
                obligation_digest=obligation.obligation_digest,
                branch_identity=obligation.local_branch_identity,
                branch_digest=obligation.branch_digest,
                navigation_context_identity=navigation_context_identity,
                global_destination_identity=obligation.global_destination_identity,
                mission_context_digest=obligation.mission_context_digest,
                route_version=str(self._runtime_route_version),
                environment_digest=str(self._runtime_environment_digest),
            )
            map_object = _live_map()
            contract = BranchCommitmentContract(
                selected_junction_identity=str(opportunity["junction_id"]),
                selected_lane_links=self._method_v2_lane_links(
                    map_object, connector
                ),
                selected_exit_road_id=int(connector["exit_road_id"]),
                selected_exit_lane_id=int(connector["exit_lane_id"]),
                branch_polyline_xy_m=tuple(
                    (float(row[0]), float(row[1]))
                    for row in connector["polyline_xy_m"]
                ),
                structural_divergence_branch_progress_m=(
                    divergence_branch_progress
                ),
                lane_clearance_m=float(unique_observation.lane_clearance_m),
                calibrated_uncertainty_m=float(
                    unique_observation.calibrated_uncertainty_m
                ),
                local_horizon_m=float(obligation.local_horizon_m),
                activation_deadline_monotonic_s=float(deadline),
            )
            if getattr(self, "_method_v2_6_enabled", False):
                if not isinstance(
                    self._method_v2_execution,
                    GenericDownstreamLandingManeuverExecution,
                ):
                    raise RuntimeError("METHOD_V2_6_EXECUTION_OWNER_MISMATCH")
                alternative_branches = []
                for alternative in sorted(
                    self._candidate_local_navigation_obligations.values(),
                    key=lambda value: value.candidate_id,
                ):
                    if alternative.candidate_id == obligation.candidate_id:
                        continue
                    alternative_connector, _ = self._method_v2_selected_connector(
                        alternative,
                        calibrated_uncertainty_m=(
                            unique_observation.calibrated_uncertainty_m
                        ),
                    )
                    alternative_branches.append(
                        derive_alternative_branch_topology(
                            map_object,
                            alternative_connector,
                            candidate_id=alternative.candidate_id,
                            obligation_digest=alternative.obligation_digest,
                            branch_identity=alternative.local_branch_identity,
                            branch_digest=alternative.branch_digest,
                        )
                    )
                downstream_landing = (
                    derive_selected_branch_downstream_landing(
                        map_object,
                        connector,
                        identity,
                        contract,
                        alternative_branches=tuple(alternative_branches),
                    )
                    if getattr(self, "_method_v3_enabled", False)
                    else derive_v27_selected_branch_downstream_landing(
                        map_object,
                        connector,
                        identity,
                        contract,
                        alternative_branches=tuple(alternative_branches),
                    )
                    if getattr(self, "_method_v2_7_enabled", False)
                    else derive_selected_branch_downstream_landing(
                        map_object,
                        connector,
                        identity,
                        contract,
                        alternative_branches=tuple(alternative_branches),
                    )
                )
                self._method_v2_execution.bind_downstream_landing(
                    downstream_landing
                )
                if (
                    getattr(self, "_method_v3_enabled", False)
                    and selected_opportunity_equivalence is not None
                ):
                    # Alternative-topology derivation reuses the same resolver.
                    # Restore the answer-selected class as the diagnostic owner.
                    self._receipt[
                        "method_v3_selected_opportunity_equivalence"
                    ] = selected_opportunity_equivalence
                self._receipt[
                    "selected_branch_downstream_landing_identity"
                ] = downstream_landing.to_dict()
                if getattr(self, "_method_v2_8_enabled", False):
                    if not isinstance(
                        self._method_v2_execution,
                        TopologyLockedManeuverExecutionV28,
                    ):
                        raise RuntimeError("METHOD_V2_8_EXECUTION_OWNER_MISMATCH")
                    staged = self._method_v2_1_staged_plan
                    if not isinstance(staged, Mapping):
                        raise RuntimeError("METHOD_V2_8_STAGED_FRESH_PLAN_MISSING")
                    fresh_receipt = staged.get("receipt")
                    fresh_result = staged.get("result")
                    if not isinstance(fresh_receipt, Mapping) or fresh_result is None:
                        raise RuntimeError("METHOD_V2_8_FRESH_PLAN_EVIDENCE_MISSING")
                    fresh_forward_evidence = fresh_receipt.get("forward_evidence")
                    if not isinstance(fresh_forward_evidence, Mapping):
                        raise RuntimeError(
                            "METHOD_V2_8_FRESH_PLAN_SOURCE_EVIDENCE_MISSING"
                        )
                    fresh_source_observation_id = str(
                        fresh_forward_evidence.get("source_observation_id") or ""
                    )
                    fresh_source_frame_id = str(
                        fresh_forward_evidence.get("source_frame_id") or ""
                    )
                    ready_observation = staged.get("ready_observation")
                    ready_observation_id = str(
                        getattr(ready_observation, "source_observation_id", "")
                    )
                    ready_frame_id = str(staged.get("ready_frame_id"))
                    if not (
                        fresh_source_observation_id
                        and fresh_source_frame_id
                        and ready_observation_id
                        and fresh_source_observation_id == ready_observation_id
                        and fresh_source_frame_id == ready_frame_id
                    ):
                        raise RuntimeError(
                            "METHOD_V2_8_FRESH_PLAN_SOURCE_IDENTITY_MISMATCH"
                        )
                    selected_branch_or_opportunity_identity = (
                        canonical_identity_digest(
                            {
                                "candidate_id": obligation.candidate_id,
                                "branch_identity": obligation.local_branch_identity,
                                "branch_digest": obligation.branch_digest,
                                "junction_identity": opportunity.get("junction_id"),
                                "target_id": opportunity.get("target_id"),
                                "route_order_index": opportunity.get(
                                    "route_order_index"
                                ),
                                "route_opportunity_index": opportunity.get(
                                    "route_opportunity_index"
                                ),
                            }
                        )
                    )
                    transaction = SelectedPlanTransactionV28(
                        candidate_id=obligation.candidate_id,
                        interpretation_id=obligation.interpretation_id,
                        obligation_identity=obligation.obligation_identity,
                        obligation_digest=obligation.obligation_digest,
                        selected_branch_or_opportunity_identity=(
                            selected_branch_or_opportunity_identity
                        ),
                        selected_branch_digest=obligation.branch_digest,
                        downstream_landing_identity_digest=(
                            downstream_landing.identity_digest
                        ),
                        navigation_context_identity=navigation_context_identity,
                        global_destination_identity=(
                            obligation.global_destination_identity
                        ),
                        mission_context_digest=obligation.mission_context_digest,
                        route_version=str(self._runtime_route_version),
                        environment_digest=str(self._runtime_environment_digest),
                        fresh_plan_id=str(staged.get("fresh_set_id")),
                        fresh_plan_route_digest=str(
                            fresh_receipt.get("route_sha256")
                            or _tensor_digest(fresh_result.raw_route)
                        ),
                        fresh_plan_speed_digest=str(
                            fresh_receipt.get("speed_sha256")
                            or _tensor_digest(fresh_result.raw_speed)
                        ),
                        source_observation_id=str(
                            fresh_source_observation_id
                        ),
                        source_frame_id=str(
                            fresh_source_frame_id
                        ),
                    )
                    self._method_v2_execution.bind_selected_plan_transaction(
                        transaction
                    )
                    self._receipt["method_v2_8_selected_plan_transaction"] = (
                        transaction.to_dict()
                    )
            # Physical observations below use CARLA world coordinates.  Seed
            # traveled-distance accounting from that exact same coordinate
            # domain; the navigation/GPS pose is only for model projection.
            live_origin = self._current_live_ego_planner_endpoint()
            if getattr(self, "_method_v3_enabled", False):
                if not isinstance(
                    self._method_v2_execution, RouteBoundManeuverExecutionV3
                ):
                    raise RuntimeError("METHOD_V3_EXECUTION_OWNER_MISMATCH")
                reconnection = self._global_route_reconnection_bridge
                if reconnection is None:
                    raise RuntimeError("METHOD_V3_EXISTING_ROUTE_BRIDGE_MISSING")
                route_bridge = SelectedBranchRouteBindingBridgeV3(
                    mission,
                    reconnection.planner_capability,
                    reconnection._route_owner,
                )
                route_binding = route_bridge.bind(
                    obligation,
                    live_origin,
                    map_object=map_object,
                    selected_topology_owner=downstream_landing,
                    preinstall_observer=(
                        self._persist_method_v3_preinstall_route_receipt
                    ),
                    installation_event_identity=(
                        f"frame-{self._latest_frame}:selected-route-install"
                    ),
                    selected_maneuver_direction=str(
                        opportunity.get("maneuver_direction") or ""
                    ),
                    transaction_provenance={
                        "planning_effective_k": int(
                            self._candidate_set.effective_k
                        ),
                        "query_identity": self._receipt.get(
                            "persistent_query_id"
                        ),
                        "answer_identity": (
                            self._receipt.get("semantic_answer_contract") or {}
                        ).get("answer_digest"),
                        "candidate_generation_identity": (
                            self._method_v2_execution.selected_plan_transaction.fresh_plan_id
                        ),
                        "selected_plan_transaction_identity": (
                            self._method_v2_execution.selected_plan_transaction.identity_digest
                        ),
                        "fresh_plan_source_frame_id": (
                            self._method_v2_execution.selected_plan_transaction.source_frame_id
                        ),
                        "fresh_plan_source_observation_id": (
                            self._method_v2_execution.selected_plan_transaction.source_observation_id
                        ),
                        "selected_answer_identity": {
                            "resolved_candidate_id": (
                                self._receipt.get("semantic_answer_contract") or {}
                            ).get("resolved_candidate_id"),
                            "selected_candidate_id": obligation.candidate_id,
                        },
                        "semantic_relevance": self._receipt.get(
                            "method_v2_8_semantic_answer_expiry"
                        ),
                        "opportunity_equivalence": self._receipt.get(
                            "method_v3_selected_opportunity_equivalence"
                        ),
                        "current_activation_opportunity": (
                            self._receipt.get(
                                "method_v2_3_pre_activation_feasibility_history"
                            )
                            or []
                        )[-1:] or None,
                    },
                )
                self._method_v2_execution.bind_selected_route(route_binding)
                resolved_route = ResolvedSelectedLocalRoute.resolve(
                    obligation=obligation,
                    mission=mission,
                    receipt=route_binding,
                )
                self._method_v2_execution.bind_resolved_selected_local_route(
                    resolved_route
                )
                self._method_v3_selected_route_binding = route_binding
                self._method_v3_resolved_selected_local_route = resolved_route
                downstream_owner = getattr(
                    self._method_v2_execution,
                    "downstream_landing_identity",
                    None,
                )
                if downstream_owner is None:
                    raise RuntimeError(
                        "METHOD_V3_R2_DOWNSTREAM_LANDING_IDENTITY_MISSING"
                    )
                self._method_v3_connector_phase_owner.bind(
                    ConnectorPhaseTransactionR2(
                        route_transaction_identity=(
                            route_binding.route_transaction_identity
                        ),
                        installed_route_identity=(
                            route_binding.installed_route_identity
                        ),
                        route_generation=int(route_binding.route_generation_after),
                        installed_route_catalog_digest=(
                            route_binding.target_window_route_catalog_digest
                        ),
                        selected_exit_waypoint_id=(
                            route_binding.selected_exit_waypoint_id
                        ),
                        selected_junction_identity=(
                            route_binding.selected_junction_identity
                        ),
                        downstream_landing_identity_digest=str(
                            downstream_owner.identity_digest
                        ),
                    )
                )
                self._method_v3_connector_target_window_transaction_identity = (
                    route_binding.route_transaction_identity
                )
                self._receipt["method_v3_connector_phase_owner_r2"] = (
                    self._method_v3_connector_phase_owner.summary()
                )
                self._method_v3_phase_owner.transition(
                    MethodV3Phase.SELECTED_ROUTE_READY,
                    "EXISTING_ROUTE_OWNER_ATOMICALLY_INSTALLED_SELECTED_BRANCH",
                )
                self._receipt["method_v3_selected_route_binding"] = (
                    route_binding.to_dict()
                )
                self._receipt["method_v3_resolved_selected_local_route"] = (
                    resolved_route.to_dict()
                )
                _atomic_high_fidelity_json(
                    self.output_dir / "METHOD_V3_POSTINSTALL_ROUTE_RECEIPT.json",
                    route_binding.to_dict(),
                )
            self._method_v2_execution.activate(
                identity,
                contract,
                frame_id=int(self._latest_frame),
                ego_x_m=float(live_origin.x),
                ego_y_m=float(live_origin.y),
                current_monotonic_s=float(now),
            )
            if getattr(self, "_method_v3_enabled", False):
                self._method_v3_phase_owner.transition(
                    MethodV3Phase.SELECTED_ACTIVE_PRECOMMIT,
                    "FRESH_SIMLINGO_EXECUTION_WILL_CONSUME_INSTALLED_ROUTE_NEXT_TICK",
                )
                self._receipt["method_v3_phase_owner"] = (
                    self._method_v3_phase_owner.summary()
                )
            self._method_v2_selected_candidate = candidate
            self._method_v2_selected_obligation = obligation
            self._method_v2_selected_prompt = str(candidate.prompt_text)
            self._method_v2_timing.update(
                {
                    "execution_activated_monotonic_s": float(now),
                    "execution_activated_frame_id": self._latest_frame,
                    "execution_activated_simulation_time_s": (
                        self._latest_simulation_time
                    ),
                    "frozen_commitment_deadline_monotonic_s": float(deadline),
                    "execution_deadline_owner": (
                        "V2_3_ACTIVE_SELECTED_MANEUVER_EXECUTION_BUDGET"
                        if execution_deadline_monotonic is not None
                        else "LEGACY_FROZEN_COMMITMENT_DEADLINE"
                    ),
                    "T_execution_margin_s": float(deadline) - float(now),
                    "execution_activation_world_xy_m": [
                        float(live_origin.x),
                        float(live_origin.y),
                    ],
                    "execution_activation_position_source": (
                        "CARLA_HERO_CURRENT_LIVE_LOCATION"
                    ),
                }
            )
            self._receipt.update(
                {
                    "method_revision_v2_selected_connector": connector,
                    "method_revision_v2_selected_opportunity": opportunity,
                    "method_revision_v2_structural_divergence": onset,
                    "method_revision_v2_navigation_context_identity": (
                        navigation_context_identity
                    ),
                    "method_revision_v2_execution": (
                        self._method_v2_execution.summary()
                    ),
                    "method_revision_v2_status": (
                        "ACTIVE_BOUNDED_SEMANTIC_MANEUVER_EXECUTION"
                    ),
                }
            )
        except Exception as error:
            activation_error_chain = []
            chained_error: Optional[BaseException] = error
            while chained_error is not None:
                activation_error_chain.append(
                    {
                        "type": type(chained_error).__name__,
                        "message": str(chained_error),
                    }
                )
                chained_error = chained_error.__cause__
            self._method_v2_execution.terminate_unknown(
                "METHOD_V2_ACTIVATION_EVIDENCE_UNKNOWN", self._latest_frame
            )
            self.shared_act.revoke(AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED)
            self._receipt.update(
                {
                    "method_revision_v2_status": (
                        "EVIDENCE_UNKNOWN_FAIL_CLOSED_AT_ACTIVATION"
                    ),
                    "method_revision_v2_activation_error": {
                        "type": type(error).__name__,
                        "message": str(error),
                        "cause_chain": activation_error_chain,
                    },
                    "method_revision_v2_execution": (
                        self._method_v2_execution.summary()
                    ),
                }
            )
            self._terminal = True

    def _method_v3_connector_context_r2(
        self,
        active_route: Any,
        *,
        live_connector_relation: str,
    ) -> tuple[Any, Any, tuple[Any, ...], ConnectorTargetWindowStateR2, Any]:
        """Build and validate the single context used before every R2 write."""

        binding = self._method_v3_selected_route_binding
        if binding is None:
            raise RuntimeError("METHOD_V3_R2_ROUTE_BINDING_MISSING")
        planner = getattr(self.agent, "_route_planner", None)
        route_owner = getattr(
            self._global_route_reconnection_bridge, "_route_owner", None
        )
        if (
            planner is None
            or route_owner is None
            or active_route is not planner.route
        ):
            raise RuntimeError("METHOD_V3_CONNECTOR_TARGET_ACTIVE_ROUTE_OWNER_INVALID")
        catalog = tuple(binding.target_window_route_catalog)
        selected_plan = getattr(
            self._method_v2_execution, "selected_plan_transaction", None
        )
        selected_plan_identity = getattr(selected_plan, "identity_digest", None)
        selected_transaction_valid = bool(
            binding.target_window_catalog_bound
            and binding.target_window_contract_identity
            == METHOD_V3_TARGET_WINDOW_R2_CONTRACT
            and selected_plan_identity
            == binding.selected_plan_transaction_identity
            and self._method_v3_connector_target_window_transaction_identity
            == binding.route_transaction_identity
            and len(catalog) == binding.installed_route_row_count
            and 0
            <= int(binding.mandatory_connector_end_index)
            < len(catalog)
            and binding.target_window_connector_terminal_identity
            == catalog[binding.mandatory_connector_end_index]
            and target_window_catalog_digest(catalog)
            == binding.target_window_route_catalog_digest
        )
        active_generation = getattr(planner, "online_update_generation", None)
        installed_generation = binding.route_generation_after
        if active_generation is None or installed_generation is None:
            raise RuntimeError("METHOD_V3_CONNECTOR_TARGET_ROUTE_GENERATION_UNKNOWN")
        connector_phase = self._method_v3_connector_phase_owner.phase
        if connector_phase is None:
            raise RuntimeError("METHOD_V3_R2_CONNECTOR_PHASE_UNBOUND")
        state = ConnectorTargetWindowStateR2(
            method_phase=self._method_v3_phase_owner.phase.value,
            connector_phase=connector_phase.value,
            live_connector_relation=str(live_connector_relation),
            planning_effective_k=int(binding.planning_effective_k),
            selected_transaction_valid=selected_transaction_valid,
            selected_transaction_identity=binding.route_transaction_identity,
            expected_transaction_identity=str(
                self._method_v3_connector_target_window_transaction_identity or ""
            ),
            active_route_identity=str(
                getattr(route_owner, "active_route_identity", "")
            ),
            installed_route_identity=binding.installed_route_identity,
            active_route_generation=int(active_generation),
            installed_route_generation=int(installed_generation),
            current_destination_identity=str(
                getattr(route_owner, "global_destination_identity", "")
            ),
            original_destination_identity=(
                binding.global_destination_identity_before
            ),
            committed=(
                self._method_v2_execution.state
                in {
                    ManeuverExecutionState.MANEUVER_COMMITTED,
                    ManeuverExecutionState.MANEUVER_COMPLETED,
                }
            ),
            terminal_fail_closed=self._method_v3_connector_phase_owner.fail_closed,
            release_latched=self._method_v3_connector_phase_owner.release_latched,
        )
        validation = validate_connector_context_r2(
            tuple(active_route),
            catalog,
            state,
            connector_start_index=binding.mandatory_connector_start_index,
            connector_end_index=binding.mandatory_connector_end_index,
            compatible_suffix_original_indices=(
                binding.target_window_compatible_suffix_original_indices
            ),
        )
        return binding, route_owner, catalog, state, validation

    def _method_v3_observe_connector_entry_r2(
        self,
        *,
        binding: Any,
        state: ConnectorTargetWindowStateR2,
        context_validation: Any,
        selected_connector_membership: bool,
        frame_id: Any,
    ) -> ConnectorPhysicalPhaseR2:
        """Apply entry only after the shared immutable/live context is valid."""

        connector_phase_owner = self._method_v3_connector_phase_owner
        if state.planning_effective_k != 1 and context_validation.valid:
            return connector_phase_owner.observe_live_selected_connector(
                selected_connector_membership=selected_connector_membership,
                frame_id=None if frame_id is None else int(frame_id),
                route_transaction_identity=binding.route_transaction_identity,
                installed_route_identity=binding.installed_route_identity,
                route_generation=int(state.active_route_generation),
                installed_route_catalog_digest=(
                    binding.target_window_route_catalog_digest
                ),
            )
        connector_phase = connector_phase_owner.phase
        if connector_phase is None:
            raise RuntimeError("METHOD_V3_R2_CONNECTOR_PHASE_UNBOUND")
        return connector_phase

    def select_connector_target_window(
        self,
        active_route: Any,
        ego_xyz_m: Any,
        baseline_targets: tuple[Any, Any],
        *,
        frame_id: Any = None,
        input_data: Any = None,
    ) -> tuple[Any, Any]:
        """Select the R2 phase-owned view before the same SimLingo forward."""

        del input_data

        if not getattr(self, "_method_v3_enabled", False):
            return baseline_targets
        binding = self._method_v3_selected_route_binding
        if binding is None:
            return baseline_targets
        planner = getattr(self.agent, "_route_planner", None)
        owner = getattr(
            self._global_route_reconnection_bridge, "_route_owner", None
        )
        if planner is None or owner is None or active_route is not planner.route:
            raise RuntimeError("METHOD_V3_CONNECTOR_TARGET_ACTIVE_ROUTE_OWNER_INVALID")
        resolved_route = getattr(
            self, "_method_v3_resolved_selected_local_route", None
        )
        if resolved_route is not None:
            returned = self._method_v2_execution.native_route_target_passthrough(
                baseline_targets,
                active_route_identity=str(
                    getattr(owner, "active_route_identity", "")
                ),
                consumed_route_identity=str(
                    getattr(owner, "next_tick_consumed_route_identity", "")
                ),
                route_generation=int(
                    getattr(planner, "online_update_generation", -1)
                ),
                destination_identity=str(
                    getattr(owner, "global_destination_identity", "")
                ),
                frame_id=None if frame_id is None else int(frame_id),
            )
            self._receipt["method_v3_native_route_target_owner"] = {
                "owner_identity": (
                    "LINGO_AGENT_TICK_EXISTING_ROUTE_PLANNER_RUN_STEP"
                ),
                "mode": "NATIVE_ROUTE_PASSTHROUGH",
                "special_target_window_reached": False,
                "target_search_count": 0,
                "route_transaction_identity": (
                    resolved_route.route_transaction_identity
                ),
                "installed_route_identity": (
                    resolved_route.installed_route_identity
                ),
            }
            return returned

        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            hero = CarlaDataProvider.get_hero_actor()
            world = CarlaDataProvider.get_world()
            hero_location = hero.get_location()
            ego_carla_world_xyz_m = (
                float(hero_location.x),
                float(hero_location.y),
                float(hero_location.z),
            )
            if not all(math.isfinite(value) for value in ego_carla_world_xyz_m):
                raise ValueError("METHOD_V3_CONNECTOR_TARGET_EGO_LOCATION_INVALID")
            waypoint = world.get_map().get_waypoint(hero_location)
            road_id = int(waypoint.road_id)
            section_id = int(waypoint.section_id)
            lane_id = int(waypoint.lane_id)
            waypoint_id = int(waypoint.id)
            is_junction = bool(waypoint.is_junction)
            junction_id = None
            if is_junction:
                raw_junction_id = getattr(waypoint, "junction_id", None)
                if raw_junction_id is None:
                    raw_junction_id = waypoint.get_junction().id
                junction_id = int(raw_junction_id)
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as error:
            raise RuntimeError(
                "METHOD_V3_CONNECTOR_TARGET_LIVE_TOPOLOGY_UNKNOWN"
            ) from error

        try:
            route_planner_input_xyz_m = tuple(
                float(value) for value in ego_xyz_m[:3]
            )
            world_to_route_planner_translation_xyz_m = tuple(
                float(value)
                for value in binding.world_to_route_planner_translation_xyz_m[:3]
            )
            ego_catalog_xyz_m = tuple(
                world_value + translation_value
                for world_value, translation_value in zip(
                    ego_carla_world_xyz_m,
                    world_to_route_planner_translation_xyz_m,
                )
            )
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise RuntimeError(
                "METHOD_V3_CONNECTOR_TARGET_CORRIDOR_EGO_OWNER_INVALID"
            ) from error
        if (
            len(route_planner_input_xyz_m) != 3
            or len(world_to_route_planner_translation_xyz_m) != 3
            or len(ego_catalog_xyz_m) != 3
            or not all(
                math.isfinite(value) for value in route_planner_input_xyz_m
            )
            or not all(
                math.isfinite(value)
                for value in world_to_route_planner_translation_xyz_m
            )
            or not all(math.isfinite(value) for value in ego_catalog_xyz_m)
        ):
            raise RuntimeError(
                "METHOD_V3_CONNECTOR_TARGET_CORRIDOR_EGO_OWNER_INVALID"
            )

        catalog = tuple(binding.target_window_route_catalog)
        topology_relation, selected_connector_membership = (
            classify_selected_connector_membership_r2(
                catalog,
                connector_start_index=binding.mandatory_connector_start_index,
                connector_end_index=binding.mandatory_connector_end_index,
                waypoint_id=waypoint_id,
                road_id=road_id,
                section_id=section_id,
                lane_id=lane_id,
                junction_id=junction_id,
                is_junction=is_junction,
            )
        )

        binding, owner, catalog, state, context_validation = (
            self._method_v3_connector_context_r2(
                active_route,
                live_connector_relation=topology_relation,
            )
        )
        planning_effective_k = state.planning_effective_k
        active_generation = state.active_route_generation
        connector_phase_owner = self._method_v3_connector_phase_owner
        membership_observed_before_tick = connector_phase_owner.entered
        connector_phase = self._method_v3_observe_connector_entry_r2(
            binding=binding,
            state=state,
            context_validation=context_validation,
            selected_connector_membership=selected_connector_membership,
            frame_id=frame_id,
        )
        state = replace(state, connector_phase=connector_phase.value)
        decision = select_connector_target_window_r2(
            tuple(active_route),
            catalog,
            state,
            connector_start_index=binding.mandatory_connector_start_index,
            connector_end_index=binding.mandatory_connector_end_index,
            compatible_suffix_original_indices=(
                binding.target_window_compatible_suffix_original_indices
            ),
            ego_xyz_m=ego_catalog_xyz_m,
            baseline_targets=baseline_targets,
        )
        if decision.release_latched:
            connector_phase_owner.acknowledge_release(
                self._method_v2_execution.state,
                None if frame_id is None else int(frame_id),
            )
        evidence = decision.evidence()
        evidence.update(
            {
                "frame_id": (
                    None if frame_id is None else int(frame_id)
                ),
                "installed_route_identity": binding.installed_route_identity,
                "active_route_identity": str(
                    getattr(owner, "active_route_identity", "")
                ),
                "route_generation": int(active_generation),
                "route_transaction_identity": binding.route_transaction_identity,
                "installed_route_catalog_digest": (
                    binding.target_window_route_catalog_digest
                ),
                "planning_effective_k": planning_effective_k,
                "topology_relation": topology_relation,
                "consolidated_connector_phase": connector_phase.value,
                "consolidated_connector_phase_owner": (
                    connector_phase_owner.summary()
                ),
                "selected_connector_membership": selected_connector_membership,
                "connector_membership_observed_before_tick": (
                    membership_observed_before_tick
                ),
                "connector_membership_observed_for_transaction": bool(
                    connector_phase_owner.entered
                ),
                "topology_compatible_with_selected_exit_or_suffix": None,
                "selected_suffix_topology_consulted_for_release": False,
                "current_waypoint_identity": {
                    "waypoint_id": waypoint_id,
                    "road_id": road_id,
                    "section_id": section_id,
                    "lane_id": lane_id,
                    "junction_id": junction_id,
                    "is_junction": is_junction,
                },
                "ego_catalog_projection_xyz_m": list(ego_catalog_xyz_m),
                "ego_catalog_projection_coordinate_domain": (
                    "SIMLINGO_ROUTE_PLANNER"
                ),
                "ego_catalog_projection_owner": (
                    "LIVE_CARLA_HERO_PLUS_BOUND_TRANSLATION"
                ),
                "ego_carla_world_xyz_m": list(ego_carla_world_xyz_m),
                "ego_carla_world_coordinate_domain": "CARLA_WORLD",
                "ego_route_planner_xyz_m": list(route_planner_input_xyz_m),
                "ego_route_planner_input_owner": "SIMLINGO_UKF_FILTERED_GPS",
                "ego_catalog_projection_vs_route_planner_input_delta_xyz_m": [
                    float(catalog_value - planner_value)
                    for catalog_value, planner_value in zip(
                        ego_catalog_xyz_m, route_planner_input_xyz_m
                    )
                ],
                "ego_catalog_projection_vs_route_planner_input_delta_xy_m": (
                    math.dist(
                        ego_catalog_xyz_m[:2], route_planner_input_xyz_m[:2]
                    )
                ),
                "installed_route_coordinate_domain": (
                    binding.installed_route_coordinate_domain
                ),
                "connector_projection_coordinate_domain": (
                    binding.connector_projection_coordinate_domain
                ),
                "source_topology_coordinate_domain": (
                    binding.source_topology_coordinate_domain
                ),
                "world_to_route_planner_translation_xyz_m": list(
                    world_to_route_planner_translation_xyz_m
                ),
                "tp1_world_xyz_m": (
                    None
                    if decision.tp1_original_index is None
                    else list(
                        catalog[decision.tp1_original_index].get(
                            "carla_world_xyz_m",
                            catalog[decision.tp1_original_index]["xyz_m"],
                        )
                    )
                ),
                "tp2_world_xyz_m": (
                    None
                    if decision.tp2_original_index is None
                    else list(
                        catalog[decision.tp2_original_index].get(
                            "carla_world_xyz_m",
                            catalog[decision.tp2_original_index]["xyz_m"],
                        )
                    )
                ),
                "tp1_route_planner_xyz_m": (
                    None
                    if decision.tp1 is None
                    else [float(value) for value in decision.tp1[0][:3]]
                ),
                "tp2_route_planner_xyz_m": (
                    None
                    if decision.tp2 is None
                    else [float(value) for value in decision.tp2[0][:3]]
                ),
                "tp1_road_option": (
                    None
                    if decision.tp1 is None
                    else str(getattr(decision.tp1[1], "name", decision.tp1[1]))
                ),
                "tp2_road_option": (
                    None
                    if decision.tp2 is None
                    else str(getattr(decision.tp2[1], "name", decision.tp2[1]))
                ),
                "destination_identity_unchanged": bool(
                    state.current_destination_identity
                    == state.original_destination_identity
                ),
                "simlingo_abi_unchanged": True,
                "scheduler_unchanged": True,
                "fresh_normal_forward_count": 0,
            }
        )
        self._method_v2_execution.record_connector_target_window(evidence)
        release_latched_derived = connector_phase_owner.release_latched
        self._receipt["method_v3_connector_phase_owner_r2"] = (
            connector_phase_owner.summary()
        )
        self._receipt["method_v3_connector_target_window"] = {
            "contract_identity": binding.target_window_contract_identity,
            "release_latched": (
                release_latched_derived
            ),
            "route_transaction_identity": binding.route_transaction_identity,
            "evidence_count": len(
                self._method_v2_execution.connector_target_window_evidence
            ),
            "latest": evidence,
        }
        if decision.mode == "FAIL_CLOSED":
            self._receipt["status"] = (
                "METHOD_V3_CONNECTOR_TARGET_WINDOW_FAIL_CLOSED"
            )
            self._persist()
            raise RuntimeError(
                "METHOD_V3_CONNECTOR_TARGET_WINDOW_FAIL_CLOSED:"
                + decision.reason
            )
        return decision.tp1, decision.tp2

    def _persist_method_v3_preinstall_route_receipt(
        self, receipt: Mapping[str, Any]
    ) -> None:
        """Persist admission evidence synchronously before the route-owner call."""

        value = dict(receipt)
        value["installation_frame_id"] = self._latest_frame
        value["installation_observation_id"] = self._latest_observation_id
        self._receipt["method_v3_preinstall_route_receipt"] = value
        _atomic_high_fidelity_json(
            self.output_dir / "METHOD_V3_PREINSTALL_ROUTE_RECEIPT.json",
            value,
        )

    def prepare_model_input(self, model_input: Any) -> Any:
        """Adapt the one normal SimLingo input to the active selected obligation."""

        if not getattr(self, "_method_revision_v2_enabled", False):
            return model_input
        execution = self._method_v2_execution
        if not execution.selected_navigation_required:
            return model_input
        try:
            obligation = self._method_v2_selected_obligation
            prompt = self._method_v2_selected_prompt
            identity = execution.identity
            if obligation is None or prompt is None or identity is None:
                raise RuntimeError("METHOD_V2_SELECTED_NAVIGATION_CONTEXT_MISSING")
            if getattr(self, "_method_v2_8_enabled", False):
                transaction = getattr(
                    execution, "selected_plan_transaction", None
                )
                downstream = getattr(
                    execution, "downstream_landing_identity", None
                )
                if transaction is None or downstream is None:
                    raise RuntimeError("METHOD_V2_8_REFRESH_TRANSACTION_MISSING")
                if not (
                    str(self._runtime_route_version)
                    == transaction.route_version
                    and str(self._runtime_environment_digest)
                    == transaction.environment_digest
                    and downstream.identity_digest
                    == transaction.downstream_landing_identity_digest
                ):
                    raise RuntimeError(
                        "METHOD_V2_8_REFRESH_ROUTE_ENVIRONMENT_OR_TOPOLOGY_CHANGED"
                    )
            if self._method_v2_pending_binding is not None:
                raise RuntimeError("METHOD_V2_PREVIOUS_NORMAL_FORWARD_NOT_CONSUMED")
            if self._latest_observation_id is None or self._latest_frame is None:
                raise RuntimeError("METHOD_V2_LIVE_SOURCE_IDENTITY_MISSING")
            refresh_materialization = None
            if getattr(self, "_method_v3_enabled", False):
                route_binding = self._method_v3_selected_route_binding
                if route_binding is None:
                    raise RuntimeError("METHOD_V3_SELECTED_ROUTE_BINDING_MISSING")
                owner = getattr(
                    self._global_route_reconnection_bridge, "_route_owner", None
                )
                active_identity = getattr(owner, "active_route_identity", None)
                consumed_identity = getattr(
                    owner, "next_tick_consumed_route_identity", None
                )
                if not (
                    active_identity == route_binding.installed_route_identity
                    and consumed_identity == route_binding.installed_route_identity
                ):
                    raise RuntimeError("METHOD_V3_INSTALLED_ROUTE_NOT_CONSUMED")
                target_points = getattr(self.agent, "target_points", None)
                binding = materialize_route_derived_forward_binding_v3(
                    obligation,
                    planning_observation_id=str(self._latest_observation_id),
                    planning_frame_id=int(self._latest_frame),
                    route_derived_target_points=target_points,
                )
            elif getattr(self, "_method_v2_8_enabled", False):
                refresh_materialization = execution.prepare_topology_locked_refresh(
                    obligation,
                    self._latest_ego_navigation_pose(),
                    planning_observation_id=str(self._latest_observation_id),
                    planning_frame_id=int(self._latest_frame),
                )
                binding = refresh_materialization.binding
            else:
                binding = materialize_forward_binding(
                    obligation,
                    self._latest_ego_navigation_pose(),
                    planning_observation_id=str(self._latest_observation_id),
                    planning_frame_id=int(self._latest_frame),
                )
            if not (
                binding.candidate_id == identity.candidate_id
                and binding.interpretation_id == identity.interpretation_id
                and binding.obligation_digest == identity.obligation_digest
                and binding.branch_digest == identity.branch_digest
                and binding.global_destination_identity
                == identity.global_destination_identity
                and binding.mission_context_digest == identity.mission_context_digest
            ):
                raise RuntimeError("METHOD_V2_FORWARD_BINDING_IDENTITY_CHANGED")
            adapted, built = self.forward_provider.adapter.adapt_driving_input(
                model_input,
                prompt,
                float(self._latest_speed),
                preserve_placeholder_values=False,
                navigation_binding=binding,
            )
            self._method_v2_pending_binding = binding
            prepared = self._receipt.setdefault(
                "method_revision_v2_prepared_normal_forward_inputs", []
            )
            prepared.append(
                {
                    "observation_id": str(self._latest_observation_id),
                    "frame_id": int(self._latest_frame),
                    "navigation_context_identity": (
                        identity.navigation_context_identity
                    ),
                    "obligation_digest": binding.obligation_digest,
                    "branch_digest": binding.branch_digest,
                    "global_destination_identity": (
                        binding.global_destination_identity
                    ),
                    "projection_digest": binding.projection_digest,
                    "prompt_body_sha256": canonical_sha256(built.prompt_body),
                    "normal_forward_owner": "SIMLINGO_EXISTING_MODEL_CALL_SITE",
                    "extra_candidate_forward_count": 0,
                    "target_point_owner": (
                        "LINGO_AGENT_TICK_EXISTING_ROUTE_PLANNER_RUN_STEP"
                        if getattr(self, "_method_v3_enabled", False)
                        else "DRIVECLARIFY_SELECTED_LOCAL_SEGMENT_PROJECTION"
                    ),
                    "active_route_identity": (
                        None
                        if not getattr(self, "_method_v3_enabled", False)
                        else active_identity
                    ),
                    "consumed_route_identity": (
                        None
                        if not getattr(self, "_method_v3_enabled", False)
                        else consumed_identity
                    ),
                    "topology_locked_segment_target_index": (
                        None
                        if refresh_materialization is None
                        else refresh_materialization.segment_target_index
                    ),
                    "topology_locked_segment_point_count": (
                        None
                        if refresh_materialization is None
                        else refresh_materialization.segment_point_count
                    ),
                    "topology_locked_world_target_pair_digest": (
                        None
                        if refresh_materialization is None
                        else refresh_materialization.world_target_pair_digest
                    ),
                    "geometry_changed_from_admission_pair": (
                        None
                        if refresh_materialization is None
                        else refresh_materialization.geometry_changed_from_admission_pair
                    ),
                }
            )
            return adapted
        except Exception as error:
            execution.terminate_unknown(
                "METHOD_V2_NORMAL_FORWARD_INPUT_PREPARATION_UNKNOWN",
                self._latest_frame,
            )
            self.shared_act.revoke(AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED)
            self._receipt.update(
                {
                    "method_revision_v2_status": (
                        "EVIDENCE_UNKNOWN_FAIL_CLOSED_NORMAL_FORWARD_PREPARATION"
                    ),
                    "method_revision_v2_prepare_error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                    "method_revision_v2_execution": execution.summary(),
                }
            )
            self._terminal = True
            self._persist()
            return model_input

    def select_plan_source(
        self, baseline_route: Any, baseline_speed: Any, current_monotonic: float
    ) -> tuple[Any, Any]:
        if self._initial_k1_emitted or self._episode_id is None:
            self._record_control_source_receipt(
                source=(
                    MethodControlSource.ORIGINAL_SIMLINGO
                    if self._initial_k1_emitted
                    else MethodControlSource.BASELINE_SIMLINGO
                ),
                baseline_route=baseline_route,
                returned_route=baseline_route,
            )
            return baseline_route, baseline_speed
        if (
            not self.control_enabled
            or self._runtime_route_version is None
        ):
            self._record_control_source_receipt(
                source=MethodControlSource.BASELINE_SIMLINGO,
                baseline_route=baseline_route,
                returned_route=baseline_route,
            )
            return baseline_route, baseline_speed
        episode = self.persistent_store.get(self._episode_id)
        window = self._latest_window
        subject = self.shared_act.subject
        action = episode.current_shared_executable_action
        same_source = bool(
            window is not None
            and window.source_observation_id == str(self._latest_observation_id)
            and str(window.source_frame_id) == str(self._latest_frame)
        )
        current_fresh = bool(
            episode.evidence_state is EvidenceState.FRESH
            and same_source
            and window is not None
            and window.valid_until_monotonic is not None
            and float(current_monotonic) < float(window.valid_until_monotonic)
        )
        current_alignment = bool(
            window is not None
            and window.route_version == self._runtime_route_version
            and (
                self._decision_evidence_v3_enabled
                or self._runtime_route_version == self._phase_b_contract["route_version"]
            )
            and window.environment_digest == self._runtime_environment_digest
        )
        member_match = bool(
            subject is not None
            and subject.active_member_set_digest
            == canonical_sha256(
                [
                    {
                        "candidate_id": row.candidate_id,
                        "interpretation_id": row.interpretation_id,
                        "semantic_sha256": row.semantic_sha256,
                        "target_obligation_digest": row.target_obligation_digest,
                    }
                    for row in sorted(
                        (
                            row
                            for row in episode.candidates
                            if row.candidate_id in episode.active_candidate_ids
                        ),
                        key=lambda row: (row.candidate_id, row.interpretation_id),
                    )
                ]
            )
        )
        if (
            getattr(self, "_method_v3_enabled", False)
            and self._method_v2_execution.selected_navigation_required
            and not self._method_v2_execution.selected_act_admissible
        ):
            admissibility = self._method_v2_execution.selected_plan_admissibility
            self._receipt["method_v3_selected_act_gate"] = {
                "selected_act_authorized": False,
                "necessary_status": SelectedPlanAdmissibility.ADMISSIBLE.value,
                "observed_status": (
                    None if admissibility is None else admissibility.status.value
                ),
                "reason_code": (
                    "ADMISSIBILITY_EVIDENCE_UNAVAILABLE"
                    if admissibility is None
                    else admissibility.reason_code
                ),
                "baseline_or_existing_lifecycle_preserved": True,
                "ask_wait_fallback_semantics_changed": False,
                "independent_safety_authority_changed": False,
                "controller_write_count": 0,
            }
            self._record_control_source_receipt(
                source=MethodControlSource.BASELINE_SIMLINGO,
                baseline_route=baseline_route,
                returned_route=baseline_route,
            )
            return baseline_route, baseline_speed
        if (
            getattr(self, "_method_v3_enabled", False)
            and self._method_v2_execution.selected_navigation_required
        ):
            admissibility = self._method_v2_execution.selected_plan_admissibility
            self._receipt["method_v3_selected_act_gate"] = {
                "selected_act_authorized": True,
                "necessary_status": SelectedPlanAdmissibility.ADMISSIBLE.value,
                "observed_status": admissibility.status.value,
                "reason_code": admissibility.reason_code,
                "controller_write_count": 0,
            }
        selected = self.shared_act.select_plan_source(
            baseline_route,
            baseline_speed,
            frame=self._latest_frame,
            observation_id=self._latest_observation_id,
            route_version=self._runtime_route_version,
            environment_digest=str(self._runtime_environment_digest),
            current_monotonic=current_monotonic,
            active_query=episode.active_query is not None,
            active_wait=self.wait.active,
            safety_active=bool(
                (
                    self._latest_unique_observation is None
                    or not self._unique_authorization_safety_gate(
                        self._latest_unique_observation
                    )
                    or not self._latest_unique_observation.hard_rule_gate
                )
                if self._authority_mode == "UNIQUE"
                else (
                    window is None
                    or window.recoverability != "RECOVERABLE"
                    or not current_alignment
                )
            ),
            episode_unresolved=episode.semantic_state.value == "UNRESOLVED",
            active_member_set_matches=member_match,
            current_action_relation=episode.current_candidate_relationship,
            evidence_fresh=current_fresh,
            plan_coverage_verified=bool(
                window is not None
                and getattr(
                    window,
                    "current_executable_coverage",
                    window.full_plan_coverage,
                )
            ),
            alignment_verified=current_alignment,
            latest_safe_slack_positive=bool(
                episode.current_candidate_relationship
                == "CURRENT_AND_FUTURE_EQUIVALENT"
                or (
                    window is not None
                    and getattr(
                        window, "precommitment_refresh_guarantee", None
                    ) == "GUARANTEED"
                )
                or (
                    window is not None
                    and window.latest_safe_clarification_monotonic is not None
                    and float(current_monotonic)
                    < float(window.latest_safe_clarification_monotonic)
                )
            ),
            recoverable=bool(
                window is not None
                and window.recoverability == "RECOVERABLE"
                and action is not None
            ),
        )
        source = (
            MethodControlSource.FRESH_UNIQUE_CANDIDATE
            if (
                self._authority_mode == "UNIQUE"
                and selected[0] is not baseline_route
            )
            or (
                self._authority_mode == "BOUNDED_UNIQUE"
                and self._method_v2_execution.selected_navigation_required
                and isinstance(
                    getattr(self, "_method_v2_3_activation_frame_id", None),
                    int,
                )
                and int(self._latest_frame)
                > int(self._method_v2_3_activation_frame_id)
            )
            else MethodControlSource.ORIGINAL_SIMLINGO_SHARED_PREFIX
            if self._authority_mode == "SHARED"
            and self.shared_act.window_consumed
            else MethodControlSource.BASELINE_SIMLINGO
        )
        self._record_control_source_receipt(
            source=source,
            baseline_route=baseline_route,
            returned_route=selected[0],
        )
        return selected

    def _record_control_source_receipt(
        self, *, source: MethodControlSource, baseline_route: Any, returned_route: Any
    ) -> None:
        envelope = self._method_decision_envelope
        row = {
            "schema_version": "driveclarify.method_v1.control_source_receipt.v1",
            "decision_label": (
                None if envelope is None else envelope.decision_label.value
            ),
            "decision_reason": (
                None if envelope is None else envelope.decision_reason
            ),
            "control_source": source.value,
            "authority_subject": (
                None if envelope is None else envelope.authority_subject
            ),
            "baseline_route_digest": _tensor_digest(baseline_route),
            "returned_route_digest": _tensor_digest(returned_route),
            "same_plan_object": returned_route is baseline_route,
            "source_frame_id": self._latest_frame,
        }
        rows = self._receipt.setdefault("control_source_receipts", [])
        if not rows or rows[-1] != row:
            rows.append(row)

    def on_pid_invocation(self, current_monotonic: float) -> None:
        del current_monotonic
        self._pid_invocations += 1
        self.shared_act.on_pid_invocation(frame=self._latest_frame)

    def on_control(self, control: Any, gt_velocity: Any, current_monotonic: float) -> None:
        del gt_velocity
        self._control_observations += 1
        self._last_returned_control = _control_values(control)
        timing_row = self._high_fidelity_step_timing_by_observation.get(
            str(self._latest_observation_id)
        )
        if timing_row is not None:
            timing_row["control_ready_monotonic_s"] = float(current_monotonic)
            received = timing_row.get("observation_received_monotonic_s")
            timing_row["observation_to_control_latency_s"] = (
                None
                if received is None
                else float(current_monotonic) - float(received)
            )
            try:
                from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

                control_world_frame = int(
                    CarlaDataProvider.get_world().get_snapshot().frame
                )
            except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
                control_world_frame = None
            timing_row["control_world_frame_id"] = control_world_frame
            timing_row["frame_lag"] = (
                None
                if control_world_frame is None
                else control_world_frame - int(self._latest_frame)
            )
            timing_row["queue_depth"] = None
            timing_row["queue_depth_observable"] = False
            self._high_fidelity_gpu_sample(timing_row)
        self.shared_act.on_control(frame=self._latest_frame)
        self._receipt["shared_act"] = dict(self.shared_act.summary())
        self._persist()

    def commit(self) -> None:
        """Record the final instrumented instant; perform no I/O on return path."""

        timing_row = getattr(
            self, "_high_fidelity_step_timing_by_observation", {}
        ).get(str(self._latest_observation_id))
        if timing_row is not None:
            # This assignment is deliberately the last instrumented operation in
            # the hook called immediately before agent_simlingo.run_step returns.
            # Derived latency and artifact I/O are deferred until close().
            timing_row["final_control_return_monotonic_s"] = time.monotonic()

    def _method_v2_live_route_context(self) -> tuple[str, str]:
        route_rows = self.topology_enumerator._route_rows(self._route())
        route_version = DecisionWindowEvidenceAdapter.route_version_id(
            [[float(x), float(y)] for x, y, _ in route_rows],
            source="AGENT_OWNED_DENSE_CARLA_WORLD_ROUTE",
        )
        map_object = _live_map()
        environment_digest = canonical_sha256(
            {
                "map": str(getattr(map_object, "name", "UNKNOWN_LIVE_MAP")),
                "route_version": route_version,
            }
        )
        return route_version, environment_digest

    @staticmethod
    def _method_v2_certificate_status(
        status: HardGateCertificateStatus, *, physical: bool
    ) -> str:
        pass_status = (
            HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_PASS
            if physical
            else HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_PASS
        )
        blocked_status = (
            HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_BLOCKED
            if physical
            else HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_BLOCKED
        )
        if status is pass_status:
            return "AVAILABLE_TRUE"
        if status is blocked_status:
            return "BLOCKED"
        return "UNKNOWN"

    def _method_v2_live_observation(self) -> LiveManeuverObservation:
        execution = self._method_v2_execution
        identity = execution.identity
        contract = execution.contract
        mission = self._candidate_local_navigation_mission
        if identity is None or contract is None or mission is None:
            raise RuntimeError("METHOD_V2_LIVE_EXECUTION_CONTEXT_MISSING")
        mission.assert_endpoint_unchanged()
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            hero = CarlaDataProvider.get_hero_actor()
            world = CarlaDataProvider.get_world()
            location = hero.get_location()
            waypoint = world.get_map().get_waypoint(location)
            is_junction = bool(waypoint.is_junction)
            junction_id = None
            if is_junction:
                raw_junction_id = getattr(waypoint, "junction_id", None)
                if raw_junction_id is None:
                    raw_junction_id = waypoint.get_junction().id
                junction_id = "junction-map-{}".format(int(raw_junction_id))
            road_id = int(waypoint.road_id)
            lane_id = int(waypoint.lane_id)
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as error:
            raise RuntimeError("METHOD_V2_LIVE_CARLA_TOPOLOGY_UNKNOWN") from error
        # Until completion the original route/environment identity must stay
        # exact. Only completion authorizes the separately audited reconnect.
        if execution.selected_navigation_required:
            route_version, environment_digest = self._method_v2_live_route_context()
        else:
            route_version = identity.route_version
            environment_digest = identity.environment_digest
        gate = self._resolve_hard_gate_evidence(
            decision_point="METHOD_V2_SELECTED_MANEUVER_EXECUTION"
        )
        selected_link = bool(
            (road_id, lane_id) in contract.selected_lane_links
            or (
                road_id == contract.selected_exit_road_id
                and lane_id == contract.selected_exit_lane_id
            )
        )
        selected_junction_now = bool(
            is_junction and junction_id == contract.selected_junction_identity
        )
        alternative_executable = not bool(
            selected_link
            and (selected_junction_now or execution.entered_selected_junction)
        )
        return LiveManeuverObservation(
            observation_id=str(self._latest_observation_id),
            frame_id=int(self._latest_frame),
            monotonic_s=time.monotonic(),
            simulation_time_s=float(self._latest_simulation_time),
            ego_x_m=float(location.x),
            ego_y_m=float(location.y),
            road_id=road_id,
            lane_id=lane_id,
            is_junction=is_junction,
            junction_identity=junction_id,
            route_version=route_version,
            environment_digest=environment_digest,
            global_destination_identity=mission.global_destination_identity,
            navigation_context_identity=identity.navigation_context_identity,
            safety_certificate_status=self._method_v2_certificate_status(
                gate.physical_safety.status, physical=True
            ),
            rule_certificate_status=self._method_v2_certificate_status(
                gate.route_local_hard_rule.status, physical=False
            ),
            alternative_topologically_executable=alternative_executable,
        )

    def _method_v2_validate_material_context(self) -> bool:
        """Validate the frozen selected obligation on every active V2 tick.

        The legacy material-trigger observer mutates the pre-answer persistent
        lifecycle, so the bounded execution path cannot call it after answer
        selection.  This read-only validator covers the V2-owned identities;
        route/environment, safety and rule validity are then checked by the
        live physical observation from the same tick.
        """

        execution = self._method_v2_execution
        identity = execution.identity
        candidate = self._method_v2_selected_candidate
        obligation = self._method_v2_selected_obligation

        def invalidate(reason_code: str) -> bool:
            self._material_context_invalidated = True
            execution.invalidate(reason_code, self._latest_frame)
            self._receipt.update(
                {
                    "method_revision_v2_material_context_valid": False,
                    "method_revision_v2_material_invalidation_reason": reason_code,
                }
            )
            return False

        current_prompt = str(
            getattr(self.agent, "custom_prompt", self.raw_instruction)
        )
        if current_prompt != self.raw_instruction:
            return invalidate("NEW_INSTRUCTION_CONFLICT")
        if identity is None or candidate is None or obligation is None:
            execution.terminate_unknown(
                "METHOD_V2_SELECTED_CONTEXT_MISSING", self._latest_frame
            )
            return False
        active_obligation = self._candidate_local_navigation_obligations.get(
            identity.candidate_id
        )
        if active_obligation is not obligation:
            return invalidate("SELECTED_OBLIGATION_OWNER_CHANGED")
        if not (
            str(candidate.candidate_id) == identity.candidate_id
            and str(candidate.interpretation_id) == identity.interpretation_id
            and obligation.candidate_id == identity.candidate_id
            and obligation.interpretation_id == identity.interpretation_id
            and obligation.obligation_identity == identity.obligation_identity
            and obligation.obligation_digest == identity.obligation_digest
            and obligation.local_branch_identity == identity.branch_identity
            and obligation.branch_digest == identity.branch_digest
            and obligation.global_destination_identity
            == identity.global_destination_identity
            and obligation.mission_context_digest == identity.mission_context_digest
        ):
            return invalidate("SELECTED_NAVIGATION_IDENTITY_CHANGED")
        mission = self._candidate_local_navigation_mission
        if mission is None:
            execution.terminate_unknown(
                "METHOD_V2_MISSION_CONTEXT_MISSING", self._latest_frame
            )
            return False
        try:
            mission.assert_endpoint_unchanged()
        except Exception:
            return invalidate("GLOBAL_DESTINATION_ENDPOINT_CHANGED")
        if not (
            mission.global_destination_identity
            == identity.global_destination_identity
            and mission.mission_context_digest == identity.mission_context_digest
        ):
            return invalidate("GLOBAL_DESTINATION_CONTEXT_CHANGED")
        self._receipt["method_revision_v2_material_context_valid"] = True
        return True

    def _method_v2_6_live_landing_evidence(
        self, observation: LiveManeuverObservation
    ) -> Any:
        execution = self._method_v2_execution
        if not isinstance(execution, GenericDownstreamLandingManeuverExecution):
            raise RuntimeError("METHOD_V2_6_EXECUTION_OWNER_MISMATCH")
        owner = execution.downstream_landing_identity
        if owner is None:
            raise RuntimeError("METHOD_V2_6_DOWNSTREAM_OWNER_MISSING")
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            world = CarlaDataProvider.get_world()
            hero = CarlaDataProvider.get_hero_actor()
            map_object = world.get_map()
            waypoint = map_object.get_waypoint(hero.get_location())
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as error:
            raise RuntimeError("METHOD_V2_6_LIVE_TOPOLOGY_UNKNOWN") from error
        return build_live_downstream_landing_evidence(
            owner,
            observation,
            map_object,
            waypoint,
        )

    def _method_v3_reconcile_connector_phase_r2(self) -> None:
        """Advance the sole physical phase owner from current V2.6 evidence."""

        if not getattr(self, "_method_v3_enabled", False):
            return
        if self._method_v3_selected_route_binding is None:
            return
        planner = getattr(self.agent, "_route_planner", None)
        if planner is None:
            raise RuntimeError("METHOD_V3_CONNECTOR_PHASE_ROUTE_OWNER_UNKNOWN")
        binding, _, _, state, context_validation = (
            self._method_v3_connector_context_r2(
                planner.route,
                live_connector_relation="NOT_OBSERVED_DURING_V2_RECONCILIATION",
            )
        )
        if not context_validation.valid:
            raise RuntimeError(
                "METHOD_V3_CONNECTOR_PHASE_CONTEXT_INVALID:"
                + str(context_validation.reason)
            )
        execution = self._method_v2_execution
        physical_row = None
        rows = getattr(execution, "physical_tick_evidence", ())
        if rows and rows[-1].get("frame_id") == int(self._latest_frame):
            physical_row = rows[-1]
        target_rows = getattr(execution, "connector_target_window_evidence", ())
        target_row = None
        if target_rows and target_rows[-1].get("frame_id") == int(
            self._latest_frame
        ):
            target_row = target_rows[-1]
        if (
            physical_row is not None
            and target_row is not None
            and target_row.get("selected_connector_membership") is True
            and not (
                physical_row.get("entered_selected_junction") is True
                and physical_row.get("selected_lane_link_match") is True
            )
        ):
            raise RuntimeError("METHOD_V3_R2_V2_ENTRY_RECONCILIATION_FAILED")
        connector_phase = self._method_v3_connector_phase_owner.reconcile_v2_execution(
            execution_state=execution.state,
            physical_tick_evidence=physical_row,
            frame_id=int(self._latest_frame),
            route_transaction_identity=binding.route_transaction_identity,
            installed_route_identity=binding.installed_route_identity,
            route_generation=int(state.active_route_generation),
            installed_route_catalog_digest=(
                binding.target_window_route_catalog_digest
            ),
        )
        if (
            connector_phase is ConnectorPhysicalPhaseR2.TERMINAL_FAIL_CLOSED
            and self._method_v3_phase_owner.phase
            in {
                MethodV3Phase.SELECTED_ACTIVE_PRECOMMIT,
                MethodV3Phase.COMMITTED,
            }
        ):
            self._method_v3_phase_owner.transition(
                MethodV3Phase.TERMINAL_FAIL_CLOSED,
                "V2_EXECUTION_FAIL_CLOSED_RECONCILED_BY_R2",
            )
        self._receipt["method_v3_connector_phase_owner_r2"] = (
            self._method_v3_connector_phase_owner.summary()
        )
        self._receipt["method_v3_phase_owner"] = (
            self._method_v3_phase_owner.summary()
        )

    def _method_v2_handle_live_tick(self, frame: Any) -> None:
        execution = self._method_v2_execution
        previous_state = execution.state
        material_context_valid = self._method_v2_validate_material_context()
        shared_completed = bool(
            material_context_valid and self.shared_act.observe_tick(frame=frame)
        )
        if shared_completed:
            # Preserve SharedActCommitV1's immutable one-tick receipt semantics;
            # V2 owns a separate semantic lifetime through normal model inputs.
            self.shared_act.prepare_next_window()
            self._authority_mode = "BOUNDED_UNIQUE"
            self._receipt[
                "method_revision_v2_old_one_tick_receipt_consumed_without_release"
            ] = True
        if material_context_valid and execution.state in (
            ManeuverExecutionState.ACTIVE,
            ManeuverExecutionState.MANEUVER_COMMITTED,
        ):
            live_observation = self._method_v2_live_observation()
            execution_observation = live_observation
            if execution.state is ManeuverExecutionState.ACTIVE:
                budget_row = self._method_v2_3_update_active_execution_budget(
                    live_observation
                )
                if (
                    getattr(self, "_method_v2_4_enabled", False)
                    and budget_row is not None
                    and execution.state is ManeuverExecutionState.ACTIVE
                ):
                    budget = self._method_v2_1_execution_budget
                    assert budget is not None and execution.contract is not None
                    compatibility_monotonic = (
                        execution.contract.activation_deadline_monotonic_s
                        - budget.total_budget_s
                        + budget.consumed_budget_s
                    )
                    if budget.exhausted:
                        compatibility_monotonic = math.nextafter(
                            execution.contract.activation_deadline_monotonic_s,
                            math.inf,
                        )
                    execution_observation = replace(
                        live_observation,
                        monotonic_s=compatibility_monotonic,
                    )
                    budget_row[
                        "execution_deadline_compatibility_monotonic_s"
                    ] = compatibility_monotonic
                    budget_row["execution_budget_exhausted"] = budget.exhausted
                    self._receipt[
                        "method_v2_3_active_execution_budget_update"
                    ] = budget_row
            if execution.state in (
                ManeuverExecutionState.ACTIVE,
                ManeuverExecutionState.MANEUVER_COMMITTED,
            ):
                if getattr(self, "_method_v2_6_enabled", False):
                    assert isinstance(
                        execution, GenericDownstreamLandingManeuverExecution
                    )
                    try:
                        downstream_evidence = (
                            self._method_v2_6_live_landing_evidence(
                                live_observation
                            )
                        )
                    except Exception as error:
                        execution.terminate_unknown(
                            "METHOD_V2_6_LIVE_TOPOLOGY_UNKNOWN",
                            self._latest_frame,
                        )
                        self._receipt["method_v2_6_live_topology_error"] = {
                            "type": type(error).__name__,
                            "message": str(error),
                        }
                    else:
                        execution.observe_with_downstream_landing(
                            execution_observation,
                            downstream_evidence,
                        )
                else:
                    execution.observe(execution_observation)
                if (
                    getattr(self, "_method_v2_4_enabled", False)
                    and execution.physical_tick_evidence
                    and execution.physical_tick_evidence[-1].get("frame_id")
                    == int(live_observation.frame_id)
                ):
                    evidence_row = execution.physical_tick_evidence[-1]
                    evidence_row["execution_clock_predicate_input_s"] = (
                        evidence_row["monotonic_s"]
                    )
                    evidence_row["monotonic_s"] = live_observation.monotonic_s
                    evidence_row["execution_clock_owner"] = (
                        "SIMULATION_EXECUTION_OPPORTUNITY"
                    )
                    evidence_row["execution_opportunity_elapsed_s"] = (
                        self._method_v2_1_execution_budget.consumed_budget_s
                    )
                    if getattr(self, "_method_v2_5_enabled", False):
                        identity = execution.identity
                        assert identity is not None
                        evidence_row.update(
                            {
                                "selected_obligation_identity": (
                                    identity.obligation_identity
                                ),
                                "selected_branch_identity": (
                                    identity.branch_identity
                                ),
                                "selected_navigation_identity": (
                                    identity.navigation_context_identity
                                ),
                                "completion_fixed_wait_dependency": False,
                            }
                        )
        self._method_v3_reconcile_connector_phase_r2()
        if (
            previous_state is ManeuverExecutionState.ACTIVE
            and execution.state is ManeuverExecutionState.MANEUVER_COMMITTED
        ):
            self._method_v2_timing.update(
                {
                    "maneuver_committed_monotonic_s": time.monotonic(),
                    "maneuver_committed_frame_id": self._latest_frame,
                    "maneuver_committed_simulation_time_s": (
                        self._latest_simulation_time
                    ),
                }
            )
            if getattr(self, "_method_v2_5_enabled", False):
                self._receipt["method_revision_v2_status"] = (
                    "MANEUVER_COMMITTED_SELECTED_NAVIGATION_CONTINUES"
                )
            if getattr(self, "_method_v3_enabled", False):
                if (
                    self._method_v3_phase_owner.phase
                    is MethodV3Phase.SELECTED_ACTIVE_PRECOMMIT
                ):
                    self._method_v3_phase_owner.transition(
                        MethodV3Phase.COMMITTED,
                        "SELECTED_BRANCH_PHYSICAL_COMMITMENT_OBSERVED",
                    )
                self._receipt["method_v3_phase_owner"] = (
                    self._method_v3_phase_owner.summary()
                )
        completion_handover = bool(
            getattr(self, "_method_v2_5_enabled", False)
        )
        handover_transition = bool(
            (
                completion_handover
                and previous_state is ManeuverExecutionState.MANEUVER_COMMITTED
                and execution.state is ManeuverExecutionState.MANEUVER_COMPLETED
            )
            or (
                not completion_handover
                and previous_state is ManeuverExecutionState.ACTIVE
                and execution.state is ManeuverExecutionState.MANEUVER_COMMITTED
            )
        )
        if handover_transition and getattr(self, "_method_v3_enabled", False):
            if self._method_v3_phase_owner.phase is MethodV3Phase.COMMITTED:
                self._method_v3_phase_owner.transition(
                    MethodV3Phase.COMPLETED,
                    "SELECTED_BRANCH_DOWNSTREAM_LANDING_COMPLETED",
                )
            if self._method_v3_phase_owner.phase is MethodV3Phase.COMPLETED:
                self._method_v3_phase_owner.transition(
                    MethodV3Phase.RECONNECTED,
                    "PREINSTALLED_SELECTED_ROUTE_ALREADY_CONTINUES_TO_ORIGINAL_DESTINATION",
                )
            self._method_v2_reconnect_frame = int(self._latest_frame)
            self._method_v2_timing.update(
                {
                    "maneuver_completed_monotonic_s": time.monotonic(),
                    "maneuver_completed_frame_id": self._latest_frame,
                    "maneuver_completed_simulation_time_s": (
                        self._latest_simulation_time
                    ),
                    "global_reconnect_completed_monotonic_s": time.monotonic(),
                    "global_reconnect_frame_id": self._latest_frame,
                }
            )
            self._receipt.update(
                {
                    "method_revision_v2_status": (
                        "MANEUVER_COMPLETED_PREBOUND_ROUTE_RECONNECTED"
                    ),
                    "global_route_reconnection_native_installation_validated": True,
                    "global_route_reconnection_next_tick_consumed": bool(
                        self._method_v3_selected_route_binding is not None
                        and self._method_v3_selected_route_binding.next_tick_consumed_route_identity
                        == self._method_v3_selected_route_binding.installed_route_identity
                    ),
                    "method_v3_phase_owner": (
                        self._method_v3_phase_owner.summary()
                    ),
                }
            )
            handover_transition = False
        if handover_transition:
            obligation = self._method_v2_selected_obligation
            if obligation is None:
                execution.terminate_unknown(
                    "METHOD_V2_COMPLETION_RELEASE_CONTEXT_MISSING",
                    self._latest_frame,
                )
            else:
                try:
                    handover_now = time.monotonic()
                    handover_endpoint = self._current_live_ego_planner_endpoint()
                    if completion_handover:
                        self._receipt[
                            "method_v2_5_completion_reconnect_origin_xyz_m"
                        ] = [
                            float(handover_endpoint.x),
                            float(handover_endpoint.y),
                            float(handover_endpoint.z),
                        ]
                    request = self.notify_candidate_local_branch_committed(
                        obligation.candidate_id,
                        handover_endpoint,
                        nominal_route_changed=bool(
                            obligation.changes_nominal_route
                        ),
                    )
                    self.perform_global_route_reconnection(request)
                    if not self._receipt.get(
                        "global_route_reconnection_native_installation_validated"
                    ):
                        raise RuntimeError(
                            "METHOD_V2_NATIVE_ROUTE_INSTALLATION_NOT_VALIDATED"
                        )
                    self._method_v2_reconnect_frame = int(self._latest_frame)
                    handover_timing = {
                        "selected_navigation_released_frame_id": (
                            self._latest_frame
                        ),
                        "global_reconnect_completed_monotonic_s": (
                            time.monotonic()
                        ),
                        "global_reconnect_frame_id": self._latest_frame,
                    }
                    if completion_handover:
                        handover_timing.update(
                            {
                                "maneuver_completed_monotonic_s": handover_now,
                                "maneuver_completed_frame_id": self._latest_frame,
                                "maneuver_completed_simulation_time_s": (
                                    self._latest_simulation_time
                                ),
                            }
                        )
                    self._method_v2_timing.update(handover_timing)
                    self._receipt["method_revision_v2_status"] = (
                        "MANEUVER_COMPLETED_RECONNECTED_WAITING_NEXT_TICK_CONSUMPTION"
                        if completion_handover
                        else "MANEUVER_COMMITTED_RECONNECTED_WAITING_NEXT_TICK_CONSUMPTION"
                    )
                except Exception as error:
                    installation_error = self._receipt.get(
                        "simlingo_online_route_update_error"
                    )
                    geometric_failure = bool(
                        isinstance(installation_error, Mapping)
                        and installation_error.get("message")
                        == "FRESH_ROUTE_ORIGIN_DISCONTINUITY"
                    )
                    if completion_handover:
                        self.shared_act.revoke(
                            AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED
                        )
                        self._receipt["status"] = (
                            "POST_COMPLETION_RECONNECT_GEOMETRIC_FAILURE"
                            if geometric_failure
                            else "METHOD_V2_COMPLETION_AWARE_RECONNECT_FAILED"
                        )
                        self._receipt["method_revision_v2_status"] = (
                            self._receipt["status"]
                        )
                        self._terminal = True
                    else:
                        execution.terminate_unknown(
                            "METHOD_V2_COMMITMENT_AWARE_RECONNECT_FAILED",
                            self._latest_frame,
                        )
                    self._receipt["method_revision_v2_reconnect_error"] = {
                        "type": type(error).__name__,
                        "message": str(error),
                    }
        reconnect_frame = self._method_v2_reconnect_frame
        if (
            reconnect_frame is not None
            and int(self._latest_frame) != reconnect_frame
            and execution.state
            is (
                ManeuverExecutionState.MANEUVER_COMPLETED
                if completion_handover
                else ManeuverExecutionState.MANEUVER_COMMITTED
            )
        ):
            evidence = self.refresh_global_route_consumption_evidence()
            if evidence.get("installed_equals_next_tick_consumed") is True:
                self._method_v2_timing.update(
                    {
                        "global_reconnect_consumed_monotonic_s": time.monotonic(),
                        "global_reconnect_consumed_frame_id": self._latest_frame,
                        "global_reconnect_consumed_simulation_time_s": (
                            self._latest_simulation_time
                        ),
                    }
                )
                self._receipt.update(
                    {
                        "status": (
                            "PASS_METHOD_REVISION_V2_ENGINEERING_NATIVE_CLOSED_LOOP"
                        ),
                        "method_revision_v2_status": (
                            "COMPLETION_RECONNECT_INSTALLED_AND_NEXT_TICK_CONSUMED"
                            if completion_handover
                            else "COMMITMENT_RECONNECT_INSTALLED_AND_NEXT_TICK_CONSUMED"
                        ),
                    }
                )
                self._terminal = True
        if execution.fail_closed:
            self.shared_act.revoke(AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED)
            self._receipt.update(
                {
                    "status": "METHOD_REVISION_V2_FAIL_CLOSED",
                    "method_revision_v2_status": execution.state.value,
                }
            )
            self._terminal = True
        self._receipt["method_revision_v2_execution"] = execution.summary()
        self._receipt["method_revision_v2_timing"] = dict(self._method_v2_timing)

    def on_tick(
        self, input_data: Any, tick_data: Any, timestamp: Any, frame: Any, observation_id: Any
    ) -> None:
        try:
            compass = _scalar(tick_data.get("compass"))
            self._latest_navigation_compass_radians = (
                float(compass)
                if compass is not None and math.isfinite(float(compass))
                else None
            )
        except (AttributeError, TypeError, ValueError):
            self._latest_navigation_compass_radians = None
        observation_received = time.monotonic()
        super().on_tick(input_data, tick_data, timestamp, frame, observation_id)
        if (
            _truthy(os.environ.get("DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED"))
            and self._receipt.get("status")
            == "BLOCKED_GROUNDED_TARGET_BINDING_COLLAPSE"
            and self._errors
            and self._errors[-1].get("message")
            == "E1R1_NO_VISUAL_REFERENT_PHRASE"
        ):
            self._errors.pop()
            self._receipt.update(
                {
                    "status": "RQ2_T_2A_EVIDENCE_UNAVAILABLE_UNSUPPORTED_LANGUAGE_FAMILY",
                    "rq2_t_evidence_unavailable_reason": (
                        "CERTIFIED_INSTRUCTION_HAS_NO_VISUAL_REFERENT_PHRASE"
                    ),
                    "rq2_t_measurement_remains_scientifically_valid": True,
                    "rq2_t_unknown_evidence_is_not_zero_imputed": True,
                }
            )
        if getattr(self, "_method_v3_enabled", False):
            native_target_rows = getattr(
                self._method_v2_execution,
                "native_route_target_owner_evidence",
                (),
            )
            target_rows = native_target_rows or getattr(
                self._method_v2_execution,
                "connector_target_window_evidence",
                (),
            )
            if (
                target_rows
                and target_rows[-1].get("frame_id") == self._latest_frame
            ):
                raw_points = getattr(self.agent, "target_points", None)
                try:
                    local_points = [
                        [float(value) for value in point]
                        for point in raw_points
                    ]
                except (TypeError, ValueError):
                    local_points = None
                target_rows[-1]["target_points_ego_local_xy_m"] = local_points
                target_rows[-1]["target_point_placeholder_count"] = (
                    None if local_points is None else len(local_points)
                )
                latest = self._receipt.get(
                    "method_v3_connector_target_window"
                )
                if isinstance(latest, dict):
                    latest["latest"] = dict(target_rows[-1])
        timing_key = str(self._latest_observation_id)
        episode_at_observation_start = (
            self.persistent_store.get(self._episode_id)
            if self._episode_id is not None
            else None
        )
        timing_row = {
            "sequence": len(self._high_fidelity_step_timing_rows) + 1,
            "source_observation_id": timing_key,
            "source_frame_id": self._latest_frame,
            "simulation_timestamp_s": self._latest_simulation_time,
            "observation_received_monotonic_s": observation_received,
            "normal_forward_count": 0,
            "candidate_forward_count": 0,
            "candidate_forwards": [],
            "effective_k": 0,
            "effective_k_at_observation_start": (
                0
                if episode_at_observation_start is None
                else len(episode_at_observation_start.active_candidate_ids)
            ),
            "semantic_state_at_observation_start": (
                None
                if episode_at_observation_start is None
                else episode_at_observation_start.semantic_state.value
            ),
            "query_pending_at_observation_start": bool(
                episode_at_observation_start is not None
                and episode_at_observation_start.active_query is not None
            ),
            "active_query_id_at_observation_start": (
                None
                if episode_at_observation_start is None
                or episode_at_observation_start.active_query is None
                else episode_at_observation_start.active_query.query_id
            ),
            "decision_output": None,
            "model_device": None,
            "model_dtype": None,
            "gpu_allocated_bytes": None,
            "gpu_reserved_bytes": None,
            "gpu_peak_allocated_bytes": None,
            "gpu_peak_reserved_bytes": None,
        }
        self._high_fidelity_model_identity(timing_row)
        self._high_fidelity_gpu_sample(timing_row)
        self._high_fidelity_step_timing_by_observation[timing_key] = timing_row
        self._high_fidelity_step_timing_rows.append(timing_row)
        self._receipt["high_fidelity_per_step_timing_row_count"] = len(
            self._high_fidelity_step_timing_rows
        )
        if (
            getattr(self, "_method_revision_v2_enabled", False)
            and self._method_v2_execution.activation_frame_id is not None
            and not self._terminal
        ):
            try:
                self._method_v2_handle_live_tick(frame)
            except Exception as error:
                self._method_v2_execution.terminate_unknown(
                    "METHOD_V2_LIVE_TICK_EVIDENCE_UNKNOWN", self._latest_frame
                )
                self.shared_act.revoke(
                    AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED
                )
                self._receipt.update(
                    {
                        "status": "METHOD_REVISION_V2_FAIL_CLOSED",
                        "method_revision_v2_status": "EVIDENCE_UNKNOWN",
                        "method_revision_v2_live_tick_error": {
                            "type": type(error).__name__,
                            "message": str(error),
                        },
                        "method_revision_v2_execution": (
                            self._method_v2_execution.summary()
                        ),
                    }
                )
                self._terminal = True
            self._persist()
            return
        if (
            getattr(self, "_online_route_update_native_validation_enabled", False)
            and not getattr(self, "_method_revision_v2_enabled", False)
        ):
            try:
                if self._receipt.get(
                    "global_route_reconnection_native_installation_validated"
                ) and not self._receipt.get(
                    "global_route_reconnection_next_tick_consumed"
                ):
                    self.refresh_global_route_consumption_evidence()
                    self._persist()
                else:
                    self._exercise_online_route_update_native_validation()
            except Exception as exc:
                self._errors.append(
                    {
                        "stage": "R4_4_ONLINE_ROUTE_UPDATE_NATIVE_VALIDATION",
                        "frame": self._latest_frame,
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                self._receipt["status"] = "BLOCKED_R4_4_ONLINE_ROUTE_UPDATE_NATIVE_VALIDATION"
                self._terminal = True
                self._persist()
                return
        if self._episode_id is None or self._terminal:
            return
        try:
            self._observe_material_triggers()
            if self._convergence_observer is not None:
                convergence = self._convergence_observer.observe(
                    image=self._latest_image,
                    frame_id=int(self._latest_frame),
                    observation_id=str(self._latest_observation_id),
                    simulation_time=float(self._latest_simulation_time),
                )
                observer_audit = self._convergence_observer.audit()
                self._receipt[
                    "runtime_grounding_convergence_observer"
                ] = observer_audit
                self._receipt["dino_event_triggered_forward_count"] = int(
                    observer_audit.get("dino_reacquisition_forward_count", 0)
                )
                if convergence is not None:
                    self.observe_candidate_convergence_evidence(convergence)
            self._ingest_tick_convergence_evidence(tick_data)
        except Exception as exc:
            try:
                self._material_invalidate(
                    "MATERIAL_INVALIDATION", "P0_TRIGGER_EVALUATION_UNKNOWN"
                )
            except Exception as invalidation_error:
                self.shared_act.revoke(
                    AuthorityReason.EXECUTION_CONTEXT_UNVERIFIED
                )
                self._errors.append(
                    {
                        "stage": "PERSISTENT_P0_FAIL_CLOSED_INVALIDATION",
                        "frame": self._latest_frame,
                        "type": type(invalidation_error).__name__,
                        "message": str(invalidation_error),
                    }
                )
            self._errors.append(
                {
                    "stage": "PERSISTENT_P0_MATERIAL_TRIGGER",
                    "frame": self._latest_frame,
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            self._receipt["status"] = "BLOCKED_PERSISTENT_P0_MATERIAL_TRIGGER_UNKNOWN"
            self._terminal = True
            self._persist()
            return
        completed = self.shared_act.observe_tick(frame=frame)
        if (
            completed
            and self._authority_mode == "SHARED"
            and not self._shared_window_store_consumed
        ):
            self.persistent_store = self.persistent_store.apply(
                StoreEvent(
                    episode_id=self._episode_id,
                    event_id="{}:shared-window-consumed:{}".format(
                        self._episode_id, self._refresh_sequence
                    ),
                    event_type="SHARED_WINDOW_CONSUMED",
                    observed_monotonic_time=time.monotonic(),
                    source_frame_id=frame,
                    reason_code="ACT_RECEIPT_CONSUMED_EPISODE_REMAINS_UNRESOLVED",
                    payload={},
                )
            )
            self._shared_window_store_consumed = True
            self._decision = None
            self._receipt.update(
                {
                    "status": "ACT_SHARED_CONSUMED_REFRESH_REQUIRED_UNRESOLVED",
                    "semantic_state": "UNRESOLVED",
                    "semantic_resolution_from_act_shared": False,
                    "next_normal_planning_event_requires_fresh_bundle": True,
                    "baseline_ownership_restored": self.shared_act.baseline_restored,
                    "shared_act": dict(self.shared_act.summary()),
                }
            )
            self.shared_act.prepare_next_window()
            self._authority_mode = None
        elif completed and self._authority_mode == "UNIQUE":
            self._receipt.update(
                {
                    "status": "PASS_PERSISTENT_POST_ANSWER_UNIQUE_CLOSED_LOOP",
                    "semantic_state": "RESOLVED",
                    "unique_authority_control_tick_observed": True,
                    "baseline_ownership_restored": self.shared_act.baseline_restored,
                }
            )
            self._terminal = True
        self._persist()

    def _material_invalidate(self, event_type: str, reason_code: str) -> None:
        assert self._episode_id is not None
        episode = self.persistent_store.get(self._episode_id)
        self.shared_act.revoke(AuthorityReason.CANDIDATE_INVALIDATED)
        if getattr(self, "_method_revision_v2_enabled", False):
            self._method_v2_execution.invalidate(
                "MATERIAL_CONTEXT_INVALIDATED:" + str(reason_code),
                self._latest_frame,
            )
        now = time.monotonic()
        self.persistent_store = self.persistent_store.apply(
            StoreEvent(
                episode_id=self._episode_id,
                event_id="{}:material:{}:{}".format(
                    self._episode_id, reason_code, self._latest_frame
                ),
                event_type=event_type,
                observed_monotonic_time=now,
                source_frame_id=self._latest_frame,
                reason_code=reason_code,
                payload={},
            )
        )
        if episode.active_query is not None and self._m3_state is not None:
            self._reduce_m3(
                _m3_event(
                    event_id=episode.active_query.query_id + ":world-change",
                    event_type=EventType.WORLD_STATE_CHANGED,
                    now=now,
                    query_id=episode.active_query.query_id,
                    simulation_time=self._latest_simulation_time,
                )
            )
            self.wait.observe_tick(
                frame=self._latest_frame,
                current_monotonic_time=now,
                ego_position=self._latest_position,
                ego_speed_mps=self._latest_speed,
                baseline_control_path_healthy=True,
                source_runtime_valid=False,
                environment_digest=self._runtime_environment_digest,
                source_simulation_time=self._latest_simulation_time,
            )
        self._receipt.update(
            {
                "status": "PERSISTENT_MATERIAL_INVALIDATION_REFRESH_REQUIRED",
                "last_material_invalidation_reason": reason_code,
                "authority_revoked_before_stale": True,
            }
        )

    def _observe_material_triggers(self) -> None:
        assert self._episode_id is not None
        episode = self.persistent_store.get(self._episode_id)
        current_prompt = str(getattr(self.agent, "custom_prompt", self.raw_instruction))
        if current_prompt != self.raw_instruction:
            self._material_context_invalidated = True
            self._material_invalidate(
                "NEW_INSTRUCTION_CONFLICT", "NEW_INSTRUCTION_CONFLICT"
            )
            return
        route_rows = self.topology_enumerator._route_rows(self._route())
        current_route_version = DecisionWindowEvidenceAdapter.route_version_id(
            [[float(x), float(y)] for x, y, _ in route_rows],
            source="AGENT_OWNED_DENSE_CARLA_WORLD_ROUTE",
        )
        map_object = _live_map()
        current_environment = canonical_sha256(
            {
                "map": str(getattr(map_object, "name", "UNKNOWN_LIVE_MAP")),
                "route_version": current_route_version,
            }
        )
        if (
            current_route_version != self._runtime_route_version
            or current_environment != self._runtime_environment_digest
        ):
            self._material_context_invalidated = True
            self._receipt.update(
                {
                    "invalidated_runtime_route_version": current_route_version,
                    "invalidated_runtime_environment_digest": current_environment,
                }
            )
            self._material_invalidate(
                "ROUTE_OR_ENV_OR_WORLD_CHANGE", "ROUTE_CONTEXT_CHANGED"
            )
            return
        action = episode.current_shared_executable_action
        now = time.monotonic()
        refresh_deadline = episode.freshness.freshness_deadline_monotonic
        if (
            episode.evidence_state is EvidenceState.FRESH
            and refresh_deadline is not None
            and now >= float(refresh_deadline)
        ):
            if action is not None:
                self.shared_act.revoke(AuthorityReason.RECEIPT_EXPIRED)
            event_type = (
                "SHARED_WINDOW_EXPIRED_UNUSED"
                if action is not None
                else "EVIDENCE_REFRESH_DEADLINE_EXPIRED"
            )
            self.persistent_store = self.persistent_store.apply(
                StoreEvent(
                    episode_id=self._episode_id,
                    event_id="{}:evidence-expired:{}".format(
                        self._episode_id, self._latest_frame
                    ),
                    event_type=event_type,
                    observed_monotonic_time=now,
                    source_frame_id=self._latest_frame,
                    reason_code=(
                        "SHARED_ACTION_WINDOW_EXPIRED"
                        if action is not None
                        else "AMBIGUITY_EPISODE_STALE"
                    ),
                    payload={},
                )
            )
            self._receipt["authority_revoked_before_stale"] = True
            self._receipt.update(
                {
                    "status": "PERSISTENT_EVIDENCE_EXPIRED_REFRESH_REQUIRED",
                    "next_normal_planning_event_requires_fresh_bundle": True,
                    "expired_freshness_deadline_monotonic": refresh_deadline,
                }
            )
            return
        if action is not None:
            observation = self._runtime_window_observation(now)
            current_safety = (
                observation.current_physical_safety_gate
                if self._decision_evidence_v3_enabled
                else observation.dynamic_safety_gate
            )
            if not current_safety or not observation.hard_rule_gate:
                self._material_invalidate(
                    "MATERIAL_INVALIDATION", "DYNAMIC_SAFETY_OR_RULE_STATE_CHANGED"
                )
                return
            earliest_commitment = min(
                observation.candidate_commitment_progress_m.values()
            )
            if observation.current_progress_m >= earliest_commitment:
                self.shared_act.revoke(AuthorityReason.CANDIDATE_INVALIDATED)
                self.persistent_store = self.persistent_store.apply(
                    StoreEvent(
                        episode_id=self._episode_id,
                        event_id="{}:commitment-passed:{}".format(
                            self._episode_id, self._latest_frame
                        ),
                        event_type="MATERIAL_DIVERGENCE",
                        observed_monotonic_time=now,
                        source_frame_id=self._latest_frame,
                        reason_code="PASSED_DECISION_POINT",
                        payload={},
                    )
                )
                self._receipt["authority_revoked_before_stale"] = True

    def _update_terminal_status(self) -> None:
        # ACT_SHARED consumption is explicitly non-terminal.  Engineering
        # failures still fail closed and are surfaced by the P1 handler.
        if not hasattr(self, "shared_act"):
            return
        status = str(self.shared_act.summary().get("status", ""))
        if status.startswith("BLOCKED_"):
            self._receipt["status"] = "BLOCKED_PERSISTENT_SHARED_AUTHORITY"
            self._terminal = True

    def _persist(self) -> None:
        active_timing_id = getattr(
            self, "_high_fidelity_active_model_observation_id", None
        )
        if active_timing_id is not None:
            timing_row = self._high_fidelity_step_timing_by_observation.get(
                active_timing_id
            )
            if timing_row is not None and timing_row.get(
                "decision_complete_monotonic_s"
            ) is None:
                decision_complete = time.monotonic()
                timing_row["decision_complete_monotonic_s"] = decision_complete
                decision_started = timing_row.get("decision_stage_start_monotonic_s")
                timing_row["decision_latency_s"] = (
                    None
                    if decision_started is None
                    else decision_complete - float(decision_started)
                )
                episode = (
                    self.persistent_store.get(self._episode_id)
                    if self._episode_id is not None
                    else None
                )
                timing_row["effective_k"] = (
                    len(episode.active_candidate_ids)
                    if episode is not None
                    and episode.semantic_state.value == "UNRESOLVED"
                    else 0
                )
                timing_row["semantic_state_at_decision"] = (
                    None if episode is None else episode.semantic_state.value
                )
                timing_row["query_pending_at_decision"] = bool(
                    episode is not None and episode.active_query is not None
                )
                timing_row["active_query_id_at_decision"] = (
                    None
                    if episode is None or episode.active_query is None
                    else episode.active_query.query_id
                )
                decision_view = getattr(self, "_decision", None)
                timing_row["decision_output"] = (
                    None
                    if decision_view is None
                    else decision_view.to_dict().get("decision")
                )
                self._high_fidelity_gpu_sample(timing_row)
            self._high_fidelity_active_model_observation_id = None
        if hasattr(self, "persistent_store") and self._episode_id is not None:
            episode = self.persistent_store.get(self._episode_id)
            contract_episode = episode.to_contract_dict()
            ContractValidator().validate("persistent_episode", contract_episode)
            self._receipt["persistent_ambiguity_episode"] = contract_episode
            self._receipt["persistent_ambiguity_episode_internal_debug"] = asdict(
                episode
            )
            self._receipt["persistent_episode_contract_validated"] = True
            self._receipt["semantic_state"] = episode.semantic_state.value
            self._receipt["evidence_state"] = episode.evidence_state.value
            self._receipt["active_candidate_ids"] = list(episode.active_candidate_ids)
        if hasattr(self, "shared_act"):
            self._receipt["shared_act"] = dict(self.shared_act.summary())
            self._receipt["m3_act_transactions"] = list(self._m3_act_transactions)
        if hasattr(self, "_method_decision_history"):
            self._receipt["method_v1_decision_history"] = list(
                self._method_decision_history
            )
            self._receipt["method_v1_m3_transactions"] = list(
                self._method_m3_transactions
            )
            self._receipt["method_v1_decision_envelope"] = (
                None
                if self._method_decision_envelope is None
                else self._method_decision_envelope.to_dict()
            )
        if hasattr(self, "_method_v2_execution"):
            self._receipt["method_revision_v2_execution"] = (
                self._method_v2_execution.summary()
            )
            self._receipt["method_revision_v2_timing"] = dict(
                self._method_v2_timing
            )
        if getattr(self, "_method_v2_3_enabled", False):
            self._receipt["method_v2_3_authority_mode"] = self._authority_mode
            self._receipt[
                "method_v2_3_active_execution_deadline_monotonic_s"
            ] = self._method_v2_3_active_execution_deadline_monotonic
        super()._persist()

    def close(self) -> None:
        """Flush audit buffers once after the final control return."""

        artifact_error: Optional[dict[str, str]] = None
        try:
            for row in self._high_fidelity_step_timing_rows:
                returned = row.get("final_control_return_monotonic_s")
                received = row.get("observation_received_monotonic_s")
                row["observation_to_final_control_return_latency_s"] = (
                    None
                    if returned is None or received is None
                    else float(returned) - float(received)
                )
            _atomic_high_fidelity_jsonl(
                self.output_dir / HIGH_FIDELITY_TIMING_FILENAME,
                self._high_fidelity_step_timing_rows,
            )
            _atomic_high_fidelity_json(
                self.output_dir / HIGH_FIDELITY_LEDGER_FILENAME,
                {
                    "schema_version": (
                        "driveclarify.high_fidelity_runtime."
                        "candidate_forward_ledger.v1"
                    ),
                    "rows": self._candidate_forward_ledger,
                },
            )
            _atomic_high_fidelity_json(
                self.output_dir / HIGH_FIDELITY_TRAJECTORY_FILENAME,
                {
                    "schema_version": (
                        "driveclarify.high_fidelity_runtime."
                        "candidate_trajectory_history.v1"
                    ),
                    "rows": self._candidate_trajectory_history,
                },
            )
        except (OSError, TypeError, ValueError) as error:
            artifact_error = {
                "type": type(error).__name__,
                "message": str(error),
            }
        self._receipt.update(
            {
                "high_fidelity_per_step_timing_row_count": len(
                    self._high_fidelity_step_timing_rows
                ),
                "candidate_forward_ledger_row_count": len(
                    self._candidate_forward_ledger
                ),
                "candidate_trajectory_history_row_count": len(
                    self._candidate_trajectory_history
                ),
                "high_fidelity_artifacts_flushed_after_control_loop": (
                    artifact_error is None
                ),
                "high_fidelity_artifact_error": artifact_error,
            }
        )
        if self._rq2_t_temporal_observer is not None:
            try:
                commitment_time = self._method_v2_timing.get(
                    "maneuver_committed_simulation_time_s"
                )
                owner_mode = _truthy(
                    os.environ.get("DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED")
                )
                terminal_state = None
                terminal_elapsed = None
                if owner_mode:
                    probe = getattr(self.agent, "_dc_probe", None)
                    terminal_reader = getattr(
                        probe, "scientific_execution_terminal", None
                    )
                    terminal_event = (
                        terminal_reader() if callable(terminal_reader) else None
                    )
                    if isinstance(terminal_event, dict):
                        terminal_state = terminal_event.get("state")
                        terminal_elapsed = terminal_event.get(
                            "simulation_elapsed_s"
                        )
                        if (
                            terminal_state == "COMMITMENT_OBSERVED"
                            and terminal_elapsed is not None
                        ):
                            commitment_time = float(terminal_elapsed)
                    if terminal_state is None:
                        # Unknown launcher/watchdog/crash closure is explicitly
                        # engineering-invalid, never scientific censoring.
                        terminal_state = "MISSING_EXECUTION_TERMINAL_EVENT"
                observer_receipt = self._rq2_t_temporal_observer.close(
                    commitment_time_simulation_s=(
                        None if commitment_time is None else float(commitment_time)
                    ),
                    commitment_reached=commitment_time is not None,
                    right_censored=(
                        commitment_time is None and not owner_mode
                    ),
                    execution_terminal_state=terminal_state,
                    simulation_elapsed_s=terminal_elapsed,
                    wall_timeout_observed=False,
                )
                self._receipt["rq2_t_temporal_observer_receipt"] = observer_receipt
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                self._rq2_t_temporal_observer_errors.append(
                    {"type": type(error).__name__, "message": str(error)}
                )
        self._receipt.update(
            {
                "rq2_t_temporal_observer_enabled": (
                    self._rq2_t_temporal_observer is not None
                ),
                "rq2_t_temporal_observer_errors": list(
                    self._rq2_t_temporal_observer_errors
                ),
                "rq2_t_extra_model_forward_count": 0,
                "rq2_t_extra_planner_advance_count": 0,
                "rq2_t_extra_pid_count": 0,
                "rq2_t_extra_control_writer_count": 0,
            }
        )
        super().close()

    def _high_fidelity_model_identity(self, row: dict[str, Any]) -> None:
        try:
            model = getattr(self.agent, "model")
            parameter = next(model.parameters())
            row["model_device"] = str(parameter.device)
            row["model_dtype"] = str(parameter.dtype)
        except (AttributeError, StopIteration, TypeError):
            row["model_device"] = "UNKNOWN"
            row["model_dtype"] = "UNKNOWN"

    def _high_fidelity_gpu_sample(self, row: dict[str, Any]) -> None:
        try:
            import torch

            if not torch.cuda.is_available():
                return
            device = torch.cuda.current_device()
            row["gpu_allocated_bytes"] = int(torch.cuda.memory_allocated(device))
            row["gpu_reserved_bytes"] = int(torch.cuda.memory_reserved(device))
            row["gpu_peak_allocated_bytes"] = int(
                torch.cuda.max_memory_allocated(device)
            )
            row["gpu_peak_reserved_bytes"] = int(
                torch.cuda.max_memory_reserved(device)
            )
            self._receipt["high_fidelity_gpu_audit"] = {
                "device_index": int(device),
                "peak_allocated_vram_bytes": max(
                    int(self._receipt.get("high_fidelity_gpu_audit", {}).get(
                        "peak_allocated_vram_bytes", 0
                    )),
                    row["gpu_peak_allocated_bytes"],
                ),
                "peak_reserved_vram_bytes": max(
                    int(self._receipt.get("high_fidelity_gpu_audit", {}).get(
                        "peak_reserved_vram_bytes", 0
                    )),
                    row["gpu_peak_reserved_bytes"],
                ),
                "oom_count": self._gpu_oom_count,
            }
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
            return


def build_persistent_ambiguity_runtime(
    agent: Any,
    instruction: str,
    output_dir: str,
    *,
    route_local_evidence_provider: Optional[HardGateEvidenceProvider] = None,
) -> PersistentAmbiguityReferentialRuntime:
    runtime = PersistentAmbiguityReferentialRuntime(
        agent,
        output_dir,
        raw_instruction=str(instruction),
        hard_gate_evidence_provider=route_local_evidence_provider,
    )
    provider = runtime._hard_gate_evidence_provider
    provider_identity = _provider_object_identity(provider)
    binding = _production_provider_binding(provider)
    runtime._receipt.update(
        {
            "production_builder": (
                "driveclarify_persistent_ambiguity_runtime_v1.runtime."
                "build_persistent_ambiguity_runtime"
            ),
            "production_builder_route_local_provider_explicit": (
                route_local_evidence_provider is not None
            ),
            "production_builder_provider_object_identity": provider_identity,
            "production_builder_provider_identity": (
                None if binding is None else binding["provider_id"]
            ),
            "production_builder_runtime_same_provider_object": bool(
                provider is runtime._hard_gate_evidence_provider
                and provider_identity
                == runtime._hard_gate_provider_object_identity
            ),
            "production_construction_binding_status": (
                "PASS_PRODUCTION_PROVIDER_IDENTITY_BOUND"
                if binding is not None
                and binding["provider_object_identity"] == provider_identity
                else "UNKNOWN_FAIL_CLOSED_PROVIDER_MISSING_OR_UNBOUND"
            ),
        }
    )
    runtime._persist()
    return runtime


__all__ = [
    "FEATURE_FLAG",
    "HardGateCertificate",
    "HardGateCertificateStatus",
    "HardGateEvidenceEnvelope",
    "HardGateEvidenceProvider",
    "NativeCarlaRouteLocalEvidenceProvider",
    "PASS_STATUS",
    "PersistentAmbiguityReferentialRuntime",
    "_persistent_decision_history_row",
    "_persistent_decision_window_dashboard",
    "build_persistent_ambiguity_runtime",
]
