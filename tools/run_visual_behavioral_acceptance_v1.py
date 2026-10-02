#!/usr/bin/env python3
"""Run one frozen TRAIN-only native visual behavioral acceptance case.

This runner contains no decision policy.  It selects already validated fixtures,
enables the frozen production runtime, and supplies read-only lifecycle/capture
predicates to the existing native CARLA triad runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_decision_evidence_v3 import FEATURE_FLAG as V3_FEATURE_FLAG  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from driveclarify_persistent_ambiguity_runtime_v1.convergence_observer import (  # noqa: E402
    CONVERGENCE_OBSERVER_ENV,
)
from driveclarify_persistent_ambiguity_runtime_v1.runtime import FEATURE_FLAG as PERSISTENT_FLAG  # noqa: E402
from driveclarify_train_decision_activation_scenario_revision_v1.contracts import scenario as activation_scenario  # noqa: E402
from tools import run_method_v1_act_semantics_targeted_native_validation as targeted  # noqa: E402
from tools import run_minimum_sufficient_decision_evidence_v3 as v3_runner  # noqa: E402
from tools import run_short_horizon_decision_evidence_v2 as v2_runner  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402


REPORT = ROOT / "reports/driveclarify_method_v1_visual_behavioral_acceptance_suite_v1"
ARTIFACT = ROOT / "artifacts/driveclarify_method_v1_visual_behavioral_acceptance_suite_v1"
MANIFEST = REPORT / "VISUAL_ACCEPTANCE_SUITE_MANIFEST.json"

CASES = {
    "01_ACT": {"run_id": "DCVA1-01-ACT-0815", "case": "act", "seed": 6101},
    "02_ACT_SHARED": {"run_id": "DCVA1-02-AS-0815", "case": "act", "seed": 6201},
    "03_ASK": {"run_id": "DCVA1-03-ASK-0815", "case": "ask", "seed": 7201},
    "04_WAIT": {"run_id": "DCVA1-04-WAIT-0815", "case": "ask", "seed": 7201},
    "05_EI": {"run_id": "DCVA1-05-EI-0815", "case": "ask", "seed": 8101},
    "06_TL": {"run_id": "DCVA1-06-TL-0815", "case": "ask", "seed": 8301},
    "07_HARD_RULE": {"run_id": "DCVA1-07-HR-0815", "case": "act", "seed": 6301},
}


def _history(live: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = live.get("persistent_decision_history")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _method_history(live: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = live.get("method_v1_decision_history")
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


def complete(kind: str, live: Mapping[str, Any]) -> bool:
    history = _history(live)
    method = _method_history(live)
    events = _events(live)
    if kind == "01_ACT":
        return any(
            row.get("decision_label") == "ACT"
            and row.get("decision_reason") == "NO_AMBIGUITY"
            and row.get("effective_K") == 1
            for row in method
        ) and live.get("candidate_simlingo_forward_count") == 0
    if kind == "02_ACT_SHARED":
        rows = [row for row in history if row.get("decision") == "ACT_SHARED"]
        return len(rows) >= 4 and all(
            row.get("current_action_relation") == "SHARED"
            and row.get("future_obligation_relation") == "UNKNOWN"
            and row.get("clarification_state") == "NOT_NEEDED_YET"
            and row.get("recoverability") == "RECOVERABLE"
            and row.get("precommitment_refresh_guarantee") == "GUARANTEED"
            and isinstance(row.get("shared_action_lease"), Mapping)
            and row["shared_action_lease"].get("valid") is True
            for row in rows[:4]
        )
    if kind == "03_ASK":
        decisions = [row.get("decision") for row in history]
        ask_rows = [row for row in history if row.get("decision") == "ASK"]
        return bool(
            decisions.count("ACT_SHARED") >= 3
            and ask_rows
            and ask_rows[0].get("current_action_relation") == "DIVERGENT"
            and ask_rows[0].get("future_obligation_relation") == "DIVERGENT"
            and ask_rows[0].get("clarification_state") == "CLARIFY_NOW"
            and "ASK_ISSUED" in events
        )
    if kind == "04_WAIT":
        decisions = [str(row.get("decision")) for row in history]
        return bool(
            "ASK" in decisions
            and "WAIT" in decisions
            and decisions.index("ASK") < decisions.index("WAIT")
            and live.get("persistent_answer_matched_active_query") is True
            and live.get("persistent_old_bundle_invalidated_before_replan") is True
            and live.get("authority_revoked_before_stale") is True
            and isinstance(live.get("fresh_replan"), Mapping)
            and live.get("post_answer_decision") in {"ACT", "FALLBACK", "WAIT"}
            and live.get("status") == "PASS_PERSISTENT_POST_ANSWER_UNIQUE_CLOSED_LOOP"
        )
    if kind == "05_EI":
        rows = [row for row in history if row.get("decision") == "FALLBACK"]
        return len(rows) >= 3 and any(
            row.get("recoverability") == "UNKNOWN"
            and row.get("clarification_state") == "UNKNOWN"
            for row in rows
        )
    if kind == "06_TL":
        rows = [row for row in history if row.get("decision") == "FALLBACK"]
        return len(rows) >= 3 and any(
            row.get("clarification_state") == "TOO_LATE"
            and row.get("precommitment_refresh_guarantee") == "NOT_GUARANTEED"
            for row in rows
        )
    hard_rule_veto = any(
        isinstance(row.get("m2b_inputs"), Mapping)
        and row["m2b_inputs"].get("hard_safety_gate") is True
        and row["m2b_inputs"].get("hard_rule_gate") is False
        for row in history
    )
    convergence_gate_error = any(
        isinstance(row, Mapping)
        and row.get("stage") == "METHOD_V1_CONVERGENCE_FRESH_REPLAN"
        and row.get("message") == "CONVERGENCE_UNIQUE_M2B_FAIL_CLOSED"
        for row in (live.get("errors") or ())
    )
    candidate_forwards_before = max(
        (
            int(row.get("cumulative_compute_accounting", {}).get("candidate_forward_count") or 0)
            for row in history
        ),
        default=0,
    )
    fresh_unique_forward_executed = (
        int(live.get("candidate_simlingo_forward_count") or 0)
        == candidate_forwards_before + 1
    )
    return bool(
        live.get("convergence_effective_k") == 1
        and live.get("convergence_old_bundle_invalidated") is True
        and live.get("convergence_shared_authority_revoked") is True
        and live.get("convergence_stale_plan_selected") is False
        and fresh_unique_forward_executed
        and hard_rule_veto
        and convergence_gate_error
        and any(row.get("decision_label") == "FALLBACK" for row in method)
    )


def capture_ready(kind: str, live: Mapping[str, Any]) -> bool:
    if kind == "04_WAIT":
        return any(row.get("decision") == "WAIT" for row in _history(live))
    return complete(kind, live)


def _manifest_preflight() -> Mapping[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("revision") != 0 or manifest.get("frozen_before_first_new_carla_run") is not True:
        raise RuntimeError("VISUAL_ACCEPTANCE_MANIFEST_NOT_FROZEN_REVISION_0")
    for row in manifest.get("cases", []):
        for item in row.get("protected_inputs", []):
            path = ROOT / str(item["path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise RuntimeError("VISUAL_ACCEPTANCE_PROTECTED_INPUT_HASH_MISMATCH:" + str(path))
    return manifest


def spec_for(kind: str, run_id: Optional[str] = None):
    run_id = run_id or CASES[kind]["run_id"]
    if kind == "01_ACT":
        targeted._manifest_preflight()
        spec = backend.resolve_episode(
            fixture_id="E1R1-ACT-PHYS-001",
            method_id="driveclarify_grounded_v1",
            episode_id=run_id,
        )
        return replace(spec, seed=6101), 0.1, targeted.SCENARIO_ROOT
    if kind in {"02_ACT_SHARED", "07_HARD_RULE"}:
        targeted._manifest_preflight()
        spec = backend.resolve_episode(
            fixture_id="E1R1-ACT-PHYS-003",
            method_id="driveclarify_grounded_v1",
            episode_id=run_id,
        )
        return replace(spec, seed=CASES[kind]["seed"]), 0.1, targeted.SCENARIO_ROOT
    if kind in {"03_ASK", "04_WAIT"}:
        scenario_id = "DA-ASK-001" if kind == "03_ASK" else "DA-WAIT-001"
        row = activation_scenario(scenario_id)
        spec = replace(v2_runner.frozen_v1.episode_spec(scenario_id, False), episode_id=run_id)
        root = ROOT / "driveclarify_train_decision_activation_scenario_revision_v1/scenario_root"
        return spec, float(row["answer_delay_s"]), root
    source_kind = "EI" if kind == "05_EI" else "TL"
    spec, delay, root = v3_runner.spec_for(source_kind)
    return replace(spec, episode_id=run_id), delay, root


def execute(kind: str, timeout_seconds: float, engineering_retry: int = 0) -> Mapping[str, Any]:
    _manifest_preflight()
    base_run_id = CASES[kind]["run_id"]
    run_id = (
        base_run_id.replace("-0815", "-ER{}-0815".format(engineering_retry))
        if engineering_retry
        else base_run_id
    )
    spec, answer_delay, scenario_root = spec_for(kind, run_id)
    output = ARTIFACT / run_id
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("VISUAL_ACCEPTANCE_OUTPUT_NOT_EMPTY:" + str(output))
    overrides = {
        PERSISTENT_FLAG: "1",
        V3_FEATURE_FLAG: "1",
        "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
        "SCENARIO_RUNNER_ROOT": str(Path(scenario_root).resolve()),
    }
    fixture_ids = {
        "01_ACT": "E1R1-ACT-PHYS-001",
        "02_ACT_SHARED": "E1R1-ACT-PHYS-003",
        "03_ASK": "E1R1-ASK-PHYS-001",
        "04_WAIT": "E1R1-ASK-PHYS-001",
        "05_EI": "E1R1-ASK-PHYS-001",
        "06_TL": "E1R1-ASK-PHYS-001",
        "07_HARD_RULE": "E1R1-ACT-PHYS-003",
    }
    overrides["DRIVECLARIFY_E1R1_FIXTURE_ID"] = fixture_ids[kind]
    if kind == "07_HARD_RULE":
        overrides[CONVERGENCE_OBSERVER_ENV] = "1"
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            result = run_grounded(
                output,
                case=CASES[kind]["case"],
                seed=spec.seed,
                method_id="driveclarify_grounded_v1",
                device="cpu",
                control=True,
                answer="The nearer white van.",
                answer_delay=answer_delay,
                timeout_seconds=timeout_seconds,
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides=overrides,
                completion_predicate=lambda live: complete(kind, live),
                desktop_capture_predicate=lambda live: capture_ready(kind, live),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)
    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else {}
    return {
        "case": kind,
        "run_id": run_id,
        "accepted_behavior_observed": complete(kind, live),
        "desktop_capture_status": result.get("desktop_capture_status"),
        "native_runner_status": result.get("status"),
        "cleanup_status": result.get("cleanup_status"),
        "evaluator_return_code": result.get("evaluator_return_code"),
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=tuple(CASES))
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--engineering-retry", type=int, default=0)
    parser.add_argument("--preflight-import-only", action="store_true")
    args = parser.parse_args()
    try:
        manifest = _manifest_preflight()
        spec, _, scenario_root = spec_for(args.case)
        if args.preflight_import_only:
            heavy = sorted(name for name in sys.modules if name.split(".", 1)[0] in {"carla", "torch"})
            value = {
                "status": "PASS" if not heavy else "BLOCKED_HEAVY_IMPORT",
                "case": args.case,
                "run_id": CASES[args.case]["run_id"],
                "scenario_id": spec.scenario_id,
                "seed": spec.seed,
                "scenario_root": str(scenario_root),
                "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
                "manifest_recorded_sha256": manifest.get("manifest_sha256_recorded_externally"),
                "heavy_modules": heavy,
            }
            print(json.dumps(value, indent=2, sort_keys=True))
            return 0 if not heavy else 2
        value = execute(args.case, args.timeout_seconds, args.engineering_retry)
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if value["accepted_behavior_observed"] and value["desktop_capture_status"] == "PASS_VALIDATED_NATIVE_DESKTOP" else 3
    except Exception as error:
        print(json.dumps({
            "status": "BLOCKED_VISUAL_ACCEPTANCE_ENGINEERING_FAILURE",
            "case": args.case,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
