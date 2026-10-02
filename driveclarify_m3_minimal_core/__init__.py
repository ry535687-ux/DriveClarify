"""Pure offline reference implementation of the frozen M3 minimal core.

This package has no production-runtime or physical-control integration.
"""

from .contracts import (
    AuditReason,
    AuditRecord,
    ControlAuthority,
    EventType,
    EvidenceGrade,
    HoldingLease,
    LifecycleState,
    MinimalM3Event,
    MinimalM3State,
    ProcessingResult,
    ReductionResult,
    TraceRecord,
)
from .reducer import (
    CORE_ID,
    TRANSITION_IDS,
    check_invariants,
    default_state,
    event_idempotency_key,
    is_finite_real,
    lease_temporally_valid,
    reduce_event,
    reduce_event_group,
    reduce_events,
    resolve_authority,
)
from .serialization import (
    canonical_json_bytes,
    canonical_sha256,
    strict_json_dumps,
    strict_json_loads,
)

__all__ = [
    "AuditReason", "AuditRecord", "ControlAuthority", "EventType",
    "EvidenceGrade", "HoldingLease", "LifecycleState", "MinimalM3Event",
    "MinimalM3State", "ProcessingResult", "ReductionResult", "TraceRecord", "CORE_ID",
    "TRANSITION_IDS", "canonical_json_bytes", "canonical_sha256",
    "check_invariants", "default_state", "event_idempotency_key",
    "is_finite_real", "lease_temporally_valid", "reduce_event",
    "reduce_event_group", "reduce_events", "resolve_authority",
    "strict_json_dumps", "strict_json_loads",
]
