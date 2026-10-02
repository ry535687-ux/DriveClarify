"""Closed contracts for the append-only Grounded Language E1-R1 revision."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


FEATURE_FLAG = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1"
SCHEMA_PREFIX = "driveclarify.grounded_language_v1_extension_e1_r1"
REPORT_ROOT = Path("reports/grounded_language_v1_extension_e1_r1")
ARTIFACT_ROOT = Path("artifacts/grounded_language_v1_extension_e1_r1")
FIXTURE_ROOT = Path("driveclarify_grounded_language_v1_extension_e1_r1/fixtures")
SCENARIO_ROOT = Path("driveclarify_grounded_language_v1_extension_e1_r1/scenario_root")

METHODS = (
    "original_simlingo",
    "never_ask",
    "always_ask",
    "always_wait",
    "driveclarify_r0",
    "driveclarify_grounded_v1",
)
MECHANISMS = ("ACT", "ASK", "WAIT")

PROTECTED_PATHS = {
    "stage6a_freeze": Path(
        "reports/paper_mvp_stage6a_final_freeze_v1/FINAL_STAGE6A_FREEZE_RECEIPT.json"
    ),
    "stage6b_r0_freeze": Path(
        "reports/paper_mvp_stage6b_runtime_contract_freeze_v1/STAGE6B_R0_FREEZE_RECEIPT.json"
    ),
    "formal_r3_ledger": Path(
        "artifacts/paper_mvp_stage6b_formal_train_r0/"
        "DC-STAGE6B-R0-FORMAL-TRAIN-20260811-R3/FORMAL_TRAIN_LEDGER.json"
    ),
    "old_e1_final_receipt": Path(
        "reports/grounded_language_v1_extension_e1/EXTENSION_FINAL_RECEIPT.json"
    ),
    "old_e1_hashes": Path(
        "reports/grounded_language_v1_extension_e1/ARTIFACT_HASHES.json"
    ),
}
PROTECTED_HASHES = {
    "stage6a_freeze": "d24502ffe5f81340ff8cc0f83acab23ac620d13a4ceeb060371f1dac838ebfee",
    "stage6b_r0_freeze": "e0c8db7b3afaeb92776e797ea92dde76e2c64158adec3d22e9d6968639ec1e90",
    "formal_r3_ledger": "91ca1c2e1e58facfaab018b661857f1f919b368495dde09e93c8a15f2966e362",
    "old_e1_final_receipt": "c1c71f821ea35f5b4fad9097aaf26c7f1500edb2df0d515e799ddcffb3397704",
    "old_e1_hashes": "a8999a68acd6d03d7273d0c2483623b71e598de1dbafa32a1bebd07c63b9b9bd",
}

# Runtime-visible physical facts.  No expected decision, gold referent, gold
# target, or candidate index is present.  ASK target identities are derived at
# runtime from the live map and route deque, never from this table.
PHYSICAL_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "fixture_id": "E1R1-ACT-PHYS-001",
        "mechanism_family": "ACT",
        "town": "Town03",
        "route_id": "99103001",
        "instruction": "Continue past the white van.",
        "actors": (
            ("vehicle.mercedes.sprinter", -6.10, 114.5, 0.30, 89.6),
            ("vehicle.volkswagen.t2_2021", -12.80, 154.0, 0.30, 89.6),
        ),
        "motion_source": "STATIC_LANDMARKS",
    },
    {
        "fixture_id": "E1R1-ACT-PHYS-002",
        "mechanism_family": "ACT",
        "town": "Town03",
        "route_id": "99103002",
        "instruction": "Proceed past the white van.",
        "actors": (
            ("vehicle.mercedes.sprinter", -6.35, 116.0, 0.30, 89.6),
        ),
        "motion_source": "STATIC_LANDMARKS",
    },
    {
        "fixture_id": "E1R1-ACT-PHYS-003",
        "mechanism_family": "ACT",
        "town": "Town03",
        "route_id": "99103003",
        "instruction": "Continue past the white van ahead.",
        "actors": (
            ("vehicle.volkswagen.t2_2021", -12.65, 113.0, 0.30, 89.6),
            ("vehicle.mercedes.sprinter", -6.00, 155.0, 0.30, 89.6),
        ),
        "motion_source": "STATIC_LANDMARKS",
    },
    {
        "fixture_id": "E1R1-ASK-PHYS-001",
        "mechanism_family": "ASK",
        "town": "Town03",
        "route_id": "99103101",
        "instruction": "Turn after the white van.",
        "actors": (
            ("vehicle.mercedes.sprinter", -6.05, 114.0, 0.30, 89.6),
            ("vehicle.mercedes.sprinter", -12.70, 145.0, 0.30, 89.6),
        ),
        "motion_source": "STATIC_LANDMARKS",
    },
    {
        "fixture_id": "E1R1-ASK-PHYS-002",
        "mechanism_family": "ASK",
        "town": "Town03",
        "route_id": "99103102",
        "instruction": "Turn after the white van ahead.",
        "actors": (
            ("vehicle.volkswagen.t2_2021", -12.55, 115.5, 0.30, 89.6),
            ("vehicle.mercedes.sprinter", -6.20, 146.0, 0.30, 89.6),
        ),
        "motion_source": "STATIC_LANDMARKS",
    },
    {
        "fixture_id": "E1R1-ASK-PHYS-003",
        "mechanism_family": "ASK",
        "town": "Town03",
        "route_id": "99103103",
        "instruction": "Turn after the white van on this approach.",
        "actors": (
            ("vehicle.mercedes.sprinter", -6.45, 118.0, 0.30, 89.6),
            ("vehicle.mercedes.sprinter", -12.45, 147.0, 0.30, 89.6),
        ),
        "motion_source": "STATIC_LANDMARKS",
    },
    {
        "fixture_id": "E1R1-WAIT-PHYS-001",
        "mechanism_family": "WAIT",
        "town": "Town05",
        "route_id": "99105001",
        "instruction": "Turn after the bus clears.",
        "actors": (("vehicle.mitsubishi.fusorosa", 107.084907532, -2.079092503, 0.316792309, 178.687240601),),
        "motion_source": "SCENARIO_AUTONOMOUS_ROUTE",
        "motion": {"visible_dwell_seconds": 0.25, "speed_mps": 8.0, "distance_m": 35.0},
    },
    {
        "fixture_id": "E1R1-WAIT-PHYS-002",
        "mechanism_family": "WAIT",
        "town": "Town05",
        "route_id": "99105002",
        "instruction": "Turn once the bus clears.",
        "actors": (("vehicle.mitsubishi.fusorosa", 107.18, -1.82, 0.316792309, 178.687240601),),
        "motion_source": "SCENARIO_AUTONOMOUS_ROUTE",
        "motion": {"visible_dwell_seconds": 0.15, "speed_mps": 7.0, "distance_m": 32.0},
    },
    {
        "fixture_id": "E1R1-WAIT-PHYS-003",
        "mechanism_family": "WAIT",
        "town": "Town05",
        "route_id": "99105003",
        "instruction": "Proceed after the bus clears.",
        "actors": (("vehicle.mitsubishi.fusorosa", 106.96, -2.34, 0.316792309, 178.687240601),),
        "motion_source": "SCENARIO_AUTONOMOUS_ROUTE",
        "motion": {"visible_dwell_seconds": 0.35, "speed_mps": 9.0, "distance_m": 38.0},
    },
)

RUNTIME_FORBIDDEN_KEYS = frozenset(
    {
        "expected_decision",
        "expected_mechanism",
        "gold_candidate_index",
        "gold_intended_referent",
        "gold_target",
        "gold_branch",
        "evaluator_labels",
    }
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_runtime_clean(value: Any, path: str = "runtime") -> None:
    if isinstance(value, Mapping):
        overlap = {str(key).casefold() for key in value}.intersection(RUNTIME_FORBIDDEN_KEYS)
        if overlap:
            raise ValueError("E1R1_RUNTIME_GOLD_KEY:{}:{}".format(path, ",".join(sorted(overlap))))
        for key, item in value.items():
            assert_runtime_clean(item, path + "." + str(key))
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            assert_runtime_clean(item, path + "[{}]".format(index))


def physical_fixture(fixture_id: str) -> dict[str, Any]:
    rows = [dict(item) for item in PHYSICAL_FIXTURES if item["fixture_id"] == fixture_id]
    if len(rows) != 1:
        raise ValueError("E1R1_PHYSICAL_FIXTURE_BINDING_NOT_ONE:" + fixture_id)
    return rows[0]


__all__ = [
    "ARTIFACT_ROOT",
    "FEATURE_FLAG",
    "FIXTURE_ROOT",
    "MECHANISMS",
    "METHODS",
    "PHYSICAL_FIXTURES",
    "PROTECTED_HASHES",
    "PROTECTED_PATHS",
    "REPORT_ROOT",
    "SCENARIO_ROOT",
    "SCHEMA_PREFIX",
    "assert_runtime_clean",
    "canonical_sha256",
    "file_sha256",
    "physical_fixture",
]
