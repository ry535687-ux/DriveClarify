"""Post-episode controlled evidence builder for one immutable native trace."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import DeadlineContract, canonical_sha256
from driveclarify_rq2_t_e2_v3.certification import _project_actor
from driveclarify_rq2_t_e2_v3.usc_admission import project_point_to_polyline

from .contracts import assert_no_true_intent
from .interface import ControlledTemporalMemory, build_views_for_source, make_b0
from .rules import evaluate_rules
from .scene_bindings import frozen_binding


DEADLINE_CONTRACT = DeadlineContract(0.5, 11, 3, 0.05)
COMMITMENT_POINTS = {
    "REFERENTIAL": [47.415340423583984, 4512.07470703125, 372.11944580078125],
    "LANDMARK": [99.00300714163924, 53.22154309617125, 0.0],
    "ORDER": [-711.0115966796875, 3498.056884765625, 362.1084289550781],
    "UNDERSPECIFIED_CONSTRAINT": [47.415340423583984, 4512.07470703125, 372.11944580078125],
}


def _load(path: Path) -> Mapping[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[Mapping[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _route_points(path: Path) -> list[list[float]]:
    route = ET.parse(path).getroot().find("route")
    if route is None:
        raise ValueError("RQ2_T_CG_ROUTE_MISSING")
    return [[float(row.attrib[key]) for key in ("x", "y", "z")] for row in route.findall("./waypoints/position")]


def _crossing_index(progress: Sequence[float], threshold: float) -> int | None:
    return next((index for index, value in enumerate(progress) if value >= threshold - 1e-6), None)


def _geometry_grounded(world: Mapping[str, Any], actor_ids: Sequence[int]) -> tuple[bool, list[Mapping[str, Any]]]:
    projections = [_project_actor(world, actor_id) for actor_id in actor_ids]
    passed = bool(projections) and all(
        row.get("in_frustum") is True
        and float(row.get("pixel_width", 0.0)) >= 5.0
        and float(row.get("pixel_height", 0.0)) >= 5.0
        and float(row.get("frustum_clipped_fraction", 0.0)) >= 0.30
        for row in projections
    )
    return passed, projections


def _event_frame(scenario: Mapping[str, Any], name: str) -> int | None:
    row = next((value for value in scenario.get("event_rows", ()) if value.get("event") == name), None)
    return None if row is None else row.get("first_verified_effective_frame")


def build_episode(output_dir: Path, *, scene_id: str, identity: str, seed: int,
                  route_path: Path) -> Mapping[str, Any]:
    output = Path(output_dir)
    binding = dict(frozen_binding(scene_id))
    assert_no_true_intent(binding)
    scenario = _load(output / "CG_SCENARIO_RECEIPT.json")
    activation = _load(output / "CG_ACTIVATION_RECEIPT.json")
    worlds = _jsonl(output / "post_hoc_world_state.jsonl")
    if not worlds:
        raise RuntimeError("RQ2_T_CG_POSTHOC_TRACE_EMPTY")
    points = _route_points(route_path)
    progress = [float(project_point_to_polyline(row["ego"]["location_xyz"], points)["route_arc_length_m"]) for row in worlds]
    commitment_projection = dict(project_point_to_polyline(COMMITMENT_POINTS[binding["family"]], points))
    owner_path = output / "RQ2_T_2A_OWNER_RECEIPT.json"
    owner = _load(owner_path) if owner_path.is_file() else {}
    owner_terminal = owner.get("terminal_event") if isinstance(owner, Mapping) else None
    if isinstance(owner_terminal, Mapping) and owner_terminal.get("state") == "COMMITMENT_OBSERVED":
        commitment_time_s = float(owner_terminal["simulation_elapsed_s"])
        commitment_index = min(
            range(len(worlds)), key=lambda index: abs(float(worlds[index]["gametime_seconds"]) - commitment_time_s)
        )
        if abs(float(worlds[commitment_index]["gametime_seconds"]) - commitment_time_s) > 0.051:
            raise RuntimeError("RQ2_T_CG_OWNER_COMMITMENT_SOURCE_FRAME_JOIN_FAILED")
        commitment_source = "UNCHANGED_V1_SHARED_PREFIX_OWNER_COMMITMENT_OBSERVED"
    else:
        commitment_index = _crossing_index(progress, float(commitment_projection["route_arc_length_m"]))
        if commitment_index is None:
            raise RuntimeError("RQ2_T_CG_COMMITMENT_NOT_OBSERVED")
        commitment_time_s = float(worlds[commitment_index]["gametime_seconds"])
        commitment_source = "FALLBACK_ROUTE_ARC_CROSSING_FOR_LEGACY_NON_OWNER_TRACE"
    deadline_s = commitment_time_s - DEADLINE_CONTRACT.total_reserved_simulation_s
    activation_arc = float(project_point_to_polyline(
        worlds[0]["ego"]["location_xyz"], points
    )["route_arc_length_m"])
    reveal_frame = _event_frame(scenario, "CERTIFIED_VISIBILITY_REVEAL")
    visibility_end_frame = _event_frame(scenario, "CERTIFIED_VISIBILITY_END")
    invalidation_frame = _event_frame(scenario, "TRACK_IDENTITY_CONFLICT")
    actor_ids = [int(row["actor_id_postepisode_only"]) for row in scenario.get("spawn_rows", ())]
    grounding_threshold = activation_arc + float(binding.get("grounding_progress_after_activation_m", 0.0))
    obligation_threshold = activation_arc + float(binding.get("obligation_progress_after_activation_m", 0.0))
    if binding["template"] == "ORD-LATE-REVEAL":
        obligation_threshold = float(commitment_projection["route_arc_length_m"]) - float(binding["late_reveal_remaining_to_commitment_m"])
    grounding_cross = _crossing_index(progress, grounding_threshold)
    obligation_cross = _crossing_index(progress, obligation_threshold)
    if binding["family"] == "ORDER" and (grounding_cross is None or obligation_cross is None):
        raise RuntimeError("RQ2_T_CG_ROUTE_EVENT_NOT_OBSERVED")
    memory = ControlledTemporalMemory()
    records, geometry_rows = [], []
    invalidation_events_seen: list[Mapping[str, Any]] = []
    for index, world in enumerate(worlds):
        frame = int(world["carla_snapshot_frame"])
        now_s = float(world["gametime_seconds"])
        observation_id = str(world.get("observation_id") or world.get("m2b_decision", {}).get("source_observation_id"))
        precommitment = index <= commitment_index
        invalidated = invalidation_frame is not None and frame >= int(invalidation_frame)
        grounding = False
        projections: list[Mapping[str, Any]] = []
        if binding["family"] in {"REFERENTIAL", "LANDMARK"} and reveal_frame is not None:
            inside = frame >= int(reveal_frame) and (visibility_end_frame is None or frame < int(visibility_end_frame))
            geometry_pass, projections = _geometry_grounded(world, actor_ids)
            grounding = bool(inside and geometry_pass and not invalidated)
        elif binding["template"] == "ORD-ASYNC":
            grounding = index == grounding_cross
        elif binding["template"] == "ORD-LATE-REVEAL":
            grounding = bool(grounding_cross is not None and index >= grounding_cross and precommitment)
        elif binding["template"] == "USC-INTRINSIC":
            grounding = precommitment
        synchronous = bool(binding["current_event_schedule"]["synchronous"])
        if synchronous:
            obligation = grounding
        elif binding["template"] in {"ORD-ASYNC", "ORD-LATE-REVEAL"}:
            obligation = index == obligation_cross
        elif binding["template"] in {"REF-ASYNC", "LMK-ASYNC"}:
            obligation = index == _crossing_index(progress, obligation_threshold)
        elif binding["template"] == "USC-INTRINSIC":
            obligation = precommitment
        else:
            obligation = False
        facts = {
            "candidate_set": bool(precommitment and not invalidated),
            "grounding": bool(grounding), "obligation": bool(obligation),
            "topology": bool(obligation), "holding": bool(precommitment and not invalidated),
            "answer_changes_action": bool(obligation and binding["template"] not in {"NONREVEAL", "USC-INTRINSIC"}),
        }
        if binding["template"] in {"NONREVEAL", "USC-INTRINSIC"}:
            b3_facts = dict(facts)
        else:
            b3_facts = {key: bool(precommitment and not invalidated) for key in facts}
        invalidations = []
        if invalidation_frame is not None and frame == int(invalidation_frame):
            invalidations = [
                "TRACK_IDENTITY_CONFLICT", "SEMANTIC_CANDIDATE_SET_CHANGED",
                "TOPOLOGY_BOUNDARY_PASSED", "HOLDING_LEASE_REVOKED",
            ]
            invalidation_events_seen.append({"frame": frame, "simulation_time_s": now_s, "events": invalidations})
        actor_binding_digest = canonical_sha256({
            "bindings": [row["binding_id"] for row in binding["candidate_bindings"]],
            "valid": not invalidated,
        })
        context = {
            "episode_id": identity, "route_version": "CG_ROUTE_V1",
            "actor_binding_digest": actor_binding_digest,
            "candidate_set_digest": binding["candidate_set_certification"]["candidate_set_digest"] if not invalidated else "INVALIDATED",
            "instruction_digest": canonical_sha256(binding["instruction"]),
            "environment_digest": canonical_sha256({"scene": scene_id, "invalidated": invalidated}),
            "topology_boundary_id": "PRECOMMITMENT_SHARED_BOUNDARY" if not invalidated else "PASSED_OR_INVALIDATED",
            "source_frame_id": frame, "safety_state_digest": "CG_HOLDING_VALID" if not invalidated else "CG_HOLDING_REVOKED",
            "holding_lease_id": "CG_NATIVE_SHARED_PREFIX_LEASE", "dynamics_state_digest": "NATIVE_TRACE",
            "commitment_time_s": commitment_time_s,
        }
        b0 = make_b0(
            frame_id=frame, observation_id=observation_id, now_s=now_s,
            scene_id=scene_id, episode_id=identity, seed=seed,
            ambiguity_type=binding["family"], map_name=binding["town"],
            route_identity="RQ2TCG-" + scene_id,
            commitment_certificate_sha256=canonical_sha256(COMMITMENT_POINTS[binding["family"]]),
        )
        record = build_views_for_source(
            b0=b0, candidate_bindings=binding["candidate_bindings"], facts=facts,
            b3_facts=b3_facts, context=context, memory=memory,
            invalidation_events=invalidations, invalidated=invalidated,
            clarification_deadline_s=deadline_s,
        )
        record = dict(record)
        record["route_progress_m"] = progress[index]
        record["commitment_route_progress_m"] = commitment_projection["route_arc_length_m"]
        record["event_owner_state"] = {
            "grounding": binding["current_event_schedule"]["grounding_owner"],
            "obligation": binding["current_event_schedule"]["obligation_owner"],
            "holding": binding["current_event_schedule"]["holding_owner"],
        }
        record["record_digest"] = canonical_sha256({key: value for key, value in record.items() if key != "record_digest"})
        records.append(record)
        if projections:
            geometry_rows.append({"frame": frame, "simulation_time_s": now_s, "grounding_current": grounding,
                                  "projections": projections})
    rules = evaluate_rules(records)
    paired_path = output / "CG_PAIRED_VIEWS.jsonl"
    paired_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records), encoding="utf-8")
    (output / "CG_RULE_OUTPUTS.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    first = {}
    for view in ("B0", "B1", "B2", "B3"):
        row = next((item for item in records if item["views"][view]["EpistemicEvidenceSufficient"]), None)
        first[view] = None if row is None else {
            "frame": row["source_identity"]["source_frame_id"],
            "simulation_time_s": row["source_identity"]["simulation_time_s"],
            "TTCmt_s": row["views"][view]["TTCmt_s"],
            "ClarificationActionable": row["views"][view]["ClarificationActionable"],
        }
    same_frame_witnesses = [
        row for row in records
        if row["views"]["B1"]["EpistemicEvidenceSufficient"] is False
        and row["views"]["B2"]["EpistemicEvidenceSufficient"] is True
        and row["views"]["B2"]["TTCmt_s"] > 0.0
    ]
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.builder_receipt.v1",
        "identity": identity, "scene_id": scene_id, "template": binding["template"], "seed": seed,
        "native_source_row_count": len(worlds), "paired_row_count": len(records),
        "same_source_identity_all_views": all(row["same_native_source_identity"] for row in records),
        "commitment_time_s": commitment_time_s, "clarification_deadline_s": deadline_s,
        "commitment_source": commitment_source,
        "deadline_contract": DEADLINE_CONTRACT.to_dict(), "first_sufficiency": first,
        "same_frame_b1_false_b2_true_witness_count": len(same_frame_witnesses),
        "false_sufficiency_b1": any(row["views"]["B1"]["EpistemicEvidenceSufficient"] for row in records)
            if binding["template"] in {"NONREVEAL", "USC-INTRINSIC"} else False,
        "false_sufficiency_b2": any(row["views"]["B2"]["EpistemicEvidenceSufficient"] for row in records)
            if binding["template"] in {"NONREVEAL", "USC-INTRINSIC"} else False,
        "fabricated_semantic_resolution_count": sum(
            row["views"][view]["evidence_vector"]["E9_ANSWER_CHANGES_ACTION"]["status"] == "AVAILABLE"
            for row in records for view in ("B1", "B2")
        ) if binding["template"] == "USC-INTRINSIC" else 0,
        "memory_snapshot": memory.snapshot(), "invalidation_events": invalidation_events_seen,
        "geometry_frame_count": len(geometry_rows), "rule_evaluation": rules,
        "controlled_interface_runtime_reads": 0, "vehicle_behavior_changes": 0,
        "production_control_read_or_write_count": 0, "added_vla_forwards": 0,
        "duplicate_candidate_computations": 0, "PID_controller_changes": 0,
        "second_control_writer": 0, "RoutePlanner_mutations": 0, "UKF_mutations": 0,
        "command_history_mutations": 0, "runtime_true_intent_reads": 0,
    }
    if binding["template"] == "ORD-LATE-REVEAL":
        late_row = records[obligation_cross]
        receipt["late_reveal"] = {
            "route_progress_threshold_m": obligation_threshold,
            "first_possible_sufficiency_TTCmt_s": late_row["views"]["B2"]["TTCmt_s"],
            "after_deadline": late_row["views"]["B2"]["remaining_decision_margin_s"] < 0.0,
            "before_commitment": late_row["views"]["B2"]["TTCmt_s"] > 0.0,
        }
    receipt["builder_receipt_digest"] = canonical_sha256(receipt)
    (output / "CG_BUILDER_RECEIPT.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


__all__ = ["COMMITMENT_POINTS", "DEADLINE_CONTRACT", "build_episode"]
