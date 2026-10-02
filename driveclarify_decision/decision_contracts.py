"""Immutable runtime contracts for the offline M2B decision layer.

The contracts in this module contain runtime-observable hypotheses and costs only.  Passenger
latent intent and every evaluation-only label are deliberately forbidden.  All recommendations
remain diagnostic and can never authorize an ASK, a wait controller, or vehicle control.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "driveclarify.query_value_decision.v0"
EVIDENCE_DESIGNATION = (
    "DECISION_LAYER_PROTOTYPE",
    "OFFLINE_SYMBOLIC_QUERY_VALUE_VALIDATION",
    "NOT_PRIMARY_EVIDENCE",
    "NOT_PAPER_RESULT",
)
FORBIDDEN_RUNTIME_FIELDS = frozenset(
    {
        "true_intent",
        "latent_true_intent",
        "gold_intent",
        "gold_candidate_id",
        "gold_answer",
        "gold_loss",
        "necessary_query_label",
        "oracle_selected_candidate",
        "expected_decision",
    }
)


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TaskOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class LongitudinalTaskOutcome(StrEnum):
    SATISFIED = "SATISFIED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Decision(StrEnum):
    ACT = "ACT"
    ASK = "ASK"
    WAIT = "WAIT"
    FALLBACK_RECOMMENDED = "FALLBACK_RECOMMENDED"


class ActTargetType(StrEnum):
    UNIQUE_CANDIDATE = "UNIQUE_CANDIDATE"
    EQUIVALENCE_CLASS = "EQUIVALENCE_CLASS"
    NONE = "NONE"


class BeliefStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    INVALID = "INVALID"


class BeliefSource(StrEnum):
    DECLARED_UNINFORMATIVE = "DECLARED_UNINFORMATIVE"
    RULE_DERIVED = "RULE_DERIVED"
    EXTERNAL_CALIBRATED = "EXTERNAL_CALIBRATED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class WaitMode(StrEnum):
    AWAIT_PENDING_ANSWER = "AWAIT_PENDING_ANSWER"
    AWAIT_EXPECTED_OBSERVATION = "AWAIT_EXPECTED_OBSERVATION"
    DEFER_BEFORE_DECISION_WINDOW = "DEFER_BEFORE_DECISION_WINDOW"
    NOT_AVAILABLE = "NOT_AVAILABLE"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    return value


class SerializableContract:
    def to_dict(self) -> dict[str, Any]:
        return {key: _json_value(value) for key, value in asdict(self).items()}


def assert_no_forbidden_runtime_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        overlap = FORBIDDEN_RUNTIME_FIELDS.intersection(str(key) for key in value)
        if overlap:
            raise ValueError(f"FORBIDDEN_DECISION_RUNTIME_FIELD:{','.join(sorted(overlap))}")
        for item in value.values():
            assert_no_forbidden_runtime_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_no_forbidden_runtime_keys(item)


def _finite_nonnegative(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}_MUST_BE_FINITE_NONNEGATIVE")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name}_MUST_BE_FINITE_NONNEGATIVE")
    return result


def _probability(value: Any, *, name: str) -> float:
    result = _finite_nonnegative(value, name=name)
    if result > 1.0:
        raise ValueError(f"{name}_MUST_BE_PROBABILITY")
    return result


def _probability_rows(
    rows: Sequence[tuple[str, Sequence[tuple[str, float]]]],
    *,
    expected_ids: Sequence[str],
) -> None:
    if tuple(row_id for row_id, _ in rows) != tuple(expected_ids):
        raise ValueError("ANSWER_CONFUSION_MATRIX_MUST_COVER_CANDIDATES_IN_ORDER")
    labels: tuple[str, ...] | None = None
    for candidate_id, row in rows:
        current = tuple(label for label, _ in row)
        if not current or len(set(current)) != len(current):
            raise ValueError("ANSWER_CONFUSION_LABELS_INVALID")
        if labels is None:
            labels = current
        elif labels != current:
            raise ValueError("ANSWER_CONFUSION_LABEL_ORDER_MISMATCH")
        total = sum(_probability(value, name="ANSWER_CONFUSION_PROBABILITY") for _, value in row)
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(f"ANSWER_CONFUSION_ROW_NOT_NORMALIZED:{candidate_id}")


@dataclass(frozen=True)
class CandidateOutcomeEvidence(SerializableContract):
    candidate_id: str
    task_outcome: TaskOutcome
    evidence_status: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class CounterfactualOutcomeCell(SerializableContract):
    action_candidate_id: str
    hypothesis_candidate_id: str
    task_outcome: TaskOutcome
    evidence_status: str
    task_error_cost: float | None
    wrong_goal_indicator: bool | None
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION
    route_task_outcome: TaskOutcome | None = None
    longitudinal_task_outcome: LongitudinalTaskOutcome | None = None

    def __post_init__(self) -> None:
        if self.task_outcome is TaskOutcome.UNKNOWN:
            if self.task_error_cost is not None or self.wrong_goal_indicator is not None:
                raise ValueError("UNKNOWN_COUNTERFACTUAL_CELL_CANNOT_HAVE_SCALAR_COST")
        elif self.task_error_cost is None:
            if not (
                self.task_outcome is TaskOutcome.FAIL
                and self.route_task_outcome is TaskOutcome.UNKNOWN
                and self.longitudinal_task_outcome
                is LongitudinalTaskOutcome.CONTRADICTED
                and self.wrong_goal_indicator is None
            ):
                raise ValueError("KNOWN_COUNTERFACTUAL_CELL_REQUIRES_TASK_ERROR_COST")
        else:
            _finite_nonnegative(self.task_error_cost, name="TASK_ERROR_COST")
            if not isinstance(self.wrong_goal_indicator, bool):
                raise ValueError("KNOWN_COUNTERFACTUAL_CELL_REQUIRES_WRONG_GOAL_INDICATOR")


@dataclass(frozen=True)
class LongitudinalTaskEvidence(SerializableContract):
    """Candidate-specific diagnostic longitudinal semantic, never a safety claim."""

    action_candidate_id: str
    status: str
    semantic_type: str | None
    semantic_value: float | None
    frame: str | None
    unit: str | None
    temporal_basis: str | None
    source: str
    evidence_grade: str
    allowed_usage_purposes: tuple[str, ...]
    provenance: tuple[str, ...]
    authorization_eligible: bool
    safety_critical_eligible: bool
    reason_codes: tuple[str, ...]
    schema_version: str = "driveclarify.longitudinal_task_evidence.v0"

    def __post_init__(self) -> None:
        if not self.action_candidate_id:
            raise ValueError("LONGITUDINAL_TASK_EVIDENCE_CANDIDATE_ID_MISSING")
        if self.authorization_eligible or self.safety_critical_eligible:
            raise ValueError("LONGITUDINAL_TASK_EVIDENCE_CANNOT_AUTHORIZE_OR_CLAIM_SAFETY")
        if self.allowed_usage_purposes != ("DIAGNOSTIC_ONLY", "LOGGING_ONLY"):
            raise ValueError("LONGITUDINAL_TASK_EVIDENCE_USAGE_INVALID")
        if not self.source or not self.provenance or not self.reason_codes:
            raise ValueError("LONGITUDINAL_TASK_EVIDENCE_TRACE_INCOMPLETE")
        if self.status == "AVAILABLE":
            if (
                self.semantic_type != "PID_DESIRED_SPEED_MPS"
                or self.frame != "EGO_LOCAL_X_FORWARD_Y_RIGHT"
                or self.unit != "MPS"
                or self.temporal_basis != "Q0_TO_Q2_0P5S"
                or self.evidence_grade != "SUPPORTED_BUT_INCOMPLETE"
            ):
                raise ValueError("LONGITUDINAL_TASK_EVIDENCE_SEMANTIC_CONTRACT_INVALID")
            value = self.semantic_value
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise ValueError("LONGITUDINAL_TASK_EVIDENCE_VALUE_INVALID")
        elif self.status == "NOT_CURRENTLY_AVAILABLE":
            if self.semantic_type is not None or self.semantic_value is not None:
                raise ValueError("UNAVAILABLE_LONGITUDINAL_TASK_EVIDENCE_HAS_SEMANTIC")
        else:
            raise ValueError("LONGITUDINAL_TASK_EVIDENCE_STATUS_INVALID")


@dataclass(frozen=True)
class LongitudinalTaskOutcomeEvidence(SerializableContract):
    """Diagnostic action-by-hypothesis task outcome, never a safety claim or cost."""

    action_candidate_id: str
    hypothesis_candidate_id: str
    target_type: str | None
    outcome: LongitudinalTaskOutcome
    metric_identity: str | None
    evidence_status: str
    raw_speed_digest: str
    source: str
    evidence_grade: str
    allowed_usage_purposes: tuple[str, ...]
    provenance: tuple[str, ...]
    authorization_eligible: bool
    safety_critical_eligible: bool
    reason_codes: tuple[str, ...]
    schema_version: str = "driveclarify.longitudinal_task_outcome_evidence.v0"

    def __post_init__(self) -> None:
        if not self.action_candidate_id or not self.hypothesis_candidate_id:
            raise ValueError("LONGITUDINAL_TASK_OUTCOME_EVIDENCE_IDENTITY_MISSING")
        if self.allowed_usage_purposes != ("DIAGNOSTIC_ONLY", "LOGGING_ONLY"):
            raise ValueError("LONGITUDINAL_TASK_OUTCOME_EVIDENCE_USAGE_INVALID")
        if self.authorization_eligible or self.safety_critical_eligible:
            raise ValueError(
                "LONGITUDINAL_TASK_OUTCOME_EVIDENCE_CANNOT_AUTHORIZE_OR_CLAIM_SAFETY"
            )
        if (
            not self.raw_speed_digest
            or not self.source
            or not self.provenance
            or not self.reason_codes
        ):
            raise ValueError("LONGITUDINAL_TASK_OUTCOME_EVIDENCE_TRACE_INCOMPLETE")
        if self.outcome in (
            LongitudinalTaskOutcome.SATISFIED,
            LongitudinalTaskOutcome.CONTRADICTED,
        ):
            if self.target_type != "STOP" or self.metric_identity != "SIMLINGO_STOP_SUCCESS_V0":
                raise ValueError("KNOWN_LONGITUDINAL_TASK_OUTCOME_CONTRACT_INVALID")
        elif self.outcome is LongitudinalTaskOutcome.NOT_APPLICABLE:
            if self.target_type is not None:
                raise ValueError("NOT_APPLICABLE_LONGITUDINAL_TASK_OUTCOME_HAS_TARGET")
        elif self.target_type not in {"STOP", "CONTINUE"}:
            raise ValueError("UNKNOWN_LONGITUDINAL_TASK_OUTCOME_TARGET_INVALID")


@dataclass(frozen=True)
class CounterfactualOutcomeMatrix(SerializableContract):
    candidate_ids: tuple[str, ...]
    cells: tuple[CounterfactualOutcomeCell, ...]
    matrix_status: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    longitudinal_task_evidence: tuple[LongitudinalTaskEvidence, ...] = ()
    longitudinal_task_outcomes: tuple[LongitudinalTaskOutcomeEvidence, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.candidate_ids or len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("COUNTERFACTUAL_MATRIX_CANDIDATE_IDS_INVALID")
        expected = {(a, h) for a in self.candidate_ids for h in self.candidate_ids}
        actual = {(cell.action_candidate_id, cell.hypothesis_candidate_id) for cell in self.cells}
        if len(actual) != len(self.cells) or not actual.issubset(expected):
            raise ValueError("COUNTERFACTUAL_MATRIX_CELL_IDENTITIES_INVALID")
        longitudinal_ids = tuple(
            evidence.action_candidate_id for evidence in self.longitudinal_task_evidence
        )
        expected_longitudinal_order = tuple(
            candidate_id
            for candidate_id in self.candidate_ids
            if candidate_id in set(longitudinal_ids)
        )
        if (
            len(set(longitudinal_ids)) != len(longitudinal_ids)
            or longitudinal_ids != expected_longitudinal_order
        ):
            raise ValueError("COUNTERFACTUAL_MATRIX_LONGITUDINAL_EVIDENCE_IDENTITIES_INVALID")
        longitudinal_outcome_ids = tuple(
            (evidence.action_candidate_id, evidence.hypothesis_candidate_id)
            for evidence in self.longitudinal_task_outcomes
        )
        expected_longitudinal_outcome_order = tuple(
            (action_id, hypothesis_id)
            for action_id in self.candidate_ids
            for hypothesis_id in self.candidate_ids
            if (action_id, hypothesis_id) in set(longitudinal_outcome_ids)
        )
        if (
            len(set(longitudinal_outcome_ids)) != len(longitudinal_outcome_ids)
            or longitudinal_outcome_ids != expected_longitudinal_outcome_order
        ):
            raise ValueError(
                "COUNTERFACTUAL_MATRIX_LONGITUDINAL_OUTCOME_IDENTITIES_INVALID"
            )

    def cell(self, action_candidate_id: str, hypothesis_candidate_id: str) -> CounterfactualOutcomeCell | None:
        return next(
            (
                cell
                for cell in self.cells
                if cell.action_candidate_id == action_candidate_id
                and cell.hypothesis_candidate_id == hypothesis_candidate_id
            ),
            None,
        )

    @property
    def complete_and_known(self) -> bool:
        expected_count = len(self.candidate_ids) ** 2
        return len(self.cells) == expected_count and all(
            cell.task_outcome is not TaskOutcome.UNKNOWN for cell in self.cells
        )


@dataclass(frozen=True)
class IntentBelief(SerializableContract):
    candidate_probabilities: tuple[tuple[str, float], ...]
    belief_status: BeliefStatus
    belief_source: BeliefSource
    normalization_status: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def validate_for(self, candidate_ids: Sequence[str]) -> None:
        if self.belief_status is not BeliefStatus.AVAILABLE:
            raise ValueError("INTENT_BELIEF_NOT_AVAILABLE")
        ids = tuple(candidate_id for candidate_id, _ in self.candidate_probabilities)
        if ids != tuple(candidate_ids):
            raise ValueError("INTENT_BELIEF_MUST_COVER_CANDIDATES_IN_ORDER")
        probabilities = [
            _probability(value, name="INTENT_BELIEF_PROBABILITY")
            for _, value in self.candidate_probabilities
        ]
        if not math.isclose(sum(probabilities), 1.0, abs_tol=1e-9):
            raise ValueError("INTENT_BELIEF_NOT_NORMALIZED")
        if self.belief_source is BeliefSource.DECLARED_UNINFORMATIVE:
            expected = 1.0 / len(probabilities)
            if any(not math.isclose(value, expected, abs_tol=1e-9) for value in probabilities):
                raise ValueError("DECLARED_UNINFORMATIVE_BELIEF_MUST_BE_UNIFORM")

    def as_dict(self) -> dict[str, float]:
        return dict(self.candidate_probabilities)


@dataclass(frozen=True)
class PhysicalSafetyEvidenceEnvelope(SerializableContract):
    """Provenance-bound physical-safety evidence for ACT eligibility.

    Route, speed, language, and symbolic task evidence are deliberately not
    admitted as physical-safety sources.  UNKNOWN remains an explicit,
    ineligible state rather than an implicit PASS.
    """

    safety_status: str
    availability: str
    evidence_grade: str
    source: str
    source_kind: str
    source_observation_id: str
    source_frame_id: str | None
    observed_monotonic_time: float
    usage_purpose: str
    safety_critical_eligible: bool
    reason_codes: tuple[str, ...]
    schema_version: str = "driveclarify.physical_safety_evidence.v1"

    def __post_init__(self) -> None:
        if self.safety_status not in {"PASS", "CLEAR", "BLOCKED", "UNKNOWN"}:
            raise ValueError("PHYSICAL_SAFETY_STATUS_INVALID")
        if self.availability not in {"AVAILABLE_VERIFIED", "UNKNOWN"}:
            raise ValueError("PHYSICAL_SAFETY_AVAILABILITY_INVALID")
        if not self.source or not self.source_kind or not self.source_observation_id:
            raise ValueError("PHYSICAL_SAFETY_SOURCE_IDENTITY_MISSING")
        if self.source_frame_id is not None and not self.source_frame_id:
            raise ValueError("PHYSICAL_SAFETY_SOURCE_FRAME_INVALID")
        if not math.isfinite(float(self.observed_monotonic_time)):
            raise ValueError("PHYSICAL_SAFETY_TIMESTAMP_INVALID")
        if self.usage_purpose != "PHYSICAL_CONTROL_AUTHORIZATION":
            raise ValueError("PHYSICAL_SAFETY_USAGE_PURPOSE_INVALID")
        forbidden_source_kinds = {
            "ROUTE_SYMBOLIC_EVIDENCE",
            "SPEED_SYMBOLIC_EVIDENCE",
            "TASK_CONSEQUENCE_EVIDENCE",
            "LANGUAGE_EVIDENCE",
        }
        if self.source_kind in forbidden_source_kinds:
            raise ValueError("SYMBOLIC_EVIDENCE_CANNOT_AUTHORIZE_PHYSICAL_CONTROL")
        verified = (
            self.safety_status in {"PASS", "CLEAR"}
            and self.availability == "AVAILABLE_VERIFIED"
            and self.evidence_grade == "VERIFIED_FROM_CONTROLLED_PROBE"
            and self.safety_critical_eligible is True
        )
        unknown = (
            self.safety_status == "UNKNOWN"
            and self.availability == "UNKNOWN"
            and self.safety_critical_eligible is False
        )
        blocked = (
            self.safety_status == "BLOCKED"
            and self.availability == "AVAILABLE_VERIFIED"
            and self.evidence_grade == "VERIFIED_FROM_CONTROLLED_PROBE"
            and self.safety_critical_eligible is False
        )
        if not (verified or unknown or blocked):
            raise ValueError("PHYSICAL_SAFETY_EVIDENCE_ELIGIBILITY_CONFLICT")


@dataclass(frozen=True)
class HardGateEnvelope(SerializableContract):
    hard_safety_status: str
    hard_rule_status: str
    evidence_gate_status: str
    query_episode_status: str
    cache_status: str
    candidate_freshness_status: str
    control_authorized: bool
    reason_codes: tuple[str, ...]
    physical_safety_evidence: PhysicalSafetyEvidenceEnvelope | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.control_authorized:
            raise ValueError("M2B_CONTROL_AUTHORIZATION_FORBIDDEN")
        evidence = self.physical_safety_evidence
        if evidence is not None:
            if evidence.safety_status != self.hard_safety_status:
                raise ValueError("PHYSICAL_SAFETY_STATUS_ENVELOPE_MISMATCH")
            if evidence.source_observation_id == "":
                raise ValueError("PHYSICAL_SAFETY_OBSERVATION_ID_MISSING")


@dataclass(frozen=True)
class AnswerChannelModel(SerializableContract):
    answer_resolution_probability: float
    no_answer_probability: float
    answer_confusion_matrix: tuple[tuple[str, tuple[tuple[str, float], ...]], ...]
    delay_distribution: tuple[tuple[float, float], ...]
    channel_status: str
    source: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def validate_for(self, candidate_ids: Sequence[str]) -> None:
        _probability(self.answer_resolution_probability, name="ANSWER_RESOLUTION_PROBABILITY")
        _probability(self.no_answer_probability, name="NO_ANSWER_PROBABILITY")
        _probability_rows(self.answer_confusion_matrix, expected_ids=candidate_ids)
        if not self.delay_distribution:
            raise ValueError("ANSWER_DELAY_DISTRIBUTION_MISSING")
        total = 0.0
        for delay, probability in self.delay_distribution:
            _finite_nonnegative(delay, name="ANSWER_DELAY")
            total += _probability(probability, name="ANSWER_DELAY_PROBABILITY")
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError("ANSWER_DELAY_DISTRIBUTION_NOT_NORMALIZED")


@dataclass(frozen=True)
class WaitOpportunityModel(SerializableContract):
    wait_mode: WaitMode
    future_information_expected: bool
    expected_information_arrival_time: float | None
    active_query_pending: bool
    holding_capability_status: str
    decision_deadline_status: str
    missed_opportunity_cost: float
    wait_cost: float
    information_resolution_probability: float
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    wait_reason: str | None = None
    closed_loop_behavior: str | None = None
    emergency_stop_semantics: bool = False
    low_level_controller_owner: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _finite_nonnegative(self.missed_opportunity_cost, name="MISSED_OPPORTUNITY_COST")
        _finite_nonnegative(self.wait_cost, name="WAIT_COST")
        _probability(self.information_resolution_probability, name="WAIT_INFORMATION_RESOLUTION_PROBABILITY")
        if self.emergency_stop_semantics:
            raise ValueError("WAIT_CANNOT_DECLARE_EMERGENCY_STOP_SEMANTICS")


@dataclass(frozen=True)
class OperatingParameters(SerializableContract):
    query_cost: float
    delay_cost_per_second: float
    no_answer_penalty: float
    unknown_task_loss_policy: str
    strict_value_epsilon: float
    provenance: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _finite_nonnegative(self.query_cost, name="QUERY_COST")
        _finite_nonnegative(self.delay_cost_per_second, name="DELAY_COST_PER_SECOND")
        _finite_nonnegative(self.no_answer_penalty, name="NO_ANSWER_PENALTY")
        _finite_nonnegative(self.strict_value_epsilon, name="STRICT_VALUE_EPSILON")
        if self.unknown_task_loss_policy != "FAIL_CLOSED_NO_SCALAR_IMPUTATION":
            raise ValueError("UNKNOWN_TASK_LOSS_POLICY_MUST_FAIL_CLOSED")


@dataclass(frozen=True)
class ExpectedLossChannels(SerializableContract):
    task_cost: float | None
    query_cost: float
    delay_cost: float
    no_answer_cost: float
    wait_cost: float
    missed_opportunity_cost: float
    total: float | None
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class PosteriorBeliefUpdate(SerializableContract):
    answer_label: str
    posterior_belief: IntentBelief
    update_status: str
    selected_candidate_id: str | None
    likelihood: float
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class DecisionContext(SerializableContract):
    decision_id: str
    episode_id: str
    source_observation_id: str | None
    candidate_ids: tuple[str, ...]
    candidate_pair_relation: str
    candidate_outcomes: tuple[CandidateOutcomeEvidence, ...]
    counterfactual_outcome_matrix: CounterfactualOutcomeMatrix
    intent_belief: IntentBelief
    query_proposal: Mapping[str, Any] | None
    answer_channel: AnswerChannelModel
    query_budget: int
    active_query_id: str | None
    monotonic_now: float
    answer_deadline_monotonic: float | None
    time_to_decision_status: str
    wait_opportunity: WaitOpportunityModel
    hard_gate_envelope: HardGateEnvelope
    evidence_masks: tuple[tuple[str, Any], ...]
    operating_parameters: OperatingParameters
    provenance: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if len(set(self.candidate_ids)) != len(self.candidate_ids) or not self.candidate_ids:
            raise ValueError("DECISION_CANDIDATE_IDS_INVALID")
        if self.query_budget < 0:
            raise ValueError("QUERY_BUDGET_MUST_BE_NONNEGATIVE")
        if not math.isfinite(float(self.monotonic_now)):
            raise ValueError("MONOTONIC_NOW_INVALID")
        if self.answer_deadline_monotonic is not None and not math.isfinite(
            float(self.answer_deadline_monotonic)
        ):
            raise ValueError("ANSWER_DEADLINE_INVALID")
        if self.counterfactual_outcome_matrix.candidate_ids != self.candidate_ids:
            raise ValueError("DECISION_MATRIX_CANDIDATE_IDS_MISMATCH")
        self.intent_belief.validate_for(self.candidate_ids)
        self.answer_channel.validate_for(self.candidate_ids)

    def evidence_dict(self) -> dict[str, Any]:
        return dict(self.evidence_masks)


@dataclass(frozen=True)
class DecisionRecommendation(SerializableContract):
    decision: Decision
    act_target_type: ActTargetType
    selected_candidate_id: str | None
    equivalence_class_candidate_ids: tuple[str, ...]
    query_id: str | None
    wait_mode: WaitMode
    expected_loss_channels: tuple[tuple[str, ExpectedLossChannels], ...]
    query_value: float | None
    wait_value: float | None
    decision_confidence_status: str
    authorization_eligible: bool
    used_for_control: bool
    reason_trace: tuple[str, ...]
    provenance: tuple[str, ...]
    decision_predicates: tuple[tuple[str, Any], ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.authorization_eligible or self.used_for_control:
            raise ValueError("M2B_RECOMMENDATION_CANNOT_AUTHORIZE_CONTROL")
        if self.decision is not Decision.ACT and self.selected_candidate_id is not None:
            raise ValueError("NON_ACT_RECOMMENDATION_CANNOT_SELECT_CANDIDATE")
        if self.act_target_type is ActTargetType.EQUIVALENCE_CLASS and self.selected_candidate_id is not None:
            raise ValueError("EQUIVALENCE_CLASS_ACT_CANNOT_DEFAULT_TO_CANDIDATE")


def immutable_runtime_hash(context: DecisionContext, recommendation: DecisionRecommendation) -> str:
    value = {"context": context.to_dict(), "recommendation": recommendation.to_dict()}
    assert_no_forbidden_runtime_keys(value)
    return stable_sha256(value)
