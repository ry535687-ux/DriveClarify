#!/usr/bin/env python3
"""Capture the frozen non-blind M2B TRAIN/DEV cases with M3 lifecycle facts."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from driveclarify_m3_offline_replay import (  # noqa: E402
    lifecycle_aware_dataset_document,
    load_and_capture_nonblind_records,
    strict_json_dumps,
    strict_json_loads,
)


SOURCE_ROOT = (
    REPO_ROOT / "reports/m2b_formal_offline_real_m1_integration" /
    "DC-M2B-FORMAL-OFFLINE-20260804T095508Z"
)
RUNTIME_CASES = SOURCE_ROOT / "M2B_RUNTIME_CASES.json"
DEVELOPMENT_UNITS = SOURCE_ROOT / "M2B_REAL_DEVELOPMENT_UNITS.json"
_FORBIDDEN_SCHEDULE_TOKENS = (
    "FORMAL_LEARNED_M1_TEST", "M2B_R3", "R3_SEALED", "R3_POST",
    "SEALED_BLIND", "BLIND_GOLD",
)


def _atomic_publish(path: Path, text: str) -> None:
    path = path.resolve()
    if path.exists():
        raise FileExistsError("immutable capture output already exists: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise FileExistsError("capture temporary path already exists: " + str(temporary))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schedule", required=True,
                        help="closed pre-frozen non-blind capture schedule JSON")
    parser.add_argument("--output", required=True,
                        help="new immutable canonical dataset JSON path")
    parser.add_argument("--milestone-id", required=True)
    args = parser.parse_args(argv)

    schedule_path = Path(args.schedule).resolve()
    normalized_schedule_path = schedule_path.as_posix().upper()
    if any(token in normalized_schedule_path for token in _FORBIDDEN_SCHEDULE_TOKENS):
        raise ValueError("Formal TEST/R3/blind schedule path rejected")
    schedule = strict_json_loads(schedule_path.read_text(encoding="utf-8"))
    records = load_and_capture_nonblind_records(
        RUNTIME_CASES,
        DEVELOPMENT_UNITS,
        schedule,
        {
            "milestone_id": args.milestone_id,
            "capture_cli": "tools/capture_m2b_nonblind_lifecycle_records.py",
            "source_selection": "ALL_FROZEN_NONBLIND_TRAIN_DEV_STABLE_ORDER",
        },
    )
    dataset = lifecycle_aware_dataset_document(records, schedule)
    publication = strict_json_dumps(dataset) + "\n"
    _atomic_publish(Path(args.output), publication)
    print(strict_json_dumps({
        "dataset_sha256": dataset["dataset_sha256"],
        "record_count": dataset["record_count"],
        "records_sha256": dataset["records_sha256"],
        "split_distribution": dataset["split_distribution"],
        "raw_action_distribution": dataset["raw_action_distribution"],
        "canonical_action_distribution": dataset[
            "canonical_action_distribution"],
        "output": str(Path(args.output).resolve()),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
