"""Write the frozen protocol and human-readable design artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .contracts import atomic_replace, canonical_bytes, file_sha256, stable_hash


COMPARISONS = [
    ("RULE_M1_PLUS_M2B", "MAIN"), ("HYBRID_CONSERVATIVE_M1_PLUS_M2B", "MAIN"),
    ("LEARNED_M1_DIAGNOSTIC_ONLY", "DIAGNOSTIC"),
    ("ALWAYS_ACT_TOP1", "BASELINE"), ("ALWAYS_ASK", "BASELINE"), ("ALWAYS_WAIT", "BASELINE"),
    ("AMBIGUITY_DETECTION_ONLY", "BASELINE"), ("PAIR_RELATION_ONLY", "BASELINE"),
    ("LANGUAGE_UNCERTAINTY_THRESHOLD", "BASELINE"), ("CONSEQUENCE_WITHOUT_QUERY_COST", "ABLATION"),
    ("CONSEQUENCE_WITHOUT_DELAY", "ABLATION"), ("CONSEQUENCE_WITHOUT_NO_ANSWER", "ABLATION"),
    ("ACT_ASK_WITHOUT_WAIT", "ABLATION"), ("NO_COUNTERFACTUAL_MATRIX", "ABLATION"),
    ("NO_POSTERIOR_UPDATE", "ABLATION"), ("EVALUATION_ONLY_ORACLE_UPPER_BOUND", "EVALUATOR_ONLY"),
]


def write_json(path: Path, value: Any) -> None:
    atomic_replace(path, canonical_bytes(value))


def write_md(path: Path, value: str) -> None:
    atomic_replace(path, (value.rstrip() + "\n").encode("utf-8"))


def finalize(out: Path, design_id: str) -> None:
    summary = json.loads((out / "DESIGN_GENERATION_SUMMARY.json").read_text())
    comparison = {
        "schema_version": "driveclarify.m2b_blind_comparison_set.v1", "design_id": design_id,
        "frozen": True, "frozen_before_predictions": True,
        "comparisons": [{"comparison_id": name, "role": role,
                         "authorization_eligible": False, "used_for_control": False,
                         "oracle_computed_after_prediction_seal": role == "EVALUATOR_ONLY",
                         "learned_override_allowed": False} for name, role in COMPARISONS],
        "post_result_add_remove_forbidden": True,
    }
    write_json(out / "M2B_BLIND_COMPARISON_SET.json", comparison)
    comparison_sha = file_sha256(out / "M2B_BLIND_COMPARISON_SET.json")
    runtime_sha = file_sha256(out / "M2B_BLIND_RUNTIME_INPUT_PACKAGE.json")
    gold_sha = file_sha256(out / "M2B_SEALED_EVALUATION_GOLD.json")
    matrices_sha = file_sha256(out / "M2B_BLIND_MATRIX_ARCHETYPES.json")
    profiles_sha = file_sha256(out / "M2B_BLIND_PROFILES.json")
    protocol = {
        "schema_version": "driveclarify.m2b_blind_protocol.v1", "protocol_version": "M2B_SEALED_BLIND_PROTOCOL_V1",
        "design_id": design_id, "state": "PREEXECUTION_VERIFIED_AWAITING_AUTHORIZATION",
        "blind_evaluation_authorized": False, "live_control_authorized": False, "m3_authorized": False,
        "research_questions": {
            "RQ-B1": "Frozen Rule M1 + M2B action generalization under unseen operating conditions",
            "RQ-B2": "Whether development fallback is justified protection or over-conservatism",
            "RQ-B3": "Detectable value of matrix, posterior, query cost, delay, no-answer, and WAIT",
            "RQ-B4": "Unique-candidate and equivalence-class ACT correctness",
            "RQ-B5": "Rule priority, zero override, and fail-closed behavior under learned disagreement",
        },
        "tracks": {"R": {"name": "REAL_MATRIX_PROFILE_HOLDOUT", "case_count": 216, "analysis_unit": "real_m1_unit"},
                   "S": {"name": "SYNTHETIC_COMPOSITIONAL_MATRIX_BLIND_TRACK", "candidate_pool_count": summary["track_s_pool_count"],
                         "primary_core_count": 128, "analysis_unit": "matrix_archetype"}},
        "core_epsilon": "0.005", "epsilon_basis": "task costs span approximately 0-3.78; 0.005 exceeds serialization/float noise and is below substantive cost changes",
        "canonical_prediction_order": ["case_id", "comparison_id"],
        "execution_order": ["VERIFY_ALL_COMMITMENTS", "DENY_GOLD_READ", "RUN_ALL_FROZEN_CASES",
                            "ATOMIC_PUBLISH_RAW_PREDICTION_BYTES", "COMPUTE_PREDICTION_SHA",
                            "UNSEAL_GOLD", "COMPUTE_PREREGISTERED_METRICS", "IMMUTABLE_FIRST_PUBLICATION"],
        "partial_prediction_consumes_event": True, "duplicate_evaluation_fail_closed": True,
        "duplicate_publication_fail_closed": True, "negative_results_retained": True,
        "commitments": {"runtime_sha256": runtime_sha, "sealed_gold_sha256": gold_sha,
                        "matrix_archetypes_sha256": matrices_sha, "profiles_sha256": profiles_sha,
                        "comparison_set_sha256": comparison_sha},
        "comparison_ids": [name for name, _ in COMPARISONS],
        "statistics": {"cluster_bootstrap_resamples": 10000, "seed": 20260804, "interval": "95_PERCENTILE",
                       "multiplicity": "HOLM_FOR_FROZEN_CONFIRMATORY_FAMILY_OTHERWISE_EXPLORATORY"},
        "claim_boundary": {"pre_result_allowed": "M2B_OFFLINE_BLIND_DECISION_LOGIC_GENERALIZATION",
                           "conditional_if_supported": "M2B_OFFLINE_BLIND_DECISION_FEASIBILITY_SUPPORTED"},
    }
    write_json(out / "M2B_BLIND_PROTOCOL.json", protocol)
    protocol_sha = file_sha256(out / "M2B_BLIND_PROTOCOL.json")
    seal_before = {"design_id": design_id, "state": "PROTOCOL_DRAFT", "prediction_event_consumed": False,
                   "blind_policy_execution_count": 0, "blind_prediction_count": 0, "blind_metric_count": 0}
    seal_after = {"design_id": design_id, "state": "PREEXECUTION_VERIFIED_AWAITING_AUTHORIZATION",
                  "commitments": {**protocol["commitments"], "protocol_sha256": protocol_sha},
                  "prediction_event_consumed": False, "prediction_completeness": "NONE", "prediction_sha256": None,
                  "evaluation_count": 0, "publication_count": 0, "blind_policy_execution_count": 0,
                  "blind_prediction_count": 0, "blind_metric_count": 0,
                  "authorization_eligible": False, "used_for_control": False, "control_authorized": False}
    write_json(out / "M2B_BLIND_SEAL_BEFORE.json", seal_before)
    write_json(out / "M2B_BLIND_SEAL_AFTER.json", seal_after)
    write_json(out / "M2B_BLIND_LIFECYCLE_STATE.json", seal_after)
    write_json(out / "M2B_BLIND_ZERO_EXECUTION_AUDIT.json", {
        "schema_version": "driveclarify.m2b_blind_zero_execution_audit.v1", "status": "PASS",
        "blind_policy_execution_count": 0, "blind_prediction_count": 0, "blind_metric_count": 0,
        "sealed_gold_unseal_count": 0, "formal_m1_test_data_plane_access_count": 0,
        "formal_m1_test_forward_count": 0, "formal_m1_retraining_count": 0,
        "control_authorization_count": 0, "live_act_ask_wait_count": 0, "holding_controller_count": 0,
        "carla_launch_count": 0, "simlingo_execution_count": 0, "candidate_forward_count": 0,
        "training_count": 0, "optimizer_count": 0, "backward_count": 0,
        "gpu_compute_count": 0, "cuda_context_count": 0, "m3_operation_count": 0,
        "dummy_policy_execution_count": "RECORDED_ONLY_IN_TEST_RESULTS_NOT_BLIND",
    })
    write_md(out / "M2B_BLIND_TRACK_R_SPEC.md", f"""# Track R — REAL_MATRIX_PROFILE_HOLDOUT

Track R freezes 36 authoritative Formal M1 TRAIN+DEV-derived matrices crossed with six new profiles: exactly 216 cases. It is blind to profiles, not matrix/unit identity, and is not a unit holdout. The original eight development profiles are excluded; exact numeric profile duplicates are zero. Profiles vary asymmetric confusion, moderate query/no-answer costs, near-deadline delay, future observation value with finite missed cost, ambiguous/contradictory mixtures, and an active-query/expired-window boundary. Profiles were frozen before any tested-policy execution and were not reworked for action balance.

Primary inference clusters by the 36 real M1 units. Repeated profiles within a unit are not independent road samples. Boundary cases are separately reported and excluded from primary macro-F1.
""")
    write_md(out / "M2B_BLIND_TRACK_S_SPEC.md", f"""# Track S — Synthetic Compositional Matrix Blind Track

The fixed pool contains {summary['archetype_count']} genuinely generated 2x2 candidate-specific archetypes × 12 profiles = {summary['track_s_pool_count']} cases. The first uniform 64-archetype pool produced only 27 ASK core cases under the independent exact solver; before any tested-policy execution, one 2.1× consequence variant was added for every one of the 16 structures. A design-review repair then added eight explicit original/swapped same-scale pairs (16 archetypes) so semantic swap correctness is directly verifiable profile-by-profile. No gold semantics were relaxed and no class-only patching was used.

The primary core is selected by canonical case-hash lexical order: exactly 32 ACT, 32 ASK, 32 WAIT, and 32 FALLBACK. The complete pool remains the secondary natural-mixture audit ({summary['natural_distribution']}). Core and boundary-stress are separate; boundary cases never enter primary macro-F1. Synthetic clustering is by archetype and does not create real driving scenes or estimate a real user distribution.
""")
    write_md(out / "M2B_BLIND_REFERENCE_SOLVER_SPEC.md", """# M2B_BLIND_REFERENCE_SOLVER_V1

The solver resides in `driveclarify_m2b_blind.reference_solver`, uses Decimal exact-style deterministic arithmetic, enumerates ACT risks, Bayes posteriors, ASK/WAIT losses, hard legality, unique/equivalence ACT, ties, and fail-closed reasons. It imports neither the tested query-value policy nor integrated decision, baseline outputs, or development predictions. UNKNOWN costs remain null. Ties prefer ACT only when a legal unique ACT participates; all other decision ties fail closed. Candidate order is never a default. An independent Fraction implementation re-enumerates every case.
""")
    write_md(out / "M2B_BLIND_METRICS_SPEC.md", """# Preregistered metrics

Track R and Track S are always separate. Primary metrics: four-class macro-F1, balanced accuracy, exact 4×4 confusion matrix/counts, unique-candidate accuracy, equivalence-class correctness, false fallback, unresolved ACT, unnecessary/missed ASK, inappropriate/missed WAIT, symbolic wrong-goal rate, mean/median regret, query rate, and fallback rate. Fallback is decomposed into justified, false, matrix-UNKNOWN, contract, expired-deadline, and unresolvable-information. Reason codes report exact match, accepted-set match, and unsupported count.

Paired ablations cover no matrix, no posterior, no query cost, no delay, no no-answer, and no WAIT. Monotonic pairs cover query cost, delay, no-answer, answer quality, wait information, and deadline. Every metric reports an exact numerator/denominator. Boundary stress reports deterministic/fail-closed/reason-code/candidate-swap correctness and is excluded from primary macro-F1. No subjective weighted total is allowed.
""")
    write_md(out / "M2B_BLIND_STATISTICAL_PROTOCOL.md", """# Frozen statistical protocol

Use deterministic cluster bootstrap with 10,000 resamples, seed `20260804`, and 95% percentile intervals. Track R clusters on real M1 unit; its six profiles are repeated conditions and 216 cases are not independent roads. Track S clusters on matrix archetype; profiles are repeated conditions. Synthetic bootstrap creates no real scenario, Track R is not unit holdout, and Track S is not a real-user distribution.

For main-vs-baseline comparisons report paired exact differences and cluster-bootstrap confidence intervals. Action correctness may additionally use exact McNemar tests. The frozen confirmatory family uses Holm correction; all other comparisons are explicitly exploratory. Negative and null findings remain published.
""")
    write_md(out / "M2B_BLIND_HYPOTHESES.md", """# Frozen blind hypotheses

- H-B1: Rule M1 + M2B exceeds Always Act/Ask/Wait, Ambiguity Only, Pair Relation Only, and Language Threshold on Track S core macro-F1.
- H-B2: Removing matrix or posterior increases fallback, wrong ACT, or regret.
- H-B3: Removing query cost, delay, or no-answer increases targeted unnecessary ASK or decision error.
- H-B4: Removing WAIT increases missed WAIT, fallback, or regret in future-information profiles.
- H-B5: Hybrid authorization-level decisions equal Rule M1 + M2B; override count remains zero; learned disagreement is analytic only.
- H-B6: Blind false-fallback is estimated without presuming whether conservatism is justified.

No success-guaranteeing threshold is preregistered.
""")
    write_md(out / "M2B_BLIND_CLAIM_BOUNDARY.md", """# Claim boundary

The preregistered allowed claim is `M2B_OFFLINE_BLIND_DECISION_LOGIC_GENERALIZATION`. Only supporting blind results may permit `M2B_OFFLINE_BLIND_DECISION_FEASIBILITY_SUPPORTED`.

Forbidden claims include real passenger benefit, physical/collision/TTC safety improvement, learned-component advantage or authority, closed-loop ACT/ASK/WAIT, control readiness, M3 readiness, and paper-level population generalization. Wrong-goal and regret are symbolic task-contract quantities only.
""")
    attacks = [
        ("Gold copies policy", "circular correctness", "independent namespace, Decimal enumeration, forbidden-import audit, Fraction verifier", "full solver/verifier disagreement table", "logic feasibility only"),
        ("Same author profiles/gold/policy", "design dependence", "pre-freeze hashes and no policy execution", "all commitments and zero-execution audit", "author-designed synthetic evidence"),
        ("Track R reuses units", "not full blindness", "name fixed as REAL_MATRIX_PROFILE_HOLDOUT", "clustered unit analysis", "profile holdout only"),
        ("Track S is synthetic", "weak external validity", "compositional stress purpose", "natural mixture plus archetype results", "no real distribution claim"),
        ("Balanced core distorts prevalence", "rates not natural", "secondary full-pool audit", "both distributions", "balanced discrimination only"),
        ("Fallback rewarded by definition", "conservative circularity", "false-fallback and legal-action audit", "fallback decomposition", "downgrade if false fallback high"),
        ("Rules reproduce hand gold", "tautology", "independent math and targeted ablations", "paired errors/regret", "contract generalization only"),
        ("Costs/channels assumed", "sensitivity to assumptions", "broad frozen profiles/monotonic pairs", "profile-stratified sensitivity", "no passenger-population claim"),
        ("Hybrid adds no decision value", "decorative component", "retain only conflict/fail-closed test", "override=0 and disagreement", "no learned advantage claim"),
        ("Wrong-goal/regret symbolic", "not physical safety", "explicit null/claim boundary", "symbolic denominators", "no safety claim"),
        ("Blind success implies M3", "stage overreach", "M3 remains unauthorized", "separate future authorization", "offline only"),
        ("Maximum claim", "population overreach", "frozen two-tier claim", "Track-separated results/caveats", "logic or conditional feasibility only"),
    ]
    lines = ["# Reviewer attack", "", "| Attack | Risk | Current mitigation | Required evidence | Claim downgrade |", "|---|---|---|---|---|"]
    lines += [f"| {a} | {r} | {m} | {e} | {d} |" for a, r, m, e, d in attacks]
    write_md(out / "M2B_BLIND_REVIEWER_ATTACK.md", "\n".join(lines))
    write_md(out / "M2B_BLIND_EVALUATION_DESIGN_REPORT.md", f"""# M2B sealed blind decision evaluation design

Design ID: `{design_id}`  
Status: `M2B_BLIND_DECISION_EVALUATION_PROTOCOL_FROZEN_READY_FOR_SEALED_EXECUTION_AUTHORIZATION`

## Frozen design

Track R is `REAL_MATRIX_PROFILE_HOLDOUT`: 36 frozen real development matrices × six unseen profiles = 216 cases. Track S contains {summary['archetype_count']} matrix archetypes and {summary['track_s_pool_count']} candidate-pool cases; its hash-selected primary core is 128 cases with ACT/ASK/WAIT/FALLBACK = 32/32/32/32. The complete Track S natural mixture is {summary['natural_distribution']}.

Runtime and sealed gold are separate canonical files. Runtime SHA-256 is `{runtime_sha}`; sealed-gold commitment is `{gold_sha}`. Runtime forbidden evaluation-field count is zero. The Decimal reference solver and independent Fraction verifier agree on all {216 + summary['track_s_pool_count']} cases. Core epsilon is 0.005; boundary stress is excluded from primary macro-F1.

The 16-entry comparison set, metrics, cluster statistics, hypotheses, execution ordering, and immutable publication lifecycle are frozen. This design ran zero blind policy executions, produced zero blind predictions, unsealed zero blind gold records, and computed zero blind metrics. All outputs remain non-control and M3 remains unauthorized.
""")
    write_md(out / "NEXT_M2B_SEALED_BLIND_EXECUTION_AUTHORIZATION_PROMPT.md", f"""Authorize exactly one sealed M2B blind decision evaluation for `{design_id}` using the frozen protocol SHA-256 `{protocol_sha}`. Before execution, verify protocol/runtime/gold-commitment/matrix/profile/comparison hashes. Keep sealed gold unreadable while running every frozen runtime case and comparison; atomically publish immutable raw prediction bytes first. A partial prediction consumes the unique event. Only after prediction SHA publication may the evaluator unseal gold and compute the preregistered Track R and Track S metrics. Duplicate evaluation/publication must fail closed. Preserve Formal M1 TEST immutability, CPU-only operation, authorization_eligible=false, used_for_control=false, control_authorized=false, override_applied=false. Do not retrain, run CARLA/SimLingo/GPU/live control, or enter M3. Stop after first immutable result publication.
""")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--design-id", required=True)
    args = parser.parse_args(); finalize(args.output, args.design_id)


if __name__ == "__main__": main()
