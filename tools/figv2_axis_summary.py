"""从 EVIDENCE_BUNDLE.json 打印每个面板将要画在证据轴上的数字。只读。"""
import json
import os

BUNDLE = os.path.join(
    "/home/buaa/wrh/DriveClarify", "deliverables", "qualitative_figure_v2",
    "EVIDENCE_BUNDLE.json")


def main():
    b = json.load(open(BUNDLE))
    for letter in "abcef":
        p = b["panels"][letter]
        print(f"\n{'='*70}")
        print(f"({letter}) {p['episode_id']}  {p['scenario_id']}  "
              f"seed={p['seed']}  {p['town']}")
        print(f"    指令: {p['raw_instruction']!r}")
        print(f"    k={p['effective_k']} initial={p['initial_decision']} "
              f"label={p['decision_label']} post={p['post_answer_decision']} "
              f"reason={p['decision_reason']}")
        print(f"    grounding_frame={p['grounding_frame_id']}  "
              f"dashboard.source_frame={p['dashboard']['source_frame']}")
        print(f"    branch_duplicate={p['branch_duplicate']} "
              f"target_duplicate={p['target_duplicate']}")
        d = p["dashboard"]
        print(f"    ego_progress={d['ego_route_progress']} "
              f"decision_point={d['decision_point']} "
              f"maneuver_onset={d['maneuver_onset']}")
        print(f"    commitment={d['commitment_boundary']}")
        print(f"    latest_safe_clarification={d['latest_safe_clarification']}"
              f" ({d['latest_safe_clarification_unit']})")
        print(f"    relations: cur={d['CurrentActionRelation']} "
              f"fut={d['FutureObligationRelation']} "
              f"clar={d['ClarificationState']} rec={d['Recoverability']}")
        for c in p["candidates"]:
            print(f"    候选 {c['slot']}: {c['ordering']} "
                  f"anchor_progress={c['anchor_progress_m_recomputed']} m "
                  f"junction={c['junction_id']} "
                  f"order={c['route_order_index']} "
                  f"conf={c['detector_confidence']:.3f}")
            print(f"           behavior={c['current_behavior']} "
                  f"maneuver={c['maneuver_direction']} "
                  f"branch={c['branch_id']}")
        for pl in p["plans"]:
            print(f"    计划 {pl['kind']}: src_f={pl['source_frame_id']} "
                  f"ego_prog={pl['ego_progress_at_source_frame_m']} "
                  f"fwd={pl['forward_extent_m']} "
                  f"-> 终点 progress={pl['horizon_end_progress_m']} "
                  f"max|lat|={pl['max_abs_lateral_m']}")
        tr = p["executed_progress"]
        if tr:
            print(f"    已执行: {len(tr)} 帧 f{tr[0]['frame']}..f{tr[-1]['frame']}"
                  f"  progress {tr[0]['progress_m']} -> {tr[-1]['progress_m']} m"
                  f"  速度 {tr[0]['speed_mps']:.2f} -> {tr[-1]['speed_mps']:.2f} m/s")
        print(f"    answer_frame={p['answer_received_frame']} "
              f"delay={p['answer_delay_simulation_seconds']} "
              f"fresh_replan_src={p['fresh_replan_source_frame_id']}")
        print(f"    ticks={p['carla_tick_count']} "
              f"obs={p['control_observation_count']} "
              f"ctrl_writes={p['direct_vehicle_control_write_count']} "
              f"cand_writes={p['candidate_direct_vehicle_control_write_count']}")
        print(f"    authority={p['authority_subject']} "
              f"control_source={p['control_source']} status={p['status']}")

    print(f"\n{'='*70}\n(d) 时序面板")
    d = b["panels"]["d"]
    for k in ("episode_id", "scenario_id", "seed", "town", "raw_instruction",
              "status", "pre_event_decision", "post_event_decision",
              "detector_invocation_count", "detector_invocation_frames",
              "reground_count", "track_id", "track_loss_count",
              "track_id_switch_count", "wait_entry_frame", "wait_exit_frame",
              "wait_exit_reason", "fresh_planning_frame",
              "frames_observed_during_wait", "carla_ticks_during_wait",
              "wait_sim_seconds_from_ticks",
              "candidate_commit_count_during_wait",
              "driveclarify_low_level_control_writes", "control_owner",
              "wait_mode", "distance_travelled_m",
              "closed_loop_candidate_plan_selection_pending",
              "information_update_emitted_simulation_time"):
        print(f"    {k} = {json.dumps(d.get(k), ensure_ascii=False)}")
    print(f"    event_region.bbox_xyxy = {d['event_region']['bbox_xyxy']}")
    print(f"    information_update = {json.dumps(d['information_update'])[:300]}")
    print("    event_timeline:")
    for e in d["event_timeline"]:
        print(f"      f{e.get('frame_id')} conf={e.get('confidence'):.4f} "
              f"cand={e.get('cleared_candidate_frame')} "
              f"conf_f={e.get('cleared_confirmed_frame')} "
              f"latency={e.get('event_latency_frames')} "
              f"state={e.get('state') or e.get('event_state')}")
    print("    track_at_key_frames:")
    for f, t in (d.get("track_at_key_frames") or {}).items():
        if t:
            print(f"      f{f}: bbox={t.get('bbox_xyxy')} "
                  f"iou={t.get('association_iou')} "
                  f"src={t.get('association_source')}")


if __name__ == "__main__":
    main()
