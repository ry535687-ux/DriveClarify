#!/usr/bin/env python3
"""Run one frozen native TRAIN decision-activation scenario."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_train_decision_activation_scenario_revision_v1.contracts import scenario  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from driveclarify_persistent_ambiguity_runtime_v1.runtime import FEATURE_FLAG  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402

REPORT = ROOT / "reports/driveclarify_train_decision_activation_scenario_revision_v1"
MANIFEST = REPORT / "TRAIN_ACTIVATION_SCENARIO_MANIFEST.json"
ARTIFACT = ROOT / "artifacts/driveclarify_train_decision_activation_scenario_revision_v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _manifest_entry(scenario_id: str) -> Mapping[str, Any]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    observed = hashlib.sha256(_canonical(value["scenarios"])).hexdigest()
    if observed != value["manifest_payload_sha256"]:
        raise RuntimeError("TRAIN_ACTIVATION_MANIFEST_HASH_MISMATCH")
    matches = [row for row in value["scenarios"] if row["scenario_id"] == scenario_id]
    if len(matches) != 1:
        raise RuntimeError("TRAIN_ACTIVATION_MANIFEST_BINDING_NOT_ONE")
    entry = matches[0]
    for key in ("fixture_xml", "physical_json"):
        path = ROOT / entry[key]
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry[key + "_sha256"]:
            raise RuntimeError("TRAIN_ACTIVATION_FROZEN_FIXTURE_HASH_MISMATCH:" + key)
    return entry


def _history(live: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = live.get("persistent_decision_history")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _events(live: Mapping[str, Any]) -> set[str]:
    episode = live.get("persistent_ambiguity_episode")
    if not isinstance(episode, Mapping):
        return set()
    return {row.get("event_type") for row in episode.get("history", ()) if isinstance(row, Mapping)}


def lifecycle_ready(family: str, live: Mapping[str, Any]) -> bool:
    history = _history(live)
    events = _events(live)
    if family == "ACT_SHARED":
        indexes = [index for index, row in enumerate(history) if row.get("decision") == "ACT_SHARED"]
        if not indexes:
            return False
        first_index = indexes[0]
        first = history[first_index]
        episode = live.get("persistent_ambiguity_episode", {})
        return bool(
            episode.get("semantic_state") == "UNRESOLVED"
            and "DECISION_ACT_SHARED" in events
            and "SHARED_WINDOW_CONSUMED" in events
            and any(
                row.get("source_frame_id") != first.get("source_frame_id")
                and row.get("bundle_id") != first.get("bundle_id")
                for row in history[first_index + 1 :]
            )
        )
    if family == "ASK":
        return bool(
            any(row.get("decision") == "ASK" for row in history)
            and "ASK_ISSUED" in events
            and "ANSWER_ARRIVED_VALID" in events
            and "LATEST_REPLAN_UNIQUE" in events
            and live.get("persistent_old_bundle_invalidated_before_replan") is True
            and isinstance(live.get("fresh_replan"), Mapping)
        )
    if family == "WAIT":
        return bool(
            any(row.get("decision") == "WAIT" for row in history)
            and "ASK_ISSUED" in events
            and live.get("persistent_answer_matched_active_query") is True
            and not bool(live.get("holding_active"))
            and live.get("persistent_old_bundle_invalidated_before_replan") is True
        )
    return False


def desktop_ready(family: str, live: Mapping[str, Any]) -> bool:
    history = _history(live)
    if family in {"ACT_SHARED", "ASK", "WAIT"}:
        return bool(isinstance(live.get("decision_window_dashboard"), Mapping)
                    and any(row.get("decision") == family for row in history))
    return bool(isinstance(live.get("decision_window_dashboard"), Mapping)
                and len(history) >= 10 and all(row.get("decision") == "FALLBACK" for row in history[-10:]))


def episode_spec(scenario_id: str, repeat: bool):
    row = scenario(scenario_id)
    entry = _manifest_entry(scenario_id)
    base = backend.resolve_episode(
        fixture_id="E1R1-ASK-PHYS-001",
        method_id="driveclarify_grounded_v1",
        episode_id="train-decision-activation-base",
    )
    route_path = (ROOT / entry["fixture_xml"]).resolve()
    route_sha256 = hashlib.sha256(route_path.read_bytes()).hexdigest()
    seed = int(entry["repeat_seed"] if repeat else entry["seed"])
    return replace(
        base,
        episode_id="DC-TRAIN-DECISION-ACTIVATION-{}-{}-20260813".format(scenario_id, "R2" if repeat else "R1"),
        scenario_id=scenario_id,
        split="train",
        seed=seed,
        town=row["town"],
        route_id=row["route_id"],
        route_path=route_path,
        raw_instruction=row["instruction"],
        schedule_sha256=route_sha256,
    )


def execute(scenario_id: str, repeat: bool, timeout_seconds: float) -> Mapping[str, Any]:
    row = scenario(scenario_id)
    family = row["design_family"]
    run_id = scenario_id + ("-R2" if repeat else "-R1")
    output = ARTIFACT / run_id
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("TRAIN_ACTIVATION_OUTPUT_NOT_EMPTY:" + str(output))
    spec = episode_spec(scenario_id, repeat)
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            return run_grounded(
                output,
                case="ask",
                seed=spec.seed,
                method_id="driveclarify_grounded_v1",
                device="cpu",
                control=True,
                answer="The nearer white van.",
                answer_delay=float(row["answer_delay_s"]),
                timeout_seconds=timeout_seconds,
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides={
                    FEATURE_FLAG: "1",
                    "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
                    "SCENARIO_RUNNER_ROOT": str((ROOT / "driveclarify_train_decision_activation_scenario_revision_v1/scenario_root").resolve()),
                },
                completion_predicate=(lambda live: lifecycle_ready(family, live)) if family in {"ACT_SHARED", "ASK", "WAIT"} else None,
                desktop_capture_predicate=lambda live: desktop_ready(family, live),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)


def main() -> int:
    choices = tuple(row["scenario_id"] for row in json.loads(MANIFEST.read_text())["scenarios"])
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario_id", choices=choices)
    parser.add_argument("--repeat", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=420.0)
    parser.add_argument("--preflight-import-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_import_only:
        entry = _manifest_entry(args.scenario_id)
        heavy = sorted(name for name in sys.modules if name.split(".", 1)[0] in {"carla", "torch"})
        print(json.dumps({
            "status": "PASS_IMPORT_AND_FROZEN_MANIFEST" if not heavy else "BLOCKED_HEAVY_IMPORT",
            "scenario_id": args.scenario_id, "split": entry["split"],
            "manifest_payload_sha256": json.loads(MANIFEST.read_text())["manifest_payload_sha256"],
            "heavy_modules": heavy,
        }, indent=2))
        return 0 if not heavy else 2
    try:
        result = execute(args.scenario_id, args.repeat, args.timeout_seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if str(result.get("status", "")).startswith("PASS_") else 2
    except Exception as error:
        print(json.dumps({
            "status": "BLOCKED_TRAIN_ACTIVATION_ENGINEERING_FAILURE",
            "scenario_id": args.scenario_id, "repeat": args.repeat,
            "error_type": type(error).__name__, "error": str(error),
            "traceback": traceback.format_exc(), "valid_scientific_run": False,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
