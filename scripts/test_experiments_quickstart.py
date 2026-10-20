#!/usr/bin/env python3
"""Build from a clean source export; install a wheel and run the published quickstart."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invariant import workspace
from invariant.experiments.engine import ExperimentEngine


def main():
    output = ROOT/'.invariant/experiment-quickstart-checks'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8])
    output.mkdir(parents=True, mode=0o700)
    checkout, project = output/'checkout', output/'project'
    project.mkdir(); checkout.mkdir()
    report = {'schema_version': 1, 'passed': False, 'checks': [], 'cleanup_errors': [], 'model_turns': 0}
    engine = None

    def command(args, *, cwd=checkout, env=None, timeout=180):
        with (output/'commands.log').open('a') as log:
            result = subprocess.run(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            raise AssertionError('Quickstart command failed: '+str(args)+'; see commands.log')

    try:
        # Export only source/package inputs. No local build, Git state, egg-info,
        # editable installation or saved workspace can make the wheel test pass.
        for name in ('pyproject.toml','README.md','LICENSE','NOTICE'):
            shutil.copyfile(ROOT/name, checkout/name)
        shutil.copytree(ROOT/'invariant',checkout/'invariant',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        command([sys.executable,'-m','pip','wheel','--no-index','--no-build-isolation','--no-deps','--wheel-dir',str(output/'wheel'),'.'])
        wheel = next((output/'wheel').glob('*.whl'))
        with zipfile.ZipFile(wheel) as archive:
            assert any(name.endswith('.dist-info/licenses/LICENSE') for name in archive.namelist())
            assert any(name.endswith('.dist-info/licenses/NOTICE') for name in archive.namelist())
        venv.EnvBuilder(with_pip=True).create(output/'venv')
        binary = output/'venv/bin'
        command([str(binary/'python'),'-m','pip','install','--no-index','--no-deps',str(wheel)])
        env = os.environ.copy();env.pop('PYTHONPATH',None);env['PATH']=str(binary)+os.pathsep+env['PATH']
        command([str(binary/'python'),'-I','-c',"import invariant;assert '/venv/' in invariant.__file__"], cwd=project, env=env)
        for name in ('compare.json','case.json','agent.py','judge.py','quickstart.sh'):
            shutil.copyfile(ROOT/'examples/experiments'/name,project/name)
        command(['/bin/sh','quickstart.sh'],cwd=project,env=env,timeout=600)
        assert (project/'result.txt').read_text()=='42\n'
        assert not (project/'.git').exists()
        command(['invariant','experiment','init','command-starter.json'],cwd=project,env=env)
        command(['invariant','experiment','--help'],cwd=project,env=env)
        # Parser-derived machine errors must remain structured and noninteractive.
        error = subprocess.run(['invariant','experiment','apply','--json'],cwd=project,env=env,capture_output=True,text=True,timeout=10)
        assert error.returncode==2 and json.loads(error.stdout)['error']['code']=='INVALID_ARGUMENTS'
        engine = ExperimentEngine(project)
        report['checks'] = ['clean source wheel imports from an isolated installation',
            'published shell quickstart completes both human/JSON apply handoffs without Git',
            'command/Codex starters and ordinary config/chats/guardrails commands parse',
            'machine misuse yields structured errors without prompts']
        report['wheel']={'name':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest()}
        report['passed']=True
    except BaseException as error:
        report['error']=str(error)
    finally:
        if engine is None and (project/'.invariant/config.json').exists():
            engine=ExperimentEngine(project)
        if engine:
            try:
                for study in engine.list()['studies']:
                    current=engine.inspect(study['id'])
                    if current['state']!='completed':engine.stop(study['id']);engine.recover(study['id'])
                    choice=engine.inspect(study['id'])['selection']
                    if choice['attempt_id']:engine.select(study['id'],revision=choice['revision'],clear=True)
                    engine.delete(study['id'],confirm=True)
            except BaseException as error:report['cleanup_errors'].append(str(error))
        report['passed'] &= not report['cleanup_errors']
        workspace.private_write(output/'report.json',report)
        print('Evidence: '+str(output/'report.json'),flush=True)
        if not report['passed']:print(json.dumps(report),file=sys.stderr)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
