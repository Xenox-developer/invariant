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
        try:study=engine.submit(relative,request_id=key)
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
