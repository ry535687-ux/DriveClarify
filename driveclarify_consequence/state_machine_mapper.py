"""State-machine mapper — maps CandidateConsequenceV0[] into AdapterToStateMachineV0, and (only when
explicitly requested) invokes the FROZEN reducer to obtain a shadow DecisionRecord.

Rules (STATE_MACHINE_INPUT_CONTRACT_V0):
  - Raw geometry never enters consequence_equivalence.
  - Diagnostic latency never enters time_to_decision.
  - Empty actors never => hard safety FALSE; unknown collision never => SAFE; missing rule => never
    rule FALSE. Any physical field UNKNOWN keeps the mapped field UNKNOWN.
  - The mapper NEVER duplicates reducer enums/logic; it imports driveclarify_offline.
  - In v0 all physical fields are UNKNOWN => the reducer's hard-feasibility gate yields FALLBACK.
  - A shadow decision may be used for shadow_decision only; NEVER for control.
Pure; no CARLA/SimLingo/GPU.
"""

from __future__ import annotations

import copy
from typing import Any

# Import the FROZEN reducer — do not reimplement.
from driveclarify_offline.evaluator import evaluate_input as _reducer_evaluate
from driveclarify_offline.fixtures import load_fixture_document as _load_reducer_fixture

STATE_MACHINE_SCHEMA_VERSION = "driveclarify.adapter_state_machine_input.v0"


def _candidate_set_status(consequences: list[dict[str, Any]]) -> str:
    if not consequences:
        return "EMPTY"
    valids = []
    for c in consequences:
        cv = c.get("integrity", {}).get("candidate_valid", {})
        valids.append(cv.get("status") == "AVAILABLE" and cv.get("value") is True)
    if all(valids):
        return "VALID"
    if any(valids):
        return "MIXED"
    return "INVALID"


def _candidate_validity_tri(consequences: list[dict[str, Any]]) -> str:
    statuses = []
    for c in consequences:
        cv = c.get("integrity", {}).get("candidate_valid", {})
        if cv.get("status") != "AVAILABLE":
            return "UNKNOWN"
        statuses.append(bool(cv.get("value")))
    if not statuses:
        return "UNKNOWN"
    return "TRUE" if all(statuses) else "FALSE"


def _candidate_freshness_tri(consequences: list[dict[str, Any]]) -> str:
    for c in consequences:
        st = c.get("integrity", {}).get("stale", {})
        if st.get("status") == "STALE":
            return "FALSE"  # a stale candidate => freshness FALSE
        if st.get("status") != "AVAILABLE":
            return "UNKNOWN"
    return "TRUE"


def _data_quality(record: dict[str, Any]) -> str:
    ws = record.get("world_state", {})
    degraded = False
    for key in ("actors", "traffic_lights", "route_context"):
        qs = ws.get(key, {}).get("query_status")
        if qs in ("EXCEPTION", "UNSUPPORTED", "MISSING", "STALE", "UNKNOWN_WITH_REASON"):
            degraded = True
    return "DEGRADED" if degraded else "OK"


def map_to_state_machine(record: dict[str, Any], consequences: list[dict[str, Any]],
                         comparison: dict[str, Any]) -> dict[str, Any]:
    """Build the AdapterToStateMachineV0 dict. All physical divergence/hard fields UNKNOWN in v0."""
    md = record.get("metadata", {})
    reason_codes: list[str] = []

    # v0 mandatory UNKNOWNs (physical consequences not established).
    consequence_equivalence = "UNKNOWN"
    material_task_divergence = "UNKNOWN"
    material_safety_divergence = "UNKNOWN"
    hard_safety_violation = "UNKNOWN"
    hard_rule_violation = "UNKNOWN"
    time_to_decision_status = "UNKNOWN"
    query_value_status = "UNKNOWN"
    holding_plan_status = "UNKNOWN"

    reason_codes.append("UNKNOWN_HARD_SAFETY")
    reason_codes.append("UNKNOWN_HARD_RULE")
    if comparison.get("shadow_reason_proposal"):
        reason_codes.append(comparison["shadow_reason_proposal"])

    return {
        "schema_version": STATE_MACHINE_SCHEMA_VERSION,
        "observation_id": md.get("observation_id", ""),
        "candidate_set_id": md.get("candidate_set_id", ""),
        "candidate_set_status": _candidate_set_status(consequences),
        "candidate_count": len(consequences),
        "candidate_validity": _candidate_validity_tri(consequences),
        "candidate_freshness": _candidate_freshness_tri(consequences),
        "consequence_equivalence": consequence_equivalence,
        "material_task_divergence": material_task_divergence,
        "material_safety_divergence": material_safety_divergence,
        "hard_safety_violation": hard_safety_violation,
        "hard_rule_violation": hard_rule_violation,
        "time_to_decision_status": time_to_decision_status,
        "query_value_status": query_value_status,
        "holding_plan_status": holding_plan_status,
        "data_quality_status": _data_quality(record),
        "adapter_reason_codes": sorted(set(reason_codes)),
        "maps_to_reducer_inputs": {
            "consequence_record_shape": "driveclarify.consequence.v0.1",
            "divergence_fields": {"D_G": "UNKNOWN", "D_S": "UNKNOWN", "D_R": "UNKNOWN", "D_I": "UNKNOWN"},
            "hard_feasibility_fields": {"safety_class": "UNKNOWN", "severe_rule_conflict": None},
        },
    }


def invoke_shadow_reducer(sm_input: dict[str, Any], consequences: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the FROZEN reducer's real input from the mapped UNKNOWN fields and run it, returning a
    shadow decision dict {decision, reason_code, next_state}. NEVER used for control.

    Constructs a schema-valid driveclarify.consequence.v0.1 record with safety_class=UNKNOWN and
    severe_rule_conflict=None (the v0 mapping), so the reducer's hard gate yields FALLBACK.
    """
    doc = _load_reducer_fixture()
    policy_input = copy.deepcopy(doc["base_policy_input"])
    base_cons = copy.deepcopy(doc["base_consequence"])

    obs = sm_input.get("observation_id", "shadow_obs")
    episode = policy_input["active_episode_id"]
    cache = policy_input["candidate_cache"]

    cons_records = []
    cand_ids = []
    for i, c in enumerate(consequences or [{"candidate_id": "z1"}]):
        rec = copy.deepcopy(base_cons)
        cid = str(c.get("candidate_id", f"z{i+1}"))
        cand_ids.append(cid)
        rec["candidate_id"] = cid
        rec["observation_id"] = obs
        rec["cache_id"] = cache["cache_id"]
        rec["episode_id"] = episode
        # v0 mapping: physical consequences UNKNOWN -> forces UNKNOWN_HARD_SAFETY fallback.
        rec["safety"]["safety_class"] = "UNKNOWN"
        rec["rule"]["severe_rule_conflict"] = None
        cons_records.append(rec)

    cache["candidate_ids"] = cand_ids
    cache["source_observation_id"] = obs
    cache["episode_id"] = episode
    policy_input["consequences"] = cons_records

    decision = _reducer_evaluate(policy_input)
    return {
        "decision": decision.decision.value,
        "reason_code": decision.reason_code,
        "next_state": decision.next_state.value,
        "shadow_only": True,
        "used_for_control": False,
    }
