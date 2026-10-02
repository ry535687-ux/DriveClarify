"""Canonical, policy-inert encoding for mandatory RQ2 native receipts."""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping

from driveclarify_t_mvp import baselines as baseline_module
from driveclarify_t_mvp.baselines import (
    FROZEN_V11_ADMISSIBILITY_OWNER,
    FrozenAdmissibility,
)
from driveclarify_t_mvp.canonical import canonical_sha256


FROZEN_ADMISSIBILITY_RECEIPT_SCHEMA = (
    "driveclarify.rq2.frozen_admissibility_receipt.v1"
)
_FACTORY_SEAL_OWNER = (
    "driveclarify_t_mvp.baselines._FROZEN_ADMISSIBILITY_FACTORY_SEAL"
)
_FIELD_NAMES = tuple(field.name for field in fields(FrozenAdmissibility))
_PUBLIC_FIELD_NAMES = tuple(name for name in _FIELD_NAMES if name != "_factory_seal")
_DISPOSITIONS = {
    "COMMIT_NOW",
    "DEFER_COMMIT",
    "REJECT_STALE_OR_INFEASIBLE",
}


def _exact_factory_seal(value: FrozenAdmissibility) -> bool:
    return value._factory_seal is getattr(
        baseline_module, "_FROZEN_ADMISSIBILITY_FACTORY_SEAL"
    )


def _semantic_fields(value: FrozenAdmissibility) -> dict[str, Any]:
    if type(value) is not FrozenAdmissibility:
        raise TypeError("FROZEN_ADMISSIBILITY_RECEIPT_TYPE_MISMATCH")
    if not _exact_factory_seal(value):
        raise RuntimeError("FROZEN_ADMISSIBILITY_RECEIPT_FACTORY_SEAL_MISMATCH")
    return {
        "owner_disposition": value.owner_disposition,
        "same_G": value.same_G,
        "hard_safety_allows": value.hard_safety_allows,
        "reason_codes": list(value.reason_codes),
        "source_owner_identity": value.source_owner_identity,
        "source_result_sha256": value.source_result_sha256,
        "assessed_route_identity": value.assessed_route_identity,
        "owner_registry_sha256": value.owner_registry_sha256,
        "oracle_fields_present": value.oracle_fields_present,
        "_factory_seal": {
            "encoding": "EXACT_FACTORY_SEAL_ATTESTATION",
            "owner_identity": _FACTORY_SEAL_OWNER,
            "runtime_type": "builtins.object",
            "verified_exact_factory_seal": True,
        },
    }


def encode_frozen_admissibility(value: FrozenAdmissibility) -> dict[str, Any]:
    """Encode every dataclass field without exposing the process-local seal id."""

    encoded_fields = _semantic_fields(value)
    receipt = {
        "schema_version": FROZEN_ADMISSIBILITY_RECEIPT_SCHEMA,
        "dataclass_type": (
            "driveclarify_t_mvp.baselines.FrozenAdmissibility"
        ),
        "field_names": list(_FIELD_NAMES),
        "fields": encoded_fields,
        "fields_canonical_sha256": canonical_sha256(encoded_fields),
    }
    validate_frozen_admissibility_receipt(receipt, expected=value)
    return receipt


def reconstruct_frozen_admissibility_semantics(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover the decision-relevant immutable projection from a receipt."""

    validate_frozen_admissibility_receipt(receipt)
    encoded_fields = receipt["fields"]
    return {
        name: (
            tuple(encoded_fields[name])
            if name == "reason_codes"
            else encoded_fields[name]
        )
        for name in _PUBLIC_FIELD_NAMES
    }


def validate_frozen_admissibility_receipt(
    receipt: Mapping[str, Any],
    *,
    expected: FrozenAdmissibility | None = None,
) -> None:
    """Fail closed on field loss, coercion, seal ambiguity, or hash drift."""

    if set(receipt) != {
        "schema_version",
        "dataclass_type",
        "field_names",
        "fields",
        "fields_canonical_sha256",
    }:
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_SCHEMA_KEYS_INVALID")
    if receipt["schema_version"] != FROZEN_ADMISSIBILITY_RECEIPT_SCHEMA:
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_SCHEMA_VERSION_INVALID")
    if receipt["dataclass_type"] != (
        "driveclarify_t_mvp.baselines.FrozenAdmissibility"
    ):
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_DATACLASS_TYPE_INVALID")
    if tuple(receipt["field_names"]) != _FIELD_NAMES:
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_FIELD_MANIFEST_INVALID")
    encoded_fields = receipt["fields"]
    if not isinstance(encoded_fields, Mapping) or set(encoded_fields) != set(
        _FIELD_NAMES
    ):
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_FIELD_LOSS")
    if type(encoded_fields["owner_disposition"]) is not str or encoded_fields[
        "owner_disposition"
    ] not in _DISPOSITIONS:
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_DISPOSITION_INVALID")
    for name in ("same_G", "hard_safety_allows", "oracle_fields_present"):
        if type(encoded_fields[name]) is not bool:
            raise TypeError("FROZEN_ADMISSIBILITY_RECEIPT_BOOLEAN_COERCION")
    reasons = encoded_fields["reason_codes"]
    if not isinstance(reasons, list) or not reasons or not all(
        type(reason) is str for reason in reasons
    ):
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_REASONS_INVALID")
    for name in (
        "source_owner_identity",
        "source_result_sha256",
        "assessed_route_identity",
        "owner_registry_sha256",
    ):
        if type(encoded_fields[name]) is not str or not encoded_fields[name]:
            raise TypeError("FROZEN_ADMISSIBILITY_RECEIPT_STRING_COERCION")
    if encoded_fields["source_owner_identity"] != FROZEN_V11_ADMISSIBILITY_OWNER:
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_OWNER_INVALID")
    if len(encoded_fields["source_result_sha256"]) != 64 or len(
        encoded_fields["owner_registry_sha256"]
    ) != 64:
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_HASH_LENGTH_INVALID")
    if encoded_fields["oracle_fields_present"] is not False:
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_ORACLE_FIELD_PRESENT")
    seal = encoded_fields["_factory_seal"]
    if seal != {
        "encoding": "EXACT_FACTORY_SEAL_ATTESTATION",
        "owner_identity": _FACTORY_SEAL_OWNER,
        "runtime_type": "builtins.object",
        "verified_exact_factory_seal": True,
    }:
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_SEAL_ATTESTATION_INVALID")
    if receipt["fields_canonical_sha256"] != canonical_sha256(encoded_fields):
        raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_CANONICAL_HASH_INVALID")
    if expected is not None:
        if type(expected) is not FrozenAdmissibility or not _exact_factory_seal(
            expected
        ):
            raise TypeError("FROZEN_ADMISSIBILITY_RECEIPT_EXPECTED_INVALID")
        expected_fields = _semantic_fields(expected)
        if dict(encoded_fields) != expected_fields:
            raise ValueError("FROZEN_ADMISSIBILITY_RECEIPT_SEMANTIC_MISMATCH")
