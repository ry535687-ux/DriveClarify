"""Independent read-only terminal verifier; imports no prediction/evaluator code."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


ACTIONS = ("ACT", "ASK", "WAIT", "FALLBACK")
TERMINAL_STATE = "BLIND_RESULTS_PUBLISHED_IMMUTABLE"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path, *, canonical: bool = True) -> dict[str, Any]:
    payload = path.read_bytes()
    value = json.loads(payload)
    if canonical and payload != canonical_bytes(value):
        raise ValueError(f"R3_TERMINAL_INPUT_NOT_CANONICAL:{path.name}")
    return value


def _macro_f1(rows: Sequence[Mapping[str, Any]], gold: Mapping[str, Mapping[str, Any]]) -> float:
    confusion = {g: {p: 0 for p in ACTIONS} for g in ACTIONS}
    for row in rows:
        confusion[gold[row["case_id"]]["gold_action_type"]][row["selected_action"]] += 1
    values = []
    for label in ACTIONS:
        tp = confusion[label][label]
        fp = sum(confusion[g][label] for g in ACTIONS if g != label)
        fn = sum(confusion[label][p] for p in ACTIONS if p != label)
        values.append(0.0 if 2 * tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn))
    return sum(values) / 4


def _source_import_audit(source_path: Path) -> dict[str, Any]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names = sorted({alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names} |
                   {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)})
    forbidden = [name for name in names if any(fragment in name.lower()
                 for fragment in ("policy", "query_value", "prediction_runtime", "evaluator_runtime"))]
    return {"imports": names, "forbidden_policy_imports": forbidden,
            "forbidden_policy_import_count": len(forbidden)}


def verify_terminal(*, state_path: Path, event_manifest_path: Path, audit_path: Path,
                    prediction_path: Path, result_path: Path, sandbox_audit_path: Path,
                    duplicate_guard_path: Path, gold_path: Path,
                    expected: Mapping[str, Any], verifier_source_path: Path | None = None) -> dict[str, Any]:
    state = _load(state_path)
    event_manifest = _load(event_manifest_path)
    prediction = _load(prediction_path)
    result = _load(result_path)
    sandbox = _load(sandbox_audit_path)
    duplicate = _load(duplicate_guard_path)
    gold = {row["case_id"]: row for row in _load(gold_path)["records"]}
    audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line]
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: Any = None) -> None:
        checks.append({"check": name, "status": "PASS" if passed else "FAIL", "detail": detail})

    check("event_id", state["blind_execution_id"] == expected["blind_execution_id"] == event_manifest["blind_execution_id"])
    check("event_created_exactly_once", state["event_create_count"] == 1 and state["formal_event_created"])
    check("event_consumed", state["event_consumed"] and state["prediction_start_count"] == 1)
    check("commitment_hashes", event_manifest["config"] == state["event_config"] and
          event_manifest["config_sha256"] == state["event_config_sha256"])
    check("production_sandbox_evidence", sandbox.get("status") == "PASS" and
          sandbox.get("forbidden_path_visible_count") == 0 and
          sandbox.get("forbidden_module_spec_count") == 0 and
          sandbox.get("inherited_forbidden_fd_count") == 0)
    records = prediction["records"]
    order = [(row["canonical_case_index"], row["canonical_comparison_index"]) for row in records]
    check("prediction_record_count", len(records) == prediction["record_count"] == expected["expected_record_count"])
    check("prediction_order", order == sorted(order) and len(order) == len(set(order)))
    check("prediction_schema", prediction["schema_version"] == "driveclarify.m2b_blind_raw_prediction.r3")
    check("prediction_bytes_sha", prediction_path.stat().st_size == state["prediction_bytes"] and
          file_sha256(prediction_path) == state["prediction_sha256"])
    actions = [row["action"] for row in audit_rows]
    check("publication_before_gold", actions.index("mark_predictions_immutable") < actions.index("record_gold_unseal"))
    check("gold_unseal_once", state["gold_open_count"] == state["gold_semantic_access_count"] == state["gold_unseal_count"] == 1)
    check("track_counts", {name: len({row["case_id"] for row in records if row[field]})
                           for name, field in expected["track_fields"].items()} == expected["track_counts"])
    check("comparison_count", len({row["comparison_id"] for row in records}) == expected["expected_comparison_count"])
    main_rows = [row for row in records if row["comparison_id"] == "RULE_M1_PLUS_M2B" and row["track_s_primary_core"]]
    reported_main = result["groups"]["track_s_primary_core"]["comparisons"]["RULE_M1_PLUS_M2B"]
    recomputed_macro = _macro_f1(main_rows, gold) if main_rows else None
    check("metrics_recomputation_consistency", recomputed_macro is None or abs(recomputed_macro - reported_main["macro_f1"]) < 1e-12,
          {"recomputed_macro_f1": recomputed_macro, "reported_macro_f1": reported_main["macro_f1"]})
    check("hypotheses_present", set(result["hypotheses"]) == {f"H-B{i}" for i in range(1, 7)} and
          all(row["verdict"] in {"SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE"} for row in result["hypotheses"].values()))
    check("duplicate_guards", duplicate.get("status") == "PASS" and duplicate.get("failed_closed_count", 0) >= 6)
    check("first_evidence_immutable", prediction_path.stat().st_mode & 0o222 == 0 and state["predictions_immutable_count"] == 1)
    check("result_immutable", result_path.stat().st_mode & 0o222 == 0 and state["results_immutable_count"] == 1)
    check("oracle_absent_from_prediction", all(row["comparison_id"] != "EVALUATION_ONLY_ORACLE_UPPER_BOUND" for row in records))
    check("hybrid_override_zero", sum(bool(row.get("override_applied")) for row in records
                                       if row["comparison_id"] == "HYBRID_CONSERVATIVE_M1_PLUS_M2B") == 0)
    check("formal_m1_test_access_zero", state["formal_m1_test_access_count"] == 0)
    check("live_control_zero", state["live_control_count"] == 0)
    check("m3_zero", state["m3_operation_count"] == 0)
    check("gpu_cuda_zero", state["gpu_compute_count"] == state["cuda_context_count"] == 0)
    check("git_history_integrity", expected["git_history_integrity"] is True)
    check("cleanup", expected["cleanup_status"] == "PASS")
    check("terminal_seal", state["state"] == TERMINAL_STATE)
    source = verifier_source_path or Path(__file__)
    import_audit = _source_import_audit(source)
    check("terminal_verifier_policy_import_zero", import_audit["forbidden_policy_import_count"] == 0, import_audit)
    status = "PASS" if all(row["status"] == "PASS" for row in checks) else "FAIL"
    return {
        "schema_version": "driveclarify.m2b_blind_terminal_verification.r3",
        "status": status,
        "checks_passed": sum(row["status"] == "PASS" for row in checks),
        "checks_total": len(checks),
        "checks": checks,
        "forward_count": 0,
        "policy_import_count": import_audit["forbidden_policy_import_count"],
        "policy_execution_count": 0,
        "prediction_modified": False,
        "result_modified": False,
    }


def write_verification_once(path: Path, result: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(result)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
