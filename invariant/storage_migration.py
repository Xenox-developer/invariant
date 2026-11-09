"""Resumable copy/verify/switch/delete migration of INVARIANT-owned state only."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat

from . import docker_storage as storage


def path_list(root):
    result=[]
    for base,dirs,files in os.walk(root,followlinks=False):
        dirs.sort()
        result.extend((Path(base)/name).relative_to(root).as_posix() for name in sorted(dirs+files) if not stat.S_ISSOCK((Path(base)/name).lstat().st_mode))
    return result


def inventory(root, *, selection=None, paths=None, content_only=False):
    sha=hashlib.sha256();count=0;size=0
    if not root.exists():return None
    for relative in paths if paths is not None else path_list(selection or root):
        if Path(relative).is_absolute() or '..' in Path(relative).parts:raise RuntimeError('Unsafe inventory path')
        path=root/relative;info=path.lstat()
        row={'path':relative}
        if not content_only:row['mode']=stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            row.pop('mode',None);row.update(type='symlink',target=os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):row['type']='directory'
        elif stat.S_ISREG(info.st_mode):
            file_sha=hashlib.sha256()
            with open(path,'rb') as stream:
                while chunk:=stream.read(1024*1024):file_sha.update(chunk)
                after=os.fstat(stream.fileno())
            if (info.st_size,info.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('Migration source changed: '+relative)
            row.update(type='file',size=info.st_size,sha256=file_sha.hexdigest());size+=info.st_size
        elif stat.S_ISSOCK(info.st_mode):continue
        else:raise RuntimeError('Unsupported migration entry: '+relative)
        sha.update(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n');count+=1
    return {'format':'portable-posix-v2','digest':sha.hexdigest(),'entries':count,'bytes':size}


def remove_owned_tree(source):
    for base,dirs,files in os.walk(source,followlinks=False):
        os.chmod(base,0o700)
        for name in dirs:
            path=Path(base)/name
            if not path.is_symlink():path.chmod(0o700)
    shutil.rmtree(source)


def discover_project(folder):
    candidates=[folder/'environment.json',folder/'latest-chat.json',*(folder/'chats').glob('*.json')]
    for path in candidates:
        try:
            value=json.loads(path.read_text());project=value.get('project')
            if project and hashlib.sha256(os.fsencode(project)).hexdigest()[:24]==folder.name:return Path(project)
        except (OSError,ValueError,AttributeError):pass
    for db in (folder/'experiments').glob('*.sqlite*'):
        if db.name.endswith(('-wal','-shm')):continue
        try:
            with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as connection:
                row=connection.execute("SELECT value FROM metadata WHERE key='project'").fetchone()
            if row and hashlib.sha256(os.fsencode(row[0])).hexdigest()[:24]==folder.name:return Path(row[0])
        except sqlite3.Error:pass
    return None


def _merge(source,destination):
    """Allow a freshly initialized coordinator, never overwrite existing history."""
    if not destination.exists():
        shutil.copytree(source,destination,symlinks=True);return
    for path in sorted(source.iterdir()):
        target=destination/path.name
        if path.is_dir() and not path.is_symlink():
            if not target.exists():shutil.copytree(path,target,symlinks=True)
            elif target.is_dir() and not target.is_symlink():_merge(path,target)
            else:raise RuntimeError('Migration destination conflicts: '+path.name)
        elif target.exists() or target.is_symlink():
            if path.name=='lock' and path.stat().st_size==target.stat().st_size==0:continue
            if path.is_symlink() and target.is_symlink() and os.readlink(path)==os.readlink(target):continue
            if not path.is_symlink() and not target.is_symlink() and path.read_bytes()==target.read_bytes():continue
            raise RuntimeError('Migration destination conflicts: '+path.name)
        else:
            if path.is_symlink():os.symlink(os.readlink(path),target)
            else:shutil.copy2(path,target)
    destination.chmod(stat.S_IMODE(source.stat().st_mode))


def worker(project,operation,payload):
    root=Path.home()/'.invariant';key=payload.get('key','')
    if not key or '/' in key or key in ('.','..'):raise RuntimeError('Invalid migration key')
    directory=root/'migration'/key;directory.mkdir(parents=True,exist_ok=True)
    journal_path=directory/'journal.json'
    journal=json.loads(journal_path.read_text()) if journal_path.exists() else {'key':key,'phase':'pending'}
    stage=directory/'stage'
    if operation=='migration_status':return {**journal,'stage':str(stage)}
    if operation=='migration_begin':
        if journal.get('expected') and journal['expected']!=payload['expected']:
            if not (journal['phase']=='copying' and 'format' not in journal['expected']):
                raise RuntimeError('Migration source changed; retain both copies for inspection')
        journal.update(expected=payload['expected'],phase='copying' if journal['phase']=='pending' else journal['phase'])
    elif operation=='migration_verify':
        observed=inventory(stage)
        if observed!=journal['expected']:raise RuntimeError('Migration copy integrity failed; original retained')
        journal.update(phase='verified',verified=observed,restore_verified=True)
    elif operation=='migration_install':
        if journal['phase'] not in ('verified','installed','deleted'):raise RuntimeError('Migration has not been verified')
        relative=Path(payload['relative'])
        if relative.is_absolute() or '..' in relative.parts:raise RuntimeError('Invalid owned state path')
        destination=root/relative
        if journal['phase']=='verified':
            destination.parent.mkdir(parents=True,exist_ok=True)
            _merge(stage,destination)
            if inventory(destination,selection=stage)!=journal['expected']:
                raise RuntimeError('Installed state verification failed; original retained')
            journal.update(phase='installed',destination=str(destination))
    elif operation=='migration_remaining':
        if journal['phase'] not in ('installed','deleted'):raise RuntimeError('State is not installed')
        if inventory(stage,paths=payload['paths'],content_only=True)!=payload['observed']:
            raise RuntimeError('Remaining legacy files changed; retain them for inspection')
        return {'verified':True}
    elif operation=='migration_deleted':
        journal['phase']='deleted'
        from .workspace import private_write
        private_write(journal_path,journal)
        # The verified installed copy is the recovery source after host deletion.
        # Retain staging only when later container work changed that copy.
        if stage.exists() and inventory(Path(journal['destination']),selection=stage)==journal['expected']:
            remove_owned_tree(stage);journal['staging_released']=True
    else:raise RuntimeError('Unknown migration operation')
    from .workspace import private_write
    private_write(journal_path,journal)
    return {**journal,'stage':str(stage)}


def _migrate_owned(manager,source,relative):
    key=hashlib.sha256(relative.encode()).hexdigest()[:24]
    status=manager.request('migration_status',{'key':key},raw=True)
    if not source.exists():return status
    # Existing kernel leases prevent migration of any active legacy job.
    leases=[]
    try:
        for path in (source/'lock',source/'experiments/controller/lock'):
            if not path.exists():continue
            stream=open(path,'r+')
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:stream.close();raise RuntimeError('Finish active legacy runs before migration: '+relative) from None
            leases.append(stream)
        expected=status.get('expected') if status['phase'] in ('installed','deleted') else inventory(source)
        if status['phase'] not in ('installed','deleted'):
            status=manager.request('migration_begin',{'key':key,'expected':expected},raw=True)
        if status['phase']=='copying':
            storage.docker('exec',manager.name,'mkdir','-p',status['stage'])
            storage.docker('cp',str(source)+'/.',manager.name+':'+status['stage'],timeout=None)
            status=manager.request('migration_verify',{'key':key},raw=True)
        if status['phase']=='verified':status=manager.request('migration_install',{'key':key,'relative':relative},raw=True)
        if status['phase']=='installed':
            with storage.registry_lock() as registry:
                registry.setdefault('migrations',{})[key]={'phase':'installed','volume':storage.STATE_VOLUME,'path':relative}
            manager.request('migration_remaining',{'key':key,'paths':path_list(source),'observed':inventory(source,content_only=True)},raw=True)
            # Only caller-constructed paths under INVARIANT's private root reach here.
            remove_owned_tree(source)
            status=manager.request('migration_deleted',{'key':key},raw=True)
            with storage.registry_lock() as registry:registry['migrations'][key]['phase']='deleted'
        return {'key':key,'phase':status['phase'],'entries':expected['entries'],'bytes':expected['bytes']}
    finally:
        for stream in leases:stream.close()


def migrate_project(project,*,manager=None):
    project=Path(project).resolve();manager=manager or storage.Manager(project)
    from .workspace import state_for
    source=state_for(project)
    result=_migrate_owned(manager,source,'workspaces/'+manager.key) if source.exists() else {'phase':'absent'}
    migrate_config(project,manager)
    return {'project':str(project),'state':result,'storage_layout':2}


def migrate_config(project,manager):
    config=project/'.invariant/config.json'
    key=hashlib.sha256(os.fsencode(project)).hexdigest()[:24]
    volume=storage.owned_volume('invariant-project-'+key+'-v2')['Name']
    if config.exists():
        if config.is_symlink():raise RuntimeError('Legacy configuration cannot be a symlink')
        content=config.read_bytes()
        from .policy import normalize
        value=normalize(json.loads(content));value['host_access']=False
        program="import json,pathlib,sys;p=pathlib.Path('/project/.invariant/config.json');p.parent.mkdir(exist_ok=True,mode=0o700);v=json.load(sys.stdin);p.write_text(json.dumps(v,indent=2)+'\\n');p.chmod(0o600);print(json.dumps(json.loads(p.read_text())))"
        check=storage.docker('run','--rm','--interactive','--network=none','--read-only','--cap-drop=ALL',
             '--security-opt=no-new-privileges','--mount','type=volume,src='+volume+',dst=/project',
             '--entrypoint=python3',storage.IMAGE,'-B','-c',program,input=json.dumps(value).encode()).stdout
        if json.loads(check)!=value or config.read_bytes()!=content:raise RuntimeError('Configuration changed during migration')
        config.unlink()
    with storage.registry_lock() as registry:
        registry['projects'].setdefault(key,{}).update(project=str(project),project_volume=volume,
            state_volume=storage.STATE_VOLUME,layout_version=2)


def owned_runtime_paths(project):
    original=Path(project)/'.invariant'
    if not original.is_dir() or original.is_symlink():return []
    names={'native-home','codex','codex-home','review-runtime','review-runtime-v2','runtime-v1',
           'node_modules','.venv','venv','.cache','.npm','__pycache__','private-state'}
    selected=[]
    for base,dirs,files in os.walk(original,followlinks=False):
        dirs[:]=[name for name in dirs if name not in ('results','logs') and not (Path(base)/name).is_symlink()]
        for name in list(dirs):
            path=Path(base)/name
            fingerprints=('pyvenv.cfg','models_cache.json','state_5.sqlite','state_6.sqlite')
            owned=name in names or any((path/f).is_file() for f in fingerprints)
            owned=owned or ((path/'experiments/store.sqlite3').is_file() and (path/'experiments').is_dir())
            if owned:selected.append(path);dirs.remove(name)
    return selected


def legacy_config_projects(project):
    result=set();root=Path(project)/'.invariant'
    if not root.is_dir() or root.is_symlink():return result
    from .policy import normalize
    for base,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[n for n in dirs if n not in ('results','logs','node_modules','.venv','venv') and not (Path(base)/n).is_symlink()]
        if Path(base).name!='.invariant' or 'config.json' not in files:continue
        path=Path(base)/'config.json'
        if path.is_symlink():continue
        try:normalize(json.loads(path.read_text()))
        except (OSError,ValueError,RuntimeError):continue
        result.add(Path(base).parent)
    return result


def migrate_local_runtime(project,manager):
    original=Path(project)/'.invariant';result=[]
    key=hashlib.sha256(os.fsencode(Path(project).resolve())).hexdigest()[:24]
    for path in owned_runtime_paths(project):
        relative='legacy-local/'+key+'/'+path.relative_to(original).as_posix()
        result.append(_migrate_owned(manager,path,relative))
    return result


def migrate_all():
    for role in ('agent-runtime','codex-workspace'):
        if storage.docker('ps','--filter','label=io.invariant.role='+role,'--format','{{.Names}}').stdout.strip():
            raise RuntimeError('Finish active runs before migrating all state')
    path=storage.registry_path()
    registry=json.loads(path.read_text()) if path.exists() else {'projects':{}}
    for row in registry['projects'].values():
        if not row.get('manager'):continue
        probe=storage.docker('inspect',row['manager'],check=False)
        if probe.returncode==0 and json.loads(probe.stdout)[0]['State']['Running']:
            if storage.Manager(row['project'],prepare=False).request('busy',raw=True)['busy']:
                raise RuntimeError('Finish active coordinators before migration')
    manager=storage.Manager();root=Path.home()/'.invariant';results=[];projects={manager.project}
    projects.update(Path(row['project']) for row in registry['projects'].values() if row.get('project') and Path(row['project']).is_dir())
    for folder in sorted((root/'workspaces').glob('*')):
        if not folder.is_dir() or folder.is_symlink():raise RuntimeError('Unexpected owned workspace path')
        project=discover_project(folder)
        print('Moving owned state '+folder.name+' into Docker…',file=__import__('sys').stderr,flush=True)
        if project and project.is_dir():
            projects.add(project)
            results.append(_migrate_owned(manager,folder,'workspaces/'+folder.name))
            migrate_config(project,manager)
        else:results.append(_migrate_owned(manager,folder,'workspaces/'+folder.name))
    for root_project in list(projects):
        for project in legacy_config_projects(root_project):
            migrate_config(project,manager);projects.add(project)
    covered=[]
    for project in sorted(projects,key=lambda p:len(p.parts)):
        original=project/'.invariant'
        if any(original.is_relative_to(parent) for parent in covered):continue
        results.extend(migrate_local_runtime(project,manager));covered.append(original)
    tools=root/'tools'
    if tools.exists():results.append(_migrate_owned(manager,tools,'legacy-tools'))
    if (root/'workspaces').exists() and not any((root/'workspaces').iterdir()):(root/'workspaces').rmdir()
    return {'storage_layout':2,'migrations':results,'host_private_state_removed':not (root/'workspaces').exists() and not tools.exists()}
