#!/usr/bin/env python3
"""生成 RQ1 V2 的无标签 split 候选冻结清单。"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq1_grounded_relation_v2.manifest import build, write_frozen

BASE = ROOT / "reports/m1_real_dataset_expansion_v3/DC-M1-DATASET-EXP-V3-20260804T055600Z"
CAMPAIGN = BASE / "combined_runtime_campaigns/DC-M1-V3-RUNTIME-C1-20260804T063000Z"
OUTPUT = ROOT / "reports/driveclarify_rq1_grounded_relation_v2_20260910/manifests/SPLIT_CANDIDATES.json"


def main() -> None:
    value = build(BASE / "V3_SHORTLIST.json", BASE / "V3_RUNTIME_CAMPAIGN_SPEC.json", CAMPAIGN / "stage_b/runs")
    write_frozen(OUTPUT, value)
    print(f"{OUTPUT} dev={len(value['dev'])} test_pending={len(value['test'])} sha256={value['sha256']}")


if __name__ == "__main__":
    main()
