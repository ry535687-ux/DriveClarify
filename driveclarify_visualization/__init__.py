"""DriveClarify Visualization Layer v0 (observer-only, diagnostic-only, non-blocking).

This package renders EXISTING CP3B offline data (and, in a future authorized round, a live
observer feed) into researcher-facing views. It never runs CARLA, never runs the model,
never advances the planner, never touches control, and never mutates any source artifact.

It does NOT change project_phase and NEVER upgrades any interface claim (F2/F4/F5/F6/TL)
to VERIFIED. Visualization completion is tracked separately as visualization_status.
"""

VISUALIZATION_VERSION = "driveclarify.visualization.v0"
