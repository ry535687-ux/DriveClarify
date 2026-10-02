#!/usr/bin/env python3
"""Prepare, post-process, and analyze RQ3 native engineering runs.

All generated identities are explicitly NON_FORMAL_DIAGNOSTIC_ONLY.  This
tool neither creates an RQ3-V3 protocol nor computes DriveClarify effects.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import itertools
import json
import math
from pathlib import Path
import secrets
import statistics
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq3_native_qualification.trace import (  # noqa: E402
    canonical_sha256,
    read_jsonl,
)


REPORT = ROOT / "reports/driveclarify_rq3_native_execution_engineering_qualification_v1"
V2_REPORT = ROOT / "reports/driveclarify_rq3_v2_bench2drive_closed_loop_validation_v1"
POSTMORTEM = ROOT / "reports/driveclarify_rq3_v2_postmortem_engineering_diagnosis_v1"
ORDINARY_TEMPLATE = V2_REPORT / "part_a_configs/RQ3V2-A-R01-S01-A0.json"
USC_TEMPLATE = V2_REPORT / "part_b_configs/RQ3V2-B-USC-EQUIVALENT-S01.json"
ORDINARY_ROUTE = Path("/home/buaa/wrh/simlingo/leaderboard/data/bench2drive_split/bench2drive_08.xml")
USC_ROUTE = ROOT / "reports/driveclarify_rq3_bench2drive_closed_loop_system_validation_v1/part_b_assets/RQ3-FORMAL-USC-EQUIVALENT-ROUTE.xml"
HISTORICAL_SEEDS = {
    542182766,
    2337308014,
    3958542675,
    1398642280,
    1981557918,
}
SOURCE_PATHS = (
    "driveclarify_rq3_native_qualification/__init__.py",
    "driveclarify_rq3_native_qualification/liveness.py",
    "driveclarify_rq3_native_qualification/trace.py",
    "driveclarify_rq3_native_qualification/simlingo_agent.py",
    "tools/run_rq3_native_execution_qualification.py",
    "tools/run_rq3_native_qualification_episode.sh",
    "tests/rq3_native_execution_qualification/test_liveness_and_trace.py",
    "tests/rq3_native_execution_qualification/test_pid_observer_noninterference.py",
)


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def source_rows() -> List[Dict[str, Any]]:
    rows = []
    for relative in SOURCE_PATHS:
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError("QUALIFICATION_SOURCE_MISSING:" + relative)
        rows.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha_file(path),
            }
        )
    return rows


def _seed_occurrences(seed: int) -> List[str]:
    completed = subprocess.run(
        ["rg", "-l", "--fixed-strings", str(seed), str(ROOT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return sorted(line for line in completed.stdout.splitlines() if line)


def fresh_seed() -> Tuple[int, List[str]]:
    for _ in range(100):
        seed = secrets.randbelow(2**32 - 1) + 1
        if seed in HISTORICAL_SEEDS:
            continue
        occurrences = _seed_occurrences(seed)
        if not occurrences:
            return seed, occurrences
    raise RuntimeError("UNABLE_TO_GENERATE_FRESH_ENGINEERING_SEED")


def _load(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _template_config(template: Path, run_id: str, seed: int, unit: str) -> Dict[str, Any]:
    config = _load(template)
    config["run_id"] = run_id
    config["scientific_seed_not_available_to_method"] = int(seed)
    config["engineering_qualification"] = {
        "stage": "RQ3_NATIVE_EXECUTION_ENGINEERING_QUALIFICATION_V1",
        "scope": "NON_FORMAL_DIAGNOSTIC_ONLY",
        "unit": unit,
        "future_scientific_denominator_eligible": False,
        "scientific_behavior_changes": [],
    }
    config["qualification_liveness_simulation_window_s"] = 4.0
    config["qualification_liveness_wall_stall_s"] = 10.0
    config["qualification_actor_sample_interval_frames"] = 5
    config["qualification_trace_fsync_interval"] = 20
    # Keep the terminal trace alive for the entire native route.
    config["observation_window_ticks"] = 10000
    return config


def prepare() -> Dict[str, Any]:
    if REPORT.exists():
        raise RuntimeError("QUALIFICATION_REPORT_DIRECTORY_ALREADY_EXISTS")
    ordinary_seed, ordinary_prior = fresh_seed()
    usc_seed, usc_prior = fresh_seed()
    while usc_seed == ordinary_seed:
        usc_seed, usc_prior = fresh_seed()

    configs = REPORT / "configs"
    runs = []
    units = [
        (
            "ORDINARY_NATIVE_U01",
            "ORDINARY_REPEATABILITY",
            ORDINARY_TEMPLATE,
            ORDINARY_ROUTE,
            ordinary_seed,
            False,
        ),
        (
            "USC_NATIVE_STALL_U01",
            "USC_STALL_REPRODUCTION",
            USC_TEMPLATE,
            USC_ROUTE,
            usc_seed,
            True,
        ),
    ]
    for unit_id, purpose, template, route, seed, terminate_on_liveness in units:
        for repeat in range(1, 4):
            run_id = "RQ3NEQ-V1-%s-R%02d" % (unit_id, repeat)
            config = _template_config(template, run_id, seed, unit_id)
            config_path = configs / (run_id + ".json")
            write_json(config_path, config)
            runs.append(
                {
                    "run_id": run_id,
                    "unit_id": unit_id,
                    "purpose": purpose,
                    "repeat": repeat,
                    "seed": seed,
                    "runtime_mode": "NATIVE_DEFAULT",
                    "config_path": str(config_path.relative_to(ROOT)),
                    "config_sha256": sha_file(config_path),
                    "route_path": str(route),
                    "route_sha256": sha_file(route),
                    "output_path": str(
                        (REPORT / "native_runs" / run_id).relative_to(ROOT)
                    ),
                    "terminate_on_liveness": terminate_on_liveness,
                    "classification": "NON_FORMAL_DIAGNOSTIC_ONLY",
                    "future_scientific_denominator_eligible": False,
                }
            )
    roster = {
        "schema": "driveclarify.rq3-native-repeatability-roster.v1",
        "stage": "RQ3_NATIVE_EXECUTION_ENGINEERING_QUALIFICATION_V1",
        "status": "FROZEN_UNEXECUTED",
        "created_epoch_s": int(time.time()),
        "selection_rule": (
            "one short historical ordinary route for endpoint repeatability plus the "
            "USC-EQUIVALENT geometry required for stall mechanism qualification; "
            "three native repeats per unit; seeds generated once by secrets and "
            "accepted solely on zero prior textual occurrence"
        ),
        "seed_selection_did_not_use_outcomes": True,
        "future_formal_seed_generation": False,
        "runs": runs,
    }
    roster["roster_digest"] = canonical_sha256(roster)
    write_json(REPORT / "NATIVE_REPEATABILITY_ROSTER.json", roster)
    frozen_sources = source_rows()
    freeze = {
        "schema": "driveclarify.rq3-native-repeatability-freeze.v1",
        "status": "FROZEN_BEFORE_ANY_NATIVE_EXECUTION",
        "scope": "NON_FORMAL_DIAGNOSTIC_ONLY",
        "roster_digest": roster["roster_digest"],
        "planned_units": 2,
        "planned_repeats_per_unit": 3,
        "planned_native_executions": 6,
        "engineering_seeds": [ordinary_seed, usc_seed],
        "seed_prior_occurrences": {
            str(ordinary_seed): ordinary_prior,
            str(usc_seed): usc_prior,
        },
        "historical_formal_seeds_excluded": sorted(HISTORICAL_SEEDS),
        "engineering_seeds_permanently_excluded_from_future_science": [
            ordinary_seed,
            usc_seed,
        ],
        "source_rows": frozen_sources,
        "source_digest": canonical_sha256(frozen_sources),
        "checkpoint_changed": False,
        "pid_or_controller_changed": False,
        "scientific_scene_changed": False,
        "deterministic_runtime_mode_in_roster": False,
    }
    freeze["receipt_digest"] = canonical_sha256(freeze)
    write_json(REPORT / "NATIVE_REPEATABILITY_FREEZE_RECEIPT.json", freeze)
    write_text(
        REPORT / "NATIVE_REPEATABILITY_PROTOCOL.md",
        "# Native repeatability protocol\n\n"
        "Scope: `NON_FORMAL_DIAGNOSTIC_ONLY`. These runs are not A0/A1, do not "
        "estimate a DriveClarify effect, and are permanently excluded from future "
        "scientific denominators.\n\n"
        "The roster was frozen before execution. It contains two units with three "
        "nominally identical native executions per unit. `ORDINARY_NATIVE_U01` uses "
        "the unchanged native SimLingo mode on route 2050 to measure forward, PID, "
        "ego, score, infraction, sensor-identity, and runtime variation. "
        "`USC_NATIVE_STALL_U01` uses the unchanged TASK_EQUIVALENT → ACT USC setup "
        "to test whether the native full-brake stall recurs and to persist the exact "
        "0/2 speed operands consumed by PID.\n\n"
        "Two new engineering seeds were generated once with `secrets`, admitted only "
        "when `rg` found zero prior occurrence, and then reused across the three "
        "within-unit repetitions. No result-dependent seed selection or replacement "
        "is allowed. USC runs may be terminated after the read-only observer has "
        "persisted a terminal liveness class; termination never writes control.\n\n"
        "Pairwise analysis is aligned by within-run model/PID sequence, never by "
        "process-local absolute CARLA frame. It reports exact byte hashes, maximum "
        "absolute tensor differences, first PID and ego divergence, raw and "
        "preprocessed image hashes, official results, infractions, and wall/simulation "
        "runtime. Aggregate equality alone is never evidence of deterministic inference.\n",
    )
    return freeze


def watchdog_check(status_path: Path) -> int:
    value = _load(status_path, {})
    liveness = value.get("liveness") or {}
    classification = liveness.get("classification")
    duration = liveness.get("no_progress_duration_simulation_s")
    if classification in {
        "NATIVE_STATIONARY_COMMANDING_STOP",
        "NATIVE_STATIONARY_DESPITE_FORWARD_COMMAND",
    } and duration is not None and float(duration) >= 4.0:
        return 10
    return 0


def _official_result(path: Path) -> Dict[str, Any]:
    value = _load(path, {})
    checkpoint = value.get("_checkpoint") or {}
    records = checkpoint.get("records") or []
    record = records[-1] if records else None
    global_record = checkpoint.get("global_record") or {}
    scores = (record or {}).get("scores") or global_record.get("scores_mean") or {}
    infractions = (record or {}).get("infractions") or global_record.get("infractions") or {}
    meta = (record or {}).get("meta") or global_record.get("meta") or {}
    infraction_counts = {}
    for key, infraction_value in infractions.items():
        if isinstance(infraction_value, list):
            infraction_counts[key] = len(infraction_value)
        else:
            infraction_counts[key] = (
                int(float(infraction_value) > 0.0)
                if infraction_value is not None
                else 0
            )
    return {
        "entry_status": value.get("entry_status"),
        "record_present": record is not None,
        "official_status": (record or {}).get("status") or global_record.get("status"),
        "driving_score": scores.get("score_composed"),
        "route_completion": scores.get("score_route"),
        "infraction_penalty": scores.get("score_penalty"),
        "infractions": infraction_counts,
        "success": bool(record and record.get("status") == "Completed"),
        "runtime_simulation_s": meta.get("duration_game"),
        "runtime_wall_s": meta.get("duration_system"),
    }


def finalize_run(
    output: Path,
    evaluator_exit: int,
    watchdog_reason: Optional[str] = None,
) -> Dict[str, Any]:
    trace_path = output / "owner_evidence/NATIVE_TERMINAL_TRACE.jsonl"
    rows = []
    trace_error = None
    if trace_path.is_file():
        try:
            rows = list(read_jsonl(trace_path))
        except Exception as error:
            trace_error = repr(error)
    last = rows[-1] if rows else {}
    liveness = last.get("liveness") or {}
    official = _official_result(output / "official_checkpoint.json")
    process = _load(output / "process_job/PROCESS_RECEIPT.json", {})
    if official["record_present"]:
        reason = "OFFICIAL_EVALUATOR_" + str(official["official_status"]).upper()
    elif watchdog_reason:
        reason = watchdog_reason
    elif int(evaluator_exit) == 124:
        reason = (
            "EVALUATOR_ONLY_WALLCLOCK_TIMEOUT"
            if liveness.get("classification") == "NATIVE_PROGRESSING"
            else liveness.get("classification", "UNKNOWN")
        )
    else:
        reason = liveness.get("classification", "UNKNOWN")

    required_top_level = {
        "input_identity",
        "simlingo_output",
        "native_pid",
        "controls",
        "world_native_execution",
        "clocks",
        "heartbeats",
        "liveness",
    }
    complete_rows = [row for row in rows if required_top_level <= set(row)]
    latest_complete = complete_rows[-1] if complete_rows else {}
    pid = latest_complete.get("native_pid") or {}
    processed = (
        (latest_complete.get("input_identity") or {}).get("processed_model_input")
        or {}
    )
    artifact_checks = {
        "trace_exists": trace_path.is_file(),
        "trace_digest_valid": trace_error is None and bool(rows),
        "complete_trace_row_present": bool(complete_rows),
        "raw_image_hash_present": any(
            any(
                sensor.get("is_image") and bool(sensor.get("sha256"))
                for sensor in ((row.get("input_identity") or {}).get("sensors") or {}).values()
            )
            for row in complete_rows
        ),
        "processed_image_hash_present": bool(
            (processed.get("camera_images") or {}).get("sha256")
        ),
        "model_output_values_present": bool(
            ((latest_complete.get("simlingo_output") or {}).get("pred_route") or {}).get("values")
        ),
        "pid_speed_indices_present": pid.get("speed_waypoint_indices_consumed") is not None,
        "pid_target_present": pid.get("target_point_consumed") is not None,
        "clock_pair_present": bool(latest_complete.get("clocks")),
        "world_and_evaluator_present": bool(latest_complete.get("world_native_execution")),
        "terminal_reason_present": reason not in (None, "UNKNOWN"),
        "process_receipt_present": bool(process),
    }
    summary = {
        "schema": "driveclarify.rq3-native-run-terminal-summary.v1",
        "scope": "NON_FORMAL_DIAGNOSTIC_ONLY",
        "run_id": last.get("run_id") or output.name,
        "evaluator_exit": int(evaluator_exit),
        "reason_code": reason,
        "watchdog_reason": watchdog_reason,
        "trace_path": str(trace_path),
        "trace_rows": len(rows),
        "complete_trace_rows": len(complete_rows),
        "trace_sha256": sha_file(trace_path) if trace_path.is_file() else None,
        "trace_error": trace_error,
        "first_no_progress_frame": liveness.get("first_no_progress_frame"),
        "no_progress_duration_simulation_s": liveness.get(
            "no_progress_duration_simulation_s"
        ),
        "no_progress_duration_wall_monotonic_s": liveness.get(
            "no_progress_duration_wall_monotonic_s"
        ),
        "heartbeats": latest_complete.get("heartbeats"),
        "liveness": liveness,
        "official": official,
        "process": process,
        "artifact_checks": artifact_checks,
        "artifact_complete": all(artifact_checks.values()),
        "observer_issued_control_or_recovery": False,
    }
    summary["receipt_digest"] = canonical_sha256(summary)
    write_json(output / "RUN_TERMINAL_SUMMARY.json", summary)
    return summary


def _flatten(values: Any) -> List[float]:
    result = []
    if isinstance(values, list):
        for value in values:
            result.extend(_flatten(value))
    elif values is not None:
        result.append(float(values))
    return result


def _max_abs(left: Any, right: Any) -> Optional[float]:
    a, b = _flatten(left), _flatten(right)
    if len(a) != len(b):
        return None
    return max((abs(x - y) for x, y in zip(a, b)), default=0.0)


def _model_rows(run_dir: Path) -> List[Dict[str, Any]]:
    path = run_dir / "owner_evidence/NATIVE_TERMINAL_TRACE.jsonl"
    if not path.is_file():
        return []
    try:
        return [row for row in read_jsonl(path) if row.get("simlingo_output")]
    except Exception:
        return []


def _first_divergence(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
    extractor,
    tolerance: float = 0.0,
) -> Optional[int]:
    for index, (a, b) in enumerate(zip(left, right)):
        av, bv = extractor(a), extractor(b)
        if av is None or bv is None:
            if av != bv:
                return index
            continue
        if isinstance(av, (list, tuple)):
            difference = _max_abs(av, bv)
            if difference is None or difference > tolerance:
                return index
        elif av != bv:
            return index
    return None


def _pairwise(left_id: str, left: List[Dict[str, Any]], right_id: str, right: List[Dict[str, Any]]) -> Dict[str, Any]:
    first_left = left[0].get("simlingo_output") if left else {}
    first_right = right[0].get("simlingo_output") if right else {}
    route_left = (first_left.get("pred_route") or {})
    route_right = (first_right.get("pred_route") or {})
    speed_left = (first_left.get("pred_speed_wps") or {})
    speed_right = (first_right.get("pred_speed_wps") or {})
    control = lambda row: [
        ((row.get("controls") or {}).get("final_vehicle_control") or {}).get(key)
        for key in ("steer", "throttle", "brake")
    ]
    ego = lambda row: (
        (((row.get("world_native_execution") or {}).get("ego_transform") or {}).get("location_xyz"))
    )
    sensor = lambda row: (
        (((row.get("input_identity") or {}).get("processed_model_input") or {}).get("camera_images") or {}).get("sha256")
    )
    return {
        "left_run_id": left_id,
        "right_run_id": right_id,
        "aligned_model_rows": min(len(left), len(right)),
        "first_model_pred_route_bitwise_identical": route_left.get("sha256") == route_right.get("sha256") and bool(route_left.get("sha256")),
        "first_model_pred_speed_wps_bitwise_identical": speed_left.get("sha256") == speed_right.get("sha256") and bool(speed_left.get("sha256")),
        "first_pred_route_max_abs_difference": _max_abs(route_left.get("values"), route_right.get("values")),
        "first_pred_speed_wps_max_abs_difference": _max_abs(speed_left.get("values"), speed_right.get("values")),
        "first_pid_control_divergence_sequence": _first_divergence(left, right, control, 0.0),
        "first_ego_xyz_divergence_gt_1mm_sequence": _first_divergence(left, right, ego, 0.001),
        "first_processed_image_hash_divergence_sequence": _first_divergence(left, right, sensor, 0.0),
    }


def _summary(values: Iterable[Any]) -> Dict[str, Any]:
    rows = [float(value) for value in values if value is not None]
    if not rows:
        return {"count": 0, "mean": None, "median": None, "minimum": None, "maximum": None, "range": None}
    return {
        "count": len(rows),
        "mean": statistics.fmean(rows),
        "median": statistics.median(rows),
        "minimum": min(rows),
        "maximum": max(rows),
        "range": max(rows) - min(rows),
    }


def analyze() -> Dict[str, Any]:
    roster = _load(REPORT / "NATIVE_REPEATABILITY_ROSTER.json", {})
    freeze = _load(REPORT / "NATIVE_REPEATABILITY_FREEZE_RECEIPT.json", {})
    if roster.get("status") != "FROZEN_UNEXECUTED":
        raise RuntimeError("REPEATABILITY_ROSTER_NOT_PROSPECTIVELY_FROZEN")
    if canonical_sha256({key: value for key, value in roster.items() if key != "roster_digest"}) != roster.get("roster_digest"):
        raise RuntimeError("REPEATABILITY_ROSTER_DIGEST_MISMATCH")
    current_sources = source_rows()
    frozen_by_path = {
        row["path"]: row for row in freeze.get("source_rows", [])
    }
    current_by_path = {row["path"]: row for row in current_sources}
    source_changes = [
        {
            "path": path,
            "frozen_sha256": frozen_by_path.get(path, {}).get("sha256"),
            "current_sha256": current_by_path.get(path, {}).get("sha256"),
        }
        for path in sorted(set(frozen_by_path) | set(current_by_path))
        if frozen_by_path.get(path, {}).get("sha256")
        != current_by_path.get(path, {}).get("sha256")
    ]
    all_source_stable = not source_changes
    execution_source_paths = set(SOURCE_PATHS) - {
        "tools/run_rq3_native_execution_qualification.py"
    }
    execution_source_stable = not any(
        row["path"] in execution_source_paths for row in source_changes
    )
    offline_postprocessor_only_amendment = bool(source_changes) and {
        row["path"] for row in source_changes
    } == {"tools/run_rq3_native_execution_qualification.py"}

    by_unit: Dict[str, List[Dict[str, Any]]] = {}
    for planned in roster.get("runs", []):
        run_dir = ROOT / planned["output_path"]
        summary = _load(run_dir / "RUN_TERMINAL_SUMMARY.json", {})
        rows = _model_rows(run_dir)
        official = summary.get("official") or _official_result(run_dir / "official_checkpoint.json")
        by_unit.setdefault(planned["unit_id"], []).append(
            {
                **planned,
                "run_dir": str(run_dir),
                "terminal_summary": summary,
                "model_rows": rows,
                "official": official,
            }
        )

    unit_results = []
    for unit_id, runs in sorted(by_unit.items()):
        pairwise = [
            _pairwise(
                left["run_id"],
                left["model_rows"],
                right["run_id"],
                right["model_rows"],
            )
            for left, right in itertools.combinations(runs, 2)
        ]
        first_hashes = []
        raw_image_hashes = []
        processed_image_hashes = []
        run_outcomes = []
        for run in runs:
            rows = run["model_rows"]
            first = rows[0] if rows else {}
            output = first.get("simlingo_output") or {}
            first_hashes.append(
                {
                    "run_id": run["run_id"],
                    "pred_route_sha256": (output.get("pred_route") or {}).get("sha256"),
                    "pred_speed_wps_sha256": (output.get("pred_speed_wps") or {}).get("sha256"),
                }
            )
            sensors = (first.get("input_identity") or {}).get("sensors") or {}
            raw_image_hashes.append(
                {
                    key: value.get("sha256")
                    for key, value in sensors.items()
                    if value.get("is_image")
                }
            )
            processed_image_hashes.append(
                ((((first.get("input_identity") or {}).get("processed_model_input") or {}).get("camera_images") or {}).get("sha256"))
            )
            simulation_times = [
                (row.get("clocks") or {}).get("simulation_time_s") for row in rows
            ]
            simulation_times = [
                float(value) for value in simulation_times if value is not None
            ]
            run_outcomes.append(
                {
                    "run_id": run["run_id"],
                    "model_trace_rows": len(rows),
                    "all_sensor_frames_synchronized": bool(rows)
                    and all(
                        ((row.get("input_identity") or {}).get("sensor_frames_synchronized"))
                        is True
                        for row in rows
                    ),
                    "first_source_frame_id": first.get("source_frame_id"),
                    "last_source_frame_id": rows[-1].get("source_frame_id") if rows else None,
                    "trace_simulation_span_s": (
                        None
                        if not simulation_times
                        else max(simulation_times) - min(simulation_times)
                    ),
                    "official": run["official"],
                    "terminal_reason": (run["terminal_summary"] or {}).get(
                        "reason_code"
                    ),
                    "artifact_complete": bool(
                        (run["terminal_summary"] or {}).get("artifact_complete")
                    ),
                }
            )
        officials = [run["official"] for run in runs]
        terminal_reasons = [
            (run["terminal_summary"] or {}).get("reason_code") for run in runs
        ]
        unit_results.append(
            {
                "unit_id": unit_id,
                "planned_repeats": 3,
                "observed_repeats": sum(bool(run["terminal_summary"]) for run in runs),
                "trace_qualified_repeats": sum(
                    bool((run["terminal_summary"] or {}).get("artifact_complete"))
                    for run in runs
                ),
                "run_outcomes": run_outcomes,
                "sensor_frame_synchronization_consistent": all(
                    row["all_sensor_frames_synchronized"] for row in run_outcomes
                ),
                "first_model_output_hashes": first_hashes,
                "pairwise": pairwise,
                "all_first_outputs_bitwise_identical": bool(pairwise) and all(
                    row["first_model_pred_route_bitwise_identical"]
                    and row["first_model_pred_speed_wps_bitwise_identical"]
                    for row in pairwise
                ),
                "raw_first_image_hashes": raw_image_hashes,
                "raw_first_image_identity_consistent": bool(raw_image_hashes)
                and all(row == raw_image_hashes[0] for row in raw_image_hashes[1:]),
                "processed_first_image_hashes": processed_image_hashes,
                "processed_first_image_identity_consistent": bool(processed_image_hashes[0])
                and len(set(processed_image_hashes)) == 1,
                "driving_score": _summary(row.get("driving_score") for row in officials),
                "route_completion": _summary(row.get("route_completion") for row in officials),
                "success_values": [bool(row.get("success")) for row in officials],
                "success_discordance": len({bool(row.get("success")) for row in officials}) > 1,
                "infraction_counts": [row.get("infractions") for row in officials],
                "infraction_discordance": len(
                    {json.dumps(row.get("infractions"), sort_keys=True) for row in officials}
                ) > 1,
                "runtime_simulation_s": _summary(row.get("runtime_simulation_s") for row in officials),
                "runtime_wall_s": _summary(
                    ((run["terminal_summary"] or {}).get("process") or {}).get("evaluator_wall_s")
                    for run in runs
                ),
                "terminal_reason_codes": terminal_reasons,
                "terminal_reason_discordance": len(set(terminal_reasons)) > 1,
            }
        )
    all_runs = [run for runs in by_unit.values() for run in runs]
    all_present = len(all_runs) == 6 and all(run["terminal_summary"] for run in all_runs)
    all_trace_qualified = all(
        bool((run["terminal_summary"] or {}).get("artifact_complete"))
        for run in all_runs
    )
    if not all_present or not all_trace_qualified or not execution_source_stable:
        qualification = "NOT_QUALIFIED"
    elif any(
        not unit["all_first_outputs_bitwise_identical"]
        or unit["success_discordance"]
        or unit["infraction_discordance"]
        or unit["driving_score"]["range"] not in (None, 0.0)
        for unit in unit_results
    ):
        qualification = "REQUIRES_REPEATABILITY_AWARE_PROTOCOL"
    else:
        qualification = "ACCEPTABLE_FOR_PROSPECTIVE_PAIRED_PROTOCOL"
    result = {
        "schema": "driveclarify.rq3-native-repeatability-results.v1",
        "scope": "NON_FORMAL_DIAGNOSTIC_ONLY",
        "not_driveclarify_performance_results": True,
        "roster_digest": roster.get("roster_digest"),
        "all_listed_source_stable_against_preexecution_freeze": all_source_stable,
        "native_execution_source_stable_against_preexecution_freeze": execution_source_stable,
        "offline_postprocessor_only_amendment": offline_postprocessor_only_amendment,
        "source_changes": source_changes,
        "planned_executions": 6,
        "observed_executions": sum(bool(run["terminal_summary"]) for run in all_runs),
        "unit_results": unit_results,
        "native_repeatability": qualification,
        "native_repeatability_floor": (
            "pairwise empirical ranges and first-divergence sequences above; no "
            "DriveClarify effect interpretation"
        ),
    }
    result["results_digest"] = canonical_sha256(result)
    write_json(REPORT / "NATIVE_REPEATABILITY_RESULTS.json", result)
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    watch = sub.add_parser("watchdog-check")
    watch.add_argument("--status", type=Path, required=True)
    finalize = sub.add_parser("finalize-run")
    finalize.add_argument("--output", type=Path, required=True)
    finalize.add_argument("--evaluator-exit", type=int, required=True)
    finalize.add_argument("--watchdog-reason")
    sub.add_parser("analyze")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        print(json.dumps(prepare(), indent=2, sort_keys=True))
        return 0
    if args.command == "watchdog-check":
        return watchdog_check(args.status)
    if args.command == "finalize-run":
        print(
            json.dumps(
                finalize_run(
                    args.output.resolve(),
                    args.evaluator_exit,
                    args.watchdog_reason,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "analyze":
        print(json.dumps(analyze(), indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
