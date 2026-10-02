"""Static DriveClarify V3 arm declarations; never imports or builds PEFT."""

from types import MappingProxyType


HIDDEN_SIZE = 896
A2_NAMESPACE = "driveclarify_route_switch_r8_v1"
A2_RANK = 8
A2_ALPHA = 16
A2_DROPOUT = 0.05
A2_A_INITIALIZATION = "NORMAL_MEAN_0_STD_0.02"
A2_B_INITIALIZATION = "ALL_ZEROS"

A2_TARGET_PATHS = tuple(
    "language_model.model.base_model.model.model.layers.{}.self_attn.{}".format(
        layer, projection
    )
    for layer in (20, 21, 22, 23)
    for projection in ("q_proj", "v_proj")
)


def _a2_tensor_names():
    names = ["route_switch_adapter.delta_switch"]
    for target in A2_TARGET_PATHS:
        for factor in ("lora_A", "lora_B"):
            names.append("{}.{}.{}.weight".format(target, factor, A2_NAMESPACE))
    return tuple(names)


A2_TRAINABLE_TENSORS = _a2_tensor_names()
A3_TRAINABLE_TENSORS = (
    "route_switch_adapter.delta_switch",
    "adaptors.driving.query_embeds_wps",
    "adaptors.driving.route_head.0.weight",
    "adaptors.driving.route_head.0.bias",
    "adaptors.driving.route_head.2.weight",
    "adaptors.driving.route_head.2.bias",
    "adaptors.driving.route_head.4.weight",
)

A2_TENSOR_NUMELS = {"route_switch_adapter.delta_switch": HIDDEN_SIZE}
for _target in A2_TARGET_PATHS:
    _out_features = HIDDEN_SIZE if _target.endswith("q_proj") else 128
    A2_TENSOR_NUMELS["{}.lora_A.{}.weight".format(_target, A2_NAMESPACE)] = (
        A2_RANK * HIDDEN_SIZE
    )
    A2_TENSOR_NUMELS["{}.lora_B.{}.weight".format(_target, A2_NAMESPACE)] = (
        _out_features * A2_RANK
    )
A2_TENSOR_NUMELS = MappingProxyType(A2_TENSOR_NUMELS)

A3_TENSOR_NUMELS = MappingProxyType({
    "route_switch_adapter.delta_switch": 896,
    "adaptors.driving.query_embeds_wps": 20 * 896,
    "adaptors.driving.route_head.0.weight": 512 * 896,
    "adaptors.driving.route_head.0.bias": 512,
    "adaptors.driving.route_head.2.weight": 256 * 512,
    "adaptors.driving.route_head.2.bias": 256,
    "adaptors.driving.route_head.4.weight": 2 * 256,
})

ARM_PARAMETER_COUNTS = MappingProxyType({
    "A0": 0,
    "A1": 896,
    "A2": 91008,
    "A3": 609920,
})


def validate_route_switch_checkpoint_compatibility(missing_keys, unexpected_keys):
    """Accept released A0 or already-adapted route-switch state layouts."""
    missing = set(missing_keys)
    unexpected = set(unexpected_keys)
    if unexpected:
        raise ValueError("ROUTE_SWITCH_CHECKPOINT_UNEXPECTED_KEYS")
    if not missing:
        return "ADAPTED_DELTA_LOADED"
    if missing == {"route_switch_adapter.delta_switch"}:
        return "RELEASED_CHECKPOINT_ZERO_DELTA_RETAINED"
    raise ValueError("ROUTE_SWITCH_CHECKPOINT_MISSING_KEYS_INVALID")


def expected_trainable_tensors(arm):
    if arm == "A0":
        return ()
    if arm == "A1":
        return ("route_switch_adapter.delta_switch",)
    if arm == "A2":
        return A2_TRAINABLE_TENSORS
    if arm == "A3":
        return A3_TRAINABLE_TENSORS
    raise ValueError("UNKNOWN_ADAPTATION_ARM")


def validate_static_parameter_contract(arm, named_numels, trainable_names):
    expected_names = expected_trainable_tensors(arm)
    if tuple(sorted(trainable_names)) != tuple(sorted(expected_names)):
        raise ValueError("TRAINABLE_TENSOR_SET_MISMATCH")
    if arm == "A2":
        expected_numels = A2_TENSOR_NUMELS
    elif arm == "A3":
        expected_numels = A3_TENSOR_NUMELS
    elif arm == "A1":
        expected_numels = {"route_switch_adapter.delta_switch": 896}
    else:
        expected_numels = {}
    actual = {name: named_numels.get(name) for name in expected_names}
    if actual != dict(expected_numels):
        raise ValueError("TRAINABLE_TENSOR_SHAPE_OR_COUNT_MISMATCH")
    total = sum(actual.values())
    if total != ARM_PARAMETER_COUNTS[arm]:
        raise ValueError("ARM_PARAMETER_COUNT_MISMATCH")
    return total


def validate_a2_adapter_composition(
        active_adapter_names, released_adapter_names, trainable_names):
    """Require additive new+released composition and frozen released tensors."""
    active = set(active_adapter_names)
    released = set(released_adapter_names)
    trainable = set(trainable_names)
    if not released:
        raise ValueError("RELEASED_ADAPTER_NAMESPACE_SET_REQUIRED")
    if active != released | {A2_NAMESPACE}:
        raise ValueError("RELEASED_AND_NEW_ADAPTERS_NOT_SIMULTANEOUSLY_ACTIVE")
    if any(name == A2_NAMESPACE for name in released):
        raise ValueError("NEW_NAMESPACE_COLLIDES_WITH_RELEASED_ADAPTER")
    released_tensor_active = any(
        any(
            (".lora_A.{}.weight".format(namespace) in name)
            or (".lora_B.{}.weight".format(namespace) in name)
            for namespace in released
        )
        for name in trainable
    )
    if released_tensor_active:
        raise ValueError("RELEASED_ADAPTER_TENSOR_NOT_FROZEN")
    if trainable != set(A2_TRAINABLE_TENSORS):
        raise ValueError("A2_OPTIMIZER_MEMBERSHIP_MISMATCH")
    return True
