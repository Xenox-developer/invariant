"""Private, same-path experiment backups with exact Docker images and recoverable restore."""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import uuid

from .. import __version__, environment, workspace
from ..execution import private_directory, project_lock
from .errors import ExperimentError
from .definitions import digest
from .storage import images
from .store import TERMINAL, SCHEMA

DIRECTORIES={'baselines','studies','attempts','apply-plans'}


def sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


@contextmanager
def quiet(engine):
    with ExitStack() as stack:
        for folder in (engine.root/'controller',engine.root/'mutations'):
            lease=project_lock(folder);stack.callback(lease.close)
        lease=engine._lock(reconcile=False);stack.callback(lease.close)
        if (engine.state/'current.json').exists():
            raise ExperimentError('WORKSPACE_RECOVERY_REQUIRED','Finish or recover the ordinary session first.')
        rows=engine.store.list(100000)
        for row in rows:
            for trial in engine.inspect(row['id'])['trials']:
                for attempt in trial['attempts']:
                    if attempt['state'] not in TERMINAL or attempt['record'].get('cleanup_confirmed') is not True:
                        raise ExperimentError('ACTIVE_STATE','Backup/restore requires all attempts stopped and cleanup confirmed.',
                                              'Stop and recover active studies first. No running work is snapshotted.')
        yield rows


def entries(root):
    result={}
    for base,dirs,files in os.walk(root,followlinks=False):
        for name in sorted(dirs+files):
            path=Path(base)/name;relative=path.relative_to(root).as_posix();info=path.lstat()
            if stat.S_ISDIR(info.st_mode):row={'type':'directory','mode':info.st_mode&0o777}
            elif stat.S_ISLNK(info.st_mode):row={'type':'symlink','target':os.readlink(path)}
            elif stat.S_ISREG(info.st_mode):row={'type':'file','mode':info.st_mode&0o777,'size':info.st_size,'sha256':sha(path)}
            else:raise ExperimentError('UNSAFE_BACKUP','Private state contains a special file.')
            result[relative]=row
    return result


def backup(engine, output):
    output=Path(output).expanduser().absolute()
    if output.resolve().is_relative_to(engine.project) or output.resolve().is_relative_to(engine.state):
        raise ExperimentError('INVALID_PATH','Keep the private backup outside the project and its live state directory.')
    with quiet(engine) as rows, tempfile.TemporaryDirectory(prefix='backup-stage-',dir=engine.state) as temporary:
        if not rows:raise ExperimentError('EMPTY_STATE','There are no saved studies to back up.')
        root=Path(temporary);state=root/'state';state.mkdir(mode=0o700)
        def ignored(directory,names):
            relative=Path(directory).relative_to(engine.root).parts
            if len(relative)==2 and relative[0]=='attempts':return [n for n in names if n=='native-home']
            if len(relative)==3 and relative[0]=='attempts' and relative[2]=='input':return [n for n in names if n.startswith('secret-')]
            return []
        for name in DIRECTORIES:
            source=engine.root/name
            if source.exists():shutil.copytree(source,state/name,symlinks=True,ignore=ignored)
        # SQLite backup incorporates committed WAL without copying a live sidecar.
        with engine.store.connection() as source:
            target=sqlite3.connect(state/'store.sqlite3')
            try:source.backup(target)
            finally:target.close()
        (state/'store.sqlite3').chmod(0o600)
        image_ids=images(engine,rows)
        for image in image_ids:
            if not environment.IMAGE_ID.fullmatch(image):raise ExperimentError('CORRUPT_STATE','Invalid saved image identity.')
        workspace.docker('image','save','--output',str(root/'images.tar'),*image_ids,timeout=1200)
        (root/'images.tar').chmod(0o600)
        manifest={'schema_version':1,'kind':'invariant-private-backup','invariant_version':__version__,
            'project':str(engine.project),'state':str(engine.root),'config_sha256':sha(engine.project/'.invariant/config.json'),
            'images':image_ids,'files':entries(root),
            'omitted':['host authentication','native conversation homes','credential-file references','regenerable runtime mounts']}
        (root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        try:fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        except FileExistsError:raise ExperimentError('OUTPUT_EXISTS','The backup destination already exists.','Choose a new archive path; no existing file is replaced.') from None
        try:
            with os.fdopen(fd,'wb') as stream:
                with tarfile.open(fileobj=stream,mode='w:gz',dereference=False) as archive:
                    archive.add(root/'manifest.json',arcname='manifest.json',recursive=False)
                    for name in sorted(manifest['files']):archive.add(root/name,arcname=name,recursive=False)
                stream.flush();os.fsync(stream.fileno())
        except BaseException:
            output.unlink(missing_ok=True);raise
        return {'backup':str(output),'sha256':sha(output),'bytes':output.stat().st_size,
                'studies':len(rows),'images':len(image_ids),'private':True,'host_authentication_included':False}


def extract(archive, root, *, project, destination):
    with tarfile.open(archive,'r:gz') as package:
        members=package.getmembers()
        if len(members)>1000000 or len({m.name for m in members})!=len(members):
            raise ExperimentError('INVALID_BACKUP','Duplicate or excessive archive entries.')
        metadata=package.getmember('manifest.json')
        if not metadata.isfile() or metadata.size>64*1024**2:raise ExperimentError('INVALID_BACKUP','Invalid backup manifest.')
        manifest=json.load(package.extractfile(metadata))
        if (manifest.get('schema_version')!=1 or manifest.get('kind')!='invariant-private-backup'
                or manifest.get('project')!=str(project) or manifest.get('state')!=str(destination)):
            raise ExperimentError('BACKUP_IDENTITY_MISMATCH','Restore requires the same project path and private state location.')
        if manifest.get('config_sha256')!=sha(project/'.invariant/config.json'):
            raise ExperimentError('BACKUP_CONFIG_MISMATCH','Project configuration differs from the backup.',
                                  'Restore the matching project configuration from your project backup before restoring experiments.')
        files=manifest.get('files')
        if not isinstance(files,dict) or set(files)|{'manifest.json'}!={m.name for m in members}:
            raise ExperimentError('INVALID_BACKUP','Archive entries do not match the manifest.')
        total=0;links=[];directories=[];maximum=shutil.disk_usage(root).free-64*1024**2
        for member in members:
            if member.name=='manifest.json':continue
            relative=PurePosixPath(member.name)
            if (relative.is_absolute() or '..' in relative.parts or relative.as_posix()!=member.name.rstrip('/')
                    or '\\' in member.name or member.name not in files):
                raise ExperimentError('INVALID_BACKUP','Unsafe archive path.')
            if relative.parts[0] not in ('state','images.tar') or (relative.parts[0]=='images.tar' and len(relative.parts)!=1):
                raise ExperimentError('INVALID_BACKUP','Unexpected backup path.')
            if len(relative.parts)>1 and relative.parts[0]=='state' and relative.parts[1] not in DIRECTORIES|{'store.sqlite3'}:
                raise ExperimentError('INVALID_BACKUP','Unexpected state path.')
            if 'native-home' in relative.parts:raise ExperimentError('INVALID_BACKUP','Backup must omit host authentication homes.')
            for parent in relative.parents:
                if str(parent)!='.' and files.get(parent.as_posix(),{}).get('type')!='directory':
                    raise ExperimentError('INVALID_BACKUP','Archive traverses a non-directory.')
            row=files[member.name];target=root.joinpath(*relative.parts)
            if (member.name=='images.tar' and row.get('type')!='file') or (member.name=='state' and row.get('type')!='directory'):
                raise ExperimentError('INVALID_BACKUP','Invalid backup root entry.')
            if row.get('type')=='directory' and member.isdir():
                target.mkdir(parents=True,exist_ok=True,mode=0o700);directories.append((target,row['mode']))
            elif row.get('type')=='file' and member.isfile() and member.size==row.get('size'):
                total+=member.size
                if total>maximum:
                    raise ExperimentError('DISK_HEADROOM','Insufficient disk for restoring the private backup.')
                target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with package.extractfile(member) as source,target.open('xb') as output:shutil.copyfileobj(source,output)
                if sha(target)!=row.get('sha256'):raise ExperimentError('INVALID_BACKUP','Backup content checksum mismatch.')
                target.chmod(row['mode']&0o777)
            elif row.get('type')=='symlink' and member.issym() and member.linkname==row.get('target'):
                links.append((target,row['target']))
            else:raise ExperimentError('INVALID_BACKUP','Archive type/size mismatch or hard link.')
        for target,value in links:target.parent.mkdir(parents=True,exist_ok=True);target.symlink_to(value)
        for target,mode in sorted(directories,key=lambda pair:len(pair[0].parts),reverse=True):target.chmod(mode&0o777)
        return manifest


def recover_restore(engine):
    marker=engine.state/'restore-active.json'
    record=environment.read_record(marker)
    if record is None:return {'recovered':True,'changed':False}
    if (type(record.get('schema')) is not int or record['schema']!=1 or
            record.get('digest')!=digest({k:v for k,v in record.items() if k!='digest'})):
        raise ExperimentError('RESTORE_RECOVERY_BLOCKED','Restore journal integrity failed; all copies were retained.')
    token=record.get('token','')
    if not re.fullmatch(r'[0-9a-f]{32}',token) or record.get('project')!=str(engine.project):
        raise ExperimentError('RESTORE_RECOVERY_BLOCKED','Unsafe restore journal; all copies were retained.')
    stage=engine.state/('restore-stage-'+token);old=engine.state/('restore-before-'+token)
    staged=stage/'state';destination=engine.root
    if any(p.is_symlink() for p in (stage,old,destination,staged)):
        raise ExperimentError('RESTORE_RECOVERY_BLOCKED','Restore state was redirected; all copies were retained.')
    # A fully validated state is published with a single directory rename. Old
    # state is retained, and recovery never executes workloads or project writes.
    if destination.exists() and not staged.exists():outcome='published'
    elif not destination.exists() and old.exists():
        old.rename(destination);outcome='rolled_back'
    elif destination.exists() and staged.exists() and not old.exists():outcome='unchanged'
    elif not destination.exists() and not old.exists():outcome='unchanged'
    else:raise ExperimentError('RESTORE_RECOVERY_BLOCKED','Concurrent state change detected; all copies were retained.')
    marker.unlink()
    if stage.exists():
        from .snapshots import unseal_tree
        unseal_tree(stage);shutil.rmtree(stage)
    from .engine import sync_directory
    sync_directory(engine.state)
    return {'recovered':True,'outcome':outcome,'previous_state':str(old) if old.exists() else None}


def verify_images(path, expected):
    """Docker's classic store uses config IDs; the containerd store uses OCI IDs."""
    with tarfile.open(path) as archive:
        item=archive.getmember('manifest.json')
        if not item.isfile() or item.size>8*1024**2:raise ExperimentError('INVALID_BACKUP','Invalid image manifest.')
        configs=json.load(archive.extractfile(item));config_ids=set()
        for config in configs:
            if config.get('RepoTags'):raise ExperimentError('INVALID_BACKUP','Backup images must have no mutable tags.')
            member=archive.getmember(config['Config'])
            if not member.isfile() or member.size>16*1024**2:raise ExperimentError('INVALID_BACKUP','Invalid image configuration.')
            config_ids.add('sha256:'+hashlib.sha256(archive.extractfile(member).read()).hexdigest())
        try:index=archive.getmember('index.json')
        except KeyError:
            if expected==config_ids:return
            raise ExperimentError('INVALID_BACKUP','Image identities do not match the backup.') from None
        if not index.isfile() or index.size>8*1024**2:raise ExperimentError('INVALID_BACKUP','Invalid OCI index.')
        descriptors=json.load(archive.extractfile(index)).get('manifests',[])
        for descriptor in descriptors:
            for key,value in descriptor.get('annotations',{}).items():
                if key in ('org.opencontainers.image.ref.name','io.containerd.image.name') and value not in expected|config_ids|{i.split(':')[1] for i in expected|config_ids}:
                    raise ExperimentError('INVALID_BACKUP','OCI backup must not contain mutable image names.')
        if expected==config_ids:return
        if {item.get('digest') for item in descriptors}!=expected:
            raise ExperimentError('INVALID_BACKUP','OCI image identities do not match the backup.')
        observed=set()
        for descriptor in descriptors:
            identity=descriptor['digest']
            if not environment.IMAGE_ID.fullmatch(identity):raise ExperimentError('INVALID_BACKUP','Invalid OCI identity.')
            blob=archive.getmember('blobs/sha256/'+identity.removeprefix('sha256:'))
            if not blob.isfile() or blob.size>8*1024**2:raise ExperimentError('INVALID_BACKUP','Invalid OCI manifest blob.')
            raw=archive.extractfile(blob).read()
            if 'sha256:'+hashlib.sha256(raw).hexdigest()!=identity:
                raise ExperimentError('INVALID_BACKUP','OCI manifest checksum mismatch.')
            observed.add(json.loads(raw)['config']['digest'])
        if observed!=config_ids:raise ExperimentError('INVALID_BACKUP','OCI configuration identities do not match.')


def restore(engine, archive=None, *, replace=False, recover=False):
    # The project lease is outside the directory being replaced and survives swaps.
    lease=project_lock(engine.state,reconcile=False)
    try:
        if recover:return recover_restore(engine)
        if (engine.state/'restore-active.json').exists():
            raise ExperimentError('RESTORE_RECOVERY_REQUIRED','Run invariant experiment restore --recover first.')
        if not archive:raise ExperimentError('INVALID_ARGUMENTS','Restore requires a private backup archive.')
        token=uuid.uuid4().hex;stage=private_directory(engine.state/('restore-stage-'+token))
        old=engine.state/('restore-before-'+token)
        try:
            manifest=extract(Path(archive),stage,project=engine.project,destination=engine.root)
            database=stage/'state/store.sqlite3'
            if database.is_symlink() or not database.is_file():raise ExperimentError('INVALID_BACKUP','Missing ordinary SQLite database.')
            with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as connection:
                if connection.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ExperimentError('INVALID_BACKUP','Database integrity failed.')
                if connection.execute('PRAGMA user_version').fetchone()[0] not in range(1,SCHEMA+1):raise ExperimentError('UNSUPPORTED_STATE_SCHEMA','Unsupported backup schema.')
                row=connection.execute("SELECT value FROM metadata WHERE key='project'").fetchone()
                if row is None or row[0]!=str(engine.project):raise ExperimentError('BACKUP_IDENTITY_MISMATCH','Database belongs to another project.')
                for table,prefix in [('studies','study'),('trials','trial'),('attempts','attempt'),('baselines','baseline')]:
                    for (identity,) in connection.execute('SELECT id FROM '+table):
                        if not re.fullmatch(prefix+r'-[0-9a-f]{'+('64' if prefix=='baseline' else '32')+r'}',identity):
                            raise ExperimentError('INVALID_BACKUP','Unsafe saved identity.')
                required={json.loads(row[0])['image']['id'] for row in connection.execute('SELECT definition FROM studies')}
                for (raw,) in connection.execute('SELECT record FROM attempts'):
                    image=json.loads(raw).get('checkpoint',{}).get('image')
                    if image:required.add(image)
                if required!=set(manifest['images']):raise ExperimentError('INVALID_BACKUP','Backup omits an image referenced by saved state.')
                for status,raw in connection.execute('SELECT state,record FROM attempts'):
                    if status not in TERMINAL or json.loads(raw).get('cleanup_confirmed') is not True:
                        raise ExperimentError('INVALID_BACKUP','Backup contains active or unresolved execution state.')
            from .store import StudyStore
            from .definitions import validate
            probe=StudyStore(stage/'state',project=engine.project)
            for row in probe.list(100000):validate(probe.inspect(row['id'])['frozen']['definition'])
            verify_images(stage/'images.tar',set(manifest['images']))
            with quiet_without_project(engine):
                if engine.store.list(1) and not replace:
                    raise ExperimentError('RESTORE_DESTINATION_EXISTS','Existing studies were kept.',
                                          'Use --replace to retain current state in a private sibling directory and restore this backup.')
                workspace.docker('image','load','--input',str(stage/'images.tar'),timeout=1200)
                for image in manifest['images']:
                    if json.loads(workspace.docker('image','inspect',image))[0]['Id']!=image:
                        raise ExperimentError('INVALID_BACKUP','Restored image identity mismatch.')
                from .engine import sync_directory
                with ExitStack() as fresh_locks:
                    for folder in (stage/'state/controller',stage/'state/mutations'):
                        fresh_locks.callback(project_lock(folder).close)
                    journal={'token':token,'project':str(engine.project),'schema':1}
                    workspace.private_write(engine.state/'restore-active.json',{**journal,'digest':digest(journal)})
                    sync_directory(engine.state)
                    if engine.root.exists():engine.root.rename(old)
                    sync_directory(engine.state)
                    (stage/'state').rename(engine.root)
                    sync_directory(stage);sync_directory(engine.state)
                    (engine.state/'restore-active.json').unlink()
                    sync_directory(engine.state)
                return {'restored':True,'studies':len(engine.store.list(100000)),'images':len(manifest['images']),
                        'previous_state':str(old) if old.exists() else None,'model_turns':0}
        finally:
            if not (engine.state/'restore-active.json').exists():
                from .snapshots import unseal_tree
                unseal_tree(stage);shutil.rmtree(stage)
    finally:lease.close()


@contextmanager
def quiet_without_project(engine):
    """Restore already holds the project lease; lock both source and new state."""
    with ExitStack() as stack:
        for folder in (engine.root/'controller',engine.root/'mutations'):
            lease=project_lock(folder);stack.callback(lease.close)
        if (engine.state/'current.json').exists() or (engine.root/'apply-active.json').exists():
            raise ExperimentError('RECOVERY_REQUIRED','Finish/recover ordinary work and apply before restoring.')
        for row in engine.store.list(100000):
            for trial in engine.inspect(row['id'])['trials']:
                if any(a['state'] not in TERMINAL or a['record'].get('cleanup_confirmed') is not True for a in trial['attempts']):
                    raise ExperimentError('ACTIVE_STATE','Stop and recover all attempts before restoring.')
        yield
