"""只读 STEP2 资产的直接依赖，摘出地图连接段；不导入或执行历史 runner。"""
import copy
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
REPORT = Path(__file__).resolve().parents[1]
DEV = ROOT / "experiments/driveclarify_three_policy_dev"
BASE = ROOT / "reports/driveclarify_v3_selected_edge_roadoption_command_owner_native_closure"
SOURCE = BASE / "NATIVE_RUNS/MRC-EL-003/DEV_V3_COMMAND_OWNER_CYCLE_02/GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"
ROUTE = ROOT / "reports/driveclarify_v2_7_el_positive_scenario_replacement_and_formal_reseal/qualification_routes/MRC_EL_003.xml"


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def check(value, source, provenance="PUBLIC_TASK_DEFINITION"):
    return {"value": value, "provenance": provenance if value is not None else "UNKNOWN", "source": source}


def gate(poly, index):
    center = poly[index]
    previous = poly[index-1]
    normal = [center[0]-previous[0], center[1]-previous[1]]
    length = math.hypot(*normal)
    return {"center": center, "normal": [v/length for v in normal], "half_width_m": 0.75}


def main():
    source = json.loads(SOURCE.read_text())
    connectors = source["candidate_executable_connector_evidence"]
    assert len(connectors) == 2
    xml = ET.parse(ROUTE).getroot().find("route")
    positions = [[float(p.attrib[k]) for k in ("x", "y")] for p in xml.findall("./waypoints/position")]
    common = [positions[0], connectors[0]["polyline_xy_m"][0]]
    poly_a, poly_b = [c["polyline_xy_m"] for c in connectors]
    prefix_b = [p for p in connectors[0]["route_continuation_xy_m"] if p[0] > poly_b[0][0]+0.0001]
    routes = [common[:1] + poly_a, common[:1] + prefix_b + poly_b]
    # 使用公开地图连接段，不读取候选模型预测轨迹，也不读取原乘客答案作为新任务标签。
    excerpt = {"source": str(SOURCE.relative_to(ROOT)), "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
               "json_pointer": "/candidate_executable_connector_evidence", "geometry_kind": "HISTORICAL_MAP_CONNECTOR_EXPORT_NOT_PREDICTED_TRAJECTORY",
               "candidates": [{k: c[k] for k in ("candidate_id", "junction_id", "entry_road_id", "entry_lane_id", "exit_road_id",
                   "exit_lane_id", "polyline_xy_m", "route_continuation_xy_m", "status", "reason_code", "live_map_pair_match")} for c in connectors]}
    write(REPORT / "evidence/MAP_CONNECTOR_EXCERPT.json", excerpt)
    evaluation = {"contract_id": "STEP3_TOWN04_TWO_RIGHT_TURN_GATES_V1", "geometry_status": "STATIC_BOUND",
                  "qualification": "NEEDS_DEV_DRIVING", "source": str(SOURCE.relative_to(ROOT)),
                  "derivation": "独立预声明任务：通过所指右转连接段；进入另一连接段即违反该任务顺序，物理可恢复性未验证。门取地图连接段的中点、倒数第4点和终点切线；0.75m半宽仅为待核验评价参数，非安全余量。",
                  "sampling_rule": "相邻实际 frame 差必须为1；仅用相邻采样弦线，丢帧断开次序；真实位姿误差/门宽覆盖待核验。",
                  "max_frame_gap": 1, "safety_endpoints": ["collision", "offroad", "red_light", "stop_sign", "route_deviation"], "branches": {}}
    branch_ids = ["J870_RIGHT", "J483_RIGHT"]
    for branch, poly in zip(branch_ids, (poly_a, poly_b)):
        evaluation["branches"][branch] = {"irreversible_for_task": True,
            "entry": gate(poly, len(poly)//2), "exit": gate(poly, len(poly)-4), "end": gate(poly, len(poly)-1)}
    write(DEV / "configs/TOWN04_BRANCH_CONTRACT.json", evaluation)
    cases = []
    for equivalent in (False, True):
        case_id = "TOWN04_EQUIVALENT_DEV" if equivalent else "TOWN04_DIVERGENT_DEV"
        candidates = []
        for i, candidate in enumerate(("A", "B")):
            target = 0 if equivalent else i
            branch = branch_ids[target]
            candidates.append({"id": candidate, "route_xy": routes[target],
                "task_evidence": check(True, "本轮给定候选任务合同，独立于隐藏真意与方法预测；不是端到端 grounding 认证"),
                "admissibility": check(None, "地图有连接段；原终点重接入、原生方向检查及 evaluator 覆盖仍未闭合"),
                "task_signature": {"candidate_id": candidate, "certified": True, "certificate_id": case_id+"_"+candidate,
                    "relevant_components": ["maneuver_obligation", "irreversible_branch_obligation", "task_completion_region"],
                    "maneuver_obligation": "RIGHT", "irreversible_branch_obligation": branch,
                    "task_completion_region": branch+"_END_GATE"}})
        public = {"case_id": case_id,
            "instruction": "在所指的那个右转口右转；候选为前方第一处或第二处。" if not equivalent else "在第一处右转口右转；参照解释甲、乙均指向该连接段。",
            "ambiguity": "AMBIGUOUS", "clear_candidate_id": None, "default_candidate_id": "A", "candidates": candidates,
            "question": {"text": "您指的是第一处还是第二处右转口？" if not equivalent else "您采用参照解释甲还是解释乙（两者都对应第一处右转口）？",
                "answer_to_candidates": {"FIRST": ["A"], "SECOND": ["B"]}},
            "checks": {"interaction_service": check(True, "显式问题回执后的本地受控答案文件服务", "CONTROLLED_SERVICE_CONDITION"),
                       "execution_constraints": check(None, "未取得两候选 native 路线 admissibility、终点一致性及 evaluator 完整观测证明；不沿用 E7/E9 True")},
            "query_budget": 1,
            "deadline": {"kind": "PUBLIC_BOUNDARY_ESTIMATE", "clock": "SIMULATION_TIME", "anchor_kind": "PUBLIC_TASK_START",
                         "anchor_event_id": "TOWN04_DEV_PUBLIC_ROUTE_START", "anchor_sim_time_s": None,
                         "budget_s": None, "interaction_budget_s": None, "execution_reserve_s": None,
                         "evidence": check(None, "公共起点及首个分支几何已绑定，时钟观测与可信预算未绑定；不使用历史 first_ASK+3s")}}
        path = DEV / ("configs/" + case_id + ".public.json")
        write(path, public)
        cases.append({"case_id": case_id, "layout_id": "TOWN04_MRC_EL_003_SHARED_LAYOUT", "independent_layout_count_increment": 0 if equivalent else 1,
            "binding_status": "STATIC_BOUND", "runtime_qualification": "NEEDS_DEV_DRIVING", "ready_for_driving": False,
            "legal_candidate_status": "MAP_CONNECTORS_PRESENT_RUNTIME_ADMISSIBILITY_UNKNOWN",
            "public_config": str(path.relative_to(ROOT)), "evaluation_contract": str((DEV / "configs/TOWN04_BRANCH_CONTRACT.json").relative_to(ROOT)),
            "common_start_xy": common, "public_default": "A", "candidate_branch_mapping": {"A": branch_ids[0], "B": branch_ids[0 if equivalent else 1]},
            "hidden_truth_isolation": "评价侧 allowed_branches 和受控服务 answer_label 单独输入；不写入公共配置。",
            "default_route_is_not_original_native_route": True,
            "original_native_route": {"path": str(ROUTE.relative_to(ROOT)), "id": xml.attrib["id"], "town": xml.attrib["town"], "start_xy": positions[0], "destination_xy": positions[-1]},
            "route_scope": "地图候选连接段及共用前缀；尚非可装入的完整同终点路线，禁止直接替换最后一点冒充重接入。",
            "backend_entry": "driveclarify_clear_passthrough_v11/supervisor.py:ClearPassThroughSafeReplanSupervisor.process → RouteTransitionManager.propose；simlingo_agent.py:_bind_candidate_route/_commit_resolved_route（只记录接缝，未接入）",
            "blocking_gaps": ["G1_BOTH_CANDIDATES_SAME_DESTINATION_RECONNECT_AND_COMMAND", "G2_NATIVE_EVALUATOR_ROUTE_COVERAGE_AND_TERMINATION", "G3_SHARED_CLOCK_AND_EXECUTION_EVIDENCE"]})
    manifest = {"scope": "STEP3_CPU_ONLY", "enabled_by_default": False, "real_independent_layouts": 1,
        "real_task_configs": 2, "cases": cases,
        "rejected_asset": {"path": "reports/driveclarify_rq3_bench2drive_closed_loop_system_validation_v1/engineering_only/round_02/assets/REF-CRITICAL-CANDIDATE-ROUTES.json",
                           "status": "UNBINDABLE_AS_DIVERGENT_BRANCH_TASK", "reason": "同一道路序列的偏移与不同任务字符串，不能认证两个不同分支通过事件。"},
        "source_assets": [str(SOURCE.relative_to(ROOT)), str(ROUTE.relative_to(ROOT)), str((BASE/"SELECTED_EDGE_ROADOPTION_PROVENANCE_AUDIT.json").relative_to(ROOT)), str((BASE/"MRC_EL_COMMAND_OWNER_PREINSTALL_RECEIPT.json").relative_to(ROOT))],
        "synthetic_clear_case": "tests/fixtures.py:public_case(clear=True)，只属逻辑测试", "native_driving_runs": 0,
        "gates_are_not_driving_qualification": True}
    write(REPORT / "DEV_CASES.json", manifest)
    # 小型复现样例；expected 由 test_prototype.py 手写，不从被测输出生成。
    import sys
    sys.path.insert(0, str(ROOT))
    from experiments.driveclarify_three_policy_dev.tests.fixtures import public_case, state, trace, contract, safety, TRUTH, COVERAGE
    for name, value in {"SYNTHETIC.public": public_case(), "SYNTHETIC.states": [state(), state(2)],
                        "SYNTHETIC.trace": trace(), "SYNTHETIC.contract": contract(), "SYNTHETIC.safety": safety(),
                        "SYNTHETIC.truth": TRUTH, "SYNTHETIC.coverage": COVERAGE,
                        "SYNTHETIC.answer": {"answer_label": "SECOND"}}.items():
        write(DEV / ("configs/"+name+".json"), value)
    print(json.dumps({"static_bound_cases": 2, "shared_layouts": 1, "runtime_ready": 0,
                      "candidate_endpoint_distance_m": math.dist(routes[0][-1], routes[1][-1]),
                      "original_endpoint_distance_m": [math.dist(route[-1], positions[-1]) for route in routes],
                      "source": str(SOURCE.relative_to(ROOT))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
