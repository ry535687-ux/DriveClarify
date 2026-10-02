"""Fail-closed contracts for certified experimental candidate evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256


INTERFACE_ID = "CERTIFIED_CANDIDATE_EVIDENCE_INTERFACE_V1"
EVIDENCE_GRADE = "CERTIFIED_EXPERIMENTAL_ONLY"
ALLOWED_USAGE = "RQ2_T_CG_MECHANISM_EVALUATION_ONLY"
FORBIDDEN_KEYS = {
    "true_intent", "true_passenger_intent", "passenger_true_choice", "correct_answer",
    "expected_answer", "querynecessitygold", "query_necessity_gold", "true_route",
    "expected_method_result", "b2_success_label",
}


def assert_no_true_intent(value: Any, path: str = "controlled") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if normalized in FORBIDDEN_KEYS or normalized.endswith("_gold"):
                raise PermissionError("RQ2_T_CG_TRUE_INTENT_OR_GOLD_KEY_FORBIDDEN:" + path + "." + str(key))
            assert_no_true_intent(child, path + "." + str(key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_no_true_intent(child, path + "[{}]".format(index))


@dataclass(frozen=True)
class CertifiedCandidateBinding:
    candidate_id: str
    interpretation_id: str
    interpretation_text: str
    binding_id: str
    entity_or_task_role: str
    obligation_descriptor: str
    binding_kind: str

    def to_dict(self) -> Mapping[str, Any]:
        value = {
            "candidate_id": self.candidate_id,
            "interpretation_id": self.interpretation_id,
            "interpretation_text": self.interpretation_text,
            "binding_id": self.binding_id,
            "entity_or_task_role": self.entity_or_task_role,
            "obligation_descriptor": self.obligation_descriptor,
            "binding_kind": self.binding_kind,
            "passenger_intent_identified": False,
            "production_deployable_perception_output": False,
        }
        assert_no_true_intent(value)
        value["binding_digest"] = canonical_sha256(value)
        return value


def validate_candidate_bindings(bindings: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    rows = [dict(row) for row in bindings]
    assert_no_true_intent(rows)
    candidate_ids = [str(row.get("candidate_id")) for row in rows]
    interpretation_ids = [str(row.get("interpretation_id")) for row in rows]
    binding_ids = [str(row.get("binding_id")) for row in rows]
    obligations = [str(row.get("obligation_descriptor")) for row in rows]
    if len(rows) < 2:
        raise ValueError("RQ2_T_CG_CANDIDATE_COUNT_LT_TWO")
    if len(set(candidate_ids)) != len(rows) or len(set(interpretation_ids)) != len(rows):
        raise ValueError("RQ2_T_CG_CANDIDATE_OR_INTERPRETATION_ID_NOT_DISTINCT")
    if len(set(binding_ids)) != len(rows) or len(set(obligations)) != len(rows):
        raise ValueError("RQ2_T_CG_BINDINGS_OR_TASK_OBLIGATIONS_NOT_DISTINCT")
    return {
        "candidate_count": len(rows), "candidate_ids_distinct": True,
        "interpretations_distinct": True, "bindings_distinct": True,
        "task_obligations_distinct": True, "true_passenger_intent_present": False,
        "candidate_set_digest": canonical_sha256(rows),
    }


def assert_experimental_only(field: Mapping[str, Any]) -> None:
    if field.get("evidence_grade") != EVIDENCE_GRADE or field.get("allowed_usage") != ALLOWED_USAGE:
        raise PermissionError("RQ2_T_CG_EVIDENCE_NOT_EXPERIMENTAL_ONLY")
    if any(field.get(key) is True for key in (
        "authorize_act", "authorize_ask", "authorize_wait", "safety_authority",
        "production_eligible", "control_eligible",
    )):
        raise PermissionError("RQ2_T_CG_EVIDENCE_PRODUCTION_AUTHORITY_FORBIDDEN")


__all__ = [
    "ALLOWED_USAGE", "EVIDENCE_GRADE", "FORBIDDEN_KEYS", "INTERFACE_ID",
    "CertifiedCandidateBinding", "assert_experimental_only", "assert_no_true_intent",
    "validate_candidate_bindings",
]
