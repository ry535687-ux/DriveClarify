#!/usr/bin/env python3
"""Execute one pre-registered RQ2-T-CG engineering identity exactly once."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as episode_backend  # noqa: E402
from driveclarify_paper_mvp_stage6b import backend as native  # noqa: E402
from driveclarify_rq2_t.measurement import canonical_sha256  # noqa: E402
from driveclarify_rq2_t_cg.builder import build_episode  # noqa: E402
from driveclarify_rq2_t_cg.lifecycle import static_route_admission, validate_activation  # noqa: E402
from driveclarify_rq2_t_cg.scene_bindings import frozen_binding  # noqa: E402
from tools.run_rq2_t_e2_v3 import _repair_irrelevant_display_process_false_positive  # noqa: E402


REPORT = ROOT / "reports/driveclarify_rq2_t_controlled_grounding_pre_science_v1"
REGISTRY = REPORT / "ENGINEERING_IDENTITY_REGISTRY.json"
ATTEMPTS = REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl"
EXECUTIONS = REPORT / "ENGINEERING_EXECUTION_LEDGER.jsonl"
EVIDENCE = REPORT / "NATIVE_EVIDENCE"
PLACEHOLDER_RUNTIME = ROOT / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/dc-runtime-06cccfa4723c09ca09a9bc2b.json"
PROMOTION = native.PROMOTION_ROOT / "LIVE_PROMOTION_MANIFEST.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[Mapping[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def registry_row(identity: str) -> Mapping[str, Any]:
    rows = [row for row in load(REGISTRY, {}).get("identities", ()) if row.get("identity") == identity]
    if len(rows) != 1:
        raise RuntimeError("RQ2_T_CG_IDENTITY_NOT_UNIQUELY_REGISTERED")
    return dict(rows[0])


def route_path(row: Mapping[str, Any]) -> Path:
    return (ROOT / row["route_path"]).resolve()


def build_spec(row: Mapping[str, Any]) -> native.EpisodeSpec:
    binding = frozen_binding(str(row["scene"]))
    schedule = REPORT / "EVIDENCE_MARGIN_RQ2_CONTRACT.json"
    return native.EpisodeSpec(
        episode_id=str(row["identity"]), runtime_config_id="RQ2TCG-" + str(row["scene"]),
        scenario_id=str(row["scene"]), runtime_fixture_id="rq2-t-cg-" + str(row["scene"]).lower(),
        split="train", seed=int(row["seed"]), method_id="original_simlingo",
        town=str(binding["town"]), route_id="RQ2TCG-" + str(row["scene"]),
        route_path=route_path(row), runtime_manifest_path=PLACEHOLDER_RUNTIME.resolve(),
        raw_instruction=str(binding["instruction"]), information_expected=False,
        schedule_sha256=sha256(schedule), runtime_manifest_sha256=sha256(PLACEHOLDER_RUNTIME),
        promotion_receipt_path=PROMOTION.resolve(), promotion_receipt_payload_sha256=sha256(PROMOTION),
    )


def environment(spec: native.EpisodeSpec, output: Path, row: Mapping[str, Any]) -> dict[str, str]:
    values = native.build_environment(spec, output, visualization=False)
    # Keep only the existing baseline agent and read-only world-state probe.
    # Neither the former automatic-E2 observer nor the controlled builder is
    # constructed in the child process.
    for key in (
        "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE", "DRIVECLARIFY_SHADOW_V0",
        "DRIVECLARIFY_GROUNDED_LANGUAGE_V1", "DRIVECLARIFY_LANGUAGE_GROUNDING_V1",
        "DRIVECLARIFY_TEMPORAL_GROUNDING_V1", "DRIVECLARIFY_RQ2_T_E2_V4_NATIVE_EVIDENCE",
        "DRIVECLARIFY_RQ2_T_E2_V3_NATIVE_EVIDENCE", "DRIVECLARIFY_RQ2_T_V2_NATIVE_EVIDENCE",
        "DRIVECLARIFY_RQ2_T_TEMPORAL_OBSERVER", "DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED",
        "DRIVECLARIFY_METHOD_V1_CONVERGENCE_OBSERVER", "DRIVECLARIFY_METHOD_REVISION_V2",
        "DRIVECLARIFY_CANDIDATE_LIVE_ACT_AUTHORITY_V0", "DRIVECLARIFY_PERSISTENT_AMBIGUITY_RUNTIME_V1",
    ):
        values.pop(key, None)
    values.update({
        "SCENARIO_RUNNER_ROOT": str(native.SCENARIO_DISCOVERY_ROOT),
        "DRIVECLARIFY_WORLDSTATE_OUTPUT": str(output / "post_hoc_world_state.jsonl"),
        "DRIVECLARIFY_RQ2_T_CG_SCENARIO_RECEIPT": str(output / "CG_SCENARIO_RECEIPT.json"),
        "DRIVECLARIFY_RQ2_T_CG_ACTIVATION_RECEIPT": str(output / "CG_ACTIVATION_RECEIPT.json"),
        "DRIVECLARIFY_RQ2_T_CG_ROUTE_ADMISSION": str(output / "CG_ROUTE_ADMISSION.json"),
        "DRIVECLARIFY_RQ2_T_CG_EPISODE_ID": str(row["identity"]),
        "DRIVECLARIFY_RQ2_T_CG_CONTROLLED_INTERFACE_RUNTIME": "0",
        "DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED": "1",
        "DRIVECLARIFY_RQ2_T_2A_SCENE_KEY": {
            "REFERENTIAL": "REF-01", "LANDMARK": "LMK-01", "ORDER": "ORD-01",
            "UNDERSPECIFIED_CONSTRAINT": "REF-01",
        }[frozen_binding(str(row["scene"]))["family"]],
        "DRIVECLARIFY_RQ2_T_2A_OWNER_RECEIPT": str(output / "RQ2_T_2A_OWNER_RECEIPT.json"),
        "DRIVECLARIFY_RQ2_T_E2_V4_NATIVE_EVIDENCE": "0",
        "DRIVECLARIFY_RQ2_T_E2_V3_NATIVE_EVIDENCE": "0",
        "DRIVECLARIFY_RQ2_T_V2_NATIVE_EVIDENCE": "0",
    })
    owner_document = load(ROOT / "driveclarify_rq2_t/owner_bindings_v1.json")
    owner_binding = owner_document["bindings"][values["DRIVECLARIFY_RQ2_T_2A_SCENE_KEY"]]
    values["DRIVECLARIFY_RQ2_T_2A_ROUTE_SHA256"] = str(owner_binding["route_sha256"])
    values["DRIVECLARIFY_RQ2_T_2A_SCENARIO_CONFIG_SHA256"] = str(owner_binding["scenario_configuration_sha256"])
    for key in tuple(values):
        upper = key.upper()
        if upper.endswith("_GOLD") or any(token in upper for token in (
            "QUERY_NECESSITY_GOLD", "TRUE_INTENT", "CORRECT_ANSWER", "EXPECTED_METHOD_RESULT",
        )):
            values.pop(key, None)
    return values


def run(identity: str, wall_timeout_s: float) -> Mapping[str, Any]:
    row = registry_row(identity)
    if any(item.get("identity") == identity for item in jsonl(ATTEMPTS)):
        raise RuntimeError("RQ2_T_CG_EXPOSED_ENGINEERING_IDENTITY_IMMUTABLE_NO_RERUN")
    binding = frozen_binding(str(row["scene"]))
    path = route_path(row)
    if sha256(path) != row["route_sha256"] or binding["scene_configuration_sha256"] != row["scene_configuration_sha256"]:
        raise RuntimeError("RQ2_T_CG_FROZEN_HASH_MISMATCH")
    admission = static_route_admission(path, binding)
    if admission["status"] != "PASS_STATIC_ROUTE_ADMISSION":
        raise RuntimeError("RQ2_T_CG_STATIC_ADMISSION_FAILED")
    output = EVIDENCE / identity / "attempt_01"
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("RQ2_T_CG_IMMUTABLE_OUTPUT_ALREADY_NONEMPTY")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "CG_ROUTE_ADMISSION.json", admission)
    reservation = {
        "schema_version": "driveclarify.rq2_t_cg.engineering_attempt.v1",
        "identity": identity, "scene": row["scene"], "template": row["template"], "seed": int(row["seed"]),
        "attempt": 1, "output": str(output.relative_to(ROOT)), "formal_seed": False,
        "formal_scientific_exposure": False, "reserved_at_unix_s": time.time(),
        "scene_configuration_sha256": binding["scene_configuration_sha256"], "route_sha256": sha256(path),
    }
    reservation["record_digest"] = canonical_sha256(reservation)
    append(ATTEMPTS, reservation)
    spec = build_spec(row)
    current_simlingo_diff = native._git_diff_sha256(native.SIMLINGO_ROOT)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = current_simlingo_diff
    preflight = _repair_irrelevant_display_process_false_positive(native.native_preflight(spec, output, visualization=True))
    runtime = error = lease = None
    if preflight.get("status") == "PASS":
        try:
            with episode_backend.serial_gpu_lease() as lease_row:
                lease = lease_row
                runtime = native.run_native_episode(
                    spec, output, command=native.build_command(spec, output),
                    environment=environment(spec, output, row), cwd=native.SIMLINGO_ROOT,
                    wall_timeout_seconds=wall_timeout_s,
                    wall_timeout_reason="RQ2_T_CG_ENGINEERING_WALL_CONTAINMENT",
                    cleanup_writer=lambda value: write_json(output / "CLEANUP_RECEIPT.json", value),
                )
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
    scenario = load(output / "CG_SCENARIO_RECEIPT.json", {})
    activation = load(output / "CG_ACTIVATION_RECEIPT.json", {})
    cleanup = load(output / "CLEANUP_RECEIPT.json", {})
    worlds = jsonl(output / "post_hoc_world_state.jsonl")
    owner = load(output / "RQ2_T_2A_OWNER_RECEIPT.json", {})
    owner_terminal = owner.get("terminal_event") if isinstance(owner, Mapping) else None
    owner_commitment = isinstance(owner_terminal, Mapping) and owner_terminal.get("state") == "COMMITMENT_OBSERVED"
    builder = None
    if error is None and worlds and (scenario.get("engineering_bound_reached") is True or owner_commitment):
        try:
            builder = build_episode(output, scene_id=str(row["scene"]), identity=identity,
                                    seed=int(row["seed"]), route_path=path)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc), "stage": "POSTEPISODE_CONTROLLED_BUILDER"}
    integrity = {
        "preflight_pass": preflight.get("status") == "PASS",
        "native_child_created": isinstance(runtime, Mapping),
        "cleanup_pass": cleanup.get("status") == "PASS",
        "scenario_initialized": scenario.get("scene_config_id") == row["scene"],
        "scenario_spawn_complete": len(scenario.get("spawn_rows", ())) == scenario.get("expected_spawn_count"),
        "scenario_events_complete": scenario.get("all_events_executed") is True,
        "scenario_event_transforms_verified": scenario.get("all_event_transforms_verified") is True,
        "native_terminal_valid": scenario.get("engineering_bound_reached") is True or owner_commitment,
        "activation_receipt_valid": validate_activation(activation, admission),
        "frozen_v1_shared_prefix_owner_active": owner.get("owner_id") == "SHARED_PREFIX_LONGITUDINAL_OWNER_V1",
        "owner_commitment_observed": owner_commitment,
        "owner_extra_compute_zero": all(owner.get(key) == 0 for key in (
            "owner_induced_model_forward_count", "owner_induced_pid_count",
            "owner_induced_route_planner_advance_count", "observer_control_write_count",
        )),
        "native_world_state_present": bool(worlds),
        "controlled_builder_complete": isinstance(builder, Mapping),
        "paired_source_identity": isinstance(builder, Mapping) and builder.get("same_source_identity_all_views") is True,
        "controlled_interface_runtime_reads_zero": isinstance(builder, Mapping) and builder.get("controlled_interface_runtime_reads") == 0,
        "vehicle_behavior_changes_zero": isinstance(builder, Mapping) and builder.get("vehicle_behavior_changes") == 0,
        "rule_added_native_episodes_zero": isinstance(builder, Mapping) and builder.get("rule_evaluation", {}).get("rule_added_native_episodes") == 0,
        "rule_added_vla_forwards_zero": isinstance(builder, Mapping) and builder.get("rule_evaluation", {}).get("rule_added_vla_forwards") == 0,
        "formal_scientific_exposure_false": reservation["formal_scientific_exposure"] is False,
    }
    valid = error is None and all(integrity.values())
    result = {
        "schema_version": "driveclarify.rq2_t_cg.engineering_execution.v1",
        "status": "VALID_ENGINEERING_EPISODE" if valid else "INVALID_ENGINEERING_ATTEMPT",
        "identity": identity, "scene": row["scene"], "template": row["template"], "seed": int(row["seed"]),
        "engineering_only": True, "formal_seed": False, "formal_scientific_exposure": False,
        "preflight": preflight, "runtime": runtime, "error": error, "gpu_lease": lease,
        "scenario_receipt": scenario, "activation_receipt": activation,
        "shared_prefix_owner_receipt": owner,
        "builder_receipt": builder, "integrity": integrity,
        "automatic_e2_runtime_enabled": False, "controlled_evidence_runtime_enabled": False,
        "simlingo_diff_sha256": current_simlingo_diff,
    }
    result["record_digest"] = canonical_sha256(result)
    write_json(output / "ENGINEERING_EPISODE_RESULT.json", result)
    append(EXECUTIONS, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", required=True)
    parser.add_argument("--wall-timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()
    result = run(args.identity, args.wall_timeout_seconds)
    print(json.dumps({"status": result["status"], "identity": result["identity"],
                      "scene": result["scene"], "integrity": result["integrity"],
                      "builder": result["builder_receipt"], "error": result["error"]},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "VALID_ENGINEERING_EPISODE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
