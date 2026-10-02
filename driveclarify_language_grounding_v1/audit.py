"""First-collapse audit over the frozen TRAIN-only R7 white-van live episode."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import canonical_sha256


DEFAULT_EPISODE_DIR = Path(
    "artifacts/paper_mvp_stage6b_r0_gates/"
    "DC-STAGE6B-R0-GATES-20260811-R7/T3_ASK/"
    "DCV0-S002_seed5103/driveclarify"
)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _frame(path: Path, frame_id: int) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("frame_id") == frame_id:
                return row
    raise ValueError("AUDIT_FRAME_NOT_FOUND")


def _prompt(raw: str, description: str) -> str:
    return raw.strip().rstrip(".?!") + ". Interpret this as " + description.rstrip(".") + "."


def _conditioning(prompt: str, speed: float) -> str:
    return (
        "<INSTRUCTION_FOLLOWING> Current speed: "
        + "{:.1f}".format(speed)
        + " m/s. "
        + prompt
        + " Predict the waypoints."
    )


def build_first_collapse_audit(repo_root: Path, episode_dir: Path) -> dict:
    audit = _load_json(episode_dir / "stage6b_runtime_audit.json")
    launch = _load_json(episode_dir / "EPISODE_LAUNCH_CONTRACT.json")
    semantic = audit["candidate_trace"]
    forms = semantic["canonical_interpretations"]
    forwards = audit["candidate_forwards"]
    frame_id = int(forwards[0]["source_frame_id"])
    trace = _frame(episode_dir / "stage6b_frame_trace.jsonl", frame_id)
    raw = str(audit["raw_instruction"])
    prompts = [_prompt(raw, item["description"]) for item in forms]
    speed = float(trace["ego_speed_mps"])
    conditionings = [_conditioning(prompt, speed) for prompt in prompts]
    prompt_hashes = [canonical_sha256(prompt) for prompt in prompts]
    conditioning_hashes = [canonical_sha256(value) for value in conditionings]
    recorded_prompt_hashes = [item["candidate_prompt_sha256"] for item in forwards]
    recorded_conditioning_hashes = [item["forwarded_prompt_sha256"] for item in forwards]
    binding_source = repo_root / "driveclarify_paper_mvp_runtime" / "simlingo_binding.py"
    binding_bytes = binding_source.read_bytes()
    target_null = all(
        item.get("target_branch") is None and item.get("target_landmark") is None
        for item in forms
    )
    route_hashes = [item["route_sha256"] for item in forwards]
    speed_hashes = [item["speed_sha256"] for item in forwards]
    first_layer = (
        "TARGET_BINDING"
        if target_null
        and semantic.get("consequence_divergence") is False
        and len(set(prompt_hashes)) == 2
        and len(set(route_hashes)) == 2
        else "NO_COLLAPSE_OBSERVED"
    )
    candidates = []
    for label, form, prompt, prompt_hash, conditioning, conditioning_hash, forward in zip(
        ("A", "B"), forms, prompts, prompt_hashes, conditionings, conditioning_hashes, forwards
    ):
        candidates.append(
            {
                "label": label,
                "raw_candidate_text": prompt,
                "canonical_semantic": form,
                "parsed_slots": {
                    "maneuver": form["maneuver"],
                    "referent_phrase": "white van",
                    "temporal_relation": "AFTER",
                    "ordering": form["ordering"],
                    "spatial_relation": form["spatial_relation"],
                    "constraint": form["constraint"],
                },
                "referent_phrase": "white van",
                "grounding_id": form["grounded_identity"],
                "grounding_source_disclosure": (
                    "PRIVILEGED_CARLA_ACTOR_CAMERA_PROJECTION_NOT_IMAGE_ONLY_DETECTOR"
                ),
                "temporal_trigger": form["temporal_trigger"],
                "target_landmark": form["target_landmark"],
                "target_branch": form["target_branch"],
                "maneuver": form["maneuver"],
                "route_context": trace["route_planner_state"],
                "exact_simlingo_conditioning": conditioning,
                "prompt_hash": prompt_hash,
                "conditioning_hash": conditioning_hash,
                "recorded_prompt_hash_matches": prompt_hash == forward["candidate_prompt_sha256"],
                "recorded_conditioning_hash_matches": (
                    conditioning_hash == forward["forwarded_prompt_sha256"]
                ),
                "simlingo_pred_route": {
                    "status": "NOT_PERSISTED_AS_POINTS_IN_R7_AUDIT",
                    "sha256": forward["route_sha256"],
                },
                "simlingo_pred_speed_wps": {
                    "status": "NOT_PERSISTED_AS_POINTS_IN_R7_AUDIT",
                    "sha256": forward["speed_sha256"],
                },
            }
        )
    return {
        "schema_version": "driveclarify.language_grounding_v1.first_collapse_audit.v1",
        "audit_scope": "EXISTING_REAL_TRAIN_ONLY_R7_LIVE_EPISODE_READ_ONLY",
        "source_episode_dir": str(episode_dir.resolve()),
        "episode_id": audit["episode_id"],
        "scenario_id_posthoc_provenance_only": launch["episode"]["scenario_id"],
        "split": "TRAIN",
        "dev_attempt_count": 0,
        "test_attempt_count": 0,
        "test_consumed": False,
        "frame_identity": {
            "frame_id": frame_id,
            "observation_id": forwards[0]["source_observation_id"],
            "vision_observation_sha256": audit["decision_trace"][0]["orchestrator"][
                "candidate_generation_audit"
            ]["vision_observation_sha256"],
            "rgb_bytes_not_persisted_at_decision_frame": True,
        },
        "raw_instruction": raw,
        "candidates": candidates,
        "other_model_input_differences": {
            "observation_same": len({item["source_observation_id"] for item in forwards}) == 1,
            "source_frame_same": len({item["source_frame_id"] for item in forwards}) == 1,
            "route_context_same": True,
            "checkpoint_same": True,
            "model_instance_same": True,
            "candidate_input_cloned": all(item["candidate_input_cloned"] for item in forwards),
            "only_intended_difference": "CANDIDATE_LANGUAGE_CONDITIONING",
        },
        "prompt_hashes_different": len(set(prompt_hashes)) == 2,
        "conditioning_hashes_different": len(set(conditioning_hashes)) == 2,
        "route_divergence": {
            "existing_r0_threshold": "ANY_MATCHED_WAYPOINT_L2_GT_0.5",
            "threshold_result": semantic["trajectory_divergence"],
            "route_hashes_different": len(set(route_hashes)) == 2,
            "exact_magnitude_status": "UNKNOWN_POINTS_NOT_PERSISTED",
        },
        "speed_divergence": {
            "speed_hashes_different": len(set(speed_hashes)) == 2,
            "exact_magnitude_status": "UNKNOWN_POINTS_NOT_PERSISTED",
        },
        "semantic_uniqueness": not semantic["semantic_duplicate"],
        "grounding_uniqueness": not semantic["grounding_duplicate"],
        "consequence_divergence": semantic["consequence_divergence"],
        "first_collapse_layer": first_layer,
        "first_collapse_evidence": {
            "canonical_targets_both_null": target_null,
            "maneuvers_same": len({item["maneuver"] for item in forms}) == 1,
            "shared_route_context_hash": audit["decision_trace"][0]["orchestrator"][
                "candidate_generation_audit"
            ]["route_context_sha256"],
            "runtime_consequence_binding_result_equivalent": (
                semantic["consequence_divergence"] is False
            ),
            "binding_source_path": str(binding_source),
            "binding_source_sha256": hashlib.sha256(binding_bytes).hexdigest(),
            "binding_logic": (
                "_instruction_task_role resolves directionless TURN against the only live "
                "turn branch, and build_runtime_consequence_evaluation applies that shared "
                "instruction role to each candidate."
            ),
        },
        "simlingo_interpretation_sensitivity_limitation": False,
        "simlingo_limitation_reason": (
            "NOT_SUPPORTED_BY_THIS_EPISODE: conditioning and plan hashes differ and the frozen "
            "0.5 m route threshold reports divergence."
        ),
        "grounding_dino_required": True,
        "grounding_dino_reason": (
            "Required to replace privileged CARLA actor projection with real image-only runtime "
            "referent enumeration; it is not the fix for the separately identified target-binding gap."
        ),
        "bytetrack_enabled": False,
        "bytetrack_reason": "Static single-frame white-van audit does not require cross-frame identity.",
        "label_firewall": audit["label_firewall"],
        "forward_accounting": audit["forward_accounting"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--episode-dir", default=str(DEFAULT_EPISODE_DIR))
    parser.add_argument(
        "--output",
        default="reports/language_grounding_v1/FIRST_COLLAPSE_LAYER_AUDIT.json",
    )
    args = parser.parse_args()
    repo = Path(args.repo_root).resolve()
    episode = (repo / args.episode_dir).resolve() if not Path(args.episode_dir).is_absolute() else Path(args.episode_dir)
    output = (repo / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    value = build_first_collapse_audit(repo, episode)
    output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "first_collapse_layer": value["first_collapse_layer"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

