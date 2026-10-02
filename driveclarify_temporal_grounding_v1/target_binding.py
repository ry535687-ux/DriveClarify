"""Bind temporal semantics to an executable upcoming runtime route branch."""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence, Tuple

from .contracts import EventRegion, RouteTarget, canonical_sha256


def _option_name(value: Any) -> str:
    if hasattr(value, "name"):
        return str(value.name).upper()
    text = str(value).upper()
    return text.rsplit(".", 1)[-1]


def _xy(location: Any) -> Optional[Tuple[float, float]]:
    try:
        return (float(location.x), float(location.y))
    except (AttributeError, TypeError, ValueError):
        try:
            return (float(location[0]), float(location[1]))
        except (IndexError, TypeError, ValueError):
            return None


class RuntimeRouteTargetBinder:
    """Read-only route-deque binding; no scenario annotation or actor state."""

    implementation_id = "RUNTIME_ROUTE_TOPOLOGY_FIRST_TURN_BINDER_V1"

    def bind(self, route: Any) -> Optional[RouteTarget]:
        rows: List[Tuple[float, float, str]] = []
        try:
            iterator = iter(route)
        except TypeError:
            return None
        for item in iterator:
            try:
                location, option = item[0], item[1]
            except (IndexError, TypeError):
                continue
            point = _xy(location)
            if point is None:
                continue
            rows.append((point[0], point[1], _option_name(option)))
        if not rows:
            return None
        route_digest = canonical_sha256(rows)
        turn_index = next(
            (
                index
                for index, row in enumerate(rows)
                if row[2] in {"LEFT", "RIGHT"}
            ),
            None,
        )
        if turn_index is None:
            return None
        x, y, option = rows[turn_index]
        anchor = (round(x, 3), round(y, 3))
        junction_id = "junction-" + canonical_sha256(
            {"route_digest": route_digest, "turn_index": turn_index, "anchor": anchor}
        )[:16]
        branch_id = "branch-{}-{}".format(
            option.casefold(),
            canonical_sha256({"junction": junction_id, "anchor": anchor})[:16],
        )
        return RouteTarget(
            target_id="target-" + canonical_sha256(
                {"junction_id": junction_id, "branch_id": branch_id}
            )[:20],
            junction_id=junction_id,
            branch_id=branch_id,
            road_option=option,
            route_opportunity_index=int(turn_index),
            anchor_xy=anchor,
            route_digest=route_digest,
            source="ONLINE_SIMLINGO_ROUTE_DEQUE_READ_ONLY",
            executable=True,
        )

    @staticmethod
    def event_region(
        target: RouteTarget, *, image_width: int, image_height: int
    ) -> EventRegion:
        """Construct the ego-forward conflict corridor without world projection.

        The prior camera-extrinsic audit left exact world-to-pixel axis semantics
        unresolved, so V1 deliberately does not pretend to project a gold map box.
        A normalized, calibration-stable lower-center roadway corridor is enabled
        only when a runtime route turn is bound. This is conservative and explicit.
        """

        width, height = int(image_width), int(image_height)
        box = (
            round(0.35 * width, 3),
            round(0.25 * height, 3),
            round(0.80 * width, 3),
            round(0.75 * height, 3),
        )
        return EventRegion(
            region_id="event-region-" + canonical_sha256(
                {
                    "target_id": target.target_id,
                    "normalized_xyxy": [0.35, 0.25, 0.80, 0.75],
                    "image_size": [width, height],
                }
            )[:20],
            bbox_xyxy=box,
            image_width=width,
            image_height=height,
            construction_source=(
                "RUNTIME_ROUTE_TURN_GATED_NORMALIZED_EGO_FORWARD_IMAGE_CORRIDOR_V1_"
                "TRAIN_T1_REAL_RGB_CALIBRATED"
            ),
            target_branch_id=target.branch_id,
            privileged_state_read_count=0,
        )


__all__ = ["RuntimeRouteTargetBinder"]
