"""Deterministic construction of the frozen 24-scenario E1 population.

The physical assets below are promoted Stage6A *TRAIN* fixtures.  E1 creates
new language-scene pair identities and a new 12/6/6 split; it never reuses an
original DEV or TEST fixture.  Only the already-proven REFERENTIAL and TEMPORAL
mechanisms are admitted.
"""

from __future__ import annotations

from typing import Any

from .contracts import MECHANISMS, canonical_sha256


_STATIC_EQUIVALENT = (
    "DCV0-S001",
    "dc-runtime-22428f8921e98ae592c0d589",
    "Town03",
    "90300498",
    (5101, 5102, 5103),
)
_STATIC_DIVERGENT = (
    "DCV0-S002",
    "dc-runtime-0bd82fb9b9bda9e4ca49040e",
    "Town03",
    "90300498",
    (5101, 5102, 5103),
)
_TEMPORAL_DIVERGENT = (
    "DCV0-S005",
    "dc-runtime-cc86d1528ba350d659eedb7c",
    "Town05",
    "90500720",
    (5301, 5302, 5303),
)
_TEMPORAL_EQUIVALENT = (
    "DCV0-S006",
    "dc-runtime-9b356a1a7689ff9e9e4bc73a",
    "Town05",
    "90500720",
    (5301, 5302, 5303),
)


def _split(index: int) -> str:
    # Per mechanism: four TRAIN, two DEV, two TEST.
    return "train" if index <= 4 else "dev" if index <= 6 else "test"


def _row(
    mechanism: str,
    index: int,
    source: tuple[Any, ...],
    instruction: str,
    *,
    ambiguity_type: str,
    gold_referent_count: int,
    material_target_divergence: bool,
    information_source: str,
    case_family: str,
) -> dict[str, Any]:
    source_id, fixture_id, town, route_id, seeds = source
    scenario_id = "DC-GLV1-E1-{}-{:03d}".format(mechanism, index)
    public = {
        "scenario_id": scenario_id,
        "split": _split(index),
        "raw_instruction": instruction,
        "ambiguity_type": ambiguity_type,
        "source_train_scenario_id": source_id,
        "source_runtime_fixture_id": fixture_id,
        "town": town,
        "route_id": route_id,
        "carla_seeds": list(seeds),
        "source_asset_scope": "ORIGINAL_STAGE6A_TRAIN_PHYSICAL_ASSET_READ_ONLY",
        "original_dev_or_test_asset_used": False,
    }
    return {
        **public,
        "runtime_projection_sha256": canonical_sha256(public),
        "evaluator_labels": {
            "expected_mechanism": mechanism,
            "gold_ambiguity_present": gold_referent_count >= 2
            or ambiguity_type == "TEMPORAL",
            "gold_referent_count": gold_referent_count,
            "material_target_divergence": material_target_divergence,
            "information_source": information_source,
            "case_family": case_family,
        },
    }


def build_population() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    act_instructions = (
        "Continue past the white van.",
        "Proceed past the white van.",
        "Continue past the white van ahead.",
        "Proceed past the white van ahead.",
        "Continue past the bus.",
        "Proceed past the bus.",
        "Continue past the bus ahead.",
        "Proceed past the bus ahead.",
    )
    for index, instruction in enumerate(act_instructions, start=1):
        static = index <= 4
        source = _STATIC_EQUIVALENT if static else _TEMPORAL_EQUIVALENT
        rows.append(
            _row(
                "ACT",
                index,
                source,
                instruction,
                ambiguity_type="REFERENTIAL",
                gold_referent_count=2 if static else 1,
                material_target_divergence=False,
                information_source="NONE",
                case_family=(
                    "MULTIPLE_REFERENTS_EQUIVALENT_EXECUTION"
                    if static
                    else "SINGLE_REFERENT_NO_MATERIAL_AMBIGUITY"
                ),
            )
        )
    ask_instructions = (
        "Turn after the white van.",
        "Turn after the white van ahead.",
        "Turn after the white van on this approach.",
        "Turn after the white van in front.",
        "Stop after the white van.",
        "Pull over after the white van.",
        "Turn before the white van.",
        "Stop before the white van.",
    )
    for index, instruction in enumerate(ask_instructions, start=1):
        rows.append(
            _row(
                "ASK",
                index,
                _STATIC_DIVERGENT,
                instruction,
                ambiguity_type="REFERENTIAL",
                gold_referent_count=2,
                material_target_divergence=True,
                information_source="PASSENGER",
                case_family="MULTIPLE_PLAUSIBLE_REFERENTS_TARGET_TIMING_DIVERGENCE",
            )
        )
    wait_instructions = (
        "Turn after the bus clears.",
        "Turn once the bus clears.",
        "Proceed after the bus clears.",
        "Continue once the bus clears.",
        "Stop after the bus clears.",
        "Pull over after the bus clears.",
        "Turn when the bus has cleared.",
        "Proceed when the bus has cleared.",
    )
    for index, instruction in enumerate(wait_instructions, start=1):
        rows.append(
            _row(
                "WAIT",
                index,
                _TEMPORAL_DIVERGENT,
                instruction,
                ambiguity_type="TEMPORAL",
                gold_referent_count=1,
                material_target_divergence=True,
                information_source="ENVIRONMENT",
                case_family="TRACKED_REFERENT_FUTURE_CLEARANCE_EVENT",
            )
        )
    if len(rows) != 24:
        raise AssertionError("EXTENSION_POPULATION_NOT_24")
    for mechanism in MECHANISMS:
        selected = [row for row in rows if row["evaluator_labels"]["expected_mechanism"] == mechanism]
        if len(selected) != 8:
            raise AssertionError("EXTENSION_MECHANISM_NOT_8:" + mechanism)
        if {split: sum(row["split"] == split for row in selected) for split in ("train", "dev", "test")} != {"train": 4, "dev": 2, "test": 2}:
            raise AssertionError("EXTENSION_MECHANISM_SPLIT_IMBALANCE:" + mechanism)
    return rows


__all__ = ["build_population"]
