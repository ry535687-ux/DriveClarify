"""Analysis-only independent QueryNecessityGold and oracle firewall."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Sequence

from .measurement import canonical_sha256
from .types import QueryNecessityGold


ANALYSIS_CONTEXT = "POST_EPISODE_ANALYSIS_ONLY"
RUNTIME_CONTEXTS = frozenset({"RUNTIME", "RUNTIME_POLICY", "ONLINE_POLICY"})
FORBIDDEN_POLICY_KEYS = frozenset(
    {
        "QueryNecessityGold",
        "query_necessity_gold",
        "true_passenger_intent",
        "oracle_consequence_truth",
        "gold_latest_useful_query_time",
    }
)


@dataclass(frozen=True)
class IndependentGoldInputs:
    scene_certificate_id: str
    authored_semantic_ambiguity: Optional[bool]
    reasonable_interpretation_ids: Sequence[str]
    true_passenger_intent_id: Optional[str]
    exact_map_route_topology_certified: Optional[bool]
    consequence_truth_by_interpretation: Mapping[str, str]
    clarification_materially_changes_intended_execution: Optional[bool]
    provenance_digests: Sequence[str]


def derive_query_necessity_gold(
    inputs: IndependentGoldInputs, *, execution_context: str
) -> dict[str, Any]:
    if execution_context != ANALYSIS_CONTEXT:
        raise PermissionError("RQ2_T_ORACLE_FIREWALL_RUNTIME_ACCESS_DENIED")
    ids = tuple(str(value) for value in inputs.reasonable_interpretation_ids if value)
    complete = bool(
        inputs.scene_certificate_id
        and inputs.authored_semantic_ambiguity is not None
        and inputs.true_passenger_intent_id
        and inputs.exact_map_route_topology_certified is not None
        and inputs.clarification_materially_changes_intended_execution is not None
        and inputs.provenance_digests
        and ids
        and all(value in inputs.consequence_truth_by_interpretation for value in ids)
    )
    if not complete:
        label = QueryNecessityGold.GOLD_UNKNOWN
        reasons = ["INDEPENDENT_GOLD_DEPENDENCY_INCOMPLETE"]
    elif inputs.true_passenger_intent_id not in ids:
        label = QueryNecessityGold.GOLD_UNKNOWN
        reasons = ["TRUE_INTENT_OUTSIDE_CERTIFIED_INTERPRETATION_SET"]
    elif (
        inputs.authored_semantic_ambiguity is True
        and len(set(ids)) >= 2
        and inputs.exact_map_route_topology_certified is True
        and len({inputs.consequence_truth_by_interpretation[value] for value in ids}) >= 2
        and inputs.clarification_materially_changes_intended_execution is True
    ):
        label = QueryNecessityGold.QUERY_NECESSARY
        reasons = ["INDEPENDENT_SEMANTIC_AND_CONSEQUENCE_TRUTH_REQUIRES_QUERY"]
    else:
        label = QueryNecessityGold.QUERY_NOT_NECESSARY
        reasons = ["INDEPENDENT_TRUTH_DOES_NOT_REQUIRE_QUERY"]
    result = {
        "schema_version": "driveclarify.rq2_t.query_necessity_gold.v1",
        "label": label.value,
        "reason_codes": reasons,
        "scene_certificate_id": inputs.scene_certificate_id,
        "owner": "INDEPENDENT_POST_EPISODE_GOLD_ADJUDICATOR",
        "execution_context": ANALYSIS_CONTEXT,
        "runtime_policy_readable": False,
        "depends_on_t_accum": False,
        "depends_on_runtime_epistemic_sufficiency": False,
        "depends_on_policy_ask": False,
        "depends_on_runtime_uncertainty": False,
        "input_snapshot": asdict(inputs),
    }
    result["gold_digest"] = canonical_sha256(result)
    return result


def classify_query_errors(
    *,
    gold_label: QueryNecessityGold,
    query_issued: bool,
    query_time_simulation_s: Optional[float],
    certified_ambiguity_onset_simulation_s: Optional[float],
    gold_latest_useful_query_time_simulation_s: Optional[float],
) -> dict[str, Optional[bool]]:
    """Apply independently owned event gold; UNKNOWN remains None."""

    if gold_label is QueryNecessityGold.GOLD_UNKNOWN:
        return {
            "premature_query": None,
            "too_late_query": None,
            "unnecessary_query": None,
            "missed_necessary_query": None,
        }
    unnecessary = bool(query_issued and gold_label is QueryNecessityGold.QUERY_NOT_NECESSARY)
    missed = bool(not query_issued and gold_label is QueryNecessityGold.QUERY_NECESSARY)
    premature = bool(
        query_issued
        and query_time_simulation_s is not None
        and certified_ambiguity_onset_simulation_s is not None
        and query_time_simulation_s < certified_ambiguity_onset_simulation_s
    )
    too_late = bool(
        query_issued
        and gold_label is QueryNecessityGold.QUERY_NECESSARY
        and query_time_simulation_s is not None
        and gold_latest_useful_query_time_simulation_s is not None
        and query_time_simulation_s > gold_latest_useful_query_time_simulation_s
    )
    return {
        "premature_query": premature,
        "too_late_query": too_late,
        "unnecessary_query": unnecessary,
        "missed_necessary_query": missed,
    }


def assert_policy_payload_has_no_oracle(value: Any) -> None:
    if isinstance(value, Mapping):
        overlap = FORBIDDEN_POLICY_KEYS.intersection(str(key) for key in value)
        if overlap:
            raise PermissionError("RQ2_T_ORACLE_KEY_IN_POLICY_PAYLOAD:" + sorted(overlap)[0])
        for child in value.values():
            assert_policy_payload_has_no_oracle(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_policy_payload_has_no_oracle(child)


__all__ = [
    "ANALYSIS_CONTEXT",
    "IndependentGoldInputs",
    "assert_policy_payload_has_no_oracle",
    "classify_query_errors",
    "derive_query_necessity_gold",
]
