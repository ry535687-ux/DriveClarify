"""CPU-only proof of the Leaderboard evaluator's dynamic agent identity.

This qualification helper intentionally never imports the evaluator or agent.
It parses their source, reproduces the evaluator's path-to-module rule, and
uses ``PathFinder`` only to resolve the module file without executing it.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
from importlib.machinery import PathFinder
import json
import os
from pathlib import Path
from typing import Any, Mapping


EXPECTED_EVALUATOR_CLASS = "LeaderboardEvaluator"
EXPECTED_LOADER_FUNCTION = "__init__"
EXPECTED_CLASS_LOADER_FUNCTION = "_load_and_run_scenario"


class EntrypointIdentityError(RuntimeError):
    """Raised when source does not implement the frozen evaluator contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _function(tree: ast.Module, class_name: str, function_name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == function_name:
                    return child
    raise EntrypointIdentityError(
        f"EVALUATOR_FUNCTION_MISSING:{class_name}.{function_name}"
    )


def _source_contract(evaluator_path: Path) -> dict[str, Any]:
    source = evaluator_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(evaluator_path))
    init = _function(tree, EXPECTED_EVALUATOR_CLASS, EXPECTED_LOADER_FUNCTION)
    scenario = _function(
        tree, EXPECTED_EVALUATOR_CLASS, EXPECTED_CLASS_LOADER_FUNCTION
    )

    module_assignment: ast.Assign | None = None
    sys_path_insert: ast.Expr | None = None
    import_assignment: ast.Assign | None = None
    entrypoint_assignment: ast.Assign | None = None
    class_assignment: ast.Assign | None = None

    for node in ast.walk(init):
        segment = ast.get_source_segment(source, node) or ""
        if isinstance(node, ast.Assign) and segment.startswith("module_name = "):
            module_assignment = node
        elif isinstance(node, ast.Expr) and segment.startswith("sys.path.insert("):
            sys_path_insert = node
        elif isinstance(node, ast.Assign) and "importlib.import_module(module_name)" in segment:
            import_assignment = node

    for node in ast.walk(scenario):
        segment = ast.get_source_segment(source, node) or ""
        if isinstance(node, ast.Assign) and segment.startswith("agent_class_name = "):
            entrypoint_assignment = node
        elif isinstance(node, ast.Assign) and segment.startswith("agent_class_obj = "):
            class_assignment = node

    required = {
        "module_assignment": module_assignment,
        "sys_path_insert": sys_path_insert,
        "import_assignment": import_assignment,
        "entrypoint_assignment": entrypoint_assignment,
        "class_assignment": class_assignment,
    }
    missing = [name for name, node in required.items() if node is None]
    if missing:
        raise EntrypointIdentityError(
            "EVALUATOR_DYNAMIC_IMPORT_PATH_INCOMPLETE:" + ",".join(missing)
        )

    assert module_assignment is not None
    assert sys_path_insert is not None
    assert import_assignment is not None
    assert entrypoint_assignment is not None
    assert class_assignment is not None
    expected_segments = {
        "module_name_derivation": "module_name = os.path.basename(args.agent).split('.')[0]",
        "sys_path_mutation": "sys.path.insert(0, os.path.dirname(args.agent))",
        "module_import": "self.module_agent = importlib.import_module(module_name)",
        "class_name_derivation": "agent_class_name = getattr(self.module_agent, 'get_entry_point')()",
        "class_resolution": "agent_class_obj = getattr(self.module_agent, agent_class_name)",
    }
    actual_segments = {
        "module_name_derivation": ast.get_source_segment(source, module_assignment),
        "sys_path_mutation": ast.get_source_segment(source, sys_path_insert),
        "module_import": ast.get_source_segment(source, import_assignment),
        "class_name_derivation": ast.get_source_segment(source, entrypoint_assignment),
        "class_resolution": ast.get_source_segment(source, class_assignment),
    }
    if actual_segments != expected_segments:
        raise EntrypointIdentityError("EVALUATOR_DYNAMIC_IMPORT_SEMANTICS_DRIFT")

    return {
        "evaluator_source_file": str(evaluator_path),
        "evaluator_source_sha256": _sha256(evaluator_path),
        "evaluator_loader_class": EXPECTED_EVALUATOR_CLASS,
        "evaluator_loader_function": EXPECTED_LOADER_FUNCTION,
        "class_loader_function": EXPECTED_CLASS_LOADER_FUNCTION,
        "source_locations": {
            "module_name_derivation": module_assignment.lineno,
            "sys_path_mutation": sys_path_insert.lineno,
            "module_import": import_assignment.lineno,
            "class_name_derivation": entrypoint_assignment.lineno,
            "class_resolution": class_assignment.lineno,
        },
        "source_statements": actual_segments,
    }


def _static_entrypoint(agent_path: Path) -> tuple[str, int, int]:
    source = agent_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(agent_path))
    entrypoint: str | None = None
    entrypoint_line = -1
    class_line = -1
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "get_entry_point":
            if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
                raise EntrypointIdentityError("AGENT_ENTRYPOINT_NOT_STATIC_RETURN")
            value = ast.literal_eval(node.body[0].value)
            if not isinstance(value, str) or not value:
                raise EntrypointIdentityError("AGENT_ENTRYPOINT_CLASS_INVALID")
            entrypoint = value
            entrypoint_line = node.lineno
    if entrypoint is None:
        raise EntrypointIdentityError("AGENT_GET_ENTRY_POINT_MISSING")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == entrypoint:
            class_line = node.lineno
            break
    if class_line < 0:
        raise EntrypointIdentityError("AGENT_ENTRYPOINT_CLASS_MISSING:" + entrypoint)
    return entrypoint, entrypoint_line, class_line


def derive_entrypoint_contract(
    *, evaluator_path: Path, agent_argument: str, launcher_path: Path
) -> dict[str, Any]:
    """Derive the exact identity without importing executable runtime modules."""

    evaluator_path = evaluator_path.resolve()
    launcher_path = launcher_path.resolve()
    requested = str(Path(agent_argument).resolve())
    agent_path = Path(requested)
    if not evaluator_path.is_file() or not agent_path.is_file() or not launcher_path.is_file():
        raise EntrypointIdentityError("ENTRYPOINT_SOURCE_MISSING")

    loader = _source_contract(evaluator_path)
    module_name = os.path.basename(requested).split(".")[0]
    inserted_path = os.path.dirname(requested)
    spec = PathFinder.find_spec(module_name, [inserted_path])
    if spec is None or spec.origin is None:
        raise EntrypointIdentityError("EVALUATOR_MODULE_SPEC_UNRESOLVED")
    resolved_source = Path(spec.origin).resolve()
    if resolved_source != agent_path:
        raise EntrypointIdentityError(
            f"EVALUATOR_MODULE_RESOLVED_WRONG_FILE:{resolved_source}"
        )
    class_name, get_entry_line, class_line = _static_entrypoint(resolved_source)
    launcher_source = launcher_path.read_text(encoding="utf-8")
    expected_agent_token = '--agent="$qualification/agent.py"'
    if expected_agent_token not in launcher_source:
        raise EntrypointIdentityError("LAUNCHER_AGENT_ARGUMENT_DRIFT")

    runtime_identity = module_name + "." + class_name
    filesystem_identity = (
        "driveclarify_t_mvp_native_qualification.agent." + class_name
    )
    return {
        "schema_version": "driveclarify.rq2.evaluator_dynamic_import_contract.v2",
        "status": "PASS_EVALUATOR_DYNAMIC_IMPORT_CONTRACT",
        **loader,
        "agent_argument_name": "args.agent",
        "launcher_source_file": str(launcher_path),
        "launcher_source_sha256": _sha256(launcher_path),
        "launcher_agent_argument_expression": expected_agent_token,
        "supplied_agent_path": requested,
        "supplied_path_form": "ABSOLUTE",
        "basename_stem_derivation": "os.path.basename(args.agent).split('.')[0]",
        "sys_path_mutation": {
            "operation": "sys.path.insert",
            "index": 0,
            "value_rule": "os.path.dirname(args.agent)",
            "derived_value": inserted_path,
        },
        "import_operation": "importlib.import_module(module_name)",
        "derived_module_name": module_name,
        "derived_class_name": class_name,
        "resolved_source_file": str(resolved_source),
        "resolved_source_sha256": _sha256(resolved_source),
        "agent_get_entry_point_source_line": get_entry_line,
        "agent_class_source_line": class_line,
        "filesystem_package_identity": filesystem_identity,
        "evaluator_runtime_module_identity": module_name,
        "expected___module__": module_name,
        "expected___qualname__": class_name,
        "runtime_identity": runtime_identity,
        "identity_binding_rule": "different identity namespaces bound by one resolved absolute source path and SHA-256",
        "agent_module_executed": False,
        "evaluator_module_executed": False,
        "real_model_loaded": False,
        "gpu_context_created": False,
    }


def validate_runtime_registry_case(
    case: Mapping[str, Any], contract: Mapping[str, Any]
) -> None:
    exact = {
        "wrapper_source_path": contract["resolved_source_file"],
        "wrapper_source_sha256": contract["resolved_source_sha256"],
        "evaluator_module_name": contract["derived_module_name"],
        "class_name": contract["derived_class_name"],
        "runtime_identity": contract["runtime_identity"],
        "agent_argument_passed_to_evaluator": contract["supplied_agent_path"],
    }
    for key, expected in exact.items():
        if case.get(key) != expected:
            raise EntrypointIdentityError("REGISTRY_RUNTIME_IDENTITY_MISMATCH:" + key)
    if case.get("filesystem_package_identity") != contract["filesystem_package_identity"]:
        raise EntrypointIdentityError("REGISTRY_FILESYSTEM_IDENTITY_MISMATCH")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluator", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--launcher", required=True)
    args = parser.parse_args()
    try:
        value = derive_entrypoint_contract(
            evaluator_path=Path(args.evaluator),
            agent_argument=args.agent,
            launcher_path=Path(args.launcher),
        )
    except (EntrypointIdentityError, OSError, SyntaxError, ValueError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
