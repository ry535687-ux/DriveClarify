"""Minimal, offline-only ambiguity ontology for R4.3 Discovery V2 preparation.

This module deliberately does not import CARLA, torch, a detector, or the
production Method runtime.  Grounding candidates are inputs to qualification;
they are never treated as semantic interpretations by rank or list position.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence


ONTOLOGY_TYPES = (
    "NO_AMBIGUITY",
    "REFERENTIAL_AMBIGUITY",
    "MANEUVER_DIRECTION_UNDERSPECIFIED",
    "TARGET_ORDER_AMBIGUITY",
    "LOCATION_OR_SIDE_UNDERSPECIFIED",
    "MULTI_SLOT_AMBIGUITY",
    "UNKNOWN_AMBIGUITY_TYPE",
)

_QUALIFIED_GROUNDING = frozenset(
    {
        "QUALIFIED",
        "QUALIFIED_EXISTING_PLAUSIBILITY_CONTRACT",
        "QUALIFIED_STATIC_SCENE_GROUNDED_IDENTITY",
    }
)
_PHYSICAL_IDENTITY_FIELDS = (
    "actor_id",
    "track_id",
    "map_landmark_id",
    "scenario_landmark_id",
)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize(instruction: str) -> str:
    return " ".join(re.findall(r"[a-z0-9'-]+", instruction.casefold()))


def _fixed_turn_direction(instruction: str) -> str | None:
    normalized = _normalize(instruction)
    left = bool(re.search(r"\bturn left\b", normalized))
    right = bool(re.search(r"\bturn right\b", normalized))
    if left == right:
        return None
    return "LEFT" if left else "RIGHT"


def _stable_physical_identity(anchor: Mapping[str, Any]) -> Mapping[str, Any] | None:
    identity = anchor.get("physical_identity")
    if not isinstance(identity, Mapping):
        identity = anchor
    for field in _PHYSICAL_IDENTITY_FIELDS:
        value = identity.get(field)
        if value is not None and str(value).strip():
            return {"kind": field, "value": value}
    return None


def _is_qualified_white_van(anchor: Mapping[str, Any]) -> bool:
    attributes = anchor.get("attributes")
    if not isinstance(attributes, Mapping):
        attributes = {}
    color = str(attributes.get("color", anchor.get("color", ""))).casefold()
    object_class = str(anchor.get("object_class", "")).casefold()
    return bool(
        str(anchor.get("grounding_status")) in _QUALIFIED_GROUNDING
        and object_class == "van"
        and color == "white"
        and _stable_physical_identity(anchor) is not None
    )


def collapse_semantic_duplicates(
    interpretations: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse semantically identical resolved candidates, preserving order."""

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for interpretation in interpretations:
        row = dict(interpretation)
        key = canonical_sha256(
            {
                "anchor": row.get("anchor", {}).get("physical_identity"),
                "maneuver": row.get("maneuver"),
                "temporal_relation": row.get("temporal_relation"),
                "resolved_instruction": _normalize(str(row.get("resolved_instruction", ""))),
                "future_obligation": row.get("future_obligation"),
            }
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _resolved_instruction(direction: str, anchor_label: str | None = None) -> str:
    suffix = "" if anchor_label is None else " " + anchor_label
    return "Turn {} after the white van{}.".format(direction.casefold(), suffix)


def _interpretation(
    *,
    slot: str,
    ambiguity_type: str,
    anchor: Mapping[str, Any],
    direction: str,
    source_instruction: str,
    source_observation: str,
    world_identity: str,
    route_identity: str,
    anchor_label: str | None = None,
) -> dict[str, Any]:
    physical_identity = _stable_physical_identity(anchor)
    if physical_identity is None:
        raise ValueError("STABLE_PHYSICAL_IDENTITY_REQUIRED")
    target_projection = {
        "anchor": physical_identity,
        "temporal_relation": "AFTER",
        "action_family": "TURN",
        "direction": direction,
    }
    branch_projection = {
        "route_identity": route_identity,
        "commitment": "FIRST_POST_ANCHOR_JUNCTION",
        "direction": direction,
    }
    obligation_projection = {
        "action_family": "TURN",
        "direction": direction,
        "temporal_relation": "AFTER",
        "anchor": physical_identity,
        "branch": branch_projection,
    }
    target_digest = canonical_sha256(target_projection)
    branch_digest = canonical_sha256(branch_projection)
    obligation_digest = canonical_sha256(obligation_projection)
    resolved = _resolved_instruction(direction, anchor_label)
    semantic_projection = {
        "ambiguity_type": ambiguity_type,
        "anchor": physical_identity,
        "maneuver": {"action_family": "TURN", "direction": direction},
        "temporal_relation": "AFTER",
        "resolved_instruction": _normalize(resolved),
        "future_obligation": obligation_projection,
    }
    interpretation_digest = canonical_sha256(semantic_projection)
    return {
        "interpretation_id": "r43v2-meaning-" + interpretation_digest[:20],
        "candidate_slot": slot,
        "ambiguity_type": ambiguity_type,
        "anchor": {
            "physical_identity": physical_identity,
            "object_class": "van",
            "attributes": {"color": "white"},
            "grounding_status": anchor.get("grounding_status"),
        },
        "maneuver": {"action_family": "TURN", "direction": direction},
        "temporal_relation": "AFTER",
        "target_identity": "target-" + target_digest[:20],
        "branch_identity": "branch-{}-{}".format(direction.casefold(), branch_digest[:16]),
        "future_obligation": {
            **obligation_projection,
            "obligation_id": "obligation-" + obligation_digest[:20],
        },
        "target_digest": target_digest,
        "branch_digest": branch_digest,
        "obligation_digest": obligation_digest,
        "source_instruction": source_instruction,
        "resolved_instruction": resolved,
        "source_observation": source_observation,
        "world_identity": world_identity,
        "route_identity": route_identity,
        "non_language_input_identity": canonical_sha256(
            {
                "source_observation": source_observation,
                "world_identity": world_identity,
                "route_identity": route_identity,
            }
        ),
        "qualification_status": "QUALIFIED_SEMANTIC_INTERPRETATION",
        "reason_code": "ONE_SLOT_RESOLVED_WITH_STABLE_ANCHOR",
    }


def generate_semantic_interpretations(
    *,
    raw_instruction: str,
    grounding_candidates: Sequence[Mapping[str, Any]],
    source_observation: str,
    world_identity: str,
    route_identity: str,
    direction_options: Sequence[str] = ("LEFT", "RIGHT"),
) -> dict[str, Any]:
    """Qualify ambiguity slots, then generate semantic interpretations.

    ``grounding_candidates`` may contain any raw detector K.  Only white vans
    that pass the existing plausibility contract and carry stable physical
    identity can affect ``qualified_anchor_K``.  Grounding rank never selects
    or creates a semantic interpretation.
    """

    normalized = _normalize(raw_instruction)
    is_turn_after_white_van = bool(
        re.search(r"\bturn\b", normalized)
        and re.search(r"\bafter\b", normalized)
        and re.search(r"\bwhite van\b", normalized)
    )
    qualified = [row for row in grounding_candidates if _is_qualified_white_van(row)]
    fixed_direction = _fixed_turn_direction(raw_instruction)
    direction_unresolved = bool(is_turn_after_white_van and fixed_direction is None)

    if not is_turn_after_white_van:
        ambiguity_type = "UNKNOWN_AMBIGUITY_TYPE"
        unresolved_slots: list[str] = []
        qualification = "NOT_QUALIFIED_UNSUPPORTED_INSTRUCTION"
        reason_code = "MINIMAL_V2_PARSER_SUPPORTS_TURN_AFTER_WHITE_VAN_ONLY"
    elif not qualified:
        ambiguity_type = "UNKNOWN_AMBIGUITY_TYPE"
        unresolved_slots = ["TURN_DIRECTION"] if direction_unresolved else []
        qualification = "NOT_QUALIFIED"
        reason_code = "NO_QUALIFIED_STABLE_WHITE_VAN_ANCHOR"
    elif len(qualified) >= 2 and direction_unresolved:
        ambiguity_type = "MULTI_SLOT_AMBIGUITY"
        unresolved_slots = ["ANCHOR_REFERENCE", "TURN_DIRECTION"]
        qualification = "EXCLUDED_FROM_PRIMARY_V2"
        reason_code = "REFERENCE_AND_DIRECTION_BOTH_UNRESOLVED"
    elif len(qualified) >= 2:
        ambiguity_type = "REFERENTIAL_AMBIGUITY"
        unresolved_slots = ["ANCHOR_REFERENCE"]
        qualification = "QUALIFIED_SECONDARY_ENGINEERING_CASE"
        reason_code = "MULTIPLE_INDEPENDENTLY_QUALIFIED_ANCHORS_DIRECTION_FIXED"
    elif direction_unresolved:
        ambiguity_type = "MANEUVER_DIRECTION_UNDERSPECIFIED"
        unresolved_slots = ["TURN_DIRECTION"]
        qualification = "QUALIFIED_PRIMARY_V2"
        reason_code = "EXACTLY_ONE_QUALIFIED_ANCHOR_DIRECTION_UNRESOLVED"
    else:
        ambiguity_type = "NO_AMBIGUITY"
        unresolved_slots = []
        qualification = "QUALIFIED_UNAMBIGUOUS"
        reason_code = "ANCHOR_AND_DIRECTION_RESOLVED"

    interpretations: list[dict[str, Any]] = []
    if ambiguity_type == "MANEUVER_DIRECTION_UNDERSPECIFIED":
        for index, direction in enumerate(direction_options):
            canonical_direction = str(direction).upper()
            if canonical_direction not in {"LEFT", "RIGHT"}:
                continue
            interpretations.append(
                _interpretation(
                    slot=chr(ord("A") + index),
                    ambiguity_type=ambiguity_type,
                    anchor=qualified[0],
                    direction=canonical_direction,
                    source_instruction=raw_instruction,
                    source_observation=source_observation,
                    world_identity=world_identity,
                    route_identity=route_identity,
                )
            )
    elif ambiguity_type == "REFERENTIAL_AMBIGUITY":
        for index, anchor in enumerate(qualified):
            interpretations.append(
                _interpretation(
                    slot=chr(ord("A") + index),
                    ambiguity_type=ambiguity_type,
                    anchor=anchor,
                    direction=str(fixed_direction),
                    source_instruction=raw_instruction,
                    source_observation=source_observation,
                    world_identity=world_identity,
                    route_identity=route_identity,
                    anchor_label="#{}".format(index + 1),
                )
            )
    elif ambiguity_type == "NO_AMBIGUITY":
        interpretations.append(
            _interpretation(
                slot="A",
                ambiguity_type=ambiguity_type,
                anchor=qualified[0],
                direction=str(fixed_direction),
                source_instruction=raw_instruction,
                source_observation=source_observation,
                world_identity=world_identity,
                route_identity=route_identity,
            )
        )

    before_collapse = len(interpretations)
    interpretations = collapse_semantic_duplicates(interpretations)
    if before_collapse > len(interpretations):
        qualification = "NOT_QUALIFIED_DUPLICATE_INTERPRETATIONS"
        reason_code = "SEMANTIC_INTERPRETATION_K_COLLAPSED"

    semantic_k = len(interpretations)
    return {
        "schema_version": "driveclarify.r4_3.ambiguity_ontology_v2.interpretation_set.v1",
        "raw_instruction": raw_instruction,
        "ambiguity_type": ambiguity_type,
        "unresolved_slots": unresolved_slots,
        "ambiguity_slot_count": len(unresolved_slots),
        "raw_grounding_K": len(grounding_candidates),
        "qualified_anchor_K": len(qualified),
        "semantic_interpretation_K_before_collapse": before_collapse,
        "semantic_interpretation_K": semantic_k,
        "candidate_forward_K": semantic_k,
        "candidate_forwards_executed": 0,
        "effective_K": semantic_k,
        "qualification_status": qualification,
        "reason_code": reason_code,
        "grounding_rank_controls_semantic_K": False,
        "interpretations": interpretations,
    }


__all__ = [
    "ONTOLOGY_TYPES",
    "canonical_sha256",
    "collapse_semantic_duplicates",
    "generate_semantic_interpretations",
]
