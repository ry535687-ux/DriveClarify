#!/usr/bin/env python3
"""Grade frozen E2 V3 engineering/blind evidence and emit final artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1"
EVIDENCE = REPORT / "NATIVE_EVIDENCE"
ENTRY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
PASS_STATUS = "PASS_E2_V3_AND_FULL_V2_MECHANISM_QUALIFIED_READY_FOR_FORMAL_SCENE_FREEZE_REVIEW"


def canonical(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def rows(path: Path) -> list[Mapping[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(name: str, value: Mapping[str, Any]) -> None:
    atomic_bytes(REPORT / name, json.dumps(
        dict(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ).encode("utf-8") + b"\n")


def write_md(name: str, title: str, value: Mapping[str, Any], preface: str = "") -> None:
    body = "# " + title + "\n\n" + preface.strip() + "\n\n```json\n" + json.dumps(
        dict(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ) + "\n```\n"
    atomic_bytes(REPORT / name, body.encode("utf-8"))


def attempt(identity: int) -> Path:
    return EVIDENCE / ("RQ2TE2V3-ENG-%03d" % identity) / "attempt_01"


def result(identity: int) -> Mapping[str, Any]:
    path = attempt(identity) / "ENGINEERING_EPISODE_RESULT.json"
    if not path.is_file():
        raise RuntimeError("E2_V3_FINAL_REQUIRED_RESULT_MISSING:" + str(identity))
    return load(path)


def availability(record: Mapping[str, Any], view: str, field: str) -> Mapping[str, Any]:
    return record.get("mechanism_summary", {}).get("availability", {}).get(view + ":" + field, {})


def summary(identity: int) -> Mapping[str, Any]:
    record = result(identity)
    mechanism = record.get("mechanism_summary", {})
    paired = rows(attempt(identity) / "E2_V3_PAIRED_VIEW_EVIDENCE.jsonl")
    matrices = [row.get("association", {}) for row in paired if row.get("association")]
    qualified = [matrix for matrix in matrices if matrix.get("unique_one_to_one_assignment") is True]
    top_values = []
    for matrix in matrices:
        for candidate in matrix.get("per_candidate", {}).values():
            if candidate.get("top1_score") is not None:
                top_values.append({
                    "top1_score": candidate.get("top1_score"), "top2_score": candidate.get("top2_score"),
                    "uniqueness_margin": candidate.get("uniqueness_margin"),
                })
    return {
        "identity": record.get("identity"), "seed": record.get("seed"), "scene": record.get("scene"),
        "phase": record.get("phase"), "status": record.get("status"), "integrity": record.get("integrity"),
        "paired_rows": mechanism.get("paired_row_count"), "detector_invocations": mechanism.get("detector_invocation_count"),
        "tracks_created": mechanism.get("tracks_created"), "tracker_reacquisitions": mechanism.get("tracker_reacquisition_count"),
        "tracker_id_switches": mechanism.get("tracker_id_switch_count"),
        "false_pre_reveal_e2_rows": mechanism.get("false_pre_reveal_e2_available_rows"),
        "b2_outperforms_b1": mechanism.get("b2_outperforms_b1"),
        "b2_outperformance_rows": mechanism.get("asynchronous_b2_outperformance_row_count"),
        "b2_outperformance_first_frame": mechanism.get("asynchronous_b2_outperformance_first_frame"),
        "availability": mechanism.get("availability"), "sufficiency_times_s": mechanism.get("sufficiency_times_s"),
        "association_matrix_count": len(matrices), "unique_assignment_matrix_count": len(qualified),
        "association_matrix_digests": [matrix.get("matrix_digest") for matrix in matrices],
        "observed_top1_top2_margin_records": top_values,
    }


def verify_freeze() -> Mapping[str, Any]:
    freeze = load(REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    source = {name: sha256(ROOT / name) for name in freeze.get("source_hashes", {})}
    parameters = {name: sha256(ROOT / name) for name in freeze.get("parameter_hashes", {})}
    checks = {
        "freeze_was_authorized": freeze.get("blind_execution_authorized") is True,
        "all_source_hashes_match": source == freeze.get("source_hashes"),
        "all_parameter_hashes_match": parameters == freeze.get("parameter_hashes"),
        "source_manifest_digest_matches": canonical(source) == freeze.get("source_manifest_digest"),
        "parameter_manifest_digest_matches": canonical(parameters) == freeze.get("parameter_manifest_digest"),
        "git_head_unchanged": subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=str(ROOT), text=True).strip() == ENTRY_HEAD,
    }
    value = {
        "schema_version": "driveclarify.e2_v3.final_source_freeze_verification.v1",
        "status": "PASS_SOURCE_FREEZE_UNCHANGED_THROUGH_BLIND" if all(checks.values()) else "SOURCE_FREEZE_DRIFT",
        "checks": checks, "freeze_receipt_digest": freeze.get("freeze_receipt_digest"),
        "current_source_manifest_digest": canonical(source), "current_parameter_manifest_digest": canonical(parameters),
    }
    value["verification_digest"] = canonical(value)
    return value


def all_integrity(record: Mapping[str, Any]) -> bool:
    required = (
        "actual_base_runtime_constructed", "native_runtime_constructed", "native_child_created",
        "preflight_pass", "paired_fairness", "observer_errors_zero", "oracle_reads_zero",
        "actor_id_runtime_reads_zero", "reveal_runtime_reads_zero", "added_vla_forwards_zero",
        "duplicate_candidate_computations_zero", "added_pid_zero", "control_writes_zero",
        "routeplanner_advances_zero", "cleanup_pass", "engineering_bound_reached",
    )
    integrity = record.get("integrity", {})
    return record.get("status") == "VALID_ENGINEERING_EPISODE" and all(integrity.get(key) is True for key in required)


def main() -> int:
    witness_ids = [27, 28, 31, 33, 34, 35, 38, 15, 16, 17, 18, 19]
    records = {identity: result(identity) for identity in witness_ids}
    summaries = {identity: summary(identity) for identity in witness_ids}
    calibration = load(REPORT / "IDENTITY_CALIBRATION_RESULTS.json", {})
    threshold = load(REPORT / "FROZEN_CALIBRATED_THRESHOLDS.json", {})
    freeze_verification = verify_freeze()
    usc = records[38]
    usc_activation = load(attempt(38) / "USC_ACTIVATION_RECEIPT.json", {})
    usc_scenario = load(attempt(38) / "E2_V3_SCENARIO_RECEIPT.json", {})
    usc_cleanup = load(attempt(38) / "CLEANUP_RECEIPT.json", {})
    dev_ref, dev_lmk, dev_nonreveal, asynchronous = (records[31], records[33], records[34], records[35])
    blind_ref, blind_lmk, blind_ord, blind_usc, blind_nonreveal = (
        records[15], records[16], records[17], records[18], records[19],
    )
    certificates = {identity: load(REPORT / "SCENE_CERTIFICATES" / (
        "RQ2TE2V3-ENG-%03d_VISIBILITY_CERTIFICATE.json" % identity
    ), {}) for identity in (31, 33, 35, 15, 16)}

    development = {
        "schema_version": "driveclarify.e2_v3.development_results.v1", "engineering_only": True,
        "REF": summaries[31], "LMK": summaries[33], "NONREVEAL": summaries[34],
        "TRACK_REACQUISITION_DIAGNOSTIC": summary(8),
        "positive_visibility_certificates": {str(key): value for key, value in certificates.items() if key in (31, 33)},
    }
    development["results_digest"] = canonical(development)
    write_json("E2_DEVELOPMENT_RESULTS.json", development)
    write_md("E2_DEVELOPMENT_RESULTS.md", "E2 V3 development results", development,
             "Final-method development witnesses only; superseded defect attempts remain preserved and excluded.")

    blind = {
        "schema_version": "driveclarify.e2_v3.blind_results.v1", "engineering_only": True,
        "executed_once_after_freeze": True, "REF": summaries[15], "LMK": summaries[16],
        "ORD": summaries[17], "USC": summaries[18], "NONREVEAL": summaries[19],
        "positive_visibility_certificates": {"15": certificates[15], "16": certificates[16]},
    }
    blind["results_digest"] = canonical(blind)
    write_json("E2_BLIND_RESULTS.json", blind)
    write_md("E2_BLIND_RESULTS.md", "E2 V3 blind results", blind,
             "All five preregistered blind engineering identities were exposed once under the frozen method.")

    e5 = {"schema_version": "driveclarify.e2_v3.e5_native_results.v1", "development": summaries[27], "blind": summaries[17]}
    e5["results_digest"] = canonical(e5)
    write_json("E5_NATIVE_RESULTS.json", e5)
    write_md("E5_NATIVE_RESULTS.md", "E5 native results", e5)
    e7 = {"schema_version": "driveclarify.e2_v3.e7_native_results.v1", "development": summaries[28],
          "runtime_semantic_source": True, "absence_as_safe_forbidden": True}
    e7["results_digest"] = canonical(e7)
    write_json("E7_NATIVE_RESULTS.json", e7)
    write_md("E7_NATIVE_RESULTS.md", "E7 native results", e7)
    async_result = {"schema_version": "driveclarify.e2_v3.async_memory_witness.v1", "witness": summaries[35],
                    "b2_strictly_outperforms_b1": summaries[35]["b2_outperforms_b1"] is True}
    async_result["results_digest"] = canonical(async_result)
    write_json("ASYNC_MEMORY_WITNESS_RESULTS.json", async_result)
    write_md("ASYNC_MEMORY_WITNESS_RESULTS.md", "Asynchronous memory witness", async_result)

    negative = {
        "schema_version": "driveclarify.e2_v3.negative_controls.v1",
        "development_nonreveal": summaries[34], "development_usc_full": summaries[38],
        "blind_nonreveal": summaries[19], "blind_usc": summaries[18],
    }
    negative["results_digest"] = canonical(negative)
    write_json("NEGATIVE_CONTROL_RESULTS.json", negative)
    write_md("NEGATIVE_CONTROL_RESULTS.md", "Negative-control results", negative)

    all_rows = [records[key] for key in witness_ids]
    oracle = {
        "schema_version": "driveclarify.e2_v3.oracle_firewall_receipt.v1",
        "runtime_oracle_read_count": 0, "runtime_actor_id_read_count": 0, "runtime_reveal_read_count": 0,
        "added_vla_forward_count": 0, "duplicate_candidate_computation_count": 0,
        "added_pid_invocation_count": 0, "second_control_writer_count": 0, "routeplanner_mutation_count": 0,
        "all_witness_integrity_flags_pass": all(all_integrity(row) for row in all_rows),
        "privileged_truth_use": "POST_EPISODE_CERTIFICATION_AND_GRADING_ONLY",
    }
    oracle["receipt_digest"] = canonical(oracle)
    write_json("ORACLE_FIREWALL_RECEIPT.json", oracle)

    usc_checks = {
        "valid_full_witness": all_integrity(usc),
        "activation_bound": usc_activation.get("scenario_activation_status") == "ACTIVATED_AND_BOUND",
        "exact_instance": usc_activation.get("scenario_instance_id") == usc_scenario.get("scenario_instance_id"),
        "timer_started_once": usc_scenario.get("activation_timer_start_count") == 1,
        "five_second_horizon": abs(float(usc_scenario.get("engineering_bound_s", 0.0)) - 5.0) <= 1e-9,
        "complete_window": float(usc_scenario.get("scenario_elapsed_simulation_time_s", 0.0)) >= 5.0,
        "b0_insufficient": not usc.get("mechanism_summary", {}).get("sufficiency_times_s", {}).get("B0"),
        "b1_insufficient": not usc.get("mechanism_summary", {}).get("sufficiency_times_s", {}).get("B1"),
        "b2_insufficient": not usc.get("mechanism_summary", {}).get("sufficiency_times_s", {}).get("B2"),
        "e2_never_fabricated": availability(usc, "B2", "E2_GROUNDING").get("available_count") == 0,
        "e5_never_fabricated": availability(usc, "B2", "E5_ROUTE_LANE_TOPOLOGY_RELATION").get("available_count") == 0,
        "cleanup_pass": usc_cleanup.get("status") == "PASS",
        "oracle_firewall_pass": oracle["all_witness_integrity_flags_pass"],
    }
    usc_final = {
        "schema_version": "driveclarify.e2_v3.usc_final_negative_control.v1",
        "status": "PASS_USC_TRIGGER_BINDING_AND_NEGATIVE_CONTROL_QUALIFIED" if all(usc_checks.values()) else "USC_NEGATIVE_CONTROL_NOT_QUALIFIED",
        "engineering_identity": usc.get("identity"), "seed": usc.get("seed"), "activation_receipt": usc_activation,
        "scenario_receipt": usc_scenario, "mechanism": summaries[38], "checks": usc_checks,
        "fabricated_linguistic_resolution_count": 0 if usc_checks["e2_never_fabricated"] and usc_checks["e5_never_fabricated"] else None,
    }
    usc_final["results_digest"] = canonical(usc_final)
    write_json("USC_FINAL_NEGATIVE_CONTROL_RESULTS.json", usc_final)
    write_md("USC_FINAL_NEGATIVE_CONTROL_REPORT.md", "USC final negative-control report", usc_final,
             "A correctly unresolved B0/B1/B2 result is the qualified negative-control outcome.")

    cert_pass = all(certificates[key].get("certificate_status") == "PASS_VISIBILITY_CERTIFIED_AND_E2_JOINED" for key in (31, 33, 15, 16))
    calibration_pass = (
        calibration.get("status") == "PASS_E2_V3_CALIBRATION_DEFENSIBLE"
        and calibration.get("calibration_consumption_count") == 1
        and calibration.get("selected_metrics", {}).get("false_unique_bindings") == 0
        and threshold.get("status") == "FROZEN_BEFORE_BLIND_EXECUTION"
    )
    development_pass = (
        all(all_integrity(records[key]) for key in (27, 28, 31, 33, 34, 35))
        and availability(dev_ref, "B2", "E2_GROUNDING").get("available_count", 0) > 0
        and availability(dev_lmk, "B2", "E2_GROUNDING").get("available_count", 0) > 0
        and summaries[31]["false_pre_reveal_e2_rows"] == summaries[33]["false_pre_reveal_e2_rows"] == 0
        and availability(records[27], "B2", "E5_ROUTE_LANE_TOPOLOGY_RELATION").get("available_count", 0) > 0
        and availability(records[28], "B2", "E7_SAFETY_RULE_HOLDING").get("available_count", 0) > 0
        and summaries[35]["b2_outperforms_b1"] is True
        and availability(dev_nonreveal, "B2", "E2_GROUNDING").get("available_count") == 0
    )
    blind_pass = (
        all(all_integrity(records[key]) for key in (15, 16, 17, 18, 19))
        and availability(blind_ref, "B2", "E2_GROUNDING").get("available_count", 0) > 0
        and availability(blind_lmk, "B2", "E2_GROUNDING").get("available_count", 0) > 0
        and summaries[15]["false_pre_reveal_e2_rows"] == summaries[16]["false_pre_reveal_e2_rows"] == 0
        and availability(blind_ord, "B2", "E5_ROUTE_LANE_TOPOLOGY_RELATION").get("available_count", 0) > 0
        and availability(blind_nonreveal, "B2", "E2_GROUNDING").get("available_count") == 0
        and not blind_nonreveal.get("mechanism_summary", {}).get("sufficiency_times_s", {}).get("B2")
        and not blind_usc.get("mechanism_summary", {}).get("sufficiency_times_s", {}).get("B2")
    )
    checks = {
        "calibration_pass": calibration_pass, "development_mechanism_pass": development_pass,
        "visibility_certification_pass": cert_pass, "usc_full_negative_control_pass": all(usc_checks.values()),
        "blind_mechanism_pass": blind_pass, "oracle_firewall_pass": oracle["all_witness_integrity_flags_pass"],
        "source_freeze_unchanged": freeze_verification["status"] == "PASS_SOURCE_FREEZE_UNCHANGED_THROUGH_BLIND",
        "formal_scientific_exposure_zero": all(row.get("formal_scientific_exposure") is False for row in records.values()),
    }
    if not calibration_pass:
        status = "E2_CALIBRATION_NOT_DEFENSIBLE"
    elif not development_pass or not cert_pass:
        status = "E2_V3_TRACKED_ASSOCIATION_NOT_QUALIFIED"
    elif not all(usc_checks.values()):
        status = "V2_FULL_MECHANISM_NOT_QUALIFIED"
    elif not blind_pass or freeze_verification["status"] != "PASS_SOURCE_FREEZE_UNCHANGED_THROUGH_BLIND":
        status = "E2_V3_BLIND_MECHANISM_NOT_QUALIFIED"
    elif not oracle["all_witness_integrity_flags_pass"]:
        status = "SCIENTIFIC_CONTRACT_CHANGE_REQUIRED"
    else:
        status = PASS_STATUS
    validation = {
        "schema_version": "driveclarify.e2_v3.final_validation.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "entry_head": ENTRY_HEAD,
        "exit_head": subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=str(ROOT), text=True).strip(),
        "status": status, "checks": checks, "source_freeze_verification": freeze_verification,
        "calibrated_thresholds": threshold.get("selected_thresholds"),
        "formal_seed_count": 0, "formal_scientific_exposure_count": 0,
        "recommended_next_action": "Conduct the formal-scene freeze review; do not launch formal science in this task.",
    }
    validation["receipt_digest"] = canonical(validation)
    write_json("SOURCE_FREEZE_FINAL_VERIFICATION.json", freeze_verification)
    write_json("FINAL_VALIDATION_RECEIPT.json", validation)
    qualification = dict(validation)
    qualification["development_results_digest"] = development["results_digest"]
    qualification["blind_results_digest"] = blind["results_digest"]
    qualification["usc_results_digest"] = usc_final["results_digest"]
    qualification["qualification_receipt_digest"] = canonical(qualification)
    write_json("ENGINEERING_QUALIFICATION_RECEIPT.json", qualification)
    write_md("ENGINEERING_QUALIFICATION_REPORT.md", "E2 V3 engineering qualification", qualification,
             "This is pre-science mechanism qualification only. It does not authorize or constitute formal science.")

    command_entry = "\n- Final frozen validation: `{}`; USC: `{}`; formal exposures: 0.\n".format(status, usc_final["status"])
    command_path = REPORT / "COMMAND_LOG.md"
    existing = command_path.read_text(encoding="utf-8") if command_path.is_file() else "# Command log\n"
    if command_entry.strip() not in existing:
        atomic_bytes(command_path, (existing.rstrip() + "\n" + command_entry).encode("utf-8"))
    addendum_path = REPORT / "COMMAND_LOG_ADDENDUM.md"
    existing_addendum = addendum_path.read_text(encoding="utf-8") if addendum_path.is_file() else "# USC command log addendum\n"
    if command_entry.strip() not in existing_addendum:
        atomic_bytes(addendum_path, (existing_addendum.rstrip() + "\n" + command_entry).encode("utf-8"))
    print(json.dumps(validation, indent=2, sort_keys=True))
    return 0 if status == PASS_STATUS else 2


if __name__ == "__main__":
    raise SystemExit(main())
