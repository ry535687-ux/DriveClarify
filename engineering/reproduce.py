#!/usr/bin/env python3
"""源码仓库的复现工具。仅 CPU / 文件检查；不启动 CARLA 或下载模型。"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PAPER = "deliverables/paper_revision_20260915"
TESTS = ["tests/offline_v0", "tests/test_paper_answer_binding.py",
         "tests/test_paper_native_binding.py", "tests/engineering"]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_file(root, name):
    path = root / name
    if Path(name).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"文件超出仓库边界：{name}")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"文件缺失或为符号链接：{name}")
    return path


def fresh_directory(path):
    path = Path(path).resolve()
    # 独占创建：失败保留现场，重跑必须换一个输出目录。
    path.mkdir(parents=True, exist_ok=False)
    return path


def copy_files(root, destination, names):
    for name in sorted(set(names)):
        source = local_file(root, name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def run(root, args, log=None):
    env = os.environ.copy()
    env.update(PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", CUDA_VISIBLE_DEVICES="",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    command = [sys.executable, "-B", *args]
    if log is None:
        subprocess.run(command, cwd=root, env=env, check=True)
    else:
        with Path(log).open("w", encoding="utf-8") as stream:
            subprocess.run(command, cwd=root, env=env, check=True,
                           stdout=stream, stderr=subprocess.STDOUT)


def verify_assets(root):
    manifest = read(root / "engineering/paper_assets.json")
    errors = []
    for record in manifest["files"]:
        try:
            path = local_file(root, record["path"])
            if sha(path) != record["sha256"]:
                errors.append(f"摘要不符：{record['path']}")
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("\n".join(errors))
    return manifest


def dependencies(profile):
    names = ["jsonschema", "pytest"]
    if profile == "paper":
        names += ["numpy", "scipy"]
    result = []
    for name in names:
        try:
            result.append({"dependency": name, "version": importlib.metadata.version(name),
                           "status": "PRESENT"})
        except importlib.metadata.PackageNotFoundError:
            result.append({"dependency": name, "status": "MISSING"})
    return result


def doctor(root, args):
    if args.profile == "native":
        return native_doctor(root, args)
    checks = dependencies(args.profile)
    required = "python3.13" if args.profile == "paper" else "python>=3.10"
    supported = sys.version_info[:2] == (3, 13) if args.profile == "paper" else sys.version_info >= (3, 10)
    checks.append({"dependency": required, "version": sys.version.split()[0],
                   "status": "PRESENT" if supported else "MISSING"})
    if args.profile == "paper":
        try:
            manifest = verify_assets(root)
            checks.append({"dependency": "paper_assets", "count": len(manifest["files"]),
                           "status": "PRESENT"})
        except ValueError as exc:
            checks.append({"dependency": "paper_assets", "status": "MISSING", "reason": str(exc)})
    result = {"profile": args.profile, "checks": checks,
              "ready": all(x["status"] == "PRESENT" for x in checks),
              "scope": "依赖元数据/文件预检；不是运行结果"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


def native_doctor(root, args):
    if args.paths is None:
        raise ValueError("native 检查需要 --paths engineering/paths.local.json；参照 paths.example.json")
    config = read(args.paths)
    paths = {k: (Path(v) if Path(v).is_absolute() else root / v).resolve()
             for k, v in config.items()}
    lock = read(root / "engineering/native_assets.json")
    checks = []
    for key, suffix in [("simlingo_root", "team_code/agent_simlingo.py"),
                        ("carla_root", "CarlaUE4.sh"), ("native_python", ""),
                        ("internvl_root", "config.json")]:
        path = paths[key] / suffix if suffix else paths[key]
        checks.append({"asset": key, "status": "PRESENT" if path.is_file() else "MISSING"})
    for record in lock["checkpoints"]:
        path = paths[record["key"]]
        status = "MISSING"
        if path.is_file():
            status = "SIZE_MATCH_HASH_NOT_CHECKED" if path.stat().st_size == record["bytes"] else "SIZE_MISMATCH"
            if args.hash_weights:
                status = "VERIFIED" if sha(path) == record["sha256"] else "HASH_MISMATCH"
        checks.append({"asset": record["key"], "status": status, "source": record["source"]})
    site = paths["native_site_packages"]
    distributions = {d.metadata["Name"].lower().replace("_", "-"): d.version
                     for d in importlib.metadata.distributions(path=[str(site)]) if d.metadata["Name"]}
    for name, expected in lock["key_packages"].items():
        actual = distributions.get(name)
        checks.append({"asset": name, "expected": expected, "actual": actual,
                       "status": "VERIFIED" if actual == expected else "VERSION_MISMATCH"})
    for record in lock["simlingo_files"]:
        path = paths["simlingo_root"] / record["path"]
        status = "MISSING" if not path.is_file() else (
            "VERIFIED" if sha(path) == record["sha256"] else "HASH_MISMATCH")
        checks.append({"asset": record["path"], "status": status})
    result = {"profile": "native", "checks": checks, "ready_for_new_experiment": False,
              "scope": "只读检查；未导入 torch/carla，未检查 GPU/显示/驾驶资格",
              "remaining": lock["remaining"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # 2 专门表示尚有原生复现缺口，不能被解释为可自动开跑。
    return 2


def reproduce_paper(root, output):
    if sys.version_info[:2] != (3, 13):
        raise ValueError("论文参考输出的逐字节复现需要 Python 3.13；其他 CPU 测试支持 Python 3.10+")
    manifest = verify_assets(root)
    if any(d["status"] == "MISSING" for d in dependencies("paper")):
        raise ValueError("请先安装 requirements/paper.txt")
    out = fresh_directory(output)
    names = [r["path"] for r in manifest["files"] if r["role"] == "input"]
    copy_files(root, out, names)
    (out / PAPER / "data").mkdir(exist_ok=True)
    (out / "logs").mkdir()
    commands = [[f"{PAPER}/analyze.py", "predict"],
                [f"{PAPER}/analyze.py", "analyze"],
                [f"{PAPER}/main_table_selectivity.py"],
                [f"{PAPER}/ablation_statistics.py"],
                [f"{PAPER}/analyze.py", "closed-loop"]]
    for index, command in enumerate(commands, 1):
        print(f"[{index}/{len(commands)}] {' '.join(command)}", flush=True)
        run(out, command, out / "logs" / f"{index:02d}.log")
    comparisons = []
    for record in manifest["files"]:
        if record["role"] != "reference":
            continue
        generated = out / record["path"]
        actual = sha(generated) if generated.is_file() else None
        comparisons.append({"path": record["path"], "match": actual == record["sha256"],
                            "expected_sha256": record["sha256"], "actual_sha256": actual})
    # 再查源文件：输出只能发生在副本；不能覆盖任何历史结果。
    verify_assets(root)
    result = {"status": "PASS" if all(r["match"] for r in comparisons) else "MISMATCH",
              "scope": "冻结数据的 CPU 重预测/评分/消融及历史闭环统计；无新驾驶",
              "python": sys.version.split()[0], "dependencies": dependencies("paper"),
              "commands": commands, "comparison": comparisons,
              "source_assets_unchanged": True, "new_carla_runs": 0}
    write(out / "REPRODUCTION_RECEIPT.json", result)
    print(f"{result['status']}: {out / 'REPRODUCTION_RECEIPT.json'}")
    return 0 if result["status"] == "PASS" else 1


def source_names(root):
    patterns = ["driveclarify_*/*.py", "driveclarify_*/**/*.py",
                "driveclarify_*.py", "sitecustomize.py", "tools/**/*.py", "tools/**/*.sh",
                "experiments/**/*.py", "experiments/**/*.json", "experiments/**/*.xml",
                "experiments/**/*.md", "configs/*", "schemas/*",
                "reports/*/scripts/*.py", "reports/*/tooling/*.py"]
    names = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            relative = path.relative_to(root)
            if path.is_file() and not path.is_symlink() and not set(relative.parts) & {
                "__pycache__", "outputs", "formal_runs", "formal_native", "node_modules"}:
                names.add(relative.as_posix())
    return names


def inventory(root, output):
    names = sorted(n for n in source_names(root) if n.endswith(".py"))
    imports = collections.Counter()
    absolute_paths, errors = [], []
    for name in names:
        try:
            tree = ast.parse(local_file(root, name).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeError) as exc:
            errors.append({"path": name, "error": str(exc)})
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imports[node.module.split(".")[0]] += 1
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/home/"):
                absolute_paths.append({"path": name, "line": node.lineno})
    external = {k: v for k, v in sorted(imports.items()) if k not in sys.stdlib_module_names
                and not (root / k).exists() and not (root / f"{k}.py").exists()}
    write(output, {"python_files": len(names), "external_import_occurrences": external,
                   "absolute_path_locations": absolute_paths, "parse_errors": errors,
                   "scope": "静态 AST 清单；包含延迟/条件导入，不代表每个入口都需要全部依赖"})
    print(f"{len(names)} 个 Python 文件，{len({r['path'] for r in absolute_paths})} 个文件含 /home/ 路径；{output}")
    return 1 if errors else 0


def export_source(root, output):
    assets = verify_assets(root)
    names = source_names(root) | {r["path"] for r in assets["files"]}
    for pattern in ["README.md", "CONTRIBUTING.md", "pyproject.toml", ".gitignore", ".gitattributes", "reproduce.sh", "Dockerfile.cpu",
                    "GIT_TRACKING_MANIFEST.md", "requirements/*.txt", ".github/workflows/*.yml",
                    "engineering/**/*.md", "engineering/**/*.py", "engineering/**/*.json",
                    "engineering/**/*.patch", "engineering/**/*.yaml", "engineering/**/*.txt",
                    "tests/offline_v0/**/*", "tests/test_paper*binding.py", "tests/engineering/*.py", "design/v0/**/*",
                    "scripts/*b2d*", "scripts/*bench2drive*"]:
        names.update(p.relative_to(root).as_posix() for p in root.glob(pattern)
                     if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts
                     and not p.name.endswith(".local.json") and p.suffix != ".pyc")
    native = read(root / "engineering/native_assets.json")
    names.update(native["project_metadata"])
    # 显式文件清单；不复制历史运行、大模型、.git、账户信息或所有 reports。
    for name in names:
        path = local_file(root, name)
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError(f"发布源码中发现 >10 MiB 文件，请单独审阅：{name}")
    out = fresh_directory(output)
    copy_files(root, out, names)
    (out / ".dockerignore").write_text(".git\nbuild\n.venv*\n__pycache__\n*.local.json\n", encoding="utf-8")
    records = [{"path": name, "bytes": (out / name).stat().st_size,
                "sha256": sha(out / name)} for name in sorted(names)]
    records.append({"path": ".dockerignore", "bytes": (out / ".dockerignore").stat().st_size,
                    "sha256": sha(out / ".dockerignore")})
    write(out / "RELEASE_MANIFEST.json", {"scope": "源代码及冻结离线复现数据；原生资产需另行提供",
                                          "files": records})
    print(f"本地发布预览：{out}；{len(records)} 文件，{sum(r['bytes'] for r in records)/1024**2:.2f} MiB")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="源仓库根目录")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("doctor", help="只读环境/数据检查")
    check.add_argument("--profile", choices=["cpu", "paper", "native"], default="cpu")
    check.add_argument("--paths", type=Path)
    check.add_argument("--hash-weights", action="store_true", help="流式读取权重做 SHA-256，不加载模型")
    sub.add_parser("test", help="明确范围的 CPU 回归")
    for command in ["demo", "paper", "inventory", "export"]:
        child = sub.add_parser(command)
        child.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "doctor":
            return doctor(root, args)
        if args.command == "test":
            run(root, ["-m", "pytest", "-q", *TESTS])
            run(root, ["-m", "unittest", "discover", "-s",
                       "experiments/driveclarify_three_policy_dev/tests", "-q"])
            return 0
        if args.command == "demo":
            out = fresh_directory(args.output)
            run(root, ["-m", "driveclarify_offline", "evaluate", "--output-dir", str(out)])
            return 0
        if args.command == "paper":
            return reproduce_paper(root, args.output)
        if args.command == "inventory":
            return inventory(root, args.output)
        return export_source(root, args.output)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"复现未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
