"""Immutable contracts for the optional ambiguity-discovery prototype."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


SCHEMA_VERSION = "driveclarify.ambiguity_discovery_prototype_v0.v0"
DESIGNATION = (
    "PROTOTYPE_ONLY",
    "OFFLINE_SYMBOLIC_DIAGNOSTIC",
    "NOT_PAPER_MVP_EVALUATION",
    "NOT_LEARNED_AMBIGUITY_DETECTOR",
    "NOT_AUTONOMOUS_VLA",
)


@dataclass(frozen=True)
class EnvironmentEntity:
    """A symbolic stand-in for a perception/environment candidate.

    v0 intentionally consumes an already available environment table.  It does not
    claim that these attributes were learned or perceived by this package.
    """

    entity_id: str
    entity_type: str
    display_name: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EnvironmentEntity":
        entity_id = str(value.get("entity_id", "")).strip()
        entity_type = str(value.get("entity_type", "")).strip().lower()
        display_name = str(value.get("display_name", "")).strip()
        attributes = value.get("attributes", {})
        if not entity_id:
            raise ValueError("ENTITY_ID_REQUIRED")
        if not entity_type:
            raise ValueError("ENTITY_TYPE_REQUIRED")
        if not display_name:
            raise ValueError("ENTITY_DISPLAY_NAME_REQUIRED")
        if not isinstance(attributes, Mapping):
            raise ValueError("ENTITY_ATTRIBUTES_MUST_BE_MAPPING")
        return cls(entity_id, entity_type, display_name, dict(attributes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "display_name": self.display_name,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class CandidateInterpretation:
    candidate_id: str
    entity: EnvironmentEntity
    slot: str
    reference_text: str
    interpretation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "entity_id": self.entity.entity_id,
            "entity": self.entity.to_dict(),
            "slot": self.slot,
            "reference_text": self.reference_text,
            "interpretation": self.interpretation,
        }


@dataclass(frozen=True)
class DiscoveryResult:
    instruction: str
    ambiguity_detected: bool
    analyzed_slot: str | None
    detected_ambiguous_slot: str | None
    unresolved_slot: str | None
    reference_text: str | None
    candidate_entities: tuple[EnvironmentEntity, ...]
    candidate_interpretations: tuple[CandidateInterpretation, ...]
    confidence: float
    reason: str
    reason_codes: tuple[str, ...]
    consequence_handoff: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION
    designation: tuple[str, ...] = DESIGNATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "designation": list(self.designation),
            "instruction": self.instruction,
            "ambiguity_detected": self.ambiguity_detected,
            "analyzed_slot": self.analyzed_slot,
            "detected_ambiguous_slot": self.detected_ambiguous_slot,
            "unresolved_slot": self.unresolved_slot,
            "reference_text": self.reference_text,
            "candidate_entities": [item.to_dict() for item in self.candidate_entities],
            "candidate_entity_ids": [item.entity_id for item in self.candidate_entities],
            "candidate_interpretations": [
                item.to_dict() for item in self.candidate_interpretations
            ],
            "confidence": self.confidence,
            "reason": self.reason,
            "reason_codes": list(self.reason_codes),
            "consequence_handoff": dict(self.consequence_handoff),
        }

