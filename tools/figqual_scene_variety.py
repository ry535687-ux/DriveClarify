"""按 town/route/指令/决策 汇总所有含干净原始帧的运行，用于挑出场景不重复的面板。"""
import glob
import json
import os

ROOT = "/home/buaa/wrh/DriveClarify"


def main():
    rows = []
    for png in glob.glob(
        os.path.join(ROOT, "artifacts", "**", "E1R1_GROUNDING_RGB_0.png"),
        recursive=True,
    ):
        d = os.path.dirname(png)
        lr = glob.glob(os.path.join(d, "*_LIVE_RECEIPT.json"))
        lc = glob.glob(os.path.join(d, "*_LAUNCH_CONTRACT.json"))
        if not lr:
            continue
        try:
            j = json.load(open(lr[0]))
        except Exception:
            continue
        ep = {}
        if lc:
            try:
                ep = json.load(open(lc[0])).get("episode", {}) or {}
            except Exception:
                pass
        g = j.get("grounding", {}) or {}
        rows.append(
            {
                "dir": os.path.relpath(d, ROOT),
                "town": ep.get("town"),
                "route": ep.get("route_id"),
                "scenario": ep.get("scenario_id"),
                "instr": ep.get("raw_instruction") or j.get("raw_instruction"),
                "decision": j.get("decision_label"),
                "reason": j.get("decision_reason"),
                "amb": j.get("ambiguity_state") or (
                    j.get("decision_window_dashboard") or {}
                ).get("ambiguity_state"),
                "k": g.get("effective_k"),
                "frame": g.get("frame_id"),
            }
        )

    # 按 (town, route, 指令) 归组，看场景多样性
    groups = {}
    for r in rows:
        groups.setdefault((r["town"], r["route"], r["instr"]), []).append(r)

    print(f"含干净帧的运行数: {len(rows)}；不同(town,route,指令)组合: {len(groups)}\n")
    for key in sorted(groups, key=lambda k: str(k)):
        town, route, instr = key
        rs = groups[key]
        decisions = sorted({str(r["decision"]) for r in rs})
        print(f"=== town={town} route={route} instr={instr!r}")
        print(f"    运行数={len(rs)}  决策={decisions}")
        print(f"    例: {rs[0]['dir']}  scenario={rs[0]['scenario']}")
        print()


if __name__ == "__main__":
    main()
