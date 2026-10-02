"""RQ1 V3 的无人值守阶段运行器。

阶段：
  S1_DEV_FORWARDS   对开发布局的全部本轮候选文本跑真实 GPU 前向（py3.8 子进程）
  S2_ASSEMBLE_DEV   装配方法输入与独立标签，装配候选未来
  S3_DEV_CALIBRATE  在开发集上按事先固定网格与并列规则选阈值
  S4_FREEZE         冻结代码、checkpoint、切分、样本清单、阈值、标注版本
  S5_SCORE_DEV      预测 → 落盘 → 锁定 → 才关联标签 → 评分
  S6_ANALYSIS_DEV   四格、成对、UNKNOWN、证据不足分析与图表
  S7_HIST_FORWARDS / S8_ASSEMBLE_HIST / S9_SCORE_HIST / S10_ANALYSIS_HIST
      对剩余 14 个历史布局做同协议的**已暴露历史分析**（非未见测试集）
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

from . import analysis, assets, dataset, forwards, pipeline
from .contracts import digest
from .firewall import join_after_lock
from .lock import lock_predictions, write_jsonl_atomic
from .methods import M2_IMPLEMENTATION_STATUS, M_LEX_STATUS, METHODS
from .scoring import evidence_insufficient_report, paired_both_correct, partition_by_label_status, score

ROOT = Path("/home/buaa/wrh/DriveClarify")
ROUND = ROOT / "reports/driveclarify_rq1_grounded_relation_v3_20260910"
SIMLINGO_PYTHON = Path("/home/buaa/anaconda3/envs/simlingo/bin/python")
CHECKPOINT_SHA256 = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
CHECKPOINT_DIGEST_ORIENTATION_CLUE = "cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044"
MINIMUM_FREE_BYTES = 5 * 1024 ** 3
FORWARD_TIMEOUT_S = 2400
MAXIMUM_TECHNICAL_RETRIES_PER_UNIT = 1

# 冻结的 InternVL2-1B 图像编码器权重与配置已完整存在于本机 HuggingFace 缓存。
# 首次运行中两个单元因 huggingface.co 的 TLS 中断而失败（纯网络故障，非科学结果）。
# 强制离线可复现地使用同一份本地缓存；这不改变权重、配置或任何科学行为。
WORKER_ENV = {
    **os.environ,
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
}

# 开发布局：沿用 v2 的 8 个，保持与前一轮可比。
DEV_UNITS = (
    "TOWN01_JUNCTION_26_UNIT01",
    "TOWN02_JUNCTION_20_UNIT01",
    "TOWN03_JUNCTION_498_UNIT01",
    "TOWN04_JUNCTION_483_UNIT01",
    "TOWN05_JUNCTION_720_UNIT01",
    "TOWN06_JUNCTION_72_UNIT01",
    "TOWN07_JUNCTION_260_UNIT01",
    "TOWN10HD_JUNCTION_664_UNIT01",
)


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
    value: dict[str, Any] = {"stages": {}}
    if path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
    if update:
        value.setdefault("stages", {}).update(update)
        value["updated"] = now()
        write_json(path, value)
    return value


def historical_units() -> list[str]:
    """22 个真实观测布局里除开发集之外的 14 个。它们属于已分析历史活动，不是未见测试集。"""
    return sorted(set(assets.observation_packages()) - set(DEV_UNITS))


def _units_for_split(split: str) -> list[str]:
    return list(DEV_UNITS) if split == "DEV" else historical_units()


def _observations(unit_id: str) -> list[dict[str, Any]]:
    return sorted(assets.observation_packages().get(unit_id, []), key=lambda row: str(row["observation_key"]))


def _samples_for_split(split: str, *, cache_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    samples: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    for index, unit_id in enumerate(_units_for_split(split)):
        observations = _observations(unit_id)
        if not observations:
            continue
        unit_samples, unit_labels = pipeline.build_unit_rows(
            unit_id, layout_index=index, split=split, observations=observations,
            cache_root=cache_root, checkpoint_sha256=CHECKPOINT_SHA256,
        )
        samples.extend(unit_samples)
        label_rows.extend(unit_labels)
    return samples, label_rows


# ---------------------------------------------------------------- S1 前向

def stage_forwards(split: str) -> dict[str, Any]:
    """对该切分的全部本轮候选文本跑真实 GPU 前向。已缓存的直接跳过。"""
    stage = "S1_DEV_FORWARDS" if split == "DEV" else "S7_HIST_FORWARDS"
    cache_root = ROUND / "cache/candidate_forwards"
    cache_root.mkdir(parents=True, exist_ok=True)
    samples, _ = _samples_for_split(split, cache_root=cache_root)
    requests = forwards.forward_requests(samples, checkpoint_sha256=CHECKPOINT_SHA256)

    unit_reports: list[dict[str, Any]] = []
    attempted = failures = 0
    for unit_id, rows in requests.items():
        observations = _observations(unit_id)
        if not observations:
            continue
        pending = [row for row in rows
                   if not forwards.plan_path(cache_root, unit_id, row["cache_key"]).is_file()]
        if not pending:
            unit_reports.append({"unit_id": unit_id, "requested": len(rows), "forwarded": 0,
                                 "plans_present": len(rows), "status": "ALL_CACHED"})
            continue
        if free_bytes() < MINIMUM_FREE_BYTES:
            unit_reports.append({"unit_id": unit_id, "requested": len(rows), "forwarded": 0,
                                 "status": "STOPPED_LOW_DISK"})
            break

        package = observations[0]
        # worker 需要 manifest 内容本体（observation_hash / source_frame /
        # package_content_sha256 用于构造观测 linkage），不是路径。
        spec = {
            "unit_id": unit_id,
            "package_directory": str(package["package_directory"]),
            "manifest_path": str(package["manifest_path"]),
            "manifest": assets.read_json(Path(package["manifest_path"])),
            "output_directory": str(cache_root / unit_id),
            "run_id": f"DC-RQ1V3-{split}-{unit_id}",
            "requests": pending,
        }
        spec_path = cache_root / unit_id / "FORWARD_SPEC.json"
        write_json(spec_path, spec)
        log_path = ROUND / f"logs/{stage}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        status = "FAILED"
        for attempt in range(MAXIMUM_TECHNICAL_RETRIES_PER_UNIT + 1):
            attempted += 1
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"\n===== {now()} {unit_id} attempt={attempt} pending={len(pending)} =====\n")
                stream.flush()
                completed = subprocess.run(
                    [str(SIMLINGO_PYTHON), "-B", "-m", "driveclarify_rq1_grounded_relation_v3.gpu_worker", str(spec_path)],
                    cwd=str(ROOT), stdout=stream, stderr=subprocess.STDOUT,
                    timeout=FORWARD_TIMEOUT_S, check=False, env=WORKER_ENV,
                )
            present = sum(1 for row in rows if forwards.plan_path(cache_root, unit_id, row["cache_key"]).is_file())
            if completed.returncode == 0 and present == len(rows):
                status = "COMPLETE"
                break
            failures += 1
            status = f"INCOMPLETE_RC{completed.returncode}_PLANS{present}_OF_{len(rows)}"
        present = sum(1 for row in rows if forwards.plan_path(cache_root, unit_id, row["cache_key"]).is_file())
        unit_reports.append({"unit_id": unit_id, "requested": len(rows), "forwarded": len(pending),
                             "plans_present": present, "status": status})

    value = {
        "stage": stage, "split": split, "finished": now(),
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "checkpoint_digest_orientation_clue": CHECKPOINT_DIGEST_ORIENTATION_CLUE,
        "checkpoint_matches_orientation_clue": CHECKPOINT_SHA256 == CHECKPOINT_DIGEST_ORIENTATION_CLUE,
        "unit_count": len(unit_reports),
        "subprocess_attempts": attempted, "subprocess_failures": failures,
        "units": unit_reports,
        "status": "COMPLETE" if all(row["status"] in ("COMPLETE", "ALL_CACHED") for row in unit_reports) and unit_reports else "PARTIAL",
    }
    write_json(ROUND / f"queue/{stage}.json", value)
    stage_state({stage: {"status": value["status"], "finished": value["finished"],
                         "unit_count": value["unit_count"]}})
    return value


# ---------------------------------------------------------------- S2 装配

def stage_assemble(split: str) -> dict[str, Any]:
    stage = "S2_ASSEMBLE_DEV" if split == "DEV" else "S8_ASSEMBLE_HIST"
    cache_root = ROUND / "cache/candidate_forwards"
    samples, label_rows = _samples_for_split(split, cache_root=cache_root)
    prefix = "DEV" if split == "DEV" else "HIST"
    inputs = pipeline.write_jsonl(ROUND / f"method_inputs/{prefix}_METHOD_INPUTS.jsonl", samples)
    labels_written = pipeline.write_jsonl(ROUND / f"label_authority/{prefix}_LABELS.jsonl", label_rows)
    futures_available = sum(1 for row in samples if (row.get("candidate_future_evidence") or {}).get("available"))
    value = {
        "stage": stage, "split": split, "samples": len(samples),
        "layout_count": len({row["layout_id"] for row in samples}),
        "observation_count": len({row["observation_key"] for row in samples}),
        "candidate_futures_available": futures_available,
        "truth_distribution": {key: sum(1 for row in label_rows if (row.get("truth") or row["truth_status"]) == key)
                               for key in sorted({(row.get("truth") or row["truth_status"]) for row in label_rows})},
        "annotation_disputes": sum(1 for row in label_rows if row["annotation"].get("annotation_dispute")),
        "method_inputs": inputs, "labels": labels_written,
        "status": "COMPLETE" if samples else "BLOCKED_NO_SAMPLES",
    }
    write_json(ROUND / f"queue/{stage}.json", value)
    stage_state({stage: {"status": value["status"], "samples": value["samples"]}})
    return value


# ---------------------------------------------------------------- S3 校准

def stage_calibrate() -> dict[str, Any]:
    samples = [json.loads(line) for line in (ROUND / "method_inputs/DEV_METHOD_INPUTS.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    label_rows = [json.loads(line) for line in (ROUND / "label_authority/DEV_LABELS.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    truth = {row["sample_id"]: row["truth"] for row in label_rows if row.get("truth")}
    result = pipeline.calibrate_trajectory_threshold(samples, truth)
    value = {"stage": "S3_DEV_CALIBRATE", **result,
             "status": "COMPLETE" if result["selected_threshold_m"] is not None else "BLOCKED_NO_CALIBRATION_ROWS"}
    write_json(ROUND / "queue/S3_DEV_CALIBRATE.json", value)
    stage_state({"S3_DEV_CALIBRATE": {"status": value["status"], "selected_threshold_m": result["selected_threshold_m"]}})
    return value


# ---------------------------------------------------------------- S4 冻结

def _source_version() -> str:
    package = Path(__file__).resolve().parent
    return digest({path.name: assets.sha256_file(path) for path in sorted(package.glob("*.py"))})


def stage_freeze() -> dict[str, Any]:
    calibration = json.loads((ROUND / "queue/S3_DEV_CALIBRATE.json").read_text(encoding="utf-8"))
    config = {
        "trajectory_threshold_m": calibration["selected_threshold_m"],
        "trajectory_horizon_s": 2.5,
        "trajectory_step_s": 0.05,
        "grounding_threshold": pipeline.GROUNDING_THRESHOLD_DEFAULT,
    }
    samples = [json.loads(line) for line in (ROUND / "method_inputs/DEV_METHOD_INPUTS.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    value = {
        "stage": "S4_FREEZE", "frozen_at": now(),
        "code_version": _source_version(),
        "checkpoint_sha256": CHECKPOINT_SHA256,
        "frozen_config": config,
        "dev_units": list(DEV_UNITS),
        "historical_units": historical_units(),
        "dev_sample_manifest_sha256": digest(sorted(str(row["sample_id"]) for row in samples)),
        "relation_configs": list(dataset.RELATION_CONFIGS),
        "phrasings": list(dataset.PHRASINGS),
        "annotation_version": "AUTOMATED_LABEL_V1_PENDING_HUMAN_REVIEW",
        "methods": list(METHODS),
        "m2_status": M2_IMPLEMENTATION_STATUS,
        "m_lex_status": M_LEX_STATUS,
        "backbone_or_perception_trained": False,
        "pid_modified": False,
        "status": "COMPLETE",
    }
    write_json(ROUND / "queue/S4_FREEZE.json", value)
    stage_state({"S4_FREEZE": {"status": "COMPLETE", "code_version": value["code_version"]}})
    return value


# ---------------------------------------------------------------- S5 评分

def stage_score(split: str) -> dict[str, Any]:
    stage = "S5_SCORE_DEV" if split == "DEV" else "S9_SCORE_HIST"
    prefix = "DEV" if split == "DEV" else "HIST"
    freeze = json.loads((ROUND / "queue/S4_FREEZE.json").read_text(encoding="utf-8"))
    config = freeze["frozen_config"]
    samples = [json.loads(line) for line in (ROUND / f"method_inputs/{prefix}_METHOD_INPUTS.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    privileged = pipeline.privileged_by_sample(samples)
    rows = pipeline.predict(samples, config, privileged=privileged)

    # 开发阶段修复后重测时保留旧输出：已存在的预测/锁文件不覆盖、不删除，
    # 改用带尝试序号的新路径，并在 attempt 记录里指明前一版本。
    predictions_path = ROUND / f"results/{prefix}_predictions.jsonl"
    lock_path = ROUND / f"results/{prefix}_PREDICTION_LOCK.json"
    superseded: list[str] = []
    attempt = 1
    while predictions_path.exists() or lock_path.exists():
        attempt += 1
        superseded.extend(str(path) for path in (predictions_path, lock_path) if path.exists())
        predictions_path = ROUND / f"results/{prefix}_predictions.attempt{attempt:02d}.jsonl"
        lock_path = ROUND / f"results/{prefix}_PREDICTION_LOCK.attempt{attempt:02d}.json"
    write_jsonl_atomic(predictions_path, rows)
    lock_predictions(predictions_path, lock_path, extra={
        "stage": stage, "split": split, "config": config, "code_version": freeze["code_version"],
        "attempt": attempt, "superseded_paths_preserved": superseded})

    joined = join_after_lock(predictions_path, lock_path, ROUND / f"label_authority/{prefix}_LABELS.jsonl")
    pipeline.write_jsonl(ROUND / f"results/{prefix}_joined_rows.jsonl", joined)

    evaluable, undefined = partition_by_label_status(joined)
    main = score(evaluable)
    insufficient = evidence_insufficient_report(undefined)
    preservation = paired_both_correct(evaluable, "semantic_preservation_pair_id")
    flip = paired_both_correct(evaluable, "relation_flip_pair_id")

    value = {
        "stage": stage, "split": split, "rows": len(joined),
        "attempt": attempt,
        "prediction_path": str(predictions_path),
        "lock_path": str(lock_path),
        "superseded_paths_preserved": superseded,
        "planned_population": len(joined),
        "evaluable_population": len(evaluable),
        "label_undefined_population": len(undefined),
        "main_results": main,
        "evidence_insufficient": insufficient,
        "semantic_preservation_pairs": len(preservation),
        "relation_flip_pairs": len(flip),
        "status": "COMPLETE",
    }
    write_json(ROUND / f"queue/{stage}.json", value)
    write_json(ROUND / f"results/{prefix}_SCORE.json", value)
    pipeline.write_jsonl(ROUND / f"results/{prefix}_preservation_pairs.jsonl", preservation)
    pipeline.write_jsonl(ROUND / f"results/{prefix}_flip_pairs.jsonl", flip)
    stage_state({stage: {"status": "COMPLETE", "rows": len(joined),
                         "evaluable": len(evaluable), "label_undefined": len(undefined)}})
    return value


# ---------------------------------------------------------------- S6 分析

def stage_analysis(split: str) -> dict[str, Any]:
    stage = "S6_ANALYSIS_DEV" if split == "DEV" else "S10_ANALYSIS_HIST"
    value = analysis.run(ROUND, split)
    write_json(ROUND / f"queue/{stage}.json", value)
    stage_state({stage: {"status": value.get("status", "COMPLETE")}})
    return value


STAGES = {
    "S1": lambda: stage_forwards("DEV"),
    "S2_DEV": lambda: stage_assemble("DEV"),
    "S3": stage_calibrate,
    "S4": stage_freeze,
    "S5_DEV": lambda: stage_score("DEV"),
    "S6_DEV": lambda: stage_analysis("DEV"),
    "S7": lambda: stage_forwards("HIST"),
    "S8_HIST": lambda: stage_assemble("HIST"),
    "S9_HIST": lambda: stage_score("HIST"),
    "S10_HIST": lambda: stage_analysis("HIST"),
}


def main(argv: Sequence[str]) -> int:
    if not argv:
        print("usage: runner STAGE [STAGE ...]  stages=" + ",".join(STAGES), file=sys.stderr)
        return 2
    for name in argv:
        action = STAGES.get(name)
        if action is None:
            print("UNKNOWN_STAGE:" + name, file=sys.stderr)
            return 2
        print(f"[{now()}] stage {name} start", flush=True)
        try:
            result = action()
        except Exception as error:  # 保存现场后停止该阶段，不无限自修复
            write_json(ROUND / f"queue/{name}_FAILURE.json", {
                "stage": name, "failed_at": now(), "error_type": type(error).__name__, "error": str(error)})
            stage_state({name: {"status": "FAILED", "error": str(error)}})
            print(f"[{now()}] stage {name} FAILED: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            return 1
        print(f"[{now()}] stage {name} -> {result.get('status')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
