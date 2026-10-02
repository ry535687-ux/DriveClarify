"""Replay summary (C1.2) + global completion status (C1.5).

Per-record statuses and the aggregate counts + reason-code/unknown-field/blocked-capability
histograms. Global status is explicit (never inferred from an exit code alone). Pure.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# Per-record statuses.
SUCCESS = "SUCCESS"
PARTIAL = "PARTIAL"
FAILED_VALIDATION = "FAILED_VALIDATION"
FAILED_COMPUTATION = "FAILED_COMPUTATION"
SKIPPED = "SKIPPED"

# Global completion statuses.
PASS = "PASS"
PASS_WITH_PARTIAL_RECORDS = "PASS_WITH_PARTIAL_RECORDS"
FAIL_INPUT_CONTRACT = "FAIL_INPUT_CONTRACT"
FAIL_GLOBAL_CONFIGURATION = "FAIL_GLOBAL_CONFIGURATION"
FAIL_OUTPUT_SERIALIZATION = "FAIL_OUTPUT_SERIALIZATION"


def build_summary(record_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the C1.2 failure summary from per-record results."""
    total = len(record_results)
    by_status: Counter[str] = Counter(r.get("status") for r in record_results)
    reason_counts: Counter[str] = Counter()
    unknown_field_counts: Counter[str] = Counter()
    blocked_capability_counts: Counter[str] = Counter()
    stale = 0
    cross_frame = 0
    obs_mismatch = 0
    serialization_errors = 0

    for r in record_results:
        rec_codes = r.get("reason_codes", [])
        for rc in rec_codes:
            reason_counts[rc] += 1
            if "SERIALIZATION" in rc:
                serialization_errors += 1
        # Per-record flags (a record counts once even if it triggers multiple related reason codes).
        if any("FRAME_MISMATCH" in rc or "SOURCE_FRAME" in rc for rc in rec_codes):
            cross_frame += 1
        if any("OBSERVATION_MISMATCH" in rc for rc in rec_codes):
            obs_mismatch += 1
        record_has_stale = any("CANDIDATE_STALE" in rc for rc in rec_codes)
        for uf in r.get("unknown_fields", []):
            unknown_field_counts[uf.get("field", "?")] += 1
            rc = uf.get("reason_code") or ""
            if uf.get("status") == "STALE" or "CANDIDATE_STALE" in rc:
                record_has_stale = True
            for dep in uf.get("dependencies", []):
                blocked_capability_counts[dep] += 1
        if record_has_stale:
            stale += 1

    return {
        "total_records": total,
        "success_records": by_status.get(SUCCESS, 0),
        "partial_records": by_status.get(PARTIAL, 0),
        "failed_validation_records": by_status.get(FAILED_VALIDATION, 0),
        "failed_computation_records": by_status.get(FAILED_COMPUTATION, 0),
        "skipped_records": by_status.get(SKIPPED, 0),
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "unknown_field_counts": dict(sorted(unknown_field_counts.items())),
        "blocked_capability_counts": dict(sorted(blocked_capability_counts.items())),
        "stale_candidate_counts": stale,
        "cross_frame_rejection_counts": cross_frame,
        "observation_mismatch_counts": obs_mismatch,
        "serialization_errors": serialization_errors,
    }


def completion_status(summary: dict[str, Any]) -> str:
    """Derive the explicit global completion status (C1.5)."""
    total = summary["total_records"]
    if total == 0:
        return FAIL_INPUT_CONTRACT
    failed = summary["failed_validation_records"] + summary["failed_computation_records"]
    if failed == total:
        return FAIL_INPUT_CONTRACT
    if summary["partial_records"] > 0 or failed > 0 or summary["skipped_records"] > 0:
        return PASS_WITH_PARTIAL_RECORDS
    return PASS
