#!/usr/bin/env python3
"""Opt-in M4 gate: two real native turns plus interchangeable selection clients."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invariant import workspace, preferences
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.snapshots import unseal_tree

JUDGE = '''import json,os,pathlib
v=json.loads(pathlib.Path(os.environ['INVARIANT_VARIANT_FILE']).read_text())
p=pathlib.Path(os.environ['INVARIANT_CANDIDATE_DIR'])/'outputs/answer.txt'
answer=p.read_text().strip() if p.exists() else None
own=pathlib.Path('/usr/local/bin/invariant-m4-'+v['id'])
other=pathlib.Path('/usr/local/bin/invariant-m4-'+('blue' if v['id']=='red' else 'red'))
print(json.dumps({'schema_version':1,'passed':answer==v['id'] and own.exists() and not other.exists(),
                 'metrics':{'correct':int(answer==v['id']),'own_tool':int(own.exists()),'sibling_tool':int(other.exists())},'artifacts':[]}))
'''


def sources():
    paths = [*ROOT.glob('invariant/**/*.py'), *ROOT.glob('scripts/tests/test_experiment*.py'), Path(__file__),
             ROOT/'MVP_CONTRACT.md', ROOT/'docs/IMPLEMENTATION_PLAN.md', ROOT/'docs/experiments/ASSISTANT_GUIDE.md']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', metavar='MODEL', required=True, help='use existing host ChatGPT allowance for exactly two small native turns')
    parser.add_argument('--effort', default='medium')
    args = parser.parse_args()
    os.umask(0o077)
    output = ROOT/'.invariant/experiment-codex-checks'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8])
    project = output/'project'; project.mkdir(parents=True)
    state = workspace.state_for(project)
    report = {'schema_version':1,'scope':'M4 native Codex and interchangeable selection clients',
              'passed':False,'checks':[],'cleanup_errors':[], 'requested_model':args.live,'requested_effort':args.effort,
              'model_turns':0,'source_sha256':sources(), 'host_system':platform.system(), 'limitations':['Local host with Linux Docker; other host systems require separate evidence.',
              'Provider authentication/throttling/cancellation failures have injected unit coverage, not extra live calls.']}
    engine = None; studies = []
    def require(value, message):
        if not value: raise AssertionError(message)
    def passed(message): report['checks'].append(message); print('PASS '+message,flush=True)
    def cli(*args, expected=0):
        value = subprocess.run([sys.executable,'-B',str(ROOT/'scripts/invariant'),*args],cwd=project,
                               capture_output=True,text=True,timeout=600)
        require(value.returncode==expected,value.stdout+value.stderr)
        return value.stdout
    def machine(*args, expected=0):
        value = json.loads(cli('experiment',*args,'--json',expected=expected))
        require(value['ok']==(expected==0),str(value)); return value['data'] if expected==0 else value['error']
    def hashes(path):
        if not path.exists(): return {}
        return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}
    def idle():
        from invariant.execution import project_lock
        end = time.monotonic()+20
        while time.monotonic()<end:
            try: lease=project_lock(engine.root/'controller')
            except RuntimeError: time.sleep(.1)
            else: lease.close(); return
        raise AssertionError('Controller did not release its lease')
    try:
        cli('init'); engine=ExperimentEngine(project)
        subprocess.run(['git','init','--quiet',str(project)],check=True,capture_output=True)
        (project/'judge.py').write_text(JUDGE)
        (project/'prompt.txt').write_text('Perform the exact small task from the developer instructions using the command tool. Then finish.\n')
        for color in ('red','blue'):
            other = 'blue' if color=='red' else 'red'
            script = "import pathlib;assert not pathlib.Path('/usr/local/bin/invariant-m4-"+other+"').exists();pathlib.Path('/usr/local/bin/invariant-m4-"+color+"').write_text('saved tool');p=pathlib.Path('/workspace/outputs');p.mkdir(exist_ok=True);(p/'answer.txt').write_text('"+color+"');print('M4 "+color+" complete')"
            (project/(color+'.txt')).write_text('Use one command to execute this Python snippet exactly, without network access: python3 -c '+repr(script)+'. Do not edit any other file or invoke other agents.\n')
        definition = {'schema_version':1,'name':'native-colors','runner':{'type':'codex'},
            'variants':[{'id':color,'model':args.live,'effort':args.effort,'instructions_file':color+'.txt'} for color in ('red','blue')],
            'cases':[{'id':'tiny','prompt_file':'prompt.txt'}], 'execution':{'max_parallel':2,'trial_cpus':.5,
               'trial_memory_mib':512,'trial_timeout_seconds':240},
            'evaluator':{'argv':['python3','judge.py'],'source_paths':['judge.py'],'timeout_seconds':15},
            'artifacts':['outputs/**']}
        (project/'native.json').write_text(json.dumps(definition))
        # Seed ordinary state so preservation means more than absence.
        preferences.save(state, project, args.live, args.effort)
        (state/'codex').mkdir(); (state/'codex/ordinary-history').write_text('ordinary conversation')
        ordinary={p:hashes(state/p) if (state/p).is_dir() else (state/p).read_bytes().hex()
                  for p in ('launch.json','codex')}
        before=hashes(project)
        text=cli('experiment','run','native.json','--create-only','--request-id','native-human')
        study=machine('status','--request-id','native-human'); studies.append(study['id'])
        require(study['id'] in text,'Readable launch did not show its durable ID')
        # Source capture correctly refuses an active ordinary session. Introduce
        # its marker only after capture to test execution's independent state.
        workspace.private_write(state/'current.json', {'fixture':'ordinary-session'})
        ordinary['current.json']=(state/'current.json').read_bytes().hex()
        # Captured prompt/instructions remain authoritative after live edits.
        (project/'prompt.txt').write_text('DO NOT EXECUTE: live source is not the captured prompt')
        (project/'red.txt').write_text('DO NOT EXECUTE: live instructions changed')
        (project/'blue.txt').write_text('DO NOT EXECUTE: live instructions changed')
        frozen_before=hashes(project)
        text=cli('experiment','execute',study['id'],'--wait','--request-id','native-execute')
        require('completed' in text,'Readable native execution did not finish')
        idle()
        result=machine('results',study['id']); report['native_results']=result
        attempts=[a for t in engine.inspect(study['id'])['trials'] for a in t['attempts']]
        report['model_turns']=sum(bool(a['record'].get('runner',{}).get('native',{}).get('turn_id')) for a in attempts)
        require(result['summary']['runner_successes']==2 and result['summary']['quality_passes']==2,str(result['summary']))
        require(report['model_turns']==2,'Expected exactly two accepted native model turns')
        require(hashes(project)==frozen_before,'Execution changed original project/Git contents')
        passed('two real Codex variants execute captured prompts/instructions and pass captured evaluation')
        homes=[];threads=[];receipts=[]
        for row in result['trials']:
            native=row['runner']['native']; homes.append(native['home']);threads.append(native['thread_id'])
            require(native['model']==args.live and native['effort']==args.effort,'Observed model/effort mismatch')
            require(row['token_usage'] is not None or row['usage_unavailable_reason'],'Missing usage was hidden')
            require(Path(native['home']).is_relative_to(state/'experiments/attempts'),'Native history escaped attempt state')
            runtime=row['runner']['runtime'];require(runtime['network']=='none','Executor had unexpected network')
            require(runtime['memory_bytes']==512*1024**2 and runtime['nano_cpus']==500000000,'Resource allocation mismatch')
            mounts={m['Destination']:m for m in runtime['mounts']}
            require(mounts['/workspace']['Source']!=str(project),'Original project mounted writable')
            require(not any('/native-home' in m['Source'] or m['Destination']=='/var/run/docker.sock' for m in runtime['mounts']),'Host auth/control exposure')
            require(row['metrics']['own_tool']==1 and row['metrics']['sibling_tool']==0,'Rootfs isolation failed')
            require((Path(row['capture']['workspace'])/'outputs/answer.txt').read_text()==row['variant_id'],'Sibling workspace output leaked')
            receipts.append((row['runner']['started_at'],row['runner']['finished_at']))
        require(len(set(homes))==2 and len(set(threads))==2,'Mutable native history was shared')
        require(max(t[0] for t in receipts)<min(t[1] for t in receipts),'Native trials did not overlap')
        passed('parallel native trials have separate conversations, workspaces and rootfs layers with verified limits')
        require(ordinary=={p:hashes(state/p) if (state/p).is_dir() else (state/p).read_bytes().hex()
                  for p in ('launch.json','current.json','codex')},'Ordinary chat state/preferences changed')
        passed('ordinary project chat history, current session and preferences stay unchanged')
        choice=result['trials'][0]['attempt_id']
        selected=machine('select',choice,'--if-revision','0','--request-id','script-choice')
        require(choice in cli('experiment','status',study['id']),'Readable client did not observe scripted choice')
        require(machine('select',choice,'--if-revision','0','--request-id','script-choice')==selected,'Selection retry changed result')
        require(machine('select',result['trials'][1]['attempt_id'],'--if-revision','0',expected=1)['code']=='SELECTION_CONFLICT','Stale choice accepted')
        require(machine('delete',study['id'],'--confirm',expected=1)['code']=='SELECTED_STATE','Selected state deleted')
        passed('readable launch → scripted selection → readable inspection; CAS, replay and deletion reference checks')
        (state/'current.json').unlink()
        # Reverse clients with cheap command trials: no additional live provider turns.
        definition.update(name='reverse-clients',runner={'type':'command','argv':['/bin/true']},
                          variants=[{'id':'one','parameters':{}},{'id':'two','parameters':{}}],cases=[{'id':'case'}])
        definition.pop('evaluator');(project/'reverse.json').write_text(json.dumps(definition))
        reverse=machine('run','reverse.json','--wait','--request-id','script-launch');studies.append(reverse['id']);idle()
        trial=reverse['trials'][1]['id']
        cli('experiment','select',trial,'--if-revision','0','--request-id','human-choice')
        require(machine('results',reverse['id'])['selection']['attempt_id']==reverse['trials'][1]['attempts'][0]['id'],'JSON client did not observe readable selection')
        passed('scripted launch → readable selection → scripted inspection uses the same state')
        frames=[json.loads(line) for line in cli('experiment','logs',study['id'],'--follow','--json').splitlines()]
        report['native_log_frames']=frames
        require(frames[-1]['kind']=='follow_end','Saved native logs did not finish')
        again=[json.loads(line) for line in cli('experiment','logs',study['id'],'--follow','--after',frames[-1]['cursor'],'--json').splitlines()]
        require(len(again)==1 and again[0]['kind']=='follow_end','Log cursor replayed native output')
        passed('native log frames persist and resume without replaying a model turn')
        report['passed']=True
    except BaseException as error:
        report['error']=str(error); report['error_type']=type(error).__name__
        print('FAIL '+str(error),flush=True)
    finally:
        if engine:
            for identifier in reversed(studies):
                try:
                    study=engine.inspect(identifier)
                    if study['state']!='completed': engine.stop(identifier);idle();engine.recover(identifier)
                    selection=engine.inspect(identifier)['selection']
                    if selection['attempt_id']: engine.select(identifier,revision=selection['revision'],clear=True)
                    engine.delete(identifier,confirm=True)
                except BaseException as error: report['cleanup_errors'].append(str(error))
        report['source_matches_at_end']=report['source_sha256']==sources()
        if report['cleanup_errors'] or not report['source_matches_at_end']: report['passed']=False
        workspace.private_write(output/'report.json',report)
        print('Report: '+str(output/'report.json'),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
