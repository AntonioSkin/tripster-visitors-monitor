"""Move only one task's changed data to a short, serialized writer job."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path, PurePosixPath

PREFIXES = {
    '1': ('data/daily/', 'data/runs/', 'data/telegram/'),
    '2': ('data/seats/',),
    '3': ('data/calendar_history/',),
    '4': ('data/fx/',),
}
FILES = {'1': {'data/daily.csv', 'reports/latest.md'}, '2': {'reports/seats.md'}, '3': set(), '4': set()}


class DataConflict(RuntimeError):
    pass


def git(root, *args, check=True):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True)
    if check and result.returncode:
        # Do not expose credentials or remote URLs from stderr.
        raise RuntimeError(f'git {args[0]} failed (exit {result.returncode})')
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def allowed(task, name):
    if not isinstance(name, str):
        return False
    path = PurePosixPath(name)
    return (task in PREFIXES and isinstance(name, str) and bool(name)
            and not path.is_absolute() and str(path) == name
            and '..' not in path.parts and '\\' not in name
            and (name in FILES[task] or any(name.startswith(p) for p in PREFIXES[task])))


def safe_file(root, name):
    result = root / name
    if not result.resolve().is_relative_to(root.resolve()) or result.is_symlink():
        raise ValueError('Result path escapes its directory')
    return result


def head_content(root, name):
    result = git(root, 'show', f'HEAD:{name}', check=False)
    return result.stdout if result.returncode == 0 else None


def export_bundle(root, task, directory, run_id):
    root, directory = Path(root), Path(directory)
    tracked = git(root, 'diff', '--name-only', '-z', 'HEAD', '--').stdout
    new = git(root, 'ls-files', '--others', '--exclude-standard', '-z').stdout
    names = sorted({n.decode('utf-8') for n in (tracked + new).split(b'\0') if n})
    entries = []
    for name in names:
        if not allowed(task, name):
            continue
        file = safe_file(root, name)
        content = file.read_bytes() if file.exists() else None
        base = digest(head_content(root, name))
        if digest(content) == base:
            continue
        if content is not None:
            destination = directory / 'files' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        entries.append({'path': name, 'base': base, 'content': digest(content)})
    if not entries:
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {'version': 1, 'task': task, 'run_id': str(run_id),
                'base_commit': git(root, 'rev-parse', 'HEAD').stdout.decode().strip(), 'files': entries}
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return len(entries)


def read_bundle(directory, task, run_id):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if (manifest.get('version') != 1 or manifest.get('task') != task
            or manifest.get('run_id') != str(run_id) or not manifest.get('files')):
        raise ValueError('Result bundle does not belong to this task and run')
    records, seen = [], set()
    for entry in manifest['files']:
        name = entry.get('path')
        if not isinstance(name, str) or not allowed(task, name) or name in seen:
            raise ValueError('Unexpected or duplicate result path')
        seen.add(name)
        for key in ('base', 'content'):
            value = entry.get(key)
            if value is not None and (not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)):
                raise ValueError('Invalid result checksum')
        content = None
        if entry['content'] is not None:
            content = safe_file(directory / 'files', name).read_bytes()
            if digest(content) != entry['content']:
                raise ValueError('Result checksum mismatch')
        records.append((name, entry['base'], entry['content'], content))
    return records


def apply_bundle(root, task, directory, run_id, attempts=5):
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('The writer may run only in a disposable GitHub Actions checkout')
    root = Path(root)
    records = read_bundle(directory, task, run_id)
    for attempt in range(attempts):
        # Only this disposable checkout is reset; the immutable artifact stays available.
        git(root, 'fetch', '--quiet', 'origin', 'main')
        git(root, 'reset', '--hard', 'FETCH_HEAD')
        changes = []
        for name, base, incoming, content in records:
            target = safe_file(root, name)
            current = digest(target.read_bytes() if target.exists() else None)
            if current == incoming:
                continue  # Retrying the writer never reverts a saved result.
            if current != base:
                raise DataConflict(f'Task {task}: newer data already exists at {name}; automatic overwrite refused')
            changes.append((name, content))
        # Check every path before changing any file.
        for name, content in changes:
            target = safe_file(root, name)
            if content is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        if not changes:
            print(f'Task {task}: results already saved')
            return 0
        git(root, 'add', '--all', '--', *(name for name, _ in changes))
        git(root, '-c', 'user.name=github-actions[bot]',
            '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
            'commit', '-m', f'Save task {task} results from run {run_id}')
        pushed = git(root, 'push', 'origin', 'HEAD:main', check=False)
        if pushed.returncode == 0:
            print(f'Task {task}: saved {len(changes)} files; push attempt {attempt + 1}')
            return len(changes)
        if attempt + 1 < attempts:
            # Another writer or a code edit may have moved main. Reapply against
            # its new head instead of force-pushing or overwriting other tasks.
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f'Task {task}: GitHub did not accept the results after {attempts} attempts; retry the writer job')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['export', 'apply'])
    parser.add_argument('--task', choices=list(PREFIXES), required=True)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    run_id = os.environ['GITHUB_RUN_ID']
    directory = Path(args.directory).resolve()
    if args.mode == 'export':
        # The bundle path is under RUNNER_TEMP, never inside the repository.
        if directory.exists():
            raise RuntimeError('Result bundle directory must be new')
        count = export_bundle(Path.cwd(), args.task, directory, run_id)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write(f'changed={str(count > 0).lower()}\n')
                output.write(f'artifact=monitor-task-{args.task}-{run_id}-{os.environ.get("GITHUB_RUN_ATTEMPT", "1")}\n')
        print(f'Task {args.task}: exported {count} changed data files')
    else:
        apply_bundle(Path.cwd(), args.task, directory, run_id)


if __name__ == '__main__':
    main()
