"""Fail-closed scene/event/G reachability check before agent exposure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from driveclarify_t_mvp.canonical import canonical_sha256
from driveclarify_t_mvp.route_authority import (
    load_route_authority_registry,
    resolve_route_authority_binding,
)


TIMING_SCHEMA = "driveclarify.rq2.prospective_timing_reachability.v1"


def validate_case(
    *,
    runtime_case: Mapping[str, Any],
    oracle_case: Mapping[str, Any],
    route_registry: Mapping[str, Any],
    timing_registry: Mapping[str, Any],
) -> Mapping[str, Any]:
    if timing_registry.get("schema_version") != TIMING_SCHEMA:
        raise RuntimeError("RQ2_TIMING_REACHABILITY_REGISTRY_SCHEMA_INVALID")
    if timing_registry.get("evaluator_only") is not True:
        raise RuntimeError("RQ2_TIMING_REACHABILITY_NOT_EVALUATOR_ONLY")
    if timing_registry.get("outcomes_or_controller_success_used") is not False:
        raise RuntimeError("RQ2_REACHABILITY_GATE_OUTCOME_CONTAMINATED")
    subset = str(runtime_case["route_subset"])
    binding = resolve_route_authority_binding(
        registry=route_registry,
        route_subset=subset,
        route_path=Path(str(runtime_case["route_path"])).resolve(),
    )
    if runtime_case.get("global_destination_identity") != binding.global_destination_identity:
        raise RuntimeError("RQ2_PREEXPOSURE_ROUTE_TO_G_IDENTITY_MISMATCH")
    try:
        authored = timing_registry["routes"][subset]
        bucket = authored["supported_buckets"][str(oracle_case["timing_bucket"])]
    except (KeyError, TypeError) as error:
        raise RuntimeError("RQ2_PLANNED_T_BUCKET_NOT_PRESENT_ON_AUTHORED_ROUTE") from error
    if oracle_case.get("trigger") != bucket.get("trigger"):
        raise RuntimeError("RQ2_ORACLE_TRIGGER_DIFFERS_FROM_AUTHORED_ROUTE_EVENT")
    if oracle_case.get("expected_oracle_event") != bucket.get("expected_oracle_event"):
        raise RuntimeError("RQ2_ORACLE_EVENT_IDENTITY_MISMATCH")
    ordinal = int(bucket["ordered_event_ordinal"])
    if ordinal >= int(authored["terminal_event_ordinal"]):
        raise RuntimeError("RQ2_PLANNED_T_BUCKET_NOT_BEFORE_AUTHORED_TERMINAL")
    sequence = authored.get("topology_sequence", [])
    if sum(int(item.get("event_ordinal", -1)) == ordinal for item in sequence) != 1:
        raise RuntimeError("RQ2_AUTHORED_EVENT_SEQUENCE_CARDINALITY_INVALID")
    truth_required = bucket.get("required_truth_class")
    truth = oracle_case.get("evaluator_truth_artifact")
    if truth_required is not None and not isinstance(truth, dict):
        raise RuntimeError("RQ2_T3_T4_PROSPECTIVE_TRUTH_MISSING")
    truth_sha256 = None
    if truth_required is not None:
        truth_path = Path(str(truth.get("absolute_path", ""))).resolve()
        if not truth_path.is_file():
            raise RuntimeError("RQ2_T3_T4_PROSPECTIVE_TRUTH_FILE_MISSING")
        truth_sha256 = hashlib.sha256(truth_path.read_bytes()).hexdigest()
        if truth_sha256 != truth.get("sha256"):
            raise RuntimeError("RQ2_T3_T4_PROSPECTIVE_TRUTH_HASH_MISMATCH")
        truth_payload = json.loads(truth_path.read_text(encoding="utf-8"))
        if (
            truth_payload.get("status") != "PASS"
            or truth_payload.get("case_id") != runtime_case.get("case_id")
            or truth_payload.get("truth_class") != truth_required
            or truth_payload.get("prospective_assertions", {}).get(
                "frozen_before_native_launch"
            )
            is not True
            or truth_payload.get("prospective_assertions", {}).get(
                "result_dependent_relabeling_allowed"
            )
            is not False
        ):
            raise RuntimeError("RQ2_T3_T4_PROSPECTIVE_TRUTH_INVALID")
    return {
        "schema_version": "driveclarify.rq2.preexposure_transition_validity.v1",
        "status": "PASS_PROSPECTIVE_EVENT_PRESENT",
        "case_id": runtime_case["case_id"],
        "route_subset": subset,
        "route_authority_binding_sha256": canonical_sha256(binding),
        "global_destination_identity": binding.global_destination_identity,
        "global_destination_endpoint_digest": binding.global_destination_endpoint_digest,
        "timing_bucket": oracle_case["timing_bucket"],
        "expected_oracle_event": bucket["expected_oracle_event"],
        "trigger": bucket["trigger"],
        "ordered_event_ordinal": ordinal,
        "terminal_event_ordinal": int(authored["terminal_event_ordinal"]),
        "required_truth_class": truth_required,
        "prospective_truth_sha256": truth_sha256,
        "learned_controller_success_required": False,
        "baseline_outcome_used": False,
    }


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--oracle-manifest", required=True)
    parser.add_argument("--route-authority-registry", required=True)
    parser.add_argument("--timing-reachability-registry", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    runtime = json.loads(Path(args.runtime_config).read_text(encoding="utf-8"))
    oracle = json.loads(Path(args.oracle_manifest).read_text(encoding="utf-8"))
    route_registry = load_route_authority_registry(
        Path(args.route_authority_registry).resolve()
    )
    timing_path = Path(args.timing_reachability_registry).resolve()
    timing_registry = json.loads(timing_path.read_text(encoding="utf-8"))
    receipt = dict(
        validate_case(
            runtime_case=runtime["cases"][args.case_id],
            oracle_case=oracle["cases"][args.case_id],
            route_registry=route_registry,
            timing_registry=timing_registry,
        )
    )
    receipt["runtime_config_sha256"] = hashlib.sha256(
        Path(args.runtime_config).read_bytes()
    ).hexdigest()
    receipt["oracle_manifest_sha256"] = hashlib.sha256(
        Path(args.oracle_manifest).read_bytes()
    ).hexdigest()
    receipt["timing_reachability_registry_sha256"] = hashlib.sha256(
        timing_path.read_bytes()
    ).hexdigest()
    receipt["canonical_sha256"] = canonical_sha256(receipt)
    _write_once(Path(args.output).resolve(), receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
