"""Gated, serial, TRAIN-only E1/E2 campaign for Grounded Language E1-R1."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping

from .backend import artifact_directory, execute_episode
from .contracts import (
    METHODS,
    PHYSICAL_FIXTURES,
    PROTECTED_HASHES,
    PROTECTED_PATHS,
    REPORT_ROOT,
    SCHEMA_PREFIX,
    file_sha256,
    physical_fixture,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / REPORT_ROOT
GROUNDED = "driveclarify_grounded_v1"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def verify_protected_integrity() -> dict[str, Any]:
    records = {}
    for name, expected in PROTECTED_HASHES.items():
        path = ROOT / PROTECTED_PATHS[name]
        observed = file_sha256(path)
        records[name] = {
            "path": _rel(path),
            "expected_sha256": expected,
            "observed_sha256": observed,
            "unchanged": observed == expected,
        }
    if not all(row["unchanged"] for row in records.values()):
        raise RuntimeError("E1R1_PROTECTED_FROZEN_INTEGRITY_FAILURE")
    return records


def _bus_motion(output: Path) -> dict[str, Any]:
    path = output / "post_hoc_world_state.jsonl"
    observations = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            actors = row.get("actors", {}).get("actors", [])
            bus = next(
                (
                    actor
                    for actor in actors
                    if "fusorosa" in str(actor.get("type_id", "")).casefold()
                    or "bus" in str(actor.get("type_id", "")).casefold()
                ),
                None,
            )
            if bus is None:
                continue
            location = [float(value) for value in bus.get("location_xyz", [0.0, 0.0, 0.0])]
            velocity = [float(value) for value in bus.get("velocity_world_mps_xyz", [0.0, 0.0, 0.0])]
            observations.append(
                {
                    "frame": row.get("carla_snapshot_frame"),
                    "simulation_time": row.get("gametime_seconds"),
                    "actor_id": bus.get("id"),
                    "type_id": bus.get("type_id"),
                    "location_xyz": location,
                    "speed_mps": math.sqrt(velocity[0] ** 2 + velocity[1] ** 2),
                }
            )
    if not observations:
        return {
            "status": "BLOCKED_POSTHOC_BUS_NOT_OBSERVED",
            "policy_input": False,
            "path": _rel(path) if path.exists() else None,
            "observation_count": 0,
            "displacement_m": 0.0,
            "max_speed_mps": 0.0,
            "first_movement_frame": None,
        }
    first = observations[0]
    displacements = [
        math.hypot(
            row["location_xyz"][0] - first["location_xyz"][0],
            row["location_xyz"][1] - first["location_xyz"][1],
        )
        for row in observations
    ]
    displacement = max(displacements)
    max_speed = max(row["speed_mps"] for row in observations)
    first_movement = next(
        (row["frame"] for row, distance in zip(observations, displacements) if distance > 0.05),
        None,
    )
    return {
        "status": "PASS_ACTOR_MOTION_OBSERVED" if displacement > 0.0 and max_speed > 0.0 else "BLOCKED_ACTOR_STATIC",
        "policy_input": False,
        "path": _rel(path),
        "sha256": file_sha256(path),
        "observation_count": len(observations),
        "first": first,
        "last": observations[-1],
        "displacement_m": displacement,
        "max_speed_mps": max_speed,
        "first_movement_frame": first_movement,
    }


def _grounded_audit(output: Path, fixture_id: str, stage: str) -> dict[str, Any]:
    fixture = physical_fixture(fixture_id)
    mechanism = fixture["mechanism_family"]
    live_path = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
    native_path = output / "GROUNDED_LANGUAGE_V1_NATIVE_RUN_RECEIPT.json"
    cleanup_path = output / "GROUNDED_LANGUAGE_V1_CLEANUP_RECEIPT.json"
    live = _load(live_path) if live_path.is_file() else {}
    native = _load(native_path) if native_path.is_file() else {}
    cleanup = _load(cleanup_path) if cleanup_path.is_file() else {}
    common = (
        native.get("status") == "PASS_NATIVE_UNIFIED_TRIAD_CAPTURE_AND_CLEANUP"
        and cleanup.get("status") == "PASS"
        and int(live.get("forced_decision_count", 0)) == 0
        and int(live.get("privileged_state_policy_read_count", 0)) == 0
        and int(live.get("gold_policy_label_reads", 0)) == 0
    )
    motion = _bus_motion(output) if mechanism == "WAIT" else None
    if mechanism == "ACT":
        bindings = live.get("target_binding_receipts", [])
        lifecycle = (
            live.get("status") == "BOUNDED_ACT_TO_ACT_CLOSED_LOOP_PASS"
            and live.get("initial_decision") == "ACT"
            and bool(bindings)
            and all(row.get("target_id") and row.get("branch_id") for row in bindings)
        )
    elif mechanism == "ASK":
        bindings = live.get("target_binding_receipts", [])
        lifecycle = (
            live.get("status") == "BOUNDED_ASK_TO_ACT_CLOSED_LOOP_PASS"
            and live.get("initial_decision") == "ASK"
            and live.get("post_answer_decision") == "ACT"
            and int(live.get("effective_k", 0)) >= 2
            and len(bindings) >= 2
            and len({row.get("target_id") for row in bindings[:2]}) == 2
            and len({row.get("branch_id") for row in bindings[:2]}) == 2
            and live.get("old_candidate_set_invalidated") is True
            and bool(live.get("fresh_replan"))
            and live.get("answer_selected_executable_target") is True
        )
    else:
        states = [row.get("state") for row in live.get("event_timeline", [])]
        lifecycle = (
            live.get("status") == "BOUNDED_WAIT_TO_ACT_CLOSED_LOOP_PASS"
            and live.get("initial_decision") == "WAIT"
            and live.get("post_information_decision") == "ACT"
            and "CLEARED" in states
            and live.get("old_candidate_invalidated") is True
            and bool(live.get("fresh_replan_id"))
            and live.get("actor_motion_policy_independent") is True
            and motion is not None
            and motion["displacement_m"] > 0.0
            and motion["max_speed_mps"] > 0.0
        )
    audit = {
        "schema_version": SCHEMA_PREFIX + ".episode_audit.v1",
        "stage": stage.upper(),
        "fixture_id": fixture_id,
        "mechanism_family": mechanism,
        "method_id": GROUNDED,
        "status": "PASS" if common and lifecycle else "BLOCKED",
        "backend_contract_pass": common,
        "completed_lifecycle_pass": lifecycle,
        "runtime_status": live.get("status"),
        "initial_decision": live.get("initial_decision"),
        "post_interaction_decision": live.get("post_answer_decision") or live.get("post_information_decision"),
        "raw_k": live.get("raw_k"),
        "effective_k": live.get("effective_k"),
        "target_duplicate": live.get("target_duplicate"),
        "branch_duplicate": live.get("branch_duplicate"),
        "target_bindings": live.get("target_binding_receipts", []),
        "candidate_plan_repetitions": live.get("candidate_plan_repetitions", []),
        "question": live.get("question"),
        "answer": live.get("answer"),
        "old_candidates_invalidated": live.get("old_candidate_set_invalidated") or live.get("old_candidate_invalidated"),
        "fresh_replan_present": bool(live.get("fresh_replan") or live.get("fresh_replan_id")),
        "event_timeline": live.get("event_timeline", []),
        "information_update": live.get("information_update"),
        "actor_motion_policy_independent": live.get("actor_motion_policy_independent"),
        "motion_source": live.get("motion_source"),
        "motion_start_condition": live.get("motion_start_condition"),
        "wait_entry_frame": live.get("pre_event_plan_captured_frame"),
        "actor_motion": motion,
        "forced_decision_count": int(live.get("forced_decision_count", 0)),
        "privileged_state_policy_read_count": int(live.get("privileged_state_policy_read_count", 0)),
        "native_run_receipt": _rel(native_path) if native_path.is_file() else None,
        "live_receipt": _rel(live_path) if live_path.is_file() else None,
        "cleanup_receipt": _rel(cleanup_path) if cleanup_path.is_file() else None,
    }
    _write(output / "E1R1_EPISODE_AUDIT.json", audit)
    return audit


def _baseline_audit(output: Path, fixture_id: str, method_id: str, stage: str) -> dict[str, Any]:
    receipt_path = output / "EPISODE_RECEIPT.json"
    cleanup_path = output / "CLEANUP_RECEIPT.json"
    receipt = _load(receipt_path) if receipt_path.is_file() else {}
    cleanup = _load(cleanup_path) if cleanup_path.is_file() else {}
    passed = (
        receipt.get("status") == "COMPLETED_RECORDED_METHOD_RESULT"
        and cleanup.get("status") == "PASS"
        and receipt.get("real_native_carla") is True
        and receipt.get("headless") is False
    )
    audit = {
        "schema_version": SCHEMA_PREFIX + ".episode_audit.v1",
        "stage": stage.upper(),
        "fixture_id": fixture_id,
        "mechanism_family": physical_fixture(fixture_id)["mechanism_family"],
        "method_id": method_id,
        "status": "PASS" if passed else "BLOCKED",
        "backend_contract_pass": passed,
        "completed_lifecycle_pass": None,
        "native_receipt": _rel(receipt_path) if receipt_path.is_file() else None,
        "cleanup_receipt": _rel(cleanup_path) if cleanup_path.is_file() else None,
    }
    _write(output / "E1R1_EPISODE_AUDIT.json", audit)
    return audit


def _audit_existing(output: Path, fixture_id: str, method_id: str, stage: str) -> dict[str, Any]:
    return (
        _grounded_audit(output, fixture_id, stage)
        if method_id == GROUNDED
        else _baseline_audit(output, fixture_id, method_id, stage)
    )


def _run_slots(stage: str, slots: Iterable[tuple[str, str]], *, timeout: float) -> dict[str, Any]:
    rows = []
    for fixture_id, method_id in slots:
        verify_protected_integrity()
        output = artifact_directory(stage=stage, fixture_id=fixture_id, method_id=method_id)
        episode_id = "E1R1-{}-{}-{}".format(stage.upper(), fixture_id, method_id)
        started = _now()
        error = None
        try:
            audit_path = output / "E1R1_EPISODE_AUDIT.json"
            if audit_path.is_file():
                audit = _load(audit_path)
            elif output.exists() and any(output.iterdir()):
                audit = _audit_existing(output, fixture_id, method_id, stage)
            else:
                execute_episode(
                    stage=stage,
                    fixture_id=fixture_id,
                    method_id=method_id,
                    episode_id=episode_id,
                    visualization=(stage == "e2" and fixture_id.endswith("PHYS-001")),
                    capture_desktop=(stage == "e2" and fixture_id.endswith("PHYS-001")),
                    wall_timeout_seconds=timeout,
                )
                audit = _audit_existing(output, fixture_id, method_id, stage)
        except Exception as exc:
            error = type(exc).__name__ + ":" + str(exc)
            output.mkdir(parents=True, exist_ok=True)
            _write(
                output / "E1R1_EXECUTION_FAILURE.json",
                {
                    "schema_version": SCHEMA_PREFIX + ".execution_failure.v1",
                    "stage": stage.upper(),
                    "episode_id": episode_id,
                    "fixture_id": fixture_id,
                    "method_id": method_id,
                    "status": "BLOCKED",
                    "error": error,
                    "traceback": traceback.format_exc(),
                    "automatic_retry_count": 0,
                    "recorded_at_utc": _now(),
                },
            )
            audit = {"status": "BLOCKED", "backend_contract_pass": False}
        row = {
            "episode_id": episode_id,
            "fixture_id": fixture_id,
            "mechanism_family": physical_fixture(fixture_id)["mechanism_family"],
            "method_id": method_id,
            "split": "TRAIN",
            "status": audit.get("status", "BLOCKED"),
            "backend_contract_pass": audit.get("backend_contract_pass", False),
            "completed_lifecycle_pass": audit.get("completed_lifecycle_pass"),
            "artifact_dir": _rel(output),
            "error": error,
            "started_at_utc": started,
            "ended_at_utc": _now(),
        }
        rows.append(row)
        _write(
            REPORT_DIR / ("E1_SMOKE_LEDGER.json" if stage == "e1" else "E2_TRIAD_LEDGER.json"),
            {
                "schema_version": SCHEMA_PREFIX + ".{}.v1".format("e1_smoke_ledger" if stage == "e1" else "e2_triad_ledger"),
                "stage": stage.upper(),
                "status": "RUNNING",
                "rows": rows,
                "scheduled": len(list(slots)) if isinstance(slots, (list, tuple)) else None,
                "pass_count": sum(item["status"] == "PASS" for item in rows),
                "dev_attempt_count": 0,
                "test_attempt_count": 0,
                "test_consumed": False,
                "updated_at_utc": _now(),
            },
        )
    required = len(rows)
    passed = sum(row["status"] == "PASS" for row in rows)
    grounded_rows = [row for row in rows if row["method_id"] == GROUNDED]
    grounded_lifecycles = {
        mechanism: sum(
            row["completed_lifecycle_pass"] is True and row["mechanism_family"] == mechanism
            for row in grounded_rows
        )
        for mechanism in ("ACT", "ASK", "WAIT")
    }
    status = (
        "PASS_E1R1_E1_18_OF_18_BACKEND_CONTRACT_SMOKE"
        if stage == "e1" and passed == required == 18 and all(grounded_lifecycles[x] >= 1 for x in grounded_lifecycles)
        else "PASS_E1R1_E2_TRIAD_9_OF_9_COMPLETED_LIFECYCLES"
        if stage == "e2" and passed == required == 9 and all(grounded_lifecycles[x] >= 3 for x in grounded_lifecycles)
        else "BLOCKED_E1R1_{}_CAMPAIGN".format(stage.upper())
    )
    result = {
        "schema_version": SCHEMA_PREFIX + ".{}.v1".format("e1_smoke_ledger" if stage == "e1" else "e2_triad_ledger"),
        "stage": stage.upper(),
        "status": status,
        "rows": rows,
        "scheduled": required,
        "pass_count": passed,
        "grounded_completed_lifecycles": grounded_lifecycles,
        "forced_decision_count": 0,
        "privileged_state_policy_read_count": 0,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "protected_integrity": verify_protected_integrity(),
        "completed_at_utc": _now(),
    }
    _write(REPORT_DIR / ("E1_SMOKE_LEDGER.json" if stage == "e1" else "E2_TRIAD_LEDGER.json"), result)
    return result


def e1_slots() -> list[tuple[str, str]]:
    fixtures = [
        "E1R1-ACT-PHYS-001",
        "E1R1-ASK-PHYS-001",
        "E1R1-WAIT-PHYS-001",
    ]
    return [(fixture_id, method_id) for fixture_id in fixtures for method_id in METHODS]


def e2_slots() -> list[tuple[str, str]]:
    return [(str(row["fixture_id"]), GROUNDED) for row in PHYSICAL_FIXTURES]


def run_e1(*, timeout: float = 240.0) -> dict[str, Any]:
    e0 = _load(REPORT_DIR / "E0_PREFREEZE_REPORT.json")
    if e0.get("status") != "PASS_E1R1_E0_NATIVE_PREFREEZE":
        raise RuntimeError("E1R1_E1_REQUIRES_PASS_E0")
    return _run_slots("e1", e1_slots(), timeout=timeout)


def run_e2(*, timeout: float = 240.0) -> dict[str, Any]:
    e1 = _load(REPORT_DIR / "E1_SMOKE_LEDGER.json")
    if e1.get("status") != "PASS_E1R1_E1_18_OF_18_BACKEND_CONTRACT_SMOKE":
        raise RuntimeError("E1R1_E2_REQUIRES_PASS_E1")
    return _run_slots("e2", e2_slots(), timeout=timeout)


__all__ = ["e1_slots", "e2_slots", "run_e1", "run_e2", "verify_protected_integrity"]
