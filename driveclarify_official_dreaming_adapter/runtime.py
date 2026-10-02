"""Default-off live runtimes for the staged alignment gates."""

from __future__ import annotations

from typing import Any

from driveclarify_simlingo_local_candidate_diagnostic.runtime import (
    LocalCandidateDiagnosticRuntime,
)

from .adapter import OfficialDreamingCandidateForwardProvider


FEATURE_FLAG = "DRIVECLARIFY_OFFICIAL_DREAMING_ADAPTER_ALIGNMENT"
MODE_ENV = "DRIVECLARIFY_OFFICIAL_DREAMING_ALIGNMENT_MODE"
EXPLICIT_RIGHT = "Move one lane towards the right."
EXPLICIT_STRAIGHT = "Continue driving on your current lane."


class OfficialAlignedExplicitRuntime(LocalCandidateDiagnosticRuntime):
    """Same-observation live gate using released-corpus instruction styles."""

    prompt_a = EXPLICIT_RIGHT
    prompt_b = EXPLICIT_STRAIGHT
    forward_provider_class = OfficialDreamingCandidateForwardProvider

    def __init__(self, agent: Any, output_dir: str) -> None:
        super().__init__(agent, output_dir)
        self._receipt.update(
            {
                "schema_version": "driveclarify.official_dreaming_adapter.explicit_live.v1",
                "status": "WAITING_FOR_OFFICIAL_ALIGNED_SAME_REAL_OBSERVATION",
                "feature_flag": FEATURE_FLAG,
                "feature_flag_default": "OFF",
                "adapter": "OfficialDreamingCandidateAdapter.v1",
                "language_provenance": {
                    "A": "released Eval_Dreamer corpus exact instruction",
                    "B": "released Eval_Dreamer corpus exact instruction",
                },
                "navigation_policy": "OMITTED_OFFICIAL_NO_NAVIGATION_BRANCH",
                "candidate_specific_target_point": False,
                "target_embedding_injected": False,
            }
        )
        self._persist()


__all__ = [
    "EXPLICIT_RIGHT",
    "EXPLICIT_STRAIGHT",
    "FEATURE_FLAG",
    "MODE_ENV",
    "OfficialAlignedExplicitRuntime",
]
