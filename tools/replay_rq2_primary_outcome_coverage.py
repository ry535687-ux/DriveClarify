#!/usr/bin/env python3
"""Read-only replay of the 72 permanently quarantined RQ2 DEV artifacts."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

REPOSITORY_IMPORT_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_IMPORT_ROOT))

from driveclarify_t_mvp.outcome_coverage import (
    ENDPOINT_REQUIREMENTS,
    EvidenceGrade,
    PredicateResolution,
    ValueState,
    create_primary_outcome_coverage_receipt,
    continuity_outcomes,
    endpoint_eligible,
    known_outcome,
    matched_endpoint_denominator,
    not_applicable_outcome,
    official_checkpoint_outcomes,
    predicate_resolution_outcomes,
    receipt_to_mapping,
    recovery_outcomes,
    unresolved_outcome,
)
from driveclarify_t_mvp.metrics import EgoSample


QUARANTINED_SEEDS = frozenset({9468329, 9674011, 9674027})
METHODS = ("T-B1", "T-B2", "T-B3", "T-B4", "T-B5", "T-B6")
BUCKETS = (
    "T1_BEFORE_COMMITMENT",
    "T2_NEAR_COMMITMENT",
    "T3_POST_COMMIT_RECOVERABLE",
    "T4_NO_SAFE_CURRENT_OPPORTUNITY",
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _legacy_rows(path: Path) -> Mapping[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows[str(row["case_id"])] = row
    return rows


def _known(value: Any) -> bool:
    return value not in (None, "UNKNOWN", "NOT_APPLICABLE")


def _legacy_metric_outcome(
    *,
    outcome_name: str,
    value: Any,
    source: Path,
    version: str,
    evidence_required: bool = True,
) -> Any:
    if not _known(value):
        return unresolved_outcome(
            outcome_name,
            state=ValueState.UNKNOWN,
            reason_code="LEGACY_COLLECTOR_VALUE_UNAVAILABLE",
            metric_definition_version=version,
            evidence_required=evidence_required,
            evidence_available=source.is_file(),
            evidence_source=str(source) if source.is_file() else None,
            evidence_digest=_sha(source) if source.is_file() else None,
            evaluator_identity="legacy DEV postprocessor; diagnostic only",
            evidence_grade=(
                EvidenceGrade.LEGACY_DIAGNOSTIC if source.is_file() else EvidenceGrade.NONE
            ),
        )
    return known_outcome(
        outcome_name,
        value,
        evidence_source=str(source),
        evidence_digest=_sha(source),
        evaluator_identity="frozen DEV postprocessor plus immutable raw artifacts",
        metric_definition_version=version,
        evidence_grade=EvidenceGrade.FROZEN_DERIVED,
        evidence_required=evidence_required,
    )


def _trace_range(attempt: Path) -> Mapping[str, Any]:
    paths = sorted((attempt / "agent_evidence" / "boundaries").glob("boundary_*.json"))
    if not paths:
        return {
            "source": None,
            "digest": None,
            "frame_start": None,
            "frame_end": None,
            "time_start": None,
            "time_end": None,
        }
    first, last = _load(paths[0]), _load(paths[-1])
    digest = hashlib.sha256(
        "".join(_sha(path) for path in paths).encode("ascii")
    ).hexdigest()
    return {
        "source": str(attempt / "agent_evidence" / "boundaries"),
        "digest": digest,
        "frame_start": int(first["sim_frame"]),
        "frame_end": int(last["sim_frame"]),
        "time_start": float(first["sim_time_s"]),
        "time_end": float(last["sim_time_s"]),
    }


def _unadjudicated_metric(name: str, reason: str) -> Any:
    return unresolved_outcome(
        name,
        state=ValueState.UNKNOWN,
        reason_code=reason,
        metric_definition_version="REQUIRES_SEPARATE_SCIENTIFIC_DECISION",
        evidence_required=False,
    )


def _continuity_from_attempt(
    row: Mapping[str, Any], attempt: Path
) -> Mapping[str, Any]:
    names = ("steering_discontinuity", "heading_discontinuity", "jerk")
    boundaries = sorted((attempt / "agent_evidence" / "boundaries").glob("boundary_*.json"))
    controls = {
        int(value["sim_frame"]): value
        for value in (
            _load(path)
            for path in sorted((attempt / "agent_evidence" / "controls").glob("control_*.json"))
        )
    }
    effect_time = row["cell_admissibility_receipt"].get("obligation_sim_time_s")
    if not row["analysis_eligible"] or effect_time is None:
        return {
            name: unresolved_outcome(
                name,
                state=ValueState.UNKNOWN,
                reason_code="SCIENTIFIC_EFFECT_EVENT_UNAVAILABLE_FOR_CONTINUITY_JOIN",
                metric_definition_version="driveclarify.rq2.continuity.v1",
                evidence_available=bool(boundaries),
                evidence_source=str(attempt / "agent_evidence") if boundaries else None,
                evidence_digest=(
                    hashlib.sha256(
                        "".join(_sha(path) for path in boundaries).encode("ascii")
                    ).hexdigest()
                    if boundaries
                    else None
                ),
                evaluator_identity=(
                    "read-only CARLA ego/control observer" if boundaries else None
                ),
                evidence_grade=(
                    EvidenceGrade.RAW_OBSERVER if boundaries else EvidenceGrade.NONE
                ),
                source_clock="CARLA_SIMULATION_TIME" if boundaries else None,
            )
            for name in names
        }
    samples = []
    previous = None
    for path in boundaries:
        value = _load(path)
        frame = int(value["sim_frame"])
        ego = value["ego"]
        control = controls.get(frame)
        samples.append(
            EgoSample(
                frame=frame,
                sim_time_s=float(value["sim_time_s"]),
                position_xyz_m=tuple(float(item) for item in ego["location_xyz_m"]),
                yaw_degrees=float(ego["rotation_pitch_yaw_roll_deg"][1]),
                acceleration_world_mps2=tuple(
                    float(item) for item in ego["acceleration_world_mps2"]
                ),
                ego_right_unit_world=tuple(
                    float(item) for item in ego["right_unit_world"]
                ),
                steer_applied=None if control is None else float(control["steer"]),
                preceding_frame_gap_identified=(
                    previous is None or frame == previous + 1
                ),
                event_id="boundary-" + str(frame),
            )
        )
        previous = frame
    return continuity_outcomes(
        samples,
        t_effect_s=float(effect_time),
        evidence_source=str(attempt / "agent_evidence"),
    )


def _outcomes_for_row(
    row: Mapping[str, Any],
    legacy: Mapping[str, Any],
    replay_source: Path,
) -> tuple[list[Any], Mapping[str, Any]]:
    metrics = row["metrics"]
    attempt = Path(str(row["raw_attempt_path"]))
    checkpoint_path = attempt / "official_checkpoint.json"
    official = official_checkpoint_outcomes(checkpoint_path)
    trace = _trace_range(attempt)
    outcomes: list[Any] = []
    bucket = str(row["timing_bucket"])
    eligible = bool(row["analysis_eligible"])
    timed_out = row["process_terminal_status"] == "SCIENTIFIC_NONCOMPLETION_TIMEOUT"

    outcomes.append(
        _legacy_metric_outcome(
            outcome_name="local_task_success",
            value=metrics["local_semantic_adaptation_success"],
            source=replay_source,
            version="driveclarify.rq2.local_task.v1",
        )
    )
    global_outcome = official["final_global_task_completion"]
    if (
        global_outcome.value_state == ValueState.UNKNOWN.value
        and timed_out
        and trace["source"] is not None
    ):
        global_outcome = unresolved_outcome(
            "final_global_task_completion",
            state=ValueState.RIGHT_CENSORED,
            reason_code="FROZEN_35S_WINDOW_ENDED_BEFORE_OFFICIAL_FINAL_G_TERMINAL",
            metric_definition_version="driveclarify.rq2.global_task.v1",
            evidence_available=True,
            evidence_source=trace["source"],
            evidence_digest=trace["digest"],
            evaluator_identity="read-only trace plus missing official final-G terminal",
            evidence_grade=EvidenceGrade.RAW_OBSERVER,
            source_frame_start=trace["frame_start"],
            source_frame_end=trace["frame_end"],
            source_time_start_s=trace["time_start"],
            source_time_end_s=trace["time_end"],
            source_clock="CARLA_SIMULATION_TIME",
        )
    outcomes.append(global_outcome)
    outcomes.extend(
        official[name]
        for name in ("collision", "route_deviation", "outside_route_lanes")
    )
    outcomes.append(
        _unadjudicated_metric(
            "off_road",
            "FROZEN_PRIMARY_SOURCE_IS_AGGREGATE_OUTSIDE_ROUTE_LANES_NOT_DISTINCT_OFF_ROAD",
        )
    )
    outcomes.append(
        _unadjudicated_metric(
            "wrong_lane",
            "WRONG_LANE_TEST_NOT_INSTANTIATED_BY_FROZEN_ROUTE_SCENARIO",
        )
    )
    outcomes.append(
        _legacy_metric_outcome(
            outcome_name="commitment_violation",
            value=metrics["commitment_violation"],
            source=replay_source,
            version="driveclarify.rq2.commitment_violation.v1",
        )
    )

    continuity = _continuity_from_attempt(row, attempt)
    outcomes.extend(
        continuity[name]
        for name in ("steering_discontinuity", "heading_discontinuity", "jerk")
    )

    if bucket == "T3_POST_COMMIT_RECOVERABLE":
        recovery_resolution = (
            PredicateResolution.RIGHT_CENSORED
            if eligible and timed_out
            else PredicateResolution.UNKNOWN
        )
        recovery = recovery_outcomes(
            resolution=recovery_resolution,
            evidence_source=trace["source"],
            evidence_digest=trace["digest"],
            evaluator_identity=(
                "read-only boundary trace; independent recovery predicate output absent"
                if trace["source"]
                else None
            ),
            source_frame_start=trace["frame_start"],
            source_frame_end=trace["frame_end"],
            source_time_start_s=trace["time_start"],
            source_time_end_s=trace["time_end"],
        )
    else:
        recovery = recovery_outcomes(
            resolution=PredicateResolution.NOT_APPLICABLE,
            evidence_source=None,
            evidence_digest=None,
            evaluator_identity=None,
        )
    outcomes.extend(recovery[name] for name in (
        "recovery_success", "recovery_time_s", "recovery_distance_m"
    ))

    if bucket == "T4_NO_SAFE_CURRENT_OPPORTUNITY":
        rejoin = predicate_resolution_outcomes(
            outcome_name="rejoin_success",
            resolution=PredicateResolution.NOT_APPLICABLE,
            metric_definition_version="driveclarify.rq2.global_task.v1",
            evidence_source=None,
            evidence_digest=None,
            evaluator_identity=None,
        )
    else:
        rejoin = predicate_resolution_outcomes(
            outcome_name="rejoin_success",
            resolution=(
                PredicateResolution.RIGHT_CENSORED
                if eligible and timed_out
                else PredicateResolution.UNKNOWN
            ),
            metric_definition_version="driveclarify.rq2.global_task.v1",
            evidence_source=trace["source"],
            evidence_digest=trace["digest"],
            evaluator_identity=(
                "read-only topology trace; independent rejoin predicate output absent"
                if trace["source"]
                else None
            ),
            source_frame_start=trace["frame_start"],
            source_frame_end=trace["frame_end"],
            source_time_start_s=trace["time_start"],
            source_time_end_s=trace["time_end"],
        )
    outcomes.append(rejoin)

    efficiency_names = {
        "global_planner_calls": "global_planner_calls",
        "local_planner_calls": "local_planner_calls",
        "vla_forwards": "vla_forwards",
        "active_planning_latency_ms": "planning_latency_ms",
    }
    for output_name, legacy_name in efficiency_names.items():
        outcomes.append(
            _legacy_metric_outcome(
                outcome_name=output_name,
                value=metrics[legacy_name],
                source=replay_source,
                version="driveclarify.rq2.efficiency.v1",
            )
        )
    checkpoint_record_exists = False
    if checkpoint_path.is_file():
        checkpoint_value = _load(checkpoint_path)
        checkpoint_records = checkpoint_value.get("_checkpoint", {}).get("records", [])
        checkpoint_record_exists = isinstance(checkpoint_records, list) and bool(checkpoint_records)
    diagnostic = {
        "checkpoint_exists": checkpoint_path.is_file(),
        "official_record_exists": checkpoint_record_exists,
        "legacy_global_known": _known(
            legacy.get("metrics", {}).get("final_global_task_completion")
        ),
        "legacy_global_value": legacy.get("metrics", {}).get(
            "final_global_task_completion", "UNKNOWN"
        ),
        "legacy_recovery_known": _known(legacy.get("metrics", {}).get("recovery_success")),
        "legacy_rejoin_known": _known(legacy.get("metrics", {}).get("rejoin_success")),
        "legacy_steering_known": _known(
            legacy.get("metrics", {}).get("max_steering_discontinuity")
        ),
        "legacy_heading_known": _known(
            legacy.get("metrics", {}).get("max_heading_discontinuity_deg")
        ),
        "legacy_jerk_known": _known(legacy.get("metrics", {}).get("max_jerk_mps3")),
        "trace": trace,
    }
    return outcomes, diagnostic


def _gap_classification(outcome: Mapping[str, Any], diagnostic: Mapping[str, Any]) -> str:
    if outcome["value_state"] == ValueState.KNOWN.value:
        return "NONE_KNOWN"
    if outcome["value_state"] == ValueState.NOT_APPLICABLE.value:
        return "E_NOT_APPLICABLE"
    if outcome["value_state"] == ValueState.RIGHT_CENSORED.value:
        return "F_RIGHT_CENSORED_BY_FROZEN_WINDOW"
    name = outcome["outcome_name"]
    if name in {"off_road", "wrong_lane"}:
        return "H_REQUIRES_SCIENTIFIC_CONTRACT_CHANGE"
    if name == "final_global_task_completion":
        return (
            "G_STRUCTURALLY_UNOBSERVABLE_UNDER_FROZEN_CONTRACT"
            if diagnostic["trace"]["time_end"] is not None
            and diagnostic["trace"]["time_end"] >= 35.0
            else "C_EVALUATOR_OUTPUT_GAP"
        )
    if name in {"collision", "route_deviation", "outside_route_lanes"}:
        return "C_EVALUATOR_OUTPUT_GAP"
    if name in {"recovery_success", "recovery_time_s", "recovery_distance_m", "rejoin_success"}:
        return "D_DEFINITION_ALREADY_FROZEN_BUT_NOT_RECORDED"
    if name in {"steering_discontinuity", "heading_discontinuity", "jerk"}:
        return "A_OBSERVER_PLUMBING_GAP"
    return "B_RECEIPT_JOIN_GAP"


def _forensic_row(
    row: Mapping[str, Any],
    receipt: Mapping[str, Any],
    diagnostic: Mapping[str, Any],
) -> Mapping[str, Any]:
    by_name = {item["outcome_name"]: item for item in receipt["outcomes"]}

    def known(name: str) -> bool:
        return by_name[name]["value_state"] == ValueState.KNOWN.value

    def value(name: str) -> Any:
        item = by_name[name]
        return item["value"] if known(name) else item["value_state"]

    result: dict[str, Any] = {
        "ordinal": int(row["ordinal"]),
        "case_id": row["case_id"],
        "seed": int(row["seed"]),
        "method": row["method_id"],
        "timing_bucket": row["timing_bucket"],
        "analysis_eligible": bool(row["analysis_eligible"]),
        "process_terminal": row["process_terminal_status"],
        "scientific_terminal": row["terminal_status"],
        "local_task_known": known("local_task_success"),
        "local_task_value": value("local_task_success"),
        "global_task_known": known("final_global_task_completion"),
        "global_task_value": value("final_global_task_completion"),
        "legacy_global_task_known": diagnostic["legacy_global_known"],
        "legacy_global_task_value": diagnostic["legacy_global_value"],
        "collision_known": known("collision"),
        "collision_value": value("collision"),
        "route_deviation_known": known("route_deviation"),
        "route_deviation_value": value("route_deviation"),
        "off_road_known": known("off_road"),
        "off_road_value": value("off_road"),
        "outside_route_lanes_known": known("outside_route_lanes"),
        "outside_route_lanes_value": value("outside_route_lanes"),
        "wrong_lane_known": known("wrong_lane"),
        "wrong_lane_value": value("wrong_lane"),
        "steering_discontinuity_known": known("steering_discontinuity"),
        "steering_discontinuity_value": value("steering_discontinuity"),
        "heading_discontinuity_known": known("heading_discontinuity"),
        "heading_discontinuity_value": value("heading_discontinuity"),
        "jerk_known": known("jerk"),
        "jerk_value": value("jerk"),
        "legacy_steering_discontinuity_known": diagnostic["legacy_steering_known"],
        "legacy_heading_discontinuity_known": diagnostic["legacy_heading_known"],
        "legacy_jerk_known": diagnostic["legacy_jerk_known"],
        "t3_recovery_applicable": row["timing_bucket"] == "T3_POST_COMMIT_RECOVERABLE",
        "recovery_success_known": known("recovery_success"),
        "recovery_success_value": value("recovery_success"),
        "recovery_time_known": known("recovery_time_s"),
        "recovery_time_value": value("recovery_time_s"),
        "recovery_distance_known": known("recovery_distance_m"),
        "recovery_distance_value": value("recovery_distance_m"),
        "rejoin_applicable": row["timing_bucket"] != "T4_NO_SAFE_CURRENT_OPPORTUNITY",
        "rejoin_known": known("rejoin_success"),
        "rejoin_value": value("rejoin_success"),
        "official_checkpoint_exists": diagnostic["checkpoint_exists"],
        "official_terminal_record_exists": diagnostic["official_record_exists"],
        "raw_boundary_trace_exists": diagnostic["trace"]["source"] is not None,
    }
    for name, item in by_name.items():
        if item["value_state"] != ValueState.KNOWN.value:
            result[name + "_gap_classification"] = _gap_classification(item, diagnostic)
            result[name + "_source_artifact_expected"] = item.get("evidence_source") or {
                "final_global_task_completion": "official_checkpoint.json terminal record",
                "collision": "official CollisionTest terminal output",
                "route_deviation": "official InRouteTest terminal output",
                "outside_route_lanes": "official OutsideRouteLanesTest terminal output",
                "off_road": "no distinct frozen source",
                "wrong_lane": "no instantiated frozen source",
                "recovery_success": "independent frozen recovery predicate output",
                "recovery_time_s": "recovery predicate plus contiguous ego trace",
                "recovery_distance_m": "recovery predicate plus contiguous ego trace",
                "rejoin_success": "independent frozen rejoin predicate output",
            }.get(name, "frozen observer/evaluator output")
            result[name + "_source_artifact_exists"] = bool(item["evidence_available"])
            result[name + "_raw_evidence_exists_but_unjoined"] = bool(
                item["evidence_available"]
                and name in {
                    "final_global_task_completion",
                    "collision",
                    "route_deviation",
                    "outside_route_lanes",
                    "steering_discontinuity",
                    "heading_discontinuity",
                    "jerk",
                    "recovery_success",
                    "recovery_time_s",
                    "recovery_distance_m",
                    "rejoin_success",
                }
            )
            result[name + "_definition_frozen"] = name not in {"off_road", "wrong_lane"}
            result[name + "_requires_scientific_semantics_change"] = name in {"off_road", "wrong_lane"}
    return result


def _summaries(
    receipts: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    dimensions: list[tuple[str, list[Mapping[str, Any]]]] = [("TOTAL", list(receipts))]
    dimensions.extend(
        ("METHOD:" + method, [row for row in receipts if row["method"] == method])
        for method in METHODS
    )
    dimensions.extend(
        ("TIMING:" + bucket, [row for row in receipts if row["timing_bucket"] == bucket])
        for bucket in BUCKETS
    )
    names = sorted(
        {item["outcome_name"] for receipt in receipts for item in receipt["outcomes"]}
    )
    output: list[Mapping[str, Any]] = []
    for dimension, rows in dimensions:
        for name in names:
            items = [
                next(item for item in row["outcomes"] if item["outcome_name"] == name)
                for row in rows
            ]
            output.append(
                {
                    "dimension": dimension,
                    "endpoint": name,
                    "planned_cells": len(rows),
                    "analysis_eligible_cells": sum(bool(row["analysis_eligible"]) for row in rows),
                    "applicable_cells": sum(item["applicable"] for item in items),
                    "known_cells": sum(item["value_state"] == ValueState.KNOWN.value for item in items),
                    "analysis_eligible_known_cells": sum(
                        row["analysis_eligible"]
                        and item["value_state"] == ValueState.KNOWN.value
                        for row, item in zip(rows, items)
                    ),
                    "unknown_cells": sum(item["value_state"] == ValueState.UNKNOWN.value for item in items),
                    "right_censored_cells": sum(
                        item["value_state"] == ValueState.RIGHT_CENSORED.value for item in items
                    ),
                    "not_applicable_cells": sum(
                        item["value_state"] == ValueState.NOT_APPLICABLE.value for item in items
                    ),
                }
            )
    return output


def _pairwise(receipts: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    by_key = {
        (int(row["seed"]), str(row["timing_bucket"]), str(row["method"])): row
        for row in receipts
    }
    output: dict[str, Any] = {}
    for comparator in ("T-B2", "T-B5"):
        pairs = [
            (
                by_key[(seed, bucket, "T-B6")],
                by_key[(seed, bucket, comparator)],
            )
            for bucket in BUCKETS
            for seed in sorted(QUARANTINED_SEEDS)
        ]
        output["T-B6_vs_" + comparator] = {
            group: matched_endpoint_denominator(pairs, endpoint_group=group)
            for group in ENDPOINT_REQUIREMENTS
        }
    return output


def _write_replay_markdown(path: Path, result: Mapping[str, Any]) -> None:
    lines = [
        "# Historical primary-outcome replay",
        "",
        "This is a read-only diagnostic replay of all 72 quarantined old DEV cells. "
        "CARLA invocations: **0**. The scientific verdict remains "
        "`RQ2_PILOT_INCONCLUSIVE_ENGINEERING`.",
        "",
        "The legacy collector labeled final-G as known in 24/72 cells. Direct official "
        "terminal evidence exists in 18/72 (12/48 admissible); six unsupported legacy "
        "values are right-censored at the frozen window. No proxy was substituted.",
        "",
        "`analysis_eligible_known_cells` is the endpoint denominator available after the "
        "separate intervention-admissibility gate. Counts outside that denominator remain "
        "visible for forensics only.",
        "",
    ]
    by_endpoint: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in result["summaries"]:
        by_endpoint[str(row["endpoint"])].append(row)
    for endpoint in sorted(by_endpoint):
        lines.extend(
            [
                "## " + endpoint,
                "",
                "| Dimension | Planned | Intervention eligible | Applicable | Known | Unknown | Right-censored | Not applicable | Eligible + known |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in by_endpoint[endpoint]:
            lines.append(
                "| {dimension} | {planned_cells} | {analysis_eligible_cells} | "
                "{applicable_cells} | {known_cells} | {unknown_cells} | "
                "{right_censored_cells} | {not_applicable_cells} | "
                "{analysis_eligible_known_cells} |".format(**row)
            )
        lines.append("")
    lines.extend(
        [
            "## Matched endpoint denominators",
            "",
            "| Comparison | Endpoint group | Planned pairs | Eligible pairs | Excluded pairs |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for comparison, groups in result["pairwise_endpoint_denominators"].items():
        for group, value in groups.items():
            lines.append(
                f"| {comparison} | {group} | {value['planned_pair_denominator']} | "
                f"{value['eligible_pair_denominator']} | {value['excluded_pair_count']} |"
            )
    lines.extend(
        [
            "",
            "No paired effect, new RQ2 verdict, seed selection, or episode execution is part "
            "of this replay.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def replay(repository: Path, report_output: Path) -> Mapping[str, Any]:
    gate_report = repository / "reports/driveclarify_rq2_episode_admissibility_and_analysis_gate_v1"
    old_report = repository / "reports/driveclarify_rq2_six_method_native_readiness_repair_and_dev_pilot_v2"
    replay_source = gate_report / "HISTORICAL_REPLAY_RAW_RESULTS.json"
    legacy_source = old_report / "DEV_EPISODE_RESULTS.jsonl"
    rows = _load(replay_source)["episodes"]
    legacy = _legacy_rows(legacy_source)
    if len(rows) != 72 or {int(row["seed"]) for row in rows} != QUARANTINED_SEEDS:
        raise RuntimeError("IMMUTABLE_OLD_DEV_72_CELL_ROSTER_REQUIRED")
    receipts: list[Mapping[str, Any]] = []
    forensic: list[Mapping[str, Any]] = []
    receipt_dir = report_output / "historical_outcome_receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        outcome_rows, diagnostic = _outcomes_for_row(
            row, legacy[str(row["case_id"])], replay_source
        )
        admissibility = row["cell_admissibility_receipt"]
        receipt = create_primary_outcome_coverage_receipt(
            case_id=str(row["case_id"]),
            episode_id=str(row["episode_id"]),
            ordinal=int(row["ordinal"]),
            seed=int(row["seed"]),
            method=str(row["method_id"]),
            timing_bucket=str(row["timing_bucket"]),
            cell_admissibility_digest=str(admissibility["canonical_sha256"]),
            analysis_eligible=bool(row["analysis_eligible"]),
            outcomes=outcome_rows,
        )
        mapping = receipt_to_mapping(receipt)
        receipts.append(mapping)
        forensic.append(_forensic_row(row, mapping, diagnostic))
        _write_json(receipt_dir / (str(row["case_id"]) + ".json"), mapping)

    forensic.sort(key=lambda item: int(item["ordinal"]))
    _write_json(
        report_output / "OLD_DEV_OUTCOME_FORENSICS.json",
        {
            "schema_version": "driveclarify.rq2.old_dev_outcome_forensics.v1",
            "read_only_replay": True,
            "quarantined_seeds": sorted(QUARANTINED_SEEDS),
            "cells": forensic,
        },
    )
    fieldnames = list(forensic[0])
    extras = sorted({key for row in forensic for key in row} - set(fieldnames))
    with (report_output / "OLD_DEV_OUTCOME_FORENSICS.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames + extras, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(forensic)

    summaries = _summaries(receipts)
    replay_result = {
        "schema_version": "driveclarify.rq2.historical_outcome_replay.v1",
        "read_only_replay": True,
        "old_verdict_preserved": "RQ2_PILOT_INCONCLUSIVE_ENGINEERING",
        "planned_cells": len(receipts),
        "analysis_eligible_cells": sum(bool(row["analysis_eligible"]) for row in receipts),
        "receipt_coverage_complete_cells": sum(
            bool(row["primary_outcome_coverage_complete"]) for row in receipts
        ),
        "legacy_global_task_known_cells": sum(
            bool(row["legacy_global_task_known"]) for row in forensic
        ),
        "authoritative_global_task_known_cells": sum(
            bool(row["global_task_known"]) for row in forensic
        ),
        "authoritative_global_task_known_eligible_cells": sum(
            bool(row["global_task_known"] and row["analysis_eligible"])
            for row in forensic
        ),
        "summaries": summaries,
        "pairwise_endpoint_denominators": _pairwise(receipts),
        "scientific_episodes_started": 0,
        "carla_invocations": 0,
    }
    _write_json(report_output / "HISTORICAL_OUTCOME_REPLAY.json", replay_result)
    _write_replay_markdown(report_output / "HISTORICAL_OUTCOME_REPLAY.md", replay_result)
    return replay_result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.repository.resolve(), args.report_output.resolve())
    print(json.dumps({
        "planned_cells": result["planned_cells"],
        "analysis_eligible_cells": result["analysis_eligible_cells"],
        "legacy_global_task_known_cells": result["legacy_global_task_known_cells"],
        "authoritative_global_task_known_cells": result["authoritative_global_task_known_cells"],
        "receipt_coverage_complete_cells": result["receipt_coverage_complete_cells"],
        "carla_invocations": result["carla_invocations"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
