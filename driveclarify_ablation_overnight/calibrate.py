"""DEV-only unlabeled distance calibration; no outcomes, labels or truth inputs."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics

from driveclarify_task_relation_ablation_dev.relation import TimedTrajectory, MetricConfig, compare_trajectories


def calibrate(paths):
    if len(paths) != 2:
        raise ValueError('EXACTLY_TWO_PREDECLARED_DEV_INPUTS')
    values = []
    sources = []
    for path in paths:
        raw = Path(path).read_bytes()
        rows = [json.loads(x) for x in raw.splitlines() if x.strip()]
        if len(rows) != 2 or not all(r.get('valid') is True for r in rows):
            raise ValueError('EXACTLY_TWO_VALID_CANDIDATE_FORWARDS_REQUIRED')
        plans = []
        for r in rows:
            t = dict(r['timed_trajectory'])
            t['xy_m'] = tuple(tuple(p) for p in t['xy_m'])
            t['relative_times_s'] = tuple(t['relative_times_s'])
            plans.append(TimedTrajectory(**t))
        result = compare_trajectories(*plans, MetricConfig(sample_period_s=.25,
                                      maximum_source_gap_s=.25, threshold_m=0.0))
        if result.max_aligned_distance_m is None:
            raise ValueError(result.reason)
        values.append(result.max_aligned_distance_m)
        sources.append({'path': str(Path(path).resolve()), 'sha256': hashlib.sha256(raw).hexdigest(),
                        'distance_m': result.max_aligned_distance_m,
                        'source_frame': plans[0].source_frame, 'source_time_s': plans[0].source_time_s,
                        'nonlanguage_context_sha256': plans[0].nonlanguage_context_sha256})
    return {'schema': 'driveclarify.ablation-overnight.dev-threshold.v1',
            'rule': 'MEDIAN_OF_TWO_PREDECLARED_UNLABELED_DEV_MAX_DISTANCES',
            'metric': asdict(MetricConfig(sample_period_s=.25, maximum_source_gap_s=.25,
                                         threshold_m=statistics.median(values))),
            'sources': sources, 'read_task_outcomes': False, 'read_task_relation_labels': False,
            'read_formal_results': False, 'development_only': True,
            'critical_comparison': 'distance_m > threshold_m (strict); equality is equivalent'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--candidate-log', action='append', required=True)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    if a.output.exists():
        raise FileExistsError('CALIBRATION_RECEIPT_MUST_NOT_BE_OVERWRITTEN')
    result = calibrate(a.candidate_log)
    a.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
