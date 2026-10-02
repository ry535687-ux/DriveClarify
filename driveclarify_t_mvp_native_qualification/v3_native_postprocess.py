"""Method-neutral post-execution native evidence join for frozen RQ2 V3.

This module is evaluator-only.  It reads immutable native artifacts after an
episode and never imports or mutates the runtime agent, controller, planner, or
PID.  Missing evidence remains UNKNOWN/RIGHT_CENSORED; it is never inferred as
success from the absence of a failure log.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from driveclarify_t_mvp.canonical import canonical_sha256
from driveclarify_t_mvp.global_task_preservation_v3 import (
    ConjunctName,
    EvidenceState,
    GlobalTaskBinding,
    create_v3_preservation_receipt,
    evaluate_same_g_identity,
    frozen_predicate_conjunct,
    receipt_to_mapping as v3_receipt_to_mapping,
    unresolved_conjunct,
    validate_v3_preservation_receipt,
)
from driveclarify_t_mvp_native_qualification.rq2_dev_postprocess import (
    _episode_metrics,
)


PRODUCER = "driveclarify_t_mvp_native_qualification.v3_native_postprocess"


def _load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reference(path: Path, repository_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(repository_root.resolve()))
    except ValueError:
        return str(resolved)


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise FileExistsError("V3_NATIVE_WRITE_ONCE_MISMATCH:" + str(path))
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _bundle(
    paths: Iterable[Path], *, role: str, repository_root: Path
) -> dict[str, Any]:
    unique = sorted({path.resolve() for path in paths if path.is_file()})
    rows = [
        {"reference": _reference(path, repository_root), "sha256": _sha(path)}
        for path in unique
    ]
    value = {
        "producer": PRODUCER + "." + role,
        "inputs": rows,
    }
    return {**value, "canonical_sha256": canonical_sha256(value)}


def _task_pair(value: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    value = value or {}
    return (
        value.get("global_destination_identity"),
        value.get("destination_endpoint_digest"),
    )


def _candidate_install_consumption(
    method: str, evidence: Path
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, Mapping[str, Any] | None, list[Path]]:
    paths: list[Path] = []
    candidate: Mapping[str, Any] | None = None
    install: Mapping[str, Any] | None = None
    consumption: Mapping[str, Any] | None = None

    dispatch_names = {
        "T-B1": "t_b1_dispatch.json",
        "T-B2": "t_b2_prepare_install.json",
        "T-B3": "common_detached_candidate.json",
        "T-B4": "t_b4_dispatch.json",
        "T-B5": "t_b5_pre_forward.json",
        "T-B6": "t_b6_dispatch.json",
    }
    dispatch_path = evidence / dispatch_names[method]
    dispatch = _load(dispatch_path, {})
    if dispatch_path.is_file():
        paths.append(dispatch_path)
    candidate = dispatch.get("candidate")

    if method == "T-B2":
        install = dispatch.get("install_receipt")
        consumption_path = evidence / "t_b2_consumption.json"
    else:
        install = dispatch.get("install_receipt")
        consumption_path = evidence / "baseline_consumption.json"

    if method == "T-B3":
        terminal_path = evidence / "t_b3_old_terminal.json"
        terminal = _load(terminal_path, {})
        if terminal_path.is_file():
            paths.append(terminal_path)
        install = terminal.get("install_receipt")
    elif method == "T-B6" and not install:
        for reevaluation_path in sorted(
            (evidence / "t_b6_reevaluations").glob("reevaluation_*.json")
        ):
            paths.append(reevaluation_path)
            reevaluation = _load(reevaluation_path, {})
            if reevaluation.get("install_receipt"):
                install = reevaluation["install_receipt"]
                break

    if consumption_path.is_file():
        paths.append(consumption_path)
        consumption = _load(consumption_path, {})
    return candidate, install, consumption, paths


def _authority_consumed(
    *, install: Mapping[str, Any], consumption: Mapping[str, Any] | None
) -> bool | None:
    if install.get("committed") is not True:
        return False
    if not isinstance(consumption, Mapping):
        return None
    lifecycle = [
        row.get("state")
        for row in consumption.get("candidate_lifecycle", [])
        if isinstance(row, Mapping)
    ]
    expected = [
        "CANDIDATE_PREPARED",
        "CANDIDATE_ADMITTED",
        "CANDIDATE_COMMITTED",
        "CANDIDATE_CONSUMED",
    ]
    frame = consumption.get("frame")
    install_frame = install.get("frame")
    return bool(
        lifecycle == expected
        and isinstance(frame, int)
        and isinstance(install_frame, int)
        and frame > install_frame
        and consumption.get("physical_active_route_identity")
        == consumption.get("physical_consumed_route_identity")
    )


def _resolution(value: Any) -> str:
    if value is True:
        return "SUCCESS"
    if value is False:
        return "FAILURE"
    if value in {"NOT_APPLICABLE", "UNKNOWN", "RIGHT_CENSORED"}:
        return str(value)
    return "UNKNOWN"


def _criterion_values(safety: Mapping[str, Any]) -> tuple[dict[str, bool], bool]:
    values: dict[str, bool] = {}
    for row in safety.get("criteria", []):
        if row.get("criterion_name") not in {
            "collision",
            "route_deviation",
            "outside_route_lanes",
        }:
            continue
        if row.get("truth_state") in {"KNOWN_TRUE", "KNOWN_FALSE"}:
            values[str(row["criterion_name"])] = bool(row.get("value"))
    return values, len(values) == 3


def _unresolved_receipt(
    *,
    cell: Mapping[str, Any],
    cell_reference: str,
    reason: str,
) -> dict[str, Any]:
    conjuncts = tuple(
        unresolved_conjunct(
            name,
            state=EvidenceState.UNKNOWN,
            reason_code=reason,
        )
        for name in (
            ConjunctName.SAME_G_IDENTITY,
            ConjunctName.NO_WRONG_DESTINATION_SUBSTITUTION,
            ConjunctName.AUTHORITATIVE_LEGAL_TOPOLOGY_AWARE_CONTINUATION,
            ConjunctName.ROUTE_AUTHORITY_CHANGE_LATER_CONSUMED,
            ConjunctName.APPLICABLE_FROZEN_RECOVERY,
            ConjunctName.APPLICABLE_FROZEN_REJOIN,
        )
    )
    receipt = create_v3_preservation_receipt(
        cell_admissibility_receipt=cell,
        cell_admissibility_reference=cell_reference,
        conjuncts=conjuncts,
    )
    result = v3_receipt_to_mapping(receipt)
    validate_v3_preservation_receipt(result)
    return result


def evaluate_native_v3(
    *,
    roster_item: Mapping[str, Any],
    runtime_case: Mapping[str, Any],
    oracle_case: Mapping[str, Any],
    cell_admissibility_receipt: Mapping[str, Any],
    cell_admissibility_path: Path,
    exposed_attempt: Path,
    oracle_manifest_path: Path,
    predicate_output_path: Path,
    receipt_output_path: Path,
    repository_root: Path,
    g_registry_path: Path | None = None,
) -> dict[str, Any]:
    """Build one canonical V3 receipt from post-execution native evidence."""

    del oracle_manifest_path  # The truth join is already sealed in CellAdmissibility.
    cell = dict(cell_admissibility_receipt)
    cell_reference = _reference(cell_admissibility_path, repository_root)
    if cell.get("analysis_eligible") is not True:
        result = _unresolved_receipt(
            cell=cell,
            cell_reference=cell_reference,
            reason="CELL_ANALYSIS_INELIGIBLE",
        )
        _write_once(receipt_output_path, result)
        return result

    evidence = exposed_attempt / "agent_evidence"
    method = str(roster_item["method_id"])
    p_old_path = evidence / "native_p_old.json"
    injection_path = evidence / "evaluator" / "injection_receipt.json"
    safety_path = exposed_attempt / "safety_endpoint_receipt.json"
    process_path = exposed_attempt / "process_job" / "PROCESS_RECEIPT.json"
    boundary_paths = sorted((evidence / "boundaries").glob("boundary_*.json"))
    if not p_old_path.is_file() or not injection_path.is_file() or not boundary_paths:
        result = _unresolved_receipt(
            cell=cell,
            cell_reference=cell_reference,
            reason="V3_REQUIRED_NATIVE_EVIDENCE_MISSING",
        )
        _write_once(receipt_output_path, result)
        return result

    g_registry_path = (
        repository_root
        / "driveclarify_t_mvp_native_qualification/g_terminal_regions_v3.json"
        if g_registry_path is None
        else g_registry_path
    )
    registry = _load(g_registry_path, {}).get("regions", {}).get(
        runtime_case.get("global_destination_identity"), {}
    )
    representations = registry.get("representations", [])
    evaluator_digest = next(
        (
            row.get("endpoint_digest")
            for row in representations
            if row.get("role") == "AUTHORITATIVE_RUNTIME_G"
        ),
        None,
    )
    p_old = _load(p_old_path, {}).get("p_old", {})
    candidate, install, consumption, method_paths = _candidate_install_consumption(
        method, evidence
    )
    install = install if isinstance(install, Mapping) else None
    consumption = consumption if isinstance(consumption, Mapping) else None
    authority_changed = bool(install and install.get("committed") is True)
    consumed = (
        _authority_consumed(install=install, consumption=consumption)
        if authority_changed and install is not None
        else None
    )

    task_sources: dict[str, Mapping[str, Any]] = {
        "episode": {
            "global_destination_identity": runtime_case.get(
                "global_destination_identity"
            ),
            "destination_endpoint_digest": evaluator_digest,
        },
        "p_old": p_old.get("global_task", {}),
        "evaluator": {
            "global_destination_identity": registry.get(
                "semantic_destination_identity"
            ),
            "destination_endpoint_digest": evaluator_digest,
        },
    }
    if isinstance(candidate, Mapping):
        task_sources["p_new"] = candidate.get("global_task", {})
    if authority_changed and install is not None:
        task_sources["installed_authority"] = install.get("global_task_after", {})
        if consumption is not None and isinstance(candidate, Mapping):
            task_sources["consumed_authority"] = candidate.get("global_task", {})
        elif consumption is None:
            task_sources["consumed_authority"] = {}

    bindings = [
        GlobalTaskBinding(role, *_task_pair(source))
        for role, source in task_sources.items()
    ]
    base_paths = [p_old_path, injection_path, g_registry_path, *method_paths]
    same_g_bundle = _bundle(
        base_paths, role="exact_g_v2_join", repository_root=repository_root
    )
    injection = _load(injection_path, {}).get("receipt", {})
    injection_frame = int(injection["actual_injection_frame"])
    injection_time = float(injection["simulation_timestamp_s"])
    same_g = evaluate_same_g_identity(
        bindings,
        required_roles=tuple(task_sources),
        evidence_source="NATIVE_EXACT_G_V2_AUTHORITY_CHAIN",
        evidence_reference=_reference(predicate_output_path, repository_root)
        + "#same_g",
        evidence_digest=same_g_bundle["canonical_sha256"],
        producer=same_g_bundle["producer"],
        source_frame_start=injection_frame,
        source_frame_end=(
            int(consumption["frame"])
            if consumption is not None and isinstance(consumption.get("frame"), int)
            else injection_frame
        ),
        source_time_start_s=injection_time,
    )

    boundary_rows = [_load(path, {}) for path in boundary_paths]
    last = boundary_rows[-1]
    last_frame = int(last["sim_frame"])
    last_time = float(last["sim_time_s"])
    frame_order_valid = all(
        int(right["sim_frame"]) > int(left["sim_frame"])
        for left, right in zip(boundary_rows, boundary_rows[1:])
    )
    terminal_lane_rows = [
        row
        for row in boundary_rows
        if int(row.get("sim_frame", -1)) >= injection_frame
        and int(row.get("road_id", -999999)) == int(registry.get("road_id", -1))
        and int(row.get("section_id", -999999))
        == int(registry.get("section_id", -1))
        and int(row.get("lane_id", -999999)) == int(registry.get("lane_id", -1))
    ]
    safety = _load(safety_path, {})
    safety_values, safety_complete = _criterion_values(safety)
    safety_violation = any(safety_values.values()) if safety_complete else None
    process = _load(process_path, {})
    endpoint_complete = safety.get("snapshot_complete") is True

    if same_g.value is False or safety_violation is True or consumed is False:
        continuation_resolution = "FAILURE"
    elif (
        same_g.value is True
        and safety_violation is False
        and frame_order_valid
        and bool(terminal_lane_rows)
        and (not authority_changed or consumed is True)
    ):
        continuation_resolution = "SUCCESS"
    elif endpoint_complete and safety.get("endpoint_type") == "FORCED_35S":
        continuation_resolution = "RIGHT_CENSORED"
    else:
        continuation_resolution = "UNKNOWN"

    if not authority_changed:
        authority_resolution = "NOT_APPLICABLE"
    elif consumed is True:
        authority_resolution = "SUCCESS"
    elif consumed is False:
        authority_resolution = "FAILURE"
    elif endpoint_complete and safety.get("endpoint_type") == "FORCED_35S":
        authority_resolution = "RIGHT_CENSORED"
    elif process.get("terminal_class") == "SCIENTIFIC_EVALUATOR_TERMINAL":
        authority_resolution = "FAILURE"
    else:
        authority_resolution = "UNKNOWN"

    frozen_metrics, frozen_provenance = _episode_metrics(
        exposed_attempt, dict(runtime_case), dict(oracle_case)
    )
    recovery_resolution = _resolution(frozen_metrics["recovery_success"])
    rejoin_resolution = _resolution(frozen_metrics["rejoin_success"])
    no_wrong_resolution = (
        "SUCCESS"
        if same_g.value is True
        else "FAILURE"
        if same_g.value is False
        else "UNKNOWN"
    )

    continuation_bundle = _bundle(
        [*base_paths, safety_path, process_path, *boundary_paths],
        role="actual_authority_topology_join",
        repository_root=repository_root,
    )
    authority_bundle = _bundle(
        [*base_paths, process_path],
        role="authority_consumption_join",
        repository_root=repository_root,
    )
    frozen_bundle = _bundle(
        [
            repository_root
            / "driveclarify_t_mvp_native_qualification/rq2_dev_postprocess.py",
            injection_path,
            *boundary_paths,
        ],
        role="frozen_recovery_rejoin_join",
        repository_root=repository_root,
    )
    predicate_output = {
        "schema_version": "driveclarify.rq2.v3_native_predicate_outputs.v2",
        "case_id": roster_item["case_id"],
        "method": method,
        "post_execution_only": True,
        "runtime_policy_access": False,
        "same_g": {
            "resolution": _resolution(same_g.value),
            "bundle": same_g_bundle,
            "roles": list(task_sources),
        },
        "no_wrong_destination_substitution": {
            "resolution": no_wrong_resolution,
            "bundle": same_g_bundle,
        },
        "legal_topology_aware_continuation": {
            "resolution": continuation_resolution,
            "bundle": continuation_bundle,
            "actual_boundary_count": len(boundary_rows),
            "actual_terminal_certificate_lane_first_frame": (
                None if not terminal_lane_rows else terminal_lane_rows[0]["sim_frame"]
            ),
            "safety_criteria_complete": safety_complete,
            "safety_violation": safety_violation,
            "generic_graph_reachability_used": False,
            "new_numeric_threshold_used": False,
        },
        "route_authority_change_later_consumed": {
            "resolution": authority_resolution,
            "bundle": authority_bundle,
            "authority_changed": authority_changed,
            "later_consumption": consumed,
        },
        "frozen_recovery": {
            "resolution": recovery_resolution,
            "bundle": frozen_bundle,
        },
        "frozen_rejoin": {
            "resolution": rejoin_resolution,
            "bundle": frozen_bundle,
        },
        "frozen_producer_provenance": frozen_provenance,
        "endpoint": {
            "type": safety.get("endpoint_type", "UNKNOWN"),
            "simulation_time_s": safety.get("simulation_time_s"),
            "last_boundary_frame": last_frame,
            "last_boundary_time_s": last_time,
        },
    }
    predicate_output["canonical_sha256"] = canonical_sha256(predicate_output)
    _write_once(predicate_output_path, predicate_output)

    def predicate(name: ConjunctName, key: str, bundle: Mapping[str, Any]):
        return frozen_predicate_conjunct(
            name,
            resolution=str(predicate_output[key]["resolution"]),
            evidence_source="NATIVE_POST_EXECUTION_PREDICATE_OUTPUT",
            evidence_reference=_reference(predicate_output_path, repository_root)
            + "#"
            + key,
            evidence_digest=str(bundle["canonical_sha256"]),
            producer=str(bundle["producer"]),
            source_frame_start=injection_frame,
            source_frame_end=last_frame,
            source_time_start_s=injection_time,
            source_time_end_s=last_time,
        )

    conjuncts = (
        same_g,
        predicate(
            ConjunctName.NO_WRONG_DESTINATION_SUBSTITUTION,
            "no_wrong_destination_substitution",
            same_g_bundle,
        ),
        predicate(
            ConjunctName.AUTHORITATIVE_LEGAL_TOPOLOGY_AWARE_CONTINUATION,
            "legal_topology_aware_continuation",
            continuation_bundle,
        ),
        predicate(
            ConjunctName.ROUTE_AUTHORITY_CHANGE_LATER_CONSUMED,
            "route_authority_change_later_consumed",
            authority_bundle,
        ),
        predicate(
            ConjunctName.APPLICABLE_FROZEN_RECOVERY,
            "frozen_recovery",
            frozen_bundle,
        ),
        predicate(
            ConjunctName.APPLICABLE_FROZEN_REJOIN,
            "frozen_rejoin",
            frozen_bundle,
        ),
    )
    receipt = create_v3_preservation_receipt(
        cell_admissibility_receipt=cell,
        cell_admissibility_reference=cell_reference,
        conjuncts=conjuncts,
    )
    result = v3_receipt_to_mapping(receipt)
    validate_v3_preservation_receipt(result)
    _write_once(receipt_output_path, result)
    return result


def result_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "case_id": receipt["case_id"],
        "method": receipt["method"],
        "cell_analysis_eligible": receipt["cell_analysis_eligible"],
        "preservation_state": receipt["preservation_state"],
        "preservation_value": receipt["preservation_value"],
        "first_unresolved_or_failure_conjunct": receipt[
            "first_unresolved_or_failure_conjunct"
        ],
        "canonical_digest": receipt["canonical_digest"],
        "conjuncts": [asdict(row) if not isinstance(row, dict) else row for row in receipt["conjuncts"]],
    }
