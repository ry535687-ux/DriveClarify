#!/usr/bin/env python3
"""Read-only extraction of Word body text (paragraphs + tables) with indices."""
from __future__ import annotations

import sys
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def text_of(el) -> str:
    return "".join(t.text or "" for t in el.iter(W + "t"))


def main(path: str) -> int:
    z = zipfile.ZipFile(path)
    doc = ET.fromstring(z.read("word/document.xml"))
    body = doc.find(W + "body")
    if body is None:
        return 1
    pidx = 0
    for child in body:
        tag = child.tag
        if tag == W + "p":
            txt = text_of(child).strip()
            style = ""
            ps = child.find(W + "pPr")
            if ps is not None:
                st = ps.find(W + "pStyle")
                if st is not None:
                    style = st.get(W + "val") or ""
            print(f"[P{pidx:04d}]{('<' + style + '>') if style else ''} {txt}")
            pidx += 1
        elif tag == W + "tbl":
            print(f"[TBL@P{pidx:04d}] === TABLE START ===")
            for r in child.findall(W + "tr"):
                cells = [text_of(c).strip().replace("\n", " ") for c in r.findall(W + "tc")]
                print("  | " + " | ".join(cells))
            print(f"[TBL@P{pidx:04d}] === TABLE END ===")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
