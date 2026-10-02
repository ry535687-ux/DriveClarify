"""Explicit coordinate/object/reference-point semantics used by the M3A audit."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class EvidenceGrade(str, Enum):
    VERIFIED_FROM_RECORDED_RUNTIME = "VERIFIED_FROM_RECORDED_RUNTIME"
    SUPPORTED_FROM_SOURCE_TRACE = "SUPPORTED_FROM_SOURCE_TRACE"
    SUPPORTED_FROM_STATIC_CONFIG = "SUPPORTED_FROM_STATIC_CONFIG"
    NOT_CURRENTLY_AVAILABLE = "NOT_CURRENTLY_AVAILABLE"
    CONFLICTING = "CONFLICTING"


@dataclass(frozen=True)
class CoordinateSemanticRecord:
    field_name: str
    source_file: str
    source_function: str
    runtime_object: str
    physical_object: str
    reference_point: str
    coordinate_frame: str
    unit: str
    clock_frame_id: str
    update_timing: str
    filtered: bool | None
    sensor_offset_affected: bool | None
    provenance: tuple[str, ...]
    evidence_grade: EvidenceGrade
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_grade"] = self.evidence_grade.value
        value["provenance"] = list(self.provenance)
        value["reason_codes"] = list(self.reason_codes)
        return value


@dataclass(frozen=True)
class SemanticComparison:
    same_physical_object: bool
    same_reference_point: bool
    same_coordinate_frame: bool
    same_unit: bool
    same_clock_frame: bool
    comparable: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_coordinate_semantics(
    left: CoordinateSemanticRecord,
    right: CoordinateSemanticRecord,
) -> SemanticComparison:
    """Compare declared semantics, never numeric values or suggestive field names."""

    same_object = left.physical_object == right.physical_object
    same_reference = left.reference_point == right.reference_point
    same_frame = left.coordinate_frame == right.coordinate_frame
    same_unit = left.unit == right.unit
    same_clock = left.clock_frame_id == right.clock_frame_id
    reasons: list[str] = []
    if not same_object:
        reasons.append("PHYSICAL_OBJECT_IDENTITY_MISMATCH")
    if not same_reference:
        reasons.append("REFERENCE_POINT_MISMATCH")
    if not same_frame:
        reasons.append("COORDINATE_FRAME_MISMATCH")
    if not same_unit:
        reasons.append("COORDINATE_UNIT_MISMATCH")
    if not same_clock:
        reasons.append("CLOCK_FRAME_IDENTITY_MISMATCH")
    return SemanticComparison(
        same_object,
        same_reference,
        same_frame,
        same_unit,
        same_clock,
        not reasons,
        tuple(reasons),
    )


def records_to_table(records: Mapping[str, CoordinateSemanticRecord]) -> dict[str, Any]:
    return {
        "schema_version": "driveclarify.coordinate_semantics.v1",
        "allowed_evidence_grades": [item.value for item in EvidenceGrade],
        "records": [records[key].to_dict() for key in sorted(records)],
    }
