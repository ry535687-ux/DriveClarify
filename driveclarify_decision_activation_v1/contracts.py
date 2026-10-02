"""Runtime-visible definitions for three decision activation scenarios."""

from __future__ import annotations

from typing import Any


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario_id": "DA-ACTSHARED-001",
        "split": "TRAIN",
        "town": "Town03",
        "route_id": "99200101",
        "instruction": "Turn after the white van.",
        # The Phase-B physical evidence contract is route-version-bound.  New
        # activation scenarios must reuse that calibrated route geometry; only
        # the TRAIN-only referent layout is new.
        "route": ((-9.65, 100.0, 0.02), (-9.10, 183.0, 0.02)),
        "trigger": (-9.65, 100.5, 0.02, 89.6),
        "actors": (
            ("vehicle.mercedes.sprinter", -6.05, 114.0, 0.30, 89.6),
            ("vehicle.mercedes.sprinter", -12.70, 145.0, 0.30, 89.6),
        ),
    },
    {
        "scenario_id": "DA-ASK-001",
        "split": "TRAIN",
        "town": "Town03",
        "route_id": "99200102",
        "instruction": "Turn after the white van.",
        "route": ((-9.65, 100.0, 0.02), (-9.10, 183.0, 0.02)),
        "trigger": (-9.65, 100.5, 0.02, 89.6),
        "actors": (
            ("vehicle.mercedes.sprinter", -6.05, 114.0, 0.30, 89.6),
            ("vehicle.mercedes.sprinter", -12.70, 145.0, 0.30, 89.6),
        ),
    },
    {
        "scenario_id": "DA-WAIT-001",
        "split": "TRAIN",
        "town": "Town03",
        "route_id": "99200103",
        "instruction": "Turn after the white van.",
        "route": ((-9.65, 100.0, 0.02), (-9.10, 183.0, 0.02)),
        "trigger": (-9.65, 100.5, 0.02, 89.6),
        "actors": (
            ("vehicle.mercedes.sprinter", -6.05, 114.0, 0.30, 89.6),
            ("vehicle.mercedes.sprinter", -12.70, 145.0, 0.30, 89.6),
        ),
    },
)


def scenario(scenario_id: str) -> dict[str, Any]:
    row = next((dict(item) for item in SCENARIOS if item["scenario_id"] == scenario_id), None)
    if row is None:
        raise ValueError("UNKNOWN_DECISION_ACTIVATION_SCENARIO:" + scenario_id)
    if row["split"] != "TRAIN":
        raise ValueError("DECISION_ACTIVATION_MUST_BE_TRAIN_ONLY")
    return row


__all__ = ["SCENARIOS", "scenario"]
