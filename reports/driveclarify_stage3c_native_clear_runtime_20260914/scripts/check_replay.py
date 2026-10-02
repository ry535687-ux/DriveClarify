"""仅从封存A数据重放原冻结CPU整理器；不查询主机、不启动任何进程。"""
import argparse
import importlib
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--raw-a", type=Path, required=True)
parser.add_argument("--expected", type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.source.resolve()))
module = importlib.import_module("experiments.driveclarify_native_clear_backend_dev_20260913.extract_observer")
configs = args.source / "experiments/driveclarify_native_clear_backend_dev_20260913/configs"
case = json.loads((configs / "case_A.json").read_text())
contract = json.loads((configs / "evaluation_A.json").read_text())
rows = [json.loads(line) for line in (args.raw_a / "world_state.jsonl").read_text().splitlines()]
route = json.loads((args.raw_a / "ROUTE_BINDING_RUNTIME.json").read_text())
stats = json.loads((args.raw_a / "leaderboard_results.json").read_text())
result = module.summarize(case, contract, rows, route, stats)
expected = json.loads(args.expected.read_text())
keys = ("task_event_evaluation", "model_observation", "control_observation", "world_state_rows", "initial_route_receipt")
assert all(result[k] == expected[k] for k in keys), "ARCHIVED_CPU_REPLAY_MISMATCH"
print(json.dumps({"status": "MATCH_ARCHIVED_A_EXTRACTION", "world_state_rows": len(rows), "compared_fields": list(keys), "native_runs": 0}, ensure_ascii=False, indent=2))
