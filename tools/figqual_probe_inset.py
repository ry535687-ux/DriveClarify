"""探测 dashboard 截图中内嵌相机图的精确区域，并比较各阶段是否为不同帧。"""
import glob
import hashlib
import os
import sys

import numpy as np
from PIL import Image


def find_inset_bbox(path):
    """在 1600x900 dashboard 左上区域寻找亮度显著的连续矩形（相机图）。"""
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(np.float32)
    # 只在左上区域搜索（相机图固定在 "WHAT DOES THE CAR SEE" 面板内）
    region = a[100:600, 0:820]
    lum = region.mean(axis=2)
    bright = lum > 60
    rows = np.where(bright.sum(axis=1) > 300)[0]
    cols = np.where(bright.sum(axis=0) > 200)[0]
    if len(rows) == 0 or len(cols) == 0:
        return None
    return (int(cols[0]), int(rows[0]) + 100, int(cols[-1]) + 1, int(rows[-1]) + 101)


def main(case_dir):
    files = sorted(glob.glob(os.path.join(case_dir, "visual_timeline", "*.png")))
    for f in files:
        bb = find_inset_bbox(f)
        im = Image.open(f).convert("RGB")
        if bb:
            crop = im.crop(bb)
            h = hashlib.sha256(crop.tobytes()).hexdigest()[:12]
            print(f"{os.path.basename(f):58s} bbox={bb} size={crop.size} sha={h}")
        else:
            print(f"{os.path.basename(f):58s} bbox=NONE")


if __name__ == "__main__":
    main(sys.argv[1])
