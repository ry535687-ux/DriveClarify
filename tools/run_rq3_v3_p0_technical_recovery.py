#!/usr/bin/env python3
"""Narrow Traffic Manager recovery and authorized RQ3-V3 P0 resume."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.run_rq3_v3_p0_scene_qualification as p0  # noqa: E402


STAGE = "RQ3_V3_P0_TECHNICAL_RECOVERY_AND_RESUME_V1"
SCOPE = "NON_SCIENTIFIC_INFRASTRUCTURE_RECOVERY_AND_ENGINEERING_P0_RESUME"
REPORT = ROOT / "reports/driveclarify_rq3_v3_p0_technical_recovery_and_resume_v1"
ORIGINAL = ROOT / "reports/driveclarify_rq3_v3_p0_scene_qualification_v1"
RUNNER = ROOT / "tools/run_rq3_v3_p0_recovery_episode.sh"
TOOL = ROOT / "tools/run_rq3_v3_p0_technical_recovery.py"
SMOKE_ROUTE = Path(
    "/home/buaa/wrh/simlingo/leaderboard/data/bench2drive_split/bench2drive_08.xml"
)
SMOKE_CONFIG = REPORT / "infrastructure_smoke/INFRASTRUCTURE_SMOKE_CONFIG.json"
SMOKE_OUTPUT = REPORT / "infrastructure_smoke/native_output"
RECOVERY_RUNS = REPORT / "resumed_native_runs"
RECOVERY_PORTS = {
    19: {"rpc": 22400, "streaming": 22401, "tm": 22502},
    20: {"rpc": 22800, "streaming": 22801, "tm": 22902},
    21: {"rpc": 23200, "streaming": 23201, "tm": 23302},
    22: {"rpc": 23600, "streaming": 23601, "tm": 23702},
    23: {"rpc": 24000, "streaming": 24001, "tm": 24102},
    24: {"rpc": 24400, "streaming": 24401, "tm": 24502},
}
SMOKE_PORTS = {"rpc": 22000, "streaming": 22001, "tm": 22102}
ALLOWED_FINAL_STATUSES = {
    "PASS_RQ3_V3_P0_ALL_SCENES_QUALIFIED",
    "FAIL_RQ3_V3_P0_SCENE_NOT_NATIVELY_QUALIFIED",
    "BLOCKED_RQ3_V3_P0_TECHNICALLY_INVALID",
    "BLOCKED_RQ3_V3_P0_TECHNICAL_RECOVERY_FAILED",
    "BLOCKED_RQ3_V3_P0_SOURCE_OR_INTEGRITY_FAILURE",
}


def load(path: Path, default: Any = None) -> Any:
    return p0.load(path, default)


def write_json(path: Path, value: Any) -> None:
    p0.write_json(path, value)


def write_text(path: Path, value: str) -> None:
    p0.write_text(path, value)


def digest(value: Any) -> str:
    return p0.digest(value)


def rel(path: Path) -> str:
    return p0.rel(path)


def hash_record(path: Path) -> Dict[str, Any]:
    return p0.hash_record(path)


def tree_record(path: Path) -> Dict[str, Any]:
    return p0.tree_record(path)


def recovery_source_rows() -> List[Dict[str, Any]]:
    original_freeze = load(ORIGINAL / "P0_SCENE_FREEZE_RECEIPT.json", {})
    paths = [Path(row["path"]) for row in original_freeze.get("source_rows", [])]
    resolved = [path if path.is_absolute() else ROOT / path for path in paths]
    resolved.extend([TOOL, RUNNER])
    missing = [str(path) for path in resolved if not path.is_file()]
    if missing:
        raise RuntimeError("RECOVERY_SOURCE_MISSING:" + ",".join(missing))
    return [hash_record(path) for path in resolved]


def verify_original_entry() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    roster = load(ORIGINAL / "P0_EXECUTION_ROSTER.json", {})
    ledger = load(ORIGINAL / "P0_EXECUTION_LEDGER.json", {})
    results = load(ORIGINAL / "P0_RESULTS.json", {})
    if len(roster.get("runs", [])) != 24:
        raise RuntimeError("ORIGINAL_P0_ROSTER_NOT_24")
    if ledger.get("status") != "STOPPED_P0_TECHNICALLY_INVALID":
        raise RuntimeError("ORIGINAL_P0_NOT_AT_AUTHORIZED_BLOCKED_STATE")
    if ledger.get("attempted_runs") != 19 or results.get("valid_runs") != 18:
        raise RuntimeError("ORIGINAL_P0_COUNTS_INVALID")
    if ledger.get("technical_invalidity_ordinal") != 19:
        raise RuntimeError("ORIGINAL_P0_INVALIDITY_NOT_ORDINAL_19")
    if [
        row["ordinal"]
        for row in ledger["entries"]
        if row.get("execution_status") == "PENDING"
    ] != [20, 21, 22, 23, 24]:
        raise RuntimeError("ORIGINAL_P0_PENDING_SET_INVALID")
    if any(
        row.get("validity") != "VALID" for row in ledger["entries"][:18]
    ):
        raise RuntimeError("ORIGINAL_P0_1_TO_18_NOT_ALL_VALID")
    if ledger["entries"][18].get("validity") != "P0_TECHNICALLY_INVALID":
        raise RuntimeError("ORIGINAL_P0_ORDINAL_19_NOT_TECHNICALLY_INVALID")
    return roster, ledger, results


def make_smoke_config() -> Dict[str, Any]:
    template = load(ORIGINAL / "configs/RQ3V3-P0-REF-EQUIVALENT-S01.json")
    template["run_id"] = "RQ3V3-P0-TM-INFRASTRUCTURE-SMOKE-V1"
    template["method_input"]["instruction"] = "Follow the assigned route."
    template["method_input"].pop("p0_scene_binding", None)
    template["engineering_qualification"] = {
        "stage": STAGE,
        "scope": "NON_SCIENTIFIC_INFRASTRUCTURE_SMOKE",
        "future_scientific_denominator_eligible": False,
        "not_a_p0_cell": True,
        "scientific_behavior_changes": [],
    }
    template["scientific_seed_not_available_to_method"] = 0
    return template


def prepare() -> Dict[str, Any]:
    if REPORT.exists():
        raise RuntimeError("RECOVERY_REPORT_DIRECTORY_ALREADY_EXISTS")
    roster, ledger, results = verify_original_entry()
    original_tree = tree_record(ORIGINAL)
    original_artifacts = [
        "P0_EXECUTION_ROSTER.json",
        "P0_EXECUTION_LEDGER.json",
        "P0_RESULTS.json",
        "P0_SCENE_MANIFEST.json",
        "P0_SCENE_FREEZE_RECEIPT.json",
        "P0_SEED_FRESHNESS_RECEIPT.json",
        "P0_SOURCE_INTEGRITY_RECEIPT.json",
        "P0_TECHNICAL_INVALIDITY_DIAGNOSIS.json",
        "USC_EQUIVALENT_REPLACEMENT_CERTIFICATION.json",
    ]
    REPORT.mkdir(parents=True)
    SMOKE_CONFIG.parent.mkdir(parents=True)
    write_json(SMOKE_CONFIG, make_smoke_config())
    diagnosis = (
        "# Traffic Manager RPC binding diagnosis\n\n"
        "Classification: `C. PORT_CONFIGURATION_COLLISION`.\n\n"
        "Ordinal 19 used CARLA RPC 34600, streaming 34601, and Traffic Manager "
        "RPC 34702. The host kernel assigns unreserved ephemeral client ports from "
        "32768 through 60999; `ip_local_reserved_ports` is empty. Thus 34702 was "
        "eligible for transient client allocation after the launcher's early "
        "listener-only preflight and before `client.get_trafficmanager(34702)` "
        "attempted its server bind.\n\n"
        "The evaluator log records `RuntimeError: trying to create rpc server for "
        "traffic manager; but the system failed to create because of bind error.` "
        "A focused non-scientific socket reproduction bound an outgoing client to "
        "local port 34702, closed it into `TIME-WAIT`, and immediately reproduced "
        "server-bind failure `errno=98 EADDRINUSE` on 0.0.0.0:34702.\n\n"
        "Stale-owned-process and prior-cleanup explanations are contradicted by the "
        "evidence: ordinal 18 used a different TM port (34502), its cleanup passed, "
        "ordinal 19 cleanup passed, no process residue remained, and all six ports "
        "were absent after the failure. There is no evidence of an unrelated "
        "persistent listener.\n\n"
        "The narrow repair allocates recovery-only launcher ports below 32768, "
        "outside the ephemeral range; checks listener freedom and real bindability "
        "before CARLA launch; rechecks TM bindability after the world-ready client "
        "probe; and retains normal process-group cleanup with post-cleanup listener "
        "release verification. It does not alter TM seed/randomness or any scientific "
        "component.\n"
    )
    write_text(REPORT / "TM_RPC_BINDING_DIAGNOSIS.md", diagnosis)
    receipt = {
        "schema": "driveclarify.rq3-v3-p0.tm-rpc-repair-receipt.v1",
        "stage": STAGE,
        "status": "FROZEN_REPAIR_PENDING_INFRASTRUCTURE_SMOKE",
        "defect_classification": "C_PORT_CONFIGURATION_COLLISION",
        "original_failed_ports": {"rpc": 34600, "streaming": 34601, "tm": 34702},
        "kernel_ephemeral_range": [32768, 60999],
        "kernel_reserved_ephemeral_ports": [],
        "focused_reproduction": {
            "local_client_port": 34702,
            "post_close_state": "TIME-WAIT",
            "server_bind_result": "EADDRINUSE_ERRNO_98",
            "scientific_or_p0_execution": False,
        },
        "repair": {
            "description": (
                "dedicated below-ephemeral recovery ports plus prelaunch listener/"
                "bind probes, post-world TM bind probe, and verified cleanup"
            ),
            "smoke_ports": SMOKE_PORTS,
            "resume_ports": RECOVERY_PORTS,
            "traffic_manager_seed_semantics_changed": False,
            "scientific_components_changed": [],
        },
        "smoke_test": None,
        "formal_seed_generated": False,
        "formal_execution_started": False,
    }
    receipt["receipt_digest"] = digest(receipt)
    write_json(REPORT / "TM_RPC_REPAIR_RECEIPT.json", receipt)
    resume_freeze = {
        "schema": "driveclarify.rq3-v3-p0.resume-freeze-receipt.v1",
        "stage": STAGE,
        "status": "FROZEN_BEFORE_RECOVERY_SMOKE_OR_P0_RESUME",
        "authorization": "NARROW_TECHNICAL_RECOVERY_AND_RESUME",
        "original_p0_tree_entry": original_tree,
        "original_artifacts": [hash_record(ORIGINAL / name) for name in original_artifacts],
        "original_roster_digest": roster["roster_digest"],
        "original_ledger_digest": ledger["ledger_digest"],
        "original_results_digest": results["results_digest"],
        "ordinals_1_to_18_rerun": False,
        "original_ordinal_19_invalid_attempt_preserved": True,
        "ordinal_19_authorized_recovery_attempt_limit": 1,
        "resume_ordinals": [19, 20, 21, 22, 23, 24],
        "resume_cell_identity_fields_immutable": [
            "run_id",
            "scene_id",
            "scene_code",
            "scene_digest",
            "seed_slot",
            "engineering_seed",
            "config_path",
            "config_sha256",
            "route_path",
            "route_sha256",
            "route_id",
            "town",
            "agent_mode",
            "runtime_mode",
        ],
        "allowed_infrastructure_override": "launcher port allocation only",
        "recovery_ports": RECOVERY_PORTS,
        "p0_seeds": [1020829307, 1777599015, 600722059],
        "p0_seeds_changed": False,
        "scenes_changed": False,
        "usc_certification_changed": False,
        "scientific_components_changed": [],
        "formal_seeds_generated": False,
        "formal_execution_started": False,
        "source_rows": recovery_source_rows(),
        "source_digest": digest(recovery_source_rows()),
        "smoke_config": hash_record(SMOKE_CONFIG),
        "smoke_route": hash_record(SMOKE_ROUTE),
    }
    resume_freeze["freeze_digest"] = digest(resume_freeze)
    write_json(REPORT / "P0_RESUME_FREEZE_RECEIPT.json", resume_freeze)
    resumed_entries = []
    for row in roster["runs"]:
        ordinal = row["ordinal"]
        if ordinal <= 18:
            old_entry = ledger["entries"][ordinal - 1]
            resumed_entries.append(
                {
                    **row,
                    "resume_status": "CARRIED_FORWARD_VALID_NOT_RERUN",
                    "rerun": False,
                    "validity": "VALID",
                    "native_qualification_pass": old_entry[
                        "native_qualification_pass"
                    ],
                    "run_result_path": old_entry["run_result_path"],
                    "run_result_digest": old_entry["run_result_digest"],
                }
            )
        elif ordinal == 19:
            resumed_entries.append(
                {
                    **row,
                    "resume_status": "AUTHORIZED_RECOVERY_PENDING",
                    "original_invalid_attempt": {
                        "preserved_path": row["output_path"],
                        "validity": "P0_TECHNICALLY_INVALID",
                        "scientific_exposure": False,
                        "native_outcome": None,
                    },
                    "authorized_recovery_attempts": 0,
                    "validity": None,
                    "native_qualification_pass": None,
                }
            )
        else:
            resumed_entries.append(
                {
                    **row,
                    "resume_status": "PENDING_ORIGINAL_FROZEN_CELL",
                    "rerun": False,
                    "validity": None,
                    "native_qualification_pass": None,
                }
            )
    resumed_ledger = {
        "schema": "driveclarify.rq3-v3-p0.resumed-execution-ledger.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "status": "FROZEN_PENDING_REPAIR_QUALIFICATION",
        "original_roster_digest": roster["roster_digest"],
        "planned_original_cells": 24,
        "carried_forward_valid_cells": 18,
        "authorized_recovery_attempts": 0,
        "newly_attempted_original_cells": 0,
        "total_valid_original_cells": 18,
        "entries": resumed_entries,
    }
    resumed_ledger["ledger_digest"] = digest(resumed_ledger)
    write_json(REPORT / "P0_RESUMED_EXECUTION_LEDGER.json", resumed_ledger)
    preservation = {
        "schema": "driveclarify.rq3-v3-p0.source-history-preservation.v1",
        "stage": STAGE,
        "status": "PASS_PREEXECUTION_RECOVERY_FREEZE",
        "original_p0_tree_entry": original_tree,
        "original_p0_tree_exit": None,
        "original_p0_tree_changed": None,
        "frozen_source_rows": resume_freeze["source_rows"],
        "current_source_rows": resume_freeze["source_rows"],
        "source_changes": [],
        "checkpoint": hash_record(p0.CHECKPOINT),
        "checkpoint_valid": p0.sha_file(p0.CHECKPOINT) == p0.CHECKPOINT_SHA256,
        "scenes_changed": False,
        "p0_seeds_changed": False,
        "scientific_components_changed": [],
        "formal_seeds_generated": False,
        "formal_execution_started": False,
    }
    preservation["receipt_digest"] = digest(preservation)
    write_json(REPORT / "SOURCE_AND_HISTORY_PRESERVATION_RECEIPT.json", preservation)
    write_text(
        REPORT / "COMMAND_LOG.md",
        "# Command log\n\n"
        "- Read-only diagnosis: kernel ephemeral range, reserved-port policy, "
        "evaluator setup order, prior cleanup receipts, live socket/process state.\n"
        "- Focused non-scientific socket reproduction: local 34702 `TIME-WAIT` "
        "caused server-bind `EADDRINUSE` (errno 98).\n"
        "- `python tools/run_rq3_v3_p0_technical_recovery.py prepare`\n"
        "  - froze narrow infrastructure repair and original-cell resume identity\n"
        "  - formal seeds generated: `NO`; formal execution started: `NO`\n",
    )
    return resume_freeze


def compare_sources(frozen: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    current = recovery_source_rows()
    before = {row["path"]: row for row in frozen}
    after = {row["path"]: row for row in current}
    changes = [
        {
            "path": path,
            "frozen_sha256": before.get(path, {}).get("sha256"),
            "current_sha256": after.get(path, {}).get("sha256"),
        }
        for path in sorted(set(before) | set(after))
        if before.get(path, {}).get("sha256") != after.get(path, {}).get("sha256")
    ]
    return current, changes


def verify_recovery_integrity() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    freeze = load(REPORT / "P0_RESUME_FREEZE_RECEIPT.json", {})
    if freeze.get("status") != "FROZEN_BEFORE_RECOVERY_SMOKE_OR_P0_RESUME":
        raise RuntimeError("RECOVERY_FREEZE_MISSING")
    if digest({k: v for k, v in freeze.items() if k != "freeze_digest"}) != freeze.get("freeze_digest"):
        raise RuntimeError("RECOVERY_FREEZE_DIGEST_MISMATCH")
    current, changes = compare_sources(freeze["source_rows"])
    if p0.sha_file(p0.CHECKPOINT) != p0.CHECKPOINT_SHA256:
        changes.append({"path": rel(p0.CHECKPOINT), "reason": "CHECKPOINT_DRIFT"})
    original_now = tree_record(ORIGINAL)
    if original_now["tree_digest"] != freeze["original_p0_tree_entry"]["tree_digest"]:
        changes.append(
            {
                "path": rel(ORIGINAL),
                "reason": "ORIGINAL_P0_TREE_CHANGED",
                "entry": freeze["original_p0_tree_entry"]["tree_digest"],
                "current": original_now["tree_digest"],
            }
        )
    return {"source_rows": current, "original_tree": original_now}, changes


def run_episode(
    config: Path,
    route: Path,
    seed: int,
    ports: Mapping[str, int],
    output: Path,
) -> subprocess.CompletedProcess:
    command = [
        str(RUNNER),
        str(config),
        str(route),
        str(seed),
        str(ports["rpc"]),
        str(ports["tm"]),
        str(output),
    ]
    with (REPORT / "COMMAND_LOG.md").open("a", encoding="utf-8") as stream:
        stream.write("\n- `%s`\n" % " ".join(command))
    return subprocess.run(command, cwd=str(ROOT), check=False)


def run_smoke() -> Dict[str, Any]:
    _, changes = verify_recovery_integrity()
    if changes:
        raise RuntimeError("RECOVERY_INTEGRITY_FAILED_BEFORE_SMOKE:" + repr(changes))
    repair = load(REPORT / "TM_RPC_REPAIR_RECEIPT.json", {})
    if repair.get("status") != "FROZEN_REPAIR_PENDING_INFRASTRUCTURE_SMOKE":
        raise RuntimeError("REPAIR_NOT_PENDING_SMOKE")
    completed = run_episode(SMOKE_CONFIG, SMOKE_ROUTE, 0, SMOKE_PORTS, SMOKE_OUTPUT)
    summary = load(SMOKE_OUTPUT / "RUN_TERMINAL_SUMMARY.json", {})
    navigation = load(
        SMOKE_OUTPUT / "owner_evidence/NATIVE_NAVIGATION_INPUT_CONTRACT.json", {}
    )
    runtime = load(SMOKE_OUTPUT / "owner_evidence/ENGINEERING_RUNTIME_MODE.json", {})
    process = load(SMOKE_OUTPUT / "process_job/PROCESS_RECEIPT.json", {})
    ports = load(SMOKE_OUTPUT / "process_job/INFRASTRUCTURE_PORT_RECEIPT.json", {})
    evaluator_log = (
        SMOKE_OUTPUT / "process_job/evaluator.log"
    ).read_text(encoding="utf-8", errors="replace")
    checks = {
        "required_ports_outside_ephemeral_range": ports.get(
            "all_ports_outside_ephemeral_range"
        )
        is True,
        "prelaunch_ports_listener_free_and_bindable": ports.get(
            "prelaunch_bind_pass"
        )
        is True,
        "postworld_tm_port_bindable": ports.get("postworld_tm_bind_pass") is True,
        "traffic_manager_bound_without_bind_error": "failed to create because of bind error"
        not in evaluator_log,
        "agent_interface_initialized": navigation.get(
            "native_set_global_plan_called_once"
        )
        is True,
        "native_agent_mode": navigation.get("mode") == "NATIVE_SIMLINGO",
        "checkpoint_identity": navigation.get("checkpoint_sha256")
        == p0.CHECKPOINT_SHA256,
        "native_runtime_unchanged": runtime.get("mode") == "NATIVE_DEFAULT"
        and runtime.get("changed_runtime_flags") == [],
        "evaluator_record_present": (summary.get("official") or {}).get(
            "record_present"
        )
        is True,
        "cleanup_pass": process.get("cleanup_pass") is True,
        "listeners_released": ports.get("listeners_released_after_cleanup") is True,
    }
    passed = all(checks.values())
    repair["status"] = (
        "PASS_TM_RPC_NARROW_REPAIR_QUALIFIED"
        if passed
        else "FAIL_TM_RPC_NARROW_REPAIR_QUALIFICATION"
    )
    repair["smoke_test"] = {
        "scope": "NON_SCIENTIFIC_INFRASTRUCTURE_SMOKE",
        "not_a_p0_cell": True,
        "seed": 0,
        "new_p0_seed": False,
        "formal_seed": False,
        "output_path": rel(SMOKE_OUTPUT),
        "wrapper_returncode": completed.returncode,
        "checks": checks,
        "pass": passed,
    }
    repair["receipt_digest"] = digest(
        {key: value for key, value in repair.items() if key != "receipt_digest"}
    )
    write_json(REPORT / "TM_RPC_REPAIR_RECEIPT.json", repair)
    ledger = load(REPORT / "P0_RESUMED_EXECUTION_LEDGER.json", {})
    ledger["status"] = (
        "REPAIR_QUALIFIED_READY_TO_RESUME"
        if passed
        else "BLOCKED_REPAIR_QUALIFICATION_FAILED"
    )
    ledger["repair_qualification"] = repair["status"]
    write_ledger(ledger)
    return repair


def write_ledger(ledger: Dict[str, Any]) -> None:
    value = {key: item for key, item in ledger.items() if key != "ledger_digest"}
    value["ledger_digest"] = digest(value)
    write_json(REPORT / "P0_RESUMED_EXECUTION_LEDGER.json", value)


def execute_resume() -> Dict[str, Any]:
    roster, _, _ = verify_original_entry()
    repair = load(REPORT / "TM_RPC_REPAIR_RECEIPT.json", {})
    ledger = load(REPORT / "P0_RESUMED_EXECUTION_LEDGER.json", {})
    if repair.get("status") != "PASS_TM_RPC_NARROW_REPAIR_QUALIFIED":
        raise RuntimeError("TM_REPAIR_NOT_QUALIFIED")
    if ledger.get("status") != "REPAIR_QUALIFIED_READY_TO_RESUME":
        raise RuntimeError("RESUMED_LEDGER_NOT_READY")
    _, changes = verify_recovery_integrity()
    if changes:
        ledger["status"] = "STOPPED_SOURCE_OR_INTEGRITY_FAILURE"
        ledger["integrity_changes"] = changes
        write_ledger(ledger)
        return ledger
    ledger["status"] = "RESUME_RUNNING"
    ledger["resume_started_epoch"] = int(time.time())
    write_ledger(ledger)
    for ordinal in range(19, 25):
        entry = ledger["entries"][ordinal - 1]
        planned = dict(roster["runs"][ordinal - 1])
        _, changes = verify_recovery_integrity()
        if changes:
            ledger["status"] = "STOPPED_SOURCE_OR_INTEGRITY_FAILURE"
            ledger["integrity_changes"] = changes
            ledger["stop_before_ordinal"] = ordinal
            write_ledger(ledger)
            return ledger
        if ordinal == 19:
            output = RECOVERY_RUNS / "ordinal_19_authorized_recovery" / planned["run_id"]
            entry["authorized_recovery_attempts"] = 1
            ledger["authorized_recovery_attempts"] = 1
        else:
            output = RECOVERY_RUNS / planned["run_id"]
        started = int(time.time())
        completed = run_episode(
            ROOT / planned["config_path"],
            ROOT / planned["route_path"],
            planned["engineering_seed"],
            RECOVERY_PORTS[ordinal],
            output,
        )
        evaluated_plan = dict(planned)
        evaluated_plan["output_path"] = rel(output)
        run_result = p0.evaluate_run(evaluated_plan)
        entry.update(
            {
                "resume_status": "ATTEMPTED_IN_AUTHORIZED_RESUME",
                "recovery_output_path": rel(output),
                "recovery_infrastructure_ports": RECOVERY_PORTS[ordinal],
                "started_epoch": started,
                "finished_epoch": int(time.time()),
                "wrapper_returncode": completed.returncode,
                "validity": run_result["validity"],
                "native_qualification_pass": run_result[
                    "native_qualification_pass"
                ],
                "run_result_path": rel(output / "P0_RUN_RESULT.json"),
                "run_result_digest": run_result["run_result_digest"],
            }
        )
        ledger["newly_attempted_original_cells"] = ordinal - 18
        if run_result["valid"]:
            ledger["total_valid_original_cells"] = ordinal
        if ordinal == 19:
            attempt_receipt = {
                "schema": "driveclarify.rq3-v3-p0.authorized-recovery-attempt.v1",
                "stage": STAGE,
                "ordinal": 19,
                "cell_identity": planned["run_id"],
                "scene_id": planned["scene_id"],
                "engineering_seed": planned["engineering_seed"],
                "same_frozen_config_sha256": planned["config_sha256"],
                "same_frozen_route_sha256": planned["route_sha256"],
                "original_invalid_attempt_preserved": True,
                "attempt_number": 1,
                "authorized_infrastructure_recovery": True,
                "scientific_retry": False,
                "seed_replacement": False,
                "validity": run_result["validity"],
                "run_result_digest": run_result["run_result_digest"],
            }
            attempt_receipt["receipt_digest"] = digest(attempt_receipt)
            write_json(REPORT / "ORDINAL_19_AUTHORIZED_RECOVERY_RECEIPT.json", attempt_receipt)
        if not run_result["valid"]:
            ledger["status"] = (
                "STOPPED_ORDINAL_19_TECHNICAL_RECOVERY_FAILED"
                if ordinal == 19
                else "STOPPED_NEW_P0_TECHNICAL_INVALIDITY"
            )
            ledger["technical_invalidity_ordinal"] = ordinal
            ledger["technical_invalidity_reasons"] = run_result[
                "technical_invalidity_reasons"
            ]
            write_ledger(ledger)
            print(
                "P0 recovery stopped at ordinal %d: technical invalidity" % ordinal,
                flush=True,
            )
            return ledger
        write_ledger(ledger)
        print(
            "P0 resume progress: ordinal %d valid; %d/24 original cells valid"
            % (ordinal, ledger["total_valid_original_cells"]),
            flush=True,
        )
    ledger["status"] = "COMPLETED_24_OF_24_VALID_ORIGINAL_CELLS"
    ledger["resume_finished_epoch"] = int(time.time())
    write_ledger(ledger)
    return ledger


def seal_preservation() -> Dict[str, Any]:
    freeze = load(REPORT / "P0_RESUME_FREEZE_RECEIPT.json", {})
    current, changes = compare_sources(freeze["source_rows"])
    original_exit = tree_record(ORIGINAL)
    original_changed = (
        original_exit["tree_digest"]
        != freeze["original_p0_tree_entry"]["tree_digest"]
    )
    checkpoint_valid = p0.sha_file(p0.CHECKPOINT) == p0.CHECKPOINT_SHA256
    passed = not changes and not original_changed and checkpoint_valid
    receipt = {
        "schema": "driveclarify.rq3-v3-p0.source-history-preservation.v1",
        "stage": STAGE,
        "status": (
            "PASS_SOURCE_AND_HISTORY_PRESERVED"
            if passed
            else "FAIL_SOURCE_OR_HISTORY_INTEGRITY"
        ),
        "original_p0_tree_entry": freeze["original_p0_tree_entry"],
        "original_p0_tree_exit": original_exit,
        "original_p0_tree_changed": original_changed,
        "frozen_source_rows": freeze["source_rows"],
        "current_source_rows": current,
        "source_changes": changes,
        "checkpoint": hash_record(p0.CHECKPOINT),
        "checkpoint_valid": checkpoint_valid,
        "scenes_changed": False,
        "p0_seeds_changed": False,
        "scientific_components_changed": [],
        "formal_seeds_generated": False,
        "formal_execution_started": False,
        "pass": passed,
    }
    receipt["receipt_digest"] = digest(receipt)
    write_json(REPORT / "SOURCE_AND_HISTORY_PRESERVATION_RECEIPT.json", receipt)
    return receipt


def final_analysis() -> Dict[str, Any]:
    roster, _, _ = verify_original_entry()
    ledger = load(REPORT / "P0_RESUMED_EXECUTION_LEDGER.json", {})
    repair = load(REPORT / "TM_RPC_REPAIR_RECEIPT.json", {})
    preservation = seal_preservation()
    manifest = load(ORIGINAL / "P0_SCENE_MANIFEST.json", {})
    seeds = load(ORIGINAL / "P0_SEED_FRESHNESS_RECEIPT.json", {})
    usc = load(ORIGINAL / "USC_EQUIVALENT_REPLACEMENT_CERTIFICATION.json", {})
    by_scene: Dict[str, List[Dict[str, Any]]] = {code: [] for code in p0.SCENE_CODES}
    for entry in ledger.get("entries", []):
        result_path = entry.get("run_result_path")
        if entry.get("validity") == "VALID" and result_path:
            by_scene[entry["scene_code"]].append(load(ROOT / result_path))
    scene_results = []
    for scene in manifest.get("scenes", []):
        rows = by_scene[scene["scene_code"]]
        valid_count = len(rows)
        pass_count = sum(bool(row["native_qualification_pass"]) for row in rows)
        repeated_stop = sum(bool(row["persistent_stop_classes"]) for row in rows) >= 2
        binding_valid = valid_count == 3 and all(
            all(row["route_owner_binding_checks"].values())
            and all(row["evidence_checks"].values())
            and all(row["observer_integrity_checks"].values())
            for row in rows
        )
        qualified = binding_valid and pass_count >= 2 and not repeated_stop
        collisions = []
        for row in rows:
            inf = row["official"].get("infractions") or {}
            collisions.append(
                sum(
                    int(inf.get(key, 0) or 0)
                    for key in (
                        "collisions_layout",
                        "collisions_pedestrian",
                        "collisions_vehicle",
                    )
                )
            )
        scene_results.append(
            {
                "condition": scene["scene_code"],
                "scene_id": scene["scene_id"],
                "planned": 3,
                "valid": valid_count,
                "native_task_success_or_completion": pass_count,
                "route_completion_percent": [
                    row["official"].get("route_completion") for row in rows
                ],
                "native_stall_or_noncompletion_count": sum(
                    row["native_stall_or_noncompletion"] for row in rows
                ),
                "collision_counts": collisions,
                "relevant_official_infractions": [
                    row["official"].get("infractions") for row in rows
                ],
                "route_owner_binding_valid": binding_valid,
                "qualification": (
                    "PASS"
                    if qualified
                    else ("FAIL" if valid_count == 3 else "UNRESOLVED")
                ),
            }
        )
    if not preservation["pass"]:
        status = "BLOCKED_RQ3_V3_P0_SOURCE_OR_INTEGRITY_FAILURE"
    elif ledger.get("status") == "STOPPED_ORDINAL_19_TECHNICAL_RECOVERY_FAILED":
        status = "BLOCKED_RQ3_V3_P0_TECHNICAL_RECOVERY_FAILED"
    elif ledger.get("status") in {
        "STOPPED_NEW_P0_TECHNICAL_INVALIDITY",
        "BLOCKED_REPAIR_QUALIFICATION_FAILED",
    }:
        status = "BLOCKED_RQ3_V3_P0_TECHNICALLY_INVALID"
    elif ledger.get("status") != "COMPLETED_24_OF_24_VALID_ORIGINAL_CELLS":
        status = "BLOCKED_RQ3_V3_P0_TECHNICALLY_INVALID"
    elif all(row["qualification"] == "PASS" for row in scene_results):
        status = "PASS_RQ3_V3_P0_ALL_SCENES_QUALIFIED"
    else:
        status = "FAIL_RQ3_V3_P0_SCENE_NOT_NATIVELY_QUALIFIED"
    if status not in ALLOWED_FINAL_STATUSES:
        raise RuntimeError("RECOVERY_FINAL_STATUS_INVALID")
    results = {
        "schema": "driveclarify.rq3-v3-p0.final-eight-scene-results.v1",
        "stage": STAGE,
        "scope": SCOPE,
        "final_status": status,
        "traffic_manager_defect_classification": "C_PORT_CONFIGURATION_COLLISION",
        "repair_qualification": repair.get("status"),
        "planned_original_cells": 24,
        "valid_original_cells": sum(len(rows) for rows in by_scene.values()),
        "ordinals_1_to_18_rerun": False,
        "original_ordinal_19_invalid_attempt_preserved": True,
        "ordinal_19_authorized_recovery_attempts": ledger.get(
            "authorized_recovery_attempts", 0
        ),
        "scene_results": scene_results,
        "usc_replacement_task_equivalent_certified": (
            usc.get("task_relation") == "TASK_EQUIVALENT"
            and usc.get("correct_driveclarify_behavior") == "ACT"
        ),
        "p0_seed_identities": seeds.get("ordered_p0_engineering_seed_identities"),
        "p0_seeds_changed": False,
        "scenes_changed": False,
        "scientific_components_changed": [],
        "formal_seeds_generated": False,
        "formal_execution_started": False,
        "formal_rq3_v3_frozen": False,
        "next_step": "INDEPENDENT_REVIEW_AND_SEPARATE_FORMAL_FREEZE_AUTHORIZATION",
    }
    results["results_digest"] = digest(results)
    write_json(REPORT / "P0_FINAL_8_SCENE_RESULTS.json", results)
    table = [
        "| Condition | Scene identity | Planned | Valid | Native pass | Route Completion (%) | Stall/noncompletion | Collisions | Binding | Qualification |",
        "|---|---|---:|---:|---:|---|---:|---|---|---|",
    ]
    for row in scene_results:
        table.append(
            "| %s | `%s` | 3 | %d | %d | %s | %d | %s | %s | **%s** |"
            % (
                row["condition"],
                row["scene_id"],
                row["valid"],
                row["native_task_success_or_completion"],
                ", ".join(
                    "null" if value is None else "%g" % value
                    for value in row["route_completion_percent"]
                ),
                row["native_stall_or_noncompletion_count"],
                ", ".join(str(value) for value in row["collision_counts"]),
                "PASS" if row["route_owner_binding_valid"] else "FAIL",
                row["qualification"],
            )
        )
    report = (
        "# RQ3-V3 P0 technical recovery and resumed qualification\n\n"
        "Final status: `%s`\n\n"
        "Traffic Manager defect classification: `C. PORT_CONFIGURATION_COLLISION`.\n\n"
        "Repair applied: dedicated recovery launcher ports below the kernel "
        "ephemeral range, with prelaunch listener/bind probes, a post-world TM "
        "bind probe, and verified post-run cleanup. TM behavior and seed semantics "
        "were unchanged.\n\n"
        "## Final eight-scene table\n\n%s\n\n"
        "## Required closure facts\n\n"
        "- Original 24-cell roster valid: `%d/24`.\n"
        "- Ordinals 1–18 rerun: `NO`.\n"
        "- Original ordinal 19 invalid attempt preserved: `YES`.\n"
        "- Ordinal 19 authorized recovery attempts: `%d`.\n"
        "- P0 seeds: `%s`; changed: `NO`.\n"
        "- Scenes changed: `NO`.\n"
        "- Scientific components changed: `NONE`.\n"
        "- USC replacement certification: `TASK_EQUIVALENT → ACT`, unchanged.\n"
        "- Formal seeds generated: `NO`.\n"
        "- Formal execution started: `NO`.\n"
        "- Formal RQ3-V3 freeze started: `NO`.\n"
        "- Source and original P0 history preservation: `%s`.\n"
        "- Next step: independent review and separate formal-freeze authorization.\n"
        % (
            status,
            "\n".join(table),
            results["valid_original_cells"],
            results["ordinal_19_authorized_recovery_attempts"],
            ", ".join(str(seed) for seed in results["p0_seed_identities"]),
            preservation["status"],
        )
    )
    write_text(REPORT / "FINAL_REPORT.md", report)
    with (REPORT / "COMMAND_LOG.md").open("a", encoding="utf-8") as stream:
        stream.write(
            "\n- `python tools/run_rq3_v3_p0_technical_recovery.py analyze`\n"
            "  - final status: `%s`\n"
            "  - formal seeds generated: `NO`; formal execution started: `NO`\n"
            % status
        )
    return results


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    sub = value.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("smoke")
    sub.add_parser("resume")
    sub.add_parser("analyze")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "prepare":
        result = prepare()
    elif args.command == "smoke":
        result = run_smoke()
    elif args.command == "resume":
        result = execute_resume()
    elif args.command == "analyze":
        result = final_analysis()
    else:
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
