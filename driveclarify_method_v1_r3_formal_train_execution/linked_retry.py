"""Append-only slot-19 A02 support for the frozen formal TRAIN campaign.

This execution-only extension preserves the original orchestrator byte-for-byte.
It adds the one explicitly authorized linked infrastructure attempt, then keeps
the original one-primary-slot-per-invocation execution order.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping

from driveclarify_paper_mvp_stage6b import backend as stage6b_backend
from driveclarify_paper_mvp_stage6b import evidence_pipeline

from . import orchestrator as base


A02_ATTEMPT_ID = base.FAMILY_ID + "-0019-A02"
A02_EPISODE_ID = base.FAMILY_ID + "-0019"
A02_PRIOR_LEDGER_SHA256 = "40ba2a95981332942498470fd3e5b86a908e09021eda278cb700b1b4276a2e87"
OLD_REPORT_MANIFEST_SHA256 = "e6a25698ca10d64dce872f96a77517e55d3b810cfc588bb654387419107e610b"
AUTHORIZATION_PATH = Path(
    "/home/buaa/.codex/attachments/3a5ecc0c-126d-4500-bbbf-e76d7b63fc00/pasted-text.txt"
)
PREFLIGHT_PATH = base.REPORT_ROOT / "SLOT_0019_A02_ENTRY_PREFLIGHT.json"
EXECUTION_RECEIPT_PATH = base.REPORT_ROOT / "SLOT_0019_A02_EXECUTION_RECEIPT.json"
HISTORY_PATH = base.REPORT_ROOT / "SLOT_0019_LINKED_ATTEMPT_HISTORY.json"
MODULE_PATH = Path(__file__).resolve()
TOOL_PATH = base.ROOT / "tools/run_method_v1_r3_formal_train_linked_retry.py"
TEST_PATH = base.ROOT / "tests/method_v1_r3_formal_train_execution/test_linked_retry.py"
PIPELINE_REPAIR_STATUS = "PASS_EVIDENCE_PIPELINE_REPAIR_READY_FOR_EXPLICIT_SLOT40_AUTHORIZATION"
FAILED_ENTRY_AUTHORIZATION_PATH = base.PREP_ROOT / "SLOT40_RESUME_AUTHORIZATION.json"
FAILED_ENTRY_AUTHORIZATION_SHA256 = "3dcc52b0c325b76930db4d4fa965cfec060763571de7481d08c7ab7f0584bf2b"
CANONICAL_CAMPAIGN_AUTHORIZATION_PATH = base.PREP_ROOT / (
    "FORMAL_TRAIN_SLOT40_TO_SLOT256_CAMPAIGN_AUTHORIZATION_R6.json"
)
AUTHORIZATION_BINDING_TEST_RECEIPT_PATH = base.REPORT_ROOT / (
    "AUTHORIZATION_PATH_BINDING_TEST_RECEIPT_R6.json"
)
AUTONOMOUS_EXECUTION_POLICY_PATH = base.REPORT_ROOT / "AUTONOMOUS_TRAIN_EXECUTION_POLICY_R6.md"
AUTHORIZATION_ENTRY_LEDGER_SHA256 = "fccec2010579a2fc57d581e2ca89d1278ef7064df5b087a5be7c8578b81cad07"
EVIDENCE_PIPELINE_R4_AGGREGATE_SHA256 = "ea58026af4dde015a6b64459755abe17d0e7aaa0e8ec68aa659f60c973bbb39e"
RESUME_ENTRY_R5_AGGREGATE_SHA256 = "7ec39a7c581db1468034d13d2e2d160e3dd68d7f75177797e2b5814c2a0f0f94"
AUTHORIZATION_SCHEMA_R6 = "driveclarify.method_v1_r3.formal_train.campaign_authorization.r6.v1"
AUTHORIZATION_ID_R6 = "DC-MV1-R3-FORMAL-TRAIN-20260815-SLOT0040-0256-R6"
SLOT39_ATTEMPT_ID = base.FAMILY_ID + "-0039-A01"
SLOT39_EPISODE_ID = base.FAMILY_ID + "-0039"


def _state(attempt: Mapping[str, Any]) -> str:
    return base._attempt_state(attempt)


def _episode_groups(attempts: list[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        groups[str(attempt["episode_id"])].append(attempt)
    return groups


def derive_linked_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    attempts = ledger.get("attempt_records", [])
    groups = _episode_groups(attempts)
    attempt_states = Counter(_state(attempt) for attempt in attempts)
    primary = [attempt for attempt in attempts if str(attempt["attempt_id"]).endswith("-A01")]
    linked = [attempt for attempt in attempts if not str(attempt["attempt_id"]).endswith("-A01")]
    terminal_states = {"COMPLETED_RECORDED", "ENGINEERING_INVALID", "BLOCKED_CONTRACT_DEFECT"}
    accounted_episode_ids = {
        episode_id
        for episode_id, records in groups.items()
        if any(_state(record) in terminal_states for record in records)
    }
    scientifically_completed_ids = {
        episode_id
        for episode_id, records in groups.items()
        if any(_state(record) == "COMPLETED_RECORDED" for record in records)
    }
    latest_by_episode = {episode_id: _state(records[-1]) for episode_id, records in groups.items()}
    active_engineering_invalid = [
        episode_id for episode_id, state in latest_by_episode.items() if state == "ENGINEERING_INVALID"
    ]
    active_contract_defect = [
        episode_id for episode_id, state in latest_by_episode.items() if state == "BLOCKED_CONTRACT_DEFECT"
    ]
    running_linked = any(_state(attempt) == "RUNNING" for attempt in linked)
    counts = {
        "scheduled": 768,
        "started": len(attempts),
        "primary_started": len(primary),
        "linked_retries": len(linked),
        "completed_recorded": attempt_states["COMPLETED_RECORDED"],
        "scientifically_completed": len(scientifically_completed_ids),
        "engineering_invalid": attempt_states["ENGINEERING_INVALID"],
        "blocked_contract_defect": attempt_states["BLOCKED_CONTRACT_DEFECT"],
        "remaining": 768 - len(accounted_episode_ids),
        "fresh_e3_runs": len(attempts),
        "formal_train_runs": len(attempts),
        "dev_attempts": 0,
        "test_attempts": 0,
        "training_jobs": 0,
        "a800_jobs": 0,
    }
    slot19 = groups.get(A02_EPISODE_ID, [])
    slot19_a02 = next(
        (attempt for attempt in slot19 if attempt["attempt_id"] == A02_ATTEMPT_ID), None
    )
    if running_linked:
        status = "FRESH_FORMAL_TRAIN_SLOT19_LINKED_RETRY_RUNNING"
        train_status = "LINKED_RETRY_RUNNING"
    elif active_contract_defect:
        status = "BLOCKED_FORMAL_EVIDENCE_CONTRACT_DEFECT"
        train_status = "BLOCKED_CONTRACT_DEFECT"
    elif active_engineering_invalid:
        if slot19_a02 is not None and _state(slot19_a02) == "ENGINEERING_INVALID":
            status = "BLOCKED_FRESH_FORMAL_TRAIN_SLOT19_LINKED_RETRY_EXHAUSTED_INFRASTRUCTURE_REVIEW_REQUIRED"
        elif slot19_a02 is None and active_engineering_invalid == [A02_EPISODE_ID]:
            status = "BLOCKED_FIRST_ENGINEERING_INVALID_APPEND_ONLY_REVIEW_REQUIRED"
        else:
            status = "BLOCKED_FRESH_FORMAL_TRAIN_NEXT_ENGINEERING_INVALID_APPEND_ONLY_REVIEW_REQUIRED"
        train_status = "BLOCKED_ENGINEERING_INVALID"
    elif len(scientifically_completed_ids) == 256:
        status = "FORMAL_TRAIN_TERMINAL_256_OF_256_PENDING_CLOSEOUT"
        train_status = "TERMINAL_PENDING_CLOSEOUT"
    else:
        status = "FRESH_FORMAL_TRAIN_IN_PROGRESS"
        train_status = "IN_PROGRESS"
    ledger["counts"] = counts
    ledger["status"] = status
    ledger["split_state"] = {
        "train": {
            "status": train_status,
            "scheduled": 256,
            "attempts": len(attempts),
            "primary_started": len(primary),
            "linked_retries": len(linked),
            "scientifically_completed": len(scientifically_completed_ids),
        },
        "dev": {"status": "SEALED_ZERO_ATTEMPTS", "scheduled": 256, "attempts": 0},
        "test": {
            "status": "SEALED_UNCONSUMED_ZERO_ATTEMPTS",
            "scheduled": 256,
            "attempts": 0,
            "consumed": False,
        },
    }
    ledger["retry_policy"]["append_only_slot19_a02_authorized"] = True
    ledger["retry_policy"]["maximum_linked_engineering_reruns_slot19"] = 1
    ledger["retry_policy"]["scientific_retry"] = 0
    ledger["retry_policy"]["automatic_retry"] = 0
    ledger["updated_at_utc"] = base._now()
    return ledger


def _save(ledger: dict[str, Any]) -> None:
    base._atomic_json(base.LEDGER_PATH, derive_linked_ledger(ledger))


def _verify_old_report_manifest() -> Mapping[str, Any]:
    path = base.REPORT_ROOT / "ARTIFACT_HASHES.json"
    if base._sha(path) != OLD_REPORT_MANIFEST_SHA256:
        raise base.FormalExecutionError("BLOCKED_OLD_REPORT_MANIFEST_HASH_MISMATCH")
    manifest = base._load(path)
    mismatches = []
    for relative, expected in manifest["files"].items():
        observed = base._sha(base.REPORT_ROOT / relative)
        if observed != expected:
            mismatches.append({"path": relative, "expected": expected, "observed": observed})
    if mismatches:
        raise base.FormalExecutionError("BLOCKED_OLD_REPORT_ARTIFACT_MUTATION")
    return {"manifest_sha256": OLD_REPORT_MANIFEST_SHA256, "mismatches": mismatches, "pass": True}


def _source_hashes() -> Mapping[str, str]:
    return {
        str(path.relative_to(base.ROOT)): base._sha(path)
        for path in (MODULE_PATH, TOOL_PATH, TEST_PATH)
    }


def _slot19_identity() -> Mapping[str, Any]:
    row = base.train_rows()[18]
    spec = base._fresh_spec(row)
    launch = base._load(
        base.ARTIFACT_ROOT
        / base.FAMILY_ID
        / "episodes"
        / A02_EPISODE_ID
        / "EPISODE_LAUNCH_CONTRACT.json"
    )["episode"]
    fields = (
        "episode_id", "scenario_id", "seed", "method_id", "runtime_fixture_id",
        "runtime_config_id", "route_id", "route_path", "runtime_manifest_sha256",
        "schedule_sha256", "split", "town", "raw_instruction", "information_expected",
    )
    current = {field: str(getattr(spec, field)) if field == "route_path" else getattr(spec, field) for field in fields}
    previous = {field: launch[field] for field in fields}
    matches = {field: current[field] == previous[field] for field in fields}
    matches.update(
        {
            "split_slot_index": row["split_slot_index"] == 19,
            "global_slot_index": row["slot_index"] == 19,
            "checkpoint": base._sha(stage6b_backend.CHECKPOINT) == base.CHECKPOINT_SHA256,
            "method_hash": base._method_integrity()["pass"],
            "evaluator_hash": base._evaluation_fix_integrity()["pass"],
            "paper_protocol_hash": base._paper_integrity()["pass"],
        }
    )
    if not all(matches.values()):
        raise base.FormalExecutionError("BLOCKED_SLOT19_A02_SCIENTIFIC_IDENTITY_MISMATCH")
    return {"row": row, "a01": previous, "a02": current, "matches": matches, "pass": True}


def write_a02_preflight() -> Mapping[str, Any]:
    if PREFLIGHT_PATH.exists():
        return verify_a02_preflight(require_prior_ledger=True)
    base._verify_amendment()
    entry = base.validate_entry(require_initial_ledger=False)
    ledger = base._load(base.LEDGER_PATH)
    ledger_sha = base._sha(base.LEDGER_PATH)
    expected_counts = {
        "scheduled": 768, "started": 19, "completed_recorded": 18,
        "engineering_invalid": 1, "blocked_contract_defect": 0, "remaining": 749,
    }
    if ledger_sha != A02_PRIOR_LEDGER_SHA256:
        raise base.FormalExecutionError("BLOCKED_A02_PRIOR_LEDGER_HASH_MISMATCH")
    if any(ledger["counts"].get(key) != value for key, value in expected_counts.items()):
        raise base.FormalExecutionError("BLOCKED_A02_PRIOR_LEDGER_COUNTS_MISMATCH")
    if len(ledger["attempt_records"]) != 19:
        raise base.FormalExecutionError("BLOCKED_A02_PRIOR_ATTEMPT_COUNT_MISMATCH")
    a01 = ledger["attempt_records"][-1]
    if not (
        a01["attempt_id"] == A02_EPISODE_ID + "-A01"
        and a01["episode_id"] == A02_EPISODE_ID
        and a01["scenario_id"] == "DCV0-S001"
        and int(a01["seed"]) == 5103
        and a01["method_id"] == "always_ask"
        and _state(a01) == "ENGINEERING_INVALID"
    ):
        raise base.FormalExecutionError("BLOCKED_SLOT19_A01_HISTORY_MISMATCH")
    resources = base.resource_snapshot()
    if os.environ.get("DISPLAY") != ":1" or resources["status"] != "PASS":
        raise base.FormalExecutionError("BLOCKED_A02_PHYSICAL_RUNTIME_PREFLIGHT")
    old_report = _verify_old_report_manifest()
    identity = _slot19_identity()
    source_hashes = _source_hashes()
    receipt = {
        "schema_version": "driveclarify.method_v1_r3.formal_train.slot19_a02_entry.v1",
        "status": "PASS_SLOT19_A02_EXACT_PREFLIGHT_LINKED_RETRY_AUTHORIZED",
        "authorization": {
            "attempt_id": A02_ATTEMPT_ID,
            "authorization_path": str(AUTHORIZATION_PATH),
            "authorization_sha256": base._sha(AUTHORIZATION_PATH),
            "scientific_retry": False,
            "automatic_retry": False,
            "linked_engineering_retry_ordinal": 1,
            "a03_authorized": False,
        },
        "entry": entry,
        "execution_extension_source_hashes": source_hashes,
        "execution_extension_aggregate_sha256": hashlib.sha256(
            "".join("{}  {}\n".format(value, key) for key, value in sorted(source_hashes.items())).encode()
        ).hexdigest(),
        "prior_ledger_sha256": ledger_sha,
        "prior_counts": expected_counts,
        "a01_terminal_state": _state(a01),
        "slot19_identity": identity,
        "old_blocked_report_integrity": old_report,
        "resources": resources,
        "display_contract": {
            "display": os.environ.get("DISPLAY"),
            "physical_x11": resources["physical_x11_1_available"],
            "headless": False,
            "xvfb": False,
            "render_offscreen": False,
            "vnc": False,
            "remote_display_forwarding": False,
        },
        "dev_attempts": 0,
        "test_attempts": 0,
        "test_consumed": False,
        "training_jobs": 0,
        "a800_jobs": 0,
        "generated_at_utc": base._now(),
    }
    base._atomic_json(PREFLIGHT_PATH, receipt)
    return receipt


def verify_a02_preflight(*, require_prior_ledger: bool) -> Mapping[str, Any]:
    receipt = base._load(PREFLIGHT_PATH)
    if receipt.get("status") != "PASS_SLOT19_A02_EXACT_PREFLIGHT_LINKED_RETRY_AUTHORIZED":
        raise base.FormalExecutionError("BLOCKED_A02_PREFLIGHT_STATUS")
    for relative, expected in receipt["execution_extension_source_hashes"].items():
        if base._sha(base.ROOT / relative) != expected:
            raise base.FormalExecutionError("BLOCKED_A02_EXECUTION_EXTENSION_MUTATION:" + relative)
    if require_prior_ledger and base._sha(base.LEDGER_PATH) != receipt["prior_ledger_sha256"]:
        raise base.FormalExecutionError("BLOCKED_A02_LEDGER_CHANGED_AFTER_PREFLIGHT")
    base._verify_amendment()
    base.validate_entry(require_initial_ledger=False)
    return receipt


def _attempt_output(row: Mapping[str, Any], attempt_id: str, linked: bool) -> Path:
    root = base.ARTIFACT_ROOT / base.FAMILY_ID / "episodes" / str(row["episode_id"])
    return root / "linked_attempts" / "A02" if linked else root


def _stable_artifact_hashes(output: Path) -> Mapping[str, Any]:
    """Hash immutable evidence; the lifecycle journal advances after ledger commit."""

    return {
        relative: value
        for relative, value in base._artifact_hashes(output).items()
        if relative != evidence_pipeline.JOURNAL_FILENAME
        and not relative.startswith(evidence_pipeline.MARKER_DIRECTORY + "/")
    }


def _hash_aggregate(hashes: Mapping[str, str]) -> str:
    return hashlib.sha256(
        "".join(
            "{}  {}\n".format(value, key)
            for key, value in sorted(hashes.items())
        ).encode("utf-8")
    ).hexdigest()


def _verify_authorization_binding_repair() -> Mapping[str, Any]:
    if not AUTHORIZATION_BINDING_TEST_RECEIPT_PATH.is_file():
        raise base.FormalExecutionError(
            "BLOCKED_AUTHORIZATION_BINDING_TEST_RECEIPT_REQUIRED"
        )
    receipt = base._load(AUTHORIZATION_BINDING_TEST_RECEIPT_PATH)
    if receipt.get("status") != "PASS_AUTHORIZATION_PATH_BINDING_R6":
        raise base.FormalExecutionError(
            "BLOCKED_AUTHORIZATION_BINDING_TEST_RECEIPT_NOT_PASS"
        )
    if receipt.get("canonical_authorization_path") != str(
        CANONICAL_CAMPAIGN_AUTHORIZATION_PATH
    ):
        raise base.FormalExecutionError(
            "BLOCKED_AUTHORIZATION_BINDING_RECEIPT_CANONICAL_PATH_MISMATCH"
        )
    if receipt.get("failed_entry_authorization_sha256") != (
        FAILED_ENTRY_AUTHORIZATION_SHA256
    ):
        raise base.FormalExecutionError(
            "BLOCKED_FAILED_ENTRY_AUTHORIZATION_HASH_NOT_BOUND"
        )
    if receipt.get("r4_artifact_aggregate_sha256") != (
        EVIDENCE_PIPELINE_R4_AGGREGATE_SHA256
    ) or receipt.get("r5_artifact_aggregate_sha256") != (
        RESUME_ENTRY_R5_AGGREGATE_SHA256
    ):
        raise base.FormalExecutionError(
            "BLOCKED_AUTHORIZATION_BINDING_PRIOR_ARTIFACT_SEAL_MISMATCH"
        )
    hashes = receipt.get("execution_only_source_hashes", {})
    if not isinstance(hashes, dict) or not hashes:
        raise base.FormalExecutionError(
            "BLOCKED_AUTHORIZATION_BINDING_SOURCE_HASHES_REQUIRED"
        )
    mismatches = []
    for relative, expected in sorted(hashes.items()):
        path = base.ROOT / str(relative)
        observed = base._sha(path) if path.is_file() else None
        if observed != expected:
            mismatches.append(
                {"path": str(relative), "expected": expected, "observed": observed}
            )
    aggregate = _hash_aggregate(hashes)
    if mismatches or aggregate != receipt.get(
        "execution_only_source_aggregate_sha256"
    ):
        raise base.FormalExecutionError(
            "BLOCKED_AUTHORIZATION_BINDING_EXECUTION_SOURCE_HASH_MISMATCH"
        )
    return {
        "status": "PASS",
        "receipt": receipt,
        "receipt_sha256": base._sha(AUTHORIZATION_BINDING_TEST_RECEIPT_PATH),
        "source_integrity": {
            "status": "PASS",
            "aggregate_sha256": aggregate,
            "mismatches": mismatches,
        },
    }


def _verify_pipeline_repair_record(ledger: Mapping[str, Any]) -> Mapping[str, Any]:
    repairs = ledger.get("evidence_pipeline_repairs", [])
    if not repairs:
        raise base.FormalExecutionError("BLOCKED_EVIDENCE_PIPELINE_REPAIR_RECORD_REQUIRED")
    record = repairs[-1]
    if record.get("status") != PIPELINE_REPAIR_STATUS:
        raise base.FormalExecutionError("BLOCKED_EVIDENCE_PIPELINE_REPAIR_NOT_PASS")
    if record.get("resume_from_slot40_authorized") is not False:
        raise base.FormalExecutionError("BLOCKED_REPAIR_RECORD_PREMATURE_RESUME_AUTHORIZATION")
    r1_core_mismatches = []
    for relative, expected in sorted(
        record.get("execution_only_source_hashes", {}).items()
    ):
        if relative == "driveclarify_method_v1_r3_formal_train_execution/linked_retry.py":
            continue
        path = base.ROOT / str(relative)
        observed = base._sha(path) if path.is_file() else None
        if observed != expected:
            r1_core_mismatches.append(
                {"path": str(relative), "expected": expected, "observed": observed}
            )
    if r1_core_mismatches:
        raise base.FormalExecutionError(
            "BLOCKED_R1_EVIDENCE_PIPELINE_CORE_SOURCE_HASH_MISMATCH"
        )
    binding = _verify_authorization_binding_repair()
    return {
        "record": record,
        "r1_core_integrity": {
            "status": "PASS",
            "mismatches": r1_core_mismatches,
        },
        "authorization_binding_repair": binding,
        "source_integrity": binding["source_integrity"],
    }


def _exact_authorization_path(path: Path | str | None) -> Path:
    if path is None:
        raise base.FormalExecutionError(
            "BLOCKED_EXPLICIT_CAMPAIGN_AUTHORIZATION_FILE_REQUIRED"
        )
    candidate = Path(path)
    if not candidate.is_absolute():
        raise base.FormalExecutionError(
            "BLOCKED_CAMPAIGN_AUTHORIZATION_PATH_MUST_BE_ABSOLUTE"
        )
    candidate = Path(os.path.abspath(str(candidate)))
    if candidate == FAILED_ENTRY_AUTHORIZATION_PATH:
        raise base.FormalExecutionError(
            "BLOCKED_FAILED_ENTRY_AUTHORIZATION_NOT_REUSABLE"
        )
    if candidate != CANONICAL_CAMPAIGN_AUTHORIZATION_PATH:
        raise base.FormalExecutionError(
            "BLOCKED_CAMPAIGN_AUTHORIZATION_NONCANONICAL_PATH"
        )
    if candidate.is_symlink():
        raise base.FormalExecutionError(
            "BLOCKED_CAMPAIGN_AUTHORIZATION_SYMLINK_FORBIDDEN"
        )
    if not candidate.is_file():
        raise base.FormalExecutionError(
            "BLOCKED_EXPLICIT_CAMPAIGN_AUTHORIZATION_FILE_MISSING"
        )
    if candidate.stat().st_nlink != 1:
        raise base.FormalExecutionError(
            "BLOCKED_CAMPAIGN_AUTHORIZATION_ALIAS_HARDLINK_FORBIDDEN"
        )
    return candidate


def _load_and_verify_campaign_authorization(
    *,
    authorization_path: Path | str | None,
    authorization_sha256: str | None,
    repair: Mapping[str, Any],
) -> Mapping[str, Any]:
    path = _exact_authorization_path(authorization_path)
    if not isinstance(authorization_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", authorization_sha256
    ) is None:
        raise base.FormalExecutionError(
            "BLOCKED_EXPLICIT_CAMPAIGN_AUTHORIZATION_SHA256_REQUIRED"
        )
    observed_sha256 = base._sha(path)
    if observed_sha256 != authorization_sha256:
        raise base.FormalExecutionError(
            "BLOCKED_CAMPAIGN_AUTHORIZATION_CALLER_SHA256_MISMATCH"
        )
    authorization = base._load(path)
    binding = repair["authorization_binding_repair"]
    expected = {
        "authorization_schema": AUTHORIZATION_SCHEMA_R6,
        "authorization_id": AUTHORIZATION_ID_R6,
        "authorization_status": "ACTIVE",
        "experiment_family_id": base.FAMILY_ID,
        "scope": "TRAIN_ONLY",
        "first_primary_slot": 40,
        "last_primary_slot": 256,
        "method_file_count": 64,
        "method_sha": base.METHOD_FREEZE_SHA256,
        "paper_protocol_sha": base.PAPER_PROTOCOL_SHA256,
        "evaluator_sha": base.EVALUATION_FIX_SHA256,
        "checkpoint_sha": base.CHECKPOINT_SHA256,
        "SimLingo_protected_state": {
            "head": base.SIMLINGO_HEAD,
            "diff_sha256": base.SIMLINGO_DIFF_SHA256,
        },
        "historical_E3_state": base.HISTORICAL_HASHES,
        "entry_ledger_sha": AUTHORIZATION_ENTRY_LEDGER_SHA256,
        "evidence_pipeline_repair_aggregate": EVIDENCE_PIPELINE_R4_AGGREGATE_SHA256,
        "R5_aggregate": RESUME_ENTRY_R5_AGGREGATE_SHA256,
        "slot39_no_rerun": True,
        "slot39_no_replacement": True,
        "automatic_execution_infrastructure_repairs": "AUTHORIZED",
        "automatic_scientific_method_repairs": "FORBIDDEN",
        "DEV_access": "FORBIDDEN",
        "TEST_access": "FORBIDDEN",
        "training": "FORBIDDEN",
        "LoRA": "FORBIDDEN",
        "A800_jobs": "FORBIDDEN",
        "issued_from_this_explicit_user_prompt": True,
        "authorization_path": str(CANONICAL_CAMPAIGN_AUTHORIZATION_PATH),
        "failed_entry_authorization_sha256": FAILED_ENTRY_AUTHORIZATION_SHA256,
        "authorization_binding_source_aggregate_sha256": repair[
            "source_integrity"
        ]["aggregate_sha256"],
        "authorization_binding_test_receipt_sha256": binding[
            "receipt_sha256"
        ],
    }
    mismatches = {
        key: {"expected": value, "observed": authorization.get(key)}
        for key, value in expected.items()
        if authorization.get(key) != value
    }
    if mismatches:
        raise base.FormalExecutionError(
            "BLOCKED_CAMPAIGN_AUTHORIZATION_FIELD_MISMATCH:"
            + json.dumps(mismatches, sort_keys=True)
        )
    return {
        "path": path,
        "sha256": observed_sha256,
        "authorization": authorization,
    }


def _verify_slot39_permanent_boundary(ledger: Mapping[str, Any]) -> Mapping[str, Any]:
    records = [
        record
        for record in ledger.get("attempt_records", [])
        if record.get("episode_id") == SLOT39_EPISODE_ID
    ]
    if len(records) != 1 or records[0].get("attempt_id") != SLOT39_ATTEMPT_ID:
        raise base.FormalExecutionError("BLOCKED_SLOT39_ATTEMPT_IDENTITY_OR_HISTORY_MISMATCH")
    if _state(records[0]) != "BLOCKED_CONTRACT_DEFECT":
        raise base.FormalExecutionError("BLOCKED_SLOT39_PERMANENT_STATE_MISMATCH")
    if any(
        str(record.get("attempt_id", "")).startswith(SLOT39_EPISODE_ID + "-")
        and record.get("attempt_id") != SLOT39_ATTEMPT_ID
        for record in ledger.get("attempt_records", [])
    ):
        raise base.FormalExecutionError("BLOCKED_SLOT39_A02_A03_OR_REPLACEMENT_PRESENT")
    reviews = ledger.get("evidence_accounting_reviews", [])
    if not reviews:
        raise base.FormalExecutionError("BLOCKED_SLOT39_ACCOUNTING_REVIEW_REQUIRED")
    review = reviews[-1]
    required = (
        review.get("reviewed_attempt_id") == SLOT39_ATTEMPT_ID
        and review.get("permanent_classification") == "CONTRACT_DEFECT_NONZERO_EXPOSURE"
        and review.get("scientific_outcome") == "UNKNOWN"
        and review.get("rerun_eligible") is False
        and review.get("replacement_eligible") is False
        and review.get("slot40_started") is False
    )
    if not required:
        raise base.FormalExecutionError("BLOCKED_SLOT39_ACCOUNTING_BOUNDARY_MISMATCH")
    return {
        "status": "PASS_SLOT39_PERMANENT_BOUNDARY",
        "attempt_id": SLOT39_ATTEMPT_ID,
        "outcome": "UNKNOWN",
        "rerun_eligible": False,
        "replacement_eligible": False,
    }


def pre_slot_gate(
    *,
    require_resume_authorization: bool,
    authorization_path: Path | str | None = None,
    authorization_sha256: str | None = None,
) -> Mapping[str, Any]:
    """Reconcile before selection and fail closed in the reconciliation call."""

    reconciliation = evidence_pipeline.reconcile_incomplete_attempts(
        ledger_path=base.LEDGER_PATH,
        repository_root=base.ROOT,
        derive_ledger=derive_linked_ledger,
        resource_snapshot=base.resource_snapshot,
        journal_search_root=(
            base.ARTIFACT_ROOT / base.FAMILY_ID / "episodes"
        ),
    )
    if not reconciliation["next_slot_selection_allowed_in_this_call"]:
        raise base.FormalExecutionError(
            "BLOCKED_RECONCILIATION_PERFORMED_NO_NEXT_SLOT_SELECTION"
        )
    ledger = base._load(base.LEDGER_PATH)
    if any(_state(record) == "RUNNING" for record in ledger["attempt_records"]):
        raise base.FormalExecutionError("BLOCKED_RUNNING_ATTEMPT_AFTER_RECONCILIATION")
    slot39 = _verify_slot39_permanent_boundary(ledger)
    other_contract_defects = [
        str(record["attempt_id"])
        for record in ledger["attempt_records"]
        if _state(record) == "BLOCKED_CONTRACT_DEFECT"
        and record.get("attempt_id") != SLOT39_ATTEMPT_ID
    ]
    if other_contract_defects:
        raise base.FormalExecutionError(
            "BLOCKED_NEW_CONTRACT_DEFECT_REQUIRES_APPEND_ONLY_REVIEW"
        )
    repair = _verify_pipeline_repair_record(ledger)
    primary_ids = {
        record["episode_id"]
        for record in ledger["attempt_records"]
        if str(record["attempt_id"]).endswith("-A01")
    }
    next_row = next(
        (row for row in base.train_rows() if row["episode_id"] not in primary_ids),
        None,
    )
    if next_row is None:
        raise base.FormalExecutionError("BLOCKED_NO_UNSTARTED_PRIMARY_AFTER_REPAIR")
    authorization = None
    authorization_binding = None
    if require_resume_authorization:
        authorization_binding = _load_and_verify_campaign_authorization(
            authorization_path=authorization_path,
            authorization_sha256=authorization_sha256,
            repair=repair,
        )
        authorization = authorization_binding["authorization"]
        if not (
            int(authorization["first_primary_slot"])
            <= int(next_row["split_slot_index"])
            <= int(authorization["last_primary_slot"])
        ):
            raise base.FormalExecutionError(
                "BLOCKED_NEXT_PRIMARY_OUTSIDE_CAMPAIGN_AUTHORIZATION_RANGE"
            )
        if int(next_row["split_slot_index"]) == 40:
            if authorization.get("entry_ledger_sha") != base._sha(base.LEDGER_PATH):
                raise base.FormalExecutionError(
                    "BLOCKED_CAMPAIGN_AUTHORIZATION_ENTRY_LEDGER_HASH_MISMATCH"
                )
        else:
            authorization_sha = authorization_binding["sha256"]
            resumed = [
                record
                for record in ledger["attempt_records"]
                if int(record.get("split_slot_index", -1)) >= 40
                and int(record.get("split_slot_index", -1))
                < int(next_row["split_slot_index"])
            ]
            if not resumed or any(
                _state(record) != "COMPLETED_RECORDED"
                or record.get("resume_authorization_sha256") != authorization_sha
                or record.get("resume_authorization_path")
                != str(CANONICAL_CAMPAIGN_AUTHORIZATION_PATH)
                or not record.get("lifecycle_journal_path")
                or evidence_pipeline.AttemptLifecycleJournal.open(
                    base.ROOT / str(record["lifecycle_journal_path"])
                ).current_stage
                != "COMPLETE"
                for record in resumed
            ):
                raise base.FormalExecutionError(
                    "BLOCKED_RESUMED_PREFIX_NOT_COMPLETE_OR_AUTHORIZATION_UNLINKED"
                )
            first_resumed = min(
                resumed, key=lambda record: int(record["split_slot_index"])
            )
            if first_resumed.get("events", [{}])[0].get(
                "prior_ledger_sha256"
            ) != authorization.get("entry_ledger_sha"):
                raise base.FormalExecutionError(
                    "BLOCKED_RESUMED_PREFIX_ENTRY_LEDGER_NOT_AUTHORIZATION_BOUND"
                )
    elif int(next_row["split_slot_index"]) != 40:
        raise base.FormalExecutionError("BLOCKED_READINESS_AUDIT_NEXT_PRIMARY_NOT_SLOT40")
    return {
        "status": (
            "PASS_EXPLICIT_SLOT40_RESUME_GATE"
            if require_resume_authorization
            else "PASS_PRE_SLOT40_INFRASTRUCTURE_GATE_AUTHORIZATION_STILL_REQUIRED"
        ),
        "reconciliation": reconciliation,
        "slot39": slot39,
        "repair": repair,
        "next_primary_identity": {
            key: next_row[key]
            for key in (
                "episode_id",
                "split_slot_index",
                "scenario_id",
                "seed",
                "method_id",
                "runtime_fixture_id",
            )
        },
        "resume_authorization": authorization,
        "resume_authorization_path": (
            str(authorization_binding["path"])
            if authorization_binding is not None
            else None
        ),
        "resume_authorization_sha256": (
            authorization_binding["sha256"]
            if authorization_binding is not None
            else None
        ),
        "slot40_started": any(
            int(record.get("split_slot_index", -1)) >= 40
            for record in ledger["attempt_records"]
        ),
    }


def _execute(
    row: Mapping[str, Any],
    *,
    attempt_id: str,
    linked: bool,
    resume_authorization_path: Path | None = None,
    resume_authorization_sha256: str | None = None,
    backend: stage6b_backend.UnifiedNativeBackend | None = None,
) -> Mapping[str, Any]:
    ledger = base._load(base.LEDGER_PATH)
    output = _attempt_output(row, attempt_id, linked)
    if output.exists() and any(output.iterdir()):
        raise base.FormalExecutionError("BLOCKED_FRESH_ATTEMPT_ARTIFACT_DIRECTORY_NOT_EMPTY")
    resources = base.resource_snapshot()
    if resources["status"] != "PASS" or os.environ.get("DISPLAY") != ":1":
        raise base.FormalExecutionError("BLOCKED_PRE_ATTEMPT_RESOURCE_GATE")
    attempt_identity = {
        "attempt_id": attempt_id,
        "episode_id": row["episode_id"],
        "split": "train",
        "global_slot_index": row["slot_index"],
        "split_slot_index": row["split_slot_index"],
        "scenario_id": row["scenario_id"],
        "seed": row["seed"],
        "method_id": row["method_id"],
        "runtime_fixture_id": row["runtime_fixture_id"],
        "method_freeze_sha256": base.METHOD_FREEZE_SHA256,
        "paper_protocol_sha256": base.PAPER_PROTOCOL_SHA256,
        "evaluation_fix_sha256": base.EVALUATION_FIX_SHA256,
    }
    journal = evidence_pipeline.AttemptLifecycleJournal.create(
        output / evidence_pipeline.JOURNAL_FILENAME, attempt_identity
    )
    started_event = {
        "event": "ATTEMPT_STARTED",
        "state": "RUNNING",
        "timestamp_utc": base._now(),
        "entry_amendment_sha256": base._sha(base.ENTRY_AMENDMENT_PATH),
        "linked_retry_preflight_sha256": base._sha(PREFLIGHT_PATH) if linked else None,
        "method_freeze_sha256": base.METHOD_FREEZE_SHA256,
        "paper_protocol_sha256": base.PAPER_PROTOCOL_SHA256,
        "evaluation_fix_sha256": base.EVALUATION_FIX_SHA256,
        "historical_e3_hashes": base.HISTORICAL_HASHES,
        "prior_ledger_sha256": base._sha(base.LEDGER_PATH),
        "resource_preflight": resources,
        "lifecycle_journal_path": str(
            (output / evidence_pipeline.JOURNAL_FILENAME).relative_to(base.ROOT)
        ),
        "resume_authorization_path": (
            str(resume_authorization_path) if not linked else None
        ),
        "resume_authorization_sha256": (
            resume_authorization_sha256 if not linked else None
        ),
    }
    attempt = {
        "attempt_id": attempt_id,
        "episode_id": row["episode_id"],
        "split": "train",
        "global_slot_index": row["slot_index"],
        "split_slot_index": row["split_slot_index"],
        "scenario_id": row["scenario_id"],
        "seed": row["seed"],
        "method_id": row["method_id"],
        "runtime_fixture_id": row["runtime_fixture_id"],
        "automatic_retry_count": 0,
        "scientific_retry_count": 0,
        "engineering_rerun_count": 1 if linked else 0,
        "linked_to_attempt_id": A02_EPISODE_ID + "-A01" if linked else None,
        "artifact_dir": str(output.relative_to(base.ROOT)),
        "lifecycle_journal_path": str(
            (output / evidence_pipeline.JOURNAL_FILENAME).relative_to(base.ROOT)
        ),
        "resume_authorization_sha256": started_event[
            "resume_authorization_sha256"
        ],
        "resume_authorization_path": started_event[
            "resume_authorization_path"
        ],
        "events": [started_event],
    }
    journal.advance(
        "LEDGER_RUNNING_COMMIT_STARTED",
        {"prior_ledger_sha256": base._sha(base.LEDGER_PATH)},
    )
    ledger.setdefault("attempt_records", []).append(attempt)
    _save(ledger)
    started_ledger_sha256 = base._sha(base.LEDGER_PATH)
    journal.advance(
        "LEDGER_RUNNING_COMMITTED",
        {"started_ledger_sha256": started_ledger_sha256},
    )
    runner = backend or stage6b_backend.UnifiedNativeBackend()
    terminal_state = "ENGINEERING_INVALID"
    blocker = None
    backend_receipt: Mapping[str, Any] = {}
    metric: Mapping[str, Any] | None = None
    try:
        if isinstance(runner, stage6b_backend.UnifiedNativeBackend):
            backend_receipt = runner.run(
                base._fresh_spec(row),
                output,
                visualization=False,
                lifecycle_journal=journal,
            )
        else:
            backend_receipt = runner.run(
                base._fresh_spec(row), output, visualization=False
            )
        metric = base._metric_receipt(row, output, backend_receipt)
        journal.advance("METRIC_RECEIPT_WRITE_STARTED")
        base._atomic_json(output / "FRESH_FORMAL_METRIC_RECEIPT.json", metric)
        journal.advance(
            "METRIC_RECEIPT_SERIALIZED",
            {
                "sha256": base._sha(output / "FRESH_FORMAL_METRIC_RECEIPT.json"),
                "status": metric["status"],
            },
        )
        if metric["status"] == "PASS_COMPLETED_RECORDED_EVIDENCE_CHAIN":
            terminal_state = "COMPLETED_RECORDED"
        elif metric["status"] == "ENGINEERING_INVALID_ENVIRONMENT":
            blocker = str(backend_receipt.get("termination_reason") or backend_receipt.get("status"))
        else:
            terminal_state = "BLOCKED_CONTRACT_DEFECT"
            blocker = str(metric["status"])
    except BaseException as exc:
        blocker = type(exc).__name__ + ":" + str(exc)
    post_resources = base.resource_snapshot()
    base._atomic_json(output / "ORCHESTRATOR_POST_CLEANUP.json", post_resources)
    if post_resources["status"] != "PASS":
        terminal_state = "ENGINEERING_INVALID"
        blocker = "POST_ATTEMPT_RESOURCE_CLEANUP_FAILED"
    disposition = None
    if terminal_state != "COMPLETED_RECORDED":
        disposition = evidence_pipeline.write_fail_closed_disposition(
            output_dir=output,
            attempt=attempt,
            journal=journal,
            cleanup=post_resources,
        )
        terminal_state = str(disposition["terminal_state"])
        journal.advance(
            "ATTEMPT_DISPOSITION_SERIALIZED",
            {
                "sha256": base._sha(
                    output / evidence_pipeline.DISPOSITION_FILENAME
                ),
                "terminal_state": terminal_state,
                "scientific_outcome": "UNKNOWN",
            },
        )
    attempt["events"].append(
        {
            "event": "ATTEMPT_TERMINATED",
            "state": terminal_state,
            "timestamp_utc": base._now(),
            "blocker": blocker,
            "backend_receipt_status": backend_receipt.get("status"),
            "backend_evaluator_return_code": backend_receipt.get("evaluator_return_code"),
            "cleanup_status": post_resources["status"],
            "started_ledger_sha256": started_ledger_sha256,
            "metric_receipt_sha256": base._sha(output / "FRESH_FORMAL_METRIC_RECEIPT.json") if (output / "FRESH_FORMAL_METRIC_RECEIPT.json").is_file() else None,
            "post_cleanup_receipt_sha256": base._sha(output / "ORCHESTRATOR_POST_CLEANUP.json"),
            "episode_receipt_path": str((output / "EPISODE_RECEIPT.json").relative_to(base.ROOT)) if (output / "EPISODE_RECEIPT.json").is_file() else None,
            "metric_receipt_path": str((output / "FRESH_FORMAL_METRIC_RECEIPT.json").relative_to(base.ROOT)) if (output / "FRESH_FORMAL_METRIC_RECEIPT.json").is_file() else None,
            "artifact_hashes": _stable_artifact_hashes(output),
            "scientific_outcome": (
                "UNKNOWN" if disposition is not None else "AVAILABLE_IN_COMPLETE_CHAIN"
            ),
            "rerun_eligible": False if disposition is not None else None,
            "replacement_eligible": False if disposition is not None else None,
        }
    )
    journal.advance(
        "TERMINAL_LEDGER_COMMIT_STARTED",
        {"terminal_state": terminal_state},
    )
    _save(ledger)
    journal.advance(
        "TERMINAL_LEDGER_COMMITTED",
        {"ledger_sha256": base._sha(base.LEDGER_PATH)},
    )
    journal.advance(
        "COMPLETE",
        {
            "terminal_state": terminal_state,
            "next_slot_started": False,
            "automatic_retry_started": False,
        },
    )
    if terminal_state == "COMPLETED_RECORDED" and int(row["split_slot_index"]) % len(base.METHOD_ORDER) == 0:
        base._block_checkpoint(ledger, row)
    result = {
        "schema_version": "driveclarify.method_v1_r3.formal_train.linked_aware_run_one.v1",
        "status": "PASS_ONE_FRESH_TRAIN_EPISODE_COMPLETED_RECORDED" if terminal_state == "COMPLETED_RECORDED" else derive_linked_ledger(ledger)["status"],
        "attempt_id": attempt_id,
        "episode_id": row["episode_id"],
        "split_slot_index": row["split_slot_index"],
        "scenario_id": row["scenario_id"],
        "seed": row["seed"],
        "method_id": row["method_id"],
        "linked_engineering_retry": linked,
        "terminal_state": terminal_state,
        "blocker": blocker,
        "ledger_sha256": base._sha(base.LEDGER_PATH),
        "formal_train_counts": base._load(base.LEDGER_PATH)["counts"],
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "training_jobs": 0,
        "a800_jobs": 0,
        "generated_at_utc": base._now(),
    }
    if linked:
        base._atomic_json(EXECUTION_RECEIPT_PATH, result)
        records = [record for record in ledger["attempt_records"] if record["episode_id"] == A02_EPISODE_ID]
        base._atomic_json(
            HISTORY_PATH,
            {
                "schema_version": "driveclarify.method_v1_r3.formal_train.slot19_attempt_history.v1",
                "status": "PASS_SLOT19_HISTORY_APPEND_ONLY_RECONSTRUCTABLE",
                "episode_id": A02_EPISODE_ID,
                "attempts": [
                    {"attempt_id": record["attempt_id"], "state": _state(record), "artifact_dir": record["artifact_dir"]}
                    for record in records
                ],
                "a01_preserved": records[0]["attempt_id"] == A02_EPISODE_ID + "-A01" and _state(records[0]) == "ENGINEERING_INVALID",
                "a03_authorized": False,
                "generated_at_utc": base._now(),
            },
        )
    else:
        base._atomic_json(base.REPORT_ROOT / "latest_run_one_receipt.json", result)
    if terminal_state != "COMPLETED_RECORDED":
        raise base.FormalExecutionError(result["status"] + ":" + str(blocker))
    return result


def run_a02(*, backend: stage6b_backend.UnifiedNativeBackend | None = None) -> Mapping[str, Any]:
    verify_a02_preflight(require_prior_ledger=True)
    ledger = base._load(base.LEDGER_PATH)
    if any(record["attempt_id"] == A02_ATTEMPT_ID for record in ledger["attempt_records"]):
        raise base.FormalExecutionError("BLOCKED_SLOT19_A02_ALREADY_EXISTS")
    return _execute(base.train_rows()[18], attempt_id=A02_ATTEMPT_ID, linked=True, backend=backend)


def run_next(
    *,
    authorization_path: Path | str,
    authorization_sha256: str,
    backend: stage6b_backend.UnifiedNativeBackend | None = None,
) -> Mapping[str, Any]:
    base._verify_amendment()
    base.validate_entry(require_initial_ledger=False)
    gate = pre_slot_gate(
        require_resume_authorization=True,
        authorization_path=authorization_path,
        authorization_sha256=authorization_sha256,
    )
    ledger = derive_linked_ledger(base._load(base.LEDGER_PATH))
    slot19_a02 = next(
        (record for record in ledger["attempt_records"] if record["attempt_id"] == A02_ATTEMPT_ID), None
    )
    if slot19_a02 is None or _state(slot19_a02) != "COMPLETED_RECORDED":
        raise base.FormalExecutionError("BLOCKED_SLOT19_A02_NOT_COMPLETED")
    primary_episode_ids = {
        record["episode_id"]
        for record in ledger["attempt_records"]
        if str(record["attempt_id"]).endswith("-A01")
    }
    row = next((item for item in base.train_rows() if item["episode_id"] not in primary_episode_ids), None)
    if row is None:
        return {"status": "FORMAL_TRAIN_NO_UNSTARTED_PRIMARY_EPISODES", "ledger": ledger}
    return _execute(
        row,
        attempt_id=str(row["episode_id"]) + "-A01",
        linked=False,
        resume_authorization_path=Path(str(gate["resume_authorization_path"])),
        resume_authorization_sha256=str(gate["resume_authorization_sha256"]),
        backend=backend,
    )


def status() -> Mapping[str, Any]:
    ledger = derive_linked_ledger(base._load(base.LEDGER_PATH))
    primary_episode_ids = {
        record["episode_id"] for record in ledger["attempt_records"] if str(record["attempt_id"]).endswith("-A01")
    }
    return {
        "schema_version": "driveclarify.method_v1_r3.formal_train.linked_aware_status.v1",
        "status": ledger["status"],
        "counts": ledger["counts"],
        "split_state": ledger["split_state"],
        "next_primary_episode": next((row for row in base.train_rows() if row["episode_id"] not in primary_episode_ids), None),
    }


def closeout() -> Mapping[str, Any]:
    ledger = derive_linked_ledger(base._load(base.LEDGER_PATH))
    if ledger["counts"]["scientifically_completed"] != 256:
        raise base.FormalExecutionError("BLOCKED_TRAIN_CLOSEOUT_REQUIRES_256_SCIENTIFICALLY_COMPLETED_SLOTS")
    groups = _episode_groups(ledger["attempt_records"])
    selected = []
    for row in base.train_rows():
        completions = [record for record in groups[row["episode_id"]] if _state(record) == "COMPLETED_RECORDED"]
        if len(completions) != 1:
            raise base.FormalExecutionError("BLOCKED_TRAIN_CLOSEOUT_NONUNIQUE_SCIENTIFIC_COMPLETION")
        selected.append(completions[0])
    results = [base._load(base.ROOT / record["artifact_dir"] / "EPISODE_RESULT.json") for record in selected]
    metrics = [base._load(base.ROOT / record["artifact_dir"] / "FRESH_FORMAL_METRIC_RECEIPT.json") for record in selected]
    unknown, available = Counter(), Counter()
    for result in results:
        for family in ("task_metrics", "safety_metrics", "interaction_metrics", "compute_metrics"):
            for name, value in result.get(family, {}).items():
                (available if value.get("status") == "AVAILABLE" else unknown)[family + "." + name] += 1
    per_method = {
        method: len([record for record in selected if record["method_id"] == method])
        for method in base.METHOD_ORDER
    }
    per_scenario = dict(Counter(record["scenario_id"] for record in selected))
    failure_taxonomy = dict(Counter(str(result.get("failure_class")) for result in results))
    gate_statuses = Counter(
        gate["status"] for metric in metrics for gate in metric["evidence_chain"]["reducer"].values()
    )
    durations = [
        float(base._load(base.ROOT / record["events"][-1]["episode_receipt_path"])["duration_wall_seconds"])
        for record in selected
    ]
    receipt = {
        "schema_version": "driveclarify.method_v1_r3.formal_train.linked_aware_closeout.v1",
        "status": "PASS_FRESH_FORMAL_TRAIN_256_SCIENTIFICALLY_COMPLETED_WITH_ONE_LINKED_INFRASTRUCTURE_RETRY",
        "scheduled_train": 256,
        "primary_started": ledger["counts"]["primary_started"],
        "linked_retries": ledger["counts"]["linked_retries"],
        "total_attempts": ledger["counts"]["started"],
        "scientifically_completed": ledger["counts"]["scientifically_completed"],
        "engineering_invalid_attempts": ledger["counts"]["engineering_invalid"],
        "contract_invalid": ledger["counts"]["blocked_contract_defect"],
        "remaining_train": 0,
        "per_method_counts": per_method,
        "per_scenario_counts": per_scenario,
        "failure_taxonomy": failure_taxonomy,
        "available_metric_counts": dict(available),
        "unknown_or_missing_metric_counts": dict(unknown),
        "formal_evidence_gate_status_counts": dict(gate_statuses),
        "compute": {"episode_wall_seconds_sum": sum(durations), "episode_wall_seconds_mean": fmean(durations), "episode_wall_seconds_max": max(durations)},
        "cleanup": base.resource_snapshot(),
        "protected_integrity": base._protected_integrity(),
        "method_integrity": base._method_integrity(),
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "training_jobs": 0,
        "a800_jobs": 0,
        "generated_at_utc": base._now(),
    }
    base._atomic_json(base.REPORT_ROOT / "FRESH_FORMAL_TRAIN_CLOSEOUT_RECEIPT_R2.json", receipt)
    return receipt
