#!/usr/bin/env python3
"""Run one frozen V2 negative control on native visible CARLA."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_decision_evidence_v2 import FEATURE_FLAG as V2_FEATURE_FLAG  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from driveclarify_persistent_ambiguity_runtime_v1.runtime import FEATURE_FLAG as V1_FEATURE_FLAG  # noqa: E402
from driveclarify_v2_negative_control_reconstruction_v1.contracts import scenario  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402

REPORT = ROOT / "reports/driveclarify_v2_negative_control_reconstruction_and_validation_v1"
MANIFEST = REPORT / "TRAIN_V2_NEGATIVE_CONTROL_MANIFEST.json"
FREEZE = REPORT / "PREFREEZE_HASH_RECEIPT.json"
ARTIFACT = ROOT / "artifacts/driveclarify_v2_negative_control_reconstruction_and_validation_v1"
SCENARIO_ROOT = ROOT / "driveclarify_v2_negative_control_reconstruction_v1/scenario_root"
LIVE_MARKER = REPORT / "LIVE_EXECUTION_STARTED"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def manifest_entry(scenario_id: str) -> Mapping[str, Any]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    if hashlib.sha256(MANIFEST.read_bytes()).hexdigest() != freeze["manifest_file_sha256"]:
        raise RuntimeError("V2_NEGATIVE_CONTROL_MANIFEST_FILE_HASH_MISMATCH")
    if hashlib.sha256(canonical(value["scenarios"])).hexdigest() != value["manifest_payload_sha256"]:
        raise RuntimeError("V2_NEGATIVE_CONTROL_MANIFEST_PAYLOAD_HASH_MISMATCH")
    matches = [row for row in value["scenarios"] if row["scenario_id"] == scenario_id]
    if len(matches) != 1:
        raise RuntimeError("V2_NEGATIVE_CONTROL_MANIFEST_BINDING_NOT_ONE")
    entry = matches[0]
    for key in ("fixture_xml", "physical_json"):
        path = ROOT / entry[key]
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry[key + "_sha256"]:
            raise RuntimeError("V2_NEGATIVE_CONTROL_FIXTURE_HASH_MISMATCH:" + key)
    return entry


def history(live: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = live.get("persistent_decision_history")
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _evidence(row: Mapping[str, Any]) -> Mapping[str, Any]:
    m2b = row.get("m2b_inputs")
    if not isinstance(m2b, Mapping):
        return {}
    value = m2b.get("evidence")
    return value if isinstance(value, Mapping) else {}


def negative_control_ready(family: str, live: Mapping[str, Any]) -> bool:
    rows = [row for row in history(live) if row.get("decision") == "FALLBACK"]
    if len(rows) < 3:
        return False
    if family == "EVIDENCE_INSUFFICIENT":
        route_order = live.get("referent_route_order_authorization", {})
        route_order_typed_unknown = bool(
            isinstance(route_order, Mapping)
            and route_order.get("value") is None
            and route_order.get("authorization_eligible") is False
            and route_order.get("reason_code")
            == "FUTURE_OBLIGATION_ROUTE_ORDER_NOT_AUTHORIZATION_GRADE"
        )
        for row in rows:
            future = _evidence(row).get("future_obligation", {})
            reasons = future.get("reason_codes", ()) if isinstance(future, Mapping) else ()
            if (
                route_order_typed_unknown
                and
                row.get("candidate_relationship") == "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"
                and future.get("relation") is None
                and future.get("authorization_eligible") is False
                and any("TOPOLOGY_UNAVAILABLE" in str(reason) for reason in reasons)
            ):
                return True
        return False
    if family == "TOO_LATE":
        for row in rows:
            evidence = _evidence(row)
            future = evidence.get("future_obligation", {})
            window = evidence.get("clarification_window", {})
            lease = evidence.get("shared_action_lease", {})
            if (
                future.get("availability") == "AVAILABLE"
                and future.get("relation") == "FUTURE_OBLIGATION_DIVERGENT"
                and window.get("urgency") == "TOO_LATE"
                and lease.get("authorization_eligible") is False
            ):
                return True
        return False
    return False


def classification_ready(family: str, live: Mapping[str, Any]) -> bool:
    """Stop after enough evidence for either acceptance or honest N1/N3 classification."""

    if negative_control_ready(family, live):
        return True
    if family != "TOO_LATE":
        return False
    rows = [row for row in history(live) if row.get("decision") == "FALLBACK"]
    if len(rows) < 3:
        return False
    row = rows[-1]
    evidence = _evidence(row)
    future = evidence.get("future_obligation", {})
    return bool(
        float(row.get("current_progress_m", -1.0)) >= 25.13
        and isinstance(future, Mapping)
        and future.get("authorization_eligible") is False
        and row.get("candidate_relationship") == "UNKNOWN_OR_INSUFFICIENT_EVIDENCE"
    )


def desktop_ready(family: str, live: Mapping[str, Any]) -> bool:
    return bool(
        isinstance(live.get("decision_window_dashboard"), Mapping)
        and classification_ready(family, live)
    )


def episode_spec(scenario_id: str, repeat: bool, repair_index: int = 0):
    row = scenario(scenario_id)
    entry = manifest_entry(scenario_id)
    base = backend.resolve_episode(
        fixture_id="E1R1-ASK-PHYS-001",
        method_id="driveclarify_grounded_v1",
        episode_id="v2-negative-control-base",
    )
    route_path = (ROOT / entry["fixture_xml"]).resolve()
    route_sha = hashlib.sha256(route_path.read_bytes()).hexdigest()
    episode_suffix = "-REPAIR{}".format(repair_index) if repair_index else ""
    return replace(
        base,
        episode_id="DC-V2-NEGATIVE-CONTROL-{}-{}-20260813".format(
            scenario_id, "R2" if repeat else "R1"
        ) + episode_suffix,
        scenario_id=scenario_id,
        split="train",
        seed=int(entry["repeat_seed"] if repeat else entry["seed"]),
        town=row["town"],
        route_id=row["route_id"],
        route_path=route_path,
        raw_instruction=row["instruction"],
        schedule_sha256=route_sha,
    )


def execute(
    scenario_id: str, repeat: bool, timeout_seconds: float, repair_index: int = 0
) -> Mapping[str, Any]:
    row = scenario(scenario_id)
    family = row["negative_control_family"]
    run_id = scenario_id + ("-R2" if repeat else "-R1")
    if repair_index:
        run_id += "-REPAIR{}".format(repair_index)
    output = ARTIFACT / run_id
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("V2_NEGATIVE_CONTROL_OUTPUT_NOT_EMPTY:" + str(output))
    REPORT.mkdir(parents=True, exist_ok=True)
    live_marker = LIVE_MARKER
    if not live_marker.exists():
        live_marker.write_text(
            json.dumps(
                {
                    "first_requested_run": run_id,
                    "manifest_file_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
                    "manifest_payload_sha256": json.loads(MANIFEST.read_text())["manifest_payload_sha256"],
                },
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
    spec = episode_spec(scenario_id, repeat, repair_index)
    lease_receipt = None
    try:
        with backend.serial_gpu_lease() as lease:
            lease_receipt = lease
            return run_grounded(
                output,
                case="ask",
                seed=spec.seed,
                method_id="driveclarify_grounded_v1",
                device="cpu",
                control=True,
                answer="The nearer white van.",
                answer_delay=float(row["answer_delay_s"]),
                timeout_seconds=timeout_seconds,
                episode_spec=spec,
                visualization=True,
                post_hoc_world_state=True,
                terminate_on_runtime_terminal=True,
                capture_desktop=True,
                environment_overrides={
                    V1_FEATURE_FLAG: "1",
                    V2_FEATURE_FLAG: "1",
                    "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
                    "SCENARIO_RUNNER_ROOT": str(SCENARIO_ROOT.resolve()),
                },
                completion_predicate=lambda live: classification_ready(family, live),
                desktop_capture_predicate=lambda live: desktop_ready(family, live),
                accept_natural_evaluator_completion=True,
                allow_scientific_collision_outcome=True,
            )
    finally:
        if lease_receipt is not None and output.is_dir():
            backend._write_lease(output, lease_receipt)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    choices = tuple(row["scenario_id"] for row in manifest["scenarios"])
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario_id", choices=choices)
    parser.add_argument("--repeat", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=420.0)
    parser.add_argument("--repair-index", type=int, default=0)
    parser.add_argument("--preflight-import-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_import_only:
        entry = manifest_entry(args.scenario_id)
        heavy = sorted(name for name in sys.modules if name.split(".", 1)[0] in {"carla", "torch"})
        print(json.dumps({
            "status": "PASS_V2_NEGATIVE_CONTROL_PREFLIGHT" if not heavy else "BLOCKED_HEAVY_IMPORT",
            "scenario_id": args.scenario_id,
            "split": entry["split"],
            "manifest_file_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
            "manifest_payload_sha256": manifest["manifest_payload_sha256"],
            "heavy_modules": heavy,
        }, indent=2, sort_keys=True))
        return 0 if not heavy else 2
    try:
        if args.repair_index < 0:
            raise ValueError("REPAIR_INDEX_MUST_BE_NONNEGATIVE")
        result = execute(
            args.scenario_id, args.repeat, args.timeout_seconds, args.repair_index
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if str(result.get("status", "")).startswith("PASS_") else 2
    except Exception as error:
        print(json.dumps({
            "status": "BLOCKED_V2_NEGATIVE_CONTROL_ENGINEERING_FAILURE",
            "scenario_id": args.scenario_id,
            "repeat": args.repeat,
            "repair_index": args.repair_index,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "valid_scientific_run": False,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
