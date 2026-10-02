"""保存本轮用到的短源码证据与只读来源身份。"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REPORT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    excerpts = {
        "driveclarify_rq1_v2/consequence.py": [(51, 81), (106, 168)],
        "driveclarify_clear_passthrough_v11/supervisor.py": [(131, 176), (198, 243)],
        "driveclarify_clear_passthrough_v11/replan.py": [(74, 85), (145, 154), (243, 273)],
        "driveclarify_clear_passthrough_v11/simlingo_agent.py": [(462, 491), (530, 551)],
        "/home/buaa/wrh/simlingo/Bench2Drive/leaderboard/leaderboard/scenarios/route_scenario.py": [(459, 477)],
        "/home/buaa/wrh/simlingo/Bench2Drive/scenario_runner/srunner/scenariomanager/scenarioatomics/atomic_criteria.py": [(1403, 1426), (1454, 1510)],
    }
    identities = {}
    text = ["# STEP3 短源码证据\n\n只读摘录用于定位接口；本轮 CPU 测试实际导入交付入口与 live 纯 CPU 比较器。\n"]
    for name, ranges in excerpts.items():
        path = ROOT/name
        identities[name] = {"sha256": sha(path), "bytes": path.stat().st_size, "ranges": ranges}
        lines = path.read_text().splitlines()
        text.append("\n## "+name+"\n\nSHA-256：`"+identities[name]["sha256"]+"`\n")
        for begin, end in ranges:
            text.append("\n```python\n"+"\n".join(f"{i}: {lines[i-1]}" for i in range(begin, min(end,len(lines))+1))+"\n```\n")
    cases = json.loads((REPORT/"DEV_CASES.json").read_text())
    for name in cases["source_assets"]:
        path = ROOT/name
        identities[name] = {"sha256": sha(path), "bytes": path.stat().st_size}
    word = Path("/home/buaa/下载/论文20260909改2.docx")
    identities[str(word)] = {"sha256": sha(word), "bytes": word.stat().st_size}
    assert identities[str(word)]["sha256"] == "d8bd6e9d4ef6ee11e086b637b2e3d061c0760699ed214dd0149afdd46abe299e"
    preinstall = json.loads((ROOT/cases["source_assets"][-1]).read_text())
    evidence = {k: preinstall[k] for k in ("scene", "cycle_1_native", "cycle_2_native", "transaction_result", "command_owner_resolved", "evidence_boundary")}
    (REPORT/"evidence/NATIVE_GAP_EXCERPT.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n")
    (REPORT/"evidence/SOURCE_EXCERPTS.md").write_text("".join(text))
    (REPORT/"evidence/SOURCE_IDENTITIES.json").write_text(json.dumps(identities, ensure_ascii=False, indent=2)+"\n")
    # 修正仅本轮新增清单/生成脚本中的接口类名，不触碰旧源文件。
    for path in (REPORT/"DEV_CASES.json", REPORT/"scripts/prepare_assets.py"):
        content = path.read_text()
        path.write_text(content.replace("ClarificationSupervisor.process", "ClearPassThroughSafeReplanSupervisor.process"))
    print(json.dumps({"source_files_hashed": len(identities), "word_hash_matches_step2": True,
                      "source_mutations": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
