"""Temporal primitive routed through the same Grounded Language V1 M2B adapter."""

from __future__ import annotations

import os
from typing import Any

from driveclarify_temporal_grounding_v1.runtime import TemporalGroundingV1Runtime

from .contracts import RECEIPT_FILENAME, RUNTIME_VERSION, canonical_sha256
from .decision_engine import UnifiedTriadDecisionEngine
from .visualization import close_native_window, render_panel


class IntegratedTemporalGroundedRuntime(TemporalGroundingV1Runtime):
    """Reuse DINO+ByteTrack+event estimator, adding only the common M2B seam."""

    def __init__(self, agent: Any, output_dir: str) -> None:
        self._triad_engine = UnifiedTriadDecisionEngine()
        self._pre_policy = None
        self._post_policy = None
        super().__init__(agent, output_dir)
        self._dashboard_refresh_count = 0
        self._dashboard_native_refresh_count = 0
        self._unified_visualization_errors = []
        self._receipt.update(
            {
                "schema_version": "driveclarify.grounded_language_v1.live_receipt.v1",
                "runtime_version": RUNTIME_VERSION,
                "feature_flag_default": "OFF",
                "mechanism_coverage_pilot": True,
                "performance_evaluation": False,
                "forced_decision_count": 0,
                "manual_override_count": 0,
                "gold_policy_label_reads": 0,
                "expected_decision_reads": 0,
                "gold_candidate_index_reads": 0,
                "evaluation_label_reads": 0,
                "ambiguity_type": "TEMPORAL",
                "visualization_induced_planner_advance_count": 0,
                "visualization_induced_vehicle_control_mutation_count": 0,
            }
        )
        self._persist()

    def _persist(self) -> None:
        super()._persist()
        if not hasattr(self, "_receipt"):
            return
        self._receipt["unified_pre_information_decision"] = (
            None if self._pre_policy is None else self._pre_policy.to_dict()
        )
        self._receipt["unified_post_information_decision"] = (
            None if self._post_policy is None else self._post_policy.to_dict()
        )
        self._receipt["dashboard_refresh_count"] = getattr(
            self, "_dashboard_refresh_count", 0
        )
        self._receipt["dashboard_native_refresh_count"] = getattr(
            self, "_dashboard_native_refresh_count", 0
        )
        errors = getattr(self, "_unified_visualization_errors", [])
        self._receipt["visualization_error_count"] = len(errors)
        self._receipt["visualization_errors"] = errors
        # The base runtime already writes atomically; mirror its complete receipt
        # under the unified contract name without changing the temporal evidence.
        from driveclarify_temporal_grounding_v1.runtime import _atomic_json

        _atomic_json(self.output_dir / RECEIPT_FILENAME, self._receipt)

    def _create_pre_candidate(self, track: Any) -> None:
        super()._create_pre_candidate(track)
        if self._candidate_pre is None or self._terminal:
            return
        observed_id = self._candidate_pre.candidate_id + ":NOT_CLEARED"
        future_id = self._candidate_pre.candidate_id + ":CLEARED"
        self._pre_policy = self._triad_engine.decide(
            decision_id="temporal-pre-" + canonical_sha256(
                {"observation": self._latest_observation_id, "track": track.track_id}
            )[:20],
            episode_id=os.environ.get("DRIVECLARIFY_PROBE_RUN_ID", "grounded-language-v1"),
            observation_id=str(self._latest_observation_id),
            candidate_ids=(observed_id, future_id),
            consequence_relation="TASK_CRITICAL",
            information_source="ENVIRONMENT",
            expected_information_delay_seconds=0.5,
            physical_safety_verified=False,
        )
        action = self._pre_policy.recommendation.decision.value
        self._receipt.update(
            {
                "initial_decision": action,
                "decision_why": (
                    "Action depends on a future tracked scene event; environmental information is expected soon"
                ),
                "raw_k": 2,
                "effective_k": 2,
                "exact_duplicate": False,
                "semantic_duplicate": False,
                "grounding_duplicate": False,
                "target_duplicate": True,
                "target_binding_receipts": [
                    {
                        "candidate_id": observed_id,
                        "referent_id": self._candidate_pre.referent_id,
                        "event_id": self._candidate_pre.event_id,
                        "event_state": "NOT_CLEARED",
                        "target_id": self._candidate_pre.target_id,
                        "branch_id": self._candidate_pre.branch_id,
                        "maneuver": self._candidate_pre.maneuver,
                        "prompt_text": self._candidate_pre.prompt_text,
                        "conditioning_hash": canonical_sha256(self._candidate_pre.prompt_text),
                    },
                    {
                        "candidate_id": future_id,
                        "referent_id": self._candidate_pre.referent_id,
                        "event_id": self._candidate_pre.event_id,
                        "event_state": "CLEARED",
                        "target_id": self._candidate_pre.target_id,
                        "branch_id": self._candidate_pre.branch_id,
                        "maneuver": self._candidate_pre.maneuver,
                        "prompt_text": "Fresh plan only after confirmed CLEARED information update",
                        "conditioning_hash": canonical_sha256("Fresh plan only after confirmed CLEARED information update"),
                    },
                ],
            }
        )
        if action != "WAIT":
            self._receipt["status"] = "BLOCKED_GROUNDED_POLICY_WAIT_UNREACHABLE"
            self._terminal = True
        else:
            self._receipt["status"] = "WAIT_ACTIVE_TRACKING_TEMPORAL_CONDITION"

    def _ensure_post_policy(self) -> bool:
        if self._post_policy is not None:
            return self._post_policy.recommendation.decision.value == "ACT"
        if self._candidate_post is None:
            return False
        self._post_policy = self._triad_engine.decide(
            decision_id="temporal-post-" + self._candidate_post.candidate_set_id,
            episode_id=os.environ.get("DRIVECLARIFY_PROBE_RUN_ID", "grounded-language-v1"),
            observation_id=str(self._latest_observation_id),
            candidate_ids=(self._candidate_post.candidate_id,),
            consequence_relation="TASK_CRITICAL",
            information_source="NONE",
            physical_safety_verified=True,
        )
        self._receipt["post_information_decision"] = self._post_policy.recommendation.decision.value
        return self._post_policy.recommendation.decision.value == "ACT"

    def _arm_closed_loop(self) -> None:
        if not self._ensure_post_policy():
            self._receipt["status"] = "BLOCKED_GROUNDED_WAIT_INFORMATION_LIFECYCLE"
            self._terminal = True
            return
        super()._arm_closed_loop()

    def _run_fresh_replan(self) -> None:
        super()._run_fresh_replan()
        if not self._ensure_post_policy():
            self._receipt["status"] = "BLOCKED_GROUNDED_WAIT_INFORMATION_LIFECYCLE"
            self._terminal = True
        elif not self.control_enabled:
            self._receipt["status"] = "SHADOW_NATURAL_WAIT_CLOSED_LOOP_PASS"
        self._persist()

    def _update_terminal_status(self) -> None:
        super()._update_terminal_status()
        if self._receipt.get("status") == "BOUNDED_WAIT_TO_REPLAN_CLOSED_LOOP_PASS":
            self._receipt.update(
                {
                    "status": "BOUNDED_WAIT_TO_ACT_CLOSED_LOOP_PASS",
                    "authority_receipt_consumed_exactly_once": self.limited_act.summary().get(
                        "authority_receipts_consumed"
                    )
                    == 1,
                    "baseline_ownership_restored": self.limited_act.summary().get(
                        "baseline_ownership_returned"
                    )
                    is True,
                }
            )

    def _render(self) -> None:
        if self._event is None or self._target is None:
            return
        track = next(
            (item for item in self._tracks if item.track_id == self._primary_track_id),
            None,
        )
        if track is None:
            return
        event_id = (
            self._candidate_pre.event_id
            if self._candidate_pre is not None
            else str(track.track_id) + ":CLEARED"
        )
        common = {
            "referent_id": track.source_grounding_id,
            "event_id": event_id,
            "target_id": self._target.target_id,
            "branch_id": self._target.branch_id,
            "maneuver": "TURN",
            "referent_phrase": track.phrase,
            "track_id": track.track_id,
            "bbox_xyxy": list(track.bbox_xyxy),
            "confidence": track.confidence,
        }
        candidates = [
            {
                **common,
                "candidate_id": str(event_id) + ":NOT_CLEARED",
                "event_state": self._event.state.value,
                "referring_expression": "tracked bus / event pending",
            },
            {
                **common,
                "candidate_id": str(event_id) + ":CLEARED",
                "event_state": "CLEARED",
                "referring_expression": "same bus / confirmed cleared",
            },
        ]
        plans = []
        for label, key in (("A1", "pre_event_plan"), ("B1", "post_event_plan")):
            row = self._receipt.get(key)
            if isinstance(row, dict):
                plans.append({**row, "candidate_id": label})
        action = (
            "ACT"
            if self._post_policy is not None
            else "WAIT"
            if self._pre_policy is not None
            else "UNKNOWN"
        )
        timeline = [item.get("state") for item in self._event_timeline[-4:]]
        interaction = {
            "question": "Future visual event: bus CLEARED",
            "answer": "INFORMATION ARRIVED" if self._information is not None else "TRACKING ENVIRONMENT",
            "state": " → ".join(str(item) for item in timeline if item)
            or self._receipt.get("status"),
        }
        limited = dict(self.limited_act.summary())
        actual = dict(getattr(self, "_last_returned_control", None) or {})
        try:
            result = render_panel(
                self.output_dir,
                self._latest_image,
                decision=action,
                why=(
                    "The tracked visual event arrived. The stale plan was invalidated and a fresh plan can act."
                    if action == "ACT"
                    else "The action depends on a future visual event. The environment is expected to resolve it soon."
                ),
                instruction=self.raw_instruction,
                ambiguity_type="TEMPORAL",
                candidates=candidates,
                decision_receipt=(
                    self._post_policy.to_dict()
                    if self._post_policy is not None
                    else self._pre_policy.to_dict()
                    if self._pre_policy is not None
                    else None
                ),
                interaction=interaction,
                plans=plans,
                grounding={
                    "raw_grounding_k": self._receipt.get("raw_k", 1),
                    "effective_k": self._receipt.get("effective_k", 2),
                    "semantic_duplicate": False,
                    "grounding_duplicate": False,
                },
                topology=(self._target.to_dict(),),
                authority={
                    "plan_source": limited.get("selected_plan_source", "CURRENT_VALID_HOLDING_PLAN"),
                    "control_owner": self._receipt.get("control_owner", "EXISTING_SIMLINGO_PID"),
                    "receipt_status": limited.get("status", self._receipt.get("status")),
                    "existing_pid_count": self._pid_invocations,
                    "new_pid_count": 0,
                    "candidate_direct_writes": 0,
                    "m3_direct_writes": 0,
                    "steer": actual.get("steer"),
                    "throttle": actual.get("throttle"),
                    "brake": actual.get("brake"),
                },
                runtime_status=str(self._receipt.get("status")),
                frame_id=self._latest_frame,
            )
            if result.get("rendered"):
                self._dashboard_refresh_count += 1
            if result.get("native_refreshed"):
                self._dashboard_native_refresh_count += 1
            if result.get("error"):
                self._unified_visualization_errors.append(
                    {"frame": self._latest_frame, "error": result["error"]}
                )
        except Exception as exc:
            self._unified_visualization_errors.append(
                {"frame": self._latest_frame, "type": type(exc).__name__, "message": str(exc)}
            )

    def close(self) -> None:
        close_native_window()
        super().close()


__all__ = ["IntegratedTemporalGroundedRuntime"]
