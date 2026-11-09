"""Repeat the research with reusable contexts, aligned tools, cold/warm order reversal."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent;REPO=HERE.parents[2];sys.path.insert(0,str(REPO))
from specification import prepare,COMMON,METHOD,CHECKER,MODEL,EFFORT
from baseline_v3 import Plain,docker
from invariant.run_context import RunContext
from invariant.docker_storage import IMAGE,STATE_VOLUME
from invariant.workspace import DISABLED_FEATURES
from invariant.experiments.preflight import check_frozen
from invariant.experiments.task_context import render

FEATURES={**{'features.'+name:False for name in DISABLED_FEATURES},
    'features.code_mode_host':True,'features.skip_host_skill_discovery':True,
    'features.skill_search':False,'features.view_image':False,'features.code_mode.default_exec_yield_time_ms':1200000}
CAPABILITIES={'python3':True,'git':True,'rg':False,'find':True,'sed':True,'node':False,'npm':False,'pytest':False}
BASE=HERE/'results/v3'


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def note(stage,**facts):print(json.dumps({'stage':stage,**facts}),flush=True)


def seed(project,inputs=None):
    definition=prepare(project,HERE,checker_inputs=inputs)
    common=COMMON.split('If invariant_validate is available')[0]
    (project/'TASK.md').write_text(common)
    return definition,common


def record_native(rows,output):
    records=[]
    for row in rows['trials']:
        runner=row['runner'];role=row['variant_id'];target=output/'agents'/role;target.mkdir(parents=True,exist_ok=True)
        for export in rows['exported_results']:
            if Path(export).name.startswith(row['attempt_id']):
                for name in ('solution.py','result.json','build_report.py','comparison.csv','report.md','errors.svg','verification.json'):
                    path=Path(export)/'files'/name
                    if path.exists():shutil.copyfile(path,target/name)
        assert row['passed'] is True,(role,row['state'],row['reason'])
        records.append({'role':role,'attempt_id':row['attempt_id'],'study_id':rows['study_id'],
            'model':runner['native']['model'],'effort':runner['native']['effort'],
            'started_at':runner['started_at'],'finished_at':runner['finished_at'],'duration_seconds':runner['duration_seconds'],
            'provider_usage':runner['token_usage'],'inference_usage':runner['inference_usage'],
            'tests':row['evaluation']['metrics'],'passed':row['passed'],'runtime':runner['runtime'],
            'reused_validation':row['evaluation'].get('reused_validation',False),'image':runner['image']})
    return records


def native(label):
    started=time.time();output=BASE/label/'with-invariant';project=HERE/'projects/v3-context'
    assert not (output/'summary.json').exists(),'Do not replay a completed measured arm'
    seed(project)
    context=RunContext(project,key='v3-'+label)
    write(output/'context.json',{'run_ref':context.run_ref,'project':str(project)})
    stage=time.time();records=[];images=[]
    for phase in ('methods','checker'):
        if phase=='checker':seed(project,{r:output/'agents'/r for r in ('trapezoid','simpson')})
        frozen=context.submit('experiment.json',request_id='v3-'+label+'-'+phase)
        images.append(frozen['frozen']['image']['id'])
        preflight=check_frozen(context.engine,frozen['id'],request_id='v3-'+label+'-'+phase+'-baseline')
        write(output/phase/'baseline.json',preflight);assert preflight['ready']
        note('native_dispatched',round=label,phase=phase,study=frozen['id'])
        context.execute(frozen['id'])
        rows=context.engine.results(frozen['id']);collected=context.collect()
        rows.update(collected);write(output/phase/'execution.json',rows)
        records+=record_native(rows,output)
        if phase=='methods':stage1=time.time()
    finished=time.time()
    summary={'backend':'invariant','started_at':started,'finished_at':finished,'wall_seconds':finished-started,
        'setup_seconds':stage-started,'parallel_stage_seconds':stage1-stage,'checker_stage_seconds':finished-stage1,
        'agents':records,'passed':True,'starting_image':images[0],'run_ref':context.run_ref,
        'profile':{'tool_profile':'coding','features':FEATURES},'project':str(project)}
    write(output/'summary.json',summary);note('native_finished',round=label,seconds=summary['wall_seconds'])
    return summary


def bare(label,image):
    started=time.time();output=BASE/label/'without-invariant';seed_project=HERE/'projects'/('v3-bare-'+label)
    assert not (output/'summary.json').exists(),'Do not replay a completed measured arm'
    _,common=seed(seed_project)
    backend=Plain('research-v3-'+label,image=image,runtime_image=IMAGE,auth_volume=STATE_VOLUME,output=output,
        tools_volume='research-v3-common-tools',features=FEATURES)
    backend.prepare(seed_project);stage=time.time()
    def prompt(role):
        text=METHOD.format(method=role) if role!='checker' else CHECKER
        files=['TASK.md','fixed_checks.py','solution.py','result.json'] if role!='checker' else ['TASK.md','fixed_checks.py',*[f'inputs/{r}/{n}' for r in ('trapezoid','simpson') for n in ('solution.py','result.json')]]
        return common+'\n'+text+render({f:{'type':'file'} for f in files},CAPABILITIES)+\
            '\nThis runner has no invariant_validate tool. Do not search for it. Run the local acceptance command shown above; rg is unavailable.\n'
    try:
        backend.parallel({r:prompt(r) for r in ('trapezoid','simpson')},MODEL,EFFORT);stage1=time.time()
        checker=HERE/'projects'/('v3-bare-checker-'+label)
        seed(checker,{r:seed_project for r in ('trapezoid','simpson')});backend.handoff(checker)
        backend.run_agent('checker',prompt('checker'),MODEL,EFFORT)
        evidence=backend.collect();assert all(v['passed'] for v in evidence.values()),evidence
        finished=time.time()
        result={'backend':'plain_docker_codex','started_at':started,'finished_at':finished,'wall_seconds':finished-started,
            'setup_seconds':stage-started,'parallel_stage_seconds':stage1-stage,'checker_stage_seconds':finished-stage1,
            'agents':backend.records,'passed':True,'starting_image':image,'state_volume':backend.state,
            'tools_volume':backend.tools,'profile':{'tool_profile':'coding','features':FEATURES}}
        write(output/'summary.json',result);note('bare_finished',round=label,seconds=result['wall_seconds']);return result
    finally:backend.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('label',choices=('cold','warm'));parser.add_argument('--arm',choices=('native','bare','both'),default='both');args=parser.parse_args()
    baseline=json.loads((HERE/'results/pair-20261107-a/comparison.json').read_text())
    order=['native','bare'] if args.label=='cold' else ['bare','native']
    write(BASE/'protocol.json',{'model':MODEL,'effort':EFFORT,'features':FEATURES,'checks_sha256':__import__('hashlib').sha256((HERE/'fixed_checks.py').read_bytes()).hexdigest(),
        'order':{'cold':['native','bare'],'warm':['bare','native']},'image':baseline['starting_image']})
    for arm in order:
        if args.arm not in ('both',arm):continue
        if arm=='native':native(args.label)
        else:bare(args.label,baseline['starting_image'])


if __name__=='__main__':main()
