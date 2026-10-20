"""Explicit execution identity and project coordination, shared by chat and trials."""
from dataclasses import dataclass, field
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Callable

from .policy import normalize


def private_directory(path):
    """Refuse redirected private state before opening locks or SQLite files."""
    path = Path(path)
    missing = []
    current = path
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        current = current.parent
    for parent in (path, *path.parents):
        if parent.is_symlink() and (parent == path or parent.lstat().st_uid == os.getuid()):
            raise RuntimeError('Private state cannot contain symlink directories')
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Unsafe private state directory')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Unsafe private state directory')
    path.chmod(0o700)
    return path


def project_lock(state, *, timeout=0, message='This project is busy; retry after its active workspace session finishes', reconcile=True):
    """The existing chat lease is also the short snapshot/apply coordination lock."""
    private_directory(state)
    fd = os.open(Path(state) / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    lock = os.fdopen(fd, 'a')
    try:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise RuntimeError('Unsafe project coordination lock')
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(message) from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        marker = Path(state) / 'experiments/apply-active.json'
        if reconcile and (marker.exists() or marker.is_symlink()):
            from .experiments.applying import reconcile_pending
            reconcile_pending(Path(state))
        return lock
    except BaseException:
        lock.close()
        raise


@dataclass(frozen=True)
class ExecutionContext:
    """A run's source identity is independent of its writable mount and state.

    Policy is copied at construction and returned by value. Trial callers supply
    private policy/control mounts and a checkpoint owner; ordinary chat retains
    its historical checkpoint format. The event sink never owns presentation.
    """
    project: Path
    workspace: Path
    state: Path
    image: str
    owner_token: str
    policy_directory: Path
    control_directory: Path | None = None
    checkpoint_owner: str | None = None
    event_sink: Callable = field(default=lambda *args, **kwargs: None, repr=False, compare=False)
    _policy_json: str = field(default='', repr=False)
    command: tuple | None = None
    readonly_mounts: tuple = ()
    output_directory: Path | None = None
    command_environment: tuple = ()
    working_directory: str = '/workspace'

    @classmethod
    def create(cls, *, project, workspace, state, image, owner_token, policy,
               policy_directory=None, control_directory=None, checkpoint_owner=None,
               event_sink=None, command=None, readonly_mounts=(), output_directory=None,
               command_environment=None, working_directory='/workspace'):
        project, writable, state = (Path(p).resolve() for p in (project, workspace, state))
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', owner_token):
            raise RuntimeError('Invalid execution ownership token')
        if state.is_relative_to(writable):
            raise RuntimeError('Execution state must be outside the writable workspace')
        if writable != project and checkpoint_owner is None:
            raise RuntimeError('A private trial workspace requires a checkpoint owner')
        if checkpoint_owner is not None:
            from .workspace import state_for
            if state == state_for(project).resolve():
                raise RuntimeError('Trial state cannot replace ordinary project state')
        if checkpoint_owner is not None and (writable == project or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', checkpoint_owner)):
            raise RuntimeError('A trial needs independent writable state and a valid checkpoint owner')
        mounts = tuple((Path(path).resolve(), destination) for path, destination in readonly_mounts)
        if any(not destination.startswith('/__invariant_inputs/') or '..' in destination.split('/')
               or any(c in str(path) + destination for c in (',', '\n', '\r', '\0')) for path, destination in mounts):
            raise RuntimeError('Invalid command input mount')
        if len({destination for _, destination in mounts}) != len(mounts):
            raise RuntimeError('Duplicate command input mount')
        extras = dict(command_environment or {})
        if any(not re.fullmatch(r'INVARIANT_[A-Z0-9_]+', key) or not isinstance(value, str) or '\0' in value
               for key, value in extras.items()):
            raise RuntimeError('Invalid command environment')
        if command is not None and (not command or not all(isinstance(arg, str) and '\0' not in arg for arg in command)):
            raise RuntimeError('Invalid command argument vector')
        if working_directory not in ('/workspace', '/__invariant_inputs/source'):
            raise RuntimeError('Invalid command working directory')
        return cls(project=project, workspace=writable, state=state, image=image, owner_token=owner_token,
                   policy_directory=Path(policy_directory or writable / '.invariant').resolve(),
                   control_directory=Path(control_directory).resolve() if control_directory is not None else None,
                   checkpoint_owner=checkpoint_owner, event_sink=event_sink or (lambda *args, **kwargs: None),
                   _policy_json=json.dumps(normalize(policy), sort_keys=True, allow_nan=False),
                   command=tuple(command) if command is not None else None, readonly_mounts=mounts,
                   output_directory=Path(output_directory).resolve() if output_directory is not None else None,
                   command_environment=tuple(sorted(extras.items())), working_directory=working_directory)

    @property
    def policy(self):
        return json.loads(self._policy_json)

    @property
    def checkpoint_path(self):
        return self.state / 'environment.json'

    def emit(self, event, **facts):
        self.event_sink(event, **facts)

    def preserve(self, container):
        from .environment import preserve
        preserve(container, self.state, self.project, self.emit, owner=self.checkpoint_owner)
