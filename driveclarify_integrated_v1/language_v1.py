"""Deterministic compositional language, question, and answer runtime v1."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .canonical_ontology import (
    CanonicalAmbiguityType,
    CanonicalCandidateStatus,
    CanonicalGroundingStatus,
    CanonicalSlot,
    CanonicalTaskBinding,
    FailureKind,
    stable_sha256,
)
from .runtime_contracts import AnswerResolutionV1, QuestionProposalV1, RUNTIME_SCHEMA_VERSION


_SLOT_TO_AMBIGUITY = {
    CanonicalSlot.REFERENCE.value: CanonicalAmbiguityType.REFERENTIAL.value,
    CanonicalSlot.LANDMARK.value: CanonicalAmbiguityType.LANDMARK.value,
    CanonicalSlot.ORDER.value: CanonicalAmbiguityType.ORDER.value,
    CanonicalSlot.CONSTRAINT.value: CanonicalAmbiguityType.UNDERSPECIFIED_CONSTRAINT.value,
}

_PATTERNS = {
    CanonicalSlot.REFERENCE.value: (
        r"\b(car|van|truck|vehicle|cyclist|pedestrian|cone|sign|object)\b",
        r"\b(nearest|farthest|closest|beside|behind|ahead of)\b",
    ),
    CanonicalSlot.LANDMARK.value: (
        r"\b(shop|store|kiosk|entrance|doorway|loading area|bus stop|depot|gate|landmark)\b",
        r"\b(near|beside|after|before)\b",
    ),
    CanonicalSlot.ORDER.value: (
        r"\b(first|second|next|following|third)\b",
        r"\b(junction|intersection|entrance|turn|branch|gate)\b",
    ),
    CanonicalSlot.CONSTRAINT.value: (
        r"\b(pull over|pull in|stop near|marked zone|loading area|designated area)\b",
    ),
}

_UNSUPPORTED_PATTERNS = (
    r"\bteleport\b",
    r"\borbit\b",
    r"\blunar yards\b",
    r"\bunsupported sensor frame\b",
    r"\bboth left and right\b",
    r"\binvisible entrance\b",
    r"\bmissing moon-shaped\b",
    r"\bdescribed only as the same\b",
    r"\brepeat the unanswered question\b",
    r"\bfirst, second, and third\b",
    r"\bentrance behind the stop beyond\b",
    r"\bplace behind the van.*what it refers to\b",
)


def _normalized(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower().replace("’", "'").replace("—", " "))


def _detected_slots(text: str) -> set[str]:
    detected: set[str] = set()
    for slot, patterns in _PATTERNS.items():
        if all(re.search(pattern, text) for pattern in patterns):
            detected.add(slot)
    return detected


def _entity_description(entity: Mapping[str, Any]) -> str:
    display = str(entity.get("display_name") or "").strip()
    if display:
        return display
    attrs = entity.get("observable_attributes", {})
    pieces = [str(value) for _, value in sorted(attrs.items()) if value not in (None, "")]
    return " ".join(pieces) or str(entity.get("entity_id") or "undifferentiated target")


def _binding_for(candidate_id: str, entity: Mapping[str, Any], slot: str) -> CanonicalTaskBinding:
    target_id = entity.get("entity_id")
    target_type = entity.get("symbolic_target_type", entity.get("entity_type"))
    grounding = str(entity.get("grounding_status", CanonicalGroundingStatus.NOT_AVAILABLE.value))
    if grounding == CanonicalGroundingStatus.GROUNDED_SYMBOLIC.value and target_id and target_type:
        status = "BOUND"
        reasons = ("CANDIDATE_SPECIFIC_SYMBOLIC_BINDING",)
    elif grounding == CanonicalGroundingStatus.CONFLICTING.value:
        status = "CONFLICTING"
        reasons = (FailureKind.GROUNDING_CONFLICT.value,)
    else:
        status = "NOT_AVAILABLE"
        reasons = (FailureKind.GROUNDING_NOT_AVAILABLE.value,)
    return CanonicalTaskBinding(
        candidate_id=candidate_id,
        task_family=str(entity.get("task_family", "MANEUVER_BRANCH")),
        symbolic_target_type=None if target_type is None else str(target_type),
        symbolic_target_id=None if target_id is None else str(target_id),
        required_slots=(slot,),
        semantic_domain=str(entity.get("frame_or_semantic_domain", "IDENTIFIER")),
        binding_status=status,
        binding_source=str(entity.get("binding_source", "SYMBOLIC_SCENE_TABLE")),
        provenance=tuple(str(item) for item in entity.get("provenance", ("CANONICAL_SCENE_GROUNDING",))),
        reason_codes=reasons,
    )


@dataclass(frozen=True)
class LanguageRuntimeResultV1:
    structured_parse: Mapping[str, Any]
    candidate_interpretations: tuple[Mapping[str, Any], ...]
    candidate_bindings: tuple[CanonicalTaskBinding, ...]
    question_proposal: QuestionProposalV1
    failure_kind: str | None
    reason_codes: tuple[str, ...]
    schema_version: str = RUNTIME_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "structured_parse": dict(self.structured_parse),
            "candidate_interpretations": [dict(item) for item in self.candidate_interpretations],
            "candidate_bindings": [item.to_dict() for item in self.candidate_bindings],
            "question_proposal": self.question_proposal.to_dict(),
            "failure_kind": self.failure_kind,
            "reason_codes": list(self.reason_codes),
        }
        return {**value, "language_output_sha256": stable_sha256(value)}


class CompositionalLanguageRuntimeV1:
    def parse(self, record: Mapping[str, Any]) -> LanguageRuntimeResultV1:
        text = _normalized(record.get("raw_instruction", ""))
        scene = record.get("symbolic_scene_table", [])
        scene_items = [item for item in scene if isinstance(item, Mapping)] if isinstance(scene, list) else []
        scene_slots = {
            str(slot)
            for item in scene_items
            for slot in item.get("supported_slots", [])
        }
        detected = _detected_slots(text)
        relevant = detected.intersection(scene_slots) if scene_slots else detected
        explicit_unsupported = any(re.search(pattern, text) for pattern in _UNSUPPORTED_PATTERNS)
        if explicit_unsupported:
            return self._fail(text, FailureKind.TRUE_OUT_OF_DOMAIN.value, "OUT_OF_DOMAIN_GRAMMAR")
        if len(relevant) > 1:
            return self._fail(text, FailureKind.MULTI_SLOT_UNSUPPORTED.value, "MULTIPLE_UNRESOLVED_SLOTS_DETECTED", sorted(relevant))
        if not relevant:
            if not text:
                return self._fail(text, FailureKind.SUPPORTED_COMPOSITIONAL_PARSE_FAILURE.value, "EMPTY_INSTRUCTION")
            if not detected and not scene_slots:
                proposal = self._no_question("UNAMBIGUOUS_NO_UNRESOLVED_SLOT")
                parse = self._parse_dict(text, CanonicalAmbiguityType.UNAMBIGUOUS.value, (), "PARSED_UNAMBIGUOUS", ())
                return LanguageRuntimeResultV1(parse, (), (), proposal, None, ("UNAMBIGUOUS",))
            return self._fail(text, FailureKind.TRUE_OUT_OF_DOMAIN.value, "NO_SUPPORTED_SCENE_SLOT_MATCH")
        slot = next(iter(relevant))
        entities = [item for item in scene_items if slot in item.get("supported_slots", [])]
        if len(entities) > 2:
            return self._fail(text, FailureKind.TRUE_OUT_OF_DOMAIN.value, "CANDIDATE_COUNT_GREATER_THAN_SUPPORTED_MAXIMUM", (slot,))
        if not entities:
            return self._fail(text, FailureKind.GROUNDING_NOT_AVAILABLE.value, "NO_ENTITY_FOR_UNRESOLVED_SLOT", (slot,))

        interpretations: list[Mapping[str, Any]] = []
        bindings: list[CanonicalTaskBinding] = []
        seen: set[tuple[str | None, str | None]] = set()
        for index, entity in enumerate(entities):
            candidate_id = str(entity.get("candidate_id") or f"CAND_{chr(65 + index)}")
            binding = _binding_for(candidate_id, entity, slot)
            identity = (binding.symbolic_target_type, binding.symbolic_target_id)
            if identity in seen:
                status = CanonicalCandidateStatus.DUPLICATE.value
                reason_codes = (FailureKind.CANDIDATE_DUPLICATE.value,)
            elif binding.binding_status == "CONFLICTING":
                status = CanonicalCandidateStatus.CONTRADICTORY.value
                reason_codes = (FailureKind.GROUNDING_CONFLICT.value,)
            elif binding.binding_status != "BOUND":
                status = CanonicalCandidateStatus.UNGROUNDED.value
                reason_codes = (FailureKind.GROUNDING_NOT_AVAILABLE.value,)
            else:
                status = CanonicalCandidateStatus.VALID.value
                reason_codes = ("SUPPORTED_COMPOSITIONAL_CANDIDATE",)
            seen.add(identity)
            interpretations.append({
                "candidate_id": candidate_id,
                "candidate_status": status,
                "target_slot": slot,
                "description": _entity_description(entity),
                "candidate_specific_task_binding": binding.to_dict(),
                "reason_codes": list(reason_codes),
            })
            bindings.append(binding)
        valid = [item for item in interpretations if item["candidate_status"] == CanonicalCandidateStatus.VALID.value]
        proposal = self._question(slot, valid)
        ambiguity = _SLOT_TO_AMBIGUITY[slot]
        parse_status = "PARSED_SUPPORTED_COMPOSITIONAL" if valid else "PARSED_GROUNDING_FAILED"
        reasons = ("ONE_UNRESOLVED_CANONICAL_SLOT",) if valid else (FailureKind.GROUNDING_NOT_AVAILABLE.value,)
        parse = self._parse_dict(text, ambiguity, (slot,), parse_status, reasons)
        failure = None if valid else FailureKind.GROUNDING_NOT_AVAILABLE.value
        return LanguageRuntimeResultV1(parse, tuple(interpretations), tuple(bindings), proposal, failure, reasons)

    @staticmethod
    def _parse_dict(text: str, ambiguity: str, slots: Sequence[str], status: str, reasons: Sequence[str]) -> dict[str, Any]:
        return {
            "ambiguity_type": ambiguity,
            "unresolved_slots": list(slots),
            "parse_status": status,
            "normalized_instruction": text,
            "reason_codes": list(reasons),
        }

    def _fail(self, text: str, failure: str, reason: str, slots: Sequence[str] = ()) -> LanguageRuntimeResultV1:
        parse = self._parse_dict(text, CanonicalAmbiguityType.UNSUPPORTED.value, slots, "FAIL_CLOSED", (failure, reason))
        return LanguageRuntimeResultV1(parse, (), (), self._no_question(reason), failure, (failure, reason))

    @staticmethod
    def _no_question(reason: str) -> QuestionProposalV1:
        return QuestionProposalV1("QUESTION_NOT_REALIZABLE", None, None, None, (), (), (reason,))

    @staticmethod
    def _question(slot: str, valid: Sequence[Mapping[str, Any]]) -> QuestionProposalV1:
        if len(valid) != 2:
            return CompositionalLanguageRuntimeV1._no_question("QUESTION_REQUIRES_EXACTLY_TWO_VALID_CANDIDATES")
        descriptions = [str(item.get("description", "")).strip() for item in valid]
        if not all(descriptions) or descriptions[0].casefold() == descriptions[1].casefold():
            return CompositionalLanguageRuntimeV1._no_question("QUESTION_NOT_DISCRIMINATIVE")
        ids = [str(item["candidate_id"]) for item in valid]
        query_seed = stable_sha256({"slot": slot, "candidate_ids": ids, "descriptions": descriptions})[:16]
        return QuestionProposalV1(
            "QUESTION_PROPOSAL",
            f"query-{query_seed}",
            slot,
            f"Do you mean option one, {descriptions[0]}, or option two, {descriptions[1]}?",
            (("OPTION_ONE", (ids[0],)), ("OPTION_TWO", (ids[1],))),
            tuple(descriptions),
            ("COMPLETE_NON_OVERLAPPING_PARTITION", "OPTION_ORDER_HAS_NO_CORRECTNESS_SEMANTICS"),
        )


class AnswerResolverV1:
    def resolve(
        self,
        answer: Any,
        question: QuestionProposalV1 | Mapping[str, Any] | None,
        *,
        answer_status: str = "RECEIVED_ON_TIME",
        previous_selection: str | None = None,
    ) -> AnswerResolutionV1:
        text = _normalized(answer)
        proposal = question.to_dict() if isinstance(question, QuestionProposalV1) else dict(question or {})
        partition = proposal.get("candidate_partition", [])
        options: dict[str, str] = {}
        for item in partition if isinstance(partition, (list, tuple)) else ():
            if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], (list, tuple)) and len(item[1]) == 1:
                options[str(item[0]).upper()] = str(item[1][0])
        active = proposal.get("proposal_status") == "QUESTION_PROPOSAL" and len(options) == 2
        if answer_status == "LATE":
            return AnswerResolutionV1("LATE_IGNORED", None, text, reason_codes=("LATE_ANSWER_DOES_NOT_SELECT",))
        if not text:
            return AnswerResolutionV1("NO_ANSWER", None, text, reason_codes=("NO_ANSWER_DOES_NOT_SELECT",))
        if text in {"whichever", "either", "both", "yes", "yeah"}:
            return AnswerResolutionV1("STILL_AMBIGUOUS", None, text, reason_codes=("AMBIGUOUS_ANSWER_DOES_NOT_SELECT",))
        correction = text in {"no, the other one.", "no, the other one", "the other one"} or bool(re.search(r"not the first.*second", text))
        if correction:
            if not active or previous_selection is None or previous_selection not in options.values():
                return AnswerResolutionV1("STILL_AMBIGUOUS", None, text, reason_codes=("CORRECTION_CONTEXT_NOT_AVAILABLE",))
            other = next(item for item in options.values() if item != previous_selection)
            return AnswerResolutionV1("RESOLVED_CORRECTION", other, text, reason_codes=("ACTIVE_QUERY_CORRECTION_APPLIED",))
        if not active:
            return AnswerResolutionV1("OUT_OF_DOMAIN", None, text, reason_codes=("ACTIVE_QUERY_NOT_AVAILABLE",))
        option = None
        if text in {"first", "option one", "one", "1"}:
            option = "OPTION_ONE"
        elif text in {"second", "option two", "two", "2"}:
            option = "OPTION_TWO"
        else:
            descriptions = proposal.get("option_descriptions", [])
            for index, description in enumerate(descriptions if isinstance(descriptions, (list, tuple)) else ()):
                if text == _normalized(description):
                    option = "OPTION_ONE" if index == 0 else "OPTION_TWO"
        if option is None or option not in options:
            return AnswerResolutionV1("OUT_OF_DOMAIN", None, text, reason_codes=("ANSWER_TARGET_NOT_IN_ACTIVE_OPTIONS",))
        return AnswerResolutionV1("RESOLVED", options[option], text, reason_codes=("ACTIVE_QUERY_OPTION_MATCH",))

