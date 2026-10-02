"""Small TRAIN/diagnostic perception and ambiguity-discovery pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .slot_parser import SemanticSlotParser
from .visual_grounder import GroundingDinoVisualGrounder


Box = Tuple[float, float, float, float]


# Human visual annotations are read only after detector inference and only in
# this post-hoc diagnostic evaluator.  The runtime grounder never imports them.
POSTHOC_GOLD: Mapping[str, Tuple[Box, ...]] = {
    "white van": (
        (469.0, 227.0, 543.0, 301.0),
        (403.0, 250.0, 437.0, 277.0),
    ),
    "red car": ((0.0, 245.0, 100.0, 306.0),),
    "pedestrian": ((845.0, 245.0, 885.0, 360.0),),
    "yellow bus": (),
}


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iou(left: Sequence[float], right: Sequence[float]) -> float:
    ix0, iy0 = max(left[0], right[0]), max(left[1], right[1])
    ix1, iy1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _match(predicted: Sequence[Sequence[float]], gold: Sequence[Sequence[float]]) -> Dict[str, Any]:
    pairs = sorted(
        (
            (_iou(pred_box, gold_box), pred_index, gold_index)
            for pred_index, pred_box in enumerate(predicted)
            for gold_index, gold_box in enumerate(gold)
        ),
        reverse=True,
    )
    used_pred, used_gold, matched = set(), set(), []
    for iou, pred_index, gold_index in pairs:
        if iou < 0.25 or pred_index in used_pred or gold_index in used_gold:
            continue
        used_pred.add(pred_index)
        used_gold.add(gold_index)
        matched.append(
            {"prediction_index": pred_index, "gold_index": gold_index, "iou": iou}
        )
    return {
        "matches": matched,
        "matched_count": len(matched),
        "false_referent_count": len(predicted) - len(matched),
        "missed_referent_count": len(gold) - len(matched),
    }


def run(image_path: Path, live_receipt_path: Path, output_path: Path) -> Mapping[str, Any]:
    import cv2

    image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise FileNotFoundError(str(image_path))
    live = json.loads(live_receipt_path.read_text(encoding="utf-8"))
    if live.get("label_firewall", {}).get("carla_actor_runtime_grounding_reads") != 0:
        raise RuntimeError("LIVE_RECEIPT_PRIVILEGED_GROUNDING_READ")
    grounder = GroundingDinoVisualGrounder(device="cpu")
    perception_cases: List[Dict[str, Any]] = []
    total_gold = total_predictions = total_matches = total_false = 0
    ambiguity_tp = ambiguity_fp = ambiguity_fn = 0
    multi_gold_cases = multi_discovered = 0
    for case_index, (query, gold_boxes) in enumerate(POSTHOC_GOLD.items(), start=1):
        result = grounder.ground(
            image,
            query,
            frame_id=int(live["frame_identity"]["carla_frame"]),
            observation_id="posthoc-pilot-{}".format(case_index),
        )
        predicted_boxes = [item.bbox_xyxy for item in result.selected_referents]
        comparison = _match(predicted_boxes, gold_boxes)
        gold_ambiguous = len(gold_boxes) >= 2
        predicted_ambiguous = result.effective_k >= 2
        if gold_ambiguous and predicted_ambiguous:
            ambiguity_tp += 1
        elif predicted_ambiguous:
            ambiguity_fp += 1
        elif gold_ambiguous:
            ambiguity_fn += 1
        if gold_ambiguous:
            multi_gold_cases += 1
            multi_discovered += int(predicted_ambiguous)
        total_gold += len(gold_boxes)
        total_predictions += len(predicted_boxes)
        total_matches += comparison["matched_count"]
        total_false += comparison["false_referent_count"]
        perception_cases.append(
            {
                "case_id": "P{:02d}".format(case_index),
                "case_type": (
                    "TWO_MATCHING_REFERENTS"
                    if len(gold_boxes) >= 2
                    else "SINGLE_MATCHING_REFERENT"
                    if len(gold_boxes) == 1
                    else "NO_MATCHING_REFERENT"
                ),
                "query": query,
                "provenance": "REAL_TRAIN_RGB_POSTHOC_DIAGNOSTIC",
                "gold_source": "HUMAN_VISUAL_BBOX_ANNOTATION_POSTHOC_ONLY",
                "gold_boxes_xyxy": gold_boxes,
                "grounding": result.to_dict(),
                "comparison": comparison,
                "gold_ambiguous": gold_ambiguous,
                "predicted_ambiguous": predicted_ambiguous,
            }
        )
    parser = SemanticSlotParser()
    contract_specs = (
        ("C05", "OCCLUDED_REFERENT", "Turn after the white van.", "LOW_CONFIDENCE_GROUNDING"),
        ("C06", "MULTIPLE_SAME_CLASS_OBJECTS", "Turn after the white van.", "REFERENTIAL"),
        ("C07", "COLOR_ADJECTIVE", "Turn after the white van.", "REFERENTIAL"),
        ("C08", "RELATIVE_POSITION_PHRASE", "Turn after the white van left of the building.", "REFERENTIAL"),
        ("C09", "TEMPORAL_BUS", "Turn after the bus clears.", "TEMPORAL_TRACKING_REQUIRED"),
        ("C10", "LANDMARK", "Turn at the school.", "LANDMARK"),
        ("C11", "ORDER_NON_VISUAL", "Turn at the second left.", "ROUTE_TOPOLOGY_REQUIRED"),
        ("C12", "UNDERSPECIFIED_CONSTRAINT", "Continue when safe.", "EXISTING_CONSTRAINT_REPRESENTATION"),
        ("C13", "UNAMBIGUOUS", "Continue straight.", "NO_VISUAL_GROUNDING_REQUIRED"),
    )
    contract_cases = []
    for case_id, case_type, instruction, expected in contract_specs:
        parsed = parser.parse(instruction)
        contract_cases.append(
            {
                "case_id": case_id,
                "case_type": case_type,
                "instruction": instruction,
                "parsed_slots": parsed.to_dict(),
                "expected_routing_or_fail_state": expected,
                "detector_forward_count": 0,
                "gold_or_label_used_by_policy": False,
            }
        )
    precision_denominator = ambiguity_tp + ambiguity_fp
    recall_denominator = ambiguity_tp + ambiguity_fn
    metrics = {
        "referent_detection_recall": total_matches / total_gold if total_gold else None,
        "false_referent_rate_per_prediction": total_false / total_predictions if total_predictions else 0.0,
        "correct_grounded_identity_rate": total_matches / total_predictions if total_predictions else None,
        "multi_referent_discovery_rate": multi_discovered / multi_gold_cases if multi_gold_cases else None,
        "ambiguity_detection_precision": ambiguity_tp / precision_denominator if precision_denominator else None,
        "ambiguity_detection_recall": ambiguity_tp / recall_denominator if recall_denominator else None,
        "counts": {
            "gold_referents": total_gold,
            "selected_predictions": total_predictions,
            "matched_referents_iou_gte_0_25": total_matches,
            "false_referents": total_false,
            "ambiguity_true_positive": ambiguity_tp,
            "ambiguity_false_positive": ambiguity_fp,
            "ambiguity_false_negative": ambiguity_fn,
        },
    }
    output = {
        "schema_version": "driveclarify.language_grounding_v1.grounding_pilot.v1",
        "status": "TRAIN_DIAGNOSTIC_PILOT_RECORDED",
        "case_count": len(perception_cases) + len(contract_cases),
        "real_rgb_detector_case_count": len(perception_cases),
        "contract_router_case_count": len(contract_cases),
        "source_live_receipt": str(live_receipt_path.resolve()),
        "source_live_receipt_sha256": _file_sha256(live_receipt_path),
        "source_rgb": str(image_path.resolve()),
        "source_rgb_sha256": _file_sha256(image_path),
        "perception_cases": perception_cases,
        "contract_router_cases": contract_cases,
        "grounding_quality_metrics": metrics,
        "detector_forward_count": grounder.forward_count,
        "visualization_induced_detector_forward_count": 0,
        "label_firewall": {
            "runtime_gold_reads": 0,
            "runtime_actor_truth_reads": 0,
            "posthoc_human_annotation_reads": len(perception_cases),
            "dev_reads": 0,
            "test_reads": 0,
        },
        "scope_limit": (
            "Four perception queries share one real TRAIN frame; the remaining nine cases "
            "validate routing/fail-closed contracts and are not detector-accuracy samples."
        ),
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
    }
    _atomic_json(output_path, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_path", type=Path)
    parser.add_argument("live_receipt_path", type=Path)
    parser.add_argument("output_path", type=Path)
    args = parser.parse_args()
    output = run(args.image_path, args.live_receipt_path, args.output_path)
    print(json.dumps({"status": output["status"], "case_count": output["case_count"], "metrics": output["grounding_quality_metrics"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
