"""Configuration for the bounded Learned M1 pilot."""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict


EXPERIMENT_ID = "DC-M1-PILOT-P1-20260803T133000Z"
BATCH_ID = "DC-MULTI-A3B3-C1-20260803T121500Z"
VALID_UNITS = (
    "TOWN04_JUNCTION_53_UNIT01",
    "TOWN04_JUNCTION_278_UNIT01",
    "TOWN04_JUNCTION_1452_UNIT01",
    "TOWN05_JUNCTION_1574_UNIT01",
    "TOWN05_JUNCTION_1722_UNIT01",
)
ENGINEERING_EXCLUSION = "TOWN03_JUNCTION_1221_UNIT01"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_batch_root() -> Path:
    return (
        project_root()
        / "reports"
        / "multi_topology_static_units_v1"
        / "offline_candidate_capture_runs"
        / BATCH_ID
    )


def default_output_root() -> Path:
    return project_root() / "reports" / "learned_m1_pilot_v1" / EXPERIMENT_ID


@dataclass(frozen=True)
class PilotConfig:
    experiment_id: str = EXPERIMENT_ID
    batch_id: str = BATCH_ID
    model_variant: str = "V2_SMALL_SHARED_ENCODER_MLP"
    hidden_dim: int = 64
    embedding_dim: int = 32
    optimizer: str = "AdamW"
    learning_rate: float = 1.0e-3
    weight_decay: float = 1.0e-4
    max_steps: int = 2000
    early_stop: bool = True
    early_stop_loss: float = 0.025
    gradient_clip_norm: float = 1.0
    lambda_unknown: float = 0.5
    lambda_repeat: float = 0.1
    seed: int = 17
    additional_diagnostic_seeds: tuple = (29, 43)
    full_unit_level_batch: bool = True
    deterministic_algorithms: bool = True
    checkpoint_cpu_atol: float = 1.0e-6
    checkpoint_cuda_atol: float = 1.0e-5

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
