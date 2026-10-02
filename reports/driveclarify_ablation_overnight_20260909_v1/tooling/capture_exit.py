"""Capture repository state without changing the index or working tree."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists():
        raise SystemExit('Refusing to overwrite an existing exit capture')
    out.mkdir(parents=True)
    repo = Path('/home/buaa/wrh/DriveClarify')
    entry = repo / 'reports/driveclarify_task_relation_ablation_dev_20260909_v1/entry'
    captured = {'captured_local': datetime.datetime.now().astimezone().isoformat(),
                'read_only_git_commands': True, 'repositories': {}}
    queries = {'branch.txt': ['branch', '--show-current'], 'head.txt': ['rev-parse', 'HEAD'],
               'status.txt': ['status', '--porcelain=v1', '--untracked-files=all'],
               'tracked.diff': ['diff', '--no-ext-diff'],
               'staged.diff': ['diff', '--cached', '--no-ext-diff'],
               'untracked.txt': ['ls-files', '--others', '--exclude-standard']}
    for label, cwd in [('driveclarify', repo), ('simlingo', repo.parent / 'simlingo')]:
        info = {'path': str(cwd), 'files': {}}
        for name, command in queries.items():
            path = out / (label + '_' + name)
            with path.open('wb') as stream:
                result = subprocess.run(['git', '-C', str(cwd)] + command,
                                        stdout=stream, stderr=subprocess.PIPE)
            if result.returncode:
                raise SystemExit(result.stderr.decode(errors='replace'))
            info['files'][name] = {'path': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'bytes': path.stat().st_size}
        old = set((entry / (label + '_untracked.txt')).read_text().splitlines())
        new = set((out / (label + '_untracked.txt')).read_text().splitlines())
        removed = sorted(old - new)
        added = sorted(new - old)
        (out / (label + '_preexisting_untracked_no_longer_listed.txt')).write_text(
            '\n'.join(removed) + ('\n' if removed else ''))
        (out / (label + '_new_untracked_since_entry.txt')).write_text(
            '\n'.join(added) + ('\n' if added else ''))
        # A path no longer appearing in untracked output can also have become
        # tracked; report the inventory difference without calling it deletion.
        info.update(preexisting_untracked_count=len(old), exit_untracked_count=len(new),
                    preexisting_untracked_no_longer_listed_count=len(removed),
                    added_untracked_count=len(added),
                    head_unchanged=(entry / (label + '_head.txt')).read_bytes() ==
                                   (out / (label + '_head.txt')).read_bytes(),
                    branch_unchanged=(entry / (label + '_branch.txt')).read_bytes() ==
                                     (out / (label + '_branch.txt')).read_bytes(),
                    tracked_diff_unchanged=(entry / (label + '_tracked.diff')).read_bytes() ==
                                           (out / (label + '_tracked.diff')).read_bytes(),
                    staged_diff_unchanged=(entry / (label + '_staged.diff')).read_bytes() ==
                                          (out / (label + '_staged.diff')).read_bytes())
        captured['repositories'][label] = info
    (out / 'GIT_EXIT_RECEIPT.json').write_text(json.dumps(captured, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: {k: v for k, v in value.items() if k != 'files'}
                      for key, value in captured['repositories'].items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
