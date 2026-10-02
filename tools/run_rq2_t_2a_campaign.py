#!/usr/bin/env python3
"""Pre-seed gate, seed/roster freeze, and serial 64-cell 2A campaign."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq2_t_formal_experiment_2a_v2"
ACCEPTED = ROOT / "reports/driveclarify_rq2_t_scene_certification_and_tfixed_calibration_v1"
ADDENDUM = ROOT / "reports/driveclarify_rq2_t_2a_contract_addendum_and_owner_qualification_v1"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_paper_mvp_stage6b import backend as native  # noqa: E402
from driveclarify_rq2_t.experiment_2a import (  # noqa: E402
    OWNER_ID,
    load_owner_bindings,
)
from tools.run_rq2_t_2a_native import (  # noqa: E402
    CUSTOM_SCENES,
    SCENES,
    binding,
    canonical,
    certificate,
    run_episode,
    sha256,
)


SCENE_ORDER = (
    "REF-01",
    "LMK-01",
    "ORD-01",
    "USC-02",
    "REF-02",
    "LMK-02",
    "ORD-02",
    "USC-03",
)
FRESHNESS_EXTENSIONS = {
    ".csv",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def command(
    args: Iterable[str], *, cwd: Path = ROOT, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        tuple(args),
        cwd=str(cwd),
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


def route_progress_to_boundary(scene: str) -> Mapping[str, Any]:
    path = Path(SCENES[scene]["route"])
    points = [
        tuple(float(row.attrib.get(key, 0.0)) for key in ("x", "y", "z"))
        for row in ET.parse(path).getroot().findall(".//waypoints/position")
    ]
    target = tuple(float(value) for value in binding(scene)["boundary_world_xyz"])
    index = min(range(len(points)), key=lambda item: math.dist(points[item], target))
    progress = sum(math.dist(points[item - 1], points[item]) for item in range(1, index + 1))
    return {
        "route_path": str(path.relative_to(ROOT)),
        "route_asset_sha256": sha256(path),
        "waypoint_count": len(points),
        "nearest_boundary_index": index,
        "nearest_boundary_distance_m": math.dist(points[index], target),
        "precommitment_route_progress_m": progress,
        "minimum_precommitment_distance_m": binding(scene)[
            "minimum_precommitment_distance_m"
        ],
    }


def source_freeze() -> Mapping[str, Any]:
    accepted_freeze = load(ADDENDUM / "SOURCE_FREEZE_RECEIPT.json")
    protected = {}
    for key, row in accepted_freeze["protected_sources"].items():
        path = Path(row["path"])
        if not path.is_absolute():
            path = (Path("/home/buaa/wrh") / path) if path.parts[0] == "simlingo" else (ROOT / path)
        protected[key] = {
            "path": str(path),
            "expected": row["expected"],
            "observed": sha256(path),
            "match": sha256(path) == row["expected"],
        }
    sources = [
        ROOT / "driveclarify_rq2_t/experiment_2a.py",
        ROOT / "driveclarify_rq2_t/measurement.py",
        ROOT / "driveclarify_rq2_t/observer.py",
        ROOT / "driveclarify_rq2_t/owner_bindings_v1.json",
        ROOT / "driveclarify_rq2_t/formal_scenario.py",
        ROOT / "driveclarify_persistent_ambiguity_runtime_v1/runtime.py",
        ROOT / "tools/run_rq2_t_2a_native.py",
        ROOT / "tools/run_rq2_t_2a_campaign.py",
        ROOT / "tools/analyze_rq2_t_2a.py",
        ROOT / "tools/build_rq2_t_2a_formal_routes.py",
        ROOT / "tools/quarantine_rq2_t_2a_roster.py",
        Path("/home/buaa/wrh/simlingo/team_code/driveclarify_probe_hook.py"),
        Path(
            "/home/buaa/wrh/simlingo/Bench2Drive/leaderboard/leaderboard/scenarios/scenario_manager.py"
        ),
    ]
    route_assets = {scene: sha256(Path(SCENES[scene]["route"])) for scene in SCENE_ORDER}
    value = {
        "schema_version": "driveclarify.rq2_t.formal_2a.source_freeze.v2",
        "generated_at_utc": utc_now(),
        "entry_head": command(("git", "rev-parse", "HEAD")).stdout.strip(),
        "protected_sources": protected,
        "protected_sources_all_match": all(row["match"] for row in protected.values()),
        "repaired_preseed_sources": {
            str(path): sha256(path) for path in sources
        },
        "route_asset_sha256": route_assets,
        "simlingo_head": native._git_head(native.SIMLINGO_ROOT),
        "simlingo_worktree_diff_sha256": native._git_diff_sha256(native.SIMLINGO_ROOT),
        "tracked_diff_sha256": hashlib.sha256(
            command(("git", "diff", "--binary")).stdout.encode("utf-8")
        ).hexdigest(),
        "staged_diff_sha256": hashlib.sha256(
            command(("git", "diff", "--cached", "--binary")).stdout.encode("utf-8")
        ).hexdigest(),
        "scientific_seed_count_at_freeze": 0,
        "preseed_engineering_repairs_semantic_scope": (
            "IMPLEMENT_FROZEN_COMMITMENT_TERMINAL_AND_PRESERVE_UNAVAILABLE_EVIDENCE_AS_UNKNOWN"
        ),
        "scientific_contract_changed": False,
    }
    value["payload_digest"] = canonical(value)
    atomic_json(REPORT / "SOURCE_FREEZE_RECEIPT.json", value)
    return value


def gate() -> Mapping[str, Any]:
    if (REPORT / "RQ2_T_2A_FRESH_SEED_ROSTER.json").exists():
        raise RuntimeError("PRESEED_GATE_CANNOT_RERUN_AFTER_SEED_MATERIALIZATION")
    test_env = dict(os.environ)
    test_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    test = command(
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_rq2_t_experiment_2a_addendum.py",
            "tests/rq2_t_temporal_evidence/test_temporal_evidence.py",
        ),
        env=test_env,
    )
    owner = load_owner_bindings()
    cert_rows = {}
    route_rows = {}
    for scene in SCENE_ORDER:
        cert = certificate(scene)
        # The accepted certification package predates the newer UTF-8
        # canonical helper and used json.dumps' default ensure_ascii=True.
        # Preserve that exact accepted digest domain (materially relevant for
        # LMK-02, whose prose contains a Unicode em dash).
        cert_digest = hashlib.sha256(
            json.dumps(cert, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cert_rows[scene] = {
            "observed": cert_digest,
            "expected": binding(scene)["scene_certificate_sha256"],
            "pass": cert_digest == binding(scene)["scene_certificate_sha256"],
            "scenario_configuration_match": cert["scenario_configuration_sha256"]
            == binding(scene)["scenario_configuration_sha256"],
        }
        route = dict(route_progress_to_boundary(scene))
        route["pass"] = bool(
            route["precommitment_route_progress_m"] + 1e-3
            >= route["minimum_precommitment_distance_m"]
            and binding(scene)["target_speed_mps"] * 35.0
            >= binding(scene)["minimum_precommitment_distance_m"]
            and route["nearest_boundary_distance_m"] <= 0.75
        )
        route_rows[scene] = route
    owner_source = (ROOT / "driveclarify_rq2_t/experiment_2a.py").read_text(
        encoding="utf-8"
    )
    owner_class = owner_source[
        owner_source.index("class SharedPrefixLongitudinalOwnerV1") : owner_source.index(
            "def _sample_polyline"
        )
    ]
    disk_free_gb = shutil.disk_usage(ROOT).free / (1024**3)
    gpu = command(
        (
            "nvidia-smi",
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
        )
    )
    gpu_free_mb = int(gpu.stdout.strip().splitlines()[0]) if gpu.returncode == 0 else 0
    processes = command(
        ("bash", "-lc", "ps -eo args= | rg 'CarlaUE4-Linux|leaderboard_evaluator' | rg -v 'rg ' || true")
    ).stdout.strip()
    engineering = {
        key: load(
            REPORT
            / "ENGINEERING_PROBES"
            / key
            / "RQ2_T_2A_ATTEMPT_RESULT.json",
            {},
        ).get("status")
        for key in (
            "ENG-RQ2T-2A-V2-NATIVE-003",
            "ENG-RQ2T-2A-V2-NATIVE-005",
            "ENG-RQ2T-2A-V2-NATIVE-006",
            "ENG-RQ2T-2A-V2-NATIVE-007",
            "ENG-RQ2T-2A-V2-NATIVE-008",
            "ENG-RQ2T-2A-V2-NATIVE-009",
        )
    }
    analysis_qualification = load(
        REPORT / "ANALYSIS_ENGINEERING_QUALIFICATION_RECEIPT.json", {}
    )
    freeze = source_freeze()
    checks = {
        "entry_head_source_freeze": freeze["protected_sources_all_match"],
        "accepted_addendum_closed": load(ADDENDUM / "FINAL_VALIDATION_RECEIPT.json")[
            "status"
        ]
        == "PASS_RQ2_T_2A_ADDENDUM_CLOSED_READY_FOR_FINAL_SEED_AUTHORIZATION",
        "all_eight_owner_bindings": set(owner["bindings"]) == set(SCENE_ORDER)
        and owner["owner_id"] == OWNER_ID,
        "all_eight_scene_certificates": all(
            row["pass"] and row["scenario_configuration_match"]
            for row in cert_rows.values()
        ),
        "thirty_five_second_viability": all(row["pass"] for row in route_rows.values()),
        "natural_terminal_contract": "PY_TREES_STATUS_SUCCESS" in owner_source
        and "PY_TREES_STATUS_FAILURE" in owner_source,
        "censoring_table": "opportunity_duration_simulation_s=None" in owner_source
        or "duration = None" in owner_source,
        "ttcmt_strata": all(token in owner_source for token in ("3.0", "1.20")),
        "observer_passivity": all(
            token not in (ROOT / "driveclarify_rq2_t/observer.py").read_text(encoding="utf-8")
            for token in ("apply_control", "VehicleControl(")
        ),
        "single_control_owner": owner["control_authority"]["plan_source_owner"]
        == OWNER_ID
        and owner["control_authority"]["observer_control_writes"] == 0,
        "checkpoint": protected["vla_checkpoint"]["match"]
        if (protected := freeze["protected_sources"])
        else False,
        "oracle_firewall": all(
            token not in owner_class
            for token in (
                "QueryNecessityGold",
                "T_ACCUM",
                "T_FIXED",
                "T_EARLY",
                "T_FULL",
            )
        ),
        "environment_capacity": disk_free_gb >= 30.0 and gpu_free_mb >= 9000,
        "exclusion_registries": (
            REPORT / "ENGINEERING_SEED_EXCLUSION_REGISTRY.json"
        ).is_file(),
        "focused_tests": test.returncode == 0 and "53 passed" in test.stdout,
        "native_fixture_qualification": engineering[
            "ENG-RQ2T-2A-V2-NATIVE-003"
        ]
        == "PASS_VALID_EPISODE",
        "native_custom_scene_qualification": engineering[
            "ENG-RQ2T-2A-V2-NATIVE-005"
        ]
        == "PASS_VALID_EPISODE",
        "postquarantine_progress_watchdog_qualification": engineering[
            "ENG-RQ2T-2A-V2-NATIVE-006"
        ]
        == "PASS_VALID_EPISODE",
        "postquarantine_unknown_preservation_qualification": engineering[
            "ENG-RQ2T-2A-V2-NATIVE-007"
        ]
        == "PASS_VALID_EPISODE",
        "postquarantine_p1_failure_unknown_preservation_qualification": engineering[
            "ENG-RQ2T-2A-V2-NATIVE-008"
        ]
        == "PASS_VALID_EPISODE",
        "postquarantine_analysis_ingest_qualification": (
            engineering["ENG-RQ2T-2A-V2-NATIVE-009"] == "PASS_VALID_EPISODE"
            and analysis_qualification.get("status")
            == "PASS_ANALYSIS_INGEST_ENGINEERING_QUALIFICATION"
            and analysis_qualification.get("qualification", {}).get(
                "temporal_rows_loaded"
            )
            == 98
            and analysis_qualification.get("qualification", {}).get(
                "expanded_evidence_rows"
            )
            == 882
            and analysis_qualification.get("qualification", {}).get(
                "gam_status_counts"
            )
            == {"NOT_IDENTIFIABLE_CONSTANT_RESPONSE": 9}
        ),
        "no_residual_native_process": not processes,
    }
    value = {
        "schema_version": "driveclarify.rq2_t.formal_2a.preseed_gate.v2",
        "generated_at_utc": utc_now(),
        "status": "PASS_FINAL_PRESEED_GATE" if all(checks.values()) else "BLOCKED_PRESEED_GATE",
        "checks": checks,
        "test_output": test.stdout[-4000:],
        "scene_certificates": cert_rows,
        "route_viability": route_rows,
        "engineering_qualifications": engineering,
        "capacity": {"disk_free_gb": disk_free_gb, "gpu_free_mb": gpu_free_mb},
        "scientific_seed_count": 0,
        "scientific_exposure_count": 0,
        "source_freeze_digest": freeze["payload_digest"],
    }
    value["payload_digest"] = canonical(value)
    atomic_json(REPORT / "PREEXECUTION_GATE_RECEIPT.json", value)
    atomic_json(REPORT / "FINAL_PREEXPOSURE_GATE_RECEIPT.json", value)
    entry = {
        "schema_version": "driveclarify.rq2_t.formal_2a.entry_integrity.v2",
        "entry_head": freeze["entry_head"],
        "accepted_scene_roster_digest": load(
            ACCEPTED / "FINAL_CERTIFIED_SCENE_ROSTER.json"
        )["roster_digest"],
        "accepted_t_fixed_freeze_digest": load(
            ACCEPTED / "FINAL_T_FIXED_FREEZE.json"
        )["freeze_digest"],
        "accepted_addendum_validation_digest": load(
            ADDENDUM / "FINAL_VALIDATION_RECEIPT.json"
        )["payload_digest"],
        "source_freeze_digest": freeze["payload_digest"],
        "preseed_gate_digest": value["payload_digest"],
        "formal_seed_count": 0,
        "scientific_exposure_count": 0,
        "status": "PASS" if all(checks.values()) else "FAIL",
    }
    entry["payload_digest"] = canonical(entry)
    atomic_json(REPORT / "ENTRY_INTEGRITY_RECEIPT.json", entry)
    (REPORT / "PREEXECUTION_GATE_REPORT.md").write_text(
        "# Final pre-seed gate\n\nStatus: `{}`.\n\n".format(value["status"])
        + "\n".join(
            "- {}: {}".format(key, "PASS" if passed else "FAIL")
            for key, passed in checks.items()
        )
        + "\n",
        encoding="utf-8",
    )
    return value


def freshness_files() -> list[Path]:
    files = []
    for root in (ROOT, Path("/home/buaa/wrh/simlingo")):
        for path in root.rglob("*"):
            # Filter by lexical suffix before stat: simulator caches can expose
            # inaccessible lock entries that are never in the text scan domain.
            if path.suffix.lower() not in FRESHNESS_EXTENSIONS:
                continue
            if any(part in {".git", "__pycache__", ".cache"} for part in path.parts):
                continue
            try:
                if path.is_file() and path.stat().st_size <= 100 * 1024 * 1024:
                    files.append(path)
            except OSError:
                continue
    return sorted(set(files))


def materialize() -> Mapping[str, Any]:
    gate_receipt = load(REPORT / "PREEXECUTION_GATE_RECEIPT.json", {})
    if gate_receipt.get("status") != "PASS_FINAL_PRESEED_GATE":
        raise RuntimeError("FORMAL_SEED_MATERIALIZATION_GATE_NOT_PASS")
    targets = (
        REPORT / "RQ2_T_2A_FRESH_SEED_ROSTER.json",
        REPORT / "RQ2_T_2A_FORMAL_ROSTER.json",
        REPORT / "RQ2_T_2A_RUN_ORDER.json",
    )
    if any(path.exists() for path in targets):
        raise RuntimeError("FORMAL_SEEDS_ALREADY_MATERIALIZED")
    # Enumerate the complete readable text domain before consuming entropy so
    # an infrastructure traversal error cannot strand an unmaterialized draw.
    files = freshness_files()
    seeds = [int.from_bytes(os.urandom(4), "big", signed=False) for _ in range(8)]
    if len(set(seeds)) != 8:
        raise RuntimeError("CSPRNG_EIGHT_DRAW_COLLISION_ABORT_NO_REDRAW")
    hits = {seed: [] for seed in seeds}
    scanned_bytes = 0
    for path in files:
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        scanned_bytes += len(payload)
        for seed in seeds:
            token = str(seed).encode("ascii")
            if re.search(rb"(?<![0-9])" + re.escape(token) + rb"(?![0-9])", payload):
                hits[seed].append(str(path))
    if any(hits.values()):
        raise RuntimeError("CSPRNG_DRAW_PRIOR_OCCURRENCE_ABORT_NO_REDRAW:" + json.dumps(hits))
    proof = {
        "schema_version": "driveclarify.rq2_t.formal_2a.seed_freshness.v2",
        "generated_at_utc": utc_now(),
        "procedure": "EXACTLY_EIGHT_UINT32_BIG_ENDIAN_DRAWS_FROM_OS_URANDOM",
        "draw_count": 8,
        "redraw_count": 0,
        "prior_aborted_unmaterialized_entropy_values": 8,
        "prior_aborted_values_were_seed_identities": False,
        "prior_aborted_values_recoverable": False,
        "prior_aborted_transaction_receipt": "PREMATERIALIZATION_ENTROPY_INCIDENT.json",
        "scan_completed_before_materialization": True,
        "scan_roots": [str(ROOT), "/home/buaa/wrh/simlingo"],
        "exact_token_pattern": "(?<![0-9])SEED(?![0-9])",
        "scanned_file_count": len(files),
        "scanned_bytes": scanned_bytes,
        "prior_occurrences": {str(seed): hits[seed] for seed in seeds},
        "all_eight_fresh": all(not value for value in hits.values()),
        "seeds": seeds,
    }
    proof["payload_digest"] = canonical(proof)
    atomic_json(REPORT / "RQ2_T_2A_FRESHNESS_PROOF.json", proof)
    seed_roster = {
        "schema_version": "driveclarify.rq2_t.formal_2a.seed_roster.v2",
        "generated_at_utc": utc_now(),
        "permanent_identity": "RQ2_T_2A_DEV",
        "seed_count": 8,
        "seeds": seeds,
        "forbidden_future_roles": ["CALIBRATION", "ENGINEERING", "RQ2_T_2B", "TEST"],
        "freshness_proof_digest": proof["payload_digest"],
    }
    seed_roster["roster_digest"] = canonical(seed_roster)
    atomic_json(REPORT / "RQ2_T_2A_FRESH_SEED_ROSTER.json", seed_roster)
    atomic_json(REPORT / "RQ2_T_2A_DEV_SEEDS.json", seed_roster)
    cells = []
    order = 0
    # Eight balanced blocks: every block contains all scenes and all seeds;
    # scene and seed positions rotate independently without outcome access.
    for block in range(8):
        scene_rotation = SCENE_ORDER[block:] + SCENE_ORDER[:block]
        for position, scene in enumerate(scene_rotation):
            seed_index = (position + 2 * block) % 8
            seed = seeds[seed_index]
            order += 1
            cell_id = "RQ2T-2A-{:03d}-{}-S{:02d}".format(
                order, scene, seed_index + 1
            )
            cells.append(
                {
                    "execution_order": order,
                    "balanced_block": block + 1,
                    "position_in_block": position + 1,
                    "cell_id": cell_id,
                    "scene_key": scene,
                    "scene_id": binding(scene)["scene_id"],
                    "ambiguity_family": certificate(scene)["ambiguity_family"],
                    "seed_id": "RQ2_T_2A_DEV_S{:02d}".format(seed_index + 1),
                    "seed": seed,
                    "route_sha256": binding(scene)["route_sha256"],
                    "route_asset_sha256": sha256(Path(SCENES[scene]["route"])),
                    "scenario_configuration_sha256": binding(scene)[
                        "scenario_configuration_sha256"
                    ],
                    "owner_id": OWNER_ID,
                    "output_path": str(
                        (
                            REPORT
                            / "LONGITUDINAL_EVIDENCE"
                            / cell_id
                        ).relative_to(ROOT)
                    ),
                }
            )
    if len(cells) != 64 or len({(row["scene_key"], row["seed"]) for row in cells}) != 64:
        raise RuntimeError("FORMAL_ROSTER_NOT_EXACT_EIGHT_BY_EIGHT")
    roster = {
        "schema_version": "driveclarify.rq2_t.formal_2a.roster.v2",
        "generated_at_utc": utc_now(),
        "owner_id": OWNER_ID,
        "max_simulated_horizon_s": 35.0,
        "evidence_schema": "driveclarify.rq2_t.temporal_observation.v2",
        "denominator_rule": "VALID_ENGINEERING_AND_TERMINAL_CLASSIFICATION_DENOMINATOR_ELIGIBLE_TRUE",
        "planned_cell_count": 64,
        "scene_count": 8,
        "seed_count": 8,
        "cells": cells,
    }
    roster["roster_digest"] = canonical(roster)
    atomic_json(REPORT / "RQ2_T_2A_FORMAL_ROSTER.json", roster)
    run_order = {
        "schema_version": "driveclarify.rq2_t.formal_2a.run_order.v2",
        "procedure": "EIGHT_BLOCK_DUAL_CYCLIC_BALANCE_FROZEN_BEFORE_EXPOSURE",
        "outcome_adaptive_reordering": False,
        "cells": [
            {
                key: row[key]
                for key in (
                    "execution_order",
                    "balanced_block",
                    "position_in_block",
                    "cell_id",
                    "scene_key",
                    "seed_id",
                    "seed",
                    "output_path",
                )
            }
            for row in cells
        ],
    }
    run_order["run_order_digest"] = canonical(run_order)
    atomic_json(REPORT / "RQ2_T_2A_RUN_ORDER.json", run_order)
    exposure = {
        "schema_version": "driveclarify.rq2_t.formal_2a.exposure_registry.v2",
        "roster_digest": roster["roster_digest"],
        "planned_cells": 64,
        "exposed_cells": 0,
        "terminated_cells": 0,
        "cells": {row["cell_id"]: {"state": "FROZEN_UNEXPOSED"} for row in cells},
    }
    atomic_json(REPORT / "SCIENTIFIC_EXPOSURE_REGISTRY.json", exposure)
    for name in (
        "ATTEMPT_LEDGER.jsonl",
        "SCIENTIFIC_EXECUTION_LEDGER.jsonl",
        "SCIENTIFIC_TERMINATION_LEDGER.jsonl",
    ):
        (REPORT / name).touch(exist_ok=False)
    return roster


def campaign() -> int:
    roster = load(REPORT / "RQ2_T_2A_FORMAL_ROSTER.json", {})
    exposure_path = REPORT / "SCIENTIFIC_EXPOSURE_REGISTRY.json"
    exposure = load(exposure_path, {})
    if roster.get("planned_cell_count") != 64 or exposure.get("roster_digest") != roster.get(
        "roster_digest"
    ):
        raise RuntimeError("FORMAL_ROSTER_OR_EXPOSURE_REGISTRY_INVALID")
    for cell in roster["cells"]:
        cell_id = cell["cell_id"]
        state = exposure["cells"][cell_id]["state"]
        if state in {"TERMINATED_VALID", "TERMINATED_SCIENTIFIC_NONCOMPLETION"}:
            continue
        if state != "FROZEN_UNEXPOSED":
            raise RuntimeError("CAMPAIGN_RESUME_STATE_NOT_SAFE:" + cell_id + ":" + state)
        attempt = 1
        while attempt <= 3:
            attempt_dir = REPORT / "LONGITUDINAL_EVIDENCE" / cell_id / "attempt_{:02d}".format(attempt)
            reservation = {
                "event": "SCIENTIFIC_ATTEMPT_RESERVED",
                "at_utc": utc_now(),
                "cell_id": cell_id,
                "scene_key": cell["scene_key"],
                "seed": cell["seed"],
                "attempt": attempt,
                "preagent_infrastructure_retry": attempt > 1,
                "scientific_retry": False,
                "output_path": str(attempt_dir.relative_to(ROOT)),
            }
            append_jsonl(REPORT / "ATTEMPT_LEDGER.jsonl", reservation)
            exposure["cells"][cell_id] = {
                "state": "RESERVED_OR_RUNNING",
                "attempt": attempt,
                "reserved_at_utc": reservation["at_utc"],
            }
            atomic_json(exposure_path, exposure)
            result = run_episode(
                cell["scene_key"],
                int(cell["seed"]),
                attempt_dir,
                engineering=False,
                episode_id=cell_id,
                wall_timeout_seconds=604800.0,
            )
            exposed = bool(result.get("scientific_exposure_observed"))
            append_jsonl(
                REPORT / "SCIENTIFIC_EXECUTION_LEDGER.jsonl",
                {
                    "event": "SCIENTIFIC_ATTEMPT_COMPLETED",
                    "at_utc": utc_now(),
                    "cell_id": cell_id,
                    "attempt": attempt,
                    "status": result.get("status"),
                    "scientific_exposure_observed": exposed,
                    "attempt_digest": result.get("attempt_digest"),
                },
            )
            if result.get("status") == "PASS_VALID_EPISODE":
                exposure["cells"][cell_id] = {
                    "state": "TERMINATED_VALID",
                    "attempt": attempt,
                    "attempt_digest": result["attempt_digest"],
                    "terminal_event": result.get("terminal_event"),
                }
                exposure["exposed_cells"] = sum(
                    row.get("state", "").startswith("TERMINATED")
                    for row in exposure["cells"].values()
                )
                exposure["terminated_cells"] = exposure["exposed_cells"]
                atomic_json(exposure_path, exposure)
                append_jsonl(
                    REPORT / "SCIENTIFIC_TERMINATION_LEDGER.jsonl",
                    {
                        "event": "VALID_SCIENTIFIC_TERMINAL",
                        "at_utc": utc_now(),
                        "cell_id": cell_id,
                        "attempt": attempt,
                        "terminal_event": result.get("terminal_event"),
                        "h2_primary_category": result.get("episode_summary", {}).get(
                            "h2_primary_category"
                        ),
                    },
                )
                break
            if not exposed and attempt < 3:
                append_jsonl(
                    REPORT / "ATTEMPT_LEDGER.jsonl",
                    {
                        "event": "LEGAL_PREAGENT_INFRASTRUCTURE_RETRY_AUTHORIZED",
                        "at_utc": utc_now(),
                        "cell_id": cell_id,
                        "failed_attempt": attempt,
                        "zero_scientific_exposure": True,
                    },
                )
                exposure["cells"][cell_id] = {"state": "FROZEN_UNEXPOSED"}
                atomic_json(exposure_path, exposure)
                attempt += 1
                continue
            exposure["cells"][cell_id] = {
                "state": "POSTEXPOSURE_ENGINEERING_DEFECT" if exposed else "PREAGENT_RETRIES_EXHAUSTED",
                "attempt": attempt,
                "attempt_digest": result.get("attempt_digest"),
            }
            atomic_json(exposure_path, exposure)
            receipt = {
                "schema_version": "driveclarify.rq2_t.formal_2a.postexposure_defect.v2",
                "status": exposure["cells"][cell_id]["state"],
                "cell_id": cell_id,
                "attempt": attempt,
                "scientific_exposure_observed": exposed,
                "required_action": (
                    "QUARANTINE_COMPLETE_ROSTER_BEFORE_ANY_REPAIR"
                    if exposed
                    else "ENVIRONMENT_PHYSICALLY_INCAPABLE_AFTER_THREE_PREAGENT_ATTEMPTS"
                ),
            }
            receipt["payload_digest"] = canonical(receipt)
            atomic_json(REPORT / "POSTEXPOSURE_ENGINEERING_DEFECT_RECEIPT.json", receipt)
            return 3
    exposure["status"] = "ALL_64_CELLS_TERMINATED_VALID"
    exposure["completed_at_utc"] = utc_now()
    atomic_json(exposure_path, exposure)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("gate", "materialize", "campaign"))
    args = parser.parse_args()
    if args.command == "gate":
        value = gate()
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if value["status"] == "PASS_FINAL_PRESEED_GATE" else 2
    if args.command == "materialize":
        value = materialize()
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return campaign()


if __name__ == "__main__":
    raise SystemExit(main())
