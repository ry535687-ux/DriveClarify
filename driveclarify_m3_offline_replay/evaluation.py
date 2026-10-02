"""Post-publication oracle evaluation over immutable replay traces."""

from __future__ import annotations

from driveclarify_m3_minimal_core import canonical_sha256

from .contracts import ReplayEvaluationResult, ReplayOracle, ReplayTrace


def evaluate_trace(immutable_trace: ReplayTrace,
                   oracle: ReplayOracle) -> ReplayEvaluationResult:
    if not isinstance(immutable_trace, ReplayTrace):
        raise TypeError("evaluate_trace requires immutable ReplayTrace")
    if not isinstance(oracle, ReplayOracle):
        raise TypeError("evaluate_trace requires ReplayOracle")
    trace_dict = immutable_trace.to_dict()
    supplied_hash = trace_dict.pop("trace_sha256")
    if canonical_sha256(trace_dict) != supplied_hash:
        return ReplayEvaluationResult.create(
            accepted=False, rejection_category="TRACE_HASH_MISMATCH", metrics={},
            trace_sha256=immutable_trace.trace_sha256, oracle_id=oracle.oracle_id)
    if oracle.episode_id != immutable_trace.episode_id:
        return ReplayEvaluationResult.create(
            accepted=False, rejection_category="ORACLE_EPISODE_MISMATCH", metrics={},
            trace_sha256=immutable_trace.trace_sha256, oracle_id=oracle.oracle_id)
    if oracle.episode_records_sha256 != immutable_trace.episode_records_sha256:
        return ReplayEvaluationResult.create(
            accepted=False, rejection_category="ORACLE_RECORDS_SHA_MISMATCH", metrics={},
            trace_sha256=immutable_trace.trace_sha256, oracle_id=oracle.oracle_id)
    observed = immutable_trace.final_state
    metrics = {
        "transition_agreement": list(immutable_trace.transition_ids) ==
                                list(oracle.expected_transition_ids),
        "final_state_agreement": dict(observed) == dict(oracle.expected_final_state),
        "authority_agreement": observed["authority"] == oracle.expected_authority,
        "query_active_agreement": observed["query_active"] == oracle.expected_query_active,
        "candidate_freshness_agreement": observed["candidate_freshness"] ==
                                         oracle.expected_candidate_freshness,
        "lease_agreement": observed["holding_lease"] == oracle.expected_lease_status,
        "processing_results_agreement": list(immutable_trace.processing_result_sequence) ==
                                        list(oracle.expected_processing_results),
        "forbidden_path_count": sum(
            int(getattr(immutable_trace, path, 0)) for path in oracle.forbidden_paths),
        "partial_mutation_count": immutable_trace.partial_mutation_count,
        "second_query_count": immutable_trace.second_query_count,
        "stale_unknown_act_count": immutable_trace.stale_unknown_act_count,
        "low_level_control_output_count": immutable_trace.low_level_control_output_count,
    }
    return ReplayEvaluationResult.create(
        accepted=all(value is True or value == 0 for value in metrics.values()),
        rejection_category=None, metrics=metrics,
        trace_sha256=immutable_trace.trace_sha256, oracle_id=oracle.oracle_id)
