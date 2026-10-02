"""保存 STEP3A 相对入口的实际文本 diff、手写反例对照和有限接口证据。"""
import difflib
import hashlib
import json
from pathlib import Path
import zipfile

REPORT = Path(__file__).resolve().parents[1]
ROOT = REPORT.parents[1]
DEV = ROOT/"experiments/driveclarify_three_policy_dev"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")


def main():
    entry = json.loads((REPORT/"evidence/ENTRY.json").read_text())
    current = {str(p.relative_to(DEV)): p for p in DEV.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    changed, patch = [], []
    for name in sorted(set(entry["dev_files"]) | set(current)):
        old = REPORT/"evidence/dev_entry"/name
        new = current.get(name)
        before = old.read_text().splitlines(keepends=True) if old.is_file() else []
        after = new.read_text().splitlines(keepends=True) if new else []
        if before == after:
            continue
        prefix = "experiments/driveclarify_three_policy_dev/"+name
        patch.extend(difflib.unified_diff(before, after, fromfile="a/"+prefix if before else "/dev/null", tofile="b/"+prefix if after else "/dev/null"))
        changed.append({"path": prefix, "before_sha256": sha(old) if old.is_file() else None,
                        "after_sha256": sha(new) if new else None})
    (REPORT/"PROTOTYPE_CHANGES.patch").write_text("".join(patch))
    write(REPORT/"evidence/CHANGE_MANIFEST.json", changed)
    prior_zip = ROOT/"reports/driveclarify_experiment_restructure_step3_20260912/REVIEW_PACKET.zip"
    with zipfile.ZipFile(prior_zip) as z:
        baseline_matches = all(hashlib.sha256(z.read("experiments/driveclarify_three_policy_dev/"+name)).hexdigest()==row["sha256"]
                               for name,row in entry["dev_files"].items())
    assert baseline_matches
    original_tests = {name: sha(current[name]) == row["sha256"] for name,row in entry["dev_files"].items() if name.startswith("tests/")}
    assert all(original_tests.values())
    before_path = next((REPORT/"logs").glob("*probes_before.stdout.txt"))
    after_path = next((REPORT/"logs").glob("*probes_final.stdout.txt"))
    before, after = (json.loads(p.read_text()) for p in (before_path, after_path))
    assert before["total"] == after["total"] == 20
    assert after["contract_met"] == 20
    write(REPORT/"PROBE_COMPARISON.json", {"provenance": before["provenance"],
        "external_review_files": "未收到可访问路径；未声称执行外部 review_probes.py，按用户消息定向重建",
        "before_log": str(before_path.relative_to(REPORT)), "after_log": str(after_path.relative_to(REPORT)),
        "before_contract_met": before["contract_met"], "after_contract_met": after["contract_met"], "total": 20,
        "cases": [{"id": a["id"], "expected": a["expected"], "before": b["actual"], "after": a["actual"],
                   "after_contract_met": a["contract_met"]} for b,a in zip(before["cases"],after["cases"])]})
    source_ranges = {
        "driveclarify_clear_passthrough_v11/supervisor.py": [(91,107)],
        "driveclarify_clear_passthrough_v11/replan.py": [(74,85),(243,263)],
        "driveclarify_clear_passthrough_v11/simlingo_agent.py": [(124,149),(434,459),(462,481),(530,551),(639,677),(988,998)],
        "driveclarify_method_v3/bridge.py": [(251,289),(880,914)],
        "/home/buaa/wrh/simlingo/Bench2Drive/leaderboard/leaderboard/scenarios/route_scenario.py": [(459,477)],
    }
    identities, text = {}, ["# STEP3A 有限原生接口证据\n\n仅按已知接缝读取。未 import/执行这些原生代码，也未据错误码虚构根因。\n"]
    for name, ranges in source_ranges.items():
        path = ROOT/name
        identities[name] = {"sha256": sha(path), "ranges": ranges}
        lines = path.read_text().splitlines()
        text.append("\n## "+name+"\n\nSHA-256：`"+identities[name]["sha256"]+"`\n")
        for begin, end in ranges:
            text.append("\n```python\n"+"\n".join(f"{i}: {lines[i-1]}" for i in range(begin,end+1))+"\n```\n")
    (REPORT/"evidence/NATIVE_INTERFACE_EVIDENCE.md").write_text("".join(text))
    write(REPORT/"evidence/NATIVE_SOURCE_IDENTITIES.json", identities)
    write(REPORT/"evidence/BASELINE_VERIFICATION.json", {"entry_dev_matches_original_uploaded_packet": baseline_matches,
        "old_test_files_unchanged": original_tests, "prior_packet_sha256": sha(prior_zip),
        "expected_prior_packet_sha256": entry["prior_packet_sha256"], "changed_dev_files": len(changed)})
    print(json.dumps({"changed_dev_files": len(changed), "original_tests_unchanged": True,
                      "before_probes_met": before["contract_met"], "after_probes_met": after["contract_met"],
                      "prep_doc_characters": len((REPORT/"CLEAR_TASK_PROBE_PREP.md").read_text())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
