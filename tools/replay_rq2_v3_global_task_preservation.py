#!/usr/bin/env python3
"""Diagnostic-only V3 replay over immutable historical admissibility receipts.

The replay reads no arm outcome values and starts no simulator.  Historical
receipts do not contain the authoritative V3 conjunction, so missing evidence
is preserved as UNKNOWN rather than reconstructed from legacy proxies.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_t_mvp.global_task_preservation_v3 import (
    ConjunctName,
    EvidenceState,
    V3_CONJUNCT_ORDER,
    create_v3_preservation_receipt,
    matched_v3_endpoint_denominator,
    not_applicable_conjunct,
    receipt_to_mapping,
    unresolved_conjunct,
    v3_endpoint_denominators,
)


HISTORICAL_RECEIPTS = (
    ROOT
    / "reports/driveclarify_rq2_episode_admissibility_and_analysis_gate_v1"
    / "historical_cell_receipts"
)
DEFAULT_OUTPUT = (
    ROOT
    / "reports/driveclarify_rq2_global_task_contract_v3_evaluator_static_qualification_v1"
    / "HISTORICAL_V3_REPLAY.json"
)
OLD_DEV_SEEDS = (9468329, 9674011, 9674027)


def _unresolved(name: ConjunctName) -> Any:
    return unresolved_conjunct(
        name,
        state=EvidenceState.UNKNOWN,
        reason_code="HISTORICAL_ARTIFACT_LACKS_AUTHORITATIVE_V3_CONJUNCT_EVIDENCE",
    )


def _not_applicable(
    name: ConjunctName, *, receipt_path: Path, cell: Mapping[str, Any]
) -> Any:
    return not_applicable_conjunct(
        name,
        reason_code="NOT_APPLICABLE_BY_FROZEN_TIMING_CONTRACT",
        evidence_source="CellAdmissibilityReceipt timing identity",
        evidence_reference=str(receipt_path.relative_to(ROOT)),
        evidence_digest=str(cell["canonical_sha256"]),
        producer="driveclarify_t_mvp.admissibility.CellAdmissibilityReceipt",
        source_frame_start=cell.get("injection_frame"),
        source_frame_end=cell.get("obligation_frame"),
        source_time_start_s=cell.get("injection_sim_time_s"),
        source_time_end_s=cell.get("obligation_sim_time_s"),
    )


def historical_conjuncts(
    *, receipt_path: Path, cell: Mapping[str, Any]
) -> tuple[Any, ...]:
    bucket = str(cell["timing_bucket"])
    recovery = (
        _unresolved(ConjunctName.APPLICABLE_FROZEN_RECOVERY)
        if bucket == "T3_POST_COMMIT_RECOVERABLE"
        else _not_applicable(
            ConjunctName.APPLICABLE_FROZEN_RECOVERY,
            receipt_path=receipt_path,
            cell=cell,
        )
    )
    rejoin = (
        _not_applicable(
            ConjunctName.APPLICABLE_FROZEN_REJOIN,
            receipt_path=receipt_path,
            cell=cell,
        )
        if bucket == "T4_NO_SAFE_CURRENT_OPPORTUNITY"
        else _unresolved(ConjunctName.APPLICABLE_FROZEN_REJOIN)
    )
    rows = (
        _unresolved(ConjunctName.SAME_G_IDENTITY),
        _unresolved(ConjunctName.NO_WRONG_DESTINATION_SUBSTITUTION),
        _unresolved(
            ConjunctName.AUTHORITATIVE_LEGAL_TOPOLOGY_AWARE_CONTINUATION
        ),
        _unresolved(ConjunctName.ROUTE_AUTHORITY_CHANGE_LATER_CONSUMED),
        recovery,
        rejoin,
    )
    if tuple(item.name for item in rows) != V3_CONJUNCT_ORDER:
        raise RuntimeError("HISTORICAL_V3_CONJUNCT_ORDER_DEFECT")
    return rows


def replay(receipt_dir: Path = HISTORICAL_RECEIPTS) -> Mapping[str, Any]:
    receipts = []
    for path in sorted(receipt_dir.glob("*.json")):
        cell = json.loads(path.read_text(encoding="utf-8"))
        if int(cell["seed"]) not in OLD_DEV_SEEDS:
            raise RuntimeError("HISTORICAL_REPLAY_UNREGISTERED_SEED")
        receipt = create_v3_preservation_receipt(
            cell_admissibility_receipt=cell,
            cell_admissibility_reference=str(path.relative_to(ROOT)),
            conjuncts=historical_conjuncts(receipt_path=path, cell=cell),
        )
        receipts.append(receipt)
    denominators = v3_endpoint_denominators(receipts)
    by_block_method = {
        (item.seed, item.timing_bucket, item.method): item for item in receipts
    }
    matched_pairs = []
    for seed in OLD_DEV_SEEDS:
        for bucket in (
            "T1_BEFORE_COMMITMENT",
            "T2_NEAR_COMMITMENT",
            "T3_POST_COMMIT_RECOVERABLE",
            "T4_NO_SAFE_CURRENT_OPPORTUNITY",
        ):
            b6 = by_block_method[(seed, bucket, "T-B6")]
            for comparator in ("T-B1", "T-B2", "T-B3", "T-B4", "T-B5"):
                matched_pairs.append(
                    (b6, by_block_method[(seed, bucket, comparator)])
                )
    matched_denominators = matched_v3_endpoint_denominator(matched_pairs)
    states: dict[str, int] = {}
    for item in receipts:
        states[item.preservation_state] = states.get(item.preservation_state, 0) + 1
    return {
        "schema_version": "driveclarify.rq2_t.v3_historical_diagnostic_replay.v1",
        "purpose": "MECHANICS_AND_EVIDENCE_COVERAGE_ONLY",
        "old_dev_verdict": "RQ2_PILOT_INCONCLUSIVE_ENGINEERING",
        "old_dev_verdict_changed": False,
        "historical_artifacts_mutated": False,
        "old_seeds_rerun": False,
        "carla_launches": 0,
        "model_executions": 0,
        "scientific_episodes_started": 0,
        "registered_historical_seeds": list(OLD_DEV_SEEDS),
        "receipt_count": len(receipts),
        "preservation_state_counts": states,
        "denominators": denominators,
        "matched_denominators": matched_denominators,
        "interpretation": (
            "Historical admissibility receipts do not establish the new V3 "
            "conjunction; UNKNOWN propagation is therefore the diagnostic result."
        ),
        "receipts": [receipt_to_mapping(item) for item in receipts],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = replay()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in result.items() if key != "receipts"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
