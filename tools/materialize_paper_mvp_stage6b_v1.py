#!/usr/bin/env python3
"""Materialize the hash-bound Stage 6B schedule without starting episodes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_paper_mvp_evaluation.hashing import canonical_bytes, file_sha256
from driveclarify_paper_mvp_evaluation.scheduler import LifecycleSealStore
from driveclarify_paper_mvp_evaluation.stage6b_materialize import (
    materialize_stage6b_schedule,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "reports/paper_mvp_stage6b"
    )
    args = parser.parse_args(argv)
    generated = ROOT / "driveclarify_paper_mvp_scenarios/generated"
    catalog_evidence, schedule, lifecycle = materialize_stage6b_schedule(
        catalog_path=ROOT / "reports/paper_mvp_scenario_freeze_v0/SCENARIO_CATALOG.json",
        stage6a_manifest_path=generated / "STAGE6A_SCENARIO_MANIFEST.json",
        hash_manifest_path=generated / "HASH_MANIFEST.json",
        generated_root=generated,
    )
    output_dir = args.output_dir.resolve()
    catalog_path = output_dir / "STAGE6B_CATALOG_BINDING.json"
    schedule_path = output_dir / "EPISODE_SCHEDULE.json"
    seal_path = output_dir / "LIFECYCLE_SEAL.json"
    if seal_path.exists():
        existing = LifecycleSealStore(seal_path).load()
        if existing.to_dict() != lifecycle.to_dict():
            raise RuntimeError("EXISTING_LIFECYCLE_SEAL_DIFFERS_FROM_FROZEN_SCHEDULE")
    else:
        LifecycleSealStore(seal_path).initialize(lifecycle)
    _write_json(catalog_path, catalog_evidence)
    _write_json(schedule_path, schedule)
    receipt = {
        "schema_version": "driveclarify.paper_mvp_stage6b_materialization_receipt.v1",
        "status": "PASS_STAGE6B_768_SLOTS_MATERIALIZED_TRAIN_NOT_STARTED",
        "catalog_binding_file_sha256": file_sha256(catalog_path),
        "schedule_file_sha256": file_sha256(schedule_path),
        "lifecycle_seal_file_sha256": file_sha256(seal_path),
        "schedule_sha256": schedule["schedule_sha256"],
        "runtime_configuration_count": schedule["runtime_configuration_count"],
        "method_count": schedule["method_count"],
        "episode_count": schedule["episode_count"],
        "split_episode_counts": schedule["split_episode_counts"],
        "method_episode_counts": schedule["method_episode_counts"],
        "policy_annotation_projection_count": 0,
        "started_episode_count": 0,
        "completed_episode_count": 0,
        "test_consumed": False,
        "lifecycle_status": lifecycle.status.value,
    }
    receipt_path = output_dir / "STAGE6B_MATERIALIZATION_RECEIPT.json"
    _write_json(receipt_path, receipt)
    print(
        json.dumps(
            {**receipt, "receipt_file_sha256": file_sha256(receipt_path)},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
