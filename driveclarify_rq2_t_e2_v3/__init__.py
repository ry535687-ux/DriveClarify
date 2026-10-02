"""E2_TRACKED_ASSOCIATION_V3 public API."""

from .association import AssociationThresholds, PRECALIBRATION_THRESHOLDS, associate_candidates_to_tracks
from .calibration import select_thresholds
from .contracts import AcquisitionSchedule, CandidateObjectSpec, parse_certified_candidate, validate_candidate_set
from .memory import E2LineageMemory
from .method import EvidenceEnabledTrackedAssociationMethodV3
from .provider import provide_grounding_e2_v3
from .tracker import PersistentMultiObjectTracker, RuntimeDetection

__all__ = [
    "AcquisitionSchedule", "AssociationThresholds", "CandidateObjectSpec",
    "E2LineageMemory", "EvidenceEnabledTrackedAssociationMethodV3",
    "PRECALIBRATION_THRESHOLDS", "PersistentMultiObjectTracker", "RuntimeDetection",
    "associate_candidates_to_tracks", "parse_certified_candidate",
    "provide_grounding_e2_v3", "select_thresholds", "validate_candidate_set",
]
