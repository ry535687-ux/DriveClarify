#!/usr/bin/env python3
"""RQ3 native agent: frozen RQ1 consequence gate followed by frozen RQ2 timing.

The class is deliberately a thin subclass of the frozen RQ1-V2 native agent.
It does not call the VLA, planner, PID, or VehicleControl writer itself.  For a
TASK_CRITICAL decision it builds a certified runtime evidence row, applies the
unchanged RQ2-T V2 temporal memory, and calls the unchanged RQ2 epistemic and
actionability predicates before allowing the inherited durable ASK path.
"""

from __future__ import annotations

import json
import math
import time

from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

from driveclarify_clear_passthrough_v11.contracts import (
    ConsequenceDecision,
    PolicyAction,
    canonical_sha256,
)
from driveclarify_clear_passthrough_v11.simlingo_agent import _atomic_json
from driveclarify_rq1_v2.simlingo_agent import DriveClarifyRQ1V2SimLingoAgent
from driveclarify_rq2_t.measurement import (
    DeadlineContract,
    clarification_actionable,
    epistemic_evidence_sufficient,
)
from driveclarify_rq2_t.types import EVIDENCE_FIELD_IDS
from driveclarify_rq2_t_v2.memory import FIELD_MEMORY_POLICIES, TemporalEvidenceMemory


FIXED_DELTA_SECONDS = 0.05
T_FIXED_SECONDS = 3.0
DEADLINE_CONTRACT = DeadlineContract(0.5, 11, 3, FIXED_DELTA_SECONDS)
if not math.isclose(DEADLINE_CONTRACT.total_reserved_simulation_s, 1.20):
    raise RuntimeError("RQ3_FROZEN_RQ2_RESERVE_NOT_1_20_SECONDS")


def get_entry_point():
    return "DriveClarifyRQ3SimLingoAgent"


def _field(field_id, value, now_s, frame, owner):
    result = {
        "field_id": field_id,
        "status": "AVAILABLE",
        "value": value,
        "units": "CERTIFIED_RQ3_RUNTIME_EVENT_STATE",
        "frame": "EXACT_NATIVE_SOURCE_FRAME",
        "simulation_timestamp_s": float(now_s),
        "source_frame_id": int(frame),
        "source_observation_id": "rq3-frame-" + str(frame),
        "owner": owner,
        "evidence_grade": "CERTIFIED_RQ3_RUNTIME_OBSERVED",
        "allowed_usage_purpose": "RQ3_FROZEN_CLOSED_LOOP_INTEGRATION",
        "freshness_age_simulation_s": 0.0,
        "dependencies": ["frozen RQ1 candidate and TaskSignature contracts"],
        "reason_codes": [],
        "retention": None,
    }
    result["evidence_digest"] = canonical_sha256(result)
    return result


def _unknown(field_id, now_s, frame):
    result = {
        "field_id": field_id,
        "status": "UNKNOWN",
        "value": None,
        "units": None,
        "frame": "EXACT_NATIVE_SOURCE_FRAME",
        "simulation_timestamp_s": float(now_s),
        "source_frame_id": int(frame),
        "source_observation_id": "rq3-frame-" + str(frame),
        "owner": "RQ3_FAIL_CLOSED_RUNTIME_ADAPTER",
        "evidence_grade": "UNKNOWN_PRESERVED",
        "allowed_usage_purpose": "RQ3_FROZEN_CLOSED_LOOP_INTEGRATION",
        "freshness_age_simulation_s": 0.0,
        "dependencies": [],
        "reason_codes": ["FIELD_NOT_REQUIRED_AT_CURRENT_RQ3_DECISION"],
        "retention": None,
    }
    result["evidence_digest"] = canonical_sha256(result)
    return result


class DriveClarifyRQ3SimLingoAgent(DriveClarifyRQ1V2SimLingoAgent):
    """One decision adapter; inherited SimLingo remains the only control owner."""

    def _load_v11_config(self):
        super()._load_v11_config()
        if getattr(self, "_rq3_config_validated", False):
            return
        temporal = self._method_input.get("rq3_temporal_contract")
        if temporal is not None:
            if temporal.get("rule") != "R-JOINT(B2)":
                raise RuntimeError("RQ3_TEMPORAL_RULE_NOT_FROZEN_R_JOINT_B2")
            if float(temporal.get("T_FIXED_s")) != T_FIXED_SECONDS:
                raise RuntimeError("RQ3_T_FIXED_CHANGED")
            if not math.isclose(float(temporal.get("reserve_s")), 1.20):
                raise RuntimeError("RQ3_RESERVE_CHANGED")
            if temporal.get("clock") != "CARLA_SIMULATION_TIME":
                raise RuntimeError("RQ3_CLOCK_DOMAIN_CHANGED")
            if temporal.get("evidence_anchor_certified") is not True:
                raise RuntimeError("RQ3_EVIDENCE_ANCHOR_NOT_CERTIFIED")
        self._rq3_config_validated = True

    def setup(self, path_to_conf_file, route_index=None, traffic_manager=None):
        super().setup(path_to_conf_file, route_index=route_index, traffic_manager=traffic_manager)
        self._rq3_memory = TemporalEvidenceMemory()
        self._rq3_gate_time_s = None
        self._rq3_first_sufficiency = None
        self._rq3_ask = None
        self._rq3_answer = None
        self._rq3_replan = None
        self._rq3_temporal_history = []
        self._rq3_candidate_generation_wall_s = 0.0
        self._rq3_candidate_binding_wall_s = 0.0

    def _generate_interpretations(self, gate_input):
        started = time.perf_counter()
        result = super()._generate_interpretations(gate_input)
        self._rq3_candidate_generation_wall_s += time.perf_counter() - started
        return result

    def _bind_candidate_route(self, candidate):
        started = time.perf_counter()
        result = super()._bind_candidate_route(candidate)
        self._rq3_candidate_binding_wall_s += time.perf_counter() - started
        return result

    @staticmethod
    def _simulation_time():
        world = CarlaDataProvider.get_world()
        if world is None:
            raise RuntimeError("RQ3_WORLD_UNAVAILABLE_FOR_SIMULATION_CLOCK")
        return float(world.get_snapshot().timestamp.elapsed_seconds)

    def _runtime_evidence_fields(self, routes, now_s, frame):
        candidate_ids = [route.candidate.candidate_id for route in routes]
        signatures = self._method_input["task_signatures"]
        fields = {field_id: _unknown(field_id, now_s, frame) for field_id in EVIDENCE_FIELD_IDS}
        fields["E1_INTERPRETATION_VALIDITY"] = _field(
            "E1_INTERPRETATION_VALIDITY",
            {
                "semantic_state": "UNRESOLVED",
                "candidate_ids": candidate_ids,
                "active_candidate_count": len(candidate_ids),
                "multiple_reasonable_interpretations": len(candidate_ids) >= 2,
                "planning_relevant": True,
                "planning_effective_k": len(candidate_ids),
            },
            now_s,
            frame,
            "FROZEN_RQ1_CANDIDATE_SET_OWNER",
        )
        fields["E2_GROUNDING"] = _field(
            "E2_GROUNDING",
            {
                "grounding_complete": True,
                "candidate_groundings": [
                    {"candidate_id": candidate_id, "binding_id": "RQ3-" + candidate_id}
                    for candidate_id in candidate_ids
                ],
            },
            now_s,
            frame,
            "RQ3_CERTIFIED_DECISION_ANCHOR_OWNER",
        )
        fields["E4_FUTURE_OBLIGATION_RELATION"] = _field(
            "E4_FUTURE_OBLIGATION_RELATION",
            {
                "relation": "DIVERGENT",
                "authorization_eligible": True,
                "obligation_digests": [canonical_sha256(row) for row in signatures],
            },
            now_s,
            frame,
            "FROZEN_RQ1_TASK_SIGNATURE_OWNER",
        )
        fields["E5_ROUTE_LANE_TOPOLOGY_RELATION"] = _field(
            "E5_ROUTE_LANE_TOPOLOGY_RELATION",
            {"all_physical_connectors_available": True, "candidate_topology_count": len(routes)},
            now_s,
            frame,
            "PREFORMAL_FULL_REPLAN_ADMISSIBILITY_OWNER",
        )
        fields["E6_CANDIDATE_CONSEQUENCE_DIVERGENCE"] = _field(
            "E6_CANDIDATE_CONSEQUENCE_DIVERGENCE",
            {"material_divergence": True, "candidate_relationship": "DIVERGENT"},
            now_s,
            frame,
            "FROZEN_RQ1_CONSEQUENCE_COMPARATOR",
        )
        fields["E7_SAFETY_RULE_HOLDING"] = _field(
            "E7_SAFETY_RULE_HOLDING",
            {
                "information_action_safe": True,
                "motion_rule_admissible": True,
                "safe_holding_available": True,
                "shared_action_lease_valid": True,
                "safe_holding_or_shared_action_admissible": True,
            },
            now_s,
            frame,
            "NATIVE_SIMLINGO_SAFE_CONTINUATION_OWNER",
        )
        fields["E9_ANSWER_CHANGES_ACTION"] = _field(
            "E9_ANSWER_CHANGES_ACTION",
            {"answer_changes_next_meaningful_decision": True},
            now_s,
            frame,
            "FROZEN_TASK_DIVERGENCE_OWNER",
        )
        return fields

    def _evaluate_consequences(self, routes):
        decision_started_wall = time.perf_counter()
        rq1_decision = super()._evaluate_consequences(routes)
        consequence_completed_wall = time.perf_counter()
        if rq1_decision.action is not PolicyAction.ASK:
            latency = {
                "schema": "driveclarify.rq3.decision-latency.v1",
                "consequence_gate_latency_wall_s": consequence_completed_wall - decision_started_wall,
                "candidate_generation_latency_wall_s": self._rq3_candidate_generation_wall_s,
                "candidate_binding_latency_wall_s": self._rq3_candidate_binding_wall_s,
                "candidate_evaluation_latency_wall_s": self._rq3_candidate_generation_wall_s + self._rq3_candidate_binding_wall_s,
                "rq2_decision_latency_wall_s": 0.0,
                "total_decision_latency_wall_s": consequence_completed_wall - decision_started_wall,
                "rq2_invoked": False,
            }
            latency["receipt_digest"] = canonical_sha256(latency)
            _atomic_json(self._output / "RQ3_DECISION_LATENCY_RECEIPT.json", latency)
            return rq1_decision

        frame = int(self._latest_supervision_frame)
        now_s = self._simulation_time()
        if self._rq3_gate_time_s is None:
            self._rq3_gate_time_s = now_s
        commitment_time_s = self._rq3_gate_time_s + T_FIXED_SECONDS
        deadline_s = commitment_time_s - DEADLINE_CONTRACT.total_reserved_simulation_s
        context = {
            "episode_id": self._v11_config["run_id"],
            "route_version": self._official_input_route_sha256,
            "actor_binding_digest": canonical_sha256(self._method_input["task_signatures"]),
            "candidate_set_digest": canonical_sha256([route.candidate.candidate_id for route in routes]),
            "instruction_digest": canonical_sha256(self._method_input["instruction"]),
            "environment_digest": canonical_sha256({"run": self._v11_config["run_id"], "anchor": self._method_input.get("observation_anchor_xyz")}),
            "topology_boundary_id": "RQ3_PRECOMMITMENT",
            "source_frame_id": frame,
            "safety_state_digest": "SAFE_NATIVE_CONTINUATION",
            "holding_lease_id": "RQ3_NATIVE_SHARED_PREFIX",
            "dynamics_state_digest": "CURRENT_NATIVE_STATE",
        }
        direct = self._runtime_evidence_fields(routes, now_s, frame)
        retained = self._rq3_memory.update(direct, now_s=now_s, context=context)
        row = {
            "simulation_time_s": now_s,
            "source_frame_id": frame,
            "source_observation_id": "rq3-frame-" + str(frame),
            "evidence_vector": retained,
        }
        sufficient = epistemic_evidence_sufficient(row)
        actionable = clarification_actionable(
            row, clarification_deadline_simulation_s=deadline_s
        )
        ttcmt_s = commitment_time_s - now_s
        remaining_margin_s = deadline_s - now_s
        if sufficient and self._rq3_first_sufficiency is None:
            self._rq3_first_sufficiency = {
                "frame": frame,
                "simulation_time_s": now_s,
                "TTCmt_s": ttcmt_s,
            }
        decision = "ASK" if sufficient and actionable else "WAIT"
        if sufficient and not actionable:
            decision = "MISSED_ACTIONABLE_WINDOW"
        record = {
            "schema": "driveclarify.rq3.temporal-decision-row.v1",
            "run_id": self._v11_config["run_id"],
            "source_frame_id": frame,
            "simulation_time_s": now_s,
            "TTCmt_s": ttcmt_s,
            "clarification_deadline_simulation_time_s": deadline_s,
            "remaining_decision_margin_s": remaining_margin_s,
            "EpistemicEvidenceSufficient": sufficient,
            "ClarificationActionable": actionable,
            "ClarificationOpportunity": bool(sufficient and actionable),
            "decision": decision,
            "rule": "R-JOINT(B2)",
            "temporal_memory_policy_digest": canonical_sha256({
                key: {
                    "max_age_simulation_s": policy.max_age_simulation_s,
                    "binding_keys": policy.binding_keys,
                    "invalidation_events": policy.invalidation_events,
                    "retention_mode": policy.retention_mode,
                }
                for key, policy in FIELD_MEMORY_POLICIES.items()
            }),
            "memory_snapshot": self._rq3_memory.snapshot(),
            "runtime_true_intent_reads": 0,
        }
        record["record_digest"] = canonical_sha256(record)
        self._rq3_temporal_history.append(record)
        with (self._output / "RQ3_TEMPORAL_DECISION_TIMELINE.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        if decision == "ASK":
            self._rq3_ask = {
                "frame": frame,
                "simulation_time_s": now_s,
                "TTCmt_s": ttcmt_s,
                "remaining_margin_s": remaining_margin_s,
            }
            receipt = {
                "schema": "driveclarify.rq3.temporal-decision-receipt.v1",
                "rule": "R-JOINT(B2)",
                "T_FIXED_s": T_FIXED_SECONDS,
                "deadline_contract": DEADLINE_CONTRACT.to_dict(),
                "first_evidence_sufficiency": self._rq3_first_sufficiency,
                "ask": self._rq3_ask,
                "no_ask_after_deadline": remaining_margin_s >= -1.0e-9,
                "runtime_true_intent_reads": 0,
            }
            receipt["receipt_digest"] = canonical_sha256(receipt)
            _atomic_json(self._output / "RQ3_TEMPORAL_DECISION_RECEIPT.json", receipt)
            completed_wall = time.perf_counter()
            latency = {
                "schema": "driveclarify.rq3.decision-latency.v1",
                "consequence_gate_latency_wall_s": consequence_completed_wall - decision_started_wall,
                "candidate_generation_latency_wall_s": self._rq3_candidate_generation_wall_s,
                "candidate_binding_latency_wall_s": self._rq3_candidate_binding_wall_s,
                "candidate_evaluation_latency_wall_s": self._rq3_candidate_generation_wall_s + self._rq3_candidate_binding_wall_s,
                "rq2_decision_latency_wall_s": completed_wall - consequence_completed_wall,
                "total_decision_latency_wall_s": completed_wall - decision_started_wall,
                "rq2_invoked": True,
            }
            latency["receipt_digest"] = canonical_sha256(latency)
            _atomic_json(self._output / "RQ3_DECISION_LATENCY_RECEIPT.json", latency)
            return rq1_decision
        return ConsequenceDecision(
            action=PolicyAction.WAIT,
            selected_candidate_id=None,
            question=None,
            reason_codes=("RQ2_JOINT_EVIDENCE_MARGIN_NOT_ACTIONABLE",),
        )

    def _read_passenger_answer(self, receipt):
        answer = super()._read_passenger_answer(receipt)
        if answer is not None and self._rq3_answer is None:
            now_s = self._simulation_time()
            frame = int(self._latest_supervision_frame)
            self._rq3_answer = {
                "frame": frame,
                "simulation_time_s": now_s,
                "answer_latency_simulation_s": (
                    None if self._rq3_ask is None else now_s - self._rq3_ask["simulation_time_s"]
                ),
            }
        return answer

    def _commit_resolved_route(self, route):
        started_wall = time.perf_counter()
        started_sim = self._simulation_time()
        result = super()._commit_resolved_route(route)
        completed_sim = self._simulation_time()
        self._rq3_replan = {
            "started_simulation_time_s": started_sim,
            "completed_simulation_time_s": completed_sim,
            "latency_simulation_s": completed_sim - started_sim,
            "latency_wall_s": time.perf_counter() - started_wall,
            "selected_candidate_id": route.route_id.split("-")[1],
            "committed": bool(result.get("committed")),
        }
        timing = {
            "schema": "driveclarify.rq3.lifecycle-timing.v1",
            "ask": self._rq3_ask,
            "answer": self._rq3_answer,
            "full_replan": self._rq3_replan,
            "first_evidence_sufficiency": self._rq3_first_sufficiency,
        }
        timing["receipt_digest"] = canonical_sha256(timing)
        _atomic_json(self._output / "RQ3_LIFECYCLE_TIMING_RECEIPT.json", timing)
        return result


__all__ = [
    "DEADLINE_CONTRACT",
    "DriveClarifyRQ3SimLingoAgent",
    "FIXED_DELTA_SECONDS",
    "T_FIXED_SECONDS",
    "get_entry_point",
]
