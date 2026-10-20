"""Conservative exact unified-text-patch validation; no host Git requirement."""
from pathlib import PurePosixPath
import re

from .definitions import parse_json, source_path
from .errors import ExperimentError
from .snapshots import protected


def bad(message):
    raise ExperimentError('INVALID_PATCH', message, 'Use an exact unified text diff against the captured baseline; binary/rename/fuzzy patches are unsupported.')


def patch_paths(header, prefix):
    path = header.rstrip('\n').split('\t', 1)[0]
    if path == '/dev/null':
        return None
    if not path.startswith(prefix + '/'):
        bad('Patch paths must use a/ and b/ prefixes.')
    path = source_path(path[2:], 'patch path')
    if protected(path):
        bad('Patch cannot change protected paths: ' + path)
    return path


def apply_text_patch(content, workspace):
    try:
        lines = content.decode('utf-8').splitlines(keepends=True)
    except UnicodeError:
        bad('Patch is not UTF-8 text.')
    changes, i = {}, 0
    while i < len(lines):
        if lines[i].startswith(('diff --git ', 'index ', 'new file mode ', 'deleted file mode ')) or not lines[i].strip():
            i += 1
            continue
        if not lines[i].startswith('--- ') or i + 1 >= len(lines) or not lines[i+1].startswith('+++ '):
            bad('Expected unified ---/+++ file headers.')
        old, new = patch_paths(lines[i][4:], 'a'), patch_paths(lines[i+1][4:], 'b')
        if old is None and new is None or old is not None and new is not None and old != new:
            bad('Renames and empty patch targets are unsupported.')
        path = old or new
        if path in changes:
            bad('Patch changes one path more than once: ' + path)
        target = workspace / path
        for parent in (target, *target.parents):
            if parent == workspace:
                break
            if parent.is_symlink():
                bad('Patch must not traverse symlinks: ' + path)
        if old is None:
            if target.exists():
                bad('Patch creates a path already present: ' + path)
            original = []
        else:
            if not target.is_file() or target.stat().st_size > 16 * 1024 ** 2:
                bad('Patch target is missing or exceeds 16 MiB: ' + path)
            try:
                original = target.read_bytes().decode('utf-8').splitlines(keepends=True)
            except UnicodeError:
                bad('Patch target is not UTF-8 text: ' + path)
        output, consumed, hunks = [], 0, 0
        i += 2
        while i < len(lines) and lines[i].startswith('@@ '):
            match = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n?', lines[i])
            if not match:
                bad('Invalid patch hunk header.')
            old_start, old_count, new_start, new_count = [int(value) if value is not None else 1 for value in match.groups()]
            offset = old_start if old_count == 0 else old_start - 1
            new_offset = new_start if new_count == 0 else new_start - 1
            if offset < consumed or offset > len(original) or new_offset != len(output) + offset - consumed:
                bad('Patch hunk position does not match baseline: ' + path)
            output.extend(original[consumed:offset]); consumed = offset
            i += 1
            old_seen = new_seen = 0
            while i < len(lines) and (old_seen < old_count or new_seen < new_count):
                row = lines[i]
                if not row or row[0] not in (' ', '+', '-'):
                    bad('Patch hunk is incomplete.')
                marker, text = row[0], row[1:]
                i += 1
                if i < len(lines) and lines[i].startswith('\\ No newline at end of file'):
                    text = text.removesuffix('\n'); i += 1
                if marker in (' ', '-'):
                    if consumed >= len(original) or original[consumed] != text:
                        bad('Patch context conflicts with captured baseline: ' + path)
                    consumed += 1; old_seen += 1
                if marker in (' ', '+'):
                    output.append(text); new_seen += 1
                if old_seen > old_count or new_seen > new_count:
                    bad('Patch hunk lengths do not match.')
            hunks += 1
        if not hunks:
            bad('Each patched file needs at least one hunk.')
        output.extend(original[consumed:])
        if new is None and output:
            bad('Deletion patch must remove the entire file.')
        changes[path] = None if new is None else ''.join(output).encode('utf-8')
    if not changes:
        bad('Patch contains no file changes.')
    return changes


def validate_inputs(definition, captured):
    """Check frozen case JSON, text prompts, patches and fixture intersections."""
    inputs, workspace = captured / 'inputs', captured / 'workspace'
    variant_changes = {}
    for variant in definition['variants']:
        changes = {}
        if 'patch_file' in variant:
            changes = apply_text_patch((inputs / variant['patch_file']).read_bytes(), workspace)
        for key in ('instructions_file',):
            if key in variant:
                try:
                    (inputs / variant[key]).read_bytes().decode('utf-8')
                except UnicodeError:
                    raise ExperimentError('INVALID_INPUT', key + ' must be UTF-8 text.') from None
        variant_changes[variant['id']] = sorted(changes)
    for case in definition['cases']:
        if 'input_file' in case:
            parse_json((inputs / case['input_file']).read_bytes(), 'case ' + case['id'])
        if 'prompt_file' in case:
            raw = (inputs / case['prompt_file']).read_bytes()
            try:
                if not raw.decode('utf-8').strip():
                    raise ValueError()
            except (UnicodeError, ValueError):
                raise ExperimentError('INVALID_INPUT', 'Case prompt must be nonempty UTF-8 text.') from None
        for fixture in case['fixtures']:
            path = fixture['destination']
            if protected(path):
                raise ExperimentError('INVALID_INPUT', 'Fixture cannot replace a protected path: ' + path)
            for parent in (PurePosixPath(path), *PurePosixPath(path).parents):
                current = workspace / parent
                if current.is_symlink():
                    raise ExperimentError('INVALID_INPUT', 'Fixture cannot traverse a symlink: ' + path)
                if parent != PurePosixPath(path) and current.exists() and not current.is_dir():
                    raise ExperimentError('INVALID_INPUT', 'Fixture parent is not a directory: ' + path)
            if (workspace / path).is_dir():
                raise ExperimentError('INVALID_INPUT', 'Fixture destination is a directory: ' + path)
            for changed in variant_changes.values():
                if path in changed or any(path.startswith(other + '/') or other.startswith(path + '/') for other in changed):
                    raise ExperimentError('INVALID_INPUT', 'Fixture conflicts with a declared variant patch: ' + path)
    return {'variant_changed_paths': variant_changes}
