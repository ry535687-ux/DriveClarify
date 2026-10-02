"""Agent-side hook that preserves official tick and sensor ownership."""

from __future__ import annotations

import threading
import re
from typing import Any, Callable, Dict, Optional

from .adapter import PDMBranchAdapter
from .contracts import SealedPDMBranchPlan


def authoritative_input_frame(input_data: Dict[str, Any]) -> int:
    """Require one already-synchronized official frame across all sensor rows."""
    if not isinstance(input_data, dict) or not input_data:
        raise ValueError("OFFICIAL_INPUT_DATA_REQUIRED")
    frames = set()
    for value in input_data.values():
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise ValueError("OFFICIAL_INPUT_DATA_ROW_INVALID")
        frame = value[0]
        if not isinstance(frame, int) or frame < 0:
            raise ValueError("OFFICIAL_INPUT_FRAME_INVALID")
        frames.add(frame)
    if len(frames) != 1:
        raise ValueError("OFFICIAL_INPUT_DATA_NOT_FRAME_SYNCHRONIZED")
    return next(iter(frames))


class BPlusOfficialAgentMixin(object):
    """Compose ahead of official DataAgent/AutoPilot without upstream edits.

    The future wrapper initializes this mixin after PDM planner setup.  A plan
    may be queued between ticks; it is installed only after the next official
    ``input_data`` exists and immediately before ``super().run_step`` computes
    the first selected-branch control.
    """

    def initialize_bplus_hook(
            self,
            adapter: PDMBranchAdapter,
            authoritative_anchor_identity: Callable[[Dict[str, Any], float], str],
            control_snapshot: Callable[[Any], Dict[str, Any]]) -> None:
        self._bplus_adapter = adapter
        self._bplus_authoritative_anchor_identity = authoritative_anchor_identity
        self._bplus_control_snapshot = control_snapshot
        self._bplus_pending_plan = None  # type: Optional[SealedPDMBranchPlan]
        self._bplus_hook_lock = threading.RLock()

    def queue_sealed_branch(self, plan: SealedPDMBranchPlan) -> None:
        if not isinstance(plan, SealedPDMBranchPlan):
            raise TypeError("SEALED_BRANCH_PLAN_REQUIRED")
        with self._bplus_hook_lock:
            if self._bplus_pending_plan is not None:
                raise RuntimeError("BRANCH_ALREADY_PENDING")
            self._bplus_pending_plan = plan

    def run_step(self, input_data: Dict[str, Any], timestamp: float, sensors=None, plant=False):
        with self._bplus_hook_lock:
            plan = self._bplus_pending_plan
            if plan is not None:
                frame = authoritative_input_frame(input_data)
                observed_anchor = self._bplus_authoritative_anchor_identity(
                    input_data, timestamp
                )
                if not isinstance(observed_anchor, str) or re.fullmatch(
                        r"[0-9a-f]{64}", observed_anchor) is None:
                    raise ValueError("AUTHORITATIVE_ANCHOR_IDENTITY_INVALID")
                if observed_anchor == plan.anchor_identity:
                    self._bplus_adapter.install_branch(
                        plan,
                        authoritative_anchor_frame=frame,
                    )
                    self._bplus_pending_plan = None
                else:
                    plan = None
        control = super().run_step(input_data, timestamp, sensors=sensors, plant=plant)
        if plan is not None:
            self._bplus_adapter.mark_first_selected_control_consumed(
                self._bplus_control_snapshot(control)
            )
        return control
