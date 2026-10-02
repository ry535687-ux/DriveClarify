"""DriveClarify Method V2.8 public surface."""

from .contracts import SelectedPlanTransactionV28
from .execution import TopologyLockedManeuverExecutionV28
from .gates import (
    PlanningAmbiguityAdmissionEvidenceV28,
    SelectedMotionEligibilityEvidenceV28,
    assess_planning_ambiguity_admission_v28,
    assess_selected_motion_eligibility_v28,
)
from .refresh import (
    TopologyLockedRefreshMaterializationV28,
    materialize_topology_locked_refresh_v28,
)

FEATURE_FLAG = "DRIVECLARIFY_METHOD_V2_8_TOPOLOGY_LOCKED_SELECTED_HANDOVER"
METHOD_VERSION = "DriveClarify Method V2.8"

__all__ = [
    "FEATURE_FLAG",
    "METHOD_VERSION",
    "PlanningAmbiguityAdmissionEvidenceV28",
    "SelectedMotionEligibilityEvidenceV28",
    "SelectedPlanTransactionV28",
    "TopologyLockedManeuverExecutionV28",
    "TopologyLockedRefreshMaterializationV28",
    "assess_planning_ambiguity_admission_v28",
    "assess_selected_motion_eligibility_v28",
    "materialize_topology_locked_refresh_v28",
]
