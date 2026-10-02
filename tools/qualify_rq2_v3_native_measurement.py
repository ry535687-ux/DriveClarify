"""Post-execution-only native evidence join for the frozen RQ2 V3 endpoint."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from driveclarify_t_mvp.canonical import canonical_sha256
from driveclarify_t_mvp.global_task_preservation_v3 import (
    ConjunctName,
    GlobalTaskBinding,
    create_v3_preservation_receipt,
    evaluate_same_g_identity,
    frozen_predicate_conjunct,
    receipt_to_mapping as v3_receipt_to_mapping,
    validate_v3_preservation_receipt,
)
from driveclarify_t_mvp_native_qualification.admissibility_evidence import (
    build_cell_admissibility_from_evidence,
    receipt_to_mapping as cell_receipt_to_mapping,
    write_cell_receipt_once,
)
from driveclarify_t_mvp_native_qualification.rq2_dev_postprocess import (
    _episode_metrics,
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _reference(path: Path, repository_root: Path) -> str:
    return str(path.resolve().relative_to(repository_root.resolve()))


def _bundle(
    paths: Iterable[Path], *, producer: str, repository_root: Path
) -> dict[str, Any]:
    rows = [
        {"reference": _reference(path, repository_root), "sha256": _sha(path)}
        for path in paths
    ]
    value = {"producer": producer, "inputs": rows}
    return {**value, "canonical_sha256": canonical_sha256(value)}


def _global_task(value: Mapping[str, Any]) -> tuple[str | None, str | None]:
    return (
        value.get("global_destination_identity"),
        value.get("destination_endpoint_digest"),
    )


def _known_resolution(value: bool) -> str:
    return "SUCCESS" if value else "FAILURE"


def evaluate(report: Path, repository_root: Path) -> dict[str, Any]:
    roster = _load(report / "ENGINEERING_PROBE_ROSTER.json")
    item = roster["case"]
    case_id = item["case_id"]
    runtime_path = report / "engineering/runtime_cases.json"
    oracle_path = report / "engineering/oracle_injection_manifest.json"
    runtime = _load(runtime_path)["cases"][case_id]
    oracle = _load(oracle_path)["cases"][case_id]
    attempts = sorted((report / "raw/dev" / case_id).glob("attempt_*"))
    exposed = [
        path for path in attempts
        if _load(path / "process_job/PROCESS_RECEIPT.json").get("agent_exposed") is True
    ]
    if len(exposed) != 1:
        raise RuntimeError(f"EXPECTED_EXACTLY_ONE_EXPOSED_ATTEMPT:{len(exposed)}")
    root = exposed[0]
    evidence = root / "agent_evidence"

    cell = build_cell_admissibility_from_evidence(
        roster_item=item,
        runtime_case=runtime,
        oracle_case=oracle,
        exposed_attempt=root,
        oracle_manifest_path=oracle_path,
        repository_root=repository_root,
    )
    cell_path = report / "V3_NATIVE_RECEIPTS/CELL_ADMISSIBILITY_RECEIPT.json"
    write_cell_receipt_once(cell_path, cell)
    cell_mapping = cell_receipt_to_mapping(cell)

    required = {
        "p_old": evidence / "native_p_old.json",
        "prepare_install": evidence / "t_b2_prepare_install.json",
        "consumption": evidence / "t_b2_consumption.json",
        "transition_receipt": evidence / "transition_receipt.json",
        "transition_events": evidence / "transition_events.jsonl",
        "injection": evidence / "evaluator/injection_receipt.json",
        "process": root / "process_job/PROCESS_RECEIPT.json",
        "registry": repository_root
        / "driveclarify_t_mvp_native_qualification/g_terminal_regions_v2.json",
        "frozen_rejoin_producer": repository_root
        / "driveclarify_t_mvp_native_qualification/rq2_dev_postprocess.py",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise RuntimeError("NATIVE_REQUIRED_ARTIFACTS_MISSING:" + ",".join(missing))
    boundaries = sorted((evidence / "boundaries").glob("boundary_*.json"))
    if not boundaries:
        raise RuntimeError("NATIVE_BOUNDARY_TRACE_MISSING")

    p_old = _load(required["p_old"])["p_old"]
    prepared = _load(required["prepare_install"])
    candidate = prepared["candidate"]
    install = prepared["install_receipt"]
    consume = _load(required["consumption"])
    transition = _load(required["transition_receipt"])
    registry = _load(required["registry"])["regions"][
        runtime["global_destination_identity"]
    ]
    boundary_rows = [_load(path) for path in boundaries]
    injection = _load(required["injection"])["receipt"]
    injection_frame = int(injection["actual_injection_frame"])
    consumption_frame = int(consume["frame"])
    last = boundary_rows[-1]

    task_sources = {
        "episode": p_old["global_task"],
        "p_old": p_old["global_task"],
        "p_new": candidate["global_task"],
        "installed_authority": install["global_task_after"],
        "consumed_authority": candidate["global_task"],
        "evaluator": {
            "global_destination_identity": registry["semantic_destination_identity"],
            "destination_endpoint_digest": next(
                row["endpoint_digest"]
                for row in registry["representations"]
                if row["role"] == "AUTHORITATIVE_RUNTIME_G"
            ),
        },
    }
    bindings = []
    for role, source in task_sources.items():
        identity, endpoint = _global_task(source)
        bindings.append(GlobalTaskBinding(role, identity, endpoint))

    same_g_bundle = _bundle(
        [
            required["p_old"], required["prepare_install"],
            required["consumption"], required["transition_receipt"],
            required["registry"],
        ],
        producer="tools.qualify_rq2_v3_native_measurement.exact_g_v2_join",
        repository_root=repository_root,
    )
    same_g = evaluate_same_g_identity(
        bindings,
        required_roles=tuple(task_sources),
        evidence_source="NATIVE_EXACT_G_V2_AUTHORITY_CHAIN",
        evidence_reference="V3_NATIVE_RECEIPTS/NATIVE_PREDICATE_OUTPUTS.json#same_g",
        evidence_digest=same_g_bundle["canonical_sha256"],
        producer=same_g_bundle["producer"],
        source_frame_start=injection_frame,
        source_frame_end=consumption_frame,
    )

    all_exact_g = (
        same_g.value is True
        and prepared.get("destination_semantic_identity_preserved") is True
        and prepared.get("destination_terminal_region_equivalent") is True
        and transition.get("integrity_assertions", {}).get("same_G") is True
    )
    no_wrong_bundle = _bundle(
        [required["prepare_install"], required["transition_receipt"], required["registry"]],
        producer="tools.qualify_rq2_v3_native_measurement.positive_no_substitution_join",
        repository_root=repository_root,
    )

    physical = prepared["identity_binding"]["physical_route_identity"]
    lifecycle = [row["state"] for row in consume["candidate_lifecycle"]]
    authority_ok = (
        lifecycle == [
            "CANDIDATE_PREPARED", "CANDIDATE_ADMITTED",
            "CANDIDATE_COMMITTED", "CANDIDATE_CONSUMED",
        ]
        and consumption_frame > int(install["frame"])
        and consume["physical_active_route_identity"] == physical
        and consume["physical_consumed_route_identity"] == physical
        and consume["identity_binding"] == prepared["identity_binding"]
        and transition.get("integrity_assertions", {}).get(
            "install_does_not_imply_consumption"
        ) is True
    )
    authority_bundle = _bundle(
        [required["prepare_install"], required["consumption"], required["transition_events"], required["transition_receipt"]],
        producer="tools.qualify_rq2_v3_native_measurement.authority_consumption_join",
        repository_root=repository_root,
    )

    downstream = [
        row for row in boundary_rows
        if int(row["sim_frame"]) > consumption_frame
        and int(row["road_id"]) == int(registry["road_id"])
        and int(row["section_id"]) == int(registry["section_id"])
        and int(row["lane_id"]) == int(registry["lane_id"])
    ]
    frames = [int(row["sim_frame"]) for row in boundary_rows]
    frame_order_valid = all(b > a for a, b in zip(frames, frames[1:]))
    continuation_ok = (
        all_exact_g
        and authority_ok
        and candidate.get("feasibility_state") == "FEASIBLE_NOW"
        and candidate.get("candidate_generator_owner") == "agent.T_B2_NATIVE_PREPARER"
        and prepared.get("destination_terminal_region_equivalent") is True
        and prepared.get("destination_terminal_region", {}).get("certificate_sha256")
        == registry.get("certificate_sha256")
        and frame_order_valid
        and bool(downstream)
    )
    if continuation_ok:
        continuation_resolution = "SUCCESS"
    elif float(last["sim_time_s"]) >= 35.0:
        continuation_resolution = "RIGHT_CENSORED"
    else:
        continuation_resolution = "UNKNOWN"
    continuation_paths = [
        required["prepare_install"], required["consumption"], required["transition_events"],
        required["registry"], *boundaries,
    ]
    continuation_bundle = _bundle(
        continuation_paths,
        producer="tools.qualify_rq2_v3_native_measurement.actual_authority_topology_join",
        repository_root=repository_root,
    )

    frozen_metrics, frozen_provenance = _episode_metrics(root, runtime, oracle)
    recovery_resolution = str(frozen_metrics["recovery_success"])
    rejoin_raw = frozen_metrics["rejoin_success"]
    rejoin_resolution = (
        "SUCCESS" if rejoin_raw is True else "FAILURE" if rejoin_raw is False
        else str(rejoin_raw)
    )
    frozen_bundle = _bundle(
        [required["frozen_rejoin_producer"], required["injection"], *boundaries],
        producer=(
            "driveclarify_t_mvp_native_qualification.rq2_dev_postprocess."
            "_episode_metrics"
        ),
        repository_root=repository_root,
    )

    predicate_output = {
        "schema_version": "driveclarify.rq2.v3_native_predicate_outputs.v1",
        "case_id": case_id,
        "post_execution_only": True,
        "runtime_policy_access": False,
        "same_g": {"resolution": _known_resolution(all_exact_g), "bundle": same_g_bundle},
        "no_wrong_destination_substitution": {
            "resolution": _known_resolution(all_exact_g), "bundle": no_wrong_bundle,
        },
        "legal_topology_aware_continuation": {
            "resolution": continuation_resolution,
            "bundle": continuation_bundle,
            "actual_downstream_certificate_lane_first_frame": (
                None if not downstream else downstream[0]["sim_frame"]
            ),
            "actual_boundary_count": len(boundary_rows),
            "last_boundary_frame": last["sim_frame"],
            "last_boundary_time_s": last["sim_time_s"],
            "generic_graph_reachability_used": False,
            "new_numeric_threshold_used": False,
        },
        "route_authority_change_later_consumed": {
            "resolution": _known_resolution(authority_ok), "bundle": authority_bundle,
            "install_frame": install["frame"], "consumption_frame": consumption_frame,
        },
        "frozen_recovery": {
            "resolution": recovery_resolution, "bundle": frozen_bundle,
        },
        "frozen_rejoin": {
            "resolution": rejoin_resolution, "bundle": frozen_bundle,
        },
        "frozen_producer_provenance": frozen_provenance,
    }
    predicate_output["canonical_sha256"] = canonical_sha256(predicate_output)
    predicate_path = report / "V3_NATIVE_RECEIPTS/NATIVE_PREDICATE_OUTPUTS.json"
    _write_once(predicate_path, predicate_output)

    def predicate(
        name: ConjunctName, key: str, *, frame_start: int, frame_end: int
    ):
        row = predicate_output[key]
        return frozen_predicate_conjunct(
            name,
            resolution=row["resolution"],
            evidence_source="NATIVE_POST_EXECUTION_PREDICATE_OUTPUT",
            evidence_reference=_reference(predicate_path, repository_root) + "#" + key,
            evidence_digest=row["bundle"]["canonical_sha256"],
            producer=row["bundle"]["producer"],
            source_frame_start=frame_start,
            source_frame_end=frame_end,
            source_time_start_s=float(injection["simulation_timestamp_s"]),
            source_time_end_s=float(last["sim_time_s"]),
        )

    conjuncts = (
        same_g,
        predicate(ConjunctName.NO_WRONG_DESTINATION_SUBSTITUTION, "no_wrong_destination_substitution", frame_start=injection_frame, frame_end=consumption_frame),
        predicate(ConjunctName.AUTHORITATIVE_LEGAL_TOPOLOGY_AWARE_CONTINUATION, "legal_topology_aware_continuation", frame_start=injection_frame, frame_end=int(last["sim_frame"])),
        predicate(ConjunctName.ROUTE_AUTHORITY_CHANGE_LATER_CONSUMED, "route_authority_change_later_consumed", frame_start=int(install["frame"]), frame_end=consumption_frame),
        predicate(ConjunctName.APPLICABLE_FROZEN_RECOVERY, "frozen_recovery", frame_start=injection_frame, frame_end=int(last["sim_frame"])),
        predicate(ConjunctName.APPLICABLE_FROZEN_REJOIN, "frozen_rejoin", frame_start=injection_frame, frame_end=int(last["sim_frame"])),
    )
    receipt = create_v3_preservation_receipt(
        cell_admissibility_receipt=cell_mapping,
        cell_admissibility_reference=_reference(cell_path, repository_root),
        conjuncts=conjuncts,
    )
    result = v3_receipt_to_mapping(receipt)
    validate_v3_preservation_receipt(result)
    receipt_path = report / "V3_NATIVE_RECEIPTS/V3_PRESERVATION_RECEIPT.json"
    _write_once(receipt_path, result)
    return {
        "case_id": case_id,
        "analysis_eligible": cell_mapping["analysis_eligible"],
        "preservation_state": result["preservation_state"],
        "preservation_value": result["preservation_value"],
        "canonical_digest": result["canonical_digest"],
        "conjuncts": [asdict(row) for row in conjuncts],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    result = evaluate(Path(args.report).resolve(), Path(args.repository_root).resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
