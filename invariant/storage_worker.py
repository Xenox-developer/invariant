"""Trusted, volume-backed coordinator RPC. Never executed by an agent."""
import contextlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import sys
import tarfile
import tempfile

from . import docker_storage as storage, workspace


def unpack(archive, destination):
    links=[]
    for item in archive:
        path=PurePosixPath(item.name)
        if path.is_absolute() or '..' in path.parts or not path.parts:
            raise RuntimeError('Unsafe archive path')
        target=destination/path
        target.parent.mkdir(parents=True,exist_ok=True)
        if any(p.is_symlink() for p in target.parents if p!=destination.parent):
            raise RuntimeError('Archive path follows a symlink')
        if item.isdir():target.mkdir(exist_ok=True);target.chmod(item.mode&0o777)
        elif item.isfile():
            with archive.extractfile(item) as source,open(target,'xb') as output:shutil.copyfileobj(source,output)
            target.chmod(item.mode&0o777)
        elif item.issym():links.append((target,item.linkname))
        else:raise RuntimeError('Archive contains a special or hard-linked file')
    for path,target in links:os.symlink(target,path)


def entries(root):
    from .experiments import tree
    result={}
    for base,dirs,files in os.walk(root,followlinks=False):
        for name in sorted(dirs+files):
            relative=(Path(base)/name).relative_to(root).as_posix()
            result[relative]=tree.entry(root,relative)
    return result


def reconcile_delivered_source(project, *, locked=False):
    """A delivered edit becomes the new host baseline, including later resets."""
    state=workspace.state_for(project);path=state/'source-mirror.json'
    if not locked:
        from .execution import project_lock
        with project_lock(state,reconcile=False,timeout=3):return reconcile_delivered_source(project,locked=True)
    if not path.exists():return
    acknowledgments=state/'source-delivery-ack.json'
    processed=set(json.loads(acknowledgments.read_text())) if acknowledgments.exists() else set()
    mirror=json.loads(path.read_text());changed=False
    journals=sorted((state/'experiments/apply-plans').glob('*/host-delivery.json'),key=lambda p:p.stat().st_mtime_ns)
    for journal in journals:
        if journal.parent.name in processed:continue
        if json.loads(journal.read_text()).get('state')!='committed':continue
        from .experiments.applying import load
        plan,_=load(state/'experiments',journal.parent.name)
        processed.add(journal.parent.name)
        for step in plan['steps']:
            key=step['path']
            if mirror.get(key)==step['before']:
                if step['after'] is None:mirror.pop(key,None)
                else:mirror[key]=step['after']
                changed=True
    if changed:workspace.private_write(path,mirror)
    workspace.private_write(acknowledgments,sorted(processed))


def undelivered_source(project):
    state=workspace.state_for(project);marker=state/'source-rebased-plans.json'
    processed=set(json.loads(marker.read_text())) if marker.exists() else set()
    paths={};plans=[]
    from .experiments.applying import load
    for journal in sorted((state/'experiments/apply-plans').glob('*/host-delivery.json'),key=lambda p:p.stat().st_mtime_ns):
        if journal.parent.name in processed or json.loads(journal.read_text()).get('state')!='conflicted':continue
        plan,_=load(state/'experiments',journal.parent.name)
        plans.append(journal.parent.name)
        for step in plan['steps']:paths[step['path']]=step['after']
    return paths,processed|set(plans)


def import_source(project):
    from .execution import project_lock
    from .experiments import tree
    state=workspace.state_for(project);state.mkdir(parents=True,exist_ok=True)
    with project_lock(state,reconcile=False,timeout=3):
        with tempfile.TemporaryDirectory(dir=state,prefix='source-import-') as temporary:
            stage=Path(temporary)
            with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:unpack(archive,stage)
            reconcile_delivered_source(project,locked=True)
            incoming=entries(stage);record=state/'source-mirror.json'
            previous=json.loads(record.read_text()) if record.exists() else {}
            undelivered,rebased=undelivered_source(project)
            changes=[];conflicts=[]
            for path in sorted(set(incoming)|set(previous)|set(undelivered)):
                before=previous.get(path);after=incoming.get(path);current=tree.entry(project,path)
                retained_result=path in undelivered and current==undelivered[path]
                if before==after and not retained_result:continue
                if retained_result:
                    if current!=after:changes.append((path,after))
                elif current not in (before,after) and not (before is None and current and after and current['type']==after['type']=='directory'):
                    conflicts.append(path)
                elif current!=after:changes.append((path,after))
            if conflicts:raise RuntimeError('Host and Docker source both changed: '+', '.join(conflicts[:20]))
            identity=tree.identity(project)
            import uuid
            transaction=uuid.uuid4().hex
            for index,(path,after) in enumerate(sorted(changes,key=lambda x:(-len(PurePosixPath(x[0]).parts),x[0]))):
                current=tree.entry(project,path)
                if current is not None and (after is None or current['type']!=after['type']):
                    temporary=str(PurePosixPath(path).parent/('.invariant-import-'+transaction+'-remove-'+str(index)))
                    tree.set_entry(project,path,current,None,None,temporary,identity)
            for index,(path,after) in enumerate(sorted(changes,key=lambda x:(len(PurePosixPath(x[0]).parts),x[0]))):
                if after is None:continue
                current=tree.entry(project,path)
                if current==after:continue
                temporary=str(PurePosixPath(path).parent/('.invariant-import-'+transaction+'-'+str(index)))
                # All ancestry is opened without following links, including
                # when a disconnected executor changed a project directory.
                tree.set_entry(project,path,current,after,stage/path,temporary,identity)
            workspace.private_write(record,incoming)
            workspace.private_write(state/'source-rebased-plans.json',sorted(rebased))
            from .experiments.delivery import DeliveryPolicy
            policy={'include':['**/*'],'exclude':[],'respect_gitignore':True}
            workspace.private_write(state/'source-delivery.json',DeliveryPolicy.freeze(policy,stage,incoming))
    if not (project/'.invariant/config.json').exists():
        with contextlib.redirect_stdout(sys.stderr):workspace.init(project)
    return {'imported':len(incoming),'storage_layout':2}


def export_logs(project,*,cursor=None):
    import hashlib,re
    from .experiments.definitions import digest
    from .execution import project_lock
    if cursor is not None and (not isinstance(cursor,str) or not re.fullmatch(r'[0-9a-f]{64}',cursor)):
        raise RuntimeError('Invalid log cursor')
    state=workspace.state_for(project)
    with project_lock(state/'log-export',reconcile=False,timeout=30):
        destination=state/'exported-logs';destination.mkdir(parents=True,exist_ok=True)
        private={'native-home','review-runtime','codex','home','workspace','snapshot','baseline','baselines',
                 'input','inputs','desired','backup','runtimes','catalog-v3','validations','exported-logs','source-import','log-deltas'}
        names={'result.json','test-report.json','test-output.txt','decision.json','host-delivery.json'}
        selected={}
        for base,dirs,files in os.walk(state,followlinks=False):
            if 'models_cache.json' in files or 'state_5.sqlite' in files or 'config.toml' in files and 'auth.json' in files:
                dirs[:]=[];continue
            dirs[:]=[d for d in dirs if d not in private and not d.startswith('source-import-')]
            for name in files:
                source=Path(base)/name
                if source.is_symlink() or not (name in names or name.endswith(('.log','.usage.jsonl')) or base==str(state/'traces') and name.endswith('.jsonl') or name.startswith('runner.commands-') or name.startswith('ws-') and name.endswith('.jsonl')):continue
                relative=source.relative_to(state).as_posix();raw=source.read_bytes();sha=hashlib.sha256(raw).hexdigest()
                selected[relative]=sha;target=destination/relative
                if not target.is_file() or target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=sha:
                    target.parent.mkdir(parents=True,exist_ok=True);target.unlink(missing_ok=True);target.write_bytes(raw);target.chmod(0o600)
        index=state/'log-revisions.json';revisions=json.loads(index.read_text()) if index.exists() else {}
        previous=revisions.get(cursor) if cursor is not None else None
        revision=digest(selected)
        for target in destination.rglob('*'):
            if target.is_file() and target.relative_to(destination).as_posix() not in selected:target.unlink()
        if revision not in revisions:
            revisions[revision]=selected;revisions=dict(list(revisions.items())[-100:]);workspace.private_write(index,revisions)
        changed=sorted(name for name,sha in selected.items() if previous is None or previous.get(name)!=sha)
        deleted=sorted(set(previous or {})-set(selected))
        if cursor is not None:
            delta=state/'log-deltas'/digest({'base':cursor,'revision':revision});delta.mkdir(parents=True,exist_ok=True)
            for name in changed:
                target=delta/name;target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists():shutil.copyfile(destination/name,target)
            workspace.private_write(delta/'delta.json',{'base_cursor':cursor,'cursor':revision,'full':previous is None,'deleted':deleted})
            destination=delta
        return {'directory':str(destination),'files':changed,'revision':revision,'cursor':revision,
                'base_cursor':cursor,'full':previous is None,'deleted':deleted}


def engine(project):
    from .experiments.engine import ExperimentEngine
    return ExperimentEngine(project)


def pending_deliveries(project):
    from .experiments.applying import load,read_journal
    root=workspace.state_for(project)/'experiments';result=[]
    for folder in sorted((root/'apply-plans').glob('plan-*')):
        if not (folder/'journal.json').exists():continue
        plan,folder=load(root,folder.name);journal=read_journal(folder,plan)
        if plan.get('storage_layout')!=2 and not (folder/'host-delivery.json').exists():continue
        delivered=json.loads((folder/'host-delivery.json').read_text()) if (folder/'host-delivery.json').exists() else {}
        if journal['state']=='committed' and delivered.get('state') not in ('committed','conflicted'):result.append(folder.name)
    return result


def delivery_bundle(project,identifier):
    from .experiments.applying import load,read_journal
    from .experiments.definitions import digest
    plan,folder=load(workspace.state_for(project)/'experiments',identifier)
    journal=read_journal(folder,plan)
    if journal['state']!='committed':raise RuntimeError('Only a validated committed result can be delivered')
    from .experiments.results import resolve
    study,attempt,*_=resolve(engine(project),plan['attempt_id'])
    if not study['frozen']['definition'].get('evaluator') or attempt['record'].get('evaluation',{}).get('passed') is not True:
        raise RuntimeError('Automatic delivery requires configured, passing checks')
    # Repeat the final receipt/integrity check immediately before host delivery.
    from .experiments.results import describe
    report=describe(engine(project),plan['attempt_id'])
    if report.get('changes')!=plan.get('changes'):raise RuntimeError('Saved result changed after review')
    bundle=folder/'delivery';bundle.mkdir(exist_ok=True)
    for name in ('desired','backup'):
        shutil.copytree(folder/name,bundle/name,dirs_exist_ok=True)
    value={k:plan[k] for k in ('plan_id','project','guards','steps','protected_inputs')}
    value['digest']=digest(value)
    if (folder/'host-delivery.json').exists():value['journal']=json.loads((folder/'host-delivery.json').read_text())
    workspace.private_write(bundle/'manifest.json',value)
    return {'directory':str(bundle),'manifest':value}


def operate(project,operation,payload):
    if operation=='shutdown_metadata':
        from .catalog import close
        close();return {'closed':True}
    if operation=='export_cache':
        from .execution import project_lock
        path=workspace.state_for(project)/'export-cache.json'
        with project_lock(path.parent/'export-index',reconcile=False,timeout=10):
            rows=json.loads(path.read_text()) if path.exists() else {}
            if 'updates' in payload:
                rows.update(payload['updates']);workspace.private_write(path,rows);return {'updated':len(payload['updates'])}
            if 'keys' in payload:return {k:rows.get(k) for k in payload['keys']}
            if 'value' in payload:
                rows[payload['key']]=payload['value'];workspace.private_write(path,rows)
            return rows.get(payload['key'])
    if operation=='collect_prepare':
        from .run_context import operate as run_operation
        saved=run_operation(project,'run_status',payload)
        results=operate(project,'result_bundles',{'studies':saved['studies']})
        logs=export_logs(project,cursor=payload.get('log_cursor'))
        keys=['result:'+r['attempt_id'] for r in results]+['logs:'+str(payload.get('log_cursor') or 'full')]
        return {'results':results,'logs':logs,'cache':operate(project,'export_cache',{'keys':keys})}
    if operation.startswith('run_'):
        from .run_context import operate as context_operation
        return context_operation(project,operation,payload)
    if operation=='source_matches':
        from .experiments.definitions import digest
        from .execution import project_lock
        state=workspace.state_for(project);path=state/'source-mirror.json'
        with project_lock(state,reconcile=False,timeout=3):
            reconcile_delivered_source(project,locked=True)
            retained,_=undelivered_source(project)
            matches=(project/'.invariant/config.json').exists() and path.exists() and not retained and digest(json.loads(path.read_text()))==payload['revision']
        return {'matches':matches}

    if operation in ('gc_collect', 'gc_status', 'gc_configure'):
        from . import storage_gc
        if operation == 'gc_collect':return storage_gc.collect(**payload)
        if operation == 'gc_configure':return storage_gc.configure(payload)
        return storage_gc.status()
    if operation=='auth_status':
        from .agent_runtime import account_home
        target=account_home()/'auth.json'
        return {'present':target.is_file(),'destination':str(target)}
    if operation=='busy':
        state=workspace.state_for(project)
        active=False
        # A live process/kernel lease is authoritative; historical interrupted records can be recovered.
        from .execution import project_lock
        try:
            with project_lock(state,reconcile=False,timeout=0):pass
            with project_lock(state/'experiments/controller',reconcile=False,timeout=0):pass
            with project_lock(state/'tasks',reconcile=False,timeout=0):pass
        except RuntimeError:active=True
        return {'busy':active}
    if operation=='export_logs':return export_logs(project,cursor=payload.get('cursor'))
    if operation=='ordinary_bundle':
        from .experiments import tree
        from .experiments.delivery import DeliveryPolicy
        from .experiments.snapshots import protected
        from .experiments.definitions import digest
        state=workspace.state_for(project);mirror=state/'source-mirror.json'
        if not mirror.exists():return []
        before=json.loads(mirror.read_text());after=storage.local_source_manifest(project)
        policy=DeliveryPolicy({},project,before,frozen=json.loads((state/'source-delivery.json').read_text()),result_entries=after)
        changes=[{'path':p,'before':before.get(p),'after':after.get(p)} for p in sorted(set(before)|set(after))
                 if before.get(p)!=after.get(p) and not protected(p) and not policy.excluded(p,after.get(p) or before.get(p))]
        if not changes:return []
        identifier='ordinary-'+digest(changes)[:32];destination=state/'exports'/identifier
        destination.mkdir(parents=True,exist_ok=True)
        for row in changes:
            path=row['path'];after=row['after'];target=destination/'files'/path
            if after is None or after['type']=='symlink':continue
            target.parent.mkdir(parents=True,exist_ok=True)
            if after['type']=='directory':target.mkdir(exist_ok=True)
            else:shutil.copyfile(project/path,target);target.chmod(after['mode'])
        workspace.private_write(destination/'manifest.json',{'changes':changes,'verified':False,'automatic_application':False})
        return [{'attempt_id':identifier,'directory':str(destination)}]
    if operation=='result_bundles':
        from .experiments.results import describe,resolve
        e=engine(project);outputs=[]
        studies=[{'id':sid} for sid in payload['studies']] if payload.get('studies') is not None else e.list(limit=1000)['studies']
        for study in studies:
            full=e.inspect(study['id'])
            for trial in full['trials']:
                for attempt in trial['attempts']:
                    if attempt['state']!='succeeded' or attempt['record'].get('evaluation',{}).get('passed') is not True:continue
                    try:
                        resolved=resolve(e,attempt['id']);saved=resolved[3]
                        report=describe(e,attempt['id'],_resolved=resolved)
                    except RuntimeError:continue
                    destination=e.root/'exports'/attempt['id']
                    destination.mkdir(parents=True,exist_ok=True)
                    entries={r['path']:r['after'] for r in report['changes'] if r['after']}
                    from .experiments.results import verify_tree
                    cached=False
                    if (destination/'manifest.json').exists() and json.loads((destination/'manifest.json').read_text())==report:
                        try:
                            verify_tree(destination/'files',entries);cached=True
                        except (OSError,RuntimeError):pass
                    for row in ([] if cached else report['changes']):
                        path=row['path'];after=row['after']
                        if after is None:continue
                        target=destination/'files'/path;target.parent.mkdir(parents=True,exist_ok=True)
                        if after['type']=='file':shutil.copyfile(saved/path,target);target.chmod(after['mode'])
                        elif after['type']=='directory':target.mkdir(exist_ok=True)
                        # Symlinks are described in the manifest, not followed on export.
                    if not cached:workspace.private_write(destination/'manifest.json',report)
                    outputs.append({'attempt_id':attempt['id'],'directory':str(destination),'entries':entries})
        return outputs
    if operation=='pending_deliveries':return pending_deliveries(project)
    if operation=='delivery_bundle':return delivery_bundle(project,payload['plan_id'])
    if operation=='delivery_status':
        from .experiments.applying import load
        plan,folder=load(workspace.state_for(project)/'experiments',payload['plan_id'])
        path=folder/'host-delivery.json'
        if path.exists():return json.loads(path.read_text())
        return {'state':'pending' if plan.get('storage_layout')==2 else 'legacy_committed'}
    if operation=='delivery_update':
        from .experiments.applying import load
        _,folder=load(workspace.state_for(project)/'experiments',payload['plan_id'])
        workspace.private_write(folder/'host-delivery.json',payload['journal'])
        if payload['journal']['state']=='committed':reconcile_delivered_source(project)
        return payload['journal']
    if operation=='engine':
        method=payload['method']
        if payload.get('run_ref') and method in ('submit','validate','execute','launch','retry','continue_result','init_definition'):
            from .run_context import operate as context_operation
            from .experiments.definitions import digest
            saved=context_operation(project,'run_status',{'run_ref':payload['run_ref']})
            if saved['policy_digest']!=digest(workspace.load_project(project)):raise RuntimeError('Run settings changed; prepare a new run context')
        if method not in {'validate','submit','inspect','list','execute','continue_result','diff','plan_apply','apply',
                          'recover_apply','launch','select','stop','recover','retry','delete','results','logs','init_definition'}:
            raise RuntimeError('Unknown engine method')
        return getattr(engine(project),method)(*payload.get('args',[]),**payload.get('kwargs',{}))
    if operation in ('review_ready','review_decide'):
        from .task_review import Reviewer
        reviewer=Reviewer(project)
        if operation=='review_ready':return reviewer.ready(None)
        import re
        if not re.fullmatch('[0-9a-f]{64}',payload['folder_key']):raise RuntimeError('Invalid review key')
        folder=workspace.state_for(project)/'external-reviews'/payload['folder_key']
        return reviewer.decide(folder,payload['payload'],model=payload['model'],effort=payload['effort'],timeout=payload['timeout'])
    if operation=='task_runner':
        from .tasks import TaskRunner
        if payload['method'] not in ('run','status','read','continue_task'):raise RuntimeError('Unknown task method')
        return getattr(TaskRunner(project),payload['method'])(*payload.get('args',[]),**payload.get('kwargs',{}))
    if operation=='task_result':
        from .tasks import TaskRunner
        from . import task_results
        method=payload['method'];runner=TaskRunner(project)
        if method=='compact_record':
            return task_results.compact_record(runner,runner.status(payload['args'][0]),**payload.get('kwargs',{}))
        if method not in ('wait','review','apply'):raise RuntimeError('Unknown task result method')
        return getattr(task_results,method)(runner,*payload.get('args',[]),**payload.get('kwargs',{}))
    if operation=='task_execute':
        from .task_runtime import Coordinator
        return Coordinator(project).execute(**payload)
    if operation=='preflight':
        from .experiments.preflight import check_frozen
        return check_frozen(engine(project),*payload.get('args',[]),**payload.get('kwargs',{}))
    if operation=='export_report':
        from .experiments.exporting import export
        return export(engine(project),*payload.get('args',[]),**payload.get('kwargs',{}))
    if operation.startswith('migration_'):
        from .storage_migration import worker
        return worker(project,operation,payload)
    raise RuntimeError('Unknown storage operation')


def _main(request=None):
    if not storage.managed():raise RuntimeError('Storage worker requires the Docker manager')
    project=Path(os.environ['INVARIANT_PROJECT'])
    if len(sys.argv)>1 and sys.argv[1]=='cli':
        from .cli import main as cli
        sys.argv=['invariant',*sys.argv[2:]]
        # Keep API stdout free of manager-internal paths.
        if '--json' in sys.argv[1:]:
            import io
            output=io.StringIO()
            with contextlib.redirect_stdout(output):code=cli()
            manager=storage.Manager(project,prepare=False);manager.home=str(Path.home());manager.map=storage.mappings()
            for line in output.getvalue().splitlines():
                try:print(json.dumps({**manager.public(json.loads(line)),'storage_layout':2}))
                except ValueError:print(line)
            return code
        return cli()
    try:
        if len(sys.argv)>1 and sys.argv[1]=='import-source':result=import_source(project)
        else:
            request=request if request is not None else json.load(sys.stdin)
            if request.get('project')!=str(project):raise RuntimeError('Wrong project identity')
            with contextlib.redirect_stdout(sys.stderr):result=operate(project,request['operation'],request.get('payload',{}))
        print(json.dumps({'ok':True,'data':result,'error':None}));return 0
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,sqlite3.Error) as error:
        record=error.record() if hasattr(error,'record') else {'code':'STORAGE_ERROR','message':str(error)}
        print(json.dumps({'ok':False,'data':None,'error':record}));return 1


def main():
    if not storage.managed():raise RuntimeError('Storage worker requires the Docker manager')
    from .storage_gc import gate
    if len(sys.argv) == 1:
        request=json.load(sys.stdin)
        if request.get('operation') in ('gc_collect','gc_configure'):
            return _main(request)
        with gate():return _main(request)
    with gate():return _main()


if __name__=='__main__':sys.exit(main())
