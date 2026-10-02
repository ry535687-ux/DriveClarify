"""Time binding — enforces TIME_POLICY_V0. Tags every time value with its clock domain and rejects
cross-domain operations. The 0.9 s sim offset is RECORDED, never auto-subtracted.

Three domains, never merged: SIM (snapshot elapsed = primary, gametime = reference) and MONOTONIC.
Pure; no CARLA/SimLingo/GPU.
"""

from __future__ import annotations

from typing import Any

from .contracts import assert_no_cross_domain_time  # re-exported cross-domain guard

CLOCK_POLICY = "driveclarify.time_policy.v0"

SIM = "SIM"
MONOTONIC = "MONOTONIC"

# Field -> clock domain (mirrors TIME_POLICY_V0 sources).
_DOMAIN_OF: dict[str, str] = {
    "snapshot_elapsed_seconds": SIM,
    "gametime_elapsed_seconds": SIM,
    "monotonic_ns": MONOTONIC,
    "generated_at_monotonic_ns": MONOTONIC,
}


def domain_of(field: str) -> str | None:
    return _DOMAIN_OF.get(field)


def cross_domain(left_field: str, right_field: str) -> bool:
    """True if the two fields live in different clock domains (SIM vs MONOTONIC)."""
    ld = domain_of(left_field)
    rd = domain_of(right_field)
    return ld is not None and rd is not None and ld != rd


def plan_age_sim_seconds(record: dict[str, Any], generated_at_snapshot_elapsed: float | None) -> float | None:
    """Compute plan age in the SIM domain ONLY (snapshot_elapsed now - snapshot_elapsed at gen).

    Returns None if either operand is missing. NEVER subtracts a monotonic value from a sim value.
    This is a same-domain (SIM) subtraction; callers must supply the generation-time snapshot
    elapsed from the SAME sim source.
    """
    now = record.get("clock_bundle", {}).get("snapshot_elapsed_seconds")
    if not isinstance(now, (int, float)) or not isinstance(generated_at_snapshot_elapsed, (int, float)):
        return None
    return float(now) - float(generated_at_snapshot_elapsed)


def observed_sim_offset(record: dict[str, Any]) -> float | None:
    """Return the RECORDED snapshot-gametime offset (diagnostic). Never auto-applied."""
    cb = record.get("clock_bundle", {})
    return cb.get("observed_sim_offset_seconds")


__all__ = [
    "CLOCK_POLICY", "SIM", "MONOTONIC", "domain_of", "cross_domain",
    "plan_age_sim_seconds", "observed_sim_offset", "assert_no_cross_domain_time",
]
