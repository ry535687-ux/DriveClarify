"""Typed contracts for DriveClarify CLEAR-PassThrough Safe-Replan V11.

The contracts intentionally contain no case, family, expected-decision, or
oracle-intent field.  Runtime method code must be unable to receive those
scientific labels through its normal API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping, Optional, Sequence, Tuple


METHOD_ID = "DRIVECLARIFY_CLEAR_PASSTHROUGH_SAFE_REPLAN_V11"
SCHEMA_VERSION = "driveclarify.clear-passthrough-safe-replan.v11"

FORBIDDEN_RUNTIME_KEYS = frozenset(
    {
        "answer",
        "case_id",
        "consequence_class",
        "expected_decision",
        "expected_label",
        "family",
        "gold_candidate_index",
        "gold_intent",
        "gold_intended_object",
        "ground_truth",
        "oracle_answer",
        "oracle_intent",
        "scientific_outcome",
        "true_intent",
    }
)


class ContractViolation(ValueError):
    """Raised when a runtime object violates a V11 method contract."""


class GateDecision(str, Enum):
    CLEAR = "CLEAR"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


class PolicyAction(str, Enum):
    ACT = "ACT"
    ASK = "ASK"
    WAIT = "WAIT"


class TransitionDisposition(str, Enum):
    COMMIT_NOW = "COMMIT_NOW"
    DEFER_COMMIT = "DEFER_COMMIT"
    REJECT_STALE_OR_INFEASIBLE = "REJECT_STALE_OR_INFEASIBLE"


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _canonical(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def assert_runtime_label_firewall(value: Any) -> None:
    """Reject scientific/oracle fields recursively before gate evaluation."""

    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        overlap = {
            str(key).casefold() for key in value
        }.intersection(FORBIDDEN_RUNTIME_KEYS)
        if overlap:
            raise ContractViolation(
                "RUNTIME_LABEL_FIREWALL:" + ",".join(sorted(overlap))
            )
        for item in value.values():
            assert_runtime_label_firewall(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            assert_runtime_label_firewall(item)


@dataclass(frozen=True)
class GroundingEvidence:
    """Family-agnostic evidence available before candidate construction.

    ``reasonable_interpretation_count`` is a count derived from current scene
    grounding/topology.  It is not an expected ambiguity label.
    """

    reasonable_interpretation_count: Optional[int]
    evidence_status: str
    source: str
    observation_frame: int
    current_frame: int
    reason_codes: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.reasonable_interpretation_count is not None:
            if type(self.reasonable_interpretation_count) is not int:
                raise ContractViolation("INTERPRETATION_COUNT_MUST_BE_INT_OR_NONE")
            if self.reasonable_interpretation_count < 0:
                raise ContractViolation("INTERPRETATION_COUNT_NEGATIVE")
        if not isinstance(self.observation_frame, int) or not isinstance(
            self.current_frame, int
        ):
            raise ContractViolation("GROUNDING_FRAME_MUST_BE_INT")
        assert_runtime_label_firewall(asdict(self))


@dataclass(frozen=True)
class GateInput:
    instruction: str
    grounding: GroundingEvidence
    runtime_evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.instruction, str) or not self.instruction.strip():
            raise ContractViolation("INSTRUCTION_REQUIRED")
        assert_runtime_label_firewall(self.runtime_evidence)


@dataclass(frozen=True)
class GateResult:
    decision: GateDecision
    semantic_kind: str
    reason_codes: Tuple[str, ...]
    evidence_sha256: str
    candidate_pipeline_authorized: bool

    def __post_init__(self) -> None:
        expected = self.decision is GateDecision.AMBIGUOUS
        if bool(self.candidate_pipeline_authorized) != expected:
            raise ContractViolation("CANDIDATE_AUTHORITY_MUST_EQUAL_AMBIGUOUS")


@dataclass(frozen=True)
class RoutePoint:
    x: float
    y: float
    z: float = 0.0
    road_option: str = "LANEFOLLOW"

    def __post_init__(self) -> None:
        if not all(math.isfinite(float(value)) for value in (self.x, self.y, self.z)):
            raise ContractViolation("ROUTE_POINT_NONFINITE")
        if not str(self.road_option).strip():
            raise ContractViolation("ROAD_OPTION_REQUIRED")

    @property
    def xyz(self) -> Tuple[float, float, float]:
        return (float(self.x), float(self.y), float(self.z))


@dataclass(frozen=True)
class AuthoritativeRoute:
    route_id: str
    points: Tuple[RoutePoint, ...]
    target_point: Tuple[float, float]
    road_option: str
    destination_xyz: Tuple[float, float, float]
    source_frame: int
    connector_id: Optional[str] = None
    commitment_point_index: Optional[int] = None
    full_route_owner: str = "NATIVE_SIMLINGO"
    active_suffix_owner: str = "NATIVE_SIMLINGO"

    def __post_init__(self) -> None:
        if not isinstance(self.route_id, str) or not self.route_id.strip():
            raise ContractViolation("ROUTE_ID_REQUIRED")
        if len(self.points) < 2:
            raise ContractViolation("ROUTE_REQUIRES_TWO_POINTS")
        if len(self.target_point) != 2 or not all(
            math.isfinite(float(value)) for value in self.target_point
        ):
            raise ContractViolation("TARGET_POINT_INVALID")
        if len(self.destination_xyz) != 3 or not all(
            math.isfinite(float(value)) for value in self.destination_xyz
        ):
            raise ContractViolation("DESTINATION_INVALID")
        if self.commitment_point_index is not None and not (
            0 <= self.commitment_point_index < len(self.points)
        ):
            raise ContractViolation("COMMITMENT_POINT_INDEX_INVALID")

    @property
    def geometry_sha256(self) -> str:
        return canonical_sha256(
            [
                {
                    "xyz": [point.x.hex(), point.y.hex(), point.z.hex()],
                    "road_option": point.road_option,
                }
                for point in self.points
            ]
        )


@dataclass(frozen=True)
class EgoState:
    x: float
    y: float
    z: float
    yaw_degrees: float
    speed_mps: float
    frame: int

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(float(value))
            for value in (self.x, self.y, self.z, self.yaw_degrees, self.speed_mps)
        ):
            raise ContractViolation("EGO_STATE_NONFINITE")
        if self.speed_mps < 0:
            raise ContractViolation("EGO_SPEED_NEGATIVE")

    @property
    def xyz(self) -> Tuple[float, float, float]:
        return (float(self.x), float(self.y), float(self.z))


@dataclass(frozen=True)
class ModelPlan:
    """A SimLingo-produced local-frame plan; never a handcrafted trajectory."""

    xy: Tuple[Tuple[float, float], ...]
    source: str = "SIMLINGO"


@dataclass(frozen=True)
class CandidateInterpretation:
    candidate_id: str
    description: str
    evidence_id: str


@dataclass(frozen=True)
class CandidateRoute:
    candidate: CandidateInterpretation
    route: AuthoritativeRoute
    simlingo_preview_plan: Optional[ModelPlan] = None


@dataclass(frozen=True)
class ConsequenceDecision:
    action: PolicyAction
    selected_candidate_id: Optional[str]
    question: Optional[str]
    reason_codes: Tuple[str, ...]

    def __post_init__(self) -> None:
        if self.action is PolicyAction.ACT and not self.selected_candidate_id:
            raise ContractViolation("ACT_REQUIRES_SELECTED_CANDIDATE")
        if self.action is PolicyAction.ASK and not self.question:
            raise ContractViolation("ASK_REQUIRES_QUESTION")


@dataclass(frozen=True)
class AskReceipt:
    receipt_id: str
    query_id: str
    observation_id: str
    durable: bool
    path: str
    sha256: str


@dataclass(frozen=True)
class PassengerAnswer:
    query_id: str
    selected_candidate_id: str


@dataclass(frozen=True)
class AdmissibilityResult:
    disposition: TransitionDisposition
    reason_codes: Tuple[str, ...]
    metrics: Mapping[str, Any]
    route_id: str
    missed_replan_opportunity: bool = False


@dataclass(frozen=True)
class RouteTransactionReceipt:
    transaction_id: str
    route_id_before: str
    route_id_after: str
    transaction_count: int
    committed: bool
    a1_route_switch_active: bool
    owner_receipt: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class InvocationCounters:
    ambiguity_gate_invocations: int = 0
    candidate_interpretation_invocations: int = 0
    candidate_route_invocations: int = 0
    consequence_candidate_comparison_invocations: int = 0
    ask_receipts: int = 0
    passenger_answer_reads: int = 0
    replan_admissibility_invocations: int = 0
    route_transactions: int = 0
    a1_active_forwards: int = 0
    driveclarify_governor_active_ticks: int = 0

    def snapshot(self) -> Mapping[str, int]:
        return dict(asdict(self))


@dataclass(frozen=True)
class SupervisorResult:
    gate: GateResult
    native_input: Any
    pass_through: bool
    authority_preserved: bool
    policy_action: Optional[PolicyAction]
    selected_candidate_id: Optional[str]
    transition: Optional[AdmissibilityResult]
    transaction: Optional[RouteTransactionReceipt]
    counters: Mapping[str, int]
    status: str


__all__ = [
    "AdmissibilityResult",
    "AskReceipt",
    "AuthoritativeRoute",
    "CandidateInterpretation",
    "CandidateRoute",
    "ConsequenceDecision",
    "ContractViolation",
    "EgoState",
    "FORBIDDEN_RUNTIME_KEYS",
    "GateDecision",
    "GateInput",
    "GateResult",
    "GroundingEvidence",
    "InvocationCounters",
    "METHOD_ID",
    "ModelPlan",
    "PassengerAnswer",
    "PolicyAction",
    "RoutePoint",
    "RouteTransactionReceipt",
    "SCHEMA_VERSION",
    "SupervisorResult",
    "TransitionDisposition",
    "assert_runtime_label_firewall",
    "canonical_sha256",
]
