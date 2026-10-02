"""Buffered default-off production observer for RQ2-T."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from .measurement import DeadlineContract, adapt_production_history_row, finalize_episode


FEATURE_FLAG = "DRIVECLARIFY_RQ2_T_TEMPORAL_OBSERVER"
OUTPUT_ENV = "DRIVECLARIFY_RQ2_T_OUTPUT_DIR"


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


class RuntimeTemporalEvidenceObserver:
    """Records already-computed decision evidence; never computes control."""

    def __init__(
        self,
        *,
        output_dir: Path,
        scene_id: str,
        episode_id: str,
        seed: int,
        ambiguity_type: str,
        map_name: str,
        route_identity: str,
        commitment_certificate_sha256: str,
        deadline_contract: DeadlineContract,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.scene_id = str(scene_id)
        self.episode_id = str(episode_id)
        self.seed = int(seed)
        self.ambiguity_type = str(ambiguity_type)
        self.map_name = str(map_name)
        self.route_identity = str(route_identity)
        self.commitment_certificate_sha256 = str(commitment_certificate_sha256)
        self.deadline_contract = deadline_contract
        self.rows: list[dict[str, Any]] = []
        self.errors: list[dict[str, str]] = []

    @classmethod
    def from_environment(cls, *, default_output_dir: Path) -> "RuntimeTemporalEvidenceObserver":
        required = {
            "scene_id": os.environ.get("DRIVECLARIFY_RQ2_T_SCENE_ID"),
            "episode_id": os.environ.get("DRIVECLARIFY_RQ2_T_EPISODE_ID"),
            "seed": os.environ.get("DRIVECLARIFY_RQ2_T_ENGINEERING_SEED"),
            "ambiguity_type": os.environ.get("DRIVECLARIFY_RQ2_T_AMBIGUITY_TYPE"),
            "map_name": os.environ.get("DRIVECLARIFY_RQ2_T_MAP"),
            "route_identity": os.environ.get("DRIVECLARIFY_RQ2_T_ROUTE_IDENTITY"),
            "commitment_certificate_sha256": os.environ.get(
                "DRIVECLARIFY_RQ2_T_COMMITMENT_CERTIFICATE_SHA256"
            ),
        }
        missing = tuple(key for key, value in required.items() if not value)
        if missing:
            raise ValueError("RQ2_T_OBSERVER_ENV_MISSING:" + ",".join(missing))
        output = Path(os.environ.get(OUTPUT_ENV) or default_output_dir / "rq2_t_temporal")
        return cls(
            output_dir=output,
            scene_id=str(required["scene_id"]),
            episode_id=str(required["episode_id"]),
            seed=int(str(required["seed"])),
            ambiguity_type=str(required["ambiguity_type"]),
            map_name=str(required["map_name"]),
            route_identity=str(required["route_identity"]),
            commitment_certificate_sha256=str(required["commitment_certificate_sha256"]),
            deadline_contract=DeadlineContract(
                answer_latency_simulation_s=float(
                    os.environ.get("DRIVECLARIFY_RQ2_T_ANSWER_LATENCY_SIM_S", "0.5")
                ),
                answer_to_first_eligible_action_ticks=int(
                    os.environ.get("DRIVECLARIFY_RQ2_T_ACTION_RESERVE_TICKS", "11")
                ),
                control_reserve_ticks=int(
                    os.environ.get("DRIVECLARIFY_RQ2_T_CONTROL_RESERVE_TICKS", "3")
                ),
                fixed_delta_seconds=float(
                    os.environ.get("DRIVECLARIFY_RQ2_T_FIXED_DELTA_SECONDS", "0.05")
                ),
            ),
        )

    def observe(self, history_row: Mapping[str, Any], *, simulation_time_s: float) -> None:
        try:
            row = adapt_production_history_row(
                history_row,
                simulation_time_s=simulation_time_s,
                ambiguity_type=self.ambiguity_type,
                scene_id=self.scene_id,
                episode_id=self.episode_id,
                seed=self.seed,
                map_name=self.map_name,
                route_identity=self.route_identity,
                commitment_certificate_sha256=self.commitment_certificate_sha256,
            )
            self.rows.append(row)
        except Exception as error:  # observational and deliberately fail-open
            self.errors.append(
                {"type": type(error).__name__, "message": str(error)}
            )

    def close(
        self,
        *,
        commitment_time_simulation_s: Optional[float],
        commitment_reached: bool,
        right_censored: bool = False,
        execution_terminal_state: Optional[str] = None,
        simulation_elapsed_s: Optional[float] = None,
        wall_timeout_observed: bool = False,
    ) -> dict[str, Any]:
        raw = "".join(json.dumps(row, sort_keys=True) + "\n" for row in self.rows)
        _atomic_text(self.output_dir / "TEMPORAL_EVIDENCE_RAW.jsonl", raw)
        receipt: dict[str, Any]
        if not self.rows:
            receipt = {
                "status": "INVALID_ENGINEERING_EVIDENCE",
                "reason": "NO_VALID_TEMPORAL_OBSERVATIONS",
                "observer_errors": self.errors,
            }
            _atomic_text(
                self.output_dir / "TEMPORAL_EVIDENCE_RECEIPT.json",
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            )
            return receipt
        rows, summary = finalize_episode(
            self.rows,
            deadline_contract=self.deadline_contract,
            commitment_time_simulation_s=commitment_time_simulation_s,
            commitment_reached=commitment_reached,
            right_censored=right_censored,
            invalid_engineering_evidence=bool(self.errors),
            execution_terminal_state=execution_terminal_state,
            simulation_elapsed_s=simulation_elapsed_s,
            wall_timeout_observed=wall_timeout_observed,
        )
        finalized = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        _atomic_text(self.output_dir / "TEMPORAL_EVIDENCE.jsonl", finalized)
        receipt = dict(summary)
        receipt.update(
            {
                "status": "PASS_OBSERVATIONAL_MEASUREMENT_FLUSHED",
                "observer_errors": self.errors,
                "extra_model_forward_count": 0,
                "extra_planner_advance_count": 0,
                "extra_pid_count": 0,
                "extra_control_writer_count": 0,
            }
        )
        _atomic_text(
            self.output_dir / "TEMPORAL_EVIDENCE_RECEIPT.json",
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        )
        return receipt


__all__ = ["FEATURE_FLAG", "OUTPUT_ENV", "RuntimeTemporalEvidenceObserver"]
