"""Run three-agent research with INVARIANT, then with stock Docker/Codex CLI.

This is application-level orchestration, not an INVARIANT extension. Only this
example's files are written. The core is fingerprinted before/after execution.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
sys.path.insert(0,str(REPO))
from specification import MODEL,EFFORT,COMMON,METHOD,CHECKER,prepare
from plain_backend import Plain,docker


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def progress(stage,**facts):print(json.dumps({'stage':stage,**facts}),flush=True)


def core_check():
    baseline=json.loads((HERE/'results/core-before.json').read_text())['files']
    current={}
    for folder in ('invariant','crates','docker','sdk','skills','scripts'):
        for p in sorted((REPO/folder).rglob('*')):
            if p.is_file() and not any(x in ('__pycache__','target','.DS_Store') for x in p.relative_to(REPO).parts):
                current[p.relative_to(REPO).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    for name in ('pyproject.toml','_build.py','Cargo.toml','Cargo.lock'):
        path=REPO/name
        if path.is_file():current[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    changed=[p for p in sorted(set(baseline)|set(current)) if baseline.get(p)!=current.get(p)]
    result={'unchanged':not changed,'files_checked':len(baseline),'changed':changed}
    write(HERE/'results/core-after.json',result)
    assert not changed,'INVARIANT core changed: '+repr(changed)
    return result


def init(project,output):
    env={**os.environ,'PYTHONPATH':str(REPO),'PYTHONDONTWRITEBYTECODE':'1'}
    result=subprocess.run([sys.executable,'-B','-m','invariant.cli','init'],cwd=project,env=env,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    (output/'init.log').write_text(result.stdout)
    assert result.returncode==0,result.stdout


def run_study(project,output,key):
    from invariant.experiments.engine import ExperimentEngine
    from invariant.experiments.preflight import check_frozen
    output.mkdir(parents=True,exist_ok=True)
    init(project,output)
    engine=ExperimentEngine(project)
    study=engine.submit('experiment.json',request_id=key)
    progress('invariant_captured',study=study['id'],project=project.name)
    baseline=check_frozen(engine,study['id'],request_id=key+'-baseline')
    write(output/'baseline.json',baseline)
    assert baseline['ready'],'Frozen baseline unavailable'
    engine.execute(study['id'])
    results=engine.results(study['id'])
    write(output/'execution.json',results)
    assert all(row['passed'] is True for row in results['trials']),[(r['variant_id'],r['state'],r['reason'],r['evaluation']) for r in results['trials']]
    records=[]
    for row in results['trials']:
        role=row['variant_id'];target=output.parent/'agents'/role;target.mkdir(parents=True,exist_ok=True)
        exported=project/'.invariant/results'/row['attempt_id']/'files'
        for name in ('solution.py','result.json','build_report.py','comparison.csv','report.md','errors.svg','verification.json'):
            path=exported/name
            if path.exists():shutil.copyfile(path,target/name)
        runner=row['runner']
        records.append({'role':role,'attempt_id':row['attempt_id'],'study_id':study['id'],
            'model':runner['native']['model'],'effort':runner['native']['effort'],
            'started_at':runner['started_at'],'finished_at':runner['finished_at'],'duration_seconds':runner['duration_seconds'],
            'provider_usage':runner.get('token_usage'),'inference_usage':runner.get('inference_usage'),
            'passed':row['passed'],'tests':row['evaluation']['metrics'],
            'reused_validation':row['evaluation'].get('reused_validation',False),
            'image':runner['image'],'runtime':runner.get('runtime'),
            'workspace_ref':row.get('capture',{}).get('workspace_ref'),'result_directory':str(target)})
    return study,records


def run_invariant(namespace):
    started=time.time();output=HERE/'results'/namespace/'with-invariant';output.mkdir(parents=True,exist_ok=True)
    project=HERE/'projects'/namespace/'with-invariant-methods'
    prepare(project,HERE)
    stage_started=time.time()
    study,records=run_study(project,output/'methods',namespace+'-methods')
    stage1_finished=time.time()
    checker=HERE/'projects'/namespace/'with-invariant-checker'
    prepare(checker,HERE,checker_inputs={r:output/'agents'/r for r in ('trapezoid','simpson')})
    _,third=run_study(checker,output/'checker',namespace+'-checker')
    finished=time.time();records+=third
    assert len(records)==3
    result={'backend':'invariant','started_at':started,'finished_at':finished,'wall_seconds':finished-started,
        'parallel_stage_seconds':stage1_finished-stage_started,'checker_stage_seconds':finished-stage1_finished,
        'setup_seconds':stage_started-started,'agents':records,'passed':True,'starting_image':study['frozen']['image']['id'],
        'resources':study['frozen']['effective_policy'],'native_image':study['frozen']['image'],
        'state_locations':'Docker named volumes managed by INVARIANT'}
    write(output/'summary.json',result)
    progress('invariant_completed',wall_seconds=result['wall_seconds'],agents=3)
    return result


def run_plain(namespace,image):
    from invariant.docker_storage import IMAGE,STATE_VOLUME
    # Only shared bootstrap identity/constants are read above. The baseline
    # backend itself imports no INVARIANT code and uses no INVARIANT commands.
    started=time.time();output=HERE/'results'/namespace/'without-invariant';output.mkdir(parents=True,exist_ok=True)
    seed=HERE/'projects'/namespace/'plain-seed';prepare(seed,HERE)
    backend=Plain('research-'+namespace,image=image,runtime_image=IMAGE,auth_volume=STATE_VOLUME,output=output)
    backend.prepare(seed)
    stage_started=time.time()
    try:
        records=backend.parallel({r:COMMON+'\n'+METHOD.format(method=r) for r in ('trapezoid','simpson')},MODEL,EFFORT)
        stage1_finished=time.time()
        checker=HERE/'projects'/namespace/'plain-checker-seed'
        prepare(checker,HERE,checker_inputs={r:seed for r in ('trapezoid','simpson')})
        backend.handoff(checker)
        third=backend.run_agent('checker',COMMON+'\n'+CHECKER,MODEL,EFFORT)
        evidence=backend.collect()
        assert all(evidence[r]['passed'] for r in ('trapezoid','simpson','checker')),evidence
        finished=time.time()
        result={'backend':'plain_docker_codex','started_at':started,'finished_at':finished,'wall_seconds':finished-started,
            'parallel_stage_seconds':stage1_finished-stage_started,'checker_stage_seconds':finished-stage1_finished,
            'setup_seconds':stage_started-started,'agents':backend.records,'passed':True,'starting_image':image,
            'state_volume':backend.state,'tools_volume':backend.tools,'state_locations':'Docker named volumes retained by the baseline harness'}
        write(output/'summary.json',result)
        progress('plain_completed',wall_seconds=result['wall_seconds'],agents=3)
        return result
    finally:backend.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--namespace',required=True)
    parser.add_argument('--arm',choices=('both','invariant','plain'),default='both');parser.add_argument('--image')
    args=parser.parse_args()
    import re
    assert re.fullmatch('[a-z0-9-]{1,50}',args.namespace)
    core_check()
    run=HERE/'results'/args.namespace;run.mkdir(parents=True,exist_ok=True)
    if args.arm in ('both','invariant'):
        assert not (run/'with-invariant/summary.json').exists(),'Completed arm already exists; do not replay model calls'
        invariant=run_invariant(args.namespace)
    else:invariant=json.loads((run/'with-invariant/summary.json').read_text())
    if args.arm in ('both','plain'):
        assert not (run/'without-invariant/summary.json').exists(),'Completed arm already exists; do not replay model calls'
        plain=run_plain(args.namespace,args.image or invariant['starting_image'])
    proof=core_check()
    write(run/'core-proof.json',proof)


if __name__=='__main__':main()
