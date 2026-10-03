#!/usr/bin/env bash
set -euo pipefail
unset PYTHONPATH
export PYTHONNOUSERSITE=1
repro_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repro_python=python3
repro_venv="$repro_root/.venv"
repro_output="$repro_root/build/evaluation-$(date +%Y%m%d-%H%M%S)-$$"
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
  "$repro_python" -c 'import sys; assert sys.version_info >= (3,10), "需要 Python 3.10+"'
  "$repro_python" -m venv "$repro_venv"
fi
if [[ $repro_install == 1 ]]; then
  "$repro_venv/bin/python" -m pip install --disable-pip-version-check \
    --require-hashes --only-binary=:all: -r requirements/cpu.lock.txt
  "$repro_venv/bin/python" -m pip install --disable-pip-version-check \
    --no-build-isolation --no-deps -e .
fi
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
"$repro_venv/bin/python" -m pytest
"$repro_venv/bin/python" -m driveclarify evaluate --output "$repro_output"
echo "复现完成：$repro_output/receipt.json"
