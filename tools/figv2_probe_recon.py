"""第五轮：可用 RGB 帧、各案例 world_state、(b) 任务位置、route progress 方法验证。

route progress 验证思路：用 (e) 自己的 route_polyline_world + ego 世界坐标算弧长，
与 (e) dashboard 记录的 ego_route_progress = 23.8735 比较。若吻合，该方法可用于把
不同帧的 ego-local 计划换算到同一 route 坐标系。只读。
"""
import glob
import json
import math
import os

ROOT = "/home/buaa/wrh/DriveClarify"
SUITE = os.path.join(
    ROOT, "artifacts",
    "driveclarify_method_v1_visual_behavioral_acceptance_suite_v1")
CASES = {
    "a": "DCVA1-02-AS-0815",
    "b": "DCVA1-03-ASK-0815",
    "c": "DCVA1-04-WAIT-0815",
    "e": "DCVA1-06-TL-0815",
    "f": "DCVA1-01-ACT-0815",
}


def brief(v, n=200):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"...<len={len(s)}>"


def arc_lengths(poly):
    out = [0.0]
    for i in range(1, len(poly)):
        dx = poly[i][0] - poly[i - 1][0]
        dy = poly[i][1] - poly[i - 1][1]
        out.append(out[-1] + math.hypot(dx, dy))
    return out


def project_progress(poly, s, pt):
    """把世界点投影到折线上，返回弧长（对每段做线段投影，取最近）。"""
    best = (float("inf"), 0.0)
    for i in range(len(poly) - 1):
        ax, ay = poly[i]
        bx, by = poly[i + 1]
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy
        if L2 <= 0:
            continue
        t = ((pt[0] - ax) * vx + (pt[1] - ay) * vy) / L2
        t = max(0.0, min(1.0, t))
        px, py = ax + t * vx, ay + t * vy
        d = math.hypot(pt[0] - px, pt[1] - py)
        if d < best[0]:
            best = (d, s[i] + t * math.sqrt(L2))
    return best  # (横向偏差 m, 弧长 m)


def main():
    print("### 各案例可用 PNG 与 world_state")
    for letter, name in CASES.items():
        d = os.path.join(SUITE, name)
        pngs = sorted(glob.glob(os.path.join(d, "**", "*.png"),
                                recursive=True))
        ws = os.path.join(d, "post_hoc_world_state.jsonl")
        print(f"\n  ({letter}) {name}")
        print(f"      world_state 存在 = {os.path.exists(ws)}")
        for p in pngs:
            print(f"      {os.path.relpath(p, d)}  "
                  f"{os.path.getsize(p)//1024} KiB")
        for sub in ("probe", "visual_timeline", "simlingo_save"):
            sd = os.path.join(d, sub)
            if os.path.isdir(sd):
                items = sorted(os.listdir(sd))
                print(f"      {sub}/ -> {items[:8]}"
                      f"{' ...' if len(items) > 8 else ''}")

    print("\n\n### (b) 任务位置数字")
    b = json.load(open(glob.glob(os.path.join(
        SUITE, CASES["b"], "*LIVE_RECEIPT.json"))[0]))
    for i, t in enumerate(b["target_binding_receipts"]):
        print(f"  tbr[{i}] {t['referring_expression']!r}")
        for k in ("bbox_xyxy", "branch_id", "branch_anchor_xy", "junction_id",
                  "junction_route_anchor_xy", "distance_or_progress",
                  "route_order_index", "maneuver_direction",
                  "current_behavior", "ordering", "detector_confidence"):
            print(f"       {k} = {brief(t.get(k), 120)}")
    for i, r in enumerate(b["candidate_plan_repetitions"]):
        rt = r["model_predicted_local_route"]
        print(f"  plan[{i}] src_frame={r['source_frame_id']} "
              f"终点={brief(rt[-1])} 最大|lat|={max(abs(x[1]) for x in rt):.3f}")
    fr = b.get("fresh_replan") or {}
    if isinstance(fr, dict) and fr.get("route"):
        rt = fr["route"]
        print(f"  fresh_replan 终点={brief(rt[-1])} "
              f"最大|lat|={max(abs(x[1]) for x in rt):.3f}")
    print(f"  fresh_replan_source_frame_id = "
          f"{b.get('fresh_replan_source_frame_id')}")
    print(f"  answer_received_frame = {b.get('answer_received_frame')}")
    print(f"  answer_delay_simulation_seconds = "
          f"{b.get('answer_delay_simulation_seconds')}")
    dwb = b.get("decision_window_dashboard") or {}
    for k in ("source_frame", "ego_route_progress", "decision_point",
              "maneuver_onset", "commitment_boundary",
              "latest_safe_clarification", "time_to_divergence",
              "ClarificationState", "CurrentActionRelation",
              "FutureObligationRelation", "Recoverability"):
        print(f"  dashboard.{k} = {brief(dwb.get(k), 150)}")

    print("\n\n### route progress 方法验证（用 (e) 的 dashboard 值对照）")
    e = json.load(open(glob.glob(os.path.join(
        SUITE, CASES["e"], "*LIVE_RECEIPT.json"))[0]))
    poly = e["topology_context"]["route_polyline_world"]
    s = arc_lengths(poly)
    print(f"  (e) polyline 点数={len(poly)} 总弧长={s[-1]:.3f} m")
    print(f"  dashboard.ego_route_progress = "
          f"{e['decision_window_dashboard']['ego_route_progress']}")
    print(f"  dashboard.source_frame = "
          f"{e['decision_window_dashboard']['source_frame']}")
    ws = os.path.join(SUITE, CASES["e"], "post_hoc_world_state.jsonl")
    if os.path.exists(ws):
        rows = [json.loads(x) for x in open(ws)]
        print(f"  world_state 行数={len(rows)} "
              f"帧 {rows[0].get('gametime_frame')}..{rows[-1].get('gametime_frame')}")
        target = e["decision_window_dashboard"]["source_frame"]
        for r in rows:
            if r.get("gametime_frame") == target:
                loc = r["ego"]["location_xyz"]
                dev, prog = project_progress(poly, s, (loc[0], loc[1]))
                print(f"  f{target} ego=({loc[0]:.3f},{loc[1]:.3f}) "
                      f"-> 我算的 progress={prog:.3f} m, 横向偏差={dev:.3f} m")
                print(f"  差值 = {prog - e['decision_window_dashboard']['ego_route_progress']:+.4f} m")
                break
        else:
            print(f"  world_state 里没有 f{target}")
            print(f"  可用帧: {[r.get('gametime_frame') for r in rows][:12]} ...")
    # (e) 的候选承诺边界与任务位置
    for i, t in enumerate(e["target_binding_receipts"]):
        print(f"  (e) tbr[{i}] dist={t['distance_or_progress']} "
              f"anchor={t['branch_anchor_xy']} "
              f"junction_anchor={brief(t.get('junction_route_anchor_xy'), 60)}")


if __name__ == "__main__":
    main()
