"""候选缓存的 Schema、关联、年龄、过期和刷新合同。"""

from __future__ import annotations

import math
from typing import Any

from .models import CacheResult, TriValue
from .schemas import validate_candidate_cache


SLOW_TRIGGER_TO_REFRESH_REASON = {
    "EPISODE_OPEN": "EPISODE_OPEN",
    "CANDIDATE_EXPIRED": "CANDIDATE_EXPIRED",
    "MATERIAL_SCENE_CHANGE": "MATERIAL_SCENE_CHANGE",
    "TIMELY_ANSWER_RECEIVED": "ANSWER_RECEIVED",
    "CANDIDATE_INVALIDATED": "PLAN_INVALIDATED",
    "EXPLICIT_REFRESH_REQUEST": "MANUAL_TEST_REFRESH",
}


def map_slow_trigger(trigger: str) -> str:
    """只接受冻结合同列出的六种事件触发器。"""

    try:
        return SLOW_TRIGGER_TO_REFRESH_REASON[trigger]
    except KeyError as exc:
        raise ValueError("ILLEGAL_REFRESH_TRIGGER") from exc


def derive_plan_age(cache: dict[str, Any], now_monotonic_s: float) -> float:
    return now_monotonic_s - float(cache["generated_at_monotonic_s"])


def validate_cache_cross_fields(
    cache: dict[str, Any],
    now_monotonic_s: float,
    *,
    tolerance: float = 1e-9,
) -> None:
    """补充 JSON Schema 无法表达的跨字段不变量。"""

    generated = float(cache["generated_at_monotonic_s"])
    valid_until = float(cache["valid_until_monotonic_s"])
    if valid_until <= generated:
        raise ValueError("CACHE_INVALID_VALIDITY_INTERVAL")
    expected_age = derive_plan_age(cache, now_monotonic_s)
    if expected_age < -tolerance:
        raise ValueError("CACHE_GENERATED_IN_FUTURE")
    if not math.isclose(float(cache["plan_age_s"]), expected_age, abs_tol=tolerance):
        raise ValueError("CACHE_PLAN_AGE_MISMATCH")


def check_cache(
    cache: dict[str, Any],
    now_monotonic_s: float,
    active_episode_id: str,
) -> CacheResult:
    """检查 cache 是否可执行；等号过期，UNKNOWN 保持 UNKNOWN。"""

    validate_candidate_cache(cache)
    validate_cache_cross_fields(cache, now_monotonic_s)
    fields = (
        "candidate_cache.validity_status",
        "candidate_cache.valid_until_monotonic_s",
        "candidate_cache.generated_at_monotonic_s",
        "candidate_cache.plan_age_s",
        "candidate_cache.episode_id",
        "candidate_cache.source_observation_id",
    )

    if cache["episode_id"] != active_episode_id:
        return CacheResult(
            TriValue.FALSE,
            "CACHE_EPISODE_MISMATCH",
            cache["validity_status"],
            float(cache["plan_age_s"]),
            fields,
            (),
        )

    status = cache["validity_status"]
    # UNKNOWN 不能改写成 EXPIRED/INVALIDATED，否则会丢失未知来源。
    if status == "UNKNOWN":
        return CacheResult(
            TriValue.UNKNOWN,
            "UNKNOWN_CACHE_VALIDITY",
            "UNKNOWN",
            float(cache["plan_age_s"]),
            fields,
            ("candidate_cache.validity_status",),
        )
    if status == "INVALIDATED":
        return CacheResult(
            TriValue.FALSE,
            "CACHE_INVALIDATED",
            "INVALIDATED",
            float(cache["plan_age_s"]),
            fields,
            (),
        )
    # 执行门采用严格 now < valid_until；等号已经过期。
    if status == "EXPIRED" or now_monotonic_s >= float(cache["valid_until_monotonic_s"]):
        return CacheResult(
            TriValue.FALSE,
            "CACHE_EXPIRED",
            "EXPIRED",
            float(cache["plan_age_s"]),
            fields,
            (),
        )
    return CacheResult(
        TriValue.TRUE,
        "CACHE_EXECUTABLE",
        "VALID",
        float(cache["plan_age_s"]),
        fields,
        (),
    )


def validate_cache_links(
    cache: dict[str, Any],
    consequences: list[dict[str, Any]],
) -> tuple[bool, tuple[str, ...]]:
    """验证 cache/episode/observation/candidate 四类 ID 的双向一致性。"""

    mismatches: list[str] = []
    candidate_ids = set(cache["candidate_ids"])
    for index, record in enumerate(consequences):
        prefix = f"consequences[{index}]"
        if record["cache_id"] != cache["cache_id"]:
            mismatches.append(f"{prefix}.cache_id")
        if record["episode_id"] != cache["episode_id"]:
            mismatches.append(f"{prefix}.episode_id")
        if record["observation_id"] != cache["source_observation_id"]:
            mismatches.append(f"{prefix}.observation_id")
        if record["candidate_id"] not in candidate_ids:
            mismatches.append(f"{prefix}.candidate_id")
    consequence_ids = {record["candidate_id"] for record in consequences}
    if consequence_ids != candidate_ids:
        mismatches.append("candidate_cache.candidate_ids")
    return not mismatches, tuple(sorted(set(mismatches)))
