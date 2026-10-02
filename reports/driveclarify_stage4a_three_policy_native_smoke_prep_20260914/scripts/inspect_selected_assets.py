#!/usr/bin/env python3
"""Read exactly the two stopped STAGE4A candidates; never import native code.

Exit 0 means the bounded evidence extraction completed, not smoke eligibility.
No directory discovery, subprocess, simulator, model, or route construction.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET


CASES = (
    ('REF', 'RQ3V3-P0-REF-CRITICAL-Q02', 'B-REF-CRITICAL-S01'),
    ('LMK', 'RQ3V3-P0-LMK-CRITICAL-Q04', 'B-LMK-CRITICAL-S05'),
)
EXECUTION = 'reports/driveclarify_rq3_v3_bench2drive_closed_loop_formal_execution_v1'
ASSETS = 'reports/driveclarify_rq3_v3_p0_scene_qualification_v1/scene_assets'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inspect(repo, output):
    sources = []
    def read(relative, label, name):
        path = repo / relative
        raw = path.read_bytes()
        destination = output / 'source' / label / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        sources.append({'path': str(path.resolve()), 'bytes': len(raw),
                        'sha256': sha(raw), 'snapshot': str(destination.relative_to(output))})
        return raw

    observations = []
    route_pairs = []
    for family, scene_id, run in CASES:
        cfg = json.loads(read(f'{EXECUTION}/part_b_configs/RQ3V3-{run}.json', family, 'CONFIG.json'))
        assets = {}
        for suffix in ('CANDIDATE-ROUTES.json', 'CENTER-ROUTE.json', 'SCENE.json', 'LAYOUT.json'):
            assets[suffix] = json.loads(read(f'{ASSETS}/{scene_id}-{suffix}', family, suffix))
        xml = ET.fromstring(read(f'{ASSETS}/{scene_id}-ROUTE.xml', family, 'ROUTE.xml'))
        base = f'{EXECUTION}/part_b_runs/{run}/attempt_01'
        owner = base + '/owner_evidence'
        receipts = {}
        for name in ('V11_SUPERVISION_RECEIPT.json', 'RQ1_V2_FULL_REPLAN_RECEIPT.json',
                     'ONLINE_ROUTE_INSTALL_RECEIPT.json', 'RQ3_LIFECYCLE_TIMING_RECEIPT.json',
                     'POST_SWITCH_PLAN_ACCEPTANCE.json', 'NATIVE_NAVIGATION_INPUT_CONTRACT.json'):
            receipts[name] = json.loads(read(f'{owner}/{name}', family, name))
        official = json.loads(read(f'{base}/official_checkpoint.json', family, 'official_checkpoint.json'))
        answer = json.loads(read(f'{owner}/oracle_exchange/ORACLE_ANSWER.json', family, 'ORACLE_ANSWER.json'))
        ask_id = answer['ask_receipt_id']
        if Path(ask_id).name != ask_id:
            raise ValueError('INVALID_ASK_RECEIPT_BASENAME')
        ask = json.loads(read(f'{owner}/oracle_exchange/{ask_id}.json', family, 'ASK.json'))
        release = json.loads(read(f'{owner}/oracle_exchange/FORMAL_ANSWER_RELEASE_RECEIPT.json', family, 'ANSWER_RELEASE.json'))
        routes = assets['CANDIDATE-ROUTES.json']
        a, b = routes['candidate_A'], routes['candidate_B']
        center = assets['CENTER-ROUTE.json']['center_route']
        for rows in (a, b, center):
            if len(rows) < 2 or any(len(r['xyz']) != 3 or
                    not all(isinstance(v, (float, int)) and math.isfinite(v) for v in r['xyz']) or
                    not isinstance(r['road_option'], str) for r in rows):
                raise ValueError('ROUTE_ROW_STRUCTURE_INVALID')
        same_count = len(a) == len(b) == len(center)
        if not same_count:
            raise ValueError('UNEXPECTED_POINT_CORRESPONDENCE')
        scene = assets['SCENE.json']
        signatures = scene['task_signatures']
        sig_keys = sorted(set(signatures[0]) | set(signatures[1]))
        differences = [k for k in sig_keys if signatures[0].get(k) != signatures[1].get(k)]
        native_record = official['_checkpoint']['records'][0]
        supervision = receipts['V11_SUPERVISION_RECEIPT.json']
        install = receipts['ONLINE_ROUTE_INSTALL_RECEIPT.json']
        timing = receipts['RQ3_LIFECYCLE_TIMING_RECEIPT.json']
        row = {
            'family': family, 'scene_id': scene_id, 'historical_run_id': cfg['run_id'],
            'selection_basis': 'REF first, LMK sole replacement; historical execution and interface availability, no performance ranking',
            'instruction': cfg['method_input']['instruction'],
            'historical_seed_reference_only': cfg['scientific_seed_not_available_to_method'],
            'new_runtime_seed': None,
            'historical_chain': {
                'durable_ask': ask['durable'], 'query_id': ask['query_id'],
                'answer_query_matches': answer['query_id'] == ask['query_id'],
                'answer_receipt_matches': answer['ask_receipt_id'] == ask['receipt_id'],
                'answer_after_ask_ns': answer['released_epoch_ns'] > ask['written_epoch_ns'],
                'ask_hash_matches_release': sha((output/'source'/family/'ASK.json').read_bytes()) == release['ask_raw_sha256'],
                'answer_hash_matches_release': sha((output/'source'/family/'ORACLE_ANSWER.json').read_bytes()) == release['answer_raw_sha256'],
                'selected_candidate': answer['selected_candidate_id'],
                'ask_frame': timing['ask']['frame'], 'answer_frame': timing['answer']['frame'],
                'supervision_status': supervision['status'],
                'transaction_id': supervision['transaction']['transaction_id'],
                'transaction_committed': supervision['transaction']['committed'],
                'installed_identity': install['installed_route_identity'],
                'owner_committed': install['committed'],
                'full_replan_selected_route_id': receipts['RQ1_V2_FULL_REPLAN_RECEIPT.json']['selected_full_route_id'],
                'native_status': native_record['status'], 'native_scores': native_record['scores'],
                'native_infractions': native_record['infractions'],
                'new_smoke_execution_evidence': False,
            },
            'geometry': {
                'town': xml.find('route').attrib['town'], 'host_route_id': xml.find('route').attrib['id'],
                'count_A': len(a), 'count_B': len(b), 'all_road_options': sorted({r['road_option'] for r in a+b}),
                'first_xyz': a[0]['xyz'], 'last_xyz': a[-1]['xyz'],
                'same_start': a[0] == b[0], 'same_end': a[-1] == b[-1],
                'same_x_z_at_every_index': all(x['xyz'][0::2] == y['xyz'][0::2] for x,y in zip(a,b)),
                'same_x_z_as_center': all(x['xyz'][0::2] == c['xyz'][0::2] for x,c in zip(a,center)),
                'max_AB_euclidean_separation_m': max(math.dist(x['xyz'],y['xyz']) for x,y in zip(a,b)),
                'max_A_center_separation_m': max(math.dist(x['xyz'],c['xyz']) for x,c in zip(a,center)),
                'max_B_center_separation_m': max(math.dist(x['xyz'],c['xyz']) for x,c in zip(b,center)),
                'construction_source_text': routes['construction'],
                'task_signature_differing_keys': differences,
                'same_declared_irreversible_branch': signatures[0]['irreversible_branch_obligation'] == signatures[1]['irreversible_branch_obligation'],
                'same_declared_corridor': signatures[0]['terminal_road_or_corridor'] == signatures[1]['terminal_road_or_corridor'],
                'coordinate_bound_branch_gates_in_read_asset_contracts': None,
                'canonical_target_encoded_by_official_route': scene['canonical_target_encoded_by_official_route'],
                'scientific_entities': assets['LAYOUT.json']['scientific_entities'],
            },
            'stage4a_eligibility': False,
            'stop_reason': 'ONLY_IN_CORRIDOR_OFFSETS_NO_DISTINCT_ROAD_BRANCH_TASK_ASSETS_OR_COORDINATE_BOUND_TASK_GATES',
        }
        observations.append(row)
        route_pairs.append((a,b))
    result = {
        'scope': 'EXACTLY_TWO_STOPPED_ASSETS_CPU_FILE_READS_ONLY',
        'asset_search_stopped_at_utc': '2026-09-13T22:03:47.802014+00:00',
        'source_reads_after_stop': 'Evidence preservation for these same two assets only; no further candidate search',
        'assets': observations,
        'REF_LMK_candidate_arrays_equal': route_pairs[0] == route_pairs[1],
        'selected_smoke_asset': None,
        'status': 'STAGE4A_NO_EXISTING_LOW_RISK_AMBIGUOUS_ASSET',
        'meaning': 'Bounded search result, not proof that no such asset exists elsewhere; no historical relabeling',
    }
    output.mkdir(parents=True, exist_ok=True)
    (output/'ASSET_FINDINGS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (output/'SOURCE_SNAPSHOTS.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status': result['status'], 'assets': len(observations),
                      'snapshots': len(sources), 'geometry_equal_across_families': result['REF_LMK_candidate_arrays_equal']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inspect(args.repo.resolve(), args.output.resolve())
