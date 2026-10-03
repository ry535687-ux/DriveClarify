#!/usr/bin/env python3
"""native.consequence agent implementation."""

from __future__ import annotations

import json

from driveclarify.native.contracts import (
    ConsequenceDecision,
    PolicyAction,
    canonical_sha256,
)
from driveclarify.native.base_agent import (
    NativeSimLingoAgent,
    _atomic_json,
)

from driveclarify.core.task_signatures import (
    AmbiguityStatus,
    ConsequenceRelation,
    GateAction,
    TaskSignature,
    compare_task_signatures,
    consequence_gate,
)


def get_entry_point():
    return "ConsequenceSimLingoAgent"


def _forbidden_intent_paths(value, prefix=""):
    findings = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = prefix + "." + str(key) if prefix else str(key)
            lowered = str(key).lower()
            if lowered in {
                "true_intent",
                "true_passenger_intent",
                "oracle_choice",
                "answer_candidate_id",
                "selected_candidate_id",
            }:
                findings.append(path)
            findings.extend(_forbidden_intent_paths(item, path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            findings.extend(_forbidden_intent_paths(item, "%s[%d]" % (prefix, index)))
    return findings


class ConsequenceSimLingoAgent(NativeSimLingoAgent):
    """Use exactly one task comparison and the existing rank-one selector."""

    def _load_native_config(self):
        super()._load_native_config()
        if getattr(self, "_rq1_observed_config_validated", False):
            return
        signatures = self._method_input.get("task_signatures")
        if not isinstance(signatures, list) or len(signatures) != 2:
            raise RuntimeError("RQ1_V2_EXACTLY_TWO_TASK_SIGNATURES_REQUIRED")
        findings = _forbidden_intent_paths(self._method_input)
        if findings:
            raise RuntimeError("RQ1_V2_PREASK_TRUE_INTENT_FIELD_FORBIDDEN:" + ",".join(findings))
        background = self._method_input.get("background_traffic_policy")
        if not isinstance(background, dict):
            raise RuntimeError("RQ1_V2_BACKGROUND_TRAFFIC_POLICY_MISSING")
        if background.get("random_background_vehicle_count") != 0:
            raise RuntimeError("RQ1_V2_RANDOM_BACKGROUND_TRAFFIC_NOT_ZERO")
        if background.get("traffic_manager_random_generation_enabled") is not False:
            raise RuntimeError("RQ1_V2_TRAFFIC_MANAGER_RANDOM_GENERATION_NOT_DISABLED")
        retained = background.get("retained_scientific_actors", [])
        if any(not isinstance(row, dict) or not row.get("scientific_role") for row in retained):
            raise RuntimeError("RQ1_V2_RETAINED_ACTOR_ROLE_MISSING")
        self._rq1_observed_config_validated = True

    def setup(self, path_to_conf_file, route_index=None, traffic_manager=None):
        super().setup(path_to_conf_file, route_index=route_index, traffic_manager=traffic_manager)
        background = self._method_input["background_traffic_policy"]
        receipt = {
            "schema": "driveclarify.rq1_v2.background-traffic-runtime-receipt.v1",
            "random_background_vehicle_count": 0,
            "traffic_manager_random_generation_enabled": False,
            "retained_scientific_actors": background.get("retained_scientific_actors", []),
            "all_retained_actors_have_machine_readable_scientific_role": all(
                row.get("scientific_role") for row in background.get("retained_scientific_actors", [])
            ),
            "unrelated_traffic_retained_for_realism": False,
            "policy_applied_before_first_model_tick": True,
        }
        receipt["receipt_digest"] = canonical_sha256(receipt)
        _atomic_json(self._output / "RQ1_V2_BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", receipt)

    def _evaluate_consequences(self, routes):
        rows = self._method_input["task_signatures"]
        route_ids = [route.candidate.candidate_id for route in routes]
        signature_ids = [str(row.get("candidate_id")) for row in rows]
        if route_ids != signature_ids:
            raise RuntimeError("RQ1_V2_SIGNATURE_ROUTE_ORDER_MISMATCH")
        first, second = (TaskSignature.from_mapping(row) for row in rows)
        comparison = compare_task_signatures(first, second)
        gate = consequence_gate(
            AmbiguityStatus.AMBIGUOUS,
            comparison.relation,
            deterministic_candidate_id=route_ids[0],
        )
        receipt = {
            "schema": "driveclarify.rq1_v2.runtime-consequence-decision.v1",
            "run_id": self._native_config["run_id"],
            "ambiguity_status": "AMBIGUOUS",
            "reasonable_interpretation_count": len(routes),
            "comparison": {
                "relation": comparison.relation.value,
                "compared_components": list(comparison.compared_components),
                "differing_components": list(comparison.differing_components),
                "reason_codes": list(comparison.reason_codes),
                "certificate_ids": list(comparison.certificate_ids),
            },
            "gate": {
                "action": gate.action.value,
                "ambiguity_status": gate.ambiguity_status.value,
                "consequence_relation": gate.consequence_relation.value,
                "selected_candidate_id": gate.selected_candidate_id,
                "reason_codes": list(gate.reason_codes),
            },
            "selected_candidate_rule": "EXISTING_DETERMINISTIC_RANK_ONE_VALID_CANDIDATE",
            "raw_local_waypoint_distance_used": False,
            "passenger_true_intent_operand_present": False,
            "runtime_true_intent_reads_before_ask": 0,
            "candidate_route_identities": [route.route.route_id for route in routes],
        }
        receipt["receipt_digest"] = canonical_sha256(receipt)
        _atomic_json(self._output / "RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json", receipt)
        if gate.action is GateAction.ACT:
            return ConsequenceDecision(
                action=PolicyAction.ACT,
                selected_candidate_id=gate.selected_candidate_id,
                question=None,
                reason_codes=gate.reason_codes,
            )
        if gate.action is GateAction.ASK:
            return ConsequenceDecision(
                action=PolicyAction.ASK,
                selected_candidate_id=None,
                question="Which of the two grounded task interpretations did you mean?",
                reason_codes=gate.reason_codes,
            )
        return ConsequenceDecision(
            action=PolicyAction.WAIT,
            selected_candidate_id=None,
            question=None,
            reason_codes=gate.reason_codes,
        )

    def _commit_resolved_route(self, route):
        """Install the full selected suffix and prove answer-conditioned replanning."""

        before = {
            "active_route_identity": self._online_route_update_owner.active_route_identity,
            "route_generation": getattr(self._online_route_update_owner, "route_generation", None),
        }
        result = super()._commit_resolved_route(route)
        answer_release = self._output / "oracle_exchange" / "FORMAL_ANSWER_RELEASE_RECEIPT.json"
        release = json.loads(answer_release.read_text()) if answer_release.is_file() else None
        receipt = {
            "schema": "driveclarify.rq1_v2.answer-conditioned-full-replan.v1",
            "planner": "EXISTING_SIMLINGO_ONLINE_FULL_ROUTE_RECONNECTION_OWNER",
            "old_b6_transition_manager_used": False,
            "selected_candidate_id": route.route_id.split("-")[1],
            "selected_full_route_id": route.route_id,
            "selected_full_route_point_count": len(route.points),
            "selected_task_destination": list(route.destination_xyz),
            "authority_before": before,
            "installation_receipt": result,
            "passenger_answer_release_receipt_present": release is not None,
            "answer_released_after_durable_ask": (
                None if release is None else release.get("answer_released_after_durable_ask")
            ),
            "additional_vla_forwards": 0,
            "duplicate_candidate_computations": 0,
            "new_controller": False,
            "second_control_writer": False,
        }
        receipt["receipt_digest"] = canonical_sha256(receipt)
        _atomic_json(self._output / "RQ1_V2_FULL_REPLAN_RECEIPT.json", receipt)
        return result


__all__ = ["ConsequenceSimLingoAgent", "get_entry_point"]
