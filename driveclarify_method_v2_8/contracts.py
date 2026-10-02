"""Identity-complete selected-plan transaction for DriveClarify V2.8."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from driveclarify_method_revision_v2 import canonical_identity_digest


def _required(value: Any, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(name + "_MISSING")
    return normalized


@dataclass(frozen=True)
class SelectedPlanTransactionV28:
    """Atomic identity binding from matched answer through commitment."""

    candidate_id: str
    interpretation_id: str
    obligation_identity: str
    obligation_digest: str
    selected_branch_or_opportunity_identity: str
    selected_branch_digest: str
    downstream_landing_identity_digest: str
    navigation_context_identity: str
    global_destination_identity: str
    mission_context_digest: str
    route_version: str
    environment_digest: str
    fresh_plan_id: str
    fresh_plan_route_digest: str
    fresh_plan_speed_digest: str
    source_observation_id: str
    source_frame_id: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            object.__setattr__(self, name, _required(value, name.upper()))
        if self.obligation_identity != self.obligation_digest:
            raise ValueError("V2_8_OBLIGATION_IDENTITY_DIGEST_MISMATCH")

    @property
    def identity_digest(self) -> str:
        return canonical_identity_digest(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.update(
            {
                "schema_version": "driveclarify.method_v2_8.selected_plan_transaction.v1",
                "identity_digest": self.identity_digest,
                "candidate_vehiclecontrol_authority": False,
                "new_planner_count": 0,
                "new_controller_count": 0,
            }
        )
        return value
