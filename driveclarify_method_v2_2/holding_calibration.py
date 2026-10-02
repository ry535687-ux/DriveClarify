"""Passive BENCH2DRIVE DEV lawful-HOLD calibration observer.

This module never creates a plan, controller, or VehicleControl.  It reuses the
production native route-local hard-gate provider and observes the control that
the frozen SimLingo baseline already returned.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Mapping

from driveclarify_persistent_ambiguity_runtime_v1.evidence_adapter import (
    DecisionWindowEvidenceAdapter,
)
from driveclarify_persistent_ambiguity_runtime_v1.runtime import (
    HardGateCertificateStatus,
    NativeCarlaRouteLocalEvidenceProvider,
)


SCHEMA = "driveclarify.method_v2_2.holding_calibration_episode.v1"
CALIBRATION_FLAG = "DRIVECLARIFY_METHOD_V2_2_HOLD_CALIBRATION"
POLICY_FLAGS = (
    "DRIVECLARIFY_GROUNDED_LANGUAGE_V1",
    "DRIVECLARIFY_PERSISTENT_AMBIGUITY_RUNTIME_V1",
    "DRIVECLARIFY_METHOD_REVISION_V2",
    "DRIVECLARIFY_METHOD_V2_1_RULE_GATE_DECOUPLED_CLARIFICATION",
    "DRIVECLARIFY_R4_5_ANSWER_CONDITIONED_RECONNECT",
    "DRIVECLARIFY_CANDIDATE_LIVE_ACT_AUTHORITY_V0",
    "DRIVECLARIFY_PAPER_MVP_STAGE6A_LIVE",
    "DRIVECLARIFY_PAPER_MVP_STAGE6B_LIVE",
)
ACTUATOR_FIELDS = (
    "steer",
    "throttle",
    "brake",
    "hand_brake",
    "reverse",
    "manual_gear_shift",
)


def _truthy(value: str | None) -> bool:
    return bool(value) and str(value).strip().lower() in {"1", "true", "yes", "on"}


def _control_values(control: Any) -> dict[str, Any]:
    return {
        name: getattr(control, name)
        for name in (*ACTUATOR_FIELDS, "gear")
        if hasattr(control, name)
    }


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class HoldingCalibrationRuntime:
    """Read-only observer for maximal continuous lawful baseline HOLD intervals."""

    enabled = True

    def __init__(self, agent: Any, output_dir: str | os.PathLike[str]) -> None:
        self.agent = agent
        self.output_dir = Path(output_dir)
        self.output_path = self.output_dir / "HOLDING_CALIBRATION_EPISODE.json"
        self.run_id = os.environ.get("DRIVECLARIFY_PROBE_RUN_ID", "UNKNOWN")
        self.split = os.environ.get("DRIVECLARIFY_CALIBRATION_SPLIT", "UNKNOWN")
        self.town = os.environ.get("DRIVECLARIFY_CALIBRATION_TOWN", "UNKNOWN")
        self.route_id = os.environ.get("DRIVECLARIFY_CALIBRATION_ROUTE_ID", "UNKNOWN")
        self.route_path = os.environ.get("DRIVECLARIFY_CALIBRATION_ROUTE_PATH", "UNKNOWN")
        self.seed = int(os.environ.get("DRIVECLARIFY_CALIBRATION_SEED", "0"))
        self._provider: NativeCarlaRouteLocalEvidenceProvider | None = None
        self._provider_error: str | None = None
        self._last_tick: dict[str, Any] | None = None
        self._last_returned_control: dict[str, Any] | None = None
        self._last_returned_control_frame: int | None = None
        self._active: dict[str, Any] | None = None
        self._events: list[dict[str, Any]] = []
        self._ticks: list[dict[str, Any]] = []
        self._errors: list[dict[str, Any]] = []
        self._closed = False
        conflicts = [name for name in POLICY_FLAGS if _truthy(os.environ.get(name))]
        if conflicts:
            raise RuntimeError("CALIBRATION_DRIVECLARIFY_POLICY_CONFLICT:" + ",".join(conflicts))

    def _native(self) -> tuple[Any, Any, Any]:
        from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

        world = CarlaDataProvider.get_world()
        hero = CarlaDataProvider.get_hero_actor()
        return world, hero, world.get_map()

    def _ensure_provider(self) -> NativeCarlaRouteLocalEvidenceProvider:
        if self._provider is None:
            self._provider = NativeCarlaRouteLocalEvidenceProvider.from_native_agent(
                self.agent
            )
        return self._provider

    def _finish(self, row: Mapping[str, Any], reason: str, censored: bool) -> None:
        if self._active is None:
            return
        end_monotonic = float(row["monotonic_s"])
        end_simulation = float(row["simulation_time_s"])
        event = dict(self._active)
        event.update(
            {
                "end_frame_id": int(row["frame_id"]),
                "end_monotonic_s": end_monotonic,
                "end_simulation_time_s": end_simulation,
                "duration_wall_s": end_monotonic - float(event["start_monotonic_s"]),
                "duration_simulation_s": end_simulation
                - float(event["start_simulation_time_s"]),
                "termination_reason": reason,
                "complete": not censored,
                "right_censored": bool(censored),
            }
        )
        self._events.append(event)
        self._active = None

    def on_tick(
        self,
        input_data: Any,
        tick_data: Any,
        timestamp: Any,
        frame: Any,
        observation_id: Any,
    ) -> None:
        del input_data, tick_data
        monotonic_s = time.monotonic()
        row: dict[str, Any] = {
            "frame_id": int(frame),
            "observation_id": str(observation_id),
            "monotonic_s": monotonic_s,
            "simulation_time_s": float(timestamp),
            "physical_safety_status": "UNKNOWN",
            "route_local_hard_rule_status": "UNKNOWN",
            "baseline_control_path_healthy": False,
            "safe_holding_authority_valid": False,
            "selected_maneuver_execution_authority": False,
            "ego_displacement_m": None,
            "route_progress_m": None,
            "route_progress_delta_m": None,
            "no_relevant_physical_progress": False,
            "evidence_finite_fresh_auditable": False,
            "lawful_hold": False,
        }
        try:
            world, hero, _map = self._native()
            snapshot = world.get_snapshot()
            location = hero.get_location()
            position = (float(location.x), float(location.y))
            if not all(math.isfinite(value) for value in position):
                raise ValueError("NONFINITE_EGO_POSITION")
            provider = self._ensure_provider()
            envelope = provider.resolve_hard_gate_evidence(
                source_observation_id=str(observation_id),
                source_frame_id=int(frame),
                route_version=None,
                environment_digest=None,
            )
            envelope.validate(
                expected_source_observation_id=str(observation_id),
                expected_current_frame_id=int(frame),
                expected_provider_binding=provider.production_binding(),
            )
            projection = DecisionWindowEvidenceAdapter.project_to_route(
                position, provider._route_points
            )
            route_progress = float(projection.progress_m)
            actual = _control_values(hero.get_control())
            control_healthy = bool(
                self._last_returned_control is not None
                and all(
                    actual.get(name) == self._last_returned_control.get(name)
                    for name in ACTUATOR_FIELDS
                )
            )
            row.update(
                {
                    "snapshot_frame_id": int(snapshot.frame),
                    "ego_position_xy_m": list(position),
                    "route_progress_m": route_progress,
                    "physical_safety_status": envelope.physical_safety.status.value,
                    "route_local_hard_rule_status": envelope.route_local_hard_rule.status.value,
                    "baseline_control_path_healthy": control_healthy,
                    "safe_holding_authority_valid": control_healthy,
                    "actual_control": actual,
                    "returned_control_source_frame_id": self._last_returned_control_frame,
                    "provider_binding": provider.production_binding(),
                    "evidence_finite_fresh_auditable": bool(
                        int(snapshot.frame) == int(frame)
                        and envelope.source_frame_id == int(frame)
                        and math.isfinite(float(envelope.source_timestamp))
                        and math.isfinite(route_progress)
                    ),
                }
            )
            if self._last_tick is not None:
                previous_position = self._last_tick.get("ego_position_xy_m")
                previous_progress = self._last_tick.get("route_progress_m")
                if previous_position is not None and previous_progress is not None:
                    displacement = math.hypot(
                        position[0] - float(previous_position[0]),
                        position[1] - float(previous_position[1]),
                    )
                    progress_delta = route_progress - float(previous_progress)
                    row["ego_displacement_m"] = displacement
                    row["route_progress_delta_m"] = progress_delta
                    # Exact stationary evidence is deliberately conservative and
                    # introduces no calibration-only speed/distance threshold.
                    row["no_relevant_physical_progress"] = bool(
                        displacement == 0.0 and progress_delta <= 0.0
                    )
            row["lawful_hold"] = bool(
                row["physical_safety_status"]
                == HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_PASS.value
                and row["route_local_hard_rule_status"]
                == HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_BLOCKED.value
                and row["safe_holding_authority_valid"]
                and row["selected_maneuver_execution_authority"] is False
                and row["no_relevant_physical_progress"]
                and row["evidence_finite_fresh_auditable"]
            )
        except Exception as exc:  # fail closed for calibration admission
            self._provider_error = type(exc).__name__ + ":" + str(exc)
            row["error"] = self._provider_error
            self._errors.append(
                {"frame_id": int(frame), "error": self._provider_error}
            )

        if row["lawful_hold"]:
            if self._active is None:
                # The audited no-progress relation spans the previous tick to this
                # tick; both endpoint gate states must be qualifying.
                previous = self._last_tick
                if previous is not None and bool(
                    previous.get("physical_safety_status")
                    == HardGateCertificateStatus.VERIFIED_PHYSICAL_SAFETY_PASS.value
                    and previous.get("route_local_hard_rule_status")
                    == HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_BLOCKED.value
                    and previous.get("baseline_control_path_healthy")
                    and previous.get("evidence_finite_fresh_auditable")
                ):
                    self._active = {
                        "event_id": f"{self.run_id}:HOLD:{len(self._events) + 1:03d}",
                        "start_frame_id": int(previous["frame_id"]),
                        "start_monotonic_s": float(previous["monotonic_s"]),
                        "start_simulation_time_s": float(previous["simulation_time_s"]),
                        "production_detector_id": NativeCarlaRouteLocalEvidenceProvider.IMPLEMENTATION_ID,
                    }
        elif self._active is not None:
            route_status = row.get("route_local_hard_rule_status")
            if route_status == HardGateCertificateStatus.VERIFIED_ROUTE_LOCAL_PASS.value:
                reason = "MOTION_BECAME_EXECUTABLE"
            elif row.get("no_relevant_physical_progress") is False:
                reason = "HOLDING_INVALID_PHYSICAL_PROGRESS"
            else:
                reason = "HOLDING_INVALID_EVIDENCE_OR_AUTHORITY"
            self._finish(row, reason, censored=False)
        self._ticks.append(row)
        self._last_tick = row
        self._write()

    def prepare_model_input(self, model_input: Any) -> Any:
        return model_input

    def on_model_output(self, *args: Any, **kwargs: Any) -> None:
        return None

    def select_plan_source(self, baseline_route: Any, baseline_speed: Any, *args: Any) -> tuple[Any, Any]:
        return baseline_route, baseline_speed

    def on_pid_invocation(self, *args: Any, **kwargs: Any) -> None:
        return None

    def on_control(self, control: Any, gt_velocity: Any, current_monotonic: float) -> None:
        del gt_velocity, current_monotonic
        self._last_returned_control = _control_values(control)
        self._last_returned_control_frame = (
            None if self._last_tick is None else int(self._last_tick["frame_id"])
        )

    def commit(self) -> None:
        self._write()

    def close(self) -> None:
        if self._closed:
            return
        if self._active is not None and self._last_tick is not None:
            self._finish(self._last_tick, "EPISODE_TERMINATED_BEFORE_MOTION_RECOVERY", censored=True)
        self._closed = True
        self._write()

    def _payload(self) -> dict[str, Any]:
        try:
            fixed_delta = float(self._provider._world.get_settings().fixed_delta_seconds) if self._provider else None
        except Exception:
            fixed_delta = None
        return {
            "schema_version": SCHEMA,
            "status": "PASS_PASSIVE_CALIBRATION_OBSERVER" if not self._errors else "PASS_WITH_FAIL_CLOSED_TICKS",
            "run_id": self.run_id,
            "split": self.split,
            "town": self.town,
            "route_id": self.route_id,
            "route_path": self.route_path,
            "seed": self.seed,
            "normal_non_ambiguity_driving": True,
            "raw_instruction": os.environ.get("DRIVECLARIFY_CALIBRATION_INSTRUCTION"),
            "driveclarify_scientific_policy_executed": False,
            "candidate_vehicle_control_authority": "OFF",
            "new_planner_count": 0,
            "new_pid_count": 0,
            "new_vehicle_control_writer_count": 0,
            "control_tick_duration_s": fixed_delta,
            "event_definition": "MAXIMAL_CONTINUOUS_PRODUCTION_RULE_BLOCKED_SAFE_BASELINE_HOLD_WITH_EXACT_ZERO_EGO_DISPLACEMENT_AND_NONINCREASING_ROUTE_PROGRESS",
            "complete_events": [row for row in self._events if row["complete"]],
            "right_censored_events": [row for row in self._events if row["right_censored"]],
            "active_event": self._active,
            "ticks": self._ticks,
            "errors": self._errors,
            "closed": self._closed,
        }

    def _write(self) -> None:
        _atomic_json(self.output_path, self._payload())

    def summary(self) -> dict[str, Any]:
        value = self._payload()
        return {
            key: value[key]
            for key in (
                "schema_version",
                "status",
                "run_id",
                "split",
                "town",
                "route_id",
                "driveclarify_scientific_policy_executed",
                "candidate_vehicle_control_authority",
                "new_planner_count",
                "new_pid_count",
                "new_vehicle_control_writer_count",
                "control_tick_duration_s",
                "closed",
            )
        } | {
            "complete_event_count": len(value["complete_events"]),
            "right_censored_event_count": len(value["right_censored_events"]),
        }


def build_holding_calibration_runtime(agent: Any) -> HoldingCalibrationRuntime:
    if not _truthy(os.environ.get(CALIBRATION_FLAG)):
        raise RuntimeError("HOLDING_CALIBRATION_NOT_ENABLED")
    output = os.environ.get("DRIVECLARIFY_METHOD_V2_2_CALIBRATION_OUTPUT_DIR")
    if not output:
        raise RuntimeError("HOLDING_CALIBRATION_OUTPUT_DIR_MISSING")
    return HoldingCalibrationRuntime(agent, output)
