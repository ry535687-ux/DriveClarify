"""Lightweight ambiguity gate that executes before candidate generation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Tuple

from driveclarify_language_grounding_v1.contracts import AmbiguityKind
from driveclarify_language_grounding_v1.slot_parser import SemanticSlotParser

from .contracts import (
    GateDecision,
    GateInput,
    GateResult,
    canonical_sha256,
)


FRESH_EVIDENCE_MAX_AGE_FRAMES = 40
SUPPORTED_AMBIGUITY_KINDS = frozenset(
    {
        AmbiguityKind.REFERENTIAL,
        AmbiguityKind.LANDMARK,
        AmbiguityKind.SPATIAL_ORDER,
        AmbiguityKind.UNDERSPECIFIED_CONSTRAINT,
    }
)


class AmbiguityGate:
    """Decide only whether multiple reasonable readings currently exist.

    The gate never selects a reading, generates a route, or decides ASK.
    UNKNOWN preserves its own outcome and does not authorize candidates.
    """

    def __init__(self, parser=None, max_age_frames=FRESH_EVIDENCE_MAX_AGE_FRAMES):
        self._parser = parser or SemanticSlotParser()
        self._max_age_frames = int(max_age_frames)
        if self._max_age_frames < 0:
            raise ValueError("GATE_MAX_AGE_NEGATIVE")

    def evaluate(self, value: GateInput) -> GateResult:
        if not isinstance(value, GateInput):
            raise TypeError("GATE_INPUT_REQUIRED")
        parsed = self._parser.parse(value.instruction)
        grounding = value.grounding
        age = grounding.current_frame - grounding.observation_frame
        evidence_hash = canonical_sha256(
            {
                "instruction": value.instruction,
                "parsed": parsed.to_dict(),
                "grounding": asdict(grounding),
                "runtime_evidence": value.runtime_evidence,
            }
        )

        if age < 0:
            return self._result(
                GateDecision.UNKNOWN,
                parsed.ambiguity_kind.value,
                ("GROUNDING_FROM_FUTURE_FRAME",),
                evidence_hash,
            )
        if age > self._max_age_frames:
            return self._result(
                GateDecision.UNKNOWN,
                parsed.ambiguity_kind.value,
                ("GROUNDING_EVIDENCE_STALE",),
                evidence_hash,
            )
        if parsed.ambiguity_kind is AmbiguityKind.UNSUPPORTED:
            return self._result(
                GateDecision.UNKNOWN,
                parsed.ambiguity_kind.value,
                tuple(parsed.reason_codes) + ("UNSUPPORTED_SEMANTICS",),
                evidence_hash,
            )
        if parsed.ambiguity_kind is AmbiguityKind.TEMPORAL:
            return self._result(
                GateDecision.UNKNOWN,
                parsed.ambiguity_kind.value,
                ("TEMPORAL_FAMILY_OUTSIDE_V10_MAIN_METHOD",),
                evidence_hash,
            )
        if parsed.ambiguity_kind is AmbiguityKind.UNAMBIGUOUS:
            return self._result(
                GateDecision.CLEAR,
                parsed.ambiguity_kind.value,
                ("NO_SUPPORTED_AMBIGUITY_SLOT",),
                evidence_hash,
            )

        if parsed.ambiguity_kind not in SUPPORTED_AMBIGUITY_KINDS:
            return self._result(
                GateDecision.UNKNOWN,
                parsed.ambiguity_kind.value,
                ("SEMANTIC_KIND_NOT_ADMITTED",),
                evidence_hash,
            )
        if grounding.evidence_status not in {"VERIFIED", "AVAILABLE_VERIFIED"}:
            return self._result(
                GateDecision.UNKNOWN,
                parsed.ambiguity_kind.value,
                ("GROUNDING_EVIDENCE_NOT_VERIFIED",) + tuple(grounding.reason_codes),
                evidence_hash,
            )

        count = grounding.reasonable_interpretation_count
        if count is None or count == 0:
            return self._result(
                GateDecision.UNKNOWN,
                parsed.ambiguity_kind.value,
                ("REASONABLE_INTERPRETATION_CARDINALITY_UNKNOWN",)
                + tuple(grounding.reason_codes),
                evidence_hash,
            )
        if count == 1:
            return self._result(
                GateDecision.CLEAR,
                parsed.ambiguity_kind.value,
                ("SINGLE_REASONABLE_INTERPRETATION_VERIFIED",),
                evidence_hash,
            )
        return self._result(
            GateDecision.AMBIGUOUS,
            parsed.ambiguity_kind.value,
            ("MULTIPLE_REASONABLE_INTERPRETATIONS_VERIFIED",),
            evidence_hash,
        )

    @staticmethod
    def _result(
        decision: GateDecision,
        semantic_kind: str,
        reasons: Tuple[str, ...],
        evidence_hash: str,
    ) -> GateResult:
        return GateResult(
            decision=decision,
            semantic_kind=semantic_kind,
            reason_codes=tuple(reasons),
            evidence_sha256=evidence_hash,
            candidate_pipeline_authorized=decision is GateDecision.AMBIGUOUS,
        )


__all__ = [
    "AmbiguityGate",
    "FRESH_EVIDENCE_MAX_AGE_FRAMES",
    "SUPPORTED_AMBIGUITY_KINDS",
]
