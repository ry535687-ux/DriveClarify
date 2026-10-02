"""追加一行体积更正。

`AGENT_WORKLOG.md` 与根 `COMMAND_LOG.md` 均为只追加文件，
即使是本轮刚写入的段落也不重写既有行，改为在末尾追加更正。
阶段副本 `COMMAND_LOG.md` 为本轮新建文件，同步追加同一行以保持一致。
"""

from __future__ import annotations

from pathlib import Path

from . import paths

LINE = (
    "\n更正（2026-09-11T01:56:00+08:00，只追加不重写）：上一段所记「交付 1.8M」为写入交接记录之前的实测值；"
    "随后新增 `evidence/THIS_ROUND_CHANGE_MANIFEST.md` 与阶段 `COMMAND_LOG.md` 副本后，"
    "`du -sh` 实测为 **1.9M**。根分区仍 12G 可用，未删除任何旧文件腾位置。\n"
)


def main() -> int:
    for path in (
        paths.REPO / "AGENT_WORKLOG.md",
        paths.REPO / "COMMAND_LOG.md",
        paths.OUT / "COMMAND_LOG.md",
    ):
        text = path.read_text(encoding="utf-8")
        separator = "" if text.endswith("\n") else "\n"
        path.write_text(text + separator + LINE, encoding="utf-8")
        print(f"appended correction -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
