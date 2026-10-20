#!/usr/bin/env python3
"""Real Docker M3 gate. Synthetic commands only; never restarts the host daemon."""
import base64
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from invariant import environment, workspace
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.snapshots import unseal_tree

HARNESS = '''import os,time,pathlib,json
v=json.loads(pathlib.Path(os.environ['INVARIANT_VARIANT_FILE']).read_text())
print('START '+os.environ['INVARIANT_ATTEMPT_ID'],flush=True)
p=pathlib.Path('/workspace/once.txt')
assert not p.exists(), 'attempt replayed'
p.write_text('executed once')
pathlib.Path('/usr/local/bin/m3-proof').write_text('persistent environment')
time.sleep(v['parameters'].get('delay',.2))
print('DONE',flush=True)
'''
FAULT_WORKER = '''import sys,time,pathlib
from invariant import environment
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.controller import Controller
from invariant.experiments.runners import CommandRunner
project,state,identifier,point,marker=sys.argv[1:]
def pause():
    pathlib.Path(marker).write_text(point)
    while True:time.sleep(.1)
class FaultRunner(CommandRunner):
    def run(self,context,**kwargs):
        original=kwargs.get('on_intent')
        def intent(operation):
            if original:original(operation)
            if (point=='before_create' and operation=='create') or (point=='after_create' and operation=='start'):pause()
        kwargs['on_intent']=intent
        started=kwargs.get('on_started')
        def running():
            if started:started()
            if point=='after_start':pause()
        kwargs['on_started']=running
        return super().run(context,**kwargs)
original=environment.preserve
def preserve(*args,**kwargs):
    if point=='before_checkpoint':pause()
    if point=='checkpoint_failure':raise RuntimeError('synthetic checkpoint fault')
    value=original(*args,**kwargs)
    if point=='after_checkpoint':pause()
    return value
environment.preserve=preserve
if point=='disk_failure':
    import invariant.experiments.controller as module
    def fail(*args,**kwargs):raise OSError('synthetic disk fault')
    module.capture_workspace=fail
Controller(ExperimentEngine(project,state=state),runner=FaultRunner()).execute(identifier)
'''


def main():
    nonce=uuid.uuid4().hex
    output=ROOT/'.invariant/experiment-lifecycle-checks'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+nonce[:8])
    project=output/'project';project.mkdir(parents=True,mode=0o700)
    state=workspace.state_for(project)
    report={'schema_version':1,'scope':'M3 detached command lifecycle','passed':False,'checks':[],
            'cleanup_errors':[],'model_turns':0,'limitations':['Docker outage is simulated; the shared host daemon is not restarted.']}
    env=os.environ.copy();env['PYTHONPATH']=str(ROOT)
    children=[];engine=None;foreign='invariant-m3-foreign-'+nonce[:12]
    def require(ok,message):
        if not ok:raise AssertionError(message)
    def passed(name):report['checks'].append(name);print('PASS '+name,flush=True)
    def cli(*args,expected=0):
        result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/invariant'),*args],cwd=project,env=env,
                              capture_output=True,text=True,timeout=240)
        require(result.returncode==expected,result.stderr+result.stdout)
        return result.stdout
    def command(*args):
        value=json.loads(cli('experiment',*args,'--json'))
        require(value['ok'],str(value));return value['data']
    def wait(predicate,timeout=40):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.1)
        raise AssertionError('Timed out waiting for lifecycle evidence')
    def idle():
        from invariant.execution import project_lock
        def available():
            try:lease=project_lock(engine.root/'controller')
            except RuntimeError:return False
            lease.close();return True
        wait(available)
    def recipe(name,delay=.2,count=1):
        value={'schema_version':1,'name':name,'runner':{'type':'command','argv':['python3','agent.py']},
               'variants':[{'id':'v'+str(i),'parameters':{'delay':delay}} for i in range(count)],'cases':[{'id':'c'}],
               'execution':{'max_parallel':1,'trial_cpus':.5,'trial_memory_mib':256,'trial_timeout_seconds':60}}
        path=name+'.json';(project/path).write_text(json.dumps(value));return path
    def queued(name,**kwargs):return command('run',recipe(name,**kwargs),'--create-only')
    def fault(study,point):
        marker=output/(point+'.marker')
        child=subprocess.Popen([sys.executable,'-I','-c','import sys;sys.path.insert(0,'+repr(str(ROOT))+');'+FAULT_WORKER,str(project),str(state),study['id'],point,str(marker)],
                               cwd=project,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                               start_new_session=True,text=True)
        children.append(child)
        if point.endswith('failure'):
            _,error=child.communicate(timeout=80);require(child.returncode==0,error)
        else:
            wait(lambda:marker.exists() or (child.poll() is not None and (_ for _ in ()).throw(AssertionError(child.stderr.read()))),timeout=240)
            if point=='after_start':
                wait(lambda:(engine.root/'attempts'/study['trials'][0]['attempts'][0]['id']/'workspace/once.txt').exists())
            child.kill();child.communicate(timeout=10)
        return engine.inspect(study['id'])
    try:
        sources=[*ROOT.glob('invariant/**/*.py'),Path(__file__),ROOT/'MVP_CONTRACT.md']
        report['source_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}
        cli('init');(project/'agent.py').write_text(HARNESS)
        (project/'invariant.py').write_text('raise RuntimeError("Untrusted project code imported on host")')
        engine=ExperimentEngine(project)
        study=queued('detached',delay=4)
        started=time.monotonic()
        launched=command('execute',study['id'])
        require(time.monotonic()-started<3,'Detached launch waited for workload')
        # A second launcher races with the first; both acknowledge the same durable attempt.
        command('execute',study['id'])
        wait(lambda:any(t['state']=='running' for t in engine.inspect(study['id'])['trials']))
        viewer=subprocess.Popen([sys.executable,str(ROOT/'scripts/invariant'),'experiment','logs',study['id'],'--follow','--json'],
                                cwd=project,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        children.append(viewer)
        first=json.loads(viewer.stdout.readline());require(first['kind']=='log_chunk',str(first))
        viewer.terminate();viewer.communicate(timeout=5)
        rest=[json.loads(line) for line in cli('experiment','logs',study['id'],'--follow','--after',first['cursor'],'--json').splitlines()]
        frames=[first,*[r for r in rest if r['kind']!='follow_end']]
        require(len({r['sequence'] for r in frames})==len(frames),'Cursor duplicated frames')
        content=b''.join(base64.b64decode(r['data']['data_base64']) for r in frames if r['kind']=='log_chunk')
        require(content.count(b'START ')==1 and content.count(b'DONE')==1,'Concurrent launch replayed or lost logs')
        complete=command('execute',study['id'],'--wait');idle()
        require(complete['state']=='completed' and rest[-1]['terminal'],'Detached work did not finish after viewer exit')
        report['detached']={'study_id':study['id'],'state':complete['state'],'log_frames':frames}
        passed('detached launch survives caller/viewer exit; concurrent launch and cursor reconnect execute exactly once')
        # Crash an actual daemon while Docker owns a live process.
        killed=queued('daemon-crash',delay=25)
        command('execute',killed['id'])
        wait(lambda:any(t['state']=='running' for t in engine.inspect(killed['id'])['trials']))
        wait(lambda:(engine.root/'attempts'/killed['trials'][0]['attempts'][0]['id']/'workspace/once.txt').exists())
        lease=environment.read_record(engine.root/'controller/lease.json')
        os.kill(lease['pid'],signal.SIGKILL);idle()
        recovered=command('recover',killed['id'],'--request-id','recover-daemon')
        require(not recovered['unresolved_attempt_ids'] and recovered['retry_required'],str(recovered))
        attempt=engine.inspect(killed['trials'][0]['attempts'][0]['id'])
        require(attempt['state']=='interrupted','Lost daemon attempt was replayed')
        require(attempt['record']['recovered_checkpoints'],'Recovery lost installed environment')
        report['daemon_recovery']={'recovery':recovered,'attempt':attempt}
        require(command('recover',killed['id'],'--request-id','recover-daemon')==recovered,'Recover acknowledgement was not idempotent')
        # Stop this long retry after proving a fresh numbered attempt exists.
        retry=command('retry',killed['trials'][0]['id'],'--request-id','retry-daemon')
        require(retry['number']==2,'Explicit retry did not add attempt 2')
        require(command('retry',killed['trials'][0]['id'],'--request-id','retry-daemon')==retry,'Retry duplicated on lost acknowledgement')
        command('stop',killed['id']);command('execute',killed['id'],'--wait');idle()
        require(engine.results(killed['id'])['summary']['runner_successes']==0,'Retry hid the first interrupted attempt')
        passed('killed daemon recovery stops and saves uncertain work; explicit retry preserves first-attempt comparison')
        # All owned foreign processes must remain untouched by stop/recover.
        image=study['frozen']['image']['id']
        workspace.docker('run','--detach','--name',foreign,'--entrypoint=python3',image,'-c','import time;time.sleep(3600)')
        report['faults']={}
        for point in ('before_create','after_create','after_start','before_checkpoint','after_checkpoint','checkpoint_failure','disk_failure'):
            candidate=queued(point,delay=8 if point=='after_start' else .1)
            before=fault(candidate,point)
            attempt_id=candidate['trials'][0]['attempts'][0]['id']
            original=engine.inspect(attempt_id)
            if point.endswith('failure'):
                require(original['state']=='failed',point+' was not classified as failed')
                expected='CHECKPOINT_FAILED' if point=='checkpoint_failure' else 'CAPTURE_FAILED'
                require(original['reason']==expected,str(original))
            recovered=command('recover',candidate['id']);idle()
            require(not recovered['unresolved_attempt_ids'],point+' could not recover')
            final=engine.inspect(attempt_id)
            require(final['state']==('failed' if point.endswith('failure') else 'interrupted'),point+' was replayed or relabeled successful')
            require(len(engine.inspect(candidate['trials'][0]['id'])['attempts'])==1,'Recovery created an implicit retry')
            require(json.loads(workspace.docker('inspect',foreign))[0]['State']['Running'],'Recovery touched a foreign container')
            if point.endswith('failure'):require(final['reason']==expected,'Recovery hid original failure reason')
            report['faults'][point]={'recovery':recovered,'attempt':final,'foreign_running':True}
            passed(point+': crash/fault recovery preserves evidence without replay or foreign process changes')
        pressure=queued('queue-pressure',delay=25,count=5)
        command('execute',pressure['id'])
        wait(lambda:any(t['state']=='running' for t in engine.inspect(pressure['id'])['trials']))
        began=time.monotonic();stopped=command('stop',pressure['id'],'--request-id','stop-pressure')
        elapsed=time.monotonic()-began
        require(stopped['termination_confirmed'] and elapsed<10,str(stopped))
        command('execute',pressure['id'],'--wait');idle()
        final=engine.inspect(pressure['id'])
        require(all(t['state']=='cancelled' for t in final['trials']),'Stop allowed queued work to start')
        require(sum(t['attempts'][0]['record']['runner']['status']=='not_started' for t in final['trials'])==4,'More than one workload started')
        report['stop_seconds']=elapsed
        passed('stop under queue pressure terminates within ten seconds and prevents four queued starts')
        preview=command('delete',study['id'])
        require(not preview['deleted'],'Deletion preview mutated saved study')
        deleted=command('delete',study['id'],'--confirm')
        require(deleted['deleted'],'Terminal state deletion did not finish')
        passed('terminal deletion previews owned files/images and removes only explicitly confirmed state')
        report['passed']=True
    except Exception as error:
        report['error']=str(error);print('FAIL '+str(error),file=sys.stderr)
    finally:
        for child in children:
            if child.poll() is None:child.kill()
            try:child.communicate(timeout=5)
            except Exception:pass
        if engine:
            # Terminate only the controller that records this fixture's private state.
            lease=environment.read_record(engine.root/'controller/lease.json')
            if lease and lease.get('state')=='active':
                try:os.kill(lease['pid'],signal.SIGKILL)
                except ProcessLookupError:pass
        try:
            names=workspace.docker('ps','-aq','--filter','label=io.invariant.project='+environment.project_key(project)).split()
            for name in names:workspace.docker('rm','--force',name)
            try:workspace.docker('rm','--force',foreign)
            except RuntimeError as error:
                if 'No such container' not in str(error):raise
            images=workspace.docker('image','ls','-q','--filter','label=io.invariant.checkpoint-project='+environment.project_key(project)).split()
            for image in dict.fromkeys(images):workspace.docker('image','rm',image,timeout=120)
        except Exception as error:report['cleanup_errors'].append(str(error))
        try:
            if state.exists():unseal_tree(state);shutil.rmtree(state)
        except Exception as error:report['cleanup_errors'].append(str(error))
        report['source_matches']=all((ROOT/path).exists() and hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==value for path,value in report.get('source_sha256',{}).items())
        report['passed'] &= report['source_matches'] and not report['cleanup_errors']
        workspace.private_write(output/'report.json',report)
        print('Evidence: '+str(output/'report.json'),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
