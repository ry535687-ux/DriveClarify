"""Panel A (legacy replay): raw CARLA rgb_0 before SimLingo preprocessing.

The saved CP3B PNG is not the final ``DrivingInput.camera_images`` tensor: SimLingo
subsequently applies a JPEG round-trip, BGR->RGB, bottom crop, dynamic resize/patching,
ImageNet normalization, and bfloat16 conversion.  The VLA debug-upgrade captures that
actual tensor separately.  This legacy panel is now labelled RAW CAMERA explicitly.
"""

from __future__ import annotations

from typing import Any, Optional

from . import contracts as C


def _draw_caption(rgb_np, lines, fill=(180, 220, 255)):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    h, w = rgb_np.shape[:2]
    margin = 84
    canvas = np.zeros((h + margin, w, 3), dtype=np.uint8)
    canvas[margin:, :, :] = rgb_np
    img = Image.fromarray(canvas)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    y = 6
    for text, col in lines:
        d.text((8, y), text, fill=col, font=font)
        y += 20
    return np.asarray(img)


def render(frame: dict[str, Any], last_image_sha: Optional[str] = None):
    """Return an RGB ndarray of Panel A for this frame. Raw pixels are never modified.

    Enforces the raw/display contract: this is RAW_CAMERA_SENSOR with enhanced=False.
    """
    import numpy as np
    from PIL import Image
    C.assert_raw_not_relabeled(C.RAW_VIEW_KIND, enhanced_applied=False)

    path = frame.get("image_abspath")
    if not path:
        # honest empty panel, not a fake image
        blank = np.zeros((512 + 84, 1024, 3), dtype=np.uint8)
        return _draw_caption(np.zeros((512, 1024, 3), dtype=np.uint8),
                             [("RAW CAMERA rgb_0 (NO IMAGE)", (255, 80, 80)),
                              ("PRE-PROCESSING | NOT FINAL MODEL TENSOR", (180, 220, 255))])

    arr = np.asarray(Image.open(path).convert("RGB"))  # exact saved pixels
    sha = frame.get("image_sha256") or ""
    hold = (last_image_sha is not None and sha == last_image_sha)
    sfps = frame.get("_sensor_fps")
    lines = [
        (f"RAW CAMERA rgb_0 — PRE-PROCESSING  {frame.get('image_wh')}", (0, 255, 120)),
        (f"run={frame.get('run_id')}  frame={frame.get('image_frame')}  "
         f"obs={frame.get('observation_id')}", (200, 220, 255)),
        (f"sha={sha[:12]}  sensor_fps={'n/a' if sfps is None else round(sfps,1)}"
         + ("   LATEST FRAME HOLD" if hold else ""),
         (255, 180, 80) if hold else (170, 200, 230)),
        ("NOT FINAL MODEL TENSOR | SIMULATION ONLY | " + " | ".join(C.GLOBAL_LABELS), (255, 200, 0)),
    ]
    return _draw_caption(arr, lines)
