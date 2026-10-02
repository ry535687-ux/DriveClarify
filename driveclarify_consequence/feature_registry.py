"""Feature registry — the single source of truth for per-feature dependencies, units, and
allowed usage purposes, loaded verbatim from the FROZEN feature-dependency matrix.

This module NEVER re-declares the matrix data inline; it reads
``reports/consequence_adapter_v0/FEATURE_DEPENDENCY_MATRIX.json`` (append-only frozen artifact) so
the offline replay pipeline and the CP2 gate stay in lockstep. Pure; no CARLA/SimLingo/GPU.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .types import UsagePurpose

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPO_ROOT / "reports" / "consequence_adapter_v0" / "FEATURE_DEPENDENCY_MATRIX.json"


@dataclass(frozen=True)
class FeatureSpec:
    """One row of the frozen feature-dependency matrix (read-only)."""

    feature: str
    category: str
    dependencies: tuple[str, ...]
    output_frame: str | None
    unit: str | None
    computable_now: bool
    allowed_usage_purposes: tuple[UsagePurpose, ...]
    note: str | None = None


@lru_cache(maxsize=1)
def _raw_matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def feature_specs() -> dict[str, FeatureSpec]:
    """Return {feature -> FeatureSpec} built from the frozen matrix. Cached; never mutated."""
    specs: dict[str, FeatureSpec] = {}
    for row in _raw_matrix()["features"]:
        purposes = tuple(UsagePurpose(p) for p in row.get("allowed_usage_purposes", ()))
        specs[row["feature"]] = FeatureSpec(
            feature=row["feature"],
            category=row.get("category", ""),
            dependencies=tuple(row.get("dependencies", ())),
            output_frame=row.get("output_frame"),
            unit=row.get("unit"),
            computable_now=bool(row.get("computable_now", False)),
            allowed_usage_purposes=purposes,
            note=row.get("note"),
        )
    return specs


def get_spec(feature: str) -> FeatureSpec:
    """Return the frozen spec for ``feature`` (raises KeyError if unknown — fail-closed)."""
    return feature_specs()[feature]


@lru_cache(maxsize=1)
def cp1_grades_from_matrix() -> dict[str, str]:
    """The frozen CP1 evidence grades embedded in the matrix (as raw grade strings)."""
    return dict(_raw_matrix()["cp1_grades"])
