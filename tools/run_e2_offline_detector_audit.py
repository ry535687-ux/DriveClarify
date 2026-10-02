#!/usr/bin/env python3
"""Replay the frozen image-only detector on selected audit frames.

This is a post-episode diagnostic.  It never imports a CARLA actor, route
planner, control object, candidate policy, or the E2 provider.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_language_grounding_v1.visual_grounder import GroundingDinoVisualGrounder


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("frames", nargs="+")
    args = parser.parse_args()

    grounder = GroundingDinoVisualGrounder(device=args.device)
    rows = []
    for raw_path in args.frames:
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
        image = np.ascontiguousarray(rgb[:, :, ::-1])
        frame_id = int(path.stem.rsplit("_", 1)[-1])
        result = grounder.ground(
            image,
            "white car",
            frame_id=frame_id,
            observation_id="post-episode-offline-detector-audit:{}".format(frame_id),
        )
        rows.append(
            {
                "frame_id": frame_id,
                "image_path": str(path),
                "status": result.status.value,
                "raw_grounding_k": result.raw_grounding_k,
                "plausible_k": result.plausible_k,
                "effective_k": result.effective_k,
                "reason_codes": list(result.reason_codes),
                "raw_referents": [row.to_dict() for row in result.raw_referents],
                "selected_referents": [row.to_dict() for row in result.selected_referents],
            }
        )
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": "driveclarify.rq2_t_v2.e2_offline_detector_audit.v1",
                "post_episode_diagnostic_only": True,
                "runtime_signal": False,
                "oracle_reads": 0,
                "device": args.device,
                "detector_forward_count": grounder.forward_count,
                "rows": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
