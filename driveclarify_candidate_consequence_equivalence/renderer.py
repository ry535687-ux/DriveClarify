"""Grounded-semantics renderer for the official Dreaming prompt envelope."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConsequenceAwareGroundedSemantic:
    relation: str
    referring_expression: str
    maneuver_direction: str
    route_order_index: int
    current_behavior: str
    persistent_target_id: str
    persistent_branch_id: str


class ConsequenceAwareOfficialDreamingRenderer:
    """Render language faithfully while leaving target identity structured.

    The output is free-form language inside the already-aligned official
    Dreaming envelope.  It contains no object ID, numeric point, GPS value, or
    scenario lookup.  A farther target remains in the structured semantic even
    when the current clause says to continue through the first opportunity.
    """

    implementation_id = "ConsequenceAwareOfficialDreamingRenderer.v1"

    def render(self, semantic: ConsequenceAwareGroundedSemantic) -> str:
        direction = semantic.maneuver_direction.strip().casefold()
        expression = semantic.referring_expression.strip()
        if not direction or not expression:
            raise ValueError("GROUNDED_RENDERER_REQUIRED_SEMANTIC_MISSING")
        if semantic.current_behavior == "TURN_AT_UPCOMING_OPPORTUNITY":
            return (
                "At the upcoming first junction, turn {} after the {}."
            ).format(direction, expression)
        if semantic.current_behavior == "CONTINUE_TO_LATER_OPPORTUNITY":
            return (
                "Continue straight through the upcoming first junction. At the "
                "following second junction, turn {} after the {}."
            ).format(direction, expression)
        raise ValueError(
            "UNSUPPORTED_GROUNDED_CURRENT_BEHAVIOR:" + semantic.current_behavior
        )


__all__ = [
    "ConsequenceAwareGroundedSemantic",
    "ConsequenceAwareOfficialDreamingRenderer",
]
