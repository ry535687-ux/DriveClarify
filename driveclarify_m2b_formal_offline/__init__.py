"""Formal M1 to M2B offline diagnostic integration.

This package is intentionally non-authorizing.  It adapts frozen TRAIN/DEV
evidence into the existing :mod:`driveclarify_decision` v0 contracts; it does
not replace or change that policy.
"""

from .integration import (
    ADAPTER_VERSION,
    PROTOCOL_VERSION,
    build_adapter_units,
    build_operating_profiles,
    build_runtime_cases,
    run_frozen_learned_diagnostics,
)

__all__ = [
    "ADAPTER_VERSION",
    "PROTOCOL_VERSION",
    "build_adapter_units",
    "build_operating_profiles",
    "build_runtime_cases",
    "run_frozen_learned_diagnostics",
]
