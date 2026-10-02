#!/usr/bin/env python3
"""Author independent passenger intent from runtime environment fixtures only.

The builder never opens the Stage 5 catalog or evaluator-private manifest.  It
uses raw instructions and allowed environment seeds, then emits natural-language
intent/answer text with no policy decision or candidate index field.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _intent_options(instruction: str) -> tuple[tuple[str, str], tuple[str, str]]:
    lowered = instruction.casefold()
    if "lane on the left" in lowered:
        return (
            (
                "adjacent left through-lane",
                "I mean the adjacent left through-lane.",
            ),
            ("geometric left fork", "I mean the geometric left fork."),
        )
    if "lane on the right" in lowered:
        return (
            (
                "adjacent right through-lane",
                "I mean the adjacent right through-lane.",
            ),
            ("geometric right fork", "I mean the geometric right fork."),
        )
    if "bus clears" in lowered:
        return (
            (
                "bus-nose-clear boundary and inner corridor",
                "Use the bus-nose-clear boundary and the inner turn corridor.",
            ),
            (
                "full-bus-tail-clear boundary and outer corridor",
                "Wait for the bus tail to clear fully and use the outer turn corridor.",
            ),
        )
    if "pull over" in lowered:
        return (
            (
                "nearest currently legal pull-over zone",
                "Use the nearest currently legal pull-over zone.",
            ),
            (
                "next legal pull-over zone with more space",
                "Use the next legal pull-over zone with more space.",
            ),
        )
    if "second opening" in lowered:
        return (
            ("nearer bus-stop anchor", "Use the nearer bus-stop anchor."),
            ("farther bus-stop anchor", "Use the farther bus-stop anchor."),
        )
    if "suitable distance" in lowered:
        return (
            ("conservative following gap", "Use a conservative following gap."),
            ("moderate following gap", "Use a moderate following gap."),
        )
    if "enough clearance" in lowered:
        return (
            ("wider passage", "Use the wider passage with more clearance."),
            ("nearer admissible passage", "Use the nearer admissible passage."),
        )
    return (
        ("nearer matching referent", "I mean the nearer matching referent."),
        ("farther matching referent", "I mean the farther matching referent."),
    )


def build(runtime_root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    files = sorted(runtime_root.glob("dc-runtime-*.json"))
    if len(files) != 24:
        raise RuntimeError("EXACTLY_24_RUNTIME_FIXTURES_REQUIRED")
    for path in files:
        runtime = json.loads(path.read_text(encoding="utf-8"))
        fixture_id = runtime["runtime_fixture_id"]
        instruction = runtime["raw_instruction"]
        seeds = sorted(int(item) for item in runtime["allowed_seed_values"])
        if len(seeds) != 4:
            raise RuntimeError("EXACTLY_FOUR_SEEDS_REQUIRED:" + fixture_id)
        options = _intent_options(instruction)
        for index, seed in enumerate(seeds):
            intent, answer = options[index % 2]
            records.append(
                {
                    "runtime_fixture_id": fixture_id,
                    "seed": seed,
                    "intent_description": intent,
                    "natural_language_answer": answer,
                    "source": "CONTROLLED_BENCHMARK_PASSENGER_INTENT",
                }
            )
    unsigned = {
        "schema_version": "driveclarify.paper_mvp_stage6b_passenger_intent.v1",
        "authoring_boundary": {
            "source": "RUNTIME_ENVIRONMENT_FIXTURES_ONLY",
            "stage5_catalog_read_count": 0,
            "evaluator_private_manifest_read_count": 0,
            "expected_decision_field_count": 0,
            "gold_candidate_index_field_count": 0,
            "policy_action_field_count": 0,
        },
        "record_count": len(records),
        "records": sorted(
            records,
            key=lambda item: (item["runtime_fixture_id"], item["seed"]),
        ),
    }
    return {**unsigned, "contract_payload_sha256": _canonical_sha256(unsigned)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(args.runtime_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
