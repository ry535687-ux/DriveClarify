"""DriveClarify Consequence Adapter — CP2 design-freeze skeleton.

DESIGN FREEZE ONLY. This package defines types, does schema validation, and propagates
UNKNOWN. It does NOT implement any real risk algorithm (no TTC, no collision prediction, no
rule evaluation, no camera projection, no control). Every physical-consequence field is
UNKNOWN-preserving and fail-closed. No CARLA / SimLingo / GPU import; pure CPU, pure functions.
"""

from __future__ import annotations

SCHEMA_INPUT = "driveclarify.consequence_adapter_input.v0"
SCHEMA_CANDIDATE_CONSEQUENCE = "driveclarify.candidate_consequence.v0"
SCHEMA_ADAPTER_STATE_MACHINE = "driveclarify.adapter_state_machine_input.v0"

from .types import UsagePurpose  # noqa: E402

__all__ = [
    "SCHEMA_INPUT",
    "SCHEMA_CANDIDATE_CONSEQUENCE",
    "SCHEMA_ADAPTER_STATE_MACHINE",
    "UsagePurpose",
]
