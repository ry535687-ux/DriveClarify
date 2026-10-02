"""Prospective event-driven T1-T4 injection infrastructure (no simulator runner)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time

from .canonical import canonical_sha256


class TimingBucket(str, Enum):
    T1_BEFORE_COMMITMENT = "T1_BEFORE_COMMITMENT"
    T2_NEAR_COMMITMENT = "T2_NEAR_COMMITMENT"
    T3_POST_COMMIT_RECOVERABLE = "T3_POST_COMMIT_RECOVERABLE"
    T4_NO_SAFE_CURRENT_OPPORTUNITY = "T4_NO_SAFE_CURRENT_OPPORTUNITY"


EXPECTED_ORACLE_EVENTS = {
    TimingBucket.T1_BEFORE_COMMITMENT: "EARLY_COMMON_DUAL_FEASIBLE_REGION_ENTERED",
    TimingBucket.T2_NEAR_COMMITMENT: "FINAL_COMMON_DUAL_FEASIBLE_REGION_ENTERED",
    TimingBucket.T3_POST_COMMIT_RECOVERABLE: "OLD_EXCLUSIVE_RECOVERY_EXISTS_ENTERED",
    TimingBucket.T4_NO_SAFE_CURRENT_OPPORTUNITY: "CURRENT_OPPORTUNITY_END_PASSED",
}


@dataclass(frozen=True)
class ProspectiveInjectionEvent:
    episode_id: str
    case_id: str
    bucket: TimingBucket
    injection_event_id: str
    expected_oracle_event: str
    source_scenario_version: str
    source_scenario_sha256: str
    instruction_old: str
    instruction_new: str
    update_event_id: str

    def __post_init__(self) -> None:
        if self.expected_oracle_event != EXPECTED_ORACLE_EVENTS[self.bucket]:
            raise ValueError("INJECTION_ORACLE_EVENT_BUCKET_MISMATCH")
        for value in (
            self.episode_id,
            self.case_id,
            self.injection_event_id,
            self.source_scenario_version,
            self.source_scenario_sha256,
            self.update_event_id,
        ):
            if not str(value).strip():
                raise ValueError("INJECTION_IDENTITY_INVALID")


@dataclass(frozen=True)
class InjectionReceipt:
    episode_id: str
    case_id: str
    bucket: str
    injection_event_id: str
    expected_oracle_event: str
    actual_injection_frame: int
    simulation_timestamp_s: float
    monotonic_timestamp_ns: int
    source_scenario_version: str
    source_scenario_sha256: str
    update_event_id: str
    injected_before_policy: bool
    canonical_sha256: str


@dataclass(frozen=True)
class RuntimeSemanticUpdate:
    """Only this oracle-free object crosses into a baseline namespace."""

    episode_id: str
    case_id: str
    update_event_id: str
    instruction_old: str
    instruction_new: str
    actual_injection_frame: int
    simulation_timestamp_s: float


class ProspectiveT1T4Injector:
    """One-shot topology-event matcher with an append-only evaluator receipt."""

    def __init__(self, event: ProspectiveInjectionEvent):
        self._event = event
        self._receipts: list[InjectionReceipt] = []

    @property
    def receipts(self) -> tuple[InjectionReceipt, ...]:
        return tuple(self._receipts)

    def inject(
        self,
        *,
        observed_oracle_event: str,
        actual_frame: int,
        simulation_timestamp_s: float,
        before_policy: bool,
        monotonic_timestamp_ns: int | None = None,
    ) -> tuple[InjectionReceipt, RuntimeSemanticUpdate]:
        if self._receipts:
            raise RuntimeError("DUPLICATE_UPDATE_INJECTION")
        if observed_oracle_event != self._event.expected_oracle_event:
            raise RuntimeError("INJECTION_TOPOLOGY_EVENT_MISMATCH")
        if before_policy is not True:
            raise RuntimeError("PROTOCOL_INVALID_UPDATE_INJECTION_LATE")
        if actual_frame < 0:
            raise ValueError("INJECTION_FRAME_INVALID")
        mono = time.monotonic_ns() if monotonic_timestamp_ns is None else int(
            monotonic_timestamp_ns
        )
        payload = {
            "episode_id": self._event.episode_id,
            "case_id": self._event.case_id,
            "bucket": self._event.bucket.value,
            "injection_event_id": self._event.injection_event_id,
            "expected_oracle_event": self._event.expected_oracle_event,
            "actual_injection_frame": actual_frame,
            "simulation_timestamp_s": simulation_timestamp_s,
            "monotonic_timestamp_ns": mono,
            "source_scenario_version": self._event.source_scenario_version,
            "source_scenario_sha256": self._event.source_scenario_sha256,
            "update_event_id": self._event.update_event_id,
            "injected_before_policy": True,
        }
        receipt = InjectionReceipt(
            canonical_sha256=canonical_sha256(payload),
            **payload,
        )
        self._receipts.append(receipt)
        runtime = RuntimeSemanticUpdate(
            episode_id=self._event.episode_id,
            case_id=self._event.case_id,
            update_event_id=self._event.update_event_id,
            instruction_old=self._event.instruction_old,
            instruction_new=self._event.instruction_new,
            actual_injection_frame=actual_frame,
            simulation_timestamp_s=simulation_timestamp_s,
        )
        return receipt, runtime
