"""本轮专用归档与解包验证；原 STEP3 目录仅校验摘要。"""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from run_logged import run

REPORT = Path(__file__).resolve().parents[1]
ROOT = REPORT.parents[1]
DEV = ROOT/"experiments/driveclarify_three_policy_dev"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True)
    return result.stdout


def members():
    skip = {"REVIEW_PACKET.zip", "FINAL_RECEIPT.json", "STATE_ENTRY.json"}
    paths = [p for directory in (DEV, REPORT) for p in directory.rglob("*")
             if p.is_file() and p.name not in skip and "__pycache__" not in p.parts]
    paths += [ROOT/"driveclarify_rq1_v2"/name for name in ("__init__.py", "consequence.py")]
    return sorted(paths)


def build():
    with zipfile.ZipFile(REPORT/"REVIEW_PACKET.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in members():
            archive.write(path, str(path.relative_to(ROOT)))


def main():
    entry = json.loads((REPORT/"evidence/ENTRY.json").read_text())
    protected = json.loads((REPORT/"evidence/PROTECTED_INPUTS.json").read_text())
    for name, row in json.loads((REPORT/"evidence/NATIVE_SOURCE_IDENTITIES.json").read_text()).items():
        protected.setdefault(name, row["sha256"])
    mismatches = [name for name, expected in protected.items() if sha(ROOT/name) != expected]
    assert not mismatches, mismatches
    old = json.loads((REPORT/"evidence/STATE_ENTRY.json").read_text())
    new = json.loads((ROOT/"STATE.json").read_text())
    assert all(new.get(k)==v for k,v in old.items() if k not in {"current_task", "status", "step"})
    preserved = {}
    for name, row in entry["handoff_baseline"].items():
        if name == "STATE.json":
            continue
        data = (ROOT/name).read_bytes()
        prior = data[-row["bytes"]:] if name in ("CURRENT_HANDOFF.md", "NEXT_AGENT_PROMPT.md") else data[:row["bytes"]]
        preserved[name] = hashlib.sha256(prior).hexdigest()==row["sha256"]
    assert all(preserved.values())
    head = git("rev-parse", "HEAD").decode().strip()
    branch = git("branch", "--show-current").decode().strip()
    assert head == entry["head"] and branch == entry["branch"]
    status, diff, staged = git("status", "--porcelain=v1", "-unormal"), git("diff", "--binary"), git("diff", "--cached", "--binary")
    assert diff == (REPORT/"evidence/GIT_ENTRY_DIFF.patch").read_bytes()
    assert staged == (REPORT/"evidence/GIT_ENTRY_STAGED.patch").read_bytes()
    before = set((REPORT/"evidence/GIT_ENTRY_STATUS.txt").read_text().splitlines())
    after = set(status.decode().splitlines())
    assert not before-after
    additions = sorted(after-before)
    assert all("driveclarify_experiment_restructure_step3a_20260912" in line for line in additions), additions
    for name,data in (("GIT_EXIT_STATUS.txt",status),("GIT_EXIT_DIFF.patch",diff),("GIT_EXIT_STAGED.patch",staged)):
        (REPORT/"evidence"/name).write_bytes(data)
    write(REPORT/"evidence/INTEGRITY_CHECK.json", {"time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "head": head, "branch": branch, "tracked_diff_unchanged": True, "staged_diff_unchanged": True,
        "entry_status_lines":len(before), "exit_status_lines":len(after), "new_status_lines":additions,
        "protected_files_verified":len(protected), "protected_mismatches":mismatches,
        "old_step3_archive_and_report_preserved":True, "old_handoff_bytes_retained":preserved,
        "old_state_noncurrent_nodes_preserved":True,
        "handoff_exit_hashes":{name:sha(ROOT/name) for name in entry["handoff_baseline"]},
        "native_probes_executed":0, "native_probes_authorized":False, "production_source_changes":0})
    build()
    with tempfile.TemporaryDirectory(prefix="driveclarify_step3a_packet_") as temporary:
        destination = Path(temporary)
        with zipfile.ZipFile(REPORT/"REVIEW_PACKET.zip") as archive:
            assert archive.testzip() is None
            archive.extractall(destination)
            tested = {name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                      if name.startswith(("experiments/", "driveclarify_rq1_v2/"))}
        receipt = run("packet_tests", [sys.executable,"-B","-m","unittest","discover","-s",
                                     "experiments/driveclarify_three_policy_dev/tests","-v"], cwd=destination)
        output = (REPORT/receipt["stderr"]).read_text()
        assert "Ran 46 tests" in output and "\nOK\n" in output
    write(REPORT/"evidence/PACKET_VALIDATION.json", {"extracted_package_tests":46,"exit_code":0,
        "command_receipt":receipt,"tested_code_config_dependency_sha256":tested,
        "scope":"解压后直接运行交付入口/评价及新旧测试；无模型或车辆服务"})
    write(REPORT/"ARTIFACT_MANIFEST.json", {"scope":"小包成员，不含清单自身或最终回执的自引用摘要",
        "members":{str(p.relative_to(ROOT)):{"sha256":sha(p),"bytes":p.stat().st_size}
                   for p in members() if p.name != "ARTIFACT_MANIFEST.json"}})
    build()
    with zipfile.ZipFile(REPORT/"REVIEW_PACKET.zip") as archive:
        assert archive.testzip() is None
        for name, expected in tested.items():
            assert hashlib.sha256(archive.read(name)).hexdigest()==expected
        manifest=json.loads((REPORT/"ARTIFACT_MANIFEST.json").read_text())
        for name,row in manifest["members"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest()==row["sha256"]
        count=len(archive.namelist())
    final={"status":"STEP3A_CPU_FIXES_COMPLETE_NATIVE_PROBES_NOT_AUTHORIZED", "cpu_tests_passed":46,
        "old_tests_retained":28,"targeted_probes_met":20,"extracted_packet_tests_passed":46,
        "packet_sha256":sha(REPORT/"REVIEW_PACKET.zip"),"packet_bytes":(REPORT/"REVIEW_PACKET.zip").stat().st_size,
        "packet_members":count,"final_code_matches_tested":True,"crc_and_manifest_verified":True,
        "prior_packet_sha256_preserved":entry["prior_packet_sha256"],"native_runs":0,"head":head,
        "next_action":"独立复核本轮修复包和两个明确任务准备；停止，不自动驾驶"}
    write(REPORT/"FINAL_RECEIPT.json",final)
    print(json.dumps(final,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
