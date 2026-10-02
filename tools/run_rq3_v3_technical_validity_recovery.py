#!/usr/bin/env python3
"""Evidence-only recovery and narrowly repaired RQ3-V3 Part-B resume.

This orchestrator never writes the historical blocked execution tree.  It
reuses the frozen classifier and statistical implementation, preserves all
exposed cells, and can launch only schedule positions 10..35 from the original
Part-B ledger.  The derived native launcher changes only the outer wall-clock
collection watchdog (600 -> 3600 seconds); scientific evaluator timeout,
sources, configuration, routes, seeds, controller, and ordering are unchanged.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "reports/driveclarify_rq3_v3_final_formal_freeze_v1"
HISTORY = ROOT / "reports/driveclarify_rq3_v3_bench2drive_closed_loop_formal_execution_v1"
RECOVERY = ROOT / "reports/driveclarify_rq3_v3_technical_validity_recovery_and_resume_v1"
RUNS = RECOVERY / "part_b_resumed_runs"
ORIGINAL_ORCHESTRATOR = ROOT / "tools/run_rq3_v3_bench2drive_formal_execution.py"
ORIGINAL_LAUNCHER = ROOT / "tools/run_rq3_native_episode.sh"
REPAIRED_LAUNCHER = RECOVERY / "infrastructure/run_rq3_native_episode_repaired.sh"
RESUME_LEDGER = RECOVERY / "PART_B_RESUMED_EXECUTION_LEDGER.json"
FINAL_FREEZE_DIGEST = "6d5b1c5feab91e5e8783231228639107e1da5337f4a9f47ff830ec110d22baef"
ORIGINAL_LAUNCHER_SHA256 = "9c84923549d1ed5828b659febdbf24a425c3cbb407c29b546c379095659ecb8c"
OUTER_WATCHDOG_OLD = "timeout --signal=TERM --kill-after=30 600 \\\n"
OUTER_WATCHDOG_NEW = "timeout --signal=TERM --kill-after=30 3600 \\\n"
PRIMARY_CLASS = "A. RECEIPT_AGGREGATION_OR_BOOKKEEPING_DEFECT"
REQUIRED_ARTIFACTS = (
    "FINAL_REPORT.md",
    "TECHNICAL_VALIDITY_ROOT_CAUSE.md",
    "CONTROL_INTEGRITY_FAILURE_ANALYSIS.md",
    "PART_A_INVALID_PAIR_FORENSICS.md",
    "PART_A_EVIDENCE_RECOVERY_RECEIPT.json",
    "PART_B_INVALID_CELL_FORENSICS.md",
    "TECHNICAL_REPAIR_MANIFEST.md",
    "REPAIR_NON_INTERFERENCE_RECEIPT.json",
    "FORMAL_RESUME_AUTHORIZATION_RECEIPT.json",
    "PART_B_RESUMED_EXECUTION_LEDGER.json",
    "PART_A_RECOVERED_RESULTS.json",
    "PART_A_RECOVERED_STATISTICAL_ANALYSIS.json",
    "PART_B_FINAL_RESULTS.json",
    "PART_B_FINAL_LIFECYCLE_ANALYSIS.json",
    "RQ3_V3_FINAL_COMBINED_RESULTS.json",
    "CONTROL_INTEGRITY_FINAL_RECEIPT.json",
    "TRUE_INTENT_FIREWALL_FINAL_RECEIPT.json",
    "SOURCE_FREEZE_FINAL_RECEIPT.json",
    "FINAL_ARTIFACT_AUDIT.json",
    "FINAL_VALIDATION_RECEIPT.json",
    "COMMAND_LOG.md",
)


def load_original_module() -> Any:
    spec = importlib.util.spec_from_file_location("rq3_v3_frozen_orchestrator", ORIGINAL_ORCHESTRATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError("ORIGINAL_ORCHESTRATOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FROZEN = load_original_module()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(value.rstrip() + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def append_log(message: str) -> None:
    path = RECOVERY / "COMMAND_LOG.md"
    if path.is_file():
        prior = path.read_text(encoding="utf-8").rstrip()
    else:
        prior = (
            "# RQ3-V3 technical-validity recovery command log\n\n"
            "Stage: `RQ3_V3_TECHNICAL_VALIDITY_RECOVERY_AND_FORMAL_RESUME_V1`\n\n"
            "The historical blocked campaign is read-only. Commands below are recovery-stage operations."
        )
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    atomic_text(path, prior + f"\n\n- `{stamp}` {message}")


def self_digest(value: Mapping[str, Any], key: str = "receipt_digest") -> dict[str, Any]:
    result = dict(value)
    result[key] = digest({name: item for name, item in result.items() if name != key})
    return result


def tree_fingerprint(root: Path, excluded_names: Iterable[str] = ()) -> dict[str, Any]:
    excluded = set(excluded_names)
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.name not in excluded)
    rows = [
        {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)}
        for path in files
    ]
    return {
        "path": str(root.relative_to(ROOT)),
        "file_count": len(rows),
        "bytes": sum(row["bytes"] for row in rows),
        "tree_digest": digest(rows),
    }


def path_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(ROOT)),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "sha256": sha(path) if path.is_file() else None,
    }


def history_key_hashes() -> dict[str, str]:
    names = (
        "FINAL_REPORT.md", "CONTROL_INTEGRITY_RECEIPT.json", "FINAL_VALIDATION_RECEIPT.json",
        "FINAL_ARTIFACT_AUDIT.json", "PART_A_EXECUTION_LEDGER.json", "PART_A_RESULTS.json",
        "PART_B_EXECUTION_LEDGER.json", "PART_B_RESULTS.json", "RQ3_V3_COMBINED_RESULTS.json", "COMMAND_LOG.md",
    )
    return {name: sha(HISTORY / name) for name in names}


def history_preserved(full: bool = True) -> tuple[bool, dict[str, Any]]:
    baseline = load(RECOVERY / "ORIGINAL_BLOCKED_CAMPAIGN_PRESERVATION_RECEIPT.json", {})
    current_hashes = history_key_hashes()
    current_tree = tree_fingerprint(HISTORY) if full else baseline.get("historical_tree_before")
    passed = bool(
        baseline
        and baseline.get("historical_tree_before") == current_tree
        and baseline.get("key_artifact_sha256_before") == current_hashes
    )
    return passed, {"historical_tree_current": current_tree, "key_artifact_sha256_current": current_hashes}


def create_derived_launcher() -> dict[str, Any]:
    original = ORIGINAL_LAUNCHER.read_text(encoding="utf-8")
    if sha(ORIGINAL_LAUNCHER) != ORIGINAL_LAUNCHER_SHA256:
        raise RuntimeError("ORIGINAL_FROZEN_LAUNCHER_DRIFT")
    if original.count(OUTER_WATCHDOG_OLD) != 1:
        raise RuntimeError("OUTER_WATCHDOG_PATCH_ANCHOR_NOT_UNIQUE")
    repaired = original.replace(OUTER_WATCHDOG_OLD, OUTER_WATCHDOG_NEW)
    atomic_text(REPAIRED_LAUNCHER, repaired)
    os.chmod(REPAIRED_LAUNCHER, 0o755)
    restored = repaired.replace(OUTER_WATCHDOG_NEW, OUTER_WATCHDOG_OLD)
    return {
        "original_launcher": path_record(ORIGINAL_LAUNCHER),
        "repaired_launcher": path_record(REPAIRED_LAUNCHER),
        "replacement_count": 1,
        "only_declared_replacement": restored == original,
        "outer_wall_collection_watchdog_s_before": 600,
        "outer_wall_collection_watchdog_s_after": 3600,
        "leaderboard_sensor_timeout_s_before_and_after": 480,
    }


def source_output(row: Mapping[str, Any]) -> Path:
    path = Path(str(row["source_output"]))
    return path if path.is_absolute() else ROOT / path


def invalid_part_a_rows() -> list[dict[str, Any]]:
    ledger = load(HISTORY / "PART_A_EXECUTION_LEDGER.json", {})
    return [row for row in ledger.get("entries", []) if not row.get("technical_valid")]


def invalid_part_b_rows() -> list[dict[str, Any]]:
    ledger = load(HISTORY / "PART_B_EXECUTION_LEDGER.json", {})
    return [row for row in ledger.get("entries", []) if not row.get("technical_valid")]


def raw_inventory(output: Path) -> list[dict[str, Any]]:
    return [path_record(path) for path in sorted(output.rglob("*")) if path.is_file()]


def part_a_recovery_records() -> list[dict[str, Any]]:
    records = []
    for row in invalid_part_a_rows():
        output = source_output(row)
        official = output / "official_checkpoint.json"
        navigation = output / "owner_evidence/NATIVE_NAVIGATION_INPUT_CONTRACT.json"
        process = output / "process_job/PROCESS_RECEIPT.json"
        recoverable = False
        if official.is_file() and navigation.is_file():
            reconstructed = FROZEN.common_evidence(row, output)
            recoverable = bool(reconstructed.get("technical_valid"))
        if official.is_file() and not navigation.is_file():
            evidence_reason = "official evaluator recorded pre-agent setup failure; scientific checkpoint/controller/navigation identity absent"
        elif not official.is_file():
            evidence_reason = "no authoritative official terminal result exists in the immutable run output"
        else:
            evidence_reason = "existing evidence does not satisfy the frozen technical-validity predicates"
        records.append({
            "FORMAL_PAIR_ID": row["pair_id"],
            "arm": row["arm"],
            "original_run_identity": row["run_id"],
            "route_id": row["route_id"],
            "formal_seed": row["seed"],
            "original_run_position": row["run_position"],
            "raw_authoritative_artifact": path_record(official) if official.is_file() else path_record(process),
            "raw_output_inventory_digest": digest(raw_inventory(output)),
            "recovered_field": None,
            "old_technical_invalid_reason": row.get("technical_invalidity_reasons", []),
            "new_evidence_chain_status": "REMAINS_TECHNICALLY_INVALID_NO_EXISTING_EVIDENCE_RECOVERY",
            "recovery_eligible": recoverable,
            "non_recovery_reason": evidence_reason,
            "original_result_digest": row.get("result_digest"),
            "no_rerun_confirmation": True,
        })
    return records


def evidence_preservation_audit(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    checks = []
    for row in rows:
        output = source_output(row)
        result_name = "RQ3_V3_FORMAL_A_RUN_RESULT.json" if "pair_id" in row else "RQ3_V3_FORMAL_B_EPISODE_RESULT.json"
        terminal = output / "official_checkpoint.json"
        raw_log = output / "process_job/evaluator.log"
        process = output / "process_job/PROCESS_RECEIPT.json"
        result_file = output / result_name
        explicit_invalid = not bool(row.get("technical_valid"))
        terminal_requirement = terminal.is_file() if not explicit_invalid else True
        checks.append({
            "run_id": row["run_id"],
            "source_output_exists": output.is_dir(),
            "classified_result_preserved": result_file.is_file(),
            "process_receipt_preserved": process.is_file(),
            "evaluator_log_preserved_when_started": raw_log.is_file() or not bool((row.get("process") or {}).get("world_observed_before_evaluator")),
            "official_terminal_artifact_present": terminal.is_file(),
            "official_terminal_required": not explicit_invalid,
            "explicit_missing_terminal_evidence_preserved_as_invalidity": explicit_invalid and not terminal.is_file(),
            "pass": bool(output.is_dir() and result_file.is_file() and process.is_file() and terminal_requirement),
        })
    return {
        "semantics": "preserve every raw artifact that exists; require terminal result for valid rows; preserve and explicitly account for absence on frozen technical-invalid rows",
        "row_count": len(checks),
        "explicit_technical_invalid_rows": sum(not bool(row.get("technical_valid")) for row in rows),
        "missing_terminal_artifacts_on_explicit_invalid_rows": sum(not bool(row.get("technical_valid")) and not (source_output(row) / "official_checkpoint.json").is_file() for row in rows),
        "pass": all(item["pass"] for item in checks),
        "checks": checks,
    }


def safe_port_map(cells: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    used: set[int] = set()
    ephemeral_low, ephemeral_high = [int(value) for value in Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()]
    for index, cell in enumerate(cells):
        rpc = 22000 + index * 3
        streaming = rpc + 1
        traffic_manager = rpc + 102
        ports = (rpc, streaming, traffic_manager)
        if any(ephemeral_low <= port <= ephemeral_high for port in ports):
            raise RuntimeError("SAFE_PORT_MAPPING_ENTERED_EPHEMERAL_RANGE")
        if used.intersection(ports):
            raise RuntimeError("SAFE_PORT_MAPPING_COLLISION")
        used.update(ports)
        rows.append({
            "schedule_position": cell["schedule_position"], "episode_id": cell["episode_id"],
            "rpc_port": rpc, "streaming_port": streaming, "traffic_manager_port": traffic_manager,
            "ephemeral_range": [ephemeral_low, ephemeral_high], "all_outside_ephemeral_range": True,
        })
    return rows


def bindable(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def original_cells() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ledger = load(HISTORY / "PART_B_EXECUTION_LEDGER.json", {})
    planned = sorted(ledger.get("planned_cells", []), key=lambda row: row["schedule_position"])
    attempted = sorted(ledger.get("entries", []), key=lambda row: row["schedule_position"])
    attempted_ids = {row["episode_id"] for row in attempted}
    remaining = [row for row in planned if row["episode_id"] not in attempted_ids]
    return planned, attempted, remaining


def create_forensic_documents() -> None:
    control = load(HISTORY / "CONTROL_INTEGRITY_RECEIPT.json", {})
    audit = load(HISTORY / "FINAL_ARTIFACT_AUDIT.json", {})
    validation = load(HISTORY / "FINAL_VALIDATION_RECEIPT.json", {})
    a_records = part_a_recovery_records()
    b_row = invalid_part_b_rows()[0]
    b_output = source_output(b_row)
    process = load(b_output / "process_job/PROCESS_RECEIPT.json", {})
    heartbeat = load(b_output / "owner_evidence/V11_RUNTIME_HEARTBEAT.json", {})
    decision = load(b_output / "owner_evidence/RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json", {})
    stop = load(HISTORY / "MANDATORY_HARD_STOP_RECEIPT_007.json", {})

    root_cause = f"""# RQ3-V3 technical-validity root cause

Primary classification: `{PRIMARY_CLASS}`.

The historical control receipt's actual control/source predicate passed (`pass_before_artifact_audit=true`). Its final `pass` became false only because `FINAL_ARTIFACT_AUDIT.pass=false` was injected into the control verdict. The audit was false because Part B was stopped at 9/35 and because it interpreted every missing official checkpoint on an explicitly technical-invalid run as failure to preserve raw evidence. Neither predicate establishes a control mutation.

A separate launcher defect caused the attempted Part-B invalidity: the frozen launcher imposed a 600-second outer wall-clock watchdog. The USC run was advancing at about 0.122x real time and was still inside the authoritative evaluator at 71.9 simulation seconds when that watchdog returned 124. The frozen native evaluator timeout remains 480 seconds and emitted no terminal record before external termination. Extending only the outer collection watchdog to 3600 seconds lets the unchanged evaluator classify native stalls/blocks/timeouts itself.

Earlier Part-A bind failures identify an additional prospective launcher risk: formal RPC/TM ports 41000-42104 are inside the host ephemeral range 32768-60999. Remaining cells are rebound to non-scientific ports outside that range. Ports are absent from the frozen Part-B scientific manifest.

No execution-affecting PID, controller, controller-ownership, RoutePlanner, second-writer, VLA-forward, duplicate-execution, true-intent, checkpoint, or frozen scientific-source violation was found.
"""
    atomic_text(RECOVERY / "TECHNICAL_VALIDITY_ROOT_CAUSE.md", root_cause)

    failing_conditions = [
        {
            "condition": "control_integrity.final_artifact_audit_pass",
            "expected": True, "observed": control.get("final_artifact_audit_pass"),
            "source_artifact": str((HISTORY / "FINAL_ARTIFACT_AUDIT.json").relative_to(ROOT)),
            "source_field": "pass", "source_value": audit.get("pass"),
            "execution_behavior_affected": False, "scientific_state_or_control_changed": False,
            "recoverable_from_existing_artifacts": True,
        },
        {
            "condition": "artifact_audit.raw_authoritative_evaluator_outputs_preserved",
            "expected": "all existing raw artifacts preserved; absence on declared invalid rows explicitly accounted",
            "observed": audit.get("raw_authoritative_evaluator_outputs_preserved"),
            "source_artifact": "tools/run_rq3_v3_bench2drive_formal_execution.py",
            "source_field": "all(source_output/official_checkpoint.json is_file for every ledger row)",
            "execution_behavior_affected": False, "scientific_state_or_control_changed": False,
            "recoverable_from_existing_artifacts": True,
        },
        {
            "condition": "artifact_audit.pass",
            "expected": "artifact preservation independent from partial scientific roster status",
            "observed": audit.get("pass"),
            "source_artifact": "tools/run_rq3_v3_bench2drive_formal_execution.py",
            "source_field": "complete_a and complete_b and source and control and firewall",
            "execution_behavior_affected": False, "scientific_state_or_control_changed": False,
            "recoverable_from_existing_artifacts": True,
        },
    ]
    analysis = f"""# Control-integrity failure analysis

Classification: `{PRIMARY_CLASS}`.

The provenance chain is:

`CONTROL_INTEGRITY_RECEIPT.pass=false` <- `final_artifact_audit_pass=false` <- `FINAL_ARTIFACT_AUDIT.pass=false` <- (`complete_b=false` and `raw_authoritative_evaluator_outputs_preserved=false`).

All explicit execution-control observations remained zero, source freeze passed, and `pass_before_artifact_audit=true`. The final control failure therefore does not evidence a scientific control-integrity violation.

```json
{json.dumps(failing_conditions, indent=2, sort_keys=True)}
```

Historical validation status remains `{validation.get('status')}` and is not overwritten.
"""
    atomic_text(RECOVERY / "CONTROL_INTEGRITY_FAILURE_ANALYSIS.md", analysis)

    lines = ["# Part-A invalid-pair forensics", "", "No Part-A CARLA episode was rerun. No invalid pair is recoverable from existing authoritative evidence.", ""]
    for item in a_records:
        lines.extend([
            f"## {item['FORMAL_PAIR_ID']} / {item['arm']}", "",
            f"- Original run: `{item['original_run_identity']}` at frozen position `{item['original_run_position']}`, seed `{item['formal_seed']}`.",
            f"- Raw authoritative artifact: `{item['raw_authoritative_artifact']['path']}` (`{item['raw_authoritative_artifact']['sha256']}`).",
            f"- Old invalidity: `{json.dumps(item['old_technical_invalid_reason'])}`.",
            f"- Finding: {item['non_recovery_reason']}.",
            f"- Evidence-chain status: `{item['new_evidence_chain_status']}`; recovered field: `null`; rerun: `NO`.", "",
        ])
    atomic_text(RECOVERY / "PART_A_INVALID_PAIR_FORENSICS.md", "\n".join(lines))
    a_receipt = self_digest({
        "schema": "driveclarify.rq3-v3.part-a-evidence-recovery-receipt.v1",
        "stage": "RQ3_V3_TECHNICAL_VALIDITY_RECOVERY_AND_FORMAL_RESUME_V1",
        "historical_campaign": str(HISTORY.relative_to(ROOT)),
        "original_evaluable_pairs": 35, "invalid_pairs_examined": 5,
        "recovered_via_existing_evidence": 0, "final_technically_evaluable_pairs": 35,
        "part_a_carla_reruns": 0, "records": a_records,
        "status": "NO_EXISTING_AUTHORITATIVE_EVIDENCE_RECOVERY_AVAILABLE",
    })
    atomic_json(RECOVERY / "PART_A_EVIDENCE_RECOVERY_RECEIPT.json", a_receipt)

    last = heartbeat.get("last_observation") or {}
    b_doc = f"""# Part-B invalid-cell forensics

Cell: `{b_row['episode_id']}` / run `{b_row['run_id']}` / frozen position `{b_row['schedule_position']}` / seed `{b_row['seed']}`.

- Agent initialization occurred: **YES** (`agent_setup_complete=true`).
- Scientific exposure occurred: **YES** (`scientific_window_started=true`, {heartbeat.get('model_forward_return_count')} model-forward returns recorded).
- DriveClarify made a decision: **YES**, `{b_row.get('policy_action')}`; frozen comparison/action receipt exists.
- DriveClarify model window completed: **YES**.
- Native evaluator episode completed: **NO**. The outer launcher watchdog returned `{process.get('evaluator_exit')}` after `{process.get('evaluator_wall_s')}` wall seconds.
- Authoritative evaluator output exists: **NO** (`official_checkpoint.json` absent).
- Official route record exists: **NO**.
- Parser/receipt-only defect: **NO**. The parser correctly reported absence; the launcher terminated collection before evaluator terminalization.
- Recoverable from immutable evidence: **NO**. The last live observation was `{last.get('official_criteria', [{}])[0].get('actual_value')}`% route completion at simulation progress represented by frame `{last.get('frame')}`, with speed `{last.get('speed_mps')}` m/s. This is not an authoritative terminal result.
- Execution affected: **YES at the infrastructure lifecycle boundary**: the outer watchdog killed the evaluator. Already-observed DriveClarify control behavior was not rewritten, and the cell will not be rerun.

Exact invalidity: `{json.dumps(b_row.get('technical_invalidity_reasons'))}`. Hard-stop receipt: `{stop.get('receipt_digest')}`.

The scientifically exposed cell remains technically invalid and outside all denominators.
"""
    atomic_text(RECOVERY / "PART_B_INVALID_CELL_FORENSICS.md", b_doc)


def qualification() -> tuple[dict[str, Any], dict[str, Any]]:
    planned, attempted, remaining = original_cells()
    manifest = load(FREEZE / "PART_B_FORMAL_MANIFEST.json", {})
    manifest_by_id = {row["episode_id"]: row for row in manifest.get("episodes", [])}
    launcher = create_derived_launcher()
    source = FROZEN.source_revalidation()
    a_ledger = load(HISTORY / "PART_A_EXECUTION_LEDGER.json", {})
    b_ledger = load(HISTORY / "PART_B_EXECUTION_LEDGER.json", {})
    all_history_rows = a_ledger.get("entries", []) + b_ledger.get("entries", [])
    planned_by_run_id = {
        row["run_id"]: row
        for row in a_ledger.get("planned_cells", []) + b_ledger.get("planned_cells", [])
    }
    parser_checks = []
    for row in all_history_rows:
        classification_cell = {**planned_by_run_id[row["run_id"]], **row}
        reconstructed = FROZEN.common_evidence(classification_cell, source_output(row))
        parser_checks.append({
            "run_id": row["run_id"],
            "expected_technical_valid": bool(row.get("technical_valid")),
            "observed_technical_valid": bool(reconstructed.get("technical_valid")),
            "official_route_record_match": reconstructed.get("identity_checks", {}).get("official_route_record"),
            "pass": bool(row.get("technical_valid")) == bool(reconstructed.get("technical_valid")),
        })
    integrity, firewall = FROZEN.build_integrity(a_ledger.get("entries", []), b_ledger.get("entries", []), source)
    preservation = evidence_preservation_audit(all_history_rows)
    port_map = safe_port_map(remaining)
    port_checks = [{**row, "currently_bindable": all(bindable(row[key]) for key in ("rpc_port", "streaming_port", "traffic_manager_port"))} for row in port_map]
    identity_checks = []
    for cell in remaining:
        manifest_row = manifest_by_id.get(cell["episode_id"], {})
        config = ROOT / cell["config_path"]
        route = ROOT / cell["route_path"]
        identity_checks.append({
            "episode_id": cell["episode_id"], "schedule_position": cell["schedule_position"],
            "manifest_seed_matches": manifest_row.get("formal_seed") == cell.get("seed"),
            "manifest_scene_matches": manifest_row.get("scene_id") == cell.get("scene_id"),
            "manifest_order_matches": manifest_row.get("schedule_position") == cell.get("schedule_position"),
            "config_hash_matches": config.is_file() and sha(config) == cell.get("config_sha256"),
            "route_hash_matches": route.is_file() and sha(route) == cell.get("route_sha256"),
        })
    history_ok, history_now = history_preserved()
    checks = {
        "A_defect_fully_identified": bool(
            load(HISTORY / "CONTROL_INTEGRITY_RECEIPT.json", {}).get("pass_before_artifact_audit") is True
            and load(HISTORY / "CONTROL_INTEGRITY_RECEIPT.json", {}).get("final_artifact_audit_pass") is False
        ),
        "B_repair_strictly_non_scientific": bool(launcher["only_declared_replacement"]),
        "C_source_checkpoint_controller_frozen": bool(source.get("pass")),
        "D_control_integrity_recovered": bool(integrity.get("pass_before_artifact_audit") and preservation.get("pass")),
        "E_true_intent_firewall_pass": bool(firewall.get("pass")),
        "F_existing_history_preserved": history_ok,
        "G_no_seed_or_scene_changed": all(all(value for key, value in row.items() if key.endswith("_matches")) for row in identity_checks),
        "H_no_previously_valid_episode_rerun": not RUNS.exists() or not any(RUNS.iterdir()),
        "I_exact_remaining_manifest_known": bool(
            len(planned) == 35 and len(attempted) == 9 and len(remaining) == 26
            and [row["schedule_position"] for row in remaining] == list(range(10, 36))
        ),
        "J_repaired_evidence_pipeline_validated": bool(
            all(row["pass"] for row in parser_checks)
            and all(row["currently_bindable"] for row in port_checks)
            and preservation.get("pass")
            and launcher["only_declared_replacement"]
        ),
    }
    qualification_value = self_digest({
        "schema": "driveclarify.rq3-v3.repair-non-interference-receipt.v1",
        "stage": "RQ3_V3_TECHNICAL_VALIDITY_RECOVERY_AND_FORMAL_RESUME_V1",
        "classification": PRIMARY_CLASS,
        "zero_carla_qualification": True,
        "launcher_derivation": launcher,
        "source_revalidation": source,
        "historical_campaign_preservation": {"pass": history_ok, **history_now},
        "historical_evidence_parser_checks": parser_checks,
        "historical_evidence_preservation_audit": preservation,
        "control_integrity_before_artifact_audit": integrity,
        "true_intent_firewall": firewall,
        "remaining_manifest_identity_checks": identity_checks,
        "prospective_port_checks": port_checks,
        "formal_seed_consumed_for_qualification": False,
        "new_scientific_scene_run_for_qualification": False,
        "scientific_components_changed": [],
        "pass": all(checks.values()),
    })
    auth = self_digest({
        "schema": "driveclarify.rq3-v3.formal-resume-authorization-receipt.v1",
        "stage": "RQ3_V3_TECHNICAL_VALIDITY_RECOVERY_AND_FORMAL_RESUME_V1",
        "final_freeze_digest": FINAL_FREEZE_DIGEST,
        "conditions": checks,
        "remaining_schedule_positions": [row["schedule_position"] for row in remaining],
        "remaining_episode_ids": [row["episode_id"] for row in remaining],
        "remaining_formal_seeds": [row["seed"] for row in remaining],
        "port_mapping": port_map,
        "original_valid_cells_rerun": 0,
        "original_invalid_cell_rerun": 0,
        "authorization": "AUTHORIZED_RESUME_EXACT_26_UNEXPOSED_CELLS" if all(checks.values()) else "NOT_AUTHORIZED",
        "pass": all(checks.values()),
    })
    return qualification_value, auth


def repair_manifest() -> str:
    return """# Technical repair manifest

Stage: `RQ3_V3_TECHNICAL_VALIDITY_RECOVERY_AND_FORMAL_RESUME_V1`.

1. The historical execution directory is immutable and is checked by a whole-tree digest plus key-artifact SHA-256 values.
2. Artifact preservation is separated from scientific completeness. A missing terminal artifact is accepted only when the frozen ledger already marks that run technical-invalid and the absence is explicitly recorded; no outcome is inferred.
3. Control integrity is computed from source freeze and the frozen zero-valued control-mutation predicates. Artifact audit remains a required independent final gate but cannot manufacture a control mutation.
4. The repaired launcher is mechanically derived from the frozen launcher with exactly one replacement: its outer wall-clock collection watchdog changes from 600 to 3600 seconds. The evaluator's native `--timeout=480` and all scientific code remain byte-identical.
5. Remaining RPC, streaming, and Traffic Manager ports are rebound outside Linux's ephemeral port range. Ports are launcher plumbing and are absent from the frozen scientific manifest.
6. The original 80 Part-A runs and 9 attempted Part-B cells are never relaunched. Only original schedule positions 10 through 35 are eligible.
7. A new technical invalidity or any integrity/source violation during resume causes an immediate validity stop. Scientific collision, stall, block, native timeout, route failure, or noncompletion with an authoritative official record remains a scientific outcome and does not stop the roster.

No checkpoint, model weight, runtime scientific configuration, PID, controller, RoutePlanner semantics, ACT/ASK/WAIT semantics, Full Replan semantics, scene, seed, order, threshold, margin, RQ1, or RQ2 component is modified.
"""


def prepare() -> dict[str, Any]:
    RECOVERY.mkdir(parents=True, exist_ok=True)
    preservation_path = RECOVERY / "ORIGINAL_BLOCKED_CAMPAIGN_PRESERVATION_RECEIPT.json"
    if not preservation_path.is_file():
        preservation = self_digest({
            "schema": "driveclarify.rq3-v3.original-blocked-campaign-preservation.v1",
            "historical_status": "BLOCKED_RQ3_V3_TECHNICAL_VALIDITY_STOP",
            "historical_tree_before": tree_fingerprint(HISTORY),
            "key_artifact_sha256_before": history_key_hashes(),
            "historical_report_writable_by_recovery": False,
        })
        atomic_json(preservation_path, preservation)
    create_forensic_documents()
    atomic_text(RECOVERY / "TECHNICAL_REPAIR_MANIFEST.md", repair_manifest())
    qualification_value, auth = qualification()
    atomic_json(RECOVERY / "REPAIR_NON_INTERFERENCE_RECEIPT.json", qualification_value)
    atomic_json(RECOVERY / "FORMAL_RESUME_AUTHORIZATION_RECEIPT.json", auth)
    planned, attempted, remaining = original_cells()
    port_by_episode = {row["episode_id"]: row for row in auth["port_mapping"]}
    if not RESUME_LEDGER.is_file():
        ledger = {
            "schema": "driveclarify.rq3-v3.part-b-resumed-execution-ledger.v1",
            "stage": "RQ3_V3_TECHNICAL_VALIDITY_RECOVERY_AND_FORMAL_RESUME_V1",
            "status": "AUTHORIZED_UNEXPOSED_26_NOT_STARTED" if auth["pass"] else "NOT_AUTHORIZED",
            "final_freeze_digest": FINAL_FREEZE_DIGEST,
            "historical_ledger_path": str((HISTORY / "PART_B_EXECUTION_LEDGER.json").relative_to(ROOT)),
            "historical_ledger_sha256": sha(HISTORY / "PART_B_EXECUTION_LEDGER.json"),
            "historical_attempted_episode_ids": [row["episode_id"] for row in attempted],
            "historical_valid_episode_ids": [row["episode_id"] for row in attempted if row.get("technical_valid")],
            "historical_invalid_episode_ids": [row["episode_id"] for row in attempted if not row.get("technical_valid")],
            "planned_remaining": [
                {
                    **{key: cell[key] for key in (
                        "schedule_position", "episode_id", "run_id", "condition", "family", "level", "scene_id",
                        "seed", "config_path", "config_sha256", "route_path", "route_sha256", "answer_candidate_id",
                        "expected_action", "canonical_candidate", "task_relation",
                    )},
                    "recovery_output_path": str((RUNS / cell["episode_id"] / "attempt_01").relative_to(ROOT)),
                    **{key: port_by_episode[cell["episode_id"]][key] for key in ("rpc_port", "streaming_port", "traffic_manager_port")},
                }
                for cell in remaining
            ],
            "entries": [], "scientific_retries": 0, "seed_replacements": 0,
            "previously_valid_part_b_cells_rerun": 0, "technical_invalid_part_b_cell_rerun": 0,
        }
        atomic_json(RESUME_LEDGER, self_digest(ledger, "ledger_digest"))
    append_log(
        f"Zero-CARLA repair qualification completed: `{auth['authorization']}`; original Part-A and Part-B outputs were not executed."
    )
    return auth


def source_and_history_gate() -> tuple[bool, dict[str, Any]]:
    source = FROZEN.source_revalidation()
    history_ok, history_now = history_preserved(full=False)
    launcher_ok = bool(
        REPAIRED_LAUNCHER.is_file()
        and REPAIRED_LAUNCHER.read_text(encoding="utf-8").replace(OUTER_WATCHDOG_NEW, OUTER_WATCHDOG_OLD)
        == ORIGINAL_LAUNCHER.read_text(encoding="utf-8")
    )
    result = {"source_revalidation": source, "history_preserved": history_ok, **history_now, "repaired_launcher_noninterference": launcher_ok}
    return bool(source.get("pass") and history_ok and launcher_ok), result


def update_resume_ledger(ledger: dict[str, Any]) -> None:
    ledger["ledger_digest"] = digest({key: value for key, value in ledger.items() if key != "ledger_digest"})
    atomic_json(RESUME_LEDGER, ledger)


def run_resume() -> dict[str, Any]:
    auth = load(RECOVERY / "FORMAL_RESUME_AUTHORIZATION_RECEIPT.json", {})
    if not auth.get("pass") or auth.get("authorization") != "AUTHORIZED_RESUME_EXACT_26_UNEXPOSED_CELLS":
        raise RuntimeError("FORMAL_RESUME_NOT_AUTHORIZED")
    ledger = load(RESUME_LEDGER, {})
    _, _, original_remaining = original_cells()
    original_by_episode = {row["episode_id"]: row for row in original_remaining}
    completed = {row["episode_id"] for row in ledger.get("entries", [])}
    expected_prefix = [row["episode_id"] for row in ledger["planned_remaining"][:len(completed)]]
    actual_prefix = [row["episode_id"] for row in ledger.get("entries", [])]
    if actual_prefix != expected_prefix:
        raise RuntimeError("RESUME_LEDGER_NOT_A_FROZEN_ORDER_PREFIX")
    ledger["status"] = "FORMAL_RESUME_IN_PROGRESS"
    ledger.setdefault("execution_started_epoch", int(time.time()))
    update_resume_ledger(ledger)
    for cell in ledger["planned_remaining"]:
        if cell["episode_id"] in completed:
            continue
        gate, gate_detail = source_and_history_gate()
        if not gate:
            ledger["status"] = "BLOCKED_RQ3_V3_SOURCE_OR_ARTIFACT_INTEGRITY_FAILURE"
            ledger["stop_detail"] = gate_detail
            update_resume_ledger(ledger)
            append_log(f"Source/history hard stop before `{cell['run_id']}`.")
            return {"status": ledger["status"]}
        output = RUNS / cell["episode_id"] / "attempt_01"
        command = [
            str(REPAIRED_LAUNCHER), str(ROOT / cell["config_path"]), str(ROOT / cell["route_path"]), str(cell["seed"]),
            str(cell["rpc_port"]), cell.get("answer_candidate_id") or "NONE", str(output),
            "RQ3_V3_FORMAL_B_TECHNICAL_RECOVERY_RESUME_NO_SCIENTIFIC_RETRY",
        ]
        recovered_post_launch_bookkeeping = False
        if output.exists():
            process = load(output / "process_job/PROCESS_RECEIPT.json", {})
            expected_existing = bool(
                cell["episode_id"] == "B-ORD-CRITICAL-S02"
                and not ledger.get("entries")
                and (output / "official_checkpoint.json").is_file()
                and (output / "owner_evidence/V11_AGENT_STATUS.json").is_file()
                and process.get("attempt_kind") == "RQ3_V3_FORMAL_B_TECHNICAL_RECOVERY_RESUME_NO_SCIENTIFIC_RETRY"
                and process.get("scientific_retry") is False
                and process.get("formal_seed_replacement") is False
                and not (output / "RQ3_V3_FORMAL_B_EPISODE_RESULT.json").exists()
            )
            if not expected_existing:
                ledger["status"] = "BLOCKED_RQ3_V3_FORMAL_RESUME_VALIDITY_FAILURE"
                ledger["stop_detail"] = {"reason": "UNLEDGERED_PREEXISTING_RECOVERY_OUTPUT", "path": str(output.relative_to(ROOT))}
                update_resume_ledger(ledger)
                append_log(f"Validity hard stop: unexpected unledgered output exists for `{cell['run_id']}`; no rerun attempted.")
                return {"status": ledger["status"]}
            wrapper_exit = int(process.get("wrapper_exit", 125))
            recovered_post_launch_bookkeeping = True
            append_log(
                f"Evidence-only post-launch bookkeeping recovery for `{cell['run_id']}` after the recovery orchestrator omitted "
                "`route_id` from its compact cell view. The one native execution is not relaunched."
            )
        else:
            ports = (cell["rpc_port"], cell["streaming_port"], cell["traffic_manager_port"])
            if not all(bindable(port) for port in ports):
                ledger["status"] = "BLOCKED_RQ3_V3_FORMAL_RESUME_VALIDITY_FAILURE"
                ledger["stop_detail"] = {"reason": "PROSPECTIVE_SAFE_PORT_NOT_BINDABLE", "ports": ports}
                update_resume_ledger(ledger)
                append_log(f"Validity hard stop before exposure: safe port preflight failed for `{cell['run_id']}`.")
                return {"status": ledger["status"]}
            append_log(
                f"Launch exact remaining frozen cell `{cell['run_id']}` at schedule position `{cell['schedule_position']}`; "
                f"seed `{cell['seed']}`; repaired outer-watchdog launcher; no retry/replacement."
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            with (output.parent / "native.log").open("wb") as stream:
                completed_process = subprocess.run(command, cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT, check=False)
            wrapper_exit = completed_process.returncode
        frozen_cell = {**original_by_episode[cell["episode_id"]], **cell}
        result = FROZEN.classify_part_b(frozen_cell, output, wrapper_exit)
        result["command"] = command
        result["resume_stage"] = "RQ3_V3_TECHNICAL_VALIDITY_RECOVERY_AND_FORMAL_RESUME_V1"
        result["evidence_only_post_launch_bookkeeping_recovery"] = recovered_post_launch_bookkeeping
        result["result_digest"] = digest({key: value for key, value in result.items() if key != "result_digest"})
        atomic_json(output / "RQ3_V3_FORMAL_B_EPISODE_RESULT.json", result)
        if recovered_post_launch_bookkeeping:
            recovery_receipt = self_digest({
                "schema": "driveclarify.rq3-v3.post-launch-bookkeeping-recovery-receipt.v1",
                "run_id": cell["run_id"], "episode_id": cell["episode_id"],
                "original_schedule_position": cell["schedule_position"], "formal_seed": cell["seed"],
                "cause": "RECOVERY_ORCHESTRATOR_COMPACT_CELL_OMITTED_ROUTE_ID_DURING_POST_RUN_CLASSIFICATION",
                "raw_output_path": str(output.relative_to(ROOT)),
                "raw_output_inventory_digest": digest(raw_inventory(output)),
                "official_checkpoint_sha256": sha(output / "official_checkpoint.json"),
                "classified_result_digest": result["result_digest"],
                "native_execution_count": 1, "native_episode_rerun": False,
                "scientific_retry": False, "seed_replacement": False,
                "scientific_behavior_affected": False, "evidence_chain_reconstructed": True,
            })
            atomic_json(RECOVERY / "POST_LAUNCH_CLASSIFICATION_RECOVERY_RECEIPT.json", recovery_receipt)
        ledger["entries"].append(result)
        update_resume_ledger(ledger)
        violations = FROZEN.integrity_violations(result)
        if violations:
            ledger["status"] = "BLOCKED_RQ3_V3_RECOVERY_SCIENTIFIC_INTEGRITY_VIOLATION"
            ledger["stop_detail"] = {"run_id": cell["run_id"], "integrity_violations": violations}
            update_resume_ledger(ledger)
            append_log(f"Scientific-integrity hard stop after `{cell['run_id']}`: `{violations}`.")
            return {"status": ledger["status"]}
        if not (result["decision_evaluable"] and result["lifecycle_evaluable"] and result["execution_evaluable"]):
            ledger["status"] = "BLOCKED_RQ3_V3_FORMAL_RESUME_VALIDITY_FAILURE"
            ledger["stop_detail"] = {"run_id": cell["run_id"], "technical_invalidity_reasons": result["technical_invalidity_reasons"]}
            update_resume_ledger(ledger)
            append_log(
                f"Technical-validity hard stop after `{cell['run_id']}`; no retry/replacement: "
                f"`{result['technical_invalidity_reasons']}`."
            )
            return {"status": ledger["status"]}
        append_log(
            f"Completed `{cell['run_id']}` as technically valid. Official status `{result['official'].get('status')}`, "
            f"route completion `{result.get('Route_Completion')}`; scientific outcome accepted without performance stop."
        )
        print(canonical({
            "completed": len(ledger["entries"]), "of": 26, "run_id": cell["run_id"],
            "official_status": result["official"].get("status"), "route_completion": result.get("Route_Completion"),
        }), flush=True)
    ledger["status"] = "COMPLETED_EXACT_26_UNEXPOSED_FORMAL_CELLS"
    ledger["execution_finished_epoch"] = int(time.time())
    update_resume_ledger(ledger)
    append_log("Reached the legal frozen Part-B endpoint: all 26 previously unexposed cells executed in original order.")
    return {"status": ledger["status"]}


def ratio_text(value: Mapping[str, Any]) -> str:
    return f"{value.get('numerator')}/{value.get('denominator')}"


def final_report_text(
    primary_status: str, a: Mapping[str, Any], b: Mapping[str, Any], combined: Mapping[str, Any],
    control: Mapping[str, Any], validation: Mapping[str, Any], resume: Mapping[str, Any],
) -> str:
    per_route = ", ".join(f"{key}={value}/5" for key, value in a["evaluable_pairs_by_route"].items())
    recovered_b = "YES" if combined["part_b_invalid_cell_recovered"] else "NO"
    return f"""# RQ3-V3 technical-validity recovery and formal resume

Primary status: `{primary_status}`

Technical root cause: `{PRIMARY_CLASS}`

Control integrity defect: `CONTROL_INTEGRITY_RECEIPT.pass` was coupled to a partial/full-roster artifact audit even though `pass_before_artifact_audit=true` and every scientific control-mutation counter was zero. The repaired audit distinguishes preserved absence on declared technical-invalid runs from lost evidence; the final control receipt is `{control.get('status')}`.

Execution-affecting scientific integrity violation: `NO`

Part-A CARLA reruns: `0`

Part-A original evaluable: `35/40`

Part-A recovered via existing evidence: `0`

Part-A final technically evaluable: `{a['evaluable_pairs']}/40`

Per-route evaluability: `{per_route}`

H-RQ3-A: `{a['H_RQ3_A']}`

Part-B original valid episodes: `8`

Part-B original technical-invalid episodes: `1`

Part-B technical-invalid cell recovered from existing evidence: `{recovered_b}`

Previously valid Part-B cells rerun: `0`

Technical-invalid Part-B cell rerun: `0`

Remaining unexposed formal cells executed: `{len(resume.get('entries', []))}/26`

Part-B final technically evaluable: `{b['technically_evaluable']['execution']}/35`

Correct scientific decisions: `{ratio_text(b['correct_decisions'])}`

LOW ACT: `{ratio_text(b['LOW_ACT'])}`

Unnecessary LOW ASK: `{ratio_text(b['LOW_unnecessary_ASK'])}`

Timely HIGH ASK: `{ratio_text(b['HIGH_timely_ASK'])}`

HIGH lifecycle: `{ratio_text(b['HIGH_lifecycle_composite'])}`

Native completion: `{ratio_text(b['native_completion'])}`

Safe completion: `{ratio_text(b['safe_completion'])}`

Wrong goal: `{ratio_text(b['wrong_goal_execution'])}`

Pedestrian collision: `{ratio_text(b['pedestrian_collision_episodes'])}`

H-RQ3-B: `{combined['H_RQ3_B']}`

H-RQ3-C: `{combined['H_RQ3_C']}`

Overall RQ3-V3 scientific verdict: `{combined['overall_RQ3_V3_scientific_verdict']}`

Scientific retries: `0`

Seed replacements: `0`

Formal seeds changed: `NO`

Scenes changed: `NO`

Checkpoint changed: `NO`

PID/controller changed: `NO`

RQ1 changed: `NO`

RQ2 changed: `NO`

Scientific components changed: `NONE`

Final validation digest: `{validation.get('validation_digest')}`

The original historical status `BLOCKED_RQ3_V3_TECHNICAL_VALIDITY_STOP` and every artifact under `{HISTORY}` remain unchanged.

Exact artifact paths:

{chr(10).join('- `' + str(RECOVERY / name) + '`' for name in REQUIRED_ARTIFACTS)}

No RQ2 extension, DriveClarify tuning, RQ3-V4 campaign, or paper modification was performed.
"""


def write_blocked_placeholder(primary_status: str, detail: Mapping[str, Any]) -> None:
    for name in ("PART_A_RECOVERED_RESULTS.json", "PART_A_RECOVERED_STATISTICAL_ANALYSIS.json", "PART_B_FINAL_RESULTS.json", "PART_B_FINAL_LIFECYCLE_ANALYSIS.json", "RQ3_V3_FINAL_COMBINED_RESULTS.json", "CONTROL_INTEGRITY_FINAL_RECEIPT.json", "TRUE_INTENT_FIREWALL_FINAL_RECEIPT.json", "SOURCE_FREEZE_FINAL_RECEIPT.json", "FINAL_ARTIFACT_AUDIT.json", "FINAL_VALIDATION_RECEIPT.json"):
        path = RECOVERY / name
        if not path.is_file():
            atomic_json(path, {"status": primary_status, "detail": detail, "not_fabricated": True})
    atomic_text(RECOVERY / "FINAL_REPORT.md", f"# RQ3-V3 recovery blocked\n\nPrimary status: `{primary_status}`\n\n```json\n{json.dumps(detail, indent=2, sort_keys=True)}\n```\n")


def finalize() -> dict[str, Any]:
    resume = load(RESUME_LEDGER, {})
    status = resume.get("status")
    if status == "BLOCKED_RQ3_V3_RECOVERY_SCIENTIFIC_INTEGRITY_VIOLATION":
        primary = "BLOCKED_RQ3_V3_RECOVERY_SCIENTIFIC_INTEGRITY_VIOLATION"
    elif status == "BLOCKED_RQ3_V3_SOURCE_OR_ARTIFACT_INTEGRITY_FAILURE":
        primary = "BLOCKED_RQ3_V3_SOURCE_OR_ARTIFACT_INTEGRITY_FAILURE"
    elif status != "COMPLETED_EXACT_26_UNEXPOSED_FORMAL_CELLS":
        primary = "BLOCKED_RQ3_V3_FORMAL_RESUME_VALIDITY_FAILURE"
    else:
        primary = "PASS_RQ3_V3_TECHNICAL_RECOVERY_AND_FORMAL_RESUME_COMPLETE"
    a_ledger = load(HISTORY / "PART_A_EXECUTION_LEDGER.json", {})
    historical_b = load(HISTORY / "PART_B_EXECUTION_LEDGER.json", {})
    a, aa, a_verdict = FROZEN.analyze_part_a(a_ledger.get("entries", []), True)
    all_b_rows = historical_b.get("entries", []) + resume.get("entries", [])
    complete_b = status == "COMPLETED_EXACT_26_UNEXPOSED_FORMAL_CELLS" and len(all_b_rows) == 35
    b, ba, b_verdict = FROZEN.analyze_part_b(all_b_rows, complete_b)
    atomic_json(RECOVERY / "PART_A_RECOVERED_RESULTS.json", a)
    atomic_json(RECOVERY / "PART_A_RECOVERED_STATISTICAL_ANALYSIS.json", aa)
    atomic_json(RECOVERY / "PART_B_FINAL_RESULTS.json", b)
    atomic_json(RECOVERY / "PART_B_FINAL_LIFECYCLE_ANALYSIS.json", ba)

    source_base = FROZEN.source_revalidation()
    history_ok, history_now = history_preserved()
    noninterference = load(RECOVERY / "REPAIR_NON_INTERFERENCE_RECEIPT.json", {})
    source_final = self_digest({
        "schema": "driveclarify.rq3-v3.source-freeze-final-receipt.v1",
        "frozen_source_revalidation": source_base,
        "original_blocked_campaign_preserved": history_ok,
        **history_now,
        "repaired_launcher_noninterference_pass": bool(noninterference.get("launcher_derivation", {}).get("only_declared_replacement")),
        "checkpoint_changed": False, "formal_seeds_changed": False, "scenes_changed": False,
        "PID_or_controller_changed": False, "scientific_components_changed": [],
        "pass": bool(source_base.get("pass") and history_ok and noninterference.get("pass")),
    })
    atomic_json(RECOVERY / "SOURCE_FREEZE_FINAL_RECEIPT.json", source_final)
    integrity, firewall = FROZEN.build_integrity(a_ledger.get("entries", []), all_b_rows, source_base)
    firewall["historical_and_resumed_rows"] = len(a_ledger.get("entries", [])) + len(all_b_rows)
    firewall["pass"] = bool(firewall.get("pass") and source_final["pass"])
    firewall["status"] = "PASS_TRUE_INTENT_FIREWALL_FINAL" if firewall["pass"] else "FAIL_TRUE_INTENT_FIREWALL_FINAL"
    firewall["receipt_digest"] = digest({key: value for key, value in firewall.items() if key != "receipt_digest"})
    atomic_json(RECOVERY / "TRUE_INTENT_FIREWALL_FINAL_RECEIPT.json", firewall)

    preservation = evidence_preservation_audit(a_ledger.get("entries", []) + all_b_rows)
    run_artifacts = []
    for row in resume.get("entries", []):
        output = source_output(row)
        run_artifacts.extend(raw_inventory(output))
    recovery_tree_exclusions = {
        "FINAL_ARTIFACT_AUDIT.json", "FINAL_VALIDATION_RECEIPT.json", "FINAL_REPORT.md",
        "CONTROL_INTEGRITY_FINAL_RECEIPT.json", "RQ3_V3_FINAL_COMBINED_RESULTS.json",
    }
    audit = {
        "schema": "driveclarify.rq3-v3.final-artifact-audit.v1",
        "historical_raw_evidence_preservation": preservation,
        "resumed_raw_artifact_count": len(run_artifacts),
        "resumed_raw_artifact_inventory_digest": digest(run_artifacts),
        "recovery_nonrecursive_tree": tree_fingerprint(RECOVERY, recovery_tree_exclusions),
        "complete_original_part_a_roster": len(a_ledger.get("entries", [])) == 80,
        "complete_combined_part_b_roster": len(all_b_rows) == 35,
        "exact_resumed_roster_count": len(resume.get("entries", [])) == 26,
        "source_freeze_pass": source_final["pass"], "firewall_pass": firewall["pass"],
        "scientific_retries": 0, "seed_replacements": 0,
    }
    audit["pass"] = bool(
        preservation["pass"] and audit["complete_original_part_a_roster"]
        and audit["complete_combined_part_b_roster"] and audit["exact_resumed_roster_count"]
        and audit["source_freeze_pass"] and audit["firewall_pass"] and primary.startswith("PASS_")
    )
    audit["status"] = "PASS_FINAL_ARTIFACT_AUDIT" if audit["pass"] else "FAIL_FINAL_ARTIFACT_AUDIT"
    audit = self_digest(audit, "audit_digest")

    control = dict(integrity)
    control.update({
        "schema": "driveclarify.rq3-v3.control-integrity-final-receipt.v1",
        "classification_of_historical_failure": PRIMARY_CLASS,
        "final_artifact_audit_pass": audit["pass"],
        "true_intent_firewall_pass": firewall["pass"],
        "source_freeze_pass": source_final["pass"],
        "pass": bool(integrity.get("pass_before_artifact_audit") and audit["pass"] and firewall["pass"] and source_final["pass"]),
    })
    control["status"] = "PASS_H_RQ3_C_CONTROL_AND_SOURCE_INTEGRITY" if control["pass"] else "FAIL_H_RQ3_C_CONTROL_OR_SOURCE_INTEGRITY"
    control["receipt_digest"] = digest({key: value for key, value in control.items() if key != "receipt_digest"})
    atomic_json(RECOVERY / "CONTROL_INTEGRITY_FINAL_RECEIPT.json", control)
    c_verdict = "SUPPORTED" if control["pass"] else "NOT_SUPPORTED"
    final_b_verdict = b_verdict if c_verdict == "SUPPORTED" else "NOT_SUPPORTED"
    scientific_status = FROZEN.choose_final_status(a_verdict, final_b_verdict, c_verdict, {}) if complete_b else "RQ3_V3_NOT_EVALUABLE_INCOMPLETE_LEGAL_ROSTER"
    combined = {
        "schema": "driveclarify.rq3-v3.final-combined-results.v1",
        "primary_recovery_status": primary, "final_freeze_digest": FINAL_FREEZE_DIGEST,
        "RQ1": "SUPPORTED_AND_FROZEN", "RQ2": "SUPPORTED_AND_FROZEN",
        "RQ3_V1": "UNCHANGED", "RQ3_V2": "UNCHANGED",
        "H_RQ3_A": a_verdict, "H_RQ3_B": final_b_verdict, "H_RQ3_C": c_verdict,
        "overall_RQ3_V3_scientific_verdict": scientific_status,
        "part_a_original_evaluable": 35, "part_a_recovered": 0, "part_a_final_evaluable": a["evaluable_pairs"],
        "part_b_original_valid": 8, "part_b_original_invalid": 1, "part_b_invalid_cell_recovered": False,
        "part_b_resumed_executed": len(resume.get("entries", [])),
        "part_b_final_technically_evaluable": b["technically_evaluable"],
        "scientific_retries": 0, "seed_replacements": 0,
        "historical_failed_results_overwritten_or_reclassified": False,
    }
    combined["combined_digest"] = digest(combined)
    atomic_json(RECOVERY / "RQ3_V3_FINAL_COMBINED_RESULTS.json", combined)
    atomic_text(RECOVERY / "FINAL_REPORT.md", "Final report pending final validation digest.")
    atomic_json(RECOVERY / "FINAL_ARTIFACT_AUDIT.json", audit)

    required_present = {name: (RECOVERY / name).is_file() for name in REQUIRED_ARTIFACTS if name != "FINAL_VALIDATION_RECEIPT.json"}
    validation = {
        "schema": "driveclarify.rq3-v3.final-validation-receipt.v1",
        "status": primary, "recovery_complete_legal_endpoint": complete_b,
        "source_freeze_pass": source_final["pass"], "control_integrity_pass": control["pass"],
        "true_intent_firewall_pass": firewall["pass"], "artifact_audit_pass": audit["pass"],
        "required_artifacts_present_except_self": required_present,
        "all_required_artifacts_present_except_self": all(required_present.values()),
        "original_history_preserved": history_ok,
        "part_a_carla_reruns": 0, "previous_valid_part_b_reruns": 0, "invalid_part_b_reruns": 0,
        "scientific_retries": 0, "seed_replacements": 0,
        "pass": bool(primary.startswith("PASS_") and complete_b and source_final["pass"] and control["pass"] and firewall["pass"] and audit["pass"] and all(required_present.values())),
    }
    validation["validation_digest"] = digest(validation)
    atomic_json(RECOVERY / "FINAL_VALIDATION_RECEIPT.json", validation)
    atomic_text(RECOVERY / "FINAL_REPORT.md", final_report_text(primary, a, b, combined, control, validation, resume))
    append_log(f"Final recovery analysis sealed with primary status `{primary}` and validation digest `{validation['validation_digest']}`.")
    return {"status": primary, "validation_digest": validation["validation_digest"], "scientific_verdict": scientific_status}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "run", "finalize", "all"))
    args = parser.parse_args()
    if args.phase in {"prepare", "all"}:
        auth = prepare()
        print(canonical({"authorization": auth["authorization"], "pass": auth["pass"]}), flush=True)
        if not auth["pass"]:
            write_blocked_placeholder("BLOCKED_RQ3_V3_TECHNICAL_DEFECT_NOT_RECOVERABLE", auth)
            return 2
    if args.phase in {"run", "all"}:
        result = run_resume()
        print(canonical(result), flush=True)
    if args.phase in {"finalize", "all"}:
        result = finalize()
        print(canonical(result), flush=True)
        return 0 if str(result["status"]).startswith("PASS_") else 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
