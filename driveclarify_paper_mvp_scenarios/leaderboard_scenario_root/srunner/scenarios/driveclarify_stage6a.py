"""Leaderboard discovery shim for the promoted DriveClarify Stage 6A scene.

Bench2Drive's ``RouteScenario`` discovers scenario classes by scanning the
directory named by ``SCENARIO_RUNNER_ROOT``.  Keeping this tiny shim in the
DriveClarify repository lets the unmodified evaluator instantiate the exact
promoted handler without copying or editing SimLingo/ScenarioRunner sources.
"""

from driveclarify_paper_mvp_scenarios.scenario_runner_scenario import (
    DriveClarifyPaperMVPScenario,
)


__all__ = ["DriveClarifyPaperMVPScenario"]
