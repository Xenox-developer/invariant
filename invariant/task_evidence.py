"""Bounded, verifiable facts for decisions; full private evidence stays on disk."""
import hashlib
import json

from . import resources
from .experiments.definitions import digest
from .experiments.errors import ExperimentError
from .experiments.snapshots import regular_bytes


def test_contract(engine,study):
    frozen=study['frozen'];evaluator=frozen['definition'].get('evaluator',{})
    argv=evaluator.get('argv',[])
    if len(argv)!=5 or argv[:3]!=['python3','-I','-B']:return None
    path=argv[-1]
    if path not in frozen['inputs']:return None
    raw,_=regular_bytes(engine.root/'baselines'/study['baseline_id']/'inputs',path)
    if hashlib.sha256(raw).hexdigest()!=frozen['inputs'][path]:
        raise ExperimentError('CORRUPT_STATE','Frozen evaluation specification changed.')
    spec=json.loads(raw)
    if not isinstance(spec,dict) or not {'command','roots','files','timeout_seconds'}<=set(spec):return None
    return {'version':digest({'spec':spec,'evaluator':evaluator,'inputs':{p:frozen['inputs'].get(p) for p in evaluator['source_paths']}}),
            'command':spec['command'],'timeout_seconds':spec['timeout_seconds'],'report':spec.get('report'),
            'test_files':[{k:row[k] for k in ('path','mode') if k in row} for row in spec['files']],
            'spec':spec}


def protected_tests(engine,study):
    contract=test_contract(engine,study)
    if contract is None:return {}
    frozen=study['frozen'];root=engine.root/'baselines'/study['baseline_id']/'inputs'
    result={}
    for row in contract['test_files']:
        path=row['path'];raw,_=regular_bytes(root,path)
        expected=frozen['inputs'][path]
        if hashlib.sha256(raw).hexdigest()!=expected:raise ExperimentError('CORRUPT_STATE','Fixed test changed: '+path)
        result[path]={'type':'file','mode':row['mode'],'size':len(raw),'sha256':expected}
    return result


def execution_receipt(engine,study,attempt):
    record=attempt.get('record',{});evaluation=record.get('evaluation',{});metrics=evaluation.get('metrics',{})
    contract=test_contract(engine,study)
    runtime=record.get('runner',{}).get('runtime',{})
    frozen=study['frozen']
    value={'schema_version':1,'study_id':study['id'],'attempt_id':attempt['id'],
           'definition_digest':frozen['definition_digest'],'baseline_digest':frozen['baseline_content_digest'],
           'result_digest':record.get('capture',{}).get('content_digest'),
           'state':attempt['state'],'passed':evaluation.get('passed'),
           'evaluation':{'status':evaluation.get('status'),'reason':evaluation.get('reason'),
                         'reused_validation':evaluation.get('reused_validation',False),
                         'validation_receipt':evaluation.get('validation_receipt'),
                         'contract_version':contract['version'] if contract else digest(frozen['definition'].get('evaluator')),
                         'command':contract['command'] if contract else None,
                         'test_exit_code':metrics.get('test_exit_code'),
                         'checks':{'tests':metrics.get('tests_total'),'failures':metrics.get('tests_failures'),'errors':metrics.get('tests_errors')},
                         'fixed_test_count':len(contract['test_files']) if contract else None,
                         'evidence_path':str(engine.root/'attempts'/attempt['id']/'evaluation')},
           'resources':{'expected_digest':digest(resources.execution_contract(frozen['effective_policy'])),
                        'observed_digest':digest(runtime.get('resource_contract')) if runtime.get('resource_contract') else None},
           'inference_usage':record.get('runner',{}).get('inference_usage')}
    runner=record.get('runner',{})
    if runner.get('native') and runner.get('command_evidence'):
        from .command_evidence import verified_summary
        value['execution_commands']=verified_summary(runner['command_evidence'])
    value['receipt_digest']=digest(value)
    return value
