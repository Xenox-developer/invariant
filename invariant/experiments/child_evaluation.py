"""New immutable evaluation inputs for a child, independent of its saved tools."""
import hashlib
import json

from .. import workspace
from ..task_evidence import test_contract
from .definitions import digest, source_path, text
from .errors import ExperimentError
from .files import write_file
from .snapshots import protected, regular_bytes


def validate_options(value):
    if not isinstance(value,dict) or set(value)-{'test_command','test_paths','test_timeout','test_report'}:
        raise ExperimentError('INVALID_ARGUMENTS','Unknown child evaluation options.')
    if 'test_command' in value:
        text(value['test_command'],'test command',maximum=8192)
        if not value['test_command'].strip():raise ExperimentError('INVALID_ARGUMENTS','Empty test command.')
    if 'test_paths' in value:
        if not isinstance(value['test_paths'],list) or not value['test_paths']:raise ExperimentError('INVALID_ARGUMENTS','Fixed test paths are required.')
        for path in value['test_paths']:source_path(path)
    if 'test_timeout' in value and (type(value['test_timeout']) is not int or not 0<=value['test_timeout']<=3590):
        raise ExperimentError('INVALID_ARGUMENTS','Test timeout must be 0–3590 seconds.')
    if value.get('test_report') is not None:
        source_path(value['test_report'])
        if protected(value['test_report'].casefold()):raise ExperimentError('INVALID_PATH','Report cannot use protected state.')
    return value


def freeze(engine,study,frozen,inputs,value):
    validate_options(value)
    contract=test_contract(engine,study)
    if contract is None:raise ExperimentError('EVALUATION_OVERRIDE_UNSUPPORTED','Use a new custom recipe for a nonguided evaluator.')
    spec={**contract['spec']}
    if 'test_paths' in value:
        from .guided import test_files
        spec['roots'],spec['files']=test_files(engine.project,value['test_paths'])
        for row in spec['files']:
            path=row['path'];raw,_=regular_bytes(engine.project,path)
            target=inputs/path
            if target.exists():target.unlink()
            write_file(target,raw,0o400);frozen['inputs'][path]=hashlib.sha256(raw).hexdigest()
    for option,key in (('test_command','command'),('test_timeout','timeout_seconds'),('test_report','report')):
        if option in value:spec[key]=value[option]
    if spec.get('report') and any(spec['report']==r or spec['report'].startswith(r+'/') for r in spec['roots']):
        raise ExperimentError('INVALID_PATH','Report cannot replace a fixed test.')
    prefix='continuation/evaluation-'+digest(spec)
    verifier=prefix+'.py';settings=prefix+'.json'
    for path,raw in ((verifier,(workspace.ASSETS/'experiment-check.py').read_bytes()),(settings,(json.dumps(spec,indent=2)+'\n').encode())):
        if (inputs/path).exists():(inputs/path).unlink()
        write_file(inputs/path,raw,0o400);frozen['inputs'][path]=hashlib.sha256(raw).hexdigest()
    definition=frozen['definition']
    definition['evaluator']={'argv':['python3','-I','-B',verifier,settings],
        'source_paths':[verifier,settings]+[r['path'] for r in spec['files']],'timeout_seconds':spec['timeout_seconds']+5 if spec['timeout_seconds'] else 0}
    if spec.get('report'):
        definition['workspace']['exclude']=list(dict.fromkeys(definition['workspace']['exclude']+[spec['report']]))
