"""Native TRAIN-only runner for the adapter-aligned live gates."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as e1_backend
from driveclarify_paper_mvp_stage6b import backend as stage6b
from tools.run_grounded_language_v1_triad import run as run_grounded

from .runtime import FEATURE_FLAG, MODE_ENV


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts/driveclarify_official_dreaming_adapter_alignment"
LOCAL_ROUTE = ROOT / "driveclarify_simlingo_local_candidate_diagnostic/fixtures/LOCAL-06.xml"
LOCAL_MANIFEST = ROOT / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/dc-runtime-0bd82fb9b9bda9e4ca49040e.json"
SCENARIO_ROOT = ROOT / "driveclarify_grounded_language_v1_extension_e1_r1/scenarios"


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def rmse(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> float:
    values = [
        (float(a) - float(b)) ** 2
        for lrow, rrow in zip(left, right)
        for a, b in zip(lrow, rrow)
    ]
    return math.sqrt(sum(values) / len(values)) if values else 0.0


def classify(receipt: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(receipt.get("candidate_plan_repetitions") or ())
    if len(rows) < 2:
        return {
            "status": "BLOCKED_ADAPTER_ALIGNED_OWN_SCENE_EXPLICIT_BRANCHING_NOT_IDENTIFIED",
            "reason": "CANDIDATE_PLAN_ROWS_UNAVAILABLE",
            "runtime_status": receipt.get("status"),
            "runtime_errors": receipt.get("errors"),
        }
    a = next(row for row in rows if str(row.get("candidate_id", "")).startswith("A"))
    b = next(row for row in rows if str(row.get("candidate_id", "")).startswith("B"))
    route_a = a["route"]
    route_b = b["route"]
    separations = [
        math.hypot(float(x[0]) - float(y[0]), float(x[1]) - float(y[1]))
        for x, y in zip(route_a, route_b)
    ]
    a_endpoint = route_a[-1]
    b_endpoint = route_b[-1]
    metrics = dict(receipt.get("route_divergence") or {})
    gates = {
        "route_rmse_at_least_1m": float(metrics.get("route_rmse_m") or 0.0) >= 1.0,
        "max_separation_at_least_3m": max(separations or [0.0]) >= 3.0,
        "fraction_waypoints_separated_ge_1m_at_least_0_25": (
            sum(value >= 1.0 for value in separations) / len(separations)
            if separations else 0.0
        ) >= 0.25,
        "candidate_A_rightward_endpoint_at_least_2m": float(a_endpoint[1]) >= 2.0,
        "candidate_B_straight_endpoint_within_1_5m": abs(float(b_endpoint[1])) <= 1.5,
        "A_endpoint_at_least_2m_right_of_B": float(a_endpoint[1]) - float(b_endpoint[1]) >= 2.0,
    }
    return {
        "status": "PASS_DRIVECLARIFY_OWN_SCENE_EXPLICIT_DREAMING_BRANCHING" if all(gates.values()) else "BLOCKED_ADAPTER_ALIGNED_OWN_SCENE_EXPLICIT_BRANCHING_NOT_IDENTIFIED",
        "hard_gates": gates,
        "route_rmse_m": float(metrics.get("route_rmse_m") or rmse(route_a, route_b)),
        "max_separation_m": max(separations or [0.0]),
        "fraction_waypoints_separated_ge_1m": sum(value >= 1.0 for value in separations) / len(separations) if separations else 0.0,
        "candidate_A_endpoint": a_endpoint,
        "candidate_B_endpoint": b_endpoint,
        "candidate_A_maneuver": "RIGHT" if float(a_endpoint[1]) >= 2.0 else "NON_RIGHT",
        "candidate_B_maneuver": "STRAIGHT" if abs(float(b_endpoint[1])) <= 1.5 else "NON_STRAIGHT",
        "same_observation": bool(receipt.get("same_rgb_state_navigation_context")),
        "candidate_rows": rows,
    }


def explicit(repetitions: int) -> int:
    name = "own_scene_explicit_a1b1" if repetitions == 1 else "own_scene_explicit_repeats"
    output = ARTIFACT_ROOT / name
    if output.exists() and any(output.iterdir()):
        attempt = 2
        while (ARTIFACT_ROOT / f"{name}_attempt{attempt}").exists():
            attempt += 1
        output = ARTIFACT_ROOT / f"{name}_attempt{attempt}"
    source = e1_backend.resolve_episode(
        fixture_id="E1R1-ASK-PHYS-002",
        method_id="driveclarify_grounded_v1",
        episode_id="DC-ODA-EXPLICIT-{}".format(repetitions),
    )
    spec = replace(
        source,
        episode_id="DC-ODA-EXPLICIT-{}".format(repetitions),
        runtime_config_id="LOCAL-06",
        scenario_id="LOCAL-06",
        runtime_fixture_id="E1R1-ASK-PHYS-002",
        town="Town03",
        route_id="99803006",
        route_path=LOCAL_ROUTE.resolve(),
        runtime_manifest_path=LOCAL_MANIFEST.resolve(),
        raw_instruction="Move one lane towards the right.",
        schedule_sha256=stage6b._file_sha256(LOCAL_ROUTE),
        runtime_manifest_sha256=stage6b._file_sha256(LOCAL_MANIFEST),
        split="train",
    )
    native = run_grounded(
        output,
        case="act",
        seed=5102,
        method_id="driveclarify_grounded_v1",
        device="cpu",
        control=False,
        answer="",
        answer_delay=0.1,
        timeout_seconds=240.0,
        episode_spec=spec,
        visualization=True,
        post_hoc_world_state=False,
        terminate_on_runtime_terminal=True,
        capture_desktop=False,
        environment_overrides={
            FEATURE_FLAG: "1",
            MODE_ENV: "explicit",
            "DRIVECLARIFY_LOCAL_CANDIDATE_REPETITIONS": str(repetitions),
            "SCENARIO_RUNNER_ROOT": str(SCENARIO_ROOT.resolve()),
            "DRIVECLARIFY_E1R1_FIXTURE_ID": "LOCAL-06",
        },
    )
    receipt = json.loads((output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json").read_text(encoding="utf-8"))
    result = classify(receipt)
    result["native_run"] = native
    result["repeat_count_per_candidate"] = repetitions
    dump(output / "OFFICIAL_ALIGNED_EXPLICIT_RESULT.json", result)
    print(json.dumps({"status": result["status"], "output": str(output), "metrics": {key: result.get(key) for key in ("route_rmse_m", "max_separation_m", "candidate_A_endpoint", "candidate_B_endpoint")}}, indent=2))
    return 0 if result["status"].startswith("PASS_") else 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("explicit",))
    parser.add_argument("--repetitions", type=int, choices=(1, 3), default=1)
    args = parser.parse_args()
    return explicit(args.repetitions)


if __name__ == "__main__":
    raise SystemExit(main())
