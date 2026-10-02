"""Lifecycle-aware capture of the frozen non-blind M2B TRAIN/DEV producer.

The module is deliberately a wrapper around the existing M2B policy.  It does
not alter a policy-selected action, infer a historical deadline, or create
holding evidence.  Identity-only metadata is derived from canonical hashes of
the producer inputs that are present at capture time.
"""

from __future__ import annotations

import hashlib
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_m3_minimal_core import (
    ControlAuthority,
    EvidenceGrade,
    LifecycleState,
    MinimalM3State,
    check_invariants,
)

from .adapters import ADAPTER_ID, ADAPTER_VERSION
from .contracts import (
    M2BDecisionEnvelope,
    ReplayEpisode,
    ReplayRecord,
    SelectedAction,
    SourceTier,
    TimeMappingMode,
)
from .serialization import (
    canonical_sha256,
    freeze_json,
    is_finite_real,
    require_sha256,
    strict_json_loads,
    to_json_compatible,
    validate_finite_json,
)


LIFECYCLE_RECORD_SCHEMA = (
    "driveclarify.m3.lifecycle-aware-m2b-decision-record.v1"
)
LIFECYCLE_DATASET_SCHEMA = (
    "driveclarify.m3.lifecycle-aware-m2b-decision-dataset.v1"
)
CAPTURE_SCHEDULE_SCHEMA = "driveclarify.m3.nonblind-capture-schedule.v1"
LIFECYCLE_ADMISSION_SCHEMA = (
    "driveclarify.m3.lifecycle-aware-m2b-admission.v1"
)
CAPTURE_PRODUCER_VERSION = (
    "driveclarify.m3.lifecycle-aware-nonblind-capture.v1"
)
SOURCE_POLICY_NAME = "driveclarify_decision.OfflineQueryValuePolicy"
DERIVED_OFFLINE_SNAPSHOT_ID = "DERIVED_OFFLINE_SNAPSHOT_ID"
SOURCE_RECORDED_FRAME_ID = "SOURCE_RECORDED_FRAME_ID"
NEW_CAPTURE_EXPLICIT_CANONICAL_ACTION_EMISSION = (
    "NEW_CAPTURE_EXPLICIT_CANONICAL_ACTION_EMISSION"
)
IDENTITY_ACTION_PRESERVED = "IDENTITY_ACTION_PRESERVED"
LEGACY_FALLBACK_ALIAS_VERDICT = "NOT_PROVEN"
RECORDED_PROVENANCE_GRADE = "RECORDED_NONBLIND_TRAIN_DEV"

_RAW_ACTIONS = frozenset({"ACT", "ASK", "WAIT", "FALLBACK_RECOMMENDED"})
_CANONICAL_ACTIONS = frozenset({"ACT", "ASK", "WAIT", "FALLBACK"})
_EVIDENCE_GRADES = frozenset(item.value for item in EvidenceGrade)
_SCHEDULE_KINDS = frozenset({
    "EXPLICIT_CONSTANT_DECISION_DEADLINE",
    "EXPLICIT_BY_SOURCE_CASE_DECISION_DEADLINE",
})
_FORBIDDEN_SOURCE_PATH_TOKENS = (
    "FORMAL_LEARNED_M1_TEST", "M2B_R3", "R3_SEALED", "R3_POST",
    "SEALED_BLIND", "BLIND_GOLD",
)


def _closed(value: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(label + " must be an object")
    actual = set(value)
    expected = set(fields)
    if actual != expected:
        raise ValueError(
            f"{label} fields mismatch missing={sorted(expected - actual)} "
            f"unknown={sorted(actual - expected)}"
        )


def _string(value: Any, label: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if type(value) is not str or not value:
        raise ValueError(label + " must be a nonempty string")


def _bool(value: Any, label: str) -> None:
    if type(value) is not bool:
        raise TypeError(label + " must be boolean")


def _finite(value: Any, label: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not is_finite_real(value):
        raise ValueError(label + " must be a finite real")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class LifecycleAwareM2BDecisionRecordV1:
    """Closed, immutable, self-hashed capture of one M2B decision."""

    schema_version: str
    record_id: str
    split: str
    source_artifact_id: str
    source_artifact_sha256: str
    source_case_id: str
    source_profile_id: str
    source_observation_id: str
    source_frame_id: str
    frame_identity_kind: str
    observation_payload_sha256: str
    candidate_set_id: str
    candidate_ids: tuple[str, ...]
    candidate_set_sha256: str
    counterfactual_matrix_sha256: str
    selected_action_raw: str
    selected_action_canonical: str
    action_mapping_kind: str
    action_mapping_evidence: Mapping[str, Any]
    selected_candidate_id: str | None
    decision_monotonic_time: int | float
    decision_deadline_monotonic: int | float
    answer_deadline_monotonic: int | float
    query_episode_id: str | None
    query_identity: str | None
    query_budget: int
    candidate_freshness: str
    decision_evidence_grade: str
    holding_evidence_grade: str
    holding_lease: Mapping[str, Any] | None
    initial_m3_state: Mapping[str, Any]
    initial_m3_state_sha256: str
    source_policy_name: str
    source_policy_version: str
    producer_version: str
    creation_provenance: Mapping[str, Any]
    record_sha256: str

    FIELDS = (
        "schema_version", "record_id", "split", "source_artifact_id",
        "source_artifact_sha256", "source_case_id", "source_profile_id",
        "source_observation_id", "source_frame_id", "frame_identity_kind",
        "observation_payload_sha256", "candidate_set_id", "candidate_ids",
        "candidate_set_sha256", "counterfactual_matrix_sha256",
        "selected_action_raw", "selected_action_canonical",
        "action_mapping_kind", "action_mapping_evidence",
        "selected_candidate_id", "decision_monotonic_time",
        "decision_deadline_monotonic", "answer_deadline_monotonic",
        "query_episode_id", "query_identity", "query_budget",
        "candidate_freshness", "decision_evidence_grade",
        "holding_evidence_grade", "holding_lease", "initial_m3_state",
        "initial_m3_state_sha256", "source_policy_name",
        "source_policy_version", "producer_version", "creation_provenance",
        "record_sha256",
    )

    def __post_init__(self) -> None:
        if self.schema_version != LIFECYCLE_RECORD_SCHEMA:
            raise ValueError("lifecycle-aware record schema mismatch")
        for name in (
                "record_id", "source_artifact_id", "source_case_id",
                "source_profile_id", "source_observation_id", "source_frame_id",
                "candidate_set_id", "source_policy_name", "source_policy_version",
                "producer_version"):
            _string(getattr(self, name), name)
        if self.split not in {"TRAIN", "DEV"}:
            raise ValueError("split must be non-blind TRAIN or DEV")
        for name in (
                "source_artifact_sha256", "observation_payload_sha256",
                "candidate_set_sha256", "counterfactual_matrix_sha256",
                "initial_m3_state_sha256", "record_sha256"):
            require_sha256(getattr(self, name), name)
        if self.frame_identity_kind not in {
                DERIVED_OFFLINE_SNAPSHOT_ID, SOURCE_RECORDED_FRAME_ID}:
            raise ValueError("unsupported frame identity kind")
        if self.frame_identity_kind == DERIVED_OFFLINE_SNAPSHOT_ID:
            expected_frame_id = "M2B-OFFLINE-SNAPSHOT-" + canonical_sha256({
                "source_artifact_sha256": self.source_artifact_sha256,
                "source_case_id": self.source_case_id,
                "source_profile_id": self.source_profile_id,
                "observation_payload_sha256": self.observation_payload_sha256,
            })
            if self.source_frame_id != expected_frame_id:
                raise ValueError("derived offline frame identity mismatch")
        if (not isinstance(self.candidate_ids, tuple) or not self.candidate_ids or
                any(type(item) is not str or not item for item in self.candidate_ids) or
                len(set(self.candidate_ids)) != len(self.candidate_ids)):
            raise ValueError("candidate_ids must be unique nonempty strings")
        expected_candidate_set_id = "M2B-CANDIDATE-SET-" + self.candidate_set_sha256
        if self.candidate_set_id != expected_candidate_set_id:
            raise ValueError("candidate set ID/digest mismatch")
        if self.selected_action_raw not in _RAW_ACTIONS:
            raise ValueError("unsupported raw M2B action")
        if self.selected_action_canonical not in _CANONICAL_ACTIONS:
            raise ValueError("unsupported canonical M3 action")
        if self.selected_action_raw == "FALLBACK_RECOMMENDED":
            if (self.selected_action_canonical != "FALLBACK" or
                    self.action_mapping_kind !=
                    NEW_CAPTURE_EXPLICIT_CANONICAL_ACTION_EMISSION):
                raise ValueError("legacy fallback may only use new-capture emission")
        elif (self.selected_action_canonical != self.selected_action_raw or
              self.action_mapping_kind != IDENTITY_ACTION_PRESERVED):
            raise ValueError("non-fallback action identity changed")
        validate_finite_json(self.action_mapping_evidence)
        if (self.action_mapping_evidence.get("legacy_fallback_alias_verdict") !=
                LEGACY_FALLBACK_ALIAS_VERDICT):
            raise ValueError("legacy fallback alias verdict must remain NOT_PROVEN")
        if self.action_mapping_evidence.get("raw_action_preserved") is not True:
            raise ValueError("raw action preservation attestation missing")
        if self.action_mapping_evidence.get("policy_selected_action_changed") is not False:
            raise ValueError("policy action change is forbidden")
        if (self.action_mapping_evidence.get(
                "canonical_action_emitted_by_versioned_recorder") is not True or
                self.action_mapping_evidence.get(
                    "m2b_producer_exact_alias_claimed") is not False):
            raise ValueError("new recorder canonical-emission attestation missing")
        if self.selected_action_raw != "ACT" and self.selected_candidate_id is not None:
            raise ValueError("non-ACT record cannot select a candidate")
        if self.selected_candidate_id is not None:
            _string(self.selected_candidate_id, "selected_candidate_id")
            if self.selected_candidate_id not in self.candidate_ids:
                raise ValueError("selected candidate is outside candidate set")
        for name in (
                "decision_monotonic_time", "decision_deadline_monotonic",
                "answer_deadline_monotonic"):
            _finite(getattr(self, name), name)
        if not (self.decision_monotonic_time <= self.answer_deadline_monotonic <=
                self.decision_deadline_monotonic):
            raise ValueError("capture deadline temporal order is invalid")
        for name in ("query_episode_id", "query_identity"):
            _string(getattr(self, name), name, nullable=True)
        if (self.query_episode_id is None) != (self.query_identity is None):
            raise ValueError("query episode and identity must be jointly present")
        if self.selected_action_raw == "ASK" and self.query_identity is None:
            raise ValueError("ASK requires captured query identity")
        if self.selected_action_raw not in {"ASK", "WAIT"} and (
                self.query_identity is not None or self.query_episode_id is not None):
            raise ValueError("non-query action cannot carry query identity")
        if type(self.query_budget) is not int or self.query_budget < 0:
            raise ValueError("query_budget must be a nonnegative integer")
        if self.candidate_freshness not in {"FRESH", "STALE", "UNKNOWN"}:
            raise ValueError("invalid candidate freshness")
        if self.decision_evidence_grade not in _EVIDENCE_GRADES:
            raise ValueError("invalid decision evidence grade")
        if self.holding_evidence_grade not in _EVIDENCE_GRADES:
            raise ValueError("invalid holding evidence grade")
        if (self.holding_evidence_grade !=
                EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value or
                self.holding_lease is not None):
            raise ValueError("recorded source has no admissible holding evidence")
        validate_finite_json(self.initial_m3_state)
        if canonical_sha256(self.initial_m3_state) != self.initial_m3_state_sha256:
            raise ValueError("initial M3 state digest mismatch")
        state = MinimalM3State.from_dict(self.initial_m3_state)
        if check_invariants(state):
            raise ValueError("initial M3 state invariant failure")
        if (state.lifecycle_state is not LifecycleState.DECISION_READY or
                state.query_active or state.query_episode_id is not None or
                state.candidate_set_id != self.candidate_set_id or
                state.candidate_freshness != self.candidate_freshness or
                state.answer_deadline_monotonic != self.answer_deadline_monotonic or
                state.decision_deadline_monotonic != self.decision_deadline_monotonic or
                state.holding_lease is not None or state.safety_guard_active or
                state.baseline_authority_eligible or
                state.authority is not ControlAuthority.NO_M3_CONTROL_AUTHORITY or
                state.model_forward_count != 0 or state.low_level_control_outputs):
            raise ValueError("initial M3 state is not the captured no-control state")
        validate_finite_json(self.creation_provenance)
        for name in ("non_blind", "r3_excluded", "formal_m1_test_excluded"):
            if self.creation_provenance.get(name) is not True:
                raise ValueError(name + " provenance attestation missing")
        if self.creation_provenance.get("capture_mode") != (
                "NONBLIND_TRAIN_DEV_LIFECYCLE_AWARE"):
            raise ValueError("capture mode provenance mismatch")
        require_sha256(
            self.creation_provenance.get("capture_schedule_sha256"),
            "creation_provenance.capture_schedule_sha256",
        )
        expected_record_id = "M3-LIFECYCLE-CAPTURE-" + canonical_sha256({
            "producer_version": self.producer_version,
            "source_artifact_sha256": self.source_artifact_sha256,
            "source_case_id": self.source_case_id,
            "source_profile_id": self.source_profile_id,
            "capture_schedule_sha256": self.creation_provenance[
                "capture_schedule_sha256"],
        })
        if self.record_id != expected_record_id:
            raise ValueError("deterministic record identity mismatch")
        if canonical_sha256(self._hash_projection()) != self.record_sha256:
            raise ValueError("record self-hash mismatch")
        object.__setattr__(self, "candidate_ids", tuple(self.candidate_ids))
        object.__setattr__(self, "action_mapping_evidence",
                           freeze_json(self.action_mapping_evidence))
        object.__setattr__(self, "creation_provenance",
                           freeze_json(self.creation_provenance))
        object.__setattr__(self, "initial_m3_state", freeze_json(self.initial_m3_state))

    def _hash_projection(self) -> dict[str, Any]:
        return {
            name: to_json_compatible(getattr(self, name))
            for name in self.FIELDS if name != "record_sha256"
        }

    @classmethod
    def create(cls, **values: Any) -> "LifecycleAwareM2BDecisionRecordV1":
        values = dict(values)
        values["schema_version"] = LIFECYCLE_RECORD_SCHEMA
        values["candidate_ids"] = tuple(values["candidate_ids"])
        provisional = {
            name: values[name] for name in cls.FIELDS
            if name != "record_sha256"
        }
        values["record_sha256"] = canonical_sha256(provisional)
        return cls(**values)

    @classmethod
    def from_dict(
            cls, value: Mapping[str, Any]) -> "LifecycleAwareM2BDecisionRecordV1":
        _closed(value, cls.FIELDS, "LifecycleAwareM2BDecisionRecordV1")
        candidate_ids = value["candidate_ids"]
        if not isinstance(candidate_ids, (list, tuple)):
            raise TypeError("candidate_ids must be an array")
        return cls(**{**dict(value), "candidate_ids": tuple(candidate_ids)})

    def to_dict(self) -> dict[str, Any]:
        return {
            name: to_json_compatible(getattr(self, name)) for name in self.FIELDS
        }


_SCHEDULE_FIELDS = (
    "schema_version", "schedule_id", "schedule_kind",
    "decision_deadline_monotonic",
    "decision_deadline_monotonic_by_source_case",
    "frozen_before_policy_execution", "non_blind_splits", "r3_excluded",
    "formal_m1_test_excluded", "baseline_authority_eligible",
)


def build_explicit_capture_schedule(
        decision_deadline_monotonic: int | float, *, schedule_id: str
        ) -> dict[str, Any]:
    """Build a closed schedule with a predeclared constant decision deadline."""
    _finite(decision_deadline_monotonic, "decision_deadline_monotonic")
    _string(schedule_id, "schedule_id")
    return {
        "schema_version": CAPTURE_SCHEDULE_SCHEMA,
        "schedule_id": schedule_id,
        "schedule_kind": "EXPLICIT_CONSTANT_DECISION_DEADLINE",
        "decision_deadline_monotonic": decision_deadline_monotonic,
        "decision_deadline_monotonic_by_source_case": {},
        "frozen_before_policy_execution": True,
        "non_blind_splits": ["DEV", "TRAIN"],
        "r3_excluded": True,
        "formal_m1_test_excluded": True,
        "baseline_authority_eligible": False,
    }


def _validate_schedule(value: Mapping[str, Any]) -> Mapping[str, Any]:
    _closed(value, _SCHEDULE_FIELDS, "capture schedule")
    if value["schema_version"] != CAPTURE_SCHEDULE_SCHEMA:
        raise ValueError("capture schedule schema mismatch")
    _string(value["schedule_id"], "schedule_id")
    if value["schedule_kind"] not in _SCHEDULE_KINDS:
        raise ValueError("unsupported capture schedule kind")
    for name in (
            "frozen_before_policy_execution", "r3_excluded",
            "formal_m1_test_excluded", "baseline_authority_eligible"):
        _bool(value[name], name)
    if (value["frozen_before_policy_execution"] is not True or
            value["r3_excluded"] is not True or
            value["formal_m1_test_excluded"] is not True or
            value["baseline_authority_eligible"] is not False):
        raise ValueError("capture schedule boundary attestation failed")
    splits = value["non_blind_splits"]
    if (not isinstance(splits, (list, tuple)) or len(splits) != 2 or
            set(splits) != {"TRAIN", "DEV"}):
        raise ValueError("schedule must be exactly TRAIN+DEV")
    per_case = value["decision_deadline_monotonic_by_source_case"]
    if not isinstance(per_case, Mapping) or any(
            type(key) is not str or not key for key in per_case):
        raise TypeError("per-case decision deadlines must be an object")
    for case_id, deadline in per_case.items():
        _finite(deadline, "decision deadline for " + case_id)
    if value["schedule_kind"] == "EXPLICIT_CONSTANT_DECISION_DEADLINE":
        _finite(value["decision_deadline_monotonic"],
                "decision_deadline_monotonic")
        if per_case:
            raise ValueError("constant deadline schedule cannot have overrides")
    else:
        if value["decision_deadline_monotonic"] is not None or not per_case:
            raise ValueError("per-case schedule requires only explicit case deadlines")
    validate_finite_json(value)
    return value


def _scheduled_deadline(schedule: Mapping[str, Any], case_id: str) -> int | float:
    if schedule["schedule_kind"] == "EXPLICIT_CONSTANT_DECISION_DEADLINE":
        return schedule["decision_deadline_monotonic"]
    try:
        return schedule["decision_deadline_monotonic_by_source_case"][case_id]
    except KeyError as exc:
        raise ValueError("explicit decision deadline missing for " + case_id) from exc


def _candidate_set_projection(case: Mapping[str, Any]) -> dict[str, Any]:
    ids = case.get("candidate_ids")
    if (not isinstance(ids, list) or not ids or
            any(type(item) is not str or not item for item in ids) or
            len(set(ids)) != len(ids)):
        raise ValueError("runtime case candidate IDs invalid")
    roles = case.get("candidate_roles")
    plans = case.get("symbolic_plans")
    bindings = case.get("hypothesis_bindings")
    if not all(isinstance(item, Mapping) for item in (roles, plans, bindings)):
        raise ValueError("runtime candidate semantics missing")
    if any(set(item) != set(ids) for item in (roles, plans, bindings)):
        raise ValueError("runtime candidate semantic identity mismatch")
    return {
        "candidate_ids_in_producer_order": list(ids),
        "candidate_semantics": [
            {
                "candidate_id": candidate_id,
                "canonical_role": roles[candidate_id],
                "symbolic_plan": plans[candidate_id],
                "hypothesis_binding": bindings[candidate_id],
            }
            for candidate_id in ids
        ],
    }


def _canonical_action(raw: str) -> tuple[str, str]:
    if raw == "FALLBACK_RECOMMENDED":
        return "FALLBACK", NEW_CAPTURE_EXPLICIT_CANONICAL_ACTION_EMISSION
    if raw in {"ACT", "ASK", "WAIT"}:
        return raw, IDENTITY_ACTION_PRESERVED
    raise ValueError("policy emitted an unsupported action")


def _query_identity(
        raw_action: str, context: Mapping[str, Any],
        recommendation: Mapping[str, Any], source_case_id: str,
        source_artifact_sha256: str) -> tuple[str | None, str | None]:
    identity = (recommendation.get("query_id") if raw_action == "ASK" else
                context.get("active_query_id") if raw_action == "WAIT" else None)
    if identity is None:
        return None, None
    _string(identity, "source query identity")
    episode_digest = canonical_sha256({
        "source_artifact_sha256": source_artifact_sha256,
        "source_case_id": source_case_id,
        "source_query_identity": identity,
    })
    return "M3-QUERY-" + episode_digest, identity


def _decision_evidence(
        context: Mapping[str, Any]) -> tuple[str, str]:
    matrix = context.get("counterfactual_outcome_matrix")
    outcomes = context.get("candidate_outcomes")
    if not isinstance(matrix, Mapping) or not isinstance(outcomes, list):
        raise ValueError("policy output decision evidence missing")
    complete = matrix.get("matrix_status") == "COMPLETE_KNOWN"
    supported = bool(outcomes) and all(
        isinstance(item, Mapping) and
        item.get("evidence_status") == "SUFFICIENT_SYMBOLIC_TASK_EVIDENCE"
        for item in outcomes
    )
    if complete and supported:
        return (
            EvidenceGrade.SUPPORTED_BUT_INCOMPLETE.value,
            "COMPLETE_SYMBOLIC_TASK_EVIDENCE_NOT_A_CONTROLLED_PROBE",
        )
    return (
        EvidenceGrade.UNRESOLVED_REQUIRES_ADDITIONAL_PROBE.value,
        "INCOMPLETE_OR_UNKNOWN_SYMBOLIC_TASK_EVIDENCE",
    )


def _validate_policy_output(
        case: Mapping[str, Any], output: Mapping[str, Any]) -> None:
    if output.get("schema_version") != "driveclarify.offline_query_value_runtime_output.v0":
        raise ValueError("unexpected M2B runtime output schema")
    context = output.get("context")
    recommendation = output.get("recommendation")
    if not isinstance(context, Mapping) or not isinstance(recommendation, Mapping):
        raise ValueError("M2B policy output missing context or recommendation")
    exact_pairs = (
        ("decision_id", "decision_id"),
        ("episode_id", "episode_id"),
        ("source_observation_id", "source_observation_id"),
        ("candidate_ids", "candidate_ids"),
        ("monotonic_now", "monotonic_now"),
        ("answer_deadline_monotonic", "answer_deadline_monotonic"),
        ("query_budget", "query_budget"),
        ("active_query_id", "active_query_id"),
    )
    for context_key, case_key in exact_pairs:
        if to_json_compatible(context.get(context_key)) != case.get(case_key):
            raise ValueError("M2B policy context changed source field " + context_key)
    if any(output.get(name) is not False for name in (
            "override_applied", "used_for_control", "authorization_eligible",
            "control_authorized", "live_ask_issued",
            "live_wait_controller_invoked", "vehicle_control_generated")):
        raise ValueError("M2B policy output crossed the no-control boundary")
    if recommendation.get("decision") not in _RAW_ACTIONS:
        raise ValueError("M2B policy recommendation action invalid")


def _capture_one(
        *, case: Mapping[str, Any], unit: Mapping[str, Any],
        policy_output: Mapping[str, Any], schedule: Mapping[str, Any],
        schedule_sha256: str, source_artifact_id: str,
        source_artifact_sha256: str, source_units_sha256: str,
        supplied_creation_provenance: Mapping[str, Any],
        ) -> LifecycleAwareM2BDecisionRecordV1:
    _validate_policy_output(case, policy_output)
    context = policy_output["context"]
    recommendation = policy_output["recommendation"]
    gate = context.get("hard_gate_envelope")
    if not isinstance(gate, Mapping) or gate.get("hard_safety_status") != "PASS":
        raise ValueError("capture requires explicit source hard-safety PASS")
    source_case_id = str(case["m2b_unit_key"])
    source_episode_id = str(case["episode_id"])
    source_profile_id = str(case["operating_profile_id"])
    split = unit["control_plane_identity"]["development_split"]
    if split not in {"TRAIN", "DEV"}:
        raise ValueError("non-TRAIN/DEV unit reached capture")
    source_observation_id = context.get("source_observation_id")
    require_sha256(source_observation_id, "source_observation_id")
    observation_payload_sha256 = source_observation_id
    frame_digest = canonical_sha256({
        "source_artifact_sha256": source_artifact_sha256,
        "source_case_id": source_case_id,
        "source_profile_id": source_profile_id,
        "observation_payload_sha256": observation_payload_sha256,
    })
    source_frame_id = "M2B-OFFLINE-SNAPSHOT-" + frame_digest
    candidate_projection = _candidate_set_projection(case)
    candidate_set_sha256 = canonical_sha256(candidate_projection)
    candidate_set_id = "M2B-CANDIDATE-SET-" + candidate_set_sha256
    matrix = context.get("counterfactual_outcome_matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("counterfactual matrix missing from policy output")
    counterfactual_matrix_sha256 = canonical_sha256(matrix)
    raw_action = recommendation["decision"]
    canonical_action, mapping_kind = _canonical_action(raw_action)
    query_episode_id, query_identity = _query_identity(
        raw_action, context, recommendation, source_case_id,
        source_artifact_sha256,
    )
    decision_time = context["monotonic_now"]
    answer_deadline = context["answer_deadline_monotonic"]
    decision_deadline = _scheduled_deadline(schedule, source_episode_id)
    _finite(answer_deadline, "source answer deadline")
    decision_grade, decision_grade_provenance = _decision_evidence(context)
    initial_state = MinimalM3State(
        lifecycle_state=LifecycleState.DECISION_READY,
        query_episode_id=None,
        query_active=False,
        candidate_set_id=candidate_set_id,
        candidate_freshness=context["hard_gate_envelope"][
            "candidate_freshness_status"],
        answer_deadline_monotonic=answer_deadline,
        decision_deadline_monotonic=decision_deadline,
        authority=ControlAuthority.NO_M3_CONTROL_AUTHORITY,
        safety_guard_active=False,
        baseline_authority_eligible=False,
        holding_lease=None,
        revalidation_required=False,
        replan_required=False,
        current_time_monotonic=decision_time,
        model_forward_count=0,
        low_level_control_outputs=(),
    )
    if check_invariants(initial_state):
        raise ValueError("constructed initial state violates minimal-core invariants")
    initial_state_dict = initial_state.to_dict()
    source_recommendation_sha256 = canonical_sha256(recommendation)
    action_mapping_evidence = {
        "legacy_fallback_alias_verdict": LEGACY_FALLBACK_ALIAS_VERDICT,
        "canonical_action_emitted_by_versioned_recorder": True,
        "m2b_producer_exact_alias_claimed": False,
        "emission_contract": CAPTURE_PRODUCER_VERSION,
        "raw_action_preserved": True,
        "policy_selected_action_changed": False,
        "source_recommendation_sha256": source_recommendation_sha256,
    }
    internal_provenance = {
        "capture_mode": "NONBLIND_TRAIN_DEV_LIFECYCLE_AWARE",
        "non_blind": True,
        "r3_excluded": True,
        "formal_m1_test_excluded": True,
        "source_runtime_case_sha256": canonical_sha256(case),
        "source_episode_id": source_episode_id,
        "source_units_sha256": source_units_sha256,
        "capture_schedule_id": schedule["schedule_id"],
        "capture_schedule_sha256": schedule_sha256,
        "decision_deadline_provenance": "EXPLICIT_FROZEN_CAPTURE_SCHEDULE",
        "decision_evidence_grade_provenance": decision_grade_provenance,
        "holding_evidence_grade_provenance": (
            "SOURCE_HAS_CONTRACT_ONLY_NO_PHYSICAL_HOLDING_EVIDENCE"
        ),
        "source_reason_codes": list(recommendation.get("reason_trace", ())),
        "policy_model_forward_count": 0,
        "policy_selected_action_changed": False,
    }
    creation_provenance = {
        **to_json_compatible(supplied_creation_provenance),
        **internal_provenance,
    }
    record_identity = canonical_sha256({
        "producer_version": CAPTURE_PRODUCER_VERSION,
        "source_artifact_sha256": source_artifact_sha256,
        "source_case_id": source_case_id,
        "source_profile_id": source_profile_id,
        "capture_schedule_sha256": schedule_sha256,
    })
    return LifecycleAwareM2BDecisionRecordV1.create(
        record_id="M3-LIFECYCLE-CAPTURE-" + record_identity,
        split=split,
        source_artifact_id=source_artifact_id,
        source_artifact_sha256=source_artifact_sha256,
        source_case_id=source_case_id,
        source_profile_id=source_profile_id,
        source_observation_id=source_observation_id,
        source_frame_id=source_frame_id,
        frame_identity_kind=DERIVED_OFFLINE_SNAPSHOT_ID,
        observation_payload_sha256=observation_payload_sha256,
        candidate_set_id=candidate_set_id,
        candidate_ids=tuple(context["candidate_ids"]),
        candidate_set_sha256=candidate_set_sha256,
        counterfactual_matrix_sha256=counterfactual_matrix_sha256,
        selected_action_raw=raw_action,
        selected_action_canonical=canonical_action,
        action_mapping_kind=mapping_kind,
        action_mapping_evidence=action_mapping_evidence,
        selected_candidate_id=recommendation.get("selected_candidate_id"),
        decision_monotonic_time=decision_time,
        decision_deadline_monotonic=decision_deadline,
        answer_deadline_monotonic=answer_deadline,
        query_episode_id=query_episode_id,
        query_identity=query_identity,
        query_budget=context["query_budget"],
        candidate_freshness=context["hard_gate_envelope"][
            "candidate_freshness_status"],
        decision_evidence_grade=decision_grade,
        holding_evidence_grade=EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value,
        holding_lease=None,
        initial_m3_state=initial_state_dict,
        initial_m3_state_sha256=canonical_sha256(initial_state_dict),
        source_policy_name=SOURCE_POLICY_NAME,
        source_policy_version=recommendation["schema_version"],
        producer_version=CAPTURE_PRODUCER_VERSION,
        creation_provenance=creation_provenance,
    )


def _validate_source_path(path: Path, expected_name: str) -> Path:
    resolved = path.resolve()
    if resolved.name != expected_name:
        raise ValueError("capture source filename must be " + expected_name)
    normalized = resolved.as_posix().upper()
    if any(token in normalized for token in _FORBIDDEN_SOURCE_PATH_TOKENS):
        raise ValueError("Formal TEST/R3/blind source path rejected")
    return resolved


def load_and_capture_nonblind_records(
        runtime_cases_path: str | Path, units_path: str | Path,
        schedule_document: Mapping[str, Any],
        creation_provenance: Mapping[str, Any],
        ) -> tuple[LifecycleAwareM2BDecisionRecordV1, ...]:
    """Load the two allowed artifacts, run M2B once, and synchronously capture.

    The function has no Formal TEST/R3 loader, no learned-model entry point, and
    no fallback path that supplies a decision deadline.  The deadline must be
    present in the already-frozen ``schedule_document``.
    """
    runtime_path = _validate_source_path(Path(runtime_cases_path),
                                         "M2B_RUNTIME_CASES.json")
    unit_path = _validate_source_path(Path(units_path),
                                      "M2B_REAL_DEVELOPMENT_UNITS.json")
    if runtime_path.parent != unit_path.parent:
        raise ValueError("runtime cases and units must share one source package")
    if not isinstance(creation_provenance, Mapping):
        raise TypeError("creation_provenance must be an object")
    validate_finite_json(creation_provenance)
    schedule = _validate_schedule(schedule_document)
    schedule_sha256 = canonical_sha256(schedule)
    runtime_document = strict_json_loads(runtime_path.read_text(encoding="utf-8"))
    units_document = strict_json_loads(unit_path.read_text(encoding="utf-8"))
    if (runtime_document.get("schema_version") !=
            "driveclarify.m2b_formal_runtime_cases.v1" or
            runtime_document.get("scoring_data_embedded") is not False):
        raise ValueError("runtime source schema/provenance mismatch")
    cases = runtime_document.get("cases")
    if (not isinstance(cases, list) or len(cases) != 288 or
            runtime_document.get("case_count") != len(cases)):
        raise ValueError("runtime source must contain the frozen 288 cases")
    if (units_document.get("schema_version") !=
            "driveclarify.m2b_real_development_units.v1"):
        raise ValueError("unit source schema mismatch")
    units = units_document.get("units")
    if (not isinstance(units, list) or len(units) != 36 or
            units_document.get("unit_count") != len(units)):
        raise ValueError("unit source must contain the frozen 36 units")
    unit_by_key: dict[str, Mapping[str, Any]] = {}
    for unit in units:
        identity = unit.get("control_plane_identity")
        if not isinstance(identity, Mapping):
            raise ValueError("unit control-plane identity missing")
        key = identity.get("m2b_unit_key")
        _string(key, "m2b_unit_key")
        if key in unit_by_key:
            raise ValueError("duplicate M2B unit key")
        if identity.get("development_split") not in {"TRAIN", "DEV"}:
            raise ValueError("non-TRAIN/DEV unit rejected")
        unit_by_key[key] = unit
    def stable_key(case: Mapping[str, Any]) -> tuple[int, str, str, str]:
        unit = unit_by_key.get(case.get("m2b_unit_key"))
        if unit is None:
            raise ValueError("runtime case unit provenance unresolved")
        split = unit["control_plane_identity"]["development_split"]
        return (
            0 if split == "DEV" else 1,
            str(case.get("m2b_unit_key", "")),
            str(case.get("operating_profile_id", "")),
            str(case.get("decision_id", "")),
        )
    ordered_cases = sorted(cases, key=stable_key)
    if len({case.get("episode_id") for case in ordered_cases}) != len(ordered_cases):
        raise ValueError("runtime case identity is not unique")
    if schedule["schedule_kind"] == (
            "EXPLICIT_BY_SOURCE_CASE_DECISION_DEADLINE"):
        scheduled = set(schedule["decision_deadline_monotonic_by_source_case"])
        observed = {str(case["episode_id"]) for case in ordered_cases}
        if scheduled != observed:
            raise ValueError("per-case deadline schedule coverage mismatch")
    source_case_sha_before = canonical_sha256(ordered_cases)
    torch_present_before = "torch" in sys.modules
    # Local import keeps the capture module itself independent of the producer.
    from driveclarify_m2b_formal_offline.integration import run_policy_cases
    observed_decisions: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []

    def capture_observer(case: Mapping[str, Any], output: Mapping[str, Any]) -> None:
        observed_decisions.append((case, output))

    outputs = run_policy_cases(
        ordered_cases, decision_observer=capture_observer)
    if not torch_present_before and "torch" in sys.modules:
        raise RuntimeError("CPU-only rule policy unexpectedly imported torch")
    if canonical_sha256(ordered_cases) != source_case_sha_before:
        raise RuntimeError("M2B policy mutated runtime cases")
    if len(outputs) != len(ordered_cases):
        raise RuntimeError("M2B policy output count mismatch")
    if len(observed_decisions) != len(ordered_cases):
        raise RuntimeError("M2B per-decision observer count mismatch")
    for index, ((observed_case, observed_output), case, output) in enumerate(
            zip(observed_decisions, ordered_cases, outputs)):
        if canonical_sha256(observed_case) != canonical_sha256(case):
            raise RuntimeError(
                "M2B observer case mismatch at index " + str(index))
        if canonical_sha256(observed_output) != canonical_sha256(output):
            raise RuntimeError(
                "M2B observer output mismatch at index " + str(index))
    source_artifact_sha256 = _file_sha256(runtime_path)
    source_units_sha256 = _file_sha256(unit_path)
    source_artifact_id = runtime_path.parent.name + ":M2B_RUNTIME_CASES"
    records = tuple(
        _capture_one(
            case=case,
            unit=unit_by_key[case["m2b_unit_key"]],
            policy_output=output,
            schedule=schedule,
            schedule_sha256=schedule_sha256,
            source_artifact_id=source_artifact_id,
            source_artifact_sha256=source_artifact_sha256,
            source_units_sha256=source_units_sha256,
            supplied_creation_provenance=creation_provenance,
        )
        for case, output in observed_decisions
    )
    if len({item.record_id for item in records}) != len(records):
        raise RuntimeError("capture record identity collision")
    if Counter(item.split for item in records) != Counter({"DEV": 88, "TRAIN": 200}):
        raise RuntimeError("captured split distribution mismatch")
    return records


def admit_lifecycle_aware_record(
        record: LifecycleAwareM2BDecisionRecordV1 | Mapping[str, Any]
        ) -> dict[str, Any]:
    """Fail closed on any non-V1, open, or self-hash-invalid capture."""
    try:
        normalized = (record if isinstance(record, LifecycleAwareM2BDecisionRecordV1)
                      else LifecycleAwareM2BDecisionRecordV1.from_dict(record))
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "schema_version": LIFECYCLE_ADMISSION_SCHEMA,
            "admitted": False,
            "admission_result": "STRUCTURED_ADMISSION_REJECTION",
            "reasons": ["LIFECYCLE_AWARE_RECORD_INVALID:" + str(exc)],
            "record_id": record.get("record_id") if isinstance(record, Mapping) else None,
            "record_sha256": None,
            "action_rewrite_count": 0,
            "evidence_fabrication_count": 0,
        }
    return {
        "schema_version": LIFECYCLE_ADMISSION_SCHEMA,
        "admitted": True,
        "admission_result": "ADMITTED_NONBLIND_" + normalized.split,
        "reasons": [],
        "record_id": normalized.record_id,
        "record_sha256": normalized.record_sha256,
        "selected_action_raw": normalized.selected_action_raw,
        "selected_action_canonical": normalized.selected_action_canonical,
        "action_mapping_kind": normalized.action_mapping_kind,
        "action_rewrite_count": 0,
        "evidence_fabrication_count": 0,
    }


def lifecycle_aware_dataset_document(
        records: Sequence[LifecycleAwareM2BDecisionRecordV1],
        schedule_document: Mapping[str, Any],
        ) -> dict[str, Any]:
    """Build a closed, self-hashed canonical publication document."""
    schedule = _validate_schedule(schedule_document)
    normalized = tuple(records)
    if not normalized or any(
            not isinstance(item, LifecycleAwareM2BDecisionRecordV1)
            for item in normalized):
        raise TypeError("records must be lifecycle-aware record objects")
    record_values = [item.to_dict() for item in normalized]
    source_artifacts = sorted({
        (item.source_artifact_id, item.source_artifact_sha256)
        for item in normalized
    })
    if len(source_artifacts) != 1:
        raise ValueError("capture publication must bind one source artifact")
    base = {
        "schema_version": LIFECYCLE_DATASET_SCHEMA,
        "producer_version": CAPTURE_PRODUCER_VERSION,
        "source_artifact_id": source_artifacts[0][0],
        "source_artifact_sha256": source_artifacts[0][1],
        "capture_schedule_id": schedule["schedule_id"],
        "capture_schedule_sha256": canonical_sha256(schedule),
        "record_count": len(normalized),
        "split_distribution": dict(sorted(Counter(
            item.split for item in normalized).items())),
        "raw_action_distribution": dict(sorted(Counter(
            item.selected_action_raw for item in normalized).items())),
        "canonical_action_distribution": dict(sorted(Counter(
            item.selected_action_canonical for item in normalized).items())),
        "records_sha256": canonical_sha256(record_values),
        "records": record_values,
        "legacy_fallback_alias_verdict": LEGACY_FALLBACK_ALIAS_VERDICT,
        "action_rewrite_count": 0,
        "evidence_fabrication_count": 0,
        "r3_access_count": 0,
        "formal_m1_test_access_count": 0,
    }
    return {**base, "dataset_sha256": canonical_sha256(base)}


def build_recorded_decision_episode(
        record: LifecycleAwareM2BDecisionRecordV1 | Mapping[str, Any],
        creation_utc: str,
        ) -> ReplayEpisode:
    """Adapt one admitted capture into a recorded-decision-only replay episode."""
    normalized = (record if isinstance(record, LifecycleAwareM2BDecisionRecordV1)
                  else LifecycleAwareM2BDecisionRecordV1.from_dict(record))
    _string(creation_utc, "creation_utc")
    admission = admit_lifecycle_aware_record(normalized)
    if not admission["admitted"]:
        raise ValueError("lifecycle-aware record is not admitted")
    selected_action = SelectedAction(normalized.selected_action_canonical)
    evidence = (normalized.holding_evidence_grade
                if selected_action is SelectedAction.WAIT
                else normalized.decision_evidence_grade)
    source_reasons = normalized.creation_provenance.get("source_reason_codes", ())
    if not isinstance(source_reasons, (list, tuple)):
        raise ValueError("captured source reason codes invalid")
    decision = M2BDecisionEnvelope(
        decision_id=normalized.record_id,
        source_observation_id=normalized.source_observation_id,
        source_frame_id=normalized.source_frame_id,
        candidate_set_id=normalized.candidate_set_id,
        selected_action=selected_action,
        selected_candidate_id=normalized.selected_candidate_id,
        decision_monotonic_time=normalized.decision_monotonic_time,
        decision_deadline_monotonic=normalized.decision_deadline_monotonic,
        answer_deadline_monotonic=normalized.answer_deadline_monotonic,
        query_episode_id=normalized.query_episode_id,
        query_identity=normalized.query_identity,
        query_budget=normalized.query_budget,
        candidate_freshness=normalized.candidate_freshness,
        evidence_grade=evidence,
        holding_lease_payload=normalized.holding_lease,
        reason_codes=tuple(str(item) for item in source_reasons),
        source_policy_version=normalized.source_policy_version,
        source_record_sha256=normalized.record_sha256,
    )
    event_payload = {
        "candidate_set_id": normalized.candidate_set_id,
        "candidate_freshness": normalized.candidate_freshness,
        "act_evidence_grade": normalized.decision_evidence_grade,
        "holding_evidence_grade": normalized.holding_evidence_grade,
        "lease": to_json_compatible(normalized.holding_lease),
        "answer_present": False,
        "baseline_authority_eligible": False,
        "physical_mode_ready": False,
        "reason_code_authorizes_control": False,
        "model_forward_requested": False,
        "low_level_control_requested": False,
        "decision_deadline_monotonic": normalized.decision_deadline_monotonic,
        "answer_deadline_monotonic": normalized.answer_deadline_monotonic,
    }
    episode_id = "M3-RECORDED-DECISION-" + normalized.record_id
    replay_record = ReplayRecord.create(
        record_id=normalized.record_id,
        episode_id=episode_id,
        sequence_index=0,
        record_type="M2B_DECISION",
        source_component="M2B_NONBLIND_LIFECYCLE_AWARE_CAPTURE",
        source_observation_id=normalized.source_observation_id,
        source_frame_id=normalized.source_frame_id,
        candidate_set_id=normalized.candidate_set_id,
        query_episode_id=normalized.query_episode_id,
        source_simulation_time=None,
        source_monotonic_time=normalized.decision_monotonic_time,
        replay_monotonic_time=normalized.decision_monotonic_time,
        calendar_utc=creation_utc,
        concurrent_group_id=None,
        payload={"decision": decision.to_dict(), "event_payload": event_payload},
        provenance_grade=RECORDED_PROVENANCE_GRADE,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
    )
    return ReplayEpisode.create(
        episode_id=episode_id,
        source_tier=SourceTier.TIER_C_RECORDED_NONBLIND_TRAIN_DEV,
        source_artifact_id=normalized.source_artifact_id,
        source_artifact_sha256=normalized.source_artifact_sha256,
        source_provenance={
            "source_identity": normalized.record_id,
            "origin": "NONBLIND_TRAIN_DEV_LIFECYCLE_AWARE_CAPTURE",
            "verified_source_artifact_sha256": normalized.source_artifact_sha256,
            "captured_record_sha256": normalized.record_sha256,
            "split": normalized.split,
            "non_blind": True,
            "r3_excluded": True,
            "formal_m1_test_excluded": True,
        },
        initial_state=normalized.initial_m3_state,
        time_mapping_mode=TimeMappingMode.EXPLICIT_REPLAY_MONOTONIC_TIMELINE,
        episode_start_source_time=normalized.decision_monotonic_time,
        episode_start_replay_monotonic_time=normalized.decision_monotonic_time,
        records=(replay_record,),
        creation_utc=creation_utc,
    )
