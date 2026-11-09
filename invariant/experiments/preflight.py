"""Evaluate an untouched baseline in Docker before spending native model turns."""
import copy
import json
from pathlib import Path
import uuid

from .definitions import digest
from .errors import ExperimentError
from .starters import publish


def check(engine, path, *, request_id=None):
    key='preflight-'+digest({'path':str(path),'request':request_id or str(uuid.uuid4())})
    existing=engine.store.lookup_request(key)
    if existing:
        study=engine.inspect(existing)
    else:
        preview=engine.validate(path)  # Includes account/catalog checks, without a model call.
        _,_,definition,_=engine._definition(path)
        if not definition.get('evaluator'):
            raise ExperimentError('EVALUATOR_REQUIRED','Preflight requires a captured evaluator.',
                                  'Add an evaluator or use guided setup with a shared test command.')
        definition=copy.deepcopy(definition)
        definition['name']='preflight-'+definition['name'][:48]
        definition['runner']={'type':'command','argv':['/bin/true']}
        definition['variants']=[{'id':'baseline','parameters':{}}]
        for case in definition['cases']:case.pop('prompt_file',None)
        definition['repetitions']=1;definition['execution']['max_parallel']=1
        temporary=engine.project/'experiments'/('.'+key+'.json')
        relative=temporary.relative_to(engine.project).as_posix()
        definition['workspace']['exclude'].append(relative)
        raw=json.dumps(definition,indent=2)+'\n'
        publish(engine,{temporary:raw},{'path':relative},request_id=key+'-file',operation='preflight_file')
        try:study=engine.submit(relative,request_id=key,_evaluation_only=True)
        finally:
            if temporary.is_file() and not temporary.is_symlink() and temporary.read_text()==raw:temporary.unlink()
    if study['state']!='completed':engine.execute(study['id'])
    result=engine.results(study['id'])
    guided=any('test_command_seconds' in row['metrics'] for row in result['trials'])
    ready=all(row['runner']['status']=='completed' and row['evaluation']['status']=='completed'
              and (not guided or row['passed'] is not None) for row in result['trials'])
    return {'ready':ready,'study_id':study['id'],'model_turns':0,'baseline':result['summary'],
            'baseline_tests_may_fail':True,'results':result,
            'next':'Ready for model trials; baseline test failures can be the task to fix.' if ready else
                   'Inspect preflight results/logs; fix missing tools, test setup, deadlines or evaluator failures before model trials.'}


def check_frozen(engine,identifier,*,request_id):
    """Evaluate a child's exact saved baseline without recapturing the host."""
    from ..docker_storage import ContainerEngine
    if isinstance(engine,ContainerEngine):return engine.manager.request('preflight',{'args':[identifier],'kwargs':{'request_id':request_id},'run_ref':getattr(engine,'run_ref',None)})
    study=prepare_frozen(engine,identifier)
    if study['state']!='completed':engine.execute(study['id'])
    result=engine.results(study['id'])
    ready=all(r['runner']['status']=='completed' and r['evaluation']['status']=='completed' and r['passed'] is not None for r in result['trials'])
    return {'ready':ready,'study_id':study['id'],'model_turns':0,'next':'Ready for child dispatch.' if ready else 'Inspect frozen baseline evaluation; no worker was dispatched.'}


def prepare_frozen(engine,identifier):
    from .definitions import expand
    from .. import workspace
    from ..execution import private_directory
    source=engine.inspect(identifier)
    # Cache only an exact frozen baseline, including image/DB state, source,
    # evaluator inputs and resource policy. A new observation ID is not new work.
    key='frozen-preflight-'+digest({'protocol':'direct-baseline-v1','frozen':source['frozen']})
    existing=engine.store.lookup_request(key)
    if existing:study=engine.inspect(existing)
    else:
        frozen=copy.deepcopy(source['frozen']);definition=frozen['definition']
        definition['runner']={'type':'command','argv':['/bin/true']}
        definition['variants']=[{'id':'baseline','parameters':{}}]
        for case in definition['cases']:case.pop('prompt_file',None)
        definition['repetitions']=1;definition['execution']['max_parallel']=1
        frozen['execution'].update(max_parallel=1,requested_max_parallel=1)
        frozen.update(definition_digest=digest(definition),requested_definition=copy.deepcopy(definition))
        frozen['evaluation_only']=True
        sid='study-'+uuid.uuid4().hex
        folder=private_directory(engine.root/'studies')/sid;folder.mkdir(mode=0o700)
        workspace.private_write(folder/'definition.json',frozen)
        from .engine import sync_tree
        from .snapshots import seal_tree
        sync_tree(folder);seal_tree(folder)
        accepted=engine.store.create(study_id=sid,baseline={'id':source['baseline_id'],
            'content_digest':frozen['baseline_content_digest'],'path':str(engine.root/'baselines'/source['baseline_id'])},
            frozen=frozen,trials=expand(definition),request_id=key,
            fingerprint=digest({'operation':'frozen_preflight','study':identifier}),project=engine.project)
        study=engine.inspect(accepted)
    return study
