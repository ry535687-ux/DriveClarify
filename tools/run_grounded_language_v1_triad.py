#!/usr/bin/env python3
"""Run one TRAIN-only unified Grounded Language V1 triad case."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1.contracts import (  # noqa: E402
    ANSWER_DELAY_ENV,
    ANSWER_ENV,
    CONTROL_ENV,
    DEVICE_ENV,
    FEATURE_FLAG,
    INSTRUCTION_ENV,
    OUTPUT_ENV,
    RECEIPT_FILENAME,
    VISUALIZATION_ENV,
)
from driveclarify_grounded_language_v1.visualization import WINDOW_TITLE  # noqa: E402
from driveclarify_paper_mvp_evaluation.display_preflight import (  # noqa: E402
    collect_native_display_facts,
    evaluate_native_display_preflight,
)
from driveclarify_paper_mvp_stage6b import backend as native  # noqa: E402
from tools.run_temporal_grounding_v1_live import diagnostic_episode  # noqa: E402


STATIC_SCENARIO_ID = "DCV0-S002"
STATIC_SEED = 5103
DEMO_ENV = "DRIVECLARIFY_GROUNDED_V1_DISTINCT_TRAJECTORY_DEMO"
CASE_INSTRUCTIONS = {
    "act": "Continue past the white van.",
    "ask": "Turn after the white van.",
    "wait": "Turn after the bus clears.",
}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _wall_timeout_reached(
    *, started: float, timeout_seconds: float, now: float | None = None
) -> bool:
    observed = time.monotonic() if now is None else float(now)
    return observed - float(started) >= float(timeout_seconds)


def _sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def _window_geometry(title: str, values: Mapping[str, str]) -> dict[str, Any]:
    result = subprocess.run(
        ["xwininfo", "-name", title],
        env=values,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    text = result.stdout
    matched_title = title
    matched_window_id = None
    discovery = "EXACT_NAME"
    # Unreal's X11/SDL window can append a transient suffix even though the
    # title bar visibly renders ``CarlaUE4``.  Fall back to the X root tree and
    # resolve a visible title containing the requested stable token.
    if result.returncode != 0:
        tree = subprocess.run(
            ["xwininfo", "-root", "-tree"],
            env=values,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        candidates = []
        for line in tree.stdout.splitlines():
            match = re.search(r'^\s*(0x[0-9a-fA-F]+)\s+"([^"]*{}[^"]*)"'.format(re.escape(title)), line)
            if match:
                candidates.append((match.group(1), match.group(2)))
        if candidates:
            matched_window_id, matched_title = candidates[-1]
            result = subprocess.run(
                ["xwininfo", "-id", matched_window_id],
                env=values,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            text = result.stdout
            discovery = "ROOT_TREE_TITLE_CONTAINS"
    def number(label: str) -> int | None:
        match = re.search(r"^\s*{}:\s*(-?\d+)".format(re.escape(label)), text, re.MULTILINE)
        return None if match is None else int(match.group(1))
    value = {
        "title": title,
        "matched_title": matched_title,
        "matched_window_id": matched_window_id,
        "discovery": discovery,
        "return_code": result.returncode,
        "x": number("Absolute upper-left X"),
        "y": number("Absolute upper-left Y"),
        "width": number("Width"),
        "height": number("Height"),
        "viewable": "Map State: IsViewable" in text,
    }
    value["bounds_valid"] = bool(
        result.returncode == 0
        and value["viewable"]
        and isinstance(value["width"], int)
        and value["width"] > 100
        and isinstance(value["height"], int)
        and value["height"] > 100
    )
    return value


def _content_metrics(image: Any) -> dict[str, Any]:
    from PIL import ImageStat

    sample = image.convert("RGB").resize((160, 90))
    pixels = list(sample.getdata())
    non_white = sum(any(channel < 245 for channel in pixel) for pixel in pixels)
    stat = ImageStat.Stat(sample)
    return {
        "non_white_pixel_fraction": non_white / max(len(pixels), 1),
        "channel_variance": [float(value) for value in stat.var],
        "content_valid": bool(
            non_white / max(len(pixels), 1) > 0.10 and max(stat.var) > 25.0
        ),
    }


def _capture_native_desktop(output_dir: Path, values: Mapping[str, str]) -> tuple[str, Mapping[str, Any]]:
    from PIL import Image

    dashboard = _window_geometry(WINDOW_TITLE, values)
    carla_window = _window_geometry("CarlaUE4", values)
    demo = str(values.get(DEMO_ENV, "")).strip().casefold() in {"1", "true", "yes", "on"}
    screenshot_name = (
        "GROUNDED_V1_DEMO_NATIVE_DESKTOP.png" if demo else "NATIVE_DESKTOP.png"
    )
    validation_name = (
        "GROUNDED_V1_DEMO_NATIVE_DESKTOP_VALIDATION.json"
        if demo
        else "NATIVE_DESKTOP_VALIDATION.json"
    )
    screenshot = output_dir / screenshot_name
    capture = subprocess.run(
        ["gnome-screenshot", "-f", str(screenshot)],
        env=values,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    validation: dict[str, Any] = {
        "schema_version": "driveclarify.grounded_language_v1.native_desktop_validation.v2",
        "dashboard_window": dashboard,
        "carla_window": carla_window,
        "capture_return_code": capture.returncode,
        "capture_stdout": capture.stdout.strip()[-500:],
        "screenshot_path": str(screenshot),
    }
    try:
        image = Image.open(screenshot)
        validation["desktop_content"] = _content_metrics(image)
        regions = {}
        for name, bounds in (("dashboard", dashboard), ("carla", carla_window)):
            if bounds.get("bounds_valid"):
                left = max(0, int(bounds["x"]))
                top = max(0, int(bounds["y"]))
                right = min(image.width, left + int(bounds["width"]))
                bottom = min(image.height, top + int(bounds["height"]))
                regions[name] = _content_metrics(image.crop((left, top, right, bottom)))
            else:
                regions[name] = {"content_valid": False}
        validation["window_content"] = regions
    except Exception as exc:
        validation["image_validation_error"] = type(exc).__name__ + ":" + str(exc)
        validation["desktop_content"] = {"content_valid": False}
        validation["window_content"] = {
            "dashboard": {"content_valid": False},
            "carla": {"content_valid": False},
        }
    valid = bool(
        capture.returncode == 0
        and screenshot.is_file()
        and dashboard.get("bounds_valid")
        and carla_window.get("bounds_valid")
        and validation["desktop_content"].get("content_valid")
        and validation["window_content"]["dashboard"].get("content_valid")
        and validation["window_content"]["carla"].get("content_valid")
    )
    validation["status"] = "PASS_VALIDATED_NATIVE_DESKTOP" if valid else "INVALID_CAPTURE"
    _write(output_dir / validation_name, validation)
    return str(validation["status"]), validation


def episode(case: str, seed: int, method_id: str) -> native.EpisodeSpec:
    if case == "wait":
        spec = diagnostic_episode(seed=seed, method_id=method_id)
    else:
        spec = native.resolve_train_episode(
            scenario_id=STATIC_SCENARIO_ID, seed=STATIC_SEED, method_id=method_id
        )
    return replace(
        spec,
        episode_id="DC-GLV1-{}-{}-{}".format(case.upper(), seed, method_id),
        raw_instruction=CASE_INSTRUCTIONS[case],
        split="train",
    )


def environment(
    spec: native.EpisodeSpec,
    output_dir: Path,
    *,
    case: str,
    device: str,
    control: bool,
    answer: str,
    answer_delay: float,
    visualization: bool = False,
    post_hoc_world_state: bool = False,
) -> dict[str, str]:
    values = native.build_environment(spec, output_dir, visualization=False)
    for key in tuple(values):
        if key.startswith("DRIVECLARIFY_PAPER_MVP_STAGE6A_") or key.startswith(
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_"
        ):
            values.pop(key, None)
    for key in (
        "DRIVECLARIFY_LANGUAGE_GROUNDING_V1",
        "DRIVECLARIFY_TEMPORAL_GROUNDING_V1",
        "DRIVECLARIFY_CANDIDATE_LIVE_ACT_AUTHORITY_V0",
    ):
        values.pop(key, None)
    values.update(
        {
            FEATURE_FLAG: "1",
            OUTPUT_ENV: str(output_dir),
            INSTRUCTION_ENV: spec.raw_instruction,
            DEVICE_ENV: device,
            CONTROL_ENV: "1" if control else "0",
            ANSWER_ENV: answer,
            ANSWER_DELAY_ENV: str(answer_delay),
            VISUALIZATION_ENV: "1" if visualization else "0",
            "DRIVECLARIFY_SHADOW_V0": "1",
            "DRIVECLARIFY_SHADOW_OUTPUT_DIR": str(output_dir),
            "DRIVECLARIFY_PROBE_RUN_ID": spec.episode_id,
            # Reused temporal primitive reads its own names internally.  These
            # are implementation configuration, never expected action labels.
            "DRIVECLARIFY_TEMPORAL_GROUNDING_V1_OUTPUT_DIR": str(output_dir),
            "DRIVECLARIFY_TEMPORAL_GROUNDING_V1_RAW_INSTRUCTION": spec.raw_instruction,
            "DRIVECLARIFY_TEMPORAL_GROUNDING_V1_DETECTOR_DEVICE": device,
            "DRIVECLARIFY_TEMPORAL_GROUNDING_V1_CONTROL": "1" if control else "0",
        }
    )
    if visualization:
        values["SDL_VIDEO_WINDOW_POS"] = "1270,80"
    if post_hoc_world_state:
        values["DRIVECLARIFY_WORLDSTATE_OUTPUT"] = str(
            output_dir / "post_hoc_world_state.jsonl"
        )
    else:
        values.pop("DRIVECLARIFY_WORLDSTATE_OUTPUT", None)
    # CARLA frame identifiers are server-global and therefore vary with process
    # history.  Ground the first synchronized RGB-0 observation delivered after
    # agent initialization instead of keying behavior to an absolute frame id.
    values.pop("DRIVECLARIFY_GROUNDED_LANGUAGE_V1_TRIGGER_FRAME", None)
    return values


def preflight(
    spec: native.EpisodeSpec,
    output_dir: Path,
    values: Mapping[str, str],
    *,
    case: str,
    control: bool,
) -> Mapping[str, Any]:
    command = native.build_command(spec, output_dir)
    session = native._local_x11_session()
    facts = collect_native_display_facts(
        launch_arguments=command,
        launch_environment=values,
        carla_executable=native.CARLA_ROOT / "CarlaUE4.sh",
        leaderboard_evaluator=native.EVALUATOR,
        checkpoint=native.CHECKPOINT,
        carla_port=native.RPC_PORT,
        traffic_manager_port=native.TRAFFIC_MANAGER_PORT,
    )
    checked = evaluate_native_display_preflight(facts)
    blockers = list(checked.blocker_codes)
    if session is None:
        blockers.append("LOCAL_SEAT0_X11_SESSION_NOT_VERIFIED")
    if spec.split != "train":
        blockers.append("UNIFIED_TRIAD_NOT_TRAIN")
    if native._file_sha256(native.CHECKPOINT) != native.CHECKPOINT_SHA256:
        blockers.append("SIMLINGO_CHECKPOINT_HASH_MISMATCH")
    if native._git_head(native.SIMLINGO_ROOT) != native.SIMLINGO_PROTECTED_HEAD:
        blockers.append("SIMLINGO_HEAD_MISMATCH")
    if native._git_diff_sha256(native.SIMLINGO_ROOT) != native.SIMLINGO_PROTECTED_DIFF_SHA256:
        blockers.append("SIMLINGO_PROTECTED_DIFF_MISMATCH")
    conflicts = {
        key: values.get(key)
        for key in (
            "DRIVECLARIFY_LANGUAGE_GROUNDING_V1",
            "DRIVECLARIFY_TEMPORAL_GROUNDING_V1",
            "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE",
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE",
        )
        if values.get(key)
    }
    if conflicts:
        blockers.append("UNIFIED_RUNTIME_COACTIVATION")
    receipt = {
        "schema_version": "driveclarify.grounded_language_v1.native_preflight.v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "observed_at_utc": _utc_now(),
        "case": case,
        "episode": spec.to_dict(),
        "train_only": True,
        "population_status": "MECHANISM_COVERAGE_PILOT_NOT_PERFORMANCE_EVALUATION",
        "native_display": dict(checked.evidence),
        "local_session": session,
        "blockers": list(dict.fromkeys(blockers)),
        "feature_flag": {"name": FEATURE_FLAG, "value": values.get(FEATURE_FLAG), "default": "OFF"},
        "coactivated": conflicts,
        "control_mode": "BOUNDED_CLOSED_LOOP" if control else "NO_CONTROL_SHADOW",
        "simlingo": {
            "head": native._git_head(native.SIMLINGO_ROOT),
            "protected_diff_sha256": native._git_diff_sha256(native.SIMLINGO_ROOT),
            "checkpoint_sha256": native._file_sha256(native.CHECKPOINT),
        },
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _write(output_dir / "GROUNDED_LANGUAGE_V1_NATIVE_PREFLIGHT.json", receipt)
    return receipt


def _terminal(status: Any) -> bool:
    text = str(status or "")
    return text in {
        "MECHANISM_DIAGNOSTIC_CAPTURE_READY",
        "SHADOW_NATURAL_ACT_PATH_PASS",
        "SHADOW_NATURAL_ASK_CLOSED_LOOP_PASS",
        "SHADOW_NATURAL_WAIT_CLOSED_LOOP_PASS",
        "BOUNDED_ACT_TO_ACT_CLOSED_LOOP_PASS",
        "BOUNDED_ASK_TO_ACT_CLOSED_LOOP_PASS",
        "BOUNDED_WAIT_TO_ACT_CLOSED_LOOP_PASS",
        "PASS_PHASE_B_DECISION_WINDOW_EVIDENCE_CAPTURED_PENDING_ANALYSIS",
        "PASS_B1_EVIDENCE_CAPTURED_PENDING_FAIL_CLOSED_ANALYSIS",
    } or text.startswith("BLOCKED_")


def _passive_phase_b_capture_ready(live: Mapping[str, Any]) -> bool:
    """Admit a fail-closed Phase B panel prearmed before route divergence."""

    return bool(
        live.get("native_desktop_capture_ready") is True
        and live.get("initial_decision") == "UNKNOWN"
        and live.get("evidence_only") is True
        and live.get("control_authority") == "EXISTING_BASELINE"
    )


def _external_completion_ready(
    live: Mapping[str, Any],
    predicate: Callable[[Mapping[str, Any]], bool] | None,
) -> bool:
    """Evaluate a read-only runner stop condition without changing policy."""

    return bool(predicate is not None and predicate(live))


def run(
    output_dir: Path,
    *,
    case: str,
    seed: int,
    method_id: str,
    device: str,
    control: bool,
    answer: str,
    answer_delay: float,
    timeout_seconds: float,
    episode_spec: native.EpisodeSpec | None = None,
    visualization: bool = False,
    post_hoc_world_state: bool = False,
    terminate_on_runtime_terminal: bool = True,
    capture_desktop: bool = False,
    environment_overrides: Mapping[str, str] | None = None,
    completion_predicate: Callable[[Mapping[str, Any]], bool] | None = None,
    desktop_capture_predicate: Callable[[Mapping[str, Any]], bool] | None = None,
    accept_natural_evaluator_completion: bool = False,
    allow_scientific_collision_outcome: bool = False,
) -> Mapping[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError("GROUNDED_LANGUAGE_V1_OUTPUT_DIR_NOT_EMPTY:" + str(output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = episode_spec or episode(case, seed, method_id)
    if spec.split != "train":
        raise RuntimeError("GROUNDED_LANGUAGE_V1_RUNNER_TRAIN_ONLY")
    values = environment(
        spec,
        output_dir,
        case=case,
        device=device,
        control=control,
        answer=answer,
        answer_delay=answer_delay,
        visualization=visualization,
        post_hoc_world_state=post_hoc_world_state,
    )
    if environment_overrides:
        values.update({str(key): str(value) for key, value in environment_overrides.items()})
    checked = preflight(spec, output_dir, values, case=case, control=control)
    if checked["status"] != "PASS":
        raise RuntimeError("BLOCKED_NATIVE_PREFLIGHT:" + ",".join(checked["blockers"]))
    command = native.build_command(spec, output_dir)
    launch = {
        "schema_version": "driveclarify.grounded_language_v1.launch.v1",
        "case": case,
        "episode": spec.to_dict(),
        "command": list(command),
        "cwd": str(native.SIMLINGO_ROOT),
        "train_only": True,
        "mechanism_coverage_pilot": True,
        "performance_evaluation": False,
        "feature_flag_default": "OFF",
        "feature_flag": FEATURE_FLAG,
        "same_runtime_version": "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_CONTROLLED_INTEGRATION_V1",
        "same_policy": "driveclarify.query_value_decision.v0",
        "scenario_to_decision_mapping": False,
        "expected_label_injected": False,
        "forced_decision_allowed": False,
        "control_mode": "BOUNDED_CLOSED_LOOP" if control else "NO_CONTROL_SHADOW",
        "native_visualization": bool(visualization),
        "passive_visualization": True,
        "post_hoc_world_state": bool(post_hoc_world_state),
        "terminate_on_runtime_terminal": bool(terminate_on_runtime_terminal),
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "environment_override_keys": sorted(environment_overrides or {}),
        "completion_predicate": (
            None
            if completion_predicate is None
            else getattr(completion_predicate, "__name__", type(completion_predicate).__name__)
        ),
        "completion_predicate_is_read_only_runner_stop_condition": True,
        "desktop_capture_predicate": (
            None
            if desktop_capture_predicate is None
            else getattr(
                desktop_capture_predicate,
                "__name__",
                type(desktop_capture_predicate).__name__,
            )
        ),
        "desktop_capture_predicate_is_read_only": True,
        "accept_natural_evaluator_completion": bool(
            accept_natural_evaluator_completion
        ),
        "allow_scientific_collision_outcome": bool(
            allow_scientific_collision_outcome
        ),
    }
    _write(output_dir / "GROUNDED_LANGUAGE_V1_LAUNCH_CONTRACT.json", launch)
    stdout_path = output_dir / "evaluator_stdout.log"
    desktop_capture_status = "NOT_REQUESTED"
    external_completion_observed = False

    def observe_runtime(_context: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal desktop_capture_status, external_completion_observed
        receipt_path = output_dir / RECEIPT_FILENAME
        if not receipt_path.is_file():
            return {}
        try:
            live = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        runtime_terminal = _terminal(live.get("status"))
        external_completion_ready = _external_completion_ready(
            live, completion_predicate
        )
        external_desktop_capture_ready = _external_completion_ready(
            live, desktop_capture_predicate
        )
        external_completion_observed = bool(
            external_completion_observed or external_completion_ready
        )
        passive_phase_b_capture_ready = _passive_phase_b_capture_ready(live)
        demo_capture_ready = bool(
            str(values.get(DEMO_ENV, "")).strip().casefold()
            in {"1", "true", "yes", "on"}
            and live.get("initial_decision") == "ASK"
            and int(live.get("effective_k") or 0) == 2
            and live.get("target_duplicate") is False
            and live.get("material_consequence_divergence") is True
            and len(live.get("candidate_plan_repetitions") or ()) >= 2
        )
        capture_ready = bool(
            (
                runtime_terminal
                or external_completion_ready
                or external_desktop_capture_ready
                or demo_capture_ready
                or passive_phase_b_capture_ready
            )
            and int(live.get("dashboard_refresh_count") or 0) > 0
            and int(live.get("dashboard_native_refresh_count") or 0) > 0
            and (
                live.get("initial_decision") in {"ACT", "ASK", "WAIT"}
                or external_completion_ready
                or external_desktop_capture_ready
                or passive_phase_b_capture_ready
                or (
                    runtime_terminal
                    and live.get("initial_decision") == "UNKNOWN"
                    and live.get("evidence_only") is True
                    and live.get("control_authority") == "EXISTING_BASELINE"
                )
            )
            and (
                isinstance(live.get("grounding"), Mapping)
                or len(live.get("rgb_identities") or ()) > 0
            )
        )
        if (
            capture_desktop
            and desktop_capture_status == "NOT_REQUESTED"
            and capture_ready
        ):
            # Preserve the original compositor-settle delay.
            time.sleep(1.0)
            desktop_capture_status, _ = _capture_native_desktop(output_dir, values)
        if terminate_on_runtime_terminal and (
            runtime_terminal or external_completion_ready
        ):
            if capture_desktop and desktop_capture_status == "NOT_REQUESTED":
                time.sleep(0.25)
                return {}
            return {
                "request_stop": True,
                "termination_reason": (
                    "EXTERNAL_LIFECYCLE_COMPLETION_CAPTURED"
                    if external_completion_ready and not runtime_terminal
                    else "UNIFIED_RECEIPT_CAPTURED"
                ),
                "signal": "SIGINT",
                "poll_interval_seconds": 0.1,
            }
        return {
            "poll_interval_seconds": (
                0.1
                if desktop_capture_status == "PASS_VALIDATED_NATIVE_DESKTOP"
                else 1.0
            )
        }

    runtime_result = native.run_native_episode(
        spec,
        output_dir,
        command=command,
        environment=values,
        cwd=native.SIMLINGO_ROOT,
        wall_timeout_seconds=timeout_seconds,
        wall_timeout_reason="UNIFIED_WALL_TIMEOUT",
        poll_observer=observe_runtime,
        cleanup_writer=lambda value: _write(
            output_dir / "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json", value
        ),
    )
    started_utc = str(runtime_result["start_utc"])
    termination_reason = str(runtime_result["termination_reason"])
    return_code = int(runtime_result["evaluator_return_code"])
    cleanup = dict(runtime_result["cleanup"])
    live_path = output_dir / RECEIPT_FILENAME
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.is_file() else None
    leaderboard_path = output_dir / "leaderboard_results.json"
    leaderboard = json.loads(leaderboard_path.read_text(encoding="utf-8")) if leaderboard_path.is_file() else {}
    records = leaderboard.get("_checkpoint", {}).get("records", [])
    collisions = [
        item
        for record in records
        for item in record.get("infractions", {}).get("collisions_vehicle", [])
    ]
    completion_satisfied = bool(
        live is not None
        and (
            _terminal(live.get("status"))
            or external_completion_observed
            or _external_completion_ready(live, completion_predicate)
            or (accept_natural_evaluator_completion and return_code == 0)
        )
    )
    collision_outcome_accepted = bool(
        not collisions or allow_scientific_collision_outcome
    )
    passed = live is not None and completion_satisfied and not str(live.get("status")).startswith("BLOCKED_") and cleanup.get("status") == "PASS" and collision_outcome_accepted and (
        not capture_desktop or desktop_capture_status == "PASS_VALIDATED_NATIVE_DESKTOP"
    )
    result = {
        "schema_version": "driveclarify.grounded_language_v1.native_run_receipt.v1",
        "status": "PASS_NATIVE_UNIFIED_TRIAD_CAPTURE_AND_CLEANUP" if passed else "BLOCKED_NATIVE_UNIFIED_TRIAD",
        "case": case,
        "start_utc": started_utc,
        "end_utc": _utc_now(),
        "duration_wall_seconds": float(runtime_result["duration_wall_seconds"]),
        "termination_reason": termination_reason,
        "evaluator_return_code": return_code,
        "live_status": None if live is None else live.get("status"),
        "initial_decision": None if live is None else live.get("initial_decision"),
        "post_interaction_decision": None if live is None else live.get("post_answer_decision", live.get("post_information_decision")),
        "cleanup_status": cleanup.get("status"),
        "vehicle_collision_count": len(collisions),
        "native_visualization": bool(visualization),
        "desktop_capture_status": desktop_capture_status,
        "completion_satisfied": completion_satisfied,
        "external_completion_observed": external_completion_observed,
        "completion_predicate": (
            None
            if completion_predicate is None
            else getattr(completion_predicate, "__name__", type(completion_predicate).__name__)
        ),
        "desktop_capture_predicate": (
            None
            if desktop_capture_predicate is None
            else getattr(
                desktop_capture_predicate,
                "__name__",
                type(desktop_capture_predicate).__name__,
            )
        ),
        "natural_evaluator_completion_accepted": bool(
            accept_natural_evaluator_completion
            and return_code == 0
            and not external_completion_observed
        ),
        "scientific_collision_outcome_allowed": bool(
            allow_scientific_collision_outcome
        ),
        "scientific_collision_outcome_observed": bool(collisions),
        "post_hoc_world_state_present": (output_dir / "post_hoc_world_state.jsonl").is_file(),
        "forced_decision_count": None if live is None else live.get("forced_decision_count"),
        "gold_policy_label_reads": None if live is None else live.get("gold_policy_label_reads"),
        "artifact_sha256": {
            RECEIPT_FILENAME: _sha(live_path),
            "evaluator_stdout.log": _sha(stdout_path),
            "NATIVE_DESKTOP.png": _sha(output_dir / "NATIVE_DESKTOP.png"),
            "NATIVE_DESKTOP_VALIDATION.json": _sha(output_dir / "NATIVE_DESKTOP_VALIDATION.json"),
            "GROUNDED_V1_DEMO_NATIVE_DESKTOP.png": _sha(
                output_dir / "GROUNDED_V1_DEMO_NATIVE_DESKTOP.png"
            ),
            "GROUNDED_V1_DEMO_NATIVE_DESKTOP_VALIDATION.json": _sha(
                output_dir / "GROUNDED_V1_DEMO_NATIVE_DESKTOP_VALIDATION.json"
            ),
        },
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _write(output_dir / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--case", choices=("act", "ask", "wait"), required=True)
    parser.add_argument("--seed", type=int, default=5301)
    parser.add_argument("--method-id", default="driveclarify")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--control", action="store_true")
    parser.add_argument("--answer", default="The nearer white van.")
    parser.add_argument("--answer-delay", type=float, default=0.1)
    parser.add_argument("--timeout-seconds", type=float, default=360.0)
    parser.add_argument("--visualization", action="store_true")
    parser.add_argument("--post-hoc-world-state", action="store_true")
    parser.add_argument("--continue-after-runtime-terminal", action="store_true")
    parser.add_argument("--capture-desktop", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.output_dir,
                case=args.case,
                seed=args.seed,
                method_id=args.method_id,
                device=args.device,
                control=args.control,
                answer=args.answer,
                answer_delay=args.answer_delay,
                timeout_seconds=args.timeout_seconds,
                visualization=args.visualization,
                post_hoc_world_state=args.post_hoc_world_state,
                terminate_on_runtime_terminal=not args.continue_after_runtime_terminal,
                capture_desktop=args.capture_desktop,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
