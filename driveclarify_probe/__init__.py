"""DriveClarify Controlled Probe Harness (CP0).

默认关闭、append-only、bounded、fail-open 的只读观测 harness。

设计约束（见 reports/controlled_probe_v0/CP0_IMPLEMENTATION_REPORT.md）：
- 不改变 SimLingo baseline 数据流；
- 不触发第二次 forward / PID / route planner；
- 不推进任何 DriveClarify state；
- 关闭时零额外运行副作用，且不引入 CARLA / torch / GPU 依赖到 offline tests。

本模块只接收 baseline 已经产生的值的只读副本；它没有执行 baseline 的职责。
"""

from __future__ import annotations

SCHEMA_VERSION = "driveclarify.controlled_probe.v0.1"

from .config import ProbeConfig  # noqa: E402
from .hooks import ControlledProbe, NullProbe, build_probe  # noqa: E402

__all__ = [
    "SCHEMA_VERSION",
    "ProbeConfig",
    "ControlledProbe",
    "NullProbe",
    "build_probe",
]
