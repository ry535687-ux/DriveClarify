"""Exact source transform for the prepared M3B wrapper; never imports CARLA or torch."""

from __future__ import annotations


def prepare_m3b_wrapper_source(source: str) -> str:
    """Repair coordinate operands while preserving V3's bounded execution contract."""

    replacements = (
        (
            '            "DRIVECLARIFY_MB_FAILURE_EVIDENCE",\n',
            '            "DRIVECLARIFY_MB_FAILURE_EVIDENCE",\n'
            '            "DRIVECLARIFY_M3B_COORDINATE_EVIDENCE",\n',
        ),
        (
            "from driveclarify_consequence.maneuver_branch_capture import (  # noqa: E402\n",
            "from driveclarify_real_mapping_v1.coordinate_root_cause import (  # noqa: E402\n"
            "    gps_to_carla as _m3b_gps_to_carla,\n"
            "    parse_opendrive_georeference as _m3b_parse_georeference,\n"
            ")\n"
            "from driveclarify_real_mapping_v1.plan_frame_trace import (  # noqa: E402\n"
            "    SUPPORTED_PLAN_FRAME as M3B_PLAN_FRAME,\n"
            "    SUPPORTED_PLAN_UNIT as M3B_PLAN_UNIT,\n"
            ")\n"
            "from driveclarify_consequence.maneuver_branch_capture import (  # noqa: E402\n",
        ),
        (
            "            actor_rotation = actor_transform.rotation\n"
            "            nav_origin = [float(tick_data[\"gps\"][0]), float(tick_data[\"gps\"][1])]\n"
            "            nav_yaw = float(tick_data[\"compass\"])\n",
            "            actor_rotation = actor_transform.rotation\n"
            "            actor_origin = [float(actor_location.x), float(actor_location.y)]\n"
            "            actor_yaw = math.radians(float(actor_rotation.yaw))\n"
            "            nav_origin = [float(tick_data[\"gps\"][0]), float(tick_data[\"gps\"][1])]\n"
            "            nav_yaw = float(tick_data[\"compass\"])\n",
        ),
        (
            "                origin_world_xy=nav_origin,\n"
            "                yaw_rad=nav_yaw,\n"
            "                opendrive_sha256=opendrive_sha256,\n",
            "                origin_world_xy=actor_origin,\n"
            "                yaw_rad=nav_yaw,\n"
            "                opendrive_sha256=opendrive_sha256,\n",
        ),
        (
            "            actor_nav_position_error = math.hypot(\n"
            "                float(actor_location.x) - nav_origin[0],\n"
            "                float(actor_location.y) - nav_origin[1],\n"
            "            )\n",
            "            map_georeference = _m3b_parse_georeference(opendrive_text)\n"
            "            raw_gnss_carla_position = _m3b_gps_to_carla(input_data[\"gps\"][1], map_georeference)\n"
            "            actor_raw_gnss_position_error = math.hypot(\n"
            "                float(actor_location.x) - float(raw_gnss_carla_position[0]),\n"
            "                float(actor_location.y) - float(raw_gnss_carla_position[1]),\n"
            "            )\n"
            "            actor_filtered_navigation_position_error = math.hypot(\n"
            "                float(actor_location.x) - nav_origin[0],\n"
            "                float(actor_location.y) - nav_origin[1],\n"
            "            )\n",
        ),
        (
            "            if actor_nav_position_error > 2.0:\n",
            "            if actor_raw_gnss_position_error > 2.0:\n",
        ),
        (
            '                "actor_to_navigation_origin_error_metres": actor_nav_position_error,\n',
            '                "actor_to_raw_gnss_map_georeference_error_metres": actor_raw_gnss_position_error,\n'
            '                "actor_to_model_filtered_navigation_origin_error_metres": actor_filtered_navigation_position_error,\n'
            '                "actor_position_gate_source": "RAW_GNSS_WITH_RECORDED_OPENDRIVE_GEOREFERENCE",\n'
            '                "actor_position_gate_threshold_metres": 2.0,\n'
            '                "raw_gnss_lat_lon_alt": [float(item) for item in input_data["gps"][1]],\n'
            '                "raw_gnss_map_carla_xyz": [float(item) for item in raw_gnss_carla_position],\n'
            '                "ego_actor_carla_xyz": [float(actor_location.x), float(actor_location.y), float(actor_location.z)],\n'
            '                "opendrive_georeference": map_georeference.to_dict(),\n'
            '                "gnss_sensor_extrinsic_vehicle_xyz": [0.0, 0.0, 0.0],\n',
        ),
        (
            '                "source_is_runtime_numeric_probe_not_comment_only": True,\n'
            '            }\n'
            '            if coordinate_reasons:\n',
            '                "source_is_runtime_numeric_probe_not_comment_only": True,\n'
            '            }\n'
            '            coordinate_evidence = with_hash({\n'
            '                "schema_version": "driveclarify.m3b.coordinate_semantic_evidence.v1",\n'
            '                "run_id": self._p0a_run_id,\n'
            '                "observation_id": observation_id,\n'
            '                "carla_frame": int(snapshot.frame),\n'
            '                "simulation_timestamp_seconds": float(snapshot.timestamp.elapsed_seconds),\n'
            '                "gps_sensor_frame": int(input_data["gps"][0]),\n'
            '                "runtime_coordinate_validation": runtime_coordinate_validation,\n'
            '                "persisted_before_coordinate_gate": True,\n'
            '                "persisted_before_candidate_output": True,\n'
            '                "capture_side_effect_delta": {"world_tick": 0, "model_forward": 0, "pid": 0, "planner_advance": 0, "control": 0},\n'
            '            })\n'
            '            coordinate_evidence_path = Path(os.environ["DRIVECLARIFY_M3B_COORDINATE_EVIDENCE"])\n'
            '            atomic_write_json(coordinate_evidence_path, coordinate_evidence)\n'
            '            persisted_coordinate_evidence = json.loads(coordinate_evidence_path.read_text(encoding="utf-8"))\n'
            '            if not verify_hash(persisted_coordinate_evidence):\n'
            '                raise RuntimeError("M3B_COORDINATE_EVIDENCE_PERSISTENCE_FAILED")\n'
            '            if coordinate_reasons:\n',
        ),
        (
            '                "navigation_transform_used_for_model": {\n'
            '                    "origin_world_xy": nav_origin,\n'
            '                    "yaw_rad": nav_yaw,\n'
            '                },\n',
            '                "navigation_transform_used_for_model_target": {\n'
            '                    "origin_world_xy": nav_origin,\n'
            '                    "yaw_rad": nav_yaw,\n'
            '                    "role": "MODEL_TARGET_INPUT_ONLY_NOT_PLAN_OUTPUT_WORLD_ORIGIN",\n'
            '                },\n'
            '                "plan_mapping_calibrated_ego_transform": {\n'
            '                    "origin_world_xy": actor_origin,\n'
            '                    "yaw_rad": nav_yaw,\n'
            '                    "actor_yaw_rad": actor_yaw,\n'
            '                    "actor_to_compass_yaw_error_degrees": math.degrees(actor_nav_yaw_error),\n'
            '                    "plan_frame": M3B_PLAN_FRAME,\n'
            '                    "plan_unit": M3B_PLAN_UNIT,\n'
            '                    "origin_source": "EGO_ACTOR_LOCATION_AT_SOURCE_OBSERVATION",\n'
            '                    "orientation_source": "PREPROCESSED_IMU_COMPASS_CARLA_YAW_SAME_OBSERVATION",\n'
            '                },\n',
        ),
        (
            "                origin_world_xy=nav_origin,\n"
            "                yaw_rad=nav_yaw,\n"
            "                runtime_coordinate_validation=runtime_coordinate_validation,\n",
            "                origin_world_xy=actor_origin,\n"
            "                yaw_rad=nav_yaw,\n"
            "                runtime_coordinate_validation=runtime_coordinate_validation,\n",
        ),
        (
            "            frozen_path = Path(os.environ[\"DRIVECLARIFY_MB_FROZEN_PACKAGE\"])\n",
            "            frozen[\"m3b_plan_semantic_mapping_contract\"] = {\n"
            "                \"plan_frame\": M3B_PLAN_FRAME,\n"
            "                \"plan_unit\": M3B_PLAN_UNIT,\n"
            "                \"origin\": \"EGO_ACTOR_TRANSFORM_AT_SOURCE_OBSERVATION\",\n"
            "                \"branch_frame\": \"CARLA_WORLD\",\n"
            "                \"branch_transform_source\": \"EGO_ACTOR_ORIGIN_PLUS_PREPROCESSED_IMU_COMPASS_YAW_SAME_CARLA_FRAME\",\n"
            "                \"source_grade\": \"SUPPORTED_FROM_SIMLINGO_SOURCE\",\n"
            "                \"runtime_probe_grade\": \"CAPTURED_BEFORE_CANDIDATE_OUTPUT\",\n"
            "            }\n"
            "            frozen = with_hash(frozen, \"package_sha256\")\n"
            "            frozen_path = Path(os.environ[\"DRIVECLARIFY_MB_FROZEN_PACKAGE\"])\n",
        ),
        (
            '                    "raw_route_frame": RAW_ROUTE_FRAME,\n'
            '                    "raw_route_unit": RAW_ROUTE_UNIT,\n',
            '                    "raw_route_frame": RAW_ROUTE_FRAME,\n'
            '                    "raw_route_unit": RAW_ROUTE_UNIT,\n'
            '                    "plan_semantic_frame": M3B_PLAN_FRAME,\n'
            '                    "plan_semantic_unit": M3B_PLAN_UNIT,\n'
            '                    "plan_semantic_origin": "EGO_ACTOR_TRANSFORM_AT_SOURCE_OBSERVATION",\n'
            '                    "plan_semantic_source_grade": "SUPPORTED_FROM_SIMLINGO_SOURCE",\n',
        ),
    )
    transformed = source
    for original, replacement in replacements:
        if transformed.count(original) != 1:
            raise RuntimeError("M3B_WRAPPER_SOURCE_PATTERN_MISMATCH")
        transformed = transformed.replace(original, replacement, 1)
    transformed = transformed.replace(
        'CAPTURE_PROTOCOL = "DRIVECLARIFY_REAL_MANEUVER_BRANCH_MAPPING_PILOT_V3"',
        'CAPTURE_PROTOCOL = "DRIVECLARIFY_M3B_SINGLE_REAL_PLAN_SEMANTIC_MAPPING_PILOT_V1"',
        1,
    ).replace(
        'RESULT_TYPE = "DRIVECLARIFY_REAL_MANEUVER_BRANCH_CAPTURE_RESULT_V3"',
        'RESULT_TYPE = "DRIVECLARIFY_M3B_SINGLE_REAL_PLAN_SEMANTIC_MAPPING_RESULT_V1"',
        1,
    )
    return transformed
