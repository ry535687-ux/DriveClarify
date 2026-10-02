"""Frozen procedures that intentionally do not contain formal seed values."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256
from .scenes import SCENE_ORDER


PROTOCOL_ID = "RQ2_T_CG_8X6_FORMAL_DEV_PROTOCOL_V1"
SLOT_IDS = ("S01", "S02", "S03", "S04", "S05", "S06")

SEED_GENERATION_PROTOCOL = {
    "authorization_required": "independent explicit formal-seed authorization after PASS pre-seed gate",
    "generator": "cryptographic OS CSPRNG via secrets.token_bytes(32)",
    "derivation": "SHAKE256 domain-separated rejection sampling of six unique unsigned 32-bit integers",
    "prior_scan_before_generation": [
        "all *SEED*REGISTRY*, *SEED*ROSTER*, *ATTEMPT*LEDGER*, and *EXECUTION*LEDGER* files under reports/",
        "all repository source literals registered as evaluation or engineering seeds",
        "RQ2-T V1, automatic-E2, RQ2-T-CG engineering, and every future TEST exclusion registry",
    ],
    "freshness": "reject any value already present in the completed prior-domain scan, duplicates, and values in administrator-reserved ranges",
    "shared_slots": "the same six fresh DEV seeds must populate every one of the eight formal scenes",
    "values_present_in_freeze": [],
    "formal_seed_values_generated": 0,
}

RUN_ORDER_PROTOCOL = {
    "applies_after_seed_authorization_only": True,
    "scene_index_order": list(SCENE_ORDER),
    "base_balanced_order_indices": [0, 1, 7, 2, 6, 3, 5, 4],
    "six_rows": "cyclic rotations 0..5 of the frozen eight-scene base order",
    "seed_slot_assignment": "sort the six authorized values by SHA256(PROTOCOL_ID || uint32 big-endian); map ranks to S01..S06",
    "within_cell_identity": "RQ2TCG-DEV-{scene_code}-S{slot_number:02d}",
    "formal_roster_materialized": False,
    "formal_roster_row_count": 0,
}

RETRY_PROTOCOL = {
    "exposure_boundary": "earlier of first agent/VLA forward and first endpoint-eligible source frame",
    "pre_agent_infrastructure_failure": "zero-exposure retry allowed once under a fresh attempt identity; preserve failed attempt",
    "after_exposure": "no retry, replacement, deletion, or seed substitution; preserve and classify the cell",
    "interpretation_invalidating_defect": "quarantine the entire 48-cell roster and issue no confirmatory analysis",
    "other_post_exposure_invalidity": "preserve invalid cell without retry; confirmatory completeness gate fails",
    "confirmatory_completeness": "48/48 valid cells required",
}

FUTURE_TEST_EXCLUSION_PROTOCOL = {
    "timing": "before any future TEST identity or value is generated",
    "required_exclusions": [
        "all six authorized formal DEV values", "all formal cell identities",
        "all pre-agent retry attempt identities and values", "all quarantined roster identities and values",
    ],
    "rule": "a future TEST generator must rejection-sample against the sealed exclusion registry and record the scan receipt",
}


def symbolic_cell_id(scene_code: str, slot_id: str) -> str:
    if scene_code not in SCENE_ORDER or slot_id not in SLOT_IDS:
        raise ValueError("RQ2_T_CG_UNKNOWN_SYMBOLIC_CELL")
    return "RQ2TCG-DEV-{}-S{:02d}".format(scene_code, int(slot_id[1:]))


def planned_run_order(seed_values: Sequence[int]) -> Sequence[Mapping[str, Any]]:
    if len(seed_values) != 6 or len(set(seed_values)) != 6:
        raise ValueError("RQ2_T_CG_SIX_UNIQUE_FUTURE_SEEDS_REQUIRED")
    if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2 ** 32 for value in seed_values):
        raise ValueError("RQ2_T_CG_UINT32_SEED_REQUIRED")
    ordered = sorted(seed_values, key=lambda value: hashlib.sha256(
        PROTOCOL_ID.encode("ascii") + value.to_bytes(4, "big")
    ).hexdigest())
    base = RUN_ORDER_PROTOCOL["base_balanced_order_indices"]
    rows = []
    for slot_index, seed in enumerate(ordered):
        rotated = base[slot_index:] + base[:slot_index]
        rows.append({
            "slot_id": SLOT_IDS[slot_index], "seed": seed,
            "scene_order": [SCENE_ORDER[index] for index in rotated],
        })
    return rows


def materialize_formal_seed_values() -> None:
    raise PermissionError("RQ2_T_CG_FORMAL_SEED_AUTHORIZATION_NOT_GRANTED")


def frozen_protocols() -> Mapping[str, Any]:
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_protocols.v1",
        "protocol_id": PROTOCOL_ID, "scene_count": 8, "shared_seed_slot_count": 6,
        "planned_cell_count": 48, "primary_unit": "scene × seed episode",
        "native_execution_not_multiplied_by_views_or_rules": True,
        "seed_generation": SEED_GENERATION_PROTOCOL,
        "run_order": RUN_ORDER_PROTOCOL, "retry": RETRY_PROTOCOL,
        "future_test_exclusion": FUTURE_TEST_EXCLUSION_PROTOCOL,
        "formal_seed_values_generated": 0, "formal_roster_rows_generated": 0,
        "formal_scientific_exposures": 0,
    }
    value["protocol_digest"] = canonical_sha256(value)
    return value


__all__ = [
    "FUTURE_TEST_EXCLUSION_PROTOCOL", "PROTOCOL_ID", "RETRY_PROTOCOL",
    "RUN_ORDER_PROTOCOL", "SEED_GENERATION_PROTOCOL", "SLOT_IDS",
    "frozen_protocols", "materialize_formal_seed_values", "planned_run_order",
    "symbolic_cell_id",
]
