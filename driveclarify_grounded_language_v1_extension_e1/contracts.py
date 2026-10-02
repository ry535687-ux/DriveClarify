"""Strict contracts for the independent Grounded Language V1 extension."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_PREFIX = "driveclarify.grounded_language_v1_extension_e1"
EXPERIMENT_ID = "GROUNDDED_LANGUAGE_V1_EXTENSION_EXPERIMENT_E1"
FEATURE_FLAG = "DRIVECLARIFY_GROUNDED_LANGUAGE_V1"
EXTENSION_METHODS = (
    "original_simlingo",
    "never_ask",
    "always_ask",
    "always_wait",
    "driveclarify_r0",
    "driveclarify_grounded_v1",
)
BASELINE_METHOD_MAP = {
    "original_simlingo": "original_simlingo",
    "never_ask": "never_ask",
    "always_ask": "always_ask",
    "always_wait": "always_wait",
    "driveclarify_r0": "driveclarify",
}
SPLITS = ("train", "dev", "test")
MECHANISMS = ("ACT", "ASK", "WAIT")

REPORT_ROOT = Path("reports/grounded_language_v1_extension_e1")
ARTIFACT_ROOT = Path("artifacts/grounded_language_v1_extension_e1")
CATALOG_PATH = REPORT_ROOT / "EXTENSION_SCENARIO_CATALOG.json"
SPLIT_PATH = REPORT_ROOT / "EXTENSION_SPLIT.json"
METHODS_PATH = REPORT_ROOT / "EXTENSION_METHODS.json"
SEEDS_PATH = REPORT_ROOT / "EXTENSION_SEEDS.json"
FIREWALL_PATH = REPORT_ROOT / "EXTENSION_LABEL_FIREWALL.json"
FREEZE_PATH = REPORT_ROOT / "EXTENSION_PREEXECUTION_FREEZE.json"
LEDGER_PATH = REPORT_ROOT / "EXTENSION_TRAIN_LEDGER.json"

PROTECTED_PATHS = {
    "stage6a_freeze": Path(
        "reports/paper_mvp_stage6a_final_freeze_v1/FINAL_STAGE6A_FREEZE_RECEIPT.json"
    ),
    "stage6b_freeze": Path(
        "reports/paper_mvp_stage6b_runtime_contract_freeze_v1/STAGE6B_R0_FREEZE_RECEIPT.json"
    ),
    "formal_r3_ledger": Path(
        "artifacts/paper_mvp_stage6b_formal_train_r0/"
        "DC-STAGE6B-R0-FORMAL-TRAIN-20260811-R3/FORMAL_TRAIN_LEDGER.json"
    ),
    "generated_scenario_tree": Path(
        "reports/grounded_language_v1_controlled_integration/"
        "FROZEN_INTEGRITY_AUDIT.json"
    ),
}
PROTECTED_HASHES = {
    "stage6a_freeze": "d24502ffe5f81340ff8cc0f83acab23ac620d13a4ceeb060371f1dac838ebfee",
    "stage6b_freeze": "e0c8db7b3afaeb92776e797ea92dde76e2c64158adec3d22e9d6968639ec1e90",
    "formal_r3_ledger": "91ca1c2e1e58facfaab018b661857f1f919b368495dde09e93c8a15f2966e362",
}

RUNTIME_FORBIDDEN_KEYS = frozenset(
    {
        "expected_mechanism",
        "expected_decision",
        "gold_referents",
        "gold_referent_count",
        "gold_target",
        "gold_targets",
        "gold_candidate",
        "gold_candidate_index",
        "gold_answer_index",
        "evaluator_annotation",
        "evaluator_labels",
    }
)


class ExtensionContractError(ValueError):
    """Raised before a split, label, or frozen-integrity boundary can be crossed."""


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


def assert_runtime_projection_clean(value: Any, path: str = "runtime") -> None:
    if isinstance(value, Mapping):
        overlap = {str(key).casefold() for key in value}.intersection(
            RUNTIME_FORBIDDEN_KEYS
        )
        if overlap:
            raise ExtensionContractError(
                "EXTENSION_RUNTIME_GOLD_KEY:" + path + ":" + ",".join(sorted(overlap))
            )
        for key, item in value.items():
            assert_runtime_projection_clean(item, path + "." + str(key))
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            assert_runtime_projection_clean(item, path + "[" + str(index) + "]")


def assert_train_only(split: str) -> None:
    if str(split).casefold() != "train":
        raise ExtensionContractError(
            "EXTENSION_SPLIT_FIREWALL_REJECTED:" + str(split).upper()
        )


def assert_exact_methods(methods: Sequence[str]) -> None:
    if tuple(methods) != EXTENSION_METHODS:
        raise ExtensionContractError("EXTENSION_EXACT_SIX_METHODS_REQUIRED")


__all__ = [
    "ARTIFACT_ROOT",
    "BASELINE_METHOD_MAP",
    "CATALOG_PATH",
    "EXPERIMENT_ID",
    "EXTENSION_METHODS",
    "ExtensionContractError",
    "FEATURE_FLAG",
    "FIREWALL_PATH",
    "FREEZE_PATH",
    "LEDGER_PATH",
    "MECHANISMS",
    "METHODS_PATH",
    "PROTECTED_HASHES",
    "PROTECTED_PATHS",
    "REPORT_ROOT",
    "RUNTIME_FORBIDDEN_KEYS",
    "SCHEMA_PREFIX",
    "SEEDS_PATH",
    "SPLITS",
    "SPLIT_PATH",
    "assert_exact_methods",
    "assert_runtime_projection_clean",
    "assert_train_only",
    "canonical_sha256",
    "file_sha256",
]
