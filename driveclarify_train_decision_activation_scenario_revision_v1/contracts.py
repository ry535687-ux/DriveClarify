"""Pre-run physical contracts for the TRAIN decision activation revision.

The policy runtime receives only the result of :func:`physical_scenario`.
Design-family and selection rationale remain evaluator-side metadata.
"""

from __future__ import annotations

from typing import Any, Mapping


ROUTE = ((-9.65, 100.0, 0.02), (-9.10, 183.0, 0.02))
TRIGGER = (-9.65, 100.5, 0.02, 89.6)
CALIBRATED_ROUTE_VERSION = "route-world-9882bcacf41ab814443101b9abce0712a54fe2351e6b1281e342d06ae323b410"


def _row(
    scenario_id: str,
    family: str,
    route_id: str,
    seed: int,
    near: tuple[float, float],
    far: tuple[float, float],
    *,
    answer_delay_s: float,
    instruction: str = "Turn after the white van.",
    selection_reason: str,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "design_family": family,
        "split": "TRAIN",
        "town": "Town03",
        "route_id": route_id,
        "route": ROUTE,
        "trigger": TRIGGER,
        "instruction": instruction,
        "actors": (
            ("vehicle.mercedes.sprinter", near[0], near[1], 0.30, 89.6),
            ("vehicle.mercedes.sprinter", far[0], far[1], 0.30, 89.6),
        ),
        "seed": seed,
        "answer_delay_s": answer_delay_s,
        "selection_reason": selection_reason,
        "calibrated_route_version": CALIBRATED_ROUTE_VERSION,
    }


SCENARIOS: tuple[dict[str, Any], ...] = (
    _row("DA-AS-001", "ACT_SHARED", "99300101", 7101, (-6.05, 114.0), (-12.70, 145.0), answer_delay_s=0.1,
         selection_reason="Phase-B calibrated route has a 25.13m earliest commitment and two future right-turn obligations; evaluate its early shared window."),
    _row("DA-AS-002", "ACT_SHARED", "99300102", 7102, (-6.25, 115.5), (-12.55, 146.5), answer_delay_s=0.1,
         selection_reason="Pre-frozen lateral/longitudinal referent perturbation on the same calibrated shared corridor."),
    _row("DA-AS-003", "ACT_SHARED", "99300103", 7103, (-6.40, 117.0), (-12.40, 148.0), answer_delay_s=0.1,
         selection_reason="Second pre-frozen perturbation tests whether upstream evidence survives layout variation."),
    _row("DA-ASK-001", "ASK", "99300201", 7201, (-6.05, 114.0), (-12.70, 145.0), answer_delay_s=0.1,
         selection_reason="Evaluate the calibrated route's intermediate region before the 25.13m commitment, where divergence may become observable while clarification remains timely."),
    _row("DA-ASK-002", "ASK", "99300202", 7202, (-6.25, 115.5), (-12.55, 146.5), answer_delay_s=0.1,
         selection_reason="Pre-frozen unseen seed and referent perturbation for the same physically defined timely-divergence region."),
    _row("DA-ASK-003", "ASK", "99300203", 7203, (-6.40, 117.0), (-12.40, 148.0), answer_delay_s=0.1,
         selection_reason="Third pre-frozen unseen seed/layout; selection uses commitment geometry, not decision output."),
    _row("DA-WAIT-001", "WAIT", "99300301", 7201, (-6.05, 114.0), (-12.70, 145.0), answer_delay_s=3.0,
         selection_reason="DA-ASK-001 physical twin with evaluator-side delayed passenger response; WAIT must derive from a natural ASK."),
    _row("DA-WAIT-002", "WAIT", "99300302", 7202, (-6.25, 115.5), (-12.55, 146.5), answer_delay_s=3.0,
         selection_reason="DA-ASK-002 physical twin with delayed response and no expected-WAIT runtime field."),
    _row("DA-WAIT-003", "WAIT", "99300303", 7203, (-6.40, 117.0), (-12.40, 148.0), answer_delay_s=3.0,
         selection_reason="DA-ASK-003 physical twin with delayed response and existing-holding-only acceptance."),
    _row("DA-FB-UNKNOWN-001", "FALLBACK_UNKNOWN", "99300401", 5101, (-6.05, 114.0), (-12.70, 145.0), answer_delay_s=0.1,
         selection_reason="Preserved white-van hard-case seed; majority upstream relationship evidence historically remains UNKNOWN."),
    _row("DA-FB-LATE-001", "FALLBACK_LATE", "99300402", 6102, (-6.05, 114.0), (-12.70, 145.0), answer_delay_s=0.1,
         selection_reason="Preserved late-divergence control seed; prior sole divergent window had negative safe slack."),
)


def scenario(scenario_id: str) -> dict[str, Any]:
    matches = [dict(row) for row in SCENARIOS if row["scenario_id"] == scenario_id]
    if len(matches) != 1:
        raise ValueError("TRAIN_ACTIVATION_SCENARIO_BINDING_NOT_ONE:" + scenario_id)
    if matches[0]["split"] != "TRAIN":
        raise ValueError("TRAIN_ACTIVATION_SCENARIO_NOT_TRAIN")
    return matches[0]


def physical_scenario(scenario_id: str) -> Mapping[str, Any]:
    """Return only physical facts consumed by ScenarioRunner."""
    row = scenario(scenario_id)
    return {key: row[key] for key in (
        "scenario_id", "split", "town", "route_id", "route", "trigger",
        "instruction", "actors",
    )}


__all__ = ["CALIBRATED_ROUTE_VERSION", "ROUTE", "SCENARIOS", "TRIGGER", "physical_scenario", "scenario"]
