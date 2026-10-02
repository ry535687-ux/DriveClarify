"""Typed, fail-closed evidence values for persistent ambiguity G0.

This module extends the existing consequence ``Result`` semantics with the
provenance fields frozen by the decision-window design.  It is intentionally
pure: importing it cannot import CARLA, a model, a planner, PID code, or a
control writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Optional, Tuple


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class EvidenceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    STALE = "STALE"
    INVALID = "INVALID"


class EvidenceGrade(StrEnum):
    VERIFIED = "VERIFIED_FROM_CONTROLLED_PROBE"
    SUPPORTED = "SUPPORTED_BUT_INCOMPLETE"
    UNRESOLVED = "UNRESOLVED_REQUIRES_ADDITIONAL_PROBE"
    INVALIDATED = "INVALIDATED_BY_CONTROLLED_PROBE"
    NOT_AVAILABLE = "NOT_CURRENTLY_AVAILABLE"


class UsagePurpose(StrEnum):
    AUTHORIZATION = "AUTHORIZATION"
    SAFETY_CRITICAL = "SAFETY_CRITICAL"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    LOGGING_ONLY = "LOGGING_ONLY"


class Visibility(StrEnum):
    RUNTIME_OBSERVABLE = "RUNTIME_OBSERVABLE"
    EVALUATOR_ONLY = "EVALUATOR_ONLY"
    OFFLINE_REPLAY_ONLY = "OFFLINE_REPLAY_ONLY"
    NOT_CURRENTLY_AVAILABLE = "NOT_CURRENTLY_AVAILABLE"


class PlanCoverageStatus(StrEnum):
    COVERED = "COVERED"
    NOT_COVERED = "NOT_COVERED"
    PARTIALLY_COVERED = "PARTIALLY_COVERED"


class CurrentActionRelation(StrEnum):
    CURRENT_ACTION_EQUIVALENT = "CURRENT_ACTION_EQUIVALENT"
    CURRENT_ACTION_DIVERGENT = "CURRENT_ACTION_DIVERGENT"


class CandidateRelationship(StrEnum):
    CONSEQUENCE_EQUIVALENT = "CONSEQUENCE_EQUIVALENT"
    MATERIAL_DIVERGENCE_NOW = "MATERIAL_DIVERGENCE_NOW"
    CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT = (
        "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT"
    )
    UNKNOWN_OR_INSUFFICIENT_EVIDENCE = "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"


class TimeToDivergenceSemantics(StrEnum):
    DIVERGENCE_OBSERVED_IN_CURRENT_PLAN = "DIVERGENCE_OBSERVED_IN_CURRENT_PLAN"
    FUTURE_DIVERGENCE_BOUND_FROM_TOPOLOGY = "FUTURE_DIVERGENCE_BOUND_FROM_TOPOLOGY"
    NOT_YET_OBSERVABLE = "NOT_YET_OBSERVABLE"
    NO_MATERIAL_DIVERGENCE = "NO_MATERIAL_DIVERGENCE"
    UNKNOWN = "UNKNOWN"
    ALREADY_DIVERGENT = "ALREADY_DIVERGENT"
    PASSED_DECISION_POINT = "PASSED_DECISION_POINT"


class Recoverability(StrEnum):
    RECOVERABLE = "RECOVERABLE"
    IRREVERSIBLE = "IRREVERSIBLE"


@dataclass(frozen=True)
class EvidenceProvenance:
    producer: str
    source_kind: str
    source_observation_id: Optional[str]
    source_frame_id: Any
    observed_monotonic_time: Optional[float]
    source_simulation_time: Optional[float]
    coordinate_transform_id: Optional[str]
    visibility: Visibility
    artifact_sha256: Optional[str]
    comparison_context_id: Optional[str]

    def to_dict(self) -> dict:
        return {
            "producer": self.producer,
            "source_kind": self.source_kind,
            "source_observation_id": self.source_observation_id,
            "source_frame_id": self.source_frame_id,
            "observed_monotonic_time": self.observed_monotonic_time,
            "source_simulation_time": self.source_simulation_time,
            "coordinate_transform_id": self.coordinate_transform_id,
            "visibility": self.visibility.value,
            "artifact_sha256": self.artifact_sha256,
            "comparison_context_id": self.comparison_context_id,
        }


@dataclass(frozen=True)
class EvidenceResult:
    status: EvidenceStatus
    value: Any
    unit: Optional[str]
    frame: Optional[str]
    evidence_grade: EvidenceGrade
    dependencies: Tuple[str, ...]
    reason_code: Optional[str]
    source_artifacts: Tuple[str, ...]
    usage_purpose: UsagePurpose
    authorization_eligible: bool
    safety_critical_eligible: bool
    clock_domain: Optional[str]
    limitations: Optional[str]
    provenance: EvidenceProvenance

    @property
    def is_available(self) -> bool:
        return self.status is EvidenceStatus.AVAILABLE

    @property
    def is_runtime_authorizable(self) -> bool:
        return (
            self.is_available
            and self.authorization_eligible
            and self.evidence_grade is EvidenceGrade.VERIFIED
            and self.usage_purpose is UsagePurpose.AUTHORIZATION
            and self.provenance.visibility is Visibility.RUNTIME_OBSERVABLE
        )

    @property
    def is_safety_authorizable(self) -> bool:
        return (
            self.is_available
            and self.safety_critical_eligible
            and self.evidence_grade is EvidenceGrade.VERIFIED
            and self.usage_purpose is UsagePurpose.SAFETY_CRITICAL
            and self.provenance.visibility is Visibility.RUNTIME_OBSERVABLE
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "frame": self.frame,
            "evidence_grade": self.evidence_grade.value,
            "dependencies": list(self.dependencies),
            "reason_code": self.reason_code,
            "source_artifacts": list(self.source_artifacts),
            "usage_purpose": self.usage_purpose.value,
            "authorization_eligible": self.authorization_eligible,
            "safety_critical_eligible": self.safety_critical_eligible,
            "clock_domain": self.clock_domain,
            "limitations": self.limitations,
            "provenance": self.provenance.to_dict(),
        }


def unavailable_provenance(producer: str = "PersistentAmbiguityRuntimeV1.G0") -> EvidenceProvenance:
    return EvidenceProvenance(
        producer=producer,
        source_kind="NOT_CURRENTLY_AVAILABLE",
        source_observation_id=None,
        source_frame_id=None,
        observed_monotonic_time=None,
        source_simulation_time=None,
        coordinate_transform_id=None,
        visibility=Visibility.NOT_CURRENTLY_AVAILABLE,
        artifact_sha256=None,
        comparison_context_id=None,
    )


def unknown_result(
    reason_code: str,
    *,
    dependencies: Iterable[str] = (),
    unit: Optional[str] = None,
    frame: Optional[str] = None,
    clock_domain: Optional[str] = None,
    purpose: UsagePurpose = UsagePurpose.DIAGNOSTIC_ONLY,
    provenance: Optional[EvidenceProvenance] = None,
    status: EvidenceStatus = EvidenceStatus.UNKNOWN,
    grade: EvidenceGrade = EvidenceGrade.UNRESOLVED,
    limitations: Optional[str] = None,
) -> EvidenceResult:
    if status is EvidenceStatus.AVAILABLE:
        raise ValueError("UNKNOWN_RESULT_STATUS_MUST_BE_NON_AVAILABLE")
    return EvidenceResult(
        status=status,
        value=None,
        unit=unit,
        frame=frame,
        evidence_grade=grade,
        dependencies=tuple(dependencies),
        reason_code=reason_code,
        source_artifacts=(),
        usage_purpose=purpose,
        authorization_eligible=False,
        safety_critical_eligible=False,
        clock_domain=clock_domain,
        limitations=limitations,
        provenance=provenance or unavailable_provenance(),
    )


def available_result(
    value: Any,
    *,
    unit: str,
    frame: str,
    clock_domain: str,
    purpose: UsagePurpose,
    provenance: EvidenceProvenance,
    grade: EvidenceGrade = EvidenceGrade.VERIFIED,
    dependencies: Iterable[str] = (),
    source_artifacts: Iterable[str] = (),
    limitations: Optional[str] = None,
) -> EvidenceResult:
    authorization_eligible = (
        purpose is UsagePurpose.AUTHORIZATION
        and grade is EvidenceGrade.VERIFIED
        and provenance.visibility is Visibility.RUNTIME_OBSERVABLE
    )
    safety_critical_eligible = (
        purpose is UsagePurpose.SAFETY_CRITICAL
        and grade is EvidenceGrade.VERIFIED
        and provenance.visibility is Visibility.RUNTIME_OBSERVABLE
    )
    return EvidenceResult(
        status=EvidenceStatus.AVAILABLE,
        value=value,
        unit=unit,
        frame=frame,
        evidence_grade=grade,
        dependencies=tuple(dependencies),
        reason_code=None,
        source_artifacts=tuple(source_artifacts),
        usage_purpose=purpose,
        authorization_eligible=authorization_eligible,
        safety_critical_eligible=safety_critical_eligible,
        clock_domain=clock_domain,
        limitations=limitations,
        provenance=provenance,
    )
