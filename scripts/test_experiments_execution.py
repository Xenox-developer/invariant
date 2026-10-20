#!/usr/bin/env python3
"""Real Docker M2 gate: overlap, isolation, captured evaluation and mixed outcomes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from invariant import environment, workspace
from invariant.experiments.snapshots import unseal_tree

HARNESS='''import json,os,pathlib,time,sys
v=json.loads(pathlib.Path(os.environ['INVARIANT_VARIANT_FILE']).read_text())
c=json.loads(pathlib.Path(os.environ['INVARIANT_CASE_FILE']).read_text())
p=v['parameters'];mode=p.get('mode','normal')
assert not pathlib.Path('/usr/local/bin/invariant-m2-tool').exists(), 'sibling root leaked'
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/workspace/.git').exists()
started=time.time()
if mode=='exit':sys.exit(7)
if mode=='timeout':time.sleep(45)
if mode=='oom':
    memory=[]
    while True:memory.append(bytearray(16*1024*1024))
assert not pathlib.Path('/workspace/outputs/answer.json').exists(), 'sibling workspace leaked'
secrets=json.loads(pathlib.Path(os.environ['INVARIANT_SECRET_FILES']).read_text())
for path in secrets.values():
    value=pathlib.Path(path).read_bytes();os.write(1,value[:4]);time.sleep(.01);os.write(1,value[4:]+b'\\n')
out=pathlib.Path(os.environ['INVARIANT_OUTPUT_DIR']);out.mkdir(exist_ok=True)
pathlib.Path('/workspace/judge.py').write_text('raise RuntimeError("candidate evaluator must not run")')
if p.get('correct',False):
    tool=pathlib.Path('/usr/local/bin/invariant-m2-tool');tool.write_text('#!/bin/sh\\nprintf tools-persisted');tool.chmod(0o755)
time.sleep(.7)
(out/'answer.json').write_text(json.dumps({'answer':c.get('answer',10) if p.get('correct',False) else -1,
  'variant':v['id'],'started':started,'finished':time.time(),'source':'frozen-v1'}))
print('command-finished '+v['id'],flush=True)
'''
JUDGE='''import json,os,pathlib,time
v=json.loads(pathlib.Path(os.environ['INVARIANT_VARIANT_FILE']).read_text());mode=v['parameters'].get('mode')
if mode=='bad-evaluation':print('not-json');raise SystemExit(0)
if mode=='evaluation-timeout':time.sleep(30)
c=json.loads(pathlib.Path(os.environ['INVARIANT_CASE_FILE']).read_text())
root=pathlib.Path(os.environ['INVARIANT_CANDIDATE_DIR'])
a=json.loads((root/'outputs/answer.json').read_text())
(root/'only-evaluation.txt').write_text('must not change saved candidate')
out=pathlib.Path(os.environ['INVARIANT_EVALUATION_OUTPUT_DIR']);(out/'report.txt').write_text('measured')
passed=a['answer']==c['answer']
print(json.dumps({'schema_version':1,'passed':passed,'metrics':{'score':int(passed)},'artifacts':['report.txt']}))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ten-variants',action='store_true',help='release matrix: ten variants, two cases and two repetitions')
    options=parser.parse_args()
    nonce=uuid.uuid4().hex
    output=ROOT/'.invariant/experiment-execution-checks'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+nonce[:8])
    project=output/'project';project.mkdir(parents=True,mode=0o700)
    report={'schema_version':1,'scope':'M2 command/evaluation regression with M3 detached dispatch','passed':False,'checks':[],'cleanup_errors':[],'model_turns':0}
    state=workspace.state_for(project);child=None
    env=os.environ.copy()
    secret=output/'secret.txt';secret.write_text('synthetic-m2-secret-'+nonce);secret.chmod(0o600)
    env['INVARIANT_SECRET_FIXTURE']=str(secret)
    images=set()
    def require(ok,message):
        if not ok:raise AssertionError(message)
    def passed(name):report['checks'].append(name);print('PASS '+name,flush=True)
    def cli(*args,expected=0):
        result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/invariant'),*args],cwd=project,env=env,capture_output=True,text=True,timeout=900 if options.ten_variants else 180)
        require(result.returncode==expected,result.stderr+result.stdout)
        return result.stdout
    def machine(*args):
        if args[0] in ('execute','run') and '--create-only' not in args:
            args = (*args, '--wait')
        result=json.loads(cli('experiment',*args,'--json'))
        require(result['ok'],str(result));return result['data']
    def hashes():
        return {str(path.relative_to(project)):hashlib.sha256(path.read_bytes()).hexdigest() for path in project.rglob('*')
                if path.is_file() and '.invariant' not in path.relative_to(project).parts}
    def remember(results):
        for trial in results['trials']:
            if (trial.get('checkpoint') or {}).get('record'):images.add(trial['checkpoint']['image'])
    def save_recipe(name,variants,**kwargs):
        value={'schema_version':1,'name':name,'runner':{'type':'command','argv':['python3','agent.py']},
               'variants':variants,'cases':[{'id':'a','input_file':'case-a.json'}],
               'execution':{'max_parallel':2,'trial_cpus':.5,'trial_memory_mib':256,'trial_timeout_seconds':15},
               'evaluator':{'argv':['python3','judge.py'],'source_paths':['judge.py'],'timeout_seconds':15},
               'secret_refs':{'provider':'FIXTURE'},'artifacts':['outputs/**'],**kwargs}
        (project/(name+'.json')).write_text(json.dumps(value));return name+'.json'
    try:
        report['source_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted([*ROOT.glob('invariant/**/*.py'),Path(__file__),ROOT/'MVP_CONTRACT.md'])}
        cli('init')
        if shutil.which('git'):subprocess.run(['git','init','--quiet',str(project)],check=True,capture_output=True)
        (project/'agent.py').write_text(HARNESS);(project/'judge.py').write_text(JUDGE)
        (project/'case-a.json').write_text('{"answer":10}')
        (project/'case-b.json').write_text('{"answer":20}')
        variants=([{'id':'v'+str(i),'parameters':{'correct':i%2==0}} for i in range(10)] if options.ten_variants else
                  [{'id':'incorrect','parameters':{'correct':False}}, {'id':'correct','parameters':{'correct':True}}])
        normal=save_recipe('parallel',variants,
                           cases=[{'id':'a','input_file':'case-a.json'},{'id':'b','input_file':'case-b.json'}],repetitions=2)
        created=machine('run',normal,'--create-only','--request-id','frozen-parallel')
        # Prove execution uses frozen code/evaluator/cases, never current project files.
        (project/'agent.py').write_text('raise AssertionError("live project used")')
        (project/'judge.py').write_text('raise AssertionError("live evaluator used")')
        (project/'case-a.json').write_text('{"answer":999}')
        before=hashes()
        text=cli('experiment','execute',created['id'],'--wait')
        require('completed' in text,'Readable execution did not finish')
        results=machine('results',created['id']);remember(results);report['parallel']=results
        require(hashes()==before,'Execution mutated original source or Git state')
        total=len(variants)*4
        require(results['summary']['scheduled']==total and results['summary']['runner_successes']==total,'Not all queued trials executed')
        require(results['summary']['quality_passes']==total//2 and results['summary']['quality_failures']==total//2,'Exit zero was confused with quality')
        require(results['summary']['failed']==0,'Valid evaluator false should remain a completed experiment')
        receipts=[]
        for trial in results['trials']:
            candidate=Path(trial['capture']['workspace'])
            receipt=json.loads((candidate/'outputs/answer.json').read_text());receipts.append(receipt)
            require(receipt['variant']==trial['variant_id'] and receipt['source']=='frozen-v1','Variant/code isolation failed')
            require(not (candidate/'only-evaluation.txt').exists(),'Evaluation changed saved candidate')
            require(trial['checkpoint']['complete'] and trial['artifacts']['complete'],'Incomplete saved evidence')
            runtime=trial['runner']['runtime']
            require(runtime['image']==created['frozen']['image']['id'],'Trial did not share frozen image')
            require(runtime['memory_bytes']==256*1024**2 and runtime['nano_cpus']==500000000,'Docker limits differ')
            source=next(m['Source'] for m in runtime['mounts'] if m['Destination']=='/workspace')
            require(source!=str(project) and str(state) in source,'Original project was mounted writable')
        require(any(a['started']<b['finished'] and b['started']<a['finished'] for i,a in enumerate(receipts) for b in receipts[i+1:]),'No real command overlap')
        report['parallel_receipts']=receipts
        report['variant_count']=len(variants);report['trial_count']=total
        report['original_source_unchanged']=hashes()==before
        passed(str(total)+' frozen trials overlap, isolate source/Git/parameters and share one base image')
        passed('captured evaluator survives candidate edits and uses a separate candidate copy')
        passed('quality failures, cases, repetitions and measured Docker limits compare explicitly')
        events=machine('status',created['id'])['events'];active=maximum=0
        for event in events:
            if event['kind']=='resources_reserved':active+=1;maximum=max(maximum,active)
            if event['kind']=='resources_released':active-=1
        require(maximum==2 and active==0,'Aggregate reservations exceeded the window or leaked')
        report['reservation_events']=events;report['maximum_reserved']=maximum
        logs=machine('logs',created['id'])
        require(secret.read_text() not in json.dumps(logs) and '[REDACTED]' in json.dumps(logs),'Known secret leaked to logs')
        passed('aggregate reservations include evaluation; logs redact referenced secrets')
        correct=next(row for row in results['trials'] if row['passed'] is True)
        restored=workspace.docker('run','--rm','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
                                  '--entrypoint=/usr/local/bin/invariant-m2-tool',correct['checkpoint']['image'])
        require(restored=='tools-persisted','Installed tool was not usable from saved image')
        passed('saved candidate image retains an executable installed by only one variant')
        require(machine('execute',created['id'])['state']=='completed','Completed study could not be inspected idempotently')
        require(machine('run',normal,'--create-only','--request-id','frozen-parallel')['id']==created['id'],'Existing run was duplicated')
        (project/'agent.py').write_text(HARNESS);(project/'judge.py').write_text(JUDGE)
        mixed=save_recipe('mixed',[{'id':mode,'parameters':{'mode':mode}} for mode in ('exit','timeout','bad-evaluation','evaluation-timeout','oom')],
             execution={'max_parallel':2,'trial_cpus':.5,'trial_memory_mib':256,'trial_timeout_seconds':15},
             # The budget includes Docker launch/attach overhead. Keep enough
             # headroom to classify malformed output on a busy desktop; the
             # sleeping evaluator still substantially exceeds this deadline.
             evaluator={'argv':['python3','judge.py'],'source_paths':['judge.py'],'timeout_seconds':10})
        mixed_study=machine('run',mixed,'--request-id','mixed-run')
        failures=machine('results',mixed_study['id']);remember(failures);report['mixed']=failures
        require(failures['summary']['failed']==5,'Failed fixtures were reported successful')
        reasons={row['variant_id']:row['reason'] for row in failures['trials']}
        require(reasons=={'exit':'PROCESS_EXIT','timeout':'DEADLINE_EXCEEDED','bad-evaluation':'INVALID_EVALUATION','evaluation-timeout':'DEADLINE_EXCEEDED','oom':'OOM'},str(reasons))
        passed('nonzero exit, OOM, runner deadline, malformed evaluation and evaluator deadline remain distinct')
        # The same command path must also work in a project with no Git metadata.
        if (project/'.git').exists():shutil.move(project/'.git',output/'saved-git')
        cli('experiment','init','no-evaluator.json')
        no_git_before=hashes()
        simple=machine('run','no-evaluator.json','--request-id','no-evaluation')
        simple_results=machine('results',simple['id']);remember(simple_results)
        require(simple_results['summary']['runner_successes']==2 and simple_results['summary']['quality_judged']==0,'Missing evaluator fabricated quality')
        report['non_git']={'passed':hashes()==no_git_before,'results':simple_results}
        require(report['non_git']['passed'],'Non-Git execution changed original source')
        passed('readable and JSON clients execute the same records; absent quality remains unknown')
        direct=project/'direct-judge.sh'
        direct.write_text('#!/bin/sh\nprintf \'{"schema_version":1,"passed":true,"metrics":{},"artifacts":[]}\n\'\n');direct.chmod(0o755)
        recipe=json.loads((project/'no-evaluator.json').read_text())
        recipe['name']='direct-evaluator';recipe['evaluator']={'argv':['./direct-judge.sh'],'source_paths':['direct-judge.sh']}
        (project/'direct.json').write_text(json.dumps(recipe))
        direct_study=machine('run','direct.json');direct_results=machine('results',direct_study['id']);remember(direct_results)
        require(direct_results['summary']['quality_passes']==2,'Executable captured evaluator lost permissions')
        passed('captured evaluators support direct shell executables without a Python-specific runner')
        cancelling=save_recipe('cancel',[{'id':str(i),'parameters':{'mode':'timeout'}} for i in range(3)],
                    execution={'max_parallel':1,'trial_cpus':.5,'trial_memory_mib':256,'trial_timeout_seconds':30})
        queued=machine('run',cancelling,'--create-only')
        cli('experiment','execute',queued['id'],'--json')
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            status=machine('status',queued['id'])
            if any(trial['state']=='running' for trial in status['trials']):break
            time.sleep(.1)
        else:raise AssertionError('Cancellation fixture did not start')
        stopped=machine('stop',queued['id'])
        require(stopped['termination_confirmed'],str(stopped))
        machine('execute',queued['id'])
        cancelled=machine('results',queued['id']);remember(cancelled);report['cancelled']=cancelled
        require(cancelled['summary']['cancelled']==3,'Cancellation started queued work or left an active attempt')
        active_cancelled=[row for row in cancelled['trials'] if row['runner']['status']=='cancelled']
        require(len(active_cancelled)==1 and active_cancelled[0]['runner']['duration_seconds']<15,'Cancelled command ran to its natural 20-second finish')
        passed('persisted stop controls stop owned work and prevent queued starts')
        report['passed']=True
    except Exception as error:
        report['error']=str(error);print('FAIL '+str(error),file=sys.stderr)
    finally:
        if child:
            child.send_signal(signal.SIGTERM)
            try:child.communicate(timeout=30)
            except subprocess.TimeoutExpired:child.kill();child.communicate()
        # Only this fixture's positively identified containers/images are cleaned.
        try:
            raw=workspace.docker('ps','-aq','--filter','label=io.invariant.project='+environment.project_key(project))
            for name in raw.split():
                info=json.loads(workspace.docker('inspect',name))[0]
                workspace.remove_owned(info['Config']['Labels']['io.invariant.session'])
            if state.exists():
                for path in (state/'experiments/attempts').glob('*/control/environment.json'):
                    images.add(json.loads(path.read_text())['image'])
            for image in images:
                info=json.loads(workspace.docker('image','inspect',image))[0]
                require(info['Config']['Labels'].get('io.invariant.checkpoint-project')==environment.project_key(project),'Refusing foreign checkpoint cleanup')
                workspace.docker('image','rm',image)
            if state.exists():unseal_tree(state);shutil.rmtree(state)
            secret.unlink(missing_ok=True)
        except Exception as error:report['cleanup_errors'].append(str(error))
        report['passed']=report['passed'] and not report['cleanup_errors']
        report['source_matches']=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in report['source_sha256'].items())
        report['passed'] &= report['source_matches']
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print('Evidence: '+str(output/'report.json'))
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
