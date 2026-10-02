"""Prospective episode-admissibility and analysis-eligibility authority.

This module does not run a simulator, infer a timing bucket, inspect outcome
quality, or execute a scientific policy.  It seals already verified evidence
for the intervention stages that the frozen RQ2 contract requires.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Iterable, Mapping

from .canonical import canonical_sha256
from .firewall import BaselineId


CELL_ADMISSIBILITY_SCHEMA = "driveclarify.rq2.cell_admissibility.v1"
PRE_EXPOSURE_VALIDITY_SCHEMA = "driveclarify.rq2.pre_exposure_validity.v1"
ADMISSIBILITY_CONTRACT_VERSION = "driveclarify.rq2.episode_admissibility_contract.v1"


class ScientificTerminalClass(str, Enum):
    SCIENTIFIC_COMPLETED = "SCIENTIFIC_COMPLETED"
    INJECTION_NOT_REACHED = "SCIENTIFIC_NONCOMPLETION_INJECTION_NOT_REACHED"
    DISPATCH_NOT_REACHED = "SCIENTIFIC_NONCOMPLETION_DISPATCH_NOT_REACHED"
    OBLIGATION_NOT_COMPLETED = (
        "SCIENTIFIC_NONCOMPLETION_OBLIGATION_NOT_COMPLETED"
    )
    MANDATORY_EVIDENCE_MISSING = (
        "SCIENTIFIC_NONCOMPLETION_MANDATORY_EVIDENCE_MISSING"
    )
    PRE_AGENT_INFRASTRUCTURE_FAILURE = "PRE_AGENT_INFRASTRUCTURE_FAILURE"


class IneligibilityReason(str, Enum):
    NONE = "NONE"
    PRE_AGENT_INFRASTRUCTURE_FAILURE = "PRE_AGENT_INFRASTRUCTURE_FAILURE"
    INJECTION_NOT_REACHED = "INJECTION_NOT_REACHED"
    DISPATCH_NOT_REACHED = "DISPATCH_NOT_REACHED"
    OBLIGATION_NOT_COMPLETED = "OBLIGATION_NOT_COMPLETED"
    MANDATORY_RECEIPT_MISSING = "MANDATORY_RECEIPT_MISSING"
    TRUTH_JOIN_MISSING = "TRUTH_JOIN_MISSING"
    OTHER_PROTOCOL_DEFECT = "OTHER_PROTOCOL_DEFECT"


class PreExposureStatus(str, Enum):
    VALID = "PRE_EXPOSURE_VALID"
    INVALID = "PRE_EXPOSURE_INVALID"


@dataclass(frozen=True)
class MethodObligationContract:
    method: str
    exact_native_policy: str
    semantic_identity: str
    mandatory_obligation: str
    mandatory_evidence_kinds: tuple[str, ...]


METHOD_OBLIGATION_CONTRACTS: Mapping[str, MethodObligationContract] = {
    BaselineId.T_B1.value: MethodObligationContract(
        method=BaselineId.T_B1.value,
        exact_native_policy="driveclarify_t_mvp.baselines.InstantOverwriteBaseline",
        semantic_identity="INSTANT_OVERWRITE",
        mandatory_obligation="INSTALL_AND_NEXT_NORMAL_FORWARD_CONSUMPTION",
        mandatory_evidence_kinds=(
            "T_B1_DISPATCH",
            "EXACT_INSTALL_RECEIPT",
            "NEXT_NORMAL_FORWARD_CONSUMED_INSTALLED_CANDIDATE",
        ),
    ),
    BaselineId.T_B2.value: MethodObligationContract(
        method=BaselineId.T_B2.value,
        exact_native_policy="driveclarify_t_mvp.baselines.AlwaysFullReplanBaseline",
        semantic_identity="ALWAYS_FULL_REPLAN_GLOBAL_PLUS_LOCAL_PREPARE_BEFORE_INSTALL",
        mandatory_obligation=(
            "DETACHED_GLOBAL_AND_LOCAL_PREPARATION_INSTALLATION_AND_CONSUMPTION"
        ),
        mandatory_evidence_kinds=(
            "T_B2_GLOBAL_PREPARATION",
            "T_B2_LOCAL_PREPARATION",
            "T_B2_EXACT_INSTALL_RECEIPT",
            "NEXT_NORMAL_FORWARD_CONSUMED_INSTALLED_CANDIDATE",
        ),
    ),
    BaselineId.T_B3.value: MethodObligationContract(
        method=BaselineId.T_B3.value,
        exact_native_policy="driveclarify_t_mvp.baselines.FinishOldFirstBaseline",
        semantic_identity="FINISH_OLD_FIRST",
        mandatory_obligation="P_OLD_RETAINED_WAIT_FINISH_OLD_FIRST_NO_RESCUE",
        mandatory_evidence_kinds=(
            "P_OLD_PRESERVED",
            "WAIT_OLD_MANEUVER_TERMINAL",
            "NO_RESCUE_SEARCH",
        ),
    ),
    BaselineId.T_B4.value: MethodObligationContract(
        method=BaselineId.T_B4.value,
        exact_native_policy="driveclarify_t_mvp.baselines.LocalReplanOnlyBaseline",
        semantic_identity="LOCAL_REPLAN_ONLY",
        mandatory_obligation=(
            "LOCAL_REPLACEMENT_CONSUMED_WITH_ZERO_GLOBAL_PLANNER_AND_RECONNECT"
        ),
        mandatory_evidence_kinds=(
            "T_B4_LOCAL_CONSUMPTION",
            "ZERO_GLOBAL_PLANNER_CALLS",
            "ZERO_GLOBAL_RECONNECTS",
            "GLOBAL_ROUTE_IDENTITY_AND_GENERATION_UNCHANGED",
        ),
    ),
    BaselineId.T_B5.value: MethodObligationContract(
        method=BaselineId.T_B5.value,
        exact_native_policy="driveclarify_t_mvp.baselines.HistoryOnlyBaseline",
        semantic_identity="HISTORY_ONLY_FROZEN_VLA",
        mandatory_obligation="LEAK_FREE_HISTORY_ONLY_NATIVE_FORWARD_CONSUMPTION",
        mandatory_evidence_kinds=(
            "HISTORY_ONLY_PAYLOAD",
            "FORBIDDEN_LEAKAGE_ZERO",
            "NEXT_NORMAL_FORWARD_CONSUMED_HISTORY_ONLY_PROMPT",
        ),
    ),
    BaselineId.T_B6.value: MethodObligationContract(
        method=BaselineId.T_B6.value,
        exact_native_policy=(
            "driveclarify_t_mvp.baselines.DriveClarifyTransitionBaseline"
        ),
        semantic_identity="DRIVECLARIFY_FROZEN_V11_TRANSITION",
        mandatory_obligation=(
            "CANONICAL_FROZEN_ADMISSIBILITY_AND_FROZEN_TRANSITION_DECISION_PATH"
        ),
        mandatory_evidence_kinds=(
            "CANONICAL_FROZEN_ADMISSIBILITY_RECEIPT",
            "ORACLE_FIELDS_ABSENT",
            "DEFAULT_THRESHOLDS_ONLY",
            "FROZEN_TRANSITION_DECISION_PATH_COMPLETE",
        ),
    ),
}


TRUTH_JOIN_REQUIREMENTS: Mapping[str, str] = {
    "T1_BEFORE_COMMITMENT": "NOT_REQUIRED_BY_FROZEN_TIMING_CONTRACT",
    "T2_NEAR_COMMITMENT": "NOT_REQUIRED_BY_FROZEN_TIMING_CONTRACT",
    "T3_POST_COMMIT_RECOVERABLE": "OLD_EXCLUSIVE_RECOVERABLE",
    "T4_NO_SAFE_CURRENT_OPPORTUNITY": "NO_SAFE_CURRENT_OPPORTUNITY",
}


FROZEN_ADMISSIBILITY_CONTRACT_DIGEST = canonical_sha256(
    {
        "schema_version": ADMISSIBILITY_CONTRACT_VERSION,
        "method_obligations": METHOD_OBLIGATION_CONTRACTS,
        "truth_join_requirements": TRUTH_JOIN_REQUIREMENTS,
        "stage_order": (
            "EXPECTED_CELL",
            "EXACT_T_BUCKET_INJECTION",
            "EXACT_NATIVE_DISPATCH",
            "METHOD_SPECIFIC_OBLIGATION",
            "REQUIRED_TRUTH_JOIN",
            "ANALYSIS_ELIGIBLE",
        ),
        "outcome_quality_used": False,
    }
)


@dataclass(frozen=True)
class EvidenceReference:
    relative_path: str
    sha256: str
    evidence_kind: str

    def __post_init__(self) -> None:
        if not self.relative_path or not self.evidence_kind:
            raise ValueError("ADMISSIBILITY_EVIDENCE_REFERENCE_INCOMPLETE")
        if len(self.sha256) != 64:
            raise ValueError("ADMISSIBILITY_EVIDENCE_SHA256_INVALID")


@dataclass(frozen=True)
class ExpectedInjectionIdentity:
    injection_event_id: str
    update_event_id: str
    expected_oracle_event: str
    timing_bucket: str


@dataclass(frozen=True)
class StageVerification:
    observed: bool
    valid: bool
    evidence_references: tuple[EvidenceReference, ...] = ()
    sim_frame: int | None = None
    sim_time_s: float | None = None
    defect_reason: str | None = None

    def __post_init__(self) -> None:
        if self.valid and not self.observed:
            raise ValueError("VALID_ADMISSIBILITY_STAGE_NOT_OBSERVED")
        if self.valid and not self.evidence_references:
            raise ValueError("VALID_ADMISSIBILITY_STAGE_REFERENCE_MISSING")
        if self.sim_frame is not None and self.sim_frame < 0:
            raise ValueError("ADMISSIBILITY_STAGE_FRAME_INVALID")


@dataclass(frozen=True)
class PreExposureValidityReceipt:
    schema_version: str
    case_id: str
    episode_id: str
    seed: int
    method: str
    timing_bucket: str
    status: str
    failure_reasons: tuple[str, ...]
    checks: tuple[tuple[str, bool], ...]
    frozen_config_contract_digest: str
    canonical_sha256: str

    @classmethod
    def create(
        cls,
        *,
        case_id: str,
        episode_id: str,
        seed: int,
        method: str,
        timing_bucket: str,
        checks: Iterable[tuple[str, bool]],
        failure_reasons: Iterable[str],
        frozen_config_contract_digest: str,
    ) -> "PreExposureValidityReceipt":
        check_tuple = tuple((str(name), bool(passed)) for name, passed in checks)
        reasons = tuple(str(reason) for reason in failure_reasons)
        status = (
            PreExposureStatus.VALID.value
            if check_tuple and all(passed for _, passed in check_tuple) and not reasons
            else PreExposureStatus.INVALID.value
        )
        value = cls(
            schema_version=PRE_EXPOSURE_VALIDITY_SCHEMA,
            case_id=case_id,
            episode_id=episode_id,
            seed=int(seed),
            method=method,
            timing_bucket=timing_bucket,
            status=status,
            failure_reasons=reasons,
            checks=check_tuple,
            frozen_config_contract_digest=frozen_config_contract_digest,
            canonical_sha256="",
        )
        return replace(value, canonical_sha256=canonical_sha256(value))


@dataclass(frozen=True)
class CellAdmissibilityReceipt:
    schema_version: str
    case_id: str
    episode_id: str
    ordinal: int
    method: str
    timing_bucket: str
    seed: int
    agent_exposed: bool
    pre_exposure_status: str
    expected_injection_identity: ExpectedInjectionIdentity
    injection_required: bool
    injection_observed: bool
    injection_evidence_valid: bool
    injection_frame: int | None
    injection_sim_time_s: float | None
    injection_evidence_reference: tuple[EvidenceReference, ...]
    exact_expected_native_policy: str
    native_dispatch_required: bool
    native_dispatch_observed: bool
    native_dispatch_evidence_valid: bool
    dispatch_frame: int | None
    dispatch_sim_time_s: float | None
    dispatch_evidence_reference: tuple[EvidenceReference, ...]
    expected_method_specific_obligation: str
    obligation_complete: bool
    obligation_evidence_valid: bool
    obligation_frame: int | None
    obligation_sim_time_s: float | None
    obligation_evidence_reference: tuple[EvidenceReference, ...]
    required_truth_join: str
    truth_join_complete: bool
    truth_join_evidence_reference: tuple[EvidenceReference, ...]
    process_terminal_class: str
    terminal_class: str
    analysis_eligible: bool
    first_ineligibility_reason: str
    canonical_schema_version: str
    frozen_config_contract_digest: str
    outcome_quality_used_for_eligibility: bool
    canonical_sha256: str


def _first_ineligibility(
    *,
    agent_exposed: bool,
    process_terminal_class: str,
    injection: StageVerification,
    dispatch: StageVerification,
    obligation: StageVerification,
    truth_join_required: bool,
    truth_join: StageVerification,
) -> tuple[IneligibilityReason, ScientificTerminalClass]:
    if not agent_exposed:
        return (
            IneligibilityReason.PRE_AGENT_INFRASTRUCTURE_FAILURE,
            ScientificTerminalClass.PRE_AGENT_INFRASTRUCTURE_FAILURE,
        )
    if not injection.observed:
        return (
            IneligibilityReason.INJECTION_NOT_REACHED,
            ScientificTerminalClass.INJECTION_NOT_REACHED,
        )
    if not injection.valid:
        return (
            IneligibilityReason.MANDATORY_RECEIPT_MISSING,
            ScientificTerminalClass.MANDATORY_EVIDENCE_MISSING,
        )
    if not dispatch.observed:
        return (
            IneligibilityReason.DISPATCH_NOT_REACHED,
            ScientificTerminalClass.DISPATCH_NOT_REACHED,
        )
    if not dispatch.valid:
        return (
            IneligibilityReason.MANDATORY_RECEIPT_MISSING,
            ScientificTerminalClass.MANDATORY_EVIDENCE_MISSING,
        )
    if not obligation.observed:
        return (
            IneligibilityReason.OBLIGATION_NOT_COMPLETED,
            ScientificTerminalClass.OBLIGATION_NOT_COMPLETED,
        )
    if not obligation.valid:
        return (
            IneligibilityReason.MANDATORY_RECEIPT_MISSING,
            ScientificTerminalClass.MANDATORY_EVIDENCE_MISSING,
        )
    if truth_join_required and not truth_join.valid:
        return (
            IneligibilityReason.TRUTH_JOIN_MISSING,
            ScientificTerminalClass.MANDATORY_EVIDENCE_MISSING,
        )
    return IneligibilityReason.NONE, ScientificTerminalClass.SCIENTIFIC_COMPLETED


def create_cell_admissibility_receipt(
    *,
    case_id: str,
    episode_id: str,
    ordinal: int,
    method: str,
    timing_bucket: str,
    seed: int,
    agent_exposed: bool,
    pre_exposure_status: str,
    expected_injection_identity: ExpectedInjectionIdentity,
    injection: StageVerification,
    dispatch: StageVerification,
    obligation: StageVerification,
    truth_join: StageVerification,
    process_terminal_class: str,
    frozen_config_digest: str,
) -> CellAdmissibilityReceipt:
    if method not in METHOD_OBLIGATION_CONTRACTS:
        raise ValueError("ADMISSIBILITY_METHOD_NOT_FROZEN:" + method)
    if timing_bucket not in TRUTH_JOIN_REQUIREMENTS:
        raise ValueError("ADMISSIBILITY_TIMING_BUCKET_NOT_FROZEN:" + timing_bucket)
    if pre_exposure_status != PreExposureStatus.VALID.value:
        raise ValueError("POST_EXPOSURE_RECEIPT_REQUIRES_VALID_PRE_EXPOSURE_CELL")
    method_contract = METHOD_OBLIGATION_CONTRACTS[method]
    required_truth = TRUTH_JOIN_REQUIREMENTS[timing_bucket]
    truth_required = not required_truth.startswith("NOT_REQUIRED")
    reason, terminal = _first_ineligibility(
        agent_exposed=agent_exposed,
        process_terminal_class=process_terminal_class,
        injection=injection,
        dispatch=dispatch,
        obligation=obligation,
        truth_join_required=truth_required,
        truth_join=truth_join,
    )
    eligible = reason is IneligibilityReason.NONE
    value = CellAdmissibilityReceipt(
        schema_version=CELL_ADMISSIBILITY_SCHEMA,
        case_id=case_id,
        episode_id=episode_id,
        ordinal=int(ordinal),
        method=method,
        timing_bucket=timing_bucket,
        seed=int(seed),
        agent_exposed=bool(agent_exposed),
        pre_exposure_status=pre_exposure_status,
        expected_injection_identity=expected_injection_identity,
        injection_required=True,
        injection_observed=injection.observed,
        injection_evidence_valid=injection.valid,
        injection_frame=injection.sim_frame,
        injection_sim_time_s=injection.sim_time_s,
        injection_evidence_reference=injection.evidence_references,
        exact_expected_native_policy=method_contract.exact_native_policy,
        native_dispatch_required=True,
        native_dispatch_observed=dispatch.observed,
        native_dispatch_evidence_valid=dispatch.valid,
        dispatch_frame=dispatch.sim_frame,
        dispatch_sim_time_s=dispatch.sim_time_s,
        dispatch_evidence_reference=dispatch.evidence_references,
        expected_method_specific_obligation=method_contract.mandatory_obligation,
        obligation_complete=obligation.observed,
        obligation_evidence_valid=obligation.valid,
        obligation_frame=obligation.sim_frame,
        obligation_sim_time_s=obligation.sim_time_s,
        obligation_evidence_reference=obligation.evidence_references,
        required_truth_join=required_truth,
        truth_join_complete=(truth_join.valid if truth_required else True),
        truth_join_evidence_reference=truth_join.evidence_references,
        process_terminal_class=process_terminal_class,
        terminal_class=terminal.value,
        analysis_eligible=eligible,
        first_ineligibility_reason=reason.value,
        canonical_schema_version=CELL_ADMISSIBILITY_SCHEMA,
        frozen_config_contract_digest=canonical_sha256(
            {
                "admissibility_contract": FROZEN_ADMISSIBILITY_CONTRACT_DIGEST,
                "prospective_cell_config": frozen_config_digest,
            }
        ),
        outcome_quality_used_for_eligibility=False,
        canonical_sha256="",
    )
    return replace(value, canonical_sha256=canonical_sha256(value))


def receipt_to_mapping(receipt: CellAdmissibilityReceipt) -> dict[str, Any]:
    return asdict(receipt)


def validate_cell_admissibility_receipt(
    receipt: CellAdmissibilityReceipt | Mapping[str, Any],
) -> Mapping[str, Any]:
    value = asdict(receipt) if isinstance(receipt, CellAdmissibilityReceipt) else dict(receipt)
    if value.get("schema_version") != CELL_ADMISSIBILITY_SCHEMA:
        raise ValueError("CELL_ADMISSIBILITY_SCHEMA_INVALID")
    digest = value.get("canonical_sha256")
    if not isinstance(digest, str) or digest != canonical_sha256(value):
        raise ValueError("CELL_ADMISSIBILITY_DIGEST_MISMATCH")
    if value.get("outcome_quality_used_for_eligibility") is not False:
        raise ValueError("CELL_ADMISSIBILITY_OUTCOME_QUALITY_DEPENDENCE_FORBIDDEN")
    eligible = value.get("analysis_eligible") is True
    if eligible:
        required = (
            value.get("agent_exposed") is True,
            value.get("pre_exposure_status") == PreExposureStatus.VALID.value,
            value.get("injection_required") is True,
            value.get("injection_observed") is True,
            value.get("injection_evidence_valid") is True,
            value.get("native_dispatch_required") is True,
            value.get("native_dispatch_observed") is True,
            value.get("native_dispatch_evidence_valid") is True,
            value.get("obligation_complete") is True,
            value.get("obligation_evidence_valid") is True,
            value.get("truth_join_complete") is True,
            value.get("terminal_class")
            == ScientificTerminalClass.SCIENTIFIC_COMPLETED.value,
            value.get("first_ineligibility_reason") == IneligibilityReason.NONE.value,
        )
        if not all(required):
            raise ValueError("CELL_ADMISSIBILITY_ELIGIBLE_INVARIANT_FAILED")
    elif value.get("first_ineligibility_reason") == IneligibilityReason.NONE.value:
        raise ValueError("CELL_ADMISSIBILITY_INELIGIBLE_REASON_MISSING")
    return value


def analysis_eligible_from_receipt(
    receipt: CellAdmissibilityReceipt | Mapping[str, Any],
) -> bool:
    """The only aggregate-facing eligibility decision point."""

    return validate_cell_admissibility_receipt(receipt).get("analysis_eligible") is True


def complete_six_method_blocks(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Classify planned seed x timing blocks from canonical receipts only."""

    grouped: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((int(row["seed"]), str(row["timing_bucket"])), []).append(row)
    output: list[dict[str, Any]] = []
    expected_methods = set(METHOD_OBLIGATION_CONTRACTS)
    for (seed, bucket), block in sorted(grouped.items()):
        by_method = {str(row["method_id"]): row for row in block}
        eligible_methods = sorted(
            method
            for method, row in by_method.items()
            if analysis_eligible_from_receipt(row["cell_admissibility_receipt"])
        )
        complete = set(by_method) == expected_methods and set(eligible_methods) == expected_methods
        output.append(
            {
                "seed": seed,
                "timing_bucket": bucket,
                "block_status": "COMPLETE_SIX_METHOD" if complete else "BLOCK_INCOMPLETE",
                "analysis_eligible_methods": eligible_methods,
                "ineligible_or_missing_methods": sorted(expected_methods - set(eligible_methods)),
            }
        )
    return output
