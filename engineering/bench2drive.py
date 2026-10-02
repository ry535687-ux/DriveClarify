#!/usr/bin/env python3
"""准备、启动和收集新的冻结 Bench2Drive A0/A1 配对；计划阶段不启动 GPU。"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "reports/driveclarify_transparent_bypass_full_bench2drive_v2"
OLD_ROOT = Path("/home/buaa/wrh/DriveClarify")
OLD_SIM = Path("/home/buaa/wrh/simlingo")


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def paths_from(config):
    return {k: (Path(v) if Path(v).is_absolute() else ROOT / v).resolve()
            for k, v in read(config).items()}


def mapped(name, paths):
    original = Path(name)
    if original.is_relative_to(OLD_SIM):
        return paths["simlingo_root"] / original.relative_to(OLD_SIM)
    if original.is_relative_to(OLD_ROOT):
        return ROOT / original.relative_to(OLD_ROOT)
    raise ValueError(f"未知冻结文件根目录：{name}")


def preflight(paths):
    freeze = read(ROOT / PROTOCOL / "FULL_B2D_FREEZE_RECEIPT.json")
    errors = []
    for group in ["scientific_files", "interface_sources"]:
        for name, expected in freeze[group].items():
            path = mapped(name, paths)
            if not path.is_file() or sha(path) != expected:
                errors.append(f"冻结文件缺失或摘要不符：{path}")
    for route in freeze["pair_order"]:
        path = mapped(route["route_path"], paths)
        if not path.is_file() or sha(path) != route["route_sha256"]:
            errors.append(f"冻结路线缺失或摘要不符：{path}")
    for key, suffix in [("native_python", ""), ("carla_root", "CarlaUE4.sh"),
                        ("internvl_root", "config.json")]:
        path = paths[key] / suffix if suffix else paths[key]
        if not path.is_file():
            errors.append(f"依赖文件缺失：{path}")
    expected_weights = freeze["checkpoint"]
    weight = paths["b2d_checkpoint"]
    if not weight.is_file() or weight.stat().st_size != expected_weights["size"] or sha(weight) != expected_weights["sha256"]:
        errors.append("B2D 权重摘要不符")
    release = read(ROOT / "engineering/release_assets.json")
    for record in release["downloads"]:
        marker = "simlingo/pretrained/InternVL2-1B/"
        if record["path"].startswith(marker):
            path = paths["internvl_root"] / record["path"][len(marker):]
            if not path.is_file() or path.stat().st_size != record["bytes"] or sha(path) != record["sha256"]:
                errors.append(f"视觉缓存摘要不符：{path}")
    sites = [paths["native_site_packages"]]
    if "native_extra_site_packages" in paths:
        sites.insert(0, paths["native_extra_site_packages"])
    packages = {}
    for site in sites:
        for distribution in importlib.metadata.distributions(path=[str(site)]):
            if distribution.metadata["Name"]:
                packages.setdefault(distribution.metadata["Name"].lower().replace("_", "-"), distribution.version)
    for name, expected in read(ROOT / "engineering/native_assets.json")["key_packages"].items():
        if packages.get(name) != expected:
            errors.append(f"原生包版本不符：{name}，要求 {expected}，实际 {packages.get(name)}")
    if packages.get("psutil") != "5.9.8":
        errors.append("进程管理依赖 psutil==5.9.8 缺失或版本不符")
    if errors:
        raise ValueError("\n".join(errors))
    return freeze


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"迁移模板身份变化：{old}")
    return text.replace(old, new)


def first_authoritative(attempt_root):
    """保留第一份合法最终结果，包含碰撞、阻塞、超时和低分。"""
    for attempt in sorted(Path(attempt_root).glob("attempt_*")):
        path = attempt / "official_checkpoint.json"
        if not path.is_file():
            continue
        records = read(path).get("_checkpoint", {}).get("records", [])
        if len(records) != 1:
            continue
        record = records[0]
        status = record.get("status", "")
        if status == "Started" or "crashed" in status.lower() or "couldn't" in status.lower():
            continue
        if not status.startswith(("Failed", "Completed", "Perfect")):
            continue
        if all(k in record.get("scores", {}) for k in ("score_composed", "score_route", "score_penalty")):
            return path, record
    return None


def prepare_plan(paths, output, all_routes=False, route_id=None, offscreen=False, gpu=0):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError(f"计划目录已存在，拒绝覆盖：{output}")
    if any(c.isspace() for c in str(output)):
        raise ValueError("原生 evaluator 的 shell 启动路径不支持空白，请使用不含空白的计划目录")
    freeze = preflight(paths)
    routes = freeze["pair_order"]
    if route_id is not None:
        routes = [r for r in routes if r["route_id"] == route_id]
        if not routes:
            raise ValueError(f"不在冻结清单中的 route ID：{route_id}")
    elif not all_routes:
        routes = routes[:1]
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    runtime.mkdir()
    checkpoint = output / "model/checkpoints/a1_selected.ckpt/pytorch_model.pt"
    checkpoint.parent.mkdir(parents=True)
    # 引用校验后的原始权重，避免为每份计划复制 2.57 GB；不改写权重。
    checkpoint.symlink_to(paths["b2d_checkpoint"])
    config_dir = output / "model/.hydra"
    config_dir.mkdir()
    original_config = ROOT / "engineering/env/b2d-model-config.yaml"
    config = original_config.read_text()
    if sha(original_config) != freeze["checkpoint"]["hydra_config_sha256"]:
        raise ValueError("B2D Hydra 快照摘要不符")
    variant = "variant: OpenGVLab/InternVL2-1B"
    if config.count(variant) != 2:
        raise ValueError("模型 variant 配置身份变化")
    config = config.replace(variant, "variant: " + json.dumps(str(paths["internvl_root"])))
    (config_dir / "config.yaml").write_text(config)
    shutil.copy2(original_config, runtime / "original-model-config.yaml")
    original_agent = ROOT / "driveclarify_transparent_bypass_v2/benchmark_agent.py"
    agent = replace_once(original_agent.read_text(), "'/home/buaa/wrh/simlingo/team_code/agent_simlingo.py'",
                         repr(str(paths["simlingo_root"] / "team_code/agent_simlingo.py")))
    (runtime / "benchmark_agent.py").write_text(agent)
    common = (ROOT / PROTOCOL / "tooling/common.py").read_text()
    values = {"ROOT": f"Path({str(ROOT)!r})", "SIM": f"Path({str(paths['simlingo_root'])!r})",
              "OUT": f"Path({str(output)!r})", "OLD": f"Path({str(output / 'unused_history')!r})",
              "PYTHON": repr(str(paths["native_python"])), "CHECKPOINT": f"Path({str(checkpoint)!r})"}
    common = "\n".join(key + "=" + values[key] if (key := line.split("=", 1)[0]) in values else line
                       for line in common.splitlines()) + "\n"
    (runtime / "common.py").write_text(common)
    runner = (ROOT / PROTOCOL / "tooling/run_route.py").read_text()
    runner = replace_once(runner, "Path('/home/buaa/CARLA_0.9.15/PythonAPI/carla')",
                          "Path(" + repr(str(paths["carla_root"] / "PythonAPI/carla")) + ")")
    runner = replace_once(runner, "'DISPLAY':':1'", "'DISPLAY':os.environ.get('DISPLAY','')")
    runner = replace_once(runner, "'XAUTHORITY':'/run/user/1000/gdm/Xauthority'",
                          "'XAUTHORITY':os.environ.get('XAUTHORITY','')")
    runner = replace_once(runner, "str(ROOT/'driveclarify_transparent_bypass_v2/benchmark_agent.py')",
                          "str(OUT/'runtime/benchmark_agent.py')")
    runner = replace_once(runner, "'--gpu-rank=0'", repr(f"--gpu-rank={gpu}"))
    if "native_extra_site_packages" in paths:
        runner = replace_once(runner, "    return env",
                              "    env['PYTHONPATH'] += os.pathsep + " + repr(str(paths["native_extra_site_packages"])) + "\n    return env")
    if offscreen:
        runner = replace_once(runner, "    return env", "    for key in ('DISPLAY','XAUTHORITY','SDL_VIDEODRIVER'):\n        env.pop(key,None)\n    return env")
    (runtime / "run_route.py").write_text(runner)
    for path in runtime.glob("*.py"):
        ast.parse(path.read_text())
    proxy = output / "carla_bootstrap_proxy"
    proxy.mkdir()
    script = "#!/bin/sh\nexec " + shlex.quote(str(paths["carla_root"] / "CarlaUE4.sh"))
    script += " /Game/Carla/Maps/Town01" + (" -RenderOffScreen" if offscreen else "") + ' "$@"\n'
    (proxy / "CarlaUE4.sh").write_text(script)
    (proxy / "CarlaUE4.sh").chmod(0o755)
    write(output / "interface_config.json", {"interface_version": freeze["interface_version"], "context": None})
    write(output / "BENCHMARK_VERSION_AUDIT.json", {"formal_checkpoint": freeze["checkpoint"]})
    generated = [*runtime.glob("*.py"), config_dir / "config.yaml", proxy / "CarlaUE4.sh",
                 output / "interface_config.json", output / "BENCHMARK_VERSION_AUDIT.json"]
    plan = {"scope": "NEW_FULL_B2D_REPRODUCTION" if all_routes else "NEW_SINGLE_ROUTE_QUALIFICATION",
            "source_freeze": freeze["freeze_digest"], "verified_frozen_source_files": len(freeze["scientific_files"]),
            "checkpoint_sha256": freeze["checkpoint"]["sha256"], "output": str(output),
            "paths": {k: str(v) for k, v in paths.items()}, "offscreen": offscreen, "gpu": gpu,
            "routes": [{**r, "route_path": str(mapped(r["route_path"], paths))} for r in routes],
            "runtime_files": {str(p.relative_to(output)): sha(p) for p in generated},
            "migration": "独立运行副本只调整宿主路径、模型缓存位置、GPU/显示启动设置；原冻结源码和权重未改写"}
    write(output / "PLAN.json", plan)
    return plan


def validate_plan(directory):
    directory = Path(directory).resolve()
    plan = read(directory / "PLAN.json")
    if plan["output"] != str(directory):
        raise ValueError("计划目录已移动，请在目标位置重新生成计划")
    for name, expected in plan["runtime_files"].items():
        if sha(directory / name) != expected:
            raise ValueError(f"运行副本被改动：{name}")
    return plan


def run_plan(directory, route_id=None, arm=None, resume=False, attempt=1, repair_note=None, dry_run=False):
    directory = Path(directory).resolve()
    plan = validate_plan(directory)
    paths = {k: Path(v) for k, v in plan["paths"].items()}
    preflight(paths)
    if attempt not in (1, 2, 3):
        raise ValueError("此启动器最多允许三个技术尝试；更多尝试需要另行记录工程修复协议")
    if attempt > 1 and not repair_note:
        raise ValueError("技术重试需要 --repair-note 说明已定位并修复的基础设施问题")
    if not plan["offscreen"] and not os.environ.get("DISPLAY"):
        raise ValueError("没有 DISPLAY；请设置目标显示，或重新生成带 --offscreen 的计划")
    if route_id is not None and route_id not in {r["route_id"] for r in plan["routes"]}:
        raise ValueError(f"路线不在计划中：{route_id}")
    for route in plan["routes"]:
        if route_id is not None and route["route_id"] != route_id:
            continue
        for selected_arm in route["arm_order"]:
            if arm is not None and selected_arm != arm:
                continue
            arm_root = directory / "results" / route["route_id"] / selected_arm
            existing = first_authoritative(arm_root)
            if existing:
                if resume:
                    print(f"保留第一份最终结果：{existing[0]}", flush=True)
                    continue
                raise ValueError(f"已有权威结果，拒绝重跑：{existing[0]}")
            output = arm_root / f"attempt_{attempt:02d}"
            if output.exists():
                raise ValueError(f"尝试目录已存在，拒绝覆盖：{output}")
            command = [str(paths["native_python"]), "-B", str(directory / "runtime/run_route.py"),
                       "--arm", selected_arm, "--route", route["route_path"], "--seed", str(route["seed"]),
                       "--port", "27000", "--output", str(output)]
            if plan["scope"] == "NEW_SINGLE_ROUTE_QUALIFICATION":
                command.append("--qualification")
            print(shlex.join(command), flush=True)
            if dry_run:
                continue
            if repair_note:
                with (directory / "TECHNICAL_REPAIRS.jsonl").open("a") as stream:
                    stream.write(json.dumps({"route": route["route_id"], "arm": selected_arm,
                                             "attempt": attempt, "reason": repair_note}) + "\n")
            environment = os.environ.copy()
            environment.update(PYTHONNOUSERSITE="1", PYTHONPATH=str(ROOT))
            if "native_extra_site_packages" in paths:
                environment["PYTHONPATH"] += os.pathsep + str(paths["native_extra_site_packages"])
            subprocess.run(command, check=True, env=environment)
            if not first_authoritative(arm_root):
                raise ValueError(f"尚无合法最终结果，停止后续运行，请检查 {output}/evaluator.log")


def collect(directory, output):
    directory = Path(directory).resolve()
    plan = validate_plan(directory)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    selected = []
    for route in plan["routes"]:
        for arm in ("A0", "A1"):
            found = first_authoritative(directory / "results" / route["route_id"] / arm)
            if not found:
                rows.append({"route_id": route["route_id"], "arm": arm, "status": "MISSING"})
                continue
            path, record = found
            destination = output / "official_merge" / arm
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination / (route["route_id"] + ".json"))
            rows.append({"route_id": route["route_id"], "arm": arm, "status": record["status"],
                         **record["scores"]})
            selected.append({"route_id": route["route_id"], "arm": arm, "path": str(path), "sha256": sha(path)})
    fields = ["route_id", "arm", "status", "score_composed", "score_route", "score_penalty"]
    with (output / "PAIRED_ROUTE_RESULTS.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    complete = len(selected) == 2 * len(plan["routes"])
    write(output / "RESULT_SOURCES.json", {"scope": plan["scope"], "complete": complete,
                                           "expected_pairs": len(plan["routes"]), "selected": selected,
                                           "missing": [r for r in rows if r["status"] == "MISSING"]})
    print(f"已收集 {len(selected)}/{2 * len(plan['routes'])} 份第一最终结果；{output}")
    return 0 if complete else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["preflight", "plan"]:
        child = sub.add_parser(name)
        child.add_argument("--paths", type=Path, required=True)
        if name == "plan":
            child.add_argument("--output", type=Path, required=True)
            group = child.add_mutually_exclusive_group()
            group.add_argument("--all", action="store_true")
            group.add_argument("--route-id")
            child.add_argument("--offscreen", action="store_true")
            child.add_argument("--gpu", type=int, default=0)
    child = sub.add_parser("run")
    child.add_argument("--plan", type=Path, required=True)
    child.add_argument("--route-id")
    child.add_argument("--arm", choices=["A0", "A1"])
    child.add_argument("--resume", action="store_true")
    child.add_argument("--attempt", type=int, default=1)
    child.add_argument("--repair-note")
    child.add_argument("--dry-run", action="store_true")
    child = sub.add_parser("collect")
    child.add_argument("--plan", type=Path, required=True)
    child.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            freeze = preflight(paths_from(args.paths))
            print(json.dumps({"status": "PASS", "frozen_source_files": len(freeze["scientific_files"]),
                              "scope": "文件、分发元数据与权重校验；未检查 GPU、显示或驾驶"}, ensure_ascii=False))
        elif args.command == "plan":
            plan = prepare_plan(paths_from(args.paths), args.output, args.all, args.route_id, args.offscreen, args.gpu)
            print(f"计划已生成：{args.output}/PLAN.json；{len(plan['routes'])} 个配对路线；尚未启动驾驶")
        elif args.command == "run":
            run_plan(args.plan, args.route_id, args.arm, args.resume, args.attempt, args.repair_note, args.dry_run)
        else:
            return collect(args.plan, args.output)
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Bench2Drive 未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
