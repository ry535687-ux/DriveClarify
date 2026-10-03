#!/usr/bin/env bash
# Compatibility entry; installation is maintained in the package's setup tool.
set -euo pipefail
native_setup_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$native_setup_root"
exec bash reproduce.sh --native --environment-only "$@"
