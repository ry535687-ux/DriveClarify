#!/usr/bin/env python3
"""Atomically quarantine the first exposed formal 2A roster after a defect."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq2_t_formal_experiment_2a_v2"
NAMES = (
    "ENTRY_INTEGRITY_RECEIPT.json",
    "FINAL_PREEXPOSURE_GATE_RECEIPT.json",
    "PREEXECUTION_GATE_RECEIPT.json",
    "PREEXECUTION_GATE_REPORT.md",
    "SOURCE_FREEZE_RECEIPT.json",
    "RQ2_T_2A_DEV_SEEDS.json",
    "RQ2_T_2A_FRESH_SEED_ROSTER.json",
    "RQ2_T_2A_FRESHNESS_PROOF.json",
    "RQ2_T_2A_FORMAL_ROSTER.json",
    "RQ2_T_2A_RUN_ORDER.json",
    "SCIENTIFIC_EXPOSURE_REGISTRY.json",
    "ATTEMPT_LEDGER.jsonl",
    "SCIENTIFIC_EXECUTION_LEDGER.jsonl",
    "SCIENTIFIC_TERMINATION_LEDGER.jsonl",
    "POSTEXPOSURE_ENGINEERING_DEFECT_RECEIPT.json",
    "LONGITUDINAL_EVIDENCE",
)
OPTIONAL_ANALYSIS_NAMES = (
    "ANALYSIS_SEED_REGISTRY.json",
    "INDEPENDENT_QUERY_NECESSITY_GOLD.json",
    "EPISODE_LEVEL_PRIMARY_TABLE.csv",
    "EPISODE_LEVEL_PRIMARY_TABLE.json",
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    if path.is_file():
        value.update(path.read_bytes())
    else:
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            value.update(str(child.relative_to(path)).encode("utf-8"))
            value.update(b"\0")
            value.update(child.read_bytes())
            value.update(b"\0")
    return value.hexdigest()


def main() -> int:
    defect = json.loads((REPORT / "POSTEXPOSURE_ENGINEERING_DEFECT_RECEIPT.json").read_text())
    seeds = json.loads((REPORT / "RQ2_T_2A_DEV_SEEDS.json").read_text())
    exposure = json.loads((REPORT / "SCIENTIFIC_EXPOSURE_REGISTRY.json").read_text())
    if defect.get("status") != "POSTEXPOSURE_ENGINEERING_DEFECT":
        raise RuntimeError("QUARANTINE_REQUIRES_POSTEXPOSURE_DEFECT")
    analysis_ingest_defect = bool(
        defect.get("scope") == "FROZEN_ANALYSIS_INGEST"
        and exposure.get("status") == "ALL_64_CELLS_TERMINATED_VALID"
        and exposure.get("terminated_cells") == 64
    )
    if not analysis_ingest_defect and not any(
        row.get("state") == "POSTEXPOSURE_ENGINEERING_DEFECT"
        for row in exposure["cells"].values()
    ):
        raise RuntimeError("QUARANTINE_REQUIRES_EXPOSED_DEFECT_CELL")
    names = NAMES + tuple(
        name for name in OPTIONAL_ANALYSIS_NAMES if (REPORT / name).exists()
    )
    missing = [name for name in NAMES if not (REPORT / name).exists()]
    if missing:
        raise RuntimeError("QUARANTINE_INPUT_MISSING:" + ",".join(missing))
    registry_path = REPORT / "QUARANTINED_ROSTER_REGISTRY.json"
    registry = json.loads(registry_path.read_text()) if registry_path.is_file() else {
        "schema_version": "driveclarify.rq2_t.formal_2a.quarantine_registry.v2",
        "entries": [],
    }
    ordinal = len(registry["entries"]) + 1
    quarantine_id = "QR-{:03d}-{}".format(
        ordinal, str(defect["cell_id"]).replace("/", "-")
    )
    destination = REPORT / "QUARANTINED_ROSTERS" / quarantine_id
    if destination.exists():
        raise RuntimeError("QUARANTINE_DESTINATION_ALREADY_EXISTS")
    before = {name: digest(REPORT / name) for name in names}
    destination.mkdir(parents=True)
    for name in names:
        shutil.move(str(REPORT / name), str(destination / name))
    receipt: dict[str, Any] = {
        "schema_version": "driveclarify.rq2_t.formal_2a.quarantined_roster.v2",
        "status": "QUARANTINED_COMPLETE_EXPOSED_ROSTER",
        "quarantined_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "quarantine_id": quarantine_id,
        "cause": defect.get("cause") or (
            "HEALTHY_REF_01_PROGRESS_INCOMPATIBLE_WITH_ABSOLUTE_WALL_CAP"
            if ordinal == 1
            else "POSTEXPOSURE_ENGINEERING_INTEGRITY_DEFECT"
        ),
        "scientific_contract_changed": False,
        "seed_count": seeds["seed_count"],
        "seeds": seeds["seeds"],
        "seeds_permanently_excluded_from": ["CALIBRATION", "ENGINEERING", "EXPERIMENT_2A", "EXPERIMENT_2B", "TEST"],
        "exposed_cell": defect["cell_id"],
        "scientific_exposure_observed": True,
        "scientific_retries": 0,
        "files_and_trees": before,
        "all_sources_preserved": True,
    }
    receipt["payload_digest"] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    temporary = destination / ".QUARANTINE_RECEIPT.json.tmp"
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, destination / "QUARANTINE_RECEIPT.json")
    registry["entries"].append({
        "quarantine_id": receipt["quarantine_id"],
        "path": str(destination.relative_to(ROOT)),
        "receipt_digest": receipt["payload_digest"],
        "seed_count": receipt["seed_count"],
        "scientific_exposure_observed": True,
    })
    registry_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
