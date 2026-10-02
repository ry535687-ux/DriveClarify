"""第四轮：post_hoc_world_state 的 camera/ego 是否够做投影；(d) 的真实语义。

只读。这一步决定 (a)(b)(c) 是否允许把计划画到 RGB 上。
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


def brief(v, n=260):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"...<len={len(s)}>"


def main():
    p = os.path.join(SUITE, "DCVA1-04-WAIT-0815", "post_hoc_world_state.jsonl")
    rows = [json.loads(x) for x in open(p)]
    print(f"### (c) post_hoc_world_state: {len(rows)} 行")
    r0 = rows[0]
    for key in ("camera", "ego", "route_context", "map_waypoint",
                "candidate_set", "m2b_decision", "baseline_control",
                "input_sensor_frames"):
        v = r0.get(key, "<缺失>")
        print(f"\n  --- {key} ({type(v).__name__})")
        if isinstance(v, dict):
            for k in sorted(v):
                print(f"      {k} = {brief(v[k], 200)}")
        else:
            print(f"      {brief(v, 400)}")

    print("\n  --- 帧号轴")
    for key in ("gametime_frame", "carla_snapshot_frame", "snapshot_frame",
                "observation_id", "agent_timestamp_seconds",
                "gametime_seconds"):
        print(f"      {key}: 首={brief(r0.get(key))} 末={brief(rows[-1].get(key))}")

    print("\n  --- pred_route_values 是否等于 receipt 的计划")
    pr = r0.get("pred_route_values")
    print(f"      类型 {type(pr).__name__}; {brief(pr, 300)}")

    print("\n  --- 是否存在 frame 2220 那一行（回答到达帧）")
    for r in rows:
        oid = r.get("observation_id") or ""
        if ":2220:" in str(oid) or r.get("gametime_frame") == 2220:
            print(f"      找到: observation_id={oid} "
                  f"gametime_frame={r.get('gametime_frame')}")
            print(f"      ego={brief(r.get('ego'), 300)}")
            break
    else:
        print("      未找到")
        ids = [str(r.get("observation_id")) for r in rows]
        print(f"      observation_id 首/末 = {ids[0]} / {ids[-1]}")

    print("\n\n### (d) T4 时序真实语义")
    t4 = json.load(open(os.path.join(
        T4, "TEMPORAL_GROUNDING_V1_LIVE_RECEIPT.json")))
    for k in ("raw_instruction", "status", "level", "plausibility_decision",
              "pre_event_decision", "post_event_decision",
              "wait_entry_frame", "wait_exit_frame", "wait_exit_reason",
              "wait_stationary_or_moving_reason", "replan_required",
              "fresh_planning_frame", "invalidated_at_frame",
              "old_candidate_invalidated", "reground_count",
              "reacquisition_count", "detector_invocation_count",
              "detector_invocation_frames", "track_loss_count",
              "track_id_switch_count", "control_observation_count",
              "normal_simlingo_forward_count",
              "candidate_simlingo_forward_count", "closed",
              "grounded_referent_id", "track_id",
              "closed_loop_candidate_plan_selection_pending",
              "candidate_identity_preserved_across_state_change"):
        print(f"  {k} = {brief(t4.get(k, '<缺失>'), 240)}")

    for k in ("event_region", "event_region_calibration", "event_timeline",
              "information_update", "initial_grounding", "wait",
              "wait_lease_calibration", "target_binding", "rgb_identities",
              "pre_event_plan", "post_event_plan", "policy_runtime_inputs"):
        v = t4.get(k, "<缺失>")
        print(f"\n  --- {k} ({type(v).__name__})")
        if isinstance(v, dict):
            for kk in sorted(v):
                print(f"      {kk} = {brief(v[kk], 200)}")
        elif isinstance(v, list):
            print(f"      长度 {len(v)}")
            for it in v[:6]:
                print(f"      {brief(it, 240)}")
        else:
            print(f"      {brief(v, 300)}")

    tl = t4.get("track_lifecycle") or []
    print(f"\n  --- track_lifecycle 长度 {len(tl)}")
    for it in tl[:3]:
        print(f"      {brief(it, 300)}")
    print(f"      末: {brief(tl[-1], 300) if tl else '-'}")


if __name__ == "__main__":
    main()
