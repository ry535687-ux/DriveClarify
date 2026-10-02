"""Separate language-scene identities from nine real physical fixtures."""

from __future__ import annotations

from typing import Any

from .contracts import PHYSICAL_FIXTURES, canonical_sha256


_LANGUAGE = {
    "ACT": (
        "Continue past the white van.",
        "Proceed past the white van.",
        "Continue past the white van ahead.",
        "Proceed past the white van ahead.",
        "Keep going beyond the white van.",
        "Continue beyond the white van.",
        "Proceed beyond the white van ahead.",
        "Keep going past the white van ahead.",
    ),
    "ASK": (
        "Turn after the white van.",
        "Turn after the white van ahead.",
        "Turn after the white van on this approach.",
        "Take the turn after the white van.",
        "Turn after the white van in front.",
        "Make the turn after the white van.",
        "Turn right after the white van.",
        "Take a right after the white van.",
    ),
    "WAIT": (
        "Turn after the bus clears.",
        "Turn once the bus clears.",
        "Proceed after the bus clears.",
        "Continue once the bus clears.",
        "Turn when the bus has cleared.",
        "Proceed when the bus has cleared.",
        "Take the turn after the bus clears.",
        "Continue after the bus has cleared.",
    ),
}


def build_language_scene_catalog() -> list[dict[str, Any]]:
    rows = []
    by_mechanism = {
        mechanism: [item for item in PHYSICAL_FIXTURES if item["mechanism_family"] == mechanism]
        for mechanism in ("ACT", "ASK", "WAIT")
    }
    for mechanism, instructions in _LANGUAGE.items():
        fixtures = by_mechanism[mechanism]
        for index, instruction in enumerate(instructions, start=1):
            split = "train" if index <= 4 else "dev" if index <= 6 else "test"
            fixture = fixtures[(index - 1) % len(fixtures)]
            public = {
                "language_scene_id": "DC-GLV1-E1R1-{}-{:03d}".format(mechanism, index),
                "split": split,
                "raw_instruction": instruction,
                "physical_fixture_id": fixture["fixture_id"],
                "town": fixture["town"],
                "route_id": fixture["route_id"],
                "physical_fixture_reused_across_language_identities": True,
            }
            rows.append({**public, "runtime_projection_sha256": canonical_sha256(public)})
    return rows


def train_rows() -> list[dict[str, Any]]:
    return [row for row in build_language_scene_catalog() if row["split"] == "train"]


__all__ = ["build_language_scene_catalog", "train_rows"]
