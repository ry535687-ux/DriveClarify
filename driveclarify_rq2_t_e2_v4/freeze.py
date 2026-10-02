"""Prospective method/generator/blind exposure immutability guards."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import canonical_sha256


OLD_BLIND_IDENTITIES = frozenset(
    "RQ2TE2V3-ENG-{:03d}".format(value) for value in range(15, 20)
)


def assert_identity_can_be_blind(identity: str) -> None:
    if str(identity) in OLD_BLIND_IDENTITIES:
        raise PermissionError("E2_V4_OLD_EXPOSED_IDENTITY_CANNOT_BE_BLIND_AGAIN")


@dataclass
class BlindFreezeGuard:
    method_digest: str
    parameter_digest: str
    lifecycle_digest: str
    generator_digest: str
    materialized_manifest_digest: str | None = None
    exposed_identities: list[str] = field(default_factory=list)

    def materialize(self, manifest: Mapping[str, Any]) -> str:
        if self.exposed_identities:
            raise RuntimeError("E2_V4_CANNOT_MATERIALIZE_AFTER_EXPOSURE")
        if not all((self.method_digest, self.parameter_digest, self.lifecycle_digest, self.generator_digest)):
            raise RuntimeError("E2_V4_METHOD_PARAMETER_LIFECYCLE_GENERATOR_FREEZE_INCOMPLETE")
        digest = canonical_sha256(manifest)
        if self.materialized_manifest_digest not in (None, digest):
            raise RuntimeError("E2_V4_BLIND_GENERATOR_OR_MANIFEST_MUTATED")
        self.materialized_manifest_digest = digest
        return digest

    def expose(self, identity: str, current: Mapping[str, str]) -> None:
        assert_identity_can_be_blind(identity)
        if self.materialized_manifest_digest is None:
            raise RuntimeError("E2_V4_EXACT_BLIND_SCENES_NOT_MATERIALIZED")
        expected = {
            "method_digest": self.method_digest,
            "parameter_digest": self.parameter_digest,
            "lifecycle_digest": self.lifecycle_digest,
            "generator_digest": self.generator_digest,
            "manifest_digest": self.materialized_manifest_digest,
        }
        if dict(current) != expected:
            raise RuntimeError("E2_V4_SOURCE_PARAMETERS_OR_BLIND_MANIFEST_CHANGED_AFTER_FREEZE")
        if identity in self.exposed_identities:
            raise RuntimeError("E2_V4_BLIND_IDENTITY_EXACTLY_ONCE_VIOLATION")
        self.exposed_identities.append(identity)

    def authorize_method_repair(self, *, valid_blind_failure: bool) -> None:
        if valid_blind_failure:
            raise PermissionError("E2_V4_VALID_BLIND_FAILURE_CANNOT_TRIGGER_METHOD_REPAIR")


__all__ = ["BlindFreezeGuard", "OLD_BLIND_IDENTITIES", "assert_identity_can_be_blind"]
