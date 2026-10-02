#!/usr/bin/env python3
"""Frozen, one-shot post-seal analysis for formal RQ2-T Experiment 2A.

This file is included in the pre-exposure source freeze.  It reads only the
64 frozen 2A cells and certified, post-episode gold.  It never launches CARLA
and never exposes any value to the online owner or observer.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from patsy import bs  # noqa: F401,E402  (made available to formula evaluation)
from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/driveclarify_rq2_t_formal_experiment_2a_v2"
CERT_ROOT = ROOT / "reports/driveclarify_rq2_t_scene_certification_and_tfixed_calibration_v1"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driveclarify_rq2_t.gold import (  # noqa: E402
    ANALYSIS_CONTEXT,
    IndependentGoldInputs,
    derive_query_necessity_gold,
)
from driveclarify_rq2_t.measurement import canonical_sha256  # noqa: E402
from driveclarify_rq2_t.types import EVIDENCE_FIELD_IDS  # noqa: E402


FAMILIES = ("REFERENTIAL", "LANDMARK", "ORDER", "UNDERSPECIFIED_CONSTRAINT")
H2_CATEGORIES = (
    "WINDOW_OBSERVED",
    "NO_WINDOW_EVIDENCE_TOO_LATE",
    "NO_WINDOW_EVIDENCE_NEVER_SUFFICIENT",
    "RIGHT_CENSORED",
    "COMMITMENT_NOT_REACHED",
    "INVALID_ENGINEERING_EVIDENCE",
)
TTCMT_BINS = (">4", "3-4", "2-3", "1-2", "0-1")
BOOTSTRAP_REPLICATES = 10_000
# Prospectively fixed in source; this is an analysis RNG identity, never a
# scientific, calibration, engineering, 2B, or TEST episode seed.
BOOTSTRAP_SEED = 0x2A4D1C7B
FIXED_DELTA_S = 0.05
PRIMARY_ANSWER_LATENCY_S = 0.5
ACTION_RESERVE_TICKS = 11
PRIMARY_CONTROL_RESERVE_TICKS = 3
T_FIXED_TTCMT_S = 3.0


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def cell_attempt_dir(cell: Mapping[str, Any], attempt_number: int) -> Path:
    """Resolve the frozen roster path from the repository root exactly once."""
    output_path = Path(str(cell["output_path"]))
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    return output_path / f"attempt_{attempt_number:02d}"


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def ttcmt_bin(value: Any) -> str | None:
    if not finite(value) or value < 0:
        return None
    if value > 4:
        return ">4"
    if value > 3:
        return "3-4"
    if value > 2:
        return "2-3"
    if value > 1:
        return "1-2"
    return "0-1"


def ttcmt_stratum(value: Any) -> str | None:
    if not finite(value) or value < 0:
        return None
    if value >= 3.0:
        return "EARLY_ACTIONABLE"
    if value >= 1.2:
        return "LATE_ACTIONABLE"
    if value > 0:
        return "TOO_LATE_NONACTIONABLE"
    return "COMMITMENT"


def certified_gold(scene: str) -> dict[str, Any]:
    cert_path = CERT_ROOT / "SCENE_CERTIFICATES" / f"{scene}.json"
    cert = load(cert_path)
    inputs = IndependentGoldInputs(
        scene_certificate_id=cert["scene_id"],
        authored_semantic_ambiguity=cert["semantic_ambiguity_certificate"]["status"] == "PASS",
        reasonable_interpretation_ids=tuple(cert["reasonable_interpretation_set"]),
        true_passenger_intent_id=cert["true_passenger_intent_representation"]["intent_id"],
        exact_map_route_topology_certified=(
            cert["topology_certificate"]["status"] == "PASS"
            and cert["planning_relevance_certificate"]["status"] == "PASS"
        ),
        consequence_truth_by_interpretation={
            key: canonical_sha256(value)
            for key, value in cert["interpretation_specific_planning_consequences"].items()
        },
        clarification_materially_changes_intended_execution=True,
        provenance_digests=(
            cert["semantic_ambiguity_certificate"]["provenance_sha256"],
            cert["topology_certificate"]["provenance_sha256"],
        ),
    )
    gold = derive_query_necessity_gold(inputs, execution_context=ANALYSIS_CONTEXT)
    if gold["label"] != cert["query_necessity_gold_provenance"]["expected_label"]:
        raise RuntimeError("INDEPENDENT_GOLD_CERTIFICATE_DISAGREEMENT:" + scene)
    return gold


def collect() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    roster = load(REPORT / "RQ2_T_2A_FORMAL_ROSTER.json")
    exposure = load(REPORT / "SCIENTIFIC_EXPOSURE_REGISTRY.json")
    if roster.get("planned_cell_count") != 64:
        raise RuntimeError("ANALYSIS_REQUIRES_EXACTLY_64_FROZEN_CELLS")
    episode_rows: list[dict[str, Any]] = []
    observation_rows: list[dict[str, Any]] = []
    gold_by_scene = {scene: certified_gold(scene) for scene in sorted({c["scene_key"] for c in roster["cells"]})}
    for cell in roster["cells"]:
        state = exposure["cells"][cell["cell_id"]]
        attempt_number = int(state.get("attempt", 0))
        attempt_dir = cell_attempt_dir(cell, attempt_number)
        result_path = attempt_dir / "RQ2_T_2A_ATTEMPT_RESULT.json"
        result = load(result_path) if result_path.is_file() else {}
        summary = result.get("episode_summary") or {}
        valid = state.get("state") == "TERMINATED_VALID" and result.get("status") == "PASS_VALID_EPISODE"
        family = cell["ambiguity_family"]
        category = summary.get("h2_primary_category") if valid else "INVALID_ENGINEERING_EVIDENCE"
        if category not in H2_CATEGORIES:
            raise RuntimeError("UNKNOWN_H2_CATEGORY:" + str(category))
        first_suff = summary.get("first_epistemic_sufficient_time_simulation_s")
        commitment = summary.get("commitment_time_simulation_s")
        first_suff_ttcmt = (
            float(commitment) - float(first_suff)
            if finite(commitment) and finite(first_suff)
            else None
        )
        temporal_path = attempt_dir / "rq2_t_temporal" / "TEMPORAL_EVIDENCE.jsonl"
        temporal = rows(temporal_path) if temporal_path.is_file() else []
        first_full = None
        first_available: dict[str, float | None] = {field: None for field in EVIDENCE_FIELD_IDS}
        first_time = min((r["simulation_time_s"] for r in temporal), default=None)
        last_time = max((r["simulation_time_s"] for r in temporal), default=None)
        per_field_states: dict[str, set[str]] = {field: set() for field in EVIDENCE_FIELD_IDS}
        for row in temporal:
            sim_time = row.get("simulation_time_s")
            if row.get("FullEvidenceAvailable") is True and first_full is None:
                first_full = sim_time
            for field in EVIDENCE_FIELD_IDS:
                evidence = row.get("evidence_vector", {}).get(field, {})
                status = evidence.get("status")
                if status == "AVAILABLE" and first_available[field] is None:
                    first_available[field] = sim_time
                if row.get("commitment_state") != "COMMITTED":
                    per_field_states[field].add(status + ":" + canonical_sha256(evidence.get("value")))
                observation_rows.append(
                    {
                        "episode_id": cell["cell_id"],
                        "scene": cell["scene_key"],
                        "family": family,
                        "seed_id": cell["seed_id"],
                        "seed": cell["seed"],
                        "simulation_time_s": sim_time,
                        "carla_frame": evidence.get("source_frame_id"),
                        "source_observation_id": evidence.get("source_observation_id"),
                        "ttcmt_s": row.get("TTCmt_s"),
                        "ttcmt_bin": ttcmt_bin(row.get("TTCmt_s")),
                        "commitment_state": row.get("commitment_state"),
                        "field": field,
                        "status": status,
                        "available": status == "AVAILABLE",
                        "unknown": status == "UNKNOWN",
                        "epistemic_sufficient": row.get("EpistemicEvidenceSufficient") is True,
                        "full_evidence": row.get("FullEvidenceAvailable") is True,
                        "clarification_actionable": row.get("ClarificationActionable") is True,
                    }
                )
        terminal_elapsed = result.get("terminal_event", {}).get("simulation_elapsed_s")
        duration = (
            max(0.0, float(last_time) - float(first_time))
            if finite(first_time) and finite(last_time)
            else None
        )
        episode_rows.append(
            {
                "episode_id": cell["cell_id"],
                "scene": cell["scene_key"],
                "scene_id": cell["scene_id"],
                "family": family,
                "seed_id": cell["seed_id"],
                "seed": cell["seed"],
                "execution_order": cell["execution_order"],
                "attempt": attempt_number,
                "valid": valid,
                "denominator_eligible": bool(summary.get("denominator_eligible")) if valid else False,
                "terminal_state": result.get("terminal_event", {}).get("state"),
                "terminal_elapsed_simulation_s": terminal_elapsed,
                "commitment_observed": bool(summary.get("commitment_observed")) if valid else False,
                "commitment_time_simulation_s": commitment,
                "h1_censoring_status": summary.get("h1_censoring_status"),
                "h2_category": category,
                "observation_count": len(temporal),
                "observation_entry_simulation_s": first_time,
                "observation_exit_simulation_s": last_time,
                "observation_duration_simulation_s": duration,
                "epistemic_evidence_ever_sufficient": bool(summary.get("epistemic_evidence_ever_sufficient")) if valid else False,
                "first_epistemic_sufficient_time_simulation_s": first_suff,
                "first_sufficiency_ttcmt_s": first_suff_ttcmt,
                "first_sufficiency_ttcmt_stratum": ttcmt_stratum(first_suff_ttcmt),
                "full_evidence_ever_available": bool(summary.get("full_evidence_ever_available")) if valid else False,
                "first_full_evidence_time_simulation_s": first_full,
                "window_duration_simulation_s": summary.get("opportunity_window_duration_s"),
                "query_necessity_gold": gold_by_scene[cell["scene_key"]]["label"],
                "gold_digest": gold_by_scene[cell["scene_key"]]["gold_digest"],
                "precommitment_evidence_transition": any(len(values) > 1 for values in per_field_states.values()),
                "first_availability_simulation_s": first_available,
                "observer_added_model_forwards": result.get("owner_receipt", {}).get("owner_induced_model_forward_count"),
                "observer_control_writes": result.get("owner_receipt", {}).get("observer_control_write_count"),
                "observer_pid_instances": result.get("owner_receipt", {}).get("owner_induced_pid_count"),
                "observer_route_planner_advances": result.get("owner_receipt", {}).get("owner_induced_route_planner_advance_count"),
                "normal_model_forwards": result.get("owner_receipt", {}).get("normal_forward_observation_count"),
                "wall_duration_s": result.get("runtime", {}).get("duration_wall_seconds"),
                "integrity": result.get("integrity", {}),
                "attempt_digest": result.get("attempt_digest"),
            }
        )
    gold_receipt = {
        "schema_version": "driveclarify.rq2_t.formal_2a.independent_gold.v2",
        "created_post_episode_only": True,
        "runtime_readable": False,
        "scenes": gold_by_scene,
    }
    gold_receipt["payload_digest"] = canonical_sha256(gold_receipt)
    atomic_json(REPORT / "INDEPENDENT_QUERY_NECESSITY_GOLD.json", gold_receipt)
    return episode_rows, observation_rows, roster


def proportion_ci(success: np.ndarray, total: np.ndarray, rng: np.random.Generator) -> tuple[float | None, float | None]:
    mask = total > 0
    if not mask.any():
        return None, None
    values = success[mask] / total[mask]
    return float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))


def bootstrap_indices(episodes: Sequence[Mapping[str, Any]]) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    result = {}
    for family in FAMILIES:
        indices = np.array([index for index, row in enumerate(episodes) if row["family"] == family and row["valid"]], dtype=int)
        result[family] = rng.choice(indices, size=(BOOTSTRAP_REPLICATES, len(indices)), replace=True)
    return result


def bootstrap_availability(
    episodes: list[dict[str, Any]], observations: list[dict[str, Any]], indices: Mapping[str, np.ndarray]
) -> dict[str, Any]:
    position = {row["episode_id"]: index for index, row in enumerate(episodes)}
    aggregate: dict[tuple[str, str], dict[int, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for row in observations:
        if row["ttcmt_bin"] is None:
            continue
        cell = aggregate[(row["field"], row["ttcmt_bin"])][position[row["episode_id"]]]
        cell[0] += int(row["available"])
        cell[1] += 1
    output: dict[str, Any] = {}
    for field in EVIDENCE_FIELD_IDS:
        output[field] = {}
        for bin_name in TTCMT_BINS:
            num = np.zeros(len(episodes), dtype=float)
            den = np.zeros(len(episodes), dtype=float)
            for episode_index, values in aggregate[(field, bin_name)].items():
                num[episode_index], den[episode_index] = values
            family_rows = {}
            all_num = np.zeros(BOOTSTRAP_REPLICATES)
            all_den = np.zeros(BOOTSTRAP_REPLICATES)
            for family in FAMILIES:
                sampled = indices[family]
                boot_num = num[sampled].sum(axis=1)
                boot_den = den[sampled].sum(axis=1)
                all_num += boot_num
                all_den += boot_den
                low, high = proportion_ci(boot_num, boot_den, np.random.default_rng(0))
                observed_den = den[[i for i, e in enumerate(episodes) if e["family"] == family and e["valid"]]].sum()
                observed_num = num[[i for i, e in enumerate(episodes) if e["family"] == family and e["valid"]]].sum()
                family_rows[family] = {
                    "available_frames": int(observed_num),
                    "eligible_frames": int(observed_den),
                    "proportion": float(observed_num / observed_den) if observed_den else None,
                    "bootstrap_95_ci": [low, high],
                }
            low, high = proportion_ci(all_num, all_den, np.random.default_rng(0))
            observed_num, observed_den = num.sum(), den.sum()
            output[field][bin_name] = {
                "overall": {
                    "available_frames": int(observed_num),
                    "eligible_frames": int(observed_den),
                    "proportion": float(observed_num / observed_den) if observed_den else None,
                    "bootstrap_95_ci": [low, high],
                },
                "by_family": family_rows,
            }
    return output


def fit_gam(observations: list[dict[str, Any]]) -> dict[str, Any]:
    columns = (
        "episode_id",
        "family",
        "ttcmt_s",
        "field",
        "status",
        "available",
    )
    frame = pd.DataFrame(
        (
            row
            for row in observations
            if finite(row["ttcmt_s"])
            and row["status"] in {"AVAILABLE", "UNKNOWN"}
        ),
        columns=columns,
    )
    result: dict[str, Any] = {}
    formula = "available ~ bs(ttcmt_s, df=4, degree=3, include_intercept=False) * C(family)"
    for field in EVIDENCE_FIELD_IDS:
        data = frame[frame["field"] == field].copy()
        data["available"] = data["available"].astype(int)
        if data.empty or data["available"].nunique() < 2:
            result[field] = {
                "status": "NOT_IDENTIFIABLE_CONSTANT_RESPONSE",
                "response_values": sorted(data["available"].unique().tolist()),
                "frame_count": len(data),
                "episode_count": data["episode_id"].nunique(),
                "formula": formula,
            }
            continue
        try:
            model = BinomialBayesMixedGLM.from_formula(
                formula,
                {"episode_random_intercept": "0 + C(episode_id)"},
                data,
            )
            fitted = model.fit_vb()
            names = list(model.exog_names)
            means = np.asarray(fitted.fe_mean)
            sds = np.asarray(fitted.fe_sd)
            result[field] = {
                "status": "FIT_COMPLETE",
                "model": "BINOMIAL_4_DF_CUBIC_SPLINE_FAMILY_INTERACTION_EPISODE_RANDOM_INTERCEPT",
                "formula": formula,
                "frame_count": len(data),
                "episode_count": data["episode_id"].nunique(),
                "fixed_effects": {
                    name: {
                        "posterior_mean_log_odds": float(mean),
                        "posterior_sd": float(sd),
                        "approximate_95_interval": [float(mean - 1.96 * sd), float(mean + 1.96 * sd)],
                    }
                    for name, mean, sd in zip(names, means, sds)
                },
                "random_intercept_log_sd_posterior_mean": float(fitted.vcp_mean[0]),
                "family_interaction_terms": [name for name in names if ":" in name],
            }
        except Exception as exc:
            result[field] = {
                "status": "MODEL_NOT_IDENTIFIABLE",
                "formula": formula,
                "frame_count": len(data),
                "episode_count": data["episode_id"].nunique(),
                "error": type(exc).__name__ + ":" + str(exc),
            }
    return result


def km_curve(durations: Iterable[float], events: Iterable[bool]) -> dict[str, Any]:
    pairs = sorted(zip(durations, events), key=lambda pair: pair[0])
    event_times = sorted({time for time, event in pairs if event})
    survival = 1.0
    curve = [{"time_from_entry_s": 0.0, "survival_no_event": 1.0, "at_risk": len(pairs), "events": 0}]
    median = None
    for time in event_times:
        at_risk = sum(t >= time for t, _ in pairs)
        observed = sum(t == time and event for t, event in pairs)
        if at_risk:
            survival *= 1.0 - observed / at_risk
        curve.append({"time_from_entry_s": time, "survival_no_event": survival, "at_risk": at_risk, "events": observed})
        if median is None and survival <= 0.5:
            median = time
    return {
        "n": len(pairs),
        "events": sum(event for _, event in pairs),
        "right_censored": sum(not event for _, event in pairs),
        "median_time_from_entry_s": median,
        "curve": curve,
    }


def first_event_results(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    def one(subset: list[dict[str, Any]], field: str | None) -> dict[str, Any]:
        durations, events = [], []
        for episode in subset:
            entry = episode["observation_entry_simulation_s"]
            exit_time = episode["observation_exit_simulation_s"]
            event_time = (
                episode["first_epistemic_sufficient_time_simulation_s"]
                if field is None
                else episode["first_availability_simulation_s"][field]
            )
            if not finite(entry) or not finite(exit_time):
                continue
            events.append(finite(event_time))
            durations.append(float(event_time if finite(event_time) else exit_time) - float(entry))
        return km_curve(durations, events)

    valid = [row for row in episodes if row["valid"]]
    output = {"first_epistemic_sufficiency": {"overall": one(valid, None), "by_family": {}}}
    for family in FAMILIES:
        output["first_epistemic_sufficiency"]["by_family"][family] = one([r for r in valid if r["family"] == family], None)
    output["first_field_availability"] = {}
    for field in EVIDENCE_FIELD_IDS:
        output["first_field_availability"][field] = {
            "overall": one(valid, field),
            "by_family": {family: one([r for r in valid if r["family"] == family], field) for family in FAMILIES},
        }
    return output


def h2_results(episodes: list[dict[str, Any]], indices: Mapping[str, np.ndarray]) -> dict[str, Any]:
    valid = [row for row in episodes if row["valid"]]
    output = {"overall": {}, "by_family": {family: {} for family in FAMILIES}}
    for category in H2_CATEGORIES:
        indicator = np.array([row["h2_category"] == category for row in episodes], dtype=float)
        overall_boot = np.zeros(BOOTSTRAP_REPLICATES)
        overall_den = np.zeros(BOOTSTRAP_REPLICATES)
        for family in FAMILIES:
            sampled = indices[family]
            boot_count = indicator[sampled].sum(axis=1)
            boot_den = np.full(BOOTSTRAP_REPLICATES, sampled.shape[1], dtype=float)
            overall_boot += boot_count
            overall_den += boot_den
            family_rows = [row for row in valid if row["family"] == family]
            count = sum(row["h2_category"] == category for row in family_rows)
            low, high = proportion_ci(boot_count, boot_den, np.random.default_rng(0))
            output["by_family"][family][category] = {
                "count": count,
                "denominator": len(family_rows),
                "proportion": count / len(family_rows) if family_rows else None,
                "bootstrap_95_ci": [low, high],
            }
        count = sum(row["h2_category"] == category for row in valid)
        low, high = proportion_ci(overall_boot, overall_den, np.random.default_rng(0))
        output["overall"][category] = {
            "count": count,
            "denominator": len(valid),
            "proportion": count / len(valid) if valid else None,
            "bootstrap_95_ci": [low, high],
        }
    durations = [row["window_duration_simulation_s"] for row in valid if row["h2_category"] == "WINDOW_OBSERVED"]
    output["window_duration_only_among_observed"] = {
        "n": len(durations),
        "mean_s": float(np.mean(durations)) if durations else None,
        "median_s": float(np.median(durations)) if durations else None,
        "minimum_s": float(np.min(durations)) if durations else None,
        "maximum_s": float(np.max(durations)) if durations else None,
        "zero_imputation": False,
    }
    return output


def sensitivity_results(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in episodes if row["valid"]]
    output = []
    for latency in (0.5, 1.0, 2.0, 3.0):
        for reserve_ticks in (2, 3, 4):
            reserve_s = latency + ACTION_RESERVE_TICKS * FIXED_DELTA_S + reserve_ticks * FIXED_DELTA_S
            counts = Counter()
            durations = []
            for row in valid:
                suff = row["first_epistemic_sufficient_time_simulation_s"]
                commitment = row["commitment_time_simulation_s"]
                if not row["commitment_observed"]:
                    category = row["h2_category"]
                elif not finite(suff):
                    category = "NO_WINDOW_EVIDENCE_NEVER_SUFFICIENT"
                elif float(suff) <= float(commitment) - reserve_s + 1e-9:
                    category = "WINDOW_OBSERVED"
                    durations.append(float(commitment) - reserve_s - float(suff))
                else:
                    category = "NO_WINDOW_EVIDENCE_TOO_LATE"
                counts[category] += 1
            output.append(
                {
                    "answer_latency_s": latency,
                    "action_reserve_ticks": ACTION_RESERVE_TICKS,
                    "control_reserve_ticks": reserve_ticks,
                    "total_reserve_s": reserve_s,
                    "denominator": len(valid),
                    "window_observed": counts["WINDOW_OBSERVED"],
                    "evidence_too_late": counts["NO_WINDOW_EVIDENCE_TOO_LATE"],
                    "evidence_never_sufficient": counts["NO_WINDOW_EVIDENCE_NEVER_SUFFICIENT"],
                    "missed_opportunity": counts["NO_WINDOW_EVIDENCE_TOO_LATE"],
                    "mean_window_duration_s": float(np.mean(durations)) if durations else None,
                    "median_window_duration_s": float(np.median(durations)) if durations else None,
                    "native_reruns": 0,
                }
            )
    return {
        "schema_version": "driveclarify.rq2_t.formal_2a.same_trace_sensitivity.v2",
        "primary_condition": {"answer_latency_s": 0.5, "control_reserve_ticks": 3},
        "same_trace_only": True,
        "conditions": output,
    }


def validity_and_gate(
    episodes: list[dict[str, Any]], observations: list[dict[str, Any]], h2: Mapping[str, Any], availability: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    valid = [row for row in episodes if row["valid"]]
    by_scene = Counter(row["scene"] for row in valid)
    by_family = Counter(row["family"] for row in valid)
    validity_checks = {
        "valid_episode_count_at_least_48": len(valid) >= 48,
        "validity_rate_at_least_0_80": len(valid) / 64 >= 0.80,
        "at_least_6_valid_per_scene": all(by_scene[scene] >= 6 for scene in {r["scene"] for r in episodes}),
        "at_least_2_represented_scenes_per_family": all(
            len({r["scene"] for r in valid if r["family"] == family}) >= 2 for family in FAMILIES
        ),
    }
    validity = {
        "status": "PASS" if all(validity_checks.values()) else "FAIL",
        "checks": validity_checks,
        "valid_episodes": len(valid),
        "planned_episodes": 64,
        "validity_rate": len(valid) / 64,
        "valid_by_scene": dict(sorted(by_scene.items())),
        "valid_by_family": dict(sorted(by_family.items())),
    }
    transition_rate = sum(r["precommitment_evidence_transition"] for r in valid) / len(valid) if valid else 0
    nonconstant_families = []
    for family in FAMILIES:
        nonconstant = False
        for field in EVIDENCE_FIELD_IDS:
            values = [
                availability[field][bin_name]["by_family"][family]["proportion"]
                for bin_name in TTCMT_BINS
            ]
            values = [v for v in values if v is not None]
            if len(values) >= 2 and max(values) - min(values) > 0:
                nonconstant = True
        if nonconstant:
            nonconstant_families.append(family)
    eligible = [
        r for r in valid
        if r["query_necessity_gold"] == "QUERY_NECESSARY" and finite(r["first_sufficiency_ttcmt_s"])
    ]
    strata = Counter(r["first_sufficiency_ttcmt_stratum"] for r in eligible)
    early_prop = strata["EARLY_ACTIONABLE"] / len(eligible) if eligible else 0
    late_prop = strata["LATE_ACTIONABLE"] / len(eligible) if eligible else 0
    paired = [
        {
            "episode_id": r["episode_id"],
            "t_accum_offline_trigger_ttcmt_s": r["first_sufficiency_ttcmt_s"],
            "t_fixed_trigger_ttcmt_s": T_FIXED_TTCMT_S,
            "absolute_difference_ticks": abs(r["first_sufficiency_ttcmt_s"] - T_FIXED_TTCMT_S) / FIXED_DELTA_S,
        }
        for r in eligible
    ]
    separated = sum(r["absolute_difference_ticks"] >= 5 - 1e-9 for r in paired)
    separated_prop = separated / len(paired) if paired else 0
    observer_failures = [
        r for r in episodes
        if not r["valid"] or r["observation_count"] == 0 or any(
            (r[key] not in (0, None)) for key in (
                "observer_added_model_forwards", "observer_control_writes", "observer_pid_instances", "observer_route_planner_advances"
            )
        )
    ]
    family_failure_rates = {
        family: sum(r in observer_failures for r in episodes if r["family"] == family)
        / sum(r["family"] == family for r in episodes)
        for family in FAMILIES
    }
    category_integrity = (
        sum(h2["overall"][category]["count"] for category in H2_CATEGORIES) == len(valid)
        and all(
            r["window_duration_simulation_s"] is None
            for r in valid if r["h2_category"] != "WINDOW_OBSERVED"
        )
    )
    checks = {
        "primary_analysis_validity": validity["status"] == "PASS",
        "observer_timestamp_oracle_failures_below_10pct_overall": len(observer_failures) / 64 < 0.10,
        "observer_timestamp_oracle_failures_below_20pct_every_family": all(rate < 0.20 for rate in family_failure_rates.values()),
        "temporal_dynamics_identifiable": transition_rate >= 0.25 or len(nonconstant_families) >= 2,
        "timing_identifiability": (early_prop >= 0.15 and late_prop >= 0.15) or separated_prop >= 0.15,
        "categories_estimable_without_zero_imputation": category_integrity,
    }
    gate = {
        "schema_version": "driveclarify.rq2_t.formal_2a.to_2b_gate.v2",
        "accepted_gate_contract_digest": "985d3f6a6669d5888b9aa31f74a2216b70ccef47471f43b4dae0207abdfc14a1",
        "operator_across_required_criteria": "AND",
        "checks": checks,
        "temporal_dynamics": {
            "episodes_with_transition": sum(r["precommitment_evidence_transition"] for r in valid),
            "denominator": len(valid),
            "proportion": transition_rate,
            "nonconstant_prespecified_curve_families": nonconstant_families,
        },
        "timing": {
            "denominator_definition": "VALID_INDEPENDENTLY_QUERY_NECESSARY_WITH_ESTIMABLE_FIRST_SUFFICIENCY_TTCMT",
            "denominator": len(eligible),
            "early_actionable": strata["EARLY_ACTIONABLE"],
            "early_proportion": early_prop,
            "late_actionable": strata["LATE_ACTIONABLE"],
            "late_proportion": late_prop,
            "too_late_diagnostic": strata["TOO_LATE_NONACTIONABLE"],
            "too_late_may_substitute": False,
            "arm_a_pass": early_prop >= 0.15 and late_prop >= 0.15,
            "arm_b_paired_offline_definition": "FIRST_EPISTEMIC_SUFFICIENCY_TTCMT_VS_FROZEN_T_FIXED_3_0S",
            "paired_episode_rows": paired,
            "paired_difference_at_least_5_ticks": separated,
            "paired_difference_proportion": separated_prop,
            "arm_b_pass": separated_prop >= 0.15,
        },
        "failure_rates": {"overall": len(observer_failures) / 64, "by_family": family_failure_rates},
        "forbidden_inputs_read": [],
        "experiment_2b_executed": False,
    }
    gate["2A_TO_2B_GATE"] = "PASS" if all(checks.values()) else "FAIL"
    gate["payload_digest"] = canonical_sha256(gate)
    strata_output = {
        "schema_version": "driveclarify.rq2_t.formal_2a.ttcmt_strata.v2",
        "denominator": len(eligible),
        "counts": dict(strata),
        "episodes": [
            {key: row[key] for key in ("episode_id", "scene", "family", "first_sufficiency_ttcmt_s", "first_sufficiency_ttcmt_stratum")}
            for row in eligible
        ],
    }
    return validity, gate, strata_output


def write_episode_tables(episodes: list[dict[str, Any]]) -> None:
    atomic_json(REPORT / "EPISODE_LEVEL_PRIMARY_TABLE.json", episodes)
    flat = []
    for row in episodes:
        item = {key: value for key, value in row.items() if not isinstance(value, (dict, list))}
        for field, value in row["first_availability_simulation_s"].items():
            item["first_available_" + field] = value
        flat.append(item)
    with (REPORT / "EPISODE_LEVEL_PRIMARY_TABLE.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)


def figures(
    episodes: list[dict[str, Any]], availability: Mapping[str, Any], h2: Mapping[str, Any], sensitivity: Mapping[str, Any]
) -> None:
    directory = REPORT / "FIGURES"
    directory.mkdir(parents=True, exist_ok=True)
    x = np.arange(len(TTCMT_BINS))
    fig, ax = plt.subplots(figsize=(10, 6))
    for field in EVIDENCE_FIELD_IDS:
        y = [availability[field][b]["overall"]["proportion"] for b in TTCMT_BINS]
        ax.plot(x, y, marker="o", label=field.split("_", 1)[0])
    ax.set(xticks=x, xticklabels=TTCMT_BINS, ylim=(-0.03, 1.03), xlabel="TTCmt bin (s)", ylabel="AVAILABLE proportion")
    ax.legend(ncol=3)
    fig.tight_layout(); fig.savefig(directory / "A_E1_E9_AVAILABILITY_VS_TTCMT.png", dpi=180); plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), sharex=True, sharey=True)
    for ax, family in zip(axes.ravel(), FAMILIES):
        for field in EVIDENCE_FIELD_IDS:
            ax.plot(x, [availability[field][b]["by_family"][family]["proportion"] for b in TTCMT_BINS], marker=".", label=field.split("_", 1)[0])
        ax.set_title(family); ax.set_xticks(x, TTCMT_BINS); ax.set_ylim(-0.03, 1.03)
    axes[0, 0].legend(ncol=3, fontsize=7); fig.tight_layout(); fig.savefig(directory / "B_FAMILY_EVIDENCE_DYNAMICS.png", dpi=180); plt.close(fig)
    valid = sorted((r for r in episodes if r["valid"]), key=lambda r: r["execution_order"])
    fig, ax = plt.subplots(figsize=(12, 13))
    for y, row in enumerate(valid):
        commitment = row["commitment_time_simulation_s"]
        entry = row["observation_entry_simulation_s"]
        exit_time = row["observation_exit_simulation_s"]
        ax.plot([entry, exit_time], [y, y], color="0.75")
        if finite(commitment): ax.scatter([commitment], [y], color="black", s=8)
        if finite(row["first_epistemic_sufficient_time_simulation_s"]): ax.scatter([row["first_epistemic_sufficient_time_simulation_s"]], [y], color="#0072B2", s=12)
        if finite(commitment): ax.scatter([commitment - 1.2], [y], color="#D55E00", marker="|", s=22)
    ax.set(xlabel="CARLA simulation time (s)", ylabel="Episode (execution order)")
    fig.tight_layout(); fig.savefig(directory / "C_EPISODE_TIMELINES.png", dpi=180); plt.close(fig)
    counts = np.array([[h2["by_family"][f][c]["count"] for c in H2_CATEGORIES] for f in FAMILIES])
    fig, ax = plt.subplots(figsize=(12, 6)); bottom = np.zeros(len(FAMILIES))
    for i, category in enumerate(H2_CATEGORIES):
        ax.bar(FAMILIES, counts[:, i], bottom=bottom, label=category); bottom += counts[:, i]
    ax.tick_params(axis="x", rotation=15); ax.set_ylabel("Episodes"); ax.legend(fontsize=7)
    fig.tight_layout(); fig.savefig(directory / "D_H2_BY_FAMILY.png", dpi=180); plt.close(fig)
    durations = [r["window_duration_simulation_s"] for r in valid if r["h2_category"] == "WINDOW_OBSERVED"]
    fig, ax = plt.subplots(figsize=(7, 5))
    if durations: ax.hist(durations, bins=min(10, len(durations)))
    else: ax.text(0.5, 0.5, "No WINDOW_OBSERVED episodes", ha="center", va="center", transform=ax.transAxes)
    ax.set(xlabel="Observed window duration (s)", ylabel="Episodes")
    fig.tight_layout(); fig.savefig(directory / "E_WINDOW_DURATION.png", dpi=180); plt.close(fig)
    primary_reserve = [r for r in sensitivity["conditions"] if r["control_reserve_ticks"] == 3]
    fig, ax = plt.subplots(figsize=(7, 5)); ax.plot([r["answer_latency_s"] for r in primary_reserve], [r["window_observed"] / r["denominator"] for r in primary_reserve], marker="o", label="WINDOW_OBSERVED"); ax.plot([r["answer_latency_s"] for r in primary_reserve], [r["evidence_too_late"] / r["denominator"] for r in primary_reserve], marker="o", label="TOO_LATE")
    ax.set(xlabel="Answer latency (simulation s)", ylabel="Episode proportion", ylim=(-0.03, 1.03)); ax.legend()
    fig.tight_layout(); fig.savefig(directory / "F_ANSWER_LATENCY_SENSITIVITY.png", dpi=180); plt.close(fig)
    strata = Counter(r["first_sufficiency_ttcmt_stratum"] for r in valid if finite(r["first_sufficiency_ttcmt_s"]))
    fig, ax = plt.subplots(figsize=(8, 5)); labels = ("EARLY_ACTIONABLE", "LATE_ACTIONABLE", "TOO_LATE_NONACTIONABLE", "COMMITMENT"); ax.bar(labels, [strata[label] for label in labels]); ax.tick_params(axis="x", rotation=15); ax.set_ylabel("Episodes")
    fig.tight_layout(); fig.savefig(directory / "G_TTCMT_STRATA_DIAGNOSTIC.png", dpi=180); plt.close(fig)


def markdown_reports(h1: Mapping[str, Any], h2: Mapping[str, Any], sensitivity: Mapping[str, Any]) -> None:
    fitted = sum(row["status"] == "FIT_COMPLETE" for row in h1["gam"].values())
    nonidentifiable = len(EVIDENCE_FIELD_IDS) - fitted
    (REPORT / "H1_ANALYSIS_REPORT.md").write_text(
        "# H1 analysis\n\n"
        f"The frozen analysis ran once over {h1['valid_episode_count']} valid scene×seed episodes. "
        "Frames were repeated observations, not independent scientific units. "
        f"The fixed binomial 4-df cubic-spline mixed model fit {fitted} E-fields; {nonidentifiable} were explicitly non-identifiable rather than forced. "
        "Family-stratified episode-cluster 95% intervals use exactly 10,000 resamples. "
        "Kaplan–Meier results retain right censoring. See `H1_ANALYSIS_RESULTS.json` for exact curves and coefficients.\n",
        encoding="utf-8",
    )
    counts = ", ".join(f"{key}={value['count']}" for key, value in h2["overall"].items() if key in H2_CATEGORIES)
    (REPORT / "H2_ANALYSIS_REPORT.md").write_text(
        "# H2 analysis\n\n"
        f"Exact primary counts are {counts}. Window duration is summarized only for WINDOW_OBSERVED; every other category retains null duration and no zero imputation. Family denominators and 10,000-resample cluster intervals are in `H2_ANALYSIS_RESULTS.json`.\n",
        encoding="utf-8",
    )
    (REPORT / "LATENCY_SENSITIVITY_REPORT.md").write_text(
        "# Same-trace latency and control-reserve sensitivity\n\n"
        "All 12 combinations (answer latency 0.5/1.0/2.0/3.0 s × control reserve 2/3/4 ticks) were recomputed offline from the same traces. No CARLA episode was added and the primary 0.5-s/3-tick condition was not changed. Exact results are in `LATENCY_SENSITIVITY_RESULTS.json`.\n",
        encoding="utf-8",
    )


def main() -> int:
    exposure = load(REPORT / "SCIENTIFIC_EXPOSURE_REGISTRY.json")
    if exposure.get("status") != "ALL_64_CELLS_TERMINATED_VALID":
        raise RuntimeError("ANALYSIS_FORBIDDEN_UNTIL_ALL_64_CELLS_SEALED")
    if (REPORT / "ANALYSIS_EXECUTION_RECEIPT.json").exists():
        raise RuntimeError("FROZEN_ANALYSIS_ALREADY_EXECUTED_ONCE")
    seed_registry = {
        "schema_version": "driveclarify.rq2_t.formal_2a.analysis_seed.v2",
        "bootstrap_seed": BOOTSTRAP_SEED,
        "role": "POST_EPISODE_FAMILY_STRATIFIED_EPISODE_CLUSTER_BOOTSTRAP_ONLY",
        "replicates": BOOTSTRAP_REPLICATES,
        "forbidden_episode_seed_roles": ["RQ2_T_2A_DEV", "CALIBRATION", "ENGINEERING", "RQ2_T_2B", "TEST"],
        "fixed_prospectively_in_source_freeze": True,
    }
    seed_registry["payload_digest"] = canonical_sha256(seed_registry)
    atomic_json(REPORT / "ANALYSIS_SEED_REGISTRY.json", seed_registry)
    episodes, observations, roster = collect()
    write_episode_tables(episodes)
    boot_indices = bootstrap_indices(episodes)
    availability = bootstrap_availability(episodes, observations, boot_indices)
    gam = fit_gam(observations)
    first_events = first_event_results(episodes)
    h1 = {
        "schema_version": "driveclarify.rq2_t.formal_2a.h1.v2",
        "analysis_executions": 1,
        "scientific_unit": "SCENE_X_SEED_EPISODE",
        "frames_independent_n": False,
        "valid_episode_count": sum(r["valid"] for r in episodes),
        "observation_frame_field_rows": len(observations),
        "frozen_ttcmt_bins": list(TTCMT_BINS),
        "availability_and_unknown_curves": availability,
        "gam": gam,
        "family_interaction_included": True,
        "episode_random_intercept_included": True,
        "first_event_kaplan_meier": first_events,
        "bootstrap": {"replicates": BOOTSTRAP_REPLICATES, "unit": "EPISODE", "stratified_by_family": True, "seed_registry_digest": seed_registry["payload_digest"]},
    }
    h1["payload_digest"] = canonical_sha256(h1)
    atomic_json(REPORT / "H1_ANALYSIS_RESULTS.json", h1)
    h2 = h2_results(episodes, boot_indices)
    h2.update({"schema_version": "driveclarify.rq2_t.formal_2a.h2.v2", "analysis_executions": 1, "bootstrap_replicates": BOOTSTRAP_REPLICATES, "zero_imputation": False})
    h2["payload_digest"] = canonical_sha256(h2)
    atomic_json(REPORT / "H2_ANALYSIS_RESULTS.json", h2)
    sensitivity = sensitivity_results(episodes)
    sensitivity["payload_digest"] = canonical_sha256(sensitivity)
    atomic_json(REPORT / "LATENCY_SENSITIVITY_RESULTS.json", sensitivity)
    validity, gate, strata = validity_and_gate(episodes, observations, h2, availability)
    atomic_json(REPORT / "PRIMARY_2A_VALIDITY_GATE.json", validity)
    atomic_json(REPORT / "TTCMT_STRATA_ANALYSIS.json", strata)
    atomic_json(REPORT / "RQ2_T_2A_TO_2B_GATE_RECEIPT.json", gate)
    figures(episodes, availability, h2, sensitivity)
    markdown_reports(h1, h2, sensitivity)
    receipt = {
        "schema_version": "driveclarify.rq2_t.formal_2a.analysis_execution.v2",
        "status": "PASS_ANALYSIS_EXECUTED_ONCE_AND_SEALED",
        "executed_at_utc": now(),
        "roster_digest": roster["roster_digest"],
        "episode_count": len(episodes),
        "valid_episode_count": sum(r["valid"] for r in episodes),
        "h1_digest": h1["payload_digest"],
        "h2_digest": h2["payload_digest"],
        "sensitivity_digest": sensitivity["payload_digest"],
        "gate_digest": gate["payload_digest"],
        "native_episodes_added_by_analysis": 0,
        "experiment_2b_executed": False,
    }
    receipt["payload_digest"] = canonical_sha256(receipt)
    atomic_json(REPORT / "ANALYSIS_EXECUTION_RECEIPT.json", receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
