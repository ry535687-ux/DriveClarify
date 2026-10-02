"""Bounded candidate live ACT authority extension V0.

This package is additive and does not alter frozen M3 lifecycle semantics.
"""

from .contracts import (
    AUTHORITY_SCHEMA,
    AUTHORITY_VERSION,
    FEATURE_FLAG_NAME,
    MAX_CONTROL_TICKS_V0,
    MAX_RECEIPT_LIFETIME_S_V0,
    AuthorityReason,
    CandidateActAuthorityDecision,
    CandidateActAuthorityReceipt,
    CandidateActAuthorityRequest,
    CandidateExecutionContext,
    FinalExecutionAuthority,
    FinalExecutionAuthorityDecision,
)
from .resolver import (
    CandidateLiveActAuthorityResolverV0,
    FinalExecutionAuthorityResolverV0,
)
from .tagged_subject_v1 import (
    ActAuthorityDecisionV1,
    ActAuthorityReceiptV1,
    ActAuthorityRequestV1,
    ActAuthoritySubjectType,
    ActAuthoritySubjectV1,
    ActExecutionContextV1,
    ActiveAuthorityMember,
    FinalExecutionAuthorityResolverV1,
    LiveActAuthorityResolverV1,
    build_shared_subject,
    build_unique_subject,
    validate_subject,
)

__all__ = [
    "AUTHORITY_SCHEMA",
    "AUTHORITY_VERSION",
    "FEATURE_FLAG_NAME",
    "MAX_CONTROL_TICKS_V0",
    "MAX_RECEIPT_LIFETIME_S_V0",
    "AuthorityReason",
    "CandidateActAuthorityDecision",
    "CandidateActAuthorityReceipt",
    "CandidateActAuthorityRequest",
    "CandidateExecutionContext",
    "CandidateLiveActAuthorityResolverV0",
    "FinalExecutionAuthority",
    "FinalExecutionAuthorityDecision",
    "FinalExecutionAuthorityResolverV0",
    "ActAuthorityDecisionV1",
    "ActAuthorityReceiptV1",
    "ActAuthorityRequestV1",
    "ActAuthoritySubjectType",
    "ActAuthoritySubjectV1",
    "ActExecutionContextV1",
    "ActiveAuthorityMember",
    "FinalExecutionAuthorityResolverV1",
    "LiveActAuthorityResolverV1",
    "build_shared_subject",
    "build_unique_subject",
    "validate_subject",
]
