"""导出定性图各面板所需的真实标注数据（候选框、置信度、决策状态、帧号）。"""
import json
import os

ROOT = "/home/buaa/wrh/DriveClarify"
SUITE = os.path.join(
    ROOT, "artifacts", "driveclarify_method_v1_visual_behavioral_acceptance_suite_v1"
)

CASES = [
    "DCVA1-01-ACT-0815",
    "DCVA1-02-AS-0815",
    "DCVA1-03-ASK-0815",
    "DCVA1-04-WAIT-0815",
    "DCVA1-06-TL-0815",
    "DCVA1-07-HR-ER1-0815",
]


def dump_case(case):
    j = json.load(
        open(os.path.join(SUITE, case, "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"))
    )
    print(f"===== {case}")
    g = j.get("grounding", {})
    print("  grounding.candidates:")
    for c in g.get("candidates", []) or []:
        keep = {
            k: c[k]
            for k in (
                "candidate_id",
                "phrase",
                "confidence",
                "bbox_xyxy",
                "semantic_label",
                "apparent_role",
            )
            if k in c
        }
        print("    ", json.dumps(keep, ensure_ascii=False))
    print(f"  grounding keys: {sorted(g.keys())}")
    print("  target_binding_receipts:")
    for r in j.get("target_binding_receipts", []) or []:
        keep = {
            k: r[k]
            for k in (
                "candidate_id",
                "bbox_xyxy",
                "branch_id",
                "route_order",
                "confidence",
                "phrase",
                "current_behavior",
            )
            if k in r
        }
        print("    ", json.dumps(keep, ensure_ascii=False))
    dw = j.get("decision_window_dashboard", {})
    print(
        "  dashboard:",
        json.dumps(
            {
                k: dw.get(k)
                for k in (
                    "ClarificationState",
                    "CurrentActionRelation",
                    "FutureObligationRelation",
                    "Recoverability",
                    "ambiguity_state",
                )
            },
            ensure_ascii=False,
        ),
    )
    for k in (
        "decision_label",
        "decision_reason",
        "initial_decision",
        "post_answer_decision",
        "answer",
        "answer_delay_simulation_seconds",
        "answer_received_frame",
        "raw_k",
        "effective_k",
        "source_frame",
    ):
        if k in j:
            print(f"  {k} = {json.dumps(j[k], ensure_ascii=False)}")
    print()


def dump_t4():
    p = os.path.join(
        ROOT,
        "artifacts",
        "temporal_grounding_v1",
        "T4_train_bounded_closed_loop",
        "TEMPORAL_GROUNDING_V1_LIVE_RECEIPT.json",
    )
    j = json.load(open(p))
    tl = j["track_lifecycle"]
    print("===== T4_train_bounded_closed_loop")
    print(
        "  wait_entry=%s wait_exit=%s fresh_planning=%s"
        % (j["wait_entry_frame"], j["wait_exit_frame"], j["fresh_planning_frame"])
    )
    print("  track_lifecycle sample (idx, keys):", sorted(tl[0].keys()))
    for idx in (0, 20, 42, 64, 85):
        e = tl[idx]
        print(
            f"    idx={idx} "
            + json.dumps(
                {
                    k: e.get(k)
                    for k in (
                        "frame",
                        "age_frames",
                        "bbox_xyxy",
                        "confidence",
                        "association_iou",
                    )
                }
            )
        )


if __name__ == "__main__":
    for c in CASES:
        dump_case(c)
    dump_t4()
