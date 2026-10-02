"""Auditable wrappers for the six frozen RQ2 transition baselines."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import inspect
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .accounting import CallKind, PlanningAccounting
from .authority import (
    AtomicAuthoritySource,
    AuthorityFingerprint,
    CandidateIsolationGuard,
)
from .canonical import bytes_sha256, canonical_sha256
from .firewall import BaselineId, build_policy_input
from .g_binding_v2 import DestinationTerminalRegion
from .models import (
    CandidateFeasibility,
    CandidateLifecycle,
    GlobalTask,
    LocalNavigationCondition,
    NavigationCandidate,
    ObservableCommitment,
    ObservableCommitmentState,
    POldSnapshot,
    PlanState,
    RouteRow,
)
from .frozen_registry import (
    FROZEN_SIMLINGO_AGENT_OWNER,
    FROZEN_SIMLINGO_AGENT_SOURCE,
    FROZEN_SIMLINGO_CHECKPOINT,
    FROZEN_SIMLINGO_CONFIG,
    FROZEN_SIMLINGO_LOADED_TOKENIZER_SHA256,
    FROZEN_SIMLINGO_MAX_CONTEXT_TOKENS,
    FROZEN_SIMLINGO_MODEL_ID,
    FROZEN_SIMLINGO_TOKENIZER_OWNER,
    FROZEN_T_B5_FILES,
    FROZEN_V11_ADMISSIBILITY_OWNER,
    assert_frozen_v11_admissibility_owner,
    assert_frozen_t_b5_registry,
    file_sha256,
)


class TransitionOutcome(str, Enum):
    COMMIT_NOW = "COMMIT_NOW"
    DEFER_COMMIT = "DEFER_COMMIT"
    REJECT_STALE_OR_INFEASIBLE = "REJECT_STALE_OR_INFEASIBLE"
    NO_EXPLICIT_TRANSITION = "NO_EXPLICIT_TRANSITION"
    WAIT_OLD_MANEUVER_TERMINAL = "WAIT_OLD_MANEUVER_TERMINAL"
    MISSED_CURRENT_OPPORTUNITY = "MISSED_CURRENT_OPPORTUNITY"
    LOCAL_CONDITION_READY = "LOCAL_CONDITION_READY"
    HISTORY_PAYLOAD_READY = "HISTORY_PAYLOAD_READY"


@dataclass(frozen=True)
class TransitionDecision:
    outcome: TransitionOutcome
    reason_codes: tuple[str, ...]
    baseline_id: BaselineId


@dataclass(frozen=True)
class PlanningBoundary:
    frame: int
    simulator_snapshot_complete: bool
    next_control_already_selected: bool
    atomic_route_owner_available: bool
    next_cycle_can_consume_generation: bool

    @property
    def legitimate(self) -> bool:
        return bool(
            self.simulator_snapshot_complete
            and not self.next_control_already_selected
            and self.atomic_route_owner_available
            and self.next_cycle_can_consume_generation
        )


@dataclass(frozen=True)
class InstallReceipt:
    committed: bool
    frame: int
    route_identity_before: str
    route_generation_before: int
    installed_route_identity: str
    installed_route_generation: int
    global_task_before: GlobalTask
    global_task_after: GlobalTask
    installed_candidate_sha256: str
    reason_code: str


class CandidateInstaller(Protocol):
    def install_candidate(
        self,
        candidate: NavigationCandidate,
        *,
        expected_old_generation: int,
        frame: int,
    ) -> InstallReceipt:
        ...


def _accounted_install(
    accounting: PlanningAccounting,
    *,
    transaction_purpose: str,
    installation_purpose: str,
    installer: CandidateInstaller,
    candidate: NavigationCandidate,
    expected_old_generation: int,
    frame: int,
) -> InstallReceipt:
    """Count the transaction and its one install attempt on function entry."""

    return accounting.invoke(
        CallKind.ROUTE_TRANSACTION,
        transaction_purpose,
        lambda: accounting.invoke(
            CallKind.ROUTE_INSTALLATION,
            installation_purpose,
            installer.install_candidate,
            candidate,
            expected_old_generation=expected_old_generation,
            frame=frame,
        ),
    )


class InstantOverwriteBaseline:
    baseline_id = BaselineId.T_B1

    def __init__(self, accounting: PlanningAccounting):
        self.accounting = accounting

    def decide_transition(
        self,
        *,
        candidate: NavigationCandidate,
        boundary: PlanningBoundary,
    ) -> TransitionDecision:
        build_policy_input(
            self.baseline_id,
            {"p_new": {"canonical_sha256": candidate.canonical_sha256},
             "planning_boundary": {"legitimate": boundary.legitimate}},
        )
        if not boundary.legitimate:
            return TransitionDecision(
                TransitionOutcome.NO_EXPLICIT_TRANSITION,
                ("WAIT_FOR_EARLIEST_LEGITIMATE_BETWEEN_CYCLE_BOUNDARY",),
                self.baseline_id,
            )
        return TransitionDecision(
            TransitionOutcome.COMMIT_NOW,
            ("EARLIEST_LEGITIMATE_BETWEEN_CYCLE_BOUNDARY",),
            self.baseline_id,
        )

    def commit_or_defer(
        self,
        *,
        lifecycle: CandidateLifecycle,
        boundary: PlanningBoundary,
        installer: CandidateInstaller,
        expected_old_generation: int,
    ) -> InstallReceipt | None:
        decision = self.decide_transition(
            candidate=lifecycle.candidate, boundary=boundary
        )
        if decision.outcome is not TransitionOutcome.COMMIT_NOW:
            return None
        lifecycle.admit(frame=boundary.frame, reason_code=decision.reason_codes[0])
        receipt = _accounted_install(
            self.accounting,
            transaction_purpose="T_B1_EXACTLY_ONCE_TRANSACTION_RECEIPT",
            installation_purpose="T_B1_EARLIEST_BOUNDARY_INSTALL",
            installer=installer,
            candidate=lifecycle.candidate,
            expected_old_generation=expected_old_generation,
            frame=boundary.frame,
        )
        if receipt.committed:
            lifecycle.commit(
                frame=receipt.frame,
                installed_route_identity=receipt.installed_route_identity,
                installed_route_generation=receipt.installed_route_generation,
            )
        return receipt


@dataclass(frozen=True)
class EgoPlanningSnapshot:
    frame: int
    sim_time_s: float
    pose_xyz_yaw: tuple[float, float, float, float]
    speed_mps: float
    source_sha256: str
    current_topology_node_identity: str | None = None


@dataclass(frozen=True)
class UpdatedObligation:
    identity: str
    semantic_update_event_id: str
    branch_or_connector_identity: str
    instruction_new: str


@dataclass(frozen=True)
class FullGlobalPlan:
    route_identity: str
    route_rows: tuple[RouteRow, ...]
    active_suffix: tuple[RouteRow, ...]
    updated_branch_identity: str
    updated_branch_route_rows: tuple[RouteRow, ...]
    updated_branch_proof_sha256: str
    planner_receipt_sha256: str
    destination_terminal_region: DestinationTerminalRegion
    destination_terminal_equivalence_sha256: str

    def assert_task_conditioned(
        self,
        *,
        updated_obligation: UpdatedObligation,
        global_task: GlobalTask,
    ) -> None:
        if not self.route_rows or not self.active_suffix or not self.updated_branch_route_rows:
            raise RuntimeError("T_B2_FULL_GLOBAL_OR_BRANCH_ROUTE_MISSING")
        if self.updated_branch_identity != updated_obligation.branch_or_connector_identity:
            raise RuntimeError("T_B2_UPDATED_BRANCH_IDENTITY_MISMATCH")
        width = len(self.updated_branch_route_rows)
        starts = range(0, len(self.route_rows) - width + 1)
        if not any(
            self.route_rows[start : start + width] == self.updated_branch_route_rows
            for start in starts
        ):
            raise RuntimeError("T_B2_UPDATED_BRANCH_NOT_IN_FULL_ROUTE")
        expected_route_identity = "t-b2-full-route-" + canonical_sha256(
            {"route_rows": self.route_rows, "global_task": global_task.identity}
        )[:24]
        if self.route_identity != expected_route_identity:
            raise RuntimeError("T_B2_FULL_ROUTE_IDENTITY_NOT_CONTENT_DERIVED")
        expected_proof = canonical_sha256(
            {
                "updated_obligation_identity": updated_obligation.identity,
                "updated_branch_identity": self.updated_branch_identity,
                "updated_branch_route_rows": self.updated_branch_route_rows,
                "full_route_identity": self.route_identity,
                "global_task_identity": global_task.identity,
            }
        )
        if self.updated_branch_proof_sha256 != expected_proof:
            raise RuntimeError("T_B2_UPDATED_BRANCH_PROOF_INVALID")
        expected_equivalence = self.destination_terminal_region.assert_equivalent(
            global_task=global_task,
            planner_terminal=self.route_rows[-1],
        )
        if self.destination_terminal_equivalence_sha256 != expected_equivalence:
            raise RuntimeError("T_B2_TERMINAL_REGION_EQUIVALENCE_PROOF_INVALID")


class TaskConditionedGlobalPlanner(Protocol):
    def trace_task_conditioned_route(
        self,
        ego: EgoPlanningSnapshot,
        updated_obligation: UpdatedObligation,
        global_task: GlobalTask,
    ) -> FullGlobalPlan:
        ...


class DetachedLocalNavigationPreparer(Protocol):
    def prepare_local_navigation(
        self,
        global_plan: FullGlobalPlan,
        ego: EgoPlanningSnapshot,
        updated_obligation: UpdatedObligation,
    ) -> LocalNavigationCondition:
        ...


class AlwaysFullReplanBaseline:
    baseline_id = BaselineId.T_B2

    def __init__(
        self,
        source: AtomicAuthoritySource,
        global_planner: TaskConditionedGlobalPlanner,
        local_preparer: DetachedLocalNavigationPreparer,
        accounting: PlanningAccounting,
        *,
        owner_identity: str = "driveclarify_t_mvp.AlwaysFullReplanBaseline",
    ) -> None:
        self.source = source
        self.global_planner = global_planner
        self.local_preparer = local_preparer
        self.accounting = accounting
        self.owner_identity = owner_identity

    def prepare_full_replan(
        self,
        *,
        ego: EgoPlanningSnapshot,
        updated_obligation: UpdatedObligation,
        global_task: GlobalTask,
    ) -> NavigationCandidate:
        build_policy_input(
            self.baseline_id,
            {
                "ego_state": {"frame": ego.frame, "source_sha256": ego.source_sha256},
                "global_task_G": {"identity": global_task.identity},
                "updated_obligation": {
                    "identity": updated_obligation.identity,
                    "branch_or_connector_identity": (
                        updated_obligation.branch_or_connector_identity
                    ),
                },
                "new_instruction": updated_obligation.instruction_new,
            },
        )

        def prepare() -> NavigationCandidate:
            before = self.source.export_atomic()
            if before.global_task != global_task:
                raise RuntimeError("T_B2_GLOBAL_TASK_INPUT_MISMATCH")
            global_plan = self.accounting.invoke(
                CallKind.GLOBAL_PLANNER,
                "T_B2_CURRENT_EGO_TASK_CONDITIONED_ROUTE_TO_G",
                self.global_planner.trace_task_conditioned_route,
                ego,
                updated_obligation,
                global_task,
            )
            global_plan.assert_task_conditioned(
                updated_obligation=updated_obligation,
                global_task=global_task,
            )
            if global_plan.route_identity == before.route_identity:
                raise RuntimeError("T_B2_FRESH_ROUTE_IDENTITY_UNCHANGED")
            local = self.accounting.invoke(
                CallKind.LOCAL_PLANNER,
                "T_B2_FRESH_LOCAL_NAVIGATION_PREPARATION_NO_VLA_FORWARD",
                self.local_preparer.prepare_local_navigation,
                global_plan,
                ego,
                updated_obligation,
            )
            if (
                local.branch_or_connector_identity
                != updated_obligation.branch_or_connector_identity
            ):
                raise RuntimeError("T_B2_LOCAL_CONDITION_OBLIGATION_MISMATCH")
            return NavigationCandidate.create(
                candidate_id="t-b2-" + canonical_sha256(
                    {
                        "update": updated_obligation.semantic_update_event_id,
                        "route": global_plan.route_identity,
                        "frame": ego.frame,
                    }
                )[:24],
                candidate_route_identity=global_plan.route_identity,
                candidate_route_generation=before.route_generation + 1,
                global_task=global_task,
                semantic_update_event_id=updated_obligation.semantic_update_event_id,
                updated_obligation_identity=updated_obligation.identity,
                local_branch_or_connector_identity=(
                    updated_obligation.branch_or_connector_identity
                ),
                full_route_projection=global_plan.route_rows,
                active_candidate_suffix=global_plan.active_suffix,
                local_navigation_condition=local,
                feasibility_state=CandidateFeasibility.FEASIBLE_NOW,
                feasibility_reason_codes=(
                    "TASK_CONDITIONED_GLOBAL_ROUTE_AND_LOCAL_CONDITION_PREPARED",
                ),
                candidate_source_frame=ego.frame,
                candidate_source_sim_time_s=ego.sim_time_s,
                candidate_generator_owner=self.owner_identity,
                preparation_call_receipts=(
                    global_plan.planner_receipt_sha256,
                    global_plan.updated_branch_proof_sha256,
                    global_plan.destination_terminal_equivalence_sha256,
                    local.preparation_receipt_sha256,
                ),
            )

        generation_count_before = self.accounting.count(CallKind.CANDIDATE_GENERATION)
        candidate = self.accounting.invoke(
            CallKind.CANDIDATE_GENERATION,
            "T_B2_GENUINE_FULL_GLOBAL_PLUS_LOCAL_CANDIDATE",
            lambda: CandidateIsolationGuard(self.source).prepare(prepare).prepared,
        )
        if self.accounting.count(CallKind.CANDIDATE_GENERATION) != generation_count_before + 1:
            raise RuntimeError("T_B2_CANDIDATE_ACCOUNTING_INVALID")
        return candidate

    def install_full_replan_candidate(
        self,
        *,
        lifecycle: CandidateLifecycle,
        installer: CandidateInstaller,
        boundary: PlanningBoundary,
        expected_old_generation: int,
    ) -> InstallReceipt:
        if not boundary.legitimate:
            raise RuntimeError("T_B2_INSTALL_BOUNDARY_NOT_LEGITIMATE")
        if lifecycle.state is not PlanState.CANDIDATE_PREPARED:
            raise RuntimeError("T_B2_CANDIDATE_NOT_PREPARED")
        lifecycle.admit(frame=boundary.frame, reason_code="T_B2_UNCONDITIONAL_ADMISSION")
        receipt = _accounted_install(
            self.accounting,
            transaction_purpose="T_B2_EXACT_PREPARED_CANDIDATE_TRANSACTION",
            installation_purpose="T_B2_INSTALL_EXACT_DETACHED_FULL_REPLAN",
            installer=installer,
            candidate=lifecycle.candidate,
            expected_old_generation=expected_old_generation,
            frame=boundary.frame,
        )
        if receipt.global_task_before != receipt.global_task_after:
            raise RuntimeError("T_B2_GLOBAL_TASK_CHANGED_DURING_INSTALL")
        if receipt.installed_candidate_sha256 != lifecycle.candidate.canonical_sha256:
            raise RuntimeError("T_B2_INSTALL_RECOMPUTED_OR_SUBSTITUTED_CANDIDATE")
        if receipt.committed:
            lifecycle.commit(
                frame=receipt.frame,
                installed_route_identity=receipt.installed_route_identity,
                installed_route_generation=receipt.installed_route_generation,
            )
        return receipt


class FinishOldFirstBaseline:
    baseline_id = BaselineId.T_B3

    def __init__(self, accounting: PlanningAccounting):
        self.accounting = accounting
        self._pending: NavigationCandidate | None = None
        self._terminal: TransitionDecision | None = None

    def prepare_update(
        self, *, p_old: POldSnapshot, candidate: NavigationCandidate
    ) -> TransitionDecision:
        # Candidate preparation/storage is an experiment-owner operation.  The
        # B3 policy itself sees no P_new until the independent old terminal.
        build_policy_input(
            self.baseline_id,
            {
                "p_old": {"canonical_sha256": p_old.canonical_sha256},
                "old_terminal_state": "NOT_YET_TERMINAL",
            },
        )
        self._pending = candidate
        return TransitionDecision(
            TransitionOutcome.WAIT_OLD_MANEUVER_TERMINAL,
            ("P_OLD_REMAINS_AUTHORITATIVE_UNTIL_INDEPENDENT_OLD_TERMINAL",),
            self.baseline_id,
        )

    def on_old_terminal(
        self,
        *,
        opportunity_still_valid: bool,
        terminal_frame: int,
    ) -> TransitionDecision:
        if self._pending is None or self._terminal is not None:
            raise RuntimeError("T_B3_PENDING_STATE_INVALID")
        if not opportunity_still_valid:
            self._pending = None
            self._terminal = TransitionDecision(
                TransitionOutcome.MISSED_CURRENT_OPPORTUNITY,
                ("MISSED_CURRENT_OPPORTUNITY_NO_RESCUE_SEARCH",),
                self.baseline_id,
            )
            return self._terminal
        assert self._pending is not None
        build_policy_input(
            self.baseline_id,
            {
                "p_new": {"canonical_sha256": self._pending.canonical_sha256},
                "old_terminal_state": "INDEPENDENT_OLD_TERMINAL_REACHED",
                "current_opportunity_available": True,
            },
        )
        self._terminal = TransitionDecision(
            TransitionOutcome.COMMIT_NOW,
            ("OLD_MANEUVER_INDEPENDENT_TERMINAL_UPDATE_NOW_ELIGIBLE",),
            self.baseline_id,
        )
        return self._terminal

    @property
    def pending_candidate(self) -> NavigationCandidate | None:
        return self._pending

    def commit_or_record(
        self,
        *,
        lifecycle: CandidateLifecycle,
        installer: CandidateInstaller,
        boundary: PlanningBoundary,
        expected_old_generation: int,
    ) -> InstallReceipt | None:
        if self._terminal is None:
            raise RuntimeError("T_B3_OLD_TERMINAL_NOT_REACHED")
        if self._terminal.outcome is TransitionOutcome.MISSED_CURRENT_OPPORTUNITY:
            return None
        if self._terminal.outcome is not TransitionOutcome.COMMIT_NOW:
            raise RuntimeError("T_B3_TERMINAL_DISPOSITION_INVALID")
        if self._pending is None or self._pending.canonical_sha256 != lifecycle.candidate.canonical_sha256:
            raise RuntimeError("T_B3_PENDING_CANDIDATE_MISMATCH")
        if not boundary.legitimate:
            raise RuntimeError("T_B3_POST_TERMINAL_BOUNDARY_NOT_LEGITIMATE")
        lifecycle.admit(frame=boundary.frame, reason_code=self._terminal.reason_codes[0])
        receipt = _accounted_install(
            self.accounting,
            transaction_purpose="T_B3_POST_OLD_TERMINAL_TRANSACTION",
            installation_purpose="T_B3_POST_OLD_TERMINAL_INSTALL",
            installer=installer,
            candidate=lifecycle.candidate,
            expected_old_generation=expected_old_generation,
            frame=boundary.frame,
        )
        if receipt.committed:
            lifecycle.commit(
                frame=receipt.frame,
                installed_route_identity=receipt.installed_route_identity,
                installed_route_generation=receipt.installed_route_generation,
            )
        self._pending = None
        return receipt


@dataclass(frozen=True)
class BoundedLocalResult:
    local_navigation_condition: LocalNavigationCondition
    source_frame: int
    source_sim_time_s: float


class PureBoundedLocalPreparer:
    """Capability-confined B4 preparer with no planner/reconnect/owner handle."""

    capability_manifest = (
        "CURRENT_EGO",
        "NEW_INSTRUCTION",
        "BOUNDED_LOCAL_ROUTE_ROWS",
        "BOUNDED_TARGET_POINTS",
    )
    global_planner_capability = None
    reconnect_capability = None

    def prepare_bounded_local_condition(
        self,
        *,
        ego_state: Mapping[str, Any],
        new_instruction: str,
        local_branch_or_connector_identity: str,
        bounded_local_route_rows: tuple[RouteRow, ...],
        target_points_ego_local_xy_m: tuple[tuple[float, float], ...],
    ) -> BoundedLocalResult:
        if not bounded_local_route_rows or not target_points_ego_local_xy_m:
            raise RuntimeError("T_B4_BOUNDED_LOCAL_INPUT_MISSING")
        frame = int(ego_state["frame"])
        sim_time = float(ego_state["sim_time_s"])
        condition = LocalNavigationCondition(
            branch_or_connector_identity=local_branch_or_connector_identity,
            route_rows=tuple(bounded_local_route_rows),
            target_points_ego_local_xy_m=tuple(target_points_ego_local_xy_m),
            first_road_option=bounded_local_route_rows[0].road_option,
            preparation_receipt_sha256=canonical_sha256(
                {
                    "capability_manifest": self.capability_manifest,
                    "ego_state": dict(ego_state),
                    "new_instruction": new_instruction,
                    "branch": local_branch_or_connector_identity,
                    "bounded_local_route_rows": bounded_local_route_rows,
                    "target_points_ego_local_xy_m": target_points_ego_local_xy_m,
                    "global_planner_call_count": 0,
                    "reconnect_count": 0,
                }
            ),
        )
        return BoundedLocalResult(condition, frame, sim_time)


class LocalReplanOnlyBaseline:
    baseline_id = BaselineId.T_B4

    def __init__(
        self,
        source: AtomicAuthoritySource,
        local_preparer: PureBoundedLocalPreparer,
        accounting: PlanningAccounting,
    ) -> None:
        self.source = source
        if type(local_preparer) is not PureBoundedLocalPreparer:
            raise TypeError("T_B4_PREPARER_MUST_BE_CAPABILITY_CONFINED")
        self.local_preparer = local_preparer
        self.accounting = accounting

    def prepare_update(
        self,
        *,
        ego_state: Mapping[str, Any],
        new_instruction: str,
        semantic_update_event_id: str,
        updated_obligation_identity: str,
        local_branch_or_connector_identity: str,
        bounded_local_route_rows: tuple[RouteRow, ...],
        target_points_ego_local_xy_m: tuple[tuple[float, float], ...],
    ) -> NavigationCandidate:
        build_policy_input(
            self.baseline_id,
            {
                "ego_state": ego_state,
                "new_instruction": new_instruction,
                "local_navigation_candidate": {
                    "branch": local_branch_or_connector_identity,
                    "route_rows": bounded_local_route_rows,
                    "target_points": target_points_ego_local_xy_m,
                },
            },
        )
        before_export = self.source.export_atomic()
        before = AuthorityFingerprint.from_export(before_export)

        def prepare() -> BoundedLocalResult:
            return self.accounting.invoke(
                CallKind.LOCAL_PLANNER,
                "T_B4_BOUNDED_LOCAL_NAVIGATION_ONLY",
                self.local_preparer.prepare_bounded_local_condition,
                ego_state=ego_state,
                new_instruction=new_instruction,
                local_branch_or_connector_identity=local_branch_or_connector_identity,
                bounded_local_route_rows=bounded_local_route_rows,
                target_points_ego_local_xy_m=target_points_ego_local_xy_m,
            )

        local_result = self.accounting.invoke(
            CallKind.CANDIDATE_GENERATION,
            "T_B4_LOCAL_ONLY_CANDIDATE",
            lambda: CandidateIsolationGuard(self.source).prepare(prepare).prepared,
        )
        after = AuthorityFingerprint.from_export(self.source.export_atomic())
        if before.route_identity != after.route_identity or before.route_generation != after.route_generation:
            raise RuntimeError("T_B4_GLOBAL_ROUTE_IDENTITY_OR_GENERATION_CHANGED")
        if self.accounting.count(CallKind.GLOBAL_PLANNER) != 0:
            raise RuntimeError("T_B4_GLOBAL_PLANNER_CALL_FORBIDDEN")
        if self.accounting.count(CallKind.RECONNECT) != 0:
            raise RuntimeError("T_B4_GLOBAL_RECONNECT_CALL_FORBIDDEN")
        return NavigationCandidate.create(
            candidate_id="t-b4-" + canonical_sha256(
                {"update": semantic_update_event_id, "frame": local_result.source_frame}
            )[:24],
            candidate_route_identity=before.route_identity,
            candidate_route_generation=before.route_generation,
            global_task=before_export.global_task,
            semantic_update_event_id=semantic_update_event_id,
            updated_obligation_identity=updated_obligation_identity,
            local_branch_or_connector_identity=(
                local_result.local_navigation_condition.branch_or_connector_identity
            ),
            full_route_projection="NOT_APPLICABLE_BY_BASELINE",
            active_candidate_suffix=(
                local_result.local_navigation_condition.route_rows
            ),
            local_navigation_condition=local_result.local_navigation_condition,
            feasibility_state=CandidateFeasibility.FEASIBLE_NOW,
            feasibility_reason_codes=("BOUNDED_LOCAL_CONDITION_PREPARED",),
            candidate_source_frame=local_result.source_frame,
            candidate_source_sim_time_s=local_result.source_sim_time_s,
            candidate_generator_owner="driveclarify_t_mvp.LocalReplanOnlyBaseline",
            preparation_call_receipts=(
                local_result.local_navigation_condition.preparation_receipt_sha256,
            ),
        )


@dataclass(frozen=True)
class HistoryOnlyPayload:
    schema_version: str
    prompt_utf8: str
    prompt_sha256: str
    token_count: int
    tokenizer_sha256: str
    checkpoint_sha256: str
    visible_field_manifest: tuple[str, ...]
    truncation: str = "PROHIBITED"


class FrozenHistoryOnlySerializer:
    """Exact, escaped, no-truncation language/history serializer for T-B5."""

    schema_version = "driveclarify.rq2.history_only.v1"
    prefix = '<INSTRUCTION_HISTORY schema="driveclarify.rq2.history_only.v1">\n'
    separator = "\n"
    suffix = "\n</INSTRUCTION_HISTORY>\nPredict the waypoints."

    def __init__(self) -> None:
        self.tokenizer_sha256 = FROZEN_SIMLINGO_LOADED_TOKENIZER_SHA256
        self.checkpoint_sha256 = FROZEN_T_B5_FILES[FROZEN_SIMLINGO_CHECKPOINT]
        self.maximum_context_tokens = FROZEN_SIMLINGO_MAX_CONTEXT_TOKENS

    def serialize(
        self,
        *,
        old_instruction: str,
        new_instruction: str,
        tokenizer: object,
        extra_context: Mapping[str, Any] | None = None,
    ) -> HistoryOnlyPayload:
        if extra_context:
            # Reject experiment context before interacting with any tokenizer.
            build_policy_input(self.baseline_id, extra_context)
        tokenizer_owner = type(tokenizer).__module__ + "." + type(tokenizer).__qualname__
        tokenizer_name_or_path = str(getattr(tokenizer, "name_or_path", ""))
        if (
            tokenizer_owner != FROZEN_SIMLINGO_TOKENIZER_OWNER
            or tokenizer_name_or_path != FROZEN_SIMLINGO_MODEL_ID
            or _loaded_tokenizer_sha256(tokenizer) != self.tokenizer_sha256
        ):
            raise RuntimeError("T_B5_TOKENIZER_DIGEST_MISMATCH")
        prompt = self.compose_prompt(
            old_instruction=old_instruction, new_instruction=new_instruction
        )
        tokenize = getattr(tokenizer, "__call__", None)
        if not callable(tokenize):
            raise RuntimeError("T_B5_FROZEN_TOKENIZER_CALL_MISSING")
        tokenized = tokenize(prompt, add_special_tokens=False)
        input_ids = tokenized.get("input_ids") if isinstance(tokenized, Mapping) else None
        if not isinstance(input_ids, (list, tuple)) or (
            input_ids and isinstance(input_ids[0], (list, tuple))
        ):
            raise RuntimeError("T_B5_FROZEN_TOKENIZER_OUTPUT_INVALID")
        token_count = len(input_ids)
        if token_count > self.maximum_context_tokens:
            raise RuntimeError("T_B5_HISTORY_EXCEEDS_FROZEN_CONTEXT_NO_TRUNCATION")
        build_policy_input(
            BaselineId.T_B5,
            {
                "old_instruction": old_instruction,
                "new_instruction": new_instruction,
                "interaction_history": prompt,
            },
        )
        prompt_bytes = prompt.encode("utf-8")
        return HistoryOnlyPayload(
            schema_version=self.schema_version,
            prompt_utf8=prompt,
            prompt_sha256=bytes_sha256(prompt_bytes),
            token_count=token_count,
            tokenizer_sha256=self.tokenizer_sha256,
            checkpoint_sha256=self.checkpoint_sha256,
            visible_field_manifest=(
                "legitimate_native_observation_history",
                "old_instruction",
                "new_instruction",
            ),
        )

    def compose_prompt(self, *, old_instruction: str, new_instruction: str) -> str:
        """Compose exact escaped bytes; token eligibility needs the frozen owner."""

        safe_old = json.dumps(old_instruction, ensure_ascii=False, allow_nan=False)
        safe_new = json.dumps(new_instruction, ensure_ascii=False, allow_nan=False)
        prompt = (
            self.prefix
            + "OLD_JSON: "
            + safe_old
            + self.separator
            + "UPDATE_JSON: "
            + safe_new
            + self.suffix
        )
        build_policy_input(
            BaselineId.T_B5,
            {
                "old_instruction": old_instruction,
                "new_instruction": new_instruction,
                "interaction_history": prompt,
            },
        )
        return prompt

    @property
    def baseline_id(self) -> BaselineId:
        return BaselineId.T_B5


class HistoryOnlyBaseline:
    baseline_id = BaselineId.T_B5

    def __init__(self, serializer: FrozenHistoryOnlySerializer):
        self.serializer = serializer

    def prepare_update(self, **kwargs: Any) -> tuple[TransitionDecision, HistoryOnlyPayload]:
        payload = self.serializer.serialize(**kwargs)
        return (
            TransitionDecision(
                TransitionOutcome.HISTORY_PAYLOAD_READY,
                ("FROZEN_HISTORY_ONLY_SCALAR_PROMPT_READY",),
                self.baseline_id,
            ),
            payload,
        )


@dataclass(frozen=True)
class HistoryPromptBindingReceipt:
    prompt_sha256: str
    target_interface: str
    custom_prompt_installed: bool
    user_flag_unchanged: bool
    model_forward_count: int = 0


@dataclass(frozen=True)
class FrozenVLAIdentity:
    tokenizer_sha256: str
    checkpoint_sha256: str
    normal_forward_owner_identity: str
    agent_source_sha256: str
    tick_source_sha256: str
    normal_forward_source_sha256: str
    config_sha256: str
    frozen_registry_sha256: str
    weights_and_decoding_frozen: bool

    def __post_init__(self) -> None:
        if (
            len(self.tokenizer_sha256) != 64
            or len(self.checkpoint_sha256) != 64
            or not self.normal_forward_owner_identity
            or len(self.agent_source_sha256) != 64
            or len(self.tick_source_sha256) != 64
            or len(self.normal_forward_source_sha256) != 64
            or len(self.config_sha256) != 64
            or len(self.frozen_registry_sha256) != 64
            or self.weights_and_decoding_frozen is not True
        ):
            raise ValueError("T_B5_FROZEN_VLA_IDENTITY_INVALID")

    @classmethod
    def from_loaded_agent(cls, agent: object) -> "FrozenVLAIdentity":
        """Measure only the exact pre-frozen real LingoAgent and load path."""

        agent_type = type(agent)
        owner = agent_type.__module__ + "." + agent_type.__qualname__
        source_file = inspect.getsourcefile(agent_type)
        if owner != FROZEN_SIMLINGO_AGENT_OWNER or source_file is None:
            raise RuntimeError("T_B5_AGENT_OWNER_OR_SOURCE_NOT_FROZEN")
        if Path(source_file).resolve() != FROZEN_SIMLINGO_AGENT_SOURCE.resolve():
            raise RuntimeError("T_B5_AGENT_OWNER_OR_SOURCE_NOT_FROZEN")
        registry_sha256 = assert_frozen_t_b5_registry()
        run_step = getattr(agent_type, "run_step", None)
        tick = getattr(agent_type, "tick", None)
        tokenizer = getattr(agent, "tokenizer", None)
        if not callable(run_step) or not callable(tick) or tokenizer is None:
            raise RuntimeError("T_B5_LOADED_VLA_INTERFACE_INCOMPLETE")
        checkpoint_path = Path(str(getattr(agent, "config_path", ""))).resolve()
        config_path = Path(str(getattr(agent, "config_load_path", ""))).resolve()
        if checkpoint_path != FROZEN_SIMLINGO_CHECKPOINT.resolve():
            raise RuntimeError("T_B5_LOADED_CHECKPOINT_PATH_NOT_FROZEN")
        if config_path != FROZEN_SIMLINGO_CONFIG.resolve():
            raise RuntimeError("T_B5_LOADED_CONFIG_PATH_NOT_FROZEN")
        if getattr(agent, "model", None) is None:
            raise RuntimeError("T_B5_LOADED_MODEL_OWNER_MISSING")
        tokenizer_sha256 = _loaded_tokenizer_sha256(tokenizer)
        if tokenizer_sha256 != FROZEN_SIMLINGO_LOADED_TOKENIZER_SHA256:
            raise RuntimeError("T_B5_RUNTIME_TOKENIZER_IDENTITY_MISMATCH")
        return cls(
            tokenizer_sha256=tokenizer_sha256,
            checkpoint_sha256=FROZEN_T_B5_FILES[FROZEN_SIMLINGO_CHECKPOINT],
            normal_forward_owner_identity=owner,
            agent_source_sha256=file_sha256(FROZEN_SIMLINGO_AGENT_SOURCE),
            tick_source_sha256=bytes_sha256(inspect.getsource(tick).encode("utf-8")),
            normal_forward_source_sha256=bytes_sha256(
                inspect.getsource(run_step).encode("utf-8")
            ),
            config_sha256=FROZEN_T_B5_FILES[FROZEN_SIMLINGO_CONFIG],
            frozen_registry_sha256=registry_sha256,
            weights_and_decoding_frozen=True,
        )


def _loaded_tokenizer_sha256(tokenizer: object) -> str:
    get_vocab = getattr(tokenizer, "get_vocab", None)
    if not callable(get_vocab):
        raise RuntimeError("T_B5_RUNTIME_TOKENIZER_VOCAB_UNAVAILABLE")
    vocab = get_vocab()
    if not isinstance(vocab, Mapping) or not vocab:
        raise RuntimeError("T_B5_RUNTIME_TOKENIZER_VOCAB_INVALID")
    special_tokens = getattr(tokenizer, "special_tokens_map", {})
    return canonical_sha256(
        {
            "schema_version": "driveclarify.rq2.loaded_tokenizer_identity.v1",
            "type_identity": type(tokenizer).__module__
            + "."
            + type(tokenizer).__qualname__,
            "name_or_path": str(getattr(tokenizer, "name_or_path", "")),
            "padding_side": str(getattr(tokenizer, "padding_side", "")),
            "vocab": tuple(sorted((str(key), int(value)) for key, value in vocab.items())),
            "special_tokens": tuple(
                sorted((str(key), str(value)) for key, value in dict(special_tokens).items())
            ),
        }
    )


def bind_history_payload_to_frozen_agent(
    agent: object,
    payload: HistoryOnlyPayload,
    *,
    frozen_vla_identity: FrozenVLAIdentity,
) -> HistoryPromptBindingReceipt:
    """Install only scalar ``custom_prompt``; never touch decoding/flags/forward."""

    if not hasattr(agent, "custom_prompt"):
        raise RuntimeError("T_B5_FROZEN_CUSTOM_PROMPT_INTERFACE_MISSING")
    observed_identity = FrozenVLAIdentity.from_loaded_agent(agent)
    if observed_identity != frozen_vla_identity:
        raise RuntimeError("T_B5_FROZEN_VLA_REGISTRY_IDENTITY_MISMATCH")
    if payload.tokenizer_sha256 != frozen_vla_identity.tokenizer_sha256:
        raise RuntimeError("T_B5_RUNTIME_TOKENIZER_IDENTITY_MISMATCH")
    if payload.checkpoint_sha256 != frozen_vla_identity.checkpoint_sha256:
        raise RuntimeError("T_B5_RUNTIME_CHECKPOINT_IDENTITY_MISMATCH")
    user_flag_before = getattr(agent, "user_flag", None)
    setattr(agent, "custom_prompt", payload.prompt_utf8)
    return HistoryPromptBindingReceipt(
        prompt_sha256=payload.prompt_sha256,
        target_interface=(
            frozen_vla_identity.normal_forward_owner_identity + ".custom_prompt"
        ),
        custom_prompt_installed=getattr(agent, "custom_prompt") == payload.prompt_utf8,
        user_flag_unchanged=getattr(agent, "user_flag", None) == user_flag_before,
    )


_FROZEN_ADMISSIBILITY_FACTORY_SEAL = object()


@dataclass(frozen=True)
class FrozenAdmissibility:
    owner_disposition: str
    same_G: bool
    hard_safety_allows: bool
    reason_codes: tuple[str, ...]
    source_owner_identity: str
    source_result_sha256: str
    assessed_route_identity: str
    owner_registry_sha256: str
    oracle_fields_present: bool = False
    _factory_seal: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self._factory_seal is not _FROZEN_ADMISSIBILITY_FACTORY_SEAL:
            raise RuntimeError("T_B6_ADMISSIBILITY_NOT_CREATED_BY_FROZEN_OWNER_BINDING")
        if self.source_owner_identity != FROZEN_V11_ADMISSIBILITY_OWNER:
            raise RuntimeError("T_B6_ADMISSIBILITY_OWNER_IDENTITY_MISMATCH")
        if self.owner_registry_sha256 != assert_frozen_v11_admissibility_owner():
            raise RuntimeError("T_B6_ADMISSIBILITY_OWNER_REGISTRY_MISMATCH")
        if self.owner_disposition not in {
            "COMMIT_NOW",
            "DEFER_COMMIT",
            "REJECT_STALE_OR_INFEASIBLE",
        }:
            raise ValueError("FROZEN_ADMISSIBILITY_DISPOSITION_INVALID")
        if self.oracle_fields_present:
            raise ValueError("FROZEN_ADMISSIBILITY_ORACLE_FIELD_PRESENT")
        if (
            not self.reason_codes
            or not self.source_owner_identity
            or not self.assessed_route_identity
        ):
            raise ValueError("FROZEN_ADMISSIBILITY_PROVENANCE_MISSING")
        if len(self.source_result_sha256) != 64:
            raise ValueError("FROZEN_ADMISSIBILITY_RESULT_HASH_INVALID")

    @classmethod
    def _from_frozen_owner_result(
        cls,
        result: object,
        *,
        owner_registry_sha256: str,
        source_operand_commitment_point_index: object,
        same_G: bool,
        hard_safety_allows: bool,
    ) -> "FrozenAdmissibility":
        from driveclarify_clear_passthrough_v11.contracts import (
            AdmissibilityResult,
            TransitionDisposition,
        )

        if source_operand_commitment_point_index is not None:
            raise RuntimeError("RQ2_AUTHORED_COMMITMENT_INDEX_FORBIDDEN")
        if type(result) is not AdmissibilityResult:
            raise RuntimeError("T_B6_FROZEN_ADMISSIBILITY_RESULT_TYPE_MISMATCH")
        raw_disposition = getattr(result, "disposition", None)
        if type(raw_disposition) is not TransitionDisposition:
            raise RuntimeError("T_B6_FROZEN_ADMISSIBILITY_DISPOSITION_TYPE_MISMATCH")
        disposition = getattr(raw_disposition, "value", raw_disposition)
        reasons = tuple(str(value) for value in getattr(result, "reason_codes", ()))
        return cls(
            owner_disposition=str(disposition),
            same_G=bool(same_G),
            hard_safety_allows=bool(hard_safety_allows),
            reason_codes=reasons,
            source_owner_identity=FROZEN_V11_ADMISSIBILITY_OWNER,
            source_result_sha256=canonical_sha256(
                {
                    "disposition": str(disposition),
                    "reason_codes": reasons,
                    "route_id": getattr(result, "route_id", None),
                    "missed_replan_opportunity": getattr(
                        result, "missed_replan_opportunity", None
                    ),
                    "source_operand_commitment_point_index": None,
                }
            ),
            assessed_route_identity=str(getattr(result, "route_id", "")),
            owner_registry_sha256=owner_registry_sha256,
            _factory_seal=_FROZEN_ADMISSIBILITY_FACTORY_SEAL,
        )


def assess_with_existing_oracle_free_admissibility_owner(
    owner: object,
    *,
    ego: object,
    current_route: object,
    resolved_route: object,
    preview_plan: object | None,
    same_G: bool,
    hard_safety_allows: bool,
) -> FrozenAdmissibility:
    """Bind existing V11 generic gates with the authored index explicitly absent."""

    if getattr(resolved_route, "commitment_point_index", None) is not None:
        raise RuntimeError("RQ2_AUTHORED_COMMITMENT_INDEX_FORBIDDEN")
    owner_registry_sha256 = assert_frozen_v11_admissibility_owner(owner)
    result = owner.assess(
        ego, current_route, resolved_route, preview_plan=preview_plan
    )
    return FrozenAdmissibility._from_frozen_owner_result(
        result,
        owner_registry_sha256=owner_registry_sha256,
        source_operand_commitment_point_index=None,
        same_G=same_G,
        hard_safety_allows=hard_safety_allows,
    )


class DriveClarifyTransitionBaseline:
    baseline_id = BaselineId.T_B6

    def __init__(self, accounting: PlanningAccounting | None = None) -> None:
        self.accounting = accounting or PlanningAccounting()
        self._pending_candidate_sha256: str | None = None

    def decide_transition(
        self,
        *,
        p_old: POldSnapshot,
        p_new: NavigationCandidate,
        ego_state: Mapping[str, Any],
        observable_commitment: ObservableCommitment,
        global_task: GlobalTask,
        admissibility: FrozenAdmissibility,
        transition_state: Mapping[str, Any] | None = None,
    ) -> TransitionDecision:
        if admissibility.owner_registry_sha256 != assert_frozen_v11_admissibility_owner():
            raise RuntimeError("T_B6_ADMISSIBILITY_OWNER_REGISTRY_MISMATCH")
        build_policy_input(
            self.baseline_id,
            {
                "p_old": {"canonical_sha256": p_old.canonical_sha256},
                "p_new": {"canonical_sha256": p_new.canonical_sha256},
                "ego_state": ego_state,
                "observable_commitment": {
                    "state": observable_commitment.state.value,
                    "reason_codes": observable_commitment.reason_codes,
                },
                "global_task_G": {"identity": global_task.identity},
                "admissibility": {
                    "owner_disposition": admissibility.owner_disposition,
                    "same_G": admissibility.same_G,
                    "hard_safety_allows": admissibility.hard_safety_allows,
                    "reason_codes": admissibility.reason_codes,
                    "source_owner_identity": admissibility.source_owner_identity,
                    "source_result_sha256": admissibility.source_result_sha256,
                    "assessed_route_identity": admissibility.assessed_route_identity,
                    "owner_registry_sha256": admissibility.owner_registry_sha256,
                },
                "transition_state": transition_state or {},
            },
        )
        if (
            p_old.global_task != global_task
            or p_new.global_task != global_task
            or not admissibility.same_G
        ):
            return self._reject("SAME_DESTINATION_CONTRACT_FAILED")
        if admissibility.assessed_route_identity != p_new.candidate_route_identity:
            return self._reject("ADMISSIBILITY_CANDIDATE_ROUTE_IDENTITY_MISMATCH")
        if (
            not admissibility.hard_safety_allows
            or p_new.feasibility_state
            in {
                CandidateFeasibility.NO_SAFE_CURRENT_OPPORTUNITY,
                CandidateFeasibility.UNKNOWN,
            }
        ):
            return self._reject(*(admissibility.reason_codes or ("CANDIDATE_INADMISSIBLE",)))
        state = observable_commitment.state
        if state is ObservableCommitmentState.UNKNOWN:
            return self._reject("OBSERVABLE_COMMITMENT_UNKNOWN_FAIL_CLOSED")
        if state is ObservableCommitmentState.NO_SAFE_CURRENT_OPPORTUNITY:
            return self._reject("NO_SAFE_CURRENT_OPPORTUNITY")
        if admissibility.owner_disposition == "REJECT_STALE_OR_INFEASIBLE":
            return self._reject(*admissibility.reason_codes)
        if admissibility.owner_disposition == "DEFER_COMMIT":
            return TransitionDecision(
                TransitionOutcome.DEFER_COMMIT,
                admissibility.reason_codes,
                self.baseline_id,
            )
        if state is ObservableCommitmentState.OLD_EXCLUSIVE_RECOVERABLE:
            return TransitionDecision(
                TransitionOutcome.DEFER_COMMIT,
                ("OLD_EXCLUSIVE_RECOVERABLE_REUSE_SAME_PENDING_CANDIDATE",),
                self.baseline_id,
            )
        if state is ObservableCommitmentState.BEFORE_OLD_EXCLUSIVITY:
            return TransitionDecision(
                TransitionOutcome.COMMIT_NOW,
                ("ROUTE_TRANSITION_ADMISSIBLE_BEFORE_OLD_EXCLUSIVITY",),
                self.baseline_id,
            )
        return self._reject("OBSERVABLE_COMMITMENT_UNKNOWN_FAIL_CLOSED")

    def commit_or_defer(
        self,
        *,
        decision: TransitionDecision,
        lifecycle: CandidateLifecycle,
        boundary: PlanningBoundary,
        installer: CandidateInstaller,
        expected_old_generation: int,
    ) -> InstallReceipt | None:
        if decision.baseline_id is not self.baseline_id:
            raise RuntimeError("T_B6_DECISION_OWNER_MISMATCH")
        if decision.outcome is TransitionOutcome.REJECT_STALE_OR_INFEASIBLE:
            self._pending_candidate_sha256 = None
            return None
        if decision.outcome is TransitionOutcome.DEFER_COMMIT:
            if lifecycle.state is PlanState.CANDIDATE_PREPARED:
                lifecycle.admit(frame=boundary.frame, reason_code=decision.reason_codes[0])
            elif lifecycle.state is not PlanState.CANDIDATE_ADMITTED:
                raise RuntimeError("T_B6_DEFER_CANDIDATE_STATE_INVALID")
            if (
                self._pending_candidate_sha256 is not None
                and self._pending_candidate_sha256 != lifecycle.candidate.canonical_sha256
            ):
                raise RuntimeError("T_B6_PENDING_CANDIDATE_SUBSTITUTED")
            self._pending_candidate_sha256 = lifecycle.candidate.canonical_sha256
            return None
        if decision.outcome is not TransitionOutcome.COMMIT_NOW:
            raise RuntimeError("T_B6_TRANSITION_OUTCOME_INVALID")
        if not boundary.legitimate:
            raise RuntimeError("T_B6_INSTALL_BOUNDARY_NOT_LEGITIMATE")
        if lifecycle.state is PlanState.CANDIDATE_PREPARED:
            lifecycle.admit(frame=boundary.frame, reason_code=decision.reason_codes[0])
        elif lifecycle.state is PlanState.CANDIDATE_ADMITTED:
            if self._pending_candidate_sha256 != lifecycle.candidate.canonical_sha256:
                raise RuntimeError("T_B6_PENDING_CANDIDATE_SUBSTITUTED")
        else:
            raise RuntimeError("T_B6_COMMIT_CANDIDATE_STATE_INVALID")
        receipt = _accounted_install(
            self.accounting,
            transaction_purpose="T_B6_FROZEN_ROUTE_TRANSACTION",
            installation_purpose="T_B6_FROZEN_EXACTLY_ONCE_INSTALL",
            installer=installer,
            candidate=lifecycle.candidate,
            expected_old_generation=expected_old_generation,
            frame=boundary.frame,
        )
        if receipt.installed_candidate_sha256 != lifecycle.candidate.canonical_sha256:
            raise RuntimeError("T_B6_INSTALLED_CANDIDATE_HASH_MISMATCH")
        if receipt.committed:
            lifecycle.commit(
                frame=receipt.frame,
                installed_route_identity=receipt.installed_route_identity,
                installed_route_generation=receipt.installed_route_generation,
            )
        self._pending_candidate_sha256 = None
        return receipt

    def _reject(self, *reasons: str) -> TransitionDecision:
        return TransitionDecision(
            TransitionOutcome.REJECT_STALE_OR_INFEASIBLE,
            tuple(reasons),
            self.baseline_id,
        )
