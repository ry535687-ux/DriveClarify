"""Unified append-only transition event stream and final receipt index."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .canonical import bytes_sha256, canonical_bytes, canonical_sha256
from .models import UnknownValue
from .firewall import BASELINE_ALLOWED_FIELDS, BaselineId, assert_no_oracle_fields


EVENT_SCHEMA = "driveclarify.rq2.transition_event.v1"
RECEIPT_SCHEMA = "driveclarify.rq2.transition_receipt.v1"


class EventType(str, Enum):
    EPISODE_SETUP = "EPISODE_SETUP"
    UPDATE_INJECTED = "UPDATE_INJECTED"
    UPDATE_RECEIVED = "UPDATE_RECEIVED"
    P_OLD_FROZEN = "P_OLD_FROZEN"
    OBSERVABLE_COMMITMENT_CAPTURED = "OBSERVABLE_COMMITMENT_CAPTURED"
    ORACLE_TIMING_LABELED = "ORACLE_TIMING_LABELED"
    CANDIDATE_PREPARATION_STARTED = "CANDIDATE_PREPARATION_STARTED"
    CANDIDATE_PREPARATION_TERMINAL = "CANDIDATE_PREPARATION_TERMINAL"
    TRANSITION_DECIDED = "TRANSITION_DECIDED"
    DEFER_REEVALUATED = "DEFER_REEVALUATED"
    ROUTE_INSTALL_REQUESTED = "ROUTE_INSTALL_REQUESTED"
    ROUTE_INSTALL_TERMINAL = "ROUTE_INSTALL_TERMINAL"
    NEXT_LEGITIMATE_PLANNER_CONSUMPTION = "NEXT_LEGITIMATE_PLANNER_CONSUMPTION"
    LOCAL_MANEUVER_TERMINAL = "LOCAL_MANEUVER_TERMINAL"
    RECONNECT_REQUESTED = "RECONNECT_REQUESTED"
    RECONNECT_TERMINAL = "RECONNECT_TERMINAL"
    FRESH_GLOBAL_ROUTE_CONSUMED = "FRESH_GLOBAL_ROUTE_CONSUMED"
    EPISODE_TERMINAL = "EPISODE_TERMINAL"


@dataclass(frozen=True)
class SourceIdentity:
    owner_id: str
    owner_generation: int | None
    source_frame: int | None
    source_clock: str

    def __post_init__(self) -> None:
        if not self.owner_id or not self.source_clock:
            raise ValueError("TRANSITION_SOURCE_IDENTITY_INCOMPLETE")
        if self.owner_generation is not None and self.owner_generation < 0:
            raise ValueError("TRANSITION_SOURCE_GENERATION_INVALID")
        if self.source_frame is not None and self.source_frame < 0:
            raise ValueError("TRANSITION_SOURCE_FRAME_INVALID")


@dataclass(frozen=True)
class TransitionEvent:
    schema_version: str
    event_id: str
    event_sequence: int
    event_type: str
    episode_id: str
    attempt_id: str
    case_id: str
    scene_id: str
    seed: int
    baseline_id: str
    update_event_id: str | None
    sim_frame: int | None
    sim_time_s: float | None
    monotonic_ns: int | None
    wall_time_utc: str | None
    source_object: SourceIdentity
    object_identity: str | None
    object_sha256: str | None
    route_identity: str | None
    route_generation: int | None
    global_task_identity_G: str | None
    p_old_sha256: str | None
    p_new_sha256: str | None
    reason_codes: tuple[str, ...]
    evidence_grade: str
    availability: str
    unknown: UnknownValue | None
    payload: Mapping[str, Any]


class TransitionEventStream:
    """In-memory append-only source of truth with write-once serialization."""

    def __init__(
        self,
        *,
        episode_id: str,
        attempt_id: str,
        case_id: str,
        scene_id: str,
        seed: int,
        baseline_id: str,
    ) -> None:
        for identity in (episode_id, attempt_id, case_id, scene_id):
            if not str(identity).strip():
                raise ValueError("TRANSITION_STREAM_IDENTITY_INVALID")
        BaselineId(baseline_id)
        self.episode_id = episode_id
        self.attempt_id = attempt_id
        self.case_id = case_id
        self.scene_id = scene_id
        self.seed = seed
        self.baseline_id = baseline_id
        self._events: list[TransitionEvent] = []
        self._sealed = False

    @property
    def events(self) -> tuple[TransitionEvent, ...]:
        return tuple(self._events)

    def append(
        self,
        event_type: EventType | str,
        *,
        source_object: SourceIdentity,
        reason_codes: tuple[str, ...],
        evidence_grade: str,
        payload: Mapping[str, Any],
        update_event_id: str | None = None,
        sim_frame: int | None = None,
        sim_time_s: float | None = None,
        monotonic_ns: int | None = None,
        wall_time_utc: str | None = None,
        object_identity: str | None = None,
        object_sha256: str | None = None,
        route_identity: str | None = None,
        route_generation: int | None = None,
        global_task_identity_G: str | None = None,
        p_old_sha256: str | None = None,
        p_new_sha256: str | None = None,
        availability: str = "AVAILABLE",
        unknown: UnknownValue | None = None,
    ) -> TransitionEvent:
        if self._sealed:
            raise RuntimeError("TRANSITION_EVENT_STREAM_SEALED")
        if not reason_codes:
            raise ValueError("TRANSITION_EVENT_REASON_REQUIRED")
        if evidence_grade not in {"runtime", "evaluator", "derived", "engineering"}:
            raise ValueError("TRANSITION_EVENT_EVIDENCE_GRADE_INVALID")
        if availability not in {
            "AVAILABLE",
            "UNKNOWN",
            "NOT_APPLICABLE_BY_CONTRACT",
        }:
            raise ValueError("TRANSITION_EVENT_AVAILABILITY_INVALID")
        if availability == "UNKNOWN" and unknown is None:
            raise ValueError("TRANSITION_EVENT_UNKNOWN_PROVENANCE_REQUIRED")
        if availability != "UNKNOWN" and unknown is not None:
            raise ValueError("TRANSITION_EVENT_UNKNOWN_PROVENANCE_UNEXPECTED")
        sequence = len(self._events)
        event_name = EventType(event_type).value
        if availability == "AVAILABLE":
            _validate_available_event_fields(
                event_name,
                baseline_id=self.baseline_id,
                update_event_id=update_event_id,
                sim_frame=sim_frame,
                sim_time_s=sim_time_s,
                route_identity=route_identity,
                route_generation=route_generation,
                global_task_identity_G=global_task_identity_G,
                p_old_sha256=p_old_sha256,
                p_new_sha256=p_new_sha256,
                payload=payload,
            )
        identity_seed = {
            "attempt_id": self.attempt_id,
            "event_sequence": sequence,
            "event_type": event_name,
            "object_identity": object_identity,
            "object_sha256": object_sha256,
        }
        event = TransitionEvent(
            schema_version=EVENT_SCHEMA,
            event_id="tmvp-event-" + canonical_sha256(identity_seed)[:24],
            event_sequence=sequence,
            event_type=event_name,
            episode_id=self.episode_id,
            attempt_id=self.attempt_id,
            case_id=self.case_id,
            scene_id=self.scene_id,
            seed=self.seed,
            baseline_id=self.baseline_id,
            update_event_id=update_event_id,
            sim_frame=sim_frame,
            sim_time_s=sim_time_s,
            monotonic_ns=monotonic_ns,
            wall_time_utc=wall_time_utc,
            source_object=source_object,
            object_identity=object_identity,
            object_sha256=object_sha256,
            route_identity=route_identity,
            route_generation=route_generation,
            global_task_identity_G=global_task_identity_G,
            p_old_sha256=p_old_sha256,
            p_new_sha256=p_new_sha256,
            reason_codes=reason_codes,
            evidence_grade=evidence_grade,
            availability=availability,
            unknown=unknown,
            payload=dict(payload),
        )
        self._events.append(event)
        return event

    def seal(self) -> None:
        self._sealed = True

    @property
    def canonical_jsonl_bytes(self) -> bytes:
        return b"".join(canonical_bytes(event) + b"\n" for event in self._events)

    @property
    def canonical_sha256(self) -> str:
        return bytes_sha256(self.canonical_jsonl_bytes)

    def write_once(self, path: str | Path) -> None:
        """Create, never overwrite, one immutable JSONL stream."""

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(Path(path), flags, 0o644)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(self.canonical_jsonl_bytes)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)


def _sequences(events: tuple[TransitionEvent, ...], event_type: EventType) -> list[int]:
    return [event.event_sequence for event in events if event.event_type == event_type.value]


class TransitionReceiptFinalizer:
    """Validate static event linkage and build an immutable episode index."""

    def finalize(
        self,
        stream: TransitionEventStream,
        *,
        method_freeze: Mapping[str, Any],
        planning_accounting: Mapping[str, Any],
        metric_results: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        events = stream.events
        if not events:
            raise RuntimeError("TRANSITION_EVENT_STREAM_EMPTY")
        if [event.event_sequence for event in events] != list(range(len(events))):
            raise RuntimeError("TRANSITION_EVENT_SEQUENCE_INVALID")
        update = _sequences(events, EventType.UPDATE_RECEIVED)
        setups = _sequences(events, EventType.EPISODE_SETUP)
        if len(setups) != 1:
            raise RuntimeError("EPISODE_SETUP_EXACTLY_ONCE_REQUIRED")
        setup_payload = events[setups[0]].payload
        baseline = BaselineId(stream.baseline_id)
        visible_fields = tuple(sorted(BASELINE_ALLOWED_FIELDS[baseline]))
        visible_manifest_sha256 = canonical_sha256(
            {"baseline_id": baseline.value, "visible_fields": visible_fields}
        )
        if (
            setup_payload.get("firewall_verified") is not True
            or tuple(setup_payload.get("baseline_visible_fields", ())) != visible_fields
            or setup_payload.get("visible_field_manifest_sha256")
            != visible_manifest_sha256
        ):
            raise RuntimeError("BASELINE_VISIBLE_FIELD_FIREWALL_NOT_PROVEN")
        evaluator_only_types = {
            EventType.UPDATE_INJECTED.value,
            EventType.ORACLE_TIMING_LABELED.value,
        }
        for event in events:
            if event.event_type in evaluator_only_types:
                if event.evidence_grade != "evaluator":
                    raise RuntimeError("EVALUATOR_ONLY_EVENT_GRADE_INVALID")
            elif event.event_type == EventType.OBSERVABLE_COMMITMENT_CAPTURED.value:
                observable_payload = dict(event.payload)
                if observable_payload.pop("oracle_fields_present", None) is not False:
                    raise RuntimeError("OBSERVABLE_COMMITMENT_ORACLE_ASSERTION_INVALID")
                assert_no_oracle_fields(observable_payload)
            else:
                assert_no_oracle_fields(event.payload)
        _validate_metric_results(metric_results)
        p_old = _sequences(events, EventType.P_OLD_FROZEN)
        candidate_start = _sequences(events, EventType.CANDIDATE_PREPARATION_STARTED)
        candidate_terminal = _sequences(events, EventType.CANDIDATE_PREPARATION_TERMINAL)
        if len(update) != 1:
            raise RuntimeError("UPDATE_RECEIVED_EXACTLY_ONCE_REQUIRED")
        if len(p_old) != 1 or p_old[0] <= update[0]:
            raise RuntimeError("P_OLD_FREEZE_ORDER_INVALID")
        update_conditioned = (
            p_old
            + candidate_start
            + candidate_terminal
            + _sequences(events, EventType.TRANSITION_DECIDED)
            + _sequences(events, EventType.ROUTE_INSTALL_REQUESTED)
            + _sequences(events, EventType.ROUTE_INSTALL_TERMINAL)
        )
        if any(sequence <= update[0] for sequence in update_conditioned):
            raise RuntimeError("UPDATE_NOT_RECEIVED_BEFORE_UPDATE_CONDITIONED_CALL")
        if candidate_start and p_old[0] >= candidate_start[0]:
            raise RuntimeError("P_OLD_NOT_FROZEN_BEFORE_CANDIDATE")
        if len(candidate_start) != len(candidate_terminal):
            raise RuntimeError("CANDIDATE_PREPARATION_START_TERMINAL_COUNT_MISMATCH")
        if any(start >= terminal for start, terminal in zip(candidate_start, candidate_terminal)):
            raise RuntimeError("CANDIDATE_PREPARATION_TERMINAL_ORDER_INVALID")
        for sequence in candidate_terminal:
            event = events[sequence]
            if event.payload.get("authority_unchanged") is not True:
                raise RuntimeError("CANDIDATE_ISOLATION_NOT_PROVEN")
            if event.payload.get("installation_state") != "UNINSTALLED":
                raise RuntimeError("P_NEW_NOT_DETACHED_AT_PREPARATION_TERMINAL")
        installs = _sequences(events, EventType.ROUTE_INSTALL_TERMINAL)
        decisions = _sequences(events, EventType.TRANSITION_DECIDED)
        install_requests = _sequences(events, EventType.ROUTE_INSTALL_REQUESTED)
        if len(installs) > 1 or len(install_requests) != len(installs):
            raise RuntimeError("ROUTE_INSTALL_ARM_COUNT_OR_PAIRING_INVALID")
        for install in installs:
            earlier_requests = [request for request in install_requests if request < install]
            if not earlier_requests:
                raise RuntimeError("INSTALL_TERMINAL_WITHOUT_PRIOR_REQUEST")
            earlier_decisions = [decision for decision in decisions if decision < install]
            if not earlier_decisions:
                raise RuntimeError("INSTALL_WITHOUT_PRIOR_ADMISSION_DECISION")
            decision_event = events[earlier_decisions[-1]]
            install_event = events[install]
            if decision_event.payload.get("candidate_state") != "CANDIDATE_ADMITTED":
                raise RuntimeError("INSTALL_WITHOUT_CANDIDATE_ADMISSION")
            admitted_hash = decision_event.payload.get("admitted_candidate_sha256")
            installed_hash = install_event.payload.get("installed_candidate_sha256")
            if not admitted_hash or admitted_hash != installed_hash:
                raise RuntimeError("INSTALLED_CANDIDATE_HASH_NOT_ADMITTED")
            if installed_hash != install_event.p_new_sha256:
                raise RuntimeError("INSTALLED_CANDIDATE_HASH_LINK_INVALID")
            if events[earlier_requests[-1]].p_new_sha256 != installed_hash:
                raise RuntimeError("INSTALL_REQUEST_CANDIDATE_HASH_LINK_INVALID")
        consumptions = _sequences(events, EventType.NEXT_LEGITIMATE_PLANNER_CONSUMPTION)
        for consumption in consumptions:
            earlier_installs = [install for install in installs if install < consumption]
            if not earlier_installs:
                raise RuntimeError("CONSUMPTION_WITHOUT_PRIOR_INSTALL")
            consume_event = events[consumption]
            install_event = events[earlier_installs[-1]]
            if (
                consume_event.route_identity != install_event.route_identity
                or consume_event.route_generation != install_event.route_generation
                or consume_event.p_new_sha256 != install_event.p_new_sha256
            ):
                raise RuntimeError("INSTALL_CONSUMPTION_IDENTITY_MISMATCH")
            if consume_event.sim_frame is None or install_event.sim_frame is None:
                raise RuntimeError("INSTALL_CONSUMPTION_FRAME_MISSING")
            if consume_event.sim_frame <= install_event.sim_frame:
                raise RuntimeError("CONSUMPTION_NOT_NEXT_LATER_CYCLE")
            if (
                consume_event.payload.get("normal_cycle") is not True
                or consume_event.payload.get("active_equals_installed") is not True
                or consume_event.payload.get("consumed_equals_installed") is not True
                or not consume_event.payload.get("vla_forward_id")
            ):
                raise RuntimeError("NEXT_LEGITIMATE_CONSUMPTION_PROOF_INCOMPLETE")
        reconnect_terminals = _sequences(events, EventType.RECONNECT_TERMINAL)
        fresh_consumptions = _sequences(events, EventType.FRESH_GLOBAL_ROUTE_CONSUMED)
        for consumption in fresh_consumptions:
            earlier_reconnects = [
                terminal for terminal in reconnect_terminals if terminal < consumption
            ]
            if not earlier_reconnects:
                raise RuntimeError("FRESH_CONSUMPTION_WITHOUT_PRIOR_RECONNECT_TERMINAL")
            consume_event = events[consumption]
            reconnect_event = events[earlier_reconnects[-1]]
            if reconnect_event.payload.get("reconnect_succeeded") is not True:
                raise RuntimeError("FRESH_CONSUMPTION_FROM_UNSUCCESSFUL_RECONNECT")
            if (
                consume_event.route_identity != reconnect_event.route_identity
                or consume_event.route_generation != reconnect_event.route_generation
                or consume_event.p_new_sha256 != reconnect_event.p_new_sha256
            ):
                raise RuntimeError("RECONNECT_FRESH_CONSUMPTION_IDENTITY_MISMATCH")
            if (
                consume_event.sim_frame is None
                or reconnect_event.sim_frame is None
                or consume_event.sim_frame <= reconnect_event.sim_frame
            ):
                raise RuntimeError("FRESH_CONSUMPTION_NOT_LATER_THAN_RECONNECT")
            if (
                consume_event.payload.get("normal_cycle") is not True
                or consume_event.payload.get("active_equals_installed") is not True
                or consume_event.payload.get("consumed_equals_installed") is not True
            ):
                raise RuntimeError("FRESH_CONSUMPTION_PROOF_INCOMPLETE")
        terminals = _sequences(events, EventType.EPISODE_TERMINAL)
        if len(terminals) != 1:
            raise RuntimeError("EPISODE_TERMINAL_EXACTLY_ONCE_REQUIRED")
        terminal_value = events[terminals[0]].payload.get("terminal")
        if terminal_value not in {
            "COMPLETED",
            "INDEPENDENTLY_FAILED",
            "SCIENTIFIC_NONCOMPLETION_TIMEOUT",
            "PROTOCOL_INVALID",
        }:
            raise RuntimeError("EPISODE_TERMINAL_ENUM_INVALID")
        if terminals[0] != len(events) - 1:
            raise RuntimeError("EPISODE_TERMINAL_NOT_FINAL_EVENT")
        g_values = {
            event.global_task_identity_G
            for event in events
            if event.global_task_identity_G is not None
        }
        if len(g_values) > 1:
            raise RuntimeError("GLOBAL_TASK_IDENTITY_CHANGED")
        expected_accounting = {
            "route_installation_count": len(installs),
            "route_transaction_count": len(installs),
            "reconnect_count": len(_sequences(events, EventType.RECONNECT_REQUESTED)),
        }
        for name, expected in expected_accounting.items():
            if planning_accounting.get(name) != expected:
                raise RuntimeError("PLANNING_ACCOUNTING_EVENT_MISMATCH:" + name)
        stream.seal()
        receipt: dict[str, Any] = {
            "schema_version": RECEIPT_SCHEMA,
            "episode_identity": {
                "episode_id": stream.episode_id,
                "attempt_id": stream.attempt_id,
                "case_id": stream.case_id,
                "scene_id": stream.scene_id,
                "seed": stream.seed,
                "baseline_id": stream.baseline_id,
            },
            "method_freeze": dict(method_freeze),
            "G": None if not g_values else {"identity": next(iter(g_values))},
            "update": {"event_sequence": update[0]},
            "P_old_event_sequence": p_old[0],
            "P_new_event_sequence": None if not candidate_terminal else candidate_terminal[-1],
            "observable_commitment_event_sequence": _first_or_none(
                _sequences(events, EventType.OBSERVABLE_COMMITMENT_CAPTURED)
            ),
            "oracle_evaluation_event_sequence": _first_or_none(
                _sequences(events, EventType.ORACLE_TIMING_LABELED)
            ),
            "decision_event_sequences": decisions,
            "route_transaction_event_sequences": installs,
            "local_terminal_event_sequence": _first_or_none(
                _sequences(events, EventType.LOCAL_MANEUVER_TERMINAL)
            ),
            "reconnect_event_sequences": (
                _sequences(events, EventType.RECONNECT_REQUESTED)
                + _sequences(events, EventType.RECONNECT_TERMINAL)
            ),
            "consumption_event_sequences": consumptions + fresh_consumptions,
            "episode_terminal_event_sequence": terminals[0],
            "planning_accounting": dict(planning_accounting),
            "metric_results": dict(metric_results),
            "integrity_assertions": {
                "update_received_once": True,
                "p_old_before_candidate": True,
                "candidate_isolation_proven": True,
                "install_does_not_imply_consumption": True,
                "unknown_preserved": True,
                "same_G": len(g_values) <= 1,
                "baseline_visible_fields_exact": True,
                "route_install_count_reconciled": True,
                "planning_accounting_reconciled": True,
                "terminal_allowlisted_and_final": True,
            },
            "event_stream_sha256": stream.canonical_sha256,
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        return receipt


def _first_or_none(values: list[int]) -> int | None:
    return None if not values else values[0]


def _validate_available_event_fields(
    event_name: str,
    *,
    baseline_id: str,
    update_event_id: str | None,
    sim_frame: int | None,
    sim_time_s: float | None,
    route_identity: str | None,
    route_generation: int | None,
    global_task_identity_G: str | None,
    p_old_sha256: str | None,
    p_new_sha256: str | None,
    payload: Mapping[str, Any],
) -> None:
    missing: list[str] = []
    timed = {
        EventType.UPDATE_INJECTED.value,
        EventType.UPDATE_RECEIVED.value,
        EventType.P_OLD_FROZEN.value,
        EventType.OBSERVABLE_COMMITMENT_CAPTURED.value,
        EventType.ORACLE_TIMING_LABELED.value,
        EventType.CANDIDATE_PREPARATION_STARTED.value,
        EventType.CANDIDATE_PREPARATION_TERMINAL.value,
        EventType.TRANSITION_DECIDED.value,
        EventType.DEFER_REEVALUATED.value,
        EventType.ROUTE_INSTALL_REQUESTED.value,
        EventType.ROUTE_INSTALL_TERMINAL.value,
        EventType.NEXT_LEGITIMATE_PLANNER_CONSUMPTION.value,
        EventType.LOCAL_MANEUVER_TERMINAL.value,
        EventType.RECONNECT_REQUESTED.value,
        EventType.RECONNECT_TERMINAL.value,
        EventType.FRESH_GLOBAL_ROUTE_CONSUMED.value,
        EventType.EPISODE_TERMINAL.value,
    }
    if event_name in timed:
        if sim_frame is None:
            missing.append("sim_frame")
        if sim_time_s is None:
            missing.append("sim_time_s")
    if event_name in timed - {EventType.EPISODE_SETUP.value} and update_event_id is None:
        missing.append("update_event_id")
    if event_name == EventType.P_OLD_FROZEN.value:
        for name, value in (
            ("route_identity", route_identity),
            ("route_generation", route_generation),
            ("global_task_identity_G", global_task_identity_G),
            ("p_old_sha256", p_old_sha256),
        ):
            if value is None:
                missing.append(name)
    if event_name == EventType.OBSERVABLE_COMMITMENT_CAPTURED.value:
        if payload.get("state") is None:
            missing.append("payload.state")
        if payload.get("oracle_fields_present") is not False:
            missing.append("payload.oracle_fields_present=false")
    if event_name == EventType.CANDIDATE_PREPARATION_TERMINAL.value:
        if payload.get("installation_state") != "NOT_APPLICABLE_BY_BASELINE":
            for name, value in (
                ("route_identity", route_identity),
                ("route_generation", route_generation),
                ("global_task_identity_G", global_task_identity_G),
                ("p_new_sha256", p_new_sha256),
            ):
                if value is None:
                    missing.append(name)
    if event_name in {
        EventType.ROUTE_INSTALL_TERMINAL.value,
        EventType.NEXT_LEGITIMATE_PLANNER_CONSUMPTION.value,
        EventType.FRESH_GLOBAL_ROUTE_CONSUMED.value,
    }:
        for name, value in (
            ("route_identity", route_identity),
            ("route_generation", route_generation),
            ("global_task_identity_G", global_task_identity_G),
            ("p_new_sha256", p_new_sha256),
        ):
            if value is None:
                missing.append(name)
    if (
        event_name == EventType.RECONNECT_TERMINAL.value
        and payload.get("reconnect_succeeded") is True
    ):
        for name, value in (
            ("route_identity", route_identity),
            ("route_generation", route_generation),
            ("global_task_identity_G", global_task_identity_G),
            ("p_new_sha256", p_new_sha256),
        ):
            if value is None:
                missing.append(name)
    if event_name == EventType.EPISODE_TERMINAL.value and not payload.get("terminal"):
        missing.append("payload.terminal")
    if missing:
        raise ValueError(
            "AVAILABLE_EVENT_APPLICABLE_FIELD_MISSING:"
            + event_name
            + ":"
            + ",".join(missing)
        )


def _validate_metric_results(metric_results: Mapping[str, Any]) -> None:
    for metric_name, raw in metric_results.items():
        value = asdict(raw) if is_dataclass(raw) else raw
        if not isinstance(value, Mapping):
            raise RuntimeError("METRIC_RESULT_SCHEMA_INVALID:" + metric_name)
        status = value.get("status")
        status = getattr(status, "value", status)
        if status not in {"AVAILABLE", "UNKNOWN", "NOT_APPLICABLE_BY_CONTRACT"}:
            raise RuntimeError("METRIC_RESULT_STATUS_INVALID:" + metric_name)
        if not value.get("formula_version"):
            raise RuntimeError("METRIC_RESULT_FORMULA_VERSION_MISSING:" + metric_name)
        if status == "UNKNOWN":
            if value.get("value") is not None:
                raise RuntimeError("UNKNOWN_METRIC_HAS_VALUE:" + metric_name)
            unknown = value.get("unknown")
            if is_dataclass(unknown):
                unknown = asdict(unknown)
            required = {"reason_code", "missing_source", "expected_owner", "affected_fields"}
            if (
                not isinstance(unknown, Mapping)
                or not required <= set(unknown)
                or not all(unknown.get(name) for name in required)
            ):
                raise RuntimeError("UNKNOWN_METRIC_PROVENANCE_MISSING:" + metric_name)
