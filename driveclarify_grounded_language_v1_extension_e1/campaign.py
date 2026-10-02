"""Sequential cold-boot E0/E1/E2/E3 campaign controller."""

from __future__ import annotations

import datetime as dt
import json
import os
import traceback
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .aggregate import aggregate
from .backend import artifact_directory, execute_episode
from .contracts import (
    EXTENSION_METHODS,
    LEDGER_PATH,
    PROTECTED_HASHES,
    PROTECTED_PATHS,
    REPORT_ROOT,
    ExtensionContractError,
    assert_train_only,
    file_sha256,
)
from .evaluation import evaluate_episode


ROOT = Path(__file__).resolve().parents[1]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(tmp), str(path))


def verify_protected_integrity() -> Mapping[str, str]:
    actual = {}
    for key, expected in PROTECTED_HASHES.items():
        path = ROOT / PROTECTED_PATHS[key]
        observed = file_sha256(path)
        actual[key] = observed
        if observed != expected:
            raise ExtensionContractError("PROTECTED_FROZEN_ASSET_CHANGED:" + key)
    return actual


def _ledger() -> dict[str, Any]:
    value = _load(ROOT / LEDGER_PATH)
    if value.get("split") != "train" or len(value.get("rows", [])) != 216:
        raise ExtensionContractError("EXTENSION_LEDGER_CONTRACT_BROKEN")
    for row in value["rows"]:
        assert_train_only(str(row["split"]))
        if row["method_id"] not in EXTENSION_METHODS:
            raise ExtensionContractError("EXTENSION_LEDGER_UNKNOWN_METHOD")
    return value


def _save_ledger(value: Mapping[str, Any]) -> None:
    _write(ROOT / LEDGER_PATH, value)


def _selected(rows: Iterable[Mapping[str, Any]], keys: set[tuple[str, int, str]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if (row["scenario_id"], int(row["seed_index"]), row["method_id"]) in keys]


def smoke_keys() -> set[tuple[str, int, str]]:
    return {
        (scenario, 1, method)
        for scenario in ("DC-GLV1-E1-ACT-001", "DC-GLV1-E1-ASK-001", "DC-GLV1-E1-WAIT-001")
        for method in EXTENSION_METHODS
    }


def coverage_keys() -> set[tuple[str, int, str]]:
    return {
        ("DC-GLV1-E1-{}-{:03d}".format(mechanism, index), 1, "driveclarify_grounded_v1")
        for mechanism in ("ACT", "ASK", "WAIT")
        for index in (1, 2, 3, 4)
    }


def run_rows(
    rows: Iterable[Mapping[str, Any]], *, stage: str, wall_timeout_seconds: float = 180.0
) -> Mapping[str, Any]:
    ledger = _ledger()
    wanted = {row["episode_id"] for row in rows}
    attempted = completed = failed = skipped = 0
    representative = {
        "DC-GLV1-E1-ACT-001",
        "DC-GLV1-E1-ACT-002",
        "DC-GLV1-E1-ASK-001",
        "DC-GLV1-E1-ASK-002",
        "DC-GLV1-E1-WAIT-001",
        "DC-GLV1-E1-WAIT-002",
    }
    for index, slot in enumerate(ledger["rows"]):
        if slot["episode_id"] not in wanted:
            continue
        if slot["status"] in {"COMPLETED_RECORDED", "FAILED_RECORDED"}:
            skipped += 1
            continue
        verify_protected_integrity()
        assert_train_only(str(slot["split"]))
        visualization = (
            slot["method_id"] == "driveclarify_grounded_v1"
            and slot["scenario_id"] in representative
            and (
                slot["seed_index"] == 1
                or (
                    slot["seed_index"] == 2
                    and slot["scenario_id"].endswith("-002")
                )
            )
        )
        out = artifact_directory(
            scenario_id=slot["scenario_id"],
            seed_index=int(slot["seed_index"]),
            method_id=slot["method_id"],
        )
        slot["status"] = "RUNNING"
        slot["attempt_count"] = int(slot["attempt_count"]) + 1
        slot["started_at_utc"] = _now()
        slot["artifact_dir"] = str(out.relative_to(ROOT))
        _save_ledger(ledger)
        attempted += 1
        try:
            execute_episode(
                scenario_id=slot["scenario_id"],
                seed_index=int(slot["seed_index"]),
                method_id=slot["method_id"],
                episode_id=slot["episode_id"],
                visualization=visualization,
                capture_desktop=visualization,
                wall_timeout_seconds=wall_timeout_seconds,
            )
            normalized = evaluate_episode(
                scenario_id=slot["scenario_id"],
                seed_index=int(slot["seed_index"]),
                method_id=slot["method_id"],
                episode_id=slot["episode_id"],
            )
            slot["status"] = "COMPLETED_RECORDED"
            slot["episode_result_sha256"] = normalized["result_sha256"]
            slot["failure_reason"] = None
            completed += 1
        except Exception as exc:  # one attempt only; preserve failure evidence
            slot["status"] = "FAILED_RECORDED"
            slot["failure_reason"] = type(exc).__name__ + ":" + str(exc)
            out.mkdir(parents=True, exist_ok=True)
            _write(
                out / "EXTENSION_EXECUTION_FAILURE.json",
                {
                    "schema_version": "driveclarify.grounded_language_v1_extension_e1.failure.v1",
                    "stage": stage,
                    "identity": {key: slot[key] for key in ("episode_id", "scenario_id", "seed_index", "method_id", "split")},
                    "error": slot["failure_reason"],
                    "traceback": traceback.format_exc(),
                    "automatic_retry_count": 0,
                    "recorded_at_utc": _now(),
                },
            )
            failed += 1
        slot["ended_at_utc"] = _now()
        _save_ledger(ledger)
        aggregate()
    result = aggregate()
    stage_natural = Counter()
    selected_accounted = 0
    for ledger_row in ledger["rows"]:
        if ledger_row["episode_id"] not in wanted or ledger_row["status"] != "COMPLETED_RECORDED":
            continue
        selected_accounted += 1
        result_path = ROOT / str(ledger_row["artifact_dir"]) / "EXTENSION_EPISODE_RESULT.json"
        if not result_path.is_file():
            continue
        episode_result = _load(result_path)
        observed = episode_result.get("decision", {}).get("observed_normalized")
        if (
            ledger_row["method_id"] == "driveclarify_grounded_v1"
            and episode_result.get("runtime_lifecycle_complete") is True
            and not episode_result.get("decision", {}).get("forced")
            and observed in {"ACT", "ASK", "WAIT"}
        ):
            stage_natural[str(observed)] += 1
    if stage == "E1":
        stage_status = (
            "PASS_E1_18_EPISODE_SMOKE_BACKENDS_AVAILABLE_SCIENTIFIC_FAILURES_RECORDED"
            if selected_accounted == 18
            else "BLOCKED_E1_SMOKE_BACKEND_OR_EVALUATOR_UNAVAILABLE"
        )
    elif stage == "E2":
        stage_status = (
            "PASS_E2_NATURAL_ACT_ASK_WAIT_COVERAGE"
            if all(stage_natural[name] >= 3 for name in ("ACT", "ASK", "WAIT"))
            else "BLOCKED_E2_NATURAL_COVERAGE"
        )
    else:
        stage_status = result["status"]
    stage_receipt = {
        "schema_version": "driveclarify.grounded_language_v1_extension_e1.stage_receipt.v1",
        "stage": stage,
        "status": stage_status,
        "attempted_this_call": attempted,
        "completed_this_call": completed,
        "failed_this_call": failed,
        "already_recorded_skipped": skipped,
        "ledger_completed": result["completed"],
        "ledger_failed": result["failed_recorded"],
        "grounded_natural_decision_counts": {
            name: stage_natural[name] for name in ("ACT", "ASK", "WAIT")
        },
        "grounded_e2_coverage_pass": all(stage_natural[name] >= 3 for name in ("ACT", "ASK", "WAIT")),
        "extension_dev_attempt_count": 0,
        "extension_test_attempt_count": 0,
        "extension_test_consumed": False,
        "finished_at_utc": _now(),
    }
    _write(ROOT / REPORT_ROOT / ("EXTENSION_{}_RECEIPT.json".format(stage.upper())), stage_receipt)
    return stage_receipt


def run_stage(stage: str, *, wall_timeout_seconds: float = 180.0) -> Mapping[str, Any]:
    verify_protected_integrity()
    ledger = _ledger()
    if stage == "e1":
        return run_rows(_selected(ledger["rows"], smoke_keys()), stage="E1", wall_timeout_seconds=wall_timeout_seconds)
    if stage == "e2":
        # E1 members are skipped if already recorded.  No synthetic or forced
        # decision is ever inserted to satisfy this gate.
        return run_rows(_selected(ledger["rows"], coverage_keys()), stage="E2", wall_timeout_seconds=wall_timeout_seconds)
    if stage == "train":
        return run_rows(ledger["rows"], stage="E3", wall_timeout_seconds=wall_timeout_seconds)
    if stage == "finalize":
        return aggregate()
    raise ExtensionContractError("UNKNOWN_EXTENSION_STAGE:" + stage)


__all__ = ["coverage_keys", "run_stage", "smoke_keys", "verify_protected_integrity"]
