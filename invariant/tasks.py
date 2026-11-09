"""Durable preparation-to-dispatch protocol; models do not own stage ordering."""
import copy
from pathlib import Path
import subprocess
import time

from . import dependencies, environment, environment_cli, preferences, resources, workspace
from .execution import private_directory, project_lock
from .experiments.definitions import digest, parse_json, text
from .experiments.errors import ExperimentError
from .experiments.snapshots import regular_bytes


def options(value):
    allowed={'name','task','test_command','test_paths','model','effort','packages',
             'prepare_command','allow_network','preparation_timeout','test_timeout','efforts','variants','max_parallel','apply_include','apply_exclude','test_report'}
    if not isinstance(value,dict) or set(value)-allowed:
        raise ExperimentError('INVALID_ARGUMENTS','Unknown task options.')
    result={'name':'task','effort':'medium','packages':[],'prepare_command':None,
            'model':None,'allow_network':False,'preparation_timeout':0,'test_timeout':0,
            'efforts':None,'variants':None,'max_parallel':None,**copy.deepcopy(value)}
    for key in ('task','test_command'):
        text(result.get(key),key,maximum=65536 if key=='task' else 8192)
        if not result[key].strip():raise ExperimentError('INVALID_ARGUMENTS',key+' cannot be blank.')
    text(result['name'],'name',maximum=64,pattern=r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
    text(result['effort'],'effort',maximum=32,pattern=r'[a-z][a-z0-9_-]*')
    if result['model'] is not None:text(result['model'],'model',maximum=128,pattern=r'[A-Za-z0-9][A-Za-z0-9._:-]*')
    if result['efforts'] is not None:
        if not isinstance(result['efforts'],list) or not result['efforts'] or 'effort' in value:
            raise ExperimentError('INVALID_ARGUMENTS','Use --effort or --efforts with nonempty values.')
        for effort in result['efforts']:text(effort,'effort',maximum=32,pattern=r'[a-z][a-z0-9_-]*')
    if result['variants'] is not None:
        if (not isinstance(result['variants'],list) or not result['variants']
                or result['model'] is not None or result['efforts'] is not None or 'effort' in value):
            raise ExperimentError('INVALID_ARGUMENTS','Use variants or model/effort settings, not both.')
        for variant in result['variants']:
            if not isinstance(variant,(list,tuple)) or len(variant)!=2:
                raise ExperimentError('INVALID_ARGUMENTS','Each variant needs model and effort.')
            text(variant[0],'model',maximum=128,pattern=r'[A-Za-z0-9][A-Za-z0-9._:-]*')
            text(variant[1],'effort',maximum=32,pattern=r'[a-z][a-z0-9_-]*')
        result['variants']=[list(variant) for variant in result['variants']]
    if result['max_parallel'] is not None and (type(result['max_parallel']) is not int or not 1<=result['max_parallel']<=1000):
        raise ExperimentError('INVALID_ARGUMENTS','max_parallel must be 1–1000.')
    if not isinstance(result.get('test_paths'),list) or not result['test_paths']:
        raise ExperimentError('INVALID_ARGUMENTS','At least one fixed test path is required.')
    from .experiments.definitions import source_path
    for path in result['test_paths']:source_path(path,'test path')
    if result.get('test_report') is not None:source_path(result['test_report'],'test report')
    if type(result['allow_network']) is not bool or type(result['preparation_timeout']) is not int or not 0<=result['preparation_timeout']<=86400:
        raise ExperimentError('INVALID_ARGUMENTS','Invalid preparation network/timeout.')
    if type(result['test_timeout']) is not int or not 0<=result['test_timeout']<=3590:
        raise ExperimentError('INVALID_ARGUMENTS','Test timeout must be 0–3590 seconds; 0 disables it.')
    from .experiments.definitions import patterns
    for field in ('apply_include','apply_exclude'):
        if field in result:patterns(result[field],field)
    # Validate typed package inputs without executing preparation.
    environment_cli.package_command(result['packages']) if result['packages'] else None
    if not isinstance(result['packages'],list):raise ExperimentError('INVALID_ARGUMENTS','packages must be a list.')
    result['packages']=sorted(set(result['packages']))
    if result['prepare_command'] is not None:
        text(result['prepare_command'],'prepare_command',maximum=8192)
        if not result['prepare_command'].strip():raise ExperimentError('INVALID_ARGUMENTS','Preparation command cannot be blank.')
    return result


class LocalBackend:
    """Adapter to the existing environment and experiment implementations."""
    def __init__(self,project):self.project=project

    def engine(self):
        from .experiments.engine import ExperimentEngine
        if not hasattr(self,'_engine'):self._engine=ExperimentEngine(self.project)
        return self._engine

    def readiness(self):
        value=dependencies.inspect_dependencies()
        if value['ready']:
            try:
                from .docker_storage import managed
                if managed():
                    from .agent_runtime import account_command
                    result=account_command(['login','status'],capture=True)
                else:result=subprocess.run([value['codex']['path'],'login','status'],capture_output=True,text=True,timeout=15)
                signed_in=result.returncode==0
            except (OSError,subprocess.SubprocessError):signed_in=False
            value={**value,'ready':signed_in,'authentication':{'status':'ready' if signed_in else 'required'}}
        return value

    def base(self):
        workspace.init(self.project)
        policy=workspace.load_project(self.project)
        if not environment.enabled(policy) or policy['workspace_access']!='read-write':
            raise ExperimentError('PROFILE_REQUIRED','Task preparation requires a general read-write environment.',
                                  'Review the existing project policy; it was not changed.')
        state=workspace.state_for(self.project)
        if (state/'current.json').exists():
            raise ExperimentError('WORKSPACE_RECOVERY_REQUIRED','Finish or recover the ordinary session before task preparation.')
        try:base=workspace.docker('image','inspect',environment.IMAGE,'--format','{{.Id}}')
        except RuntimeError:
            if (state/'environment.json').exists():
                raise ExperimentError('IMAGE_UNAVAILABLE','Restore the saved environment; no replacement was built.')
            workspace.prepare(persistent=True)
            base=workspace.docker('image','inspect',environment.IMAGE,'--format','{{.Id}}')
        image=environment.selected_image(state,self.project,base)
        selected,_=resources.select(policy,self.project,resources.capacity(workspace.docker),preferences.memory_hint(state,self.project))
        return {'image':image,'resource_contract':resources.execution_contract(selected)}

    def packages(self,value):
        return environment_cli.prepare_packages(value['packages'],project=self.project,
            timeout=value['preparation_timeout'] or None,allow_network=value['allow_network'])

    def command(self,value):
        return environment_cli.prepare(value['prepare_command'],project=self.project,
            timeout=value['preparation_timeout'] or None,network='internet' if value['allow_network'] else None)

    def definition(self,value,key):
        from .experiments.guided import create
        return create(self.engine(),name=value['name'],task=value['task'],test_command=value['test_command'],
                      test_paths=value['test_paths'],model=value['model'],
                      efforts=None if value['variants'] else value['efforts'] or [value['effort']],variants=value['variants'],
                      max_parallel=value['max_parallel'],test_timeout=value['test_timeout'],request_id=key,
                      apply_include=value.get('apply_include'),apply_exclude=value.get('apply_exclude'),test_report=value.get('test_report'))

    def validate(self,path):
        # Capture and publish exactly once. Acceptance is not dispatch; baseline
        # readiness still gates every model turn. Durable ID survives restarts.
        value=self.engine().submit(path,request_id=self.prepared_key(path))
        return {'valid':True,'trial_count':len(value['trials']),'prepared_study_id':value['id'],
                'resource_contract':resources.execution_contract(value['frozen']['effective_policy'])}

    def prepared_key(self,path):return 'task-prepared-'+digest(str(Path(path).resolve()))

    def prepared(self,path):
        engine=self.engine();identifier=engine.store.lookup_request(self.prepared_key(path))
        if identifier:
            value=engine.inspect(identifier)
            _,original,_,_=engine._definition(path)
            if original!=value['frozen']['requested_definition']:
                raise ExperimentError('REQUEST_CONFLICT','Prepared definition changed; the saved snapshot was retained.')
            return value

    def preflight(self,path,key):
        from .experiments.preflight import check,check_frozen
        prepared=self.prepared(path)
        value=check_frozen(self.engine(),prepared['id'],request_id=key) if prepared else check(self.engine(),path,request_id=key)
        return {name:value[name] for name in ('ready','study_id','model_turns','next')}

    def submit(self,path,key):return {'id':(self.prepared(path) or self.engine().submit(path,request_id=key))['id']}
    def dispatch(self,study,key):return {'id':self.engine().launch(study,request_id=key)['id']}
    def child_preflight(self,study,key):
        from .experiments.preflight import check_frozen
        return check_frozen(self.engine(),study,request_id=key)

    def inspect(self,study):
        engine=self.engine();result=engine.results(study)
        from .task_evidence import execution_receipt
        item=engine.inspect(study)
        result['receipts']=[execution_receipt(engine,item,a) for t in item['trials'] for a in t['attempts']]
        result['calls']=[{'call_id':a['id'],'usage':a.get('record',{}).get('runner',{}).get('token_usage')}
                         for t in engine.inspect(study)['trials'] for a in t['attempts']]
        return result

    def continuation(self,parent,task,key,evaluation=None):
        from .experiments.guided import inline_prompt
        engine=self.engine()
        return {'id':engine.continue_result(parent,inline_prompt(engine,task),request_id=key,evaluation=evaluation)['id']}


class TaskRunner:
    def __new__(cls,project=None,*,state=None,backend=None):
        from .docker_storage import managed,ContainerTaskRunner
        if state is None and backend is None and not managed():return ContainerTaskRunner(project)
        return super().__new__(cls)

    def __init__(self,project=None,*,state=None,backend=None):
        self.project=Path(project or Path.cwd()).resolve()
        self.state=Path(state) if state is not None else workspace.state_for(self.project)
        self.root=self.state/'tasks'
        self.backend=backend or LocalBackend(self.project)

    def path(self,key):
        text(key,'request ID',maximum=128,pattern=r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
        return self.root/(digest(key)+'.json')

    def read(self,key):
        path=self.path(key)
        try:raw,_=regular_bytes(self.root,path.name,maximum=1024*1024)
        except FileNotFoundError:return None
        value=parse_json(raw,'task state')
        if value.get('schema_version')!=1 or value.get('project')!=str(self.project) or value.get('request_id')!=key:
            raise ExperimentError('CORRUPT_STATE','Task record identity/schema mismatch.')
        return value

    def save(self,value):
        value['updated_at']=time.time()
        workspace.private_write(self.path(value['request_id']),value)

    def status(self,key):
        value=self.read(key)
        if value is None:raise ExperimentError('NOT_FOUND','No task has this request ID.')
        if value['state']=='running':
            try:lock=project_lock(self.root,reconcile=False)
            except RuntimeError:value['resume_required']=False
            else:
                lock.close()
                value['resume_required']=True
        if value.get('study_id'):value['execution']=self.backend.inspect(value['study_id'])
        return value

    def run(self,key,value=None,*,retry_preparation=False):
        self.path(key)
        supplied=options({'name':'task-'+digest(key)[:16],**value}) if isinstance(value,dict) else options(value) if value is not None else None
        private_directory(self.root)
        try:lock=project_lock(self.root,reconcile=False)
        except RuntimeError:
            existing=self.read(key)
            if existing:
                if supplied is not None and supplied!=existing['options']:
                    raise ExperimentError('REQUEST_CONFLICT','Task options differ from the accepted request.')
                return existing
            raise ExperimentError('TASK_BUSY','Another task is preparing this project.',
                                  'Observe its task status; do not create parallel setup mutations.') from None
        try:
            record=self.read(key)
            if record is None:
                if supplied is None:raise ExperimentError('NOT_FOUND','Supply task options for the first invocation.')
                record={'schema_version':1,'project':str(self.project),'request_id':key,'options':supplied,
                        'state':'running','active_stage':None,'completed':{},'study_id':None,'created_at':time.time()}
                self.save(record)
            elif supplied is not None and supplied!=record['options']:
                raise ExperimentError('REQUEST_CONFLICT','This task key was already used with different options.')
            if record['state']=='submitted':return record
            value=record['options']
            active=record.get('active_stage')
            if active in ('packages','command'):
                if not retry_preparation:
                    if record['state']!='blocked':
                        record.update(state='blocked',error={'code':'PREPARATION_UNCERTAIN',
                            'message':'Setup may have run before its acknowledgement was saved.',
                            'recovery':'Inspect its log and environment; recover if needed, then explicitly use --retry-preparation.'})
                        self.save(record)
                    return record
                if (self.state/'current.json').exists():
                    raise ExperimentError('WORKSPACE_RECOVERY_REQUIRED','Recover interrupted preparation before explicitly repeating it.')
            stages=['readiness','base']
            if 'parent' in value:
                stages=['continuation']+(['child_preflight'] if value.get('evaluation') is not None else [])+['dispatch']
            elif value['packages']:stages.append('packages')
            if 'parent' not in value:
                if value['prepare_command']:stages.append('command')
                stages+=['definition','validate','preflight','submit','dispatch']
            for stage in stages:
                if stage in record['completed']:continue
                stage_started=time.time()
                record.update(state='running',active_stage=stage)
                record.pop('error',None)
                self.save(record)  # Write intent before every possible side effect.
                subkey='task-'+digest({'request':record['request_id'],'project':str(self.project),'stage':stage})
                definition=record['completed'].get('definition',{}).get('definition')
                try:
                    if stage=='continuation':result=self.backend.continuation(value['parent'],value['task'],subkey,**({'evaluation':value['evaluation']} if value.get('evaluation') is not None else {}))
                    elif stage=='child_preflight':result=self.backend.child_preflight(record['study_id'],subkey)
                    elif stage=='readiness':result=self.backend.readiness()
                    elif stage=='base':result=self.backend.base()
                    elif stage in ('packages','command'):result=getattr(self.backend,stage)(value)
                    elif stage=='definition':result=self.backend.definition(value,subkey)
                    elif stage=='validate':result=self.backend.validate(definition)
                    elif stage=='preflight':result=self.backend.preflight(definition,subkey)
                    elif stage=='submit':result=self.backend.submit(definition,subkey)
                    else:result=self.backend.dispatch(record['study_id'],subkey)
                    if stage=='readiness' and not result['ready']:
                        raise ExperimentError('HOST_NOT_READY','Host prerequisites are unavailable.',
                            'Use invariant setup --check --json, restore Docker access or run invariant setup in a terminal.',details=result)
                    if stage in ('packages','command') and not result['prepared']:
                        raise ExperimentError('PREPARATION_FAILED','Environment preparation did not succeed.',
                            'Inspect the saved log; downloads already used bounded acquisition retries. Use --retry-preparation only after reviewing partial effects.',details=result)
                    if stage in ('preflight','child_preflight') and not result['ready']:
                        raise ExperimentError('PREFLIGHT_FAILED','Baseline checks could not run reliably.',
                                              'Inspect the saved preflight study; no worker was submitted.',details=result)
                    if stage=='validate':
                        original=record['completed']['base'].get('resource_contract')
                        if original and original!=result.get('resource_contract'):
                            raise ExperimentError('RESOURCE_CHANGED','Effective resources changed during preparation.',
                                                  'Review resources and create a new task request.')
                    record['completed'][stage]=result
                    record.setdefault('stage_timings',{})[stage]={'started_at':stage_started,'finished_at':time.time(),
                                                                'duration_seconds':time.time()-stage_started}
                    if stage in ('submit','continuation'):record['study_id']=result['id']
                    record['active_stage']=None
                    self.save(record)
                except (OSError,RuntimeError,ValueError) as error:
                    details=error.record() if isinstance(error,ExperimentError) else {
                        'code':'TASK_STAGE_FAILED','message':str(error),'recovery':'Inspect task status; repeat the same request key after resolving the cause.'}
                    record.update(state='blocked',error=details)
                    self.save(record)
                    return record
            record.update(state='submitted',active_stage=None)
            self.save(record)
            return record
        finally:lock.close()


    def continue_task(self,key,parent,task,*,evaluation=None):
        self.path(key)
        text(parent,'parent',maximum=128,pattern=r'attempt-[a-f0-9]+')
        text(task,'task',maximum=65536)
        if not task.strip():raise ExperimentError('INVALID_ARGUMENTS','Continuation task cannot be blank.')
        if evaluation is not None:
            from .experiments.child_evaluation import validate_options
            validate_options(evaluation)
        value={'parent':parent,'task':task,**({'evaluation':evaluation} if evaluation is not None else {})}
        private_directory(self.root)
        with project_lock(self.root,reconcile=False):
            existing=self.read(key)
            if existing and existing['options']!=value:
                raise ExperimentError('REQUEST_CONFLICT','Continuation key has different inputs.')
            if existing is None:
                self.save({'schema_version':1,'project':str(self.project),'request_id':key,'options':value,
                           'state':'running','active_stage':None,'completed':{},'study_id':None,'created_at':time.time()})
        return self.run(key)
