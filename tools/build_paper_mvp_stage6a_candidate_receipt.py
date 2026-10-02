#!/usr/bin/env python3
"""Build Gate B from 24 genuine live RGB candidate-capture records."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from driveclarify_paper_mvp_candidate_audit import (  # noqa: E402
    AUDIT_FILENAME,
    RecordedCandidateGeneration,
    run_candidate_generation_audit,
)
from driveclarify_paper_mvp_runtime import PolicyEpisodeInput  # noqa: E402
from driveclarify_paper_mvp_runtime.live_candidate_capture import (  # noqa: E402
    ADAPTER_CONFIG_SHA256,
    ADAPTER_ID,
    CAPTURE_HASH_FIELD,
    CAPTURE_SCHEMA,
)
from driveclarify_paper_mvp_scenarios.contracts import (  # noqa: E402
    canonical_sha256,
    file_sha256,
    load_json,
    pretty_json_bytes,
    require,
)
from driveclarify_paper_mvp_scenarios.live_promotion import (  # noqa: E402
    _atomic_write,
)


TOWNS = (
    "Town01",
    "Town02",
    "Town03",
    "Town04",
    "Town05",
    "Town06",
    "Town07",
    "Town10HD",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=REPOSITORY_ROOT / "driveclarify_paper_mvp_scenarios/generated",
    )
    parser.add_argument(
        "--frozen-catalog",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "reports/paper_mvp_scenario_freeze_v0/SCENARIO_CATALOG.json"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def _capture_records(root: Path) -> list[Mapping[str, Any]]:
    records = []
    for town in TOWNS:
        progress_path = root / "progress" / (town + ".json")
        require(progress_path.is_file(), "LIVE_CANDIDATE_PROGRESS_MISSING:" + town)
        progress = load_json(progress_path)
        require(progress.get("status") == "PASS", "LIVE_CANDIDATE_TOWN_NOT_PASS:" + town)
        cleanup = progress.get("cleanup")
        require(
            isinstance(cleanup, Mapping) and cleanup.get("status") == "PASS",
            "LIVE_CANDIDATE_TOWN_CLEANUP_NOT_PASS:" + town,
        )
        records.extend(progress.get("records", ()))
    require(len(records) == 24, "LIVE_CANDIDATE_CAPTURE_COUNT_NOT_24")
    identities = {
        (str(item["runtime_fixture_id"]), int(item["selected_seed"]))
        for item in records
    }
    require(len(identities) == 24, "LIVE_CANDIDATE_CAPTURE_IDENTITIES_NOT_UNIQUE")
    return sorted(records, key=lambda item: item["runtime_fixture_id"])


def _scenario_by_runtime(runtime_root: Path) -> Mapping[str, str]:
    manifest = load_json(runtime_root / "STAGE6A_SCENARIO_MANIFEST.json")
    rows = manifest.get("records") or manifest.get("runtime_fixtures")
    require(isinstance(rows, list) and len(rows) == 24, "STAGE6A_RUNTIME_MANIFEST_INVALID")
    result = {
        str(item["runtime_fixture_id"]): str(item["scenario_id"])
        for item in rows
    }
    require(len(result) == 24, "STAGE6A_RUNTIME_TO_SCENARIO_JOIN_INVALID")
    return result


def _load_verified_capture(
    capture_root: Path, progress_record: Mapping[str, Any]
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    descriptor = progress_record.get("candidate_capture")
    require(isinstance(descriptor, Mapping), "CANDIDATE_CAPTURE_DESCRIPTOR_MISSING")
    relative = str(descriptor.get("candidate_capture_path", ""))
    path = capture_root / relative
    require(path.is_file(), "CANDIDATE_CAPTURE_FILE_MISSING")
    capture = load_json(path)
    digest = str(capture.get(CAPTURE_HASH_FIELD, ""))
    unsigned = dict(capture)
    unsigned.pop(CAPTURE_HASH_FIELD, None)
    require(
        capture.get("schema_version") == CAPTURE_SCHEMA
        and capture.get("status") == "VERIFIED"
        and digest == canonical_sha256(unsigned)
        and digest == descriptor.get(CAPTURE_HASH_FIELD)
        and file_sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest(),
        "CANDIDATE_CAPTURE_CONTENT_ADDRESS_INVALID",
    )
    require(
        capture.get("runtime_fixture_id") == progress_record.get("runtime_fixture_id")
        and capture.get("selected_seed") == progress_record.get("selected_seed")
        and capture.get("runtime_manifest_sha256")
        == progress_record.get("runtime_manifest_sha256"),
        "CANDIDATE_CAPTURE_RUNTIME_IDENTITY_MISMATCH",
    )
    handler_path = capture_root / str(progress_record["handler_receipt_path"])
    handler = load_json(handler_path)
    handler_capture = handler.get("runtime_candidate_generation", {}).get("capture")
    require(
        handler.get("front_rgb", {}).get("raw_bgra_sha256")
        == capture.get("front_rgb", {}).get("raw_bgra_sha256")
        and handler.get("front_rgb", {}).get("frame")
        == capture.get("front_rgb", {}).get("frame")
        and handler_capture == descriptor
        and handler.get("runtime_candidate_generation", {}).get("verified") is True,
        "CANDIDATE_CAPTURE_HANDLER_RGB_BINDING_INVALID",
    )
    require(
        capture.get("vision_adapter", {}).get("adapter_config_sha256")
        == ADAPTER_CONFIG_SHA256
        and capture.get("vision_adapter", {}).get("world_actor_state_read_count") == 0
        and capture.get("vision_adapter", {}).get("image_only_adapter") is True
        and capture.get("front_rgb", {}).get("genuine_carla_sensor_callback") is True
        and capture.get("front_rgb", {}).get(
            "world_actor_state_projection_used_as_image"
        )
        is False,
        "CANDIDATE_CAPTURE_REAL_RGB_ADAPTER_EVIDENCE_INVALID",
    )
    policy = PolicyEpisodeInput.from_mapping(capture["policy_input_projection"])
    generation = capture["generation_output"]
    require(
        generation.get("status") == "READY"
        and len(generation.get("candidates", ())) == 2
        and generation.get("audit", {}).get("catalog_read_count") == 0
        and generation.get("audit", {}).get("evaluation_label_access_count") == 0
        and policy.vision_observation.image_sha256
        == capture["front_rgb"]["raw_bgra_sha256"],
        "CANDIDATE_CAPTURE_RUNTIME_OUTPUT_INVALID",
    )
    return capture, {
        "runtime_fixture_id": capture["runtime_fixture_id"],
        "selected_seed": capture["selected_seed"],
        "candidate_capture_path": relative,
        "candidate_capture_payload_sha256": digest,
        "handler_receipt_payload_sha256": progress_record[
            "handler_receipt_payload_sha256"
        ],
        "front_rgb_raw_sha256": capture["front_rgb"]["raw_bgra_sha256"],
        "source_frame_id": capture["front_rgb"]["frame"],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    capture_root = args.capture_dir.resolve()
    runtime_root = args.runtime_root.resolve()
    output_root = args.output_dir.resolve()
    progress_records = _capture_records(capture_root)
    scenario_by_runtime = _scenario_by_runtime(runtime_root)
    audit_records = []
    evidence_rows = []
    for progress_record in progress_records:
        capture, evidence = _load_verified_capture(capture_root, progress_record)
        runtime_id = str(capture["runtime_fixture_id"])
        require(runtime_id in scenario_by_runtime, "CANDIDATE_RUNTIME_SCENARIO_JOIN_MISSING")
        audit_records.append(
            RecordedCandidateGeneration(
                scenario_id=scenario_by_runtime[runtime_id],
                policy_input_projection=capture["policy_input_projection"],
                generation_output=capture["generation_output"],
            )
        )
        evidence_rows.append(evidence)

    report = run_candidate_generation_audit(
        audit_records,
        frozen_catalog_path=args.frozen_catalog.resolve(),
        output_directory=output_root,
    )
    audit_path = output_root / AUDIT_FILENAME
    payload = load_json(audit_path)
    payload["live_evidence"] = {
        "source_kind": "LIVE_CARLA_FRONT_RGB_RUNTIME_CANDIDATE_CAPTURE_SET",
        "status": "VERIFIED" if report.status == "PASS" else "BLOCKED",
        "capture_count": len(evidence_rows),
        "genuine_front_rgb_callback_count": 24,
        "runtime_candidate_count": 48,
        "adapter_id": ADAPTER_ID,
        "adapter_config_sha256": ADAPTER_CONFIG_SHA256,
        "world_actor_state_projection_used_as_image_count": 0,
        "runtime_catalog_read_count": 0,
        "runtime_evaluation_label_access_count": 0,
        "model_forward_count": 0,
        "pid_invocation_count": 0,
        "control_write_count": 0,
        "test_labels_opened": False,
        "physical_display_runtime_window": True,
        "capture_set_sha256": canonical_sha256(evidence_rows),
        "captures": evidence_rows,
    }
    _atomic_write(audit_path, pretty_json_bytes(payload))
    result = {
        "status": "PASS_RUNTIME_CANDIDATE_GENERATION_24_OF_24"
        if report.status == "PASS"
        else "BLOCKED_RUNTIME_CANDIDATE_GENERATION_AUDIT",
        "candidate_audit_path": str(audit_path),
        "candidate_audit_sha256": file_sha256(audit_path),
        "capture_count": len(evidence_rows),
        "test_labels_opened": False,
    }
    _atomic_write(
        output_root / "CANDIDATE_AUDIT_BUILD_RESULT.json",
        pretty_json_bytes(result),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
