"""Input Contract Validator + binding invariants (CP2 skeleton).

Validates a ConsequenceAdapterInputV0 dict against structural rules and the binding invariants
from CONSEQUENCE_ADAPTER_INPUT_CONTRACT.md. Pure; no CARLA/model. Returns a list of violations
(empty = valid). Does not mutate the input.
"""

from __future__ import annotations

from typing import Any

INPUT_SCHEMA_VERSION = "driveclarify.consequence_adapter_input.v0"

_SIM_FIELDS = {"snapshot_elapsed_seconds", "gametime_elapsed_seconds"}
_MONOTONIC_FIELDS = {"monotonic_ns", "generated_at_monotonic_ns"}


def validate_input(record: dict[str, Any]) -> list[str]:
    """Return a list of violation codes; empty means the record satisfies the v0 contract."""
    v: list[str] = []
    md = record.get("metadata")
    if not isinstance(md, dict):
        return ["MISSING_METADATA"]
    if md.get("schema_version") != INPUT_SCHEMA_VERSION:
        v.append("BAD_SCHEMA_VERSION")
    obs = md.get("observation_id")
    if not obs:
        v.append("MISSING_OBSERVATION_ID")

    # binding invariant 1 + 2: every candidate binds to the shared observation
    cands = record.get("candidate_plans")
    if not isinstance(cands, list) or not cands:
        v.append("MISSING_CANDIDATE_PLANS")
    else:
        for i, c in enumerate(cands):
            if c.get("source_observation_id") != obs:
                v.append(f"CANDIDATE_{i}_OBSERVATION_MISMATCH")
            if "stale_after" not in c:
                v.append(f"CANDIDATE_{i}_MISSING_STALE_AFTER")

    # clock domains must be present as tagged fields (invariant 7)
    clk = record.get("clock_bundle")
    if not isinstance(clk, dict):
        v.append("MISSING_CLOCK_BUNDLE")
    else:
        if clk.get("clock_policy") != "driveclarify.time_policy.v0":
            v.append("BAD_CLOCK_POLICY")

    # contract snapshot present (invariant 8)
    if not isinstance(record.get("contract_snapshot"), dict):
        v.append("MISSING_CONTRACT_SNAPSHOT")
    return v


def candidates_comparable(record: dict[str, Any]) -> bool:
    """Invariant 2: candidates are comparable only if they share observation_id AND snapshot frame.

    Here all candidates share one input record's observation/frame by construction, so this checks
    that no candidate declared a mismatched source_observation_id.
    """
    md = record.get("metadata", {})
    obs = md.get("observation_id")
    cands = record.get("candidate_plans", [])
    return bool(cands) and all(c.get("source_observation_id") == obs for c in cands)


def assert_no_cross_domain_time(op_left_field: str, op_right_field: str) -> None:
    """Reject a time operation that mixes SIM and MONOTONIC domains (TIME_POLICY_V0).

    Raises ValueError('TIME_CROSS_DOMAIN_FORBIDDEN') if one operand is a sim field and the other is
    monotonic. Same-domain operations pass.
    """
    left_sim = op_left_field in _SIM_FIELDS
    right_sim = op_right_field in _SIM_FIELDS
    left_mono = op_left_field in _MONOTONIC_FIELDS
    right_mono = op_right_field in _MONOTONIC_FIELDS
    if (left_sim and right_mono) or (left_mono and right_sim):
        raise ValueError("TIME_CROSS_DOMAIN_FORBIDDEN")
