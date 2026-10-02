#!/usr/bin/env python3
"""Run one frozen RQ2-T V2 engineering identity through native SimLingo.

This launcher is engineering-only.  It cannot materialize a calibration,
DEV, TEST, or formal seed and it refuses identities outside the pre-registered
engineering registry.  Every run is immutable and appends an attempt and an
execution record before returning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as episode_backend  # noqa: E402
from driveclarify_paper_mvp_stage6b import backend as native  # noqa: E402
from driveclarify_rq2_t_v2.scene_bindings import frozen_binding  # noqa: E402
from tools.run_grounded_language_v1_triad import environment as grounded_environment  # noqa: E402


REPORT = ROOT / "reports/driveclarify_rq2_t_v2_pre_science_mechanism_qualification_v1"
REGISTRY = REPORT / "ENGINEERING_IDENTITY_REGISTRY.json"
PARAMETER_FREEZE = REPORT / "V2_PARAMETER_PROVENANCE_AND_FREEZE.json"
PHASE_A_MANIFEST = REPORT / "PHASE_A_ENGINEERING_SCENE_MANIFEST.json"
PHASE_B_SEAL = REPORT / "PHASE_B_BLIND_LAYOUT_SEAL.json"
ATTEMPT_LEDGER = REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl"
EXECUTION_LEDGER = REPORT / "ENGINEERING_EXECUTION_LEDGER.jsonl"
EVIDENCE_ROOT = REPORT / "PAIRED_VIEW_EVIDENCE"
PLACEHOLDER_RUNTIME = (
    ROOT
    / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/"
    "dc-runtime-06cccfa4723c09ca09a9bc2b.json"
)
PROMOTION = native.PROMOTION_ROOT / "LIVE_PROMOTION_MANIFEST.json"
WALL_CONTAINMENT_S = 1800.0
NO_PROGRESS_CONTAINMENT_S = 900.0


def canonical(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def jsonl_rows(path: Path) -> list[Mapping[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, Mapping):
                rows.append(value)
    return rows


def registry_row(identity: str) -> Mapping[str, Any]:
    registry = load(REGISTRY, {})
    matches = [row for row in registry.get("identities", ()) if row.get("identity") == identity]
    if len(matches) != 1:
        raise RuntimeError("RQ2_T_V2_ENGINEERING_IDENTITY_NOT_UNIQUELY_REGISTERED")
    row = dict(matches[0])
    if row.get("phase") not in {"A", "B", "RESERVE"}:
        raise RuntimeError("RQ2_T_V2_FORMAL_IDENTITY_FORBIDDEN")
    return row


def route_path(scene: str) -> Path:
    path = ROOT / "driveclarify_rq2_t_v2/engineering_routes" / (scene.lower() + ".xml")
    if not path.is_file():
        raise RuntimeError("RQ2_T_V2_ENGINEERING_ROUTE_MISSING")
    return path.resolve()


def _frozen_route_sha(scene: str, phase: str) -> str:
    if phase == "B":
        rows = load(PHASE_B_SEAL, {}).get("layouts", ())
        key = "layout"
    else:
        rows = load(PHASE_A_MANIFEST, {}).get("templates", ())
        key = "template"
    match = [row for row in rows if row.get(key) == scene]
    if len(match) != 1:
        raise RuntimeError("RQ2_T_V2_SCENE_NOT_IN_FROZEN_MANIFEST")
    return str(match[0]["route_sha256"])


def _bind_reserve(row: Mapping[str, Any], scene: str) -> Mapping[str, Any]:
    if row.get("phase") != "RESERVE":
        if row.get("scene") != scene:
            raise RuntimeError("RQ2_T_V2_IDENTITY_SCENE_MISMATCH")
        return row
    # A reserve may replace a failed Phase-A identity, but never a blind one.
    if scene.startswith("BLIND-"):
        raise RuntimeError("RQ2_T_V2_RESERVE_CANNOT_ENTER_BLIND_SET")
    value = dict(row)
    value["phase"] = "A"
    value["scene"] = scene
    value["role"] = "PHASE_A_FRESH_REPLACEMENT"
    return value


def _assert_run_admission(row: Mapping[str, Any], scene: str) -> None:
    prior = [value for value in jsonl_rows(ATTEMPT_LEDGER) if value.get("identity") == row["identity"]]
    if prior:
        raise RuntimeError("RQ2_T_V2_EXPOSED_IDENTITY_IMMUTABLE_NO_RERUN")
    if row.get("phase") == "B":
        qualification = load(REPORT / "ENGINEERING_QUALIFICATION_RECEIPT.json", {})
        if qualification.get("phase_a_status") != "PASS_PHASE_A_MECHANISM_QUALIFIED":
            raise RuntimeError("RQ2_T_V2_PHASE_B_REQUIRES_SEALED_PHASE_A_PASS")
        source_freeze = load(REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
        if source_freeze.get("phase_b_execution_authorized") is not True:
            raise RuntimeError("RQ2_T_V2_PHASE_B_SOURCE_FREEZE_NOT_AUTHORIZED")
    expected = _frozen_route_sha(scene, str(row["phase"]))
    if sha256(route_path(scene)) != expected:
        raise RuntimeError("RQ2_T_V2_FROZEN_ENGINEERING_ROUTE_HASH_MISMATCH")
    binding = frozen_binding(scene)
    manifest_name = PHASE_B_SEAL if row.get("phase") == "B" else PHASE_A_MANIFEST
    manifest = load(manifest_name, {})
    rows = manifest.get("layouts", ()) if row.get("phase") == "B" else manifest.get("templates", ())
    key = "layout" if row.get("phase") == "B" else "template"
    match = [value for value in rows if value.get(key) == scene]
    if str(match[0]["scene_configuration_sha256"]) != str(binding["scene_configuration_sha256"]):
        raise RuntimeError("RQ2_T_V2_FROZEN_SCENE_CONFIGURATION_HASH_MISMATCH")


def build_spec(row: Mapping[str, Any], scene: str) -> native.EpisodeSpec:
    binding = frozen_binding(scene)
    runtime = PLACEHOLDER_RUNTIME.resolve()
    return native.EpisodeSpec(
        episode_id=str(row["identity"]),
        runtime_config_id="RQ2TV2-" + scene,
        scenario_id=scene,
        runtime_fixture_id="rq2-t-v2-engineering-" + scene.lower(),
        split="train",
        seed=int(row["seed"]),
        method_id="original_simlingo",
        town=str(binding["town"]),
        route_id="RQ2TV2-" + scene,
        route_path=route_path(scene),
        runtime_manifest_path=runtime,
        raw_instruction=str(binding["instruction"]),
        information_expected=False,
        schedule_sha256=sha256(PARAMETER_FREEZE),
        runtime_manifest_sha256=sha256(runtime),
        promotion_receipt_path=PROMOTION.resolve(),
        promotion_receipt_payload_sha256=sha256(PROMOTION),
    )


def episode_environment(
    spec: native.EpisodeSpec, output: Path, row: Mapping[str, Any], scene: str
) -> dict[str, str]:
    binding = frozen_binding(scene)
    values = grounded_environment(
        spec, output, case="act", device="cpu", control=False, answer="",
        answer_delay=0.5, visualization=False, post_hoc_world_state=True,
    )
    commitment = canonical(
        {
            "scene_configuration_sha256": binding["scene_configuration_sha256"],
            "commitment_owner": "UNCHANGED_V1_RUNTIME_COMMITMENT_EVENT",
            "engineering_only": True,
        }
    )
    values.update(
        {
            "SCENARIO_RUNNER_ROOT": str(native.SCENARIO_DISCOVERY_ROOT),
            "DRIVECLARIFY_STAGE6A_GENERATED_ROOT": str(native.GENERATED_ROOT),
            "DRIVECLARIFY_STAGE6A_PROMOTION_ROOT": str(native.PROMOTION_ROOT),
            "DRIVECLARIFY_STAGE6A_SELECTED_SEED": "5101",
            "DRIVECLARIFY_RQ2_T_V2_NATIVE_EVIDENCE": "1",
            "DRIVECLARIFY_RQ2_T_V2_SCENE_ID": scene,
            "DRIVECLARIFY_RQ2_T_V2_EPISODE_ID": str(row["identity"]),
            "DRIVECLARIFY_RQ2_T_V2_ENGINEERING_SEED": str(row["seed"]),
            "DRIVECLARIFY_RQ2_T_V2_AMBIGUITY_TYPE": str(binding["family"]),
            "DRIVECLARIFY_RQ2_T_V2_MAP": str(binding["town"]),
            "DRIVECLARIFY_RQ2_T_V2_ROUTE_IDENTITY": "RQ2TV2-" + scene,
            "DRIVECLARIFY_RQ2_T_V2_COMMITMENT_CERTIFICATE_SHA256": commitment,
            "DRIVECLARIFY_RQ2_T_V2_SCENARIO_RECEIPT": str(output / "RQ2_T_V2_SCENARIO_RECEIPT.json"),
            "DRIVECLARIFY_PERSISTENT_AMBIGUITY_RUNTIME_V1": "1",
            "DRIVECLARIFY_DECISION_EVIDENCE_ARCHITECTURE_V3": "1",
            "DRIVECLARIFY_DECISION_EVIDENCE_CONTRACT_V2": "0",
            "DRIVECLARIFY_RQ2_T_TEMPORAL_OBSERVER": "0",
            "DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED": "0",
            "DRIVECLARIFY_METHOD_V1_CONVERGENCE_OBSERVER": "0",
            "DRIVECLARIFY_METHOD_REVISION_V2": "0",
            "DRIVECLARIFY_METHOD_V2_1_RULE_GATE_DECOUPLING": "0",
            "DRIVECLARIFY_METHOD_V2_3_PRE_ACTIVATION_FEASIBILITY": "0",
            "DRIVECLARIFY_METHOD_V2_4_SIMULATION_EXECUTION_OPPORTUNITY": "0",
            "DRIVECLARIFY_METHOD_V2_5_COMPLETION_HANDOVER": "0",
            "DRIVECLARIFY_METHOD_V2_6_GENERIC_DOWNSTREAM_LANDING": "0",
            "DRIVECLARIFY_METHOD_V2_7_ASK_BASELINE": "0",
            "DRIVECLARIFY_CANDIDATE_LIVE_ACT_AUTHORITY_V0": "0",
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
        }
    )
    forbidden_tokens = (
        "QUERY_NECESSITY_GOLD", "EXPECTED_LABEL", "TRUE_INTERPRETATION",
        "FUTURE_REVEAL", "AUTHORED_REVEAL", "EXPECTED_MECHANISM_OUTCOME",
    )
    for key in tuple(values):
        if any(token in key.upper() for token in forbidden_tokens) or key.upper().endswith("_GOLD"):
            values.pop(key, None)
    return values


def _summary(rows: list[Mapping[str, Any]], scenario: Mapping[str, Any]) -> Mapping[str, Any]:
    availability: dict[str, dict[str, Any]] = {}
    for view in ("B0", "B1", "B2"):
        for field in ("E2_GROUNDING", "E5_ROUTE_LANE_TOPOLOGY_RELATION", "E7_SAFETY_RULE_HOLDING"):
            selected = [
                item for item in rows
                if item.get("views", {}).get(view, {}).get("evidence_vector", {}).get(field, {}).get("status") == "AVAILABLE"
            ]
            availability[view + ":" + field] = {
                "available_count": len(selected),
                "first_simulation_time_s": None if not selected else selected[0]["source_identity"]["simulation_time_s"],
            }
    sufficiency = {
        view: [
            item["source_identity"]["simulation_time_s"] for item in rows
            if item.get("views", {}).get(view, {}).get("EpistemicEvidenceSufficient") is True
        ]
        for view in ("B0", "B1", "B2")
    }
    reveal_frame = scenario.get("reveal_frame")
    false_early = 0
    if isinstance(reveal_frame, int):
        false_early = sum(
            1 for item in rows
            if isinstance(item.get("source_identity", {}).get("source_frame_id"), int)
            and int(item["source_identity"]["source_frame_id"]) < reveal_frame
            and any(
                item.get("views", {}).get(view, {}).get("evidence_vector", {}).get(field, {}).get("status") == "AVAILABLE"
                for view in ("B1", "B2") for field in ("E2_GROUNDING", "E5_ROUTE_LANE_TOPOLOGY_RELATION")
            )
        )
    return {
        "paired_row_count": len(rows), "availability": availability,
        "sufficiency_times_s": sufficiency,
        "false_pre_reveal_availability_row_count": false_early,
        "b2_outperforms_b1": bool(not sufficiency["B1"] and sufficiency["B2"]),
    }


def run_identity(
    identity: str, scene_override: Optional[str], wall_timeout_s: float
) -> Mapping[str, Any]:
    registered = registry_row(identity)
    scene = str(scene_override or registered.get("scene") or "")
    if not scene:
        raise RuntimeError("RQ2_T_V2_RESERVE_REQUIRES_EXPLICIT_SCENE")
    row = _bind_reserve(registered, scene)
    _assert_run_admission(row, scene)
    output = EVIDENCE_ROOT / identity / "attempt_01"
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("RQ2_T_V2_IMMUTABLE_OUTPUT_ALREADY_NONEMPTY")
    output.mkdir(parents=True, exist_ok=True)
    binding = frozen_binding(scene)
    reservation = {
        "schema_version": "driveclarify.rq2_t_v2.engineering_attempt.v1",
        "identity": identity, "phase": row["phase"], "scene": scene,
        "seed": int(row["seed"]), "attempt": 1,
        "formal_scientific_exposure": False,
        "output": str(output.relative_to(ROOT)),
        "scene_configuration_sha256": binding["scene_configuration_sha256"],
        "route_sha256": sha256(route_path(scene)),
        "reserved_at_unix_s": time.time(),
    }
    reservation["record_digest"] = canonical(reservation)
    append_jsonl(ATTEMPT_LEDGER, reservation)
    spec = build_spec(row, scene)
    current_simlingo_diff = native._git_diff_sha256(native.SIMLINGO_ROOT)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = current_simlingo_diff
    preflight = native.native_preflight(spec, output, visualization=True)
    runtime = error = lease = None
    progress = {
        "latest_game_time_s": None, "progress_events": 0,
        "last_progress_wall_monotonic_s": time.monotonic(),
    }
    if preflight.get("status") == "PASS":
        values = episode_environment(spec, output, row, scene)
        command = native.build_command(spec, output)

        def observe(context: Mapping[str, Any]) -> Mapping[str, Any]:
            try:
                text = Path(str(context["stdout_path"])).read_text(encoding="utf-8", errors="replace")
                matches = re.findall(r"Game time\s*=\s*([0-9]+(?:\.[0-9]+)?)", text)
                latest = float(matches[-1]) if matches else None
            except OSError:
                latest = None
            previous = progress["latest_game_time_s"]
            if latest is not None and (previous is None or latest > float(previous) + 1e-9):
                progress["latest_game_time_s"] = latest
                progress["last_progress_wall_monotonic_s"] = float(context["now_monotonic"])
                progress["progress_events"] = int(progress["progress_events"]) + 1
            if float(context["now_monotonic"]) - float(progress["last_progress_wall_monotonic_s"]) >= NO_PROGRESS_CONTAINMENT_S:
                return {
                    "request_stop": True,
                    "termination_reason": "RQ2_T_V2_NO_SIMULATOR_PROGRESS_ENGINEERING_INVALID",
                    "signal": "SIGINT",
                }
            return {"poll_interval_seconds": 0.5}

        try:
            with episode_backend.serial_gpu_lease() as lease_row:
                lease = lease_row
                runtime = native.run_native_episode(
                    spec, output, command=command, environment=values,
                    cwd=native.SIMLINGO_ROOT, wall_timeout_seconds=wall_timeout_s,
                    wall_timeout_reason="RQ2_T_V2_WALL_CONTAINMENT_ENGINEERING_INVALID",
                    poll_observer=observe,
                    cleanup_writer=lambda value: atomic_json(output / "CLEANUP_RECEIPT.json", value),
                )
        except BaseException as exc:  # immutable evidence, including interruption
            error = {"type": type(exc).__name__, "message": str(exc)}
    scenario = load(output / "RQ2_T_V2_SCENARIO_RECEIPT.json", {})
    native_receipt = load(output / "RQ2_T_V2_NATIVE_RUNTIME_RECEIPT.json", {})
    cleanup = load(output / "CLEANUP_RECEIPT.json", {})
    paired = jsonl_rows(output / "RQ2_T_V2_PAIRED_VIEW_EVIDENCE.jsonl")
    fairness = bool(
        paired
        and all(item.get("fairness_counters_before") == item.get("fairness_counters_after") for item in paired)
        and all(
            item.get("source_identity", {}).get("source_frame_id")
            == item.get("views", {}).get(view, {}).get("source_frame_id")
            for item in paired for view in ("B0", "B1", "B2")
        )
    )
    expected_reveal = binding.get("reveal_after_simulation_s") is not None
    integrity = {
        "preflight_pass": preflight.get("status") == "PASS",
        "native_child_created": isinstance(runtime, Mapping),
        "cleanup_pass": cleanup.get("status") == "PASS",
        "scenario_initialized": scenario.get("scene_config_id") == scene,
        "scenario_spawn_complete": int(scenario.get("expected_spawn_count", -1)) == len(scenario.get("spawn_rows") or ()),
        "scenario_reveal_contract_executed_if_expected": bool(not expected_reveal or scenario.get("reveal_executed") is True),
        "native_runtime_constructed": native_receipt.get("wrapper_runtime_type") == "driveclarify_rq2_t_v2.native_runtime.RQ2TV2NativeEvidenceRuntime",
        "actual_base_runtime_constructed": native_receipt.get("base_runtime_type") == "driveclarify_persistent_ambiguity_runtime_v1.runtime.PersistentAmbiguityReferentialRuntime",
        "paired_rows_present": len(paired) > 0,
        "paired_fairness": fairness,
        "observer_errors_zero": not (native_receipt.get("errors") or ()),
        "added_vla_forwards_zero": native_receipt.get("observer_added_vla_forwards") == 0,
        "duplicate_candidate_computations_zero": native_receipt.get("observer_added_candidate_computations") == 0,
        "added_pid_zero": native_receipt.get("observer_added_pid_instances") == 0,
        "control_writes_zero": native_receipt.get("observer_control_writes") == 0,
        "routeplanner_advances_zero": native_receipt.get("observer_route_planner_advances") == 0,
        "oracle_reads_zero": native_receipt.get("oracle_input_reads") == 0,
    }
    valid = bool(error is None and all(integrity.values()))
    result = {
        "schema_version": "driveclarify.rq2_t_v2.engineering_execution.v1",
        "status": "VALID_ENGINEERING_EPISODE" if valid else "INVALID_ENGINEERING_ATTEMPT",
        "identity": identity, "phase": row["phase"], "scene": scene,
        "seed": int(row["seed"]), "engineering_only": True,
        "formal_scientific_exposure": False, "formal_seed": False,
        "preflight": preflight, "runtime": runtime, "error": error,
        "gpu_lease": lease, "progress": progress,
        "scenario_receipt": scenario, "native_runtime_receipt": native_receipt,
        "integrity": integrity, "mechanism_summary": _summary(paired, scenario),
        "simlingo_diff_sha256": current_simlingo_diff,
        "source_hashes": {
            "grounded_factory": sha256(ROOT / "driveclarify_grounded_language_v1/runtime.py"),
            "native_runtime": sha256(ROOT / "driveclarify_rq2_t_v2/native_runtime.py"),
            "providers": sha256(ROOT / "driveclarify_rq2_t_v2/providers.py"),
            "memory": sha256(ROOT / "driveclarify_rq2_t_v2/memory.py"),
            "v1_sufficiency": sha256(ROOT / "driveclarify_rq2_t/measurement.py"),
        },
    }
    result["record_digest"] = canonical(result)
    atomic_json(output / "ENGINEERING_EPISODE_RESULT.json", result)
    append_jsonl(EXECUTION_LEDGER, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", required=True)
    parser.add_argument("--scene", help="Required only when consuming a registered reserve identity")
    parser.add_argument("--wall-timeout-seconds", type=float, default=WALL_CONTAINMENT_S)
    args = parser.parse_args()
    result = run_identity(args.identity, args.scene, float(args.wall_timeout_seconds))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "VALID_ENGINEERING_EPISODE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
