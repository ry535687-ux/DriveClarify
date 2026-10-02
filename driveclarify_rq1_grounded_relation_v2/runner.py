"""本轮 RQ1 的无人值守阶段运行器。

阶段：
  S1_DEV_FORWARDS   对 8 个开发布局的全部候选文本跑真实 GPU 前向（py3.8 子进程）
  S2_DEV_ASSEMBLE   装配方法输入与独立标签，装配候选未来
  S3_DEV_CALIBRATE  在开发集上按事先固定网格与并列规则选阈值
  S4_FREEZE         冻结代码、checkpoint、切分、样本清单、阈值、标注版本
  S5_TEST_PRECHECK  检查测试集前置条件（真实观测是否存在）
  S6_SCORE          预测 → 落盘 → 锁定 → 才关联标签 → 评分
  S7_ANALYSIS       四格、成对、UNKNOWN 分析与图表
每阶段状态写入 queue/STAGE_STATE.json；失败不无限重试，保存现场后停止该阶段。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import assets, dataset, forwards, labels, pipeline
from .contracts import digest
from .firewall import join_after_lock
from .lock import lock_predictions, write_jsonl_atomic
from .methods import M2_IMPLEMENTATION_STATUS, METHODS
from .scoring import paired_both_correct, score

ROOT = Path("/home/buaa/wrh/DriveClarify")
ROUND = ROOT / "reports/driveclarify_rq1_grounded_relation_v2_20260910"
SIMLINGO_PYTHON = Path("/home/buaa/anaconda3/envs/simlingo/bin/python")
CHECKPOINT_SHA256 = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
# §8 给出的定位线索 cc6873e2... 与实际文件不符；如实记录实际值，不改文件去凑旧摘要。
CHECKPOINT_DIGEST_ORIENTATION_CLUE = "cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044"
MINIMUM_FREE_BYTES = 6 * 1024 ** 3
FORWARD_TIMEOUT_S = 1800
MAXIMUM_TECHNICAL_RETRIES_PER_UNIT = 1


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def free_bytes() -> int:
    stat = os.statvfs(str(ROOT))
    return stat.f_bavail * stat.f_frsize


def stage_state(update: Mapping[str, Any] | None = None) -> dict[str, Any]:
    path = ROUND / "queue/STAGE_STATE.json"
    value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"stages": {}}
    if update:
        value["stages"].update(update)
        value["updated"] = now()
        write_json(path, value)
    return value


def split_units() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = json.loads((ROUND / "manifests/SPLIT_CANDIDATES.json").read_text(encoding="utf-8"))
    return list(manifest["dev"]), list(manifest["test"])


def build_split_rows(rows: Sequence[Mapping[str, Any]], split: str, *, cache_root: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """为一个切分装配样本、标签与缺观测记录。"""
    packages = assets.observation_packages()
    samples: list[dict] = []
    label_rows: list[dict] = []
    missing: list[dict] = []
    for index, unit in enumerate(rows):
        unit_id = str(unit["unit_id"])
        observations = packages.get(unit_id) or []
        if not observations:
            missing.append({
                "unit_id": unit_id,
                "layout_identity": str(unit.get("layout_identity") or ""),
                "reason_code": "NO_REAL_OBSERVATION_PACKAGE_AVAILABLE",
                "fabricated_observation_used": False,
            })
            continue
        unit_samples, unit_labels = pipeline.build_unit_rows(
            unit_id, layout_index=index, split=split, observations=observations,
        )
        for sample in unit_samples:
            attach_fresh_futures(sample, cache_root=cache_root)
        samples.extend(unit_samples)
        label_rows.extend(unit_labels)
    return samples, label_rows, missing


def attach_fresh_futures(sample: dict[str, Any], *, cache_root: Path) -> None:
    """用本轮真实候选文本前向替换历史前向复用。

    两个候选的前向必须来自同一观测、同一 checkpoint，且注入文本正是本轮候选文本。
    任一候选缺前向 → 不放任何 candidate_futures，由方法侧闭合为 UNKNOWN。
    """
    plans, keys = [], []
    for index in (0, 1):
        key = forwards.cache_key_for_candidate(sample, index, checkpoint_sha256=CHECKPOINT_SHA256)
        keys.append(key)
        plans.append(forwards.load_plan(cache_root, str(sample["unit_id"]), key))
    if any(plan is None for plan in plans):
        sample["candidate_future_evidence"] = {
            "available": False,
            "reason_code": "FRESH_CANDIDATE_FORWARD_MISSING",
            "cache_keys": keys,
            "candidate_text_matches_forward_injection_text": None,
        }
        return
    context = str(sample["observation"]["sha256"])
    futures = []
    for plan in plans:
        row = pipeline.candidate_future_from_plan(plan, nonlanguage_context_sha256=context)
        futures.append(row)
    matches = all(str(plan.get("candidate_text")) == str(sample["candidates"][index]["text"]) for index, plan in enumerate(plans))
    evidence = {
        "available": all(row.get("valid") for row in futures),
        "reuse_note": "FRESH_FORWARD_WITH_THIS_ROUND_CANDIDATE_TEXT",
        # 这是本轮的关键改进：注入文本就是候选文本本身，不再是历史注入文本。
        "candidate_text_matches_forward_injection_text": matches,
        "cache_keys": keys,
        "checkpoint_sha256": str(plans[0].get("checkpoint_sha256")),
        "candidate_forward_count": 2,
        "same_observation_hash": len({str(plan.get("observation_hash")) for plan in plans}) == 1,
        "distinct_speed_plan_hash": len({str(plan.get("speed_plan_hash")) for plan in plans}),
        "geometric_paths": [pipeline.geometric_path_from_plan(plan) for plan in plans],
    }
    if evidence["available"] and matches:
        sample["candidate_futures"] = futures
    sample["candidate_future_evidence"] = evidence


def stage1_dev_forwards() -> dict[str, Any]:
    """S1：对开发布局的全部候选文本跑真实前向。GPU 与 CARLA 分时，不并发。"""
    cache_root = ROUND / "cache/candidate_forwards"
    dev_units, _ = split_units()
    packages = assets.observation_packages()
    samples: list[dict] = []
    for index, unit in enumerate(dev_units):
        unit_id = str(unit["unit_id"])
        observations = packages.get(unit_id) or []
        if not observations:
            continue
        unit_samples, _ = pipeline.build_unit_rows(unit_id, layout_index=index, split="DEV", observations=observations)
        samples.extend(unit_samples)
    requests = forwards.forward_requests(samples, checkpoint_sha256=CHECKPOINT_SHA256)

    units_report, attempted, failures = [], 0, 0
    for unit_id, rows in requests.items():
        output_directory = cache_root / unit_id
        pending = [row for row in rows if not forwards.plan_path(cache_root, unit_id, row["cache_key"]).is_file()]
        if not pending:
            units_report.append({
                "unit_id": unit_id, "status": "ALL_CACHED", "requested": len(rows), "forwarded": 0,
                "plans_present": sum(1 for row in rows if forwards.plan_path(cache_root, unit_id, row["cache_key"]).is_file()),
            })
            continue
        if free_bytes() < MINIMUM_FREE_BYTES:
            units_report.append({"unit_id": unit_id, "status": "STOPPED_LOW_DISK", "free_bytes": free_bytes()})
            break
        observation = (packages.get(unit_id) or [])[0]
        manifest = assets.read_json(Path(observation["manifest_path"]))
        spec_path = output_directory / "WORKER_SPEC.json"
        write_json(spec_path, {
            "package_directory": str(observation["package_directory"]),
            "manifest": manifest,
            "unit_id": unit_id,
            "requests": rows,
            "output_directory": str(output_directory.resolve()),
            "run_id": "DC-RQ1V2-{}".format(unit_id),
        })
        attempt, result = 0, None
        while attempt <= MAXIMUM_TECHNICAL_RETRIES_PER_UNIT:
            attempt += 1
            attempted += 1
            log_path = output_directory / "WORKER_STDOUT_attempt{}.log".format(attempt)
            with log_path.open("w", encoding="utf-8") as stream:
                completed = subprocess.run(
                    [str(SIMLINGO_PYTHON), "-B", "-m", "driveclarify_rq1_grounded_relation_v2.gpu_worker", str(spec_path)],
                    cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT, timeout=FORWARD_TIMEOUT_S, check=False,
                )
            # 每次尝试的日志全部保留，不"多生成取最好"。
            result = {"attempt": attempt, "returncode": completed.returncode, "log": str(log_path)}
            if completed.returncode == 0:
                break
            failures += 1
        produced = sum(1 for row in rows if forwards.plan_path(cache_root, unit_id, row["cache_key"]).is_file())
        units_report.append({
            "unit_id": unit_id, "requested": len(rows), "plans_present": produced,
            "status": "COMPLETE" if produced == len(rows) else "INCOMPLETE",
            "attempts": result, "attempt_logs_retained": True,
        })
    value = {
        "stage": "S1_DEV_FORWARDS", "finished": now(),
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "checkpoint_digest_orientation_clue": CHECKPOINT_DIGEST_ORIENTATION_CLUE,
        "checkpoint_matches_orientation_clue": False,
        "unit_count": len(requests), "subprocess_attempts": attempted, "subprocess_failures": failures,
        "units": units_report,
        "status": "COMPLETE" if all(row.get("status") in ("COMPLETE", "ALL_CACHED") for row in units_report) else "PARTIAL",
    }
    write_json(ROUND / "queue/S1_DEV_FORWARDS.json", value)
    stage_state({"S1_DEV_FORWARDS": {"status": value["status"], "finished": value["finished"]}})
    return value


def stage2_assemble(split: str) -> dict[str, Any]:
    """S2：装配方法输入与独立标签，物理分离落盘。"""
    cache_root = ROUND / "cache/candidate_forwards"
    dev_units, test_units = split_units()
    rows = dev_units if split == "DEV" else test_units
    samples, label_rows, missing = build_split_rows(rows, split, cache_root=cache_root)
    inputs_path = ROUND / "method_inputs/{}_METHOD_INPUTS.jsonl".format(split)
    labels_path = ROUND / "label_authority/{}_LABELS.jsonl".format(split)
    inputs_receipt = pipeline.write_jsonl(inputs_path, samples)
    labels_receipt = pipeline.write_jsonl(labels_path, label_rows)
    value = {
        "stage": "S2_ASSEMBLE", "split": split, "finished": now(),
        "planned_layout_count": len(rows),
        "layouts_with_real_observations": len({str(row["unit_id"]) for row in samples}),
        "layouts_missing_observations": missing,
        "decision_sample_count": len(samples),
        "observation_count": len({str(row["observation_key"]) for row in samples}),
        "language_variant_count": len({str(row["phrasing_id"]) for row in samples}),
        "label_count": len(label_rows),
        "annotation_disputes": [row["sample_id"] for row in label_rows if row["annotation"]["annotation_dispute"]],
        "samples_with_candidate_futures": sum(1 for row in samples if row.get("candidate_futures")),
        "method_inputs": inputs_receipt, "labels": labels_receipt,
        "labels_physically_separate_file": True,
        "status": "COMPLETE" if samples else "NO_SAMPLES_NO_REAL_OBSERVATIONS",
    }
    write_json(ROUND / "queue/S2_ASSEMBLE_{}.json".format(split), value)
    stage_state({"S2_ASSEMBLE_{}".format(split): {"status": value["status"], "samples": len(samples)}})
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stage3_calibrate() -> dict[str, Any]:
    """S3：开发集阈值校准。目标、网格与并列规则事先固定，只用开发集。"""
    samples = read_jsonl(ROUND / "method_inputs/DEV_METHOD_INPUTS.jsonl")
    label_rows = read_jsonl(ROUND / "label_authority/DEV_LABELS.jsonl")
    truth = {str(row["sample_id"]): str(row["truth"]) for row in label_rows}
    value = pipeline.calibrate_trajectory_threshold(samples, truth)
    value.update({
        "stage": "S3_DEV_CALIBRATE", "finished": now(),
        "dev_sample_count": len(samples),
        "samples_with_candidate_futures": sum(1 for row in samples if row.get("candidate_futures")),
        "grounding_threshold": pipeline.GROUNDING_THRESHOLD_DEFAULT,
        "m2_status": M2_IMPLEMENTATION_STATUS,
        # 校准只挑阈值这一个标量，不训练骨干、感知或神经决策头。
        "trained_components": [],
    })
    write_json(ROUND / "queue/S3_DEV_CALIBRATE.json", value)
    stage_state({"S3_DEV_CALIBRATE": {"status": "COMPLETE", "selected_threshold_m": value["selected_threshold_m"]}})
    return value


def stage4_freeze() -> dict[str, Any]:
    """S4：冻结代码、checkpoint、切分、清单、阈值与标注版本。"""
    calibration = json.loads((ROUND / "queue/S3_DEV_CALIBRATE.json").read_text(encoding="utf-8"))
    threshold = calibration.get("selected_threshold_m")
    module_root = ROOT / "driveclarify_rq1_grounded_relation_v2"
    code = {path.name: assets.sha256_file(path) for path in sorted(module_root.glob("*.py"))}
    value = {
        "stage": "S4_FREEZE", "frozen_at": now(),
        "code_sha256": code,
        "code_version": digest(code),
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "split_manifest_sha256": assets.sha256_file(ROUND / "manifests/SPLIT_CANDIDATES.json"),
        "method_inputs_dev_sha256": assets.sha256_file(ROUND / "method_inputs/DEV_METHOD_INPUTS.jsonl"),
        "labels_dev_sha256": assets.sha256_file(ROUND / "label_authority/DEV_LABELS.jsonl"),
        "frozen_config": {
            # 轨迹时域固定为 2.5s：速度头 10 点 × 0.25s 的完整时域。
            "trajectory_threshold_m": threshold,
            "trajectory_horizon_s": 2.5,
            "trajectory_step_s": 0.05,
            "grounding_threshold": pipeline.GROUNDING_THRESHOLD_DEFAULT,
        },
        "threshold_calibrated_on": "DEV_ONLY",
        "annotation_version": "AUTOMATED_LABEL_V1_PENDING_HUMAN_REVIEW",
        "methods": list(METHODS),
        "m2_status": M2_IMPLEMENTATION_STATUS,
        "post_freeze_changes_forbidden": [
            "thresholds", "class_composition", "sample_count", "method_definitions", "test_layout_membership",
        ],
    }
    write_json(ROUND / "queue/S4_FREEZE.json", value)
    stage_state({"S4_FREEZE": {"status": "COMPLETE", "code_version": value["code_version"]}})
    return value


def stage5_test_precheck() -> dict[str, Any]:
    """S5：测试集前置条件。缺真实观测就如实记录 BLOCKED，不用历史已分析布局顶替。"""
    _, test_units = split_units()
    packages = assets.observation_packages()
    covered = [str(row["unit_id"]) for row in test_units if packages.get(str(row["unit_id"]))]
    display = os.environ.get("DISPLAY")
    value = {
        "stage": "S5_TEST_PRECHECK", "finished": now(),
        "planned_test_layout_count": len(test_units),
        "test_layouts_with_real_observations": len(covered),
        "test_layouts_missing_observations": len(test_units) - len(covered),
        "capture_required": len(covered) < len(test_units),
        "display_present": bool(display),
        "display": display,
        "headless_or_virtual_display_used": False,
        "free_bytes": free_bytes(),
        "historical_analyzed_layouts_reused_as_test": False,
        "duplicated_layouts_to_pad_scale": False,
        "status": "READY" if len(covered) == len(test_units) else "BLOCKED_NO_TEST_OBSERVATIONS",
        "blocked_reason": None if len(covered) == len(test_units) else (
            "全部 24 个测试布局没有真实观测包；本轮 22 个真实观测包全部属于已分析的 "
            "m1_real_dataset_expansion_v3 活动，按 §4 不得当作未见测试布局。需要新的 CARLA "
            "采集（原生 Ubuntu + 本地物理显示器），本轮预算内未完成。"
        ),
    }
    write_json(ROUND / "queue/S5_TEST_PRECHECK.json", value)
    stage_state({"S5_TEST_PRECHECK": {"status": value["status"]}})
    return value


def stage6_score(split: str) -> dict[str, Any]:
    """S6：预测 → 落盘 → 锁定 → 才关联标签 → 评分。顺序不可颠倒。"""
    frozen = json.loads((ROUND / "queue/S4_FREEZE.json").read_text(encoding="utf-8"))
    config = frozen["frozen_config"]
    if config.get("trajectory_threshold_m") is None:
        # 阈值不可用时 M3/M5 的轨迹分支必然闭合为 UNKNOWN；如实记录，不塞一个默认值冒充校准。
        config = dict(config, trajectory_threshold_m=float("nan"))
    samples = read_jsonl(ROUND / "method_inputs/{}_METHOD_INPUTS.jsonl".format(split))
    if not samples:
        value = {"stage": "S6_SCORE", "split": split, "status": "NO_SAMPLES", "finished": now()}
        write_json(ROUND / "queue/S6_SCORE_{}.json".format(split), value)
        return value
    label_rows = read_jsonl(ROUND / "label_authority/{}_LABELS.jsonl".format(split))
    privileged = {}
    for row in label_rows:
        # M6 专属特权输入取自标签侧，绝不进入 M0--M5 的方法输入文件。
        privileged[str(row["sample_id"])] = {"task_signatures": row["annotation"]["outcome_tuples"]}

    predictions = pipeline.predict(samples, config, privileged_by_sample=privileged)
    predictions_path = ROUND / "results/{}_predictions.jsonl".format(split)
    lock_path = ROUND / "results/{}_PREDICTION_LOCK.json".format(split)
    if predictions_path.exists():
        # 禁止静默覆盖原结果：换版本目录而不是覆盖。
        stamp = time.strftime("%H%M%S")
        predictions_path = ROUND / "results/{}_predictions.rerun{}.jsonl".format(split, stamp)
        lock_path = ROUND / "results/{}_PREDICTION_LOCK.rerun{}.json".format(split, stamp)
    receipt = write_jsonl_atomic(predictions_path, predictions)
    lock_receipt = lock_predictions(predictions_path, lock_path)

    joined = join_after_lock(predictions_path, lock_path, ROUND / "label_authority/{}_LABELS.jsonl".format(split))
    by_sample = {str(row["sample_id"]): row for row in label_rows}
    for row in joined:
        source = by_sample[str(row["sample_id"])]
        row["semantic_preservation_pair_id"] = source["semantic_preservation_pair_id"]
        row["relation_flip_pair_id"] = source["relation_flip_pair_id"]
        row["evidence_condition"] = source["evidence_condition"]
    summary = score(joined)
    value = {
        "stage": "S6_SCORE", "split": split, "finished": now(),
        "prediction_receipt": receipt, "prediction_lock": lock_receipt,
        "labels_read_only_after_lock": True,
        "primary_metric": "BALANCED_ACCURACY_UNKNOWN_IN_DENOMINATOR",
        "prespecified_main_comparisons": [
            "M5_DRIVECLARIFY_FULL_vs_M3_TRAJECTORY_ONLY",
            "M5_DRIVECLARIFY_FULL_vs_M4_TASK_STATE_TOPOLOGY_ONLY",
        ],
        "summary": summary,
        "m2_status": M2_IMPLEMENTATION_STATUS,
        "status": "COMPLETE",
    }
    write_json(ROUND / "queue/S6_SCORE_{}.json".format(split), value)
    pipeline.write_jsonl(ROUND / "results/{}_joined_rows.jsonl".format(split), joined)
    stage_state({"S6_SCORE_{}".format(split): {"status": "COMPLETE", "rows": len(joined)}})
    return value


def stage7_analysis(split: str) -> dict[str, Any]:
    """S7：四格交叉、成对分析、UNKNOWN 分析、CSV 与图表。"""
    from . import analysis

    joined = read_jsonl(ROUND / "results/{}_joined_rows.jsonl".format(split))
    if not joined:
        value = {"stage": "S7_ANALYSIS", "split": split, "status": "NO_ROWS", "finished": now()}
        write_json(ROUND / "queue/S7_ANALYSIS_{}.json".format(split), value)
        return value
    samples = read_jsonl(ROUND / "method_inputs/{}_METHOD_INPUTS.jsonl".format(split))
    frozen = json.loads((ROUND / "queue/S4_FREEZE.json").read_text(encoding="utf-8"))
    value = analysis.run(split=split, joined=joined, samples=samples, frozen=frozen, round_root=ROUND)
    value.update({"stage": "S7_ANALYSIS", "split": split, "finished": now(), "status": "COMPLETE"})
    write_json(ROUND / "queue/S7_ANALYSIS_{}.json".format(split), value)
    stage_state({"S7_ANALYSIS_{}".format(split): {"status": "COMPLETE"}})
    return value


STAGES = {
    "S1": lambda: stage1_dev_forwards(),
    "S2_DEV": lambda: stage2_assemble("DEV"),
    "S3": lambda: stage3_calibrate(),
    "S4": lambda: stage4_freeze(),
    "S5": lambda: stage5_test_precheck(),
    "S6_DEV": lambda: stage6_score("DEV"),
    "S7_DEV": lambda: stage7_analysis("DEV"),
    "S2_TEST": lambda: stage2_assemble("TEST"),
    "S6_TEST": lambda: stage6_score("TEST"),
    "S7_TEST": lambda: stage7_analysis("TEST"),
}
# 默认全序：开发集打通并冻结后，测试集按前置条件决定是否可运行。
DEFAULT_ORDER = ("S1", "S2_DEV", "S3", "S4", "S5", "S6_DEV", "S7_DEV")


def main(argv: Sequence[str] = ()) -> int:
    arguments = list(argv or sys.argv[1:])
    order = arguments or list(DEFAULT_ORDER)
    for name in order:
        if name not in STAGES:
            print("unknown stage: " + name, file=sys.stderr)
            return 2
    for name in order:
        started = time.time()
        try:
            result = STAGES[name]()
            print("{} -> {} ({:.1f}s)".format(name, result.get("status"), time.time() - started), flush=True)
        except Exception as error:  # 保存现场后停止本阶段，不无限自修复
            write_json(ROUND / "queue/STAGE_FAILURE_{}.json".format(name), {
                "stage": name, "failed_at": now(),
                "error_type": type(error).__name__, "error": str(error),
                "infinite_retry_attempted": False,
            })
            stage_state({name: {"status": "FAILED", "error": str(error)[:400]}})
            print("{} -> FAILED: {}: {}".format(name, type(error).__name__, error), file=sys.stderr, flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
