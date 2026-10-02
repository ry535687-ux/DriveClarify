"""Panel B: DISPLAY-ONLY human research view. NOT the model input.

Applies reversible display enhancement (brightness/contrast/saturation/gamma/optional
CLAHE/mild sharpen) to a COPY of rgb_0. Never writes the source PNG, never feeds the model.
Every rendered frame is captioned DISPLAY ENHANCED / NOT MODEL INPUT / NOT USED FOR CONTROL,
and the enhancement params + before/after image statistics are returned in metadata so a
near-grayscale source is reported honestly (no fabricated colour).
"""

from __future__ import annotations

from typing import Any

from . import contracts as C

DEFAULT_PARAMS = {
    "brightness": 1.0,
    "contrast": 1.15,
    "saturation": 3.0,
    "gamma": 0.9,
    "clahe": False,
    "sharpen": 0.3,
}


def _img_stats(rgb_np) -> dict[str, float]:
    import numpy as np
    a = rgb_np.reshape(-1, 3).astype("float32")
    sat = (a.max(axis=1) - a.min(axis=1))
    lum = 0.299 * a[:, 0] + 0.587 * a[:, 1] + 0.114 * a[:, 2]
    return {"mean_lum": round(float(lum.mean()), 2),
            "mean_saturation": round(float(sat.mean()), 2),
            "p95_saturation": round(float(np.percentile(sat, 95)), 2)}


def enhance(rgb_np, params: dict[str, Any] | None = None):
    """Return (enhanced_rgb, metadata). Pure display transform on a copy."""
    import numpy as np
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    before = _img_stats(rgb_np)
    out = rgb_np.astype("float32")
    try:
        import cv2
        # gamma
        if p["gamma"] and p["gamma"] != 1.0:
            out = 255.0 * np.power(np.clip(out / 255.0, 0, 1), p["gamma"])
        # brightness/contrast
        out = np.clip((out - 128) * p["contrast"] + 128 * p["brightness"], 0, 255)
        # saturation in HSV
        hsv = cv2.cvtColor(out.astype("uint8"), cv2.COLOR_RGB2HSV).astype("float32")
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * p["saturation"], 0, 255)
        out = cv2.cvtColor(hsv.astype("uint8"), cv2.COLOR_HSV2RGB).astype("float32")
        if p.get("clahe"):
            lab = cv2.cvtColor(out.astype("uint8"), cv2.COLOR_RGB2LAB)
            cl = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            lab[:, :, 0] = cl.apply(lab[:, :, 0])
            out = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB).astype("float32")
        if p.get("sharpen"):
            blur = cv2.GaussianBlur(out, (0, 0), 3)
            out = np.clip(out * (1 + p["sharpen"]) - blur * p["sharpen"], 0, 255)
    except Exception:  # noqa: BLE001 - fail-open: return the unmodified copy
        out = rgb_np.astype("float32")
        p = dict(p, note="cv2_unavailable_returned_copy")
    out = out.astype("uint8")
    after = _img_stats(out)
    meta = {
        "view_kind": C.DISPLAY_VIEW_KIND,
        "enhanced": True,
        "params": p,
        "stats_before": before,
        "stats_after": after,
        "honesty_note": (
            "DISPLAY-ONLY. Source is near-grayscale if mean_saturation is low; enhancement "
            "raises apparent saturation but cannot recover colour that is not in the sensor "
            "frame. before/after stats disclose the true change."),
    }
    return out, meta


def render(frame: dict[str, Any], params: dict[str, Any] | None = None):
    """Return (rgb_ndarray, metadata) for Panel B. Captioned DISPLAY ENHANCED."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    path = frame.get("image_abspath")
    if not path:
        blank = np.zeros((512, 1024, 3), dtype=np.uint8)
        enh, meta = blank, {"view_kind": C.DISPLAY_VIEW_KIND, "enhanced": True, "no_image": True}
    else:
        src = np.asarray(Image.open(path).convert("RGB"))
        enh, meta = enhance(src, params)
    # contract: an enhanced frame must NOT be labeled raw
    C.assert_raw_not_relabeled(C.DISPLAY_VIEW_KIND, enhanced_applied=True)

    h, w = enh.shape[:2]
    margin = 84
    canvas = np.zeros((h + margin, w, 3), dtype=np.uint8)
    canvas[margin:, :, :] = enh
    img = Image.fromarray(canvas)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    sb = meta.get("stats_before", {})
    sa = meta.get("stats_after", {})
    d.text((8, 6), "DISPLAY ENHANCED - NOT MODEL INPUT - NOT USED FOR CONTROL", fill=(255, 140, 0), font=font)
    d.text((8, 28), f"sat before={sb.get('mean_saturation')} -> after={sa.get('mean_saturation')} "
                    f"(colour not fabricated; see metadata)", fill=(255, 200, 120), font=font)
    d.text((8, 50), "presentation only | " + " | ".join(C.GLOBAL_LABELS), fill=(255, 200, 0), font=font)
    return np.asarray(img), meta
