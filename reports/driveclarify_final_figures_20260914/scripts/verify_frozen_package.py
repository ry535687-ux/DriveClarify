"""Read-only verification of the final package; never renders or analyzes."""
import json
from figure_io import OUT, sha, verify_protected

manifest = json.loads((OUT / 'FINAL_FIGURE_MANIFEST.json').read_text())
assert manifest['status'] == 'FINAL_FIGURES_FROZEN_READY_FOR_MANUSCRIPT'
expected = {member['path'] for member in manifest['members']}
actual = {p.relative_to(OUT).as_posix() for p in OUT.rglob('*')
          if p.is_file() and '__pycache__' not in p.parts
          and p.name != 'FINAL_FIGURE_MANIFEST.json'}
assert expected == actual
for member in manifest['members']:
    path = OUT / member['path']
    assert path.stat().st_size == member['bytes'] and sha(path) == member['sha256'], member['path']
protected = verify_protected()
assert manifest['visual_qa']['normal_size_opened']
assert manifest['visual_qa']['actual_paper_size_opened']
assert manifest['visual_qa']['grayscale_opened']
assert manifest['data_or_statistics_changed'] is False
print(json.dumps(dict(status=manifest['status'], final_members_verified=len(expected),
                      protected_inputs_verified=protected, data_or_statistics_changed=False)))
