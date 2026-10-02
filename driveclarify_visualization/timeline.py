"""Bottom timeline strip: per-frame identity + timing, with DISTINCT FPS domains.

Renders sim time / wall-clock(monotonic) / frame / observation id / speed / yaw / route
progress / model+adapter+controller timing / current decision. FPS fields are kept in
SEPARATE named slots (display vs sensor vs simulation vs model-inference) so a replay/export
FPS is never presented as the model-inference rate.
"""

from __future__ import annotations

from typing import Any, Optional

from . import contracts as C


def compute_fps_block(frame: dict[str, Any], display_refresh_fps: Optional[float],
                      dropped: int) -> dict[str, Any]:
    """Assemble the five DISTINCT FPS/rate fields. Each has its own source; none aliases
    another. model_inference_rate is derived from the frame's own model start/end monotonic
    span (a per-frame inference duration), NOT from the replay/display rate."""
    ms = frame.get("model_start_monotonic_s")
    me = frame.get("model_end_monotonic_s")
    model_infer_hz = None
    if ms is not None and me is not None and me > ms:
        model_infer_hz = round(1.0 / (me - ms), 3)
    block = {
        "display_refresh_fps": display_refresh_fps,      # rendering rate (this viewer)
        "new_sensor_frame_fps": frame.get("_sensor_fps"),  # from sensor-frame deltas
        "simulation_rate": 20.0,                          # fixed_delta 1/20 (SIM domain)
        "model_inference_rate": model_infer_hz,           # 1/(model_end-model_start), MONOTONIC
        "visualization_dropped_frames": dropped,
    }
    C.assert_fps_domains_distinct(block["display_refresh_fps"],
                                  block["new_sensor_frame_fps"],
                                  block["model_inference_rate"])
    # every required key present (structural guarantee they are not conflated)
    for k in C.REQUIRED_FPS_KEYS:
        assert k in block, k
    return block


def render(frame: dict[str, Any], fps_block: dict[str, Any], decision: str,
           width: int = 1024, height: int = 120):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (width, height), (12, 12, 16))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
        small = ImageFont.truetype("DejaVuSans.ttf", 12)
    except Exception:  # noqa: BLE001
        font = small = ImageFont.load_default()
    ego = frame.get("ego", {}) or {}
    speed = ego.get("speed_world_mps")
    rlen = (frame.get("route_context") or {}).get("route_len_remaining")
    d.text((8, 6), f"sim={frame.get('sim_time_s')}s  mono={frame.get('monotonic_s')}  "
                   f"frame={frame.get('carla_frame')}  obs={frame.get('observation_id')}",
           fill=(200, 220, 230), font=small)
    d.text((8, 26), f"speed={None if speed is None else round(speed,2)} m/s  "
                    f"yaw={frame.get('ego_yaw_deg')}  route_len_remaining={rlen}  "
                    f"decision={decision}", fill=(210, 210, 200), font=small)
    d.text((8, 48), f"display_fps={fps_block['display_refresh_fps']}  "
                    f"sensor_fps={fps_block['new_sensor_frame_fps']}  "
                    f"sim_rate={fps_block['simulation_rate']}  "
                    f"model_infer_hz={fps_block['model_inference_rate']}  "
                    f"dropped={fps_block['visualization_dropped_frames']}",
           fill=(150, 200, 255), font=small)
    d.text((8, 70), "FPS domains are SEPARATE: replay/display rate is NOT the model-inference rate",
           fill=(255, 180, 90), font=small)
    d.text((8, 92), " | ".join(C.GLOBAL_LABELS), fill=(255, 200, 0), font=small)
    return np.asarray(img)
