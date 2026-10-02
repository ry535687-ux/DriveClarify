"""End-to-end CPU-only replay of recorded candidate evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from .consequence import ConsequenceEvaluator
from .gate import CandidateStabilityGate, GateConfig
from .runner import FixtureInterpretationGenerator, RecordedCandidateRunner
from .shadow_logger import ShadowLogger
from .state_machine import OfflineDecisionStateMachine
from .types import DeterministicSettings


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _context_dict(context: Any) -> Dict[str, Any]:
    settings = context.deterministic_settings
    return {
        "run_id": context.run_id,
        "observation_id": context.observation_id,
        "observation_digest": context.observation_digest,
        "source_frame": context.source_frame,
        "closed_loop_state_identity": context.closed_loop_state_identity,
        "ego_state_identity": context.ego_state_identity,
        "navigation_identity": context.navigation_identity,
        "model_instance_identity": context.model_instance_identity,
        "preprocessing_identity": context.preprocessing_identity,
        "initial_rng_state_identities": context.initial_rng_state_identities,
        "cache_history_state_identity": context.cache_history_state_identity,
        "freshness_token": context.freshness_token,
        "deterministic_settings": {
            "model_eval_required": settings.model_eval_required,
            "model_eval_observed": settings.model_eval_observed,
            "inference_only_required": settings.inference_only_required,
            "inference_only_observed": settings.inference_only_observed,
            "restore_rng_before_each_repetition_required": (
                settings.restore_rng_before_each_repetition_required
            ),
            "rng_states_recorded": settings.rng_states_recorded,
            "repetition_rng_state_equal": settings.repetition_rng_state_equal,
            "isolate_mutable_cache_history_required": (
                settings.isolate_mutable_cache_history_required
            ),
            "mutable_cache_history_isolated": (
                settings.mutable_cache_history_isolated
            ),
            "decoding_mode": settings.decoding_mode,
            "provenance": list(settings.provenance),
        },
        "provenance": list(context.provenance),
    }


def _candidate_summary(candidate: Any) -> Dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "interpretation_id": candidate.interpretation_id,
        "repetition_index": candidate.repetition_index,
        "observation_id": candidate.observation_id,
        "observation_digest": candidate.observation_digest,
        "source_frame": candidate.source_frame,
        "freshness_token": candidate.freshness_token,
        "model_instance_identity": candidate.model_instance_identity,
        "route_hash": candidate.route_hash,
        "speed_hash": candidate.speed_hash,
        "route_shape": [1, len(candidate.route), 2],
        "speed_shape": [1, len(candidate.speed), 2],
        "normalized_dtype": candidate.normalized_dtype,
        "normalized_device": candidate.normalized_device,
        "language_output": list(candidate.language_output),
        "language_token_ids": (
            list(candidate.language_token_ids)
            if candidate.language_token_ids is not None
            else None
        ),
        "latency_seconds": candidate.latency_seconds,
        "valid": candidate.valid,
    }


def run_recorded_replay(
    source: Path,
    *,
    gate_config: GateConfig = GateConfig(),
) -> Dict[str, Any]:
    deterministic_settings = DeterministicSettings(
        model_eval_observed=False,
        inference_only_observed=True,
        rng_states_recorded=False,
        repetition_rng_state_equal=None,
        mutable_cache_history_isolated=None,
        decoding_mode="GREEDY_ARGMAX_TEMPERATURE_0",
        provenance=(
            "Phase0A agent _forward_one uses torch.inference_mode()",
            "Phase0A setup and SimLingo agent contain no model.eval() call",
            "psplan13 payload did not persist Python/NumPy/Torch RNG states",
            "SimLingo greedy_sample defaults temperature=0 and takes argmax",
        ),
    )
    runner = RecordedCandidateRunner(Path(source), deterministic_settings)
    interpretations = {
        "A": str(runner.payload["prompts"]["A"]),
        "B": str(runner.payload["prompts"]["B"]),
    }
    generator = FixtureInterpretationGenerator(interpretations)
    requests = generator.generate()
    context = runner.context()
    candidates = runner.run(requests)
    gate = CandidateStabilityGate(gate_config).evaluate(context, candidates)
    consequence = ConsequenceEvaluator().evaluate_diagnostics(context, gate)
    decision = OfflineDecisionStateMachine().reduce(gate, consequence)

    classifications = {
        gate.route_classification,
        gate.speed_classification,
    }
    noise_dominated = any(
        item is not None and item.value == "NOISE_DOMINATED"
        for item in classifications
    )
    if noise_dominated:
        terminal = "BLOCKED_CANDIDATE_SIGNAL_NOT_IDENTIFIABLE_OVER_REPEAT_NOISE"
    elif gate.candidate_mechanism_supported:
        terminal = "RECORDED_CANDIDATE_MECHANISM_SUPPORTED"
    else:
        terminal = "RECORDED_CANDIDATE_EVIDENCE_INSUFFICIENT"

    candidate_summaries = [_candidate_summary(candidate) for candidate in candidates]
    context_payload = _context_dict(context)
    gate_payload = gate.to_dict()
    shadow_entry = ShadowLogger.entry(
        context=context_payload,
        candidates=candidate_summaries,
        stability=gate_payload,
        consequence=consequence,
        decision=decision,
    )
    return {
        "result_type": "DRIVECLARIFY_PSPLAN13_OFFLINE_CANDIDATE_REPLAY_V1",
        "source": {
            "path": str(Path(source).resolve()),
            "sha256": _file_sha256(Path(source)),
            "kind": runner.source_kind,
            "run_id": context.run_id,
            "candidate_evidence_sha256_recorded": runner.evidence_hash,
            "candidate_evidence_sha256_recomputed": (
                runner.evidence_hash_recomputed
            ),
            "candidate_evidence_hash_matches": (
                runner.evidence_hash == runner.evidence_hash_recomputed
            ),
        },
        "offline_only": True,
        "fixture_interpretation_count": 2,
        "fixture_interpretations": interpretations,
        "candidate_requests": [
            {
                "candidate_id": request.candidate_id,
                "interpretation": request.interpretation,
                "expected_repetition_group": request.expected_repetition_group,
            }
            for request in requests
        ],
        "context": context_payload,
        "candidates": candidate_summaries,
        "candidate_stability_metrics": gate.metrics,
        "stability_gate": gate_payload,
        "consequence_evaluator": consequence,
        "state_machine": decision,
        "shadow_log_entry": shadow_entry,
        "candidate_mechanism_supported": gate.candidate_mechanism_supported,
        "decision_evaluation_entered": decision["entered_decision_evaluation"],
        "real_act_ask_wait_authorized": False,
        "physical_control_authorized": False,
        "side_effect_counts": dict(runner.side_effect_counts),
        "simlingo_modified": False,
        "carla_started": False,
        "evaluator_started": False,
        "model_started": False,
        "cuda_used": False,
        "phase0a_started": False,
        "phase0b_started": False,
        "training_started": False,
        "automatic_continuation": False,
        "terminal_state": terminal,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            run_recorded_replay(args.source),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
