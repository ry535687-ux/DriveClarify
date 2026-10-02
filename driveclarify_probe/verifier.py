"""独立离线 JSONL verifier（只读，不回写）。

用法：
    python -m driveclarify_probe.verifier path/to/probe.jsonl

返回非零退出码表示验证失败，并向 stdout 输出 machine-readable summary JSON。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .schema import (
    CONTROL_RANGES,
    PRED_ROUTE_EXPECTED_POINTS,
    PRED_SPEED_EXPECTED_POINTS,
    SCHEMA_VERSION,
    SIMULATION_CLOCK_FIELDS,
)


class LineIssue(dict):
    pass


def verify_lines(lines: list[str]) -> dict[str, Any]:
    """核验一组 JSONL 行，返回 summary（不修改输入、不写文件）。"""

    issues: list[dict[str, Any]] = []
    prev_seq: int | None = None
    prev_sim_time: dict[str, float] = {}
    prev_monotonic: dict[str, float] = {}
    seen_obs_ids: set[str] = set()
    record_count = 0

    for idx, raw in enumerate(lines):
        raw = raw.strip()
        if not raw:
            continue
        record_count += 1
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            issues.append({"line": idx, "code": "INVALID_JSON", "detail": str(exc)})
            continue

        _check_schema_version(rec, idx, issues)
        prev_seq = _check_record_seq(rec, idx, issues, prev_seq)
        _check_observation_id(rec, idx, issues, seen_obs_ids)
        _check_frame_types(rec, idx, issues)
        _check_frame_join(rec, idx, issues)
        prev_sim_time = _check_sim_monotonic_clocks(
            rec, idx, issues, prev_sim_time, prev_monotonic
        )
        _check_latency_order(rec, idx, issues)
        _check_route_speed(rec, idx, issues)
        _check_source_link(rec, idx, issues)
        _check_baseline_control(rec, idx, issues)
        _check_unknown_reasons(rec, idx, issues)
        _check_provenance(rec, idx, issues)
        _check_counters(rec, idx, issues)

    summary = {
        "verifier_schema_version": "driveclarify.controlled_probe_verifier.v0.1",
        "records_checked": record_count,
        "issue_count": len(issues),
        "status": "PASS" if not issues else "FAIL",
        "issues": issues,
    }
    return summary


def _check_schema_version(rec: dict, idx: int, issues: list) -> None:
    if rec.get("schema_version") != SCHEMA_VERSION:
        issues.append(
            {"line": idx, "code": "SCHEMA_VERSION_MISMATCH", "detail": rec.get("schema_version")}
        )


def _check_record_seq(rec: dict, idx: int, issues: list, prev: int | None) -> int | None:
    seq = rec.get("record_seq")
    if not isinstance(seq, int) or isinstance(seq, bool):
        issues.append({"line": idx, "code": "RECORD_SEQ_INVALID", "detail": seq})
        return prev
    if prev is not None and seq <= prev:
        issues.append(
            {"line": idx, "code": "RECORD_SEQ_NOT_MONOTONIC", "detail": f"{prev}->{seq}"}
        )
    return seq


def _check_observation_id(rec: dict, idx: int, issues: list, seen: set) -> None:
    obs = rec.get("observation_id")
    if obs is None:
        # null 允许，但必须有 quality.unknown reason（由 _check_unknown_reasons 覆盖）。
        return
    if not isinstance(obs, str):
        issues.append({"line": idx, "code": "OBSERVATION_ID_TYPE", "detail": obs})
        return
    if obs in seen:
        issues.append({"line": idx, "code": "OBSERVATION_ID_DUPLICATE", "detail": obs})
    seen.add(obs)


def _check_frame_types(rec: dict, idx: int, issues: list) -> None:
    frame = rec.get("frame", {})
    for key in ("carla_snapshot_frame", "game_time_frame"):
        val = frame.get(key)
        if val is not None and (not isinstance(val, int) or isinstance(val, bool)):
            issues.append({"line": idx, "code": "FRAME_TYPE_INVALID", "detail": f"{key}={val}"})


def _check_frame_join(rec: dict, idx: int, issues: list) -> None:
    quality = rec.get("quality", {})
    status = quality.get("frame_join_status")
    if status not in ("CONSISTENT", "MISMATCH", "UNKNOWN"):
        issues.append({"line": idx, "code": "FRAME_JOIN_STATUS_INVALID", "detail": status})


def _check_sim_monotonic_clocks(
    rec: dict,
    idx: int,
    issues: list,
    prev_sim: dict[str, float],
    prev_mono: dict[str, float],
) -> dict[str, float]:
    clock = rec.get("clock", {})
    # simulation time 单调（若存在）
    new_sim = dict(prev_sim)
    for f in SIMULATION_CLOCK_FIELDS:
        v = clock.get(f)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            issues.append({"line": idx, "code": "SIM_CLOCK_TYPE", "detail": f"{f}={v}"})
            continue
        if f == "sim_delta_s":
            continue
        if f in prev_sim and v < prev_sim[f]:
            issues.append(
                {"line": idx, "code": "SIM_CLOCK_NOT_MONOTONIC", "detail": f"{f} {prev_sim[f]}->{v}"}
            )
        new_sim[f] = v

    # monotonic clock 单调（probe_read）
    mono = clock.get("probe_read_monotonic_s")
    if mono is not None:
        if not isinstance(mono, (int, float)) or isinstance(mono, bool):
            issues.append({"line": idx, "code": "MONOTONIC_TYPE", "detail": mono})
        elif "probe_read_monotonic_s" in prev_mono and mono < prev_mono["probe_read_monotonic_s"]:
            issues.append(
                {"line": idx, "code": "MONOTONIC_NOT_MONOTONIC",
                 "detail": f"{prev_mono['probe_read_monotonic_s']}->{mono}"}
            )
        else:
            prev_mono["probe_read_monotonic_s"] = mono

    # 明显错误：simulation time 被填进 monotonic 字段（值相等且都存在时告警）。
    sim_elapsed = clock.get("sim_elapsed_s")
    if (
        sim_elapsed is not None
        and mono is not None
        and isinstance(sim_elapsed, (int, float))
        and isinstance(mono, (int, float))
        and float(sim_elapsed) == float(mono)
        and float(sim_elapsed) != 0.0
    ):
        issues.append(
            {"line": idx, "code": "SIM_TIME_IN_MONOTONIC_FIELD", "detail": sim_elapsed}
        )
    return new_sim


def _check_latency_order(rec: dict, idx: int, issues: list) -> None:
    clock = rec.get("clock", {})
    start = clock.get("model_start_monotonic_s")
    end = clock.get("model_end_monotonic_s")
    ready = clock.get("control_ready_monotonic_s")
    seq = [v for v in (start, end, ready) if v is not None]
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seq):
        if start is not None and end is not None and end < start:
            issues.append({"line": idx, "code": "MODEL_END_BEFORE_START", "detail": f"{start}->{end}"})
        if end is not None and ready is not None and ready < end:
            issues.append({"line": idx, "code": "CONTROL_READY_BEFORE_MODEL_END", "detail": f"{end}->{ready}"})


def _check_route_speed(rec: dict, idx: int, issues: list) -> None:
    mo = rec.get("model_output", {})
    _check_one_tensor(mo.get("pred_route"), "pred_route", PRED_ROUTE_EXPECTED_POINTS, idx, issues)
    _check_one_tensor(mo.get("pred_speed_wps"), "pred_speed_wps", PRED_SPEED_EXPECTED_POINTS, idx, issues)


def _check_one_tensor(t: Any, name: str, expected_points: int, idx: int, issues: list) -> None:
    if not isinstance(t, dict):
        return
    if t.get("present") is not True:
        return  # absent/unknown handled by unknown_fields
    shape = t.get("shape")
    if isinstance(shape, list) and len(shape) == 3:
        if shape[1] != expected_points:
            issues.append(
                {"line": idx, "code": f"{name.upper()}_POINT_COUNT",
                 "detail": f"expected {expected_points} got {shape[1]}"}
            )
        if shape[2] != 2:
            issues.append(
                {"line": idx, "code": f"{name.upper()}_DIM", "detail": shape[2]}
            )
    else:
        issues.append({"line": idx, "code": f"{name.upper()}_SHAPE_RANK", "detail": shape})
    finite = t.get("finite")
    if finite is False:
        issues.append({"line": idx, "code": f"{name.upper()}_NON_FINITE", "detail": True})


def _check_source_link(rec: dict, idx: int, issues: list) -> None:
    mo = rec.get("model_output", {})
    if not mo:
        return
    same = mo.get("generated_from_same_forward")
    if same is not None and same is not True:
        issues.append({"line": idx, "code": "NOT_SAME_FORWARD", "detail": same})
    obs = rec.get("observation_id")
    src = mo.get("source_observation_id")
    if obs is not None and src is not None and obs != src:
        issues.append(
            {"line": idx, "code": "SOURCE_OBS_ID_MISMATCH", "detail": f"{obs}!={src}"}
        )


def _check_baseline_control(rec: dict, idx: int, issues: list) -> None:
    bc = rec.get("baseline_control", {})
    for field_name, (lo, hi) in CONTROL_RANGES.items():
        v = bc.get(field_name)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            issues.append({"line": idx, "code": "CONTROL_TYPE", "detail": f"{field_name}={v}"})
            continue
        if not (lo <= float(v) <= hi):
            issues.append(
                {"line": idx, "code": "CONTROL_OUT_OF_RANGE", "detail": f"{field_name}={v}"}
            )


def _check_unknown_reasons(rec: dict, idx: int, issues: list) -> None:
    quality = rec.get("quality", {})
    unknown = quality.get("unknown_fields")
    if unknown is None:
        issues.append({"line": idx, "code": "MISSING_UNKNOWN_FIELDS_LIST", "detail": None})
        return
    if not isinstance(unknown, list):
        issues.append({"line": idx, "code": "UNKNOWN_FIELDS_TYPE", "detail": type(unknown).__name__})
        return
    for entry in unknown:
        if not isinstance(entry, dict) or "path" not in entry or "reason" not in entry:
            issues.append({"line": idx, "code": "UNKNOWN_ENTRY_MALFORMED", "detail": entry})
        elif not entry.get("reason"):
            issues.append({"line": idx, "code": "UNKNOWN_WITHOUT_REASON", "detail": entry.get("path")})


def _check_provenance(rec: dict, idx: int, issues: list) -> None:
    # commit/config/checkpoint hash 存在性只做存在提示，不阻断（缺失应在 unknown_fields）。
    quality = rec.get("quality", {})
    prov = quality.get("provenance", {})
    if not isinstance(prov, dict):
        issues.append({"line": idx, "code": "PROVENANCE_TYPE", "detail": type(prov).__name__})


def _check_counters(rec: dict, idx: int, issues: list) -> None:
    quality = rec.get("quality", {})
    prov = quality.get("provenance", {})
    for c in ("dropped_records", "serialization_errors", "writer_errors"):
        v = prov.get(c)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            issues.append({"line": idx, "code": "COUNTER_INVALID", "detail": f"{c}={v}"})


def verify_file(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    return verify_lines(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="driveclarify_probe.verifier",
        description="Offline read-only verifier for controlled probe JSONL.",
    )
    parser.add_argument("path", help="Path to probe JSONL file")
    args = parser.parse_args(argv)
    try:
        summary = verify_file(args.path)
    except OSError as exc:
        print(json.dumps({"status": "FAIL", "code": "FILE_ERROR", "detail": str(exc)}))
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
