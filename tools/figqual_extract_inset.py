"""从 dashboard 截图中裁出内嵌相机图，用于评估画质是否满足出版要求。"""
import os
import sys

from PIL import Image

# 由 figqual_probe_inset.py 探测得到的稳定区域（1600x900 dashboard）
INSET_BOX = (18, 161, 806, 575)
BORDER = 3


def extract(path, out_path):
    im = Image.open(path).convert("RGB")
    x0, y0, x1, y1 = INSET_BOX
    crop = im.crop((x0 + BORDER, y0 + BORDER, x1 - BORDER, y1 - BORDER))
    crop.save(out_path)
    print(f"{out_path}  size={crop.size}")
    return crop


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    extract(src, dst)
