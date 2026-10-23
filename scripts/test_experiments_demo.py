#!/usr/bin/env python3
"""Opt-in real demo gate: three parallel configurations, one continuation and reviewed apply.

Retains the generated demo/results, restores its original stub and clears selections so
a person can choose a result afterwards. Refuses an existing destination.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invariant import workspace
from invariant.experiments import demo
from invariant.experiments.acceptance import sources
from invariant.experiments.engine import ExperimentEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', required=True, metavar='MODEL', help='authorize three real initial turns and one continuation')
    parser.add_argument('--project', type=Path, help='new directory in which to retain the usable demo')
    args = parser.parse_args()
    os.umask(0o077)
    output = ROOT/'.invariant/experiment-demo-checks'/(time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())+'-'+uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    project = (args.project or output/'project').expanduser().absolute()
    report = {'schema_version':1, 'passed':False, 'checks':[], 'source_sha256':sources(ROOT),
              'host_system':platform.system(), 'project':str(project), 'requested_model':args.live,
              'model_turns':0, 'retained_studies':[], 'limitations':['One task and one initial attempt per effort; no statistical winner.']}
    engine = None

    def require(value, message):
        if not value: raise AssertionError(message)

    def passed(message):
        report['checks'].append(message); print('PASS '+message, flush=True)

    def cli(*arguments, machine=True):
        command = [sys.executable, '-B', str(ROOT/'scripts/invariant'), 'experiment', *arguments]
        if machine: command.append('--json')
        result = subprocess.run(command, cwd=project if project.exists() else ROOT,
                                capture_output=True, text=True, timeout=1200)
        with (output/'commands.log').open('a') as log:
            log.write(json.dumps(command)+'\n'+result.stdout+'\n'+result.stderr+'\n')
        require(result.returncode==0, result.stdout+result.stderr)
        if not machine: return result.stdout
        envelope = json.loads(result.stdout)
        require(envelope['ok'], str(envelope)); return envelope['data']

    def hashes():
        return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in project.iterdir() if p.is_file()}

    def idle():
        from invariant.execution import project_lock
        end = time.monotonic()+30
        while time.monotonic()<end:
            try: lease = project_lock(engine.root/'controller')
            except RuntimeError: time.sleep(.1)
            else: lease.close(); return
        raise AssertionError('Controller did not release its lease')

    try:
        require(not project.exists() and not project.is_symlink(), 'Use a new destination; existing project was kept.')
        cli('demo', str(project), '--model', args.live)
        stub = (project/'solution.py').read_bytes(); original = hashes()
        initial = cli('demo', str(project), '--run')
        engine = ExperimentEngine(project); idle()
        study = engine.inspect(initial['study_id'])
        report['retained_studies'].append(study['id']); report['initial_results'] = initial['results']
        rows = initial['results']['trials']
        require(len(rows)==3 and {r['variant_id'] for r in rows}=={'low','medium','high'}, 'Expected three effort configurations')
        require(all(r['state']=='succeeded' and r['metrics'].get('tests_total')==23 and type(r['passed']) is bool for r in rows),
                'Some initial candidates did not complete shared evaluation; inspect saved results')
        require(any(r['passed'] for r in rows), 'No passing candidate is available for continuation verification')
        require(hashes()==original and study['selection']['attempt_id'] is None, 'Execution changed project files/selection')
        native = [r['runner']['native'] for r in rows]
        require(all(n['model']==args.live and n['effort']==r['variant_id'] for n,r in zip(native,rows)), 'Observed settings differ')
        require(len({n['thread_id'] for n in native})==len({n['home'] for n in native})==3, 'Native histories were shared')
        require(all(r['runner']['runtime']['network']=='none' for r in rows), 'Unexpected command networking')
        mounts = [{m['Destination']:m for m in r['runner']['runtime']['mounts']} for r in rows]
        require(len({m['/workspace']['Source'] for m in mounts})==3 and all(m['/workspace']['Source']!=str(project) for m in mounts),
                'Private workspaces were not distinct')
        intervals = [(r['runner']['started_at'],r['runner']['finished_at']) for r in rows]
        require(initial['concurrency']['max_parallel']==3 and max(a for a,b in intervals)<min(b for a,b in intervals), 'Three runs did not overlap')
        passed('three real Codex efforts overlap, use distinct histories/workspaces and receive the same 23 frozen checks')
        repeated = cli('demo', str(project), '--run')
        require(repeated['study_id']==study['id'] and repeated['results']==initial['results'], 'Reopening replayed or changed results')
        passed('reopening the demo preserves the same study, turns, results and untouched project')
        # This verification choice exercises the workflow, not a product recommendation.
        chosen = next((r for r in rows if r['variant_id']=='medium' and r['passed']), next(r for r in rows if r['passed']))
        cli('select', chosen['attempt_id'], '--if-revision', '0', machine=False)
        require(cli('results',study['id'])['selection']['attempt_id']==chosen['attempt_id'], 'JSON did not observe human selection')
        parent = engine.results(study['id'])
        child = cli('continue',study['id'],'--prompt-file','followup.md','--wait','--request-id','demo-verification-child')
        idle(); report['retained_studies'].append(child['id'])
        child_results = cli('results',child['id']); report['child_results'] = child_results
        row = child_results['trials'][0]
        require(row['passed'] is True and row['metrics']=={'tests_passed':23,'tests_total':23}, 'Continuation failed shared evaluation')
        require(child['frozen']['image']['id']==chosen['checkpoint']['image'], 'Child did not use exact parent environment')
        require(row['runner']['native']['thread_id']!=chosen['runner']['native']['thread_id'], 'Continuation reused conversation')
        for name in ('suite.json','verify.py'):
            require(child['frozen']['inputs'][name]==study['frozen']['inputs'][name], 'Captured evaluation changed')
        require(engine.results(study['id'])==parent and hashes()==original, 'Continuation changed parent or project')
        passed('human selection hands off to JSON continuation with the exact saved environment and shared evaluator')
        cli('select',row['attempt_id'],'--if-revision','0')
        require(demo.review_apply(engine,child['id'],confirm=lambda _: 'y'), 'Reviewed lineage apply did not complete')
        require((project/'solution.py').read_bytes()==(Path(row['capture']['workspace'])/'solution.py').read_bytes(), 'Applied bytes differ')
        require({k:v for k,v in hashes().items() if k!='solution.py'}=={k:v for k,v in original.items() if k!='solution.py'}, 'Apply changed unrelated files')
        passed('reviewed plans apply parent then continuation, preserving unrelated project files')
        # Reset only this newly created, test-owned stub; keep frozen evidence intact.
        (project/'solution.py').write_bytes(stub)
        for identifier in reversed(report['retained_studies']):
            selection = engine.inspect(identifier)['selection']
            engine.select(identifier,revision=selection['revision'],clear=True)
        require(hashes()==original, 'Demo was not restored for a user choice')
        reopened = cli('demo',str(project),'--run')
        require(reopened['study_id']==study['id'] and reopened['results']['selection']['attempt_id'] is None, 'Restored demo cannot reopen')
        report['restored_stub_and_cleared_selections']=True
        passed('saved initial results remain ready for a user to choose, continue or apply without rerunning them')
        report['passed']=True
    except BaseException as error:
        report['error']=str(error); report['error_type']=type(error).__name__
        print('FAIL '+str(error),flush=True)
    finally:
        if engine:
            turns = set()
            for study in engine.list()['studies']:
                for trial in engine.inspect(study['id'])['trials']:
                    for attempt in trial['attempts']:
                        n = attempt['record'].get('runner',{}).get('native',{})
                        if n.get('turn_id'): turns.add((n.get('thread_id'),n['turn_id']))
            report['model_turns']=len(turns)
            if report['model_turns']!=4:
                report['passed']=False
                report.setdefault('error', 'Expected exactly four accepted native turns')
        report['source_matches_at_end']=sources(ROOT)==report['source_sha256']
        if not report['source_matches_at_end']: report['passed']=False
        workspace.private_write(output/'report.json',report)
        print('Evidence: '+str(output/'report.json'),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
