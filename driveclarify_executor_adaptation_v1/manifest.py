"""Frozen metadata-only manifest checks for selected-executor adaptation.

This module has no CARLA, model, tokenizer, checkpoint, or training imports.
It validates metadata supplied by a separately authorized future generator.
"""

from collections import defaultdict
import hashlib
import json
import math
import os
import re


class ManifestValidationError(ValueError):
    pass


TRAINING_XML_SHA256 = (
    "06003fb907c70fb7cc084fe58bb366b7b4d41ec6d6ace306d55a440ea31727a1"
)
VALIDATION_XML_SHA256 = (
    "6122cffc1be24f1055a24c9744fa54f41fef152eb66a347d0d6393b124760017"
)

SPLIT_RULES = (
    (TRAINING_XML_SHA256, 0, 71, "train"),
    (TRAINING_XML_SHA256, 72, 80, "dev"),
    (TRAINING_XML_SHA256, 81, 89, "in_map_test"),
    (VALIDATION_XML_SHA256, 0, 19, "held_out_map_test"),
)

QUOTAS = {
    "train": (32000, 8000, 20000, 4000, 12000, 8000, 4000, 800, 80000),
    "dev": (4000, 1000, 2500, 500, 1500, 1000, 500, 100, 10000),
    "in_map_test": (4000, 1000, 2500, 500, 1500, 1000, 500, 100, 10000),
    "held_out_map_test": (8000, 2000, 5000, 1000, 3000, 2000, 1000, 200, 20000),
}
QUOTA_FIELDS = (
    "paired_quartet",
    "paired_groups",
    "true_ordinary_no_switch",
    "selected_post_window_marker_off",
    "changed_route",
    "safety_retention",
    "ordinary_false_marker_records",
    "ordinary_false_marker_safety_context_subset",
    "total",
)
BUCKETS = frozenset({
    "paired_quartet",
    "true_ordinary_no_switch",
    "selected_post_window_marker_off",
    "changed_route",
    "safety_retention",
    "ordinary_false_marker",
})

ALLOWED_MODEL_INPUT_KEYS = frozenset({
    "camera_images",
    "image_sizes",
    "camera_intrinsics",
    "camera_extrinsics",
    "vehicle_speed",
    "target_point",
    "prompt",
    "prompt_inference",
    "route_switch_active",
})
FORBIDDEN_KEY_FRAGMENTS = frozenset({
    "road_id",
    "lane_id",
    "town_id",
    "map_id",
    "junction_id",
    "scenario_id",
    "route_id",
    "route_row",
    "world_coordinate",
    "world_x",
    "world_y",
    "world_z",
    "transaction_id",
    "generation_number",
    "mrc_el_identity",
    "clarification_case_id",
    "route_switch_event_id",
    "route_switch_forward_index",
    "pair_group_id",
    "false_marker_pair_id",
    "episode_id",
    "record_id",
    "source_file_sha256",
    "augmentation_parent_id",
    "image_phash64",
    "rgb_sha256",
    "trajectory_sha256",
    "target_sha256",
    "label_sha256",
    "exact_sensor_sha256",
})
FORBIDDEN_EXACT_KEYS = frozenset({
    "road",
    "lane",
    "town",
    "map",
    "junction",
    "scenario",
    "route",
    "coordinates",
    "world",
    "gps",
    "latitude",
    "longitude",
})
FORBIDDEN_LOSS_OR_LABEL_KEYS = frozenset({
    "verifier_status",
    "corridor_distance",
    "selected_act_eligibility",
    "driving_score",
    "adapted_model_output",
})
REQUIRED_RECORD_KEYS = frozenset({
    "record_id",
    "split",
    "source_file_sha256",
    "route_id",
    "episode_id",
    "frame_index",
    "rgb_sha256",
    "trajectory_sha256",
    "target_sha256",
    "label_sha256",
    "exact_sensor_sha256",
    "image_phash64",
    "bucket",
    "source_origin",
    "source_revision",
    "source_spdx_or_terms",
    "license_gate_passed",
    "collection_command",
    "simulator_version",
    "expert_version",
    "random_seed",
    "acceptance_reason",
    "model_inputs",
    "provenance",
})

SHA256_FIELDS = (
    "source_file_sha256",
    "rgb_sha256",
    "trajectory_sha256",
    "target_sha256",
    "label_sha256",
    "exact_sensor_sha256",
)

FORBIDDEN_STRING_PATTERN = re.compile(
    r"(?i)(town\s*\d+|road[_\s-]*id|lane[_\s-]*id|map[_\s-]*id|"
    r"junction[_\s-]*id|scenario[_\s-]*id|route[_\s-]*id|"
    r"transaction[_\s-]*id|generation[_\s-]*(number|id)|"
    r"world[_\s-]*(coordinate|x|y|z)|mrc[_\s-]*el|"
    r"route[_\s-]*switch[_\s-]*(event[_\s-]*id|forward[_\s-]*index)|"
    r"pair[_\s-]*group[_\s-]*id|false[_\s-]*marker[_\s-]*pair[_\s-]*id|"
    r"verifier[_\s-]*status|corridor[_\s-]*distance|"
    r"selected[_\s-]*act[_\s-]*eligibility|driving[_\s-]*score|"
    r"adapted[_\s-]*model[_\s-]*output|"
    r"(road|lane|route|junction|scenario)\s*#?\s*\d+|"
    r"\b(x|y|z)\s*=\s*[-+]?\d)"
)


def resolve_split_owner(source_file_sha256, route_id):
    if isinstance(route_id, bool) or not isinstance(route_id, int):
        raise ManifestValidationError("ROUTE_ID_INVALID")
    for digest, lower, upper, split in SPLIT_RULES:
        if source_file_sha256 == digest and lower <= route_id <= upper:
            return split
    raise ManifestValidationError("SOURCE_ROUTE_OUTSIDE_FROZEN_SPLIT")


def _walk_keys(value, path="model_inputs"):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            yield normalized, "{}.{}".format(path, key)
            yield from _walk_keys(child, "{}.{}".format(path, key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk_keys(child, "{}[{}]".format(path, index))


def _walk_strings(value, path="model_inputs"):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, "{}.{}".format(path, key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk_strings(child, "{}[{}]".format(path, index))
    elif isinstance(value, str):
        yield value, path


def _contains_forbidden_fragment(key, fragments):
    return key in FORBIDDEN_EXACT_KEYS or any(fragment in key for fragment in fragments)


def validate_model_inputs(model_inputs):
    if not isinstance(model_inputs, dict):
        raise ManifestValidationError("MODEL_INPUTS_NOT_OBJECT")
    if not set(model_inputs).issubset(ALLOWED_MODEL_INPUT_KEYS):
        raise ManifestValidationError("MODEL_INPUT_TOP_LEVEL_FIELD_NOT_FROZEN")
    marker = model_inputs.get("route_switch_active", False)
    if not isinstance(marker, bool):
        raise ManifestValidationError("ROUTE_SWITCH_ACTIVE_NOT_BOOLEAN")
    for key, path in _walk_keys(model_inputs):
        if _contains_forbidden_fragment(
                key, FORBIDDEN_KEY_FRAGMENTS | FORBIDDEN_LOSS_OR_LABEL_KEYS):
            raise ManifestValidationError("FORBIDDEN_MODEL_FEATURE_AT:" + path)
    for text, path in _walk_strings(model_inputs):
        if FORBIDDEN_STRING_PATTERN.search(text):
            raise ManifestValidationError("FORBIDDEN_MODEL_VALUE_AT:" + path)
    return True


def validate_record(record):
    if not isinstance(record, dict) or not REQUIRED_RECORD_KEYS.issubset(record):
        raise ManifestValidationError("RECORD_SCHEMA_MISSING_REQUIRED_FIELD")
    owner = resolve_split_owner(record["source_file_sha256"], record["route_id"])
    if record["split"] != owner:
        raise ManifestValidationError("SPLIT_INHERITANCE_MISMATCH")
    if isinstance(record["frame_index"], bool) or not isinstance(
            record["frame_index"], int):
        raise ManifestValidationError("FRAME_INDEX_INVALID")
    for field in SHA256_FIELDS:
        value = record[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ManifestValidationError("SHA256_INVALID:" + field)
    if (not isinstance(record["image_phash64"], str)
            or re.fullmatch(r"[0-9a-f]{16}", record["image_phash64"]) is None):
        raise ManifestValidationError("PHASH64_INVALID")
    for field in (
            "record_id", "episode_id", "bucket", "source_origin",
            "source_revision", "source_spdx_or_terms", "collection_command",
            "simulator_version", "expert_version", "acceptance_reason"):
        if not isinstance(record[field], str) or not record[field].strip():
            raise ManifestValidationError("SOURCE_PROVENANCE_FIELD_INVALID:" + field)
    if not os.path.isabs(record["source_origin"]):
        raise ManifestValidationError("SOURCE_ORIGIN_NOT_ABSOLUTE")
    if record["bucket"] not in BUCKETS:
        raise ManifestValidationError("UNKNOWN_PRIMARY_BUCKET")
    if record["license_gate_passed"] is not True:
        raise ManifestValidationError("SOURCE_LICENSE_GATE_NOT_PASSED")
    if isinstance(record["random_seed"], bool) or not isinstance(
            record["random_seed"], int):
        raise ManifestValidationError("RANDOM_SEED_INVALID")
    validate_model_inputs(record["model_inputs"])
    provenance = record["provenance"]
    if not isinstance(provenance, dict):
        raise ManifestValidationError("PROVENANCE_NOT_OBJECT")
    required_acceptance_flags = {
        "required_sensor_and_label_present": True,
        "expert_trajectory_finite": True,
        "target_label_frame_aligned": True,
        "expert_red_light_violation_before_horizon": False,
        "expert_collision_before_horizon": False,
    }
    if any(provenance.get(field) is not expected
           for field, expected in required_acceptance_flags.items()):
        raise ManifestValidationError("ACCEPTANCE_GATE_PROVENANCE_MISMATCH")
    if provenance.get("road_id") == 886 and record["split"] != "dev":
        raise ManifestValidationError("ROAD_886_DEV_ONLY_POLICY_VIOLATION")
    for key, path in _walk_keys(record.get("label_and_loss", {}), "label_and_loss"):
        if _contains_forbidden_fragment(key, FORBIDDEN_LOSS_OR_LABEL_KEYS):
            raise ManifestValidationError("FORBIDDEN_LABEL_OR_LOSS_FIELD_AT:" + path)
    for text, path in _walk_strings(
            record.get("label_and_loss", {}), "label_and_loss"):
        if FORBIDDEN_STRING_PATTERN.search(text):
            raise ManifestValidationError("FORBIDDEN_LABEL_OR_LOSS_VALUE_AT:" + path)
    marker = record["model_inputs"].get("route_switch_active", False)
    false_pair_id = record.get("false_marker_pair_id") or provenance.get(
        "false_marker_pair_id"
    )
    event_id = record.get("route_switch_event_id") or provenance.get(
        "route_switch_event_id"
    )
    if record["bucket"] in (
            "paired_quartet", "selected_post_window_marker_off", "changed_route"):
        if not isinstance(event_id, str) or not event_id.strip():
            raise ManifestValidationError("ROUTE_SWITCH_EVENT_ID_MISSING")
    if record["bucket"] == "ordinary_false_marker":
        if marker is not True or not false_pair_id:
            raise ManifestValidationError("FALSE_MARKER_RECORD_CONTRACT_MISMATCH")
    if record["bucket"] in (
            "true_ordinary_no_switch", "selected_post_window_marker_off"):
        if marker is not False:
            raise ManifestValidationError("MARKER_OFF_BUCKET_CONTRACT_MISMATCH")
    return True


def _expected_active_distribution(split):
    if split == "train":
        return {index: 500 for index in range(16)}
    if split in ("dev", "in_map_test"):
        return {index: 63 if index < 8 else 62 for index in range(16)}
    return {index: 125 for index in range(16)}


def _expected_post_window_distribution(split):
    if split == "train":
        return {index: 250 for index in range(16, 32)}
    if split in ("dev", "in_map_test"):
        return {index: 32 if index < 20 else 31 for index in range(16, 32)}
    return {index: 63 if index < 24 else 62 for index in range(16, 32)}


def _integer_keyed(distribution):
    try:
        return {int(key): value for key, value in distribution.items()}
    except (AttributeError, TypeError, ValueError) as error:
        raise ManifestValidationError("LIFECYCLE_DISTRIBUTION_INVALID") from error


def validate_quota_and_lifecycle_summaries(quota_summary, lifecycle_summary):
    if set(quota_summary) != set(QUOTAS) or set(lifecycle_summary) != set(QUOTAS):
        raise ManifestValidationError("SPLIT_SUMMARY_SET_MISMATCH")
    for split, expected_values in QUOTAS.items():
        summary = quota_summary[split]
        actual_values = tuple(summary.get(field) for field in QUOTA_FIELDS)
        if actual_values != expected_values:
            raise ManifestValidationError("QUOTA_MISMATCH:" + split)
        additive = (
            summary["paired_quartet"]
            + summary["true_ordinary_no_switch"]
            + summary["selected_post_window_marker_off"]
            + summary["changed_route"]
            + summary["safety_retention"]
            + summary["ordinary_false_marker_records"]
        )
        if additive != summary["total"]:
            raise ManifestValidationError("ADDITIVE_QUOTA_TOTAL_MISMATCH:" + split)
        if summary["ordinary_false_marker_safety_context_subset"] > summary[
                "ordinary_false_marker_records"]:
            raise ManifestValidationError("FALSE_MARKER_SUBQUOTA_INVALID:" + split)
        lifecycle = lifecycle_summary[split]
        if _integer_keyed(lifecycle.get("active_paired_groups", {})) != (
                _expected_active_distribution(split)):
            raise ManifestValidationError("ACTIVE_INDEX_DISTRIBUTION_MISMATCH:" + split)
        if _integer_keyed(lifecycle.get("post_window_marker_off_records", {})) != (
                _expected_post_window_distribution(split)):
            raise ManifestValidationError("POST_WINDOW_DISTRIBUTION_MISMATCH:" + split)
    return True


def validate_cross_split_leakage(records):
    ownership = defaultdict(set)
    episode_frames = defaultdict(list)
    phashes = []
    identity_fields = (
        "episode_id",
        "rgb_sha256",
        "trajectory_sha256",
        "pair_group_id",
        "false_marker_pair_id",
        "route_switch_event_id",
        "augmentation_parent_id",
        "exact_sensor_sha256",
    )
    for record in records:
        split = record["split"]
        ownership[("source_route", record["source_file_sha256"], record["route_id"])].add(split)
        for field in identity_fields:
            value = record.get(field) or record["provenance"].get(field)
            if value is not None:
                ownership[(field, value)].add(split)
        episode_frames[record["episode_id"]].append((record["frame_index"], split))
        phash = record.get("image_phash64") or record["provenance"].get("image_phash64")
        if phash is not None:
            try:
                phashes.append((int(phash, 16), split))
            except (TypeError, ValueError) as error:
                raise ManifestValidationError("PHASH64_INVALID") from error
    if any(len(splits) != 1 for splits in ownership.values()):
        raise ManifestValidationError("CROSS_SPLIT_IDENTITY_INTERSECTION")
    for frames in episode_frames.values():
        for index, (frame_a, split_a) in enumerate(frames):
            for frame_b, split_b in frames[index + 1:]:
                if abs(frame_a - frame_b) <= 64 and split_a != split_b:
                    raise ManifestValidationError("CROSS_SPLIT_FRAME_NEIGHBORHOOD")
    # BK-tree avoids an infeasible quadratic scan on the frozen 120k records.
    tree = None
    for phash, split in phashes:
        if tree is None:
            tree = [phash, {split}, {}]
            continue
        pending = [tree]
        while pending:
            node = pending.pop()
            distance = bin(phash ^ node[0]).count("1")
            if distance <= 2 and any(owner != split for owner in node[1]):
                raise ManifestValidationError("CROSS_SPLIT_NEAR_DUPLICATE_IMAGE")
            lower, upper = max(0, distance - 2), distance + 2
            pending.extend(
                child for edge, child in node[2].items()
                if lower <= edge <= upper
            )
        node = tree
        while True:
            distance = bin(phash ^ node[0]).count("1")
            if distance == 0:
                node[1].add(split)
                break
            child = node[2].get(distance)
            if child is None:
                node[2][distance] = [phash, {split}, {}]
                break
            node = child
    return True


def validate_pair_structures(records):
    quartets = defaultdict(list)
    false_marker_pairs = defaultdict(list)
    for record in records:
        if record["bucket"] == "paired_quartet":
            pair_group_id = record.get("pair_group_id") or record["provenance"].get(
                "pair_group_id"
            )
            if not pair_group_id:
                raise ManifestValidationError("PAIRED_QUARTET_GROUP_ID_MISSING")
            quartets[pair_group_id].append(record)
        false_marker_pair_id = record.get("false_marker_pair_id") or record[
            "provenance"
        ].get("false_marker_pair_id")
        if false_marker_pair_id:
            false_marker_pairs[false_marker_pair_id].append(record)
    required_names = {
        "P_old", "P_selected", "P_selected_no_marker", "P_old_false_marker"
    }
    for group in quartets.values():
        names = {record["provenance"].get("paired_record_name") for record in group}
        if len(group) != 4 or names != required_names:
            raise ManifestValidationError("PAIRED_QUARTET_STRUCTURE_MISMATCH")
        if len({record["split"] for record in group}) != 1:
            raise ManifestValidationError("PAIRED_QUARTET_SPLIT_MISMATCH")
        expected_markers = {
            "P_old": False,
            "P_selected": True,
            "P_selected_no_marker": False,
            "P_old_false_marker": True,
        }
        for record in group:
            name = record["provenance"]["paired_record_name"]
            if record["model_inputs"].get("route_switch_active", False) is not (
                    expected_markers[name]):
                raise ManifestValidationError("PAIRED_QUARTET_MARKER_MISMATCH")
        by_name = {
            record["provenance"]["paired_record_name"]: record
            for record in group
        }
        for first, second in (
                ("P_selected", "P_selected_no_marker"),
                ("P_old", "P_old_false_marker")):
            for field in (
                    "rgb_sha256", "target_sha256", "label_sha256",
                    "exact_sensor_sha256", "trajectory_sha256"):
                first_value = by_name[first].get(field) or by_name[first][
                    "provenance"
                ].get(field)
                second_value = by_name[second].get(field) or by_name[second][
                    "provenance"
                ].get(field)
                if first_value is None or first_value != second_value:
                    raise ManifestValidationError(
                        "PAIRED_QUARTET_EXACT_TWIN_MISMATCH:" + field
                    )
        for field in ("target_sha256", "label_sha256", "trajectory_sha256"):
            if by_name["P_old"][field] == by_name["P_selected"][field]:
                raise ManifestValidationError(
                    "PAIRED_QUARTET_BRANCH_NOT_COUNTERFACTUAL:" + field
                )
        event_ids = {
            record.get("route_switch_event_id")
            or record["provenance"].get("route_switch_event_id")
            for record in group
        }
        if len(event_ids) != 1 or None in event_ids:
            raise ManifestValidationError("PAIRED_QUARTET_EVENT_ID_MISMATCH")
        if len({record["episode_id"] for record in group}) != 1:
            raise ManifestValidationError("PAIRED_QUARTET_EPISODE_MISMATCH")
        if max(record["frame_index"] for record in group) - min(
                record["frame_index"] for record in group) > 1:
            raise ManifestValidationError("PAIRED_QUARTET_TICK_DELTA_EXCEEDED")
        for record in group:
            if record["provenance"].get("branch_expert_valid") is not True:
                raise ManifestValidationError("PAIR_BRANCH_NOT_EXPERT_VALID")
            if record["provenance"].get(
                    "red_light_violation_before_horizon") is not False:
                raise ManifestValidationError("PAIR_RED_LIGHT_VIOLATION")
            if record["provenance"].get(
                    "collision_before_horizon") is not False:
                raise ManifestValidationError("PAIR_COLLISION_BEFORE_HORIZON")
        if (len({record["rgb_sha256"] for record in group}) > 1
                or len({record["exact_sensor_sha256"] for record in group}) > 1):
            near_same_values = {
                "translation_m": 0.25,
                "yaw_deg": 1.0,
                "speed_delta_mps": 0.25,
            }
            normalized_receipts = set()
            for record in group:
                near_same = record["provenance"].get("near_same_observation")
                if not isinstance(near_same, dict):
                    raise ManifestValidationError("NEAR_SAME_RECEIPT_MISSING")
                for field, maximum in near_same_values.items():
                    value = near_same.get(field)
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ManifestValidationError("NEAR_SAME_VALUE_INVALID:" + field)
                    if not math.isfinite(value):
                        raise ManifestValidationError("NEAR_SAME_VALUE_NOT_FINITE:" + field)
                    if value < 0 or value > maximum:
                        raise ManifestValidationError("NEAR_SAME_LIMIT_EXCEEDED:" + field)
                if near_same.get("visible_actor_set_unchanged") is not True:
                    raise ManifestValidationError("NEAR_SAME_ACTOR_SET_CHANGED")
                if near_same.get("traffic_control_state_unchanged") is not True:
                    raise ManifestValidationError("NEAR_SAME_TRAFFIC_STATE_CHANGED")
                normalized_receipts.add(tuple(
                    near_same.get(field) for field in (
                        "translation_m", "yaw_deg", "speed_delta_mps",
                        "visible_actor_set_unchanged",
                        "traffic_control_state_unchanged",
                    )
                ))
            if len(normalized_receipts) != 1:
                raise ManifestValidationError("NEAR_SAME_RECEIPT_DISAGREEMENT")
    for pair in false_marker_pairs.values():
        if len(pair) != 2:
            raise ManifestValidationError("FALSE_MARKER_TWIN_COUNT_MISMATCH")
        marker_values = {record["model_inputs"].get("route_switch_active", False)
                         for record in pair}
        if marker_values != {False, True}:
            raise ManifestValidationError("FALSE_MARKER_TWIN_MARKERS_MISMATCH")
        if {record["bucket"] for record in pair} != {
                "ordinary_false_marker", "true_ordinary_no_switch"}:
            raise ManifestValidationError("FALSE_MARKER_TWIN_BUCKETS_MISMATCH")
        for field in ("exact_sensor_sha256", "target_sha256", "label_sha256"):
            values = {
                record.get(field) or record["provenance"].get(field)
                for record in pair
            }
            if len(values) != 1 or None in values:
                raise ManifestValidationError("FALSE_MARKER_TWIN_CONTENT_MISMATCH")
        if {record["split"] for record in pair}.__len__() != 1:
            raise ManifestValidationError("FALSE_MARKER_TWIN_SPLIT_MISMATCH")
    return True


def validate_lifecycle_record_provenance(
        records, lifecycle_summary, require_complete=False):
    active_groups = defaultdict(set)
    post_records = defaultdict(int)
    group_indices = defaultdict(set)
    for record in records:
        index = record["provenance"].get("route_switch_forward_index")
        if record["bucket"] == "paired_quartet":
            if isinstance(index, bool) or not isinstance(index, int) or index not in range(16):
                raise ManifestValidationError("ACTIVE_FORWARD_INDEX_INVALID")
            group_id = record.get("pair_group_id") or record["provenance"].get(
                "pair_group_id"
            )
            group_indices[group_id].add(index)
            active_groups[(record["split"], index)].add(group_id)
        elif record["bucket"] == "selected_post_window_marker_off":
            if isinstance(index, bool) or not isinstance(index, int) or index not in range(16, 32):
                raise ManifestValidationError("POST_WINDOW_FORWARD_INDEX_INVALID")
            if record["model_inputs"].get("route_switch_active", False) is not False:
                raise ManifestValidationError("POST_WINDOW_MARKER_MUST_BE_FALSE")
            post_records[(record["split"], index)] += 1
    if any(len(indices) != 1 for indices in group_indices.values()):
        raise ManifestValidationError("PAIRED_GROUP_FORWARD_INDEX_MISMATCH")
    if require_complete:
        for split in QUOTAS:
            active_actual = {
                index: len(active_groups[(split, index)]) for index in range(16)
            }
            post_actual = {
                index: post_records[(split, index)] for index in range(16, 32)
            }
            lifecycle = lifecycle_summary[split]
            if active_actual != _integer_keyed(lifecycle["active_paired_groups"]):
                raise ManifestValidationError("ACTIVE_RECORD_DISTRIBUTION_MISMATCH:" + split)
            if post_actual != _integer_keyed(
                    lifecycle["post_window_marker_off_records"]):
                raise ManifestValidationError("POST_RECORD_DISTRIBUTION_MISMATCH:" + split)
    return True


def validate_complete_record_counts(records, quota_summary):
    if len(records) != sum(values[-1] for values in QUOTAS.values()):
        raise ManifestValidationError("COMPLETE_MANIFEST_RECORD_COUNT_MISMATCH")
    by_split_bucket = defaultdict(int)
    for record in records:
        by_split_bucket[(record["split"], record["bucket"])] += 1
    for split in QUOTAS:
        summary = quota_summary[split]
        for field in (
                "paired_quartet", "true_ordinary_no_switch",
                "selected_post_window_marker_off", "changed_route",
                "safety_retention"):
            if by_split_bucket[(split, field)] != summary[field]:
                raise ManifestValidationError("RECORD_BUCKET_COUNT_MISMATCH:" + split)
        if by_split_bucket[(split, "ordinary_false_marker")] != summary[
                "ordinary_false_marker_records"]:
            raise ManifestValidationError("FALSE_MARKER_RECORD_COUNT_MISMATCH:" + split)
        safety_context_count = sum(
            1 for record in records
            if record["split"] == split
            and record["bucket"] == "ordinary_false_marker"
            and record["provenance"].get("safety_context") is True
        )
        if safety_context_count != summary[
                "ordinary_false_marker_safety_context_subset"]:
            raise ManifestValidationError("FALSE_MARKER_SUBQUOTA_COUNT_MISMATCH:" + split)
    return True


def canonical_sha256(value):
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_manifest_sha256(manifest):
    value = dict(manifest)
    publication = dict(value.get("publication_receipts", {}))
    publication.pop("manifest_content_sha256", None)
    value["publication_receipts"] = publication
    return canonical_sha256(value)


def attach_publication_receipts(
        manifest, rejection_counts_by_reason, shard_receipts=()):
    """Attach deterministic metadata receipts; does not create dataset data."""
    rejection_counts = dict(rejection_counts_by_reason)
    rejection_ledger = {
        "counts_by_reason": rejection_counts,
        "total_rejected": sum(rejection_counts.values()),
    }
    rejection_ledger["sha256"] = canonical_sha256(rejection_ledger)
    publication = {
        "rejection_ledger": rejection_ledger,
        "bucket_count_receipt_sha256": canonical_sha256(
            manifest.get("quota_summary", {})
        ),
        "shards": [dict(receipt) for receipt in shard_receipts],
    }
    manifest["publication_receipts"] = publication
    publication["manifest_content_sha256"] = canonical_manifest_sha256(manifest)
    return manifest


def validate_publication_receipts(manifest, synthetic_fixture=False):
    publication = manifest.get("publication_receipts")
    if not isinstance(publication, dict):
        raise ManifestValidationError("PUBLICATION_RECEIPTS_MISSING")
    ledger = publication.get("rejection_ledger")
    if not isinstance(ledger, dict) or not isinstance(
            ledger.get("counts_by_reason"), dict):
        raise ManifestValidationError("REJECTION_LEDGER_INVALID")
    counts = ledger["counts_by_reason"]
    if any(not isinstance(reason, str) or not reason.strip()
           or isinstance(count, bool) or not isinstance(count, int) or count < 0
           for reason, count in counts.items()):
        raise ManifestValidationError("REJECTION_REASON_COUNT_INVALID")
    if ledger.get("total_rejected") != sum(counts.values()):
        raise ManifestValidationError("REJECTION_LEDGER_TOTAL_MISMATCH")
    ledger_without_hash = dict(ledger)
    ledger_hash = ledger_without_hash.pop("sha256", None)
    if ledger_hash != canonical_sha256(ledger_without_hash):
        raise ManifestValidationError("REJECTION_LEDGER_HASH_MISMATCH")
    if publication.get("bucket_count_receipt_sha256") != canonical_sha256(
            manifest.get("quota_summary", {})):
        raise ManifestValidationError("BUCKET_COUNT_RECEIPT_HASH_MISMATCH")
    shards = publication.get("shards")
    if not isinstance(shards, list) or (not synthetic_fixture and not shards):
        raise ManifestValidationError("SHARD_RECEIPTS_INVALID")
    for receipt in shards:
        if not isinstance(receipt, dict) or set(receipt) != {"path", "sha256"}:
            raise ManifestValidationError("SHARD_RECEIPT_SCHEMA_INVALID")
        path, expected = receipt["path"], receipt["sha256"]
        if not isinstance(path, str) or not os.path.isabs(path):
            raise ManifestValidationError("SHARD_PATH_NOT_ABSOLUTE")
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ManifestValidationError("SHARD_SHA256_INVALID")
        if not synthetic_fixture:
            try:
                digest = hashlib.sha256()
                with open(path, "rb") as handle:
                    while True:
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                actual = digest.hexdigest()
            except OSError as error:
                raise ManifestValidationError("SHARD_FILE_UNREADABLE") from error
            if actual != expected:
                raise ManifestValidationError("SHARD_FILE_HASH_MISMATCH")
    if publication.get("manifest_content_sha256") != canonical_manifest_sha256(
            manifest):
        raise ManifestValidationError("MANIFEST_CONTENT_HASH_MISMATCH")
    return True


def validate_manifest(manifest, synthetic_fixture=False):
    if not isinstance(manifest, dict):
        raise ManifestValidationError("MANIFEST_NOT_OBJECT")
    if manifest.get("schema_version") != "driveclarify.v3.offline_manifest.v1":
        raise ManifestValidationError("MANIFEST_SCHEMA_VERSION_MISMATCH")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ManifestValidationError("MANIFEST_RECORDS_NOT_LIST")
    for record in records:
        validate_record(record)
    validate_cross_split_leakage(records)
    validate_pair_structures(records)
    validate_lifecycle_record_provenance(
        records, manifest.get("lifecycle_summary", {}),
        require_complete=not synthetic_fixture,
    )
    validate_quota_and_lifecycle_summaries(
        manifest.get("quota_summary", {}),
        manifest.get("lifecycle_summary", {}),
    )
    if manifest.get("generation_runs") != 0:
        raise ManifestValidationError("DATA_GENERATION_NOT_AUTHORIZED")
    validate_publication_receipts(manifest, synthetic_fixture=synthetic_fixture)
    if not synthetic_fixture:
        validate_complete_record_counts(records, manifest["quota_summary"])
    return True
