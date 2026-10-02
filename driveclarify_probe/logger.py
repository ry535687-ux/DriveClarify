"""Append-only、bounded、fail-open 的 JSONL logger。

关键合同：
- bounded queue，满时丢日志并累加 dropped_records，不阻塞 baseline；
- 序列化 / 写盘 / close 的任何异常都不得传播到 control path；
- 每行独立 JSON，不覆盖旧行；
- logger 线程不得调用 CARLA / 模型 / PID / planner。
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from .serializer import SerializationError, serialize_record

# 队列哨兵：通知后台线程退出。
_SENTINEL = object()


class ProbeLogger:
    """线程化 append-only logger；关闭 probe 时不应创建本对象。"""

    def __init__(
        self,
        output_path: str,
        queue_maxsize: int = 1024,
        background: bool = True,
    ) -> None:
        self._output_path = output_path
        self._background = background
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=max(1, queue_maxsize))
        self._counters_lock = threading.Lock()
        self.dropped_records = 0
        self.serialization_errors = 0
        self.writer_errors = 0
        self.written_records = 0
        self._closed = False
        self._fh = None
        self._thread: threading.Thread | None = None
        self._start()

    def _start(self) -> None:
        # 打开文件失败也必须 fail-open：记 writer error，logger 变为 no-op。
        try:
            Path(self._output_path).parent.mkdir(parents=True, exist_ok=True)
            # append 模式：绝不覆盖旧行。
            self._fh = open(self._output_path, "a", encoding="utf-8")
        except OSError:
            self._fh = None
            self._bump("writer_errors")
            return
        if self._background:
            self._thread = threading.Thread(
                target=self._run, name="driveclarify-probe-logger", daemon=True
            )
            self._thread.start()

    def _bump(self, name: str) -> None:
        with self._counters_lock:
            setattr(self, name, getattr(self, name) + 1)

    def log(self, record: dict[str, Any]) -> None:
        """入队一条记录。永不抛异常、永不阻塞 baseline。"""

        if self._closed or self._fh is None:
            self._bump("dropped_records")
            return
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._bump("dropped_records")
            return
        if not self._background:
            # 同步 flush 模式：立即取出并写。异常仍不外传。
            self._drain_once(block=False)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                self._queue.task_done()
                break
            self._write_one(item)
            self._queue.task_done()

    def _drain_once(self, block: bool) -> None:
        try:
            item = self._queue.get(block=block)
        except queue.Empty:
            return
        if item is _SENTINEL:
            self._queue.task_done()
            return
        self._write_one(item)
        self._queue.task_done()

    def _write_one(self, record: Any) -> None:
        try:
            line = serialize_record(record)
        except SerializationError:
            self._bump("serialization_errors")
            return
        try:
            assert self._fh is not None
            self._fh.write(line + "\n")
            self._fh.flush()
            self._bump("written_records")
        except (OSError, ValueError):
            self._bump("writer_errors")

    def counters(self) -> dict[str, int]:
        with self._counters_lock:
            return {
                "dropped_records": self.dropped_records,
                "serialization_errors": self.serialization_errors,
                "writer_errors": self.writer_errors,
                "written_records": self.written_records,
            }

    def flush(self) -> None:
        """尽力 flush；失败不得影响 baseline。"""

        try:
            if not self._background:
                while not self._queue.empty():
                    self._drain_once(block=False)
            if self._fh is not None:
                self._fh.flush()
        except (OSError, ValueError):
            self._bump("writer_errors")

    def close(self) -> None:
        """关闭 logger；close 失败绝不影响 baseline 控制。"""

        if self._closed:
            return
        self._closed = True
        try:
            if self._background and self._thread is not None:
                self._queue.put_nowait(_SENTINEL)
                self._thread.join(timeout=5.0)
            else:
                while not self._queue.empty():
                    self._drain_once(block=False)
        except Exception:  # noqa: BLE001 - close must be inert to baseline
            self._bump("writer_errors")
        finally:
            try:
                if self._fh is not None:
                    self._fh.close()
            except (OSError, ValueError):
                self._bump("writer_errors")
