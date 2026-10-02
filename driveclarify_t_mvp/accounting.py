"""Call-entry and latency accounting that never invokes work on its own."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import time
from typing import Callable, Generic, TypeVar


class CallKind(str, Enum):
    VLA_FORWARD = "vla_forward_count"
    GLOBAL_PLANNER = "global_planner_call_count"
    LOCAL_PLANNER = "local_planner_call_count"
    CANDIDATE_GENERATION = "candidate_generation_call_count"
    ROUTE_CONVERSION = "route_conversion_count"
    ROUTE_INSTALLATION = "route_installation_count"
    ROUTE_TRANSACTION = "route_transaction_count"
    RECONNECT = "reconnect_count"


@dataclass(frozen=True)
class CallReceipt:
    call_id: str
    kind: CallKind
    purpose: str
    start_monotonic_ns: int
    end_monotonic_ns: int
    success: bool
    failure_reason: str | None

    def __post_init__(self) -> None:
        if self.end_monotonic_ns < self.start_monotonic_ns:
            raise ValueError("PLANNING_MONOTONIC_CLOCK_REGRESSION")

    @property
    def latency_s(self) -> float:
        return (self.end_monotonic_ns - self.start_monotonic_ns) / 1_000_000_000.0


T = TypeVar("T")


class PlanningAccounting:
    """Counts on function entry, including failed calls."""

    def __init__(self) -> None:
        self._counts = {kind: 0 for kind in CallKind}
        self._receipts: list[CallReceipt] = []

    @property
    def receipts(self) -> tuple[CallReceipt, ...]:
        return tuple(self._receipts)

    def count(self, kind: CallKind) -> int:
        return self._counts[kind]

    def invoke(
        self,
        kind: CallKind,
        purpose: str,
        function: Callable[..., T],
        *args: object,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        **kwargs: object,
    ) -> T:
        self._counts[kind] += 1
        ordinal = self._counts[kind]
        call_id = f"{kind.value}:{ordinal}:{purpose}"
        start = int(clock_ns())
        try:
            result = function(*args, **kwargs)
        except BaseException as error:
            end = int(clock_ns())
            self._receipts.append(
                CallReceipt(
                    call_id,
                    kind,
                    purpose,
                    start,
                    end,
                    False,
                    type(error).__name__ + ":" + str(error),
                )
            )
            raise
        end = int(clock_ns())
        self._receipts.append(
            CallReceipt(call_id, kind, purpose, start, end, True, None)
        )
        return result

    def record_vla_forward(
        self,
        function: Callable[..., T],
        *args: object,
        purpose: str = "NORMAL_CONTROL",
        **kwargs: object,
    ) -> T:
        if purpose not in {"NORMAL_CONTROL", "DIAGNOSTIC_NONCONTROL"}:
            raise ValueError("VLA_FORWARD_PURPOSE_INVALID")
        return self.invoke(CallKind.VLA_FORWARD, purpose, function, *args, **kwargs)

    def snapshot(self) -> dict[str, object]:
        values: dict[str, object] = {
            kind.value: count for kind, count in self._counts.items()
        }
        values["planning_latency_s"] = {
            "per_call": [receipt.latency_s for receipt in self._receipts],
            "active_planning_sum_s": sum(
                receipt.latency_s for receipt in self._receipts
            ),
        }
        values["calls"] = [asdict(receipt) for receipt in self._receipts]
        return values


class TransitionLatencyAccounting:
    """Update-to-milestone monotonic latency; waiting remains included."""

    _allowed = frozenset({"candidate", "decision", "install", "consumption", "terminal"})

    def __init__(self, update_monotonic_ns: int):
        self.update_monotonic_ns = int(update_monotonic_ns)
        self._milestones: dict[str, int] = {}

    def mark(self, milestone: str, monotonic_ns: int) -> None:
        if milestone not in self._allowed:
            raise ValueError("TRANSITION_LATENCY_MILESTONE_INVALID")
        timestamp = int(monotonic_ns)
        if timestamp < self.update_monotonic_ns:
            raise ValueError("TRANSITION_LATENCY_CLOCK_REGRESSION")
        if milestone in self._milestones:
            raise RuntimeError("TRANSITION_LATENCY_MILESTONE_ALREADY_RECORDED")
        self._milestones[milestone] = timestamp

    def snapshot(self) -> dict[str, float | None]:
        return {
            "update_to_" + milestone + "_s": (
                None
                if milestone not in self._milestones
                else (self._milestones[milestone] - self.update_monotonic_ns)
                / 1_000_000_000.0
            )
            for milestone in sorted(self._allowed)
        }
