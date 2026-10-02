"""Controlled probe 配置。

probe 必须默认关闭。关闭时不创建线程、不创建文件、不同步 GPU tensor、
不改变 baseline 返回值、不引入 CARLA 依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# camera_logging_mode 允许值：不落原始像素，只落 shape/calibration 是默认。
CAMERA_MODES = ("NONE", "SHAPE_ONLY", "REFERENCE")
# flush_policy：每条 flush 或由 logger 线程批量 flush。
FLUSH_POLICIES = ("PER_RECORD", "BACKGROUND")


@dataclass(frozen=True)
class ProbeConfig:
    """不可变 probe 配置；默认全部安全关闭。"""

    enabled: bool = False
    schema_version: str = "driveclarify.controlled_probe.v0.1"
    output_path: str | None = None
    run_id: str | None = None
    queue_maxsize: int = 1024
    actor_roi_m: float = 50.0
    actor_max_count: int = 32
    camera_logging_mode: str = "SHAPE_ONLY"
    static_map_once: bool = True
    flush_policy: str = "BACKGROUND"
    # 运行环境标签（审计用），不参与任何 baseline 计算。
    provenance_tags: tuple[str, ...] = (
        "CONTROLLED_PROBE_V0",
        "NOT_CARLA_VERIFIED",
        "NOT_A_SAFETY_THRESHOLD",
    )

    def __post_init__(self) -> None:
        if self.camera_logging_mode not in CAMERA_MODES:
            raise ValueError(
                f"INVALID_CAMERA_LOGGING_MODE:{self.camera_logging_mode}"
            )
        if self.flush_policy not in FLUSH_POLICIES:
            raise ValueError(f"INVALID_FLUSH_POLICY:{self.flush_policy}")
        if self.queue_maxsize < 1:
            raise ValueError("QUEUE_MAXSIZE_MUST_BE_POSITIVE")
        if self.actor_max_count < 0:
            raise ValueError("ACTOR_MAX_COUNT_MUST_BE_NONNEGATIVE")
        if self.enabled and not self.output_path:
            raise ValueError("ENABLED_PROBE_REQUIRES_OUTPUT_PATH")

    @classmethod
    def disabled(cls) -> "ProbeConfig":
        """显式关闭配置；便于 baseline 默认路径使用。"""

        return cls(enabled=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProbeConfig":
        if not data:
            return cls.disabled()
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"UNKNOWN_PROBE_CONFIG_KEYS:{sorted(unknown)}")
        payload = dict(data)
        if "provenance_tags" in payload and payload["provenance_tags"] is not None:
            payload["provenance_tags"] = tuple(payload["provenance_tags"])
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "schema_version": self.schema_version,
            "output_path": self.output_path,
            "run_id": self.run_id,
            "queue_maxsize": self.queue_maxsize,
            "actor_roi_m": self.actor_roi_m,
            "actor_max_count": self.actor_max_count,
            "camera_logging_mode": self.camera_logging_mode,
            "static_map_once": self.static_map_once,
            "flush_policy": self.flush_policy,
            "provenance_tags": list(self.provenance_tags),
        }
