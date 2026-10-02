"""Typed, oracle-free contracts for E2_TRACKED_ASSOCIATION_V3."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple


METHOD_ID = "E2_TRACKED_ASSOCIATION_V3"
UNKNOWN = "UNKNOWN"
UNSPECIFIED = "UNSPECIFIED"

OBJECT_CLASSES = (
    "car", "vehicle", "van", "truck", "bus", "motorcycle", "bicycle",
    "pedestrian", "kiosk", "shop", "store", "cafe", "school",
    "hospital", "station", "building", "bus stop", "landmark",
)
COLORS = ("white", "black", "red", "blue", "green", "yellow", "silver", "gray", "grey")
MOTION_STATES = ("MOVING", "PARKED", "STATIONARY")
LANE_RELATIONS = (
    "EGO_LANE", "ADJACENT_LANE", "PARKING_LANE", "LEFT_LANE", "RIGHT_LANE",
)
ROAD_RELATIONS = ("ON_ROAD", "ROADSIDE", "OFF_ROAD")
LONGITUDINAL_ORDERS = ("FRONT", "REAR", "AHEAD", "BEHIND", "FIRST", "SECOND")
NEAR_FAR = ("NEARER", "FARTHER")
LEFT_RIGHT = ("LEFT", "RIGHT", "CENTER")
INSTRUCTION_RELATIONS = ("AFTER", "BEFORE", "NEAR", "AT", "PAST")

FORBIDDEN_CANDIDATE_KEYS = frozenset(
    {
        "true_intent", "true_interpretation", "gold_candidate", "gold_actor_id",
        "carla_actor_id", "query_necessity_gold", "reveal_frame", "reveal_time",
        "expected_e2_transition", "intended_candidate",
    }
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def assert_no_forbidden_candidate_keys(value: Any, path: str = "candidate") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if normalized in FORBIDDEN_CANDIDATE_KEYS or normalized.endswith("_gold"):
                raise PermissionError("E2_V3_CANDIDATE_ORACLE_KEY_FORBIDDEN:" + path + "." + str(key))
            assert_no_forbidden_candidate_keys(child, path + "." + str(key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_no_forbidden_candidate_keys(child, path + "[{}]".format(index))


def _token(text: str, values: Sequence[str], aliases: Optional[Mapping[str, str]] = None) -> str:
    aliases = {} if aliases is None else aliases
    normalized = " " + re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip() + " "
    ordered = sorted(values, key=lambda item: (-len(item), item))
    for value in ordered:
        phrase = value.casefold().replace("_", " ")
        if " " + phrase + " " in normalized:
            return aliases.get(value, value)
    return UNSPECIFIED


@dataclass(frozen=True)
class CandidateObjectSpec:
    candidate_id: str
    interpretation_id: str
    text: str
    object_class: str = UNSPECIFIED
    color: str = UNSPECIFIED
    motion_state: str = UNSPECIFIED
    lane_relation: str = UNSPECIFIED
    road_relation: str = UNSPECIFIED
    relative_longitudinal_order: str = UNSPECIFIED
    apparent_near_far: str = UNSPECIFIED
    parked_vs_moving: str = UNSPECIFIED
    left_right_relation: str = UNSPECIFIED
    instruction_relation: str = UNSPECIFIED
    local_ordinal: str = UNSPECIFIED
    unsupported_attributes: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.interpretation_id or not self.text.strip():
            raise ValueError("E2_V3_CANDIDATE_IDENTITY_OR_TEXT_INCOMPLETE")
        assert_no_forbidden_candidate_keys(asdict(self))

    def typed_projection(self) -> Mapping[str, Any]:
        value = asdict(self)
        value.pop("candidate_id")
        value.pop("interpretation_id")
        value.pop("text")
        value["unsupported_attributes"] = list(value["unsupported_attributes"])
        return value

    def to_dict(self) -> Mapping[str, Any]:
        value = asdict(self)
        value["unsupported_attributes"] = list(value["unsupported_attributes"])
        value["schema_version"] = "driveclarify.e2_v3.certified_candidate.v1"
        value["typed_projection_sha256"] = canonical_sha256(self.typed_projection())
        value["true_intent_field_present"] = False
        value["reason_codes"] = [
            "UNSUPPORTED_ATTRIBUTE:" + item for item in self.unsupported_attributes
        ]
        return value


def parse_certified_candidate(
    candidate_id: str,
    interpretation_id: str,
    text: str,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> CandidateObjectSpec:
    """Parse supported distinctions; every absent value remains UNSPECIFIED."""

    raw = str(text)
    normalized = raw.casefold()
    object_class = _token(normalized, OBJECT_CLASSES, {"vehicle": "vehicle", "bus stop": "bus stop"})
    color = _token(normalized, COLORS, {"grey": "gray"})
    motion = _token(normalized, ("moving", "parked", "stationary"), {
        "moving": "MOVING", "parked": "PARKED", "stationary": "STATIONARY",
    })
    lane = _token(normalized, ("ego lane", "adjacent lane", "parking lane", "left lane", "right lane"), {
        "ego lane": "EGO_LANE", "adjacent lane": "ADJACENT_LANE",
        "parking lane": "PARKING_LANE", "left lane": "LEFT_LANE", "right lane": "RIGHT_LANE",
    })
    road = _token(normalized, ("on road", "roadside", "off road"), {
        "on road": "ON_ROAD", "roadside": "ROADSIDE", "off road": "OFF_ROAD",
    })
    longitudinal = _token(normalized, ("front", "rear", "ahead", "behind", "first", "second"), {
        "front": "FRONT", "rear": "REAR", "ahead": "AHEAD", "behind": "BEHIND",
        "first": "FIRST", "second": "SECOND",
    })
    near_far = _token(normalized, ("nearer", "farther", "near", "far"), {
        "nearer": "NEARER", "near": "NEARER", "farther": "FARTHER", "far": "FARTHER",
    })
    left_right = _token(normalized, ("on the left", "on the right", "left side", "right side", "center"), {
        "on the left": "LEFT", "left side": "LEFT", "on the right": "RIGHT",
        "right side": "RIGHT", "center": "CENTER",
    })
    relation = _token(normalized, ("after", "before", "near", "at", "past"), {
        "after": "AFTER", "before": "BEFORE", "near": "NEAR", "at": "AT", "past": "PAST",
    })
    ordinal = _token(normalized, ("first", "second"), {"first": "FIRST", "second": "SECOND"})
    unsupported = []
    for token in ("third", "fourth", "between", "closest to passenger", "preferred"):
        if token in normalized:
            unsupported.append(token.upper().replace(" ", "_"))
    values = {
        "candidate_id": str(candidate_id),
        "interpretation_id": str(interpretation_id),
        "text": raw,
        "object_class": object_class,
        "color": color,
        "motion_state": motion,
        "lane_relation": lane,
        "road_relation": road,
        "relative_longitudinal_order": longitudinal,
        "apparent_near_far": near_far,
        "parked_vs_moving": motion,
        "left_right_relation": left_right,
        "instruction_relation": relation,
        "local_ordinal": ordinal,
        "unsupported_attributes": tuple(unsupported),
    }
    if overrides:
        assert_no_forbidden_candidate_keys(overrides)
        allowed = set(values).difference({"candidate_id", "interpretation_id", "text"})
        unexpected = set(overrides).difference(allowed)
        if unexpected:
            raise ValueError("E2_V3_UNSUPPORTED_OVERRIDE_KEYS:" + ",".join(sorted(unexpected)))
        values.update(overrides)
    return CandidateObjectSpec(**values)


def validate_candidate_set(specs: Sequence[CandidateObjectSpec]) -> Mapping[str, Any]:
    if len(specs) < 2:
        raise ValueError("E2_V3_CERTIFIED_CANDIDATE_SET_LT_2")
    candidate_ids = [item.candidate_id for item in specs]
    interpretation_ids = [item.interpretation_id for item in specs]
    projections = [canonical_sha256(item.typed_projection()) for item in specs]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("E2_V3_DUPLICATE_CANDIDATE_ID")
    if len(set(interpretation_ids)) != len(interpretation_ids):
        raise ValueError("E2_V3_DUPLICATE_INTERPRETATION_ID")
    if len(set(projections)) != len(projections):
        raise ValueError("E2_V3_DISTINCT_INTERPRETATIONS_COLLAPSED_TO_IDENTICAL_TYPED_SPECS")
    return {
        "schema_version": "driveclarify.e2_v3.candidate_set_validation.v1",
        "candidate_count": len(specs),
        "candidate_ids_distinct": True,
        "interpretation_ids_distinct": True,
        "typed_projections_distinct": True,
        "true_intent_field_count": 0,
        "unsupported_reason_codes": sorted({
            "UNSUPPORTED_ATTRIBUTE:" + reason
            for spec in specs for reason in spec.unsupported_attributes
        }),
        "candidate_set_digest": canonical_sha256([item.to_dict() for item in specs]),
    }


@dataclass(frozen=True)
class AcquisitionSchedule:
    periodic_cadence_ticks: int = 10
    resolved_validation_cadence_ticks: int = 20
    maximum_detector_invocations_per_tick: int = 2
    track_loss_event_trigger: bool = True
    unresolved_event_trigger: bool = True
    stale_result_policy: str = "REJECT_UNLESS_EXACT_SOURCE_FRAME"

    def should_invoke(
        self,
        *,
        frame_id: int,
        first_frame_id: int,
        resolved: bool,
        track_loss_event: bool,
        already_invoked_frame: Optional[int],
    ) -> Tuple[bool, str]:
        if already_invoked_frame == frame_id:
            return False, "ALREADY_INVOKED_THIS_FRAME"
        elapsed = int(frame_id) - int(first_frame_id)
        cadence = self.resolved_validation_cadence_ticks if resolved else self.periodic_cadence_ticks
        if track_loss_event and self.track_loss_event_trigger:
            return True, "TRACK_LOSS_EVENT"
        if elapsed == 0:
            return True, "FIRST_RUNTIME_FRAME"
        if elapsed >= 0 and elapsed % cadence == 0:
            return True, "FROZEN_PERIODIC_CADENCE"
        return False, "CADENCE_NOT_DUE"

    def to_dict(self) -> Mapping[str, Any]:
        return {
            **asdict(self),
            "schema_version": "driveclarify.e2_v3.acquisition_schedule.v1",
            "runtime_inputs_only": True,
            "reveal_time_or_frame_input": False,
        }


__all__ = [
    "AcquisitionSchedule", "CandidateObjectSpec", "METHOD_ID", "UNKNOWN",
    "UNSPECIFIED", "assert_no_forbidden_candidate_keys", "canonical_sha256",
    "parse_certified_candidate", "validate_candidate_set",
]
