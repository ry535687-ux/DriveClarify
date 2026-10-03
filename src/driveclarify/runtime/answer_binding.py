"""runtime.answer binding implementation."""
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class ExecutionRequest:
    candidate_id: str
    instruction: str
    observation_id: str
    observation_frame: int
    selection_frame: int
    answer_frame: Optional[int]
    query_id: Optional[str]


class AnswerBoundExecution:
    """各策略共用的执行接口；策略只决定询问还是直接选择候选。

    指令须在看到答案之前作为所有公开候选的一部分提供。收到答案只选择
    已有候选，不临时生成目标或真值。prepare 不激活指令；原生导航安装
    成功后调用 activate，随后原生 tick/forward/PID 使用最新观测与该指令。
    """

    def __init__(self, raw_instruction: str, candidate_instructions: Mapping[str, str]):
        if not raw_instruction.strip() or len(candidate_instructions) < 2:
            raise ValueError("RAW_INSTRUCTION_AND_MULTIPLE_PUBLIC_CANDIDATES_REQUIRED")
        if any(not isinstance(k, str) or not k or not isinstance(v, str) or not v.strip()
               for k, v in candidate_instructions.items()):
            raise ValueError("INVALID_PUBLIC_CANDIDATE_INSTRUCTION")
        self.raw_instruction = raw_instruction
        self._instructions = dict(candidate_instructions)
        self.active_instruction = raw_instruction
        self._query = None
        self._selection = None
        self._active_request = None

    def begin_query(self, query_id: str, frame: int, *, durable: bool):
        if not durable or not query_id or self._query is not None or self._selection is not None:
            raise ValueError("ONE_DURABLE_QUERY_REQUIRED_BEFORE_ANSWER")
        self._query = (str(query_id), self._frame(frame))

    @staticmethod
    def _frame(frame):
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            raise ValueError("NONNEGATIVE_INTEGER_FRAME_REQUIRED")
        return frame

    def _candidate(self, candidate_id):
        if candidate_id not in self._instructions:
            raise ValueError("ANSWER_NOT_IN_PUBLIC_CANDIDATE_SET")

    def receive_answer(self, query_id: str, candidate_id: str, frame: int):
        frame = self._frame(frame)
        self._candidate(candidate_id)
        if self._query is None or query_id != self._query[0] or frame < self._query[1]:
            raise ValueError("ANSWER_QUERY_OR_FRAME_MISMATCH")
        selection = (candidate_id, frame, frame, query_id)
        if self._selection is not None and self._selection != selection:
            raise ValueError("RESOLUTION_CANNOT_CHANGE_AFTER_ANSWER")
        self._selection = selection

    def select_without_query(self, candidate_id: str, frame: int):
        self._candidate(candidate_id)
        frame = self._frame(frame)
        if self._query is not None or self._selection is not None:
            raise ValueError("DIRECT_SELECTION_REQUIRES_NO_PENDING_QUERY_OR_SELECTION")
        self._selection = (candidate_id, frame, None, None)

    def prepare(self, observation_id: str, observation_frame: int):
        frame = self._frame(observation_frame)
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError("SOURCE_OBSERVATION_ID_REQUIRED")
        if self._selection is None:
            return None
        candidate, selected_at, answered_at, query_id = self._selection
        # 同帧内“先读答案再forward”没有产生一份在答案后采集的新观测。
        if frame < selected_at or (answered_at is not None and frame <= answered_at):
            return None
        return ExecutionRequest(candidate, self._instructions[candidate], observation_id,
                                frame, selected_at, answered_at, query_id)

    def activate(self, request: ExecutionRequest, *, navigation_committed: bool,
                 current_observation_id: str, current_frame: int):
        if not navigation_committed:
            raise ValueError("NATIVE_NAVIGATION_NOT_COMMITTED")
        expected = self.prepare(current_observation_id, current_frame)
        if expected is None or request != expected:
            raise ValueError("REQUEST_IS_NOT_FOR_CURRENT_OBSERVATION_AND_RESOLUTION")
        if self._active_request is not None:
            raise ValueError("EXECUTION_ALREADY_ACTIVATED")
        self._active_request = request
        self.active_instruction = request.instruction
        return self.active_instruction

    def verify_model_input(self, *, observation_id: str, observation_frame: int,
                           instruction: str):
        """调用方应从实际模型输入取值；本函数不把期望值冒充运行证据。"""
        active = self._active_request
        frame = self._frame(observation_frame)
        if active is None:
            if instruction != self.raw_instruction:
                raise ValueError("RESOLVED_INSTRUCTION_USED_BEFORE_ACTIVATION")
            return
        if frame < active.observation_frame or not observation_id:
            raise ValueError("MODEL_REUSED_A_PREACTIVATION_OBSERVATION")
        if frame == active.observation_frame and observation_id != active.observation_id:
            raise ValueError("MODEL_OBSERVATION_ID_MISMATCH")
        if instruction != active.instruction:
            raise ValueError("MODEL_DID_NOT_RECEIVE_RESOLVED_INSTRUCTION")

