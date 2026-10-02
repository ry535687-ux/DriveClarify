"""加载人工 fixtures；本模块绝不推导、生成或回填 expected labels。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = PROJECT_ROOT / "tests/offline_v0/fixtures/fixture_cases.json"
REQUIRED_PROVENANCE = {
    "SYNTHETIC_TEST_ONLY",
    "NOT_CARLA_MEASURED",
    "NOT_HUMAN_RESPONSE_DATA",
    "NOT_A_SAFETY_THRESHOLD",
}
LABEL_AUTHOR = "DRIVECLARIFY_V0_1_DESIGN_CONTRACT"
LABEL_BASIS = "HAND_AUTHORED_FROM_FROZEN_RULE_TABLE"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_fixture_document(path: Path = DEFAULT_FIXTURE_PATH) -> dict[str, Any]:
    """在 evaluator 执行前验证 provenance 与人工标签元数据。"""

    document = json.loads(path.read_text(encoding="utf-8"))
    labels = set(document.get("provenance_labels", []))
    if labels != REQUIRED_PROVENANCE:
        raise ValueError("FIXTURE_PROVENANCE_LABELS_INVALID")
    ids: set[str] = set()
    for case in document["cases"]:
        fixture_id = case.get("fixture_id")
        if not fixture_id or fixture_id in ids:
            raise ValueError("FIXTURE_ID_INVALID_OR_DUPLICATE")
        ids.add(fixture_id)
        if case.get("label_author") != LABEL_AUTHOR:
            raise ValueError(f"FIXTURE_LABEL_AUTHOR_INVALID:{fixture_id}")
        if case.get("label_basis") != LABEL_BASIS:
            raise ValueError(f"FIXTURE_LABEL_BASIS_INVALID:{fixture_id}")
        if case.get("reviewed_before_execution") is not True:
            raise ValueError(f"FIXTURE_NOT_PREREVIEWED:{fixture_id}")
        expected = case.get("expected", {})
        if set(expected) != {"decision", "reason_code", "state"}:
            raise ValueError(f"FIXTURE_EXPECTED_FIELDS_INVALID:{fixture_id}")
    return document


def _resolve_policy_case(document: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    # 基础记录加显式覆盖只是输入去重；解析后每条 consequence 均为完整记录。
    base = deepcopy(document["base_policy_input"])
    profiles = document["consequence_profiles"]
    consequences = []
    for profile_name in case.get("candidate_profiles", []):
        record = deep_merge(document["base_consequence"], profiles[profile_name])
        consequences.append(record)

    base["consequences"] = consequences
    base["candidate_cache"]["candidate_ids"] = [
        record["candidate_id"] for record in consequences
    ]
    resolved = deep_merge(base, case.get("input_overrides", {}))
    for index_text, override in case.get("consequence_overrides", {}).items():
        index = int(index_text)
        resolved["consequences"][index] = deep_merge(
            resolved["consequences"][index], override
        )
    return resolved


def resolve_case(document: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """解析输入，不读取 evaluator 输出，也不触碰 expected 字段。"""

    kind = case.get("kind", "policy")
    if kind == "policy":
        resolved = _resolve_policy_case(document, case)
    else:
        resolved = deepcopy(case["input"])
        resolved["kind"] = kind
    resolved["fixture_id"] = case["fixture_id"]
    resolved["synthetic_provenance"] = sorted(REQUIRED_PROVENANCE)
    return resolved


def iter_resolved_cases(path: Path = DEFAULT_FIXTURE_PATH):
    document = load_fixture_document(path)
    for case in document["cases"]:
        yield case, resolve_case(document, case)
