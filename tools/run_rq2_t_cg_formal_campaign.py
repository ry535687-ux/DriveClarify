#!/usr/bin/env python3
"""Gate, materialize, and execute the sealed 8x6 RQ2-T-CG campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1 import backend as episode_backend
from driveclarify_paper_mvp_stage6b import backend as native
from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_cg_formal_execution.builder import build_formal_episode
from driveclarify_rq2_t_cg_formal_execution.routes import materialize_all, static_route_admission
from driveclarify_rq2_t_cg_formal_execution.specs import EXPECTED_STATUS, accepted_scenes, scene_by_code, verify_freeze
from driveclarify_rq2_t_cg_formal_freeze.protocols import PROTOCOL_ID, planned_run_order, symbolic_cell_id
from tools.prepare_rq2_t_cg_formal_freeze import predecessor_gate
from tools.run_rq2_t_e2_v3 import _repair_irrelevant_display_process_false_positive


PRIMARY_REPORT = ROOT / "reports" / "driveclarify_rq2_t_cg_formal_48_episode_experiment_v1"
REPORT = Path(os.environ.get(
    "DRIVECLARIFY_RQ2_T_CG_FORMAL_REPORT_ROOT", str(PRIMARY_REPORT)
)).resolve()
ROSTER_INDEX = int(os.environ.get("DRIVECLARIFY_RQ2_T_CG_FORMAL_ROSTER_INDEX", "0"))
if ROSTER_INDEX not in (0, 1):
    raise RuntimeError("RQ2_T_CG_AT_MOST_ONE_REPLACEMENT_ROSTER")
if ROSTER_INDEX == 0 and REPORT != PRIMARY_REPORT:
    raise RuntimeError("RQ2_T_CG_PRIMARY_ROSTER_REPORT_ROOT_MISMATCH")
if ROSTER_INDEX == 1 and REPORT != PRIMARY_REPORT / "REPLACEMENT_ROSTER_01":
    raise RuntimeError("RQ2_T_CG_REPLACEMENT_ROSTER_REPORT_ROOT_MISMATCH")
FREEZE = ROOT / "reports" / "driveclarify_rq2_t_cg_formal_scene_and_protocol_freeze_v1"
ROUTES = REPORT / "FROZEN_NATIVE_ROUTES"
SEALED = REPORT / "SEALED_FORMAL_ROSTER"
ATTEMPTS = REPORT / "ATTEMPT_LEDGER.jsonl"
EXECUTIONS = REPORT / "SCIENTIFIC_EXECUTION_LEDGER.jsonl"
TERMINATIONS = REPORT / "SCIENTIFIC_TERMINATION_LEDGER.jsonl"
EXPOSURES = REPORT / "SCIENTIFIC_EXPOSURE_REGISTRY.json"
PLACEHOLDER_RUNTIME = ROOT / "driveclarify_paper_mvp_scenarios/generated/runtime_visible/dc-runtime-06cccfa4723c09ca09a9bc2b.json"
PROMOTION = native.PROMOTION_ROOT / "LIVE_PROMOTION_MANIFEST.json"
HEAD = "eaa332b1bb994279b59ea5af786fdb5de96adc1b"
REPLACEMENT_AUTHORIZATION = PRIMARY_REPORT / "REPLACEMENT_ROSTER_AUTHORIZATION.json"
REPAIR_ROOT = PRIMARY_REPORT / "ENGINEERING_REPAIR_QUALIFICATION"


EXECUTION_SOURCE_PATHS = (
    "driveclarify_rq2_t/measurement.py", "driveclarify_rq2_t_cg/contracts.py",
    "driveclarify_rq2_t_cg/interface.py", "driveclarify_rq2_t_cg/memory.py",
    "driveclarify_rq2_t_cg/rules.py", "driveclarify_rq2_t_cg_formal_freeze/contracts.py",
    "driveclarify_rq2_t_cg_formal_freeze/scenes.py", "driveclarify_rq2_t_cg_formal_freeze/protocols.py",
    "driveclarify_rq2_t_cg_formal_execution/__init__.py",
    "driveclarify_rq2_t_cg_formal_execution/specs.py",
    "driveclarify_rq2_t_cg_formal_execution/routes.py",
    "driveclarify_rq2_t_cg_formal_execution/native_scenario.py",
    "driveclarify_rq2_t_cg_formal_execution/builder.py",
    "driveclarify_paper_mvp_scenarios/leaderboard_scenario_root/srunner/scenarios/driveclarify_rq2_t_cg_formal.py",
    "tests/rq2_t_cg_formal_execution/test_formal_execution.py",
    "tools/run_rq2_t_cg_formal_campaign.py",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, default: Any = None) -> Any:
    return default if not path.is_file() else json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> Sequence[Mapping[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def _append(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        handle.flush(); os.fsync(handle.fileno())


def _write_md(path: Path, title: str, lines: Sequence[str]) -> None:
    path.write_text("# " + title + "\n\n" + "\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _command(args: Sequence[str], cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def _tree_snapshot(roots: Sequence[Path]) -> Mapping[str, Any]:
    count, byte_count = 0, 0
    inaccessible = []
    digest = hashlib.sha256()
    for root in roots:
        rows = []
        def onerror(error):
            inaccessible.append(str(getattr(error, "filename", error)))
        for directory, directories, files in os.walk(str(root), topdown=True, onerror=onerror, followlinks=False):
            base = Path(directory)
            if REPORT == base or REPORT in base.parents:
                directories[:] = []
                continue
            for name in files:
                rows.append(base / name)
        for path in sorted(rows):
            try:
                size = path.stat().st_size
            except OSError:
                inaccessible.append(str(path)); continue
            count += 1; byte_count += size
            rel = str(path).encode("utf-8", errors="surrogateescape")
            digest.update(len(rel).to_bytes(8, "big")); digest.update(rel)
            digest.update(size.to_bytes(8, "big"))
    return {
        "root_count": len(roots), "file_count": count, "byte_count": byte_count,
        "identity_digest": digest.hexdigest(), "inaccessible_path_count": len(set(inaccessible)),
        "inaccessible_paths": sorted(set(inaccessible)),
        "inaccessible_paths_are_nonregistry_runtime_caches": all("/runtime/cache/" in path for path in inaccessible),
    }


def _source_freeze() -> Mapping[str, Any]:
    files = [{"path": path, "bytes": (ROOT / path).stat().st_size, "sha256": _sha(ROOT / path)} for path in EXECUTION_SOURCE_PATHS]
    simlingo = Path("/home/buaa/wrh/simlingo")
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_execution_source_freeze.v1",
        "entry_head": HEAD, "current_head": _command(("git", "rev-parse", "HEAD")).stdout.strip(),
        "tracked_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--binary"], cwd=str(ROOT))).hexdigest(),
        "staged_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=str(ROOT))).hexdigest(),
        "source_files": files, "source_file_count": len(files), "aggregate_source_digest": canonical_sha256(files),
        "simlingo_head": _command(("git", "rev-parse", "HEAD"), simlingo).stdout.strip(),
        "simlingo_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=str(simlingo))).hexdigest(),
        "checkpoint_sha256": _sha(simlingo / "outputs/simlingo/checkpoints/epoch=013.ckpt/pytorch_model.pt"),
        "PID_controller_identity": _sha(ROOT / "driveclarify_paper_mvp_runtime/agent_wrapper.py") if (ROOT / "driveclarify_paper_mvp_runtime/agent_wrapper.py").is_file() else "NATIVE_SIMLINGO_UNCHANGED",
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
        "roster_index": ROSTER_INDEX,
        "replacement_roster_use_count": ROSTER_INDEX,
        "quarantined_roster_00_preserved": (
            ROSTER_INDEX == 0 or (
                (PRIMARY_REPORT / "FULL_ROSTER_QUARANTINE.json").is_file()
                and (PRIMARY_REPORT / "FULL_ROSTER_QUARANTINE_EVIDENCE_SEAL.json").is_file()
            )
        ),
    }
    value["status"] = "PASS" if (
        value["current_head"] == HEAD
        and value["tracked_diff_sha256"] == hashlib.sha256(b"").hexdigest()
        and value["staged_diff_sha256"] == hashlib.sha256(b"").hexdigest()
        and value["simlingo_head"] == "743b243afd6cf5ff51b9fa1f8cac86f22d569684"
        and value["simlingo_diff_sha256"] == "40298d8760c787d81038756932785e4ba8ab4da78d31c2cc66075dffed613c7a"
        and value["checkpoint_sha256"] == "ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28"
    ) else "FAIL"
    value["receipt_digest"] = canonical_sha256(value)
    return value


def _route_path(scene_code: str) -> Path:
    return ROUTES / (scene_code + ".xml")


def _episode_spec(cell_id: str, scene_code: str, seed: int) -> native.EpisodeSpec:
    scene = scene_by_code(scene_code)
    return native.EpisodeSpec(
        episode_id=cell_id, runtime_config_id="RQ2TCG-FORMAL-" + scene_code,
        scenario_id=scene["formal_scene_id"], runtime_fixture_id="rq2-t-cg-formal-" + scene_code.lower(),
        split="train", seed=int(seed), method_id="original_simlingo",
        town=scene["route"]["town"], route_id="RQ2TCG-FORMAL-" + scene_code,
        route_path=_route_path(scene_code).resolve(), runtime_manifest_path=PLACEHOLDER_RUNTIME.resolve(),
        raw_instruction=scene["instruction"], information_expected=False,
        schedule_sha256=_sha(FREEZE / "FORMAL_48_EPISODE_PROTOCOL.json"),
        runtime_manifest_sha256=_sha(PLACEHOLDER_RUNTIME), promotion_receipt_path=PROMOTION.resolve(),
        promotion_receipt_payload_sha256=_sha(PROMOTION),
    )


def _environment(spec: native.EpisodeSpec, output: Path, cell: Mapping[str, Any]) -> Mapping[str, str]:
    values = native.build_environment(spec, output, visualization=False)
    for key in tuple(values):
        upper = key.upper()
        if key.startswith("DRIVECLARIFY_") and key not in ("DRIVECLARIFY_PAPER_MVP_STAGE6B_METHOD_ID",):
            values.pop(key, None)
        if upper.endswith("_GOLD") or any(token in upper for token in ("TRUE_INTENT", "CORRECT_ANSWER", "EXPECTED_METHOD_RESULT")):
            values.pop(key, None)
    values.update({
        "SCENARIO_RUNNER_ROOT": str(native.SCENARIO_DISCOVERY_ROOT),
        # The already-qualified CP1 probe is the read-only native source-frame
        # recorder.  WORLDSTATE_OUTPUT alone is intentionally inert in the
        # SimLingo hook, so its explicit enablement and provenance outputs are
        # part of the formal execution environment.
        "DRIVECLARIFY_PROBE_ENABLED": "1",
        "DRIVECLARIFY_PROBE_OUTPUT": str(output / "probe" / "probe.jsonl"),
        "DRIVECLARIFY_PROBE_EQUIVALENCE": str(output / "probe" / "PROBE_EQUIVALENCE.json"),
        "DRIVECLARIFY_PROBE_RUN_ID": str(cell["cell_id"]),
        "DRIVECLARIFY_WORLDSTATE_OUTPUT": str(output / "post_hoc_world_state.jsonl"),
        "DRIVECLARIFY_RQ2_T_CG_FORMAL_SCENARIO_RECEIPT": str(output / "FORMAL_SCENARIO_RECEIPT.json"),
        "DRIVECLARIFY_RQ2_T_CG_FORMAL_ACTIVATION_RECEIPT": str(output / "FORMAL_ACTIVATION_RECEIPT.json"),
        "DRIVECLARIFY_RQ2_T_CG_FORMAL_COMMITMENT_RECEIPT": str(output / "FORMAL_COMMITMENT_RECEIPT.json"),
        "DRIVECLARIFY_RQ2_T_CG_FORMAL_CELL_ID": str(cell["cell_id"]),
        "DRIVECLARIFY_RQ2_T_CG_CONTROLLED_INTERFACE_RUNTIME": "0",
        "DRIVECLARIFY_RQ2_T_CG_ENGINEERING_QUALIFICATION": (
            "1" if cell.get("engineering_qualification") is True else "0"
        ),
        "DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED": "0",
        "DRIVECLARIFY_RQ2_T_E2_V4_NATIVE_EVIDENCE": "0",
        "DRIVECLARIFY_RQ2_T_E2_V3_NATIVE_EVIDENCE": "0",
        "DRIVECLARIFY_RQ2_T_V2_NATIVE_EVIDENCE": "0",
    })
    return values


def queue_and_environment_gate() -> Mapping[str, Any]:
    replacement_authorization = _load(REPLACEMENT_AUTHORIZATION, {})
    replacement_authorized = (
        ROSTER_INDEX == 0 or (
            replacement_authorization.get("status") == "AUTHORIZED_SINGLE_REPLACEMENT_ROSTER_01"
            and replacement_authorization.get("replacement_roster_use_count") == 1
            and replacement_authorization.get("replacement_report_root") == str(REPORT.relative_to(ROOT))
            and (PRIMARY_REPORT / "FULL_ROSTER_QUARANTINE.json").is_file()
            and (REPAIR_ROOT / "ENGINEERING_REPAIR_QUALIFICATION_RESULT.json").is_file()
        )
    )
    if not replacement_authorized:
        raise RuntimeError("FORMAL_EXECUTION_INTEGRITY_NOT_CLOSED")
    REPORT.mkdir(parents=True, exist_ok=True)
    mechanism = predecessor_gate()
    freeze = verify_freeze()
    queue = {
        "schema_version": "driveclarify.rq2_t_cg.formal_execution_queue_gate.v1",
        "mechanism_status": mechanism["predecessor_primary_status"],
        "mechanism_gate_pass": mechanism["gate_pass"], "mechanism_gate_digest": mechanism["receipt_digest"],
        "formal_freeze_status": EXPECTED_STATUS, "formal_freeze_gate_pass": freeze["pass"],
        "formal_freeze_verification": freeze,
        "formal_seed_values_generated": 0, "formal_scientific_exposures": 0,
        "roster_index": ROSTER_INDEX,
        "replacement_roster_use_count": ROSTER_INDEX,
        "replacement_authorized": replacement_authorized,
    }
    queue["pass"] = queue["mechanism_gate_pass"] and queue["formal_freeze_gate_pass"]
    queue["status_if_failed"] = "PREDECESSOR_FORMAL_FREEZE_GATE_NOT_SATISFIED_NO_EXECUTION"
    queue["receipt_digest"] = canonical_sha256(queue)
    _write_json(REPORT / "QUEUE_EXECUTION_PREDECESSOR_GATE_RECEIPT.json", queue)
    _write_md(REPORT / "QUEUE_EXECUTION_PREDECESSOR_GATE_REPORT.md", "Queue execution predecessor gate", [
        "Mechanism gate: `{}`; formal-freeze gate: `{}`.".format(queue["mechanism_gate_pass"], queue["formal_freeze_gate_pass"]),
        "Formal seeds/exposures before gate: `0/0`.",
    ])
    if not queue["pass"]:
        raise RuntimeError(queue["status_if_failed"])
    route_receipts = list(materialize_all(ROUTES))
    source = _source_freeze()
    _write_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", source)
    spec = _episode_spec("RQ2TCG-PRESEED-NOEXPOSURE", "REF-ASYNC", 0)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = native._git_diff_sha256(native.SIMLINGO_ROOT)
    preflight_dir = REPORT / "PRESEED_PREFLIGHT"
    preflight_dir.mkdir(parents=True, exist_ok=True)
    preflight = _repair_irrelevant_display_process_false_positive(native.native_preflight(spec, preflight_dir, visualization=True))
    disk = shutil.disk_usage(str(ROOT))
    compile_default = _command((sys.executable, "-m", "py_compile") + tuple(str(ROOT / path) for path in EXECUTION_SOURCE_PATHS if path.endswith(".py")))
    compile_py38 = _command(("/home/buaa/anaconda3/envs/simlingo/bin/python", "-m", "py_compile") + tuple(str(ROOT / path) for path in EXECUTION_SOURCE_PATHS if path.endswith(".py")))
    pytest_default = _command(("env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", sys.executable, "-m", "pytest", "-q", "tests/rq2_t_cg", "tests/rq2_t_cg_formal_freeze", "tests/rq2_t_cg_formal_execution"))
    pytest_py38 = _command(("env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "/home/buaa/anaconda3/envs/simlingo/bin/python", "-m", "pytest", "-q", "tests/rq2_t_cg", "tests/rq2_t_cg_formal_freeze", "tests/rq2_t_cg_formal_execution"))
    checks = {
        "queue_gate": queue["pass"], "source_freeze": source["status"] == "PASS",
        "replacement_authorization": replacement_authorized,
        "quarantined_roster_00_preserved": source["quarantined_roster_00_preserved"],
        "eight_routes_static": len(route_receipts) == 8 and all(row["status"] == "PASS_STATIC_FORMAL_ROUTE_ADMISSION" for row in route_receipts),
        "physical_display_gpu_carla_preflight": preflight.get("status") == "PASS",
        "disk_free_at_least_10GiB": disk.free >= 10 * 1024 ** 3,
        "atomic_publication_writable": os.access(str(REPORT), os.W_OK),
        "default_compile": compile_default.returncode == 0,
        "python38_compile": compile_py38.returncode == 0,
        "default_tests": pytest_default.returncode == 0,
        "python38_tests": pytest_py38.returncode == 0,
        "formal_seeds_unmaterialized": not (REPORT / "FORMAL_DEV_SEEDS.json").exists(),
        "formal_exposures_zero": not EXPOSURES.exists(),
    }
    receipt = {
        "schema_version": "driveclarify.rq2_t_cg.final_preseed_environment_gate.v1",
        "checks": checks, "failed_checks": [key for key, value in checks.items() if not value],
        "route_admissions": route_receipts, "native_preflight": preflight,
        "disk": {"free_bytes": disk.free, "total_bytes": disk.total},
        "compile_default_output": compile_default.stdout[-4000:], "compile_python38_output": compile_py38.stdout[-4000:],
        "tests_default_output": pytest_default.stdout[-4000:], "tests_python38_output": pytest_py38.stdout[-4000:],
        "source_freeze_digest": source["receipt_digest"], "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0,
    }
    receipt["pass"] = not receipt["failed_checks"]
    receipt["status"] = "PASS_FINAL_PRESEED_ENVIRONMENT_GATE" if receipt["pass"] else "ENVIRONMENT_PHYSICALLY_INCAPABLE"
    receipt["receipt_digest"] = canonical_sha256(receipt)
    _write_json(REPORT / "FINAL_PRESEED_ENVIRONMENT_GATE_RECEIPT.json", receipt)
    _write_json(REPORT / "ENTRY_INTEGRITY_RECEIPT.json", {
        "entry_head": HEAD, "queue_gate_digest": queue["receipt_digest"], "environment_gate_digest": receipt["receipt_digest"],
        "formal_manifest_digest": freeze["manifest_digest"], "source_freeze_digest": source["receipt_digest"],
        "formal_scene_digests": freeze["scene_digests"], "formal_seed_values_generated": 0,
        "formal_scientific_exposures": 0, "status": "PASS",
    })
    _write_md(REPORT / "FINAL_PRESEED_ENVIRONMENT_GATE_REPORT.md", "Final pre-seed environment gate", [
        "All {} checks pass: `{}`.".format(len(checks), receipt["pass"]),
        "Eight exact route serializations pass; native preflight status: `{}`.".format(preflight.get("status")),
        "Free disk: {:.2f} GiB; formal seeds/exposures remain `0/0`.".format(disk.free / 1024 ** 3),
    ])
    print(json.dumps({"status": receipt["status"], "failed": receipt["failed_checks"], "free_GiB": disk.free / 1024 ** 3}, sort_keys=True), flush=True)
    if not receipt["pass"]:
        raise RuntimeError(receipt["status"])
    return receipt


def _freshness_occurrences(values: Sequence[int], excluded_root: Path = None) -> Mapping[str, Any]:
    pattern = "(?<![0-9])(?:" + "|".join(str(value) for value in values) + ")(?![0-9])"
    command = ["rg", "-a", "-uuu", "-P", "-o", "--no-messages", "-e", pattern, str(ROOT), "/home/buaa/wrh/simlingo"]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    matches_by_value = {str(value): [] for value in values}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        path, match = line.rsplit(":", 1)
        candidate_path = Path(path).resolve()
        if excluded_root is not None and (
            candidate_path == excluded_root or excluded_root in candidate_path.parents
        ):
            continue
        if match in matches_by_value:
            matches_by_value[match].append(path)
    paths = sorted({path for rows in matches_by_value.values() for path in rows})
    filename_matches = []
    for base in (ROOT, Path("/home/buaa/wrh/simlingo")):
        for directory, directories, files in os.walk(str(base), topdown=True, onerror=lambda error: None, followlinks=False):
            parent = Path(directory)
            if excluded_root is not None and (
                excluded_root == parent or excluded_root in parent.parents
            ):
                directories[:] = []
                continue
            for name in files:
                if any(str(value) in name for value in values):
                    filename_matches.append(str(parent / name))
    return {
        "content_match_paths": paths, "content_matches_by_value": matches_by_value,
        "filename_match_paths": filename_matches, "rg_return_code": result.returncode,
        "strict_decimal_token_boundary": True,
        "excluded_root": None if excluded_root is None else str(excluded_root),
    }


def materialize_seeds_and_roster() -> Mapping[str, Any]:
    gate = _load(REPORT / "FINAL_PRESEED_ENVIRONMENT_GATE_RECEIPT.json", {})
    source = _load(REPORT / "SOURCE_FREEZE_RECEIPT.json", {})
    if gate.get("status") != "PASS_FINAL_PRESEED_ENVIRONMENT_GATE" or source.get("status") != "PASS":
        raise RuntimeError("CONTROLLED_GROUNDING_FORMAL_CONTRACT_NOT_CLOSED")
    if SEALED.exists() or (REPORT / "FORMAL_DEV_SEEDS.json").exists():
        raise RuntimeError("RQ2_T_CG_FORMAL_SEED_TRANSACTION_ALREADY_EXISTS")
    for row in source["source_files"]:
        if _sha(ROOT / row["path"]) != row["sha256"]:
            raise RuntimeError("RQ2_T_CG_PRESEED_SOURCE_DRIFT")
    snapshot = _tree_snapshot((ROOT, Path("/home/buaa/wrh/simlingo")))
    entropy = secrets.token_bytes(32)
    values = []
    counter = 0
    while len(values) < 6:
        payload = PROTOCOL_ID.encode("ascii") + b"\x00" + entropy + counter.to_bytes(8, "big")
        value = int.from_bytes(hashlib.shake_256(payload).digest(4), "big")
        counter += 1
        if value <= 65535 or value in values:
            continue
        values.append(value)
    freshness_rejections = []
    rejected_all = set()
    while True:
        freshness = _freshness_occurrences(values, excluded_root=REPORT)
        rejected = {
            int(value) for value, paths in freshness["content_matches_by_value"].items() if paths
        }
        rejected.update(
            value for value in values
            if any(str(value) in path for path in freshness["filename_match_paths"])
        )
        if not rejected:
            break
        rejected_all.update(rejected)
        freshness_rejections.append({
            "rejected_values": sorted(rejected),
            "reason": "STRICT_PRIOR_DECIMAL_TOKEN_OR_FILENAME_OCCURRENCE",
            "content_match_paths": freshness["content_match_paths"],
            "filename_match_paths": freshness["filename_match_paths"],
        })
        values = [value for value in values if value not in rejected]
        while len(values) < 6:
            payload = PROTOCOL_ID.encode("ascii") + b"\x00" + entropy + counter.to_bytes(8, "big")
            value = int.from_bytes(hashlib.shake_256(payload).digest(4), "big")
            counter += 1
            if value <= 65535 or value in values or value in rejected_all:
                continue
            values.append(value)
    order_rows = planned_run_order(values)
    scene_map = {scene["scene_code"]: scene for scene in accepted_scenes()}
    route_admissions = {row["scene_code"]: row for row in gate["route_admissions"]}
    roster = []
    sequence = 0
    for seed_row in order_rows:
        for scene_code in seed_row["scene_order"]:
            sequence += 1
            scene = scene_map[scene_code]
            base_cell_id = symbolic_cell_id(scene_code, seed_row["slot_id"])
            cell_id = (
                base_cell_id if ROSTER_INDEX == 0
                else base_cell_id.replace("RQ2TCG-DEV-", "RQ2TCG-DEV-R1-", 1)
            )
            cell = {
                "run_sequence": sequence, "cell_id": cell_id,
                "scene_code": scene_code, "formal_scene_id": scene["formal_scene_id"],
                "formal_scene_digest": scene["formal_scene_digest"], "route_spec_digest": scene["route"]["route_spec_digest"],
                "native_route_sha256": route_admissions[scene_code]["native_route_sha256"],
                "event_schedule_digest": canonical_sha256(scene["events"]),
                "candidate_binding_digest": canonical_sha256(scene["candidate_bindings"]),
                "seed_slot": seed_row["slot_id"], "seed": int(seed_row["seed"]),
                "seed_classification": "RQ2_T_CG_FORMAL_DEV",
                "roster_index": ROSTER_INDEX,
                "replacement_roster_use_count": ROSTER_INDEX,
                "output_path": str((REPORT / "NATIVE_TRACES" / cell_id).relative_to(ROOT)),
                "source_freeze_digest": source["receipt_digest"], "retry_contract": "INITIAL_PLUS_AT_MOST_TWO_ZERO_EXPOSURE_INFRASTRUCTURE_RETRIES",
            }
            cell["cell_digest"] = canonical_sha256(cell)
            roster.append(cell)
    if len(roster) != 48 or len({row["cell_id"] for row in roster}) != 48:
        raise RuntimeError("RQ2_T_CG_FORMAL_ROSTER_CARDINALITY_FAILED")
    seeds_doc = {
        "schema_version": "driveclarify.rq2_t_cg.formal_dev_seeds.v1", "protocol_id": PROTOCOL_ID,
        "classification": "RQ2_T_CG_FORMAL_DEV", "entropy_sha256": hashlib.sha256(entropy).hexdigest(),
        "roster_index": ROSTER_INDEX, "replacement_roster_use_count": ROSTER_INDEX,
        "derivation": "SHAKE256_DOMAIN_SEPARATED_UINT32_REJECTION", "rejection_count": counter - 6,
        "reserved_range_rejected": "0..65535", "seed_count": 6,
        "seeds": [{"seed_slot": row["slot_id"], "value": int(row["seed"])} for row in order_rows],
        "same_six_across_all_eight_scenes": True, "no_outcome_based_redraw": True,
    }
    seeds_doc["seed_set_digest"] = canonical_sha256(seeds_doc)
    freshness_doc = {
        "schema_version": "driveclarify.rq2_t_cg.formal_seed_freshness.v1",
        "prior_domain_snapshot_before_generation": snapshot,
        "scan_roots": [str(ROOT), "/home/buaa/wrh/simlingo"],
        "excluded_path": str(REPORT), "content_matches": freshness["content_match_paths"],
        "filename_matches": freshness["filename_match_paths"], "freshness_pass": True,
        "freshness_rejection_rounds": freshness_rejections,
        "future_test_exclusion_required": True, "seed_set_digest": seeds_doc["seed_set_digest"],
    }
    freshness_doc["proof_digest"] = canonical_sha256(freshness_doc)
    roster_doc = {
        "schema_version": "driveclarify.rq2_t_cg.formal_roster.v1", "cell_count": 48,
        "scene_count": 8, "seed_count": 6, "cells": roster,
        "roster_index": ROSTER_INDEX, "replacement_roster_use_count": ROSTER_INDEX,
        "source_freeze_digest": source["receipt_digest"], "seed_set_digest": seeds_doc["seed_set_digest"],
    }
    roster_doc["roster_digest"] = canonical_sha256(roster_doc)
    run_order_doc = {
        "schema_version": "driveclarify.rq2_t_cg.formal_balanced_order.v1",
        "algorithm": _load(FREEZE / "FUTURE_BALANCED_RUN_ORDER_PROTOCOL.json")["protocol"],
        "seed_block_orders": order_rows, "run_sequence": [{"run_sequence": row["run_sequence"], "cell_id": row["cell_id"]} for row in roster],
        "roster_digest": roster_doc["roster_digest"],
    }
    run_order_doc["run_order_digest"] = canonical_sha256(run_order_doc)
    txn = REPORT / (".seed_transaction_" + secrets.token_hex(8))
    txn.mkdir(parents=False, exist_ok=False)
    _write_json(txn / "FORMAL_DEV_SEEDS.json", seeds_doc)
    _write_json(txn / "FORMAL_SEED_FRESHNESS_PROOF.json", freshness_doc)
    _write_json(txn / "FORMAL_48_CELL_ROSTER.json", roster_doc)
    _write_json(txn / "FORMAL_BALANCED_RUN_ORDER.json", run_order_doc)
    os.replace(str(txn), str(SEALED))
    for name in ("FORMAL_DEV_SEEDS.json", "FORMAL_SEED_FRESHNESS_PROOF.json", "FORMAL_48_CELL_ROSTER.json", "FORMAL_BALANCED_RUN_ORDER.json"):
        os.link(str(SEALED / name), str(REPORT / name))
    _write_json(EXPOSURES, {"schema_version": "driveclarify.rq2_t_cg.scientific_exposure_registry.v1", "roster_digest": roster_doc["roster_digest"], "exposure_count": 0, "exposures": []})
    for path in (ATTEMPTS, EXECUTIONS, TERMINATIONS):
        path.touch(exist_ok=False)
    result = {"seed_count": 6, "cell_count": 48, "seed_set_digest": seeds_doc["seed_set_digest"], "roster_digest": roster_doc["roster_digest"], "status": "PASS_ATOMIC_FORMAL_ROSTER_MATERIALIZATION"}
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


def _fresh_engineering_identity_and_seed() -> Mapping[str, Any]:
    for rejection_round in range(4096):
        seed = secrets.randbits(32)
        if seed <= 65535:
            continue
        freshness = _freshness_occurrences((seed,), excluded_root=None)
        if freshness["content_match_paths"] or freshness["filename_match_paths"]:
            continue
        identity = "RQ2TCG-ENG-FORMAL-REPAIR-" + secrets.token_hex(8).upper()
        identity_scan = subprocess.run(
            ["rg", "-a", "-uuu", "-F", "-l", "--no-messages", identity,
             str(ROOT), "/home/buaa/wrh/simlingo"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if identity_scan.stdout.strip():
            continue
        return {
            "identity": identity, "seed": seed,
            "seed_freshness": freshness,
            "identity_prior_match_paths": [],
            "rejection_rounds": rejection_round,
        }
    raise RuntimeError("FRESH_ENGINEERING_REPAIR_IDENTITY_GENERATION_FAILED")


def qualify_engineering_repair(wall_timeout: float) -> Mapping[str, Any]:
    if REPORT != PRIMARY_REPORT or ROSTER_INDEX != 0:
        raise RuntimeError("ENGINEERING_REPAIR_QUALIFICATION_MUST_USE_PRIMARY_REPORT_ROOT")
    if (REPAIR_ROOT / "ENGINEERING_REPAIR_QUALIFICATION_RESULT.json").exists():
        raise RuntimeError("ENGINEERING_REPAIR_IDENTITY_IMMUTABLE_NO_RERUN")
    quarantine = _load(PRIMARY_REPORT / "FULL_ROSTER_QUARANTINE.json", {})
    seal = _load(PRIMARY_REPORT / "FULL_ROSTER_QUARANTINE_EVIDENCE_SEAL.json", {})
    if (
        quarantine.get("status") != "QUARANTINED_FULL_ROSTER_POST_EXPOSURE_INTEGRITY_DEFECT"
        or seal.get("status") != "SEALED_QUARANTINED_ROSTER_00_POST_EXPOSURE_ENGINEERING_INTEGRATION_DEFECT"
    ):
        raise RuntimeError("QUARANTINED_ROSTER_00_NOT_SEALED")
    for name, expected in seal.get("critical_artifact_sha256", {}).items():
        if name in (
            "FORMAL_ACTIVATION_RECEIPT.json", "FORMAL_COMMITMENT_RECEIPT.json",
            "FORMAL_SCENARIO_RECEIPT.json", "FORMAL_EXECUTION_RESULT.json",
            "CLEANUP_RECEIPT.json", "evaluator_stdout.log", "leaderboard_results.json",
        ):
            path = PRIMARY_REPORT / "NATIVE_TRACES" / "RQ2TCG-DEV-REF-ASYNC-S01" / "attempt_01" / name
        else:
            path = PRIMARY_REPORT / name
        if not path.is_file() or _sha(path) != expected:
            raise RuntimeError("QUARANTINED_ROSTER_00_EVIDENCE_DRIFT")

    fresh = _fresh_engineering_identity_and_seed()
    identity, seed = str(fresh["identity"]), int(fresh["seed"])
    output = REPAIR_ROOT / identity / "attempt_01"
    output.mkdir(parents=True, exist_ok=False)
    source = _source_freeze()
    scene = scene_by_code("REF-ASYNC")
    route_receipt = static_route_admission(_route_path("REF-ASYNC"), scene)
    cell = {
        "cell_id": identity, "scene_code": "REF-ASYNC", "seed_slot": "ENG-REPAIR-01",
        "seed": seed, "engineering_qualification": True,
        "formal_scene_digest": scene["formal_scene_digest"],
        "native_route_sha256": route_receipt["native_route_sha256"],
    }
    registry = {
        "schema_version": "driveclarify.rq2_t_cg.engineering_repair_exclusion_registry.v1",
        "identities": [{
            "identity": identity, "seed": seed, "classification": "ENGINEERING_ONLY",
            "formal_scientific_exposure": False, "future_formal_dev_excluded": True,
            "future_test_excluded": True, "future_rq3_excluded": True,
            "automatic_e2_excluded": True, "status": "RESERVED_IMMUTABLE_ATTEMPT_01",
        }],
        "freshness_proof": fresh,
    }
    registry["registry_digest"] = canonical_sha256(registry)
    _write_json(REPAIR_ROOT / "ENGINEERING_REPAIR_EXCLUSION_REGISTRY.json", registry)

    spec = _episode_spec(identity, "REF-ASYNC", seed)
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = native._git_diff_sha256(native.SIMLINGO_ROOT)
    preflight = _repair_irrelevant_display_process_false_positive(
        native.native_preflight(spec, output, visualization=True)
    )
    exposure_before = _sha(EXPOSURES)
    runtime = error = lease = None
    if preflight.get("status") == "PASS":
        try:
            with episode_backend.serial_gpu_lease() as lease_row:
                lease = lease_row
                runtime = native.run_native_episode(
                    spec, output, command=native.build_command(spec, output),
                    environment=_environment(spec, output, cell), cwd=native.SIMLINGO_ROOT,
                    wall_timeout_seconds=wall_timeout,
                    wall_timeout_reason="RQ2_T_CG_ENGINEERING_REPAIR_QUALIFICATION_WALL_CONTAINMENT",
                    cleanup_writer=lambda value: _write_json(output / "CLEANUP_RECEIPT.json", value),
                )
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
    activation = _load(output / "FORMAL_ACTIVATION_RECEIPT.json", {})
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    cleanup = _load(output / "CLEANUP_RECEIPT.json", {})
    builder = None
    if (
        error is None
        and activation.get("formal_scientific_exposure") is False
        and isinstance(scenario.get("terminal"), Mapping)
        and scenario["terminal"].get("state") == "NATURAL_HORIZON_OBSERVED"
        and (output / "post_hoc_world_state.jsonl").is_file()
    ):
        try:
            builder = build_formal_episode(output, cell)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc), "stage": "ENGINEERING_POSTTRACE_BUILDER"}
    zero_added = isinstance(builder, Mapping) and all(builder.get(key) == 0 for key in (
        "observer_added_vla_forwards", "duplicate_candidate_computations", "PID_controller_changes",
        "second_control_writer", "RoutePlanner_mutations", "UKF_mutations", "command_history_mutations",
        "runtime_true_intent_reads", "online_ask_count",
    ))
    integrity = {
        "preflight_pass": preflight.get("status") == "PASS",
        "runtime_created": isinstance(runtime, Mapping),
        "cleanup_pass": cleanup.get("status") == "PASS",
        "engineering_activation_only": activation.get("engineering_qualification") is True,
        "formal_scientific_exposure_false": activation.get("formal_scientific_exposure") is False,
        "scientific_exposure_registry_unchanged": _sha(EXPOSURES) == exposure_before,
        "native_trace_present_nonempty": (
            (output / "post_hoc_world_state.jsonl").is_file()
            and (output / "post_hoc_world_state.jsonl").stat().st_size > 0
        ),
        "natural_horizon": isinstance(scenario.get("terminal"), Mapping)
        and scenario["terminal"].get("state") == "NATURAL_HORIZON_OBSERVED",
        "all_event_starts": len(scenario.get("event_start_rows", ())) == scenario.get("expected_event_count"),
        "all_continuous_event_windows_entered": all(
            row.get("window_entry_observed") is True for row in scenario.get("event_start_rows", ())
        ),
        "commitment_observed": isinstance(scenario.get("commitment"), Mapping)
        and scenario["commitment"].get("state") == "COMMITMENT_OBSERVED",
        "posttrace_builder_complete": isinstance(builder, Mapping),
        "same_source_identity_all_views": isinstance(builder, Mapping)
        and builder.get("same_source_identity_all_views") is True,
        "zero_added_compute_control": zero_added,
    }
    passed = error is None and all(integrity.values())
    result = {
        "schema_version": "driveclarify.rq2_t_cg.engineering_repair_qualification.v1",
        "status": "PASS_ENGINEERING_REPAIR_QUALIFIED_FOR_SINGLE_REPLACEMENT_ROSTER" if passed else "FAIL_ENGINEERING_REPAIR_QUALIFICATION",
        "identity": identity, "seed": seed, "classification": "ENGINEERING_ONLY_PERMANENTLY_EXCLUDED",
        "formal_scientific_exposure": False, "source_freeze": source,
        "first_invalid_owner_repaired": "FORMAL_WORLD_STATE_RECORDER_ENABLEMENT",
        "secondary_integrity_repair": "CONTINUOUS_ROUTE_SEGMENT_EVENT_WINDOW_INTERSECTION",
        "scientific_definition_changed": False, "preflight": preflight,
        "runtime": runtime, "gpu_lease": lease, "error": error,
        "integrity": integrity, "builder": builder,
    }
    result["record_digest"] = canonical_sha256(result)
    _write_json(output / "ENGINEERING_REPAIR_QUALIFICATION_RESULT.json", result)
    _write_json(REPAIR_ROOT / "ENGINEERING_REPAIR_QUALIFICATION_RESULT.json", result)
    registry["identities"][0]["status"] = (
        "QUALIFIED_AND_PERMANENTLY_EXCLUDED" if passed else "FAILED_AND_PERMANENTLY_EXCLUDED"
    )
    registry["registry_digest"] = canonical_sha256({key: value for key, value in registry.items() if key != "registry_digest"})
    _write_json(REPAIR_ROOT / "ENGINEERING_REPAIR_EXCLUSION_REGISTRY.json", registry)
    if passed:
        authorization = {
            "schema_version": "driveclarify.rq2_t_cg.single_replacement_roster_authorization.v1",
            "status": "AUTHORIZED_SINGLE_REPLACEMENT_ROSTER_01",
            "quarantined_roster_digest": quarantine["roster_digest"],
            "quarantine_evidence_seal_sha256": _sha(PRIMARY_REPORT / "FULL_ROSTER_QUARANTINE_EVIDENCE_SEAL.json"),
            "engineering_repair_qualification_digest": result["record_digest"],
            "engineering_identity": identity, "engineering_seed": seed,
            "engineering_identity_permanently_excluded": True,
            "replacement_roster_use_count": 1,
            "additional_replacement_rosters_authorized": 0,
            "replacement_report_root": str((PRIMARY_REPORT / "REPLACEMENT_ROSTER_01").relative_to(ROOT)),
        }
        authorization["authorization_digest"] = canonical_sha256(authorization)
        _write_json(REPLACEMENT_AUTHORIZATION, authorization)
    print(json.dumps({"status": result["status"], "identity": identity, "seed": seed,
                      "integrity": integrity, "error": error}, sort_keys=True), flush=True)
    return result


def _exposure_add(cell: Mapping[str, Any], attempt: int, activation: Mapping[str, Any]) -> None:
    registry = _load(EXPOSURES)
    if any(row["cell_id"] == cell["cell_id"] for row in registry["exposures"]):
        return
    row = {"cell_id": cell["cell_id"], "attempt": attempt, "seed": cell["seed"], "scene_code": cell["scene_code"], "activation": activation}
    row["exposure_digest"] = canonical_sha256(row)
    registry["exposures"].append(row); registry["exposure_count"] = len(registry["exposures"])
    registry["registry_digest"] = canonical_sha256({key: value for key, value in registry.items() if key != "registry_digest"})
    _write_json(EXPOSURES, registry)


def _run_attempt(cell: Mapping[str, Any], attempt: int, wall_timeout: float) -> Mapping[str, Any]:
    cell_id = str(cell["cell_id"])
    output = REPORT / "NATIVE_TRACES" / cell_id / "attempt_{:02d}".format(attempt)
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("RQ2_T_CG_FORMAL_ATTEMPT_OUTPUT_NONEMPTY")
    output.mkdir(parents=True, exist_ok=True)
    scene = scene_by_code(str(cell["scene_code"]))
    route_receipt = static_route_admission(_route_path(cell["scene_code"]), scene)
    if route_receipt["status"] != "PASS_STATIC_FORMAL_ROUTE_ADMISSION" or route_receipt["native_route_sha256"] != cell["native_route_sha256"]:
        raise RuntimeError("RQ2_T_CG_FORMAL_ROUTE_DRIFT")
    reservation = {
        "schema_version": "driveclarify.rq2_t_cg.formal_attempt.v1", "cell_id": cell_id,
        "attempt": attempt, "scene_code": cell["scene_code"], "seed": cell["seed"],
        "reserved_at_unix_s": time.time(), "output": str(output.relative_to(ROOT)),
        "scientific_retry": False, "exposure_at_reservation": False,
    }
    reservation["record_digest"] = canonical_sha256(reservation); _append(ATTEMPTS, reservation)
    spec = _episode_spec(cell_id, cell["scene_code"], int(cell["seed"]))
    native.SIMLINGO_PROTECTED_DIFF_SHA256 = native._git_diff_sha256(native.SIMLINGO_ROOT)
    preflight = _repair_irrelevant_display_process_false_positive(native.native_preflight(spec, output, visualization=True))
    runtime = error = lease = None
    if preflight.get("status") == "PASS":
        try:
            with episode_backend.serial_gpu_lease() as lease_row:
                lease = lease_row
                runtime = native.run_native_episode(
                    spec, output, command=native.build_command(spec, output),
                    environment=_environment(spec, output, cell), cwd=native.SIMLINGO_ROOT,
                    wall_timeout_seconds=wall_timeout, wall_timeout_reason="RQ2_T_CG_FORMAL_PROGRESS_AWARE_WALL_CONTAINMENT",
                    cleanup_writer=lambda value: _write_json(output / "CLEANUP_RECEIPT.json", value),
                )
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
    activation = _load(output / "FORMAL_ACTIVATION_RECEIPT.json", {})
    exposed = activation.get("formal_scientific_exposure") is True
    if exposed:
        _exposure_add(cell, attempt, activation)
    scenario = _load(output / "FORMAL_SCENARIO_RECEIPT.json", {})
    cleanup = _load(output / "CLEANUP_RECEIPT.json", {})
    builder = None
    terminal = scenario.get("terminal") if isinstance(scenario, Mapping) else None
    if error is None and exposed and isinstance(terminal, Mapping) and terminal.get("state") == "NATURAL_HORIZON_OBSERVED":
        try:
            builder = build_formal_episode(output, cell)
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc), "stage": "POSTTRACE_FORMAL_BUILDER"}
    integrity = {
        "preflight_pass": preflight.get("status") == "PASS", "runtime_created": isinstance(runtime, Mapping),
        "cleanup_pass": cleanup.get("status") == "PASS", "formal_exposure_observed": exposed,
        "scenario_identity": scenario.get("formal_scene_digest") == cell["formal_scene_digest"],
        "natural_horizon": isinstance(terminal, Mapping) and terminal.get("state") == "NATURAL_HORIZON_OBSERVED",
        "commitment_observed": isinstance(scenario.get("commitment"), Mapping) and scenario["commitment"].get("state") == "COMMITMENT_OBSERVED",
        "all_event_starts": len(scenario.get("event_start_rows", ())) == scenario.get("expected_event_count"),
        "event_windows_entered": all(row.get("window_entry_observed") is True for row in scenario.get("event_start_rows", ())),
        "native_trace_present": (output / "post_hoc_world_state.jsonl").is_file(),
        "builder_complete": isinstance(builder, Mapping),
        "paired_source_identity": isinstance(builder, Mapping) and builder.get("same_source_identity_all_views") is True,
        "zero_added_compute_control": isinstance(builder, Mapping) and all(builder.get(key) == 0 for key in (
            "observer_added_vla_forwards", "duplicate_candidate_computations", "PID_controller_changes",
            "second_control_writer", "RoutePlanner_mutations", "UKF_mutations", "command_history_mutations",
            "runtime_true_intent_reads", "online_ask_count",
        )),
    }
    valid = error is None and all(integrity.values())
    result = {
        "schema_version": "driveclarify.rq2_t_cg.formal_execution.v1",
        "status": "TERMINATED_VALID" if valid else "INVALID_ATTEMPT",
        "cell_id": cell_id, "attempt": attempt, "scene_code": cell["scene_code"], "seed": cell["seed"],
        "exposed": exposed, "preflight": preflight, "runtime": runtime, "error": error,
        "gpu_lease": lease, "integrity": integrity, "builder": builder,
        "scientific_retry": False, "formal_scene_digest": cell["formal_scene_digest"],
    }
    result["record_digest"] = canonical_sha256(result)
    _write_json(output / "FORMAL_EXECUTION_RESULT.json", result); _append(EXECUTIONS, result)
    termination = {
        "cell_id": cell_id, "attempt": attempt, "status": result["status"], "exposed": exposed,
        "terminal": terminal, "censoring_category": "NONE_VALID_COMPLETE" if valid else "INVALID_ATTEMPT",
        "error": error,
    }
    termination["record_digest"] = canonical_sha256(termination); _append(TERMINATIONS, termination)
    if valid:
        for directory, filename in (
            ("EVENT_RECEIPTS", "FORMAL_SCENARIO_RECEIPT.json"),
            ("COMMITMENT_RECEIPTS", "FORMAL_COMMITMENT_RECEIPT.json"),
            ("CLEANUP_RECEIPTS", "CLEANUP_RECEIPT.json"),
            ("PAIRED_VIEW_ROWS", "FORMAL_PAIRED_VIEWS.jsonl"),
        ):
            target = REPORT / directory; target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(output / filename), str(target / (cell_id + Path(filename).suffix)))
    return result


def run_campaign(wall_timeout: float) -> Mapping[str, Any]:
    if ROSTER_INDEX == 1:
        authorization = _load(REPLACEMENT_AUTHORIZATION, {})
        if (
            authorization.get("status") != "AUTHORIZED_SINGLE_REPLACEMENT_ROSTER_01"
            or authorization.get("replacement_roster_use_count") != 1
            or authorization.get("additional_replacement_rosters_authorized") != 0
        ):
            raise RuntimeError("FORMAL_EXECUTION_INTEGRITY_NOT_CLOSED")
    if (REPORT / "FULL_ROSTER_QUARANTINE.json").exists() or any(
        row.get("exposed") is True and row.get("status") != "TERMINATED_VALID"
        for row in _jsonl(EXECUTIONS)
    ):
        raise RuntimeError("FORMAL_EXECUTION_INTEGRITY_NOT_CLOSED")
    roster = _load(REPORT / "FORMAL_48_CELL_ROSTER.json")
    source = _load(REPORT / "SOURCE_FREEZE_RECEIPT.json")
    for row in source["source_files"]:
        if _sha(ROOT / row["path"]) != row["sha256"]:
            raise RuntimeError("SCIENTIFIC_CONTRACT_CHANGE_REQUIRED")
    valid_existing = {row["cell_id"] for row in _jsonl(EXECUTIONS) if row.get("status") == "TERMINATED_VALID"}
    for cell in roster["cells"]:
        if cell["cell_id"] in valid_existing:
            continue
        prior = [row for row in _jsonl(ATTEMPTS) if row.get("cell_id") == cell["cell_id"]]
        if any(row.get("exposure_at_reservation") for row in prior):
            raise RuntimeError("FORMAL_EXECUTION_INTEGRITY_NOT_CLOSED")
        attempt = len(prior) + 1
        while attempt <= 3:
            print(json.dumps({"event": "CELL_START", "sequence": cell["run_sequence"], "cell": cell["cell_id"], "attempt": attempt, "completed": len(valid_existing)}, sort_keys=True), flush=True)
            result = _run_attempt(cell, attempt, wall_timeout)
            print(json.dumps({"event": "CELL_END", "cell": cell["cell_id"], "attempt": attempt, "status": result["status"], "exposed": result["exposed"], "error": result["error"]}, sort_keys=True), flush=True)
            if result["status"] == "TERMINATED_VALID":
                valid_existing.add(cell["cell_id"]); break
            if result["exposed"]:
                quarantine = {"status": "QUARANTINED_FULL_ROSTER_POST_EXPOSURE_INTEGRITY_DEFECT", "cell": cell["cell_id"], "attempt": attempt, "roster_digest": roster["roster_digest"], "replacement_roster_use_count": ROSTER_INDEX, "additional_replacement_rosters_authorized": 0 if ROSTER_INDEX == 1 else 1}
                _write_json(REPORT / "FULL_ROSTER_QUARANTINE.json", quarantine)
                raise RuntimeError("FORMAL_EXECUTION_INTEGRITY_NOT_CLOSED")
            attempt += 1
        if cell["cell_id"] not in valid_existing:
            raise RuntimeError("FORMAL_EXECUTION_INTEGRITY_NOT_CLOSED")
    results = [row["builder"] for row in _jsonl(EXECUTIONS) if row.get("status") == "TERMINATED_VALID"]
    if len(results) != 48:
        raise RuntimeError("FORMAL_EXECUTION_INTEGRITY_NOT_CLOSED")
    fields = sorted({key for row in results for key in row if key != "rule_results" and not isinstance(row.get(key), (dict, list))})
    with (REPORT / "EPISODE_LEVEL_PRIMARY_TABLE.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in results: writer.writerow({key: row.get(key) for key in fields})
    _write_json(REPORT / "EPISODE_LEVEL_PRIMARY_TABLE.json", {"primary_unit": "scene × seed episode", "row_count": 48, "rows": results, "table_digest": canonical_sha256(results)})
    summary = {"status": "PASS_FORMAL_EXECUTION_COMPLETE", "planned_cells": 48, "valid_episodes": 48, "exposed_cells": _load(EXPOSURES)["exposure_count"], "attempts": len(_jsonl(ATTEMPTS)), "scientific_retries": 0, "roster_index": ROSTER_INDEX, "replacement_roster_use_count": ROSTER_INDEX}
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preseed", "materialize", "repair-qualify", "run"))
    parser.add_argument("--wall-timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()
    if args.command == "preseed": queue_and_environment_gate()
    elif args.command == "materialize": materialize_seeds_and_roster()
    elif args.command == "repair-qualify":
        result = qualify_engineering_repair(args.wall_timeout_seconds)
        if not str(result["status"]).startswith("PASS_"):
            return 2
    else:
        run_campaign(args.wall_timeout_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
