"""Exact formal-child construction and fail-closed first-row admission for V2."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from driveclarify_paper_mvp_stage6b import backend as native
from driveclarify_rq2_t.measurement import canonical_sha256

from .scene_io import SCENE_MANIFEST_ENV
from driveclarify_rq2_t_cg_background_traffic import POLICY_ID


FIRST_ROW_WALL_TIMEOUT_SECONDS = 30.0
RELEVANT_ENVIRONMENT_KEYS = (
    "DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE", "XDG_SESSION_REMOTE",
    "__NV_PRIME_RENDER_OFFLOAD", "__GLX_VENDOR_LIBRARY_NAME",
    "CARLA_ROOT", "LEADERBOARD_ROOT", "SCENARIO_RUNNER_ROOT", "PYTHONPATH",
    "ROUTES", "TEAM_AGENT", "TEAM_CONFIG", "CHECKPOINT_ENDPOINT", "SAVE_PATH",
    "IS_BENCH2DRIVE", "CUDA_VISIBLE_DEVICES", "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE", "TOKENIZERS_PARALLELISM", "PYTHONUNBUFFERED",
)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def build_exact_formal_child_environment(
    spec: native.EpisodeSpec,
    output: Path,
    cell: Mapping[str, Any],
) -> Mapping[str, str]:
    """Build the one environment used by admission, engineering, and formal children."""

    values = native.build_environment(spec, output, visualization=False)
    for key in tuple(values):
        upper = key.upper()
        if key.startswith("DRIVECLARIFY_") and key not in (
            "DRIVECLARIFY_PAPER_MVP_STAGE6B_METHOD_ID",
        ):
            values.pop(key, None)
        if upper.endswith("_GOLD") or any(
            token in upper
            for token in ("TRUE_INTENT", "CORRECT_ANSWER", "EXPECTED_METHOD_RESULT")
        ):
            values.pop(key, None)
    values.update({
        "SCENARIO_RUNNER_ROOT": str(native.SCENARIO_DISCOVERY_ROOT),
        "DRIVECLARIFY_PROBE_ENABLED": "1",
        "DRIVECLARIFY_PROBE_OUTPUT": str(output / "probe" / "probe.jsonl"),
        "DRIVECLARIFY_PROBE_EQUIVALENCE": str(output / "probe" / "PROBE_EQUIVALENCE.json"),
        "DRIVECLARIFY_PROBE_RUN_ID": str(cell["cell_id"]),
        "DRIVECLARIFY_WORLDSTATE_OUTPUT": str(output / "post_hoc_world_state.jsonl"),
        "DRIVECLARIFY_ROUTE_BINDING_RUNTIME_RECEIPT": str(
            output / "AGENT_ROUTE_BINDING_RUNTIME_RECEIPT.json"
        ),
        "DRIVECLARIFY_RQ2_T_CG_FORMAL_SCENARIO_RECEIPT": str(
            output / "FORMAL_SCENARIO_RECEIPT.json"
        ),
        "DRIVECLARIFY_RQ2_T_CG_FORMAL_ACTIVATION_RECEIPT": str(
            output / "FORMAL_ACTIVATION_RECEIPT.json"
        ),
        "DRIVECLARIFY_RQ2_T_CG_FORMAL_COMMITMENT_RECEIPT": str(
            output / "FORMAL_COMMITMENT_RECEIPT.json"
        ),
        "DRIVECLARIFY_RQ2_T_CG_FORMAL_CELL_ID": str(cell["cell_id"]),
        "DRIVECLARIFY_RQ2_T_CG_BACKGROUND_TRAFFIC_POLICY": POLICY_ID,
        "DRIVECLARIFY_RQ2_T_CG_BACKGROUND_TRAFFIC_RUNTIME_RECEIPT": str(
            output / "BACKGROUND_TRAFFIC_RUNTIME_RECEIPT.json"
        ),
        "DRIVECLARIFY_RQ2_T_CG_CONTROLLED_INTERFACE_RUNTIME": "0",
        "DRIVECLARIFY_RQ2_T_CG_ENGINEERING_QUALIFICATION": (
            "1" if cell.get("engineering_qualification") is True else "0"
        ),
        "DRIVECLARIFY_RQ2_T_2A_OWNER_ENABLED": "0",
        "DRIVECLARIFY_RQ2_T_E2_V4_NATIVE_EVIDENCE": "0",
        "DRIVECLARIFY_RQ2_T_E2_V3_NATIVE_EVIDENCE": "0",
        "DRIVECLARIFY_RQ2_T_V2_NATIVE_EVIDENCE": "0",
    })
    if cell.get("execution_scene_manifest"):
        values[SCENE_MANIFEST_ENV] = str(cell["execution_scene_manifest"])
    return values


def expanded_relevant_environment(environment: Mapping[str, str]) -> Mapping[str, str]:
    keys = set(RELEVANT_ENVIRONMENT_KEYS)
    keys.update(key for key in environment if key.startswith("DRIVECLARIFY_"))
    return {key: str(environment[key]) for key in sorted(keys) if key in environment}


def persist_child_construction_receipt(
    path: Path,
    *,
    command: tuple[str, ...],
    environment: Mapping[str, str],
    cell: Mapping[str, Any],
) -> Mapping[str, Any]:
    relevant = expanded_relevant_environment(environment)
    value = {
        "schema_version": "driveclarify.rq2_t_cg.formal_child_construction.v2",
        "cell_id": cell["cell_id"],
        "engineering_qualification": cell.get("engineering_qualification") is True,
        "exact_child_command": list(command),
        "fully_expanded_relevant_child_environment": relevant,
        "child_environment_digest": canonical_sha256(relevant),
        "probe_enabled": relevant.get("DRIVECLARIFY_PROBE_ENABLED") == "1",
        "worldstate_output": relevant.get("DRIVECLARIFY_WORLDSTATE_OUTPUT"),
        "probe_output": relevant.get("DRIVECLARIFY_PROBE_OUTPUT"),
        "probe_equivalence_output": relevant.get("DRIVECLARIFY_PROBE_EQUIVALENCE"),
        "runtime_route_receipt_output": relevant.get(
            "DRIVECLARIFY_ROUTE_BINDING_RUNTIME_RECEIPT"
        ),
        "background_traffic_policy": relevant.get(
            "DRIVECLARIFY_RQ2_T_CG_BACKGROUND_TRAFFIC_POLICY"
        ),
        "background_traffic_runtime_receipt_output": relevant.get(
            "DRIVECLARIFY_RQ2_T_CG_BACKGROUND_TRAFFIC_RUNTIME_RECEIPT"
        ),
        "random_background_traffic_disabled": relevant.get(
            "DRIVECLARIFY_RQ2_T_CG_BACKGROUND_TRAFFIC_POLICY"
        ) == POLICY_ID,
        "true_intent_environment_keys": [
            key for key in relevant
            if any(token in key.upper() for token in ("TRUE_INTENT", "CORRECT_ANSWER", "_GOLD"))
        ],
    }
    value["construction_digest"] = canonical_sha256(value)
    _atomic_json(path, value)
    return value


def validate_first_source_row(row: Mapping[str, Any], activation: Mapping[str, Any]) -> list[str]:
    missing = [
        key for key in (
            "schema_version", "run_id", "observation_id", "carla_snapshot_frame",
            "gametime_frame", "gametime_seconds", "ego", "route_context",
        )
        if key not in row
    ]
    reasons = ["MISSING_FIRST_ROW_FIELD:" + key for key in missing]
    if row.get("schema_version") != "driveclarify.world_state.v1":
        reasons.append("FIRST_ROW_SCHEMA_VERSION_MISMATCH")
    if str(row.get("run_id")) != str(activation.get("cell_id")):
        reasons.append("FIRST_ROW_RUN_ID_MISMATCH")
    try:
        if int(row["carla_snapshot_frame"]) <= int(activation["simulator_frame"]):
            reasons.append("FIRST_ROW_NOT_AFTER_ACTIVATION")
        if int(row["gametime_frame"]) != int(row["carla_snapshot_frame"]):
            reasons.append("FIRST_ROW_FRAME_IDENTITY_MISMATCH")
    except (KeyError, TypeError, ValueError):
        reasons.append("FIRST_ROW_FRAME_IDENTITY_INVALID")
    return list(dict.fromkeys(reasons))


class FirstLegalRowWatchdog:
    """Poll observer that stops the exact child before scientific continuation."""

    def __init__(self, output: Path, timeout_seconds: float = FIRST_ROW_WALL_TIMEOUT_SECONDS):
        self.output = Path(output)
        self.timeout_seconds = float(timeout_seconds)
        self.activation_seen_monotonic: float | None = None
        self.admitted = False
        self.failure_reason: str | None = None
        self.receipt: Mapping[str, Any] | None = None

    def _read_first_complete_line(self) -> bytes | None:
        path = self.output / "post_hoc_world_state.jsonl"
        if not path.is_file():
            return None
        data = path.read_bytes()
        if b"\n" not in data:
            return None
        line = data.split(b"\n", 1)[0]
        return line if line.strip() else None

    def __call__(self, runtime: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.admitted or self.failure_reason is not None:
            return {}
        now = float(runtime["now_monotonic"])
        activation_path = self.output / "FORMAL_ACTIVATION_RECEIPT.json"
        if not activation_path.is_file():
            return {}
        try:
            activation = json.loads(activation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if self.activation_seen_monotonic is None:
            self.activation_seen_monotonic = now
        raw = self._read_first_complete_line()
        if raw is not None:
            try:
                row = json.loads(raw.decode("utf-8"))
                reasons = validate_first_source_row(row, activation)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                row = None
                reasons = ["FIRST_ROW_JSON_INVALID:" + type(exc).__name__]
            value = {
                "schema_version": "driveclarify.rq2_t_cg.first_legal_source_row.v2",
                "status": "PASS_FIRST_LEGAL_SOURCE_ROW" if not reasons else "FAIL_FIRST_LEGAL_SOURCE_ROW",
                "cell_id": activation.get("cell_id"),
                "activation_receipt": activation,
                "first_row_schema_validation_pass": not reasons,
                "reason_codes": reasons,
                "first_row_source_frame_identity": None if row is None else {
                    "observation_id": row.get("observation_id"),
                    "carla_snapshot_frame": row.get("carla_snapshot_frame"),
                    "gametime_frame": row.get("gametime_frame"),
                    "gametime_seconds": row.get("gametime_seconds"),
                },
                "first_row_sha256": hashlib.sha256(raw).hexdigest(),
                "first_row_canonical_digest": None if row is None else canonical_sha256(row),
                "watchdog_timeout_seconds": self.timeout_seconds,
            }
            value["receipt_digest"] = canonical_sha256(value)
            _atomic_json(self.output / "FIRST_LEGAL_SOURCE_ROW_RECEIPT.json", value)
            self.receipt = value
            if reasons:
                self.failure_reason = "FIRST_LEGAL_RECORDER_ROW_INVALID_FAIL_CLOSED"
                return {"request_stop": True, "termination_reason": self.failure_reason, "signal": "SIGINT"}
            self.admitted = True
            return {}
        if now - self.activation_seen_monotonic >= self.timeout_seconds:
            self.failure_reason = "FIRST_LEGAL_RECORDER_ROW_ABSENT_FAIL_CLOSED"
            value = {
                "schema_version": "driveclarify.rq2_t_cg.first_legal_source_row.v2",
                "status": "FAIL_FIRST_LEGAL_SOURCE_ROW",
                "cell_id": activation.get("cell_id"),
                "activation_receipt": activation,
                "first_row_schema_validation_pass": False,
                "reason_codes": [self.failure_reason],
                "first_row_source_frame_identity": None,
                "first_row_sha256": None,
                "first_row_canonical_digest": None,
                "watchdog_timeout_seconds": self.timeout_seconds,
            }
            value["receipt_digest"] = canonical_sha256(value)
            _atomic_json(self.output / "FIRST_LEGAL_SOURCE_ROW_RECEIPT.json", value)
            self.receipt = value
            return {"request_stop": True, "termination_reason": self.failure_reason, "signal": "SIGINT"}
        return {}


__all__ = [
    "FIRST_ROW_WALL_TIMEOUT_SECONDS",
    "FirstLegalRowWatchdog",
    "build_exact_formal_child_environment",
    "expanded_relevant_environment",
    "persist_child_construction_receipt",
    "validate_first_source_row",
]
