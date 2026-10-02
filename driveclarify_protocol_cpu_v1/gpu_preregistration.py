"""Pure-CPU preregistration for the controlled official GPU forward matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .contracts import canonical_sha256
from .fixtures import CANDIDATE_FIXTURE_IDS, PROMPT_A, PROMPT_B


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_SHA256 = "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
RGB_SHA256 = "49131c87b5ce8e7bec52cb377f74f0379abd7df453447569c56c019104f98c58"
RECEIPT_SHA256 = "a01f83341857e04f22682318d3f8e556c3ca9da84b9427b9c694b21dbdd4e5f8"


def _semantics(fixture_id: str, label: str, prompt: str) -> str:
    if fixture_id == "NOT_DISTINCT_SEMANTIC_EQUIVALENCE":
        return canonical_sha256([fixture_id, "same-obligation"])
    return canonical_sha256([fixture_id, label, prompt])


def build() -> dict[str, Any]:
    schedule: list[dict[str, Any]] = []
    fixtures: list[dict[str, Any]] = []
    ordinal = 0
    for fixture_id in CANDIDATE_FIXTURE_IDS:
        prompts = [PROMPT_A, PROMPT_B]
        if fixture_id == "NOT_DISTINCT_SEMANTIC_EQUIVALENCE":
            prompts = [PROMPT_A, PROMPT_A]
        candidate_ids = [f"{fixture_id}-A", f"{fixture_id}-B"]
        semantic_digests = [
            _semantics(fixture_id, label, prompt)
            for label, prompt in zip(("A", "B"), prompts)
        ]
        bundle = {
            "fixture_id": fixture_id,
            "observation_id": "R9-STATIC-TRAIN-OBSERVATION",
            "frame_id": 410,
            "rgb_sha256": RGB_SHA256,
            "checkpoint_sha256": CHECKPOINT_SHA256,
            "prompts": prompts,
            "candidate_ids": candidate_ids,
            "candidate_semantic_digests": semantic_digests,
            "execution_device_class": "CUDA",
            "provider_class": "OfficialDreamingCandidateForwardProvider",
        }
        input_bundle_sha256 = canonical_sha256(bundle)
        fixtures.append(
            {
                "fixture_id": fixture_id,
                "canonical_input": bundle,
                "input_bundle_sha256": input_bundle_sha256,
                "fixture_id_is_non_authoritative_metadata": True,
                "certification_requires_separate_replayable_evidence": True,
                "repeat_stability_is_measured_not_assumed": True,
            }
        )
        for repeat_index in range(1, 5):
            for candidate_order_index, (label, prompt) in enumerate(zip(("A", "B"), prompts)):
                ordinal += 1
                row = {
                    "ordinal": ordinal,
                    "fixture_id": fixture_id,
                    "repeat_index": repeat_index,
                    "candidate_order_index": candidate_order_index,
                    "candidate_label": label,
                    "candidate_id": candidate_ids[candidate_order_index],
                    "interpretation_id": "interpretation-" + semantic_digests[candidate_order_index][:20],
                    "prompt_text": prompt,
                    "prompt_sha256": canonical_sha256(prompt),
                    "semantic_digest": semantic_digests[candidate_order_index],
                    "input_bundle_sha256": input_bundle_sha256,
                }
                row["schedule_row_sha256"] = canonical_sha256(row)
                schedule.append(row)
    value = {
        "schema_version": "driveclarify.method_v1_r4.gpu_forward_matrix_preregistration.v1",
        "status": "FROZEN_BEFORE_STATIC_GPU_AUDIT_AND_SMOKE",
        "scientific_attempt_count": 0,
        "carla_episode_count": 0,
        "dev_access_count": 0,
        "test_access_count": 0,
        "engineering_smoke": {
            "label": "NONCOUNTED_ENGINEERING_GPU_SMOKE_FORWARD",
            "count": 1,
            "schedule_source_ordinal": 1,
            "counted_matrix_membership": False,
            "one_minimal_dtype_device_repair_if_needed": True,
            "same_signature_repeat_stop": True,
        },
        "matrix_contract": {
            "authorized_counted_forward_count": 48,
            "retry_forward_count": 0,
            "fixture_count": 6,
            "repeat_count_per_candidate": 4,
            "candidate_count_per_fixture": 2,
            "fixed_order": "fixture_registry_then_repeat_1_to_4_then_A_B",
            "no_output_adaptive_add_delete_or_rerun": True,
        },
        "canonical_gpu": {
            "name": "NVIDIA GeForce RTX 4070 Ti",
            "uuid": "GPU-f59ba35f-9570-f5d8-b194-1e67e6854159",
            "compute_capability": "8.9",
            "production_equivalent_required": True,
            "a800_substitution_authorized": False,
        },
        "frozen_inputs": {
            "checkpoint_sha256": CHECKPOINT_SHA256,
            "rgb_sha256": RGB_SHA256,
            "receipt_sha256": RECEIPT_SHA256,
            "provider_class": "OfficialDreamingCandidateForwardProvider",
            "provider_path": "driveclarify_official_dreaming_adapter.adapter.OfficialDreamingCandidateForwardProvider",
            "preprocessing": "released live JPEG roundtrip/crop/dynamic_preprocess(use_thumbnail=False,max_num=2)/BF16 camera",
            "precision": "unchanged FP32 checkpoint parameters with provider-owned CUDA FP16 autocast; BF16 camera input",
        },
        "fixtures": fixtures,
        "schedule": schedule,
    }
    value["preregistration_sha256"] = canonical_sha256(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": value["status"], "count": len(value["schedule"]), "sha256": value["preregistration_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
