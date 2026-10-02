"""Frozen-shape task-level comparator and minimal RQ1-V2 decision gate.

The comparator intentionally has no trajectory-distance threshold and accepts no
passenger-intent operand.  A scene contract declares the subset of task fields
that are genuinely relevant; incomplete or inconsistent certificates abstain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Mapping, Optional, Tuple, Union


TASK_COMPONENTS = (
    "terminal_task_region",
    "terminal_road_or_corridor",
    "maneuver_obligation",
    "irreversible_branch_obligation",
    "goal_lane_or_side_obligation_if_task_relevant",
    "task_completion_region",
)


class AmbiguityStatus(str, Enum):
    CLEAR = "CLEAR"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


class ConsequenceRelation(str, Enum):
    TASK_EQUIVALENT = "TASK_EQUIVALENT"
    TASK_CRITICAL = "TASK_CRITICAL"
    UNKNOWN = "UNKNOWN"


class GateAction(str, Enum):
    ACT = "ACT"
    ASK = "ASK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TaskSignature:
    candidate_id: str
    relevant_components: Tuple[str, ...]
    values: Mapping[str, Optional[str]]
    certified: bool
    certificate_id: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskSignature":
        return cls(
            candidate_id=str(value.get("candidate_id", "")),
            relevant_components=tuple(value.get("relevant_components", ())),
            values={key: value.get(key) for key in TASK_COMPONENTS},
            certified=value.get("certified") is True,
            certificate_id=str(value.get("certificate_id", "")),
        )

    def validation_errors(self) -> Tuple[str, ...]:
        errors: List[str] = []
        if not self.candidate_id:
            errors.append("CANDIDATE_ID_MISSING")
        if not self.certificate_id:
            errors.append("CERTIFICATE_ID_MISSING")
        if not self.certified:
            errors.append("SIGNATURE_NOT_CERTIFIED")
        if not self.relevant_components:
            errors.append("RELEVANT_COMPONENT_SET_EMPTY")
        if len(set(self.relevant_components)) != len(self.relevant_components):
            errors.append("RELEVANT_COMPONENT_DUPLICATE")
        for component in self.relevant_components:
            if component not in TASK_COMPONENTS:
                errors.append("UNKNOWN_COMPONENT:" + component)
            elif self.values.get(component) in (None, ""):
                errors.append("RELEVANT_VALUE_MISSING:" + component)
        return tuple(errors)


@dataclass(frozen=True)
class ConsequenceComparison:
    relation: ConsequenceRelation
    compared_components: Tuple[str, ...]
    differing_components: Tuple[str, ...]
    reason_codes: Tuple[str, ...]
    certificate_ids: Tuple[str, str]


@dataclass(frozen=True)
class GateDecision:
    action: GateAction
    ambiguity_status: AmbiguityStatus
    consequence_relation: ConsequenceRelation
    selected_candidate_id: Optional[str]
    reason_codes: Tuple[str, ...]


def compare_task_signatures(
    first: TaskSignature, second: TaskSignature
) -> ConsequenceComparison:
    """Compare two certified task signatures, preserving incomplete evidence."""

    first_errors = first.validation_errors()
    second_errors = second.validation_errors()
    if first_errors or second_errors:
        return ConsequenceComparison(
            relation=ConsequenceRelation.UNKNOWN,
            compared_components=(),
            differing_components=(),
            reason_codes=(
                "TASK_EQUIVALENCE_NOT_CERTIFIED",
                *("FIRST:" + item for item in first_errors),
                *("SECOND:" + item for item in second_errors),
            ),
            certificate_ids=(first.certificate_id, second.certificate_id),
        )
    if first.candidate_id == second.candidate_id:
        return ConsequenceComparison(
            relation=ConsequenceRelation.UNKNOWN,
            compared_components=(),
            differing_components=(),
            reason_codes=("CANDIDATE_IDENTITIES_NOT_DISTINCT",),
            certificate_ids=(first.certificate_id, second.certificate_id),
        )
    if first.relevant_components != second.relevant_components:
        return ConsequenceComparison(
            relation=ConsequenceRelation.UNKNOWN,
            compared_components=(),
            differing_components=(),
            reason_codes=("RELEVANCE_SCHEMA_MISMATCH",),
            certificate_ids=(first.certificate_id, second.certificate_id),
        )
    differing = tuple(
        component
        for component in first.relevant_components
        if first.values[component] != second.values[component]
    )
    relation = (
        ConsequenceRelation.TASK_CRITICAL
        if differing
        else ConsequenceRelation.TASK_EQUIVALENT
    )
    return ConsequenceComparison(
        relation=relation,
        compared_components=first.relevant_components,
        differing_components=differing,
        reason_codes=(
            "CERTIFIED_TASK_OBLIGATION_DIFFERS"
            if differing
            else "CERTIFIED_TASK_OBLIGATIONS_EQUAL",
        ),
        certificate_ids=(first.certificate_id, second.certificate_id),
    )


def consequence_gate(
    ambiguity_status: AmbiguityStatus,
    relation: ConsequenceRelation,
    *,
    deterministic_candidate_id: Optional[str],
) -> GateDecision:
    """Apply the complete, fail-closed RQ1-V2 decision table."""

    if ambiguity_status is AmbiguityStatus.CLEAR:
        return GateDecision(
            GateAction.ACT,
            ambiguity_status,
            relation,
            deterministic_candidate_id,
            ("CLEAR_ACT",),
        )
    if (
        ambiguity_status is AmbiguityStatus.AMBIGUOUS
        and relation is ConsequenceRelation.TASK_EQUIVALENT
        and deterministic_candidate_id
    ):
        return GateDecision(
            GateAction.ACT,
            ambiguity_status,
            relation,
            deterministic_candidate_id,
            ("AMBIGUOUS_CERTIFIED_TASK_EQUIVALENT_ACT_RANK_ONE",),
        )
    if (
        ambiguity_status is AmbiguityStatus.AMBIGUOUS
        and relation is ConsequenceRelation.TASK_CRITICAL
    ):
        return GateDecision(
            GateAction.ASK,
            ambiguity_status,
            relation,
            None,
            ("AMBIGUOUS_CERTIFIED_TASK_CRITICAL_ASK",),
        )
    return GateDecision(
        GateAction.UNKNOWN,
        ambiguity_status,
        relation,
        None,
        ("UNKNOWN_FAIL_CLOSED_PRESERVE_EXISTING_AUTHORITY",),
    )
