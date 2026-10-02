"""CP3B offline replay viewer/exporter (READ-ONLY, no CARLA/GPU/model).

Turns the SAVED per-frame model-input RGB (rgb_0) into a real-time colour video and an
interactive player. This is the model's ACTUAL input — the same 1024x512 frames the
SimLingo forward consumed — NOT the washed-out high-altitude spectator window.

Per docs/CARLA_LIVE_VISUALIZATION_REQUIREMENTS.md: the offline replay viewer is the
FIRST visualization deliverable (before any live dashboard) because it is easy to validate
and reproduce. It never runs CARLA, never runs the model, never touches baseline/control.

Every frame gets a small research-debug caption (frame/obs id, sim time, speed, ego yaw,
saved-RGB SHA-256 prefix) drawn on a black margin ABOVE the image, so the RGB pixels the
model saw are never overpainted. Labels required by the viz contract are shown.

Usage:
  # export a 20 fps mp4 (real-time; 204 frames -> ~10 s):
  python -m driveclarify_probe.cp3b_replay_viewer export <run_dir> [out.mp4] [--fps 20] [--raw]
  # live playback in a window (needs DISPLAY):
  python -m driveclarify_probe.cp3b_replay_viewer play <run_dir> [--fps 20] [--loop]
"""

from __future__ import annotations

import json
import os
import sys

_LABELS = ["RESEARCH DEBUG VIEW", "NO FORMAL SAFETY GUARANTEE", "SIMULATION ONLY"]


def _load_index(run_dir):
    """Return frames sorted by record_seq: list of (seq, image_abspath, meta)."""
    ws = os.path.join(run_dir, "world_state.jsonl")
    img_dir = os.path.join(run_dir, "images")
    frames = []
    with open(ws, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            cp3b = r.get("cp3b", {}) or {}
            rgb = cp3b.get("rgb", {}) or {}
            ip = rgb.get("image_path")
            if not ip:
                continue
            path = os.path.join(img_dir, ip)
            if not os.path.exists(path):
                continue
            ego = r.get("ego", {}) or {}
            rot = ego.get("rotation_rpy_deg") or [None, None, None]
            meta = {
                "seq": cp3b.get("record_seq"),
                "obs": r.get("observation_id"),
                "sim_s": r.get("snapshot_elapsed_seconds"),
                "speed": ego.get("speed_world_mps"),
                "yaw": rot[2] if len(rot) == 3 else None,
                "sha": (rgb.get("image_sha256") or "")[:12],
                "landmarks": (cp3b.get("landmarks", {}) or {}).get("count"),
            }
            frames.append((cp3b.get("record_seq") or 0, path, meta))
    frames.sort(key=lambda x: x[0])
    return frames


def _enhance_display(rgb_np):
    """DISPLAY-ONLY saturation/contrast boost for human inspection.

    This does NOT represent the model input — the model consumed the low-saturation frame.
    Used only when --enhance is passed, and the frame is captioned DISPLAY-ENHANCED so it is
    never mistaken for the real input. Applied in HSV; stored PNGs are never modified.
    """
    try:
        import cv2
        import numpy as np
        hsv = cv2.cvtColor(rgb_np, cv2.COLOR_RGB2HSV).astype("float32")
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 3.0, 0, 255)          # boost saturation x3
        out = cv2.cvtColor(hsv.astype("uint8"), cv2.COLOR_HSV2RGB)
        # mild contrast stretch
        out = np.clip((out.astype("float32") - 128) * 1.15 + 128, 0, 255).astype("uint8")
        return out
    except Exception:  # noqa: BLE001
        return rgb_np


def _annotate(rgb_np, meta, enhanced=False):
    """Draw a caption on a black margin ABOVE the image (model pixels untouched)."""
    import numpy as np
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001
        return rgb_np
    h, w = rgb_np.shape[:2]
    margin = 96
    canvas = np.zeros((h + margin, w, 3), dtype=np.uint8)
    canvas[margin:, :, :] = rgb_np
    img = Image.fromarray(canvas)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 18)
        small = ImageFont.truetype("DejaVuSans.ttf", 14)
    except Exception:  # noqa: BLE001
        font = small = ImageFont.load_default()

    def fmt(v, nd=2):
        return "n/a" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))

    line1 = (f"seq={fmt(meta['seq'])}  sim={fmt(meta['sim_s'])}s  "
             f"speed={fmt(meta['speed'])} m/s  yaw={fmt(meta['yaw'],1)} deg  "
             f"landmarks={fmt(meta['landmarks'])}")
    tag = "DISPLAY-ENHANCED (NOT model input; sat x3)" if enhanced else "MODEL INPUT rgb_0 (as consumed)"
    line2 = f"{tag} 1024x512  obs={fmt(meta['obs'])}  sha={meta['sha']}"
    d.text((8, 6), line1, fill=(0, 255, 120), font=font)
    d.text((8, 30), line2, fill=((255, 140, 0) if enhanced else (180, 220, 255)), font=small)
    d.text((8, 52), " | ".join(_LABELS), fill=(255, 200, 0), font=small)
    return np.asarray(img)


def export(run_dir, out_path=None, fps=20, raw=False, enhance=False):
    """Encode frames to mp4. Prefers cv2.VideoWriter (reliable, no plugin guesswork);
    falls back to piping PNGs through the system ffmpeg. Read-only w.r.t. the run data.
    enhance=True applies a DISPLAY-ONLY saturation boost (captioned; not the model input)."""
    import numpy as np
    from PIL import Image
    frames = _load_index(run_dir)
    if not frames:
        print("no frames found", file=sys.stderr)
        return 2
    if out_path is None:
        out_path = os.path.join(run_dir, "model_input_replay.mp4")

    def _prep(path, meta):
        a = np.asarray(Image.open(path).convert("RGB"))
        if enhance:
            a = _enhance_display(a)
        if not raw:
            a = _annotate(a, meta, enhanced=enhance)
        return a

    first = _prep(frames[0][1], frames[0][2])
    h, w = first.shape[:2]

    backend = None
    try:
        import cv2
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        vw = cv2.VideoWriter(out_path, fourcc, float(fps), (w, h))
        if not vw.isOpened():
            raise RuntimeError("cv2 VideoWriter failed to open")
        n = 0
        for _seq, path, meta in frames:
            arr = _prep(path, meta)
            if arr.shape[:2] != (h, w):
                arr = np.asarray(Image.fromarray(arr).resize((w, h)))
            vw.write(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
            n += 1
        vw.release()
        backend = "cv2.VideoWriter(mp4v)"
    except Exception as e:  # noqa: BLE001 - fall back to system ffmpeg on a temp PNG seq
        import subprocess, tempfile, shutil
        tmp = tempfile.mkdtemp(prefix="cp3b_replay_")
        try:
            n = 0
            for i, (_seq, path, meta) in enumerate(frames):
                arr = _prep(path, meta)
                Image.fromarray(arr).save(os.path.join(tmp, f"f_{i:05d}.png"))
                n += 1
            cmd = ["ffmpeg", "-y", "-framerate", str(fps), "-i",
                   os.path.join(tmp, "f_%05d.png"), "-pix_fmt", "yuv420p",
                   "-c:v", "libx264", out_path]
            subprocess.run(cmd, check=True, capture_output=True)
            backend = f"ffmpeg(libx264) [cv2 fallback: {type(e).__name__}]"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    dur = n / float(fps)
    print(f"[export] wrote {out_path}  frames={n}  fps={fps}  duration={dur:.2f}s  "
          f"size={w}x{h}  annotated={not raw}  backend={backend}")
    return 0


def play(run_dir, fps=20, loop=False, enhance=False):
    import time
    try:
        import cv2
    except Exception as e:  # noqa: BLE001
        print(f"cv2 unavailable ({e}); use 'export' to make an mp4 instead", file=sys.stderr)
        return 2
    import numpy as np
    from PIL import Image
    frames = _load_index(run_dir)
    if not frames:
        print("no frames found", file=sys.stderr)
        return 2
    win = "CP3B model input rgb_0 (RESEARCH DEBUG / SIM ONLY)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    delay = 1.0 / float(fps)
    while True:
        for _seq, path, meta in frames:
            arr = np.asarray(Image.open(path).convert("RGB"))
            if enhance:
                arr = _enhance_display(arr)
            arr = _annotate(arr, meta, enhanced=enhance)
            cv2.imshow(win, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
            if cv2.waitKey(max(1, int(delay * 1000))) & 0xFF in (27, ord("q")):
                cv2.destroyAllWindows()
                return 0
        if not loop:
            break
    cv2.waitKey(1500)
    cv2.destroyAllWindows()
    return 0


def main(argv):
    if len(argv) < 3 or argv[1] not in ("export", "play"):
        print(__doc__)
        return 1
    mode, run_dir = argv[1], argv[2]
    fps = 20
    raw = "--raw" in argv
    loop = "--loop" in argv
    enhance = "--enhance" in argv
    if "--fps" in argv:
        fps = int(argv[argv.index("--fps") + 1])
    rest = [a for a in argv[3:] if not a.startswith("--") and a != str(fps)]
    out = rest[0] if (mode == "export" and rest) else None
    if mode == "export":
        return export(run_dir, out, fps=fps, raw=raw, enhance=enhance)
    return play(run_dir, fps=fps, loop=loop, enhance=enhance)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
