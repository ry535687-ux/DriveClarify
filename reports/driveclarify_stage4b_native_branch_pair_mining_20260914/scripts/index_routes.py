#!/usr/bin/env python3
"""STAGE4B bounded native XML index and first-stage pair filter; stdlib only.

No recursive discovery, native imports, subprocess, route edits, or driving.
Exit 0 means file screening completed, not that any pair is qualified.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET


SUPPLEMENTS = ('bench2drive220.xml', 'routes_devtest.xml',
               'routes_training.xml', 'routes_validation.xml')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def metadata(element):
    if element is None:
        return None
    return {'tag': element.tag, 'attributes': dict(element.attrib),
            'children': [metadata(child) for child in element]}


def measures(a, b):
    yaw_a, yaw_b = a['initial_yaw'], b['initial_yaw']
    yaw_delta = None if yaw_a is None or yaw_b is None else abs((yaw_a-yaw_b+180)%360-180)
    return {
        'a': a['record_id'], 'b': b['record_id'],
        'route_A_id': a['route_id'], 'route_B_id': b['route_id'],
        'town': a['town'],
        'start_xy_distance_m': math.dist(a['start_xyz'][:2], b['start_xyz'][:2]),
        'endpoint_xyz_distance_m': math.dist(a['endpoint_xyz'], b['endpoint_xyz']),
        'yaw_delta_deg': yaw_delta,
        'yaw_status': 'BOTH_AUTHORED' if yaw_delta is not None else 'NOT_COMPARABLE_FROM_XML',
    }


def main(data, output):
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    (output/'evidence').mkdir(exist_ok=True)
    contract = {
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'same_town': True, 'start_xy_max_m': 2.0, 'endpoint_xyz_max_m': 0.001,
        'initial_yaw_max_deg_if_both_authored': 15.0,
        'shared_prefix_min_m': 10.0, 'shared_prefix_tolerance_m': 1.5,
        'resampling_step_m': 1.0,
        'stable_branch_separation_strictly_gt_m': 5.0,
        'stable_branch_interval_min_m': 10.0,
        'distinct_heading_min_deg_for_geometric_branch': 30.0,
        'threshold_role': 'FIXED_DEVELOPMENT_SCREEN_NOT_SCIENTIFIC_OR_SAFETY_THRESHOLD',
        'allowed_primary_root': str(data/'bench2drive_split'),
        'same_level_files': list(SUPPLEMENTS),
        'same_level_source_caveat': 'Combined B2D file plus complete same-level XML inputs. '
            'Exact launcher usage of devtest/training/validation is not verified; '
            'these supplemental files are only static geometry coverage, never proof of native execution.',
    }
    def write(name, value):
        # Keep the complete machine index compact; no raw XML review packet.
        options = {'separators': (',', ':')} if name == 'ROUTE_INDEX.json' else {'indent': 2}
        (output/name).write_text(json.dumps(value, ensure_ascii=False, **options)+'\n')
    write('evidence/SEARCH_CONTRACT.json', contract)
    files = sorted((data/'bench2drive_split').glob('*.xml')) + [data/n for n in SUPPLEMENTS]
    rows, sources, errors = [], [], []
    for path in files:
        if time.monotonic()-started > 1200:
            raise TimeoutError('STAGE4B_INDEX_HARD_STOP')
        raw = path.read_bytes()
        file_sha = digest(raw)
        sources.append({'path': str(path), 'bytes': len(raw), 'sha256': file_sha})
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as error:
            errors.append({'source': str(path), 'error': str(error)})
            continue
        for index, route in enumerate(root.findall('route')):
            waypoints = route.findall('./waypoints/position')
            if not waypoints:
                waypoints = route.findall('./waypoint')
            xyz = [[float(w.attrib[k]) for k in ('x','y','z')] for w in waypoints]
            if len(xyz)<2 or not all(math.isfinite(v) for point in xyz for v in point):
                errors.append({'source': str(path), 'route': route.attrib, 'error': 'INVALID_COMPLETE_ROUTE_POINTS'})
                continue
            geometry = {'town': route.attrib['town'], 'xyz': xyz}
            geometry_sha = digest(json.dumps(geometry,sort_keys=True,separators=(',',':')).encode())
            rows.append({
                'record_id': str(path.relative_to(data))+':'+str(index)+':'+route.attrib['id'],
                'source_file': str(path), 'source_sha256': file_sha,
                'route_id': route.attrib['id'], 'town': route.attrib['town'],
                'authored_waypoints': [dict(w.attrib) for w in waypoints],
                'xyz': xyz, 'sparse_polyline_xy': [point[:2] for point in xyz],
                'start_xyz': xyz[0], 'endpoint_xyz': xyz[-1],
                'initial_yaw': float(waypoints[0].attrib['yaw']) if 'yaw' in waypoints[0].attrib else None,
                'waypoint_count': len(xyz),
                'authored_polyline_length_xy_m': sum(math.dist(a[:2],b[:2]) for a,b in zip(xyz,xyz[1:])),
                'scenario_metadata': metadata(route.find('scenarios')),
                'weather_metadata': metadata(route.find('weathers')),
                'geometry_sha256': geometry_sha,
                'input_scope': 'PRIMARY_B2D' if path.name.startswith('bench2drive') else
                    'SUPPLEMENTAL_SAME_LEVEL_FULL_XML_NATIVE_USE_UNVERIFIED',
            })
    groups = {}
    for row in rows:
        groups.setdefault(row['geometry_sha256'], []).append(row)
    canonical = [group[0] for group in groups.values()]
    pairs = []
    stats = {'same_town_distinct_geometry_pairs': 0, 'near_start_pairs': 0,
             'compatible_endpoint_pairs': 0, 'both_start_and_endpoint_pairs': 0}
    for a,b in itertools.combinations(canonical,2):
        if a['town'] != b['town']:
            continue
        stats['same_town_distinct_geometry_pairs'] += 1
        m = measures(a,b)
        near = m['start_xy_distance_m'] <= contract['start_xy_max_m']
        end = m['endpoint_xyz_distance_m'] <= contract['endpoint_xyz_max_m']
        stats['near_start_pairs'] += int(near)
        stats['compatible_endpoint_pairs'] += int(end)
        stats['both_start_and_endpoint_pairs'] += int(near and end)
        if near and end and (m['yaw_delta_deg'] is None or m['yaw_delta_deg']<=15):
            pairs.append(m)
    focus = []
    for priority,rid in enumerate(('26458','25968'),1):
        a = next(row for row in canonical if row['route_id']==rid and row['town']=='Town07')
        others = [b for b in canonical if b['town']=='Town07' and b['geometry_sha256']!=a['geometry_sha256']]
        neighbors = [measures(a,b) for b in others]
        focus.append({
            'priority': priority, 'route_id': rid, 'town': 'Town07',
            'source_file': a['source_file'], 'record_id': a['record_id'],
            'start_xyz': a['start_xyz'], 'endpoint_xyz': a['endpoint_xyz'],
            'other_distinct_Town07_geometries': len(others),
            'near_start_or_equal_endpoint_neighbors': [m for m in neighbors if
                m['start_xy_distance_m']<=2 or m['endpoint_xyz_distance_m']<=.001],
            'closest_endpoint_other_route': min(neighbors,key=lambda m:m['endpoint_xyz_distance_m']) if neighbors else None,
            'stage1_pair_candidates': [m for m in pairs if a['record_id'] in (m['a'],m['b'])],
        })
    geometry_groups = [{'geometry_sha256': key, 'canonical_record': group[0]['record_id'],
                        'source_records': [row['record_id'] for row in group]} for key,group in groups.items()]
    counts = {'source_xml_files': len(files), 'route_records_including_aggregate_duplicates': len(rows),
              'distinct_authored_geometries': len(canonical),
              'primary_distinct_geometries': len({row['geometry_sha256'] for row in rows if row['input_scope']=='PRIMARY_B2D'}),
              'parse_errors': len(errors)}
    write('ROUTE_INDEX.json', {'schema': 'driveclarify.stage4b.route-index.v1',
          'files': sources, 'route_records': rows, 'geometry_groups': geometry_groups,
          'counts': counts, 'parse_errors': errors,
          'metadata_note': 'Geometry deduplicated independently of weather/scenarios; '
              'the metadata differences are not permitted differences in a future public scene.'})
    write('PAIR_CANDIDATES.json', {
        'priority_1_and_2': focus, 'priority_3_distinct_geometry_stage1_pairs': pairs,
        'aggregate_pair_filter_counts': stats,
        'identical_geometry_records_collapsed': len(rows)-len(canonical),
        'geometry_examined_after_stage1': False,
        'execution_tier_lookups_performed': 0,
        'screen_elapsed_seconds': time.monotonic()-started,
    })
    print(json.dumps({'counts': counts, 'filters': stats, 'pairs': pairs, 'focus': focus},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    main(args.data.resolve(),args.output.resolve())
