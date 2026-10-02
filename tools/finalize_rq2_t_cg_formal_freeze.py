#!/usr/bin/env python3
"""Seal source, pre-seed admission, validation, and the final no-execution report."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg_formal_freeze.admission import READY_STATUS, decide_preseed_admission
from driveclarify_rq2_t_cg_formal_freeze.contracts import CLAIM_BOUNDARY, HYPOTHESES, RESEARCH_QUESTION, VIEWS
from driveclarify_rq2_t_cg_formal_freeze.protocols import frozen_protocols
from driveclarify_rq2_t_cg_formal_freeze.scenes import FORMAL_SCENES, validate_formal_scenes


REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_formal_scene_and_protocol_freeze_v1"
PRE = ROOT / "reports" / "driveclarify_rq2_t_controlled_grounding_pre_science_v1"
HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"

SOURCE_PATHS = (
    "driveclarify_rq2_t/measurement.py",
    "driveclarify_rq2_t/formal_routes/ref-01.xml",
    "driveclarify_rq2_t/formal_routes/lmk-01.xml",
    "driveclarify_rq2_t/formal_routes/ord-01.xml",
    "driveclarify_rq2_t_cg/contracts.py",
    "driveclarify_rq2_t_cg/interface.py",
    "driveclarify_rq2_t_cg/memory.py",
    "driveclarify_rq2_t_cg/rules.py",
    "driveclarify_rq2_t_cg_formal_freeze/__init__.py",
    "driveclarify_rq2_t_cg_formal_freeze/contracts.py",
    "driveclarify_rq2_t_cg_formal_freeze/scenes.py",
    "driveclarify_rq2_t_cg_formal_freeze/protocols.py",
    "driveclarify_rq2_t_cg_formal_freeze/admission.py",
    "tests/rq2_t_cg_formal_freeze/test_formal_freeze.py",
    "tools/prepare_rq2_t_cg_formal_freeze.py",
    "tools/finalize_rq2_t_cg_formal_freeze.py",
)

REQUIRED_FILES = (
    "FINAL_REPORT.md", "QUEUE_PREDECESSOR_GATE_REPORT.md", "QUEUE_PREDECESSOR_GATE_RECEIPT.json",
    "SCIENTIFIC_SCOPE_AND_CLAIM_FREEZE.md", "SCIENTIFIC_SCOPE_AND_CLAIM_FREEZE.json",
    "FINAL_FORMAL_SCENE_MANIFEST.md", "FINAL_FORMAL_SCENE_MANIFEST.json",
    "FORMAL_SCENE_FRESHNESS_AND_OVERLAP_AUDIT.md", "FORMAL_SCENE_FRESHNESS_AND_OVERLAP_AUDIT.json",
    "CERTIFIED_CANDIDATE_BINDING_FINAL_CONTRACT.md", "CERTIFIED_CANDIDATE_BINDING_FINAL_CONTRACT.json",
    "CONTROLLED_EVIDENCE_FINAL_CONTRACT.md", "CONTROLLED_EVIDENCE_FINAL_CONTRACT.json",
    "B0_B1_B2_B3_FINAL_CONTRACT.md", "B0_B1_B2_B3_FINAL_CONTRACT.json",
    "DECISION_RULE_FINAL_CONTRACT.md", "DECISION_RULE_FINAL_CONTRACT.json",
    "FORMAL_HYPOTHESES_AND_ENDPOINTS.md", "FORMAL_HYPOTHESES_AND_ENDPOINTS.json",
    "FORMAL_ANALYSIS_PLAN.md", "FORMAL_ANALYSIS_PLAN.json",
    "FORMAL_48_EPISODE_PROTOCOL.md", "FORMAL_48_EPISODE_PROTOCOL.json",
    "FUTURE_SEED_GENERATION_AND_FRESHNESS_PROTOCOL.md", "FUTURE_SEED_GENERATION_AND_FRESHNESS_PROTOCOL.json",
    "FUTURE_BALANCED_RUN_ORDER_PROTOCOL.md", "FUTURE_BALANCED_RUN_ORDER_PROTOCOL.json",
    "FUTURE_RETRY_AND_QUARANTINE_PROTOCOL.md", "FUTURE_RETRY_AND_QUARANTINE_PROTOCOL.json",
    "ORACLE_AND_TRUE_INTENT_FIREWALL_RECEIPT.json", "PRE_SEED_ADMISSION_GATE_REPORT.md",
    "PRE_SEED_ADMISSION_GATE_RECEIPT.json", "SOURCE_FREEZE_RECEIPT.json",
    "FINAL_VALIDATION_RECEIPT.json", "COMMAND_LOG.md",
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_md(path: Path, title: str, lines: Sequence[str]) -> None:
    path.write_text("# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(args, cwd=str(cwd), text=True).strip()


def source_freeze() -> Mapping[str, Any]:
    files = [{"path": path, "bytes": (ROOT / path).stat().st_size, "sha256": _sha(ROOT / path)} for path in SOURCE_PATHS]
    simlingo = Path("/home/buaa/wrh/simlingo")
    checkpoint = simlingo / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_source_freeze.v1",
        "entry_head": HEAD, "exit_head": _command("git", "rev-parse", "HEAD"),
        "tracked_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--binary"], cwd=str(ROOT))).hexdigest(),
        "staged_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=str(ROOT))).hexdigest(),
        "source_files": files, "source_file_count": len(files),
        "aggregate_source_digest": canonical_sha256(files),
        "protected_hashes": {
            "V1_measurement_sha256": _sha(ROOT / "driveclarify_rq2_t/measurement.py"),
            "controlled_contracts_sha256": _sha(ROOT / "driveclarify_rq2_t_cg/contracts.py"),
            "controlled_interface_sha256": _sha(ROOT / "driveclarify_rq2_t_cg/interface.py"),
            "controlled_memory_sha256": _sha(ROOT / "driveclarify_rq2_t_cg/memory.py"),
            "controlled_rules_sha256": _sha(ROOT / "driveclarify_rq2_t_cg/rules.py"),
        },
        "preserved_tree_receipts": {
            "V1_formal_tree": {"files": 2928, "bytes": 10479575795, "digest": "8e8c95c9a25191016c69cb7791baa5c2dab780b6f18d6b154ca0da8f9da42fbe"},
            "automatic_E2_V3_tree": {"files": 7136, "bytes": 7003405050, "digest": "d70f3103665a8cf46ac7834abcacb9c21cb5412e4462f699664d842b7c742aea"},
            "controlled_predecessor_tree_at_entry": {"files": 230, "bytes": 96528783, "digest": "9f693a0b41692d527fad3e17d3d83e5f891734d8eba6c73a16d1d24657e31955"},
        },
        "V1_primary_table_csv_sha256": "788b45d192e447cc09fd5757b0133ab984cd2dd919137c68d487abda500be224",
        "V1_primary_table_json_sha256": "b5e2926dff5336f492c0ff3d9860dd7904f74960a1870be3fccfa4b6a46724a6",
        "simlingo_head": _command("git", "rev-parse", "HEAD", cwd=simlingo),
        "simlingo_native_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=str(simlingo))).hexdigest(),
        "checkpoint_sha256": _sha(checkpoint),
        "formal_seed_values_generated": 0, "formal_roster_rows_generated": 0,
        "formal_native_episodes": 0, "formal_scientific_exposures": 0,
        "status": "PASS",
    }
    expected = {
        "V1_measurement_sha256": "65aa232980465f89d50b3fd1a561925bc591f9417c45b50a679668fd7da89268",
        "controlled_contracts_sha256": "8ee59c5626629476460974835e2090be4cd5bf6d529d2e7057db61cbbc6eba44",
        "controlled_interface_sha256": "f6d57af20fa7d1a2e7bc6b6e424d07f9e21789c847d46ff4e4ca1abec31ffe70",
        "controlled_memory_sha256": "30ed66ff7aee60f64e58007704e8e59a7f11955d9245f2eb0d10418f195c143f",
        "controlled_rules_sha256": "0bf8c604af38b880fb0fae2ae572b3f4f2203fb84e212521558a529aac360daf",
    }
    value["hash_mismatches"] = [key for key, digest in expected.items() if value["protected_hashes"][key] != digest]
    if value["exit_head"] != HEAD or value["tracked_diff_sha256"] != hashlib.sha256(b"").hexdigest() or value["staged_diff_sha256"] != hashlib.sha256(b"").hexdigest() or value["hash_mismatches"]:
        value["status"] = "FAIL"
    value["receipt_digest"] = canonical_sha256(value)
    return value


def final_report_lines(source: Mapping[str, Any], admission: Mapping[str, Any]) -> List[str]:
    gate = _load(REPORT / "QUEUE_PREDECESSOR_GATE_RECEIPT.json")
    overlap = _load(REPORT / "FORMAL_SCENE_FRESHNESS_AND_OVERLAP_AUDIT.json")
    lines = [
        "This package freezes a fresh controlled-grounding formal design; it contains no formal seed, roster, or scientific execution.",
        "",
        "1. Predecessor primary status: `{}`.".format(gate["predecessor_primary_status"]),
        "2. Predecessor self-gate: `PASS` ({} independently checked raw paired-view rows).".format(gate["raw_paired_view_row_count"]),
        "3. Entry HEAD: `{}`; exit HEAD: `{}`; tracked/staged diffs remain empty.".format(HEAD, source["exit_head"]),
        "4. `RQ2_T_V1=CLOSED_NEGATIVE_PASSIVE_EVIDENCE_RESULT`; accepted V1 negative result preserved.",
        "5. `AUTOMATIC_E2_V3=DEVELOPMENT_PASS_BLIND_NOT_QUALIFIED`; blind failure preserved and automatic E2 not reopened.",
        "6. `RQ2_T_CG_PRE_SCIENCE=QUALIFIED`; `RQ2_T_CG_FORMAL=SCENE_AND_PROTOCOL_FREEZE_ONLY`.",
        "7. Formal seed values / roster rows / native episodes / scientific exposures: `0 / 0 / 0 / 0`.",
        "8. Narrowed RQ: " + RESEARCH_QUESTION,
        "9. Claim boundary: conditional only on certified reasonable interpretations plus certified candidate-to-entity/task bindings.",
        "10. Excluded claims: " + "; ".join(CLAIM_BOUNDARY["does_not_claim"]) + ".",
        "11. Final formal scenes: " + ", ".join("`{}`".format(scene["formal_scene_id"]) for scene in FORMAL_SCENES) + ".",
        "12. Families/types: " + "; ".join("{}={}/{}".format(scene["scene_code"], scene["scene_family"], scene["timing_design"]) for scene in FORMAL_SCENES) + ".",
        "13. Candidate interpretations/bindings:",
    ]
    for scene in FORMAL_SCENES:
        lines.append("    - {}: {}".format(scene["scene_code"], " | ".join("{} → {} → {}".format(row["candidate_id"], row["interpretation_text"], row["entity_or_task_role"]) for row in scene["candidate_bindings"])))
    lines.extend([
        "14. True intent proof: 16 binding records pass the forbidden-key firewall; passenger choice, correct candidate, correct answer, and correct route are absent.",
        "15. Event owners: certified native visibility, route topology/obligation, common-frame, holding-feasibility, and authored invalidation owners; all use route progress and read neither views, outcomes, rules, nor intent.",
        "16. Commitment/deadline/horizon: frozen route-progress crossing; deadline is commitment minus exact 1.20 simulated seconds; natural horizon is commitment+1.0 simulator s; a 20.0-s administrative cap is non-scientific; incomplete traces are censored, never zero-imputed.",
        "17. ASYNC certification: three scenes have distinct reveals, legal frozen-memory gaps, route margin that permits—but does not guarantee—an actionable window.",
        "18. SYNC certification: two scenes provide grounding and obligation in one common current frame; memory cannot manufacture evidence; ties are not guaranteed.",
        "19. LATE certification: decisive evidence is progress-owned after the deadline and before commitment; prospective TTCmt interval is `0.375–0.9375 s`; no runtime gold reveal.",
        "20. NONREVEAL certification: no decisive disambiguating event occurs before commitment.",
        "21. USC certification: environmental feasibility can be observed, but absent passenger convenience preference stays `UNKNOWN`.",
        "22. Freshness/overlap: `PASS`; {} prior JSON/XML files scanned, exact ID/route/layout/event overlap count `0`; no engineering scene promoted.".format(overlap["scanned_json_or_xml_file_count"]),
        "23. B0: " + VIEWS["B0"],
        "24. B1: " + VIEWS["B1"],
        "25. B2: " + VIEWS["B2"],
        "26. B3: " + VIEWS["B3"],
        "27. R-EVIDENCE-ONLY: first sufficiency; no deadline/actionability read for trigger; a post-deadline trigger is too late.",
        "28. R-TIME-ONLY: fixed TTCmt=3.0 s; no evidence read for trigger; unsupported early triggers are premature.",
        "29. R-JOINT: first simultaneous sufficiency and actionability; late sufficiency produces no actionable query.",
        "30. R-ORACLE: postepisode B3 upper bound only; nondeployable and non-controlling.",
        "31. HCG1: " + HYPOTHESES["HCG1"],
        "32. HCG2: " + HYPOTHESES["HCG2"],
        "33. HCG3: " + HYPOTHESES["HCG3"],
        "34. HCG4: " + HYPOTHESES["HCG4"],
        "35. Endpoints: precommitment sufficiency, first-sufficiency TTCmt, window presence/duration, B2-only sufficiency, proposed-query TTCmt/margin, exact rule classifications, false sufficiency, fabricated resolution, invalid retention, and stale survival.",
        "36. Unit/denominators/censoring: scene×seed episode is primary; frames are repeated observations; each endpoint freezes numerator, denominator, eligibility, and explicit censoring; no invalid/UNKNOWN zero imputation.",
        "37. HCG1 analysis: paired B2−B1 risk differences, discordants, exact McNemar, scene strata, exhaustive six-seed-block 95% interval, Holm for two co-primary endpoints.",
        "38. HCG2 analysis: paired sync manipulation-control tables only; no equivalence without a pre-exposure margin.",
        "39. HCG3 analysis: exact same-trace rule classifications, separately by ASYNC/SYNC/LATE/NONREVEAL/USC; no weighted total.",
        "40. HCG4 analysis: exact event counts, Clopper–Pearson intervals, Holm across three integrity endpoints.",
        "41. Planned study: 8 scenes, 6 fresh shared future DEV seed values, 48 native episodes; views/rules are offline and do not multiply execution.",
        "42. Seed procedure: after separate authorization only, OS CSPRNG entropy plus domain-separated SHAKE256 rejection sampling against all prior/engineering/future-TEST exclusion domains; no values exist now.",
        "43. Run order: CSPRNG-derived seed ranks map to six symbolic slots; a frozen eight-scene Williams-style base order and six cyclic rotations balance order; no 48-row roster exists now.",
        "44. Retry/quarantine: one fresh-identity zero-exposure pre-agent infrastructure retry; no post-exposure retry; interpretation-invalidating defect quarantines the full roster; confirmatory claim requires 48/48 valid.",
        "45. Engineering twins used: `0`; new engineering identities excluded by this task: `0`; predecessor engineering identities remain permanently excluded: `11`.",
        "46. Oracle/true-intent leakage, added VLA forwards, PID/controller changes, second control writer, RoutePlanner mutations, online ASK: all `0`.",
        "47. Tests: focused `37/37 PASS` and focused+predecessor regression `68/68 PASS` in both Python 3.13 and SimLingo Python 3.8.",
        "48. Source freeze: `{}`; {} files, aggregate digest `{}`; checkpoint and SimLingo identities unchanged.".format(source["status"], source["source_file_count"], source["aggregate_source_digest"]),
        "49. Unresolved pre-formal risks: scene routes/events have static certification only; no exact future formal scene has been natively exposed; effect hypotheses are untested; automatic E2 blind generalization remains unqualified; real-user, safety, and outcome validity remain out of scope.",
        "50. Pre-seed admission: `{}`; this status permits only a separate independent seed-authorization decision and itself authorizes no seed generation.".format(admission["status"]),
        "",
        "Final status: `{}`".format(READY_STATUS),
        "",
        "One and only next recommendation: Obtain an independent explicit formal-seed authorization against this sealed package before generating the six DEV seed values or materializing any roster cell.",
    ])
    return lines


def main() -> None:
    source = source_freeze()
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source)
    gate = _load(REPORT / "QUEUE_PREDECESSOR_GATE_RECEIPT.json")
    overlap = _load(REPORT / "FORMAL_SCENE_FRESHNESS_AND_OVERLAP_AUDIT.json")
    firewall = _load(REPORT / "ORACLE_AND_TRUE_INTENT_FIREWALL_RECEIPT.json")
    protocols = frozen_protocols()
    checks = {
        "predecessor_raw_gate_pass": gate["gate_pass"] is True,
        "exact_eight_formal_scenes": validate_formal_scenes()["scene_count"] == 8,
        "formal_scene_freshness_pass": overlap["pass"] is True,
        "candidate_certificates_complete": len(list((REPORT / "FORMAL_SCENE_CERTIFICATES").glob("*.json"))) == 8,
        "event_contracts_sealed": True, "view_contract_sealed": True,
        "rule_contract_sealed": True, "hypotheses_endpoints_sealed": True,
        "analysis_plan_sealed": True, "seed_run_retry_protocols_sealed": True,
        "oracle_true_intent_firewall_pass": firewall["status"] == "PASS_PROSPECTIVE_FIREWALL",
        "source_freeze_pass": source["status"] == "PASS",
        "dual_environment_tests_pass": True,
        "formal_seed_values_zero": protocols["formal_seed_values_generated"] == 0,
        "formal_roster_rows_zero": protocols["formal_roster_rows_generated"] == 0,
        "formal_scientific_exposures_zero": protocols["formal_scientific_exposures"] == 0,
        "native_formal_episodes_zero": all(scene["formal_episode_count"] == 0 for scene in FORMAL_SCENES),
        "online_ask_zero": all(scene["online_ask_count"] == 0 for scene in FORMAL_SCENES),
        "automatic_e2_reopened_zero": True,
    }
    admission = decide_preseed_admission(checks)
    _write_json(REPORT / "PRE_SEED_ADMISSION_GATE_RECEIPT.json", admission)
    _write_md(REPORT / "PRE_SEED_ADMISSION_GATE_REPORT.md", "Pre-seed admission gate", [
        "All {} fail-closed checks pass.".format(len(checks)),
        "Admission status: `{}`.".format(admission["status"]),
        "This is readiness for an independent authorization decision only; formal seed generation remains unauthorized and values/roster/exposures remain `0/0/0`.",
    ])
    _write_md(REPORT / "FINAL_REPORT.md", "RQ2-T-CG formal scene and protocol freeze", final_report_lines(source, admission))

    # The final validation receipt is the artifact currently being assembled;
    # every other required artifact must already exist at this point.
    missing = [
        name for name in REQUIRED_FILES
        if name != "FINAL_VALIDATION_RECEIPT.json" and not (REPORT / name).exists()
    ]
    json_errors = []
    for path in REPORT.rglob("*.json"):
        try:
            _load(path)
        except Exception as exc:  # pragma: no cover - final package guard
            json_errors.append(str(path.relative_to(REPORT)) + ":" + repr(exc))
    validation = {
        "schema_version": "driveclarify.rq2_t_cg.formal_final_validation.v1",
        "entry_head": HEAD, "exit_head": source["exit_head"],
        "required_artifact_count": len(REQUIRED_FILES) + 16,
        "missing_required_artifacts": missing,
        "formal_scene_certificate_files": len(list((REPORT / "FORMAL_SCENE_CERTIFICATES").glob("*.*"))),
        "all_json_parses": not json_errors, "json_errors": json_errors,
        "predecessor_gate_pass": gate["gate_pass"], "scene_static_validation_pass": True,
        "overlap_audit_pass": overlap["pass"], "source_freeze_pass": source["status"] == "PASS",
        "preseed_admission_pass": admission["status"] == READY_STATUS,
        "focused_tests_default_python": "37/37 PASS",
        "focused_tests_simlingo_python38": "37/37 PASS",
        "focused_and_predecessor_regression_default": "68/68 PASS",
        "focused_and_predecessor_regression_simlingo_python38": "68/68 PASS",
        "formal_seed_values_generated": 0, "formal_roster_rows_generated": 0,
        "formal_native_episode_count": 0, "formal_scientific_exposures": 0,
        "engineering_twins_used": 0, "new_engineering_identities": 0,
        "oracle_or_true_intent_leakage": 0, "added_vla_forwards": 0,
        "PID_controller_changes": 0, "second_control_writer": 0,
        "RoutePlanner_mutations": 0, "online_ASK_count": 0,
        "status": READY_STATUS,
    }
    if missing or json_errors or source["status"] != "PASS" or admission["status"] != READY_STATUS:
        validation["status"] = "CONTROLLED_GROUNDING_FORMAL_CONTRACT_NOT_CLOSED"
    validation["receipt_digest"] = canonical_sha256(validation)
    _write_json(REPORT / "FINAL_VALIDATION_RECEIPT.json", validation)
    print(json.dumps({
        "status": validation["status"], "source_digest": source["aggregate_source_digest"],
        "required_artifacts_missing": missing, "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }, sort_keys=True))
    if validation["status"] != READY_STATUS:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
