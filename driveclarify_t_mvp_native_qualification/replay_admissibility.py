"""Replay the RQ2 admissibility gate over immutable episode artifacts only."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Mapping

from driveclarify_t_mvp.admissibility import validate_cell_admissibility_receipt
from driveclarify_t_mvp.canonical import canonical_sha256
from driveclarify_t_mvp_native_qualification.admissibility_evidence import write_once
from driveclarify_t_mvp_native_qualification.rq2_dev_postprocess import analyze, collect


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_write_once(path: Path, value: Mapping[str, Any] | list[Any]) -> None:
    write_once(path, value if isinstance(value, Mapping) else {"cells": value})


def replay(*, source_report: Path, output_report: Path, repository_root: Path) -> dict[str, Any]:
    roster_path = source_report / "DEV_ROSTER.json"
    runtime_path = source_report / "dev/runtime_cases.json"
    oracle_path = source_report / "dev/oracle_injection_manifest.json"
    gated_raw = collect(
        source_report,
        roster_path,
        runtime_path,
        oracle_path,
        admissibility_output_dir=output_report / "historical_cell_receipts",
        repository_root=repository_root,
    )
    gated_analysis = analyze(gated_raw)
    original_raw = _load(source_report / "RQ2_T_MVP_RAW_RESULTS.json")
    original_episode_ids = {
        str(row["episode_id"]) for row in original_raw.get("episodes", [])
    }
    cells: list[dict[str, Any]] = []
    for row in gated_raw["episodes"]:
        receipt = validate_cell_admissibility_receipt(
            row["cell_admissibility_receipt"]
        )
        references: list[str] = []
        for field in (
            "injection_evidence_reference",
            "dispatch_evidence_reference",
            "obligation_evidence_reference",
            "truth_join_evidence_reference",
        ):
            references.extend(str(item["relative_path"]) for item in receipt[field])
        references.append(
            str(Path(row["raw_attempt_path"]).relative_to(repository_root))
            + "/process_job/PROCESS_RECEIPT.json"
        )
        cells.append(
            {
                "ordinal": int(row["ordinal"]),
                "case_id": row["case_id"],
                "episode_id": row["episode_id"],
                "seed": int(row["seed"]),
                "method": row["method_id"],
                "timing_bucket": row["timing_bucket"],
                "agent_exposure": receipt["agent_exposed"],
                "pre_exposure_status": receipt["pre_exposure_status"],
                "expected_injection": {
                    **receipt["expected_injection_identity"],
                    "required": receipt["injection_required"],
                },
                "actual_injection": (
                    receipt["injection_observed"]
                    and receipt["injection_evidence_valid"]
                ),
                "injection_frame": receipt["injection_frame"],
                "injection_time_s": receipt["injection_sim_time_s"],
                "expected_dispatch": receipt["exact_expected_native_policy"],
                "actual_dispatch": (
                    receipt["native_dispatch_observed"]
                    and receipt["native_dispatch_evidence_valid"]
                ),
                "dispatch_frame": receipt["dispatch_frame"],
                "dispatch_time_s": receipt["dispatch_sim_time_s"],
                "expected_obligation": receipt[
                    "expected_method_specific_obligation"
                ],
                "obligation_complete": (
                    receipt["obligation_complete"]
                    and receipt["obligation_evidence_valid"]
                ),
                "obligation_frame": receipt["obligation_frame"],
                "obligation_time_s": receipt["obligation_sim_time_s"],
                "required_truth_join": receipt["required_truth_join"],
                "truth_join_complete": receipt["truth_join_complete"],
                "process_terminal_reason": receipt["process_terminal_class"],
                "scientific_terminal_reason": receipt["terminal_class"],
                "timeout": receipt["process_terminal_class"]
                == "SCIENTIFIC_NONCOMPLETION_TIMEOUT",
                "current_extractor_eligibility": row["episode_id"]
                in original_episode_ids,
                "repaired_gate_decision": (
                    "ANALYSIS_ELIGIBLE"
                    if receipt["analysis_eligible"]
                    else "ANALYSIS_INELIGIBLE"
                ),
                "analysis_eligible": receipt["analysis_eligible"],
                "first_missing_mandatory_stage": receipt[
                    "first_ineligibility_reason"
                ],
                "canonical_receipt_sha256": receipt["canonical_sha256"],
                "evidence_path": sorted(set(references)),
            }
        )

    forensics = {
        "schema_version": "driveclarify.rq2.old_dev_cell_forensics.v1",
        "source_is_diagnostic_only": True,
        "old_dev_seeds_quarantined": sorted({row["seed"] for row in cells}),
        "cell_count": len(cells),
        "cells": cells,
    }
    _json_write_once(output_report / "OLD_DEV_CELL_FORENSICS.json", forensics)

    fieldnames = [
        "ordinal",
        "case_id",
        "episode_id",
        "seed",
        "method",
        "timing_bucket",
        "agent_exposure",
        "pre_exposure_status",
        "expected_injection",
        "actual_injection",
        "injection_frame",
        "injection_time_s",
        "expected_dispatch",
        "actual_dispatch",
        "dispatch_frame",
        "dispatch_time_s",
        "expected_obligation",
        "obligation_complete",
        "obligation_frame",
        "obligation_time_s",
        "required_truth_join",
        "truth_join_complete",
        "process_terminal_reason",
        "scientific_terminal_reason",
        "timeout",
        "current_extractor_eligibility",
        "repaired_gate_decision",
        "analysis_eligible",
        "first_missing_mandatory_stage",
        "canonical_receipt_sha256",
        "evidence_path",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for cell in cells:
        flat = dict(cell)
        flat["expected_injection"] = json.dumps(
            flat["expected_injection"], sort_keys=True, separators=(",", ":")
        )
        flat["evidence_path"] = "|".join(flat["evidence_path"])
        writer.writerow(flat)
    csv_path = output_report / "OLD_DEV_CELL_FORENSICS.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_payload = buffer.getvalue()
    if csv_path.exists() and csv_path.read_text(encoding="utf-8") != csv_payload:
        raise FileExistsError("FORENSICS_CSV_WRITE_ONCE_MISMATCH")
    if not csv_path.exists():
        csv_path.write_text(csv_payload, encoding="utf-8")

    ineligibility = Counter(
        row["first_missing_mandatory_stage"]
        for row in cells
        if not row["analysis_eligible"]
    )
    receipt: dict[str, Any] = {
        "schema_version": "driveclarify.rq2.historical_admissibility_replay.v1",
        "source_report": str(source_report.relative_to(repository_root)),
        "source_artifacts": {
            "roster_sha256": _sha256(roster_path),
            "runtime_cases_sha256": _sha256(runtime_path),
            "oracle_manifest_sha256": _sha256(oracle_path),
            "old_raw_results_sha256": _sha256(
                source_report / "RQ2_T_MVP_RAW_RESULTS.json"
            ),
        },
        "diagnostic_only_no_carla_execution": True,
        "classifier_contains_hardcoded_historical_counts": False,
        "planned_cells_derived": len(cells),
        "agent_exposures_derived": sum(bool(row["agent_exposure"]) for row in cells),
        "injection_complete_cells_derived": sum(
            bool(row["actual_injection"]) for row in cells
        ),
        "dispatch_complete_cells_derived": sum(
            bool(row["actual_dispatch"]) for row in cells
        ),
        "obligation_complete_cells_derived": sum(
            bool(row["obligation_complete"]) for row in cells
        ),
        "analysis_eligible_cells_derived": sum(
            bool(row["analysis_eligible"]) for row in cells
        ),
        "analysis_ineligible_cells_derived": sum(
            not bool(row["analysis_eligible"]) for row in cells
        ),
        "ineligibility_taxonomy_counts": dict(sorted(ineligibility.items())),
        "process_terminal_counts": gated_analysis["process_terminal_counts"],
        "scientific_terminal_counts": gated_analysis["terminal_counts"],
        "eligible_b6_vs_b2_matched_pairs": sum(
            bool(row["pair_analysis_eligible"])
            for row in gated_analysis["paired_t_b6_vs_t_b2"]
        ),
        "eligible_b6_vs_b5_matched_pairs": sum(
            bool(row["pair_analysis_eligible"])
            for row in gated_analysis["paired_t_b6_vs_t_b5"]
        ),
        "complete_six_method_blocks": sum(
            row["block_status"] == "COMPLETE_SIX_METHOD"
            for row in gated_analysis["six_method_blocks"]
        ),
        "incomplete_six_method_blocks": sum(
            row["block_status"] == "BLOCK_INCOMPLETE"
            for row in gated_analysis["six_method_blocks"]
        ),
        "ineligible_cells_have_only_unknown_metrics": all(
            all(value == "UNKNOWN" for value in row["metrics"].values())
            for row in gated_raw["episodes"]
            if not row["analysis_eligible"]
        ),
        "old_extractor_admitted_all_cells": len(original_episode_ids) == len(cells),
        "canonical_cell_receipt_sha256s": [
            row["canonical_receipt_sha256"] for row in cells
        ],
        "canonical_sha256": "",
    }
    receipt["canonical_sha256"] = canonical_sha256(receipt)
    _json_write_once(output_report / "HISTORICAL_REPLAY_RECEIPT.json", receipt)
    _json_write_once(output_report / "HISTORICAL_REPLAY_RAW_RESULTS.json", gated_raw)
    _json_write_once(output_report / "HISTORICAL_REPLAY_ANALYSIS.json", gated_analysis)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-report", required=True)
    parser.add_argument("--output-report", required=True)
    parser.add_argument("--repository-root", required=True)
    args = parser.parse_args()
    replay(
        source_report=Path(args.source_report).resolve(),
        output_report=Path(args.output_report).resolve(),
        repository_root=Path(args.repository_root).resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
