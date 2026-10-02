"""纯离线评估器共享的强类型枚举与审计记录。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class StrEnum(str, Enum):
    """让枚举在 JSON/日志中稳定表现为合同规定的字符串。"""

    def __str__(self) -> str:
        return self.value


class TriValue(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class AskTimingStatus(StrEnum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE_KNOWN = "INFEASIBLE_KNOWN"
    UNKNOWN = "UNKNOWN"


class CommitReentryCause(StrEnum):
    NEW_INSTRUCTION = "NEW_INSTRUCTION"
    NEW_AMBIGUITY = "NEW_AMBIGUITY"
    MATERIAL_INVALIDATION = "MATERIAL_INVALIDATION"
    NONE = "NONE"


class Decision(StrEnum):
    ACT = "ACT"
    ASK = "ASK"
    WAIT = "WAIT"
    FALLBACK = "FALLBACK"


class PolicyState(StrEnum):
    NORMAL = "NORMAL"
    AMBIGUITY_ACTIVE = "AMBIGUITY_ACTIVE"
    QUESTION_SENT = "QUESTION_SENT"
    HOLDING = "HOLDING"
    ANSWER_RECEIVED = "ANSWER_RECEIVED"
    COMMITTED = "COMMITTED"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True)
class TimingResult:
    status: AskTimingStatus
    reason_code: str
    t_clarify_s: float | None
    slack_s: float | None
    fields_used: tuple[str, ...]
    unknown_fields: tuple[str, ...]


@dataclass(frozen=True)
class DivergenceResult:
    d_g: TriValue
    d_s: TriValue
    d_r: TriValue
    d_i: TriValue
    critical: TriValue
    equivalent: TriValue
    fields_used: tuple[str, ...]
    unknown_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "D_G": self.d_g.value,
            "D_S": self.d_s.value,
            "D_R": self.d_r.value,
            "D_I": self.d_i.value,
            "critical": self.critical.value,
            "equivalent": self.equivalent.value,
            "fields_used": list(self.fields_used),
            "unknown_fields": list(self.unknown_fields),
        }


@dataclass(frozen=True)
class CacheResult:
    executable: TriValue
    reason_code: str
    effective_status: str
    plan_age_s: float | None
    fields_used: tuple[str, ...]
    unknown_fields: tuple[str, ...]


@dataclass(frozen=True)
class QuestionCheck:
    relevant: TriValue
    non_redundant: TriValue
    answerable: TriValue
    reason_code: str
    signature: str | None
    fields_used: tuple[str, ...]
    unknown_fields: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return (
            self.relevant is TriValue.TRUE
            and self.non_redundant is TriValue.TRUE
            and self.answerable is TriValue.TRUE
        )


@dataclass(frozen=True)
class AnswerResult:
    classification: str
    accepted: bool
    reason_code: str
    selected_candidate_id: str | None
    opens_new_episode: bool
    oracle_mismatch: bool
    fields_used: tuple[str, ...]
    unknown_fields: tuple[str, ...]


@dataclass(frozen=True)
class HoldingSummary:
    """把语义 ASK/WAIT 与已验证 holding 的具体身份绑定。"""

    holding_id: str
    holding_type: str
    version: str
    validity_status: str
    deadline_status: str
    valid_until_monotonic_s: float
    hard_feasible: bool
    can_extend_or_preserve: bool
    provenance: str


@dataclass
class DecisionRecord:
    """一次语义决策的完整、机器可读审计输出。"""

    decision: Decision
    reason_code: str
    current_state: PolicyState
    next_state: PolicyState
    fields_used: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    cache_status: str = "NOT_APPLICABLE"
    timing_status: AskTimingStatus = AskTimingStatus.UNKNOWN
    question_dispatch: str = "NONE"
    holding_required: bool = False
    holding_summary: HoldingSummary | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    selected_candidate_id: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """在输出边界强制 ASK/WAIT 与 holding 的双向不变量。"""

        uses_holding = self.decision in {Decision.ASK, Decision.WAIT}
        if uses_holding and (
            self.holding_required is not True or self.holding_summary is None
        ):
            raise ValueError("ASK_WAIT_REQUIRES_STRUCTURED_HOLDING")
        if not uses_holding and (
            self.holding_required is not False or self.holding_summary is not None
        ):
            raise ValueError("ACT_FALLBACK_MUST_NOT_BIND_HOLDING")

    def to_dict(self) -> dict[str, Any]:
        # dataclasses.asdict 不会自动把 Enum 转成值，因此在边界处显式转换。
        data = asdict(self)
        data["decision"] = self.decision.value
        data["current_state"] = self.current_state.value
        data["next_state"] = self.next_state.value
        data["timing_status"] = self.timing_status.value
        return data


def trace_step(index: int, gate: str, outcome: str, reason_code: str) -> dict[str, Any]:
    """构造确定性 gate 轨迹；index 最终由统一输出函数连续编号。"""

    return {
        "index": index,
        "gate": gate,
        "outcome": outcome,
        "reason_code": reason_code,
    }
