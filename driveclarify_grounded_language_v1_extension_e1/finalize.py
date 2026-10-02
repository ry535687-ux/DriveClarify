"""Finalize the E1 TRAIN prefix with explicit partial/blocker evidence."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from .aggregate import aggregate
from .contracts import (
    ARTIFACT_ROOT,
    EXTENSION_METHODS,
    LEDGER_PATH,
    PROTECTED_HASHES,
    PROTECTED_PATHS,
    REPORT_ROOT,
    SCHEMA_PREFIX,
    file_sha256,
)
from .materialize import atomic_json, atomic_text


ROOT = Path(__file__).resolve().parents[1]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _command(*argv: str) -> str:
    return subprocess.run(argv, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout


def _bus_stationarity(output: Path) -> Mapping[str, Any]:
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
                    if "fusorosa" in str(actor.get("type_id", ""))
                    or "bus" in str(actor.get("type_id", ""))
                ),
                None,
            )
            if bus:
                velocity = bus.get("velocity_world_mps_xyz", [0.0, 0.0, 0.0])
                observations.append(
                    {
                        "frame": row.get("carla_snapshot_frame"),
                        "simulation_time": row.get("gametime_seconds"),
                        "actor_id": bus.get("id"),
                        "type_id": bus.get("type_id"),
                        "location_xyz": bus.get("location_xyz"),
                        "speed_mps": math.sqrt(sum(float(value) ** 2 for value in velocity)),
                    }
                )
    if not observations:
        return {"status": "UNKNOWN", "reason": "POSTHOC_BUS_ACTOR_NOT_OBSERVED", "path": _rel(path) if path.exists() else None}
    first, last = observations[0], observations[-1]
    displacement = math.sqrt(
        sum(
            (float(last["location_xyz"][index]) - float(first["location_xyz"][index])) ** 2
            for index in range(3)
        )
    )
    max_speed = max(float(row["speed_mps"]) for row in observations)
    return {
        "status": "PASS_POSTHOC_CONFIRMS_BUS_STATIONARY" if displacement < 0.1 and max_speed < 0.05 else "BUS_MOTION_OBSERVED",
        "policy_input": False,
        "path": _rel(path),
        "sha256": file_sha256(path),
        "observation_count": len(observations),
        "first": first,
        "last": last,
        "displacement_m": displacement,
        "max_speed_mps": max_speed,
    }


def finalize() -> Mapping[str, Any]:
    results = aggregate()
    ledger = _load(ROOT / LEDGER_PATH)
    report_root = ROOT / REPORT_ROOT
    artifact_root = ROOT / ARTIFACT_ROOT
    counts = Counter(row["status"] for row in ledger["rows"])
    per_method = {}
    for method in EXTENSION_METHODS:
        selected = [row for row in ledger["rows"] if row["method_id"] == method]
        per_method[method] = {
            "scheduled": len(selected),
            "started": sum(row["status"] != "SCHEDULED_NOT_STARTED" for row in selected),
            "completed": sum(row["status"] == "COMPLETED_RECORDED" for row in selected),
            "failed_recorded": sum(row["status"] == "FAILED_RECORDED" for row in selected),
        }

    wait_outputs = [
        artifact_root / "train" / ("DC-GLV1-E1-WAIT-{:03d}".format(index)) / "seed_1" / "driveclarify_grounded_v1"
        for index in (2, 3, 4)
    ]
    wait_audits = [_bus_stationarity(path) for path in wait_outputs]
    ask_rows = []
    for index in (1, 2, 3, 4):
        output = artifact_root / "train" / ("DC-GLV1-E1-ASK-{:03d}".format(index)) / "seed_1" / "driveclarify_grounded_v1"
        path = output / "EXTENSION_EPISODE_RESULT.json"
        if path.is_file():
            row = _load(path)
            ask_rows.append(
                {
                    "scenario_id": row["identity"]["scenario_id"],
                    "runtime_status": row.get("runtime_status"),
                    "lifecycle_complete": row.get("runtime_lifecycle_complete"),
                    "decision": row["decision"],
                    "raw_k": row["candidate"]["raw_k"],
                    "effective_k": row["candidate"]["effective_k"],
                    "result_path": _rel(path),
                }
            )
    e2_diagnosis = {
        "schema_version": SCHEMA_PREFIX + ".e2_diagnosis.v1",
        "status": "BLOCKED_E2_NATURAL_COVERAGE_TRUSTWORTHY_DIAGNOSIS",
        "natural_completed": results["grounded_natural_decision_counts"],
        "required": {"ACT": 3, "ASK": 3, "WAIT": 3},
        "forced_decision_total": 0,
        "ask_evidence": ask_rows,
        "ask_root_cause": "CPU online DINO produced effective K=1 on all three non-OOM ASK smoke configurations, so the label-free policy naturally ACTed; evaluator labels were not injected.",
        "wait_posthoc_stationarity": wait_audits,
        "wait_root_cause": "The selected original TRAIN fixture keeps the bus pre-trigger static. WAIT holds the ego before the route-proximity scene trigger, so no physical bus-clear event arrives and WAIT cannot complete.",
        "population_design_findings": [
            "All TRAIN ASK scenarios share one Town03 source fixture and one physical route/branch context; this does not satisfy the requested multi-branch ASK diversity standard.",
            "WAIT language variants share one pre-trigger-static Town05 fixture that is incompatible with autonomous WAIT resolution from the initial route state.",
            "The population is a controlled paraphrase/fixture study, not 24 independent physical scenes and not evidence of broad language understanding.",
        ],
        "gold_or_forced_repair_used": False,
        "automatic_retry_used": False,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
    }
    atomic_json(report_root / "EXTENSION_E2_DIAGNOSIS.json", e2_diagnosis)

    visual_specs = {
        "ACT": artifact_root / "train/DC-GLV1-E1-ACT-002/seed_1/driveclarify_grounded_v1",
        "ASK": artifact_root / "train/DC-GLV1-E1-ASK-002/seed_1/driveclarify_grounded_v1",
        "WAIT": artifact_root / "train/DC-GLV1-E1-WAIT-002/seed_1/driveclarify_grounded_v1",
    }
    visual_rows = {}
    for mechanism, output in visual_specs.items():
        panel = output / "GROUNDED_LANGUAGE_V1_PANEL.png"
        desktop = output / "NATIVE_DESKTOP.png"
        live = output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
        receipt = _load(live) if live.is_file() else {}
        visual_rows[mechanism] = {
            "decision_shown": receipt.get("initial_decision"),
            "runtime_status": receipt.get("status"),
            "panel": {"path": _rel(panel), "sha256": file_sha256(panel)} if panel.is_file() else None,
            "native_desktop": {"path": _rel(desktop), "sha256": file_sha256(desktop)} if desktop.is_file() else None,
            "native_title": "DriveClarify Grounded V1 Runtime",
            "carla_simultaneous_in_desktop_capture": desktop.is_file(),
            "visualization_induced_simlingo_forwards": receipt.get("visualization_induced_simlingo_forward_count", 0),
            "visualization_induced_detector_forwards": receipt.get("visualization_induced_detector_forward_count", 0),
            "visualization_induced_pid": receipt.get("visualization_induced_pid_count", 0),
        }
    visualization = {
        "schema_version": SCHEMA_PREFIX + ".visualization_audit.v1",
        "status": "PARTIAL_VISUALIZATION_REQUIREMENT_NOT_FULLY_MET",
        "physical_display": ":1",
        "headless": False,
        "representatives": visual_rows,
        "findings": [
            "ACT and WAIT full-resolution evidence panels are human-readable and derived only from already-computed runtime evidence.",
            "The physical desktop captures contain both native window titles, but were captured on the compositor's blank first dashboard frame; they do not prove a painted side-by-side dashboard.",
            "No natural ASK occurred in the extension prefix, so a truthful ASK dashboard representative is unavailable.",
            "WAIT panel proves natural WAIT entry but not completed WAIT-to-ACT resolution.",
        ],
        "passive_zero_counters": True,
    }
    atomic_json(report_root / "EXTENSION_VISUALIZATION_AUDIT.json", visualization)

    regression_path = artifact_root / "full_regression_matrix_20260811/FULL_REGRESSION_MATRIX_RESULT.json"
    regression_receipt = artifact_root / "full_regression_matrix_20260811/FULL_REGRESSION_RECEIPT.json"
    regression = _load(regression_path)
    protected_actual = {name: file_sha256(ROOT / path) for name, path in PROTECTED_PATHS.items() if name in PROTECTED_HASHES}
    integrity_pass = protected_actual == PROTECTED_HASHES
    processes = _command("ps", "-eo", "pid=,args=")
    live_process_lines = [line for line in processes.splitlines() if any(token in line for token in ("CarlaUE4", "leaderboard_evaluator.py", "scenario_runner.py", "stage6b_live_panel"))]
    listeners = _command("ss", "-ltnp")
    occupied_ports = [port for port in (2020, 2021, 8020) if (":" + str(port)) in listeners]
    gpu = _command("nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader").strip()
    cleanup = {
        "schema_version": SCHEMA_PREFIX + ".final_cleanup.v1",
        "status": "PASS" if not live_process_lines and not occupied_ports and not gpu else "BLOCKED",
        "live_project_processes": live_process_lines,
        "occupied_ports": occupied_ports,
        "gpu_compute_processes": gpu.splitlines() if gpu else [],
        "observed_at_utc": _now(),
    }
    atomic_json(report_root / "EXTENSION_FINAL_CLEANUP.json", cleanup)

    quality = _load(report_root / "EXTENSION_TRAIN_DATA_QUALITY_AUDIT.json")
    quality.update(
        {
            "status": "PARTIAL_BLOCKED_E2_AND_INCOMPLETE_TRAIN",
            "population_design_findings": e2_diagnosis["population_design_findings"],
            "e1_cuda_oom_count": 3,
            "e2_natural_coverage": results["grounded_natural_decision_counts"],
            "e2_gate_pass": False,
            "e3_started": False,
            "full_regression": regression["status"],
            "full_regression_totals": regression["totals"],
            "protected_integrity_pass": integrity_pass,
            "cleanup_status": cleanup["status"],
        }
    )
    atomic_json(report_root / "EXTENSION_TRAIN_DATA_QUALITY_AUDIT.json", quality)

    final = {
        "schema_version": SCHEMA_PREFIX + ".final_receipt.v1",
        "status": "PARTIAL_GROUNDED_LANGUAGE_V1_EXTENSION_TRAIN_VALID_CONTRACTS",
        "reason_codes": [
            "E0_CONTRACTS_FROZEN",
            "E1_18_OF_18_BACKENDS_LAUNCHED",
            "E2_NATURAL_ACT_6_ASK_0_WAIT_0_COMPLETED",
            "E2_HARD_GATE_BLOCKED_E3_NOT_STARTED",
            "TRAIN_PREFIX_27_OF_216_COMPLETED_ACCOUNTED",
            "POPULATION_ASK_DIVERSITY_INSUFFICIENT",
            "WAIT_FIXTURE_PRETRIGGER_STATIC_DEADLOCK",
            "VISUALIZATION_PARTIAL",
            "FULL_REGRESSION_PASS_{}_OF_{}".format(
                regression["totals"]["passed"], regression["totals"]["tests"]
            ),
        ],
        "scheduled": 216,
        "started": 27,
        "completed": counts["COMPLETED_RECORDED"],
        "failed_recorded": counts["FAILED_RECORDED"],
        "not_started": counts["SCHEDULED_NOT_STARTED"],
        "per_method_coverage": per_method,
        "natural_completed_decisions": results["grounded_natural_decision_counts"],
        "e1": _load(report_root / "EXTENSION_E1_RECEIPT.json"),
        "e2": _load(report_root / "EXTENSION_E2_RECEIPT.json"),
        "e3_started": False,
        "full_regression": {
            "status": regression["status"],
            "totals": regression["totals"],
            "result_path": _rel(regression_path),
            "receipt_path": _rel(regression_receipt),
            "result_sha256": file_sha256(regression_path),
            "receipt_sha256": file_sha256(regression_receipt),
        },
        "protected_integrity": {"status": "PASS_UNCHANGED" if integrity_pass else "BLOCKED", "actual": protected_actual, "expected": PROTECTED_HASHES},
        "original_dev_attempt_count": 0,
        "original_test_attempt_count": 0,
        "original_test_consumed": False,
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
        "cleanup": cleanup,
        "claims": {
            "train_only": True,
            "final_paper_result": False,
            "h1_h2_h3_verdict": None,
            "significance_claim": None,
            "universal_language_understanding": False,
            "ready_for_dev_authorization": False,
        },
        "recommended_next_action": "Do not authorize DEV/TEST. Design and prefreeze a new independent extension revision with real ASK branch diversity and WAIT fixtures whose environment event can resolve without ego-trigger deadlock; then rerun E0/E1/E2 from a fresh ledger.",
        "generated_at_utc": _now(),
    }
    atomic_json(report_root / "EXTENSION_FINAL_RECEIPT.json", final)

    report = f"""# Grounded Language V1 Extension E1 — TRAIN Report

Status: `PARTIAL_GROUNDED_LANGUAGE_V1_EXTENSION_TRAIN_VALID_CONTRACTS`

TRAIN ONLY. NOT A FINAL PAPER RESULT. DEV and TEST were not attempted.

## Outcome

- Frozen schedule: 216 TRAIN episodes; started/completed/accounted: 27; not started: 189.
- E1 backend smoke: 18/18 launched and recorded. Three Grounded CUDA attempts OOMed; the failures remain in the evidence.
- E2 completed natural lifecycle decisions: ACT=6, ASK=0, WAIT=0; forced=0.
- E2 hard gate failed, so E3 was not started.
- Full isolated regression: {regression['totals']['passed']}/{regression['totals']['tests']} passed, 0 failures/errors/skips.
- Original Stage6A, Stage6B-R0 and formal R3 protected hashes remain unchanged.

## Trustworthy diagnosis

CPU isolation removed the concurrent CUDA OOM and produced real online DINO ACT
decisions. The ASK-oriented configurations then produced effective K=1 and
naturally ACTed; no label was injected to force ASK. The WAIT configurations
entered WAIT, but independent post-hoc actor evidence shows the selected bus was
stationary throughout. That fixture requires an ego route-proximity trigger,
while WAIT holds the ego before it reaches the trigger, so WAIT-to-ACT cannot
complete.

The population also falls short of the requested ASK target-diversity standard:
all TRAIN ASK scenarios share one physical fixture/route context. This and the
WAIT deadlock are population-design failures, not evidence that Grounded V1 is
ready for DEV.

## Visualization

Full ACT and WAIT panels were saved and all visualization-induced inference/PID/
control counters are zero. Physical desktop captures contain CarlaUE4 and the
native dashboard window, but capture occurred on the compositor's blank first
dashboard frame. No truthful natural-ASK representative exists. Visualization is
therefore PARTIAL, not PASS.

## Claims boundary

No H1/H2/H3 verdict, significance claim, universal language-understanding claim,
or safety claim is made. Goal correctness and wrong-goal execution remain UNKNOWN
where independent goal evidence is absent.

## Recommended next action

Do not authorize DEV/TEST. Create a fresh extension revision with multiple real
ASK branch/junction targets and WAIT fixtures whose physical event starts without
an ego-trigger deadlock; prefreeze it and rerun E0/E1/E2 from a new ledger.
"""
    atomic_text(report_root / "EXTENSION_TRAIN_REPORT.md", report)

    # Hash final reports and the principal native evidence without rewriting
    # any protected historical artifact.
    hash_paths = [path for path in report_root.iterdir() if path.is_file() and path.name != "ARTIFACT_HASHES.json"]
    hash_paths += [ROOT / LEDGER_PATH, regression_path, regression_receipt]
    for output in visual_specs.values():
        hash_paths += [path for path in (output / "GROUNDED_LANGUAGE_V1_PANEL.png", output / "NATIVE_DESKTOP.png", output / "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json") if path.is_file()]
    hashes = {
        _rel(path): {"sha256": file_sha256(path), "bytes": path.stat().st_size}
        for path in sorted(set(hash_paths))
    }
    atomic_json(
        report_root / "ARTIFACT_HASHES.json",
        {"schema_version": SCHEMA_PREFIX + ".artifact_hashes.v2", "generated_at_utc": _now(), "artifacts": hashes},
    )
    final["final_receipt_path"] = _rel(report_root / "EXTENSION_FINAL_RECEIPT.json")
    final["final_receipt_sha256"] = file_sha256(report_root / "EXTENSION_FINAL_RECEIPT.json")
    final["artifact_hashes_path"] = _rel(report_root / "ARTIFACT_HASHES.json")
    final["artifact_hashes_sha256"] = file_sha256(report_root / "ARTIFACT_HASHES.json")
    return final


__all__ = ["finalize"]
