"""按用户消息手写的 STEP3A 定向反例；不是尚未收到的外部 review_probes.py。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.driveclarify_three_policy_dev.policy import (
    POLICIES, ControlledAnswerService, CPURequestBackend, DurableQuestions, ThreePolicy,
)
from experiments.driveclarify_three_policy_dev.tests.fixtures import public_case, state


def decision(public, policy, now):
    with tempfile.TemporaryDirectory() as directory:
        writer = DurableQuestions(directory)
        backend = CPURequestBackend()
        runner = ThreePolicy(policy, public, writer, ControlledAnswerService("FIRST", writer), backend, enabled=True)
        current = state(1)
        current["sim_time_s"] = now
        try:
            result = runner.step(current)
            return {"action": result["action"], "reason": result.get("reason"), "requests": len(backend.requests)}
        except (TypeError, AttributeError) as error:
            return {"exception": type(error).__name__, "detail": str(error), "requests": len(backend.requests)}


def main():
    rows = []
    for label, now, change in (("after_deadline", 11.0, {}), ("unknown_budget", 1.0, {"budget_s": None}),
                              ("before_start", 1.0, {"anchor_sim_time_s": 2.0}),
                              ("execute_only_window", 8.5, {}), ("execution_equal_boundary", 9.0, {})):
        for policy, equivalent in ((POLICIES[0], False), (POLICIES[2], True)):
            public = public_case(equivalent=equivalent)
            public["deadline"].update(change)
            expected = "REQUEST_ROUTE" if label == "execute_only_window" else "WAIT"
            actual = decision(public, policy, now)
            rows.append({"id": "R1_"+label+"_"+policy, "expected": expected, "actual": actual,
                         "contract_met": actual.get("action") == expected})
    for label, field, value in (("candidates_null", "candidates", None), ("candidate_null", "candidates", [None, None]),
                                ("checks_null", "checks", None)):
        for policy in POLICIES:
            public = public_case()
            public[field] = value
            actual = decision(public, policy, 1.0)
            rows.append({"id": "R2_"+label+"_"+policy, "expected": "WAIT", "actual": actual,
                         "contract_met": actual.get("action") == "WAIT" and actual["requests"] == 0})
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        (folder/"public.json").write_text(json.dumps(public_case()))
        (folder/"states.json").write_text(json.dumps([state(), state(2)]))
        result = subprocess.run([sys.executable, "-B", "-m", "experiments.driveclarify_three_policy_dev", "--enable-cpu-dev", "policy",
            "--public", str(folder/"public.json"), "--states", str(folder/"states.json"), "--policy", POLICIES[1],
            "--receipts", str(folder/"receipts"), "--answer-file", str(folder/"missing.json")], cwd=ROOT, capture_output=True, text=True)
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            output = None
        files = []
        for path in sorted((folder/"receipts").glob("*.json")):
            files.append({"name": path.name, "content": json.loads(path.read_text())})
        actual = {"exit_code": result.returncode, "output": output, "durable_files": files, "stderr": result.stderr}
        met = bool(output and output.get("query_count") == 1 and output.get("backend_requests") == []
                   and output.get("service_failure") and len(files) >= 2 and "Traceback" not in result.stderr)
        rows.append({"id": "R3_missing_answer_file", "expected": "ONE_QUESTION_DURABLE_FAILURE_NO_REQUEST", "actual": actual, "contract_met": met})
    print(json.dumps({"provenance": "RECONSTRUCTED_FROM_USER_REQUEST_NOT_EXTERNAL_REVIEW_SCRIPT", "cases": rows,
                      "contract_met": sum(row["contract_met"] for row in rows), "total": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
