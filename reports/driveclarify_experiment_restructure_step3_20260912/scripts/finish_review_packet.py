"""STEP3 专用完整性核对和小包解压复现；不修改旧源码或执行驾驶。"""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from run_checks import run

REPORT = Path(__file__).resolve().parents[1]
ROOT = REPORT.parents[1]
DEV = ROOT / "experiments/driveclarify_three_policy_dev"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode())
    return result.stdout


def package_members():
    excluded = {"REVIEW_PACKET.zip", "FINAL_RECEIPT.json", "STATE_ENTRY.json"}
    members = [p for directory in (DEV, REPORT) for p in directory.rglob("*")
               if p.is_file() and p.name not in excluded and "__pycache__" not in p.parts]
    members.extend(ROOT / ("driveclarify_rq1_v2/"+name) for name in ("__init__.py", "consequence.py"))
    return sorted(members)


def build_packet():
    with zipfile.ZipFile(REPORT/"REVIEW_PACKET.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for path in package_members():
            z.write(path, str(path.relative_to(ROOT)))


def main():
    entry = json.loads((REPORT/"evidence/ENTRY.json").read_text())
    protected = json.loads((REPORT/"evidence/PROTECTED_INPUTS.json").read_text())
    sources = json.loads((REPORT/"evidence/SOURCE_IDENTITIES.json").read_text())
    for name, row in sources.items():
        protected.setdefault(name, row["sha256"])
    mismatches = [name for name, expected in protected.items() if sha(ROOT/name) != expected]
    assert not mismatches, mismatches
    old_state = json.loads((REPORT/"evidence/STATE_ENTRY.json").read_text())
    current_state = json.loads((ROOT/"STATE.json").read_text())
    unchanged_state = all(current_state.get(k) == v for k, v in old_state.items() if k not in {"current_task", "status", "step"})
    assert unchanged_state
    retained = {}
    for name, before in entry["handoff_baseline"].items():
        if name == "STATE.json":
            continue
        content = (ROOT/name).read_bytes()
        old_bytes = content[-before["bytes"]:] if name in ("CURRENT_HANDOFF.md", "NEXT_AGENT_PROMPT.md") else content[:before["bytes"]]
        retained[name] = hashlib.sha256(old_bytes).hexdigest() == before["sha256"]
    assert all(retained.values())
    status = git("status", "--porcelain=v1", "-unormal")
    diff, staged = git("diff", "--binary"), git("diff", "--cached", "--binary")
    head, branch = git("rev-parse", "HEAD").decode().strip(), git("branch", "--show-current").decode().strip()
    assert head == entry["head"] and branch == entry["branch"]
    assert diff == (REPORT/"evidence/GIT_ENTRY_DIFF.patch").read_bytes()
    assert staged == (REPORT/"evidence/GIT_ENTRY_STAGED.patch").read_bytes()
    prior_status = set((REPORT/"evidence/GIT_ENTRY_STATUS.txt").read_text().splitlines())
    current_status = set(status.decode().splitlines())
    removed_status = sorted(prior_status-current_status)
    assert not removed_status, removed_status
    added_status = sorted(current_status-prior_status)
    assert all("experiments/" in line or "driveclarify_experiment_restructure_step3_20260912" in line for line in added_status), added_status
    (REPORT/"evidence/GIT_EXIT_STATUS.txt").write_bytes(status)
    (REPORT/"evidence/GIT_EXIT_DIFF.patch").write_bytes(diff)
    (REPORT/"evidence/GIT_EXIT_STAGED.patch").write_bytes(staged)
    write(REPORT/"evidence/INTEGRITY_CHECK.json", {
        "time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "head": head, "branch": branch,
        "entry_status_lines": len(prior_status), "exit_status_lines": len(current_status),
        "added_status_lines": added_status, "removed_preexisting_status_lines": removed_status,
        "tracked_diff_identical": True, "staged_diff_identical": True,
        "protected_files_verified": len(protected), "protected_source_mismatches": mismatches,
        "handoff_old_bytes_retained": retained, "state_old_noncurrent_nodes_preserved": unchanged_state,
        "root_handoff_exit_hashes": {name: sha(ROOT/name) for name in entry["handoff_baseline"]},
        "new_code_root": str(DEV.relative_to(ROOT)), "new_report_root": str(REPORT.relative_to(ROOT)),
        "native_driving_runs": 0, "model_forwards": 0, "production_source_changes": 0})
    build_packet()
    with tempfile.TemporaryDirectory(prefix="driveclarify_step3_packet_") as temporary:
        extracted = Path(temporary)
        with zipfile.ZipFile(REPORT/"REVIEW_PACKET.zip") as z:
            assert z.testzip() is None
            z.extractall(extracted)
            tested_members = {name: hashlib.sha256(z.read(name)).hexdigest() for name in z.namelist()
                              if name.startswith(("experiments/", "driveclarify_rq1_v2/"))}
        result = run("packet_unittest", [sys.executable, "-B", "-m", "unittest", "discover", "-s",
                                       "experiments/driveclarify_three_policy_dev/tests", "-v"], cwd=extracted)
        log = (REPORT/result["stderr"]).read_text()
        assert "Ran 28 tests" in log and "\nOK\n" in log
    write(REPORT/"evidence/PACKET_VALIDATION.json", {"extracted_package_tests": 28, "exit_code": 0,
        "command_receipt": result, "tested_code_and_config_sha256": tested_members,
        "scope": "隔离临时目录执行交付代码、配置及原样 CPU 比较器；不运行驾驶"})
    members = package_members()
    write(REPORT/"ARTIFACT_MANIFEST.json", {"scope": "本轮小包成员；不包含本清单自身摘要或 zip 的自引用摘要",
        "members": {str(p.relative_to(ROOT)): {"sha256": sha(p), "bytes": p.stat().st_size}
                    for p in members if p.name != "ARTIFACT_MANIFEST.json"}})
    build_packet()
    with zipfile.ZipFile(REPORT/"REVIEW_PACKET.zip") as z:
        assert z.testzip() is None
        for name, expected in tested_members.items():
            assert hashlib.sha256(z.read(name)).hexdigest() == expected
        for name, row in json.loads((REPORT/"ARTIFACT_MANIFEST.json").read_text())["members"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == row["sha256"]
        count = len(z.namelist())
    receipt = {"status": "STEP3_PARTIAL_WITH_CONCRETE_INTERFACE_GAP", "packet_sha256": sha(REPORT/"REVIEW_PACKET.zip"),
        "packet_bytes": (REPORT/"REVIEW_PACKET.zip").stat().st_size, "packet_members": count,
        "cpu_tests_passed": 28, "extracted_packet_tests_passed": 28, "final_packet_code_matches_tested_members": True,
        "final_zip_crc_and_manifest_verified": True, "head": head, "branch": branch,
        "native_runs": 0, "ready_real_configurations": 0, "next_action": "独立复核包及三个具体接口缺口；停止，不自动驾驶"}
    write(REPORT/"FINAL_RECEIPT.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
