"""Bounded direct Docker command execution; no workload runs on the host."""
import base64
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
import uuid

from .. import environment, workspace
from ..dependencies import docker_env, find_docker
from .errors import ExperimentError


class Redactor:
    def __init__(self, secrets=()):
        values = sorted({value for value in secrets if value}, key=len, reverse=True)
        self.pattern = re.compile(b'|'.join(re.escape(value) for value in values)) if values else None
        self.keep = max(map(len, values), default=1) - 1
        self.buffer = b''

    def feed(self, data, *, final=False):
        self.buffer += data
        end = len(self.buffer) if final else max(0, len(self.buffer) - self.keep)
        if not self.pattern:
            result, self.buffer = self.buffer, b''
            return result
        chunks, cursor = [], 0
        for match in self.pattern.finditer(self.buffer):
            if match.start() >= end:
                break
            chunks += [self.buffer[cursor:match.start()], b'[REDACTED]']
            cursor = match.end()
        end = max(end, cursor)
        chunks.append(self.buffer[cursor:end])
        self.buffer = self.buffer[end:]
        return b''.join(chunks)


class BoundedLog:
    def __init__(self, path, limit, secrets=(), on_log=None):
        self.stream = open(path, 'xb')
        os.chmod(path, 0o600)
        self.on_log = on_log
        self.limit, self.saved, self.observed = limit, 0, 0
        self.redactor = Redactor(secrets)
        self.truncated = False

    def write(self, data, *, final=False):
        data = self.redactor.feed(data, final=final)
        self.observed += len(data)
        remaining = self.limit - self.saved
        content = data[:max(0, remaining)]
        if content and self.on_log:
            self.on_log('log_chunk', {'data_base64': base64.b64encode(content).decode('ascii')})
        self.stream.write(content)
        self.saved += min(len(data), max(0, remaining))
        self.truncated |= len(data) > remaining
        self.stream.flush()

    def close(self):
        if self.stream.closed:
            return
        self.write(b'', final=True)
        self.stream.flush(); os.fsync(self.stream.fileno()); self.stream.close()
        if self.on_log:
            self.on_log('log_end', {'bytes': self.saved, 'observed_bytes': self.observed, 'truncated': self.truncated})


def owned_info(context, *, timeout=30):
    try:
        info = json.loads(workspace.docker('inspect', context.owner_token, timeout=timeout))[0]
    except RuntimeError as error:
        if 'No such object' in str(error) or 'No such container' in str(error):
            return None
        raise
    labels = info.get('Config', {}).get('Labels') or {}
    if (labels.get('io.invariant.role') != 'codex-workspace'
            or labels.get('io.invariant.session') != context.owner_token
            or labels.get('io.invariant.project') != environment.project_key(context.project)
            or labels.get('io.invariant.owner') != context.checkpoint_owner):
        raise ExperimentError('FOREIGN_CONTAINER', 'Refusing to operate on a container with another owner.')
    return info


def stop_owned(context, *, inspect_timeout=3):
    info = owned_info(context, timeout=inspect_timeout)
    if info is not None and info.get('State', {}).get('Running'):
        workspace.docker('stop', '--time=3', info.get('Id', context.owner_token), timeout=6)
        info = owned_info(context, timeout=inspect_timeout)
    return info is None or not info.get('State', {}).get('Running')


def reclaim_private(context):
    """Return stopped Linux trial files to the operator, without running agent software."""
    if sys.platform != 'linux':
        return  # Docker Desktop maps bind-mount ownership to the host user.
    roots = [context.workspace]
    if context.output_directory is not None:
        roots.append(context.output_directory)
    uid, gid = os.getuid(), os.getgid()
    foreign = False
    def unreadable(error):
        raise error
    for root in roots:
        try:
            for base, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
                if any(p.lstat().st_uid != uid for p in [Path(base), *(Path(base)/n for n in dirs+files)]):
                    foreign = True; break
        except PermissionError:
            foreign = True
    if not foreign:
        return
    private = workspace.state_for(context.project).resolve()
    if (not context.checkpoint_owner or uid == 0 or any(p.is_symlink() or not p.resolve().is_relative_to(private)
            or p.resolve() == private for p in roots) or context.workspace == context.project):
        raise RuntimeError('Refusing ownership transfer outside private execution state')
    info = owned_info(context)
    if info is not None and info.get('State', {}).get('Running'):
        raise RuntimeError('Stop the private executor before transferring its files')
    environment.verify_control(context.project, path=context.control_directory)
    name = 'invariant-reclaim-' + uuid.uuid4().hex
    args = ['run', '--name', name, '--rm', '--pull=never', '--network=none', '--read-only',
            '--user=0:0', '--cap-drop=ALL', '--cap-add=CHOWN', '--cap-add=DAC_OVERRIDE',
            # Fit within even the smallest supported trial reservation.
            '--security-opt=no-new-privileges', '--pids-limit=32', '--memory=64m', '--memory-swap=64m',
            '--cpus=0.1', '--ulimit=nofile=64:64',
            '--label=io.invariant.role=ownership-helper', '--label=io.invariant.owner='+context.checkpoint_owner,
            '--mount', f'type=bind,src={context.workspace},dst=/workspace',
            '--mount', f'type=bind,src={context.control_directory},dst=/__invariant,readonly']
    if context.output_directory is not None:
        args += ['--mount', f'type=bind,src={context.output_directory},dst=/__invariant_output']
    args += ['--entrypoint='+environment.INSPECTOR, context.image, 'reclaim', str(uid), str(gid)]
    if context.output_directory is not None:
        args += ['output']
    try:
        result = json.loads(workspace.docker(*args, timeout=60))
        if result != {'reclaimed': True}:
            raise RuntimeError('Private file ownership was not confirmed')
    finally:
        # Remove only this helper, even after a timed-out Docker client.
        try:
            helper = json.loads(workspace.docker('inspect', name))[0]
        except RuntimeError as error:
            if 'No such' not in str(error): raise
        else:
            labels = helper.get('Config', {}).get('Labels') or {}
            if labels.get('io.invariant.role') != 'ownership-helper' or labels.get('io.invariant.owner') != context.checkpoint_owner:
                raise RuntimeError('Foreign ownership helper')
            workspace.docker('rm', '--force', helper['Id'])


class CommandRunner:
    def run(self, context, *, timeout, cancel, log_path, limit, secrets=(), kill_on_limit=False, on_started=None, on_intent=None, on_log=None):
        started = time.time()
        deadline = time.monotonic() + timeout
        log = BoundedLog(log_path, limit, secrets, on_log)
        process = None
        reason = None
        cleanup = False
        try:
            if on_intent: on_intent('create')
            if cancel.is_set():
                raise ExperimentError('CANCELLED_BEFORE_START', 'This attempt was stopped before container creation.')
            workspace.docker(*workspace.container_command(context.owner_token, context.project, context.image, context=context))
            inspected = owned_info(context)
            workspace.verify_container(context.owner_token, context.project, context.image, context=context, info=inspected)
            evidence = {'image': inspected['Image'], 'mounts': inspected['Mounts'],
                        'memory_bytes': inspected['HostConfig']['Memory'], 'nano_cpus': inspected['HostConfig']['NanoCpus'],
                        'pids_limit': inspected['HostConfig']['PidsLimit'], 'network': inspected['HostConfig']['NetworkMode']}
            executable = find_docker()
            # Preparation is a separate phase; the workload deadline starts at launch.
            started = time.time()
            deadline = time.monotonic() + timeout
            if on_intent: on_intent('start')
            if cancel.is_set():
                raise ExperimentError('CANCELLED_BEFORE_START', 'This attempt was stopped before container launch.')
            process = subprocess.Popen([executable, 'start', '--attach', context.owner_token],
                                       env=docker_env(executable), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
            if on_started:
                on_started()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                eof = False
                while not eof:
                    if reason is None and (cancel.is_set() or time.monotonic() >= deadline or (kill_on_limit and log.truncated)):
                        reason = 'CANCELLED' if cancel.is_set() else 'OUTPUT_LIMIT' if log.truncated else 'DEADLINE_EXCEEDED'
                    if reason is not None and process.poll() is None:
                        # A start request can be in flight while inspect still sees
                        # Created. Keep checking until the attached client exits.
                        try:
                            cleanup = stop_owned(context)
                        except (OSError, RuntimeError, subprocess.SubprocessError):
                            reason = 'UNRESOLVED_EXECUTION'
                            process.terminate()
                    events = selector.select(0.1)
                    for key, _ in events:
                        data = os.read(key.fileobj.fileno(), 65536)
                        if data:
                            log.write(data)
                        else:
                            eof = True
                    if not events and process.poll() is not None:
                        remainder = process.stdout.read()
                        if remainder: log.write(remainder)
                        eof = True
            process.wait(timeout=10)
            info = owned_info(context)
            if info is None:
                raise ExperimentError('CONTAINER_LOST', 'The owned container disappeared before its outcome was captured.')
            if info['State'].get('Running'):
                reason = reason or 'DOCKER_TRANSPORT_FAILED'
                cleanup = stop_owned(context)
                info = owned_info(context)
            else:
                cleanup = True
            state = (info or {}).get('State', {})
            if state.get('OOMKilled'):
                reason = 'OOM'
            exit_code = state.get('ExitCode')
            if reason is None and (process.returncode != 0 or exit_code != 0):
                reason = 'PROCESS_EXIT'
            log.close()
            return {'status': 'completed' if reason is None else 'cancelled' if reason == 'CANCELLED' else 'failed',
                    'reason': reason, 'exit_code': exit_code, 'started_at': started, 'finished_at': time.time(),
                    'duration_seconds': time.time() - started, 'cleanup_confirmed': cleanup,
                    'log': str(log_path), 'log_bytes': log.saved, 'log_truncated': log.truncated,
                    'container': context.owner_token, 'image': context.image, 'runtime': evidence}
        finally:
            log.close()
            if process:
                if process.poll() is None:
                    try: stop_owned(context)
                    finally:
                        process.kill(); process.wait(timeout=10)
                process.stdout.close()
            reclaim_private(context)
