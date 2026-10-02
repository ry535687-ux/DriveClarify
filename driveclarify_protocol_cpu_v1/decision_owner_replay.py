"""CPU-only actual-owner replay for R4.1 decision opportunities."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from driveclarify_persistent_ambiguity_runtime_v1.runtime import (
    PersistentAmbiguityReferentialRuntime,
)

from .contracts import StaticHardGateEvidenceProvider, canonical_sha256


class _Tensor:
    def __init__(self, value: Any) -> None:
        self.value = np.ascontiguousarray(value, dtype=np.float32)
    def detach(self): return self
    def cpu(self): return self
    def contiguous(self): return self
    def numpy(self): return self.value


def _act_owner(evidence: Mapping[str, Any]) -> dict[str, Any]:
    runtime = PersistentAmbiguityReferentialRuntime.__new__(PersistentAmbiguityReferentialRuntime)
    runtime._initial_k1_pending = True
    runtime._initial_k1_emitted = False
    runtime._latest_observation_id = "r4-1-act-observation"
    runtime._latest_frame = 901
    runtime._latest_simulation_time = 90.1
    runtime._runtime_route_version = "R4_1_STATIC_ROUTE"
    runtime._runtime_environment_digest = canonical_sha256("R4_1_STATIC_MAP")
    runtime._hard_gate_evidence_provider = StaticHardGateEvidenceProvider(
        str(evidence.get("physical_status", "UNKNOWN")),
        str(evidence.get("route_status", "UNKNOWN")),
    )
    runtime._method_decision_envelope = None
    runtime._method_decision_history = []
    runtime._method_m3_transactions = []
    runtime._receipt = {"control_source_receipts": [], "hard_gate_evidence_history": []}
    runtime._episode_id = None
    runtime._normal_forwards = 1
    runtime._candidate_forwards = 0
    runtime._latest_image = None
    route = _Tensor([[float(index), 0.0] for index in range(20)]) if evidence.get("plan_valid") is True else None
    speed = _Tensor([[float(index), 0.0] for index in range(10)]) if evidence.get("plan_valid") is True else None
    runtime._emit_initial_k1_decision(route, speed)
    return {
        "decision": runtime._decision.recommendation.decision.value,
        "reason_codes": [str(runtime._receipt["initial_k1_reason"])],
        "owner_module": "driveclarify_persistent_ambiguity_runtime_v1.runtime",
        "owner_class": "PersistentAmbiguityReferentialRuntime",
        "owner_function": "_emit_initial_k1_decision",
        "gate_trace": {
            "effective_K": 1,
            "plan_valid": evidence.get("plan_valid"),
            "physical_status": evidence.get("physical_status"),
            "route_status": evidence.get("route_status"),
            "hard_gate_evidence": runtime._receipt["latest_hard_gate_evidence"],
        },
        "authorization_eligible": runtime._decision.recommendation.decision.value == "ACT",
        "evidence_contract_error": None,
    }


def _persistent_base() -> dict[str, Any]:
    return {
        "active_candidate_count": 2,
        "semantic_state": "UNRESOLVED",
        "current_action_relation": "CURRENT_ACTION_EQUIVALENT",
        "future_obligation_relation": "FUTURE_DIVERGENT",
        "evidence_fresh": True,
        "full_plan_coverage": True,
        "alignment_verified": True,
        "shared_action_safe": True,
        "recoverable": True,
        "lease_valid": True,
        "decision_deadline_available": True,
        "decision_deadline_crossed": False,
        "hard_safety_gate": True,
        "hard_rule_gate": True,
        "multiple_plausible_interpretations": True,
        "material_consequence_divergence": False,
        "answer_changes_decision": True,
        "positive_query_value": False,
        "query_budget_available": True,
        "answer_likely_before_deadline": True,
        "passenger_resolvable": True,
        "query_lifecycle": {},
    }


def _opportunity_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    def add(case_id: str, family: str, evidence: Any, expected: str, matched: str | None, target_gate: str) -> None:
        cases.append({"case_id": case_id, "family": family, "execution_evidence": evidence, "offline_expected": expected, "matched_positive": matched, "target_gate": target_gate})

    act = {"plan_valid": True, "physical_status": "PASS", "route_status": "PASS"}
    add("ACT_POSITIVE", "ACT", act, "ACT", None, "ALL_ACT_GATES")
    invalid = deepcopy(act); invalid["plan_valid"] = False
    add("ACT_NEG_PLAN_INVALID", "ACT", invalid, "FALLBACK", "ACT_POSITIVE", "plan_valid")
    blocked = deepcopy(act); blocked["route_status"] = "BLOCKED"
    add("ACT_NEG_HARD_RULE_BLOCKED", "ACT", blocked, "FALLBACK", "ACT_POSITIVE", "hard_rule_gate")

    shared = _persistent_base()
    add("ACT_SHARED_POSITIVE", "ACT_SHARED", shared, "ACT_SHARED", None, "ALL_ACT_SHARED_GATES")
    for suffix, key, value in (
        ("CURRENT_NOT_SHARED", "current_action_relation", "CURRENT_ACTION_DIVERGENT"),
        ("LEASE_INVALID", "lease_valid", False),
        ("RECOVERABILITY_FALSE", "recoverable", False),
        ("REFRESH_NOT_GUARANTEED", "evidence_fresh", False),
    ):
        item = deepcopy(shared); item[key] = value
        add("ACT_SHARED_NEG_" + suffix, "ACT_SHARED", item, "FALLBACK", "ACT_SHARED_POSITIVE", key)

    ask = _persistent_base()
    ask.update(current_action_relation="CURRENT_ACTION_DIVERGENT", shared_action_safe=False, material_consequence_divergence=True, positive_query_value=True)
    add("ASK_POSITIVE", "ASK", ask, "ASK", None, "ALL_ASK_GATES")
    for suffix, key, value in (
        ("NO_MATERIAL_DIVERGENCE", "material_consequence_divergence", False),
        ("NOT_NEEDED_YET", "positive_query_value", False),
        ("TOO_LATE", "answer_likely_before_deadline", False),
        ("QUERY_UNAVAILABLE", "query_budget_available", False),
    ):
        item = deepcopy(ask); item[key] = value
        add("ASK_NEG_" + suffix, "ASK", item, "FALLBACK", "ASK_POSITIVE", key)

    wait = _persistent_base()
    wait.pop("query_lifecycle", None)
    wait.update(
        current_action_relation="UNKNOWN", query_budget_available=False,
        material_consequence_divergence=False, positive_query_value=False,
        lifecycle_events=[
            "ASK_EMITTED", "QUERY_ACTIVATED", "ANSWER_REMAINS_PENDING",
            "HOLDING_GRANTED", "LEASE_VALIDATED", "QUERY_BOUND_TO_HOLDING",
        ],
    )
    add("WAIT_POSITIVE_LIFECYCLE", "WAIT", wait, "WAIT", None, "ASK_TO_ACTIVE_QUERY_TO_PENDING_HOLD")
    for suffix, event in (
        ("QUERY_NOT_ACTIVE", "QUERY_ACTIVATED"),
        ("ANSWER_NOT_PENDING", "ANSWER_REMAINS_PENDING"),
        ("HOLDING_INVALID", "HOLDING_GRANTED"),
    ):
        item = deepcopy(wait); item["lifecycle_events"].remove(event)
        add("WAIT_NEG_" + suffix, "WAIT", item, "FALLBACK", "WAIT_POSITIVE_LIFECYCLE", event)

    fallback = _persistent_base()
    fallback["active_candidate_count"] = 0
    add("FALLBACK_POSITIVE_K0", "FALLBACK", fallback, "FALLBACK", None, "K_ZERO")
    for suffix, key, value in (
        ("EVIDENCE_INSUFFICIENT", "evidence_fresh", None),
        ("TOO_LATE", "decision_deadline_crossed", True),
        ("HARD_RULE_BLOCKED", "hard_rule_gate", False),
    ):
        item = deepcopy(fallback); item["active_candidate_count"] = 2; item[key] = value
        add("FALLBACK_" + suffix, "FALLBACK", item, "FALLBACK", None, key)
    add("FALLBACK_MALFORMED_EVIDENCE", "FALLBACK", {"active_candidate_count": "two"}, "FALLBACK", None, "malformed_evidence")
    return cases


def build_actual_owner_records() -> list[dict[str, Any]]:
    """Execute first, seal actual record, and only then join offline expected."""
    output = []
    for case in _opportunity_cases():
        evidence = case["execution_evidence"]
        execution = (
            _act_owner(evidence)
            if case["family"] == "ACT"
            else PersistentAmbiguityReferentialRuntime.evaluate_decision_opportunity_lifecycle(evidence)
            if case["family"] == "WAIT"
            else PersistentAmbiguityReferentialRuntime.evaluate_decision_opportunity_evidence(evidence)
        )
        sealed = {
            "schema_version": "driveclarify.decision_opportunity.actual_owner.r4_1.v1",
            "opportunity_id": case["case_id"],
            "family": case["family"],
            "matched_positive_opportunity_id": case["matched_positive"],
            "target_gate_condition": case["target_gate"],
            "input_evidence_sha": canonical_sha256(evidence),
            "execution_evidence": evidence,
            "actual_execution": execution,
            "gold_visible_to_owner": False,
        }
        sealed["sealed_actual_record_sha256"] = canonical_sha256(sealed)
        # Offline auditor join occurs only after the actual record is sealed.
        sealed["offline_audit"] = {
            "expected_decision": case["offline_expected"],
            "observed_decision": execution["decision"],
            "match": execution["decision"] == case["offline_expected"],
            "joined_after_actual_seal": True,
        }
        sealed["final_record_sha256"] = canonical_sha256(sealed)
        output.append(sealed)
    return output


__all__ = ["build_actual_owner_records"]
