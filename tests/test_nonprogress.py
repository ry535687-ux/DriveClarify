from driveclarify.native.nonprogress import (
    NonProgressMonitor,
    ProgressSample,
)


def sample(
    frame,
    *,
    x=0.0,
    speed=0.0,
    target=(10.0, 0.0),
    progress=0.0,
    desired=1.0,
    brake=0.0,
    forwards=None,
    controls=None,
    defer=False,
):
    return ProgressSample(
        frame=frame,
        xyz=(x, 0.0, 0.0),
        speed_mps=speed,
        target_point=target,
        route_progress=progress,
        desired_speed_mps=desired,
        brake=brake,
        model_forward_count=frame if forwards is None else forwards,
        control_return_count=frame if controls is None else controls,
        defer_pending=defer,
    )


def classify(rows):
    monitor = NonProgressMonitor(window_ticks=len(rows))
    result = None
    for row in rows:
        result = monitor.observe(row)
    return result


def test_detects_sequence21_model_requested_stop_without_case_identity():
    result = classify(
        [sample(i, desired=0.03, brake=1.0) for i in range(8)]
    )
    assert result["classification"] == "MODEL_REQUESTED_STOP"


def test_detects_positive_prediction_while_ego_is_stationary():
    result = classify([sample(i, desired=2.0) for i in range(8)])
    assert result["classification"] == "POSITIVE_PREDICTED_SPEED_EGO_STATIONARY"


def test_detects_live_control_loop_with_dead_model_loop():
    result = classify(
        [sample(i, forwards=4, controls=i, desired=None) for i in range(8)]
    )
    assert result["classification"] == "MODEL_LOOP_DEAD_CONTROL_LOOP_LIVE"


def test_detects_indefinite_defer_before_other_stationary_categories():
    result = classify([sample(i, defer=True) for i in range(8)])
    assert result["classification"] == "INDEFINITE_DEFER_NONPROGRESS"


def test_detects_frozen_route_and_target_with_neutral_speed_prediction():
    result = classify([sample(i, desired=0.5) for i in range(8)])
    assert result["classification"] == "ROUTE_AND_TARGET_PROGRESS_FROZEN"


def test_progress_is_not_a_fault():
    result = classify(
        [
            sample(i, x=float(i), speed=2.0, target=(10.0 - i, 0.0), progress=i)
            for i in range(8)
        ]
    )
    assert result["classification"] == "PROGRESS_OR_NO_GENERIC_FAULT"
