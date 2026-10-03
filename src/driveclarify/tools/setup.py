"""Prepare the complete native stack and optionally run a frozen experiment."""
from __future__ import annotations

import argparse
import ctypes.util
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import xml.etree.ElementTree as ET

from driveclarify.paths import RESOURCES
from . import assets, benchmark, prepare


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".writing")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def clean_environment():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return environment


def execute(command, **kwargs):
    print("执行：" + " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, env=clean_environment(), **kwargs)


def download_runtime(record, cache):
    """Pin upstream object identity; record a local hash, not an invented upstream hash."""
    target = assets.checked_path(cache, record["path"])
    if "sha256" in record:
        return assets.download_file(record, cache)
    receipt = target.with_name(target.name + ".receipt.json")
    if target.exists():
        if not receipt.is_file():
            raise ValueError(f"下载文件没有校验凭据：{target}")
        saved = benchmark.read(receipt)
        if saved["object"] != record or saved["sha256"] != assets.digest(target) or target.stat().st_size != record["bytes"]:
            raise ValueError(f"运行时缓存校验失败：{target}")
        return target
    partial = assets.checked_path(cache, record["path"] + ".partial")
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(5):
        try:
            request = urllib.request.Request(record["url"], method="HEAD")
            with urllib.request.urlopen(request, timeout=45) as response:
                if (response.headers.get("ETag") != record["etag"] or
                        int(response.headers.get("Content-Length", -1)) != record["bytes"]):
                    raise ValueError(f"上游运行时对象已变化：{record['path']}")
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > record["bytes"]:
                raise ValueError(f"断点文件超过固定大小：{partial}")
            if offset < record["bytes"]:
                headers = {"If-Match": record["etag"], "User-Agent": "DriveClarify/0.2.1"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                with urllib.request.urlopen(urllib.request.Request(record["url"], headers=headers), timeout=45) as response:
                    if response.headers.get("ETag") != record["etag"]:
                        raise ValueError("下载响应的对象身份不符")
                    resumed = response.status == 206
                    if resumed and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                        raise ValueError("下载断点不符")
                    written = offset if resumed else 0
                    reported = time.monotonic()
                    with partial.open("ab" if resumed else "wb") as stream:
                        while block := response.read(4 * 1024 * 1024):
                            written += len(block)
                            if written > record["bytes"]:
                                raise ValueError("运行时下载超过固定大小")
                            stream.write(block)
                            if time.monotonic() - reported > 20:
                                print(f"{record['path']}：{written}/{record['bytes']} 字节", flush=True)
                                reported = time.monotonic()
            if partial.stat().st_size != record["bytes"]:
                raise OSError("运行时下载尚未完成")
            save(receipt, {"object": record, "sha256": assets.digest(partial),
                           "integrity": "固定 HTTPS 对象 ETag/大小；本地 SHA-256；解压时检查 gzip CRC"})
            partial.replace(target)
            return target
        except OSError:
            if attempt == 4:
                raise
            print("运行时下载中断，保留断点重试。", flush=True)
            time.sleep(2)
    raise RuntimeError("运行时下载未完成")


def extract(archive, root):
    """Support CARLA's relative links without letting tar entries escape the root."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive, "rb") as expanded, tarfile.open(fileobj=expanded, mode="r|") as stream:
        for member in stream:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or "\\" in member.name:
                raise ValueError(f"压缩包路径越界：{member.name}")
            target = root / member.name
            if not target.resolve().is_relative_to(root):
                raise ValueError(f"压缩包目标越界：{member.name}")
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                if link.is_absolute():
                    raise ValueError("拒绝压缩包中的绝对链接")
                linked = target.parent / member.linkname if member.issym() else root / member.linkname
                if not linked.resolve().is_relative_to(root):
                    raise ValueError("压缩包链接越界")
            elif not (member.isfile() or member.isdir()):
                raise ValueError(f"不支持的压缩包条目：{member.name}")
            # Apply the same filter on Python 3.10 and 3.13; newer tar defaults
            # otherwise vary. Ownership is never restored from the archive.
            if sys.version_info >= (3, 12):
                stream.extract(member, root, filter="data")
            else:
                stream.extract(member, root)
        # tar stops at its own end marker, which can precede the gzip trailer.
        # Drain the gzip stream so CRC/length validation always runs.
        while expanded.read(4 * 1024 * 1024):
            pass


def required_towns():
    freeze = benchmark.read(RESOURCES / "routes.json")
    return sorted({ET.fromstring(r["xml"]).find("route").attrib["town"] for r in freeze["pair_order"]})


def verify_carla(root):
    root = Path(root)
    if not (root / "CarlaUE4.sh").is_file():
        raise ValueError(f"CARLA 启动文件缺失：{root}")
    if not list((root / "PythonAPI/carla/dist").glob("carla-0.9.15-*")):
        raise ValueError("CARLA 0.9.15 客户端包缺失")
    maps = root / "CarlaUE4/Content/Carla/Maps"
    missing = [town for town in required_towns()
               if not (maps / (town + ".umap")).is_file() and not (maps / town / (town + ".umap")).is_file()]
    if missing:
        raise ValueError("CARLA 路线地图缺失：" + ", ".join(missing))


def install_carla(root, cache, records):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    receipt = root / "DRIVECLARIFY_INSTALL.json"
    if receipt.exists():
        if benchmark.read(receipt)["objects"] != records:
            raise ValueError("CARLA 安装清单变化；请选择新的安装目录")
        verify_carla(root)
        return
    owner = root / ".driveclarify-installing.json"
    if owner.exists():
        if benchmark.read(owner) != records:
            raise ValueError("已有安装目录属于另一份运行时清单")
    elif any(root.iterdir()):
        raise ValueError(f"拒绝修改已有 CARLA；可用 --carla-root 指定：{root}")
    else:
        save(owner, records)
    for record in records:
        marker = root / (record["path"] + ".extracted.json")
        if marker.exists() and benchmark.read(marker) == record:
            continue
        print("准备运行时：" + record["path"], flush=True)
        archive = download_runtime(record, cache)
        extract(archive, root)
        save(marker, record)
    verify_carla(root)
    save(receipt, {"status": "PASS", "objects": records, "towns": required_towns(),
                   "scope": "CARLA 0.9.15 与路线所需地图已安装；尚未启动仿真"})


def install_environment(prefix, cache, bootstrap):
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    lock = {name: assets.digest(RESOURCES / name) for name in
            ("native-requirements.txt", "conda-linux64.explicit.txt")}
    receipt = prefix / "DRIVECLARIFY_ENV.json"
    if receipt.exists():
        if benchmark.read(receipt)["locks"] != lock:
            raise ValueError("原生依赖清单变化；请选择新环境路径")
        execute([prefix / "bin/python", "-m", "pip", "check"])
        return
    conda = shutil.which("conda")
    if not conda:
        miniforge = prefix.parent / "miniforge"
        installer = assets.download_file(bootstrap, cache)
        if not (miniforge / "bin/conda").is_file():
            if miniforge.exists():
                raise ValueError(f"Miniforge 安装未完成；保留文件，请使用新 --root：{miniforge}")
            execute(["bash", installer, "-b", "-p", miniforge])
        conda = str(miniforge / "bin/conda")
    owner = prefix.parent / (prefix.name + ".installing.json")
    if owner.exists():
        if benchmark.read(owner) != lock:
            raise ValueError("环境安装断点与清单不符")
    elif prefix.exists():
        raise ValueError(f"拒绝修改已有原生环境；可用 --native-python 指定：{prefix}")
    else:
        save(owner, lock)
    python = prefix / "bin/python"
    if not (prefix / "conda-meta/history").is_file():
        execute([conda, "create", "--yes", "--prefix", prefix, "--file", RESOURCES / "conda-linux64.explicit.txt"])
    execute([python, "-c", "import sys; assert sys.version_info[:3] == (3,8,18), sys.version"])
    execute([python, "-m", "pip", "install", "--index-url", "https://pypi.org/simple",
             "numpy==1.23.0", "torch==2.2.0", "torchvision==0.17.0", "torchaudio==2.2.0"])
    execute([python, "-m", "pip", "install", "--index-url", "https://pypi.org/simple",
             "-r", RESOURCES / "native-requirements.txt"])
    environment = clean_environment()
    environment["DS_BUILD_OPS"] = "0"
    subprocess.run([str(python), "-m", "pip", "install", "--no-build-isolation", "--index-url",
                    "https://pypi.org/simple", "deepspeed==0.16.2"], check=True, env=environment)
    execute([python, "-m", "pip", "check"])
    save(receipt, {"status": "PASS", "locks": lock})


def host_check(root, needs_downloads, gpu):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("原生驾驶入口支持 Linux x86_64；建议 Ubuntu 22.04")
    if os.geteuid() == 0:
        raise ValueError("CARLA 拒绝以 root 启动，请在普通用户下运行")
    if not shutil.which("git"):
        raise ValueError("缺少 Git；Ubuntu：sudo apt-get install git")
    if not shutil.which("nvidia-smi"):
        raise ValueError("未检测到 NVIDIA 驱动；安装主机驱动后再运行")
    missing = [name for name in ("vulkan", "X11", "glib-2.0") if not ctypes.util.find_library(name)]
    if missing:
        raise ValueError("主机运行库缺失：" + ", ".join(missing) +
                         "；Ubuntu：sudo apt-get install libvulkan1 libx11-6 libglib2.0-0")
    result = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.total,driver_version",
                             "--format=csv,noheader"], check=True, capture_output=True, text=True)
    lines = result.stdout.strip().splitlines()
    if gpu < 0 or not any(int(line.split(",")[0]) == gpu for line in lines):
        raise ValueError(f"GPU 编号不存在：{gpu}")
    driver = next(line.split(",")[-1].strip() for line in lines if int(line.split(",")[0]) == gpu)
    if int(driver.split(".")[0]) < 535:
        raise ValueError(f"请使用 NVIDIA 535 或更新驱动；当前 {driver}")
    print("主机 GPU：\n" + result.stdout.strip(), flush=True)
    root.mkdir(parents=True, exist_ok=True)
    if needs_downloads and shutil.disk_usage(root).free < 100 * 1024 ** 3:
        raise ValueError("全新准备需要至少 100 GiB 空闲空间（归档、地图、模型、环境及缓存）；可用 --paths 复用已准备工作区")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("build/native"))
    parser.add_argument("--paths", type=Path, help="复用准备好的 paths.local.json；不下载或修改已有环境")
    parser.add_argument("--carla-root", type=Path, help="复用已安装 CARLA 0.9.15 与完整地图")
    parser.add_argument("--native-python", type=Path, help="复用已安装依赖的 Python 3.8.18")
    parser.add_argument("--environment-only", action="store_true")
    parser.add_argument("--setup-only", action="store_true", help="只安装与校验，不创建驾驶计划")
    parser.add_argument("--output", type=Path, help="运行计划目录；默认生成新目录")
    parser.add_argument("--all", action="store_true", help="220 路线；默认单路线 A0/A1 配对")
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="完成准备并打印运行命令；不启动驾驶")
    parser.add_argument("--resume", action="store_true", help="续跑 --output 指定的已有计划")
    parser.add_argument("--attempt", type=int, default=1, help="技术尝试编号，最多 3 次；重试需修复说明")
    parser.add_argument("--repair-note", help="技术故障的修复原因；不改变已获得的合法结果")
    parser.add_argument("--check", action="store_true", help="只显示主机与下载清单，不下载安装")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        lock = benchmark.read(RESOURCES / "runtime.json")
        if args.check:
            print(json.dumps({"platform": platform.platform(), "root": str(root), "runtime": lock,
                              "towns": required_towns(), "minimum_free_gib": 100}, ensure_ascii=False, indent=2))
            return 0
        if args.resume and (args.output is None or args.setup_only or args.environment_only):
            raise ValueError("--resume 需要 --output，且不能与只安装模式一起使用")
        if args.attempt not in (1, 2, 3) or (args.attempt > 1 and not args.repair_note):
            raise ValueError("技术尝试最多 3 次；--attempt 2/3 需要 --repair-note")
        if args.paths and (args.carla_root or args.native_python or args.environment_only):
            raise ValueError("--paths 已包含环境与 CARLA 路径，不可再指定安装参数")
        paths_file = args.paths.resolve() if args.paths else root / "workspace/paths.local.json"
        owned_ready = (root / "SETUP_RECEIPT.json").is_file()
        started = root / "SETUP_STARTED.json"
        if started.exists() and benchmark.read(started) != lock:
            raise ValueError("安装断点与当前运行时清单不同；请选择新的 --root")
        host_check(root, not args.paths and not owned_ready and not started.exists(), args.gpu)
        if not args.paths:
            save(started, lock)
            cache = root / "downloads"
            cache.mkdir(parents=True, exist_ok=True)
            native = args.native_python.resolve() if args.native_python else root / "env/bin/python"
            if not args.native_python:
                install_environment(root / "env", cache, lock["miniforge"])
            if args.environment_only:
                return 0
            carla = args.carla_root.resolve() if args.carla_root else root / "carla"
            if args.carla_root:
                verify_carla(carla)
            else:
                install_carla(carla, cache, lock["carla"])
            prepare.main(["--workspace", str(root / "workspace"), "--carla-root", str(carla),
                          "--native-python", str(native)])
            if assets.main(["--output", str(root / "workspace")]):
                return 1
        paths = benchmark.paths_from(paths_file)
        execute([paths["native_python"], "-c", "import sys; assert sys.version_info[:3] == (3,8,18), sys.version"])
        verify_carla(paths["carla_root"])
        freeze = benchmark.preflight(paths)
        if not args.paths:
            save(root / "SETUP_RECEIPT.json", {"status": "PASS", "paths": str(paths_file),
                 "routes": len(freeze["pair_order"]), "towns": required_towns(),
                 "scope": "完整文件、地图、环境元数据与模型校验；驾驶结果另行生成"})
        if not args.dry_run:
            environment = clean_environment()
            if "native_extra_site_packages" in paths:
                environment["PYTHONPATH"] = str(paths["native_extra_site_packages"])
            probe = ("import torch, carla; assert torch.cuda.is_available(), 'CUDA 不可用'; "
                     f"print('CUDA GPU:', torch.cuda.get_device_name({args.gpu})); "
                     f"print('CUDA kernel:', torch.ones(1, device='cuda:{args.gpu}').sum().item())")
            subprocess.run([str(paths["native_python"]), "-c", probe], check=True, env=environment)
        if args.setup_only:
            print(f"准备完成：{paths_file}；下一步：driveclarify setup --paths {paths_file}", flush=True)
            return 0
        output = args.output.resolve() if args.output else root / ("run-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid()))
        if args.resume:
            plan = benchmark.validate_plan(output)
            if plan["paths"] != {k: str(v) for k, v in paths.items()}:
                raise ValueError("续跑计划的路径与所选工作区不符")
        else:
            benchmark.prepare_plan(paths, output, all_routes=args.all, offscreen=True, gpu=args.gpu)
        benchmark.run_plan(output, resume=args.resume, attempt=args.attempt,
                           repair_note=args.repair_note, dry_run=args.dry_run)
        if args.dry_run:
            print(f"命令检查完成；计划：{output}；未启动驾驶", flush=True)
            return 0
        summary = output / ("summary-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid()))
        status = benchmark.collect(output, summary, merge=True)
        print(f"结果：{summary}", flush=True)
        return status
    except (OSError, EOFError, tarfile.TarError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"原生复现未完成：{exc}\n修复后重新执行原命令，已完成的下载和安装会复用。", file=sys.stderr)
        return 1
