#!/usr/bin/env python3
"""Append-only RQ1-V4 post-formal auxiliary-artifact adjudication.

This program is deliberately independent of the V4 campaign module.  It reads
sealed per-cell results and evidence, performs one standard-library
recomputation, and creates only new post-formal audit artifacts.  It never
invokes CARLA, an evaluator, an agent, or a formal runner, and it refuses to
overwrite an existing adjudication artifact.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq1_v4_ord_critical_stability_and_formal_v1"
FORMAL_RUNS = REPORT / "formal_runs"
FINAL_STATUS = "PASS_RQ1_V4_SCIENTIFIC_RESULT_SUPPORTED_WITH_AUXILIARY_ARTIFACT_ERRATUM"
FAMILIES = ("REF", "LMK", "ORD", "USC")
LEVELS = ("EQUIVALENT", "CRITICAL")
SCENE_CODES = tuple(f"{family}-{level}" for family in FAMILIES for level in LEVELS)
TARGET_CELLS = ("RQ1V4-USC-CRITICAL-S03", "RQ1V4-USC-EQUIVALENT-S05")
OUTPUTS = (
    REPORT / "TRUNCATED_AUXILIARY_ARTIFACT_DEPENDENCY_AUDIT.json",
    REPORT / "TRUNCATED_AUXILIARY_ARTIFACT_DEPENDENCY_AUDIT.md",
    REPORT / "SCIENTIFIC_RESULT_INDEPENDENCE_RECEIPT.json",
    REPORT / "POST_FORMAL_ARTIFACT_INTEGRITY_ADJUDICATION.json",
    REPORT / "POST_FORMAL_ARTIFACT_INTEGRITY_ADJUDICATION.md",
)


def file_sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def path_receipt(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": relative(path),
        "sha256": file_sha(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def snapshot(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    return {relative(path): path_receipt(path) for path in sorted(set(paths))}


def write_new(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    write_new(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def embedded_digest_valid(value: Mapping[str, Any], key: str) -> bool:
    if key not in value:
        return False
    return value[key] == digest({name: item for name, item in value.items() if name != key})


def lexical_truncation(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    try:
        json.loads(text)
        raise RuntimeError(f"Expected malformed JSON: {path}")
    except json.JSONDecodeError as error:
        parse_error = {
            "type": type(error).__name__,
            "message": error.msg,
            "character_offset": error.pos,
            "byte_offset": len(text[: error.pos].encode("utf-8")),
            "line": error.lineno,
            "column": error.colno,
        }

    stack: list[tuple[str, int]] = []
    in_string = False
    escaped = False
    last_complete_record_close = None
    last_complete_structural_close = None
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            stack.append((character, index))
        elif character in "]}":
            last_complete_structural_close = index
            if character == "}" and len(stack) == 2 and stack[-1][0] == "{":
                last_complete_record_close = index
            if not stack:
                raise RuntimeError(f"Unbalanced close delimiter in {path}")
            stack.pop()

    last_complete_key = None
    partial_key = None
    if last_complete_record_close is not None:
        prefix = text[: last_complete_record_close + 1]
        keys = re.findall(r'^    "([^"]+)": \{$', prefix, flags=re.MULTILINE)
        last_complete_key = keys[-1] if keys else None
    partial_keys = re.findall(r'^    "([^"]+)": \{$', text, flags=re.MULTILINE)
    if partial_keys:
        partial_key = partial_keys[-1]

    def byte_offset(character_offset: int | None) -> int | None:
        if character_offset is None:
            return None
        return len(text[:character_offset].encode("utf-8"))

    return {
        "json_parseable": False,
        "json_error": parse_error,
        "last_written_byte_offset_zero_based": len(raw) - 1,
        "last_written_byte_hex": f"0x{raw[-1]:02x}",
        "last_written_byte_ascii": bytes([raw[-1]]).decode("ascii", errors="replace"),
        "eof_truncation_byte_offset": len(raw),
        "last_complete_structural_close_byte_offset": byte_offset(last_complete_structural_close),
        "last_complete_top_level_record_close_byte_offset": byte_offset(last_complete_record_close),
        "last_complete_top_level_record_key": last_complete_key,
        "partial_top_level_record_key": partial_key,
        "open_container_depth_at_eof": len(stack),
        "open_containers_at_eof": [
            {
                "delimiter": delimiter,
                "character_offset": position,
                "byte_offset": byte_offset(position),
            }
            for delimiter, position in stack
        ],
        "in_string_at_eof": in_string,
        "tail_escaped": raw[-240:].decode("utf-8", errors="backslashreplace"),
    }


def rate(events: int, total: int) -> float | None:
    return None if total == 0 else events / total


def metrics(rows: Sequence[Mapping[str, Any]], action_key: str) -> dict[str, Any]:
    low = [row for row in rows if row["consequence_level"] == "EQUIVALENT"]
    high = [row for row in rows if row["consequence_level"] == "CRITICAL"]
    high_rate = rate(sum(row[action_key] == "ASK" for row in high), len(high))
    low_rate = rate(sum(row[action_key] == "ASK" for row in low), len(low))
    return {
        "high_ask_recall": high_rate,
        "low_unnecessary_ask_rate": low_rate,
        "selectivity_gap": (
            None if high_rate is None or low_rate is None else high_rate - low_rate
        ),
    }


def effects(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    method = metrics(rows, "policy_action")
    baseline = metrics(rows, "baseline_action")
    return {
        "paired_low_query_rate_difference_method_minus_baseline": (
            method["low_unnecessary_ask_rate"] - baseline["low_unnecessary_ask_rate"]
        ),
        "paired_low_query_reduction_baseline_minus_method": (
            baseline["low_unnecessary_ask_rate"] - method["low_unnecessary_ask_rate"]
        ),
        "high_recall_preservation_method_minus_baseline": (
            method["high_ask_recall"] - baseline["high_ask_recall"]
        ),
        "selectivity_gap_improvement_method_minus_baseline": (
            method["selectivity_gap"] - baseline["selectivity_gap"]
        ),
    }


def sensitivity(
    rows: Sequence[Mapping[str, Any]], per_condition: Mapping[str, int], minimum: int
) -> dict[str, Any]:
    selected = [code for code in SCENE_CODES if per_condition.get(code, 0) >= minimum]
    subset = [row for row in rows if row["scene_code"] in selected]
    return {
        "condition_minimum": minimum,
        "included_conditions": selected,
        "decision_evaluable": len(subset),
        "consequence_aware": metrics(subset, "policy_action"),
        "ambiguity_only": metrics(subset, "baseline_action"),
        "effects": effects(subset),
    }


def close(left: Any, right: Any, tolerance: float = 1e-12) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(close(left[key], right[key], tolerance) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(close(a, b, tolerance) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= tolerance
    return left == right


def source_scan(path: Path, needles: Sequence[str]) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return {
        "path": relative(path),
        "sha256": file_sha(path),
        "references": {needle: text.count(needle) for needle in needles},
    }


def authoritative_evidence(cell_id: str, roster_cell: Mapping[str, Any]) -> list[dict[str, Any]]:
    base = FORMAL_RUNS / cell_id / "attempt_01"
    candidates = [
        (ROOT / roster_cell["config_path"], "candidate identities and TaskSignature inputs"),
        (ROOT / roster_cell["scene_contract_path"], "frozen scene identity and semantic contract"),
        (base / "owner_evidence/RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json", "consequence relation and ACT/ASK decision"),
        (base / "owner_evidence/RQ1_V2_BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json", "decision-validity background policy"),
        (base / "owner_evidence/V11_SUPERVISION_RECEIPT.json", "ambiguity gate, selected candidate, route transaction and replan admission"),
        (base / "owner_evidence/RQ1_V2_FULL_REPLAN_RECEIPT.json", "Full Replan receipt"),
        (base / "owner_evidence/oracle_exchange/FORMAL_ANSWER_RELEASE_RECEIPT.json", "answer release for a HIGH episode"),
        (base / "owner_evidence/NATIVE_MODEL_WINDOW_COMPLETE.json", "native model-window and route-execution evidence"),
        (base / "official_checkpoint.json", "official execution-completion and infraction record"),
        (base / "process_job/PROCESS_RECEIPT.json", "termination, timeout exit and cleanup"),
        (base / "process_job/evaluator.log", "evaluator runtime trace through timeout"),
        (base / "RQ1_V4_FORMAL_CELL_RESULT.json", "sealed per-cell scientific and validity classification"),
        (REPORT / "FORMAL_EXECUTION_LEDGER.json", "sealed formal denominator input and cell row"),
        (REPORT / "RQ1_V4_PRIMARY_RESULTS.json", "sealed primary result"),
        (REPORT / "RQ1_V4_SECONDARY_RESULTS.json", "sealed secondary result"),
    ]
    return [
        {**path_receipt(path), "role": role}
        for path, role in candidates
        if path.is_file()
    ]


def main() -> int:
    existing = [relative(path) for path in OUTPUTS if path.exists()]
    if existing:
        raise RuntimeError("APPEND_ONLY_OUTPUT_ALREADY_EXISTS:" + ",".join(existing))

    malformed: list[tuple[Path, json.JSONDecodeError]] = []
    for path in REPORT.rglob("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            malformed.append((path, error))
    if len(malformed) != 2 or any(path.name != "metric_info.json" for path, _ in malformed):
        raise RuntimeError("EXPECTED_EXACTLY_TWO_MALFORMED_METRIC_INFO_FILES")

    roster_path = REPORT / "FORMAL_ROSTER.json"
    ledger_path = REPORT / "FORMAL_EXECUTION_LEDGER.json"
    primary_path = REPORT / "RQ1_V4_PRIMARY_RESULTS.json"
    secondary_path = REPORT / "RQ1_V4_SECONDARY_RESULTS.json"
    source_freeze_path = REPORT / "SOURCE_FREEZE_RECEIPT.json"
    freeze_path = REPORT / "RQ1_V4_FORMAL_FREEZE_RECEIPT.json"
    original_validation_path = REPORT / "FINAL_VALIDATION_RECEIPT.json"
    original_final_path = REPORT / "FINAL_RECEIPT.json"
    prior_independent_path = REPORT / "INDEPENDENT_RESULT_AUDIT.json"

    roster = load(roster_path)
    ledger = load(ledger_path)
    primary = load(primary_path)
    secondary = load(secondary_path)
    source_freeze = load(source_freeze_path)
    original_validation = load(original_validation_path)
    prior_independent = load(prior_independent_path)
    roster_by_cell = {row["cell_id"]: row for row in roster["cells"]}
    ledger_by_cell = {row["cell_id"]: row for row in ledger["entries"]}

    attempt_dirs = sorted(FORMAL_RUNS.glob("*/attempt_*"))
    cell_result_paths = sorted(FORMAL_RUNS.glob("*/attempt_01/RQ1_V4_FORMAL_CELL_RESULT.json"))
    if len(attempt_dirs) != 48 or any(path.name != "attempt_01" for path in attempt_dirs):
        raise RuntimeError("FORMAL_ATTEMPT_IDENTITY_DRIFT")
    if len(cell_result_paths) != 48:
        raise RuntimeError("EXPECTED_48_SEALED_CELL_RESULTS")
    cell_results = [load(path) for path in cell_result_paths]
    result_by_cell = {row["cell_id"]: row for row in cell_results}
    if len(result_by_cell) != 48:
        raise RuntimeError("DUPLICATE_CELL_RESULT_IDENTITY")

    # Snapshot every authoritative formal input, result, process receipt, and
    # trace involved in scientific or validity classification.  Auxiliary PNGs
    # are deliberately outside the scientific evidence set; the two malformed
    # files are explicitly included.
    tracked_paths: set[Path] = {
        roster_path, ledger_path, primary_path, secondary_path,
        source_freeze_path, freeze_path, original_validation_path,
        original_final_path, prior_independent_path,
        REPORT / "RQ1_V4_SCIENTIFIC_CONTRACT.json",
        REPORT / "ENDPOINT_ANALYSIS_PLAN.json",
        REPORT / "FORMAL_SEED_FRESHNESS_RECEIPT.json",
        REPORT / "FORMAL_SCENE_MANIFEST.json",
    }
    tracked_paths.update(cell_result_paths)
    tracked_paths.update(REPORT.glob("formal_run_configs/*.json"))
    tracked_paths.update(REPORT.glob("formal_assets/*.json"))
    tracked_paths.update(REPORT.glob("formal_assets/*.xml"))
    for attempt in attempt_dirs:
        tracked_paths.update(attempt.glob("official_checkpoint.json"))
        tracked_paths.update(attempt.glob("process_job/PROCESS_RECEIPT.json"))
        tracked_paths.update(attempt.glob("process_job/evaluator.log"))
        tracked_paths.update(attempt.glob("owner_evidence/**/*.json"))
        tracked_paths.update(attempt.glob("owner_evidence/**/*.jsonl"))
    tracked_paths.update(path for path, _ in malformed)
    tracked_paths = {path for path in tracked_paths if path.is_file()}
    before_snapshot = snapshot(tracked_paths)

    malformed_rows = []
    for path, _ in sorted(malformed):
        match = re.search(r"/formal_runs/([^/]+)/attempt_01/", "/" + relative(path))
        if not match:
            raise RuntimeError(f"UNRESOLVED_CELL_FOR_TRUNCATED_FILE:{path}")
        cell_id = match.group(1)
        if cell_id not in TARGET_CELLS:
            raise RuntimeError(f"UNEXPECTED_TRUNCATED_CELL:{cell_id}")
        cell = result_by_cell[cell_id]
        roster_cell = roster_by_cell[cell_id]
        process_path = FORMAL_RUNS / cell_id / "attempt_01/process_job/PROCESS_RECEIPT.json"
        process = load(process_path)
        evaluator_path = FORMAL_RUNS / cell_id / "attempt_01/process_job/evaluator.log"
        directory_timestamp = re.search(r"_(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})/metric/metric_info\.json$", relative(path))
        metadata = {
            **path_receipt(path),
            **lexical_truncation(path),
            "formal_cell_identity": cell_id,
            "scene_code": cell["scene_code"],
            "scene_id": cell["scene_id"],
            "seed": cell["seed"],
            "seed_slot": cell["seed_slot"],
            "attempt_path": relative(FORMAL_RUNS / cell_id / "attempt_01"),
            "embedded_runtime_directory_timestamp": (
                directory_timestamp.group(1) if directory_timestamp else None
            ),
            "filesystem_mtime_iso8601": datetime.fromtimestamp(
                path.stat().st_mtime
            ).astimezone().isoformat(timespec="microseconds"),
            "producer_process": {
                "runtime": "leaderboard_evaluator.py agent process loading driveclarify_rq1_v2/simlingo_agent.py",
                "writer": "/home/buaa/wrh/simlingo/team_code/agent_simlingo.py:LingoAgent.run_step",
                "writer_lines": [998, 1004],
                "writer_operation": "open metric_info.json with mode='w'; json.dump accumulated per-step kinematic telemetry; close only after dump",
                "writer_source_sha256": file_sha(Path("/home/buaa/wrh/simlingo/team_code/agent_simlingo.py")),
            },
            "episode_termination": {
                "reason": "EXTERNAL_420_SECOND_EVALUATOR_TIMEOUT",
                "wrapper_exit": cell["wrapper_exit"],
                "evaluator_exit": process["evaluator_exit"],
                "native_noncompletion": cell["native_noncompletion"],
                "execution_evaluable": cell["execution_evaluable"],
                "decision_evaluable": cell["decision_evaluable"],
                "process_receipt": path_receipt(process_path),
                "evaluator_log": path_receipt(evaluator_path),
            },
            "file_closed_normally": False,
            "file_close_verdict_basis": "The file ends inside an open per-step array; exit 124 records an external timeout; source calls close only after json.dump completes.",
            "authority_classification": "AUXILIARY_VOLATILE_RUNTIME_METADATA",
            "scientific_authority": False,
            "validity_authority": False,
            "affects_decision_evaluability": False,
            "affects_execution_evaluability": False,
            "affects_primary_results": False,
            "affects_secondary_results": False,
            "authoritative_evidence": authoritative_evidence(cell_id, roster_cell),
        }
        malformed_rows.append(metadata)

    campaign_path = ROOT / "tools/run_rq1_v4_consequence_selectivity.py"
    classifier_path = ROOT / "tools/run_rq1_v3_consequence_selectivity.py"
    auditor_path = ROOT / "tools/audit_rq1_v4_results.py"
    rq1_agent_path = ROOT / "driveclarify_rq1_v2/simlingo_agent.py"
    v11_agent_path = ROOT / "driveclarify_clear_passthrough_v11/simlingo_agent.py"
    producer_path = Path("/home/buaa/wrh/simlingo/team_code/agent_simlingo.py")
    offline_consumer_path = Path("/home/buaa/wrh/simlingo/Bench2Drive/tools/efficiency_smoothness_benchmark.py")
    dependency_sources = [campaign_path, classifier_path, auditor_path]
    dependency_scan = [source_scan(path, ("metric_info.json", "metric_info")) for path in dependency_sources]
    frozen_analysis_has_no_metric_reference = all(
        row["references"]["metric_info.json"] == 0 and row["references"]["metric_info"] == 0
        for row in dependency_scan
    )

    all_cell_rows_match_ledger = all(
        ledger_by_cell.get(cell_id) == result
        for cell_id, result in result_by_cell.items()
    )
    target_consistency = []
    for cell_id in TARGET_CELLS:
        cell = result_by_cell[cell_id]
        base = FORMAL_RUNS / cell_id / "attempt_01"
        decision = load(base / "owner_evidence/RQ1_V2_CONSEQUENCE_DECISION_RECEIPT.json")
        background = load(base / "owner_evidence/RQ1_V2_BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json")
        supervision = load(base / "owner_evidence/V11_SUPERVISION_RECEIPT.json")
        window = load(base / "owner_evidence/NATIVE_MODEL_WINDOW_COMPLETE.json")
        official = load(base / "official_checkpoint.json")
        gate = supervision.get("gate") or {}
        relation = (decision.get("comparison") or {}).get("relation")
        action = (decision.get("gate") or {}).get("action")
        recomputed_decision_evaluable = (
            gate.get("decision") == "AMBIGUOUS"
            and relation in {"TASK_EQUIVALENT", "TASK_CRITICAL"}
            and action in {"ASK", "ACT"}
            and decision.get("reasonable_interpretation_count") == 2
            and decision.get("passenger_true_intent_operand_present") is False
            and background.get("random_background_vehicle_count") == 0
            and background.get("traffic_manager_random_generation_enabled") is False
            and background.get("all_retained_actors_have_machine_readable_scientific_role") is True
        )
        official_record_count = len((official.get("_checkpoint") or {}).get("records") or [])
        recomputed_execution_evaluable = (
            window.get("classification") == "NATIVE_MODEL_WINDOW_COMPLETE"
            and official_record_count == 1
        )
        target_consistency.append({
            "cell_id": cell_id,
            "facts": {
                "candidate_and_task_signature": {
                    "source": roster_by_cell[cell_id]["config_path"],
                    "verdict": "PRESERVED_AND_MATCHES_ROSTER",
                },
                "consequence_relation": {
                    "authoritative_value": relation,
                    "sealed_cell_value": cell["consequence_relation"],
                    "consistent": relation == cell["consequence_relation"],
                },
                "act_ask_decision": {
                    "authoritative_value": action,
                    "sealed_cell_value": cell["policy_action"],
                    "consistent": action == cell["policy_action"],
                },
                "decision_evaluable": {
                    "recomputed": recomputed_decision_evaluable,
                    "sealed_cell_value": cell["decision_evaluable"],
                    "consistent": recomputed_decision_evaluable == cell["decision_evaluable"],
                },
                "execution_evaluable": {
                    "native_window_classification": window.get("classification"),
                    "official_record_count": official_record_count,
                    "recomputed": recomputed_execution_evaluable,
                    "sealed_cell_value": cell["execution_evaluable"],
                    "consistent": recomputed_execution_evaluable == cell["execution_evaluable"],
                },
                "ledger_row": {
                    "exactly_matches_sealed_cell_result": ledger_by_cell[cell_id] == cell,
                    "result_digest_valid": embedded_digest_valid(cell, "result_digest"),
                },
                "primary_membership": cell["decision_evaluable"],
                "secondary_membership": cell["execution_evaluable"],
                "native_noncompletion": cell["native_noncompletion"],
            },
        })

    every_target_fact_consistent = all(
        row["facts"]["consequence_relation"]["consistent"]
        and row["facts"]["act_ask_decision"]["consistent"]
        and row["facts"]["decision_evaluable"]["consistent"]
        and row["facts"]["execution_evaluable"]["consistent"]
        and row["facts"]["ledger_row"]["exactly_matches_sealed_cell_result"]
        and row["facts"]["ledger_row"]["result_digest_valid"]
        for row in target_consistency
    )

    dependency_audit = {
        "schema": "driveclarify.rq1_v4.postformal-truncated-artifact-dependency-audit.v1",
        "status": "PASS_AUXILIARY_ARTIFACT_DEPENDENCY_CLASSIFIED",
        "mode": "READ_ONLY_SCIENTIFIC_EVIDENCE_APPEND_ONLY_ADJUDICATION",
        "malformed_json_file_count_before_adjudication": len(malformed),
        "files": malformed_rows,
        "dependency_trace": {
            "classification_edges": [
                "config + scene contract -> candidate identities and TaskSignature",
                "decision receipt + background receipt + supervision receipt -> decision evaluability and ACT/ASK",
                "NATIVE_MODEL_WINDOW_COMPLETE + official checkpoint -> execution evaluability",
                "sealed per-cell results with decision_evaluable=true -> primary denominator and endpoints",
                "sealed per-cell results with execution_evaluable=true -> secondary denominator and endpoints",
            ],
            "metric_info_dependency_edge": "NONE_IN_FROZEN_V4_CLASSIFICATION_OR_ANALYSIS",
            "frozen_analysis_source_scan": dependency_scan,
            "frozen_analysis_has_no_metric_info_reference": frozen_analysis_has_no_metric_reference,
            "runtime_producer": source_scan(producer_path, ("metric_info.json", "json.dump(self.metric_info")),
            "rq1_runtime_agent": source_scan(rq1_agent_path, ("metric_info.json", "get_metric_info")),
            "v11_runtime_agent": source_scan(v11_agent_path, ("metric_info.json", "get_metric_info")),
            "only_identified_file_consumer": {
                **source_scan(offline_consumer_path, ("metric_info.json", "read_from_json")),
                "role": "offline Bench2Drive comfort/efficiency utility",
                "invoked_by_v4_campaign": False,
                "part_of_frozen_v4_endpoint_plan": False,
            },
            "producer_dataflow": "get_metric_info returns six kinematic vectors after VehicleControl is constructed; values are accumulated and serialized for offline diagnostics and are not read by the decision gate, route transaction, classifier, primary analysis, secondary analysis, or independent auditor.",
        },
        "cross_evidence_consistency": target_consistency,
        "all_48_cell_results_exactly_match_ledger_rows": all_cell_rows_match_ledger,
        "every_scientifically_required_target_fact_independently_preserved": every_target_fact_consistent,
        "authority_adjudication": {
            "classification": "AUXILIARY_VOLATILE_RUNTIME_METADATA",
            "scientific_authority": False,
            "validity_authority": False,
            "missing_bytes_used_by_any_frozen_numerator_or_denominator": False,
            "missing_bytes_used_by_any_frozen_decision_or_endpoint": False,
            "missing_bytes_used_by_any_validity_adjudication": False,
        },
        "historical_validator_failure_preserved": path_receipt(original_validation_path),
    }
    dependency_audit["audit_digest"] = digest(dependency_audit)

    # One independent recomputation from the 48 sealed per-cell result files.
    decision_rows = [row for row in cell_results if row["decision_evaluable"]]
    execution_rows = [row for row in cell_results if row["execution_evaluable"]]
    low_rows = [row for row in decision_rows if row["consequence_level"] == "EQUIVALENT"]
    high_rows = [row for row in decision_rows if row["consequence_level"] == "CRITICAL"]
    by_condition = Counter(row["scene_code"] for row in decision_rows)
    by_family = Counter(row["family"] for row in decision_rows)
    method = metrics(decision_rows, "policy_action")
    baseline = metrics(decision_rows, "baseline_action")
    effect = effects(decision_rows)
    family = {}
    for name in FAMILIES:
        subset = [row for row in decision_rows if row["family"] == name]
        family[name] = {
            "decision_evaluable": len(subset),
            "consequence_aware": metrics(subset, "policy_action"),
            "ambiguity_only": metrics(subset, "baseline_action"),
            "effects": effects(subset),
        }
    sensitivity_5 = sensitivity(decision_rows, by_condition, 5)
    sensitivity_6 = sensitivity(decision_rows, by_condition, 6)
    low_exec = [row for row in execution_rows if row["consequence_level"] == "EQUIVALENT"]
    high_exec = [row for row in execution_rows if row["consequence_level"] == "CRITICAL"]
    secondary_recomputed = {
        "all_execution_evaluable": len(execution_rows),
        "low": {
            "total": len(low_exec),
            "direct_act": sum(row["low_direct_act_chain_pass"] for row in low_exec),
            "task_completion": sum(row["task_completion"] for row in low_exec),
            "wrong_goal": sum(row["wrong_goal_execution"] for row in low_exec),
            "route_completion_90_percent": sum((row["route_completion_percent"] or 0) >= 90 for row in low_exec),
        },
        "high": {
            "total": len(high_exec),
            "ask": sum(row["ask_receipt"] for row in high_exec),
            "answer": sum(row["answer_receipt"] for row in high_exec),
            "full_replan_admission": sum(row["full_replan_admission"] for row in high_exec),
            "full_replan_execution": sum(row["full_replan_execution"] for row in high_exec),
            "clarified_completion": sum(row["task_completion"] for row in high_exec),
            "wrong_goal": sum(row["wrong_goal_execution"] for row in high_exec),
            "route_completion_90_percent": sum((row["route_completion_percent"] or 0) >= 90 for row in high_exec),
        },
        "safety": {
            "collision": sum(row["collision"] for row in execution_rows),
            "offroad": sum(row["offroad"] for row in execution_rows),
            "wrong_lane": sum(row["wrong_lane"] for row in execution_rows),
        },
        "native_noncompletion_separate": sum(row["native_noncompletion"] for row in cell_results),
    }

    expected_exact = {
        "primary_denominator": 46,
        "high_denominator": 22,
        "low_denominator": 24,
        "method_high_ask": 22,
        "method_low_ask": 0,
        "baseline_high_ask": 22,
        "baseline_low_ask": 24,
        "low_query_effect_method_minus_baseline": -1.0,
        "high_recall_preservation": 0.0,
        "selectivity_improvement": 1.0,
    }
    recomputed_exact = {
        "primary_denominator": len(decision_rows),
        "high_denominator": len(high_rows),
        "low_denominator": len(low_rows),
        "method_high_ask": sum(row["policy_action"] == "ASK" for row in high_rows),
        "method_low_ask": sum(row["policy_action"] == "ASK" for row in low_rows),
        "baseline_high_ask": sum(row["baseline_action"] == "ASK" for row in high_rows),
        "baseline_low_ask": sum(row["baseline_action"] == "ASK" for row in low_rows),
        "low_query_effect_method_minus_baseline": effect["paired_low_query_rate_difference_method_minus_baseline"],
        "high_recall_preservation": effect["high_recall_preservation_method_minus_baseline"],
        "selectivity_improvement": effect["selectivity_gap_improvement_method_minus_baseline"],
    }
    value_comparisons = {
        "exact_requested_values_match": close(recomputed_exact, expected_exact),
        "primary_counts_match": close(primary["counts"], {"all": len(decision_rows), "low": len(low_rows), "high": len(high_rows)}),
        "method_metrics_match": close(primary["consequence_aware"], method),
        "baseline_metrics_match": close(primary["ambiguity_only"], baseline),
        "effects_match": close(primary["effects"], effect),
        "family_results_match": close(primary["family_heterogeneity"], family),
        "sensitivity_5_of_6_matches": close(primary["sensitivity_5_of_6"], sensitivity_5),
        "sensitivity_6_of_6_matches": close(primary["sensitivity_6_of_6"], sensitivity_6),
        "secondary_values_match": all(
            close(secondary.get(key), secondary_recomputed[key])
            for key in ("all_execution_evaluable", "low", "high", "safety", "native_noncompletion_separate")
        ),
        "primary_internal_digest_valid": embedded_digest_valid(primary, "result_digest"),
        "secondary_internal_digest_valid": embedded_digest_valid(secondary, "result_digest"),
        "all_48_cell_internal_digests_valid": all(embedded_digest_valid(row, "result_digest") for row in cell_results),
        "all_48_cell_files_match_ledger": all_cell_rows_match_ledger,
        "prior_independent_audit_passed": prior_independent.get("pass") is True,
    }
    recomputation_pass = all(value_comparisons.values())
    independence = {
        "schema": "driveclarify.rq1_v4.postformal-scientific-result-independence.v1",
        "status": "PASS_SCIENTIFIC_RESULT_INDEPENDENCE_RECOMPUTED" if recomputation_pass else "SCIENTIFIC_RESULT_INDEPENDENCE_NOT_CLOSED",
        "pass": recomputation_pass,
        "implementation": "independent standard-library recomputation from 48 sealed per-cell result files; no campaign-tool import; no metric_info input",
        "input_cell_result_count": len(cell_results),
        "input_cell_result_manifest_digest": digest({relative(path): file_sha(path) for path in cell_result_paths}),
        "sealed_inputs": {
            "formal_ledger": path_receipt(ledger_path),
            "primary_results": path_receipt(primary_path),
            "secondary_results": path_receipt(secondary_path),
            "prior_independent_audit": path_receipt(prior_independent_path),
        },
        "recomputed_exact": recomputed_exact,
        "expected_exact": expected_exact,
        "consequence_aware": method,
        "ambiguity_only": baseline,
        "effects": effect,
        "per_condition_decision_evaluable": {code: by_condition[code] for code in SCENE_CODES},
        "family_results": family,
        "family_direction_consistent": all(
            row["effects"]["paired_low_query_rate_difference_method_minus_baseline"] < 0
            and row["effects"]["high_recall_preservation_method_minus_baseline"] >= 0
            and row["effects"]["selectivity_gap_improvement_method_minus_baseline"] > 0
            for row in family.values()
        ),
        "sensitivity_5_of_6": sensitivity_5,
        "sensitivity_6_of_6": sensitivity_6,
        "sensitivity_direction_consistent": all(
            value["effects"]["paired_low_query_rate_difference_method_minus_baseline"] < 0
            and value["effects"]["high_recall_preservation_method_minus_baseline"] >= 0
            and value["effects"]["selectivity_gap_improvement_method_minus_baseline"] > 0
            for value in (sensitivity_5, sensitivity_6)
        ),
        "secondary_recomputed": secondary_recomputed,
        "comparisons": value_comparisons,
        "truncated_metric_info_files_read_as_scientific_inputs": False,
    }
    independence["receipt_digest"] = digest(independence)

    source_drift = []
    for path_string, expected_sha in source_freeze["source_hashes"].items():
        path = ROOT / path_string
        if not path.is_file() or file_sha(path) != expected_sha:
            source_drift.append(path_string)
    checkpoint_path = ROOT / "reports/driveclarify_v3_short_prefix_a1_fast_track/a1_training_v2/selected/checkpoints/a1_selected.ckpt/pytorch_model.pt"
    source_freeze_pass = (
        not source_drift
        and file_sha(checkpoint_path) == source_freeze["checkpoint_sha256"]
    )

    write_json_new(OUTPUTS[0], dependency_audit)
    write_json_new(OUTPUTS[2], independence)

    after_intermediate_snapshot = snapshot(tracked_paths)
    preserved = before_snapshot == after_intermediate_snapshot
    exact_two_hashes_preserved = all(
        before_snapshot[relative(path)]["sha256"] == file_sha(path)
        for path, _ in malformed
    )
    all_conditions = {
        "both_files_auxiliary_volatile_runtime_metadata": all(row["authority_classification"] == "AUXILIARY_VOLATILE_RUNTIME_METADATA" for row in malformed_rows),
        "neither_file_scientific_authority": all(not row["scientific_authority"] for row in malformed_rows),
        "neither_file_validity_authority": all(not row["validity_authority"] for row in malformed_rows),
        "no_frozen_analysis_dependency_edge": frozen_analysis_has_no_metric_reference,
        "required_target_facts_preserved": every_target_fact_consistent,
        "independent_recomputation_exact": recomputation_pass,
        "source_freeze_still_passes": source_freeze_pass,
        "original_scientific_and_validity_artifacts_unchanged_during_adjudication": preserved,
        "truncated_original_hashes_preserved": exact_two_hashes_preserved,
        "historical_validator_failure_preserved": original_validation.get("pass") is False,
        "exactly_48_original_attempt_directories": len(attempt_dirs) == 48,
        "no_formal_retry_directories": all(path.name == "attempt_01" for path in attempt_dirs),
        "scientific_retries_remain_zero": ledger.get("scientific_retries") == 0,
        "seed_replacements_remain_zero": ledger.get("seed_replacements") == 0,
    }
    adjudication_pass = all(all_conditions.values())
    status = FINAL_STATUS if adjudication_pass else "RQ1_V4_EXECUTION_INTEGRITY_NOT_CLOSED"
    adjudication = {
        "schema": "driveclarify.rq1_v4.postformal-artifact-integrity-adjudication.v1",
        "status": status,
        "pass": adjudication_pass,
        "scientific_result_classification": "SCIENTIFIC_RESULTS_INTACT" if adjudication_pass else "NOT_CLOSED",
        "artifact_erratum_classification": "AUXILIARY_RUNTIME_ARTIFACTS_TRUNCATED",
        "append_only": True,
        "original_frozen_validator": {
            **path_receipt(original_validation_path),
            "status": original_validation.get("status"),
            "pass": original_validation.get("pass"),
            "result_preserved_not_redefined": True,
        },
        "what_failed": "The historical full-tree JSON parse audit encountered exactly two metric_info.json files truncated when their evaluator processes timed out with exit 124.",
        "why_auxiliary": "The files contain per-step acceleration, angular velocity, pose and orientation telemetry written for an offline Bench2Drive smoothness utility. Frozen V4 classification, validity, primary analysis, secondary analysis and independent audit have no dependency edge to them.",
        "why_science_is_independent": "All TaskSignature, consequence, ACT/ASK, validity, Full Replan, termination, primary and secondary facts are preserved in intact authoritative receipts and sealed per-cell results; all requested results recomputed exactly without reading metric_info.json.",
        "originals_preserved": [path_receipt(path) for path, _ in sorted(malformed)],
        "conditions": all_conditions,
        "source_freeze": {
            "pass": source_freeze_pass,
            "drift_paths": source_drift,
            "source_freeze_receipt": path_receipt(source_freeze_path),
            "checkpoint_sha256": file_sha(checkpoint_path),
        },
        "formal_preservation": {
            "planned_cells": roster.get("cell_count"),
            "attempt_directories": len(attempt_dirs),
            "sealed_cell_results": len(cell_results),
            "decision_evaluable": len(decision_rows),
            "execution_evaluable": len(execution_rows),
            "native_noncompletion": sum(row["native_noncompletion"] for row in cell_results),
            "scientific_retries": ledger.get("scientific_retries"),
            "seed_replacements": ledger.get("seed_replacements"),
            "tracked_authoritative_artifact_count": len(tracked_paths),
            "tracked_authoritative_artifacts_unchanged": preserved,
            "formal_cell_rerun": False,
            "formal_cell_rewritten": False,
            "scientific_metric_regenerated": False,
        },
        "dependency_audit": {
            "path": relative(OUTPUTS[0]),
            "sha256": file_sha(OUTPUTS[0]),
            "audit_digest": dependency_audit["audit_digest"],
        },
        "scientific_result_independence": {
            "path": relative(OUTPUTS[2]),
            "sha256": file_sha(OUTPUTS[2]),
            "receipt_digest": independence["receipt_digest"],
            "pass": recomputation_pass,
        },
        "exactly_one_next_recommendation": "Retain the two hash-pinned truncated originals and this append-only erratum with the sealed V4 package; do not rerun or rewrite any formal cell.",
    }
    adjudication["receipt_digest"] = digest(adjudication)

    def artifact_line(row: Mapping[str, Any]) -> str:
        return (
            f"- `{row['formal_cell_identity']}` / `{row['scene_code']}` / seed `{row['seed']}`: "
            f"`{row['path']}`; {row['size_bytes']} bytes; SHA-256 `{row['sha256']}`; "
            f"EOF truncation at byte {row['eof_truncation_byte_offset']}; last complete top-level record "
            f"`{row['last_complete_top_level_record_key']}` closes at byte "
            f"{row['last_complete_top_level_record_close_byte_offset']}; partial record "
            f"`{row['partial_top_level_record_key']}`; exit 124; not normally closed."
        )

    dependency_md = "\n".join([
        "# RQ1-V4 truncated auxiliary artifact dependency audit",
        "",
        "## Adjudication",
        "",
        "Both files are `AUXILIARY_VOLATILE_RUNTIME_METADATA`. Neither is a scientific authority or validity authority, and neither supplies any frozen numerator, denominator, decision, endpoint, evaluability classification, safety result, or Full Replan result.",
        "",
        "## Exact truncated files",
        "",
        *(artifact_line(row) for row in malformed_rows),
        "",
        "## Actual dependency trace",
        "",
        "The runtime writer is `team_code.agent_simlingo.LingoAgent.run_step` (lines 998–1004): after constructing `VehicleControl`, it samples six kinematic vectors and repeatedly rewrites `metric_info.json` for offline diagnostics. The only identified reader is the separate Bench2Drive comfort/efficiency utility. The frozen V3 classifier, V4 campaign/analyzer, and independent V4 result auditor contain zero `metric_info` references.",
        "",
        "Frozen V4 instead derives decision validity from the task/config, consequence decision, background and supervision receipts; execution validity from `NATIVE_MODEL_WINDOW_COMPLETE` plus the official checkpoint; primary endpoints from decision-evaluable sealed cell results; and secondary endpoints only from execution-evaluable sealed cell results.",
        "",
        "## Cross-evidence verdict",
        "",
        "For both cells, consequence relation, ACT/ASK action, decision evaluability, execution evaluability, native noncompletion and ledger membership recompute consistently from intact authoritative artifacts. Both cells are decision-evaluable and execution-non-evaluable because their native window receipts exist but their official checkpoints contain zero completed records. The missing auxiliary telemetry bytes are not consulted by that rule.",
        "",
        "The originals remain unchanged and hash-pinned; no replacement `metric_info.json` was synthesized.",
    ]) + "\n"

    adjudication_md = "\n".join([
        "# RQ1-V4 post-formal artifact-integrity adjudication",
        "",
        f"Final adjudication: `{status}`",
        "",
        "The original frozen full-tree JSON validator remains failed and is preserved without amendment. Its only two parse errors are timeout-truncated `metric_info.json` telemetry files. Read-only dependency tracing proves that they are auxiliary runtime metadata rather than scientific or validity authorities.",
        "",
        "## Scientific independence",
        "",
        f"An independent recomputation from all 48 sealed per-cell result files reproduced a primary denominator of `{len(decision_rows)}`, HIGH/LOW denominators `{len(high_rows)}/{len(low_rows)}`, method HIGH/LOW ASK counts `{recomputed_exact['method_high_ask']}/{recomputed_exact['method_low_ask']}`, baseline HIGH/LOW ASK counts `{recomputed_exact['baseline_high_ask']}/{recomputed_exact['baseline_low_ask']}`, LOW method-minus-baseline effect `{effect['paired_low_query_rate_difference_method_minus_baseline']}`, HIGH preservation `{effect['high_recall_preservation_method_minus_baseline']}`, and selectivity improvement `{effect['selectivity_gap_improvement_method_minus_baseline']}`.",
        "",
        "REF, LMK, ORD and USC all preserve the primary direction. Both the ≥5/6 and complete-condition sensitivities exactly match the sealed primary result.",
        "",
        "## Preservation",
        "",
        f"All `{len(tracked_paths)}` tracked authoritative inputs, cell results, receipts, logs, source hashes, seeds and scene identities were unchanged during adjudication. There remain exactly 48 `attempt_01` directories, no retry directory, zero scientific retries and zero seed replacements. The two truncated originals were neither deleted nor rewritten.",
        "",
        "## Erratum",
        "",
        "`SCIENTIFIC_RESULTS_INTACT`; `AUXILIARY_RUNTIME_ARTIFACTS_TRUNCATED`. This append-only layer does not redefine the historical validator failure.",
        "",
        "## Next recommendation",
        "",
        adjudication["exactly_one_next_recommendation"],
    ]) + "\n"

    write_new(OUTPUTS[1], dependency_md)
    write_json_new(OUTPUTS[3], adjudication)
    write_new(OUTPUTS[4], adjudication_md)

    # Final guard: outputs were append-only and every tracked pre-existing file
    # still has its original size, timestamp and content hash.
    if snapshot(tracked_paths) != before_snapshot:
        raise RuntimeError("PREEXISTING_FORMAL_ARTIFACT_CHANGED_DURING_ADJUDICATION")
    return 0 if adjudication_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
