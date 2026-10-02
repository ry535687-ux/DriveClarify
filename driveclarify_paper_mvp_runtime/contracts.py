"""Strict, catalog-free contracts for the paper MVP Stage 6A runtime.

Only ordinary runtime observations cross the policy boundary.  Evaluation
labels, scenario identities, frozen candidate text, and evaluator ordering are
rejected before candidate generation or a model callback can run.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, is_dataclass
from numbers import Real
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, Tuple


SCHEMA_VERSION = "driveclarify.paper_mvp_stage6a_runtime.v1"

# This is a protective schema denylist, not data loaded from the evaluation
# catalog.  The runtime package performs no filesystem or catalog access.
FORBIDDEN_RUNTIME_FIELDS = frozenset(
    {
        "scenario_id",
        "scenario_type",
        "expected_decision",
        "expected_decision_for_validation",
        "decision_reason",
        "ambiguity_type",
        "scenario_ambiguity_description",
        "missing_slots",
        "candidate_interpretations",
        "ground_truth_reason",
        "candidate_consequence_summary",
        "candidate_consequence_linkage",
        "query_value_expectation",
        "wait_value_expectation",
        "answer_impact",
        "future_information_impact",
        "future_information_resolution_oracle_by_seed",
        "wait_evidence",
        "implementation_status",
        "calibration_status",
        "provenance_scope",
        "split",
        "prompt_family_id",
        "scenario_template_id",
        "object_combination_id",
        "object_combination",
        "layout_group_id",
        "paired_family_id",
        "candidate_order_by_seed",
    }
)

_FORBIDDEN_SOURCE_MARKERS = (
    "catalog",
    "evaluation_annotation",
    "evaluation_label",
    "ground_truth",
    "oracle",
    "candidate_order",
    "scenario_template",
    "fixture_declared",
    "dcv0-s",
)
_HEX = frozenset("0123456789abcdef")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_CATEGORY = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,63}$")


class Stage6AContractError(ValueError):
    """Raised before an untrusted or evaluation-tainted value is consumed."""


def _canonical(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_sha256(value: Any, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise Stage6AContractError(field + "_MUST_BE_LOWERCASE_SHA256")
    return value


def _require_text(value: Any, field: str, *, maximum: int = 4096) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise Stage6AContractError(field + "_MUST_BE_NONEMPTY_BOUNDED_TEXT")
    if any(ord(character) < 32 and character not in "\t\n" for character in value):
        raise Stage6AContractError(field + "_CONTAINS_CONTROL_CHARACTER")
    return value


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise Stage6AContractError(field + "_MUST_BE_FINITE")
    number = float(value)
    if not math.isfinite(number):
        raise Stage6AContractError(field + "_MUST_BE_FINITE")
    return number


def assert_no_evaluation_fields(value: Any) -> None:
    """Recursively reject every evaluator-only key at the policy boundary."""

    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        keys = {str(key).casefold() for key in value}
        overlap = keys.intersection(FORBIDDEN_RUNTIME_FIELDS)
        if overlap:
            raise Stage6AContractError(
                "EVALUATION_ONLY_FIELD:" + ",".join(sorted(overlap))
            )
        for item in value.values():
            assert_no_evaluation_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_no_evaluation_fields(item)


def assert_unprivileged_source(value: Any, field: str) -> str:
    text = _require_text(value, field, maximum=256)
    lowered = text.casefold()
    if any(marker in lowered for marker in _FORBIDDEN_SOURCE_MARKERS):
        raise Stage6AContractError(field + "_EVALUATION_OR_PRIVILEGED_SOURCE_FORBIDDEN")
    return text


def _require_exact_keys(
    payload: Mapping[str, Any], expected: frozenset[str], field: str
) -> None:
    if not isinstance(payload, Mapping):
        raise Stage6AContractError(field + "_MUST_BE_MAPPING")
    actual = {str(key) for key in payload}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise Stage6AContractError(
            field
            + "_SCHEMA_MISMATCH:missing="
            + repr(missing)
            + ":extra="
            + repr(extra)
        )


@dataclass(frozen=True)
class VisualReference:
    """One online vision-pipeline detection usable as a language anchor."""

    track_id: str
    category: str
    relative_bearing_degrees: float
    relative_distance_m: float
    confidence: float
    observation_source: str

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.track_id):
            raise Stage6AContractError("VISUAL_TRACK_ID_INVALID")
        assert_unprivileged_source(self.track_id, "VISUAL_TRACK_ID")
        if not _SAFE_CATEGORY.fullmatch(self.category):
            raise Stage6AContractError("VISUAL_CATEGORY_INVALID")
        assert_unprivileged_source(self.category, "VISUAL_CATEGORY")
        bearing = _finite(self.relative_bearing_degrees, "VISUAL_BEARING")
        distance = _finite(self.relative_distance_m, "VISUAL_DISTANCE")
        confidence = _finite(self.confidence, "VISUAL_CONFIDENCE")
        if not -180.0 <= bearing <= 180.0:
            raise Stage6AContractError("VISUAL_BEARING_OUT_OF_RANGE")
        if distance < 0.0:
            raise Stage6AContractError("VISUAL_DISTANCE_MUST_BE_NONNEGATIVE")
        if not 0.0 <= confidence <= 1.0:
            raise Stage6AContractError("VISUAL_CONFIDENCE_OUT_OF_RANGE")
        assert_unprivileged_source(self.observation_source, "VISUAL_SOURCE")

    @property
    def sort_key(self) -> tuple[float, float, str, str]:
        return (
            round(float(self.relative_bearing_degrees), 9),
            round(float(self.relative_distance_m), 9),
            self.category.casefold(),
            self.track_id,
        )


@dataclass(frozen=True)
class VisionObservation:
    observation_id: str
    frame_id: int
    captured_monotonic_time: float
    image_sha256: str
    image_width: int
    image_height: int
    references: tuple[VisualReference, ...]
    source: str

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.observation_id):
            raise Stage6AContractError("VISION_OBSERVATION_ID_INVALID")
        assert_unprivileged_source(self.observation_id, "VISION_OBSERVATION_ID")
        if type(self.frame_id) is not int or self.frame_id < 0:
            raise Stage6AContractError("VISION_FRAME_ID_INVALID")
        _finite(self.captured_monotonic_time, "VISION_CAPTURE_TIME")
        _require_sha256(self.image_sha256, "VISION_IMAGE_DIGEST")
        if type(self.image_width) is not int or self.image_width <= 0:
            raise Stage6AContractError("VISION_IMAGE_WIDTH_INVALID")
        if type(self.image_height) is not int or self.image_height <= 0:
            raise Stage6AContractError("VISION_IMAGE_HEIGHT_INVALID")
        if not isinstance(self.references, tuple) or any(
            not isinstance(item, VisualReference) for item in self.references
        ):
            raise Stage6AContractError("VISION_REFERENCES_INVALID")
        if len(self.references) > 64:
            raise Stage6AContractError("VISION_REFERENCE_COUNT_EXCEEDED")
        if len({item.track_id for item in self.references}) != len(self.references):
            raise Stage6AContractError("VISION_TRACK_ID_DUPLICATED")
        assert_unprivileged_source(self.source, "VISION_SOURCE")

    def canonical_projection(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "frame_id": self.frame_id,
            "captured_monotonic_time": float(self.captured_monotonic_time),
            "image_sha256": self.image_sha256,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "references": [
                asdict(item) for item in sorted(self.references, key=lambda item: item.sort_key)
            ],
            "source": self.source,
        }


@dataclass(frozen=True)
class EgoState:
    observed_monotonic_time: float
    position_x_m: float
    position_y_m: float
    yaw_degrees: float
    speed_mps: float
    source: str

    def __post_init__(self) -> None:
        _finite(self.observed_monotonic_time, "EGO_OBSERVED_TIME")
        _finite(self.position_x_m, "EGO_POSITION_X")
        _finite(self.position_y_m, "EGO_POSITION_Y")
        _finite(self.yaw_degrees, "EGO_YAW")
        if _finite(self.speed_mps, "EGO_SPEED") < 0.0:
            raise Stage6AContractError("EGO_SPEED_MUST_BE_NONNEGATIVE")
        assert_unprivileged_source(self.source, "EGO_SOURCE")


@dataclass(frozen=True)
class RouteContext:
    observed_monotonic_time: float
    route_command: str
    target_point_x_m: float
    target_point_y_m: float
    route_digest: str
    source: str

    def __post_init__(self) -> None:
        _finite(self.observed_monotonic_time, "ROUTE_OBSERVED_TIME")
        if not _SAFE_ID.fullmatch(self.route_command):
            raise Stage6AContractError("ROUTE_COMMAND_INVALID")
        assert_unprivileged_source(self.route_command, "ROUTE_COMMAND")
        _finite(self.target_point_x_m, "ROUTE_TARGET_X")
        _finite(self.target_point_y_m, "ROUTE_TARGET_Y")
        _require_sha256(self.route_digest, "ROUTE_DIGEST")
        assert_unprivileged_source(self.source, "ROUTE_SOURCE")


@dataclass(frozen=True)
class PolicyEpisodeInput:
    """The complete and exclusive Stage 6A policy-visible input."""

    raw_instruction: str
    vision_observation: VisionObservation
    ego_state: EgoState
    route_context: RouteContext
    opaque_token: str

    def __post_init__(self) -> None:
        _require_text(self.raw_instruction, "RAW_INSTRUCTION")
        if not isinstance(self.vision_observation, VisionObservation):
            raise Stage6AContractError("VISION_OBSERVATION_CONTRACT_REQUIRED")
        if not isinstance(self.ego_state, EgoState):
            raise Stage6AContractError("EGO_STATE_CONTRACT_REQUIRED")
        if not isinstance(self.route_context, RouteContext):
            raise Stage6AContractError("ROUTE_CONTEXT_CONTRACT_REQUIRED")
        _require_sha256(self.opaque_token, "OPAQUE_EPISODE_TOKEN")
        observed = float(self.vision_observation.captured_monotonic_time)
        if not math.isclose(
            float(self.ego_state.observed_monotonic_time),
            observed,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise Stage6AContractError("EGO_VISION_TIME_IDENTITY_MISMATCH")
        if not math.isclose(
            float(self.route_context.observed_monotonic_time),
            observed,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise Stage6AContractError("ROUTE_VISION_TIME_IDENTITY_MISMATCH")
        assert_no_evaluation_fields(self.public_projection())

    def public_projection(self) -> dict[str, Any]:
        return {
            "raw_instruction": self.raw_instruction,
            "vision_observation": self.vision_observation.canonical_projection(),
            "ego_state": asdict(self.ego_state),
            "route_context": asdict(self.route_context),
            "opaque_token": self.opaque_token,
        }

    @property
    def input_digest(self) -> str:
        return canonical_sha256(self.public_projection())

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PolicyEpisodeInput":
        assert_no_evaluation_fields(payload)
        _require_exact_keys(
            payload,
            frozenset(
                {
                    "raw_instruction",
                    "vision_observation",
                    "ego_state",
                    "route_context",
                    "opaque_token",
                }
            ),
            "POLICY_EPISODE_INPUT",
        )
        vision = payload["vision_observation"]
        _require_exact_keys(
            vision,
            frozenset(
                {
                    "observation_id",
                    "frame_id",
                    "captured_monotonic_time",
                    "image_sha256",
                    "image_width",
                    "image_height",
                    "references",
                    "source",
                }
            ),
            "VISION_OBSERVATION",
        )
        references: list[VisualReference] = []
        for raw_reference in vision["references"]:
            _require_exact_keys(
                raw_reference,
                frozenset(
                    {
                        "track_id",
                        "category",
                        "relative_bearing_degrees",
                        "relative_distance_m",
                        "confidence",
                        "observation_source",
                    }
                ),
                "VISUAL_REFERENCE",
            )
            references.append(VisualReference(**raw_reference))
        ego = payload["ego_state"]
        _require_exact_keys(
            ego,
            frozenset(
                {
                    "observed_monotonic_time",
                    "position_x_m",
                    "position_y_m",
                    "yaw_degrees",
                    "speed_mps",
                    "source",
                }
            ),
            "EGO_STATE",
        )
        route = payload["route_context"]
        _require_exact_keys(
            route,
            frozenset(
                {
                    "observed_monotonic_time",
                    "route_command",
                    "target_point_x_m",
                    "target_point_y_m",
                    "route_digest",
                    "source",
                }
            ),
            "ROUTE_CONTEXT",
        )
        return cls(
            raw_instruction=payload["raw_instruction"],
            vision_observation=VisionObservation(
                references=tuple(references),
                **{key: value for key, value in vision.items() if key != "references"},
            ),
            ego_state=EgoState(**ego),
            route_context=RouteContext(**route),
            opaque_token=payload["opaque_token"],
        )


@dataclass(frozen=True)
class RuntimeCandidate:
    candidate_id: str
    interpretation_id: str
    prompt_text: str
    visual_track_id: str
    visual_anchor_digest: str
    candidate_semantic_digest: str
    candidate_input_digest: str
    source_observation_id: str
    source_frame_id: int
    generator_rule: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.candidate_id, "CANDIDATE_ID"),
            (self.interpretation_id, "INTERPRETATION_ID"),
            (self.visual_track_id, "CANDIDATE_TRACK_ID"),
            (self.source_observation_id, "CANDIDATE_OBSERVATION_ID"),
            (self.generator_rule, "CANDIDATE_GENERATOR_RULE"),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise Stage6AContractError(field + "_INVALID")
            assert_unprivileged_source(value, field)
        _require_text(self.prompt_text, "CANDIDATE_PROMPT")
        _require_sha256(self.visual_anchor_digest, "VISUAL_ANCHOR_DIGEST")
        _require_sha256(self.candidate_semantic_digest, "CANDIDATE_SEMANTIC_DIGEST")
        _require_sha256(self.candidate_input_digest, "CANDIDATE_INPUT_DIGEST")
        if type(self.source_frame_id) is not int or self.source_frame_id < 0:
            raise Stage6AContractError("CANDIDATE_FRAME_ID_INVALID")


@dataclass(frozen=True)
class CandidateGenerationAudit:
    generator_id: str
    generator_contract_sha256: str
    policy_input_sha256: str
    instruction_sha256: str
    vision_observation_sha256: str
    ego_state_sha256: str
    route_context_sha256: str
    opaque_token_sha256: str
    candidate_ids: tuple[str, ...]
    candidate_semantic_sha256: tuple[str, ...]
    candidate_prompt_sha256: tuple[str, ...]
    runtime_source_modalities: tuple[str, ...]
    runtime_order_basis: str
    catalog_read_count: int
    evaluation_label_access_count: int
    catalog_candidate_order_visible: bool
    runtime_annotation_overlap_checked: bool
    forbidden_field_overlap: tuple[str, ...]
    status: str
    reason_codes: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _canonical(self)


@dataclass(frozen=True)
class CandidateGenerationResult:
    status: str
    candidates: tuple[RuntimeCandidate, ...]
    audit: CandidateGenerationAudit
    reason_codes: tuple[str, ...]


def _plan_points(value: Any, field: str, *, exact_length: int | None = None) -> None:
    if not isinstance(value, tuple) or (exact_length is not None and len(value) != exact_length):
        raise Stage6AContractError(field + "_SHAPE_INVALID")
    if exact_length is None and len(value) < 2:
        raise Stage6AContractError(field + "_SHAPE_INVALID")
    for point in value:
        if not isinstance(point, tuple) or len(point) != 2:
            raise Stage6AContractError(field + "_SHAPE_INVALID")
        _finite(point[0], field)
        _finite(point[1], field)


@dataclass(frozen=True)
class CandidatePlan:
    candidate_id: str
    source_observation_id: str
    source_frame_id: int
    route: tuple[tuple[float, float], ...]
    speed: tuple[tuple[float, float], ...]
    language: tuple[str, ...]
    model_forward_sequence_id: str
    latency_s: float

    def __post_init__(self) -> None:
        for value, field in (
            (self.candidate_id, "PLAN_CANDIDATE_ID"),
            (self.source_observation_id, "PLAN_OBSERVATION_ID"),
            (self.model_forward_sequence_id, "PLAN_FORWARD_ID"),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise Stage6AContractError(field + "_INVALID")
            assert_unprivileged_source(value, field)
        if type(self.source_frame_id) is not int or self.source_frame_id < 0:
            raise Stage6AContractError("PLAN_FRAME_ID_INVALID")
        _plan_points(self.route, "PLAN_ROUTE")
        _plan_points(self.speed, "PLAN_SPEED", exact_length=10)
        if not isinstance(self.language, tuple) or any(
            type(item) is not str for item in self.language
        ):
            raise Stage6AContractError("PLAN_LANGUAGE_INVALID")
        if _finite(self.latency_s, "PLAN_LATENCY") < 0.0:
            raise Stage6AContractError("PLAN_LATENCY_MUST_BE_NONNEGATIVE")

    @property
    def output_digest(self) -> str:
        return canonical_sha256(
            {"route": self.route, "speed": self.speed, "language": self.language}
        )


@dataclass(frozen=True)
class ConsequenceEvaluation:
    status: str
    source_observation_id: str
    source_frame_id: int
    candidate_ids: tuple[str, ...]
    evaluator_id: str
    mapping_context: Any | None
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"AVAILABLE_VERIFIED", "UNKNOWN"}:
            raise Stage6AContractError("CONSEQUENCE_STATUS_INVALID")
        if not _SAFE_ID.fullmatch(self.source_observation_id):
            raise Stage6AContractError("CONSEQUENCE_OBSERVATION_ID_INVALID")
        assert_unprivileged_source(self.source_observation_id, "CONSEQUENCE_OBSERVATION_ID")
        if type(self.source_frame_id) is not int or self.source_frame_id < 0:
            raise Stage6AContractError("CONSEQUENCE_FRAME_ID_INVALID")
        if len(self.candidate_ids) != 2 or len(set(self.candidate_ids)) != 2:
            raise Stage6AContractError("CONSEQUENCE_CANDIDATE_IDENTITIES_INVALID")
        assert_unprivileged_source(self.evaluator_id, "CONSEQUENCE_EVALUATOR")
        if self.status == "AVAILABLE_VERIFIED" and self.mapping_context is None:
            raise Stage6AContractError("CONSEQUENCE_MAPPING_CONTEXT_REQUIRED")
        if self.status == "UNKNOWN" and self.mapping_context is not None:
            raise Stage6AContractError("UNKNOWN_CONSEQUENCE_CANNOT_CARRY_MAPPING")


@dataclass(frozen=True)
class HardRuleEvidence:
    status: str
    availability: str
    evidence_grade: str
    source: str
    source_kind: str
    source_observation_id: str
    source_frame_id: int
    observed_monotonic_time: float
    rule_critical_eligible: bool
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "CLEAR", "UNKNOWN", "BLOCKED"}:
            raise Stage6AContractError("HARD_RULE_STATUS_INVALID")
        if self.availability not in {"AVAILABLE_VERIFIED", "UNKNOWN"}:
            raise Stage6AContractError("HARD_RULE_AVAILABILITY_INVALID")
        assert_unprivileged_source(self.source, "HARD_RULE_SOURCE")
        assert_unprivileged_source(self.source_kind, "HARD_RULE_SOURCE_KIND")
        if not _SAFE_ID.fullmatch(self.source_observation_id):
            raise Stage6AContractError("HARD_RULE_OBSERVATION_ID_INVALID")
        if type(self.source_frame_id) is not int or self.source_frame_id < 0:
            raise Stage6AContractError("HARD_RULE_FRAME_ID_INVALID")
        _finite(self.observed_monotonic_time, "HARD_RULE_TIME")
        verified_pass = (
            self.status in {"PASS", "CLEAR"}
            and self.availability == "AVAILABLE_VERIFIED"
            and self.evidence_grade == "VERIFIED_FROM_CONTROLLED_PROBE"
            and self.source_kind == "INDEPENDENT_TRAFFIC_RULE_MONITOR"
            and self.rule_critical_eligible is True
        )
        verified_block = (
            self.status == "BLOCKED"
            and self.availability == "AVAILABLE_VERIFIED"
            and self.evidence_grade == "VERIFIED_FROM_CONTROLLED_PROBE"
            and self.source_kind == "INDEPENDENT_TRAFFIC_RULE_MONITOR"
            and self.rule_critical_eligible is False
        )
        unknown = (
            self.status == "UNKNOWN"
            and self.availability == "UNKNOWN"
            and self.evidence_grade == "NOT_CURRENTLY_AVAILABLE"
            and self.rule_critical_eligible is False
        )
        if not (verified_pass or verified_block or unknown):
            raise Stage6AContractError("HARD_RULE_EVIDENCE_ELIGIBILITY_CONFLICT")

    @property
    def authorizes_progress(self) -> bool:
        return (
            self.status in {"PASS", "CLEAR"}
            and self.availability == "AVAILABLE_VERIFIED"
            and self.evidence_grade == "VERIFIED_FROM_CONTROLLED_PROBE"
            and self.rule_critical_eligible is True
        )

    @classmethod
    def unknown(
        cls,
        episode: PolicyEpisodeInput,
        observed_monotonic_time: float,
    ) -> "HardRuleEvidence":
        return cls(
            status="UNKNOWN",
            availability="UNKNOWN",
            evidence_grade="NOT_CURRENTLY_AVAILABLE",
            source="DRIVECLARIFY_STAGE6A_NO_RULE_SIGNAL",
            source_kind="NO_INDEPENDENT_RULE_MONITOR",
            source_observation_id=episode.vision_observation.observation_id,
            source_frame_id=episode.vision_observation.frame_id,
            observed_monotonic_time=observed_monotonic_time,
            rule_critical_eligible=False,
            reason_codes=("NO_VERIFIED_HARD_RULE_EVIDENCE",),
        )


@dataclass(frozen=True)
class ExecutionBoundaryFacts:
    current_monotonic_time: float
    candidate_freshness: str
    candidate_invalidated: bool
    active_query: bool
    active_holding_lease: bool
    independent_safety_guard_active: bool
    baseline_available: bool
    simulation_runtime: str

    def __post_init__(self) -> None:
        _finite(self.current_monotonic_time, "EXECUTION_TIME")
        if self.candidate_freshness not in {"FRESH", "STALE", "UNKNOWN"}:
            raise Stage6AContractError("EXECUTION_FRESHNESS_INVALID")
        for field in (
            "candidate_invalidated",
            "active_query",
            "active_holding_lease",
            "independent_safety_guard_active",
            "baseline_available",
        ):
            if type(getattr(self, field)) is not bool:
                raise Stage6AContractError(field.upper() + "_MUST_BE_BOOLEAN")
        if self.simulation_runtime != "CARLA":
            raise Stage6AContractError("STAGE6A_AUTHORITY_IS_SIMULATION_ONLY")


class PlanProvider(Protocol):
    def __call__(
        self, episode: PolicyEpisodeInput, candidate: RuntimeCandidate
    ) -> CandidatePlan: ...


class ConsequenceEvaluator(Protocol):
    def __call__(
        self,
        episode: PolicyEpisodeInput,
        candidates: tuple[RuntimeCandidate, RuntimeCandidate],
        plans: tuple[CandidatePlan, CandidatePlan],
    ) -> ConsequenceEvaluation: ...


class PhysicalSafetyProvider(Protocol):
    def __call__(
        self,
        episode: PolicyEpisodeInput,
        candidates: tuple[RuntimeCandidate, RuntimeCandidate],
        plans: tuple[CandidatePlan, CandidatePlan],
        consequence: ConsequenceEvaluation,
        decision_monotonic_time: float,
    ) -> Mapping[str, Any] | None: ...


class HardRuleProvider(Protocol):
    def __call__(
        self,
        episode: PolicyEpisodeInput,
        candidates: tuple[RuntimeCandidate, RuntimeCandidate],
        plans: tuple[CandidatePlan, CandidatePlan],
        consequence: ConsequenceEvaluation,
        decision_monotonic_time: float,
    ) -> HardRuleEvidence | None: ...


RuntimeSignalProvider = Callable[
    [
        PolicyEpisodeInput,
        Tuple[RuntimeCandidate, RuntimeCandidate],
        Tuple[CandidatePlan, CandidatePlan],
        ConsequenceEvaluation,
        float,
    ],
    Optional[Mapping[str, Any]],
]


__all__ = [
    "CandidateGenerationAudit",
    "CandidateGenerationResult",
    "CandidatePlan",
    "ConsequenceEvaluation",
    "ConsequenceEvaluator",
    "EgoState",
    "ExecutionBoundaryFacts",
    "FORBIDDEN_RUNTIME_FIELDS",
    "HardRuleEvidence",
    "HardRuleProvider",
    "PhysicalSafetyProvider",
    "PlanProvider",
    "PolicyEpisodeInput",
    "RouteContext",
    "RuntimeCandidate",
    "RuntimeSignalProvider",
    "SCHEMA_VERSION",
    "Stage6AContractError",
    "VisionObservation",
    "VisualReference",
    "assert_no_evaluation_fields",
    "assert_unprivileged_source",
    "canonical_sha256",
]
