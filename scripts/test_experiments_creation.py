#!/usr/bin/env python3
"""Real Docker/CLI M0–M1 checks; no study execution or model turns."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invariant import environment, resources, workspace
from invariant.execution import ExecutionContext
from invariant.experiments.snapshots import unseal_tree


def require(value, message):
    if not value:
        raise AssertionError(message)


def main():
    nonce = uuid.uuid4().hex
    output = ROOT / '.invariant/experiment-creation-checks' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + nonce[:8])
    output.mkdir(parents=True, mode=0o700)
    report = {'schema_version': 1, 'scope': 'M0/M1 only', 'passed': False,
              'checks': [], 'cleanup_errors': [], 'model_turns': 0}
    temporary = tempfile.TemporaryDirectory(prefix='invariant-m1-check-')
    project = Path(temporary.name).resolve() / 'project'; project.mkdir()
    state = workspace.state_for(project)
    containers, images = [], []
    def passed(name):
        report['checks'].append(name); print('PASS ' + name, flush=True)
    def cli(*arguments, expected=0):
        result = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/invariant'), *arguments],
                                cwd=project, capture_output=True, text=True, timeout=60)
        require(result.returncode == expected, result.stderr + result.stdout)
        return result.stdout
    def machine(*arguments, expected=0):
        response = json.loads(cli('experiment', *arguments, '--json', expected=expected))
        require(response['ok'] == (expected == 0), 'Unexpected JSON error status')
        return response['data'] if expected == 0 else response['error']
    try:
        report['source_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted([*ROOT.glob('invariant/**/*.py'), Path(__file__), ROOT/'MVP_CONTRACT.md', ROOT/'docs/LEGACY_MVP_CONTRACT.md'])}
        cli('init')
        machine('init', 'experiments/compare.json')
        (project/'untracked.txt').write_text('before capture\n')
        (project/'.env').write_text('synthetic-private-value')
        validated = machine('validate', 'experiments/compare.json')
        require(validated['trial_count'] == 2, 'Expected two expanded variants')
        require(not (state/'experiments/store.sqlite3').exists(), 'Validation created a database')
        passed('strict validation and image/capacity resolution without launching')
        arguments=[sys.executable,'-B',str(ROOT/'scripts/invariant'),'experiment','run',
                   'experiments/compare.json','--request-id','real-cli-once','--create-only','--json']
        clients=[subprocess.Popen(arguments,cwd=project,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
        replies=[]
        for client in clients:
            stdout,stderr=client.communicate(timeout=90)
            require(client.returncode==0,stdout+stderr);replies.append(json.loads(stdout)['data'])
        created=replies[0]
        require(replies[1]['id']==created['id'] and replies[1]['trials']==created['trials'],'Concurrent submission duplicated study/attempts')
        report['concurrent_submission']={'study_ids':[r['id'] for r in replies],'attempt_ids':
              [[a['id'] for t in r['trials'] for a in t['attempts']] for r in replies]}
        report['created_study'] = created
        require(created['state'] == 'queued' and created['dispatch_available'] is True, 'Submission claimed execution')
        baseline = state/'experiments/baselines'/created['baseline_id']
        require((baseline/'workspace/untracked.txt').read_text() == 'before capture\n', 'Untracked source missing')
        require(not (baseline/'workspace/.env').exists(), 'Credential path was captured')
        require(not (baseline/'workspace/.invariant').exists(), 'Control state was captured')
        passed('durable queued study with frozen non-Git source and protected exclusions')
        (project/'untracked.txt').write_text('edited after capture\n')
        repeated = machine('run', 'experiments/compare.json', '--request-id', 'real-cli-once', '--create-only')
        require(repeated['id'] == created['id'] and repeated['idempotent_replay'], 'Duplicate request created new study')
        require(machine('status', '--request-id', 'real-cli-once')['id'] == created['id'], 'Request lookup failed')
        require(created['id'] in cli('experiment', 'status', created['id']), 'Readable CLI sees different state')
        require(len(machine('list')['studies']) == 1, 'Duplicate saved studies')
        passed('independent CLI processes share IDs and lost-acknowledgement recovery')
        recipe = json.loads((project/'experiments/compare.json').read_text()); recipe['name'] = 'different'
        (project/'experiments/compare.json').write_text(json.dumps(recipe))
        require(machine('run', 'experiments/compare.json', '--request-id', 'real-cli-once', expected=1)['code'] == 'REQUEST_CONFLICT', 'Changed request not rejected')
        require((baseline/'workspace/untracked.txt').read_text() == 'before capture\n', 'Frozen source mutated')
        passed('changed recipe conflicts; later project edits do not mutate captured evidence')

        policy, _ = resources.select(workspace.load_project(project), project, resources.capacity(workspace.docker))
        image = created['frozen']['image']['id']
        environment.prepare_control(project, image)
        trial_state = state/'m0-context'; trial_state.mkdir(mode=0o700)
        writable = trial_state/'files'; writable.mkdir()
        policy_dir = trial_state/'policy'; policy_dir.mkdir()
        workspace.private_write(policy_dir/'config.json', policy)
        owner = 'attempt-' + nonce
        def context(name, pinned):
            return ExecutionContext.create(project=project, workspace=writable, state=trial_state/'control',
                image=pinned, owner_token=name, policy=policy, policy_directory=policy_dir,
                control_directory=environment.control_path(project), checkpoint_owner=owner)
        name = 'invariant-context-' + nonce; containers.append(name)
        first = context(name, image)
        workspace.docker(*workspace.container_command(name, project, image, context=first))
        workspace.verify_container(name, project, image, context=first)
        workspace.docker('start', name)
        workspace.docker('exec', name, '/bin/sh', '-c',
                         'printf context-only > /workspace/private.txt; printf "#!/bin/sh\\nprintf persisted" > /usr/local/bin/invariant-m0-tool; chmod +x /usr/local/bin/invariant-m0-tool')
        require(not (project/'private.txt').exists(), 'Execution context wrote original source')
        require((writable/'private.txt').read_text() == 'context-only', 'Private workspace write failed')
        passed('Docker verifies private workspace mounts using explicit execution identity')
        # Mutated mount observations must be rejected against the supplied context.
        info = json.loads(workspace.docker('inspect', name))[0]
        for mount in info['Mounts']:
            if mount['Destination'] == '/workspace': mount['Source'] = str(project)
        try:
            workspace.verify_container(name, project, image, info=info, context=first)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Verifier accepted original-project mount for trial')
        passed('mount drift cannot be verified as the private context')
        (trial_state/'control').mkdir()
        first.preserve(name)
        saved = json.loads(first.checkpoint_path.read_text())['image']; images.append(saved)
        require(not (state/'environment.json').exists(), 'Trial overwrote ordinary saved environment')
        require(environment.selected_image(trial_state/'control', project, image, owner=owner) == saved, 'Owned checkpoint cannot be selected')
        second_name = 'invariant-context-restored-' + nonce; containers.append(second_name)
        second = context(second_name, saved)
        workspace.docker(*workspace.container_command(second_name, project, saved, context=second))
        workspace.verify_container(second_name, project, saved, context=second)
        workspace.docker('start', second_name)
        require(workspace.docker('exec', second_name, '/usr/local/bin/invariant-m0-tool') == 'persisted', 'Tool did not survive private checkpoint')
        passed('context-owned tool persistence leaves ordinary environment/checkpoints untouched')
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
        print('FAIL ' + str(error), file=sys.stderr)
    finally:
        for name in containers:
            try: workspace.remove_owned(name)
            except Exception as error: report['cleanup_errors'].append(str(error))
        for image in images:
            try: workspace.docker('image','rm', image)
            except Exception as error: report['cleanup_errors'].append(str(error))
        try:
            if state.exists(): unseal_tree(state); shutil.rmtree(state)
            temporary.cleanup()
        except Exception as error:
            report['cleanup_errors'].append(str(error))
        report['passed'] = report['passed'] and not report['cleanup_errors']
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print('Evidence: ' + str(output/'report.json'))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
