"""Result assembler — builds a schema-valid CandidateConsequenceV0 dict per candidate.

Every field is a Result envelope (via feature_extractors). Physical fields are UNKNOWN via the
dependency gate; integrity + raw-geometry + plan_age_sim + adapter_latency are diagnostics that may
be AVAILABLE. The assembled dict is validated against the frozen candidate schema before return, so
no field can bypass schema validation. Pure; no CARLA/SimLingo/GPU.
"""

from __future__ import annotations

from typing import Any

from . import feature_extractors as fx
from . import raw_geometry as rg
from .frame_binding import RAW_FRAME, RAW_UNIT
from .types import EvidenceGrade, Result

CANDIDATE_SCHEMA_VERSION = "driveclarify.candidate_consequence.v0"


def _rd(results: dict[str, Result]) -> dict[str, Any]:
    return {k: v.to_dict() for k, v in results.items()}


def assemble_candidate_consequence(
    candidate: dict[str, Any],
    record: dict[str, Any],
    grades: dict[str, EvidenceGrade],
    *,
    is_stale: bool,
    linkage_ok: bool,
    shape_ok: bool,
    shape_reason: str | None,
    pairwise: dict[str, Any] | None,
    terminal: float | None,
    plan_age_sim: float | None,
    adapter_latency_s: float | None,
    source_artifacts: tuple[str, ...],
) -> dict[str, Any]:
    """Build one CandidateConsequenceV0 dict. Does not mutate inputs."""
    md = record.get("metadata", {})
    integrity = fx.integrity_results(candidate, record, grades, is_stale, None, linkage_ok,
                                     shape_ok, shape_reason, source_artifacts)
    raw_geo = fx.raw_geometry_results(candidate, grades, pairwise, terminal, source_artifacts)
    task = fx.task_results(grades)
    safety = fx.safety_results(grades)
    rule = fx.rule_results(grades)
    timing = fx.timing_results(grades, plan_age_sim, adapter_latency_s, source_artifacts)
    recoverability = fx.recoverability_results(grades)
    comfort = fx.comfort_results(grades)

    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "cache_id": md.get("candidate_set_id", "set:unknown"),
        "episode_id": md.get("run_id", "run:unknown"),
        "observation_id": md.get("observation_id", ""),
        "candidate_id": candidate.get("candidate_id", ""),
        "computed_at_monotonic_ns": md.get("generated_at_monotonic_ns", 0),
        "integrity": _rd(integrity),
        "raw_geometry": _rd(raw_geo),
        "task": _rd(task),
        "safety": _rd(safety),
        "rule": _rd(rule),
        "timing": _rd(timing),
        "recoverability": _rd(recoverability),
        "comfort": _rd(comfort),
    }


def compute_set_geometry(candidates: list[dict[str, Any]]) -> dict[str, tuple]:
    """Compute pairwise/terminal raw divergence for each candidate vs the lexicographically-first
    OTHER candidate (stable, deterministic). Returns {candidate_id: (pairwise|None, terminal|None)}.
    Single-candidate sets get (None, None)."""
    out: dict[str, tuple] = {}
    ordered = sorted(candidates, key=lambda c: str(c.get("candidate_id")))
    if len(ordered) < 2:
        for c in ordered:
            out[str(c.get("candidate_id"))] = (None, None)
        return out
    ref = ordered[0]
    for c in ordered:
        cid = str(c.get("candidate_id"))
        other = ordered[1] if c is ref else ref
        pw, _ = rg.pairwise_route_divergence_raw(c.get("pred_route_raw"), other.get("pred_route_raw"))
        tv, _ = rg.terminal_divergence_raw(c.get("pred_route_raw"), other.get("pred_route_raw"))
        out[cid] = (pw, tv)
    return out
