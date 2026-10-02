"""M3E adapter that makes direct-spawn process bindings authoritative.

This adapter hash-pins the historical supervisor instead of mutating it.  It
runs a CPU-only direct-spawn selftest before an authorization receipt can be
claimed and before the historical supervisor can launch CARLA.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .process_binding import (
    ProcessBinding,
    ProcessBindingError,
    claim_authorization_receipt,
    cleanup_bound_process,
    create_direct_spawn_binding,
    observe_known_binding_tag,
    read_process_identity,
    read_run_id_tag,
    sha256_bytes,
    validate_launch_authorization,
    validate_process_binding,
)


HISTORICAL_SUPERVISOR_PATH = Path(
    "/home/buaa/wrh/DriveClarify/reports/"
    "driveclarify_manual_phase0a_supervisor_process_identity_fix/"
    "PHASE0A_TASK_SUPERVISOR.py"
)
HISTORICAL_SUPERVISOR_SHA256 = (
    "79e085aaba8fe63a8964cecc8d49767f9a8a7e71bab086fe2c39292fcc1139be"
)
DUMMY_PATH = Path(__file__).resolve().with_name("dummy_bound_process.py")
SELFTEST_RUN_ID = "M3E-PRELAUNCH-SUPERVISOR-SELFTEST"


def _load_historical_supervisor() -> Any:
    raw = HISTORICAL_SUPERVISOR_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != HISTORICAL_SUPERVISOR_SHA256:
        raise ProcessBindingError("HISTORICAL_SUPERVISOR_SHA256_MISMATCH")
    name = "_driveclarify_m3e_historical_supervisor_" + HISTORICAL_SUPERVISOR_SHA256[:16]
    spec = importlib.util.spec_from_file_location(name, str(HISTORICAL_SUPERVISOR_PATH))
    if spec is None or spec.loader is None:
        raise ProcessBindingError("HISTORICAL_SUPERVISOR_IMPORT_SPEC")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _dummy_receipt(run_id: str) -> Mapping[str, Any]:
    return {
        "schema_version": "driveclarify.m3e_authorization_receipt.selftest.v1",
        "receipt_id": "SELFTEST-RECEIPT-" + run_id,
        "run_id": run_id,
        "receipt_reuse_allowed": False,
    }


def run_prelaunch_selftest(supervisor_module: Optional[Any] = None) -> Mapping[str, Any]:
    """Exercise the same binding and pidfd cleanup used by the real adapter."""

    supervisor = supervisor_module or _load_historical_supervisor()
    receipt = _dummy_receipt(SELFTEST_RUN_ID)
    environment = dict(os.environ)
    environment["DRIVECLARIFY_PHASE0A_RUN_ID"] = SELFTEST_RUN_ID
    token = "%d-%d" % (os.getpid(), time.monotonic_ns())
    bound_argv = [
        sys.executable,
        "-B",
        str(DUMMY_PATH),
        "--hold-seconds",
        "30",
        "--token",
        "bound-" + token,
    ]
    sentinel_argv = [
        sys.executable,
        "-B",
        str(DUMMY_PATH),
        "--hold-seconds",
        "30",
        "--token",
        "unrelated-" + token,
    ]
    bound = sentinel = None
    binding = None
    cleanup = None
    try:
        sentinel = subprocess.Popen(
            sentinel_argv,
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )
        bound = subprocess.Popen(
            bound_argv,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )
        binding = create_direct_spawn_binding(
            bound,
            run_id=SELFTEST_RUN_ID,
            expected_argv=bound_argv,
            expected_executable=sys.executable,
            authorization_receipt=receipt,
        )
        validate_process_binding(
            binding,
            expected_run_id=SELFTEST_RUN_ID,
            authorization_receipt=receipt,
        )
        auxiliary = observe_known_binding_tag(binding)
        cleanup = cleanup_bound_process(
            binding,
            bound,
            expected_run_id=SELFTEST_RUN_ID,
            authorization_receipt=receipt,
            pidfd_backend=supervisor.LinuxPidfdBackendV1(),
        )
        if sentinel.poll() is not None:
            raise ProcessBindingError("SELFTEST_UNRELATED_PROCESS_TERMINATED")
        return {
            "schema_version": "driveclarify.m3e_prelaunch_supervisor_selftest.v1",
            "status": "PASS",
            "cpu_only": True,
            "real_system_launches": 0,
            "run_id": SELFTEST_RUN_ID,
            "binding": binding.to_dict(),
            "auxiliary_run_id_tag": auxiliary,
            "cleanup": cleanup,
            "unrelated_pid_survived_bound_cleanup": True,
            "production_historical_supervisor_sha256": HISTORICAL_SUPERVISOR_SHA256,
        }
    finally:
        for process in (bound, sentinel):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)


class _BindingAdapter:
    def __init__(
        self,
        *,
        supervisor: Any,
        receipt: Mapping[str, Any],
        receipt_sha256: str,
    ) -> None:
        self.supervisor = supervisor
        self.receipt = receipt
        self.receipt_sha256 = receipt_sha256
        self.bindings: List[ProcessBinding] = []
        self.binding_roles: Dict[int, str] = {}
        self.original_complete = supervisor._complete_launched_child_record
        self.original_tag_validator = supervisor._validate_tagged_owned_set

    def complete_record(self, record: Dict[str, Any], identity: Mapping[str, Any]) -> None:
        self.original_complete(record, identity)
        public = record["public"]
        binding = create_direct_spawn_binding(
            record["process"],
            run_id=str(public["run_id"]),
            expected_argv=list(public["argv_cmdline"]),
            expected_executable=str(public["executable_path"]),
            authorization_receipt=self.receipt,
            authorization_receipt_sha256=self.receipt_sha256,
            launch_parent_pid=os.getpid(),
        )
        public["supervisor_process_binding"] = binding.to_dict()
        public["run_id_tag_authority"] = "AUXILIARY_ONLY"
        self.bindings.append(binding)
        self.binding_roles[binding.pid] = str(public["role"])

    def known_tag_scan(
        self, run_id: str, scan_audit: Optional[List[Mapping[str, Any]]] = None
    ) -> List[Mapping[str, Any]]:
        """Observe only supervisor/direct-spawn PIDs; never enumerate user processes."""

        audit = scan_audit if scan_audit is not None else []
        rows: List[Mapping[str, Any]] = []
        known_pids = [os.getpid()] + [binding.pid for binding in self.bindings]
        for binding in self.bindings:
            try:
                validate_process_binding(
                    binding,
                    expected_run_id=binding.run_id,
                    authorization_receipt=self.receipt,
                    authorization_receipt_sha256=self.receipt_sha256,
                )
            except ProcessBindingError as exc:
                if exc.code != "BOUND_PROCESS_EXITED":
                    raise
                audit.append(
                    {
                        "pid": binding.pid,
                        "role": self.binding_roles.get(binding.pid, "unknown"),
                        "status": "DIRECT_BINDING_EXIT_CONFIRMED",
                        "binding_source": binding.binding_source,
                        "run_id": binding.run_id,
                    }
                )
                continue
            audit.append(
                {
                    "pid": binding.pid,
                    "role": self.binding_roles.get(binding.pid, "unknown"),
                    "status": "DIRECT_BINDING_REVALIDATED",
                    "binding_source": binding.binding_source,
                    "run_id": binding.run_id,
                }
            )
        for pid in known_pids:
            try:
                if not read_run_id_tag(pid, run_id):
                    audit.append({"pid": pid, "status": "KNOWN_PID_RUN_TAG_MISSING"})
                    continue
                row = dict(read_process_identity(pid))
                row["exact_run_id_environment"] = True
                row["tag_authority"] = "AUXILIARY_ONLY"
                rows.append(row)
            except (FileNotFoundError, ProcessLookupError):
                audit.append({"pid": pid, "status": "KNOWN_PID_EXITED"})
            except (PermissionError, OSError, UnicodeDecodeError, RuntimeError) as exc:
                audit.append(
                    {
                        "pid": pid,
                        "status": "KNOWN_PID_TAG_UNREADABLE",
                        "error": type(exc).__name__ + ":" + str(exc),
                    }
                )
        return sorted(rows, key=lambda row: int(row["pid"]))

    @staticmethod
    def validate_auxiliary_tags(
        rows: Sequence[Mapping[str, Any]],
        *,
        supervisor_pid: int,
        supervisor_pgid: int,
        supervisor_sid: int,
    ) -> None:
        """Validate positive observations, but do not require a tag to exist."""

        for row in rows:
            pid = int(row["pid"])
            if row.get("uid") != os.getuid():
                raise ProcessBindingError("AUXILIARY_TAGGED_PROCESS_UID_MISMATCH")
            if pid != supervisor_pid and (
                row.get("pgid") != supervisor_pgid or row.get("sid") != supervisor_sid
            ):
                raise ProcessBindingError("AUXILIARY_TAGGED_PROCESS_DETACHED")

    def known_live_children(self, _pgid: int) -> List[int]:
        """Recheck only direct bindings; do not scan unrelated process groups."""

        live = []
        for binding in self.bindings:
            try:
                validate_process_binding(
                    binding,
                    expected_run_id=binding.run_id,
                    authorization_receipt=self.receipt,
                    authorization_receipt_sha256=self.receipt_sha256,
                )
            except ProcessBindingError as exc:
                if exc.code == "BOUND_PROCESS_EXITED":
                    continue
                continue
            live.append(binding.pid)
        return sorted(live)

    def install(self) -> None:
        self.supervisor._complete_launched_child_record = self.complete_record
        self.supervisor._validate_tagged_owned_set = self.validate_auxiliary_tags

    def restore(self) -> None:
        self.supervisor._complete_launched_child_record = self.original_complete
        self.supervisor._validate_tagged_owned_set = self.original_tag_validator


def run_bound_supervisor(
    *,
    spec_path: Path,
    receipt_path: Path,
    receipt_claim_directory: Path,
) -> int:
    """Preflight, claim once, then execute the hash-pinned production supervisor."""

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    receipt_raw = receipt_path.read_bytes()
    receipt = json.loads(receipt_raw.decode("utf-8"))
    receipt_sha256 = sha256_bytes(receipt_raw)
    validate_launch_authorization(spec, receipt, receipt_sha256=receipt_sha256)
    supervisor = _load_historical_supervisor()
    selftest = run_prelaunch_selftest(supervisor)
    if selftest.get("status") != "PASS":
        raise ProcessBindingError("M3E_PRELAUNCH_SUPERVISOR_SELFTEST_FAILED")
    claim_authorization_receipt(
        receipt,
        expected_run_id=str(spec["run_id"]),
        claim_directory=receipt_claim_directory,
        receipt_sha256=receipt_sha256,
    )
    adapter = _BindingAdapter(
        supervisor=supervisor,
        receipt=receipt,
        receipt_sha256=receipt_sha256,
    )
    adapter.install()
    try:
        return int(
            supervisor.run_supervisor(
                spec_path=spec_path,
                tagged_scan_fn=adapter.known_tag_scan,
                group_members_fn=adapter.known_live_children,
            )
        )
    finally:
        adapter.restore()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest-only", action="store_true")
    parser.add_argument("--selftest-output", type=Path)
    parser.add_argument("--run-spec", type=Path)
    parser.add_argument("--authorization-receipt", type=Path)
    parser.add_argument("--receipt-claim-directory", type=Path)
    args = parser.parse_args(argv)
    if args.selftest_only:
        result = run_prelaunch_selftest()
        if args.selftest_output:
            args.selftest_output.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        else:
            print(json.dumps(result, sort_keys=True))
        return 0
    if not args.run_spec or not args.authorization_receipt or not args.receipt_claim_directory:
        parser.error(
            "--run-spec, --authorization-receipt, and --receipt-claim-directory are required"
        )
    return run_bound_supervisor(
        spec_path=args.run_spec,
        receipt_path=args.authorization_receipt,
        receipt_claim_directory=args.receipt_claim_directory,
    )


if __name__ == "__main__":
    raise SystemExit(main())
