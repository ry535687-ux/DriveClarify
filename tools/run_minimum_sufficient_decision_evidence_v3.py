#!/usr/bin/env python3
"""Run one gated visible-native V3 preservation case."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import traceback
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_decision_evidence_v3 import FEATURE_FLAG as V3_FEATURE_FLAG  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from driveclarify_persistent_ambiguity_runtime_v1.runtime import FEATURE_FLAG as V1_FEATURE_FLAG  # noqa: E402
from driveclarify_train_decision_activation_scenario_revision_v1.contracts import scenario as activation_scenario  # noqa: E402
from driveclarify_v2_negative_control_reconstruction_v1.contracts import scenario as negative_scenario  # noqa: E402
from driveclarify_v2_negative_control_reconstruction_v2.contracts import scenario as negative_scenario_v2  # noqa: E402
from tools import run_method_v1_act_semantics_targeted_native_validation as old_targeted  # noqa: E402
from tools import run_short_horizon_decision_evidence_v2 as old_v2  # noqa: E402
from tools import run_v2_negative_control_v1 as old_negative  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402


REPORT = ROOT / "reports/driveclarify_minimum_sufficient_decision_evidence_v3_revision_and_native_validation"
ARTIFACT = ROOT / "artifacts/driveclarify_minimum_sufficient_decision_evidence_v3_revision_and_native_validation"
CASES = {
    "B": {"run_id": "DCV3-B-I-0814", "fixture_id": "E1R1-ACT-PHYS-003", "seed": 6201, "receipt": "CASE_B_V3_NATIVE_RECEIPT.json", "case": "act", "answer_delay": 0.1},
    "B_REPEAT": {"run_id": "DCV3-B-R-0814", "fixture_id": "E1R1-ACT-PHYS-003", "seed": 6202, "receipt": "CASE_B_V3_REPEAT_RECEIPT.json", "case": "act", "answer_delay": 0.1},
    "B_V31": {"run_id": "DCV31-B-I-0814", "fixture_id": "E1R1-ACT-PHYS-003", "seed": 6201, "receipt": "CASE_B_V3_1_NATIVE_RECEIPT.json", "case": "act", "answer_delay": 0.1},
    "B_V31_R2": {"run_id": "DCV31-B-I2-0814", "fixture_id": "E1R1-ACT-PHYS-003", "seed": 6201, "receipt": "CASE_B_V3_1_NATIVE_RECEIPT_R2.json", "case": "act", "answer_delay": 0.1},
    "B_V31_REPEAT": {"run_id": "DCV31-B-R-0814", "fixture_id": "E1R1-ACT-PHYS-003", "seed": 6202, "receipt": "CASE_B_V3_1_REPEAT_RECEIPT.json", "case": "act", "answer_delay": 0.1},
    "ASK": {"run_id": "DCV3-ASK-P-0814", "scenario_id": "DA-ASK-001", "receipt": "ASK_V3_PRESERVATION_RECEIPT.json", "case": "ask"},
    "ASK_R2": {"run_id": "DCV31-ASK-P2-0814", "scenario_id": "DA-ASK-001", "receipt": "ASK_V3_PRESERVATION_RECEIPT_R2.json", "case": "ask"},
    "EI": {"run_id": "DCV3-EI-P-0814", "scenario_id": "NC-EI-001", "receipt": "NEGATIVE_V3_PRESERVATION_RECEIPT.json", "case": "ask"},
    "TL": {"run_id": "DCV3-TL-P-0814", "scenario_id": "NC-TL2-001", "receipt": "NEGATIVE_V3_PRESERVATION_RECEIPT.json", "case": "ask"},
}


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(dict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _history(live: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = live.get("persistent_decision_history")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _events(live: Mapping[str, Any]) -> set[str]:
    episode = live.get("persistent_ambiguity_episode")
    return {
        str(row.get("event_type")) for row in episode.get("history", ())
        if isinstance(row, Mapping)
    } if isinstance(episode, Mapping) else set()


def _axis_ready(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("decision_evidence_contract_version") in {"3.0", "3.1"}
        and row.get("current_action_relation") in {"SHARED", "DIVERGENT", "UNKNOWN"}
        and row.get("future_obligation_relation") in {"EQUIVALENT", "DIVERGENT", "UNKNOWN"}
        and row.get("clarification_state") in {"NOT_NEEDED_YET", "CLARIFY_NOW", "TOO_LATE", "UNKNOWN"}
        and row.get("precommitment_refresh_guarantee") in {"GUARANTEED", "NOT_GUARANTEED", "UNKNOWN"}
        and isinstance(row.get("shared_action_lease"), Mapping)
    )


def complete(kind: str, live: Mapping[str, Any]) -> bool:
    history = _history(live)
    events = _events(live)
    if live.get("decision_evidence_contract_version") != "3.1":
        return False
    if kind in {"B", "B_REPEAT", "B_V31", "B_V31_R2", "B_V31_REPEAT"}:
        rows = [row for row in history if row.get("decision") == "ACT_SHARED" and _axis_ready(row)]
        first = rows[0] if rows else None
        return bool(
            first and first.get("current_action_relation") == "SHARED"
            and first.get("future_obligation_relation") in {"UNKNOWN", "EQUIVALENT"}
            and first.get("clarification_state") != "CLARIFY_NOW"
            and first.get("clarification_state") != "TOO_LATE"
            and first.get("recoverability") == "RECOVERABLE"
            and first.get("precommitment_refresh_guarantee") == "GUARANTEED"
            and first.get("shared_action_lease", {}).get("valid") is True
            and first.get("authority_subject_type") == "SHARED_EQUIVALENCE_CLASS"
            and "DECISION_ACT_SHARED" in events
            and live.get("persistent_ambiguity_episode", {}).get("semantic_state") == "UNRESOLVED"
            and live.get("semantic_resolution_from_act_shared") is False
        )
    if kind in {"ASK", "ASK_R2"}:
        rows = [row for row in history if row.get("decision") == "ASK" and _axis_ready(row)]
        return bool(rows and rows[0].get("future_obligation_relation") == "DIVERGENT" and rows[0].get("clarification_state") == "CLARIFY_NOW" and "ASK_ISSUED" in events)
    if kind == "EI":
        rows = [row for row in history if row.get("decision") == "FALLBACK" and _axis_ready(row)]
        return bool(len(rows) >= 3 and any(row.get("future_obligation_relation") == "UNKNOWN" and row.get("recoverability") == "UNKNOWN" for row in rows))
    rows = [row for row in history if row.get("decision") == "FALLBACK" and _axis_ready(row)]
    return bool(len(rows) >= 3 and any(
        row.get("clarification_state") == "TOO_LATE"
        or row.get("current_action_relation") == "DIVERGENT"
        or row.get("precommitment_refresh_guarantee") == "NOT_GUARANTEED"
        for row in rows
    ))


def desktop_ready(kind: str, live: Mapping[str, Any]) -> bool:
    """Read-only evidence-visibility predicate, independent of scientific success."""
    dashboard = live.get("decision_window_dashboard")
    return bool(
        isinstance(dashboard, Mapping)
        and dashboard.get("decision_evidence_contract_version") == "3.1"
        and dashboard.get("CurrentActionRelation") is not None
        and dashboard.get("FutureObligationRelation") is not None
        and dashboard.get("ClarificationState") is not None
        and dashboard.get("SharedActionLease") is not None
        and dashboard.get("Recoverability") is not None
        and dashboard.get("RefreshGuarantee") is not None
    )


def spec_for(kind: str):
    row = CASES[kind]
    if kind in {"B", "B_REPEAT", "B_V31", "B_V31_R2", "B_V31_REPEAT"}:
        old_targeted._manifest_preflight()
        spec = backend.resolve_episode(
            fixture_id=row["fixture_id"], method_id="driveclarify_grounded_v1",
            episode_id=row["run_id"],
        )
        return replace(spec, seed=row["seed"]), 0.1, old_targeted.SCENARIO_ROOT
    if kind in {"ASK", "ASK_R2"}:
        scenario_id = row["scenario_id"]
        activation = activation_scenario(scenario_id)
        spec = replace(old_v2.frozen_v1.episode_spec(scenario_id, False), episode_id=row["run_id"])
        return spec, float(activation["answer_delay_s"]), ROOT / "driveclarify_train_decision_activation_scenario_revision_v1/scenario_root"
    scenario_id = row["scenario_id"]
    if kind == "EI":
        negative = negative_scenario(scenario_id)
        spec = replace(old_negative.episode_spec(scenario_id, False), episode_id=row["run_id"])
        return spec, float(negative["answer_delay_s"]), old_negative.SCENARIO_ROOT
    negative = negative_scenario_v2(scenario_id)
    report = ROOT / "reports/driveclarify_v2_negative_control_reconstruction_and_validation_v1"
    manifest_path = report / "TRAIN_V2_NEGATIVE_CONTROL_MANIFEST_V2.json"
    freeze = json.loads((report / "PREFREEZE_HASH_RECEIPT_V2.json").read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != freeze["manifest_file_sha256"]:
        raise RuntimeError("V3_TL_SOURCE_MANIFEST_HASH_MISMATCH")
    entries = [value for value in manifest["scenarios"] if value["scenario_id"] == scenario_id]
    if len(entries) != 1:
        raise RuntimeError("V3_TL_SOURCE_MANIFEST_BINDING_NOT_ONE")
    entry = entries[0]
    for key in ("fixture_xml", "physical_json"):
        if hashlib.sha256((ROOT / entry[key]).read_bytes()).hexdigest() != entry[key + "_sha256"]:
            raise RuntimeError("V3_TL_SOURCE_FIXTURE_HASH_MISMATCH:" + key)
    base = backend.resolve_episode(
        fixture_id="E1R1-ASK-PHYS-001", method_id="driveclarify_grounded_v1",
        episode_id=row["run_id"],
    )
    route_path = (ROOT / entry["fixture_xml"]).resolve()
    spec = replace(
        base, episode_id=row["run_id"], scenario_id=scenario_id, split="train",
        seed=int(entry["seed"]), town=negative["town"], route_id=negative["route_id"],
        route_path=route_path, raw_instruction=negative["instruction"],
        schedule_sha256=hashlib.sha256(route_path.read_bytes()).hexdigest(),
    )
    scenario_root = ROOT / "driveclarify_v2_negative_control_reconstruction_v2/scenario_root"
    return spec, float(negative["answer_delay_s"]), scenario_root


def _receipt(kind: str, output: Path, native: Mapping[str, Any]) -> dict[str, Any]:
    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else {}
    history = _history(live)
    matching = {
        "B": "ACT_SHARED", "B_REPEAT": "ACT_SHARED",
        "B_V31": "ACT_SHARED", "B_V31_R2": "ACT_SHARED",
        "B_V31_REPEAT": "ACT_SHARED", "ASK": "ASK", "ASK_R2": "ASK",
        "EI": "FALLBACK", "TL": "FALLBACK",
    }[kind]
    selected = next((row for row in history if row.get("decision") == matching and _axis_ready(row)), {})
    valid_desktop = bool(
        native.get("desktop_capture_status") == "PASS_VALIDATED_NATIVE_DESKTOP"
        and desktop_ready(kind, live)
    )
    valid_scientific_run = bool(
        live
        and live.get("closed") is True
        and not live.get("errors")
        and valid_desktop
        and native.get("status") == "PASS_NATIVE_UNIFIED_TRIAD_CAPTURE_AND_CLEANUP"
        and native.get("cleanup_status") == "PASS"
        and native.get("evaluator_return_code") == 0
    )
    passed = bool(complete(kind, live) and valid_scientific_run)
    return {
        "schema_version": "driveclarify.minimum_sufficient_decision_evidence_v3.native_case.v1",
        "created_at_utc": _utc(), "case": kind, "run_id": CASES[kind]["run_id"],
        "split": "TRAIN", "status": "PASS" if passed else "SCIENTIFIC_OUTCOME_NOT_ACCEPTED" if valid_scientific_run else "ENGINEERING_NATIVE_OR_VISIBILITY_INVALID",
        "valid_scientific_run": valid_scientific_run,
        "native_runner_status": native.get("status"), "live_status": live.get("status"),
        "K": len(selected.get("candidate_ids") or live.get("active_candidate_ids") or ()),
        "current_action_relation_v3": selected.get("current_action_relation"),
        "future_obligation_relation_v3": selected.get("future_obligation_relation"),
        "clarification_state_v3": selected.get("clarification_state"),
        "shared_action_lease_v3": selected.get("shared_action_lease"),
        "recoverability_v3": selected.get("recoverability"),
        "recoverability_by_candidate": selected.get("recoverability_by_candidate"),
        "refresh_guarantee_v3": selected.get("precommitment_refresh_guarantee"),
        "decision": selected.get("decision"), "reason_codes": selected.get("decision_reason_codes"),
        "decision_subject": selected.get("authority_subject_type"),
        "semantic_state": selected.get("semantic_state"),
        "semantic_resolution_from_act_shared": live.get("semantic_resolution_from_act_shared"),
        "decision_counts": dict(Counter(row.get("decision") for row in history)),
        "normal_forwards": live.get("normal_simlingo_forward_count"),
        "candidate_forwards": live.get("candidate_simlingo_forward_count"),
        "fresh_forwards": live.get("convergence_fresh_unique_forward_count", 0),
        "dino_forwards": live.get("grounding", {}).get("detector_forward_count") if isinstance(live.get("grounding"), Mapping) else None,
        "visualization_extra_forwards": live.get("visualization_extra_forward_count"),
        "hidden_forwards": live.get("hidden_convergence_forward_count", 0),
        "new_pid": live.get("new_pid_count"), "new_planner": live.get("new_planner_count"),
        "new_writer": live.get("direct_vehicle_control_write_count"),
        "candidate_direct_control_writes": live.get("candidate_direct_vehicle_control_write_count"),
        "expected_decision_reads": live.get("expected_decision_reads"),
        "gold_policy_label_reads": live.get("gold_policy_label_reads"),
        "privileged_state_policy_reads": live.get("privileged_state_policy_read_count"),
        "collisions": native.get("vehicle_collision_count"), "red_lights": native.get("red_light_count"),
        "desktop_capture_status": native.get("desktop_capture_status"),
        "live_receipt": str(live_path.relative_to(ROOT)) if live_path.is_file() else None,
        "native_receipt": str((output / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json").relative_to(ROOT)),
        "axis_trace": [{
            key: row.get(key) for key in (
                "sequence", "planning_event_id", "bundle_id", "current_progress_m",
                "current_action_relation", "future_obligation_relation",
                "clarification_state", "shared_action_lease", "recoverability",
                "recoverability_by_candidate", "precommitment_refresh_guarantee",
                "decision", "decision_reason_codes", "authority_subject_type",
            )
        } for row in history],
        "dev_attempt_count": 0, "test_attempt_count": 0, "test_consumed": False,
    }


def execute(kind: str, timeout_seconds: float) -> Mapping[str, Any]:
    row = CASES[kind]
    output = ARTIFACT / row["run_id"]
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("V3_NATIVE_OUTPUT_NOT_EMPTY:" + str(output))
    spec, answer_delay, scenario_root = spec_for(kind)
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            native = run_grounded(
                output, case=row["case"], seed=spec.seed,
                method_id="driveclarify_grounded_v1", device="cpu", control=True,
                answer="The nearer white van.", answer_delay=answer_delay,
                timeout_seconds=timeout_seconds, episode_spec=spec,
                visualization=True, post_hoc_world_state=True,
                terminate_on_runtime_terminal=True, capture_desktop=True,
                environment_overrides={
                    V1_FEATURE_FLAG: "1", V3_FEATURE_FLAG: "1",
                    "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
                    "SCENARIO_RUNNER_ROOT": str(Path(scenario_root).resolve()),
                    "DRIVECLARIFY_E1R1_FIXTURE_ID": row.get("fixture_id", "E1R1-ASK-PHYS-001"),
                },
                completion_predicate=lambda live: complete(kind, live),
                desktop_capture_predicate=lambda live: desktop_ready(kind, live),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)
    receipt = _receipt(kind, output, native)
    if kind in {"EI", "TL"}:
        target = REPORT / row["receipt"]
        aggregate = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {
            "schema_version": "driveclarify.minimum_sufficient_decision_evidence_v3.negative_preservation.v1",
            "runs": {},
        }
        aggregate["runs"][kind] = receipt
        aggregate["status"] = "PASS" if set(aggregate["runs"]) == {"EI", "TL"} and all(value.get("status") == "PASS" for value in aggregate["runs"].values()) else "IN_PROGRESS_OR_FAILED"
        _write(target, aggregate)
    else:
        _write(REPORT / row["receipt"], receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=tuple(CASES))
    parser.add_argument("--timeout-seconds", type=float, default=420.0)
    parser.add_argument("--preflight-import-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_import_only:
        spec, _, scenario_root = spec_for(args.case)
        heavy = sorted(name for name in sys.modules if name.split(".", 1)[0] in {"carla", "torch"})
        value = {"status": "PASS_V3_NATIVE_PREFLIGHT" if not heavy else "BLOCKED_HEAVY_IMPORT", "case": args.case, "run_id": CASES[args.case]["run_id"], "split": "TRAIN", "seed": spec.seed, "scenario_root": str(scenario_root), "heavy_modules": heavy}
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0 if not heavy else 2
    try:
        receipt = execute(args.case, args.timeout_seconds)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if receipt.get("status") == "PASS" else 3
    except Exception as error:
        value = {"status": "BLOCKED_V3_NATIVE_ENGINEERING_PRELAUNCH", "case": args.case, "valid_scientific_run": False, "error_type": type(error).__name__, "error": str(error), "traceback": traceback.format_exc()}
        print(json.dumps(value, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
