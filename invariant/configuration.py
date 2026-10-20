"""Inspect and atomically edit one project's supported workspace settings."""
import fcntl
import json
import os
from pathlib import Path
import stat

from .codex_rpc import inert
from .policy import normalize, unique_object, DEFAULT_CONFIG
from .workspace import load_project, private_write


def set_value(project, key, text):
    from .workspace import state_for
    from .execution import project_lock
    state = state_for(project)
    marker = state / 'experiments/apply-active.json'
    if marker.exists() or marker.is_symlink():
        with project_lock(state):
            return set_value(project, key, text)
    if key not in ("profile", "workspace_access", "duration_seconds", "command_network", "resource_mode") and not key.startswith("limits."):
        raise RuntimeError("Setting is not editable: " + inert(key) + ". Use invariant config to see supported settings")
    if len(text) > 128:
        raise RuntimeError("Configuration value is too long")
    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except ValueError:
        value = text
    # Check the directory/config before creating a lock, then reload under lock.
    load_project(project)
    path = project / '.invariant' / 'config.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise RuntimeError('Unsafe project configuration lock')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another configuration edit is in progress; retry shortly") from None
        config = load_project(project)
        if key.startswith('limits.'):
            config['resource_mode'] = 'manual'
            config['limits'][key.removeprefix('limits.')] = value
        else:
            config[key] = value
        if key == 'resource_mode' and value == 'auto':
            config['limits'] = dict(DEFAULT_CONFIG['limits'])
        config = normalize(config)
        private_write(project / '.invariant' / 'config.json', config)
    return config


def main(action=None, key=None, value=None, *, machine=False):
    project = Path.cwd().resolve()
    if action == 'set':
        if key is None or value is None:
            raise RuntimeError('Use invariant config set KEY VALUE')
        config = set_value(project, key, value)
    elif action is None:
        config = load_project(project)
    else:
        raise RuntimeError('Use invariant config, or invariant config set KEY VALUE')
    if machine:
        print(json.dumps(config))
    else:
        print('Project configuration:', inert(str(project / '.invariant' / 'config.json')))
        print(json.dumps(config, indent=2))
        print('Changes apply on the next run or resume. A running session keeps its launch settings.')
        print('Resources: ' + ('managed automatically at each launch' if config['resource_mode'] == 'auto' else 'manual limits'))
        print('Automatic resources: invariant config set resource_mode auto')
        print('General persistent Linux environment: invariant config set profile codex-environment-v2')
        print('Edit a setting: invariant config set limits.memory_mib 1024')
        print('Project access: invariant config set workspace_access read-only')
        print('Internet: invariant config set command_network internet (disable with none)')
        if config['command_network'] == 'internet':
            print('Internet mode permits outbound connections to reachable internet and local-network services. No ports are published.')
    return 0
