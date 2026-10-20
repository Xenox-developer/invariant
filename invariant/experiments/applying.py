"""Destination-bound apply plans, journaled writes, and conservative crash rollback."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import uuid

from .. import workspace
from ..execution import private_directory, project_lock
from .definitions import canonical, digest, parse_json
from .errors import ExperimentError
from .files import write_file
from .lifecycle import request_key
from .results import describe, resolve, excluded, safe_links
from .snapshots import regular_bytes
from .store import StudyStore
from . import tree

MAX_PLAN=32*1024**2


def sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def read_json(root,path):
    try:
        value=parse_json(regular_bytes(root,path,maximum=MAX_PLAN)[0],'private apply metadata')
        if not isinstance(value,dict):raise ValueError()
        return value
    except (ExperimentError,ValueError):
        raise ExperimentError('CORRUPT_PLAN','Apply metadata is invalid: '+path,
            'Restore the private plan/journal from backup; do not remove its active marker to bypass recovery.') from None


def read_journal(folder,plan):
    value=read_json(folder,'journal.json');progress=value.get('progress')
    if (value.get('journal_digest')!=digest({k:v for k,v in value.items() if k!='journal_digest'})
        or value.get('plan_digest')!=digest(plan) or not isinstance(progress,list) or len(progress)!=len(plan['steps'])
        or not all(p in ('pending','intent','done','restored') for p in progress)
        or value.get('state') not in ('applying','committed','rolled_back','blocked')
        or value.get('state')=='committed' and (not all(p=='done' for p in progress) or not isinstance(value.get('result'),dict))):
        raise ExperimentError('CORRUPT_PLAN','Apply journal integrity check failed.',
            'Restore the private journal from backup. Project writes remain blocked; do not discard recovery data.')
    return value


def load(root,identifier):
    if not re.fullmatch(r'plan-[0-9a-f]{32}',identifier):raise ExperimentError('INVALID_REQUEST','Invalid apply plan ID.')
    with StudyStore(root).connection() as db:
        row=db.execute('SELECT * FROM apply_plans WHERE id=?',(identifier,)).fetchone() if db.execute('PRAGMA user_version').fetchone()[0]>=5 else None
        project=db.execute("SELECT value FROM metadata WHERE key='project'").fetchone()
    if row is None:raise ExperimentError('NOT_FOUND','Unknown apply plan ID.')
    plan=read_json(root,'apply-plans/'+identifier+'/plan.json')
    if digest(plan)!=row['digest'] or canonical(plan)!=row['record'] or plan['project']!=project['value']:
        raise ExperimentError('CORRUPT_PLAN','Saved plan differs from its durable record.')
    return plan,root/'apply-plans'/identifier


def ensure_ready(engine):
    if (engine.state/'current.json').exists():
        raise ExperimentError('WORKSPACE_RECOVERY_REQUIRED','An ordinary chat is active or needs recovery.',
                              'Finish/recover the ordinary chat before planning or applying project changes.')


def steps_for(changes,plan_id):
    steps=[]
    def add(path,before,after):
        index=len(steps);parent=PurePosixPath(path).parent
        prefix=(str(parent)+'/' if str(parent)!='.' else '')+'.invariant-apply-'+plan_id[5:]+'-'+str(index)
        steps.append({'path':path,'before':before,'after':after,'temporary':prefix,'undo_temporary':prefix+'-undo'})
    removed=[r for r in changes if r['before'] and (r['after'] is None or r['before']['type']!=r['after']['type'])]
    for r in sorted(removed,key=lambda r:(-len(PurePosixPath(r['path']).parts),r['path'])):add(r['path'],r['before'],None)
    dirs=[r for r in changes if r['after'] and r['after']['type']=='directory' and (not r['before'] or r['before']['type']!='directory')]
    for r in sorted(dirs,key=lambda r:(len(PurePosixPath(r['path']).parts),r['path'])):add(r['path'],None,{'type':'directory','mode':0o700})
    for r in changes:
        if r['after'] and r['after']['type']!='directory':
            add(r['path'],r['before'] if r['before'] and r['before']['type']==r['after']['type'] else None,r['after'])
    for r in sorted(changes,key=lambda r:(-len(PurePosixPath(r['path']).parts),r['path'])):
        if r['after'] and r['after']['type']=='directory':
            before=r['before'] if r['before'] and r['before']['type']=='directory' else {'type':'directory','mode':0o700}
            if before!=r['after']:add(r['path'],before,r['after'])
    return steps


def plan(engine,identifier,*,request_id=None):
    request_id=request_key(request_id);fingerprint=digest({'operation':'apply_plan','identifier':identifier})
    replay=engine.store.lookup_operation(request_id,fingerprint)
    if replay is not None:return replay
    with project_lock(engine.root/'mutations',timeout=3):
        with engine._lock(reconcile=False):
            ensure_ready(engine)
            replay=engine.store.lookup_operation(request_id,fingerprint)
            if replay is not None:return replay
            report=describe(engine,identifier)
            study,attempt,base,saved,before,after=resolve(engine,report['attempt_id'])
            changes=report['changes']
            if len(changes)>10000:raise ExperimentError('APPLY_LIMIT','Review/apply at most 10,000 changed paths in one result.')
            # Cross-version case aliases would refer to the same path on macOS.
            names={}
            for p in before['entries'].keys()|after['entries'].keys():
                if p.startswith('@input/'):continue
                if p.casefold() in names and names[p.casefold()]!=p:raise ExperimentError('CASE_COLLISION','Case-only path replacements require a new unambiguous result.')
                names[p.casefold()]=p
            paths={r['path'] for r in changes}
            desired={p:r for p,r in after['entries'].items() if not excluded(p,study)}
            link_paths=safe_links(desired)
            paths|=link_paths
            for p in list(paths):paths.update(str(a) for a in PurePosixPath(p).parents if str(a)!='.')
            guards={};conflicts=[];changed={r['path']:r for r in changes}
            for p in sorted(paths):
                current=tree.entry(engine.project,p);guards[p]={'entry':current}
                if current and current['type']=='directory':guards[p]['identity']=tree.identity(engine.project/p)
                if p in changed and current!=changed[p]['before']:
                    conflicts.append({'path':p,'reason':'baseline_changed','expected':changed[p]['before'],'actual':current})
                if p not in changed and current and current['type']!='directory' and any(q.startswith(p+'/') for q in changed):
                    conflicts.append({'path':p,'reason':'unsafe_parent'})
                if p in link_paths and p not in changed and current!=desired.get(p):
                    conflicts.append({'path':p,'reason':'link_dependency_changed'})
                if p in changed and current and current['type']=='directory' and (not changed[p]['after'] or changed[p]['after']['type']!='directory'):
                    children=tree.entry(engine.project,p,children=True)['children'];guards[p]['children']=children
                    for name in children:
                        child=p+'/'+name
                        if child not in changed or changed[child]['after'] is not None:
                            conflicts.append({'path':child,'reason':'unplanned_directory_content'})
            plan_id='plan-'+uuid.uuid4().hex
            value={**report,'schema_version':1,'plan_id':plan_id,'project':str(engine.project),
                'root_identity':tree.identity(engine.project),'guards':guards,'conflicts':conflicts,
                'steps':steps_for(changes,plan_id),'applied':False,'state':'conflicted' if conflicts else 'planned'}
            value['destination_revision']=digest({'root':value['root_identity'],'guards':guards})
            if len(json.dumps(value,indent=2).encode())+1>MAX_PLAN:raise ExperimentError('APPLY_LIMIT','Apply plan exceeds its metadata limit.')
            folder=private_directory(engine.root/'apply-plans')/plan_id;folder.mkdir(mode=0o700)
            private_directory(folder/'desired')
            for index,step in enumerate(value['steps']):
                row=step['after']
                if row and row['type']=='file':
                    data,_=regular_bytes(saved,step['path'],maximum=row['size']);write_file(folder/'desired'/str(index),data,0o400)
            workspace.private_write(folder/'plan.json',value);sync(folder);sync(folder.parent)
            with engine.store.connection(write=True) as db:
                db.execute('INSERT INTO apply_plans VALUES (?,?,?,?,?)',(plan_id,value['study_id'],value['attempt_id'],canonical(value),digest(value)))
            return engine.store.control_operation(request_id,fingerprint,value,complete=True)


def check_destination(plan):
    project=Path(plan['project'])
    if tree.identity(project)!=plan['root_identity']:raise ExperimentError('STALE_PLAN','Project directory was replaced.')
    changed=[]
    for path,guard in plan['guards'].items():
        current=tree.entry(project,path)
        if current!=guard['entry'] or ('identity' in guard and tree.identity(project/path)!=guard['identity']):changed.append(path)
        elif 'children' in guard and tree.entry(project,path,children=True)['children']!=guard['children']:changed.append(path)
    if changed:raise ExperimentError('STALE_PLAN','Project paths changed after review; nothing was applied.',
        'Create and review a new dry-run plan. INVARIANT never silently merges changes.',details={'paths':changed})


def save_journal(folder,journal):
    journal['journal_digest']=digest({k:v for k,v in journal.items() if k!='journal_digest'})
    workspace.private_write(folder/'journal.json',journal);sync(folder)


def cleanup_temporary(plan,step,key,expected):
    path=step[key];project=Path(plan['project'])
    current=tree.entry(project,path)
    if current is None:return
    if current!=expected:raise ExperimentError('APPLY_RECOVERY_BLOCKED','An apply staging file was changed: '+path)
    tree.set_entry(project,path,current,None,None,path+'-unused',plan['root_identity'])


def rollback(plan,folder,journal):
    project=Path(plan['project']);blocked=[]
    if tree.identity(project)!=plan['root_identity']:
        raise ExperimentError('APPLY_RECOVERY_BLOCKED','Restore the original project directory before recovery.')
    for index in reversed(range(len(plan['steps']))):
        if journal['progress'][index] not in ('intent','done'):continue
        step=plan['steps'][index]
        try:
            cleanup_temporary(plan,step,'temporary',step['after'])
            cleanup_temporary(plan,step,'undo_temporary',step['before'])
            current=tree.entry(project,step['path'])
            if current!=step['before']:
                if current!=step['after']:
                    raise ExperimentError('APPLY_RECOVERY_BLOCKED','External edit retained: '+step['path'])
                blob=folder/'backup'/str(index)
                tree.set_entry(project,step['path'],current,step['before'],blob,step['undo_temporary'],plan['root_identity'])
            journal['progress'][index]='restored';save_journal(folder,journal)
        except (OSError,RuntimeError) as error:
            blocked.append({'path':step['path'],'reason':getattr(error,'code','RESTORE_FAILED'),
                            'backup':str(folder/'backup'/str(index))})
    journal.update(state='blocked' if blocked else 'rolled_back',blocked=blocked);save_journal(folder,journal)
    if blocked:
        raise ExperimentError('APPLY_RECOVERY_BLOCKED','Recovery preserved external edits or could not restore files.',
            'Inspect the journal/backups. Restore each blocked path to its reviewed before/after content, then run experiment apply --recover. New managed project writes are blocked.',
            details={'plan_id':plan['plan_id'],'journal':str(folder/'journal.json'),'blocked':blocked})


def reconcile_pending(state):
    """Called only while holding the original project's kernel lease."""
    state=Path(state);marker=state/'experiments/apply-active.json'
    if not marker.exists() and not marker.is_symlink():return {'recovered':False}
    record=read_json(state,'experiments/apply-active.json')
    plan,folder=load(state/'experiments',record['plan_id'])
    if plan['project']!=record['project']:raise ExperimentError('CORRUPT_PLAN','Active apply belongs to another project.')
    journal=read_journal(folder,plan)
    if journal['state']!='committed':rollback(plan,folder,journal)
    if journal.get('request_id'):
        result=journal.get('result') if journal['state']=='committed' else {
            'plan_id':plan['plan_id'],'applied':False,'state':'rolled_back','operation_failed':True,
            'error_code':'PLAN_RECOVERED','recovery':'Create and review a new dry-run plan; this operation will not replay.'}
        StudyStore(state/'experiments',project=plan['project']).control_operation(journal['request_id'],
            digest({'operation':'apply','plan_id':plan['plan_id']}),result,complete=True)
    marker.unlink();sync(marker.parent)
    return {'recovered':journal['state']!='committed','committed':journal['state']=='committed','plan_id':plan['plan_id'],
            'state':journal['state'],'project_files_changed':False}


def recover(engine,*,request_id=None):
    request_id=request_key(request_id);fingerprint=digest({'operation':'recover_apply'})
    replay=engine.store.lookup_operation(request_id,fingerprint)
    if replay is not None:return replay
    with project_lock(engine.state,timeout=3,reconcile=False):
        result=reconcile_pending(engine.state)
        return engine.store.control_operation(request_id,fingerprint,result,complete=True)


def apply(engine,plan_id,*,request_id=None):
    request_id=request_key(request_id);fingerprint=digest({'operation':'apply','plan_id':plan_id})
    replay=engine.store.lookup_operation(request_id,fingerprint)
    if replay is not None and not replay.get('operation_pending'):
        if replay.get('operation_failed'):raise ExperimentError(replay['error_code'],'The earlier apply was rolled back.',replay['recovery'])
        return replay
    with project_lock(engine.root/'mutations',timeout=3):
        with engine._lock():
            ensure_ready(engine)
            value,folder=load(engine.root,plan_id)
            if value['project']!=str(engine.project):raise ExperimentError('FOREIGN_STATE','Apply plan belongs to another project.')
            if (folder/'journal.json').exists():
                journal=read_journal(folder,value)
                if journal['state']=='committed':return engine.store.control_operation(request_id,fingerprint,journal['result'],complete=True)
                raise ExperimentError('PLAN_RECOVERED','An earlier apply was rolled back; this plan cannot be replayed.', 'Create a new dry-run plan and use a new request ID.')
            if value['conflicts']:raise ExperimentError('APPLY_CONFLICT','The reviewed plan contains conflicts.',details={'conflicts':value['conflicts']})
            study=engine.inspect(value['study_id'])
            if study['selection']['revision']!=value['selection_revision']:raise ExperimentError('STALE_PLAN','Selection changed after review; create a new plan.')
            source=resolve(engine,value['attempt_id'])
            if source[-1]['content_digest']!=value['result_digest']:raise ExperimentError('CORRUPT_RESULT','The bound result changed.')
            check_destination(value)
            required=sum((s['before'] or {}).get('size',0)+(s['after'] or {}).get('size',0) for s in value['steps'])+16*1024**2
            if shutil.disk_usage(folder).free<required:raise ExperimentError('DISK_HEADROOM','Insufficient space for staged changes and rollback backups.')
            private_directory(folder/'backup')
            for index,step in enumerate(value['steps']):
                before=step['before']
                if before and before['type']=='file':
                    # Paths removed/recreated by an earlier step still use their original content.
                    data,_=regular_bytes(engine.project,step['path'],maximum=before['size'])
                    if hashlib.sha256(data).hexdigest()!=before['sha256']:raise ExperimentError('STALE_PLAN','Destination changed while preparing backups.')
                    write_file(folder/'backup'/str(index),data,0o400)
            journal={'schema_version':1,'plan_digest':digest(value),'request_id':request_id,'state':'applying','progress':['pending']*len(value['steps']),'blocked':[]}
            save_journal(folder,journal);sync(folder/'backup')
            engine.store.control_operation(request_id,fingerprint,{'plan_id':plan_id})
            workspace.private_write(engine.root/'apply-active.json',{'schema_version':1,'plan_id':plan_id,'project':str(engine.project)});sync(engine.root)
            try:
                check_destination(value)
                for index,step in enumerate(value['steps']):
                    journal['progress'][index]='intent';save_journal(folder,journal)
                    tree.set_entry(engine.project,step['path'],step['before'],step['after'],folder/'desired'/str(index),step['temporary'],value['root_identity'])
                    journal['progress'][index]='done';save_journal(folder,journal)
                result={'plan_id':plan_id,'study_id':value['study_id'],'attempt_id':value['attempt_id'],'applied':True,
                        'paths':[r['path'] for r in value['changes']],'destination_revision':value['destination_revision'],
                        'environment_applied':False}
                journal.update(state='committed',result=result);save_journal(folder,journal)
                (engine.root/'apply-active.json').unlink();sync(engine.root)
            except BaseException:
                reconcile_pending(engine.state)
                raise
            return engine.store.control_operation(request_id,fingerprint,result,complete=True)
