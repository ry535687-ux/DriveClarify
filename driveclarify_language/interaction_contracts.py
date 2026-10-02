"""Immutable contracts for the offline DriveClarify language interaction prototype.

Runtime-observable values live in this module.  Evaluation labels intentionally live in
``offline_language_evaluation`` so they cannot be threaded into parser, grounding, adapter, or
question-planning signatures.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


SCHEMA_VERSION = "driveclarify.language_interaction.v0"
EVIDENCE_DESIGNATION = (
    "LANGUAGE_LAYER_PROTOTYPE",
    "OFFLINE_SYMBOLIC_VALIDATION",
    "NOT_PRIMARY_EVIDENCE",
    "NOT_PAPER_RESULT",
)
FORBIDDEN_RUNTIME_FIELDS = frozenset(
    {
        "true_intent",
        "gold_intent",
        "gold_candidate_id",
        "gold_task_anchor",
        "gold_answer",
        "necessary_query_label",
        "expected_pair_label",
        "oracle_selected_candidate",
    }
)


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class AmbiguityType(StrEnum):
    REFERENTIAL = "REFERENTIAL"
    LANDMARK = "LANDMARK"
    ORDER = "ORDER"
    UNDERSPECIFIED_CONSTRAINT = "UNDERSPECIFIED_CONSTRAINT"
    UNAMBIGUOUS = "UNAMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class EpisodeState(StrEnum):
    NEW = "NEW"
    PARSED = "PARSED"
    CANDIDATES_READY = "CANDIDATES_READY"
    QUERY_PROPOSED = "QUERY_PROPOSED"
    QUERY_ISSUED = "QUERY_ISSUED"
    ANSWER_RECEIVED = "ANSWER_RECEIVED"
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    EXPIRED = "EXPIRED"
    UNSUPPORTED = "UNSUPPORTED"


class CandidateStatus(StrEnum):
    VALID = "VALID"
    DUPLICATE = "DUPLICATE"
    UNGROUNDED = "UNGROUNDED"
    CONTRADICTORY = "CONTRADICTORY"
    UNSUPPORTED = "UNSUPPORTED"
    INCOMPLETE = "INCOMPLETE"


class GroundingStatus(StrEnum):
    GROUNDED_SYMBOLIC = "GROUNDED_SYMBOLIC"
    GROUNDED_ORACLE_FIXTURE = "GROUNDED_ORACLE_FIXTURE"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    CONFLICTING = "CONFLICTING"
    UNSUPPORTED = "UNSUPPORTED"


class BindingStatus(StrEnum):
    BOUND = "BOUND"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    CONFLICTING = "CONFLICTING"
    UNSUPPORTED = "UNSUPPORTED"


class LongitudinalTaskTargetType(StrEnum):
    STOP = "STOP"
    CONTINUE = "CONTINUE"


class QuestionStatus(StrEnum):
    QUESTION_PROPOSAL = "QUESTION_PROPOSAL"
    QUESTION_NOT_REALIZABLE = "QUESTION_NOT_REALIZABLE"
    QUESTION_NOT_REQUIRED = "QUESTION_NOT_REQUIRED"


class AnswerStatus(StrEnum):
    RESOLVED = "RESOLVED"
    STILL_AMBIGUOUS = "STILL_AMBIGUOUS"
    CONTRADICTORY = "CONTRADICTORY"
    NO_ANSWER = "NO_ANSWER"
    EXPIRED = "EXPIRED"
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"


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


@dataclass(frozen=True)
class SymbolicSceneEntity(SerializableContract):
    entity_type: str
    entity_id: str | None
    display_name: str | None
    observable_attributes: tuple[tuple[str, Any], ...]
    ordinal: int | None
    supports_slots: tuple[str, ...]
    symbolic_target_type: str
    binding_source: str
    frame_or_semantic_domain: str
    grounding_status: GroundingStatus
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class RuntimeEpisodeInput(SerializableContract):
    episode_id: str
    instruction_id: str
    raw_instruction: str
    source_observation_id: str | None
    symbolic_scene_entities: tuple[SymbolicSceneEntity, ...]
    allowed_task_vocabulary: tuple[str, ...]
    observed_at_monotonic: float
    answer_deadline_monotonic: float | None
    episode_metadata: tuple[tuple[str, Any], ...]
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class CanonicalIntent(SerializableContract):
    intent_type: str
    maneuver: str | None
    spatial_relation: str | None
    temporal_relation: str | None
    reference_slot: str | None
    landmark_slot: str | None
    order_slot: str | None
    constraint_slots: tuple[tuple[str, str | None], ...]
    canonical_tokens: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class StructuredAmbiguityParse(SerializableContract):
    source_instruction_id: str
    normalized_instruction: str
    ambiguity_type: AmbiguityType
    unresolved_slots: tuple[str, ...]
    base_intent: CanonicalIntent
    explicit_entity_ids: tuple[str, ...]
    parser_status: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class LongitudinalTaskTarget(SerializableContract):
    """Language-side longitudinal requirement; never a candidate-speed observation."""

    target_type: LongitudinalTaskTargetType
    source_maneuver: str
    source_instruction_id: str
    binding_source: str
    provenance: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class CandidateSpecificTaskBinding(SerializableContract):
    source_candidate_id: str
    task_family: str
    symbolic_target_type: str
    symbolic_target_id: str | None
    required_slots: tuple[str, ...]
    binding_status: BindingStatus
    binding_source: str
    frame_or_semantic_domain: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    longitudinal_task_target: LongitudinalTaskTarget | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class GroundingEvidence(SerializableContract):
    grounding_status: GroundingStatus
    entity_type: str
    entity_id: str | None
    display_name: str | None
    observable_attributes: tuple[tuple[str, Any], ...]
    source: str
    confidence_status: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class PromptAdapterInput(SerializableContract):
    candidate_id: str
    backbone_instruction_text: str
    optional_hlc_token: str | None
    adapter_version: str
    source_instruction_id: str
    candidate_semantic_sha256: str
    task_binding_sha256: str
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class CandidateInterpretation(SerializableContract):
    candidate_id: str
    source_instruction_id: str
    canonical_intent: CanonicalIntent
    candidate_specific_task_binding: CandidateSpecificTaskBinding
    grounding_evidence: GroundingEvidence
    human_readable_description: str | None
    prompt_adapter_input: PromptAdapterInput | None
    candidate_status: CandidateStatus
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    canonical_semantic_sha256: str
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class AmbiguityEpisode(SerializableContract):
    episode_id: str
    instruction_id: str
    raw_instruction: str
    source_observation_id: str | None
    ambiguity_type: AmbiguityType
    unresolved_slots: tuple[str, ...]
    candidate_set: tuple[CandidateInterpretation, ...]
    active_query_id: str | None
    answer_deadline_monotonic: float | None
    episode_state: EpisodeState
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class ClarificationQuestionProposal(SerializableContract):
    query_id: str | None
    target_slot: str | None
    question_type: str | None
    candidate_partition: tuple[tuple[str, tuple[str, ...]], ...]
    option_descriptions: tuple[str, ...]
    question_text: str | None
    source_candidate_ids: tuple[str, ...]
    deadline_status: str
    proposal_status: QuestionStatus
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class AnswerResolution(SerializableContract):
    status: AnswerStatus
    query_id: str | None
    selected_candidate_id: str | None
    normalized_answer: str | None
    received_at_monotonic: float
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class ResolvedInstruction(SerializableContract):
    source_instruction_id: str
    resolved_candidate_id: str
    explicit_instruction_text: str
    canonical_intent: CanonicalIntent
    candidate_specific_task_binding: CandidateSpecificTaskBinding
    source_observation_id: str | None
    requires_latest_observation_replan: bool
    cached_pre_answer_plan_reusable: bool
    output_type: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


def assert_no_forbidden_runtime_keys(value: Any) -> None:
    """Recursively reject exact forbidden runtime field names."""

    if isinstance(value, Mapping):
        overlap = FORBIDDEN_RUNTIME_FIELDS.intersection(str(key) for key in value)
        if overlap:
            raise ValueError(f"FORBIDDEN_RUNTIME_FIELD:{','.join(sorted(overlap))}")
        for item in value.values():
            assert_no_forbidden_runtime_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_no_forbidden_runtime_keys(item)
