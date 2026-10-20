"""Strict recipes, bounded inputs and deterministic trial expansion."""
import hashlib
import json
import math
from pathlib import PurePosixPath
import re

from ..policy import unique_object
from .errors import ExperimentError

SCHEMA_VERSION = 1
MAX_DEFINITION = 1024 * 1024
MAX_TRIALS = 1000
MAX_SOURCE_BYTES = 1024 ** 3
MAX_ENTRIES = 200000
NAME = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}'
LIMITS = {'definition_bytes': MAX_DEFINITION, 'trials': MAX_TRIALS,
          'source_bytes': MAX_SOURCE_BYTES, 'entries': MAX_ENTRIES,
          'log_bytes': 64 * 1024 ** 2, 'artifact_bytes': 256 * 1024 ** 2,
          'event_bytes': 2 * 1024 ** 2}


def invalid(message):
    raise ExperimentError('INVALID_DEFINITION', message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def parse_json(raw, label='JSON input'):
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            invalid(label + ': nonfinite number is not allowed')
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique_object, parse_float=finite_float,
                          parse_constant=lambda value: invalid(f'{label}: nonfinite number is not allowed'))
    except (ValueError, RecursionError, UnicodeError):
        invalid(f'{label}: expected finite JSON without duplicate keys')


def object_keys(value, allowed, required, label):
    if not isinstance(value, dict):
        invalid(label + ' must be an object')
    unknown, missing = set(value) - set(allowed), set(required) - set(value)
    if unknown or missing:
        invalid(f'{label}: unknown keys {sorted(unknown)}; missing keys {sorted(missing)}')


def text(value, label, *, maximum=4096, pattern=None):
    if not isinstance(value, str) or not value or len(value) > maximum or '\0' in value:
        invalid(label + ' must be a nonempty bounded string without NUL')
    if pattern and not re.fullmatch(pattern, value):
        invalid(label + ' has an invalid format')
    return value


def number(value, label, low, high, *, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not low <= value <= high or (type(value) is float and not math.isfinite(value)):
        invalid(f'{label} must be {"an integer" if integer else "a number"} between {low} and {high}')
    return value


def source_path(value, label='source path'):
    text(value, label)
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('..', '.') for part in value.split('/')) or '\\' in value or '\n' in value or '\r' in value:
        invalid(label + ' must be a project-relative POSIX path without traversal')
    if value != path.as_posix():
        invalid(label + ' must use a canonical project-relative path')
    return value


def patterns(values, label):
    if not isinstance(values, list) or len(values) > 256:
        invalid(label + ' must be a bounded list of glob strings')
    for value in values:
        source_path(value, label)
        if any(c in value for c in '[]{}'):
            invalid(label + ' supports only *, ** and ? glob operators')
    return values


def argv(value, label):
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        invalid(label + ' must be a nonempty argument vector of at most 128 strings')
    for arg in value:
        if not isinstance(arg, str) or len(arg) > 8192 or '\0' in arg:
            invalid(label + ' contains an invalid argument')
    text(value[0], label + ' executable', maximum=8192)


def validate(value):
    object_keys(value, ('schema_version', 'name', 'runner', 'workspace', 'variants', 'cases',
                       'repetitions', 'execution', 'evaluator', 'artifacts',
                       'external_dependencies', 'secret_refs'),
                ('schema_version', 'name', 'runner', 'variants', 'cases'), 'definition')
    if type(value['schema_version']) is not int or value['schema_version'] != SCHEMA_VERSION:
        raise ExperimentError('UNSUPPORTED_SCHEMA', 'Only experiment schema_version 1 is supported.')
    text(value['name'], 'name', maximum=64, pattern=NAME)
    runner = value['runner']
    object_keys(runner, ('type', 'argv'), ('type',), 'runner')
    if runner['type'] == 'command':
        if 'argv' not in runner:
            invalid('command runner requires argv')
        argv(runner['argv'], 'runner.argv')
    elif runner['type'] == 'codex':
        if set(runner) != {'type'}:
            invalid('Codex runner accepts only type; put model/effort/instructions_file on each variant')
    else:
        invalid('runner.type must be command or codex')
    workspace = value.setdefault('workspace', {})
    object_keys(workspace, ('include', 'exclude'), (), 'workspace')
    patterns(workspace.setdefault('include', ['**/*']), 'workspace.include')
    if not workspace['include']:
        invalid('workspace.include cannot be empty')
    patterns(workspace.setdefault('exclude', []), 'workspace.exclude')
    variants, cases = value['variants'], value['cases']
    for rows, label in ((variants, 'variants'), (cases, 'cases')):
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_TRIALS:
            invalid(label + ' must be a nonempty bounded list')
        ids = set()
        for row in rows:
            if not isinstance(row, dict):
                invalid(label + ' entries must be objects')
            identifier = text(row.get('id'), label + '.id', maximum=64, pattern=NAME)
            if identifier in ids:
                invalid(label + ' contains duplicate id ' + identifier)
            ids.add(identifier)
    for variant in variants:
        allowed = ('id', 'parameters', 'patch_file') if runner['type'] == 'command' else ('id', 'model', 'effort', 'instructions_file', 'patch_file')
        object_keys(variant, allowed, ('id',), 'variant ' + variant['id'])
        if runner['type'] == 'command':
            if not isinstance(variant.setdefault('parameters', {}), dict):
                invalid('command variant parameters must be an object')
            try:
                canonical(variant['parameters'])
            except (ValueError, TypeError, RecursionError, OverflowError):
                invalid('command parameters must contain finite JSON values')
        else:
            if 'model' in variant:
                text(variant['model'], 'variant.model', maximum=128, pattern=r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
            if 'effort' in variant:
                text(variant['effort'], 'variant.effort', maximum=32, pattern=r'[a-z][a-z0-9_-]{0,31}')
            if 'instructions_file' in variant:
                source_path(variant['instructions_file'], 'variant.instructions_file')
        if 'patch_file' in variant:
            source_path(variant['patch_file'], 'variant.patch_file')
    for case in cases:
        allowed = ('id', 'input_file', 'fixtures', 'prompt_file') if runner['type'] == 'codex' else ('id', 'input_file', 'fixtures')
        object_keys(case, allowed, ('id',), 'case ' + case['id'])
        if runner['type'] == 'codex' and 'prompt_file' not in case:
            invalid('each Codex case requires prompt_file')
        for key in ('input_file', 'prompt_file'):
            if key in case:
                source_path(case[key], 'case.' + key)
        fixtures = case.setdefault('fixtures', [])
        if not isinstance(fixtures, list) or len(fixtures) > 256:
            invalid('case.fixtures must be a bounded list')
        destinations = set()
        for fixture in fixtures:
            object_keys(fixture, ('source', 'destination'), ('source', 'destination'), 'fixture')
            source_path(fixture['source'], 'fixture.source')
            destination = source_path(fixture['destination'], 'fixture.destination')
            if any(destination == old or destination.startswith(old + '/') or old.startswith(destination + '/') for old in destinations):
                invalid('overlapping fixture destinations')
            destinations.add(destination)
    repeats = number(value.setdefault('repetitions', 1), 'repetitions', 1, MAX_TRIALS, integer=True)
    if len(variants) * len(cases) * repeats > MAX_TRIALS:
        invalid(f'trial expansion exceeds {MAX_TRIALS}')
    execution = value.setdefault('execution', {})
    object_keys(execution, ('max_parallel', 'trial_cpus', 'trial_memory_mib', 'trial_timeout_seconds',
                            'cpu_budget', 'memory_budget_mib'), (), 'execution')
    bounds = {'max_parallel': (1, 128, True), 'trial_cpus': (0.1, 64, False),
              'trial_memory_mib': (64, 65536, True), 'trial_timeout_seconds': (1, 86400, True),
              'cpu_budget': (0.1, 65536, False), 'memory_budget_mib': (64, 2**40, True)}
    for key, val in execution.items():
        low, high, integer = bounds[key]
        number(val, 'execution.' + key, low, high, integer=integer)
        if key in ('trial_cpus', 'cpu_budget') and abs(val * 100 - round(val * 100)) > 1e-7:
            invalid('CPU allocations support at most two decimal places')
    execution.setdefault('max_parallel', 1)
    evaluator = value.get('evaluator')
    if evaluator is not None:
        object_keys(evaluator, ('argv', 'source_paths', 'timeout_seconds'), ('argv', 'source_paths'), 'evaluator')
        argv(evaluator['argv'], 'evaluator.argv')
        if not isinstance(evaluator['source_paths'], list) or not 1 <= len(evaluator['source_paths']) <= 256:
            invalid('evaluator.source_paths must name its captured source files')
        for path in evaluator['source_paths']:
            source_path(path, 'evaluator source')
        number(evaluator.setdefault('timeout_seconds', 60), 'evaluator.timeout_seconds', 1, 3600, integer=True)
    patterns(value.setdefault('artifacts', ['outputs/**']), 'artifacts')
    external = value.setdefault('external_dependencies', [])
    if not isinstance(external, list) or len(external) > 256:
        invalid('external_dependencies must be a bounded list')
    for dependency in external:
        text(dependency, 'external dependency', maximum=1024)
    refs = value.setdefault('secret_refs', {})
    if not isinstance(refs, dict) or len(refs) > 64:
        invalid('secret_refs must be a bounded name-to-reference map')
    for name, reference in refs.items():
        text(name, 'secret name', maximum=64, pattern=NAME)
        text(reference, 'secret reference', maximum=64, pattern=r'[A-Z][A-Z0-9_]{0,63}')
    return value


def declared_inputs(definition):
    result = set()
    for row in definition['variants']:
        result.update(row[key] for key in ('patch_file', 'instructions_file') if key in row)
    for case in definition['cases']:
        result.update(case[key] for key in ('input_file', 'prompt_file') if key in case)
        result.update(fixture['source'] for fixture in case['fixtures'])
    if definition.get('evaluator'):
        result.update(definition['evaluator']['source_paths'])
    return sorted(result)


def expand(definition):
    return [{'variant_id': variant['id'], 'case_id': case['id'], 'repetition': repetition}
            for variant in definition['variants'] for case in definition['cases']
            for repetition in range(1, definition['repetitions'] + 1)]
