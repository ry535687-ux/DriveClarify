#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_grounded_language_v1_extension_e1_r1.finalize import finalize  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(finalize(), ensure_ascii=False, indent=2, sort_keys=True))
