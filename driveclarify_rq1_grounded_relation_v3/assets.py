"""真实观测资产读取：观测包身份、自车/导航状态、公开地图派生拓扑。

这里是唯一允许触碰历史冻结资产的读取层，全部只读。方法侧永远拿不到本模块读出的
TASK_BINDING candidate_bindings；那一项只交给 labels.py 与 M6。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from driveclarify_static_branch.m1_expansion_v3 import generated_topology

ROOT = Path(__file__).resolve().parents[1]
V3_BASE = ROOT / "reports/m1_real_dataset_expansion_v3/DC-M1-DATASET-EXP-V3-20260804T055600Z"
V3_CAMPAIGN = V3_BASE / "combined_runtime_campaigns/DC-M1-V3-RUNTIME-C1-20260804T063000Z"


class AssetError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def shortlist_rows() -> dict[str, dict[str, Any]]:
    rows = read_json(V3_BASE / "V3_SHORTLIST.json").get("candidates", [])
    return {str(row["unit_id"]): row for row in rows}


def unit_directory(unit_id: str) -> Path:
    path = V3_BASE / "units" / unit_id
    if not path.is_dir():
        raise AssetError("UNIT_DIRECTORY_MISSING:" + unit_id)
    return path


def public_topology(unit_id: str) -> dict[str, Any]:
    """从公开 OpenDRIVE 与场景无关路线 fixture 重算分支拓扑。

    重算而非读取历史 BRANCH_TOPOLOGY_GROUND_TRUTH.json，以证明方法侧使用的每个
    拓扑字段都可由公开地图派生。
    """
    candidate = shortlist_rows().get(unit_id)
    if candidate is None:
        raise AssetError("SHORTLIST_ROW_MISSING:" + unit_id)
    return generated_topology(candidate, unit_directory(unit_id) / "SCENARIO_FREE_ROUTE.xml")


def privileged_task_binding(unit_id: str) -> dict[str, Any]:
    """标注/M6 专属。任何 M0–M5 代码路径都不得调用本函数。"""
    return read_json(unit_directory(unit_id) / "TASK_BINDING.json")


def observation_packages() -> dict[str, list[dict[str, Any]]]:
    """索引历史 stage_a 真实观测包，按 unit_id 归组。"""
    index: dict[str, list[dict[str, Any]]] = {}
    for manifest_path in sorted(V3_CAMPAIGN.glob("stage_a/runs/*/OBSERVATION_PACKAGE_MANIFEST.json")):
        manifest = read_json(manifest_path)
        directory = manifest.get("package_directory")
        if not isinstance(directory, str) or not directory:
            # 历史上有两个 stage_a run 未产出观测包；如实跳过，不伪造观测。
            continue
        package = Path(directory)
        identity_path = package / "metadata/source_identity.json"
        if not identity_path.is_file():
            continue
        identity = read_json(identity_path)
        index.setdefault(str(identity["unit_id"]), []).append({
            "unit_id": str(identity["unit_id"]),
            "observation_key": manifest_path.parent.name,
            "package_directory": str(package),
            "observation_sha256": str(manifest["observation_hash"]),
            "package_content_sha256": str(manifest["package_content_sha256"]),
            "source_frame": manifest.get("source_frame"),
            "camera_images_sha256": sha256_file(package / "model_ready/camera_images.pt"),
            "manifest_path": str(manifest_path),
            "capture_origin": "HISTORICAL_FROZEN_STAGE_A_REAL_CARLA",
        })
    return index


def observation_states(package_directory: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    package = Path(package_directory)
    return read_json(package / "metadata/ego_state.json"), read_json(package / "metadata/navigation_state.json")


def observation_speed(package_directory: str | Path) -> float | None:
    """从观测包 metadata 读取真实自车速度；缺失时返回 None 而非 0。"""
    metadata = read_json(Path(package_directory) / "metadata/runtime_metadata.json")
    for key in ("vehicle_speed_mps", "vehicle_speed", "speed_mps"):
        if key in metadata:
            return float(metadata[key])
    ego, _ = observation_states(package_directory)
    velocity = ego.get("velocity_xyz_mps")
    if isinstance(velocity, (list, tuple)) and len(velocity) == 3:
        return float(sum(component * component for component in velocity) ** 0.5)
    return None
