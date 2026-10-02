#!/usr/bin/env python3
"""Execute one prospectively registered V4 engineering identity once."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as episode_backend  # noqa: E402
from driveclarify_paper_mvp_stage6b import backend as native  # noqa: E402
from driveclarify_rq2_t_e2_v4.contracts import METHOD_ID, canonical_sha256  # noqa: E402
from driveclarify_rq2_t_e2_v4.lifecycle import (  # noqa: E402
    evaluate_activation_boundary, static_route_lifecycle_admission,
    validate_activation_receipt, validate_scenario_completion,
)
from driveclarify_rq2_t_e2_v4.scene_bindings import frozen_binding  # noqa: E402
from tools.run_grounded_language_v1_triad import environment as grounded_environment  # noqa: E402
from tools.run_rq2_t_e2_v3 import _repair_irrelevant_display_process_false_positive, _summary  # noqa: E402


REPORT = ROOT / "reports/driveclarify_rq2_t_v2_final_blind_domain_shift_redesign_v1"
REGISTRY = REPORT / "ENGINEERING_IDENTITY_REGISTRY.json"
ATTEMPT_LEDGER = REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl"
EXECUTION_LEDGER = REPORT / "ENGINEERING_EXECUTION_LEDGER.jsonl"
BLIND_LEDGER = REPORT / "FRESH_BLIND_EXECUTION_LEDGER.jsonl"
EVIDENCE_ROOT = REPORT / "NATIVE_EVIDENCE"
PARAMETERS = REPORT / "FINAL_PARAMETER_FREEZE.json"
SOURCE_FREEZE = REPORT / "SOURCE_FREEZE_RECEIPT.json"
PLACEHOLDER_RUNTIME = ROOT / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/dc-runtime-06cccfa4723c09ca09a9bc2b.json"
PROMOTION = native.PROMOTION_ROOT / "LIVE_PROMOTION_MANIFEST.json"
NO_PROGRESS_CONTAINMENT_S = 900.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[Mapping[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            rows.append(value)
    return rows


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def route_path(scene: str) -> Path:
    path = ROOT / "driveclarify_rq2_t_e2_v4/engineering_routes" / (scene.lower() + ".xml")
    if not path.is_file():
        raise RuntimeError("E2_V4_ENGINEERING_ROUTE_MISSING:" + scene)
    return path.resolve()


def registry_row(identity: str) -> Mapping[str, Any]:
    matches = [row for row in load(REGISTRY, {}).get("identities", ()) if row.get("identity") == identity]
    if len(matches) != 1:
        raise RuntimeError("E2_V4_ENGINEERING_IDENTITY_NOT_UNIQUELY_REGISTERED")
    return dict(matches[0])


def manifest_row(phase: str, scene: str) -> Mapping[str, Any]:
    name = "FRESH_BLIND_SCENE_MANIFEST.json" if phase == "BLIND" else phase + "_SCENE_MANIFEST.json"
    matches = [row for row in load(REPORT / name, {}).get("scenes", ()) if row.get("scene") == scene]
    if len(matches) != 1:
        raise RuntimeError("E2_V4_SCENE_NOT_IN_FROZEN_MANIFEST:" + scene)
    return dict(matches[0])


def admit(row: Mapping[str, Any]) -> Mapping[str, Any]:
    identity, scene, phase = str(row["identity"]), str(row["scene"]), str(row["phase"])
    if any(value.get("identity") == identity for value in jsonl(ATTEMPT_LEDGER)):
        raise RuntimeError("E2_V4_EXPOSED_IDENTITY_IMMUTABLE_NO_RERUN")
    binding = frozen_binding(scene)
    if binding["phase"] != phase:
        raise RuntimeError("E2_V4_IDENTITY_PHASE_MISMATCH")
    manifest = manifest_row(phase, scene)
    path = route_path(scene)
    if sha256(path) != manifest["route_sha256"]:
        raise RuntimeError("E2_V4_FROZEN_ROUTE_HASH_MISMATCH")
    if binding["scene_configuration_sha256"] != manifest["scene_configuration_sha256"]:
        raise RuntimeError("E2_V4_FROZEN_SCENE_HASH_MISMATCH")
    if phase == "BLIND":
        freeze = load(SOURCE_FREEZE, {})
        if freeze.get("blind_execution_authorized") is not True:
            raise RuntimeError("E2_V4_BLIND_COMPLETE_FREEZE_NOT_AUTHORIZED")
    admission = static_route_lifecycle_admission(
        route_path=path, binding=binding, expected_route_sha256=manifest["route_sha256"],
        expected_scene_configuration_sha256=manifest["scene_configuration_sha256"],
    )
    if admission["status"] != "PASS_STATIC_ROUTE_LIFECYCLE_ADMISSION":
        raise RuntimeError("E2_V4_STATIC_ROUTE_LIFECYCLE_ADMISSION_FAILED:" + json.dumps(admission, sort_keys=True))
    return admission


def build_spec(row: Mapping[str, Any]) -> Any:
    scene, binding = str(row["scene"]), frozen_binding(str(row["scene"]))
    schedule = PARAMETERS if PARAMETERS.is_file() else REPORT / "CALIBRATION_PROTOCOL.json"
    return native.EpisodeSpec(
        episode_id=str(row["identity"]), runtime_config_id="RQ2TE2V4-" + scene,
        scenario_id=scene, runtime_fixture_id="rq2-t-e2-v4-" + scene.lower(),
        split="train", seed=int(row["seed"]), method_id="original_simlingo",
        town=str(binding["town"]), route_id="RQ2TE2V4-" + scene,
        route_path=route_path(scene), runtime_manifest_path=PLACEHOLDER_RUNTIME.resolve(),
        raw_instruction=str(binding["instruction"]), information_expected=False,
        schedule_sha256=sha256(schedule), runtime_manifest_sha256=sha256(PLACEHOLDER_RUNTIME),
        promotion_receipt_path=PROMOTION.resolve(), promotion_receipt_payload_sha256=sha256(PROMOTION),
    )


def episode_environment(spec: Any, output: Path, row: Mapping[str, Any]) -> dict[str, str]:
    scene, binding = str(row["scene"]), frozen_binding(str(row["scene"]))
    values = grounded_environment(
        spec, output, case="act", device="cpu", control=False, answer="",
        answer_delay=0.5, visualization=False, post_hoc_world_state=True,
    )
    commitment = canonical_sha256({
        "scene_configuration_sha256": binding["scene_configuration_sha256"],
        "commitment_owner": "UNCHANGED_V1_RUNTIME_COMMITMENT_EVENT", "engineering_only": True,
    })
    configuration = None
    provenance = "PRECALIBRATION_FROZEN_GRID_MEMBER"
    if PARAMETERS.is_file():
        frozen = load(PARAMETERS, {})
        configuration = frozen.get("selected_configuration")
        provenance = "SCENE_LEVEL_CALIBRATION:" + str(frozen.get("freeze_receipt_digest"))
    values.update({
        "SCENARIO_RUNNER_ROOT": str(native.SCENARIO_DISCOVERY_ROOT),
        "DRIVECLARIFY_STAGE6A_GENERATED_ROOT": str(native.GENERATED_ROOT),
        "DRIVECLARIFY_STAGE6A_PROMOTION_ROOT": str(native.PROMOTION_ROOT),
        "DRIVECLARIFY_STAGE6A_SELECTED_SEED": "5101",
        "DRIVECLARIFY_RQ2_T_E2_V4_NATIVE_EVIDENCE": "1",
        "DRIVECLARIFY_RQ2_T_E2_V3_NATIVE_EVIDENCE": "0",
        "DRIVECLARIFY_RQ2_T_V2_NATIVE_EVIDENCE": "0",
        "DRIVECLARIFY_E2_V4_SCENE_ID": scene,
        "DRIVECLARIFY_E2_V4_EPISODE_ID": str(row["identity"]),
        "DRIVECLARIFY_E2_V4_ENGINEERING_SEED": str(row["seed"]),
        "DRIVECLARIFY_E2_V4_AMBIGUITY_TYPE": str(binding["family"]),
        "DRIVECLARIFY_E2_V4_MAP": str(binding["town"]),
        "DRIVECLARIFY_E2_V4_ROUTE_IDENTITY": "RQ2TE2V4-" + scene,
        "DRIVECLARIFY_E2_V4_COMMITMENT_CERTIFICATE_SHA256": commitment,
        "DRIVECLARIFY_E2_V4_CERTIFIED_CANDIDATES_JSON": json.dumps(binding["runtime_candidates"], ensure_ascii=False, sort_keys=True),
        "DRIVECLARIFY_E2_V4_CONFIGURATION_PROVENANCE": provenance,
        "DRIVECLARIFY_E2_V4_DETECTOR_DEVICE": "cpu",
        "DRIVECLARIFY_E2_V4_RGB_ARCHIVE_DIR": str(output / "RGB_0_ARCHIVE"),
        "DRIVECLARIFY_E2_V4_SCENARIO_RECEIPT": str(output / "E2_V4_SCENARIO_RECEIPT.json"),
        "DRIVECLARIFY_E2_V4_ACTIVATION_RECEIPT": str(output / "E2_V4_ACTIVATION_RECEIPT.json"),
        "DRIVECLARIFY_E2_V4_LIFECYCLE_ADMISSION_RECEIPT": str(output / "ROUTE_LIFECYCLE_ADMISSION.json"),
        "DRIVECLARIFY_PERSISTENT_AMBIGUITY_RUNTIME_V1": "1",
        "DRIVECLARIFY_DECISION_EVIDENCE_ARCHITECTURE_V3": "1",
        "DRIVECLARIFY_DECISION_EVIDENCE_CONTRACT_V2": "0",
        "DRIVECLARIFY_RQ2_T_TEMPORAL_OBSERVER": "0", "DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED": "0",
        "DRIVECLARIFY_METHOD_V1_CONVERGENCE_OBSERVER": "0", "DRIVECLARIFY_METHOD_REVISION_V2": "0",
        "DRIVECLARIFY_CANDIDATE_LIVE_ACT_AUTHORITY_V0": "0",
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
    })
    if configuration is not None:
        values["DRIVECLARIFY_E2_V4_CONFIGURATION_JSON"] = json.dumps(configuration, sort_keys=True)
    for key in tuple(values):
        upper = key.upper()
        if upper.endswith("_GOLD") or any(token in upper for token in (
            "QUERY_NECESSITY_GOLD", "EXPECTED_LABEL", "TRUE_INTERPRETATION",
            "FUTURE_REVEAL", "AUTHORED_REVEAL", "EXPECTED_MECHANISM_OUTCOME",
        )):
            values.pop(key, None)
    return values


def order_receipt_fields(paired: list[Mapping[str, Any]], scenario: Mapping[str, Any]) -> Mapping[str, Any]:
    available = []
    for row in paired:
        field = row.get("views", {}).get("B1", {}).get("evidence_vector", {}).get("E5_ROUTE_LANE_TOPOLOGY_RELATION", {})
        if field.get("status") == "AVAILABLE":
            available.append(field)
    counts = [len(field.get("value", {}).get("qualifying_opportunities", ())) for field in available]
    value = dict(scenario)
    value.update({
        "order_e5_owner_active": bool(available),
        "qualifying_junction_opportunity_count": max(counts, default=0),
        "local_topology_reveal_before_route_end": bool(available),
    })
    return value


def run_identity(identity: str, wall_timeout_s: float) -> Mapping[str, Any]:
    row = registry_row(identity)
    admission = admit(row)
    scene, phase = str(row["scene"]), str(row["phase"])
    output = EVIDENCE_ROOT / identity / "attempt_01"
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("E2_V4_IMMUTABLE_OUTPUT_ALREADY_NONEMPTY")
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "ROUTE_LIFECYCLE_ADMISSION.json", admission)
    binding = frozen_binding(scene)
    reservation = {
        "schema_version": "driveclarify.e2_v4.engineering_attempt.v1",
        "identity": identity, "phase": phase, "scene": scene, "seed": int(row["seed"]),
        "attempt": 1, "formal_seed": False, "formal_scientific_exposure": False,
        "output": str(output.relative_to(ROOT)),
        "scene_configuration_sha256": binding["scene_configuration_sha256"],
        "route_sha256": sha256(route_path(scene)), "reserved_at_unix_s": time.time(),
    }
    reservation["record_digest"] = canonical_sha256(reservation)
    append_jsonl(ATTEMPT_LEDGER, reservation)
    spec = build_spec(row)
    current_simlingo_diff = native._git_diff_sha256(native.SIMLINGO_ROOT)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = current_simlingo_diff
    preflight = _repair_irrelevant_display_process_false_positive(native.native_preflight(spec, output, visualization=True))
    runtime = error = lease = None
    progress = {"latest_game_time_s": None, "last_progress_wall_monotonic_s": time.monotonic(), "activation_monitor": None}
    if preflight.get("status") == "PASS":
        values = episode_environment(spec, output, row)
        command = native.build_command(spec, output)

        def observe(context: Mapping[str, Any]) -> Mapping[str, Any]:
            try:
                stdout = Path(str(context["stdout_path"])).read_text(encoding="utf-8", errors="replace")
                matches = re.findall(r"Game time\s*=\s*([0-9]+(?:\.[0-9]+)?)", stdout)
                latest = float(matches[-1]) if matches else None
            except OSError:
                latest = None
            if latest is not None and (progress["latest_game_time_s"] is None or latest > float(progress["latest_game_time_s"]) + 1e-9):
                progress["latest_game_time_s"] = latest
                progress["last_progress_wall_monotonic_s"] = float(context["now_monotonic"])
            if float(context["now_monotonic"]) - float(progress["last_progress_wall_monotonic_s"]) >= NO_PROGRESS_CONTAINMENT_S:
                return {"request_stop": True, "termination_reason": "E2_V4_NO_SIMULATOR_PROGRESS_ENGINEERING_INVALID", "signal": "SIGINT"}
            activation = load(output / "E2_V4_ACTIVATION_RECEIPT.json", {})
            if activation and validate_activation_receipt(activation, admission=admission):
                monitor = {"status": "ACTIVATED_AND_BOUND", "request_stop": False}
            else:
                rows = jsonl(output / "post_hoc_world_state.jsonl")
                ego = None if not rows else rows[-1].get("ego", {}).get("location_xyz")
                monitor = None if not (isinstance(ego, list) and len(ego) == 3) else dict(
                    evaluate_activation_boundary(admission, ego_xyz=ego, activation_receipt=activation or None)
                )
            progress["activation_monitor"] = monitor
            if monitor and monitor.get("request_stop") is True:
                return {"request_stop": True, "termination_reason": "E2_V4_SCENARIO_ACTIVATION_MISSED", "signal": "SIGINT", "stop_wait_seconds": 30.0}
            return {"poll_interval_seconds": 0.5}

        try:
            with episode_backend.serial_gpu_lease() as lease_row:
                lease = lease_row
                runtime = native.run_native_episode(
                    spec, output, command=command, environment=values, cwd=native.SIMLINGO_ROOT,
                    wall_timeout_seconds=wall_timeout_s,
                    wall_timeout_reason="E2_V4_WALL_CONTAINMENT_ENGINEERING_INVALID",
                    poll_observer=observe,
                    cleanup_writer=lambda value: atomic_json(output / "CLEANUP_RECEIPT.json", value),
                )
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
    scenario = load(output / "E2_V4_SCENARIO_RECEIPT.json", {})
    receipt = load(output / "E2_V4_NATIVE_RUNTIME_RECEIPT.json", {})
    cleanup = load(output / "CLEANUP_RECEIPT.json", {})
    paired = jsonl(output / "E2_V4_PAIRED_VIEW_EVIDENCE.jsonl")
    trace = jsonl(output / "E2_V4_RUNTIME_TRACE.jsonl")
    activation = load(output / "E2_V4_ACTIVATION_RECEIPT.json", {})
    scenario_for_lifecycle = order_receipt_fields(paired, scenario) if binding["family"] == "ORDER" else scenario
    route_ended_before_bound = bool(
        scenario.get("engineering_bound_reached") is not True
        and isinstance(runtime, Mapping) and runtime.get("termination_reason") == "NATURAL_EVALUATOR_COMPLETION"
    )
    lifecycle = validate_scenario_completion(
        admission=admission, activation_receipt=activation or None,
        scenario_receipt=scenario_for_lifecycle,
        cleanup_receipt={"cleanup_pass": cleanup.get("status") == "PASS"},
        route_ended=route_ended_before_bound,
    )
    fairness = bool(
        paired and all(item.get("fairness_counters_before") == item.get("fairness_counters_after") for item in paired)
        and all(
            item.get("source_identity", {}).get("source_frame_id") == item.get("views", {}).get(view, {}).get("source_frame_id")
            for item in paired for view in ("B0", "B1", "B2")
        )
    )
    integrity = {
        "preflight_pass": preflight.get("status") == "PASS",
        "native_child_created": isinstance(runtime, Mapping), "cleanup_pass": cleanup.get("status") == "PASS",
        "scenario_initialized": scenario.get("scene_config_id") == scene,
        "scenario_spawn_complete": int(scenario.get("expected_spawn_count", -1)) == len(scenario.get("spawn_rows") or ()),
        "scenario_events_complete": scenario.get("all_events_executed") is True,
        "scenario_event_transforms_verified": scenario.get("all_event_transforms_verified") is True,
        "engineering_bound_reached": scenario.get("engineering_bound_reached") is True,
        "mandatory_activation_receipt_valid": validate_activation_receipt(activation, admission=admission),
        "route_lifecycle_complete": lifecycle.get("status") == "PASS_SCENARIO_RELATIVE_LIFECYCLE",
        "native_runtime_constructed": receipt.get("method_id") == METHOD_ID,
        "trace_rows_present": bool(trace), "paired_rows_present": bool(paired), "paired_fairness": fairness,
        "observer_errors_zero": not (receipt.get("errors") or ()),
        "rgb_archive_present": int(receipt.get("rgb_archive_frame_count", 0)) > 0,
        "added_vla_forwards_zero": receipt.get("observer_added_vla_forwards") == 0,
        "duplicate_candidate_computations_zero": receipt.get("observer_added_candidate_computations") == 0,
        "added_pid_zero": receipt.get("observer_added_pid_instances") == 0,
        "control_writes_zero": receipt.get("observer_control_writes") == 0,
        "routeplanner_advances_zero": receipt.get("observer_route_planner_advances") == 0,
        "oracle_reads_zero": receipt.get("oracle_input_reads") == 0,
        "actor_id_runtime_reads_zero": receipt.get("carla_actor_id_runtime_reads") == 0,
        "reveal_runtime_reads_zero": receipt.get("reveal_time_or_frame_runtime_reads") == 0,
    }
    valid = bool(error is None and all(integrity.values()))
    result = {
        "schema_version": "driveclarify.e2_v4.engineering_execution.v1",
        "status": "VALID_ENGINEERING_EPISODE" if valid else "INVALID_ENGINEERING_ATTEMPT",
        "identity": identity, "phase": phase, "scene": scene, "seed": int(row["seed"]),
        "engineering_only": True, "formal_seed": False, "formal_scientific_exposure": False,
        "preflight": preflight, "runtime": runtime, "error": error, "gpu_lease": lease,
        "progress": progress, "scenario_receipt": scenario, "activation_receipt": activation,
        "route_lifecycle_admission": admission, "route_lifecycle_completion": lifecycle,
        "native_runtime_receipt": receipt, "integrity": integrity,
        "mechanism_summary": _summary(paired, scenario, receipt),
        "simlingo_diff_sha256": current_simlingo_diff,
        "source_hashes": {
            name: sha256(ROOT / name) for name in (
                "driveclarify_grounded_language_v1/runtime.py", "driveclarify_rq2_t_e2_v4/runtime.py",
                "driveclarify_rq2_t_e2_v4/tracker.py", "driveclarify_rq2_t_e2_v4/association.py",
                "driveclarify_rq2_t_e2_v4/provider.py", "driveclarify_rq2_t/measurement.py",
            )
        },
    }
    result["record_digest"] = canonical_sha256(result)
    atomic_json(output / "ENGINEERING_EPISODE_RESULT.json", result)
    append_jsonl(EXECUTION_LEDGER, result)
    if phase == "BLIND":
        append_jsonl(BLIND_LEDGER, {
            "identity": identity, "scene": scene, "seed": int(row["seed"]),
            "attempt": 1, "status": result["status"], "record_digest": result["record_digest"],
            "formal_scientific_exposure": False,
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", required=True)
    parser.add_argument("--wall-timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    result = run_identity(args.identity, args.wall_timeout_seconds)
    print(json.dumps({
        "status": result["status"], "identity": result["identity"], "phase": result["phase"],
        "scene": result["scene"], "integrity": result["integrity"],
        "mechanism_summary": result["mechanism_summary"], "error": result["error"],
    }, indent=2, sort_keys=True))
    return 0 if result["status"] == "VALID_ENGINEERING_EPISODE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
