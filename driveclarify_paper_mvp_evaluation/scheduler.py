"""Hash-bound 768-episode schedule and TRAIN/DEV/TEST one-shot lifecycle."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .contracts import ContractError, METHOD_ORDER
from .freeze import (
    FROZEN_BASELINE_CONFIG,
    baseline_freeze_hash,
    method_config_hash,
    validate_baseline_config,
)
from .hashing import canonical_bytes, canonical_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SPLIT_ORDER = ("train", "dev", "test")


def _require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ContractError(f"{name}_SHA256_REQUIRED")
    return value


def _catalog_hash(catalog: Mapping[str, Any]) -> str:
    for key in ("scenario_payload_sha256", "catalog_sha256"):
        candidate = catalog.get(key)
        if isinstance(candidate, str) and _SHA256.fullmatch(candidate):
            return candidate
    raise ContractError("FROZEN_CATALOG_SHA256_REQUIRED")


def _scenario_identity(scenario: Mapping[str, Any]) -> dict[str, Any]:
    required = ("scenario_id", "split", "seed", "route_id", "town")
    missing = [key for key in required if key not in scenario]
    if missing:
        raise ContractError(f"SCENARIO_IDENTITY_FIELDS_MISSING:{missing}")
    scenario_id = scenario["scenario_id"]
    split = scenario["split"]
    seeds = scenario["seed"]
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ContractError("SCENARIO_ID_REQUIRED")
    if split not in _SPLIT_ORDER:
        raise ContractError(f"INVALID_SPLIT:{split}")
    if (
        not isinstance(seeds, list)
        or len(seeds) != 4
        or len(set(seeds)) != 4
        or not all(isinstance(seed, int) and not isinstance(seed, bool) for seed in seeds)
    ):
        raise ContractError(f"EXACTLY_FOUR_INTEGER_SEEDS_REQUIRED:{scenario_id}")
    fixture_sha = scenario.get("executable_fixture_sha256")
    if fixture_sha is None and isinstance(scenario.get("carla_fixture"), Mapping):
        fixture_sha = scenario["carla_fixture"].get("sha256")
    fixture_sha = _require_sha256(fixture_sha, "EXECUTABLE_FIXTURE")
    instruction_sha = scenario.get("instruction_sha256")
    if instruction_sha is None and isinstance(scenario.get("instruction_text"), str):
        instruction_sha = hashlib.sha256(
            scenario["instruction_text"].encode("utf-8")
        ).hexdigest()
    instruction_sha = _require_sha256(instruction_sha, "INSTRUCTION")
    return {
        "scenario_id": scenario_id,
        "split": split,
        "seeds": sorted(seeds),
        "route_id": str(scenario["route_id"]),
        "town": str(scenario["town"]),
        "executable_fixture_sha256": fixture_sha,
        "instruction_sha256": instruction_sha,
    }


def build_hash_bound_schedule(
    catalog: Mapping[str, Any],
    config: Mapping[str, Any] = FROZEN_BASELINE_CONFIG,
) -> dict[str, Any]:
    """Build the evaluator-only schedule without projecting annotations to policy."""

    validated = validate_baseline_config(config)
    catalog_sha = _catalog_hash(catalog)
    scenarios = catalog.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 24:
        raise ContractError("EXACTLY_24_SCENARIOS_REQUIRED")
    identities = [_scenario_identity(item) for item in scenarios]
    scenario_ids = [item["scenario_id"] for item in identities]
    if len(set(scenario_ids)) != 24:
        raise ContractError("DUPLICATE_SCENARIO_ID")
    split_counts = Counter(item["split"] for item in identities)
    if split_counts != Counter({"train": 8, "dev": 8, "test": 8}):
        raise ContractError(f"SPLIT_SCENARIO_COUNTS_INVALID:{dict(split_counts)}")

    freeze_sha = baseline_freeze_hash(validated)
    method_hashes = {
        method_id: method_config_hash(method_id, validated)
        for method_id in METHOD_ORDER
    }
    runtime_configs: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    ordered = sorted(
        identities,
        key=lambda item: (_SPLIT_ORDER.index(item["split"]), item["scenario_id"]),
    )
    for scenario in ordered:
        for seed in scenario["seeds"]:
            runtime_payload = {
                "catalog_sha256": catalog_sha,
                "scenario_id": scenario["scenario_id"],
                "split": scenario["split"],
                "seed": seed,
                "route_id": scenario["route_id"],
                "town": scenario["town"],
                "executable_fixture_sha256": scenario[
                    "executable_fixture_sha256"
                ],
                "instruction_sha256": scenario["instruction_sha256"],
            }
            runtime_config_id = "runtime-config-" + canonical_sha256(runtime_payload)[:32]
            runtime_configs.append(
                {
                    "runtime_config_id": runtime_config_id,
                    **runtime_payload,
                    "policy_payload_contains_evaluation_label": False,
                    "status": "SCHEDULED_NOT_STARTED",
                }
            )
            for method_id in METHOD_ORDER:
                identity = {
                    "runtime_config_id": runtime_config_id,
                    "method_id": method_id,
                    "method_config_sha256": method_hashes[method_id],
                    "baseline_freeze_sha256": freeze_sha,
                }
                episodes.append(
                    {
                        "episode_id": "episode-" + canonical_sha256(identity)[:32],
                        **identity,
                        "scenario_id": scenario["scenario_id"],
                        "split": scenario["split"],
                        "seed": seed,
                        "status": "SCHEDULED_NOT_STARTED",
                        "expected_label_join_status": "POST_EPISODE_ONLY_NOT_JOINED",
                    }
                )

    if len(runtime_configs) != 96 or len(episodes) != 768:
        raise ContractError("SCHEDULE_CARDINALITY_INVARIANT_FAILED")
    if len({item["runtime_config_id"] for item in runtime_configs}) != 96:
        raise ContractError("RUNTIME_CONFIG_ID_COLLISION")
    if len({item["episode_id"] for item in episodes}) != 768:
        raise ContractError("EPISODE_ID_COLLISION")
    method_counts = Counter(item["method_id"] for item in episodes)
    if method_counts != Counter({method_id: 96 for method_id in METHOD_ORDER}):
        raise ContractError("METHOD_SCHEDULE_NOT_BALANCED")
    split_episode_counts = Counter(item["split"] for item in episodes)
    if split_episode_counts != Counter({"train": 256, "dev": 256, "test": 256}):
        raise ContractError("SPLIT_EPISODE_COUNTS_INVALID")
    schedule_body = {
        "catalog_sha256": catalog_sha,
        "baseline_freeze_sha256": freeze_sha,
        "method_config_sha256": method_hashes,
        "runtime_configurations": runtime_configs,
        "episodes": episodes,
    }
    return {
        "schema_version": "driveclarify.paper_mvp_hash_bound_schedule.v1",
        **schedule_body,
        "runtime_configuration_count": 96,
        "method_count": 8,
        "episode_count": 768,
        "split_episode_counts": dict(sorted(split_episode_counts.items())),
        "method_episode_counts": dict(sorted(method_counts.items())),
        "schedule_sha256": canonical_sha256(schedule_body),
    }


def frozen_threshold_hash(
    config: Mapping[str, Any] = FROZEN_BASELINE_CONFIG,
) -> str:
    validated = validate_baseline_config(config)
    thresholds = {
        method["id"]: method["threshold"]
        for method in validated["methods"]
        if method["threshold"] is not None
    }
    return canonical_sha256(
        {
            "freeze_id": validated["freeze_id"],
            "thresholds": thresholds,
            "test_threshold_tuning_allowed": False,
        }
    )


class LifecycleStatus(str, Enum):
    TRAIN_READY = "TRAIN_READY"
    TRAIN_IN_PROGRESS = "TRAIN_IN_PROGRESS"
    DEV_READY = "DEV_READY"
    DEV_IN_PROGRESS = "DEV_IN_PROGRESS"
    DEV_COMPLETE = "DEV_COMPLETE"
    TEST_LOCKED_AWAITING_AUTHORIZATION = "TEST_LOCKED_AWAITING_AUTHORIZATION"
    TEST_READY_ONE_SHOT = "TEST_READY_ONE_SHOT"
    TEST_IN_PROGRESS_CONSUMED = "TEST_IN_PROGRESS_CONSUMED"
    TEST_COMPLETE_CONSUMED = "TEST_COMPLETE_CONSUMED"
    TEST_FAILED_CONSUMED = "TEST_FAILED_CONSUMED"


class LifecycleEvent(str, Enum):
    BEGIN_TRAIN = "BEGIN_TRAIN"
    COMPLETE_TRAIN = "COMPLETE_TRAIN"
    BEGIN_DEV = "BEGIN_DEV"
    COMPLETE_DEV = "COMPLETE_DEV"
    FREEZE_THRESHOLDS = "FREEZE_THRESHOLDS"
    AUTHORIZE_TEST = "AUTHORIZE_TEST"
    BEGIN_TEST = "BEGIN_TEST"
    COMPLETE_TEST = "COMPLETE_TEST"
    FAIL_TEST = "FAIL_TEST"


@dataclass(frozen=True)
class EvaluationLifecycle:
    schedule_sha256: str
    baseline_freeze_sha256: str
    status: LifecycleStatus = LifecycleStatus.TRAIN_READY
    threshold_sha256: str | None = None
    test_authorization_sha256: str | None = None
    test_attempt_count: int = 0
    transition_count: int = 0
    evidence_chain: tuple[dict[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require_sha256(self.schedule_sha256, "SCHEDULE")
        _require_sha256(self.baseline_freeze_sha256, "BASELINE_FREEZE")
        if self.threshold_sha256 is not None:
            _require_sha256(self.threshold_sha256, "THRESHOLD")
        if self.test_authorization_sha256 is not None:
            _require_sha256(self.test_authorization_sha256, "TEST_AUTHORIZATION")
        if self.test_attempt_count not in (0, 1):
            raise ContractError("TEST_ONE_SHOT_COUNT_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "driveclarify.paper_mvp_split_lifecycle.v1",
            "schedule_sha256": self.schedule_sha256,
            "baseline_freeze_sha256": self.baseline_freeze_sha256,
            "status": self.status.value,
            "threshold_sha256": self.threshold_sha256,
            "test_authorization_sha256": self.test_authorization_sha256,
            "test_attempt_count": self.test_attempt_count,
            "transition_count": self.transition_count,
            "evidence_chain": [dict(item) for item in self.evidence_chain],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvaluationLifecycle":
        return cls(
            schedule_sha256=str(value["schedule_sha256"]),
            baseline_freeze_sha256=str(value["baseline_freeze_sha256"]),
            status=LifecycleStatus(str(value["status"])),
            threshold_sha256=value.get("threshold_sha256"),
            test_authorization_sha256=value.get("test_authorization_sha256"),
            test_attempt_count=int(value.get("test_attempt_count", 0)),
            transition_count=int(value.get("transition_count", 0)),
            evidence_chain=tuple(dict(item) for item in value.get("evidence_chain", [])),
        )


_TRANSITIONS = {
    (LifecycleStatus.TRAIN_READY, LifecycleEvent.BEGIN_TRAIN): LifecycleStatus.TRAIN_IN_PROGRESS,
    (LifecycleStatus.TRAIN_IN_PROGRESS, LifecycleEvent.COMPLETE_TRAIN): LifecycleStatus.DEV_READY,
    (LifecycleStatus.DEV_READY, LifecycleEvent.BEGIN_DEV): LifecycleStatus.DEV_IN_PROGRESS,
    (LifecycleStatus.DEV_IN_PROGRESS, LifecycleEvent.COMPLETE_DEV): LifecycleStatus.DEV_COMPLETE,
    (
        LifecycleStatus.DEV_COMPLETE,
        LifecycleEvent.FREEZE_THRESHOLDS,
    ): LifecycleStatus.TEST_LOCKED_AWAITING_AUTHORIZATION,
    (
        LifecycleStatus.TEST_LOCKED_AWAITING_AUTHORIZATION,
        LifecycleEvent.AUTHORIZE_TEST,
    ): LifecycleStatus.TEST_READY_ONE_SHOT,
    (
        LifecycleStatus.TEST_READY_ONE_SHOT,
        LifecycleEvent.BEGIN_TEST,
    ): LifecycleStatus.TEST_IN_PROGRESS_CONSUMED,
    (
        LifecycleStatus.TEST_IN_PROGRESS_CONSUMED,
        LifecycleEvent.COMPLETE_TEST,
    ): LifecycleStatus.TEST_COMPLETE_CONSUMED,
    (
        LifecycleStatus.TEST_IN_PROGRESS_CONSUMED,
        LifecycleEvent.FAIL_TEST,
    ): LifecycleStatus.TEST_FAILED_CONSUMED,
}


def advance_lifecycle(
    state: EvaluationLifecycle,
    event: LifecycleEvent,
    *,
    evidence_sha256: str,
    threshold_sha256: str | None = None,
    test_authorization_sha256: str | None = None,
    expected_threshold_sha256: str | None = None,
) -> EvaluationLifecycle:
    """Advance exactly one legal edge; starting TEST irreversibly consumes it."""

    evidence = _require_sha256(evidence_sha256, "TRANSITION_EVIDENCE")
    target = _TRANSITIONS.get((state.status, event))
    if target is None:
        raise ContractError(
            f"ILLEGAL_LIFECYCLE_TRANSITION:{state.status.value}:{event.value}"
        )
    changes: dict[str, Any] = {"status": target}
    if event is LifecycleEvent.FREEZE_THRESHOLDS:
        frozen = _require_sha256(threshold_sha256, "THRESHOLD")
        if expected_threshold_sha256 is not None and frozen != _require_sha256(
            expected_threshold_sha256, "EXPECTED_THRESHOLD"
        ):
            raise ContractError("DEV_THRESHOLD_HASH_DIFFERS_FROM_PREREGISTERED_CONFIG")
        changes["threshold_sha256"] = frozen
    if event is LifecycleEvent.AUTHORIZE_TEST:
        if state.threshold_sha256 is None:
            raise ContractError("TEST_AUTHORIZATION_BEFORE_THRESHOLD_FREEZE")
        changes["test_authorization_sha256"] = _require_sha256(
            test_authorization_sha256, "TEST_AUTHORIZATION"
        )
    if event is LifecycleEvent.BEGIN_TEST:
        if state.test_attempt_count != 0 or state.test_authorization_sha256 is None:
            raise ContractError("TEST_ONE_SHOT_ALREADY_CONSUMED_OR_UNAUTHORIZED")
        changes["test_attempt_count"] = 1
    changes["transition_count"] = state.transition_count + 1
    changes["evidence_chain"] = state.evidence_chain + (
        {
            "sequence": str(state.transition_count + 1),
            "event": event.value,
            "from": state.status.value,
            "to": target.value,
            "evidence_sha256": evidence,
            "previous_state_sha256": canonical_sha256(state.to_dict()),
        },
    )
    return replace(state, **changes)


def split_execution_allowed(state: EvaluationLifecycle, split: str) -> bool:
    if split == "train":
        return state.status is LifecycleStatus.TRAIN_IN_PROGRESS
    if split == "dev":
        return state.status is LifecycleStatus.DEV_IN_PROGRESS
    if split == "test":
        return state.status is LifecycleStatus.TEST_IN_PROGRESS_CONSUMED
    raise ContractError(f"INVALID_SPLIT:{split}")


class LifecycleSealStore:
    """Atomic file-backed lifecycle with an advisory lock for one-shot TEST."""

    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def initialize(self, state: EvaluationLifecycle) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(self.path, flags, 0o644)
        except FileExistsError as exc:
            raise ContractError("LIFECYCLE_SEAL_ALREADY_EXISTS") from exc
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_bytes(self._sealed(state)))
            handle.flush()
            os.fsync(handle.fileno())

    def load(self) -> EvaluationLifecycle:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ContractError("LIFECYCLE_SEAL_OBJECT_REQUIRED")
        if set(value) != {"schema_version", "state", "state_sha256"}:
            raise ContractError("LIFECYCLE_SEAL_KEY_MISMATCH")
        if value.get("schema_version") != "driveclarify.paper_mvp_lifecycle_seal.v1":
            raise ContractError("LIFECYCLE_SEAL_SCHEMA_MISMATCH")
        body = value.get("state")
        if not isinstance(body, Mapping):
            raise ContractError("LIFECYCLE_SEAL_STATE_OBJECT_REQUIRED")
        if value.get("state_sha256") != canonical_sha256(body):
            raise ContractError("LIFECYCLE_SEAL_HASH_MISMATCH")
        return EvaluationLifecycle.from_dict(body)

    @staticmethod
    def _sealed(state: EvaluationLifecycle) -> dict[str, Any]:
        body = state.to_dict()
        return {
            "schema_version": "driveclarify.paper_mvp_lifecycle_seal.v1",
            "state": body,
            "state_sha256": canonical_sha256(body),
        }

    def transition(self, event: LifecycleEvent, **kwargs: Any) -> EvaluationLifecycle:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = self.load()
            updated = advance_lifecycle(current, event, **kwargs)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", dir=str(self.path.parent)
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(canonical_bytes(self._sealed(updated)))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                directory = os.open(str(self.path.parent), os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return updated
