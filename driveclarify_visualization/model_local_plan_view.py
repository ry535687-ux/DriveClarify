"""Panel C-2: Model-Local RAW plan. pred_route / pred_speed_wps in their OWN raw frame.

Plots the two raw model output tensors in MODEL_LOCAL_RAW coordinates (index axis), with NO
conversion to metres, NO world frame, NO left/right label. dim1 sign is shown as a raw
number only. A metric overlay is gated by contracts.assert_metric_overlay_allowed and stays
OFF at v0 (no F5 VERIFIED). Labels: MODEL_LOCAL_RAW / RAW UNIT / NO PHYSICAL SCALE CLAIM.
"""

from __future__ import annotations

from typing import Any

from . import contracts as C


def render(frame: dict[str, Any], size_px: int = 512, enable_metric_overlay: bool = False):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    # Metric overlay must be blocked unless the dependent claim is VERIFIED (v0: none).
    if enable_metric_overlay:
        C.assert_metric_overlay_allowed("F5_pred_route")  # raises at v0

    img = Image.new("RGB", (size_px, size_px + 84), (16, 20, 16))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 15)
        small = ImageFont.truetype("DejaVuSans.ttf", 12)
    except Exception:  # noqa: BLE001
        font = small = ImageFont.load_default()

    pr = frame.get("pred_route_values")
    ps = frame.get("pred_speed_wps_values")

    def pts(t):
        try:
            return t[0]
        except Exception:  # noqa: BLE001
            return None

    route = pts(pr)
    speed = pts(ps)

    # autoscale in RAW units: dim0 (call it "raw fwd axis"), dim1 ("raw lateral axis").
    allpts = []
    for arr in (route, speed):
        if arr:
            allpts += [(p[0], p[1]) for p in arr if len(p) >= 2]
    top = 84
    cx = size_px * 0.15
    cy = top + size_px / 2
    if allpts:
        xs = [p[0] for p in allpts]
        ys = [p[1] for p in allpts]
        xr = max(1e-6, max(xs) - min(xs))
        yr = max(1e-6, max(abs(min(ys)), abs(max(ys)), 1e-6) * 2)
        sx = (size_px * 0.8) / xr
        sy = (size_px * 0.8) / yr
    else:
        sx = sy = 1.0

    def r2p(fwd, lat):
        return cx + fwd * sx, cy - lat * sy

    # axes
    d.line([(cx, top), (cx, top + size_px)], fill=(60, 70, 60))
    d.line([(0, cy), (size_px, cy)], fill=(60, 70, 60))

    if route:
        prev = None
        for p in route:
            xy = r2p(p[0], p[1])
            d.ellipse([xy[0] - 3, xy[1] - 3, xy[0] + 3, xy[1] + 3], fill=(230, 90, 90))
            if prev:
                d.line([prev, xy], fill=(180, 70, 70))
            prev = xy
    if speed:
        prev = None
        for p in speed:
            xy = r2p(p[0], p[1])
            d.ellipse([xy[0] - 2, xy[1] - 2, xy[0] + 2, xy[1] + 2], fill=(90, 220, 120))
            if prev:
                d.line([prev, xy], fill=(70, 180, 100))
            prev = xy

    lat_mean_route = None
    if route:
        lat_mean_route = round(sum(p[1] for p in route) / len(route), 4)

    d.text((6, 6), "MODEL-LOCAL RAW PLAN (pred_route red / pred_speed_wps green)", fill=(230, 160, 160), font=font)
    d.text((6, 28), f"raw dim1 mean(route)={lat_mean_route}  (RAW sign only; NOT left/right)", fill=(220, 180, 180), font=small)
    d.text((6, 46), "MODEL_LOCAL_RAW | RAW UNIT | NO PHYSICAL SCALE CLAIM | metric_overlay=OFF", fill=(255, 180, 90), font=small)
    d.text((6, 64), " | ".join(C.GLOBAL_LABELS), fill=(255, 200, 0), font=small)
    return np.asarray(img)
