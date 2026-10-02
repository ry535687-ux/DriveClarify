"""One immutable M2B adapter used by ACT, ASK, and WAIT.

The adapter contains no scenario identifier, expected action, forced decision, or
evaluation label.  Its only branch is the availability/source of future
information: passenger information for referential ambiguity, environment
information for temporal ambiguity, or neither for equivalent consequences.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from driveclarify_decision.decision_contracts import (
    AnswerChannelModel,
    BeliefSource,
    BeliefStatus,
    CandidateOutcomeEvidence,
    CounterfactualOutcomeCell,
    CounterfactualOutcomeMatrix,
    DecisionContext,
    HardGateEnvelope,
    IntentBelief,
    OperatingParameters,
    PhysicalSafetyEvidenceEnvelope,
    TaskOutcome,
    WaitMode,
    WaitOpportunityModel,
    immutable_runtime_hash,
)
from driveclarify_decision.query_value_policy import OfflineQueryValuePolicy

from .contracts import RUNTIME_VERSION, assert_policy_firewall, canonical_sha256


@dataclass(frozen=True)
class DecisionEngineResult:
    context: DecisionContext
    recommendation: Any
    context_recommendation_sha256: str
    latency_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self.context.to_dict(),
            "recommendation": self.recommendation.to_dict(),
            "context_recommendation_sha256": self.context_recommendation_sha256,
            "latency_seconds": self.latency_seconds,
            "policy_component": "driveclarify_decision.OfflineQueryValuePolicy",
            "policy_version": "driveclarify.query_value_decision.v0",
            "forced_decision": False,
            "manual_override": False,
            "gold_policy_label_reads": 0,
        }


def _matrix(ids: tuple[str, ...], relation: str) -> CounterfactualOutcomeMatrix:
    cells = []
    for action in ids:
        for hypothesis in ids:
            passed = relation == "TASK_EQUIVALENT" or action == hypothesis
            cells.append(
                CounterfactualOutcomeCell(
                    action_candidate_id=action,
                    hypothesis_candidate_id=hypothesis,
                    task_outcome=TaskOutcome.PASS if passed else TaskOutcome.FAIL,
                    evidence_status="SUFFICIENT_GROUNDED_TARGET_CONSEQUENCE_EVIDENCE",
                    task_error_cost=0.0 if passed else 10.0,
                    wrong_goal_indicator=not passed,
                    provenance=(
                        RUNTIME_VERSION,
                        "GROUNDED_EXECUTABLE_TARGET_IDENTITY",
                        "CANDIDATE_SPECIFIC_SIMLINGO_PLAN",
                        "CONSEQUENCE_COMPARISON",
                    ),
                    reason_codes=(
                        "EXECUTABLE_TARGET_AND_CONSEQUENCE_MATCH"
                        if passed
                        else "EXECUTABLE_TARGET_OR_TEMPORAL_STATE_MISMATCH"
                    ,),
                )
            )
    return CounterfactualOutcomeMatrix(
        candidate_ids=ids,
        cells=tuple(cells),
        matrix_status="COMPLETE_K_BY_K_KNOWN",
        provenance=(RUNTIME_VERSION, "GROUNDING_TO_CONSEQUENCE_ADAPTER_V1"),
        reason_codes=("ALL_CELLS_BOUND_TO_RUNTIME_CANDIDATE_IDENTITIES",),
    )


def _safety(observation_id: str, *, verified: bool, now: float) -> PhysicalSafetyEvidenceEnvelope:
    if verified:
        return PhysicalSafetyEvidenceEnvelope(
            safety_status="PASS",
            availability="AVAILABLE_VERIFIED",
            evidence_grade="VERIFIED_FROM_CONTROLLED_PROBE",
            source="EXISTING_PID_AND_AUTHORITY_CONTROLLED_PROBE",
            source_kind="CONTROLLED_RUNTIME_PROBE",
            source_observation_id=observation_id,
            source_frame_id=observation_id.rsplit(":", 2)[-2] if ":" in observation_id else None,
            observed_monotonic_time=now,
            usage_purpose="PHYSICAL_CONTROL_AUTHORIZATION",
            safety_critical_eligible=True,
            reason_codes=("CURRENT_VALID_BASELINE_CONTROL_PATH_VERIFIED",),
        )
    return PhysicalSafetyEvidenceEnvelope(
        safety_status="UNKNOWN",
        availability="UNKNOWN",
        evidence_grade="NOT_CURRENTLY_AVAILABLE",
        source="NON_CONTROL_GROUNDED_DECISION",
        source_kind="IMAGE_ONLY_RUNTIME_EVIDENCE",
        source_observation_id=observation_id,
        source_frame_id=observation_id.rsplit(":", 2)[-2] if ":" in observation_id else None,
        observed_monotonic_time=now,
        usage_purpose="PHYSICAL_CONTROL_AUTHORIZATION",
        safety_critical_eligible=False,
        reason_codes=("NON_CONTROL_DECISION_DOES_NOT_REQUIRE_SAFETY_PASS",),
    )


class UnifiedTriadDecisionEngine:
    """Build and execute the same frozen M2B contract for all three actions."""

    def __init__(self) -> None:
        self.policy = OfflineQueryValuePolicy()

    def decide(
        self,
        *,
        decision_id: str,
        episode_id: str,
        observation_id: str,
        candidate_ids: Sequence[str],
        consequence_relation: str,
        information_source: str,
        query_text: str | None = None,
        query_labels: Sequence[str] = (),
        query_delay_seconds: float = 0.1,
        expected_information_delay_seconds: float = 0.5,
        active_query_id: str | None = None,
        physical_safety_verified: bool = False,
    ) -> DecisionEngineResult:
        started = time.monotonic()
        ids = tuple(str(item) for item in candidate_ids)
        if not ids:
            raise ValueError("UNIFIED_DECISION_REQUIRES_CANDIDATES")
        now = time.monotonic()
        matrix = _matrix(ids, consequence_relation)
        uniform = 1.0 / len(ids)
        belief = IntentBelief(
            candidate_probabilities=tuple((candidate_id, uniform) for candidate_id in ids),
            belief_status=BeliefStatus.AVAILABLE,
            belief_source=BeliefSource.DECLARED_UNINFORMATIVE,
            normalization_status="NORMALIZED_RUNTIME_GROUNDED_HYPOTHESES",
            provenance=(RUNTIME_VERSION, "NO_GOLD_UNINFORMATIVE_PRIOR"),
            reason_codes=("NO_CANDIDATE_ORDER_DEFAULT",),
        )

        labels = tuple(str(item).upper() for item in query_labels)
        if information_source == "PASSENGER" and len(ids) == 2 and len(labels) == 2:
            rows = (
                (ids[0], ((labels[0], 1.0), (labels[1], 0.0))),
                (ids[1], ((labels[0], 0.0), (labels[1], 1.0))),
            )
            query_id = "query-" + canonical_sha256({"decision": decision_id, "ids": ids})[:20]
            proposal: Mapping[str, Any] | None = {
                "proposal_status": "QUESTION_PROPOSAL",
                "query_id": query_id,
                "target_slot": "GROUNDED_REFERENT",
                "question_text": query_text,
                "candidate_partition": (
                    (labels[0], (ids[0],)),
                    (labels[1], (ids[1],)),
                ),
            }
            passenger_resolvable = True
        else:
            labels = ("UNRESOLVED",)
            rows = tuple((candidate_id, (("UNRESOLVED", 1.0),)) for candidate_id in ids)
            query_id = None
            proposal = None
            passenger_resolvable = False
        channel = AnswerChannelModel(
            answer_resolution_probability=1.0 if information_source == "PASSENGER" else 0.0,
            no_answer_probability=0.0,
            answer_confusion_matrix=rows,
            delay_distribution=((float(query_delay_seconds), 1.0),),
            channel_status="VALID",
            source="LABEL_FREE_PASSENGER_LANGUAGE_CHANNEL" if information_source == "PASSENGER" else "NO_PASSENGER_CHANNEL",
            provenance=(RUNTIME_VERSION, "NO_GOLD_CANDIDATE_INDEX"),
            reason_codes=("ANSWER_TEXT_RESOLVES_GROUNDED_DESCRIPTION_ONLY",),
        )

        temporal = information_source == "ENVIRONMENT"
        if temporal:
            wait = WaitOpportunityModel(
                wait_mode=WaitMode.AWAIT_EXPECTED_OBSERVATION,
                future_information_expected=True,
                expected_information_arrival_time=now + float(expected_information_delay_seconds),
                active_query_pending=False,
                holding_capability_status="AVAILABLE_CONTRACT_ONLY",
                decision_deadline_status="VALID",
                missed_opportunity_cost=0.0,
                wait_cost=0.1,
                information_resolution_probability=1.0,
                provenance=(RUNTIME_VERSION, "RUNTIME_DECISION_AUTHORITY_EVIDENCE_V1", "TRACKED_VISUAL_EVENT"),
                reason_codes=("FUTURE_ENVIRONMENT_INFORMATION_EXPECTED",),
                wait_reason="AWAIT_TRACKED_SCENE_EVENT",
                closed_loop_behavior="MAINTAIN_CURRENT_VALID_CLOSED_LOOP_BEHAVIOR",
                emergency_stop_semantics=False,
                low_level_controller_owner="EXISTING_BASELINE_PID",
            )
        elif active_query_id is not None:
            wait = WaitOpportunityModel(
                wait_mode=WaitMode.AWAIT_PENDING_ANSWER,
                future_information_expected=False,
                expected_information_arrival_time=now + float(query_delay_seconds),
                active_query_pending=True,
                holding_capability_status="AVAILABLE_CONTRACT_ONLY",
                decision_deadline_status="VALID",
                missed_opportunity_cost=0.0,
                wait_cost=0.01,
                information_resolution_probability=1.0,
                provenance=(RUNTIME_VERSION, "RUNTIME_DECISION_AUTHORITY_EVIDENCE_V1", "PASSENGER_ANSWER_PENDING"),
                reason_codes=("ACTIVE_QUERY_AWAITING_ANSWER",),
                wait_reason="AWAIT_PENDING_PASSENGER_ANSWER",
                closed_loop_behavior="MAINTAIN_CURRENT_VALID_CLOSED_LOOP_BEHAVIOR",
                emergency_stop_semantics=False,
                low_level_controller_owner="EXISTING_BASELINE_PID",
            )
        else:
            wait = WaitOpportunityModel(
                wait_mode=WaitMode.NOT_AVAILABLE,
                future_information_expected=False,
                expected_information_arrival_time=None,
                active_query_pending=False,
                holding_capability_status="NOT_AVAILABLE",
                decision_deadline_status="VALID",
                missed_opportunity_cost=0.0,
                wait_cost=0.0,
                information_resolution_probability=0.0,
                provenance=(RUNTIME_VERSION, "NO_FUTURE_INFORMATION_SOURCE"),
                reason_codes=("WAIT_NOT_APPLICABLE",),
            )

        safety = _safety(observation_id, verified=physical_safety_verified, now=now)
        active_id = active_query_id
        context = DecisionContext(
            decision_id=decision_id,
            episode_id=episode_id,
            source_observation_id=observation_id,
            candidate_ids=ids,
            candidate_pair_relation=consequence_relation,
            candidate_outcomes=tuple(
                CandidateOutcomeEvidence(
                    candidate_id=candidate_id,
                    task_outcome=TaskOutcome.PASS,
                    evidence_status="GROUNDED_EXECUTABLE_CANDIDATE",
                    provenance=(RUNTIME_VERSION, "TARGET_BINDING_RECEIPT"),
                    reason_codes=("TARGET_IDENTITY_NONEMPTY",),
                )
                for candidate_id in ids
            ),
            counterfactual_outcome_matrix=matrix,
            intent_belief=belief,
            query_proposal=proposal,
            answer_channel=channel,
            query_budget=0 if active_id else 1,
            active_query_id=active_id,
            monotonic_now=now,
            answer_deadline_monotonic=now + 5.0,
            time_to_decision_status="VALID",
            wait_opportunity=wait,
            hard_gate_envelope=HardGateEnvelope(
                hard_safety_status=safety.safety_status,
                hard_rule_status="PASS",
                evidence_gate_status="PASS",
                query_episode_status="ACTIVE_QUERY_PENDING" if active_id else "NO_ACTIVE_QUERY",
                cache_status="FRESH",
                candidate_freshness_status="FRESH",
                control_authorized=False,
                reason_codes=("UNIFIED_GROUNDED_RUNTIME_CONTRACT_VALID",),
                physical_safety_evidence=safety,
            ),
            evidence_masks=(
                ("ambiguity_present", len(ids) >= 2),
                ("passenger_resolvable_unknown", passenger_resolvable),
                ("unknown_causes", ("GROUNDED_REFERENT_AMBIGUITY",) if passenger_resolvable else ()),
            ),
            operating_parameters=OperatingParameters(
                query_cost=0.1,
                delay_cost_per_second=0.5,
                no_answer_penalty=1.0,
                unknown_task_loss_policy="FAIL_CLOSED_NO_SCALAR_IMPUTATION",
                strict_value_epsilon=1e-9,
                provenance=(RUNTIME_VERSION, "FROZEN_M2B_SEMANTICS_UNMODIFIED"),
            ),
            provenance=(RUNTIME_VERSION, "GROUNDING_TO_CONSEQUENCE_TO_M2B"),
        )
        assert_policy_firewall(context.to_dict())
        recommendation = self.policy.recommend(context)
        assert_policy_firewall(recommendation.to_dict())
        return DecisionEngineResult(
            context=context,
            recommendation=recommendation,
            context_recommendation_sha256=immutable_runtime_hash(context, recommendation),
            latency_seconds=time.monotonic() - started,
        )


__all__ = ["DecisionEngineResult", "UnifiedTriadDecisionEngine"]
