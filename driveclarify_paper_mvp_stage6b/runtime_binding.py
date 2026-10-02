"""Unified eight-method binding for the existing SimLingo pre-PID hook.

The binding changes only plan selection at the already-protected seam.  It
never creates a PID, calls ``apply_control``, advances the planner, or emits a
``VehicleControl`` object.  Original SimLingo receives the raw instruction and
uses its normal forward; candidate methods consume only their Baseline-V2
candidate-forward budget.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_m3_runtime_shadow.speed_consequence import (
    evaluate_pid_desired_speed_v0,
)
from driveclarify_m3_runtime_shadow.live_shadow_runtime import (
    _route_planner_snapshot,
)
from driveclarify_paper_mvp_evaluation.contracts import (
    GateStatus,
    MethodId,
    RuntimeAction,
    RuntimeCandidate as EvaluationCandidate,
)
from driveclarify_paper_mvp_runtime.contracts import (
    CandidateGenerationResult,
    CandidatePlan,
    ConsequenceEvaluation,
    ExecutionBoundaryFacts,
    PolicyEpisodeInput,
    RuntimeCandidate,
    canonical_sha256,
)
from driveclarify_paper_mvp_runtime.orchestrator import Stage6AOrchestrator
from driveclarify_paper_mvp_runtime.simlingo_binding import (
    CandidateForwardResult,
    CarlaOneTickHardRuleMonitor,
    CarlaOneTickPhysicalSafetyMonitor,
    SimLingoCandidateForwardProvider,
    Stage6ASimLingoBinding,
    _atomic_json,
    _ego_relative_xy,
    _jsonable,
    _points,
    _scalar,
    build_runtime_consequence_evaluation,
)

from .contracts import (
    ForwardAccounting,
    Freshness,
    LabelFirewallCounters,
    MethodOutput,
    PlanSource,
    Stage6BContractError,
)
from .evaluator import OnlineSafetyAccumulator
from .interaction import (
    ASK_DELAY_SECONDS,
    AnswerDelivery,
    BlackboardInformationChannel,
    ControlledPassengerIntentProvider,
    DelayedPassengerAnswerChannel,
    InformationDelivery,
    StructuredClarificationQuery,
    WaitLifecycle,
)
from .method_adapter import UnifiedMethodAdapter
from .semantics import (
    CandidateSetAudit,
    SemanticCandidatePipeline,
    SemanticCandidateSet,
    resolve_natural_language_selection,
    update_post_plan_divergence,
)


STAGE6B_LIVE_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE"
STAGE6B_OUTPUT_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6B_OUTPUT_DIR"
STAGE6B_METHOD_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6B_METHOD_ID"
STAGE6B_EPISODE_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6B_EPISODE_ID"
STAGE6B_FIXTURE_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6B_RUNTIME_FIXTURE_ID"
STAGE6B_SEED_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6B_SEED"
STAGE6B_RAW_INSTRUCTION_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6B_RAW_INSTRUCTION"
STAGE6B_INFORMATION_EXPECTED_ENV = (
    "DRIVECLARIFY_PAPER_MVP_STAGE6B_INFORMATION_EXPECTED"
)
STAGE6B_VISUALIZATION_ENV = "DRIVECLARIFY_PAPER_MVP_STAGE6B_VISUALIZATION"
STAGE6B_AUDIT_FILENAME = "stage6b_runtime_audit.json"
STAGE6B_FRAME_TRACE_FILENAME = "stage6b_frame_trace.jsonl"
STAGE6B_SCHEMA = "driveclarify.paper_mvp_stage6b_unified_runtime.v1"


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise Stage6BContractError("MISSING_REQUIRED_STAGE6B_ENV:" + name)
    return value.strip()


def _zero_speed_like(value: Any) -> Any:
    detach = getattr(value, "detach", None)
    clone = getattr(detach(), "clone", None) if callable(detach) else None
    if callable(clone):
        result = clone()
        zero = getattr(result, "zero_", None)
        if callable(zero):
            zero()
            return result
    if isinstance(value, tuple):
        return tuple(_zero_speed_like(item) for item in value)
    if isinstance(value, list):
        return [_zero_speed_like(item) for item in value]
    try:
        return value * 0
    except Exception as exc:
        raise Stage6BContractError("STOP_SPEED_PLAN_ZEROING_UNAVAILABLE") from exc


def _control_values(control: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in (
        "steer",
        "throttle",
        "brake",
        "hand_brake",
        "reverse",
        "manual_gear_shift",
        "gear",
    ):
        try:
            if hasattr(control, name):
                value = getattr(control, name)
                result[name] = value.item() if hasattr(value, "item") else value
        except Exception:
            continue
    return result


class _FixedCandidateGenerator:
    def __init__(self, result: CandidateGenerationResult) -> None:
        self.result = result

    def generate(self, episode: PolicyEpisodeInput) -> CandidateGenerationResult:
        if episode.input_digest != self.result.audit.policy_input_sha256:
            raise Stage6BContractError("FIXED_CANDIDATE_EPISODE_MISMATCH")
        return self.result


class Stage6BUnifiedSimLingoBinding(Stage6ASimLingoBinding):
    """Common CARLA/SimLingo backend with one interchangeable policy adapter."""

    enabled = True

    def __init__(self, agent: Any, output_dir: str | os.PathLike[str]) -> None:
        self.method_id = _required_env(STAGE6B_METHOD_ENV)
        self.episode_id = _required_env(STAGE6B_EPISODE_ENV)
        self.runtime_fixture_id = _required_env(STAGE6B_FIXTURE_ENV)
        self.seed = int(_required_env(STAGE6B_SEED_ENV))
        self.raw_instruction = _required_env(STAGE6B_RAW_INSTRUCTION_ENV)
        self.information_expected = _truthy(
            os.environ.get(STAGE6B_INFORMATION_EXPECTED_ENV)
        )
        self.stage6b_output_dir = Path(output_dir)
        self.stage6b_output_dir.mkdir(parents=True, exist_ok=True)
        physical = CarlaOneTickPhysicalSafetyMonitor()
        rule = CarlaOneTickHardRuleMonitor()
        super().__init__(
            agent,
            output_dir,
            run_id=self.episode_id,
            physical_safety_monitor=physical,
            hard_rule_monitor=rule,
            instruction_provider=lambda *_: self.raw_instruction,
            authority_enabled=True,
        )
        # Set before the first SimLingo tick builds DrivingInput.  This is raw
        # benchmark language, never a DriveClarify-resolved interpretation.
        # SimLingo's existing prompt interface requires ``user_flag == 1`` to
        # select its trained <INSTRUCTION_FOLLOWING> mode while retaining the
        # ordinary route target.  This is prompt-mode selection only: it does
        # not alter model output, PID input, throttle, or control ownership.
        self.agent.custom_prompt = self.raw_instruction
        self.agent.user_flag = 1
        self.semantic_pipeline = SemanticCandidatePipeline()
        self.firewall = LabelFirewallCounters()
        private_path = (
            Path(__file__).resolve().parent
            / "private"
            / "PASSENGER_INTENT_CONTRACT.json"
        )
        provider = ControlledPassengerIntentProvider.from_file(
            private_path,
            runtime_fixture_id=self.runtime_fixture_id,
            seed=self.seed,
            firewall=self.firewall,
        )
        self.answer_channel = DelayedPassengerAnswerChannel(
            provider, delay_seconds=ASK_DELAY_SECONDS
        )
        self.information_channel = BlackboardInformationChannel(
            runtime_fixture_id=self.runtime_fixture_id,
            information_expected=self.information_expected,
        )
        self.adapter = UnifiedMethodAdapter(self.method_id)
        self.safety_accumulator = OnlineSafetyAccumulator()
        self._semantic_set: SemanticCandidateSet | None = None
        self._candidate_audit: CandidateSetAudit | None = None
        self._current_output: MethodOutput | None = None
        self._current_forwards: dict[str, CandidateForwardResult] = {}
        self._all_candidate_forwards: list[Mapping[str, Any]] = []
        self._pending_interaction: AnswerDelivery | InformationDelivery | None = None
        self._wait_lifecycle: WaitLifecycle | None = None
        self._query_trace: list[dict[str, Any]] = []
        self._wait_trace: list[dict[str, Any]] = []
        self._decision_trace: list[dict[str, Any]] = []
        self._frame_pending: dict[str, Any] = {}
        self._last_simulation_time = 0.0
        self._normal_model_forwards = 0
        self._candidate_model_forwards = 0
        self._existing_pid_invocations = 0
        self._candidate_commits = 0
        self._authority_receipts = 0
        self._full_brake_count = 0
        self._zero_speed_count = 0
        self._maximum_speed_mps = 0.0
        self._runtime_errors: list[dict[str, Any]] = []
        self._terminal = False
        self._closed = False
        self._selected_raw_route: Any = None
        self._selected_raw_speed: Any = None
        self._latest_baseline_route: Any = None
        self._latest_baseline_speed: Any = None
        self._last_desired_speed_mps: float | None = None
        self._last_selected_plan_source = PlanSource.BASELINE_FALLBACK_PLAN.value
        self._frame_trace_path = self.stage6b_output_dir / STAGE6B_FRAME_TRACE_FILENAME
        if _truthy(os.environ.get(STAGE6B_VISUALIZATION_ENV)) and self._visualizer is None:
            from driveclarify_m3_runtime_shadow.live_visualization import (
                LiveShadowVisualizerV0,
            )

            self._visualizer = LiveShadowVisualizerV0(
                self.stage6b_output_dir / "stage6b_live_panel.png",
                open_window=True,
                window_title="DriveClarify Stage 6A/6B Live Runtime",
            )
        self._persist()

    # ------------------------------------------------------------------
    # Existing hook lifecycle
    # ------------------------------------------------------------------
    def on_tick(
        self,
        input_data: Any,
        tick_data: Any,
        timestamp: Any,
        frame: Any,
        observation_id: Any,
    ) -> None:
        super().on_tick(input_data, tick_data, timestamp, frame, observation_id)
        self._last_simulation_time = float(timestamp)
        self._plan_selection_done = False
        self._selected_raw_route = None
        self._selected_raw_speed = None
        self._capture_runtime_frame(tick_data, frame, observation_id)
        self._poll_interaction_channels()
        self._persist()

    def on_model_output(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        model_start: float,
        model_end: float,
    ) -> None:
        self._normal_model_forwards += 1
        self._latest_baseline_route = baseline_route
        self._latest_baseline_speed = baseline_speed
        self._baseline_visual_route = _points(baseline_route) or ()
        self._baseline_visual_speed = _points(baseline_speed) or ()
        self._plan_selection_done = False
        self._frame_pending["model"] = self._model_trace(
            baseline_route, baseline_speed, model_start, model_end
        )
        try:
            if self._latest_episode is None or self._current_output is not None and self._pending_interaction is None:
                self._persist()
                return
            if self._pending_interaction is not None:
                self._run_interaction_replan()
            elif self._current_output is None:
                self._run_initial_decision()
        except Exception as exc:
            self._runtime_errors.append(
                {
                    "stage": "ON_MODEL_OUTPUT",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "frame_id": self._latest_frame,
                }
            )
            self._current_output = self._fallback_output(
                "STAGE6B_RUNTIME_DECISION_EXCEPTION"
            )
        self._render_visualization(force=True)
        self._persist()

    def select_plan_source(
        self,
        baseline_route: Any,
        baseline_speed: Any,
        current_monotonic: float,
    ) -> tuple[Any, Any]:
        del current_monotonic
        self._plan_selection_done = True
        output = self._current_output
        route, speed = baseline_route, baseline_speed
        source = PlanSource.BASELINE_FALLBACK_PLAN
        if output is not None:
            source = output.plan_source
            if output.action is RuntimeAction.ACT and output.selected_candidate_id is not None:
                candidate = self._current_forwards.get(output.selected_candidate_id)
                if (
                    candidate is not None
                    and self._latest_frame is not None
                    and candidate.plan.source_frame_id == self._latest_frame
                ):
                    route, speed = candidate.raw_route, candidate.raw_speed
                    self._candidate_commits += 1
                # Later ordinary forwards are already conditioned by
                # ``agent.custom_prompt`` and remain the normal forward path.
            elif output.action is RuntimeAction.STOP:
                speed = _zero_speed_like(baseline_speed)
            elif output.action in {
                RuntimeAction.ASK,
                RuntimeAction.WAIT,
                RuntimeAction.FALLBACK,
            }:
                route, speed = baseline_route, baseline_speed
        self._last_selected_plan_source = source.value
        self._selected_raw_route, self._selected_raw_speed = route, speed
        self._last_desired_speed_mps = self._desired_speed(speed)
        self._frame_pending["pre_pid"] = {
            "plan_source": source.value,
            "selected_candidate_id": (
                None if output is None else output.selected_candidate_id
            ),
            "desired_speed_mps": self._last_desired_speed_mps,
            "control_owner": "EXISTING_SIMLINGO_PID",
            "new_pid_instance_count": 0,
            "candidate_direct_control_write_count": 0,
            "m3_direct_control_write_count": 0,
        }
        self._persist()
        return route, speed

    def on_pid_invocation(self, current_monotonic: float) -> None:
        self._existing_pid_invocations += 1
        self._frame_pending["pid"] = {
            "invocation_monotonic": float(current_monotonic),
            "owner": "EXISTING_SIMLINGO_PID",
            "invocation_index": self._existing_pid_invocations,
            "new_pid_invocations": 0,
        }

    def on_control(
        self, control: Any, gt_velocity: Any, current_monotonic: float
    ) -> None:
        values = _control_values(control)
        speed = _scalar(gt_velocity)
        if speed is not None:
            self._maximum_speed_mps = max(self._maximum_speed_mps, abs(speed))
            if abs(speed) <= 1e-9:
                self._zero_speed_count += 1
        if float(values.get("brake", 0.0) or 0.0) >= 0.999 and float(
            values.get("throttle", 0.0) or 0.0
        ) <= 1e-9:
            self._full_brake_count += 1
        brake_source = "UNKNOWN"
        if self._current_output is not None and self._current_output.action is RuntimeAction.STOP:
            brake_source = "POLICY_STOP_SPEED_PLAN_THROUGH_EXISTING_PID"
        elif (
            self._last_desired_speed_mps is not None
            and self._last_desired_speed_mps
            < float(getattr(self.agent.config, "brake_speed", 0.4))
        ):
            brake_source = "EXISTING_SIMLINGO_PID_LOW_DESIRED_SPEED_RULE"
        self._frame_pending["control"] = {
            "observed_monotonic": float(current_monotonic),
            "vehicle_control": values,
            "ego_velocity_mps": speed,
            "brake_source": brake_source,
            "throttle_source": "EXISTING_SIMLINGO_PID",
            "control_owner": "EXISTING_SIMLINGO_PID",
            "method_stop": bool(
                self._current_output is not None
                and self._current_output.action is RuntimeAction.STOP
            ),
            "safety_emergency_action": False,
        }
        self._append_frame_trace()
        self._persist()

    def commit(self) -> None:
        self._persist()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._terminal = True
        if self.adapter.state is not None and not self.adapter.state.terminal:
            self.adapter.terminate("SIMLINGO_AGENT_DESTROY")
        if self._wait_lifecycle is not None:
            self._record_wait_trace()
        try:
            if self._visualizer is not None:
                self._visualizer.close()
        except Exception as exc:
            self._runtime_errors.append(
                {
                    "stage": "VISUALIZER_CLOSE",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
        self._persist()

    # ------------------------------------------------------------------
    # Decision, ASK, WAIT and replan
    # ------------------------------------------------------------------
    def _run_initial_decision(self) -> None:
        episode = self._episode()
        if self.method_id in {
            MethodId.ORIGINAL_SIMLINGO.value,
            MethodId.ALWAYS_STOP.value,
        }:
            effective_k = 0
            self._semantic_set = None
            self._candidate_audit = None
        else:
            semantic = self.semantic_pipeline.generate(episode)
            self._semantic_set = semantic
            self._candidate_audit = semantic.audit
            effective_k = semantic.audit.effective_k
        self.adapter.initialize_episode(
            episode_id=self.episode_id,
            raw_instruction=self.raw_instruction,
            effective_k=effective_k,
        )
        self.adapter.observe(
            observation_id=episode.vision_observation.observation_id
        )
        language_uncertainty = (
            None
            if self._semantic_set is None or effective_k == 0
            else 0.0
            if effective_k == 1
            else 1.0
            if self._candidate_audit and self._candidate_audit.semantic_divergence
            else 0.0
        )
        required = self.adapter.required_initial_candidate_forwards(
            language_uncertainty=language_uncertainty
        )
        candidates = () if self._semantic_set is None else self._semantic_set.candidates
        forwards = self._run_candidate_forwards(episode, candidates[:required])
        scores = self._evaluation_candidates(episode, candidates, forwards)
        safety, rule, consequence = self._decision_gates(
            episode, candidates, forwards
        )
        authoritative_action = None
        authoritative_candidate_id = None
        resolver_applied = False
        orchestrator_audit: Mapping[str, Any] | None = None
        if (
            self.method_id == MethodId.DRIVECLARIFY.value
            and effective_k == 2
            and len(forwards) == 2
        ):
            (
                authoritative_action,
                authoritative_candidate_id,
                resolver_applied,
                orchestrator_audit,
            ) = self._driveclarify_authority(
                episode, candidates, forwards, consequence
            )
        output = self.adapter.decide(
            candidates=scores,
            language_uncertainty=language_uncertainty,
            holding_verified=self.information_expected,
            future_information_before_deadline=self.information_expected,
            hard_safety_status=safety,
            hard_rule_status=rule,
            authoritative_driveclarify_action=authoritative_action,
            authoritative_driveclarify_candidate_id=authoritative_candidate_id,
            authority_resolver_applied=resolver_applied,
            observed_candidate_forwards=len(forwards),
            observed_normal_forwards=1,
            candidate_forward_input_sha256=tuple(
                candidate.candidate_input_digest
                for candidate in candidates[: len(forwards)]
            ),
            candidate_forward_output_sha256=tuple(
                item.plan.output_digest for item in forwards.values()
            ),
            candidate_forward_latencies_seconds=tuple(
                item.plan.latency_s for item in forwards.values()
            ),
            runtime_evidence_ids=(
                "stage6b-semantic-gate",
                "carla-one-tick-physical-monitor",
                "carla-one-tick-rule-monitor",
            ),
        )
        if output.action is RuntimeAction.ASK and (
            self._semantic_set is None or self._semantic_set.audit.effective_k != 2
        ):
            output = self._fallback_output("ASK_QUERY_NOT_REALIZABLE")
        self._current_output = output
        self._record_decision(output, orchestrator_audit)
        self._apply_output_lifecycle(output)
        self._update_candidate_post_plan_audit(forwards, consequence)

    def _run_interaction_replan(self) -> None:
        delivery = self._pending_interaction
        if delivery is None:
            return
        episode = self._episode()
        semantic = self.semantic_pipeline.generate(episode)
        self._semantic_set = semantic
        self._candidate_audit = semantic.audit
        selected = resolve_natural_language_selection(
            (
                delivery.answer_text
                if isinstance(delivery, AnswerDelivery)
                else delivery.information_text
            ),
            semantic.canonical,
        )
        if isinstance(delivery, AnswerDelivery):
            self.adapter.on_answer(selected_candidate_id=selected)
            if self._query_trace:
                self._query_trace[-1].update(
                    {
                        "answer_time": delivery.answer_simulation_time,
                        "answer_delay_seconds": delivery.answer_delay_seconds,
                        "answer_text": delivery.answer_text,
                        "resolution": (
                            "RESOLVED" if selected is not None else "UNRESOLVED"
                        ),
                        "cache_invalidated": True,
                    }
                )
        else:
            self.adapter.on_information_update(selected_candidate_id=selected)
        self.adapter.observe(
            observation_id=episode.vision_observation.observation_id
        )
        selected_candidate = next(
            (item for item in semantic.candidates if item.candidate_id == selected),
            None,
        )
        forwards = self._run_candidate_forwards(
            episode,
            (() if selected_candidate is None else (selected_candidate,)),
        )
        scores = self._evaluation_candidates(
            episode, semantic.candidates, forwards
        )
        safety, rule, consequence = self._decision_gates(
            episode,
            (() if selected_candidate is None else (selected_candidate,)),
            forwards,
        )
        output = self.adapter.decide(
            candidates=scores,
            language_uncertainty=0.0,
            holding_verified=True,
            future_information_before_deadline=True,
            hard_safety_status=safety,
            hard_rule_status=rule,
            authoritative_driveclarify_action=(
                RuntimeAction.ACT
                if self.method_id == MethodId.DRIVECLARIFY.value
                and selected is not None
                else None
            ),
            authoritative_driveclarify_candidate_id=(
                selected
                if self.method_id == MethodId.DRIVECLARIFY.value
                else None
            ),
            authority_resolver_applied=(
                self.method_id == MethodId.DRIVECLARIFY.value
                and selected is not None
            ),
            observed_candidate_forwards=len(forwards),
            observed_normal_forwards=1,
            candidate_forward_input_sha256=(
                ()
                if selected_candidate is None
                else (selected_candidate.candidate_input_digest,)
            ),
            candidate_forward_output_sha256=tuple(
                item.plan.output_digest for item in forwards.values()
            ),
            candidate_forward_latencies_seconds=tuple(
                item.plan.latency_s for item in forwards.values()
            ),
            runtime_evidence_ids=(
                "fresh-observation-after-interaction",
                "stale-candidate-cache-invalidated",
            ),
        )
        self._current_output = output
        self._record_decision(output, None)
        self._apply_output_lifecycle(output)
        self._update_candidate_post_plan_audit(forwards, consequence)
        self._pending_interaction = None

    def _apply_output_lifecycle(self, output: MethodOutput) -> None:
        if output.authority_receipt_id is not None:
            self._authority_receipts += 1
        if output.action is RuntimeAction.ACT and output.selected_candidate_id:
            candidate = next(
                (
                    item
                    for item in (self._semantic_set.candidates if self._semantic_set else ())
                    if item.candidate_id == output.selected_candidate_id
                ),
                None,
            )
            if candidate is not None:
                self.agent.custom_prompt = candidate.prompt_text
        elif output.action is RuntimeAction.ASK:
            assert self._semantic_set is not None
            query = StructuredClarificationQuery.from_candidates(
                episode_id=self.episode_id,
                candidates=self._semantic_set.canonical,
                simulation_time=self._last_simulation_time,
            )
            self.answer_channel.request(query)
            self._query_trace.append(
                {
                    "query_id": query.query_id,
                    "question_text": query.question_text,
                    "query_start": query.issued_simulation_time,
                    "answer_deadline": query.answer_deadline_simulation_time,
                    "answer_time": None,
                    "answer_delay_seconds": None,
                    "resolution": "PENDING",
                    "same_tick_answer": False,
                }
            )
            self.adapter.mark_query_pending()
            self._start_wait_lifecycle()
        elif output.action is RuntimeAction.WAIT:
            self._start_wait_lifecycle()

    def _poll_interaction_channels(self) -> None:
        output = self._current_output
        if output is None or output.action not in {RuntimeAction.ASK, RuntimeAction.WAIT}:
            return
        delivery: AnswerDelivery | InformationDelivery | None = None
        if output.action is RuntimeAction.ASK:
            delivery = self.answer_channel.poll(self._last_simulation_time)
        if delivery is None:
            delivery = self.information_channel.poll()
        position = self._ego_position()
        if self._wait_lifecycle is not None:
            state = self._wait_lifecycle.observe(
                self._last_simulation_time, position, delivery
            )
            if state == "WAIT_TIMEOUT":
                self.adapter.mark_deadline_expired()
                self._record_wait_trace()
                self._current_output = self._fallback_output("WAIT_TIMEOUT")
                return
            if delivery is not None:
                self._record_wait_trace()
        if delivery is not None:
            self._pending_interaction = delivery

    def _start_wait_lifecycle(self) -> None:
        if self._wait_lifecycle is not None:
            return
        self._wait_lifecycle = WaitLifecycle(
            maximum_duration_seconds=(5.0 if self._current_output and self._current_output.action is RuntimeAction.ASK else 2.5)
        )
        self._wait_lifecycle.start(
            self._last_simulation_time, self._ego_position()
        )

    def _record_wait_trace(self) -> None:
        if self._wait_lifecycle is None:
            return
        payload = self._wait_lifecycle.to_dict()
        if payload not in self._wait_trace:
            self._wait_trace.append(payload)

    # ------------------------------------------------------------------
    # Candidate forwards, M2B, scoring and gates
    # ------------------------------------------------------------------
    def _run_candidate_forwards(
        self,
        episode: PolicyEpisodeInput,
        candidates: Sequence[RuntimeCandidate],
    ) -> dict[str, CandidateForwardResult]:
        if not candidates:
            self._current_forwards = {}
            return {}
        provider = self.forward_provider
        begin = getattr(provider, "begin_event", None)
        if callable(begin):
            begin()
        results: dict[str, CandidateForwardResult] = {}
        for candidate in candidates:
            result = provider(episode, candidate)
            if not isinstance(result, CandidateForwardResult):
                raise Stage6BContractError("CANDIDATE_FORWARD_RESULT_REQUIRED")
            results[candidate.candidate_id] = result
            self._candidate_model_forwards += 1
            evidence = {
                **dict(result.forward_evidence),
                "candidate_input_digest": candidate.candidate_input_digest,
                "candidate_output_digest": result.plan.output_digest,
                "event_kind": (
                    "INTERACTION_REPLAN"
                    if self._pending_interaction is not None
                    else "INITIAL_DECISION"
                ),
            }
            self._all_candidate_forwards.append(evidence)
        self._current_forwards = results
        return results

    def _evaluation_candidates(
        self,
        episode: PolicyEpisodeInput,
        candidates: Sequence[RuntimeCandidate],
        forwards: Mapping[str, CandidateForwardResult],
    ) -> tuple[EvaluationCandidate, ...]:
        result: list[EvaluationCandidate] = []
        for candidate in candidates:
            forward = forwards.get(candidate.candidate_id)
            if forward is None:
                rank, risk = None, None
            else:
                rank, risk = self._plan_rank_and_risk(episode, forward.plan)
            result.append(
                EvaluationCandidate(
                    candidate_id=candidate.candidate_id,
                    rank_score=rank,
                    risk_score=risk,
                    source="RUNTIME_GENERATED",
                )
            )
        return tuple(result)

    def _plan_rank_and_risk(
        self, episode: PolicyEpisodeInput, plan: CandidatePlan
    ) -> tuple[float | None, float | None]:
        speed_evidence = evaluate_pid_desired_speed_v0(plan.speed)
        desired = speed_evidence.semantic_value
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            hero = CarlaDataProvider.get_hero_actor()
            world = CarlaDataProvider.get_world()
            if hero is None or world is None:
                return None, None
            hero_extent = hero.bounding_box.extent
            hero_radius = math.hypot(float(hero_extent.x), float(hero_extent.y))
            minimum = float("inf")
            for actor in list(world.get_actors()):
                if int(actor.id) == int(hero.id) or not bool(actor.is_alive):
                    continue
                if not str(actor.type_id).startswith(("vehicle.", "walker.")):
                    continue
                point = actor.get_location()
                relative = _ego_relative_xy((float(point.x), float(point.y)), episode)
                extent = actor.bounding_box.extent
                radius = math.hypot(float(extent.x), float(extent.y))
                clearance = min(
                    math.hypot(relative[0] - route[0], relative[1] - route[1])
                    - hero_radius
                    - radius
                    for route in plan.route
                )
                minimum = min(minimum, clearance)
            if not math.isfinite(minimum):
                return None, None
            # Frozen R0 physical proxy: 0 at >=2 m swept clearance, 1 at
            # overlap.  This is runtime plan geometry, never label/confidence.
            risk = max(0.0, min(1.0, (2.0 - minimum) / 2.0))
            progress = sum(
                math.hypot(
                    plan.route[index][0] - plan.route[index - 1][0],
                    plan.route[index][1] - plan.route[index - 1][1],
                )
                for index in range(1, len(plan.route))
            )
            rank = progress + float(desired or 0.0) - 2.0 * risk
            return rank, risk
        except Exception:
            return None, None

    def _decision_gates(
        self,
        episode: PolicyEpisodeInput,
        candidates: Sequence[RuntimeCandidate],
        forwards: Mapping[str, CandidateForwardResult],
    ) -> tuple[GateStatus, GateStatus, ConsequenceEvaluation | None]:
        if self.method_id == MethodId.ORIGINAL_SIMLINGO.value:
            # These PASS values do not authorize a new plan; they preserve the
            # original baseline's own normal controller path.
            return GateStatus.PASS, GateStatus.PASS, None
        if not forwards:
            return GateStatus.UNKNOWN, GateStatus.UNKNOWN, None
        plans = tuple(forwards[item.candidate_id].plan for item in candidates if item.candidate_id in forwards)
        if len(plans) == 2 and len(candidates) == 2:
            typed_candidates = (candidates[0], candidates[1])
            typed_plans = (plans[0], plans[1])
            scene = self._latest_scene
            if scene is None:
                return GateStatus.UNKNOWN, GateStatus.UNKNOWN, None
            consequence = build_runtime_consequence_evaluation(
                episode, typed_candidates, typed_plans, scene=scene
            )
            observed = time.monotonic()
            physical = self.physical_safety_monitor(
                episode, typed_candidates, typed_plans, consequence, observed
            )
            rule = self.hard_rule_monitor(
                episode, typed_candidates, typed_plans, consequence, observed
            )
        else:
            plan = plans[0]
            candidate = next(item for item in candidates if item.candidate_id in forwards)
            fake = ConsequenceEvaluation(
                status="UNKNOWN",
                source_observation_id=episode.vision_observation.observation_id,
                source_frame_id=episode.vision_observation.frame_id,
                candidate_ids=(candidate.candidate_id, candidate.candidate_id + "-safety"),
                evaluator_id="STAGE6B_SINGLE_PLAN_GATE_ONLY",
                mapping_context=None,
                reason_codes=("SINGLE_PLAN_GATE_NO_CONSEQUENCE_COMPARISON",),
            )
            physical = self.physical_safety_monitor(
                episode, (candidate, candidate), (plan, plan), fake, time.monotonic()
            )
            rule = self.hard_rule_monitor(
                episode, (candidate, candidate), (plan, plan), fake, time.monotonic()
            )
            consequence = fake
        safety_status = (
            GateStatus.PASS
            if isinstance(physical, Mapping)
            and physical.get("safety_status") == "PASS"
            and physical.get("safety_critical_eligible") is True
            else GateStatus.FAIL
            if isinstance(physical, Mapping)
            and physical.get("safety_status") == "BLOCKED"
            else GateStatus.UNKNOWN
        )
        rule_status = (
            GateStatus.PASS
            if rule is not None and rule.authorizes_progress
            else GateStatus.FAIL
            if rule is not None and rule.status == "BLOCKED"
            else GateStatus.UNKNOWN
        )
        return safety_status, rule_status, consequence

    def _driveclarify_authority(
        self,
        episode: PolicyEpisodeInput,
        candidates: Sequence[RuntimeCandidate],
        forwards: Mapping[str, CandidateForwardResult],
        consequence: ConsequenceEvaluation | None,
    ) -> tuple[RuntimeAction | None, str | None, bool, Mapping[str, Any] | None]:
        if len(candidates) != 2 or consequence is None:
            return None, None, False, None
        typed_candidates = (candidates[0], candidates[1])
        plans = (
            forwards[candidates[0].candidate_id].plan,
            forwards[candidates[1].candidate_id].plan,
        )

        def plan_provider(_: PolicyEpisodeInput, candidate: RuntimeCandidate) -> CandidatePlan:
            return forwards[candidate.candidate_id].plan

        def consequence_provider(*_: Any) -> ConsequenceEvaluation:
            return consequence

        clarification = None if self.information_expected else self._clarification_signal
        holding = self._holding_signal if self.information_expected else None
        orchestrator = Stage6AOrchestrator(
            plan_provider=plan_provider,
            consequence_evaluator=consequence_provider,
            physical_safety_provider=self.physical_safety_monitor,
            hard_rule_provider=self.hard_rule_monitor,
            clarification_provider=clarification,
            holding_provider=holding,
            candidate_generator=_FixedCandidateGenerator(
                CandidateGenerationResult(
                    status="READY",
                    candidates=typed_candidates,
                    audit=self._semantic_set.raw_generation.audit,  # type: ignore[union-attr]
                    reason_codes=("STAGE6B_EFFECTIVE_K2",),
                )
            ),
            pre_pid_authority=self.authority,
        )
        facts = ExecutionBoundaryFacts(
            current_monotonic_time=0.0,
            candidate_freshness="FRESH",
            candidate_invalidated=False,
            active_query=False,
            active_holding_lease=False,
            independent_safety_guard_active=(
                True
                if self._latest_scene is None
                or self._latest_scene.independent_safety_guard_active is None
                else self._latest_scene.independent_safety_guard_active
            ),
            baseline_available=True,
            simulation_runtime="CARLA",
        )
        result = orchestrator.run(
            episode, decision_monotonic_time=0.0, issuance_facts=facts
        )
        audit = result.to_audit_dict()
        if result.m2b_result is None:
            return None, None, False, audit
        action_text = result.m2b_result.producer_action
        action = {
            "ACT": RuntimeAction.ACT,
            "ASK": RuntimeAction.ASK,
            "WAIT": RuntimeAction.WAIT,
            "FALLBACK_RECOMMENDED": RuntimeAction.FALLBACK,
        }.get(action_text)
        return (
            action,
            result.m2b_result.selected_candidate_id,
            action is not None,
            audit,
        )

    @staticmethod
    def _clarification_signal(
        episode: PolicyEpisodeInput,
        candidates: tuple[RuntimeCandidate, RuntimeCandidate],
        plans: tuple[CandidatePlan, CandidatePlan],
        consequence: ConsequenceEvaluation,
        at: float,
    ) -> Mapping[str, Any]:
        del episode, plans, consequence
        first, second = (item.candidate_id for item in candidates)
        return {
            "answer_confusion_matrix": {
                first: {"OPTION_ONE": 1.0, "OPTION_TWO": 0.0},
                second: {"OPTION_ONE": 0.0, "OPTION_TWO": 1.0},
            },
            "answer_resolution_probability": 1.0,
            "no_answer_probability": 0.0,
            "delay_distribution": [[ASK_DELAY_SECONDS, 1.0]],
            "query_cost": 0.01,
            "delay_cost_per_second": 0.0,
            "no_answer_penalty": 0.1,
            "answer_deadline_monotonic": at + 5.0,
            "source": "controlled-passenger-response-channel",
            "usage_purpose": "CLARIFICATION_DECISION_AUTHORITY",
            "evidence_grade": "VERIFIED_FROM_CONTROLLED_PROBE",
            "reason_codes": ["NATURAL_LANGUAGE_RESPONSE_CHANNEL_AVAILABLE"],
        }

    @staticmethod
    def _holding_signal(
        episode: PolicyEpisodeInput,
        candidates: tuple[RuntimeCandidate, RuntimeCandidate],
        plans: tuple[CandidatePlan, CandidatePlan],
        consequence: ConsequenceEvaluation,
        at: float,
    ) -> Mapping[str, Any]:
        del candidates, plans, consequence
        return {
            "expected_information_id": (
                "runtime-info-" + episode.vision_observation.observation_id[-24:]
            ),
            "expected_information_arrival_time": at + 0.8,
            "decision_deadline_monotonic": at + 2.5,
            "information_resolution_probability": 1.0,
            "wait_reason": "AWAIT_EXPECTED_OBSERVATION",
            "wait_cost": 0.03,
            "missed_opportunity_cost": 0.0,
            "maximum_holding_duration_s": 2.5,
            "reevaluation_interval_s": 0.1,
            "source": "scenario-runtime-information-channel",
            "evidence_grade": "VERIFIED_FROM_CONTROLLED_PROBE",
            "holding_capability_status": "AVAILABLE_CONTRACT_ONLY",
            "closed_loop_behavior": "MAINTAIN_CURRENT_VALID_CLOSED_LOOP_BEHAVIOR",
            "emergency_stop_semantics": False,
            "low_level_controller_owner": "EXISTING_BASELINE_PID",
            "usage_purpose": "WAIT_DECISION_AUTHORITY",
            "reason_codes": ["BOUNDED_RUNTIME_INFORMATION_CHANNEL_AVAILABLE"],
        }

    # ------------------------------------------------------------------
    # Diagnostic/evaluator evidence
    # ------------------------------------------------------------------
    def _capture_runtime_frame(
        self, tick_data: Any, frame: Any, observation_id: Any
    ) -> None:
        frame_id = int(frame) if frame is not None else None
        speed = _scalar(tick_data.get("speed")) if isinstance(tick_data, Mapping) else None
        self._frame_pending = {
            "schema_version": "driveclarify.stage6b_frame_trace.v1",
            "frame_id": frame_id,
            "observation_id": observation_id,
            "simulation_time": self._last_simulation_time,
            "method_id": self.method_id,
            "raw_instruction": self.raw_instruction,
            "agent_custom_prompt": getattr(self.agent, "custom_prompt", None),
            "agent_user_flag": getattr(self.agent, "user_flag", None),
            "simlingo_prompt": getattr(self.agent, "prompt", None),
            "ego_speed_mps": speed,
            "route_planner_state": _route_planner_snapshot(self.agent),
            "scenario_runner_state": {
                "raw_instruction_channel": "BOUND_BY_RUNTIME_ENVIRONMENT",
                "observable_condition_private_projection_read_count": 0,
                "runtime_signal_key": self.information_channel.blackboard_key,
                "runtime_signal_expected": self.information_expected,
            },
        }
        self.safety_accumulator.expect_tick()
        world_evidence = self._world_state_for_safety()
        self._frame_pending["world"] = world_evidence["trace"]
        if world_evidence["safety"] is not None:
            self.safety_accumulator.observe_tick(**world_evidence["safety"])

    def _world_state_for_safety(self) -> dict[str, Any]:
        try:
            from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

            hero = CarlaDataProvider.get_hero_actor()
            world = CarlaDataProvider.get_world()
            if hero is None or world is None:
                return {"trace": {"status": "UNAVAILABLE"}, "safety": None}
            transform = hero.get_transform()
            velocity = hero.get_velocity()
            extent = hero.bounding_box.extent
            actors: list[dict[str, Any]] = []
            trace_actors: list[dict[str, Any]] = []
            traffic_lights: list[dict[str, Any]] = []
            stop_signs: list[dict[str, Any]] = []
            for actor in list(world.get_actors())[:256]:
                if int(actor.id) == int(hero.id) or not bool(actor.is_alive):
                    continue
                type_id = str(actor.type_id)
                location = actor.get_location()
                actor_velocity = actor.get_velocity()
                if type_id.startswith(("vehicle.", "walker.")):
                    actor_extent = actor.bounding_box.extent
                    row = {
                        "actor_id": int(actor.id),
                        "type_id": type_id,
                        "position_xy": (float(location.x), float(location.y)),
                        "velocity_xy": (
                            float(actor_velocity.x),
                            float(actor_velocity.y),
                        ),
                        "radius_m": math.hypot(
                            float(actor_extent.x), float(actor_extent.y)
                        ),
                    }
                    actors.append(row)
                    if len(trace_actors) < 32:
                        trace_actors.append(row)
                elif type_id == "traffic.traffic_light":
                    traffic_lights.append(
                        {
                            "actor_id": int(actor.id),
                            "state": str(actor.get_state()),
                            "position_xy": [float(location.x), float(location.y)],
                        }
                    )
                elif type_id == "traffic.stop":
                    stop_signs.append(
                        {
                            "actor_id": int(actor.id),
                            "position_xy": [float(location.x), float(location.y)],
                        }
                    )
            trace = {
                "status": "AVAILABLE",
                "snapshot_frame": int(world.get_snapshot().frame),
                "ego_transform": {
                    "x": float(transform.location.x),
                    "y": float(transform.location.y),
                    "z": float(transform.location.z),
                    "yaw": float(transform.rotation.yaw),
                },
                "spawn_transform": {
                    "x": float(transform.location.x),
                    "y": float(transform.location.y),
                    "z": float(transform.location.z),
                    "yaw": float(transform.rotation.yaw),
                    "semantic": "FIRST_OBSERVED_EGO_TRANSFORM"
                    if self._normal_model_forwards == 0
                    else "CURRENT_EGO_TRANSFORM_COMPATIBILITY_ALIAS",
                },
                "traffic_lights": traffic_lights,
                "stop_signs": stop_signs,
                "actors": trace_actors,
                "initial_blocking_actor": self._nearest_forward_actor(
                    transform, trace_actors
                ),
                "obstacle_safety_state": _jsonable(
                    getattr(self.physical_safety_monitor, "last_evidence", None)
                ),
            }
            safety = {
                "ego_position_xy": (
                    float(transform.location.x), float(transform.location.y)
                ),
                "ego_velocity_xy": (float(velocity.x), float(velocity.y)),
                "ego_radius_m": math.hypot(float(extent.x), float(extent.y)),
                "actors": actors,
            }
            return {"trace": trace, "safety": safety}
        except Exception as exc:
            return {
                "trace": {
                    "status": "INVALID_EVIDENCE",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                "safety": None,
            }

    @staticmethod
    def _nearest_forward_actor(
        transform: Any, actors: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any] | None:
        yaw = math.radians(float(transform.rotation.yaw))
        cosine, sine = math.cos(yaw), math.sin(yaw)
        best: tuple[float, Mapping[str, Any]] | None = None
        for actor in actors:
            dx = float(actor["position_xy"][0]) - float(transform.location.x)
            dy = float(actor["position_xy"][1]) - float(transform.location.y)
            forward = cosine * dx + sine * dy
            lateral = -sine * dx + cosine * dy
            if forward <= 0.0 or abs(lateral) > 4.0:
                continue
            if best is None or forward < best[0]:
                best = (
                    forward,
                    {
                        "actor_id": actor["actor_id"],
                        "type_id": actor["type_id"],
                        "forward_m": forward,
                        "lateral_m": lateral,
                    },
                )
        return None if best is None else best[1]

    def _model_trace(
        self,
        route: Any,
        speed: Any,
        model_start: float,
        model_end: float,
    ) -> Mapping[str, Any]:
        route_points = _points(route)
        speed_points = _points(speed)
        return {
            "raw_instruction": self.raw_instruction,
            "simlingo_route_output": route_points,
            "pred_route": route_points,
            "pred_speed_wps": speed_points,
            "desired_speed_mps": self._desired_speed(speed),
            "longitudinal_target": (
                None
                if self._current_output is None
                else self._current_output.selected_candidate_id
            ),
            "model_start_monotonic": float(model_start),
            "model_end_monotonic": float(model_end),
            "model_latency_seconds": float(model_end) - float(model_start),
            "normal_forward_index": self._normal_model_forwards,
        }

    @staticmethod
    def _desired_speed(speed: Any) -> float | None:
        points = _points(speed)
        if points is None:
            return None
        evidence = evaluate_pid_desired_speed_v0(points)
        return evidence.semantic_value

    def _append_frame_trace(self) -> None:
        self._frame_trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self._frame_trace_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    _jsonable(self._frame_pending),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            handle.flush()
        self._frame_pending = {}

    def _desired_interaction_summary(self) -> Mapping[str, Any]:
        adapter = self.adapter.state
        query_count = 0 if adapter is None else adapter.query_count
        replan_count = 0 if adapter is None else adapter.replan_count
        wait_complete = any(
            item.get("exit_reason") == "MEANINGFUL_INFORMATION_ARRIVED"
            for item in self._wait_trace
        )
        return {
            "query_count": query_count,
            "decision_delay_seconds": sum(
                float(item.get("answer_delay_seconds") or 0.0)
                for item in self._query_trace
            ),
            "replan_count": replan_count,
            "resume_success": bool(
                replan_count > 0
                and self._current_output is not None
                and self._current_output.action is RuntimeAction.ACT
            ),
            "timeout": bool(
                any(item.get("timed_out") is True for item in self._wait_trace)
            ),
            "fallback": bool(
                self._current_output is not None
                and self._current_output.action is RuntimeAction.FALLBACK
            ),
            "wait_information_resume_observed": wait_complete,
        }

    def _record_decision(
        self,
        output: MethodOutput,
        orchestrator_audit: Mapping[str, Any] | None,
    ) -> None:
        row = output.to_dict()
        row["simulation_time"] = self._last_simulation_time
        row["source_frame_id"] = self._latest_frame
        row["orchestrator"] = (
            None if orchestrator_audit is None else dict(orchestrator_audit)
        )
        self._decision_trace.append(row)

    def _update_candidate_post_plan_audit(
        self,
        forwards: Mapping[str, CandidateForwardResult],
        consequence: ConsequenceEvaluation | None,
    ) -> None:
        if self._candidate_audit is None:
            return
        plans = [item.plan for item in forwards.values()]
        trajectory = None
        if len(plans) == 2:
            pairs = zip(plans[0].route, plans[1].route)
            trajectory = any(
                math.hypot(left[0] - right[0], left[1] - right[1]) > 0.5
                for left, right in pairs
            )
        consequence_divergence = None
        if consequence is not None and consequence.status == "AVAILABLE_VERIFIED":
            bindings = consequence.mapping_context.interpretation_task_bindings
            targets = {
                binding.symbolic_target_id for binding in bindings.values()
            }
            consequence_divergence = len(targets) > 1
        self._candidate_audit = update_post_plan_divergence(
            self._candidate_audit,
            trajectory_divergence=trajectory,
            consequence_divergence=consequence_divergence,
        )

    def _fallback_output(self, reason: str) -> MethodOutput:
        accounting = ForwardAccounting(
            compute_budget_case="STAGE6B_RUNTIME_FALLBACK_NO_CANDIDATE_FORWARD",
            required_candidate_forwards=0,
            observed_candidate_forwards=0,
            observed_normal_forwards=1,
        )
        return MethodOutput(
            method_id=self.method_id,
            action=RuntimeAction.FALLBACK,
            selected_candidate_id=None,
            plan_source=PlanSource.BASELINE_FALLBACK_PLAN,
            interaction_phase=(
                self.adapter.state.phase
                if self.adapter.state is not None
                else self._initial_phase()
            ),
            decision_reason=(reason,),
            runtime_evidence_ids=("stage6b-runtime-fallback",),
            freshness=Freshness.NOT_APPLICABLE,
            forward_accounting=accounting,
        )

    @staticmethod
    def _initial_phase():
        from driveclarify_paper_mvp_evaluation.contracts import InteractionPhase

        return InteractionPhase.INITIAL

    def _episode(self) -> PolicyEpisodeInput:
        if self._latest_episode is None:
            raise Stage6BContractError("LATEST_POLICY_EPISODE_INPUT_UNAVAILABLE")
        return self._latest_episode

    def _ego_position(self) -> tuple[float, float] | None:
        episode = self._latest_episode
        if episode is None:
            return None
        return (
            episode.ego_state.position_x_m,
            episode.ego_state.position_y_m,
        )

    # ------------------------------------------------------------------
    # Persistence and native panel projection
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        _atomic_json(
            self.stage6b_output_dir / STAGE6B_AUDIT_FILENAME,
            dict(self.summary()),
        )

    def summary(self) -> Mapping[str, Any]:
        adapter_summary = (
            None
            if self.adapter.state is None
            else self.adapter.finalize_episode()
        )
        forward_accounting = {
            "decision_events": (
                []
                if adapter_summary is None
                else [
                    item["forward_accounting"]
                    for item in adapter_summary["outputs"]
                ]
            ),
            "all_event_budgets_compliant": bool(
                adapter_summary is not None
                and all(
                    item.forward_accounting.budget_compliant
                    for item in self.adapter.state.outputs  # type: ignore[union-attr]
                )
            ),
        }
        compute = {
            "normal_model_forwards": self._normal_model_forwards,
            "candidate_model_forwards": self._candidate_model_forwards,
            "existing_pid_invocations": self._existing_pid_invocations,
            "new_pid_invocations": 0,
            "planner_advances": self._normal_model_forwards,
            "adapter_extra_planner_advances": 0,
            "authority_receipts": self._authority_receipts,
            "candidate_commits": self._candidate_commits,
            "candidate_direct_control_writes": 0,
            "m3_direct_control_writes": 0,
            "vehicle_control_ownership_violations": 0,
        }
        return {
            "schema_version": STAGE6B_SCHEMA,
            "enabled": True,
            "terminal": self._terminal,
            "termination_reason": (
                None
                if self.adapter.state is None
                else self.adapter.state.termination_reason
            ),
            "episode_id": self.episode_id,
            "method_id": self.method_id,
            "runtime_fixture_id": self.runtime_fixture_id,
            "seed": self.seed,
            "raw_instruction": self.raw_instruction,
            "original_simlingo_contract": {
                "raw_ambiguous_instruction_received": True,
                "driveclarify_resolved_interpretation_received": False,
                "candidate_forward_count": (
                    self._candidate_model_forwards
                    if self.method_id == MethodId.ORIGINAL_SIMLINGO.value
                    else None
                ),
            },
            "method_adapter": adapter_summary,
            "decision_trace": list(self._decision_trace),
            "query_trace": list(self._query_trace),
            "wait_trace": list(self._wait_trace),
            "candidate_trace": (
                {
                    "raw_k": 0,
                    "effective_k": 0,
                    "status": "UNAVAILABLE_BY_DESIGN_FOR_METHOD",
                }
                if self._candidate_audit is None
                else self._candidate_audit.to_dict()
            ),
            "interaction": dict(self._desired_interaction_summary()),
            "compute": compute,
            "forward_accounting": forward_accounting,
            "pid_accounting": {
                "existing_pid_invocations": self._existing_pid_invocations,
                "new_pid_invocations": 0,
                "existing_pid_only": True,
            },
            "label_firewall": self.firewall.to_dict(),
            "safety_accumulator": self.safety_accumulator.to_dict(),
            "movement": {
                "full_brake_control_count": self._full_brake_count,
                "zero_speed_observation_count": self._zero_speed_count,
                "maximum_absolute_speed_mps": self._maximum_speed_mps,
                "all_observed_controls_full_brake": bool(
                    self._existing_pid_invocations > 0
                    and self._full_brake_count == self._existing_pid_invocations
                ),
            },
            "candidate_forwards": list(self._all_candidate_forwards),
            "runtime_errors": list(self._runtime_errors),
            "frame_trace_path": str(self._frame_trace_path),
            "visualization": {
                "enabled": self._visualizer is not None,
                "extra_model_forwards": 0,
                "extra_pid_invocations": 0,
                "extra_planner_advances": 0,
                "vehicle_control_mutations": 0,
                "summary": (
                    None
                    if self._visualizer is None
                    else dict(self._visualizer.summary())
                ),
            },
        }

    def _visualization_record(self) -> Mapping[str, Any]:
        canonical = (
            () if self._semantic_set is None else self._semantic_set.canonical
        )
        audit = self._candidate_audit
        candidates = []
        for item in canonical:
            forward = self._current_forwards.get(item.candidate_id)
            candidates.append(
                {
                    "candidate_id": item.candidate_id,
                    "route_semantic": item.target_branch or item.target_landmark or item.referent,
                    "stop_status": "N/A",
                    "route": () if forward is None else forward.plan.route,
                    "semantics": item.to_dict(),
                }
            )
        output = self._current_output
        wait = self._wait_lifecycle
        return {
            "dashboard_title": "STAGE 6B UNIFIED | ONE BACKEND | EIGHT POLICIES | ONE EXISTING PID",
            "source_identity": {"source_frame_id": self._latest_frame},
            "instruction": {
                "raw": self.raw_instruction,
                "interpretation_a": (
                    canonical[0].description if len(canonical) > 0 else "N/A"
                ),
                "interpretation_b": (
                    canonical[1].description if len(canonical) > 1 else "N/A"
                ),
            },
            "candidates": candidates,
            "candidate_semantics": {
                "raw_k": 0 if audit is None else audit.raw_k,
                "effective_k": 0 if audit is None else audit.effective_k,
                "semantic_duplicate": False if audit is None else audit.semantic_duplicate,
                "grounding_duplicate": False if audit is None else audit.grounding_duplicate,
                "action_divergence": False if audit is None else audit.action_divergence,
                "trajectory_divergence": None if audit is None else audit.trajectory_divergence,
                "consequence_divergence": None if audit is None else audit.consequence_divergence,
                "canonical_interpretations": [
                    item.to_dict() for item in canonical
                ],
            },
            "baseline": {"route": self._baseline_visual_route},
            "counterfactual_matrix": {"cells": []},
            "m2b": {
                "producer_action": (
                    "NOT_EXECUTED" if output is None else output.action.value
                )
            },
            "m3": {"current_state": "UNIFIED_METHOD_ADAPTER"},
            "physical_wait_v0": {
                "status": (
                    "INACTIVE"
                    if output is None
                    else "ACTIVE"
                    if output.action in {RuntimeAction.ASK, RuntimeAction.WAIT}
                    else output.action.value
                ),
                "lease_elapsed_s": (
                    0
                    if wait is None or wait.start_simulation_time is None
                    else self._last_simulation_time - wait.start_simulation_time
                ),
                "lease_remaining_s": (
                    0
                    if wait is None or wait.start_simulation_time is None
                    else max(
                        0.0,
                        wait.maximum_duration_seconds
                        - (self._last_simulation_time - wait.start_simulation_time),
                    )
                ),
                "wait_entry_frame": (
                    "N/A" if wait is None else self._latest_frame
                ),
                "current_frame": self._latest_frame,
                "distance_travelled_m": (
                    0 if wait is None or wait.distance_m is None else wait.distance_m
                ),
                "carla_ticks_during_wait": 0,
                "candidate_commit_status": self._last_selected_plan_source,
                "m3_lifecycle_state": "UNIFIED_METHOD_ADAPTER",
                "m3_authority": "EXISTING_BASELINE_PID",
            },
            "ask_replanning_v0": {"enabled": False},
            "limited_act_commit_v0": {"enabled": False},
            "performance": {
                "candidate_model_forward_count": self._candidate_model_forwards,
                "existing_pid_invocation_count": self._existing_pid_invocations,
                "visualization_render_ms": self._visualization_last_render_ms,
            },
        }


def build_stage6b_simlingo_binding(
    agent: Any,
    output_dir: str | os.PathLike[str],
) -> Stage6BUnifiedSimLingoBinding:
    return Stage6BUnifiedSimLingoBinding(agent, output_dir)


__all__ = [
    "STAGE6B_AUDIT_FILENAME",
    "STAGE6B_EPISODE_ENV",
    "STAGE6B_FIXTURE_ENV",
    "STAGE6B_FRAME_TRACE_FILENAME",
    "STAGE6B_INFORMATION_EXPECTED_ENV",
    "STAGE6B_LIVE_ENV",
    "STAGE6B_METHOD_ENV",
    "STAGE6B_OUTPUT_ENV",
    "STAGE6B_RAW_INSTRUCTION_ENV",
    "STAGE6B_SCHEMA",
    "STAGE6B_SEED_ENV",
    "STAGE6B_VISUALIZATION_ENV",
    "Stage6BUnifiedSimLingoBinding",
    "build_stage6b_simlingo_binding",
]
