"""决定性验证：把 receipt 里的 bbox 裁出来，确认它确实落在白色厢式车上。

若 bbox 属于这张 PNG，裁块应为白色车体（高亮度、低饱和度）。
"""
import json
import os

import numpy as np
from PIL import Image

ROOT = "/home/buaa/wrh/DriveClarify"
SUITE = os.path.join(
    ROOT, "artifacts", "driveclarify_method_v1_visual_behavioral_acceptance_suite_v1"
)
CASES = [
    "DCVA1-01-ACT-0815",
    "DCVA1-02-AS-0815",
    "DCVA1-03-ASK-0815",
    "DCVA1-04-WAIT-0815",
    "DCVA1-06-TL-0815",
    "DCVA1-07-HR-ER1-0815",
]


def stats(a):
    """返回裁块的亮度均值与平均饱和度（白色车体应高亮度、低饱和）。"""
    f = a.astype(np.float32) / 255.0
    mx = f.max(axis=2)
    mn = f.min(axis=2)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    return float(mx.mean() * 255), float(sat.mean())


def main():
    os.makedirs("/tmp/figqual/bbox_check", exist_ok=True)
    for c in CASES:
        j = json.load(
            open(os.path.join(SUITE, c, "GROUNDED_LANGUAGE_V1_LIVE_RECEIPT.json"))
        )
        im = Image.open(os.path.join(SUITE, c, "E1R1_GROUNDING_RGB_0.png")).convert(
            "RGB"
        )
        a = np.asarray(im)
        print(f"=== {c}  image={im.size}")
        refs = j["grounding"].get("selected_referents") or []
        for i, r in enumerate(refs):
            x0, y0, x1, y1 = [int(round(v)) for v in r["bbox_xyxy"]]
            crop = a[y0:y1, x0:x1]
            if crop.size == 0:
                print(f"    cand{i}: EMPTY crop {(x0, y0, x1, y1)}")
                continue
            lum, sat = stats(crop)
            frac = dict(r.get("appearance_attributes") or {}).get(
                "requested_color_pixel_fraction"
            )
            print(
                f"    cand{'AB'[i]}: box=({x0},{y0},{x1},{y1}) "
                f"size={x1 - x0}x{y1 - y0} lum={lum:5.1f} sat={sat:.3f} "
                f"receipt_white_frac={frac}"
            )
            Image.fromarray(crop).resize(
                ((x1 - x0) * 3, (y1 - y0) * 3), Image.NEAREST
            ).save(f"/tmp/figqual/bbox_check/{c}_{'AB'[i]}.png")


if __name__ == "__main__":
    main()
