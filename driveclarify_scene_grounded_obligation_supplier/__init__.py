"""Ambiguity-generic scene-grounded candidate obligation supplier.

The package converts one already-identified, language-derived semantic
interpretation into at most one ``CandidateLocalNavigationObligation``.  It owns
no planner, no path search, no ambiguity detection and no language model.

Layering, enforced by the import graph rather than by convention alone:

``contracts``        types and constants only
``interpretations``  language layer; imports no map/scene/topology symbol, so the
                     value space of an unresolved slot is necessarily computed
                     before any map exists
``scene_grounding``  scene layer; answers "does this stated constraint have a
                     matching bounded local route?" and never produces a reading
``supplier``         dispatch on ``obligation_type`` into the scene layer, then
                     delegate qualification to the frozen mapper
``mission_context``  MissionContext producer plus scene-evidence assembler
``resolution``       the single entry point the production runtime seam calls

Realized obligation families are route/topology-affecting only:
``MANEUVER_DIRECTION`` and ``EXECUTION_LOCATION``.  Every other family returns
``UNSUPPORTED_OBLIGATION_TYPE`` rather than a guessed obligation.
"""

from __future__ import annotations

from .contracts import (
    DEFAULT_LOCAL_HORIZON_MARGIN_M,
    DirectionBranchCandidate,
    FEATURE_FLAG,
    ObligationType,
    ROUTE_OWNER_ATTRIBUTE,
    STRAIGHT_YAW_TOLERANCE_DEG,
    SUPPLIER_IMPLEMENTATION_ID,
    SceneGroundingContext,
    SemanticInterpretation,
    SupplierContractError,
    SupplyResult,
    SupplyStatus,
)
from .interpretations import (
    DIRECTION_VALUE_SPACE,
    OBLIGATION_TYPE_FIELD,
    ORDINAL_TO_ROUTE_ORDER,
    SEMANTIC_CONSTRAINT_FIELD,
    direction_value_space,
    enumerate_semantic_interpretations,
    language_semantic_authority,
    ordinal_value_space,
    resolved_direction_from_language,
)
from .mission_context import (
    build_runtime_mission_navigation_context,
    build_runtime_scene_grounding_context,
)
from .resolution import (
    record_supplier_audit,
    resolve_scene_grounded_candidate_local_navigation,
)
from .scene_grounding import (
    branch_identity,
    classify_junction_exit_direction,
    local_branch,
    read_direction_branches,
)
from .supplier import SceneGroundedCandidateObligationSupplier


__all__ = [
    "DEFAULT_LOCAL_HORIZON_MARGIN_M",
    "DIRECTION_VALUE_SPACE",
    "DirectionBranchCandidate",
    "FEATURE_FLAG",
    "OBLIGATION_TYPE_FIELD",
    "ORDINAL_TO_ROUTE_ORDER",
    "ObligationType",
    "ROUTE_OWNER_ATTRIBUTE",
    "SEMANTIC_CONSTRAINT_FIELD",
    "SUPPLIER_IMPLEMENTATION_ID",
    "STRAIGHT_YAW_TOLERANCE_DEG",
    "SceneGroundedCandidateObligationSupplier",
    "SceneGroundingContext",
    "SemanticInterpretation",
    "SupplierContractError",
    "SupplyResult",
    "SupplyStatus",
    "branch_identity",
    "build_runtime_mission_navigation_context",
    "build_runtime_scene_grounding_context",
    "classify_junction_exit_direction",
    "direction_value_space",
    "enumerate_semantic_interpretations",
    "language_semantic_authority",
    "local_branch",
    "ordinal_value_space",
    "read_direction_branches",
    "record_supplier_audit",
    "resolve_scene_grounded_candidate_local_navigation",
    "resolved_direction_from_language",
]
