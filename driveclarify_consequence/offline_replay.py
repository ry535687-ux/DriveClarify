"""Offline replay orchestrator — the deterministic, CPU-only, UNKNOWN-preserving pipeline driver.

Ties modules 1-20 together. Record-level error isolation (C1.1): a single bad record NEVER aborts
the run; it becomes a FAILED_VALIDATION / FAILED_COMPUTATION / SKIPPED result with reason codes and
processing continues. Only CLI/config/schema-file/output-dir errors abort globally.

Three modes:
  - synthetic-only                         (test_only=true)
  - cp1-worldstate-with-synthetic-candidates
  - cp1-recorded-candidate-replay

No CARLA / SimLingo / GPU import; no torch; no control. Deterministic: sorted candidate order,
canonical serialization, semantic replay hash excluding volatile runtime metadata.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import replay_summary as rs
from .feature_extractors import grades_from_snapshot
from .input_assembler import assemble_from_world_state
from .candidate_comparator import compare_candidate_set
from .record_loader import CandidateBundle, LoadedRecord, load_candidate_bundles, stream_jsonl
from .result_assembler import assemble_candidate_consequence, compute_set_geometry
from .state_machine_mapper import invoke_shadow_reducer, map_to_state_machine
from .time_binding import plan_age_sim_seconds
from .validators import (
    evaluate_freshness,
    linkage_validate,
    schema_validate,
    semantic_validate,
)

MODE_SYNTHETIC = "synthetic_only"
MODE_CP1_SYNTH = "cp1_worldstate_with_synthetic_candidates"
MODE_CP1_RECORDED = "cp1_recorded_candidate_replay"

_REAL_LOG_MODES = {MODE_CP1_SYNTH, MODE_CP1_RECORDED}


@dataclass
class ReplayConfig:
    mode: str
    input_path: str | None = None
    candidates_path: str | None = None
    invoke_shadow_reducer: bool = True
    # Provenance flags surfaced in every record result.
    test_only: bool = False

    def provenance(self) -> dict[str, Any]:
        if self.mode == MODE_SYNTHETIC:
            return {
                "world_state_source": "SYNTHETIC",
                "candidate_source": "SYNTHETIC",
                "physical_authorization_allowed": False,
                "test_only": True,
            }
        return {
            "world_state_source": "REAL_CP1_LOG",
            "candidate_source": "SYNTHETIC" if self.mode == MODE_CP1_SYNTH else "RECORDED_CP1",
            "physical_authorization_allowed": False,
            "test_only": False,
        }


@dataclass
class ReplayResult:
    record_results: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)


def _collect_unknown_fields(consequence: dict[str, Any]) -> list[dict[str, Any]]:
    """List every non-AVAILABLE Result field with its reason (for unknowns.jsonl / summary)."""
    out: list[dict[str, Any]] = []
    for category, fields in consequence.items():
        if not isinstance(fields, dict):
            continue
        for fname, r in fields.items():
            if isinstance(r, dict) and "status" in r and r.get("status") != "AVAILABLE":
                out.append({
                    "field": f"{category}.{fname}",
                    "status": r.get("status"),
                    "reason_code": r.get("reason_code"),
                    "dependencies": r.get("dependencies", []),
                })
    return out


def _process_record(
    input_record: dict[str, Any],
    cfg: ReplayConfig,
    record_seq: int,
    source_artifacts: tuple[str, ...],
    adapter_latency_s: float | None,
) -> dict[str, Any]:
    """Process ONE assembled ConsequenceAdapterInputV0 into a full record result. Isolated by caller."""
    obs = input_record.get("metadata", {}).get("observation_id")
    base = {
        "record_seq": record_seq,
        "observation_id": obs,
        "provenance": cfg.provenance(),
        "reason_codes": [],
        "unknown_fields": [],
        "consequences": [],
        "state_machine": None,
        "shadow_decision": None,
    }

    # (4) schema + (5) semantic + (6) linkage validation.
    schema_errors = schema_validate(input_record)
    semantic_errors = semantic_validate(input_record)
    linkage_errors = linkage_validate(input_record)
    if schema_errors or semantic_errors or linkage_errors:
        base["status"] = rs.FAILED_VALIDATION
        base["reason_codes"] = sorted(set(
            [e.split(":")[0] + ":" + e.split(":")[1] if e.startswith("SCHEMA") else e
             for e in schema_errors] + semantic_errors + linkage_errors))
        return base

    grades = grades_from_snapshot(input_record["contract_snapshot"])
    candidates = input_record["candidate_plans"]
    setgeo = compute_set_geometry(candidates)

    # plan age (SIM domain): now snapshot_elapsed - generation snapshot elapsed. Synthetic gen time
    # is unknown for CP1 (single tick), so plan_age uses stale_after basis only when SNAPSHOT_FRAME.
    plan_age = None  # v0: no verified per-candidate sim generation timestamp => leave None

    consequences: list[dict[str, Any]] = []
    partial = False
    for c in sorted(candidates, key=lambda x: str(x.get("candidate_id"))):
        cid = str(c.get("candidate_id"))
        is_stale, stale_reason = evaluate_freshness(input_record, c)
        linkage_ok = c.get("source_observation_id") == obs
        shape_ok, shape_reason = _shape_ok(c)
        pw, tv = setgeo.get(cid, (None, None))
        cc = assemble_candidate_consequence(
            c, input_record, grades,
            is_stale=is_stale, linkage_ok=linkage_ok, shape_ok=shape_ok, shape_reason=shape_reason,
            pairwise=pw, terminal=tv, plan_age_sim=plan_age, adapter_latency_s=adapter_latency_s,
            source_artifacts=source_artifacts,
        )
        # record-level schema validation of the OUTPUT (no field bypasses schema)
        out_errors = schema_validate(cc, _CANDIDATE_SCHEMA)
        if out_errors:
            base["status"] = rs.FAILED_COMPUTATION
            base["reason_codes"] = ["OUTPUT_SCHEMA_INVALID"] + out_errors[:3]
            return base
        if is_stale or not shape_ok:
            partial = True
        consequences.append(cc)
        base["unknown_fields"].extend(_collect_unknown_fields(cc))

    comparison = compare_candidate_set(consequences)
    sm = map_to_state_machine(input_record, consequences, comparison)
    sm_errors = schema_validate(sm, _SM_SCHEMA)
    if sm_errors:
        base["status"] = rs.FAILED_COMPUTATION
        base["reason_codes"] = ["STATE_MACHINE_SCHEMA_INVALID"] + sm_errors[:3]
        return base

    base["consequences"] = consequences
    base["state_machine"] = sm
    base["comparison"] = comparison
    base["reason_codes"] = list(sm["adapter_reason_codes"])
    if cfg.invoke_shadow_reducer:
        base["shadow_decision"] = invoke_shadow_reducer(sm, consequences)
    base["status"] = rs.PARTIAL if partial else rs.SUCCESS
    return base


def _shape_ok(candidate: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate the raw route shape [B][N][2] with finite numeric points."""
    from . import raw_geometry as rg
    _, reason = rg.route_point_count(candidate.get("pred_route_raw"))
    if reason:
        return False, reason
    return True, None


# Lazy schema paths (avoid import cycle at module import; resolved on first use).
from .validators import CANDIDATE_SCHEMA_PATH as _CANDIDATE_SCHEMA  # noqa: E402
from .validators import STATE_MACHINE_SCHEMA_PATH as _SM_SCHEMA      # noqa: E402


def run_replay(cfg: ReplayConfig) -> ReplayResult:
    """Run the full replay for the configured mode. Record-level isolation guarantees one bad record
    cannot abort the run. Returns a ReplayResult (record_results ordered by record_seq)."""
    result = ReplayResult(provenance=cfg.provenance())
    if cfg.mode == MODE_SYNTHETIC:
        _run_synthetic(cfg, result)
    elif cfg.mode in _REAL_LOG_MODES:
        _run_cp1(cfg, result)
    else:
        raise ValueError(f"UNKNOWN_REPLAY_MODE:{cfg.mode}")
    return result


def _measure_latency(fn, *args, **kwargs):
    """Run fn, returning (result, monotonic_latency_seconds). Latency is a diagnostic only."""
    t0 = time.monotonic()
    out = fn(*args, **kwargs)
    return out, time.monotonic() - t0


def _run_synthetic(cfg: ReplayConfig, result: ReplayResult) -> None:
    """Synthetic-only mode: candidates file already contains full ConsequenceAdapterInputV0 records,
    OR bundles keyed by observation with an embedded world_state stub. We accept full input records
    (one JSON object per line) under a 'records' list or JSONL of input records."""
    import json as _json
    path = Path(cfg.input_path)
    text = path.read_text(encoding="utf-8").strip()
    records: list[dict[str, Any]] = []
    # A single JSON object with a "records" wrapper, else JSONL (one input record per line).
    wrapper = None
    if text.startswith("{"):
        try:
            wrapper = _json.loads(text)
        except _json.JSONDecodeError:
            wrapper = None  # multi-line JSONL that happens to start with '{'
    src = (str(path),)
    if isinstance(wrapper, dict) and "records" in wrapper:
        for seq, rec in enumerate(wrapper["records"]):
            result.record_results.append(_isolate(_process_record, rec, cfg, seq, src, None))
        return
    # JSONL: parse each line independently so a malformed line is isolated (C1.1), never aborts.
    seq = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = _json.loads(line)
        except _json.JSONDecodeError:
            result.record_results.append({
                "record_seq": seq, "observation_id": None,
                "status": rs.FAILED_VALIDATION,
                "reason_codes": ["JSON_PARSE_ERROR:JSONDecodeError"],
                "source_line": lineno,
                "consequences": [], "unknown_fields": [],
                "state_machine": None, "shadow_decision": None,
                "provenance": cfg.provenance(),
            })
            seq += 1
            continue
        if not isinstance(rec, dict):
            result.record_results.append({
                "record_seq": seq, "observation_id": None,
                "status": rs.FAILED_VALIDATION, "reason_codes": ["JSON_NOT_OBJECT"],
                "source_line": lineno, "consequences": [], "unknown_fields": [],
                "state_machine": None, "shadow_decision": None, "provenance": cfg.provenance(),
            })
            seq += 1
            continue
        result.record_results.append(_isolate(_process_record, rec, cfg, seq, src, None))
        seq += 1


def _run_cp1(cfg: ReplayConfig, result: ReplayResult) -> None:
    """CP1 world-state + candidate bundles. Streams world_state.jsonl; a bad line is isolated."""
    bundles = load_candidate_bundles(cfg.candidates_path)
    src = (cfg.input_path, cfg.candidates_path)
    seq = 0
    for loaded in stream_jsonl(cfg.input_path):
        if not loaded.ok:
            result.record_results.append({
                "record_seq": seq,
                "observation_id": None,
                "status": rs.FAILED_VALIDATION,
                "reason_codes": [loaded.parse_error or "PARSE_ERROR"],
                "source_line": loaded.source_line,
                "consequences": [], "unknown_fields": [],
                "state_machine": None, "shadow_decision": None,
                "provenance": cfg.provenance(),
            })
            seq += 1
            continue
        obs = loaded.observation_id
        bundle = bundles.get(obs)
        if bundle is None:
            result.record_results.append({
                "record_seq": seq,
                "observation_id": obs,
                "status": rs.SKIPPED,
                "reason_codes": ["NO_CANDIDATE_BUNDLE_FOR_OBSERVATION"],
                "consequences": [], "unknown_fields": [],
                "state_machine": None, "shadow_decision": None,
                "provenance": cfg.provenance(),
            })
            seq += 1
            continue
        assembled = _isolate_assemble(loaded.data, bundle, seq)
        if isinstance(assembled, dict) and assembled.get("_assembly_error"):
            result.record_results.append({
                "record_seq": seq, "observation_id": obs,
                "status": rs.FAILED_COMPUTATION,
                "reason_codes": [assembled["_assembly_error"]],
                "consequences": [], "unknown_fields": [],
                "state_machine": None, "shadow_decision": None,
                "provenance": cfg.provenance(),
            })
            seq += 1
            continue
        rr = _isolate(_process_record, assembled, cfg, seq, src, None)
        result.record_results.append(rr)
        seq += 1


def _isolate_assemble(ws: dict[str, Any], bundle: CandidateBundle, seq: int) -> dict[str, Any]:
    try:
        return assemble_from_world_state(ws, bundle, seq)
    except Exception as exc:  # isolation: assembly failure is per-record
        return {"_assembly_error": f"ASSEMBLY_EXCEPTION:{exc.__class__.__name__}"}


def _isolate(fn, input_record, cfg, seq, src, latency) -> dict[str, Any]:
    """Run _process_record under isolation; any exception becomes a FAILED_COMPUTATION result."""
    try:
        return fn(input_record, cfg, seq, src, latency)
    except Exception as exc:  # C1.1: one record's exception must not corrupt later records
        return {
            "record_seq": seq,
            "observation_id": (input_record or {}).get("metadata", {}).get("observation_id"),
            "status": rs.FAILED_COMPUTATION,
            "reason_codes": [f"RECORD_EXCEPTION:{exc.__class__.__name__}"],
            "consequences": [], "unknown_fields": [],
            "state_machine": None, "shadow_decision": None,
            "provenance": cfg.provenance(),
        }


if __name__ == "__main__":
    from .cli import main
    raise SystemExit(main())
