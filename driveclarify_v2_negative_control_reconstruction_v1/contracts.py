"""Outcome-blind physical contracts for V2 negative controls.

Only :func:`physical_scenario` is reachable from ScenarioRunner.  Family,
selection rationale, and evaluator expectations remain outside production
runtime inputs.
"""

from __future__ import annotations

from typing import Any, Mapping


ROUTE = ((-9.65, 100.0, 0.02), (-9.10, 183.0, 0.02))
TRIGGER = (-9.65, 100.5, 0.02, 89.6)
CALIBRATED_ROUTE_VERSION = (
    "route-world-9882bcacf41ab814443101b9abce0712a54fe2351e6b1281e342d06ae323b410"
)
EARLIEST_COMMITMENT_PROGRESS_M = 25.13
MANEUVER_ONSET_PROGRESS_M = 28.22


def _row(
    scenario_id: str,
    family: str,
    route_id: str,
    seed: int,
    actors: tuple[tuple[Any, ...], ...],
    *,
    selection_reason: str,
    ego_initial_transform: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "negative_control_family": family,
        "split": "TRAIN",
        "town": "Town03",
        "route_id": route_id,
        "route": ROUTE,
        "trigger": TRIGGER,
        "instruction": "Turn after the white van.",
        "actors": actors,
        "ego_initial_transform": ego_initial_transform,
        "seed": seed,
        "repeat_seed": seed + 1000,
        "answer_delay_s": 0.1,
        "selection_reason": selection_reason,
        "calibrated_route_version": CALIBRATED_ROUTE_VERSION,
    }


SCENARIOS: tuple[dict[str, Any], ...] = (
    _row(
        "NC-EI-001", "EVIDENCE_INSUFFICIENT", "99400101", 8101,
        (("vehicle.mercedes.sprinter", -3.95, 114.0, 0.30, 89.6),
         ("vehicle.mercedes.sprinter", -15.35, 114.8, 0.30, 89.6)),
        selection_reason=(
            "Two same-depth lateral white vans remain plausible, but image-only LEFT/RIGHT "
            "identity cannot authorize a near/far-to-route-order future-obligation join."
        ),
    ),
    _row(
        "NC-EI-002", "EVIDENCE_INSUFFICIENT", "99400102", 8102,
        (("vehicle.mercedes.sprinter", -4.10, 115.2, 0.30, 89.6),
         ("vehicle.mercedes.sprinter", -15.20, 114.4, 0.30, 89.6)),
        selection_reason=(
            "Independent lateral-layout perturbation reverses the small longitudinal offset "
            "while preserving a physically unresolved image-to-route-order relation."
        ),
    ),
    _row(
        "NC-EI-003", "EVIDENCE_INSUFFICIENT", "99400103", 8103,
        (("vehicle.mercedes.sprinter", -3.80, 116.0, 0.30, 89.6),
         ("vehicle.mercedes.sprinter", -15.50, 115.7, 0.30, 89.6)),
        selection_reason=(
            "Third independent same-depth lateral pair: K2 is visually plausible but neither "
            "referent carries authorization-grade longitudinal route-order identity."
        ),
    ),
    _row(
        "NC-TL-001", "TOO_LATE", "99400201", 8201,
        (("vehicle.mercedes.sprinter", -6.05, 137.0, 0.30, 89.6),
         ("vehicle.mercedes.sprinter", -12.70, 154.0, 0.30, 89.6)),
        ego_initial_transform=(-9.46, 129.4, 0.30, 89.6),
        selection_reason=(
            "The full calibrated route is retained, while physical initialization begins "
            "past the 28.22m maneuver onset and 25.13m earliest commitment."
        ),
    ),
    _row(
        "NC-TL-002", "TOO_LATE", "99400202", 8202,
        (("vehicle.mercedes.sprinter", -6.20, 138.1, 0.30, 89.6),
         ("vehicle.mercedes.sprinter", -12.55, 155.2, 0.30, 89.6)),
        ego_initial_transform=(-9.45, 130.2, 0.30, 89.6),
        selection_reason=(
            "Independent post-onset initialization with both referents still visible; no "
            "deadline, latency, commitment, or safety quantity is altered."
        ),
    ),
    _row(
        "NC-TL-003", "TOO_LATE", "99400203", 8203,
        (("vehicle.mercedes.sprinter", -6.40, 139.0, 0.30, 89.6),
         ("vehicle.mercedes.sprinter", -12.40, 156.0, 0.30, 89.6)),
        ego_initial_transform=(-9.44, 131.0, 0.30, 89.6),
        selection_reason=(
            "Third post-commitment start provides a bounded geometry perturbation while "
            "preserving the frozen route, timing calibration, and two obligations."
        ),
    ),
)


def scenario(scenario_id: str) -> dict[str, Any]:
    rows = [dict(row) for row in SCENARIOS if row["scenario_id"] == scenario_id]
    if len(rows) != 1:
        raise ValueError("V2_NEGATIVE_CONTROL_SCENARIO_BINDING_NOT_ONE:" + scenario_id)
    if rows[0]["split"] != "TRAIN":
        raise ValueError("V2_NEGATIVE_CONTROL_NOT_TRAIN")
    return rows[0]


def physical_scenario(scenario_id: str) -> Mapping[str, Any]:
    row = scenario(scenario_id)
    return {
        key: row[key]
        for key in (
            "scenario_id", "split", "town", "route_id", "route", "trigger",
            "instruction", "actors", "ego_initial_transform",
        )
    }


__all__ = [
    "CALIBRATED_ROUTE_VERSION", "EARLIEST_COMMITMENT_PROGRESS_M",
    "MANEUVER_ONSET_PROGRESS_M", "ROUTE", "SCENARIOS", "TRIGGER",
    "physical_scenario", "scenario",
]
