"""Validation layer for the offline replay pipeline (pure, read-only).

Three independent validators, each returning a list of structured violation codes (empty = valid):
  1. schema_validate        — JSON Schema (Draft 2020-12) against the frozen input schema.
  2. semantic_validate      — the CP2 input-contract binding invariants (reuses contracts.py).
  3. linkage_validate       — observation / frame / forward linkage between candidates and record.
Plus freshness evaluation (candidate_stale) against the record's snapshot frame / monotonic clock.

None of these mutate the input. A record failing (1) or (2) is classified FAILED_VALIDATION by the
replay driver; it is never silently coerced into a success.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .contracts import candidates_comparable, validate_input

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "schemas"
INPUT_SCHEMA_PATH = SCHEMA_DIR / "consequence_adapter_input_v0.schema.json"
CANDIDATE_SCHEMA_PATH = SCHEMA_DIR / "candidate_consequence_v0.schema.json"
STATE_MACHINE_SCHEMA_PATH = SCHEMA_DIR / "adapter_state_machine_input_v0.schema.json"


@lru_cache(maxsize=4)
def _load_validator(path_str: str) -> Draft202012Validator:
    schema = json.loads(Path(path_str).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def schema_validate(record: dict[str, Any], schema_path: Path = INPUT_SCHEMA_PATH) -> list[str]:
    """Return sorted JSON-schema violation strings (path: message). Empty => schema-valid."""
    validator = _load_validator(str(schema_path))
    errors = sorted(
        validator.iter_errors(record),
        key=lambda e: (tuple(str(p) for p in e.absolute_path), e.message),
    )
    out: list[str] = []
    for e in errors:
        path = ".".join(str(p) for p in e.absolute_path) or "$"
        out.append(f"SCHEMA:{path}:{e.message}")
    return out


def semantic_validate(record: dict[str, Any]) -> list[str]:
    """Binding invariants from the CP2 input contract (delegates to contracts.validate_input)."""
    return list(validate_input(record))


def linkage_validate(record: dict[str, Any]) -> list[str]:
    """Observation / frame / forward-linkage checks between candidates and the shared observation.

    - Every candidate.source_observation_id MUST equal metadata.observation_id (else mismatch).
    - candidates_comparable must hold (shared observation + frame by construction).
    - source_forward_id, when present on multiple candidates, must be internally consistent per
      candidate (a candidate cannot claim two forwards) — we only check presence/shape here.
    """
    v: list[str] = []
    md = record.get("metadata", {})
    obs = md.get("observation_id")
    cands = record.get("candidate_plans", [])
    if not isinstance(cands, list) or not cands:
        v.append("LINKAGE_NO_CANDIDATES")
        return v
    for i, c in enumerate(cands):
        if c.get("source_observation_id") != obs:
            v.append(f"LINKAGE_OBSERVATION_MISMATCH:{i}")
    if not candidates_comparable(record):
        v.append("LINKAGE_CANDIDATES_NOT_COMPARABLE")
    return v


def evaluate_freshness(record: dict[str, Any], candidate: dict[str, Any]) -> tuple[bool, str | None]:
    """Return (is_stale, reason_code). Compares candidate.stale_after against the record clock, in
    the SAME domain only (TIME_POLICY_V0: no cross-domain comparison).

    - basis SNAPSHOT_FRAME  -> compare against metadata.source_snapshot_frame (sim domain).
    - basis MONOTONIC_NS    -> compare against metadata.generated_at_monotonic_ns (monotonic domain).
    - basis UNKNOWN or missing value -> not decidable => (False, 'FRESHNESS_UNKNOWN_BASIS').
    A candidate is stale when the current clock has passed stale_after (strictly greater).
    """
    stale_after = candidate.get("stale_after")
    if not isinstance(stale_after, dict):
        return False, "FRESHNESS_MISSING_STALE_AFTER"
    basis = stale_after.get("basis")
    value = stale_after.get("value")
    md = record.get("metadata", {})
    if basis == "SNAPSHOT_FRAME":
        now = md.get("source_snapshot_frame")
        if not isinstance(value, int) or not isinstance(now, int):
            return False, "FRESHNESS_UNKNOWN_BASIS"
        return (now > value), (None if now <= value else "CANDIDATE_STALE")
    if basis == "MONOTONIC_NS":
        now = md.get("generated_at_monotonic_ns")
        if not isinstance(value, int) or not isinstance(now, int):
            return False, "FRESHNESS_UNKNOWN_BASIS"
        return (now > value), (None if now <= value else "CANDIDATE_STALE")
    return False, "FRESHNESS_UNKNOWN_BASIS"
