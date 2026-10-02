"""Offline replay CLI (module 20) — CPU-only entry point.

    python -m driveclarify_consequence.offline_replay \
        --input <world_state.jsonl> --candidates <bundles.json> \
        --output <dir> --mode cp3a_v0

Writes the full output file set (RUN_MANIFEST, REPLAY_CONFIG, REPLAY_SUMMARY, REPLAY_HASH,
records.jsonl, failures.jsonl, unknowns.jsonl, capability_usage.json,
state_machine_shadow_summary.json, COMMAND_LOG.md). Global config/serialization errors return a
FAIL_* completion status and a nonzero exit code; per-record errors are isolated. No CARLA/SimLingo/GPU.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import replay_summary as rs
from .hashing import config_hash, hash_file, semantic_replay_hash
from .offline_replay import (
    MODE_CP1_RECORDED,
    MODE_CP1_SYNTH,
    MODE_SYNTHETIC,
    ReplayConfig,
    run_replay,
)
from .serialization import write_json, write_jsonl

ADAPTER_VERSION = "driveclarify.consequence_adapter.cp3a.v0"
INPUT_SCHEMA_VERSION = "driveclarify.consequence_adapter_input.v0"

_MODE_ALIASES = {
    "cp3a_v0": MODE_CP1_SYNTH,
    "synthetic": MODE_SYNTHETIC,
    "synthetic_only": MODE_SYNTHETIC,
    "cp1_synth": MODE_CP1_SYNTH,
    "cp1_worldstate_with_synthetic_candidates": MODE_CP1_SYNTH,
    "cp1_recorded": MODE_CP1_RECORDED,
    "cp1_recorded_candidate_replay": MODE_CP1_RECORDED,
}


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "UNKNOWN"


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    try:
        from importlib.metadata import version as _pkg_version
        versions["jsonschema"] = _pkg_version("jsonschema")
    except Exception:
        versions["jsonschema"] = "absent"
    return versions


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="driveclarify_consequence.offline_replay",
                                description="CPU-only offline consequence adapter replay (CP3A v0).")
    p.add_argument("--input", help="Input JSONL (CP1 world_state or synthetic input records).")
    p.add_argument("--candidates", help="Candidate bundle JSON/JSONL (required for CP1 modes).")
    p.add_argument("--output", required=True, help="Output directory (created if absent).")
    p.add_argument("--mode", required=True, help="cp3a_v0 | synthetic | cp1_synth | cp1_recorded")
    p.add_argument("--no-shadow", action="store_true", help="Skip frozen-reducer shadow invocation.")
    return p


def _resolve_mode(mode_arg: str) -> str:
    if mode_arg not in _MODE_ALIASES:
        raise SystemExit(_fail_config(f"UNKNOWN_MODE:{mode_arg}"))
    return _MODE_ALIASES[mode_arg]


def _fail_config(reason: str) -> int:
    sys.stderr.write(f"{rs.FAIL_GLOBAL_CONFIGURATION}: {reason}\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = _resolve_mode(args.mode)

    # Global configuration validation (aborts globally — allowed by C1.1).
    if mode in (MODE_CP1_SYNTH, MODE_CP1_RECORDED):
        if not args.input or not Path(args.input).is_file():
            return _fail_config(f"INPUT_NOT_FOUND:{args.input}")
        if not args.candidates or not Path(args.candidates).is_file():
            return _fail_config(f"CANDIDATES_NOT_FOUND:{args.candidates}")
    else:  # synthetic
        if not args.input or not Path(args.input).is_file():
            return _fail_config(f"INPUT_NOT_FOUND:{args.input}")

    out_dir = Path(args.output)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / ".writetest").write_text("ok", encoding="utf-8")
        (out_dir / ".writetest").unlink()
    except Exception as exc:
        return _fail_config(f"OUTPUT_DIR_NOT_WRITABLE:{exc.__class__.__name__}")

    cfg = ReplayConfig(
        mode=mode, input_path=args.input, candidates_path=args.candidates,
        invoke_shadow_reducer=not args.no_shadow,
        test_only=(mode == MODE_SYNTHETIC),
    )
    return _execute(cfg, out_dir)


def _execute(cfg: ReplayConfig, out_dir: Path) -> int:
    result = run_replay(cfg)
    records = result.record_results
    summary = rs.build_summary(records)
    status = rs.completion_status(summary)

    cfg_dict = {
        "mode": cfg.mode, "input_path": cfg.input_path, "candidates_path": cfg.candidates_path,
        "invoke_shadow_reducer": cfg.invoke_shadow_reducer, "test_only": cfg.test_only,
        "adapter_version": ADAPTER_VERSION,
    }
    cfg_h = config_hash(cfg_dict)

    source_hashes: dict[str, str] = {}
    for pth in filter(None, [cfg.input_path, cfg.candidates_path]):
        if Path(pth).is_file():
            source_hashes[pth] = hash_file(pth)

    replay_hash = semantic_replay_hash(
        records, schema_version=INPUT_SCHEMA_VERSION, adapter_version=ADAPTER_VERSION,
        config_hash=cfg_h, source_hashes=source_hashes)

    try:
        _write_outputs(out_dir, cfg, cfg_dict, cfg_h, records, summary, status,
                       source_hashes, replay_hash)
    except ValueError as exc:
        sys.stderr.write(f"{rs.FAIL_OUTPUT_SERIALIZATION}: {exc}\n")
        return 3

    sys.stdout.write(f"{status} replay_hash={replay_hash} records={summary['total_records']}\n")
    if status in (rs.FAIL_INPUT_CONTRACT, rs.FAIL_GLOBAL_CONFIGURATION, rs.FAIL_OUTPUT_SERIALIZATION):
        return 1
    return 0


def _capability_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-capability usage report: purpose, evidence grade, gate result, computed?, eligible?, block."""
    caps: dict[str, dict[str, Any]] = {}
    for r in records:
        for cc in r.get("consequences", []):
            for category, fields in cc.items():
                if not isinstance(fields, dict):
                    continue
                for fname, res in fields.items():
                    if not (isinstance(res, dict) and "status" in res):
                        continue
                    entry = caps.setdefault(f"{category}.{fname}", {
                        "requested_purpose": res.get("usage_purpose"),
                        "evidence_grade": res.get("evidence_grade"),
                        "computed_count": 0, "unknown_count": 0,
                        "authorization_eligible": bool(res.get("authorization_eligible")),
                        "safety_critical_eligible": bool(res.get("safety_critical_eligible")),
                        "reason_codes": Counter(),
                    })
                    if res.get("status") == "AVAILABLE":
                        entry["computed_count"] += 1
                    else:
                        entry["unknown_count"] += 1
                        if res.get("reason_code"):
                            entry["reason_codes"][res["reason_code"]] += 1
    # freeze counters into sorted dicts
    for e in caps.values():
        e["reason_codes"] = dict(sorted(e["reason_codes"].items()))
    return dict(sorted(caps.items()))


def _shadow_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for r in records:
        sd = r.get("shadow_decision")
        if sd:
            decisions[sd.get("decision", "NONE")] += 1
            reasons[sd.get("reason_code", "NONE")] += 1
    return {
        "decision_counts": dict(sorted(decisions.items())),
        "reason_code_counts": dict(sorted(reasons.items())),
        "note": "shadow_only; never used for control; v0 expected all FALLBACK.",
    }


def _write_outputs(out_dir: Path, cfg: ReplayConfig, cfg_dict: dict[str, Any], cfg_h: str,
                   records: list[dict[str, Any]], summary: dict[str, Any], status: str,
                   source_hashes: dict[str, str], replay_hash: str) -> None:
    write_jsonl(out_dir / "records.jsonl", records)
    failures = [r for r in records if r.get("status") in
                (rs.FAILED_VALIDATION, rs.FAILED_COMPUTATION, rs.PARTIAL, rs.SKIPPED)]
    write_jsonl(out_dir / "failures.jsonl", failures)
    unknowns = [{"record_seq": r.get("record_seq"), "observation_id": r.get("observation_id"),
                 "unknown_fields": r.get("unknown_fields", [])}
                for r in records if r.get("unknown_fields")]
    write_jsonl(out_dir / "unknowns.jsonl", unknowns)
    write_json(out_dir / "capability_usage.json", _capability_usage(records))
    write_json(out_dir / "state_machine_shadow_summary.json", _shadow_summary(records))
    write_json(out_dir / "REPLAY_SUMMARY.json", {**summary, "completion_status": status})
    write_json(out_dir / "REPLAY_CONFIG.json", cfg_dict)
    write_json(out_dir / "REPLAY_HASH.json", {
        "semantic_replay_hash": replay_hash,
        "config_hash": cfg_h,
        "source_hashes": source_hashes,
        "adapter_version": ADAPTER_VERSION,
        "schema_version": INPUT_SCHEMA_VERSION,
        "excludes": ["current_date", "temp_dir", "process_id", "monotonic_start",
                     "adapter_latency_monotonic"],
    })

    output_hashes = {}
    for name in ("records.jsonl", "failures.jsonl", "unknowns.jsonl", "capability_usage.json",
                 "state_machine_shadow_summary.json", "REPLAY_SUMMARY.json", "REPLAY_CONFIG.json",
                 "REPLAY_HASH.json"):
        fp = out_dir / name
        if fp.is_file():
            output_hashes[name] = hash_file(fp)

    manifest = {
        "adapter_version": ADAPTER_VERSION,
        "schema_versions": {
            "input": INPUT_SCHEMA_VERSION,
            "candidate": "driveclarify.candidate_consequence.v0",
            "state_machine": "driveclarify.adapter_state_machine_input.v0",
        },
        "git_head": _git_head(),
        "config_hash": cfg_h,
        "source_artifact_hashes": source_hashes,
        "output_file_hashes": output_hashes,
        "record_counts": {
            "total": summary["total_records"],
            "success": summary["success_records"],
            "partial": summary["partial_records"],
            "failed_validation": summary["failed_validation_records"],
            "failed_computation": summary["failed_computation_records"],
            "skipped": summary["skipped_records"],
        },
        "replay_mode": cfg.mode,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "dependency_versions": _dependency_versions(),
        "cpu_only": True,
        "no_carla": True,
        "no_simlingo": True,
        "no_gpu": True,
        "completion_status": status,
        "semantic_replay_hash": replay_hash,
    }
    write_json(out_dir / "RUN_MANIFEST.json", manifest)

    cmd_log = (
        "# COMMAND LOG (offline replay)\n\n"
        f"- mode: {cfg.mode}\n- input: {cfg.input_path}\n- candidates: {cfg.candidates_path}\n"
        f"- completion_status: {status}\n- semantic_replay_hash: {replay_hash}\n"
        f"- records: {summary['total_records']}\n"
        f"- python: {platform.python_version()}\n- cpu_only: true; no CARLA/SimLingo/GPU\n"
    )
    (out_dir / "COMMAND_LOG.md").write_text(cmd_log, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
