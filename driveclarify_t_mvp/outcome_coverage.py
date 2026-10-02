"""Post-execution primary-outcome evidence receipts for frozen RQ2.

This module is deliberately outside every baseline input path.  It accepts only
sealed post-execution evidence and a digest of an already-finalized
``CellAdmissibilityReceipt``.  It never exposes an outcome object to a policy,
planner, controller, prompt, commitment decision, or transition decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .canonical import canonical_sha256
from .forced_horizon_safety import (
    SafetyTruthState,
    load_forced_horizon_safety_receipt,
)
from .frozen_registry import assert_frozen_evaluator_registry
from .metrics import (
    CONTINUITY_FORMULA_VERSION,
    EgoSample,
    continuity_metrics,
)
from .models import Availability


PRIMARY_OUTCOME_COVERAGE_SCHEMA = "driveclarify.rq2.primary_outcome_coverage.v1"
OUTCOME_EVIDENCE_SCHEMA = "driveclarify.rq2.outcome_evidence.v1"
POST_EXECUTION_PHASE = "POST_EXECUTION_ONLY"
OFFICIAL_EVALUATOR_IDENTITY = (
    "leaderboard_autopilot.RouteScenario+StatisticsManager:frozen-registry"
)


class ValueState(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    RIGHT_CENSORED = "RIGHT_CENSORED"


class EvidenceGrade(str, Enum):
    OFFICIAL_EVALUATOR = "AUTHORITATIVE_OFFICIAL_EVALUATOR"
    FROZEN_EVALUATOR = "AUTHORITATIVE_FROZEN_EVALUATOR"
    FROZEN_DERIVED = "AUTHORIZED_FROZEN_DERIVATION"
    RAW_OBSERVER = "READ_ONLY_RAW_OBSERVER"
    LEGACY_DIAGNOSTIC = "LEGACY_DIAGNOSTIC_ONLY"
    NONE = "NO_AUTHORITY"


class PredicateResolution(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    RIGHT_CENSORED = "RIGHT_CENSORED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# The exact endpoint names and group memberships are frozen here prospectively.
# ``outside_route_lanes`` is the already-frozen official metric.  The legacy
# ``off_road`` label and separate wrong-lane endpoint are retained as explicit,
# non-required gaps; neither silently aliases the aggregate official criterion.
ENDPOINT_REQUIREMENTS: Mapping[str, tuple[str, ...]] = {
    "LOCAL_TASK_ELIGIBLE": ("local_task_success",),
    "GLOBAL_TASK_ELIGIBLE": ("final_global_task_completion",),
    "SAFETY_ELIGIBLE": (
        "collision",
        "route_deviation",
        "outside_route_lanes",
    ),
    "TRANSITION_ELIGIBLE": ("commitment_violation",),
    "RECOVERY_ELIGIBLE": (
        "recovery_success",
        "recovery_time_s",
        "recovery_distance_m",
    ),
    "REJOIN_ELIGIBLE": ("rejoin_success",),
    "CONTINUITY_ELIGIBLE": (
        "steering_discontinuity",
        "heading_discontinuity",
        "jerk",
    ),
    "EFFICIENCY_ELIGIBLE": (
        "global_planner_calls",
        "local_planner_calls",
        "vla_forwards",
        "active_planning_latency_ms",
    ),
}

OPTIONAL_OR_UNADJUDICATED_OUTCOMES = frozenset({"off_road", "wrong_lane"})

FROZEN_OUTCOME_CONTRACT_DIGEST = canonical_sha256(
    {
        "schema_version": PRIMARY_OUTCOME_COVERAGE_SCHEMA,
        "endpoint_requirements": ENDPOINT_REQUIREMENTS,
        "optional_or_unadjudicated": tuple(sorted(OPTIONAL_OR_UNADJUDICATED_OUTCOMES)),
        "value_states": tuple(item.value for item in ValueState),
        "global_task_definition": (
            "verified official evaluator completion of the long-term mission "
            "ending at the same G"
        ),
        "global_task_proxy_substitution": False,
        "recovery_start": "t_update",
        "recovery_terminal": (
            "exact updated obligation completed AND legal/safe same-G continuation "
            "AND any installed route consumed"
        ),
        "continuity_clock": "CARLA_SIMULATION_TIME",
        "safety_owner_classes": (
            "CollisionTest",
            "InRouteTest",
            "OutsideRouteLanesTest",
        ),
        "runtime_policy_access": False,
    }
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class OutcomeEvidence:
    schema_version: str
    outcome_name: str
    applicable: bool
    evidence_required: bool
    evidence_available: bool
    evidence_source: str | None
    evidence_digest: str | None
    evaluator_identity: str | None
    metric_definition_version: str
    value_state: str
    value: Any
    reason_code: str | None
    source_frame_start: int | None
    source_frame_end: int | None
    source_time_start_s: float | None
    source_time_end_s: float | None
    source_clock: str | None
    evidence_grade: str

    def __post_init__(self) -> None:
        if self.schema_version != OUTCOME_EVIDENCE_SCHEMA:
            raise ValueError("OUTCOME_EVIDENCE_SCHEMA_INVALID")
        if not self.outcome_name or not self.metric_definition_version:
            raise ValueError("OUTCOME_EVIDENCE_IDENTITY_INCOMPLETE")
        state = ValueState(self.value_state)
        if self.evidence_digest is not None and len(self.evidence_digest) != 64:
            raise ValueError("OUTCOME_EVIDENCE_DIGEST_INVALID")
        if self.source_frame_start is not None and self.source_frame_start < 0:
            raise ValueError("OUTCOME_SOURCE_FRAME_INVALID")
        if self.source_frame_end is not None and self.source_frame_end < 0:
            raise ValueError("OUTCOME_SOURCE_FRAME_INVALID")
        if (
            self.source_frame_start is not None
            and self.source_frame_end is not None
            and self.source_frame_end < self.source_frame_start
        ):
            raise ValueError("OUTCOME_SOURCE_FRAME_RANGE_INVALID")
        if (
            self.source_time_start_s is not None
            and self.source_time_end_s is not None
            and self.source_time_end_s < self.source_time_start_s
        ):
            raise ValueError("OUTCOME_SOURCE_TIME_RANGE_INVALID")
        if state is ValueState.KNOWN:
            if (
                not self.applicable
                or not self.evidence_available
                or self.value is None
                or not self.evidence_source
                or not self.evidence_digest
                or not self.evaluator_identity
            ):
                raise ValueError("KNOWN_OUTCOME_AUTHORITY_INCOMPLETE")
            if self.reason_code is not None:
                raise ValueError("KNOWN_OUTCOME_REASON_FORBIDDEN")
        elif state is ValueState.NOT_APPLICABLE:
            if self.applicable or self.evidence_required or self.value is not None:
                raise ValueError("NOT_APPLICABLE_OUTCOME_INVARIANT_FAILED")
            if not self.reason_code:
                raise ValueError("NOT_APPLICABLE_REASON_MISSING")
        else:
            if self.value is not None or not self.reason_code:
                raise ValueError("UNRESOLVED_OUTCOME_INVARIANT_FAILED")
            if not self.applicable:
                raise ValueError("UNRESOLVED_OUTCOME_MUST_BE_APPLICABLE")


def known_outcome(
    outcome_name: str,
    value: Any,
    *,
    evidence_source: str,
    evidence_digest: str,
    evaluator_identity: str,
    metric_definition_version: str,
    evidence_grade: EvidenceGrade | str,
    evidence_required: bool = True,
    source_frame_start: int | None = None,
    source_frame_end: int | None = None,
    source_time_start_s: float | None = None,
    source_time_end_s: float | None = None,
    source_clock: str | None = "CARLA_SIMULATION_TIME",
) -> OutcomeEvidence:
    return OutcomeEvidence(
        schema_version=OUTCOME_EVIDENCE_SCHEMA,
        outcome_name=outcome_name,
        applicable=True,
        evidence_required=bool(evidence_required),
        evidence_available=True,
        evidence_source=evidence_source,
        evidence_digest=evidence_digest,
        evaluator_identity=evaluator_identity,
        metric_definition_version=metric_definition_version,
        value_state=ValueState.KNOWN.value,
        value=value,
        reason_code=None,
        source_frame_start=source_frame_start,
        source_frame_end=source_frame_end,
        source_time_start_s=source_time_start_s,
        source_time_end_s=source_time_end_s,
        source_clock=source_clock,
        evidence_grade=EvidenceGrade(evidence_grade).value,
    )


def unresolved_outcome(
    outcome_name: str,
    *,
    state: ValueState | str,
    reason_code: str,
    metric_definition_version: str,
    evidence_required: bool = True,
    evidence_available: bool = False,
    evidence_source: str | None = None,
    evidence_digest: str | None = None,
    evaluator_identity: str | None = None,
    evidence_grade: EvidenceGrade | str = EvidenceGrade.NONE,
    source_frame_start: int | None = None,
    source_frame_end: int | None = None,
    source_time_start_s: float | None = None,
    source_time_end_s: float | None = None,
    source_clock: str | None = None,
) -> OutcomeEvidence:
    value_state = ValueState(state)
    if value_state not in {ValueState.UNKNOWN, ValueState.RIGHT_CENSORED}:
        raise ValueError("UNRESOLVED_OUTCOME_STATE_INVALID")
    return OutcomeEvidence(
        schema_version=OUTCOME_EVIDENCE_SCHEMA,
        outcome_name=outcome_name,
        applicable=True,
        evidence_required=bool(evidence_required),
        evidence_available=bool(evidence_available),
        evidence_source=evidence_source,
        evidence_digest=evidence_digest,
        evaluator_identity=evaluator_identity,
        metric_definition_version=metric_definition_version,
        value_state=value_state.value,
        value=None,
        reason_code=reason_code,
        source_frame_start=source_frame_start,
        source_frame_end=source_frame_end,
        source_time_start_s=source_time_start_s,
        source_time_end_s=source_time_end_s,
        source_clock=source_clock,
        evidence_grade=EvidenceGrade(evidence_grade).value,
    )


def not_applicable_outcome(
    outcome_name: str,
    *,
    reason_code: str,
    metric_definition_version: str,
) -> OutcomeEvidence:
    return OutcomeEvidence(
        schema_version=OUTCOME_EVIDENCE_SCHEMA,
        outcome_name=outcome_name,
        applicable=False,
        evidence_required=False,
        evidence_available=False,
        evidence_source=None,
        evidence_digest=None,
        evaluator_identity=None,
        metric_definition_version=metric_definition_version,
        value_state=ValueState.NOT_APPLICABLE.value,
        value=None,
        reason_code=reason_code,
        source_frame_start=None,
        source_frame_end=None,
        source_time_start_s=None,
        source_time_end_s=None,
        source_clock=None,
        evidence_grade=EvidenceGrade.NONE.value,
    )


@dataclass(frozen=True)
class PrimaryOutcomeCoverageReceipt:
    schema_version: str
    case_id: str
    episode_id: str
    ordinal: int
    seed: int
    method: str
    timing_bucket: str
    cell_admissibility_digest: str
    analysis_eligible: bool
    contract_version: str
    frozen_outcome_contract_digest: str
    production_phase: str
    runtime_policy_access: bool
    outcomes: tuple[OutcomeEvidence, ...]
    primary_outcome_coverage_complete: bool
    missing_required_outcomes: tuple[str, ...]
    canonical_digest: str


def create_primary_outcome_coverage_receipt(
    *,
    case_id: str,
    episode_id: str,
    ordinal: int,
    seed: int,
    method: str,
    timing_bucket: str,
    cell_admissibility_digest: str,
    analysis_eligible: bool,
    outcomes: Iterable[OutcomeEvidence],
) -> PrimaryOutcomeCoverageReceipt:
    if len(cell_admissibility_digest) != 64:
        raise ValueError("OUTCOME_RECEIPT_ADMISSIBILITY_DIGEST_INVALID")
    rows = tuple(outcomes)
    names = tuple(row.outcome_name for row in rows)
    if len(names) != len(set(names)):
        raise ValueError("OUTCOME_RECEIPT_DUPLICATE_OUTCOME")
    missing = tuple(
        sorted(
            row.outcome_name
            for row in rows
            if row.evidence_required
            and row.applicable
            and row.value_state != ValueState.KNOWN.value
        )
    )
    value = PrimaryOutcomeCoverageReceipt(
        schema_version=PRIMARY_OUTCOME_COVERAGE_SCHEMA,
        case_id=case_id,
        episode_id=episode_id,
        ordinal=int(ordinal),
        seed=int(seed),
        method=method,
        timing_bucket=timing_bucket,
        cell_admissibility_digest=cell_admissibility_digest,
        analysis_eligible=bool(analysis_eligible),
        contract_version=PRIMARY_OUTCOME_COVERAGE_SCHEMA,
        frozen_outcome_contract_digest=FROZEN_OUTCOME_CONTRACT_DIGEST,
        production_phase=POST_EXECUTION_PHASE,
        runtime_policy_access=False,
        outcomes=rows,
        primary_outcome_coverage_complete=not missing,
        missing_required_outcomes=missing,
        canonical_digest="",
    )
    return replace(
        value,
        canonical_digest=canonical_sha256(value, exclude=("canonical_digest",)),
    )


def receipt_to_mapping(receipt: PrimaryOutcomeCoverageReceipt) -> dict[str, Any]:
    return asdict(receipt)


def validate_primary_outcome_coverage_receipt(
    receipt: PrimaryOutcomeCoverageReceipt | Mapping[str, Any],
) -> Mapping[str, Any]:
    value = asdict(receipt) if isinstance(receipt, PrimaryOutcomeCoverageReceipt) else dict(receipt)
    if value.get("schema_version") != PRIMARY_OUTCOME_COVERAGE_SCHEMA:
        raise ValueError("PRIMARY_OUTCOME_RECEIPT_SCHEMA_INVALID")
    if value.get("production_phase") != POST_EXECUTION_PHASE:
        raise ValueError("OUTCOME_RECEIPT_NOT_POST_EXECUTION")
    if value.get("runtime_policy_access") is not False:
        raise ValueError("OUTCOME_RECEIPT_RUNTIME_ACCESS_FORBIDDEN")
    if value.get("frozen_outcome_contract_digest") != FROZEN_OUTCOME_CONTRACT_DIGEST:
        raise ValueError("OUTCOME_CONTRACT_DIGEST_MISMATCH")
    if value.get("canonical_digest") != canonical_sha256(value, exclude=("canonical_digest",)):
        raise ValueError("PRIMARY_OUTCOME_RECEIPT_DIGEST_MISMATCH")
    outcomes = value.get("outcomes")
    if not isinstance(outcomes, (list, tuple)):
        raise ValueError("PRIMARY_OUTCOME_RECEIPT_OUTCOMES_INVALID")
    missing = tuple(
        sorted(
            row["outcome_name"]
            for row in outcomes
            if row.get("evidence_required") is True
            and row.get("applicable") is True
            and row.get("value_state") != ValueState.KNOWN.value
        )
    )
    if tuple(value.get("missing_required_outcomes", ())) != missing:
        raise ValueError("PRIMARY_OUTCOME_RECEIPT_MISSING_SET_INVALID")
    if value.get("primary_outcome_coverage_complete") is not (not missing):
        raise ValueError("PRIMARY_OUTCOME_RECEIPT_COMPLETENESS_INVALID")
    return value


def _official_source(
    checkpoint_path: Path,
) -> tuple[str, str, str]:
    if not checkpoint_path.is_file():
        raise FileNotFoundError(checkpoint_path)
    registry = assert_frozen_evaluator_registry()
    return str(checkpoint_path), _file_sha256(checkpoint_path), registry


def _official_record(checkpoint: Mapping[str, Any]) -> Mapping[str, Any] | None:
    records = checkpoint.get("_checkpoint", {}).get("records", [])
    if not isinstance(records, list) or not records:
        return None
    record = records[-1]
    return record if isinstance(record, Mapping) else None


def official_checkpoint_outcomes(
    checkpoint_path: Path,
) -> Mapping[str, OutcomeEvidence]:
    """Join only explicit terminal fields from the frozen official checkpoint.

    Empty, present infraction arrays are positive evidence of a criterion that
    was evaluated and did not trigger.  A missing record or field is UNKNOWN.
    Final-G completion uses the official terminal status only; route score,
    route preservation, connectivity, and rejoin never substitute.
    """

    metric_versions = {
        "collision": "driveclarify.rq2.safety.v1",
        "route_deviation": "driveclarify.rq2.safety.v1",
        "outside_route_lanes": "driveclarify.rq2.safety.v1",
        "final_global_task_completion": "driveclarify.rq2.global_task.v1",
    }
    if not checkpoint_path.is_file():
        return {
            name: unresolved_outcome(
                name,
                state=ValueState.UNKNOWN,
                reason_code="OFFICIAL_CHECKPOINT_MISSING",
                metric_definition_version=version,
            )
            for name, version in metric_versions.items()
        }
    source, digest, registry = _official_source(checkpoint_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    record = _official_record(checkpoint)
    if record is None:
        return {
            name: unresolved_outcome(
                name,
                state=ValueState.UNKNOWN,
                reason_code="OFFICIAL_TERMINAL_RECORD_MISSING",
                metric_definition_version=version,
                evidence_available=True,
                evidence_source=source,
                evidence_digest=digest,
                evaluator_identity=OFFICIAL_EVALUATOR_IDENTITY + ":" + registry,
                evidence_grade=EvidenceGrade.OFFICIAL_EVALUATOR,
            )
            for name, version in metric_versions.items()
        }
    duration = record.get("meta", {}).get("duration_game")
    end_time = float(duration) if isinstance(duration, (int, float)) else None
    evaluator = OFFICIAL_EVALUATOR_IDENTITY + ":" + registry
    output: dict[str, OutcomeEvidence] = {}
    infractions = record.get("infractions")
    infractions = infractions if isinstance(infractions, Mapping) else {}
    sources = {
        "collision": ("collisions_layout", "collisions_pedestrian", "collisions_vehicle"),
        "route_deviation": ("route_dev",),
        "outside_route_lanes": ("outside_route_lanes",),
    }
    for name, fields in sources.items():
        values = [infractions.get(field) for field in fields]
        if not all(isinstance(value, list) for value in values):
            output[name] = unresolved_outcome(
                name,
                state=ValueState.UNKNOWN,
                reason_code="OFFICIAL_CRITERION_FIELD_MISSING",
                metric_definition_version=metric_versions[name],
                evidence_available=True,
                evidence_source=source,
                evidence_digest=digest,
                evaluator_identity=evaluator,
                evidence_grade=EvidenceGrade.OFFICIAL_EVALUATOR,
                source_time_start_s=0.0,
                source_time_end_s=end_time,
                source_clock="CARLA_SIMULATION_TIME",
            )
        else:
            output[name] = known_outcome(
                name,
                any(bool(value) for value in values),
                evidence_source=source,
                evidence_digest=digest,
                evaluator_identity=evaluator,
                metric_definition_version=metric_versions[name],
                evidence_grade=EvidenceGrade.OFFICIAL_EVALUATOR,
                source_time_start_s=0.0,
                source_time_end_s=end_time,
            )
    status = record.get("status")
    if status == "Completed":
        global_value: bool | None = True
    elif isinstance(status, str) and status.startswith("Failed - "):
        global_value = False
    else:
        global_value = None
    if global_value is None:
        output["final_global_task_completion"] = unresolved_outcome(
            "final_global_task_completion",
            state=ValueState.UNKNOWN,
            reason_code="OFFICIAL_FINAL_G_TERMINAL_STATUS_UNRESOLVED",
            metric_definition_version=metric_versions["final_global_task_completion"],
            evidence_available=True,
            evidence_source=source,
            evidence_digest=digest,
            evaluator_identity=evaluator,
            evidence_grade=EvidenceGrade.OFFICIAL_EVALUATOR,
            source_time_start_s=0.0,
            source_time_end_s=end_time,
            source_clock="CARLA_SIMULATION_TIME",
        )
    else:
        output["final_global_task_completion"] = known_outcome(
            "final_global_task_completion",
            global_value,
            evidence_source=source,
            evidence_digest=digest,
            evaluator_identity=evaluator,
            metric_definition_version=metric_versions["final_global_task_completion"],
            evidence_grade=EvidenceGrade.OFFICIAL_EVALUATOR,
            source_time_start_s=0.0,
            source_time_end_s=end_time,
        )
    return output


def forced_horizon_safety_outcomes(
    snapshot_path: Path,
) -> Mapping[str, OutcomeEvidence]:
    """Join the canonical realized-endpoint receipt into the safety layer.

    Forced-35-second and allowed natural-terminal paths share this one receipt
    schema, including active-lifetime proof and realized exposure duration.
    """

    value = load_forced_horizon_safety_receipt(snapshot_path)
    receipt_digest = str(value["canonical_digest"])
    evaluator_identity = str(value["source_evaluator_identity"])
    rows = {row["criterion_name"]: row for row in value["criteria"]}
    output: dict[str, OutcomeEvidence] = {}
    for name in ("collision", "route_deviation", "outside_route_lanes"):
        row = rows[name]
        proof = row["active_evaluated_proof"]
        common = {
            "source_frame_start": proof.get("first_observed_frame"),
            "source_frame_end": value["simulation_frame"],
            "source_time_start_s": proof.get("first_observed_time_s"),
            "source_time_end_s": value["simulation_time_s"],
        }
        state = SafetyTruthState(row["truth_state"])
        if state in {SafetyTruthState.KNOWN_TRUE, SafetyTruthState.KNOWN_FALSE}:
            output[name] = known_outcome(
                name,
                state is SafetyTruthState.KNOWN_TRUE,
                evidence_source=str(snapshot_path),
                evidence_digest=receipt_digest,
                evaluator_identity=evaluator_identity,
                metric_definition_version="driveclarify.rq2.safety_endpoint.v2",
                evidence_grade=EvidenceGrade.OFFICIAL_EVALUATOR,
                **common,
            )
        else:
            output[name] = unresolved_outcome(
                name,
                state=ValueState.UNKNOWN,
                reason_code=str(row["reason_code"]),
                metric_definition_version="driveclarify.rq2.safety_endpoint.v2",
                evidence_available=True,
                evidence_source=str(snapshot_path),
                evidence_digest=receipt_digest,
                evaluator_identity=evaluator_identity,
                evidence_grade=EvidenceGrade.OFFICIAL_EVALUATOR,
                source_clock="CARLA_SIMULATION_TIME",
                **common,
            )
    return output


safety_endpoint_outcomes = forced_horizon_safety_outcomes


def authoritative_safety_outcomes(
    *,
    checkpoint_path: Path,
    forced_horizon_snapshot_path: Path | None = None,
) -> Mapping[str, OutcomeEvidence]:
    """Select the canonical realized-endpoint receipt and otherwise fail closed.

    Official checkpoint data remain useful for diagnostics, but the pilot's
    mandatory FALSE semantics require criterion-active lifetime proof.  A
    missing endpoint receipt therefore remains UNKNOWN rather than falling
    back to an unsealed checkpoint absence.
    """

    if forced_horizon_snapshot_path is not None and forced_horizon_snapshot_path.is_file():
        return forced_horizon_safety_outcomes(forced_horizon_snapshot_path)
    _ = checkpoint_path
    return {
        name: unresolved_outcome(
            name,
            state=ValueState.UNKNOWN,
            reason_code="CANONICAL_SAFETY_ENDPOINT_RECEIPT_MISSING",
            metric_definition_version="driveclarify.rq2.safety_endpoint.v2",
        )
        for name in ("collision", "route_deviation", "outside_route_lanes")
    }


def predicate_resolution_outcomes(
    *,
    outcome_name: str,
    resolution: PredicateResolution | str,
    metric_definition_version: str,
    evidence_source: str | None,
    evidence_digest: str | None,
    evaluator_identity: str | None,
    evidence_grade: EvidenceGrade | str = EvidenceGrade.FROZEN_EVALUATOR,
    source_frame_start: int | None = None,
    source_frame_end: int | None = None,
    source_time_start_s: float | None = None,
    source_time_end_s: float | None = None,
) -> OutcomeEvidence:
    state = PredicateResolution(resolution)
    if state is PredicateResolution.NOT_APPLICABLE:
        return not_applicable_outcome(
            outcome_name,
            reason_code="NOT_APPLICABLE_BY_FROZEN_TIMING_CONTRACT",
            metric_definition_version=metric_definition_version,
        )
    if state in {PredicateResolution.SUCCESS, PredicateResolution.FAILURE}:
        if not evidence_source or not evidence_digest or not evaluator_identity:
            return unresolved_outcome(
                outcome_name,
                state=ValueState.UNKNOWN,
                reason_code="PREDICATE_AUTHORITY_MISSING",
                metric_definition_version=metric_definition_version,
            )
        return known_outcome(
            outcome_name,
            state is PredicateResolution.SUCCESS,
            evidence_source=evidence_source,
            evidence_digest=evidence_digest,
            evaluator_identity=evaluator_identity,
            metric_definition_version=metric_definition_version,
            evidence_grade=evidence_grade,
            source_frame_start=source_frame_start,
            source_frame_end=source_frame_end,
            source_time_start_s=source_time_start_s,
            source_time_end_s=source_time_end_s,
        )
    return unresolved_outcome(
        outcome_name,
        state=(
            ValueState.RIGHT_CENSORED
            if state is PredicateResolution.RIGHT_CENSORED
            else ValueState.UNKNOWN
        ),
        reason_code=(
            "FROZEN_OBSERVATION_WINDOW_ENDED_BEFORE_PREDICATE_RESOLVED"
            if state is PredicateResolution.RIGHT_CENSORED
            else "PREDICATE_EVIDENCE_UNAVAILABLE_OR_CORRUPT"
        ),
        metric_definition_version=metric_definition_version,
        evidence_available=bool(evidence_source and evidence_digest),
        evidence_source=evidence_source,
        evidence_digest=evidence_digest,
        evaluator_identity=evaluator_identity,
        evidence_grade=evidence_grade if evidence_source else EvidenceGrade.NONE,
        source_frame_start=source_frame_start,
        source_frame_end=source_frame_end,
        source_time_start_s=source_time_start_s,
        source_time_end_s=source_time_end_s,
        source_clock="CARLA_SIMULATION_TIME" if evidence_source else None,
    )


def recovery_outcomes(
    *,
    resolution: PredicateResolution | str,
    evidence_source: str | None,
    evidence_digest: str | None,
    evaluator_identity: str | None,
    recovery_time_s: float | None = None,
    recovery_distance_m: float | None = None,
    source_frame_start: int | None = None,
    source_frame_end: int | None = None,
    source_time_start_s: float | None = None,
    source_time_end_s: float | None = None,
) -> Mapping[str, OutcomeEvidence]:
    """Encode recovery without false infinity or collapsed censoring states."""

    resolution = PredicateResolution(resolution)
    version = "driveclarify.rq2.recovery.v1"
    success = predicate_resolution_outcomes(
        outcome_name="recovery_success",
        resolution=resolution,
        metric_definition_version=version,
        evidence_source=evidence_source,
        evidence_digest=evidence_digest,
        evaluator_identity=evaluator_identity,
        source_frame_start=source_frame_start,
        source_frame_end=source_frame_end,
        source_time_start_s=source_time_start_s,
        source_time_end_s=source_time_end_s,
    )
    output = {"recovery_success": success}
    if resolution is PredicateResolution.SUCCESS:
        for name, value, unit in (
            ("recovery_time_s", recovery_time_s, "s"),
            ("recovery_distance_m", recovery_distance_m, "m"),
        ):
            if value is None or not math.isfinite(float(value)) or float(value) < 0.0:
                output[name] = unresolved_outcome(
                    name,
                    state=ValueState.UNKNOWN,
                    reason_code="RECOVERY_NUMERIC_EVIDENCE_MISSING_OR_INVALID",
                    metric_definition_version=version,
                    evidence_available=bool(evidence_source and evidence_digest),
                    evidence_source=evidence_source,
                    evidence_digest=evidence_digest,
                    evaluator_identity=evaluator_identity,
                    evidence_grade=(
                        EvidenceGrade.FROZEN_DERIVED if evidence_source else EvidenceGrade.NONE
                    ),
                    source_frame_start=source_frame_start,
                    source_frame_end=source_frame_end,
                    source_time_start_s=source_time_start_s,
                    source_time_end_s=source_time_end_s,
                    source_clock="CARLA_SIMULATION_TIME" if evidence_source else None,
                )
            else:
                output[name] = known_outcome(
                    name,
                    float(value),
                    evidence_source=str(evidence_source),
                    evidence_digest=str(evidence_digest),
                    evaluator_identity=str(evaluator_identity),
                    metric_definition_version=version,
                    evidence_grade=EvidenceGrade.FROZEN_DERIVED,
                    source_frame_start=source_frame_start,
                    source_frame_end=source_frame_end,
                    source_time_start_s=source_time_start_s,
                    source_time_end_s=source_time_end_s,
                )
    elif resolution is PredicateResolution.FAILURE:
        output["recovery_time_s"] = not_applicable_outcome(
            "recovery_time_s",
            reason_code="NOT_APPLICABLE_ON_NO_RECOVERY",
            metric_definition_version=version,
        )
        output["recovery_distance_m"] = not_applicable_outcome(
            "recovery_distance_m",
            reason_code="NOT_APPLICABLE_ON_NO_RECOVERY",
            metric_definition_version=version,
        )
    elif resolution is PredicateResolution.NOT_APPLICABLE:
        for name in ("recovery_time_s", "recovery_distance_m"):
            output[name] = not_applicable_outcome(
                name,
                reason_code="NOT_APPLICABLE_BY_FROZEN_TIMING_CONTRACT",
                metric_definition_version=version,
            )
    else:
        state = (
            ValueState.RIGHT_CENSORED
            if resolution is PredicateResolution.RIGHT_CENSORED
            else ValueState.UNKNOWN
        )
        reason = (
            "FROZEN_OBSERVATION_WINDOW_ENDED_BEFORE_RECOVERY_RESOLVED"
            if state is ValueState.RIGHT_CENSORED
            else "RECOVERY_EVIDENCE_UNAVAILABLE_OR_CORRUPT"
        )
        for name in ("recovery_time_s", "recovery_distance_m"):
            output[name] = unresolved_outcome(
                name,
                state=state,
                reason_code=reason,
                metric_definition_version=version,
                evidence_available=bool(evidence_source and evidence_digest),
                evidence_source=evidence_source,
                evidence_digest=evidence_digest,
                evaluator_identity=evaluator_identity,
                evidence_grade=(
                    EvidenceGrade.RAW_OBSERVER if evidence_source else EvidenceGrade.NONE
                ),
                source_frame_start=source_frame_start,
                source_frame_end=source_frame_end,
                source_time_start_s=source_time_start_s,
                source_time_end_s=source_time_end_s,
                source_clock="CARLA_SIMULATION_TIME" if evidence_source else None,
            )
    return output


def continuity_outcomes(
    samples: Sequence[EgoSample],
    *,
    t_effect_s: float,
    evidence_source: str,
    evaluator_identity: str = "DriveClarify read-only ego/control observer",
) -> Mapping[str, OutcomeEvidence]:
    """Evaluate frozen continuity formulas from one CARLA simulation clock."""

    ordered = sorted(samples, key=lambda item: item.frame)
    source_payload = [asdict(item) for item in ordered]
    digest = canonical_sha256(source_payload)
    invalid_clock = any(
        second.frame <= first.frame or second.sim_time_s <= first.sim_time_s
        for first, second in zip(ordered, ordered[1:])
    )
    if invalid_clock:
        return {
            name: unresolved_outcome(
                name,
                state=ValueState.UNKNOWN,
                reason_code="CONTINUITY_SIMULATION_FRAME_TIME_ALIGNMENT_INVALID",
                metric_definition_version=CONTINUITY_FORMULA_VERSION,
                evidence_available=bool(ordered),
                evidence_source=evidence_source,
                evidence_digest=digest,
                evaluator_identity=evaluator_identity,
                evidence_grade=EvidenceGrade.RAW_OBSERVER,
                source_frame_start=ordered[0].frame if ordered else None,
                source_frame_end=ordered[-1].frame if ordered else None,
                source_time_start_s=(
                    min(item.sim_time_s for item in ordered) if ordered else None
                ),
                source_time_end_s=(
                    max(item.sim_time_s for item in ordered) if ordered else None
                ),
                source_clock="CARLA_SIMULATION_TIME" if ordered else None,
            )
            for name in ("steering_discontinuity", "heading_discontinuity", "jerk")
        }
    metrics = continuity_metrics(ordered, t_effect_s=t_effect_s)
    output: dict[str, OutcomeEvidence] = {}
    for name in ("steering_discontinuity", "heading_discontinuity", "jerk"):
        metric = metrics[name]
        common = {
            "source_frame_start": ordered[0].frame if ordered else None,
            "source_frame_end": ordered[-1].frame if ordered else None,
            "source_time_start_s": ordered[0].sim_time_s if ordered else None,
            "source_time_end_s": ordered[-1].sim_time_s if ordered else None,
        }
        if metric.status is Availability.AVAILABLE:
            output[name] = known_outcome(
                name,
                metric.value,
                evidence_source=evidence_source,
                evidence_digest=digest,
                evaluator_identity=evaluator_identity,
                metric_definition_version=CONTINUITY_FORMULA_VERSION,
                evidence_grade=EvidenceGrade.FROZEN_DERIVED,
                **common,
            )
        else:
            reason = (
                metric.unknown.reason_code
                if metric.unknown is not None
                else "CONTINUITY_EVIDENCE_UNKNOWN"
            )
            output[name] = unresolved_outcome(
                name,
                state=ValueState.UNKNOWN,
                reason_code=reason,
                metric_definition_version=CONTINUITY_FORMULA_VERSION,
                evidence_available=bool(ordered),
                evidence_source=evidence_source,
                evidence_digest=digest,
                evaluator_identity=evaluator_identity,
                evidence_grade=EvidenceGrade.RAW_OBSERVER,
                source_clock="CARLA_SIMULATION_TIME" if ordered else None,
                **common,
            )
    return output


def endpoint_eligible(
    receipt: PrimaryOutcomeCoverageReceipt | Mapping[str, Any],
    endpoint_group: str,
) -> bool:
    value = validate_primary_outcome_coverage_receipt(receipt)
    if endpoint_group not in ENDPOINT_REQUIREMENTS:
        raise ValueError("ENDPOINT_GROUP_NOT_FROZEN:" + endpoint_group)
    if value.get("analysis_eligible") is not True:
        return False
    by_name = {row["outcome_name"]: row for row in value["outcomes"]}
    applicable_count = 0
    for name in ENDPOINT_REQUIREMENTS[endpoint_group]:
        row = by_name.get(name)
        if row is None:
            return False
        if row["value_state"] == ValueState.NOT_APPLICABLE.value:
            continue
        applicable_count += 1
        if row["value_state"] != ValueState.KNOWN.value:
            return False
    return applicable_count > 0


def matched_endpoint_denominator(
    pairs: Iterable[
        tuple[
            PrimaryOutcomeCoverageReceipt | Mapping[str, Any],
            PrimaryOutcomeCoverageReceipt | Mapping[str, Any],
        ]
    ],
    *,
    endpoint_group: str,
) -> Mapping[str, Any]:
    rows = list(pairs)
    included: list[Mapping[str, Any]] = []
    excluded: list[Mapping[str, Any]] = []
    for left, right in rows:
        left_value = validate_primary_outcome_coverage_receipt(left)
        right_value = validate_primary_outcome_coverage_receipt(right)
        if (
            left_value["seed"],
            left_value["timing_bucket"],
        ) != (
            right_value["seed"],
            right_value["timing_bucket"],
        ):
            raise ValueError("MATCHED_ENDPOINT_CASE_IDENTITY_MISMATCH")
        item = {
            "seed": left_value["seed"],
            "timing_bucket": left_value["timing_bucket"],
            "left_method": left_value["method"],
            "right_method": right_value["method"],
            "left_eligible": endpoint_eligible(left_value, endpoint_group),
            "right_eligible": endpoint_eligible(right_value, endpoint_group),
        }
        item["pair_endpoint_eligible"] = (
            item["left_eligible"] and item["right_eligible"]
        )
        (included if item["pair_endpoint_eligible"] else excluded).append(item)
    return {
        "endpoint_group": endpoint_group,
        "planned_pair_denominator": len(rows),
        "eligible_pair_denominator": len(included),
        "excluded_pair_count": len(excluded),
        "included_pairs": included,
        "excluded_pairs": excluded,
    }
