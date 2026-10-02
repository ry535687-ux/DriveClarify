"""Deterministic semantic slot extraction and ambiguity routing."""

from __future__ import annotations

import re
from typing import Optional, Tuple

from .contracts import AmbiguityKind, ParsedSlots


_REFERENCE_NOUNS = (
    "bus",
    "van",
    "truck",
    "car",
    "vehicle",
    "pedestrian",
    "cyclist",
    "motorcycle",
)
_LANDMARK_NOUNS = ("shop", "store", "cafe", "school", "hospital", "station", "building")
_ADJECTIVES = (
    "white",
    "black",
    "red",
    "blue",
    "green",
    "yellow",
    "silver",
    "gray",
    "grey",
    "near",
    "far",
    "left",
    "right",
)


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9'-]+", value.casefold().replace("’", "'")))


def _noun_phrase(text: str, nouns: Tuple[str, ...]) -> Optional[str]:
    words = text.split()
    for index, word in enumerate(words):
        if word in nouns:
            start = index
            while start > 0 and words[start - 1] in _ADJECTIVES:
                start -= 1
            return " ".join(words[start : index + 1])
    return None


class SemanticSlotParser:
    """Small runtime parser; it emits slots before any candidates exist."""

    def parse(self, instruction: str) -> ParsedSlots:
        normalized = _normalize(instruction)
        maneuver = (
            "PULL_OVER"
            if "pull over" in normalized
            else "STOP"
            if re.search(r"\bstop\b", normalized)
            else "TURN"
            if re.search(r"\bturn\b", normalized)
            else "CONTINUE"
            if re.search(r"\b(continue|proceed|go straight)\b", normalized)
            else None
        )
        clear_event = bool(re.search(r"\b(clears|cleared|clear)\b", normalized))
        temporal = (
            "UNTIL_CLEAR"
            if clear_event
            else "AFTER"
            if re.search(r"\b(after|past)\b", normalized)
            else "BEFORE"
            if re.search(r"\bbefore\b", normalized)
            else "UNTIL_CLEAR"
            if re.search(r"\b(until|clears|clear)\b", normalized)
            else None
        )
        spatial = (
            "LEFT_OF"
            if "left of" in normalized
            else "RIGHT_OF"
            if "right of" in normalized
            else "NEAR"
            if re.search(r"\b(near|beside|by)\b", normalized)
            else "AT"
            if re.search(r"\bat\b", normalized)
            else None
        )
        ordering_match = re.search(r"\b(first|second|third|next|nearest|farthest)\b", normalized)
        ordering = ordering_match.group(1).upper() if ordering_match else None
        referent = _noun_phrase(normalized, _REFERENCE_NOUNS)
        landmark = _noun_phrase(normalized, _LANDMARK_NOUNS)
        constraint = (
            "SUITABLE_DISTANCE"
            if "suitable distance" in normalized
            else "ENOUGH_CLEARANCE"
            if "enough clearance" in normalized
            else "LEGAL_AND_SAFE"
            if "legal" in normalized or "safe" in normalized
            else None
        )
        if maneuver is None:
            kind = AmbiguityKind.UNSUPPORTED
            status = "UNSUPPORTED"
            reasons = ("MANEUVER_NOT_RECOGNIZED",)
        elif ordering is not None and referent is None and landmark is None:
            kind = AmbiguityKind.SPATIAL_ORDER
            status = "PARSED"
            reasons = ("ROUTE_TOPOLOGY_ROUTING_REQUIRED",)
        elif landmark is not None:
            kind = AmbiguityKind.LANDMARK
            status = "PARSED"
            reasons = ("LANDMARK_VISUAL_GROUNDING_REQUIRED",)
        elif referent is not None and clear_event:
            kind = AmbiguityKind.TEMPORAL
            status = "PARSED"
            reasons = ("VISUAL_GROUNDING_AND_TRACKING_REQUIRED",)
        elif referent is not None:
            kind = AmbiguityKind.REFERENTIAL
            status = "PARSED"
            reasons = ("VISUAL_REFERENT_ENUMERATION_REQUIRED",)
        elif constraint is not None:
            kind = AmbiguityKind.UNDERSPECIFIED_CONSTRAINT
            status = "PARSED"
            reasons = ("EXISTING_CONSTRAINT_REPRESENTATION_REQUIRED",)
        else:
            kind = AmbiguityKind.UNAMBIGUOUS
            status = "PARSED"
            reasons = ("NO_SUPPORTED_AMBIGUITY_SLOT_FOUND",)
        return ParsedSlots(
            raw_instruction=instruction,
            normalized_instruction=normalized,
            maneuver=maneuver,
            referent_phrase=referent,
            landmark_phrase=landmark,
            spatial_relation=spatial,
            temporal_relation=temporal,
            ordering=ordering,
            constraint=constraint,
            ambiguity_kind=kind,
            parser_status=status,
            reason_codes=reasons,
        )
