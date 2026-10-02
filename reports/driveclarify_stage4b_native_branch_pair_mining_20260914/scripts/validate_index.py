#!/usr/bin/env python3
"""Validate saved STAGE4B extraction and its negative first-stage conclusion.

Uses authored decimal strings for an independent endpoint/start boundary check.
Does not import native packages or query historical execution results.
"""
from decimal import Decimal
import hashlib
import itertools
import json
from pathlib import Path


def main():
    report = Path(__file__).resolve().parents[1]
    index = json.loads((report/'ROUTE_INDEX.json').read_text())
    candidates = json.loads((report/'PAIR_CANDIDATES.json').read_text())
    checks = []
    def check(name, condition):
        checks.append({'check': name, 'pass': bool(condition)})
    allowed = Path('/home/buaa/wrh/simlingo/leaderboard/data')
    check('224 unchanged sources confined to explicit known library', len(index['files']) == 224 and all(
        Path(row['path']).parent in (allowed,allowed/'bench2drive_split') and
        hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() == row['sha256']
        for row in index['files']))
    records = index['route_records']
    by_id = {row['record_id']: row for row in records}
    groups = index['geometry_groups']
    membership = [member for group in groups for member in group['source_records']]
    check('552 records completely assigned once to 249 geometry groups',
          len(records) == len(by_id) == 552 and len(groups) == 249 and
          len(membership) == len(set(membership)) == len(records) and set(membership) == set(by_id))
    check('All extracted complete route fields present', index['parse_errors'] == [] and all(
        len(row['authored_waypoints']) == row['waypoint_count'] == len(row['xyz']) == len(row['sparse_polyline_xy']) and
        row['waypoint_count'] >= 2 and row['xyz'][0] == row['start_xyz'] and row['xyz'][-1] == row['endpoint_xyz'] and
        'scenario_metadata' in row and 'weather_metadata' in row and row['source_file']
        for row in records))
    canonical = [by_id[group['canonical_record']] for group in groups]
    def distance_squared(a,b,position,keys):
        return sum((Decimal(a['authored_waypoints'][position][key])-
                    Decimal(b['authored_waypoints'][position][key]))**2 for key in keys)
    decimal_pairs = []
    near, end, same_town = 0, 0, 0
    for a,b in itertools.combinations(canonical,2):
        if a['town'] != b['town']:
            continue
        same_town += 1
        start_ok = distance_squared(a,b,0,('x','y')) <= Decimal('4')
        end_ok = distance_squared(a,b,-1,('x','y','z')) <= Decimal('0.000001')
        near += int(start_ok)
        end += int(end_ok)
        if start_ok and end_ok:
            decimal_pairs.append((a['record_id'],b['record_id']))
    check('Authored Decimal arithmetic confirms no joint-compatible pair',
          decimal_pairs == [] and candidates['priority_3_distinct_geometry_stage1_pairs'] == [] and
          (same_town,near,end) == (10251,4,2))
    check('Both priority Town07 routes have no sibling',
          [row['route_id'] for row in candidates['priority_1_and_2']] == ['26458','25968'] and
          all(row['stage1_pair_candidates'] == [] and row['near_start_or_equal_endpoint_neighbors'] == []
              for row in candidates['priority_1_and_2']))
    selected = json.loads((report/'SELECTED_PAIR.json').read_text())
    geometry = json.loads((report/'BRANCH_GEOMETRY.json').read_text())
    gates = json.loads((report/'BRANCH_GATE_DRAFT.json').read_text())
    check('Unreached geometry/gates and native tier remain null', selected['selected_pair'] is None and
          selected['native_execution_tier'] is None and geometry['shared_prefix_length_m'] is None and
          geometry['max_branch_separation_m'] is None and gates['branch_A_gate'] is None and gates['branch_B_gate'] is None)
    boundary = json.loads((report/'evidence/SEARCH_BOUNDARY.json').read_text())
    check('Three priorities under 20 minutes and no historical-result lookup',
          boundary['priorities_executed'] == [1,2,3] and boundary['active_search_upper_bound_s'] < 1200 and
          candidates['execution_tier_lookups_performed'] == 0)
    result = {'status': 'PASS' if all(row['pass'] for row in checks) else 'FAIL',
              'scope': 'CURRENT_XML_INDEX_AND_NEGATIVE_FILTER_ONLY', 'checks': checks,
              'passed': sum(row['pass'] for row in checks), 'total': len(checks),
              'independent_decimal_joint_pairs': decimal_pairs,
              'native_execution': False, 'valid_pair_found': False}
    (report/'logs/validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
