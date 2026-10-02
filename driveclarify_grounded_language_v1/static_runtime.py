"""Referential ACT/ASK runtime over real rgb_0 and one common M2B policy."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from driveclarify_candidate_stability.sensitivity_metrics import analyze_sensitivity
from driveclarify_language_grounding_v1.candidate_pipeline import GroundedCandidatePipeline
from driveclarify_language_grounding_v1.contracts import GroundedCandidate
from driveclarify_language_grounding_v1.runtime import _EpisodeView, _EgoView, _VisionView, _runtime_candidate
from driveclarify_language_grounding_v1.slot_parser import SemanticSlotParser
from driveclarify_language_grounding_v1.visual_grounder import GroundingDinoVisualGrounder
from driveclarify_m3_minimal_core import EventType, EvidenceGrade, MinimalM3Event, MinimalM3State, ProcessingResult, reduce_event
from driveclarify_m3_runtime_shadow.limited_act_commit_v0 import LimitedActCommitV0, _tensor_digest
from driveclarify_m3_runtime_shadow.physical_wait_v0 import PhysicalWaitExecutorV0, build_bounded_wait_pilot_binding
from driveclarify_m3_live_authority import CandidateLiveActAuthorityResolverV0
from driveclarify_paper_mvp_runtime.simlingo_binding import SimLingoCandidateForwardProvider, _points, _scalar
from driveclarify_temporal_grounding_v1.target_binding import RuntimeRouteTargetBinder

from .contracts import ANSWER_DELAY_ENV, ANSWER_ENV, CONTROL_ENV, DEVICE_ENV, RECEIPT_FILENAME, RUNTIME_VERSION, canonical_sha256
from .decision_engine import UnifiedTriadDecisionEngine
from .visualization import close_native_window, render_panel


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def _front(input_data: Any) -> tuple[int, Any]:
    import numpy as np

    entry = input_data["rgb_0"]
    frame = int(entry[0]) if isinstance(entry, (tuple, list)) else -1
    image = entry[1] if isinstance(entry, (tuple, list)) else entry
    value = np.asarray(image)
    if value.ndim != 3 or value.shape[2] < 3:
        raise ValueError("GROUNDED_LANGUAGE_RGB_0_INVALID")
    return frame, np.ascontiguousarray(value).copy()


def _xy(value: Any) -> Optional[tuple[float, float, float]]:
    try:
        sequence = value.detach().cpu().reshape(-1).tolist() if hasattr(value, "detach") else value
        if hasattr(sequence, "tolist"):
            sequence = sequence.tolist()
        return (float(sequence[0]), float(sequence[1]), 0.0)
    except Exception:
        return None


def _control_values(control: Any) -> dict[str, Any]:
    return {
        name: getattr(control, name)
        for name in ("steer", "throttle", "brake", "hand_brake", "reverse", "manual_gear_shift", "gear")
        if hasattr(control, name)
    }


def _rmse(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> float:
    values = [(float(a) - float(b)) ** 2 for lrow, rrow in zip(left, right) for a, b in zip(lrow, rrow)]
    return math.sqrt(sum(values) / len(values)) if values else 0.0


def _m3_event(
    *, event_id: str, event_type: EventType, now: float, query_id: str | None, simulation_time: float,
    candidate_set_id: str | None = None, candidate_freshness: str = "UNKNOWN", answer_present: bool = False,
) -> MinimalM3Event:
    payload = {
        "candidate_set_id": candidate_set_id,
        "candidate_freshness": candidate_freshness,
        "act_evidence_grade": EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value,
        "holding_evidence_grade": EvidenceGrade.NOT_CURRENTLY_AVAILABLE.value,
        "lease": None,
        "answer_present": answer_present,
        "baseline_authority_eligible": True,
        "physical_mode_ready": False,
        "reason_code_authorizes_control": False,
        "model_forward_requested": False,
        "low_level_control_requested": False,
    }
    if event_type is EventType.CANDIDATES_READY:
        payload["answer_deadline_monotonic"] = now + 5.0
        payload["decision_deadline_monotonic"] = now + 5.0
    return MinimalM3Event.create(
        event_id=event_id,
        event_type=event_type,
        query_episode_id=query_id,
        source_component=RUNTIME_VERSION,
        observed_monotonic_time=now,
        source_simulation_time=simulation_time,
        payload=payload,
    )


class ReferentialGroundedRuntime:
    enabled = True

    def __init__(
        self,
        agent: Any,
        output_dir: str,
        *,
        raw_instruction: str,
        trigger_frame: int | None,
        forward_provider_class: type[Any] = SimLingoCandidateForwardProvider,
    ) -> None:
        self.agent = agent
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_instruction = raw_instruction
        self.trigger_frame = trigger_frame
        # RQ2-T 2A 的完整收据可达数十 MB。每 tick 重写整个 JSON 与控制
        # 计算无关，并产生随 episode 长度线性增长的同步 I/O。科学 owner
        # 启用时只在 close() 原子发布一次；内存中的字段与计算顺序不变。
        self._defer_full_receipt_io = _truthy(
            os.environ.get("DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED")
        )
        self._force_receipt_persist = False
        self._deferred_full_receipt_write_count = 0
        self.control_enabled = _truthy(os.environ.get(CONTROL_ENV))
        self.answer_text = os.environ.get(ANSWER_ENV, "The nearer white van.")
        self.answer_delay_sim_seconds = float(os.environ.get(ANSWER_DELAY_ENV, "0.1"))
        self.parser = SemanticSlotParser()
        self.pipeline = GroundedCandidatePipeline()
        self.detector = GroundingDinoVisualGrounder(device=os.environ.get(DEVICE_ENV, "cuda"))
        self.target_binder = RuntimeRouteTargetBinder()
        self.forward_provider = forward_provider_class(agent)
        self.engine = UnifiedTriadDecisionEngine()
        self.wait = PhysicalWaitExecutorV0(enabled=True)
        self.limited_act = LimitedActCommitV0(
            self.output_dir,
            authority_resolver=CandidateLiveActAuthorityResolverV0(enabled=self.control_enabled),
        )
        self.agent.custom_prompt = raw_instruction
        self.agent.user_flag = 1
        self._processed = False
        self._plans_ready = False
        self._terminal = False
        self._candidate_set = None
        self._bound_candidates: list[dict[str, Any]] = []
        self._plan_rows: list[dict[str, Any]] = []
        self._latest_frame: int | None = None
        self._latest_observation_id: str | None = None
        self._latest_simulation_time = 0.0
        self._latest_speed = 0.0
        self._latest_position = None
        self._latest_image = None
        self._latest_image_sha = None
        self._decision = None
        self._final_decision = None
        self._query_started_sim_time: float | None = None
        self._query_started_frame: int | None = None
        self._answer_received_sim_time: float | None = None
        self._resolved_index: int | None = None
        self._replan_pending = False
        self._fresh_forward = None
        self._m3_state = None
        self._m3_events: list[dict[str, Any]] = []
        self._m3_results: list[dict[str, Any]] = []
        self._normal_forwards = 0
        self._candidate_forwards = 0
        self._pid_invocations = 0
        self._control_observations = 0
        self._dashboard_refresh_count = 0
        self._dashboard_native_refresh_count = 0
        self._carla_tick_count = 0
        self._visualization_errors: list[dict[str, Any]] = []
        # The hook's on_control argument is the control returned by run_step.
        # agent.control may intentionally retain SimLingo's startup-delay brake
        # even while run_step returns the freshly computed control to CARLA.
        self._last_returned_control: dict[str, Any] | None = None
        self._errors: list[dict[str, Any]] = []
        self._receipt: dict[str, Any] = {
            "schema_version": "driveclarify.grounded_language_v1.live_receipt.v1",
            "runtime_version": RUNTIME_VERSION,
            "status": "WAITING_FOR_REAL_RGB_0",
            "feature_flag_default": "OFF",
            "control_mode": "BOUNDED_CLOSED_LOOP" if self.control_enabled else "NO_CONTROL_SHADOW",
            "train_only": True,
            "mechanism_coverage_pilot": True,
            "performance_evaluation": False,
            "raw_instruction": raw_instruction,
            "forced_decision_count": 0,
            "manual_override_count": 0,
            "gold_policy_label_reads": 0,
            "expected_decision_reads": 0,
            "gold_candidate_index_reads": 0,
            "evaluation_label_reads": 0,
            "privileged_state_policy_read_count": 0,
            "label_firewall": {
                "carla_actor_id_reads": 0,
                "actor_transform_reads": 0,
                "actor_velocity_truth_reads": 0,
                "simulator_bbox_truth_reads": 0,
                "gold_intended_referent_reads": 0,
                "gold_act_ask_wait_reads": 0,
                "dev_reads": 0,
                "test_reads": 0,
            },
            "new_pid_count": 0,
            "candidate_direct_vehicle_control_write_count": 0,
            "m3_direct_vehicle_control_write_count": 0,
            "visualization_induced_detector_forward_count": 0,
            "visualization_induced_simlingo_forward_count": 0,
            "visualization_induced_pid_count": 0,
            "visualization_induced_planner_advance_count": 0,
            "visualization_induced_vehicle_control_mutation_count": 0,
            "errors": self._errors,
        }
        self._persist()

    def _persist(self) -> None:
        self._receipt.update(
            {
                "normal_simlingo_forward_count": self._normal_forwards,
                "candidate_simlingo_forward_count": self._candidate_forwards,
                "existing_pid_invocation_count": self._pid_invocations,
                "control_observation_count": self._control_observations,
                "dashboard_refresh_count": self._dashboard_refresh_count,
                "dashboard_native_refresh_count": self._dashboard_native_refresh_count,
                "carla_tick_count": self._carla_tick_count,
                "visualization_error_count": len(self._visualization_errors),
                "visualization_errors": self._visualization_errors,
                "m3_lifecycle": {"events": self._m3_events, "results": self._m3_results, "final_state": None if self._m3_state is None else self._m3_state.to_dict()},
                "limited_act": dict(self.limited_act.summary()),
                "dev_attempt_count": 0,
                "test_attempt_count": 0,
                "test_consumed": False,
            }
        )
        self._receipt["full_receipt_io_deferred_until_close"] = bool(
            self._defer_full_receipt_io
        )
        self._receipt["deferred_full_receipt_write_count"] = int(
            self._deferred_full_receipt_write_count
        )
        if self._defer_full_receipt_io and not self._force_receipt_persist:
            self._deferred_full_receipt_write_count += 1
            return
        _atomic_json(self.output_dir / RECEIPT_FILENAME, self._receipt)

    def on_tick(self, input_data: Any, tick_data: Any, timestamp: Any, frame: Any, observation_id: Any) -> None:
        try:
            sensor_frame, image = _front(input_data)
            frame_id = int(frame)
            if sensor_frame != frame_id:
                raise RuntimeError("GROUNDED_LANGUAGE_SENSOR_FRAME_MISMATCH")
            self._latest_frame = frame_id
            self._carla_tick_count += 1
            self._latest_observation_id = str(observation_id)
            self._latest_simulation_time = float(timestamp)
            self._latest_speed = float(_scalar(tick_data.get("speed")) or 0.0)
            self._latest_position = _xy(tick_data.get("gps"))
            self._latest_image = image
            self._latest_image_sha = hashlib.sha256(image.tobytes(order="C")).hexdigest()
            actual = self._last_returned_control
            self.limited_act.observe_tick(frame=frame_id, snapshot={"ego_position": self._latest_position, "ego_speed_mps": self._latest_speed, "actual_control": actual})
            if actual:
                self.wait.observe_actual_carla_control(source_frame=frame_id - 1, actual_control=actual)
            if not self._processed and (self.trigger_frame is None or frame_id >= self.trigger_frame):
                self._ground(image)
            if self._query_started_sim_time is not None and self._answer_received_sim_time is None:
                self._poll_answer()
            self._update_terminal_status()
            self._render()
            self._persist()
        except Exception as exc:
            self._errors.append({"stage": "ON_TICK", "frame": self._latest_frame, "type": type(exc).__name__, "message": str(exc)})
            if type(exc).__name__ == "OutOfMemoryError":
                self._receipt["status"] = "BLOCKED_GROUNDED_PERCEPTION_COMPUTE"
            else:
                self._receipt["status"] = "BLOCKED_GROUNDED_ASK_INTERACTION" if self._query_started_sim_time is not None else "BLOCKED_GROUNDED_TARGET_BINDING_COLLAPSE"
            self._terminal = True
            self._persist()

    def _ground(self, image: Any) -> None:
        self._processed = True
        assert self._latest_frame is not None and self._latest_observation_id is not None
        parsed = self.parser.parse(self.raw_instruction)
        phrase = parsed.referent_phrase or parsed.landmark_phrase
        if not phrase:
            raise RuntimeError("NO_VISUAL_REFERENT_PHRASE")
        started = time.monotonic()
        grounding = self.detector.ground(image, phrase, frame_id=self._latest_frame, observation_id=self._latest_observation_id, captured_monotonic=time.monotonic())
        constructed = self.pipeline.construct(parsed, grounding)
        target = self.target_binder.bind(getattr(getattr(self.agent, "_route_planner", None), "route", None))
        if target is None:
            raise RuntimeError("TARGET_UNRESOLVED_NO_EXECUTABLE_RUNTIME_TURN")
        if constructed.effective_k < 1:
            self._receipt.update({"status": "BLOCKED_VISUAL_REFERENT_GROUNDING", "raw_k": constructed.raw_k, "effective_k": constructed.effective_k})
            self._terminal = True
            return
        self._candidate_set = constructed
        self._bound_candidates = []
        equivalent_execution = parsed.maneuver == "CONTINUE"
        selected_by_id = {
            item.local_object_id: item for item in grounding.selected_referents
        }
        for candidate in constructed.candidates:
            referent = selected_by_id.get(candidate.grounded_referent_id)
            event_id = candidate.grounded_referent_id + ":PASSED"
            target_id = target.target_id if equivalent_execution else "target-" + canonical_sha256({"branch": target.branch_id, "event": event_id})[:20]
            self._bound_candidates.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "interpretation_id": candidate.interpretation_id,
                    "referent_id": candidate.grounded_referent_id,
                    "event_id": event_id,
                    "target_id": target_id,
                    "branch_id": target.branch_id,
                    "target_landmark": candidate.grounded_referent_id,
                    "maneuver": candidate.maneuver,
                    "referring_expression": candidate.referring_expression,
                    "prompt_text": candidate.prompt_text,
                    "conditioning_hash": canonical_sha256(candidate.prompt_text),
                    "source_observation_id": candidate.source_observation_id,
                    "source_frame_id": candidate.source_frame_id,
                    "semantic_sha256": candidate.semantic_sha256,
                    "grounding_sha256": candidate.grounding_sha256,
                    "referent_phrase": candidate.referent_phrase,
                    "ordering": candidate.ordering,
                    "bbox_xyxy": None if referent is None else list(referent.bbox_xyxy),
                    "detector_confidence": None if referent is None else referent.detector_confidence,
                    "relative_image_location": None if referent is None else referent.relative_image_location,
                    "track_id": None,
                }
            )
        self._receipt.update(
            {
                "status": "GROUNDED_CANDIDATES_TARGET_BOUND",
                "ambiguity_type": parsed.ambiguity_kind.value,
                "parsed_slots": parsed.to_dict(),
                "grounding": grounding.to_dict(),
                "raw_k": constructed.raw_k,
                "effective_k": constructed.effective_k,
                "exact_duplicate": False,
                "semantic_duplicate": constructed.semantic_duplicate,
                "grounding_duplicate": constructed.grounding_duplicate,
                "target_duplicate": len({item["target_id"] for item in self._bound_candidates}) < len(self._bound_candidates),
                "target_binding_receipts": self._bound_candidates,
                "target_branch": target.to_dict(),
                "candidate_construction_latency_seconds": time.monotonic() - started,
                "detector_latency_seconds": grounding.detector_latency_seconds,
            }
        )

    def on_model_output(self, baseline_route: Any, baseline_speed: Any, model_start: float, model_end: float) -> None:
        del model_start, model_end
        self._normal_forwards += 1
        try:
            if self._terminal:
                self._persist()
                return
            if self._candidate_set is not None and not self._plans_ready:
                self._run_initial_candidate_plans(baseline_route, baseline_speed)
            elif self._replan_pending and self._fresh_forward is None:
                self._run_fresh_replan()
            self._persist()
        except Exception as exc:
            self._errors.append({"stage": "ON_MODEL_OUTPUT", "frame": self._latest_frame, "type": type(exc).__name__, "message": str(exc)})
            self._receipt["status"] = "BLOCKED_GROUNDED_ASK_INTERACTION" if self._query_started_sim_time is not None else "BLOCKED_GROUNDED_POLICY_ASK_UNREACHABLE"
            self._terminal = True
            self._persist()

    def _forward(self, candidate: GroundedCandidate, repetition_id: str, *, latest: bool = False) -> Any:
        runtime_candidate = _runtime_candidate(candidate, repetition_id)
        if latest:
            runtime_candidate = replace(
                runtime_candidate,
                source_observation_id=str(self._latest_observation_id),
                source_frame_id=int(self._latest_frame),
                candidate_input_digest=canonical_sha256(
                    {"resolved": repetition_id, "observation": self._latest_observation_id}
                ),
            )
        episode = _EpisodeView(
            ego_state=_EgoView(self._latest_speed),
            vision_observation=_VisionView(str(self._latest_observation_id), int(self._latest_frame)),
        )
        self.forward_provider.begin_event()
        result = self.forward_provider(episode, runtime_candidate)
        self._candidate_forwards += 1
        return result

    @staticmethod
    def _plan_receipt(result: Any, label: str) -> dict[str, Any]:
        route = _points(result.raw_route)
        speed = _points(result.raw_speed)
        return {
            "candidate_id": label,
            "route": route,
            "speed": speed,
            "route_sha256": canonical_sha256(route),
            "speed_sha256": canonical_sha256(speed),
            "raw_route_tensor_sha256": _tensor_digest(result.raw_route),
            "raw_speed_tensor_sha256": _tensor_digest(result.raw_speed),
            "latency_seconds": result.plan.latency_s,
            "forward_evidence": dict(result.forward_evidence),
            "forwarded_prompt_text": result.forward_evidence.get(
                "forwarded_prompt_text"
            ),
            "forwarded_prompt_sha256": result.forward_evidence.get(
                "forwarded_prompt_sha256"
            ),
        }

    def _run_initial_candidate_plans(self, baseline_route: Any, baseline_speed: Any) -> None:
        assert self._candidate_set is not None and self._latest_observation_id is not None
        records = []
        results = {}
        rows = []
        for repetition in range(1, 4):
            for group, candidate in zip(("A", "B"), self._candidate_set.candidates):
                label = group + str(repetition)
                result = self._forward(candidate, label)
                receipt = self._plan_receipt(result, label)
                rows.append(receipt)
                results[label] = result
                records.append({"candidate_id": label, "interpretation_id": group, "route": receipt["route"], "speed": receipt["speed"]})
        if len(self._candidate_set.candidates) >= 2:
            metrics = analyze_sensitivity(records)
        else:
            metrics = {
                "protocol": "SINGLE_REFERENT_NOT_APPLICABLE",
                "final_status": "NOT_APPLICABLE_SINGLE_REFERENT",
                "candidate_signal_identified": False,
                "repeat_count_per_group": 3,
                "channels": {},
                "outlier_candidates": [],
            }
        self._plan_rows = rows
        self._plans_ready = True
        targets_unique = (
            len(self._bound_candidates) >= 2
            and len({item["target_id"] for item in self._bound_candidates}) == 2
        )
        material = bool(metrics["candidate_signal_identified"] and targets_unique)
        relation = "TASK_CRITICAL" if material else "TASK_EQUIVALENT"
        information_source = "PASSENGER" if relation == "TASK_CRITICAL" else "NONE"
        phrase = str(self._receipt.get("parsed_slots", {}).get("referent_phrase") or "object")
        question = self._build_question(phrase)
        self._decision = self.engine.decide(
            decision_id="initial-" + canonical_sha256({"observation": self._latest_observation_id, "candidates": [item["candidate_id"] for item in self._bound_candidates]})[:20],
            episode_id=os.environ.get("DRIVECLARIFY_PROBE_RUN_ID", "grounded-language-v1"),
            observation_id=self._latest_observation_id,
            candidate_ids=[item["candidate_id"] for item in self._bound_candidates],
            consequence_relation=relation,
            information_source=information_source,
            query_text=question,
            query_labels=("NEARER", "FARTHER"),
            query_delay_seconds=self.answer_delay_sim_seconds,
            physical_safety_verified=relation == "TASK_EQUIVALENT",
        )
        action = self._decision.recommendation.decision.value
        why = (
            "One grounded referent yields one executable meaning. Clarification would not change the action."
            if action == "ACT" and len(self._bound_candidates) == 1
            else "Two plausible meanings lead to the same executable outcome. Asking would not change the action."
            if action == "ACT"
            else "Two plausible grounded targets lead to materially different plans. Passenger information can change the action."
            if action == "ASK"
            else "UNEXPECTED_POLICY_RESULT"
        )
        self._receipt.update(
            {
                "status": "NATURAL_{}_SHADOW_READY".format(action),
                "initial_decision": action,
                "decision_why": why,
                "candidate_plan_repetitions": rows,
                "plan_divergence": metrics,
                "route_divergence_rmse_A1_B1": _rmse(rows[0]["route"], rows[1]["route"]) if len(self._bound_candidates) >= 2 else None,
                "speed_divergence_rmse_A1_B1": _rmse(rows[0]["speed"], rows[1]["speed"]) if len(self._bound_candidates) >= 2 else None,
                "material_consequence_divergence": material,
                "consequence_relation": relation,
                "decision_engine": self._decision.to_dict(),
                "question": question if action == "ASK" else None,
            }
        )
        if action == "ASK":
            self._enter_ask(question)
        elif action == "ACT":
            if self.control_enabled:
                selected_result = results.get("A1") if len(self._bound_candidates) == 1 else None
                route = selected_result.raw_route if selected_result is not None else baseline_route
                speed = selected_result.raw_speed if selected_result is not None else baseline_speed
                self._arm_plan(
                    route,
                    speed,
                    plan_id="equivalence-class-" + canonical_sha256([item["target_id"] for item in self._bound_candidates])[:20],
                    interpretation_id=(
                        self._bound_candidates[0]["interpretation_id"]
                        if len(self._bound_candidates) == 1
                        else "equivalence-class-no-default-candidate"
                    ),
                    instruction=(
                        self._candidate_set.candidates[0].prompt_text
                        if len(self._bound_candidates) == 1
                        else self.raw_instruction
                    ),
                    model_route_digest=canonical_sha256(_points(route)),
                    model_speed_digest=canonical_sha256(_points(speed)),
                    projection=(
                        "SINGLE_GROUNDED_CANDIDATE_SIMLINGO_FORWARD"
                        if len(self._bound_candidates) == 1
                        else "CURRENT_BASELINE_PLAN_REPRESENTS_GROUNDED_EQUIVALENCE_CLASS"
                    ),
                )
            else:
                self._receipt["status"] = "SHADOW_NATURAL_ACT_PATH_PASS"
                self._terminal = True
        else:
            self._receipt["status"] = "BLOCKED_GROUNDED_POLICY_ASK_UNREACHABLE"
            self._terminal = True

    def _build_question(self, phrase: str) -> str:
        """Render a question from runtime-grounded descriptions only.

        Extension runtimes may make the already-derived topology distinction
        explicit.  No evaluator label or candidate index enters this seam.
        """

        return "Which {} do you mean—the visually nearer one or the visually farther one?".format(phrase)

    def _reduce_m3(self, event: MinimalM3Event) -> None:
        if self._m3_state is None:
            self._m3_state = MinimalM3State(baseline_authority_eligible=True, current_time_monotonic=event.observed_monotonic_time)
        result = reduce_event(self._m3_state, event, event.observed_monotonic_time)
        if result.processing_results != (ProcessingResult.PROCESSED,):
            raise RuntimeError("M3_INTERACTION_TRANSITION_REJECTED:" + event.event_type.value)
        self._m3_state = result.state
        self._m3_events.append(event.to_dict())
        self._m3_results.append(result.to_dict())

    def _enter_ask(self, question: str) -> None:
        assert self._latest_observation_id is not None and self._latest_frame is not None
        now = time.monotonic()
        set_id = "grounded-set-" + canonical_sha256(self._bound_candidates)[:20]
        query_id = self._answer_query_identity()
        self._reduce_m3(_m3_event(event_id=set_id + ":ready", event_type=EventType.CANDIDATES_READY, now=now, query_id=None, simulation_time=self._latest_simulation_time, candidate_set_id=set_id, candidate_freshness="FRESH"))
        self._reduce_m3(_m3_event(event_id=set_id + ":ask", event_type=EventType.DECISION_ASK, now=now, query_id=query_id, simulation_time=self._latest_simulation_time))
        binding = build_bounded_wait_pilot_binding(
            run_id=set_id,
            source_observation_id=self._latest_observation_id,
            source_frame_id=str(self._latest_frame),
            candidate_set_id=set_id,
            start_monotonic_time=now,
            duration_s=30.0,
            source_simulation_time=self._latest_simulation_time,
            query_episode_id=query_id,
        )
        entered = self.wait.enter(
            binding,
            current_monotonic_time=now,
            frame=self._latest_frame,
            ego_position=self._latest_position,
            ego_speed_mps=self._latest_speed,
            baseline_control_path_healthy=True,
            no_control_ownership_conflict=True,
            native_carla_session_available=True,
            source_identity_recordable=True,
            environment_digest=self._latest_image_sha,
        )
        if not entered:
            raise RuntimeError("ASK_HOLDING_ENTRY_FAILED")
        self._query_started_sim_time = self._latest_simulation_time
        self._query_started_frame = self._latest_frame
        self._receipt.update(
            {
                "status": "ASK_QUERY_EMITTED_HOLDING",
                "query_start_frame": self._latest_frame,
                "query_start_simulation_time": self._latest_simulation_time,
                "answer_eta_simulation_time": self._latest_simulation_time + self.answer_delay_sim_seconds,
                "holding_owner": "EXISTING_BASELINE_PID_CURRENT_VALID_PLAN",
                "question": question,
            }
        )

    def _answer_query_identity(self) -> Any:
        """Return the query identity owned by this runtime's answer lifecycle."""

        return self._decision.recommendation.query_id

    def _poll_answer(self) -> None:
        assert self._query_started_sim_time is not None and self._latest_frame is not None
        self.wait.observe_tick(
            frame=self._latest_frame,
            current_monotonic_time=time.monotonic(),
            ego_position=self._latest_position,
            ego_speed_mps=self._latest_speed,
            baseline_control_path_healthy=True,
            source_runtime_valid=True,
            environment_digest=self._latest_image_sha,
            source_simulation_time=self._latest_simulation_time,
        )
        if self._latest_simulation_time - self._query_started_sim_time + 1e-9 < self.answer_delay_sim_seconds:
            return
        self._resolved_index = self._resolve_answer_index()
        self._answer_received_sim_time = self._latest_simulation_time
        now = time.monotonic()
        query_id = self._answer_query_identity()
        self._reduce_m3(_m3_event(event_id=str(query_id) + ":answer", event_type=EventType.ANSWER_ARRIVED, now=now, query_id=query_id, simulation_time=self._latest_simulation_time, answer_present=True))
        self.wait.observe_tick(
            frame=self._latest_frame,
            current_monotonic_time=now,
            ego_position=self._latest_position,
            ego_speed_mps=self._latest_speed,
            baseline_control_path_healthy=True,
            source_runtime_valid=False,
            environment_digest=self._latest_image_sha,
            source_simulation_time=self._latest_simulation_time,
        )
        self._replan_pending = True
        self._receipt.update(
            {
                "status": "ASK_ANSWER_RECEIVED_CANDIDATES_INVALIDATED",
                "answer": self.answer_text,
                "answer_label_exposed_to_policy": False,
                "answer_received_frame": self._latest_frame,
                "answer_arrival_simulation_time": self._latest_simulation_time,
                "answer_delay_simulation_seconds": self._latest_simulation_time - self._query_started_sim_time,
                "old_candidate_set_invalidated": True,
                "cache_invalidation_reason": "ASK_ANSWER",
                "answer_resolution": self._bound_candidates[self._resolved_index]["referring_expression"],
            }
        )

    def _resolve_answer_index(self) -> int:
        """Resolve the frozen legacy near/far answer vocabulary.

        Persistent semantic runtimes override this label-free seam.  Keeping
        the original parser here makes the existing referential path byte-for-
        byte equivalent in meaning while avoiding duplicated answer lifecycle
        logic in subclasses.
        """

        normalized = self.answer_text.casefold()
        nearer = any(token in normalized for token in ("nearer", "nearest", "closer", "first"))
        farther = any(token in normalized for token in ("farther", "farthest", "second"))
        if nearer == farther:
            raise RuntimeError("LABEL_FREE_ANSWER_DID_NOT_RESOLVE_REFERENT")
        return 0 if nearer else 1

    def _run_fresh_replan(self) -> None:
        assert self._resolved_index is not None and self._candidate_set is not None and self._latest_observation_id is not None
        candidate = self._candidate_set.candidates[self._resolved_index]
        result = self._forward(candidate, "RESOLVED", latest=True)
        self._fresh_forward = result
        receipt = self._plan_receipt(result, "RESOLVED")
        now = time.monotonic()
        fresh_set_id = "fresh-set-" + canonical_sha256({"observation": self._latest_observation_id, "resolved": self._bound_candidates[self._resolved_index]["candidate_id"]})[:20]
        self._reduce_m3(_m3_event(event_id=fresh_set_id + ":revalidated", event_type=EventType.REVALIDATION_PASSED, now=now, query_id=None, simulation_time=self._latest_simulation_time))
        self._reduce_m3(_m3_event(event_id=fresh_set_id + ":replan", event_type=EventType.REPLAN_COMPLETE, now=now, query_id=None, simulation_time=self._latest_simulation_time, candidate_set_id=fresh_set_id, candidate_freshness="FRESH"))
        self._final_decision = self.engine.decide(
            decision_id="post-answer-" + fresh_set_id,
            episode_id=os.environ.get("DRIVECLARIFY_PROBE_RUN_ID", "grounded-language-v1"),
            observation_id=self._latest_observation_id,
            candidate_ids=(self._bound_candidates[self._resolved_index]["candidate_id"],),
            consequence_relation="TASK_CRITICAL",
            information_source="NONE",
            physical_safety_verified=True,
        )
        final_action = self._final_decision.recommendation.decision.value
        self._receipt.update(
            {
                "fresh_replan": receipt,
                "fresh_replan_source_observation_id": self._latest_observation_id,
                "fresh_replan_source_frame_id": self._latest_frame,
                "final_decision_engine": self._final_decision.to_dict(),
                "post_answer_decision": final_action,
                "answer_to_replan_latency_simulation_seconds": self._latest_simulation_time - float(self._answer_received_sim_time),
                "total_added_interaction_delay_simulation_seconds": self._latest_simulation_time - float(self._query_started_sim_time),
            }
        )
        if final_action != "ACT":
            self._receipt["status"] = "BLOCKED_GROUNDED_ASK_INTERACTION"
            self._terminal = True
            return
        self._replan_pending = False
        if self.control_enabled:
            self._arm_plan(
                result.raw_route,
                result.raw_speed,
                plan_id=fresh_set_id,
                interpretation_id=self._bound_candidates[self._resolved_index]["interpretation_id"],
                instruction=candidate.prompt_text,
                model_route_digest=receipt["route_sha256"],
                model_speed_digest=receipt["speed_sha256"],
                projection="FRESH_POST_ANSWER_SIMLINGO_CANDIDATE_FORWARD",
            )
        else:
            self._receipt["status"] = "SHADOW_NATURAL_ASK_CLOSED_LOOP_PASS"
            self._terminal = True

    def _arm_plan(
        self, route: Any, speed: Any, *, plan_id: str, interpretation_id: str, instruction: str,
        model_route_digest: str, model_speed_digest: str, projection: str,
    ) -> None:
        assert self._latest_frame is not None and self._latest_observation_id is not None
        route_points, speed_points = _points(route), _points(speed)
        replan = {
            "fresh_model_computation": True,
            "source_observation_id": self._latest_observation_id,
            "source_frame_id": str(self._latest_frame),
            "route_digest": _tensor_digest(route),
            "speed_digest": _tensor_digest(speed),
            "plan_id": plan_id,
            "resolved_interpretation_id": interpretation_id,
            "resolved_instruction_text": instruction,
            "route": [route_points],
            "speed": [speed_points],
            "model_route_digest": model_route_digest,
            "model_speed_digest": model_speed_digest,
            "model_output_dtype": str(getattr(route, "dtype", "UNKNOWN")),
            "execution_plan_dtype": str(getattr(route, "dtype", "UNKNOWN")),
            "execution_projection": projection,
        }
        armed = self.limited_act.arm_from_replan(
            replan,
            old_candidate=None,
            natural_m2b_action="ACT",
            current_frame=self._latest_frame,
            current_observation_id=self._latest_observation_id,
            current_snapshot={"ego_position": self._latest_position, "ego_speed_mps": self._latest_speed, "actual_control": _control_values(getattr(self.agent, "control", None))},
            active_query=False,
            active_wait=False,
            safety_active=False,
        )
        if not armed:
            raise RuntimeError("ACT_AUTHORITY_ARM_FAILED")
        self._receipt["status"] = "NATURAL_ACT_AUTHORITY_ARMED"

    def select_plan_source(self, baseline_route: Any, baseline_speed: Any, current_monotonic: float) -> tuple[Any, Any]:
        if not self.control_enabled:
            return baseline_route, baseline_speed
        return self.limited_act.select_plan_source(
            baseline_route,
            baseline_speed,
            frame=self._latest_frame,
            observation_id=self._latest_observation_id,
            current_monotonic=current_monotonic,
            active_query=False,
            active_wait=self.wait.active,
            safety_active=False,
        )

    def on_pid_invocation(self, current_monotonic: float) -> None:
        self._pid_invocations += 1
        self.limited_act.on_pid_invocation(frame=self._latest_frame, current_monotonic=current_monotonic)

    def on_control(self, control: Any, gt_velocity: Any, current_monotonic: float) -> None:
        del gt_velocity
        self._control_observations += 1
        self._last_returned_control = _control_values(control)
        if self.wait.active:
            self.wait.observe_baseline_control(control, frame=self._latest_frame, ready_time=current_monotonic)
        self.limited_act.on_control(control, frame=self._latest_frame, ready_time=current_monotonic)
        self._update_terminal_status()
        self._persist()

    def _update_terminal_status(self) -> None:
        limited = self.limited_act.summary()
        if str(limited.get("status", "")).startswith("PASS_LIMITED_CLOSED_LOOP"):
            initial = None if self._decision is None else self._decision.recommendation.decision.value
            self._receipt.update(
                {
                    "status": "BOUNDED_{}_TO_ACT_CLOSED_LOOP_PASS".format(initial),
                    "authority_receipt_consumed_exactly_once": limited.get("authority_receipts_consumed") == 1,
                    "baseline_ownership_restored": limited.get("baseline_ownership_returned") is True,
                    "actual_actuator_matches_authorized_plan_source": limited.get("actual_control_exact_match") is True,
                }
            )
            self._terminal = True
        elif str(limited.get("status", "")).startswith("BLOCKED_"):
            self._receipt["status"] = "BLOCKED_GROUNDED_ASK_INTERACTION" if self._query_started_sim_time is not None else "BLOCKED_GROUNDED_POLICY_ACT_UNREACHABLE"
            self._terminal = True

    def _render(self) -> None:
        action = (
            self._final_decision.recommendation.decision.value
            if self._final_decision is not None
            else self._decision.recommendation.decision.value
            if self._decision is not None
            else "UNKNOWN"
        )
        interaction = None
        if self._query_started_sim_time is not None:
            interaction = {
                "question": self._receipt.get("question"),
                "answer": self._receipt.get("answer", "PENDING"),
                "state": self._receipt.get("status"),
            }
        try:
            limited = dict(self.limited_act.summary())
            actual = dict(self._last_returned_control or {})
            display_candidates, display_plans, display_topology = (
                self._passive_visualization_payload()
            )
            result = render_panel(
                self.output_dir,
                self._latest_image,
                decision=action,
                why=str(self._receipt.get("decision_why", "Awaiting grounded decision")),
                instruction=self.raw_instruction,
                ambiguity_type=str(self._receipt.get("ambiguity_type", "REFERENTIAL")),
                candidates=display_candidates,
                decision_receipt=None if self._decision is None else self._decision.to_dict(),
                interaction=interaction,
                plans=display_plans,
                grounding={
                    **dict(self._receipt.get("grounding", {})),
                    "semantic_duplicate": self._receipt.get("semantic_duplicate"),
                    "grounding_duplicate": self._receipt.get("grounding_duplicate"),
                },
                topology=display_topology,
                topology_context=self._receipt.get("topology_context", {}),
                authority={
                    "plan_source": limited.get("selected_plan_source", "CURRENT_VALID_PLAN"),
                    "control_owner": limited.get("control_owner", "EXISTING_SIMLINGO_PID"),
                    "receipt_status": limited.get("status"),
                    "existing_pid_count": self._pid_invocations,
                    "new_pid_count": 0,
                    "candidate_direct_writes": 0,
                    "m3_direct_writes": 0,
                    "steer": actual.get("steer"),
                    "throttle": actual.get("throttle"),
                    "brake": actual.get("brake"),
                },
                decision_window=self._receipt.get("decision_window_dashboard"),
                runtime_status=str(self._receipt.get("status")),
                frame_id=self._latest_frame,
            )
            if result.get("rendered"):
                self._dashboard_refresh_count += 1
            if result.get("native_refreshed"):
                self._dashboard_native_refresh_count += 1
            if result.get("error"):
                self._visualization_errors.append({"frame": self._latest_frame, "error": result["error"]})
        except Exception as exc:
            self._visualization_errors.append(
                {"frame": self._latest_frame, "type": type(exc).__name__, "message": str(exc)}
            )

    def _passive_visualization_payload(self) -> tuple[list, list, list]:
        """Project already-computed evidence into the dashboard schema.

        This method is deliberately a read-only adapter.  It performs no model
        forward, topology/map query, planner call, PID invocation, or control
        write.  R4.4 semantic candidates and their latest persisted plan rows
        supersede the legacy referent/repetition display whenever present.
        """

        receipt = self._receipt if isinstance(self._receipt, dict) else {}
        production_plans = receipt.get("candidate_plan_repetitions") or ()
        display_plans = [
            dict(row) for row in production_plans if isinstance(row, dict)
        ]
        if not display_plans:
            display_plans = [
                dict(row) for row in self._plan_rows if isinstance(row, dict)
            ]

        supplier = receipt.get("scene_grounded_obligation_supplier") or {}
        supplier_rows = supplier.get("results") or ()
        semantic = receipt.get("r4_4_semantic_authority") or {}
        semantic_rows = semantic.get("candidates") or ()
        by_id = {
            str(row.get("candidate_id")): dict(row)
            for row in self._bound_candidates
            if isinstance(row, dict) and row.get("candidate_id")
        }
        for row in semantic_rows:
            if not isinstance(row, dict) or not row.get("candidate_id"):
                continue
            by_id.setdefault(str(row["candidate_id"]), {}).update(row)
        for row in supplier_rows:
            if not isinstance(row, dict) or not row.get("candidate_id"):
                continue
            by_id.setdefault(str(row["candidate_id"]), {}).update(row)

        obligations = getattr(self, "_candidate_local_navigation_obligations", {})
        if not isinstance(obligations, dict):
            obligations = {}
        candidate_order = [
            str(row.get("candidate_id"))
            for row in display_plans
            if row.get("candidate_id")
        ]
        if not candidate_order:
            candidate_order = list(by_id)
        display_candidates = []
        display_topology = []
        for index, candidate_id in enumerate(dict.fromkeys(candidate_order)):
            if len(display_candidates) >= 2:
                break
            row = dict(by_id.get(candidate_id, {"candidate_id": candidate_id}))
            obligation = obligations.get(candidate_id)
            if obligation is not None:
                row["semantic_constraint"] = str(
                    getattr(obligation, "maneuver_direction", None)
                    or row.get("semantic_constraint")
                    or "UNKNOWN"
                )
                row["branch_id"] = str(
                    getattr(obligation, "local_branch_identity", None)
                    or row.get("branch_id")
                    or "UNKNOWN"
                )
                row["qualification_status"] = str(
                    getattr(
                        getattr(obligation, "qualification_status", None),
                        "value",
                        getattr(obligation, "qualification_status", "UNKNOWN"),
                    )
                )
            constraint = str(row.get("semantic_constraint") or "UNKNOWN")
            branch_id = str(row.get("branch_id") or "UNKNOWN")
            branch_family = (
                branch_id.rsplit("-", 1)[0]
                if branch_id.startswith("branch-") and "-" in branch_id
                else branch_id
            )
            row["display_descriptor"] = "{} / {}".format(
                constraint, branch_family
            )
            display_candidates.append(row)
            display_topology.append(
                {
                    "route_order_index": index + 1,
                    "maneuver_direction": constraint,
                    "branch_id": branch_id,
                    "junction_id": row.get("topology_junction_id")
                    or row.get("junction_id"),
                    "candidate_id": candidate_id,
                    "evidence_source": "EXISTING_QUALIFIED_SEMANTIC_OBLIGATION",
                }
            )

        if not display_candidates:
            display_candidates = [
                dict(row) for row in self._bound_candidates if isinstance(row, dict)
            ]
        if not display_topology:
            display_topology = [
                dict(row)
                for row in receipt.get("maneuver_opportunities", ())
                if isinstance(row, dict)
            ]
        return display_candidates, display_plans, display_topology

    def commit(self) -> None:
        self._persist()

    def close(self) -> None:
        close_native_window()
        self._receipt["closed"] = True
        self._force_receipt_persist = True
        self._persist()

    def summary(self) -> dict[str, Any]:
        return {"enabled": True, "status": self._receipt.get("status"), "decision": self._receipt.get("initial_decision"), "candidate_forward_count": self._candidate_forwards, "new_pid": 0}


__all__ = ["ReferentialGroundedRuntime"]
