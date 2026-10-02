"""Thin DriveClarify wrapper around the released SimLingo Dreaming contract.

The wrapper deliberately delegates conversation/token construction and route
normalization to released SimLingo functions.  It never changes the model,
checkpoint, planner, PID, or control.  An optional scoped R4.4 binding may
replace only the cloned candidate input's existing target-point representation.
"""

from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any, Mapping

from driveclarify_candidate_local_navigation_bridge import (
    navigation_projection_digest,
)
from driveclarify_m3_offline_replay.serialization import (
    canonical_sha256 as m3_canonical_sha256,
)
from driveclarify_m3_runtime_shadow.speed_consequence import (
    evaluate_pid_desired_speed_v0,
)
from driveclarify_paper_mvp_runtime.contracts import (
    CandidatePlan,
    PolicyEpisodeInput,
    RuntimeCandidate,
    canonical_sha256,
)
from driveclarify_paper_mvp_runtime.simlingo_binding import (
    CandidateForwardResult,
    SimLingoCandidateForwardProvider,
    _points,
)


MARKER = "<INSTRUCTION_FOLLOWING>"
OFFICIAL_ANSWER = "Following the given instruction. Waypoints:"
TARGET_POINT_PROMPT_SUFFIX = " Target waypoint: <TARGET_POINT><TARGET_POINT>."


def _tensor_sha256(value: Any) -> str:
    current = value.detach().cpu().contiguous()
    array = current.numpy()
    envelope = f"{array.dtype}|{array.shape}|".encode("ascii") + array.tobytes()
    return hashlib.sha256(envelope).hexdigest()


@dataclass(frozen=True)
class OfficialDreamingBuild:
    prompt_body: str
    full_prompt: str
    inference_prompt: str
    prompt: Any
    prompt_inference: Any


class OfficialDreamingCandidateAdapter:
    """Construct only the candidate language fields through official helpers."""

    implementation_id = "OfficialDreamingCandidateAdapter.v1"

    def __init__(self, *, tokenizer: Any, encoder_variant: str, device: Any = None) -> None:
        self.tokenizer = tokenizer
        self.encoder_variant = str(encoder_variant)
        self.device = device
        self._num_image_tokens_per_patch: int | None = None

    @classmethod
    def from_agent(cls, agent: Any) -> "OfficialDreamingCandidateAdapter":
        return cls(
            tokenizer=agent.tokenizer,
            encoder_variant=str(agent.cfg.model.vision_model.variant),
            device=getattr(agent, "device", None),
        )

    def _image_tokens(self, image_patch_count: int) -> int:
        if self._num_image_tokens_per_patch is None:
            from simlingo_training.utils.internvl2_utils import get_num_image_tokens_per_patch

            self._num_image_tokens_per_patch = int(
                get_num_image_tokens_per_patch(self.encoder_variant)
            )
        if image_patch_count < 1:
            raise ValueError("OFFICIAL_DREAMING_IMAGE_PATCH_COUNT_INVALID")
        return self._num_image_tokens_per_patch * int(image_patch_count)

    @staticmethod
    def prompt_body(
        instruction: str,
        speed_mps: float,
        *,
        include_target_points: bool = False,
    ) -> str:
        text = str(instruction).strip()
        if not text:
            raise ValueError("OFFICIAL_DREAMING_EMPTY_CANDIDATE_INSTRUCTION")
        # Eval_Dreamer rounds the displayed speed to one decimal place.
        body = f"{MARKER} Current speed: {round(float(speed_mps), 1):.1f} m/s. {text}"
        if include_target_points:
            body += TARGET_POINT_PROMPT_SUFFIX
        return body

    @staticmethod
    def consumed_placeholder_points(
        built: "OfficialDreamingBuild", token_id: int
    ) -> tuple[tuple[float, float], ...]:
        """Read back the target points carried by the built forward input."""

        label = built.prompt_inference
        if len(label.placeholder_values) != 1:
            raise ValueError("TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH")
        value = label.placeholder_values[0].get(int(token_id))
        try:
            return tuple((float(row[0]), float(row[1])) for row in value)
        except (IndexError, TypeError, ValueError) as error:
            raise ValueError("TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH") from error

    @staticmethod
    def navigation_target_points(binding: Any) -> tuple[tuple[float, float], ...]:
        """Validate the two-point R4.4 wire payload without importing torch."""

        try:
            points = tuple(
                (float(row[0]), float(row[1]))
                for row in binding.target_points_ego_local_xy_m
            )
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise ValueError("TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH") from error
        if len(points) != 2 or any(
            not all(value == value and abs(value) != float("inf") for value in row)
            for row in points
        ):
            raise ValueError("TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH")
        return points

    def navigation_placeholder_values(self, binding: Any) -> list[dict[int, Any]]:
        token_id = self.tokenizer.convert_tokens_to_ids("<TARGET_POINT>")
        if (
            isinstance(token_id, bool)
            or not isinstance(token_id, int)
            or token_id < 0
            or token_id == getattr(self.tokenizer, "unk_token_id", None)
        ):
            raise ValueError("TARGET_POINT_TOKEN_ID_INVALID")
        points = self.navigation_target_points(binding)
        return [{int(token_id): [list(row) for row in points]}]

    @staticmethod
    def _move_label(label: Any, device: Any) -> Any:
        if device is None:
            return label
        return label._replace(
            phrase_ids=label.phrase_ids.to(device),
            phrase_valid=label.phrase_valid.to(device),
            phrase_mask=label.phrase_mask.to(device),
            loss_masking=(
                None if label.loss_masking is None else label.loss_masking.to(device)
            ),
        )

    def build(
        self,
        instruction: str,
        speed_mps: float,
        *,
        placeholder_values: list[Mapping[int, Any]] | None = None,
        image_patch_count: int = 2,
        include_target_points: bool = False,
    ) -> OfficialDreamingBuild:
        from simlingo_training.utils.custom_types import LanguageLabel
        from simlingo_training.utils.internvl2_utils import get_custom_chat_template

        body = self.prompt_body(
            instruction,
            speed_mps,
            include_target_points=include_target_points,
        )
        conversation = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": body},
                    {"type": "image"},
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": OFFICIAL_ANSWER}],
            },
        ]
        conversation_dict, question_dict = get_custom_chat_template(
            [conversation],
            self.tokenizer,
            self.encoder_variant,
            self._image_tokens(image_patch_count),
        )
        transported = [dict(item) for item in (placeholder_values or [{}])]

        def label(values: Mapping[str, Any]) -> Any:
            return self._move_label(
                LanguageLabel(
                    phrase_ids=values["phrase_ids"],
                    phrase_valid=values["phrase_valid"],
                    phrase_mask=values["phrase_mask"],
                    placeholder_values=[dict(item) for item in transported],
                    language_string=list(values["language_string"]),
                    loss_masking=values["loss_masking"],
                ),
                self.device,
            )

        prompt = label(conversation_dict)
        prompt_inference = label(question_dict)
        return OfficialDreamingBuild(
            prompt_body=body,
            full_prompt=prompt.language_string[0],
            inference_prompt=prompt_inference.language_string[0],
            prompt=prompt,
            prompt_inference=prompt_inference,
        )

    def adapt_driving_input(
        self,
        base: Any,
        instruction: str,
        speed_mps: float,
        *,
        preserve_placeholder_values: bool = False,
        navigation_binding: Any = None,
    ) -> tuple[Any, OfficialDreamingBuild]:
        if navigation_binding is not None and preserve_placeholder_values:
            raise ValueError("R4_4_NAVIGATION_CANNOT_PRESERVE_NOMINAL_PLACEHOLDERS")
        import torch
        from simlingo_training.utils.custom_types import DrivingInput

        clone = SimLingoCandidateForwardProvider._clone_value
        values = {name: clone(torch, value) for name, value in base._asdict().items()}
        placeholders = (
            self.navigation_placeholder_values(navigation_binding)
            if navigation_binding is not None
            else clone(torch, base.prompt_inference.placeholder_values)
            if preserve_placeholder_values
            else [{}]
        )
        built = self.build(
            instruction,
            speed_mps,
            placeholder_values=placeholders,
            image_patch_count=int(base.camera_images.shape[2]),
            include_target_points=navigation_binding is not None,
        )
        if navigation_binding is not None:
            points = self.navigation_target_points(navigation_binding)
            token_id = self.tokenizer.convert_tokens_to_ids("<TARGET_POINT>")
            for label in (built.prompt, built.prompt_inference):
                token_count = int((label.phrase_ids == token_id).sum().item())
                if token_count != 2:
                    raise ValueError("TARGET_POINT_PLACEHOLDER_COUNT_NOT_TWO")
                if len(label.placeholder_values) != 1:
                    raise ValueError("TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH")
                value = label.placeholder_values[0].get(token_id)
                try:
                    normalized = tuple(
                        (float(row[0]), float(row[1])) for row in value
                    )
                except (IndexError, TypeError, ValueError) as error:
                    raise ValueError(
                        "TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH"
                    ) from error
                if normalized != points:
                    raise ValueError("TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH")
            target = getattr(base, "target_point", None)
            try:
                values["target_point"] = target.new_tensor([points[0]])
            except AttributeError:
                values["target_point"] = torch.tensor(
                    [points[0]], dtype=getattr(target, "dtype", None)
                )
        values["prompt"] = built.prompt
        values["prompt_inference"] = built.prompt_inference
        return DrivingInput(**values), built


class OfficialDreamingCandidateForwardProvider:
    """Candidate forward using the validated official prompt and route APIs."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.adapter = OfficialDreamingCandidateAdapter.from_agent(agent)
        self._event_calls = 0
        self._candidate_local_navigation_binding: Any = None

    @contextmanager
    def candidate_local_navigation(self, binding: Any) -> Any:
        """Scope one candidate-local binding to exactly one provider call."""

        if binding is None:
            raise ValueError("LOCAL_NAVIGATION_BINDING_MISSING")
        if self._candidate_local_navigation_binding is not None:
            raise RuntimeError("LOCAL_NAVIGATION_BINDING_NESTED_OR_STALE")
        self.adapter.navigation_target_points(binding)
        self._candidate_local_navigation_binding = binding
        try:
            yield self
        finally:
            self._candidate_local_navigation_binding = None

    def begin_event(self) -> None:
        self._event_calls = 0

    def _target_point_wire_evidence(
        self, built: OfficialDreamingBuild, navigation_binding: Any
    ) -> tuple[int, tuple[tuple[float, float], ...] | None, list[dict[str, Any]]]:
        if navigation_binding is None:
            # Preserve a genuinely dependency-free default-off path.  Token
            # identity/counting is part of the R4.4 binding contract only.
            return 0, None, [{}]
        token_id = self.agent.tokenizer.convert_tokens_to_ids("<TARGET_POINT>")
        if (
            isinstance(token_id, bool)
            or not isinstance(token_id, int)
            or token_id < 0
            or token_id == getattr(self.agent.tokenizer, "unk_token_id", None)
        ):
            raise RuntimeError("TARGET_POINT_TOKEN_ID_INVALID")
        target_tokens = int(
            (built.prompt_inference.phrase_ids == token_id).sum().item()
        )
        navigation_points = self.adapter.navigation_target_points(
            navigation_binding
        )
        if target_tokens != 2:
            raise RuntimeError("TARGET_POINT_PLACEHOLDER_COUNT_NOT_TWO")
        # Recompute the digest from the values actually placed on the wire, so
        # the echoed identity cannot be a stale constructor argument.
        consumed = self.adapter.consumed_placeholder_points(built, token_id)
        if consumed != navigation_points:
            raise RuntimeError("TARGET_POINT_PLACEHOLDER_VALUE_MISMATCH")
        if navigation_projection_digest(consumed) != str(
            getattr(navigation_binding, "projection_digest", "")
        ):
            raise RuntimeError("TARGET_POINT_PROJECTION_DIGEST_MISMATCH")
        return (
            target_tokens,
            navigation_points,
            [
                {
                    str(token_id): [list(row) for row in navigation_points]
                }
            ],
        )

    @property
    def event_forward_count(self) -> int:
        return self._event_calls

    def __call__(
        self, episode: PolicyEpisodeInput, candidate: RuntimeCandidate
    ) -> CandidateForwardResult:
        import numpy as np
        import torch
        from simlingo_training.utils.custom_types import DrivingInput

        base = DrivingInput(**self.agent.DrivingInput)
        navigation_binding = self._candidate_local_navigation_binding
        # The semantic candidate identity of this forward.  An enriched candidate
        # states it explicitly because RuntimeCandidate.candidate_id may carry a
        # per-forward repetition identity instead; plain candidates keep using
        # candidate_id, so the legacy path is unchanged.
        semantic_candidate_id = str(
            getattr(candidate, "semantic_candidate_id", None)
            or candidate.candidate_id
        )
        if navigation_binding is not None:
            if (
                str(getattr(navigation_binding, "candidate_id", ""))
                != semantic_candidate_id
            ):
                raise RuntimeError("LOCAL_NAVIGATION_CANDIDATE_MISMATCH")
            if str(getattr(navigation_binding, "interpretation_id", "")) != str(
                candidate.interpretation_id
            ):
                raise RuntimeError("LOCAL_NAVIGATION_INTERPRETATION_MISMATCH")
            if str(
                getattr(navigation_binding, "planning_observation_id", "")
            ) != str(episode.vision_observation.observation_id) or str(
                getattr(navigation_binding, "planning_frame_id", "")
            ) != str(episode.vision_observation.frame_id):
                raise RuntimeError("LOCAL_NAVIGATION_SOURCE_IDENTITY_MISMATCH")
            candidate_obligation_digest = getattr(
                candidate, "obligation_digest", None
            )
            if candidate_obligation_digest is not None and str(
                candidate_obligation_digest
            ) != str(getattr(navigation_binding, "obligation_digest", "")):
                raise RuntimeError("LOCAL_NAVIGATION_OBLIGATION_DIGEST_MISMATCH")
        model_input, built = self.adapter.adapt_driving_input(
            base,
            candidate.prompt_text,
            float(episode.ego_state.speed_mps),
            preserve_placeholder_values=False,
            navigation_binding=navigation_binding,
        )
        model = self.agent.model
        inner_model = getattr(model, "model", model)
        training = tuple((module, bool(module.training)) for module in model.modules())
        cache_state = SimLingoCandidateForwardProvider._cache_state(inner_model)
        if any(
            not SimLingoCandidateForwardProvider._empty_cache_value(value)
            for _, _, value in cache_state
        ):
            raise RuntimeError("OFFICIAL_DREAMING_PERSISTENT_MODEL_CACHE_PRESENT")
        output_state = {
            name: getattr(inner_model, name, None)
            for name in ("route", "speed_wps", "language")
        }
        cpu_rng = torch.random.get_rng_state()
        cuda_rng = torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
        started = time.monotonic()
        try:
            model.eval()
            inner_model.route = None
            inner_model.speed_wps = None
            inner_model.language = []
            try:
                parameter_dtype = next(model.parameters()).dtype
                autocast_scope = (
                    torch.autocast(device_type="cuda", dtype=torch.float16)
                    if parameter_dtype == torch.float32
                    else nullcontext()
                )
                with torch.inference_mode(), autocast_scope:
                    pred_speed, pred_route_raw, language = model(
                        model_input, return_language=True
                    )
            except Exception as error:
                image_token_id = self.agent.tokenizer.convert_tokens_to_ids(
                    "<IMG_CONTEXT>"
                )
                image_token_count = int(
                    (built.prompt_inference.phrase_ids == image_token_id).sum().item()
                )
                raise RuntimeError(
                    "OFFICIAL_DREAMING_FORWARD_FAILED:"
                    + str(error)
                    + ":camera_shape="
                    + repr(tuple(model_input.camera_images.shape))
                    + ":image_context_tokens="
                    + str(image_token_count)
                    + ":prompt_token_count="
                    + str(int(built.prompt_inference.phrase_valid.sum().item()))
                ) from error
            self._event_calls += 1
            raw_cpu = pred_route_raw[0].detach().float().cpu()
            equal_np = np.asarray(
                model.equal_spacing_route(raw_cpu), dtype=np.float32
            )
            pred_route_equal = torch.as_tensor(
                equal_np,
                dtype=torch.float32,
                device=pred_route_raw.device,
            ).unsqueeze(0)
            pred_route_raw = pred_route_raw.float().detach().clone()
            pred_speed = pred_speed.float().detach().clone()
            pred_route_equal = pred_route_equal.detach().clone()
            generated = list(language or ())
        finally:
            try:
                torch.random.set_rng_state(cpu_rng)
                if cuda_rng is not None:
                    torch.cuda.set_rng_state_all(cuda_rng)
            finally:
                SimLingoCandidateForwardProvider._restore_cache_state(
                    inner_model, cache_state
                )
                for module, was_training in training:
                    module.training = was_training
                for name, value in output_state.items():
                    setattr(inner_model, name, value)
        ended = time.monotonic()
        route = _points(pred_route_equal)
        speed_plan = _points(pred_speed)
        if route is None or speed_plan is None:
            raise RuntimeError("OFFICIAL_DREAMING_CANDIDATE_OUTPUT_SHAPE_INVALID")
        plan = CandidatePlan(
            candidate_id=candidate.candidate_id,
            source_observation_id=episode.vision_observation.observation_id,
            source_frame_id=episode.vision_observation.frame_id,
            route=route,
            speed=speed_plan,
            language=(candidate.prompt_text,),
            model_forward_sequence_id="official-dreaming-forward-" + candidate.candidate_id,
            latency_s=ended - started,
        )
        (
            target_tokens,
            navigation_points,
            placeholder_evidence,
        ) = self._target_point_wire_evidence(built, navigation_binding)
        evidence = {
            "adapter": self.adapter.implementation_id,
            "candidate_id": semantic_candidate_id,
            "source_observation_id": episode.vision_observation.observation_id,
            "source_frame_id": episode.vision_observation.frame_id,
            "candidate_prompt_sha256": canonical_sha256(candidate.prompt_text),
            "forwarded_prompt_text": built.prompt_body,
            "full_inference_prompt": built.inference_prompt,
            "forwarded_prompt_sha256": canonical_sha256(built.prompt_body),
            "full_inference_prompt_sha256": hashlib.sha256(
                built.inference_prompt.encode("utf-8")
            ).hexdigest(),
            "prompt_token_ids_sha256": _tensor_sha256(
                built.prompt_inference.phrase_ids
            ),
            "attention_mask_sha256": _tensor_sha256(
                built.prompt_inference.phrase_valid
            ),
            "marker": MARKER,
            "navigation": (
                "OMITTED_OFFICIAL_NO_NAVIGATION_BRANCH"
                if navigation_binding is None
                else "R4_4_CANDIDATE_BOUNDED_LOCAL_ROUTE_AS_TARGET_POINTS"
            ),
            "target_point_placeholder_count": target_tokens,
            "target_point_embedding_injected": navigation_binding is not None,
            "placeholder_values": placeholder_evidence,
            "raw_route": _points(pred_route_raw),
            "equal_spaced_route": route,
            "raw_route_sha256": _tensor_sha256(pred_route_raw),
            "equal_spaced_route_sha256": _tensor_sha256(pred_route_equal),
            "speed_tensor_sha256": _tensor_sha256(pred_speed),
            "generated_language": generated[0] if generated else "",
            "route_sha256": m3_canonical_sha256(route),
            "speed_sha256": evaluate_pid_desired_speed_v0(speed_plan).source_digest,
            "model_forward_count": 1,
            "official_chat_template_function_reused": True,
            "official_forward_entry": "DrivingModel.forward(return_language=True)",
            "official_equal_spacing_function_reused": True,
            "fresh_candidate_conditioned_model_execution": True,
            "candidate_input_cloned": True,
            "candidate_specific_target_point": navigation_binding is not None,
            "torch_inference_only": True,
            "rng_restored_after_forward": True,
            "model_eval_verified": True,
            "model_training_state_restored_after": all(
                bool(module.training) is was_training
                for module, was_training in training
            ),
            "started_monotonic": started,
            "ended_monotonic": ended,
        }
        if navigation_binding is not None:
            evidence.update(
                {
                    "local_navigation_obligation_id": str(
                        navigation_binding.obligation_identity
                    ),
                    "local_navigation_obligation_digest": str(
                        navigation_binding.obligation_digest
                    ),
                    "local_navigation_branch_digest": str(
                        navigation_binding.branch_digest
                    ),
                    "local_navigation_target_digest": str(
                        navigation_binding.target_digest
                    ),
                    "global_destination_identity": str(
                        navigation_binding.global_destination_identity
                    ),
                    "mission_context_digest": str(
                        navigation_binding.mission_context_digest
                    ),
                    "planning_observation_id": str(
                        navigation_binding.planning_observation_id
                    ),
                    "planning_frame_id": navigation_binding.planning_frame_id,
                    "target_points_ego_local_xy_m": [
                        list(row) for row in navigation_points or ()
                    ],
                    "target_point_projection_digest": str(
                        navigation_binding.projection_digest
                    ),
                    "cause_of_difference": "PASSENGER_INTERPRETATION",
                    "candidate_specific_full_global_route_input": False,
                }
            )
        return CandidateForwardResult(
            plan=plan,
            # The legacy field name is retained by the shared contract, but
            # the value is now the official equal-spaced route.
            raw_route=pred_route_equal,
            raw_speed=pred_speed,
            forward_evidence=evidence,
        )
