"""Generate deterministic Method M1 v0 offline evaluation artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .method_evaluation import (
    build_labelled_dataset,
    dataset_provenance,
    deterministic_repeat_check,
    evaluate_method,
    s1_forward_report,
    write_json,
)


REPO = Path(__file__).resolve().parents[1]
DEFAULT_S1 = (
    REPO
    / "reports/driveclarify_candidate_sensitivity_pilot"
    / "DC-CSENS-S1-20260730T082509Z"
    / "CANDIDATES.json"
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--s1-artifact", default=str(DEFAULT_S1))
    args = parser.parse_args(argv)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    dataset = build_labelled_dataset()
    baselines, ablations = evaluate_method(dataset)
    provenance = dataset_provenance(dataset, Path(args.s1_artifact))
    provenance["real_s1_compatibility"] = s1_forward_report(
        Path(args.s1_artifact), dataset
    )
    provenance["cpu_deterministic_repeat"] = deterministic_repeat_check(dataset)
    write_json(output / "DATASET_PROVENANCE.json", provenance)
    write_json(output / "BASELINE_RESULTS.json", baselines)
    write_json(output / "ABLATION_RESULTS.json", ablations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

