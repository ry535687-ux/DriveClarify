"""Read one operational, oracle-free native episode progress snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


HEALTHY = "HEALTHY"
PRE_AGENT_STARTUP_GUARD_EXPIRED = "PRE_AGENT_STARTUP_GUARD_EXPIRED"
POST_AGENT_NO_PROGRESS_GUARD_EXPIRED = "POST_AGENT_NO_PROGRESS_GUARD_EXPIRED"
ABSOLUTE_ENGINEERING_WALL_GUARD_EXPIRED = (
    "ABSOLUTE_ENGINEERING_WALL_GUARD_EXPIRED"
)


def _json_lines(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def progress_snapshot(
    *,
    state_log: Path,
    reservation_id: str,
    boundary_dir: Path,
) -> dict[str, Any]:
    """Return exposure plus the newest completely decoded boundary.

    Temporary files are intentionally outside the ``boundary_*.json`` glob.
    A malformed legacy final file is skipped so an operational observer cannot
    itself crash the episode; current producers publish atomically.
    """

    exposed = any(
        row.get("reservation_id") == reservation_id
        and row.get("event_type") == "AGENT_EXPOSURE"
        for row in _json_lines(state_log)
    )
    newest: dict[str, Any] | None = None
    for path in sorted(boundary_dir.glob("boundary_*.json"), reverse=True):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            frame = int(value["sim_frame"])
            sim_time_s = float(value["sim_time_s"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        newest = {
            "sim_frame": frame,
            "sim_time_s": sim_time_s,
            "boundary_path": str(path.resolve()),
        }
        break
    return {
        "agent_exposed": exposed,
        "sim_frame": -1 if newest is None else newest["sim_frame"],
        "sim_time_s": -1.0 if newest is None else newest["sim_time_s"],
        "boundary_path": None if newest is None else newest["boundary_path"],
    }


def classify_liveness(
    *,
    exposed: bool,
    now_epoch_s: int,
    evaluator_started_epoch_s: int,
    last_progress_epoch_s: int,
    new_progress_observed: bool,
    pre_agent_startup_guard_s: int,
    post_agent_no_progress_guard_s: int,
    absolute_evaluator_wall_guard_s: int,
) -> str:
    """Classify only engineering liveness; scientific time is not an input."""

    effective_last_progress = (
        now_epoch_s if new_progress_observed else last_progress_epoch_s
    )
    elapsed = now_epoch_s - evaluator_started_epoch_s
    if not exposed and elapsed >= pre_agent_startup_guard_s:
        return PRE_AGENT_STARTUP_GUARD_EXPIRED
    if exposed and now_epoch_s - effective_last_progress >= post_agent_no_progress_guard_s:
        return POST_AGENT_NO_PROGRESS_GUARD_EXPIRED
    if elapsed >= absolute_evaluator_wall_guard_s:
        return ABSOLUTE_ENGINEERING_WALL_GUARD_EXPIRED
    return HEALTHY


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-log", required=True)
    parser.add_argument("--reservation-id", required=True)
    parser.add_argument("--boundary-dir", required=True)
    parser.add_argument("--now-epoch-s", type=int)
    parser.add_argument("--evaluator-started-epoch-s", type=int)
    parser.add_argument("--last-progress-epoch-s", type=int)
    parser.add_argument("--previous-progress-frame", type=int, default=-1)
    parser.add_argument("--pre-agent-startup-guard-s", type=int, default=900)
    parser.add_argument("--post-agent-no-progress-guard-s", type=int, default=180)
    parser.add_argument("--absolute-evaluator-wall-guard-s", type=int, default=3600)
    args = parser.parse_args()
    value = progress_snapshot(
        state_log=Path(args.state_log),
        reservation_id=args.reservation_id,
        boundary_dir=Path(args.boundary_dir),
    )
    decision = HEALTHY
    decision_args = (
        args.now_epoch_s,
        args.evaluator_started_epoch_s,
        args.last_progress_epoch_s,
    )
    if any(value is not None for value in decision_args):
        if not all(value is not None for value in decision_args):
            parser.error("all liveness epoch arguments are required together")
        decision = classify_liveness(
            exposed=bool(value["agent_exposed"]),
            now_epoch_s=args.now_epoch_s,
            evaluator_started_epoch_s=args.evaluator_started_epoch_s,
            last_progress_epoch_s=args.last_progress_epoch_s,
            new_progress_observed=(
                int(value["sim_frame"]) > args.previous_progress_frame
            ),
            pre_agent_startup_guard_s=args.pre_agent_startup_guard_s,
            post_agent_no_progress_guard_s=args.post_agent_no_progress_guard_s,
            absolute_evaluator_wall_guard_s=args.absolute_evaluator_wall_guard_s,
        )
    print(
        int(value["agent_exposed"]),
        int(value["sim_frame"]),
        float(value["sim_time_s"]),
        decision,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
