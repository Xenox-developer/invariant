#!/usr/bin/env python3
"""Live native persistence, terminal closure, and same-conversation recovery."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from invariant.workspace import private_write, state_for
from invariant import environment, workspace


def sources():
    paths = [p for p in (ROOT / 'invariant').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths += [Path(__file__), ROOT / 'docs/LEGACY_MVP_CONTRACT.md', ROOT / 'scripts/invariant']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', required=True, metavar='MODEL', help='uses signed-in Codex allowance for three short turns')
    parser.add_argument('--effort', default='medium')
    args = parser.parse_args()
    os.umask(0o077)
    output = ROOT / '.invariant/resume-checks' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
    project = output / 'project'
    project.mkdir(parents=True)
    report = {'passed': False, 'checks': [], 'source_sha256': sources(), 'model': args.live, 'effort': args.effort}
    child = None
    sequence = 0

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def passed(name):
        report['checks'].append(name)
        print('PASS ' + name, flush=True)

    def cli(*arguments, text=None):
        return subprocess.run([str(ROOT / 'scripts/invariant'), *arguments], cwd=project, input=text,
                              capture_output=True, text=True, timeout=60)

    def start(*arguments):
        nonlocal child, sequence
        sequence += 1
        log = output / (str(sequence) + '.log')
        with log.open('w') as stream:
            child = subprocess.Popen([str(ROOT / 'scripts/invariant'), *arguments, '--duration', '240'],
                                     cwd=project, stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT, text=True)
        wait(lambda: '\nYou> ' in log.read_text(), log)
        return log

    def wait(condition, log):
        end = time.monotonic() + 210
        while time.monotonic() < end:
            if condition():
                return
            if child.poll() is not None:
                raise AssertionError('Launcher exited: ' + log.read_text()[-4000:])
            time.sleep(0.15)
        raise AssertionError('Launcher timed out: ' + log.read_text()[-4000:])

    def message(text, log):
        previous = log.read_text().count('\nYou> ')
        child.stdin.write(text + '\n')
        child.stdin.flush()
        wait(lambda: log.read_text().count('\nYou> ') > previous, log)

    def finish():
        nonlocal child
        child.wait(timeout=25)
        child.stdin.close()
        require(child.returncode == 0, 'Shutdown failed')
        require(not (state_for(project) / 'current.json').exists(), 'Active record survived clean shutdown')
        child = None

    def saved():
        result = cli('chats', '--json')
        require(result.returncode == 0, result.stderr)
        return json.loads(result.stdout)

    try:
        result = cli('init')
        require(result.returncode == 0, result.stderr)
        require(saved() == [], 'Fresh project contained saved chats')
        nonce = 'invariant-memory-' + uuid.uuid4().hex
        log = start('run', '--agent', 'codex', args.live, '--effort', args.effort)
        require(saved() == [], 'Empty native chat was advertised as durable')
        message('Remember this exact phrase for later: ' + nonce + '. Do not write it to any file or use tools. Reply only: Remembered.', log)
        rows = saved()
        require(len(rows) == 1, 'First accepted turn was not saved')
        chat_id = rows[0]['id']
        report['chat'] = chat_id
        require(rows[0]['model'] == args.live and rows[0]['effort'] == args.effort, 'Saved configuration changed')
        child.send_signal(signal.SIGHUP)
        finish()
        passed('terminal_hangup_saves_chat_and_cleans_executor')

        # Restart in another process, without passing the nonce or model again.
        log = start('resume')
        require('Resumed chat: ' + chat_id in log.read_text(), 'Resume selected a different chat')
        require('Reasoning effort: ' + args.effort in log.read_text(), 'Resume lost effort')
        status = cli('status', '--json')
        require(status.returncode == 0, status.stderr)
        executor = 'invariant-' + json.loads(status.stdout)['session']
        policy=workspace.load_project(project)
        general=environment.enabled(policy)
        observed=json.loads(workspace.docker('inspect',executor))[0]
        config=observed['HostConfig']
        require(not config['Privileged'] and config['CapDrop']==['ALL'],'Resumed executor gained capabilities')
        require(config['NetworkMode']=='none' and config['Memory']>0 and config['PidsLimit']>0,'Resumed executor lost isolation/limits')
        require(any('no-new-privileges' in option for option in config['SecurityOpt']),'Resumed executor can gain privileges')
        require(config['ReadonlyRootfs']==(not general),'Resumed filesystem profile changed')
        expected_uid=0 if general else 65532
        probe_code='import os,socket,pathlib\nassert os.getuid()=='+str(expected_uid)+'\n'+'''
assert not pathlib.Path('/var/run/docker.sock').exists()
for family in (socket.AF_INET,socket.AF_INET6):
    try: s=socket.socket(family,socket.SOCK_STREAM)
    except PermissionError: pass
    else: s.close(); raise AssertionError('network socket allowed')
'''
        probe_code+='\nassert not pathlib.Path('+repr(str(Path.home()/'.codex/auth.json'))+').exists()\n'
        probe = subprocess.run(['docker', 'exec', executor, 'python3', '-c', probe_code], capture_output=True, text=True, timeout=15)
        require(probe.returncode == 0, 'Resumed executor boundary failed: ' + probe.stderr)
        report['resumed_runtime']={'profile':policy['profile'],'uid':expected_uid,'image':observed['Image'],
            'memory_bytes':config['Memory'],'pids_limit':config['PidsLimit'],'network':config['NetworkMode'],
            'cap_drop':config['CapDrop'],'readonly_rootfs':config['ReadonlyRootfs'],'mounts':observed['Mounts']}
        passed('resumed_executor_denies_network_credentials_and_docker_control')
        message('Write the exact phrase I asked you to remember to remembered.txt, followed by a newline. '
                'Use a Python command to read it back and print it. Do not guess or ask me to repeat it.', log)
        require((project / 'remembered.txt').read_text() == nonce + '\n', 'Conversation memory was lost after terminal hangup')
        passed('latest_resume_remembers_prior_message_and_runs_in_workspace')
        status = cli('status', '--json')
        require(status.returncode == 0, status.stderr)
        previous = json.loads(status.stdout)['session']
        child.kill()
        child.wait(timeout=10)
        child.stdin.close()
        child = None

        log = start('resume', chat_id)
        message('Write that same remembered phrase into after-kill.txt with a newline and verify it using Python. '
                'Also run a Python check that IPv4 and IPv6 sockets are denied and /var/run/docker.sock is absent. '
                'Report the checks.', log)
        require((project / 'after-kill.txt').read_text() == nonce + '\n', 'Conversation memory was lost after forced termination')
        child.stdin.write('/exit\n')
        child.stdin.flush()
        finish()
        journals = [json.loads(line) for path in state_for(project).glob('ws-*.jsonl') for line in path.read_text().splitlines()]
        require(any(row['event'] == 'recovered_previous_session' and row['previous'] == previous for row in journals), 'Missing exact-session recovery')
        configurations = [row for row in journals if row['event'] == 'session_configured']
        require(len(configurations) == 3 and all(row['chat'] == chat_id for row in configurations), 'Conversation identity changed')
        require(all(row['reasoning_effort'] == args.effort for row in configurations), 'Effort changed')
        require(sorted(row['resumed'] for row in configurations) == [False, True, True], 'Missing resume evidence')
        report['model_turns']=sum(row['event']=='turn_completed' and row.get('status')=='completed' for row in journals)
        require(report['model_turns']==3,'Expected three completed native turns')
        report['sessions']=[{k:row[k] for k in ('chat','resumed','reasoning_effort')} for row in configurations]
        passed('explicit_resume_after_sigkill_restores_history_and_recovers_owned_container')

        result = cli('resume', text='')
        require(result.returncode == 0, result.stderr)
        require(len(saved()) == 1 and saved()[0]['id'] == chat_id, 'EOF lost saved chat')
        passed('eof_keeps_saved_conversation')
        result = cli('run', '--agent', 'codex', args.live, text='/exit\n')
        require(result.returncode == 0, result.stderr)
        require(len(saved()) == 1 and saved()[0]['id'] == chat_id, 'Empty new chat replaced latest')
        passed('empty_new_chat_preserves_latest')
        result = cli('resume', str(uuid.uuid4()), text='/exit\n')
        require(result.returncode != 0 and 'metadata' in result.stderr, 'Unknown chat silently started a new chat')
        passed('unknown_chat_fails_without_replacement')
        # Other user projects may have their own live sessions; check only ours.
        for row in journals:
            if row['event'] == 'session_start':
                check = subprocess.run(['docker', 'inspect', 'invariant-' + row['session']], capture_output=True)
                require(check.returncode != 0, 'Test container survived cleanup')
        require(sources() == report['source_sha256'], 'Source changed during verification')
        report['passed'] = True
    except BaseException as error:
        report['error'] = str(error)
        print('FAIL ' + str(error), file=sys.stderr, flush=True)
    finally:
        if child:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=25)
            child.stdin.close()
        private_write(output / 'report.json', report)
        print(output / 'report.json', flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
