"""Fail-closed process binding for an M3E supervisor direct spawn.

The authoritative identity is the PID returned by ``subprocess.Popen`` plus
Linux process start time, executable, and exact command digest.  Run-ID tags in
argv or environment are deliberately auxiliary evidence: neither can select a
signal target.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


RUN_ID_ENVIRONMENT_KEY = "DRIVECLARIFY_PHASE0A_RUN_ID"
BINDING_SOURCE_DIRECT = "DIRECT_SPAWN_RETURN"


class ProcessBindingError(RuntimeError):
    """Stable fail-closed process-binding error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def command_sha256(argv: Sequence[str]) -> str:
    """Hash the exact argv vector without shell re-tokenization."""

    return sha256_bytes(_canonical(list(argv)))


def parse_proc_stat(raw: str) -> Mapping[str, Any]:
    """Parse the identity fields in Linux ``/proc/PID/stat`` safely."""

    left = raw.find("(")
    right = raw.rfind(")")
    if left <= 0 or right <= left or right + 2 >= len(raw):
        raise ProcessBindingError("PROC_STAT_SHAPE")
    tail = raw[right + 2 :].split()
    if len(tail) < 20:
        raise ProcessBindingError("PROC_STAT_FIELDS")
    return {
        "pid": int(raw[:left].strip()),
        "comm": raw[left + 1 : right],
        "state": tail[0],
        "ppid": int(tail[1]),
        "pgid": int(tail[2]),
        "sid": int(tail[3]),
        "starttime": int(tail[19]),
    }


def read_process_identity(pid: int) -> Mapping[str, Any]:
    """Read only one explicitly supplied PID; never enumerate ``/proc``."""

    entry = Path("/proc", str(int(pid)))
    row = dict(parse_proc_stat((entry / "stat").read_text(encoding="utf-8")))
    if row["pid"] != int(pid):
        raise ProcessBindingError("PROC_PID_IDENTITY")
    row["cmdline"] = [
        item.decode("utf-8", "strict")
        for item in (entry / "cmdline").read_bytes().split(b"\0")
        if item
    ]
    row["executable"] = str((entry / "exe").resolve(strict=True))
    row["uid"] = entry.stat().st_uid
    return row


def read_run_id_tag(pid: int, run_id: str) -> bool:
    """Read an auxiliary Run-ID environment tag for one known PID."""

    raw = Path("/proc", str(int(pid)), "environ").read_bytes()
    expected = (RUN_ID_ENVIRONMENT_KEY + "=" + run_id).encode("utf-8")
    return expected in filter(None, raw.split(b"\0"))


@dataclass(frozen=True)
class ProcessBinding:
    run_id: str
    pid: int
    process_start_time_ticks: int
    executable: str
    command_sha256: str
    command_argv: Sequence[str]
    launch_parent_pid: int
    binding_created_monotonic_ns: int
    binding_source: str
    authorization_receipt_id: str
    authorization_receipt_sha256: str

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["command_argv"] = list(self.command_argv)
        return value


def validate_authorization_receipt(
    receipt: Mapping[str, Any],
    *,
    expected_run_id: str,
    receipt_sha256: Optional[str] = None,
) -> Mapping[str, str]:
    """Bind a unique, non-reusable receipt to exactly one Run ID."""

    if not expected_run_id or receipt.get("run_id") != expected_run_id:
        raise ProcessBindingError("AUTHORIZATION_RECEIPT_RUN_ID_MISMATCH")
    receipt_id = receipt.get("receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise ProcessBindingError("AUTHORIZATION_RECEIPT_ID_MISSING")
    if receipt.get("receipt_reuse_allowed") is not False:
        raise ProcessBindingError("AUTHORIZATION_RECEIPT_REUSE_POLICY")
    digest = receipt_sha256 or sha256_bytes(_canonical(receipt))
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ProcessBindingError("AUTHORIZATION_RECEIPT_SHA256_INVALID")
    return {"receipt_id": receipt_id, "receipt_sha256": digest}


def validate_launch_authorization(
    spec: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    receipt_sha256: Optional[str] = None,
) -> Mapping[str, str]:
    """Reject a consumed/unauthorized spec before any real child can spawn."""

    run_id = spec.get("run_id")
    environment = spec.get("exact_command", {}).get("environment", {})
    if not isinstance(run_id, str) or not run_id:
        raise ProcessBindingError("RUN_SPEC_RUN_ID_MISSING")
    if environment.get(RUN_ID_ENVIRONMENT_KEY) != run_id:
        raise ProcessBindingError("RUN_SPEC_ENVIRONMENT_RUN_ID_MISMATCH")
    if spec.get("run_authorized") is not True:
        raise ProcessBindingError("RUN_SPEC_NOT_AUTHORIZED")
    if spec.get("authorization_consumed") is not False:
        raise ProcessBindingError("RUN_SPEC_AUTHORIZATION_ALREADY_CONSUMED")
    if spec.get("authorization_receipt_present") is not True:
        raise ProcessBindingError("RUN_SPEC_AUTHORIZATION_RECEIPT_MISSING")
    return validate_authorization_receipt(
        receipt,
        expected_run_id=run_id,
        receipt_sha256=receipt_sha256,
    )


def claim_authorization_receipt(
    receipt: Mapping[str, Any],
    *,
    expected_run_id: str,
    claim_directory: Path,
    receipt_sha256: Optional[str] = None,
) -> Path:
    """Atomically claim a receipt once, immediately before the real supervisor."""

    identity = validate_authorization_receipt(
        receipt,
        expected_run_id=expected_run_id,
        receipt_sha256=receipt_sha256,
    )
    claim_directory.mkdir(parents=True, exist_ok=True)
    safe_name = sha256_bytes(identity["receipt_id"].encode("utf-8"))
    path = claim_directory / (safe_name + ".receipt-claim.json")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise ProcessBindingError("AUTHORIZATION_RECEIPT_REUSED") from exc
    try:
        raw = _canonical(
            {
                "schema_version": "driveclarify.m3e_receipt_claim.v1",
                "run_id": expected_run_id,
                "receipt_id": identity["receipt_id"],
                "receipt_sha256": identity["receipt_sha256"],
                "claimed_by_pid": os.getpid(),
                "claimed_monotonic_ns": time.monotonic_ns(),
            }
        )
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError("short receipt-claim write")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def _identity_mismatch(
    identity: Mapping[str, Any],
    *,
    pid: int,
    parent_pid: int,
    expected_executable: str,
    expected_argv: Sequence[str],
) -> Optional[str]:
    if identity.get("pid") != pid:
        return "DIRECT_SPAWN_PID_MISMATCH"
    if identity.get("ppid") != parent_pid:
        return "DIRECT_SPAWN_PARENT_PID_MISMATCH"
    if not isinstance(identity.get("starttime"), int):
        return "DIRECT_SPAWN_STARTTIME_MISSING"
    if identity.get("uid") != os.getuid():
        return "DIRECT_SPAWN_UID_MISMATCH"
    if str(identity.get("executable")) != expected_executable:
        return "DIRECT_SPAWN_EXECUTABLE_MISMATCH"
    if list(identity.get("cmdline", [])) != list(expected_argv):
        return "DIRECT_SPAWN_COMMAND_MISMATCH"
    return None


def create_direct_spawn_binding(
    process: Any,
    *,
    run_id: str,
    expected_argv: Sequence[str],
    expected_executable: str,
    authorization_receipt: Mapping[str, Any],
    authorization_receipt_sha256: Optional[str] = None,
    launch_parent_pid: Optional[int] = None,
    identity_reader: Callable[[int], Mapping[str, Any]] = read_process_identity,
    visibility_timeout_seconds: float = 1.0,
    poll_interval_seconds: float = 0.005,
) -> ProcessBinding:
    """Create authority from the exact PID returned by a direct ``Popen``."""

    pid = int(process.pid)
    parent_pid = os.getpid() if launch_parent_pid is None else int(launch_parent_pid)
    argv = list(expected_argv)
    executable = str(Path(expected_executable).resolve(strict=True))
    receipt = validate_authorization_receipt(
        authorization_receipt,
        expected_run_id=run_id,
        receipt_sha256=authorization_receipt_sha256,
    )
    deadline = time.monotonic() + max(0.0, visibility_timeout_seconds)
    last_error = "PROCESS_NOT_VISIBLE"
    while True:
        if process.poll() is not None:
            raise ProcessBindingError("DIRECT_SPAWN_EXITED_BEFORE_BINDING")
        try:
            identity = dict(identity_reader(pid))
            mismatch = _identity_mismatch(
                identity,
                pid=pid,
                parent_pid=parent_pid,
                expected_executable=executable,
                expected_argv=argv,
            )
            if mismatch is None:
                break
            last_error = mismatch
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError) as exc:
            last_error = type(exc).__name__
        if time.monotonic() >= deadline:
            raise ProcessBindingError("DIRECT_SPAWN_VISIBILITY_TIMEOUT:" + last_error)
        time.sleep(poll_interval_seconds)
    binding = ProcessBinding(
        run_id=run_id,
        pid=pid,
        process_start_time_ticks=int(identity["starttime"]),
        executable=executable,
        command_sha256=command_sha256(argv),
        command_argv=tuple(argv),
        launch_parent_pid=parent_pid,
        binding_created_monotonic_ns=time.monotonic_ns(),
        binding_source=BINDING_SOURCE_DIRECT,
        authorization_receipt_id=receipt["receipt_id"],
        authorization_receipt_sha256=receipt["receipt_sha256"],
    )
    validate_process_binding(
        binding,
        expected_run_id=run_id,
        authorization_receipt=authorization_receipt,
        authorization_receipt_sha256=authorization_receipt_sha256,
        identity_reader=identity_reader,
    )
    return binding


def validate_process_binding(
    binding: ProcessBinding,
    *,
    expected_run_id: str,
    authorization_receipt: Mapping[str, Any],
    authorization_receipt_sha256: Optional[str] = None,
    identity_reader: Callable[[int], Mapping[str, Any]] = read_process_identity,
) -> Mapping[str, Any]:
    """Revalidate PID reuse, executable, command, parent, Run ID, and receipt."""

    if binding.binding_source != BINDING_SOURCE_DIRECT:
        raise ProcessBindingError("BINDING_SOURCE_NOT_DIRECT_SPAWN_RETURN")
    if binding.run_id != expected_run_id:
        raise ProcessBindingError("BINDING_RUN_ID_MISMATCH")
    receipt = validate_authorization_receipt(
        authorization_receipt,
        expected_run_id=expected_run_id,
        receipt_sha256=authorization_receipt_sha256,
    )
    if (
        binding.authorization_receipt_id != receipt["receipt_id"]
        or binding.authorization_receipt_sha256 != receipt["receipt_sha256"]
    ):
        raise ProcessBindingError("BINDING_RECEIPT_MISMATCH")
    try:
        identity = dict(identity_reader(binding.pid))
    except (FileNotFoundError, ProcessLookupError) as exc:
        raise ProcessBindingError("BOUND_PROCESS_EXITED") from exc
    if identity.get("pid") != binding.pid:
        raise ProcessBindingError("BOUND_PID_MISMATCH")
    if identity.get("starttime") != binding.process_start_time_ticks:
        raise ProcessBindingError("BOUND_PID_REUSED_OR_STARTTIME_MISMATCH")
    if identity.get("ppid") != binding.launch_parent_pid:
        raise ProcessBindingError("BOUND_PARENT_PID_MISMATCH")
    if identity.get("uid") != os.getuid():
        raise ProcessBindingError("BOUND_UID_MISMATCH")
    if str(identity.get("executable")) != binding.executable:
        raise ProcessBindingError("BOUND_EXECUTABLE_MISMATCH")
    if command_sha256(identity.get("cmdline", [])) != binding.command_sha256:
        raise ProcessBindingError("BOUND_COMMAND_MISMATCH")
    return identity


def select_unique_binding(
    bindings: Sequence[ProcessBinding], *, expected_run_id: str
) -> ProcessBinding:
    """Reject lookup ambiguity; a scan can never choose among candidates."""

    matches = [item for item in bindings if item.run_id == expected_run_id]
    if not matches:
        raise ProcessBindingError("BOUND_PROCESS_MISSING")
    if len(matches) != 1:
        raise ProcessBindingError("MULTIPLE_PROCESS_BINDINGS")
    return matches[0]


def observe_known_binding_tag(
    binding: ProcessBinding,
    *,
    environment_reader: Callable[[int, str], bool] = read_run_id_tag,
) -> Mapping[str, Any]:
    """Observe env tagging for a bound PID without granting it authority."""

    try:
        tagged = bool(environment_reader(binding.pid, binding.run_id))
    except (FileNotFoundError, ProcessLookupError):
        return {"pid": binding.pid, "status": "EXITED", "authoritative": False}
    except (PermissionError, OSError, UnicodeDecodeError) as exc:
        return {
            "pid": binding.pid,
            "status": "UNREADABLE",
            "error": type(exc).__name__ + ":" + str(exc),
            "authoritative": False,
        }
    return {
        "pid": binding.pid,
        "status": "EXACT_TAG" if tagged else "TAG_MISSING",
        "authoritative": False,
    }


def cleanup_bound_process(
    binding: ProcessBinding,
    process: Any,
    *,
    expected_run_id: str,
    authorization_receipt: Mapping[str, Any],
    pidfd_backend: Any,
    authorization_receipt_sha256: Optional[str] = None,
    identity_reader: Callable[[int], Mapping[str, Any]] = read_process_identity,
    grace_seconds: float = 2.0,
) -> Mapping[str, Any]:
    """Signal exactly one revalidated binding through pidfd, never a scan result."""

    if int(process.pid) != binding.pid:
        raise ProcessBindingError("POPEN_BOUND_PID_MISMATCH")
    if process.poll() is not None:
        return {"pid": binding.pid, "terminal_status": "ALREADY_EXITED", "signals": []}
    validate_process_binding(
        binding,
        expected_run_id=expected_run_id,
        authorization_receipt=authorization_receipt,
        authorization_receipt_sha256=authorization_receipt_sha256,
        identity_reader=identity_reader,
    )
    pidfd = pidfd_backend.open(binding.pid)
    signals = []
    try:
        validate_process_binding(
            binding,
            expected_run_id=expected_run_id,
            authorization_receipt=authorization_receipt,
            authorization_receipt_sha256=authorization_receipt_sha256,
            identity_reader=identity_reader,
        )
        pidfd_backend.send_signal(pidfd, signal.SIGTERM)
        signals.append("SIGTERM")
        if pidfd_backend.wait_exited(pidfd, grace_seconds):
            process.poll()
            return {
                "pid": binding.pid,
                "terminal_status": "EXITED_AFTER_SIGTERM",
                "signals": signals,
                "target_source": BINDING_SOURCE_DIRECT,
            }
        pidfd_backend.send_signal(pidfd, signal.SIGKILL)
        signals.append("SIGKILL")
        if not pidfd_backend.wait_exited(pidfd, grace_seconds):
            raise ProcessBindingError("BOUND_PROCESS_SURVIVED_SIGKILL")
        process.poll()
        return {
            "pid": binding.pid,
            "terminal_status": "EXITED_AFTER_SIGKILL",
            "signals": signals,
            "target_source": BINDING_SOURCE_DIRECT,
        }
    finally:
        pidfd_backend.close(pidfd)

