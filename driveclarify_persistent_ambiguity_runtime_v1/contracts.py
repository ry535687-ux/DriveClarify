"""Frozen-schema and cross-field validators for G0.

JSON Schema is deliberately followed by the mandatory expression rules stored
in each frozen contract.  A schema-only PASS is not a DriveClarify contract
PASS.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator

from .types import (
    EvidenceGrade,
    EvidenceResult,
    EvidenceStatus,
    UsagePurpose,
    Visibility,
)


ROOT = Path(__file__).resolve().parents[1]
FROZEN_CONTRACT_ROOT = (
    ROOT / "reports/driveclarify_decision_window_persistent_ambiguity_design_freeze"
)
SCHEMA_PATHS = {
    "decision_window": FROZEN_CONTRACT_ROOT / "DECISION_WINDOW_EVIDENCE_CONTRACT.json",
    "persistent_episode": FROZEN_CONTRACT_ROOT / "PERSISTENT_AMBIGUITY_LIFECYCLE_CONTRACT.json",
    "authority_subject": FROZEN_CONTRACT_ROOT / "ACT_SHARED_AUTHORITY_EXTENSION_CONTRACT.json",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ContractViolation(ValueError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code + (":" + detail if detail else ""))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_pair_key(left: str, right: str) -> str:
    return "::".join(sorted((str(left), str(right))))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value.to_dict() if isinstance(value, EvidenceResult) else value


def validate_evidence_result(value: Any) -> None:
    row = _mapping(value)
    if not isinstance(row, Mapping):
        raise ContractViolation("EVIDENCE_RESULT_NOT_OBJECT")
    required = {
        "status", "value", "unit", "frame", "evidence_grade", "dependencies",
        "reason_code", "source_artifacts", "usage_purpose", "authorization_eligible",
        "safety_critical_eligible", "clock_domain", "limitations", "provenance",
    }
    if set(row) != required:
        raise ContractViolation("EVIDENCE_RESULT_FIELDS_INVALID")
    try:
        status = EvidenceStatus(row["status"])
        grade = EvidenceGrade(row["evidence_grade"])
        purpose = UsagePurpose(row["usage_purpose"])
    except (TypeError, ValueError) as error:
        raise ContractViolation("EVIDENCE_RESULT_ENUM_INVALID", str(error))
    provenance = row["provenance"]
    if not isinstance(provenance, Mapping):
        raise ContractViolation("EVIDENCE_PROVENANCE_INVALID")
    try:
        visibility = Visibility(provenance["visibility"])
    except (KeyError, TypeError, ValueError) as error:
        raise ContractViolation("EVIDENCE_VISIBILITY_INVALID", str(error))

    if status is EvidenceStatus.AVAILABLE:
        if row["value"] is None:
            raise ContractViolation("AVAILABLE_RESULT_REQUIRES_VALUE")
        if row["reason_code"] is not None:
            raise ContractViolation("AVAILABLE_RESULT_MUST_NOT_HAVE_REASON")
        for field in ("unit", "frame", "clock_domain"):
            if not isinstance(row[field], str) or not row[field]:
                raise ContractViolation("AVAILABLE_RESULT_REQUIRES_" + field.upper())
        for field in (
            "source_observation_id", "source_frame_id", "observed_monotonic_time",
            "comparison_context_id",
        ):
            if provenance.get(field) is None or provenance.get(field) == "":
                raise ContractViolation("AVAILABLE_RESULT_PROVENANCE_INCOMPLETE", field)
    else:
        if row["value"] is not None:
            raise ContractViolation("NON_AVAILABLE_RESULT_MUST_HAVE_NULL_VALUE")
        reason = row["reason_code"]
        if not isinstance(reason, str) or not REASON_RE.fullmatch(reason):
            raise ContractViolation("NON_AVAILABLE_RESULT_REQUIRES_REASON_CODE")
        if row["authorization_eligible"] or row["safety_critical_eligible"]:
            raise ContractViolation("NON_AVAILABLE_RESULT_CANNOT_BE_ELIGIBLE")

    if row["authorization_eligible"]:
        if not (
            status is EvidenceStatus.AVAILABLE
            and grade is EvidenceGrade.VERIFIED
            and purpose is UsagePurpose.AUTHORIZATION
            and visibility is Visibility.RUNTIME_OBSERVABLE
        ):
            raise ContractViolation("AUTHORIZATION_ELIGIBILITY_INVALID")
    if row["safety_critical_eligible"]:
        if not (
            status is EvidenceStatus.AVAILABLE
            and grade is EvidenceGrade.VERIFIED
            and purpose is UsagePurpose.SAFETY_CRITICAL
            and visibility is Visibility.RUNTIME_OBSERVABLE
        ):
            raise ContractViolation("SAFETY_ELIGIBILITY_INVALID")
    if purpose in (UsagePurpose.DIAGNOSTIC_ONLY, UsagePurpose.LOGGING_ONLY):
        if row["authorization_eligible"] or row["safety_critical_eligible"]:
            raise ContractViolation("NON_AUTHORIZING_PURPOSE_ELIGIBLE")

    digest = provenance.get("artifact_sha256")
    if digest is not None and not SHA256_RE.fullmatch(str(digest)):
        raise ContractViolation("PROVENANCE_SHA256_INVALID")
    if not isinstance(row["dependencies"], list) or len(set(row["dependencies"])) != len(row["dependencies"]):
        raise ContractViolation("EVIDENCE_DEPENDENCIES_INVALID")


def _iter_result_objects(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if "status" in value and "provenance" in value and "authorization_eligible" in value:
            yield value
        for child in value.values():
            for result in _iter_result_objects(child):
                yield result
    elif isinstance(value, list):
        for child in value:
            for result in _iter_result_objects(child):
                yield result


class ContractValidator:
    """Validate one of the three frozen G0 contract families."""

    def __init__(self, schema_paths: Optional[Mapping[str, Path]] = None) -> None:
        paths = dict(schema_paths or SCHEMA_PATHS)
        self.schemas = {}
        for name, path in paths.items():
            schema = json.loads(path.read_text(encoding="utf-8"))
            if name == "persistent_episode":
                schema = self._method_v1_persistent_schema_revision(schema)
            self.schemas[name] = schema
        self.validators = {
            name: Draft202012Validator(schema) for name, schema in self.schemas.items()
        }

    @staticmethod
    def _method_v1_persistent_schema_revision(
        frozen_schema: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Apply the authorized ACT/convergence delta without rewriting history."""

        schema = deepcopy(frozen_schema)
        for field in ("consequence_state", "current_candidate_relationship"):
            values = schema.get("properties", {}).get(field, {}).get("enum", [])
            if "CURRENT_ACTION_SHARED_FUTURE_UNKNOWN" not in values:
                values.append("CURRENT_ACTION_SHARED_FUTURE_UNKNOWN")
            if (
                field == "current_candidate_relationship"
                and "CURRENT_ACTION_DIVERGENT" not in values
            ):
                values.append("CURRENT_ACTION_DIVERGENT")
        for rule in schema.get("allOf", []):
            condition = rule.get("if", {}).get("properties", {})
            semantic = condition.get("semantic_state", {})
            shared = condition.get("current_shared_executable_action", {})
            if semantic.get("const") == "UNRESOLVED":
                original = deepcopy(rule["then"])
                rule["then"] = {
                    "anyOf": [
                        original,
                        {
                            "properties": {
                                "candidates": {
                                    "minItems": 2,
                                    "contains": {
                                        "properties": {
                                            "status": {
                                                "const": "ACTIVE_UNRESOLVED"
                                            }
                                        },
                                        "required": ["status"],
                                    },
                                    "minContains": 1,
                                    "maxContains": 1,
                                },
                                "unresolved_slots": {"minItems": 1},
                                "evidence_state": {
                                    "const": "REFRESH_REQUIRED"
                                },
                                "reason_codes": {
                                    "contains": {
                                        "const": (
                                            "RUNTIME_EVIDENCE_CANDIDATE_REJECTED"
                                        )
                                    }
                                },
                            }
                        },
                    ]
                }
            if shared.get("type") == "object":
                consequence = rule.get("then", {}).get("properties", {}).get(
                    "consequence_state"
                )
                if consequence == {
                    "const": "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT"
                }:
                    rule["then"]["properties"]["consequence_state"] = {
                        "enum": [
                            "CURRENTLY_EQUIVALENT_FUTURE_DIVERGENT",
                            "NO_MATERIAL_DIVERGENCE",
                            "CURRENT_ACTION_SHARED_FUTURE_UNKNOWN",
                        ]
                    }
        return schema

    def validate(self, contract_name: str, value: Mapping[str, Any]) -> None:
        if contract_name not in self.validators:
            raise ContractViolation("UNKNOWN_CONTRACT", contract_name)
        errors = sorted(
            self.validators[contract_name].iter_errors(value),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            first = errors[0]
            path = ".".join(str(part) for part in first.absolute_path)
            raise ContractViolation("JSON_SCHEMA_VALIDATION_FAILED", path + ":" + first.message)
        if contract_name == "decision_window":
            self._validate_decision_window(value)
        elif contract_name == "persistent_episode":
            self._validate_persistent_episode(value)
        else:
            self._validate_authority_subject(value)

    @staticmethod
    def _validate_decision_window(value: Mapping[str, Any]) -> None:
        candidate_ids = list(value["comparison_context"]["candidate_ids"])
        expected = set(candidate_ids)
        for field in ("semantic_targets", "topology_targets", "commitment_points", "plan_coverage"):
            if set(value[field]) != expected:
                raise ContractViolation("CANDIDATE_KEYSET_MISMATCH", field)
        if value["candidate_keyset_digest"] != canonical_sha256(sorted(expected)):
            raise ContractViolation("CANDIDATE_KEYSET_DIGEST_MISMATCH")

        expected_pairs = {
            canonical_pair_key(left, right)
            for left, right in itertools.combinations(sorted(expected), 2)
        }
        actual_pairs = set()
        for row in value["pairwise_maneuver_onsets"]:
            pair = canonical_pair_key(*row["candidate_ids"])
            if row["pair_key"] != pair or pair in actual_pairs:
                raise ContractViolation("PAIRWISE_ONSET_COVERAGE_INVALID")
            actual_pairs.add(pair)
        if actual_pairs != expected_pairs:
            raise ContractViolation("PAIRWISE_ONSET_COVERAGE_INVALID")

        context = value["comparison_context"]
        context_id = context["comparison_context_id"]
        source_ids = {
            str(context["source_observation_id"]), str(context["source_frame_id"])
        }
        expected_route = context["route_version_id"]
        alignment = value["alignment_evidence"]
        aligned_source_ids = set()
        if alignment["status"] == "AVAILABLE":
            aligned_source_ids = set(alignment["value"]["aligned_source_ids"])
        for result in _iter_result_objects(value):
            validate_evidence_result(result)
            provenance = result["provenance"]
            if result["authorization_eligible"] or result["safety_critical_eligible"]:
                if provenance["comparison_context_id"] != context_id:
                    raise ContractViolation("AUTHORIZING_CONTEXT_NOT_ALIGNED")
            if result["status"] == "AVAILABLE":
                observed_ids = {
                    str(provenance["source_observation_id"]),
                    str(provenance["source_frame_id"]),
                }
                if not observed_ids.issubset(source_ids | aligned_source_ids):
                    raise ContractViolation("SOURCE_IDENTITY_NOT_ALIGNED")
                result_value = result["value"]
                if isinstance(result_value, Mapping) and "route_version_id" in result_value:
                    if result_value["route_version_id"] != expected_route:
                        raise ContractViolation("ROUTE_VERSION_MISMATCH")
        if value["route_version"]["status"] == "AVAILABLE" and value["route_version"]["value"] != expected_route:
            raise ContractViolation("ROUTE_VERSION_MISMATCH")

        timing = value["time_to_divergence"]
        if timing["status"] == "AVAILABLE" and timing["value"]["lower_bound_s"] is not None:
            lower = timing["value"]["lower_bound_s"]
            upper = timing["value"]["upper_bound_s"]
            if not (math.isfinite(lower) and math.isfinite(upper) and 0 <= lower <= upper):
                raise ContractViolation("TEMPORAL_ORDER_INVALID")
        observed = float(context["observed_monotonic_time"])
        for field in ("latest_safe_clarification_time", "refresh_deadline"):
            result = value[field]
            if result["status"] == "AVAILABLE" and not math.isfinite(float(result["value"])):
                raise ContractViolation("TEMPORAL_ORDER_INVALID", field)
        refresh = value["refresh_deadline"]
        if refresh["status"] == "AVAILABLE" and float(refresh["value"]) < observed:
            raise ContractViolation("TEMPORAL_ORDER_INVALID", "refresh_deadline")

    @staticmethod
    def _validate_persistent_episode(value: Mapping[str, Any]) -> None:
        if value["created_monotonic_time"] > value["updated_monotonic_time"]:
            raise ContractViolation("EPISODE_TIME_ORDER_INVALID")
        ordered = [
            value.get("last_refresh_monotonic_time"),
            value.get("freshness_deadline_monotonic"),
            value.get("expiry_monotonic_time"),
        ]
        present = [item for item in ordered if item is not None]
        if any(left > right for left, right in zip(present, present[1:])):
            raise ContractViolation("FRESHNESS_EXPIRY_ORDER_INVALID")
        if value["semantic_state"] == "UNRESOLVED":
            active = [row for row in value["candidates"] if row["status"] == "ACTIVE_UNRESOLVED"]
            identities = {row["semantic_sha256"] for row in active}
            convergence_pending = bool(
                len(active) == 1
                and len(identities) == 1
                and value["evidence_state"] == "REFRESH_REQUIRED"
                and "RUNTIME_EVIDENCE_CANDIDATE_REJECTED"
                in value["reason_codes"]
            )
            if not convergence_pending and (len(active) < 2 or len(identities) < 2):
                raise ContractViolation("UNRESOLVED_ACTIVE_CANDIDATES_INVALID")
        history = value["history"]
        invalidation_types = {
            "MATERIAL_INVALIDATION", "ROUTE_OR_ENV_OR_WORLD_CHANGE",
            "REFERENT_IDENTITY_LOST", "ANSWER_ARRIVED_VALID",
            "CANDIDATE_REJECTED_BY_EVIDENCE",
        }
        for index, row in enumerate(history):
            if row["event_type"] in invalidation_types:
                if index == 0 or history[index - 1]["event_type"] != "AUTHORIZATION_REVOKED":
                    raise ContractViolation("MATERIAL_INVALIDATION_DID_NOT_REVOKE_FIRST")
        stale_digests = {
            row["reason_code"].split("STALE_PLAN_DIGEST_", 1)[1]
            for row in history
            if row["reason_code"].startswith("STALE_PLAN_DIGEST_")
        }
        action = value.get("current_shared_executable_action")
        if action is not None and action["plan_reference_digest"] in stale_digests:
            raise ContractViolation("STALE_PLAN_REAUTHORIZED")
        if value["audit_sequence"] != len(history):
            raise ContractViolation("AUDIT_CHAIN_DIGEST_MISMATCH")
        if value["audit_digest"] != canonical_sha256(history):
            raise ContractViolation("AUDIT_CHAIN_DIGEST_MISMATCH")
        forbidden = {"RUNTIME_TERMINAL", "LMDRIVE_COMPLETED", "ROUTE_PLANNER_IS_LAST", "ACT_RECEIPT_CONSUMED"}
        if value["semantic_state"] == "RESOLVED" and any(
            row["event_type"] in forbidden for row in history
        ):
            raise ContractViolation("NON_RESOLUTION_EVENT_USED_AS_RESOLUTION")

    @staticmethod
    def _validate_authority_subject(value: Mapping[str, Any]) -> None:
        issued = float(value["issued_monotonic_time"])
        valid_until = float(value["valid_until_monotonic"])
        if not (math.isfinite(issued) and math.isfinite(valid_until) and issued < valid_until):
            raise ContractViolation("AUTHORITY_WINDOW_INVALID")
        if value["subject_type"] == "SHARED_EQUIVALENCE_CLASS":
            if value["candidate_id"] is not None or value["resolved_interpretation_id"] is not None:
                raise ContractViolation("SHARED_SUBJECT_FALSE_RESOLUTION")
            members = value["active_members"]
            sorted_members = sorted(
                members,
                key=lambda row: (row["candidate_id"], row["interpretation_id"]),
            )
            if members != sorted_members or value["active_member_set_digest"] != canonical_sha256(members):
                raise ContractViolation("ACTIVE_MEMBER_SET_INVALID")
        identity_payload = dict(value)
        identity_payload.pop("identity_digest", None)
        if value["identity_digest"] != canonical_sha256(identity_payload):
            raise ContractViolation("AUTHORITY_SUBJECT_IDENTITY_DIGEST_MISMATCH")


def validate_dependency_completeness(
    dependencies: Sequence[EvidenceResult], *, require_safety: bool = False
) -> Tuple[bool, Tuple[str, ...]]:
    reasons: List[str] = []
    for index, dependency in enumerate(dependencies):
        try:
            validate_evidence_result(dependency)
        except ContractViolation as error:
            reasons.append("DEPENDENCY_{}_{}".format(index, error.reason_code))
            continue
        eligible = dependency.is_safety_authorizable if require_safety else dependency.is_runtime_authorizable
        if not eligible:
            reasons.append("DEPENDENCY_{}_NOT_RUNTIME_AUTHORIZABLE".format(index))
    return not reasons, tuple(reasons)
