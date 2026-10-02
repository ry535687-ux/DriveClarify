"""CP1 record loader + synthetic candidate loader (streaming, read-only).

Streams JSONL one line at a time (does NOT load the whole file into memory), parses each line
independently, and preserves provenance: run_id, record_seq, source artifact path, source line
number, and the raw observation_id. It NEVER mutates the source dict, never back-fills zeros, never
converts an empty list to UNKNOWN, never converts MISSING to an empty list, and never converts an
EXCEPTION into UNSUPPORTED. A malformed line yields a LoadedRecord with ``parse_error`` set instead
of raising, so a single bad line cannot abort the whole replay (C1.1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class LoadedRecord:
    """One JSONL line with its provenance. Exactly one of ``data`` / ``parse_error`` is meaningful."""

    source_path: str
    source_line: int  # 1-indexed line number in the file
    raw_line: str
    data: dict[str, Any] | None = None
    parse_error: str | None = None
    # Provenance pulled out for convenience WITHOUT mutating data.
    run_id: str | None = None
    record_seq: int | None = None
    observation_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.parse_error is None and self.data is not None


def _provenance(data: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    """Extract (run_id, record_seq, observation_id) from a probe/world_state record shape.

    Reads either the top-level probe fields or a nested ``metadata`` block (adapter input shape).
    Returns None for anything absent — never fabricates.
    """
    run_id = data.get("run_id")
    record_seq = data.get("record_seq")
    observation_id = data.get("observation_id")
    md = data.get("metadata")
    if isinstance(md, dict):
        run_id = md.get("run_id", run_id)
        record_seq = md.get("record_seq", record_seq)
        observation_id = md.get("observation_id", observation_id)
    if not isinstance(record_seq, int) or isinstance(record_seq, bool):
        record_seq = None
    return run_id, record_seq, observation_id


def stream_jsonl(path: str | Path) -> Iterator[LoadedRecord]:
    """Yield one LoadedRecord per line, streaming. Malformed lines are captured, not raised."""
    p = Path(path)
    source = str(p)
    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue  # skip blank lines; they carry no record
            try:
                data = json.loads(stripped)
            except (json.JSONDecodeError, ValueError) as exc:
                yield LoadedRecord(
                    source_path=source,
                    source_line=lineno,
                    raw_line=stripped,
                    data=None,
                    parse_error=f"JSON_PARSE_ERROR:{exc.__class__.__name__}",
                )
                continue
            if not isinstance(data, dict):
                yield LoadedRecord(
                    source_path=source,
                    source_line=lineno,
                    raw_line=stripped,
                    data=None,
                    parse_error="JSON_NOT_OBJECT",
                )
                continue
            run_id, record_seq, observation_id = _provenance(data)
            yield LoadedRecord(
                source_path=source,
                source_line=lineno,
                raw_line=stripped,
                data=data,
                parse_error=None,
                run_id=run_id,
                record_seq=record_seq,
                observation_id=observation_id,
            )


@dataclass(frozen=True)
class CandidateBundle:
    """A synthetic-or-recorded candidate set bound to ONE observation. Fields mirror the CP2 input
    contract's ``candidate_plans`` items plus set-level identity. ``synthetic`` marks provenance so a
    synthetic value can never be laundered into a real physical consequence."""

    candidate_set_id: str
    source_observation_id: str
    candidates: tuple[dict[str, Any], ...]
    synthetic: bool = True
    contract_snapshot: dict[str, str] | None = None


def load_candidate_bundles(path: str | Path) -> dict[str, CandidateBundle]:
    """Load candidate bundles from a JSON or JSONL file, keyed by ``source_observation_id``.

    Accepts either a single JSON object with a ``bundles`` list, or JSONL (one bundle per line).
    Each bundle must declare ``source_observation_id`` and a non-empty ``candidates`` list; a bundle
    missing these is skipped-with-reason by the assembler, not silently coerced.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    bundles: dict[str, CandidateBundle] = {}
    raw_bundles: list[dict[str, Any]] = []
    text_stripped = text.strip()
    if text_stripped.startswith("{"):
        obj = json.loads(text_stripped)
        raw_bundles = list(obj.get("bundles", []))
    else:
        for line in text_stripped.splitlines():
            line = line.strip()
            if line:
                raw_bundles.append(json.loads(line))
    for rb in raw_bundles:
        obs = rb.get("source_observation_id")
        if not obs:
            continue
        bundles[obs] = CandidateBundle(
            candidate_set_id=rb.get("candidate_set_id", f"set:{obs}"),
            source_observation_id=obs,
            candidates=tuple(rb.get("candidates", ())),
            synthetic=bool(rb.get("synthetic", True)),
            contract_snapshot=rb.get("contract_snapshot"),
        )
    return bundles
