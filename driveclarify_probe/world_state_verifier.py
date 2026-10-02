"""Offline read-only verifier for CP1 world_state.jsonl.

Checks structural integrity only; does NOT assert physical conclusions (unit, sign,
handedness, camera direction, safety). Exit 0 = PASS, 1 = FAIL. Never writes labels.

Usage: PYTHONPATH=. python -m driveclarify_probe.world_state_verifier world_state.jsonl [probe.jsonl]
If probe.jsonl is given, also checks observation_id 1:1 linkage between the two files.
"""

from __future__ import annotations

import json
import math
import sys

VERIFIER_SCHEMA_VERSION = "driveclarify.world_state_verifier.v1"
WS_SCHEMA = "driveclarify.world_state.v1"


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def verify(ws_path: str, probe_path: str | None = None) -> dict:
    issues: list[str] = []
    records = []
    with open(ws_path, encoding="utf-8") as fh:
        for i, ln in enumerate(fh):
            ln = ln.strip()
            if not ln:
                continue
            try:
                records.append(json.loads(ln))
            except Exception as exc:  # noqa: BLE001
                issues.append(f"line {i}: JSON parse error: {exc}")
    n = len(records)
    obs_ids = []
    for i, r in enumerate(records):
        if r.get("schema_version") != WS_SCHEMA:
            issues.append(f"rec {i}: schema_version != {WS_SCHEMA}")
        oid = r.get("observation_id")
        if not oid:
            issues.append(f"rec {i}: missing observation_id")
        else:
            obs_ids.append(oid)
        # ego present with a status
        ego = r.get("ego")
        if not isinstance(ego, dict) or "status" not in ego:
            issues.append(f"rec {i}: ego missing/without status")
        else:
            loc = ego.get("location_xyz")
            if loc is not None and not (_finite(loc[0]) and _finite(loc[1]) and _finite(loc[2])):
                issues.append(f"rec {i}: ego location non-finite")
        # independent frame/clock presence (may be absent if getter failed, but if present must be finite)
        for key in ("gametime_frame", "snapshot_frame", "agent_timestamp_seconds",
                    "snapshot_elapsed_seconds", "probe_read_monotonic_s"):
            if key in r and r[key] is not None and not _finite(r[key]):
                issues.append(f"rec {i}: {key} non-finite")
        # actor query flag must be explicit (True/False), never silently absent when actors key present
        actors = r.get("actors")
        if isinstance(actors, dict) and "actor_query_ok" not in actors:
            issues.append(f"rec {i}: actors without actor_query_ok flag")
        # traffic lights must never be coerced to a default; state is str or null
        tls = r.get("traffic_lights")
        if isinstance(tls, dict):
            for lt in tls.get("lights", []) or []:
                st = lt.get("state")
                if st is not None and not isinstance(st, str):
                    issues.append(f"rec {i}: traffic light state not str/null")
        # pred route/speed, if present, must be finite lists
        for key, pts in (("pred_route_values", 20), ("pred_speed_wps_values", 10)):
            v = r.get(key)
            if v is not None:
                try:
                    flat = v[0] if (isinstance(v, list) and len(v) == 1 and isinstance(v[0], list)) else v
                    if not all(_finite(c) for p in flat for c in p):
                        issues.append(f"rec {i}: {key} non-finite")
                except Exception:  # noqa: BLE001
                    issues.append(f"rec {i}: {key} malformed")

    # monotonic clock non-decreasing (if present)
    mono = [r.get("probe_read_monotonic_s") for r in records if _finite(r.get("probe_read_monotonic_s"))]
    if any(mono[i] < mono[i - 1] for i in range(1, len(mono))):
        issues.append("probe_read_monotonic_s not non-decreasing")

    linkage = None
    if probe_path:
        probe_ids = []
        with open(probe_path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    try:
                        probe_ids.append(json.loads(ln).get("observation_id"))
                    except Exception:  # noqa: BLE001
                        pass
        ws_set = set(obs_ids)
        pr_set = set(probe_ids)
        linkage = {
            "probe_records": len(probe_ids),
            "world_state_records": n,
            "observation_ids_common": len(ws_set & pr_set),
            "in_world_state_not_probe": sorted(ws_set - pr_set)[:5],
            "in_probe_not_world_state": sorted(pr_set - ws_set)[:5],
        }
        if ws_set != pr_set:
            issues.append("observation_id sets differ between world_state.jsonl and probe.jsonl")

    result = {
        "verifier_schema_version": VERIFIER_SCHEMA_VERSION,
        "world_state_path": ws_path,
        "records_checked": n,
        "observation_ids_unique": len(set(obs_ids)),
        "status": "PASS" if not issues else "FAIL",
        "issue_count": len(issues),
        "issues": issues[:50],
    }
    if linkage is not None:
        result["linkage"] = linkage
    return result


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: world_state_verifier world_state.jsonl [probe.jsonl]", file=sys.stderr)
        return 2
    res = verify(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(json.dumps(res, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if res["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
