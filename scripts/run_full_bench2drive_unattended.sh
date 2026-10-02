#!/usr/bin/env bash
set -euo pipefail
campaign_root=/home/buaa/wrh/DriveClarify
campaign_service=driveclarify_full_b2d_v2.service
if [[ "${1:-}" != "--service" ]]; then
  if systemctl --user is-active --quiet "$campaign_service"; then
    echo "已有活动的无人值守服务，拒绝重复启动。" >&2
    exit 73
  fi
  exec systemctl --user start "$campaign_service"
fi
cd "$campaign_root"
source /home/buaa/anaconda3/etc/profile.d/conda.sh
conda activate /home/buaa/anaconda3/envs/simlingo
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority
export XDG_SESSION_TYPE=x11 XDG_SESSION_REMOTE=false
exec /home/buaa/anaconda3/envs/simlingo/bin/python -B \
  "$campaign_root/reports/driveclarify_transparent_bypass_full_bench2drive_v2/tooling/unattended.py"
