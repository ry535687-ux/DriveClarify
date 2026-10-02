"""Fresh, TRAIN-only execution orchestration for frozen DriveClarify Method V1 R3.

This module is deliberately outside the 64-file Method freeze.  It materializes
the preregistered schedule, maintains a monotonic attempt ledger, delegates one
native episode to the already-reviewed Stage6B backend, and fail-closes evidence
before accepting an episode.  It never opens a DEV/TEST runtime fixture or gold
payload and never retries a slot.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import random
import socket
import subprocess
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

from driveclarify_paper_mvp_stage6b import backend as stage6b_backend
from driveclarify_paper_mvp_stage6b.campaign import _formal_evidence_gates


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "driveclarify_method_v1_r3_formal_train_execution"
TOOL_PATH = ROOT / "tools/run_method_v1_r3_fresh_formal_train.py"
TEST_PATH = ROOT / "tests/method_v1_r3_formal_train_execution/test_orchestrator.py"
PREPARATION_ROOT = ROOT / (
    "reports/driveclarify_method_v1_r3_native_freeze_protocol_and_formal_train_preparation"
)
FREEZE_ROOT = PREPARATION_ROOT / "02_METHOD_FINAL_FREEZE_R3"
PROTOCOL_ROOT = PREPARATION_ROOT / "03_PAPER_EXPERIMENT_PROTOCOL"
FIX_ROOT = PREPARATION_ROOT / "04_E3_UNKNOWN_PRESERVATION_FIX"
PREP_ROOT = PREPARATION_ROOT / "05_FRESH_FORMAL_TRAIN_PREPARATION"
REPORT_ROOT = ROOT / "reports/driveclarify_method_v1_r3_fresh_formal_train_execution"
ARTIFACT_ROOT = ROOT / "artifacts/driveclarify_method_v1_r3_fresh_formal_train_execution"
LEDGER_PATH = PREP_ROOT / "FRESH_FORMAL_TRAIN_LEDGER.json"
FILESET_PATH = FREEZE_ROOT / "METHOD_V1_FINAL_PRODUCTION_FILESET_R3.json"
SCENARIO_PROTOCOL_PATH = PROTOCOL_ROOT / "PAPER_SCENARIO_SEED_PROTOCOL_R3.json"
ROSTER_PATH = PROTOCOL_ROOT / "PAPER_METHOD_ROSTER_R3.json"
ENTRY_AMENDMENT_PATH = REPORT_ROOT / "EXECUTION_ENTRY_AMENDMENT.json"
TRAIN_SCHEDULE_PATH = REPORT_ROOT / "FRESH_TRAIN_SCHEDULE.json"
TRAIN_HASH_VIEW_PATH = REPORT_ROOT / "FRESH_TRAIN_SCHEDULE_HASH_VIEW.json"
TRAIN_BINDINGS_PATH = REPORT_ROOT / "FRESH_TRAIN_EXECUTION_BINDINGS.json"

FAMILY_ID = "DC-MV1-R3-FORMAL-TRAIN-20260815"
METHOD_FREEZE_SHA256 = "35ddfa888ea7c7043a1a50d517cc70be3d0ee1ef951179468b3b2cc71174c0dd"
PAPER_PROTOCOL_SHA256 = "ca8271a18acf24e172e0b1766cb85e034f1278774a9f199e6f9f66d3d766c630"
EVALUATION_FIX_SHA256 = "88e1d9ef7610a78e3d4c287421bb4d709cb5d29d4203249b9ea14a41b5f67a5b"
FULL_SCHEDULE_SHA256 = "202fa2ae316e99e5a7876e7abd94886eeccc1b2320a9608268d09f4a0fc6d430"
TRAIN_SCHEDULE_SHA256 = "e50f6980a73455516a044ddf0cc3ed03b77941b2389f43b5e8d48796031f9e9f"
INITIAL_LEDGER_SHA256 = "079f29212cda3af71e80177d556b12ddd7d903c2af59cdb3a7f560f845730d68"
SCENARIO_MANIFEST_SHA256 = "bcad5208efc3c13d6844ba2367e07236d5121949bc23acbf3014ba504fc5c1e5"
SIMLINGO_HEAD = "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
SIMLINGO_DIFF_SHA256 = "dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058"
CHECKPOINT_SHA256 = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
DINO_SHA256 = "1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3"
HISTORICAL_HASHES = {
    "ledger": "9a7bba939f6865247674260dacd9c92d61561d691b3ae3ad0fb43d3b062fcc14",
    "final_receipt": "bd546a75126b782b547ea5633ab10272f29ade884b8fe053ffdcd6cae880f798",
    "artifact_manifest": "0e252efd2f1b9bc1bd9ae822d87da853fbaa1faabebc334d891e8ccbd0b99eeb",
}
METHOD_ORDER = (
    "original_simlingo",
    "driveclarify",
    "always_ask",
    "always_stop",
    "always_wait",
    "never_ask",
    "language_only_uncertainty",
    "risk_only",
)


class FormalExecutionError(RuntimeError):
    """Fail-closed formal execution error."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_json(path: Path, value: Any, *, canonical: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    payload = (
        _canonical_bytes(value)
        if canonical
        else (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            + "\n"
        ).encode("utf-8")
    )
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payload_sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _command(argv: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _git_head(root: Path) -> str:
    result = _command(("git", "rev-parse", "HEAD"), cwd=root)
    return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"


def _git_branch(root: Path) -> str:
    result = _command(("git", "branch", "--show-current"), cwd=root)
    return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"


def _git_diff_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--binary"],
        cwd=str(root),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def _method_integrity() -> Mapping[str, Any]:
    fileset = _load(FILESET_PATH)
    mismatches = []
    lines = []
    for record in sorted(fileset["files"], key=lambda item: str(item["path"])):
        path = Path(str(record["path"]))
        if not path.is_absolute():
            path = ROOT / path
        observed = _sha(path) if path.is_file() else None
        if observed != record["sha256"]:
            mismatches.append(
                {"path": str(record["path"]), "expected": record["sha256"], "observed": observed}
            )
        if observed is not None:
            lines.append("{}  {}\n".format(observed, record["path"]))
    aggregate = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return {
        "file_count": len(fileset["files"]),
        "aggregate_sha256": aggregate,
        "expected_aggregate_sha256": METHOD_FREEZE_SHA256,
        "mismatches": mismatches,
        "pass": len(fileset["files"]) == 64 and not mismatches and aggregate == METHOD_FREEZE_SHA256,
    }


def _paper_integrity() -> Mapping[str, Any]:
    manifest_path = PROTOCOL_ROOT / "PAPER_EXPERIMENT_PROTOCOL_HASHES_R3.json"
    manifest = _load(manifest_path)
    mismatches = []
    lines = []
    for name, expected in sorted(manifest["files"].items()):
        observed = _sha(PROTOCOL_ROOT / name)
        if observed != expected:
            mismatches.append({"file": name, "expected": expected, "observed": observed})
        lines.append("{}  {}\n".format(observed, name))
    aggregate = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return {
        "aggregate_sha256": aggregate,
        "expected_aggregate_sha256": PAPER_PROTOCOL_SHA256,
        "mismatches": mismatches,
        "pass": not mismatches and aggregate == PAPER_PROTOCOL_SHA256,
    }


def _evaluation_fix_integrity() -> Mapping[str, Any]:
    paths = (
        ROOT / "driveclarify_grounded_language_v1_extension_e1_r1_e3/campaign.py",
        ROOT / "driveclarify_paper_mvp_stage6b/campaign.py",
    )
    lines = ["{}  {}\n".format(_sha(path), path.relative_to(ROOT)) for path in sorted(paths)]
    aggregate = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return {
        "aggregate_sha256": aggregate,
        "expected_aggregate_sha256": EVALUATION_FIX_SHA256,
        "files": {str(path.relative_to(ROOT)): _sha(path) for path in paths},
        "pass": aggregate == EVALUATION_FIX_SHA256,
    }


def _protected_integrity() -> Mapping[str, Any]:
    historical_root = ROOT / "reports/grounded_language_v1_extension_e1_r1_e3"
    observed = {
        "repository_branch": _git_branch(ROOT),
        "repository_head": _git_head(ROOT),
        "simlingo_head": _git_head(stage6b_backend.SIMLINGO_ROOT),
        "simlingo_diff_sha256": _git_diff_sha(stage6b_backend.SIMLINGO_ROOT),
        "checkpoint_sha256": _sha(stage6b_backend.CHECKPOINT),
        "grounding_dino_sha256": _sha(ROOT / "pretrained/grounding-dino-tiny/model.safetensors"),
        "scenario_manifest_sha256": _sha(stage6b_backend.SCENARIO_MANIFEST_PATH),
        "historical_e3_ledger_sha256": _sha(historical_root / "E3_FORMAL_TRAIN_LEDGER.json"),
        "historical_e3_final_receipt_sha256": _sha(historical_root / "E3_FINAL_RECEIPT.json"),
        "historical_e3_artifact_manifest_sha256": _sha(historical_root / "ARTIFACT_HASHES.json"),
    }
    expected = {
        "repository_branch": "master",
        "repository_head": "eaa332b1bb994279b59ea5af786fdb5de96adc1b",
        "simlingo_head": SIMLINGO_HEAD,
        "simlingo_diff_sha256": SIMLINGO_DIFF_SHA256,
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "grounding_dino_sha256": DINO_SHA256,
        "scenario_manifest_sha256": SCENARIO_MANIFEST_SHA256,
        "historical_e3_ledger_sha256": HISTORICAL_HASHES["ledger"],
        "historical_e3_final_receipt_sha256": HISTORICAL_HASHES["final_receipt"],
        "historical_e3_artifact_manifest_sha256": HISTORICAL_HASHES["artifact_manifest"],
    }
    mismatches = {
        key: {"expected": expected[key], "observed": observed[key]}
        for key in expected
        if observed[key] != expected[key]
    }
    return {"observed": observed, "expected": expected, "mismatches": mismatches, "pass": not mismatches}


def _port_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _ancestor_pids() -> set[int]:
    result = {os.getpid()}
    current = os.getpid()
    while current > 1:
        try:
            fields = Path("/proc/{}/stat".format(current)).read_text().split()
            current = int(fields[3])
        except (OSError, ValueError, IndexError):
            break
        result.add(current)
    return result


def resource_snapshot() -> Mapping[str, Any]:
    ancestors = _ancestor_pids()
    process = _command(("ps", "-eo", "pid=,comm=,args="))
    residual = []
    for line in process.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) < 2:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        if pid in ancestors:
            continue
        command = fields[1]
        args = fields[2] if len(fields) == 3 else ""
        if (
            command.startswith("CarlaUE4")
            or "leaderboard_evaluator.py" in args
            or "scenario_runner.py" in args
        ):
            residual.append({"pid": pid, "command": command, "args": args})
    gpu = _command(
        (
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader",
        )
    )
    project_gpu = [
        line
        for line in gpu.stdout.splitlines()
        if any(token in line.casefold() for token in ("python", "carla", "grounding"))
    ]
    display = _command(("xdpyinfo",), cwd=ROOT)
    ports = {str(port): _port_free(port) for port in (2020, 2021, 8020)}
    passed = not residual and not project_gpu and all(ports.values()) and display.returncode == 0
    return {
        "status": "PASS" if passed else "BLOCKED",
        "checked_at_utc": _now(),
        "display": os.environ.get("DISPLAY"),
        "physical_x11_1_available": display.returncode == 0,
        "residual_processes": residual,
        "project_gpu_compute_processes": project_gpu,
        "ports_free": ports,
    }


def materialize_full_schedule() -> list[dict[str, Any]]:
    scenarios = _load(SCENARIO_PROTOCOL_PATH)
    roster = _load(ROSTER_PATH)
    if tuple(roster["method_order"]) != METHOD_ORDER:
        raise FormalExecutionError("BLOCKED_METHOD_ORDER_MISMATCH")
    rows: list[dict[str, Any]] = []
    for scenario in scenarios["scenarios"]:
        for seed in scenario["seeds"]:
            for method_id in METHOD_ORDER:
                slot_index = len(rows) + 1
                rows.append(
                    {
                        "experiment_family_id": FAMILY_ID,
                        "split": scenario["split"],
                        "scenario_id": scenario["scenario_id"],
                        "seed": int(seed),
                        "method_id": method_id,
                        "runtime_fixture_id": scenario["runtime_fixture_id"],
                        "slot_index": slot_index,
                        "episode_id": "{}-{:04d}".format(FAMILY_ID, slot_index),
                    }
                )
    if len(rows) != 768 or _payload_sha(rows) != FULL_SCHEDULE_SHA256:
        raise FormalExecutionError("BLOCKED_FRESH_FULL_SCHEDULE_HASH_MISMATCH")
    return rows


def train_rows() -> list[dict[str, Any]]:
    rows = [dict(row) for row in materialize_full_schedule() if row["split"] == "train"]
    if len(rows) != 256:
        raise FormalExecutionError("BLOCKED_FRESH_TRAIN_COUNT_NOT_256")
    counts = Counter(row["method_id"] for row in rows)
    if counts != Counter({method: 32 for method in METHOD_ORDER}):
        raise FormalExecutionError("BLOCKED_FRESH_TRAIN_METHOD_COUNTS")
    for index, row in enumerate(rows, start=1):
        row["split_slot_index"] = index
    view = [
        {
            key: row[key]
            for key in (
                "experiment_family_id",
                "split",
                "scenario_id",
                "seed",
                "method_id",
                "runtime_fixture_id",
                "split_slot_index",
            )
        }
        for row in rows
    ]
    if _payload_sha(view) != TRAIN_SCHEDULE_SHA256:
        raise FormalExecutionError("BLOCKED_FRESH_TRAIN_SCHEDULE_HASH_MISMATCH")
    return rows


def _fresh_spec(row: Mapping[str, Any]) -> stage6b_backend.EpisodeSpec:
    legacy = stage6b_backend.resolve_train_episode(
        scenario_id=str(row["scenario_id"]),
        seed=int(row["seed"]),
        method_id=str(row["method_id"]),
    )
    if legacy.runtime_fixture_id != row["runtime_fixture_id"]:
        raise FormalExecutionError("BLOCKED_RUNTIME_FIXTURE_BINDING_MISMATCH")
    return replace(
        legacy,
        episode_id=str(row["episode_id"]),
        schedule_sha256=FULL_SCHEDULE_SHA256,
    )


def _schedule_bindings(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    bindings = []
    for row in rows:
        spec = _fresh_spec(row)
        bindings.append(
            {
                "split_slot_index": row["split_slot_index"],
                "global_slot_index": row["slot_index"],
                "fresh_episode_id": row["episode_id"],
                "legacy_source_episode_id": stage6b_backend.resolve_train_episode(
                    scenario_id=str(row["scenario_id"]),
                    seed=int(row["seed"]),
                    method_id=str(row["method_id"]),
                ).episode_id,
                "scenario_id": spec.scenario_id,
                "seed": spec.seed,
                "method_id": spec.method_id,
                "runtime_fixture_id": spec.runtime_fixture_id,
                "runtime_config_id": spec.runtime_config_id,
                "runtime_manifest_sha256": spec.runtime_manifest_sha256,
                "fresh_schedule_sha256": spec.schedule_sha256,
            }
        )
    return bindings


def _attempt_state(attempt: Mapping[str, Any]) -> str:
    events = attempt.get("events", [])
    return str(events[-1]["state"]) if events else "UNKNOWN"


def _derive_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    attempts = ledger.get("attempt_records", [])
    states = Counter(_attempt_state(attempt) for attempt in attempts)
    accounted = sum(
        states[state]
        for state in ("COMPLETED_RECORDED", "ENGINEERING_INVALID", "BLOCKED_CONTRACT_DEFECT")
    )
    ledger["counts"] = {
        "scheduled": 768,
        "started": len(attempts),
        "completed_recorded": states["COMPLETED_RECORDED"],
        "engineering_invalid": states["ENGINEERING_INVALID"],
        "blocked_contract_defect": states["BLOCKED_CONTRACT_DEFECT"],
        "remaining": 768 - accounted,
        "fresh_e3_runs": len(attempts),
        "formal_train_runs": len(attempts),
        "dev_attempts": 0,
        "test_attempts": 0,
        "training_jobs": 0,
        "a800_jobs": 0,
    }
    if states["ENGINEERING_INVALID"]:
        status_value = "BLOCKED_FIRST_ENGINEERING_INVALID_APPEND_ONLY_REVIEW_REQUIRED"
        train_status = "BLOCKED_ENGINEERING_INVALID"
    elif states["BLOCKED_CONTRACT_DEFECT"]:
        status_value = "BLOCKED_FORMAL_EVIDENCE_CONTRACT_DEFECT"
        train_status = "BLOCKED_CONTRACT_DEFECT"
    elif states["COMPLETED_RECORDED"] == 256:
        status_value = "FORMAL_TRAIN_TERMINAL_256_OF_256_PENDING_CLOSEOUT"
        train_status = "TERMINAL_PENDING_CLOSEOUT"
    elif attempts:
        status_value = "FRESH_FORMAL_TRAIN_IN_PROGRESS"
        train_status = "IN_PROGRESS"
    else:
        status_value = "PREPARED_ZERO_ATTEMPTS_NOT_EXECUTED"
        train_status = "PREPARED_NOT_STARTED"
    ledger["status"] = status_value
    ledger["split_state"] = {
        "train": {"status": train_status, "scheduled": 256, "attempts": len(attempts)},
        "dev": {"status": "SEALED_ZERO_ATTEMPTS", "scheduled": 256, "attempts": 0},
        "test": {
            "status": "SEALED_UNCONSUMED_ZERO_ATTEMPTS",
            "scheduled": 256,
            "attempts": 0,
            "consumed": False,
        },
    }
    ledger["created_for_preparation_only"] = not bool(attempts)
    ledger["execution_performed"] = bool(attempts)
    ledger["updated_at_utc"] = _now()
    return ledger


def _save_ledger(ledger: dict[str, Any]) -> None:
    _atomic_json(LEDGER_PATH, _derive_ledger(ledger))


def validate_entry(*, require_initial_ledger: bool) -> Mapping[str, Any]:
    method = _method_integrity()
    paper = _paper_integrity()
    evaluation = _evaluation_fix_integrity()
    protected = _protected_integrity()
    ledger = _load(LEDGER_PATH)
    ledger_hash = _sha(LEDGER_PATH)
    zero_boundary = (
        ledger.get("counts", {}).get("dev_attempts") == 0
        and ledger.get("counts", {}).get("test_attempts") == 0
        and ledger.get("split_state", {}).get("test", {}).get("consumed") is False
        and ledger.get("counts", {}).get("training_jobs") == 0
        and ledger.get("counts", {}).get("a800_jobs") == 0
    )
    initial_ok = (
        not require_initial_ledger
        or (
            ledger_hash == INITIAL_LEDGER_SHA256
            and ledger.get("status") == "PREPARED_ZERO_ATTEMPTS_NOT_EXECUTED"
            and len(ledger.get("attempt_records", [])) == 0
        )
    )
    passed = method["pass"] and paper["pass"] and evaluation["pass"] and protected["pass"] and zero_boundary and initial_ok
    result = {
        "schema_version": "driveclarify.method_v1_r3.formal_train_entry_check.v1",
        "status": "PASS_FORMAL_TRAIN_ENTRY" if passed else "BLOCKED_FORMAL_TRAIN_ENTRY_MISMATCH",
        "checked_at_utc": _now(),
        "method": method,
        "paper_protocol": paper,
        "evaluation_fix": evaluation,
        "protected": protected,
        "fresh_ledger_sha256": ledger_hash,
        "require_initial_ledger": require_initial_ledger,
        "initial_ledger_ok": initial_ok,
        "dev_test_training_boundary": zero_boundary,
    }
    if not passed:
        raise FormalExecutionError("BLOCKED_FORMAL_TRAIN_ENTRY_MISMATCH")
    return result


def prepare_execution() -> Mapping[str, Any]:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    entry = validate_entry(require_initial_ledger=True)
    resources = resource_snapshot()
    if resources["status"] != "PASS":
        raise FormalExecutionError("BLOCKED_PREEXECUTION_RESOURCES_NOT_CLEAN")
    rows = train_rows()
    view = [
        {
            key: row[key]
            for key in (
                "experiment_family_id",
                "split",
                "scenario_id",
                "seed",
                "method_id",
                "runtime_fixture_id",
                "split_slot_index",
            )
        }
        for row in rows
    ]
    bindings = _schedule_bindings(rows)
    _atomic_json(TRAIN_SCHEDULE_PATH, rows, canonical=True)
    _atomic_json(TRAIN_HASH_VIEW_PATH, view, canonical=True)
    _atomic_json(TRAIN_BINDINGS_PATH, bindings, canonical=True)
    receipt = {
        "schema_version": "driveclarify.method_v1_r3.formal_train_dry_run.v1",
        "status": "PASS_FRESH_TRAIN_256_ROWS_ZERO_ATTEMPTS_DRY_RUN",
        "experiment_family_id": FAMILY_ID,
        "full_schedule_sha256": FULL_SCHEDULE_SHA256,
        "train_schedule_sha256": _payload_sha(view),
        "train_row_file_sha256": _sha(TRAIN_SCHEDULE_PATH),
        "train_hash_view_file_sha256": _sha(TRAIN_HASH_VIEW_PATH),
        "binding_file_sha256": _sha(TRAIN_BINDINGS_PATH),
        "row_count": len(rows),
        "method_counts": dict(Counter(row["method_id"] for row in rows)),
        "fresh_episode_id_count": len({row["episode_id"] for row in rows}),
        "attempt_count": 0,
        "entry": entry,
        "resources": resources,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "generated_at_utc": _now(),
    }
    _atomic_json(REPORT_ROOT / "FRESH_TRAIN_DRY_RUN_RECEIPT.json", receipt)
    return receipt


def _run_test_command(command: Sequence[str], log_path: Path) -> Mapping[str, Any]:
    environment = dict(os.environ)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = _now()
    with log_path.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            list(command),
            cwd=str(ROOT),
            env=environment,
            check=False,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    return {
        "command": list(command),
        "started_at_utc": started,
        "ended_at_utc": _now(),
        "return_code": result.returncode,
        "log_path": str(log_path.relative_to(ROOT)),
        "log_sha256": _sha(log_path),
    }


def freeze_execution_entry() -> Mapping[str, Any]:
    if ENTRY_AMENDMENT_PATH.exists():
        return _load(ENTRY_AMENDMENT_PATH)
    dry_run = prepare_execution()
    python = "/home/buaa/anaconda3/envs/simlingo/bin/python"
    regression = _run_test_command(
        (
            python,
            "-m",
            "pytest",
            "-q",
            "tests/grounded_language_v1_extension_e1_r1_e3",
            "tests/paper_mvp_stage6b",
            "tests/paper_mvp_stage6b_r0",
        ),
        REPORT_ROOT / "logs/EVALUATION_REGRESSION_34.log",
    )
    orchestrator_tests = _run_test_command(
        (python, "-m", "pytest", "-q", str(TEST_PATH.relative_to(ROOT))),
        REPORT_ROOT / "logs/EXECUTION_ORCHESTRATOR_TESTS.log",
    )
    if regression["return_code"] != 0 or orchestrator_tests["return_code"] != 0:
        raise FormalExecutionError("BLOCKED_EXECUTION_ENTRY_TEST_FAILURE")
    source_paths = (
        PACKAGE_ROOT / "__init__.py",
        PACKAGE_ROOT / "orchestrator.py",
        TOOL_PATH,
    )
    source_hashes = {str(path.relative_to(ROOT)): _sha(path) for path in source_paths}
    test_hashes = {str(TEST_PATH.relative_to(ROOT)): _sha(TEST_PATH)}
    source_lines = ["{}  {}\n".format(value, key) for key, value in sorted(source_hashes.items())]
    test_lines = ["{}  {}\n".format(value, key) for key, value in sorted(test_hashes.items())]
    amendment = {
        "schema_version": "driveclarify.method_v1_r3.formal_train_execution_entry_amendment.v1",
        "status": "PASS_EXECUTION_ONLY_ORCHESTRATOR_FROZEN_READY_FOR_FIRST_TRAIN_EPISODE",
        "experiment_family_id": FAMILY_ID,
        "authorized_stage": "DRIVECLARIFY_FRESH_FORMAL_E3_AND_TRAIN_EXECUTION",
        "execution_only_source_hashes": source_hashes,
        "execution_only_source_aggregate_sha256": hashlib.sha256("".join(source_lines).encode()).hexdigest(),
        "execution_test_hashes": test_hashes,
        "execution_test_aggregate_sha256": hashlib.sha256("".join(test_lines).encode()).hexdigest(),
        "evaluation_regression": regression,
        "orchestrator_tests": orchestrator_tests,
        "dry_run_receipt_sha256": _sha(REPORT_ROOT / "FRESH_TRAIN_DRY_RUN_RECEIPT.json"),
        "train_schedule_file_sha256": dry_run["train_row_file_sha256"],
        "train_hash_view_file_sha256": dry_run["train_hash_view_file_sha256"],
        "train_schedule_payload_sha256": dry_run["train_schedule_sha256"],
        "train_execution_bindings_sha256": dry_run["binding_file_sha256"],
        "initial_ledger_sha256": _sha(LEDGER_PATH),
        "method_freeze_sha256": METHOD_FREEZE_SHA256,
        "paper_protocol_sha256": PAPER_PROTOCOL_SHA256,
        "evaluation_fix_sha256": EVALUATION_FIX_SHA256,
        "scientific_retry_authorized": 0,
        "automatic_retry_authorized": 0,
        "engineering_rerun_authorized": 0,
        "max_primary_train_runs": 256,
        "run_at_most_one_new_episode_per_cli_invocation": True,
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "training_jobs": 0,
        "a800_jobs": 0,
        "created_at_utc": _now(),
    }
    if amendment["initial_ledger_sha256"] != INITIAL_LEDGER_SHA256:
        raise FormalExecutionError("BLOCKED_INITIAL_LEDGER_CHANGED_BEFORE_ENTRY_FREEZE")
    _atomic_json(ENTRY_AMENDMENT_PATH, amendment)
    return amendment


def _verify_amendment() -> Mapping[str, Any]:
    if not ENTRY_AMENDMENT_PATH.is_file():
        raise FormalExecutionError("BLOCKED_EXECUTION_ENTRY_AMENDMENT_REQUIRED")
    amendment = _load(ENTRY_AMENDMENT_PATH)
    if amendment.get("status") != "PASS_EXECUTION_ONLY_ORCHESTRATOR_FROZEN_READY_FOR_FIRST_TRAIN_EPISODE":
        raise FormalExecutionError("BLOCKED_EXECUTION_ENTRY_AMENDMENT_STATUS")
    for path_text, expected in amendment["execution_only_source_hashes"].items():
        if _sha(ROOT / path_text) != expected:
            raise FormalExecutionError("BLOCKED_EXECUTION_ONLY_SOURCE_MUTATION:" + path_text)
    for path_text, expected in amendment["execution_test_hashes"].items():
        if _sha(ROOT / path_text) != expected:
            raise FormalExecutionError("BLOCKED_EXECUTION_TEST_MUTATION:" + path_text)
    frozen_artifacts = {
        TRAIN_SCHEDULE_PATH: amendment["train_schedule_file_sha256"],
        TRAIN_HASH_VIEW_PATH: amendment["train_hash_view_file_sha256"],
        TRAIN_BINDINGS_PATH: amendment["train_execution_bindings_sha256"],
        REPORT_ROOT / "FRESH_TRAIN_DRY_RUN_RECEIPT.json": amendment["dry_run_receipt_sha256"],
    }
    for path, expected in frozen_artifacts.items():
        if not path.is_file() or _sha(path) != expected:
            raise FormalExecutionError(
                "BLOCKED_EXECUTION_ENTRY_ARTIFACT_MUTATION:" + str(path.relative_to(ROOT))
            )
    for record in (amendment["evaluation_regression"], amendment["orchestrator_tests"]):
        path = ROOT / record["log_path"]
        if not path.is_file() or _sha(path) != record["log_sha256"]:
            raise FormalExecutionError("BLOCKED_EXECUTION_ENTRY_TEST_LOG_MUTATION")
    return amendment


def _artifact_hashes(output: Path) -> Mapping[str, Any]:
    return {
        str(path.relative_to(output)): {"sha256": _sha(path), "bytes": path.stat().st_size}
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }


def _metric_receipt(row: Mapping[str, Any], output: Path, backend_receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    required = {
        "runtime_producer": output / "stage6b_runtime_audit.json",
        "serialized_episode_result": output / "EPISODE_RESULT.json",
        "backend_episode_receipt": output / "EPISODE_RECEIPT.json",
        "cleanup_receipt": output / "CLEANUP_RECEIPT.json",
        "evaluator_stdout": output / "evaluator_stdout.log",
    }
    present = {name: path.is_file() for name, path in required.items()}
    result = _load(required["serialized_episode_result"]) if present["serialized_episode_result"] else {}
    identity = result.get("episode_identity", {})
    identity_match = all(
        identity.get(key) == row[key]
        for key in ("episode_id", "scenario_id", "method_id", "split")
    ) and int(identity.get("seed", -1)) == int(row["seed"])
    gates = _formal_evidence_gates(result) if result else {
        family: {"status": "UNKNOWN"}
        for family in ("forward", "pid", "authority", "label_firewall", "cleanup")
    }
    receipt_hashes = backend_receipt.get("artifacts", {})
    hash_paths = {
        "episode_result_sha256": output / "EPISODE_RESULT.json",
        "runtime_audit_sha256": output / "stage6b_runtime_audit.json",
        "evaluator_stdout_sha256": output / "evaluator_stdout.log",
        "leaderboard_results_sha256": output / "leaderboard_results.json",
        "full_brake_diagnosis_sha256": output / "FULL_BRAKE_DIAGNOSIS.json",
    }
    artifact_hash_matches = {}
    for name, path in hash_paths.items():
        expected = receipt_hashes.get(name)
        artifact_hash_matches[name] = (
            path.is_file() and expected == _sha(path)
            if expected is not None
            else not path.is_file()
        )
    fingerprint = result.get("runtime_fingerprint", {})
    checkpoint = result.get("checkpoint", {})
    input_hash_matches = {
        "fresh_schedule": fingerprint.get("schedule_sha256") == FULL_SCHEDULE_SHA256,
        "simlingo_head": fingerprint.get("simlingo_head") == SIMLINGO_HEAD,
        "simlingo_diff": fingerprint.get("simlingo_protected_diff_sha256") == SIMLINGO_DIFF_SHA256,
        "checkpoint": checkpoint.get("sha256") == CHECKPOINT_SHA256,
        "physical_display": fingerprint.get("native_display") == ":1" and fingerprint.get("headless") is False,
    }
    all_gates_pass = all(gates[name]["status"] == "PASS" for name in gates)
    backend_complete = backend_receipt.get("status") == "COMPLETED_RECORDED_METHOD_RESULT"
    evidence_complete = (
        all(present.values())
        and identity_match
        and all(artifact_hash_matches.values())
        and all(input_hash_matches.values())
        and all_gates_pass
        and backend_complete
    )
    status_value = (
        "PASS_COMPLETED_RECORDED_EVIDENCE_CHAIN"
        if evidence_complete
        else "ENGINEERING_INVALID_ENVIRONMENT"
        if not backend_complete
        else "BLOCKED_CONTRACT_DEFECT_REQUIRED_EVIDENCE"
    )
    return {
        "schema_version": "driveclarify.method_v1_r3.formal_train_metric_receipt.v1",
        "status": status_value,
        "episode_identity": {
            key: row[key]
            for key in (
                "episode_id",
                "split",
                "scenario_id",
                "seed",
                "method_id",
                "runtime_fixture_id",
                "slot_index",
                "split_slot_index",
            )
        },
        "evidence_chain": {
            "producer": present["runtime_producer"],
            "serialization": present["serialized_episode_result"],
            "ingest": bool(result),
            "aggregation_input_identity_match": identity_match,
            "backend_artifact_hash_matches": artifact_hash_matches,
            "frozen_input_hash_matches": input_hash_matches,
            "reducer": gates,
            "receipt_inputs_present": present,
        },
        "all_required_gates_pass": all_gates_pass,
        "backend_complete": backend_complete,
        "failure_class": result.get("failure_class"),
        "metric_families": {
            family: result.get(family, {})
            for family in ("task_metrics", "safety_metrics", "interaction_metrics", "compute_metrics")
        },
        "label_firewall": result.get("label_firewall", {}),
        "forward_accounting": result.get("forward_accounting", {}),
        "pid_accounting": result.get("pid_accounting", {}),
        "cleanup_state": result.get("cleanup_state", {}),
        "generated_at_utc": _now(),
    }


def _block_checkpoint(ledger: Mapping[str, Any], row: Mapping[str, Any]) -> None:
    if int(row["split_slot_index"]) % len(METHOD_ORDER) != 0:
        return
    method = _method_integrity()
    if not method["pass"]:
        raise FormalExecutionError("BLOCKED_POST_FREEZE_METHOD_MUTATION")
    block_index = int(row["split_slot_index"]) // len(METHOD_ORDER)
    attempts = ledger.get("attempt_records", [])
    checkpoint = {
        "schema_version": "driveclarify.method_v1_r3.formal_train_block_checkpoint.v1",
        "status": "PASS_SCENARIO_SEED_BLOCK_CHECKPOINT",
        "block_index": block_index,
        "scenario_id": row["scenario_id"],
        "seed": row["seed"],
        "completed_episode_count": sum(_attempt_state(item) == "COMPLETED_RECORDED" for item in attempts),
        "method_integrity": method,
        "historical_e3_hashes": _protected_integrity()["observed"],
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "generated_at_utc": _now(),
    }
    _atomic_json(REPORT_ROOT / "block_checkpoints/BLOCK_{:02d}.json".format(block_index), checkpoint)


def run_one(
    *,
    backend: stage6b_backend.UnifiedNativeBackend | None = None,
) -> Mapping[str, Any]:
    amendment = _verify_amendment()
    validate_entry(require_initial_ledger=False)
    ledger = _load(LEDGER_PATH)
    if str(ledger.get("status", "")).startswith("BLOCKED_"):
        raise FormalExecutionError("BLOCKED_LEDGER_REQUIRES_APPEND_ONLY_REVIEW")
    attempts = ledger.setdefault("attempt_records", [])
    attempted_ids = {attempt["episode_id"] for attempt in attempts}
    rows = train_rows()
    row = next((item for item in rows if item["episode_id"] not in attempted_ids), None)
    if row is None:
        return {"status": "FORMAL_TRAIN_NO_UNSTARTED_EPISODES", "ledger": _derive_ledger(ledger)}
    resources = resource_snapshot()
    if resources["status"] != "PASS":
        raise FormalExecutionError("BLOCKED_PRE_ATTEMPT_RESOURCE_GATE")
    attempt_id = str(row["episode_id"]) + "-A01"
    output = ARTIFACT_ROOT / FAMILY_ID / "episodes" / str(row["episode_id"])
    if output.exists() and any(output.iterdir()):
        raise FormalExecutionError("BLOCKED_FRESH_ATTEMPT_ARTIFACT_DIRECTORY_NOT_EMPTY")
    prior_ledger_sha256 = _sha(LEDGER_PATH)
    started_event = {
        "event": "ATTEMPT_STARTED",
        "state": "RUNNING",
        "timestamp_utc": _now(),
        "entry_amendment_sha256": _sha(ENTRY_AMENDMENT_PATH),
        "method_freeze_sha256": METHOD_FREEZE_SHA256,
        "paper_protocol_sha256": PAPER_PROTOCOL_SHA256,
        "evaluation_fix_sha256": EVALUATION_FIX_SHA256,
        "historical_e3_hashes": HISTORICAL_HASHES,
        "prior_ledger_sha256": prior_ledger_sha256,
        "resource_preflight": resources,
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
        "engineering_rerun_count": 0,
        "artifact_dir": str(output.relative_to(ROOT)),
        "events": [started_event],
    }
    attempts.append(attempt)
    _save_ledger(ledger)
    started_ledger_sha256 = _sha(LEDGER_PATH)
    spec = _fresh_spec(row)
    runner = backend or stage6b_backend.UnifiedNativeBackend()
    terminal_state = "ENGINEERING_INVALID"
    blocker = None
    backend_receipt: Mapping[str, Any] = {}
    metric: Mapping[str, Any] | None = None
    try:
        backend_receipt = runner.run(spec, output, visualization=False)
        metric = _metric_receipt(row, output, backend_receipt)
        _atomic_json(output / "FRESH_FORMAL_METRIC_RECEIPT.json", metric)
        if metric["status"] == "PASS_COMPLETED_RECORDED_EVIDENCE_CHAIN":
            terminal_state = "COMPLETED_RECORDED"
        elif metric["status"] == "ENGINEERING_INVALID_ENVIRONMENT":
            terminal_state = "ENGINEERING_INVALID"
            blocker = str(backend_receipt.get("termination_reason") or backend_receipt.get("status"))
        else:
            terminal_state = "BLOCKED_CONTRACT_DEFECT"
            blocker = str(metric["status"])
    except Exception as exc:
        terminal_state = "ENGINEERING_INVALID"
        blocker = type(exc).__name__ + ":" + str(exc)
    post_resources = resource_snapshot()
    _atomic_json(output / "ORCHESTRATOR_POST_CLEANUP.json", post_resources)
    if post_resources["status"] != "PASS":
        terminal_state = "ENGINEERING_INVALID"
        blocker = "POST_ATTEMPT_RESOURCE_CLEANUP_FAILED"
    terminal_event = {
        "event": "ATTEMPT_TERMINATED",
        "state": terminal_state,
        "timestamp_utc": _now(),
        "blocker": blocker,
        "backend_receipt_status": backend_receipt.get("status"),
        "backend_evaluator_return_code": backend_receipt.get("evaluator_return_code"),
        "cleanup_status": post_resources["status"],
        "started_ledger_sha256": started_ledger_sha256,
        "metric_receipt_sha256": (
            _sha(output / "FRESH_FORMAL_METRIC_RECEIPT.json")
            if (output / "FRESH_FORMAL_METRIC_RECEIPT.json").is_file()
            else None
        ),
        "post_cleanup_receipt_sha256": _sha(output / "ORCHESTRATOR_POST_CLEANUP.json"),
        "episode_receipt_path": (
            str((output / "EPISODE_RECEIPT.json").relative_to(ROOT))
            if (output / "EPISODE_RECEIPT.json").is_file()
            else None
        ),
        "metric_receipt_path": (
            str((output / "FRESH_FORMAL_METRIC_RECEIPT.json").relative_to(ROOT))
            if (output / "FRESH_FORMAL_METRIC_RECEIPT.json").is_file()
            else None
        ),
        "artifact_hashes": _artifact_hashes(output),
    }
    attempt["events"].append(terminal_event)
    _save_ledger(ledger)
    if terminal_state == "COMPLETED_RECORDED":
        try:
            _block_checkpoint(ledger, row)
        except Exception as exc:
            terminal_state = "BLOCKED_CONTRACT_DEFECT"
            blocker = type(exc).__name__ + ":" + str(exc)
            attempt["events"].append(
                {
                    "event": "POST_BLOCK_INTEGRITY_FAILED",
                    "state": terminal_state,
                    "timestamp_utc": _now(),
                    "blocker": blocker,
                }
            )
            _save_ledger(ledger)
    result = {
        "schema_version": "driveclarify.method_v1_r3.formal_train_run_one.v1",
        "status": (
            "PASS_ONE_FRESH_TRAIN_EPISODE_COMPLETED_RECORDED"
            if terminal_state == "COMPLETED_RECORDED"
            else "BLOCKED_FIRST_ENGINEERING_INVALID_APPEND_ONLY_REVIEW_REQUIRED"
            if terminal_state == "ENGINEERING_INVALID"
            else "BLOCKED_FORMAL_EVIDENCE_CONTRACT_DEFECT"
        ),
        "attempt_id": attempt_id,
        "episode_id": row["episode_id"],
        "split_slot_index": row["split_slot_index"],
        "scenario_id": row["scenario_id"],
        "seed": row["seed"],
        "method_id": row["method_id"],
        "terminal_state": terminal_state,
        "blocker": blocker,
        "ledger_sha256": _sha(LEDGER_PATH),
        "entry_amendment_sha256": _sha(ENTRY_AMENDMENT_PATH),
        "formal_train_counts": _load(LEDGER_PATH)["counts"],
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "training_jobs": 0,
        "a800_jobs": 0,
        "generated_at_utc": _now(),
    }
    _atomic_json(REPORT_ROOT / "latest_run_one_receipt.json", result)
    if terminal_state != "COMPLETED_RECORDED":
        raise FormalExecutionError(result["status"] + ":" + str(blocker))
    return result


def status() -> Mapping[str, Any]:
    ledger = _derive_ledger(_load(LEDGER_PATH))
    attempts = ledger.get("attempt_records", [])
    per_method = defaultdict(Counter)
    per_scenario = defaultdict(Counter)
    for attempt in attempts:
        state = _attempt_state(attempt)
        per_method[attempt["method_id"]][state] += 1
        per_scenario[attempt["scenario_id"]][state] += 1
    return {
        "schema_version": "driveclarify.method_v1_r3.formal_train_status.v1",
        "status": ledger["status"],
        "counts": ledger["counts"],
        "split_state": ledger["split_state"],
        "per_method": {key: dict(value) for key, value in sorted(per_method.items())},
        "per_scenario": {key: dict(value) for key, value in sorted(per_scenario.items())},
        "next_episode": next(
            (row for row in train_rows() if row["episode_id"] not in {a["episode_id"] for a in attempts}),
            None,
        ),
    }


def _available_value(metric: Mapping[str, Any]) -> Any:
    return metric.get("value") if metric.get("status") == "AVAILABLE" else None


def _paired_cluster_bootstrap(
    paired: Sequence[Mapping[str, Any]], *, seed: int = 20260815, draws: int = 10000
) -> Mapping[str, Any]:
    scenarios = sorted({str(row["scenario_id"]) for row in paired})
    by_scenario = {
        scenario: [float(row["difference"]) for row in paired if row["scenario_id"] == scenario]
        for scenario in scenarios
    }
    if not scenarios or any(not values for values in by_scenario.values()):
        return {"status": "UNKNOWN", "draws": draws, "estimate": None, "ci95": None}
    estimate = fmean(value for values in by_scenario.values() for value in values)
    rng = random.Random(seed)
    samples = []
    for _ in range(draws):
        sampled = [rng.choice(scenarios) for _ in scenarios]
        samples.append(fmean(value for scenario in sampled for value in by_scenario[scenario]))
    samples.sort()
    return {
        "status": "AVAILABLE",
        "draws": draws,
        "analysis_seed": seed,
        "estimate": estimate,
        "ci95": [samples[int(0.025 * draws)], samples[int(0.975 * draws) - 1]],
        "scenario_cluster_count": len(scenarios),
        "paired_episode_count": len(paired),
    }


def closeout() -> Mapping[str, Any]:
    _verify_amendment()
    ledger = _derive_ledger(_load(LEDGER_PATH))
    if ledger["counts"]["completed_recorded"] != 256:
        raise FormalExecutionError("BLOCKED_TRAIN_CLOSEOUT_REQUIRES_256_COMPLETED_RECORDED")
    attempts = ledger["attempt_records"]
    results = []
    metrics = []
    for attempt in attempts:
        output = ROOT / attempt["artifact_dir"]
        results.append(_load(output / "EPISODE_RESULT.json"))
        metrics.append(_load(output / "FRESH_FORMAL_METRIC_RECEIPT.json"))
    unknown = Counter()
    available = Counter()
    for result in results:
        for family in ("task_metrics", "safety_metrics", "interaction_metrics", "compute_metrics"):
            for name, value in result.get(family, {}).items():
                key = family + "." + name
                if value.get("status") == "AVAILABLE":
                    available[key] += 1
                else:
                    unknown[key] += 1
    coverage = {
        method: dict(Counter(_attempt_state(a) for a in attempts if a["method_id"] == method))
        for method in METHOD_ORDER
    }
    scenario_coverage = {
        scenario: dict(Counter(_attempt_state(a) for a in attempts if a["scenario_id"] == scenario))
        for scenario in sorted({a["scenario_id"] for a in attempts})
    }
    failure_taxonomy = dict(Counter(str(result.get("failure_class")) for result in results))
    gate_statuses = Counter(
        gate["status"]
        for metric in metrics
        for gate in metric["evidence_chain"]["reducer"].values()
    )
    label_totals = {}
    for field in (
        "policy_expected_decision_reads",
        "policy_gold_candidate_index_reads",
        "policy_evaluator_annotation_reads",
    ):
        values = [result.get("label_firewall", {}).get(field) for result in results]
        label_totals[field] = sum(values) if all(isinstance(value, int) for value in values) else None
    duration_values = [
        float(_load(ROOT / attempt["events"][-1]["episode_receipt_path"])["duration_wall_seconds"])
        for attempt in attempts
    ]
    data_quality = {
        "schema_version": "driveclarify.method_v1_r3.formal_train_closeout.v1",
        "status": "PASS_FRESH_FORMAL_TRAIN_256_COMPLETED_RECORDED",
        "scheduled_train": 256,
        "started_train": len(attempts),
        "completed_recorded_train": ledger["counts"]["completed_recorded"],
        "engineering_invalid": ledger["counts"]["engineering_invalid"],
        "blocked_contract_defect": ledger["counts"]["blocked_contract_defect"],
        "remaining_train": 0,
        "remaining_global_sealed_dev_test": 512,
        "per_method_coverage": coverage,
        "per_scenario_coverage": scenario_coverage,
        "failure_taxonomy": failure_taxonomy,
        "retry_count": 0,
        "available_metric_counts": dict(available),
        "unknown_or_missing_metric_counts": dict(unknown),
        "formal_evidence_gate_status_counts": dict(gate_statuses),
        "label_firewall_totals": label_totals,
        "compute": {
            "episode_wall_seconds_sum": sum(duration_values),
            "episode_wall_seconds_mean": fmean(duration_values),
            "episode_wall_seconds_max": max(duration_values),
        },
        "cleanup": resource_snapshot(),
        "protected_integrity": _protected_integrity(),
        "method_integrity": _method_integrity(),
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "training_jobs": 0,
        "a800_jobs": 0,
        "generated_at_utc": _now(),
    }
    _atomic_json(REPORT_ROOT / "FRESH_FORMAL_TRAIN_CLOSEOUT_RECEIPT.json", data_quality)
    primary = ("goal_correct", "wrong_goal_execution")
    primary_complete = all(
        result.get("task_metrics", {}).get(name, {}).get("status") == "AVAILABLE"
        for result in results
        for name in primary
    )
    paired_payload = {}
    result_by_key = {
        (
            result["episode_identity"]["scenario_id"],
            int(result["episode_identity"]["seed"]),
            result["episode_identity"]["method_id"],
        ): result
        for result in results
    }
    if primary_complete:
        for comparator in ("original_simlingo", "never_ask", "always_ask"):
            for metric_name in primary:
                paired = []
                for scenario in sorted({attempt["scenario_id"] for attempt in attempts}):
                    seeds = sorted({int(a["seed"]) for a in attempts if a["scenario_id"] == scenario})
                    for seed in seeds:
                        main = _available_value(result_by_key[(scenario, seed, "driveclarify")]["task_metrics"][metric_name])
                        other = _available_value(result_by_key[(scenario, seed, comparator)]["task_metrics"][metric_name])
                        paired.append({"scenario_id": scenario, "seed": seed, "difference": float(main) - float(other)})
                paired_payload[metric_name + "__driveclarify_minus__" + comparator] = _paired_cluster_bootstrap(paired)
    precision = {
        "schema_version": "driveclarify.method_v1_r3.formal_train_precision_decision.v1",
        "status": (
            "PASS_TRAIN_PRECISION_ANALYSIS_CURRENT_SIZE_FROZEN"
            if primary_complete
            else "BLOCKED_TRAIN_PRECISION_PRIMARY_METRICS_UNKNOWN"
        ),
        "primary_metric_evidence_complete": primary_complete,
        "paired_cluster_bootstrap": paired_payload,
        "initial_scenario_seed_units": 32,
        "bootstrap_draws": 10000,
        "decision": (
            "CURRENT_INITIAL_SAMPLE_SIZE_ACCEPTED_WITH_REPORTED_ATTAINABLE_PRECISION"
            if primary_complete
            else "NO_SAMPLE_SIZE_DECISION; EVALUATION_EVIDENCE_COMPLETION_REQUIRES_APPEND_ONLY_REVIEW; NEW_SEEDS_WOULD_NOT_REPAIR_MISSING_PRIMARY_VALUES"
        ),
        "dev_authorized": primary_complete,
        "test_authorized": False,
        "generated_at_utc": _now(),
    }
    _atomic_json(REPORT_ROOT / "TRAIN_VARIANCE_AND_PRECISION_DECISION.json", precision)
    return {"closeout": data_quality, "precision": precision}
