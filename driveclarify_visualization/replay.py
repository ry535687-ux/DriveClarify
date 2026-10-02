"""Offline replay driver: turn an existing CP3B run into per-panel PNG sequences, a combined
2x2+timeline dashboard mp4, and a per-frame decision trace + metadata JSON.

Read-only w.r.t. all source artifacts. Never runs CARLA/model. FPS is a DISPLAY/export rate
only and is labeled as such; it is never presented as the model-inference rate.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from . import contracts as C
from .data_source import OfflineRunSource
from . import dashboard


def _load_decision_map(cp3a_records_path: Optional[str]) -> dict[str, Any]:
    """Map observation_id -> CP3A shadow-decision record, if a records.jsonl is given."""
    dmap: dict[str, Any] = {}
    if not cp3a_records_path or not os.path.exists(cp3a_records_path):
        return dmap
    with open(cp3a_records_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            oid = r.get("observation_id")
            if oid is not None and oid not in dmap:
                dmap[oid] = r
    return dmap


def _sensor_fps(prev_frame, frame):
    """Instantaneous sensor FPS from consecutive sensor frame deltas over sim dt."""
    try:
        dt = frame["sim_time_s"] - prev_frame["sim_time_s"]
        df = frame["carla_frame"] - prev_frame["carla_frame"]
        if dt and df:
            return df / dt
    except Exception:  # noqa: BLE001
        return None
    return None


def replay_run(run_dir: str, out_dir: str, fps: int = 20,
               cp3a_records_path: Optional[str] = None,
               max_frames: Optional[int] = None,
               write_panel_pngs: bool = True,
               reference_summary: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Produce dashboard mp4 + panel PNGs + decision trace + metadata for one run.
    Returns a metadata dict (also written to out_dir/metadata.json)."""
    import numpy as np
    from PIL import Image

    src = OfflineRunSource(run_dir)
    run_id = src.run_id()
    dmap = _load_decision_map(cp3a_records_path)

    os.makedirs(out_dir, exist_ok=True)
    for sub in ("raw_input", "enhanced_input", "world_bev", "model_local_plan"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    mp4_path = os.path.join(out_dir, f"combined_dashboard_{_route_tag(run_id)}.mp4")
    decision_trace = []
    frames = list(src)
    if max_frames:
        frames = frames[:max_frames]

    vw = None
    dropped = 0
    prev = None
    last_sha = None
    display_fps = float(fps)  # this is the EXPORT/display rate, not model-inference rate
    n_written = 0
    try:
        import cv2
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        for i, fr in enumerate(frames):
            fr["_sensor_fps"] = _sensor_fps(prev, fr) if prev else None
            dec = dmap.get(fr.get("observation_id"))
            full, fps_block, decision = dashboard.compose_frame(
                fr, dec_record=dec, last_image_sha=last_sha,
                display_refresh_fps=display_fps, dropped=dropped,
                reference_summary=reference_summary)
            if vw is None:
                h, w = full.shape[:2]
                vw = cv2.VideoWriter(mp4_path, fourcc, display_fps, (w, h))
                if not vw.isOpened():
                    raise RuntimeError("cv2 VideoWriter open failed")
            vw.write(cv2.cvtColor(full, cv2.COLOR_RGB2BGR))
            n_written += 1
            decision_trace.append({
                "record_seq": fr.get("record_seq"),
                "observation_id": fr.get("observation_id"),
                "sim_time_s": fr.get("sim_time_s"),
                "decision": decision,
                "fps_block": fps_block,
                "image_sha256": fr.get("image_sha256"),
                "forward_invocation_counter": fr.get("forward_invocation_counter"),
            })
            if write_panel_pngs and (i % 20 == 0):  # sample panels to keep disk bounded
                from . import raw_input_view, enhanced_view, world_bev, model_local_plan_view
                Image.fromarray(raw_input_view.render(fr, last_image_sha=last_sha)).save(
                    os.path.join(out_dir, "raw_input", f"{i:05d}.png"))
                enh, _ = enhanced_view.render(fr)
                Image.fromarray(enh).save(os.path.join(out_dir, "enhanced_input", f"{i:05d}.png"))
                Image.fromarray(world_bev.render(fr)).save(
                    os.path.join(out_dir, "world_bev", f"{i:05d}.png"))
                Image.fromarray(model_local_plan_view.render(fr)).save(
                    os.path.join(out_dir, "model_local_plan", f"{i:05d}.png"))
            last_sha = fr.get("image_sha256")
            prev = fr
    finally:
        if vw is not None:
            vw.release()

    with open(os.path.join(out_dir, "decision_trace.json"), "w") as f:
        json.dump(decision_trace, f, indent=2)

    meta = {
        "run_id": run_id,
        "run_dir": run_dir,
        "source_world_state_sha256": _sha(os.path.join(run_dir, "world_state.jsonl")),
        "frames_rendered": n_written,
        "export_fps_display_only": display_fps,
        "fps_semantics": "display/export rate ONLY; NOT the model-inference rate",
        "dashboard_mp4": mp4_path,
        "decisions_seen": sorted({d["decision"] for d in decision_trace}),
        "cp3a_records_used": bool(dmap),
        "labels": {"raw": C.RAW_LABELS, "display": C.DISPLAY_LABELS,
                   "model_local": C.MODEL_LOCAL_LABELS, "global": C.GLOBAL_LABELS},
    }
    with open(os.path.join(out_dir, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def _route_tag(run_id: str) -> str:
    for rid in ("25378", "26950", "26990"):
        if rid in run_id:
            return rid
    return run_id


def _sha(path: str) -> Optional[str]:
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for c in iter(lambda: f.read(1 << 20), b""):
                h.update(c)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return None
