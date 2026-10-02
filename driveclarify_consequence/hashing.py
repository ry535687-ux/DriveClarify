"""Deterministic replay hashing (C1.4) + file/source artifact hashing.

The SEMANTIC replay hash is computed over the canonicalized semantic outputs ONLY. It EXCLUDES all
volatile runtime metadata: wall-clock times, temp dirs, process id, monotonic start time, and the
adapter_latency diagnostic value. Identical semantic input => identical hash across reruns. Pure.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .serialization import canonical_json

# Fields excluded from the semantic hash because they are volatile runtime metadata.
_VOLATILE_TIMING_FIELDS = {"adapter_latency_monotonic"}


def _strip_volatile(consequence: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a CandidateConsequenceV0 with volatile diagnostics neutralized.

    - computed_at_monotonic_ns is dropped (assembly wall-clock).
    - timing.adapter_latency_monotonic value/status are normalized (latency is runtime-volatile).
    """
    c = {k: v for k, v in consequence.items() if k != "computed_at_monotonic_ns"}
    timing = dict(c.get("timing", {}))
    for field in _VOLATILE_TIMING_FIELDS:
        if field in timing:
            r = dict(timing[field])
            # keep the fact that it's a latency diagnostic, drop the volatile numeric value/status
            r["value"] = "<VOLATILE_EXCLUDED>"
            r["status"] = "<VOLATILE_EXCLUDED>"
            r["limitations"] = "<VOLATILE_EXCLUDED>"
            timing[field] = r
    c["timing"] = timing
    return c


def semantic_record_view(record_result: dict[str, Any]) -> dict[str, Any]:
    """Project one record result down to its stable semantic content for hashing."""
    return {
        "record_seq": record_result.get("record_seq"),
        "observation_id": record_result.get("observation_id"),
        "status": record_result.get("status"),
        "reason_codes": sorted(record_result.get("reason_codes", [])),
        "consequences": [_strip_volatile(c) for c in record_result.get("consequences", [])],
        "state_machine": record_result.get("state_machine"),
        "comparison": record_result.get("comparison"),
        "shadow_decision": record_result.get("shadow_decision"),
    }


def semantic_replay_hash(record_results: list[dict[str, Any]], *,
                         schema_version: str, adapter_version: str,
                         config_hash: str, source_hashes: dict[str, str]) -> str:
    """SHA-256 over the canonicalized semantic outputs + versions + config + source hashes.

    Excludes current date, temp dir, pid, monotonic start, and adapter latency (see _strip_volatile).
    """
    payload = {
        "schema_version": schema_version,
        "adapter_version": adapter_version,
        "config_hash": config_hash,
        "source_hashes": {k: source_hashes[k] for k in sorted(source_hashes)},
        "records": [semantic_record_view(r) for r in record_results],
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: str | Path) -> str:
    """Stream-hash a file's bytes (SHA-256)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def config_hash(config_dict: dict[str, Any]) -> str:
    """Deterministic hash of the replay config (canonical JSON)."""
    return hashlib.sha256(canonical_json(config_dict).encode("utf-8")).hexdigest()
