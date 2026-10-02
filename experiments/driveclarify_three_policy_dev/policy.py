"""三策略共享的受控问题、时间与后端请求合同。只依赖标准库和真实 CPU 比较器。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path

from driveclarify_rq1_v2.consequence import (
    TASK_COMPONENTS, TaskSignature, compare_task_signatures,
)

POLICIES = ("NO_CLARIFICATION", "AMBIGUITY_IMMEDIATE_QUERY", "DRIVECLARIFY_CONTROLLED")
PROVENANCE = {"PUBLIC_TASK_DEFINITION", "CONTROLLED_SERVICE_CONDITION", "EXISTING_VALID_CHECK"}
PUBLIC_KEYS = {"case_id", "instruction", "ambiguity", "clear_candidate_id", "default_candidate_id",
               "candidates", "question", "checks", "deadline", "query_budget"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def valid_check(row):
    return (isinstance(row, dict) and row.get("value") is True
            and isinstance(row.get("provenance"), str)
            and row.get("provenance") in PROVENANCE
            and isinstance(row.get("source"), str) and bool(row["source"].strip()))


def valid_state(state):
    return (isinstance(state, dict) and type(state.get("frame")) is int
            and state["frame"] >= 0 and finite(state.get("sim_time_s"))
            and isinstance(state.get("observation_id"), str) and bool(state["observation_id"])
            and isinstance(state.get("xy"), list) and len(state["xy"]) == 2
            and all(finite(v) for v in state["xy"]))


def public_structure_errors(public):
    """只检查本入口使用的容器形状；缺任务证据仍由关系函数报告 UNKNOWN。"""
    if not isinstance(public, dict):
        return ["PUBLIC_MUST_BE_OBJECT"]
    errors = []
    rows = public.get("candidates")
    if not isinstance(rows, list):
        errors.append("CANDIDATES_MUST_BE_ARRAY")
    else:
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f"CANDIDATE_{index}_MUST_BE_OBJECT")
                continue
            for key in ("task_signature", "task_evidence", "admissibility"):
                if row.get(key) is not None and not isinstance(row[key], dict):
                    errors.append(f"CANDIDATE_{index}_{key.upper()}_MUST_BE_OBJECT_OR_NULL")
    if not isinstance(public.get("checks", {}), dict):
        errors.append("CHECKS_MUST_BE_OBJECT")
    for key in ("question", "deadline"):
        if public.get(key) is not None and not isinstance(public[key], dict):
            errors.append(key.upper()+"_MUST_BE_OBJECT_OR_NULL")
    return errors


def candidates_valid(public):
    if public_structure_errors(public):
        return False
    rows = public.get("candidates", [])
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2:
        return False
    ids = [r.get("id") for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or any(not isinstance(v, str) or not v for v in ids):
        return False
    if len(set(ids)) != len(ids) or public.get("default_candidate_id") not in ids:
        return False
    for row in rows:
        xy = row.get("route_xy")
        if not valid_check(row.get("admissibility")) or not isinstance(xy, list) or len(xy) < 2:
            return False
        if any(not isinstance(p, list) or len(p) != 2 or not all(finite(v) for v in p) for p in xy):
            return False
    return True


def relation(public):
    """必要字段先验校验；不接收轨迹、参照一致性或隐藏真意作为关系操作数。"""
    if public_structure_errors(public):
        return "UNKNOWN"
    rows = public.get("candidates", [])
    if len(rows) != 2:
        return "UNKNOWN"
    signatures = []
    for row in rows:
        sig = row.get("task_signature")
        if not isinstance(sig, dict) or sig.get("candidate_id") != row.get("id"):
            return "UNKNOWN"
        fields = sig.get("relevant_components")
        if (not isinstance(fields, list) or not fields
                or any(not isinstance(k, str) or k not in TASK_COMPONENTS for k in fields)
                or any(not isinstance(sig.get(k), str) or not sig[k].strip() for k in fields)
                or not isinstance(sig.get("certificate_id"), str) or not sig["certificate_id"].strip()
                or not valid_check(row.get("task_evidence"))):
            return "UNKNOWN"
        signatures.append(TaskSignature.from_mapping(sig))
    return compare_task_signatures(*signatures).relation.value


def question_valid(public):
    """仅支持明确声明的两答案单元素完整分区，不声称一般语言可回答性。"""
    if not candidates_valid(public):
        return False
    q = public.get("question")
    if not isinstance(q, dict) or not isinstance(q.get("text"), str) or not q["text"].strip():
        return False
    mapping = q.get("answer_to_candidates")
    ids = {r["id"] for r in public["candidates"]}
    if not isinstance(mapping, dict) or len(mapping) != 2 or len(ids) != 2:
        return False
    subsets = list(mapping.values())
    return (all(isinstance(k, str) and bool(k.strip()) for k in mapping)
            and all(isinstance(v, list) and len(v) == 1 and isinstance(v[0], str) and v[0] in ids for v in subsets)
            and {v[0] for v in subsets} == ids)


def deadline_result(contract, now, *, phase="QUERY"):
    """公共固定截止。直接执行仅收执行预留；询问/回答后保留原保守总预算。

    所有阶段严格要求 remaining > required；等号拒绝。直接执行无需未知的回答预算。
    """
    if phase not in ("QUERY", "DIRECT_EXECUTION", "POST_ANSWER_CONSERVATIVE"):
        raise ValueError("INVALID_TIMING_PHASE")
    unknown = {"deadline_sim_time_s": None, "remaining_sim_s": None, "time_ok": False,
               "wall_latency_s": None, "required_budget_s": None,
               "phase": phase, "reason": "TIME_EVIDENCE_UNKNOWN"}
    if not isinstance(contract, dict) or contract.get("clock") != "SIMULATION_TIME":
        return unknown
    if (contract.get("kind") not in ("CONTROLLED_PROTOCOL_DEADLINE", "PUBLIC_BOUNDARY_ESTIMATE")
            or contract.get("anchor_kind") != "PUBLIC_TASK_START"
            or not isinstance(contract.get("anchor_event_id"), str) or not contract["anchor_event_id"]
            or not valid_check(contract.get("evidence"))):
        return unknown
    keys = ("anchor_sim_time_s", "budget_s", "execution_reserve_s")
    if phase != "DIRECT_EXECUTION":
        keys += ("interaction_budget_s",)
    if not all(finite(contract.get(k)) for k in keys) or not finite(now):
        return unknown
    if any(contract[k] < 0 for k in keys[1:]) or now < contract["anchor_sim_time_s"]:
        return unknown
    deadline = contract["anchor_sim_time_s"] + contract["budget_s"]
    remaining = deadline - now
    required = contract["execution_reserve_s"]
    if phase != "DIRECT_EXECUTION":
        required += contract["interaction_budget_s"]
    if not all(finite(value) for value in (deadline, remaining, required)):
        return unknown
    return {"deadline_sim_time_s": deadline, "remaining_sim_s": remaining,
            "time_ok": remaining > required, "required_budget_s": required, "phase": phase,
            "wall_latency_s": None, "reason": "TIME_OK" if remaining > required else "NO_TIME", "kind": contract["kind"],
            "anchor_event_id": contract["anchor_event_id"]}


def common_query_failures(public, *, pending, count, service_available, now):
    """共同门没有任务关系或任务区分价值条件。"""
    failed = []
    if public_structure_errors(public):
        return ["INVALID_PUBLIC_STRUCTURE"]
    if not candidates_valid(public) or len(public.get("candidates", [])) != 2:
        failed.append("CANDIDATES_INVALID")
    elif not question_valid(public):
        failed.append("QUESTION_MAPPING_INVALID")
    checks = public.get("checks", {})
    for name in ("interaction_service", "execution_constraints"):
        if not valid_check(checks.get(name)):
            failed.append(name.upper() + "_UNKNOWN_OR_INVALID")
    if not service_available:
        failed.append("SERVICE_UNAVAILABLE")
    if pending:
        failed.append("PENDING_QUESTION")
    budget = public.get("query_budget")
    if type(budget) is not int or count >= budget:
        failed.append("QUERY_BUDGET_EXHAUSTED")
    if not deadline_result(public.get("deadline"), now)["time_ok"]:
        failed.append("TIME_UNKNOWN_OR_EXHAUSTED")
    return failed


class DurableQuestions:
    """本地真实落盘的问题回执；只代表 CPU 交互服务，不代表乘客/车辆日志。"""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.issued = {}

    def write(self, question, state):
        payload = {"question": copy.deepcopy(question), "state": copy.deepcopy(state)}
        query_id = digest(payload)
        path = self.directory / (query_id + ".json")
        # 不覆盖以前的问题回执；flush + fsync 后才允许服务读取答案。
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        receipt = {"query_id": query_id, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.issued[query_id] = receipt
        return dict(receipt)

    def verify(self, receipt):
        if not isinstance(receipt, dict) or self.issued.get(receipt.get("query_id")) != receipt:
            return False
        path = Path(receipt["path"])
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == receipt["sha256"]

    def record_answer_failure(self, receipt, failure):
        """问题已经发出后的终态另存，不覆盖问题，不重置次数或创建第二问题。"""
        path = self.directory / (receipt["query_id"]+".answer_failure.json")
        with path.open("x", encoding="utf-8") as stream:
            json.dump(failure, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class AnswerServiceError(RuntimeError):
    """服务边界内预期的文件/格式故障；不吞掉其他编程错误。"""
    def __init__(self, code, detail):
        super().__init__(code)
        self.code, self.detail = code, detail


class ControlledAnswerService:
    """隔离的受控答案服务；私有答案只能在本服务验证实际提问回执后读出。"""
    def __init__(self, answer_label, writer, available=True):
        self.__answer_label = answer_label
        self.writer = writer
        self.available = available
        self.read_count = 0

    def release(self, receipt, state):
        if not self.available or not self.writer.verify(receipt):
            raise ValueError("NO_VALID_DURABLE_QUESTION")
        if not valid_state(state):
            raise ValueError("INVALID_ANSWER_STATE")
        self.read_count += 1
        return {"label": self.__answer_label, "query_id": receipt["query_id"],
                "received_frame": state["frame"], "received_sim_time_s": state["sim_time_s"],
                "wall_latency_s": None, "service_kind": "CONTROLLED_CPU_STUB"}


class FileAnswerService(ControlledAnswerService):
    """直到有效问题落盘后才打开答案文件；预期故障用专用类型交回运行入口。"""
    def __init__(self, path, writer):
        super().__init__(None, writer, available=bool(path))
        self.path = path
        self.read_attempt_count = 0

    def release(self, receipt, state):
        try:
            verified = self.writer.verify(receipt)
        except OSError as error:
            raise AnswerServiceError("QUESTION_RECEIPT_IO_ERROR", type(error).__name__) from error
        if not verified:
            raise AnswerServiceError("NO_VALID_DURABLE_QUESTION", "问题回执不存在或摘要不匹配")
        self.read_attempt_count += 1
        try:
            value = json.loads(Path(self.path).read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise AnswerServiceError("ANSWER_FILE_MISSING", type(error).__name__) from error
        except OSError as error:
            raise AnswerServiceError("ANSWER_FILE_IO_ERROR", type(error).__name__) from error
        except (UnicodeError, json.JSONDecodeError) as error:
            raise AnswerServiceError("ANSWER_FILE_DECODE_ERROR", type(error).__name__) from error
        if (not isinstance(value, dict) or set(value) != {"answer_label"}
                or not isinstance(value["answer_label"], str) or not value["answer_label"].strip()):
            raise AnswerServiceError("ANSWER_FORMAT_INVALID", "仅接受一个非空字符串 answer_label")
        provider = ControlledAnswerService(value["answer_label"], self.writer)
        try:
            result = provider.release(receipt, state)
        except OSError as error:
            raise AnswerServiceError("QUESTION_RECEIPT_IO_ERROR", type(error).__name__) from error
        except ValueError as error:
            raise AnswerServiceError("ANSWER_RECEIPT_VALIDATION_FAILED", str(error)) from error
        self.read_count += provider.read_count
        return result


class CPURequestBackend:
    """共同后端 stub：记录 fresh replan/安装请求，不计算模型计划或安装原生路线。"""
    def __init__(self):
        self.requests = []

    def request(self, candidate, state, *, answer=None):
        if not valid_state(state):
            raise ValueError("INVALID_REPLAN_STATE")
        if answer and (state["frame"] <= answer["received_frame"]
                       or state["sim_time_s"] <= answer["received_sim_time_s"]):
            raise ValueError("FRESH_STATE_AFTER_ANSWER_REQUIRED")
        request = {"candidate_id": candidate["id"], "route_xy": copy.deepcopy(candidate["route_xy"]),
                   "state": copy.deepcopy(state), "source_observation_digest": digest(state),
                   "answer_query_id": answer["query_id"] if answer else None,
                   "request_kind": "FRESH_REPLAN_AND_ROUTE_INSTALL_REQUEST",
                   "backend_kind": "CPU_REQUEST_STUB", "native_installation": None,
                   "native_first_control_adoption": None, "computed_plan": None}
        request["request_id"] = digest(request)
        self.requests.append(request)
        return copy.deepcopy(request)

    @staticmethod
    def validate_plan_binding(request, plan):
        """只校验接口身份，测试 stub 计划的通过也不是原生计划证明。"""
        return (plan.get("request_id") == request["request_id"]
                and plan.get("source_observation_digest") == request["source_observation_digest"]
                and plan.get("source_frame") == request["state"]["frame"]
                and plan.get("route_digest") == digest(request["route_xy"]))


class ThreePolicy:
    def __init__(self, policy, public, writer, service, backend, *, enabled=False):
        if not enabled:
            raise RuntimeError("CPU_DEV_DISABLED")
        if policy not in POLICIES or (isinstance(public, dict) and set(public) - PUBLIC_KEYS):
            raise ValueError("INVALID_POLICY_OR_NONPUBLIC_INPUT")
        self.policy = policy
        self.public = copy.deepcopy(public)
        self.writer, self.service, self.backend = writer, service, backend
        self.receipt = self.answer = None
        self.query_count = 0
        self.done = False
        self.last_state = None
        self.service_failure = None

    def _wait(self, reason, **extra):
        return {"action": "WAIT", "authority": "PRESERVE_EXISTING_AUTHORITY",
                "physical_holding": False, "reason": reason, **extra}

    def _request(self, candidate_id, state, answer=None):
        errors = public_structure_errors(self.public)
        if errors:
            return self._wait("INVALID_PUBLIC_STRUCTURE", errors=errors)
        if not candidates_valid(self.public) or not valid_check(self.public.get("checks", {}).get("execution_constraints")):
            return self._wait("EXECUTION_EVIDENCE_UNKNOWN_OR_INVALID")
        timing = deadline_result(self.public.get("deadline"), state["sim_time_s"],
                                 phase="POST_ANSWER_CONSERVATIVE" if answer else "DIRECT_EXECUTION")
        if not timing["time_ok"]:
            return self._wait("ANSWER_TOO_LATE_OR_TIME_UNKNOWN" if answer else "EXECUTION_TIME_UNKNOWN_OR_EXHAUSTED", timing=timing)
        candidate = next((c for c in self.public["candidates"] if c["id"] == candidate_id), None)
        if candidate is None:
            return self._wait("CANDIDATE_INVALID")
        request = self.backend.request(candidate, state, answer=answer)
        self.done = True
        return {"action": "REQUEST_ROUTE", "request": request, "timing": timing, "authority": "PRESERVE_EXISTING_AUTHORITY"}

    def _answer_failure(self, code, detail):
        failure = self._wait(code, detail=detail, question_written=True, query_count=self.query_count,
                             question_receipt=dict(self.receipt), backend_request_count=len(self.backend.requests))
        self.service_failure = failure
        # 若日志介质本身也故障，仍输出结构化的持久化失败，不假称已保存。
        try:
            failure["durable_failure_receipt"] = self.writer.record_answer_failure(self.receipt, failure)
        except OSError as error:
            failure["durable_failure_receipt"] = None
            failure["persistence_error"] = type(error).__name__
        return copy.deepcopy(failure)

    def step(self, state):
        if self.service_failure:
            return self._wait("PENDING_QUESTION", service_failure=copy.deepcopy(self.service_failure),
                              query_count=self.query_count)
        errors = public_structure_errors(self.public)
        if errors:
            return self._wait("INVALID_PUBLIC_STRUCTURE", errors=errors)
        if not valid_state(state):
            return self._wait("INVALID_STATE")
        if self.last_state and (state["frame"] < self.last_state["frame"]
                                or state["sim_time_s"] < self.last_state["sim_time_s"]):
            return self._wait("STATE_CLOCK_REGRESSION")
        self.last_state = copy.deepcopy(state)
        if self.done:
            return self._wait("REQUEST_ALREADY_ISSUED")
        if self.answer:
            if (state["frame"] <= self.answer["received_frame"]
                    or state["sim_time_s"] <= self.answer["received_sim_time_s"]):
                return self._wait("WAIT_FOR_POST_ANSWER_STATE")
            mapping = self.public["question"]["answer_to_candidates"]
            if self.answer["label"] not in mapping:
                return self._wait("INVALID_ANSWER")
            return self._request(mapping[self.answer["label"]][0], state, self.answer)
        if self.receipt:
            # 外部异步服务可尚未返回；禁止重提问。当前受控 stub 同步返回。
            return self._wait("PENDING_QUESTION")
        ambiguity = self.public.get("ambiguity")
        if ambiguity == "CLEAR":
            return self._request(self.public.get("clear_candidate_id"), state)
        if ambiguity != "AMBIGUOUS":
            return self._wait("AMBIGUITY_UNKNOWN")
        if self.policy == "NO_CLARIFICATION":
            return self._request(self.public.get("default_candidate_id"), state)
        rel = relation(self.public)
        if self.policy == "DRIVECLARIFY_CONTROLLED":
            if rel == "TASK_EQUIVALENT":
                return self._request(self.public.get("default_candidate_id"), state)
            if rel != "TASK_CRITICAL":
                return self._wait("TASK_EVIDENCE_UNKNOWN", relation=rel)
        failed = common_query_failures(self.public, pending=bool(self.receipt), count=self.query_count,
                                       service_available=self.service.available, now=state["sim_time_s"])
        if failed:
            return self._wait("QUERY_BLOCKED", failed=failed)
        # 对两个单元素分区，完整有效的 TASK_CRITICAL 恰好意味着回答具有任务区分价值。
        self.receipt = self.writer.write(self.public["question"], state)
        self.query_count += 1
        try:
            answer = self.service.release(self.receipt, state)
        except AnswerServiceError as error:
            return self._answer_failure(error.code, error.detail)
        if (not isinstance(answer, dict) or answer.get("query_id") != self.receipt["query_id"]
                or answer.get("received_frame") != state["frame"]
                or answer.get("received_sim_time_s") != state["sim_time_s"]
                or not isinstance(answer.get("label"), str)
                or answer["label"] not in self.public["question"]["answer_to_candidates"]):
            return self._answer_failure("INVALID_ANSWER_RECEIPT_BINDING", "答案未匹配已发问题、接收状态或完整映射")
        self.answer = answer
        return {"action": "ASK", "receipt": dict(self.receipt), "relation": rel,
                "authority": "PRESERVE_EXISTING_AUTHORITY", "physical_holding": False,
                "timing": deadline_result(self.public.get("deadline"), state["sim_time_s"])}
