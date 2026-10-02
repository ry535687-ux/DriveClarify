"""Exact, bounded source compatibility used only by the engineering bring-up.

The V3 wrapper mixed two coordinate roles: the UKF-filtered navigation origin
used to construct SimLingo's target tensor, and the raw GNSS position converted
to CARLA world coordinates.  The former remains the model transform origin;
the latter is the appropriate read-only comparison with the actor transform.
"""

from __future__ import annotations


def bind_actor_gate_to_raw_gnss_carla_position(source: str) -> str:
    replacements = (
        (
            """            actor_nav_position_error = math.hypot(
                float(actor_location.x) - nav_origin[0],
                float(actor_location.y) - nav_origin[1],
            )
""",
            """            raw_gnss_carla_position = self._route_planner.convert_gps_to_carla(
                input_data[\"gps\"][1]
            )
            actor_raw_gnss_position_error = math.hypot(
                float(actor_location.x) - float(raw_gnss_carla_position[0]),
                float(actor_location.y) - float(raw_gnss_carla_position[1]),
            )
            actor_filtered_navigation_position_error = math.hypot(
                float(actor_location.x) - nav_origin[0],
                float(actor_location.y) - nav_origin[1],
            )
""",
        ),
        (
            "            if actor_nav_position_error > 2.0:\n",
            "            if actor_raw_gnss_position_error > 2.0:\n",
        ),
        (
            '                "actor_to_navigation_origin_error_metres": actor_nav_position_error,\n',
            '                "actor_to_raw_gnss_carla_position_error_metres": actor_raw_gnss_position_error,\n'
            '                "actor_to_model_filtered_navigation_origin_error_metres": actor_filtered_navigation_position_error,\n'
            '                "actor_position_gate_source": "RAW_GNSS_CONVERTED_TO_CARLA_WORLD_READ_ONLY",\n',
        ),
    )
    transformed = source
    for original, replacement in replacements:
        if transformed.count(original) != 1:
            raise RuntimeError("MANEUVER_BRANCH_ENGINEERING_SOURCE_COMPAT_PATTERN_MISMATCH")
        transformed = transformed.replace(original, replacement, 1)
    return transformed

