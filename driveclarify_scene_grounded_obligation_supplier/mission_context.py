"""MissionContext producer plus the scene-evidence assembler.

Both functions read state production already owns.  No second destination owner,
route owner or mission manager is created here, and the existing planner owner is
never resolved at module import time.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from driveclarify_candidate_local_navigation_bridge import (
    MissionNavigationContext,
    canonical_navigation_digest,
)

from .contracts import DirectionBranchCandidate, SceneGroundingContext
from .scene_grounding import read_direction_branches


def _planner_native_endpoint(world_xyz: tuple[float, ...]) -> Any:
    """Spell one already-owned XYZ in the type the existing planner requires.

    The numbers are not touched: the same floats go in and come back out.  Only
    the runtime object handed to the existing ``trace_route`` owner changes, and
    ``carla`` is imported lazily here so no simulator import happens at module
    import time.  When ``carla`` is unavailable (offline/CPU contexts) the
    numeric triple is returned unchanged.
    """

    try:
        import carla  # noqa: PLC0415

        return carla.Location(
            x=float(world_xyz[0]), y=float(world_xyz[1]), z=float(world_xyz[2])
        )
    except (AttributeError, ImportError, IndexError):
        return world_xyz


def build_runtime_mission_navigation_context(
    runtime: Any,
) -> Optional[MissionNavigationContext]:
    """Build the mission context from the state production already owns.

    The global destination comes from the agent-owned detached dense global
    route; the nominal route identity comes from the route version the runtime
    already computed.  Nothing is invented, and a missing owner yields ``None``
    rather than a substituted default.
    """

    from driveclarify_phase_b_evidence_completion_v1.runtime import (  # noqa: PLC0415
        _detached_dense_route,
    )

    nominal_route_identity = getattr(runtime, "_runtime_route_version", None)
    if not nominal_route_identity:
        return None
    rows, _source_attribute = _detached_dense_route(getattr(runtime, "agent", None))
    if not rows:
        return None
    final_world_xyz = tuple(float(value) for value in rows[-1][0])
    # The canonical numeric destination identity is derived from the numbers, so
    # it is independent of how the endpoint is spelled for the planner.
    destination_identity = "global-destination-" + canonical_navigation_digest(
        {"final_world_xyz": [round(value, 3) for value in final_world_xyz]}
    )[:24]
    return MissionNavigationContext(
        global_destination_identity=destination_identity,
        global_destination_planner_endpoint=_planner_native_endpoint(final_world_xyz),
        nominal_global_route_identity=str(nominal_route_identity),
    )


def build_runtime_scene_grounding_context(
    runtime: Any,
    *,
    opportunity_index: Optional[int] = None,
) -> SceneGroundingContext:
    """Assemble existing route/topology evidence for the current observation."""

    from driveclarify_grounded_language_v1_extension_e1_r1.runtime import (  # noqa: PLC0415
        _live_map,
    )

    receipt = getattr(runtime, "_receipt", {}) or {}
    opportunities = tuple(
        row
        for row in (receipt.get("maneuver_opportunities") or ())
        if isinstance(row, Mapping)
    )
    try:
        route_rows = tuple(runtime.topology_enumerator._route_rows(runtime._route()))
    except Exception:
        route_rows = ()
    index = opportunity_index
    if index is None and opportunities:
        try:
            index = int(opportunities[0]["route_opportunity_index"])
        except (KeyError, TypeError, ValueError):
            index = None
    branches: tuple[DirectionBranchCandidate, ...] = ()
    if index is not None:
        branches = read_direction_branches(_live_map(), route_rows, int(index))
    nominal_direction = None
    if opportunities:
        value = opportunities[0].get("maneuver_direction")
        nominal_direction = None if value is None else str(value)
    return SceneGroundingContext(
        route_rows=route_rows,
        maneuver_opportunities=opportunities,
        direction_branches=branches,
        nominal_direction=nominal_direction,
    )


__all__ = [
    "build_runtime_mission_navigation_context",
    "build_runtime_scene_grounding_context",
]
