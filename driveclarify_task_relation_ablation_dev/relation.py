"""仅消费同源、带时间的短轨迹；禁止由导航路线猜测时间或补零。"""
from dataclasses import dataclass
import math
from typing import Optional, Tuple

from driveclarify_rq1_v2.consequence import ConsequenceRelation


@dataclass(frozen=True)
class TimedTrajectory:
    # slot 仅用于同次候选绑定，不携带候选的语言名称。
    slot: int
    source_frame: int
    source_time_s: float
    nonlanguage_context_sha256: str
    xy_m: Tuple[Tuple[float, float], ...]
    relative_times_s: Tuple[float, ...]
    valid: bool
    coordinate_frame: str = "CARLA_EGO_X_FORWARD_Y_RIGHT_METRES"
    producer: str = "SIMLINGO_CANDIDATE_FORWARD"


@dataclass(frozen=True)
class MetricConfig:
    horizon_s: float = 2.0
    sample_period_s: float = 0.1
    maximum_source_gap_s: float = 0.2
    threshold_m: Optional[float] = None


@dataclass(frozen=True)
class RelationResult:
    relation: ConsequenceRelation
    max_aligned_distance_m: Optional[float]
    reason: str
    sample_count: int = 0


def unknown(reason):
    return RelationResult(ConsequenceRelation.UNKNOWN, None, reason)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate(t, cfg):
    if type(t) is not TimedTrajectory:
        return "TIMED_CANDIDATE_NOT_AVAILABLE"
    if t.valid is not True:
        return "CANDIDATE_INVALID"
    if t.coordinate_frame != "CARLA_EGO_X_FORWARD_Y_RIGHT_METRES":
        return "UNVERIFIED_COORDINATE_FRAME"
    if t.producer != "SIMLINGO_CANDIDATE_FORWARD":
        return "UNVERIFIED_CANDIDATE_PRODUCER"
    if type(t.slot) is not int or type(t.source_frame) is not int or t.source_frame < 0:
        return "SOURCE_IDENTITY_INVALID"
    if not _finite(t.source_time_s):
        return "SOURCE_TIME_INVALID"
    if (not isinstance(t.nonlanguage_context_sha256, str)
            or len(t.nonlanguage_context_sha256) != 64
            or any(c not in "0123456789abcdef" for c in t.nonlanguage_context_sha256)):
        return "NONLANGUAGE_CONTEXT_DIGEST_INVALID"
    if not isinstance(t.xy_m, tuple) or not isinstance(t.relative_times_s, tuple):
        return "IMMUTABLE_SAMPLES_REQUIRED"
    if len(t.xy_m) != len(t.relative_times_s) or len(t.xy_m) < 2:
        return "SAMPLE_SHAPE_INVALID"
    if any(not isinstance(p, tuple) or len(p) != 2 or not all(_finite(v) for v in p) for p in t.xy_m):
        return "COORDINATE_INVALID"
    times = t.relative_times_s
    if not all(_finite(v) for v in times):
        return "TIME_INVALID"
    if any(b <= a or b - a > cfg.maximum_source_gap_s + 1e-9 for a, b in zip(times, times[1:])):
        return "TIME_ALIGNMENT_INVALID"
    if abs(times[0]) > 1e-9 or times[-1] < cfg.horizon_s - 1e-9:
        return "FULL_TIME_HORIZON_UNAVAILABLE"
    if math.hypot(*t.xy_m[0]) > 1e-6:
        return "EGO_ORIGIN_NOT_SHARED"
    return None


def _interpolate(t, now):
    for i in range(len(t.relative_times_s) - 1):
        a, b = t.relative_times_s[i:i + 2]
        if a - 1e-9 <= now <= b + 1e-9:
            alpha = min(1.0, max(0.0, (now - a) / (b - a)))
            return tuple(x + alpha * (y - x) for x, y in zip(t.xy_m[i], t.xy_m[i + 1]))
    raise ValueError("NO_EXTRAPOLATION_ALLOWED")


def compare_trajectories(first, second, cfg):
    """max_t ||p1(t)-p2(t)||2，0..2 s 同时刻插值，不作 DTW/最近点匹配。"""
    if type(cfg) is not MetricConfig:
        return unknown("METRIC_CONFIG_INVALID")
    if not all(_finite(v) and v > 0 for v in (cfg.horizon_s, cfg.sample_period_s, cfg.maximum_source_gap_s)):
        return unknown("METRIC_CONFIG_INVALID")
    steps = cfg.horizon_s / cfg.sample_period_s
    if steps > 10000 or abs(steps - round(steps)) > 1e-9:
        return unknown("METRIC_GRID_INVALID")
    for t in (first, second):
        error = _validate(t, cfg)
        if error:
            return unknown(error)
    if first.slot == second.slot:
        return unknown("CANDIDATES_NOT_DISTINCT")
    if (first.source_frame != second.source_frame
            or first.source_time_s != second.source_time_s
            or first.nonlanguage_context_sha256 != second.nonlanguage_context_sha256):
        return unknown("NONLANGUAGE_CONTEXT_NOT_IDENTICAL")
    if cfg.threshold_m is None:
        return unknown("DEVELOPMENT_THRESHOLD_NOT_CALIBRATED")
    if not _finite(cfg.threshold_m) or cfg.threshold_m < 0:
        return unknown("THRESHOLD_INVALID")
    values = []
    for i in range(int(round(steps)) + 1):
        a = _interpolate(first, i * cfg.sample_period_s)
        b = _interpolate(second, i * cfg.sample_period_s)
        distance = math.hypot(a[0] - b[0], a[1] - b[1])
        if not all(math.isfinite(v) for v in (*a, *b, distance)):
            return unknown("DISTANCE_NUMERIC_OVERFLOW")
        values.append(distance)
    distance = max(values)
    if not math.isfinite(distance):
        return unknown("DISTANCE_NUMERIC_OVERFLOW")
    relation = (ConsequenceRelation.TASK_CRITICAL if distance > cfg.threshold_m
                else ConsequenceRelation.TASK_EQUIVALENT)
    return RelationResult(relation, distance, "SHORT_TRAJECTORY_THRESHOLD", len(values))
