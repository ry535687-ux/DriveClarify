"""检查图中用到的字体是否真的含所需字形（缺字形时 matplotlib 会静默替换，不报错）。

重点：U+2192 右箭头。决策 chip 用 serif，state 行用等宽。
"""
from fontTools.ttLib import TTFont
from matplotlib import font_manager

WANT = {
    0x2192: "RIGHTWARDS ARROW",
    0x2014: "EM DASH",
    0x201C: "LEFT DOUBLE QUOTATION MARK",
    0x201D: "RIGHT DOUBLE QUOTATION MARK",
}

FAMILIES = [
    "Nimbus Roman",
    "Liberation Serif",
    "DejaVu Serif",
    "DejaVu Sans Mono",
]


def cmap_of(path):
    f = TTFont(path, fontNumber=0, lazy=True)
    chars = set()
    for table in f["cmap"].tables:
        chars.update(table.cmap.keys())
    f.close()
    return chars


def main():
    for fam in FAMILIES:
        try:
            path = font_manager.findfont(
                font_manager.FontProperties(family=fam), fallback_to_default=False
            )
        except Exception as exc:  # noqa: BLE001
            print(f"=== {fam}: 未找到 ({exc})")
            continue
        try:
            chars = cmap_of(path)
        except Exception as exc:  # noqa: BLE001
            print(f"=== {fam}: 无法解析 cmap ({exc})")
            continue
        print(f"=== {fam}")
        print(f"    {path}")
        for cp, name in WANT.items():
            mark = "有" if cp in chars else "缺"
            print(f"    U+{cp:04X} {name:34s} {mark}")


if __name__ == "__main__":
    main()
