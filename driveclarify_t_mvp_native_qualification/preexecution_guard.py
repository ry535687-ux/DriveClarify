"""Fail-closed budget and admission guard for bounded RQ2 native probes.

This module is qualification infrastructure.  It does not import CARLA, the
evaluator, SimLingo, torch, or any driving implementation.  The launcher calls
``reserve`` before starting CARLA.  The qualification wrapper atomically claims
``agent-exposure`` only after its configuration and baseline selector have been
validated, immediately before the inner SimLingo setup/model load.  This keeps a
CARLA/evaluator death that never reaches agent setup eligible for the bounded
same-seed infrastructure retry required by the experiment protocol.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from driveclarify_t_mvp.admissibility import PreExposureStatus
from driveclarify_t_mvp_native_qualification.admissibility_evidence import (
    build_pre_exposure_validity_receipt,
    pre_exposure_receipt_to_mapping,
    write_once,
)


MAX_PRE_AGENT_ATTEMPTS = 3
ALLOWED_INFRA_FAILURES = frozenset(
    {
        "SOURCE_FREEZE_VERIFICATION_FAILED",
        "PHYSICAL_DISPLAY_GATE_FAILED",
        "VIRTUAL_DISPLAY_DETECTED",
        "PROBE_PORT_ALREADY_IN_USE",
        "CARLA_SERVER_STARTUP_FAILED",
        "CARLA_SERVER_DIED_BEFORE_AGENT_SETUP",
        "EVALUATOR_EXITED_BEFORE_AGENT_SETUP",
        "INJECTOR_STARTUP_FAILED",
    }
)


class GuardRejected(RuntimeError):
    """Raised before CARLA/model exposure when a launch is not admissible."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_state(handle: Any) -> list[dict[str, Any]]:
    handle.seek(0)
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(handle, 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as error:
            raise GuardRejected(f"STATE_LOG_INVALID_JSON_LINE:{number}") from error
        if not isinstance(row, dict):
            raise GuardRejected(f"STATE_LOG_ROW_NOT_OBJECT:{number}")
        rows.append(row)
    return rows


def _append_state(handle: Any, row: Mapping[str, Any]) -> None:
    handle.seek(0, os.SEEK_END)
    handle.write(_canonical_bytes(dict(row)).decode("utf-8") + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def _probe(ledger: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    probes = ledger.get("probes")
    if not isinstance(probes, list):
        raise GuardRejected("LEDGER_PROBES_INVALID")
    maximum = int(ledger.get("maximum_native_probes", -1))
    mode = str(ledger.get("execution_mode", "ENGINEERING_SMOKE"))
    valid_budget = (
        (mode == "ENGINEERING_SMOKE" and 1 <= maximum <= 8)
        or (mode == "RQ2_T_MVP_DEV" and maximum == 72)
    )
    if not valid_budget or len(probes) != maximum:
        raise GuardRejected("LEDGER_NATIVE_EPISODE_BUDGET_INVALID")
    matches = [row for row in probes if row.get("engineering_case_id") == case_id]
    if len(matches) != 1:
        raise GuardRejected("UNKNOWN_OR_DUPLICATE_PROBE")
    return matches[0]


def _validate_static_contract(
    *,
    ledger_path: Path,
    registry_path: Path,
    runtime_config_path: Path,
    oracle_manifest_path: Path,
    gate_receipt_path: Path,
    case_id: str,
    rpc_port: int,
    include_pre_exposure: bool = False,
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | tuple[Mapping[str, Any], Mapping[str, Any], Any]:
    ledger = _load_json(ledger_path)
    registry = _load_json(registry_path)
    runtime_all = _load_json(runtime_config_path)
    oracle_all = _load_json(oracle_manifest_path)
    gate = _load_json(gate_receipt_path)

    if ledger.get("immutable") is not True:
        raise GuardRejected("LEDGER_NOT_IMMUTABLE")
    if ledger.get("post_agent_retry_allowed") is not False:
        raise GuardRejected("LEDGER_POST_AGENT_RETRY_NOT_DISABLED")
    if int(ledger.get("maximum_pre_agent_attempts_per_probe", -1)) != MAX_PRE_AGENT_ATTEMPTS:
        raise GuardRejected("LEDGER_PRE_AGENT_RETRY_BUDGET_INVALID")
    mode = str(ledger.get("execution_mode", "ENGINEERING_SMOKE"))
    expected_gate = (
        "PASS_NATIVE_ENGINEERING_PREEXECUTION_GATE"
        if mode == "ENGINEERING_SMOKE"
        else "PASS_RQ2_DEV_PREEXECUTION_GATE"
    )
    if gate.get("final_status") != expected_gate:
        raise GuardRejected("PREEXECUTION_GATE_NOT_PASS")
    if gate.get("native_execution_started") is not False:
        raise GuardRejected("PREEXECUTION_GATE_RECEIPT_EXPOSURE_STATE_INVALID")

    probe = _probe(ledger, case_id)
    cases = runtime_all.get("cases", {})
    if case_id not in cases:
        raise GuardRejected("CASE_MISSING_FROM_RUNTIME_CONFIG")
    case = cases[case_id]
    oracle_cases = oracle_all.get("cases", {})
    if case_id not in oracle_cases:
        raise GuardRejected("CASE_MISSING_FROM_ORACLE_MANIFEST")
    oracle = oracle_cases[case_id]
    registry_cases = registry.get("cases", [])
    registered = [row for row in registry_cases if row.get("engineering_case_id") == case_id]
    if len(registered) != 1:
        raise GuardRejected("CASE_MISSING_OR_DUPLICATE_IN_QUARANTINE")
    eligibility = registered[0].get("eligibility", {})
    expected_eligibility = {
        "engineering_native": mode == "ENGINEERING_SMOKE",
        "t_mvp_dev": mode == "RQ2_T_MVP_DEV",
        "t_formal_test": False,
        "future_rq3_formal": False,
    }
    if eligibility != expected_eligibility:
        raise GuardRejected("ENGINEERING_QUARANTINE_ELIGIBILITY_INVALID")

    exact = {
        "seed": int(case.get("seed", -1)),
        "baseline_id": case.get("baseline_id"),
        "case_config_sha256": _canonical_sha256(case),
        "runtime_config_sha256": _file_sha256(runtime_config_path),
        "oracle_manifest_sha256": _file_sha256(oracle_manifest_path),
        "injection_event_id": oracle.get("injection_event_id"),
        "update_event_id": oracle.get("update_event_id"),
        "rpc_port": int(rpc_port),
    }
    for key, actual in exact.items():
        if probe.get(key) != actual:
            raise GuardRejected("PROBE_IDENTITY_MISMATCH:" + key)

    pre_exposure = build_pre_exposure_validity_receipt(
        roster_item={
            "case_id": case_id,
            "episode_id": case.get("episode_id"),
            "ordinal": probe.get("run_ordinal", probe.get("ordinal", 0)),
            "seed": case.get("seed"),
            "method_id": case.get("baseline_id"),
            "timing_bucket": oracle.get("timing_bucket"),
        },
        runtime_case=case,
        oracle_case=oracle,
    )
    if include_pre_exposure:
        return probe, case, pre_exposure
    return probe, case


def evaluate_eligibility(
    *,
    probe: Mapping[str, Any],
    case: Mapping[str, Any],
    case_id: str,
    attempt_number: int,
    rows: list[Mapping[str, Any]],
) -> None:
    if attempt_number < 1 or attempt_number > MAX_PRE_AGENT_ATTEMPTS:
        raise GuardRejected("PRE_AGENT_ATTEMPT_NUMBER_OUT_OF_BUDGET")
    relevant = [row for row in rows if row.get("probe_id") == probe.get("probe_id")]
    exposures = [row for row in relevant if row.get("event_type") == "AGENT_EXPOSURE"]
    if exposures:
        raise GuardRejected("POST_AGENT_EXPOSURE_RETRY_FORBIDDEN")
    attempts = [row for row in relevant if row.get("event_type") == "PRE_AGENT_INFRA_ATTEMPT"]
    failures = [row for row in relevant if row.get("event_type") == "PRE_AGENT_INFRA_FAILURE"]
    if len(attempts) >= MAX_PRE_AGENT_ATTEMPTS:
        raise GuardRejected("PRE_AGENT_ATTEMPT_BUDGET_EXHAUSTED")
    if len(attempts) != len(failures):
        raise GuardRejected("PREVIOUS_ATTEMPT_NOT_POSITIVELY_CLOSED_AS_INFRA_FAILURE")
    if attempt_number != len(attempts) + 1:
        raise GuardRejected("ATTEMPT_NUMBER_NOT_NEXT_EXACT_ATTEMPT")
    for row in relevant:
        if row.get("engineering_case_id") != case_id:
            raise GuardRejected("STATE_CASE_IDENTITY_DRIFT")
        if int(row.get("seed", -1)) != int(case["seed"]):
            raise GuardRejected("STATE_SEED_IDENTITY_DRIFT")
        if row.get("case_config_sha256") != probe.get("case_config_sha256"):
            raise GuardRejected("STATE_CONFIG_IDENTITY_DRIFT")
        if row.get("injection_event_id") != probe.get("injection_event_id"):
            raise GuardRejected("STATE_INJECTION_IDENTITY_DRIFT")


def reserve(args: argparse.Namespace) -> str:
    validated = _validate_static_contract(
        ledger_path=Path(args.ledger).resolve(),
        registry_path=Path(args.registry).resolve(),
        runtime_config_path=Path(args.runtime_config).resolve(),
        oracle_manifest_path=Path(args.oracle_manifest).resolve(),
        gate_receipt_path=Path(args.gate_receipt).resolve(),
        case_id=args.case_id,
        rpc_port=args.rpc_port,
        include_pre_exposure=True,
    )
    probe, case, pre_exposure = validated
    output = getattr(args, "pre_exposure_validity_output", None)
    if output:
        write_once(
            Path(output).resolve(),
            pre_exposure_receipt_to_mapping(pre_exposure),
        )
    if pre_exposure.status != PreExposureStatus.VALID.value:
        raise GuardRejected(
            "PRE_EXPOSURE_INVALID:" + ",".join(pre_exposure.failure_reasons)
        )
    state_path = Path(args.state_log).resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        rows = _load_state(handle)
        evaluate_eligibility(
            probe=probe,
            case=case,
            case_id=args.case_id,
            attempt_number=args.attempt_number,
            rows=rows,
        )
        reservation_id = hashlib.sha256(
            _canonical_bytes(
                {
                    "probe_id": probe["probe_id"],
                    "attempt_number": args.attempt_number,
                    "prior_event_count": len(rows),
                    "monotonic_ns": time.monotonic_ns(),
                }
            )
        ).hexdigest()
        _append_state(
            handle,
            {
                "schema_version": "driveclarify.rq2.native_probe_state.v2",
                "event_type": "PRE_AGENT_INFRA_ATTEMPT",
                "probe_id": probe["probe_id"],
                "engineering_case_id": args.case_id,
                "seed": int(case["seed"]),
                "case_config_sha256": probe["case_config_sha256"],
                "injection_event_id": probe["injection_event_id"],
                "pre_exposure_validity_sha256": pre_exposure.canonical_sha256,
                "attempt_number": args.attempt_number,
                "reservation_id": reservation_id,
            },
        )
    return reservation_id


def _transition(args: argparse.Namespace, event_type: str) -> None:
    state_path = Path(args.state_log).resolve()
    if not state_path.is_file():
        raise GuardRejected("STATE_LOG_MISSING")
    with state_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        rows = _load_state(handle)
        attempts = [
            row
            for row in rows
            if row.get("reservation_id") == args.reservation_id
            and row.get("event_type") == "PRE_AGENT_INFRA_ATTEMPT"
        ]
        if len(attempts) != 1:
            raise GuardRejected("RESERVATION_NOT_EXACTLY_ONCE")
        attempt = attempts[0]
        terminals = [
            row
            for row in rows
            if row.get("reservation_id") == args.reservation_id
            and row.get("event_type") in {"PRE_AGENT_INFRA_FAILURE", "AGENT_EXPOSURE"}
        ]
        if terminals:
            raise GuardRejected("RESERVATION_ALREADY_TERMINAL")
        row = dict(attempt)
        row["event_type"] = event_type
        if event_type == "PRE_AGENT_INFRA_FAILURE":
            if args.reason_code not in ALLOWED_INFRA_FAILURES:
                raise GuardRejected("INFRA_FAILURE_REASON_NOT_POSITIVELY_ALLOWLISTED")
            row["reason_code"] = args.reason_code
        _append_state(handle, row)


def claim_agent_exposure(
    *, state_log: Path, reservation_id: str, case_id: str, seed: int
) -> None:
    if not state_log.is_file():
        raise GuardRejected("AGENT_ADMISSION_STATE_LOG_MISSING")
    with state_log.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        rows = _load_state(handle)
        attempts = [
            row
            for row in rows
            if row.get("reservation_id") == reservation_id
            and row.get("event_type") == "PRE_AGENT_INFRA_ATTEMPT"
        ]
        terminals = [
            row
            for row in rows
            if row.get("reservation_id") == reservation_id
            and row.get("event_type") in {"PRE_AGENT_INFRA_FAILURE", "AGENT_EXPOSURE"}
        ]
        if len(attempts) != 1 or terminals:
            raise GuardRejected("AGENT_EXPOSURE_RESERVATION_NOT_CLAIMABLE")
        attempt = attempts[0]
        if attempt.get("engineering_case_id") != case_id:
            raise GuardRejected("AGENT_ADMISSION_CASE_MISMATCH")
        if int(attempt.get("seed", -1)) != int(seed):
            raise GuardRejected("AGENT_ADMISSION_SEED_MISMATCH")
        exposure = dict(attempt)
        exposure["event_type"] = "AGENT_EXPOSURE"
        exposure["claimed_by"] = (
            "DriveClarifyTMVPNativeQualificationAgent.setup_after_selector_validation"
        )
        _append_state(handle, exposure)


def validate_agent_admission(
    *, state_log: Path, reservation_id: str, case_id: str, seed: int
) -> None:
    """Compatibility read check for already-claimed admissions."""

    if not state_log.is_file():
        raise GuardRejected("AGENT_ADMISSION_STATE_LOG_MISSING")
    with state_log.open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        rows = _load_state(handle)
    exposure = [row for row in rows if row.get("reservation_id") == reservation_id and row.get("event_type") == "AGENT_EXPOSURE"]
    if len(exposure) != 1:
        raise GuardRejected("AGENT_EXPOSURE_ADMISSION_NOT_EXACTLY_ONCE")
    if exposure[0].get("engineering_case_id") != case_id or int(exposure[0].get("seed", -1)) != int(seed):
        raise GuardRejected("AGENT_ADMISSION_IDENTITY_MISMATCH")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    reserve_parser = sub.add_parser("reserve")
    for name in ("ledger", "registry", "runtime-config", "oracle-manifest", "gate-receipt", "state-log"):
        reserve_parser.add_argument("--" + name, required=True)
    reserve_parser.add_argument("--case-id", required=True)
    reserve_parser.add_argument("--rpc-port", required=True, type=int)
    reserve_parser.add_argument("--attempt-number", required=True, type=int)
    reserve_parser.add_argument("--pre-exposure-validity-output")
    for command in ("agent-exposure", "infra-failure"):
        item = sub.add_parser(command)
        item.add_argument("--state-log", required=True)
        item.add_argument("--reservation-id", required=True)
        if command == "infra-failure":
            item.add_argument("--reason-code", required=True)
    admission = sub.add_parser("validate-agent-admission")
    admission.add_argument("--state-log", required=True)
    admission.add_argument("--reservation-id", required=True)
    admission.add_argument("--case-id", required=True)
    admission.add_argument("--seed", required=True, type=int)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "reserve":
            print(reserve(args))
        elif args.command == "agent-exposure":
            _transition(args, "AGENT_EXPOSURE")
        elif args.command == "infra-failure":
            _transition(args, "PRE_AGENT_INFRA_FAILURE")
        else:
            validate_agent_admission(
                state_log=Path(args.state_log).resolve(),
                reservation_id=args.reservation_id,
                case_id=args.case_id,
                seed=args.seed,
            )
    except (GuardRejected, KeyError, OSError, ValueError) as error:
        print("PREEXECUTION_GUARD_REJECTED:" + str(error), file=os.sys.stderr)
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
