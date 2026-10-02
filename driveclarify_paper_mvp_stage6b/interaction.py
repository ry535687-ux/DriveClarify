"""Label-free ASK and shared WAIT/HOLD runtime mechanisms."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .contracts import LabelFirewallCounters, Stage6BContractError
from .semantics import CanonicalInterpretation


ASK_DELAY_SECONDS = 0.1
ASK_DEADLINE_SECONDS = 5.0
WAIT_REEVALUATION_SECONDS = 0.1
WAIT_DEFAULT_DEADLINE_SECONDS = 2.5

_FORBIDDEN_PRIVATE_KEYS = frozenset(
    {
        "expected_decision",
        "gold_decision",
        "gold_candidate_id",
        "gold_candidate_index",
        "selected_candidate_id",
        "oracle_selected_candidate",
        "policy_action",
    }
)


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Stage6BContractError(field + "_FINITE_REQUIRED")
    result = float(value)
    if not math.isfinite(result):
        raise Stage6BContractError(field + "_FINITE_REQUIRED")
    return result


def _assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        overlap = {str(key).casefold() for key in value}.intersection(
            _FORBIDDEN_PRIVATE_KEYS
        )
        if overlap:
            raise Stage6BContractError(
                "PASSENGER_TRUTH_CONTAINS_POLICY_LABEL:" + ",".join(sorted(overlap))
            )
        for item in value.values():
            _assert_no_forbidden_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_forbidden_keys(item)


@dataclass(frozen=True)
class StructuredClarificationQuery:
    query_id: str
    question_text: str
    option_descriptions: tuple[str, str]
    target_semantic_fields: tuple[str, ...]
    issued_simulation_time: float
    answer_deadline_simulation_time: float

    def __post_init__(self) -> None:
        if not self.query_id or not self.question_text.strip():
            raise Stage6BContractError("QUERY_ID_AND_TEXT_REQUIRED")
        if len(set(item.strip().casefold() for item in self.option_descriptions)) != 2:
            raise Stage6BContractError("QUERY_OPTIONS_MUST_BE_DISTINCT")
        issued = _finite(self.issued_simulation_time, "QUERY_ISSUED_TIME")
        deadline = _finite(self.answer_deadline_simulation_time, "QUERY_DEADLINE")
        if deadline <= issued:
            raise Stage6BContractError("QUERY_DEADLINE_MUST_FOLLOW_ISSUANCE")

    @classmethod
    def from_candidates(
        cls,
        *,
        episode_id: str,
        candidates: Sequence[CanonicalInterpretation],
        simulation_time: float,
        answer_deadline_seconds: float = ASK_DEADLINE_SECONDS,
    ) -> "StructuredClarificationQuery":
        if len(candidates) != 2:
            raise Stage6BContractError("BINARY_QUERY_REQUIRES_EFFECTIVE_K2")
        differences = tuple(
            field
            for field in candidates[0].semantic_projection()
            if candidates[0].semantic_projection()[field]
            != candidates[1].semantic_projection()[field]
        )
        if not differences:
            raise Stage6BContractError("QUERY_REQUIRES_SEMANTIC_DIVERGENCE")
        descriptions = (candidates[0].description, candidates[1].description)
        query_id = "query-" + _canonical_sha256(
            {
                "episode_id": episode_id,
                "options": descriptions,
                "fields": differences,
            }
        )[:20]
        return cls(
            query_id=query_id,
            question_text=(
                "Do you mean " + descriptions[0] + " or " + descriptions[1] + "?"
            ),
            option_descriptions=descriptions,
            target_semantic_fields=differences,
            issued_simulation_time=float(simulation_time),
            answer_deadline_simulation_time=(
                float(simulation_time) + float(answer_deadline_seconds)
            ),
        )


@dataclass(frozen=True)
class PassengerIntentTruth:
    runtime_fixture_id: str
    seed: int
    intent_description: str
    natural_language_answer: str
    source: str = "CONTROLLED_BENCHMARK_PASSENGER_INTENT"

    def __post_init__(self) -> None:
        if not self.runtime_fixture_id.startswith("dc-runtime-"):
            raise Stage6BContractError("PASSENGER_RUNTIME_FIXTURE_ID_INVALID")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise Stage6BContractError("PASSENGER_SEED_INTEGER_REQUIRED")
        if not self.intent_description.strip() or not self.natural_language_answer.strip():
            raise Stage6BContractError("PASSENGER_INTENT_AND_ANSWER_REQUIRED")
        lowered = self.natural_language_answer.casefold()
        forbidden_phrases = (
            "candidate a",
            "candidate b",
            "choose candidate",
            "ask was correct",
            "act now",
            "wait was correct",
        )
        if any(value in lowered for value in forbidden_phrases):
            raise Stage6BContractError("PASSENGER_ANSWER_POLICY_LABEL_FORBIDDEN")


class ControlledPassengerIntentProvider:
    """Environment-side provider; only answer text crosses to policy runtime."""

    def __init__(
        self,
        records: Sequence[PassengerIntentTruth],
        *,
        runtime_fixture_id: str,
        seed: int,
        firewall: LabelFirewallCounters,
    ) -> None:
        matches = [
            item
            for item in records
            if item.runtime_fixture_id == runtime_fixture_id and item.seed == seed
        ]
        if len(matches) != 1:
            raise Stage6BContractError("PASSENGER_INTENT_BINDING_COUNT_NOT_ONE")
        self._truth = matches[0]
        self._firewall = firewall

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        runtime_fixture_id: str,
        seed: int,
        firewall: LabelFirewallCounters,
    ) -> "ControlledPassengerIntentProvider":
        value = json.loads(path.read_text(encoding="utf-8"))
        _assert_no_forbidden_keys(value)
        if not isinstance(value, Mapping) or not isinstance(value.get("records"), list):
            raise Stage6BContractError("PASSENGER_INTENT_CONTRACT_INVALID")
        records = tuple(PassengerIntentTruth(**item) for item in value["records"])
        return cls(
            records,
            runtime_fixture_id=runtime_fixture_id,
            seed=seed,
            firewall=firewall,
        )

    def answer(self, query: StructuredClarificationQuery) -> str:
        # The provider reads environment truth once.  No method decision or
        # candidate index exists in this object or return value.
        del query
        self._firewall.environment_passenger_intent_reads += 1
        self._firewall.assert_runtime_clean()
        return self._truth.natural_language_answer


@dataclass(frozen=True)
class AnswerDelivery:
    query_id: str
    answer_text: str
    query_start_simulation_time: float
    answer_simulation_time: float
    answer_delay_seconds: float
    source: str = "CONTROLLED_PASSENGER_ANSWER_CHANNEL"


class DelayedPassengerAnswerChannel:
    """One-query simulation-time channel with no same-tick answer path."""

    def __init__(
        self,
        provider: ControlledPassengerIntentProvider,
        *,
        delay_seconds: float = ASK_DELAY_SECONDS,
    ) -> None:
        self.provider = provider
        self.delay_seconds = _finite(delay_seconds, "ASK_DELAY")
        if self.delay_seconds <= 0.0:
            raise Stage6BContractError("ASK_DELAY_MUST_BE_POSITIVE")
        self._query: StructuredClarificationQuery | None = None
        self._answer_text: str | None = None
        self._delivered = False

    @property
    def active(self) -> bool:
        return self._query is not None and not self._delivered

    def request(self, query: StructuredClarificationQuery) -> None:
        if self._query is not None:
            raise Stage6BContractError("AT_MOST_ONE_QUERY_PER_EPISODE")
        self._query = query
        self._answer_text = self.provider.answer(query)

    def poll(self, simulation_time: float) -> AnswerDelivery | None:
        now = _finite(simulation_time, "ANSWER_POLL_TIME")
        query = self._query
        if query is None or self._delivered:
            return None
        if now > query.answer_deadline_simulation_time:
            self._delivered = True
            return None
        due = query.issued_simulation_time + self.delay_seconds
        if now + 1e-12 < due:
            return None
        if now <= query.issued_simulation_time:
            raise Stage6BContractError("SAME_TICK_ANSWER_FORBIDDEN")
        self._delivered = True
        assert self._answer_text is not None
        return AnswerDelivery(
            query_id=query.query_id,
            answer_text=self._answer_text,
            query_start_simulation_time=query.issued_simulation_time,
            answer_simulation_time=now,
            answer_delay_seconds=now - query.issued_simulation_time,
        )


@dataclass(frozen=True)
class InformationDelivery:
    payload_id: str
    information_text: str
    delivery_simulation_time: float
    signal_source: str
    content_sha256: str


class BlackboardInformationChannel:
    """Read only ScenarioRunner's delivered EV04 payload; never read its oracle."""

    def __init__(
        self,
        *,
        runtime_fixture_id: str,
        information_expected: bool,
        reader: Callable[[str], Any] | None = None,
    ) -> None:
        suffix = runtime_fixture_id.replace("-", "_")
        self.blackboard_key = "DriveClarifyRuntimeSignal_" + suffix
        self.information_expected = bool(information_expected)
        self._reader = reader
        self._seen_sha256: set[str] = set()

    def _read(self) -> Any:
        if self._reader is not None:
            return self._reader(self.blackboard_key)
        try:
            import py_trees

            return py_trees.blackboard.Blackboard().get(self.blackboard_key)
        except Exception:
            return None

    def poll(self) -> InformationDelivery | None:
        value = self._read()
        if not isinstance(value, Mapping) or value.get("delivered") is not True:
            return None
        allowed = {
            "delivered",
            "delivery_simulation_time",
            "payload_id",
            "runtime_payload",
            "signal_source",
        }
        if set(value) != allowed:
            raise Stage6BContractError("RUNTIME_INFORMATION_PAYLOAD_SCHEMA_INVALID")
        content = {
            "payload_id": value["payload_id"],
            "runtime_payload": value["runtime_payload"],
            "signal_source": value["signal_source"],
        }
        digest = _canonical_sha256(content)
        if digest in self._seen_sha256:
            return None
        self._seen_sha256.add(digest)
        return InformationDelivery(
            payload_id=str(value["payload_id"]),
            information_text=str(value["runtime_payload"]),
            delivery_simulation_time=_finite(
                value["delivery_simulation_time"], "INFORMATION_DELIVERY_TIME"
            ),
            signal_source=str(value["signal_source"]),
            content_sha256=digest,
        )


@dataclass
class WaitLifecycle:
    """Shared HOLD lifecycle used unchanged by DriveClarify and always_wait."""

    maximum_duration_seconds: float = WAIT_DEFAULT_DEADLINE_SECONDS
    start_simulation_time: float | None = None
    end_simulation_time: float | None = None
    start_position_xy: tuple[float, float] | None = None
    end_position_xy: tuple[float, float] | None = None
    exit_reason: str | None = None
    timed_out: bool = False
    information_changed: bool = False

    def start(
        self,
        simulation_time: float,
        position_xy: tuple[float, float] | None,
    ) -> None:
        if self.start_simulation_time is not None:
            return
        self.start_simulation_time = _finite(simulation_time, "WAIT_START_TIME")
        self.start_position_xy = position_xy

    def observe(
        self,
        simulation_time: float,
        position_xy: tuple[float, float] | None,
        information: InformationDelivery | AnswerDelivery | None,
    ) -> str:
        if self.start_simulation_time is None:
            raise Stage6BContractError("WAIT_MUST_START_BEFORE_OBSERVE")
        now = _finite(simulation_time, "WAIT_OBSERVE_TIME")
        if self.end_simulation_time is not None:
            return str(self.exit_reason)
        if information is not None:
            self.information_changed = True
            self.end_simulation_time = now
            self.end_position_xy = position_xy
            self.exit_reason = "MEANINGFUL_INFORMATION_ARRIVED"
            return self.exit_reason
        if now - self.start_simulation_time >= self.maximum_duration_seconds:
            self.timed_out = True
            self.end_simulation_time = now
            self.end_position_xy = position_xy
            self.exit_reason = "WAIT_TIMEOUT"
            return self.exit_reason
        return "WAIT_HOLDING"

    @property
    def duration_seconds(self) -> float | None:
        if self.start_simulation_time is None or self.end_simulation_time is None:
            return None
        return self.end_simulation_time - self.start_simulation_time

    @property
    def distance_m(self) -> float | None:
        if self.start_position_xy is None or self.end_position_xy is None:
            return None
        return math.hypot(
            self.end_position_xy[0] - self.start_position_xy[0],
            self.end_position_xy[1] - self.start_position_xy[1],
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.update({
            "duration_seconds": self.duration_seconds,
            "distance_m": self.distance_m,
            "holding_behavior": "MAINTAIN_CURRENT_VALID_CLOSED_LOOP_BEHAVIOR",
            "low_level_controller_owner": "EXISTING_BASELINE_PID",
            "emergency_stop_semantics": False,
        })
        return value


__all__ = [
    "ASK_DEADLINE_SECONDS",
    "ASK_DELAY_SECONDS",
    "AnswerDelivery",
    "BlackboardInformationChannel",
    "ControlledPassengerIntentProvider",
    "DelayedPassengerAnswerChannel",
    "InformationDelivery",
    "PassengerIntentTruth",
    "StructuredClarificationQuery",
    "WAIT_DEFAULT_DEADLINE_SECONDS",
    "WAIT_REEVALUATION_SECONDS",
    "WaitLifecycle",
]
