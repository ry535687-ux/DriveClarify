#!/usr/bin/env python3
"""Final non-circular seal for the prospective-formal-v2 package."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/driveclarify_v3_prospective_formal_rq1_rq2_rq3_v2"
OLD = ROOT / "reports/driveclarify_v3_formal_main_experiment_rq1_rq2_rq3"
EXCLUDED_FROM_AGGREGATE = {"ARTIFACT_HASHES.json", "FINAL_REPORT.md", "FINAL_RECEIPT.json"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory():
    return [path for path in sorted(OUT.rglob("*")) if path.is_file() and path.name != "ARTIFACT_HASHES.json"]


def aggregate(paths: list[Path]) -> tuple[str, int]:
    covered = [path for path in paths if path.name not in EXCLUDED_FROM_AGGREGATE]
    payload = "".join(
        f"{path.relative_to(OUT)}\0{sha(path)}\n" for path in covered
    ).encode()
    return hashlib.sha256(payload).hexdigest(), len(covered)


def main() -> None:
    paths = inventory()
    aggregate_sha, covered_count = aggregate(paths)
    total_count = len(paths) + 1  # include ARTIFACT_HASHES.json itself

    report_path = OUT / "FINAL_REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    report = re.sub(
        r"31–32\.[^\n]*",
        f"31. Artifact count: {total_count}.\n32. Aggregate SHA-256: `{aggregate_sha}` (non-circular coverage defined in `ARTIFACT_HASHES.json`).",
        report,
    )
    report_path.write_text(report, encoding="utf-8")

    receipt_path = OUT / "FINAL_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["artifact_count"] = total_count
    receipt["aggregate_sha256"] = aggregate_sha
    receipt["aggregate_covered_artifact_count"] = covered_count
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Aggregate remains stable because the two just-updated files are excluded.
    paths = inventory()
    check_sha, check_count = aggregate(paths)
    if (check_sha, check_count) != (aggregate_sha, covered_count):
        raise RuntimeError("NON_CIRCULAR_AGGREGATE_CHANGED_DURING_SEAL")
    artifacts = [
        {"path": str(path.relative_to(OUT)), "sha256": sha(path), "bytes": path.stat().st_size}
        for path in paths
    ]
    old_ledger = json.loads((OLD / "ARTIFACT_HASHES.json").read_text(encoding="utf-8"))
    ledger = {
        "schema": "driveclarify.prospective_formal.artifact_hashes.v2.final",
        "hash_algorithm": "SHA-256",
        "artifact_count": total_count,
        "listed_artifact_count_excluding_self": len(artifacts),
        "aggregate_covered_artifact_count": covered_count,
        "aggregate_exclusions": sorted(EXCLUDED_FROM_AGGREGATE),
        "aggregate_definition": "sha256(path + NUL + sha256 + LF, lexicographic path order) over covered artifacts; self-referential final report/receipt and ledger excluded",
        "aggregate_sha256": aggregate_sha,
        "artifacts": artifacts,
        "prior_failed_batch_artifact_count": old_ledger["artifact_count"],
        "prior_failed_batch_aggregate_before": old_ledger["aggregate_sha256"],
        "prior_failed_batch_aggregate_after": json.loads((OLD / "ARTIFACT_HASHES.json").read_text(encoding="utf-8"))["aggregate_sha256"],
    }
    (OUT / "ARTIFACT_HASHES.json").write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
