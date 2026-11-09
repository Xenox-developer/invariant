"""Application-owned sequencing over a reusable Docker execution context."""
import hashlib
import json
from pathlib import Path
import re
import uuid


def record(project,reference):
    from .workspace import state_for
    if not isinstance(reference,str) or not re.fullmatch(r'run-[0-9a-f]{32}',reference):raise RuntimeError('Invalid run reference')
    return state_for(project)/'runs'/ (reference+'.json')


def operate(project,operation,payload):
    from . import workspace
    from .experiments.definitions import digest
    from .execution import project_lock
    reference=payload.get('run_ref')
    if operation=='run_prepare':
        key=payload.get('key') or uuid.uuid4().hex
        reference='run-'+hashlib.sha256((str(project)+'\0'+str(key)).encode()).hexdigest()[:32]
    path=record(project,reference)
    with project_lock(path.parent,reconcile=False,timeout=10):
        if path.exists():value=json.loads(path.read_text())
        elif operation=='run_prepare':
            value={'run_ref':reference,'project':str(project),'policy_digest':digest(workspace.load_project(project)),'studies':[]}
        else:raise RuntimeError('Saved run context not found')
        if value['project']!=str(project):raise RuntimeError('Run belongs to another project')
        if operation=='run_prepare' and value['policy_digest']!=digest(workspace.load_project(project)):
            raise RuntimeError('Run settings changed; use a new run key')
        if operation=='run_add' and payload['study_id'] not in value['studies']:value['studies'].append(payload['study_id'])
        if operation in ('run_prepare','run_add'):workspace.private_write(path,value)
        if operation=='run_submit':
            if value['policy_digest']!=digest(workspace.load_project(project)):raise RuntimeError('Run settings changed; use a new context')
            from .storage_worker import engine
            result=engine(project).submit(payload['recipe'],request_id=payload['request_id'],result_inputs=payload.get('result_inputs'))
            if result['id'] not in value['studies']:value['studies'].append(result['id']);workspace.private_write(path,value)
            return result
        if operation=='run_view':
            from .storage_worker import engine
            e=engine(project);studies=[]
            for sid in value['studies']:
                study=e.inspect(sid)
                studies.append(study if payload.get('details') else {'id':sid,'state':study['state'],
                    'trials':[{'id':t['id'],'state':t['state'],'attempt_ids':[a['id'] for a in t['attempts']]} for t in study['trials']]})
            return {'run_ref':reference,'studies':studies}
    return value


class RunContext:
    def __init__(self,project=None,*,key=None,run_ref=None):
        from .docker_storage import Manager,ContainerEngine
        self.manager=Manager(project);self.project=self.manager.project;self.manager.enable_stream()
        if run_ref is None:
            from .storage_migration import migrate_project
            migrate_project(self.project,manager=self.manager);self.manager.sync()
            saved=self.manager.request('run_prepare',{'key':key},raw=True)
        else:saved=self.manager.request('run_status',{'run_ref':run_ref},raw=True)
        self.run_ref=saved['run_ref']
        self.engine=ContainerEngine(self.project,manager=self.manager,sync=False,auto_export=False,run_ref=self.run_ref)

    def submit(self,recipe,*,request_id,result_inputs=None,sync=True):
        if sync:self.manager.sync()
        return self.manager.request('run_submit',{'run_ref':self.run_ref,'recipe':str(recipe),'request_id':request_id,'result_inputs':result_inputs})

    def execute(self,study_id,*,preflight=True):return self.engine.execute(study_id,baseline_check=preflight)
    def status(self,*,details=False):return self.manager.request('run_view',{'run_ref':self.run_ref,'details':details})
    def collect(self,*,log_cursor=None):
        from .host_delivery import collect_context
        return collect_context(self.manager,self.run_ref,log_cursor=log_cursor)

    def close(self):self.manager.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def main(arguments):
    import argparse
    parser=argparse.ArgumentParser(prog='invariant context')
    parser.add_argument('action',choices=('prepare','status','submit','execute','collect'))
    parser.add_argument('--key');parser.add_argument('--ref');parser.add_argument('--input');parser.add_argument('--request-id');parser.add_argument('--study')
    parser.add_argument('--json',action='store_true');args=parser.parse_args(arguments)
    if args.action!='prepare' and not args.ref:parser.error('--ref is required')
    context=RunContext(key=args.key,run_ref=args.ref)
    if args.action=='prepare':result={'run_ref':context.run_ref}
    elif args.action=='status':result=context.status()
    elif args.action=='collect':result=context.collect()
    elif args.action=='submit':
        if not args.input or not args.request_id:parser.error('--input and --request-id are required')
        result=context.submit(args.input,request_id=args.request_id)
    else:
        if not args.study:parser.error('--study is required')
        result=context.execute(args.study)
    print(json.dumps({'ok':True,'data':result,'error':None},indent=2));return 0
