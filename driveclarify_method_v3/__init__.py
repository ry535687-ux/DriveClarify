"""DriveClarify Method V3: clarification-conditioned route binding."""

from .bridge import (
    SelectedBranchRouteBindingBridgeV3,
    SelectedRouteBindingReceiptV3,
    materialize_route_derived_forward_binding_v3,
)
from .contracts import (
    FEATURE_FLAG,
    MethodV3Phase,
    MethodV3PhaseOwner,
    OpportunityEquivalenceResolutionV3,
    resolve_selected_opportunity_equivalence_v3,
)
from .execution import RouteBoundManeuverExecutionV3
from .native_route import (
    ResolvedSelectedLocalRoute,
    RouteTopologyIdentityV3,
    SelectedConnectorRelation,
    SelectedPlanAdmissibility,
    SelectedPlanAdmissibilityInput,
    SelectedPlanAdmissibilityResult,
    verify_selected_plan_admissibility,
)
from .connector_phase_r2 import (
    CONSOLIDATED_PHASE_OWNER_IDENTITY,
    ConnectorPhaseOwnerR2,
    ConnectorPhaseTransactionR2,
    ConnectorPhysicalPhaseR2,
    classify_selected_connector_membership_r2,
)

__all__ = [
    "FEATURE_FLAG",
    "CONSOLIDATED_PHASE_OWNER_IDENTITY",
    "ConnectorPhaseOwnerR2",
    "ConnectorPhaseTransactionR2",
    "ConnectorPhysicalPhaseR2",
    "classify_selected_connector_membership_r2",
    "MethodV3Phase",
    "MethodV3PhaseOwner",
    "OpportunityEquivalenceResolutionV3",
    "RouteBoundManeuverExecutionV3",
    "ResolvedSelectedLocalRoute",
    "RouteTopologyIdentityV3",
    "SelectedConnectorRelation",
    "SelectedPlanAdmissibility",
    "SelectedPlanAdmissibilityInput",
    "SelectedPlanAdmissibilityResult",
    "SelectedBranchRouteBindingBridgeV3",
    "SelectedRouteBindingReceiptV3",
    "materialize_route_derived_forward_binding_v3",
    "resolve_selected_opportunity_equivalence_v3",
    "verify_selected_plan_admissibility",
]
