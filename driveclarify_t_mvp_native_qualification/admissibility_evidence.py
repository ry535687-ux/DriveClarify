"""Filesystem verifier for the frozen RQ2 cell-admissibility contract."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import inspect
import json
import os
from pathlib import Path
from typing import Any, Mapping

from driveclarify_t_mvp.admissibility import (
    CELL_ADMISSIBILITY_SCHEMA,
    FROZEN_ADMISSIBILITY_CONTRACT_DIGEST,
    METHOD_OBLIGATION_CONTRACTS,
    TRUTH_JOIN_REQUIREMENTS,
    CellAdmissibilityReceipt,
    EvidenceReference,
    ExpectedInjectionIdentity,
    PreExposureValidityReceipt,
    PreExposureStatus,
    StageVerification,
    create_cell_admissibility_receipt,
    receipt_to_mapping,
    validate_cell_admissibility_receipt,
)
from driveclarify_t_mvp.canonical import canonical_sha256
from driveclarify_t_mvp_native_qualification.receipt_encoding import (
    validate_frozen_admissibility_receipt,
)


def _load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, repository_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(repository_root.resolve()))
    except ValueError:
        return str(resolved)


def _reference(
    path: Path, evidence_kind: str, repository_root: Path
) -> EvidenceReference:
    return EvidenceReference(
        relative_path=_relative(path, repository_root),
        sha256=_file_sha256(path),
        evidence_kind=evidence_kind,
    )


def _truth_required(bucket: str) -> bool:
    return not TRUTH_JOIN_REQUIREMENTS[bucket].startswith("NOT_REQUIRED")


def _truth_preexposure_checks(
    *,
    case_id: str,
    seed: int,
    bucket: str,
    oracle: Mapping[str, Any],
) -> tuple[list[tuple[str, bool]], list[str]]:
    checks: list[tuple[str, bool]] = []
    reasons: list[str] = []
    reference = oracle.get("evaluator_truth_artifact")
    required = _truth_required(bucket)
    checks.append(("truth_requirement_matches_timing_bucket", required == (reference is not None)))
    if required:
        path = Path(str((reference or {}).get("absolute_path", ""))).resolve()
        exists = path.is_file()
        checks.append(("required_evaluator_truth_artifact_exists", exists))
        hash_ok = exists and _file_sha256(path) == (reference or {}).get("sha256")
        checks.append(("required_evaluator_truth_artifact_hash_matches", hash_ok))
        proof = _load(path, {}) if exists else {}
        expected_class = TRUTH_JOIN_REQUIREMENTS[bucket]
        proof_ok = (
            proof.get("case_id") == case_id
            and int(proof.get("seed", -1)) == int(seed)
            and proof.get("status") == "PASS"
            and proof.get("truth_class") == expected_class
            and proof.get("prospective_assertions", {}).get(
                "frozen_before_native_launch"
            )
            is True
            and proof.get("prospective_assertions", {}).get(
                "result_dependent_relabeling_allowed"
            )
            is False
            and proof.get("prospective_assertions", {}).get(
                "native_behavior_can_change_truth_class"
            )
            is False
        )
        checks.append(("required_evaluator_truth_is_prospective_and_exact", proof_ok))
    if not all(passed for _, passed in checks):
        reasons.append("REQUIRED_PROSPECTIVE_TRUTH_ARTIFACT_INVALID")
    return checks, reasons


def build_pre_exposure_validity_receipt(
    *,
    roster_item: Mapping[str, Any],
    runtime_case: Mapping[str, Any],
    oracle_case: Mapping[str, Any],
) -> PreExposureValidityReceipt:
    """Validate only outcome-independent conditions knowable before exposure."""

    case_id = str(roster_item.get("case_id", ""))
    episode_id = str(roster_item.get("episode_id", ""))
    method = str(roster_item.get("method_id", ""))
    bucket = str(roster_item.get("timing_bucket", ""))
    seed = int(roster_item.get("seed", -1))
    checks = [
        ("case_identity_join", runtime_case.get("case_id") == case_id),
        ("episode_identity_runtime_join", runtime_case.get("episode_id") == episode_id),
        ("episode_identity_oracle_join", oracle_case.get("episode_id") == episode_id),
        ("seed_runtime_join", int(runtime_case.get("seed", -2)) == seed),
        ("exact_baseline_mapping_exists", method in METHOD_OBLIGATION_CONTRACTS),
        ("baseline_selector_join", runtime_case.get("baseline_id") == method),
        ("exact_timing_bucket_exists", bucket in TRUTH_JOIN_REQUIREMENTS),
        ("prospective_timing_bucket_join", oracle_case.get("timing_bucket") == bucket),
        ("expected_injection_event_wired", bool(oracle_case.get("injection_event_id"))),
        ("expected_update_event_wired", bool(oracle_case.get("update_event_id"))),
        ("expected_oracle_event_wired", bool(oracle_case.get("expected_oracle_event"))),
        (
            "semantic_update_config_present",
            bool(oracle_case.get("instruction_old"))
            and bool(oracle_case.get("instruction_new"))
            and bool(runtime_case.get("updated_branch_identity")),
        ),
        (
            "route_and_task_binding_present",
            bool(runtime_case.get("route_path"))
            and Path(str(runtime_case.get("route_path", ""))).is_file()
            and bool(runtime_case.get("route_subset"))
            and bool(runtime_case.get("global_destination_identity")),
        ),
        (
            "canonical_receipt_writer_available",
            Path(inspect.getsourcefile(CellAdmissibilityReceipt) or "").is_file(),
        ),
    ]
    reasons: list[str] = []
    for name, passed in checks:
        if not passed:
            reasons.append(name.upper())
    if bucket in TRUTH_JOIN_REQUIREMENTS:
        truth_checks, truth_reasons = _truth_preexposure_checks(
            case_id=case_id,
            seed=seed,
            bucket=bucket,
            oracle=oracle_case,
        )
        checks.extend(truth_checks)
        reasons.extend(truth_reasons)
    frozen_config_digest = canonical_sha256(
        {
            "roster_identity": {
                "case_id": case_id,
                "episode_id": episode_id,
                "ordinal": roster_item.get("ordinal"),
                "seed": seed,
                "method": method,
                "timing_bucket": bucket,
            },
            "runtime_case": runtime_case,
            "oracle_case": oracle_case,
            "method_contract": METHOD_OBLIGATION_CONTRACTS.get(method),
            "truth_requirement": TRUTH_JOIN_REQUIREMENTS.get(bucket),
            "admissibility_contract_digest": FROZEN_ADMISSIBILITY_CONTRACT_DIGEST,
        }
    )
    return PreExposureValidityReceipt.create(
        case_id=case_id,
        episode_id=episode_id,
        seed=seed,
        method=method,
        timing_bucket=bucket,
        checks=checks,
        failure_reasons=tuple(dict.fromkeys(reasons)),
        frozen_config_contract_digest=frozen_config_digest,
    )


def _boundary_sim_time(evidence: Path, frame: int | None) -> float | None:
    if frame is None:
        return None
    row = _load(evidence / "boundaries" / f"boundary_{frame:08d}.json", {})
    value = row.get("sim_time_s")
    return None if value is None else float(value)


def _verify_injection(
    *,
    evidence: Path,
    case_id: str,
    episode_id: str,
    bucket: str,
    oracle: Mapping[str, Any],
    repository_root: Path,
) -> StageVerification:
    injection_path = evidence / "evaluator" / "injection_receipt.json"
    if not injection_path.is_file():
        return StageVerification(False, False, defect_reason="INJECTION_RECEIPT_ABSENT")
    references = [_reference(injection_path, "EVALUATOR_INJECTION_RECEIPT", repository_root)]
    value = _load(injection_path, {})
    receipt = value.get("receipt", {})
    runtime_path = evidence / "exchange" / "runtime_update.json"
    runtime = _load(runtime_path, {})
    if runtime_path.is_file():
        references.append(_reference(runtime_path, "AGENT_RUNTIME_UPDATE_RECEIPT", repository_root))
    frame_value = receipt.get("actual_injection_frame")
    time_value = receipt.get("simulation_timestamp_s")
    frame = int(frame_value) if isinstance(frame_value, int) and frame_value >= 0 else None
    sim_time = (
        float(time_value)
        if isinstance(time_value, (int, float)) and float(time_value) >= 0.0
        else None
    )
    valid = (
        value.get("schema_version") == "driveclarify.rq2.native_evaluator_injection.v1"
        and receipt.get("case_id") == case_id
        and receipt.get("episode_id") == episode_id
        and receipt.get("bucket") == bucket
        and receipt.get("injection_event_id") == oracle.get("injection_event_id")
        and receipt.get("update_event_id") == oracle.get("update_event_id")
        and receipt.get("expected_oracle_event") == oracle.get("expected_oracle_event")
        and receipt.get("injected_before_policy") is True
        and frame is not None
        and sim_time is not None
        and runtime.get("case_id") == case_id
        and runtime.get("episode_id") == episode_id
        and runtime.get("update_event_id") == oracle.get("update_event_id")
        and runtime.get("actual_injection_frame") == frame
        and runtime.get("simulation_timestamp_s") == time_value
    )
    return StageVerification(
        True,
        valid,
        tuple(references),
        sim_frame=frame,
        sim_time_s=sim_time,
        defect_reason=None if valid else "INJECTION_IDENTITY_OR_RUNTIME_JOIN_INVALID",
    )


def _verify_dispatch(
    *,
    evidence: Path,
    method: str,
    expected_update_event_id: str,
    injection_frame: int | None,
    repository_root: Path,
) -> StageVerification:
    path = evidence / "native_dispatch_exercised.json"
    if not path.is_file():
        return StageVerification(False, False, defect_reason="NATIVE_DISPATCH_RECEIPT_ABSENT")
    value = _load(path, {})
    contract = METHOD_OBLIGATION_CONTRACTS[method]
    expected_class = contract.exact_native_policy.rsplit(".", 1)[-1]
    frame_value = value.get("sim_frame")
    frame = int(frame_value) if isinstance(frame_value, int) and frame_value >= 0 else None
    binding = value.get("dispatch_binding", {})
    valid = (
        value.get("baseline_id") == method
        and value.get("exact_instance_type") == contract.exact_native_policy
        and value.get("module") == "driveclarify_t_mvp.baselines"
        and value.get("resolved_class") == expected_class
        and value.get("fallback_used") is False
        and value.get("update_event_id") == expected_update_event_id
        and frame is not None
        and (injection_frame is None or frame >= injection_frame)
        and binding.get("baseline_id") == method
        and binding.get("resolved") is True
        and binding.get("resolved_class_or_function") == expected_class
        and binding.get("semantic_identity") == contract.semantic_identity
    )
    return StageVerification(
        True,
        valid,
        (_reference(path, "EXACT_NATIVE_DISPATCH_RECEIPT", repository_root),),
        sim_frame=frame,
        sim_time_s=_boundary_sim_time(evidence, frame),
        defect_reason=None if valid else "EXACT_NATIVE_DISPATCH_EVIDENCE_INVALID",
    )


def _lifecycle_consumed(rows: Any) -> bool:
    return isinstance(rows, list) and any(
        isinstance(row, Mapping) and row.get("state") == "CANDIDATE_CONSUMED"
        for row in rows
    )


def _method_obligation_valid(
    *,
    evidence: Path,
    method: str,
    expected_update_event_id: str,
) -> tuple[bool, tuple[tuple[Path, str], ...]]:
    if method == "T-B1":
        dispatch_path = evidence / "t_b1_dispatch.json"
        consumption_path = evidence / "baseline_consumption.json"
        dispatch = _load(dispatch_path, {})
        consumption = _load(consumption_path, {})
        valid = (
            dispatch.get("resolved_instance_type")
            == METHOD_OBLIGATION_CONTRACTS[method].exact_native_policy
            and dispatch.get("install_receipt", {}).get("committed") is True
            and consumption.get("baseline_id") == method
            and _lifecycle_consumed(consumption.get("candidate_lifecycle"))
            and consumption.get("one_normal_forward_per_wrapper_cycle") is True
            and int(consumption.get("wrapper_control_writes", -1)) == 0
        )
        return valid, (
            (dispatch_path, "T_B1_EXACT_INSTALL_RECEIPT"),
            (consumption_path, "T_B1_NEXT_NORMAL_FORWARD_CONSUMPTION"),
        )
    if method == "T-B2":
        prepare_path = evidence / "t_b2_prepare_install.json"
        consumption_path = evidence / "t_b2_consumption.json"
        transition_path = evidence / "transition_receipt.json"
        prepare = _load(prepare_path, {})
        consumption = _load(consumption_path, {})
        accounting = prepare.get("accounting_after_install", {})
        valid = (
            prepare.get("install_receipt", {}).get("committed") is True
            and int(accounting.get("global_planner_call_count", -1)) == 1
            and int(accounting.get("local_planner_call_count", -1)) == 1
            and int(accounting.get("route_installation_count", -1)) == 1
            and _lifecycle_consumed(consumption.get("candidate_lifecycle"))
            and bool(_load(transition_path, {}).get("consumption_event_sequences"))
        )
        return valid, (
            (prepare_path, "T_B2_DETACHED_PREPARATION_AND_INSTALL"),
            (consumption_path, "T_B2_NEXT_NORMAL_FORWARD_CONSUMPTION"),
            (transition_path, "T_B2_TRANSITION_RECEIPT"),
        )
    if method == "T-B3":
        dispatch_path = evidence / "t_b3_dispatch.json"
        dispatch = _load(dispatch_path, {})
        valid = (
            dispatch.get("resolved_instance_type")
            == METHOD_OBLIGATION_CONTRACTS[method].exact_native_policy
            and dispatch.get("p_old_preserved") is True
            and dispatch.get("no_rescue_search") is True
            and dispatch.get("decision", {}).get("baseline_id") == method
            and dispatch.get("decision", {}).get("outcome")
            == "WAIT_OLD_MANEUVER_TERMINAL"
        )
        return valid, ((dispatch_path, "T_B3_P_OLD_WAIT_NO_RESCUE"),)
    if method == "T-B4":
        dispatch_path = evidence / "t_b4_dispatch.json"
        consumption_path = evidence / "t_b4_local_consumption.json"
        dispatch = _load(dispatch_path, {})
        consumption = _load(consumption_path, {})
        valid = (
            dispatch.get("resolved_instance_type")
            == METHOD_OBLIGATION_CONTRACTS[method].exact_native_policy
            and int(dispatch.get("global_planner_call_count", -1)) == 0
            and int(dispatch.get("reconnect_count", -1)) == 0
            and dispatch.get("global_route_identity_before")
            == dispatch.get("global_route_identity_after")
            and dispatch.get("global_route_generation_before")
            == dispatch.get("global_route_generation_after")
            and consumption.get("global_route_identity_unchanged") is True
            and int(consumption.get("control_writer_count_added", -1)) == 0
            and consumption.get("update_event_id") == expected_update_event_id
        )
        return valid, (
            (dispatch_path, "T_B4_ZERO_GLOBAL_ACTIVITY_DISPATCH"),
            (consumption_path, "T_B4_LOCAL_CONSUMPTION"),
        )
    if method == "T-B5":
        pre_path = evidence / "t_b5_pre_forward.json"
        post_path = evidence / "t_b5_post_forward.json"
        pre = _load(pre_path, {})
        post = _load(post_path, {})
        visible = set(pre.get("history_payload", {}).get("visible_field_manifest", []))
        valid = (
            pre.get("decision", {}).get("baseline_id") == method
            and pre.get("decision", {}).get("outcome") == "HISTORY_PAYLOAD_READY"
            and pre.get("update_event_id") == expected_update_event_id
            and visible
            == {"legitimate_native_observation_history", "old_instruction", "new_instruction"}
            and pre.get("binding", {}).get("custom_prompt_installed") is True
            and int(pre.get("binding", {}).get("model_forward_count", -1)) == 0
            and post.get("custom_prompt_still_exact") is True
            and post.get("custom_prompt_embedded_in_inner_prompt") is True
            and post.get("model_object_identity_unchanged") is True
            and int(post.get("normal_forward_count_this_wrapper_cycle", -1)) == 1
        )
        return valid, (
            (pre_path, "T_B5_LEAK_FREE_HISTORY_ONLY_PAYLOAD"),
            (post_path, "T_B5_NATIVE_FORWARD_CONSUMPTION"),
        )
    dispatch_path = evidence / "t_b6_dispatch.json"
    dispatch = _load(dispatch_path, {})
    frozen = dispatch.get("frozen_admissibility")
    frozen_valid = False
    if isinstance(frozen, Mapping):
        try:
            validate_frozen_admissibility_receipt(frozen)
            frozen_valid = True
        except (KeyError, TypeError, ValueError):
            frozen_valid = False
    valid = (
        dispatch.get("resolved_instance_type")
        == METHOD_OBLIGATION_CONTRACTS[method].exact_native_policy
        and frozen_valid
        and dispatch.get("oracle_fields_present") is False
        and dispatch.get("runtime_config_has_t_bucket") is False
        and dispatch.get("authored_commitment_point_index") is None
        and dispatch.get("default_thresholds_only") is True
        and dispatch.get("decision", {}).get("baseline_id") == method
        and bool(dispatch.get("decision", {}).get("outcome"))
    )
    return valid, ((dispatch_path, "T_B6_CANONICAL_FROZEN_TRANSITION_RECEIPT"),)


def _verify_obligation(
    *,
    evidence: Path,
    method: str,
    expected_update_event_id: str,
    dispatch_frame: int | None,
    repository_root: Path,
) -> StageVerification:
    path = evidence / "native_obligation_complete.json"
    if not path.is_file():
        return StageVerification(False, False, defect_reason="NATIVE_OBLIGATION_RECEIPT_ABSENT")
    value = _load(path, {})
    frame_value = value.get("sim_frame")
    frame = int(frame_value) if isinstance(frame_value, int) and frame_value >= 0 else None
    completion_kinds = {
        "T-B1": "NEXT_NORMAL_FORWARD_CONSUMED_INSTALLED_CANDIDATE",
        "T-B2": "NEXT_NORMAL_FORWARD_CONSUMED_INSTALLED_CANDIDATE",
        "T-B3": "FROZEN_FINISH_OLD_FIRST_WAIT_ACTIVE",
        "T-B4": "NEXT_NORMAL_FORWARD_CONSUMED_LOCAL_REPLACEMENT",
        "T-B5": "NEXT_NORMAL_FORWARD_CONSUMED_HISTORY_ONLY_PROMPT",
        "T-B6": "FROZEN_B6_DECISION_PATH_AND_REEVALUATION_REACHED",
    }
    method_valid, method_paths = _method_obligation_valid(
        evidence=evidence,
        method=method,
        expected_update_event_id=expected_update_event_id,
    )
    paths_exist = all(item.is_file() for item, _ in method_paths)
    references = [_reference(path, "NATIVE_OBLIGATION_COMPLETION", repository_root)]
    references.extend(
        _reference(item, kind, repository_root)
        for item, kind in method_paths
        if item.is_file()
    )
    valid = (
        value.get("baseline_id") == method
        and value.get("completion_kind") == completion_kinds[method]
        and value.get("update_event_id") == expected_update_event_id
        and frame is not None
        and (dispatch_frame is None or frame >= dispatch_frame)
        and paths_exist
        and method_valid
    )
    return StageVerification(
        True,
        valid,
        tuple(references),
        sim_frame=frame,
        sim_time_s=_boundary_sim_time(evidence, frame),
        defect_reason=None if valid else "METHOD_SPECIFIC_OBLIGATION_EVIDENCE_INVALID",
    )


def _verify_truth_join(
    *,
    evidence: Path,
    case_id: str,
    seed: int,
    bucket: str,
    oracle: Mapping[str, Any],
    repository_root: Path,
    oracle_manifest_path: Path,
) -> StageVerification:
    injection_path = evidence / "evaluator" / "injection_receipt.json"
    if not _truth_required(bucket):
        return StageVerification(
            True,
            True,
            (_reference(oracle_manifest_path, "TRUTH_JOIN_NOT_REQUIRED_AUTHORITY", repository_root),),
        )
    expected = oracle.get("evaluator_truth_artifact") or {}
    truth_path = Path(str(expected.get("absolute_path", ""))).resolve()
    injection = _load(injection_path, {})
    actual = injection.get("evaluator_truth_artifact") or {}
    truth = _load(truth_path, {})
    references = []
    if truth_path.is_file():
        references.append(_reference(truth_path, "PROSPECTIVE_EVALUATOR_TRUTH", repository_root))
    if injection_path.is_file():
        references.append(_reference(injection_path, "INJECTION_TRUTH_JOIN_RECEIPT", repository_root))
    expected_class = TRUTH_JOIN_REQUIREMENTS[bucket]
    valid = (
        truth_path.is_file()
        and _file_sha256(truth_path) == expected.get("sha256")
        and truth.get("case_id") == case_id
        and int(truth.get("seed", -1)) == int(seed)
        and truth.get("status") == "PASS"
        and truth.get("truth_class") == expected_class
        and truth.get("prospective_assertions", {}).get("frozen_before_native_launch")
        is True
        and truth.get("prospective_assertions", {}).get(
            "result_dependent_relabeling_allowed"
        )
        is False
        and actual.get("absolute_path") == expected.get("absolute_path")
        and actual.get("sha256") == expected.get("sha256")
        and actual.get("truth_class") == expected_class
        and actual.get("frozen_before_native_launch") is True
    )
    return StageVerification(
        observed=bool(references),
        valid=valid,
        evidence_references=tuple(references),
        defect_reason=None if valid else "PROSPECTIVE_TRUTH_JOIN_INVALID_OR_MISSING",
    )


def build_cell_admissibility_from_evidence(
    *,
    roster_item: Mapping[str, Any],
    runtime_case: Mapping[str, Any],
    oracle_case: Mapping[str, Any],
    exposed_attempt: Path,
    oracle_manifest_path: Path,
    repository_root: Path,
) -> CellAdmissibilityReceipt:
    pre = build_pre_exposure_validity_receipt(
        roster_item=roster_item,
        runtime_case=runtime_case,
        oracle_case=oracle_case,
    )
    if pre.status != PreExposureStatus.VALID.value:
        raise ValueError("CELL_PRE_EXPOSURE_INVALID:" + ",".join(pre.failure_reasons))
    evidence = exposed_attempt / "agent_evidence"
    process_path = exposed_attempt / "process_job" / "PROCESS_RECEIPT.json"
    process = _load(process_path, {})
    method = str(roster_item["method_id"])
    bucket = str(roster_item["timing_bucket"])
    injection = _verify_injection(
        evidence=evidence,
        case_id=str(roster_item["case_id"]),
        episode_id=str(roster_item["episode_id"]),
        bucket=bucket,
        oracle=oracle_case,
        repository_root=repository_root,
    )
    dispatch = _verify_dispatch(
        evidence=evidence,
        method=method,
        expected_update_event_id=str(oracle_case["update_event_id"]),
        injection_frame=injection.sim_frame,
        repository_root=repository_root,
    )
    obligation = _verify_obligation(
        evidence=evidence,
        method=method,
        expected_update_event_id=str(oracle_case["update_event_id"]),
        dispatch_frame=dispatch.sim_frame,
        repository_root=repository_root,
    )
    truth_join = _verify_truth_join(
        evidence=evidence,
        case_id=str(roster_item["case_id"]),
        seed=int(roster_item["seed"]),
        bucket=bucket,
        oracle=oracle_case,
        repository_root=repository_root,
        oracle_manifest_path=oracle_manifest_path,
    )
    return create_cell_admissibility_receipt(
        case_id=str(roster_item["case_id"]),
        episode_id=str(roster_item["episode_id"]),
        ordinal=int(roster_item.get("ordinal", 0)),
        method=method,
        timing_bucket=bucket,
        seed=int(roster_item["seed"]),
        agent_exposed=process.get("agent_exposed") is True,
        pre_exposure_status=pre.status,
        expected_injection_identity=ExpectedInjectionIdentity(
            injection_event_id=str(oracle_case["injection_event_id"]),
            update_event_id=str(oracle_case["update_event_id"]),
            expected_oracle_event=str(oracle_case["expected_oracle_event"]),
            timing_bucket=bucket,
        ),
        injection=injection,
        dispatch=dispatch,
        obligation=obligation,
        truth_join=truth_join,
        process_terminal_class=str(process.get("terminal_class", "UNKNOWN")),
        frozen_config_digest=pre.frozen_config_contract_digest,
    )


def write_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise FileExistsError("ADMISSIBILITY_RECEIPT_WRITE_ONCE_MISMATCH:" + str(path))
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def write_cell_receipt_once(path: Path, receipt: CellAdmissibilityReceipt) -> None:
    value = receipt_to_mapping(receipt)
    validate_cell_admissibility_receipt(value)
    if value.get("schema_version") != CELL_ADMISSIBILITY_SCHEMA:
        raise ValueError("CELL_ADMISSIBILITY_SCHEMA_INVALID_BEFORE_WRITE")
    write_once(path, value)


def pre_exposure_receipt_to_mapping(
    receipt: PreExposureValidityReceipt,
) -> dict[str, Any]:
    value = asdict(receipt)
    if value.get("canonical_sha256") != canonical_sha256(value):
        raise ValueError("PRE_EXPOSURE_VALIDITY_DIGEST_MISMATCH")
    return value
