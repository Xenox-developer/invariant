"""One creation/inspection interface for interactive and machine experiment callers."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

from ..execution import private_directory, project_lock
from .. import workspace
from .definitions import (LIMITS, MAX_DEFINITION, canonical, digest, expand,
                          parse_json, source_path, text, validate)
from .errors import ExperimentError
from .patches import validate_inputs
from .runtime import LocalRuntime, allocations, secret_files
from .snapshots import SourceCapture, protected, regular_bytes, seal_tree
from .store import StudyStore


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_tree(root):
    for base, _, _ in os.walk(root, topdown=False, followlinks=False):
        sync_directory(base)


class ExperimentEngine:
    def __init__(self, project=None, *, runtime=None, state=None, clock=time.time):
        self.project = Path(project or Path.cwd()).resolve()
        try:
            workspace.load_project(self.project)
        except RuntimeError as error:
            raise ExperimentError('PROJECT_NOT_READY', str(error), 'Run invariant init in the project first.') from error
        self.state = Path(state) if state is not None else workspace.state_for(self.project)
        if self.state.resolve().is_relative_to(self.project):
            raise ExperimentError('UNSAFE_STATE', 'Experiment state must live outside the original project.')
        self.root = self.state / 'experiments'
        self.store = StudyStore(self.root, project=self.project)
        self.runtime = runtime or LocalRuntime()
        self.clock = clock

    def _path(self, path):
        path = Path(path)
        absolute = path if path.is_absolute() else self.project / path
        try:
            relative = absolute.relative_to(self.project).as_posix()
        except ValueError:
            raise ExperimentError('INVALID_PATH', 'The experiment definition must be inside this project.') from None
        source_path(relative, 'definition path')
        if protected(relative):
            raise ExperimentError('INVALID_PATH', 'Use a shareable definition outside protected .invariant/credential paths.')
        return relative

    def _definition(self, path):
        relative = self._path(path)
        try:
            raw, _ = regular_bytes(self.project, relative, maximum=MAX_DEFINITION)
        except FileNotFoundError:
            raise ExperimentError('INPUT_MISSING', 'Experiment definition does not exist: ' + relative) from None
        original = parse_json(raw, 'experiment definition')
        definition = validate(copy.deepcopy(original))
        fingerprint = digest({'operation': 'create_study', 'project': str(self.project),
                              'definition_path': relative, 'definition': original})
        return relative, original, definition, fingerprint

    def _capture(self, relative, original, definition, directory):
        policy = workspace.load_project(self.project)
        if (self.state / 'current.json').exists():
            raise ExperimentError('WORKSPACE_RECOVERY_REQUIRED', 'An ordinary chat has a current/recovery record.',
                                  'Finish or recover it with invariant run/resume before capturing experiments.')
        secrets = secret_files(definition)
        prepared = self.runtime.resolve(self.project, self.state, definition, policy)
        execution = allocations(definition, prepared)
        capture = SourceCapture(self.project, definition, secret_paths=secrets, extra_inputs=[relative])
        manifest = capture.capture(directory)
        captured_recipe = parse_json((directory / 'inputs' / relative).read_bytes(), 'captured definition')
        if canonical(captured_recipe) != canonical(original):
            raise ExperimentError('SOURCE_CHANGED', 'The experiment definition changed during preparation.',
                                  'Stop the writer and submit again with a new request if desired.')
        input_checks = validate_inputs(definition, directory)
        baseline = {'schema_version': 1, 'project': str(self.project), 'manifest': manifest,
                    'image': prepared['image'], 'policy': execution['trial_policy']}
        baseline_id = 'baseline-' + digest(baseline)
        effective = copy.deepcopy(definition)
        effective['variants'] = copy.deepcopy(prepared['variants'])
        frozen = {'schema_version': 1, 'milestone': 'M1', 'definition': effective,
                  'requested_definition': original, 'definition_path': relative,
                  'definition_digest': digest(effective), 'baseline_id': baseline_id,
                  'baseline_content_digest': manifest['content_digest'],
                  'inputs': {name.removeprefix('@input/'): row['sha256'] for name, row in manifest['entries'].items() if name.startswith('@input/')},
                  'image': prepared['image'], 'runtime': prepared['runtime'],
                  'project_policy': policy, 'effective_policy': execution['trial_policy'],
                  'execution': execution, 'resource_sizing': prepared['resource_sizing'],
                  'capacity_at_creation': prepared['capacity'], 'capture_limits': LIMITS,
                  'input_checks': input_checks, 'external_state_notice': bool(definition['external_dependencies'])}
        workspace.private_write(directory / 'manifest.json', manifest)
        workspace.private_write(directory / 'baseline.json', baseline)
        sync_tree(directory)
        return baseline_id, manifest, frozen

    def _lock(self, *, reconcile=True):
        try:
            lock = project_lock(self.state, timeout=3, reconcile=reconcile,
                                message='An ordinary chat or source capture holds the project lock.')
            marker = self.root / 'apply-active.json'
            if not reconcile and (marker.exists() or marker.is_symlink()):
                lock.close()
                raise ExperimentError('APPLY_RECOVERY_REQUIRED','An interrupted apply needs reconciliation.',
                    'Run invariant experiment apply --recover, then repeat this read-only capture/preview. No project files were changed.')
            return lock
        except ExperimentError:
            raise
        except RuntimeError as error:
            raise ExperimentError('PROJECT_BUSY', str(error),
                                  'Finish the active chat/capture and retry; existing study records were not changed.') from error

    def validate(self, path):
        relative, original, definition, _ = self._definition(path)
        lock = self._lock(reconcile=False)
        try:
            with tempfile.TemporaryDirectory(prefix='invariant-validation-') as temporary:
                _, manifest, frozen = self._capture(relative, original, definition, Path(temporary))
        finally:
            lock.close()
        return {'valid': True, 'trial_count': len(expand(definition)), 'frozen_preview': frozen,
                'capture': {'source_bytes': manifest['source_bytes'], 'entry_count': len(manifest['entries']),
                            'excluded': manifest['excluded'], 'mandatory_exclusions': manifest['mandatory_exclusions']},
                'dispatch_available': True,
                'execution_notice': 'Command and native Codex studies support detached execution and explicit recovery.'}

    def submit(self, path, *, request_id=None):
        request_id = request_id or str(uuid.uuid4())
        text(request_id, 'request ID', maximum=128, pattern=r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
        relative, original, definition, fingerprint = self._definition(path)
        existing = self.store.lookup_request(request_id, fingerprint)
        if existing:
            return {**self.inspect(existing), 'idempotent_replay': True}
        lock = self._lock(reconcile=False)
        capture_dir = None
        try:
            existing = self.store.lookup_request(request_id, fingerprint)
            if existing:
                return {**self.inspect(existing), 'idempotent_replay': True}
            private_directory(self.root)
            staging = private_directory(self.root / 'staging')
            capture_dir = staging / ('capture-' + uuid.uuid4().hex)
            capture_dir.mkdir(mode=0o700)
            baseline_id, manifest, frozen = self._capture(relative, original, definition, capture_dir)
            baselines = private_directory(self.root / 'baselines')
            baseline_path = baselines / baseline_id
            if baseline_path.exists():
                if baseline_path.is_symlink() or (baseline_path / 'manifest.json').read_bytes() != (capture_dir / 'manifest.json').read_bytes():
                    raise ExperimentError('CORRUPT_STATE', 'Existing baseline does not match the captured content.')
                shutil.rmtree(capture_dir)
                capture_dir = None
            else:
                capture_dir.rename(baseline_path)
                capture_dir = None
                seal_tree(baseline_path)
                sync_tree(baseline_path)
                sync_directory(baselines)
            study_id = 'study-' + uuid.uuid4().hex
            study_dir = private_directory(self.root / 'studies') / study_id
            study_dir.mkdir(mode=0o700)
            workspace.private_write(study_dir / 'definition.json', frozen)
            sync_tree(study_dir)
            seal_tree(study_dir)
            sync_directory(study_dir.parent)
            created = self.store.create(study_id=study_id,
                                        baseline={'id': baseline_id, 'content_digest': manifest['content_digest'], 'path': str(baseline_path)},
                                        frozen=frozen, trials=expand(definition), request_id=request_id,
                                        fingerprint=fingerprint, project=self.project, now=self.clock())
            sync_directory(self.root)
            return {**self.inspect(created), 'idempotent_replay': False}
        except BaseException as error:
            # A database rollback cannot undo filesystem publication. Preserve and
            # identify staged/orphaned data instead of pretending it was committed.
            try:
                diagnostics = private_directory(self.root / 'preparation-failures')
                diagnostic_id = 'failure-' + uuid.uuid4().hex
                facts = {'schema_version': 1, 'request_id': request_id, 'time': self.clock(),
                         'error_code': getattr(error, 'code', 'PREPARATION_FAILED'),
                         'staged_path': str(capture_dir) if capture_dir is not None else None,
                         'published_baseline': locals().get('baseline_id'), 'published_study': locals().get('study_id'),
                         'notice': 'Unreferenced files are not an accepted study and were retained for reconciliation.'}
                workspace.private_write(diagnostics / (diagnostic_id + '.json'), facts)
                if isinstance(error, ExperimentError):
                    error.details['preparation_failure_id'] = diagnostic_id
            except OSError:
                pass
            raise
        finally:
            lock.close()

    def inspect(self, identifier=None, *, request_id=None):
        if request_id is not None:
            if identifier is not None:
                raise ExperimentError('INVALID_REQUEST', 'Choose an identifier or a request ID, not both.')
            operation = self.store.operation(request_id) if self.store.path.exists() else None
            if operation is not None:
                return {'kind': 'operation', 'id': request_id, 'state': 'pending' if operation.get('operation_pending') else 'accepted', **operation}
            identifier = self.store.lookup_request(request_id)
            if identifier is None:
                raise ExperimentError('NOT_FOUND', 'No accepted study has this request ID.',
                                      'The operation may not have committed; retry the original request with the same content.')
        if not identifier:
            raise ExperimentError('INVALID_REQUEST', 'status requires an identifier or --request-id.')
        result = self.store.inspect(identifier)
        if result['kind'] == 'study':
            self._verify_saved(result)
            from ..environment import read_record
            result['controller_lease'] = read_record(self.root / 'controller/lease.json')
        return result

    def _verify_saved(self, record):
        try:
            relative = 'studies/' + record['id'] + '/definition.json'
            raw, _ = regular_bytes(self.root, relative, maximum=8 * MAX_DEFINITION)
            if digest(parse_json(raw)) != record['definition_hash']:
                raise ValueError()
            baseline_id = record['baseline_id']
            if not baseline_id.startswith('baseline-') or len(baseline_id) != 73:
                raise ValueError()
            base_relative = 'baselines/' + baseline_id
            raw, _ = regular_bytes(self.root, base_relative + '/baseline.json')
            baseline = parse_json(raw)
            raw, _ = regular_bytes(self.root, base_relative + '/manifest.json')
            manifest = parse_json(raw)
            if ('baseline-' + digest(baseline) != baseline_id or baseline['manifest'] != manifest
                    or manifest['content_digest'] != record['frozen']['baseline_content_digest']):
                raise ValueError()
        except (OSError, ValueError, KeyError, ExperimentError):
            raise ExperimentError('CORRUPT_STATE', 'Saved study files are missing or differ from their recorded digests.',
                                  'Restore the private saved state from backup; a new baseline was not substituted.') from None
        record['capture_integrity'] = 'metadata_verified'
        record['baseline_files_notice'] = 'Full content was verified at capture; status verifies metadata, not every saved file.'

    def list(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ExperimentError('INVALID_REQUEST', 'list limit must be an integer between 1 and 1000.')
        return {'studies': self.store.list(limit), 'command_execution_available': True,
                'codex_execution_available': True,
                'execution_notice': 'Command and native Codex execution support detached dispatch, stop, recovery, retry, resumable logs and selection.'}

    def execute(self, identifier):
        from .controller import Controller
        return Controller(self).execute(identifier)

    def continue_result(self, identifier, prompt_file, *, request_id=None):
        from .continuation import create
        return create(self, identifier, prompt_file, request_id=request_id)

    def diff(self, identifier):
        from .results import describe
        return describe(self, identifier)

    def plan_apply(self, identifier, *, request_id=None):
        from .applying import plan
        return plan(self, identifier, request_id=request_id)

    def apply(self, plan_id, *, request_id=None):
        from .applying import apply
        return apply(self, plan_id, request_id=request_id)

    def recover_apply(self, *, request_id=None):
        from .applying import recover
        return recover(self, request_id=request_id)

    def launch(self, identifier, *, request_id=None):
        from .lifecycle import launch
        return launch(self, identifier, request_id=request_id)

    def select(self, identifier, *, revision, request_id=None, clear=False):
        from .selection import select
        return select(self, identifier, revision=revision, request_id=request_id, clear=clear)

    def stop(self, identifier, *, request_id=None):
        from .lifecycle import stop
        return stop(self, identifier, request_id=request_id)

    def recover(self, identifier, *, request_id=None):
        from .lifecycle import recover
        return recover(self, identifier, request_id=request_id)

    def retry(self, identifier, *, request_id=None, dispatch=True):
        from .lifecycle import request_key
        result = self.store.retry(identifier, request_id=request_key(request_id))
        if dispatch:
            self.launch(result['study_id'])
        return result

    def delete(self, identifier, *, confirm=False, request_id=None):
        from .deletion import delete
        return delete(self, identifier, confirm=confirm, request_id=request_id)

    def log_frames(self, identifier, *, after=None, follow=False):
        from .logs import frames
        return frames(self, identifier, after=after, follow=follow)

    def results(self, identifier):
        from .evaluation import compare
        study = self.inspect(identifier)
        if study['kind'] != 'study':
            raise ExperimentError('INVALID_REQUEST', 'results requires a study ID.')
        return compare(study)

    def logs(self, identifier, *, tail_bytes=65536):
        if type(tail_bytes) is not int or not 1 <= tail_bytes <= 1024 * 1024:
            raise ExperimentError('INVALID_REQUEST', 'tail-bytes must be between 1 and 1048576.')
        item = self.inspect(identifier)
        attempts = [attempt for trial in item['trials'] for attempt in trial['attempts']] if item['kind'] == 'study' else item['attempts'] if item['kind'] == 'trial' else [item]
        logs = []
        for attempt in attempts:
            for phase in ('runner', 'evaluation'):
                path = self.root / 'attempts' / attempt['id'] / (phase + '.log')
                if not path.exists():
                    continue
                with open(path, 'rb', opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW)) as stream:
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    offset = max(0, size - tail_bytes)
                    stream.seek(offset)
                    content = stream.read(tail_bytes).decode('utf-8', errors='replace')
                logs.append({'attempt_id': attempt['id'], 'phase': phase, 'text': content,
                             'offset': offset, 'file_bytes': size, 'tail_limited': offset > 0})
        return {'identifier': identifier, 'logs': logs, 'follow_available': True}

    def init_definition(self, path, *, request_id=None, runner='command'):
        from .starters import create
        return create(self, path, request_id=request_id, runner=runner)
