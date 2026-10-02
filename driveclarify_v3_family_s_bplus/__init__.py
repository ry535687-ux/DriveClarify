"""DriveClarify V3 Family-S B+ planner transaction foundation.

This package is deliberately simulator-runtime independent.  It contains no
CARLA lifecycle owner, sensor owner, model path, or online quartet builder.
"""

from .adapter import (
    BranchLifecycle,
    BranchTransactionError,
    ObjectPlannerHost,
    PDMBranchAdapter,
    PreparedNativePlanner,
)
from .builder import SealedPDMBranchPlanBuilder
from .contracts import RoutePoint, SealedPDMBranchPlan
from .recorder import ControlSnapshot, OfficialRuntimeSample, ThinRecorder

__all__ = (
    "BranchLifecycle",
    "BranchTransactionError",
    "ControlSnapshot",
    "ObjectPlannerHost",
    "OfficialRuntimeSample",
    "PDMBranchAdapter",
    "PreparedNativePlanner",
    "RoutePoint",
    "SealedPDMBranchPlan",
    "SealedPDMBranchPlanBuilder",
    "ThinRecorder",
)
