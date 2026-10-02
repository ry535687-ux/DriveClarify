"""Fail-closed deterministic live-candidate orchestration protocol.

The orchestrator is executable with CPU-only mocks.  It has no SimLingo,
torch, CUDA, CARLA, planner, PID or control adapter.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Any, ContextManager, Dict, Mapping, Optional, Protocol, Sequence, Tuple

from .determinism import (
    AUDIT_FAIL,
    AUDIT_PASS,
    AUDIT_UNRESOLVED,
    CandidateInputIsolation,
    CandidateStateIsolation,
    DeterministicInferenceConfig,
    RNGBackend,
    RNGCoordinator,
    StandardLibraryInputIsolation,
    StandardLibraryStateIsolation,
    audit_module_training_state,
    canonical_digest,
)
from .types import CandidateBatchContext, CandidateRequest


DETERMINISTIC_RUNNER_VERSION = "DRIVECLARIFY_DETERMINISTIC_CANDIDATE_RUNNER_V1"
EVIDENCE_SCHEMA_VERSION = "DRIVECLARIFY_DETERMINISM_EVIDENCE_V1"

STATUS_COMPLETE = "COMPLETE"
STATUS_FAIL = "FAIL_DETERMINISM_CONTRACT"
STATUS_UNRESOLVED = "UNRESOLVED_DETERMINISM"


@dataclass(frozen=True)
class PlanOnlyOutput:
    """Plan-only output with immutable source-linkage evidence."""

    route: Any
    speed: Any
    language: Tuple[str, ...]
    generated_token_ids: Optional[Tuple[int, ...]]
    source_observation_identity: str
    source_frame: Optional[int]
    freshness_token: str
    model_instance_identity: Any

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route": self.route,
            "speed": self.speed,
            "language": list(self.language),
            "generated_token_ids": (
                list(self.generated_token_ids)
                if self.generated_token_ids is not None
                else None
            ),
            "source_observation_identity": self.source_observation_identity,
            "source_frame": self.source_frame,
            "freshness_token": self.freshness_token,
            "model_instance_identity": self.model_instance_identity,
        }


class LiveCandidateBackend(Protocol):
    """Future live adapter surface; deliberately excludes all control APIs."""

    def apply_model_eval(self) -> None:
        ...

    def model_for_training_audit(self) -> Any:
        ...

    def model_instance_identity(self) -> Any:
        ...

    def rng_backends(self) -> Sequence[RNGBackend]:
        ...

    def persistent_candidate_state(self) -> Mapping[str, Any]:
        ...

    def inference_context(self) -> ContextManager[Any]:
        ...

    def inference_mode_active(self) -> bool:
        ...

    def plan_only_forward(
        self,
        *,
        context: CandidateBatchContext,
        request: CandidateRequest,
        candidate_input: Any,
        candidate_state: Mapping[str, Any],
        generation_config: DeterministicInferenceConfig,
    ) -> PlanOnlyOutput:
        ...


@dataclass(frozen=True)
class DeterministicCandidateRecord:
    candidate_id: str
    interpretation_id: str
    repetition_index: int
    status: str
    output: Optional[PlanOnlyOutput]
    evidence: Mapping[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "interpretation_id": self.interpretation_id,
            "repetition_index": self.repetition_index,
            "status": self.status,
            "output": self.output.to_dict() if self.output is not None else None,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class DeterministicRunResult:
    status: str
    reason_codes: Tuple[str, ...]
    candidates: Tuple[DeterministicCandidateRecord, ...]
    preflight_evidence: Mapping[str, Any]
    side_effect_counts: Mapping[str, int]
    automatic_continuation: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result_type": "DRIVECLARIFY_DETERMINISTIC_CANDIDATE_RUN_V1",
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "preflight_evidence": dict(self.preflight_evidence),
            "side_effect_counts": dict(self.side_effect_counts),
            "automatic_continuation": self.automatic_continuation,
        }


def _context_reasons(context: CandidateBatchContext) -> Tuple[str, ...]:
    reasons = []
    for name in (
        "run_id",
        "observation_id",
        "observation_digest",
        "closed_loop_state_identity",
        "preprocessing_identity",
        "freshness_token",
    ):
        value = getattr(context, name, None)
        if not isinstance(value, str) or not value:
            reasons.append("BATCH_CONTEXT_INVALID:" + name)
    if context.source_frame is not None and (
        isinstance(context.source_frame, bool)
        or not isinstance(context.source_frame, int)
    ):
        reasons.append("BATCH_CONTEXT_INVALID:source_frame")
    if context.model_instance_identity is None:
        reasons.append("BATCH_CONTEXT_INVALID:model_instance_identity")
    return tuple(reasons)


def _request_reasons(requests: Sequence[CandidateRequest]) -> Tuple[str, ...]:
    if len(requests) != 2:
        return ("DETERMINISTIC_RUNNER_REQUIRES_EXACT_A_B",)
    by_group = {request.expected_repetition_group: request for request in requests}
    if set(by_group) != {"A", "B"}:
        return ("DETERMINISTIC_RUNNER_REQUIRES_EXACT_A_B",)
    if any(
        request.candidate_id != request.expected_repetition_group
        for request in requests
    ):
        return ("CANDIDATE_REQUEST_ID_GROUP_MISMATCH",)
    if any(not request.interpretation for request in requests):
        return ("CANDIDATE_INTERPRETATION_EMPTY",)
    return ()


def _context_payload(context: CandidateBatchContext) -> Dict[str, Any]:
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
        "initial_rng_state_identities": (
            dict(context.initial_rng_state_identities)
            if context.initial_rng_state_identities is not None
            else None
        ),
        "cache_history_state_identity": context.cache_history_state_identity,
        "freshness_token": context.freshness_token,
        "deterministic_settings": {
            name: getattr(settings, name)
            for name in settings.__dataclass_fields__
        },
        "provenance": tuple(context.provenance),
    }


def _public_rng_snapshots(snapshots: Mapping[str, Any]) -> Dict[str, Any]:
    return {name: snapshot.public_dict() for name, snapshot in snapshots.items()}


def validate_determinism_evidence(evidence: Mapping[str, Any]) -> Tuple[str, ...]:
    """Return missing/failed critical evidence reason codes."""

    reasons = []
    training = evidence.get("model_training_audit")
    if not isinstance(training, Mapping) or training.get("status") != AUDIT_PASS:
        reasons.append("MODEL_TRAINING_AUDIT_NOT_PASS")
    if evidence.get("inference_mode_verified") is not True:
        reasons.append("INFERENCE_MODE_NOT_VERIFIED")
    if not evidence.get("generation_config_digest"):
        reasons.append("GENERATION_CONFIG_DIGEST_MISSING")
    availability = evidence.get("rng_backend_availability")
    if not isinstance(availability, Mapping) or any(
        value != "AVAILABLE" for value in availability.values()
    ):
        reasons.append("RNG_BACKEND_AVAILABILITY_INCOMPLETE")
    if set(availability or {}) != set(RNGCoordinator.REQUIRED_BACKENDS):
        reasons.append("RNG_BACKEND_SET_INCOMPLETE")
    if evidence.get("rng_restore_verified") is not True:
        reasons.append("RNG_RESTORE_NOT_VERIFIED")
    if evidence.get("input_isolation_verified") is not True:
        reasons.append("INPUT_ISOLATION_NOT_VERIFIED")
    if evidence.get("history_isolation_verified") is not True:
        reasons.append("HISTORY_ISOLATION_NOT_VERIFIED")
    if evidence.get("cache_isolation_verified") is not True:
        reasons.append("CACHE_ISOLATION_NOT_VERIFIED")
    for name in (
        "base_rng_digest",
        "pre_candidate_rng_digest",
        "post_candidate_rng_digest",
        "input_pre_digest",
        "input_post_digest",
        "model_instance_identity",
        "source_observation_identity",
        "freshness_token",
        "candidate_sequence_position",
        "deterministic_runner_version",
    ):
        if evidence.get(name) is None:
            reasons.append("EVIDENCE_FIELD_MISSING:" + name)
    return tuple(reasons)


class DeterministicCandidateRunner:
    """Execute mock/future live candidates under a strict fail-closed contract."""

    def __init__(
        self,
        backend: LiveCandidateBackend,
        config: DeterministicInferenceConfig,
        *,
        input_isolation: Optional[CandidateInputIsolation] = None,
        state_isolation: Optional[CandidateStateIsolation] = None,
    ) -> None:
        self.backend = backend
        self.config = config
        self.input_isolation = input_isolation or StandardLibraryInputIsolation()
        self.state_isolation = state_isolation or StandardLibraryStateIsolation()

    @staticmethod
    def _result(
        status: str,
        reasons: Sequence[str],
        records: Sequence[DeterministicCandidateRecord],
        preflight: Mapping[str, Any],
    ) -> DeterministicRunResult:
        return DeterministicRunResult(
            status=status,
            reason_codes=tuple(reasons),
            candidates=tuple(records),
            preflight_evidence=dict(preflight),
            side_effect_counts={
                "pid": 0,
                "control": 0,
                "planner_advancement": 0,
            },
            automatic_continuation=False,
        )

    @staticmethod
    def _record(
        *,
        candidate_id: str,
        interpretation_id: str,
        repetition_index: int,
        status: str,
        evidence: Mapping[str, Any],
        output: Optional[PlanOnlyOutput],
    ) -> DeterministicCandidateRecord:
        evidence_copy = dict(evidence)
        evidence_copy["candidate_status"] = status
        return DeterministicCandidateRecord(
            candidate_id=candidate_id,
            interpretation_id=interpretation_id,
            repetition_index=repetition_index,
            status=status,
            output=output if status == STATUS_COMPLETE else None,
            evidence=evidence_copy,
        )

    def _persist_plan_only_failure(
        self,
        *,
        context: CandidateBatchContext,
        candidate_id: str,
        stage: str,
        exc: Exception,
    ) -> Mapping[str, Any]:
        """Best-effort durable evidence that cannot replace the first error."""

        persist = getattr(self.backend, "persist_failure_evidence", None)
        if not callable(persist):
            return {
                "status": "NOT_AVAILABLE",
                "reason_code": "BACKEND_FAILURE_EVIDENCE_WRITER_UNAVAILABLE",
            }
        traceback_text = traceback.format_exc()
        try:
            result = persist(
                context=context,
                candidate_id=candidate_id,
                stage=stage,
                exc=exc,
                traceback_text=traceback_text,
            )
        except Exception as persistence_exc:
            return {
                "status": "WRITE_FAILED_OR_UNVERIFIED",
                "reason_code": "FAILURE_EVIDENCE_PERSISTENCE_FAILED",
                "persistence_exception_type": type(persistence_exc).__name__,
                "persistence_exception_message": str(persistence_exc),
            }
        if not isinstance(result, Mapping):
            return {
                "status": "WRITE_FAILED_OR_UNVERIFIED",
                "reason_code": "FAILURE_EVIDENCE_WRITER_RESULT_INVALID",
            }
        return dict(result)

    def run(
        self,
        *,
        context: CandidateBatchContext,
        requests: Sequence[CandidateRequest],
        source_input: Any,
    ) -> DeterministicRunResult:
        """Run A1/B1/A2/B2; stop at the first unresolved or failed contract."""

        preflight: Dict[str, Any] = {
            "deterministic_runner_version": DETERMINISTIC_RUNNER_VERSION,
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "fixture_marker": (
                "TEST_ONLY_SYNTHETIC"
                if "TEST_ONLY_SYNTHETIC" in context.provenance
                else None
            ),
            "live_simlingo_adapter_implemented": (
                getattr(
                    self.backend,
                    "live_simlingo_adapter_implemented",
                    False,
                )
                is True
            ),
        }
        context_failures = _context_reasons(context)
        request_failures = _request_reasons(requests)
        config_audit = self.config.audit()
        preflight["generation_config"] = self.config.to_dict()
        preflight["generation_config_audit"] = config_audit.to_dict()
        preflight["batch_context_reason_codes"] = list(context_failures)
        preflight["request_reason_codes"] = list(request_failures)
        failures = list(context_failures + request_failures)
        failures.extend(config_audit.reason_codes)
        if failures:
            return self._result(STATUS_FAIL, failures, (), preflight)
        try:
            batch_context_digest = canonical_digest(_context_payload(context))
        except Exception as exc:
            return self._result(
                STATUS_FAIL,
                ("BATCH_CONTEXT_DIGEST_FAILED:" + type(exc).__name__,),
                (),
                preflight,
            )
        preflight["batch_context_digest"] = batch_context_digest

        try:
            source_probe = self.input_isolation.prepare(source_input)
            source_digest = source_probe.source_pre_digest
        except Exception as exc:
            return self._result(
                STATUS_FAIL,
                (
                    "SOURCE_INPUT_ISOLATION_PREFLIGHT_FAILED:"
                    + type(exc).__name__,
                ),
                (),
                preflight,
            )
        preflight["source_input_digest"] = source_digest
        if source_digest != context.observation_digest:
            return self._result(
                STATUS_FAIL,
                ("SOURCE_INPUT_OBSERVATION_DIGEST_MISMATCH",),
                (),
                preflight,
            )

        # Establish an audited model state before capturing the one batch-wide
        # base RNG.  The same captured state is explicitly bound to both
        # repetition rounds and restored before every A/B candidate.
        try:
            self.backend.apply_model_eval()
            initial_training_audit = audit_module_training_state(
                self.backend.model_for_training_audit()
            )
        except Exception as exc:
            return self._result(
                STATUS_FAIL,
                ("MODEL_EVAL_OR_AUDIT_FAILED:" + type(exc).__name__,),
                (),
                preflight,
            )
        preflight["initial_model_training_audit"] = (
            initial_training_audit.to_dict()
        )
        if initial_training_audit.status != AUDIT_PASS:
            status = (
                STATUS_UNRESOLVED
                if initial_training_audit.status == AUDIT_UNRESOLVED
                else STATUS_FAIL
            )
            return self._result(
                status,
                ("MODEL_TRAINING_AUDIT_" + initial_training_audit.status,),
                (),
                preflight,
            )
        if self.backend.model_instance_identity() != context.model_instance_identity:
            return self._result(
                STATUS_FAIL,
                ("BACKEND_MODEL_IDENTITY_MISMATCH_BEFORE_CANDIDATES",),
                (),
                preflight,
            )

        try:
            coordinator = RNGCoordinator(self.backend.rng_backends())
            base_rng = coordinator.capture_base()
        except Exception as exc:
            return self._result(
                STATUS_UNRESOLVED,
                ("RNG_COORDINATOR_SETUP_FAILED:" + type(exc).__name__,),
                (),
                preflight,
            )
        preflight["rng_backends"] = _public_rng_snapshots(base_rng)
        preflight["repetition_base_rng_digests"] = {
            "1": {name: item.digest for name, item in base_rng.items()},
            "2": {name: item.digest for name, item in base_rng.items()},
        }
        if not coordinator.all_available(base_rng):
            return self._result(
                STATUS_UNRESOLVED,
                ("RNG_BACKEND_AVAILABILITY_UNRESOLVED",),
                (),
                preflight,
            )

        try:
            persistent_state = self.backend.persistent_candidate_state()
            baseline_state_digest = self.state_isolation.capture_baseline(
                persistent_state
            )
        except Exception as exc:
            return self._result(
                STATUS_FAIL,
                ("FAIL_CANDIDATE_STATE_ISOLATION:" + type(exc).__name__,),
                (),
                preflight,
            )
        preflight["candidate_state_baseline_digest"] = baseline_state_digest

        request_by_group = {
            request.expected_repetition_group: request for request in requests
        }
        schedule = (
            ("A1", "A", 1),
            ("B1", "B", 1),
            ("A2", "A", 2),
            ("B2", "B", 2),
        )
        records = []
        prior_candidate_state_ids = set()
        prior_candidate_states = []

        for sequence_position, (candidate_id, group, repetition) in enumerate(
            schedule, start=1
        ):
            request = request_by_group[group]
            evidence: Dict[str, Any] = {
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "candidate_status": None,
                "reason_codes": [],
                "model_training_audit": None,
                "inference_mode_verified": None,
                "generation_config_digest": config_audit.generation_config_digest,
                "rng_backend_availability": {
                    name: snapshot.availability
                    for name, snapshot in base_rng.items()
                },
                "base_rng_digest": {
                    name: snapshot.digest for name, snapshot in base_rng.items()
                },
                "pre_candidate_rng_digest": None,
                "post_candidate_rng_digest": None,
                "rng_restore_verified": None,
                "input_isolation_verified": None,
                "input_pre_digest": source_digest,
                "input_post_digest": None,
                "candidate_input_pre_digest": None,
                "candidate_input_post_digest": None,
                "history_isolation_verified": None,
                "cache_isolation_verified": None,
                "state_baseline_pre_digest": baseline_state_digest,
                "state_baseline_post_digest": None,
                "model_instance_identity": self.backend.model_instance_identity(),
                "source_observation_identity": context.observation_id,
                "source_frame": context.source_frame,
                "freshness_token": context.freshness_token,
                "candidate_sequence_position": sequence_position,
                "repetition_index": repetition,
                "deterministic_runner_version": DETERMINISTIC_RUNNER_VERSION,
                "output_digest": None,
            }

            def abort(reason: str, unresolved: bool = False) -> DeterministicRunResult:
                evidence["reason_codes"] = list(evidence["reason_codes"]) + [reason]
                status = STATUS_UNRESOLVED if unresolved else STATUS_FAIL
                records.append(
                    self._record(
                        candidate_id=candidate_id,
                        interpretation_id=group,
                        repetition_index=repetition,
                        status=status,
                        evidence=evidence,
                        output=None,
                    )
                )
                return self._result(status, (reason,), records, preflight)

            # Steps 2-3: eval plus recursive training audit.
            try:
                self.backend.apply_model_eval()
                preparation_audit = audit_module_training_state(
                    self.backend.model_for_training_audit()
                )
            except Exception as exc:
                return abort("MODEL_EVAL_OR_AUDIT_FAILED:" + type(exc).__name__)
            evidence["model_training_audit"] = {
                "status": preparation_audit.status,
                "preparation_audit": preparation_audit.to_dict(),
                "forward_adjacent_audit": None,
            }
            if preparation_audit.status != AUDIT_PASS:
                return abort(
                    "MODEL_TRAINING_AUDIT_" + preparation_audit.status,
                    unresolved=preparation_audit.status == AUDIT_UNRESOLVED,
                )

            # Step 4: no Transformers/model defaults are consulted.
            per_candidate_config_audit = self.config.audit()
            if per_candidate_config_audit.status != AUDIT_PASS:
                return abort("GENERATION_CONFIG_VALIDATION_FAILED")

            # Steps 6-7: each candidate gets its own input and state objects.
            try:
                isolated_input = self.input_isolation.prepare(source_input)
            except Exception as exc:
                return abort("FAIL_CANDIDATE_INPUT_ISOLATION:" + type(exc).__name__)
            evidence["candidate_input_pre_digest"] = (
                isolated_input.candidate_pre_digest
            )
            try:
                current_persistent_state = self.backend.persistent_candidate_state()
                isolated_state = self.state_isolation.prepare(
                    current_persistent_state, baseline_state_digest
                )
            except Exception as exc:
                return abort("FAIL_CANDIDATE_STATE_ISOLATION:" + type(exc).__name__)

            # Step 8: restore the exact batch base before every candidate.
            pre_rng, restore_verified, restore_reasons = (
                coordinator.restore_and_verify(base_rng)
            )
            evidence["pre_candidate_rng_digest"] = dict(pre_rng)
            evidence["rng_restore_verified"] = restore_verified
            if not restore_verified:
                return abort(
                    "RNG_RESTORE_VERIFICATION_FAILED:"
                    + ",".join(restore_reasons)
                )

            # The second audit is deliberately adjacent to the inference
            # context/forward.  A later mode mutation cannot hide behind eval().
            adjacent_audit = audit_module_training_state(
                self.backend.model_for_training_audit()
            )
            evidence["model_training_audit"] = {
                "status": adjacent_audit.status,
                "preparation_audit": preparation_audit.to_dict(),
                "forward_adjacent_audit": adjacent_audit.to_dict(),
            }
            if adjacent_audit.status != AUDIT_PASS:
                return abort(
                    "FORWARD_ADJACENT_TRAINING_AUDIT_" + adjacent_audit.status,
                    unresolved=adjacent_audit.status == AUDIT_UNRESOLVED,
                )

            # Steps 9-11: inference-only, plan-only forward and local capture.
            try:
                with self.backend.inference_context():
                    inference_active = self.backend.inference_mode_active()
                    evidence["inference_mode_verified"] = (
                        inference_active is True
                    )
                    if inference_active is not True:
                        return abort("INFERENCE_MODE_NOT_VERIFIED")
                    output = self.backend.plan_only_forward(
                        context=context,
                        request=request,
                        candidate_input=isolated_input.value,
                        candidate_state=isolated_state.value,
                        generation_config=self.config,
                    )
            except Exception as exc:
                evidence["failure_evidence_persistence"] = (
                    self._persist_plan_only_failure(
                        context=context,
                        candidate_id=candidate_id,
                        stage="PLAN_ONLY_FORWARD",
                        exc=exc,
                    )
                )
                return abort("PLAN_ONLY_FORWARD_FAILED:" + type(exc).__name__)

            # Steps 12-14: mutation/state/RNG and linkage verification.
            input_audit = self.input_isolation.verify(
                isolated_input, source_input
            )
            evidence["input_isolation_verified"] = input_audit.verified
            evidence["input_post_digest"] = input_audit.source_post_digest
            evidence["candidate_input_post_digest"] = (
                input_audit.candidate_post_digest
            )
            if not input_audit.verified:
                return abort(
                    "FAIL_CANDIDATE_INPUT_ISOLATION:"
                    + ",".join(input_audit.reason_codes)
                )

            state_audit = self.state_isolation.verify(
                self.backend.persistent_candidate_state(),
                isolated_state,
                prior_candidate_state_ids,
            )
            evidence["history_isolation_verified"] = (
                state_audit.history_isolation_verified
            )
            evidence["cache_isolation_verified"] = (
                state_audit.cache_isolation_verified
            )
            evidence["state_baseline_post_digest"] = (
                state_audit.baseline_post_digest
            )
            if state_audit.status != AUDIT_PASS:
                return abort(
                    "FAIL_CANDIDATE_STATE_ISOLATION:"
                    + ",".join(state_audit.reason_codes)
                )

            evidence["post_candidate_rng_digest"] = dict(
                coordinator.current_digests()
            )
            if canonical_digest(_context_payload(context)) != batch_context_digest:
                return abort("IMMUTABLE_BATCH_CONTEXT_CHANGED")
            prior_candidate_state_ids.update(isolated_state.candidate_mutable_ids)
            # Keep prior isolated state graphs alive so CPython object-id reuse
            # cannot masquerade as cross-candidate sharing (or hide it).
            prior_candidate_states.append(isolated_state)
            linkage_failures = []
            if output.source_observation_identity != context.observation_id:
                linkage_failures.append("OUTPUT_OBSERVATION_IDENTITY_MISMATCH")
            if output.source_frame != context.source_frame:
                linkage_failures.append("OUTPUT_SOURCE_FRAME_MISMATCH")
            if output.freshness_token != context.freshness_token:
                linkage_failures.append("OUTPUT_FRESHNESS_TOKEN_MISMATCH")
            if (
                output.model_instance_identity
                != context.model_instance_identity
            ):
                linkage_failures.append("OUTPUT_MODEL_IDENTITY_MISMATCH")
            if (
                self.backend.model_instance_identity()
                != context.model_instance_identity
            ):
                linkage_failures.append("BACKEND_MODEL_IDENTITY_CHANGED")
            if linkage_failures:
                return abort(",".join(linkage_failures))
            try:
                evidence["output_digest"] = canonical_digest(output.to_dict())
            except Exception as exc:
                return abort("OUTPUT_DIGEST_FAILED:" + type(exc).__name__)

            critical_failures = validate_determinism_evidence(evidence)
            if critical_failures:
                return abort(
                    "DETERMINISM_EVIDENCE_INCOMPLETE:"
                    + ",".join(critical_failures)
                )
            records.append(
                self._record(
                    candidate_id=candidate_id,
                    interpretation_id=group,
                    repetition_index=repetition,
                    status=STATUS_COMPLETE,
                    evidence=evidence,
                    output=output,
                )
            )

        return self._result(STATUS_COMPLETE, (), records, preflight)
