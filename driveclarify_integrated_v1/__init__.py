"""DriveClarify integrated deployable offline method v1.

This package is deliberately CPU-only and diagnostic-only.  It never emits
vehicle control and it does not load evaluation labels.
"""

from .integrated_runtime import IntegratedRuntimeV1, run_integrated_runtime

__all__ = ["IntegratedRuntimeV1", "run_integrated_runtime"]

