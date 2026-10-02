"""Receipt-driven Stage 6A implementation freeze.

This module writes only implementation-freeze artifacts.  It does not run a
simulator, invoke a model, execute a regression command, read the scenario
catalog, join gold labels, or compute scientific evaluation metrics.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import (
    BASELINE_CONFIG_FILENAME,
    BASELINE_FREEZE_FILENAME,
    BASELINE_HASHES_FILENAME,
    FULL_REGRESSION_RECEIPT_SCHEMA_VERSION,
    GATE_ORDER,
    IMPLEMENTATION_FREEZE_REPORT_JSON_FILENAME,
    IMPLEMENTATION_FREEZE_REPORT_MD_FILENAME,
    OVERALL_BLOCKED_STATUS,
    READY_STATUS,
    REPORT_SCHEMA_VERSION,
    GateResult,
    Stage6AFreezeError,
    atomic_write,
    canonical_json_bytes,
    canonical_sha256,
    file_sha256,
    is_sha256,
    load_explicit_receipt,
    yaml_bytes,
)


SCENARIO_LIVE_MANIFEST_FILENAME = "LIVE_PROMOTION_MANIFEST.json"
SCENARIO_EXECUTION_RECEIPT_SCHEMA_VERSION = (
    "driveclarify.paper_mvp_stage6a_scenario_runner_live_execution.v1"
)
CANDIDATE_AUDIT_SCHEMA_VERSION = "driveclarify.paper_mvp_candidate_generation_audit.v1"
AUTHORITY_BINDING_AUDIT_SCHEMA_VERSION = (
    "driveclarify.paper_mvp.stage6a.live_binding_audit.v1"
)

LIVE_HOOK_REQUIREMENTS = {
    "DRIVECLARIFY_PROBE_ENABLED": "TRUTHY_REQUIRED",
    "DRIVECLARIFY_SHADOW_V0": "TRUTHY_REQUIRED",
    "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE": "TRUTHY_REQUIRED",
    "DRIVECLARIFY_PROBE_OUTPUT": "SEPARATELY_CONFIGURED_REQUIRED",
    "DRIVECLARIFY_PAPER_MVP_STAGE6A_OUTPUT_DIR": ("SEPARATELY_CONFIGURED_REQUIRED"),
    "DRIVECLARIFY_PAPER_MVP_STAGE6A_ACT_AUTHORITY": (
        "TRUTHY_REQUIRED_FOR_LIVE_ACT_AUTHORITY_GATE"
    ),
}

_CANDIDATE_PASS_PREDICATES = frozenset(
    {
        "catalog_contains_exactly_24_scenarios",
        "runtime_records_cover_24_of_24_once",
        "all_24_scenario_audits_pass",
        "runtime_generator_catalog_read_count_zero",
        "runtime_generator_evaluation_label_access_count_zero",
        "runtime_generator_catalog_order_visibility_false",
        "runtime_annotation_comparison_remains_evaluator_only",
        "exact_text_overlap_zero",
        "exact_hash_overlap_zero",
        "identifier_overlap_zero",
        "order_overlap_zero",
        "annotation_overlap_zero",
        "forbidden_field_overlap_zero",
        "catalog_read_occurs_once_at_evaluator_boundary",
    }
)

_CANDIDATE_OVERLAP_FIELDS = (
    "exact_text_overlap_count",
    "exact_hash_overlap_count",
    "identifier_overlap_count",
    "order_overlap_count",
    "annotation_overlap_count",
    "forbidden_field_overlap_count",
)


def _blocked(
    gate_id: str,
    reason_code: str,
    *,
    receipt_filename: str | None = None,
    receipt_sha256: str | None = None,
    more_reasons: Sequence[str] = (),
) -> GateResult:
    return GateResult(
        gate_id=gate_id,
        passed=False,
        status="BLOCKED_" + reason_code,
        reason_codes=tuple(dict.fromkeys((reason_code, *more_reasons))),
        receipt_filename=receipt_filename,
        receipt_sha256=receipt_sha256,
        evidence={},
    )


def _receipt_failure(gate_id: str, exc: Stage6AFreezeError) -> GateResult:
    reason = str(exc)
    if reason == "RECEIPT_FILE_MISSING":
        reason = "MISSING_EXPLICIT_RECEIPT"
    return _blocked(gate_id, reason)


def _explicit_path_missing(gate_id: str) -> GateResult:
    return _blocked(gate_id, "MISSING_EXPLICIT_RECEIPT")


def _scenario_gate(
    promotion_dir: str | Path | None,
    runtime_root: str | Path | None,
    execution_receipt_path: str | Path | None = None,
) -> GateResult:
    gate_id = "scenario_live_execution"
    if promotion_dir is None or runtime_root is None:
        return _explicit_path_missing(gate_id)
    promotion_root = Path(promotion_dir)
    if not promotion_root.is_dir():
        return _blocked(gate_id, "LIVE_PROMOTION_DIRECTORY_MISSING")
    manifest_path = promotion_root / SCENARIO_LIVE_MANIFEST_FILENAME
    try:
        manifest_receipt = load_explicit_receipt(manifest_path)
    except Stage6AFreezeError as exc:
        return _receipt_failure(gate_id, exc)
    try:
        # Lazy import keeps CARLA out of this process.  The validator itself is
        # pure/offline and checks receipts authored by a prior live run.
        from driveclarify_paper_mvp_scenarios import validate_live_promotions

        validation = validate_live_promotions(
            promotion_root,
            runtime_root=Path(runtime_root),
            require_ready=True,
        )
    except Exception:
        return _blocked(
            gate_id,
            "LIVE_RECEIPT_SET_VALIDATION_FAILED",
            receipt_filename=manifest_receipt.filename,
            receipt_sha256=manifest_receipt.sha256,
        )
    exact_ready = bool(
        validation.get("status") == "PASS_LIVE_PROMOTION_96_OF_96_READY"
        and validation.get("runtime_fixture_count") == 24
        and validation.get("seed_configuration_count") == 96
        and validation.get("physical_spawn_verified_count") == 96
        and validation.get("semantic_fidelity_verified_count") == 96
        and validation.get("promotion_ready_count") == 96
        and validation.get("all_promotion_ready") is True
        and validation.get("carla_server_started_by_validator") is False
    )
    if not exact_ready:
        return _blocked(
            gate_id,
            "LIVE_SCENARIO_24_OF_24_NOT_EXECUTABLE",
            receipt_filename=manifest_receipt.filename,
            receipt_sha256=manifest_receipt.sha256,
        )
    if execution_receipt_path is None:
        return _blocked(
            gate_id,
            "LIVE_SCENARIO_RUNNER_EXECUTION_RECEIPT_MISSING",
            receipt_filename=manifest_receipt.filename,
            receipt_sha256=manifest_receipt.sha256,
        )
    try:
        execution_receipt = load_explicit_receipt(execution_receipt_path)
    except Stage6AFreezeError as exc:
        return _receipt_failure(gate_id, exc)
    execution_failures = _scenario_execution_failures(
        execution_receipt.payload, manifest_receipt
    )
    if execution_failures:
        return _blocked(
            gate_id,
            execution_failures[0],
            receipt_filename=execution_receipt.filename,
            receipt_sha256=execution_receipt.sha256,
            more_reasons=execution_failures[1:],
        )
    return GateResult(
        gate_id=gate_id,
        passed=True,
        status="PASS_LIVE_SCENARIO_24_OF_24_EXECUTABLE",
        reason_codes=("LIVE_SCENARIO_24_OF_24_EXECUTABLE_ACROSS_4_SEEDS",),
        receipt_filename=execution_receipt.filename,
        receipt_sha256=execution_receipt.sha256,
        evidence={
            "source_kind": "LIVE_CARLA_SCENARIO_RUNNER_EXECUTION_RECEIPT_SET",
            "runtime_fixture_count": 24,
            "seed_configuration_count": 96,
            "promotion_ready_count": 96,
            "handler_instantiated_count": 96,
            "event_timeline_executed_count": 96,
            "termination_reached_count": 96,
            "cleanup_verified_count": 96,
            "composite_clearance_verified_count": 8,
            "promotion_manifest_file_sha256": manifest_receipt.sha256,
            "static_status_accepted_as_live_evidence": False,
            "spawn_destroy_probe_accepted_as_handler_execution": False,
            "injected_client_or_world_accepted_as_live_evidence": False,
            "validator_started_carla_server": False,
        },
    )


def _marker_is_true(payload: Mapping[str, Any], *names: str) -> bool:
    wanted = {name.casefold() for name in names}

    def visit(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).casefold() in wanted and item is True:
                    return True
                if visit(item):
                    return True
        elif isinstance(value, list):
            return any(visit(item) for item in value)
        return False

    return visit(payload)


def _scenario_execution_failures(
    payload: Mapping[str, Any], manifest_receipt: Any
) -> list[str]:
    """Validate proof of real handler execution beyond spawn/destroy probing."""

    failures: list[str] = []
    if payload.get("schema_version") != SCENARIO_EXECUTION_RECEIPT_SCHEMA_VERSION:
        failures.append("SCENARIO_RUNNER_EXECUTION_RECEIPT_SCHEMA_INVALID")
    if payload.get("status") != "PASS":
        failures.append("SCENARIO_RUNNER_EXECUTION_RECEIPT_STATUS_NOT_PASS")
    if payload.get("source_kind") != "LIVE_CARLA_SCENARIO_RUNNER_EXECUTION":
        failures.append("SCENARIO_RUNNER_LIVE_SOURCE_KIND_INVALID")
    if payload.get("synthetic") is not False or payload.get("mock") is not False:
        failures.append("SYNTHETIC_SCENARIO_EXECUTION_RECEIPT_FORBIDDEN")
    if _marker_is_true(payload, "test_fixture"):
        failures.append("SYNTHETIC_SCENARIO_EXECUTION_RECEIPT_FORBIDDEN")
    run_id = payload.get("run_id")
    if type(run_id) is not str or not run_id.strip():
        failures.append("SCENARIO_RUNNER_EXECUTION_RUN_ID_MISSING")
    elif any(
        marker in run_id.casefold()
        for marker in ("mock", "synthetic", "pytest", "unit-test", "unit_test")
    ):
        failures.append("SYNTHETIC_SCENARIO_EXECUTION_RUN_ID_FORBIDDEN")

    manifest = manifest_receipt.payload
    if not (
        payload.get("promotion_manifest_file_sha256") == manifest_receipt.sha256
        and payload.get("promotion_manifest_payload_sha256")
        == manifest.get("manifest_payload_sha256")
        and is_sha256(manifest.get("manifest_payload_sha256"))
    ):
        failures.append("SCENARIO_EXECUTION_PROMOTION_BINDING_INVALID")

    provenance = payload.get("execution_provenance")
    required_true = (
        "native_ubuntu",
        "physical_display_verified",
        "real_carla_server_process_verified",
        "carla_client_server_version_bound",
        "scenario_runner_native_import_verified",
        "world_tick_observed",
        "semantic_evidence_content_verified",
        "paths_and_secrets_omitted",
    )
    required_false = (
        "headless",
        "xvfb",
        "vnc",
        "render_off_screen",
        "no_rendering_mode",
        "injected_client_or_world",
    )
    if not isinstance(provenance, Mapping) or not (
        all(provenance.get(field) is True for field in required_true)
        and all(provenance.get(field) is False for field in required_false)
        and is_sha256(provenance.get("carla_session_sha256"))
    ):
        failures.append("SCENARIO_RUNNER_LIVE_PROVENANCE_INVALID")

    coverage = payload.get("coverage")
    if not isinstance(coverage, Mapping) or not (
        coverage.get("runtime_fixture_count") == 24
        and coverage.get("seed_configuration_count") == 96
        and coverage.get("handler_registered_count") == 96
        and coverage.get("handler_instantiated_count") == 96
        and coverage.get("event_timeline_executed_count") == 96
        and coverage.get("termination_reached_count") == 96
        and coverage.get("cleanup_verified_count") == 96
        and coverage.get("composite_clearance_configuration_count") == 8
        and coverage.get("composite_clearance_verified_count") == 8
        and coverage.get("route_scenario_skipped_count") == 0
        and coverage.get("failed_configuration_count") == 0
    ):
        failures.append("SCENARIO_RUNNER_24_BY_4_EXECUTION_COVERAGE_INVALID")

    promotion_records = manifest.get("records")
    expected: dict[tuple[str, int], Mapping[str, Any]] = {}
    if isinstance(promotion_records, list):
        for record in promotion_records:
            if not isinstance(record, Mapping):
                continue
            runtime_id = record.get("runtime_fixture_id")
            seed = record.get("selected_seed")
            if type(runtime_id) is str and type(seed) is int:
                expected[(runtime_id, seed)] = record
    if len(expected) != 96:
        failures.append("PROMOTION_MANIFEST_PAIR_BINDING_INVALID")

    records = payload.get("configuration_receipts")
    actual: set[tuple[str, int]] = set()
    composite_count = 0
    records_valid = isinstance(records, list) and len(records) == 96
    if records_valid:
        for record in records:
            if not isinstance(record, Mapping):
                records_valid = False
                break
            unsigned = dict(record)
            record_hash = unsigned.pop("execution_payload_sha256", None)
            runtime_id = record.get("runtime_fixture_id")
            seed = record.get("selected_seed")
            pair = (runtime_id, seed)
            promotion = expected.get(pair)
            composite = record.get("composite_clearance_configuration") is True
            composite_count += int(composite)
            if not (
                type(runtime_id) is str
                and bool(runtime_id)
                and type(seed) is int
                and pair not in actual
                and promotion is not None
                and is_sha256(record_hash)
                and canonical_sha256(unsigned) == record_hash
                and record.get("runtime_manifest_sha256")
                == promotion.get("runtime_manifest_sha256")
                and record.get("promotion_receipt_payload_sha256")
                == promotion.get("receipt_payload_sha256")
                and is_sha256(record.get("derived_route_sha256"))
                and is_sha256(record.get("carla_session_sha256"))
                and record.get("status") == "PASS"
                and record.get("real_carla_execution") is True
                and record.get("injected_client_or_world") is False
                and record.get("physical_display_rendered") is True
                and record.get("no_rendering_mode") is False
                and record.get("handler_registered") is True
                and record.get("handler_instantiated") is True
                and record.get("route_scenario_skipped") is False
                and record.get("world_tick_observed") is True
                and record.get("physical_timeline_verified") is True
                and record.get("semantic_evidence_verified") is True
                and record.get("termination_reached") is True
                and record.get("cleanup_verified") is True
                and (
                    not composite
                    or record.get("composite_clearance_realization_verified") is True
                )
            ):
                records_valid = False
                break
            actual.add(pair)
    if not (
        records_valid
        and actual == set(expected)
        and composite_count == 8
        and is_sha256(payload.get("execution_records_sha256"))
        and payload.get("execution_records_sha256") == canonical_sha256(records)
    ):
        failures.append("SCENARIO_RUNNER_CONFIGURATION_RECEIPTS_INVALID")
    return list(dict.fromkeys(failures))


def _candidate_gate(receipt_path: str | Path | None) -> GateResult:
    gate_id = "runtime_candidate_generation"
    if receipt_path is None:
        return _explicit_path_missing(gate_id)
    try:
        receipt = load_explicit_receipt(receipt_path)
    except Stage6AFreezeError as exc:
        return _receipt_failure(gate_id, exc)
    payload = receipt.payload
    failures: list[str] = []
    if payload.get("schema_version") != CANDIDATE_AUDIT_SCHEMA_VERSION:
        failures.append("CANDIDATE_AUDIT_SCHEMA_INVALID")
    if payload.get("status") != "PASS":
        failures.append("CANDIDATE_AUDIT_STATUS_NOT_PASS")
    if _marker_is_true(payload, "synthetic", "mock", "test_fixture"):
        failures.append("SYNTHETIC_CANDIDATE_RECEIPT_FORBIDDEN")

    catalog = payload.get("catalog")
    if not isinstance(catalog, Mapping) or not (
        catalog.get("read_boundary") == "EVALUATOR_ONLY_POST_EPISODE"
        and catalog.get("evaluator_read_count") == 1
        and is_sha256(catalog.get("sha256"))
    ):
        failures.append("CANDIDATE_AUDIT_CATALOG_BOUNDARY_INVALID")

    coverage = payload.get("coverage")
    if not isinstance(coverage, Mapping) or not (
        coverage.get("required_scenario_count") == 24
        and coverage.get("catalog_scenario_count") == 24
        and coverage.get("audited_scenario_count") == 24
        and coverage.get("passed_scenario_count") == 24
        and coverage.get("missing_scenario_ids") == []
        and coverage.get("extra_scenario_ids") == []
        and coverage.get("duplicate_record_scenario_ids") == []
    ):
        failures.append("CANDIDATE_AUDIT_24_OF_24_COVERAGE_INVALID")

    overlaps = payload.get("overlap_totals")
    if not isinstance(overlaps, Mapping) or any(
        overlaps.get(field) != 0 for field in _CANDIDATE_OVERLAP_FIELDS
    ):
        failures.append("CANDIDATE_ANNOTATION_OVERLAP_NOT_ZERO")

    runtime_totals = payload.get("runtime_generator_self_report_totals")
    if not isinstance(runtime_totals, Mapping) or not (
        runtime_totals.get("catalog_read_count") == 0
        and runtime_totals.get("evaluation_label_access_count") == 0
    ):
        failures.append("CANDIDATE_RUNTIME_LABEL_FIREWALL_INVALID")

    predicates = payload.get("pass_predicates")
    if not isinstance(predicates, Mapping) or not (
        _CANDIDATE_PASS_PREDICATES.issubset(predicates)
        and all(predicates.get(name) is True for name in _CANDIDATE_PASS_PREDICATES)
        and all(value is True for value in predicates.values())
    ):
        failures.append("CANDIDATE_AUDIT_PASS_PREDICATES_INVALID")

    scenario_audits = payload.get("scenario_audits")
    scenario_ids: set[str] = set()
    scenario_contracts_valid = (
        isinstance(scenario_audits, list) and len(scenario_audits) == 24
    )
    if scenario_contracts_valid:
        for audit in scenario_audits:
            if not isinstance(audit, Mapping):
                scenario_contracts_valid = False
                break
            scenario_id = audit.get("scenario_id")
            candidates = audit.get("runtime_candidate_ids")
            self_report = audit.get("runtime_generator_self_report")
            overlap_sections = (
                audit.get("exact_text_overlap"),
                audit.get("exact_hash_overlap"),
                audit.get("identifier_overlap"),
                audit.get("order_overlap"),
            )
            if not (
                type(scenario_id) is str
                and bool(scenario_id)
                and scenario_id not in scenario_ids
                and audit.get("status") == "PASS"
                and audit.get("record_present") is True
                and audit.get("runtime_input_contract_valid") is True
                and audit.get("runtime_output_contract_valid") is True
                and isinstance(candidates, list)
                and len(candidates) == 2
                and len(set(candidates)) == 2
                and all(type(item) is str and item for item in candidates)
                and isinstance(self_report, Mapping)
                and self_report.get("catalog_read_count") == 0
                and self_report.get("evaluation_label_access_count") == 0
                and self_report.get("catalog_candidate_order_visible") is False
                and self_report.get("runtime_annotation_overlap_checked") is False
                and audit.get("forbidden_field_paths") == []
                and audit.get("annotation_overlap_count") == 0
                and audit.get("runtime_audit_integrity_errors") == []
                and all(
                    isinstance(section, Mapping)
                    and section.get("count") == 0
                    and section.get("findings") == []
                    for section in overlap_sections
                )
            ):
                scenario_contracts_valid = False
                break
            scenario_ids.add(scenario_id)
    if not scenario_contracts_valid:
        failures.append("RUNTIME_CANDIDATES_NOT_VERIFIED_FOR_24_OF_24")

    if failures:
        return _blocked(
            gate_id,
            failures[0],
            receipt_filename=receipt.filename,
            receipt_sha256=receipt.sha256,
            more_reasons=failures[1:],
        )
    return GateResult(
        gate_id=gate_id,
        passed=True,
        status="PASS_RUNTIME_CANDIDATE_GENERATION_24_OF_24",
        reason_codes=(
            "RUNTIME_GENERATED_CANDIDATES_VERIFIED",
            "ANNOTATION_OVERLAP_ZERO",
            "RUNTIME_CATALOG_AND_LABEL_READ_COUNT_ZERO",
        ),
        receipt_filename=receipt.filename,
        receipt_sha256=receipt.sha256,
        evidence={
            "source_kind": "POST_EPISODE_CANDIDATE_LEAKAGE_AUDIT",
            "audited_scenario_count": 24,
            "passed_scenario_count": 24,
            "runtime_candidates_per_scenario": 2,
            "annotation_overlap_count": 0,
            "runtime_catalog_read_count": 0,
            "runtime_evaluation_label_access_count": 0,
            "candidate_text_or_identifiers_copied_to_freeze_report": False,
        },
    )


def _authority_gate(receipt_path: str | Path | None) -> GateResult:
    """Validate the fixed live-binding audit without accepting unit-test status."""

    gate_id = "live_authority_binding"
    if receipt_path is None:
        return _explicit_path_missing(gate_id)
    try:
        receipt = load_explicit_receipt(receipt_path)
    except Stage6AFreezeError as exc:
        return _receipt_failure(gate_id, exc)
    payload = receipt.payload
    failures: list[str] = []
    if payload.get("schema_version") != AUTHORITY_BINDING_AUDIT_SCHEMA_VERSION:
        failures.append("LIVE_AUTHORITY_AUDIT_SCHEMA_INVALID")
    if _marker_is_true(payload, "synthetic", "mock", "test_fixture"):
        failures.append("SYNTHETIC_LIVE_AUTHORITY_RECEIPT_FORBIDDEN")
    run_id = payload.get("run_id")
    if type(run_id) is not str or not run_id.strip():
        failures.append("LIVE_AUTHORITY_RUN_ID_MISSING")
    elif any(
        marker in run_id.casefold()
        for marker in ("mock", "synthetic", "pytest", "unit-test", "unit_test")
    ):
        failures.append("SYNTHETIC_LIVE_AUTHORITY_RUN_ID_FORBIDDEN")

    hook = payload.get("live_hook_configuration")
    hook_fields = (
        "probe_enabled",
        "shadow_gateway_enabled",
        "stage6a_selector_enabled",
        "probe_output_configured",
        "stage6a_output_configured",
        "act_authority_enabled",
        "dispatch_prerequisites_satisfied",
        "paths_and_secrets_omitted",
    )
    if not isinstance(hook, Mapping) or any(
        hook.get(field) is not True for field in hook_fields
    ):
        failures.append("LIVE_HOOK_DISPATCH_CONFIGURATION_NOT_VERIFIED")

    event = payload.get("event")
    authority = payload.get("authority")
    if not isinstance(event, Mapping):
        failures.append("LIVE_AUTHORITY_EVENT_MISSING")
        event = {}
    if not isinstance(authority, Mapping):
        failures.append("PERSISTENT_LIVE_AUTHORITY_SUMMARY_MISSING")
        authority = {}
    selection = event.get("plan_selection")
    arm = event.get("authority_arm")
    if not isinstance(selection, Mapping):
        failures.append("LIVE_AUTHORITY_PLAN_SELECTION_MISSING")
        selection = {}
    if not isinstance(arm, Mapping):
        failures.append("LIVE_AUTHORITY_ARM_RECEIPT_MISSING")
        arm = {}

    identity = payload.get("persistent_pre_pid_authority_identity")
    receipt_id = selection.get("receipt_id")
    receipt_digest = selection.get("receipt_digest")
    decision = selection.get("authority_decision")
    event_contract_valid = bool(
        payload.get("enabled") is True
        and payload.get("status") == "DECISION_READY_PRE_PID"
        and payload.get("valid_episode_count") == 1
        and payload.get("valid_live_act_authority_count") == 1
        and event.get("status") == "DECISION_READY_PRE_PID"
        and event.get("valid_episode") is True
        and event.get("valid_live_act_authority") is True
        and event.get("activation_count") == 1
        and event.get("candidate_model_forward_count") == 2
        and event.get("blockers") == []
        and event.get("errors") == []
        and type(identity) in {str, int}
        and str(identity)
        and event.get("persistent_authority_identity") == identity
        and authority.get("authority_adapter_identity") == identity
        and authority.get("persistence_verified") is True
        and authority.get("arm") == arm
        and authority.get("plan_selection") == selection
        and arm.get("armed") is True
        and arm.get("receipt_id") == receipt_id
        and arm.get("receipt_digest") == receipt_digest
        and selection.get("m2b_action") == "ACT"
        and selection.get("resolved_source") == "candidate"
        and selection.get("authority_status") == "AUTHORIZED_CANDIDATE_PLAN"
        and selection.get("authority_adapter_identity") == identity
        and type(receipt_id) is str
        and bool(receipt_id)
        and is_sha256(receipt_digest)
        and selection.get("blocker") is None
        and selection.get("single_existing_pid_seam") is True
        and isinstance(decision, Mapping)
        and decision.get("owner") == "DRIVECLARIFY_CANDIDATE_CONTROL"
        and decision.get("authorized") is True
        and decision.get("reason_code") == "CANDIDATE_ACT_AUTHORITY_GRANTED"
        and decision.get("candidate_receipt_id") == receipt_id
        and is_sha256(decision.get("route_digest"))
        and is_sha256(decision.get("speed_digest"))
        and is_sha256(decision.get("decision_digest"))
        and payload.get("pid_invocation_count") == 1
        and payload.get("new_pid_instance_count") == 0
        and payload.get("candidate_control_write_count") == 0
        and payload.get("m3_control_write_count") == 0
        and event.get("control_write_count") == 0
        and event.get("pid_invocation_count") == 1
        and event.get("pid", {}).get("owner") == "EXISTING_SIMLINGO_PID"
        and event.get("pid", {}).get("instance_count") == 1
        and event.get("pid", {}).get("new_pid_instance_count") == 0
        and payload.get("pending_candidate_set_ids") == []
    )
    if not event_contract_valid:
        failures.append("LIVE_AUTHORITY_EPISODE_BINDING_NOT_VERIFIED")

    if failures:
        return _blocked(
            gate_id,
            failures[0],
            receipt_filename=receipt.filename,
            receipt_sha256=receipt.sha256,
            more_reasons=failures[1:],
        )
    return GateResult(
        gate_id=gate_id,
        passed=True,
        status="PASS_LIVE_AUTHORITY_BINDING",
        reason_codes=(
            "ACTIVATION_V1_CONNECTED",
            "PERSISTENT_AUTHORITY_IDENTITY_VERIFIED",
            "IDENTITY_BOUND_RECEIPT_CONSUMED_PRE_PID",
            "EXISTING_PID_ONLY",
        ),
        receipt_filename=receipt.filename,
        receipt_sha256=receipt.sha256,
        evidence={
            "source_kind": "LIVE_RUNTIME_AUTHORITY_BINDING_AUDIT",
            "valid_episode_count": 1,
            "valid_live_act_authority_count": 1,
            "activation_v1_invocations_per_episode": 1,
            "authorized_candidate_plan_pre_pid": True,
            "persistent_authority_identity_verified": True,
            "existing_pid_invocations_per_episode": 1,
            "new_pid_instance_count": 0,
            "hook_dispatch_prerequisites_satisfied": True,
            "act_authority_flag_enabled": True,
            "live_hook_requirement_ids": list(LIVE_HOOK_REQUIREMENTS),
            "synthetic_test_status_accepted_as_live_evidence": False,
        },
    )


def _threshold_text(method: Mapping[str, Any]) -> str:
    threshold = method.get("threshold")
    if not isinstance(threshold, Mapping):
        return "none"
    return (
        f"{threshold['field']} {threshold['comparison']} {threshold['value']}"
        f" → {threshold['equal_action']}"
    )


def _budget_text(method: Mapping[str, Any]) -> str:
    budget = method["compute_budget"]
    normal = budget["normal_simlingo_forward_count"]
    cases = budget["candidate_conditioned_forward_cases"]
    return "; ".join(
        f"{case}=normal:{normal}+candidate:{count}" for case, count in cases.items()
    )


def _baseline_markdown(config: Mapping[str, Any], hashes: Mapping[str, Any]) -> bytes:
    lines = [
        "# Stage 6A executable baseline freeze",
        "",
        f"- Schema: `{config['schema_version']}`",
        f"- Freeze ID: `{config['freeze_id']}`",
        f"- Baseline freeze SHA-256: `{hashes['baseline_freeze_sha256']}`",
        f"- Aggregate implementation SHA-256: `{hashes['aggregate_sha256']}`",
        "- Exact method count: `8`",
        "- `always_obey` present: `false`",
        "",
        "| # | Method | Threshold | Frozen forward cases | Method SHA-256 |",
        "|---:|---|---|---|---|",
    ]
    method_hashes = hashes["method_config_sha256"]
    for index, method in enumerate(config["methods"], 1):
        method_id = method["id"]
        lines.append(
            "| "
            + " | ".join(
                (
                    str(index),
                    f"`{method_id}`",
                    _threshold_text(method).replace("|", "\\|"),
                    _budget_text(method).replace("|", "\\|"),
                    f"`{method_hashes[method_id]}`",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "The configuration freezes implementations, thresholds, per-case model-forward budgets, denominator contracts, split protocol, and prohibitions. It contains no observed evaluation values.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _baseline_gate(output_dir: Path) -> GateResult:
    gate_id = "baseline_v2_freeze"
    try:
        from driveclarify_paper_mvp_evaluation.freeze import (
            FROZEN_BASELINE_CONFIG,
            build_baseline_hash_manifest,
            validate_baseline_config,
        )

        config = validate_baseline_config(copy.deepcopy(FROZEN_BASELINE_CONFIG))
        hashes = build_baseline_hash_manifest(config)
        exact = bool(
            config.get("schema_version") == "driveclarify.paper_mvp_baseline_config.v2"
            and config.get("freeze_id") == "DRIVECLARIFY_PAPER_MVP_STAGE6A_BASELINES_V2"
            and config.get("status") == "STAGE6A_EXECUTABLE_CONTRACT_FROZEN"
            and config.get("method_order")
            == [
                "original_simlingo",
                "driveclarify",
                "always_ask",
                "always_stop",
                "always_wait",
                "never_ask",
                "language_only_uncertainty",
                "risk_only",
            ]
            and isinstance(config.get("methods"), list)
            and len(config["methods"]) == 8
            and "always_obey" not in config["method_order"]
            and hashes.get("schema_version")
            == "driveclarify.paper_mvp_baseline_hashes.v1"
            and is_sha256(hashes.get("baseline_freeze_sha256"))
            and is_sha256(hashes.get("aggregate_sha256"))
            and set(hashes.get("method_config_sha256", {}))
            == set(config["method_order"])
            and all(
                is_sha256(value)
                for value in hashes.get("method_config_sha256", {}).values()
            )
        )
        if not exact:
            raise Stage6AFreezeError("BASELINE_V2_EXACT_FREEZE_INVALID")
        config_path = output_dir / BASELINE_CONFIG_FILENAME
        hashes_path = output_dir / BASELINE_HASHES_FILENAME
        markdown_path = output_dir / BASELINE_FREEZE_FILENAME
        atomic_write(config_path, yaml_bytes(config))
        atomic_write(hashes_path, canonical_json_bytes(hashes, pretty=True))
        atomic_write(markdown_path, _baseline_markdown(config, hashes))
        artifact_hashes = {
            BASELINE_CONFIG_FILENAME: file_sha256(config_path),
            BASELINE_HASHES_FILENAME: file_sha256(hashes_path),
            BASELINE_FREEZE_FILENAME: file_sha256(markdown_path),
        }
    except Exception:
        return _blocked(gate_id, "BASELINE_V2_FREEZE_GENERATION_FAILED")
    return GateResult(
        gate_id=gate_id,
        passed=True,
        status="PASS_EXACT_8_BASELINE_V2_FREEZE",
        reason_codes=("EXACT_8_BASELINE_V2_CONTRACTS_FROZEN",),
        evidence={
            "config_schema_version": config["schema_version"],
            "freeze_id": config["freeze_id"],
            "method_count": 8,
            "method_order": list(config["method_order"]),
            "always_obey_present": False,
            "baseline_freeze_sha256": hashes["baseline_freeze_sha256"],
            "baseline_hash_manifest_aggregate_sha256": hashes["aggregate_sha256"],
            "artifact_sha256": artifact_hashes,
        },
    )


def _regression_gate(receipt_path: str | Path | None) -> GateResult:
    gate_id = "full_regression"
    if receipt_path is None:
        return _explicit_path_missing(gate_id)
    try:
        receipt = load_explicit_receipt(receipt_path)
    except Stage6AFreezeError as exc:
        return _receipt_failure(gate_id, exc)
    payload = receipt.payload
    failures: list[str] = []
    if payload.get("schema_version") != FULL_REGRESSION_RECEIPT_SCHEMA_VERSION:
        failures.append("FULL_REGRESSION_RECEIPT_SCHEMA_INVALID")
    if payload.get("receipt_kind") != "FULL_REGRESSION":
        failures.append("FULL_REGRESSION_RECEIPT_KIND_INVALID")
    if payload.get("scope") != "REPOSITORY_CONFIGURED_FULL_REGRESSION":
        failures.append("FULL_REGRESSION_SCOPE_INVALID")
    if payload.get("status") != "PASS":
        failures.append("FULL_REGRESSION_STATUS_NOT_PASS")
    if type(payload.get("exit_code")) is not int or payload.get("exit_code") != 0:
        failures.append("FULL_REGRESSION_EXIT_CODE_NOT_ZERO")
    command = payload.get("command_argv")
    if (
        not isinstance(command, list)
        or not command
        or any(type(item) is not str or not item for item in command)
    ):
        failures.append("FULL_REGRESSION_COMMAND_IDENTITY_MISSING")
    if payload.get("synthetic_live_receipts_promoted") is not False:
        failures.append("FULL_REGRESSION_SYNTHETIC_LIVE_PROMOTION_UNDECLARED")
    for digest_name in ("stdout_sha256", "stderr_sha256"):
        if not is_sha256(payload.get(digest_name)):
            failures.append("FULL_REGRESSION_STREAM_DIGEST_INVALID")
            break
    if failures:
        return _blocked(
            gate_id,
            failures[0],
            receipt_filename=receipt.filename,
            receipt_sha256=receipt.sha256,
            more_reasons=failures[1:],
        )
    return GateResult(
        gate_id=gate_id,
        passed=True,
        status="PASS_FULL_REGRESSION_EXIT_ZERO",
        reason_codes=("EXPLICIT_FULL_REGRESSION_RECEIPT_EXIT_ZERO",),
        receipt_filename=receipt.filename,
        receipt_sha256=receipt.sha256,
        evidence={
            "scope": "REPOSITORY_CONFIGURED_FULL_REGRESSION",
            "exit_code": 0,
            "synthetic_live_receipts_promoted": False,
            "command_argv_sha256": canonical_sha256(command),
            "stdout_sha256": payload["stdout_sha256"],
            "stderr_sha256": payload["stderr_sha256"],
        },
    )


def _report_markdown(report: Mapping[str, Any]) -> bytes:
    lines = [
        "# Stage 6A implementation freeze report",
        "",
        f"## Status: `{report['status']}`",
        "",
        "| Gate | Status | Receipt SHA-256 |",
        "|---|---|---|",
    ]
    for gate_id in GATE_ORDER:
        gate = report["gates"][gate_id]
        receipt = gate.get("receipt")
        receipt_hash = "—" if receipt is None else f"`{receipt['sha256']}`"
        lines.append(f"| `{gate_id}` | `{gate['status']}` | {receipt_hash} |")
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "- Scenario readiness requires both the strict 24×4 CARLA promotion set and a separately supplied real ScenarioRunner handler-execution receipt covering instantiate, physical timeline, termination, and cleanup; static status or spawn/destroy probing is insufficient.",
            "- Candidate and authority inputs are explicit receipts. Their raw scenario identities, instructions, candidates, and event payloads are not copied here.",
            "- This builder did not open TEST labels, run Stage 6B episodes, or generate evaluation metrics.",
            "- Regression tests cannot substitute for either live scenario evidence or live authority evidence.",
            "",
            "## Frozen live-hook prerequisites",
            "",
            "- `DRIVECLARIFY_PROBE_ENABLED=1`, `DRIVECLARIFY_SHADOW_V0=1`, and `DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE=1` are all required for factory dispatch.",
            "- `DRIVECLARIFY_PROBE_OUTPUT` and `DRIVECLARIFY_PAPER_MVP_STAGE6A_OUTPUT_DIR` must each be configured; their values are not copied into this report.",
            "- Live ACT evidence additionally requires `DRIVECLARIFY_PAPER_MVP_STAGE6A_ACT_AUTHORITY=1`.",
            "",
        ]
    )
    if report["blockers"]:
        lines.extend(["## Blockers", ""])
        for blocker in report["blockers"]:
            lines.append(
                f"- `{blocker['status']}` ({', '.join(blocker['reason_codes'])})"
            )
        lines.append("")
    else:
        lines.extend(
            [
                "All five implementation gates passed. This authorizes Stage 6B execution; it is not a scientific result.",
                "",
            ]
        )
    lines.append(f"Report payload SHA-256: `{report['report_payload_sha256']}`")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def build_stage6a_implementation_freeze(
    *,
    output_dir: str | Path,
    scenario_live_promotion_dir: str | Path | None = None,
    scenario_runtime_root: str | Path | None = None,
    scenario_live_execution_receipt: str | Path | None = None,
    candidate_audit_receipt: str | Path | None = None,
    authority_binding_receipt: str | Path | None = None,
    full_regression_receipt: str | Path | None = None,
) -> dict[str, Any]:
    """Write baseline artifacts and aggregate explicit Stage 6A receipts.

    Missing, malformed, UNKNOWN, static-only, or failing evidence produces a
    ``BLOCKED_`` status.  ``READY_STAGE6B_EXECUTION`` has exactly one path: all
    five gates must return strict PASS results.
    """

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    gate_results = (
        _scenario_gate(
            scenario_live_promotion_dir,
            scenario_runtime_root,
            scenario_live_execution_receipt,
        ),
        _candidate_gate(candidate_audit_receipt),
        _authority_gate(authority_binding_receipt),
        _baseline_gate(destination),
        _regression_gate(full_regression_receipt),
    )
    gates = {item.gate_id: item.to_dict() for item in gate_results}
    failed = [item for item in gate_results if not item.passed]
    status = READY_STATUS if not failed else OVERALL_BLOCKED_STATUS[failed[0].gate_id]
    blockers = [
        {
            "gate_id": item.gate_id,
            "status": item.status,
            "reason_codes": list(item.reason_codes),
        }
        for item in failed
    ]
    body: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": status,
        "ready_for_stage6b_execution": not failed,
        "scope": "STAGE6A_IMPLEMENTATION_FREEZE_ONLY",
        "evidence_policy": {
            "explicit_receipts_only": True,
            "historical_report_discovery": False,
            "static_scenario_status_accepted_as_live": False,
            "spawn_destroy_probe_accepted_as_scenario_runner_execution": False,
            "scenario_runner_execution_receipt_required": True,
            "synthetic_tests_accepted_as_live_receipts": False,
        },
        "live_hook_freeze": {
            "required_environment_contract": dict(LIVE_HOOK_REQUIREMENTS),
            "all_three_dispatch_selectors_required": True,
            "probe_and_stage6a_outputs_separately_required": True,
            "environment_values_or_paths_copied_to_report": False,
        },
        "scientific_boundary": {
            "stage6b_evaluation_executed_by_builder": False,
            "test_split_opened_by_builder": False,
            "test_labels_opened_by_builder": False,
            "evaluation_metrics_generated_by_builder": False,
            "scientific_result_claimed": False,
        },
        "gates": gates,
        "blockers": blockers,
    }
    report = {**body, "report_payload_sha256": canonical_sha256(body)}
    json_path = destination / IMPLEMENTATION_FREEZE_REPORT_JSON_FILENAME
    markdown_path = destination / IMPLEMENTATION_FREEZE_REPORT_MD_FILENAME
    atomic_write(json_path, canonical_json_bytes(report, pretty=True))
    atomic_write(markdown_path, _report_markdown(report))
    return report


__all__ = [
    "AUTHORITY_BINDING_AUDIT_SCHEMA_VERSION",
    "CANDIDATE_AUDIT_SCHEMA_VERSION",
    "LIVE_HOOK_REQUIREMENTS",
    "SCENARIO_EXECUTION_RECEIPT_SCHEMA_VERSION",
    "SCENARIO_LIVE_MANIFEST_FILENAME",
    "build_stage6a_implementation_freeze",
]
