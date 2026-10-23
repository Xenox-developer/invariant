#!/usr/bin/env python3
"""Opt-in guided-project gate: three real Codex configurations and an inline continuation."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from invariant import workspace
from invariant.experiments.acceptance import sources
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.demo import review_apply

TASK='Implement double(value) in solution.py. Return the input integer multiplied by two. Only edit solution.py; use standard-library Python, no packages or network.'
TESTS='import solution\nfor value in [0,2,-3,500]:\n    assert solution.double(value)==value*2\nprint("4 shared checks passed")\n'
COMMAND="python3 -B -c 'import runpy; runpy.run_path(\"tests/check.py\")'"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',required=True,metavar='MODEL',help='authorize three initial model turns and one continuation')
    args=parser.parse_args();os.umask(0o077)
    output=ROOT/'.invariant/experiment-guided-checks'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8])
    project=output/'project';project.mkdir(parents=True);(project/'tests').mkdir()
    (project/'solution.py').write_text('def double(value):\n    raise NotImplementedError()\n')
    (project/'tests/check.py').write_text(TESTS)
    report={'schema_version':1,'passed':False,'checks':[],'model_turns':0,'cleanup_errors':[],
            'source_sha256':sources(ROOT),'requested_model':args.live,'project':str(project)}
    engine=None;studies=[]

    def require(value,message):
        if not value:raise AssertionError(message)

    def passed(message):
        report['checks'].append(message);print('PASS '+message,flush=True)

    def cli(*arguments,machine=True):
        command=[sys.executable,'-B',str(ROOT/'scripts/invariant'),*arguments]
        if machine:command.append('--json')
        result=subprocess.run(command,cwd=project,capture_output=True,text=True,timeout=1200)
        with (output/'commands.log').open('a') as log:log.write(json.dumps(command)+'\n'+result.stdout+'\n'+result.stderr+'\n')
        require(result.returncode==0,result.stdout+result.stderr)
        return json.loads(result.stdout)['data'] if machine else result.stdout

    def idle():
        from invariant.execution import project_lock
        end=time.monotonic()+30
        while time.monotonic()<end:
            try:lease=project_lock(engine.root/'controller')
            except RuntimeError:time.sleep(.1)
            else:lease.close();return
        raise AssertionError('Controller retained its lease')

    def setup(*more):
        return cli('experiment','init','--name','guided-proof','--task',TASK,
                   '--test-command',COMMAND,'--test-path','tests','--model',args.live,
                   '--request-id','guided-proof',*more)

    try:
        cli('init',machine=False);engine=ExperimentEngine(project)
        info=setup()
        require(len(info['configurations'])==3 and info['applied'] is False,'Invalid offline setup')
        require(not engine.list()['studies'],'Setup unexpectedly submitted a study')
        passed('ordinary initialized project accepts flag-only setup with no authored configuration or model calls')
        stub=(project/'solution.py').read_bytes()
        started=setup('--run');idle();studies.append(started['study_id'])
        results=started['results'];report['results']=results
        require(results['summary']['quality_passes']==3 and results['summary']['runner_successes']==3,str(results['summary']))
        intervals=[(r['runner']['started_at'],r['runner']['finished_at']) for r in results['trials']]
        require(max(a for a,b in intervals)<min(b for a,b in intervals),'Initial trials did not overlap')
        require((project/'solution.py').read_bytes()==stub and (project/'tests/check.py').read_text()==TESTS,'Execution changed project files')
        for row in results['trials']:
            require(row['metrics']['test_exit_code']==0,'Test command status missing')
            folder=Path(row['capture']['workspace']).parents[1]/'evaluation/artifacts'
            require((folder/'test-output.txt').read_text().strip()=='4 shared checks passed','Shared command output not captured')
            require(json.loads((folder/'test-result.json').read_text())['command']==COMMAND,'Evaluation used another command')
        passed('three native configurations overlap and pass the original shared command in separate Docker evaluation copies')
        require(setup('--run')['results']==results,'Identical setup/run key replayed work')
        require(cli('experiment','review',started['study_id'])==results,'Review JSON differs from ordinary results')
        require('shared tests' in cli('experiment','review',started['study_id'],machine=False),'Readable review missing')
        passed('keyed reopening and manual/JSON review reuse identical saved results without additional turns')
        choice=next(r for r in results['trials'] if r['variant_id']=='medium')
        cli('experiment','select',choice['attempt_id'],'--if-revision','0',machine=False)
        child=cli('experiment','continue',started['study_id'],'--task',
                  'Add a concise docstring with two examples to double in solution.py. Preserve its behavior. Only edit solution.py.',
                  '--request-id','guided-child','--wait')
        idle();studies.append(child['id']);child_results=cli('experiment','review',child['id']);report['child']=child_results
        row=child_results['trials'][0]
        require(row['passed'] is True and row['metrics']['test_exit_code']==0,'Inline continuation failed evaluation')
        require(child['frozen']['image']['id']==choice['checkpoint']['image'],'Parent checkpoint was not retained')
        require(row['runner']['native']['thread_id']!=choice['runner']['native']['thread_id'],'Conversation was reused')
        cli('experiment','select',row['attempt_id'],'--if-revision','0')
        with contextlib.redirect_stdout(io.StringIO()):require(review_apply(engine,child['id'],confirm=lambda _: 'y'),'Lineage apply failed')
        require((project/'solution.py').read_bytes()==(Path(row['capture']['workspace'])/'solution.py').read_bytes(),'Applied wrong result')
        require((project/'tests/check.py').read_text()==TESTS,'Apply changed original shared tests')
        passed('human selection → inline assistant continuation → reviewed parent/child apply uses one state and preserves shared tests')
        report['passed']=True
    except BaseException as error:
        report['error']=str(error);report['error_type']=type(error).__name__;print('FAIL '+str(error),flush=True)
    finally:
        if engine and engine.store.path.exists():
            turns=set()
            owned=engine.list()['studies']
            for item in owned:
                for trial in engine.inspect(item['id'])['trials']:
                    for attempt in trial['attempts']:
                        native=attempt['record'].get('runner',{}).get('native',{})
                        if native.get('turn_id'):turns.add((native.get('thread_id'),native['turn_id']))
            report['model_turns']=len(turns)
            for identifier in [item['id'] for item in owned]:
                try:
                    if engine.inspect(identifier)['state']!='completed':
                        engine.stop(identifier);idle();engine.recover(identifier)
                    selection=engine.inspect(identifier)['selection']
                    if selection['attempt_id']:engine.select(identifier,revision=selection['revision'],clear=True)
                    engine.delete(identifier,confirm=True)
                except Exception as error:report['cleanup_errors'].append(str(error))
        report['source_matches_at_end']=report['source_sha256']==sources(ROOT)
        report['passed'] &= report['model_turns']==4 and not report['cleanup_errors'] and report['source_matches_at_end']
        workspace.private_write(output/'report.json',report);print('Evidence: '+str(output/'report.json'),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
