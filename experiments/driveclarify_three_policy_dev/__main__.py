"""只提供默认关闭的 CPU 策略请求与离线轨迹评价命令。"""
import argparse
import json
from pathlib import Path

from .evaluator import evaluate_trace
from .policy import POLICIES, CPURequestBackend, DurableQuestions, FileAnswerService, ThreePolicy


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="STEP3 CPU 开发；不提供原生驾驶接口")
    parser.add_argument("--enable-cpu-dev", action="store_true")
    sub = parser.add_subparsers(dest="operation", required=True)
    policy = sub.add_parser("policy")
    policy.add_argument("--public", required=True)
    policy.add_argument("--states", required=True)
    policy.add_argument("--policy", choices=POLICIES, required=True)
    policy.add_argument("--receipts", required=True)
    policy.add_argument("--answer-file", help="仅受控交互服务读取；NO_CLARIFICATION 不打开")
    evaluator = sub.add_parser("evaluate")
    for field in ("contract", "trace", "truth", "coverage", "safety"):
        evaluator.add_argument("--" + field, required=True)
    args = parser.parse_args()
    if not args.enable_cpu_dev:
        parser.error("CPU_DEV_DISABLED：需显式 --enable-cpu-dev；此开关不授权驾驶")
    if args.operation == "evaluate":
        output = evaluate_trace(*(read(getattr(args, k)) for k in ("contract", "trace", "truth", "coverage", "safety")))
    else:
        try:
            public, states = read(args.public), read(args.states)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            print(json.dumps({"kind": "CPU_DEVELOPMENT_ONLY", "action": "WAIT", "reason": "CONFIG_LOAD_ERROR",
                              "detail": type(error).__name__, "authority": "PRESERVE_EXISTING_AUTHORITY"}))
            return 2
        if not isinstance(states, list):
            print(json.dumps({"kind": "CPU_DEVELOPMENT_ONLY", "action": "WAIT", "reason": "STATES_MUST_BE_ARRAY",
                              "authority": "PRESERVE_EXISTING_AUTHORITY"}))
            return 2
        writer = DurableQuestions(args.receipts)
        service = FileAnswerService(args.answer_file, writer)
        backend = CPURequestBackend()
        try:
            runner = ThreePolicy(args.policy, public, writer, service, backend, enabled=True)
        except ValueError as error:
            # 仅配置构造边界；step 内的编程异常不在此处吞掉。
            print(json.dumps({"kind": "CPU_DEVELOPMENT_ONLY", "action": "WAIT", "reason": str(error),
                              "authority": "PRESERVE_EXISTING_AUTHORITY"}))
            return 2
        decisions = [runner.step(state) for state in states]
        output = {"kind": "CPU_DEVELOPMENT_ONLY", "decisions": decisions,
                  "answer_reads": service.read_count, "answer_read_attempts": service.read_attempt_count,
                  "query_count": runner.query_count, "service_failure": runner.service_failure,
                  "backend_requests": backend.requests}
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 3 if args.operation == "policy" and runner.service_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
