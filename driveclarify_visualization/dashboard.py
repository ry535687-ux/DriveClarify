"""Compose the 2x2 dashboard + bottom timeline for one frame.

Layout:
  [A raw camera rgb_0] [B display-enhanced human view]
  [C world BEV      ] [D decision & diagnostics    ]
  [ timeline strip (full width)                     ]

compose_frame returns (full_rgb_ndarray, fps_block, decision_str). Pure rendering from
existing data; no CARLA, no model, no control.
"""

from __future__ import annotations

from typing import Any, Optional

from . import raw_input_view, enhanced_view, world_bev, model_local_plan_view
from . import decision_panel, timeline


def _fit(img_np, w, h):
    import numpy as np
    from PIL import Image
    im = Image.fromarray(img_np).resize((w, h))
    return np.asarray(im)


def compose_frame(frame: dict[str, Any], dec_record: Optional[dict[str, Any]] = None,
                  last_image_sha: Optional[str] = None,
                  display_refresh_fps: Optional[float] = None,
                  dropped: int = 0,
                  cell=(512, 320),
                  reference_summary: Optional[dict[str, Any]] = None):
    """Return (full_rgb, fps_block, decision_str)."""
    import numpy as np

    cw, ch = cell
    a = _fit(raw_input_view.render(frame, last_image_sha=last_image_sha), cw, ch)
    enh, _ = enhanced_view.render(frame)
    b = _fit(enh, cw, ch)
    c = _fit(world_bev.render(frame), cw, ch)
    # Panel D: decision + consequence diagnostics, with an honest real-CP3A reference footer.
    # WORLD (panel C) and MODEL-LOCAL raw plan are kept as strictly separate views.
    d_dec = decision_panel.render(frame, dec_record=dec_record, reference_summary=reference_summary)
    d = _fit(d_dec, cw, ch)

    top = np.concatenate([a, b], axis=1)
    mid = np.concatenate([c, d], axis=1)
    grid = np.concatenate([top, mid], axis=0)

    # decision string for timeline
    from .decision_panel import summarize_decision
    decision = summarize_decision(dec_record)["decision"]

    fps_block = timeline.compute_fps_block(frame, display_refresh_fps, dropped)
    tl = timeline.render(frame, fps_block, decision, width=grid.shape[1], height=120)
    full = np.concatenate([grid, tl], axis=0)
    return full, fps_block, decision
