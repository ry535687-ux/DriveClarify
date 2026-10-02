"""Closed CPU-resolvable bindings for the six frozen T-MVP baselines."""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any, Mapping

from driveclarify_t_mvp.baselines import (
    AlwaysFullReplanBaseline,
    DriveClarifyTransitionBaseline,
    FinishOldFirstBaseline,
    HistoryOnlyBaseline,
    InstantOverwriteBaseline,
    LocalReplanOnlyBaseline,
)
from driveclarify_t_mvp.firewall import BASELINE_ALLOWED_FIELDS, BaselineId
from driveclarify_t_mvp.frozen_registry import (
    FROZEN_V11_ADMISSIBILITY_OWNER,
    FROZEN_V11_ADMISSIBILITY_SOURCE,
    FROZEN_V11_ADMISSIBILITY_SOURCE_SHA256,
    FROZEN_V11_ADMISSIBILITY_THRESHOLDS,
    assert_frozen_v11_admissibility_owner,
)


BASELINE_BINDINGS: Mapping[BaselineId, type[Any]] = {
    BaselineId.T_B1: InstantOverwriteBaseline,
    BaselineId.T_B2: AlwaysFullReplanBaseline,
    BaselineId.T_B3: FinishOldFirstBaseline,
    BaselineId.T_B4: LocalReplanOnlyBaseline,
    BaselineId.T_B5: HistoryOnlyBaseline,
    BaselineId.T_B6: DriveClarifyTransitionBaseline,
}


EXPECTED_QUALNAMES: Mapping[BaselineId, str] = {
    BaselineId.T_B1: "driveclarify_t_mvp.baselines.InstantOverwriteBaseline",
    BaselineId.T_B2: "driveclarify_t_mvp.baselines.AlwaysFullReplanBaseline",
    BaselineId.T_B3: "driveclarify_t_mvp.baselines.FinishOldFirstBaseline",
    BaselineId.T_B4: "driveclarify_t_mvp.baselines.LocalReplanOnlyBaseline",
    BaselineId.T_B5: "driveclarify_t_mvp.baselines.HistoryOnlyBaseline",
    BaselineId.T_B6: "driveclarify_t_mvp.baselines.DriveClarifyTransitionBaseline",
}


FORBIDDEN_INFORMATION: Mapping[BaselineId, tuple[str, ...]] = {
    BaselineId.T_B1: ("observable_commitment", "oracle_commitment", "oracle_bucket"),
    BaselineId.T_B2: ("p_old", "p_new", "observable_commitment", "oracle_commitment", "oracle_bucket"),
    BaselineId.T_B3: ("observable_commitment", "oracle_commitment", "oracle_bucket", "rescue_search"),
    BaselineId.T_B4: ("global_task_G", "global_planner", "global_reconnect", "oracle_commitment", "oracle_bucket"),
    BaselineId.T_B5: ("p_old", "p_new", "observable_commitment", "transition_state", "route", "oracle_commitment", "oracle_bucket"),
    BaselineId.T_B6: ("oracle_commitment", "oracle_bucket", "commitment_point_index", "authored_future_trajectory"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_baseline_class(selector: BaselineId | str) -> type[Any]:
    baseline = BaselineId(selector)
    if set(BASELINE_BINDINGS) != set(BaselineId):
        raise RuntimeError("SIX_BASELINE_BINDING_SET_INCOMPLETE")
    resolved = BASELINE_BINDINGS[baseline]
    identity = resolved.__module__ + "." + resolved.__qualname__
    if identity != EXPECTED_QUALNAMES[baseline]:
        raise RuntimeError("BASELINE_BINDING_IDENTITY_DRIFT:" + baseline.value)
    if getattr(resolved, "baseline_id", None) is not baseline:
        raise RuntimeError("BASELINE_CLASS_SELECTOR_MISMATCH:" + baseline.value)
    if baseline is BaselineId.T_B1 and resolved is DriveClarifyTransitionBaseline:
        raise RuntimeError("T_B1_RESOLVED_TO_T_B6")
    if baseline is BaselineId.T_B6:
        assert_frozen_v11_admissibility_owner()
    return resolved


def resolve_all_bindings() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source = Path(inspect.getsourcefile(InstantOverwriteBaseline) or "").resolve()
    source_hash = _sha256(source)
    for baseline in BaselineId:
        resolved = resolve_baseline_class(baseline)
        row: dict[str, Any] = {
            "baseline_id": baseline.value,
            "config_selector": baseline.value,
            "factory_registry_selector": "BASELINE_BINDINGS[BaselineId('" + baseline.value + "')]",
            "resolved_module": resolved.__module__,
            "resolved_class_or_function": resolved.__qualname__,
            "source_path": str(source),
            "source_sha256": source_hash,
            "policy_input_schema": sorted(BASELINE_ALLOWED_FIELDS[baseline]),
            "allowed_information_schema": sorted(BASELINE_ALLOWED_FIELDS[baseline]),
            "forbidden_information_schema": list(FORBIDDEN_INFORMATION[baseline]),
            "planner_binding": "BASELINE_DEFINED; no wrapper-hidden planner",
            "controller_binding": "team_code.agent_simlingo.LingoAgent.control_pid",
            "hard_safety_binding": "shared leaderboard RouteScenario official criteria; no baseline override",
            "resolved": True,
        }
        if baseline is BaselineId.T_B1:
            row.update(
                {
                    "semantic_identity": "INSTANT_OVERWRITE",
                    "commitment_access": "FORBIDDEN",
                    "oracle_access": "FORBIDDEN",
                    "cannot_resolve_to_t_b6": True,
                }
            )
        elif baseline is BaselineId.T_B2:
            row["semantic_identity"] = "ALWAYS_FULL_REPLAN_GLOBAL_PLUS_LOCAL_PREPARE_BEFORE_INSTALL"
        elif baseline is BaselineId.T_B3:
            source_text = inspect.getsource(resolved)
            row.update(
                {
                    "semantic_identity": "FINISH_OLD_FIRST",
                    "no_rescue_search_after_expiry": "MISSED_CURRENT_OPPORTUNITY_NO_RESCUE_SEARCH" in source_text,
                }
            )
        elif baseline is BaselineId.T_B4:
            source_text = inspect.getsource(resolved)
            row.update(
                {
                    "semantic_identity": "LOCAL_REPLAN_ONLY",
                    "global_planner_disabled_for_transition": "T_B4_GLOBAL_PLANNER_CALL_FORBIDDEN" in source_text,
                    "global_reconnect_disabled_for_transition": "T_B4_GLOBAL_RECONNECT_CALL_FORBIDDEN" in source_text,
                    "normal_production_defaults_changed": False,
                }
            )
        elif baseline is BaselineId.T_B5:
            row["semantic_identity"] = "HISTORY_ONLY_FROZEN_VLA"
        else:
            row.update(
                {
                    "semantic_identity": "DRIVECLARIFY_FROZEN_V11_TRANSITION",
                    "owner_type": FROZEN_V11_ADMISSIBILITY_OWNER,
                    "owner_source_path": str(FROZEN_V11_ADMISSIBILITY_SOURCE),
                    "owner_source_sha256": FROZEN_V11_ADMISSIBILITY_SOURCE_SHA256,
                    "configuration": "ReplanThresholds() exact defaults",
                    "default_thresholds": dict(FROZEN_V11_ADMISSIBILITY_THRESHOLDS),
                    "patched_stronger_b6_selected": False,
                    "alternative_thresholds_selected": False,
                    "oracle_commitment_selected": False,
                    "authored_commitment_point_index_selected": False,
                }
            )
        rows.append(row)
    return rows
