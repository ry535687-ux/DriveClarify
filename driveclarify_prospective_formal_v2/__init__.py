"""Prospective formal-v2 integrity boundary.

This package is deliberately policy-agnostic.  It protects the existing
production Stage6A/Stage6B decision owner from evaluator and oracle fields and
provides the physical-grounding eligibility predicate used before any formal
CARLA exposure.
"""

from .integrity import (
    AuditedOracleProvider,
    FormalIntegrityError,
    assert_arm_wiring,
    assert_family_scene_diversity,
    assert_geometry_report_matches,
    assert_grounding_eligible,
    assert_runtime_config_clean,
)

__all__ = [
    "AuditedOracleProvider",
    "FormalIntegrityError",
    "assert_arm_wiring",
    "assert_family_scene_diversity",
    "assert_geometry_report_matches",
    "assert_grounding_eligible",
    "assert_runtime_config_clean",
]
