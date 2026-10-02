"""Fail-closed readiness contract for one real plan-semantic mapping pilot."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class MappingReadinessVerdict(str, Enum):
    READY_FOR_INFERRED_MAPPING = "READY_FOR_INFERRED_MAPPING"
    PARTIALLY_SUPPORTED_MAPPING = "PARTIALLY_SUPPORTED_MAPPING"
    NOT_READY_MAPPING = "NOT_READY_MAPPING"


REQUIRED_DEPENDENCIES = (
    "source_observation_identity",
    "ego_actor_transform",
    "model_plan_frame",
    "model_plan_unit",
    "frame_calibration",
    "route_topology",
    "candidate_specific_branch_anchors",
    "projection_algorithm",
    "mapping_threshold_provenance",
    "scenario_runtime_fairness",
    "artifact_provenance",
)

SATISFIED = frozenset({"VERIFIED", "SUPPORTED_FROM_SOURCE_TRACE", "SUPPORTED_FROM_STATIC_CONFIG"})


@dataclass(frozen=True)
class PlanSemanticMappingReadinessV1:
    dependencies: Mapping[str, str]
    verdict: MappingReadinessVerdict
    reason_codes: tuple[str, ...]
    schema_version: str = "driveclarify.plan_semantic_mapping_readiness.v1"

    @classmethod
    def evaluate(cls, dependencies: Mapping[str, str]) -> "PlanSemanticMappingReadinessV1":
        reasons: list[str] = []
        for name in REQUIRED_DEPENDENCIES:
            if name not in dependencies:
                reasons.append("DEPENDENCY_MISSING:" + name)
            elif dependencies[name] not in SATISFIED:
                reasons.append("DEPENDENCY_NOT_SATISFIED:" + name + ":" + str(dependencies[name]))
        if not reasons:
            verdict = MappingReadinessVerdict.READY_FOR_INFERRED_MAPPING
        elif any(
            item.startswith("DEPENDENCY_MISSING:source_observation_identity")
            or item.startswith("DEPENDENCY_NOT_SATISFIED:scenario_runtime_fairness")
            or item.startswith("DEPENDENCY_NOT_SATISFIED:frame_calibration")
            for item in reasons
        ):
            verdict = MappingReadinessVerdict.NOT_READY_MAPPING
        else:
            verdict = MappingReadinessVerdict.PARTIALLY_SUPPORTED_MAPPING
        return cls(dict(dependencies), verdict, tuple(reasons))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["verdict"] = self.verdict.value
        value["reason_codes"] = list(self.reason_codes)
        value["dependencies"] = dict(sorted(self.dependencies.items()))
        return value
