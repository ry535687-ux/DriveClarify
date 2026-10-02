"""Strict, label-free contracts for Temporal Grounding V1."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "driveclarify.temporal_grounding_v1.v1"
FEATURE_FLAG = "DRIVECLARIFY_TEMPORAL_GROUNDING_V1"
FORBIDDEN_POLICY_KEYS = frozenset(
    {
        "actor_id",
        "actor_transform",
        "actor_velocity",
        "expected_decision",
        "gold_candidate_index",
        "gold_cleared_timestamp",
        "gold_intended_actor",
        "scenario_internal_trigger",
        "simulator_bbox_ground_truth",
    }
)


class TemporalGroundingContractError(ValueError):
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
        canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_policy_firewall(value: Any) -> None:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        overlap = {str(key).casefold() for key in value}.intersection(
            FORBIDDEN_POLICY_KEYS
        )
        if overlap:
            raise TemporalGroundingContractError(
                "TEMPORAL_POLICY_FORBIDDEN_KEYS:" + ",".join(sorted(overlap))
            )
        for item in value.values():
            assert_policy_firewall(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            assert_policy_firewall(item)


class TrackLifecycleState(str, Enum):
    ACTIVE = "ACTIVE"
    TEMPORARILY_OCCLUDED = "TEMPORARILY_OCCLUDED"
    LOST = "LOST"
    REACQUIRE_PENDING = "REACQUIRE_PENDING"
    EXPIRED = "EXPIRED"


class EventState(str, Enum):
    UNKNOWN = "UNKNOWN"
    APPROACHING = "APPROACHING"
    OCCUPYING_RELEVANT_REGION = "OCCUPYING_RELEVANT_REGION"
    CLEARING = "CLEARING"
    CLEARED = "CLEARED"
    TRACK_LOST = "TRACK_LOST"
    STALE = "STALE"


@dataclass(frozen=True)
class TrackObservation:
    track_id: str
    source_grounding_id: str
    phrase: str
    bbox_xyxy: Tuple[float, float, float, float]
    frame_id: int
    simulation_time: float
    age_frames: int
    time_since_seen_frames: int
    confidence: float
    velocity_proxy_xy: Tuple[float, float]
    lifecycle_state: TrackLifecycleState
    freshness_frames: int
    association_source: str
    association_iou: Optional[float]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.track_id or not self.source_grounding_id or not self.phrase:
            raise TemporalGroundingContractError("TRACK_IDENTITY_INCOMPLETE")
        if len(self.bbox_xyxy) != 4 or not all(
            math.isfinite(float(item)) for item in self.bbox_xyxy
        ):
            raise TemporalGroundingContractError("TRACK_BBOX_INVALID")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise TemporalGroundingContractError("TRACK_CONFIDENCE_INVALID")

    @property
    def centroid_xy(self) -> Tuple[float, float]:
        x0, y0, x1, y1 = self.bbox_xyxy
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    @property
    def area(self) -> float:
        x0, y0, x1, y1 = self.bbox_xyxy
        return max(0.0, x1 - x0) * max(0.0, y1 - y0)

    def to_dict(self) -> dict:
        return canonical(self)


@dataclass(frozen=True)
class EventRegion:
    region_id: str
    bbox_xyxy: Tuple[float, float, float, float]
    image_width: int
    image_height: int
    construction_source: str
    target_branch_id: str
    privileged_state_read_count: int = 0
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.privileged_state_read_count != 0:
            raise TemporalGroundingContractError("EVENT_REGION_PRIVILEGED_READ")
        x0, y0, x1, y1 = self.bbox_xyxy
        if not (0 <= x0 < x1 <= self.image_width and 0 <= y0 < y1 <= self.image_height):
            raise TemporalGroundingContractError("EVENT_REGION_BBOX_INVALID")

    def to_dict(self) -> dict:
        return canonical(self)


@dataclass(frozen=True)
class EventObservation:
    event_id: str
    track_id: str
    state: EventState
    frame_id: int
    simulation_time: float
    first_evidence_frame: Optional[int]
    transition_frames: Tuple[Tuple[str, int], ...]
    cleared_candidate_frame: Optional[int]
    cleared_confirmed_frame: Optional[int]
    event_latency_frames: Optional[int]
    track_age_frames: int
    region_id: str
    overlap_fraction: float
    outside_persistence_frames: int
    motion_evidence_frames: int
    confidence: float
    reason_codes: Tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return canonical(self)


@dataclass(frozen=True)
class RouteTarget:
    target_id: str
    junction_id: str
    branch_id: str
    road_option: str
    route_opportunity_index: int
    anchor_xy: Tuple[float, float]
    route_digest: str
    source: str
    executable: bool
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return canonical(self)


@dataclass(frozen=True)
class TemporalCandidate:
    candidate_id: str
    candidate_core_id: str
    candidate_set_id: str
    interpretation_id: str
    instruction_id: str
    raw_instruction: str
    prompt_text: str
    referent_id: str
    event_id: str
    event_state: EventState
    target_id: str
    branch_id: str
    relation: str
    maneuver: str
    generation_frame: int
    source_observation_id: str
    freshness: str
    semantic_sha256: str
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        instruction_id: str,
        raw_instruction: str,
        prompt_text: str,
        referent_id: str,
        event_id: str,
        event_state: EventState,
        target: RouteTarget,
        relation: str,
        maneuver: str,
        generation_frame: int,
        source_observation_id: str,
        freshness: str = "FRESH",
    ) -> "TemporalCandidate":
        projection = {
            "referent_id": referent_id,
            "event_id": event_id,
            "event_state": event_state.value,
            "target_id": target.target_id,
            "branch_id": target.branch_id,
            "relation": relation,
            "maneuver": maneuver,
        }
        semantic = canonical_sha256(projection)
        core = "tgv1-core-" + canonical_sha256(
            {
                "referent_id": referent_id,
                "event_id": event_id,
                "target_id": target.target_id,
                "relation": relation,
                "maneuver": maneuver,
            }
        )[:20]
        candidate_set_id = "tgv1-set-" + canonical_sha256(
            {"instruction_id": instruction_id, "core": core, "state": event_state.value}
        )[:20]
        candidate_id = "tgv1-cand-" + canonical_sha256(
            {
                "candidate_set_id": candidate_set_id,
                "generation_frame": int(generation_frame),
                "source_observation_id": source_observation_id,
            }
        )[:20]
        return cls(
            candidate_id=candidate_id,
            candidate_core_id=core,
            candidate_set_id=candidate_set_id,
            interpretation_id="tgv1-meaning-" + semantic[:20],
            instruction_id=instruction_id,
            raw_instruction=raw_instruction,
            prompt_text=prompt_text,
            referent_id=referent_id,
            event_id=event_id,
            event_state=event_state,
            target_id=target.target_id,
            branch_id=target.branch_id,
            relation=relation,
            maneuver=maneuver,
            generation_frame=int(generation_frame),
            source_observation_id=source_observation_id,
            freshness=freshness,
            semantic_sha256=semantic,
        )

    def to_dict(self) -> dict:
        return canonical(self)


@dataclass(frozen=True)
class TemporalInformationUpdate:
    update_id: str
    event_id: str
    old_state: EventState
    new_state: EventState
    source_frames: Tuple[int, ...]
    candidate_affected: str
    target_affected: str
    emitted_frame: int
    emitted_simulation_time: float
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        old_state: EventState,
        new_state: EventState,
        source_frames: Sequence[int],
        candidate_affected: str,
        target_affected: str,
        emitted_frame: int,
        emitted_simulation_time: float,
    ) -> "TemporalInformationUpdate":
        payload = {
            "event_id": event_id,
            "old_state": old_state.value,
            "new_state": new_state.value,
            "source_frames": list(source_frames),
            "candidate_affected": candidate_affected,
            "target_affected": target_affected,
            "emitted_frame": emitted_frame,
        }
        return cls(
            update_id="tgv1-info-" + canonical_sha256(payload)[:20],
            event_id=event_id,
            old_state=old_state,
            new_state=new_state,
            source_frames=tuple(int(item) for item in source_frames),
            candidate_affected=candidate_affected,
            target_affected=target_affected,
            emitted_frame=int(emitted_frame),
            emitted_simulation_time=float(emitted_simulation_time),
        )

    def to_dict(self) -> dict:
        return canonical(self)


__all__ = [
    "EventObservation",
    "EventRegion",
    "EventState",
    "FEATURE_FLAG",
    "RouteTarget",
    "SCHEMA_VERSION",
    "TemporalCandidate",
    "TemporalGroundingContractError",
    "TemporalInformationUpdate",
    "TrackLifecycleState",
    "TrackObservation",
    "assert_policy_firewall",
    "canonical",
    "canonical_sha256",
]
