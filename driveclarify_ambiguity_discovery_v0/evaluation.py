"""Reproducible diagnostic evaluation and requested artifact generation."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import DESIGNATION, SCHEMA_VERSION
from .discovery import AmbiguityDiscoveryPipeline


DIAGNOSTIC_SET_SCHEMA = "driveclarify.ambiguity_discovery_diagnostic_set_v0.v0"
DIAGNOSTIC_GOLD_SCHEMA = "driveclarify.ambiguity_discovery_diagnostic_gold_v0.v0"


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("DIAGNOSTIC_JSON_ROOT_MUST_BE_OBJECT")
    return value


def load_diagnostic_inputs(path: str | Path) -> list[dict[str, Any]]:
    value = _load_json(path)
    if value.get("schema_version") != DIAGNOSTIC_SET_SCHEMA:
        raise ValueError("BAD_DIAGNOSTIC_INPUT_SCHEMA")
    cases = value.get("cases")
    if not isinstance(cases, list) or not 20 <= len(cases) <= 50:
        raise ValueError("DIAGNOSTIC_SET_REQUIRES_20_TO_50_CASES")
    ids = [str(item.get("case_id", "")) for item in cases if isinstance(item, Mapping)]
    if len(ids) != len(cases) or any(not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("DIAGNOSTIC_CASE_IDS_INVALID")
    return [copy.deepcopy(dict(item)) for item in cases]


def load_diagnostic_gold(path: str | Path) -> dict[str, dict[str, Any]]:
    value = _load_json(path)
    if value.get("schema_version") != DIAGNOSTIC_GOLD_SCHEMA:
        raise ValueError("BAD_DIAGNOSTIC_GOLD_SCHEMA")
    labels = value.get("labels")
    if not isinstance(labels, list):
        raise ValueError("DIAGNOSTIC_GOLD_LABELS_MISSING")
    result: dict[str, dict[str, Any]] = {}
    for item in labels:
        if not isinstance(item, Mapping):
            raise ValueError("DIAGNOSTIC_GOLD_LABEL_INVALID")
        case_id = str(item.get("case_id", ""))
        if not case_id or case_id in result:
            raise ValueError("DIAGNOSTIC_GOLD_CASE_ID_INVALID")
        result[case_id] = copy.deepcopy(dict(item))
    return result


def evaluate(
    cases: Sequence[Mapping[str, Any]],
    gold: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pipeline = AmbiguityDiscoveryPipeline(enable_consequence_handoff=True)
    rows: list[dict[str, Any]] = []
    demos: list[dict[str, Any]] = []
    tp = tn = fp = fn = 0
    recalled = expected_total = 0
    slot_correct = slot_total = 0
    reason_correct = 0
    fail_closed_handoffs = 0
    for case in cases:
        case_id = str(case["case_id"])
        if case_id not in gold:
            raise ValueError(f"GOLD_LABEL_MISSING:{case_id}")
        label = gold[case_id]
        result = pipeline.discover(str(case.get("instruction", "")), case.get("environment", []))
        expected_detected = label.get("ambiguity_detected") is True
        actual_detected = result.ambiguity_detected
        if expected_detected and actual_detected:
            tp += 1
        elif expected_detected:
            fn += 1
        elif actual_detected:
            fp += 1
        else:
            tn += 1
        expected_candidates = {str(item) for item in label.get("candidate_entity_ids", [])}
        predicted_candidates = {item.entity_id for item in result.candidate_entities}
        if expected_detected:
            expected_total += len(expected_candidates)
            recalled += len(expected_candidates.intersection(predicted_candidates))
            slot_total += 1
            slot_correct += int(result.unresolved_slot == label.get("unresolved_slot"))
        reason_correct += int(
            not actual_detected or result.reason == "multiple compatible grounding candidates"
        )
        handoff_status = result.consequence_handoff.get("status")
        if expected_detected and handoff_status == "PASSED_FAIL_CLOSED":
            matrix = result.consequence_handoff.get("matrix", {})
            cells = matrix.get("cells", []) if isinstance(matrix, Mapping) else []
            if cells and all(cell.get("task_outcome") == "UNKNOWN" for cell in cells):
                fail_closed_handoffs += 1
        row = {
            "case_id": case_id,
            "expected_ambiguity_detected": expected_detected,
            "actual_ambiguity_detected": actual_detected,
            "detection_correct": expected_detected == actual_detected,
            "expected_unresolved_slot": label.get("unresolved_slot"),
            "actual_unresolved_slot": result.unresolved_slot,
            "expected_candidate_entity_ids": sorted(expected_candidates),
            "actual_candidate_entity_ids": sorted(predicted_candidates),
            "candidate_recalled_count": len(expected_candidates.intersection(predicted_candidates)),
            "confidence": result.confidence,
            "reason": result.reason,
            "consequence_handoff_status": handoff_status,
        }
        rows.append(row)
        demo = {"case_id": case_id, **result.to_dict(), "diagnostic_evaluation": row}
        demos.append(demo)
    if set(gold) != {str(case["case_id"]) for case in cases}:
        raise ValueError("INPUT_AND_GOLD_CASE_SETS_DIFFER")
    count = len(cases)
    negatives = tn + fp
    positives = tp + fn
    metrics = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_name": "AMBIGUITY_DISCOVERY_PROTOTYPE_V0_DIAGNOSTIC",
        "designation": list(DESIGNATION),
        "diagnostic_case_count": count,
        "positive_case_count": positives,
        "negative_case_count": negatives,
        "ambiguity_detection_accuracy": round((tp + tn) / count, 6),
        "candidate_recall": round(recalled / expected_total, 6) if expected_total else None,
        "false_ambiguity_rate": round(fp / negatives, 6) if negatives else None,
        "ambiguous_slot_accuracy": round(slot_correct / slot_total, 6) if slot_total else None,
        "required_reason_exact_rate": round(reason_correct / count, 6),
        "consequence_handoff_fail_closed_rate": round(fail_closed_handoffs / positives, 6)
        if positives
        else None,
        "confusion_matrix": {
            "true_positive": tp,
            "true_negative": tn,
            "false_positive": fp,
            "false_negative": fn,
        },
        "candidate_recall_counts": {
            "recalled": recalled,
            "expected": expected_total,
        },
        "confidence_definition": (
            "deterministic rule-support score for the reported detection decision; "
            "not a learned or calibrated probability"
        ),
        "rows": rows,
    }
    canonical = json.dumps(metrics, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    metrics["metrics_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return metrics, demos


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _final_report(metrics: Mapping[str, Any]) -> str:
    confusion = metrics["confusion_matrix"]
    return f"""# Ambiguity Discovery Prototype v0 最终报告

## 结论

`IMPLEMENT_AMBIGUITY_DISCOVERY_PROTOTYPE_V0` 已完成。该模块以独立、可选的离线包实现了：

```text
Instruction
  -> language slot analysis
  -> potentially unresolved reference
  -> symbolic environment candidate retrieval
  -> candidate interpretations
  -> existing consequence module (fail-closed handoff)
```

它回答的是一个受限的原型问题：在受支持的英文指称模板与结构化环境实体表上，是否能通过“同一指称仍有多个兼容 grounding candidate”发现潜在歧义。它不是 Paper MVP 评价结果。

## 诊断集与指标

- 诊断样例：{metrics['diagnostic_case_count']}（正例 {metrics['positive_case_count']}，负例 {metrics['negative_case_count']}）
- ambiguity detection accuracy：{metrics['ambiguity_detection_accuracy']:.6f}
- candidate recall：{metrics['candidate_recall']:.6f}
- false ambiguity rate：{metrics['false_ambiguity_rate']:.6f}
- ambiguous slot accuracy：{metrics['ambiguous_slot_accuracy']:.6f}
- confusion matrix：TP={confusion['true_positive']}，TN={confusion['true_negative']}，FP={confusion['false_positive']}，FN={confusion['false_negative']}

这些数值只描述同一份人工构造、规则覆盖内的 diagnostic set；不用于证明自然语言泛化、真实视觉 grounding 或驾驶性能。

## 每例输出合同

`DEMO_CASES.json` 为每个样例保存 instruction、`detected_ambiguous_slot`、candidate entity list、candidate interpretations、confidence 和 reason。歧义正例的 reason 固定为 `multiple compatible grounding candidates`。confidence 是确定性的规则支持分数，不是学习得到或校准后的概率。

## Existing consequence module handoff

评价时显式启用了可选 bridge，把发现的 candidate IDs 和 symbolic bindings 传入现有 `CandidateSpecificConsequenceEngineV1`。由于本原型不运行 SimLingo、没有 route/plan semantic evidence，也不伪造 wrong-goal cost，现有 consequence engine 对全部矩阵单元返回 `UNKNOWN`；fail-closed handoff rate={metrics['consequence_handoff_fail_closed_rate']:.6f}。这验证了边界兼容性，但不是 consequence 结果。

## 隔离边界

- 新代码只位于 `driveclarify_ambiguity_discovery_v0/`；
- 没有修改 Paper MVP pipeline、M2B、M3、authority 或 SimLingo backbone；
- 默认 API 不启用 consequence handoff，只有显式 `enable_consequence_handoff=True` 才导入并调用现有 consequence engine；
- 没有启动 CARLA、evaluator、模型、checkpoint、CUDA 或 vehicle control；
- 输出 designation 明确包含 `PROTOTYPE_ONLY / NOT_PAPER_MVP_EVALUATION / NOT_LEARNED_AMBIGUITY_DETECTOR / NOT_AUTONOMOUS_VLA`。

## 产物

- `FINAL_REPORT.md`：本报告；
- `DEMO_CASES.json`：逐例 runtime 输出与 diagnostic 对照；
- `METRICS.json`：聚合指标、逐例结果和稳定哈希；
- `LIMITATIONS.md`：适用边界与不能声称的能力。

按任务要求，工作停在 prototype evaluation；未进入 CARLA benchmark。
"""


def _limitations() -> str:
    return """# Ambiguity Discovery Prototype v0 局限

## 能力边界

1. 本实现是英文关键词、有限 noun phrase 与属性兼容规则，不是 full language understanding。
2. 本实现没有训练过程、learned ambiguity detector、语言模型或置信度校准；`confidence` 只是确定性规则支持分数。
3. 环境输入是人工提供的 symbolic entity table。本模块不负责目标检测、跟踪、深度估计、地图定位或真实视觉 grounding。
4. v0 只选择一个主要指称并逐属性 exact-match；不处理指代消解、否定、比较级（如 closest）、量词作用域、关系图、多轮上下文或多个同时 unresolved slots。
5. “两个兼容实体”是启发式歧义信号，不等价于乘客实际具有两个意图，也不证明 clarification 有价值。
6. candidate interpretation 是显式 symbolic binding 文本，不是生成式语义推理。
7. 诊断集由开发者人工构造且覆盖当前规则词表；指标不代表开放词汇、分布外语言、真实视觉数据或自然驾驶场景泛化。

## Consequence 与控制边界

1. bridge 只把 candidate identity/binding 传入现有 consequence engine；没有 candidate plan 或 plan-semantic evidence，因此 engine 按合同返回 `UNKNOWN`。
2. 没有从 entity 差异推断碰撞、TTC、车道安全、物理距离、交通规则或 safety criticality。
3. 输出不是 ASK/ACT/WAIT 决策，不具备 authority，不得触发 vehicle control。
4. 没有修改或运行 Paper MVP、M2B、M3、authority、SimLingo backbone 或 CARLA。

## 明确不能声称

- 不能声称 full language understanding；
- 不能声称 learned ambiguity detector；
- 不能声称 autonomous VLA capability；
- 不能声称 paper result、CARLA benchmark result、真实视觉 grounding、驾驶安全改进或 clarification policy superiority。

下一阶段若继续，应使用独立冻结、未参与规则开发的语言与场景集，并加入 perception uncertainty、关系约束和 calibrated abstention；这些均不属于本次 prototype v0。
"""


def write_artifacts(
    metrics: Mapping[str, Any],
    demos: Sequence[Mapping[str, Any]],
    output_dir: str | Path,
) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    demo_payload = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_name": "AMBIGUITY_DISCOVERY_PROTOTYPE_V0_DIAGNOSTIC",
        "designation": list(DESIGNATION),
        "case_count": len(demos),
        "cases": list(demos),
    }
    (destination / "DEMO_CASES.json").write_text(_json_text(demo_payload), encoding="utf-8")
    (destination / "METRICS.json").write_text(_json_text(metrics), encoding="utf-8")
    (destination / "FINAL_REPORT.md").write_text(_final_report(metrics), encoding="utf-8")
    (destination / "LIMITATIONS.md").write_text(_limitations(), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    cases = load_diagnostic_inputs(args.inputs)
    gold = load_diagnostic_gold(args.gold)
    metrics, demos = evaluate(cases, gold)
    write_artifacts(metrics, demos, args.output_dir)
    print(
        json.dumps(
            {
                "status": "PASS_PROTOTYPE_EVALUATION_COMPLETE",
                "case_count": metrics["diagnostic_case_count"],
                "ambiguity_detection_accuracy": metrics["ambiguity_detection_accuracy"],
                "candidate_recall": metrics["candidate_recall"],
                "false_ambiguity_rate": metrics["false_ambiguity_rate"],
                "output_dir": str(Path(args.output_dir)),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

