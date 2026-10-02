#!/usr/bin/env python3
"""Bounded R4.3 engineering discovery runner and offline qualification helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_r4_3_unified_remaining_opportunity_discovery_and_qualification"
PLAN = REPORT / "DISCOVERY_PLAN.json"
OUTPUT_ROOT = REPORT / "discovery_attempts"
SITE_ROOT = ROOT / "tools/r4_3_candidate_capture_site"
ATTEMPT_JOURNAL = REPORT / "DISCOVERY_ATTEMPTS.jsonl"
EXPECTED_CANDIDATE = "5a1ae5d0251028fff6679120dd2ba15d0de265fe77e950bf82435e611a221a3e"
OLD_VULKAN_FINGERPRINT = "244f611913006aa77c42a50bdb23acc029f745d6acbecadb17aa2b0927b2ab8a"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def geometry_qualification(
    path_points: Iterable[Iterable[Any]], speed_points: Iterable[Iterable[Any]], *, expected_points: int = 20
) -> Mapping[str, Any]:
    try:
        route = [[float(value) for value in row] for row in path_points]
        speed = [[float(value) for value in row] for row in speed_points]
    except (TypeError, ValueError, OverflowError):
        return {"status": "INVALID", "reason_code": "NON_NUMERIC_GEOMETRY"}
    finite = bool(route) and all(len(row) >= 2 and all(math.isfinite(value) for value in row[:2]) for row in route)
    speed_finite = bool(speed) and all(bool(row) and all(math.isfinite(value) for value in row) for row in speed)
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(route, route[1:])] if finite else []
    unique = len({(row[0], row[1]) for row in route}) if finite else 0
    arc = sum(lengths)
    reasons = []
    if len(route) != expected_points:
        reasons.append("EXPECTED_POINT_COUNT_NOT_MET")
    if not finite or not speed_finite:
        reasons.append("NONFINITE_OR_MISSING_PATH_OR_SPEED")
    if unique < 2 or arc <= 1e-6 or (lengths and all(value <= 1e-6 for value in lengths)):
        reasons.append("DEGENERATE_REPEATED_PATH")
    return {
        "status": "VALID" if not reasons else "INVALID",
        "reason_code": "PASS_EXISTING_20_POINT_NONZERO_ARC_CONTRACT" if not reasons else reasons[0],
        "reason_codes": reasons,
        "unique_point_count": unique,
        "arc_length_m": arc,
        "degenerate_point_count": sum(value <= 1e-6 for value in lengths),
    }


def identity_pair_qualification(left: Mapping[str, Any], right: Mapping[str, Any]) -> Mapping[str, Any]:
    identity_fields = ("actor_id", "track_id", "map_landmark_id", "topology_target_id")
    left_ids = {field: left.get(field) for field in identity_fields if left.get(field) is not None}
    right_ids = {field: right.get(field) for field in identity_fields if right.get(field) is not None}
    if not left_ids or not right_ids:
        return {"status": "UNKNOWN", "reason_code": "STABLE_PHYSICAL_IDENTITY_UNAVAILABLE"}
    same_physical = any(field in right_ids and right_ids[field] == value for field, value in left_ids.items())
    digest_fields = ("target_digest", "branch_digest", "obligation_digest")
    digests_complete = all(left.get(field) is not None and right.get(field) is not None for field in digest_fields)
    same_obligation = digests_complete and all(left[field] == right[field] for field in digest_fields)
    if same_physical and same_obligation:
        return {"status": "DUPLICATE", "reason_code": "SAME_PHYSICAL_IDENTITY_AND_TARGET_BRANCH_OBLIGATION"}
    if not digests_complete:
        return {"status": "UNKNOWN", "reason_code": "OBLIGATION_DIGESTS_INCOMPLETE"}
    return {"status": "DISTINCT", "reason_code": "AUDITABLE_PHYSICAL_AND_OBLIGATION_DISTINCTNESS"}


def route_background_qualification(
    *, planned_route_identity: Any, runtime_route_identity: Any, background_inventory_sealed: bool
) -> Mapping[str, Any]:
    reasons = []
    if not planned_route_identity or runtime_route_identity != planned_route_identity:
        reasons.append("FAIL_ROUTE_IDENTITY_MISMATCH")
    if not background_inventory_sealed:
        reasons.append("UNSEALED_BACKGROUND_ACTOR_INVENTORY")
    return {"status": "PASS" if not reasons else "FAIL", "reason_codes": reasons}


def receipt_semantics_v2(
    native_receipt: Mapping[str, Any], *, scientific_target_observed: bool, success_criteria_met: bool
) -> Mapping[str, Any]:
    return {
        "protocol_execution_terminal_reached": bool(
            native_receipt.get("evaluator_return_code") is not None
            or native_receipt.get("termination_reason") not in (None, "UNKNOWN")
        ),
        "scientific_target_observed": bool(scientific_target_observed),
        "success_criteria_met": bool(success_criteria_met),
        "external_completion_observed": bool(native_receipt.get("external_completion_observed") is True),
        "legacy_aggregate": native_receipt.get("completion_satisfied"),
    }


def _load_plan() -> Mapping[str, Any]:
    value = json.loads(PLAN.read_text(encoding="utf-8"))
    if value.get("family_id") != "DCR43_REMAINING_OPPORTUNITY_DISCOVERY_V1":
        raise RuntimeError("R43_DISCOVERY_FAMILY_MISMATCH")
    if value.get("protocol_version") != "R4.3-REMAINING-OPPORTUNITY-DISCOVERY-V1":
        raise RuntimeError("R43_DISCOVERY_PROTOCOL_VERSION_MISMATCH")
    if canonical_sha256(value["protocol_preimage"]) != value.get("protocol_hash"):
        raise RuntimeError("R43_DISCOVERY_PROTOCOL_HASH_MISMATCH")
    if len(value["protocol_preimage"]["attempts"]) > 8:
        raise RuntimeError("R43_DISCOVERY_LAUNCH_BUDGET_EXCEEDED")
    for relative, expected in value.get("protected_inputs", {}).items():
        if sha256(ROOT / relative) != expected:
            raise RuntimeError("R43_DISCOVERY_INPUT_HASH_MISMATCH:" + relative)
    return value


def _attempt(plan: Mapping[str, Any], attempt_id: str) -> Mapping[str, Any]:
    rows = [row for row in plan["protocol_preimage"]["attempts"] if row["attempt_id"] == attempt_id]
    if len(rows) != 1:
        raise RuntimeError("R43_DISCOVERY_ATTEMPT_BINDING_NOT_ONE:" + attempt_id)
    prior = [row for row in read_jsonl(ATTEMPT_JOURNAL) if row.get("attempt_id") == attempt_id]
    if prior:
        raise RuntimeError("R43_DISCOVERY_ATTEMPT_ALREADY_RECORDED:" + attempt_id)
    return rows[0]


def _build_spec(row: Mapping[str, Any]):
    from tools.run_visual_behavioral_acceptance_v1 import spec_for

    kind = str(row["kind"])
    if kind in {"ACT_SHARED", "ASK_WAIT"}:
        visual_kind = "02_ACT_SHARED" if kind == "ACT_SHARED" else "04_WAIT"
        runner_case = "act" if kind == "ACT_SHARED" else "ask"
        fixture_id = "E1R1-ACT-PHYS-003" if kind == "ACT_SHARED" else "E1R1-ASK-PHYS-001"
        base, _, _ = spec_for(visual_kind, str(row["attempt_id"]))
        route = ROOT / str(row["route_path"])
        spec = replace(
            base,
            episode_id=str(row["attempt_id"]),
            scenario_id=str(row["case_id"]),
            seed=int(row["seed"]),
            town="Town03",
            route_id=str(row["route_id"]),
            route_path=route.resolve(),
            raw_instruction=str(row["instruction"]),
            information_expected=kind == "ASK_WAIT",
            schedule_sha256=sha256(route),
        )
        scenario_root = REPORT / "scenario_root"
        answer = str(row.get("answer", "The nearer white van."))
        delay = float(row.get("answer_delay_seconds", 0.1))
    else:
        visual_kind, runner_case, fixture_id = "07_HARD_RULE", "act", "E1R1-ACT-PHYS-003"
        base, _, scenario_root = spec_for(visual_kind, str(row["attempt_id"]))
        spec = replace(base, episode_id=str(row["attempt_id"]), seed=int(row["seed"]))
        answer, delay = "The nearer white van.", 0.1
    return visual_kind, runner_case, fixture_id, spec, Path(scenario_root), answer, delay


def _overrides(row: Mapping[str, Any], visual_kind: str, fixture_id: str, scenario_root: Path) -> Mapping[str, str]:
    from driveclarify_decision_evidence_v3 import FEATURE_FLAG as v3_flag
    from driveclarify_persistent_ambiguity_runtime_v1.convergence_observer import CONVERGENCE_OBSERVER_ENV
    from driveclarify_persistent_ambiguity_runtime_v1.runtime import FEATURE_FLAG as persistent_flag
    from driveclarify_paper_mvp_stage6b import backend as native

    values = {
        persistent_flag: "1",
        v3_flag: "1",
        "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
        "DRIVECLARIFY_E1R1_FIXTURE_ID": fixture_id,
        "SCENARIO_RUNNER_ROOT": str(scenario_root.resolve()),
        "PYTHONPATH": str(SITE_ROOT.resolve()) + os.pathsep + native._pythonpath(),
        "DRIVECLARIFY_R43_CAPTURE_ROOT": str(REPORT.resolve()),
        "DRIVECLARIFY_R43_DISCOVERY_ATTEMPT_ID": str(row["attempt_id"]),
        "DRIVECLARIFY_R43_SEED": str(row["seed"]),
        "DRIVECLARIFY_R43_MAP_IDENTITY": "Town03",
        "DRIVECLARIFY_R43_ROUTE_IDENTITY": str(row["route_identity"]),
    }
    if visual_kind == "07_HARD_RULE":
        values[CONVERGENCE_OBSERVER_ENV] = "1"
    return values


def _fallback_complete(live: Mapping[str, Any]) -> bool:
    method = live.get("method_v1_decision_history") or []
    gates = live.get("hard_gate_evidence_history") or []
    return bool(
        any(row.get("decision_label") == "FALLBACK" for row in method)
        and any(
            isinstance(row.get("route_local_hard_rule"), Mapping)
            and row["route_local_hard_rule"].get("status") == "VERIFIED_ROUTE_LOCAL_BLOCKED"
            and row.get("runtime_provider_identity_match") is True
            for row in gates
        )
    )


def _world_source_inventory(output: Path, source_observation_id: Any, kind: str) -> Mapping[str, Any]:
    path = output / "post_hoc_world_state.jsonl"
    rows = read_jsonl(path)
    matches = [row for row in rows if row.get("observation_id") == source_observation_id]
    if not matches:
        return {"sealed": False, "reason_code": "SOURCE_FRAME_WORLD_INVENTORY_MISSING", "actors": []}
    actors = matches[0].get("actors", {}).get("actors", [])
    if kind in {"ACT_SHARED", "ASK_WAIT"}:
        expected = {
            ("vehicle.mercedes.sprinter", 62.10994854976513, 196.87608650932077),
            ("vehicle.volkswagen.t2_2021", 32.10130877971777, 189.95095305914214),
        }
        observed = {(str(row.get("type_id")), round(float(row["location_xyz"][0]), 3), round(float(row["location_xyz"][1]), 3)) for row in actors}
        wanted = {(name, round(x, 3), round(y, 3)) for name, x, y in expected}
        sealed = observed == wanted
        reason = "EXACT_TWO_SCRIPTED_LANDMARKS_IN_50M" if sealed else "UNSEALED_OR_UNEXPECTED_ACTOR_IN_50M"
    else:
        sealed, reason = False, "FALLBACK_BACKGROUND_INVENTORY_NOT_SCRIPT_OWNERSHIP_SEALED"
    return {"sealed": sealed, "reason_code": reason, "actors": actors, "source_frame_row": matches[0]}


def qualify_output(row: Mapping[str, Any], output: Path) -> Mapping[str, Any]:
    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    native_path = output / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else {}
    native = json.loads(native_path.read_text(encoding="utf-8")) if native_path.is_file() else {}
    candidate_rows = [item for item in read_jsonl(REPORT / "CANDIDATE_FORWARD_EVIDENCE.jsonl") if item.get("discovery_attempt_id") == row["attempt_id"]]
    pair = identity_pair_qualification(candidate_rows[0], candidate_rows[1]) if len(candidate_rows) == 2 else {"status": "UNKNOWN", "reason_code": "EXACT_TWO_DURABLE_CANDIDATES_NOT_AVAILABLE"}
    geometry = [item.get("candidate_validity") for item in candidate_rows]
    source_observation = candidate_rows[0].get("source_observation_id") if candidate_rows else None
    inventory = _world_source_inventory(output, source_observation, str(row["kind"]))
    route_background = route_background_qualification(
        planned_route_identity=row.get("route_identity"),
        runtime_route_identity=live.get("runtime_route_version"),
        background_inventory_sealed=bool(inventory["sealed"]),
    )
    method = live.get("method_v1_decision_history") or []
    persistent = live.get("persistent_decision_history") or []
    decisions = [item.get("decision_label", item.get("decision")) for item in method + persistent]
    gate_history = live.get("hard_gate_evidence_history") or []
    latest_gate = gate_history[-1] if gate_history else {}
    route_local = latest_gate.get("route_local_hard_rule")
    route_local_status = route_local.get("status") if isinstance(route_local, Mapping) else None
    target_observed = False
    reasons = []
    kind = str(row["kind"])
    if pair["status"] != "DISTINCT":
        reasons.append(pair["reason_code"])
    if len(candidate_rows) != 2 or any(value != "VALID" for value in geometry):
        reasons.append("EXACT_TWO_VALID_DURABLE_CANDIDATE_OUTPUTS_NOT_AVAILABLE")
    reasons.extend(route_background["reason_codes"])
    if kind == "ACT_SHARED":
        target_observed = "ACT_SHARED" in decisions
        if not target_observed:
            reasons.append("ACT_SHARED_DECISION_NOT_OBSERVED")
    elif kind == "ASK_WAIT":
        ask = "ASK" in decisions
        wait = "WAIT" in decisions and bool(live.get("active_query_id") or live.get("query_lifecycle"))
        target_observed = ask and wait
        if not ask:
            reasons.append("QUALIFIED_ASK_NOT_OBSERVED")
        if not wait:
            reasons.append("QUERY_BOUND_WAIT_CHAIN_NOT_OBSERVED")
    else:
        target_observed = _fallback_complete(live)
        if not target_observed:
            reasons.append("DETERMINISTIC_ROUTE_LOCAL_BLOCKED_TO_FALLBACK_NOT_OBSERVED")
    qualified = bool(target_observed and not reasons)
    semantics = receipt_semantics_v2(native, scientific_target_observed=target_observed, success_criteria_met=qualified)
    return {
        "qualification": "QUALIFIED_ENGINEERING_DISCOVERY" if qualified else "UNKNOWN" if pair["status"] == "UNKNOWN" else "UNQUALIFIED",
        "qualification_reasons": list(dict.fromkeys(reasons)),
        "candidate_output_hashes": [
            {"route": item.get("raw_pred_route", {}).get("sha256"), "speed": item.get("raw_pred_speed_wps", {}).get("sha256")}
            for item in candidate_rows
        ],
        "physical_identity": pair,
        "route_identity": {"planned": row.get("route_identity"), "runtime": live.get("runtime_route_version")},
        "background_seal": {"sealed": inventory["sealed"], "reason_code": inventory["reason_code"]},
        "route_local_status": route_local_status,
        "relation_status": (live.get("persistent_decision_window") or {}).get("candidate_relationship"),
        "timing_status": (live.get("persistent_decision_window") or {}).get("latest_safe_slack_s"),
        "source_frame_ids": sorted({item.get("source_frame_id") for item in candidate_rows}, key=str),
        "receipt_semantics_v2": semantics,
    }


def execute(attempt_id: str, *, preflight_only: bool = False) -> Mapping[str, Any]:
    plan = _load_plan()
    row = _attempt(plan, attempt_id)
    visual_kind, runner_case, fixture_id, spec, scenario_root, answer, delay = _build_spec(row)
    output = OUTPUT_ROOT / attempt_id
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("R43_DISCOVERY_OUTPUT_NOT_EMPTY:" + str(output))
    overrides = _overrides(row, visual_kind, fixture_id, scenario_root)
    if preflight_only:
        from tools.run_grounded_language_v1_triad import environment, preflight
        values = environment(spec, output, case=runner_case, device="cpu", control=True, answer=answer, answer_delay=delay, visualization=True, post_hoc_world_state=True)
        values.update(overrides)
        result = preflight(spec, output, values, case=runner_case, control=True)
        return {"status": result["status"], "attempt_id": attempt_id, "preflight": result}
    from driveclarify_grounded_language_v1_extension_e1_r1 import backend
    from tools.run_grounded_language_v1_triad import run
    from tools.run_visual_behavioral_acceptance_v1 import capture_ready, complete

    lease = None
    try:
        with backend.serial_gpu_lease() as current_lease:
            lease = current_lease
            native = run(
                output,
                case=runner_case,
                seed=spec.seed,
                method_id="driveclarify_r4_3_engineering_discovery_only",
                device="cpu",
                control=True,
                answer=answer,
                answer_delay=delay,
                timeout_seconds=600.0,
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides=overrides,
                completion_predicate=(lambda live: _fallback_complete(live)) if row["kind"] == "FALLBACK" else (lambda live: complete(visual_kind, live)),
                desktop_capture_predicate=(lambda live: _fallback_complete(live)) if row["kind"] == "FALLBACK" else (lambda live: capture_ready(visual_kind, live)),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease is not None and output.is_dir():
            backend._write_lease(output, lease)
    log_path = output / "evaluator_stdout.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    if "FVulkanSurface::EvictSurface" in log_text or OLD_VULKAN_FINGERPRINT in log_text:
        status = "BLOCKED_R4_3_SYSTEMIC_VULKAN_RECURRENCE"
        qualification = {"qualification": "UNKNOWN", "qualification_reasons": [status]}
    else:
        qualification = qualify_output(row, output)
        status = qualification["qualification"]
    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else {}
    attempt_receipt = {
        "schema_version": "driveclarify.r4_3.discovery_attempt.v1",
        "purpose": "ENGINEERING_DISCOVERY_ONLY",
        "attempt_id": attempt_id,
        "case_id": row["case_id"],
        "kind": row["kind"],
        "seed": row["seed"],
        "status": status,
        "candidate_identity": EXPECTED_CANDIDATE,
        "scientific_exposure": {
            "observations": int(live.get("carla_tick_count") or live.get("dashboard_refresh_count") or 0),
            "normal_forwards": int(live.get("normal_simlingo_forward_count") or 0),
            "candidate_forwards": int(live.get("candidate_simlingo_forward_count") or 0),
            "decisions": max(len(live.get("method_v1_decision_history") or []), len(live.get("persistent_decision_history") or [])),
            "existing_pid_controls": int(live.get("existing_pid_invocation_count") or 0),
            "vehicle_scientific_movement": int(live.get("control_observation_count") or 0),
        },
        "qualification_evidence": qualification,
        "native_receipt": native,
        "output": str(output.relative_to(ROOT)),
    }
    append_jsonl(ATTEMPT_JOURNAL, attempt_receipt)
    return attempt_receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("attempt_id")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    try:
        result = execute(args.attempt_id, preflight_only=args.preflight_only)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("status") in {"PASS", "QUALIFIED_ENGINEERING_DISCOVERY"} else 3
    except Exception as error:
        print(json.dumps({
            "status": "BLOCKED_R4_3_DISCOVERY_RUNNER",
            "attempt_id": args.attempt_id,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
