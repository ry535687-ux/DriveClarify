import pytest

from driveclarify.native.ambiguity_gate import AmbiguityGate
from driveclarify.native.contracts import (
    ContractViolation,
    GateDecision,
    GateInput,
    GroundingEvidence,
)


def evidence(count, *, status="VERIFIED", observation_frame=100, current_frame=100):
    return GroundingEvidence(
        reasonable_interpretation_count=count,
        evidence_status=status,
        source="CURRENT_SCENE_GROUNDING",
        observation_frame=observation_frame,
        current_frame=current_frame,
    )


@pytest.mark.parametrize(
    "instruction",
    (
        "Turn after the white van.",
        "Turn at the school.",
        "Take the second turn.",
        "Pull over at a suitable distance.",
    ),
)
def test_supported_semantic_families_need_scene_cardinality(instruction):
    gate = AmbiguityGate()
    assert gate.evaluate(GateInput(instruction, evidence(1))).decision is GateDecision.CLEAR
    assert gate.evaluate(GateInput(instruction, evidence(2))).decision is GateDecision.AMBIGUOUS


def test_unambiguous_instruction_is_clear_without_candidate_cardinality():
    result = AmbiguityGate().evaluate(
        GateInput("Continue straight.", evidence(None, status="UNKNOWN"))
    )
    assert result.decision is GateDecision.CLEAR
    assert result.candidate_pipeline_authorized is False


def test_unknown_is_preserved_for_stale_unverified_or_missing_evidence():
    gate = AmbiguityGate()
    values = (
        evidence(2, observation_frame=1, current_frame=100),
        evidence(2, status="UNKNOWN"),
        evidence(None),
    )
    for value in values:
        result = gate.evaluate(GateInput("Turn after the white van.", value))
        assert result.decision is GateDecision.UNKNOWN
        assert result.candidate_pipeline_authorized is False


def test_lane_and_timing_are_not_silently_added_to_main_method():
    result = AmbiguityGate().evaluate(
        GateInput("Stop after the van clears.", evidence(2))
    )
    assert result.decision is GateDecision.UNKNOWN
    assert "TEMPORAL_FAMILY_OUTSIDE_V11_MAIN_METHOD" in result.reason_codes


@pytest.mark.parametrize(
    "forbidden",
    ("case_id", "family", "expected_label", "true_intent", "oracle_answer"),
)
def test_runtime_scientific_label_firewall(forbidden):
    with pytest.raises(ContractViolation, match="RUNTIME_LABEL_FIREWALL"):
        GateInput(
            "Continue straight.",
            evidence(None),
            runtime_evidence={"nested": {forbidden: "leak"}},
        )
