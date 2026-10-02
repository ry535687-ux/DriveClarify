"""DriveClarify-owned lightweight real-SimLingo sensitivity pilot backend."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
import os
import time
import traceback
from collections.abc import MutableMapping, MutableSequence, MutableSet
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .determinism import AUDIT_PASS, RNGCoordinator, audit_module_training_state
from .simlingo_live_adapter import (
    NumPyRandomBackend,
    PythonRandomBackend,
    RealSimLingoLiveAdapter,
    SimLingoCandidateSource,
    SimLingoTensorInputIsolation,
    TorchCPURandomBackend,
    TorchCUDAAllRandomBackend,
    _canonical_json,
    _input_fingerprint,
    _input_storage_identities,
    _sha256,
    _source_storage_identities,
    _tensor_sha,
    atomic_write_json,
)


PILOT_PROTOCOL = "DRIVECLARIFY_CANDIDATE_SENSITIVITY_PILOT_V1"
PILOT_BACKEND_VERSION = "DRIVECLARIFY_SIMLINGO_SENSITIVITY_BACKEND_V1"
CANDIDATE_ALIAS_GUARD_VERSION = (
    "DRIVECLARIFY_LIVE_MUTABLE_ALIAS_GUARD_V1"
)
EXPECTED_ORDER = tuple(
    "{}{}".format(group, repetition)
    for repetition in range(1, 6)
    for group in ("A", "B")
)


@dataclass(frozen=True)
class PilotInferenceConfig:
    model_eval_required: bool = True
    inference_mode_required: bool = True
    do_sample: bool = False
    temperature: float = 0.0
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    num_beams: int = 1
    max_new_tokens: int = 100
    use_cache: bool = False
    autocast_enabled: bool = False
    autocast_dtype: Optional[str] = None
    tf32_enabled: bool = False
    deterministic_algorithms: bool = False
    cudnn_deterministic: bool = False
    cudnn_benchmark: bool = False
    reset_rng_per_candidate: bool = True
    isolate_cache_per_candidate: bool = True
    isolate_history_per_candidate: bool = True
    clone_inputs_per_candidate: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol": PILOT_PROTOCOL,
            "model_eval_required": self.model_eval_required,
            "inference_mode_required": self.inference_mode_required,
            "do_sample": self.do_sample,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "num_beams": self.num_beams,
            "max_new_tokens": self.max_new_tokens,
            "use_cache": self.use_cache,
            "autocast_enabled": self.autocast_enabled,
            "autocast_dtype": self.autocast_dtype,
            "tf32_enabled": self.tf32_enabled,
            "deterministic_algorithms": self.deterministic_algorithms,
            "cudnn_deterministic": self.cudnn_deterministic,
            "cudnn_benchmark": self.cudnn_benchmark,
            "reset_rng_per_candidate": self.reset_rng_per_candidate,
            "isolate_cache_per_candidate": self.isolate_cache_per_candidate,
            "isolate_history_per_candidate": self.isolate_history_per_candidate,
            "clone_inputs_per_candidate": self.clone_inputs_per_candidate,
        }

    @property
    def digest(self) -> str:
        return _sha256(_canonical_json(self.to_dict()))

    def audit(self) -> Mapping[str, Any]:
        expected = {
            "model_eval_required": True,
            "inference_mode_required": True,
            "do_sample": False,
            "temperature": 0.0,
            "top_p": None,
            "top_k": None,
            "num_beams": 1,
            "max_new_tokens": 100,
            "use_cache": False,
            "autocast_enabled": False,
            "autocast_dtype": None,
            "tf32_enabled": False,
            "deterministic_algorithms": False,
            "cudnn_deterministic": False,
            "cudnn_benchmark": False,
            "reset_rng_per_candidate": True,
            "isolate_cache_per_candidate": True,
            "isolate_history_per_candidate": True,
            "clone_inputs_per_candidate": True,
        }
        actual = dict(self.to_dict())
        actual.pop("protocol")
        reasons = [] if actual == expected else ["PILOT_INFERENCE_CONFIG_MISMATCH"]
        return {
            "status": "PASS" if not reasons else "FAIL",
            "reason_codes": reasons,
            "config_digest": self.digest,
        }


def explicit_sensitivity_pilot_config() -> PilotInferenceConfig:
    return PilotInferenceConfig()


def _training_summary(audit: Any) -> Dict[str, Any]:
    records = audit.modules
    return {
        "status": audit.status,
        "reason_codes": list(audit.reason_codes),
        "traversal": audit.traversal,
        "module_count": len(records),
        "training_true_count": len(
            [item for item in records if item.training is True]
        ),
        "training_unresolved_count": len(
            [item for item in records if item.training is None]
        ),
        "stochastic_module_count": len(
            [item for item in records if item.stochastic_layer]
        ),
        "lora_dropout_count": len(
            [
                item
                for item in records
                if "lora_dropout" in item.module_path
                and item.module_type == "Dropout"
            ]
        ),
    }


class LiveCandidateMutableAliasGuard:
    """Compare live mutable objects by ``is`` while retaining strong refs.

    Cross-candidate numeric ``id()`` values are intentionally never retained.
    Local traversal uses live object references for cycle protection, and
    tensors are excluded because input storage aliasing has a separate,
    stricter storage/data-pointer contract.
    """

    _IMMUTABLE_ATOMS = (
        type(None),
        bool,
        int,
        float,
        complex,
        str,
        bytes,
        range,
    )

    def __init__(self, *, tensor_type: Any = None) -> None:
        self._tensor_type = tensor_type
        self._retained_candidates = []
        self._persistent_baselines = []
        self._next_logical_id = 1

    @staticmethod
    def _contains_same_object(objects: Sequence[Any], value: Any) -> bool:
        return any(value is item for item in objects)

    def _is_tensor(self, value: Any) -> bool:
        return (
            self._tensor_type is not None
            and isinstance(value, self._tensor_type)
        )

    def _live_mutable_objects(self, root: Any) -> Tuple[Any, ...]:
        stack = [root]
        traversed = []
        mutable_objects = []
        while stack:
            value = stack.pop()
            if self._contains_same_object(traversed, value):
                continue
            traversed.append(value)
            if self._is_tensor(value):
                continue
            if isinstance(value, self._IMMUTABLE_ATOMS):
                continue
            if isinstance(value, MutableMapping):
                mutable_objects.append(value)
                stack.extend(value.values())
                continue
            if isinstance(value, MutableSequence):
                mutable_objects.append(value)
                stack.extend(value)
                continue
            if isinstance(value, MutableSet):
                mutable_objects.append(value)
                stack.extend(value)
                continue
            if isinstance(value, (tuple, frozenset)):
                stack.extend(value)
                continue
            attributes = getattr(value, "__dict__", None)
            slots = getattr(type(value), "__slots__", ())
            if attributes is not None or slots:
                mutable_objects.append(value)
                if attributes:
                    stack.extend(attributes.values())
                if isinstance(slots, str):
                    slots = (slots,)
                for name in slots:
                    try:
                        stack.append(getattr(value, name))
                    except (AttributeError, TypeError):
                        continue
        return tuple(mutable_objects)

    @staticmethod
    def _first_live_alias(
        current: Sequence[Any],
        retained: Sequence[Any],
    ) -> Optional[Any]:
        for current_value in current:
            for retained_value in retained:
                if current_value is retained_value:
                    return current_value
        return None

    def retain_persistent_baseline(
        self,
        *,
        baseline_id: str,
        state_root: Any,
    ) -> Mapping[str, Any]:
        if any(
            item["baseline_id"] == baseline_id
            for item in self._persistent_baselines
        ):
            raise RuntimeError(
                "SIMLINGO_PILOT_PERSISTENT_BASELINE_ID_REUSED"
            )
        objects = self._live_mutable_objects(state_root)
        self._persistent_baselines.append(
            {
                "baseline_id": baseline_id,
                "state_root": state_root,
                "objects": objects,
            }
        )
        return {
            "baseline_id": baseline_id,
            "mutable_object_count": len(objects),
            "strong_references_retained": True,
            "raw_numeric_ids_retained": False,
        }

    def assert_and_retain(
        self,
        *,
        candidate_id: str,
        state_root: Any,
    ) -> Mapping[str, Any]:
        if any(
            item["candidate_id"] == candidate_id
            for item in self._retained_candidates
        ):
            raise RuntimeError("SIMLINGO_PILOT_CANDIDATE_STATE_ID_REUSED")
        objects = self._live_mutable_objects(state_root)
        for baseline in self._persistent_baselines:
            if self._first_live_alias(objects, baseline["objects"]) is not None:
                raise RuntimeError(
                    "SIMLINGO_PILOT_CANDIDATE_STATE_SHARED_WITH_"
                    "PERSISTENT_BASELINE"
                )
        for retained in self._retained_candidates:
            if self._first_live_alias(objects, retained["objects"]) is not None:
                raise RuntimeError("SIMLINGO_PILOT_CANDIDATE_STATE_SHARED")
        logical_id = self._next_logical_id
        self._next_logical_id += 1
        self._retained_candidates.append(
            {
                "candidate_id": candidate_id,
                "logical_id": logical_id,
                "state_root": state_root,
                "objects": objects,
            }
        )
        return {
            "guard_version": CANDIDATE_ALIAS_GUARD_VERSION,
            "candidate_id": candidate_id,
            "logical_candidate_id": logical_id,
            "mutable_object_count": len(objects),
            "prior_live_candidate_count": len(self._retained_candidates) - 1,
            "comparison_semantics": "LIVE_OBJECT_IS_WITH_STRONG_REFERENCES",
            "tensor_alias_semantics": "SEPARATE_STORAGE_IDENTITY_GUARD",
            "strong_references_retained": True,
            "raw_numeric_ids_retained": False,
            "status": "PASS",
        }

    def audit(self) -> Mapping[str, Any]:
        return {
            "guard_version": CANDIDATE_ALIAS_GUARD_VERSION,
            "comparison_semantics": "LIVE_OBJECT_IS_WITH_STRONG_REFERENCES",
            "retained_candidate_count": len(self._retained_candidates),
            "persistent_baseline_count": len(self._persistent_baselines),
            "logical_candidate_ids": [
                item["logical_id"] for item in self._retained_candidates
            ],
            "strong_references_retained": True,
            "raw_numeric_ids_retained": False,
        }


class SimLingoSensitivityPilotBackend(RealSimLingoLiveAdapter):
    """Run 3 or 5 A/B repeats without enabling strict deterministic kernels."""

    def __init__(
        self,
        *,
        agent: Any,
        driving_input_factory: Any,
        label_builder: Any,
        generation_config: PilotInferenceConfig,
        candidates_path: Path,
        runtime_audit_path: Path,
        rng_evidence_path: Path,
        isolation_evidence_path: Path,
        failure_evidence_path: Path,
        run_id: str,
        rng_backends_override: Optional[Sequence[Any]] = None,
        expected_order: Optional[Sequence[str]] = None,
    ) -> None:
        super().__init__(
            agent=agent,
            driving_input_factory=driving_input_factory,
            label_builder=label_builder,
            generation_config=generation_config,
            forward_journal_path=candidates_path,
            runtime_audit_path=runtime_audit_path,
            failure_evidence_path=failure_evidence_path,
            rng_backends_override=rng_backends_override,
        )
        self.candidates_path = Path(candidates_path)
        self.rng_evidence_path = Path(rng_evidence_path)
        self.isolation_evidence_path = Path(isolation_evidence_path)
        self.run_id = run_id
        self.expected_order = tuple(expected_order) if expected_order is not None else EXPECTED_ORDER
        if (
            not self.expected_order
            or len(self.expected_order) > len(EXPECTED_ORDER)
            or len(set(self.expected_order)) != len(self.expected_order)
            or any(
                not isinstance(item, str)
                or len(item) < 2
                or item[0] not in ("A", "B")
                or not item[1:].isdigit()
                for item in self.expected_order
            )
        ):
            raise ValueError("SIMLINGO_PILOT_EXPECTED_ORDER_INVALID")
        self._candidate_records = []
        self._rng_records = []
        self._isolation_records = []
        self._candidate_state_alias_guard = LiveCandidateMutableAliasGuard(
            tensor_type=self._torch.Tensor
        )
        self._coordinator = None
        self._base_rng = None
        self._runtime_audit_sha256 = None

    def rng_backends(self) -> Sequence[Any]:
        if self._rng_backends_override is not None:
            return self._rng_backends_override
        return (
            PythonRandomBackend(),
            NumPyRandomBackend(self._numpy),
            TorchCPURandomBackend(self._torch),
            TorchCUDAAllRandomBackend(self._torch),
        )

    def _pilot_backend_inventory(self) -> Dict[str, Any]:
        torch = self._torch
        return {
            "deterministic_algorithms_enabled": bool(
                torch.are_deterministic_algorithms_enabled()
            ),
            "cuda_matmul_allow_tf32": bool(torch.backends.cuda.matmul.allow_tf32),
            "cudnn_allow_tf32": bool(torch.backends.cudnn.allow_tf32),
            "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
            "cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
            "strict_cumsum_environment": os.environ.get(
                "DRIVECLARIFY_STRICT_DETERMINISTIC_CUMSUM"
            ),
        }

    def apply_model_eval(self) -> None:
        self.model.eval()
        self._torch.use_deterministic_algorithms(False)
        self._torch.backends.cuda.matmul.allow_tf32 = False
        self._torch.backends.cudnn.allow_tf32 = False
        self._torch.backends.cudnn.deterministic = False
        self._torch.backends.cudnn.benchmark = False
        expected = {
            "deterministic_algorithms_enabled": False,
            "cuda_matmul_allow_tf32": False,
            "cudnn_allow_tf32": False,
            "cudnn_deterministic": False,
            "cudnn_benchmark": False,
            "strict_cumsum_environment": None,
        }
        if self._pilot_backend_inventory() != expected:
            raise RuntimeError("SIMLINGO_PILOT_BACKEND_SETTINGS_MISMATCH")

    def run_read_only_runtime_audit(self) -> Mapping[str, Any]:
        self.apply_model_eval()
        training = audit_module_training_state(self.model)
        config_audit = self.config.audit()
        self._coordinator = RNGCoordinator(self.rng_backends())
        self._base_rng = self._coordinator.capture_base()
        restored, restore_verified, restore_reasons = (
            self._coordinator.restore_and_verify(self._base_rng)
        )
        cache_inventory = self._cache_inventory()
        generation_contract = self._generation_contract_inventory()
        self._reset_model_outputs()
        failures = []
        if training.status != AUDIT_PASS:
            failures.append("MODEL_TRAINING_AUDIT_" + training.status)
        if config_audit["status"] != "PASS":
            failures.extend(config_audit["reason_codes"])
        if not self._coordinator.all_available(self._base_rng):
            failures.append("RNG_BACKEND_AVAILABILITY_INCOMPLETE")
        if not restore_verified:
            failures.extend(restore_reasons)
        if not self._cache_inventory_empty(cache_inventory):
            failures.append("PERSISTENT_GENERATION_CACHE_PRESENT")
        audit = {
            "protocol": PILOT_PROTOCOL,
            "backend_version": PILOT_BACKEND_VERSION,
            "run_id": self.run_id,
            "status": "PASS" if not failures else "FAIL",
            "reason_codes": failures,
            "model_instance_identity": self.model_instance_identity(),
            "model_training_audit": training.to_dict(),
            "model_training_summary": _training_summary(training),
            "generation_config": self.config.to_dict(),
            "generation_config_audit": config_audit,
            "generation_contract": generation_contract,
            "backend_settings": self._pilot_backend_inventory(),
            "rng_backends": {
                name: snapshot.public_dict()
                for name, snapshot in self._base_rng.items()
            },
            "rng_restore_verification": {
                "verified": restore_verified,
                "restored_digests": dict(restored),
                "reason_codes": list(restore_reasons),
                "strict_determinism_claimed": False,
                "note": (
                    "RNG restore does not imply bitwise deterministic CUDA "
                    "kernel execution."
                ),
            },
            "cache_inventory": cache_inventory,
            "cache_inventory_empty": self._cache_inventory_empty(cache_inventory),
            "mutable_outputs_after_reset": self._model_output_inventory(),
            "candidate_state_alias_guard": (
                self._candidate_state_alias_guard.audit()
            ),
            "candidate_forward_count": 0,
            "automatic_continuation": True,
        }
        audit["evidence_sha256"] = _sha256(_canonical_json(audit))
        persistence = atomic_write_json(self.runtime_audit_path, audit)
        self._runtime_audit = audit
        self._runtime_audit_sha256 = persistence["sha256"]
        if failures:
            raise RuntimeError("SIMLINGO_PILOT_RUNTIME_AUDIT_FAILED")
        return audit

    def _persist_candidates(self, *, automatic_continuation: bool) -> None:
        payload = {
            "protocol": PILOT_PROTOCOL,
            "backend_version": PILOT_BACKEND_VERSION,
            "run_id": self.run_id,
            "expected_order_maximum": list(self.expected_order),
            "candidate_count": len(self._candidate_records),
            "records": copy.deepcopy(self._candidate_records),
            "automatic_continuation": automatic_continuation,
        }
        payload["evidence_sha256"] = _sha256(_canonical_json(payload))
        atomic_write_json(self.candidates_path, payload)

    def _persist_rng(self, *, automatic_continuation: bool) -> None:
        payload = {
            "protocol": PILOT_PROTOCOL,
            "run_id": self.run_id,
            "base_rng": {
                name: snapshot.public_dict()
                for name, snapshot in self._base_rng.items()
            },
            "records": copy.deepcopy(self._rng_records),
            "rng_restore_is_not_strict_cuda_determinism": True,
            "automatic_continuation": automatic_continuation,
        }
        payload["evidence_sha256"] = _sha256(_canonical_json(payload))
        atomic_write_json(self.rng_evidence_path, payload)

    def _persist_isolation(self, *, automatic_continuation: bool) -> None:
        payload = {
            "protocol": PILOT_PROTOCOL,
            "run_id": self.run_id,
            "contract": (
                "SAME_SOURCE_OBSERVATION_INDEPENDENT_CLONE_PER_CANDIDATE"
            ),
            "records": copy.deepcopy(self._isolation_records),
            "automatic_continuation": automatic_continuation,
        }
        payload["evidence_sha256"] = _sha256(_canonical_json(payload))
        atomic_write_json(self.isolation_evidence_path, payload)

    def _candidate_failure(
        self,
        *,
        candidate_id: str,
        linkage: Mapping[str, Any],
        exc: BaseException,
    ) -> None:
        payload = {
            "protocol": PILOT_PROTOCOL,
            "run_id": self.run_id,
            "candidate_id": candidate_id,
            "candidate_status": "FAIL_INCOMPLETE",
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "exception_repr": repr(exc),
            "traceback": traceback.format_exc(),
            "source_observation_id": linkage["observation_id"],
            "source_observation_digest": linkage["observation_digest"],
            "complete_candidate_count": len(
                [
                    item
                    for item in self._candidate_records
                    if item["completion_status"] == "COMPLETE"
                ]
            ),
            "strict_deterministic_algorithms": False,
            "automatic_continuation": False,
        }
        payload["evidence_sha256"] = _sha256(_canonical_json(payload))
        atomic_write_json(self.failure_evidence_path, payload)

    def run_candidate(
        self,
        *,
        candidate_id: str,
        interpretation_id: str,
        interpretation_text: str,
        source: SimLingoCandidateSource,
        input_isolation: SimLingoTensorInputIsolation,
        linkage: Mapping[str, Any],
        candidate_metadata: Optional[Mapping[str, Any]] = None,
    ) -> Mapping[str, Any]:
        try:
            return self._run_candidate(
                candidate_id=candidate_id,
                interpretation_id=interpretation_id,
                interpretation_text=interpretation_text,
                source=source,
                input_isolation=input_isolation,
                linkage=linkage,
                candidate_metadata=candidate_metadata,
            )
        except BaseException as exc:
            self._candidate_failure(
                candidate_id=candidate_id,
                linkage=linkage,
                exc=exc,
            )
            raise

    def _run_candidate(
        self,
        *,
        candidate_id: str,
        interpretation_id: str,
        interpretation_text: str,
        source: SimLingoCandidateSource,
        input_isolation: SimLingoTensorInputIsolation,
        linkage: Mapping[str, Any],
        candidate_metadata: Optional[Mapping[str, Any]] = None,
    ) -> Mapping[str, Any]:
        position = len(self._candidate_records)
        if position >= len(self.expected_order) or self.expected_order[position] != candidate_id:
            raise RuntimeError("SIMLINGO_PILOT_CANDIDATE_ORDER_CHANGED")
        if candidate_id[0] != interpretation_id:
            raise RuntimeError("SIMLINGO_PILOT_INTERPRETATION_ID_CHANGED")
        if source.interpretations[interpretation_id] != interpretation_text:
            raise RuntimeError("SIMLINGO_PILOT_INTERPRETATION_TEXT_CHANGED")
        if self._coordinator is None or self._base_rng is None:
            raise RuntimeError("SIMLINGO_PILOT_RUNTIME_AUDIT_NOT_RUN")
        restored, verified, restore_reasons = (
            self._coordinator.restore_and_verify(self._base_rng)
        )
        if not verified:
            raise RuntimeError(
                "SIMLINGO_PILOT_RNG_RESTORE_FAILED:" + ",".join(restore_reasons)
            )
        pre_rng = self._coordinator.current_digests()
        if pre_rng != restored:
            raise RuntimeError("SIMLINGO_PILOT_RNG_PRE_DIGEST_CHANGED")
        adjacent_training = audit_module_training_state(self.model)
        if adjacent_training.status != AUDIT_PASS:
            raise RuntimeError("SIMLINGO_PILOT_FORWARD_ADJACENT_TRAINING_FAILED")
        if self._torch.are_deterministic_algorithms_enabled():
            raise RuntimeError("SIMLINGO_PILOT_STRICT_DETERMINISM_ENABLED")

        isolated = input_isolation.prepare(source)
        common = isolated.value.common_inputs
        label = self.label_builder(interpretation_text)
        label_storage = self._label_tensor_storage(label)
        if label_storage & self._label_storage_identities:
            raise RuntimeError("SIMLINGO_PILOT_PROMPT_STORAGE_SHARED")
        self._label_storage_identities.update(label_storage)
        self._retained_labels.append(label)
        model_input = self.driving_input_factory(
            camera_images=common["camera_images"],
            image_sizes=common["image_sizes"],
            camera_intrinsics=common["camera_intrinsics"],
            camera_extrinsics=common["camera_extrinsics"],
            vehicle_speed=common["vehicle_speed"],
            target_point=common["target_point"],
            prompt=label,
            prompt_inference=label,
        )
        actual_common = {
            name: getattr(model_input, name)
            for name in (
                "camera_images",
                "image_sizes",
                "camera_intrinsics",
                "camera_extrinsics",
                "vehicle_speed",
                "target_point",
            )
        }
        if {
            name: _input_fingerprint(self._torch, value)
            for name, value in actual_common.items()
        } != {
            name: _input_fingerprint(self._torch, value)
            for name, value in common.items()
        }:
            raise RuntimeError("SIMLINGO_PILOT_MODEL_INPUT_VALUE_CHANGED")
        actual_storage = set()
        for value in actual_common.values():
            actual_storage.update(_input_storage_identities(self._torch, value))
        if actual_storage != _source_storage_identities(
            self._torch, isolated.value
        ):
            raise RuntimeError("SIMLINGO_PILOT_MODEL_INPUT_NOT_ISOLATED")

        candidate_state = {
            "conversation_history": [],
            "generated_token_history": [],
            "past_key_values": {},
            "kv_cache": {},
            "model_candidate_cache": {},
        }
        candidate_state_alias_audit = (
            self._candidate_state_alias_guard.assert_and_retain(
                candidate_id=candidate_id,
                state_root=candidate_state,
            )
        )
        self._reset_model_outputs()
        cache_before = self._cache_inventory()
        if not self._cache_inventory_empty(cache_before):
            raise RuntimeError("SIMLINGO_PILOT_CACHE_BEFORE_FORWARD")

        started_ns = time.monotonic_ns()
        with self.inference_context():
            with self._generation_boundary(self.config) as generation:
                speed, route, language = self.model_proxy(model_input)
                route_cpu = route.detach().contiguous().cpu().clone()
                speed_cpu = speed.detach().contiguous().cpu().clone()
                language_copy = tuple(
                    str(item) for item in copy.deepcopy(language)
                )
        elapsed_ns = time.monotonic_ns() - started_ns
        if generation["greedy_call_count"] != 1:
            raise RuntimeError("SIMLINGO_PILOT_GREEDY_CALL_COUNT")
        if generation["underlying_language_forward_call_count"] <= 0:
            raise RuntimeError("SIMLINGO_PILOT_LANGUAGE_FORWARD_NOT_OBSERVED")
        if generation["all_underlying_use_cache_false"] is not True:
            raise RuntimeError("SIMLINGO_PILOT_USE_CACHE_NOT_FALSE")
        if generation["sampled_token_ids"] is None:
            raise RuntimeError("SIMLINGO_PILOT_GENERATED_TOKENS_MISSING")

        record = {
            "candidate_id": candidate_id,
            "interpretation_id": interpretation_id,
            "interpretation_text": interpretation_text,
            "sequence_position": position + 1,
            "source_observation_id": linkage["observation_id"],
            "source_observation_digest": linkage["observation_digest"],
            "source_frame": linkage["source_frame"],
            "freshness_token": linkage["freshness_token"],
            "closed_loop_state_digest": linkage["state_digest"],
            "candidate_metadata": copy.deepcopy(dict(candidate_metadata or {})),
            "candidate_input_digest": input_isolation.source_digest(source),
            "model_identity": self.model_instance_identity(),
            "runtime_audit_sha256": self._runtime_audit_sha256,
            "forward_adjacent_training_audit": _training_summary(
                adjacent_training
            ),
            "inference_configuration": self.config.to_dict(),
            "candidate_state_alias_audit": copy.deepcopy(
                candidate_state_alias_audit
            ),
            "generation_invocation": copy.deepcopy(generation),
            "route": route_cpu.tolist(),
            "speed": speed_cpu.tolist(),
            "route_shape": list(route_cpu.shape),
            "speed_shape": list(speed_cpu.shape),
            "route_dtype": str(route_cpu.dtype),
            "speed_dtype": str(speed_cpu.dtype),
            "route_source_device": str(route.device),
            "speed_source_device": str(speed.device),
            "route_persisted_device": str(route_cpu.device),
            "speed_persisted_device": str(speed_cpu.device),
            "route_sha256": _tensor_sha(self._torch, route_cpu),
            "speed_sha256": _tensor_sha(self._torch, speed_cpu),
            "generated_language": list(language_copy),
            "generated_token_ids": list(generation["sampled_token_ids"]),
            "route_nan_count": int(self._torch.isnan(route_cpu).sum().item()),
            "route_inf_count": int(self._torch.isinf(route_cpu).sum().item()),
            "speed_nan_count": int(self._torch.isnan(speed_cpu).sum().item()),
            "speed_inf_count": int(self._torch.isinf(speed_cpu).sum().item()),
            "missing_output": route is None or speed is None,
            "shape_mismatch": (
                tuple(route_cpu.shape) != (1, 20, 2)
                or tuple(speed_cpu.shape) != (1, 10, 2)
            ),
            "latency_ns": elapsed_ns,
            "latency_seconds": elapsed_ns / 1_000_000_000.0,
            "completion_status": "OUTPUT_PERSISTED_POSTCHECK_PENDING",
        }
        self._candidate_records.append(record)
        self._persist_candidates(automatic_continuation=True)

        isolation_audit = input_isolation.verify(isolated, source)
        self._reset_model_outputs()
        cache_after = self._cache_inventory()
        post_rng = self._coordinator.current_digests()
        if isolation_audit.status != AUDIT_PASS:
            raise RuntimeError("SIMLINGO_PILOT_INPUT_ISOLATION_POSTCHECK_FAILED")
        if not self._cache_inventory_empty(cache_after):
            raise RuntimeError("SIMLINGO_PILOT_CACHE_AFTER_FORWARD")
        if (
            record["missing_output"]
            or record["shape_mismatch"]
            or record["route_nan_count"]
            or record["route_inf_count"]
            or record["speed_nan_count"]
            or record["speed_inf_count"]
        ):
            raise RuntimeError("SIMLINGO_PILOT_INVALID_OUTPUT")
        self._rng_records.append(
            {
                "candidate_id": candidate_id,
                "restore_verified": verified,
                "base_digest": {
                    name: snapshot.digest
                    for name, snapshot in self._base_rng.items()
                },
                "pre_digest": dict(pre_rng),
                "post_digest": dict(post_rng),
                "strict_determinism_claimed": False,
            }
        )
        self._isolation_records.append(
            {
                "candidate_id": candidate_id,
                "source_pre_digest": isolation_audit.source_pre_digest,
                "source_post_digest": isolation_audit.source_post_digest,
                "candidate_pre_digest": isolation_audit.candidate_pre_digest,
                "candidate_post_digest": isolation_audit.candidate_post_digest,
                "source_unchanged": isolation_audit.source_unchanged,
                "candidate_unchanged": isolation_audit.candidate_unchanged,
                "no_shared_mutable_objects": (
                    isolation_audit.no_shared_mutable_objects
                ),
                "independent_candidate_state": True,
                "candidate_state_alias_audit": copy.deepcopy(
                    candidate_state_alias_audit
                ),
                "cache_before": cache_before,
                "cache_after": cache_after,
            }
        )
        record.update(
            {
                "rng_pre_digest": dict(pre_rng),
                "rng_post_digest": dict(post_rng),
                "input_isolation_status": isolation_audit.status,
                "model_outputs_reset_after_forward": True,
                "cache_cleared": True,
                "completion_status": "COMPLETE",
            }
        )
        self._persist_candidates(automatic_continuation=True)
        self._persist_rng(automatic_continuation=True)
        self._persist_isolation(automatic_continuation=True)
        return copy.deepcopy(record)

    def finalize_evidence(self) -> None:
        self._persist_candidates(automatic_continuation=False)
        self._persist_rng(automatic_continuation=False)
        self._persist_isolation(automatic_continuation=False)

    @property
    def candidate_records(self) -> Tuple[Mapping[str, Any], ...]:
        return tuple(copy.deepcopy(self._candidate_records))
