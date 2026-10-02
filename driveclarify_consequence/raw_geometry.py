"""Raw geometry diagnostics (CPU-only, pure Python, deterministic).

Computes RAW-unit geometry over model-local raw plans: point counts, raw route length, adjacent
spacing, terminal divergence, pairwise divergence. These are RAW diagnostics in MODEL_LOCAL_RAW /
RAW_UNIT — explicitly NOT metres, NOT physical safety consequences. No thresholds are frozen in CP2,
so a relation label is only ever LOW/MEDIUM/HIGH when a caller supplies frozen thresholds; otherwise
UNKNOWN. No torch/CUDA. Every function validates rank / point-dim / finiteness / emptiness first and
returns (value, None) on success or (None, reason_code) on any structural problem.

algorithm_version is embedded in outputs so replay hashes are stable across refactors.
"""

from __future__ import annotations

import math
from typing import Any

ALGORITHM_VERSION = "raw_geometry.v0"

# A raw plan tensor is [B][N][2]. We operate on the FIRST batch (B index 0) only — the single forward.
_POINT_DIM = 2


def _extract_points(raw: Any) -> tuple[list[list[float]] | None, str | None]:
    """Validate and extract [N][2] points from a [B][N][2] nested list. Returns (points, reason)."""
    if raw is None:
        return None, "GEOMETRY_MISSING"
    if not isinstance(raw, list) or not raw:
        return None, "GEOMETRY_EMPTY"
    batch = raw[0]
    if not isinstance(batch, list):
        return None, "GEOMETRY_BAD_RANK"
    if not batch:
        return None, "GEOMETRY_EMPTY"
    points: list[list[float]] = []
    for pt in batch:
        if not isinstance(pt, list) or len(pt) != _POINT_DIM:
            return None, "GEOMETRY_BAD_POINT_DIM"
        x, y = pt[0], pt[1]
        if isinstance(x, bool) or isinstance(y, bool):
            return None, "GEOMETRY_BAD_DTYPE"
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None, "GEOMETRY_BAD_DTYPE"
        if not math.isfinite(x) or not math.isfinite(y):
            return None, "GEOMETRY_NONFINITE"
        points.append([float(x), float(y)])
    return points, None


def route_point_count(raw: Any) -> tuple[int | None, str | None]:
    points, reason = _extract_points(raw)
    if reason:
        return None, reason
    return len(points), None


def _euclidean(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def raw_route_length(raw: Any) -> tuple[float | None, str | None]:
    """Sum of adjacent raw euclidean distances. Needs >= 2 points."""
    points, reason = _extract_points(raw)
    if reason:
        return None, reason
    if len(points) < 2:
        return None, "GEOMETRY_TOO_FEW_POINTS"
    total = 0.0
    for i in range(len(points) - 1):
        total += _euclidean(points[i], points[i + 1])
    return total, None


def raw_adjacent_spacing(raw: Any) -> tuple[dict[str, float] | None, str | None]:
    """Return {min,max,mean,count} of adjacent raw spacings. Needs >= 2 points."""
    points, reason = _extract_points(raw)
    if reason:
        return None, reason
    if len(points) < 2:
        return None, "GEOMETRY_TOO_FEW_POINTS"
    gaps = [_euclidean(points[i], points[i + 1]) for i in range(len(points) - 1)]
    n = len(gaps)
    return {"min": min(gaps), "max": max(gaps), "mean": sum(gaps) / n, "count": n}, None


def plan_continuity(raw: Any, max_gap: float | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """Report raw continuity as {max_adjacent_gap, monotone_index, n_points}. If a frozen max_gap is
    supplied, also report continuous=bool; otherwise continuous stays None (no invented threshold)."""
    points, reason = _extract_points(raw)
    if reason:
        return None, reason
    if len(points) < 2:
        return {"max_adjacent_gap": 0.0, "n_points": len(points), "continuous": None}, None
    gaps = [_euclidean(points[i], points[i + 1]) for i in range(len(points) - 1)]
    max_g = max(gaps)
    continuous = None
    if isinstance(max_gap, (int, float)):
        continuous = bool(max_g <= float(max_gap))
    return {"max_adjacent_gap": max_g, "n_points": len(points), "continuous": continuous}, None


def terminal_divergence_raw(raw_a: Any, raw_b: Any) -> tuple[float | None, str | None]:
    """Raw L2 between the two candidates' terminal (last) points. Same-frame diagnostic only."""
    pa, ra = _extract_points(raw_a)
    if ra:
        return None, ra
    pb, rb = _extract_points(raw_b)
    if rb:
        return None, rb
    return _euclidean(pa[-1], pb[-1]), None


def pairwise_route_divergence_raw(raw_a: Any, raw_b: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Per-index raw L2 between two candidates over the OVERLAPPING index range.

    Returns {mean,max,max_index,compared_points,algorithm_version}. This is a raw same-frame
    diagnostic — NEVER a safety/task divergence. Requires both plans have >= 1 comparable point.
    """
    pa, ra = _extract_points(raw_a)
    if ra:
        return None, ra
    pb, rb = _extract_points(raw_b)
    if rb:
        return None, rb
    k = min(len(pa), len(pb))
    if k == 0:
        return None, "GEOMETRY_NO_OVERLAP"
    dists = [_euclidean(pa[i], pb[i]) for i in range(k)]
    max_index = max(range(k), key=lambda i: dists[i])
    return {
        "mean": sum(dists) / k,
        "max": max(dists),
        "max_index": max_index,
        "compared_points": k,
        "algorithm_version": ALGORITHM_VERSION,
    }, None


def raw_geometry_relation(value: float, thresholds: dict[str, float] | None) -> str:
    """Map a raw divergence to LOW/MEDIUM/HIGH ONLY if frozen thresholds are supplied; else UNKNOWN.

    CP2 froze NO thresholds, so the offline replay passes thresholds=None and this returns UNKNOWN.
    """
    if not thresholds or "medium" not in thresholds or "high" not in thresholds:
        return "UNKNOWN"
    if value >= thresholds["high"]:
        return "HIGH"
    if value >= thresholds["medium"]:
        return "MEDIUM"
    return "LOW"
