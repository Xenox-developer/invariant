"""Detached dispatch, conservative recovery and scoped process controls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

from .. import environment, workspace
from ..execution import private_directory, project_lock
from .definitions import text
from .definitions import digest
from .errors import ExperimentError
from .files import capture_workspace
from .runners import owned_info, stop_owned
from .runtime import secret_files
from .snapshots import seal_tree
from .store import TERMINAL


def request_key(value):
    if value is not None:
        text(value, 'request ID', maximum=128, pattern=r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
    return value


def references(engine, study, *, update=False):
    path = engine.root / 'references' / (study['id'] + '.json')
    saved = environment.read_record(path) or {}
    values = {}
    for reference in study['frozen']['definition']['secret_refs'].values():
        key = 'INVARIANT_SECRET_' + reference
        if key in os.environ or key in saved:
            values[key] = os.environ.get(key, saved.get(key)) if update else saved.get(key, os.environ.get(key))
    if update:
        secret_files(study['frozen']['definition'], environ=values)
        private_directory(path.parent)
        workspace.private_write(path, values)  # References only, never credential contents.
    return values


def launch(engine, identifier, *, request_id=None):
    request_id = request_key(request_id)
    fingerprint = digest({'operation': 'execute', 'identifier': identifier})
    replay = engine.store.lookup_operation(request_id, fingerprint)
    if replay is not None and not replay.get('operation_pending'):
        return replay
    study = engine.inspect(identifier)
    if study['kind'] != 'study':
        raise ExperimentError('INVALID_REQUEST', 'execute requires a study ID.')
    if all(trial['state'] in TERMINAL for trial in study['trials']):
        return engine.store.control_operation(request_id, fingerprint, study, complete=True)
    references(engine, study, update=True)
    engine.store.control_operation(request_id, fingerprint)
    engine.store.dispatch(identifier)
    wake(engine)
    return engine.store.control_operation(request_id, fingerprint, engine.inspect(identifier), complete=True)


def wake(engine):
    directory = private_directory(engine.root / 'controller')
    fd = os.open(directory / 'launcher.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        package = str(Path(__file__).resolve().parents[2])
        bootstrap = ('import sys; sys.path.insert(0, sys.argv[1]); '
                     'from invariant.experiments.engine import ExperimentEngine; '
                     'from invariant.experiments.lifecycle import serve; '
                     'serve(ExperimentEngine(sys.argv[2], state=sys.argv[3]))')
        # The project may contain invariant.py/sitecustomize.py. Never import
        # controller code from that writable directory or inherited PYTHONPATH.
        subprocess.Popen([sys.executable, '-I', '-c', bootstrap, package,
                          str(engine.project), str(engine.state)],
                         cwd=package, env=os.environ.copy(), stdin=subprocess.DEVNULL,
                         stdout=fd, stderr=fd, start_new_session=True, close_fds=True)
    except OSError as error:
        raise ExperimentError('CONTROLLER_START_FAILED', 'Study is queued but its controller could not be started.',
                              'Use execute STUDY_ID after fixing the Python installation.') from error
    finally:
        os.close(fd)


def serve(engine):
    """The kernel lease is authoritative; heartbeat age never authorizes replay."""
    directory = private_directory(engine.root / 'controller')
    try:
        lock = project_lock(directory, timeout=5, message='An experiment controller is already active.')
    except RuntimeError:
        return
    done = threading.Event()
    token = 'controller-' + uuid.uuid4().hex
    def heartbeat():
        while not done.is_set():
            workspace.private_write(directory / 'lease.json',
                {'schema_version': 1, 'pid': os.getpid(), 'token': token, 'heartbeat_at': time.time(), 'state': 'active'})
            done.wait(1)
    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    try:
        # Never dispatch a row whose prior controller may have launched it.
        with engine.store.connection(write=True) as database:
            rows = database.execute("SELECT study_id FROM dispatches WHERE state='running'").fetchall()
            for row in rows:
                database.execute("UPDATE dispatches SET state='recovery_required',reason='CONTROLLER_LOST' WHERE study_id=?", (row['study_id'],))
        while True:
            identifier = engine.store.next_dispatch()
            if identifier is None:
                break
            engine.store.dispatch_state(identifier, 'running')
            try:
                from .controller import Controller
                controller = Controller(engine)
                controller.token = token
                controller.execute(identifier, owned_lock=True)
                engine.store.dispatch_state(identifier, 'completed')
            except (ExperimentError, OSError, RuntimeError, subprocess.SubprocessError) as error:
                reason = getattr(error, 'code', 'CONTROLLER_FAILED')
                engine.store.dispatch_state(identifier, 'recovery_required', reason,
                    error=error.record() if isinstance(error, ExperimentError) else None)
    finally:
        done.set(); pulse.join(timeout=2)
        workspace.private_write(directory / 'lease.json',
            {'schema_version': 1, 'pid': os.getpid(), 'token': token, 'heartbeat_at': time.time(), 'state': 'stopped'})
        lock.close()


def identities(engine, attempt):
    """Only immutable attempt identities can name containers; saved paths are checked."""
    folder = engine.root / 'attempts' / attempt['id']
    for phase, subpath in (('runner', 'control'), ('evaluation', 'evaluation/control')):
        name = 'invariant-exp-' + attempt['id'].removeprefix('attempt-') + '-' + phase
        yield phase, SimpleNamespace(project=engine.project, owner_token=name,
                    checkpoint_owner=attempt['id'] + '-' + phase, state=folder / subpath)


def stop(engine, identifier, *, request_id=None):
    accepted = engine.store.stop(identifier, request_id=request_key(request_id))
    deadline = time.monotonic() + 10
    unresolved = set(accepted['attempt_ids'])
    errors = {}
    while unresolved and time.monotonic() < deadline:
        for attempt_id in tuple(unresolved):
            attempt = engine.store.inspect(attempt_id)
            if attempt['state'] in TERMINAL and attempt['record'].get('cleanup_confirmed', True):
                unresolved.remove(attempt_id)
                continue
            try:
                # Direct stop also works when the controller has died. The worker
                # independently observes cancellation before/through Docker start.
                for _, identity in identities(engine, attempt):
                    if not stop_owned(identity):
                        raise RuntimeError('Process still running')
                if attempt['state'] == 'saving' and attempt['record'].get('intents', {}).get('runner', {}).get('operation') == 'checkpoint':
                    unresolved.remove(attempt_id)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                errors[attempt_id] = getattr(error, 'code', 'DOCKER_UNAVAILABLE')
        if unresolved:
            time.sleep(.1)
    engine.store.finish_study(accepted['study_id']) if not unresolved and all(
        row['state'] in TERMINAL for row in engine.inspect(accepted['study_id'])['trials']) else None
    return {**accepted, 'termination_confirmed': not unresolved, 'unresolved_attempt_ids': sorted(unresolved),
            'errors': errors, 'recovery': 'Use recover STUDY_ID if the controller has stopped; retry is always explicit.'}


def recover(engine, identifier, *, request_id=None):
    request_key(request_id)
    study = engine.inspect(identifier)
    if study['kind'] != 'study':
        raise ExperimentError('INVALID_REQUEST', 'recover requires a study ID.')
    replay = engine.store.recovery_operation(identifier, request_id, lookup_only=True)
    if replay is not None:
        if replay.get('queued_dispatched'):
            wake(engine)
        return replay
    try:
        lock = project_lock(engine.root / 'controller', message='The controller is active; stop the study first.')
    except RuntimeError as error:
        raise ExperimentError('CONTROLLER_BUSY', str(error), 'Wait for stop to finish, then recover.') from error
    recovered, unresolved, errors = [], [], {}
    try:
        replay = engine.store.recovery_operation(identifier, request_id)
        if replay is not None:
            if replay.get('queued_dispatched'):
                wake(engine)
            return replay
        study = engine.inspect(identifier)
        for trial in study['trials']:
            for attempt in trial['attempts']:
                record = attempt['record']
                if attempt['state'] == 'queued' or (attempt['state'] in TERMINAL and record.get('cleanup_confirmed') is not False
                        and (record.get('recovery_complete') or record.get('checkpoint', {}).get('complete', True))):
                    continue
                clean, checkpoints, recovery_errors = True, [], []
                folder = engine.root / 'attempts' / attempt['id']
                for phase, identity in identities(engine, attempt):
                    operation = 'stop'
                    try:
                        if not stop_owned(identity, inspect_timeout=10):
                            clean = False
                            continue
                        info = owned_info(identity)
                        if info is not None and phase == 'runner':
                            # Saving then removing the exact container also fences an
                            # orphaned Docker start request. Copy bind-mounted files
                            # only after the container can no longer start or write.
                            operation = 'checkpoint'
                            environment.preserve(identity.owner_token, identity.state, engine.project,
                                                 lambda *args, **kwargs: None, owner=identity.checkpoint_owner)
                            if owned_info(identity) is not None:
                                raise RuntimeError('Recovered container removal is unconfirmed')
                        elif info is not None:
                            workspace.remove_owned(identity.owner_token)
                        if phase == 'runner' and not record.get('capture', {}).get('complete') and not record.get('recovered_capture'):
                            writable = folder / 'workspace'
                            if writable.is_dir():
                                target = folder / ('recovered-' + uuid.uuid4().hex)
                                private_directory(target)
                                try:
                                    manifest = capture_workspace(writable, target / 'workspace', study['frozen']['capture_limits'])
                                    workspace.private_write(target / 'manifest.json', manifest)
                                    seal_tree(target)
                                    record['recovered_capture'] = str(target)
                                    record.pop('recovery_capture_pending', None)
                                except (OSError, ExperimentError) as error:
                                    record['recovery_capture_pending'] = True
                                    clean = False
                                    recovery_errors.append({'phase': phase, 'operation': 'capture',
                                        'code': 'CAPTURE_FAILED', 'error_type': type(error).__name__})
                        checkpoint = environment.read_record(identity.state / 'environment.json')
                        if checkpoint:
                            # Verify exact image + checkpoint project/attempt labels.
                            operation = 'image_verification'
                            environment.selected_image(identity.state, engine.project, study['frozen']['image']['id'],
                                                       owner=identity.checkpoint_owner)
                            checkpoints.append(checkpoint)
                    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                        clean = False
                        recovery_errors.append({'phase': phase, 'operation': operation,
                            'code': getattr(error, 'code', 'CHECKPOINT_FAILED' if operation == 'checkpoint' else 'RUNTIME_UNAVAILABLE'),
                            'error_type': type(error).__name__})
                record['recovery_errors'] = recovery_errors
                # Credentials are excluded from workspaces/checkpoints. Remove temporary input copies after stop.
                if clean and (folder / 'input').is_dir():
                    (folder / 'input').chmod(0o700)
                    for name in study['frozen']['definition']['secret_refs']:
                        (folder / 'input' / ('secret-' + name)).unlink(missing_ok=True)
                    seal_tree(folder / 'input')
                record.update(cleanup_confirmed=clean, recovered_at=time.time(), recovered_checkpoints=checkpoints, recovery_complete=clean)
                reason = 'CONTROLLER_INTERRUPTED' if clean else 'RECOVERY_UNRESOLVED'
                engine.store.reconcile(attempt['id'], record, reason=reason)
                (recovered if clean else unresolved).append(attempt['id'])
                if not clean:
                    errors[attempt['id']] = recovery_errors
        engine.store.finish_study(identifier)
        engine.store.dispatch_state(identifier, 'recovery_required' if unresolved else 'recovered',
                                    'RECOVERY_UNRESOLVED' if unresolved else None)
        workspace.private_write(engine.root / 'controller/lease.json',
            {'schema_version': 1, 'state': 'recovered', 'heartbeat_at': time.time(), 'pid': None})
        # Persist never-claimed dispatch before the acknowledgement. A crash
        # before waking is repaired by replaying the request, without model/work replay.
        queued = any(row['state'] == 'queued' for row in engine.inspect(identifier)['trials'])
        result = {'study_id': identifier, 'recovered_attempt_ids': recovered, 'unresolved_attempt_ids': unresolved,
                  'queued_dispatched': queued and not unresolved, 'retry_required': bool(recovered), 'recovery_errors': errors}
        if queued and not unresolved:
            references(engine, engine.inspect(identifier), update=True)
            engine.store.dispatch(identifier)
        engine.store.recovery_operation(identifier, request_id, result)
    finally:
        lock.close()
    if result['queued_dispatched']:
        wake(engine)
    return result
