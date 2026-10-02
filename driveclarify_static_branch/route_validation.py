"""M3D scenario-free route/evaluator and first-observation contracts.

This module is deliberately Python-standard-library only.  It never imports
CARLA, SimLingo, torch, or an evaluator module.  Runtime users may explicitly
call :func:`execute_compatible_evaluator` after all external authorization gates
have passed; CPU/static validation only parses and compiles the evaluator AST.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, ModuleType, SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree as ET


SOURCE_ROUTE_ID = "27515"
SOURCE_TOWN = "Town03"
SOURCE_JUNCTION_ID = "238"
SOURCE_ROUTE_SHA256 = "db7c61ad823130fb40aa2ce2541ebd780a7588f7f4a4d2e3524d5df612cb6a69"
EVALUATOR_SOURCE_SHA256 = "745b1635a820276f665b95d63c783cf2e8eec62363784a731877bd3062511964"
EVALUATOR_SOURCE_PATH = Path(
    "/home/buaa/wrh/simlingo/Bench2Drive/leaderboard/leaderboard/leaderboard_evaluator.py"
)
SCENARIO_FREE_FIXTURE_PATH = Path(
    "/home/buaa/wrh/DriveClarify/reports/static_maneuver_branch_primary_mvp_v1/fixtures/"
    "town03_route_27515_junction_238_scenario_free_v1.xml"
)
ORDINARY_SCENARIO_FIXTURE_PATH = Path(
    "/home/buaa/wrh/simlingo/leaderboard/data/bench2drive_split/bench2drive_202.xml"
)
PRODUCTION_EVALUATOR_MODULE_NAME = (
    "driveclarify_pinned_scenario_free_bench2drive_evaluator_m3e"
)
SOURCE_VULNERABLE = "SOURCE_VULNERABLE"
SOURCE_ALREADY_COMPATIBLE = "SOURCE_ALREADY_COMPATIBLE"
SOURCE_UNEXPECTED = "SOURCE_UNEXPECTED"
TOPOLOGY_EMBEDDED_SHA256 = "cfd5bb11099680c1a887e847adbb1cc11bf62ec13ba490dfd72075929e03c7db"
THRESHOLD_EMBEDDED_SHA256 = "6dfeea8907eb987c512c2b180a824d78c38d7c57662385c24d0d884bf3a37553"

ROUTE_ONLY_STATISTICS_NAME = "ROUTE_ONLY"

DECISION_POINT_XYZ = (-74.63550219738866, 146.84996536542312, 0.0)
ROUTE_START_LEAD_IN_M = 31.11000000000005
BRANCH_DIVERGENCE_M = 8.700000000000001
EVALUATION_INTERVAL_M = (8.700000000000001, 20.700000000000003)
SIMLINGO_ROUTE_POINT_COUNT = 20
SIMLINGO_NOMINAL_ROUTE_STATIONS_M = tuple(float(value) for value in range(20))
MAPPER_TAIL_POINT_COUNT = 3
SIMLINGO_NOMINAL_TAIL_OFFSETS_M = SIMLINGO_NOMINAL_ROUTE_STATIONS_M[-MAPPER_TAIL_POINT_COUNT:]
NOMINAL_FIRST_OBSERVATION_SIGNED_INTERVAL_M = (
    EVALUATION_INTERVAL_M[0] - SIMLINGO_NOMINAL_TAIL_OFFSETS_M[0],
    0.0,
)


class RouteValidationContractError(ValueError):
    """Fail-closed contract violation with a stable reason string."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _indent_xml_for_python38(tree: Any, space: str = "  ", level: int = 0) -> None:
    """Equivalent of ``ElementTree.indent`` for the production Python 3.8."""

    if level < 0:
        raise ValueError("Initial indentation level must be >= 0, got %s" % level)
    if isinstance(tree, ET.ElementTree):
        tree = tree.getroot()
    if not len(tree):
        return
    indentations = ["\n" + level * space]

    def indent_children(element: Any, child_level: int) -> None:
        child_indentation = "\n" + child_level * space
        if not element.text or not element.text.strip():
            element.text = child_indentation
        for child in element:
            if len(child):
                indent_children(child, child_level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = child_indentation
        if not child.tail.strip():
            child.tail = indentations[0] if child_level == 1 else "\n" + (child_level - 1) * space

    indent_children(tree, level + 1)


def derive_scenario_free_fixture_bytes(source_path: str | Path) -> bytes:
    """Copy route 27515 and remove scenario children, preserving route semantics.

    The only semantic XML edit is replacing the source ``<scenarios>`` contents
    with an empty ``<scenarios />`` container required by the baseline parser.
    ElementTree indentation/empty-element serialization is then normalized.
    """

    source = Path(source_path)
    if sha256_file(source) != SOURCE_ROUTE_SHA256:
        raise RouteValidationContractError("SOURCE_ROUTE_SHA256_MISMATCH")

    try:
        tree = ET.parse(source)
    except (OSError, ET.ParseError) as exc:
        raise RouteValidationContractError("SOURCE_ROUTE_XML_INVALID") from exc
    root = tree.getroot()
    routes = [route for route in root.iter("route") if route.get("id") == SOURCE_ROUTE_ID]
    if len(routes) != 1:
        raise RouteValidationContractError("SOURCE_ROUTE_ID_NOT_UNIQUE")
    route = routes[0]
    if route.get("town") != SOURCE_TOWN:
        raise RouteValidationContractError("SOURCE_ROUTE_TOWN_MISMATCH")
    scenarios = route.find("scenarios")
    if scenarios is None:
        raise RouteValidationContractError("SOURCE_SCENARIOS_CONTAINER_MISSING")
    for child in list(scenarios):
        scenarios.remove(child)

    if hasattr(ET, "indent"):
        ET.indent(tree, space="   ")
    else:
        _indent_xml_for_python38(tree, space="   ")
    scenarios.text = None
    return ET.tostring(root, encoding="utf-8", short_empty_elements=True) + b"\n"


def inspect_scenario_free_fixture(path: str | Path) -> dict[str, Any]:
    """Parse the standalone fixture without importing the baseline parser."""

    fixture = Path(path)
    try:
        root = ET.parse(fixture).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RouteValidationContractError("SCENARIO_FREE_FIXTURE_XML_INVALID") from exc

    routes = list(root.iter("route"))
    if len(routes) != 1:
        raise RouteValidationContractError("SCENARIO_FREE_FIXTURE_ROUTE_COUNT_INVALID")
    route = routes[0]
    scenarios = route.find("scenarios")
    if scenarios is None:
        raise RouteValidationContractError("SCENARIO_FREE_FIXTURE_SCENARIOS_CONTAINER_MISSING")

    prohibited_tags = {
        "scenario",
        "trigger_point",
        "other_actor",
        "actor",
        "pedestrian",
        "walker",
        "vehicle",
        "event",
        "behavior",
        "behaviour",
    }
    present_prohibited_tags = sorted({element.tag.lower() for element in route.iter()} & prohibited_tags)
    scenario_names = [element.get("name") for element in route.findall("./scenarios/scenario")]
    return {
        "route_id": route.get("id"),
        "town": route.get("town"),
        "road_id": route.get("road_id"),
        "waypoint_count": len(route.findall("./waypoints/position")),
        "weather_count": len(route.findall("./weathers/weather")),
        "scenario_count": len(scenario_names),
        "scenario_names": scenario_names,
        "scenarios_container_present": True,
        "scenarios_container_empty": len(list(scenarios)) == 0,
        "present_prohibited_tags": present_prohibited_tags,
        "fixture_sha256": sha256_file(fixture),
    }


@dataclass(frozen=True)
class EvaluatorReportingContext:
    """Metadata only; this is never inserted into ``scenario_configs``."""

    statistics_name: str
    scenario_config_present: bool
    scenario_timing_classification: str
    scenario_timing_reason: str


def resolve_evaluator_reporting_context(config: Any) -> EvaluatorReportingContext:
    """Resolve evaluator statistics metadata without indexing scenario config 0."""

    scenario_configs = getattr(config, "scenario_configs", None)
    if scenario_configs is None:
        raise RouteValidationContractError("SCENARIO_CONFIGS_ATTRIBUTE_MISSING")
    try:
        first = next(iter(scenario_configs), None)
    except TypeError as exc:
        raise RouteValidationContractError("SCENARIO_CONFIGS_NOT_ITERABLE") from exc

    if first is None:
        return EvaluatorReportingContext(
            statistics_name=ROUTE_ONLY_STATISTICS_NAME,
            scenario_config_present=False,
            scenario_timing_classification="UNKNOWN",
            scenario_timing_reason="NO_SCENARIO_CONFIG; PRETRIGGER_NOT_APPLICABLE",
        )
    name = getattr(first, "name", None)
    if not isinstance(name, str) or not name:
        raise RouteValidationContractError("FIRST_SCENARIO_NAME_MISSING")
    return EvaluatorReportingContext(
        statistics_name=name,
        scenario_config_present=True,
        scenario_timing_classification="NOT_EVALUATED",
        scenario_timing_reason="NORMAL_SCENARIO_PATH_UNCHANGED",
    )


def resolve_evaluator_statistics_name(config: Any) -> str:
    return resolve_evaluator_reporting_context(config).statistics_name


def _is_vulnerable_scenario_index(node: ast.AST) -> bool:
    """Return true only for ``config.scenario_configs[0].name``."""

    if not isinstance(node, ast.Attribute) or node.attr != "name":
        return False
    subscript = node.value
    if not isinstance(subscript, ast.Subscript):
        return False
    owner = subscript.value
    if not (
        isinstance(owner, ast.Attribute)
        and owner.attr == "scenario_configs"
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "config"
    ):
        return False
    index = subscript.slice
    # Python 3.8 preserves the legacy ``ast.Index`` wrapper.  Python 3.9+
    # exposes the contained Constant directly.  P2 ran on Python 3.8 while
    # the original closure tests ran on Python 3.13, so this normalization is
    # part of the production contract rather than a compatibility nicety.
    if type(index).__name__ == "Index" and hasattr(index, "value"):
        index = index.value
    return isinstance(index, ast.Constant) and index.value == 0


def _legacy_python38_vulnerable_match(node: ast.AST) -> bool:
    """The exact pre-fix predicate, retained only for root-cause regression."""

    if not isinstance(node, ast.Attribute) or node.attr != "name":
        return False
    subscript = node.value
    if not isinstance(subscript, ast.Subscript):
        return False
    owner = subscript.value
    if not (
        isinstance(owner, ast.Attribute)
        and owner.attr == "scenario_configs"
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "config"
    ):
        return False
    return isinstance(subscript.slice, ast.Constant) and subscript.slice.value == 0


def diagnose_legacy_matcher_count(source: str | bytes) -> int:
    """Count with the exact P2 matcher for a stable Python-version regression."""

    tree = ast.parse(source, filename=str(EVALUATOR_SOURCE_PATH))
    return sum(1 for node in ast.walk(tree) if _legacy_python38_vulnerable_match(node))


def count_vulnerable_evaluator_accesses(tree: ast.AST) -> int:
    return sum(1 for node in ast.walk(tree) if _is_vulnerable_scenario_index(node))


def _is_compatible_statistics_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_evaluator_statistics_name"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "config"
        and not node.keywords
    )


def _ast_sha256(tree: ast.AST) -> str:
    payload = ast.dump(tree, annotate_fields=True, include_attributes=False).encode("utf-8")
    return sha256_bytes(payload)


@dataclass(frozen=True)
class PreparedEvaluatorSource:
    source_path: str
    source_sha256: str
    source_state: str
    tree: ast.Module
    pre_transform_vulnerable_access_count: int
    pre_transform_compatible_access_count: int
    post_transform_vulnerable_access_count: int
    post_transform_compatible_access_count: int
    transformed_ast_sha256: str
    ast_diff: tuple[Mapping[str, Any], ...]

    def provenance(self) -> Mapping[str, Any]:
        return {
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "source_state": self.source_state,
            "transformed_ast_sha256": self.transformed_ast_sha256,
            "pre_transform_vulnerable_access_count": (
                self.pre_transform_vulnerable_access_count
            ),
            "post_transform_vulnerable_access_count": (
                self.post_transform_vulnerable_access_count
            ),
            "ast_diff": [dict(item) for item in self.ast_diff],
        }


class _EvaluatorCompatibilityTransformer(ast.NodeTransformer):
    def __init__(self) -> None:
        self.class_name: str | None = None
        self.function_name: str | None = None
        self.replacement_count = 0
        self.diffs: list[Mapping[str, Any]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        previous = self.class_name
        self.class_name = node.name
        result = self.generic_visit(node)
        self.class_name = previous
        return result

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        previous = self.function_name
        self.function_name = node.name
        result = self.generic_visit(node)
        self.function_name = previous
        return result

    def visit_Assign(self, node: ast.Assign) -> ast.AST:
        self.generic_visit(node)
        correct_scope = (
            self.class_name == "LeaderboardEvaluator"
            and self.function_name == "_load_and_run_scenario"
        )
        correct_target = (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "scenario_name"
        )
        if correct_scope and correct_target and _is_vulnerable_scenario_index(node.value):
            before = ast.dump(node.value, annotate_fields=True, include_attributes=False)
            node.value = ast.copy_location(
                ast.Call(
                    func=ast.Name(id="resolve_evaluator_statistics_name", ctx=ast.Load()),
                    args=[ast.Name(id="config", ctx=ast.Load())],
                    keywords=[],
                ),
                node.value,
            )
            self.replacement_count += 1
            self.diffs.append(
                {
                    "scope": "LeaderboardEvaluator._load_and_run_scenario",
                    "target": "scenario_name",
                    "line": int(getattr(node, "lineno", -1)),
                    "before": before,
                    "after": ast.dump(
                        node.value, annotate_fields=True, include_attributes=False
                    ),
                }
            )
        return node


def _compatible_assignment_count(tree: ast.AST) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "scenario_name"
        and _is_compatible_statistics_call(node.value)
    )


def _transform_verified_evaluator_tree(
    tree: ast.Module,
    *,
    source_path: str,
    source_sha256: str,
) -> PreparedEvaluatorSource:
    pre_vulnerable = count_vulnerable_evaluator_accesses(tree)
    pre_compatible = _compatible_assignment_count(tree)
    if pre_vulnerable != 1 or pre_compatible != 0:
        raise RouteValidationContractError(
            "EVALUATOR_SOURCE_STATE_UNEXPECTED:"
            f"VULNERABLE={pre_vulnerable}:COMPATIBLE={pre_compatible}"
        )

    transformer = _EvaluatorCompatibilityTransformer()
    transformed = transformer.visit(tree)
    ast.fix_missing_locations(transformed)
    if transformer.replacement_count != 1 or len(transformer.diffs) != 1:
        raise RouteValidationContractError(
            f"EVALUATOR_VULNERABLE_ACCESS_COUNT:{transformer.replacement_count}"
        )
    post_vulnerable = count_vulnerable_evaluator_accesses(transformed)
    post_compatible = _compatible_assignment_count(transformed)
    if post_vulnerable != 0 or post_compatible != 1:
        raise RouteValidationContractError(
            "EVALUATOR_TRANSFORM_POSTCONDITION:"
            f"VULNERABLE={post_vulnerable}:COMPATIBLE={post_compatible}"
        )
    compile(transformed, source_path, "exec")
    return PreparedEvaluatorSource(
        source_path=source_path,
        source_sha256=source_sha256,
        source_state=SOURCE_VULNERABLE,
        tree=transformed,
        pre_transform_vulnerable_access_count=pre_vulnerable,
        pre_transform_compatible_access_count=pre_compatible,
        post_transform_vulnerable_access_count=post_vulnerable,
        post_transform_compatible_access_count=post_compatible,
        transformed_ast_sha256=_ast_sha256(transformed),
        ast_diff=tuple(transformer.diffs),
    )


def _read_authoritative_evaluator_source(
    evaluator_source_path: str | Path,
    *,
    expected_source_sha256: str = EVALUATOR_SOURCE_SHA256,
) -> tuple[Path, bytes, str]:
    """Read only the exact path/hash authorized by the production contract."""

    if expected_source_sha256 != EVALUATOR_SOURCE_SHA256:
        raise RouteValidationContractError("EVALUATOR_EXPECTED_SOURCE_AUTHORITY_MISMATCH")
    try:
        path = Path(evaluator_source_path).resolve(strict=True)
        authority_path = EVALUATOR_SOURCE_PATH.resolve(strict=True)
    except OSError as exc:
        raise RouteValidationContractError("EVALUATOR_SOURCE_PATH_UNAVAILABLE") from exc
    if path != authority_path:
        raise RouteValidationContractError("EVALUATOR_SOURCE_PATH_MISMATCH")
    payload = path.read_bytes()
    digest = sha256_bytes(payload)
    if digest != EVALUATOR_SOURCE_SHA256:
        raise RouteValidationContractError("EVALUATOR_SOURCE_SHA256_MISMATCH")
    return path, payload, digest


def prepare_compatible_evaluator_source(
    evaluator_source_path: str | Path,
    *,
    expected_source_sha256: str = EVALUATOR_SOURCE_SHA256,
) -> PreparedEvaluatorSource:
    """Verify the one production source path/hash, classify it, and transform it."""

    path, payload, digest = _read_authoritative_evaluator_source(
        evaluator_source_path, expected_source_sha256=expected_source_sha256
    )
    try:
        tree = ast.parse(payload, filename=str(path))
    except SyntaxError as exc:
        raise RouteValidationContractError("EVALUATOR_SOURCE_SYNTAX_INVALID") from exc
    return _transform_verified_evaluator_tree(
        tree, source_path=str(path), source_sha256=digest
    )


def build_compatible_evaluator_ast(
    source: str | bytes,
    *,
    expected_source_sha256: str = EVALUATOR_SOURCE_SHA256,
) -> ast.Module:
    """Build a fail-closed in-memory one-expression evaluator adaptation."""

    if expected_source_sha256 != EVALUATOR_SOURCE_SHA256:
        raise RouteValidationContractError("EVALUATOR_EXPECTED_SOURCE_AUTHORITY_MISMATCH")
    payload = source.encode("utf-8") if isinstance(source, str) else bytes(source)
    if sha256_bytes(payload) != expected_source_sha256:
        raise RouteValidationContractError("EVALUATOR_SOURCE_SHA256_MISMATCH")
    try:
        tree = ast.parse(payload, filename="leaderboard_evaluator.py")
    except SyntaxError as exc:
        raise RouteValidationContractError("EVALUATOR_SOURCE_SYNTAX_INVALID") from exc
    prepared = _transform_verified_evaluator_tree(
        tree,
        source_path=str(EVALUATOR_SOURCE_PATH),
        source_sha256=expected_source_sha256,
    )
    return prepared.tree


class _DriveClarifyAstExecLoader:
    """Identity marker for the source-pinned in-memory AST execution loader."""

    def create_module(self, _spec: Any) -> None:
        return None

    def exec_module(self, _module: Any) -> None:
        raise RuntimeError("DRIVECLARIFY_AST_LOADER_DIRECT_EXEC_REQUIRED")


class _DryRunDependencyImporter:
    """Supply inert evaluator dependencies without importing CARLA/ScenarioRunner."""

    EXTERNAL_PREFIXES = ("carla", "srunner", "leaderboard")

    def __init__(self) -> None:
        self.requests: list[str] = []
        self._real_import = builtins.__import__
        self._modules = self._build_modules()

    @staticmethod
    def _module(name: str, **attributes: Any) -> ModuleType:
        module = ModuleType(name)
        module.__dict__.update(attributes)
        module.__all__ = tuple(attributes)
        return module

    @classmethod
    def _build_modules(cls) -> Mapping[str, ModuleType]:
        class CarlaDataProvider:
            @staticmethod
            def cleanup() -> None:
                return None

        class GameTime:
            @staticmethod
            def get_time() -> float:
                return 0.0

        class Watchdog:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

        class ScenarioManager:
            pass

        class RouteScenario:
            pass

        class SensorConfigurationInvalid(Exception):
            pass

        class AgentError(Exception):
            pass

        class TickRuntimeError(Exception):
            pass

        class StatisticsManager:
            pass

        class RouteIndexer:
            pass

        return {
            "carla": cls._module("carla"),
            "srunner.scenariomanager.carla_data_provider": cls._module(
                "srunner.scenariomanager.carla_data_provider",
                CarlaDataProvider=CarlaDataProvider,
            ),
            "srunner.scenariomanager.timer": cls._module(
                "srunner.scenariomanager.timer", GameTime=GameTime
            ),
            "srunner.scenariomanager.watchdog": cls._module(
                "srunner.scenariomanager.watchdog", Watchdog=Watchdog
            ),
            "leaderboard.scenarios.scenario_manager": cls._module(
                "leaderboard.scenarios.scenario_manager", ScenarioManager=ScenarioManager
            ),
            "leaderboard.scenarios.route_scenario": cls._module(
                "leaderboard.scenarios.route_scenario", RouteScenario=RouteScenario
            ),
            "leaderboard.envs.sensor_interface": cls._module(
                "leaderboard.envs.sensor_interface",
                SensorConfigurationInvalid=SensorConfigurationInvalid,
            ),
            "leaderboard.autoagents.agent_wrapper": cls._module(
                "leaderboard.autoagents.agent_wrapper",
                AgentError=AgentError,
                validate_sensor_configuration=lambda *_args, **_kwargs: None,
                TickRuntimeError=TickRuntimeError,
            ),
            "leaderboard.utils.statistics_manager": cls._module(
                "leaderboard.utils.statistics_manager",
                StatisticsManager=StatisticsManager,
                FAILURE_MESSAGES={"Simulation": ("Crashed", "Simulation")},
            ),
            "leaderboard.utils.route_indexer": cls._module(
                "leaderboard.utils.route_indexer", RouteIndexer=RouteIndexer
            ),
        }

    def __call__(
        self,
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if level == 0 and name in self._modules:
            self.requests.append(name)
            return self._modules[name]
        if level == 0 and name.startswith(self.EXTERNAL_PREFIXES):
            raise RouteValidationContractError(
                "CPU_DRY_RUN_UNEXPECTED_EXTERNAL_IMPORT:" + name
            )
        return self._real_import(name, globals, locals, fromlist, level)


class _TrackingScenarioConfigs(list):
    def __init__(self, values: Iterable[Any]) -> None:
        super().__init__(values)
        self.getitem_zero_count = 0

    def __getitem__(self, index: Any) -> Any:
        if index == 0:
            self.getitem_zero_count += 1
            if not self:
                raise AssertionError("scenario_configs[0] was accessed")
        return super().__getitem__(index)


def _probe_config_from_fixture(path: Path) -> tuple[Any, Mapping[str, Any]]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RouteValidationContractError("EVALUATOR_BEHAVIOR_FIXTURE_INVALID") from exc
    routes = list(root.iter("route"))
    if len(routes) != 1:
        raise RouteValidationContractError("EVALUATOR_BEHAVIOR_ROUTE_COUNT")
    route = routes[0]
    scenarios = [
        SimpleNamespace(name=item.get("name"), type=item.get("type"))
        for item in route.findall("./scenarios/scenario")
    ]
    configs = _TrackingScenarioConfigs(scenarios)
    config = SimpleNamespace(
        name="RouteScenario_" + str(route.get("id")),
        repetition_index=0,
        town=route.get("town"),
        weather=[[0.0, SimpleNamespace()]],
        index=0,
        scenario_configs=configs,
    )
    return config, {
        "fixture_path": str(path.resolve(strict=True)),
        "fixture_sha256": sha256_file(path),
        "route_id": route.get("id"),
        "route_name": config.name,
        "town": route.get("town"),
        "scenario_count": len(scenarios),
        "scenario_names": [item.name for item in scenarios],
        "scenario_types": [item.type for item in scenarios],
    }


def _function_code_identity(function: Any) -> Mapping[str, Any]:
    code = function.__code__

    def normalize(value: Any) -> Any:
        if isinstance(value, CodeType):
            return {
                "co_argcount": value.co_argcount,
                "co_kwonlyargcount": value.co_kwonlyargcount,
                "co_nlocals": value.co_nlocals,
                "co_stacksize": value.co_stacksize,
                "co_flags": value.co_flags,
                "co_code": value.co_code.hex(),
                "co_consts": [normalize(item) for item in value.co_consts],
                "co_names": list(value.co_names),
                "co_varnames": list(value.co_varnames),
                "co_freevars": list(value.co_freevars),
                "co_cellvars": list(value.co_cellvars),
            }
        if isinstance(value, tuple):
            return [normalize(item) for item in value]
        if isinstance(value, frozenset):
            return sorted((normalize(item) for item in value), key=repr)
        if isinstance(value, bytes):
            return {"bytes_hex": value.hex()}
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        raise RouteValidationContractError("EVALUATOR_CODE_CONSTANT_UNCLASSIFIABLE")

    normalized = normalize(code)
    payload = json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return {
        "module": function.__module__,
        "qualname": function.__qualname__,
        "co_filename": code.co_filename,
        "co_firstlineno": code.co_firstlineno,
        "code_sha256": sha256_bytes(payload),
    }


def run_loaded_evaluator_behavior_probe(module: ModuleType, fixture: str | Path) -> Mapping[str, Any]:
    """Exercise the loaded production method only through its pre-world metadata path."""

    path = Path(fixture).resolve(strict=True)
    config, fixture_identity = _probe_config_from_fixture(path)
    before_configs = config.scenario_configs
    before_scenarios = tuple(config.scenario_configs)
    calls: list[tuple[Any, ...]] = []

    class StatisticsRecorder:
        def create_route_data(self, *args: Any) -> None:
            calls.append(tuple(args))

    class StopBeforeWorld(RuntimeError):
        pass

    evaluator = object.__new__(module.LeaderboardEvaluator)
    evaluator.statistics_manager = StatisticsRecorder()

    def stop_before_world(*_args: Any, **_kwargs: Any) -> None:
        raise StopBeforeWorld("CPU_DRY_RUN_STOP_BEFORE_WORLD")

    evaluator._load_and_wait_for_world = stop_before_world
    evaluator._register_statistics = lambda *_args, **_kwargs: None
    evaluator._cleanup = lambda *_args, **_kwargs: None
    original_weather = module.get_weather_id
    original_datetime = module.datetime

    class FixedDateTime:
        @staticmethod
        def now() -> Any:
            return SimpleNamespace(strftime=lambda _format: "DRY_RUN_TIME")

    module.get_weather_id = lambda _weather: "DRY_RUN_WEATHER"
    module.datetime = FixedDateTime
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            returned = module.LeaderboardEvaluator._load_and_run_scenario(
                evaluator, SimpleNamespace(debug=0), config
            )
    finally:
        module.get_weather_id = original_weather
        module.datetime = original_datetime
    if len(calls) != 1:
        raise RouteValidationContractError("EVALUATOR_BEHAVIOR_STATISTICS_CALL_COUNT")
    expected_name = (
        before_scenarios[0].name if before_scenarios else ROUTE_ONLY_STATISTICS_NAME
    )
    if calls[0][0] != config.name + "_rep0" or calls[0][1] != expected_name:
        raise RouteValidationContractError("EVALUATOR_BEHAVIOR_REPORTING_IDENTITY")
    if config.scenario_configs is not before_configs or tuple(config.scenario_configs) != before_scenarios:
        raise RouteValidationContractError("EVALUATOR_BEHAVIOR_SCENARIO_MUTATION")
    if config.scenario_configs.getitem_zero_count != 0:
        raise RouteValidationContractError("EVALUATOR_BEHAVIOR_INDEX_ZERO_ACCESSED")
    return {
        **fixture_identity,
        "status": "PASS",
        "statistics_route_name": calls[0][0],
        "statistics_scenario_name": calls[0][1],
        "statistics_town": calls[0][4],
        "scenario_configs_identity_preserved": True,
        "scenario_names_preserved": True,
        "scenario_configs_index_zero_access_count": 0,
        "pseudo_scenario_created": False,
        "stopped_before_world": returned is True,
        "captured_output_sha256": sha256_bytes(captured.getvalue().encode("utf-8")),
    }


@dataclass(frozen=True)
class EvaluatorModuleLoadResult:
    module: ModuleType
    evidence: Mapping[str, Any]


def _validate_loaded_compatible_module(
    module: ModuleType,
    *,
    source_path: Path,
    source_sha256: str,
) -> Mapping[str, Any]:
    provenance = getattr(module, "_DRIVECLARIFY_EVALUATOR_PROVENANCE", None)
    if not isinstance(provenance, Mapping):
        raise RouteValidationContractError("EVALUATOR_LOADED_PROVENANCE_MISSING")
    if (
        module.__file__ != str(source_path)
        or provenance.get("source_path") != str(source_path)
        or provenance.get("source_sha256") != source_sha256
        or provenance.get("post_transform_vulnerable_access_count") != 0
        or provenance.get("post_transform_compatible_access_count") != 1
        or provenance.get("adapter_invocation_count") != 1
    ):
        raise RouteValidationContractError("EVALUATOR_LOADED_PROVENANCE_MISMATCH")
    current_identity = _function_code_identity(
        module.LeaderboardEvaluator._load_and_run_scenario
    )
    if current_identity != provenance.get("function_code_identity"):
        raise RouteValidationContractError("EVALUATOR_LOADED_CODE_IDENTITY_MISMATCH")
    return provenance


def load_compatible_evaluator_module(
    evaluator_source_path: str | Path = EVALUATOR_SOURCE_PATH,
    *,
    expected_source_sha256: str = EVALUATOR_SOURCE_SHA256,
    cpu_dry_run: bool = False,
) -> EvaluatorModuleLoadResult:
    """The production adapter/loader used by the future M3E entry and CPU dry run."""

    source_path, payload, source_sha256 = _read_authoritative_evaluator_source(
        evaluator_source_path, expected_source_sha256=expected_source_sha256
    )
    existing = sys.modules.get(PRODUCTION_EVALUATOR_MODULE_NAME)
    if existing is not None:
        provenance = _validate_loaded_compatible_module(
            existing,
            source_path=source_path,
            source_sha256=source_sha256,
        )
        probes = {
            "scenario_free": run_loaded_evaluator_behavior_probe(
                existing, SCENARIO_FREE_FIXTURE_PATH
            ),
            "ordinary_scenario": run_loaded_evaluator_behavior_probe(
                existing, ORDINARY_SCENARIO_FIXTURE_PATH
            ),
        }
        return EvaluatorModuleLoadResult(
            module=existing,
            evidence={
                **dict(provenance),
                "source_state": SOURCE_ALREADY_COMPATIBLE,
                "source_state_reason": "LOADED_FROZEN_TRANSFORM_PRODUCT_PROVENANCE_VERIFIED",
                "double_application_detected": True,
                "transform_reapplied": False,
                "behavior_probes": probes,
            },
        )

    try:
        tree = ast.parse(payload, filename=str(source_path))
    except SyntaxError as exc:
        raise RouteValidationContractError("EVALUATOR_SOURCE_SYNTAX_INVALID") from exc
    prepared = _transform_verified_evaluator_tree(
        tree, source_path=str(source_path), source_sha256=source_sha256
    )

    loader = _DriveClarifyAstExecLoader()
    spec = importlib.util.spec_from_loader(
        PRODUCTION_EVALUATOR_MODULE_NAME, loader, origin=str(source_path)
    )
    if spec is None:
        raise RouteValidationContractError("EVALUATOR_MODULE_SPEC_FAILED")
    module = ModuleType(PRODUCTION_EVALUATOR_MODULE_NAME)
    module.__file__ = str(source_path)
    module.__package__ = ""
    module.__loader__ = loader
    module.__spec__ = spec
    module.__dict__["resolve_evaluator_statistics_name"] = resolve_evaluator_statistics_name
    dependency_importer = None
    forbidden_before = {
        name
        for name in sys.modules
        if name == "carla" or name == "torch" or name.startswith("torch.")
    }
    if cpu_dry_run:
        dependency_importer = _DryRunDependencyImporter()
        guarded_builtins = dict(vars(builtins))
        guarded_builtins["__import__"] = dependency_importer
        module.__dict__["__builtins__"] = guarded_builtins
    sys.modules[PRODUCTION_EVALUATOR_MODULE_NAME] = module
    try:
        code = compile(prepared.tree, str(source_path), "exec", dont_inherit=True)
        exec(code, module.__dict__, module.__dict__)
        function_identity = _function_code_identity(
            module.LeaderboardEvaluator._load_and_run_scenario
        )
        probes = {
            "scenario_free": run_loaded_evaluator_behavior_probe(
                module, SCENARIO_FREE_FIXTURE_PATH
            ),
            "ordinary_scenario": run_loaded_evaluator_behavior_probe(
                module, ORDINARY_SCENARIO_FIXTURE_PATH
            ),
        }
        forbidden_after = {
            name
            for name in sys.modules
            if name == "carla" or name == "torch" or name.startswith("torch.")
        }
        if cpu_dry_run and forbidden_after != forbidden_before:
            raise RouteValidationContractError("CPU_DRY_RUN_PROHIBITED_MODULE_LOADED")
        provenance = {
            **dict(prepared.provenance()),
            "post_transform_compatible_access_count": (
                prepared.post_transform_compatible_access_count
            ),
            "source_state_reason": "ONE_PINNED_VULNERABLE_ACCESS_STRICTLY_TRANSFORMED",
            "module_name": module.__name__,
            "module_file": module.__file__,
            "loader_type": type(loader).__module__ + "." + type(loader).__name__,
            "function_code_identity": function_identity,
            "adapter_invocation_count": 1,
            "double_application_detected": False,
            "transform_reapplied": True,
            "cpu_dry_run": cpu_dry_run,
            "external_dependency_requests_stubbed": (
                list(dependency_importer.requests) if dependency_importer is not None else []
            ),
            "forbidden_runtime_modules_loaded": sorted(forbidden_after - forbidden_before),
            "behavior_probes": probes,
        }
        module._DRIVECLARIFY_EVALUATOR_PROVENANCE = provenance
        return EvaluatorModuleLoadResult(module=module, evidence=provenance)
    except BaseException:
        if sys.modules.get(PRODUCTION_EVALUATOR_MODULE_NAME) is module:
            sys.modules.pop(PRODUCTION_EVALUATOR_MODULE_NAME, None)
        raise


def load_compatible_evaluator_for_base_adapter(
    evaluator_source_path: str | Path,
    expected_source_sha256: str,
    *,
    cpu_dry_run: bool = False,
) -> ModuleType:
    """Drop-in ``_load_original`` hook for the existing production base adapter."""

    result = load_compatible_evaluator_module(
        evaluator_source_path,
        expected_source_sha256=expected_source_sha256,
        cpu_dry_run=cpu_dry_run,
    )
    result.module._DRIVECLARIFY_LAST_LOAD_EVIDENCE = result.evidence
    return result.module


def execute_compatible_evaluator(
    evaluator_source_path: str | Path,
    *,
    expected_source_sha256: str = EVALUATOR_SOURCE_SHA256,
) -> None:
    """Explicit future runtime entry; never called by CPU/static tests.

    The baseline file is read, hash-pinned, AST-adapted in memory, and executed
    under a non-``__main__`` namespace before its existing ``main`` is called.
    No baseline file is changed.  Calling this function is a real evaluator run
    and therefore requires the separate M3E authorization gates.
    """

    result = load_compatible_evaluator_module(
        evaluator_source_path,
        expected_source_sha256=expected_source_sha256,
        cpu_dry_run=False,
    )
    main = getattr(result.module, "main", None)
    if not callable(main):
        raise RouteValidationContractError("EVALUATOR_MAIN_MISSING")
    main()


def nominal_first_observation_interval() -> dict[str, Any]:
    """Return the statically derived nominal observation station interval."""

    lower, exclusive_upper = NOMINAL_FIRST_OBSERVATION_SIGNED_INTERVAL_M
    return {
        "signed_station_origin": "DECISION_POINT; NEGATIVE_IS_INCOMING_PREDECISION",
        "lower_inclusive_m": lower,
        "upper_exclusive_m": exclusive_upper,
        "equivalent_predecision_distance_m": {
            "minimum_exclusive_m": 0.0,
            "maximum_inclusive_m": -lower,
        },
        "derivation": "tail[17,18,19]+observation_station must be inside [8.7,20.7]; observation_station < 0",
    }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def evaluate_first_observation_eligibility(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Fail-closed first-frame gate for the future M3E entry.

    PASS requires only the verified runtime position of observation zero to be
    inside the statically derived nominal interval. Candidate plans do not exist
    yet at this entry gate and therefore are neither read nor selected here.
    Their actual tail geometry remains independently fail-closed in the mapper.
    Every missing/invalid/out-of-contract case returns UNKNOWN with
    ``EVIDENCE_UNAVAILABLE``; the caller must stop and must not examine a second
    observation.
    """

    reasons: list[str] = []
    if evidence.get("observation_sequence_index") != 0:
        reasons.append("NOT_FIRST_MODEL_READY_OBSERVATION")
    if evidence.get("prior_model_ready_observation_count") != 0:
        reasons.append("PRIOR_MODEL_READY_OBSERVATION_EXISTS")
    if evidence.get("skipped_observation_count") != 0:
        reasons.append("OBSERVATION_FALLBACK_FORBIDDEN")
    if evidence.get("position_evidence_status") != "VERIFIED":
        reasons.append("OBSERVATION_POSITION_NOT_VERIFIED")
    observation_station = _finite_number(evidence.get("observation_signed_station_m"))
    if observation_station is None:
        reasons.append("OBSERVATION_SIGNED_STATION_MISSING_OR_INVALID")
    elif observation_station >= 0.0:
        reasons.append("OBSERVATION_NOT_BEFORE_DECISION_POINT")
    elif observation_station < -ROUTE_START_LEAD_IN_M - 1e-9:
        reasons.append("OBSERVATION_BEFORE_FROZEN_ROUTE_START")
    elif observation_station < NOMINAL_FIRST_OBSERVATION_SIGNED_INTERVAL_M[0] - 1e-9:
        reasons.append("NOMINAL_PLAN_HORIZON_CANNOT_REACH_EVALUATION_INTERVAL_FROM_FIRST_OBSERVATION")

    verdict = "PASS" if not reasons else "UNKNOWN"
    nominal_tail = None
    if observation_station is not None:
        nominal_tail = [observation_station + offset for offset in SIMLINGO_NOMINAL_TAIL_OFFSETS_M]
    return {
        "verdict": verdict,
        "evidence_status": "COMPLETE" if verdict == "PASS" else "EVIDENCE_UNAVAILABLE",
        "eligible_for_candidate_batch": verdict == "PASS",
        "must_stop_without_second_observation": verdict != "PASS",
        "reason_codes": reasons,
        "observation_signed_station_m": observation_station,
        "nominal_plan_tail_stations_from_decision_point_m": nominal_tail,
        "candidate_or_model_output_read_by_entry_gate": False,
        "actual_candidate_plan_tail_policy": "DEFER_TO_STATIC_BRANCH_PLAN_MAPPER_UNKNOWN_PRESERVING",
        "frozen_evaluation_interval_m": list(EVALUATION_INTERVAL_M),
    }


def simple_config(scenario_configs: Iterable[Any]) -> Any:
    """Tiny test/helper factory; it does not mutate or synthesize scenarios."""

    return SimpleNamespace(scenario_configs=scenario_configs)
