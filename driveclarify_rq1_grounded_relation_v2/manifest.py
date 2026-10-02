"""从历史冻结 V3 静态包构建无标签的 RQ1 split 候选。"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .contracts import canonical_json, digest


class ManifestError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ManifestError("JSON_OBJECT_REQUIRED:" + str(path))
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _junction_number(row: Mapping[str, Any]) -> int:
    return int(row["junction_id"])


def _public_row(row: Mapping[str, Any], status: str) -> dict[str, Any]:
    fields = ("unit_id", "town", "junction_id", "junction_group", "route_id", "route_family", "map_sha256", "source_type", "static_verdict")
    return {
        **{field: row[field] for field in fields},
        "layout_identity": f"{row['town']}:junction:{row['junction_id']}",
        "qualification_status": status,
        "source_candidate_sha256": digest(row),
    }


def build(shortlist_path: Path, campaign_spec_path: Path, stage_b_runs: Path) -> dict[str, Any]:
    shortlist = read_json(shortlist_path)
    campaign = read_json(campaign_spec_path)
    candidates = shortlist.get("candidates", [])
    if len(candidates) != 52 or len({row["unit_id"] for row in candidates}) != 52:
        raise ManifestError("SHORTLIST_IDENTITY_INVALID")
    by_id = {row["unit_id"]: row for row in candidates}
    consumed = list(campaign.get("unit_order", []))
    if len(consumed) != 24 or not set(consumed) <= set(by_id):
        raise ManifestError("CONSUMED_SET_INVALID")
    completed = set()
    for path in stage_b_runs.glob("*/RUN_RESULT.json"):
        result = read_json(path)
        if result.get("final_status") == "COMPLETE_SIX_PLANS":
            completed.add(result["unit_id"])
    if len(completed) != 22 or not completed <= set(consumed):
        raise ManifestError("HISTORICAL_COMPLETION_SET_INVALID")

    completed_by_town: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit_id in completed:
        completed_by_town[by_id[unit_id]["town"]].append(by_id[unit_id])
    if len(completed_by_town) != 8:
        raise ManifestError("DEV_TOWN_COVERAGE_INVALID")
    dev = [sorted(rows, key=_junction_number)[0] for _, rows in sorted(completed_by_town.items())]

    prospective_by_town: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        if row["unit_id"] not in consumed:
            prospective_by_town[row["town"]].append(row)
    for rows in prospective_by_town.values():
        rows.sort(key=_junction_number)
    if sum(map(len, prospective_by_town.values())) != 28:
        raise ManifestError("PROSPECTIVE_POOL_INVALID")
    test = []
    while len(test) < 24:
        progressed = False
        for town in sorted(prospective_by_town):
            if prospective_by_town[town] and len(test) < 24:
                test.append(prospective_by_town[town].pop(0)); progressed = True
        if not progressed:
            raise ManifestError("PROSPECTIVE_POOL_EXHAUSTED")
    capacity_exclusions = [row for town in sorted(prospective_by_town) for row in prospective_by_town[town]]

    if {row["unit_id"] for row in dev} & {row["unit_id"] for row in test}:
        raise ManifestError("DEV_TEST_LEAKAGE")
    payload = {
        "schema_version": "driveclarify.rq1_grounded_relation_split_candidates.v1",
        "status": "PROSPECTIVE_TEST_PENDING_FAMILY_QUALIFICATION",
        "source": {"shortlist_path": str(shortlist_path), "shortlist_file_sha256": sha256_file(shortlist_path), "shortlist_embedded_sha256": shortlist.get("sha256"), "campaign_spec_path": str(campaign_spec_path), "campaign_spec_file_sha256": sha256_file(campaign_spec_path)},
        "dev": [_public_row(row, "HISTORICAL_COMPLETE_DEVELOPMENT_ONLY") for row in dev],
        "test": [_public_row(row, "PENDING_FAMILY_QUALIFICATION") for row in test],
        "capacity_exclusions": [{**_public_row(row, "NOT_SELECTED"), "reason": "PROSPECTIVE_CAP_24"} for row in capacity_exclusions],
        "audits": {"dev_count": len(dev), "test_candidate_count": len(test), "capacity_exclusion_count": len(capacity_exclusions), "dev_test_unit_overlap": 0, "test_consumed_historical_overlap": 0, "family_assigned_before_qualification": False, "relation_label_read": False},
    }
    payload["sha256"] = digest(payload)
    return payload


def write_frozen(path: Path, value: Mapping[str, Any]) -> None:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise ManifestError("FROZEN_OUTPUT_MISMATCH:" + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
