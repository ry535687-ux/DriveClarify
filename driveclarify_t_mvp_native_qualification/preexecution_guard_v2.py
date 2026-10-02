"""V2 CLI wrapper adding bounded progress-monitor failure classes.

The V1 guard is a byte-frozen historical input.  This wrapper deliberately
reuses all of its validation, reservation, exposure, and closure logic while
extending only the engineering infrastructure failure vocabulary used by the
progress-aware launcher.
"""

from __future__ import annotations

from driveclarify_t_mvp_native_qualification import preexecution_guard as _v1


_v1.ALLOWED_INFRA_FAILURES = _v1.ALLOWED_INFRA_FAILURES | frozenset(
    {
        "INJECTOR_PROCESS_FAILED_BEFORE_AGENT_SETUP",
        "EVALUATOR_AGENT_SETUP_NO_PROGRESS_TIMEOUT",
        "EVALUATOR_ABSOLUTE_WALL_GUARD_EXPIRED",
    }
)


if __name__ == "__main__":
    raise SystemExit(_v1.main())
