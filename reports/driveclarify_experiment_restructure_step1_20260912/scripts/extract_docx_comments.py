#!/usr/bin/env python3
"""Read-only extraction of Word comments + their anchored paragraphs.

Never writes to the source .docx. Emits JSON to stdout.
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def text_of(el) -> str:
    return "".join(t.text or "" for t in el.iter(W + "t"))


def main(path: str) -> int:
    z = zipfile.ZipFile(path)
    names = set(z.namelist())

    comments = {}
    if "word/comments.xml" in names:
        root = ET.fromstring(z.read("word/comments.xml"))
        for c in root.iter(W + "comment"):
            cid = c.get(W + "id")
            comments[cid] = {
                "id": cid,
                "author": c.get(W + "author"),
                "date": c.get(W + "date"),
                "initials": c.get(W + "initials"),
                "text": text_of(c).strip(),
            }

    # Map comment ranges to enclosing paragraphs of word/document.xml
    doc = ET.fromstring(z.read("word/document.xml"))
    body = doc.find(W + "body")
    paras = list(body.iter(W + "p")) if body is not None else []

    anchors = {cid: {"paragraph_indices": [], "paragraph_texts": []} for cid in comments}
    open_ids: set[str] = set()
    for idx, p in enumerate(paras):
        starts = {e.get(W + "id") for e in p.iter(W + "commentRangeStart")}
        ends = {e.get(W + "id") for e in p.iter(W + "commentRangeEnd")}
        refs = {e.get(W + "id") for e in p.iter(W + "commentReference")}
        active = open_ids | starts | ends | refs
        ptxt = text_of(p).strip()
        for cid in active:
            if cid in anchors:
                anchors[cid]["paragraph_indices"].append(idx)
                if ptxt:
                    anchors[cid]["paragraph_texts"].append(ptxt)
        open_ids = (open_ids | starts) - ends - refs

    out = {
        "source_path": path,
        "comment_count": len(comments),
        "paragraph_count": len(paras),
        "comments": [
            {**comments[cid], **anchors.get(cid, {})}
            for cid in sorted(comments, key=lambda s: int(s) if s.isdigit() else 0)
        ],
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
