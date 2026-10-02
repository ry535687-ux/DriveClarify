"""Generic semantic-to-official-style language renderer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GroundedCandidateSemantic:
    relation: str
    referent_description: str
    relative_order: str
    local_maneuver: str
    target_semantic_descriptor: str


class OfficialDreamingInstructionRenderer:
    """Render current executable behavior without numeric target conditioning."""

    implementation_id = "OfficialDreamingInstructionRenderer.v1"
    provenance = {
        "RIGHT": "released Eval_Dreamer corpus: Move one lane towards the right.",
        "STRAIGHT": "released Eval_Dreamer corpus: Continue driving on your current lane.",
    }
    _templates = {
        "RIGHT": "Move one lane towards the right.",
        "STRAIGHT": "Continue driving on your current lane.",
    }

    def render(self, semantic: GroundedCandidateSemantic) -> str:
        maneuver = str(semantic.local_maneuver).strip().upper()
        if maneuver not in self._templates:
            raise ValueError("UNSUPPORTED_OFFICIAL_DREAMING_LOCAL_MANEUVER:" + maneuver)
        # Target/referent identity stays in structured provenance.  The short-
        # horizon model receives only the currently executable semantic action.
        return self._templates[maneuver]
