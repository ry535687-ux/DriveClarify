"""Timed model output contract, independent of task identities and truth."""
from dataclasses import asdict
import hashlib
import json
import math

from driveclarify_task_relation_ablation_dev.relation import TimedTrajectory


def fingerprint(value):
    """Read-only digest, including bfloat16 storage without converting its values."""
    if hasattr(value, 'detach'):
        import torch
        t = value.detach().cpu().contiguous()
        return {'dtype': str(t.dtype), 'shape': list(t.shape),
                'bytes': hashlib.sha256(t.view(torch.uint8).numpy().tobytes()).hexdigest()}
    if isinstance(value, dict):
        return {str(k): fingerprint(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [fingerprint(v) for v in value]
    if hasattr(value, 'tolist'):
        return fingerprint(value.tolist())
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError('UNSUPPORTED_CONTEXT_VALUE:' + type(value).__name__)


def context_digest(driving_input):
    value = {k: fingerprint(v) for k, v in driving_input.items()
             if k not in ('prompt', 'prompt_inference')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def timed_speed_waypoints(points, *, slot, frame, simulation_time_s, digest,
                          data_save_freq, carla_fps, wp_dilation, age_s):
    # Native datagen saves every 5 of 20 ticks. Training loads consecutive saved
    # ego matrices and drops the current point. Head predicts cumulative xy in
    # that original CARLA ego frame; route's equal-distance points are NOT used.
    dt = float(data_save_freq) * float(wp_dilation) / float(carla_fps)
    if not math.isclose(dt, 0.25, abs_tol=1e-12):
        raise ValueError('UNVERIFIED_MODEL_WAYPOINT_TIMEBASE')
    if not math.isfinite(age_s) or not -1e-9 <= age_s <= 0.100000001:
        raise ValueError('CANDIDATE_SENSOR_CONTEXT_STALE')
    xy = tuple(tuple(float(v) for v in p) for p in points)
    if len(xy) < 8 or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in xy):
        raise ValueError('INVALID_TIMED_SPEED_WAYPOINTS')
    # t=0 is the ego coordinate origin by definition, not an imputed model point.
    return TimedTrajectory(slot, int(frame), float(simulation_time_s), digest,
                           ((0.0, 0.0),) + xy[:8],
                           tuple(i * dt for i in range(9)), True)
