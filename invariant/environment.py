"""Persistent Linux environment lifecycle, independent of language/package manager.

The host owns checkpoint identities and immutable control executables. The agent
owns the environment filesystem. Only stopped filesystems are checkpointed;
processes, mounts, networking and runtime authority are never restored.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

PROFILE = 'codex-environment-v2'
IMAGE = 'invariant-codex-environment:0.163.0-v1'
CONTROL = '/__invariant'
INSPECTOR = CONTROL + '/inspect'
CAPABILITIES = ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'FSETID', 'SETGID', 'SETUID', 'SYS_CHROOT', 'SETFCAP')
CAPABILITY_MASK = sum(1 << bit for bit in (0, 1, 3, 4, 6, 7, 18, 31))
ENVIRONMENT = {'HOME': '/root', 'CODEX_HOME': '/scratch/codex', 'TMPDIR': '/scratch',
               'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'}
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}')


def enabled(policy):
    return policy['profile'] == PROFILE


def control_path(project):
    from .workspace import state_for
    return state_for(project) / 'runtime-v1/control'


def project_key(project):
    return hashlib.sha256(os.fsencode(project)).hexdigest()


def read_record(path):
    try:
        with open(path, 'rb', opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW | os.O_NONBLOCK)) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_size > 8192:
                raise ValueError()
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise RuntimeError('The private environment record is unsafe or invalid. Project files and chats were kept.') from None


def verify_control(project, *, path=None):
    path = Path(path) if path is not None else control_path(project)
    record = read_record(path.parent / 'control.json')
    if not record or path.is_symlink() or not path.is_dir() or set(p.name for p in path.iterdir()) != {'codex', 'inspect'}:
        raise RuntimeError('The protected environment runtime is unavailable')
    for name in ('codex', 'inspect'):
        file = path / name
        info = file.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or hashlib.sha256(file.read_bytes()).hexdigest() != record.get(name)):
            raise RuntimeError('The protected environment runtime failed integrity verification')
    return record


def prepare_control(project, image, *, path=None):
    from .workspace import docker, private_write, remove_owned
    path = Path(path) if path is not None else control_path(project)
    if path.exists() or path.is_symlink():
        record = verify_control(project, path=path)
        if record.get('base_image') != image:
            raise RuntimeError('The protected runtime belongs to another base image; keep the existing Docker base image or restore its runtime.')
        return path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.parent / ('control-' + uuid.uuid4().hex)
    temporary.mkdir(mode=0o700)
    name = 'invariant-runtime-' + uuid.uuid4().hex
    try:
        docker('create', '--name', name, '--network=none', '--pull=never',
               '--label=io.invariant.role=codex-workspace', '--label=io.invariant.session=' + name,
               '--entrypoint=/bin/true', image)
        docker('cp', name + ':/__invariant/.', str(temporary), timeout=60)
        if set(p.name for p in temporary.iterdir()) != {'codex', 'inspect'}:
            raise RuntimeError('Unexpected files in the protected runtime image')
        record = {'base_image': image}
        for file in temporary.iterdir():
            info = file.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise RuntimeError('Unsafe protected runtime file')
            file.chmod(0o500)
            record[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
        private_write(path.parent / 'control.json', record)
        temporary.rename(path)
        verify_control(project, path=path)
        return path
    finally:
        remove_owned(name)


def selected_image(state, project, base, *, owner=None):
    from .workspace import docker
    record = read_record(state / 'environment.json')
    if record is None:
        return base
    if (type(record.get('schema')) is not int or record['schema'] != 1 or record.get('project') != str(project)
            or not isinstance(record.get('image'), str) or not IMAGE_ID.fullmatch(record['image'])):
        raise RuntimeError('Saved environment does not belong to this project. No replacement environment was created.')
    if record.get('owner') != owner:
        raise RuntimeError('Saved environment belongs to a different execution owner')
    try:
        info = json.loads(docker('image', 'inspect', record['image']))[0]
    except (RuntimeError, ValueError, IndexError):
        raise RuntimeError('Saved environment image is missing. Restore that image from Docker backups. Project files and chats are kept; a fresh environment was not substituted.') from None
    labels = info.get('Config', {}).get('Labels') or {}
    if info.get('Id') != record['image'] or labels.get('io.invariant.checkpoint-project') != project_key(project):
        raise RuntimeError('Saved environment ownership does not match the project')
    if labels.get('io.invariant.checkpoint-owner') != owner:
        raise RuntimeError('Saved environment checkpoint owner does not match')
    return record['image']


def preserve(name, state, project, audit, *, owner=None):
    """Stop, checkpoint atomically, then remove; failure keeps recovery authority."""
    from .workspace import docker, private_write, remove_owned
    try:
        info = json.loads(docker('inspect', name))[0]
    except RuntimeError as error:
        if 'No such object' in str(error) or 'No such container' in str(error):
            return
        raise
    labels = info.get('Config', {}).get('Labels') or {}
    if (labels.get('io.invariant.role') != 'codex-workspace' or labels.get('io.invariant.session') != name
            or labels.get('io.invariant.environment') != PROFILE or labels.get('io.invariant.project') != project_key(project)
            or labels.get('io.invariant.owner') != owner):
        raise RuntimeError('Refusing to checkpoint an environment owned by another project/session')
    if info.get('State', {}).get('Running'):
        docker('stop', '--time=3', name, timeout=30)
    if not info['HostConfig'].get('ReadonlyRootfs'):
        changes = ['--change', 'LABEL io.invariant.checkpoint-project=' + project_key(project)]
        if owner is not None:
            changes += ['--change', 'LABEL io.invariant.checkpoint-owner=' + owner]
        image = docker('commit', *changes, name, timeout=180)
        if not IMAGE_ID.fullmatch(image):
            raise RuntimeError('Docker did not return a valid environment checkpoint; the stopped container was kept')
        record = {'schema': 1, 'project': str(project), 'image': image}
        if owner is not None:
            record['owner'] = owner
        private_write(state / 'environment.json', record)
        audit('environment_saved', image=image)
    remove_owned(name)


def recover(name, state, project, audit):
    from .workspace import docker, remove_owned
    try:
        info = json.loads(docker('inspect', name))[0]
    except RuntimeError as error:
        if 'No such object' in str(error) or 'No such container' in str(error):
            return
        raise
    if (info.get('Config', {}).get('Labels') or {}).get('io.invariant.environment') == PROFILE:
        preserve(name, state, project, audit)
    else:
        remove_owned(name)


def instructions(policy):
    return ('You are administrator inside a persistent Debian Linux environment. Use any Linux-compatible '
            'language, toolchain, package manager or application; install and configure tools normally as needed. '
            'The environment filesystem, /root home, installed software and configuration are saved on exit and '
            'recovered on the next run or resume. Project files are in /workspace. Use ordinary shell profiles '
            'for custom PATH/setup; login shells load them. /scratch and running processes are temporary. '
            'No package-manager-specific installation paths or runtime profiles are required. '
            + ('The project and environment filesystem are currently read-only; use /scratch for temporary work. '
               if policy['workspace_access'] == 'read-only' else '')
            + 'Do not alter /__invariant or request host access, Docker control, credentials or additional privileges. ')
