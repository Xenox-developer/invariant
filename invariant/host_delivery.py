"""The sole host writer: exported artifacts and guarded, reviewed source changes."""
import json
import os
from pathlib import Path
import shutil
import stat
import uuid
import contextlib
import contextvars
import fcntl
import functools

from .docker_storage import docker, atomic_json
from .experiments import tree
from .experiments.errors import ExperimentError
from .experiments.definitions import digest

_collection_locks=contextvars.ContextVar('invariant_host_collection_locks',default=frozenset())


@contextlib.contextmanager
def collection_lock(project):
    """Serialize host publication, including the manifest/copy/index transaction."""
    key=str(Path(project).resolve());held=_collection_locks.get()
    if key in held:
        yield;return
    from .execution import private_directory
    directory=private_directory(Path(project)/'.invariant/logs')
    fd=os.open(directory/'.collection.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1:
            raise RuntimeError('Unsafe collection lock')
        fcntl.flock(fd,fcntl.LOCK_EX);token=_collection_locks.set(held|{key})
        try:yield
        finally:_collection_locks.reset(token)
    finally:os.close(fd)


def serialized_collection(function):
    @functools.wraps(function)
    def invoke(manager,*args,**kwargs):
        with collection_lock(manager.project):return function(manager,*args,**kwargs)
    return invoke


def copy_bundle(manager, source, destination):
    # Source must be a manager-owned volume path, never an arbitrary container path.
    manager.path_ref(source)
    destination=Path(destination)
    if destination.is_symlink():raise RuntimeError('Output destination cannot be a symlink')
    for parent in destination.parents:
        if parent.is_symlink():raise RuntimeError('Output parent cannot be a symlink')
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Docker extracts only into a fresh, unpublished artifact directory.
    stage=destination.parent/('.export-'+uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    try:
        docker('cp',manager.name+':'+source+'/.',str(stage),timeout=None)
        for base,dirs,files in os.walk(stage,followlinks=False):
            for name in dirs+files:
                path=Path(base)/name;info=path.lstat()
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink==1):
                    raise RuntimeError('Unsafe exported artifact')
        if destination.exists():destination=destination.with_name(destination.name+'-'+uuid.uuid4().hex[:12])
        os.rename(stage,destination)
        return destination
    finally:
        if stage.exists():shutil.rmtree(stage)


def bundle_inventory(path):
    import hashlib
    path=Path(path)
    if not path.is_dir() or any(p.is_symlink() for p in (path,*path.parents)):return None
    result={}
    for base,dirs,files in os.walk(path,followlinks=False):
        for name in dirs+files:
            file=Path(base)/name
            if file.is_symlink():return None
            if file.is_file():result[file.relative_to(path).as_posix()]=hashlib.sha256(file.read_bytes()).hexdigest()
    return result


@serialized_collection
def export_once(manager,key,revision,source,target):
    cached=manager.request('export_cache',{'key':key},raw=True)
    if cached and cached['revision']==revision:
        existing=Path(cached['path'])
        if existing.is_relative_to(manager.project/'.invariant') and bundle_inventory(existing)==cached['inventory']:
            return str(existing)
    result=copy_bundle(manager,source,target)
    manager.request('export_cache',{'key':key,'value':{'revision':revision,'path':str(result),
                    'inventory':bundle_inventory(result)}},raw=True)
    return str(result)


@serialized_collection
def export_logs(manager):
    result=manager.request('export_logs',raw=True)
    target=manager.project/'.invariant/logs'/('export-'+uuid.uuid4().hex)
    return export_once(manager,'logs',result['revision'],result['directory'],target)


@serialized_collection
def collect_context(manager,reference,*,log_cursor=None):
    """One manifest request and at most one acknowledgement for all exports."""
    result=manager.request('collect_prepare',{'run_ref':reference,'log_cursor':log_cursor},raw=True)
    updates={};outputs=[]
    def collect(key,revision,source,target):
        cached=result['cache'].get(key)
        if cached and cached['revision']==revision:
            path=Path(cached['path'])
            if path.is_relative_to(manager.project/'.invariant') and bundle_inventory(path)==cached['inventory']:return str(path)
        path=copy_bundle(manager,source,target)
        updates[key]={'revision':revision,'path':str(path),'inventory':bundle_inventory(path)}
        return str(path)
    for row in result['results']:
        outputs.append(collect('result:'+row['attempt_id'],digest(row['entries']),row['directory'],manager.project/'.invariant/results'/row['attempt_id']))
    logs=result['logs'];log_key='logs:'+str(log_cursor or 'full')
    exported=collect(log_key,logs['revision'],logs['directory'],manager.project/'.invariant/logs'/('export-'+uuid.uuid4().hex))
    if updates:manager.request('export_cache',{'updates':updates},raw=True)
    return {'run_ref':reference,'exported_results':outputs,'exported_logs':exported,'log_cursor':logs['cursor'],
            'logs_full':logs['full'],'logs_deleted':logs['deleted']}


def apply_bundle(manager, folder):
    value=json.loads((folder/'manifest.json').read_text())
    plan={k:v for k,v in value.items() if k not in ('digest','journal')}
    if digest(plan)!=value['digest'] or plan['project']!=str(manager.project):
        raise RuntimeError('Delivery manifest integrity failed')
    journal=value.get('journal') or {'state':'prepared','progress':['pending']*len(plan['steps']),
                                    'root_identity':tree.identity(manager.project),'plan_digest':value['digest']}
    def save():
        manager.request('delivery_update',{'plan_id':plan['plan_id'],'journal':journal},raw=True)
        atomic_json(folder/'host-delivery.json',journal)
    if journal['plan_digest']!=value['digest']:raise RuntimeError('Delivery recovery manifest changed')
    if journal['state']=='committed':return journal
    if tree.identity(manager.project)!=journal['root_identity']:
        raise ExperimentError('HOST_CONFLICT','Original project directory was replaced; result remains in Docker')
    def rollback():
        blocked=[]
        for index in reversed(range(len(plan['steps']))):
            if journal['progress'][index] not in ('intent','done'):continue
            step=plan['steps'][index]
            try:
                for key in ('temporary','undo_temporary'):
                    temporary=step[key];row=tree.entry(manager.project,temporary)
                    if row is None:continue
                    if row['type']=='directory':raise RuntimeError('Unexpected recovery directory')
                    recovery=folder/'recovered-temporaries';recovery.mkdir(exist_ok=True)
                    if row['type']=='file':
                        from .experiments.snapshots import regular_bytes
                        data,_=regular_bytes(manager.project,temporary,maximum=row['size'])
                        target=recovery/(str(index)+'-'+key+'-'+row['sha256'])
                        if not target.exists():
                            with open(target,'xb') as stream:stream.write(data)
                    else:atomic_json(recovery/(str(index)+'-'+key+'.json'),row)
                    tree.set_entry(manager.project,temporary,row,None,None,temporary+'-unused',journal['root_identity'])
                current=tree.entry(manager.project,step['path'])
                if current!=step['before']:
                    if current!=step['after']:raise RuntimeError('External edit retained')
                    tree.set_entry(manager.project,step['path'],current,step['before'],folder/'backup'/str(index),
                                   step['undo_temporary'],journal['root_identity'])
                journal['progress'][index]='restored';save()
            except (OSError,RuntimeError):blocked.append(step['path'])
        journal.update(state='blocked' if blocked else 'rolled_back',conflicts=blocked);save()
        if blocked:raise ExperimentError('HOST_CONFLICT','Recovery retained external edits',details={'paths':blocked})
    if journal['state'] in ('applying','blocked'):rollback()
    conflicts=[]
    for path,guard in plan['guards'].items():
        if tree.entry(manager.project,path)!=guard['entry']:conflicts.append(path)
        elif 'children' in guard and tree.entry(manager.project,path,children=True)['children']!=guard['children']:conflicts.append(path)
    if conflicts:
        journal.update(state='conflicted',conflicts=conflicts);save()
        raise ExperimentError('HOST_CONFLICT','Source files changed; result remains in Docker',details={'paths':conflicts})
    journal.update(state='applying',progress=['pending']*len(plan['steps']));save()
    try:
        for index,step in enumerate(plan['steps']):
            journal['progress'][index]='intent';save()
            tree.set_entry(manager.project,step['path'],step['before'],step['after'],folder/'desired'/str(index),
                           step['temporary'],journal['root_identity'])
            journal['progress'][index]='done';save()
        expected={step['path']:step['after'] for step in plan['steps']}
        expected.update(plan['protected_inputs'])
        conflicts=[p for p,row in expected.items() if tree.entry(manager.project,p)!=row]
        if conflicts:raise ExperimentError('HOST_CONFLICT','Files changed during verification',details={'paths':conflicts})
    except BaseException:
        journal['state']='blocked';save();rollback();raise
    journal.update(state='committed',verified=True,changed_paths=len({s['path'] for s in plan['steps']}),protected_inputs=len(plan['protected_inputs']));save()
    return journal


def deliver_plan(manager,identifier):
    result=manager.request('delivery_bundle',{'plan_id':identifier},raw=True)
    target=manager.project/'.invariant/results'/identifier
    target=copy_bundle(manager,result['directory'],target)
    try:return {'plan_id':identifier,'report':str(target/'host-delivery.json'),**apply_bundle(manager,target)}
    except ExperimentError as error:
        return {'state':'conflicted','plan_id':identifier,'report':str(target/'host-delivery.json'),'error':error.record()}


def deliver_pending(manager, *, recover_only=False):
    results=[]
    for identifier in manager.request('pending_deliveries',raw=True):
        if recover_only:
            result=manager.request('delivery_bundle',{'plan_id':identifier},raw=True)
            if result['manifest'].get('journal',{}).get('state') not in ('applying','blocked'):continue
        results.append(deliver_plan(manager,identifier))
    return results


@serialized_collection
def export_results(manager, *, ordinary=False,studies=None):
    result=manager.request('result_bundles',{'studies':studies},raw=True)
    if ordinary:result+=manager.request('ordinary_bundle',raw=True)
    outputs=[]
    for row in result:
        target=manager.project/'.invariant/results'/row['attempt_id']
        outputs.append(export_once(manager,'result:'+row['attempt_id'],digest(row.get('entries',row['attempt_id'])),row['directory'],target))
    return outputs
