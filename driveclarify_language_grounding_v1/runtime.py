"""Default-off live diagnostic runtime over SimLingo's exact rgb_0 input."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional, Sequence

from driveclarify_candidate_stability.sensitivity_metrics import analyze_sensitivity
from driveclarify_paper_mvp_runtime.contracts import RuntimeCandidate, canonical_sha256 as stage6_sha256
from driveclarify_paper_mvp_runtime.simlingo_binding import SimLingoCandidateForwardProvider, _points, _scalar

from .candidate_pipeline import GroundedCandidatePipeline
from .contracts import (
    AmbiguityStatus,
    CandidateSetResult,
    FEATURE_FLAG,
    GroundedCandidate,
    canonical_sha256,
)
from .simlingo_adapter import binding_set_receipt
from .slot_parser import SemanticSlotParser
from .visual_grounder import (
    COLOR_PIXEL_FRACTION_THRESHOLD,
    DEFAULT_MODEL_PATH,
    DETECTOR_BOX_THRESHOLD,
    MAX_AREA_FRACTION,
    MIN_AREA_FRACTION,
    MIN_DETECTOR_CONFIDENCE,
    MODEL_ID,
    MODEL_REVISION,
    PLAUSIBILITY_THRESHOLD,
    TEXT_THRESHOLD,
    GroundingDinoVisualGrounder,
)
from .visualization import render_grounding_panel


OUTPUT_ENV = "DRIVECLARIFY_LANGUAGE_GROUNDING_V1_OUTPUT_DIR"
RAW_INSTRUCTION_ENV = "DRIVECLARIFY_LANGUAGE_GROUNDING_V1_RAW_INSTRUCTION"
DEVICE_ENV = "DRIVECLARIFY_LANGUAGE_GROUNDING_V1_DETECTOR_DEVICE"
TRIGGER_FRAME_ENV = "DRIVECLARIFY_LANGUAGE_GROUNDING_V1_TRIGGER_FRAME"
RECEIPT_FILENAME = "LANGUAGE_GROUNDING_V1_LIVE_RECEIPT.json"


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def _file_sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _front(input_data: Any) -> tuple[int, Any]:
    import numpy as np

    entry = input_data["rgb_0"]
    frame = int(entry[0]) if isinstance(entry, (tuple, list)) else -1
    image = entry[1] if isinstance(entry, (tuple, list)) else entry
    value = np.asarray(image)
    if value.ndim != 3 or value.shape[2] < 3:
        raise ValueError("LANGUAGE_GROUNDING_RGB_0_INVALID")
    return frame, np.ascontiguousarray(value).copy()


@dataclass(frozen=True)
class _EgoView:
    speed_mps: float


@dataclass(frozen=True)
class _VisionView:
    observation_id: str
    frame_id: int


@dataclass(frozen=True)
class _EpisodeView:
    ego_state: _EgoView
    vision_observation: _VisionView


def _runtime_candidate(candidate: GroundedCandidate, repetition_id: str) -> RuntimeCandidate:
    return RuntimeCandidate(
        candidate_id=repetition_id,
        interpretation_id=candidate.interpretation_id,
        prompt_text=candidate.prompt_text,
        visual_track_id=candidate.grounded_referent_id,
        visual_anchor_digest=candidate.grounding_sha256,
        candidate_semantic_digest=candidate.semantic_sha256,
        candidate_input_digest=stage6_sha256(
            {
                "language_grounding_v1_candidate_id": candidate.candidate_id,
                "repetition_id": repetition_id,
                "source_observation_id": candidate.source_observation_id,
                "source_frame_id": candidate.source_frame_id,
                "image_sha256": candidate.image_sha256,
                "prompt_sha256": candidate.prompt_sha256,
            }
        ),
        source_observation_id=candidate.source_observation_id,
        source_frame_id=candidate.source_frame_id,
        generator_rule="LANGUAGE_GROUNDING_V1_IMAGE_ONLY_REFERENT_V1",
    )


class LanguageGroundingV1Runtime:
    """One-instruction diagnostic; never returns a candidate plan to the PID."""

    enabled = True

    def __init__(self, agent: Any, output_dir: str, *, detector: Any = None) -> None:
        self.agent = agent
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.parser = SemanticSlotParser()
        self.pipeline = GroundedCandidatePipeline()
        self.detector = detector or GroundingDinoVisualGrounder(
            device=os.environ.get(DEVICE_ENV, "cpu")
        )
        self.raw_instruction = (
            os.environ.get(RAW_INSTRUCTION_ENV)
            or os.environ.get("DRIVECLARIFY_PAPER_MVP_STAGE6B_RAW_INSTRUCTION")
            or getattr(agent, "custom_prompt", None)
        )
        trigger_value = os.environ.get(TRIGGER_FRAME_ENV)
        self.trigger_frame = int(trigger_value) if trigger_value else None
        self._receipt: dict = {
            "schema_version": "driveclarify.language_grounding_v1.live_receipt.v1",
            "enabled": True,
            "feature_flag": FEATURE_FLAG,
            "parallel_research_branch": True,
            "stage6b_frozen_experiment_replaced": False,
            "status": "WAITING_FOR_RGB_0",
            "level": 0,
            "detector_forward_count": 0,
            "tracker_forward_count": 0,
            "simlingo_candidate_forward_count": 0,
            "simlingo_visualization_induced_forward_count": 0,
            "candidate_control_write_count": 0,
            "m3_control_write_count": 0,
            "new_pid_count": 0,
            "configured_trigger_frame": self.trigger_frame,
            "runtime_model_versions": {
                "grounding_dino": {
                    "role": "ADOPTED_PRETRAINED_OPEN_VOCABULARY_GROUNDER",
                    "model_id": MODEL_ID,
                    "revision": MODEL_REVISION,
                    "checkpoint_path": str(DEFAULT_MODEL_PATH / "model.safetensors"),
                    "checkpoint_sha256": _file_sha256(
                        DEFAULT_MODEL_PATH / "model.safetensors"
                    ),
                    "device": os.environ.get(DEVICE_ENV, "cpu"),
                    "detector_proposal_box_threshold": DETECTOR_BOX_THRESHOLD,
                    "detector_text_threshold": TEXT_THRESHOLD,
                },
                "driveclarify_plausibility_filter": {
                    "version": "LANGUAGE_GROUNDING_V1_LABEL_FREE_FILTER_V2",
                    "minimum_detector_confidence": MIN_DETECTOR_CONFIDENCE,
                    "plausibility_threshold": PLAUSIBILITY_THRESHOLD,
                    "minimum_bbox_area_fraction": MIN_AREA_FRACTION,
                    "maximum_bbox_area_fraction": MAX_AREA_FRACTION,
                    "requested_color_pixel_fraction_threshold": COLOR_PIXEL_FRACTION_THRESHOLD,
                },
                "candidate_stability_protocol": "DRIVECLARIFY_CANDIDATE_SENSITIVITY_PILOT_V1",
                "simlingo_candidate_adapter": "EXISTING_SIMLINGO_INSTRUCTION_FOLLOWING_BINDING",
            },
            "label_firewall": {
                "policy_gold_candidate_index_reads": 0,
                "policy_expected_decision_reads": 0,
                "policy_evaluator_annotation_reads": 0,
                "carla_actor_runtime_grounding_reads": 0,
                "dev_reads": 0,
                "test_reads": 0,
            },
            "byte_track": {
                "enabled": False,
                "reason": "SINGLE_FRAME_STATIC_REFERENTIAL_CASE_DOES_NOT_REQUIRE_TRACKING",
            },
            "errors": [],
        }
        self._candidate_set: Optional[CandidateSetResult] = None
        self._speed = 0.0
        self._processed = False
        self._forwarded = False
        self._pid_invocations = 0
        self._control_observations = 0
        self._front_copy = None
        self._persist()

    def _persist(self) -> None:
        self._receipt["baseline_pid_invocations_observed"] = self._pid_invocations
        self._receipt["baseline_control_observations"] = self._control_observations
        _atomic_json(self.output_dir / RECEIPT_FILENAME, self._receipt)

    def on_tick(
        self,
        input_data: Any,
        tick_data: Any,
        timestamp: Any,
        frame: Any,
        observation_id: Any,
    ) -> None:
        del timestamp
        if self._processed:
            return
        try:
            sensor_frame, image = _front(input_data)
            expected_frame = int(frame)
            if sensor_frame != expected_frame:
                raise ValueError("LANGUAGE_GROUNDING_SENSOR_FRAME_MISMATCH")
            if self.trigger_frame is not None and expected_frame < self.trigger_frame:
                self._receipt.update(
                    {
                        "status": "WAITING_FOR_CONFIGURED_DIAGNOSTIC_FRAME",
                        "latest_frame_observed": expected_frame,
                    }
                )
                self._persist()
                return
            self._processed = True
            if not isinstance(self.raw_instruction, str) or not self.raw_instruction.strip():
                raise ValueError("LANGUAGE_GROUNDING_RAW_INSTRUCTION_MISSING")
            self._front_copy = image
            before = hashlib.sha256(image.tobytes(order="C")).hexdigest()
            self._speed = float(_scalar(tick_data.get("speed")) or 0.0) if isinstance(tick_data, dict) else 0.0
            parsed = self.parser.parse(self.raw_instruction)
            phrase = parsed.referent_phrase or parsed.landmark_phrase
            if phrase is None:
                self._receipt.update(
                    {
                        "status": "UNKNOWN",
                        "parsed_slots": parsed.to_dict(),
                        "reason_codes": ["NO_VISUAL_REFERENT_PHRASE"],
                    }
                )
                self._persist()
                return
            grounding = self.detector.ground(
                image,
                phrase,
                frame_id=expected_frame,
                observation_id=str(observation_id),
                captured_monotonic=time.monotonic(),
            )
            candidate_set = self.pipeline.construct(parsed, grounding)
            self._candidate_set = candidate_set
            after = hashlib.sha256(image.tobytes(order="C")).hexdigest()
            raw_path = self.output_dir / "front_rgb_0_same_frame.png"
            import cv2

            cv2.imwrite(str(raw_path), image[:, :, :3])
            self._receipt.update(
                {
                    "status": (
                        "GROUNDING_AND_CANDIDATES_READY"
                        if candidate_set.effective_k == 2
                        else "BLOCKED_VISUAL_REFERENT_GROUNDING"
                    ),
                    "level": 3 if candidate_set.effective_k == 2 else 1,
                    "frame_identity": {
                        "carla_frame": expected_frame,
                        "sensor_frame": sensor_frame,
                        "observation_id": str(observation_id),
                        "rgb_sha256": before,
                        "rgb_sha256_after_copy": after,
                        "model_rgb_unchanged": before == after,
                        "source": 'input_data["rgb_0"]',
                        "saved_copy_path": str(raw_path),
                    },
                    "raw_instruction": self.raw_instruction,
                    "parsed_slots": parsed.to_dict(),
                    "grounding_query": grounding.query,
                    "grounding": grounding.to_dict(),
                    "candidate_set": candidate_set.to_dict(),
                    "raw_k": candidate_set.raw_k,
                    "effective_k": candidate_set.effective_k,
                    "semantic_uniqueness": not candidate_set.semantic_duplicate,
                    "grounding_uniqueness": not candidate_set.grounding_duplicate,
                    "detector_forward_count": grounding.detector_forward_count,
                    "latency": {
                        "detector_seconds": grounding.detector_latency_seconds,
                        "ambiguity_layer_seconds": grounding.ambiguity_latency_seconds,
                        "tracker_seconds": 0.0,
                    },
                    "cache": {
                        "status": grounding.cache_status,
                        "grounding_timestamp_monotonic": grounding.completed_monotonic,
                        "frame_age_seconds_at_completion": (
                            grounding.completed_monotonic - grounding.captured_monotonic
                        ),
                        "invalidation_events": [
                            "NEW_INSTRUCTION",
                            "REFERENT_LEAVES_FOV",
                            "TRACK_ID_LOST",
                            "SCENE_MATERIAL_CHANGE",
                            "ASK_ANSWER",
                            "WAIT_INFORMATION_ARRIVAL",
                        ],
                    },
                }
            )
            render_grounding_panel(self.output_dir, image, candidate_set, plans=None)
        except Exception as exc:
            self._receipt["status"] = "BLOCKED_VISUAL_REFERENT_GROUNDING"
            self._receipt["errors"].append(
                {"stage": "ON_TICK", "type": type(exc).__name__, "message": str(exc)}
            )
        self._persist()

    def on_model_output(self, baseline_route: Any, baseline_speed: Any, model_start: float, model_end: float) -> None:
        del baseline_route, baseline_speed, model_start, model_end
        if self._forwarded or self._candidate_set is None or self._candidate_set.effective_k != 2:
            return
        self._forwarded = True
        try:
            candidates = self._candidate_set.candidates
            bindings = binding_set_receipt(candidates, self._speed)
            if not bindings["conditioning_unique"]:
                self._receipt["status"] = "BLOCKED_CANDIDATE_TO_SIMLINGO_BINDING_COLLAPSE"
                self._receipt["simlingo_binding"] = bindings
                self._persist()
                return
            provider = SimLingoCandidateForwardProvider(self.agent)
            episode = _EpisodeView(
                ego_state=_EgoView(self._speed),
                vision_observation=_VisionView(
                    candidates[0].source_observation_id, candidates[0].source_frame_id
                ),
            )
            records = []
            forward_rows = []
            plan_sets = {"A": [], "B": []}
            # Existing sensitivity protocol freezes this exact interleaving.
            for repetition in range(1, 4):
                for group, candidate in zip(("A", "B"), candidates):
                    repetition_id = "{}{}".format(group, repetition)
                    runtime_candidate = _runtime_candidate(candidate, repetition_id)
                    provider.begin_event()
                    result = provider(episode, runtime_candidate)  # type: ignore[arg-type]
                    route = [list(point) for point in result.plan.route]
                    speed = [list(point) for point in result.plan.speed]
                    evidence = dict(result.forward_evidence)
                    row = {
                        "candidate_id": repetition_id,
                        "interpretation_id": group,
                        "route": route,
                        "speed": speed,
                    }
                    records.append(row)
                    plan_sets[group].append(row)
                    forward_rows.append(
                        {
                            **evidence,
                            "candidate_id": repetition_id,
                            "grounded_candidate_id": candidate.candidate_id,
                            "interpretation_id": group,
                            "pred_route": route,
                            "pred_speed_wps": speed,
                            "route_sha256": canonical_sha256(route),
                            "speed_sha256": canonical_sha256(speed),
                        }
                    )
            metrics = analyze_sensitivity(records)
            plan_sets["metrics"] = metrics
            signal = bool(metrics["candidate_signal_identified"])
            self._receipt.update(
                {
                    "status": (
                        "REAL_PLAN_DIVERGENCE_OVER_REPEAT_NOISE_PASS"
                        if signal
                        else "BLOCKED_SIMLINGO_GROUNDED_INTERPRETATION_SENSITIVITY"
                    ),
                    "level": 5 if signal else 4,
                    "simlingo_binding": bindings,
                    "candidate_plan_repetitions": forward_rows,
                    "candidate_plan_stability": metrics,
                    "simlingo_candidate_forward_count": len(forward_rows),
                    "forward_accounting": {
                        "normal_simlingo_forward_count_for_trigger_tick": 1,
                        "language_grounding_candidate_forwards": len(forward_rows),
                        "visualization_induced_forwards": 0,
                        "candidate_plan_commits": 0,
                        "candidate_control_writes": 0,
                    },
                    "first_collapse_status": {
                        "historical_current_stage6b_first_collapse_layer": "TARGET_BINDING",
                        "v1_prompt_binding_collapse": False,
                        "v1_plan_collapse": not signal,
                    },
                    "scientific_claim": (
                        "DISTINCT_GROUNDED_INTERPRETATIONS_CAN_PRODUCE_STABLE_DISTINCT_PLANS"
                        if signal
                        else "SIMLINGO_INTERPRETATION_SENSITIVITY_LIMITATION"
                    ),
                    "consequence_divergence": {
                        "status": "NOT_EVALUATED_IN_NO_CONTROL_SHADOW_PILOT",
                        "claim_allowed": False,
                    },
                }
            )
            render_grounding_panel(self.output_dir, self._front_copy, self._candidate_set, plans=plan_sets)
        except Exception as exc:
            self._receipt["status"] = "BLOCKED_SIMLINGO_GROUNDED_INTERPRETATION_SENSITIVITY"
            self._receipt["errors"].append(
                {"stage": "ON_MODEL_OUTPUT", "type": type(exc).__name__, "message": str(exc)}
            )
        self._persist()

    def select_plan_source(self, baseline_route: Any, baseline_speed: Any, current_monotonic: float) -> tuple:
        del current_monotonic
        return baseline_route, baseline_speed

    def on_pid_invocation(self, current_monotonic: float) -> None:
        del current_monotonic
        self._pid_invocations += 1

    def on_control(self, control: Any, gt_velocity: Any, current_monotonic: float) -> None:
        del control, gt_velocity, current_monotonic
        self._control_observations += 1

    def commit(self) -> None:
        self._persist()

    def close(self) -> None:
        self._receipt["closed"] = True
        self._persist()

    def summary(self) -> dict:
        return {
            "enabled": True,
            "status": self._receipt.get("status"),
            "level": self._receipt.get("level"),
            "detector_forward_count": self._receipt.get("detector_forward_count", 0),
            "candidate_forward_count": self._receipt.get("simlingo_candidate_forward_count", 0),
            "control_writes": 0,
            "m3_control_writes": 0,
            "extra_baseline_pid": 0,
            "planner_advancement": 0,
        }


def build_language_grounding_v1_runtime(agent: Any) -> Any:
    if not _truthy(os.environ.get(FEATURE_FLAG)):
        raise RuntimeError("LANGUAGE_GROUNDING_V1_FEATURE_FLAG_OFF")
    output = os.environ.get(OUTPUT_ENV) or os.environ.get("DRIVECLARIFY_SHADOW_OUTPUT_DIR")
    if not output:
        raise RuntimeError("LANGUAGE_GROUNDING_V1_OUTPUT_DIR_MISSING")
    return LanguageGroundingV1Runtime(agent, output)


__all__ = [
    "DEVICE_ENV",
    "LanguageGroundingV1Runtime",
    "OUTPUT_ENV",
    "RAW_INSTRUCTION_ENV",
    "RECEIPT_FILENAME",
    "TRIGGER_FRAME_ENV",
    "build_language_grounding_v1_runtime",
]
