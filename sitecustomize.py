"""Narrow opt-in startup hook for the RQ2 evaluator-side safety observer.

All normal Python processes are unaffected.  Installation is attempted only
for the official leaderboard evaluator command and only under the explicit
qualification/pilot environment flag.
"""

import os
import sys


if (
    os.environ.get("DRIVECLARIFY_RQ2_FORCED_HORIZON_SAFETY_ENABLE") == "1"
    and sys.argv
    and str(sys.argv[0]).endswith("leaderboard_evaluator.py")
):
    from driveclarify_t_mvp.forced_horizon_safety_runtime import install_runtime_hook

    install_runtime_hook()
