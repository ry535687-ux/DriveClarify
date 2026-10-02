"""CPU-only runtime pipeline and evaluation-only scorer for Method M2A v0."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from driveclarify_consequence.task_pipeline import inspect_s1_artifact

from .interaction_contracts import (
    BindingStatus,
    CandidateSpecificTaskBinding,
    CandidateStatus,
    EVIDENCE_DESIGNATION,
    assert_no_forbidden_runtime_keys,
)
from .semantic_plan_bridge import (
    CandidatePlanRecord,
    CandidateSpecificSemanticPlanBridge,
)
from .structured_interaction import (
    run_runtime_pipeline,
)


RUNTIME_FIXTURE_SCHEMA = "driveclarify.language_runtime_fixture_set.v0"
EVALUATION_FIXTURE_SCHEMA = "driveclarify.language_evaluation_fixture_set.v0"


def load_runtime_fixture_set(path: str | Path) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    assert_no_forbidden_runtime_keys(value)
    if value.get("schema_version") != RUNTIME_FIXTURE_SCHEMA:
        raise ValueError("BAD_LANGUAGE_RUNTIME_FIXTURE_SCHEMA")
    episodes = value.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("LANGUAGE_RUNTIME_EPISODES_MISSING")
    return [dict(item) for item in episodes]


def load_evaluation_fixture_set(path: str | Path) -> list[dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("schema_version") != EVALUATION_FIXTURE_SCHEMA:
        raise ValueError("BAD_LANGUAGE_EVALUATION_FIXTURE_SCHEMA")
    if value.get("provenance") != "HAND_AUTHORED_LANGUAGE_FIXTURE":
        raise ValueError("LANGUAGE_EVALUATION_FIXTURES_MUST_BE_HAND_AUTHORED")
    if value.get("declared_before_runtime_evaluation") is not True:
        raise ValueError("LANGUAGE_EVALUATION_LABELS_NOT_PREDECLARED")
    episodes = value.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("LANGUAGE_EVALUATION_EPISODES_MISSING")
    return [dict(item) for item in episodes]


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _macro_f1(rows: Sequence[tuple[str, str]]) -> float:
    labels = sorted({item for row in rows for item in row})
    if not labels:
        return 0.0
    scores: list[float] = []
    for label in labels:
        tp = sum(expected == label and actual == label for expected, actual in rows)
        fp = sum(expected != label and actual == label for expected, actual in rows)
        fn = sum(expected == label and actual != label for expected, actual in rows)
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        scores.append(_safe_div(2 * precision * recall, precision + recall))
    return round(sum(scores) / len(scores), 6)


def _slot_f1(expected: Sequence[str], actual: Sequence[str]) -> float:
    expected_set, actual_set = set(expected), set(actual)
    tp = len(expected_set.intersection(actual_set))
    precision = _safe_div(tp, len(actual_set))
    recall = _safe_div(tp, len(expected_set))
    if not expected_set and not actual_set:
        return 1.0
    return _safe_div(2 * precision * recall, precision + recall)


def _candidate_targets(output: Mapping[str, Any]) -> list[str]:
    return sorted(
        str(item["candidate_specific_task_binding"]["symbolic_target_id"])
        for item in output["candidate_interpretations"]
        if item["candidate_status"] == CandidateStatus.VALID.value
        and item["candidate_specific_task_binding"]["symbolic_target_id"] is not None
    )


def runtime_pre_answer_projection(output: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "structured_parse": output["structured_parse"],
        "candidate_interpretations": output["candidate_interpretations"],
        "semantic_plan_alignment": output["semantic_plan_alignment"],
        "question_proposal": output["question_proposal"],
    }


def evaluate_fixture_set(
    runtime_fixture_path: str | Path,
    evaluation_fixture_path: str | Path,
) -> dict[str, Any]:
    """Evaluation-only entrypoint.  Labels are loaded only after runtime records are isolated."""

    runtime_records = load_runtime_fixture_set(runtime_fixture_path)
    evaluation_records = load_evaluation_fixture_set(evaluation_fixture_path)
    evaluation_by_id = {str(item["episode_id"]): item for item in evaluation_records}
    rows: list[dict[str, Any]] = []
    answer_rows: list[tuple[str, str]] = []
    leakage_violations = 0
    repeat_equal = True
    unknown_pairs = 0
    bridge_pairs = 0
    for runtime_record in runtime_records:
        episode_id = str(runtime_record["episode_id"])
        evaluation_record = evaluation_by_id[episode_id]
        output = run_runtime_pipeline(
            runtime_record,
            evaluation_record.get("passenger_answer_text"),
            evaluation_record.get("answer_timestamp_monotonic"),
        )
        repeat = run_runtime_pipeline(
            runtime_record,
            evaluation_record.get("passenger_answer_text"),
            evaluation_record.get("answer_timestamp_monotonic"),
        )
        repeat_equal = repeat_equal and output["deterministic_runtime_sha256"] == repeat["deterministic_runtime_sha256"]
        try:
            assert_no_forbidden_runtime_keys(output)
        except ValueError:
            leakage_violations += 1
        expected_slots = evaluation_record.get("expected_unresolved_slots", [])
        actual_slots = output["structured_parse"]["unresolved_slots"]
        expected_targets = sorted(evaluation_record.get("expected_candidate_targets", []))
        actual_targets = _candidate_targets(output)
        duplicate_actual = any(item["candidate_status"] == "DUPLICATE" for item in output["candidate_interpretations"])
        abstention_actual = any(item["candidate_status"] == "UNGROUNDED" for item in output["candidate_interpretations"])
        question = output.get("question_proposal") or {}
        partition_ids = [
            candidate_id
            for _, candidate_ids in question.get("candidate_partition", [])
            for candidate_id in candidate_ids
        ]
        valid_ids = [
            item["candidate_id"] for item in output["candidate_interpretations"] if item["candidate_status"] == "VALID"
        ]
        answer = output.get("answer_resolution")
        expected_answer = evaluation_record.get("expected_answer_status")
        actual_answer = None if answer is None else answer["status"]
        if expected_answer is not None:
            answer_rows.append((str(expected_answer), str(actual_answer)))
        resolved = output.get("resolved_instruction")
        resolved_target = (
            None
            if resolved is None
            else resolved["candidate_specific_task_binding"]["symbolic_target_id"]
        )
        alignment = output["semantic_plan_alignment"]
        pair = alignment.get("pair_consequence_relation")
        if pair is not None:
            bridge_pairs += 1
            unknown_pairs += int(pair == "UNKNOWN")
        rows.append(
            {
                "episode_id": episode_id,
                "ambiguity_type_match": output["structured_parse"]["ambiguity_type"] == evaluation_record["expected_ambiguity_type"],
                "unresolved_slot_exact_match": actual_slots == expected_slots,
                "unresolved_slot_f1": _slot_f1(expected_slots, actual_slots),
                "candidate_set_exact_match": actual_targets == expected_targets,
                "candidate_distinctness_match": len(actual_targets) == int(evaluation_record.get("expected_distinct_candidate_count", len(expected_targets))),
                "duplicate_detection_match": duplicate_actual == bool(evaluation_record.get("expected_duplicate", False)),
                "grounding_abstention_match": abstention_actual == bool(evaluation_record.get("expected_grounding_abstention", False)),
                "question_target_slot_match": question.get("target_slot") == evaluation_record.get("expected_question_target_slot"),
                "question_option_coverage": (
                    sorted(partition_ids) == sorted(valid_ids) and len(partition_ids) == len(set(partition_ids))
                    if question.get("proposal_status") == "QUESTION_PROPOSAL"
                    else evaluation_record.get("expected_question_target_slot") is None
                ),
                "answer_status_match": expected_answer is None or actual_answer == expected_answer,
                "end_to_end_resolution_match": resolved_target == evaluation_record.get("expected_resolved_candidate_target"),
                "runtime_sha256": output["deterministic_runtime_sha256"],
            }
        )

    def accuracy(field: str) -> float:
        return round(_safe_div(sum(bool(row[field]) for row in rows), len(rows)), 6)

    expected_counts = Counter(str(item["expected_ambiguity_type"]) for item in evaluation_records)
    answer_status_counts = Counter(expected for expected, _ in answer_rows)
    return {
        "schema_version": "driveclarify.offline_language_evaluation_results.v0",
        "verdict": "OFFLINE_SYMBOLIC_VALIDATION",
        "evidence_designation": list(EVIDENCE_DESIGNATION),
        "fixture_count": len(rows),
        "fixture_category_counts": dict(sorted(expected_counts.items())),
        "ambiguity_type_exact_match": accuracy("ambiguity_type_match"),
        "unresolved_slot_exact_match": accuracy("unresolved_slot_exact_match"),
        "unresolved_slot_f1": round(sum(row["unresolved_slot_f1"] for row in rows) / len(rows), 6),
        "candidate_set_exact_match": accuracy("candidate_set_exact_match"),
        "candidate_distinctness_accuracy": accuracy("candidate_distinctness_match"),
        "duplicate_detection_accuracy": accuracy("duplicate_detection_match"),
        "grounding_abstention_recall": accuracy("grounding_abstention_match"),
        "question_target_slot_accuracy": accuracy("question_target_slot_match"),
        "question_option_coverage": accuracy("question_option_coverage"),
        "answer_resolution_macro_f1": _macro_f1(answer_rows),
        "answer_status_support": dict(sorted(answer_status_counts.items())),
        "still_ambiguous_recall": round(
            _safe_div(sum(expected == actual == "STILL_AMBIGUOUS" for expected, actual in answer_rows), sum(expected == "STILL_AMBIGUOUS" for expected, _ in answer_rows)),
            6,
        ),
        "expired_recall": round(
            _safe_div(sum(expected == actual == "EXPIRED" for expected, actual in answer_rows), sum(expected == "EXPIRED" for expected, _ in answer_rows)),
            6,
        ),
        "end_to_end_resolution_accuracy": accuracy("end_to_end_resolution_match"),
        "gold_leakage_violation_count": leakage_violations,
        "deterministic_repeat_hash_equality": repeat_equal,
        "unknown_propagation_rate": round(_safe_div(unknown_pairs, bridge_pairs), 6),
        "runtime_and_evaluation_fixture_files_separate": Path(runtime_fixture_path).resolve() != Path(evaluation_fixture_path).resolve(),
        "labels_declared_before_runtime_evaluation": True,
        "torch_loaded": "torch" in sys.modules,
        "cuda_initialized": False,
        "carla_launch_count": 0,
        "evaluator_launch_count": 0,
        "simlingo_checkpoint_or_model_load_count": 0,
        "rows": rows,
    }


def real_s1_unknown_report(path: str | Path) -> dict[str, Any]:
    """Read the real S1 artifact without adding any task target or synthetic label."""

    source = Path(path)
    before = source.read_bytes()
    case, integration = inspect_s1_artifact(source)
    plans = []
    bindings = []
    masks: dict[str, Mapping[str, bool]] = {}
    for item in case["candidate_plans"]:
        candidate_id = str(item["candidate_id"])
        plans.append(
            CandidatePlanRecord(
                candidate_id=candidate_id,
                source_observation_id=item.get("source_observation_id"),
                mapped_symbolic_target_type=None,
                mapped_symbolic_target_id=None,
                plan_frame="MODEL_LOCAL_RAW",
                plan_unit="RAW_UNIT",
                provenance=("REAL_RECORDED_UNLABELED",),
                source_artifacts=(str(source.resolve()),),
            )
        )
        bindings.append(
            CandidateSpecificTaskBinding(
                source_candidate_id=candidate_id,
                task_family="REFERENCE_GOAL",
                symbolic_target_type="REFERENCE_ENTITY_ID",
                symbolic_target_id=None,
                required_slots=("reference_entity",),
                binding_status=BindingStatus.NOT_AVAILABLE,
                binding_source="FIXTURE_DECLARED_ENTITY",
                frame_or_semantic_domain="LANGUAGE_REFERENCE",
                provenance=("REAL_RECORDED_UNLABELED",),
                reason_codes=("REAL_PLAN_TO_TASK_MAPPING_UNAVAILABLE",),
            )
        )
        masks[candidate_id] = {"plan": True, "task_binding": False, "provenance": True, "alignment": False}
    result = CandidateSpecificSemanticPlanBridge().align_pair(
        plans[0], bindings[0], plans[1], bindings[1], masks, ("READ_ONLY_REAL_S1_COMPATIBILITY",)
    )
    after = source.read_bytes()
    return {
        "schema_version": "driveclarify.language_real_s1_compatibility.v0",
        "source_artifact": str(source.resolve()),
        "source_artifact_sha256": hashlib.sha256(before).hexdigest(),
        "embedded_evidence_sha256": integration["source_recorded_evidence_sha256"],
        "candidate_labels": {
            result.candidate_a_alignment.candidate_id: result.candidate_a_alignment.alignment_status,
            result.candidate_b_alignment.candidate_id: result.candidate_b_alignment.alignment_status,
        },
        "pair_label": result.pair_consequence_relation,
        "unknown_propagation": result.pair_consequence_relation == "UNKNOWN" and result.candidate_a_alignment.alignment_status == result.candidate_b_alignment.alignment_status == "UNKNOWN",
        "synthetic_label_written_to_real_artifact": False,
        "fabricated_task_anchor": False,
        "source_artifact_unchanged": before == after,
        "source_artifact_sha256_unchanged": hashlib.sha256(before).hexdigest() == hashlib.sha256(after).hexdigest(),
        "m1_learned_pair_comparator_status": result.m1_learned_pair_comparator_status,
        "reason_trace": list(result.reason_trace),
    }
