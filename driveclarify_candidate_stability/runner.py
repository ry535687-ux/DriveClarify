"""Candidate runner contracts and a recorded-evidence replay implementation."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any, Dict, Protocol, Sequence, Tuple

from .live_protocol import LiveCandidateBackend
from .metrics import normalize_trajectory
from .types import (
    CandidateBatchContext,
    CandidateRequest,
    CandidateResult,
    DeterministicSettings,
)


EVIDENCE_FIELDS = (
    "observation_id",
    "observation_digest",
    "observation_members",
    "closed_loop_state_digest",
    "closed_loop_state_members",
    "shared_tensor_object_identity",
    "prompts",
    "forward_order",
    "outputs",
    "measured_evidence",
    "prestop_identity_before_forwards",
)


class CandidateRunner(Protocol):
    """Interface shared by replay and a future audited live runner."""

    def context(self) -> CandidateBatchContext:
        ...

    def run(self, requests: Sequence[CandidateRequest]) -> Tuple[CandidateResult, ...]:
        ...


class FixtureInterpretationGenerator:
    """MVP K=2 fixture-only interpretation generator; never calls an LLM."""

    def __init__(self, interpretations: Mapping[str, str]) -> None:
        if tuple(sorted(interpretations)) != ("A", "B"):
            raise ValueError("MVP_FIXTURE_INTERPRETATIONS_REQUIRE_EXACT_A_B")
        self._interpretations = dict(interpretations)

    def generate(self) -> Tuple[CandidateRequest, ...]:
        return tuple(
            CandidateRequest(
                candidate_id=key,
                interpretation=self._interpretations[key],
                expected_repetition_group=key,
            )
            for key in ("A", "B")
        )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _candidate_evidence_hash(payload: Mapping[str, Any]) -> str:
    missing = [field for field in EVIDENCE_FIELDS if field not in payload]
    if missing:
        raise ValueError("RECORDED_EVIDENCE_FIELDS_MISSING:" + ",".join(missing))
    evidence = {field: payload[field] for field in EVIDENCE_FIELDS}
    return hashlib.sha256(_canonical(evidence)).hexdigest()


def _float32_hash(trajectory: Any) -> str:
    normalized = normalize_trajectory(trajectory)
    raw = b"".join(
        struct.pack("<f", coordinate)
        for point in normalized
        for coordinate in point
    )
    return hashlib.sha256(raw).hexdigest()


def _read_payload(source: Path) -> Tuple[Dict[str, Any], str]:
    document = json.loads(source.read_text(encoding="utf-8"))
    if (
        isinstance(document, dict)
        and isinstance(document.get("candidate_persistence"), dict)
        and isinstance(
            document["candidate_persistence"].get("raw_candidate_payload"), dict
        )
    ):
        return (
            document["candidate_persistence"]["raw_candidate_payload"],
            "SCIENTIFIC_RESULT_EMBEDDED_CANDIDATE",
        )
    if isinstance(document, dict) and "outputs" in document:
        return document, "DIRECT_CANDIDATE_PAYLOAD"
    raise ValueError("UNRECOGNIZED_RECORDED_CANDIDATE_SOURCE")


class RecordedCandidateRunner:
    """Read-only runner for psplan13-style persisted candidate payloads."""

    def __init__(
        self,
        source: Path,
        deterministic_settings: DeterministicSettings,
    ) -> None:
        self.source = Path(source)
        self.payload, self.source_kind = _read_payload(self.source)
        self.side_effect_counts = {
            "model_forward": 0,
            "carla": 0,
            "evaluator": 0,
            "pid": 0,
            "control": 0,
            "planner_advancement": 0,
        }
        self._validate_payload()
        self._context = self._build_context(deterministic_settings)
        self._results = self._build_results()

    def _validate_payload(self) -> None:
        if self.payload.get("candidate_evaluation_status") != "COMPLETE":
            raise ValueError("RECORDED_CANDIDATE_NOT_COMPLETE")
        if self.payload.get("forward_order") != ["A1", "B1", "A2", "B2"]:
            raise ValueError("RECORDED_FORWARD_ORDER_INVALID")
        recorded = self.payload.get("candidate_evidence_sha256")
        recomputed = _candidate_evidence_hash(self.payload)
        if recorded != recomputed:
            raise ValueError("RECORDED_CANDIDATE_EVIDENCE_HASH_MISMATCH")
        outputs = self.payload.get("outputs")
        if not isinstance(outputs, list) or len(outputs) != 4:
            raise ValueError("RECORDED_OUTPUT_COUNT_INVALID")
        for output in outputs:
            if _float32_hash(output.get("pred_route")) != output.get("route_sha256"):
                raise ValueError(
                    "RECORDED_ROUTE_HASH_MISMATCH:" + str(output.get("label"))
                )
            if (
                _float32_hash(output.get("pred_speed_wps"))
                != output.get("speed_sha256")
            ):
                raise ValueError(
                    "RECORDED_SPEED_HASH_MISMATCH:" + str(output.get("label"))
                )

    def _build_context(
        self, deterministic_settings: DeterministicSettings
    ) -> CandidateBatchContext:
        measured = self.payload["measured_evidence"]
        before = measured.get("model_object_identity_before")
        after = measured.get("model_object_identity_after")
        if before != after:
            raise ValueError("RECORDED_MODEL_INSTANCE_IDENTITY_CHANGED")
        prestop = self.payload.get("prestop_guard", {})
        frame = (
            prestop.get("identity_inventory", {})
            .get("expected", {})
            .get("snapshot", {})
            .get("frame")
        )
        digest = str(self.payload["observation_digest"])
        return CandidateBatchContext(
            run_id=str(self.payload["run_id"]),
            observation_id=str(self.payload["observation_id"]),
            observation_digest=digest,
            source_frame=frame,
            closed_loop_state_identity=str(
                self.payload["closed_loop_state_digest"]
            ),
            ego_state_identity=None,
            navigation_identity=None,
            model_instance_identity=before,
            preprocessing_identity="RECORDED_OBSERVATION_DIGEST:" + digest,
            initial_rng_state_identities=None,
            cache_history_state_identity=None,
            freshness_token=str(self.payload["candidate_evidence_sha256"]),
            deterministic_settings=deterministic_settings,
            provenance=(
                str(self.source),
                self.source_kind,
                "ego/navigation are bound by the aggregate closed-loop state digest "
                "but were not persisted as separate identities",
            ),
        )

    def _build_results(self) -> Tuple[CandidateResult, ...]:
        results = []
        for output in self.payload["outputs"]:
            label = str(output["label"])
            interpretation = label[0]
            repetition = int(label[1:])
            results.append(
                CandidateResult(
                    candidate_id=label,
                    interpretation_id=interpretation,
                    repetition_index=repetition,
                    observation_id=str(self.payload["observation_id"]),
                    observation_digest=str(self.payload["observation_digest"]),
                    source_frame=self._context.source_frame,
                    freshness_token=self._context.freshness_token,
                    model_instance_identity=self._context.model_instance_identity,
                    route=normalize_trajectory(output["pred_route"]),
                    speed=normalize_trajectory(output["pred_speed_wps"]),
                    language_output=tuple(str(item) for item in output["language"]),
                    language_token_ids=None,
                    route_hash=str(output["route_sha256"]),
                    speed_hash=str(output["speed_sha256"]),
                    normalized_dtype="float32",
                    normalized_device="cpu",
                    latency_seconds=None,
                )
            )
        return tuple(results)

    def context(self) -> CandidateBatchContext:
        return self._context

    def run(
        self, requests: Sequence[CandidateRequest]
    ) -> Tuple[CandidateResult, ...]:
        requested = {request.expected_repetition_group for request in requests}
        if requested != {"A", "B"}:
            raise ValueError("RECORDED_REPLAY_REQUESTS_REQUIRE_A_B")
        return self._results

    @property
    def evidence_hash(self) -> str:
        return str(self.payload["candidate_evidence_sha256"])

    @property
    def evidence_hash_recomputed(self) -> str:
        return _candidate_evidence_hash(self.payload)
