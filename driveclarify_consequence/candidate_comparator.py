"""Candidate comparator — six separate dimensions, no weighted scalar (CANDIDATE_COMPARISON_POLICY_V0).

Produces SHADOW reason proposals only; never authorizes control. Raw geometric divergence is a
diagnostic and CANNOT upgrade task/safety/rule/timing equivalence out of UNKNOWN. In v0 every hard
dimension is UNKNOWN, so the only possible proposal is FALLBACK_INSUFFICIENT_EVIDENCE. Pure.
"""

from __future__ import annotations

from typing import Any

from .types import TriValue


def compare_candidate_set(consequences: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the six-dimension comparison + shadow reason proposal for a candidate set.

    ``consequences`` are assembled CandidateConsequenceV0 dicts. Geometric equivalence reads the raw
    diagnostic; all other dimensions are UNKNOWN in v0 because their physical Results are UNKNOWN.
    """
    n = len(consequences)
    # 1. Geometric equivalence: diagnostic-only. UNKNOWN relation (no frozen thresholds in v0).
    geometric = _geometric_relation(consequences)
    # 2-5. task / safety / rule / timing equivalence: UNKNOWN (physical fields UNKNOWN).
    task_equiv = TriValue.UNKNOWN
    safety_equiv = TriValue.UNKNOWN
    rule_equiv = TriValue.UNKNOWN
    timing_equiv = TriValue.UNKNOWN
    # 6. overall consequence equivalence: three-valued AND; UNKNOWN if any hard dim UNKNOWN.
    overall = TriValue.UNKNOWN

    proposal = "FALLBACK_INSUFFICIENT_EVIDENCE"
    return {
        "candidate_count": n,
        "geometric_equivalence": geometric,          # diagnostic relation label (or UNKNOWN)
        "task_equivalence": task_equiv.value,
        "safety_equivalence": safety_equiv.value,
        "rule_equivalence": rule_equiv.value,
        "timing_equivalence": timing_equiv.value,
        "consequence_equivalence": overall.value,
        "shadow_reason_proposal": proposal,
    }


def _geometric_relation(consequences: list[dict[str, Any]]) -> dict[str, Any]:
    """Report the raw terminal divergence diagnostic across the set (relation UNKNOWN: no thresholds)."""
    values: list[float] = []
    for c in consequences:
        td = c.get("raw_geometry", {}).get("terminal_divergence_raw", {})
        if td.get("status") == "AVAILABLE" and isinstance(td.get("value"), (int, float)):
            values.append(float(td["value"]))
    if not values:
        return {"raw_terminal_divergence_max": None, "relation": "UNKNOWN", "unit": "RAW_UNIT"}
    return {
        "raw_terminal_divergence_max": max(values),
        "relation": "UNKNOWN",  # CP2 froze NO thresholds; never invent one
        "unit": "RAW_UNIT",
    }
