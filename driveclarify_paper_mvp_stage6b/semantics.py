"""Pre-trajectory candidate canonicalization and semantic uniqueness gates.

Candidate identity is never used as a proxy for interpretation identity.  The
canonical form is computed before any candidate-conditioned SimLingo forward,
so trajectory equivalence cannot collapse two semantically distinct meanings.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

from driveclarify_paper_mvp_runtime.candidate_generation import (
    GENERATOR_CONTRACT_SHA256,
    RuntimeCandidateGenerator,
)
from driveclarify_paper_mvp_runtime.contracts import (
    CandidateGenerationAudit,
    CandidateGenerationResult,
    PolicyEpisodeInput,
    RuntimeCandidate,
    VisualReference,
    canonical_sha256,
)

from .contracts import Stage6BContractError


SEMANTIC_GENERATOR_ID = "DRIVECLARIFY_STAGE6B_RUNTIME_SEMANTIC_CANDIDATES_V1"
SEMANTIC_GENERATOR_RULE = "RUNTIME_LANGUAGE_AND_GROUNDED_REFERENCE_CANONICALIZATION_V1"
CANONICAL_FIELDS = (
    "action",
    "maneuver",
    "referent",
    "referent_identity",
    "target_landmark",
    "target_branch",
    "ordering",
    "temporal_trigger",
    "spatial_relation",
    "constraint",
    "grounded_identity",
)


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _tokens(text: str) -> frozenset[str]:
    stop = {
        "a",
        "an",
        "and",
        "at",
        "do",
        "i",
        "is",
        "it",
        "mean",
        "of",
        "or",
        "please",
        "the",
        "to",
        "use",
    }
    return frozenset(token for token in _normalize(text).split() if token not in stop)


@dataclass(frozen=True)
class CanonicalInterpretation:
    candidate_id: str
    action: str
    maneuver: str
    referent: str | None
    referent_identity: str | None
    target_landmark: str | None
    target_branch: str | None
    ordering: str | None
    temporal_trigger: str | None
    spatial_relation: str | None
    constraint: str | None
    grounded_identity: str | None
    description: str

    def semantic_projection(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in CANONICAL_FIELDS}

    @property
    def semantic_sha256(self) -> str:
        return canonical_sha256(self.semantic_projection())

    @property
    def lexical_tokens(self) -> frozenset[str]:
        values = [self.description]
        values.extend(
            str(value)
            for value in self.semantic_projection().values()
            if value is not None
        )
        return _tokens(" ".join(values))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "semantic_sha256": self.semantic_sha256}


@dataclass(frozen=True)
class CandidateSetAudit:
    raw_k: int
    effective_k: int
    exact_duplicate: bool
    semantic_duplicate: bool
    grounding_duplicate: bool
    candidate_collapse: bool
    semantic_divergence: tuple[str, ...]
    action_divergence: bool
    trajectory_divergence: bool | None
    consequence_divergence: bool | None
    raw_candidate_ids: tuple[str, ...]
    effective_candidate_ids: tuple[str, ...]
    canonical_interpretations: tuple[CanonicalInterpretation, ...]
    gate_order: tuple[str, ...] = (
        "RAW_CANDIDATE_GENERATION",
        "SEMANTIC_CANONICALIZATION",
        "SEMANTIC_UNIQUENESS",
        "GROUNDED_REFERENT_UNIQUENESS",
        "EFFECTIVE_CANDIDATE_SET",
        "SIMLINGO_CANDIDATE_PLANNING",
        "CONSEQUENCE_COMPARISON",
    )

    def __post_init__(self) -> None:
        if self.raw_k != len(self.raw_candidate_ids):
            raise Stage6BContractError("RAW_K_ID_COUNT_MISMATCH")
        if self.effective_k != len(self.effective_candidate_ids):
            raise Stage6BContractError("EFFECTIVE_K_ID_COUNT_MISMATCH")
        if self.effective_k > self.raw_k:
            raise Stage6BContractError("EFFECTIVE_K_CANNOT_EXCEED_RAW_K")
        if self.candidate_collapse != (self.effective_k < self.raw_k):
            raise Stage6BContractError("CANDIDATE_COLLAPSE_FLAG_MISMATCH")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["canonical_interpretations"] = [
            item.to_dict() for item in self.canonical_interpretations
        ]
        if self.candidate_collapse and self.effective_k == 1:
            value["collapse_status"] = (
                "CANDIDATE_SET_COLLAPSED_TO_SINGLE_INTERPRETATION"
            )
        else:
            value["collapse_status"] = "NO_CANDIDATE_COLLAPSE"
        return value


@dataclass(frozen=True)
class SemanticCandidateSet:
    raw_generation: CandidateGenerationResult
    candidates: tuple[RuntimeCandidate, ...]
    canonical: tuple[CanonicalInterpretation, ...]
    audit: CandidateSetAudit

    def __post_init__(self) -> None:
        if len(self.candidates) != self.audit.effective_k:
            raise Stage6BContractError("EFFECTIVE_CANDIDATE_OBJECT_COUNT_MISMATCH")
        if tuple(item.candidate_id for item in self.candidates) != (
            self.audit.effective_candidate_ids
        ):
            raise Stage6BContractError("EFFECTIVE_CANDIDATE_IDENTITY_MISMATCH")


class SemanticCandidatePipeline:
    """Build K<=2 meanings from runtime language, scene references and route cues."""

    def __init__(self, raw_generator: RuntimeCandidateGenerator | None = None) -> None:
        self.raw_generator = raw_generator or RuntimeCandidateGenerator()

    def generate(self, episode: PolicyEpisodeInput) -> SemanticCandidateSet:
        raw = self.raw_generator.generate(episode)
        if raw.status != "READY" or len(raw.candidates) != 2:
            audit = CandidateSetAudit(
                raw_k=len(raw.candidates),
                effective_k=0,
                exact_duplicate=False,
                semantic_duplicate=False,
                grounding_duplicate=False,
                candidate_collapse=False,
                semantic_divergence=(),
                action_divergence=False,
                trajectory_divergence=None,
                consequence_divergence=None,
                raw_candidate_ids=tuple(item.candidate_id for item in raw.candidates),
                effective_candidate_ids=(),
                canonical_interpretations=(),
            )
            return SemanticCandidateSet(raw, (), (), audit)

        forms = self._canonical_forms(episode)
        candidates = tuple(
            self._runtime_candidate(episode, form, index)
            for index, form in enumerate(forms)
        )
        forms = tuple(
            replace(form, candidate_id=candidate.candidate_id)
            for form, candidate in zip(forms, candidates)
        )
        # Rebind semantic digests after the final candidate-independent forms are
        # known.  Candidate IDs are intentionally excluded from each digest.
        candidates = tuple(
            replace(
                candidate,
                candidate_semantic_digest=form.semantic_sha256,
                candidate_input_digest=canonical_sha256(
                    {
                        "policy_input_sha256": episode.input_digest,
                        "candidate_id": candidate.candidate_id,
                        "prompt_sha256": canonical_sha256(candidate.prompt_text),
                        "semantic_sha256": form.semantic_sha256,
                    }
                ),
            )
            for candidate, form in zip(candidates, forms)
        )
        exact_duplicate = len({_normalize(item.prompt_text) for item in candidates}) < len(candidates)
        semantic_duplicate = len({item.semantic_sha256 for item in forms}) < len(forms)
        groundings = [item.grounded_identity for item in forms]
        grounding_duplicate = bool(
            len(groundings) >= 2
            and all(value is not None for value in groundings)
            and len(set(groundings)) < len(groundings)
        )
        seen: set[str] = set()
        effective_candidates: list[RuntimeCandidate] = []
        effective_forms: list[CanonicalInterpretation] = []
        for candidate, form in zip(candidates, forms):
            if form.semantic_sha256 in seen:
                continue
            seen.add(form.semantic_sha256)
            effective_candidates.append(candidate)
            effective_forms.append(form)
        divergences = _divergent_fields(forms)
        audit = CandidateSetAudit(
            raw_k=len(candidates),
            effective_k=len(effective_candidates),
            exact_duplicate=exact_duplicate,
            semantic_duplicate=semantic_duplicate,
            grounding_duplicate=grounding_duplicate,
            candidate_collapse=len(effective_candidates) < len(candidates),
            semantic_divergence=divergences,
            action_divergence=bool(
                any(field in divergences for field in ("action", "maneuver"))
            ),
            trajectory_divergence=None,
            consequence_divergence=None,
            raw_candidate_ids=tuple(item.candidate_id for item in candidates),
            effective_candidate_ids=tuple(
                item.candidate_id for item in effective_candidates
            ),
            canonical_interpretations=forms,
        )
        return SemanticCandidateSet(
            raw_generation=_semantic_generation_audit(episode, raw, candidates),
            candidates=tuple(effective_candidates),
            canonical=tuple(effective_forms),
            audit=audit,
        )

    def _canonical_forms(
        self, episode: PolicyEpisodeInput
    ) -> tuple[CanonicalInterpretation, CanonicalInterpretation]:
        raw = _normalize(episode.raw_instruction)
        references = tuple(
            sorted(
                episode.vision_observation.references,
                key=lambda item: (
                    round(float(item.relative_distance_m), 6),
                    round(abs(float(item.relative_bearing_degrees)), 6),
                    item.track_id,
                ),
            )
        )
        if len(references) < 2:
            raise Stage6BContractError("SEMANTIC_PIPELINE_REQUIRES_TWO_REFERENCES")
        if "lane on the left" in raw or "lane on the right" in raw:
            side = "LEFT" if "left" in raw else "RIGHT"
            return (
                _form(
                    action="CHANGE_LANE",
                    maneuver="LANE_CHANGE_" + side,
                    target_branch="ADJACENT_" + side + "_THROUGH_LANE",
                    spatial_relation=side,
                    grounded_identity="route:adjacent_" + side.casefold() + "_lane",
                    description=("the adjacent " + side.casefold() + " through-lane"),
                ),
                _form(
                    action="TAKE_BRANCH",
                    maneuver="TURN_" + side,
                    target_branch=side + "_FORK",
                    spatial_relation=side,
                    grounded_identity="route:" + side.casefold() + "_fork",
                    description="the geometric " + side.casefold() + " fork",
                ),
            )
        if "bus clears" in raw:
            bus = next(
                (item for item in references if "bus" in item.category.casefold()),
                references[0],
            )
            referent = _reference_name(bus)
            return (
                _form(
                    action="TURN",
                    maneuver="TURN_INNER_CORRIDOR",
                    referent=referent,
                    referent_identity=bus.track_id,
                    target_branch="INNER_TURN_CORRIDOR",
                    temporal_trigger="BUS_NOSE_CLEAR",
                    grounded_identity=bus.track_id,
                    description="the bus nose clearing and the inner turn corridor",
                ),
                _form(
                    action="TURN",
                    maneuver="TURN_OUTER_CORRIDOR",
                    referent=referent,
                    referent_identity=bus.track_id,
                    target_branch="OUTER_TURN_CORRIDOR",
                    temporal_trigger="BUS_TAIL_FULLY_CLEAR",
                    grounded_identity=bus.track_id,
                    description="the bus tail fully clearing and the outer turn corridor",
                ),
            )
        if "pull over" in raw:
            return (
                _form(
                    action="PULL_OVER",
                    maneuver="PULL_OVER",
                    target_landmark="NEAREST_LEGAL_PULL_OVER_ZONE",
                    ordering="NEAREST",
                    constraint="LEGAL_AND_CURRENTLY_SAFE",
                    grounded_identity="route:nearest_legal_pull_over_zone",
                    description="the nearest currently legal pull-over zone",
                ),
                _form(
                    action="PULL_OVER",
                    maneuver="PULL_OVER",
                    target_landmark="NEXT_LEGAL_PULL_OVER_ZONE",
                    ordering="NEXT",
                    constraint="LEGAL_WITH_MORE_SPACE",
                    grounded_identity="route:next_legal_pull_over_zone",
                    description="the next legal pull-over zone with more space",
                ),
            )
        if "second opening" in raw:
            return tuple(  # type: ignore[return-value]
                _reference_form(
                    reference,
                    action="TAKE_OPENING",
                    maneuver="TURN_AT_SECOND_OPENING",
                    ordering="SECOND_AFTER_REFERENCE",
                    temporal_trigger="AFTER_REFERENCE",
                )
                for reference in references[:2]
            )
        if "suitable distance" in raw or "enough clearance" in raw:
            constraint_name = (
                "CONSERVATIVE_GAP" if "distance" in raw else "WIDE_CLEARANCE"
            )
            alternate = "MODERATE_GAP" if "distance" in raw else "NARROW_CLEARANCE"
            return (
                _reference_form(
                    references[0],
                    action="FOLLOW" if "distance" in raw else "USE_PASSAGE",
                    maneuver="MAINTAIN_PATH",
                    constraint=constraint_name,
                ),
                _reference_form(
                    references[1],
                    action="FOLLOW" if "distance" in raw else "USE_PASSAGE",
                    maneuver="MAINTAIN_PATH",
                    constraint=alternate,
                ),
            )
        action, maneuver = _instruction_action(raw)
        return (
            _reference_form(
                references[0],
                action=action,
                maneuver=maneuver,
                ordering="NEARER",
                temporal_trigger=("AFTER_REFERENCE" if "after" in raw or "past" in raw else None),
            ),
            _reference_form(
                references[1],
                action=action,
                maneuver=maneuver,
                ordering="FARTHER",
                temporal_trigger=("AFTER_REFERENCE" if "after" in raw or "past" in raw else None),
            ),
        )

    @staticmethod
    def _runtime_candidate(
        episode: PolicyEpisodeInput,
        form: CanonicalInterpretation,
        index: int,
    ) -> RuntimeCandidate:
        references = tuple(
            sorted(
                episode.vision_observation.references,
                key=lambda item: (
                    round(float(item.relative_distance_m), 6),
                    item.track_id,
                ),
            )
        )
        reference = _reference_for_form(form, references, index)
        semantic = form.semantic_sha256
        candidate_id = "r0-" + canonical_sha256(
            {
                "opaque": episode.opaque_token,
                "semantic": semantic,
                "index": index,
            }
        )[:24]
        description = form.description.rstrip(".")
        prompt = episode.raw_instruction.strip().rstrip(".?!") + ". Interpret this as " + description + "."
        anchor = canonical_sha256(asdict(reference))
        return RuntimeCandidate(
            candidate_id=candidate_id,
            interpretation_id="meaning-" + semantic[:24],
            prompt_text=prompt,
            visual_track_id=reference.track_id,
            visual_anchor_digest=anchor,
            candidate_semantic_digest=semantic,
            candidate_input_digest=canonical_sha256(
                {
                    "policy_input_sha256": episode.input_digest,
                    "candidate_id": candidate_id,
                    "prompt_sha256": canonical_sha256(prompt),
                    "generator_contract_sha256": GENERATOR_CONTRACT_SHA256,
                }
            ),
            source_observation_id=episode.vision_observation.observation_id,
            source_frame_id=episode.vision_observation.frame_id,
            generator_rule=SEMANTIC_GENERATOR_RULE,
        )


def _form(**values: Any) -> CanonicalInterpretation:
    payload = {field: None for field in CANONICAL_FIELDS}
    payload.update(values)
    if payload["action"] is None or payload["maneuver"] is None:
        raise Stage6BContractError("CANONICAL_ACTION_AND_MANEUVER_REQUIRED")
    description = values.get("description")
    if not isinstance(description, str) or not description.strip():
        raise Stage6BContractError("CANONICAL_DESCRIPTION_REQUIRED")
    return CanonicalInterpretation(
        candidate_id="pending",
        description=description,
        **{field: payload[field] for field in CANONICAL_FIELDS},
    )


def _reference_form(
    reference: VisualReference,
    *,
    action: str,
    maneuver: str,
    ordering: str | None = None,
    temporal_trigger: str | None = None,
    constraint: str | None = None,
) -> CanonicalInterpretation:
    name = _reference_name(reference)
    relation = _bearing_relation(reference)
    distance = _distance_relation(reference)
    description = "the " + distance.casefold() + " " + name
    return _form(
        action=action,
        maneuver=maneuver,
        referent=name,
        referent_identity=reference.track_id,
        ordering=ordering,
        temporal_trigger=temporal_trigger,
        spatial_relation=relation,
        constraint=constraint,
        grounded_identity=reference.track_id,
        description=description,
    )


def _reference_name(reference: VisualReference) -> str:
    return _normalize(reference.category).replace(" ", "_").upper() or "RUNTIME_ACTOR"


def _bearing_relation(reference: VisualReference) -> str:
    bearing = float(reference.relative_bearing_degrees)
    return "LEFT" if bearing < -8.0 else "RIGHT" if bearing > 8.0 else "CENTER"


def _distance_relation(reference: VisualReference) -> str:
    distance = float(reference.relative_distance_m)
    return "NEAR" if distance < 15.0 else "MIDDLE" if distance < 35.0 else "FAR"


def _reference_for_form(
    form: CanonicalInterpretation,
    references: Sequence[VisualReference],
    index: int,
) -> VisualReference:
    if form.referent_identity is not None:
        matched = next(
            (item for item in references if item.track_id == form.referent_identity),
            None,
        )
        if matched is not None:
            return matched
    return references[min(index, len(references) - 1)]


def _instruction_action(normalized: str) -> tuple[str, str]:
    if "stop" in normalized:
        return "STOP_AT_TARGET", "STOP"
    if "pass" in normalized:
        return "PASS_THEN_TURN", "PASS_AND_TURN"
    if "turn" in normalized:
        return "TURN", "TURN"
    if "road" in normalized or "branch" in normalized:
        return "TAKE_BRANCH", "BRANCH_SELECTION"
    return "FOLLOW_INSTRUCTION", "ROUTE_PROGRESS"


def _divergent_fields(
    forms: Sequence[CanonicalInterpretation],
) -> tuple[str, ...]:
    if len(forms) < 2:
        return ()
    return tuple(
        field
        for field in CANONICAL_FIELDS
        if len({getattr(item, field) for item in forms}) > 1
    )


def _semantic_generation_audit(
    episode: PolicyEpisodeInput,
    raw: CandidateGenerationResult,
    candidates: tuple[RuntimeCandidate, ...],
) -> CandidateGenerationResult:
    old = raw.audit
    audit = CandidateGenerationAudit(
        generator_id=SEMANTIC_GENERATOR_ID,
        generator_contract_sha256=canonical_sha256(
            {
                "raw_generator_contract_sha256": GENERATOR_CONTRACT_SHA256,
                "rule": SEMANTIC_GENERATOR_RULE,
                "canonical_fields": CANONICAL_FIELDS,
                "pre_trajectory": True,
            }
        ),
        policy_input_sha256=episode.input_digest,
        instruction_sha256=old.instruction_sha256,
        vision_observation_sha256=old.vision_observation_sha256,
        ego_state_sha256=old.ego_state_sha256,
        route_context_sha256=old.route_context_sha256,
        opaque_token_sha256=old.opaque_token_sha256,
        candidate_ids=tuple(item.candidate_id for item in candidates),
        candidate_semantic_sha256=tuple(
            item.candidate_semantic_digest for item in candidates
        ),
        candidate_prompt_sha256=tuple(
            canonical_sha256(item.prompt_text) for item in candidates
        ),
        runtime_source_modalities=old.runtime_source_modalities,
        runtime_order_basis=(
            "RUNTIME_LANGUAGE_CUES_THEN_DISTANCE_BEARING_TRACK_ID"
        ),
        catalog_read_count=0,
        evaluation_label_access_count=0,
        catalog_candidate_order_visible=False,
        runtime_annotation_overlap_checked=False,
        forbidden_field_overlap=(),
        status="READY",
        reason_codes=(
            "TWO_RAW_RUNTIME_CANDIDATES_CANONICALIZED_BEFORE_PLANNING",
        ),
    )
    return CandidateGenerationResult(
        status="READY",
        candidates=candidates,
        audit=audit,
        reason_codes=audit.reason_codes,
    )


def resolve_natural_language_selection(
    text: str,
    candidates: Sequence[CanonicalInterpretation],
) -> str | None:
    """Resolve answer/information text semantically; never accept an index label."""

    normalized = _normalize(text)
    forbidden = (
        r"\bcandidate\s+[ab12]\b",
        r"\bchoose\s+[ab12]\b",
        r"\bact\s+now\b",
        r"\bask\s+was\s+correct\b",
    )
    if any(re.search(pattern, normalized) for pattern in forbidden):
        return None
    answer_tokens = _tokens(text)
    scores: list[tuple[float, CanonicalInterpretation]] = []
    for candidate in candidates:
        overlap = len(answer_tokens.intersection(candidate.lexical_tokens))
        union = len(answer_tokens.union(candidate.lexical_tokens)) or 1
        score = overlap / union
        projection = candidate.semantic_projection()
        marker_values = {
            "near": {"NEAR", "NEARER", "NEAREST"},
            "far": {"FAR", "FARTHER"},
            "adjacent": {"ADJACENT_LEFT_THROUGH_LANE", "ADJACENT_RIGHT_THROUGH_LANE"},
            "fork": {"LEFT_FORK", "RIGHT_FORK"},
            "nose": {"BUS_NOSE_CLEAR", "INNER_TURN_CORRIDOR"},
            "inner": {"BUS_NOSE_CLEAR", "INNER_TURN_CORRIDOR"},
            "tail": {"BUS_TAIL_FULLY_CLEAR", "OUTER_TURN_CORRIDOR"},
            "outer": {"BUS_TAIL_FULLY_CLEAR", "OUTER_TURN_CORRIDOR"},
            "nearest": {"NEAREST", "NEAREST_LEGAL_PULL_OVER_ZONE"},
            "next": {"NEXT", "NEXT_LEGAL_PULL_OVER_ZONE"},
        }
        values = {str(value) for value in projection.values() if value is not None}
        for marker, expected in marker_values.items():
            if marker in answer_tokens and values.intersection(expected):
                score += 1.0
        scores.append((score, candidate))
    if not scores:
        return None
    best = max(score for score, _ in scores)
    winners = [candidate for score, candidate in scores if score == best]
    if best <= 0.0 or len(winners) != 1:
        return None
    return winners[0].candidate_id


def update_post_plan_divergence(
    audit: CandidateSetAudit,
    *,
    trajectory_divergence: bool | None,
    consequence_divergence: bool | None,
) -> CandidateSetAudit:
    """Attach downstream evidence without changing the pre-trajectory effective set."""

    return replace(
        audit,
        trajectory_divergence=trajectory_divergence,
        consequence_divergence=consequence_divergence,
    )


__all__ = [
    "CANONICAL_FIELDS",
    "CandidateSetAudit",
    "CanonicalInterpretation",
    "SEMANTIC_GENERATOR_ID",
    "SEMANTIC_GENERATOR_RULE",
    "SemanticCandidatePipeline",
    "SemanticCandidateSet",
    "resolve_natural_language_selection",
    "update_post_plan_divergence",
]
