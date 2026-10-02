#!/usr/bin/env python3
"""Run one immutable frozen TRAIN scene with Decision Evidence V2 enabled."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_decision_evidence_v2 import FEATURE_FLAG as V2_FEATURE_FLAG  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from driveclarify_persistent_ambiguity_runtime_v1.runtime import FEATURE_FLAG as V1_FEATURE_FLAG  # noqa: E402
from driveclarify_train_decision_activation_scenario_revision_v1.contracts import scenario  # noqa: E402
from tools import run_train_decision_activation_v1 as frozen_v1  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402


MANIFEST = frozen_v1.MANIFEST
ARTIFACT = ROOT / "artifacts/driveclarify_short_horizon_decision_evidence_contract_v2"


def _history(live: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = live.get("persistent_decision_history")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _events(live: Mapping[str, Any]) -> set[str]:
    episode = live.get("persistent_ambiguity_episode")
    if not isinstance(episode, Mapping):
        return set()
    return {
        str(row.get("event_type"))
        for row in episode.get("history", ())
        if isinstance(row, Mapping)
    }


def lifecycle_ready(family: str, live: Mapping[str, Any]) -> bool:
    history = _history(live)
    events = _events(live)
    if live.get("decision_evidence_contract_version") != "2.0":
        return False
    if family == "ACT_SHARED":
        episode = live.get("persistent_ambiguity_episode", {})
        shared_rows = [
            row
            for row in history
            if row.get("decision") == "ACT_SHARED"
            and row.get("current_executable_coverage") is True
            and row.get("decision_evidence_contract_version") == "2.0"
        ]
        first_shared = shared_rows[0] if shared_rows else None
        vehicle_advanced_after_shared = bool(
            first_shared is not None
            and any(
                row.get("planning_event_id")
                != first_shared.get("planning_event_id")
                and isinstance(row.get("current_progress_m"), (int, float))
                and isinstance(first_shared.get("current_progress_m"), (int, float))
                and float(row["current_progress_m"])
                > float(first_shared["current_progress_m"])
                for row in history
            )
        )
        return bool(
            shared_rows
            and vehicle_advanced_after_shared
            and "DECISION_ACT_SHARED" in events
            and "SHARED_WINDOW_CONSUMED" in events
            and episode.get("semantic_state") == "UNRESOLVED"
            and live.get("semantic_resolution_from_act_shared") is False
        )
    if family == "ASK":
        return bool(
            any(
                row.get("decision") == "ASK"
                and row.get("decision_evidence_contract_version") == "2.0"
                for row in history
            )
            and "ASK_ISSUED" in events
            and "ANSWER_ARRIVED_VALID" in events
            and "LATEST_REPLAN_UNIQUE" in events
            and live.get("persistent_old_bundle_invalidated_before_replan") is True
            and isinstance(live.get("fresh_replan"), Mapping)
        )
    if family == "WAIT":
        decisions = [str(row.get("decision")) for row in history]
        return bool(
            "ASK" in decisions
            and "WAIT" in decisions
            and decisions.index("ASK") < decisions.index("WAIT")
            and "ASK_ISSUED" in events
            and live.get("wait_uses_existing_holding_authority") is True
            and live.get("persistent_answer_matched_active_query") is True
            and not bool(live.get("holding_active"))
            and live.get("persistent_old_bundle_invalidated_before_replan") is True
            and "LATEST_REPLAN_UNIQUE" in events
            and isinstance(live.get("fresh_replan"), Mapping)
            and live.get("status")
            == "PASS_PERSISTENT_POST_ANSWER_UNIQUE_CLOSED_LOOP"
        )
    return False


def fallback_ready(family: str, live: Mapping[str, Any]) -> bool:
    history = _history(live)
    fallback = [row for row in history if row.get("decision") == "FALLBACK"]
    if len(fallback) < 10:
        return False
    if family == "FALLBACK_UNKNOWN":
        return any(
            row.get("candidate_relationship")
            == "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"
            for row in fallback
        )
    if family == "FALLBACK_LATE":
        return any(
            (
                row.get("m2b_inputs", {})
                .get("evidence", {})
                .get("clarification_window", {})
                .get("urgency")
            )
            == "TOO_LATE"
            for row in fallback
        )
    return False


def desktop_ready(family: str, live: Mapping[str, Any]) -> bool:
    history = _history(live)
    dashboard = live.get("decision_window_dashboard")
    if family in {"ACT_SHARED", "ASK", "WAIT"}:
        return bool(
            isinstance(dashboard, Mapping)
            and dashboard.get("decision_evidence_contract_version") == "2.0"
            and any(row.get("decision") == family for row in history)
        )
    return bool(
        isinstance(dashboard, Mapping)
        and len(history) >= 10
        and all(row.get("decision") == "FALLBACK" for row in history[-10:])
    )


def execute(
    scenario_id: str,
    repeat: bool,
    timeout_seconds: float,
    repair_index: int = 0,
) -> Mapping[str, Any]:
    row = scenario(scenario_id)
    family = row["design_family"]
    run_id = scenario_id + ("-R2" if repeat else "-R1")
    if repair_index:
        run_id += "-REPAIR{}".format(repair_index)
    output = ARTIFACT / run_id
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("DECISION_EVIDENCE_V2_OUTPUT_NOT_EMPTY:" + str(output))
    spec = frozen_v1.episode_spec(scenario_id, repeat)
    if repair_index:
        spec = replace(spec, episode_id=spec.episode_id + "-REPAIR{}".format(repair_index))
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
                    V1_FEATURE_FLAG: "1",
                    V2_FEATURE_FLAG: "1",
                    "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
                    "SCENARIO_RUNNER_ROOT": str(
                        (
                            ROOT
                            / "driveclarify_train_decision_activation_scenario_revision_v1/scenario_root"
                        ).resolve()
                    ),
                },
                completion_predicate=(
                    (lambda live: lifecycle_ready(family, live))
                    if family in {"ACT_SHARED", "ASK", "WAIT"}
                    else (lambda live: fallback_ready(family, live))
                ),
                desktop_capture_predicate=lambda live: desktop_ready(family, live),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    choices = tuple(row["scenario_id"] for row in manifest["scenarios"])
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario_id", choices=choices)
    parser.add_argument("--repeat", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=420.0)
    parser.add_argument("--repair-index", type=int, default=0)
    parser.add_argument("--preflight-import-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_import_only:
        entry = frozen_v1._manifest_entry(args.scenario_id)
        heavy = sorted(
            name
            for name in sys.modules
            if name.split(".", 1)[0] in {"carla", "torch"}
        )
        print(
            json.dumps(
                {
                    "status": (
                        "PASS_V2_IMPORT_AND_IMMUTABLE_MANIFEST"
                        if not heavy
                        else "BLOCKED_HEAVY_IMPORT"
                    ),
                    "scenario_id": args.scenario_id,
                    "split": entry["split"],
                    "manifest_file_sha256": frozen_v1.hashlib.sha256(
                        MANIFEST.read_bytes()
                    ).hexdigest(),
                    "manifest_payload_sha256": manifest[
                        "manifest_payload_sha256"
                    ],
                    "heavy_modules": heavy,
                    "artifact_root": str(ARTIFACT),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if not heavy else 2
    try:
        if args.repair_index < 0:
            raise ValueError("REPAIR_INDEX_MUST_BE_NONNEGATIVE")
        result = execute(
            args.scenario_id,
            args.repeat,
            args.timeout_seconds,
            args.repair_index,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if str(result.get("status", "")).startswith("PASS_") else 2
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_DECISION_EVIDENCE_V2_ENGINEERING_FAILURE",
                    "scenario_id": args.scenario_id,
                    "repeat": args.repeat,
                    "repair_index": args.repair_index,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "traceback": traceback.format_exc(),
                    "valid_scientific_run": False,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
