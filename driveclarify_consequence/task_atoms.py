"""Narrow, UNKNOWN-preserving Task atoms for offline candidate-plan diagnostics.

This module consumes only explicit symbolic plan-to-task bindings.  Raw route geometry is never
used to infer a destination, maneuver, safety property, or physical unit.  All outputs are
DIAGNOSTIC_ONLY and non-authorizing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .types import EvidenceGrade, ResultStatus, UsagePurpose


class _StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TaskAtomType(_StrEnum):
    REFERENCE_GOAL = "REFERENCE_GOAL"
    DESTINATION_GOAL = "DESTINATION_GOAL"
    MANEUVER_BRANCH = "MANEUVER_BRANCH"


class TaskState(_StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class PairClass(_StrEnum):
    TASK_EQUIVALENT = "TASK_EQUIVALENT"
    TASK_CRITICAL = "TASK_CRITICAL"
    UNKNOWN = "UNKNOWN"


_DIAGNOSTIC_EVIDENCE = frozenset({EvidenceGrade.VERIFIED, EvidenceGrade.SUPPORTED})
_PHYSICAL_UNITS = frozenset({"METRE", "METER", "METRES", "METERS", "M", "SECOND", "MPS"})
_ATOM_BINDINGS = {
    TaskAtomType.REFERENCE_GOAL: (
        frozenset({"LANGUAGE_REFERENCE", "REGISTERED_TASK"}),
        frozenset({"IDENTIFIER", "CLASS"}),
        frozenset({"target_evidence"}),
    ),
    TaskAtomType.DESTINATION_GOAL: (
        frozenset({"REGISTERED_TOPOLOGY"}),
        frozenset({"REGION_ID", "CLASS"}),
        frozenset({"plan_to_goal_transform", "target_evidence"}),
    ),
    TaskAtomType.MANEUVER_BRANCH: (
        frozenset({"TOPOLOGY_BRANCH", "REGISTERED_TOPOLOGY"}),
        frozenset({"MANEUVER_CLASS", "BRANCH_ID", "CLASS"}),
        frozenset({"plan_to_maneuver_mapping", "target_evidence"}),
    ),
}


def _enum(enum_type: type[Enum], value: Any, default: Enum) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default


def _is_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class TaskAtom:
    """A registered task target plus one candidate's explicit symbolic mapping evidence."""

    atom_id: str
    atom_type: TaskAtomType
    target_id: str | None
    target_class: str | None
    mapped_target_id: str | None
    mapped_target_class: str | None
    source_candidate_id: str
    observation_id: str
    evidence_grade: EvidenceGrade
    allowed_usage_purposes: tuple[UsagePurpose, ...]
    frame: str | None
    unit: str | None
    dependencies: tuple[str, ...]
    dependency_statuses: Mapping[str, bool | None]
    status: ResultStatus
    reason_code: str | None
    source_artifacts: tuple[str, ...] = ()

    def validation_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        for name, value in (
            ("atom_id", self.atom_id),
            ("source_candidate_id", self.source_candidate_id),
            ("observation_id", self.observation_id),
        ):
            if not _is_nonempty_text(value):
                errors.append(f"MISSING_{name.upper()}")
        if not _is_nonempty_text(self.target_id) and not _is_nonempty_text(self.target_class):
            errors.append("MISSING_TASK_TARGET")
        if self.status is ResultStatus.AVAILABLE:
            if self.reason_code is not None:
                errors.append("AVAILABLE_TASK_ATOM_HAS_REASON")
            if not _is_nonempty_text(self.mapped_target_id) and not _is_nonempty_text(
                self.mapped_target_class
            ):
                errors.append("AVAILABLE_TASK_ATOM_MISSING_MAPPED_TARGET")
        elif not _is_nonempty_text(self.reason_code):
            errors.append("UNAVAILABLE_TASK_ATOM_MISSING_REASON")
        if self.unit is not None and self.unit.upper() in _PHYSICAL_UNITS:
            errors.append("TASK_ATOM_PHYSICAL_UNIT_FORBIDDEN")
        if len(set(self.dependencies)) != len(self.dependencies):
            errors.append("DUPLICATE_TASK_DEPENDENCY")
        return tuple(sorted(set(errors)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "atom_type": self.atom_type.value,
            "target_id": self.target_id,
            "target_class": self.target_class,
            "mapped_target_id": self.mapped_target_id,
            "mapped_target_class": self.mapped_target_class,
            "source_candidate_id": self.source_candidate_id,
            "observation_id": self.observation_id,
            "evidence_grade": self.evidence_grade.value,
            "allowed_usage_purposes": [item.value for item in self.allowed_usage_purposes],
            "frame": self.frame,
            "unit": self.unit,
            "dependencies": list(self.dependencies),
            "dependency_statuses": {
                key: self.dependency_statuses[key] for key in sorted(self.dependency_statuses)
            },
            "status": self.status.value,
            "reason_code": self.reason_code,
            "source_artifacts": list(self.source_artifacts),
            "usage_purpose": UsagePurpose.DIAGNOSTIC_ONLY.value,
            "authorization_eligible": False,
            "safety_critical_eligible": False,
        }


def _unavailable_atom(
    registry: Mapping[str, Any], candidate: Mapping[str, Any], reason_code: str
) -> TaskAtom:
    return TaskAtom(
        atom_id=str(registry.get("atom_id", "")),
        atom_type=_enum(
            TaskAtomType,
            registry.get("atom_type"),
            TaskAtomType.REFERENCE_GOAL,
        ),
        target_id=registry.get("target_id"),
        target_class=registry.get("target_class"),
        mapped_target_id=None,
        mapped_target_class=None,
        source_candidate_id=str(candidate.get("candidate_id", "")),
        observation_id=str(candidate.get("source_observation_id", "")),
        evidence_grade=EvidenceGrade.NOT_AVAILABLE,
        allowed_usage_purposes=(UsagePurpose.DIAGNOSTIC_ONLY,),
        frame=registry.get("frame"),
        unit=registry.get("unit"),
        dependencies=tuple(str(item) for item in registry.get("dependencies", ())),
        dependency_statuses={},
        status=ResultStatus.UNKNOWN,
        reason_code=reason_code,
    )


def map_candidate_plan_to_task_atoms(
    candidate: Mapping[str, Any],
    registered_atoms: Sequence[Mapping[str, Any]],
    observation_id: str,
) -> tuple[TaskAtom, ...]:
    """Merge explicit candidate ``task_bindings`` with the registered task atom schema.

    Missing bindings produce UNKNOWN atoms.  This function deliberately does not inspect
    ``pred_route_raw`` or ``pred_speed_wps_raw``.
    """

    bindings_raw = candidate.get("task_bindings")
    bindings = bindings_raw if isinstance(bindings_raw, list) else []
    by_id: dict[str, Mapping[str, Any]] = {}
    duplicates: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, Mapping):
            continue
        atom_id = str(binding.get("atom_id", ""))
        if atom_id in by_id:
            duplicates.add(atom_id)
        by_id[atom_id] = binding

    atoms: list[TaskAtom] = []
    for registry in sorted(registered_atoms, key=lambda item: str(item.get("atom_id", ""))):
        atom_id = str(registry.get("atom_id", ""))
        try:
            TaskAtomType(registry.get("atom_type"))
        except (TypeError, ValueError):
            atoms.append(_unavailable_atom(registry, candidate, "INVALID_TASK_ATOM_TYPE"))
            continue
        if atom_id in duplicates:
            atoms.append(_unavailable_atom(registry, candidate, "DUPLICATE_TASK_BINDING"))
            continue
        binding = by_id.get(atom_id)
        if binding is None:
            atoms.append(
                _unavailable_atom(registry, candidate, "PLAN_TO_TASK_MAPPING_UNAVAILABLE")
            )
            continue
        if candidate.get("source_observation_id") != observation_id:
            atoms.append(_unavailable_atom(registry, candidate, "TASK_OBSERVATION_MISMATCH"))
            continue

        purposes: list[UsagePurpose] = []
        invalid_purpose = False
        for value in binding.get("allowed_usage_purposes", ()):
            try:
                purposes.append(UsagePurpose(value))
            except (TypeError, ValueError):
                invalid_purpose = True
        status = _enum(ResultStatus, binding.get("status"), ResultStatus.UNKNOWN)
        reason_code = binding.get("reason_code")
        if invalid_purpose:
            status = ResultStatus.UNKNOWN
            reason_code = "INVALID_USAGE_PURPOSE"
        try:
            grade = EvidenceGrade(binding.get("evidence_grade"))
        except (TypeError, ValueError):
            grade = EvidenceGrade.NOT_AVAILABLE
        dep_status = binding.get("dependency_statuses")
        if not isinstance(dep_status, Mapping):
            dep_status = {}
        atoms.append(
            TaskAtom(
                atom_id=atom_id,
                atom_type=_enum(
                    TaskAtomType,
                    registry.get("atom_type"),
                    TaskAtomType.REFERENCE_GOAL,
                ),
                target_id=registry.get("target_id"),
                target_class=registry.get("target_class"),
                mapped_target_id=binding.get("mapped_target_id"),
                mapped_target_class=binding.get("mapped_target_class"),
                source_candidate_id=str(candidate.get("candidate_id", "")),
                observation_id=str(candidate.get("source_observation_id", "")),
                evidence_grade=grade,
                allowed_usage_purposes=tuple(purposes),
                frame=registry.get("frame"),
                unit=registry.get("unit"),
                dependencies=tuple(str(item) for item in registry.get("dependencies", ())),
                dependency_statuses={str(key): value for key, value in dep_status.items()},
                status=status,
                reason_code=reason_code,
                source_artifacts=tuple(
                    str(item) for item in binding.get("source_artifacts", ())
                ),
            )
        )
    return tuple(atoms)


def _task_evaluation(atom: TaskAtom, state: TaskState, reason_code: str) -> dict[str, Any]:
    return {
        "atom_id": atom.atom_id,
        "atom_type": atom.atom_type.value,
        "target_id": atom.target_id,
        "target_class": atom.target_class,
        "mapped_target_id": atom.mapped_target_id,
        "mapped_target_class": atom.mapped_target_class,
        "source_candidate_id": atom.source_candidate_id,
        "observation_id": atom.observation_id,
        "status": state.value,
        "reason_code": reason_code,
        "evidence_grade": atom.evidence_grade.value,
        "allowed_usage_purposes": [item.value for item in atom.allowed_usage_purposes],
        "frame": atom.frame,
        "unit": atom.unit,
        "dependencies": list(atom.dependencies),
        "dependency_statuses": {
            key: atom.dependency_statuses[key] for key in sorted(atom.dependency_statuses)
        },
        "source_artifacts": list(atom.source_artifacts),
        "usage_purpose": UsagePurpose.DIAGNOSTIC_ONLY.value,
        "authorization_eligible": False,
        "safety_critical_eligible": False,
    }


def evaluate_task_atom(atom: TaskAtom) -> dict[str, Any]:
    """Evaluate one candidate atom as PASS/FAIL/UNKNOWN with fail-closed evidence checks."""

    errors = atom.validation_errors()
    if errors:
        result = _task_evaluation(atom, TaskState.UNKNOWN, "TASK_ATOM_SCHEMA_INVALID")
        result["reason_details"] = list(errors)
        return result
    if atom.status is not ResultStatus.AVAILABLE:
        return _task_evaluation(
            atom, TaskState.UNKNOWN, atom.reason_code or "TASK_MAPPING_NOT_AVAILABLE"
        )
    if atom.evidence_grade not in _DIAGNOSTIC_EVIDENCE:
        return _task_evaluation(atom, TaskState.UNKNOWN, "TASK_EVIDENCE_GRADE_INELIGIBLE")
    if UsagePurpose.DIAGNOSTIC_ONLY not in atom.allowed_usage_purposes:
        return _task_evaluation(atom, TaskState.UNKNOWN, "TASK_USAGE_PURPOSE_NOT_ALLOWED")

    allowed_frames, allowed_units, required_dependencies = _ATOM_BINDINGS[atom.atom_type]
    if atom.frame not in allowed_frames:
        return _task_evaluation(atom, TaskState.UNKNOWN, "TASK_FRAME_NOT_ESTABLISHED")
    if atom.unit not in allowed_units:
        return _task_evaluation(atom, TaskState.UNKNOWN, "TASK_UNIT_NOT_ESTABLISHED")
    if not required_dependencies.issubset(atom.dependencies):
        return _task_evaluation(atom, TaskState.UNKNOWN, "TASK_REQUIRED_DEPENDENCY_MISSING")
    for dependency in atom.dependencies:
        if dependency not in atom.dependency_statuses:
            return _task_evaluation(atom, TaskState.UNKNOWN, "TASK_DEPENDENCY_STATUS_MISSING")
        value = atom.dependency_statuses[dependency]
        if value is None:
            return _task_evaluation(atom, TaskState.UNKNOWN, "TASK_DEPENDENCY_UNKNOWN")
        if value is not True:
            return _task_evaluation(atom, TaskState.UNKNOWN, "TASK_DEPENDENCY_UNAVAILABLE")

    if atom.target_id is not None:
        matched = atom.mapped_target_id == atom.target_id
    else:
        matched = atom.mapped_target_class == atom.target_class
    return _task_evaluation(
        atom,
        TaskState.PASS if matched else TaskState.FAIL,
        "TASK_TARGET_MATCH" if matched else "TASK_TARGET_MISMATCH",
    )


def reduce_task_pair(
    evaluations_by_candidate: Mapping[str, Sequence[Mapping[str, Any]]],
    registered_atom_ids: Sequence[str],
) -> dict[str, Any]:
    """Reduce exactly two candidates to TASK_EQUIVALENT/TASK_CRITICAL/UNKNOWN.

    A verified difference wins even if another atom is UNKNOWN.  Otherwise any missing/UNKNOWN
    atom propagates UNKNOWN.  Raw geometry is not an input to this reducer.
    """

    candidate_ids = sorted(evaluations_by_candidate)
    comparisons: list[dict[str, Any]] = []
    reason_code = "TASK_PAIR_EVALUATION_UNKNOWN"
    pair_class = PairClass.UNKNOWN
    if len(candidate_ids) != 2:
        reason_code = "TASK_PAIR_REQUIRES_EXACTLY_TWO_CANDIDATES"
    elif not registered_atom_ids:
        reason_code = "NO_REGISTERED_TASK_ATOMS"
    else:
        indexes: dict[str, dict[str, Mapping[str, Any]]] = {}
        for candidate_id in candidate_ids:
            indexes[candidate_id] = {
                str(item.get("atom_id")): item
                for item in evaluations_by_candidate[candidate_id]
            }
        known_difference = False
        has_unknown = False
        for atom_id in sorted(set(str(item) for item in registered_atom_ids)):
            left = indexes[candidate_ids[0]].get(atom_id)
            right = indexes[candidate_ids[1]].get(atom_id)
            left_state = left.get("status") if left else None
            right_state = right.get("status") if right else None
            if left_state not in {TaskState.PASS.value, TaskState.FAIL.value} or right_state not in {
                TaskState.PASS.value,
                TaskState.FAIL.value,
            }:
                relation = "UNKNOWN"
                has_unknown = True
            elif left_state != right_state:
                relation = "DIFFERENT"
                known_difference = True
            else:
                relation = "SAME"
            comparisons.append(
                {
                    "atom_id": atom_id,
                    "left_candidate_id": candidate_ids[0],
                    "right_candidate_id": candidate_ids[1],
                    "left_status": left_state,
                    "right_status": right_state,
                    "relation": relation,
                }
            )
        if known_difference:
            pair_class = PairClass.TASK_CRITICAL
            reason_code = "VERIFIED_TASK_STATE_DIFFERENCE"
        elif has_unknown:
            pair_class = PairClass.UNKNOWN
            reason_code = "TASK_EVALUATION_UNKNOWN"
        else:
            pair_class = PairClass.TASK_EQUIVALENT
            reason_code = "ALL_REGISTERED_TASK_STATES_MATCH"

    return {
        "schema_version": "driveclarify.task_pair_equivalence.v1",
        "candidate_ids": candidate_ids,
        "registered_atom_ids": sorted(set(str(item) for item in registered_atom_ids)),
        "pair_class": pair_class.value,
        "reason_code": reason_code,
        "atom_comparisons": comparisons,
        "raw_plan_metrics_used": False,
        "usage_purpose": UsagePurpose.DIAGNOSTIC_ONLY.value,
        "authorization_eligible": False,
        "safety_critical_eligible": False,
    }
