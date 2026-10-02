"""Read-only post-execution evaluator for the frozen RQ2-T V3 global endpoint.

This module composes authoritative evidence that already exists after an
episode.  It does not inspect or mutate runtime policy state and it deliberately
does not import or alias the V1 final-G completion endpoint.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .admissibility import validate_cell_admissibility_receipt
from .canonical import canonical_sha256


V3_RECEIPT_SCHEMA = "driveclarify.rq2_t.global_task_preservation.receipt.v3"
V3_CONJUNCT_SCHEMA = "driveclarify.rq2_t.global_task_preservation.conjunct.v3"
V3_CONTRACT_VERSION = "driveclarify.rq2.global_task.contract.v3"
V3_CONTRACT_REFERENCE = (
    "reports/driveclarify_rq2_two_timescale_global_task_contract_v3_freeze/"
    "RQ2_GLOBAL_TASK_CONTRACT_V3_FROZEN.md"
)
V3_CONTRACT_DIGEST = (
    "64f78cb4ae07e52f246a6e212512e09a84a7a2aab4e6ccd147d3dddb519d25e1"
)
G_CONTRACT_V2_REFERENCE = (
    "reports/driveclarify_rq2_t_mvp_practical_dev_v1/"
    "RQ2_T_MVP_G_CONTRACT_V2.md"
)
G_CONTRACT_V2_DIGEST = (
    "4d166de88f0d3f9c1ef329e49d3b3f6ab1f800041cce59ac3980b662ad695971"
)
RQ2_T_WINDOW_SECONDS = 35
PRODUCTION_PHASE = "POST_EXECUTION_ONLY"
RUNTIME_POLICY_ACCESS = False
RQ2_L_IMPLEMENTATION_STATUS = "UNIMPLEMENTED_UNAUTHORIZED"


class EvidenceState(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    RIGHT_CENSORED = "RIGHT_CENSORED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PreservationState(str, Enum):
    KNOWN_TRUE = "KNOWN_TRUE"
    KNOWN_FALSE = "KNOWN_FALSE"
    UNKNOWN = "UNKNOWN"
    RIGHT_CENSORED = "RIGHT_CENSORED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ConjunctName(str, Enum):
    SAME_G_IDENTITY = "SAME_G_IDENTITY"
    NO_WRONG_DESTINATION_SUBSTITUTION = "NO_WRONG_DESTINATION_SUBSTITUTION"
    AUTHORITATIVE_LEGAL_TOPOLOGY_AWARE_CONTINUATION = (
        "AUTHORITATIVE_LEGAL_TOPOLOGY_AWARE_CONTINUATION"
    )
    ROUTE_AUTHORITY_CHANGE_LATER_CONSUMED = (
        "ROUTE_AUTHORITY_CHANGE_LATER_CONSUMED"
    )
    APPLICABLE_FROZEN_RECOVERY = "APPLICABLE_FROZEN_RECOVERY"
    APPLICABLE_FROZEN_REJOIN = "APPLICABLE_FROZEN_REJOIN"


V3_CONJUNCT_ORDER = tuple(item.value for item in ConjunctName)


@dataclass(frozen=True)
class GlobalTaskBinding:
    """One exact semantic-G plus G-V2 endpoint-digest binding."""

    role: str
    global_task_identity: str | None
    endpoint_digest: str | None

    def __post_init__(self) -> None:
        if not self.role:
            raise ValueError("V3_G_BINDING_ROLE_MISSING")
        if self.endpoint_digest is not None and len(self.endpoint_digest) != 64:
            raise ValueError("V3_G_BINDING_ENDPOINT_DIGEST_INVALID")


@dataclass(frozen=True)
class V3ConjunctEvidence:
    schema_version: str
    name: str
    mandatory: bool
    applicable: bool
    evidence_state: str
    evidence_source: str | None
    evidence_reference: str | None
    evidence_digest: str | None
    producer: str | None
    source_frame_start: int | None
    source_frame_end: int | None
    source_time_start_s: float | None
    source_time_end_s: float | None
    source_clock: str | None
    value: bool | None
    reason_code: str | None

    def __post_init__(self) -> None:
        if self.schema_version != V3_CONJUNCT_SCHEMA:
            raise ValueError("V3_CONJUNCT_SCHEMA_INVALID")
        ConjunctName(self.name)
        state = EvidenceState(self.evidence_state)
        if self.mandatory is not True:
            raise ValueError("V3_CONJUNCT_MUST_BE_MANDATORY")
        if self.evidence_digest is not None and len(self.evidence_digest) != 64:
            raise ValueError("V3_CONJUNCT_EVIDENCE_DIGEST_INVALID")
        if self.source_frame_start is not None and self.source_frame_start < 0:
            raise ValueError("V3_CONJUNCT_SOURCE_FRAME_INVALID")
        if self.source_frame_end is not None and self.source_frame_end < 0:
            raise ValueError("V3_CONJUNCT_SOURCE_FRAME_INVALID")
        if (
            self.source_frame_start is not None
            and self.source_frame_end is not None
            and self.source_frame_end < self.source_frame_start
        ):
            raise ValueError("V3_CONJUNCT_SOURCE_FRAME_RANGE_INVALID")
        if (
            self.source_time_start_s is not None
            and self.source_time_end_s is not None
            and self.source_time_end_s < self.source_time_start_s
        ):
            raise ValueError("V3_CONJUNCT_SOURCE_TIME_RANGE_INVALID")
        provenance_complete = all(
            (self.evidence_source, self.evidence_reference, self.evidence_digest, self.producer)
        )
        if state is EvidenceState.KNOWN:
            if not self.applicable or self.value not in (True, False):
                raise ValueError("V3_KNOWN_CONJUNCT_VALUE_INVALID")
            if not provenance_complete:
                raise ValueError("V3_KNOWN_CONJUNCT_PROVENANCE_INCOMPLETE")
            if self.reason_code is not None:
                raise ValueError("V3_KNOWN_CONJUNCT_REASON_FORBIDDEN")
        elif state is EvidenceState.NOT_APPLICABLE:
            if self.applicable or self.value is not None or not self.reason_code:
                raise ValueError("V3_NOT_APPLICABLE_CONJUNCT_INVALID")
            if not provenance_complete:
                raise ValueError("V3_NOT_APPLICABLE_AUTHORITY_INCOMPLETE")
        else:
            if not self.applicable or self.value is not None or not self.reason_code:
                raise ValueError("V3_UNRESOLVED_CONJUNCT_INVALID")


def known_conjunct(
    name: ConjunctName | str,
    value: bool,
    *,
    evidence_source: str,
    evidence_reference: str,
    evidence_digest: str,
    producer: str,
    source_frame_start: int | None = None,
    source_frame_end: int | None = None,
    source_time_start_s: float | None = None,
    source_time_end_s: float | None = None,
    source_clock: str = "CARLA_SIMULATION_TIME",
) -> V3ConjunctEvidence:
    return V3ConjunctEvidence(
        schema_version=V3_CONJUNCT_SCHEMA,
        name=ConjunctName(name).value,
        mandatory=True,
        applicable=True,
        evidence_state=EvidenceState.KNOWN.value,
        evidence_source=evidence_source,
        evidence_reference=evidence_reference,
        evidence_digest=evidence_digest,
        producer=producer,
        source_frame_start=source_frame_start,
        source_frame_end=source_frame_end,
        source_time_start_s=source_time_start_s,
        source_time_end_s=source_time_end_s,
        source_clock=source_clock,
        value=bool(value),
        reason_code=None,
    )


def unresolved_conjunct(
    name: ConjunctName | str,
    *,
    state: EvidenceState | str,
    reason_code: str,
    evidence_source: str | None = None,
    evidence_reference: str | None = None,
    evidence_digest: str | None = None,
    producer: str | None = None,
    source_frame_start: int | None = None,
    source_frame_end: int | None = None,
    source_time_start_s: float | None = None,
    source_time_end_s: float | None = None,
    source_clock: str | None = None,
) -> V3ConjunctEvidence:
    resolved_state = EvidenceState(state)
    if resolved_state not in {EvidenceState.UNKNOWN, EvidenceState.RIGHT_CENSORED}:
        raise ValueError("V3_UNRESOLVED_FACTORY_STATE_INVALID")
    return V3ConjunctEvidence(
        schema_version=V3_CONJUNCT_SCHEMA,
        name=ConjunctName(name).value,
        mandatory=True,
        applicable=True,
        evidence_state=resolved_state.value,
        evidence_source=evidence_source,
        evidence_reference=evidence_reference,
        evidence_digest=evidence_digest,
        producer=producer,
        source_frame_start=source_frame_start,
        source_frame_end=source_frame_end,
        source_time_start_s=source_time_start_s,
        source_time_end_s=source_time_end_s,
        source_clock=source_clock,
        value=None,
        reason_code=reason_code,
    )


def not_applicable_conjunct(
    name: ConjunctName | str,
    *,
    reason_code: str,
    evidence_source: str,
    evidence_reference: str,
    evidence_digest: str,
    producer: str,
    source_frame_start: int | None = None,
    source_frame_end: int | None = None,
    source_time_start_s: float | None = None,
    source_time_end_s: float | None = None,
    source_clock: str = "CARLA_SIMULATION_TIME",
) -> V3ConjunctEvidence:
    return V3ConjunctEvidence(
        schema_version=V3_CONJUNCT_SCHEMA,
        name=ConjunctName(name).value,
        mandatory=True,
        applicable=False,
        evidence_state=EvidenceState.NOT_APPLICABLE.value,
        evidence_source=evidence_source,
        evidence_reference=evidence_reference,
        evidence_digest=evidence_digest,
        producer=producer,
        source_frame_start=source_frame_start,
        source_frame_end=source_frame_end,
        source_time_start_s=source_time_start_s,
        source_time_end_s=source_time_end_s,
        source_clock=source_clock,
        value=None,
        reason_code=reason_code,
    )


def evaluate_same_g_identity(
    bindings: Sequence[GlobalTaskBinding],
    *,
    required_roles: Sequence[str],
    evidence_source: str,
    evidence_reference: str,
    evidence_digest: str,
    producer: str,
    source_frame_start: int | None = None,
    source_frame_end: int | None = None,
    source_time_start_s: float | None = None,
    source_time_end_s: float | None = None,
) -> V3ConjunctEvidence:
    """Exact G-V2 identity/digest join; no coordinate-distance fallback exists."""

    by_role = {item.role: item for item in bindings}
    if len(by_role) != len(bindings):
        raise ValueError("V3_G_BINDING_DUPLICATE_ROLE")
    missing = tuple(role for role in required_roles if role not in by_role)
    incomplete = tuple(
        role
        for role in required_roles
        if role in by_role
        and (
            not by_role[role].global_task_identity
            or not by_role[role].endpoint_digest
        )
    )
    common = {
        "evidence_source": evidence_source,
        "evidence_reference": evidence_reference,
        "evidence_digest": evidence_digest,
        "producer": producer,
        "source_frame_start": source_frame_start,
        "source_frame_end": source_frame_end,
        "source_time_start_s": source_time_start_s,
        "source_time_end_s": source_time_end_s,
    }
    if missing or incomplete:
        return unresolved_conjunct(
            ConjunctName.SAME_G_IDENTITY,
            state=EvidenceState.UNKNOWN,
            reason_code="REQUIRED_G_V2_BINDING_MISSING_OR_INCOMPLETE",
            **common,
        )
    identities = {by_role[role].global_task_identity for role in required_roles}
    endpoint_digests = {by_role[role].endpoint_digest for role in required_roles}
    return known_conjunct(
        ConjunctName.SAME_G_IDENTITY,
        len(identities) == 1 and len(endpoint_digests) == 1,
        **common,
    )


def frozen_predicate_conjunct(
    name: ConjunctName | str,
    *,
    resolution: str,
    evidence_source: str | None,
    evidence_reference: str | None,
    evidence_digest: str | None,
    producer: str | None,
    source_frame_start: int | None = None,
    source_frame_end: int | None = None,
    source_time_start_s: float | None = None,
    source_time_end_s: float | None = None,
) -> V3ConjunctEvidence:
    """Join a frozen predicate result without recomputing its semantics."""

    common = {
        "evidence_source": evidence_source,
        "evidence_reference": evidence_reference,
        "evidence_digest": evidence_digest,
        "producer": producer,
        "source_frame_start": source_frame_start,
        "source_frame_end": source_frame_end,
        "source_time_start_s": source_time_start_s,
        "source_time_end_s": source_time_end_s,
    }
    normalized = str(resolution)
    if normalized in {"SUCCESS", "TRUE", "KNOWN_TRUE"}:
        if not all((evidence_source, evidence_reference, evidence_digest, producer)):
            return unresolved_conjunct(
                name,
                state=EvidenceState.UNKNOWN,
                reason_code="FROZEN_PREDICATE_AUTHORITY_MISSING",
                **common,
            )
        return known_conjunct(name, True, **common)
    if normalized in {"FAILURE", "FALSE", "KNOWN_FALSE"}:
        if not all((evidence_source, evidence_reference, evidence_digest, producer)):
            return unresolved_conjunct(
                name,
                state=EvidenceState.UNKNOWN,
                reason_code="FROZEN_PREDICATE_AUTHORITY_MISSING",
                **common,
            )
        return known_conjunct(name, False, **common)
    if normalized == EvidenceState.NOT_APPLICABLE.value:
        if not all((evidence_source, evidence_reference, evidence_digest, producer)):
            return unresolved_conjunct(
                name,
                state=EvidenceState.UNKNOWN,
                reason_code="APPLICABILITY_AUTHORITY_MISSING",
                **common,
            )
        return not_applicable_conjunct(
            name,
            reason_code="NOT_APPLICABLE_BY_FROZEN_CONTRACT",
            **common,
        )
    if normalized == EvidenceState.RIGHT_CENSORED.value:
        return unresolved_conjunct(
            name,
            state=EvidenceState.RIGHT_CENSORED,
            reason_code="FROZEN_35S_WINDOW_ENDED_BEFORE_LEGITIMATE_RESOLUTION",
            **common,
        )
    if normalized != EvidenceState.UNKNOWN.value:
        raise ValueError("V3_FROZEN_PREDICATE_RESOLUTION_INVALID")
    return unresolved_conjunct(
        name,
        state=EvidenceState.UNKNOWN,
        reason_code="FROZEN_PREDICATE_EVIDENCE_UNAVAILABLE_OR_CORRUPT",
        **common,
    )


@dataclass(frozen=True)
class RQ2TGlobalTaskPreservationReceiptV3:
    schema_version: str
    contract_version: str
    case_id: str
    episode_id: str
    ordinal: int
    seed: int
    method: str
    timing_bucket: str
    cell_admissibility_reference: str
    cell_admissibility_digest: str
    cell_analysis_eligible: bool
    frozen_v3_contract_reference: str
    frozen_v3_contract_digest: str
    g_contract_v2_reference: str
    g_contract_v2_digest: str
    rq2_t_window_seconds: int
    production_phase: str
    runtime_policy_access: bool
    endpoint_applicable: bool
    conjuncts: tuple[V3ConjunctEvidence, ...]
    preservation_state: str
    preservation_value: bool | None
    first_unresolved_or_failure_conjunct: str | None
    rq2_l_status: str
    canonical_digest: str


def _adjudicate(
    conjuncts: Sequence[V3ConjunctEvidence], *, endpoint_applicable: bool
) -> tuple[PreservationState, bool | None, str | None]:
    if not endpoint_applicable:
        return PreservationState.NOT_APPLICABLE, None, None
    by_name = {item.name: item for item in conjuncts}
    failures = [
        name
        for name in V3_CONJUNCT_ORDER
        if by_name[name].evidence_state == EvidenceState.KNOWN.value
        and by_name[name].value is False
    ]
    if failures:
        return PreservationState.KNOWN_FALSE, False, failures[0]
    unknown = [
        name
        for name in V3_CONJUNCT_ORDER
        if by_name[name].evidence_state == EvidenceState.UNKNOWN.value
    ]
    if unknown:
        return PreservationState.UNKNOWN, None, unknown[0]
    censored = [
        name
        for name in V3_CONJUNCT_ORDER
        if by_name[name].evidence_state == EvidenceState.RIGHT_CENSORED.value
    ]
    if censored:
        return PreservationState.RIGHT_CENSORED, None, censored[0]
    if all(
        item.evidence_state == EvidenceState.NOT_APPLICABLE.value
        or (
            item.evidence_state == EvidenceState.KNOWN.value
            and item.value is True
        )
        for item in conjuncts
    ):
        return PreservationState.KNOWN_TRUE, True, None
    raise ValueError("V3_CONJUNCTION_STATE_UNREACHABLE")


def create_v3_preservation_receipt(
    *,
    cell_admissibility_receipt: Mapping[str, Any] | Any,
    cell_admissibility_reference: str,
    conjuncts: Iterable[V3ConjunctEvidence],
    endpoint_applicable: bool = True,
) -> RQ2TGlobalTaskPreservationReceiptV3:
    cell = validate_cell_admissibility_receipt(cell_admissibility_receipt)
    rows = tuple(conjuncts)
    names = tuple(item.name for item in rows)
    if names != V3_CONJUNCT_ORDER:
        raise ValueError("V3_CONJUNCT_SET_OR_ORDER_INVALID")
    if not endpoint_applicable and any(
        item.evidence_state != EvidenceState.NOT_APPLICABLE.value for item in rows
    ):
        raise ValueError("V3_NONAPPLICABLE_ENDPOINT_CONJUNCTS_MUST_BE_NOT_APPLICABLE")
    state, result, first = _adjudicate(rows, endpoint_applicable=endpoint_applicable)
    value = RQ2TGlobalTaskPreservationReceiptV3(
        schema_version=V3_RECEIPT_SCHEMA,
        contract_version=V3_CONTRACT_VERSION,
        case_id=str(cell["case_id"]),
        episode_id=str(cell["episode_id"]),
        ordinal=int(cell["ordinal"]),
        seed=int(cell["seed"]),
        method=str(cell["method"]),
        timing_bucket=str(cell["timing_bucket"]),
        cell_admissibility_reference=cell_admissibility_reference,
        cell_admissibility_digest=str(cell["canonical_sha256"]),
        cell_analysis_eligible=cell.get("analysis_eligible") is True,
        frozen_v3_contract_reference=V3_CONTRACT_REFERENCE,
        frozen_v3_contract_digest=V3_CONTRACT_DIGEST,
        g_contract_v2_reference=G_CONTRACT_V2_REFERENCE,
        g_contract_v2_digest=G_CONTRACT_V2_DIGEST,
        rq2_t_window_seconds=RQ2_T_WINDOW_SECONDS,
        production_phase=PRODUCTION_PHASE,
        runtime_policy_access=RUNTIME_POLICY_ACCESS,
        endpoint_applicable=bool(endpoint_applicable),
        conjuncts=rows,
        preservation_state=state.value,
        preservation_value=result,
        first_unresolved_or_failure_conjunct=first,
        rq2_l_status=RQ2_L_IMPLEMENTATION_STATUS,
        canonical_digest="",
    )
    return replace(
        value,
        canonical_digest=canonical_sha256(value, exclude=("canonical_digest",)),
    )


def receipt_to_mapping(
    receipt: RQ2TGlobalTaskPreservationReceiptV3,
) -> dict[str, Any]:
    return asdict(receipt)


def validate_v3_preservation_receipt(
    receipt: RQ2TGlobalTaskPreservationReceiptV3 | Mapping[str, Any],
) -> Mapping[str, Any]:
    value = asdict(receipt) if isinstance(receipt, RQ2TGlobalTaskPreservationReceiptV3) else dict(receipt)
    if value.get("schema_version") != V3_RECEIPT_SCHEMA:
        raise ValueError("V3_RECEIPT_SCHEMA_INVALID")
    if value.get("contract_version") != V3_CONTRACT_VERSION:
        raise ValueError("V3_RECEIPT_CONTRACT_VERSION_INVALID")
    if value.get("frozen_v3_contract_reference") != V3_CONTRACT_REFERENCE:
        raise ValueError("V3_RECEIPT_CONTRACT_REFERENCE_INVALID")
    if value.get("frozen_v3_contract_digest") != V3_CONTRACT_DIGEST:
        raise ValueError("V3_RECEIPT_CONTRACT_DIGEST_INVALID")
    if value.get("g_contract_v2_reference") != G_CONTRACT_V2_REFERENCE:
        raise ValueError("V3_RECEIPT_G_CONTRACT_REFERENCE_INVALID")
    if value.get("g_contract_v2_digest") != G_CONTRACT_V2_DIGEST:
        raise ValueError("V3_RECEIPT_G_CONTRACT_DIGEST_INVALID")
    if value.get("rq2_t_window_seconds") != RQ2_T_WINDOW_SECONDS:
        raise ValueError("V3_RECEIPT_WINDOW_CHANGED")
    if value.get("production_phase") != PRODUCTION_PHASE:
        raise ValueError("V3_RECEIPT_NOT_POST_EXECUTION")
    if value.get("runtime_policy_access") is not False:
        raise ValueError("V3_RECEIPT_RUNTIME_POLICY_ACCESS_FORBIDDEN")
    if value.get("rq2_l_status") != RQ2_L_IMPLEMENTATION_STATUS:
        raise ValueError("V3_RECEIPT_RQ2_L_STATUS_INVALID")
    rows = value.get("conjuncts")
    if not isinstance(rows, (list, tuple)):
        raise ValueError("V3_RECEIPT_CONJUNCTS_INVALID")
    conjuncts = tuple(V3ConjunctEvidence(**dict(item)) for item in rows)
    if tuple(item.name for item in conjuncts) != V3_CONJUNCT_ORDER:
        raise ValueError("V3_RECEIPT_CONJUNCT_SET_OR_ORDER_INVALID")
    state, result, first = _adjudicate(
        conjuncts, endpoint_applicable=value.get("endpoint_applicable") is True
    )
    if (
        value.get("preservation_state"),
        value.get("preservation_value"),
        value.get("first_unresolved_or_failure_conjunct"),
    ) != (state.value, result, first):
        raise ValueError("V3_RECEIPT_ADJUDICATION_MISMATCH")
    if value.get("canonical_digest") != canonical_sha256(
        value, exclude=("canonical_digest",)
    ):
        raise ValueError("V3_RECEIPT_CANONICAL_DIGEST_MISMATCH")
    return value


def v3_endpoint_evaluable(
    receipt: RQ2TGlobalTaskPreservationReceiptV3 | Mapping[str, Any],
) -> bool:
    value = validate_v3_preservation_receipt(receipt)
    return (
        value.get("cell_analysis_eligible") is True
        and value.get("endpoint_applicable") is True
        and value.get("preservation_state")
        in {PreservationState.KNOWN_TRUE.value, PreservationState.KNOWN_FALSE.value}
    )


def v3_endpoint_denominators(
    receipts: Iterable[RQ2TGlobalTaskPreservationReceiptV3 | Mapping[str, Any]],
) -> Mapping[str, int]:
    rows = [validate_v3_preservation_receipt(item) for item in receipts]
    return {
        "planned_denominator": len(rows),
        "intervention_eligible_denominator": sum(
            item["cell_analysis_eligible"] is True for item in rows
        ),
        "applicable_denominator": sum(item["endpoint_applicable"] is True for item in rows),
        "v3_known_denominator": sum(
            item["preservation_state"]
            in {PreservationState.KNOWN_TRUE.value, PreservationState.KNOWN_FALSE.value}
            for item in rows
        ),
        "unknown_count": sum(
            item["preservation_state"] == PreservationState.UNKNOWN.value
            for item in rows
        ),
        "right_censored_count": sum(
            item["preservation_state"] == PreservationState.RIGHT_CENSORED.value
            for item in rows
        ),
        "not_applicable_count": sum(
            item["preservation_state"] == PreservationState.NOT_APPLICABLE.value
            for item in rows
        ),
        "endpoint_evaluable_denominator": sum(v3_endpoint_evaluable(item) for item in rows),
    }


def matched_v3_endpoint_denominator(
    pairs: Iterable[
        tuple[
            RQ2TGlobalTaskPreservationReceiptV3 | Mapping[str, Any],
            RQ2TGlobalTaskPreservationReceiptV3 | Mapping[str, Any],
        ]
    ],
) -> Mapping[str, Any]:
    rows = list(pairs)
    details = []
    for left_raw, right_raw in rows:
        left = validate_v3_preservation_receipt(left_raw)
        right = validate_v3_preservation_receipt(right_raw)
        if (left["seed"], left["timing_bucket"]) != (
            right["seed"],
            right["timing_bucket"],
        ):
            raise ValueError("V3_MATCHED_BLOCK_IDENTITY_MISMATCH")
        intervention_eligible = (
            left["cell_analysis_eligible"] is True
            and right["cell_analysis_eligible"] is True
        )
        both_known = (
            left["preservation_state"]
            in {PreservationState.KNOWN_TRUE.value, PreservationState.KNOWN_FALSE.value}
            and right["preservation_state"]
            in {PreservationState.KNOWN_TRUE.value, PreservationState.KNOWN_FALSE.value}
        )
        both_applicable = (
            left["endpoint_applicable"] is True
            and right["endpoint_applicable"] is True
        )
        details.append(
            {
                "seed": left["seed"],
                "timing_bucket": left["timing_bucket"],
                "left_method": left["method"],
                "right_method": right["method"],
                "intervention_eligible": intervention_eligible,
                "both_v3_known": both_known,
                "both_applicable": both_applicable,
                "matched_comparison_eligible": (
                    intervention_eligible and both_known and both_applicable
                ),
            }
        )
    return {
        "planned_pair_denominator": len(details),
        "intervention_eligible_pair_denominator": sum(
            item["intervention_eligible"] for item in details
        ),
        "v3_known_pair_denominator": sum(item["both_v3_known"] for item in details),
        "matched_comparison_denominator": sum(
            item["matched_comparison_eligible"] for item in details
        ),
        "pairs": details,
    }
