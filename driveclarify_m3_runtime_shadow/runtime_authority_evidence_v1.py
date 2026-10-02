"""Verified runtime evidence producer for decision-authority activation V1.

This module validates externally observed evidence.  It never infers physical
safety from route, speed, language, or symbolic task consequences, and it does
not select ACT/ASK/WAIT itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from driveclarify_decision.decision_contracts import (
    PhysicalSafetyEvidenceEnvelope,
)

from .contracts import ShadowObservationSnapshot


RUNTIME_AUTHORITY_EVIDENCE_SCHEMA = (
    "driveclarify.runtime_decision_authority_evidence.v1"
)
RUNTIME_AUTHORITY_EVIDENCE_PRODUCER = (
    "DRIVECLARIFY_RUNTIME_AUTHORITY_EVIDENCE_PRODUCER_V1"
)
VERIFIED_GRADE = "VERIFIED_FROM_CONTROLLED_PROBE"


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + "_MUST_BE_FINITE")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(label + "_MUST_BE_FINITE")
    return result


def _probability(value: Any, label: str) -> float:
    result = _finite(value, label)
    if not 0.0 <= result <= 1.0:
        raise ValueError(label + "_MUST_BE_PROBABILITY")
    return result


def _nonnegative(value: Any, label: str) -> float:
    result = _finite(value, label)
    if result < 0.0:
        raise ValueError(label + "_MUST_BE_NONNEGATIVE")
    return result


def _nonempty(value: Any, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(label + "_MUST_BE_NONEMPTY")
    return value


@dataclass(frozen=True)
class ClarificationOpportunityEvidenceV1:
    candidate_ids: tuple[str, str]
    answer_confusion_matrix: tuple[
        tuple[str, tuple[tuple[str, float], ...]], ...
    ]
    answer_resolution_probability: float
    no_answer_probability: float
    delay_distribution: tuple[tuple[float, float], ...]
    query_cost: float
    delay_cost_per_second: float
    no_answer_penalty: float
    answer_deadline_monotonic: float
    source: str
    source_frame_id: str | None
    usage_purpose: str
    evidence_grade: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.candidate_ids) != 2 or len(set(self.candidate_ids)) != 2:
            raise ValueError("CLARIFICATION_REQUIRES_TWO_CANDIDATES")
        rows = dict(self.answer_confusion_matrix)
        if tuple(rows) != self.candidate_ids:
            raise ValueError("ANSWER_CHANNEL_CANDIDATE_IDENTITY_MISMATCH")
        label_sets: list[set[str]] = []
        for candidate_id in self.candidate_ids:
            row = rows[candidate_id]
            labels = {label for label, _ in row}
            label_sets.append(labels)
            if len(labels) != len(row):
                raise ValueError("ANSWER_CHANNEL_LABEL_DUPLICATED")
            if not math.isclose(sum(value for _, value in row), 1.0, abs_tol=1e-9):
                raise ValueError("ANSWER_CHANNEL_ROW_NOT_NORMALIZED")
            for _, value in row:
                _probability(value, "ANSWER_CHANNEL_VALUE")
        if not label_sets or any(labels != label_sets[0] for labels in label_sets):
            raise ValueError("ANSWER_CHANNEL_LABEL_DOMAIN_MISMATCH")
        if tuple(rows[self.candidate_ids[0]]) == tuple(rows[self.candidate_ids[1]]):
            raise ValueError("ANSWER_CHANNEL_MUST_BE_INFORMATIVE")
        _probability(
            self.answer_resolution_probability, "ANSWER_RESOLUTION_PROBABILITY"
        )
        _probability(self.no_answer_probability, "NO_ANSWER_PROBABILITY")
        if not self.delay_distribution:
            raise ValueError("ANSWER_DELAY_DISTRIBUTION_MISSING")
        if not math.isclose(
            sum(probability for _, probability in self.delay_distribution),
            1.0,
            abs_tol=1e-9,
        ):
            raise ValueError("ANSWER_DELAY_DISTRIBUTION_NOT_NORMALIZED")
        for delay, probability in self.delay_distribution:
            _nonnegative(delay, "ANSWER_DELAY")
            _probability(probability, "ANSWER_DELAY_PROBABILITY")
        _nonnegative(self.query_cost, "QUERY_COST")
        _nonnegative(self.delay_cost_per_second, "DELAY_COST")
        _nonnegative(self.no_answer_penalty, "NO_ANSWER_PENALTY")
        _finite(self.answer_deadline_monotonic, "ANSWER_DEADLINE")
        _nonempty(self.source, "CLARIFICATION_SOURCE")
        if self.usage_purpose != "CLARIFICATION_DECISION_AUTHORITY":
            raise ValueError("CLARIFICATION_USAGE_PURPOSE_INVALID")
        if self.evidence_grade != VERIFIED_GRADE:
            raise ValueError("CLARIFICATION_EVIDENCE_NOT_VERIFIED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_ids": list(self.candidate_ids),
            "answer_confusion_matrix": [
                [candidate_id, [[label, value] for label, value in row]]
                for candidate_id, row in self.answer_confusion_matrix
            ],
            "answer_resolution_probability": self.answer_resolution_probability,
            "no_answer_probability": self.no_answer_probability,
            "delay_distribution": [list(item) for item in self.delay_distribution],
            "query_cost": self.query_cost,
            "delay_cost_per_second": self.delay_cost_per_second,
            "no_answer_penalty": self.no_answer_penalty,
            "answer_deadline_monotonic": self.answer_deadline_monotonic,
            "source": self.source,
            "source_frame_id": self.source_frame_id,
            "usage_purpose": self.usage_purpose,
            "evidence_grade": self.evidence_grade,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class HoldingCapabilityDeclarationV1:
    expected_information_id: str
    expected_information_arrival_time: float
    decision_deadline_monotonic: float
    information_resolution_probability: float
    wait_reason: str
    wait_cost: float
    missed_opportunity_cost: float
    maximum_holding_duration_s: float
    reevaluation_interval_s: float
    source: str
    source_frame_id: str | None
    evidence_grade: str
    holding_capability_status: str
    closed_loop_behavior: str
    emergency_stop_semantics: bool
    low_level_controller_owner: str
    usage_purpose: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _nonempty(self.expected_information_id, "EXPECTED_INFORMATION_ID")
        arrival = _finite(
            self.expected_information_arrival_time, "EXPECTED_INFORMATION_ARRIVAL"
        )
        deadline = _finite(self.decision_deadline_monotonic, "DECISION_DEADLINE")
        if arrival > deadline:
            raise ValueError("EXPECTED_INFORMATION_ARRIVES_AFTER_DEADLINE")
        _probability(
            self.information_resolution_probability,
            "INFORMATION_RESOLUTION_PROBABILITY",
        )
        if self.information_resolution_probability <= 0.0:
            raise ValueError("WAIT_INFORMATION_VALUE_CANNOT_BE_ZERO")
        _nonempty(self.wait_reason, "WAIT_REASON")
        _nonnegative(self.wait_cost, "WAIT_COST")
        _nonnegative(self.missed_opportunity_cost, "MISSED_OPPORTUNITY_COST")
        if _nonnegative(
            self.maximum_holding_duration_s, "MAXIMUM_HOLDING_DURATION"
        ) <= 0.0:
            raise ValueError("MAXIMUM_HOLDING_DURATION_MUST_BE_POSITIVE")
        if _nonnegative(self.reevaluation_interval_s, "REEVALUATION_INTERVAL") <= 0.0:
            raise ValueError("REEVALUATION_INTERVAL_MUST_BE_POSITIVE")
        if self.evidence_grade != VERIFIED_GRADE:
            raise ValueError("HOLDING_CAPABILITY_EVIDENCE_NOT_VERIFIED")
        if self.holding_capability_status != "AVAILABLE_CONTRACT_ONLY":
            raise ValueError("HOLDING_CAPABILITY_CONTRACT_NOT_AVAILABLE")
        if self.closed_loop_behavior != "MAINTAIN_CURRENT_VALID_CLOSED_LOOP_BEHAVIOR":
            raise ValueError("WAIT_CLOSED_LOOP_BEHAVIOR_INVALID")
        if self.emergency_stop_semantics is not False:
            raise ValueError("WAIT_MUST_NOT_MEAN_EMERGENCY_STOP")
        if self.low_level_controller_owner != "EXISTING_BASELINE_PID":
            raise ValueError("WAIT_MUST_PRESERVE_EXISTING_PID_OWNER")
        if self.usage_purpose != "WAIT_DECISION_AUTHORITY":
            raise ValueError("WAIT_USAGE_PURPOSE_INVALID")
        _nonempty(self.source, "HOLDING_CAPABILITY_SOURCE")

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_information_id": self.expected_information_id,
            "expected_information_arrival_time": self.expected_information_arrival_time,
            "decision_deadline_monotonic": self.decision_deadline_monotonic,
            "information_resolution_probability": self.information_resolution_probability,
            "wait_reason": self.wait_reason,
            "wait_cost": self.wait_cost,
            "missed_opportunity_cost": self.missed_opportunity_cost,
            "maximum_holding_duration_s": self.maximum_holding_duration_s,
            "reevaluation_interval_s": self.reevaluation_interval_s,
            "source": self.source,
            "source_frame_id": self.source_frame_id,
            "evidence_grade": self.evidence_grade,
            "holding_capability_status": self.holding_capability_status,
            "closed_loop_behavior": self.closed_loop_behavior,
            "emergency_stop_semantics": self.emergency_stop_semantics,
            "low_level_controller_owner": self.low_level_controller_owner,
            "usage_purpose": self.usage_purpose,
            "reason_codes": list(self.reason_codes),
        }

    def build_holding_lease(
        self,
        *,
        decision_monotonic_time: float,
        source_observation_id: str,
        source_frame_id: str,
        candidate_set_id: str,
    ) -> dict[str, Any]:
        issued = _finite(decision_monotonic_time, "DECISION_MONOTONIC_TIME")
        if self.expected_information_arrival_time <= issued:
            raise ValueError("EXPECTED_INFORMATION_MUST_ARRIVE_IN_FUTURE")
        expiry = min(
            issued + self.maximum_holding_duration_s,
            self.decision_deadline_monotonic,
        )
        if expiry < self.expected_information_arrival_time:
            raise ValueError("HOLDING_LEASE_EXPIRES_BEFORE_INFORMATION_ARRIVAL")
        next_reevaluation = min(issued + self.reevaluation_interval_s, expiry)
        if next_reevaluation <= issued:
            raise ValueError("HOLDING_REEVALUATION_NOT_IN_FUTURE")
        return {
            "lease_id": "WAIT-LEASE-" + candidate_set_id[-24:],
            "issued_monotonic_time": issued,
            "next_reevaluation_monotonic_time": next_reevaluation,
            "expires_monotonic_time": expiry,
            "maximum_expiry_monotonic_time": expiry,
            "evidence_grade": VERIFIED_GRADE,
            "source_observation_id": source_observation_id,
            "source_frame_id": source_frame_id,
            "candidate_set_id": candidate_set_id,
            "revoked": False,
            "revocation_reason": None,
            "authority_on_exit": "BASELINE_CONTROL",
        }


@dataclass(frozen=True)
class RuntimeDecisionAuthorityEvidenceV1:
    source_observation_id: str
    source_frame_id: str | None
    decision_monotonic_time: float
    physical_safety: PhysicalSafetyEvidenceEnvelope
    clarification: ClarificationOpportunityEvidenceV1 | None
    holding: HoldingCapabilityDeclarationV1 | None
    producer: str = RUNTIME_AUTHORITY_EVIDENCE_PRODUCER
    schema_version: str = RUNTIME_AUTHORITY_EVIDENCE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "producer": self.producer,
            "source_observation_id": self.source_observation_id,
            "source_frame_id": self.source_frame_id,
            "decision_monotonic_time": self.decision_monotonic_time,
            "physical_safety": self.physical_safety.to_dict(),
            "clarification": (
                None if self.clarification is None else self.clarification.to_dict()
            ),
            "holding": None if self.holding is None else self.holding.to_dict(),
        }


def _rows(
    candidate_ids: tuple[str, str], raw: Mapping[str, Any]
) -> tuple[tuple[str, tuple[tuple[str, float], ...]], ...]:
    matrix = raw["answer_confusion_matrix"]
    if not isinstance(matrix, Mapping):
        raise ValueError("ANSWER_CONFUSION_MATRIX_MUST_BE_MAPPING")
    return tuple(
        (
            candidate_id,
            tuple(
                (str(label).upper(), float(probability))
                for label, probability in matrix[candidate_id].items()
            ),
        )
        for candidate_id in candidate_ids
    )


def produce_runtime_decision_authority_evidence_v1(
    snapshot: ShadowObservationSnapshot,
    candidate_ids: Sequence[str],
    *,
    decision_monotonic_time: float = 100.0,
    physical_safety_signal: Mapping[str, Any] | None = None,
    clarification_signal: Mapping[str, Any] | None = None,
    holding_signal: Mapping[str, Any] | None = None,
) -> RuntimeDecisionAuthorityEvidenceV1:
    """Validate explicit signals and produce an immutable evidence bundle.

    Absence of a physical signal produces UNKNOWN.  No caller can obtain PASS
    without a verified, safety-critical-eligible physical source envelope.
    """

    ids = tuple(str(item) for item in candidate_ids)
    if len(ids) != 2 or len(set(ids)) != 2:
        raise ValueError("RUNTIME_AUTHORITY_REQUIRES_TWO_CANDIDATES")
    typed_ids = (ids[0], ids[1])
    now = _finite(decision_monotonic_time, "DECISION_MONOTONIC_TIME")
    frame_id = None if snapshot.frame_id is None else str(snapshot.frame_id)
    if physical_safety_signal is None:
        physical = PhysicalSafetyEvidenceEnvelope(
            safety_status="UNKNOWN",
            availability="UNKNOWN",
            evidence_grade="NOT_CURRENTLY_AVAILABLE",
            source=RUNTIME_AUTHORITY_EVIDENCE_PRODUCER,
            source_kind="NO_PHYSICAL_SAFETY_SIGNAL",
            source_observation_id=snapshot.observation_id,
            source_frame_id=frame_id,
            observed_monotonic_time=now,
            usage_purpose="PHYSICAL_CONTROL_AUTHORIZATION",
            safety_critical_eligible=False,
            reason_codes=("NO_VERIFIED_PHYSICAL_SAFETY_EVIDENCE",),
        )
    else:
        signal_frame = (
            None
            if physical_safety_signal.get("source_frame_id") is None
            else str(physical_safety_signal["source_frame_id"])
        )
        if signal_frame != frame_id:
            raise ValueError("PHYSICAL_SAFETY_FRAME_IDENTITY_MISMATCH")
        observed_time = float(
            physical_safety_signal.get("observed_monotonic_time", now)
        )
        if not math.isclose(observed_time, now, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("PHYSICAL_SAFETY_TIMESTAMP_NOT_CURRENT")
        physical = PhysicalSafetyEvidenceEnvelope(
            safety_status=str(physical_safety_signal["safety_status"]),
            availability=str(physical_safety_signal["availability"]),
            evidence_grade=str(physical_safety_signal["evidence_grade"]),
            source=str(physical_safety_signal["source"]),
            source_kind=str(physical_safety_signal["source_kind"]),
            source_observation_id=snapshot.observation_id,
            source_frame_id=signal_frame,
            observed_monotonic_time=observed_time,
            usage_purpose=str(physical_safety_signal["usage_purpose"]),
            safety_critical_eligible=bool(
                physical_safety_signal["safety_critical_eligible"]
            ),
            reason_codes=tuple(physical_safety_signal.get("reason_codes", ())),
        )

    clarification = None
    if clarification_signal is not None:
        clarification = ClarificationOpportunityEvidenceV1(
            candidate_ids=typed_ids,
            answer_confusion_matrix=_rows(typed_ids, clarification_signal),
            answer_resolution_probability=float(
                clarification_signal["answer_resolution_probability"]
            ),
            no_answer_probability=float(
                clarification_signal["no_answer_probability"]
            ),
            delay_distribution=tuple(
                (float(delay), float(probability))
                for delay, probability in clarification_signal["delay_distribution"]
            ),
            query_cost=float(clarification_signal["query_cost"]),
            delay_cost_per_second=float(
                clarification_signal["delay_cost_per_second"]
            ),
            no_answer_penalty=float(clarification_signal["no_answer_penalty"]),
            answer_deadline_monotonic=float(
                clarification_signal["answer_deadline_monotonic"]
            ),
            source=str(clarification_signal["source"]),
            source_frame_id=frame_id,
            usage_purpose=str(clarification_signal["usage_purpose"]),
            evidence_grade=str(clarification_signal["evidence_grade"]),
            reason_codes=tuple(clarification_signal.get("reason_codes", ())),
        )
        if clarification.answer_deadline_monotonic <= now:
            raise ValueError("CLARIFICATION_TIME_WINDOW_EXPIRED")

    holding = None
    if holding_signal is not None:
        holding = HoldingCapabilityDeclarationV1(
            expected_information_id=str(holding_signal["expected_information_id"]),
            expected_information_arrival_time=float(
                holding_signal["expected_information_arrival_time"]
            ),
            decision_deadline_monotonic=float(
                holding_signal["decision_deadline_monotonic"]
            ),
            information_resolution_probability=float(
                holding_signal["information_resolution_probability"]
            ),
            wait_reason=str(holding_signal["wait_reason"]),
            wait_cost=float(holding_signal["wait_cost"]),
            missed_opportunity_cost=float(
                holding_signal["missed_opportunity_cost"]
            ),
            maximum_holding_duration_s=float(
                holding_signal["maximum_holding_duration_s"]
            ),
            reevaluation_interval_s=float(
                holding_signal["reevaluation_interval_s"]
            ),
            source=str(holding_signal["source"]),
            source_frame_id=frame_id,
            evidence_grade=str(holding_signal["evidence_grade"]),
            holding_capability_status=str(
                holding_signal["holding_capability_status"]
            ),
            closed_loop_behavior=str(holding_signal["closed_loop_behavior"]),
            emergency_stop_semantics=bool(
                holding_signal["emergency_stop_semantics"]
            ),
            low_level_controller_owner=str(
                holding_signal["low_level_controller_owner"]
            ),
            usage_purpose=str(holding_signal["usage_purpose"]),
            reason_codes=tuple(holding_signal.get("reason_codes", ())),
        )
        if holding.expected_information_arrival_time <= now:
            raise ValueError("EXPECTED_INFORMATION_MUST_ARRIVE_IN_FUTURE")

    return RuntimeDecisionAuthorityEvidenceV1(
        source_observation_id=snapshot.observation_id,
        source_frame_id=frame_id,
        decision_monotonic_time=now,
        physical_safety=physical,
        clarification=clarification,
        holding=holding,
    )
