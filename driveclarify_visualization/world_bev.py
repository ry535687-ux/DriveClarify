"""Panel C-1: World BEV. Only VERIFIED CARLA world-state data (ego/actors/lights/map).

Top-down plot in CARLA world XY, centered on ego, of quantities CP1 already VERIFIED as
readable: ego pose, in-ROI actors, traffic-light positions, current lane waypoint. It does
NOT draw the model plan here (that lives in the separate model-local panel), does NOT claim
left/right, does NOT compute TTC/clearance. It is a spatial view of measured world state.
"""

from __future__ import annotations

import math
from typing import Any

from . import contracts as C


def render(frame: dict[str, Any], size_px: int = 512, span_m: float = 60.0):
    """Return an RGB ndarray: world BEV centered on ego, world-state only."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (size_px, size_px + 84), (18, 18, 22))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 15)
        small = ImageFont.truetype("DejaVuSans.ttf", 12)
    except Exception:  # noqa: BLE001
        font = small = ImageFont.load_default()

    top = 84
    cx, cy = size_px / 2, top + size_px / 2
    scale = size_px / span_m  # px per metre (display geometry only, world data is real)

    ego = frame.get("ego", {}) or {}
    eloc = ego.get("location_xyz")
    eyaw = frame.get("ego_yaw_deg")

    def w2p(x, y):
        # world XY -> panel px, ego at center; +x world to the right, +y world downward.
        if eloc is None:
            return cx, cy
        return cx + (x - eloc[0]) * scale, cy + (y - eloc[1]) * scale

    # grid
    for gm in range(-int(span_m // 2), int(span_m // 2) + 1, 10):
        gx = cx + gm * scale
        gy = cy + gm * scale
        d.line([(gx, top), (gx, top + size_px)], fill=(38, 38, 44))
        d.line([(0, gy), (size_px, gy)], fill=(38, 38, 44))

    # traffic lights (positions are real world coords; state string shown, never GREEN-filled)
    tl = frame.get("traffic_lights", {}) or {}
    C.assert_traffic_light_empty_not_green(tl.get("status", ""), tl.get("lights", []) or [])
    for lt in (tl.get("lights") or []):
        loc = lt.get("location_xyz")
        if not loc:
            continue
        px, py = w2p(loc[0], loc[1])
        state = str(lt.get("state"))
        col = {"Red": (230, 60, 60), "Green": (60, 210, 90), "Yellow": (230, 210, 60)}.get(state, (150, 150, 150))
        d.ellipse([px - 4, py - 4, px + 4, py + 4], fill=col)

    # actors
    ac = frame.get("actors", {}) or {}
    for a in (ac.get("actors") or []):
        loc = a.get("location_xyz")
        if not loc:
            continue
        px, py = w2p(loc[0], loc[1])
        d.rectangle([px - 3, py - 3, px + 3, py + 3], outline=(120, 180, 255))

    # current lane waypoint
    mw = frame.get("map_waypoint", {}) or {}
    wl = mw.get("waypoint_location_xyz")
    if wl:
        px, py = w2p(wl[0], wl[1])
        d.ellipse([px - 3, py - 3, px + 3, py + 3], outline=(200, 200, 120))

    # ego (triangle pointing along yaw)
    if eloc is not None and eyaw is not None:
        yr = math.radians(eyaw)
        L = 10
        tip = (cx + L * math.cos(yr), cy + L * math.sin(yr))
        left = (cx + 6 * math.cos(yr + 2.5), cy + 6 * math.sin(yr + 2.5))
        right = (cx + 6 * math.cos(yr - 2.5), cy + 6 * math.sin(yr - 2.5))
        d.polygon([tip, left, right], fill=(255, 230, 90))

    d.text((6, 6), "WORLD BEV (measured CARLA world-state; VERIFIED-readable only)", fill=(140, 220, 255), font=font)
    d.text((6, 28), f"ego=({eloc[0]:.1f},{eloc[1]:.1f}) yaw={eyaw:.1f}deg  span={span_m:.0f}m  road={mw.get('road_id')} lane={mw.get('lane_id')}"
            if eloc else "ego UNKNOWN", fill=(180, 200, 220), font=small)
    d.text((6, 46), f"lights={len(tl.get('lights') or [])} actors={ (ac.get('counts') or {}).get('logged') }  NO TTC / NO left-right claim", fill=(170, 190, 210), font=small)
    d.text((6, 64), " | ".join(C.GLOBAL_LABELS), fill=(255, 200, 0), font=small)
    return __import__("numpy").asarray(img)
