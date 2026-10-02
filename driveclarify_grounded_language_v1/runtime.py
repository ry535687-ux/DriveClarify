"""Factory for one default-off grounded runtime supporting ACT, ASK, and WAIT."""

from __future__ import annotations

import os
from typing import Any

from driveclarify_language_grounding_v1.contracts import AmbiguityKind
from driveclarify_language_grounding_v1.slot_parser import SemanticSlotParser

from .contracts import FEATURE_FLAG, INSTRUCTION_ENV, OUTPUT_ENV, TRIGGER_FRAME_ENV
from .static_runtime import ReferentialGroundedRuntime
from .temporal_runtime import IntegratedTemporalGroundedRuntime


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def build_grounded_language_v1_runtime(agent: Any) -> Any:
    if not _truthy(os.environ.get(FEATURE_FLAG)):
        raise RuntimeError("GROUNDED_LANGUAGE_V1_FEATURE_FLAG_OFF")
    output = os.environ.get(OUTPUT_ENV) or os.environ.get("DRIVECLARIFY_SHADOW_OUTPUT_DIR")
    if not output:
        raise RuntimeError("GROUNDED_LANGUAGE_V1_OUTPUT_DIR_MISSING")
    instruction = (
        os.environ.get(INSTRUCTION_ENV)
        or os.environ.get("DRIVECLARIFY_PAPER_MVP_STAGE6B_RAW_INSTRUCTION")
        or getattr(agent, "custom_prompt", None)
    )
    if not instruction:
        raise RuntimeError("GROUNDED_LANGUAGE_V1_RAW_INSTRUCTION_MISSING")
    if _truthy(os.environ.get("DRIVECLARIFY_RQ2_T_E2_V4_NATIVE_EVIDENCE")):
        from driveclarify_rq2_t_e2_v4.runtime import build_e2_v4_native_runtime

        return build_e2_v4_native_runtime(
            agent, str(instruction), str(output)
        )
    if _truthy(os.environ.get("DRIVECLARIFY_RQ2_T_E2_V3_NATIVE_EVIDENCE")):
        from driveclarify_rq2_t_e2_v3.runtime import build_e2_v3_native_runtime

        return build_e2_v3_native_runtime(
            agent, str(instruction), str(output)
        )
    if _truthy(os.environ.get("DRIVECLARIFY_RQ2_T_V2_NATIVE_EVIDENCE")):
        from driveclarify_rq2_t_v2.native_runtime import (
            build_rq2_t_v2_native_runtime,
        )

        return build_rq2_t_v2_native_runtime(
            agent, str(instruction), str(output)
        )
    if _truthy(os.environ.get("DRIVECLARIFY_PHASE_B_EVIDENCE_COMPLETION_V1")):
        from driveclarify_phase_b_evidence_completion_v1.runtime import (
            PhaseBEvidenceCompletionRuntime,
        )

        return PhaseBEvidenceCompletionRuntime(
            agent, str(output), raw_instruction=str(instruction)
        )
    if _truthy(
        os.environ.get("DRIVECLARIFY_PERSISTENT_AMBIGUITY_EVIDENCE_PROBE")
    ):
        from driveclarify_decision_window_carla_probe_v1.runtime import (
            DecisionWindowEvidenceProbeRuntime,
        )

        return DecisionWindowEvidenceProbeRuntime(
            agent, str(output), raw_instruction=str(instruction)
        )
    if _truthy(
        os.environ.get("DRIVECLARIFY_DECISION_EVIDENCE_CONTRACT_V2")
    ) or _truthy(
        os.environ.get("DRIVECLARIFY_PERSISTENT_AMBIGUITY_RUNTIME_V1")
    ):
        from driveclarify_persistent_ambiguity_runtime_v1.runtime import (
            NativeCarlaRouteLocalEvidenceProvider,
            build_persistent_ambiguity_runtime,
        )

        route_local_evidence_provider = (
            NativeCarlaRouteLocalEvidenceProvider.from_native_agent(agent)
        )
        runtime = build_persistent_ambiguity_runtime(
            agent,
            str(instruction),
            str(output),
            route_local_evidence_provider=route_local_evidence_provider,
        )
        provider_object_identity = (
            type(route_local_evidence_provider).__module__
            + "."
            + type(route_local_evidence_provider).__qualname__
            + "@"
            + format(id(route_local_evidence_provider), "x")
        )
        provider_binding = route_local_evidence_provider.production_binding()
        runtime._receipt.update(
            {
                "native_grounded_factory": (
                    "driveclarify_grounded_language_v1.runtime."
                    "build_grounded_language_v1_runtime"
                ),
                "native_grounded_factory_provider_object_identity": (
                    provider_object_identity
                ),
                "native_grounded_factory_provider_identity": (
                    provider_binding["provider_id"]
                ),
                "native_grounded_factory_builder_runtime_same_provider_object": bool(
                    runtime._hard_gate_evidence_provider
                    is route_local_evidence_provider
                    and runtime._receipt.get(
                        "production_builder_provider_object_identity"
                    )
                    == provider_object_identity
                    and runtime._receipt.get(
                        "hard_gate_evidence_provider_object_identity"
                    )
                    == provider_object_identity
                ),
                "native_grounded_factory_production_binding_status": (
                    "PASS_NATIVE_FACTORY_BUILDER_RUNTIME_PROVIDER_BOUND"
                ),
            }
        )
        runtime._persist()
        return runtime
    if _truthy(
        os.environ.get("DRIVECLARIFY_OFFICIAL_DREAMING_ADAPTER_ALIGNMENT")
    ):
        mode = os.environ.get(
            "DRIVECLARIFY_OFFICIAL_DREAMING_ALIGNMENT_MODE", "explicit"
        ).strip().casefold()
        if mode == "explicit":
            from driveclarify_official_dreaming_adapter.runtime import (
                OfficialAlignedExplicitRuntime,
            )

            return OfficialAlignedExplicitRuntime(agent, str(output))
        if mode == "grounded_white_van":
            raise RuntimeError(
                "GROUNDED_WHITE_VAN_NOT_AUTHORIZED_EXPLICIT_GATE_NOT_PASSED"
            )
        raise RuntimeError("UNKNOWN_OFFICIAL_DREAMING_ALIGNMENT_MODE:" + mode)
    if _truthy(
        os.environ.get(
            "DRIVECLARIFY_SIMLINGO_DISTINCT_LOCAL_CANDIDATE_DIAGNOSTIC"
        )
    ):
        # Isolated TRAIN-only diagnostic.  This branch performs candidate
        # forwards only and never changes the plan delivered to the existing
        # PID/controller lifecycle.
        from driveclarify_simlingo_local_candidate_diagnostic.runtime import (
            LocalCandidateDiagnosticRuntime,
        )

        return LocalCandidateDiagnosticRuntime(agent, str(output))
    if _truthy(os.environ.get("DRIVECLARIFY_GROUNDED_LANGUAGE_V1_E1_R1")):
        # E1-R1 is an append-only TRAIN extension.  Keep the controlled-
        # integration factory as the single SimLingo hook, but route the
        # explicitly enabled revision to its own runtime implementation.
        from driveclarify_grounded_language_v1_extension_e1_r1.runtime import (
            build_e1r1_runtime,
        )

        return build_e1r1_runtime(agent, str(instruction), str(output))
    parsed = SemanticSlotParser().parse(str(instruction))
    if parsed.ambiguity_kind is AmbiguityKind.TEMPORAL:
        return IntegratedTemporalGroundedRuntime(agent, output)
    trigger = os.environ.get(TRIGGER_FRAME_ENV)
    return ReferentialGroundedRuntime(
        agent,
        output,
        raw_instruction=str(instruction),
        trigger_frame=int(trigger) if trigger else None,
    )


__all__ = ["build_grounded_language_v1_runtime"]
