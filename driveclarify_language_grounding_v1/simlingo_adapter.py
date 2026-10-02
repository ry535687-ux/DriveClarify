"""Candidate-specific SimLingo prompt binding without internal object IDs."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import GroundedCandidate, canonical_sha256


ADAPTER_ID = "DRIVECLARIFY_LANGUAGE_GROUNDING_V1_SIMLINGO_PROMPT_ADAPTER_V1"


def forwarded_prompt(candidate: GroundedCandidate, speed_mps: float) -> str:
    return (
        "<INSTRUCTION_FOLLOWING> Current speed: "
        + "{:.1f}".format(float(speed_mps))
        + " m/s. "
        + candidate.prompt_text
        + " Predict the waypoints."
    )


def binding_receipt(candidate: GroundedCandidate, speed_mps: float) -> dict:
    prompt = forwarded_prompt(candidate, speed_mps)
    return {
        "adapter_id": ADAPTER_ID,
        "candidate_id": candidate.candidate_id,
        "interpretation_id": candidate.interpretation_id,
        "candidate_prompt": candidate.prompt_text,
        "candidate_prompt_sha256": candidate.prompt_sha256,
        "exact_simlingo_conditioning": prompt,
        "exact_simlingo_conditioning_sha256": canonical_sha256(prompt),
        "grounded_referent_id_retained_in_internal_receipt": candidate.grounded_referent_id,
        "internal_id_present_in_language_prompt": candidate.grounded_referent_id in prompt,
        "user_flag": 1,
        "route_context_policy": "UNCHANGED_SHARED_SIMLINGO_ROUTE_CONTEXT",
    }


def binding_set_receipt(candidates: Sequence[GroundedCandidate], speed_mps: float) -> dict:
    rows = [binding_receipt(candidate, speed_mps) for candidate in candidates]
    hashes = [row["exact_simlingo_conditioning_sha256"] for row in rows]
    return {
        "adapter_id": ADAPTER_ID,
        "bindings": rows,
        "conditioning_unique": len(hashes) == len(set(hashes)),
        "status": (
            "SIMLINGO_CONDITIONING_PRESERVES_CANDIDATE_IDENTITY"
            if len(hashes) == len(set(hashes))
            else "SIMLINGO_BINDING_COLLAPSE"
        ),
    }


__all__ = ["ADAPTER_ID", "binding_receipt", "binding_set_receipt", "forwarded_prompt"]

