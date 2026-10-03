#!/usr/bin/env python3
"""在独立工作区还原原生源代码；不安装驱动、不启动训练或驾驶。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from driveclarify.paths import RESOURCES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--upstream-source", default="https://github.com/RenzKa/simlingo.git",
                        help="上游 URL 或已有本地仓库；本地仓库保持原样")
    parser.add_argument("--carla-root", type=Path, required=True)
    parser.add_argument("--native-python", type=Path, required=True)
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    sim = workspace / "simlingo"
    if sim.exists():
        parser.error(f"拒绝覆盖已有上游目录：{sim}；请选择新工作区")
    lock = json.loads((RESOURCES / "native.json").read_text())
    patch = RESOURCES / "simlingo.patch"
    if hashlib.sha256(patch.read_bytes()).hexdigest() != lock["patch_sha256"]:
        parser.error("上游补丁摘要不符")
    workspace.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--no-hardlinks", "--no-checkout", args.upstream_source, str(sim)], check=True)
    subprocess.run(["git", "-C", str(sim), "checkout", "--detach", lock["simlingo_base_commit"]], check=True)
    subprocess.run(["git", "-C", str(sim), "apply", "--check", str(patch)], check=True)
    subprocess.run(["git", "-C", str(sim), "apply", str(patch)], check=True)
    for name in lock["extra_files"]:
        destination = sim / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RESOURCES / "upstream_extra" / name, destination)
    for record in lock["simlingo_files"]:
        path = sim / record["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"还原后摘要不符：{record['path']}")
    freeze = json.loads((RESOURCES / "routes.json").read_text())
    for route in freeze["pair_order"]:
        payload = route["xml"].encode("utf-8")
        if hashlib.sha256(payload).hexdigest() != route["route_sha256"]:
            raise ValueError("路线摘要不符")
        destination = sim / route["route_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_bytes() != payload:
            raise ValueError(f"拒绝覆盖不同的上游路线：{destination}")
        destination.write_bytes(payload)
    python = args.native_python.resolve()
    paths = {
        "simlingo_root": str(sim), "carla_root": str(args.carla_root.resolve()),
        "native_python": str(python),
        "native_site_packages": str(python.parent.parent / "lib/python3.8/site-packages"),
        "base_checkpoint": str(sim / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"),
        "b2d_checkpoint": str(workspace / "models/a1_selected.ckpt/pytorch_model.pt"),
        "internvl_root": str(sim / "pretrained/InternVL2-1B"),
    }
    config = workspace / "paths.local.json"
    with config.open("x") as stream:
        json.dump(paths, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    receipt = {"status": "PASS", "verified_source_files": len(lock["simlingo_files"]),
               "verified_route_files": len(freeze["pair_order"]),
               "base_commit": lock["simlingo_base_commit"], "paths": str(config),
               "scope": "源代码还原；环境与模型资产另行准备；未运行原生实验"}
    (workspace / "SOURCE_RESTORE_RECEIPT.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
