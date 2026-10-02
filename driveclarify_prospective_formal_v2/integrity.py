"""Fail-closed integrity primitives for FORMAL_PROSPECTIVE_BATCH_V2.

No function in this module chooses ACT/ASK/WAIT or a candidate.  Decision
authority remains with ``Stage6AOrchestrator`` through the production Stage6B
runtime binding.  This module only rejects contaminated inputs and protects
offline oracle answers until a durable ASK receipt exists.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


FORBIDDEN_RUNTIME_FIELDS = frozenset(
    {
        "expected_decision",
        "expected_ask",
        "oracle_intent",
        "true_route",
        "correct_connector",
        "expected_route",
        "true_intent",
        "true_interpretation",
        "true_candidate",
        "oracle_answer",
        "oracle_route",
        "expected_action",
        "expected_selected_interpretation",
    }
)


class FormalIntegrityError(RuntimeError):
    """A fail-closed prospective-formal integrity rejection."""


def _walk(value: Any, path: str = "$"):
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield str(key), child, child_path
            yield from _walk(child, child_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def assert_runtime_config_clean(config: Mapping[str, Any]) -> None:
    """Reject every evaluator/oracle field at any runtime-config depth."""

    for key, _value, path in _walk(config):
        normalized = key.lower()
        forbidden_family = (
            normalized.startswith("expected_")
            or normalized.startswith("oracle_")
            or normalized.startswith("true_")
            or "ground_truth" in normalized
            or normalized in FORBIDDEN_RUNTIME_FIELDS
        )
        if forbidden_family:
            raise FormalIntegrityError(f"FORBIDDEN_RUNTIME_FIELD:{path}")


@dataclass
class AuditedOracleProvider:
    """Offline evaluator vault bound to a frozen durable-commit index.

    The runtime supplies only a receipt id, observation identity and request
    time.  Receipt contents are re-read from the trusted root and hash-bound by
    the frozen index; caller-provided receipt dictionaries are never trusted.
    """

    _answers: Mapping[str, str]
    receipt_root: Path
    commit_index_sha256: str
    access_count: int = 0
    pre_ask_access_count: int = 0

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def answer_after_ask(
        self,
        case_id: str,
        receipt_id: str,
        *,
        source_observation_id: str,
        request_time: float,
    ) -> str:
        try:
            index_path = self.receipt_root / "ASK_COMMIT_INDEX.json"
            if self._sha256(index_path) != self.commit_index_sha256:
                raise FormalIntegrityError("ASK_COMMIT_INDEX_HASH_MISMATCH")
            index = json.loads(index_path.read_text(encoding="utf-8"))
            entry = index["receipts"][receipt_id]
            receipt_path = (self.receipt_root / entry["relative_path"]).resolve()
            if self.receipt_root.resolve() not in receipt_path.parents:
                raise FormalIntegrityError("ASK_RECEIPT_PATH_ESCAPE")
            if self._sha256(receipt_path) != entry["sha256"]:
                raise FormalIntegrityError("ASK_RECEIPT_HASH_MISMATCH")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            valid = (
                receipt.get("durable") is True
                and receipt.get("action") == "ASK"
                and receipt.get("case_id") == case_id
                and receipt.get("receipt_id") == receipt_id
                and receipt.get("query_id")
                and receipt.get("source_observation_id") == source_observation_id
                and receipt.get("writer_id") == "STAGE6B_PRODUCTION_ASK_COMMIT_WRITER_V1"
                and float(receipt["query_issued_time"]) <= float(receipt["durable_commit_time"])
                and float(receipt["durable_commit_time"]) <= float(request_time)
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.pre_ask_access_count += 1
            raise FormalIntegrityError("ORACLE_READ_BEFORE_DURABLE_ASK_RECEIPT") from exc
        if not valid:
            self.pre_ask_access_count += 1
            raise FormalIntegrityError("ORACLE_READ_BEFORE_DURABLE_ASK_RECEIPT")
        self.access_count += 1
        try:
            return self._answers[case_id]
        except KeyError as exc:
            raise FormalIntegrityError("ORACLE_ANSWER_NOT_AVAILABLE") from exc


def assert_grounding_eligible(receipt: Mapping[str, Any]) -> None:
    """Require scene evidence, two legal options and same-destination routes."""

    required = (
        "ambiguity_family",
        "raw_instruction",
        "scene_entity_a",
        "scene_entity_b",
        "interpretation_a",
        "interpretation_b",
        "route_a",
        "route_b",
        "connector_a",
        "connector_b",
        "decision_anchor",
        "visibility",
    )
    missing = [key for key in required if not receipt.get(key)]
    if missing:
        raise FormalIntegrityError("GROUNDING_FIELDS_MISSING:" + ",".join(missing))
    if receipt.get("scene_entity_a") == receipt.get("scene_entity_b"):
        raise FormalIntegrityError("SECOND_RELEVANT_ENTITY_ABSENT")
    if receipt.get("interpretation_a") == receipt.get("interpretation_b"):
        raise FormalIntegrityError("DISTINCT_INTERPRETATIONS_ABSENT")
    if receipt.get("route_a") == receipt.get("route_b"):
        raise FormalIntegrityError("DISTINCT_ROUTE_BRANCHES_ABSENT")
    if receipt.get("connector_a") == receipt.get("connector_b"):
        raise FormalIntegrityError("DISTINCT_CONNECTORS_ABSENT")
    if receipt.get("same_global_destination_proof") != "PASS":
        raise FormalIntegrityError("SAME_DESTINATION_PROOF_ABSENT")
    if receipt.get("entity_a_runtime_visible") is not True:
        raise FormalIntegrityError("ENTITY_A_NOT_RUNTIME_VISIBLE")
    if receipt.get("entity_b_runtime_visible") is not True:
        raise FormalIntegrityError("ENTITY_B_NOT_RUNTIME_VISIBLE")
    if receipt.get("option_a_legal_feasible") is not True:
        raise FormalIntegrityError("OPTION_A_NOT_LEGAL_FEASIBLE")
    if receipt.get("option_b_legal_feasible") is not True:
        raise FormalIntegrityError("OPTION_B_NOT_LEGAL_FEASIBLE")
    if receipt.get("grounding_eligible") is not True:
        raise FormalIntegrityError("GROUNDING_NOT_DECLARED_ELIGIBLE")


def assert_geometry_report_matches(roster: Mapping[str, Any], reported: Mapping[str, Any]) -> None:
    """Recompute the five geometry identity counts from roster rows."""

    rows = roster.get("cases")
    if not isinstance(rows, list) or not rows:
        raise FormalIntegrityError("GEOMETRY_AUDIT_REQUIRES_NONEMPTY_ROSTER")
    keys = {
        "unique_map_count": "map",
        "unique_route_hash_count": "route_pair_sha256",
        "unique_anchor_identity_count": "decision_anchor",
        "unique_connector_pair_count": "connector_pair_sha256",
        "unique_scene_entity_configuration_count": "scene_entity_configuration_sha256",
    }
    for report_key, row_key in keys.items():
        actual = len({row.get(row_key) for row in rows if row.get(row_key)})
        if actual != reported.get(report_key):
            raise FormalIntegrityError(f"GEOMETRY_COUNT_MISMATCH:{report_key}:{actual}")


def assert_family_scene_diversity(roster: Mapping[str, Any]) -> None:
    """Reject mapping all ambiguity families to one physical scene identity."""

    rows = roster.get("cases")
    if not isinstance(rows, list) or not rows:
        raise FormalIntegrityError("SCENE_DIVERSITY_REQUIRES_NONEMPTY_ROSTER")
    mapping: dict[str, set[str]] = {}
    for row in rows:
        mapping.setdefault(str(row.get("ambiguity_family")), set()).add(
            str(row.get("scene_entity_configuration_sha256"))
        )
    if len(mapping) < 4 or len(set().union(*mapping.values())) < 4:
        raise FormalIntegrityError("FOUR_FAMILIES_COLLAPSE_TO_SHARED_SCENE")


def assert_arm_wiring(config: Mapping[str, Any]) -> None:
    """Reject hard-coded decisions and candidate-generator bypasses."""

    assert_runtime_config_clean(config)
    required = {
        "candidate_generator_owner": "RuntimeCandidateGenerator.generate",
        "consequence_owner": "Stage6BUnifiedSimLingoBinding._decision_gates",
        "decision_owner": "Stage6AOrchestrator.run",
    }
    for key, suffix in required.items():
        if not str(config.get(key, "")).endswith(suffix):
            raise FormalIntegrityError(f"REQUIRED_PRODUCTION_OWNER_MISSING:{key}")
    if config.get("invoke_candidate_generator") is not True:
        raise FormalIntegrityError("CANDIDATE_GENERATOR_BYPASS")
    if config.get("fixed_candidates") is not None:
        raise FormalIntegrityError("FIXED_CANDIDATE_BYPASS_FORBIDDEN")
    if config.get("hardcoded_action") is not None or config.get("hardcoded_ask_count") is not None:
        raise FormalIntegrityError("HARDCODED_DECISION_FORBIDDEN")
