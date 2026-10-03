"""Simulation-time terminal and censoring classification."""
from __future__ import annotations


import hashlib


import json


import math


import os


from dataclasses import asdict, dataclass


from enum import Enum


from pathlib import Path


from typing import Any, Mapping, Optional, Sequence


MAX_2A_SIMULATED_HORIZON_S = 35.0


FIXED_DELTA_SECONDS = 0.05


EARLY_ACTIONABLE_LOWER_S = 3.0


LATE_ACTIONABLE_LOWER_S = 1.20


_VALID_NATURAL_STATES = frozenset(
    {
        "PY_TREES_STATUS_SUCCESS",
        "PY_TREES_STATUS_FAILURE",
    }
)


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        .encode("utf-8")
    ).hexdigest()


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


class TTCmtStratum(str, Enum):
    EARLY_ACTIONABLE = "EARLY_ACTIONABLE"
    LATE_ACTIONABLE = "LATE_ACTIONABLE"
    TOO_LATE_NONACTIONABLE = "TOO_LATE_NONACTIONABLE"
    COMMITMENT_BOUNDARY = "COMMITMENT_BOUNDARY"


def classify_ttcmt_stratum(ttcmt_simulation_s: float) -> TTCmtStratum:
    if not _finite(ttcmt_simulation_s) or float(ttcmt_simulation_s) < 0.0:
        raise ValueError("RQ2_T_TTCMT_STRATUM_INPUT_INVALID")
    ttcmt = float(ttcmt_simulation_s)
    if ttcmt >= EARLY_ACTIONABLE_LOWER_S:
        return TTCmtStratum.EARLY_ACTIONABLE
    if ttcmt >= LATE_ACTIONABLE_LOWER_S:
        return TTCmtStratum.LATE_ACTIONABLE
    if ttcmt > 0.0:
        return TTCmtStratum.TOO_LATE_NONACTIONABLE
    return TTCmtStratum.COMMITMENT_BOUNDARY


class ExecutionTerminalClass(str, Enum):
    COMMITMENT_OBSERVED = "COMMITMENT_OBSERVED"
    NATURAL_SCENARIO_TREE_SUCCESS = "NATURAL_SCENARIO_TREE_SUCCESS"
    NATURAL_SCENARIO_TREE_FAILURE = "NATURAL_SCENARIO_TREE_FAILURE"
    MAX_SIMULATED_HORIZON_REACHED = "MAX_SIMULATED_HORIZON_REACHED"
    INVALID_ENGINEERING_TERMINATION = "INVALID_ENGINEERING_TERMINATION"


class H1CensoringStatus(str, Enum):
    EVENT_TIME_OBSERVED = "EVENT_TIME_OBSERVED"
    RIGHT_CENSORED_AT_NATURAL_TERMINAL = "RIGHT_CENSORED_AT_NATURAL_TERMINAL"
    RIGHT_CENSORED_AT_ADMINISTRATIVE_HORIZON = "RIGHT_CENSORED_AT_ADMINISTRATIVE_HORIZON"
    NOT_ESTIMABLE_ENGINEERING_INVALID = "NOT_ESTIMABLE_ENGINEERING_INVALID"


class H2PrimaryCategory(str, Enum):
    WINDOW_OBSERVED = "WINDOW_OBSERVED"
    NO_WINDOW_EVIDENCE_TOO_LATE = "NO_WINDOW_EVIDENCE_TOO_LATE"
    NO_WINDOW_EVIDENCE_NEVER_SUFFICIENT = "NO_WINDOW_EVIDENCE_NEVER_SUFFICIENT"
    RIGHT_CENSORED = "RIGHT_CENSORED"
    COMMITMENT_NOT_REACHED = "COMMITMENT_NOT_REACHED"
    INVALID_ENGINEERING_EVIDENCE = "INVALID_ENGINEERING_EVIDENCE"


class TerminalClassification:
    execution_terminal_class: str
    commitment_observed: bool
    commitment_time_simulation_s: Optional[float]
    ttcmt_simulation_s: Optional[float]
    opportunity_duration_simulation_s: Optional[float]
    h1_censoring_status: str
    h2_primary_category: str
    denominator_eligible: bool
    natural_terminal_state: Optional[str]
    wall_time_has_scientific_authority: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["classification_digest"] = _canonical_sha256(value)
        return value


def classify_primary_terminal(
    *,
    engineering_integrity_valid: bool,
    commitment_time_simulation_s: Optional[float],
    first_epistemic_sufficient_time_simulation_s: Optional[float],
    clarification_deadline_simulation_s: Optional[float],
    simulation_elapsed_s: float,
    natural_terminal_state: Optional[str] = None,
    wall_timeout_observed: bool = False,
) -> TerminalClassification:
    """按冻结优先级分类；wall 信号只能使工程证据失效。"""

    if not _finite(simulation_elapsed_s) or float(simulation_elapsed_s) < 0.0:
        raise ValueError("RQ2_T_SIMULATION_ELAPSED_INVALID")
    engineering_valid = bool(engineering_integrity_valid) and not wall_timeout_observed
    if natural_terminal_state is not None and natural_terminal_state not in _VALID_NATURAL_STATES:
        engineering_valid = False
    if not engineering_valid:
        return TerminalClassification(
            execution_terminal_class=ExecutionTerminalClass.INVALID_ENGINEERING_TERMINATION.value,
            commitment_observed=False,
            commitment_time_simulation_s=None,
            ttcmt_simulation_s=None,
            opportunity_duration_simulation_s=None,
            h1_censoring_status=H1CensoringStatus.NOT_ESTIMABLE_ENGINEERING_INVALID.value,
            h2_primary_category=H2PrimaryCategory.INVALID_ENGINEERING_EVIDENCE.value,
            denominator_eligible=False,
            natural_terminal_state=natural_terminal_state,
        )

    if commitment_time_simulation_s is not None:
        if not _finite(commitment_time_simulation_s):
            raise ValueError("RQ2_T_COMMITMENT_TIME_INVALID")
        commitment = float(commitment_time_simulation_s)
        sufficient = first_epistemic_sufficient_time_simulation_s
        deadline = clarification_deadline_simulation_s
        if sufficient is not None and not _finite(sufficient):
            raise ValueError("RQ2_T_SUFFICIENCY_TIME_INVALID")
        if deadline is not None and not _finite(deadline):
            raise ValueError("RQ2_T_DEADLINE_INVALID")
        ttcmt = (
            None
            if sufficient is None or float(sufficient) > commitment
            else commitment - float(sufficient)
        )
        if sufficient is not None and deadline is not None and float(sufficient) <= float(deadline):
            category = H2PrimaryCategory.WINDOW_OBSERVED
            duration = max(0.0, float(deadline) - float(sufficient))
        elif sufficient is not None and deadline is not None and float(deadline) < float(sufficient) <= commitment:
            category = H2PrimaryCategory.NO_WINDOW_EVIDENCE_TOO_LATE
            duration = None
        else:
            category = H2PrimaryCategory.NO_WINDOW_EVIDENCE_NEVER_SUFFICIENT
            duration = None
        return TerminalClassification(
            execution_terminal_class=ExecutionTerminalClass.COMMITMENT_OBSERVED.value,
            commitment_observed=True,
            commitment_time_simulation_s=commitment,
            ttcmt_simulation_s=ttcmt,
            opportunity_duration_simulation_s=duration,
            h1_censoring_status=H1CensoringStatus.EVENT_TIME_OBSERVED.value,
            h2_primary_category=category.value,
            denominator_eligible=True,
            natural_terminal_state=natural_terminal_state,
        )

    if natural_terminal_state in _VALID_NATURAL_STATES and float(simulation_elapsed_s) < MAX_2A_SIMULATED_HORIZON_S:
        terminal = (
            ExecutionTerminalClass.NATURAL_SCENARIO_TREE_SUCCESS
            if natural_terminal_state == "PY_TREES_STATUS_SUCCESS"
            else ExecutionTerminalClass.NATURAL_SCENARIO_TREE_FAILURE
        )
        return TerminalClassification(
            execution_terminal_class=terminal.value,
            commitment_observed=False,
            commitment_time_simulation_s=None,
            ttcmt_simulation_s=None,
            opportunity_duration_simulation_s=None,
            h1_censoring_status=H1CensoringStatus.RIGHT_CENSORED_AT_NATURAL_TERMINAL.value,
            h2_primary_category=H2PrimaryCategory.RIGHT_CENSORED.value,
            denominator_eligible=True,
            natural_terminal_state=natural_terminal_state,
        )

    if float(simulation_elapsed_s) >= MAX_2A_SIMULATED_HORIZON_S:
        return TerminalClassification(
            execution_terminal_class=ExecutionTerminalClass.MAX_SIMULATED_HORIZON_REACHED.value,
            commitment_observed=False,
            commitment_time_simulation_s=None,
            ttcmt_simulation_s=None,
            opportunity_duration_simulation_s=None,
            h1_censoring_status=H1CensoringStatus.RIGHT_CENSORED_AT_ADMINISTRATIVE_HORIZON.value,
            h2_primary_category=H2PrimaryCategory.COMMITMENT_NOT_REACHED.value,
            denominator_eligible=True,
            natural_terminal_state=natural_terminal_state,
        )
    raise ValueError("RQ2_T_EPISODE_NOT_AT_SCIENTIFIC_TERMINAL")


