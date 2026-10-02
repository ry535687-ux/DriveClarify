"""冻结 Draft 2020-12 Schema 的加载与确定性校验。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATHS = {
    "consequence": PROJECT_ROOT / "design/v0/CONSEQUENCE_SCHEMA.json",
    "candidate_cache": PROJECT_ROOT / "design/v0/CANDIDATE_CACHE_SCHEMA.json",
}


class RecordValidationError(ValueError):
    """输入记录违反冻结 Schema 时抛出的显式错误。"""

    def __init__(self, schema_name: str, errors: list[str]):
        self.schema_name = schema_name
        self.errors = tuple(errors)
        super().__init__(f"{schema_name} validation failed: {'; '.join(errors)}")


@lru_cache(maxsize=2)
def load_schema(schema_name: str) -> dict[str, Any]:
    try:
        path = SCHEMA_PATHS[schema_name]
    except KeyError as exc:
        raise KeyError(f"unknown schema: {schema_name}") from exc
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@lru_cache(maxsize=2)
def get_validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(schema_name))


def validate_record(schema_name: str, record: dict[str, Any]) -> None:
    # 固定按字段路径和消息排序，避免不同运行中的错误顺序漂移。
    errors = sorted(
        get_validator(schema_name).iter_errors(record),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    if errors:
        rendered = []
        for error in errors:
            path = ".".join(str(part) for part in error.absolute_path) or "$"
            rendered.append(f"{path}: {error.message}")
        raise RecordValidationError(schema_name, rendered)


def validate_consequence(record: dict[str, Any]) -> None:
    validate_record("consequence", record)


def validate_candidate_cache(record: dict[str, Any]) -> None:
    validate_record("candidate_cache", record)
