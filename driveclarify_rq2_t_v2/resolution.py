"""Prospective observation-resolvability classification for V2 scenes."""

from __future__ import annotations

from typing import Any, Mapping


def classify_observation_resolvability(scene_contract: Mapping[str, Any]) -> str:
    """Classify from the frozen ambiguity mechanism, never from policy success."""

    family = str(scene_contract.get("ambiguity_family") or "")
    missing = tuple(str(value) for value in scene_contract.get("missing_information", ()))
    reveal = scene_contract.get("deployable_reveal_mechanism")
    if family == "UNDERSPECIFIED_CONSTRAINT":
        if reveal:
            raise ValueError("UNDERSPECIFIED_CONTROL_MUST_NOT_AUTHOR_LINGUISTIC_REVEAL")
        if not missing:
            raise ValueError("UNDERSPECIFIED_CONTROL_MISSING_INFORMATION_REQUIRED")
        return "INTRINSICALLY_LINGUISTIC_CLARIFICATION_REQUIRED"
    if family not in {"REFERENTIAL", "LANDMARK", "ORDER"}:
        raise ValueError("RQ2_T_V2_UNKNOWN_AMBIGUITY_FAMILY")
    if not isinstance(reveal, Mapping):
        return "OBSERVATION_UNRESOLVED_NONREVEAL_BOUNDARY_CASE"
    if reveal.get("runtime_observable_without_future_truth") is not True:
        raise ValueError("REVEAL_MUST_BE_RUNTIME_OBSERVABLE_WITHOUT_FUTURE_TRUTH")
    return "OBSERVATION_RESOLVABLE"


__all__ = ["classify_observation_resolvability"]
