"""Shared constants and strict serialization helpers for Stage 6A fixtures."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
FREEZE_DIR = ROOT / "reports/paper_mvp_scenario_freeze_v0"
CATALOG_PATH = FREEZE_DIR / "SCENARIO_CATALOG.json"
SPLIT_PATH = FREEZE_DIR / "SCENARIO_SPLIT.json"
FREEZE_EVIDENCE_PATH = FREEZE_DIR / "EVIDENCE_INDEX.json"

EXPECTED_CATALOG_FILE_SHA256 = (
    "c5ff762019d5e36e3f0752d6785924c94f3e096669cc93d9b91f6397ee7fbb75"
)
EXPECTED_SCENARIO_PAYLOAD_SHA256 = (
    "a639cc6de677d188385b22f0eb52f876a601f4ee95fd000ebcaeb1da66935a73"
)
EXPECTED_SCENARIO_DEFINITION_SHA256 = (
    "eddaa68b32f2eaa82bf75b08e4d2645e65a0b943755cfcd30de1df1bf05eccb9"
)
EXPECTED_SPLIT_FILE_SHA256 = (
    "760ee733421e744fbb2159abf0029dd03fd124ae43b8309beb57817a588f784e"
)

SCHEMA_VERSION = "driveclarify.paper_mvp_stage6a_scenario_manifest.v0"
RUNTIME_SCHEMA_VERSION = "driveclarify.paper_mvp_stage6a_runtime_fixture.v0"
PRIVATE_SCHEMA_VERSION = "driveclarify.paper_mvp_stage6a_evaluator_private.v0"
ROUTE_SCENARIO_TYPE = "DriveClarifyPaperMVPScenario"
STATIC_STATUS = "PASS_STATIC_COMPILATION_24_OF_24_BLOCKED_LIVE_RESOLUTION"


class ScenarioFixtureContractError(RuntimeError):
    """Fail-closed contract error with a stable reason code."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ScenarioFixtureContractError(reason)


def _reject_constant(value: str) -> None:
    raise ScenarioFixtureContractError(f"NONFINITE_JSON_CONSTANT:{value}")


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScenarioFixtureContractError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
    except ScenarioFixtureContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScenarioFixtureContractError(f"JSON_READ_FAILED:{source}") from exc
    require(isinstance(value, dict), f"JSON_OBJECT_REQUIRED:{source}")
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def verify_embedded_sha256(value: Mapping[str, Any]) -> bool:
    recorded = value.get("sha256")
    unsigned = dict(value)
    unsigned.pop("sha256", None)
    return isinstance(recorded, str) and recorded == canonical_sha256(unsigned)


def assert_finite(value: Any, path: str = "$") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        require(math.isfinite(value), f"NONFINITE_NUMBER:{path}")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            assert_finite(child, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_finite(child, f"{path}[{index}]")
        return
    raise ScenarioFixtureContractError(f"UNSUPPORTED_VALUE_TYPE:{path}:{type(value).__name__}")


def walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys(child)


def relative_to_root(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ScenarioFixtureContractError(f"PATH_OUTSIDE_REPOSITORY:{resolved}") from exc
