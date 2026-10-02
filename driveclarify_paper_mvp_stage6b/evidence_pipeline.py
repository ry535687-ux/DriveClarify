"""Durable execution-evidence lifecycle and restart reconciliation.

This module is execution infrastructure only.  It never imports or invokes the
scientific method, model, planner, PID, or evaluator reducer.  Its responsibilities
are limited to durable stage accounting, exact owned-process cleanup, JSONL
integrity validation, and fail-closed reconciliation of interrupted attempts.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import signal
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


JOURNAL_FILENAME = "ATTEMPT_LIFECYCLE_JOURNAL.json"
MARKER_DIRECTORY = "ATTEMPT_LIFECYCLE_MARKERS"
JSONL_VALIDATION_FILENAME = "JSONL_VALIDATION_RECEIPT.json"
DISPOSITION_FILENAME = "EVIDENCE_PIPELINE_DISPOSITION.json"
RECONCILIATION_RECEIPT_FILENAME = "EVIDENCE_PIPELINE_RECONCILIATION_RECEIPT.json"
CHILD_IDENTITY_FILENAME = "CHILD_PROCESS_IDENTITY.json"

# Values leave intentional gaps so a future execution-only revision can add a
# stage without renumbering any already-durable marker.
STAGE_RANK = {
    "PREPARED": 0,
    "LEDGER_RUNNING_COMMIT_STARTED": 10,
    "LEDGER_RUNNING_COMMITTED": 20,
    "CHILD_LAUNCHING": 30,
    "CHILD_LAUNCHED": 40,
    "WAITING_FOR_CHILD": 45,
    "FIRST_OBSERVATION_DURABLE": 50,
    "CHILD_EXITED": 60,
    "CLEANUP_STARTED": 70,
    "CLEANUP_COMPLETE": 80,
    "JSONL_VALIDATION_STARTED": 90,
    "JSONL_VALIDATED": 100,
    "TERMINAL_PRODUCER_CAPTURED": 110,
    "EPISODE_RESULT_SERIALIZATION_STARTED": 120,
    "EPISODE_RESULT_SERIALIZED": 130,
    "EPISODE_RECEIPT_WRITE_STARTED": 140,
    "EPISODE_RECEIPT_SERIALIZED": 150,
    "METRIC_RECEIPT_WRITE_STARTED": 160,
    "METRIC_RECEIPT_SERIALIZED": 170,
    "ATTEMPT_DISPOSITION_SERIALIZED": 175,
    "TERMINAL_LEDGER_COMMIT_STARTED": 180,
    "TERMINAL_LEDGER_COMMITTED": 190,
    "COMPLETE": 200,
    "RECONCILIATION_STARTED": 210,
    "RECONCILIATION_CLEANUP_COMPLETE": 220,
    "RECONCILIATION_DISPOSITION_SERIALIZED": 230,
    "RECONCILIATION_LEDGER_COMMIT_STARTED": 240,
    "RECONCILIATION_LEDGER_COMMITTED": 250,
    "RECONCILED": 260,
}


class EvidencePipelineError(RuntimeError):
    """Fail-closed execution-evidence error."""


class ParentInterruption(EvidencePipelineError):
    """A catchable SIGINT/SIGTERM raised inside the supervision boundary."""

    def __init__(self, signum: int) -> None:
        self.signum = int(signum)
        super().__init__("PARENT_INTERRUPTION:" + signal.Signals(signum).name)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: Any) -> None:
    """Write one durable JSON snapshot and fsync the containing directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp." + str(os.getpid()))
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))
    _fsync_directory(path.parent)


def _atomic_create_json(path: Path, value: Any) -> None:
    """Create an immutable marker; an existing marker is never overwritten."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(path.parent)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _proc_identity(pid: int) -> Mapping[str, Any] | None:
    """Return Linux PID identity using starttime ticks to defeat PID reuse."""

    try:
        raw = Path("/proc/{}/stat".format(int(pid))).read_text(encoding="utf-8")
        right = raw.rfind(")")
        if right < 0:
            return None
        tail = raw[right + 2 :].split()
        # tail[0] is field 3 (state); ppid/pgrp/starttime are fields 4/5/22.
        return {
            "pid": int(pid),
            "state": tail[0],
            "ppid": int(tail[1]),
            "pgid": int(tail[2]),
            "starttime_ticks": int(tail[19]),
        }
    except (OSError, ValueError, IndexError):
        return None


def capture_process_identity(pid: int) -> Mapping[str, Any]:
    identity = _proc_identity(int(pid))
    if identity is None:
        raise EvidencePipelineError("CHILD_PROCESS_IDENTITY_UNAVAILABLE")
    return identity


def _process_group_members(pgid: int) -> list[Mapping[str, Any]]:
    members = []
    proc = Path("/proc")
    for child in proc.iterdir():
        if not child.name.isdigit():
            continue
        identity = _proc_identity(int(child.name))
        if (
            identity is not None
            and identity.get("state") != "Z"
            and int(identity["pgid"]) == int(pgid)
        ):
            members.append(identity)
    return sorted(members, key=lambda item: int(item["pid"]))


class AttemptLifecycleJournal:
    """Atomic snapshot plus immutable, monotonically ranked stage markers."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.marker_dir = self.path.parent / MARKER_DIRECTORY

    @classmethod
    def create(cls, path: Path, identity: Mapping[str, Any]) -> "AttemptLifecycleJournal":
        journal = cls(path)
        if journal.path.exists() or journal.marker_dir.exists():
            raise EvidencePipelineError("ATTEMPT_LIFECYCLE_JOURNAL_ALREADY_EXISTS")
        journal.path.parent.mkdir(parents=True, exist_ok=True)
        journal.marker_dir.mkdir(parents=True, exist_ok=False)
        _fsync_directory(journal.marker_dir.parent)
        journal._identity = dict(identity)
        journal._identity_sha256 = canonical_sha256(journal._identity)
        journal.advance("PREPARED", {"journal_created_before_child_launch": True})
        return journal

    @classmethod
    def open(cls, path: Path) -> "AttemptLifecycleJournal":
        journal = cls(path)
        snapshot = _load_json(journal.path)
        journal._identity = dict(snapshot["attempt_identity"])
        journal._identity_sha256 = str(snapshot["attempt_identity_sha256"])
        if canonical_sha256(journal._identity) != journal._identity_sha256:
            raise EvidencePipelineError("ATTEMPT_JOURNAL_IDENTITY_HASH_MISMATCH")
        journal._write_snapshot()
        return journal

    @property
    def identity(self) -> Mapping[str, Any]:
        return dict(self._identity)

    @property
    def identity_sha256(self) -> str:
        return self._identity_sha256

    def _markers(self) -> list[Mapping[str, Any]]:
        markers = []
        if self.marker_dir.is_dir():
            for path in sorted(self.marker_dir.glob("*.json")):
                value = _load_json(path)
                if value.get("attempt_identity_sha256") != self._identity_sha256:
                    raise EvidencePipelineError("ATTEMPT_MARKER_IDENTITY_HASH_MISMATCH")
                markers.append(value)
        last = -1
        for marker in markers:
            rank = int(marker["stage_rank"])
            stage = str(marker["stage"])
            if STAGE_RANK.get(stage) != rank or rank <= last:
                raise EvidencePipelineError("NON_MONOTONIC_OR_UNKNOWN_ATTEMPT_MARKER")
            last = rank
        return markers

    @property
    def current_stage(self) -> str | None:
        markers = self._markers()
        return str(markers[-1]["stage"]) if markers else None

    def has_stage(self, stage: str) -> bool:
        return any(marker["stage"] == stage for marker in self._markers())

    def _write_snapshot(self) -> None:
        markers = self._markers()
        child = None
        for marker in markers:
            if marker["stage"] == "CHILD_LAUNCHED":
                child = marker.get("details", {}).get("process_identity")
        snapshot = {
            "schema_version": "driveclarify.formal_execution.attempt_lifecycle_journal.v1",
            "status": "DURABLE_MONOTONIC_LIFECYCLE",
            "attempt_identity": self._identity,
            "attempt_identity_sha256": self._identity_sha256,
            "current_stage": markers[-1]["stage"] if markers else None,
            "current_stage_rank": markers[-1]["stage_rank"] if markers else None,
            "transition_count": len(markers),
            "transitions": markers,
            "child_process_identity": child,
            "updated_at_utc": _utc_now(),
        }
        atomic_json(self.path, snapshot)

    def advance(self, stage: str, details: Mapping[str, Any] | None = None) -> None:
        if stage not in STAGE_RANK:
            raise EvidencePipelineError("UNKNOWN_ATTEMPT_LIFECYCLE_STAGE:" + stage)
        markers = self._markers()
        rank = STAGE_RANK[stage]
        if markers and rank <= int(markers[-1]["stage_rank"]):
            if rank == int(markers[-1]["stage_rank"]) and stage == markers[-1]["stage"]:
                return
            raise EvidencePipelineError("ATTEMPT_LIFECYCLE_STAGE_REGRESSION:" + stage)
        marker = {
            "schema_version": "driveclarify.formal_execution.attempt_lifecycle_marker.v1",
            "stage": stage,
            "stage_rank": rank,
            "timestamp_utc": _utc_now(),
            "monotonic_ns": time.monotonic_ns(),
            "attempt_identity_sha256": self._identity_sha256,
            "previous_marker_sha256": (
                file_sha256(
                    self.marker_dir
                    / "{:03d}_{}.json".format(
                        int(markers[-1]["stage_rank"]), markers[-1]["stage"]
                    )
                )
                if markers
                else None
            ),
            "details": dict(details or {}),
        }
        marker_path = self.marker_dir / "{:03d}_{}.json".format(rank, stage)
        _atomic_create_json(marker_path, marker)
        self._write_snapshot()

    def record_child(self, pid: int) -> Mapping[str, Any]:
        identity = capture_process_identity(pid)
        if int(identity["pgid"]) != int(pid):
            raise EvidencePipelineError("CHILD_NOT_OWN_SESSION_GROUP_LEADER")
        self.advance("CHILD_LAUNCHED", {"process_identity": identity})
        return identity

    def record_child_handshake(
        self, handshake_path: Path, *, expected_pid: int | None = None
    ) -> Mapping[str, Any]:
        value = _load_json(Path(handshake_path))
        if (
            value.get("status") != "PASS_CHILD_IDENTITY_DURABLE_BEFORE_EXEC"
            or value.get("attempt_identity_sha256") != self._identity_sha256
        ):
            raise EvidencePipelineError("CHILD_IDENTITY_HANDSHAKE_MISMATCH")
        identity = {
            "pid": int(value["pid"]),
            "pgid": int(value["pgid"]),
            "starttime_ticks": int(value["starttime_ticks"]),
        }
        if expected_pid is not None and identity["pid"] != int(expected_pid):
            raise EvidencePipelineError("CHILD_IDENTITY_HANDSHAKE_PID_MISMATCH")
        if identity["pid"] <= 1 or identity["pgid"] != identity["pid"]:
            raise EvidencePipelineError("CHILD_IDENTITY_HANDSHAKE_UNSAFE_GROUP")
        current = _proc_identity(identity["pid"])
        if current is not None and int(current["starttime_ticks"]) != identity[
            "starttime_ticks"
        ]:
            raise EvidencePipelineError("CHILD_IDENTITY_HANDSHAKE_PID_REUSED")
        self.advance(
            "CHILD_LAUNCHED",
            {
                "process_identity": identity,
                "identity_source": "CHILD_FSYNC_HANDSHAKE_BEFORE_EXEC",
                "handshake_sha256": file_sha256(Path(handshake_path)),
            },
        )
        return identity

    def recover_child_handshake(
        self, handshake_path: Path, *, wait_seconds: float = 2.0
    ) -> Mapping[str, Any] | None:
        if self.has_stage("CHILD_LAUNCHED"):
            return _load_json(self.path).get("child_process_identity")
        deadline = time.monotonic() + float(wait_seconds)
        while not Path(handshake_path).is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not Path(handshake_path).is_file():
            return None
        return self.record_child_handshake(Path(handshake_path))


@contextlib.contextmanager
def parent_interruption_guard() -> Iterable[None]:
    """Convert SIGINT/SIGTERM into a catchable exception for finally cleanup."""

    old: dict[int, Any] = {}

    def _handler(signum: int, _frame: Any) -> None:
        raise ParentInterruption(signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            old[signum] = signal.getsignal(signum)
            signal.signal(signum, _handler)
        yield
    finally:
        for signum, handler in old.items():
            signal.signal(signum, handler)


@contextlib.contextmanager
def interruption_safe_cleanup() -> Iterable[None]:
    """Defer new parent signals while exact cleanup evidence is emitted."""

    old: dict[int, Any] = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            old[signum] = signal.getsignal(signum)
            signal.signal(signum, signal.SIG_IGN)
        yield
    finally:
        for signum, handler in old.items():
            signal.signal(signum, handler)


def cleanup_owned_process(journal: AttemptLifecycleJournal) -> Mapping[str, Any]:
    snapshot = _load_json(journal.path)
    recorded = snapshot.get("child_process_identity")
    if not isinstance(recorded, Mapping):
        return {
            "status": "PASS_NO_CHILD_RECORDED",
            "signal_escalation": [],
            "members_before": [],
            "members_after": [],
            "pid_reuse_blocked": False,
        }
    pid = int(recorded["pid"])
    pgid = int(recorded["pgid"])
    if pid <= 1 or pgid != pid or pgid == os.getpgrp():
        return {
            "status": "BLOCKED_UNSAFE_RECORDED_PROCESS_IDENTITY",
            "signal_escalation": [],
            "members_before": [],
            "members_after": [],
            "pid_reuse_blocked": True,
        }
    current = _proc_identity(pid)
    if current is not None and int(current["starttime_ticks"]) != int(
        recorded["starttime_ticks"]
    ):
        return {
            "status": "BLOCKED_PID_REUSE_IDENTITY_MISMATCH",
            "signal_escalation": [],
            "members_before": _process_group_members(pgid),
            "members_after": _process_group_members(pgid),
            "pid_reuse_blocked": True,
        }
    before = _process_group_members(pgid)
    escalation = []
    for name, signum, timeout in (
        ("SIGTERM", signal.SIGTERM, 1.0),
        ("SIGKILL", signal.SIGKILL, 1.0),
    ):
        if not _process_group_members(pgid):
            break
        try:
            os.killpg(pgid, signum)
            escalation.append(name + ":pgid=" + str(pgid))
        except ProcessLookupError:
            break
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and _process_group_members(pgid):
            time.sleep(0.01)
    after = _process_group_members(pgid)
    return {
        "status": "PASS" if not after else "BLOCKED_RESIDUAL_OWNED_PROCESS",
        "recorded_process_identity": dict(recorded),
        "signal_escalation": escalation,
        "members_before": before,
        "members_after": after,
        "pid_reuse_blocked": False,
    }


def validate_jsonl(
    path: Path,
    *,
    expected_episode_id: str | None = None,
    record_kind: str = "generic",
) -> Mapping[str, Any]:
    """Validate a JSONL stream byte-for-byte and stop at the first bad record."""

    path = Path(path)
    if not path.is_file():
        return {
            "status": "MISSING",
            "path": str(path),
            "file_sha256": None,
            "file_bytes": 0,
            "valid_record_count": 0,
            "last_valid_offset": 0,
            "first_invalid_offset": None,
            "trailing_bytes": 0,
            "reason": "FILE_MISSING",
        }
    raw = path.read_bytes()
    valid = 0
    last_valid = 0
    first_invalid = None
    reason = None
    previous_frame = None
    expected_sequence = 0
    offset = 0
    for physical in raw.splitlines(keepends=True):
        start = offset
        offset += len(physical)
        if b"\x00" in physical:
            first_invalid, reason = start, "NUL_BYTE_IN_JSONL_TAIL_OR_RECORD"
            break
        if not physical.endswith(b"\n"):
            first_invalid, reason = start, "PARTIAL_FINAL_LINE_WITHOUT_NEWLINE"
            break
        payload = physical[:-1]
        if payload.endswith(b"\r"):
            payload = payload[:-1]
        if not payload:
            first_invalid, reason = start, "EMPTY_PHYSICAL_LINE"
            break
        try:
            row = json.loads(payload.decode("utf-8"))
        except UnicodeDecodeError:
            first_invalid, reason = start, "INVALID_UTF8"
            break
        except json.JSONDecodeError:
            first_invalid, reason = start, "MALFORMED_JSON"
            break
        if not isinstance(row, Mapping):
            first_invalid, reason = start, "JSON_RECORD_NOT_OBJECT"
            break
        if expected_episode_id is not None and record_kind == "probe":
            if row.get("run_id") != expected_episode_id:
                first_invalid, reason = start, "PROBE_EPISODE_IDENTITY_MISMATCH"
                break
            if row.get("record_seq") != expected_sequence:
                first_invalid, reason = start, "PROBE_RECORD_SEQUENCE_DISCONTINUITY"
                break
            expected_sequence += 1
        elif expected_episode_id is not None and record_kind == "frame":
            observation = row.get("observation_id")
            if not isinstance(observation, str) or not observation.startswith(
                expected_episode_id + ":"
            ):
                first_invalid, reason = start, "FRAME_EPISODE_IDENTITY_MISMATCH"
                break
            frame = row.get("frame_id")
            if not isinstance(frame, int) or (
                previous_frame is not None and frame != previous_frame + 1
            ):
                first_invalid, reason = start, "FRAME_SEQUENCE_DISCONTINUITY"
                break
            previous_frame = frame
        valid += 1
        last_valid = offset
    if first_invalid is None and offset < len(raw):
        first_invalid, reason = offset, "UNPARSED_TRAILING_BYTES"
    status = "PASS_VALID_JSONL" if first_invalid is None else "BLOCKED_INVALID_JSONL"
    return {
        "status": status,
        "path": str(path),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "file_bytes": len(raw),
        "valid_record_count": valid,
        "last_valid_offset": last_valid,
        "valid_prefix_sha256": hashlib.sha256(raw[:last_valid]).hexdigest(),
        "first_invalid_offset": first_invalid,
        "trailing_bytes": len(raw) - last_valid,
        "trailing_sha256": (
            hashlib.sha256(raw[last_valid:]).hexdigest() if len(raw) > last_valid else None
        ),
        "reason": reason,
        "record_kind": record_kind,
        "expected_episode_id": expected_episode_id,
        "physical_line_count_not_used_as_record_count": True,
    }


def validate_attempt_jsonl(output_dir: Path, episode_id: str) -> Mapping[str, Any]:
    output_dir = Path(output_dir)
    frame = validate_jsonl(
        output_dir / "stage6b_frame_trace.jsonl",
        expected_episode_id=episode_id,
        record_kind="frame",
    )
    probe = validate_jsonl(
        output_dir / "probe/probe.jsonl",
        expected_episode_id=episode_id,
        record_kind="probe",
    )
    passed = (
        frame["status"] == "PASS_VALID_JSONL"
        and probe["status"] == "PASS_VALID_JSONL"
        and int(frame["valid_record_count"]) > 0
        and int(probe["valid_record_count"]) > 0
    )
    return {
        "schema_version": "driveclarify.formal_execution.jsonl_validation.v1",
        "status": "PASS" if passed else "BLOCKED_JSONL_EVIDENCE_INCOMPLETE_OR_CORRUPT",
        "episode_id": episode_id,
        "frame_trace": frame,
        "probe_trace": probe,
        "valid_record_count_uses_successful_json_decode_only": True,
        "unknown_outcome_preserved_on_failure": True,
        "generated_at_utc": _utc_now(),
    }


def write_jsonl_validation_receipt(output_dir: Path, episode_id: str) -> Mapping[str, Any]:
    receipt = validate_attempt_jsonl(output_dir, episode_id)
    path = Path(output_dir) / JSONL_VALIDATION_FILENAME
    if path.exists():
        existing = _load_json(path)
        comparable_existing = {
            key: value for key, value in existing.items() if key != "generated_at_utc"
        }
        comparable_current = {
            key: value for key, value in receipt.items() if key != "generated_at_utc"
        }
        if comparable_existing != comparable_current:
            raise EvidencePipelineError(
                "EXISTING_JSONL_VALIDATION_RECEIPT_NO_LONGER_MATCHES_RAW_BYTES"
            )
        return existing
    atomic_json(path, receipt)
    return receipt


def _attempt_state(attempt: Mapping[str, Any]) -> str:
    events = attempt.get("events", [])
    return str(events[-1].get("state")) if events else "UNKNOWN"


def _scientific_exposure(
    output_dir: Path, journal: AttemptLifecycleJournal
) -> Mapping[str, Any]:
    validation = validate_attempt_jsonl(output_dir, str(journal.identity["episode_id"]))
    frame_count = int(validation["frame_trace"]["valid_record_count"])
    probe_count = int(validation["probe_trace"]["valid_record_count"])
    runtime_path = Path(output_dir) / "stage6b_runtime_audit.json"
    runtime = _load_json(runtime_path) if runtime_path.is_file() else {}
    counters = []
    for key in (
        "normal_model_forwards",
        "candidate_model_forwards",
        "planner_advances",
        "existing_pid_invocations",
    ):
        value = runtime.get(key)
        if isinstance(value, (int, float)):
            counters.append(float(value))
    exposed = bool(
        journal.has_stage("FIRST_OBSERVATION_DURABLE")
        or frame_count > 0
        or probe_count > 0
        or any(value > 0 for value in counters)
    )
    return {
        "nonzero": exposed,
        "valid_frame_records": frame_count,
        "valid_probe_records": probe_count,
        "first_observation_marker": journal.has_stage("FIRST_OBSERVATION_DURABLE"),
        "runtime_positive_counter_observed": any(value > 0 for value in counters),
        "whole_attempt_scientific_outcome": "UNKNOWN",
    }


def _identity_from_attempt(attempt: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: attempt.get(key)
        for key in (
            "attempt_id",
            "episode_id",
            "split",
            "global_slot_index",
            "split_slot_index",
            "scenario_id",
            "seed",
            "method_id",
            "runtime_fixture_id",
        )
    }


def _completed_chain_integrity(
    output_dir: Path,
    attempt: Mapping[str, Any],
    journal: AttemptLifecycleJournal,
    jsonl: Mapping[str, Any],
) -> Mapping[str, Any]:
    required = {
        "cleanup": Path(output_dir) / "CLEANUP_RECEIPT.json",
        "episode_result": Path(output_dir) / "EPISODE_RESULT.json",
        "episode_receipt": Path(output_dir) / "EPISODE_RECEIPT.json",
        "metric_receipt": Path(output_dir) / "FRESH_FORMAL_METRIC_RECEIPT.json",
        "jsonl_validation": Path(output_dir) / JSONL_VALIDATION_FILENAME,
    }
    present = {name: path.is_file() for name, path in required.items()}
    episode_id = str(attempt["episode_id"])
    identities = {}
    statuses = {}
    for name in ("episode_result", "episode_receipt", "metric_receipt"):
        if present[name]:
            value = _load_json(required[name])
            identities[name] = value.get("episode_identity", {}).get("episode_id")
            statuses[name] = value.get("status")
    required_stages = (
        "CLEANUP_COMPLETE",
        "JSONL_VALIDATED",
        "TERMINAL_PRODUCER_CAPTURED",
        "EPISODE_RESULT_SERIALIZED",
        "EPISODE_RECEIPT_SERIALIZED",
        "METRIC_RECEIPT_SERIALIZED",
        "TERMINAL_LEDGER_COMMIT_STARTED",
    )
    stage_presence = {stage: journal.has_stage(stage) for stage in required_stages}
    passed = (
        all(present.values())
        and jsonl.get("status") == "PASS"
        and all(identity == episode_id for identity in identities.values())
        and statuses.get("episode_receipt")
        == "COMPLETED_RECORDED_METHOD_RESULT"
        and statuses.get("metric_receipt")
        == "PASS_COMPLETED_RECORDED_EVIDENCE_CHAIN"
        and all(stage_presence.values())
    )
    return {
        "status": "PASS_COMPLETE_CHAIN" if passed else "BLOCKED_INCOMPLETE_CHAIN",
        "required_artifacts_present": present,
        "artifact_episode_identities": identities,
        "artifact_statuses": statuses,
        "required_stage_presence": stage_presence,
        "jsonl_status": jsonl.get("status"),
    }


def _write_disposition(
    output_dir: Path,
    attempt: Mapping[str, Any],
    exposure: Mapping[str, Any],
    cleanup: Mapping[str, Any],
    jsonl: Mapping[str, Any],
) -> Mapping[str, Any]:
    path = Path(output_dir) / DISPOSITION_FILENAME
    state = "BLOCKED_CONTRACT_DEFECT" if exposure["nonzero"] else "ENGINEERING_INVALID"
    value = {
        "schema_version": "driveclarify.formal_execution.interruption_disposition.v1",
        "status": "PASS_FAIL_CLOSED_INTERRUPTED_ATTEMPT_DISPOSITION",
        "attempt_identity": _identity_from_attempt(attempt),
        "terminal_state": state,
        "scientific_exposure": "NONZERO" if exposure["nonzero"] else "ZERO_VERIFIED",
        "scientific_outcome": "UNKNOWN",
        "route_outcome": "UNKNOWN",
        "safety_outcome": "UNKNOWN",
        "rerun_eligible": False,
        "replacement_eligible": False,
        "automatic_retry_allowed": False,
        "scientific_retry_allowed": False,
        "exposure_evidence": dict(exposure),
        "jsonl_validation": jsonl,
        "owned_process_cleanup": cleanup,
        "optimistic_inference_used": False,
        "generated_at_utc": _utc_now(),
    }
    if path.exists():
        old = _load_json(path)
        if old.get("attempt_identity") != value["attempt_identity"]:
            raise EvidencePipelineError("EXISTING_DISPOSITION_IDENTITY_MISMATCH")
        return old
    atomic_json(path, value)
    return value


def write_fail_closed_disposition(
    *,
    output_dir: Path,
    attempt: Mapping[str, Any],
    journal: AttemptLifecycleJournal,
    cleanup: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Classify a non-complete attempt without inferring a scientific result."""

    output_dir = Path(output_dir)
    jsonl = write_jsonl_validation_receipt(
        output_dir, str(attempt["episode_id"])
    )
    exposure = _scientific_exposure(output_dir, journal)
    cleanup_value = dict(cleanup or {"status": "UNKNOWN_NOT_AVAILABLE"})
    return _write_disposition(
        output_dir, attempt, exposure, cleanup_value, jsonl
    )


def reconcile_incomplete_attempts(
    *,
    ledger_path: Path,
    repository_root: Path,
    derive_ledger: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    resource_snapshot: Callable[[], Mapping[str, Any]] | None = None,
    journal_search_root: Path | None = None,
) -> Mapping[str, Any]:
    """Append a terminal disposition to every durable RUNNING attempt.

    A RUNNING attempt is never promoted to a scientific completion during
    recovery.  Nonzero exposure becomes CONTRACT_DEFECT + UNKNOWN; verified
    zero exposure becomes ENGINEERING_INVALID + UNKNOWN.  The caller must hard
    stop after any reconciliation and must not select a next slot in that call.
    """

    ledger_path = Path(ledger_path)
    repository_root = Path(repository_root)
    ledger = _load_json(ledger_path)
    known_attempt_ids = {
        str(attempt.get("attempt_id")) for attempt in ledger.get("attempt_records", [])
    }
    known_journals = {
        str(attempt.get("lifecycle_journal_path"))
        for attempt in ledger.get("attempt_records", [])
        if attempt.get("lifecycle_journal_path")
    }
    search_root = Path(journal_search_root or repository_root)
    orphan_journals = []
    for journal_path in sorted(search_root.rglob(JOURNAL_FILENAME)):
        relative = str(journal_path.relative_to(repository_root))
        if relative in known_journals:
            continue
        journal = AttemptLifecycleJournal.open(journal_path)
        identity = dict(journal.identity)
        attempt_id = str(identity.get("attempt_id"))
        if not attempt_id or attempt_id in known_attempt_ids:
            raise EvidencePipelineError("ORPHAN_JOURNAL_ATTEMPT_ID_COLLISION")
        artifact_dir = str(journal_path.parent.relative_to(repository_root))
        recovered = {
            **_identity_from_attempt(identity),
            "automatic_retry_count": 0,
            "scientific_retry_count": 0,
            "engineering_rerun_count": 0,
            "artifact_dir": artifact_dir,
            "lifecycle_journal_path": relative,
            "events": [
                {
                    "event": "ORPHAN_LIFECYCLE_JOURNAL_RECOVERED",
                    "state": "RUNNING",
                    "timestamp_utc": _utc_now(),
                    "journal_created_before_child_launch": True,
                    "automatic_rerun_started": False,
                }
            ],
        }
        ledger.setdefault("attempt_records", []).append(recovered)
        known_attempt_ids.add(attempt_id)
        known_journals.add(relative)
        orphan_journals.append(relative)
    if orphan_journals:
        saved = derive_ledger(ledger) if derive_ledger is not None else ledger
        atomic_json(ledger_path, saved)
    repaired = []
    terminal_journal_repairs = []
    for attempt in ledger.get("attempt_records", []):
        output_dir = repository_root / str(attempt["artifact_dir"])
        journal_path = output_dir / JOURNAL_FILENAME
        state = _attempt_state(attempt)
        if state != "RUNNING" and not journal_path.is_file():
            continue
        if not journal_path.is_file():
            output_dir.mkdir(parents=True, exist_ok=True)
            journal = AttemptLifecycleJournal.create(
                journal_path, _identity_from_attempt(attempt)
            )
        else:
            journal = AttemptLifecycleJournal.open(journal_path)
        if journal.current_stage in ("COMPLETE", "RECONCILED"):
            continue
        journal.recover_child_handshake(
            output_dir / CHILD_IDENTITY_FILENAME,
            wait_seconds=2.0 if journal.current_stage == "CHILD_LAUNCHING" else 0.0,
        )
        journal.advance(
            "RECONCILIATION_STARTED",
            {"ledger_state_at_restart": state, "automatic_rerun": False},
        )
        with interruption_safe_cleanup():
            cleanup = cleanup_owned_process(journal)
        resources = dict(resource_snapshot()) if resource_snapshot is not None else None
        cleanup_pass = cleanup["status"].startswith("PASS") and (
            resources is None or resources.get("status") == "PASS"
        )
        journal.advance(
            "RECONCILIATION_CLEANUP_COMPLETE",
            {"owned_cleanup": cleanup, "resource_snapshot": resources},
        )
        jsonl = write_jsonl_validation_receipt(
            output_dir, str(attempt["episode_id"])
        )
        if state != "RUNNING":
            chain = (
                _completed_chain_integrity(output_dir, attempt, journal, jsonl)
                if state == "COMPLETED_RECORDED"
                else {"status": "NOT_APPLICABLE_NONCOMPLETED_TERMINAL_STATE"}
            )
            if state == "COMPLETED_RECORDED" and chain["status"] != "PASS_COMPLETE_CHAIN":
                exposure = _scientific_exposure(output_dir, journal)
                disposition = _write_disposition(
                    output_dir, attempt, exposure, cleanup, jsonl
                )
                ledger_before = file_sha256(ledger_path)
                events_before = list(attempt.get("events", []))
                attempt.setdefault("events", []).append(
                    {
                        "event": "RECOVERY_REJECTED_INCOMPLETE_COMPLETION_CHAIN",
                        "state": "BLOCKED_CONTRACT_DEFECT",
                        "timestamp_utc": _utc_now(),
                        "scientific_outcome": "UNKNOWN",
                        "rerun_eligible": False,
                        "replacement_eligible": False,
                        "completed_chain_integrity": chain,
                        "events_prefix_sha256": canonical_sha256(events_before),
                    }
                )
                journal.advance(
                    "RECONCILIATION_DISPOSITION_SERIALIZED",
                    {
                        "existing_completed_state_rejected": True,
                        "new_terminal_state": "BLOCKED_CONTRACT_DEFECT",
                        "scientific_outcome": "UNKNOWN",
                        "disposition_sha256": file_sha256(
                            output_dir / DISPOSITION_FILENAME
                        ),
                    },
                )
                journal.advance(
                    "RECONCILIATION_LEDGER_COMMIT_STARTED",
                    {"ledger_sha256_before": ledger_before},
                )
                saved = derive_ledger(ledger) if derive_ledger is not None else ledger
                atomic_json(ledger_path, saved)
                journal.advance(
                    "RECONCILIATION_LEDGER_COMMITTED",
                    {"ledger_sha256_after": file_sha256(ledger_path)},
                )
                journal.advance(
                    "RECONCILED",
                    {
                        "existing_completed_state_preserved": False,
                        "next_slot_started": False,
                    },
                )
                terminal_journal_repairs.append(str(attempt["attempt_id"]))
                continue
            journal.advance(
                "RECONCILIATION_DISPOSITION_SERIALIZED",
                {
                    "existing_terminal_state": state,
                    "ledger_history_changed": False,
                    "cleanup_pass": cleanup_pass,
                    "completed_chain_integrity": chain,
                },
            )
            journal.advance(
                "RECONCILED",
                {"existing_terminal_state_preserved": state, "next_slot_started": False},
            )
            terminal_journal_repairs.append(str(attempt["attempt_id"]))
            continue
        exposure = _scientific_exposure(output_dir, journal)
        disposition = _write_disposition(
            output_dir, attempt, exposure, cleanup, jsonl
        )
        journal.advance(
            "RECONCILIATION_DISPOSITION_SERIALIZED",
            {
                "disposition_sha256": file_sha256(output_dir / DISPOSITION_FILENAME),
                "terminal_state": disposition["terminal_state"],
                "scientific_outcome": "UNKNOWN",
            },
        )
        ledger_before = file_sha256(ledger_path)
        events_before = list(attempt.get("events", []))
        events_before_sha = canonical_sha256(events_before)
        journal.advance(
            "RECONCILIATION_LEDGER_COMMIT_STARTED",
            {"ledger_sha256_before": ledger_before},
        )
        attempt.setdefault("events", []).append(
            {
                "event": "INTERRUPTED_ATTEMPT_RECONCILED",
                "state": disposition["terminal_state"],
                "timestamp_utc": _utc_now(),
                "blocker": "PARENT_OR_SESSION_INTERRUPTION_INCOMPLETE_EVIDENCE_CHAIN",
                "scientific_exposure": disposition["scientific_exposure"],
                "scientific_outcome": "UNKNOWN",
                "route_outcome": "UNKNOWN",
                "safety_outcome": "UNKNOWN",
                "rerun_eligible": False,
                "replacement_eligible": False,
                "cleanup_status": cleanup["status"],
                "resource_status": resources.get("status") if resources else None,
                "jsonl_validation_status": jsonl["status"],
                "disposition_path": str(
                    (output_dir / DISPOSITION_FILENAME).relative_to(repository_root)
                ),
                "disposition_sha256": file_sha256(output_dir / DISPOSITION_FILENAME),
                "events_prefix_sha256": events_before_sha,
            }
        )
        saved = derive_ledger(ledger) if derive_ledger is not None else ledger
        atomic_json(ledger_path, saved)
        ledger_after = file_sha256(ledger_path)
        journal.advance(
            "RECONCILIATION_LEDGER_COMMITTED",
            {
                "ledger_sha256_before": ledger_before,
                "ledger_sha256_after": ledger_after,
                "attempt_events_before_count": len(events_before),
                "attempt_events_after_count": len(attempt["events"]),
                "events_prefix_preserved": attempt["events"][: len(events_before)]
                == events_before,
            },
        )
        receipt = {
            "schema_version": "driveclarify.formal_execution.restart_reconciliation.v1",
            "status": "PASS_RECONCILED_AND_HARD_STOP_REQUIRED" if cleanup_pass else "BLOCKED_RECONCILIATION_CLEANUP",
            "attempt_identity": _identity_from_attempt(attempt),
            "terminal_state": disposition["terminal_state"],
            "scientific_outcome": "UNKNOWN",
            "rerun_eligible": False,
            "replacement_eligible": False,
            "ledger_sha256_before": ledger_before,
            "ledger_sha256_after": ledger_after,
            "ledger_event_prefix_sha256": events_before_sha,
            "ledger_event_prefix_preserved": attempt["events"][: len(events_before)]
            == events_before,
            "owned_process_cleanup": cleanup,
            "resource_snapshot": resources,
            "jsonl_validation": jsonl,
            "next_slot_selected": False,
            "automatic_rerun_started": False,
            "generated_at_utc": _utc_now(),
        }
        atomic_json(output_dir / RECONCILIATION_RECEIPT_FILENAME, receipt)
        journal.advance(
            "RECONCILED",
            {
                "receipt_sha256": file_sha256(
                    output_dir / RECONCILIATION_RECEIPT_FILENAME
                ),
                "next_slot_started": False,
            },
        )
        repaired.append(receipt)
    return {
        "schema_version": "driveclarify.formal_execution.restart_reconciliation_batch.v1",
        "status": (
            "PASS_NO_INCOMPLETE_ATTEMPTS"
            if not repaired and not terminal_journal_repairs
            else "HARD_STOP_RECONCILIATION_PERFORMED"
        ),
        "reconciled_running_attempt_count": len(repaired),
        "reconciled_terminal_journal_count": len(terminal_journal_repairs),
        "recovered_orphan_journal_count": len(orphan_journals),
        "recovered_orphan_journals": orphan_journals,
        "reconciled_attempts": repaired,
        "terminal_journals_finalized": terminal_journal_repairs,
        "next_slot_selection_allowed_in_this_call": False
        if repaired or terminal_journal_repairs
        else True,
    }


def verify_source_hash_record(
    record: Mapping[str, Any], repository_root: Path
) -> Mapping[str, Any]:
    mismatches = []
    hashes = record.get("execution_only_source_hashes", {})
    for relative, expected in hashes.items():
        path = Path(repository_root) / str(relative)
        observed = file_sha256(path) if path.is_file() else None
        if observed != expected:
            mismatches.append(
                {"path": str(relative), "expected": expected, "observed": observed}
            )
    lines = "".join("{}  {}\n".format(value, key) for key, value in sorted(hashes.items()))
    aggregate = hashlib.sha256(lines.encode("utf-8")).hexdigest()
    expected_aggregate = record.get("execution_only_source_aggregate_sha256")
    return {
        "status": "PASS" if not mismatches and aggregate == expected_aggregate else "BLOCKED",
        "aggregate_sha256": aggregate,
        "expected_aggregate_sha256": expected_aggregate,
        "mismatches": mismatches,
    }


__all__ = [
    "AttemptLifecycleJournal",
    "CHILD_IDENTITY_FILENAME",
    "DISPOSITION_FILENAME",
    "EvidencePipelineError",
    "JOURNAL_FILENAME",
    "JSONL_VALIDATION_FILENAME",
    "ParentInterruption",
    "RECONCILIATION_RECEIPT_FILENAME",
    "STAGE_RANK",
    "atomic_json",
    "capture_process_identity",
    "cleanup_owned_process",
    "file_sha256",
    "interruption_safe_cleanup",
    "parent_interruption_guard",
    "reconcile_incomplete_attempts",
    "validate_attempt_jsonl",
    "validate_jsonl",
    "verify_source_hash_record",
    "write_fail_closed_disposition",
    "write_jsonl_validation_receipt",
]
