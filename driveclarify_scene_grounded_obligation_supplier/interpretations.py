"""Language layer: derive semantic interpretations from the parsed instruction.

This module imports no map, topology, junction, opportunity or scene symbol at
all.  That is the point: the value space of an unresolved slot is a property of
the words, so it must be computable before any map exists.  A map can therefore
only veto an interpretation downstream; it can never manufacture one.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

from .contracts import ObligationType, SemanticInterpretation, SupplierContractError


_DIRECTION_TOKENS = {
    "LEFT": re.compile(r"\bleft\b"),
    "RIGHT": re.compile(r"\bright\b"),
    "STRAIGHT": re.compile(r"\b(straight|ahead)\b"),
}
# The language-admissible value space for an unresolved turn-direction slot.
DIRECTION_VALUE_SPACE = ("LEFT", "RIGHT", "STRAIGHT")

ORDINAL_TO_ROUTE_ORDER = {
    "FIRST": 1,
    "SECOND": 2,
    "THIRD": 3,
    "NEAREST": 1,
    # ``FARTHEST`` names the last existing opportunity rather than a fixed index.
    "FARTHEST": None,
}
# ``next`` does not fix an ordinal: it admits the immediately upcoming
# opportunity and the one after the referent event.  This is a property of the
# word, decided before any map is consulted.
_UNRESOLVED_ORDINALS = {"NEXT": ("FIRST", "SECOND")}

# Candidate-row fields the R4.4 semantic authority stamps.  They are written by
# exactly one owner (the arbitration seam) and read here; no map value is ever
# permitted to appear in them.
SEMANTIC_CONSTRAINT_FIELD = "r4_4_semantic_constraint"
OBLIGATION_TYPE_FIELD = "r4_4_obligation_type"


def _normalized_instruction(parsed_slots: Mapping[str, Any]) -> str:
    value = parsed_slots.get("normalized_instruction")
    if isinstance(value, str) and value:
        return value
    raw = parsed_slots.get("raw_instruction")
    return str(raw or "").casefold()


def resolved_direction_from_language(
    parsed_slots: Mapping[str, Any]
) -> Optional[str]:
    """Return the direction the instruction states, or None if unresolved."""

    text = _normalized_instruction(parsed_slots)
    stated = [
        direction
        for direction, pattern in _DIRECTION_TOKENS.items()
        if pattern.search(text)
    ]
    # A single explicitly stated direction resolves the slot.  Two stated
    # directions ("left or right") leave it unresolved rather than picking one.
    if len(stated) == 1:
        return stated[0]
    return None


def direction_value_space(parsed_slots: Mapping[str, Any]) -> tuple[str, ...]:
    """Language-admissible direction values, decided before any map query."""

    resolved = resolved_direction_from_language(parsed_slots)
    if resolved is not None:
        return (resolved,)
    return DIRECTION_VALUE_SPACE


def ordinal_value_space(parsed_slots: Mapping[str, Any]) -> tuple[str, ...]:
    """Language-admissible execution-location ordinals for this instruction."""

    ordering = parsed_slots.get("ordering")
    if not isinstance(ordering, str):
        return ()
    token = ordering.strip().upper()
    if token in _UNRESOLVED_ORDINALS:
        return _UNRESOLVED_ORDINALS[token]
    if token in ORDINAL_TO_ROUTE_ORDER:
        return (token,)
    return ()


def language_semantic_authority(
    parsed_slots: Mapping[str, Any]
) -> Optional[tuple[str, tuple[str, ...], str]]:
    """Return ``(obligation_type, value_space, semantic_source)`` or ``None``.

    This is the single place where the words decide what kind of obligation is
    at stake and which values the unresolved slot admits.  It consults no map,
    topology, opportunity, detector inventory or candidate count, so any caller
    that needs the semantic value space must come here rather than re-deriving
    it, and no second semantic authority can appear.
    """

    if not isinstance(parsed_slots, Mapping):
        raise SupplierContractError("PARSED_SLOTS_INVALID")
    ordinals = ordinal_value_space(parsed_slots)
    if ordinals:
        return (
            ObligationType.EXECUTION_LOCATION.value,
            ordinals,
            "PARSED_SLOT_ORDERING",
        )
    maneuver = str(parsed_slots.get("maneuver") or "").strip().upper()
    if maneuver == "TURN":
        value_space = direction_value_space(parsed_slots)
        return (
            ObligationType.MANEUVER_DIRECTION.value,
            value_space,
            "PARSED_SLOT_MANEUVER_TURN_DIRECTION_RESOLVED"
            if len(value_space) == 1
            else "PARSED_SLOT_MANEUVER_TURN_DIRECTION_UNRESOLVED",
        )
    # No supported unresolved slot: say nothing rather than guessing.
    return None


def enumerate_semantic_interpretations(
    parsed_slots: Mapping[str, Any],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> tuple[SemanticInterpretation, ...]:
    """Derive interpretations from the unresolved instruction slot only.

    The number of interpretations is a language fact.  No map, topology,
    detector inventory or candidate count is consulted here, and the function
    never pads or truncates to a target K.

    When the R4.4 semantic authority has already stamped a constraint onto a
    candidate row, that stamp is honoured verbatim.  This keeps the semantic
    writer count at exactly one: positional assignment below is only the
    fallback for callers that never went through arbitration.
    """

    if not isinstance(parsed_slots, Mapping):
        raise SupplierContractError("PARSED_SLOTS_INVALID")
    rows = [row for row in candidate_rows if isinstance(row, Mapping)]
    if not rows:
        return ()

    authority = language_semantic_authority(parsed_slots)
    if authority is None:
        return ()
    obligation_type, value_space, semantic_source = authority

    interpretations: list[SemanticInterpretation] = []
    for index, row in enumerate(rows):
        # One candidate row carries one reading.  When the language admits more
        # values than there are candidate rows, the extra values have no
        # candidate to attach to and are not invented here; when it admits
        # fewer, the surplus rows get no interpretation.
        stamped = row.get(SEMANTIC_CONSTRAINT_FIELD)
        stamped_type = row.get(OBLIGATION_TYPE_FIELD)
        if isinstance(stamped, str) and stamped.strip():
            # Honour the arbitrated stamp; never re-decide it positionally.
            if stamped not in value_space:
                raise SupplierContractError(
                    "ARBITRATED_SEMANTIC_CONSTRAINT_OUTSIDE_LANGUAGE_VALUE_SPACE"
                )
            row_constraint = stamped
            row_obligation_type = (
                stamped_type
                if isinstance(stamped_type, str) and stamped_type.strip()
                else obligation_type
            )
            if row_obligation_type != obligation_type:
                raise SupplierContractError(
                    "ARBITRATED_OBLIGATION_TYPE_DISAGREES_WITH_LANGUAGE_AUTHORITY"
                )
        elif index < len(value_space):
            row_constraint = value_space[index]
            row_obligation_type = obligation_type
        else:
            break
        candidate_id = str(row.get("candidate_id") or "").strip()
        interpretation_id = str(row.get("interpretation_id") or "").strip()
        observation_id = str(row.get("source_observation_id") or "").strip()
        anchor = str(
            row.get("referent_id") or row.get("target_landmark") or ""
        ).strip()
        if not (candidate_id and interpretation_id and observation_id and anchor):
            continue
        interpretations.append(
            SemanticInterpretation(
                interpretation_id=interpretation_id,
                candidate_id=candidate_id,
                source_observation_id=observation_id,
                anchor_identity=anchor,
                obligation_type=row_obligation_type,
                semantic_constraint=row_constraint,
                semantic_source=semantic_source,
                grounded_anchor_ids=(anchor,),
            )
        )
    return tuple(interpretations)


__all__ = [
    "DIRECTION_VALUE_SPACE",
    "OBLIGATION_TYPE_FIELD",
    "ORDINAL_TO_ROUTE_ORDER",
    "SEMANTIC_CONSTRAINT_FIELD",
    "direction_value_space",
    "enumerate_semantic_interpretations",
    "language_semantic_authority",
    "ordinal_value_space",
    "resolved_direction_from_language",
]
