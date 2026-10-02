"""Versioned canonical ontology and explicit external-schema translation."""

from __future__ import annotations

import hashlib
import json
import copy
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


ONTOLOGY_SCHEMA_VERSION = "driveclarify.canonical_runtime_ontology.v1"


class StableStrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class CanonicalAmbiguityType(StableStrEnum):
    REFERENTIAL = "REFERENTIAL"
    LANDMARK = "LANDMARK"
    ORDER = "ORDER"
    UNDERSPECIFIED_CONSTRAINT = "UNDERSPECIFIED_CONSTRAINT"
    UNAMBIGUOUS = "UNAMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class CanonicalSlot(StableStrEnum):
    REFERENCE = "reference_slot"
    LANDMARK = "landmark_slot"
    ORDER = "order_slot"
    CONSTRAINT = "constraint_slot"


class CanonicalCandidateStatus(StableStrEnum):
    VALID = "VALID"
    DUPLICATE = "DUPLICATE"
    UNGROUNDED = "UNGROUNDED"
    INCOMPLETE = "INCOMPLETE"
    CONTRADICTORY = "CONTRADICTORY"
    UNSUPPORTED = "UNSUPPORTED"


class CanonicalGroundingStatus(StableStrEnum):
    GROUNDED_SYMBOLIC = "GROUNDED_SYMBOLIC"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    CONFLICTING = "CONFLICTING"
    UNSUPPORTED = "UNSUPPORTED"


class FailureKind(StableStrEnum):
    SCHEMA_TRANSLATION_FAILURE = "SCHEMA_TRANSLATION_FAILURE"
    SUPPORTED_COMPOSITIONAL_PARSE_FAILURE = "SUPPORTED_COMPOSITIONAL_PARSE_FAILURE"
    TRUE_OUT_OF_DOMAIN = "TRUE_OUT_OF_DOMAIN"
    MULTI_SLOT_UNSUPPORTED = "MULTI_SLOT_UNSUPPORTED"
    GROUNDING_NOT_AVAILABLE = "GROUNDING_NOT_AVAILABLE"
    GROUNDING_CONFLICT = "GROUNDING_CONFLICT"
    CANDIDATE_DUPLICATE = "CANDIDATE_DUPLICATE"


@dataclass(frozen=True)
class CanonicalTaskBinding:
    candidate_id: str
    task_family: str
    symbolic_target_type: str | None
    symbolic_target_id: str | None
    required_slots: tuple[str, ...]
    semantic_domain: str
    binding_status: str
    binding_source: str
    provenance: tuple[str, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = ONTOLOGY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


@dataclass(frozen=True)
class AliasRule:
    source_schema: str
    registry_field: str
    external_value: str
    canonical_value: str
    rule_id: str


@dataclass(frozen=True)
class TranslationTrace:
    source_schema: str
    registry_field: str
    source_value: str
    canonical_value: str | None
    mapping_status: str
    rule_id: str | None
    reason_codes: tuple[str, ...]
    schema_version: str = ONTOLOGY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


def canonical_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return canonical_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): canonical_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(canonical_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class VersionedAliasRegistry:
    """Closed registry: only an exact (schema, field, value) rule can translate."""

    def __init__(self, rules: Sequence[AliasRule]) -> None:
        self._rules = tuple(rules)
        self._index = {
            (rule.source_schema, rule.registry_field, rule.external_value): rule
            for rule in self._rules
        }
        if len(self._index) != len(self._rules):
            raise ValueError("ALIAS_REGISTRY_DUPLICATE_RULE")

    @property
    def rules(self) -> tuple[AliasRule, ...]:
        return self._rules

    def translate(self, source_schema: str, registry_field: str, source_value: Any) -> tuple[str | None, TranslationTrace]:
        text = str(source_value)
        rule = self._index.get((source_schema, registry_field, text))
        if rule is None:
            schema_known = any(item.source_schema == source_schema for item in self._rules)
            reason = "ALIAS_VALUE_NOT_REGISTERED" if schema_known else "UNKNOWN_SOURCE_SCHEMA"
            return None, TranslationTrace(
                source_schema=source_schema,
                registry_field=registry_field,
                source_value=text,
                canonical_value=None,
                mapping_status="REJECTED",
                rule_id=None,
                reason_codes=(FailureKind.SCHEMA_TRANSLATION_FAILURE.value, reason),
            )
        return rule.canonical_value, TranslationTrace(
            source_schema=source_schema,
            registry_field=registry_field,
            source_value=text,
            canonical_value=rule.canonical_value,
            mapping_status="MAPPED_EXPLICIT_ALIAS" if text != rule.canonical_value else "CANONICAL_IDENTITY",
            rule_id=rule.rule_id,
            reason_codes=("EXPLICIT_VERSIONED_ALIAS_RULE",),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ONTOLOGY_SCHEMA_VERSION,
            "rules": [canonical_value(item) for item in self._rules],
            "registry_sha256": stable_sha256([canonical_value(item) for item in self._rules]),
        }


_SUPPORTED_SCHEMAS = (
    "driveclarify.integrated_runtime_input.v1",
    "driveclarify.m2c_blind_integrated_challenge.v0",
    "driveclarify.development_external.alpha.v1",
    "driveclarify.development_external.beta.v1",
)


def _identity_rules(field: str, values: Sequence[str]) -> list[AliasRule]:
    return [
        AliasRule(schema, field, value, value, f"{schema}:{field}:{value}")
        for schema in _SUPPORTED_SCHEMAS
        for value in values
    ]


_rules: list[AliasRule] = []
_rules.extend(_identity_rules("slot", [item.value for item in CanonicalSlot]))
_rules.extend(_identity_rules("ambiguity_type", [item.value for item in CanonicalAmbiguityType]))
_rules.extend(_identity_rules("grounding_status", [item.value for item in CanonicalGroundingStatus]))
for schema in _SUPPORTED_SCHEMAS:
    for external, canonical in (
        ("reference_entity", CanonicalSlot.REFERENCE.value),
        ("reference_object", CanonicalSlot.REFERENCE.value),
        ("landmark", CanonicalSlot.LANDMARK.value),
        ("landmark_entity", CanonicalSlot.LANDMARK.value),
        ("ordered_trigger", CanonicalSlot.ORDER.value),
        ("intersection_order", CanonicalSlot.ORDER.value),
        ("pull_over_zone", CanonicalSlot.CONSTRAINT.value),
        ("spatial_constraint", CanonicalSlot.CONSTRAINT.value),
    ):
        _rules.append(AliasRule(schema, "slot", external, canonical, f"{schema}:slot:{external}"))


ALIAS_REGISTRY = VersionedAliasRegistry(_rules)


def ontology_document() -> dict[str, Any]:
    document = {
        "schema_version": ONTOLOGY_SCHEMA_VERSION,
        "ambiguity_types": [item.value for item in CanonicalAmbiguityType],
        "slots": [item.value for item in CanonicalSlot],
        "candidate_statuses": [item.value for item in CanonicalCandidateStatus],
        "grounding_statuses": [item.value for item in CanonicalGroundingStatus],
        "failure_kinds": [item.value for item in FailureKind],
        "alias_registry": ALIAS_REGISTRY.to_dict(),
    }
    return {**document, "canonical_sha256": stable_sha256(document)}


class ExternalToCanonicalAdapterV1:
    """Translate external records without consulting labels or fuzzy matching."""

    def adapt(self, record: Mapping[str, Any]) -> tuple[dict[str, Any] | None, tuple[TranslationTrace, ...]]:
        source_schema = str(record.get("schema_version", ""))
        if source_schema not in _SUPPORTED_SCHEMAS:
            _, trace = ALIAS_REGISTRY.translate(source_schema, "slot", "")
            return None, (trace,)
        result = copy.deepcopy(dict(record))
        traces: list[TranslationTrace] = []
        scene_key = "symbolic_scene_table" if "symbolic_scene_table" in result else "scene_entities"
        scene = result.get(scene_key, [])
        if not isinstance(scene, list):
            trace = TranslationTrace(
                source_schema, "scene", type(scene).__name__, None, "REJECTED", None,
                (FailureKind.SCHEMA_TRANSLATION_FAILURE.value, "SCENE_TABLE_NOT_A_LIST"),
            )
            return None, (trace,)
        canonical_scene: list[dict[str, Any]] = []
        for raw in scene:
            if not isinstance(raw, Mapping):
                trace = TranslationTrace(
                    source_schema, "scene_entity", type(raw).__name__, None, "REJECTED", None,
                    (FailureKind.SCHEMA_TRANSLATION_FAILURE.value, "SCENE_ENTITY_NOT_AN_OBJECT"),
                )
                return None, tuple((*traces, trace))
            item = copy.deepcopy(dict(raw))
            raw_slots = item.get("supported_slots", item.get("supports_slots", []))
            canonical_slots: list[str] = []
            for slot in raw_slots if isinstance(raw_slots, list) else []:
                mapped, trace = ALIAS_REGISTRY.translate(source_schema, "slot", slot)
                traces.append(trace)
                if mapped is None:
                    return None, tuple(traces)
                canonical_slots.append(mapped)
            grounding_raw = item.get("grounding_status", CanonicalGroundingStatus.NOT_AVAILABLE.value)
            grounding, trace = ALIAS_REGISTRY.translate(source_schema, "grounding_status", grounding_raw)
            traces.append(trace)
            if grounding is None:
                return None, tuple(traces)
            item["supported_slots"] = sorted(set(canonical_slots))
            item.pop("supports_slots", None)
            item["grounding_status"] = grounding
            item.setdefault("observable_attributes", {})
            item.setdefault("frame_or_semantic_domain", "IDENTIFIER")
            canonical_scene.append(item)
        result["symbolic_scene_table"] = canonical_scene
        if scene_key != "symbolic_scene_table":
            result.pop(scene_key, None)
        result["source_schema_version"] = source_schema
        result["schema_version"] = "driveclarify.integrated_runtime_input.v1"
        result["canonicalization_trace"] = [item.to_dict() for item in traces]
        result["canonical_input_sha256"] = stable_sha256({key: value for key, value in result.items() if key != "canonical_input_sha256"})
        return result, tuple(traces)
