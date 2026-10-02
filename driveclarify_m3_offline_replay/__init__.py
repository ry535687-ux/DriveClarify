"""Minimal deterministic non-blind offline replay over the frozen M3 core."""

from .adapters import ADAPTER_ID, ADAPTER_VERSION, adapt_record
from .contracts import (
    AdapterRejection, DESIGN_VERSION, EPISODE_SCHEMA, EpisodeRejection,
    M2BDecisionEnvelope, ORACLE_SCHEMA, RECORD_SCHEMA, ReplayEpisode,
    ReplayEvaluationResult, ReplayOracle, ReplayRecord, ReplayStepResult,
    ReplayTrace, SelectedAction, SourceTier, TimeMappingMode,
)
from .evaluation import evaluate_trace
from .runner import RUNNER_VERSION, run_episode
from .recorded_campaign import (
    ADMISSION_SCHEMA, STRUCTURED_ADMISSION_REJECTION,
    admit_recorded_decision, audit_legacy_decision_results,
    deterministic_double_replay, deterministic_select, file_sha256,
    legacy_m2b_projection, source_record_sha256,
)
from .recorded_capture import (
    CAPTURE_PRODUCER_VERSION, CAPTURE_SCHEDULE_SCHEMA,
    DERIVED_OFFLINE_SNAPSHOT_ID, IDENTITY_ACTION_PRESERVED,
    LEGACY_FALLBACK_ALIAS_VERDICT, LIFECYCLE_ADMISSION_SCHEMA,
    LIFECYCLE_DATASET_SCHEMA, LIFECYCLE_RECORD_SCHEMA,
    NEW_CAPTURE_EXPLICIT_CANONICAL_ACTION_EMISSION,
    LifecycleAwareM2BDecisionRecordV1, admit_lifecycle_aware_record,
    build_explicit_capture_schedule, build_recorded_decision_episode,
    lifecycle_aware_dataset_document, load_and_capture_nonblind_records,
)
from .serialization import (
    canonical_json_bytes, canonical_sha256, strict_json_dumps, strict_json_loads,
)

__all__ = [
    "ADAPTER_ID", "ADAPTER_VERSION", "AdapterRejection", "DESIGN_VERSION",
    "EPISODE_SCHEMA", "EpisodeRejection", "M2BDecisionEnvelope", "ORACLE_SCHEMA",
    "RECORD_SCHEMA", "RUNNER_VERSION", "ReplayEpisode", "ReplayEvaluationResult",
    "ReplayOracle", "ReplayRecord", "ReplayStepResult", "ReplayTrace",
    "SelectedAction", "SourceTier", "TimeMappingMode", "adapt_record",
    "canonical_json_bytes", "canonical_sha256", "evaluate_trace", "run_episode",
    "strict_json_dumps", "strict_json_loads",
    "ADMISSION_SCHEMA", "STRUCTURED_ADMISSION_REJECTION",
    "admit_recorded_decision", "audit_legacy_decision_results",
    "deterministic_double_replay", "deterministic_select", "file_sha256",
    "legacy_m2b_projection", "source_record_sha256",
    "CAPTURE_PRODUCER_VERSION", "CAPTURE_SCHEDULE_SCHEMA",
    "DERIVED_OFFLINE_SNAPSHOT_ID", "IDENTITY_ACTION_PRESERVED",
    "LEGACY_FALLBACK_ALIAS_VERDICT", "LIFECYCLE_ADMISSION_SCHEMA",
    "LIFECYCLE_DATASET_SCHEMA", "LIFECYCLE_RECORD_SCHEMA",
    "NEW_CAPTURE_EXPLICIT_CANONICAL_ACTION_EMISSION",
    "LifecycleAwareM2BDecisionRecordV1", "admit_lifecycle_aware_record",
    "build_explicit_capture_schedule", "build_recorded_decision_episode",
    "lifecycle_aware_dataset_document", "load_and_capture_nonblind_records",
]
