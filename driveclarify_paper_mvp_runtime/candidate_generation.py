"""Deterministic K=2 runtime candidate generation with no external I/O."""

from __future__ import annotations

from dataclasses import asdict

from .contracts import (
    CandidateGenerationAudit,
    CandidateGenerationResult,
    PolicyEpisodeInput,
    RuntimeCandidate,
    VisualReference,
    assert_no_evaluation_fields,
    canonical_sha256,
)


GENERATOR_ID = "DRIVECLARIFY_STAGE6A_VISUAL_REFERENCE_TEMPLATE_V1"
GENERATOR_RULE = "VISIBLE_REFERENCE_BINDING_V1"
MINIMUM_VISUAL_CONFIDENCE = 0.25
GENERATOR_CONTRACT_SHA256 = canonical_sha256(
    {
        "generator_id": GENERATOR_ID,
        "generator_rule": GENERATOR_RULE,
        "candidate_count": 2,
        "minimum_visual_confidence": MINIMUM_VISUAL_CONFIDENCE,
        "ordering": (
            "relative_bearing_degrees",
            "relative_distance_m",
            "category_casefold",
            "runtime_track_id",
        ),
        "inputs": (
            "raw_instruction",
            "vision_observation",
            "ego_state",
            "route_context",
            "opaque_token",
        ),
        "external_io": False,
        "evaluation_labels": False,
    }
)


def _bearing_phrase(reference: VisualReference) -> str:
    bearing = float(reference.relative_bearing_degrees)
    if bearing <= -8.0:
        return "to the left of the ego heading"
    if bearing >= 8.0:
        return "to the right of the ego heading"
    return "near the center of the ego heading"


def _distance_phrase(reference: VisualReference) -> str:
    distance = float(reference.relative_distance_m)
    if distance < 15.0:
        return "at near range"
    if distance < 35.0:
        return "at middle range"
    return "at far range"


def _prompt(instruction: str, reference: VisualReference) -> str:
    base = instruction.strip()
    if base[-1] not in ".!?":
        base += "."
    return (
        base
        + " Resolve the ambiguous visual reference as the visible "
        + reference.category.casefold()
        + " tracked as "
        + reference.track_id
        + ", "
        + _bearing_phrase(reference)
        + " and "
        + _distance_phrase(reference)
        + "."
    )


def _audit(
    episode: PolicyEpisodeInput,
    candidates: tuple[RuntimeCandidate, ...],
    *,
    status: str,
    reason_codes: tuple[str, ...],
) -> CandidateGenerationAudit:
    vision = episode.vision_observation.canonical_projection()
    return CandidateGenerationAudit(
        generator_id=GENERATOR_ID,
        generator_contract_sha256=GENERATOR_CONTRACT_SHA256,
        policy_input_sha256=episode.input_digest,
        instruction_sha256=canonical_sha256(episode.raw_instruction),
        vision_observation_sha256=canonical_sha256(vision),
        ego_state_sha256=canonical_sha256(asdict(episode.ego_state)),
        route_context_sha256=canonical_sha256(asdict(episode.route_context)),
        opaque_token_sha256=canonical_sha256(episode.opaque_token),
        candidate_ids=tuple(item.candidate_id for item in candidates),
        candidate_semantic_sha256=tuple(
            item.candidate_semantic_digest for item in candidates
        ),
        candidate_prompt_sha256=tuple(
            canonical_sha256(item.prompt_text) for item in candidates
        ),
        runtime_source_modalities=(
            "RAW_INSTRUCTION",
            "ONLINE_VISION_OBSERVATION",
            "ONLINE_EGO_STATE",
            "STANDARD_RUNTIME_ROUTE_CONTEXT",
        ),
        runtime_order_basis=(
            "RUNTIME_VISUAL_GEOMETRY_BEARING_DISTANCE_CATEGORY_TRACK_ID"
        ),
        catalog_read_count=0,
        evaluation_label_access_count=0,
        catalog_candidate_order_visible=False,
        runtime_annotation_overlap_checked=False,
        forbidden_field_overlap=(),
        status=status,
        reason_codes=reason_codes,
    )


class RuntimeCandidateGenerator:
    """Generate two source-bound prompts solely from the policy input object."""

    k = 2

    def generate(self, episode: PolicyEpisodeInput) -> CandidateGenerationResult:
        if not isinstance(episode, PolicyEpisodeInput):
            raise TypeError("POLICY_EPISODE_INPUT_CONTRACT_REQUIRED")
        assert_no_evaluation_fields(episode.public_projection())
        references = tuple(
            sorted(
                (
                    item
                    for item in episode.vision_observation.references
                    if float(item.confidence) >= MINIMUM_VISUAL_CONFIDENCE
                ),
                key=lambda item: item.sort_key,
            )
        )
        if len(references) < self.k:
            reasons = ("INSUFFICIENT_RUNTIME_VISUAL_REFERENCES",)
            audit = _audit(episode, (), status="UNKNOWN", reason_codes=reasons)
            return CandidateGenerationResult(
                status="UNKNOWN", candidates=(), audit=audit, reason_codes=reasons
            )

        input_digest = episode.input_digest
        candidates: list[RuntimeCandidate] = []
        for reference in references[: self.k]:
            anchor_digest = canonical_sha256(
                {
                    "vision_observation_sha256": canonical_sha256(
                        episode.vision_observation.canonical_projection()
                    ),
                    "reference": asdict(reference),
                }
            )
            semantic_digest = canonical_sha256(
                {
                    "generator_rule": GENERATOR_RULE,
                    "policy_input_sha256": input_digest,
                    "instruction_sha256": canonical_sha256(
                        episode.raw_instruction
                    ),
                    "visual_anchor_sha256": anchor_digest,
                    "ego_state_sha256": canonical_sha256(asdict(episode.ego_state)),
                    "route_context_sha256": canonical_sha256(
                        asdict(episode.route_context)
                    ),
                }
            )
            candidate_id = "rt-" + canonical_sha256(
                {
                    "opaque_token_sha256": canonical_sha256(episode.opaque_token),
                    "semantic_sha256": semantic_digest,
                }
            )[:24]
            interpretation_id = "meaning-" + semantic_digest[:24]
            prompt = _prompt(episode.raw_instruction, reference)
            candidate_input_digest = canonical_sha256(
                {
                    "policy_input_sha256": input_digest,
                    "candidate_id": candidate_id,
                    "prompt_sha256": canonical_sha256(prompt),
                    "generator_contract_sha256": GENERATOR_CONTRACT_SHA256,
                }
            )
            candidates.append(
                RuntimeCandidate(
                    candidate_id=candidate_id,
                    interpretation_id=interpretation_id,
                    prompt_text=prompt,
                    visual_track_id=reference.track_id,
                    visual_anchor_digest=anchor_digest,
                    candidate_semantic_digest=semantic_digest,
                    candidate_input_digest=candidate_input_digest,
                    source_observation_id=episode.vision_observation.observation_id,
                    source_frame_id=episode.vision_observation.frame_id,
                    generator_rule=GENERATOR_RULE,
                )
            )
        typed = (candidates[0], candidates[1])
        reasons = ("TWO_RUNTIME_VISUAL_CANDIDATES_GENERATED",)
        audit = _audit(episode, typed, status="READY", reason_codes=reasons)
        return CandidateGenerationResult(
            status="READY", candidates=typed, audit=audit, reason_codes=reasons
        )


__all__ = [
    "GENERATOR_CONTRACT_SHA256",
    "GENERATOR_ID",
    "GENERATOR_RULE",
    "MINIMUM_VISUAL_CONFIDENCE",
    "RuntimeCandidateGenerator",
]
