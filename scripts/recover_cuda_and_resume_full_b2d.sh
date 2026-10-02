#!/usr/bin/env bash
set -euo pipefail
campaign_root=/home/buaa/wrh/DriveClarify
campaign_out="$campaign_root/reports/driveclarify_transparent_bypass_full_bench2drive_v2"
campaign_python=/home/buaa/anaconda3/envs/simlingo/bin/python
if [[ "$EUID" == 0 ]]; then
  echo "请以 buaa 用户运行本脚本；脚本会仅为计算模块重载调用 sudo。" >&2
  exit 70
fi
if systemctl --user is-active --quiet driveclarify_full_b2d_v2.service; then
  echo "已有活动评测服务，拒绝重载模块或重复启动。" >&2
  exit 73
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# CUDA已恢复时直接执行校验续跑；不做不必要的模块重载。
if ! "$campaign_python" -B "$campaign_out/tooling/cuda_readiness_probe.py"; then
  active_compute="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)"
  if [[ -n "$active_compute" ]]; then
    echo "存在其他CUDA计算进程；保留其运行，本脚本停止。" >&2
    exit 70
  fi
  if [[ "$(cat /sys/module/nvidia_uvm/refcnt)" != 0 ]]; then
    echo "nvidia_uvm仍被占用，停止；不强制卸载。" >&2
    exit 70
  fi
  sudo_flags=()
  if [[ "${1:-}" == "--noninteractive" ]]; then sudo_flags=(-n); fi
  # 仅重载未占用的UVM计算模块；不卸载nvidia显示驱动，不重启机器。
  sudo "${sudo_flags[@]}" /sbin/modprobe -r nvidia_uvm
  sudo "${sudo_flags[@]}" /sbin/modprobe nvidia_uvm
  /usr/bin/nvidia-modprobe -u -c=0
fi
exec "$campaign_python" -B "$campaign_out/tooling/resume_after_cuda_recovery.py"
