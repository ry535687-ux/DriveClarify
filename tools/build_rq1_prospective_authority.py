#!/usr/bin/env python3
"""重建 RQ1 prospective TEST 静态 authority 包。"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq1_grounded_relation_v2.manifest import write_frozen
from driveclarify_rq1_grounded_relation_v2.prospective import build_units
from driveclarify_static_branch.multi_topology import FROZEN_THRESHOLD_PATH

REPORT = ROOT / "reports/driveclarify_rq1_grounded_relation_v2_20260910"
V3 = ROOT / "reports/m1_real_dataset_expansion_v3/DC-M1-DATASET-EXP-V3-20260804T055600Z"


def main() -> None:
    summary = build_units(REPORT / "manifests/SPLIT_CANDIDATES.json", V3 / "V3_SHORTLIST.json", FROZEN_THRESHOLD_PATH, REPORT / "label_authority/prospective_units")
    output = REPORT / "manifests/PROSPECTIVE_AUTHORITY_SUMMARY.json"
    write_frozen(output, summary)
    print(f"{output} units={summary['unit_count']} sha256={summary['sha256']}")


if __name__ == "__main__":
    main()
