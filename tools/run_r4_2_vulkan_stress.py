#!/usr/bin/env python3
"""Run the preregistered R4.2 R1 smoke or stress roster."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from driveclarify_vulkan_execution_v1.supervisor import CycleSpec, ROOT, run_batch


HISTORICAL = ("driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-0bd82fb9b9bda9e4ca49040e.xml", "d961d55267001411c77610406e593892cd9054df9fe3b6c1f18ccdf77a8946c8", 5101)
DISTINCT = (
    ("driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-2718fa9418f8dd73e16a1d64.xml", "6e70c4c366f7ecf0e263053d3b55ee3c130c0dc94579b8b91f5cac35033ca731", 6201),
    ("driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-2799433ea2fab3dd4ad98919.xml", "fb41438543875c78133974e8113a2d51aeb60c4e23bfcfd4e901451bbe226157", 5701),
    ("driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-840d047f120ca346e73d2654.xml", "5cd86e83008c1d29f52e9a15f1984a739f72eee29002937fe5bed2bb624e1334", 5201),
    ("driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-9b356a1a7689ff9e9e4bc73a.xml", "fb5937603162c65177a3b58bbfbec4bbe291811acda11ff934b3834dd2aab843", 5301),
    ("driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-7091eedbf681e076baca527f.xml", "12f4c42a761ec5583bdc95226bd71e3e9f0a91ab350dc978756460bd08623781", 5401),
    ("driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-18b01f4515edd45690d6a62d.xml", "2304878f4fb413ce3d64200fad40bbedb1870a5fd5a94534dbfd0e2771e1f349", 5801),
    ("driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-06cccfa4723c09ca09a9bc2b.xml", "3885155820b801d3af766555af9be551ce3dc4e9e68caa7d339b06f963e20eef", 6001),
    ("driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-85109942c439ad3b4611b098.xml", "9b4bc5a6c11768e320c777c7b354aa6e980cf6eb12c5d3f150905e4486efe65c", 5901),
)


def roster(phase):
    if phase == "smoke":
        rows = [HISTORICAL] * 4
        prefix = "DC-R4-2-VULKAN-INFRA-R2-SMOKE"
    elif phase == "runtime_stress":
        rows = [HISTORICAL] * 16 + list(DISTINCT)
        prefix = "DC-R4-2-RUNTIME-R2-STRESS"
    else:
        rows = [HISTORICAL] * 24 + list(DISTINCT)
        prefix = "DC-R4-2-VULKAN-INFRA-R2-STRESS"
    return [
        CycleSpec(
            cycle_id=f"{prefix}-{index:04d}",
            route=ROOT / route, expected_sha256=digest, phase=phase.upper(), seed=seed,
        )
        for index, (route, digest, seed) in enumerate(rows, 1)
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("smoke", "stress", "runtime_stress"))
    parser.add_argument("--quiet-seconds", type=float, default=10.0)
    args = parser.parse_args()
    receipts = run_batch(roster(args.phase), quiet_seconds=args.quiet_seconds)
    summary = {
        "phase": args.phase.upper(), "planned": len(roster(args.phase)),
        "completed": len(receipts), "passed": sum(bool(row["pass"]) for row in receipts),
        "status": "PASS" if len(receipts) == len(roster(args.phase)) and all(row["pass"] for row in receipts) else "BLOCKED",
        "cycle_ids": [row["cycle_id"] for row in receipts],
    }
    print(json.dumps(summary, sort_keys=True))
    raise SystemExit(0 if summary["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
