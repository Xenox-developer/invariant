"""Conservative, Docker-only garbage collection; saved task state is never expired."""
import contextlib
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import shutil
import sqlite3
import stat
import tempfile
import threading
import time

from . import docker_storage as storage

DEFAULTS = {'enabled': True, 'older_than_days': 30, 'interval_hours': 1}
IMAGE_ID = re.compile(rb'sha256:[0-9a-f]{64}')
# Download caches only. Installed packages, virtual environments and Codex state
# (including its model catalog) are not disposable caches.
DOWNLOADS = ('.npm/_cacache', '.cache/pip/http', '.cache/pip/http-v2', '.cache/pip/wheels')


def root():
    if not storage.managed():
        raise RuntimeError('Automatic maintenance must run inside the Docker manager')
    return Path.home() / '.invariant'


@contextlib.contextmanager
def gate(*, exclusive=False, blocking=True):
    """Every manager operation holds a shared lease; maintenance needs exclusivity."""
    directory = root() / 'maintenance'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
                    | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def policy():
    path = root() / 'maintenance/config.json'
    value = {**DEFAULTS, **(json.loads(path.read_text()) if path.exists() else {})}
    if type(value['enabled']) is not bool:
        raise ValueError('enabled must be a boolean')
    for key, low, high in (('older_than_days', 0, 36500), ('interval_hours', 1/60, 8760)):
        number = value[key]
        if type(number) not in (int, float) or not low <= number <= high:
            raise ValueError('Invalid maintenance ' + key)
    if set(value) != set(DEFAULTS):
        raise ValueError('Unknown maintenance setting')
    return value


def configure(changes):
    with gate(exclusive=True):
        current = policy()
        if set(changes) - set(DEFAULTS):
            raise ValueError('Unknown maintenance setting')
        value = {**current, **changes}
        # Validate before replacing an existing policy.
        if type(value['enabled']) is not bool:
            raise ValueError('enabled must be a boolean')
        for key, low, high in (('older_than_days', 0, 36500), ('interval_hours', 1/60, 8760)):
            if type(value[key]) not in (int, float) or not low <= value[key] <= high:
                raise ValueError('Invalid maintenance ' + key)
        storage.atomic_json(root() / 'maintenance/config.json', value)
        return status()


def status():
    path = root() / 'maintenance/last.json'
    return {'policy': policy(), 'last_run': json.loads(path.read_text()) if path.exists() else None,
            'saved_tasks_expire': False, 'scope': 'invariant-owned-only'}


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return float('inf')  # Unknown age is never old enough.


def inspect_all(kind):
    arguments = {'container': ('ps', '--all', '--quiet'),
                 'image': ('image', 'ls', '--all', '--quiet', '--no-trunc'),
                 'volume': ('volume', 'ls', '--quiet', '--filter', 'label=' + storage.OWNER + '=v2')}[kind]
    identifiers = sorted(set(storage.docker(*arguments).stdout.decode().split()))
    result = []
    for start in range(0, len(identifiers), 128):
        result.extend(json.loads(storage.docker(kind, 'inspect', *identifiers[start:start+128]).stdout))
    return result


def owned_container(info):
    labels = info.get('Config', {}).get('Labels') or {}
    name = info.get('Name', '').lstrip('/')
    role = labels.get('io.invariant.role')
    return ((role in ('agent-runtime','catalog-runtime') and labels.get(storage.OWNER) == 'v2' and name.startswith('invariant-agent-'))
            or (role == 'codex-workspace' and labels.get('io.invariant.session') == name and name.startswith('invariant-')))


@contextlib.contextmanager
def metadata_database(path):
    """Do not create SQLite lock/WAL files inside immutable saved snapshots."""
    wal = path.with_name(path.name + '-wal')
    with contextlib.ExitStack() as stack:
        if wal.exists() or wal.is_symlink():
            if wal.is_symlink() or not wal.is_file():
                raise RuntimeError('Unsafe metadata journal')
            scratch = root() / 'maintenance'
            scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=scratch, prefix='reference-scan-')))
            shutil.copyfile(path, directory / path.name)
            shutil.copyfile(wal, directory / wal.name)
            uri = (directory / path.name).as_uri() + '?mode=ro'
        else:
            uri = path.as_uri() + '?mode=ro&immutable=1'
        database = stack.enter_context(contextlib.closing(sqlite3.connect(uri, uri=True)))
        yield database


def references(state):
    """Read durable metadata, not audit logs: old log mentions are not live leases.

    SQLite is queried, rather than grepped: TEXT can cross database overflow pages.
    WAL is included by SQLite's read transaction. Any unreadable metadata aborts GC.
    No directories or symlinks are followed outside the state volume.
    """
    images = set()
    current = set()
    homes = set()
    saved = []
    def scan(value):
        if isinstance(value, str):
            images.update(match.decode() for match in IMAGE_ID.findall(value.encode()))
        elif isinstance(value, bytes):
            images.update(match.decode() for match in IMAGE_ID.findall(value))
    for base, dirs, files in os.walk(state, followlinks=False):
        directory = Path(base)
        dirs[:] = [name for name in dirs if not (directory / name).is_symlink()
                   and not (directory == state and name == 'maintenance')]
        # Immutable snapshots and migrated staging copies must remain untouched.
        # They still participate in reference discovery below.
        if directory.name in ('codex', 'native-home') or directory.parent.name == 'review-runtime-v2' and directory.name == 'home':
            homes.add(directory / 'home')
        for name in files:
            path = directory / name
            if path.is_symlink():
                continue
            if name.endswith(('.sqlite', '.sqlite3', '.db')):
                with metadata_database(path) as database:
                    tables = database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    for (table,) in tables:
                        quoted = '"' + table.replace('"', '""') + '"'
                        for row in database.execute('SELECT * FROM ' + quoted):
                            for item in row:
                                scan(item)
            elif name.endswith(('.json', '.toml')):
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise RuntimeError('Non-regular maintenance metadata')
                    tail = b''
                    while chunk := stream.read(1024 * 1024):
                        scan(tail + chunk)
                        tail = chunk[-80:]
                if name == 'current.json':
                    current.update(re.findall(r'invariant-[A-Za-z0-9_.-]+', path.read_text()))
                    # Ordinary sessions record an ID without the invariant- prefix.
                    value = json.loads(path.read_text())
                    if isinstance(value.get('session'), str):
                        current.add('invariant-' + value['session'])
                elif name == 'environment.json':
                    record = json.loads(path.read_text())
                    if record.get('schema') == 1 and isinstance(record.get('image'), str):
                        saved.append(record)
    return images, current, homes, saved


def cache_candidates(state, homes, cutoff):
    result = []
    for home in sorted(homes):
        # Only known mutable runtime homes, never frozen copies or migration data.
        relative = home.relative_to(state)
        if any(part in ('snapshot', 'baselines', 'workspace', 'migration', 'exports')
               or part.startswith('restore-before-') for part in relative.parts):
            continue
        for suffix in DOWNLOADS:
            directory = home / suffix
            if any(p.is_symlink() for p in (directory, *directory.parents)):
                continue
            for base, dirs, files in os.walk(directory, followlinks=False):
                dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
                for name in files:
                    path = Path(base) / name
                    info = path.lstat()
                    if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_mtime < cutoff:
                        result.append({'path': path.relative_to(state).as_posix(), 'bytes': info.st_size,
                                       'identity': [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size]})
    return result


def plan(state, containers, images, *, cutoff, volumes=()):
    retained, current, homes, saved = references(state)
    retained.update(c['Image'] for c in containers)
    image_map = {image['Id']: image for image in images}
    # Tagged images may be user-selected tools or prepared bases. Never expire them.
    retained.update(image['Id'] for image in images if image.get('RepoTags'))
    candidates = {'containers': [], 'images': [], 'code_volumes': [],
                  'cache_files': cache_candidates(state, homes, cutoff)}
    for volume in volumes:
        if (not re.fullmatch(storage.RUNTIME_VOLUME_PREFIX + '[0-9a-f]{16}', volume['Name'])
                or (volume.get('Labels') or {}).get(storage.OWNER) != 'v2'
                or timestamp(volume.get('CreatedAt')) >= cutoff):
            continue
        mountpoint = volume['Mountpoint']
        if not any(m.get('Name') == volume['Name'] or m.get('Source') == mountpoint
                   or m.get('Source', '').startswith(mountpoint + '/')
                   for c in containers for m in c.get('Mounts', [])):
            candidates['code_volumes'].append(volume['Name'])
    for container in containers:
        if not owned_container(container) or container['State'].get('Running'):
            continue
        name = container['Name'].lstrip('/')
        if name in current or timestamp(container['Created']) >= cutoff:
            continue
        labels = container['Config'].get('Labels') or {}
        removable = labels.get('io.invariant.role') in ('agent-runtime','catalog-runtime')
        if not removable:
            # A stopped executor can contain the only surviving edits/packages.
            # Only a saved, referenced checkpoint of this exact session proves it
            # is disposable. Failed/partial checkpoints remain for recovery.
            for record in saved:
                image = image_map.get(record.get('image'), {})
                tags = image.get('Config', {}).get('Labels') or {}
                if (tags.get('io.invariant.session') == name
                        and tags.get('io.invariant.checkpoint-owner') == labels.get('io.invariant.owner')
                        and tags.get('io.invariant.checkpoint-project') == labels.get('io.invariant.project')
                        and timestamp(image.get('Created')) != float('inf')
                        and timestamp(image.get('Created')) >= timestamp(container['State'].get('FinishedAt'))):
                    removable = True
                    break
        if removable:
            candidates['containers'].append(container['Id'])
    for image in images:
        labels = image.get('Config', {}).get('Labels') or {}
        owned = bool(re.fullmatch('[0-9a-f]{64}', labels.get('io.invariant.checkpoint-project', ''))
                     or re.fullmatch('[0-9a-f]{64}', labels.get(storage.OWNER + '.source', '')))
        if owned and image['Id'] not in retained and timestamp(image['Created']) < cutoff:
            candidates['images'].append(image['Id'])
    return candidates


def collect(*, confirm=False, older_than_days=None, scheduled=False, now=None):
    now = time.time() if now is None else now
    try:
        with gate(exclusive=True, blocking=False):
            settings = policy()
            previous = status()['last_run']
            if scheduled and (not settings['enabled'] or previous and now - previous['started_at'] < settings['interval_hours'] * 3600):
                return {'state': 'not_due'}
            days = settings['older_than_days'] if older_than_days is None else older_than_days
            if type(days) not in (int, float) or not 0 <= days <= 36500:
                raise ValueError('older_than_days must be between 0 and 36500')
            for journal in (root() / 'migration').glob('*/journal.json'):
                if json.loads(journal.read_text()).get('phase') != 'deleted':
                    return {'state': 'busy', 'reason': 'migration_incomplete'}
            containers = inspect_all('container')
            # Also protect work in old managers that predate the shared gate.
            if any(owned_container(c) and c['State'].get('Running') and (c['Config'].get('Labels') or {}).get('io.invariant.role')!='catalog-runtime' for c in containers):
                return {'state': 'busy', 'reason': 'active_executor'}
            candidates = plan(root(), containers, inspect_all('image'), cutoff=now-days*86400,
                              volumes=inspect_all('volume'))
            result = {'state': 'completed' if confirm else 'preview', 'started_at': now,
                      'older_than_days': days, 'candidates': candidates,
                      'removed': {'containers': [], 'images': [], 'code_volumes': [], 'cache_files': []}, 'errors': []}
            if confirm:
                for kind, arguments in (('containers', ('container', 'rm')), ('images', ('image', 'rm', '--no-prune')),
                                        ('code_volumes', ('volume', 'rm'))):
                    for identity in candidates[kind]:
                        # No force: a newly started container or image use vetoes deletion.
                        removed = storage.docker(*arguments, identity, check=False)
                        if removed.returncode:
                            result['errors'].append({'kind': kind, 'id': identity, 'reason': 'in_use_or_changed'})
                        else:
                            result['removed'][kind].append(identity)
                for entry in candidates['cache_files']:
                    path = root() / entry['path']
                    try:
                        if any(p.is_symlink() for p in (path, *path.parents)):
                            raise RuntimeError('Redirected cache file')
                        info = path.lstat()
                        if [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size] != entry['identity']:
                            raise RuntimeError('Cache file changed')
                        path.unlink()
                        result['removed']['cache_files'].append(entry['path'])
                    except (OSError, RuntimeError):
                        result['errors'].append({'kind': 'cache_files', 'path': entry['path'], 'reason': 'changed'})
                # Journals never contain cache contents, credentials or model output.
                storage.atomic_json(root() / 'maintenance/last.json', result)
                with open(root() / 'maintenance/history.jsonl', 'a') as stream:
                    stream.write(json.dumps({k: v for k, v in result.items() if k != 'candidates'}) + '\n')
            return result
    except BlockingIOError:
        return {'state': 'busy', 'reason': 'active_manager_operation'}


def daemon(stop=None):
    """One scheduler per manager; the volume lock and last_run deduplicate work."""
    if stop is None:
        stop = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())
    # Give startup/migration/import operations first access to the manager.
    while not stop.wait(60):
        try:
            collect(confirm=True, scheduled=True)
        except Exception as error:
            # Fail closed. Incomplete reference discovery must never allow removal.
            # Keep only the type: SQLite/filesystem errors can contain private paths.
            print('Maintenance deferred: ' + type(error).__name__, flush=True)


if __name__ == '__main__':
    daemon()
