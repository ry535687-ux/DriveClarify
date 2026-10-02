"""Lossless Method V1 decision semantics and runtime convergence evidence.

This module records policy authorization separately from the existing physical
plan/control owner.  It has no model, planner, PID, or VehicleControl API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from driveclarify_m3_minimal_core import (
    EventType,
    MinimalM3Event,
    MinimalM3State,
    reduce_event,
)
from driveclarify_m3_runtime_shadow.limited_act_commit_v0 import (
    _build_frozen_m3_act_result,
)

from .contracts import canonical_sha256


class MethodDecisionLabel(str, Enum):
    ACT = "ACT"
    ACT_SHARED = "ACT_SHARED"
    ASK = "ASK"
    WAIT = "WAIT"
    FALLBACK = "FALLBACK"


class MethodControlSource(str, Enum):
    ORIGINAL_SIMLINGO = "ORIGINAL_SIMLINGO"
    ORIGINAL_SIMLINGO_SHARED_PREFIX = "ORIGINAL_SIMLINGO_SHARED_PREFIX"
    FRESH_UNIQUE_CANDIDATE = "FRESH_UNIQUE_CANDIDATE"
    EXISTING_HOLDING = "EXISTING_HOLDING"
    BASELINE_SIMLINGO = "BASELINE_SIMLINGO"


class MethodAuthorizationStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    QUERY_AUTHORIZED = "QUERY_AUTHORIZED"
    HOLD_AUTHORIZED = "HOLD_AUTHORIZED"
    DEFERRED_PENDING_FRESH_REPLAN = "DEFERRED_PENDING_FRESH_REPLAN"
    FAIL_CLOSED = "FAIL_CLOSED"


M2B_TO_M3_EVENT = {
    MethodDecisionLabel.ACT: EventType.DECISION_ACT,
    MethodDecisionLabel.ACT_SHARED: EventType.DECISION_ACT,
    MethodDecisionLabel.ASK: EventType.DECISION_ASK,
    MethodDecisionLabel.WAIT: EventType.DECISION_WAIT,
    MethodDecisionLabel.FALLBACK: EventType.DECISION_FALLBACK,
}


@dataclass(frozen=True)
class MethodDecisionEnvelope:
    decision_label: MethodDecisionLabel
    decision_reason: str
    ambiguity_state: str
    effective_K: int
    relationship: str
    decision_subject: str
    control_source: MethodControlSource
    authorization_status: MethodAuthorizationStatus
    freshness: str
    source_planning_event: str
    candidate_bundle_version: Optional[str]
    authority_subject: str
    query_active: bool
    answer_pending: bool
    valid_holding_authorization: bool
    decision_digest: str

    @classmethod
    def create(
        cls,
        *,
        decision_label: MethodDecisionLabel,
        decision_reason: str,
        ambiguity_state: str,
        effective_K: int,
        relationship: str,
        decision_subject: str,
        control_source: MethodControlSource,
        authorization_status: MethodAuthorizationStatus,
        freshness: str,
        source_planning_event: str,
        candidate_bundle_version: Optional[str],
        authority_subject: str,
        query_active: bool = False,
        answer_pending: bool = False,
        valid_holding_authorization: bool = False,
    ) -> "MethodDecisionEnvelope":
        if not isinstance(decision_label, MethodDecisionLabel):
            raise TypeError("METHOD_DECISION_LABEL_INVALID")
        if type(effective_K) is not int or effective_K < 0:
            raise ValueError("METHOD_EFFECTIVE_K_INVALID")
        required = (
            decision_reason,
            ambiguity_state,
            relationship,
            decision_subject,
            freshness,
            source_planning_event,
            authority_subject,
        )
        if any(type(value) is not str or not value for value in required):
            raise ValueError("METHOD_DECISION_ENVELOPE_STRING_INVALID")
        if any(
            type(value) is not bool
            for value in (
                query_active,
                answer_pending,
                valid_holding_authorization,
            )
        ):
            raise TypeError("METHOD_DECISION_LIFECYCLE_FLAG_INVALID")
        if decision_label is MethodDecisionLabel.WAIT and not (
            query_active and answer_pending and valid_holding_authorization
        ):
            raise ValueError("METHOD_WAIT_SEMANTIC_INVARIANT_VIOLATED")
        payload = {
            "schema_version": "driveclarify.method_v1.decision_envelope.v1",
            "decision_label": decision_label.value,
            "decision_reason": decision_reason,
            "ambiguity_state": ambiguity_state,
            "effective_K": effective_K,
            "relationship": relationship,
            "decision_subject": decision_subject,
            "control_source": control_source.value,
            "authorization_status": authorization_status.value,
            "freshness": freshness,
            "source_planning_event": source_planning_event,
            "candidate_bundle_version": candidate_bundle_version,
            "authority_subject": authority_subject,
            "query_active": query_active,
            "answer_pending": answer_pending,
            "valid_holding_authorization": valid_holding_authorization,
        }
        return cls(
            decision_label=decision_label,
            decision_reason=decision_reason,
            ambiguity_state=ambiguity_state,
            effective_K=effective_K,
            relationship=relationship,
            decision_subject=decision_subject,
            control_source=control_source,
            authorization_status=authorization_status,
            freshness=freshness,
            source_planning_event=source_planning_event,
            candidate_bundle_version=candidate_bundle_version,
            authority_subject=authority_subject,
            query_active=query_active,
            answer_pending=answer_pending,
            valid_holding_authorization=valid_holding_authorization,
            decision_digest=canonical_sha256(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "driveclarify.method_v1.decision_envelope.v1",
            "decision_label": self.decision_label.value,
            "decision_reason": self.decision_reason,
            "ambiguity_state": self.ambiguity_state,
            "effective_K": self.effective_K,
            "relationship": self.relationship,
            "decision_subject": self.decision_subject,
            "control_source": self.control_source.value,
            "authorization_status": self.authorization_status.value,
            "freshness": self.freshness,
            "source_planning_event": self.source_planning_event,
            "candidate_bundle_version": self.candidate_bundle_version,
            "authority_subject": self.authority_subject,
            "query_active": self.query_active,
            "answer_pending": self.answer_pending,
            "valid_holding_authorization": self.valid_holding_authorization,
            "decision_digest": self.decision_digest,
        }


class ConvergenceEvidenceKind(str, Enum):
    RUNTIME_GROUNDING = "RUNTIME_GROUNDING"
    RUNTIME_OBSERVATION = "RUNTIME_OBSERVATION"
    RUNTIME_TOPOLOGY = "RUNTIME_TOPOLOGY"
    PASSENGER_ANSWER = "PASSENGER_ANSWER"


_RUNTIME_PROVENANCE = frozenset(item.value for item in ConvergenceEvidenceKind)


@dataclass(frozen=True)
class CandidateConvergenceEvidence:
    evidence_id: str
    evidence_kind: ConvergenceEvidenceKind
    source_observation_id: str
    source_frame_id: Any
    observed_monotonic_time: float
    rejected_candidate_ids: Tuple[str, ...]
    provenance: Tuple[str, ...]
    visibility: str
    privileged_authorization_reads: int
    evidence_digest: str

    @classmethod
    def create(
        cls,
        *,
        evidence_id: str,
        evidence_kind: ConvergenceEvidenceKind,
        source_observation_id: str,
        source_frame_id: Any,
        observed_monotonic_time: float,
        rejected_candidate_ids: Tuple[str, ...],
        provenance: Tuple[str, ...],
        visibility: str = "RUNTIME_OBSERVABLE",
        privileged_authorization_reads: int = 0,
    ) -> "CandidateConvergenceEvidence":
        if type(evidence_id) is not str or not evidence_id:
            raise ValueError("CONVERGENCE_EVIDENCE_ID_INVALID")
        if not isinstance(evidence_kind, ConvergenceEvidenceKind):
            raise TypeError("CONVERGENCE_EVIDENCE_KIND_INVALID")
        if type(source_observation_id) is not str or not source_observation_id:
            raise ValueError("CONVERGENCE_SOURCE_OBSERVATION_INVALID")
        if not math.isfinite(float(observed_monotonic_time)):
            raise ValueError("CONVERGENCE_TIME_INVALID")
        rejected = tuple(sorted(set(str(value) for value in rejected_candidate_ids)))
        if not rejected or any(not value for value in rejected):
            raise ValueError("CONVERGENCE_REJECTED_SET_INVALID")
        sources = tuple(str(value) for value in provenance)
        if not sources or any(value not in _RUNTIME_PROVENANCE for value in sources):
            raise ValueError("CONVERGENCE_PROVENANCE_NOT_RUNTIME_AUTHORIZABLE")
        if visibility != "RUNTIME_OBSERVABLE":
            raise ValueError("CONVERGENCE_VISIBILITY_INVALID")
        if privileged_authorization_reads != 0:
            raise ValueError("CONVERGENCE_PRIVILEGED_READS_FORBIDDEN")
        payload = {
            "evidence_id": evidence_id,
            "evidence_kind": evidence_kind.value,
            "source_observation_id": source_observation_id,
            "source_frame_id": source_frame_id,
            "observed_monotonic_time": float(observed_monotonic_time),
            "rejected_candidate_ids": list(rejected),
            "provenance": list(sources),
            "visibility": visibility,
            "privileged_authorization_reads": privileged_authorization_reads,
        }
        return cls(
            evidence_id=evidence_id,
            evidence_kind=evidence_kind,
            source_observation_id=source_observation_id,
            source_frame_id=source_frame_id,
            observed_monotonic_time=float(observed_monotonic_time),
            rejected_candidate_ids=rejected,
            provenance=sources,
            visibility=visibility,
            privileged_authorization_reads=privileged_authorization_reads,
            evidence_digest=canonical_sha256(payload),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CandidateConvergenceEvidence":
        if not isinstance(value, Mapping):
            raise TypeError("CONVERGENCE_EVIDENCE_MAPPING_REQUIRED")
        return cls.create(
            evidence_id=str(value.get("evidence_id", "")),
            evidence_kind=ConvergenceEvidenceKind(str(value.get("evidence_kind", ""))),
            source_observation_id=str(value.get("source_observation_id", "")),
            source_frame_id=value.get("source_frame_id"),
            observed_monotonic_time=float(value.get("observed_monotonic_time")),
            rejected_candidate_ids=tuple(value.get("rejected_candidate_ids", ())),
            provenance=tuple(value.get("provenance", ())),
            visibility=str(value.get("visibility", "")),
            privileged_authorization_reads=int(
                value.get("privileged_authorization_reads", -1)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "driveclarify.candidate_convergence_evidence.v1",
            "evidence_id": self.evidence_id,
            "evidence_kind": self.evidence_kind.value,
            "source_observation_id": self.source_observation_id,
            "source_frame_id": self.source_frame_id,
            "observed_monotonic_time": self.observed_monotonic_time,
            "rejected_candidate_ids": list(self.rejected_candidate_ids),
            "provenance": list(self.provenance),
            "visibility": self.visibility,
            "privileged_authorization_reads": self.privileged_authorization_reads,
            "evidence_digest": self.evidence_digest,
        }


def m3_event_for_decision(label: MethodDecisionLabel) -> EventType:
    try:
        return M2B_TO_M3_EVENT[label]
    except KeyError as error:
        raise ValueError("METHOD_DECISION_M3_MAPPING_MISSING") from error


def method_m3_receipt_from_results(
    *, label: MethodDecisionLabel, ready: Any, final: Any
) -> dict[str, Any]:
    """Project an already-executed frozen M3 transaction into Method V1."""

    return {
        "schema_version": "driveclarify.method_v1.m3_mapping_receipt.v1",
        "decision_label": label.value,
        "m3_event_type": m3_event_for_decision(label).value,
        "ready_transition_ids": list(ready.transition_ids),
        "decision_transition_ids": list(final.transition_ids),
        "final_lifecycle_state": final.state.lifecycle_state.value,
        "authority_owner": final.state.authority.value,
        "baseline_authority_eligible": final.state.baseline_authority_eligible,
        "m3_state": final.state.to_dict(),
    }


def build_method_m3_transaction(
    *, label: MethodDecisionLabel, candidate_set_id: str, observed_monotonic_time: float
) -> tuple[dict[str, Any], Any]:
    """Execute one frozen M3 transition and return its receipt and result."""

    if label in (MethodDecisionLabel.ACT, MethodDecisionLabel.ACT_SHARED):
        ready, final = _build_frozen_m3_act_result(
            candidate_set_id, float(observed_monotonic_time)
        )
    elif label is MethodDecisionLabel.FALLBACK:
        now = float(observed_monotonic_time)
        ready = reduce_event(
            MinimalM3State(baseline_authority_eligible=True),
            MinimalM3Event.create(
                event_id="method-v1-ready-" + canonical_sha256([candidate_set_id, now])[:20],
                event_type=EventType.CANDIDATES_READY,
                query_episode_id=None,
                source_component="METHOD_V1_DECISION_ENVELOPE",
                observed_monotonic_time=now,
                payload={
                    "candidate_set_id": candidate_set_id,
                    "candidate_freshness": "FRESH",
                    "answer_deadline_monotonic": now + 1.0,
                    "decision_deadline_monotonic": now + 2.0,
                    "baseline_authority_eligible": True,
                },
            ),
            now,
        )
        final = reduce_event(
            ready.state,
            MinimalM3Event.create(
                event_id="method-v1-fallback-"
                + canonical_sha256([candidate_set_id, now])[:20],
                event_type=EventType.DECISION_FALLBACK,
                query_episode_id=None,
                source_component="METHOD_V1_DECISION_ENVELOPE",
                observed_monotonic_time=now,
                payload={"baseline_authority_eligible": True},
            ),
            now,
        )
    else:
        raise ValueError("METHOD_M3_RECEIPT_REQUIRES_LIFECYCLE_CONTEXT")
    receipt = method_m3_receipt_from_results(label=label, ready=ready, final=final)
    return receipt, final


def build_method_m3_receipt(
    *, label: MethodDecisionLabel, candidate_set_id: str, observed_monotonic_time: float
) -> dict[str, Any]:
    """Execute one frozen M3 transition and return its serializable receipt."""

    return build_method_m3_transaction(
        label=label,
        candidate_set_id=candidate_set_id,
        observed_monotonic_time=observed_monotonic_time,
    )[0]


__all__ = [
    "CandidateConvergenceEvidence",
    "ConvergenceEvidenceKind",
    "M2B_TO_M3_EVENT",
    "MethodAuthorizationStatus",
    "MethodControlSource",
    "MethodDecisionEnvelope",
    "MethodDecisionLabel",
    "build_method_m3_receipt",
    "build_method_m3_transaction",
    "method_m3_receipt_from_results",
    "m3_event_for_decision",
]
