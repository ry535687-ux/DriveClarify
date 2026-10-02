"""Default-off Language / Scene Grounding V1 research branch."""

from .candidate_pipeline import GroundedCandidatePipeline
from .contracts import (
    AmbiguityKind,
    AmbiguityStatus,
    GroundedCandidate,
    GroundingResult,
    ParsedSlots,
    VisualReferentCandidate,
)
from .slot_parser import SemanticSlotParser
from .visual_grounder import GroundingDinoVisualGrounder

__all__ = [
    "AmbiguityKind",
    "AmbiguityStatus",
    "GroundedCandidate",
    "GroundedCandidatePipeline",
    "GroundingDinoVisualGrounder",
    "GroundingResult",
    "ParsedSlots",
    "SemanticSlotParser",
    "VisualReferentCandidate",
]
