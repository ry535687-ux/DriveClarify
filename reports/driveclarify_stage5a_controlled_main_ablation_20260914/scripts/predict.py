"""先锁定公开输入与三策略建议；本进程不解析标签文件。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from common import *
from policies import project_public, decide

def run():
    manifest = verify_sources()  # 字节摘要核验；标签内容从不解码/传入策略
    config = manifest['relation_config']
    public_rows, decisions, index = [], [], []
    for split in ('DEV', 'HIST'):
        raw_rows = read_jsonl(CROOT / f'method_inputs/{split}_METHOD_INPUTS.jsonl')
        for raw in raw_rows:
            assert [c['candidate_id'] for c in raw['candidates']] == ['K1', 'K2']
            public = project_public(raw)
            pid = public['sample_id']
            public_rows.append({'public_id': pid, 'public_sha256': digest(public), 'input': public})
            index.append({'public_id': pid, 'record_id': raw['sample_id'], 'layout_id': raw['layout_id'],
                          'split': split, 'raw_input_digest_declared': raw['method_input_sha256']})
            for policy in POLICIES:
                decisions.append({'public_id': pid, 'policy': policy, 'public_sha256': digest(public),
                                  **decide(policy, public, config)})
    assert len({r['public_id'] for r in public_rows}) == len(public_rows)
    for name, rows in [('PUBLIC_INPUTS', public_rows), ('POLICY_DECISIONS', decisions), ('RECORD_INDEX', index)]:
        write_jsonl(REPORT / f'benchmark/{name}.jsonl', rows)
    write_json(REPORT / 'benchmark/PREDICTION_LOCK.json', {
        'protocol_sha256': manifest['protocol_sha256'],
        'files': {n: sha(REPORT / f'benchmark/{n}.jsonl') for n in ('PUBLIC_INPUTS', 'POLICY_DECISIONS', 'RECORD_INDEX')},
        'public_records': len(public_rows), 'policy_decisions': len(decisions),
        'evaluator_labels_parsed_in_predict_process': False, 'new_forward_count': 0,
        'meaning': '策略建议先锁定；历史数据已暴露，不是盲预注册'})
    print({'public_records': len(public_rows), 'locked_decisions': len(decisions), 'new_forward': 0})

if __name__ == '__main__':
    run()
