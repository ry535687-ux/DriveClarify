"""Stage 6A static compiler for the frozen DriveClarify paper-MVP scenarios.

The package is intentionally CARLA-free at import time.  It produces the exact
XML shape consumed by the pinned Bench2Drive ``RouteParser`` while keeping all
unverified blueprint and semantic bindings behind explicit live-resolution
gates.
"""

from .compiler import DEFAULT_OUTPUT_DIR, compile_scenarios
from .live_contracts import DEFAULT_LIVE_PROMOTION_DIR
from .validator import (
    ScenarioFixtureValidationError,
    validate_generated,
    validate_live_promotions,
)

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_LIVE_PROMOTION_DIR",
    "ScenarioFixtureValidationError",
    "compile_scenarios",
    "validate_generated",
    "validate_live_promotions",
]
