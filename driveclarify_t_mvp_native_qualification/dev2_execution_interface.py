"""Build the non-scientific DEV2 execution interface from an immutable roster.

This module never selects seeds, constructs a scientific cell, changes run order,
or launches CARLA.  It serializes the existing RQ2-V3-DEV2-RAR1 runtime/oracle
projection into the execution-ledger contract already enforced by the native
guard.  Three attempts mean the initial attempt plus at most two positively
classified pre-agent infrastructure retries; scientific retry budget stays zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from driveclarify_t_mvp.canonical import canonical_sha256
from driveclarify_t_mvp_native_qualification.admissibility_evidence import write_once
from driveclarify_t_mvp_native_qualification.preexecution_guard import MAX_PRE_AGENT_ATTEMPTS


PILOT_ID = "RQ2-V3-DEV2-RAR1"
MAX_PRE_AGENT_RETRIES = MAX_PRE_AGENT_ATTEMPTS - 1
SCIENTIFIC_RETRY_BUDGET = 0
EXECUTION_LEDGER_SCHEMA = "driveclarify.rq2.native_execution_ledger.v2"
EXPOSURE_REGISTRY_SCHEMA = "driveclarify.rq2.exposure_registry.v2"
PREEXECUTION_GATE_SCHEMA = "driveclarify.rq2.native_preexecution_gate.v2"
EXPECTED_SEEDS = frozenset({1306903733, 1027700338, 498608294})
EXPECTED_METHODS = frozenset({"T-B1", "T-B2", "T-B3", "T-B4", "T-B5", "T-B6"})
EXPECTED_BUCKETS = frozenset(
    {
        "T1_BEFORE_COMMITMENT",
        "T2_NEAR_COMMITMENT",
        "T3_POST_COMMIT_RECOVERABLE",
        "T4_NO_SAFE_CURRENT_OPPORTUNITY",
    }
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exact_int(value: Any, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(reason)
    return value


def validate_interface_documents(
    ledger: Mapping[str, Any], registry: Mapping[str, Any], gate: Mapping[str, Any]
) -> dict[str, bool]:
    """Strict pre-guard validation for the repaired serialized interface."""

    checks = {
        "ledger_schema": ledger.get("schema_version") == EXECUTION_LEDGER_SCHEMA,
        "registry_schema": registry.get("schema_version") == EXPOSURE_REGISTRY_SCHEMA,
        "gate_schema": gate.get("schema_version") == PREEXECUTION_GATE_SCHEMA,
        "execution_mode": ledger.get("execution_mode") == "RQ2_T_MVP_DEV",
        "ledger_immutable": ledger.get("immutable") is True,
        "post_agent_retry_forbidden": ledger.get("post_agent_retry_allowed") is False,
        "gate_scientific_retry_forbidden": gate.get("scientific_retries_allowed") is False,
        "gate_not_started": gate.get("native_execution_started") is False,
        "gate_status": gate.get("final_status") == "PASS_RQ2_DEV_PREEXECUTION_GATE",
    }
    numeric = {
        "maximum_native_probes": (ledger.get("maximum_native_probes"), 72),
        "maximum_pre_agent_attempts_per_probe": (
            ledger.get("maximum_pre_agent_attempts_per_probe"),
            MAX_PRE_AGENT_ATTEMPTS,
        ),
        "maximum_pre_agent_retries_per_probe": (
            ledger.get("maximum_pre_agent_retries_per_probe"),
            MAX_PRE_AGENT_RETRIES,
        ),
        "scientific_retry_budget_per_probe": (
            ledger.get("scientific_retry_budget_per_probe"),
            SCIENTIFIC_RETRY_BUDGET,
        ),
        "future_episode_count": (gate.get("future_episode_count"), 72),
        "pre_agent_identical_retry_ceiling": (
            gate.get("pre_agent_identical_retry_ceiling"),
            MAX_PRE_AGENT_ATTEMPTS,
        ),
        "maximum_pre_agent_retries_per_case": (
            gate.get("maximum_pre_agent_retries_per_case"),
            MAX_PRE_AGENT_RETRIES,
        ),
    }
    for name, (value, expected) in numeric.items():
        checks[name] = _exact_int(value, "EXECUTION_INTERFACE_TYPE_INVALID:" + name) == expected
    probes = ledger.get("probes")
    cases = registry.get("cases")
    checks["probe_count"] = isinstance(probes, list) and len(probes) == 72
    checks["registry_count"] = isinstance(cases, list) and len(cases) == 72
    if isinstance(probes, list):
        required = {
            "probe_id", "engineering_case_id", "baseline_id", "seed",
            "case_config_sha256", "runtime_config_sha256", "oracle_manifest_sha256",
            "injection_event_id", "update_event_id", "rpc_port", "run_ordinal", "status",
        }
        checks["probe_fields"] = all(required <= set(row) for row in probes)
        checks["probe_ordinals"] = all(
            _exact_int(row.get("run_ordinal"), "EXECUTION_INTERFACE_TYPE_INVALID:run_ordinal") == ordinal
            for ordinal, row in enumerate(probes, 1)
        )
        checks["probe_ports"] = all(
            _exact_int(row.get("rpc_port"), "EXECUTION_INTERFACE_TYPE_INVALID:rpc_port") == 2540
            for row in probes
        )
    if isinstance(cases, list):
        expected_eligibility = {
            "engineering_native": False,
            "t_mvp_dev": True,
            "t_formal_test": False,
            "future_rq3_formal": False,
        }
        checks["registry_eligibility"] = all(
            row.get("eligibility") == expected_eligibility for row in cases
        )
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise ValueError("EXECUTION_INTERFACE_VALIDATION_FAILED:" + ",".join(failures))
    return checks


def build_interface(
    *,
    roster_path: Path,
    order_path: Path,
    runtime_path: Path,
    oracle_path: Path,
    output_dir: Path,
    rpc_port: int = 2540,
    execution_authorized: bool = True,
) -> dict[str, Any]:
    roster_bytes = roster_path.read_bytes()
    order_bytes = order_path.read_bytes()
    runtime_bytes = runtime_path.read_bytes()
    oracle_bytes = oracle_path.read_bytes()
    roster = json.loads(roster_bytes)
    order = json.loads(order_bytes)
    runtime = json.loads(runtime_bytes)
    oracle = json.loads(oracle_bytes)

    if roster.get("pilot_id") != PILOT_ID or order.get("pilot_id") != PILOT_ID:
        raise ValueError("DEV2_PILOT_ID_MISMATCH")
    rows = order.get("run_order")
    cells = {row["case_id"]: row for row in roster.get("cells", [])}
    runtime_cases = runtime.get("cases", {})
    oracle_cases = oracle.get("cases", {})
    ordered_ids = [row["case_id"] for row in rows]
    if len(rows) != len(cells) or len(rows) != len(set(ordered_ids)) or len(rows) != 72:
        raise ValueError("DEV2_72_UNIQUE_CELLS_REQUIRED")
    if set(ordered_ids) != set(runtime_cases) or set(ordered_ids) != set(oracle_cases):
        raise ValueError("DEV2_RUNTIME_ORACLE_IDENTITY_JOIN_FAILED")
    if {int(row["seed"]) for row in rows} != EXPECTED_SEEDS:
        raise ValueError("DEV2_SEED_SET_MUTATED")
    if {row["method_id"] for row in rows} != EXPECTED_METHODS:
        raise ValueError("DEV2_METHOD_SET_MUTATED")
    if {row["timing_bucket"] for row in rows} != EXPECTED_BUCKETS:
        raise ValueError("DEV2_TIMING_SET_MUTATED")

    runtime_sha = hashlib.sha256(runtime_bytes).hexdigest()
    oracle_sha = hashlib.sha256(oracle_bytes).hexdigest()
    probes = []
    registry_cases = []
    for row in rows:
        case_id = row["case_id"]
        cell = cells[case_id]
        case = runtime_cases[case_id]
        event = oracle_cases[case_id]
        if not (
            int(row["run_ordinal"]) == int(cell["ordinal"])
            and row["method_id"] == cell["method_id"] == case["baseline_id"]
            and int(row["seed"]) == int(cell["seed"]) == int(case["seed"])
            and row["timing_bucket"] == cell["timing_bucket"] == event["timing_bucket"]
        ):
            raise ValueError("DEV2_CELL_EXECUTION_JOIN_FAILED:" + case_id)
        probes.append(
            {
                "probe_id": f"DEV2-{int(row['run_ordinal']):03d}",
                "engineering_case_id": case_id,
                "baseline_id": case["baseline_id"],
                "seed": int(case["seed"]),
                "case_config_sha256": canonical_sha256(case),
                "runtime_config_sha256": runtime_sha,
                "oracle_manifest_sha256": oracle_sha,
                "injection_event_id": event["injection_event_id"],
                "update_event_id": event["update_event_id"],
                "rpc_port": rpc_port,
                "run_ordinal": int(row["run_ordinal"]),
                "status": "PROSPECTIVELY_FROZEN_NOT_STARTED",
            }
        )
        registry_cases.append(
            {
                "engineering_case_id": case_id,
                "seed": int(case["seed"]),
                "eligibility": {
                    "engineering_native": False,
                    "t_mvp_dev": True,
                    "t_formal_test": False,
                    "future_rq3_formal": False,
                },
            }
        )

    ledger = {
        "schema_version": EXECUTION_LEDGER_SCHEMA,
        "pilot_id": PILOT_ID,
        "execution_mode": "RQ2_T_MVP_DEV",
        "immutable": True,
        "execution_authorized": execution_authorized,
        "maximum_native_probes": 72,
        "maximum_pre_agent_attempts_per_probe": MAX_PRE_AGENT_ATTEMPTS,
        "maximum_pre_agent_retries_per_probe": MAX_PRE_AGENT_RETRIES,
        "scientific_retry_budget_per_probe": SCIENTIFIC_RETRY_BUDGET,
        "post_agent_retry_allowed": False,
        "attempt_count_definition": "TOTAL_PRE_AGENT_LAUNCH_RESERVATIONS_INCLUDING_INITIAL_ATTEMPT",
        "retry_count_definition": "PRE_AGENT_ATTEMPTS_AFTER_THE_INITIAL_ATTEMPT_ONLY",
        "probes": probes,
    }
    registry = {
        "schema_version": EXPOSURE_REGISTRY_SCHEMA,
        "pilot_id": PILOT_ID,
        "immutable": True,
        "cases": registry_cases,
    }
    gate = {
        "schema_version": PREEXECUTION_GATE_SCHEMA,
        "pilot_id": PILOT_ID,
        "final_status": "PASS_RQ2_DEV_PREEXECUTION_GATE",
        "native_execution_started": False,
        "execution_authorized": execution_authorized,
        "future_episode_count": 72,
        "scientific_retries_allowed": False,
        "pre_agent_identical_retry_ceiling": MAX_PRE_AGENT_ATTEMPTS,
        "maximum_pre_agent_retries_per_case": MAX_PRE_AGENT_RETRIES,
        "guard_rejected_call_counts_as_attempt": False,
    }
    validation_checks = validate_interface_documents(ledger, registry, gate)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_once(output_dir / "DEV_EXECUTION_LEDGER.json", ledger)
    write_once(output_dir / "DEV_EXPOSURE_REGISTRY.json", registry)
    write_once(output_dir / "DEV_PREEXECUTION_GATE.json", gate)
    build_receipt = {
        "schema_version": "driveclarify.rq2.v3.dev2.execution_interface_build.v1",
        "pilot_id": PILOT_ID,
        "status": "PASS_DEV2_EXECUTION_INTERFACE_DERIVATION",
        "scientific_inputs_byte_unchanged": True,
        "input_sha256": {
            "roster": hashlib.sha256(roster_bytes).hexdigest(),
            "run_order": hashlib.sha256(order_bytes).hexdigest(),
            "runtime": runtime_sha,
            "oracle": oracle_sha,
        },
        "output_sha256": {
            "ledger": _sha256(output_dir / "DEV_EXECUTION_LEDGER.json"),
            "registry": _sha256(output_dir / "DEV_EXPOSURE_REGISTRY.json"),
            "gate": _sha256(output_dir / "DEV_PREEXECUTION_GATE.json"),
        },
        "cells": 72,
        "maximum_pre_agent_attempts_per_case": MAX_PRE_AGENT_ATTEMPTS,
        "maximum_pre_agent_retries_per_case": MAX_PRE_AGENT_RETRIES,
        "scientific_retries_per_case": SCIENTIFIC_RETRY_BUDGET,
        "method_specific_budget_variants": 0,
        "timing_specific_budget_variants": 0,
        "seed_specific_budget_variants": 0,
        "ordinal_position_budget_variants": 0,
        "validation_checks": validation_checks,
    }
    write_once(output_dir / "EXECUTION_INTERFACE_BUILD_RECEIPT.json", build_receipt)
    return build_receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roster", required=True, type=Path)
    parser.add_argument("--order", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--oracle", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--rpc-port", default=2540, type=int)
    args = parser.parse_args()
    receipt = build_interface(
        roster_path=args.roster,
        order_path=args.order,
        runtime_path=args.runtime,
        oracle_path=args.oracle,
        output_dir=args.output_dir,
        rpc_port=args.rpc_port,
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
