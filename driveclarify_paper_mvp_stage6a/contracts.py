"""Pure contracts for the Stage 6A implementation-freeze aggregator.

The aggregator is deliberately evidence-only.  It does not execute CARLA,
models, evaluation episodes, or regression commands, and it never opens the
frozen scenario catalog or evaluator-private manifests.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


REPORT_SCHEMA_VERSION = "driveclarify.paper_mvp_stage6a_implementation_freeze.v1"
FULL_REGRESSION_RECEIPT_SCHEMA_VERSION = (
    "driveclarify.paper_mvp_stage6a_full_regression_receipt.v1"
)

BASELINE_FREEZE_FILENAME = "BASELINE_FREEZE.md"
BASELINE_CONFIG_FILENAME = "BASELINE_CONFIG.yaml"
BASELINE_HASHES_FILENAME = "BASELINE_HASHES.json"
IMPLEMENTATION_FREEZE_REPORT_MD_FILENAME = "STAGE6A_IMPLEMENTATION_FREEZE_REPORT.md"
IMPLEMENTATION_FREEZE_REPORT_JSON_FILENAME = "STAGE6A_IMPLEMENTATION_FREEZE_REPORT.json"

READY_STATUS = "READY_STAGE6B_EXECUTION"

GATE_ORDER = (
    "scenario_live_execution",
    "runtime_candidate_generation",
    "live_authority_binding",
    "baseline_v2_freeze",
    "full_regression",
)

OVERALL_BLOCKED_STATUS = {
    "scenario_live_execution": "BLOCKED_STAGE6A_SCENARIO_LIVE_EXECUTION",
    "runtime_candidate_generation": "BLOCKED_STAGE6A_RUNTIME_CANDIDATE_GENERATION",
    "live_authority_binding": "BLOCKED_STAGE6A_LIVE_AUTHORITY_BINDING",
    "baseline_v2_freeze": "BLOCKED_STAGE6A_BASELINE_V2_FREEZE",
    "full_regression": "BLOCKED_STAGE6A_FULL_REGRESSION",
}

# These are private evaluation payload fields, not ordinary words such as
# "test" or audit counters such as ``evaluation_label_access_count``.  A
# receipt containing one of these keys is rejected before any value is copied
# into an output artifact.
PRIVATE_LABEL_KEYS = frozenset(
    {
        "expected_decision",
        "expected_decision_for_validation",
        "decision_reason",
        "ground_truth_reason",
        "gold_label",
        "gold_decision",
        "test_label",
        "test_labels",
        "candidate_interpretations",
        "candidate_consequence_summary",
        "candidate_consequence_linkage",
        "query_value_expectation",
        "wait_value_expectation",
        "answer_impact",
        "future_information_impact",
        "future_information_resolution_oracle_by_seed",
        "wait_evidence",
        "candidate_order_by_seed",
    }
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_YAML_PLAIN_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_MAX_RECEIPT_BYTES = 64 * 1024 * 1024


class Stage6AFreezeError(ValueError):
    """Fail-closed contract error with a stable, non-sensitive reason code."""


@dataclass(frozen=True)
class LoadedReceipt:
    """An explicitly supplied receipt and its content identity."""

    filename: str
    sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class GateResult:
    """Sanitized result for one Stage 6A readiness gate."""

    gate_id: str
    passed: bool
    status: str
    reason_codes: tuple[str, ...]
    receipt_filename: str | None = None
    receipt_sha256: str | None = None
    evidence: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.gate_id not in GATE_ORDER:
            raise Stage6AFreezeError("UNKNOWN_GATE_ID")
        if type(self.passed) is not bool:
            raise Stage6AFreezeError("GATE_PASS_FLAG_MUST_BE_BOOLEAN")
        if not self.status.startswith("PASS_") and not self.status.startswith(
            "BLOCKED_"
        ):
            raise Stage6AFreezeError("GATE_STATUS_PREFIX_INVALID")
        if self.passed is not self.status.startswith("PASS_"):
            raise Stage6AFreezeError("GATE_STATUS_PASS_FLAG_MISMATCH")
        if not self.reason_codes or any(
            type(item) is not str or not item for item in self.reason_codes
        ):
            raise Stage6AFreezeError("GATE_REASON_CODES_REQUIRED")
        if self.receipt_sha256 is not None and not is_sha256(self.receipt_sha256):
            raise Stage6AFreezeError("GATE_RECEIPT_SHA256_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "reason_codes": list(self.reason_codes),
            "receipt": (
                None
                if self.receipt_sha256 is None
                else {
                    "filename": self.receipt_filename,
                    "sha256": self.receipt_sha256,
                }
            ),
            "evidence": dict(self.evidence or {}),
        }


def is_sha256(value: Any) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + ("\n" if pretty else "")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Stage6AFreezeError("RECEIPT_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _assert_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise Stage6AFreezeError("RECEIPT_NONFINITE_NUMBER")
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_finite(item)
    elif isinstance(value, list):
        for item in value:
            _assert_finite(item)


def _contains_private_label_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in PRIVATE_LABEL_KEYS:
                return True
            if _contains_private_label_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_private_label_key(item) for item in value)
    return False


def load_explicit_receipt(path: str | Path) -> LoadedReceipt:
    """Load one explicit JSON receipt without retaining its source path.

    Only the basename and SHA-256 are eligible for the aggregate report.  This
    prevents report generation from copying arbitrary receipt payloads or
    evaluator-private paths.
    """

    source = Path(path)
    if not source.is_file():
        raise Stage6AFreezeError("RECEIPT_FILE_MISSING")
    size = source.stat().st_size
    if size <= 0 or size > _MAX_RECEIPT_BYTES:
        raise Stage6AFreezeError("RECEIPT_SIZE_INVALID")
    raw = source.read_bytes()
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except Stage6AFreezeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage6AFreezeError("RECEIPT_JSON_INVALID") from exc
    if not isinstance(payload, Mapping):
        raise Stage6AFreezeError("RECEIPT_ROOT_MUST_BE_OBJECT")
    _assert_finite(payload)
    if _contains_private_label_key(payload):
        raise Stage6AFreezeError("RECEIPT_PRIVATE_LABEL_FIELD_FORBIDDEN")
    return LoadedReceipt(
        filename=source.name,
        sha256=hashlib.sha256(raw).hexdigest(),
        payload=payload,
    )


def atomic_write(path: str | Path, payload: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise Stage6AFreezeError("YAML_NONFINITE_NUMBER")
        return json.dumps(value, allow_nan=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise Stage6AFreezeError("YAML_UNSUPPORTED_SCALAR")


def _yaml_key(value: Any) -> str:
    text = str(value)
    return text if _YAML_PLAIN_KEY_RE.fullmatch(text) else _yaml_scalar(text)


def _yaml_lines(value: Any, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, Mapping):
        if not value:
            return [prefix + "{}"]
        lines: list[str] = []
        for key, item in value.items():
            key_text = _yaml_key(key)
            if isinstance(item, Mapping) and item:
                lines.append(f"{prefix}{key_text}:")
                lines.extend(_yaml_lines(item, indent + 2))
            elif isinstance(item, list) and item:
                lines.append(f"{prefix}{key_text}:")
                lines.extend(_yaml_lines(item, indent + 2))
            elif isinstance(item, Mapping):
                lines.append(f"{prefix}{key_text}: {{}}")
            elif isinstance(item, list):
                lines.append(f"{prefix}{key_text}: []")
            else:
                lines.append(f"{prefix}{key_text}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [prefix + "[]"]
        lines = []
        for item in value:
            if isinstance(item, (Mapping, list)) and item:
                lines.append(prefix + "-")
                lines.extend(_yaml_lines(item, indent + 2))
            elif isinstance(item, Mapping):
                lines.append(prefix + "- {}")
            elif isinstance(item, list):
                lines.append(prefix + "- []")
            else:
                lines.append(prefix + "- " + _yaml_scalar(item))
        return lines
    return [prefix + _yaml_scalar(value)]


def yaml_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize a deterministic block-style YAML document using stdlib only."""

    return ("---\n" + "\n".join(_yaml_lines(value, 0)) + "\n").encode("utf-8")


__all__ = [
    "BASELINE_CONFIG_FILENAME",
    "BASELINE_FREEZE_FILENAME",
    "BASELINE_HASHES_FILENAME",
    "FULL_REGRESSION_RECEIPT_SCHEMA_VERSION",
    "GATE_ORDER",
    "GateResult",
    "IMPLEMENTATION_FREEZE_REPORT_JSON_FILENAME",
    "IMPLEMENTATION_FREEZE_REPORT_MD_FILENAME",
    "LoadedReceipt",
    "OVERALL_BLOCKED_STATUS",
    "READY_STATUS",
    "REPORT_SCHEMA_VERSION",
    "Stage6AFreezeError",
    "atomic_write",
    "canonical_json_bytes",
    "canonical_sha256",
    "file_sha256",
    "is_sha256",
    "load_explicit_receipt",
    "yaml_bytes",
]
