"""Read-only/default-off adapters for existing SimLingo owners."""

from __future__ import annotations

import ast
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

from .authority import AuthorityExport
from .canonical import bytes_sha256, canonical_sha256
from .frozen_registry import (
    FROZEN_SIMLINGO_AGENT_SOURCE,
    assert_frozen_t_b5_registry,
)
from .models import GlobalTask, RouteRow


def _road_option_token(value: object) -> str:
    if hasattr(value, "name"):
        return str(value.name)
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _route_rows(
    route: Sequence[tuple[object, object]], distances: Sequence[float]
) -> tuple[RouteRow, ...]:
    if len(route) != len(distances):
        raise ValueError("SIMLINGO_ROUTE_DISTANCE_LENGTH_MISMATCH")
    rows: list[RouteRow] = []
    for index, ((point, option), distance) in enumerate(zip(route, distances)):
        values = tuple(float(value) for value in point)
        if len(values) < 3 or not all(math.isfinite(value) for value in values[:3]):
            raise ValueError("SIMLINGO_ROUTE_POSITION_INVALID")
        rows.append(
            RouteRow(
                x_m=values[0],
                y_m=values[1],
                z_m=values[2],
                road_option=_road_option_token(option),
                distance_from_previous_m=0.0 if index == 0 else float(distance),
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class AuthorityMetadataExport:
    """One boundary-owned export for non-RoutePlanner authority components."""

    source_frame: int
    source_sim_time_s: float
    current_maneuver_identity: str
    current_branch_or_connector_identity: str
    route_owner_write_count: int
    route_switch_lifecycle: object
    cache_state: object
    controller_pid_state: object
    component_versions: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        required = {
            "mission_context",
            "maneuver_owner",
            "controller_owner",
            "cache_owner",
            "route_switch_owner",
            "control_owner",
        }
        supplied = {name for name, _ in self.component_versions}
        missing = required - supplied
        if missing:
            raise ValueError(
                "AUTHORITY_METADATA_COMPONENT_VERSION_MISSING:"
                + ",".join(sorted(missing))
            )


class SimLingoReadOnlyAuthorityAdapter:
    """Capture RoutePlanner state under its existing ``_route_lock``.

    Callers supply experiment metadata owners because the route planner does not
    own maneuver, connector, simulator-frame, controller, or mission semantics.
    The adapter does not create defaults for those fields.
    """

    def __init__(
        self,
        route_planner: Any,
        *,
        global_task: GlobalTask,
        metadata_export: Callable[[], AuthorityMetadataExport],
        control_owner_identity: str,
    ) -> None:
        self._planner = route_planner
        self._global_task = global_task
        self._metadata_export = metadata_export
        self._control_owner_identity = control_owner_identity

    def export_atomic(self) -> AuthorityExport:
        planner = self._planner
        lock = getattr(planner, "_route_lock", None)
        if lock is None:
            raise AttributeError("SIMLINGO_ROUTE_LOCK_MISSING")
        with lock:
            generation = int(planner.online_update_generation)
            metadata = self._metadata_export()
            if not isinstance(metadata, AuthorityMetadataExport):
                raise TypeError("AUTHORITY_METADATA_EXPORT_INVALID")
            active_route = tuple(planner.route)
            active_distances = tuple(planner.route_distances)
            saved_route = tuple(planner.saved_route)
            saved_distances = tuple(planner.saved_route_distances)
            full_rows = _route_rows(saved_route, saved_distances)
            active_rows = _route_rows(active_route, active_distances)
            route_identity = str(planner.active_route_identity)
            frame = int(metadata.source_frame)
            sim_time = float(metadata.source_sim_time_s)
            planner_owner = type(planner).__module__ + "." + type(planner).__qualname__
            suffix_identity = canonical_sha256(
                {
                    "route_identity": route_identity,
                    "route_generation": generation,
                    "active_suffix": active_rows,
                }
            )
            return AuthorityExport(
                route_identity=route_identity,
                route_generation=generation,
                global_task=self._global_task,
                full_route=full_rows,
                active_suffix=active_rows,
                active_suffix_identity=suffix_identity,
                current_maneuver_identity=str(metadata.current_maneuver_identity),
                current_branch_or_connector_identity=str(
                    metadata.current_branch_or_connector_identity
                ),
                route_owner_identity=planner_owner,
                active_suffix_owner_identity=planner_owner,
                planner_owner_identity=planner_owner,
                control_owner_identity=self._control_owner_identity,
                source_frame=frame,
                source_sim_time_s=sim_time,
                boundary_identity="PRE_UPDATE_AUTHORITY_BOUNDARY",
                component_versions=(
                    ("route_identity", generation),
                    ("full_route", generation),
                    ("active_suffix", generation),
                    ("route_distances", generation),
                ) + tuple(metadata.component_versions),
                last_consumed_route_identity=(
                    None
                    if planner.last_consumed_route_identity is None
                    else str(planner.last_consumed_route_identity)
                ),
                saved_route_state_sha256=canonical_sha256(
                    {"route": full_rows, "distances": saved_distances}
                ),
                active_route_distance_state_sha256=canonical_sha256(
                    {"route": active_rows, "distances": active_distances}
                ),
                route_owner_write_count=int(metadata.route_owner_write_count),
                route_switch_lifecycle_sha256=canonical_sha256(
                    metadata.route_switch_lifecycle
                ),
                cache_generation_sha256=canonical_sha256(metadata.cache_state),
                controller_pid_state_sha256=canonical_sha256(
                    metadata.controller_pid_state
                ),
            )


@dataclass(frozen=True)
class FrozenVLAInterfaceQualification:
    status: str
    source_sha256: str
    findings: tuple[str, ...]


def qualify_frozen_simlingo_history_interface(
    source_path: str | Path,
) -> FrozenVLAInterfaceQualification:
    """Statically prove the existing single-forward scalar prompt seam exists."""

    path = Path(source_path).resolve()
    if path != FROZEN_SIMLINGO_AGENT_SOURCE.resolve():
        raise RuntimeError("T_B5_QUALIFIER_SOURCE_NOT_FROZEN")
    registry_sha256 = assert_frozen_t_b5_registry()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    agent_class = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "LingoAgent"
        ),
        None,
    )
    if agent_class is None:
        raise RuntimeError("T_B5_FROZEN_LINGO_AGENT_CLASS_MISSING")
    methods = {
        node.name: node
        for node in agent_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    setup = methods.get("setup")
    tick = methods.get("tick")
    run_step = methods.get("run_step")
    if setup is None or tick is None or run_step is None:
        raise RuntimeError("T_B5_FROZEN_AGENT_METHOD_MISSING")
    custom_prompt_initialized = False
    custom_prompt_read = False
    tokenizer_called = False
    for node in ast.walk(setup):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr == "custom_prompt"
                ):
                    custom_prompt_initialized = True
    for node in ast.walk(tick):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr == "custom_prompt"
            and isinstance(node.ctx, ast.Load)
        ):
            custom_prompt_read = True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == "tokenizer"
        ):
            tokenizer_called = True
    setup_source = ast.get_source_segment(source, setup) or ""
    tick_source = ast.get_source_segment(source, tick) or ""
    run_step_source = ast.get_source_segment(source, run_step) or ""
    checkpoint_loaded = "torch.load(self.config_path)" in setup_source
    state_loaded = "self.model.load_state_dict(checkpoint_state" in setup_source
    one_normal_forward_site = run_step_source.count("self.model(model_input)") == 1
    findings = (
        "FROZEN_REGISTRY_SHA256=" + registry_sha256,
        "SCALAR_CUSTOM_PROMPT_INITIALIZED=" + str(custom_prompt_initialized).lower(),
        "TICK_CUSTOM_PROMPT_READ=" + str(custom_prompt_read).lower(),
        "TICK_TOKENIZER_CALLED_ON_COMPOSED_PROMPT=" + str(tokenizer_called).lower(),
        "SETUP_LOADS_EXACT_CONFIG_PATH=" + str(checkpoint_loaded).lower(),
        "SETUP_BINDS_STATE_DICT=" + str(state_loaded).lower(),
        "RUN_STEP_NORMAL_FORWARD_SITE_COUNT=" + str(int(one_normal_forward_site)),
        "TICK_SOURCE_SHA256=" + bytes_sha256(tick_source.encode("utf-8")),
        "RUN_STEP_SOURCE_SHA256=" + bytes_sha256(run_step_source.encode("utf-8")),
        "QUALIFIER_SCOPE=EXACT_FROZEN_REGISTRY_LOAD_TICK_PROMPT_TOKENIZER_AND_FORWARD",
    )
    qualified = all(
        (
            custom_prompt_initialized,
            custom_prompt_read,
            tokenizer_called,
            checkpoint_loaded,
            state_loaded,
            one_normal_forward_site,
        )
    )
    return FrozenVLAInterfaceQualification(
        status=(
            "T_B5_INTERFACE_QUALIFIED"
            if qualified
            else "T_B5_NOT_IMPLEMENTABLE_WITH_FROZEN_VLA_INTERFACE"
        ),
        source_sha256=bytes_sha256(source.encode("utf-8")),
        findings=findings,
    )
