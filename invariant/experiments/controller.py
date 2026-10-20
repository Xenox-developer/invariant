"""Single-owner scheduler and workers over durable frozen command attempts."""
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
import uuid

from .. import environment, workspace
from ..execution import ExecutionContext, private_directory, project_lock
from .errors import ExperimentError, recovery_for
from .evaluation import parse_result
from .files import (apply_variant, capture_workspace, collect_artifacts, diff_entries,
                    materialize, prepare_inputs, write_file)
from .runners import CommandRunner, owned_info, stop_owned
from .runtime import secret_files
from .snapshots import regular_bytes, seal_tree
from .store import TERMINAL


class Controller:
    def __init__(self, engine, *, runner=None, native_runner=None):
        self.engine = engine
        self.store = engine.store
        self.runner = runner or CommandRunner()
        from .native import CodexRunner
        self.native_runner = native_runner or CodexRunner()
        self.cancel = threading.Event()
        self.token = 'controller-' + uuid.uuid4().hex

    def execute(self, identifier, *, owned_lock=False):
        study = self.engine.inspect(identifier)
        if study['kind'] != 'study':
            raise ExperimentError('INVALID_REQUEST', 'execute requires a study ID.')
        if all(trial['state'] in TERMINAL for trial in study['trials']):
            return study
        try:
            lock = None if owned_lock else project_lock(self.engine.root / 'controller', message='Another experiment controller is active for this project.')
        except RuntimeError as error:
            raise ExperimentError('CONTROLLER_BUSY', str(error), 'Inspect the active study; no attempt was submitted twice.') from error
        handlers = {}
        try:
            blockers = self.store.blockers()
            if blockers:
                raise ExperimentError('CONTROLLER_RECOVERY_REQUIRED', 'An earlier attempt is unfinished or its process stop is unconfirmed.',
                                      'Inspect saved attempts and their named containers. Do not replay them; Use invariant experiment recover STUDY_ID before dispatching more work.',
                                      details={'attempt_ids': [item['id'] for item in blockers]})
            study = self.engine.inspect(identifier)
            slots = self.preflight(study)
            frozen = study['frozen']
            self.control_path = self.engine.root / 'runtimes' / study['baseline_id'] / 'control'
            environment.prepare_control(self.engine.project, frozen['image']['id'], path=self.control_path)
            self.evaluator_source = self.control_path.parent / 'evaluator-source'
            if not self.evaluator_source.exists():
                baseline = self.engine.root / 'baselines' / study['baseline_id']
                manifest = json.loads((baseline / 'manifest.json').read_text())['entries']
                inputs = {path.removeprefix('@input/'): row for path, row in manifest.items() if path.startswith('@input/')}
                materialize(baseline / 'inputs', self.evaluator_source, inputs)
                seal_tree(self.evaluator_source)
            if threading.current_thread() is threading.main_thread():
                for signum in (signal.SIGINT, signal.SIGTERM):
                    handlers[signum] = signal.signal(signum, lambda *_: self.cancel.set())
            pending = deque(trial for trial in study['trials'] if trial['state'] == 'queued')
            active = {}
            with ThreadPoolExecutor(max_workers=slots, thread_name_prefix='invariant-trial') as pool:
                while pending or active:
                    if self.cancel.is_set():
                        self.store.cancel_queued(identifier)
                        pending.clear()
                    while pending and len(active) < slots and not self.cancel.is_set():
                        trial = pending.popleft()
                        attempt = self.store.claim(identifier, trial['id'], self.token)
                        if attempt is not None:
                            active[pool.submit(self.trial, study, trial, attempt)] = attempt['id']
                    if active:
                        done, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
                        for future in done:
                            active.pop(future)
                            result = future.result()
                            if not result['cleanup_confirmed']:
                                self.cancel.set()
            self.store.finish_study(identifier)
            return self.engine.inspect(identifier)
        finally:
            for signum, previous in handlers.items():
                signal.signal(signum, previous)
            if lock is not None:
                lock.close()

    def preflight(self, study):
        frozen = study['frozen']
        try:
            info = json.loads(workspace.docker('info', '--format', '{{json .}}', timeout=10))
            image = json.loads(workspace.docker('image', 'inspect', frozen['image']['id']))[0]
        except (RuntimeError, OSError, ValueError, IndexError) as error:
            raise ExperimentError('RUNTIME_UNAVAILABLE', 'The frozen image or Docker engine is unavailable.',
                                  'Restore the exact image/start Docker, then execute this queued study again.') from error
        if image.get('Id') != frozen['image']['id'] or info.get('OSType') != 'linux' or image.get('Os') != 'linux':
            raise ExperimentError('RUNTIME_MISMATCH', 'The frozen Linux image could not be verified.')
        requested = frozen['execution']
        try:
            cpus = float(info['NCPU']) * 0.75
            memory = int(info['MemTotal']) // (1024 ** 2) * 3 // 4
            slots = min(requested['max_parallel'], int((cpus + 1e-9) / requested['trial_cpus']),
                        memory // requested['trial_memory_mib'])
        except (KeyError, TypeError, ValueError):
            slots = 0
        if slots < 1:
            raise ExperimentError('CAPACITY_EXCEEDED', 'Current Docker capacity cannot fit one frozen trial with headroom.')
        return slots

    def context(self, study, attempt, phase, writable, state, image, inputs, *, output=None, command=None):
        name = 'invariant-exp-' + attempt['id'].removeprefix('attempt-') + '-' + phase
        frozen = study['frozen']
        baseline = self.engine.root / 'baselines' / study['baseline_id']
        mounts = [(inputs, '/__invariant_inputs/run')]
        if phase == 'evaluation':
            mounts.append((self.evaluator_source, '/__invariant_inputs/source'))
        extras = {'INVARIANT_VARIANT_FILE': '/__invariant_inputs/run/variant.json',
                  'INVARIANT_CASE_FILE': '/__invariant_inputs/run/case.json',
                  'INVARIANT_OUTPUT_DIR': '/workspace/outputs',
                  'INVARIANT_CANDIDATE_DIR': '/workspace' if phase == 'evaluation' else '',
                  'INVARIANT_EVALUATION_OUTPUT_DIR': '/__invariant_output' if output else '',
                  'INVARIANT_SECRET_FILES': '/__invariant_inputs/run/secrets.json' if phase == 'runner' else '',
                  'INVARIANT_ATTEMPT_ID': attempt['id']}
        extras['INVARIANT_PROMPT_FILE'] = '/__invariant_inputs/run/prompt.txt' if (inputs / 'prompt.txt').exists() else ''
        return ExecutionContext.create(project=self.engine.project, workspace=writable, state=state, image=image,
            owner_token=name, policy=frozen['effective_policy'], policy_directory=state / 'policy',
            control_directory=self.control_path, checkpoint_owner=attempt['id'] + '-' + phase,
            command=command, readonly_mounts=mounts, output_directory=output, command_environment=extras,
            working_directory='/__invariant_inputs/source' if phase == 'evaluation' else '/workspace')

    def trial(self, study, trial, attempt):
        started = time.time()
        frozen, definition = study['frozen'], study['frozen']['definition']
        baseline = self.engine.root / 'baselines' / study['baseline_id']
        folder = self.engine.root / 'attempts' / attempt['id']
        contexts, secrets, secret_paths = [], [], []
        record = {**attempt['record'], 'started_at': started, 'cleanup_confirmed': True,
                  'runner': {'status': 'not_started'}, 'evaluation': {'status': 'not_run'},
                  'capture': {'complete': False}, 'checkpoint': {'complete': False},
                  'resources': frozen['execution'], 'base_image': frozen['image']['id']}
        cancel = AttemptCancellation(self.cancel, self.store, attempt['id'])
        terminal, reason = 'failed', 'PREPARATION_FAILED'
        def intent(context, operation):
            phase = 'evaluation' if context.owner_token.endswith('-evaluation') else 'runner'
            record.setdefault('intents', {})[phase] = {
                'container': context.owner_token, 'owner': context.checkpoint_owner,
                'state_directory': str(context.state), 'workspace': str(context.workspace),
                'image': context.image, 'operation': operation, 'updated_at': time.time()}
            self.store.update_attempt(attempt['id'], 'evaluating' if phase == 'evaluation' else
                                      'saving' if operation == 'checkpoint' else 'preparing', record)

        try:
            private_directory(folder.parent)
            folder.mkdir(mode=0o700)
            free = shutil.disk_usage(folder).free
            manifest = json.loads((baseline / 'manifest.json').read_text())
            needed = manifest['source_bytes'] * 3 + 64 * 1024 ** 2
            if free < needed:
                raise ExperimentError('DISK_HEADROOM', 'Insufficient host disk for workspace/evaluation copies.')
            record['disk'] = {'host_free_before': free, 'estimated_workspace_bytes': needed,
                              'daemon_free_bytes': None, 'hard_quota': False}
            writable = folder / 'workspace'
            materialize(baseline / 'workspace', writable, manifest['entries'])
            variant = next(row for row in definition['variants'] if row['id'] == trial['variant_id'])
            case = next(row for row in definition['cases'] if row['id'] == trial['case_id'])
            apply_variant(baseline, writable, variant, case)
            inputs = folder / 'input'
            prepare_inputs(baseline, frozen, variant, case, inputs)
            from .lifecycle import references as saved_references
            references = secret_files(definition, environ=saved_references(self.engine, study))
            mapping = {}
            for (name, _), source in zip(definition['secret_refs'].items(), references):
                value, _ = regular_bytes(source.parent, source.name, maximum=65536)
                secrets.extend([value, value.strip()])
                target = inputs / ('secret-' + name)
                write_file(target, value, 0o400)
                secret_paths.append(target)
                mapping[name] = '/__invariant_inputs/run/' + target.name
            workspace.private_write(inputs / 'secrets.json', mapping)
            seal_tree(inputs)
            state = private_directory(folder / 'control')
            private_directory(state / 'policy')
            workspace.private_write(state / 'policy/config.json', frozen['effective_policy'])
            runner_context = self.context(study, attempt, 'runner', writable, state, frozen['image']['id'], inputs,
                                          command=definition['runner'].get('argv'))
            contexts.append(runner_context)
            def running():
                record['runner'] = {'status': 'running', 'started_at': time.time(), 'container': runner_context.owner_token}
                self.store.update_attempt(attempt['id'], 'running', record)
            intent(runner_context, 'create')
            options = dict(timeout=frozen['execution']['trial_timeout_seconds'], cancel=cancel,
                log_path=folder / 'runner.log', limit=frozen['capture_limits']['log_bytes'],
                secrets=secrets, on_started=running, on_intent=lambda operation: intent(runner_context, operation),
                on_log=lambda kind, data: self.store.append_log(study['id'], attempt['id'], 'runner', kind, data))
            if definition['runner']['type'] == 'codex':
                prompt, _ = regular_bytes(baseline / 'inputs', case['prompt_file'], maximum=frozen['capture_limits']['source_bytes'])
                instructions = ''
                if variant.get('instructions_file'):
                    instructions = regular_bytes(baseline / 'inputs', variant['instructions_file'], maximum=frozen['capture_limits']['source_bytes'])[0].decode('utf-8')
                def native_state(value):
                    record['runner']['native'] = value
                    self.store.update_attempt(attempt['id'], 'running', record)
                record['runner'] = self.native_runner.run(runner_context, variant=variant,
                    prompt=prompt.decode('utf-8'), instructions=instructions, home=folder / 'native-home',
                    on_native=native_state, **options)
            else:
                record['runner'] = self.runner.run(runner_context, **options)
            if not record['runner']['cleanup_confirmed']:
                raise ExperimentError('UNRESOLVED_EXECUTION', 'Runner process termination could not be confirmed.')
            for path in secret_paths:
                path.parent.chmod(0o700); path.unlink(missing_ok=True)
            secret_paths.clear()
            seal_tree(inputs)
            self.store.update_attempt(attempt['id'], 'saving', record)
            snapshot_root = private_directory(folder / 'snapshot')
            try:
                saved = capture_workspace(writable, snapshot_root / 'workspace', frozen['capture_limits'])
            except OSError as error:
                raise ExperimentError('CAPTURE_FAILED', 'The stopped workspace could not be captured; it was retained.') from error
            workspace.private_write(snapshot_root / 'manifest.json', saved)
            record['workspace_diff'] = diff_entries(manifest['entries'], saved['entries'])
            record['capture'] = {'complete': True, 'workspace': str(snapshot_root / 'workspace'),
                                 'manifest': str(snapshot_root / 'manifest.json'), 'content_digest': saved['content_digest']}
            record['artifacts'] = collect_artifacts(snapshot_root / 'workspace', saved['entries'], definition['artifacts'],
                                                     folder / 'artifacts', frozen['capture_limits']['artifact_bytes'])
            # Stop was independently verified before copying files or committing.
            try:
                intent(runner_context, 'checkpoint')
                runner_context.preserve(runner_context.owner_token)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                raise ExperimentError('CHECKPOINT_FAILED', 'The stopped environment could not be saved; it was retained.') from error
            checkpoint = environment.read_record(runner_context.checkpoint_path)
            if checkpoint is None and frozen['effective_policy']['workspace_access'] == 'read-write':
                raise ExperimentError('CHECKPOINT_FAILED', 'No saved image was recorded; a fresh environment was not substituted.')
            candidate_image = checkpoint['image'] if checkpoint else frozen['image']['id']
            record['checkpoint'] = {'complete': True, 'image': candidate_image,
                                    'owner': runner_context.checkpoint_owner, 'record': str(runner_context.checkpoint_path) if checkpoint else None}
            size = json.loads(workspace.docker('image', 'inspect', candidate_image))[0].get('Size')
            record['disk'].update(workspace_bytes=saved['source_bytes'], artifact_bytes=record['artifacts']['bytes'],
                                   image_logical_bytes=size, image_size_includes_shared_layers=True)
            seal_tree(snapshot_root)
            if record['runner']['status'] == 'completed' and definition.get('evaluator') and not cancel.is_set():
                self.store.update_attempt(attempt['id'], 'evaluating', record)
                record['evaluation'] = {'status': 'running', 'passed': None, 'metrics': {}}
                record['evaluation'] = self.evaluate(study, attempt, folder, inputs, candidate_image, saved, contexts, secrets, cancel, intent)
            elif not definition.get('evaluator'):
                record['evaluation'] = {'status': 'absent', 'passed': None, 'metrics': {}}
            reason = record['runner'].get('reason')
            if cancel.is_set() or record['runner']['status'] == 'cancelled':
                terminal, reason = 'cancelled', 'CANCELLED'
            elif record['runner']['status'] != 'completed':
                terminal, reason = 'failed', reason or 'PROCESS_EXIT'
            elif record['evaluation']['status'] not in ('completed', 'absent'):
                terminal, reason = 'failed', record['evaluation'].get('reason', 'EVALUATION_FAILED')
            else:
                terminal, reason = 'succeeded', 'COMPLETED'
        except Exception as error:
            reason = getattr(error, 'code', 'RUNTIME_FAILED')
            record['error_type'] = type(error).__name__
            if cancel.is_set() and reason != 'UNRESOLVED_EXECUTION':
                terminal, reason = 'cancelled', 'CANCELLED'
        finally:
            clean = True
            for context in contexts:
                try:
                    stopped = stop_owned(context)
                    clean &= stopped
                    # Failed saves keep the stopped candidate for future recovery.
                    if context.owner_token.endswith('-evaluation') and stopped:
                        workspace.remove_owned(context.owner_token)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    clean = False
            record['cleanup_confirmed'] = clean
            if not clean:
                terminal, reason = 'interrupted', 'UNRESOLVED_EXECUTION'
            for path in secret_paths:
                try:
                    path.parent.chmod(0o700); path.unlink(missing_ok=True)
                except OSError:
                    # Never advertise complete credential cleanup if the private temp file remains.
                    record['secret_cleanup_pending'] = True
            record.update(finished_at=time.time(), duration_seconds=time.time() - started, reason=reason,
                          recovery=recovery_for(reason) if terminal != 'succeeded' else None)
            if folder.exists():
                workspace.private_write(folder / 'result.json', record)
            self.store.update_attempt(attempt['id'], terminal, record, reason)
        return record

    def evaluate(self, study, attempt, folder, inputs, image, saved, contexts, secrets, cancel, intent):
        evaluator = study['frozen']['definition']['evaluator']
        evaluation = private_directory(folder / 'evaluation')
        writable = evaluation / 'workspace'
        materialize(folder / 'snapshot/workspace', writable, saved['entries'])
        state = private_directory(evaluation / 'control')
        private_directory(state / 'policy')
        workspace.private_write(state / 'policy/config.json', study['frozen']['effective_policy'])
        output = private_directory(evaluation / 'output')
        context = self.context(study, attempt, 'evaluation', writable, state, image, inputs,
                               output=output, command=evaluator['argv'])
        contexts.append(context)
        intent(context, 'create')
        outcome = self.runner.run(context, timeout=evaluator['timeout_seconds'], cancel=cancel,
                                  log_path=folder / 'evaluation.log', limit=study['frozen']['capture_limits']['event_bytes'],
                                  secrets=secrets, kill_on_limit=True, on_intent=lambda operation: intent(context, operation),
                                  on_log=lambda kind, data: self.store.append_log(study['id'], attempt['id'], 'evaluation', kind, data))
        if outcome['status'] != 'completed' or outcome['log_truncated']:
            return {**outcome, 'status': 'failed', 'reason': outcome['reason'] or 'OUTPUT_LIMIT', 'passed': None, 'metrics': {}}
        try:
            value = parse_result((folder / 'evaluation.log').read_bytes())
            # Validate references against a stopped, bounded copy without following links.
            captured = capture_workspace(output, evaluation / 'saved-output', study['frozen']['capture_limits'])
            for reference in value['artifacts']:
                if captured['entries'].get(reference, {}).get('type') != 'file':
                    raise ExperimentError('INVALID_EVALUATION', 'Evaluator artifact must be an existing regular output file.')
            artifacts = collect_artifacts(evaluation / 'saved-output', captured['entries'], value['artifacts'],
                                          evaluation / 'artifacts', study['frozen']['capture_limits']['artifact_bytes'])
            if not artifacts['complete']:
                raise ExperimentError('EVALUATION_ARTIFACT_LIMIT', 'Evaluator artifacts exceeded the capture limit.')
            return {**outcome, **value, 'status': 'completed', 'artifacts': artifacts}
        except ExperimentError as error:
            return {**outcome, 'status': 'failed', 'reason': error.code, 'passed': None, 'metrics': {}}


class AttemptCancellation:
    """Persisted stop requests remain effective across clients and controller restarts."""
    def __init__(self, parent, store, attempt_id):
        self.parent, self.store, self.attempt_id = parent, store, attempt_id

    def is_set(self):
        return self.parent.is_set() or self.store.cancelled(self.attempt_id)
