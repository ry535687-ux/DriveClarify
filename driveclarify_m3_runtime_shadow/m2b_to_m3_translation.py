"""Explicit producer-M2B to lifecycle-M3 translation for shadow traces only.

This module is the only place in the runtime-shadow package where the M2B
producer vocabulary is translated into the M3 lifecycle vocabulary.  In
particular, ``FALLBACK_RECOMMENDED`` is preserved as the producer action and is
translated by a versioned semantic rule to lifecycle ``FALLBACK``.  It is not
treated as an enum alias.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from driveclarify_m3_minimal_core import (
    ControlAuthority,
    EvidenceGrade,
    LifecycleState,
    MinimalM3State,
    check_invariants,
)
from driveclarify_m3_offline_replay import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    M2BDecisionEnvelope,
    ReplayEpisode,
    ReplayRecord,
    SelectedAction,
    SourceTier,
    TimeMappingMode,
)
from driveclarify_m3_offline_replay.serialization import (
    canonical_sha256,
    is_finite_real,
    require_sha256,
    to_json_compatible,
)
from driveclarify_m3_shadow_bridge import (
    M3ShadowBridge,
    ShadowBridgeRejection,
    ShadowInputEnvelope,
    ShadowTrace,
)

_DATACLASS_SLOT_KWARGS = {"slots": True} if sys.version_info >= (3, 10) else {}

from .m2b_binding import ShadowM2BDecisionResult


M2B_TO_M3_ACTION_TRANSLATION_SCHEMA = (
    "driveclarify.m2b-to-m3-shadow-action-translation.v1"
)
M2B_TO_M3_SHADOW_TRANSLATION_SCHEMA = (
    "driveclarify.m2b-to-m3-shadow-translation.v1"
)
M2B_TO_M3_SHADOW_TRANSLATOR_VERSION = (
    "EXPLICIT_M2B_TO_M3_SHADOW_TRANSLATOR_V1"
)
M2B_PRODUCER_ACTION_NAMESPACE = (
    "driveclarify.query_value_decision.v0.Decision"
)
M3_LIFECYCLE_ACTION_NAMESPACE = (
    "driveclarify.m3.offline-replay.v1.SelectedAction"
)
EXISTING_M3_SHADOW_BRIDGE_COMPONENT = (
    "driveclarify_m3_shadow_bridge.M3ShadowBridge"
)
DETERMINISTIC_SHADOW_CREATION_MARKER = (
    "SHADOW_TRANSLATION_LOGICAL_TIME_ONLY_NO_WALL_CLOCK"
)

PASS_EXPLICIT_M2B_TO_M3_SHADOW_TRANSLATION_BOUND_TO_EXISTING_BRIDGE = (
    "PASS_EXPLICIT_M2B_TO_M3_SHADOW_TRANSLATION_BOUND_TO_EXISTING_BRIDGE"
)
BLOCKED_EXISTING_M3_SHADOW_BRIDGE_REJECTED_TRANSLATED_INPUT = (
    "BLOCKED_EXISTING_M3_SHADOW_BRIDGE_REJECTED_TRANSLATED_INPUT"
)
BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID = (
    "BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID"
)
BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_ACTION_UNSUPPORTED = (
    "BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_ACTION_UNSUPPORTED"
)
BLOCKED_M2B_TO_M3_SHADOW_CONTROL_BOUNDARY_VIOLATION = (
    "BLOCKED_M2B_TO_M3_SHADOW_CONTROL_BOUNDARY_VIOLATION"
)

_ACTION_RULES: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "ACT": (
            "ACT",
            "EXPLICIT_CROSS_NAMESPACE_ACT_LIFECYCLE_TRANSLATION",
        ),
        "ASK": (
            "ASK",
            "EXPLICIT_CROSS_NAMESPACE_ASK_LIFECYCLE_TRANSLATION",
        ),
        "WAIT": (
            "WAIT",
            "EXPLICIT_CROSS_NAMESPACE_WAIT_WITHOUT_PHYSICAL_HOLDING_TRANSLATION",
        ),
        "FALLBACK_RECOMMENDED": (
            "FALLBACK",
            "FALLBACK_RECOMMENDATION_TO_FAIL_CLOSED_M3_LIFECYCLE_FALLBACK",
        ),
    }
)
EXPLICIT_M2B_TO_M3_ACTION_MAPPING = _ACTION_RULES


def _nonempty(value: Any, label: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(label + " must be a nonempty string")


def _optional_nonempty(value: Any, label: str) -> None:
    if value is not None:
        _nonempty(value, label)


@dataclass(frozen=True, **_DATACLASS_SLOT_KWARGS)
class ExplicitM2BToM3ActionTranslation:
    """Closed evidence for one cross-module action translation rule."""

    schema_version: str
    translator_version: str
    producer_namespace: str
    lifecycle_namespace: str
    producer_action: str
    lifecycle_action: str
    semantic_relation: str
    exact_enum_alias_claimed: bool
    producer_action_preserved: bool
    m2b_policy_modified: bool
    action_translation_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != M2B_TO_M3_ACTION_TRANSLATION_SCHEMA:
            raise ValueError("action translation schema mismatch")
        if self.translator_version != M2B_TO_M3_SHADOW_TRANSLATOR_VERSION:
            raise ValueError("action translator version mismatch")
        if self.producer_namespace != M2B_PRODUCER_ACTION_NAMESPACE:
            raise ValueError("producer action namespace mismatch")
        if self.lifecycle_namespace != M3_LIFECYCLE_ACTION_NAMESPACE:
            raise ValueError("lifecycle action namespace mismatch")
        expected = _ACTION_RULES.get(self.producer_action)
        if expected is None:
            raise ValueError(BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_ACTION_UNSUPPORTED)
        if (self.lifecycle_action, self.semantic_relation) != expected:
            raise ValueError("action translation rule mismatch")
        if self.exact_enum_alias_claimed is not False:
            raise ValueError("cross-module exact enum alias claims are forbidden")
        if self.producer_action_preserved is not True:
            raise ValueError("producer action must remain preserved")
        if self.m2b_policy_modified is not False:
            raise ValueError("M2B policy modification is forbidden")
        require_sha256(
            self.action_translation_sha256,
            "action_translation_sha256",
        )
        if self.action_translation_sha256 != canonical_sha256(
            self._hash_projection()
        ):
            raise ValueError("action translation hash mismatch")

    def _hash_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "translator_version": self.translator_version,
            "producer_namespace": self.producer_namespace,
            "lifecycle_namespace": self.lifecycle_namespace,
            "producer_action": self.producer_action,
            "lifecycle_action": self.lifecycle_action,
            "semantic_relation": self.semantic_relation,
            "exact_enum_alias_claimed": self.exact_enum_alias_claimed,
            "producer_action_preserved": self.producer_action_preserved,
            "m2b_policy_modified": self.m2b_policy_modified,
        }

    @classmethod
    def create(cls, producer_action: str) -> "ExplicitM2BToM3ActionTranslation":
        try:
            lifecycle_action, semantic_relation = _ACTION_RULES[producer_action]
        except KeyError as exc:
            raise ValueError(
                BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_ACTION_UNSUPPORTED
            ) from exc
        values = {
            "schema_version": M2B_TO_M3_ACTION_TRANSLATION_SCHEMA,
            "translator_version": M2B_TO_M3_SHADOW_TRANSLATOR_VERSION,
            "producer_namespace": M2B_PRODUCER_ACTION_NAMESPACE,
            "lifecycle_namespace": M3_LIFECYCLE_ACTION_NAMESPACE,
            "producer_action": producer_action,
            "lifecycle_action": lifecycle_action,
            "semantic_relation": semantic_relation,
            "exact_enum_alias_claimed": False,
            "producer_action_preserved": True,
            "m2b_policy_modified": False,
        }
        return cls(
            **values,
            action_translation_sha256=canonical_sha256(values),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._hash_projection(),
            "action_translation_sha256": self.action_translation_sha256,
        }


@dataclass(frozen=True, **_DATACLASS_SLOT_KWARGS)
class M2BToM3ShadowTranslation:
    """Hash-bound M3 shadow input plus its explicit translation evidence."""

    schema_version: str
    translator_version: str
    translation_id: str
    action_translation: ExplicitM2BToM3ActionTranslation
    source_observation_id: str
    source_frame_id: str | None
    source_simulation_time: int | float
    producer_decision_id: str
    candidate_ids: tuple[str, str]
    candidate_set_id: str
    candidate_identity_sha256: str
    consequence_input_sha256: str
    counterfactual_matrix_sha256: str
    decision_context_sha256: str
    producer_recommendation_sha256: str
    selected_candidate_id: str | None
    producer_query_id: str | None
    m3_query_episode_id: str | None
    query_budget: int
    decision_monotonic_time: int | float
    answer_deadline_monotonic: int | float | None
    candidate_freshness: str
    decision_evidence_grade: str
    holding_evidence_grade: str
    holding_lease: None
    reason_codes: tuple[str, ...]
    shadow_only: bool
    used_for_control: bool
    physical_holding_implemented: bool
    control_authority_requested: bool
    low_level_control_requested: bool
    m2b_policy_modified: bool
    m3_minimal_core_modified: bool
    initial_m3_state_sha256: str
    shadow_input: ShadowInputEnvelope
    translation_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != M2B_TO_M3_SHADOW_TRANSLATION_SCHEMA:
            raise ValueError("shadow translation schema mismatch")
        if self.translator_version != M2B_TO_M3_SHADOW_TRANSLATOR_VERSION:
            raise ValueError("shadow translator version mismatch")
        if not isinstance(
            self.action_translation,
            ExplicitM2BToM3ActionTranslation,
        ):
            raise TypeError("action_translation has invalid type")
        for name in (
            "source_observation_id",
            "producer_decision_id",
            "candidate_set_id",
            "decision_evidence_grade",
            "holding_evidence_grade",
        ):
            _nonempty(getattr(self, name), name)
        _optional_nonempty(self.source_frame_id, "source_frame_id")
        _optional_nonempty(self.selected_candidate_id, "selected_candidate_id")
        _optional_nonempty(self.producer_query_id, "producer_query_id")
        _optional_nonempty(self.m3_query_episode_id, "m3_query_episode_id")
        if not is_finite_real(self.source_simulation_time):
            raise ValueError("source_simulation_time must be finite")
        if not is_finite_real(self.decision_monotonic_time):
            raise ValueError("decision_monotonic_time must be finite")
        if (
            self.answer_deadline_monotonic is not None
            and not is_finite_real(self.answer_deadline_monotonic)
        ):
            raise ValueError("answer_deadline_monotonic must be finite or null")
        if type(self.query_budget) is not int or self.query_budget < 0:
            raise ValueError("query_budget must be a nonnegative integer")
        if (
            not isinstance(self.candidate_ids, tuple)
            or len(self.candidate_ids) != 2
            or len(set(self.candidate_ids)) != 2
            or any(type(item) is not str or not item for item in self.candidate_ids)
        ):
            raise ValueError("candidate_ids must contain two unique strings")
        if (
            self.selected_candidate_id is not None
            and self.selected_candidate_id not in self.candidate_ids
        ):
            raise ValueError("selected candidate is outside candidate set")
        if (
            self.action_translation.producer_action != "ACT"
            and self.selected_candidate_id is not None
        ):
            raise ValueError("non-ACT translation cannot select a candidate")
        if self.action_translation.producer_action == "ASK" and (
            self.producer_query_id is None
            or self.m3_query_episode_id is None
            or self.answer_deadline_monotonic is None
        ):
            raise ValueError("ASK translation requires source query identity and deadline")
        if (self.producer_query_id is None) != (self.m3_query_episode_id is None):
            raise ValueError("query identities must be jointly present")
        if self.candidate_freshness not in {"FRESH", "STALE", "UNKNOWN"}:
            raise ValueError("invalid candidate freshness")
        if self.decision_evidence_grade == (
            EvidenceGrade.VERIFIED_FROM_CONTROLLED_PROBE.value
        ):
            raise ValueError("translation cannot promote evidence to controlled-probe verified")
        if self.holding_evidence_grade != EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value:
            raise ValueError("holding evidence must remain unavailable")
        if self.holding_lease is not None:
            raise ValueError("physical holding lease fabrication is forbidden")
        for name, expected in (
            ("shadow_only", True),
            ("used_for_control", False),
            ("physical_holding_implemented", False),
            ("control_authority_requested", False),
            ("low_level_control_requested", False),
            ("m2b_policy_modified", False),
            ("m3_minimal_core_modified", False),
        ):
            if getattr(self, name) is not expected:
                raise ValueError(name + " boundary mismatch")
        for name in (
            "candidate_identity_sha256",
            "consequence_input_sha256",
            "counterfactual_matrix_sha256",
            "decision_context_sha256",
            "producer_recommendation_sha256",
            "initial_m3_state_sha256",
            "translation_id",
            "translation_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if not isinstance(self.shadow_input, ShadowInputEnvelope):
            raise TypeError("shadow_input must be ShadowInputEnvelope")
        if self.initial_m3_state_sha256 != self.shadow_input.episode.initial_state_sha256:
            raise ValueError("initial M3 state identity mismatch")
        expected_id = canonical_sha256(
            [
                M2B_TO_M3_SHADOW_TRANSLATION_SCHEMA,
                self.action_translation.action_translation_sha256,
                self.shadow_input.envelope_sha256,
            ]
        )
        if self.translation_id != expected_id:
            raise ValueError("translation identity mismatch")
        if self.translation_sha256 != canonical_sha256(self._hash_projection()):
            raise ValueError("translation hash mismatch")

    def _hash_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "translator_version": self.translator_version,
            "translation_id": self.translation_id,
            "action_translation": self.action_translation.to_dict(),
            "source_observation_id": self.source_observation_id,
            "source_frame_id": self.source_frame_id,
            "source_simulation_time": self.source_simulation_time,
            "producer_decision_id": self.producer_decision_id,
            "candidate_ids": list(self.candidate_ids),
            "candidate_set_id": self.candidate_set_id,
            "candidate_identity_sha256": self.candidate_identity_sha256,
            "consequence_input_sha256": self.consequence_input_sha256,
            "counterfactual_matrix_sha256": self.counterfactual_matrix_sha256,
            "decision_context_sha256": self.decision_context_sha256,
            "producer_recommendation_sha256": self.producer_recommendation_sha256,
            "selected_candidate_id": self.selected_candidate_id,
            "producer_query_id": self.producer_query_id,
            "m3_query_episode_id": self.m3_query_episode_id,
            "query_budget": self.query_budget,
            "decision_monotonic_time": self.decision_monotonic_time,
            "answer_deadline_monotonic": self.answer_deadline_monotonic,
            "candidate_freshness": self.candidate_freshness,
            "decision_evidence_grade": self.decision_evidence_grade,
            "holding_evidence_grade": self.holding_evidence_grade,
            "holding_lease": self.holding_lease,
            "reason_codes": list(self.reason_codes),
            "shadow_only": self.shadow_only,
            "used_for_control": self.used_for_control,
            "physical_holding_implemented": self.physical_holding_implemented,
            "control_authority_requested": self.control_authority_requested,
            "low_level_control_requested": self.low_level_control_requested,
            "m2b_policy_modified": self.m2b_policy_modified,
            "m3_minimal_core_modified": self.m3_minimal_core_modified,
            "initial_m3_state_sha256": self.initial_m3_state_sha256,
            "shadow_input": self.shadow_input.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._hash_projection(),
            "translation_sha256": self.translation_sha256,
        }


@dataclass(frozen=True, **_DATACLASS_SLOT_KWARGS)
class M2BToM3ShadowExecutionResult:
    """Result of exactly one call to the existing M3 shadow bridge."""

    status: str
    translation: M2BToM3ShadowTranslation
    m3_shadow_result: ShadowTrace | ShadowBridgeRejection
    existing_m3_shadow_bridge_component: str
    bridge_invocation_count: int
    existing_m3_shadow_bridge_modified: bool
    m2b_policy_modified: bool
    m3_minimal_core_modified: bool
    physical_holding_implemented: bool
    control_write_count: int
    low_level_output_count: int
    model_forward_count: int
    planner_call_count: int
    pid_call_count: int

    def __post_init__(self) -> None:
        expected_status = (
            PASS_EXPLICIT_M2B_TO_M3_SHADOW_TRANSLATION_BOUND_TO_EXISTING_BRIDGE
            if isinstance(self.m3_shadow_result, ShadowTrace)
            else BLOCKED_EXISTING_M3_SHADOW_BRIDGE_REJECTED_TRANSLATED_INPUT
        )
        if self.status != expected_status:
            raise ValueError("shadow execution status mismatch")
        if not isinstance(self.translation, M2BToM3ShadowTranslation):
            raise TypeError("translation has invalid type")
        if not isinstance(self.m3_shadow_result, (ShadowTrace, ShadowBridgeRejection)):
            raise TypeError("m3_shadow_result has invalid type")
        if self.existing_m3_shadow_bridge_component != EXISTING_M3_SHADOW_BRIDGE_COMPONENT:
            raise ValueError("existing M3 shadow bridge identity mismatch")
        if self.bridge_invocation_count != 1:
            raise ValueError("existing M3 shadow bridge must be invoked exactly once")
        for name in (
            "existing_m3_shadow_bridge_modified",
            "m2b_policy_modified",
            "m3_minimal_core_modified",
            "physical_holding_implemented",
        ):
            if getattr(self, name) is not False:
                raise ValueError(name + " must remain false")
        for name in (
            "control_write_count",
            "low_level_output_count",
            "model_forward_count",
            "planner_call_count",
            "pid_call_count",
        ):
            if getattr(self, name) != 0:
                raise ValueError(name + " must remain zero")

    @property
    def m3_trace(self) -> ShadowTrace:
        if isinstance(self.m3_shadow_result, ShadowTrace):
            return self.m3_shadow_result
        raise TypeError("m3_shadow_result is not a ShadowTrace")

    def to_dict(self) -> dict[str, Any]:
        return to_json_compatible(self)


def _validate_source_result(result: ShadowM2BDecisionResult) -> None:
    if not isinstance(result, ShadowM2BDecisionResult):
        raise TypeError(BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID)
    if (
        result.shadow_only is not True
        or result.used_for_control is not False
        or result.candidate_rewrite_count != 0
        or result.candidate_fabrication_count != 0
        or result.model_forward_count != 0
        or result.model_load_count != 0
        or result.cuda_initialization_count != 0
        or result.control_write_count != 0
        or result.planner_invocation_count != 0
        or result.pid_invocation_count != 0
        or result.m3_invocation_count != 0
        or result.carla_invocation_count != 0
    ):
        raise RuntimeError(BLOCKED_M2B_TO_M3_SHADOW_CONTROL_BOUNDARY_VIOLATION)
    for value, label in (
        (result.source_observation_id, "source_observation_id"),
        (result.decision_id, "decision_id"),
        (result.candidate_set_id, "candidate_set_id"),
    ):
        _nonempty(value, label)
    for name in (
        "consequence_input_digest",
        "counterfactual_matrix_sha256",
        "decision_context_sha256",
        "producer_recommendation_sha256",
    ):
        require_sha256(getattr(result, name), name)
    if not is_finite_real(result.source_simulation_time):
        raise ValueError(BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID)
    if not is_finite_real(result.decision_monotonic_time):
        raise ValueError(BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID)
    if result.candidate_freshness not in {"FRESH", "STALE", "UNKNOWN"}:
        raise ValueError(BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID)


def _decision_evidence_grade(matrix_status: str) -> str:
    if matrix_status == "COMPLETE_KNOWN":
        return EvidenceGrade.SUPPORTED_BUT_INCOMPLETE.value
    return EvidenceGrade.UNRESOLVED_REQUIRES_ADDITIONAL_PROBE.value


def translate_m2b_decision_to_m3_shadow_input(
    result: ShadowM2BDecisionResult,
) -> M2BToM3ShadowTranslation:
    """Translate one unchanged producer result into one M3 shadow envelope."""

    _validate_source_result(result)
    action = ExplicitM2BToM3ActionTranslation.create(result.producer_action)
    if (
        result.selected_candidate_id is not None
        and result.selected_candidate_id not in result.candidate_ids
    ):
        raise ValueError(BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID)
    if result.producer_action != "ACT" and result.selected_candidate_id is not None:
        raise ValueError(BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID)
    if result.producer_action == "ASK" and (
        not result.producer_query_id
        or result.answer_deadline_monotonic is None
    ):
        raise ValueError(BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID)

    source_frame_id = (
        None if result.source_frame_id is None else str(result.source_frame_id)
    )
    candidate_identity = {
        "candidate_ids_in_producer_order": list(result.candidate_ids),
        "candidate_input_digests": list(result.candidate_input_digests),
        "candidate_output_digests": list(result.candidate_output_digests),
        "candidate_set_id": result.candidate_set_id,
    }
    candidate_identity_sha256 = canonical_sha256(candidate_identity)
    m3_query_episode_id = (
        "M3-SHADOW-QUERY-"
        + canonical_sha256(
            {
                "producer_query_id": result.producer_query_id,
                "source_observation_id": result.source_observation_id,
                "decision_context_sha256": result.decision_context_sha256,
            }
        )
        if result.producer_query_id is not None
        else None
    )
    decision_evidence_grade = _decision_evidence_grade(
        result.counterfactual_matrix_status
    )
    holding_evidence_grade = EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value
    reason_codes = tuple(result.reason_codes) + (
        "EXPLICIT_M2B_TO_M3_CROSS_MODULE_TRANSLATION",
        "EXACT_ENUM_ALIAS_NOT_CLAIMED",
        "M3_SHADOW_TRACE_ONLY",
        "NO_CONTROL_AUTHORITY_REQUESTED",
        "NO_PHYSICAL_HOLDING_IMPLEMENTED",
        "M2B_EVIDENCE_NOT_PROMOTED_TO_CONTROLLED_PROBE_VERIFIED",
    )
    if result.producer_action == "FALLBACK_RECOMMENDED":
        reason_codes += (
            "FALLBACK_RECOMMENDED_TRANSLATED_TO_M3_LIFECYCLE_FALLBACK",
        )

    initial_state = MinimalM3State(
        lifecycle_state=LifecycleState.DECISION_READY,
        query_episode_id=None,
        query_active=False,
        candidate_set_id=result.candidate_set_id,
        candidate_freshness=result.candidate_freshness,
        answer_deadline_monotonic=result.answer_deadline_monotonic,
        decision_deadline_monotonic=None,
        authority=ControlAuthority.NO_M3_CONTROL_AUTHORITY,
        safety_guard_active=False,
        baseline_authority_eligible=False,
        holding_lease=None,
        revalidation_required=False,
        replan_required=False,
        current_time_monotonic=result.decision_monotonic_time,
        model_forward_count=0,
        low_level_control_outputs=(),
    )
    if check_invariants(initial_state):
        raise ValueError(BLOCKED_M2B_TO_M3_SHADOW_TRANSLATION_INPUT_INVALID)
    initial_state_value = initial_state.to_dict()

    source_projection = {
        "translator_version": M2B_TO_M3_SHADOW_TRANSLATOR_VERSION,
        "action_translation": action.to_dict(),
        "source_observation_id": result.source_observation_id,
        "source_frame_id": source_frame_id,
        "source_simulation_time": result.source_simulation_time,
        "producer_decision_id": result.decision_id,
        "candidate_identity": candidate_identity,
        "consequence_input_sha256": result.consequence_input_digest,
        "counterfactual_matrix_sha256": result.counterfactual_matrix_sha256,
        "decision_context_sha256": result.decision_context_sha256,
        "producer_recommendation_sha256": result.producer_recommendation_sha256,
        "selected_candidate_id": result.selected_candidate_id,
        "producer_query_id": result.producer_query_id,
        "m3_query_episode_id": m3_query_episode_id,
        "query_budget": result.query_budget,
        "decision_monotonic_time": result.decision_monotonic_time,
        "answer_deadline_monotonic": result.answer_deadline_monotonic,
        "candidate_freshness": result.candidate_freshness,
        "decision_evidence_grade": decision_evidence_grade,
        "holding_evidence_grade": holding_evidence_grade,
        "reason_codes": list(reason_codes),
    }
    source_projection_sha256 = canonical_sha256(source_projection)
    decision = M2BDecisionEnvelope(
        decision_id=result.decision_id,
        source_observation_id=result.source_observation_id,
        source_frame_id=source_frame_id,
        candidate_set_id=result.candidate_set_id,
        selected_action=SelectedAction(action.lifecycle_action),
        selected_candidate_id=result.selected_candidate_id,
        decision_monotonic_time=result.decision_monotonic_time,
        decision_deadline_monotonic=None,
        answer_deadline_monotonic=result.answer_deadline_monotonic,
        query_episode_id=m3_query_episode_id,
        query_identity=result.producer_query_id,
        query_budget=result.query_budget,
        candidate_freshness=result.candidate_freshness,
        evidence_grade=(
            holding_evidence_grade
            if action.lifecycle_action == "WAIT"
            else decision_evidence_grade
        ),
        holding_lease_payload=None,
        reason_codes=reason_codes,
        source_policy_version=result.existing_m2b_version,
        source_record_sha256=source_projection_sha256,
    )
    event_payload = {
        "candidate_set_id": result.candidate_set_id,
        "candidate_freshness": result.candidate_freshness,
        "act_evidence_grade": decision_evidence_grade,
        "holding_evidence_grade": holding_evidence_grade,
        "lease": None,
        "answer_present": False,
        "baseline_authority_eligible": False,
        "physical_mode_ready": False,
        "reason_code_authorizes_control": False,
        "model_forward_requested": False,
        "low_level_control_requested": False,
        "answer_deadline_monotonic": result.answer_deadline_monotonic,
    }
    episode_id = "M3-SHADOW-M2B-" + source_projection_sha256
    replay_record = ReplayRecord.create(
        record_id="M3-SHADOW-DECISION-" + source_projection_sha256,
        episode_id=episode_id,
        sequence_index=0,
        record_type="M2B_DECISION",
        source_component=M2B_TO_M3_SHADOW_TRANSLATOR_VERSION,
        source_observation_id=result.source_observation_id,
        source_frame_id=source_frame_id,
        candidate_set_id=result.candidate_set_id,
        query_episode_id=m3_query_episode_id,
        source_simulation_time=result.source_simulation_time,
        source_monotonic_time=result.decision_monotonic_time,
        replay_monotonic_time=result.decision_monotonic_time,
        calendar_utc=None,
        concurrent_group_id=None,
        payload={
            "decision": decision.to_dict(),
            "event_payload": event_payload,
        },
        provenance_grade="NONBLIND_SYNTHETIC_CONTRACT_TEST_ONLY",
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
    )
    episode = ReplayEpisode.create(
        episode_id=episode_id,
        source_tier=SourceTier.TIER_B_NONBLIND_SYNTHETIC_DEV,
        source_artifact_id="M2B-SHADOW-RESULT-" + source_projection_sha256,
        source_artifact_sha256=source_projection_sha256,
        source_provenance={
            "source_identity": result.decision_id,
            "origin": "M2B_RUNTIME_SHADOW_EXPLICIT_TRANSLATION",
            "verified_source_artifact_sha256": source_projection_sha256,
            "source_m2b_projection_sha256": source_projection_sha256,
            "action_translation_sha256": action.action_translation_sha256,
            "producer_action_preserved": True,
            "exact_enum_alias_claimed": False,
            "shadow_only": True,
            "used_for_control": False,
            "physical_holding_implemented": False,
            "non_blind": True,
            "r3_excluded": True,
            "formal_m1_test_excluded": True,
        },
        initial_state=initial_state_value,
        time_mapping_mode=TimeMappingMode.EXPLICIT_REPLAY_MONOTONIC_TIMELINE,
        episode_start_source_time=result.source_simulation_time,
        episode_start_replay_monotonic_time=result.decision_monotonic_time,
        records=(replay_record,),
        creation_utc=DETERMINISTIC_SHADOW_CREATION_MARKER,
    )
    shadow_input = ShadowInputEnvelope.create(episode)
    translation_id = canonical_sha256(
        [
            M2B_TO_M3_SHADOW_TRANSLATION_SCHEMA,
            action.action_translation_sha256,
            shadow_input.envelope_sha256,
        ]
    )
    values = {
        "schema_version": M2B_TO_M3_SHADOW_TRANSLATION_SCHEMA,
        "translator_version": M2B_TO_M3_SHADOW_TRANSLATOR_VERSION,
        "translation_id": translation_id,
        "action_translation": action,
        "source_observation_id": result.source_observation_id,
        "source_frame_id": source_frame_id,
        "source_simulation_time": result.source_simulation_time,
        "producer_decision_id": result.decision_id,
        "candidate_ids": result.candidate_ids,
        "candidate_set_id": result.candidate_set_id,
        "candidate_identity_sha256": candidate_identity_sha256,
        "consequence_input_sha256": result.consequence_input_digest,
        "counterfactual_matrix_sha256": result.counterfactual_matrix_sha256,
        "decision_context_sha256": result.decision_context_sha256,
        "producer_recommendation_sha256": result.producer_recommendation_sha256,
        "selected_candidate_id": result.selected_candidate_id,
        "producer_query_id": result.producer_query_id,
        "m3_query_episode_id": m3_query_episode_id,
        "query_budget": result.query_budget,
        "decision_monotonic_time": result.decision_monotonic_time,
        "answer_deadline_monotonic": result.answer_deadline_monotonic,
        "candidate_freshness": result.candidate_freshness,
        "decision_evidence_grade": decision_evidence_grade,
        "holding_evidence_grade": holding_evidence_grade,
        "holding_lease": None,
        "reason_codes": reason_codes,
        "shadow_only": True,
        "used_for_control": False,
        "physical_holding_implemented": False,
        "control_authority_requested": False,
        "low_level_control_requested": False,
        "m2b_policy_modified": False,
        "m3_minimal_core_modified": False,
        "initial_m3_state_sha256": canonical_sha256(initial_state_value),
        "shadow_input": shadow_input,
    }
    projection = {
        **values,
        "action_translation": action.to_dict(),
        "candidate_ids": list(result.candidate_ids),
        "reason_codes": list(reason_codes),
        "shadow_input": shadow_input.to_dict(),
    }
    return M2BToM3ShadowTranslation(
        **values,
        translation_sha256=canonical_sha256(projection),
    )


def bind_m2b_decision_to_existing_m3_shadow_bridge(
    result: ShadowM2BDecisionResult,
    *,
    bridge: M3ShadowBridge | None = None,
) -> M2BToM3ShadowExecutionResult:
    """Translate, then invoke the existing bridge exactly once in shadow mode."""

    translation = translate_m2b_decision_to_m3_shadow_input(result)
    existing_bridge = M3ShadowBridge() if bridge is None else bridge
    if not isinstance(existing_bridge, M3ShadowBridge):
        raise TypeError("bridge must be the existing M3ShadowBridge")
    shadow_result = existing_bridge.run(translation.shadow_input)
    if isinstance(shadow_result, ShadowTrace):
        counters = (
            shadow_result.m3_control_write_delta,
            shadow_result.low_level_output_count,
            shadow_result.model_forward_delta,
            shadow_result.planner_call_delta,
            shadow_result.pid_call_delta,
        )
        if any(counters):
            raise RuntimeError(BLOCKED_M2B_TO_M3_SHADOW_CONTROL_BOUNDARY_VIOLATION)
    return M2BToM3ShadowExecutionResult(
        status=(
            PASS_EXPLICIT_M2B_TO_M3_SHADOW_TRANSLATION_BOUND_TO_EXISTING_BRIDGE
            if isinstance(shadow_result, ShadowTrace)
            else BLOCKED_EXISTING_M3_SHADOW_BRIDGE_REJECTED_TRANSLATED_INPUT
        ),
        translation=translation,
        m3_shadow_result=shadow_result,
        existing_m3_shadow_bridge_component=EXISTING_M3_SHADOW_BRIDGE_COMPONENT,
        bridge_invocation_count=1,
        existing_m3_shadow_bridge_modified=False,
        m2b_policy_modified=False,
        m3_minimal_core_modified=False,
        physical_holding_implemented=False,
        control_write_count=0,
        low_level_output_count=0,
        model_forward_count=0,
        planner_call_count=0,
        pid_call_count=0,
    )


# Short, discoverable aliases for callers that already use the runtime-shadow
# package as their entry point.  Both retain the explicit contract above.
translate_m2b_to_m3_shadow_input = translate_m2b_decision_to_m3_shadow_input
run_m2b_to_m3_shadow = bind_m2b_decision_to_existing_m3_shadow_bridge
