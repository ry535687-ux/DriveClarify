"""第二轮探测：坐标系声明、ego pose、相机标定、时间余量字段。

决定 (a)(b)(c)(e) 能否把计划投影到 RGB。只读。
"""
import glob
import json
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


def brief(v, n=300):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"...<len={len(s)}>"


def find_keys(obj, needles, path="", out=None, depth=0):
    """递归找出键名含有 needles 任一子串的所有路径。"""
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if any(n in k.lower() for n in needles):
                out.append((p, v))
            find_keys(v, needles, p, out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:4]):
            find_keys(v, needles, f"{path}[{i}]", out, depth + 1)
    return out


def main():
    d = os.path.join(SUITE, CASES["c"])
    j = json.load(open(glob.glob(
        os.path.join(d, "*LIVE_RECEIPT.json"))[0]))

    print("### (c) 计划坐标系声明")
    for i, r in enumerate(j["candidate_plan_repetitions"]):
        print(f"  rep[{i}] candidate_id={r['candidate_id']}"
              f" interp={r['interpretation_id']}")
        print(f"       plan_frame = {r['plan_frame']!r}")
        print(f"       plan_dimension_order = {r['plan_dimension_order']!r}")
        print(f"       source_frame_id = {r['source_frame_id']}")
        print(f"       visualization_source = {r['visualization_source']!r}")
        print(f"       route[:4] = {brief(r['model_predicted_local_route'][:4])}")
        print(f"       route[-2:] = {brief(r['model_predicted_local_route'][-2:])}")
        print(f"       speed_wp[:3] = {brief(r['model_predicted_speed_waypoints'][:3])}")

    print("\n### (c) fresh_replan")
    fr = j["fresh_replan"]
    for k in sorted(fr):
        if k in ("route", "speed"):
            print(f"  {k}[:4] = {brief(fr[k][:4])}")
        else:
            print(f"  {k} = {brief(fr[k], 200)}")
    print(f"  fresh_replan_source_frame_id = {j.get('fresh_replan_source_frame_id')}")
    print(f"  fresh_replan_source_observation_id = "
          f"{j.get('fresh_replan_source_observation_id')}")

    print("\n### (c) target_branch / maneuver_opportunities")
    print(f"  target_branch = {brief(j['target_branch'], 600)}")
    for i, m in enumerate(j["maneuver_opportunities"]):
        print(f"  opp[{i}] = {brief(m, 400)}")

    print("\n### (c) topology_context")
    tc = j["topology_context"]
    print(f"  source = {tc.get('source')!r}")
    print(f"  extra_map_query_for_visualization_count = "
          f"{tc.get('extra_map_query_for_visualization_count')}")
    rp = tc.get("route_polyline_world") or []
    print(f"  route_polyline_world: {len(rp)} 点, 首 {brief(rp[:3])}")

    print("\n### (c) target_binding_receipts 里的任务位置")
    for i, t in enumerate(j["target_binding_receipts"]):
        print(f"  tbr[{i}] candidate={t['candidate_id']} "
              f"interp={t['interpretation_id']}")
        for k in ("referent_phrase", "referring_expression",
                  "relative_image_location", "bbox_xyxy", "track_id",
                  "branch_id", "branch_anchor_xy", "junction_id",
                  "junction_route_anchor_xy", "maneuver",
                  "maneuver_direction", "ordering", "distance_or_progress",
                  "route_order_index", "route_opportunity_index",
                  "route_reachable", "availability", "target_landmark",
                  "target_id", "detector_confidence", "current_behavior",
                  "event_relation", "target_binding_source"):
            print(f"       {k} = {brief(t.get(k, '<缺失>'), 150)}")

    print("\n### 全 receipt 搜 ego pose / 标定 / 内参")
    hits = find_keys(j, ["ego_pose", "ego_transform", "ego_location",
                         "ego_x", "ego_yaw", "intrinsic", "extrinsic",
                         "camera_matrix", "fov", "camera_config",
                         "sensor", "calib", "camera_transform"])
    if not hits:
        print("  无")
    for p, v in hits[:30]:
        print(f"  {p} = {brief(v, 200)}")

    print("\n### 全 receipt 搜 时间 / 余量 字段")
    hits = find_keys(j, ["margin", "deadline", "remaining", "time_to",
                         "simulation_time", "timestamp", "elapsed",
                         "intervention"])
    seen = set()
    for p, v in hits[:60]:
        if p in seen:
            continue
        seen.add(p)
        print(f"  {p} = {brief(v, 160)}")


if __name__ == "__main__":
    main()
