"""Closed baseline input schemas and recursive oracle/future-data firewall."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import json
import re
from typing import Any, Mapping

from .canonical import canonical_sha256


class FirewallViolation(ValueError):
    pass


class BaselineId(str, Enum):
    T_B1 = "T-B1"
    T_B2 = "T-B2"
    T_B3 = "T-B3"
    T_B4 = "T-B4"
    T_B5 = "T-B5"
    T_B6 = "T-B6"


def _key_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


ORACLE_OR_FUTURE_KEYS = frozenset(
    {
        "oracle_commitment",
        "oracle_bucket",
        "oracle_event",
        "oracle_event_type",
        "oracle_region",
        "oracle_feasibility",
        "oracle_timing",
        "oracle_g_relationship",
        "commitment_point_index",
        "t_bucket",
        "timing_bucket",
        "bucket",
        "future_branch_identity",
        "future_recovery_path",
        "future_route",
        "future_route_identity",
        "future_outcome",
        "future_collision",
        "future_planner_outcome",
        "ground_truth_future_ego_path",
        "expected_action",
        "expected_decision",
        "true_outcome",
        "true_intent",
    }
)


def _walk(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if is_dataclass(value):
        value = asdict(value)
    hits: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            token = _key_token(str(key))
            if token in ORACLE_OR_FUTURE_KEYS or token.startswith("oracle_") or token.startswith("future_"):
                hits.append((path + "." + str(key), token))
            hits.extend(_walk(item, path + "." + str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            hits.extend(_walk(item, f"{path}[{index}]"))
    return hits


def assert_no_oracle_fields(value: Any) -> None:
    hits = _walk(value)
    if hits:
        raise FirewallViolation(
            "ORACLE_OR_FUTURE_FIELD_FORBIDDEN:"
            + ",".join(path for path, _ in hits)
        )


BASELINE_ALLOWED_FIELDS: Mapping[BaselineId, frozenset[str]] = {
    BaselineId.T_B1: frozenset(
        {"old_instruction", "new_instruction", "p_new", "planning_boundary"}
    ),
    BaselineId.T_B2: frozenset(
        {
            "old_instruction",
            "new_instruction",
            "ego_state",
            "global_task_G",
            "updated_obligation",
        }
    ),
    BaselineId.T_B3: frozenset(
        {
            "old_instruction",
            "new_instruction",
            "p_old",
            "p_new",
            "old_terminal_state",
            "current_opportunity_available",
        }
    ),
    BaselineId.T_B4: frozenset(
        {
            "old_instruction",
            "new_instruction",
            "ego_state",
            "active_local_suffix",
            "local_navigation_candidate",
        }
    ),
    BaselineId.T_B5: frozenset(
        {"old_instruction", "new_instruction", "interaction_history"}
    ),
    BaselineId.T_B6: frozenset(
        {
            "old_instruction",
            "new_instruction",
            "p_old",
            "p_new",
            "ego_state",
            "observable_commitment",
            "global_task_G",
            "admissibility",
            "transition_state",
        }
    ),
}


def build_policy_input(
    baseline_id: BaselineId | str,
    supplied: Mapping[str, Any],
) -> Mapping[str, Any]:
    baseline = BaselineId(baseline_id)
    assert_no_oracle_fields(supplied)
    allowed = BASELINE_ALLOWED_FIELDS[baseline]
    forbidden = set(supplied) - allowed
    if forbidden:
        raise FirewallViolation(
            baseline.value + "_POLICY_FIELD_FORBIDDEN:" + ",".join(sorted(forbidden))
        )
    # A JSON round trip yields a detached policy object and prevents access to
    # caller-owned context objects or globals through aliases.
    detached = json.loads(json.dumps(supplied, default=_json_default))
    assert_no_oracle_fields(detached)
    return detached


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    raise TypeError(type(value).__qualname__ + "_NOT_POLICY_SERIALIZABLE")


def access_matrix_receipt() -> Mapping[str, Any]:
    matrix = {
        baseline.value: sorted(fields)
        for baseline, fields in BASELINE_ALLOWED_FIELDS.items()
    }
    return {
        "schema_version": "driveclarify.rq2.baseline_access_matrix.v1",
        "default_access": "FORBIDDEN_UNLESS_EXPLICITLY_ALLOWED",
        "allowed_fields": matrix,
        "oracle_and_future_keys": sorted(ORACLE_OR_FUTURE_KEYS),
        "canonical_sha256": canonical_sha256(matrix),
    }
