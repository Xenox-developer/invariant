#!/usr/bin/env python3
"""Bare init/run/resume, automatic limits, preferences and recovery with real Docker/Codex."""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from invariant.workspace import docker, private_write, remove_owned, state_for


def sources():
    paths = [p for p in (ROOT / 'invariant').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths += [Path(__file__), ROOT / 'docs/LEGACY_MVP_CONTRACT.md', ROOT / 'scripts/invariant']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='use two model turns for persistent chat and native resume')
    parser.add_argument('--oom', action='store_true', help='trigger one bounded child OOM inside our disposable executor')
    args = parser.parse_args()
    output = ROOT / '.invariant/easy-workflow-checks' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
    project = output / 'project';project.mkdir(parents=True, mode=0o700)
    report = {'passed': False, 'checks': [], 'source_sha256': sources(), 'project': str(project),
              'live': args.live, 'cleanup_errors': []}
    child = None
    containers = []
    sequence = 0

    def require(condition, message):
        if not condition: raise AssertionError(message)

    def passed(name):
        report['checks'].append(name);print('PASS ' + name, flush=True)

    def cli(*commands, cwd=None, check=True):
        result = subprocess.run([str(ROOT / 'scripts/invariant'), *commands], cwd=cwd or project,
                                capture_output=True, text=True, timeout=30)
        if check: require(result.returncode == 0, result.stderr + result.stdout)
        return result.stdout if check else result

    def wait(condition, log, seconds=210):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if condition(): return
            if child.poll() is not None: raise AssertionError('Launcher exited: ' + log.read_text()[-5000:])
            time.sleep(.2)
        raise AssertionError('Launcher timed out: ' + log.read_text()[-5000:])

    def start(*command):
        nonlocal child, sequence
        sequence += 1
        log = output / (str(sequence) + '.log')
        with log.open('w') as stream:
            child = subprocess.Popen([str(ROOT / 'scripts/invariant'), *command], cwd=project,
                                     stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT, text=True)
        wait(lambda: '\nYou> ' in log.read_text(), log, seconds=85)
        return log

    def message(text, log):
        before = log.read_text().count('\nYou> ')
        child.stdin.write(text+'\n');child.stdin.flush()
        wait(lambda: log.read_text().count('\nYou> ') > before, log)

    def close(log, *, eof=False):
        nonlocal child
        if eof: child.stdin.close()
        else: child.stdin.write('/exit\n');child.stdin.flush()
        # General environments checkpoint the stopped filesystem before exit.
        # Allow the bounded commit/cleanup path, including under Docker disk load.
        require(child.wait(timeout=330) == 0, log.read_text()[-4000:])
        if not eof: child.stdin.close()
        child = None
        require(not (state_for(project)/'current.json').exists(), 'Active record survived cleanup')

    def inspect(label):
        value = json.loads(cli('guardrails','--json'))
        require(value['verified'], str(value['issues']))
        name = 'invariant-' + value['session']
        if name not in containers: containers.append(name)
        private_write(output/(label+'.json'),value)
        return value,name

    try:
        initialized = cli('init')
        require('Next: invariant run' in initialized, 'Init still requires model flags')
        config = project/'.invariant/config.json';baseline=config.read_bytes()
        require(json.loads(baseline)['resource_mode']=='auto', 'New project does not use auto sizing')
        require(json.loads(baseline)['duration_seconds']==3600, 'New session budget differs')
        cli('init');require(config.read_bytes()==baseline, 'Init reset settings')
        inactive = json.loads(cli('guardrails','--json'))
        require(not inactive['verified'] and inactive['configured']['memory_bytes'] is None, 'Auto guesses labeled live/selected')
        passed('bare_init_selects_auto_defaults_without_questions_or_overwrites')

        log = start('run')
        initial,name=inspect('initial')
        plan=initial['runtime']['resources'];kernel=initial['runtime']['kernel']
        require(plan['mode']=='auto' and 64<=kernel['memory_bytes']//1024**2<=4096 and kernel['cpus']<=4, 'Auto budget unbounded')
        require(not initial['configuration_changed'], 'Resolved auto budget called a pending config edit')
        require(initial['runtime']['launcher']['duration_seconds']==3600, 'Bare run ignored default lifetime')
        launch=json.loads((state_for(project)/'launch.json').read_text())
        require(launch['project']==str(project) and launch['model'], 'Confirmed preferences missing')
        report['selected_launch']=launch
        require((state_for(project)/'launch.json').stat().st_mode&0o777==0o600, 'Preferences not private')
        require(config.read_bytes()==baseline, 'Auto sizing rewrote project config')
        passed('bare_run_discovers_account_default_remembers_preferences_and_verifies_auto_limits')
        nonce='remember-'+uuid.uuid4().hex
        if args.live:
            message('Remember this exact phrase for later: '+nonce+'. Do not write that phrase to any file. '
                    'Create hello.py containing print(6*7), run it, and write its output to result.txt. Do this in the workspace.',log)
            require((project/'result.txt').read_text()=='42\n', 'Bare run did not execute a model-driven task')
            chats=json.loads(cli('chats','--json'));require(len(chats)==1,'Chat not saved')
            chat=chats[0]['id'];report['chat']=chat
            passed('no_flag_chat_creates_and_executes_project_files')
        else: chat=None

        increased=None
        if args.oom:
            memory=kernel['memory_bytes']//1024**2
            require(memory<=2048, 'Refusing a stress check over 2 GiB')
            # Only this container's child allocates; its measured cgroup is a hard boundary.
            result=subprocess.run(['docker','exec',name,'python3','-I','-c',
                'parts=[]\nwhile True: parts.append(bytearray(16*1024*1024))'],capture_output=True,text=True,timeout=45)
            require(result.returncode==137, 'Child did not hit its bounded OOM: '+result.stderr[-1000:])
            increased=min(plan['memory_ceiling_mib'],memory*2)
            report['oom_child_exit']=result.returncode
        close(log)
        require('Chat saved.' in log.read_text() if args.live else 'no message was accepted' in log.read_text(), 'Closure guidance incorrect')
        if args.oom and increased>plan['limits']['memory_mib']:
            require(json.loads((state_for(project)/'resources.json').read_text())['memory_mib']==increased,'OOM advice not recorded')
            require('next session will use' in log.read_text(),'OOM recovery not explained')
            passed('real_child_oom_learns_bounded_next_session_memory_without_replaying_commands')

        log=start('resume') if args.live else start('run')
        resumed,name=inspect('resumed')
        require(json.loads((state_for(project)/'launch.json').read_text())==launch,'Remembered preferences changed')
        require('remembered project settings' in log.read_text() if not args.live else 'saved chat' in log.read_text(), 'Preference source not explained')
        if increased and increased>plan['limits']['memory_mib']:
            require(resumed['runtime']['kernel']['memory_bytes']==increased*1024**2,'Next session did not resize')
        if args.live:
            require('Resumed chat: '+chat in log.read_text(),'Resume replaced conversation')
            message('Write the exact phrase I asked you to remember to remembered.txt followed by a newline. '
                    'Do not guess. Run hello.py again and confirm its output is 42.',log)
            require((project/'remembered.txt').read_text()==nonce+'\n','Native conversation memory lost')
            passed('bare_resume_preserves_context_model_effort_and_uses_recovered_resource_budget')
        close(log,eof=True)

        # Empty new chats can remember preferences but must not replace last saved chat.
        log=start('run','--model',launch['model'],*(['--effort',launch['effort']] if launch['effort'] else []))
        inspect('explicit-preferences')
        previous=json.loads((state_for(project)/'current.json').read_text())['session']
        child.kill();child.wait(timeout=10);child.stdin.close();child=None
        log=start('resume') if args.live else start('run')
        inspect('recovered')
        require('Recovering the previous workspace' in log.read_text(),'Missing crash recovery message')
        require('will not be replayed' in log.read_text(),'Recovery implied retrying work')
        if args.live:
            require('Resumed chat: '+chat in log.read_text(),'Empty new chat replaced saved conversation')
        check=subprocess.run(['docker','inspect','invariant-'+previous],capture_output=True)
        require(check.returncode!=0,'Crash orphan not removed')
        close(log)
        passed('remembered_flags_empty_chats_and_crash_recovery_preserve_prior_conversation')

        empty=output/'empty';empty.mkdir();cli('init',cwd=empty)
        missing=cli('resume',cwd=empty,check=False)
        require(missing.returncode==1 and 'Start one with invariant run' in missing.stderr,'No-chat recovery is unclear')
        require(config.read_bytes()==baseline,'Workflow changed project guardrails')
        require(sources()==report['source_sha256'],'Sources changed during verification')
        report['passed']=True
    except BaseException as error:
        report['error']=str(error);print('FAIL '+str(error),file=sys.stderr,flush=True)
    finally:
        if child:
            if child.poll() is None: child.terminate();child.wait(timeout=330)
            child.stdin.close()
        current=state_for(project)/'current.json'
        if current.exists():
            session=json.loads(current.read_text()).get('session','')
            if session.startswith('ws-') and len(session)==35:containers.append('invariant-'+session)
        for name in set(containers):
            try:remove_owned(name)
            except Exception as error:report['cleanup_errors'].append(str(error))
        if report['cleanup_errors']:report['passed']=False
        private_write(output/'report.json',report);print(output/'report.json',flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
