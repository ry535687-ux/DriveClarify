"""Experimental wrapper around the frozen V2 field-specific memory contract."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from driveclarify_rq2_t.measurement import canonical_sha256
from driveclarify_rq2_t_v2.memory import FIELD_MEMORY_POLICIES, TemporalEvidenceMemory

from .contracts import ALLOWED_USAGE, EVIDENCE_GRADE, assert_experimental_only


def controlled_label(field: Mapping[str, Any], *, view: str) -> dict[str, Any]:
    value = copy.deepcopy(dict(field))
    value["evidence_grade"] = EVIDENCE_GRADE
    value["allowed_usage"] = ALLOWED_USAGE
    value["controlled_view"] = view
    value["production_eligible"] = False
    value["control_eligible"] = False
    value["safety_authority"] = False
    value.pop("allowed_usage_purpose", None)
    value.pop("evidence_digest", None)
    value["evidence_digest"] = canonical_sha256(value)
    assert_experimental_only(value)
    return value


class ControlledTemporalMemory:
    """Retain only through the already-frozen per-field TTL/binding policies."""

    def __init__(self) -> None:
        self._memory = TemporalEvidenceMemory()

    @property
    def events(self) -> list[dict[str, Any]]:
        return self._memory.events

    def update(
        self,
        fields: Mapping[str, Mapping[str, Any]],
        *,
        now_s: float,
        context: Mapping[str, Any],
        invalidation_events: Sequence[str] = (),
    ) -> dict[str, dict[str, Any]]:
        output = self._memory.update(
            fields, now_s=now_s, context=context,
            invalidation_events=invalidation_events,
        )
        return {field_id: controlled_label(field, view="B2") for field_id, field in output.items()}

    def snapshot(self) -> Mapping[str, Any]:
        value = dict(self._memory.snapshot())
        value["policy_source"] = "driveclarify_rq2_t_v2.memory.FIELD_MEMORY_POLICIES"
        value["policy_digest"] = canonical_sha256({
            key: {
                "max_age_simulation_s": policy.max_age_simulation_s,
                "binding_keys": policy.binding_keys,
                "invalidation_events": policy.invalidation_events,
                "retention_mode": policy.retention_mode,
            }
            for key, policy in FIELD_MEMORY_POLICIES.items()
        })
        return value


__all__ = ["ControlledTemporalMemory", "controlled_label"]
