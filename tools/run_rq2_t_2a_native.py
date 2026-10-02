#!/usr/bin/env python3
"""Run one hash-bound native RQ2-T Experiment 2A episode."""

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
ACCEPTED = (
    ROOT
    / "reports"
    / "driveclarify_rq2_t_scene_certification_and_tfixed_calibration_v1"
)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as episode_backend  # noqa: E402
from driveclarify_paper_mvp_stage6b import backend as native  # noqa: E402
from tools.run_grounded_language_v1_triad import environment as grounded_environment  # noqa: E402


FIXTURE_SCENES = {
    "REF-02": {
        "runtime_fixture_id": "dc-runtime-0bd82fb9b9bda9e4ca49040e",
        "route": ROOT / "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-0bd82fb9b9bda9e4ca49040e.xml",
        "runtime": ROOT / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/dc-runtime-0bd82fb9b9bda9e4ca49040e.json",
        "promotion_seed": 5101,
    },
    "LMK-02": {
        "runtime_fixture_id": "dc-runtime-ed0e96eb054a43cd26e73b95",
        "route": ROOT / "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-ed0e96eb054a43cd26e73b95.xml",
        "runtime": ROOT / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/dc-runtime-ed0e96eb054a43cd26e73b95.json",
        "promotion_seed": 5301,
    },
    "USC-02": {
        "runtime_fixture_id": "dc-runtime-7091eedbf681e076baca527f",
        "route": ROOT / "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-7091eedbf681e076baca527f.xml",
        "runtime": ROOT / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/dc-runtime-7091eedbf681e076baca527f.json",
        "promotion_seed": 5101,
    },
    "USC-03": {
        "runtime_fixture_id": "dc-runtime-06cccfa4723c09ca09a9bc2b",
        "route": ROOT / "driveclarify_paper_mvp_scenarios/generated/routes/dc-runtime-06cccfa4723c09ca09a9bc2b.xml",
        "runtime": ROOT / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/dc-runtime-06cccfa4723c09ca09a9bc2b.json",
        "promotion_seed": 5101,
    },
}
CUSTOM_SCENES = {
    scene: {
        "runtime_fixture_id": "rq2t-2a-formal-" + scene.lower(),
        "route": ROOT / "driveclarify_rq2_t/formal_routes" / (scene.lower() + ".xml"),
        # EpisodeSpec requires a content-addressed runtime provenance file.  The
        # custom RouteScenario does not read this placeholder; its own binding
        # is the accepted scene certificate and owner binding.
        "runtime": FIXTURE_SCENES["USC-03"]["runtime"],
        "promotion_seed": 5101,
    }
    for scene in ("REF-01", "LMK-01", "ORD-01", "ORD-02")
}
SCENES = {**CUSTOM_SCENES, **FIXTURE_SCENES}
ABSOLUTE_WALL_CONTAINMENT_S = 604800.0
NO_SIMULATOR_PROGRESS_CONTAINMENT_S = 1800.0


def canonical(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
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
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def jsonl_count(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, Mapping):
                count += 1
    return count


def certificate(scene: str) -> Mapping[str, Any]:
    return load(ACCEPTED / "SCENE_CERTIFICATES" / (scene + ".json"))


def binding(scene: str) -> Mapping[str, Any]:
    return load(ROOT / "driveclarify_rq2_t/owner_bindings_v1.json")["bindings"][scene]


def build_spec(scene: str, seed: int, episode_id: str, engineering: bool) -> native.EpisodeSpec:
    selected = SCENES[scene]
    cert = certificate(scene)
    runtime = Path(selected["runtime"])
    promotion = native.PROMOTION_ROOT / "LIVE_PROMOTION_MANIFEST.json"
    return native.EpisodeSpec(
        episode_id=episode_id,
        runtime_config_id="RQ2T-2A-" + scene,
        scenario_id=str(cert["scene_id"]),
        runtime_fixture_id=str(selected["runtime_fixture_id"]),
        split="train" if engineering else "dev",
        seed=int(seed),
        method_id="original_simlingo",
        town=str(cert["map_identity"]["town"]),
        route_id=str(cert["route_identity"]),
        route_path=Path(selected["route"]).resolve(),
        runtime_manifest_path=runtime.resolve(),
        raw_instruction=str(cert["original_ambiguous_instruction"]),
        information_expected=False,
        schedule_sha256=sha256(ACCEPTED / "RQ2_T_FINAL_2A_PROTOCOL.json"),
        runtime_manifest_sha256=sha256(runtime),
        promotion_receipt_path=promotion.resolve(),
        promotion_receipt_payload_sha256=sha256(promotion),
    )


def episode_environment(
    spec: native.EpisodeSpec, output: Path, scene: str
) -> dict[str, str]:
    cert = certificate(scene)
    owner = binding(scene)
    values = grounded_environment(
        spec,
        output,
        case="act",
        device="cpu",
        control=False,
        answer="",
        answer_delay=0.5,
        visualization=False,
        post_hoc_world_state=True,
    )
    values.update(
        {
            "SCENARIO_RUNNER_ROOT": str(native.SCENARIO_DISCOVERY_ROOT),
            "DRIVECLARIFY_STAGE6A_GENERATED_ROOT": str(native.GENERATED_ROOT),
            "DRIVECLARIFY_STAGE6A_PROMOTION_ROOT": str(native.PROMOTION_ROOT),
            "DRIVECLARIFY_STAGE6A_SELECTED_SEED": str(SCENES[scene]["promotion_seed"]),
            "DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED": "1",
            "DRIVECLARIFY_RQ2_T_2A_SCENE_KEY": scene,
            "DRIVECLARIFY_RQ2_T_2A_ROUTE_SHA256": str(owner["route_sha256"]),
            "DRIVECLARIFY_RQ2_T_2A_SCENARIO_CONFIG_SHA256": str(
                owner["scenario_configuration_sha256"]
            ),
            "DRIVECLARIFY_RQ2_T_2A_OWNER_RECEIPT": str(
                output / "RQ2_T_2A_OWNER_RECEIPT.json"
            ),
            "DRIVECLARIFY_RQ2_T_2A_SCENARIO_RECEIPT": str(
                output / "RQ2_T_2A_SCENARIO_RECEIPT.json"
            ),
            "DRIVECLARIFY_PERSISTENT_AMBIGUITY_RUNTIME_V1": "1",
            "DRIVECLARIFY_DECISION_EVIDENCE_ARCHITECTURE_V3": "1",
            "DRIVECLARIFY_DECISION_EVIDENCE_CONTRACT_V2": "0",
            "DRIVECLARIFY_RQ2_T_TEMPORAL_OBSERVER": "1",
            "DRIVECLARIFY_RQ2_T_OUTPUT_DIR": str(output / "rq2_t_temporal"),
            "DRIVECLARIFY_RQ2_T_SCENE_ID": str(cert["scene_id"]),
            "DRIVECLARIFY_RQ2_T_EPISODE_ID": spec.episode_id,
            "DRIVECLARIFY_RQ2_T_ENGINEERING_SEED": str(spec.seed),
            "DRIVECLARIFY_RQ2_T_AMBIGUITY_TYPE": str(cert["ambiguity_family"]),
            "DRIVECLARIFY_RQ2_T_MAP": str(cert["map_identity"]["town"]),
            "DRIVECLARIFY_RQ2_T_ROUTE_IDENTITY": str(cert["route_identity"]),
            "DRIVECLARIFY_RQ2_T_COMMITMENT_CERTIFICATE_SHA256": canonical(
                cert["commitment_point_certificate"]
            ),
            "DRIVECLARIFY_RQ2_T_ANSWER_LATENCY_SIM_S": "0.5",
            "DRIVECLARIFY_RQ2_T_ACTION_RESERVE_TICKS": "11",
            "DRIVECLARIFY_RQ2_T_CONTROL_RESERVE_TICKS": "3",
            "DRIVECLARIFY_RQ2_T_FIXED_DELTA_SECONDS": "0.05",
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
    for key in tuple(values):
        upper = key.upper()
        if "QUERY_NECESSITY_GOLD" in upper or upper.endswith("_EXPECTED_LABEL"):
            values.pop(key, None)
    return values


def _leaderboard_entry_finished(path: Path) -> bool:
    value = load(path, {})
    return isinstance(value, Mapping) and value.get("entry_status") == "Finished"


def _scientific_exposure_observed(output: Path) -> bool:
    candidates = (
        output / "stage6b_frame_trace.jsonl",
        output / "probe/probe.jsonl",
        output / "post_hoc_world_state.jsonl",
        output / "rq2_t_temporal/TEMPORAL_EVIDENCE_RAW.jsonl",
    )
    return any(path.is_file() and path.stat().st_size > 0 for path in candidates)


def run_episode(
    scene: str,
    seed: int,
    output: Path,
    *,
    engineering: bool,
    episode_id: str,
    wall_timeout_seconds: float,
) -> Mapping[str, Any]:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("IMMUTABLE_ATTEMPT_OUTPUT_ALREADY_NONEMPTY:" + str(output))
    output.mkdir(parents=True, exist_ok=True)
    spec = build_spec(scene, seed, episode_id, engineering)
    current_simlingo_diff = native._git_diff_sha256(native.SIMLINGO_ROOT)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = current_simlingo_diff
    preflight = native.native_preflight(spec, output, visualization=True)
    if preflight["status"] != "PASS":
        result = {
            "schema_version": "driveclarify.rq2_t.formal_2a_native_attempt.v2",
            "status": "BLOCKED_PREFLIGHT",
            "engineering_only": engineering,
            "formal_scientific_exposure": False,
            "scientific_exposure_observed": False,
            "scene": scene,
            "seed": seed,
            "episode_id": episode_id,
            "preflight": preflight,
        }
        result["attempt_digest"] = canonical(result)
        atomic_json(output / "RQ2_T_2A_ATTEMPT_RESULT.json", result)
        return result

    values = episode_environment(spec, output, scene)
    command = native.build_command(spec, output)
    progress = {
        "last_progress_wall_monotonic_s": time.monotonic(),
        "latest_game_time_s": None,
        "progress_events": 0,
        "no_progress_containment_s": NO_SIMULATOR_PROGRESS_CONTAINMENT_S,
    }

    def observe(context: Mapping[str, Any]) -> Mapping[str, Any]:
        stdout_path = Path(str(context["stdout_path"]))
        try:
            text = stdout_path.read_text(encoding="utf-8", errors="replace")
            matches = re.findall(r"Game time\s*=\s*([0-9]+(?:\.[0-9]+)?)", text)
            latest = float(matches[-1]) if matches else None
        except OSError:
            latest = None
        previous = progress["latest_game_time_s"]
        if latest is not None and (previous is None or latest > float(previous) + 1e-9):
            progress["latest_game_time_s"] = latest
            progress["last_progress_wall_monotonic_s"] = float(context["now_monotonic"])
            progress["progress_events"] = int(progress["progress_events"]) + 1
        stalled = float(context["now_monotonic"]) - float(
            progress["last_progress_wall_monotonic_s"]
        )
        if stalled >= NO_SIMULATOR_PROGRESS_CONTAINMENT_S:
            return {
                "request_stop": True,
                "termination_reason": "RQ2_T_2A_NO_SIMULATOR_PROGRESS_ENGINEERING_INVALID",
                "signal": "SIGINT",
            }
        # Scientific terminal authority remains exclusively simulator-domain.
        return {"poll_interval_seconds": 0.5}

    runtime = None
    error = None
    lease = None
    try:
        with episode_backend.serial_gpu_lease() as lease_row:
            lease = lease_row
            runtime = native.run_native_episode(
                spec,
                output,
                command=command,
                environment=values,
                cwd=native.SIMLINGO_ROOT,
                wall_timeout_seconds=wall_timeout_seconds,
                wall_timeout_reason="RQ2_T_2A_WALL_CONTAINMENT_ENGINEERING_INVALID",
                poll_observer=observe,
                cleanup_writer=lambda value: atomic_json(
                    output / "CLEANUP_RECEIPT.json", value
                ),
            )
    except BaseException as exc:  # preserve an immutable attempt receipt
        error = {"type": type(exc).__name__, "message": str(exc)}

    temporal_dir = output / "rq2_t_temporal"
    raw_count = jsonl_count(temporal_dir / "TEMPORAL_EVIDENCE_RAW.jsonl")
    final_count = jsonl_count(temporal_dir / "TEMPORAL_EVIDENCE.jsonl")
    temporal = load(temporal_dir / "TEMPORAL_EVIDENCE_RECEIPT.json", {})
    owner = load(output / "RQ2_T_2A_OWNER_RECEIPT.json", {})
    scenario = load(output / "RQ2_T_2A_SCENARIO_RECEIPT.json", {})
    live = load(output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json", {})
    cleanup = load(output / "CLEANUP_RECEIPT.json", {})
    terminal = owner.get("terminal_event") if isinstance(owner, Mapping) else None
    terminal_state = terminal.get("state") if isinstance(terminal, Mapping) else None
    valid_terminal = terminal_state in {
        "COMMITMENT_OBSERVED",
        "MAX_2A_SIMULATED_HORIZON_REACHED",
        "PY_TREES_STATUS_SUCCESS",
        "PY_TREES_STATUS_FAILURE",
    }
    custom_valid = True
    if scene in CUSTOM_SCENES:
        custom_valid = bool(
            scenario.get("scene_key") == scene
            and int(scenario.get("expected_spawn_count", -1))
            == len(scenario.get("spawn_rows") or ())
            and scenario.get("status") in {"INITIALIZED", "CLEANED"}
        )
    integrity = {
        "evaluator_return_code_zero": bool(
            isinstance(runtime, Mapping)
            and int(runtime.get("evaluator_return_code", -1)) == 0
        ),
        "leaderboard_entry_finished": _leaderboard_entry_finished(
            output / "leaderboard_results.json"
        ),
        "cleanup_pass": cleanup.get("status") == "PASS",
        "temporal_receipt_pass": temporal.get("status")
        == "PASS_OBSERVATIONAL_MEASUREMENT_FLUSHED",
        "temporal_rows_present": raw_count > 0 and final_count == raw_count,
        "temporal_observer_error_free": not (temporal.get("observer_errors") or ()),
        "owner_terminal_valid": valid_terminal,
        "owner_single_plan_source": owner.get("owner_id")
        == "SHARED_PREFIX_LONGITUDINAL_OWNER_V1",
        "owner_observer_control_writes_zero": owner.get("observer_control_write_count")
        == 0,
        "owner_extra_compute_zero": all(
            owner.get(key) == 0
            for key in (
                "owner_induced_model_forward_count",
                "owner_induced_pid_count",
                "owner_induced_route_planner_advance_count",
            )
        ),
        "temporal_extra_compute_zero": all(
            temporal.get(key) == 0
            for key in (
                "extra_model_forward_count",
                "extra_planner_advance_count",
                "extra_pid_count",
                "extra_control_writer_count",
            )
        ),
        "custom_scenario_valid": custom_valid,
        "no_wall_terminal_authority": owner.get("wall_time_has_scientific_authority")
        is False,
    }
    valid = bool(error is None and all(integrity.values()))
    result = {
        "schema_version": "driveclarify.rq2_t.formal_2a_native_attempt.v2",
        "status": "PASS_VALID_EPISODE" if valid else "INVALID_ENGINEERING_EVIDENCE",
        "engineering_only": engineering,
        "formal_scientific_exposure": not engineering,
        "scientific_exposure_observed": _scientific_exposure_observed(output),
        "scene": scene,
        "seed": seed,
        "episode_id": episode_id,
        "route_path": str(spec.route_path),
        "route_asset_sha256": sha256(spec.route_path),
        "accepted_route_sha256": binding(scene)["route_sha256"],
        "accepted_scenario_configuration_sha256": binding(scene)[
            "scenario_configuration_sha256"
        ],
        "simlingo_worktree_diff_sha256": current_simlingo_diff,
        "preflight": preflight,
        "runtime": runtime,
        "progress_watchdog": progress,
        "error": error,
        "gpu_lease": lease,
        "terminal_event": terminal,
        "raw_temporal_observation_count": raw_count,
        "finalized_temporal_observation_count": final_count,
        "episode_summary": temporal,
        "owner_receipt": owner,
        "scenario_receipt": scenario,
        "integrity": integrity,
        "production_behavior_changed": False,
    }
    result["attempt_digest"] = canonical(result)
    atomic_json(output / "RQ2_T_2A_ATTEMPT_RESULT.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", required=True, choices=tuple(sorted(SCENES)))
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--engineering", action="store_true")
    parser.add_argument(
        "--wall-timeout-seconds", type=float, default=ABSOLUTE_WALL_CONTAINMENT_S
    )
    args = parser.parse_args()
    result = run_episode(
        args.scene,
        args.seed,
        args.output,
        engineering=bool(args.engineering),
        episode_id=args.episode_id,
        wall_timeout_seconds=float(args.wall_timeout_seconds),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS_VALID_EPISODE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
