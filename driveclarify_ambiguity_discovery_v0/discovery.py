"""Rule-based instruction/scene compatibility discovery pipeline.

The implementation is intentionally small and auditable.  It identifies one target
noun phrase, extracts a bounded set of observable modifiers, and retrieves every
environment entity compatible with those constraints.  Ambiguity is reported only
when at least two distinct entities remain compatible.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import CandidateInterpretation, DiscoveryResult, EnvironmentEntity


_SPACE_RE = re.compile(r"\s+")
_COLORS = ("white", "black", "red", "blue", "gray", "grey", "silver", "yellow", "green")
_STATES = ("parked", "moving", "stopped")
_SIDES = ("left", "right")
_DIRECTIONS = ("north", "south", "east", "west")
_ORDINALS = {"first": 1, "second": 2, "third": 3}


@dataclass(frozen=True)
class _ReferenceSpec:
    noun: str
    slot: str
    reference_text: str
    constraints: Mapping[str, Any]


# Longest noun forms come first so "loading zone" wins over "zone".
_NOUN_SPECS: tuple[tuple[str, str], ...] = (
    ("loading zone", "target_area"),
    ("parking bay", "target_area"),
    ("parking area", "target_area"),
    ("bus stop", "target_landmark"),
    ("cargo van", "target_vehicle"),
    ("delivery van", "target_vehicle"),
    ("pickup truck", "target_vehicle"),
    ("minivan", "target_vehicle"),
    ("pedestrian", "target_road_user"),
    ("cyclist", "target_road_user"),
    ("entrance", "target_access_point"),
    ("vehicle", "target_vehicle"),
    ("truck", "target_vehicle"),
    ("van", "target_vehicle"),
    ("car", "target_vehicle"),
    ("bus", "target_vehicle"),
    ("gate", "target_access_point"),
    ("exit", "target_access_point"),
    ("lane", "target_lane"),
    ("zone", "target_area"),
    ("bay", "target_area"),
    ("cafe", "target_landmark"),
    ("shop", "target_landmark"),
    ("store", "target_landmark"),
)

_RELATION_OR_ACTION = re.compile(
    r"\b(?:after|before|beside|behind|past|near|at|by|towards?|next\s+to|"
    r"follow|pass|take|use|choose|enter|into|in)\b"
)


def _normalize(value: Any) -> str:
    text = str(value).strip().lower().replace("’", "'").replace("—", " ")
    return _SPACE_RE.sub(" ", text)


def _noun_mentions(text: str) -> list[tuple[int, int, str, str]]:
    matches: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    for noun, slot in _NOUN_SPECS:
        for match in re.finditer(rf"\b{re.escape(noun)}s?\b", text):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in occupied):
                continue
            occupied.append(span)
            matches.append((span[0], span[1], noun, slot))
    return sorted(matches)


def _select_target_mention(text: str) -> tuple[int, int, str, str] | None:
    mentions = _noun_mentions(text)
    if not mentions:
        return None
    anchors = list(_RELATION_OR_ACTION.finditer(text))
    if anchors:
        for anchor in anchors:
            following = [mention for mention in mentions if mention[0] >= anchor.end()]
            if following:
                return following[0]
    return mentions[0]


def _reference_spec(text: str) -> _ReferenceSpec | None:
    mention = _select_target_mention(text)
    if mention is None:
        return None
    start, end, noun, slot = mention
    window_start = max(0, start - 40)
    window_end = min(len(text), end + 28)
    window = text[window_start:window_end]
    constraints: dict[str, Any] = {}
    for color in _COLORS:
        if re.search(rf"\b{color}\b", window):
            constraints["color"] = "gray" if color == "grey" else color
            break
    for state in _STATES:
        if re.search(rf"\b{state}\b", window):
            constraints["state"] = state
            break
    for word, value in _ORDINALS.items():
        if re.search(rf"\b{word}\b", window):
            constraints["ordinal"] = value
            break
    for side in _SIDES:
        if re.search(rf"\b{side}\b", window):
            constraints["side"] = side
            break
    for direction in _DIRECTIONS:
        if re.search(rf"\b{direction}\b", window):
            constraints["direction"] = direction
            break
    reference_tokens = [
        str(value) for value in (
            constraints.get("ordinal"),
            constraints.get("direction"),
            constraints.get("side"),
            constraints.get("color"),
            constraints.get("state"),
            noun,
        ) if value is not None
    ]
    ordinal_names = {value: key for key, value in _ORDINALS.items()}
    if constraints.get("ordinal") in ordinal_names:
        reference_tokens[0] = ordinal_names[int(constraints["ordinal"])]
    return _ReferenceSpec(noun, slot, " ".join(reference_tokens), constraints)


def _tokens(entity: EnvironmentEntity) -> str:
    values = [entity.entity_type, entity.display_name]
    values.extend(str(value) for value in entity.attributes.values())
    return _normalize(" ".join(values))


def _noun_compatible(noun: str, entity: EnvironmentEntity) -> bool:
    entity_type = _normalize(entity.entity_type)
    tokens = _tokens(entity)
    if noun in {"vehicle", "van", "cargo van", "delivery van", "minivan", "car", "truck", "pickup truck", "bus"}:
        if entity_type != "vehicle":
            return False
        noun_aliases = {
            "vehicle": (),
            "van": ("van", "minivan"),
            "cargo van": ("cargo van",),
            "delivery van": ("delivery van",),
            "minivan": ("minivan",),
            "car": ("car", "sedan", "hatchback"),
            "truck": ("truck", "pickup"),
            "pickup truck": ("pickup truck", "pickup"),
            "bus": ("bus",),
        }[noun]
        return not noun_aliases or any(alias in tokens for alias in noun_aliases)
    expected_types = {
        "pedestrian": "road_user",
        "cyclist": "road_user",
        "entrance": "access_point",
        "gate": "access_point",
        "exit": "access_point",
        "lane": "lane",
        "loading zone": "area",
        "parking bay": "area",
        "parking area": "area",
        "zone": "area",
        "bay": "area",
        "bus stop": "landmark",
        "cafe": "landmark",
        "shop": "landmark",
        "store": "landmark",
    }
    if entity_type != expected_types.get(noun):
        return False
    return noun in tokens


def _attribute_compatible(key: str, expected: Any, entity: EnvironmentEntity) -> bool:
    actual = entity.attributes.get(key)
    if key == "color" and _normalize(actual) == "grey":
        actual = "gray"
    if isinstance(expected, int):
        return actual == expected
    return _normalize(actual) == _normalize(expected)


def _retrieve(spec: _ReferenceSpec, entities: Sequence[EnvironmentEntity]) -> tuple[EnvironmentEntity, ...]:
    compatible = [
        entity
        for entity in entities
        if _noun_compatible(spec.noun, entity)
        and all(_attribute_compatible(key, value, entity) for key, value in spec.constraints.items())
    ]
    return tuple(sorted(compatible, key=lambda item: item.entity_id))


def _confidence(candidate_count: int, constraint_count: int, reference_found: bool) -> float:
    if not reference_found:
        return 0.72
    if candidate_count == 0:
        return 0.62
    if candidate_count == 1:
        return round(min(0.97, 0.91 + 0.01 * constraint_count), 3)
    if candidate_count == 2:
        return round(min(0.96, 0.86 + 0.02 * constraint_count), 3)
    return round(min(0.91, 0.80 + 0.015 * constraint_count), 3)


def _interpretations(
    instruction: str,
    spec: _ReferenceSpec,
    candidates: Sequence[EnvironmentEntity],
) -> tuple[CandidateInterpretation, ...]:
    result: list[CandidateInterpretation] = []
    for index, entity in enumerate(candidates):
        label = chr(ord("A") + index)
        text = (
            f'Interpret "{spec.reference_text}" in "{instruction}" as '
            f"{entity.display_name} ({entity.entity_id})."
        )
        result.append(
            CandidateInterpretation(
                candidate_id=f"CANDIDATE_{label}",
                entity=entity,
                slot=spec.slot,
                reference_text=spec.reference_text,
                interpretation=text,
            )
        )
    return tuple(result)


class AmbiguityDiscoveryPipeline:
    """Optional discovery pipeline with an opt-in existing-consequence handoff."""

    def __init__(self, *, enable_consequence_handoff: bool = False) -> None:
        self.enable_consequence_handoff = enable_consequence_handoff

    def discover(
        self,
        instruction: str,
        environment: Sequence[Mapping[str, Any] | EnvironmentEntity],
    ) -> DiscoveryResult:
        original_environment = copy.deepcopy(environment)
        entities = tuple(
            item if isinstance(item, EnvironmentEntity) else EnvironmentEntity.from_dict(item)
            for item in environment
        )
        if len({item.entity_id for item in entities}) != len(entities):
            raise ValueError("ENVIRONMENT_ENTITY_IDS_MUST_BE_UNIQUE")
        normalized_instruction = _SPACE_RE.sub(" ", str(instruction).strip())
        spec = _reference_spec(_normalize(normalized_instruction))
        if spec is None:
            candidates: tuple[EnvironmentEntity, ...] = ()
            interpretations: tuple[CandidateInterpretation, ...] = ()
            detected = False
            slot = None
            reference_text = None
            reason = "no supported grounding reference found"
            codes = ("NO_SUPPORTED_REFERENCE",)
            confidence = _confidence(0, 0, False)
        else:
            candidates = _retrieve(spec, entities)
            interpretations = _interpretations(normalized_instruction, spec, candidates)
            detected = len(candidates) >= 2
            slot = spec.slot
            reference_text = spec.reference_text
            if detected:
                reason = "multiple compatible grounding candidates"
                codes = ("MULTIPLE_COMPATIBLE_GROUNDING_CANDIDATES",)
            elif len(candidates) == 1:
                reason = "single compatible grounding candidate"
                codes = ("SINGLE_COMPATIBLE_GROUNDING_CANDIDATE",)
            else:
                reason = "no compatible grounding candidate"
                codes = ("NO_COMPATIBLE_GROUNDING_CANDIDATE",)
            confidence = _confidence(len(candidates), len(spec.constraints), True)

        provisional = DiscoveryResult(
            instruction=normalized_instruction,
            ambiguity_detected=detected,
            analyzed_slot=slot,
            detected_ambiguous_slot=slot if detected else None,
            unresolved_slot=slot if detected else None,
            reference_text=reference_text,
            candidate_entities=candidates,
            candidate_interpretations=interpretations,
            confidence=confidence,
            reason=reason,
            reason_codes=codes,
            consequence_handoff={
                "status": "NOT_REQUESTED",
                "reason": "OPTIONAL_HANDOFF_DISABLED",
            },
        )
        if self.enable_consequence_handoff:
            # Lazy import keeps the prototype optional and prevents import-time changes
            # to any existing consequence/runtime path.
            from .consequence_bridge import pass_to_existing_consequence_module

            handoff = pass_to_existing_consequence_module(provisional)
            provisional = DiscoveryResult(
                **{
                    **provisional.__dict__,
                    "consequence_handoff": handoff,
                }
            )
        if environment != original_environment:
            raise RuntimeError("DISCOVERY_PIPELINE_MUTATED_ENVIRONMENT")
        return provisional


def discover_ambiguity(
    instruction: str,
    environment: Sequence[Mapping[str, Any] | EnvironmentEntity],
) -> DiscoveryResult:
    """Convenience API.  Consequence handoff remains disabled by default."""

    return AmbiguityDiscoveryPipeline().discover(instruction, environment)

