#!/usr/bin/env bash
# Native CARLA launcher adapter used only to make the physical two-window
# Stage 6A runtime observable.  It preserves every evaluator-supplied argument
# and adds window geometry; rendering stays enabled and no virtual display is
# involved.
set -euo pipefail

exec /home/buaa/CARLA_0.9.15/CarlaUE4.sh \
  -windowed \
  -ResX=1024 \
  -ResY=768 \
  -WinX=1536 \
  -WinY=45 \
  "$@"
