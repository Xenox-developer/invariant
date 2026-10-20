"""Verified workspace materialization and bounded, link-safe result capture."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat

from .. import workspace
from .definitions import digest, parse_json, source_path
from .errors import ExperimentError
from .patches import apply_text_patch
from .snapshots import glob_match, regular_bytes, seal_tree


def write_file(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as output:
        output.write(content); output.flush(); os.fchmod(output.fileno(), mode & 0o777); os.fsync(output.fileno())


def materialize(source, destination, manifest):
    destination.mkdir(mode=0o700)
    links = []
    for relative, row in sorted(manifest.items()):
        source_path(relative)
        if relative.startswith('@input/'):
            continue
        target = destination / relative
        if row['type'] == 'directory':
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
        elif row['type'] == 'symlink':
            if not (source / relative).is_symlink() or os.readlink(source / relative) != row['target']:
                raise ExperimentError('CORRUPT_BASELINE', 'Saved symlink differs from the captured manifest.')
            links.append((target, row['target']))
        elif row['type'] == 'file':
            content, _ = regular_bytes(source, relative, maximum=row['size'])
            if len(content) != row['size'] or hashlib.sha256(content).hexdigest() != row['sha256']:
                raise ExperimentError('CORRUPT_BASELINE', 'Saved file differs from the captured manifest: ' + relative)
            write_file(target, content, row.get('mode', 0o600) & 0o777)
        else:
            raise ExperimentError('CORRUPT_BASELINE', 'Unknown saved entry type.')
    for path, link in links:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.symlink_to(link)
    for relative, row in sorted(manifest.items(), key=lambda item: len(item[0]), reverse=True):
        if row['type'] == 'directory' and not relative.startswith('@input/'):
            (destination / relative).chmod(row.get('mode', 0o700))


def prepare_inputs(baseline, frozen, variant, case, destination):
    for relative, expected in frozen['inputs'].items():
        content, _ = regular_bytes(baseline / 'inputs', relative,
                                   maximum=frozen['capture_limits']['source_bytes'])
        if hashlib.sha256(content).hexdigest() != expected:
            raise ExperimentError('CORRUPT_BASELINE', 'Captured input digest changed: ' + relative)
    destination.mkdir(mode=0o700)
    workspace.private_write(destination / 'variant.json', variant)
    value = parse_json((baseline / 'inputs' / case['input_file']).read_bytes()) if 'input_file' in case else {}
    workspace.private_write(destination / 'case.json', value)
    if case.get('prompt_file'):
        content, _ = regular_bytes(baseline / 'inputs', case['prompt_file'])
        write_file(destination / 'prompt.txt', content, 0o400)


def apply_variant(baseline, writable, variant, case):
    if 'patch_file' in variant:
        changes = apply_text_patch((baseline / 'inputs' / variant['patch_file']).read_bytes(), writable)
        for relative, content in changes.items():
            target = writable / relative
            mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
            target.unlink(missing_ok=True)
            if content is not None:
                write_file(target, content, mode)
    for fixture in case['fixtures']:
        target = writable / fixture['destination']
        for parent in (target, *target.parents):
            if parent == writable:
                break
            if parent.is_symlink():
                raise ExperimentError('INVALID_INPUT', 'Fixture would traverse a symlink.')
        target.unlink(missing_ok=True)
        content, _ = regular_bytes(baseline / 'inputs', fixture['source'])
        write_file(target, content)


def capture_workspace(source, destination, limits):
    """After verified process stop. Never follow candidate symlinks or special files."""
    destination.mkdir(mode=0o700)
    entries, size, count = {}, 0, 0
    for base, directories, files in os.walk(source, topdown=True, followlinks=False,
                                            onerror=lambda error: (_ for _ in ()).throw(error)):
        for name in sorted(directories + files):
            relative = (Path(base).relative_to(source) / name).as_posix()
            if PurePosixPath(relative).parts[0] in ('.invariant', '.git'):
                if name in directories: directories.remove(name)
                continue
            count += 1
            if count > limits['entries']:
                raise ExperimentError('CAPTURE_LIMIT', 'Final workspace exceeds its entry limit.')
            path, target = source / relative, destination / relative
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                entries[relative] = {'type': 'directory', 'mode': info.st_mode & 0o777}
            elif stat.S_ISLNK(info.st_mode):
                # Link text is evidence, never a file to dereference on the host.
                entries[relative] = {'type': 'symlink', 'target': os.readlink(path)}
            elif stat.S_ISREG(info.st_mode):
                content, _ = regular_bytes(source, relative, maximum=max(0, limits['source_bytes'] - size))
                size += len(content)
                write_file(target, content, info.st_mode & 0o777)
                entries[relative] = {'type': 'file', 'size': len(content), 'mode': info.st_mode & 0o777,
                                     'sha256': hashlib.sha256(content).hexdigest()}
            else:
                raise ExperimentError('UNSAFE_OUTPUT', 'Cannot preserve a candidate socket, FIFO or device: ' + relative)
    for relative, row in entries.items():
        if row['type'] == 'symlink':
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.symlink_to(row['target'])
    return {'schema_version': 1, 'entries': entries, 'content_digest': digest(entries), 'source_bytes': size}


def diff_entries(before, after):
    before = {key: value for key, value in before.items() if not key.startswith('@input/')}
    return [{'path': path, 'change': 'created' if path not in before else 'deleted' if path not in after else 'modified',
             'before': before.get(path), 'after': after.get(path)}
            for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]


def collect_artifacts(source, manifest, patterns, destination, maximum):
    destination.mkdir(mode=0o700)
    records, total, skipped = [], 0, []
    for relative, row in sorted(manifest.items()):
        if not any(glob_match(relative, pattern) for pattern in patterns) or row['type'] == 'directory':
            continue
        if row['type'] != 'file':
            skipped.append({'path': relative, 'reason': 'symlink_not_followed'}); continue
        if total + row['size'] > maximum:
            skipped.append({'path': relative, 'reason': 'artifact_limit'}); continue
        content, _ = regular_bytes(source, relative, maximum=row['size'])
        write_file(destination / relative, content)
        total += len(content)
        records.append({'path': relative, 'size': len(content), 'sha256': row['sha256']})
    seal_tree(destination)
    return {'files': records, 'bytes': total, 'complete': not skipped, 'skipped': skipped}
