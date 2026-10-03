"""Exercise the installation boundaries without downloading a simulator."""
import hashlib
import http.server
import io
import json
from pathlib import Path
import subprocess
import tarfile
import threading

import pytest

from driveclarify.tools import prepare, setup


def archive(path, entries):
    with tarfile.open(path, "w:gz") as stream:
        for name, content in entries:
            member = tarfile.TarInfo(name)
            if isinstance(content, tuple):
                member.type = tarfile.SYMTYPE
                member.linkname = content[0]
                stream.addfile(member)
            else:
                member.size = len(content)
                stream.addfile(member, io.BytesIO(content))
    return path


@pytest.mark.parametrize("entries", [[("../outside", b"bad")], [("/outside", b"bad")],
                                    [("link", ("../../outside",))]])
def test_archive_escape_is_rejected(tmp_path, entries):
    source = archive(tmp_path / "archive.tar.gz", entries)
    with pytest.raises(ValueError, match="越界|绝对"):
        setup.extract(source, tmp_path / "root")
    assert not (tmp_path / "outside").exists()


def test_archive_preserves_safe_relative_links(tmp_path):
    source = archive(tmp_path / "archive.tar.gz", [("data/value", b"ok"), ("link", ("data/value",))])
    setup.extract(source, tmp_path / "root")
    assert (tmp_path / "root/link").read_bytes() == b"ok"


def test_truncated_gzip_trailer_is_rejected(tmp_path):
    source = archive(tmp_path / "archive.tar.gz", [("value", b"ok")])
    source.write_bytes(source.read_bytes()[:-4])
    with pytest.raises((EOFError, OSError)):
        setup.extract(source, tmp_path / "root")


def test_carla_install_checks_maps_and_reuses_completed_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "required_towns", lambda: ["Town12"])
    main = archive(tmp_path / "main.tgz", [("CarlaUE4.sh", b"#!/bin/sh\n"),
                    ("PythonAPI/carla/dist/carla-0.9.15-test.egg", b"client")])
    maps = archive(tmp_path / "maps.tgz", [("CarlaUE4/Content/Carla/Maps/Town12/Town12.umap", b"map")])
    records = [{"path": "main.tgz"}, {"path": "maps.tgz"}]
    calls = []
    monkeypatch.setattr(setup, "download_runtime", lambda record, cache: calls.append(record["path"]) or
                        {"main.tgz": main, "maps.tgz": maps}[record["path"]])
    root = tmp_path / "carla"
    setup.install_carla(root, tmp_path, records)
    setup.install_carla(root, tmp_path, records)
    assert calls == ["main.tgz", "maps.tgz"]
    (root / "CarlaUE4/Content/Carla/Maps/Town12/Town12.umap").unlink()
    with pytest.raises(ValueError, match="地图缺失"):
        setup.install_carla(root, tmp_path, records)


def test_existing_carla_directory_is_preserved(tmp_path):
    (tmp_path / "user-file").write_bytes(b"keep")
    with pytest.raises(ValueError, match="拒绝修改"):
        setup.install_carla(tmp_path, tmp_path, [])
    assert (tmp_path / "user-file").read_bytes() == b"keep"


@pytest.fixture
def runtime_server():
    payload = b"simulator-archive-fixture"
    state = {"etag": '"fixed-object"', "ranges": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("ETag", state["etag"])
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()

        def do_GET(self):
            offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
            state["ranges"].append(offset)
            self.send_response(206 if offset else 200)
            self.send_header("ETag", state["etag"])
            self.send_header("Content-Length", str(len(payload) - offset))
            if offset:
                self.send_header("Content-Range", f"bytes {offset}-{len(payload)-1}/{len(payload)}")
            self.end_headers()
            self.wfile.write(payload[offset:])

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    record = {"path": "runtime.tar.gz", "bytes": len(payload), "etag": state["etag"],
              "url": f"http://127.0.0.1:{server.server_port}/runtime"}
    try:
        yield record, payload, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_runtime_download_resumes_and_audits_local_bytes(tmp_path, runtime_server):
    record, payload, state = runtime_server
    (tmp_path / "runtime.tar.gz.partial").write_bytes(payload[:7])
    target = setup.download_runtime(record, tmp_path)
    assert target.read_bytes() == payload
    assert state["ranges"] == [7]
    setup.download_runtime(record, tmp_path)
    assert state["ranges"] == [7]
    target.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="缓存校验失败"):
        setup.download_runtime(record, tmp_path)


def test_changed_upstream_runtime_is_rejected(tmp_path, runtime_server):
    record, payload, state = runtime_server
    state["etag"] = '"different-object"'
    with pytest.raises(ValueError, match="已变化"):
        setup.download_runtime(record, tmp_path)
    assert not state["ranges"]


def test_environment_install_resumes_pip_and_reuses_receipt(tmp_path, monkeypatch):
    prefix = tmp_path / "env"
    (prefix / "conda-meta").mkdir(parents=True)
    (prefix / "conda-meta/history").write_text("created")
    locks = {name: setup.assets.digest(setup.RESOURCES / name) for name in
             ("native-requirements.txt", "conda-linux64.explicit.txt")}
    setup.save(tmp_path / "env.installing.json", locks)
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/fake/conda")
    calls = []
    monkeypatch.setattr(setup, "execute", lambda command, **kw: calls.append(list(map(str, command))))
    monkeypatch.setattr(setup.subprocess, "run", lambda command, **kw: calls.append(command))
    setup.install_environment(prefix, tmp_path, {})
    assert not any("create" in c for c in calls)
    assert any("deepspeed==0.16.2" in c for c in calls)
    assert setup.benchmark.read(prefix / "DRIVECLARIFY_ENV.json")["locks"] == locks
    calls.clear()
    setup.install_environment(prefix, tmp_path, {})
    assert len(calls) == 1 and calls[0][-1] == "check"


def test_existing_unowned_environment_is_not_overwritten(tmp_path, monkeypatch):
    prefix = tmp_path / "env"
    prefix.mkdir()
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/fake/conda")
    with pytest.raises(ValueError, match="拒绝修改"):
        setup.install_environment(prefix, tmp_path, {})


def test_frozen_routes_require_all_twelve_towns():
    assert setup.required_towns() == ["Town01", "Town02", "Town03", "Town04", "Town05", "Town06",
                                     "Town07", "Town10HD", "Town11", "Town12", "Town13", "Town15"]


def test_packaged_native_dependency_pins_are_parseable():
    from packaging.requirements import Requirement
    pins = [Requirement(line) for line in (setup.RESOURCES / "native-requirements.txt").read_text().splitlines()
            if line and not line.startswith("#")]
    assert all(len(list(p.specifier)) == 1 and next(iter(p.specifier)).operator == "==" for p in pins)
    by_name = {p.name: str(p.specifier) for p in pins}
    assert by_name["carla"] == "==0.9.15"
    assert by_name["torch"] == "==2.2.0"
    assert by_name["scipy"] == "==1.10.1"


def test_native_resume_requires_an_existing_plan(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(setup, "host_check", lambda *args: calls.append(args))
    assert setup.main(["--resume", "--root", str(tmp_path)]) == 1
    assert calls == []


def test_native_dry_run_reuses_paths_without_installing(tmp_path, monkeypatch):
    paths_file = tmp_path / "paths.json"
    setup.save(paths_file, {"native_python": "/fake/python", "carla_root": "/fake/carla"})
    calls = []
    monkeypatch.setattr(setup, "host_check", lambda *args: None)
    monkeypatch.setattr(setup, "execute", lambda *args: None)
    monkeypatch.setattr(setup, "verify_carla", lambda *args: None)
    monkeypatch.setattr(setup.benchmark, "preflight", lambda *args: {"pair_order": []})
    monkeypatch.setattr(setup, "install_environment", lambda *args: pytest.fail("must not install"))
    monkeypatch.setattr(setup.benchmark, "prepare_plan", lambda *args, **kw: calls.append(("plan", kw)))
    monkeypatch.setattr(setup.benchmark, "run_plan", lambda *args, **kw: calls.append(("run", kw)))
    monkeypatch.setattr(setup.benchmark, "collect", lambda *args, **kw: pytest.fail("dry run must not collect"))
    assert setup.main(["--root", str(tmp_path), "--paths", str(paths_file), "--all", "--dry-run"]) == 0
    assert calls[0][1] == {"all_routes": True, "offscreen": True, "gpu": 0}
    assert calls[1][1] == {"resume": False, "attempt": 1, "repair_note": None, "dry_run": True}


def test_source_restore_is_idempotent_and_detects_changes(tmp_path, monkeypatch):
    source = tmp_path / "upstream"
    source.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], stderr=subprocess.DEVNULL).decode().strip()
    git("init")
    (source / "model.py").write_bytes(b"original\n")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    commit = git("rev-parse", "HEAD")
    (source / "model.py").write_bytes(b"patched\n")
    patch = (git("diff") + "\n").encode()
    (source / "model.py").write_bytes(b"original\n")
    resources = tmp_path / "resources"
    resources.mkdir()
    (resources / "simlingo.patch").write_bytes(patch)
    model_hash = hashlib.sha256(b"patched\n").hexdigest()
    setup.save(resources / "native.json", {"simlingo_base_commit": commit,
        "patch_sha256": hashlib.sha256(patch).hexdigest(), "extra_files": [],
        "simlingo_files": [{"path": "model.py", "sha256": model_hash}],
        "frozen_upstream_files": {"model.py": model_hash}})
    xml = '<routes><route town="Town12"/></routes>'
    setup.save(resources / "routes.json", {"pair_order": [{"xml": xml, "route_path": "route.xml",
        "route_sha256": hashlib.sha256(xml.encode()).hexdigest()}]})
    monkeypatch.setattr(prepare, "RESOURCES", resources)
    workspace = tmp_path / "workspace"
    argv = ["--workspace", str(workspace), "--upstream-source", str(source), "--carla-root", str(tmp_path / "carla"),
            "--native-python", str(tmp_path / "env/bin/python")]
    prepare.main(argv)
    original = (workspace / "paths.local.json").read_bytes()
    prepare.main(argv)
    assert (workspace / "paths.local.json").read_bytes() == original
    assert (source / "model.py").read_bytes() == b"original\n"
    (workspace / "simlingo/model.py").write_bytes(b"changed\n")
    with pytest.raises(ValueError, match="摘要不符"):
        prepare.main(argv)
