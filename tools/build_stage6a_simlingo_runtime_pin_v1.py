#!/usr/bin/env python3
"""Build the Stage 6A SimLingo runtime pin evidence package."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from driveclarify_paper_mvp_stage6a.runtime_pin import (
    EXPECTED_STAGE6A_PROTECTED_DIFF_SHA256,
    HISTORICAL_PROTECTED_DIFF_SHA256,
    assert_expected_stage6a_capture,
    canonical_json_bytes,
    capture_simlingo_runtime,
    sha256_bytes,
    sha256_file,
)


DEFAULT_SIMLINGO_ROOT = Path("/home/buaa/wrh/simlingo")
DEFAULT_CHECKPOINT = Path(
    "/home/buaa/wrh/simlingo/outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
)
DEFAULT_OUTPUT = REPO_ROOT / "reports/paper_mvp_stage6a_simlingo_runtime_pin_v1"


SOURCE_EVIDENCE = {
    "historical_cp0_source_snapshot": "reports/cp3b_additional_evidence_targeted_plan_review/PROTECTED_END_SNAPSHOT.json",
    "candidate_authority_source_audit": "reports/candidate_live_act_authority_extension_v0/DC-CANDIDATE-ACT-AUTH-V0-20260809T064552Z/SINGLE_PID_EXECUTION_SEAM_AUDIT.json",
    "limited_act_preflight": "reports/limited_closed_loop_act_commit_v0/DC-LIMITED-ACT-COMMIT-V0-20260809T071608Z/PREFLIGHT_AND_TESTS.json",
    "limited_act_final_report": "reports/limited_closed_loop_act_commit_v0/DC-LIMITED-ACT-COMMIT-V0-20260809T071608Z/FINAL_REPORT.md",
    "limited_act_git_start_end": "reports/limited_closed_loop_act_commit_v0/DC-LIMITED-ACT-COMMIT-V0-20260809T071608Z/GIT_START_END.json",
    "stage5_runtime_validation": "reports/paper_mvp_scenario_freeze_v0/VALIDATION_REPORT.json",
    "stage5_evidence_index": "reports/paper_mvp_scenario_freeze_v0/EVIDENCE_INDEX.json",
    "stage6a_recovery_blockers": "reports/paper_mvp_stage6a_implementation_freeze_v1/STAGE6A_EXECUTION_BLOCKERS.md",
    "scenario_live_receipt": "artifacts/paper_mvp_stage6a/live_execution_v1/SCENARIO_RUNNER_EXECUTION_RECEIPT.json",
    "candidate_live_receipt": "artifacts/paper_mvp_stage6a/candidate_gate_v1/candidate_generation_audit.json",
    "authority_live_receipt": "artifacts/paper_mvp_stage6a/live_authority_v1/stage6a/stage6a_live_binding_audit.json",
    "implementation_freeze_report": "reports/paper_mvp_stage6a_implementation_freeze_v1/STAGE6A_IMPLEMENTATION_FREEZE_REPORT.json",
    "baseline_config": "reports/paper_mvp_stage6a_implementation_freeze_v1/BASELINE_CONFIG.yaml",
    "baseline_hashes": "reports/paper_mvp_stage6a_implementation_freeze_v1/BASELINE_HASHES.json",
}


EXPECTED_RECEIPTS = {
    "scenario_live_receipt": "9b48050a0b0660c0f3477729df32bbc2b2b1bbb63d22b3d1d2caeb0d9d7e3528",
    "candidate_live_receipt": "ce3c2a3b62b755eece214481da1aa454a797b5fd117864bd12a2a030ee970070",
    "authority_live_receipt": "6cc0e8e3a0687b933fcba2adca9c9071c842e06dc2b4957e7b5b4aa289d1109d",
}


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def artifact_record(path: Path, logical_path: str) -> Dict[str, Any]:
    return {
        "path": logical_path,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def ensure_source_evidence() -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for name, relative in SOURCE_EVIDENCE.items():
        path = REPO_ROOT / relative
        if not path.is_file():
            raise RuntimeError("缺少 source-of-truth evidence: {}".format(path))
        record = artifact_record(path, relative)
        if name in EXPECTED_RECEIPTS and record["sha256"] != EXPECTED_RECEIPTS[name]:
            raise RuntimeError(
                "live receipt hash 漂移: {} actual={} expected={}".format(
                    relative, record["sha256"], EXPECTED_RECEIPTS[name]
                )
            )
        records[name] = record
    return records


def reconciliation_markdown(capture: Dict[str, Any], evidence: Dict[str, Any]) -> str:
    repo = capture["repository"]
    key = capture["runtime_key_files"]
    return """# SimLingo 指纹来源协调（Stage 6A-R2）

## 结论

主分类：`C. LEGITIMATE_VERSIONED_RUNTIME_EVOLUTION`。

`7bd0947ef17df95b56d95ce155468d378a3c282b59f7dee4a2653a7195bc5e34` 与
`dad60227cada5a17ba336881e583480e83eb272331e72be3c146ec96abe2d058` 使用完全相同的定义：
对 `git diff --binary` 的原始 stdout 字节做 SHA-256。差异不是 hash scope 或算法变化；它是经过现有
DriveClarify bounded shadow / WAIT / ACT 工作演进后的版本化 runtime 状态。相对本轮 Stage 6A 接管点，
该状态同时属于 `A. VERIFIED_PREEXISTING_PROTECTED_SIMLINGO_DIRTY_STATE`，因为本轮只读取证未改
SimLingo，且 Stage 5 已在 Stage 6A 前冻结同一个 `dad60227…` 状态。

明确约束：`HISTORICAL_PIN != CURRENT_RUNTIME_PIN`。历史 artifact 未被改写。

## 八个取证问题

1. **`7bd0947e…` 产生阶段。** 它最晚在 CP3B/候选稳定性前后的历史冻结工件中稳定出现，并继续被
   Stage 3、Stage 4 与旧回归契约继承。它是历史 runtime pin，不是当前 Stage 6A pin。
2. **精确定义。** `sha256(raw stdout bytes of git diff --binary)`；历史记录中的 `7722`/当前记录中的
   `9276` 是字节数，不是额外 hash。
3. **`dad60227…` 改动范围。** 相对 HEAD `{head}`，共有 {tracked_count} 个 tracked 文件：
   `{tracked_files}`。完整逐字节 diff 保存在 `SIMLINGO_PROTECTED_DIFF.patch`。
4. **首次出现时间。** 前四个 launcher/environment 改动在 CP0 前已存在且持续不变；
   `agent_simlingo.py` 的 CP0 default-OFF observer 版本可重构为
   `78c959db85a26acbcfb44d65894c2439dfc788c4b5226a7a11d45835c485910f`；候选/M2B 只读观察扩展
   在 2026-08-09 的 authority 工作前已形成
   `adf52f1842db5bf7445fb1618096a31cc7b4a1a414a65d72e097c5fad72a785d`；随后
   `DC-LIMITED-ACT-COMMIT-V0-20260809T071608Z` 增加 default-OFF 的 pre-PID plan selector、PID observer
   与 destroy 兼容处理，得到当前 `{agent_sha}`。
5. **是否早于当前 Stage 6A。** 是。Stage 5 的 `VALIDATION_REPORT.json` 已同时验证 HEAD、
   `dad60227…` 和 `{agent_sha}`；Stage 6A recovery 明确记录未改 SimLingo。
6. **各原型实际状态。** 旧 closed-loop/候选稳定性冻结仍属于历史 `7bd0947e…` 合同；CP0 observer
   使用 `78c959…` 源文件状态；WAIT/ASK 与 authority extension 已处于扩展 runtime；limited ACT 产生
   当前 agent 状态；Stage 5 和本轮 Stage 6A live gates 使用当前 `dad60227…` 状态。
7. **Stage 6A live evidence 是否建立在 `dad60227…`。** 是，依据 Stage 5 前置 runtime validation、
   Stage 6A 无 SimLingo 写入记录和文件时间线。绑定强度需精确表述：authority receipt 还直接绑定当前
   `LingoAgent.run_step` 源码切片 hash `ef2b823112b845d65f89da2577f8160f11168a714b1b1c05df7e2c2f8bf749a1`；
   scenario/candidate receipt 本身没有内嵌完整 worktree hash，因此它们依靠上述前置 pin + 无写入链，
   不能被描述成各自独立地 hash 了全部 SimLingo worktree。
8. **是否存在不可归属字节。** protected tracked diff 中没有。所有 diff hunk 均落入前置 launcher/environment
   适配、default-OFF probe、只读候选/M2B 观察、bounded ACT pre-PID seam 或 cleanup compatibility。
   {untracked_count} 个 untracked 文件也已逐文件列名、字节数、mtime 与 SHA-256：runtime hook 单独归入
   DriveClarify 版本化 probe，其余为接管前的配置/文档或历史 SimLingo runtime 输出；它们未被伪装成
   tracked protected source。

## 当前运行时 pin

- branch: `{branch}`
- HEAD: `{head}`
- protected diff: `{diff_bytes}` bytes / `{diff_sha}`
- staged diff: `{staged_bytes}` bytes / `{staged_sha}`
- `team_code/agent_simlingo.py`: `{agent_sha}`
- `team_code/driveclarify_probe_hook.py`: `{hook_sha}`
- checkpoint: `{checkpoint_sha}`
- 完整 runtime-state aggregate: `{aggregate_sha}`

## Live receipt 绑定

- Scenario: `{scenario_sha}`，24/24 executable；与当前 pin 为前置 runtime-freeze + 无写入时间链绑定。
- Candidate: `{candidate_sha}`，24/24、K=2；与当前 pin 为前置 runtime-freeze + 无写入时间链绑定。
- Authority: `{authority_sha}`；除时间链外，直接绑定当前 `run_step` source hash。
- 本次协调只增加 DriveClarify manifest、验证器和测试，不修改 SimLingo runtime behavior source，故无需
  重跑已通过的 native CARLA gate；后续 Stage 6B 启动前必须验证完整 current pin。
""".format(
        head=repo["head"],
        tracked_count=repo["modified_tracked_file_count"],
        tracked_files="`, `".join(item["path"] for item in repo["modified_tracked_files"]),
        agent_sha=key["team_code/agent_simlingo.py"]["sha256"],
        hook_sha=key["team_code/driveclarify_probe_hook.py"]["sha256"],
        untracked_count=repo["untracked_inventory"]["count"],
        branch=repo["branch"],
        diff_bytes=repo["protected_tracked_diff_bytes"],
        diff_sha=repo["protected_tracked_diff_sha256"],
        staged_bytes=repo["staged_diff_bytes"],
        staged_sha=repo["staged_diff_sha256"],
        checkpoint_sha=capture["checkpoint"]["sha256"],
        aggregate_sha=capture["pin_contract"]["runtime_state_aggregate_sha256"],
        scenario_sha=evidence["scenario_live_receipt"]["sha256"],
        candidate_sha=evidence["candidate_live_receipt"]["sha256"],
        authority_sha=evidence["authority_live_receipt"]["sha256"],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simlingo-root", type=Path, default=DEFAULT_SIMLINGO_ROOT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    evidence = ensure_source_evidence()
    capture = capture_simlingo_runtime(args.simlingo_root, args.checkpoint)
    assert_expected_stage6a_capture(capture)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    fingerprint_path = output / "SIMLINGO_RUNTIME_FINGERPRINT.json"
    patch_path = output / "SIMLINGO_PROTECTED_DIFF.patch"
    reconciliation_path = output / "SIMLINGO_FINGERPRINT_RECONCILIATION.md"
    frozen_path = output / "FROZEN_SIMLINGO_RUNTIME_STAGE6A_V1.json"
    hashes_path = output / "SIMLINGO_RUNTIME_PIN_HASHES.json"
    evidence_index_path = output / "RUNTIME_PIN_EVIDENCE_INDEX.json"

    write_json(fingerprint_path, capture)
    protected_diff = __import__("subprocess").check_output(
        ["git", "-C", str(args.simlingo_root.resolve()), "diff", "--binary"],
        env={**__import__("os").environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if sha256_bytes(protected_diff) != EXPECTED_STAGE6A_PROTECTED_DIFF_SHA256:
        raise RuntimeError("写 patch 前 protected diff 漂移")
    patch_path.write_bytes(protected_diff)
    reconciliation_path.write_text(
        reconciliation_markdown(capture, evidence), encoding="utf-8"
    )

    frozen = {
        "schema_version": "driveclarify.frozen_simlingo_runtime.stage6a.v1",
        "generated_at_utc": capture["generated_at_utc"],
        "classification": capture["classification"],
        "historical_integrity": {
            "pin": HISTORICAL_PROTECTED_DIFF_SHA256,
            "definition": "sha256(raw stdout bytes of `git diff --binary`)",
            "must_remain_unchanged": True,
        },
        "current_runtime_integrity": {
            "pin": EXPECTED_STAGE6A_PROTECTED_DIFF_SHA256,
            "definition": "sha256(raw stdout bytes of `git diff --binary`)",
            "runtime_state_aggregate_sha256": capture["pin_contract"]["runtime_state_aggregate_sha256"],
            "historical_pin_not_equal_current_runtime_pin": True,
        },
        "repository": capture["repository"],
        "runtime_key_files": capture["runtime_key_files"],
        "checkpoint": capture["checkpoint"],
        "live_receipt_bindings": {
            "scenario": {
                **evidence["scenario_live_receipt"],
                "binding": "STAGE5_FULL_RUNTIME_PIN_THEN_STAGE6A_NO_SIMLINGO_WRITE",
            },
            "candidate": {
                **evidence["candidate_live_receipt"],
                "binding": "STAGE5_FULL_RUNTIME_PIN_THEN_STAGE6A_NO_SIMLINGO_WRITE",
            },
            "authority": {
                **evidence["authority_live_receipt"],
                "binding": "TEMPORAL_FULL_RUNTIME_PROOF_PLUS_DIRECT_RUN_STEP_SOURCE_HASH",
                "run_step_source_sha256": "ef2b823112b845d65f89da2577f8160f11168a714b1b1c05df7e2c2f8bf749a1",
            },
        },
    }
    write_json(frozen_path, frozen)

    primary_paths = [fingerprint_path, reconciliation_path, patch_path, frozen_path]
    primary_records = {
        path.name: artifact_record(path, str(path.relative_to(REPO_ROOT)))
        for path in primary_paths
    }
    primary_aggregate_inputs = {
        name: {"bytes": record["bytes"], "sha256": record["sha256"]}
        for name, record in sorted(primary_records.items())
    }
    hash_manifest = {
        "schema_version": "driveclarify.simlingo.runtime_pin_hashes.v1",
        "generated_at_utc": capture["generated_at_utc"],
        "historical_pin": HISTORICAL_PROTECTED_DIFF_SHA256,
        "current_runtime_pin": EXPECTED_STAGE6A_PROTECTED_DIFF_SHA256,
        "historical_pin_not_equal_current_runtime_pin": True,
        "runtime_state_aggregate_sha256": capture["pin_contract"]["runtime_state_aggregate_sha256"],
        "artifacts": primary_records,
        "artifact_set_sha256": sha256_bytes(canonical_json_bytes(primary_aggregate_inputs)),
    }
    write_json(hashes_path, hash_manifest)

    evidence_index = {
        "schema_version": "driveclarify.simlingo.runtime_pin_evidence_index.v1",
        "generated_at_utc": capture["generated_at_utc"],
        "status": "PASS_VERSIONED_RUNTIME_PIN_RECONCILED",
        "classification": capture["classification"],
        "answers": {
            "same_hash_algorithm": True,
            "historical_pin_preserved": True,
            "current_pin_frozen": True,
            "all_protected_diff_bytes_attributed": True,
            "all_stage6a_live_evidence_on_current_runtime": True,
            "receipt_binding_scope_caveat_recorded": True,
            "simlingo_modified_by_capture": False,
        },
        "generated_artifacts": {
            **primary_records,
            hashes_path.name: artifact_record(
                hashes_path, str(hashes_path.relative_to(REPO_ROOT))
            ),
        },
        "source_evidence": evidence,
    }
    write_json(evidence_index_path, evidence_index)

    print(json.dumps({
        "status": evidence_index["status"],
        "output": str(output),
        "historical_pin": HISTORICAL_PROTECTED_DIFF_SHA256,
        "current_runtime_pin": EXPECTED_STAGE6A_PROTECTED_DIFF_SHA256,
        "runtime_state_aggregate_sha256": capture["pin_contract"]["runtime_state_aggregate_sha256"],
        "protected_diff_bytes": capture["repository"]["protected_tracked_diff_bytes"],
        "untracked_count": capture["repository"]["untracked_inventory"]["count"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
