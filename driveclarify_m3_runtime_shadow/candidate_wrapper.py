"""Shadow candidate wrapper around deterministic candidate runner."""

from __future__ import annotations

from typing import Any

from driveclarify_candidate_stability.live_protocol import (
    DeterministicCandidateRunner,
    STATUS_COMPLETE,
)
from driveclarify_candidate_stability.types import (
    CandidateBatchContext,
    CandidateRequest,
    DeterministicSettings,
)
from driveclarify_m3_offline_replay.serialization import canonical_sha256

from .contracts import (
    ShadowCandidateResult,
    ShadowObservationSnapshot,
)

BLOCKED_SHADOW_CANDIDATE_EXECUTION_FAILED = (
    "BLOCKED_SHADOW_CANDIDATE_EXECUTION_FAILED"
)
BLOCKED_SHADOW_CANDIDATE_BACKEND_NOT_ISOLATABLE = (
    "BLOCKED_SHADOW_CANDIDATE_BACKEND_NOT_ISOLATABLE"
)
BLOCKED_SHADOW_CANDIDATE_BACKEND_SIGNATURE_UNRESOLVED = (
    "BLOCKED_SHADOW_CANDIDATE_BACKEND_SIGNATURE_UNRESOLVED"
)


def _as_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _backend_model_identity(backend: Any) -> int:
    identity = getattr(backend, "model_instance_identity", None)
    if callable(identity):
        try:
            return _as_int(identity())
        except Exception:
            return 0
    if identity is not None:
        return _as_int(identity)
    return 0


def _build_context(snapshot: ShadowObservationSnapshot, backend: Any) -> CandidateBatchContext:
    return CandidateBatchContext(
        run_id=str(snapshot.observation_id),
        observation_id=str(snapshot.observation_id),
        observation_digest=str(snapshot.source_digest),
        source_frame=snapshot.frame_id,
        closed_loop_state_identity=str(snapshot.source_digest),
        ego_state_identity=None,
        navigation_identity=None,
        model_instance_identity=_backend_model_identity(backend),
        preprocessing_identity=(
            "SHADOW_CANDIDATE_WRAPPER_PREPROC_" + str(snapshot.source_digest)
        ),
        initial_rng_state_identities=None,
        cache_history_state_identity=None,
        freshness_token=str(snapshot.source_digest),
        deterministic_settings=DeterministicSettings(
            model_eval_required=True,
            model_eval_observed=True,
            inference_only_required=True,
            inference_only_observed=True,
            restore_rng_before_each_repetition_required=True,
            rng_states_recorded=True,
            repetition_rng_state_equal=True,
            isolate_mutable_cache_history_required=True,
            mutable_cache_history_isolated=True,
            decoding_mode="SYNTHETIC_SHADOW",
            provenance=(str(snapshot.observation_id), "TEST_ONLY_SYNTHETIC"),
        ),
        provenance=(str(snapshot.observation_id), "SHADOW_RUNTIME_CANDIDATE_WRAPPER"),
    )


def _evidence_output_digest(record: Any) -> str:
    evidence = getattr(record, "evidence", None)
    if isinstance(evidence, dict):
        digest = evidence.get("output_digest")
        if isinstance(digest, str) and len(digest) == 64:
            return digest
    return canonical_sha256({
        "candidate_output": getattr(getattr(record, "output", None), "__dict__", None),
        "evidence": dict(evidence) if isinstance(evidence, dict) else None,
    })


def _to_shadow_candidate_result(
    record: Any,
    context: CandidateBatchContext,
    semantic_candidate_id: str,
) -> ShadowCandidateResult:
    output = getattr(record, "output", None)
    if output is None:
        raise RuntimeError(
            "SHADOW_CANDIDATE_RECORD_MISSING_OUTPUT_" + str(getattr(record, "candidate_id", ""))
        )
    language = getattr(output, "language", ())
    return ShadowCandidateResult(
        candidate_id=semantic_candidate_id,
        interpretation_id=str(record.interpretation_id),
        model_forward_sequence_id=str(getattr(output, "model_instance_identity")),
        source_observation_id=str(getattr(output, "source_observation_identity")),
        source_frame_id=getattr(output, "source_frame"),
        route=getattr(output, "route"),
        speed=getattr(output, "speed"),
        language=tuple(str(item) for item in language),
        candidate_input_digest=str(context.observation_digest),
        candidate_output_digest=_evidence_output_digest(record),
        latency=0.0,
    )


def _is_backend_not_isolatable(run_result: Any, records: tuple[Any, ...]) -> bool:
    reason_codes = tuple(
        str(code).upper() for code in getattr(run_result, "reason_codes", ())
    )
    if any(
        "ISOLATION" in code
        or "CONTAMIN" in code
        or "STATE_MUTATION" in code
        or "SHARED_MUTABLE" in code
        for code in reason_codes
    ):
        return True
    side_effects = getattr(run_result, "side_effect_counts", {})
    for key in ("pid", "control", "planner_advancement"):
        if int(side_effects.get(key, 0)) > 0:
            return True
    for record in records:
        evidence = getattr(record, "evidence", None)
        if isinstance(evidence, dict):
            status = str(getattr(record, "status", "")).upper()
            evidence_keys = (
                str(status),
                str(evidence.get("candidate_status", "")).upper(),
                *[str(value).upper() for value in evidence.get("reason_codes", ())],
            )
            if any(
                "ISOLATION" in text
                or "CONTAMIN" in text
                or "STATE_MUTATION" in text
                or "SHARED_MUTABLE" in text
                for text in evidence_keys
            ):
                return True
    return False


def _select_first_round_records(records: tuple[Any, ...]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for record in records:
        interpretation = str(getattr(record, "interpretation_id", ""))
        if interpretation not in ("A", "B"):
            continue
        if int(getattr(record, "repetition_index", 0)) != 1:
            continue
        if interpretation in selected:
            continue
        selected[interpretation] = record
    return selected


def run_candidates(
    snapshot: ShadowObservationSnapshot,
    interpretation_a: str,
    interpretation_b: str,
    *,
    backend: Any,
    config: Any,
    input_isolation: Any | None = None,
    state_isolation: Any | None = None,
) -> tuple[ShadowCandidateResult, ...]:
    try:
        context = _build_context(snapshot, backend)
        requests = (
            CandidateRequest("A", interpretation_a, "A"),
            CandidateRequest("B", interpretation_b, "B"),
        )
        runner = DeterministicCandidateRunner(
            backend=backend,
            config=config,
            input_isolation=input_isolation,
            state_isolation=state_isolation,
        )
        run_result = runner.run(
            context=context,
            requests=requests,
            source_input=snapshot.model_input_snapshot,
        )
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError(BLOCKED_SHADOW_CANDIDATE_BACKEND_SIGNATURE_UNRESOLVED)

    if str(getattr(run_result, "status", "")) != STATUS_COMPLETE:
        raise RuntimeError(BLOCKED_SHADOW_CANDIDATE_EXECUTION_FAILED)

    candidates = tuple(getattr(run_result, "candidates", ()))
    if not candidates:
        raise RuntimeError(BLOCKED_SHADOW_CANDIDATE_EXECUTION_FAILED)

    if _is_backend_not_isolatable(run_result, candidates):
        raise RuntimeError(BLOCKED_SHADOW_CANDIDATE_BACKEND_NOT_ISOLATABLE)

    selected = _select_first_round_records(candidates)

    if set(selected) != {"A", "B"}:
        raise RuntimeError(BLOCKED_SHADOW_CANDIDATE_EXECUTION_FAILED)

    results: list[ShadowCandidateResult] = []
    for semantic_candidate_id, group in (("candidate_A", "A"), ("candidate_B", "B")):
        record = selected[group]
        if str(getattr(record, "status", "")) != STATUS_COMPLETE:
            raise RuntimeError(BLOCKED_SHADOW_CANDIDATE_EXECUTION_FAILED)
        results.append(_to_shadow_candidate_result(record, context, semantic_candidate_id))

    if len(results) != 2:
        raise RuntimeError(BLOCKED_SHADOW_CANDIDATE_EXECUTION_FAILED)
    return tuple(results)
