"""DriveClarify-owned deterministic adapter for the real SimLingo forward path.

The adapter contains no CARLA, evaluator, planner, PID, or control API.  Torch
and NumPy are imported lazily so the existing standard-library offline package
remains importable in environments where those packages are absent.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
import os
import pickle
import time
import types
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from .determinism import (
    AUDIT_PASS,
    DeterministicInferenceConfig,
    InputIsolationAudit,
    IsolatedCandidateInput,
    PythonRandomBackend,
    RNGCoordinator,
    audit_module_training_state,
    canonical_digest,
)
from .live_protocol import PlanOnlyOutput


LIVE_ADAPTER_VERSION = (
    "DRIVECLARIFY_REAL_SIMLINGO_LIVE_ADAPTER_V1_CUBLAS_CONFIG_FIX_1"
)
FORWARD_JOURNAL_SCHEMA = "DRIVECLARIFY_SIMLINGO_FORWARD_JOURNAL_V1"
RUNTIME_AUDIT_SCHEMA = "DRIVECLARIFY_SIMLINGO_RUNTIME_AUDIT_V1"
FAILURE_EVIDENCE_SCHEMA = "DRIVECLARIFY_SIMLINGO_FAILURE_EVIDENCE_V1"

_COMMON_INPUT_FIELDS = (
    "camera_images",
    "image_sizes",
    "camera_intrinsics",
    "camera_extrinsics",
    "vehicle_speed",
    "target_point",
)
_CACHE_ATTRIBUTE_NAMES = (
    "past_key_values",
    "_past_key_values",
    "kv_cache",
    "_kv_cache",
    "cache_mask",
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return raw + (b"\n" if newline else b"")


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Durably replace one JSON file through a same-directory fsync."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_json(payload, newline=True)
    temporary = path.with_name(
        "."
        + path.name
        + "."
        + hashlib.sha256(os.urandom(32)).hexdigest()[:20]
        + ".tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(temporary), flags, 0o600)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OSError("SHORT_ATOMIC_JSON_WRITE")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(str(temporary), str(path))
        directory_descriptor = os.open(
            str(path.parent),
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    persisted = path.read_bytes()
    if persisted != raw:
        raise RuntimeError("ATOMIC_JSON_PERSISTED_BYTES_MISMATCH")
    return {
        "path": str(path),
        "sha256": _sha256(raw),
        "bytes": len(raw),
        "atomic_rename": True,
        "file_fsync": True,
        "directory_fsync": True,
    }


def explicit_simlingo_generation_config(
    *, max_new_tokens: int = 100
) -> DeterministicInferenceConfig:
    return DeterministicInferenceConfig.from_mapping(
        {
            "model_eval_required": True,
            "inference_mode_required": True,
            "do_sample": False,
            "temperature": 0.0,
            "top_p": None,
            "top_k": None,
            "num_beams": 1,
            "max_new_tokens": max_new_tokens,
            "use_cache": False,
            "autocast_enabled": False,
            "autocast_dtype": None,
            "tf32_enabled": False,
            "deterministic_algorithms": True,
            "cudnn_deterministic": True,
            "cudnn_benchmark": False,
            "reset_rng_per_candidate": True,
            "isolate_cache_per_candidate": True,
            "isolate_history_per_candidate": True,
            "clone_inputs_per_candidate": True,
        }
    )


class NumPyRandomBackend:
    name = "numpy_random"

    def __init__(self, numpy_module: Any) -> None:
        self._numpy = numpy_module

    def availability(self) -> Tuple[bool, Optional[str]]:
        return True, None

    def capture_state(self) -> Any:
        state = self._numpy.random.get_state()
        return (
            state[0],
            state[1].copy(),
            state[2],
            state[3],
            state[4],
        )

    def restore_state(self, state: Any) -> None:
        self._numpy.random.set_state(state)

    def state_digest(self, state: Any) -> str:
        return _sha256(pickle.dumps(state, protocol=4))


class TorchCPURandomBackend:
    name = "torch_cpu"

    def __init__(self, torch_module: Any) -> None:
        self._torch = torch_module

    def availability(self) -> Tuple[bool, Optional[str]]:
        return True, None

    def capture_state(self) -> Any:
        return self._torch.get_rng_state().detach().cpu().clone()

    def restore_state(self, state: Any) -> None:
        self._torch.set_rng_state(state.detach().cpu().clone())

    def state_digest(self, state: Any) -> str:
        value = state.detach().contiguous().cpu()
        return _sha256(value.numpy().tobytes(order="C"))


class TorchCUDAAllRandomBackend:
    name = "torch_cuda_all"

    def __init__(self, torch_module: Any) -> None:
        self._torch = torch_module

    def availability(self) -> Tuple[bool, Optional[str]]:
        if not self._torch.cuda.is_available():
            return False, "TORCH_CUDA_UNAVAILABLE"
        if self._torch.cuda.device_count() <= 0:
            return False, "TORCH_CUDA_DEVICE_COUNT_ZERO"
        return True, None

    def capture_state(self) -> Any:
        return tuple(
            state.detach().cpu().clone()
            for state in self._torch.cuda.get_rng_state_all()
        )

    def restore_state(self, state: Any) -> None:
        self._torch.cuda.set_rng_state_all(
            [item.detach().cpu().clone() for item in state]
        )

    def state_digest(self, state: Any) -> str:
        digest = hashlib.sha256()
        digest.update(str(len(state)).encode("ascii"))
        for index, item in enumerate(state):
            raw = item.detach().contiguous().cpu().numpy().tobytes(order="C")
            digest.update(index.to_bytes(4, "big"))
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
        return digest.hexdigest()


@dataclass(frozen=True)
class SimLingoCandidateSource:
    common_inputs: Mapping[str, Any]
    interpretations: Mapping[str, str]

    @classmethod
    def from_driving_input(
        cls,
        driving_input: Any,
        *,
        prompt_a: str,
        prompt_b: str,
    ) -> "SimLingoCandidateSource":
        return cls(
            common_inputs={
                name: getattr(driving_input, name) for name in _COMMON_INPUT_FIELDS
            },
            interpretations={"A": prompt_a, "B": prompt_b},
        )


def _tensor_bytes(torch_module: Any, value: Any) -> bytes:
    tensor = value.detach().contiguous().cpu()
    return tensor.view(torch_module.uint8).numpy().tobytes(order="C")


def _tensor_fingerprint(torch_module: Any, value: Any) -> Dict[str, Any]:
    return {
        "shape": list(value.shape),
        "dtype": str(value.dtype),
        "source_device": str(value.device),
        "requires_grad": bool(value.requires_grad),
        "sha256": _sha256(_tensor_bytes(torch_module, value)),
    }


def _input_fingerprint(torch_module: Any, value: Any) -> Dict[str, Any]:
    """Return a canonical, type-preserving model-input fingerprint."""

    if value is None:
        return {"kind": "none"}
    if isinstance(value, torch_module.Tensor):
        return {
            "kind": "tensor",
            **_tensor_fingerprint(torch_module, value),
        }
    if isinstance(value, bool):
        return {"kind": "bool", "value": value}
    if isinstance(value, int):
        return {"kind": "int", "value": value}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("SIMLINGO_METADATA_NON_FINITE_FLOAT")
        return {"kind": "float", "hex": value.hex()}
    if isinstance(value, str):
        return {"kind": "str", "value": value}
    if isinstance(value, bytes):
        return {
            "kind": "bytes",
            "length": len(value),
            "sha256": _sha256(value),
        }
    if isinstance(value, list):
        return {
            "kind": "list",
            "items": [_input_fingerprint(torch_module, item) for item in value],
        }
    if isinstance(value, tuple):
        return {
            "kind": "tuple",
            "items": [_input_fingerprint(torch_module, item) for item in value],
        }
    if isinstance(value, dict):
        invalid_keys = [key for key in value if not isinstance(key, str)]
        if invalid_keys:
            key = invalid_keys[0]
            raise TypeError(
                "SIMLINGO_METADATA_DICT_KEY_NOT_STRING:"
                + type(key).__module__
                + "."
                + type(key).__qualname__
            )
        return {
            "kind": "dict",
            "items": [
                {
                    "key": key,
                    "value": _input_fingerprint(torch_module, value[key]),
                }
                for key in sorted(value)
            ],
        }
    value_type = type(value)
    raise TypeError(
        "SIMLINGO_METADATA_UNSUPPORTED_TYPE:"
        + value_type.__module__
        + "."
        + value_type.__qualname__
    )


def _source_digest(torch_module: Any, source: SimLingoCandidateSource) -> str:
    payload = {
        "common_inputs": {
            name: _input_fingerprint(torch_module, source.common_inputs[name])
            for name in _COMMON_INPUT_FIELDS
        },
        "interpretations": dict(source.interpretations),
    }
    return _sha256(_canonical_json(payload))


def _tensor_storage_identity(value: Any) -> Tuple[str, int, int]:
    storage = value.untyped_storage()
    return (str(value.device), int(storage.data_ptr()), int(storage.nbytes()))


def _input_storage_identities(torch_module: Any, value: Any) -> set:
    if isinstance(value, torch_module.Tensor):
        return {_tensor_storage_identity(value)}
    if value is None or isinstance(value, (bool, int, str, bytes)):
        return set()
    if isinstance(value, float):
        _input_fingerprint(torch_module, value)
        return set()
    if isinstance(value, (list, tuple)):
        storage = set()
        for item in value:
            storage.update(_input_storage_identities(torch_module, item))
        return storage
    if isinstance(value, dict):
        _input_fingerprint(torch_module, value)
        storage = set()
        for item in value.values():
            storage.update(_input_storage_identities(torch_module, item))
        return storage
    _input_fingerprint(torch_module, value)
    raise AssertionError("UNREACHABLE_INPUT_STORAGE_TYPE")


def _source_storage_identities(
    torch_module: Any,
    source: SimLingoCandidateSource,
) -> set:
    storage = set()
    for name in _COMMON_INPUT_FIELDS:
        storage.update(
            _input_storage_identities(torch_module, source.common_inputs[name])
        )
    return storage


def _clone_input_value(torch_module: Any, value: Any, *, field: str) -> Any:
    if isinstance(value, torch_module.Tensor):
        cloned = value.detach().clone()
        if cloned.requires_grad:
            raise RuntimeError("SIMLINGO_CLONED_INPUT_REQUIRES_GRAD:" + field)
        return cloned
    if value is None or isinstance(value, (bool, int, str, bytes)):
        return value
    if isinstance(value, float):
        _input_fingerprint(torch_module, value)
        return value
    if isinstance(value, list):
        return [
            _clone_input_value(torch_module, item, field=field)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _clone_input_value(torch_module, item, field=field)
            for item in value
        )
    if isinstance(value, dict):
        _input_fingerprint(torch_module, value)
        return {
            key: _clone_input_value(torch_module, item, field=field)
            for key, item in value.items()
        }
    _input_fingerprint(torch_module, value)
    raise AssertionError("UNREACHABLE_INPUT_CLONE_TYPE")


def _mutable_input_identities(torch_module: Any, value: Any) -> set:
    if isinstance(value, torch_module.Tensor):
        return {id(value)}
    if isinstance(value, list):
        identities = {id(value)}
        for item in value:
            identities.update(_mutable_input_identities(torch_module, item))
        return identities
    if isinstance(value, tuple):
        identities = set()
        for item in value:
            identities.update(_mutable_input_identities(torch_module, item))
        return identities
    if isinstance(value, dict):
        identities = {id(value)}
        for item in value.values():
            identities.update(_mutable_input_identities(torch_module, item))
        return identities
    _input_fingerprint(torch_module, value)
    return set()


def _source_mutable_identities(
    torch_module: Any,
    source: SimLingoCandidateSource,
) -> set:
    identities = set()
    for name in _COMMON_INPUT_FIELDS:
        identities.update(
            _mutable_input_identities(torch_module, source.common_inputs[name])
        )
    return identities


class SimLingoTensorInputIsolation:
    """Clone tensors and type-preserve metadata for every candidate input."""

    def __init__(self, torch_module: Any) -> None:
        self._torch = torch_module
        self._retained_candidates = []

    def source_digest(self, source: SimLingoCandidateSource) -> str:
        return _source_digest(self._torch, source)

    def prepare(self, source: SimLingoCandidateSource) -> IsolatedCandidateInput:
        if set(source.common_inputs) != set(_COMMON_INPUT_FIELDS):
            raise ValueError("SIMLINGO_SOURCE_COMMON_INPUT_FIELDS_INVALID")
        if set(source.interpretations) != {"A", "B"}:
            raise ValueError("SIMLINGO_SOURCE_INTERPRETATIONS_INVALID")
        source_digest = _source_digest(self._torch, source)
        cloned = {}
        for name in _COMMON_INPUT_FIELDS:
            value = source.common_inputs[name]
            cloned[name] = _clone_input_value(
                self._torch,
                value,
                field=name,
            )
        candidate = SimLingoCandidateSource(
            common_inputs=cloned,
            interpretations=dict(source.interpretations),
        )
        candidate_digest = _source_digest(self._torch, candidate)
        if candidate_digest != source_digest:
            raise RuntimeError("SIMLINGO_INPUT_CLONE_DIGEST_MISMATCH")
        source_storage = _source_storage_identities(self._torch, source)
        candidate_storage = _source_storage_identities(self._torch, candidate)
        if source_storage & candidate_storage:
            raise RuntimeError("SIMLINGO_INPUT_CLONE_SHARED_SOURCE_STORAGE")
        source_mutable = _source_mutable_identities(self._torch, source)
        candidate_mutable = _source_mutable_identities(self._torch, candidate)
        if source_mutable & candidate_mutable:
            raise RuntimeError("SIMLINGO_INPUT_CLONE_SHARED_MUTABLE_OBJECT")
        for retained in self._retained_candidates:
            if candidate_storage & retained["storage"]:
                raise RuntimeError("SIMLINGO_INPUT_CLONE_SHARED_CANDIDATE_STORAGE")
            if candidate_mutable & retained["mutable"]:
                raise RuntimeError(
                    "SIMLINGO_INPUT_CLONE_SHARED_CANDIDATE_MUTABLE_OBJECT"
                )
        self._retained_candidates.append(
            {
                "source": candidate,
                "storage": candidate_storage,
                "mutable": candidate_mutable,
            }
        )
        isolated = IsolatedCandidateInput(
            value=candidate,
            source_pre_digest=source_digest,
            candidate_pre_digest=candidate_digest,
            source_mutable_ids=source_mutable,
            candidate_mutable_ids=candidate_mutable,
        )
        isolated.source_storage_identities = source_storage
        isolated.candidate_storage_identities = candidate_storage
        return isolated

    def verify(
        self,
        isolated: IsolatedCandidateInput,
        source: SimLingoCandidateSource,
    ) -> InputIsolationAudit:
        reasons = []
        try:
            source_post = _source_digest(self._torch, source)
        except Exception as exc:
            source_post = None
            reasons.append("SOURCE_INPUT_POST_DIGEST_FAILED:" + type(exc).__name__)
        try:
            candidate_post = _source_digest(self._torch, isolated.value)
        except Exception as exc:
            candidate_post = None
            reasons.append(
                "CANDIDATE_INPUT_POST_DIGEST_FAILED:" + type(exc).__name__
            )
        source_storage = _source_storage_identities(self._torch, source)
        candidate_storage = _source_storage_identities(
            self._torch, isolated.value
        )
        source_mutable = _source_mutable_identities(self._torch, source)
        candidate_mutable = _source_mutable_identities(
            self._torch, isolated.value
        )
        storage_shared = bool(source_storage & candidate_storage)
        mutable_shared = bool(source_mutable & candidate_mutable)
        no_shared = not storage_shared and not mutable_shared
        source_unchanged = source_post == isolated.source_pre_digest
        candidate_unchanged = candidate_post == isolated.candidate_pre_digest
        if candidate_storage != isolated.candidate_storage_identities:
            reasons.append("CANDIDATE_INPUT_STORAGE_IDENTITY_CHANGED")
        if storage_shared:
            reasons.append("INPUT_TENSOR_STORAGE_SHARED")
        if mutable_shared:
            reasons.append("INPUT_MUTABLE_OBJECT_SHARED")
        if not source_unchanged:
            reasons.append("SOURCE_INPUT_MUTATED")
        if not candidate_unchanged:
            reasons.append("CANDIDATE_INPUT_MUTATED")
        return InputIsolationAudit(
            status="FAIL" if reasons else AUDIT_PASS,
            source_pre_digest=isolated.source_pre_digest,
            source_post_digest=source_post,
            candidate_pre_digest=isolated.candidate_pre_digest,
            candidate_post_digest=candidate_post,
            no_shared_mutable_objects=no_shared,
            source_unchanged=source_unchanged,
            candidate_unchanged=candidate_unchanged,
            reason_codes=tuple(reasons),
        )


def _tensor_descriptor(torch_module: Any, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, torch_module.Tensor):
        return {
            "kind": "tensor",
            **_tensor_fingerprint(torch_module, value),
        }
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_tensor_descriptor(torch_module, item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _tensor_descriptor(torch_module, item)
            for key, item in value.items()
        }
    return {
        "kind": "object",
        "type": type(value).__module__ + "." + type(value).__qualname__,
        "identity": id(value),
    }


def _tensor_to_trajectory(value: Any) -> Tuple[Tuple[float, float], ...]:
    nested = value.tolist()
    if (
        isinstance(nested, list)
        and len(nested) == 1
        and isinstance(nested[0], list)
    ):
        nested = nested[0]
    return tuple((float(point[0]), float(point[1])) for point in nested)


def _tensor_sha(torch_module: Any, value: Any) -> str:
    return _sha256(_tensor_bytes(torch_module, value))


class RealSimLingoLiveAdapter:
    """Production LiveCandidateBackend at the real SimLingo model boundary."""

    live_simlingo_adapter_implemented = True

    def __init__(
        self,
        *,
        agent: Any,
        driving_input_factory: Callable[..., Any],
        label_builder: Callable[[str], Any],
        generation_config: DeterministicInferenceConfig,
        forward_journal_path: Path,
        runtime_audit_path: Path,
        failure_evidence_path: Path,
        rng_backends_override: Optional[Sequence[Any]] = None,
    ) -> None:
        import numpy as numpy_module
        import torch as torch_module

        self._numpy = numpy_module
        self._torch = torch_module
        self.agent = agent
        self.model_proxy = agent.model
        self.model = getattr(self.model_proxy, "model", self.model_proxy)
        self.driving_input_factory = driving_input_factory
        self.label_builder = label_builder
        self.config = generation_config
        self.forward_journal_path = Path(forward_journal_path)
        self.runtime_audit_path = Path(runtime_audit_path)
        self.failure_evidence_path = Path(failure_evidence_path)
        self._inference_active = False
        self._forward_index = 0
        self._forward_records = []
        self._retained_labels = []
        self._label_storage_identities = set()
        self._runtime_audit = None
        self._model_proxy_call_entered = False
        self._generation_boundary_entered = False
        self._last_completed_integration_checkpoint = "ADAPTER_INITIALIZED"
        self._model_proxy_forward_count_before_candidate = None
        self._initial_mutable_outputs = self._model_output_inventory()
        self._rng_backends_override = (
            tuple(rng_backends_override)
            if rng_backends_override is not None
            else None
        )

    def model_for_training_audit(self) -> Any:
        return self.model

    def model_instance_identity(self) -> Any:
        return id(self.model)

    def rng_backends(self) -> Sequence[Any]:
        if self._rng_backends_override is not None:
            return self._rng_backends_override
        return (
            PythonRandomBackend(),
            NumPyRandomBackend(self._numpy),
            TorchCPURandomBackend(self._torch),
            TorchCUDAAllRandomBackend(self._torch),
        )

    def _deterministic_backend_inventory(self) -> Dict[str, Any]:
        torch = self._torch
        return {
            "deterministic_algorithms_enabled": bool(
                torch.are_deterministic_algorithms_enabled()
            ),
            "cuda_matmul_allow_tf32": bool(torch.backends.cuda.matmul.allow_tf32),
            "cudnn_allow_tf32": bool(torch.backends.cudnn.allow_tf32),
            "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
            "cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
            "cublas_workspace_config": os.environ.get(
                "CUBLAS_WORKSPACE_CONFIG"
            ),
        }

    def apply_model_eval(self) -> None:
        self.model.eval()
        self._torch.use_deterministic_algorithms(True)
        self._torch.backends.cuda.matmul.allow_tf32 = False
        self._torch.backends.cudnn.allow_tf32 = False
        self._torch.backends.cudnn.deterministic = True
        self._torch.backends.cudnn.benchmark = False
        actual = self._deterministic_backend_inventory()
        expected = {
            "deterministic_algorithms_enabled": True,
            "cuda_matmul_allow_tf32": False,
            "cudnn_allow_tf32": False,
            "cudnn_deterministic": True,
            "cudnn_benchmark": False,
            "cublas_workspace_config": ":4096:8",
        }
        if actual != expected:
            raise RuntimeError("SIMLINGO_DETERMINISTIC_BACKEND_SETTINGS_MISMATCH")

    @contextmanager
    def inference_context(self):
        with self._torch.inference_mode():
            self._inference_active = bool(self._torch.is_inference_mode_enabled())
            try:
                yield
            finally:
                self._inference_active = False

    def inference_mode_active(self) -> bool:
        return bool(
            self._inference_active
            and self._torch.is_inference_mode_enabled()
        )

    def _cache_inventory(self) -> Dict[str, Any]:
        inventory = {}
        for module_path, module in self.model.named_modules():
            for name in _CACHE_ATTRIBUTE_NAMES:
                if name in getattr(module, "__dict__", {}):
                    inventory[(module_path or "<root>") + "." + name] = (
                        _tensor_descriptor(self._torch, module.__dict__[name])
                    )
        return dict(sorted(inventory.items()))

    @staticmethod
    def _cache_inventory_empty(inventory: Mapping[str, Any]) -> bool:
        return all(value in (None, [], {}, ()) for value in inventory.values())

    def _model_output_inventory(self) -> Dict[str, Any]:
        return {
            "route": _tensor_descriptor(
                self._torch, getattr(self.model, "route", None)
            ),
            "speed_wps": _tensor_descriptor(
                self._torch, getattr(self.model, "speed_wps", None)
            ),
            "language": _tensor_descriptor(
                self._torch, getattr(self.model, "language", None)
            ),
        }

    def _reset_model_outputs(self) -> None:
        self.model.route = None
        self.model.speed_wps = None
        self.model.language = []
        if self._model_output_inventory() != {
            "route": None,
            "speed_wps": None,
            "language": [],
        }:
            raise RuntimeError("SIMLINGO_MODEL_MUTABLE_OUTPUT_RESET_FAILED")

    def _tokenizer_state(self) -> Dict[str, Any]:
        tokenizer = getattr(self.model, "tokenizer", None)
        return {
            "tokenizer_identity": id(tokenizer) if tokenizer is not None else None,
            "padding_side": getattr(tokenizer, "padding_side", None),
            "pad_token_id": getattr(tokenizer, "pad_token_id", None),
            "eos_token_id": getattr(tokenizer, "eos_token_id", None),
            "bos_token_id": getattr(tokenizer, "bos_token_id", None),
        }

    def persistent_candidate_state(self) -> Mapping[str, Any]:
        cache_inventory = self._cache_inventory()
        return {
            "conversation_history": [],
            "generated_token_history": [],
            "past_key_values": {
                key: value
                for key, value in cache_inventory.items()
                if "past_key_values" in key
            },
            "kv_cache": {
                key: value
                for key, value in cache_inventory.items()
                if "kv_cache" in key or "cache_mask" in key
            },
            "mutable_tokenizer_chat_state": self._tokenizer_state(),
            "model_candidate_cache": self._model_output_inventory(),
            "command_history": [
                int(getattr(item, "value", item))
                for item in list(getattr(self.agent, "commands", []))
            ],
        }

    def _generation_contract_inventory(self) -> Dict[str, Any]:
        language_model = getattr(self.model, "language_model", None)
        greedy = getattr(language_model, "greedy_sample", None)
        sample = getattr(language_model, "sample_categorical", None)
        hf_model = getattr(language_model, "model", None)
        if not callable(greedy) or not callable(sample) or hf_model is None:
            raise RuntimeError("SIMLINGO_GENERATION_CALL_PATH_UNAVAILABLE")
        greedy_signature = str(inspect.signature(greedy))
        sample_source = inspect.getsource(sample)
        greedy_parameters = set(inspect.signature(greedy).parameters)
        required = {
            "max_new_tokens",
            "temperature",
            "top_k",
            "top_p",
        }
        if not required.issubset(greedy_parameters):
            raise RuntimeError("SIMLINGO_GREEDY_PARAMETERS_UNAVAILABLE")
        argmax_contract = (
            "temperature <= 0.0" in sample_source
            and "argmax" in sample_source
        )
        if not argmax_contract:
            raise RuntimeError("SIMLINGO_GREEDY_ARGMAX_CONTRACT_UNRESOLVED")
        return {
            "language_model_type": (
                type(language_model).__module__
                + "."
                + type(language_model).__qualname__
            ),
            "greedy_sample_signature": greedy_signature,
            "greedy_sample_source_sha256": _sha256(
                inspect.getsource(greedy).encode("utf-8")
            ),
            "sample_categorical_source_sha256": _sha256(
                sample_source.encode("utf-8")
            ),
            "zero_temperature_argmax_contract": argmax_contract,
            "transformers_generate_used": False,
            "custom_greedy_boundary_translation": {
                "do_sample": (
                    "explicitly passed to DriveClarify boundary; false selects "
                    "the audited custom greedy/argmax path"
                ),
                "num_beams": (
                    "explicitly passed to DriveClarify boundary; one selects "
                    "the single custom greedy path"
                ),
                "use_cache": (
                    "explicitly passed to DriveClarify boundary and injected "
                    "as false into every underlying language-model forward"
                ),
            },
        }

    def run_read_only_runtime_audit(self) -> Mapping[str, Any]:
        self.apply_model_eval()
        training = audit_module_training_state(self.model)
        config_audit = self.config.audit()
        coordinator = RNGCoordinator(self.rng_backends())
        base = coordinator.capture_base()
        restored, restore_verified, restore_reasons = (
            coordinator.restore_and_verify(base)
        )
        cache_inventory = self._cache_inventory()
        generation_contract = self._generation_contract_inventory()
        self._reset_model_outputs()
        failures = []
        if training.status != AUDIT_PASS:
            failures.append("MODEL_TRAINING_AUDIT_" + training.status)
        if config_audit.status != AUDIT_PASS:
            failures.extend(config_audit.reason_codes)
        if not coordinator.all_available(base):
            failures.append("RNG_BACKEND_AVAILABILITY_INCOMPLETE")
        if not restore_verified:
            failures.append(
                "RNG_RESTORE_FAILED:" + ",".join(restore_reasons)
            )
        if not self._cache_inventory_empty(cache_inventory):
            failures.append("PERSISTENT_GENERATION_CACHE_PRESENT")
        audit = {
            "result_type": RUNTIME_AUDIT_SCHEMA,
            "adapter_version": LIVE_ADAPTER_VERSION,
            "status": "PASS" if not failures else "FAIL",
            "reason_codes": failures,
            "model_instance_identity": self.model_instance_identity(),
            "model_training_audit": training.to_dict(),
            "generation_config": self.config.to_dict(),
            "generation_config_audit": config_audit.to_dict(),
            "generation_contract": generation_contract,
            "actual_generation_invocation": "PENDING_CANDIDATE_FORWARD",
            "rng_backends": {
                name: snapshot.public_dict() for name, snapshot in base.items()
            },
            "rng_restore_verification": {
                "verified": restore_verified,
                "restored_digests": dict(restored),
                "reason_codes": list(restore_reasons),
            },
            "deterministic_backend_settings": (
                self._deterministic_backend_inventory()
            ),
            "initial_mutable_outputs_before_reset": self._initial_mutable_outputs,
            "mutable_outputs_after_reset": self._model_output_inventory(),
            "cache_inventory": cache_inventory,
            "cache_inventory_empty": self._cache_inventory_empty(cache_inventory),
            "candidate_forward_count": 0,
            "automatic_continuation": False,
        }
        audit["evidence_sha256"] = _sha256(_canonical_json(audit))
        audit["persistence"] = atomic_write_json(self.runtime_audit_path, audit)
        self._runtime_audit = audit
        self._last_completed_integration_checkpoint = "RUNTIME_AUDIT_PERSISTED"
        if failures:
            raise RuntimeError("SIMLINGO_READ_ONLY_RUNTIME_AUDIT_FAILED")
        return audit

    @contextmanager
    def _generation_boundary(
        self,
        generation_config: DeterministicInferenceConfig,
    ):
        language_model = self.model.language_model
        hf_model = language_model.model
        greedy_had_instance_value = "greedy_sample" in language_model.__dict__
        hf_forward_had_instance_value = "forward" in hf_model.__dict__
        prior_greedy_instance_value = language_model.__dict__.get("greedy_sample")
        prior_hf_forward_instance_value = hf_model.__dict__.get("forward")
        original_greedy = language_model.greedy_sample
        original_hf_forward = hf_model.forward
        invocation: Dict[str, Any] = {
            "greedy_call_count": 0,
            "underlying_language_forward_call_count": 0,
            "all_underlying_use_cache_false": True,
            "boundary_parameters": None,
            "simlingo_caller_parameters": None,
            "sampled_token_ids": None,
        }
        self._generation_boundary_entered = True
        self._last_completed_integration_checkpoint = "GENERATION_BOUNDARY_ENTERED"

        def hf_forward_wrapper(_module: Any, *args: Any, **kwargs: Any) -> Any:
            invocation["underlying_language_forward_call_count"] += 1
            if kwargs.get("use_cache", False) is not False:
                invocation["all_underlying_use_cache_false"] = False
                raise RuntimeError("SIMLINGO_UNDERLYING_USE_CACHE_TRUE")
            kwargs["use_cache"] = False
            return original_hf_forward(*args, **kwargs)

        def greedy_wrapper(*args: Any, **kwargs: Any) -> Any:
            invocation["greedy_call_count"] += 1
            if invocation["greedy_call_count"] != 1:
                raise RuntimeError("SIMLINGO_GREEDY_CALL_COUNT_NOT_ONE")
            caller_values = {
                "max_new_tokens": kwargs.get("max_new_tokens"),
                "temperature": kwargs.get("temperature", "OMITTED"),
                "top_k": kwargs.get("top_k", "OMITTED"),
                "top_p": kwargs.get("top_p", "OMITTED"),
            }
            if caller_values["max_new_tokens"] != generation_config.max_new_tokens:
                raise RuntimeError("SIMLINGO_MAX_NEW_TOKENS_CALLER_MISMATCH")

            def invoke_custom_greedy(
                *,
                do_sample: bool,
                temperature: float,
                top_k: Optional[int],
                top_p: Optional[float],
                num_beams: int,
                max_new_tokens: int,
                use_cache: bool,
            ) -> Any:
                boundary_values = {
                    "do_sample": do_sample,
                    "temperature": float(temperature),
                    "top_k": top_k,
                    "top_p": top_p,
                    "num_beams": num_beams,
                    "max_new_tokens": max_new_tokens,
                    "use_cache": use_cache,
                }
                expected = {
                    "do_sample": False,
                    "temperature": 0.0,
                    "top_k": None,
                    "top_p": None,
                    "num_beams": 1,
                    "max_new_tokens": generation_config.max_new_tokens,
                    "use_cache": False,
                }
                if boundary_values != expected:
                    raise RuntimeError("SIMLINGO_GENERATION_BOUNDARY_CONFIG_MISMATCH")
                invocation["boundary_parameters"] = boundary_values
                invocation["simlingo_caller_parameters"] = caller_values
                forwarded = dict(kwargs)
                forwarded.update(
                    {
                        "temperature": temperature,
                        "top_k": top_k,
                        "top_p": top_p,
                        "max_new_tokens": max_new_tokens,
                    }
                )
                result = original_greedy(*args, **forwarded)
                sampled = result[0].detach().clone().cpu()
                invocation["sampled_token_ids"] = [
                    int(item) for item in sampled.reshape(-1).tolist()
                ]
                invocation["sampled_token_sha256"] = _tensor_sha(
                    self._torch, sampled
                )
                return result

            return invoke_custom_greedy(
                do_sample=generation_config.do_sample,
                temperature=generation_config.temperature,
                top_k=generation_config.top_k,
                top_p=generation_config.top_p,
                num_beams=generation_config.num_beams,
                max_new_tokens=generation_config.max_new_tokens,
                use_cache=generation_config.use_cache,
            )

        language_model.greedy_sample = greedy_wrapper
        hf_model.forward = types.MethodType(hf_forward_wrapper, hf_model)
        try:
            yield invocation
        finally:
            if greedy_had_instance_value:
                language_model.greedy_sample = prior_greedy_instance_value
            else:
                del language_model.greedy_sample
            if hf_forward_had_instance_value:
                hf_model.forward = prior_hf_forward_instance_value
            else:
                del hf_model.forward

    def _label_tensor_storage(self, label: Any) -> set:
        storage = set()
        for name in (
            "phrase_ids",
            "phrase_valid",
            "phrase_mask",
            "loss_masking",
        ):
            value = getattr(label, name, None)
            if isinstance(value, self._torch.Tensor):
                storage.add(_tensor_storage_identity(value))
        return storage

    def _persist_forward_journal(self, status: str) -> Mapping[str, Any]:
        payload = {
            "result_type": FORWARD_JOURNAL_SCHEMA,
            "adapter_version": LIVE_ADAPTER_VERSION,
            "status": status,
            "forward_order_expected": ["A1", "B1", "A2", "B2"],
            "forward_count": len(self._forward_records),
            "records": copy.deepcopy(self._forward_records),
            "automatic_continuation": False,
        }
        payload["evidence_sha256"] = _sha256(_canonical_json(payload))
        payload["persistence"] = atomic_write_json(
            self.forward_journal_path, payload
        )
        return payload

    @staticmethod
    def _exception_link(value: Any) -> Optional[Mapping[str, Any]]:
        if value is None:
            return None
        return {
            "type": type(value).__name__,
            "qualified_type": (
                type(value).__module__ + "." + type(value).__qualname__
            ),
            "message": str(value),
        }

    def _model_proxy_forward_count(self) -> Optional[int]:
        value = getattr(self.model_proxy, "forward_count", None)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return int(value)

    def persist_failure_evidence(
        self,
        *,
        context: Any,
        candidate_id: str,
        stage: str,
        exc: Exception,
        traceback_text: str,
    ) -> Mapping[str, Any]:
        """Atomically persist the original plan-only exception evidence."""

        payload = {
            "result_type": FAILURE_EVIDENCE_SCHEMA,
            "run_id": context.run_id,
            "candidate_id": candidate_id,
            "stage": stage,
            "monotonic_timestamp_ns": time.monotonic_ns(),
            "exception_type": type(exc).__name__,
            "exception_qualified_type": (
                type(exc).__module__ + "." + type(exc).__qualname__
            ),
            "exception_message": str(exc),
            "exception_repr": repr(exc),
            "traceback": traceback_text,
            "cause": self._exception_link(exc.__cause__),
            "context": self._exception_link(exc.__context__),
            "model_proxy_call_entered": self._model_proxy_call_entered,
            "generation_boundary_entered": self._generation_boundary_entered,
            "last_completed_integration_checkpoint": (
                self._last_completed_integration_checkpoint
            ),
            "model_identity": self.model_instance_identity(),
            "source_observation_digest": context.observation_digest,
            "model_proxy_forward_count_before_candidate": (
                self._model_proxy_forward_count_before_candidate
            ),
            "forward_count_before_failure": self._model_proxy_forward_count(),
            "adapter_forward_sequence_position": self._forward_index,
            "complete_forward_record_count": len(self._forward_records),
            "forward_journal_record_created": bool(self._forward_records),
            "candidate_status": "FAIL_DETERMINISM_CONTRACT",
            "output_accepted": False,
            "automatic_continuation": True,
        }
        payload["evidence_sha256"] = _sha256(_canonical_json(payload))
        persistence = atomic_write_json(self.failure_evidence_path, payload)
        return {
            "status": "PERSISTED",
            "schema": FAILURE_EVIDENCE_SCHEMA,
            "path": str(self.failure_evidence_path),
            "sha256": persistence["sha256"],
            "atomic_rename": persistence["atomic_rename"],
            "file_fsync": persistence["file_fsync"],
            "directory_fsync": persistence["directory_fsync"],
        }

    @property
    def forward_records(self) -> Tuple[Mapping[str, Any], ...]:
        return tuple(copy.deepcopy(self._forward_records))

    @property
    def runtime_audit(self) -> Optional[Mapping[str, Any]]:
        return copy.deepcopy(self._runtime_audit)

    def plan_only_forward(
        self,
        *,
        context: Any,
        request: Any,
        candidate_input: SimLingoCandidateSource,
        candidate_state: Mapping[str, Any],
        generation_config: DeterministicInferenceConfig,
    ) -> PlanOnlyOutput:
        self._model_proxy_call_entered = False
        self._generation_boundary_entered = False
        self._last_completed_integration_checkpoint = "PLAN_ONLY_FORWARD_ENTERED"
        self._model_proxy_forward_count_before_candidate = (
            self._model_proxy_forward_count()
        )
        if generation_config.digest != self.config.digest:
            raise RuntimeError("SIMLINGO_LIVE_GENERATION_CONFIG_CHANGED")
        self._forward_index += 1
        expected = (("A1", "A"), ("B1", "B"), ("A2", "A"), ("B2", "B"))
        if self._forward_index > len(expected):
            raise RuntimeError("SIMLINGO_EXACTLY_FOUR_FORWARD_EXCEEDED")
        candidate_id, group = expected[self._forward_index - 1]
        if request.expected_repetition_group != group:
            raise RuntimeError("SIMLINGO_FORWARD_ORDER_CHANGED")
        if candidate_input.interpretations[group] != request.interpretation:
            raise RuntimeError("SIMLINGO_INTERPRETATION_IDENTITY_MISMATCH")
        if not self.inference_mode_active():
            raise RuntimeError("SIMLINGO_INFERENCE_MODE_INACTIVE_AT_FORWARD")
        self._reset_model_outputs()
        before_outputs = self._model_output_inventory()
        before_cache = self._cache_inventory()
        if not self._cache_inventory_empty(before_cache):
            raise RuntimeError("SIMLINGO_PERSISTENT_CACHE_BEFORE_FORWARD")

        label = self.label_builder(request.interpretation)
        label_storage = self._label_tensor_storage(label)
        if label_storage & self._label_storage_identities:
            raise RuntimeError("SIMLINGO_PROMPT_TENSOR_STORAGE_SHARED")
        self._label_storage_identities.update(label_storage)
        self._retained_labels.append(label)
        common = candidate_input.common_inputs
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
        source_storage = _source_storage_identities(
            self._torch, candidate_input
        )
        actual_common = {
            name: getattr(model_input, name) for name in _COMMON_INPUT_FIELDS
        }
        expected_fingerprints = {
            name: _input_fingerprint(self._torch, common[name])
            for name in _COMMON_INPUT_FIELDS
        }
        actual_fingerprints = {
            name: _input_fingerprint(self._torch, actual_common[name])
            for name in _COMMON_INPUT_FIELDS
        }
        if actual_fingerprints != expected_fingerprints:
            raise RuntimeError("SIMLINGO_MODEL_INPUT_COMMON_VALUE_CHANGED")
        actual_common_storage = set()
        for name in _COMMON_INPUT_FIELDS:
            actual_common_storage.update(
                _input_storage_identities(
                    self._torch,
                    actual_common[name],
                )
            )
        if actual_common_storage != source_storage:
            raise RuntimeError("SIMLINGO_MODEL_INPUT_NOT_ISOLATED_CLONE")

        self._last_completed_integration_checkpoint = "MODEL_INPUT_ISOLATION_VERIFIED"
        with self._generation_boundary(generation_config) as generation:
            self._model_proxy_call_entered = True
            self._last_completed_integration_checkpoint = "MODEL_PROXY_CALL_ENTERED"
            speed, route, language = self.model_proxy(model_input)
            self._last_completed_integration_checkpoint = "MODEL_PROXY_CALL_RETURNED"
            route_cpu = route.detach().float().contiguous().cpu().clone()
            speed_cpu = speed.detach().float().contiguous().cpu().clone()
            language_copy = tuple(str(item) for item in copy.deepcopy(language))

        if generation["greedy_call_count"] != 1:
            raise RuntimeError("SIMLINGO_GREEDY_CALL_COUNT_NOT_ONE")
        if generation["underlying_language_forward_call_count"] <= 0:
            raise RuntimeError("SIMLINGO_LANGUAGE_FORWARD_NOT_OBSERVED")
        if generation["all_underlying_use_cache_false"] is not True:
            raise RuntimeError("SIMLINGO_UNDERLYING_USE_CACHE_NOT_FALSE")
        if generation["sampled_token_ids"] is None:
            raise RuntimeError("SIMLINGO_GENERATED_TOKENS_NOT_CAPTURED")
        output_inventory_before_reset = self._model_output_inventory()
        record = {
            "candidate_id": candidate_id,
            "interpretation_id": group,
            "sequence_position": self._forward_index,
            "model_instance_identity": self.model_instance_identity(),
            "source_observation_identity": context.observation_id,
            "source_frame": context.source_frame,
            "freshness_token": context.freshness_token,
            "inference_mode_active": True,
            "generation_invocation": copy.deepcopy(generation),
            "input_isolation": {
                "common_tensor_storage": [
                    list(item) for item in sorted(actual_common_storage)
                ],
                "prompt_tensor_storage": [
                    list(item) for item in sorted(label_storage)
                ],
                "source_storage_reused": False,
                "prior_candidate_storage_reused": False,
            },
            "mutable_outputs_before_forward": before_outputs,
            "mutable_outputs_after_forward_before_reset": (
                output_inventory_before_reset
            ),
            "cache_before_forward": before_cache,
            "route_sha256": _tensor_sha(self._torch, route_cpu),
            "speed_sha256": _tensor_sha(self._torch, speed_cpu),
            "route_dtype": str(route_cpu.dtype),
            "speed_dtype": str(speed_cpu.dtype),
            "route_device": str(route_cpu.device),
            "speed_device": str(speed_cpu.device),
            "pred_route": route_cpu.tolist(),
            "pred_speed_wps": speed_cpu.tolist(),
            "language": list(language_copy),
            "status": "OUTPUT_CLONED_CPU_PENDING_POSTCHECK",
        }
        self._forward_records.append(record)
        self._persist_forward_journal("IN_PROGRESS")
        self._last_completed_integration_checkpoint = "FORWARD_JOURNAL_PERSISTED"

        self._reset_model_outputs()
        after_cache = self._cache_inventory()
        if not self._cache_inventory_empty(after_cache):
            raise RuntimeError("SIMLINGO_PERSISTENT_CACHE_AFTER_FORWARD")
        candidate_state["generated_token_history"].extend(
            generation["sampled_token_ids"]
        )
        candidate_state["kv_cache"]["underlying_use_cache"] = False
        candidate_state["conversation_history"].append(request.interpretation)
        record.update(
            {
                "mutable_outputs_after_reset": self._model_output_inventory(),
                "cache_after_forward": after_cache,
                "original_source_input_unchanged_pending_runner_verify": True,
                "status": "FORWARD_COMPLETE_DETERMINISM_PENDING",
            }
        )
        journal_status = (
            "FOUR_FORWARDS_COMPLETE_DETERMINISM_PENDING"
            if self._forward_index == 4
            else "IN_PROGRESS"
        )
        self._persist_forward_journal(journal_status)

        if route_cpu.device.type != "cpu" or speed_cpu.device.type != "cpu":
            raise RuntimeError("SIMLINGO_OUTPUT_NOT_CPU")
        if route_cpu.requires_grad or speed_cpu.requires_grad:
            raise RuntimeError("SIMLINGO_OUTPUT_REQUIRES_GRAD")
        return PlanOnlyOutput(
            route=_tensor_to_trajectory(route_cpu),
            speed=_tensor_to_trajectory(speed_cpu),
            language=language_copy,
            generated_token_ids=tuple(generation["sampled_token_ids"]),
            source_observation_identity=context.observation_id,
            source_frame=context.source_frame,
            freshness_token=context.freshness_token,
            model_instance_identity=context.model_instance_identity,
        )
