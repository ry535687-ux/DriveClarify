"""V2.5 lifecycle overlay for the byte-frozen Method V2 predicates.

The base class remains the sole owner of commitment and completion evaluation.
This overlay changes only when selected navigation is released and makes the
base navigation-evidence validator available during the committed interval.
"""

from __future__ import annotations

from typing import Any

from driveclarify_method_revision_v2.execution import (
    BoundedSemanticManeuverExecution,
    ManeuverExecutionState,
)


class CompletionHandoverManeuverExecution(BoundedSemanticManeuverExecution):
    """Keep selected navigation through commitment and release at completion."""

    schema_version = "driveclarify.method_v2_5.execution.v1"

    @property
    def selected_navigation_required(self) -> bool:
        return self.state in {
            ManeuverExecutionState.ACTIVE,
            ManeuverExecutionState.MANEUVER_COMMITTED,
        }

    def _transition(
        self,
        state: ManeuverExecutionState,
        reason_code: str,
        frame_id: int | None,
    ) -> ManeuverExecutionState:
        result = super()._transition(state, reason_code, frame_id)
        if state is ManeuverExecutionState.MANEUVER_COMMITTED:
            # The base field encoded the historical commitment-time release.
            # V2.5 keeps the predicate and transition, but moves release only.
            self.release_frame_id = None
        elif state is ManeuverExecutionState.MANEUVER_COMPLETED:
            self.release_frame_id = frame_id
        return result

    def record_navigation_tick(self, **evidence: Any) -> ManeuverExecutionState:
        if self.state is not ManeuverExecutionState.MANEUVER_COMMITTED:
            return super().record_navigation_tick(**evidence)

        # Reuse the byte-frozen base validator rather than clone its identity and
        # forward-accounting semantics. Restore COMMITTED only when validation
        # succeeded; fail-closed transitions remain effective and are relabelled
        # with their true prior state in the audit transition row.
        transition_count = len(self.transitions)
        self.state = ManeuverExecutionState.ACTIVE
        result = super().record_navigation_tick(**evidence)
        if result is ManeuverExecutionState.ACTIVE:
            self.state = ManeuverExecutionState.MANEUVER_COMMITTED
            return self.state
        if len(self.transitions) > transition_count:
            self.transitions[-1]["from"] = (
                ManeuverExecutionState.MANEUVER_COMMITTED.value
            )
        return result

    def summary(self) -> dict[str, Any]:
        value = super().summary()
        value.update(
            {
                "handover_trigger": "MANEUVER_COMPLETED",
                "semantic_commitment_releases_navigation": False,
                "completion_predicate_owner": (
                    "BYTE_FROZEN_METHOD_V2_BOUNDED_EXECUTION_OBSERVE"
                ),
                "completion_fixed_wait_dependency": False,
            }
        )
        return value
