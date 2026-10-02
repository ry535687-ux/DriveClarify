#!/usr/bin/env python3
"""CPU-only dry run of the production M3E evaluator adapter/loader path."""

from __future__ import annotations

import ast
import functools
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPO = Path("/home/buaa/wrh/DriveClarify")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from driveclarify_static_branch.route_validation import (
    EVALUATOR_SOURCE_PATH,
    EVALUATOR_SOURCE_SHA256,
    PRODUCTION_EVALUATOR_MODULE_NAME,
    count_vulnerable_evaluator_accesses,
    diagnose_legacy_matcher_count,
    load_compatible_evaluator_for_base_adapter,
    prepare_compatible_evaluator_source,
)


PYTHON38 = Path("/home/buaa/anaconda3/envs/simlingo/bin/python3.8")
P2 = (
    REPO
    / "reports/static_maneuver_branch_primary_mvp_v1/M3E_STATIC_BRANCH_PILOT/run_outputs/"
    "DC-M3E-STATIC-P2-20260803T092100Z"
)
P2_ENTRY = P2 / "M3E_EVALUATOR_ENTRY.py"
P2_BINDING = P2 / "EVALUATOR_PROCESS_BINDING.json"
P2_LOG = P2 / "logs/supervisor_execution.log"
BASE_ADAPTER = (
    REPO
    / "reports/driveclarify_manual_phase0a_world_alias_fix/"
    "NO_LAUNCH_EVALUATOR_ADAPTER.py"
)
BASE_ADAPTER_SHA256 = "b59723b06b9aec6178c386987f9b782840979b327fd6a4fd94f0d77ffdaefc4f"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _vulnerable_access_shape(source: bytes) -> Mapping[str, Any]:
    tree = ast.parse(source, filename=str(EVALUATOR_SOURCE_PATH))
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "name"
        and isinstance(node.value, ast.Subscript)
        and isinstance(node.value.value, ast.Attribute)
        and node.value.value.attr == "scenario_configs"
    ]
    if len(matches) != 1:
        raise RuntimeError("DRY_RUN_ACTUAL_ACCESS_SHAPE_NOT_UNIQUE")
    node = matches[0]
    return {
        "line": node.lineno,
        "expression": "config.scenario_configs[0].name",
        "ast_dump": ast.dump(node, annotate_fields=True, include_attributes=False),
        "subscript_slice_type": type(node.value.slice).__name__,
        "subscript_slice_dump": ast.dump(
            node.value.slice, annotate_fields=True, include_attributes=False
        ),
    }


def main() -> int:
    if Path(sys.executable).resolve(strict=True) != PYTHON38.resolve(strict=True):
        raise RuntimeError("DRY_RUN_REQUIRES_EXACT_P2_PYTHON38")
    binding = json.loads(P2_BINDING.read_text(encoding="utf-8"))
    command = binding["binding"]["command_argv"]
    bound_source = Path(command[command.index("--original-evaluator") + 1]).resolve(
        strict=True
    )
    bound_sha = command[command.index("--original-evaluator-sha256") + 1]
    source = EVALUATOR_SOURCE_PATH.resolve(strict=True)
    raw = source.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    if bound_source != source or bound_sha != source_sha or source_sha != EVALUATOR_SOURCE_SHA256:
        raise RuntimeError("DRY_RUN_P2_SOURCE_IDENTITY_MISMATCH")
    traceback_text = P2_LOG.read_text(encoding="utf-8", errors="replace")
    if "EVALUATOR_VULNERABLE_ACCESS_COUNT:0" not in traceback_text:
        raise RuntimeError("DRY_RUN_P2_FAILURE_EVIDENCE_MISSING")

    entry_spec = importlib.util.spec_from_file_location(
        "_driveclarify_p2_historical_entry_identity_only", str(P2_ENTRY)
    )
    if entry_spec is None or entry_spec.loader is None:
        raise RuntimeError("DRY_RUN_P2_ENTRY_SPEC_FAILED")
    before_modules = set(sys.modules)
    base_spec = importlib.util.spec_from_file_location(
        "_driveclarify_evaluator_dry_run_base_adapter", str(BASE_ADAPTER)
    )
    if base_spec is None or base_spec.loader is None or _sha(BASE_ADAPTER) != BASE_ADAPTER_SHA256:
        raise RuntimeError("DRY_RUN_BASE_ADAPTER_IDENTITY")
    base_adapter = importlib.util.module_from_spec(base_spec)
    sys.modules[base_spec.name] = base_adapter
    base_spec.loader.exec_module(base_adapter)
    base_adapter._load_original = functools.partial(
        load_compatible_evaluator_for_base_adapter, cpu_dry_run=True
    )
    prepared = prepare_compatible_evaluator_source(source)
    first_module = base_adapter._load_original(source, source_sha)
    first_evidence = dict(first_module._DRIVECLARIFY_LAST_LOAD_EVIDENCE)
    second_module = base_adapter._load_original(source, source_sha)
    second_evidence = dict(second_module._DRIVECLARIFY_LAST_LOAD_EVIDENCE)
    added_modules = set(sys.modules) - before_modules
    forbidden = sorted(
        name
        for name in added_modules
        if name == "carla" or name == "torch" or name.startswith("torch.")
    )
    if forbidden:
        raise RuntimeError("DRY_RUN_PROHIBITED_RUNTIME_MODULE_LOADED:" + ",".join(forbidden))
    if first_module is not second_module:
        raise RuntimeError("DRY_RUN_DOUBLE_APPLICATION_MODULE_REPLACED")

    evidence = {
        "schema_version": "driveclarify.evaluator_production_path_dry_run.v1",
        "status": "PASS",
        "cpu_only": True,
        "sys_executable": sys.executable,
        "python_version": sys.version,
        "sys_path": list(sys.path),
        "p2_historical_production_entry": {
            "path": str(P2_ENTRY.resolve(strict=True)),
            "sha256": _sha(P2_ENTRY),
            "loader_type": (
                type(entry_spec.loader).__module__
                + "."
                + type(entry_spec.loader).__name__
            ),
            "actual_source_path": str(bound_source),
            "actual_source_sha256": bound_sha,
            "adapter_count_stage": "PRE_TRANSFORM_AST",
            "legacy_python38_matcher_count": diagnose_legacy_matcher_count(raw),
            "recorded_exception": "EVALUATOR_VULNERABLE_ACCESS_COUNT:0",
            "exact_traceback_present": True,
        },
        "closure_source": {
            "path": str(source),
            "sha256": source_sha,
            "same_as_p2": bound_source == source and bound_sha == source_sha,
        },
        "actual_ast_access": _vulnerable_access_shape(raw),
        "production_base_adapter": {
            "path": str(BASE_ADAPTER.resolve(strict=True)),
            "sha256": _sha(BASE_ADAPTER),
            "resolved_module": base_adapter.__name__,
            "loader_type": type(base_spec.loader).__module__ + "." + type(base_spec.loader).__name__,
            "load_hook": "driveclarify_static_branch.route_validation.load_compatible_evaluator_for_base_adapter",
        },
        "source_state_first_application": first_evidence["source_state"],
        "source_state_second_application": second_evidence["source_state"],
        "source_state_reason_first": first_evidence["source_state_reason"],
        "source_state_reason_second": second_evidence["source_state_reason"],
        "pre_transform_ast_count": prepared.pre_transform_vulnerable_access_count,
        "post_transform_ast_count": prepared.post_transform_vulnerable_access_count,
        "post_transform_compatible_call_count": (
            prepared.post_transform_compatible_access_count
        ),
        "transformed_ast_sha256": prepared.transformed_ast_sha256,
        "ast_diff": [dict(item) for item in prepared.ast_diff],
        "resolved_module": first_module.__name__,
        "expected_resolved_module": PRODUCTION_EVALUATOR_MODULE_NAME,
        "imported_module_file": first_module.__file__,
        "loader_type": first_evidence["loader_type"],
        "function_code_identity": first_evidence["function_code_identity"],
        "adapter_invocation_count": second_evidence["adapter_invocation_count"],
        "double_application_detected": second_evidence["double_application_detected"],
        "transform_reapplied_on_second_application": second_evidence[
            "transform_reapplied"
        ],
        "scenario_free": second_evidence["behavior_probes"]["scenario_free"],
        "ordinary_scenario": second_evidence["behavior_probes"][
            "ordinary_scenario"
        ],
        "external_dependency_import_syntax_requests_stubbed": first_evidence[
            "external_dependency_requests_stubbed"
        ],
        "real_prohibited_modules_loaded": forbidden,
        "prohibited_runtime_counts": {
            "carla_launch": 0,
            "real_evaluator_simulation_launch": 0,
            "checkpoint_load": 0,
            "model_load": 0,
            "observation": 0,
            "model_forward": 0,
            "torch_import": 0,
            "cuda_initialization": 0,
            "gpu_use": 0,
            "new_real_run_id": 0,
            "authorization_receipt": 0,
            "new_run_outputs": 0,
        },
        "raw_ast_vulnerable_access_count_after_fixed_matcher": (
            count_vulnerable_evaluator_accesses(
                ast.parse(raw, filename=str(source))
            )
        ),
    }
    if evidence["p2_historical_production_entry"]["legacy_python38_matcher_count"] != 0:
        raise RuntimeError("DRY_RUN_LEGACY_COUNT_NOT_REPRODUCED")
    if evidence["pre_transform_ast_count"] != 1 or evidence["post_transform_ast_count"] != 0:
        raise RuntimeError("DRY_RUN_TRANSFORM_COUNTS_INVALID")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
