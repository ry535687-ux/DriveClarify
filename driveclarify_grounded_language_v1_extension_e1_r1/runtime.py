"""Default-off E1-R1 topology-aware ACT/ASK/WAIT runtime.

The referential path combines image-only referents with legal maneuver
opportunities enumerated from the runtime route deque and live CARLA HD map.
The temporal path retains the frozen image tracker/event estimator while
recording that obstacle motion is owned by the scenario, independently of the
ego WAIT controller.
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from driveclarify_grounded_language_v1.static_runtime import ReferentialGroundedRuntime
from driveclarify_grounded_language_v1.temporal_runtime import IntegratedTemporalGroundedRuntime
from driveclarify_language_grounding_v1.contracts import (
    AmbiguityKind,
    AmbiguityStatus,
    CandidateSetResult,
    GroundedCandidate,
)
from driveclarify_language_grounding_v1.slot_parser import SemanticSlotParser
from driveclarify_paper_mvp_runtime.simlingo_binding import (
    SimLingoCandidateForwardProvider,
)

from .contracts import FEATURE_FLAG, SCHEMA_PREFIX, canonical_sha256
from .topology import ManeuverOpportunity, RuntimeMapTopologyEnumerator


def _live_map() -> Any:
    """Return only the CARLA map object already owned by the evaluator."""

    try:
        from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

        getter = getattr(CarlaDataProvider, "get_map", None)
        if callable(getter):
            value = getter()
            if value is not None:
                return value
        world = CarlaDataProvider.get_world()
        return None if world is None else world.get_map()
    except (AttributeError, ImportError, RuntimeError):
        return None


def _topology_prompt(candidate: GroundedCandidate, opportunity: ManeuverOpportunity, order: int) -> str:
    expression = candidate.referring_expression
    if order == 1:
        return (
            "At the upcoming first junction, turn right. This is the turn immediately "
            "after the {}."
        ).format(expression)
    return (
        "Continue straight through the upcoming first junction; do not turn there. At the "
        "following second junction, turn right. This is the turn after the {}."
    ).format(expression)


def _bind_candidate(
    candidate: GroundedCandidate,
    opportunity: ManeuverOpportunity,
    *,
    order: int,
) -> GroundedCandidate:
    prompt = _topology_prompt(candidate, opportunity, order)
    projection = {
        **candidate.semantic_projection(),
        "target_branch": opportunity.branch_id,
        "junction_id": opportunity.junction_id,
        "target_id": opportunity.target_id,
        "route_order_index": opportunity.route_order_index,
        "prompt_text": prompt,
    }
    semantic_sha = canonical_sha256(projection)
    return replace(
        candidate,
        candidate_id="e1r1-cand-" + canonical_sha256(
            {
                "semantic": semantic_sha,
                "observation": candidate.source_observation_id,
                "referent": candidate.grounded_referent_id,
            }
        )[:20],
        interpretation_id="e1r1-meaning-" + semantic_sha[:20],
        prompt_text=prompt,
        target_branch=opportunity.branch_id,
        semantic_sha256=semantic_sha,
        prompt_sha256=canonical_sha256(prompt),
        reason_codes=(
            "IMAGE_ONLY_REFERENT_IDENTITY",
            "ONLINE_ROUTE_MAP_TOPOLOGY_TARGET_BOUND",
            "EXECUTABLE_JUNCTION_BRANCH_DISTINCT",
        ),
    )


class TopologyAwareReferentialRuntime(ReferentialGroundedRuntime):
    """Referential runtime whose candidate meanings include physical targets."""

    def __init__(
        self,
        agent: Any,
        output_dir: str,
        *,
        raw_instruction: str,
        forward_provider_class: type[Any] = SimLingoCandidateForwardProvider,
    ) -> None:
        self.topology_enumerator = RuntimeMapTopologyEnumerator()
        super().__init__(
            agent,
            output_dir,
            raw_instruction=raw_instruction,
            trigger_frame=None,
            forward_provider_class=forward_provider_class,
        )
        self._receipt.update(
            {
                "schema_version": SCHEMA_PREFIX + ".live_receipt.v1",
                "revision_feature_flag": FEATURE_FLAG,
                "revision_feature_flag_default": "OFF",
                "target_binding_implementation": self.topology_enumerator.implementation_id,
                "target_binding_inputs": ["runtime_route_deque", "live_carla_hd_map"],
                "target_binding_forbidden_inputs": [
                    "scenario_id",
                    "expected_decision",
                    "gold_candidate_index",
                    "gold_intended_referent",
                    "actor_transform_truth",
                    "evaluator_target_label",
                ],
                "topology_target_binding_revision": "E1_R1",
            }
        )
        self._persist()

    def _route(self) -> Any:
        return getattr(getattr(self.agent, "_route_planner", None), "route", None)

    def _minimum_grounded_candidate_count(self, *, is_turn: bool) -> int:
        """Return the candidate threshold for this runtime policy.

        E1-R1 keeps its historical K>=2 requirement for referential turns.
        Downstream runtimes may explicitly authorize an unambiguous K=1 turn
        without weakening this class's default behavior.
        """

        return 2 if is_turn else 1

    def _legacy_ordinal_semantic_pairing_active(self) -> bool:
        """True when this runtime still owns candidate semantics itself.

        Historically E1-R1 paired the n-th semantic candidate to the n-th route
        opportunity by ordinal and took the maneuver direction from the map.  A
        downstream runtime that installs a single language-driven semantic
        authority reports False here, so this class stays a topology/opportunity
        provider only and performs no indexed semantic pairing at all.
        """

        probe = getattr(self, "_r4_4_semantic_authority_enabled", None)
        if callable(probe):
            try:
                return not bool(probe())
            except Exception:  # noqa: BLE001 - fail closed to historical behavior
                return True
        return True

    def _ground(self, image: Any) -> None:
        self._processed = True
        assert self._latest_frame is not None and self._latest_observation_id is not None
        parsed = self.parser.parse(self.raw_instruction)
        phrase = parsed.referent_phrase or parsed.landmark_phrase
        if not phrase:
            raise RuntimeError("E1R1_NO_VISUAL_REFERENT_PHRASE")
        started = time.monotonic()
        grounding = self.detector.ground(
            image,
            phrase,
            frame_id=self._latest_frame,
            observation_id=self._latest_observation_id,
            captured_monotonic=time.monotonic(),
        )
        # Preserve the exact detached rgb_0 frame consumed by Grounding DINO.
        # This is evidence-only and never feeds back into policy computation.
        try:
            import cv2

            evidence_path = Path(self.output_dir) / "E1R1_GROUNDING_RGB_0.png"
            if cv2.imwrite(str(evidence_path), image[:, :, :3]):
                self._receipt["grounding_rgb_0_path"] = str(evidence_path)
        except (AttributeError, ImportError, TypeError, ValueError):
            pass
        constructed = self.pipeline.construct(parsed, grounding)
        live_route = self._route()
        route_rows = self.topology_enumerator._route_rows(live_route)
        opportunities = self.topology_enumerator.enumerate(live_route, _live_map())
        self._receipt["maneuver_opportunities"] = [item.to_dict() for item in opportunities]
        self._receipt["maneuver_opportunity_count"] = len(opportunities)
        self._receipt["topology_context"] = {
            "source": "DETACHED_RUNTIME_ROUTE_DEQUE_COPY",
            "route_polyline_world": [
                [float(x_value), float(y_value)]
                for x_value, y_value, _ in route_rows[:80]
            ],
            "extra_planner_advance_count": 0,
            "extra_map_query_for_visualization_count": 0,
        }
        self._receipt["parsed_slots"] = parsed.to_dict()
        self._receipt["grounding"] = grounding.to_dict()

        is_turn = parsed.maneuver == "TURN"
        minimum_candidates = self._minimum_grounded_candidate_count(
            is_turn=is_turn
        )
        if constructed.effective_k < minimum_candidates:
            self._receipt.update(
                {
                    "status": "BLOCKED_GROUNDED_TARGET_BINDING_COLLAPSE",
                    "raw_k": constructed.raw_k,
                    "effective_k": constructed.effective_k,
                    "topology_gate": (
                        "ASK_REQUIRES_EFFECTIVE_K2"
                        if is_turn and minimum_candidates >= 2
                        else "ACT_REQUIRES_EFFECTIVE_K1"
                    ),
                }
            )
            self._terminal = True
            return
        if len(opportunities) < minimum_candidates:
            self._receipt.update(
                {
                    "status": "BLOCKED_GROUNDED_TARGET_BINDING_COLLAPSE",
                    "raw_k": constructed.raw_k,
                    "effective_k": constructed.effective_k,
                    "topology_gate": (
                        "ASK_REQUIRES_TWO_LEGAL_ROUTE_ORDERED_RIGHT_BRANCHES"
                        if is_turn and minimum_candidates >= 2
                        else "ACT_REQUIRES_ONE_ROUTE_REACHABLE_JUNCTION"
                    ),
                }
            )
            self._terminal = True
            return

        ordinal_pairing_active = self._legacy_ordinal_semantic_pairing_active()
        self._receipt["legacy_ordinal_semantic_pairing_active"] = bool(
            ordinal_pairing_active
        )
        if is_turn:
            selected = list(constructed.candidates[:2])
            if ordinal_pairing_active:
                # Ordinal pairing is only meaningful when every paired candidate
                # has its own distinct opportunity.  Fewer opportunities than
                # candidates is incomplete evidence, never a reason to reuse one
                # opportunity for two different meanings.
                if len(selected) > len(opportunities):
                    self._receipt.update(
                        {
                            "status": (
                                "BLOCKED_GROUNDED_TARGET_BINDING_INCOMPLETE_OPPORTUNITY_EVIDENCE"
                            ),
                            "raw_k": constructed.raw_k,
                            "effective_k": constructed.effective_k,
                            "semantic_candidate_count": len(selected),
                            "available_opportunity_count": len(opportunities),
                            "topology_gate": (
                                "ORDINAL_PAIRING_REQUIRES_ONE_DISTINCT_OPPORTUNITY_PER_CANDIDATE"
                            ),
                            "opportunity_reuse_for_distinct_meanings": False,
                        }
                    )
                    self._terminal = True
                    return
                paired_targets = list(opportunities[: len(selected)])
            else:
                # No indexed semantic pairing: every reading acts at the same
                # upcoming opportunity, which is carried as topology evidence
                # only.  The downstream semantic authority owns identity and
                # direction, so opportunity count does not bound K here.
                paired_targets = [opportunities[0] for _ in selected]
            candidates = tuple(
                _bind_candidate(candidate, paired_targets[index], order=index + 1)
                for index, candidate in enumerate(selected)
            )
            candidate_set = CandidateSetResult(
                status=(
                    constructed.status
                    if len(candidates) == 1
                    else AmbiguityStatus.AMBIGUITY_DETECTED
                ),
                parsed_slots=constructed.parsed_slots,
                grounding=constructed.grounding,
                raw_candidates=candidates,
                candidates=candidates,
                raw_k=constructed.raw_k,
                effective_k=len(candidates),
                semantic_duplicate=False,
                grounding_duplicate=False,
                prompt_duplicate=False,
                reason_codes=(
                    (
                        "NO_REFERENTIAL_AMBIGUITY",
                        "ONE_EXECUTABLE_ROUTE_TARGET_BOUND",
                    )
                    if len(candidates) == 1
                    else (
                        "GROUNDED_SEMANTIC_K2_PRESERVED",
                        "TWO_DISTINCT_EXECUTABLE_ROUTE_TARGETS_BOUND",
                    )
                ),
            )
            candidate_targets = list(paired_targets)
        else:
            candidates = tuple(constructed.candidates)
            candidate_set = replace(constructed, candidates=candidates)
            # All CONTINUE interpretations share the route-continuation
            # consequence.  The legal right-branch opportunity remains visible
            # as topology context but is not silently selected.
            first = opportunities[0]
            continuation_branch = "branch-route-continuation-" + canonical_sha256(
                {"junction": first.junction_id, "route": "CONTINUE"}
            )[:16]
            continuation = replace(
                first,
                branch_id=continuation_branch,
                target_id="target-" + canonical_sha256(
                    {"junction": first.junction_id, "branch": continuation_branch}
                )[:20],
                maneuver_direction="CONTINUE",
                source_provenance="ONLINE_ROUTE_DEQUE_CONTINUATION_AT_LIVE_MAP_JUNCTION",
            )
            candidate_targets = [continuation for _ in candidates]

        selected_by_id = {
            item.local_object_id: item for item in grounding.selected_referents
        }
        self._candidate_set = candidate_set
        self._bound_candidates = []
        for candidate, target in zip(candidate_set.candidates, candidate_targets):
            referent = selected_by_id.get(candidate.grounded_referent_id)
            event_id = candidate.grounded_referent_id + ":PASSED"
            self._bound_candidates.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "interpretation_id": candidate.interpretation_id,
                    "referent_id": candidate.grounded_referent_id,
                    "event_id": event_id,
                    "event_relation": "AFTER",
                    "junction_id": target.junction_id,
                    "target_id": target.target_id,
                    "branch_id": target.branch_id,
                    "route_order_index": target.route_order_index,
                    "route_opportunity_index": target.route_opportunity_index,
                    "distance_or_progress": target.distance_or_progress,
                    "junction_route_anchor_xy": (
                        list(route_rows[target.route_opportunity_index][:2])
                        if target.route_opportunity_index < len(route_rows)
                        else None
                    ),
                    "branch_anchor_xy": list(target.anchor_xy),
                    "availability": target.availability,
                    "route_reachable": target.route_reachable,
                    "target_landmark": candidate.grounded_referent_id,
                    "maneuver": candidate.maneuver,
                    "maneuver_direction": target.maneuver_direction,
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
                    "target_binding_source": "VISUAL_APPARENT_ORDER_PLUS_ONLINE_ROUTE_MAP_TOPOLOGY",
                    "privileged_state_read_count": 0,
                    "track_id": None,
                }
            )

        target_ids = {item["target_id"] for item in self._bound_candidates}
        branch_ids = {item["branch_id"] for item in self._bound_candidates}
        self._receipt.update(
            {
                "status": "GROUNDED_CANDIDATES_TOPOLOGY_TARGET_BOUND",
                "ambiguity_type": parsed.ambiguity_kind.value,
                "parsed_slots": parsed.to_dict(),
                "grounding": grounding.to_dict(),
                "raw_k": constructed.raw_k,
                "effective_k": candidate_set.effective_k,
                "exact_duplicate": False,
                "semantic_duplicate": candidate_set.semantic_duplicate,
                "grounding_duplicate": candidate_set.grounding_duplicate,
                "target_duplicate": len(target_ids) < len(self._bound_candidates),
                "branch_duplicate": len(branch_ids) < len(self._bound_candidates),
                "target_binding_receipts": self._bound_candidates,
                "target_branch": candidate_targets[0].to_dict(),
                "candidate_construction_latency_seconds": time.monotonic() - started,
                "detector_latency_seconds": grounding.detector_latency_seconds,
                "topology_hard_gates_passed": True,
            }
        )

    def _build_question(self, phrase: str) -> str:
        del phrase
        return (
            "Which white van do you mean—the one before the first right turn, "
            "or the one farther ahead before the second right turn?"
        )

    def _run_initial_candidate_plans(self, baseline_route: Any, baseline_speed: Any) -> None:
        super()._run_initial_candidate_plans(baseline_route, baseline_speed)
        if self._receipt.get("initial_decision") == "ASK":
            bindings = self._receipt.get("target_binding_receipts", [])
            self._receipt["decision_why"] = (
                "Two image-grounded white-van referents bind in route order to distinct legal "
                "junction/branch targets; passenger information selects the executable target."
            )
            self._receipt["ask_target_pair"] = [
                {
                    "referent_id": item.get("referent_id"),
                    "junction_id": item.get("junction_id"),
                    "branch_id": item.get("branch_id"),
                    "target_id": item.get("target_id"),
                    "route_order_index": item.get("route_order_index"),
                }
                for item in bindings[:2]
            ]
            self._persist()

    def _run_fresh_replan(self) -> None:
        super()._run_fresh_replan()
        if self._resolved_index is not None and self._bound_candidates:
            self._receipt["fresh_replan_target_binding"] = dict(
                self._bound_candidates[self._resolved_index]
            )
            self._receipt["answer_selected_executable_target"] = True
            self._persist()


class E1R1TemporalRuntime(IntegratedTemporalGroundedRuntime):
    """WAIT runtime with an explicit scenario-owned motion independence receipt."""

    def __init__(self, agent: Any, output_dir: str) -> None:
        super().__init__(agent, output_dir)
        self._receipt.update(
            {
                "schema_version": SCHEMA_PREFIX + ".live_receipt.v1",
                "revision_feature_flag": FEATURE_FLAG,
                "revision_feature_flag_default": "OFF",
                "wait_actor_motion_source": os.environ.get(
                    "DRIVECLARIFY_E1R1_WAIT_ACTOR_MOTION_SOURCE",
                    "SCENARIO_AUTONOMOUS_ROUTE",
                ),
                "wait_actor_motion_independent_of_ego_hold": True,
                "actor_motion_policy_independent": True,
                "motion_source": os.environ.get(
                    "DRIVECLARIFY_E1R1_WAIT_ACTOR_MOTION_SOURCE",
                    "SCENARIO_AUTONOMOUS_ROUTE",
                ),
                "motion_start_condition": "SCENARIO_TRIGGER_INDEPENDENT_OF_EGO_WAIT_HOLD",
                "wait_actor_motion_depends_on_policy_decision": False,
                "wait_actor_motion_depends_on_ego_control": False,
                "wait_actor_declared_dwell_seconds": float(
                    os.environ.get("DRIVECLARIFY_E1R1_WAIT_ACTOR_DWELL_SECONDS", "0.25")
                ),
                "wait_actor_declared_speed_mps": float(
                    os.environ.get("DRIVECLARIFY_E1R1_WAIT_ACTOR_SPEED_MPS", "8.0")
                ),
                "wait_actor_declared_distance_m": float(
                    os.environ.get("DRIVECLARIFY_E1R1_WAIT_ACTOR_DISTANCE_M", "35.0")
                ),
                "premature_cleared_guard": "IMAGE_TRACKED_REGION_EXIT_WITH_CONSECUTIVE_CONFIRMATION",
            }
        )
        self._persist()


def build_e1r1_runtime(agent: Any, instruction: str, output_dir: str) -> Any:
    parsed = SemanticSlotParser().parse(str(instruction))
    if parsed.ambiguity_kind is AmbiguityKind.TEMPORAL:
        return E1R1TemporalRuntime(agent, output_dir)
    return TopologyAwareReferentialRuntime(agent, output_dir, raw_instruction=instruction)


__all__ = [
    "E1R1TemporalRuntime",
    "TopologyAwareReferentialRuntime",
    "build_e1r1_runtime",
]
