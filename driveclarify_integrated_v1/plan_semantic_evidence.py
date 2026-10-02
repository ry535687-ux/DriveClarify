"""Explicit candidate-specific plan semantic evidence contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .canonical_ontology import canonical_value
from .runtime_contracts import RUNTIME_SCHEMA_VERSION


MAPPING_STATUSES = frozenset(
    {
        "MAPPED_SYMBOLIC_TARGET",
        "MAPPING_NOT_AVAILABLE",
        "MAPPING_CONFLICTING",
        "FRAME_UNSUPPORTED",
        "PROVENANCE_INVALID",
    }
)


@dataclass(frozen=True)
class PlanSemanticEvidence:
    candidate_id: str
    source_observation_id: str | None
    plan_frame: str | None
    plan_unit: str | None
    raw_route_available: bool
    raw_speed_available: bool
    symbolic_plan_target_type: str | None
    symbolic_plan_target_id: str | None
    mapping_status: str
    mapping_source: str | None
    mapping_provenance: tuple[str, ...]
    evidence_grade: str
    reason_codes: tuple[str, ...]
    schema_version: str = RUNTIME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.mapping_status not in MAPPING_STATUSES:
            raise ValueError("PLAN_SEMANTIC_MAPPING_STATUS_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


def plan_semantic_evidence_from_record(record: Mapping[str, Any]) -> PlanSemanticEvidence:
    candidate_id = str(record.get("candidate_id", ""))
    target_type = record.get("symbolic_plan_target_type")
    target_id = record.get("symbolic_plan_target_id")
    mapping_source = record.get("mapping_source")
    provenance = tuple(str(item) for item in record.get("mapping_provenance", record.get("provenance", ())))
    plan_frame = None if record.get("plan_frame") is None else str(record.get("plan_frame"))
    plan_unit = None if record.get("plan_unit") is None else str(record.get("plan_unit"))
    declared_status = record.get("mapping_status")
    if declared_status is not None and str(declared_status) not in MAPPING_STATUSES:
        status = "PROVENANCE_INVALID"
        reasons = ("PLAN_SEMANTIC_MAPPING_STATUS_INVALID",)
    elif str(declared_status) == "MAPPING_CONFLICTING":
        status = "MAPPING_CONFLICTING"
        reasons = ("PLAN_TO_SYMBOLIC_TARGET_CONFLICT",)
    elif target_type is None or target_id is None:
        status = "MAPPING_NOT_AVAILABLE"
        reasons = ("PLAN_TO_SYMBOLIC_TARGET_MAPPING_MISSING",)
    elif not mapping_source or not provenance:
        status = "PROVENANCE_INVALID"
        reasons = ("PLAN_SEMANTIC_MAPPING_PROVENANCE_INVALID",)
    elif plan_frame not in {"MODEL_LOCAL_RAW", "SYMBOLIC_PLAN_TARGET"} or plan_unit not in {"RAW_UNIT", "IDENTIFIER"}:
        status = "FRAME_UNSUPPORTED"
        reasons = ("PLAN_FRAME_OR_UNIT_UNSUPPORTED",)
    else:
        status = "MAPPED_SYMBOLIC_TARGET"
        reasons = (
            "EXPLICIT_PLAN_TO_SYMBOLIC_TARGET_MAPPING",
            "MODEL_LOCAL_RAW_NOT_INTERPRETED_AS_CARLA_WORLD",
            "RAW_UNIT_NOT_INTERPRETED_AS_METRE",
        )
    route = record.get("raw_route_tokens", record.get("pred_route_raw"))
    speed = record.get("raw_speed_profile_tokens", record.get("pred_speed_wps_raw"))
    return PlanSemanticEvidence(
        candidate_id=candidate_id,
        source_observation_id=None if record.get("source_observation_id") is None else str(record.get("source_observation_id")),
        plan_frame=plan_frame,
        plan_unit=plan_unit,
        raw_route_available=route is not None,
        raw_speed_available=speed is not None,
        symbolic_plan_target_type=None if target_type is None else str(target_type),
        symbolic_plan_target_id=None if target_id is None else str(target_id),
        mapping_status=status,
        mapping_source=None if mapping_source is None else str(mapping_source),
        mapping_provenance=provenance,
        evidence_grade="AUTHORITATIVE_SYMBOLIC_OFFLINE" if status == "MAPPED_SYMBOLIC_TARGET" else "NOT_EVALUABLE",
        reason_codes=reasons,
    )

