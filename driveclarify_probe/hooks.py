"""Controlled probe hook API。

设计合同（baseline equivalence）：
- probe 只接收 baseline 已经产生的值，不拥有执行 baseline 的职责；
- observe_* 不调用 model / PID / route planner；
- observe_* 不修改传入 tensor / dict / list / control 对象；
- observe_* 不返回替代 control（返回 None 或原对象本身）；
- logger 异常绝不改变 baseline 返回或抛入 control path。

默认使用 NullProbe（关闭）：零线程、零文件、零 GPU 同步、零 CARLA 依赖。
"""

from __future__ import annotations

import itertools
from typing import Any

from .config import ProbeConfig
from .logger import ProbeLogger
from .record import build_record


class NullProbe:
    """关闭态 probe。所有方法为 no-op，返回 None，无任何副作用。"""

    enabled = False

    def observe_tick(self, *args: Any, **kwargs: Any) -> None:
        return None

    def observe_model_output(self, *args: Any, **kwargs: Any) -> None:
        return None

    def observe_baseline_control(self, *args: Any, **kwargs: Any) -> None:
        return None

    def observe_candidate_set(self, *args: Any, **kwargs: Any) -> None:
        return None

    def observe_final_m2b_decision(self, *args: Any, **kwargs: Any) -> None:
        return None

    def commit_tick(self, *args: Any, **kwargs: Any) -> None:
        return None

    def counters(self) -> dict[str, int]:
        return {
            "dropped_records": 0,
            "serialization_errors": 0,
            "writer_errors": 0,
            "written_records": 0,
        }

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class ControlledProbe:
    """启用态 probe。缓存单个 tick 的三边界观测，commit 时构造一条记录。

    每个 CARLA tick 的用法：
        probe.observe_tick(p0_dict)          # P0：tick() 之后
        probe.observe_model_output(p1_dict)  # P1：唯一一次 forward + .float() 之后
        probe.observe_baseline_control(p2)   # P2：VehicleControl 构造后、return 前
        probe.commit_tick()                  # 汇总入队（append-only, fail-open）

    任一 observe_* 或 commit_tick 抛出异常都会被吞掉并累加内部错误计数，
    确保 baseline control 不受影响。
    """

    enabled = True

    def __init__(
        self,
        config: ProbeConfig,
        logger: ProbeLogger | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        self._config = config
        self._provenance = provenance or {}
        assert config.output_path is not None
        self._logger = logger or ProbeLogger(
            config.output_path,
            queue_maxsize=config.queue_maxsize,
            background=(config.flush_policy == "BACKGROUND"),
        )
        self._run_id = config.run_id or "run"
        self._seq = itertools.count()
        self.harness_errors = 0
        self._reset_pending()

    def _reset_pending(self) -> None:
        self._p0: dict[str, Any] | None = None
        self._p1: dict[str, Any] | None = None
        self._p2: dict[str, Any] | None = None
        self._p3: dict[str, Any] | None = None
        self._p4: dict[str, Any] | None = None

    # ---- observe API：只接收既有值，绝不 re-execute ----

    def observe_tick(self, p0_tick: dict[str, Any] | None) -> None:
        try:
            self._p0 = dict(p0_tick) if isinstance(p0_tick, dict) else None
        except Exception:  # noqa: BLE001
            self.harness_errors += 1

    def observe_model_output(self, p1_model: dict[str, Any] | None) -> None:
        try:
            # 浅复制引用（tensor 不复制、不修改；后续只读摘要）。
            self._p1 = dict(p1_model) if isinstance(p1_model, dict) else None
        except Exception:  # noqa: BLE001
            self.harness_errors += 1

    def observe_baseline_control(self, p2_control: dict[str, Any] | None) -> None:
        try:
            self._p2 = dict(p2_control) if isinstance(p2_control, dict) else None
        except Exception:  # noqa: BLE001
            self.harness_errors += 1

    def observe_candidate_set(self, candidate_set: dict[str, Any] | None) -> None:
        try:
            self._p3 = dict(candidate_set) if isinstance(candidate_set, dict) else None
        except Exception:  # noqa: BLE001
            self.harness_errors += 1

    def observe_final_m2b_decision(self, m2b_decision: dict[str, Any] | None) -> None:
        try:
            self._p4 = dict(m2b_decision) if isinstance(m2b_decision, dict) else None
        except Exception:  # noqa: BLE001
            self.harness_errors += 1

    def commit_tick(self, clock_now_monotonic: float | None = None) -> None:
        """构造记录并 fail-open 入队，然后清空本 tick 缓存。"""

        try:
            counters = self._logger.counters()
            record = build_record(
                run_id=self._run_id,
                record_seq=next(self._seq),
                p0_tick=self._p0,
                p1_model=self._p1,
                p2_control=self._p2,
                candidate_set=self._p3,
                m2b_decision=self._p4,
                provenance={**self._provenance, **counters},
                clock_now_monotonic=clock_now_monotonic,
            )
            self._logger.log(record)
        except Exception:  # noqa: BLE001 - never propagate into baseline
            self.harness_errors += 1
        finally:
            self._reset_pending()

    def counters(self) -> dict[str, int]:
        c = self._logger.counters()
        c["harness_errors"] = self.harness_errors
        return c

    def flush(self) -> None:
        try:
            self._logger.flush()
        except Exception:  # noqa: BLE001
            self.harness_errors += 1

    def close(self) -> None:
        try:
            self._logger.close()
        except Exception:  # noqa: BLE001
            self.harness_errors += 1


def build_probe(
    config: ProbeConfig | None,
    provenance: dict[str, Any] | None = None,
) -> "NullProbe | ControlledProbe":
    """工厂：默认（None 或 disabled）返回 NullProbe，无任何副作用。"""

    if config is None or not config.enabled:
        return NullProbe()
    return ControlledProbe(config, provenance=provenance)


def observe_without_reexecution(
    probe: "NullProbe | ControlledProbe",
    *,
    existing_tick: dict[str, Any] | None,
    existing_model_output: dict[str, Any] | None,
    existing_control: Any,
    candidate_set: dict[str, Any] | None = None,
    m2b_decision: dict[str, Any] | None = None,
    control_fields: dict[str, Any] | None = None,
    clock_now_monotonic: float | None = None,
) -> Any:
    """一次性观测三边界并返回**原始 control 对象本身**。

    该 helper 明确不构造替代控制：它接收 baseline 已产生的 control，
    观测后原样返回，从不调用 baseline_callable。
    """

    probe.observe_tick(existing_tick)
    probe.observe_model_output(existing_model_output)
    probe.observe_candidate_set(candidate_set)
    probe.observe_final_m2b_decision(m2b_decision)
    # control_fields 是 control 对象的只读快照（steer/throttle/brake…）。
    probe.observe_baseline_control(control_fields)
    probe.commit_tick(clock_now_monotonic=clock_now_monotonic)
    # 返回原对象引用，不构造新 control。
    return existing_control
