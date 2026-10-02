"""Optional, deterministic ambiguity-discovery prototype v0.

This package is deliberately outside the Paper MVP runtime.  Importing it does not
start a model, simulator, evaluator, or authority path.
"""

from .contracts import CandidateInterpretation, DiscoveryResult, EnvironmentEntity
from .discovery import AmbiguityDiscoveryPipeline, discover_ambiguity

__all__ = [
    "AmbiguityDiscoveryPipeline",
    "CandidateInterpretation",
    "DiscoveryResult",
    "EnvironmentEntity",
    "discover_ambiguity",
]

