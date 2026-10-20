"""Verified source capture without Git or a writable mount of the original tree."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .definitions import MAX_ENTRIES, MAX_SOURCE_BYTES, declared_inputs, digest, source_path
from .errors import ExperimentError

MANDATORY_EXCLUSIONS = ('.git', '.invariant', '.env', '.env.*', '.ssh', '.aws', '.codex', 'auth.json')


def glob_match(path, pattern):
    """POSIX globs: * and ? stay in one segment; ** crosses directories."""
    parts, i = [], 0
    while i < len(pattern):
        if pattern[i:i+3] == '**/':
            parts.append('(?:.*/)?'); i += 3
        elif pattern[i:i+2] == '**':
            parts.append('.*'); i += 2
        elif pattern[i] == '*':
            parts.append('[^/]*'); i += 1
        elif pattern[i] == '?':
            parts.append('[^/]'); i += 1
        else:
            parts.append(re.escape(pattern[i])); i += 1
    return re.fullmatch(''.join(parts), path) is not None


def protected(path):
    return any(glob_match(part, pattern) for part in PurePosixPath(path).parts for pattern in MANDATORY_EXCLUSIONS)


def safe_path(project, relative):
    """Open directory ancestry without following links, even if a writer swaps it."""
    source_path(relative)
    fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def regular_bytes(project, relative, *, maximum=MAX_SOURCE_BYTES):
    try:
        directory, name = safe_path(project, relative)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
    except FileNotFoundError:
        raise
    except OSError:
        raise ExperimentError('UNSAFE_INPUT', 'Source path is unsafe or unreadable: ' + relative) from None
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ExperimentError('UNSAFE_INPUT', 'Expected a regular source file: ' + relative)
        if before.st_size > maximum:
            raise ExperimentError('CAPTURE_LIMIT', 'Source file exceeds capture limit: ' + relative)
        content = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if len(content) > maximum:
        raise ExperimentError('CAPTURE_LIMIT', 'Source file exceeds capture limit: ' + relative)
    if fingerprint(before) != fingerprint(after):
        raise ExperimentError('SOURCE_CHANGED', 'Source changed during capture: ' + relative,
                              'Stop the writer and retry; no study was created.')
    return content, after


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(content); output.flush(); os.fsync(output.fileno())
        os.fchmod(output.fileno(), mode & 0o777)


class SourceCapture:
    def __init__(self, project, definition, *, secret_paths=(), extra_inputs=()):
        self.project = Path(project)
        self.definition = definition
        self.inputs = set(declared_inputs(definition)) | set(extra_inputs)
        self.secret_paths = {str(Path(path).resolve()) for path in secret_paths}
        self.rules = definition['workspace']
        for path in self.inputs:
            if protected(path) or str(self.project / path) in self.secret_paths:
                raise ExperimentError('UNSAFE_INPUT', 'Declared input is a protected/credential path: ' + path)

    def excluded(self, relative):
        candidates = [relative, *(parent.as_posix() for parent in PurePosixPath(relative).parents if parent.as_posix() != '.')]
        return (protected(relative) or str(self.project / relative) in self.secret_paths
                or any(glob_match(candidate, pattern) or glob_match(candidate + '/', pattern)
                       for pattern in self.rules['exclude'] for candidate in candidates))

    def selected(self, relative):
        return not self.excluded(relative) and any(glob_match(relative, pattern) for pattern in self.rules['include'])

    def scan(self, destination=None):
        entries, versions, excluded = {}, {}, []
        total = count = 0
        encountered_inputs = set()
        for base, directories, files in os.walk(self.project, topdown=True, followlinks=False,
                                                onerror=lambda error: (_ for _ in ()).throw(error)):
            relative_base = Path(base).relative_to(self.project)
            for name in sorted(directories + files):
                count += 1
                if count > MAX_ENTRIES:
                    raise ExperimentError('CAPTURE_LIMIT', 'Workspace exceeds the entry-count limit.')
                relative = (relative_base / name).as_posix()
                path = self.project / relative
                is_input = relative in self.inputs
                # Excluded directories may contain explicit non-secret input files.
                descendants = any(item.startswith(relative + '/') for item in self.inputs)
                blocked = self.excluded(relative)
                if blocked and not is_input and not descendants:
                    excluded.append(relative)
                    if name in directories:
                        directories.remove(name)
                    continue
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    if self.selected(relative):
                        entries[relative] = {'type': 'directory', 'mode': info.st_mode & 0o777}
                        if destination:
                            (destination / 'workspace' / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                selected = self.selected(relative)
                if not selected and not is_input:
                    excluded.append(relative)
                    continue
                versions[relative] = fingerprint(info)
                if stat.S_ISLNK(info.st_mode):
                    if is_input:
                        raise ExperimentError('UNSAFE_INPUT', 'Declared input must be a regular file: ' + relative)
                    target = os.readlink(path)
                    resolved = path.resolve()
                    if os.path.isabs(target) or not resolved.is_relative_to(self.project) or not resolved.exists():
                        raise ExperimentError('UNSAFE_INPUT', 'Only existing in-project relative symlinks can be captured: ' + relative)
                    target_relative = resolved.relative_to(self.project).as_posix()
                    if self.excluded(target_relative) or not self.selected(target_relative):
                        raise ExperimentError('UNSAFE_INPUT', 'Symlink target is not included in the baseline: ' + relative)
                    # Record only the link; never read or copy through it.
                    entries[relative] = {'type': 'symlink', 'target': target}
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ExperimentError('UNSAFE_INPUT', 'Cannot capture a socket, FIFO or device: ' + relative)
                remaining = MAX_SOURCE_BYTES - total
                content, read_info = regular_bytes(self.project, relative, maximum=max(0, remaining))
                if fingerprint(info) != fingerprint(read_info):
                    raise ExperimentError('SOURCE_CHANGED', 'Source changed before capture: ' + relative)
                total += len(content)
                row = {'type': 'file', 'mode': info.st_mode & 0o777, 'size': len(content),
                       'sha256': hashlib.sha256(content).hexdigest()}
                if selected:
                    entries[relative] = row
                    if destination:
                        _write(destination / 'workspace' / relative, content, row['mode'])
                if is_input:
                    encountered_inputs.add(relative)
                    entries['@input/' + relative] = row
                    if destination:
                        _write(destination / 'inputs' / relative, content, 0o400)
        missing = self.inputs - encountered_inputs
        if missing:
            raise ExperimentError('INPUT_MISSING', 'Missing declared source inputs: ' + ', '.join(sorted(missing)))
        # Materialize links last so no file write can follow a candidate-created link.
        if destination:
            for relative, row in entries.items():
                if row['type'] == 'symlink':
                    path = destination / 'workspace' / relative
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.symlink_to(row['target'])
        return {'schema_version': 1, 'entries': entries, 'source_bytes': total,
                'excluded': sorted(set(excluded)), 'mandatory_exclusions': list(MANDATORY_EXCLUSIONS)}, versions

    def capture(self, destination):
        before, versions = self.scan()
        (destination / 'workspace').mkdir(mode=0o700)
        (destination / 'inputs').mkdir(mode=0o700)
        captured, copied_versions = self.scan(destination)
        after, after_versions = self.scan()
        if before != captured or captured != after or versions != copied_versions or versions != after_versions:
            raise ExperimentError('SOURCE_CHANGED', 'Workspace changed during capture.',
                                  'Stop the writer and retry; the partial capture is not a study.')
        captured['content_digest'] = digest(captured['entries'])
        return captured


def seal_tree(directory):
    """Immutable to workloads because never mounted writable; modes also deter accidents."""
    for base, dirs, files in os.walk(directory, followlinks=False):
        for name in files:
            path = Path(base) / name
            if not path.is_symlink():
                path.chmod(0o500 if path.stat().st_mode & 0o111 else 0o400)
        for name in dirs:
            path = Path(base) / name
            if not path.is_symlink():
                path.chmod(0o500)
    Path(directory).chmod(0o500)


def unseal_tree(directory):
    for base, dirs, files in os.walk(directory, topdown=True, followlinks=False):
        Path(base).chmod(0o700)
        for name in dirs:
            path = Path(base) / name
            if not path.is_symlink():
                path.chmod(0o700)
