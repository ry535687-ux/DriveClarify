"""Outcome-blind V2-revision physical contracts for TOO_LATE controls."""

from __future__ import annotations

from typing import Any, Mapping

from driveclarify_v2_negative_control_reconstruction_v1.contracts import (
    CALIBRATED_ROUTE_VERSION,
    ROUTE,
    TRIGGER,
)


def _row(scenario_id: str, route_id: str, seed: int, progress_m: float, near_y: float, far_y: float) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "negative_control_family": "TOO_LATE",
        "split": "TRAIN",
        "town": "Town03",
        "route_id": route_id,
        "route": ROUTE,
        "trigger": TRIGGER,
        "instruction": "Turn after the white van.",
        "actors": (
            ("vehicle.mercedes.sprinter", -7.00, near_y, 0.30, 89.6),
            ("vehicle.mercedes.sprinter", -12.00, far_y, 0.30, 89.6),
        ),
        "ego_initial_transform": (-9.50, 100.0 + progress_m, 0.30, 89.6),
        "seed": seed,
        "repeat_seed": seed + 1000,
        "answer_delay_s": 0.1,
        "selection_reason": (
            "Revised prevalidation places the first observation after the frozen latest-safe "
            "start boundary but before commitment, and keeps both referents in the existing "
            "CENTER/CENTER apparent near-far authorization category."
        ),
        "calibrated_route_version": CALIBRATED_ROUTE_VERSION,
    }


SCENARIOS = (
    _row("NC-TL2-001", "99401201", 8301, 23.50, 138.0, 169.0),
    _row("NC-TL2-002", "99401202", 8302, 23.70, 139.0, 170.0),
    _row("NC-TL2-003", "99401203", 8303, 23.90, 140.0, 171.0),
)


def scenario(scenario_id: str) -> dict[str, Any]:
    rows = [dict(row) for row in SCENARIOS if row["scenario_id"] == scenario_id]
    if len(rows) != 1:
        raise ValueError("V2_NEGATIVE_CONTROL_REV2_BINDING_NOT_ONE:" + scenario_id)
    return rows[0]


def physical_scenario(scenario_id: str) -> Mapping[str, Any]:
    row = scenario(scenario_id)
    return {key: row[key] for key in (
        "scenario_id", "split", "town", "route_id", "route", "trigger",
        "instruction", "actors", "ego_initial_transform",
    )}


__all__ = ["SCENARIOS", "physical_scenario", "scenario"]
