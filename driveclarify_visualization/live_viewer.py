"""Local native-window viewer for ``runtime/debug_visualization/latest.png``.

Run this only inside the target Ubuntu machine's active local desktop session.  It
does not connect to CARLA and does not forward graphics; it simply refreshes a local
OpenCV window from the recorder's latest atomic PNG.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="DriveClarify local VLA debug viewer")
    parser.add_argument(
        "--input",
        default="/home/buaa/wrh/DriveClarify/runtime/debug_visualization/latest.png",
        help="atomic latest.png produced by the debug recorder",
    )
    parser.add_argument("--fps", type=float, default=15.0, help="display refresh only")
    parser.add_argument("--window", default="DriveClarify — VLA Research Debug View")
    args = parser.parse_args()
    if not os.environ.get("DISPLAY"):
        raise SystemExit("BLOCKED: DISPLAY is unset; use the host's local desktop session")

    import cv2

    source = Path(args.input)
    delay_ms = max(1, int(1000.0 / max(args.fps, 1.0)))
    last_mtime = None
    latest = None
    cv2.namedWindow(args.window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(args.window, 1600, 900)
    try:
        while True:
            try:
                mtime = source.stat().st_mtime_ns
                if mtime != last_mtime:
                    candidate = cv2.imread(str(source), cv2.IMREAD_COLOR)
                    if candidate is not None:
                        latest = candidate
                        last_mtime = mtime
            except FileNotFoundError:
                pass
            if latest is not None:
                cv2.imshow(args.window, latest)
            key = cv2.waitKey(delay_ms) & 0xFF
            if key in (27, ord("q")):
                break
            if latest is None:
                time.sleep(min(0.05, delay_ms / 1000.0))
    finally:
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
