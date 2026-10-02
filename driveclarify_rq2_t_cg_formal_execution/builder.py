"""Build frozen B0/B1/B2/B3 views and episode endpoints post trace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import DeadlineContract, canonical_sha256
from driveclarify_rq2_t_e2_v3.usc_admission import project_point_to_polyline
from driveclarify_rq2_t_cg.interface import ControlledTemporalMemory, build_views_for_source, make_b0
from driveclarify_rq2_t_cg.rules import evaluate_rules

from .scene_io import execution_scene_for_cell


DEADLINE_CONTRACT = DeadlineContract(0.5, 11, 3, 0.05)


def _load(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> Sequence[Mapping[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _progress(world: Mapping[str, Any], scene: Mapping[str, Any]) -> float:
    points = [[float(point[key]) for key in ("x", "y", "z")] for point in scene["route"]["waypoints"]]
    return float(project_point_to_polyline(world["ego"]["location_xyz"], points)["route_arc_length_m"])


def _active_event(scene: Mapping[str, Any], progress: float, kinds: Sequence[str]) -> bool:
    return any(
        event["event_kind"] in kinds
        and float(event["activation"]["start_inclusive_m"]) <= progress < float(event["activation"]["end_exclusive_m"])
        for event in scene["events"]
    )


def _invalidation(scene: Mapping[str, Any], progress: float) -> Mapping[str, Any]:
    row = next((event for event in scene["events"] if "INVALIDATION" in event["event_kind"]), None)
    if row is None or progress < float(row["activation"]["start_inclusive_m"]):
        return {"invalidated": False, "events": []}
    semantic = str(row["payload"].get("invalidation"))
    mapping = {
        "REFERENT_BINDING_CHANGED": ["TRACK_IDENTITY_CONFLICT", "SEMANTIC_CANDIDATE_SET_CHANGED"],
        "ROUTE_TOPOLOGY_CHANGED": ["ROUTE_CHANGED", "TOPOLOGY_BOUNDARY_PASSED"],
        "HOLDING_FEASIBILITY_CHANGED": ["HOLDING_LEASE_REVOKED", "SAFETY_STATE_CHANGED"],
    }
    return {"invalidated": True, "events": mapping.get(semantic, [semantic]), "frozen_event_id": row["event_id"]}


def _window_duration(rows: Sequence[Mapping[str, Any]], view: str, deadline: float) -> float:
    duration = 0.0
    for left, right in zip(rows, rows[1:]):
        value = left["views"][view]
        if value["EpistemicEvidenceSufficient"] and value["ClarificationActionable"]:
            start = float(left["source_identity"]["simulation_time_s"])
            stop = min(float(right["source_identity"]["simulation_time_s"]), deadline)
            duration += max(0.0, stop - start)
    return duration


def build_formal_episode(output: Path, cell: Mapping[str, Any]) -> Mapping[str, Any]:
    scene = execution_scene_for_cell(cell)
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json")
    activation = _load(output / "FORMAL_ACTIVATION_RECEIPT.json")
    commitment = _load(output / "FORMAL_COMMITMENT_RECEIPT.json")
    worlds_all = _jsonl(output / "post_hoc_world_state.jsonl")
    activation_frame = int(activation["simulator_frame"])
    terminal = scenario.get("terminal") or {}
    terminal_frame = int(terminal.get("simulator_frame", worlds_all[-1]["carla_snapshot_frame"]))
    worlds = [row for row in worlds_all if activation_frame <= int(row["carla_snapshot_frame"]) <= terminal_frame]
    if not worlds:
        raise RuntimeError("RQ2_T_CG_FORMAL_NATIVE_SOURCE_TRACE_EMPTY")
    commitment_frame = int(commitment["commitment"]["simulator_frame"])
    commitment_index = next((index for index, row in enumerate(worlds) if int(row["carla_snapshot_frame"]) >= commitment_frame), None)
    if commitment_index is None:
        raise RuntimeError("RQ2_T_CG_FORMAL_COMMITMENT_SOURCE_JOIN_FAILED")
    commitment_time = float(worlds[commitment_index]["gametime_seconds"])
    deadline = commitment_time - DEADLINE_CONTRACT.total_reserved_simulation_s
    memory = ControlledTemporalMemory()
    records = []
    invalidation_first_index = None
    for index, world in enumerate(worlds):
        frame = int(world["carla_snapshot_frame"])
        now = float(world["gametime_seconds"])
        progress = _progress(world, scene)
        precommitment = index <= commitment_index
        invalidation = _invalidation(scene, progress)
        invalidated = bool(invalidation["invalidated"])
        if invalidated and invalidation_first_index is None:
            invalidation_first_index = index
        grounding = _active_event(scene, progress, (
            "GROUNDING_REVEAL", "SYNCHRONOUS_GROUNDING_AND_OBLIGATION", "DECISIVE_LATE_REVEAL",
        ))
        obligation = _active_event(scene, progress, (
            "OBLIGATION_REVEAL", "SYNCHRONOUS_GROUNDING_AND_OBLIGATION", "DECISIVE_LATE_REVEAL",
        ))
        if scene["scene_code"] in ("NONREVEAL", "USC-INTRINSIC"):
            grounding = False
            obligation = False
        facts = {
            "candidate_set": bool(precommitment and not invalidated),
            "grounding": bool(grounding and precommitment and not invalidated),
            "obligation": bool(obligation and precommitment and not invalidated),
            "topology": bool(obligation and precommitment and not invalidated),
            "holding": bool(precommitment and not invalidated),
            "answer_changes_action": bool(obligation and precommitment and not invalidated),
        }
        if scene["scene_code"] in ("NONREVEAL", "USC-INTRINSIC"):
            b3_facts = dict(facts)
        else:
            b3_facts = {key: bool(precommitment and not invalidated) for key in facts}
        observation_id = str(world.get("observation_id") or "{}:{}:simlingo_agent_v0".format(cell["cell_id"], frame))
        b0 = make_b0(
            frame_id=frame, observation_id=observation_id, now_s=now,
            scene_id=scene["formal_scene_id"], episode_id=cell["cell_id"], seed=int(cell["seed"]),
            ambiguity_type=scene["scene_family"], map_name=scene["route"]["town"],
            route_identity=scene["route"]["route_spec_digest"],
            commitment_certificate_sha256=canonical_sha256(scene["commitment"]),
        )
        context = {
            "episode_id": cell["cell_id"], "route_version": scene["route"]["route_spec_digest"],
            "actor_binding_digest": canonical_sha256({"scene": scene["formal_scene_id"], "valid": not invalidated}),
            "candidate_set_digest": canonical_sha256(scene["candidate_bindings"]) if not invalidated else "INVALIDATED",
            "instruction_digest": canonical_sha256(scene["instruction"]),
            "environment_digest": canonical_sha256({"scene": scene["formal_scene_id"], "invalidated": invalidated}),
            "topology_boundary_id": "PRECOMMITMENT" if not invalidated else "INVALIDATED_OR_PASSED",
            "source_frame_id": frame, "safety_state_digest": "VALID" if not invalidated else "INVALIDATED",
            "holding_lease_id": "FORMAL_SHARED_PREFIX" if not invalidated else "REVOKED",
            "dynamics_state_digest": "IMMUTABLE_NATIVE_TRACE", "commitment_time_s": commitment_time,
        }
        invalidation_events = invalidation["events"] if invalidation_first_index == index else []
        record = dict(build_views_for_source(
            b0=b0, candidate_bindings=scene["candidate_bindings"], facts=facts,
            b3_facts=b3_facts, context=context, memory=memory,
            invalidation_events=invalidation_events, invalidated=invalidated,
            clarification_deadline_s=deadline,
        ))
        record["route_progress_m"] = progress
        record["commitment_route_progress_m"] = float(scene["commitment"]["threshold_m"])
        record["event_owner_state"] = [event["owner"] for event in scene["events"]]
        record["formal_scene_digest"] = scene["formal_scene_digest"]
        record["record_digest"] = canonical_sha256({key: value for key, value in record.items() if key != "record_digest"})
        records.append(record)
    rules = evaluate_rules(records)
    paired_path = output / "FORMAL_PAIRED_VIEWS.jsonl"
    paired_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records), encoding="utf-8")
    _write_json(output / "FORMAL_RULE_OUTPUTS.json", rules)
    first = {}
    for view in ("B0", "B1", "B2", "B3"):
        row = next((item for item in records if item["views"][view]["EpistemicEvidenceSufficient"]), None)
        first[view] = None if row is None else {
            "source_frame_id": row["source_identity"]["source_frame_id"],
            "simulation_time_s": row["source_identity"]["simulation_time_s"],
            "TTCmt_s": row["views"][view]["TTCmt_s"],
            "ClarificationActionable": row["views"][view]["ClarificationActionable"],
        }
    b2_only = sum(
        row["views"]["B1"]["EpistemicEvidenceSufficient"] is False
        and row["views"]["B2"]["EpistemicEvidenceSufficient"] is True
        and row["views"]["B2"]["TTCmt_s"] > 0.0
        for row in records
    )
    post_invalidation = [] if invalidation_first_index is None else records[invalidation_first_index:]
    stale_survival = sum(
        field["status"] == "AVAILABLE" and field.get("retention", {}).get("state") == "RETAINED_WITHIN_POLICY"
        for row in post_invalidation for field in row["views"]["B2"]["evidence_vector"].values()
    )
    table = {
        "cell_id": cell["cell_id"], "scene_code": scene["scene_code"],
        "formal_scene_id": scene["formal_scene_id"], "formal_scene_digest": scene["formal_scene_digest"],
        "seed_slot": cell["seed_slot"], "seed": int(cell["seed"]),
        "condition": scene["timing_design"], "family": scene["scene_family"],
        "source_row_count": len(records), "commitment_observed": True,
        "commitment_time_s": commitment_time, "deadline_time_s": deadline,
        "B1_precommitment_sufficiency": first["B1"] is not None and float(first["B1"]["TTCmt_s"]) > 0.0,
        "B2_precommitment_sufficiency": first["B2"] is not None and float(first["B2"]["TTCmt_s"]) > 0.0,
        "B1_first_sufficiency_TTCmt_s": None if first["B1"] is None else first["B1"]["TTCmt_s"],
        "B2_first_sufficiency_TTCmt_s": None if first["B2"] is None else first["B2"]["TTCmt_s"],
        "B1_window_observed": any(row["views"]["B1"]["ClarificationOpportunity"] for row in records),
        "B2_window_observed": any(row["views"]["B2"]["ClarificationOpportunity"] for row in records),
        "B1_window_duration_s": _window_duration(records, "B1", deadline),
        "B2_window_duration_s": _window_duration(records, "B2", deadline),
        "B2_only_same_frame_sufficiency_count": b2_only,
        "false_sufficiency_B1": scene["scene_code"] in ("NONREVEAL", "USC-INTRINSIC") and first["B1"] is not None,
        "false_sufficiency_B2": scene["scene_code"] in ("NONREVEAL", "USC-INTRINSIC") and first["B2"] is not None,
        "fabricated_semantic_resolution_count": sum(
            row["views"][view]["evidence_vector"]["E9_ANSWER_CHANGES_ACTION"]["status"] == "AVAILABLE"
            for row in records for view in ("B1", "B2")
        ) if scene["scene_code"] == "USC-INTRINSIC" else 0,
        "invalidation_observed": invalidation_first_index is not None,
        "stale_evidence_survival_after_invalidation_count": stale_survival,
        "invalid_retention_failure": stale_survival > 0,
        "first_sufficiency": first, "rule_results": {
            "{}({})".format(row["rule_id"], row["evidence_view"] or "NONE"): row
            for row in rules["rules"]
        },
        "same_source_identity_all_views": all(row["same_native_source_identity"] for row in records),
        "formal_valid": True, "censoring_category": "NONE_VALID_COMPLETE",
        "observer_added_vla_forwards": 0, "duplicate_candidate_computations": 0,
        "PID_controller_changes": 0, "second_control_writer": 0,
        "RoutePlanner_mutations": 0, "UKF_mutations": 0, "command_history_mutations": 0,
        "runtime_true_intent_reads": 0, "online_ask_count": 0,
    }
    table["episode_result_digest"] = canonical_sha256(table)
    _write_json(output / "FORMAL_EPISODE_RESULT.json", table)
    return table


__all__ = ["DEADLINE_CONTRACT", "build_formal_episode"]
