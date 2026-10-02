"""Typed enums + structured-result envelope for the Consequence Adapter (CP2 skeleton).

No real risk computation. Types + the UNKNOWN-preserving result envelope only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StrEnum(str, Enum):
    def __str__(self) -> str:  # stable JSON/string form
        return self.value


class ResultStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"
    STALE = "STALE"
    INVALID = "INVALID"


class EvidenceGrade(StrEnum):
    VERIFIED = "VERIFIED_FROM_CONTROLLED_PROBE"
    SUPPORTED = "SUPPORTED_BUT_INCOMPLETE"
    UNRESOLVED = "UNRESOLVED_REQUIRES_ADDITIONAL_PROBE"
    INVALIDATED = "INVALIDATED_BY_CONTROLLED_PROBE"
    NOT_AVAILABLE = "NOT_CURRENTLY_AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Frame(StrEnum):
    MODEL_LOCAL_RAW = "MODEL_LOCAL_RAW"
    EGO_LOCAL_UNCALIBRATED = "EGO_LOCAL_UNCALIBRATED"
    CARLA_WORLD = "CARLA_WORLD"
    CAMERA_FRAME = "CAMERA_FRAME"
    IMAGE_PIXEL = "IMAGE_PIXEL"


class TriValue(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class QueryStatus(StrEnum):
    SUCCESS_NONEMPTY = "SUCCESS_NONEMPTY"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    UNKNOWN_WITH_REASON = "UNKNOWN_WITH_REASON"
    UNSUPPORTED = "UNSUPPORTED"
    EXCEPTION = "EXCEPTION"
    STALE = "STALE"
    MISSING = "MISSING"


class UsagePurpose(StrEnum):
    """The four D03 usage tiers. A value's *purpose* — why it was computed — is explicit; it is
    never inferred. AUTHORIZATION / SAFETY_CRITICAL require VERIFIED evidence; DIAGNOSTIC_ONLY /
    LOGGING_ONLY may use SUPPORTED evidence but can never authorize or feed a hard gate."""

    AUTHORIZATION = "AUTHORIZATION"
    SAFETY_CRITICAL = "SAFETY_CRITICAL"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    LOGGING_ONLY = "LOGGING_ONLY"


# The tiers that may EVER authorize control or feed a hard safety/rule gate.
AUTHORIZING_PURPOSES = frozenset({UsagePurpose.AUTHORIZATION, UsagePurpose.SAFETY_CRITICAL})
# The tiers that are non-authorizing (diagnostics / logging / visualization / shadow only).
NON_AUTHORIZING_PURPOSES = frozenset({UsagePurpose.DIAGNOSTIC_ONLY, UsagePurpose.LOGGING_ONLY})


@dataclass(frozen=True)
class Result:
    """The one structured envelope every consequence field uses.

    Invariants (enforced by ``validate``):
      - AVAILABLE  => value is not None and reason_code is None.
      - non-AVAILABLE => value is None and reason_code is a non-empty string.
    A physical value is therefore impossible to emit without status=AVAILABLE, which the
    dependency gate only grants when all contracts are VERIFIED and the frame/unit is allowed.
    """

    status: ResultStatus
    value: Any = None
    unit: str | None = None
    frame: str | None = None
    evidence_grade: EvidenceGrade = EvidenceGrade.NOT_AVAILABLE
    dependencies: tuple[str, ...] = ()
    reason_code: str | None = None
    source_artifacts: tuple[str, ...] = ()
    # CAP-1 / D03 usage tier fields. usage_purpose states why the value was computed.
    # authorization_eligible / safety_critical_eligible make the tier machine-checkable so a
    # DIAGNOSTIC_ONLY / LOGGING_ONLY value can never be read as authorizing.
    usage_purpose: UsagePurpose | None = None
    authorization_eligible: bool = False
    safety_critical_eligible: bool = False
    clock_domain: str | None = None
    limitations: str | None = None

    def validate(self) -> None:
        if self.status is ResultStatus.AVAILABLE:
            if self.value is None:
                raise ValueError("AVAILABLE_RESULT_REQUIRES_VALUE")
            if self.reason_code is not None:
                raise ValueError("AVAILABLE_RESULT_MUST_NOT_HAVE_REASON")
        else:
            if self.value is not None:
                raise ValueError("NON_AVAILABLE_RESULT_MUST_HAVE_NULL_VALUE")
            if not self.reason_code:
                raise ValueError("NON_AVAILABLE_RESULT_REQUIRES_REASON_CODE")
        # Eligibility invariants (D03): eligibility implies the matching authorizing purpose,
        # AVAILABLE status, and VERIFIED evidence. Non-authorizing purposes can never be eligible.
        if self.authorization_eligible:
            if self.usage_purpose is not UsagePurpose.AUTHORIZATION:
                raise ValueError("AUTH_ELIGIBLE_REQUIRES_AUTHORIZATION_PURPOSE")
            if self.status is not ResultStatus.AVAILABLE:
                raise ValueError("AUTH_ELIGIBLE_REQUIRES_AVAILABLE")
            if self.evidence_grade is not EvidenceGrade.VERIFIED:
                raise ValueError("AUTH_ELIGIBLE_REQUIRES_VERIFIED")
        if self.safety_critical_eligible:
            if self.usage_purpose is not UsagePurpose.SAFETY_CRITICAL:
                raise ValueError("SAFETY_ELIGIBLE_REQUIRES_SAFETY_CRITICAL_PURPOSE")
            if self.status is not ResultStatus.AVAILABLE:
                raise ValueError("SAFETY_ELIGIBLE_REQUIRES_AVAILABLE")
            if self.evidence_grade is not EvidenceGrade.VERIFIED:
                raise ValueError("SAFETY_ELIGIBLE_REQUIRES_VERIFIED")
        if self.usage_purpose in NON_AUTHORIZING_PURPOSES and (
            self.authorization_eligible or self.safety_critical_eligible
        ):
            raise ValueError("NON_AUTHORIZING_PURPOSE_MUST_NOT_BE_ELIGIBLE")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "frame": self.frame,
            "evidence_grade": self.evidence_grade.value,
            "dependencies": list(self.dependencies),
            "reason_code": self.reason_code,
            "source_artifacts": list(self.source_artifacts),
            "usage_purpose": self.usage_purpose.value if self.usage_purpose else None,
            "authorization_eligible": self.authorization_eligible,
            "safety_critical_eligible": self.safety_critical_eligible,
            "clock_domain": self.clock_domain,
            "limitations": self.limitations,
        }


def unknown(reason_code: str, dependencies: tuple[str, ...] = (),
            evidence_grade: EvidenceGrade = EvidenceGrade.NOT_AVAILABLE,
            unit: str | None = None, frame: str | None = None,
            usage_purpose: UsagePurpose | None = None,
            clock_domain: str | None = None, limitations: str | None = None) -> Result:
    """Construct a fail-closed UNKNOWN result. Helper used everywhere a value is not computable.
    Always non-authorizing (eligibility flags stay False)."""
    r = Result(status=ResultStatus.UNKNOWN, value=None, unit=unit, frame=frame,
               evidence_grade=evidence_grade, dependencies=tuple(dependencies),
               reason_code=reason_code, usage_purpose=usage_purpose,
               authorization_eligible=False, safety_critical_eligible=False,
               clock_domain=clock_domain, limitations=limitations)
    r.validate()
    return r


def available(value: Any, unit: str | None, frame: str | None,
              evidence_grade: EvidenceGrade, dependencies: tuple[str, ...] = (),
              source_artifacts: tuple[str, ...] = (),
              usage_purpose: UsagePurpose | None = None,
              clock_domain: str | None = None, limitations: str | None = None) -> Result:
    """Construct an AVAILABLE result. value MUST be non-None (asserted by validate).

    Eligibility is DERIVED, never caller-supplied: a result is authorization/safety eligible only
    when its purpose is the matching authorizing tier AND evidence is VERIFIED. A DIAGNOSTIC_ONLY /
    LOGGING_ONLY value therefore can never present authorization_eligible=true.
    """
    auth_ok = usage_purpose is UsagePurpose.AUTHORIZATION and evidence_grade is EvidenceGrade.VERIFIED
    safety_ok = usage_purpose is UsagePurpose.SAFETY_CRITICAL and evidence_grade is EvidenceGrade.VERIFIED
    r = Result(status=ResultStatus.AVAILABLE, value=value, unit=unit, frame=frame,
               evidence_grade=evidence_grade, dependencies=tuple(dependencies),
               reason_code=None, source_artifacts=tuple(source_artifacts),
               usage_purpose=usage_purpose, authorization_eligible=auth_ok,
               safety_critical_eligible=safety_ok, clock_domain=clock_domain,
               limitations=limitations)
    r.validate()
    return r
