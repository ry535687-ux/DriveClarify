"""Frozen scientific scope, views, hypotheses, endpoints, and analysis rules."""

from __future__ import annotations

from typing import Any, Mapping

from driveclarify_rq2_t.measurement import canonical_sha256


FORMAL_STATUS = "SCENE_AND_PROTOCOL_FREEZE_ONLY"
RESEARCH_QUESTION = (
    "Under certified candidate-grounding conditions, how does interpretation-relevant "
    "evidence evolve as the vehicle approaches commitment, and does field-specific "
    "temporal retention plus joint evidence-margin reasoning improve identification of "
    "actionable clarification windows relative to frame-only evidence and single-factor rules?"
)

CLAIM_BOUNDARY = {
    "conditional_on": [
        "independently certified reasonable candidate interpretations",
        "certified candidate-to-entity or candidate-to-task bindings",
    ],
    "may_estimate": [
        "precommitment evidence sufficiency",
        "actionable clarification-window availability",
        "offline decision-rule classification",
    ],
    "does_not_claim": [
        "automatic E2 generalization", "arbitrary-referent grounding",
        "automatic E2 qualification", "real-user validity", "safety guarantee",
        "closed-loop ASK validity", "downstream task or driving outcome improvement",
    ],
}

VIEWS = {
    "B0": "Exact frozen RQ2-T V1 passive-evidence computation; no controlled candidate evidence.",
    "B1": "Certified evidence from the current source frame only; absent fields remain UNKNOWN; retention forbidden.",
    "B2": "B1 current-frame evidence plus the exact frozen V2 field-specific TTL, binding, freshness, and invalidation memory.",
    "B3": "Postepisode complete certified evidence; nondeployable offline upper bound only.",
}

RULES = {
    "R-EVIDENCE-ONLY": "Trigger at first EpistemicEvidenceSufficient; does not read deadline/actionability to trigger.",
    "R-TIME-ONLY": "Trigger at the prospectively fixed TTCmt=3.0 s crossing; evidence is posthoc classification only.",
    "R-JOINT": "Trigger only when EpistemicEvidenceSufficient and ClarificationActionable are simultaneously true.",
    "R-ORACLE": "Nondeployable B3 upper-bound point; never a policy comparator.",
}

HYPOTHESES = {
    "HCG1": (
        "Within REF-ASYNC, LMK-ASYNC, and ORD-ASYNC, B2 improves truthful "
        "precommitment sufficiency and actionable-window presence relative to B1."
    ),
    "HCG2": (
        "Within REF-SYNC and LMK-SYNC, any B2-only advantage is reported solely as a "
        "manipulation-control result; no equivalence claim is allowed without a frozen margin."
    ),
    "HCG3": (
        "On identical traces and within separately reported timing-condition strata, "
        "R-JOINT avoids the evidence-only late trigger and time-only premature trigger "
        "constructed by the frozen timing conditions."
    ),
    "HCG4": (
        "In NONREVEAL and USC-INTRINSIC, B2 creates neither false sufficiency, "
        "fabricated semantic resolution, nor invalid retention."
    ),
}

PRIMARY_UNIT = "scene × seed episode"
FRAME_ROWS_INDEPENDENT_SAMPLES = False

ENDPOINTS = {
    "precommitment_sufficiency": {
        "numerator": "eligible episodes with at least one truthful sufficiency row strictly before commitment",
        "denominator": "eligible scene×seed episodes in the endpoint's predeclared scene stratum",
        "eligibility": "valid complete episode with certified candidate set, commitment observed, and paired B1/B2 trace",
        "censoring": "no sufficiency before observed commitment is 0; missing commitment or invalid trace is censored and never zero-imputed",
    },
    "first_sufficiency_TTCmt_s": {
        "numerator": "not a ratio; TTCmt at the first truthful sufficient row",
        "denominator": "episodes with a truthful precommitment sufficiency event",
        "eligibility": "endpoint-eligible episode with at least one sufficient row",
        "censoring": "right-censored at commitment when no sufficiency occurs; never assign TTCmt=0",
    },
    "actionable_window_presence": {
        "numerator": "eligible episodes with at least one row where sufficiency and actionability are both true",
        "denominator": "eligible scene×seed episodes in the predeclared scene stratum",
        "eligibility": "valid complete paired episode with observed commitment/deadline",
        "censoring": "0 only after a valid horizon completes without a joint row; invalid/incomplete episode is censored",
    },
    "actionable_window_duration_s": {
        "numerator": "not a ratio; CARLA simulation-time measure of the union of intervals with simultaneous sufficiency and actionability",
        "denominator": "eligible scene×seed episodes, including valid zero-duration windows",
        "eligibility": "valid complete paired episode with regular frozen source-frame sampling and observed deadline",
        "censoring": "truncate only at the frozen natural horizon; invalid/incomplete trace is censored, not assigned zero",
    },
    "B2_only_sufficiency_event": {
        "numerator": "eligible paired episodes with a source frame where B1 sufficiency=false and B2 sufficiency=true before commitment",
        "denominator": "eligible scene×seed episodes within the predeclared ASYNC or SYNC stratum",
        "eligibility": "valid complete episode with exact paired B1/B2 source identities",
        "censoring": "valid completed episode with no such frame is 0; invalid/incomplete pair is censored",
    },
    "rule_classification": {
        "numerator": "episodes assigned each frozen classification by each rule",
        "denominator": "eligible episodes within each separately reported condition stratum and rule",
        "eligibility": "one identical paired source trace available to every offline rule",
        "censoring": "FIXED_POINT_NOT_OBSERVED and no-trigger classes remain explicit; no weighted cross-stratum total",
    },
    "proposed_query_TTCmt_s": {
        "numerator": "not a ratio; TTCmt at the rule's first proposed-query point",
        "denominator": "eligible episodes where that rule proposes a query",
        "eligibility": "valid identical-trace offline rule evaluation",
        "censoring": "no-trigger is an explicit category and carries no fabricated numeric TTCmt",
    },
    "remaining_margin_at_trigger_s": {
        "numerator": "not a ratio; proposed-query TTCmt minus the frozen 1.20-s clarification reserve",
        "denominator": "eligible episodes where that rule proposes a query",
        "eligibility": "valid identical-trace rule evaluation with an observed query point",
        "censoring": "no-trigger has no numeric margin and is reported separately",
    },
    "SUPPORTED_TIMELY_TRIGGER": {
        "numerator": "eligible rule evaluations classified SUPPORTED_TIMELY_TRIGGER",
        "denominator": "eligible episodes within each condition stratum and rule",
        "eligibility": "valid identical-trace offline rule evaluation",
        "censoring": "other classifications remain explicit mutually exclusive outcomes",
    },
    "PREMATURE_UNSUPPORTED_TRIGGER": {
        "numerator": "eligible rule evaluations whose frozen trigger precedes evidence support",
        "denominator": "eligible episodes within each condition stratum and rule",
        "eligibility": "valid identical-trace offline rule evaluation",
        "censoring": "no-trigger and invalid traces are not recoded as premature",
    },
    "TOO_LATE_RULE_TRIGGER": {
        "numerator": "eligible rule evaluations where first evidence-only trigger occurs after the frozen deadline",
        "denominator": "eligible episodes within each condition stratum and rule",
        "eligibility": "valid identical-trace offline rule evaluation",
        "censoring": "no-trigger and invalid traces are not recoded as too late",
    },
    "ACTIONABLE_WINDOW_MISSED": {
        "numerator": "eligible joint-rule evaluations with sufficiency observed only after the actionable deadline and no joint trigger",
        "denominator": "eligible episodes within each condition stratum for R-JOINT",
        "eligibility": "valid identical-trace offline joint-rule evaluation",
        "censoring": "evidence-never-sufficient remains a separate no-trigger classification",
    },
    "no_sufficiency_or_no_trigger": {
        "numerator": "eligible evaluations in a frozen NO_TRIGGER_EVIDENCE_NEVER_SUFFICIENT, NO_CERTIFIED_ORACLE_POINT, or FIXED_POINT_NOT_OBSERVED category",
        "denominator": "eligible episodes within each separately named view/rule and condition stratum",
        "eligibility": "valid complete episode or identical-trace offline evaluation",
        "censoring": "categories are reported verbatim; they are never merged with invalid/incomplete traces",
    },
    "false_sufficiency": {
        "numerator": "negative-control episodes where B2 sufficiency becomes true without all frozen evidence predicates",
        "denominator": "all eligible NONREVEAL and USC-INTRINSIC scene×seed episodes",
        "eligibility": "valid complete negative-control episode",
        "censoring": "invalid/incomplete episodes are censored and disclosed; never counted as successes",
    },
    "fabricated_semantic_resolution": {
        "numerator": "negative-control episodes where absent passenger semantic constraint is changed from UNKNOWN by B1 or B2",
        "denominator": "all eligible NONREVEAL and USC-INTRINSIC scene×seed episodes",
        "eligibility": "valid complete negative-control episode",
        "censoring": "invalid/incomplete episodes are censored and disclosed",
    },
    "invalid_retention_failure": {
        "numerator": "eligible episodes with any retained field surviving its matching frozen invalidation event",
        "denominator": "episodes in which the predeclared invalidation event is observed and the field was previously retained",
        "eligibility": "valid trace spanning a retention interval and its matching invalidation",
        "censoring": "no denominator entry when either prerequisite event is not observed; never zero-impute",
    },
    "stale_evidence_survival_after_invalidation": {
        "numerator": "matching retained field instances still AVAILABLE on the first eligible source frame after the frozen invalidation event",
        "denominator": "retained field instances with an observed matching invalidation and an eligible post-event source frame",
        "eligibility": "field was legally retained before the independently owned invalidation event",
        "censoring": "missing post-event frame is censored; it is never recorded as successful invalidation",
    },
}

ANALYSIS_PLAN = {
    "HCG1": {
        "population": "18 async scene×seed episodes; three scenes and the same six seed slots",
        "estimands": ["paired B2−B1 risk difference for precommitment sufficiency", "paired B2−B1 risk difference for actionable-window presence"],
        "test": "two-sided exact McNemar test on paired discordant episodes",
        "interval": "95% percentile cluster-bootstrap interval from exhaustive enumeration of all 6^6 ordered resamples of six shared seed blocks with replacement (no Monte Carlo RNG); scene-stratified estimates also reported",
        "multiplicity": "Holm correction across the two HCG1 co-primary binary endpoints; descriptive duration/TTCmt estimates are not confirmatory",
    },
    "HCG2": {
        "population": "12 sync manipulation-control episodes",
        "analysis": "report paired B1/B2 endpoint tables and exact B2-only witness counts by scene",
        "claim_rule": "descriptive manipulation check only; neither non-significance nor zero observed discordance establishes equivalence",
    },
    "HCG3": {
        "population": "endpoint-eligible positive episodes",
        "analysis": "apply every rule to the identical source trace; compare exact classification pairs within ASYNC, SYNC, and LATE-REVEAL strata",
        "test": "two-sided exact McNemar test for the predeclared error contrast when discordants are estimable",
        "prohibition": "no weighted total or pooled cross-condition headline",
    },
    "HCG4": {
        "population": "12 negative-control episodes",
        "analysis": "exact numerator/denominator counts for false sufficiency, fabricated resolution, and invalid-retention failure",
        "interval": "two-sided 95% Clopper–Pearson interval for each event probability",
        "multiplicity": "Holm correction across the three HCG4 integrity endpoints",
    },
    "repeated_measurement": "frames are repeated measurements nested inside one scene×seed episode and are never independent samples",
    "confirmatory_completeness": "confirmatory analysis requires all 48/48 valid formal cells; otherwise preserve data and issue no confirmatory claim",
}


def frozen_contract() -> Mapping[str, Any]:
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_contract.v1",
        "formal_status": FORMAL_STATUS,
        "research_question": RESEARCH_QUESTION,
        "claim_boundary": CLAIM_BOUNDARY,
        "views": VIEWS,
        "rules": RULES,
        "hypotheses": HYPOTHESES,
        "primary_unit": PRIMARY_UNIT,
        "frame_rows_independent_samples": FRAME_ROWS_INDEPENDENT_SAMPLES,
        "endpoints": ENDPOINTS,
        "analysis_plan": ANALYSIS_PLAN,
    }
    value["contract_digest"] = canonical_sha256(value)
    return value


__all__ = [
    "ANALYSIS_PLAN", "CLAIM_BOUNDARY", "ENDPOINTS", "FORMAL_STATUS",
    "FRAME_ROWS_INDEPENDENT_SAMPLES", "HYPOTHESES", "PRIMARY_UNIT",
    "RESEARCH_QUESTION", "RULES", "VIEWS", "frozen_contract",
]
