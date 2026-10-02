"""Opt-in adapter to the existing candidate-specific consequence engine.

Discovery has no route/plan evidence.  The existing engine must therefore receive
the discovered candidate identities and bindings but return UNKNOWN cells.  This
fail-closed result is the intended prototype boundary, not a consequence estimate.
"""

from __future__ import annotations

from typing import Any

from driveclarify_integrated_v1.canonical_ontology import CanonicalTaskBinding
from driveclarify_integrated_v1.consequence_v1 import CandidateSpecificConsequenceEngineV1

from .contracts import DiscoveryResult


_CANONICAL_SLOT = {
    "target_vehicle": "reference_slot",
    "target_road_user": "reference_slot",
    "target_landmark": "landmark_slot",
    "target_access_point": "landmark_slot",
    "target_lane": "constraint_slot",
    "target_area": "constraint_slot",
}


def pass_to_existing_consequence_module(result: DiscoveryResult) -> dict[str, Any]:
    if not result.ambiguity_detected:
        return {
            "status": "NOT_APPLICABLE",
            "reason": "AMBIGUITY_NOT_DETECTED",
            "module": "driveclarify_integrated_v1.consequence_v1.CandidateSpecificConsequenceEngineV1",
        }
    candidate_ids = tuple(item.candidate_id for item in result.candidate_interpretations)
    canonical_slot = _CANONICAL_SLOT.get(result.unresolved_slot or "", "reference_slot")
    bindings = {
        item.candidate_id: CanonicalTaskBinding(
            candidate_id=item.candidate_id,
            task_family="MANEUVER_BRANCH",
            symbolic_target_type=item.entity.entity_type.upper(),
            symbolic_target_id=item.entity.entity_id,
            required_slots=(canonical_slot,),
            semantic_domain="SYMBOLIC_ENVIRONMENT_IDENTIFIER",
            binding_status="BOUND",
            binding_source="AMBIGUITY_DISCOVERY_PROTOTYPE_V0",
            provenance=("DISCOVERED_COMPATIBLE_GROUNDING_CANDIDATE",),
            reason_codes=("PROTOTYPE_SYMBOLIC_BINDING",),
        )
        for item in result.candidate_interpretations
    }
    matrix = CandidateSpecificConsequenceEngineV1().build_matrix(
        candidate_ids,
        evidences={},
        bindings=bindings,
        wrong_goal_costs={},
    )
    return {
        "status": "PASSED_FAIL_CLOSED",
        "reason": "CANDIDATES_ACCEPTED_BUT_PLAN_EVIDENCE_UNAVAILABLE",
        "module": "driveclarify_integrated_v1.consequence_v1.CandidateSpecificConsequenceEngineV1",
        "candidate_ids": list(candidate_ids),
        "candidate_entity_ids": [
            item.entity.entity_id for item in result.candidate_interpretations
        ],
        "matrix": matrix.to_dict(),
        "physical_safety_inference_performed": False,
        "authorization_eligible": False,
    }

