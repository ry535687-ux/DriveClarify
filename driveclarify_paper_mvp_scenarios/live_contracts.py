"""Pure contracts shared by live promotion, validation, and the handler.

Importing this module never imports CARLA and never reads evaluator-private
artifacts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .contracts import canonical_sha256, require


DEFAULT_LIVE_PROMOTION_DIR = Path(__file__).resolve().parent / "live_promotion"
LIVE_PROMOTION_MANIFEST = "LIVE_PROMOTION_MANIFEST.json"
LIVE_RECEIPT_DIR = "receipts"
LIVE_RECEIPT_SCHEMA_VERSION = "driveclarify.paper_mvp_stage6a_live_receipt.v1"
LIVE_MANIFEST_SCHEMA_VERSION = "driveclarify.paper_mvp_stage6a_live_manifest.v1"
HANDLER_EXECUTION_RECEIPT_SCHEMA_VERSION = (
    "driveclarify.paper_mvp_stage6a_handler_execution_receipt.v1"
)
SEMANTIC_ATTESTATION_SCHEMA_VERSION = (
    "driveclarify.paper_mvp_stage6a_semantic_attestation.v0"
)

RECEIPT_HASH_FIELD = "receipt_payload_sha256"
MANIFEST_HASH_FIELD = "manifest_payload_sha256"
HANDLER_RECEIPT_HASH_FIELD = "handler_receipt_payload_sha256"

LIVE_EVIDENCE_ORIGIN = "LIVE_CARLA_EXISTING_SERVER"
TEST_DOUBLE_EVIDENCE_ORIGIN = "TEST_DOUBLE"

# Frozen into code so the live process can audit its runtime-visible inputs
# without opening SCENARIO_CATALOG.json or EVALUATOR_PRIVATE_MANIFEST.json.
RUNTIME_FORBIDDEN_KEYS = frozenset(
    {
        "scenario_id",
        "scenario_type",
        "expected_decision",
        "expected_decision_for_validation",
        "decision_reason",
        "ambiguity_type",
        "scenario_ambiguity_description",
        "missing_slots",
        "candidate_interpretations",
        "ground_truth_reason",
        "candidate_consequence_summary",
        "candidate_consequence_linkage",
        "query_value_expectation",
        "wait_value_expectation",
        "answer_impact",
        "future_information_impact",
        "future_information_resolution_oracle_by_seed",
        "wait_evidence",
        "implementation_status",
        "calibration_status",
        "provenance_scope",
        "split",
        "prompt_family_id",
        "scenario_template_id",
        "object_combination_id",
        "object_combination",
        "layout_group_id",
        "paired_family_id",
        "candidate_order_by_seed",
    }
)

SEMANTIC_FIDELITY_STATUSES = frozenset(
    {"VERIFIED", "UNKNOWN", "BLOCKED", "NOT_APPLICABLE"}
)
REALIZATION_MODES = frozenset(
    {
        "SPAWNED_BLUEPRINT_ACTOR",
        "SPAWNED_ASSET_CLUSTER",
        "EXISTING_WORLD_FEATURE",
        "COMPOSITE_MEASURED_CLEARANCE_GATEWAY",
        "NOT_APPLICABLE",
    }
)


def content_address(value: Mapping[str, Any], hash_field: str) -> tuple[dict[str, Any], str]:
    require(hash_field not in value, f"CONTENT_HASH_FIELD_ALREADY_PRESENT:{hash_field}")
    digest = canonical_sha256(value)
    result = dict(value)
    result[hash_field] = digest
    return result, digest


def verify_content_address(value: Mapping[str, Any], hash_field: str) -> bool:
    digest = value.get(hash_field)
    unsigned = dict(value)
    unsigned.pop(hash_field, None)
    return isinstance(digest, str) and digest == canonical_sha256(unsigned)
