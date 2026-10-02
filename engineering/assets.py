#!/usr/bin/env python3
"""下载固定版本资产，校验字节与摘要；不加载模型或启动仿真。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def checked_path(root, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
        raise ValueError(f"资产路径不合法：{name}")
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"资产路径越界：{name}")
    for path in [target, *target.parents]:
        if path == root.parent:
            break
        if path.is_symlink():
            raise ValueError(f"资产路径含符号链接：{path}")
    return target


def verify(path, record):
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"资产缺失：{path}")
    if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
        raise ValueError(f"资产大小或摘要不符：{path}")


def download_file(record, root):
    target = checked_path(root, record["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        verify(target, record)
        return target
    partial = checked_path(root, record["path"] + ".partial")
    for attempt in range(5):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > record["bytes"]:
            raise ValueError(f"断点文件过大，请检查后删除：{partial}")
        if offset == record["bytes"]:
            verify(partial, record)
            partial.replace(target)
            return target
        headers = {"User-Agent": "DriveClarify-reproduction/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(record["url"], headers=headers), timeout=45) as response:
                resumed = response.status == 206
                if resumed and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("服务器返回了错误的下载断点")
                written = offset if resumed else 0
                last_report = time.monotonic()
                with partial.open("ab" if resumed else "wb") as stream:
                    while block := response.read(4 * 1024 * 1024):
                        written += len(block)
                        if written > record["bytes"]:
                            raise ValueError(f"服务器返回了超出清单大小的资产：{record['path']}")
                        stream.write(block)
                        if time.monotonic() - last_report > 20:
                            print(f"{record['path']}: {written}/{record['bytes']} 字节", flush=True)
                            last_report = time.monotonic()
            verify(partial, record)
            partial.replace(target)
            print(f"已校验：{record['path']}", flush=True)
            return target
        except (OSError, urllib.error.URLError) as exc:
            if attempt == 4:
                raise
            print(f"下载中断，保留断点并重试：{exc}", flush=True)
            time.sleep(2)
    raise RuntimeError("下载未完成")


def assemble(record, root):
    target = checked_path(root, record["path"])
    if target.exists():
        verify(target, record)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = checked_path(root, record["path"] + ".assembling")
    if partial.exists():
        raise ValueError(f"保留了之前的合并文件，请检查后删除：{partial}")
    with partial.open("xb") as output:
        for part in record["parts"]:
            source = checked_path(root, part["path"])
            verify(source, part)
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, output, 4 * 1024 * 1024)
    verify(partial, record)
    partial.replace(target)


def extract_archive(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        # 在写入任何文件前检查整个归档，拒绝链接、设备和路径穿越。
        for member in members:
            checked_path(destination, member.name)
            if not member.isfile() and not member.isdir():
                raise ValueError(f"归档含非普通文件：{member.name}")
        for member in members:
            target = checked_path(destination, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = bundle.extractfile(member)
            if target.exists():
                h = hashlib.sha256()
                for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
                    h.update(block)
                source.close()
                if target.stat().st_size != member.size or digest(target) != h.hexdigest():
                    raise ValueError(f"已有文件与归档不一致，拒绝覆盖：{target}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, 4 * 1024 * 1024)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["evidence", "paper-artifacts", "native"], default="evidence")
    parser.add_argument("--output", type=Path, required=True, help="资产工作区；重复运行可续传")
    parser.add_argument("--verify-only", action="store_true", help="只校验已下载文件，不联网、不解压")
    args = parser.parse_args(argv)
    manifest = json.loads((ROOT / "engineering/release_assets.json").read_text())
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected = [r for r in manifest["downloads"] if args.profile in r["profiles"]]
    try:
        for record in selected:
            if args.verify_only:
                verify(checked_path(root, record["path"]), record)
            else:
                download_file(record, root)
        for record in manifest["assemblies"]:
            if args.profile in record["profiles"]:
                if args.verify_only:
                    verify(checked_path(root, record["path"]), record)
                else:
                    assemble(record, root)
        if not args.verify_only:
            for record in selected:
                if "extract_to" in record:
                    extract_archive(checked_path(root, record["path"]), checked_path(root, record["extract_to"]))
        print(json.dumps({"status": "PASS", "profile": args.profile,
                          "downloads": len(selected), "output": str(root),
                          "scope": "资产字节校验；不代表原生实验运行通过"}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(f"资产准备未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
