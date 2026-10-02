"""Immutable closed contracts for non-blind offline replay."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .serialization import (
    canonical_sha256, freeze_json, is_finite_real, require_sha256,
    to_json_compatible, validate_finite_json,
)


DESIGN_VERSION = "driveclarify.m3.offline-replay.design.v1"
EPISODE_SCHEMA = "driveclarify.m3.replay-episode.v1"
RECORD_SCHEMA = "driveclarify.m3.replay-record.v1"
ORACLE_SCHEMA = "driveclarify.m3.replay-oracle.v1"
TRACE_SCHEMA = "driveclarify.m3.replay-trace.v1"


class SourceTier(str, Enum):
    TIER_A_FROZEN_CONFORMANCE = "TIER_A_FROZEN_CONFORMANCE"
    TIER_B_NONBLIND_SYNTHETIC_DEV = "TIER_B_NONBLIND_SYNTHETIC_DEV"
    TIER_C_RECORDED_NONBLIND_TRAIN_DEV = "TIER_C_RECORDED_NONBLIND_TRAIN_DEV"
    TIER_D_NONBLIND_SCHEDULED_LIFECYCLE_DEV = "TIER_D_NONBLIND_SCHEDULED_LIFECYCLE_DEV"


class TimeMappingMode(str, Enum):
    PRESERVE_RELATIVE_MONOTONIC_DELTAS = "PRESERVE_RELATIVE_MONOTONIC_DELTAS"
    EXPLICIT_REPLAY_MONOTONIC_TIMELINE = "EXPLICIT_REPLAY_MONOTONIC_TIMELINE"


class SelectedAction(str, Enum):
    ACT = "ACT"
    ASK = "ASK"
    WAIT = "WAIT"
    FALLBACK = "FALLBACK"


def _closed(value: Mapping[str, Any], required: tuple[str, ...], label: str) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(label + " must be an object")
    actual = set(value)
    expected = set(required)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{label} fields mismatch missing={missing} unknown={unknown}")


def _string(value: Any, label: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if type(value) is not str or not value:
        raise ValueError(label + " must be a nonempty string")


def _enum(value: Any, enum: type[Enum], label: str) -> Any:
    try:
        return value if isinstance(value, enum) else enum(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid " + label) from exc


def _bool(value: Any, label: str) -> None:
    if type(value) is not bool:
        raise TypeError(label + " must be boolean")


def _reject_control_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        forbidden = {"throttle", "brake", "steer", "pid", "control_command"}
        overlap = forbidden & set(value)
        if overlap:
            raise ValueError("low-level control field rejected: " + sorted(overlap)[0])
        for item in value.values():
            _reject_control_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_control_fields(item)


@dataclass(frozen=True)
class M2BDecisionEnvelope:
    decision_id: str
    source_observation_id: str | None
    source_frame_id: str | None
    candidate_set_id: str | None
    selected_action: SelectedAction
    selected_candidate_id: str | None
    decision_monotonic_time: int | float
    decision_deadline_monotonic: int | float | None
    answer_deadline_monotonic: int | float | None
    query_episode_id: str | None
    query_identity: str | None
    query_budget: int | float | None
    candidate_freshness: str
    evidence_grade: str
    holding_lease_payload: Mapping[str, Any] | None
    reason_codes: tuple[str, ...]
    source_policy_version: str
    source_record_sha256: str

    FIELDS = ("decision_id", "source_observation_id", "source_frame_id",
              "candidate_set_id", "selected_action", "selected_candidate_id",
              "decision_monotonic_time", "decision_deadline_monotonic",
              "answer_deadline_monotonic", "query_episode_id", "query_identity",
              "query_budget", "candidate_freshness", "evidence_grade",
              "holding_lease_payload", "reason_codes", "source_policy_version",
              "source_record_sha256")

    def __post_init__(self) -> None:
        _string(self.decision_id, "decision_id")
        for name in ("source_observation_id", "source_frame_id", "candidate_set_id",
                     "selected_candidate_id", "query_episode_id", "query_identity"):
            value = getattr(self, name)
            if value is not None and type(value) is not str:
                raise TypeError(name + " must be string or null")
        if not isinstance(self.selected_action, SelectedAction):
            raise ValueError("invalid selected_action")
        if not is_finite_real(self.decision_monotonic_time):
            raise ValueError("decision_monotonic_time must be finite")
        for name in ("decision_deadline_monotonic", "answer_deadline_monotonic",
                     "query_budget"):
            value = getattr(self, name)
            if value is not None and not is_finite_real(value):
                raise ValueError(name + " must be finite or null")
        if self.candidate_freshness not in {"FRESH", "STALE", "UNKNOWN"}:
            raise ValueError("invalid candidate_freshness")
        _string(self.evidence_grade, "evidence_grade")
        if self.holding_lease_payload is not None:
            object.__setattr__(self, "holding_lease_payload",
                               freeze_json(self.holding_lease_payload))
        if not isinstance(self.reason_codes, tuple) or any(
                type(item) is not str for item in self.reason_codes):
            raise TypeError("reason_codes must be tuple[str]")
        _string(self.source_policy_version, "source_policy_version")
        require_sha256(self.source_record_sha256, "source_record_sha256")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "M2BDecisionEnvelope":
        _closed(value, cls.FIELDS, "M2BDecisionEnvelope")
        reasons = value["reason_codes"]
        if not isinstance(reasons, (list, tuple)):
            raise TypeError("reason_codes must be an array")
        return cls(**{**value, "selected_action": _enum(
            value["selected_action"], SelectedAction, "selected_action"),
            "reason_codes": tuple(reasons)})

    def to_dict(self) -> dict[str, Any]:
        return {name: to_json_compatible(getattr(self, name)) for name in self.FIELDS}


@dataclass(frozen=True)
class ReplayRecord:
    record_id: str
    episode_id: str
    sequence_index: int
    record_type: str
    source_component: str
    source_observation_id: str | None
    source_frame_id: str | None
    candidate_set_id: str | None
    query_episode_id: str | None
    source_simulation_time: int | float | None
    source_monotonic_time: int | float | None
    replay_monotonic_time: int | float | None
    calendar_utc: str | None
    concurrent_group_id: str | None
    payload: Mapping[str, Any]
    payload_digest: str
    source_record_sha256: str
    provenance_grade: str
    adapter_id: str
    adapter_version: str

    FIELDS = ("record_id", "episode_id", "sequence_index", "record_type",
              "source_component", "source_observation_id", "source_frame_id",
              "candidate_set_id", "query_episode_id", "source_simulation_time",
              "source_monotonic_time", "replay_monotonic_time", "calendar_utc",
              "concurrent_group_id", "payload", "payload_digest",
              "source_record_sha256", "provenance_grade", "adapter_id",
              "adapter_version")
    RECORD_TYPES = frozenset({
        "CANDIDATES_READY", "M2B_DECISION", "PASSENGER_ANSWER", "QUERY_TIMEOUT",
        "QUERY_CANCEL", "WORLD_STATE_CHANGE", "CANDIDATE_STALE",
        "HOLDING_CAPABILITY_LOST", "SAFETY_PREEMPTION", "LEASE_EXPIRY_CHECK",
        "REVALIDATION_PASS", "REVALIDATION_FAIL", "REVALIDATION_RESULT",
        "REPLAN_RESULT",
    })
    PROVENANCE = frozenset({
        "FROZEN_CONFORMANCE",
        "NONBLIND_SYNTHETIC_CONTRACT_TEST_ONLY",
        "RECORDED_NONBLIND_TRAIN_DEV",
        "NONBLIND_SCHEDULED_LIFECYCLE_DEV",
    })

    def __post_init__(self) -> None:
        for name in ("record_id", "episode_id", "record_type", "source_component",
                     "provenance_grade", "adapter_id", "adapter_version"):
            _string(getattr(self, name), name)
        if type(self.sequence_index) is not int or self.sequence_index < 0:
            raise ValueError("sequence_index must be a nonnegative integer")
        if self.record_type not in self.RECORD_TYPES:
            raise ValueError("unsupported record_type")
        if self.provenance_grade not in self.PROVENANCE:
            raise ValueError("unsupported provenance_grade")
        for name in ("source_observation_id", "source_frame_id", "candidate_set_id",
                     "query_episode_id", "calendar_utc", "concurrent_group_id"):
            value = getattr(self, name)
            if value is not None and type(value) is not str:
                raise TypeError(name + " must be string or null")
        for name in ("source_simulation_time", "source_monotonic_time",
                     "replay_monotonic_time"):
            value = getattr(self, name)
            if value is not None and not is_finite_real(value):
                raise ValueError(name + " must be finite or null")
        validate_finite_json(self.payload)
        _reject_control_fields(self.payload)
        require_sha256(self.payload_digest, "payload_digest")
        require_sha256(self.source_record_sha256, "source_record_sha256")
        if canonical_sha256(self.payload) != self.payload_digest:
            raise ValueError("payload digest mismatch")
        if canonical_sha256(self._hash_projection()) != self.source_record_sha256:
            raise ValueError("source record hash mismatch")
        object.__setattr__(self, "payload", freeze_json(self.payload))

    def _hash_projection(self) -> dict[str, Any]:
        return {name: to_json_compatible(getattr(self, name))
                for name in self.FIELDS if name != "source_record_sha256"}

    @classmethod
    def create(cls, **values: Any) -> "ReplayRecord":
        payload = values["payload"]
        values["payload_digest"] = canonical_sha256(payload)
        provisional = {name: values[name] for name in cls.FIELDS
                       if name != "source_record_sha256"}
        values["source_record_sha256"] = canonical_sha256(provisional)
        return cls(**values)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplayRecord":
        _closed(value, cls.FIELDS, "ReplayRecord")
        return cls(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return {name: to_json_compatible(getattr(self, name)) for name in self.FIELDS}


@dataclass(frozen=True)
class ReplayEpisode:
    episode_id: str
    schema_version: str
    source_tier: SourceTier
    source_artifact_id: str
    source_artifact_sha256: str
    source_record_count: int
    source_provenance: Mapping[str, Any]
    non_blind_confirmation: bool
    anti_overfitting_confirmation: bool
    initial_state: Mapping[str, Any]
    initial_state_sha256: str
    time_mapping_mode: TimeMappingMode
    episode_start_source_time: int | float | None
    episode_start_replay_monotonic_time: int | float
    records: tuple[ReplayRecord, ...]
    records_sha256: str
    runner_visible_oracle_fields: tuple[Any, ...]
    expected_low_level_control_outputs: tuple[Any, ...]
    creation_utc: str
    design_version: str

    FIELDS = ("episode_id", "schema_version", "source_tier",
              "source_artifact_id", "source_artifact_sha256", "source_record_count",
              "source_provenance", "non_blind_confirmation",
              "anti_overfitting_confirmation", "initial_state",
              "initial_state_sha256", "time_mapping_mode",
              "episode_start_source_time", "episode_start_replay_monotonic_time",
              "records", "records_sha256", "runner_visible_oracle_fields",
              "expected_low_level_control_outputs", "creation_utc", "design_version")

    def __post_init__(self) -> None:
        _string(self.episode_id, "episode_id")
        if self.schema_version != EPISODE_SCHEMA:
            raise ValueError("episode schema mismatch")
        if not isinstance(self.source_tier, SourceTier):
            raise ValueError("unknown source tier")
        _string(self.source_artifact_id, "source_artifact_id")
        require_sha256(self.source_artifact_sha256, "source_artifact_sha256")
        if type(self.source_record_count) is not int or self.source_record_count < 0:
            raise ValueError("invalid source_record_count")
        _bool(self.non_blind_confirmation, "non_blind_confirmation")
        _bool(self.anti_overfitting_confirmation, "anti_overfitting_confirmation")
        validate_finite_json(self.source_provenance)
        validate_finite_json(self.initial_state)
        require_sha256(self.initial_state_sha256, "initial_state_sha256")
        if canonical_sha256(self.initial_state) != self.initial_state_sha256:
            raise ValueError("initial state hash mismatch")
        if not isinstance(self.time_mapping_mode, TimeMappingMode):
            raise ValueError("invalid time mapping mode")
        if self.episode_start_source_time is not None and not is_finite_real(
                self.episode_start_source_time):
            raise ValueError("episode_start_source_time must be finite or null")
        if not is_finite_real(self.episode_start_replay_monotonic_time):
            raise ValueError("episode_start_replay_monotonic_time must be finite")
        if not isinstance(self.records, tuple) or any(
                not isinstance(item, ReplayRecord) for item in self.records):
            raise TypeError("records must be tuple[ReplayRecord]")
        require_sha256(self.records_sha256, "records_sha256")
        if self.source_record_count != len(self.records):
            raise ValueError("record count mismatch")
        if canonical_sha256([item.to_dict() for item in self.records]) != self.records_sha256:
            raise ValueError("records hash mismatch")
        if any(item.episode_id != self.episode_id for item in self.records):
            raise ValueError("record episode identity mismatch")
        if tuple(item.sequence_index for item in self.records) != tuple(range(len(self.records))):
            raise ValueError("record indices must be unique and contiguous")
        # Exact repeated source events remain distinct replay records by their
        # contiguous sequence index.  They must not be silently deduplicated;
        # the bound core owns idempotency semantics for their repeated event ID.
        if self.runner_visible_oracle_fields != ():
            raise ValueError("runner-visible oracle leakage")
        if self.expected_low_level_control_outputs != ():
            raise ValueError("low-level control expectation must be empty")
        _string(self.creation_utc, "creation_utc")
        if self.design_version != DESIGN_VERSION:
            raise ValueError("design version mismatch")
        object.__setattr__(self, "source_provenance", freeze_json(self.source_provenance))
        object.__setattr__(self, "initial_state", freeze_json(self.initial_state))

    @classmethod
    def create(cls, *, episode_id: str, source_tier: SourceTier,
               source_artifact_id: str, source_artifact_sha256: str,
               source_provenance: Mapping[str, Any], initial_state: Mapping[str, Any],
               time_mapping_mode: TimeMappingMode,
               episode_start_source_time: int | float | None,
               episode_start_replay_monotonic_time: int | float,
               records: tuple[ReplayRecord, ...], creation_utc: str) -> "ReplayEpisode":
        return cls(
            episode_id=episode_id, schema_version=EPISODE_SCHEMA,
            source_tier=source_tier, source_artifact_id=source_artifact_id,
            source_artifact_sha256=source_artifact_sha256,
            source_record_count=len(records), source_provenance=source_provenance,
            non_blind_confirmation=True, anti_overfitting_confirmation=True,
            initial_state=initial_state, initial_state_sha256=canonical_sha256(initial_state),
            time_mapping_mode=time_mapping_mode,
            episode_start_source_time=episode_start_source_time,
            episode_start_replay_monotonic_time=episode_start_replay_monotonic_time,
            records=records, records_sha256=canonical_sha256(
                [item.to_dict() for item in records]),
            runner_visible_oracle_fields=(), expected_low_level_control_outputs=(),
            creation_utc=creation_utc, design_version=DESIGN_VERSION)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplayEpisode":
        _closed(value, cls.FIELDS, "ReplayEpisode")
        records = value["records"]
        if not isinstance(records, (list, tuple)):
            raise TypeError("records must be an array")
        return cls(**{**value,
            "source_tier": _enum(value["source_tier"], SourceTier, "source_tier"),
            "time_mapping_mode": _enum(value["time_mapping_mode"], TimeMappingMode,
                                       "time_mapping_mode"),
            "records": tuple(item if isinstance(item, ReplayRecord)
                             else ReplayRecord.from_dict(item) for item in records),
            "runner_visible_oracle_fields": tuple(value["runner_visible_oracle_fields"]),
            "expected_low_level_control_outputs": tuple(
                value["expected_low_level_control_outputs"])})

    def to_dict(self) -> dict[str, Any]:
        return {name: ([item.to_dict() for item in self.records] if name == "records"
                       else to_json_compatible(getattr(self, name))) for name in self.FIELDS}


@dataclass(frozen=True)
class ReplayOracle:
    oracle_id: str
    episode_id: str
    episode_records_sha256: str
    oracle_schema_version: str
    expected_transition_ids: tuple[str, ...]
    expected_final_state: Mapping[str, Any]
    expected_authority: str
    expected_query_active: bool
    expected_candidate_freshness: str
    expected_lease_status: Mapping[str, Any] | None
    expected_processing_results: tuple[str, ...]
    expected_audit_predicates: tuple[Mapping[str, Any], ...]
    forbidden_paths: tuple[str, ...]
    oracle_provenance: Mapping[str, Any]
    non_blind_only: bool
    creation_utc: str

    FIELDS = ("oracle_id", "episode_id", "episode_records_sha256",
              "oracle_schema_version", "expected_transition_ids",
              "expected_final_state", "expected_authority", "expected_query_active",
              "expected_candidate_freshness", "expected_lease_status",
              "expected_processing_results", "expected_audit_predicates",
              "forbidden_paths", "oracle_provenance", "non_blind_only", "creation_utc")

    def __post_init__(self) -> None:
        _string(self.oracle_id, "oracle_id"); _string(self.episode_id, "episode_id")
        require_sha256(self.episode_records_sha256, "episode_records_sha256")
        if self.oracle_schema_version != ORACLE_SCHEMA:
            raise ValueError("oracle schema mismatch")
        for name in ("expected_transition_ids", "expected_processing_results",
                     "forbidden_paths"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or any(type(item) is not str for item in value):
                raise TypeError(name + " must be tuple[str]")
        _string(self.expected_authority, "expected_authority")
        _bool(self.expected_query_active, "expected_query_active")
        if self.expected_candidate_freshness not in {"FRESH", "STALE", "UNKNOWN"}:
            raise ValueError("invalid expected_candidate_freshness")
        if not isinstance(self.expected_audit_predicates, tuple):
            raise TypeError("expected_audit_predicates must be tuple")
        _bool(self.non_blind_only, "non_blind_only")
        if not self.non_blind_only:
            raise ValueError("oracle must be non-blind")
        for name in ("expected_final_state", "expected_lease_status",
                     "expected_audit_predicates", "oracle_provenance"):
            validate_finite_json(getattr(self, name))
        _string(self.creation_utc, "creation_utc")
        object.__setattr__(self, "expected_final_state", freeze_json(self.expected_final_state))
        if self.expected_lease_status is not None:
            object.__setattr__(self, "expected_lease_status",
                               freeze_json(self.expected_lease_status))
        object.__setattr__(self, "expected_audit_predicates",
                           tuple(freeze_json(item) for item in self.expected_audit_predicates))
        object.__setattr__(self, "oracle_provenance", freeze_json(self.oracle_provenance))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplayOracle":
        _closed(value, cls.FIELDS, "ReplayOracle")
        return cls(**{**value,
            "expected_transition_ids": tuple(value["expected_transition_ids"]),
            "expected_processing_results": tuple(value["expected_processing_results"]),
            "expected_audit_predicates": tuple(value["expected_audit_predicates"]),
            "forbidden_paths": tuple(value["forbidden_paths"])})

    def to_dict(self) -> dict[str, Any]:
        return {name: to_json_compatible(getattr(self, name)) for name in self.FIELDS}


@dataclass(frozen=True)
class AdapterRejection:
    record_id: str
    adapter_id: str
    adapter_version: str
    category: str
    detail_code: str
    input_unchanged: bool = True
    state_unchanged: bool = True
    continuation_disposition: str = "CONTINUE_EPISODE"

    def to_dict(self) -> dict[str, Any]:
        return to_json_compatible(self)


@dataclass(frozen=True)
class EpisodeRejection:
    episode_id: str | None
    category: str
    detail: str
    reducer_call_count: int = 0
    partial_trace: bool = False
    metrics_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return to_json_compatible(self)


@dataclass(frozen=True)
class ReplayStepResult:
    episode_id: str
    record_id: str
    sequence_index: int
    pre_state_sha256: str
    adapted_event_sha256: str | None
    adapter_result: str
    reduction_processing_result: str
    executed_transition_ids: tuple[str, ...]
    post_state_sha256: str
    authority_before: str
    authority_after: str
    query_active_before: bool
    query_active_after: bool
    candidate_freshness_before: str
    candidate_freshness_after: str
    lease_status_before: Mapping[str, Any] | None
    lease_status_after: Mapping[str, Any] | None
    audit_records: tuple[Mapping[str, Any], ...]
    business_mutation: bool
    low_level_control_output_count: int
    step_trace_sha256: str

    def __post_init__(self) -> None:
        for name in ("pre_state_sha256", "post_state_sha256", "step_trace_sha256"):
            require_sha256(getattr(self, name), name)
        if self.adapted_event_sha256 is not None:
            require_sha256(self.adapted_event_sha256, "adapted_event_sha256")
        object.__setattr__(self, "audit_records",
                           tuple(freeze_json(item) for item in self.audit_records))
        if self.lease_status_before is not None:
            object.__setattr__(self, "lease_status_before",
                               freeze_json(self.lease_status_before))
        if self.lease_status_after is not None:
            object.__setattr__(self, "lease_status_after", freeze_json(self.lease_status_after))

    @classmethod
    def create(cls, **values: Any) -> "ReplayStepResult":
        values["step_trace_sha256"] = canonical_sha256(values)
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return to_json_compatible(self)


@dataclass(frozen=True)
class ReplayTrace:
    trace_id: str
    episode_id: str
    episode_records_sha256: str
    runner_version: str
    adapter_versions: tuple[str, ...]
    minimal_core_package_sha256: str
    initial_state_sha256: str
    step_results: tuple[ReplayStepResult, ...]
    step_count: int
    accepted_record_count: int
    rejected_record_count: int
    transition_ids: tuple[str, ...]
    final_state: Mapping[str, Any]
    final_state_sha256: str
    authority_sequence: tuple[str, ...]
    query_lifecycle_summary: Mapping[str, Any]
    lease_lifecycle_summary: Mapping[str, Any]
    invariant_violation_count: int
    partial_mutation_count: int
    second_query_count: int
    stale_unknown_act_count: int
    low_level_control_output_count: int
    time_mapping_mode: str
    ordered_record_ids: tuple[str, ...]
    processing_result_sequence: tuple[str, ...]
    audit_reason_sequence: tuple[str, ...]
    first_rejection_index: int | None
    terminal_disposition: str
    source_binding_sha256: str
    reducer_call_count: int
    trace_sha256: str

    def __post_init__(self) -> None:
        for name in ("trace_id", "episode_records_sha256", "minimal_core_package_sha256",
                     "initial_state_sha256", "final_state_sha256",
                     "source_binding_sha256", "trace_sha256"):
            require_sha256(getattr(self, name), name)
        if self.step_count != len(self.step_results):
            raise ValueError("step_count mismatch")
        object.__setattr__(self, "final_state", freeze_json(self.final_state))
        object.__setattr__(self, "query_lifecycle_summary",
                           freeze_json(self.query_lifecycle_summary))
        object.__setattr__(self, "lease_lifecycle_summary",
                           freeze_json(self.lease_lifecycle_summary))

    @classmethod
    def create(cls, **values: Any) -> "ReplayTrace":
        values["trace_sha256"] = canonical_sha256(values)
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return to_json_compatible(self)


@dataclass(frozen=True)
class ReplayEvaluationResult:
    accepted: bool
    rejection_category: str | None
    metrics: Mapping[str, Any]
    metrics_count: int
    trace_sha256: str
    oracle_id: str
    result_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", freeze_json(self.metrics))

    @classmethod
    def create(cls, *, accepted: bool, rejection_category: str | None,
               metrics: Mapping[str, Any], trace_sha256: str,
               oracle_id: str) -> "ReplayEvaluationResult":
        base = {"accepted": accepted, "rejection_category": rejection_category,
                "metrics": metrics, "metrics_count": len(metrics),
                "trace_sha256": trace_sha256, "oracle_id": oracle_id}
        return cls(**base, result_sha256=canonical_sha256(base))

    def to_dict(self) -> dict[str, Any]:
        return to_json_compatible(self)
