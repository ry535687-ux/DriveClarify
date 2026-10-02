"""Opt-in evaluator hook for the read-only forced-horizon safety receipt."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Mapping, Optional

from .forced_horizon_safety import (
    ALLOWED_NATURAL_TERMINAL_CLASSES,
    CRITERION_OWNER_SPECS,
    CriterionLifetimeEvidence,
    OFFICIAL_SCENARIO_MANAGER_SHA256,
    RQ2_T_FORCED_HORIZON_SECONDS,
    file_sha256,
    locate_route_owned_criteria,
    snapshot_forced_horizon_safety,
    snapshot_natural_terminal_safety,
    write_forced_horizon_safety_receipt_exclusive,
)


ENABLE_ENV = "DRIVECLARIFY_RQ2_FORCED_HORIZON_SAFETY_ENABLE"
OUTPUT_ENV = "DRIVECLARIFY_RQ2_FORCED_HORIZON_SAFETY_OUTPUT"
TIMING_BUCKET_ENV = "DRIVECLARIFY_RQ2_FORCED_HORIZON_TIMING_BUCKET"
WINDOW_CONTRACT_ENV = "DRIVECLARIFY_RQ2_SCIENTIFIC_WINDOW_CONTRACT"
CELL_ADMISSIBILITY_REFERENCE_ENV = "DRIVECLARIFY_RQ2_CELL_ADMISSIBILITY_REFERENCE"
MANAGER_IDENTITY_OUTPUT_ENV = "DRIVECLARIFY_RQ2_SCENARIOMANAGER_IDENTITY_OUTPUT"
ACTIVE_SCENARIO_MANAGER_MODULE = "leaderboard.scenarios.scenario_manager"
ACTIVE_SCENARIO_MANAGER_CLASS = "ScenarioManager"
INACTIVE_SCENARIO_MANAGER_MODULE = "srunner.scenariomanager.scenario_manager"


def _status_name(value: Any) -> str:
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else str(value).rsplit(".", 1)[-1].upper()


def _instance_identity(value: Any) -> str:
    return "{}.{},python_id={}".format(
        type(value).__module__, type(value).__name__, id(value)
    )


def resolve_authoritative_scenario_manager() -> Any:
    """Resolve the exact class imported by the active leaderboard evaluator."""

    module = importlib.import_module(ACTIVE_SCENARIO_MANAGER_MODULE)
    manager_class = getattr(module, ACTIVE_SCENARIO_MANAGER_CLASS)
    if manager_class.__module__ != ACTIVE_SCENARIO_MANAGER_MODULE:
        raise RuntimeError(
            "ACTIVE_SCENARIOMANAGER_MODULE_IDENTITY_MISMATCH:"
            + manager_class.__module__
        )
    return manager_class


def build_manager_identity_evidence(
    manager: Any,
    observer: Any,
    scenario: Any,
    authoritative_class: Any,
) -> Dict[str, Any]:
    """Describe passive-observer ownership without reading policy state."""

    manager_tree = getattr(manager, "scenario_tree", None)
    scenario_tree = getattr(scenario, "scenario_tree", None)
    criteria = locate_route_owned_criteria(scenario)
    return {
        "schema_version": "driveclarify.rq2.scenariomanager_identity.v1",
        "authoritative_manager_class": (
            authoritative_class.__module__ + "." + authoritative_class.__name__
        ),
        "authoritative_manager_class_python_id": id(authoritative_class),
        "active_manager_object_identity": _instance_identity(manager),
        "observer_target_object_identity": _instance_identity(observer.manager),
        "manager_class_identity_same": type(manager) is authoritative_class,
        "manager_object_identity_same": observer.manager is manager,
        "active_manager_module_exact": (
            type(manager).__module__ == ACTIVE_SCENARIO_MANAGER_MODULE
        ),
        "scenario_owner_identity": _instance_identity(scenario),
        "manager_scenario_identity_same": getattr(manager, "scenario", None) is scenario,
        "manager_scenario_tree_identity": _instance_identity(manager_tree),
        "scenario_tree_identity": _instance_identity(scenario_tree),
        "scenario_tree_identity_same": manager_tree is scenario_tree,
        "criterion_ownership_path": "leaderboard_autopilot.RouteScenario.criteria_node",
        "criteria": {
            spec.endpoint_name: {
                "required_class": spec.class_name,
                "owner": spec.owner,
                "located_instance_identity": (
                    None
                    if criteria.get(spec.endpoint_name) is None
                    else _instance_identity(criteria[spec.endpoint_name])
                ),
            }
            for spec in CRITERION_OWNER_SPECS
        },
        "inactive_manager_module": INACTIVE_SCENARIO_MANAGER_MODULE,
        "inactive_manager_used_for_outcome_receipt": False,
        "second_manager_instantiated": False,
    }


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _collision_sensor_live(criterion: Any) -> bool:
    sensor = getattr(criterion, "_collision_sensor", None)
    if sensor is None:
        return False
    listening = getattr(sensor, "is_listening", None)
    return listening if isinstance(listening, bool) else True


@dataclass
class _MutableLifetime:
    criterion_name: str
    observation_count: int = 0
    first_observed_frame: Optional[int] = None
    first_observed_time_s: Optional[float] = None
    last_observed_frame: Optional[int] = None
    last_observed_time_s: Optional[float] = None
    present_every_observed_tick: bool = True
    same_instance_every_observed_tick: bool = True
    active_after_every_observed_tick: bool = True
    collision_sensor_live_after_every_observed_tick: bool = True
    instance_identity: Optional[str] = None
    terminal_observation_seen: bool = False
    terminal_invalidation_observed: bool = False
    collision_sensor_stopped_at_terminal: bool = False

    def observe(
        self,
        criterion: Any,
        frame: int,
        sim_time_s: float,
        *,
        terminal_observation: bool = False,
    ) -> None:
        self.observation_count += 1
        self.terminal_observation_seen = (
            self.terminal_observation_seen or terminal_observation
        )
        if self.first_observed_frame is None:
            self.first_observed_frame = frame
            self.first_observed_time_s = sim_time_s
        self.last_observed_frame = frame
        self.last_observed_time_s = sim_time_s
        if criterion is None:
            self.present_every_observed_tick = False
            self.active_after_every_observed_tick = False
            self.collision_sensor_live_after_every_observed_tick = False
            return
        identity = _instance_identity(criterion)
        if self.instance_identity is None:
            self.instance_identity = identity
        elif self.instance_identity != identity:
            self.same_instance_every_observed_tick = False
        if _status_name(getattr(criterion, "status", "INVALID")) == "INVALID":
            if terminal_observation:
                self.terminal_invalidation_observed = True
            else:
                self.active_after_every_observed_tick = False
        if self.criterion_name == "collision" and not _collision_sensor_live(criterion):
            if terminal_observation:
                self.collision_sensor_stopped_at_terminal = True
            else:
                self.collision_sensor_live_after_every_observed_tick = False

    def freeze(self) -> CriterionLifetimeEvidence:
        return CriterionLifetimeEvidence(
            criterion_name=self.criterion_name,
            tracking_started_before_first_tree_tick=True,
            route_criterion_always_active_by_owner=True,
            observation_count=self.observation_count,
            first_observed_frame=self.first_observed_frame,
            first_observed_time_s=self.first_observed_time_s,
            last_observed_frame=self.last_observed_frame,
            last_observed_time_s=self.last_observed_time_s,
            present_every_observed_tick=self.present_every_observed_tick,
            same_instance_every_observed_tick=self.same_instance_every_observed_tick,
            active_after_every_observed_tick=self.active_after_every_observed_tick,
            collision_sensor_live_after_every_observed_tick=self.collision_sensor_live_after_every_observed_tick,
            instance_identity=self.instance_identity,
            evaluator_tick_owner=(
                "leaderboard.scenarios.scenario_manager.ScenarioManager._tick_scenario"
            ),
            evaluator_tick_owner_sha256=OFFICIAL_SCENARIO_MANAGER_SHA256,
            terminal_observation_seen=self.terminal_observation_seen,
            terminal_invalidation_observed=self.terminal_invalidation_observed,
            collision_sensor_stopped_at_terminal=self.collision_sensor_stopped_at_terminal,
        )


class ForcedHorizonSafetyObserver:
    """Passive bookkeeping around already-completed scenario-tree ticks."""

    def __init__(self, manager: Any, scenario: Any) -> None:
        self.manager = manager
        self.scenario = scenario
        self.captured = False
        self.lifetimes = {
            spec.endpoint_name: _MutableLifetime(spec.endpoint_name)
            for spec in CRITERION_OWNER_SPECS
        }

    def observe_after_existing_criteria_tick(
        self,
        frame: int,
        sim_time_s: float,
        tree_status: Any = "RUNNING",
    ) -> None:
        status = _status_name(tree_status)
        terminal_class = (
            "AUTHORITATIVE_ROUTE_TREE_" + status
            if status in {"SUCCESS", "FAILURE"}
            else None
        )
        terminal_observation = terminal_class in ALLOWED_NATURAL_TERMINAL_CLASSES
        criteria = locate_route_owned_criteria(self.scenario)
        for endpoint_name, lifetime in self.lifetimes.items():
            lifetime.observe(
                criteria.get(endpoint_name),
                frame,
                sim_time_s,
                terminal_observation=terminal_observation,
            )
        if self.captured:
            return
        if sim_time_s >= RQ2_T_FORCED_HORIZON_SECONDS:
            receipt = self._build_receipt(criteria, frame, sim_time_s)
        elif terminal_class in ALLOWED_NATURAL_TERMINAL_CLASSES:
            receipt = self._build_receipt(
                criteria,
                frame,
                sim_time_s,
                terminal_class=terminal_class,
            )
        else:
            return
        output = Path(os.environ[OUTPUT_ENV])
        write_forced_horizon_safety_receipt_exclusive(output, receipt)
        self.captured = True

    def _build_receipt(
        self,
        criteria: Mapping[str, Any],
        frame: int,
        sim_time_s: float,
        terminal_class: Optional[str] = None,
    ) -> Any:
        config_path = Path(os.environ["DRIVECLARIFY_TMVP_RUNTIME_CONFIG"])
        case_id = os.environ["DRIVECLARIFY_TMVP_CASE_ID"]
        case = json.loads(config_path.read_text(encoding="utf-8"))["cases"][case_id]
        contract_path = Path(os.environ[WINDOW_CONTRACT_ENV])
        builder = (
            snapshot_natural_terminal_safety
            if terminal_class is not None
            else snapshot_forced_horizon_safety
        )
        keyword = {"terminal_class": terminal_class} if terminal_class is not None else {}
        return builder(
            case_id=case_id,
            episode_id=str(case["episode_id"]),
            seed=int(case["seed"]),
            method=str(case["baseline_id"]),
            timing_bucket=os.environ[TIMING_BUCKET_ENV],
            simulation_frame=frame,
            simulation_time_s=sim_time_s,
            criteria=criteria,
            lifetime_evidence={name: item.freeze() for name, item in self.lifetimes.items()},
            scientific_window_contract_reference=str(contract_path),
            scientific_window_contract_digest=file_sha256(contract_path),
            source_evaluator_identity=(
                "leaderboard_autopilot.RouteScenario+"
                "leaderboard.scenarios.scenario_manager.ScenarioManager"
            ),
            evaluator_instance_identity=_instance_identity(self.manager),
            cell_admissibility_reference=os.environ.get(
                CELL_ADMISSIBILITY_REFERENCE_ENV
            ),
            **keyword,
        )


_INSTALLED = False


def install_runtime_hook() -> bool:
    """Install once; disabled operation imports no ScenarioRunner modules."""

    global _INSTALLED
    if _INSTALLED:
        return False
    if os.environ.get(ENABLE_ENV) != "1":
        return False
    required = (
        OUTPUT_ENV,
        TIMING_BUCKET_ENV,
        WINDOW_CONTRACT_ENV,
        "DRIVECLARIFY_TMVP_RUNTIME_CONFIG",
        "DRIVECLARIFY_TMVP_CASE_ID",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("SAFETY_SNAPSHOT_ENVIRONMENT_INCOMPLETE:" + ",".join(missing))

    ScenarioManager = resolve_authoritative_scenario_manager()
    from srunner.scenariomanager.timer import GameTime

    original_load_scenario = ScenarioManager.load_scenario

    def load_scenario_with_read_only_observer(
        self: Any,
        scenario: Any,
        agent: Any,
        route_index: Any,
        rep_number: Any,
    ) -> Any:
        result = original_load_scenario(self, scenario, agent, route_index, rep_number)
        observer = ForcedHorizonSafetyObserver(self, scenario)
        original_tick_once = self.scenario_tree.tick_once

        def tick_once_then_observe() -> Any:
            tick_result = original_tick_once()
            try:
                observer.observe_after_existing_criteria_tick(
                    int(GameTime.get_frame()),
                    float(GameTime.get_time()),
                    getattr(self.scenario_tree, "status", "INVALID"),
                )
            except Exception as exc:  # Measurement failure must not alter driving.
                print(
                    "DRIVECLARIFY_SAFETY_SNAPSHOT_OBSERVER_ERROR:" + repr(exc),
                    file=sys.stderr,
                    flush=True,
                )
            return tick_result

        self.scenario_tree.tick_once = tick_once_then_observe
        self._driveclarify_forced_horizon_safety_observer = observer
        identity_output = os.environ.get(MANAGER_IDENTITY_OUTPUT_ENV)
        if identity_output:
            try:
                _write_json_exclusive(
                    Path(identity_output),
                    build_manager_identity_evidence(
                        self, observer, scenario, ScenarioManager
                    ),
                )
            except Exception as exc:  # Evidence failure must not alter driving.
                print(
                    "DRIVECLARIFY_SCENARIOMANAGER_IDENTITY_ERROR:" + repr(exc),
                    file=sys.stderr,
                    flush=True,
                )
        return result

    ScenarioManager.load_scenario = load_scenario_with_read_only_observer
    _INSTALLED = True
    return True
