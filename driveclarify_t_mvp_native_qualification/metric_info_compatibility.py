"""Exact, source-bound SimLingo metric-interface compatibility adapter.

The native qualification intentionally retains the previously selected
``leaderboard_autopilot`` evaluator and base class.  SimLingo's closed-loop
agent nevertheless calls two methods supplied by its authoritative
Bench2Drive ``AutonomousAgent``: ``get_hero`` and ``get_metric_info``.  This
module extracts those two functions from the exact hash-pinned upstream file
and binds them only to the qualification's inner ``LingoAgent`` instance.

No fallback values exist here.  Any path, byte, class, signature, or schema
drift fails closed before SimLingo setup/model loading.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path
from types import MethodType
from typing import Any, Callable, Dict, Mapping, Tuple


AUTHORITATIVE_BASE_SOURCE = Path(
    "/home/buaa/wrh/simlingo/Bench2Drive/leaderboard/leaderboard/autoagents/autonomous_agent.py"
)
AUTHORITATIVE_BASE_SHA256 = (
    "b97c9ccc00c1a56a089f3ecfa194be39e2fced445c9a8d21cd7805849661d861"
)
SELECTED_BASE_SOURCE = Path(
    "/home/buaa/wrh/simlingo/leaderboard_autopilot/leaderboard/autoagents/autonomous_agent.py"
)
SELECTED_BASE_SHA256 = (
    "52e938be3f4d37c05a91ddc263f00c7cd230add1b68220ca3c6a80e45546b9b5"
)
LINGO_AGENT_SOURCE = Path("/home/buaa/wrh/simlingo/team_code/agent_simlingo.py")
LINGO_AGENT_SOURCE_SHA256 = (
    "863e60ee19906ed58d3a9afff898b1c65426676cada8b722904ed65f4e2c3db8"
)
EXPECTED_TARGET_IDENTITY = "team_code.agent_simlingo.LingoAgent"
EXPECTED_RETURN_KEYS = (
    "acceleration",
    "angular_velocity",
    "forward_vector",
    "right_vector",
    "location",
    "rotation",
)
GET_HERO_SOURCE_SEGMENT_SHA256 = (
    "3e393c60500234d6eab9dfd27ce5c24819e2e9441cebcd0525aea6c670630610"
)
GET_METRIC_INFO_SOURCE_SEGMENT_SHA256 = (
    "732b7cb0c8dec2babdcf12a83df0d8a3e3687ac1d44931149a8b8b4f9773b791"
)


class MetricInfoCompatibilityError(RuntimeError):
    """Raised when the exact authoritative interface cannot be restored."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _class_node(tree: ast.AST, name: str) -> ast.ClassDef:
    matches = [
        node
        for node in getattr(tree, "body", ())
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    if len(matches) != 1:
        raise MetricInfoCompatibilityError("AUTHORITATIVE_CLASS_NOT_EXACTLY_ONCE")
    return matches[0]


def _method_node(
    owner: ast.ClassDef, name: str, *, self_only_signature: bool = True
) -> ast.FunctionDef:
    matches = [
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    if len(matches) != 1:
        raise MetricInfoCompatibilityError(
            "AUTHORITATIVE_METHOD_NOT_EXACTLY_ONCE:" + name
        )
    method = matches[0]
    if self_only_signature and (
        [argument.arg for argument in method.args.args] != ["self"]
        or method.args.vararg is not None
        or method.args.kwarg is not None
        or method.args.kwonlyargs
        or method.args.defaults
    ):
        raise MetricInfoCompatibilityError("AUTHORITATIVE_SIGNATURE_DRIFT:" + name)
    return method


def _assigned_output_keys(method: ast.FunctionDef) -> Tuple[str, ...]:
    keys = []
    for node in ast.walk(method):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Subscript):
            continue
        if not isinstance(target.value, ast.Name) or target.value.id != "output":
            continue
        value = target.slice
        if hasattr(ast, "Index") and isinstance(value, ast.Index):
            value = value.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            keys.append(value.value)
    return tuple(keys)


def _compile_method(method: ast.FunctionDef, source_path: Path) -> Callable[..., Any]:
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: Dict[str, Any] = {}
    exec(compile(module, str(source_path), "exec"), namespace, namespace)
    function = namespace[method.name]
    if tuple(inspect.signature(function).parameters) != ("self",):
        raise MetricInfoCompatibilityError(
            "COMPILED_AUTHORITATIVE_SIGNATURE_DRIFT:" + method.name
        )
    return function


def _source_segment_sha256(source: str, node: ast.AST) -> str:
    if getattr(node, "end_lineno", None) is None:
        raise MetricInfoCompatibilityError("AUTHORITATIVE_SOURCE_END_LINE_UNAVAILABLE")
    lines = source.splitlines(keepends=True)
    segment = "".join(lines[node.lineno - 1 : node.end_lineno])
    return hashlib.sha256(segment.encode("utf-8")).hexdigest()


def resolve_authoritative_metric_methods() -> Mapping[str, Any]:
    """Resolve exact upstream methods without importing CARLA or Leaderboard."""

    if _sha256(AUTHORITATIVE_BASE_SOURCE) != AUTHORITATIVE_BASE_SHA256:
        raise MetricInfoCompatibilityError("AUTHORITATIVE_BASE_SOURCE_HASH_MISMATCH")
    if _sha256(SELECTED_BASE_SOURCE) != SELECTED_BASE_SHA256:
        raise MetricInfoCompatibilityError("SELECTED_BASE_SOURCE_HASH_MISMATCH")
    if _sha256(LINGO_AGENT_SOURCE) != LINGO_AGENT_SOURCE_SHA256:
        raise MetricInfoCompatibilityError("LINGO_AGENT_SOURCE_HASH_MISMATCH")

    upstream_source = AUTHORITATIVE_BASE_SOURCE.read_text(encoding="utf-8")
    upstream_tree = ast.parse(
        upstream_source,
        filename=str(AUTHORITATIVE_BASE_SOURCE),
    )
    selected_tree = ast.parse(
        SELECTED_BASE_SOURCE.read_text(encoding="utf-8"),
        filename=str(SELECTED_BASE_SOURCE),
    )
    caller_tree = ast.parse(
        LINGO_AGENT_SOURCE.read_text(encoding="utf-8"),
        filename=str(LINGO_AGENT_SOURCE),
    )
    upstream_class = _class_node(upstream_tree, "AutonomousAgent")
    selected_class = _class_node(selected_tree, "AutonomousAgent")
    selected_methods = {
        node.name for node in selected_class.body if isinstance(node, ast.FunctionDef)
    }
    if {"get_hero", "get_metric_info"} & selected_methods:
        raise MetricInfoCompatibilityError("SELECTED_BASE_UNEXPECTEDLY_PROVIDES_INTERFACE")

    caller_class = _class_node(caller_tree, "LingoAgent")
    run_step = _method_node(caller_class, "run_step", self_only_signature=False)
    calls = [
        node
        for node in ast.walk(run_step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and node.func.attr == "get_metric_info"
        and not node.args
        and not node.keywords
    ]
    if len(calls) != 1:
        raise MetricInfoCompatibilityError("LINGO_CALL_SITE_NOT_EXACTLY_ONCE")

    get_hero = _method_node(upstream_class, "get_hero")
    get_metric_info = _method_node(upstream_class, "get_metric_info")
    if _source_segment_sha256(upstream_source, get_hero) != GET_HERO_SOURCE_SEGMENT_SHA256:
        raise MetricInfoCompatibilityError("AUTHORITATIVE_GET_HERO_SOURCE_SEGMENT_DRIFT")
    if (
        _source_segment_sha256(upstream_source, get_metric_info)
        != GET_METRIC_INFO_SOURCE_SEGMENT_SHA256
    ):
        raise MetricInfoCompatibilityError(
            "AUTHORITATIVE_GET_METRIC_INFO_SOURCE_SEGMENT_DRIFT"
        )
    if _assigned_output_keys(get_metric_info) != EXPECTED_RETURN_KEYS:
        raise MetricInfoCompatibilityError("AUTHORITATIVE_RETURN_SCHEMA_DRIFT")

    return {
        "get_hero": _compile_method(get_hero, AUTHORITATIVE_BASE_SOURCE),
        "get_metric_info": _compile_method(get_metric_info, AUTHORITATIVE_BASE_SOURCE),
        "contract": {
            "authoritative_source": str(AUTHORITATIVE_BASE_SOURCE),
            "authoritative_source_sha256": AUTHORITATIVE_BASE_SHA256,
            "selected_base_source": str(SELECTED_BASE_SOURCE),
            "selected_base_source_sha256": SELECTED_BASE_SHA256,
            "caller_source": str(LINGO_AGENT_SOURCE),
            "caller_source_sha256": LINGO_AGENT_SOURCE_SHA256,
            "get_hero_lines": [get_hero.lineno, get_hero.end_lineno],
            "get_metric_info_lines": [
                get_metric_info.lineno,
                get_metric_info.end_lineno,
            ],
            "get_hero_source_segment_sha256": GET_HERO_SOURCE_SEGMENT_SHA256,
            "get_metric_info_source_segment_sha256": (
                GET_METRIC_INFO_SOURCE_SEGMENT_SHA256
            ),
            "call_site_line": calls[0].lineno,
            "signature": "get_metric_info(self)",
            "return_keys": list(EXPECTED_RETURN_KEYS),
            "vector_shape": [3],
        },
    }


def install_exact_metric_info_compatibility(agent: Any) -> Mapping[str, Any]:
    """Bind and initialize the exact upstream interface on one LingoAgent."""

    target_type = type(agent)
    identity = target_type.__module__ + "." + target_type.__qualname__
    if identity != EXPECTED_TARGET_IDENTITY:
        raise MetricInfoCompatibilityError("TARGET_CLASS_IDENTITY_MISMATCH:" + identity)
    target_source_raw = inspect.getsourcefile(target_type)
    if target_source_raw is None:
        raise MetricInfoCompatibilityError("TARGET_CLASS_SOURCE_UNAVAILABLE")
    target_source = Path(target_source_raw).resolve()
    if target_source != LINGO_AGENT_SOURCE.resolve():
        raise MetricInfoCompatibilityError("TARGET_CLASS_SOURCE_PATH_MISMATCH")
    if _sha256(target_source) != LINGO_AGENT_SOURCE_SHA256:
        raise MetricInfoCompatibilityError("TARGET_CLASS_SOURCE_HASH_MISMATCH")

    selected_bases = []
    for base in target_type.__mro__[1:]:
        try:
            source_raw = inspect.getsourcefile(base)
        except TypeError:
            source_raw = None
        if source_raw is not None and Path(source_raw).resolve() == SELECTED_BASE_SOURCE.resolve():
            selected_bases.append(base)
    if len(selected_bases) != 1:
        raise MetricInfoCompatibilityError("SELECTED_BASE_CLASS_IDENTITY_MISMATCH")
    if "get_metric_info" in agent.__dict__ or hasattr(target_type, "get_metric_info"):
        raise MetricInfoCompatibilityError("METRIC_INTERFACE_ALREADY_PRESENT_OR_SHADOWED")

    resolved = resolve_authoritative_metric_methods()
    agent.get_hero = MethodType(resolved["get_hero"], agent)
    agent.get_metric_info = MethodType(resolved["get_metric_info"], agent)
    agent.get_hero()

    receipt = dict(resolved["contract"])
    receipt.update(
        {
            "schema_version": "driveclarify.rq2.metric_info_compatibility_receipt.v1",
            "status": "PASS_EXACT_HASH_BOUND_UPSTREAM_INTERFACE_INSTALLED",
            "repair_type": "COMPATIBILITY_ADAPTER_EQUIVALENT_TO_UPSTREAM",
            "target_class_identity": identity,
            "target_class_source": str(target_source),
            "selected_base_class_identity": (
                selected_bases[0].__module__ + "." + selected_bases[0].__qualname__
            ),
            "binding_scope": "ONE_QUALIFICATION_INNER_AGENT_INSTANCE",
            "fallback_or_stub_values": False,
            "hero_lookup_initialized": True,
        }
    )
    agent.driveclarify_metric_info_compatibility_receipt = receipt
    return receipt
