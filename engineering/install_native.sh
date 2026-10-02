#!/usr/bin/env bash
# 新建原生环境；不触碰已有 simlingo 环境，不启动 CARLA / 模型。
set -euo pipefail
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo "此原生环境清单适用于 Linux x86_64。" >&2
  exit 2
fi
export PYTHONNOUSERSITE=1
unset PYTHONPATH
native_setup_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
native_setup_prefix=${1:-"$native_setup_root/build/native-env"}
if [[ -e "$native_setup_prefix" ]]; then
  echo "环境路径已存在，拒绝覆盖：$native_setup_prefix" >&2
  exit 2
fi
conda create --yes --prefix "$native_setup_prefix" \
  --file "$native_setup_root/engineering/env/simlingo-conda-linux64.explicit.txt"
native_setup_python="$native_setup_prefix/bin/python"
"$native_setup_python" -m pip install numpy==1.23.0 \
  torch==2.2.0 torchvision==0.17.0 torchaudio==2.2.0 \
  --index-url https://pypi.org/simple
"$native_setup_python" -m pip install --index-url https://pypi.org/simple \
  -r "$native_setup_root/requirements/native-observed.txt"
DS_BUILD_OPS=0 "$native_setup_python" -m pip install --no-build-isolation \
  --index-url https://pypi.org/simple deepspeed==0.16.2
"$native_setup_python" -m pip check
echo "原生包安装结束；下一步准备 CARLA、模型和路线，然后执行 bench2drive.py preflight。"
