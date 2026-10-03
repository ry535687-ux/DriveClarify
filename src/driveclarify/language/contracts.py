"""language.contracts implementation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "driveclarify.language_grounding_v1.v1"
FEATURE_FLAG = "DRIVECLARIFY_LANGUAGE_GROUNDING_V1"
FORBIDDEN_POLICY_KEYS = frozenset(
    {
        "expected_decision",
        "gold_candidate_index",
        "gold_intended_object",
        "ground_truth",
        "scenario_id",
        "split",
        "test",
        "dev",
    }
)


class LanguageGroundingContractError(ValueError):
    pass


def canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {
            str(key): canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [canonical(item) for item in value]
    return value


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_label_firewall(value: Any) -> None:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        overlap = {str(key).casefold() for key in value}.intersection(FORBIDDEN_POLICY_KEYS)
        if overlap:
            raise LanguageGroundingContractError(
                "LABEL_FIREWALL_FORBIDDEN_KEYS:" + ",".join(sorted(overlap))
            )
        for item in value.values():
            assert_label_firewall(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            assert_label_firewall(item)


class AmbiguityKind(str, Enum):
    REFERENTIAL = "REFERENTIAL"
    LANDMARK = "LANDMARK"
    TEMPORAL = "TEMPORAL"
    SPATIAL_ORDER = "SPATIAL_ORDER"
    UNDERSPECIFIED_CONSTRAINT = "UNDERSPECIFIED_CONSTRAINT"
    UNAMBIGUOUS = "UNAMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class AmbiguityStatus(str, Enum):
    NO_REFERENT_FOUND = "NO_REFERENT_FOUND"
    SINGLE_REFERENT = "SINGLE_REFERENT"
    MULTIPLE_REFERENTS = "MULTIPLE_REFERENTS"
    LOW_CONFIDENCE_GROUNDING = "LOW_CONFIDENCE_GROUNDING"
    GROUNDING_STALE = "GROUNDING_STALE"
    TRACK_ID_UNCERTAIN = "TRACK_ID_UNCERTAIN"
    AMBIGUITY_DETECTED = "AMBIGUITY_DETECTED"
    CANDIDATE_COLLAPSED = "CANDIDATE_COLLAPSED"
    K_EXCEEDS_V1 = "K_EXCEEDS_V1"
    SIMLINGO_BINDING_COLLAPSE = "SIMLINGO_BINDING_COLLAPSE"
    SIMLINGO_PLAN_COLLAPSE = "SIMLINGO_PLAN_COLLAPSE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ParsedSlots:
    raw_instruction: str
    normalized_instruction: str
    maneuver: Optional[str]
    referent_phrase: Optional[str]
    landmark_phrase: Optional[str]
    spatial_relation: Optional[str]
    temporal_relation: Optional[str]
    ordering: Optional[str]
    constraint: Optional[str]
    ambiguity_kind: AmbiguityKind
    parser_status: str
    reason_codes: Tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return canonical(self)


@dataclass(frozen=True)
class VisualReferentCandidate:
    local_object_id: str
    phrase: str
    bbox_xyxy: Tuple[float, float, float, float]
    detector_confidence: float
    phrase_label: str
    frame_id: int
    observation_id: str
    image_sha256: str
    captured_monotonic: float
    relative_image_location: str
    bbox_area_fraction: float
    apparent_size_rank: Optional[int]
    plausibility_score: float
    plausible: bool
    freshness_seconds: float
    track_id: Optional[str] = None
    appearance_attributes: Tuple[Tuple[str, str], ...] = ()
    rejection_reasons: Tuple[str, ...] = ()
    depth_or_distance_status: str = "NOT_AVAILABLE_IMAGE_ONLY"
    privileged_state_read_count: int = 0
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if len(self.bbox_xyxy) != 4:
            raise LanguageGroundingContractError("BBOX_SHAPE_INVALID")
        if not all(math.isfinite(float(item)) for item in self.bbox_xyxy):
            raise LanguageGroundingContractError("BBOX_NONFINITE")
        if not 0.0 <= float(self.detector_confidence) <= 1.0:
            raise LanguageGroundingContractError("DETECTOR_CONFIDENCE_OUT_OF_RANGE")
        if self.privileged_state_read_count != 0:
            raise LanguageGroundingContractError("PRIVILEGED_RUNTIME_GROUNDING_FORBIDDEN")

    @property
    def grounding_identity(self) -> str:
        return self.track_id or self.local_object_id

    def to_dict(self) -> dict:
        return canonical(self)


@dataclass(frozen=True)
class GroundingResult:
    status: AmbiguityStatus
    query: str
    frame_id: int
    observation_id: str
    image_sha256: str
    image_width: int
    image_height: int
    detector_id: str
    detector_revision: str
    raw_referents: Tuple[VisualReferentCandidate, ...]
    plausible_referents: Tuple[VisualReferentCandidate, ...]
    selected_referents: Tuple[VisualReferentCandidate, ...]
    raw_grounding_k: int
    plausible_k: int
    effective_k: int
    discarded_candidate_count: int
    detector_forward_count: int
    detector_latency_seconds: float
    ambiguity_latency_seconds: float
    captured_monotonic: float
    completed_monotonic: float
    cache_status: str
    privileged_state_read_count: int
    reason_codes: Tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.raw_grounding_k != len(self.raw_referents):
            raise LanguageGroundingContractError("RAW_GROUNDING_K_MISMATCH")
        if self.plausible_k != len(self.plausible_referents):
            raise LanguageGroundingContractError("PLAUSIBLE_K_MISMATCH")
        if self.effective_k != len(self.selected_referents):
            raise LanguageGroundingContractError("EFFECTIVE_K_MISMATCH")
        if self.privileged_state_read_count != 0:
            raise LanguageGroundingContractError("GROUNDING_PRIVILEGED_STATE_READ")

    def to_dict(self) -> dict:
        return canonical(self)


@dataclass(frozen=True)
class GroundedCandidate:
    candidate_id: str
    interpretation_id: str
    raw_instruction: str
    prompt_text: str
    referring_expression: str
    maneuver: str
    referent_phrase: str
    grounded_referent_id: str
    relation: Optional[str]
    target_event: Optional[str]
    target_landmark: Optional[str]
    target_branch: Optional[str]
    ordering: Optional[str]
    constraint: Optional[str]
    source_observation_id: str
    source_frame_id: int
    image_sha256: str
    semantic_sha256: str
    grounding_sha256: str
    prompt_sha256: str
    status: str
    reason_codes: Tuple[str, ...]
    # Which unresolved slot family this row is a reading of, and which value of
    # that slot it carries.  Both are language facts supplied by the semantic
    # authority; no map/topology value may ever be written here.  They stay None
    # on the referential axis, where the grounded referent identity *is* the
    # semantic axis and these fields would be redundant.
    obligation_type: Optional[str] = None
    semantic_constraint: Optional[str] = None
    semantic_source: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def semantic_projection(self) -> dict:
        projection = {
            "maneuver": self.maneuver,
            "referent_phrase": self.referent_phrase,
            "grounded_referent_id": self.grounded_referent_id,
            "relation": self.relation,
            "target_event": self.target_event,
            "target_landmark": self.target_landmark,
            "target_branch": self.target_branch,
            "ordering": self.ordering,
            "constraint": self.constraint,
        }
        # Added only when a semantic-constraint axis is actually in use, so every
        # historical referential candidate keeps its exact previous digest.
        if self.obligation_type is not None or self.semantic_constraint is not None:
            projection["obligation_type"] = self.obligation_type
            projection["semantic_constraint"] = self.semantic_constraint
        return projection

    def to_dict(self) -> dict:
        return canonical(self)


@dataclass(frozen=True)
class CandidateSetResult:
    status: AmbiguityStatus
    parsed_slots: ParsedSlots
    grounding: GroundingResult
    raw_candidates: Tuple[GroundedCandidate, ...]
    candidates: Tuple[GroundedCandidate, ...]
    raw_k: int
    effective_k: int
    semantic_duplicate: bool
    grounding_duplicate: bool
    prompt_duplicate: bool
    reason_codes: Tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return canonical(self)

