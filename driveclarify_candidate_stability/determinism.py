"""CPU-only determinism contracts and isolation utilities.

This module deliberately imports only the Python standard library.  Torch,
NumPy and live-model support are supplied later through explicit adapters.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import pickle
import random
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Iterable, Mapping, Optional, Protocol, Sequence, Tuple


AUDIT_PASS = "PASS"
AUDIT_FAIL = "FAIL"
AUDIT_UNRESOLVED = "UNRESOLVED"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _typed_value(value: Any) -> Any:
    """Return a type-preserving, JSON-canonical representation.

    Live tensor-like inputs intentionally are not accepted here.  Their future
    adapter must provide clone/detach and digest semantics without weakening
    this standard-library implementation.
    """

    if value is None:
        return {"type": "none", "value": None}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NONFINITE_VALUE_NOT_DIGESTIBLE")
        return {"type": "float", "value": value.hex()}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, bytearray):
        return {"type": "bytearray", "value": bytes(value).hex()}
    if isinstance(value, list):
        return {"type": "list", "value": [_typed_value(item) for item in value]}
    if isinstance(value, tuple):
        return {"type": "tuple", "value": [_typed_value(item) for item in value]}
    if isinstance(value, dict):
        entries = [
            (_typed_value(key), _typed_value(item)) for key, item in value.items()
        ]
        entries.sort(
            key=lambda pair: json.dumps(
                pair[0], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
        return {"type": "dict", "value": entries}
    raise TypeError("UNSUPPORTED_STANDARD_LIBRARY_DIGEST_TYPE:" + type(value).__name__)


def canonical_digest(value: Any) -> str:
    raw = json.dumps(
        _typed_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(raw)


def _mutable_object_ids(value: Any, seen: Optional[set] = None) -> set:
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return set()
    seen.add(identity)
    found = set()
    if isinstance(value, (dict, list, set, bytearray)):
        found.add(identity)
    if isinstance(value, dict):
        for key, item in value.items():
            found.update(_mutable_object_ids(key, seen))
            found.update(_mutable_object_ids(item, seen))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found.update(_mutable_object_ids(item, seen))
    return found


@dataclass(frozen=True)
class ConfigAudit:
    status: str
    reason_codes: Tuple[str, ...]
    generation_config_digest: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "generation_config_digest": self.generation_config_digest,
        }


@dataclass(frozen=True)
class DeterministicInferenceConfig:
    """Fully explicit, immutable candidate inference configuration.

    There are intentionally no dataclass defaults: construction itself proves
    that every governed value was supplied or loaded by the strict reader.
    """

    model_eval_required: bool
    inference_mode_required: bool
    do_sample: bool
    temperature: float
    top_p: Optional[float]
    top_k: Optional[int]
    num_beams: int
    max_new_tokens: int
    use_cache: bool
    autocast_enabled: bool
    autocast_dtype: Optional[str]
    tf32_enabled: bool
    deterministic_algorithms: bool
    cudnn_deterministic: bool
    cudnn_benchmark: bool
    reset_rng_per_candidate: bool
    isolate_cache_per_candidate: bool
    isolate_history_per_candidate: bool
    clone_inputs_per_candidate: bool

    SCHEMA: ClassVar[str] = "DRIVECLARIFY_DETERMINISTIC_INFERENCE_CONFIG_V1"
    FIELD_NAMES: ClassVar[Tuple[str, ...]] = (
        "model_eval_required",
        "inference_mode_required",
        "do_sample",
        "temperature",
        "top_p",
        "top_k",
        "num_beams",
        "max_new_tokens",
        "use_cache",
        "autocast_enabled",
        "autocast_dtype",
        "tf32_enabled",
        "deterministic_algorithms",
        "cudnn_deterministic",
        "cudnn_benchmark",
        "reset_rng_per_candidate",
        "isolate_cache_per_candidate",
        "isolate_history_per_candidate",
        "clone_inputs_per_candidate",
    )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeterministicInferenceConfig":
        missing = [name for name in cls.FIELD_NAMES if name not in value]
        extra = sorted(set(value) - set(cls.FIELD_NAMES))
        if missing:
            raise ValueError("GENERATION_CONFIG_FIELDS_MISSING:" + ",".join(missing))
        if extra:
            raise ValueError("GENERATION_CONFIG_FIELDS_UNKNOWN:" + ",".join(extra))
        return cls(**{name: value[name] for name in cls.FIELD_NAMES})

    def to_dict(self) -> Dict[str, Any]:
        output = {"schema": self.SCHEMA}
        output.update({name: getattr(self, name) for name in self.FIELD_NAMES})
        return output

    @property
    def digest(self) -> str:
        raw = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _sha256(raw)

    def audit(self) -> ConfigAudit:
        reasons = []
        boolean_fields = (
            "model_eval_required",
            "inference_mode_required",
            "do_sample",
            "use_cache",
            "autocast_enabled",
            "tf32_enabled",
            "deterministic_algorithms",
            "cudnn_deterministic",
            "cudnn_benchmark",
            "reset_rng_per_candidate",
            "isolate_cache_per_candidate",
            "isolate_history_per_candidate",
            "clone_inputs_per_candidate",
        )
        for name in boolean_fields:
            if not isinstance(getattr(self, name), bool):
                reasons.append("GENERATION_CONFIG_TYPE_INVALID:" + name)
        if self.model_eval_required is not True:
            reasons.append("MODEL_EVAL_NOT_REQUIRED")
        if self.inference_mode_required is not True:
            reasons.append("INFERENCE_MODE_NOT_REQUIRED")
        if self.do_sample is not False:
            reasons.append("DO_SAMPLE_MUST_BE_FALSE")
        if isinstance(self.temperature, bool) or not isinstance(
            self.temperature, (int, float)
        ):
            reasons.append("TEMPERATURE_TYPE_INVALID")
        elif float(self.temperature) != 0.0:
            reasons.append("TEMPERATURE_MUST_BE_ZERO")
        if self.top_p is not None:
            reasons.append("TOP_P_MUST_BE_EXPLICIT_NONE")
        if self.top_k is not None:
            reasons.append("TOP_K_MUST_BE_EXPLICIT_NONE")
        if isinstance(self.num_beams, bool) or self.num_beams != 1:
            reasons.append("NUM_BEAMS_MUST_BE_ONE")
        if (
            isinstance(self.max_new_tokens, bool)
            or not isinstance(self.max_new_tokens, int)
            or self.max_new_tokens <= 0
        ):
            reasons.append("MAX_NEW_TOKENS_INVALID")
        if self.autocast_enabled:
            if not isinstance(self.autocast_dtype, str) or not self.autocast_dtype:
                reasons.append("AUTOCAST_DTYPE_REQUIRED")
        elif self.autocast_dtype is not None:
            reasons.append("AUTOCAST_DTYPE_MUST_BE_NONE_WHEN_DISABLED")
        if self.tf32_enabled is not False:
            reasons.append("TF32_MUST_BE_DISABLED")
        if self.deterministic_algorithms is not True:
            reasons.append("DETERMINISTIC_ALGORITHMS_MUST_BE_ENABLED")
        if self.cudnn_deterministic is not True:
            reasons.append("CUDNN_DETERMINISTIC_MUST_BE_ENABLED")
        if self.cudnn_benchmark is not False:
            reasons.append("CUDNN_BENCHMARK_MUST_BE_DISABLED")
        for name in (
            "reset_rng_per_candidate",
            "isolate_cache_per_candidate",
            "isolate_history_per_candidate",
            "clone_inputs_per_candidate",
        ):
            if getattr(self, name) is not True:
                reasons.append(name.upper() + "_REQUIRED")
        return ConfigAudit(
            status=AUDIT_FAIL if reasons else AUDIT_PASS,
            reason_codes=tuple(reasons),
            generation_config_digest=self.digest,
        )


@dataclass(frozen=True)
class ModuleTrainingRecord:
    module_path: str
    module_type: str
    training: Optional[bool]
    stochastic_layer: bool
    dropout_probability: Optional[float]
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_path": self.module_path,
            "module_type": self.module_type,
            "training": self.training,
            "stochastic_layer": self.stochastic_layer,
            "dropout_probability": self.dropout_probability,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class ModuleTrainingAudit:
    status: str
    reason_codes: Tuple[str, ...]
    traversal: str
    modules: Tuple[ModuleTrainingRecord, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "traversal": self.traversal,
            "module_count": len(self.modules),
            "modules": [item.to_dict() for item in self.modules],
        }


def _module_is_stochastic(module: Any, module_type: str) -> bool:
    lowered = module_type.lower().replace("_", "")
    markers = (
        "dropout",
        "droppath",
        "stochasticdepth",
        "randomlayer",
        "randaugment",
    )
    return any(marker in lowered for marker in markers) or any(
        getattr(module, marker, False) is True
        for marker in (
            "determinism_random_layer",
            "stochastic",
            "uses_randomness",
        )
    )


def _dropout_probability(module: Any) -> Optional[float]:
    for name in ("p", "drop_prob", "dropout_probability"):
        value = getattr(module, name, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _enumerate_modules(model: Any) -> Tuple[str, Tuple[Tuple[str, Any], ...]]:
    named_modules = getattr(model, "named_modules", None)
    if callable(named_modules):
        modules = tuple(
            (path or "<root>", module) for path, module in named_modules()
        )
        if not modules:
            raise ValueError("NAMED_MODULES_RETURNED_EMPTY")
        return "named_modules", modules

    named_children = getattr(model, "named_children", None)
    if not callable(named_children):
        raise ValueError("MODULE_TREE_ENUMERATION_UNAVAILABLE")
    output = []
    visited = set()

    def visit(path: str, module: Any) -> None:
        if id(module) in visited:
            return
        visited.add(id(module))
        output.append((path or "<root>", module))
        child_method = getattr(module, "named_children", None)
        if not callable(child_method):
            raise ValueError("CHILD_ENUMERATION_UNAVAILABLE:" + (path or "<root>"))
        for name, child in child_method():
            child_path = name if not path else path + "." + name
            visit(child_path, child)

    visit("", model)
    return "named_children_recursive", tuple(output)


def audit_module_training_state(model: Any) -> ModuleTrainingAudit:
    """Recursively audit root/child training flags without importing torch."""

    try:
        traversal, modules = _enumerate_modules(model)
    except Exception as exc:  # fail closed on an uninspectable module tree
        return ModuleTrainingAudit(
            status=AUDIT_UNRESOLVED,
            reason_codes=(
                "MODULE_TREE_ENUMERATION_UNRESOLVED:" + type(exc).__name__,
            ),
            traversal="UNRESOLVED",
            modules=(),
        )

    records = []
    global_reasons = []
    unresolved = False
    failed = False
    for path, module in modules:
        module_type = type(module).__name__
        stochastic = _module_is_stochastic(module, module_type)
        training_value = getattr(module, "training", None)
        training = training_value if isinstance(training_value, bool) else None
        probability = _dropout_probability(module) if stochastic else None
        reasons = []
        if training is None:
            reasons.append("MODULE_TRAINING_FLAG_UNRESOLVED")
            unresolved = True
        elif training:
            reasons.append("MODULE_TRAINING_TRUE")
            if stochastic:
                reasons.append("STOCHASTIC_MODULE_TRAINING_TRUE")
            failed = True
        if stochastic and probability is None:
            reasons.append("STOCHASTIC_PROBABILITY_UNRESOLVED")
            unresolved = True
        records.append(
            ModuleTrainingRecord(
                module_path=path,
                module_type=module_type,
                training=training,
                stochastic_layer=stochastic,
                dropout_probability=probability,
                reason_codes=tuple(reasons),
            )
        )
        global_reasons.extend(path + ":" + reason for reason in reasons)
    status = AUDIT_FAIL if failed else AUDIT_UNRESOLVED if unresolved else AUDIT_PASS
    return ModuleTrainingAudit(
        status=status,
        reason_codes=tuple(global_reasons),
        traversal=traversal,
        modules=tuple(records),
    )


class RNGBackend(Protocol):
    name: str

    def availability(self) -> Tuple[bool, Optional[str]]:
        ...

    def capture_state(self) -> Any:
        ...

    def restore_state(self, state: Any) -> None:
        ...

    def state_digest(self, state: Any) -> str:
        ...


class PythonRandomBackend:
    """Real capture/restore adapter for Python's random generator."""

    name = "python_random"

    def __init__(self, generator: Any = random) -> None:
        self._generator = generator

    def availability(self) -> Tuple[bool, Optional[str]]:
        return True, None

    def capture_state(self) -> Any:
        return self._generator.getstate()

    def restore_state(self, state: Any) -> None:
        self._generator.setstate(state)

    def state_digest(self, state: Any) -> str:
        return _sha256(pickle.dumps(state, protocol=4))


class UnavailableRNGBackend:
    """Explicit placeholder for a backend unavailable in the current process."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self._reason = reason

    def availability(self) -> Tuple[bool, Optional[str]]:
        return False, self._reason

    def capture_state(self) -> Any:
        raise RuntimeError("RNG_BACKEND_UNAVAILABLE:" + self.name)

    def restore_state(self, state: Any) -> None:
        raise RuntimeError("RNG_BACKEND_UNAVAILABLE:" + self.name)

    def state_digest(self, state: Any) -> str:
        raise RuntimeError("RNG_BACKEND_UNAVAILABLE:" + self.name)


@dataclass(frozen=True)
class RNGSnapshot:
    backend: str
    availability: str
    digest: Optional[str]
    reason_code: Optional[str]
    state: Any = field(repr=False, compare=False)

    def public_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "availability": self.availability,
            "digest": self.digest,
            "reason_code": self.reason_code,
        }


class RNGCoordinator:
    REQUIRED_BACKENDS = (
        "python_random",
        "numpy_random",
        "torch_cpu",
        "torch_cuda_all",
    )

    def __init__(self, backends: Sequence[RNGBackend]) -> None:
        by_name = {backend.name: backend for backend in backends}
        if len(by_name) != len(backends):
            raise ValueError("RNG_BACKEND_NAME_DUPLICATE")
        missing = [name for name in self.REQUIRED_BACKENDS if name not in by_name]
        if missing:
            raise ValueError("RNG_BACKENDS_MISSING:" + ",".join(missing))
        self._backends = by_name

    def capture_base(self) -> Mapping[str, RNGSnapshot]:
        output = {}
        for name in self.REQUIRED_BACKENDS:
            backend = self._backends[name]
            available, reason = backend.availability()
            if not available:
                output[name] = RNGSnapshot(
                    backend=name,
                    availability="UNAVAILABLE",
                    digest=None,
                    reason_code=reason or "RNG_BACKEND_UNAVAILABLE",
                    state=None,
                )
                continue
            try:
                state = backend.capture_state()
                output[name] = RNGSnapshot(
                    backend=name,
                    availability="AVAILABLE",
                    digest=backend.state_digest(state),
                    reason_code=None,
                    state=state,
                )
            except Exception as exc:
                output[name] = RNGSnapshot(
                    backend=name,
                    availability="UNRESOLVED",
                    digest=None,
                    reason_code="RNG_CAPTURE_FAILED:" + type(exc).__name__,
                    state=None,
                )
        return output

    @staticmethod
    def all_available(snapshots: Mapping[str, RNGSnapshot]) -> bool:
        return all(
            snapshot.availability == "AVAILABLE"
            for snapshot in snapshots.values()
        )

    def restore_and_verify(
        self, snapshots: Mapping[str, RNGSnapshot]
    ) -> Tuple[Mapping[str, Optional[str]], bool, Tuple[str, ...]]:
        current = {}
        reasons = []
        for name in self.REQUIRED_BACKENDS:
            snapshot = snapshots[name]
            if snapshot.availability != "AVAILABLE":
                current[name] = None
                reasons.append(name + ":RNG_BACKEND_NOT_AVAILABLE")
                continue
            backend = self._backends[name]
            try:
                backend.restore_state(snapshot.state)
                restored = backend.capture_state()
                digest = backend.state_digest(restored)
                current[name] = digest
                if digest != snapshot.digest:
                    reasons.append(name + ":RNG_RESTORE_DIGEST_MISMATCH")
            except Exception as exc:
                current[name] = None
                reasons.append(name + ":RNG_RESTORE_FAILED:" + type(exc).__name__)
        return current, not reasons, tuple(reasons)

    def current_digests(self) -> Mapping[str, Optional[str]]:
        output = {}
        for name in self.REQUIRED_BACKENDS:
            backend = self._backends[name]
            available, _ = backend.availability()
            if not available:
                output[name] = None
                continue
            try:
                state = backend.capture_state()
                output[name] = backend.state_digest(state)
            except Exception:
                output[name] = None
        return output


class CandidateInputIsolation(Protocol):
    def prepare(self, source: Any) -> "IsolatedCandidateInput":
        ...

    def verify(
        self, isolated: "IsolatedCandidateInput", source: Any
    ) -> "InputIsolationAudit":
        ...


@dataclass
class IsolatedCandidateInput:
    value: Any
    source_pre_digest: str
    candidate_pre_digest: str
    source_mutable_ids: set = field(repr=False)
    candidate_mutable_ids: set = field(repr=False)


@dataclass(frozen=True)
class InputIsolationAudit:
    status: str
    source_pre_digest: str
    source_post_digest: Optional[str]
    candidate_pre_digest: str
    candidate_post_digest: Optional[str]
    no_shared_mutable_objects: bool
    source_unchanged: bool
    candidate_unchanged: bool
    reason_codes: Tuple[str, ...]

    @property
    def verified(self) -> bool:
        return self.status == AUDIT_PASS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "source_pre_digest": self.source_pre_digest,
            "source_post_digest": self.source_post_digest,
            "candidate_pre_digest": self.candidate_pre_digest,
            "candidate_post_digest": self.candidate_post_digest,
            "no_shared_mutable_objects": self.no_shared_mutable_objects,
            "source_unchanged": self.source_unchanged,
            "candidate_unchanged": self.candidate_unchanged,
            "reason_codes": list(self.reason_codes),
        }


class StandardLibraryInputIsolation:
    """Deep-copy isolation for nested standard-library fixtures."""

    def prepare(self, source: Any) -> IsolatedCandidateInput:
        source_digest = canonical_digest(source)
        candidate = copy.deepcopy(source)
        candidate_digest = canonical_digest(candidate)
        source_ids = _mutable_object_ids(source)
        candidate_ids = _mutable_object_ids(candidate)
        if source_digest != candidate_digest:
            raise ValueError("INPUT_CLONE_DIGEST_MISMATCH")
        return IsolatedCandidateInput(
            value=candidate,
            source_pre_digest=source_digest,
            candidate_pre_digest=candidate_digest,
            source_mutable_ids=source_ids,
            candidate_mutable_ids=candidate_ids,
        )

    def verify(
        self, isolated: IsolatedCandidateInput, source: Any
    ) -> InputIsolationAudit:
        reasons = []
        try:
            source_post = canonical_digest(source)
        except Exception as exc:
            source_post = None
            reasons.append("SOURCE_INPUT_POST_DIGEST_FAILED:" + type(exc).__name__)
        try:
            candidate_post = canonical_digest(isolated.value)
        except Exception as exc:
            candidate_post = None
            reasons.append("CANDIDATE_INPUT_POST_DIGEST_FAILED:" + type(exc).__name__)
        no_shared = not (
            isolated.source_mutable_ids & isolated.candidate_mutable_ids
        )
        source_unchanged = source_post == isolated.source_pre_digest
        candidate_unchanged = candidate_post == isolated.candidate_pre_digest
        if not no_shared:
            reasons.append("INPUT_MUTABLE_OBJECT_SHARED")
        if not source_unchanged:
            reasons.append("SOURCE_INPUT_MUTATED")
        if not candidate_unchanged:
            reasons.append("CANDIDATE_INPUT_MUTATED")
        return InputIsolationAudit(
            status=AUDIT_FAIL if reasons else AUDIT_PASS,
            source_pre_digest=isolated.source_pre_digest,
            source_post_digest=source_post,
            candidate_pre_digest=isolated.candidate_pre_digest,
            candidate_post_digest=candidate_post,
            no_shared_mutable_objects=no_shared,
            source_unchanged=source_unchanged,
            candidate_unchanged=candidate_unchanged,
            reason_codes=tuple(reasons),
        )


REQUIRED_MUTABLE_STATE_KEYS = (
    "conversation_history",
    "generated_token_history",
    "past_key_values",
    "kv_cache",
    "mutable_tokenizer_chat_state",
    "model_candidate_cache",
    "command_history",
)
CACHE_STATE_KEYS = ("past_key_values", "kv_cache", "model_candidate_cache")
HISTORY_STATE_KEYS = (
    "conversation_history",
    "generated_token_history",
    "mutable_tokenizer_chat_state",
    "command_history",
)


def _empty_like(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, tuple):
        return ()
    if isinstance(value, set):
        return set()
    if isinstance(value, bytearray):
        return bytearray()
    raise TypeError("CACHE_STATE_CANNOT_BE_CLEARED:" + type(value).__name__)


@dataclass
class IsolatedCandidateState:
    value: Dict[str, Any]
    baseline_digest: str
    candidate_pre_digest: str
    baseline_mutable_ids: set = field(repr=False)
    candidate_mutable_ids: set = field(repr=False)
    cache_cleared: bool = False


@dataclass(frozen=True)
class CandidateStateIsolationAudit:
    status: str
    baseline_pre_digest: str
    baseline_post_digest: Optional[str]
    candidate_pre_digest: str
    candidate_post_digest: Optional[str]
    history_isolation_verified: bool
    cache_isolation_verified: bool
    no_cross_candidate_mutable_objects: bool
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "baseline_pre_digest": self.baseline_pre_digest,
            "baseline_post_digest": self.baseline_post_digest,
            "candidate_pre_digest": self.candidate_pre_digest,
            "candidate_post_digest": self.candidate_post_digest,
            "history_isolation_verified": self.history_isolation_verified,
            "cache_isolation_verified": self.cache_isolation_verified,
            "no_cross_candidate_mutable_objects": (
                self.no_cross_candidate_mutable_objects
            ),
            "reason_codes": list(self.reason_codes),
        }


class CandidateStateIsolation(Protocol):
    def capture_baseline(self, state: Mapping[str, Any]) -> str:
        ...

    def prepare(
        self, state: Mapping[str, Any], baseline_digest: str
    ) -> IsolatedCandidateState:
        ...

    def verify(
        self,
        state: Mapping[str, Any],
        isolated: IsolatedCandidateState,
        prior_candidate_mutable_ids: Iterable[int],
    ) -> CandidateStateIsolationAudit:
        ...


class StandardLibraryStateIsolation:
    """Isolate all governed histories and clear candidate-specific caches."""

    @staticmethod
    def _validate_keys(state: Mapping[str, Any]) -> None:
        missing = [name for name in REQUIRED_MUTABLE_STATE_KEYS if name not in state]
        if missing:
            raise ValueError("CANDIDATE_STATE_FIELDS_MISSING:" + ",".join(missing))

    def capture_baseline(self, state: Mapping[str, Any]) -> str:
        self._validate_keys(state)
        return canonical_digest(dict(state))

    def prepare(
        self, state: Mapping[str, Any], baseline_digest: str
    ) -> IsolatedCandidateState:
        self._validate_keys(state)
        if canonical_digest(dict(state)) != baseline_digest:
            raise ValueError("CANDIDATE_STATE_BASELINE_CHANGED_BEFORE_ISOLATION")
        candidate = copy.deepcopy(dict(state))
        for key in CACHE_STATE_KEYS:
            candidate[key] = _empty_like(candidate[key])
        baseline_ids = _mutable_object_ids(state)
        candidate_ids = _mutable_object_ids(candidate)
        cache_cleared = all(
            candidate[key] in (None, (), [], {}, set(), bytearray())
            for key in CACHE_STATE_KEYS
        )
        return IsolatedCandidateState(
            value=candidate,
            baseline_digest=baseline_digest,
            candidate_pre_digest=canonical_digest(candidate),
            baseline_mutable_ids=baseline_ids,
            candidate_mutable_ids=candidate_ids,
            cache_cleared=cache_cleared,
        )

    def verify(
        self,
        state: Mapping[str, Any],
        isolated: IsolatedCandidateState,
        prior_candidate_mutable_ids: Iterable[int],
    ) -> CandidateStateIsolationAudit:
        reasons = []
        try:
            baseline_post = canonical_digest(dict(state))
        except Exception as exc:
            baseline_post = None
            reasons.append("STATE_BASELINE_POST_DIGEST_FAILED:" + type(exc).__name__)
        try:
            candidate_post = canonical_digest(isolated.value)
        except Exception as exc:
            candidate_post = None
            reasons.append("CANDIDATE_STATE_POST_DIGEST_FAILED:" + type(exc).__name__)
        baseline_unchanged = baseline_post == isolated.baseline_digest
        no_baseline_sharing = not (
            isolated.baseline_mutable_ids & isolated.candidate_mutable_ids
        )
        prior_ids = set(prior_candidate_mutable_ids)
        no_cross = not (prior_ids & isolated.candidate_mutable_ids)
        history_verified = baseline_unchanged and no_baseline_sharing and no_cross
        cache_verified = (
            history_verified
            and isolated.cache_cleared
            and all(key in isolated.value for key in CACHE_STATE_KEYS)
        )
        if not baseline_unchanged:
            reasons.append("CANDIDATE_STATE_BASELINE_MUTATED")
        if not no_baseline_sharing:
            reasons.append("CANDIDATE_STATE_SHARES_BASELINE_MUTABLE_OBJECT")
        if not no_cross:
            reasons.append("CANDIDATE_STATE_SHARED_ACROSS_CANDIDATES")
        if not isolated.cache_cleared:
            reasons.append("CANDIDATE_CACHE_NOT_CLEARED")
        return CandidateStateIsolationAudit(
            status=AUDIT_FAIL if reasons else AUDIT_PASS,
            baseline_pre_digest=isolated.baseline_digest,
            baseline_post_digest=baseline_post,
            candidate_pre_digest=isolated.candidate_pre_digest,
            candidate_post_digest=candidate_post,
            history_isolation_verified=history_verified,
            cache_isolation_verified=cache_verified,
            no_cross_candidate_mutable_objects=no_cross,
            reason_codes=tuple(reasons),
        )
