#!/usr/bin/env python3
"""Finalize the bounded RQ2-T V2 pre-science mechanism qualification.

This is an offline report builder.  It reads only already-persisted engineering
evidence and integrity sources; it cannot launch CARLA or materialize seeds.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq2_t_v2_pre_science_mechanism_qualification_v1"
V1_ROOT = ROOT / "reports/driveclarify_rq2_t_formal_experiment_2a_v2"
REDESIGN_ROOT = ROOT / "reports/driveclarify_rq2_t_v2_evidence_enabled_method_redesign_v1"
FINAL_STATUS = "V2_METHOD_MECHANISM_NOT_QUALIFIED"
ENTRY_HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
FINAL_VALID = ("RQ2TV2-ENG-022", "RQ2TV2-ENG-023")
FAILED = (
    "RQ2TV2-ENG-001",
    "RQ2TV2-ENG-019",
    "RQ2TV2-ENG-020",
    "RQ2TV2-ENG-021",
    "RQ2TV2-ENG-002",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[Mapping[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(name: str, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    atomic_bytes(REPORT / name, payload)


def write_text(name: str, value: str) -> None:
    atomic_bytes(REPORT / name, value.rstrip().encode("utf-8") + b"\n")


def git_bytes(*args: str, cwd: Path = ROOT) -> bytes:
    return subprocess.check_output(("git",) + args, cwd=str(cwd))


def git_text(*args: str, cwd: Path = ROOT) -> str:
    return git_bytes(*args, cwd=cwd).decode("utf-8").strip()


def tree_digest(path: Path) -> Mapping[str, Any]:
    digest = hashlib.sha256()
    count = 0
    total = 0
    for item in sorted(value for value in path.rglob("*") if value.is_file()):
        file_hash = sha256(item)
        count += 1
        total += item.stat().st_size
        digest.update(file_hash.encode("ascii"))
        digest.update(b"  ")
        digest.update(item.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\n")
    return {"file_count": count, "bytes": total, "path_content_sha256": digest.hexdigest()}


def relative_hash(path: str) -> str:
    return sha256(ROOT / path)


def main() -> None:
    entry = load(REPORT / "ENTRY_INTEGRITY_RECEIPT.json")
    parameter_freeze = load(REPORT / "V2_PARAMETER_PROVENANCE_AND_FREEZE.json")
    registry = load(REPORT / "ENGINEERING_IDENTITY_REGISTRY.json")
    phase_a = load(REPORT / "PHASE_A_ENGINEERING_SCENE_MANIFEST.json")
    phase_b = load(REPORT / "PHASE_B_BLIND_LAYOUT_SEAL.json")
    attempts = jsonl(REPORT / "ENGINEERING_ATTEMPT_LEDGER.jsonl")
    executions = jsonl(REPORT / "ENGINEERING_EXECUTION_LEDGER.jsonl")
    adjudications = jsonl(REPORT / "ENGINEERING_ADJUDICATION_LEDGER.jsonl")
    exposed = [str(row["identity"]) for row in attempts]
    registered = [str(row["identity"]) for row in registry["identities"]]
    unexposed = [value for value in registered if value not in exposed]
    assert len(registered) == 24
    assert len(set(exposed)) == len(exposed) == 7
    assert set(FINAL_VALID).issubset(exposed)
    assert set(FAILED).issubset(exposed)

    per_episode: dict[str, Any] = {}
    paired_rows: list[Mapping[str, Any]] = []
    e2_reasons: Counter[str] = Counter()
    for identity in FINAL_VALID:
        root = REPORT / "PAIRED_VIEW_EVIDENCE" / identity / "attempt_01"
        result = load(root / "ENGINEERING_EPISODE_RESULT.json")
        scenario = load(root / "RQ2_T_V2_SCENARIO_RECEIPT.json")
        cleanup = load(root / "CLEANUP_RECEIPT.json")
        rows = jsonl(root / "RQ2_T_V2_PAIRED_VIEW_EVIDENCE.jsonl")
        assert result["status"] == "VALID_ENGINEERING_EPISODE"
        assert cleanup["status"] == "PASS"
        assert scenario["engineering_bound_reached"] is True
        assert len(rows) == 200
        reveal_frame = int(scenario["reveal_frame"])
        pre = [row for row in rows if int(row["source_identity"]["source_frame_id"]) < reveal_frame]
        post = [row for row in rows if int(row["source_identity"]["source_frame_id"]) >= reveal_frame]
        identity_match = all(
            int(row["views"][view]["source_frame_id"])
            == int(row["source_identity"]["source_frame_id"])
            and str(row["views"][view]["source_observation_id"])
            == str(row["source_identity"]["source_observation_id"])
            and float(row["views"][view]["simulation_time_s"])
            == float(row["source_identity"]["simulation_time_s"])
            for row in rows for view in ("B0", "B1", "B2")
        )
        counter_fairness = all(
            row["fairness_counters_before"] == row["fairness_counters_after"]
            for row in rows
        )
        for row in rows:
            e2_reasons.update(
                row["views"]["B1"]["evidence_vector"]["E2_GROUNDING"].get(
                    "reason_codes", ()
                )
            )
        availability = {}
        sufficiency = {}
        for view in ("B0", "B1", "B2"):
            availability[view] = {
                field: sum(
                    row["views"][view]["evidence_vector"][field]["status"] == "AVAILABLE"
                    for row in rows
                )
                for field in (
                    "E2_GROUNDING",
                    "E5_ROUTE_LANE_TOPOLOGY_RELATION",
                    "E7_SAFETY_RULE_HOLDING",
                )
            }
            sufficiency[view] = sum(
                row["views"][view]["EpistemicEvidenceSufficient"] is True
                for row in rows
            )
        per_episode[identity] = {
            "seed": result["seed"],
            "scene": result["scene"],
            "paired_rows": len(rows),
            "pre_reveal_rows": len(pre),
            "post_reveal_rows": len(post),
            "reveal_frame": reveal_frame,
            "reveal_executed": scenario["reveal_executed"],
            "availability": availability,
            "sufficiency_true_counts": sufficiency,
            "source_identity_match": identity_match,
            "counter_fairness": counter_fairness,
            "cleanup_status": cleanup["status"],
            "runtime_status": result["runtime"]["status"],
            "observer_added_vla_forwards": result["native_runtime_receipt"]["observer_added_vla_forwards"],
            "observer_added_candidate_computations": result["native_runtime_receipt"]["observer_added_candidate_computations"],
            "observer_added_pid_instances": result["native_runtime_receipt"]["observer_added_pid_instances"],
            "observer_control_writes": result["native_runtime_receipt"]["observer_control_writes"],
            "observer_route_planner_advances": result["native_runtime_receipt"]["observer_route_planner_advances"],
            "oracle_input_reads": result["native_runtime_receipt"]["oracle_input_reads"],
        }
        paired_rows.extend(rows)

    all_identity_match = all(value["source_identity_match"] for value in per_episode.values())
    all_counter_fairness = all(value["counter_fairness"] for value in per_episode.values())
    total_rows = len(paired_rows)
    assert total_rows == 400 and all_identity_match and all_counter_fairness
    aggregate = {
        view: {
            field: sum(
                row["views"][view]["evidence_vector"][field]["status"] == "AVAILABLE"
                for row in paired_rows
            )
            for field in (
                "E2_GROUNDING",
                "E5_ROUTE_LANE_TOPOLOGY_RELATION",
                "E7_SAFETY_RULE_HOLDING",
            )
        }
        for view in ("B0", "B1", "B2")
    }
    aggregate_sufficiency = {
        view: sum(row["views"][view]["EpistemicEvidenceSufficient"] is True for row in paired_rows)
        for view in ("B0", "B1", "B2")
    }

    head = git_text("rev-parse", "HEAD")
    branch = git_text("branch", "--show-current")
    tracked_sha = hashlib.sha256(git_bytes("diff", "--binary")).hexdigest()
    staged_sha = hashlib.sha256(git_bytes("diff", "--cached", "--binary")).hexdigest()
    simlingo = Path("/home/buaa/wrh/simlingo")
    simlingo_head = git_text("rev-parse", "HEAD", cwd=simlingo)
    simlingo_diff = hashlib.sha256(git_bytes("diff", "--binary", cwd=simlingo)).hexdigest()
    v1_tree = tree_digest(V1_ROOT)
    redesign_tree = tree_digest(REDESIGN_ROOT)
    v1_csv = relative_hash("reports/driveclarify_rq2_t_formal_experiment_2a_v2/EPISODE_LEVEL_PRIMARY_TABLE.csv")
    v1_json = relative_hash("reports/driveclarify_rq2_t_formal_experiment_2a_v2/EPISODE_LEVEL_PRIMARY_TABLE.json")
    v1_measurement = relative_hash("driveclarify_rq2_t/measurement.py")
    checkpoint = sha256(Path("/home/buaa/wrh/simlingo/outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"))
    integrity_checks = {
        "head_unchanged": head == ENTRY_HEAD == entry["entry_head"],
        "branch_unchanged": branch == "master",
        "tracked_diff_empty": tracked_sha == EMPTY_SHA,
        "staged_diff_empty": staged_sha == EMPTY_SHA,
        "v1_tree_exact": v1_tree["path_content_sha256"] == entry["v1_final_evidence_tree"]["path_content_sha256"] and v1_tree["file_count"] == 2928,
        "v1_primary_csv_exact": v1_csv == entry["v1_primary_table_sha256"]["csv"],
        "v1_primary_json_exact": v1_json == entry["v1_primary_table_sha256"]["json"],
        "v1_sufficiency_source_exact": v1_measurement == parameter_freeze["unchanged_v1_sufficiency_source_sha256"],
        "redesign_tree_exact": redesign_tree["path_content_sha256"] == entry["v2_redesign_tree"]["path_content_sha256"],
        "simlingo_head_exact": simlingo_head == entry["simlingo"]["head"],
        "simlingo_diff_exact": simlingo_diff == entry["simlingo"]["tracked_diff_sha256"],
        "checkpoint_exact": checkpoint == entry["checkpoint_sha256"],
    }
    assert all(integrity_checks.values())

    source_hashes = {
        "driveclarify_grounded_language_v1/runtime.py": relative_hash("driveclarify_grounded_language_v1/runtime.py"),
        "driveclarify_m3_runtime_shadow/live_shadow_runtime.py": relative_hash("driveclarify_m3_runtime_shadow/live_shadow_runtime.py"),
        "driveclarify_persistent_ambiguity_runtime_v1/runtime.py": relative_hash("driveclarify_persistent_ambiguity_runtime_v1/runtime.py"),
        "driveclarify_rq2_t/measurement.py": v1_measurement,
        "driveclarify_rq2_t/observer.py": relative_hash("driveclarify_rq2_t/observer.py"),
        "driveclarify_rq2_t_v2/providers.py": relative_hash("driveclarify_rq2_t_v2/providers.py"),
        "driveclarify_rq2_t_v2/memory.py": relative_hash("driveclarify_rq2_t_v2/memory.py"),
        "driveclarify_rq2_t_v2/method.py": relative_hash("driveclarify_rq2_t_v2/method.py"),
        "driveclarify_rq2_t_v2/native_runtime.py": relative_hash("driveclarify_rq2_t_v2/native_runtime.py"),
        "driveclarify_rq2_t_v2/native_scenario.py": relative_hash("driveclarify_rq2_t_v2/native_scenario.py"),
        "driveclarify_rq2_t_v2/scene_bindings.py": relative_hash("driveclarify_rq2_t_v2/scene_bindings.py"),
        "tools/run_rq2_t_v2_pre_science.py": relative_hash("tools/run_rq2_t_v2_pre_science.py"),
        "tests/rq2_t_v2/test_pre_science_native_contract.py": relative_hash("tests/rq2_t_v2/test_pre_science_native_contract.py"),
    }

    native_binding = {
        "schema_version": "driveclarify.rq2_t_v2.native_binding_receipt.v1",
        "status": "PASS_REAL_NATIVE_DEFAULT_OFF_INTEGRATION",
        "feature_flag": "DRIVECLARIFY_RQ2_T_V2_NATIVE_EVIDENCE",
        "feature_flag_default": "OFF",
        "construction_path": [
            "driveclarify_m3_runtime_shadow.live_shadow_runtime.build_live_shadow_runtime",
            "driveclarify_grounded_language_v1.runtime.build_grounded_language_v1_runtime",
            "driveclarify_rq2_t_v2.native_runtime.build_rq2_t_v2_native_runtime",
            "driveclarify_persistent_ambiguity_runtime_v1.runtime.build_persistent_ambiguity_runtime",
            "driveclarify_rq2_t_v2.native_runtime.RQ2TV2NativeEvidenceRuntime",
        ],
        "flag_off_behavior": "the new conditional is skipped and the pre-existing persistent/static runtime selection and production control behavior execute unchanged",
        "actual_native_witnesses": list(FINAL_VALID),
        "actual_base_runtime_type": "driveclarify_persistent_ambiguity_runtime_v1.runtime.PersistentAmbiguityReferentialRuntime",
        "providers": {
            "E2": "V2_RUNTIME_CAMERA_TRACK_GROUNDING_PROVIDER",
            "E5": "V2_LIVE_CARLA_LOCAL_TOPOLOGY_PROVIDER",
            "E7": "V2_CURRENT_PHYSICAL_SAFETY_AND_EXISTING_HOLDING_PROVIDER",
        },
        "paired_rows": total_rows,
        "same_source_identity_all_rows": all_identity_match,
        "same_trajectory_and_counter_fairness_all_rows": all_counter_fairness,
        "close_time_legacy_receipt_publication": True,
        "source_hashes": source_hashes,
    }
    native_binding["receipt_digest"] = canonical_sha256(native_binding)
    write_json("V2_NATIVE_RUNTIME_BINDING_RECEIPT.json", native_binding)
    write_text(
        "V2_NATIVE_RUNTIME_BINDING.md",
        """# V2 native runtime binding

The default-off flag `DRIVECLARIFY_RQ2_T_V2_NATIVE_EVIDENCE` enters the real live-shadow → grounded factory → persistent runtime construction path. The V2 wrapper delegates every policy/control call to the existing runtime and consumes only already-produced history rows. With the flag absent or false, the conditional is skipped and the former runtime selection is unchanged.

Native identities `RQ2TV2-ENG-022/023` each produced 200 B0/B1/B2 rows from identical frame, observation, simulator-time, trajectory, and control sources. Observer-added VLA forwards, candidate computations, PID instances, control writes, and RoutePlanner advances were all zero. Full details and hashes are in the sibling receipt.
""",
    )

    provider_results = {
        "schema_version": "driveclarify.rq2_t_v2.provider_correctness_results.v1",
        "status": "FAIL_REQUIRED_REFERENTIAL_E2_TRANSITION_ABSENT",
        "final_valid_native_identities": list(FINAL_VALID),
        "per_episode": per_episode,
        "aggregate_final_valid_rows": total_rows,
        "aggregate_availability": aggregate,
        "E2": {
            "required": "UNKNOWN before reveal then AVAILABLE within one CARLA tick",
            "observed_available_rows_B1": aggregate["B1"]["E2_GROUNDING"],
            "observed_available_rows_B2": aggregate["B2"]["E2_GROUNDING"],
            "reason_distribution": dict(e2_reasons),
            "positive_detection_latency_CARLA_ticks": None,
            "result": "FAIL_NO_TRANSITION_IN_TWO_FRESH_VALID_REF_EPISODES",
        },
        "E5": {"result": "NOT_EXECUTED_PHASE_A_EARLY_STOP", "scientific_claim": False},
        "E7": {
            "available_rows_B1": aggregate["B1"]["E7_SAFETY_RULE_HOLDING"],
            "available_rows_B2": aggregate["B2"]["E7_SAFETY_RULE_HOLDING"],
            "current_dependency_path_result": "PASS_IN_REF_WITNESSES",
            "native_invalidation_witness": "NOT_EXECUTED_PHASE_A_EARLY_STOP",
        },
        "false_pre_reveal_E2_or_E5_availability_rows": 0,
        "parameters_changed_to_obtain_success": False,
    }
    provider_results["result_digest"] = canonical_sha256(provider_results)
    write_json("PROVIDER_CORRECTNESS_RESULTS.json", provider_results)

    async_results = {
        "schema_version": "driveclarify.rq2_t_v2.async_memory_witness_results.v1",
        "status": "NOT_EXECUTED_PHASE_A_HARD_GO_ALREADY_FAILED",
        "component_contract_test": "PASS_B1_FALSE_B2_TRUE_WITH_FROZEN_MEMORY_SEMANTICS",
        "native_B1_result": None,
        "native_B2_result": None,
        "native_B2_outperforms_B1": False,
        "qualification_credit": False,
        "reason": "two fresh valid REF witnesses already failed the mandatory E2 transition, so Phase A could not seal PASS and further identity exposure stopped",
    }
    async_results["result_digest"] = canonical_sha256(async_results)
    write_json("ASYNC_MEMORY_WITNESS_RESULTS.json", async_results)
    write_text(
        "ASYNC_MEMORY_WITNESS_REPORT.md",
        """# Async memory witness

The focused deterministic contract test proves that the frozen implementation can represent B1=false/B2=true when evidence is supplied asynchronously. Native qualification credit is not awarded: the native async template was not exposed after two fresh valid REF witnesses had already failed the mandatory E2 transition. Native B1 and B2 results are therefore null, and B2>B1 is not demonstrated.
""",
    )

    end_to_end = {
        "schema_version": "driveclarify.rq2_t_v2.end_to_end_sufficiency_results.v1",
        "status": "FAIL_REFERENTIAL_AND_NOT_EXECUTED_OTHER_FAMILIES",
        "REFERENTIAL": {
            "identities": list(FINAL_VALID),
            "B0_sufficiency_events": aggregate_sufficiency["B0"],
            "B1_sufficiency_events": aggregate_sufficiency["B1"],
            "B2_sufficiency_events": aggregate_sufficiency["B2"],
            "truthful_precommitment_B2_witness": False,
        },
        "LANDMARK": {"status": "NOT_EXECUTED_PHASE_A_EARLY_STOP"},
        "ORDER": {"status": "NOT_EXECUTED_PHASE_A_EARLY_STOP"},
        "false_sufficiency_events_across_final_valid_rows": 0,
        "unchanged_v1_sufficiency_source_sha256": v1_measurement,
    }
    end_to_end["result_digest"] = canonical_sha256(end_to_end)
    write_json("END_TO_END_SUFFICIENCY_WITNESS_RESULTS.json", end_to_end)
    write_text(
        "END_TO_END_SUFFICIENCY_WITNESS_REPORT.md",
        """# End-to-end sufficiency witness

Across 400 paired rows from two fresh valid REFERENTIAL episodes, B0/B1/B2 each produced zero `EpistemicEvidenceSufficient=true` events. The V1 authority source stayed byte-exact. LANDMARK and ORDER were not exposed after the mandatory REF E2 transition failed twice; they receive no qualification credit.
""",
    )

    negative_results = {
        "schema_version": "driveclarify.rq2_t_v2.negative_control_results.v1",
        "status": "NOT_EXECUTED_PHASE_A_HARD_GO_ALREADY_FAILED",
        "native_nonreveal_result": None,
        "native_USC_result": None,
        "component_nonreveal_test": "PASS",
        "component_USC_test": "PASS",
        "false_sufficiency_events_across_executed_final_valid_rows": 0,
        "qualification_credit": False,
    }
    negative_results["result_digest"] = canonical_sha256(negative_results)
    write_json("NEGATIVE_CONTROL_RESULTS.json", negative_results)
    write_text(
        "NEGATIVE_CONTROL_REPORT.md",
        """# Negative controls

Focused contracts pass for non-reveal and intrinsic USC behavior, but their native templates were not exposed after the decisive Phase A REF failure. They receive no native qualification credit. No false sufficiency occurred in the 400 final-valid executed rows.
""",
    )

    blind_results = {
        "schema_version": "driveclarify.rq2_t_v2.blind_smoke_results.v1",
        "status": "GATE_CLOSED_UNEXPOSED",
        "seal_created_before_phase_a_outcome": phase_b["sealed_before_any_phase_a_native_outcome"],
        "phase_a_pass_required": True,
        "phase_a_pass_obtained": False,
        "phase_b_execution_authorized": False,
        "blind_exposure_count": 0,
        "results": {layout["layout"]: "NOT_EXECUTED" for layout in phase_b["layouts"]},
        "post_exposure_source_or_parameter_changes": 0,
    }
    blind_results["result_digest"] = canonical_sha256(blind_results)
    write_json("BLIND_ENGINEERING_SMOKE_RESULTS.json", blind_results)
    write_text(
        "BLIND_ENGINEERING_SMOKE_REPORT.md",
        """# Blind engineering smoke

The four layouts were sealed before any Phase A outcome and remain unexposed. Phase A did not qualify, so the driver gate correctly refused authorization. REF, LMK, ORD, and USC blind results are all `NOT_EXECUTED`; this is a gate-preserving NO-GO, not missing data imputed as success or failure.
""",
    )

    oracle = {
        "schema_version": "driveclarify.rq2_t_v2.oracle_firewall_receipt.v1",
        "status": "PASS_ZERO_ORACLE_LEAKAGE",
        "runtime_modules_import_scene_binding_or_reveal_owner": False,
        "forbidden_environment_keys_removed": True,
        "actual_native_rows": total_rows,
        "oracle_input_reads": 0,
        "gold_policy_label_reads": 0,
        "expected_mechanism_outcome_reads": 0,
        "focused_firewall_tests": "PASS_BOTH_PYTHON_ENVIRONMENTS",
    }
    oracle["receipt_digest"] = canonical_sha256(oracle)
    write_json("ORACLE_FIREWALL_RECEIPT.json", oracle)

    criteria = {
        "1_real_native_E2_E5_E7_path_constructed": "PASS",
        "2_paired_same_native_episode": "PASS",
        "3_no_extra_forward_control_planner": "PASS",
        "4_E2_REF_and_LMK_transition": "FAIL_REF_TRANSITION_0_OF_2;_LMK_NOT_EXECUTED",
        "5_E5_ORDER_transition": "NOT_EXECUTED",
        "6_E7_explicit_dependencies": "PASS_CURRENT_REF_PATH;INVALIDATION_NOT_EXECUTED",
        "7_false_pre_reveal_zero": "PASS",
        "8_positive_latency_bound": "FAIL_NO_POSITIVE_DETECTION",
        "9_retention_and_invalidation": "PASS_COMPONENT;NATIVE_WITNESS_NOT_COMPLETE",
        "10_async_B1_false_B2_true": "NOT_EXECUTED",
        "11_REF_LMK_ORD_end_to_end": "FAIL_REF;LMK_ORD_NOT_EXECUTED",
        "12_nonreveal": "NOT_EXECUTED",
        "13_USC": "NOT_EXECUTED",
        "14_false_sufficiency_zero": "PASS_EXECUTED_FINAL_VALID_ROWS",
        "15_oracle_zero": "PASS",
        "16_added_VLA_zero": "PASS",
        "17_duplicate_candidate_zero": "PASS",
        "18_PID_controller_unchanged": "PASS",
        "19_second_writer_zero": "PASS",
        "20_RoutePlanner_mutation_zero": "PASS",
        "21_cleanup_publication": "PASS_2_OF_2_FINAL_VALID",
        "22_blind_REF_LMK_ORD": "NOT_EXECUTED_GATE_CLOSED",
        "23_blind_USC": "NOT_EXECUTED_GATE_CLOSED",
    }
    qualification = {
        "schema_version": "driveclarify.rq2_t_v2.engineering_qualification_receipt.v1",
        "status": FINAL_STATUS,
        "phase_a_status": "FAIL_PHASE_A_REQUIRED_REF_E2_TRANSITION_ABSENT",
        "phase_a_target_valid_episode_count": 14,
        "phase_a_final_valid_episode_count": 2,
        "phase_a_early_stop": True,
        "phase_a_early_stop_basis": "two fresh valid post-repair REF identities both failed mandatory E2 transition; GO became impossible without changing frozen method semantics",
        "phase_b_status": "NOT_EXECUTED_GATE_CLOSED",
        "phase_b_execution_authorized": False,
        "phase_b_exposure_count": 0,
        "registered_engineering_identity_count": len(registered),
        "exposed_engineering_identity_count": len(exposed),
        "unexposed_engineering_identity_count": len(unexposed),
        "final_valid_identities": list(FINAL_VALID),
        "preserved_failed_identities": list(FAILED),
        "unexposed_identities": unexposed,
        "formal_seed_count": 0,
        "formal_scientific_exposure_count": 0,
        "parameters_changed_during_phase_a": False,
        "scientific_method_changed_during_phase_a": False,
        "criteria": criteria,
    }
    qualification["receipt_digest"] = canonical_sha256(qualification)
    write_json("ENGINEERING_QUALIFICATION_RECEIPT.json", qualification)
    write_text(
        "ENGINEERING_QUALIFICATION_REPORT.md",
        f"""# Engineering qualification

Final status: `{FINAL_STATUS}`.

The actual default-off native construction path and passive paired evaluator are closed. After lifecycle, serialization, and roadway-geometry defects were preserved and repaired with fresh identities, `RQ2TV2-ENG-022` and `RQ2TV2-ENG-023` each completed the sealed 10-second REF witness with 200 paired rows and clean teardown. In both episodes, E7 was available in B1/B2 on every row, but E2 remained UNKNOWN on every row before and after reveal. B0/B1/B2 sufficiency stayed false.

This is an unfavorable valid mechanism outcome, not an engineering defect: lowering the 0.80 threshold, rescaling raw confidence, or weakening V1 sufficiency is forbidden. Phase A therefore cannot seal PASS. Remaining Phase A and every blind identity stayed unexposed, and no formal seed or scientific exposure was created.
""",
    )

    source_freeze = {
        "schema_version": "driveclarify.rq2_t_v2.pre_science_source_freeze_receipt.v1",
        "status": "PHASE_B_SOURCE_FREEZE_NOT_AUTHORIZED_PHASE_A_FAILED",
        "entry_head": ENTRY_HEAD,
        "exit_head": head,
        "branch": branch,
        "tracked_diff_sha256": tracked_sha,
        "staged_diff_sha256": staged_sha,
        "phase_b_execution_authorized": False,
        "phase_b_exposure_count": 0,
        "parameter_freeze_preceded_all_native_outcomes": parameter_freeze["freeze_precedes_phase_a_native_outcome"],
        "parameters_changed_during_phase_a": False,
        "source_or_parameter_change_after_blind_exposure": 0,
        "v1_final_tree": v1_tree,
        "v2_redesign_tree": redesign_tree,
        "v1_primary_table_sha256": {"csv": v1_csv, "json": v1_json},
        "v1_sufficiency_source_sha256": v1_measurement,
        "source_hashes": source_hashes,
        "simlingo": {"head": simlingo_head, "tracked_diff_sha256": simlingo_diff},
        "checkpoint_sha256": checkpoint,
        "integrity_checks": integrity_checks,
    }
    source_freeze["receipt_digest"] = canonical_sha256(source_freeze)
    write_json("SOURCE_FREEZE_RECEIPT.json", source_freeze)

    exclusion = load(REPORT / "ENGINEERING_SEED_EXCLUSION_REGISTRY.json")
    exclusion.update(
        {
            "exposed_identity_count": len(exposed),
            "unexposed_identity_count": len(unexposed),
            "final_valid_identity_count": len(FINAL_VALID),
            "preserved_failed_identity_count": len(FAILED),
            "phase_a_repair_scene_hashes_also_excluded": True,
            "formal_seed_count": 0,
            "formal_scientific_exposure_count": 0,
        }
    )
    write_json("ENGINEERING_SEED_EXCLUSION_REGISTRY.json", exclusion)

    phase_a_seeds = {
        row["scene"]: (row["identity"], row["seed"])
        for row in registry["identities"] if row["phase"] == "A"
    }
    phase_a_seed_text = "; ".join(
        f"{row['template']}={','.join(row['identities'])}"
        for row in phase_a["templates"]
    )
    blind_seed_text = "; ".join(
        f"{row['layout']}={row['identity']}/{row['seed']}" for row in phase_b["layouts"]
    )
    primary_parameters = (
        "E2=0.80; TTLs E1/E2/E3/E4/E5/E6/E7/E8/E9="
        "0.50/2.00/0/2.00/10.00/2.00/0.10/0.25/60.00 s; E5 horizon=100.0 m"
    )
    returns = [
        f"Entry HEAD: `{ENTRY_HEAD}`.",
        f"Exit HEAD: `{head}`; no commit was created.",
        "V1 result preserved: yes, `RQ2_T_TIMING_HYPOTHESIS_NOT_SUPPORTED` remains authoritative.",
        f"V1 table/source hashes preserved: CSV `{v1_csv}`, JSON `{v1_json}`, sufficiency `{v1_measurement}`; all exact.",
        "Formal V2 seeds generated: 0.",
        "Formal scientific exposures: 0.",
        "V2 runtime integration path: live-shadow → grounded factory → default-off V2 native wrapper → actual persistent runtime.",
        "Production behavior with V2 flag OFF: pre-existing runtime selection, policy, planner, PID, and control behavior are unchanged.",
        "E2 runtime owner: `V2_RUNTIME_CAMERA_TRACK_GROUNDING_PROVIDER`.",
        "E5 runtime owner: `V2_LIVE_CARLA_LOCAL_TOPOLOGY_PROVIDER`.",
        "E7 runtime owner: `V2_CURRENT_PHYSICAL_SAFETY_AND_EXISTING_HOLDING_PROVIDER`.",
        "Exact E5 horizon: current ego-projected prefix of the existing active-route deque, cumulative polyline distance ≤100.0 m, live CARLA HD-map junction entries only; no planner advance or unrestricted future answer.",
        f"Primary parameters: {primary_parameters}.",
        "Parameter provenance: authoritative-existing, previously-prospective, or V2-method-primary exactly as frozen in `V2_PARAMETER_PROVENANCE_AND_FREEZE.json`.",
        "Parameter changes during Phase A: none; only lifecycle, serialization, and actor-roadway geometry engineering defects were repaired.",
        "Freeze before Phase B: parameters were frozen before the first Phase A outcome; Phase B never opened, so no blind exposure preceded or followed a mutation.",
        f"Phase-A templates/seeds: {phase_a_seed_text}; REF replacements `022/3223334113` and `023/1363468664`; all other planned Phase-A identities stayed unexposed after early stop.",
        f"Phase-B sealed layouts/seeds: {blind_seed_text}; all remain unexposed.",
        f"Engineering identities: 24 registered, {len(exposed)} exposed, 2 final-valid, 5 preserved failed, {len(unexposed)} unexposed.",
        "Preserved failed identities: `001` initial lifecycle defect; `019` premature pose verification; `020` serialization containment; `021` pre-repair route obstruction; `002` operator-observed route obstruction/interrupted cleanup.",
        "Permanent exclusion proof: all 24 registered seeds, all original/repaired engineering scene hashes, and every exposed identity are excluded from calibration, V2-A/V2-B DEV/TEST, and paper denominators.",
        "B0 semantics: unchanged V1 passive adapter, built before V2 signals, no V2 reads and no memory.",
        "B1 semantics: same current native frame with E2/E5/E7 providers and no retention.",
        "B2 semantics: identical B1 provider inputs plus only the frozen field-specific retention/freshness/binding/invalidation layer.",
        "Paired source identity proof: 400/400 final-valid rows match frame, observation ID, simulator time, trajectory/control counters across B0/B1/B2.",
        "E2 pre/post reveal: 0 AVAILABLE rows before and 0 after reveal in each of two fresh valid REF episodes; required transition failed.",
        "E5 pre/post reveal: native ORDER witness not executed after decisive Phase-A failure; no qualification credit.",
        "E7 availability/invalidation: AVAILABLE in B1/B2 on 400/400 REF rows with explicit dependencies; native invalidation template not executed.",
        "False pre-reveal availability: 0 E2/E5 rows across final-valid episodes.",
        "Reveal-detection latency: undefined because no positive E2 detection occurred; one-tick bound failed.",
        "Memory retention: component semantics PASS; no native positive E2/E5 value existed to earn end-to-end retention credit.",
        "Invalidation: focused E2/E5/E7 and lifecycle tests PASS; native invalidation witness not executed after early stop.",
        "Async witness B1: not executed; native result null.",
        "Async witness B2: not executed; native result null.",
        "B2>B1 mechanism witness: not passed; only the component capability test passed, with no native qualification witness.",
        "REF end-to-end sufficiency: FAIL, 0 B2 events across 400 rows/two episodes.",
        "LMK end-to-end sufficiency: not executed after Phase-A gate failure.",
        "ORD end-to-end sufficiency: not executed after Phase-A gate failure.",
        "Non-reveal result: native control not executed; focused contract PASS, no qualification credit.",
        "USC negative-control result: native control not executed; focused contract PASS, no qualification credit.",
        "False sufficiency: 0 across all 400 final-valid rows.",
        "Phase-B REF result: NOT_EXECUTED_GATE_CLOSED.",
        "Phase-B LMK result: NOT_EXECUTED_GATE_CLOSED.",
        "Phase-B ORD result: NOT_EXECUTED_GATE_CLOSED.",
        "Phase-B USC result: NOT_EXECUTED_GATE_CLOSED.",
        "Oracle leakage: 0 reads; import/dataflow/environment firewall PASS.",
        "Observer-added VLA forwards: 0.",
        "Duplicate candidate computations: 0.",
        "PID/controller changes: 0/new none.",
        "Second control writer: 0.",
        "RoutePlanner mutations: 0 observer advances/mutations.",
        "Native cleanup/result integrity: 2/2 final-valid episodes naturally reached 10.0 s, published 200 rows each, and released processes/ports with PASS.",
        "Tests: focused 32/32 PASS in default and SimLingo Python; broader relevant 217 PASS + 4 unchanged historical Method-V1 failures in each environment; new failures=0.",
        "Unresolved mechanism risks: deployable E2 identity confidence never established; native E5, invalidation, async memory, LMK/ORD sufficiency, negative controls, and held-out generalization remain unqualified.",
        "Source-freeze result: V1 tree/table/sufficiency, redesign tree, SimLingo, checkpoint, HEAD and tracked/staged diffs are exact; Phase-B source freeze was correctly not authorized after Phase-A FAIL.",
        f"Exact GO/NO-GO verdict: `{FINAL_STATUS}`.",
        "Exactly one recommended next action: authorize a separate pre-science E2 identity-confidence provenance/calibration redesign review before any new engineering witness, formal seed, or scientific exposure.",
    ]
    assert len(returns) == 57
    final_report = (
        "# DriveClarify RQ2-T V2 pre-science native mechanism qualification\n\n"
        f"Final status: `{FINAL_STATUS}`.\n\n"
        "The real default-off integration path is closed, but the method mechanism is not qualified. "
        "Two fresh, geometry-valid native REF witnesses both executed cleanly and both failed the mandatory E2 transition without false positives. "
        "The frozen method was not weakened. Phase A stopped once GO became impossible, Phase B stayed sealed, and formal science remained at zero.\n\n"
        "## Required 57-item return\n\n"
        + "\n".join(f"{index}. {value}" for index, value in enumerate(returns, 1))
        + f"\n\n{FINAL_STATUS}\n"
    )
    write_text("FINAL_REPORT.md", final_report)

    write_text(
        "COMMAND_LOG.md",
        """# Command log

- Read authoritative state, V1 final evidence, V2 redesign contracts, source owners, and prospective freezes.
- Verified entry `master@eaa332b1bb994279b59ea5af786fdb5de96adc1b`, empty tracked/staged diffs, V1 tree/table/source, redesign tree, SimLingo, and checkpoint hashes.
- Added and compiled the default-off native wrapper, engineering scenario, launcher, and 22 focused native contracts.
- Ran engineering-only native identities `001,019,020,021,002,022,023`; no identity was rerun. Preserved every failed/adjudicated attempt.
- Repaired only lifecycle pose timing, close-time receipt serialization, and route-obstructing actor geometry. Parameters, V1 sufficiency, provider thresholds, policy, planner, PID, and control were unchanged.
- Focused pytest: 32 passed under default Python and 32 passed under SimLingo Python 3.8.
- Broader relevant pytest: 217 passed, 4 unchanged historical Method-V1 failures in each environment.
- Rehashed all protected inputs, parsed report JSON/JSONL, verified process/port cleanup, and finalized the NO-GO offline.
- Formal seed generation, formal scientific exposure, Phase B, destructive Git, evidence deletion, and commit were not run.
""",
    )

    required = [
        "FINAL_REPORT.md", "ENTRY_INTEGRITY_RECEIPT.json",
        "V1_NEGATIVE_RESULT_PRESERVATION_RECEIPT.json",
        "V2_NATIVE_RUNTIME_BINDING.md", "V2_NATIVE_RUNTIME_BINDING_RECEIPT.json",
        "V2_PARAMETER_PROVENANCE_AND_FREEZE.md", "V2_PARAMETER_PROVENANCE_AND_FREEZE.json",
        "B0_B1_B2_PAIRED_VIEW_CONTRACT.md", "B0_B1_B2_PAIRED_VIEW_CONTRACT.json",
        "PHASE_A_ENGINEERING_SCENE_MANIFEST.md", "PHASE_A_ENGINEERING_SCENE_MANIFEST.json",
        "PHASE_B_BLIND_LAYOUT_SEAL.md", "PHASE_B_BLIND_LAYOUT_SEAL.json",
        "ENGINEERING_IDENTITY_REGISTRY.json", "ENGINEERING_SEED_EXCLUSION_REGISTRY.json",
        "ENGINEERING_ATTEMPT_LEDGER.jsonl", "ENGINEERING_EXECUTION_LEDGER.jsonl",
        "PROVIDER_CORRECTNESS_RESULTS.json",
        "ASYNC_MEMORY_WITNESS_REPORT.md", "ASYNC_MEMORY_WITNESS_RESULTS.json",
        "END_TO_END_SUFFICIENCY_WITNESS_REPORT.md", "END_TO_END_SUFFICIENCY_WITNESS_RESULTS.json",
        "NEGATIVE_CONTROL_REPORT.md", "NEGATIVE_CONTROL_RESULTS.json",
        "BLIND_ENGINEERING_SMOKE_REPORT.md", "BLIND_ENGINEERING_SMOKE_RESULTS.json",
        "ORACLE_FIREWALL_RECEIPT.json",
        "ENGINEERING_QUALIFICATION_REPORT.md", "ENGINEERING_QUALIFICATION_RECEIPT.json",
        "SOURCE_FREEZE_RECEIPT.json", "FINAL_VALIDATION_RECEIPT.json", "COMMAND_LOG.md",
    ]
    write_json("FINAL_VALIDATION_RECEIPT.json", {"status": "BUILDING"})
    presence = {name: (REPORT / name).is_file() for name in required}
    json_files = sorted(REPORT.glob("*.json"))
    json_parse = {}
    for path in json_files:
        try:
            load(path)
            json_parse[path.name] = True
        except (OSError, json.JSONDecodeError):
            json_parse[path.name] = False
    validation = {
        "schema_version": "driveclarify.rq2_t_v2.pre_science_final_validation.v1",
        "status": "PASS_FINAL_VALIDATION_NO_GO_SEALED",
        "final_status": FINAL_STATUS,
        "required_file_count": len(required),
        "required_files_present": presence,
        "all_required_files_present": all(presence.values()),
        "top_level_json_parse": json_parse,
        "all_top_level_json_parse": all(json_parse.values()),
        "paired_final_valid_row_count": total_rows,
        "paired_source_identity_match": all_identity_match,
        "paired_counter_fairness": all_counter_fairness,
        "final_valid_cleanup_pass": all(value["cleanup_status"] == "PASS" for value in per_episode.values()),
        "focused_tests": {"default_python": "32 passed", "simlingo_python_3_8": "32 passed"},
        "broader_relevant_tests": {
            "default_python": "217 passed, 4 unchanged historical Method-V1 failures",
            "simlingo_python_3_8": "217 passed, 4 unchanged historical Method-V1 failures",
            "new_failures": 0,
        },
        "integrity_checks": integrity_checks,
        "formal_seed_count": 0,
        "formal_scientific_exposure_count": 0,
        "phase_b_exposure_count": 0,
        "final_report_sha256": sha256(REPORT / "FINAL_REPORT.md"),
        "qualification_receipt_sha256": sha256(REPORT / "ENGINEERING_QUALIFICATION_RECEIPT.json"),
        "source_freeze_receipt_sha256": sha256(REPORT / "SOURCE_FREEZE_RECEIPT.json"),
    }
    validation["receipt_digest"] = canonical_sha256(validation)
    write_json("FINAL_VALIDATION_RECEIPT.json", validation)
    print(json.dumps({"status": FINAL_STATUS, "final_valid": 2, "paired_rows": total_rows}, sort_keys=True))


if __name__ == "__main__":
    main()
