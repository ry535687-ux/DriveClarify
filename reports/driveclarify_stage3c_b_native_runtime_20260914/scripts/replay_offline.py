"""复核包内仅CPU重提取既有B观测；绝不启动原生或加载模型。"""
import importlib.util
import json
from pathlib import Path

REPORT=Path(__file__).resolve().parents[1]
ROOT=REPORT.parents[1]
DEV=ROOT/'experiments/driveclarify_native_clear_backend_dev_20260913'


def main():
    case=json.loads((DEV/'configs/case_B.json').read_text())
    contract=json.loads((DEV/'configs/evaluation_B.json').read_text())
    raw=DEV/'outputs'/case['case_id']
    rows=[json.loads(line) for line in (raw/'world_state.jsonl').read_text().splitlines()]
    route=json.loads((raw/'ROUTE_BINDING_RUNTIME.json').read_text())
    native=json.loads((raw/'leaderboard_results.json').read_text())
    spec=importlib.util.spec_from_file_location('frozen_observer_extract',DEV/'extract_observer.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=module.summarize(case,contract,rows,route,native)
    expected=json.loads((REPORT/'B_ROUTE_PLAN_CONTROL_SUMMARY.json').read_text())
    checks={
        'case_id':result['case_id']==expected['case_id'],
        'record_count':result['world_state_rows']==expected['world_state_rows'],
        'model_observations':result['model_observation']==expected['model_observation'],
        'returned_control_observations':result['control_observation']==expected['control_observation'],
        'gate_events':result['task_event_evaluation']['events']==expected['task_event_evaluation']['events'],
        'cadence':result['task_event_evaluation']['observed_intervals_cadence_consistent']==expected['task_event_evaluation']['observed_intervals_cadence_consistent'],
        'native_record_id':all(r['route_id']=='RouteScenario_26458_rep0' and r['town_name']=='Town07' and r['scenario_name']=='T_Junction_1' for r in native['_checkpoint']['records']),
    }
    print(json.dumps({'pass':all(checks.values()),'checks':checks,'rows':len(rows),'native_runs':0},indent=2))
    if not all(checks.values()):raise SystemExit(1)


if __name__=='__main__':main()
