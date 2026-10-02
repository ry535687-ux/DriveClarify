#!/usr/bin/env python3
"""Write one immutable post-episode E2 V3 visibility certificate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t_e2_v3.certification import certify_episode  # noqa: E402


REPORT = ROOT / "reports/driveclarify_rq2_t_v2_e2_tracked_association_and_full_mechanism_v1"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", required=True)
    args = parser.parse_args()
    output = REPORT / "NATIVE_EVIDENCE" / args.identity / "attempt_01"
    certificate = dict(certify_episode(output))
    target = REPORT / "SCENE_CERTIFICATES" / (args.identity + "_VISIBILITY_CERTIFICATE.json")
    atomic_json(target, certificate)
    atomic_json(output / "E2_V3_VISIBILITY_CERTIFICATE.json", certificate)
    print(json.dumps({
        "identity": args.identity, "status": certificate["certificate_status"],
        "first_visible_frame": certificate["first_physically_visible_and_discriminable_frame"],
        "sustained": certificate["sustained_visible_interval"], "target": str(target),
    }, indent=2, sort_keys=True))
    return 0 if certificate["certificate_status"] == "PASS_VISIBILITY_CERTIFIED_AND_E2_JOINED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
