"""Deterministic structured-language interaction components for Method M2A v0."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping, Sequence

from .interaction_contracts import (
    AmbiguityEpisode,
    AmbiguityType,
    AnswerResolution,
    AnswerStatus,
    BindingStatus,
    CandidateInterpretation,
    CandidateSpecificTaskBinding,
    CandidateStatus,
    ClarificationQuestionProposal,
    EVIDENCE_DESIGNATION,
    EpisodeState,
    GroundingEvidence,
    GroundingStatus,
    LongitudinalTaskTarget,
    LongitudinalTaskTargetType,
    PromptAdapterInput,
    QuestionStatus,
    ResolvedInstruction,
    RuntimeEpisodeInput,
    StructuredAmbiguityParse,
    SymbolicSceneEntity,
    CanonicalIntent,
    assert_no_forbidden_runtime_keys,
    stable_sha256,
)
from .semantic_plan_bridge import CandidateSpecificSemanticPlanBridge, plan_record_for_candidate


_SUPPORTED_TASK_FAMILIES = frozenset({"REFERENCE_GOAL", "DESTINATION_GOAL", "MANEUVER_BRANCH"})
_SUPPORTED_TARGET_TYPES = frozenset(
    {
        "SCENARIO_ACTOR_ID",
        "LANDMARK_ID",
        "ROUTE_BRANCH_ID",
        "ORDERED_TRIGGER_ID",
        "SYMBOLIC_PULL_OVER_ZONE",
        "REFERENCE_ENTITY_ID",
    }
)
_SUPPORTED_BINDING_SOURCES = frozenset(
    {
        "HAND_AUTHORED_ORACLE_GROUNDING",
        "SYMBOLIC_SCENE_TABLE",
        "FIXTURE_DECLARED_ENTITY",
        "ROUTE_BRANCH_SYMBOL",
    }
)
_SLOT_ENTITY_TYPES = {
    "reference_entity": frozenset({"REFERENCE_ENTITY", "VEHICLE", "SCENARIO_ACTOR"}),
    "landmark": frozenset({"LANDMARK"}),
    "ordered_trigger": frozenset({"ORDERED_TRIGGER", "INTERSECTION", "ROUTE_BRANCH"}),
    "pull_over_zone": frozenset({"PULL_OVER_ZONE"}),
}


def _normalize(text: str) -> str:
    lowered = text.casefold().replace("’", "'")
    return " ".join(re.findall(r"[\w'-]+", lowered, flags=re.UNICODE))


def _maneuver(text: str) -> str | None:
    if "pull over" in text:
        return "PULL_OVER"
    if re.search(r"\bstop\b", text):
        return "STOP"
    if re.search(r"\bturn\b", text):
        return "TURN"
    if re.search(r"\b(continue|go straight|proceed)\b", text):
        return "CONTINUE"
    return None


def _temporal_relation(text: str) -> str | None:
    for token, value in (("after", "AFTER"), ("before", "BEFORE"), ("past", "AFTER")):
        if re.search(rf"\b{token}\b", text):
            return value
    return None


def _spatial_relation(text: str) -> str | None:
    for token, value in (("near", "NEAR"), ("beside", "BESIDE"), ("by", "NEAR"), ("at", "AT")):
        if re.search(rf"\b{token}\b", text):
            return value
    return None


class StructuredAmbiguityParser:
    """Rule/template parser whose inputs contain only runtime-observable values."""

    def parse(self, runtime_input: RuntimeEpisodeInput) -> StructuredAmbiguityParse:
        assert_no_forbidden_runtime_keys(runtime_input.to_dict())
        normalized = _normalize(runtime_input.raw_instruction)
        maneuver = _maneuver(normalized)
        explicit_ids = tuple(
            sorted(
                entity.entity_id
                for entity in runtime_input.symbolic_scene_entities
                if entity.entity_id and _normalize(entity.entity_id) in normalized
            )
        )
        unresolved: list[str] = []
        temporal = _temporal_relation(normalized)
        spatial = _spatial_relation(normalized)

        has_reference_noun = bool(
            re.search(r"\b(car|vehicle|truck|van|pedestrian|cyclist|it|that one)\b", normalized)
        )
        has_landmark_noun = bool(
            re.search(r"\b(shop|store|cafe|school|hospital|station|building|landmark)\b", normalized)
        )
        has_order_cue = bool(
            re.search(r"\b(intersection|junction|turning|exit|branch)\b", normalized)
            and re.search(r"\b(ahead|next|upcoming|first|second)\b", normalized)
        )
        if not explicit_ids:
            if temporal is not None and has_reference_noun:
                unresolved.append("reference_entity")
            if spatial is not None and has_landmark_noun:
                unresolved.append("landmark")
            if has_order_cue:
                unresolved.append("ordered_trigger")
            if maneuver == "PULL_OVER":
                unresolved.append("pull_over_zone")

        unresolved = list(dict.fromkeys(unresolved))
        if maneuver is None:
            ambiguity = AmbiguityType.UNSUPPORTED
            parser_status = "UNSUPPORTED"
            reasons = ("SUPPORTED_MANEUVER_NOT_RECOGNIZED",)
        elif "reference_entity" in unresolved:
            ambiguity = AmbiguityType.REFERENTIAL
            parser_status = "PARSED"
            reasons = ("REFERENTIAL_SLOT_UNRESOLVED",)
        elif "landmark" in unresolved:
            ambiguity = AmbiguityType.LANDMARK
            parser_status = "PARSED"
            reasons = ("LANDMARK_SLOT_UNRESOLVED",)
        elif "ordered_trigger" in unresolved:
            ambiguity = AmbiguityType.ORDER
            parser_status = "PARSED"
            reasons = ("ORDERED_TRIGGER_SLOT_UNRESOLVED",)
        elif "pull_over_zone" in unresolved:
            ambiguity = AmbiguityType.UNDERSPECIFIED_CONSTRAINT
            parser_status = "PARSED"
            reasons = ("PULL_OVER_ZONE_UNRESOLVED",)
        else:
            ambiguity = AmbiguityType.UNAMBIGUOUS
            parser_status = "PARSED"
            reasons = ("NO_SUPPORTED_AMBIGUITY_DETECTED",)

        intent = CanonicalIntent(
            intent_type=ambiguity.value,
            maneuver=maneuver,
            spatial_relation=spatial,
            temporal_relation=temporal,
            reference_slot=explicit_ids[0] if explicit_ids and temporal else None,
            landmark_slot=explicit_ids[0] if explicit_ids and has_landmark_noun else None,
            order_slot=explicit_ids[0] if explicit_ids and has_order_cue else None,
            constraint_slots=(
                (("pull_over_zone", explicit_ids[0]),)
                if explicit_ids and maneuver == "PULL_OVER"
                else ()
            ),
            canonical_tokens=tuple(normalized.split()),
        )
        return StructuredAmbiguityParse(
            source_instruction_id=runtime_input.instruction_id,
            normalized_instruction=normalized,
            ambiguity_type=ambiguity,
            unresolved_slots=tuple(unresolved),
            base_intent=intent,
            explicit_entity_ids=explicit_ids,
            parser_status=parser_status,
            provenance=("DETERMINISTIC_TEMPLATE_PARSER", *runtime_input.provenance),
            reason_codes=reasons,
        )


def _slot_value(intent: CanonicalIntent, slot: str) -> str | None:
    if slot == "reference_entity":
        return intent.reference_slot
    if slot == "landmark":
        return intent.landmark_slot
    if slot == "ordered_trigger":
        return intent.order_slot
    return dict(intent.constraint_slots).get(slot)


def _fill_slot(intent: CanonicalIntent, slot: str, target: str | None) -> CanonicalIntent:
    tokens = tuple((*intent.canonical_tokens, f"{slot}={target if target is not None else 'MISSING'}"))
    if slot == "reference_entity":
        return replace(intent, reference_slot=target, canonical_tokens=tokens)
    if slot == "landmark":
        return replace(intent, landmark_slot=target, canonical_tokens=tokens)
    if slot == "ordered_trigger":
        return replace(intent, order_slot=target, canonical_tokens=tokens)
    constraints = dict(intent.constraint_slots)
    constraints[slot] = target
    return replace(intent, constraint_slots=tuple(sorted(constraints.items())), canonical_tokens=tokens)


def _task_family(slot: str) -> str:
    if slot == "reference_entity":
        return "REFERENCE_GOAL"
    if slot in {"ordered_trigger"}:
        return "MANEUVER_BRANCH"
    return "DESTINATION_GOAL"


def _longitudinal_task_target(
    intent: CanonicalIntent,
    source_instruction_id: str,
    parser_provenance: tuple[str, ...],
) -> LongitudinalTaskTarget | None:
    """Bind only explicit, parser-backed longitudinal maneuvers as language targets."""

    try:
        target_type = LongitudinalTaskTargetType(intent.maneuver)
    except (TypeError, ValueError):
        return None
    return LongitudinalTaskTarget(
        target_type=target_type,
        source_maneuver=target_type.value,
        source_instruction_id=source_instruction_id,
        binding_source="CANONICAL_INTENT_MANEUVER",
        provenance=("CANONICAL_INTENT_MANEUVER", *parser_provenance),
    )


def _description(intent: CanonicalIntent, entity: SymbolicSceneEntity) -> str | None:
    name = entity.display_name.strip() if isinstance(entity.display_name, str) else None
    if not name:
        return None
    if intent.temporal_relation == "AFTER":
        return f"after {name}"
    if intent.temporal_relation == "BEFORE":
        return f"before {name}"
    if intent.spatial_relation in {"NEAR", "BESIDE"}:
        return f"near {name}"
    if intent.maneuver == "PULL_OVER":
        return f"in {name}"
    return f"at {name}"


def _render_instruction(intent: CanonicalIntent, entity: SymbolicSceneEntity) -> str | None:
    name = entity.display_name.strip() if isinstance(entity.display_name, str) else None
    if not name:
        return None
    if intent.maneuver == "PULL_OVER":
        return f"Pull over in {name}."
    verb = "Stop" if intent.maneuver == "STOP" else "Turn" if intent.maneuver == "TURN" else "Continue"
    if intent.temporal_relation == "AFTER":
        return f"{verb} after {name}."
    if intent.temporal_relation == "BEFORE":
        return f"{verb} before {name}."
    if intent.spatial_relation in {"NEAR", "BESIDE"}:
        return f"{verb} near {name}."
    return f"{verb} at {name}."


class CandidateSpecificGrounder:
    """Convert one observable scene entity into explicit symbolic grounding evidence."""

    def ground(self, entity: SymbolicSceneEntity) -> GroundingEvidence:
        reasons = entity.reason_codes or (
            ("SYMBOLIC_ENTITY_GROUNDED",)
            if entity.grounding_status
            in {GroundingStatus.GROUNDED_SYMBOLIC, GroundingStatus.GROUNDED_ORACLE_FIXTURE}
            else ("SYMBOLIC_ENTITY_NOT_GROUNDED",)
        )
        return GroundingEvidence(
            grounding_status=entity.grounding_status,
            entity_type=entity.entity_type,
            entity_id=entity.entity_id,
            display_name=entity.display_name,
            observable_attributes=entity.observable_attributes,
            source=entity.binding_source,
            confidence_status=(
                "DETERMINISTIC_SYMBOLIC" if entity.entity_id is not None else "NOT_AVAILABLE"
            ),
            provenance=entity.provenance,
            reason_codes=reasons,
        )


class CandidateInterpretationGenerator:
    """Generate at most the fixed v0 K=2 candidates without consulting plans or labels."""

    k = 2

    def generate(
        self,
        parsed: StructuredAmbiguityParse,
        runtime_input: RuntimeEpisodeInput,
    ) -> tuple[CandidateInterpretation, ...]:
        assert_no_forbidden_runtime_keys(runtime_input.to_dict())
        if parsed.ambiguity_type is AmbiguityType.UNSUPPORTED:
            return ()
        if len(parsed.unresolved_slots) > 1:
            return ()
        if parsed.unresolved_slots:
            slot = parsed.unresolved_slots[0]
            allowed_types = _SLOT_ENTITY_TYPES.get(slot, frozenset())
            entities = [
                entity
                for entity in runtime_input.symbolic_scene_entities
                if slot in entity.supports_slots and entity.entity_type in allowed_types
            ]
        else:
            slot = self._explicit_slot(parsed)
            explicit = set(parsed.explicit_entity_ids)
            entities = [
                entity for entity in runtime_input.symbolic_scene_entities if entity.entity_id in explicit
            ]
        entities.sort(
            key=lambda entity: (
                entity.ordinal is None,
                entity.ordinal if entity.ordinal is not None else 10**9,
                _normalize(entity.display_name or ""),
                entity.entity_id or "",
            )
        )
        return tuple(
            self._candidate(parsed, runtime_input, slot, entity, rank)
            for rank, entity in enumerate(entities[: self.k], start=1)
        )

    @staticmethod
    def _explicit_slot(parsed: StructuredAmbiguityParse) -> str:
        intent = parsed.base_intent
        if intent.reference_slot:
            return "reference_entity"
        if intent.landmark_slot:
            return "landmark"
        if intent.order_slot:
            return "ordered_trigger"
        if dict(intent.constraint_slots).get("pull_over_zone"):
            return "pull_over_zone"
        return "reference_entity"

    @staticmethod
    def _candidate(
        parsed: StructuredAmbiguityParse,
        runtime_input: RuntimeEpisodeInput,
        slot: str,
        entity: SymbolicSceneEntity,
        rank: int,
    ) -> CandidateInterpretation:
        target = entity.entity_id
        intent = _fill_slot(parsed.base_intent, slot, target)
        grounding = CandidateSpecificGrounder().ground(entity)
        if entity.grounding_status is GroundingStatus.CONFLICTING:
            binding_status = BindingStatus.CONFLICTING
            candidate_status = CandidateStatus.CONTRADICTORY
        elif (
            target is None
            or entity.grounding_status
            not in {GroundingStatus.GROUNDED_SYMBOLIC, GroundingStatus.GROUNDED_ORACLE_FIXTURE}
        ):
            binding_status = BindingStatus.NOT_AVAILABLE
            candidate_status = CandidateStatus.UNGROUNDED
        elif entity.symbolic_target_type not in _SUPPORTED_TARGET_TYPES:
            binding_status = BindingStatus.UNSUPPORTED
            candidate_status = CandidateStatus.UNSUPPORTED
        else:
            binding_status = BindingStatus.BOUND
            candidate_status = CandidateStatus.VALID
        semantic_payload = {
            "canonical_intent": intent.to_dict(),
            "grounding_identity": {"entity_type": entity.entity_type, "entity_id": target},
        }
        semantic_sha = stable_sha256(semantic_payload)
        candidate_id = "cand-" + stable_sha256(
            {
                "instruction_id": runtime_input.instruction_id,
                "rank": rank,
                "semantic_sha256": semantic_sha,
            }
        )[:16]
        binding = CandidateSpecificTaskBinding(
            source_candidate_id=candidate_id,
            task_family=_task_family(slot),
            symbolic_target_type=entity.symbolic_target_type,
            symbolic_target_id=target,
            required_slots=(slot,),
            binding_status=binding_status,
            binding_source=entity.binding_source,
            frame_or_semantic_domain=entity.frame_or_semantic_domain,
            provenance=entity.provenance,
            reason_codes=(f"CANDIDATE_SPECIFIC_BINDING_{binding_status.value}",),
            longitudinal_task_target=_longitudinal_task_target(
                intent,
                parsed.source_instruction_id,
                parsed.provenance,
            ),
        )
        return CandidateInterpretation(
            candidate_id=candidate_id,
            source_instruction_id=runtime_input.instruction_id,
            canonical_intent=intent,
            candidate_specific_task_binding=binding,
            grounding_evidence=grounding,
            human_readable_description=_description(intent, entity),
            prompt_adapter_input=None,
            candidate_status=candidate_status,
            provenance=("DETERMINISTIC_K2_CANDIDATE_GENERATOR", *entity.provenance),
            reason_codes=(f"CANDIDATE_{candidate_status.value}",),
            canonical_semantic_sha256=semantic_sha,
        )


class CandidateValidityDistinctnessGate:
    """Fail closed on invalid candidates and canonical-semantic duplicates."""

    def apply(
        self,
        parsed: StructuredAmbiguityParse,
        candidates: Sequence[CandidateInterpretation],
    ) -> tuple[CandidateInterpretation, ...]:
        seen: set[str] = set()
        result: list[CandidateInterpretation] = []
        for candidate in candidates:
            status = candidate.candidate_status
            reasons = list(candidate.reason_codes)
            binding = candidate.candidate_specific_task_binding
            if candidate.source_instruction_id != parsed.source_instruction_id:
                status = CandidateStatus.CONTRADICTORY
                reasons.append("SOURCE_INSTRUCTION_MISMATCH")
            if binding.task_family not in _SUPPORTED_TASK_FAMILIES:
                status = CandidateStatus.UNSUPPORTED
                reasons.append("TASK_FAMILY_UNSUPPORTED")
            if binding.symbolic_target_type not in _SUPPORTED_TARGET_TYPES:
                status = CandidateStatus.UNSUPPORTED
                reasons.append("SYMBOLIC_TARGET_TYPE_UNSUPPORTED")
            if binding.binding_source not in _SUPPORTED_BINDING_SOURCES:
                status = CandidateStatus.UNSUPPORTED
                reasons.append("BINDING_SOURCE_UNSUPPORTED")
            if any(_slot_value(candidate.canonical_intent, slot) is None for slot in binding.required_slots):
                if status is CandidateStatus.VALID:
                    status = CandidateStatus.INCOMPLETE
                reasons.append("REQUIRED_CANONICAL_SLOT_MISSING")
            if binding.binding_status is not BindingStatus.BOUND and status is CandidateStatus.VALID:
                status = CandidateStatus.UNGROUNDED
                reasons.append("TASK_BINDING_NOT_AVAILABLE")
            if status is CandidateStatus.VALID and candidate.canonical_semantic_sha256 in seen:
                status = CandidateStatus.DUPLICATE
                reasons.append("CANONICAL_SEMANTICS_AND_GROUNDING_DUPLICATE")
            if status is CandidateStatus.VALID:
                seen.add(candidate.canonical_semantic_sha256)
            result.append(replace(candidate, candidate_status=status, reason_codes=tuple(sorted(set(reasons)))))
        return tuple(result)


class PromptAdapter:
    adapter_version = "driveclarify.prompt_adapter.v0"

    def adapt(self, candidate: CandidateInterpretation) -> CandidateInterpretation:
        binding = candidate.candidate_specific_task_binding
        rendered = candidate.human_readable_description
        if rendered is None or candidate.candidate_status is not CandidateStatus.VALID:
            return replace(
                candidate,
                reason_codes=tuple(sorted(set((*candidate.reason_codes, "PROMPT_ADAPTER_NOT_APPLICABLE")))),
            )
        maneuver = candidate.canonical_intent.maneuver
        entity = candidate.grounding_evidence
        fake_entity = SymbolicSceneEntity(
            entity_type=entity.entity_type,
            entity_id=entity.entity_id,
            display_name=entity.display_name,
            observable_attributes=entity.observable_attributes,
            ordinal=None,
            supports_slots=binding.required_slots,
            symbolic_target_type=binding.symbolic_target_type,
            binding_source=binding.binding_source,
            frame_or_semantic_domain=binding.frame_or_semantic_domain,
            grounding_status=entity.grounding_status,
            provenance=entity.provenance,
        )
        instruction = _render_instruction(candidate.canonical_intent, fake_entity)
        if instruction is None:
            return replace(candidate, candidate_status=CandidateStatus.INCOMPLETE)
        hlc = {
            "TURN": "HLC_TURN",
            "STOP": "HLC_STOP",
            "PULL_OVER": "HLC_PULL_OVER",
            "CONTINUE": "HLC_CONTINUE",
        }.get(maneuver)
        adapter = PromptAdapterInput(
            candidate_id=candidate.candidate_id,
            backbone_instruction_text=instruction,
            optional_hlc_token=hlc,
            adapter_version=self.adapter_version,
            source_instruction_id=candidate.source_instruction_id,
            candidate_semantic_sha256=candidate.canonical_semantic_sha256,
            task_binding_sha256=stable_sha256(binding.to_dict()),
        )
        return replace(candidate, prompt_adapter_input=adapter)


class ClarificationQuestionPlanner:
    """Build one discriminative binary-choice proposal; never emit a live ASK action."""

    def propose(
        self,
        episode: AmbiguityEpisode,
        current_monotonic: float,
    ) -> ClarificationQuestionProposal:
        valid = tuple(
            candidate for candidate in episode.candidate_set if candidate.candidate_status is CandidateStatus.VALID
        )
        if not episode.unresolved_slots and len(valid) == 1:
            return self._not_realizable("QUESTION_NOT_REQUIRED_FOR_UNAMBIGUOUS", QuestionStatus.QUESTION_NOT_REQUIRED)
        if len(valid) != 2:
            return self._not_realizable("BINARY_QUESTION_REQUIRES_TWO_VALID_CANDIDATES")
        differing = [
            slot
            for slot in episode.unresolved_slots
            if _slot_value(valid[0].canonical_intent, slot) != _slot_value(valid[1].canonical_intent, slot)
        ]
        if len(differing) != 1:
            return self._not_realizable("SINGLE_DISCRIMINATIVE_SLOT_NOT_AVAILABLE")
        descriptions = tuple(candidate.human_readable_description for candidate in valid)
        if any(not item for item in descriptions) or len(set(descriptions)) != 2:
            return self._not_realizable("HUMAN_DISTINGUISHABLE_OPTION_TEXT_NOT_AVAILABLE")
        target_slot = differing[0]
        deadline_status = (
            "NOT_SET"
            if episode.answer_deadline_monotonic is None
            else "EXPIRED"
            if current_monotonic > episode.answer_deadline_monotonic
            else "ACTIVE"
        )
        if deadline_status == "EXPIRED":
            return self._not_realizable("QUESTION_DEADLINE_ALREADY_EXPIRED")
        left, right = descriptions
        if target_slot == "reference_entity":
            question = f"Do you mean {left} or {right}?"
        elif target_slot == "landmark":
            question = f"Do you mean {left} or {right}?"
        elif target_slot == "ordered_trigger":
            question = f"Do you mean {left} or {right}?"
        elif target_slot == "pull_over_zone":
            question = f"Do you mean {left} or {right}?"
        else:
            return self._not_realizable("QUESTION_SLOT_TEMPLATE_UNSUPPORTED")
        ids = tuple(candidate.candidate_id for candidate in valid)
        query_id = "query-" + stable_sha256(
            {"episode_id": episode.episode_id, "target_slot": target_slot, "candidate_ids": ids}
        )[:16]
        return ClarificationQuestionProposal(
            query_id=query_id,
            target_slot=target_slot,
            question_type="BINARY_CHOICE",
            candidate_partition=(("OPTION_ONE", (ids[0],)), ("OPTION_TWO", (ids[1],))),
            option_descriptions=(str(left), str(right)),
            question_text=question,
            source_candidate_ids=ids,
            deadline_status=deadline_status,
            proposal_status=QuestionStatus.QUESTION_PROPOSAL,
            provenance=("DETERMINISTIC_BINARY_SLOT_QUESTION", *EVIDENCE_DESIGNATION),
            reason_codes=("CANDIDATE_DIFFERENTIATING_SLOT_REALIZED",),
        )

    @staticmethod
    def _not_realizable(
        reason: str,
        status: QuestionStatus = QuestionStatus.QUESTION_NOT_REALIZABLE,
    ) -> ClarificationQuestionProposal:
        return ClarificationQuestionProposal(
            query_id=None,
            target_slot=None,
            question_type=None,
            candidate_partition=(),
            option_descriptions=(),
            question_text=None,
            source_candidate_ids=(),
            deadline_status="NOT_APPLICABLE",
            proposal_status=status,
            provenance=("DETERMINISTIC_BINARY_SLOT_QUESTION", *EVIDENCE_DESIGNATION),
            reason_codes=(reason,),
        )


class AnswerResolver:
    """Resolve only explicit option references; never default to the first candidate."""

    def resolve(
        self,
        question: ClarificationQuestionProposal,
        passenger_answer_text: str | None,
        current_episode_state: EpisodeState,
        monotonic_timestamp: float,
        answer_deadline_monotonic: float | None,
    ) -> AnswerResolution:
        if current_episode_state is not EpisodeState.QUERY_ISSUED:
            return self._result(AnswerStatus.OUT_OF_DOMAIN, question, None, None, monotonic_timestamp, "QUERY_NOT_ACTIVE")
        if answer_deadline_monotonic is not None and monotonic_timestamp > answer_deadline_monotonic:
            return self._result(AnswerStatus.EXPIRED, question, None, passenger_answer_text, monotonic_timestamp, "ANSWER_AFTER_DEADLINE")
        if passenger_answer_text is None or not passenger_answer_text.strip():
            return self._result(AnswerStatus.NO_ANSWER, question, None, passenger_answer_text, monotonic_timestamp, "PASSENGER_ANSWER_NOT_AVAILABLE")
        normalized = _normalize(passenger_answer_text)
        if normalized in {"yes", "yeah", "yep", "whichever", "either", "i do not know", "i don't know", "dont know"}:
            return self._result(AnswerStatus.STILL_AMBIGUOUS, question, None, normalized, monotonic_timestamp, "ANSWER_DOES_NOT_SELECT_OPTION")
        first_words = {"first", "option one", "option 1", "one", "the first one"}
        second_words = {"second", "option two", "option 2", "two", "the second one"}
        first_hit = normalized in first_words or bool(re.search(r"\b(first|option one|option 1)\b", normalized))
        second_hit = normalized in second_words or bool(re.search(r"\b(second|option two|option 2)\b", normalized))
        descriptions = tuple(_normalize(item) for item in question.option_descriptions)
        if len(descriptions) == 2:
            first_hit = first_hit or normalized == descriptions[0]
            second_hit = second_hit or normalized == descriptions[1]
        if "both" in normalized or (first_hit and second_hit):
            return self._result(AnswerStatus.CONTRADICTORY, question, None, normalized, monotonic_timestamp, "MUTUALLY_EXCLUSIVE_OPTIONS_BOTH_SELECTED")
        if first_hit and len(question.source_candidate_ids) == 2:
            return self._result(AnswerStatus.RESOLVED, question, question.source_candidate_ids[0], normalized, monotonic_timestamp, "OPTION_ONE_SELECTED")
        if second_hit and len(question.source_candidate_ids) == 2:
            return self._result(AnswerStatus.RESOLVED, question, question.source_candidate_ids[1], normalized, monotonic_timestamp, "OPTION_TWO_SELECTED")
        return self._result(AnswerStatus.OUT_OF_DOMAIN, question, None, normalized, monotonic_timestamp, "ANSWER_DOES_NOT_MATCH_AVAILABLE_OPTIONS")

    @staticmethod
    def _result(
        status: AnswerStatus,
        question: ClarificationQuestionProposal,
        selected: str | None,
        normalized: str | None,
        timestamp: float,
        reason: str,
    ) -> AnswerResolution:
        return AnswerResolution(
            status=status,
            query_id=question.query_id,
            selected_candidate_id=selected,
            normalized_answer=normalized,
            received_at_monotonic=timestamp,
            provenance=("DETERMINISTIC_OPTION_ANSWER_RESOLVER", *EVIDENCE_DESIGNATION),
            reason_codes=(reason,),
        )


class InteractionEpisodeReducer:
    """Pure immutable state transitions for one offline language episode."""

    @staticmethod
    def new(runtime_input: RuntimeEpisodeInput) -> AmbiguityEpisode:
        return AmbiguityEpisode(
            episode_id=runtime_input.episode_id,
            instruction_id=runtime_input.instruction_id,
            raw_instruction=runtime_input.raw_instruction,
            source_observation_id=runtime_input.source_observation_id,
            ambiguity_type=AmbiguityType.UNSUPPORTED,
            unresolved_slots=(),
            candidate_set=(),
            active_query_id=None,
            answer_deadline_monotonic=runtime_input.answer_deadline_monotonic,
            episode_state=EpisodeState.NEW,
            provenance=runtime_input.provenance,
            reason_codes=runtime_input.reason_codes,
        )

    @staticmethod
    def parsed(episode: AmbiguityEpisode, parsed: StructuredAmbiguityParse) -> AmbiguityEpisode:
        if episode.episode_state is not EpisodeState.NEW:
            raise ValueError("PARSE_TRANSITION_REQUIRES_NEW")
        state = EpisodeState.UNSUPPORTED if parsed.ambiguity_type is AmbiguityType.UNSUPPORTED else EpisodeState.PARSED
        return replace(
            episode,
            ambiguity_type=parsed.ambiguity_type,
            unresolved_slots=parsed.unresolved_slots,
            episode_state=state,
            reason_codes=tuple((*episode.reason_codes, *parsed.reason_codes)),
        )

    @staticmethod
    def candidates_ready(
        episode: AmbiguityEpisode,
        candidates: Sequence[CandidateInterpretation],
    ) -> AmbiguityEpisode:
        if episode.episode_state is not EpisodeState.PARSED:
            raise ValueError("CANDIDATE_TRANSITION_REQUIRES_PARSED")
        state = EpisodeState.CANDIDATES_READY if candidates else EpisodeState.UNSUPPORTED
        return replace(episode, candidate_set=tuple(candidates), episode_state=state)

    @staticmethod
    def query_proposed(
        episode: AmbiguityEpisode,
        proposal: ClarificationQuestionProposal,
    ) -> AmbiguityEpisode:
        if episode.episode_state is not EpisodeState.CANDIDATES_READY:
            raise ValueError("QUERY_PROPOSAL_REQUIRES_CANDIDATES_READY")
        if proposal.proposal_status is not QuestionStatus.QUESTION_PROPOSAL:
            return replace(episode, episode_state=EpisodeState.UNRESOLVED, reason_codes=tuple((*episode.reason_codes, *proposal.reason_codes)))
        if episode.active_query_id is not None:
            raise ValueError("ACTIVE_QUERY_ALREADY_EXISTS")
        return replace(episode, active_query_id=proposal.query_id, episode_state=EpisodeState.QUERY_PROPOSED)

    @staticmethod
    def query_issued(
        episode: AmbiguityEpisode,
        proposal: ClarificationQuestionProposal,
    ) -> AmbiguityEpisode:
        if episode.episode_state is not EpisodeState.QUERY_PROPOSED:
            raise ValueError("QUERY_ISSUE_REQUIRES_VALID_PROPOSAL")
        if proposal.query_id is None or episode.active_query_id != proposal.query_id:
            raise ValueError("QUERY_ID_MISMATCH")
        return replace(episode, episode_state=EpisodeState.QUERY_ISSUED)

    @staticmethod
    def apply_answer(
        episode: AmbiguityEpisode,
        resolution: AnswerResolution,
    ) -> tuple[AmbiguityEpisode, ResolvedInstruction | None]:
        if episode.episode_state is not EpisodeState.QUERY_ISSUED:
            raise ValueError("ANSWER_REQUIRES_ISSUED_QUERY")
        if resolution.query_id != episode.active_query_id:
            raise ValueError("ANSWER_QUERY_ID_MISMATCH")
        if resolution.status is AnswerStatus.EXPIRED:
            return replace(episode, episode_state=EpisodeState.EXPIRED, active_query_id=None), None
        if resolution.status is not AnswerStatus.RESOLVED:
            return replace(
                episode,
                episode_state=EpisodeState.UNRESOLVED,
                reason_codes=tuple((*episode.reason_codes, *resolution.reason_codes)),
            ), None
        selected = next(
            (item for item in episode.candidate_set if item.candidate_id == resolution.selected_candidate_id),
            None,
        )
        if selected is None or selected.candidate_status is not CandidateStatus.VALID or selected.prompt_adapter_input is None:
            raise ValueError("RESOLUTION_SELECTED_CANDIDATE_INVALID")
        resolved = ResolvedInstruction(
            source_instruction_id=episode.instruction_id,
            resolved_candidate_id=selected.candidate_id,
            explicit_instruction_text=selected.prompt_adapter_input.backbone_instruction_text,
            canonical_intent=selected.canonical_intent,
            candidate_specific_task_binding=selected.candidate_specific_task_binding,
            source_observation_id=episode.source_observation_id,
            requires_latest_observation_replan=True,
            cached_pre_answer_plan_reusable=False,
            output_type="OFFLINE_RESOLUTION_RESULT",
            provenance=("ANSWER_RESOLUTION_RECONSTRUCTION", *EVIDENCE_DESIGNATION),
            reason_codes=("LATEST_OBSERVATION_REPLAN_REQUIRED", "PRE_ANSWER_PLAN_INVALIDATED"),
        )
        return replace(episode, episode_state=EpisodeState.RESOLVED, active_query_id=None), resolved

    @staticmethod
    def resolve_unambiguous(episode: AmbiguityEpisode) -> tuple[AmbiguityEpisode, ResolvedInstruction]:
        valid = [item for item in episode.candidate_set if item.candidate_status is CandidateStatus.VALID]
        if episode.unresolved_slots or len(valid) != 1 or valid[0].prompt_adapter_input is None:
            raise ValueError("UNAMBIGUOUS_RESOLUTION_REQUIRES_ONE_VALID_CANDIDATE")
        selected = valid[0]
        resolved = ResolvedInstruction(
            source_instruction_id=episode.instruction_id,
            resolved_candidate_id=selected.candidate_id,
            explicit_instruction_text=selected.prompt_adapter_input.backbone_instruction_text,
            canonical_intent=selected.canonical_intent,
            candidate_specific_task_binding=selected.candidate_specific_task_binding,
            source_observation_id=episode.source_observation_id,
            requires_latest_observation_replan=True,
            cached_pre_answer_plan_reusable=False,
            output_type="OFFLINE_RESOLUTION_RESULT",
            provenance=("UNAMBIGUOUS_INSTRUCTION_RECONSTRUCTION", *EVIDENCE_DESIGNATION),
            reason_codes=("LATEST_OBSERVATION_REPLAN_REQUIRED", "PRE_ANSWER_PLAN_INVALIDATED"),
        )
        return replace(episode, episode_state=EpisodeState.RESOLVED), resolved


def build_candidates(
    parsed: StructuredAmbiguityParse,
    runtime_input: RuntimeEpisodeInput,
) -> tuple[CandidateInterpretation, ...]:
    generated = CandidateInterpretationGenerator().generate(parsed, runtime_input)
    gated = CandidateValidityDistinctnessGate().apply(parsed, generated)
    return tuple(PromptAdapter().adapt(candidate) for candidate in gated)


def candidate_target_map(candidates: Sequence[CandidateInterpretation]) -> dict[str, str | None]:
    return {
        candidate.candidate_id: candidate.candidate_specific_task_binding.symbolic_target_id
        for candidate in candidates
    }


def load_runtime_input(value: Mapping[str, Any]) -> RuntimeEpisodeInput:
    """Parse a JSON runtime fixture and reject evaluation-only keys before construction."""

    assert_no_forbidden_runtime_keys(value)
    entities: list[SymbolicSceneEntity] = []
    for raw in value.get("symbolic_scene_entities", ()):
        attrs = raw.get("observable_attributes", {})
        entities.append(
            SymbolicSceneEntity(
                entity_type=str(raw["entity_type"]),
                entity_id=raw.get("entity_id"),
                display_name=raw.get("display_name"),
                observable_attributes=tuple(sorted(dict(attrs).items())),
                ordinal=raw.get("ordinal"),
                supports_slots=tuple(raw.get("supports_slots", ())),
                symbolic_target_type=str(raw["symbolic_target_type"]),
                binding_source=str(raw.get("binding_source", "SYMBOLIC_SCENE_TABLE")),
                frame_or_semantic_domain=str(raw.get("frame_or_semantic_domain", "LANGUAGE_REFERENCE")),
                grounding_status=GroundingStatus(raw.get("grounding_status", "GROUNDED_SYMBOLIC")),
                provenance=tuple(raw.get("provenance", ("HAND_AUTHORED_LANGUAGE_FIXTURE",))),
                reason_codes=tuple(raw.get("reason_codes", ())),
            )
        )
    metadata = value.get("episode_metadata", {})
    return RuntimeEpisodeInput(
        episode_id=str(value["episode_id"]),
        instruction_id=str(value["instruction_id"]),
        raw_instruction=str(value["raw_instruction"]),
        source_observation_id=value.get("source_observation_id"),
        symbolic_scene_entities=tuple(entities),
        allowed_task_vocabulary=tuple(value.get("allowed_task_vocabulary", ())),
        observed_at_monotonic=float(value.get("observed_at_monotonic", 100.0)),
        answer_deadline_monotonic=(
            None if value.get("answer_deadline_monotonic") is None else float(value["answer_deadline_monotonic"])
        ),
        episode_metadata=tuple(sorted(dict(metadata).items())),
        provenance=tuple(value.get("provenance", ("HAND_AUTHORED_LANGUAGE_FIXTURE",))),
        reason_codes=tuple(value.get("reason_codes", ())),
    )


def _semantic_bridge_output(
    runtime_record: Mapping[str, Any],
    candidates: Sequence[CandidateInterpretation],
    source_observation_id: str | None,
) -> dict[str, Any]:
    if len(candidates) != 2:
        return {
            "status": "NOT_APPLICABLE",
            "reason_trace": ["SEMANTIC_PLAN_PAIR_REQUIRES_EXACTLY_TWO_CANDIDATES"],
            "pair_consequence_relation": "UNKNOWN",
            "m1_learned_pair_comparator_status": "NOT_APPLICABLE",
            "mode": "DIAGNOSTIC_ONLY",
            "control_authorized": False,
        }
    fixtures = runtime_record.get("plan_semantic_fixtures")
    fixtures = fixtures if isinstance(fixtures, Mapping) else {}
    plans = []
    masks: dict[str, Mapping[str, bool]] = {}
    for candidate in candidates:
        target_id = candidate.candidate_specific_task_binding.symbolic_target_id
        fixture = fixtures.get(target_id) if target_id is not None else None
        fixture_mapping = fixture if isinstance(fixture, Mapping) else {}
        plans.append(
            plan_record_for_candidate(candidate.candidate_id, source_observation_id, fixture_mapping)
        )
        mask = fixture_mapping.get("evidence_mask")
        masks[candidate.candidate_id] = dict(mask) if isinstance(mask, Mapping) else {}
    result = CandidateSpecificSemanticPlanBridge().align_pair(
        plans[0],
        candidates[0].candidate_specific_task_binding,
        plans[1],
        candidates[1].candidate_specific_task_binding,
        masks,
        ("OFFLINE_RUNTIME_PIPELINE",),
    )
    return result.to_dict()


def run_runtime_pipeline(
    runtime_record: Mapping[str, Any],
    passenger_answer_text: str | None = None,
    answer_timestamp_monotonic: float | None = None,
) -> dict[str, Any]:
    """Run only runtime-observable values; no evaluation label type is accepted or imported."""

    assert_no_forbidden_runtime_keys(runtime_record)
    runtime_input = load_runtime_input(runtime_record)
    reducer = InteractionEpisodeReducer()
    episode = reducer.new(runtime_input)
    parsed = StructuredAmbiguityParser().parse(runtime_input)
    episode = reducer.parsed(episode, parsed)
    candidates: tuple[CandidateInterpretation, ...] = ()
    question = None
    answer = None
    resolved = None
    alignment: dict[str, Any] = {
        "status": "NOT_APPLICABLE",
        "reason_trace": ["NO_CANDIDATE_PAIR_AVAILABLE"],
        "pair_consequence_relation": "UNKNOWN",
        "m1_learned_pair_comparator_status": "NOT_APPLICABLE",
        "mode": "DIAGNOSTIC_ONLY",
        "control_authorized": False,
    }
    if episode.episode_state is EpisodeState.PARSED:
        candidates = build_candidates(parsed, runtime_input)
        episode = reducer.candidates_ready(episode, candidates)
        alignment = _semantic_bridge_output(runtime_record, candidates, runtime_input.source_observation_id)
    if episode.episode_state is EpisodeState.CANDIDATES_READY:
        if parsed.ambiguity_type is AmbiguityType.UNAMBIGUOUS:
            question = ClarificationQuestionPlanner().propose(
                episode, runtime_input.observed_at_monotonic
            )
            try:
                episode, resolved = reducer.resolve_unambiguous(episode)
            except ValueError:
                episode = reducer.query_proposed(episode, question)
        else:
            question = ClarificationQuestionPlanner().propose(
                episode, runtime_input.observed_at_monotonic
            )
            episode = reducer.query_proposed(episode, question)
            if question.proposal_status is QuestionStatus.QUESTION_PROPOSAL:
                episode = reducer.query_issued(episode, question)
                timestamp = (
                    runtime_input.observed_at_monotonic
                    if answer_timestamp_monotonic is None
                    else float(answer_timestamp_monotonic)
                )
                answer = AnswerResolver().resolve(
                    question,
                    passenger_answer_text,
                    episode.episode_state,
                    timestamp,
                    episode.answer_deadline_monotonic,
                )
                episode, resolved = reducer.apply_answer(episode, answer)
    result = {
        "schema_version": "driveclarify.offline_language_pipeline.v0",
        "output_type": "OFFLINE_RESOLUTION_RESULT",
        "interaction_output": "INTERACTION_RECOMMENDATION_ONLY",
        "question_output": (
            "QUESTION_PROPOSAL"
            if question and question.proposal_status is QuestionStatus.QUESTION_PROPOSAL
            else None
        ),
        "evidence_designation": list(EVIDENCE_DESIGNATION),
        "runtime_input": runtime_input.to_dict(),
        "structured_parse": parsed.to_dict(),
        "candidate_interpretations": [candidate.to_dict() for candidate in candidates],
        "semantic_plan_alignment": alignment,
        "question_proposal": None if question is None else question.to_dict(),
        "answer_resolution": None if answer is None else answer.to_dict(),
        "resolved_instruction": None if resolved is None else resolved.to_dict(),
        "final_episode": episode.to_dict(),
        "live_ask_issued": False,
        "live_act_authorized": False,
        "live_wait_authorized": False,
        "vehicle_control_authorized": False,
        "carla_used": False,
        "simlingo_model_loaded": False,
        "checkpoint_loaded": False,
        "cuda_initialized": False,
        "gpu_used": False,
    }
    assert_no_forbidden_runtime_keys(result)
    result["deterministic_runtime_sha256"] = stable_sha256(result)
    return result
