"""探测六面板 v2 所需证据：候选计划、回答后计划、任务位置、时间余量、标定。

只读。不改任何 artifacts。
"""
import glob
import json
import os

ROOT = "/home/buaa/wrh/DriveClarify"
SUITE = os.path.join(
    ROOT, "artifacts",
    "driveclarify_method_v1_visual_behavioral_acceptance_suite_v1")

CASES = {
    "a": os.path.join(SUITE, "DCVA1-02-AS-0815"),
    "b": os.path.join(SUITE, "DCVA1-03-ASK-0815"),
    "c": os.path.join(SUITE, "DCVA1-04-WAIT-0815"),
    "e": os.path.join(SUITE, "DCVA1-06-TL-0815"),
    "f": os.path.join(SUITE, "DCVA1-01-ACT-0815"),
}


def brief(v, n=220):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"...<len={len(s)}>"


def walk_numeric_arrays(obj, path="", hits=None, depth=0):
    """找出所有看起来像轨迹的数值数组（>=3 个数对）。"""
    if hits is None:
        hits = []
    if depth > 7:
        return hits
    if isinstance(obj, list):
        if len(obj) >= 3 and all(
            isinstance(x, (list, tuple)) and len(x) in (2, 3)
            and all(isinstance(v, (int, float)) for v in x) for x in obj
        ):
            hits.append((path, f"{len(obj)}x{len(obj[0])} 数对"))
            return hits
        if len(obj) >= 4 and all(isinstance(x, (int, float)) for x in obj):
            hits.append((path, f"{len(obj)} 个标量"))
            return hits
        for i, x in enumerate(obj[:6]):
            walk_numeric_arrays(x, f"{path}[{i}]", hits, depth + 1)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            walk_numeric_arrays(v, f"{path}.{k}" if path else k, hits,
                                depth + 1)
    return hits


def main():
    for letter, d in CASES.items():
        lr = sorted(glob.glob(os.path.join(d, "*LIVE_RECEIPT.json")))
        if not lr:
            print(f"({letter}) 无 live receipt")
            continue
        j = json.load(open(lr[0]))
        print(f"\n{'='*66}\n({letter}) {os.path.basename(d)}")
        print(f"  raw_instruction = {j.get('raw_instruction')!r}")
        print(f"  question = {brief(j.get('question'))}")
        print(f"  answer = {brief(j.get('answer'))}")
        print(f"  decision_label={j.get('decision_label')} "
              f"initial={j.get('initial_decision')} "
              f"post_answer={j.get('post_answer_decision')} "
              f"reason={j.get('decision_reason')}")

        for key in ("candidate_plan_repetitions", "fresh_replan",
                    "target_branch", "topology_context",
                    "maneuver_opportunities", "shared_act", "limited_act",
                    "target_binding_receipts", "parsed_slots"):
            v = j.get(key, "<缺失>")
            print(f"  --- {key}: {type(v).__name__}")
            if isinstance(v, dict):
                print(f"      键: {sorted(v)}")
            elif isinstance(v, list) and v:
                print(f"      长度 {len(v)}; 首元素类型 {type(v[0]).__name__}")
                if isinstance(v[0], dict):
                    print(f"      首元素键: {sorted(v[0])}")
            else:
                print(f"      {brief(v)}")

        print("  --- 疑似轨迹数值数组（全 receipt 扫描）:")
        hits = walk_numeric_arrays(j)
        if not hits:
            print("      无")
        for path, desc in hits[:40]:
            print(f"      {path}  ->  {desc}")


if __name__ == "__main__":
    main()
