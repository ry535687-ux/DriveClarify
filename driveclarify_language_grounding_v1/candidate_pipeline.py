"""Scene-conditioned ambiguity discovery and grounded candidate construction.

Two semantic axes live here, and which one a given instruction uses is decided by
the words alone:

* **Referential axis** — the unresolved slot is *which object*, so each distinct
  grounded referent identity is itself a distinct reading and the rows enumerate
  referents.  This is the historical behavior and is unchanged.
* **Semantic-constraint axis** — the referent resolves uniquely but some other
  slot (turn direction, execution location) is unstated, so the rows enumerate
  the *language-admissible values of that slot* and every row shares the one
  grounded referent.

The second axis is why one white van can still yield K=2: a shared anchor is not
a shared meaning.  In both cases the row count is a language fact.  No map,
topology, junction, branch or opportunity value is read in this module, so the
scene can qualify or reject a row downstream but can never create one.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from .contracts import (
    AmbiguityKind,
    AmbiguityStatus,
    CandidateSetResult,
    GroundedCandidate,
    GroundingResult,
    ParsedSlots,
    VisualReferentCandidate,
    canonical_sha256,
)


# A semantic authority is any callable mapping parsed slots to
# ``(obligation_type, value_space, semantic_source)`` or None.  It is injected
# rather than imported at module scope so this module keeps importing nothing
# from the scene/obligation layer, and so tests can supply their own.
SemanticAuthority = Callable[
    [Mapping[str, Any]], Optional[Tuple[str, Tuple[str, ...], str]]
]

# Gate for resolving the *production* authority when none is injected.  Same flag
# the runtime arbitration seam already uses, so a single switch governs the whole
# R4.4 semantic path and the default-off behavior is byte-identical to before.
PRODUCTION_SEMANTIC_AUTHORITY_FLAG = "DRIVECLARIFY_R4_4_SCENE_GROUNDED_LOCAL_NAVIGATION"

# ``TURN`` admits a left or a right completion only.  STRAIGHT is excluded on
# this repository's own contract, not on taste: the slot parser routes
# "go straight"/"continue"/"proceed" to maneuver CONTINUE rather than TURN, and
# the existing topology enumerator classifies a sub-35-degree yaw delta as not a
# turn at all.  A STRAIGHT "turn" is therefore not a reading of TURN in this
# ontology, and per fail-closed policy it is dropped rather than kept to inflate
# K.  The language-layer value space itself is left untouched; this is an
# admissibility filter applied at candidate construction.
TURN_ADMISSIBLE_DIRECTIONS = ("LEFT", "RIGHT")


def _truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _production_semantic_authority() -> Optional[SemanticAuthority]:
    """Resolve the production authority lazily, or None when unavailable/off."""

    if not _truthy(os.environ.get(PRODUCTION_SEMANTIC_AUTHORITY_FLAG)):
        return None
    try:  # noqa: PLC0415 - lazy on purpose: no import-time layer coupling
        from driveclarify_scene_grounded_obligation_supplier.interpretations import (
            language_semantic_authority,
        )
    except ImportError:
        return None
    return language_semantic_authority


def _admissible_values(
    obligation_type: str, value_space: Sequence[str], maneuver: Optional[str]
) -> Tuple[str, ...]:
    """Drop values the instruction's own maneuver cannot semantically admit."""

    values = tuple(str(value) for value in value_space)
    if obligation_type == "MANEUVER_DIRECTION" and (maneuver or "").upper() == "TURN":
        return tuple(value for value in values if value in TURN_ADMISSIBLE_DIRECTIONS)
    return values


def _referring_expression(
    phrase: str,
    referent: VisualReferentCandidate,
    all_referents: Sequence[VisualReferentCandidate],
) -> str:
    parts = []
    if len(all_referents) > 1:
        if referent.apparent_size_rank == 1:
            parts.append("visually nearer")
        elif referent.apparent_size_rank == len(all_referents):
            parts.append("visually farther")
        else:
            parts.append("middle-distance")
    parts.append(phrase)
    if referent.relative_image_location == "LEFT":
        parts.append("on the left")
    elif referent.relative_image_location == "RIGHT":
        parts.append("on the right")
    else:
        parts.append("ahead near the center")
    return " ".join(parts)


_DIRECTION_WORD = {"LEFT": "left", "RIGHT": "right", "STRAIGHT": "straight"}
_ORDINAL_WORD = {
    "FIRST": "first",
    "SECOND": "second",
    "THIRD": "third",
    "NEAREST": "nearest",
    "FARTHEST": "farthest",
}


def _render_prompt(
    slots: ParsedSlots,
    expression: str,
    *,
    obligation_type: Optional[str] = None,
    semantic_constraint: Optional[str] = None,
) -> str:
    verb = {
        "TURN": "Turn",
        "STOP": "Stop",
        "PULL_OVER": "Pull over",
        "CONTINUE": "Continue",
    }.get(slots.maneuver or "", slots.raw_instruction.strip().rstrip(".?!"))
    # The unresolved slot's chosen value must reach the surface form, otherwise
    # two distinct readings would render to one prompt and collapse downstream.
    if obligation_type == "MANEUVER_DIRECTION" and semantic_constraint:
        verb = "{} {}".format(verb, _DIRECTION_WORD.get(semantic_constraint, semantic_constraint.casefold()))
    elif obligation_type == "EXECUTION_LOCATION" and semantic_constraint:
        word = _ORDINAL_WORD.get(semantic_constraint, semantic_constraint.casefold())
        return "{} at the {} opportunity after the {}.".format(verb, word, expression)
    if slots.temporal_relation == "AFTER":
        return "{} after the {}.".format(verb, expression)
    if slots.temporal_relation == "BEFORE":
        return "{} before the {}.".format(verb, expression)
    if slots.temporal_relation == "UNTIL_CLEAR":
        return "Wait until the {} has fully cleared, then {}.".format(expression, verb.casefold())
    if slots.spatial_relation in {"NEAR", "AT"}:
        return "{} near the {}.".format(verb, expression)
    return "{} with respect to the {}.".format(verb, expression)


class GroundedCandidatePipeline:
    """Preserve referent/event identity independently of maneuver and trajectory."""

    def construct(
        self,
        parsed: ParsedSlots,
        grounding: GroundingResult,
        *,
        semantic_authority: Optional[SemanticAuthority] = None,
    ) -> CandidateSetResult:
        phrase = parsed.referent_phrase or parsed.landmark_phrase
        if (
            phrase is None
            or parsed.ambiguity_kind
            not in {AmbiguityKind.REFERENTIAL, AmbiguityKind.LANDMARK, AmbiguityKind.TEMPORAL}
        ):
            return CandidateSetResult(
                status=AmbiguityStatus.UNKNOWN,
                parsed_slots=parsed,
                grounding=grounding,
                raw_candidates=(),
                candidates=(),
                raw_k=0,
                effective_k=0,
                semantic_duplicate=False,
                grounding_duplicate=False,
                prompt_duplicate=False,
                reason_codes=("AMBIGUITY_ROUTED_AWAY_FROM_VISUAL_CANDIDATE_CONSTRUCTION",),
            )
        axis, values, obligation_type, semantic_source = self._semantic_axis(
            parsed, grounding, semantic_authority
        )
        if axis == "SEMANTIC_CONSTRAINT":
            anchor = grounding.selected_referents[0]
            raw = tuple(
                self._candidate(
                    parsed,
                    phrase,
                    anchor,
                    grounding.selected_referents,
                    index,
                    obligation_type=obligation_type,
                    semantic_constraint=value,
                    semantic_source=semantic_source,
                )
                for index, value in enumerate(values, start=1)
            )
            axis_reason = "SEMANTIC_CONSTRAINT_AXIS_ONE_ROW_PER_LANGUAGE_HYPOTHESIS"
        else:
            raw = tuple(
                self._candidate(parsed, phrase, referent, grounding.selected_referents, index)
                for index, referent in enumerate(grounding.selected_referents, start=1)
            )
            axis_reason = "REFERENTIAL_AXIS_ONE_ROW_PER_GROUNDED_REFERENT"
        semantic_duplicate = len({item.semantic_sha256 for item in raw}) < len(raw)
        # Evidence only.  On the semantic-constraint axis every row shares the one
        # grounded anchor by design, so a shared anchor must never be read as a
        # duplicate candidate: collapse is decided by semantic identity alone.
        grounding_duplicate = len({item.grounded_referent_id for item in raw}) < len(raw)
        prompt_duplicate = len({item.prompt_sha256 for item in raw}) < len(raw)
        seen = set()
        effective = []
        for candidate in raw:
            key = candidate.semantic_sha256
            if key in seen:
                continue
            seen.add(key)
            effective.append(candidate)
        if len(effective) < len(raw):
            status = AmbiguityStatus.CANDIDATE_COLLAPSED
            reasons = ("SEMANTIC_IDENTITY_DUPLICATE", axis_reason)
        elif len(effective) == 0:
            status = grounding.status
            reasons = grounding.reason_codes
        elif len(effective) == 1:
            status = AmbiguityStatus.SINGLE_REFERENT
            reasons = ("NO_REFERENTIAL_AMBIGUITY", axis_reason)
        elif prompt_duplicate:
            status = AmbiguityStatus.SIMLINGO_BINDING_COLLAPSE
            reasons = ("DISTINCT_GROUNDING_RENDERED_TO_DUPLICATE_PROMPT", axis_reason)
        else:
            status = (
                AmbiguityStatus.K_EXCEEDS_V1
                if grounding.status is AmbiguityStatus.K_EXCEEDS_V1
                else AmbiguityStatus.AMBIGUITY_DETECTED
            )
            reasons = ("GROUNDED_SEMANTIC_K2_PRESERVED", axis_reason)
        return CandidateSetResult(
            status=status,
            parsed_slots=parsed,
            grounding=grounding,
            raw_candidates=raw,
            candidates=tuple(effective),
            raw_k=len(raw),
            effective_k=len(effective),
            semantic_duplicate=semantic_duplicate,
            grounding_duplicate=grounding_duplicate,
            prompt_duplicate=prompt_duplicate,
            reason_codes=reasons,
        )

    @staticmethod
    def _semantic_axis(
        parsed: ParsedSlots,
        grounding: GroundingResult,
        injected: Optional[SemanticAuthority],
    ) -> Tuple[str, Tuple[str, ...], Optional[str], Optional[str]]:
        """Pick the axis the rows enumerate along, from the words only.

        The semantic-constraint axis is used exactly when the words leave a
        non-referent slot open *and* the referent resolved uniquely, because only
        then is "which value" the open question.  Two or more grounded referents
        together with an unstated direction is a multi-slot ambiguity, which this
        version does not support, so it fails closed to the referential axis
        rather than guessing which slot the speaker meant.
        """

        authority = injected or _production_semantic_authority()
        if authority is None:
            return ("REFERENTIAL", (), None, None)
        if len(grounding.selected_referents) != 1:
            return ("REFERENTIAL", (), None, None)
        resolved = authority(parsed.to_dict())
        if not resolved:
            return ("REFERENTIAL", (), None, None)
        obligation_type, value_space, semantic_source = resolved
        values = _admissible_values(str(obligation_type), value_space, parsed.maneuver)
        # A single admissible value means the slot is effectively stated: there is
        # no second reading, so the referential axis (K=1) remains correct.
        if len(values) < 2:
            return ("REFERENTIAL", (), None, None)
        return ("SEMANTIC_CONSTRAINT", values, str(obligation_type), str(semantic_source))

    @staticmethod
    def _candidate(
        slots: ParsedSlots,
        phrase: str,
        referent: VisualReferentCandidate,
        referents: Sequence[VisualReferentCandidate],
        rank: int,
        *,
        obligation_type: Optional[str] = None,
        semantic_constraint: Optional[str] = None,
        semantic_source: Optional[str] = None,
    ) -> GroundedCandidate:
        expression = _referring_expression(phrase, referent, referents)
        prompt = _render_prompt(
            slots,
            expression,
            obligation_type=obligation_type,
            semantic_constraint=semantic_constraint,
        )
        relation = slots.temporal_relation or slots.spatial_relation
        target_event = (
            referent.grounding_identity + ":CLEARED"
            if slots.temporal_relation in {"AFTER", "UNTIL_CLEAR"}
            else None
        )
        projection = {
            "maneuver": slots.maneuver,
            "referent_phrase": phrase,
            "grounded_referent_id": referent.grounding_identity,
            "relation": relation,
            "target_event": target_event,
            "target_landmark": (
                referent.grounding_identity if slots.ambiguity_kind is AmbiguityKind.LANDMARK else None
            ),
            "target_branch": None,
            "ordering": slots.ordering or referent.apparent_size_rank,
            "constraint": slots.constraint,
        }
        if obligation_type is not None or semantic_constraint is not None:
            # Present only on the semantic-constraint axis, so referential rows
            # keep their exact historical digest.  This is what makes two readings
            # of the same referent carry two distinct semantic identities.
            projection["obligation_type"] = obligation_type
            projection["semantic_constraint"] = semantic_constraint
        semantic_sha = canonical_sha256(projection)
        grounding_sha = canonical_sha256(
            {
                "identity": referent.grounding_identity,
                "bbox": referent.bbox_xyxy,
                "frame_id": referent.frame_id,
                "image_sha256": referent.image_sha256,
            }
        )
        candidate_id = "lgv1-cand-" + canonical_sha256(
            {
                "semantic": semantic_sha,
                "observation_id": referent.observation_id,
                # On the semantic-constraint axis the constraint already
                # distinguishes the rows, so enumeration position is excluded and
                # identity cannot move when an upstream ordering changes.
                "rank": None if semantic_constraint is not None else rank,
            }
        )[:20]
        return GroundedCandidate(
            candidate_id=candidate_id,
            interpretation_id="lgv1-meaning-" + semantic_sha[:20],
            raw_instruction=slots.raw_instruction,
            prompt_text=prompt,
            referring_expression=expression,
            maneuver=slots.maneuver or "UNKNOWN",
            referent_phrase=phrase,
            grounded_referent_id=referent.grounding_identity,
            relation=relation,
            target_event=target_event,
            target_landmark=(
                referent.grounding_identity if slots.ambiguity_kind is AmbiguityKind.LANDMARK else None
            ),
            target_branch=None,
            ordering=slots.ordering or (
                "APPARENT_NEARER" if referent.apparent_size_rank == 1 else "APPARENT_FARTHER"
            ),
            constraint=slots.constraint,
            source_observation_id=referent.observation_id,
            source_frame_id=referent.frame_id,
            image_sha256=referent.image_sha256,
            semantic_sha256=semantic_sha,
            grounding_sha256=grounding_sha,
            prompt_sha256=canonical_sha256(prompt),
            status="VALID",
            obligation_type=obligation_type,
            semantic_constraint=semantic_constraint,
            semantic_source=semantic_source,
            reason_codes=(
                ("IMAGE_ONLY_GROUNDED_CANDIDATE", "TARGET_BRANCH_NOT_INFERRED_FROM_IMAGE_BOX")
                + (
                    ("LANGUAGE_PROPOSED_SEMANTIC_HYPOTHESIS_SCENE_MAY_ONLY_QUALIFY",)
                    if semantic_constraint is not None
                    else ()
                )
            ),
        )


__all__ = ["GroundedCandidatePipeline"]

