"""Image-only Grounding DINO adapter with a frozen plausibility filter."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from .contracts import (
    AmbiguityStatus,
    GroundingResult,
    LanguageGroundingContractError,
    VisualReferentCandidate,
    canonical_sha256,
)


MODEL_ID = "IDEA-Research/grounding-dino-tiny"
MODEL_REVISION = "a2bb814dd30d776dcf7e30523b00659f4f141c71"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "pretrained" / "grounding-dino-tiny"
# Detector proposal thresholds are intentionally permissive; the separate,
# frozen plausibility filter makes the policy decision.  These values were
# calibrated on TRAIN diagnostic frame 2561, where the small farther van is a
# 0.099 proposal and the ego-vehicle hood is a 0.051 oversized false proposal.
DETECTOR_BOX_THRESHOLD = 0.05
TEXT_THRESHOLD = 0.05
MIN_DETECTOR_CONFIDENCE = 0.075
PLAUSIBILITY_THRESHOLD = 0.24
MIN_AREA_FRACTION = 0.0005
MAX_AREA_FRACTION = 0.12
MAX_EFFECTIVE_K = 2
COLOR_PIXEL_FRACTION_THRESHOLD = 0.04
COLOR_WORDS = ("white", "black", "red", "blue", "green", "yellow", "silver", "gray", "grey")


def _image_array(image: Any) -> Any:
    import numpy as np

    value = np.asarray(image)
    if value.ndim != 3 or value.shape[2] < 3:
        raise LanguageGroundingContractError("FRONT_RGB_SHAPE_INVALID")
    if value.dtype != np.uint8:
        value = value.astype(np.uint8)
    return np.ascontiguousarray(value)


def _bgr_to_pil(image: Any) -> Any:
    from PIL import Image

    # CARLA/Leaderboard rgb_0 is BGR(A). Work only on a detached copy.
    rgb = image[:, :, :3][:, :, ::-1].copy()
    return Image.fromarray(rgb, mode="RGB")


def _location(box: Tuple[float, float, float, float], width: int) -> str:
    center = 0.5 * (box[0] + box[2]) / float(width)
    return "LEFT" if center < 0.4 else "RIGHT" if center > 0.6 else "CENTER"


def _color_support(
    image: Any, bbox: Tuple[float, float, float, float], phrase: str
) -> Optional[Tuple[str, float]]:
    """Return requested-color pixel support from the detached BGR image copy."""

    import numpy as np

    tokens = set(phrase.casefold().replace("-", " ").split())
    requested = next((item for item in COLOR_WORDS if item in tokens), None)
    if requested is None:
        return None
    requested = "gray" if requested == "grey" else requested
    height, width = image.shape[:2]
    x0, y0, x1, y1 = bbox
    left = max(0, min(width - 1, int(round(x0))))
    top = max(0, min(height - 1, int(round(y0))))
    right = max(left + 1, min(width, int(round(x1))))
    bottom = max(top + 1, min(height, int(round(y1))))
    crop = image[top:bottom, left:right, :3].astype(np.float32)
    blue, green, red = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
    maximum = np.maximum(np.maximum(red, green), blue)
    minimum = np.minimum(np.minimum(red, green), blue)
    if requested == "white":
        mask = (minimum >= 150.0) & ((maximum - minimum) <= 55.0)
    elif requested == "black":
        mask = maximum <= 65.0
    elif requested in {"gray", "silver"}:
        mask = (minimum >= 65.0) & (maximum <= 220.0) & ((maximum - minimum) <= 35.0)
    elif requested == "red":
        mask = (red >= 120.0) & (red >= 1.30 * green) & (red >= 1.30 * blue)
    elif requested == "blue":
        mask = (blue >= 110.0) & (blue >= 1.25 * red) & (blue >= 1.20 * green)
    elif requested == "green":
        mask = (green >= 100.0) & (green >= 1.20 * red) & (green >= 1.20 * blue)
    else:  # yellow
        mask = (red >= 130.0) & (green >= 110.0) & (blue <= 0.70 * np.minimum(red, green))
    return requested, float(mask.mean())


class ReferentPlausibilityFilter:
    """Frozen, label-free scoring over detector evidence only."""

    def score(
        self,
        *,
        confidence: float,
        bbox: Tuple[float, float, float, float],
        width: int,
        height: int,
    ) -> Tuple[float, bool, Tuple[str, ...], float]:
        x0, y0, x1, y1 = bbox
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        area_fraction = area / float(width * height)
        center_x = 0.5 * (x0 + x1) / float(width)
        fov_relevance = max(0.0, 1.0 - abs(center_x - 0.5) / 0.5)
        visibility = 1.0 if x0 >= 1 and y0 >= 1 and x1 <= width - 1 and y1 <= height - 1 else 0.75
        size_score = min(1.0, area_fraction / 0.04)
        score = 0.65 * confidence + 0.15 * size_score + 0.10 * fov_relevance + 0.10 * visibility
        reasons = []
        if confidence < MIN_DETECTOR_CONFIDENCE:
            reasons.append("BELOW_BOX_THRESHOLD")
        if area_fraction < MIN_AREA_FRACTION:
            reasons.append("BBOX_TOO_SMALL")
        if area_fraction > MAX_AREA_FRACTION:
            reasons.append("BBOX_IMPLAUSIBLY_LARGE")
        if score < PLAUSIBILITY_THRESHOLD:
            reasons.append("PLAUSIBILITY_SCORE_BELOW_THRESHOLD")
        return score, not reasons, tuple(reasons), area_fraction


class GroundingDinoVisualGrounder:
    """Transformers-backed Grounding DINO; no CARLA/world object is accepted."""

    detector_id = MODEL_ID
    detector_revision = MODEL_REVISION

    def __init__(
        self,
        model_path: Optional[str] = None,
        *,
        device: str = "cpu",
        processor: Any = None,
        model: Any = None,
    ) -> None:
        self.model_path = str(model_path or DEFAULT_MODEL_PATH)
        self.device = device
        self._processor = processor
        self._model = model
        self._forwards = 0
        self.filter = ReferentPlausibilityFilter()

    @property
    def forward_count(self) -> int:
        return self._forwards

    def _load(self) -> None:
        if self._processor is not None and self._model is not None:
            return
        path = Path(self.model_path)
        if not (path / "model.safetensors").is_file():
            raise FileNotFoundError("GROUNDING_DINO_CHECKPOINT_NOT_AVAILABLE:" + str(path))
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        self._processor = AutoProcessor.from_pretrained(str(path), local_files_only=True)
        self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
            str(path), local_files_only=True, disable_custom_kernels=True
        ).to(self.device)
        self._model.eval()

    @staticmethod
    def _post_process(processor: Any, outputs: Any, inputs: Mapping[str, Any], size: Tuple[int, int]) -> Mapping[str, Any]:
        kwargs = {
            "outputs": outputs,
            "input_ids": inputs.get("input_ids"),
            "box_threshold": DETECTOR_BOX_THRESHOLD,
            "text_threshold": TEXT_THRESHOLD,
            "target_sizes": [size],
        }
        result = processor.post_process_grounded_object_detection(**kwargs)
        return result[0]

    def ground(
        self,
        image: Any,
        phrase: str,
        *,
        frame_id: int,
        observation_id: str,
        captured_monotonic: Optional[float] = None,
    ) -> GroundingResult:
        import torch

        captured = time.monotonic() if captured_monotonic is None else float(captured_monotonic)
        array = _image_array(image)
        image_sha = hashlib.sha256(array.tobytes(order="C")).hexdigest()
        height, width = int(array.shape[0]), int(array.shape[1])
        self._load()
        query = phrase.strip().rstrip(".") + "."
        pil = _bgr_to_pil(array)
        started = time.monotonic()
        inputs = self._processor(images=pil, text=query, return_tensors="pt")
        inputs = {key: value.to(self.device) if hasattr(value, "to") else value for key, value in inputs.items()}
        with torch.inference_mode():
            outputs = self._model(**inputs)
        self._forwards += 1
        detector_done = time.monotonic()
        processed = self._post_process(self._processor, outputs, inputs, (height, width))
        boxes = processed.get("boxes", ())
        scores = processed.get("scores", ())
        labels = processed.get("text_labels", processed.get("labels", ()))
        rows = []
        for index, (box_value, score_value) in enumerate(zip(boxes, scores)):
            box = tuple(float(item) for item in box_value.detach().cpu().tolist())
            confidence = float(score_value.detach().cpu().item())
            label = str(labels[index]) if index < len(labels) else phrase
            plausibility, plausible, rejections, area_fraction = self.filter.score(
                confidence=confidence, bbox=box, width=width, height=height
            )
            color_support = _color_support(array, box, phrase)
            attributes = ()
            if color_support is not None:
                color_name, support_fraction = color_support
                attributes = (
                    ("requested_color", color_name),
                    ("requested_color_pixel_fraction", "{:.6f}".format(support_fraction)),
                )
                if support_fraction < COLOR_PIXEL_FRACTION_THRESHOLD:
                    plausible = False
                    rejections = tuple(rejections) + (
                        "COLOR_ATTRIBUTE_UNSUPPORTED_BY_BOX_PIXELS",
                    )
            local_id = "gdino-" + canonical_sha256(
                {"frame_id": frame_id, "image_sha256": image_sha, "query": query, "bbox": [round(item, 3) for item in box]}
            )[:20]
            rows.append(
                VisualReferentCandidate(
                    local_object_id=local_id,
                    phrase=phrase,
                    bbox_xyxy=box,
                    detector_confidence=confidence,
                    phrase_label=label,
                    frame_id=int(frame_id),
                    observation_id=observation_id,
                    image_sha256=image_sha,
                    captured_monotonic=captured,
                    relative_image_location=_location(box, width),
                    bbox_area_fraction=area_fraction,
                    apparent_size_rank=None,
                    plausibility_score=plausibility,
                    plausible=plausible,
                    freshness_seconds=max(0.0, detector_done - captured),
                    appearance_attributes=attributes,
                    rejection_reasons=rejections,
                )
            )
        ranked = sorted(
            rows,
            key=lambda item: (
                -item.plausibility_score,
                -item.detector_confidence,
                -item.bbox_area_fraction,
                item.bbox_xyxy,
                item.local_object_id,
            ),
        )
        plausible_rows = [item for item in ranked if item.plausible]
        # Identity/prompt ordering is apparent near-to-far (box area), not detector-score order.
        ordered = sorted(
            plausible_rows,
            key=lambda item: (-item.bbox_area_fraction, item.bbox_xyxy[0], item.local_object_id),
        )
        with_ranks = [
            VisualReferentCandidate(**{**item.__dict__, "apparent_size_rank": index})
            for index, item in enumerate(ordered, start=1)
        ]
        selected = tuple(with_ranks[:MAX_EFFECTIVE_K])
        completed = time.monotonic()
        if not rows:
            status = AmbiguityStatus.NO_REFERENT_FOUND
            reasons = ("DETECTOR_RETURNED_NO_MATCH",)
        elif not with_ranks:
            status = AmbiguityStatus.LOW_CONFIDENCE_GROUNDING
            reasons = ("NO_DETECTION_PASSED_PLAUSIBILITY",)
        elif len(with_ranks) == 1:
            status = AmbiguityStatus.SINGLE_REFERENT
            reasons = ("ONE_PLAUSIBLE_VISUAL_REFERENT",)
        elif len(with_ranks) > MAX_EFFECTIVE_K:
            status = AmbiguityStatus.K_EXCEEDS_V1
            reasons = ("TOP2_SELECTED_BY_FROZEN_LABEL_FREE_RANKING",)
        else:
            status = AmbiguityStatus.AMBIGUITY_DETECTED
            reasons = ("MULTIPLE_PLAUSIBLE_VISUAL_REFERENTS",)
        return GroundingResult(
            status=status,
            query=query,
            frame_id=int(frame_id),
            observation_id=observation_id,
            image_sha256=image_sha,
            image_width=width,
            image_height=height,
            detector_id=self.detector_id,
            detector_revision=self.detector_revision,
            raw_referents=tuple(ranked),
            plausible_referents=tuple(with_ranks),
            selected_referents=selected,
            raw_grounding_k=len(rows),
            plausible_k=len(with_ranks),
            effective_k=len(selected),
            discarded_candidate_count=max(0, len(rows) - len(selected)),
            detector_forward_count=1,
            detector_latency_seconds=detector_done - started,
            ambiguity_latency_seconds=completed - detector_done,
            captured_monotonic=captured,
            completed_monotonic=completed,
            cache_status="MISS_NEW_INSTRUCTION",
            privileged_state_read_count=0,
            reason_codes=reasons,
        )
