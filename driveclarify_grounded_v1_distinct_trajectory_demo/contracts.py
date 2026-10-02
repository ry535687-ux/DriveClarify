"""Closed constants for the demo-only, TRAIN-only live diagnostic."""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEMO_ENV = "DRIVECLARIFY_GROUNDED_V1_DISTINCT_TRAJECTORY_DEMO"
FIXTURE_ID = "DC-GV1-DEMO-TOWN04-CLOSE-JUNCTIONS-001"
RUN_ID = "DRIVECLARIFY_GROUNDED_V1_DISTINCT_CANDIDATE_TRAJECTORY_LIVE_DEMO"
FINAL_PASS_STATUS = "PASS_GROUNDED_V1_DISTINCT_CANDIDATE_TRAJECTORY_LIVE_DEMO"
ARTIFACT_ROOT = ROOT / "artifacts/grounded_v1_distinct_candidate_trajectory_live_demo"
REPORT_ROOT = ROOT / "reports/grounded_v1_distinct_candidate_trajectory_live_demo"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
SCENARIO_ROOT = Path(__file__).resolve().parent / "scenario_root"
ROUTE_PATH = FIXTURE_ROOT / (FIXTURE_ID + ".xml")
MANIFEST_PATH = FIXTURE_ROOT / (FIXTURE_ID + ".json")

# Demo-only physical facts. They are never imported by the frozen E1-R1
# population and contain no expected decision, candidate label, or gold target.
ACTORS = (
    ("vehicle.mercedes.sprinter", 329.17, -177.80, 0.35, -117.9),
    ("vehicle.mercedes.sprinter", 314.57, -178.14, 0.35, -89.5),
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "ACTORS",
    "ARTIFACT_ROOT",
    "DEMO_ENV",
    "FINAL_PASS_STATUS",
    "FIXTURE_ID",
    "MANIFEST_PATH",
    "REPORT_ROOT",
    "ROUTE_PATH",
    "RUN_ID",
    "SCENARIO_ROOT",
    "file_sha256",
]
