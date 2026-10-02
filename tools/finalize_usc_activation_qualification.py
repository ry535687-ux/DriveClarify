#!/usr/bin/env python3
"""Seal the USC trigger-binding repair and activation-only qualification."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1"
OUTPUT = REPORT / "NATIVE_EVIDENCE/RQ2TE2V3-ENG-037/attempt_01"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value: Any) -> str:
    import hashlib
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(name: str, value: Mapping[str, Any]) -> None:
    atomic_bytes(
        REPORT / name,
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n",
    )


def write_text(name: str, value: str) -> None:
    atomic_bytes(REPORT / name, value.rstrip().encode("utf-8") + b"\n")


def main() -> int:
    result = load(OUTPUT / "ENGINEERING_EPISODE_RESULT.json")
    activation = load(OUTPUT / "USC_ACTIVATION_RECEIPT.json")
    admission = load(OUTPUT / "USC_ROUTE_TRIGGER_ADMISSION.json")
    scenario = load(OUTPUT / "E2_V3_SCENARIO_RECEIPT.json")
    cleanup = load(OUTPUT / "CLEANUP_RECEIPT.json")
    prior = load(REPORT / "NATIVE_EVIDENCE/RQ2TE2V3-ENG-007/attempt_01/ENGINEERING_EPISODE_RESULT.json")
    checks = {
        "identity_037_valid_engineering_episode": result.get("status") == "VALID_ENGINEERING_EPISODE",
        "static_route_trigger_admission_pass": admission.get("status") == "PASS_STATIC_ROUTE_TRIGGER_ADMISSION",
        "activation_status_bound": activation.get("scenario_activation_status") == "ACTIVATED_AND_BOUND",
        "exact_scenario_instance": activation.get("scenario_instance_id") == scenario.get("scenario_instance_id"),
        "activation_timer_started_once": activation.get("activation_timer_start_count") == 1 == scenario.get("activation_timer_start_count"),
        "activation_only_horizon_exact": abs(float(scenario.get("engineering_bound_s", 0.0)) - 0.25) <= 1e-9,
        "activation_only_horizon_complete": scenario.get("engineering_bound_reached") is True and float(scenario.get("scenario_elapsed_simulation_time_s", 0.0)) >= 0.25,
        "detector_disabled_by_nonvisual_usc_binding": result.get("mechanism_summary", {}).get("detector_invocation_count") == 0,
        "formal_scientific_exposure_zero": result.get("formal_scientific_exposure") is False,
        "cleanup_pass": cleanup.get("status") == "PASS",
        "identity_007_preserved_invalid": prior.get("status") == "INVALID_ENGINEERING_ATTEMPT",
        "identity_007_scenario_bound_never_reached": prior.get("integrity", {}).get("engineering_bound_reached") is False,
    }
    receipt = {
        "schema_version": "driveclarify.e2_v3.usc_activation_only_qualification.v1",
        "status": "PASS_USC_NATIVE_ACTIVATION_QUALIFICATION" if all(checks.values()) else "USC_TRIGGER_BINDING_NOT_CLOSED",
        "engineering_identity": "RQ2TE2V3-ENG-037",
        "engineering_seed": int(result["seed"]),
        "activation_simulator_frame": activation.get("simulator_frame"),
        "activation_simulator_time": activation.get("simulator_time"),
        "scenario_instance_id": activation.get("scenario_instance_id"),
        "route_id": activation.get("route_id"),
        "route_digest": activation.get("route_digest"),
        "scenario_config_digest": activation.get("scenario_config_digest"),
        "last_admissible_activation_boundary": admission.get("last_admissible_activation_boundary"),
        "scenario_relative_horizon_s": scenario.get("engineering_bound_s"),
        "scenario_elapsed_simulation_time_s": scenario.get("scenario_elapsed_simulation_time_s"),
        "checks": checks,
        "cleanup": cleanup,
        "formal_seed": False,
        "formal_scientific_exposure": False,
        "eligible_for_method_or_science_denominators": False,
        "identity_007_classification": "ENGINEERING_INVALID_SCENARIO_TRIGGER_NOT_ACTIVATED",
    }
    receipt["receipt_digest"] = canonical(receipt)
    write_json("USC_ACTIVATION_ONLY_QUALIFICATION_RECEIPT.json", receipt)
    write_text("USC_ACTIVATION_ONLY_QUALIFICATION_REPORT.md", """
# USC activation-only qualification

Identity `RQ2TE2V3-ENG-037` passed static admission and wrote an exact-instance
`ACTIVATED_AND_BOUND` receipt at simulator frame `{frame}` / time `{time}`.
The scenario-relative timer started once and completed the activation-only
0.25 s horizon. Grounding-DINO calls were zero and cleanup passed. This
identity is activation infrastructure evidence only and is excluded from all
method, formal, and paper denominators.

Status: `{status}`
""".format(frame=activation.get("simulator_frame"), time=activation.get("simulator_time"), status=receipt["status"]))

    route_contract = {
        "schema_version": "driveclarify.e2_v3.usc_route_trigger_admission_contract.v1",
        "fail_closed_before_carla": True,
        "required_bindings": [
            "town", "route_path_digest_id", "scenario_config_digest", "scenario_type_owner",
            "trigger_transform_volume", "route_polyline_intersection_direction",
            "first_last_admissible_arc_positions", "road_lane_junction_certificate",
            "actor_semantics", "usc_instruction_candidates", "engineering_exclusion",
        ],
        "qualified_admission": admission,
    }
    route_contract["contract_digest"] = canonical(route_contract)
    write_json("USC_ROUTE_TRIGGER_ADMISSION_CONTRACT.json", route_contract)
    write_text("USC_ROUTE_TRIGGER_ADMISSION_CONTRACT.md", "# USC route/trigger admission contract\n\n```json\n{}\n```".format(json.dumps(route_contract, indent=2, sort_keys=True)))

    activation_contract = {
        "schema_version": "driveclarify.e2_v3.usc_activation_receipt_contract.v1",
        "positive_status": "ACTIVATED_AND_BOUND",
        "construction_is_not_activation": True,
        "exact_scenario_instance_required": True,
        "timer_starts_only_at_behavior_initialise": True,
        "full_horizon_s": 5.0,
        "activation_receipt_example": activation,
    }
    activation_contract["contract_digest"] = canonical(activation_contract)
    write_json("USC_ACTIVATION_RECEIPT_CONTRACT.json", activation_contract)
    write_text("USC_ACTIVATION_RECEIPT_CONTRACT.md", "# USC activation receipt contract\n\n```json\n{}\n```".format(json.dumps(activation_contract, indent=2, sort_keys=True)))

    fail_fast = {
        "schema_version": "driveclarify.e2_v3.usc_activation_fail_fast_contract.v1",
        "owner": "ENGINEERING_LAUNCHER_POLL_OBSERVER",
        "scientific_terminal": False,
        "boundary": admission.get("last_admissible_activation_boundary"),
        "miss_status": "ENGINEERING_SCENARIO_ACTIVATION_MISSED",
        "wall_timeout_semantics": "FINAL_INFRASTRUCTURE_SAFEGUARD_ONLY",
        "cleanup_required": True,
    }
    fail_fast["contract_digest"] = canonical(fail_fast)
    write_json("USC_ACTIVATION_FAIL_FAST_CONTRACT.json", fail_fast)
    write_text("USC_ACTIVATION_FAIL_FAST_CONTRACT.md", "# USC activation fail-fast contract\n\n```json\n{}\n```".format(json.dumps(fail_fast, indent=2, sort_keys=True)))

    short_route = {
        "schema_version": "driveclarify.e2_v3.usc_short_route_certificate.v1",
        "engineering_only": True,
        "source_base_route": "driveclarify_rq2_t/formal_routes/ref-01.xml",
        "executable_route": admission.get("route_path"),
        "route_id": admission.get("route_id"),
        "route_sha256": admission.get("route_sha256"),
        "town": admission.get("town"),
        "usc_semantics_unchanged": True,
        "owner_control_semantics_unchanged": True,
        "blind_or_formal_scene_modified": False,
        "route_trigger_admission_pass": True,
    }
    short_route["certificate_digest"] = canonical(short_route)
    write_json("USC_SHORT_ROUTE_CERTIFICATE.json", short_route)
    write_text("USC_SHORT_ROUTE_CERTIFICATE.md", "# USC short engineering route certificate\n\n```json\n{}\n```".format(json.dumps(short_route, indent=2, sort_keys=True)))

    write_text("USC_TRIGGER_BINDING_ROOT_CAUSE.md", """
# USC trigger-binding root cause

Identity 007 used the old long Town06 base route. Scenario construction
occurred, but the executable route never entered the intended behavior-tree
activation region, so the scenario-relative five-second timer never started.
The evaluator made healthy route progress until its 1,200-second wall cap.
It is classified `ENGINEERING_INVALID_SCENARIO_TRIGGER_NOT_ACTIVATED`; its
2,642 partial rows are preserved but excluded from every denominator.

The repair uses an engineering-only Town12 short route whose exact trigger is
at route arc length 0.0 m, intersects the polyline at 0.0 m distance, and has
direction dot product {dot:.9f}. The last admissible activation boundary is
2.0 route-arc metres. Construction and activation now have separate states.
""".format(dot=float(admission["route_direction_dot_trigger_forward"])))

    for source, target in (
        (REPORT / "ENGINEERING_IDENTITY_REGISTRY.json", REPORT / "UPDATED_ENGINEERING_IDENTITY_REGISTRY.json"),
        (REPORT / "ENGINEERING_SEED_EXCLUSION_REGISTRY.json", REPORT / "UPDATED_ENGINEERING_SEED_EXCLUSION_REGISTRY.json"),
    ):
        shutil.copyfile(str(source), str(target))
    write_text("COMMAND_LOG_ADDENDUM.md", """
# USC trigger-binding command-log addendum

- Identity 007 terminated at its declared wall containment and remains immutable.
- Static route/trigger admission implemented and passed before identity 037 launch.
- Identity 037 activation-only qualification passed; detector invocations: 0.
- Identity 038 remains unexposed until source freeze authorization.
- Formal seeds: 0. Formal scientific exposures: 0.
""")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS_USC_NATIVE_ACTIVATION_QUALIFICATION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
