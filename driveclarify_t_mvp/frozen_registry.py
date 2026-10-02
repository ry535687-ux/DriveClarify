"""Exact, pre-exposure source registries for T-B5 and safety evidence.

These identities come from the authoritative V11 workspace at the RQ2 T-MVP
implementation boundary. Runtime callers cannot replace paths or expected hashes.
Any drift fails closed and requires a new prospective freeze, never a per-result
override.
"""

from __future__ import annotations

import ast
from dataclasses import asdict
import hashlib
import inspect
from pathlib import Path
from typing import Mapping

from .canonical import canonical_sha256


FROZEN_SIMLINGO_AGENT_OWNER = "team_code.agent_simlingo.LingoAgent"
FROZEN_SIMLINGO_AGENT_SOURCE = Path(
    "/home/buaa/wrh/simlingo/team_code/agent_simlingo.py"
)
FROZEN_SIMLINGO_CHECKPOINT = Path(
    "/home/buaa/wrh/DriveClarify/reports/driveclarify_v3_short_prefix_a1_fast_track/"
    "a1_training_v2/selected/checkpoints/a1_selected.ckpt/pytorch_model.pt"
)
FROZEN_SIMLINGO_CONFIG = Path(
    "/home/buaa/wrh/DriveClarify/reports/driveclarify_v3_short_prefix_a1_fast_track/"
    "a1_training_v2/selected/.hydra/config.yaml"
)
FROZEN_SIMLINGO_TOKENIZER_DIRECTORY = Path(
    "/home/buaa/wrh/simlingo/pretrained/InternVL2-1B"
)
FROZEN_SIMLINGO_LOADED_TOKENIZER_SHA256 = (
    "e071b6218a708a796c7e0aaf3df4f6df1e7912ecee9dc5d5a3a83442f8050cfd"
)
FROZEN_SIMLINGO_MODEL_ID = "OpenGVLab/InternVL2-1B"
FROZEN_SIMLINGO_TOKENIZER_OWNER = (
    "transformers.models.qwen2.tokenization_qwen2_fast.Qwen2TokenizerFast"
)
FROZEN_SIMLINGO_MAX_CONTEXT_TOKENS = 8192

FROZEN_T_B5_FILES: Mapping[Path, str] = {
    FROZEN_SIMLINGO_AGENT_SOURCE: (
        "863e60ee19906ed58d3a9afff898b1c65426676cada8b722904ed65f4e2c3db8"
    ),
    FROZEN_SIMLINGO_CHECKPOINT: (
        "cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044"
    ),
    FROZEN_SIMLINGO_CONFIG: (
        "b7c25f1d0d2d7cd466c32a4115d843a1f75fcb52d1c89353f1a0753a2f6185a0"
    ),
    FROZEN_SIMLINGO_TOKENIZER_DIRECTORY / "model.safetensors": (
        "9420916a7fab7d2009f7907cdffa341c9cb6be7c5e0cf4ee193de16fde647dea"
    ),
    FROZEN_SIMLINGO_TOKENIZER_DIRECTORY / "vocab.json": (
        "87a257b04b17642a0688c98cd1df89c398bda4fee532d6f88b38a659ecb4ac8d"
    ),
    FROZEN_SIMLINGO_TOKENIZER_DIRECTORY / "merges.txt": (
        "455e0caaa06abffc663e9282dfe71dde07fd1991eaf24146bf08793c4dba4497"
    ),
    FROZEN_SIMLINGO_TOKENIZER_DIRECTORY / "tokenizer_config.json": (
        "2ed7120320ff66f65e1dccc704f318eeccab8941292424dfdf8290dd1b689634"
    ),
    FROZEN_SIMLINGO_TOKENIZER_DIRECTORY / "added_tokens.json": (
        "baea1c1e8f24ce785f49112072664b8be778e9242a1a21cf6f7c6cb92add6d10"
    ),
    FROZEN_SIMLINGO_TOKENIZER_DIRECTORY / "special_tokens_map.json": (
        "af3c7a7e8e3396571d8640b822cd611de2b51b15cf65a5dc1054ffea31483427"
    ),
    FROZEN_SIMLINGO_TOKENIZER_DIRECTORY / "config.json": (
        "2e85a81acfc8cbea3dc3cee831e6c4b171d456f30938aef14cf37d5eb2ac00ed"
    ),
}

FROZEN_EVALUATOR_CRITERIA_SOURCE = Path(
    "/home/buaa/wrh/simlingo/scenario_runner_autopilot/srunner/"
    "scenariomanager/scenarioatomics/atomic_criteria.py"
)
FROZEN_EVALUATOR_FILES: Mapping[Path, str] = {
    FROZEN_EVALUATOR_CRITERIA_SOURCE: (
        "c2b76391ddf2d324c7370c65cab571b1706aad0f80dd539f04514abff6468c92"
    ),
    Path(
        "/home/buaa/wrh/simlingo/leaderboard_autopilot/leaderboard/leaderboard_evaluator.py"
    ): "c0b27c1b6461833dc24bd9b70373def7eeb89cf2594c149f7b77e3a5e1a07cd4",
    Path(
        "/home/buaa/wrh/simlingo/leaderboard_autopilot/leaderboard/utils/statistics_manager.py"
    ): "c2c08f87f4e0da04a5d88f8f0abffa36be57ad3293b4c54fe8879cfe2a222ffd",
    Path(
        "/home/buaa/wrh/DriveClarify/reports/driveclarify_v11_fresh_prospective_rq1/"
        "run_formal_arm_v11.sh"
    ): "b0adf44f636e836f3e7be261794e556700cb4441dacb36644c57c8e337409806",
}
FROZEN_EVALUATOR_OWNER_MODULE = (
    "srunner.scenariomanager.scenarioatomics.atomic_criteria"
)
FROZEN_SAFETY_OWNER_CLASSES: Mapping[str, str] = {
    "collision": "CollisionTest",
    "outside_route_lanes": "OutsideRouteLanesTest",
    "route_deviation": "InRouteTest",
}

FROZEN_V11_ADMISSIBILITY_OWNER = (
    "driveclarify_clear_passthrough_v11.replan.ReplanAdmissibility"
)
FROZEN_V11_ADMISSIBILITY_SOURCE = Path(
    "/home/buaa/wrh/DriveClarify/driveclarify_clear_passthrough_v11/replan.py"
)
FROZEN_V11_ADMISSIBILITY_SOURCE_SHA256 = (
    "0d8bf97e5a11bde248f8eebabb135e586f73118dc4c7ef7568eaef57f775c37c"
)
FROZEN_V11_ADMISSIBILITY_THRESHOLDS: Mapping[str, int | float] = {
    "maximum_route_age_frames": 120,
    "maximum_destination_delta_m": 0.001,
    "maximum_join_distance_m": 3.0,
    "maximum_join_heading_delta_degrees": 45.0,
    "maximum_route_segment_gap_m": 8.0,
    "maximum_local_curvature_per_m": 0.35,
    "local_curvature_horizon_m": 35.0,
    "maximum_lateral_acceleration_mps2": 2.5,
    "comfortable_deceleration_mps2": 2.5,
    "reaction_time_s": 0.5,
    "minimum_commitment_margin_m": 3.0,
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def assert_file_registry_exact(registry: Mapping[Path, str], *, owner: str) -> str:
    measured: list[tuple[str, str]] = []
    for path, expected in registry.items():
        if not path.is_file():
            raise RuntimeError(owner + "_FROZEN_FILE_MISSING:" + str(path))
        actual = file_sha256(path)
        if actual != expected:
            raise RuntimeError(owner + "_FROZEN_FILE_HASH_MISMATCH:" + str(path))
        measured.append((str(path), actual))
    return canonical_sha256(
        {"owner": owner, "files": tuple(sorted(measured))}
    )


def assert_frozen_t_b5_registry() -> str:
    registry_hash = assert_file_registry_exact(FROZEN_T_B5_FILES, owner="T_B5")
    tree = ast.parse(
        FROZEN_SIMLINGO_AGENT_SOURCE.read_text(encoding="utf-8"),
        filename=str(FROZEN_SIMLINGO_AGENT_SOURCE),
    )
    lingo = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "LingoAgent"
        ),
        None,
    )
    if lingo is None:
        raise RuntimeError("T_B5_FROZEN_LINGO_AGENT_CLASS_MISSING")
    methods = {
        node.name: node
        for node in lingo.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if not {"setup", "tick", "run_step"} <= set(methods):
        raise RuntimeError("T_B5_FROZEN_AGENT_METHOD_MISSING")
    source = FROZEN_SIMLINGO_AGENT_SOURCE.read_text(encoding="utf-8")
    required_source_tokens = (
        "checkpoint_state = torch.load(self.config_path)",
        "self.model.load_state_dict(checkpoint_state",
        "self.custom_prompt",
        "self.tokenizer(prompt_batch_list",
        "pred_speed_wps, pred_route, language = self.model(model_input)",
    )
    if any(token not in source for token in required_source_tokens):
        raise RuntimeError("T_B5_FROZEN_LOAD_PROMPT_FORWARD_DATAFLOW_MISSING")
    return registry_hash


def assert_frozen_evaluator_registry() -> str:
    registry_hash = assert_file_registry_exact(
        FROZEN_EVALUATOR_FILES, owner="T_MVP_EVALUATOR"
    )
    tree = ast.parse(
        FROZEN_EVALUATOR_CRITERIA_SOURCE.read_text(encoding="utf-8"),
        filename=str(FROZEN_EVALUATOR_CRITERIA_SOURCE),
    )
    class_names = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }
    missing = set(FROZEN_SAFETY_OWNER_CLASSES.values()) - class_names
    if missing:
        raise RuntimeError(
            "T_MVP_FROZEN_SAFETY_OWNER_CLASS_MISSING:" + ",".join(sorted(missing))
        )
    return registry_hash


def assert_frozen_v11_admissibility_owner(owner: object | None = None) -> str:
    """Fail closed unless B6 uses the exact frozen V11 owner and defaults.

    Passing ``None`` revalidates the class/source/default registry itself.  Passing
    an instance additionally proves that the runtime owner is neither a duck type,
    subclass, monkey-patched method, nor a threshold-retuned instance.
    """

    from driveclarify_clear_passthrough_v11.replan import (
        ReplanAdmissibility,
        ReplanThresholds,
    )

    measured_source = Path(inspect.getsourcefile(ReplanAdmissibility) or "").resolve()
    if measured_source != FROZEN_V11_ADMISSIBILITY_SOURCE.resolve():
        raise RuntimeError("T_B6_FROZEN_ADMISSIBILITY_SOURCE_PATH_MISMATCH")
    if file_sha256(measured_source) != FROZEN_V11_ADMISSIBILITY_SOURCE_SHA256:
        raise RuntimeError("T_B6_FROZEN_ADMISSIBILITY_SOURCE_HASH_MISMATCH")
    measured_owner_identity = (
        ReplanAdmissibility.__module__ + "." + ReplanAdmissibility.__qualname__
    )
    if measured_owner_identity != FROZEN_V11_ADMISSIBILITY_OWNER:
        raise RuntimeError("T_B6_FROZEN_ADMISSIBILITY_OWNER_IDENTITY_MISMATCH")

    measured_owner = ReplanAdmissibility() if owner is None else owner
    if type(measured_owner) is not ReplanAdmissibility:
        raise RuntimeError("T_B6_FROZEN_ADMISSIBILITY_OWNER_TYPE_MISMATCH")
    if getattr(measured_owner.assess, "__func__", None) is not ReplanAdmissibility.assess:
        raise RuntimeError("T_B6_FROZEN_ADMISSIBILITY_ASSESS_METHOD_MISMATCH")
    thresholds = getattr(measured_owner, "thresholds", None)
    if type(thresholds) is not ReplanThresholds:
        raise RuntimeError("T_B6_FROZEN_ADMISSIBILITY_THRESHOLDS_TYPE_MISMATCH")
    measured_thresholds = asdict(thresholds)
    if measured_thresholds != dict(FROZEN_V11_ADMISSIBILITY_THRESHOLDS):
        raise RuntimeError("T_B6_FROZEN_ADMISSIBILITY_THRESHOLDS_RETUNED")
    if asdict(ReplanThresholds()) != dict(FROZEN_V11_ADMISSIBILITY_THRESHOLDS):
        raise RuntimeError("T_B6_FROZEN_ADMISSIBILITY_DEFAULTS_DRIFTED")
    return canonical_sha256(
        {
            "schema_version": "driveclarify.rq2.t_b6_owner_registry.v1",
            "owner_identity": measured_owner_identity,
            "source_path": str(measured_source),
            "source_sha256": FROZEN_V11_ADMISSIBILITY_SOURCE_SHA256,
            "thresholds": tuple(sorted(measured_thresholds.items())),
        }
    )
