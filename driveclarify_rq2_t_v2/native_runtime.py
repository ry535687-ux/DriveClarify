"""Default-off native B0/B1/B2 evidence-only runtime integration.

The wrapper delegates every policy/control hook to the already-qualified V1
persistent runtime.  It reads one already-computed decision-history row and
publishes three observational views; it owns no model, planner, PID, candidate
computation, or vehicle-control API.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from driveclarify_rq2_t.measurement import adapt_production_history_row, canonical_sha256

from .method import EvidenceEnabledTemporalMethodV2
from .providers import FORBIDDEN_RUNTIME_KEYS, assert_runtime_payload_has_no_oracle


FEATURE_FLAG = "DRIVECLARIFY_RQ2_T_V2_NATIVE_EVIDENCE"
LOCAL_HORIZON_M = 100.0
PAIRED_FILENAME = "RQ2_T_V2_PAIRED_VIEW_EVIDENCE.jsonl"
RECEIPT_FILENAME = "RQ2_T_V2_NATIVE_RUNTIME_RECEIPT.json"
_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}


def _truthy(value: Any) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def parse_required_ordinal(raw_instruction: str) -> Optional[int]:
    """Parse only an explicit 1..10 ordinal attached to a topology noun."""

    text = str(raw_instruction).casefold()
    noun = r"(?:opening|junction|intersection|turn|branch|exit)"
    for word, value in _ORDINAL_WORDS.items():
        if re.search(r"\b" + word + r"\s+" + noun + r"\b", text):
            return value
    match = re.search(r"\b(10|[1-9])(?:st|nd|rd|th)?\s+" + noun + r"\b", text)
    return None if match is None else int(match.group(1))


def _planner_snapshot(agent: Any) -> dict[str, Any]:
    planner = getattr(agent, "_route_planner", None)
    route = getattr(planner, "route", None) if planner is not None else None
    try:
        route_length = len(route)
    except TypeError:
        route_length = None
    return {
        "planner_object_id": None if planner is None else id(planner),
        "route_object_id": None if route is None else id(route),
        "route_length": route_length,
        "is_last": None if planner is None else getattr(planner, "is_last", None),
    }


def _counter_snapshot(base: Any) -> dict[str, Any]:
    return {
        "normal_forwards": int(getattr(base, "_normal_forwards", 0)),
        "candidate_forwards": int(getattr(base, "_candidate_forwards", 0)),
        "pid_invocations": int(getattr(base, "_pid_invocations", 0)),
        "control_observations": int(getattr(base, "_control_observations", 0)),
        "planner": _planner_snapshot(base.agent),
    }


def _selected_grounding_rows(base: Any) -> tuple[Mapping[str, Any], ...]:
    grounding = getattr(base, "_receipt", {}).get("grounding", {})
    rows = grounding.get("selected_referents", ()) if isinstance(grounding, Mapping) else ()
    return tuple(row for row in rows if isinstance(row, Mapping))


def _grounding_signal(base: Any, history_row: Mapping[str, Any], now_s: float) -> Optional[dict[str, Any]]:
    source_frame = history_row.get("source_frame_id")
    source_observation = str(history_row.get("source_observation_id") or "")
    selected = _selected_grounding_rows(base)
    # B1 is current-frame only.  Past detections are supplied once, at their
    # native source frame; only B2 may retain them afterwards.
    if not selected or any(row.get("frame_id") != source_frame for row in selected):
        return None
    by_lineage = {
        str(row.get("local_object_id") or row.get("track_id") or ""): row
        for row in selected
    }
    evidence = history_row.get("m2b_inputs", {}).get("evidence", {})
    future = evidence.get("future_obligation", {}) if isinstance(evidence, Mapping) else {}
    candidates = []
    for row in future.get("rows", ()) if isinstance(future, Mapping) else ():
        if not isinstance(row, Mapping):
            continue
        lineage = str(row.get("referent_lineage_id") or "")
        detected = by_lineage.get(lineage, {})
        candidates.append(
            {
                "candidate_id": row.get("candidate_id"),
                "interpretation_id": row.get("interpretation_id"),
                "referent_lineage_id": lineage,
                "referent_description": row.get("referent_description"),
                "identity_confidence": detected.get("detector_confidence"),
                "visibility": row.get("visibility"),
                "privileged": row.get("privileged"),
                "semantic_fresh": row.get("semantic_fresh"),
                "active_unresolved": row.get("active_unresolved"),
                "source_kinds": list(row.get("source_kinds", ())),
            }
        )
    return {
        "authorization_scope": "RUNTIME_CAMERA_CURRENT_OR_PAST",
        "source_frame_id": source_frame,
        "source_observation_id": source_observation,
        "simulation_time_s": float(now_s),
        "candidate_groundings": candidates,
    }


def _live_map() -> Any:
    try:
        from srunner.scenariomanager.carla_data_provider import CarlaDataProvider

        getter = getattr(CarlaDataProvider, "get_map", None)
        value = getter() if callable(getter) else None
        if value is not None:
            return value
        world = CarlaDataProvider.get_world()
        return None if world is None else world.get_map()
    except (AttributeError, ImportError, RuntimeError):
        return None


def _topology_signal(base: Any, history_row: Mapping[str, Any], now_s: float) -> Optional[dict[str, Any]]:
    ordinal = parse_required_ordinal(str(base.raw_instruction))
    if ordinal is None:
        return None
    route = base._route()
    route_rows = base.topology_enumerator._route_rows(route)
    if len(route_rows) < 2:
        return None
    opportunities = base.topology_enumerator.enumerate(route, _live_map())
    local = [row for row in opportunities if float(row.distance_or_progress) <= LOCAL_HORIZON_M + 1e-9]
    values = [
        {
            "junction_id": row.junction_id,
            "road_id": row.entry_road_id,
            "lane_id": row.entry_lane_id,
            "route_order_index": row.route_order_index,
            "maneuver_class": row.maneuver_direction,
            "locally_observable": True,
        }
        for row in local
    ]
    boundary = values[0]["junction_id"] if values else "NO_LOCAL_JUNCTION"
    return {
        "authorization_scope": "LOCAL_DEPLOYABLE_ROUTE_HORIZON",
        "source_kind": "LIVE_CARLA_HD_MAP_LOCAL_TOPOLOGY",
        "privileged": False,
        "source_frame_id": history_row.get("source_frame_id"),
        "source_observation_id": str(history_row.get("source_observation_id") or ""),
        "simulation_time_s": float(now_s),
        "route_version": base._runtime_route_version,
        "environment_digest": base._runtime_environment_digest,
        "required_ordinal": ordinal,
        "qualifying_opportunities": values,
        "local_horizon_end_progress_m": min(
            LOCAL_HORIZON_M,
            sum(
                ((route_rows[index][0] - route_rows[index - 1][0]) ** 2 +
                 (route_rows[index][1] - route_rows[index - 1][1]) ** 2) ** 0.5
                for index in range(1, len(route_rows))
            ),
        ),
        "topology_boundary_id": boundary,
    }


def _safety_context(history_row: Mapping[str, Any]) -> tuple[str, Optional[str]]:
    m2b = history_row.get("m2b_inputs", {})
    evidence = m2b.get("evidence", {}) if isinstance(m2b, Mapping) else {}
    lease = evidence.get("shared_action_lease", {}) if isinstance(evidence, Mapping) else {}
    state = {
        "physical": m2b.get("hard_safety_gate") if isinstance(m2b, Mapping) else None,
        "rule": m2b.get("hard_rule_gate") if isinstance(m2b, Mapping) else None,
        "holding": m2b.get("safe_holding_available") if isinstance(m2b, Mapping) else None,
        "lease_valid": lease.get("valid") if isinstance(lease, Mapping) else None,
    }
    return canonical_sha256(state), (
        str(lease.get("lease_id")) if isinstance(lease, Mapping) and lease.get("lease_id") else None
    )


class RQ2TV2NativeEvidenceRuntime:
    """Evidence-only wrapper around the exact existing V1 persistent runtime."""

    enabled = True

    def __init__(self, base: Any, output_dir: str, raw_instruction: str) -> None:
        self.base = base
        self.agent = base.agent
        self.raw_instruction = str(raw_instruction)
        self.output_dir = Path(output_dir)
        self.paired_path = self.output_dir / PAIRED_FILENAME
        self.receipt_path = self.output_dir / RECEIPT_FILENAME
        self.b1 = EvidenceEnabledTemporalMethodV2(use_temporal_memory=False)
        self.b2 = EvidenceEnabledTemporalMethodV2(use_temporal_memory=True)
        self.rows: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self._processed_history_count = 0
        self._previous_context: Optional[dict[str, Any]] = None
        self._construction_counters = _counter_snapshot(base)
        self._validate_environment_firewall()
        self._persist_receipt("ACTIVE")

    def _validate_environment_firewall(self) -> None:
        forbidden = []
        for key in os.environ:
            normalized = key.casefold()
            if normalized in FORBIDDEN_RUNTIME_KEYS or normalized.endswith("_gold") or any(
                token in normalized
                for token in ("true_interpretation", "future_reveal", "authored_reveal", "expected_mechanism_outcome")
            ):
                forbidden.append(key)
        if forbidden:
            raise PermissionError("RQ2_T_V2_ORACLE_ENV_FORBIDDEN:" + ",".join(sorted(forbidden)))

    def _metadata(self) -> dict[str, Any]:
        required = {
            "scene_id": os.environ.get("DRIVECLARIFY_RQ2_T_V2_SCENE_ID"),
            "episode_id": os.environ.get("DRIVECLARIFY_RQ2_T_V2_EPISODE_ID"),
            "seed": os.environ.get("DRIVECLARIFY_RQ2_T_V2_ENGINEERING_SEED"),
            "ambiguity_type": os.environ.get("DRIVECLARIFY_RQ2_T_V2_AMBIGUITY_TYPE"),
            "map_name": os.environ.get("DRIVECLARIFY_RQ2_T_V2_MAP"),
            "route_identity": os.environ.get("DRIVECLARIFY_RQ2_T_V2_ROUTE_IDENTITY"),
            "commitment_certificate_sha256": os.environ.get("DRIVECLARIFY_RQ2_T_V2_COMMITMENT_CERTIFICATE_SHA256"),
        }
        if any(value in (None, "") for value in required.values()):
            raise RuntimeError("RQ2_T_V2_NATIVE_METADATA_INCOMPLETE")
        required["seed"] = int(required["seed"])
        return required

    def _invalidation_events(self, context: Mapping[str, Any]) -> tuple[str, ...]:
        previous = self._previous_context
        events = []
        if previous is not None:
            if previous.get("route_version") != context.get("route_version"):
                events.append("ROUTE_CHANGED")
            if previous.get("environment_digest") != context.get("environment_digest"):
                events.append("ENVIRONMENT_CHANGED")
            if previous.get("topology_boundary_id") != context.get("topology_boundary_id"):
                events.append("TOPOLOGY_BOUNDARY_PASSED")
            if previous.get("actor_binding_digest") != context.get("actor_binding_digest"):
                events.append("TRACK_IDENTITY_CONFLICT")
            if previous.get("safety_state_digest") != context.get("safety_state_digest"):
                events.append("SAFETY_STATE_CHANGED")
            if previous.get("holding_lease_id") != context.get("holding_lease_id"):
                events.append("HOLDING_LEASE_REVOKED")
        self._previous_context = dict(context)
        return tuple(events)

    def _observe_row(self, history_row: Mapping[str, Any]) -> None:
        metadata = self._metadata()
        now_s = float(getattr(self.base, "_latest_simulation_time"))
        b0 = adapt_production_history_row(history_row, simulation_time_s=now_s, **metadata)
        grounding = _grounding_signal(self.base, history_row, now_s)
        topology = _topology_signal(self.base, history_row, now_s)
        lineages = [] if grounding is None else [
            str(row.get("referent_lineage_id") or "")
            for row in grounding.get("candidate_groundings", ())
        ]
        safety_digest, lease_id = _safety_context(history_row)
        topology_boundary = None if topology is None else topology.get("topology_boundary_id")
        signals = {
            "grounding": grounding,
            "topology": topology,
            "route_version": self.base._runtime_route_version,
            "environment_digest": self.base._runtime_environment_digest,
            "topology_boundary_id": topology_boundary,
            "candidate_set_digest": canonical_sha256(tuple(b0.get("interpretation_ids", ()))),
            "instruction_digest": canonical_sha256(self.raw_instruction),
            "safety_state_digest": safety_digest,
            "holding_lease_id": lease_id,
            "actor_binding_digest": canonical_sha256(lineages) if lineages else "UNKNOWN_ACTOR_BINDING",
        }
        assert_runtime_payload_has_no_oracle(signals)
        invalidations = self._invalidation_events(signals)
        before = _counter_snapshot(self.base)
        b1 = self.b1.observe(b0, runtime_signals=signals, history_row=history_row)
        b2 = self.b2.observe(
            b0,
            runtime_signals=signals,
            history_row=history_row,
            invalidation_events=invalidations,
        )
        after = _counter_snapshot(self.base)
        if before != after:
            raise RuntimeError("RQ2_T_V2_OBSERVER_MUTATED_PRODUCTION_COUNTER_OR_PLANNER")
        source_identity = {
            "source_frame_id": b0["source_frame_id"],
            "source_observation_id": b0["source_observation_id"],
            "simulation_time_s": b0["simulation_time_s"],
        }
        if any(
            (row.get("source_frame_id"), row.get("source_observation_id"), row.get("simulation_time_s"))
            != (source_identity["source_frame_id"], source_identity["source_observation_id"], source_identity["simulation_time_s"])
            for row in (b1, b2)
        ):
            raise RuntimeError("RQ2_T_V2_PAIRED_SOURCE_IDENTITY_MISMATCH")
        record = {
            "schema_version": "driveclarify.rq2_t_v2.native_paired_views.v1",
            "episode_id": metadata["episode_id"],
            "scene_id": metadata["scene_id"],
            "source_identity": source_identity,
            "views": {"B0": b0, "B1": b1, "B2": b2},
            "runtime_signals_digest": canonical_sha256(signals),
            "invalidation_events": list(invalidations),
            "fairness_counters_before": before,
            "fairness_counters_after": after,
        }
        record["record_digest"] = canonical_sha256(record)
        self.rows.append(record)
        _atomic_jsonl(self.paired_path, self.rows)
        self._persist_receipt("ACTIVE")

    def _consume_new_history(self) -> None:
        history = getattr(self.base, "_persistent_decision_history", ())
        while self._processed_history_count < len(history):
            row = history[self._processed_history_count]
            self._processed_history_count += 1
            try:
                self._observe_row(row)
            except Exception as error:  # evidence fails closed; driving stays delegated
                self.errors.append(
                    {
                        "history_index": self._processed_history_count - 1,
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                )
                self._persist_receipt("EVIDENCE_FAIL_CLOSED")

    def _persist_receipt(self, status: str) -> None:
        final_counters = _counter_snapshot(self.base)
        value = {
            "schema_version": "driveclarify.rq2_t_v2.native_runtime_receipt.v1",
            "status": status,
            "feature_flag": FEATURE_FLAG,
            "feature_flag_default": "OFF",
            "base_runtime_type": type(self.base).__module__ + "." + type(self.base).__qualname__,
            "wrapper_runtime_type": type(self).__module__ + "." + type(self).__qualname__,
            "provider_owners": {
                "E2": "V2_RUNTIME_CAMERA_TRACK_GROUNDING_PROVIDER",
                "E5": "V2_LIVE_CARLA_LOCAL_TOPOLOGY_PROVIDER",
                "E7": "V2_CURRENT_PHYSICAL_SAFETY_AND_EXISTING_HOLDING_PROVIDER",
            },
            "paired_row_count": len(self.rows),
            "processed_history_count": self._processed_history_count,
            "errors": copy.deepcopy(self.errors),
            "construction_counters": self._construction_counters,
            "latest_production_counters": final_counters,
            "observer_added_vla_forwards": 0,
            "observer_added_candidate_computations": 0,
            "observer_added_pid_instances": 0,
            "observer_control_writes": 0,
            "observer_route_planner_advances": 0,
            "atomic_publication": True,
            "oracle_input_reads": 0,
        }
        value["receipt_digest"] = canonical_sha256(value)
        _atomic_json(self.receipt_path, value)

    def on_tick(self, *args: Any, **kwargs: Any) -> None:
        self.base.on_tick(*args, **kwargs)

    def prepare_model_input(self, model_input: Any) -> Any:
        return self.base.prepare_model_input(model_input)

    def on_model_output(self, *args: Any, **kwargs: Any) -> None:
        self.base.on_model_output(*args, **kwargs)
        self._consume_new_history()

    def select_plan_source(self, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        return self.base.select_plan_source(*args, **kwargs)

    def on_pid_invocation(self, *args: Any, **kwargs: Any) -> None:
        self.base.on_pid_invocation(*args, **kwargs)

    def on_control(self, *args: Any, **kwargs: Any) -> None:
        self.base.on_control(*args, **kwargs)

    def commit(self) -> None:
        self.base.commit()

    def close(self) -> None:
        try:
            self._consume_new_history()
            self.base.close()
        finally:
            self._persist_receipt("CLOSED" if not self.errors else "EVIDENCE_FAIL_CLOSED")

    def summary(self) -> Mapping[str, Any]:
        value = dict(self.base.summary()) if hasattr(self.base, "summary") else {}
        value["rq2_t_v2_native_evidence"] = {
            "enabled": True,
            "paired_row_count": len(self.rows),
            "error_count": len(self.errors),
        }
        return value

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)


def build_rq2_t_v2_native_runtime(agent: Any, raw_instruction: str, output_dir: str) -> Any:
    if not _truthy(os.environ.get(FEATURE_FLAG)):
        raise RuntimeError("RQ2_T_V2_NATIVE_FEATURE_FLAG_OFF")
    from driveclarify_persistent_ambiguity_runtime_v1.runtime import (
        NativeCarlaRouteLocalEvidenceProvider,
        build_persistent_ambiguity_runtime,
    )

    provider = NativeCarlaRouteLocalEvidenceProvider.from_native_agent(agent)
    base = build_persistent_ambiguity_runtime(
        agent,
        str(raw_instruction),
        str(output_dir),
        route_local_evidence_provider=provider,
    )
    # The evidence wrapper never consumes the legacy monolithic receipt during
    # an episode.  Rewriting that multi-megabyte JSON document on every tick is
    # synchronous observer I/O and can dominate simulator time without changing
    # any runtime state.  Keep the in-memory receipt updates intact and use the
    # base runtime's existing atomic close-time publication path.  This switch is
    # deliberately local to the default-off V2 wrapper; it does not enable the
    # RQ2-T 2A owner or alter policy, control, evidence, or planner behavior.
    if hasattr(base, "_defer_full_receipt_io"):
        base._defer_full_receipt_io = True
        base._force_receipt_persist = False
    base._receipt.update(
        {
            "rq2_t_v2_native_factory": "driveclarify_rq2_t_v2.native_runtime.build_rq2_t_v2_native_runtime",
            "rq2_t_v2_native_flag": FEATURE_FLAG,
            "rq2_t_v2_native_flag_default": "OFF",
            "rq2_t_v2_close_time_full_receipt_publication": True,
            "rq2_t_v2_close_time_publication_scope": "EVIDENCE_ONLY_SERIALIZATION",
        }
    )
    base._persist()
    return RQ2TV2NativeEvidenceRuntime(base, output_dir, raw_instruction)


__all__ = [
    "FEATURE_FLAG",
    "LOCAL_HORIZON_M",
    "RQ2TV2NativeEvidenceRuntime",
    "build_rq2_t_v2_native_runtime",
    "parse_required_ordinal",
]
