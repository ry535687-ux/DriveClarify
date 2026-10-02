"""已停止后的行政留痕；不执行预测、评分、重标或方法修改。"""
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1]
REPO = REPORT.parents[1]
PACKET = REPO / 'reports/driveclarify_stage5b_heldout_annotation_20260914'
STAGE5A = REPO / 'reports/driveclarify_stage5a_controlled_main_ablation_20260914'
STATUS = 'STAGE5B_HELDOUT_INPUT_SCHEMA_BLOCKED'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(name, value):
    path = REPORT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def write_text(name, text):
    with (REPORT / name).open('x') as f:
        f.write(text)


def main():
    audit = json.loads((REPORT / 'INPUT_SCHEMA_AUDIT.json').read_text())
    assert audit['status'] == STATUS
    timestamp = datetime.now(timezone.utc).isoformat()
    confirmation = {
        'TASK_EQUIVALENT': [3, 4, 9, 15, 16, 17, 18, 20, 22, 30],
        'TASK_DIVERGENT': [1, 2, 6, 7, 11, 12, 23, 24, 25, 29],
        'INSUFFICIENT_EVIDENCE': [5, 8, 10, 13, 14, 19, 21, 26, 27, 28],
    }
    supplied = {f'HOLDOUT_{n:03}': relation for relation, ns in confirmation.items() for n in ns}
    with (PACKET / 'annotations_B.csv').open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len({r['case_id'] for r in rows}) == 30
    assert {r['case_id'] for r in rows} == set(supplied)
    assert all(r['label'] == supplied[r['case_id']] for r in rows)
    assert all(int(r['confidence']) == (4 if r['label'] == 'INSUFFICIENT_EVIDENCE' else 5) for r in rows)
    assert all(r['annotation_quality_flag'] == 'OK' for r in rows)
    write_json('ANNOTATION_SOURCE_CHECK.json', {
        'purpose': '阻断后的来源一致性留痕；不是 GOLD_FREEZE，也不是新的标注或评分。',
        'n': len(rows), 'B_matches_user_consensus': 30,
        'B_label_counts': dict(Counter(r['label'] for r in rows)),
        'B_confidence_counts': dict(Counter(r['confidence'] for r in rows)),
        'B_quality_flag_counts': dict(Counter(r['annotation_quality_flag'] for r in rows)),
        'annotation_A_source': 'user-supplied blinded annotation output',
        'annotation_A_confidence_summary_as_supplied_by_user': {'5': 30},
        'annotation_pass_agreement_as_confirmed_by_user': {'exact_agreement': 30, 'agreement_rate': 1.0, 'cohen_kappa': 1.0},
        'human_verification_as_supplied_by_user': 'confirmed_by_user_2026-09-14',
        'annotation_B_file_sha256': sha(PACKET / 'annotations_B.csv'),
        'annotation_B_notes_sha256': sha(PACKET / 'ANNOTATOR_B_NOTES.md'),
        'confidence_used_to_determine_labels': False,
        'gold_csv_materialized': False,
    })
    write_json('evidence/STAGE5A_PRESERVATION.json', {
        str(p.relative_to(REPO)): sha(p) for p in sorted(STAGE5A.rglob('*')) if p.is_file()
    })
    skipped = [
        'GOLD_LABELS_V1.csv', 'GOLD_FREEZE.json', 'ANNOTATION_AGREEMENT.json',
        'HUMAN_VERIFICATION_RECEIPT.md', 'PREDICTOR_FREEZE.json',
        'PREDICTION_INPUT_MANIFEST.json', 'PREDICTION_LOCK.json',
        'HELDOUT_PREDICTIONS_V1.csv', 'PREDICTION_RECEIPT.json',
        'HELDOUT_CASE_RESULTS.csv', 'HELDOUT_EVALUATION_V1.json',
        'FIG_HELDOUT_CONFUSION.png', 'FIG_HELDOUT_POLICY_BEHAVIOR.png',
        'EXPERIMENT_CHAPTER_V4_HELDOUT_DRAFT.md', 'PAPER_CLAIMS_BOUNDARY_V2.md',
        'scripts/predict_heldout.py',
    ]
    metrics = dict.fromkeys([
        'relation_accuracy', 'macro_f1', 'per_class_results', 'confusion_matrix',
        'DV_query_recall', 'EQ_unnecessary_query_rate', 'EQ_correct_no_query_rate',
        'DV_missed_query_rate', 'IE_abstention_rate', 'IE_false_determination_rate',
        'determinate_relation_coverage', 'abstention_coverage', 'case_prediction_failures',
    ])
    write_json('BLOCKED_RECEIPT.json', {
        'status': STATUS, 'timestamp_utc': timestamp,
        'blocking_instruction': '用户任务第七节：若当前 packet 的语义无法通过通用映射进入冻结 predictor，立即停止。',
        'blocking_reason': 'NATURAL_LANGUAGE_PACKET_HAS_NO_FROZEN_SEMANTIC_ADAPTER_TO_TASK_OBLIGATIONS',
        'schema_check_preceded_gold_materialization': True,
        'gold_freeze_sha256': None, 'predictor_freeze_sha256': None, 'prediction_receipt_sha256': None,
        'canonical_packet_sha256': audit['packet_file_hashes']['ANNOTATION_PACKET_CANONICAL.json']['actual'],
        'existing_stage5a_predictor_source_sha256': sha(REPO / 'driveclarify_rq1_conditional_supplement/judges_revised.py'),
        'input_schema_audit_sha256': sha(REPORT / 'INPUT_SCHEMA_AUDIT.json'),
        'annotation_source_check_sha256': sha(REPORT / 'ANNOTATION_SOURCE_CHECK.json'),
        'prediction_execution_count': 0, 'prediction_retry_count': 0, 'evaluation_execution_count': 0,
        'predictor_import_count': 0, 'input_adapter_created': False,
        'private_provenance_read': False,
        'prohibited_activity_counts': {'CARLA': 0, 'model_forward': 0, 'CUDA': 0, 'training': 0, 'route_engineering': 0},
        'unmeasured_metrics': metrics,
        'not_generated_artifacts': skipped,
        'not_generated_reason': '输入合同预检先于 gold 文件生成触发用户停止门；本轮只封存阻断证据与更新交接，不继续成功路径。',
        'heldout_promoted_to_main_experiment': False,
        'stage5a_results_preserved': True,
        'next_step_only': '独立复核输入合同阻断；本轮不恢复预测、不新增解析规则、不改 packet。',
    })
    write_text('FINAL_REPORT.md', f'''# STAGE5B-1 输入合同阻断报告

状态：`{STATUS}`。记录时间：`{timestamp}`。

在 gold 文件生成和任何预测执行之前，公共输入合同预检确认：当前自然语言 packet 无法仅靠通用薄 schema 映射进入 Stage5A 冻结关系实现。依用户任务第七节立即停止科学执行。本轮预测 0 次、评分 0 次、重试 0 次；没有获得 held-out 科学结果。

这项阻断来自用户明确的停止条件，非工具权限或 skill 审批要求。

## 可复核的阻断依据

Stage5A 实际入口为 `driveclarify_rq1_conditional_supplement.judges_revised.evaluate_c_m5_revised`。`contracts.validate_method_input` 要求 `sample_id / observation / candidates / runtime_context`。30 条 canonical case 均不直接满足该结构。

但外层字段差异本身可以适配，真正阻断位于 `_topology` 的任务语义合同：它消费两条 `runtime_context.candidate_obligations`，要求 `available=true`、五个任务字段及对应 `field_sources`，随后直接比较两候选的五字段投影。冻结调用链不包含本 packet 自然语言证据到已解析任务结构的解析入口。

| 所需字段 | packet 现有信息 | 为何不是字段重命名 |
| --- | --- | --- |
| `maneuver` | 两种解释、分支叙述 | 未提供与旧合同绑定的规范动作值 |
| `public_topology_target` | 自然语言路口、参照位置、分支描述 | 必须先完成参照定位、入口计数及分支消解 |
| `ordering` | first/second、before/after 等解释文字 | 比较的是任务义务，不能把表达形式当已解析顺序 |
| `constraint` | 场景及任务完成义务叙述 | 需抽取并归一化相关约束，不能擅自填默认值 |
| `completion_predicate` | 完成线的叙述或 null | 需解析边界对应及完成义务；不能把原文相同与否当语义判据 |
| `available / field_sources` | 无旧结构逐字段解析来源 | 不能将自然语言原文复制后自行声明已解析、available=true |

仅重新封装候选文字不能解决上述问题。新增针对 before/after、物体边界、不同路口与入口计数的解析器属于新增语义算法，超出本轮授权。把整段原文塞入比较字段会改变任务等价含义；将全部任务输入清空得到 UNKNOWN 会把表示不兼容混同为证据不足，不能作为本次评测。

没有写 adapter、调用 predictor、比较原文产生预测、使用 trajectory distance 补任务证据，也没有读取 private provenance 或 builder expected 类别。源码只作文本/AST 和字节摘要检查；关系模块未 import。

可复核资料：[INPUT_SCHEMA_AUDIT.json](INPUT_SCHEMA_AUDIT.json)、[静态检查脚本](scripts/audit_blocked_inputs.py)。五个冻结任务字段与允许来源类型由实际源码 AST 提取；无新任务 component 或 threshold。

## 完整性与标注来源

- canonical packet SHA-256：`{audit['packet_file_hashes']['ANNOTATION_PACKET_CANONICAL.json']['actual']}`。
- A/B 两个 packet、指南、重复审计均与 PACKET_FREEZE 原摘要一致；三份 packet 的 30 个 case ID 与逐条内容摘要全部一致，A/B 顺序不同。独立洗牌过程承接已有冻结记录，不声称此次重新生成了洗牌。
- Stage5A manifest 所列 10 个 Python 文件均匹配原冻结摘要，配置保持 `{json.dumps(audit['relation_config_unchanged'], ensure_ascii=False)}`。
- 旧 predictor 主源码 SHA-256：`{sha(REPO / 'driveclarify_rq1_conditional_supplement/judges_revised.py')}`。这是既有源码身份，不是本轮新的完整 predictor/adapter freeze hash。
- B 文件与用户给定的 consensus 逐条 30/30 一致，10 EQ / 10 DV / 10 IE；EQ/DV confidence=5，IE=4，quality 全 OK。来源见 [ANNOTATION_SOURCE_CHECK.json](ANNOTATION_SOURCE_CHECK.json)。
- A 的结果与 confidence=5×30，以及 A/B agreement=30/30、κ=1.0，承接用户提供的独立盲标输出及人工确认；没有冒称读取到独立的 A CSV。
- 根据用户明确事实：用户在看到 A/B 独立标注完成后人工核查并确认 30 条共同标签；无 label disagreement，无第三方 adjudication。这是 single human verification。合适描述为 “two independent blinded model-assisted annotation passes, followed by human verification”。不虚构姓名、机构或第二名人工标注者。
- confidence 不用于标签决策。原 packet 与标注文件均未改动。

重复审计无已检出的历史 exact duplicate，但有 80 对内部模板相似关系；该检查不证明自然分布、地图独立抽样或绝对语义新颖性。

## 未生成的交付与指标

本轮先进行公共 schema 预检，停止门触发时尚未落盘 GOLD_LABELS_V1 / GOLD_FREEZE，也未冻结新的 predictor/adapter。用户确认的标签并未被更改；这里只记录来源一致性，不以来源检查冒充 Gold Freeze。

Gold Freeze hash、新 Predictor Freeze hash、Prediction Receipt hash 均为 `null`。预测、逐条评分、混淆矩阵、两张检查图、V4 章节及 V2 主张文件均未生成；完整缺项见 [BLOCKED_RECEIPT.json](BLOCKED_RECEIPT.json)。这些不是遗漏的成功结果，而是用户停止条件下明确未执行的步骤。

| 请求指标 | 本轮结果 |
| --- | --- |
| 30 条 relation accuracy / macro-F1 / per-class P/R/F1 / confusion matrix | 未计算 |
| 20 条已知关系的 DV query recall、EQ unnecessary-query、EQ correct no-query、DV missed-query | 未计算 |
| 10 条 IE relation-level abstention / false determination | 未计算 |
| 30 条 determinate coverage / abstention coverage | 未计算 |
| 是否存在预测失败 case | 未知，预测未执行；不能写 0 个失败 |
| 三策略 policy sanity baselines | 未运行；未构造 evaluator-side intent 或 completion 结果 |
| CI、p-value、bootstrap | 未执行 |

## 论文主张边界

没有 held-out empirical endpoint，因此本轮不能将这 30 条提升为已完成的 Main Experiment，也不以空表或假定结果重写 V4。Stage5A 的 176 条 DEV/HIST、原 V3、受控 intent 分析、消融与历史闭环材料完整保留；用户希望将其定位为 Large-Scale Controlled Analysis 的方向仍应保留，但本轮没有新的独立验证主张可以替换原主结果。

未来如存在合规结果，应明确称 “a frozen held-out controlled task-relation set”。构造边界必须披露：“Cases were constructed to cover equivalent, divergent, and evidence-insufficient task relations; the final independently verified labels also resulted in a balanced 10/10/10 split.” 当前 consensus 是这个平衡受控集合的标签，不能据此推导现实发生频率上的 expected accuracy。标注一致性不等于客观无歧义。

历史 closed-loop validation 不属于这些 held-out cases，没有重新包装为新驾驶结果。ABSTAIN 的含义仅限 relation-level，不能解释为物理停车或安全认证。

当前最大 reviewer risk 是自然语言 packet 与已解析任务结构之间尚无冻结、可复核且独立于已知标签的语义映射。现在补写解析器会引入测试标签暴露后的方法适配，破坏本轮禁止改方法的边界。

唯一下一步建议：独立复核本次输入合同阻断。本轮停止，不恢复预测、不修改 packet 或方法。

## 验证与留痕

CPU 验证结果见 `ACCEPTANCE_RESULTS.json` 和 `evidence/TEST_OUTPUT.txt`；测试仅检查阻断证据、冻结摘要、未执行状态、指标 null 及 Stage5A 保全，不调用 predictor。交付清单为 `ARTIFACT_MANIFEST.json`。

本轮 CARLA=0、model forward=0、CUDA=0、training=0、route engineering=0；没有 Git 写操作。除新阻断目录及四份交接入口之外，未编辑项目文件。Data Analytics 验证规范用于核对证据与未执行边界；因无预测数据，制图流程未进入渲染。
''')
    increment = f'''## STAGE5B-1 {timestamp} — held-out 输入合同阻断，预测未运行

状态：`{STATUS}`。入口：`reports/driveclarify_stage5b_heldout_evaluation_20260914/FINAL_REPORT.md`。
canonical/A/B packet、指南、重复审计及 Stage5A 10 个冻结 Python 文件摘要一致；B 30 条与用户确认 consensus 一致，10/10/10，confidence 与 quality 符合给定事实。
公共 schema 预检发现：packet 的自然语言任务叙述无法仅靠薄映射变成旧 predictor 的五字段 candidate_obligations；冻结调用链无该自然语言解析入口。依本轮用户第七节立即停止，不新写语义解析规则。
预检先于 Gold 文件生成，故 GOLD_FREEZE / 新 PREDICTOR_FREEZE / PREDICTION_RECEIPT 均未生成；预测0、评分0、重试0。所有性能及 case 失败数未知，不将缺结构强制输出 UNKNOWN 充当评测。
private provenance 未读；方法/threshold/packet/原标注/Stage5A 历史结果未改；V4与两图未生成，未提升 held-out 为已完成主实验。CARLA/forward/CUDA/training/route engineering均0。
唯一下一步：独立复核本次输入合同阻断；本轮停止，不恢复预测或新增方法。此前交接原文保留如下。

'''
    write_text('evidence/HANDOFF_INCREMENT.md', increment)
    state_path = REPO / 'STATE.json'
    state = json.loads(state_path.read_text())
    keys = ['current_task', 'current_task_status', 'status', 'stage', 'step', 'next_task', 'state_updated_at']
    state['stage5b1_prior_current_pointers'] = {key: state.get(key) for key in keys}
    write_json('evidence/PRIOR_CURRENT_POINTERS.json', state['stage5b1_prior_current_pointers'])
    state.update({
        'current_task': 'STAGE5B-1 held-out 输入合同阻断，预测未运行，停止',
        'current_task_status': STATUS, 'status': STATUS, 'stage': 'STAGE5B_1_HELDOUT_EVALUATION',
        'step': '自然语言 packet 缺少进入冻结任务字段比较器的通用语义映射；禁止补写方法，停止',
        'next_task': '仅独立复核本次输入合同阻断；不恢复预测、重标、改 packet 或方法',
        'state_updated_at': timestamp,
        'driveclarify_stage5b_heldout_evaluation_20260914': {
            'status': STATUS, 'report': str((REPORT / 'FINAL_REPORT.md').relative_to(REPO)),
            'prediction_execution_count': 0, 'evaluation_execution_count': 0,
            'gold_freeze_sha256': None, 'predictor_freeze_sha256': None, 'prediction_receipt_sha256': None,
            'canonical_packet_sha256': audit['packet_file_hashes']['ANNOTATION_PACKET_CANONICAL.json']['actual'],
            'private_provenance_read': False, 'stage5a_preserved': True,
            'new_CARLA': 0, 'new_forward': 0, 'new_CUDA': 0, 'new_training': 0, 'new_route_engineering': 0,
        },
    })
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    for name in ['CURRENT_HANDOFF.md', 'NEXT_AGENT_PROMPT.md']:
        path = REPO / name
        old = path.read_text()
        path.write_text(increment + old)
    with (REPO / 'AGENT_WORKLOG.md').open('a') as f:
        f.write('\n' + increment.replace('此前交接原文保留如下。', '本轮仅完成阻断留痕。'))
    print(STATUS)


if __name__ == '__main__':
    main()
