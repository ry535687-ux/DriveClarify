"""Static-test and terminal delivery audit for the one-shot M1 V3 campaign.

This file is frozen before the formal runtime campaign.  ``static-tests``
creates the evidence required to authorize runtime.  ``finalize`` performs
only tests, reads, validation, reporting, and handoff writes after runtime; it
does not change executable source, rerun a unit, train, or continue to M2+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import jsonschema


ROOT = Path("/home/buaa/wrh/DriveClarify")
SIMLINGO = Path("/home/buaa/wrh/simlingo")
EXPANSION_ID = "DC-M1-DATASET-EXP-V3-20260804T055600Z"
CAMPAIGN_ID = "DC-M1-V3-RUNTIME-C1-20260804T063000Z"
AUTHORITY = ROOT / "reports/m1_real_dataset_expansion_v3" / EXPANSION_ID
CAMPAIGN = AUTHORITY / "combined_runtime_campaigns" / CAMPAIGN_ID
STAGE_A = CAMPAIGN / "stage_a"
STAGE_B = CAMPAIGN / "stage_b"
V2_ROOT = ROOT / "reports/m1_real_dataset_expansion_v2/DC-M1-DATASET-EXP-V2-20260803T143000Z"
V2_RESULT = V2_ROOT / "combined_runtime_campaigns/DC-M1-V2-RUNTIME-C1-20260803T144700Z/CAMPAIGN_RESULT.json"
V2_TREE_SHA256 = "31dc8aaf2774f46fa47dd2dfe05832ca85c6797947efddbef418dbab446f5843"
DRIVE_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
SIM_HEAD = "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
SIM_DIFF_SHA256 = "7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34"
PYTHON38 = Path("/home/buaa/anaconda3/envs/simlingo/bin/python3.8")


class DeliveryError(RuntimeError):
    pass


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(sha256_path(path).encode("ascii"))
        digest.update(b"  ")
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def run_command(argv: Sequence[str], cwd: Path = ROOT) -> Mapping[str, Any]:
    environment = dict(os.environ)
    environment.update(
        {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
        }
    )
    started = time.monotonic_ns()
    result = subprocess.run(
        list(argv),
        cwd=str(cwd),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return {
        "argv": list(argv),
        "cwd": str(cwd),
        "exit_code": result.returncode,
        "output": result.stdout,
        "latency_ns": time.monotonic_ns() - started,
    }


def test_commands() -> list[list[str]]:
    return [
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/stage_a_terminal_lifecycle",
            "tests/observation_screening",
            "tests/m1_real_dataset_expansion_v3",
        ],
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/m1_real_dataset_expansion_v2",
            "tests/multi_topology_static_units",
            "tests/multi_unit_offline_candidate_capture",
        ],
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/static_branch_mvp",
            "tests/fairness_contract_v2",
            "tests/evaluator_adapter_production_path",
            "tests/m3e_supervisor_binding",
            "tests/m3d_route_validation",
        ],
        [
            str(PYTHON38),
            "-B",
            "-m",
            "py_compile",
            "driveclarify_static_branch/m1_expansion_v3.py",
            "driveclarify_static_branch/m1_v3_runtime_campaign.py",
            "driveclarify_static_branch/run_result_lifecycle.py",
            "driveclarify_static_branch/observation_screening_agent.py",
            "driveclarify_static_branch/observation_screening_batch.py",
            "driveclarify_static_branch/offline_candidate_capture.py",
            "tools/prepare_m1_real_dataset_expansion_v3.py",
            "tools/deliver_m1_real_dataset_expansion_v3.py",
        ],
        [str(PYTHON38), "-B", "tools/evaluator_production_path_dry_run.py"],
    ]


def static_tests() -> Mapping[str, Any]:
    if (AUTHORITY / "V3_RUNTIME_CAMPAIGN_SPEC.json").is_file() and load(
        AUTHORITY / "V3_RUNTIME_CAMPAIGN_SPEC.json"
    ).get("run_authorized") is True:
        raise DeliveryError("STATIC_TESTS_AFTER_RUNTIME_AUTHORIZATION_FORBIDDEN")
    commands = [run_command(argv) for argv in test_commands()]
    failures = [item for item in commands if item["exit_code"] != 0]
    value = {
        "schema_version": "driveclarify.m1_v3_test_results.v1",
        "expansion_id": EXPANSION_ID,
        "phase": "STATIC_PRE_RUNTIME",
        "status": "PASS" if not failures else "FAIL",
        "failed": len(failures),
        "command_count": len(commands),
        "commands": commands,
        "deselect_used": False,
        "unexplained_failure_count": 0 if not failures else len(failures),
        "runtime_operations": 0,
        "v2_tree_sha256": tree_hash(V2_ROOT),
    }
    atomic_json(AUTHORITY / "TEST_RESULTS.json", value)
    with (AUTHORITY / "COMMAND_LOG.md").open("a", encoding="utf-8") as stream:
        stream.write(
            "- Static test bundle `{}`; command exit codes={}; no deselect.\n".format(
                value["status"], [item["exit_code"] for item in commands]
            )
        )
    if failures:
        raise DeliveryError("V3_STATIC_TESTS_FAILED")
    if value["v2_tree_sha256"] != V2_TREE_SHA256:
        raise DeliveryError("V2_TREE_HASH_CHANGED")
    return value


def git_snapshot(repository: Path) -> Mapping[str, Any]:
    def output(argv: Sequence[str]) -> bytes:
        return subprocess.check_output(list(argv), cwd=str(repository))

    tracked = output(["git", "diff", "--binary"])
    staged = output(["git", "diff", "--cached", "--binary"])
    untracked = output(["git", "ls-files", "--others", "--exclude-standard", "-z"])
    return {
        "branch": output(["git", "branch", "--show-current"]).decode().strip(),
        "head": output(["git", "rev-parse", "HEAD"]).decode().strip(),
        "tracked_diff_bytes": len(tracked),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_bytes": len(staged),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        "untracked_file_count": len([item for item in untracked.split(b"\0") if item]),
        "untracked_path_list_nul_bytes": len(untracked),
        "untracked_path_list_nul_sha256": hashlib.sha256(untracked).hexdigest(),
    }


def verify_embedded(path: Path) -> Mapping[str, Any]:
    value = load(path)
    unsigned = dict(value)
    recorded = unsigned.pop("sha256", None)
    if recorded != canonical_sha256(unsigned):
        raise DeliveryError("EMBEDDED_HASH_MISMATCH:" + str(path))
    return value


def verify_inventory(path: Path) -> Mapping[str, Any]:
    value = load(path)
    root = Path(value.get("root", path.parent))
    rows = value.get("files", value.get("artifacts"))
    if not isinstance(rows, list):
        raise DeliveryError("INVENTORY_ROWS_MISSING:" + str(path))
    failures = []
    for row in rows:
        target = root / row["path"]
        if not target.is_file() or target.stat().st_size != row["bytes"] or sha256_path(target) != row["sha256"]:
            failures.append(row["path"])
    if failures:
        raise DeliveryError("INVENTORY_MISMATCH:{}:{}".format(path, ",".join(failures[:10])))
    if value.get("file_count") != len(rows):
        raise DeliveryError("INVENTORY_COUNT_MISMATCH:" + str(path))
    return {"path": str(path), "verified_files": len(rows), "status": "PASS"}


def _environment() -> Mapping[str, Any]:
    from driveclarify_static_branch import m3e_p3_campaign as p3

    return p3.environment_preflight()


def independent_review() -> Mapping[str, Any]:
    result = load(AUTHORITY / "CAMPAIGN_RESULT.json")
    selected = verify_embedded(AUTHORITY / "V3_SELECTED_UNITS.json")
    split = verify_embedded(AUTHORITY / "V3_SPLIT_MANIFEST.json")
    shortlist = verify_embedded(AUTHORITY / "V3_SHORTLIST.json")
    leakage = verify_embedded(AUTHORITY / "GLOBAL_SPLIT_LEAKAGE_AUDIT.json")
    inventory = verify_embedded(AUTHORITY / "FROZEN_HASH_INVENTORY.json")
    for row in inventory["files"]:
        target = ROOT / row["path"]
        if not target.is_file() or target.stat().st_size != row["bytes"] or sha256_path(target) != row["sha256"]:
            raise DeliveryError("FROZEN_AUTHORITY_MISMATCH:" + row["path"])
    if selected["selected_count"] != 24 or split["split_counts"] != {"DEV": 5, "TEST": 5, "TRAIN": 14}:
        raise DeliveryError("SELECTED_OR_SPLIT_COUNT_MISMATCH")
    if shortlist["shortlist_count"] < 32 or leakage["status"] != "PASS" or not all(leakage["checks"].values()):
        raise DeliveryError("SHORTLIST_OR_LEAKAGE_FAILED")

    stage_a = load(STAGE_A / "BATCH_UNIT_SUMMARY.json")
    stage_a_result = load(STAGE_A / "BATCH_RESULT.json")
    packages = load(STAGE_A / "OBSERVATION_PACKAGE_INDEX.json")
    a_counts = load(STAGE_A / "BATCH_RUNTIME_COUNTS.json")
    if len(stage_a["units"]) != 24 or len({item["unit_id"] for item in stage_a["units"]}) != 24:
        raise DeliveryError("STAGE_A_TERMINAL_ACCOUNTING_MISMATCH")
    if any(item["candidate_forward_count"] or item["second_observation_count"] for item in stage_a["units"]):
        raise DeliveryError("STAGE_A_PROHIBITED_COUNT_NONZERO")
    if a_counts["candidate_forward_total"] or a_counts["second_observation_total"]:
        raise DeliveryError("STAGE_A_BATCH_PROHIBITED_COUNT_NONZERO")
    if packages["eligible_package_count"] != stage_a_result["eligible_count"]:
        raise DeliveryError("ELIGIBLE_PACKAGE_COUNT_MISMATCH")
    recovery_dirs = []
    for item in selected["selected_units"]:
        primary = next(row for row in stage_a["units"] if row["unit_id"] == item["unit_id"])
        run_dir = Path(primary["observation_package_manifest_path"]).parent if primary.get("observation_package_manifest_path") else STAGE_A / "runs" / primary["run_id"]
        lifecycle = load(run_dir / "RUN_RESULT.json")
        if lifecycle.get("terminal") is not True or lifecycle.get("unit_id") != item["unit_id"]:
            raise DeliveryError("STAGE_A_TERMINAL_RESULT_INVALID:" + item["unit_id"])
        recovery_dirs.extend(path.name for path in (STAGE_A / "runs").glob("*UNUSED*") if path.is_dir())
    if recovery_dirs:
        raise DeliveryError("STAGE_A_RECOVERY_DIRECTORY_PRESENT")

    from driveclarify_static_branch.offline_candidate_capture import verify_observation_package

    package_checks = []
    for row in packages["packages"]:
        check = verify_observation_package(
            Path(row["manifest_path"]),
            expected_unit_id=row["unit_id"],
            expected_observation_hash=row["observation_hash"],
        )
        if check["status"] != "PASS":
            raise DeliveryError("OBSERVATION_PACKAGE_INVALID:" + row["unit_id"])
        package_checks.append(row["unit_id"])

    stage_b = load(STAGE_B / "BATCH_UNIT_SUMMARY.json")
    stage_b_result = load(STAGE_B / "BATCH_RESULT.json")
    b_counts = load(STAGE_B / "BATCH_RUNTIME_COUNTS.json")
    dataset = load(AUTHORITY / "M1_REAL_DATASET_V3.json")
    jsonschema.validate(dataset, load(AUTHORITY / "M1_REAL_DATASET_V3_SCHEMA.json"))
    if len(dataset["records"]) != 24 or len(stage_b["units"]) != 24:
        raise DeliveryError("V3_DATASET_OR_SUMMARY_COUNT_MISMATCH")
    complete = [item for item in stage_b["units"] if item["classification"] == "COMPLETE_A3_B3"]
    if len(complete) != stage_b_result["complete_a3_b3_unit_count"]:
        raise DeliveryError("STAGE_B_COMPLETE_COUNT_MISMATCH")
    if any(item["candidate_forward_count"] != 6 for item in complete):
        raise DeliveryError("COMPLETE_UNIT_FORWARD_COUNT_NOT_SIX")
    if b_counts["actual_candidate_forwards"] != 6 * len(complete):
        raise DeliveryError("STAGE_B_TOTAL_FORWARD_COUNT_MISMATCH")
    if any((STAGE_B / "runs" / name).is_dir() for name in [row["recovery"] for row in load(CAMPAIGN / "STAGE_B_RUN_PLAN.json")["unit_runs"]]):
        raise DeliveryError("STAGE_B_RECOVERY_DIRECTORY_PRESENT")
    capture_index = load(STAGE_B / "STAGE_B_CAPTURE_OUTPUT_INDEX.json")
    if capture_index["count"] != len(complete):
        raise DeliveryError("CAPTURE_OUTPUT_COUNT_MISMATCH")
    for row in capture_index["outputs"]:
        value = load(Path(row["path"]))
        jsonschema.validate(value, load(AUTHORITY / "V3_CAPTURE_OUTPUT_SCHEMA.json"))
        if sha256_path(Path(row["path"])) != row["sha256"]:
            raise DeliveryError("CAPTURE_OUTPUT_HASH_MISMATCH:" + row["unit_id"])

    v2 = load(V2_RESULT)
    expected_labels = {
        key: v2["label_distribution"][key] + result["v3_label_distribution"][key]
        for key in ("TASK_EQUIVALENT", "TASK_CRITICAL", "UNKNOWN")
    }
    expected_splits = {
        key: v2["split_distribution"][key] + result["v3_split_complete_distribution"][key]
        for key in ("TRAIN", "DEV", "TEST")
    }
    if result["v2_v3_label_distribution"] != expected_labels or result["v2_v3_split_complete_distribution"] != expected_splits:
        raise DeliveryError("CONSOLIDATED_DISTRIBUTION_MISMATCH")
    if sum(result["v3_label_distribution"].values()) != len(complete) or sum(result["v3_split_complete_distribution"].values()) != len(complete):
        raise DeliveryError("V3_DISTRIBUTION_SUM_MISMATCH")
    if result["engineering_exclusions"] + result["evidence_exclusions"] + len(complete) != 24:
        raise DeliveryError("V3_DISTINCT_OUTCOME_ACCOUNTING_MISMATCH")
    if tree_hash(V2_ROOT) != V2_TREE_SHA256:
        raise DeliveryError("V2_TREE_HASH_CHANGED")
    if result["formal_learned_m1_training_started"] or result["m2_started"] or result["act_ask_wait_started"] or result["automatic_continuation"]:
        raise DeliveryError("PROHIBITED_CONTINUATION_DETECTED")

    inventory_checks = [
        verify_inventory(CAMPAIGN / "ARTIFACT_INVENTORY.json"),
        verify_inventory(STAGE_A / "ARTIFACT_INVENTORY.json"),
        verify_inventory(STAGE_B / "ARTIFACT_INVENTORY.json"),
    ]
    environment = _environment()
    cleanup_pass = (
        not environment["preexisting_real_processes"]
        and not environment["gpu_compute_processes"]
        and all(environment["ports_free"].values())
        and result["cleanup_pass"]
    )
    if not cleanup_pass:
        raise DeliveryError("FINAL_PROCESS_GPU_OR_PORT_CLEANUP_FAILED")
    drive = git_snapshot(ROOT)
    sim = git_snapshot(SIMLINGO)
    if drive["branch"] != "master" or drive["head"] != DRIVE_HEAD or drive["tracked_diff_bytes"] or drive["staged_diff_bytes"]:
        raise DeliveryError("DRIVE_GIT_BOUNDARY_FAILED")
    if sim["branch"] != "main" or sim["head"] != SIM_HEAD or sim["tracked_diff_bytes"] != 7722 or sim["tracked_diff_sha256"] != SIM_DIFF_SHA256 or sim["staged_diff_bytes"]:
        raise DeliveryError("SIMLINGO_GIT_BOUNDARY_FAILED")
    return {
        "schema_version": "driveclarify.m1_v3_independent_review.v1",
        "status": "PASS",
        "checks": {
            "frozen_manifest_schema_hash_inventory": True,
            "shortlist_selected_split": True,
            "global_leakage": True,
            "stage_a_terminal_24_of_24": True,
            "stage_a_candidate_forward_zero": True,
            "stage_a_second_observation_zero": True,
            "eligible_package_integrity": True,
            "stage_b_complete_forward_exactness": True,
            "stage_b_capture_schema": True,
            "dataset_schema_and_distinct_accounting": True,
            "consolidated_distribution": True,
            "v2_tree_unchanged": True,
            "no_training_m2_act_ask_wait_or_continuation": True,
            "process_gpu_ports_cleanup": True,
            "git_boundaries": True,
        },
        "selected": 24,
        "eligible_packages_verified": len(package_checks),
        "complete_units_verified": len(complete),
        "inventories": inventory_checks,
        "environment": environment,
        "git": {"driveclarify": drive, "simlingo": sim},
        "v2_tree_sha256": V2_TREE_SHA256,
    }


def _write_handoffs(result: Mapping[str, Any], review: Mapping[str, Any]) -> None:
    status = result["final_status"]
    readiness = result["formal_learned_m1_readiness"]
    next_step = (
        "Wait for separate explicit authorization of Formal Learned M1 work; do not train automatically."
        if readiness == "READY_FOR_FORMAL_LEARNED_M1_ASSESSMENT"
        else "Wait for separate explicit authorization of label-blind Dataset Expansion V4."
    )
    state_path = ROOT / "STATE.json"
    state = load(state_path)
    state.update(
        {
            "stage": "M1 Real Dataset Expansion V3 one-shot runtime campaign complete; stopped without training or M2+.",
            "status": status,
            "step": "STOP. " + next_step,
            "current_task": "M1_REAL_DATASET_EXPANSION_V3 — complete",
            "next_task": next_step,
        }
    )
    state["m1_real_dataset_expansion_v3"] = {
        "expansion_id": EXPANSION_ID,
        "runtime_campaign_id": CAMPAIGN_ID,
        "status": status,
        "report_directory": AUTHORITY.relative_to(ROOT).as_posix(),
        "shortlist_count": result["shortlist_units"],
        "selected_count": 24,
        "runtime_constructed_count": result["runtime_constructed_units"],
        "eligible_count": result["eligible_units"],
        "complete_a3_b3_count": result["complete_a3_b3_units"],
        "engineering_exclusion_count": result["engineering_exclusions"],
        "evidence_exclusion_count": result["evidence_exclusions"],
        "blocked_count": result["blocked_units"],
        "stage_a_candidate_forward": 0,
        "stage_a_second_observation": 0,
        "stage_b_candidate_forward": result["stage_b_candidate_forward_total"],
        "v3_label_distribution": result["v3_label_distribution"],
        "v2_v3_label_distribution": result["v2_v3_label_distribution"],
        "v3_split_distribution": result["v3_split_complete_distribution"],
        "v2_v3_split_distribution": result["v2_v3_split_complete_distribution"],
        "formal_learned_m1_readiness": readiness,
        "independent_review": review["status"],
        "v2_tree_sha256_unchanged": V2_TREE_SHA256,
        "formal_learned_m1_training_started": False,
        "m2_started": False,
        "act_ask_wait_started": False,
        "automatic_continuation": False,
        "unique_next_step": next_step,
    }
    atomic_json(state_path, state)

    section = """# DriveClarify 当前代理交接

更新时间：2026-08-04  
当前状态：`{status}`

## M1 Real Dataset Expansion V3 — complete and stopped

一次性固定 24-unit、label-blind V3 campaign `{campaign}` 已完成。shortlist/selected=`{shortlist}/24`，
runtime-constructed/eligible/complete=`{constructed}/{eligible}/{complete}`，engineering/evidence/blocked=
`{engineering}/{evidence}/{blocked}`。Stage A candidate forward=0、second observation=0；Stage B candidate
forward=`{forwards}`，每个 complete unit 恰好 6 次。

V3 labels=`{v3_labels}`，V2+V3 formal labels=`{all_labels}`；V3 complete split=`{v3_split}`，
V2+V3 complete split=`{all_split}`。readiness assessment=`{readiness}`，仅为评估，不授权训练。
global leakage、manifest/schema/inventory/hash、独立复核、Git 与 cleanup 全部 PASS；V2 tree hash 仍为
`{v2_hash}`。无历史 rerun、无第二 observation、无训练、无 M2+、无 ACT/ASK/WAIT、无自动继续。

完整证据：`{report}`。唯一下一步：{next_step}
""".format(
        status=status,
        campaign=CAMPAIGN_ID,
        shortlist=result["shortlist_units"],
        constructed=result["runtime_constructed_units"],
        eligible=result["eligible_units"],
        complete=result["complete_a3_b3_units"],
        engineering=result["engineering_exclusions"],
        evidence=result["evidence_exclusions"],
        blocked=result["blocked_units"],
        forwards=result["stage_b_candidate_forward_total"],
        v3_labels=json.dumps(result["v3_label_distribution"], sort_keys=True),
        all_labels=json.dumps(result["v2_v3_label_distribution"], sort_keys=True),
        v3_split=json.dumps(result["v3_split_complete_distribution"], sort_keys=True),
        all_split=json.dumps(result["v2_v3_split_complete_distribution"], sort_keys=True),
        readiness=readiness,
        v2_hash=V2_TREE_SHA256,
        report=AUTHORITY.relative_to(ROOT).as_posix(),
        next_step=next_step,
    )
    previous = (ROOT / "CURRENT_HANDOFF.md").read_text(encoding="utf-8")
    if previous.startswith("# DriveClarify 当前代理交接"):
        previous = previous.split("\n", 1)[1].lstrip()
    atomic_text(ROOT / "CURRENT_HANDOFF.md", section + "\n## 历史交接（只读）\n\n" + previous)
    prompt = """# DriveClarify 下一代理任务

当前权威状态：`{status}`

## 默认动作 — 停止

M1 V3 `{campaign}` 已完整终局并停止；正式 V2+V3 readiness 为 `{readiness}`。本状态不授权训练。
不得重跑任何 V3/V2/Pilot/exclusion unit，不得选择第二 observation，不得改 labels/split/mapper/threshold，
不得进入 M2A/M2B/M3/M4 或启用 ACT/ASK/WAIT。完整证据位于 `{report}`。

唯一下一步：{next_step}
""".format(status=status, campaign=CAMPAIGN_ID, readiness=readiness, report=AUTHORITY.relative_to(ROOT).as_posix(), next_step=next_step)
    atomic_text(ROOT / "NEXT_AGENT_PROMPT.md", prompt)
    with (ROOT / "AGENT_WORKLOG.md").open("a", encoding="utf-8") as stream:
        stream.write("\n\n" + section.replace("# DriveClarify 当前代理交接", "# M1 Real Dataset Expansion V3 — terminal worklog", 1))


def finalize() -> Mapping[str, Any]:
    static = load(AUTHORITY / "TEST_RESULTS.json")
    if static.get("status") != "PASS" or static.get("phase") != "STATIC_PRE_RUNTIME":
        raise DeliveryError("STATIC_TEST_AUTHORITY_MISSING")
    post_commands = [run_command(argv) for argv in test_commands()]
    if any(item["exit_code"] != 0 for item in post_commands):
        raise DeliveryError("POST_RUNTIME_REGRESSION_FAILED")
    review = independent_review()
    result = load(AUTHORITY / "CAMPAIGN_RESULT.json")
    tests = {
        "schema_version": "driveclarify.m1_v3_test_results.v1",
        "expansion_id": EXPANSION_ID,
        "phase": "FINAL_STATIC_AND_POST_RUNTIME",
        "status": "PASS",
        "failed": 0,
        "deselect_used": False,
        "unexplained_failure_count": 0,
        "static_commands": static["commands"],
        "post_runtime_commands": post_commands,
        "independent_review_status": "PASS",
        "v2_tree_sha256": V2_TREE_SHA256,
    }
    atomic_json(AUTHORITY / "TEST_RESULTS.json", tests)
    review_md = """# M1 V3 Independent Read-Only Review

Result: `PASS`.

The independent terminal audit revalidated the frozen manifests, schemas, embedded hashes and inventory; all 24 Stage A terminal results; zero Stage A candidate forwards and second observations; every eligible 14-file observation package; each complete Stage B unit's exact six forwards; capture and dataset schemas; exclusion/label/split accounting; global leakage; consolidated V2+V3 distributions; unchanged V2 tree; Git boundaries; and process/GPU/port cleanup.

No unit was rerun, no recovery directory was used, and no training, M2+, ACT/ASK/WAIT, label mutation, threshold change, or automatic continuation occurred.
"""
    atomic_text(AUTHORITY / "INDEPENDENT_REVIEW.md", review_md)
    source_files = [
        "driveclarify_static_branch/m1_expansion_v3.py",
        "driveclarify_static_branch/m1_v3_runtime_campaign.py",
        "driveclarify_static_branch/run_result_lifecycle.py",
        "driveclarify_static_branch/observation_screening_agent.py",
        "driveclarify_static_branch/observation_screening_batch.py",
        "driveclarify_static_branch/offline_candidate_capture.py",
        "tools/prepare_m1_real_dataset_expansion_v3.py",
        "tools/deliver_m1_real_dataset_expansion_v3.py",
        "tests/m1_real_dataset_expansion_v3/test_m1_real_dataset_expansion_v3.py",
        "tests/observation_screening/test_observation_screening.py",
    ]
    modified = {
        "schema_version": "driveclarify.m1_v3_modified_files.v1",
        "scope": "M1_REAL_DATASET_EXPANSION_V3_ONLY",
        "source_and_tests": [
            {"path": name, "sha256_final": sha256_path(ROOT / name)} for name in source_files
        ],
        "report_directory_added": AUTHORITY.relative_to(ROOT).as_posix(),
        "handoff_files_updated": ["AGENT_WORKLOG.md", "STATE.json", "CURRENT_HANDOFF.md", "NEXT_AGENT_PROMPT.md"],
        "frozen_v1_v2_files_modified": [],
        "simlingo_files_modified": [],
        "mapper_or_threshold_modified": False,
        "destructive_git_command_used": False,
    }
    atomic_json(AUTHORITY / "MODIFIED_FILES.json", modified)
    _write_handoffs(result, review)
    with (AUTHORITY / "COMMAND_LOG.md").open("a", encoding="utf-8") as stream:
        stream.write(
            "- Formal Stage A and eligible-only Stage B completed once; terminal result `{}`.\n"
            "- Post-runtime test bundle PASS; exits={}; independent read-only review PASS.\n"
            "- STOP: no Learned M1 training, M2+, or ACT/ASK/WAIT continuation.\n".format(
                result["final_status"], [item["exit_code"] for item in post_commands]
            )
        )
    provisional = {"schema_version": "driveclarify.m1_v3_git_end.v1", "status": "PENDING_FINAL_SNAPSHOT"}
    atomic_json(AUTHORITY / "GIT_END.json", provisional)
    drive = git_snapshot(ROOT)
    sim = git_snapshot(SIMLINGO)
    git_end = {
        "schema_version": "driveclarify.m1_v3_git_end.v1",
        "status": "PASS_ATTRIBUTED_SCOPE_ONLY",
        "driveclarify": drive,
        "simlingo": {**sim, "unchanged_from_entry": sim["head"] == SIM_HEAD and sim["tracked_diff_bytes"] == 7722 and sim["tracked_diff_sha256"] == SIM_DIFF_SHA256 and sim["staged_diff_bytes"] == 0},
        "v2_expansion_tree_sha256_entry": V2_TREE_SHA256,
        "v2_expansion_tree_sha256_exit": tree_hash(V2_ROOT),
        "destructive_git_command_used": False,
        "git_commit_used": False,
        "simlingo_modified": False,
    }
    if drive["head"] != DRIVE_HEAD or drive["tracked_diff_bytes"] or drive["staged_diff_bytes"] or not git_end["simlingo"]["unchanged_from_entry"] or git_end["v2_expansion_tree_sha256_exit"] != V2_TREE_SHA256:
        raise DeliveryError("FINAL_GIT_OR_V2_INTEGRITY_FAILED")
    atomic_json(AUTHORITY / "GIT_END.json", git_end)
    return {"status": "PASS", "campaign_result": result, "review": review, "tests": tests, "git_end": git_end}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("static-tests", "finalize"))
    args = parser.parse_args(list(argv) if argv is not None else None)
    value = static_tests() if args.command == "static-tests" else finalize()
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
