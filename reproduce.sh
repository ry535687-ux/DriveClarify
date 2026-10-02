#!/usr/bin/env bash
# 安装锁定依赖，执行 CPU 回归、合成演示和论文计算。
set -euo pipefail
repro_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repro_python=python3
repro_venv="$repro_root/.venv-reproduce"
repro_output="$repro_root/build/reproduction-$(date +%Y%m%d-%H%M%S)-$$"
repro_install=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python) repro_python=$2; shift 2 ;;
    --venv) repro_venv=$2; shift 2 ;;
    --output) repro_output=$2; shift 2 ;;
    --skip-install) repro_install=0; shift ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done
cd "$repro_root"
if [[ ! -x "$repro_venv/bin/python" ]]; then
  "$repro_python" -c 'import sys; assert sys.version_info >= (3, 10), "需要 Python 3.10+"'
  "$repro_python" -m venv "$repro_venv"
fi
if [[ $repro_install == 1 ]]; then
  "$repro_venv/bin/python" -m pip install --disable-pip-version-check \
    --require-hashes --only-binary=:all: -r requirements/paper.lock.txt
fi
"$repro_venv/bin/python" engineering/reproduce.py doctor --profile paper
"$repro_venv/bin/python" engineering/reproduce.py test
"$repro_venv/bin/python" engineering/reproduce.py demo --output "$repro_output-demo"
"$repro_venv/bin/python" engineering/reproduce.py paper --output "$repro_output"
echo "复现完成：$repro_output/REPRODUCTION_RECEIPT.json"
