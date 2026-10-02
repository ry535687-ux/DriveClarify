"""VLA research-debug rendering from one already-materialized SimLingo tick.

This module is deliberately CPU-only.  It receives copies made *after* the baseline
forward and PID have completed, then produces labelled research views:

* ``raw_camera``: CARLA rgb_0 after BGRA/BGR -> RGB only.
* ``input_rgb``: inverse-normalized visualization derived from the exact
  ``DrivingInput.camera_images`` tensor used by the one baseline forward.
* ``ego_view``: display-only brightness/contrast treatment of ``raw_camera``.
* ``bev_debug``: model-local raw pred_route / pred_speed_wps plot.  It is not a
  camera projection and makes no metric/world/left-right claim.
* ``overlay``: the combined research dashboard.

Nothing here imports CARLA or torch, calls a model/controller/planner, or writes into
any input object.
"""

from __future__ import annotations

import math
import textwrap
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

IMAGENET_MEAN = np.asarray((0.485, 0.456, 0.406), dtype=np.float32)
IMAGENET_STD = np.asarray((0.229, 0.224, 0.225), dtype=np.float32)

_BG = (12, 15, 20)
_PANEL = (22, 27, 35)
_TEXT = (226, 232, 240)
_MUTED = (151, 164, 181)
_AMBER = (255, 190, 72)
_GREEN = (84, 220, 146)
_CYAN = (76, 190, 235)
_RED = (255, 105, 112)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 - fall through to portable default
            pass
    return ImageFont.load_default()


def fit_image(image: Image.Image, size: tuple[int, int], background=_PANEL) -> Image.Image:
    """Letterbox an image without changing its aspect ratio."""
    canvas = Image.new("RGB", size, background)
    src = image.convert("RGB")
    src.thumbnail(size, Image.Resampling.LANCZOS)
    x = (size[0] - src.width) // 2
    y = (size[1] - src.height) // 2
    canvas.paste(src, (x, y))
    return canvas


def model_tensor_to_rgb_patches(values: np.ndarray) -> tuple[list[np.ndarray], dict[str, Any]]:
    """Inverse ImageNet normalization for the exact model tensor values.

    ``values`` is a lossless float32 representation of the source bfloat16 values.
    The returned uint8 arrays are human-viewable visualizations.  The PNG is not
    itself the tensor; exact numeric binding lives in the companion tensor artifact
    and SHA-256 metadata.
    """
    arr = np.asarray(values, dtype=np.float32)
    if arr.ndim < 3 or tuple(arr.shape[-3:-2]) != (3,):
        raise ValueError(f"expected (...,3,H,W), got {arr.shape}")
    flat = arr.reshape((-1,) + tuple(arr.shape[-3:]))
    patches: list[np.ndarray] = []
    for patch in flat:
        hwc = np.transpose(patch, (1, 2, 0))
        rgb = (hwc * IMAGENET_STD.reshape(1, 1, 3)) + IMAGENET_MEAN.reshape(1, 1, 3)
        rgb = np.rint(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
        patches.append(rgb)
    return patches, {
        "normalization": "ImageNet mean/std inverse for display only",
        "mean": IMAGENET_MEAN.tolist(),
        "std": IMAGENET_STD.tolist(),
        "source_shape": list(arr.shape),
        "patch_count": len(patches),
        "png_semantics": (
            "human-viewable inverse-normalized rendering derived from the exact "
            "model tensor; exact values are in model_input_tensor/*.npy"
        ),
    }


def patch_mosaic(patches: list[np.ndarray]) -> Image.Image:
    if not patches:
        return Image.new("RGB", (448, 448), _PANEL)
    count = len(patches)
    cols = min(count, 2) if count <= 4 else int(math.ceil(math.sqrt(count)))
    rows = int(math.ceil(count / cols))
    ph, pw = patches[0].shape[:2]
    canvas = Image.new("RGB", (cols * pw, rows * ph), _PANEL)
    for idx, patch in enumerate(patches):
        canvas.paste(Image.fromarray(patch, "RGB"), ((idx % cols) * pw, (idx // cols) * ph))
    return canvas


def enhance_for_researcher(raw_rgb: np.ndarray) -> tuple[Image.Image, dict[str, Any]]:
    """Display-only transform; never used for inference or control."""
    raw = np.asarray(raw_rgb, dtype=np.uint8)
    src = Image.fromarray(raw, "RGB")
    luma = (
        raw[:, :, 0].astype(np.float32) * 0.2126
        + raw[:, :, 1].astype(np.float32) * 0.7152
        + raw[:, :, 2].astype(np.float32) * 0.0722
    )
    mean_luma = float(luma.mean())
    # Conservative adaptive exposure for the *display copy*: brighten dark/night
    # scenes but avoid blowing out bright fog/rain scenes.
    if mean_luma < 60.0:
        brightness = 1.35
    elif mean_luma < 105.0:
        brightness = 1.16
    elif mean_luma > 175.0:
        brightness = 0.92
    else:
        brightness = 1.0
    contrast = 1.08
    color = 1.15
    out = ImageEnhance.Brightness(src).enhance(brightness)
    out = ImageEnhance.Contrast(out).enhance(contrast)
    out = ImageEnhance.Color(out).enhance(color)
    return out, {
        "display_only": True,
        "used_by_model": False,
        "adaptive_from_raw_mean_luma": round(mean_luma, 3),
        "brightness": brightness,
        "contrast": contrast,
        "color": color,
        "sensor_parameters_changed": False,
    }


def _tensor_points(value: Any) -> list[list[float]]:
    try:
        arr = np.asarray(value, dtype=np.float32)
        if arr.ndim == 3:
            arr = arr[0]
        if arr.ndim != 2 or arr.shape[1] < 2:
            return []
        finite = arr[:, :2][np.isfinite(arr[:, :2]).all(axis=1)]
        return finite.astype(float).tolist()
    except Exception:  # noqa: BLE001
        return []


def render_model_local_bev(snapshot: dict[str, Any], size=(580, 270)) -> Image.Image:
    """Debug-only model-local plot; no camera/world projection or metric claim."""
    img = Image.new("RGB", size, (15, 22, 24))
    draw = ImageDraw.Draw(img)
    title = _font(17, bold=True)
    small = _font(13)
    route = _tensor_points(snapshot.get("pred_route"))
    speed_wps = _tensor_points(snapshot.get("pred_speed_wps"))
    all_points = route + speed_wps + [[0.0, 0.0]]

    pad_x, top, bottom = 38, 56, 30
    plot_w = size[0] - pad_x * 2
    plot_h = size[1] - top - bottom
    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    xmin, xmax = min(xs), max(xs)
    yr = max(abs(min(ys)), abs(max(ys)), 1.0)
    xr = max(xmax - xmin, 1.0)

    def xy(point: list[float]) -> tuple[float, float]:
        x = pad_x + ((point[0] - xmin) / xr) * plot_w
        y = top + ((yr - point[1]) / (2.0 * yr)) * plot_h
        return x, y

    draw.line((pad_x, top + plot_h / 2, size[0] - pad_x, top + plot_h / 2), fill=(65, 80, 83))
    draw.line((xy([0.0, -yr])[0], top, xy([0.0, yr])[0], top + plot_h), fill=(65, 80, 83))
    ego = xy([0.0, 0.0])
    draw.polygon(
        ((ego[0] + 8, ego[1]), (ego[0] - 6, ego[1] - 5), (ego[0] - 6, ego[1] + 5)),
        fill=(250, 250, 250),
    )

    def draw_path(points: list[list[float]], color: tuple[int, int, int], radius: int) -> None:
        coords = [xy(p) for p in points]
        if len(coords) > 1:
            draw.line(coords, fill=color, width=2)
        for px, py in coords:
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=color)

    draw_path(route, _RED, 3)
    draw_path(speed_wps, _CYAN, 2)
    draw.text((12, 8), "MODEL OUTPUT — MODEL-LOCAL BEV (DEBUG ONLY)", fill=_TEXT, font=title)
    draw.text(
        (12, 31),
        f"pred_route={len(route)} red  pred_speed_wps={len(speed_wps)} cyan  ego=white",
        fill=_MUTED,
        font=small,
    )
    draw.text(
        (12, size[1] - 20),
        "RAW UNIT · NO METRIC/WORLD/CAMERA PROJECTION CLAIM · dim1 is not labelled left/right",
        fill=_AMBER,
        font=small,
    )
    return img


def _panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str) -> None:
    draw.rounded_rectangle(box, radius=8, fill=_PANEL, outline=(48, 57, 70), width=1)
    draw.text((box[0] + 12, box[1] + 8), title, fill=_TEXT, font=_font(17, bold=True))


def _multiline(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    width: int,
    *,
    fill=_TEXT,
    font_size=15,
    max_lines=8,
) -> int:
    font = _font(font_size)
    chars = max(10, int(width / (font_size * 0.62)))
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        lines.extend(textwrap.wrap(paragraph, width=chars) or [""])
    y = xy[1]
    for line in lines[:max_lines]:
        draw.text((xy[0], y), line, fill=fill, font=font)
        y += font_size + 4
    if len(lines) > max_lines:
        draw.text((xy[0], y), "…", fill=_MUTED, font=font)
        y += font_size + 4
    return y


def render_dashboard(
    snapshot: dict[str, Any],
    raw_camera: Image.Image,
    model_input: Image.Image,
    ego_view: Image.Image,
    bev: Image.Image,
) -> Image.Image:
    """Compose the 1600×900 research dashboard."""
    canvas = Image.new("RGB", (1600, 900), _BG)
    draw = ImageDraw.Draw(canvas)
    draw.text((18, 12), "DriveClarify / SimLingo — VLA RESEARCH DEBUG VIEW", fill=_TEXT, font=_font(24, True))
    draw.text(
        (995, 17),
        "SIMULATION ONLY · NO FORMAL SAFETY GUARANTEE",
        fill=_AMBER,
        font=_font(16, True),
    )

    ego_box = (10, 54, 1000, 620)
    input_box = (1010, 54, 1590, 335)
    bev_box = (1010, 344, 1590, 620)
    language_box = (10, 630, 700, 890)
    state_box = (710, 630, 1125, 890)
    decision_box = (1135, 630, 1590, 890)
    for box, title in (
        (ego_box, "EGO VIEW — DISPLAY ONLY / NOT MODEL INPUT"),
        (input_box, "ACTUAL MODEL INPUT RGB — FROM SAME camera_images TENSOR"),
        (bev_box, "PREDICTIONS"),
        (language_box, "LANGUAGE / NAVIGATION CONDITION"),
        (state_box, "FRAME ALIGNMENT / VEHICLE EXECUTION"),
        (decision_box, "DriveClarify DECISION STATUS (PLACEHOLDER)"),
    ):
        _panel(draw, box, title)

    canvas.paste(fit_image(ego_view, (966, 516)), (22, 92))
    canvas.paste(fit_image(model_input, (556, 238)), (1022, 88))
    canvas.paste(fit_image(bev, (556, 238)), (1022, 374))

    # A tiny raw-camera source marker makes the display/model distinction visible.
    raw_thumb = fit_image(raw_camera, (188, 106))
    canvas.paste(raw_thumb, (800, 490))
    draw.rectangle((796, 466, 994, 610), outline=(75, 89, 105), width=1)
    draw.text((804, 470), "raw_camera source", fill=_MUTED, font=_font(13))

    x, y = 24, 668
    _multiline(
        draw,
        f"Instruction: {snapshot.get('instruction_text') or 'UNKNOWN'}",
        (x, y),
        650,
        fill=_TEXT,
        font_size=16,
        max_lines=5,
    )
    y = 777
    _multiline(
        draw,
        f"HLC/model command history: {snapshot.get('command_history') or 'UNKNOWN'}\n"
        f"previous command: {snapshot.get('previous_command')}\n"
        f"model prompt SHA: {(snapshot.get('prompt_token_sha256') or 'UNKNOWN')[:16]}",
        (x, y),
        650,
        fill=_MUTED,
        font_size=14,
        max_lines=5,
    )

    control = snapshot.get("control") or {}
    state_lines = [
        f"route={snapshot.get('route_id') or 'UNKNOWN'}  town={snapshot.get('town') or 'UNKNOWN'}",
        f"frame={snapshot.get('frame_id')}  sensor_frame={snapshot.get('sensor_frame')}",
        f"timestamp={snapshot.get('timestamp_s')} s [{snapshot.get('timestamp_domain')}]",
        f"obs={snapshot.get('observation_id')}",
        f"tensor={snapshot.get('model_input_shape')} {snapshot.get('model_input_source_dtype')}",
        f"tensor SHA={str(snapshot.get('model_input_value_sha256') or 'UNKNOWN')[:16]}",
        f"speed={snapshot.get('speed_mps')} m/s",
        f"steer={control.get('steer')}  throttle={control.get('throttle')}",
        f"brake={control.get('brake')}  gear={control.get('gear')}",
        f"predicted waypoints={snapshot.get('pred_route_count')}",
        f"same forward={snapshot.get('generated_from_same_forward')}",
        f"recording={snapshot.get('recording_mode')}",
    ]
    _multiline(draw, "\n".join(state_lines), (724, 668), 385, fill=_TEXT, font_size=14, max_lines=13)

    decision_lines = [
        "Decision Layer: N/A",
        "Query State: NONE",
        "Evidence Grade: logging_only",
        "Notes: debug visualization only",
        "",
        "ACT / ASK / WAIT / FALLBACK:",
        "reserved; not connected to control",
        "",
        "Model-local plan is diagnostic-only.",
        "UNKNOWN is never rendered as safe/zero.",
    ]
    _multiline(draw, "\n".join(decision_lines), (1149, 670), 425, fill=_TEXT, font_size=15, max_lines=13)
    draw.text(
        (1149, 855),
        f"display sample={snapshot.get('sample_seq')}  dropped={snapshot.get('dropped_before_enqueue', 0)}",
        fill=_MUTED,
        font=_font(13),
    )
    return canvas
