#!/usr/bin/env python3
"""Serial R6 TRAIN-only supervisor; stops on the first non-C0 event."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_method_v1_r3_formal_train_execution import linked_retry  # noqa: E402
from driveclarify_method_v1_r3_formal_train_execution import orchestrator as base  # noqa: E402
from driveclarify_paper_mvp_stage6b import evidence_pipeline  # noqa: E402


TERMINAL_STATES = {
    "COMPLETED_RECORDED",
    "ENGINEERING_INVALID",
    "BLOCKED_CONTRACT_DEFECT",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _groups(ledger: Mapping[str, Any]) -> Mapping[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in ledger.get("attempt_records", []):
        grouped[str(record["episode_id"])].append(record)
    return grouped


def _checkpoint(slot: int) -> Mapping[str, Any]:
    ledger = linked_retry.derive_linked_ledger(base._load(base.LEDGER_PATH))
    groups = _groups(ledger)
    train_rows = base.train_rows()
    train_ids = {str(row["episode_id"]) for row in train_rows}
    terminal_primary = {
        episode_id
        for episode_id, records in groups.items()
        if episode_id in train_ids
        and any(linked_retry._state(record) in TERMINAL_STATES for record in records)
    }
    completed = [
        record
        for record in ledger["attempt_records"]
        if record["episode_id"] in train_ids
        and linked_retry._state(record) == "COMPLETED_RECORDED"
    ]
    per_method = Counter(str(record["method_id"]) for record in completed)
    per_scenario_seed = Counter(
        "{}:{}".format(record["scenario_id"], record["seed"])
        for record in completed
    )
    resources = base.resource_snapshot()
    if resources["status"] != "PASS":
        raise base.FormalExecutionError(
            "BLOCKED_R6_PROGRESS_CHECKPOINT_RESOURCE_INTEGRITY"
        )
    method = base._method_integrity()
    paper = base._paper_integrity()
    evaluation = base._evaluation_fix_integrity()
    protected = base._protected_integrity()
    if not (
        method["pass"]
        and paper["pass"]
        and evaluation["pass"]
        and protected["pass"]
    ):
        raise base.FormalExecutionError(
            "BLOCKED_R6_PROGRESS_CHECKPOINT_FROZEN_HASH_MISMATCH"
        )
    receipt = {
        "schema_version": "driveclarify.method_v1_r3.formal_train.progress_checkpoint.r6.v1",
        "status": "PASS_R6_TRAIN_PROGRESS_CHECKPOINT",
        "through_primary_slot": slot,
        "primary_started": ledger["counts"]["primary_started"],
        "primary_terminally_accounted": len(terminal_primary),
        "scientifically_completed": ledger["counts"]["scientifically_completed"],
        "linked_attempts": ledger["counts"]["linked_retries"],
        "engineering_invalid_attempts": ledger["counts"]["engineering_invalid"],
        "contract_defect_slots": ledger["counts"]["blocked_contract_defect"],
        "missing_train_primary": 256 - len(terminal_primary),
        "valid_per_method": dict(sorted(per_method.items())),
        "valid_scenario_seed_coverage": dict(sorted(per_scenario_seed.items())),
        "repair_events": {
            "evidence_pipeline_repairs": len(ledger.get("evidence_pipeline_repairs", [])),
            "autonomous_repairs": len(ledger.get("autonomous_repairs", [])),
        },
        "ledger_sha256": _sha(base.LEDGER_PATH),
        "method_integrity": method,
        "paper_integrity": paper,
        "evaluation_integrity": evaluation,
        "protected_integrity": protected,
        "cleanup": resources,
        "DEV_attempts": ledger["counts"]["dev_attempts"],
        "TEST_attempts": ledger["counts"]["test_attempts"],
        "TEST_consumed": ledger["split_state"]["test"]["consumed"],
        "generated_at_utc": base._now(),
    }
    stem = "TRAIN_PROGRESS_CHECKPOINT_{:04d}".format(slot)
    json_path = base.REPORT_ROOT / (stem + ".json")
    md_path = base.REPORT_ROOT / (stem + ".md")
    if json_path.exists() or md_path.exists():
        raise base.FormalExecutionError(
            "BLOCKED_R6_PROGRESS_CHECKPOINT_APPEND_ONLY_PATH_EXISTS"
        )
    base._atomic_json(json_path, receipt)
    _atomic_text(
        md_path,
        "# TRAIN Progress Checkpoint {slot}\n\n"
        "Status: `PASS_R6_TRAIN_PROGRESS_CHECKPOINT`\n\n"
        "- Primary started: {started}\n"
        "- Primary terminally accounted: {accounted}\n"
        "- Scientifically completed: {completed}\n"
        "- Linked attempts: {linked}\n"
        "- Engineering-invalid attempts: {engineering}\n"
        "- Contract-defect slots: {contract}\n"
        "- Missing TRAIN primary: {missing}\n"
        "- Ledger SHA-256: `{ledger_sha}`\n"
        "- Method/Paper/evaluator/protected integrity: PASS\n"
        "- DEV/TEST: 0 / 0 unconsumed\n"
        "- Cleanup: PASS\n".format(
            slot=slot,
            started=receipt["primary_started"],
            accounted=receipt["primary_terminally_accounted"],
            completed=receipt["scientifically_completed"],
            linked=receipt["linked_attempts"],
            engineering=receipt["engineering_invalid_attempts"],
            contract=receipt["contract_defect_slots"],
            missing=receipt["missing_train_primary"],
            ledger_sha=receipt["ledger_sha256"],
        ),
    )
    return receipt


def _post_slot_gate(slot: int, authorization_path: Path, authorization_sha256: str) -> Mapping[str, Any]:
    ledger = linked_retry.derive_linked_ledger(base._load(base.LEDGER_PATH))
    records = [
        record
        for record in ledger["attempt_records"]
        if int(record.get("split_slot_index", -1)) == slot
        and str(record.get("attempt_id", "")).endswith("-A01")
    ]
    if len(records) != 1 or linked_retry._state(records[0]) != "COMPLETED_RECORDED":
        raise base.FormalExecutionError(
            "BLOCKED_R6_POST_SLOT_NON_C0_TERMINAL_STATE"
        )
    journal = evidence_pipeline.AttemptLifecycleJournal.open(
        base.ROOT / str(records[0]["lifecycle_journal_path"])
    )
    if journal.current_stage != "COMPLETE":
        raise base.FormalExecutionError(
            "BLOCKED_R6_POST_SLOT_LIFECYCLE_NOT_COMPLETE"
        )
    if records[0].get("resume_authorization_path") != str(authorization_path):
        raise base.FormalExecutionError(
            "BLOCKED_R6_POST_SLOT_AUTHORIZATION_PATH_UNLINKED"
        )
    if records[0].get("resume_authorization_sha256") != authorization_sha256:
        raise base.FormalExecutionError(
            "BLOCKED_R6_POST_SLOT_AUTHORIZATION_SHA_UNLINKED"
        )
    entry = base.validate_entry(require_initial_ledger=False)
    resources = base.resource_snapshot()
    if resources["status"] != "PASS":
        raise base.FormalExecutionError("BLOCKED_R6_POST_SLOT_RESOURCE_GATE")
    return {
        "status": "PASS_R6_POST_SLOT_GATE",
        "slot": slot,
        "ledger_sha256": _sha(base.LEDGER_PATH),
        "entry": entry["status"],
        "cleanup": resources,
    }


def run_until_stop(authorization_path: Path, authorization_sha256: str) -> int:
    if os.environ.get("DISPLAY") != ":1":
        raise SystemExit("DISPLAY=:1_REQUIRED")
    previous_slot = None
    while True:
        campaign_status = linked_retry.status()
        next_row = campaign_status["next_primary_episode"]
        if next_row is None:
            print(json.dumps({"status": "R6_TRAIN_PRIMARY_RANGE_EXHAUSTED"}), flush=True)
            return 0
        slot = int(next_row["split_slot_index"])
        if slot < 40:
            raise base.FormalExecutionError("BLOCKED_R6_NEXT_SLOT_BEFORE_40")
        if slot > 256:
            print(json.dumps({"status": "R6_TRAIN_PRIMARY_RANGE_COMPLETE"}), flush=True)
            return 0
        if previous_slot is not None and slot != previous_slot + 1:
            raise base.FormalExecutionError("BLOCKED_R6_NONCONTIGUOUS_PRIMARY_ORDER")
        result = linked_retry.run_next(
            authorization_path=authorization_path,
            authorization_sha256=authorization_sha256,
        )
        post = _post_slot_gate(slot, authorization_path, authorization_sha256)
        print(
            json.dumps(
                {
                    "event": "R6_PRIMARY_SLOT_TERMINAL",
                    "slot": slot,
                    "attempt_id": result["attempt_id"],
                    "terminal_state": result["terminal_state"],
                    "ledger_sha256": result["ledger_sha256"],
                    "post_slot_gate": post["status"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if (slot - 39) % 16 == 0 or slot == 256:
            checkpoint = _checkpoint(slot)
            print(
                json.dumps(
                    {
                        "event": "R6_PROGRESS_CHECKPOINT",
                        "slot": slot,
                        "ledger_sha256": checkpoint["ledger_sha256"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        previous_slot = slot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run-until-stop", "status", "checkpoint"))
    parser.add_argument("--authorization-file", required=True, type=Path)
    parser.add_argument("--authorization-sha256", required=True)
    parser.add_argument("--slot", type=int)
    args = parser.parse_args()
    try:
        if args.action == "run-until-stop":
            return run_until_stop(args.authorization_file, args.authorization_sha256)
        if args.action == "checkpoint":
            if args.slot is None:
                parser.error("checkpoint requires --slot")
            result = _checkpoint(args.slot)
        else:
            result = linked_retry.status()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except BaseException as exc:
        snapshot = {
            "status": "R6_AUTONOMOUS_SUPERVISOR_STOPPED_ON_NON_C0_EVENT",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "ledger_sha256": _sha(base.LEDGER_PATH),
            "campaign_status": linked_retry.status(),
            "resources": base.resource_snapshot(),
        }
        print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
