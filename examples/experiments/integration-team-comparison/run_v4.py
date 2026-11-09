"""Container-internal handoff and validated completion; reversed paired benchmarks."""
import argparse,hashlib,json,shutil,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[2]))
from specification import prepare,COMMON,METHOD,CHECKER,MODEL,EFFORT
from run_v3 import FEATURES,CAPABILITIES,record_native
from baseline_v3 import Plain,docker
from invariant.run_context import RunContext
from invariant.docker_storage import IMAGE,STATE_VOLUME,source_digest
from invariant.experiments.task_context import render
BASE=HERE/'results/v4'
BATCH='Batch related reads and short sequential tool calls in one awaited exec. Run the full fixed acceptance suite once; do not repeat checks that it already covers. '
COMPUTE=json.loads((HERE/'results/v3/protocol.json').read_text())['image']
def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
def note(stage,**facts):print(json.dumps({'stage':stage,**facts}),flush=True)
def seed_native(project):
    project.mkdir(parents=True,exist_ok=True)
    methods=prepare(project,HERE);methods['runner']['completion']='validated'
    (project/'methods.json').write_text(json.dumps(methods,indent=2)+'\n')
    checker=prepare(project,HERE,checker_inputs={});checker['name']='integration-team-check';checker['runner']['completion']='validated'
    (project/'checker.json').write_text(json.dumps(checker,indent=2)+'\n')
    (project/'experiment.json').unlink();(project/'TASK.md').write_text(COMMON.split('If invariant_validate is available')[0])

def native(label,*,smoke=False):
    started=time.time();output=BASE/label/'with-invariant';project=HERE/'projects'/('v4-smoke' if smoke else 'v4-context')
    assert not (output/'summary.json').exists(),'Do not replay a completed measured arm'
    seed_native(project)
    if smoke:
        path=project/'methods.json';recipe=json.loads(path.read_text());recipe['variants']=recipe['variants'][:1];recipe['execution']['max_parallel']=1;path.write_text(json.dumps(recipe,indent=2)+'\n')
    context=RunContext(project,key='v4-'+label);write(output/'context.json',{'run_ref':context.run_ref,'project':str(project)})
    setup=time.time()-started;stages={};rows_by_phase={};previous=time.time();refs=[]
    for phase in (('methods',) if smoke else ('methods','checker')):
        frozen=context.submit(phase+'.json',request_id='v4-'+label+'-'+phase,result_inputs=refs,sync=False)
        note('native_dispatched',round=label,phase=phase,study=frozen['id'])
        executed=context.execute(frozen['id']);write(output/phase/'dispatch.json',executed)
        rows=context.engine.results(frozen['id']);assert all(r['passed'] is True for r in rows['trials']),[(r['state'],r.get('reason'),r.get('runner',{}).get('reason')) for r in rows['trials']]
        rows_by_phase[phase]=rows
        if phase=='methods':
            refs=[{'attempt_id':r['attempt_id'],'result_digest':r['capture']['content_digest'],
                   'files':{n:'inputs/'+r['variant_id']+'/'+n for n in ('solution.py','result.json')}} for r in rows['trials']]
        stages[phase]=time.time()-previous;previous=time.time()
    cursor=None
    if not smoke:
        labels=('first','warm1','warm2','warm3','release')
        for prior in labels[:labels.index(label)]:
            path=BASE/prior/'with-invariant/checker/execution.json'
            if path.exists():cursor=json.loads(path.read_text()).get('log_cursor')
    collected=context.collect(log_cursor=cursor);records=[]
    for phase,rows in rows_by_phase.items():
        rows.update(collected);write(output/phase/'execution.json',rows);records+=record_native(rows,output)
    finished=time.time()
    summary={'backend':'invariant','started_at':started,'finished_at':finished,'wall_seconds':finished-started,
      'setup_seconds':setup,'parallel_stage_seconds':stages['methods'],'checker_stage_seconds':stages.get('checker',0),
      'final_collect_seconds':finished-previous,'agents':records,'passed':True,'starting_image':COMPUTE,
      'run_ref':context.run_ref,'project':str(project),'profile':{'tool_profile':'coding','features':FEATURES},
      'container_internal_handoff':True,'source_digest':source_digest(),'smoke':smoke}
    write(output/'summary.json',summary);context.close();note('native_finished',round=label,seconds=summary['wall_seconds']);return summary

def bare(label):
    started=time.time();output=BASE/label/'without-invariant';project=HERE/'projects'/('v4-bare-'+label)
    assert not (output/'summary.json').exists(),'Do not replay a completed measured arm'
    prepare(project,HERE);common=COMMON.split('If invariant_validate is available')[0];(project/'TASK.md').write_text(common)
    backend=Plain('research-v4-'+label,image=COMPUTE,runtime_image=IMAGE,auth_volume=STATE_VOLUME,output=output,
                  tools_volume='research-v4-common-tools',features=FEATURES)
    backend.prepare(project);stage=time.time()
    def prompt(role):
        text=METHOD.format(method=role) if role!='checker' else CHECKER
        files=['TASK.md','fixed_checks.py','solution.py','result.json'] if role!='checker' else ['TASK.md','fixed_checks.py',*[f'inputs/{r}/{n}' for r in ('trapezoid','simpson') for n in ('solution.py','result.json')]]
        return common+'\n'+text+render({f:{'type':'file'} for f in files},CAPABILITIES)+'\n'+BATCH+'This runner has no invariant_validate tool. Do not search for it. Run the local acceptance command shown above; rg is unavailable.\n'
    try:
        backend.parallel({r:prompt(r) for r in ('trapezoid','simpson')},MODEL,EFFORT);stage1=time.time()
        checker=HERE/'projects'/('v4-bare-checker-'+label);prepare(checker,HERE,checker_inputs={r:project for r in ('trapezoid','simpson')});backend.handoff(checker)
        backend.run_agent('checker',prompt('checker'),MODEL,EFFORT);evidence=backend.collect();assert all(v['passed'] for v in evidence.values()),evidence
        finished=time.time();summary={'backend':'plain_docker_codex','started_at':started,'finished_at':finished,'wall_seconds':finished-started,
         'setup_seconds':stage-started,'parallel_stage_seconds':stage1-stage,'checker_stage_seconds':finished-stage1,
         'agents':backend.records,'passed':True,'starting_image':COMPUTE,'state_volume':backend.state,'tools_volume':backend.tools,
         'profile':{'tool_profile':'coding','features':FEATURES}}
        write(output/'summary.json',summary);note('bare_finished',round=label,seconds=summary['wall_seconds']);return summary
    finally:backend.close()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('label',choices=('smoke','first','warm1','warm2','warm3','release'));parser.add_argument('--arm',choices=('native','bare','both'),default='both');args=parser.parse_args()
    old=json.loads((BASE/'protocol.json').read_text()) if (BASE/'protocol.json').exists() else {}
    sources=old.get('source_digests',{label:old.get('source_digest',source_digest()) for label in ('first','warm1','warm2','warm3')})
    sources[args.label]=source_digest()
    write(BASE/'protocol.json',{'model':MODEL,'effort':EFFORT,'features':FEATURES,'image':COMPUTE,
      'checks_sha256':hashlib.sha256((HERE/'fixed_checks.py').read_bytes()).hexdigest(),'source_digest':old.get('source_digest',source_digest()),'source_digests':sources,
      'order':{'first':['native','bare'],'warm1':['bare','native'],'warm2':['native','bare'],'warm3':['bare','native'],'release':['native','bare']},
      'first_run_notice':'New project/run; shared Docker runtime cache already exists. Runtime build excluded from measured arms.',
      'baseline_retains_volumes_but_not_equivalent_rootfs_checkpoints':True})
    if args.label=='smoke':native('smoke',smoke=True);return
    order=['native','bare'] if args.label in ('first','warm2','release') else ['bare','native']
    for arm in order:
        if args.arm not in ('both',arm):continue
        if arm=='native':native(args.label)
        else:bare(args.label)
if __name__=='__main__':main()
