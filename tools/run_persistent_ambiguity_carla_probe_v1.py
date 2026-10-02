#!/usr/bin/env python3
"""Run exactly one bounded native white-van Phase B evidence-only probe."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


# Match the repository's existing direct ``python tools/...py`` entrypoints:
# Python otherwise exposes ``tools/`` rather than the repository root at
# sys.path[0], making sibling DriveClarify packages invisible.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_decision_window_carla_probe_v1.analysis import analyze  # noqa: E402
from driveclarify_decision_window_carla_probe_v1.runtime import FEATURE_FLAG  # noqa: E402
from driveclarify_grounded_language_v1_extension_e1_r1 import backend  # noqa: E402
from tools.run_grounded_language_v1_triad import run as run_grounded  # noqa: E402


RUN_ID = "B0-R1"
OUTPUT = ROOT / (
    "artifacts/driveclarify_persistent_ambiguity_runtime_v1_phase_b_import_repair_r1/"
    + RUN_ID
)
REPORT = ROOT / "reports/driveclarify_persistent_ambiguity_runtime_v1_phase_b_import_repair_r1"


def import_preflight_receipt() -> dict:
    heavy_modules = sorted(
        name
        for name in sys.modules
        if name.split(".", 1)[0] in {"carla", "torch", "transformers"}
    )
    return {
        "schema_version": "driveclarify.phase_b_import_repair.import_preflight.v1",
        "status": (
            "PASS_PHASE_B_RUNNER_TOP_LEVEL_IMPORT_ONLY"
            if not heavy_modules
            else "BLOCKED_IMPORT_PREFLIGHT_HEAVY_RUNTIME_SIDE_EFFECT"
        ),
        "run_id": RUN_ID,
        "python_executable": sys.executable,
        "runner_path": str(Path(__file__).resolve()),
        "repository_root": str(ROOT),
        "repository_root_on_sys_path": str(ROOT) in sys.path,
        "probe_package_path": str(
            Path(sys.modules["driveclarify_decision_window_carla_probe_v1"].__file__).resolve()
        ),
        "top_level_imports_completed": True,
        "heavy_runtime_modules_loaded": heavy_modules,
        "carla_process_launch_count": 0,
        "model_initialization_count": 0,
        "checkpoint_load_count": 0,
        "dino_initialization_count": 0,
        "vehicle_control_write_count": 0,
        "artifact_write_count": 0,
    }


def main() -> int:
    try:
        if OUTPUT.exists() and any(OUTPUT.iterdir()):
            raise RuntimeError("PHASE_B_B0_R1_ARTIFACT_DIRECTORY_NOT_EMPTY")
        spec = backend.resolve_episode(
            fixture_id="E1R1-ASK-PHYS-001",
            method_id="driveclarify_grounded_v1",
            episode_id="DC-PERSISTENT-AMBIGUITY-PHASE-B0-R1-20260812",
        )
        fixture = backend.physical_fixture("E1R1-ASK-PHYS-001")
        lease_receipt = None
        try:
            with backend.serial_gpu_lease() as lease:
                lease_receipt = lease
                run_grounded(
                    OUTPUT,
                    case="ask",
                    seed=spec.seed,
                    method_id="driveclarify_grounded_v1",
                    device="cpu",
                    control=False,
                    answer="The nearer white van.",
                    answer_delay=0.1,
                    timeout_seconds=240.0,
                    episode_spec=spec,
                    visualization=True,
                    post_hoc_world_state=True,
                    terminate_on_runtime_terminal=True,
                    capture_desktop=True,
                    environment_overrides={
                        FEATURE_FLAG: "1",
                        "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1": "1",
                        "SCENARIO_RUNNER_ROOT": str(
                            (ROOT / backend.SCENARIO_ROOT).resolve()
                        ),
                        "DRIVECLARIFY_E1R1_FIXTURE_ID": "E1R1-ASK-PHYS-001",
                        "DRIVECLARIFY_E1R1_WAIT_ACTOR_MOTION_SOURCE": str(
                            fixture["motion_source"]
                        ),
                    },
                )
        finally:
            if lease_receipt is not None and OUTPUT.is_dir():
                backend._write_lease(OUTPUT, lease_receipt)
        receipt = analyze(OUTPUT, REPORT, run_id=RUN_ID)
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if receipt["valid_controlled_probe_run"] else 2
    except Exception as error:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        REPORT.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema_version": "driveclarify.phase_b.execution_failure.v1",
            "status": "BLOCKED_CARLA_EXECUTION_FAILURE",
            "run_id": RUN_ID,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "retry_executed": False,
            "phase_c_authorized_to_continue": False,
        }
        for path in (
            OUTPUT / "PHASE_B_EXECUTION_FAILURE.json",
            REPORT / "PHASE_B_EXECUTION_FAILURE.json",
        ):
            path.write_text(
                json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        print(json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


def entrypoint() -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded native white-van Phase B evidence probe."
    )
    parser.add_argument(
        "--preflight-import-only",
        action="store_true",
        help="validate top-level imports without CARLA/model/artifact execution",
    )
    args = parser.parse_args()
    if args.preflight_import_only:
        receipt = import_preflight_receipt()
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if receipt["status"].startswith("PASS_") else 2
    return main()


if __name__ == "__main__":
    raise SystemExit(entrypoint())
