"""替换 RQ1 判断 seam，RQ3 联合时机及所有执行父方法按原 MRO 运行。"""
from dataclasses import asdict

from driveclarify_clear_passthrough_v11.contracts import ConsequenceDecision, PolicyAction
from driveclarify_rq1_v2.consequence import AmbiguityStatus, GateAction, consequence_gate
from .relation import compare_trajectories, unknown


def make_agent_classes(rq3_class, rq1_class, metric_config, write_json):
    class ABL_FULL(rq3_class):
        pass

    class TrajectoryRelationSeam(rq1_class):
        def _evaluate_consequences(self, routes):
            # 不读取 TaskSignature、道路选项、目标、候选名称/描述或 HIGH/LOW。
            if len(routes) != 2:
                result = unknown("EXACTLY_TWO_CANDIDATES_REQUIRED")
            else:
                result = compare_trajectories(routes[0].simlingo_preview_plan,
                                              routes[1].simlingo_preview_plan, metric_config)
            # 候选 ID 只在判定之后绑定 ACT 的既有 rank-one；不进入 metric。
            selected = routes[0].candidate.candidate_id if routes else None
            gate = consequence_gate(AmbiguityStatus.AMBIGUOUS, result.relation,
                                    deterministic_candidate_id=selected)
            row = asdict(result)
            row.update(relation=result.relation.value, requested_action=gate.action.value,
                       configuration_id="ABL_TRAJ_ONLY", version="TASK_RELATION_ABLATION_DEV_V1",
                       decision_source_frame=self._latest_supervision_frame,
                       metric_config=asdict(metric_config),
                       actual_emitted_question=None,
                       emission_authority="EXISTING_DURABLE_ASK_WRITER_ONLY")
            write_json(self._output / "ABL_TASK_RELATION_RECEIPT.json", row)
            if gate.action is GateAction.ACT:
                return ConsequenceDecision(PolicyAction.ACT, gate.selected_candidate_id, None, gate.reason_codes)
            if gate.action is GateAction.ASK:
                return ConsequenceDecision(PolicyAction.ASK, None,
                    "Which of the two grounded task interpretations did you mean?", gate.reason_codes)
            return ConsequenceDecision(PolicyAction.WAIT, None, None, gate.reason_codes)

    class ABL_TRAJ_ONLY(rq3_class, TrajectoryRelationSeam):
        pass

    return {"ABL_FULL": ABL_FULL, "ABL_TRAJ_ONLY": ABL_TRAJ_ONLY}
