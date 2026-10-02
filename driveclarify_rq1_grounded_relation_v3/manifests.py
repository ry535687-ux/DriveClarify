"""生成本轮的清单文件：切分清单、观测采集清单、推理配置摘要、公平性汇总。

清单只汇总**已经落盘的真实产物**，不生成任何新的科学结论：
  SPLIT_MANIFEST.json        每切分的布局/观测/样本/配置分布，并显式声明未达成规模
  COLLECTION_MANIFEST.json   观测包来源、渲染方式、文件内容摘要（本轮未新采集）
  INFERENCE_CONFIG_DIGEST.json  checkpoint/配置/缓存键/冻结阈值/公平性统计
  FORWARD_FAIRNESS_SUMMARY.json 逐单元 fairness / input_isolation / 状态回基线
"""

from __future__ import annotations

import glob
import json
from collections import Counter
from pathlib import Path
from typing import Any

from . import assets, cache, dataset
from .contracts import digest

ROOT = Path("/home/buaa/wrh/DriveClarify")
ROUND = ROOT / "reports/driveclarify_rq1_grounded_relation_v3_20260910"
VALID = ("TASK_EQUIVALENT", "TASK_DIVERGENT")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return str(path)


def split_manifest() -> str:
    freeze = json.loads((ROUND / "queue/S4_FREEZE.json").read_text(encoding="utf-8"))
    splits: dict[str, Any] = {}
    for prefix in ("DEV", "HIST"):
        samples = _read_jsonl(ROUND / f"method_inputs/{prefix}_METHOD_INPUTS.jsonl")
        labels = _read_jsonl(ROUND / f"label_authority/{prefix}_LABELS.jsonl")
        truths = Counter(str(row.get("truth") or row["truth_status"]) for row in labels)
        splits[prefix] = {
            "layout_count": len({row["layout_id"] for row in samples}),
            "observation_count": len({row["observation_key"] for row in samples}),
            "observations_per_layout": 1,
            "sample_count": len(samples),
            "relation_config_distribution": dict(sorted(Counter(row["relation_config_id"] for row in samples).items())),
            "phrasing_distribution": dict(sorted(Counter(row["phrasing_id"] for row in samples).items())),
            "family_distribution": dict(sorted(Counter(row["family"] for row in samples).items())),
            "truth_distribution": dict(sorted(truths.items())),
            "evaluable_count": sum(count for key, count in truths.items() if key in VALID),
            "label_undefined_count": truths.get("LABEL_UNDEFINED_UNDER_ALLOWED_INPUTS", 0),
            "sample_id_manifest_sha256": digest(sorted(str(row["sample_id"]) for row in samples)),
            "role": ("实现调试与阈值校准" if prefix == "DEV" else "已暴露历史分析，不是未见测试集"),
        }
    value = {
        "schema_version": "driveclarify.rq1_v3_split_manifest.v1",
        "splits": splits,
        "test_split": {
            "layout_count": 0, "observation_count": 0, "sample_count": 0,
            "status": "TEST_BLOCKED_NO_UNSEEN_OBSERVATIONS",
            "reason_zh": "24 个未见测试布局没有真实观测包；新采集须原生 Ubuntu + 本地物理显示器，"
                         "禁 headless/RenderOffScreen/Xvfb/VNC，且根分区仅剩约 12G。",
        },
        "undelivered_scale_declarations": [
            "目标每布局 2 个预设初态（两个种子）：实际每布局只有 1 个真实观测，本轮不存在第二个种子。",
            "目标 24 布局 × 8 = 192 正式测试样本：未达成。实际 DEV 64 + HIST 112 = 176，未用重复样本凑数。",
            "22 个布局全部来自已分析的 m1_real_dataset_expansion_v3，其中 14 个只作历史分析，不申报为测试集。",
            "四族标签仅用于分层报告；本轮歧义来源统一是指代表达指向哪个分支，不表示四种不同歧义机制。",
        ],
        "dev_units": freeze["dev_units"],
        "historical_units": freeze["historical_units"],
        "relation_configs": list(dataset.RELATION_CONFIGS),
        "phrasings": list(dataset.PHRASINGS),
    }
    return _write(ROUND / "manifests/SPLIT_MANIFEST.json", value)


def collection_manifest() -> str:
    packages = assets.observation_packages()
    units: list[dict[str, Any]] = []
    for unit_id in sorted(packages):
        for row in packages[unit_id]:
            manifest = assets.read_json(Path(row["manifest_path"]))
            units.append({
                "unit_id": unit_id,
                "observation_key": str(row["observation_key"]),
                "package_directory": str(row["package_directory"]),
                "file_count": manifest.get("file_count"),
                "package_content_sha256": manifest.get("package_content_sha256"),
                "observation_hash": manifest.get("observation_hash"),
                "source_frame": manifest.get("source_frame"),
            })
    value = {
        "schema_version": "driveclarify.rq1_v3_collection_manifest.v1",
        "collected_this_round": False,
        "reason_zh": "本轮未新采集 CARLA 数据，全部使用既有冻结观测包，故不涉及渲染方式选择。",
        "source_round": "reports/m1_real_dataset_expansion_v3/DC-M1-DATASET-EXP-V3-20260804T055600Z",
        "source_stage": "stage_a 冻结观测包",
        "rendering_zh": "来源轮次为真实 CARLA 渲染采集；本轮只读取，不重采、不修改、不删除。",
        "unit_count": len(units),
        "units": units,
        "package_manifest_sha256": digest([row["package_content_sha256"] for row in units]),
    }
    return _write(ROUND / "manifests/COLLECTION_MANIFEST.json", value)


def _fairness_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(str(ROUND / "cache/candidate_forwards/*/WORKER_RESULT.json"))):
        unit_directory = Path(path).parent
        result = json.loads(Path(path).read_text(encoding="utf-8"))
        records = result.get("fairness_records") or []
        identity_path = unit_directory / "MODEL_CHECKPOINT_IDENTITY.json"
        identity = json.loads(identity_path.read_text(encoding="utf-8")) if identity_path.is_file() else {}
        rows.append({
            "unit_id": unit_directory.name,
            "fairness": result.get("fairness"),
            "candidates_per_backend_limit": result.get("candidates_per_backend_limit"),
            "batch_count": result.get("batch_count"),
            "forward_records": len(records),
            "plans_present": len(list(unit_directory.glob("*_PLAN.json"))),
            "input_isolation_pass": sum(1 for row in records if row.get("input_isolation") == "PASS"),
            "state_before_matches_baseline": sum(1 for row in records if row.get("state_before_matches_baseline")),
            "state_after_matches_baseline": sum(1 for row in records if row.get("state_after_matches_baseline")),
            "checkpoint_sha256": identity.get("checkpoint_sha256"),
            "config_sha256": identity.get("config_sha256"),
            "model_training_flag": identity.get("model_training_flag"),
            "all_modules_eval": identity.get("all_modules_eval"),
            "inference_mode": identity.get("inference_mode"),
            "cuda_device": identity.get("cuda_device"),
        })
    return rows


def fairness_summary() -> str:
    rows = _fairness_rows()
    total_records = sum(row["forward_records"] for row in rows)
    value = {
        "schema_version": "driveclarify.rq1_v3_forward_fairness_summary.v1",
        "unit_count": len(rows),
        "candidate_forward_records": total_records,
        "plans_present_total": sum(row["plans_present"] for row in rows),
        "units_fairness_pass": sum(1 for row in rows if row["fairness"] == "PASS"),
        "records_input_isolation_pass": sum(row["input_isolation_pass"] for row in rows),
        "records_state_before_matches_baseline": sum(row["state_before_matches_baseline"] for row in rows),
        "records_state_after_matches_baseline": sum(row["state_after_matches_baseline"] for row in rows),
        "all_units_pass": all(row["fairness"] == "PASS" for row in rows) and bool(rows),
        "all_records_isolated_and_restored": (
            sum(row["input_isolation_pass"] for row in rows) == total_records
            and sum(row["state_before_matches_baseline"] for row in rows) == total_records
            and sum(row["state_after_matches_baseline"] for row in rows) == total_records
        ),
        "distinct_checkpoint_identities": sorted({
            json.dumps({key: row[key] for key in
                        ("checkpoint_sha256", "config_sha256", "model_training_flag",
                         "all_modules_eval", "inference_mode")}, sort_keys=True)
            for row in rows}),
        "training_or_pid_changes": {"backbone_or_perception_trained": False, "pid_modified": False,
                                   "vehicle_control_applied": False},
        "units": rows,
    }
    return _write(ROUND / "manifests/FORWARD_FAIRNESS_SUMMARY.json", value)


def inference_config_digest() -> str:
    freeze = json.loads((ROUND / "queue/S4_FREEZE.json").read_text(encoding="utf-8"))
    calibration = json.loads((ROUND / "queue/S3_DEV_CALIBRATE.json").read_text(encoding="utf-8"))
    value = {
        "schema_version": "driveclarify.rq1_v3_inference_config_digest.v1",
        "checkpoint_sha256": freeze["checkpoint_sha256"],
        "checkpoint_digest_orientation_clue": "cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044",
        "checkpoint_matches_orientation_clue": False,
        "orientation_clue_note_zh": "用户 §8 给出的线索摘要与本机实际 checkpoint 文件不符。"
                                    "如实记录实际值，不修改文件以凑旧摘要。",
        "code_version": freeze["code_version"],
        "frozen_config": freeze["frozen_config"],
        "threshold_calibration": {
            "objective": calibration.get("objective"),
            "grid_m": calibration.get("grid_m"),
            "tie_rule": calibration.get("tie_rule"),
            "selected_threshold_m": calibration.get("selected_threshold_m"),
            "selected_on_split": "DEV",
            "frozen_before_test_scoring": True,
        },
        "cache_key_fields": list(cache.REQUIRED_CACHE_FIELDS),
        "shared_across_candidates_zh": "两候选共享同一 RGB、自车状态、路线、历史、权重、模式、解码协议；只有解释文本不同。",
        "annotation_version": freeze["annotation_version"],
        "m2_status": freeze["m2_status"],
        "m_lex_status": freeze["m_lex_status"],
        "reused_previous_round_plans": False,
        "reuse_note_zh": "不复用 v2 的 A1/B1 plan：那会让候选文本与实际注入文本不一致。",
    }
    return _write(ROUND / "manifests/INFERENCE_CONFIG_DIGEST.json", value)


def run() -> dict[str, Any]:
    written = {
        "split_manifest": split_manifest(),
        "collection_manifest": collection_manifest(),
        "forward_fairness_summary": fairness_summary(),
        "inference_config_digest": inference_config_digest(),
    }
    print(json.dumps(written, ensure_ascii=False, indent=2))
    return written


if __name__ == "__main__":
    run()
