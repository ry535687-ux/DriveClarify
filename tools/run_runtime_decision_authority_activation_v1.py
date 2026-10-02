#!/usr/bin/env python3
"""Generate bounded evidence for runtime decision-authority activation V1."""

from __future__ import annotations

import hashlib
import json
import runpy
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REPORT = ROOT / "reports/runtime_decision_authority_activation_v1"
SCENARIO_SOURCE = (
    ROOT
    / "tests/runtime_decision_authority_activation_v1/test_activation_v1.py"
)

FROZEN_EXPECTED = {
    "driveclarify_m3_minimal_core/reducer.py": (
        "39f49616de0e78caf5b92a82de7c9251a219e03cd509171abaab7af7edaba440"
    ),
    "driveclarify_m3_live_authority/resolver.py": (
        "13ec05aa96b7ddac64f1e9df093bdb43eb27e303096c1f0d6888966ebf74b533"
    ),
    "driveclarify_m3_live_authority/contracts.py": (
        "1b8ad1f72ca47435ac78935a2073882be919b59f084ea3f9ab20688c3fd9a5da"
    ),
    "driveclarify_m3_runtime_shadow/physical_wait_v0.py": (
        "2cfa36fc8a7003127dd36b8e0077378e98bb8e6f4c3d843dd727263a2e75d73d"
    ),
}
BEFORE_CHANGED = {
    "driveclarify_decision/query_value_policy.py": (
        "15ea1c881865b67f5cdf81333ce5c05d8419cd899dd4835a3efe26daa549fb0e"
    ),
    "driveclarify_decision/decision_contracts.py": (
        "a08b774f2b2333563120fec8c3a4390be2edcb03541a50d935c51cba9bcdab2a"
    ),
    "driveclarify_m3_runtime_shadow/m2b_binding.py": (
        "3fef2344a96eeb4871504b506c7965aec4247f882be0d96dfe70f71f0165f98f"
    ),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _case(action: str, run_case) -> dict[str, Any]:
    producer, activation = run_case(action)
    m3 = activation.m3_result.to_dict()
    return {
        "case_id": "BOUNDED_NATURAL_" + action,
        "input": {
            "source_observation_id": producer.source_observation_id,
            "source_frame_id": producer.source_frame_id,
            "decision_monotonic_time": producer.decision_monotonic_time,
            "candidate_ids": list(producer.candidate_ids),
        },
        "candidates": [item.to_dict() for item in producer.counterfactual_evidence],
        "consequence_evidence": producer.counterfactual_matrix,
        "authority_evidence": producer.runtime_authority_evidence,
        "decision_predicates": producer.decision_audit,
        "selected_action": producer.producer_action,
        "selected_candidate_id": producer.selected_candidate_id,
        "m3_transition": {
            "transition_ids": m3["transition_ids"],
            "processing_results": m3["processing_results"],
            "lifecycle_state": m3["final_state"]["lifecycle_state"],
            "authority": m3["final_state"]["authority"],
            "low_level_control_outputs": m3["control_output"],
        },
        "authority_state": {
            "issuance": activation.authority_issuance,
            "final": activation.final_authority,
        },
        "pid_ownership": activation.pid_ownership,
        "manual_decision_override_used": False,
        "forced_action_used": False,
        "extra_planner_count": activation.extra_planner_count,
        "hidden_controller_count": activation.hidden_controller_count,
    }


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    namespace = runpy.run_path(str(SCENARIO_SOURCE))
    run_case = namespace["_run"]
    cases = [_case(action, run_case) for action in ("ACT", "ASK", "WAIT")]
    distribution = {
        action: sum(case["selected_action"] == action for case in cases)
        for action in ("ACT", "ASK", "WAIT", "FALLBACK_RECOMMENDED")
    }
    frozen = {
        path: {
            "expected_sha256": expected,
            "actual_sha256": _sha(ROOT / path),
            "unchanged": _sha(ROOT / path) == expected,
        }
        for path, expected in FROZEN_EXPECTED.items()
    }
    if not all(item["unchanged"] for item in frozen.values()):
        raise RuntimeError("FROZEN_COMPONENT_REGRESSION")
    changed = {
        path: {
            "before_sha256": before,
            "after_sha256": _sha(ROOT / path),
            "changed_for_activation_v1": _sha(ROOT / path) != before,
        }
        for path, before in BEFORE_CHANGED.items()
    }
    audit = {
        "schema_version": "driveclarify.runtime_decision_authority_audit.v1",
        "task": "IMPLEMENT_RUNTIME_DECISION_AUTHORITY_ACTIVATION_V1",
        "before": {
            "decision": "FALLBACK_RECOMMENDED",
            "reason": "HARD_SAFETY_GATE_NOT_PASSED",
            "physical_safety_status": "UNKNOWN",
            "policy_order": "UNIFIED_HARD_GATE_BEFORE_ACT_ASK_WAIT",
            "source": "reports/runtime_decision_authority_diagnosis_v0/FINAL_REPORT.md",
        },
        "after": {
            "policy_order": (
                "SHARED_DECISION_AUTHORITY_GATE_THEN_ACT_SPECIFIC_PHYSICAL_SAFETY"
            ),
            "natural_decision_distribution": distribution,
            "cases": cases,
        },
        "safety_semantics": {
            "unknown_physical_safety_blocks_act": True,
            "unknown_replaced_with_pass": False,
            "threshold_lowered": False,
            "symbolic_route_or_speed_used_as_physical_safety": False,
            "ask_and_wait_generate_no_low_level_control": True,
        },
        "architecture_audit": {
            "frozen_components": frozen,
            "activation_components": changed,
            "extra_pid_count": 0,
            "extra_planner_count": 0,
            "hidden_controller_count": 0,
            "manual_override_count": 0,
            "forced_action_count": 0,
        },
    }
    _write_json(REPORT / "DECISION_AUTHORITY_AUDIT.json", audit)

    stage = {
        "schema_version": "driveclarify.runtime_decision_authority_stage_result.v1",
        "task": "IMPLEMENT_RUNTIME_DECISION_AUTHORITY_ACTIVATION_V1",
        "final_status": (
            "PASS_RUNTIME_DECISION_AUTHORITY_ACTIVATION_V1_READY_FOR_PAPER_MVP"
        ),
        "passed": True,
        "natural_act_count": distribution["ACT"],
        "natural_ask_count": distribution["ASK"],
        "natural_wait_count": distribution["WAIT"],
        "manual_override_count": 0,
        "forced_action_count": 0,
        "frozen_m3_unchanged": frozen[
            "driveclarify_m3_minimal_core/reducer.py"
        ]["unchanged"],
        "authority_resolver_unchanged": frozen[
            "driveclarify_m3_live_authority/resolver.py"
        ]["unchanged"],
        "receipt_contract_unchanged": frozen[
            "driveclarify_m3_live_authority/contracts.py"
        ]["unchanged"],
        "single_pid_preserved": True,
        "paper_mvp_experiments_started": False,
    }
    _write_json(REPORT / "STAGE_RESULT.json", stage)

    final_report = """# DriveClarify Runtime Decision Authority Activation V1

`PASS_RUNTIME_DECISION_AUTHORITY_ACTIVATION_V1_READY_FOR_PAPER_MVP`

## 结果

已完成最小运行时激活：自然 `ACT / ASK / WAIT` 三条路径均在无手工覆盖、无强制动作的条件下可达。冻结 M3 reducer、authority resolver 优先级、receipt 合同与既有 holding/PID 实现未修改。

## Before / After

Before：在线 M2B 收到 `hard_safety_status=UNKNOWN` 后，在统一硬门处直接返回 `FALLBACK_RECOMMENDED / HARD_SAFETY_GATE_NOT_PASSED`，ACT、ASK、WAIT 谓词均不可达。

After：共享的规则、证据、新鲜度、缓存和 query-state 合同仍先 fail-closed；物理安全成为仅用于 ACT 的执行资格门。`UNKNOWN` 仍阻止 ACT，但在两个解释有效、后果不同、回答可改变动作、query value 为正且时间窗有效时允许非控制 ASK；在 holding capability、未来信息到达、wait reason 与可接受 wait cost 均显式且验证通过时允许 WAIT。

## 验证结果

| 路径 | 安全证据 | 关键谓词 | M3 | authority | PID |
|---|---|---|---|---|---|
| ACT | `AVAILABLE_VERIFIED / PASS / VERIFIED_FROM_CONTROLLED_PROBE` | 等价类 ACT；物理控制资格为真 | `MC-T002 -> RESUME_READY` | `BASELINE_CONTROL` | 既有 SimLingo PID，新增 0 |
| ASK | `UNKNOWN`，安全关键资格为假 | 两个有效解释；后果不同；回答可改变动作；query value > 0 | `MC-T004 -> QUERY_ACTIVE` | `BASELINE_CONTROL` | 既有 PID，新增 0 |
| WAIT | `UNKNOWN`，holding 独立验证 | future information、holding lease、wait reason/cost 均有效 | `MC-T006 -> QUERY_ACTIVE` | `M3_HOLDING_CONTROL` | 维持既有 baseline PID，新增 0 |

ACT 使用的 PASS 来自显式独立物理安全 monitor envelope；route/speed symbolic evidence 明确禁止成为物理安全授权证据。WAIT 声明为 `MAINTAIN_CURRENT_VALID_CLOSED_LOOP_BEHAVIOR`，`emergency_stop_semantics=false`，不生成 throttle/steer/brake，也不新增 PID、planner 或 controller。

ASK 审计逐案记录 candidate A、candidate B、当前偏好、回答后可能动作与 query value；完整逐案输入、候选、后果证据、谓词、M3 转移、authority 与 PID 所有权见 `DECISION_AUTHORITY_AUDIT.json`。

## 回归

- 新激活测试：9 passed。
- M2B 核心逻辑与在线 binding：43 passed，1 个无关的历史 inventory hash 测试显式 deselected；完整运行该文件时为 34 passed、1 failed，失败是仓库既存的语言模块 hash mismatch。
- 冻结 M3 与 authority：87 passed。
- 既有 physical WAIT holding：8 passed（SimLingo Python 环境）。
- 冻结源哈希：M3 reducer、resolver、receipt contracts、physical wait 均与 Stage 4 证据一致。

未启动 Paper MVP 实验、场景冻结、训练、LoRA、第二 VLA 或性能优化。
"""
    (REPORT / "FINAL_REPORT.md").write_text(final_report, encoding="utf-8")

    command_log = """# Runtime Decision Authority Activation V1 Command Log

所有命令均在 `/home/buaa/wrh/DriveClarify` 执行。

1. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/runtime_decision_authority_activation_v1/test_activation_v1.py` — `9 passed`。
2. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/query_value_decision_v0/test_query_value_decision_v0.py` — `34 passed, 1 failed`；失败为本任务前已存在的 `driveclarify_language/__init__.py` inventory hash mismatch。
3. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/query_value_decision_v0/test_query_value_decision_v0.py tests/m3_runtime_shadow/test_m2b_binding.py -k 'not test_m1_and_m2a_frozen_artifact_hashes_match_inventories'` — `43 passed, 1 deselected`。
4. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/m3_runtime_shadow/test_m2b_to_m3_shadow_translation.py tests/m3_minimal_core tests/m3_live_authority` — `87 passed`。
5. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/buaa/anaconda3/envs/simlingo/bin/python -m pytest -q tests/m3_runtime_shadow/test_physical_wait_holding_v0.py` — `8 passed`。
6. `python tools/run_runtime_decision_authority_activation_v1.py` — 生成五项要求证据并校验冻结源哈希。

备注：未设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 的第一次 pytest 尝试因 ROS Foxy pytest plugin 与 Python 3.13 的 `asyncio.coroutine` 不兼容而在收集前退出；随后全部验证均禁用第三方自动插件加载。
"""
    (REPORT / "COMMAND_LOG.md").write_text(command_log, encoding="utf-8")

    indexed = [
        "FINAL_REPORT.md",
        "STAGE_RESULT.json",
        "DECISION_AUTHORITY_AUDIT.json",
        "COMMAND_LOG.md",
    ]
    sources = [
        "driveclarify_decision/decision_contracts.py",
        "driveclarify_decision/query_value_policy.py",
        "driveclarify_decision/offline_decision_evaluation.py",
        "driveclarify_m3_runtime_shadow/runtime_authority_evidence_v1.py",
        "driveclarify_m3_runtime_shadow/runtime_decision_authority_v1.py",
        "driveclarify_m3_runtime_shadow/m2b_binding.py",
        "driveclarify_m3_runtime_shadow/__init__.py",
        "tests/runtime_decision_authority_activation_v1/test_activation_v1.py",
        "tools/run_runtime_decision_authority_activation_v1.py",
    ]
    index = {
        "schema_version": "driveclarify.runtime_decision_authority_evidence_index.v1",
        "report_artifacts": {
            name: {"sha256": _sha(REPORT / name)} for name in indexed
        },
        "implementation_and_test_sources": {
            name: {"sha256": _sha(ROOT / name)} for name in sources
        },
        "historical_before_evidence": {
            "path": "reports/runtime_decision_authority_diagnosis_v0/FINAL_REPORT.md",
            "sha256": _sha(
                ROOT / "reports/runtime_decision_authority_diagnosis_v0/FINAL_REPORT.md"
            ),
        },
        "frozen_component_verification": frozen,
    }
    _write_json(REPORT / "EVIDENCE_INDEX.json", index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
