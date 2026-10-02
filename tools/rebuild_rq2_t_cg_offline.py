#!/usr/bin/env python3
"""Repair an offline CG join against a sealed native trace without rerunning CARLA."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.measurement import canonical_sha256  # noqa: E402
from driveclarify_rq2_t_cg.builder import build_episode  # noqa: E402
from tools.run_rq2_t_cg import REPORT, EVIDENCE, append, load, registry_row, route_path, sha256, write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", required=True)
    args = parser.parse_args()
    row = registry_row(args.identity)
    output = EVIDENCE / args.identity / "attempt_01"
    original = output / "ENGINEERING_EPISODE_RESULT.json"
    if not original.is_file() or not (output / "post_hoc_world_state.jsonl").is_file():
        raise RuntimeError("RQ2_T_CG_SEALED_NATIVE_TRACE_MISSING")
    repair_path = output / "OFFLINE_BUILDER_REPAIR_RESULT.json"
    if repair_path.exists():
        raise RuntimeError("RQ2_T_CG_OFFLINE_REPAIR_ALREADY_MATERIALIZED")
    builder = build_episode(output, scene_id=row["scene"], identity=row["identity"],
                            seed=int(row["seed"]), route_path=route_path(row))
    valid = bool(
        builder.get("same_source_identity_all_views") is True
        and builder.get("controlled_interface_runtime_reads") == 0
        and builder.get("production_control_read_or_write_count") == 0
        and builder.get("rule_evaluation", {}).get("rule_added_native_episodes") == 0
        and builder.get("rule_evaluation", {}).get("rule_added_vla_forwards") == 0
    )
    result = {
        "schema_version": "driveclarify.rq2_t_cg.offline_builder_repair.v1",
        "status": "PASS_VALID_ENGINEERING_EPISODE_AFTER_OFFLINE_JOIN_REPAIR" if valid else "FAIL_OFFLINE_JOIN_REPAIR",
        "identity": args.identity, "scene": row["scene"], "template": row["template"],
        "native_episode_rerun": False, "new_native_episode_count": 0, "new_vla_forward_count": 0,
        "original_engineering_result_sha256": sha256(original),
        "sealed_native_trace_sha256": sha256(output / "post_hoc_world_state.jsonl"),
        "repair": "JOIN_TO_UNCHANGED_V1_OWNER_COMMITMENT_EVENT_INSTEAD_OF_STRICTER_ARC_CROSSING",
        "scientific_contract_changed": False, "commitment_contract_changed": False,
        "builder_receipt": builder,
    }
    result["record_digest"] = canonical_sha256(result)
    write_json(repair_path, result)
    append(REPORT / "OFFLINE_BUILDER_REPAIR_LEDGER.jsonl", result)
    print(json.dumps({"status": result["status"], "identity": args.identity,
                      "witness_count": builder["same_frame_b1_false_b2_true_witness_count"],
                      "first_sufficiency": builder["first_sufficiency"],
                      "native_episode_rerun": False}, indent=2))
    return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
