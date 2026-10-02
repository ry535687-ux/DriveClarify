"""第三轮探测：ego pose / 执行轨迹 / (a) 共享任务 / (e) 时间余量 / (d) 真实语义。

只读。
"""
import glob
import json
import os

ROOT = "/home/buaa/wrh/DriveClarify"
SUITE = os.path.join(
    ROOT, "artifacts",
    "driveclarify_method_v1_visual_behavioral_acceptance_suite_v1")
T4 = os.path.join(ROOT, "artifacts", "temporal_grounding_v1",
                  "T4_train_bounded_closed_loop")
CASES = {
    "a": "DCVA1-02-AS-0815",
    "b": "DCVA1-03-ASK-0815",
    "c": "DCVA1-04-WAIT-0815",
    "e": "DCVA1-06-TL-0815",
    "f": "DCVA1-01-ACT-0815",
}


def brief(v, n=220):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"...<len={len(s)}>"


def rec(letter):
    d = os.path.join(SUITE, CASES[letter])
    return json.load(open(glob.glob(
        os.path.join(d, "*LIVE_RECEIPT.json"))[0]))


def main():
    print("### post_hoc_world_state.jsonl 结构 (c)")
    p = os.path.join(SUITE, CASES["c"], "post_hoc_world_state.jsonl")
    with open(p) as fh:
        lines = fh.readlines()
    print(f"  行数 = {len(lines)}")
    first = json.loads(lines[0])
    print(f"  首行键 = {sorted(first)}")
    print(f"  首行 = {brief(first, 700)}")
    last = json.loads(lines[-1])
    print(f"  末行 = {brief(last, 400)}")

    print("\n### (a) 两个候选是否共享同一任务位置")
    a = rec("a")
    print(f"  raw_instruction = {a['raw_instruction']!r}")
    print(f"  branch_duplicate = {a.get('branch_duplicate')} "
          f"target_duplicate = {a.get('target_duplicate')}")
    for i, t in enumerate(a["target_binding_receipts"]):
        print(f"  tbr[{i}] referring={t['referring_expression']!r}")
        for k in ("branch_id", "branch_anchor_xy", "junction_id",
                  "junction_route_anchor_xy", "distance_or_progress",
                  "route_order_index", "maneuver", "maneuver_direction",
                  "current_behavior", "ordering", "target_id",
                  "detector_confidence", "bbox_xyxy"):
            print(f"       {k} = {brief(t.get(k, '<缺失>'), 130)}")
    for i, r in enumerate(a["candidate_plan_repetitions"]):
        rt = r["model_predicted_local_route"]
        print(f"  plan[{i}] frame={r['plan_frame']} src={r['source_frame_id']}"
              f" 终点={brief(rt[-1])} 最大|lateral|="
              f"{max(abs(x[1]) for x in rt):.3f}")
    print(f"  shared_act.status = {a['shared_act'].get('status')}")
    print(f"  target_branch = {brief(a['target_branch'], 400)}")

    print("\n### (e) 时间余量与是否真的执行了 FALLBACK")
    e = rec("e")
    print(f"  decision_label={e['decision_label']} reason={e['decision_reason']}")
    print(f"  decision_why = {brief(e.get('decision_why'), 300)}")
    dw = e["decision_window_dashboard"]
    for k in sorted(dw):
        print(f"  dashboard.{k} = {brief(dw[k], 200)}")
    for k in ("evidence_state", "semantic_state", "authority_subject",
              "baseline_ownership_restored", "carla_tick_count",
              "control_source", "status", "closed",
              "direct_vehicle_control_write_count",
              "candidate_direct_vehicle_control_write_count",
              "manual_override_count", "forced_decision_count"):
        print(f"  {k} = {brief(e.get(k, '<缺失>'), 200)}")
    for i, t in enumerate(e["target_binding_receipts"]):
        print(f"  tbr[{i}] {t['referring_expression']!r} "
              f"dist={t['distance_or_progress']} "
              f"junction={t['junction_id']} "
              f"order={t['route_order_index']}")

    print("\n### (f) 无歧义与无询问的时间跨度证据")
    f = rec("f")
    for k in ("effective_k", "raw_k", "decision_label", "decision_reason",
              "authority_subject", "carla_tick_count",
              "control_observation_count", "normal_simlingo_forward_count",
              "candidate_simlingo_forward_count", "closed", "status",
              "persistent_decision_history", "current_persistent_decision"):
        v = f.get(k, "<缺失>")
        if isinstance(v, list):
            print(f"  {k}: 长度 {len(v)}")
        else:
            print(f"  {k} = {brief(v, 200)}")

    print("\n### (d) 时序 receipt 真实语义")
    t4 = json.load(open(os.path.join(
        T4, "TEMPORAL_GROUNDING_V1_LIVE_RECEIPT.json")))
    print(f"  顶层键 = {sorted(t4)}")
    for k in ("raw_instruction", "decision_label", "decision_reason",
              "ambiguity_type", "evidence_state", "status",
              "historical_rgb_or_plan_authority_reuse"):
        print(f"  {k} = {brief(t4.get(k, '<缺失>'), 220)}")


if __name__ == "__main__":
    main()
