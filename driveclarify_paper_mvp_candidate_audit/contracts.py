"""Evaluator-side contracts for Stage 6A candidate leakage audits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


AUDIT_SCHEMA_VERSION = "driveclarify.paper_mvp_candidate_generation_audit.v1"
AUDIT_FILENAME = "candidate_generation_audit.json"
REQUIRED_SCENARIO_COUNT = 24


@dataclass(frozen=True)
class RecordedCandidateGeneration:
    """One post-episode evaluator join; never passed back to runtime policy."""

    scenario_id: str
    policy_input_projection: Mapping[str, Any]
    generation_output: Any

    def __post_init__(self) -> None:
        if type(self.scenario_id) is not str or not self.scenario_id:
            raise ValueError("AUDIT_SCENARIO_ID_REQUIRED")
        if not isinstance(self.policy_input_projection, Mapping):
            raise TypeError("RECORDED_POLICY_INPUT_PROJECTION_REQUIRED")


@dataclass(frozen=True)
class ScenarioCandidateLeakageAudit:
    scenario_id: str
    status: str
    record_present: bool
    runtime_input_contract_valid: bool
    runtime_output_contract_valid: bool
    policy_input_sha256: str | None
    runtime_candidate_ids: tuple[str, ...]
    runtime_reported_catalog_read_count: int | None
    runtime_reported_evaluation_label_access_count: int | None
    runtime_reported_catalog_candidate_order_visible: bool | None
    runtime_reported_annotation_overlap_checked: bool | None
    forbidden_field_paths: tuple[str, ...]
    exact_text_overlap_findings: tuple[Mapping[str, Any], ...]
    exact_hash_overlap_findings: tuple[Mapping[str, Any], ...]
    identifier_overlap_findings: tuple[Mapping[str, Any], ...]
    order_overlap_findings: tuple[Mapping[str, Any], ...]
    annotation_overlap_count: int
    runtime_audit_integrity_errors: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "status": self.status,
            "record_present": self.record_present,
            "runtime_input_contract_valid": self.runtime_input_contract_valid,
            "runtime_output_contract_valid": self.runtime_output_contract_valid,
            "policy_input_sha256": self.policy_input_sha256,
            "runtime_candidate_ids": list(self.runtime_candidate_ids),
            "runtime_generator_self_report": {
                "catalog_read_count": self.runtime_reported_catalog_read_count,
                "evaluation_label_access_count": (
                    self.runtime_reported_evaluation_label_access_count
                ),
                "catalog_candidate_order_visible": (
                    self.runtime_reported_catalog_candidate_order_visible
                ),
                "runtime_annotation_overlap_checked": (
                    self.runtime_reported_annotation_overlap_checked
                ),
            },
            "forbidden_field_paths": list(self.forbidden_field_paths),
            "exact_text_overlap": {
                "count": len(self.exact_text_overlap_findings),
                "findings": [dict(item) for item in self.exact_text_overlap_findings],
            },
            "exact_hash_overlap": {
                "count": len(self.exact_hash_overlap_findings),
                "findings": [dict(item) for item in self.exact_hash_overlap_findings],
            },
            "identifier_overlap": {
                "count": len(self.identifier_overlap_findings),
                "findings": [dict(item) for item in self.identifier_overlap_findings],
            },
            "order_overlap": {
                "count": len(self.order_overlap_findings),
                "findings": [dict(item) for item in self.order_overlap_findings],
            },
            "annotation_overlap_count": self.annotation_overlap_count,
            "runtime_audit_integrity_errors": list(
                self.runtime_audit_integrity_errors
            ),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class CandidateGenerationAuditReport:
    status: str
    catalog_path: str
    catalog_sha256: str
    catalog_read_boundary: str
    evaluator_catalog_read_count: int
    required_scenario_count: int
    catalog_scenario_count: int
    audited_scenario_count: int
    passed_scenario_count: int
    missing_scenario_ids: tuple[str, ...]
    extra_scenario_ids: tuple[str, ...]
    duplicate_record_scenario_ids: tuple[str, ...]
    exact_text_overlap_count: int
    exact_hash_overlap_count: int
    identifier_overlap_count: int
    order_overlap_count: int
    annotation_overlap_count: int
    forbidden_field_overlap_count: int
    runtime_reported_catalog_read_count: int
    runtime_reported_evaluation_label_access_count: int
    pass_predicates: Mapping[str, bool]
    scenario_audits: tuple[ScenarioCandidateLeakageAudit, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = AUDIT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "catalog": {
                "path": self.catalog_path,
                "sha256": self.catalog_sha256,
                "read_boundary": self.catalog_read_boundary,
                "evaluator_read_count": self.evaluator_catalog_read_count,
            },
            "coverage": {
                "required_scenario_count": self.required_scenario_count,
                "catalog_scenario_count": self.catalog_scenario_count,
                "audited_scenario_count": self.audited_scenario_count,
                "passed_scenario_count": self.passed_scenario_count,
                "missing_scenario_ids": list(self.missing_scenario_ids),
                "extra_scenario_ids": list(self.extra_scenario_ids),
                "duplicate_record_scenario_ids": list(
                    self.duplicate_record_scenario_ids
                ),
            },
            "overlap_totals": {
                "exact_text_overlap_count": self.exact_text_overlap_count,
                "exact_hash_overlap_count": self.exact_hash_overlap_count,
                "identifier_overlap_count": self.identifier_overlap_count,
                "order_overlap_count": self.order_overlap_count,
                "annotation_overlap_count": self.annotation_overlap_count,
                "forbidden_field_overlap_count": (
                    self.forbidden_field_overlap_count
                ),
            },
            "runtime_generator_self_report_totals": {
                "catalog_read_count": self.runtime_reported_catalog_read_count,
                "evaluation_label_access_count": (
                    self.runtime_reported_evaluation_label_access_count
                ),
            },
            "pass_predicates": dict(self.pass_predicates),
            "scenario_audits": [item.to_dict() for item in self.scenario_audits],
            "reason_codes": list(self.reason_codes),
        }


__all__ = [
    "AUDIT_FILENAME",
    "AUDIT_SCHEMA_VERSION",
    "CandidateGenerationAuditReport",
    "REQUIRED_SCENARIO_COUNT",
    "RecordedCandidateGeneration",
    "ScenarioCandidateLeakageAudit",
]
