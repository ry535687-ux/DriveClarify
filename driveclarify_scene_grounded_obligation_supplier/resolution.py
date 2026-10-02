"""Production default resolution: the one entry point the runtime seam calls.

Wires the language layer, the scene layer, the frozen qualification mapper and
the frozen bridge together.  Returning ``None`` leaves candidate-local
navigation disarmed, which is the documented default-off behavior; a partial or
unqualified obligation set is never bound.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from driveclarify_candidate_local_navigation_bridge import (
    CandidateLocalNavigationMapper,
    ExistingPlannerCapability,
    GlobalRecoverabilityEvaluator,
    MissionNavigationContext,
)

from .contracts import (
    ROUTE_OWNER_ATTRIBUTE,
    SUPPLIER_IMPLEMENTATION_ID,
    SupplyResult,
    SupplyStatus,
)
from .interpretations import enumerate_semantic_interpretations
from .mission_context import (
    build_runtime_mission_navigation_context,
    build_runtime_scene_grounding_context,
)
from .supplier import SceneGroundedCandidateObligationSupplier


def resolve_scene_grounded_candidate_local_navigation(
    runtime: Any,
) -> Optional[Mapping[str, Any]]:
    """Produce the mission context and the qualified obligation set, or None."""

    mission = build_runtime_mission_navigation_context(runtime)
    if mission is None:
        record_supplier_audit(runtime, "MISSION_CONTEXT_UNAVAILABLE", ())
        return None
    # Lazy: the existing planner owner is read here, never at module import.
    capability = runtime.resolve_existing_global_route_planner_capability()
    if not isinstance(capability, ExistingPlannerCapability):
        record_supplier_audit(runtime, "EXISTING_PLANNER_CAPABILITY_INVALID", ())
        return None
    evaluator = GlobalRecoverabilityEvaluator(capability)
    mapper = CandidateLocalNavigationMapper(mission, evaluator)
    supplier = SceneGroundedCandidateObligationSupplier(mapper)

    parsed_slots = (getattr(runtime, "_receipt", {}) or {}).get("parsed_slots") or {}
    candidate_rows = list(getattr(runtime, "_bound_candidates", ()) or ())
    interpretations = enumerate_semantic_interpretations(parsed_slots, candidate_rows)
    if not interpretations:
        record_supplier_audit(runtime, "NO_LANGUAGE_DERIVED_INTERPRETATION", ())
        return None
    grounding_context = build_runtime_scene_grounding_context(runtime)

    results = tuple(
        supplier.supply(mission, interpretation, grounding_context)
        for interpretation in interpretations
    )
    qualified = tuple(
        result for result in results if result.status is SupplyStatus.QUALIFIED
    )
    record_supplier_audit(
        runtime, "SUPPLIER_EVALUATED", results, mission=mission, supplier=supplier
    )

    expected_candidate_ids = {
        str(row.get("candidate_id"))
        for row in candidate_rows
        if isinstance(row, Mapping) and row.get("candidate_id")
    }
    if not qualified or {
        result.candidate_id for result in qualified
    } != expected_candidate_ids:
        # Effective K is derived, never forced: no minimum K is asserted here.
        # The existing K>=2 grounding gate upstream and the existing atomic
        # all-or-nothing binding downstream remain the only K policies.
        return None
    resolution: dict[str, Any] = {
        "mission": mission,
        "obligations": tuple(result.obligation for result in qualified),
    }
    route_owner = getattr(
        getattr(runtime, "agent", None), ROUTE_OWNER_ATTRIBUTE, None
    )
    if route_owner is not None:
        from driveclarify_candidate_local_navigation_bridge import (  # noqa: PLC0415
            GlobalRouteReconnectionBridge,
        )

        # Built from the same evaluator, so qualification and reconnection share
        # one existing planner capability object by construction.
        resolution["reconnection_bridge"] = GlobalRouteReconnectionBridge(
            mission, evaluator, route_owner
        )
    return resolution


def _endpoint_xyz(endpoint: Any) -> Optional[list[float]]:
    """Read the endpoint's XYZ for evidence, whatever type it is spelled as."""

    for reader in (
        lambda: [
            float(getattr(endpoint, "x")),
            float(getattr(endpoint, "y")),
            float(getattr(endpoint, "z", 0.0)),
        ],
        lambda: [float(value) for value in tuple(endpoint)[:3]],
    ):
        try:
            values = reader()
        except (AttributeError, IndexError, TypeError, ValueError):
            continue
        if len(values) == 3:
            return [round(value, 3) for value in values]
    return None


def record_supplier_audit(
    runtime: Any,
    status: str,
    results: Sequence[SupplyResult],
    *,
    mission: Optional[MissionNavigationContext] = None,
    supplier: Optional[SceneGroundedCandidateObligationSupplier] = None,
) -> None:
    """Publish an evidence-only audit row; never used for control flow."""

    receipt = getattr(runtime, "_receipt", None)
    if not isinstance(receipt, dict):
        return
    endpoint = (
        None if mission is None else mission.global_destination_planner_endpoint
    )
    receipt["scene_grounded_obligation_supplier"] = {
        "implementation_id": SUPPLIER_IMPLEMENTATION_ID,
        "status": status,
        # Evidence only, never read for control flow: records which runtime type
        # the one destination endpoint actually carries into the existing planner,
        # so a type-contract mismatch is visible in the durable receipt instead of
        # only as an anonymous UNKNOWN.
        "global_destination_planner_endpoint_type": (
            None if endpoint is None else type(endpoint).__name__
        ),
        "global_destination_planner_endpoint_xyz": (
            None if endpoint is None else _endpoint_xyz(endpoint)
        ),
        "mission_context_digest": (
            None if mission is None else mission.mission_context_digest
        ),
        "global_destination_identity": (
            None if mission is None else mission.global_destination_identity
        ),
        "nominal_global_route_identity": (
            None if mission is None else mission.nominal_global_route_identity
        ),
        "supply_call_count": 0 if supplier is None else supplier.supply_count,
        "interpretation_count": len(results),
        "qualified_count": sum(
            1 for result in results if result.status is SupplyStatus.QUALIFIED
        ),
        "results": [result.to_dict() for result in results],
        "forced_top_k": False,
        "new_planner_count": 0,
        "new_map_search_system_count": 0,
    }


__all__ = [
    "record_supplier_audit",
    "resolve_scene_grounded_candidate_local_navigation",
]
