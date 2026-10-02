"""合成 Y 形几何与受控十秒窗口，数值仅为软件测试，不应用于真实资产。"""
import copy


def check(value=True, source="手写合成任务合同", provenance="PUBLIC_TASK_DEFINITION"):
    return {"value": value, "source": source, "provenance": provenance if value is not None else "UNKNOWN"}


def public_case(equivalent=False, clear=False):
    rows = []
    for candidate, branch, xy in (("A", "LEFT", [[0, 0], [2, 2], [4, 2]]),
                                  ("B", "LEFT" if equivalent else "RIGHT", [[0, 0], [2, -2], [4, -2]])):
        rows.append({"id": candidate, "route_xy": xy if not equivalent else [[0, 0], [2, 2], [4, 2]],
                     "admissibility": check(), "task_evidence": check(),
                     "task_signature": {"candidate_id": candidate, "certified": True,
                                        "certificate_id": "SYNTHETIC-" + candidate,
                                        "relevant_components": ["irreversible_branch_obligation", "task_completion_region"],
                                        "irreversible_branch_obligation": branch,
                                        "task_completion_region": branch + "_END"}})
    return {"case_id": "SYNTHETIC_LOGIC_ONLY", "instruction": "左转" if clear else "在所指分支通过",
            "ambiguity": "CLEAR" if clear else "AMBIGUOUS", "clear_candidate_id": "A" if clear else None,
            "default_candidate_id": "A", "candidates": rows,
            "question": {"text": "是左侧还是右侧解释？", "answer_to_candidates": {"FIRST": ["A"], "SECOND": ["B"]}},
            "checks": {"interaction_service": check(source="CPU 同步受控服务", provenance="CONTROLLED_SERVICE_CONDITION"),
                       "execution_constraints": check(source="CPU 后端只记录请求，合成逻辑条件")},
            "query_budget": 1,
            "deadline": {"kind": "CONTROLLED_PROTOCOL_DEADLINE", "clock": "SIMULATION_TIME",
                         "anchor_kind": "PUBLIC_TASK_START", "anchor_event_id": "SYNTHETIC_START",
                         "anchor_sim_time_s": 0.0, "budget_s": 10.0, "interaction_budget_s": 1.0,
                         "execution_reserve_s": 1.0, "evidence": check(source="仅 CPU 合成十秒协议，非车辆物理时限")}}


def state(frame=1):
    return {"frame": frame, "sim_time_s": float(frame), "observation_id": "SYNTHETIC:" + str(frame), "xy": [0.0, 0.0]}


def contract():
    return {"contract_id": "SYNTHETIC_Y_PASS", "max_frame_gap": 1,
            "safety_endpoints": ["collision", "offroad", "red_light"],
            "branches": {branch: {"irreversible_for_task": True,
                         **{kind: {"center": [x, y], "normal": [1, 0], "half_width_m": 0.4}
                            for kind, x in (("entry", 1), ("exit", 3), ("end", 4))}}
                         for branch, y in (("LEFT", 2), ("RIGHT", -2))}}


def trace(y=2, reverse=False):
    xs = [0, 2, 3.5, 5]
    if reverse:
        xs.reverse()
    return [{"frame": i, "sim_time_s": i * 0.05, "x": x, "y": y} for i, x in enumerate(xs)]


def safety():
    return {key: {"event_observed": False, "coverage_complete": True} for key in contract()["safety_endpoints"]}


COVERAGE = {"start_observed": True, "end_observed": True}
TRUTH = {"allowed_branches": ["LEFT"]}
