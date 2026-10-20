#!/usr/bin/env python3
"""Verify an initialized operator project while preserving its existing files/chats."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from invariant.codex_rpc import CodexRpc, RpcError
from invariant.dependencies import inspect_dependencies
from invariant.workspace import (IMAGE, check_project_files, codex_configuration, container_command,
                                 docker, find_codex, load_project, private_write, remove_owned,
                                 state_for, verify_container)


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--effort', default='medium')
    args = parser.parse_args()
    os.umask(0o077)
    project = args.project.resolve()
    load_project(project)
    check_project_files(project)
    state = state_for(project)
    if (state / 'current.json').exists():
        raise RuntimeError('An existing workspace session must finish before this test; it was not interrupted')
    run_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
    output = ROOT / '.invariant/project-checks' / run_id
    output.mkdir(parents=True, mode=0o700)
    probe_name = '.invariant-check-' + uuid.uuid4().hex[:12]
    probe_dir = project / probe_name
    baseline = hashes(project)
    source_hashes = hashes(ROOT / 'invariant')
    original_chats = {p.name: p.read_bytes() for p in (state / 'chats').glob('*.json')}
    latest_path = state / 'latest-chat.json'
    original_latest = latest_path.read_bytes() if latest_path.exists() else None
    report = {'passed': False, 'project': str(project), 'model': args.model, 'effort': args.effort,
              'checks': [], 'observations': {}, 'cleanup_errors': [], 'source_sha256': source_hashes,
              'original_project_sha256': baseline, 'original_chat_ids': list(original_chats)}
    child = rpc = None
    raw_container = None
    sessions = []
    test_chat = None
    sequence = 0
    hostile = project / '.codex'
    hostile_created = False
    sentinel = output / 'host-canary.txt'
    sentinel.write_text('synthetic host canary ' + uuid.uuid4().hex + '\n')
    original_sentinel = sentinel.read_bytes()
    escape_marker = output / 'unexpected-host-write'

    def require(condition, detail):
        if not condition:
            raise AssertionError(detail)

    def passed(name, **facts):
        report['checks'].append({'name': name, 'passed': True, **facts})
        print('PASS ' + name, flush=True)
        private_write(output / 'report.json', report)

    def cli(*arguments, text=None, timeout=60):
        return subprocess.run([str(ROOT / 'scripts/invariant'), *arguments], cwd=project, input=text,
                              capture_output=True, text=True, timeout=timeout)

    def wait(condition, log, timeout=210):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return
            if child.poll() is not None:
                raise AssertionError('Launcher stopped: ' + log.read_text()[-3000:])
            time.sleep(0.2)
        raise AssertionError('Launcher did not become ready: ' + log.read_text()[-3000:])

    def start(*arguments):
        nonlocal child, sequence
        sequence += 1
        log = output / (str(sequence) + '-chat.log')
        with log.open('w') as stream:
            child = subprocess.Popen([str(ROOT / 'scripts/invariant'), *arguments, '--duration', '300'],
                                     cwd=project, stdin=subprocess.PIPE, stdout=stream,
                                     stderr=subprocess.STDOUT, text=True)
        wait(lambda: '\nYou> ' in log.read_text(), log)
        status = cli('status', '--json')
        require(status.returncode == 0, status.stderr)
        session = json.loads(status.stdout)['session']
        sessions.append(session)
        return log, 'invariant-' + session

    def message(text, log):
        before = log.read_text().count('\nYou> ')
        child.stdin.write(text + '\n')
        child.stdin.flush()
        wait(lambda: log.read_text().count('\nYou> ') > before, log)

    def finish(*, hangup=False):
        nonlocal child
        if hangup:
            child.send_signal(signal.SIGHUP)
        else:
            child.stdin.write('/exit\n')
            child.stdin.flush()
        child.wait(timeout=25)
        child.stdin.close()
        require(child.returncode == 0, 'Launcher shutdown failed')
        require(not (state / 'current.json').exists(), 'Active metadata survived shutdown')
        child = None

    try:
        ready = inspect_dependencies()
        require(ready['ready'], 'Host dependencies are not ready')
        passed('host_dependencies_ready', dependencies=ready)
        image = docker('image', 'inspect', IMAGE, '--format', '{{.Id}}')
        probe_dir.mkdir(mode=0o700)
        log, container = start('run', '--agent', 'codex', args.model, '--effort', args.effort)
        verify_container(container, project, image)
        passed('real_project_session_uses_verified_container', container=container, image=image)
        second = cli('run', '--agent', 'codex', args.model, text='/exit\n')
        require(second.returncode != 0 and 'already has an active' in second.stderr, 'Concurrent ownership was allowed')
        wrong = cli('stop', 'ws-' + '0' * 32)
        require(wrong.returncode != 0 and child.poll() is None, 'A stale session ID stopped the live session')
        passed('exclusive_ownership_and_stale_stop_rejection')

        probe = '''import ctypes,errno,json,os,pathlib,resource,shutil,socket,subprocess,sys
root=pathlib.Path('/workspace')
out=root/PROBE
results={}
assert os.getuid()!=0
status=pathlib.Path('/proc/self/status').read_text().splitlines()
fields=dict(line.split(':',1) for line in status if ':' in line)
assert fields['NoNewPrivs'].strip()=='1'
assert int(fields['CapEff'].strip(),16)==0 and int(fields['CapBnd'].strip(),16)==0
assert fields['Seccomp'].strip()=='2'
results['non_root_no_capabilities_seccomp']=True
for family in [socket.AF_INET,socket.AF_INET6]:
    try: sock=socket.socket(family,socket.SOCK_STREAM)
    except OSError as e: assert e.errno in (errno.EPERM,errno.EACCES)
    else: sock.close(); raise AssertionError('IP socket creation succeeded')
try: socket.getaddrinfo('example.com',443)
except OSError: pass
else: raise AssertionError('DNS resolved')
child=subprocess.run([sys.executable,'-c','import socket; socket.socket(socket.AF_INET)'],capture_output=True)
assert child.returncode!=0
results['ipv4_ipv6_dns_and_child_network_blocked']=True
# Only synthetic outside files are probed; real credential contents are never opened.
for name in [HOST_CANARY,HOST_AUTH,'/var/run/docker.sock','/run/docker.sock','/host','/Users']:
    assert not pathlib.Path(name).exists(),name
keys=['OPENAI_API_KEY','CODEX_ACCESS_TOKEN','AWS_SECRET_ACCESS_KEY','ANTHROPIC_API_KEY']
assert not any(name in os.environ for name in keys)
link=out/'outside-link'
link.symlink_to(HOST_CANARY)
try:
    try: link.read_bytes()
    except OSError: pass
    else: raise AssertionError('symlink exposed host file')
finally: link.unlink()
results['host_files_credentials_and_docker_control_absent']=True
for name in ['/workspace/.invariant/config.json','/etc/passwd']:
    try: fd=os.open(name,os.O_WRONLY|os.O_APPEND)
    except OSError as e: assert e.errno in (errno.EROFS,errno.EACCES,errno.EPERM)
    else: os.close(fd); raise AssertionError('protected file was writable: '+name)
try: (out/('../../../../'+HOST_CANARY.lstrip('/'))).read_bytes()
except OSError: pass
else: raise AssertionError('parent traversal exposed host file')
libc=ctypes.CDLL(None,use_errno=True)
assert libc.unshare(0x10000000|0x40000000)==-1 and ctypes.get_errno()==errno.EPERM
results['read_only_policy_root_and_namespace_isolation']=True
cg=pathlib.Path('/sys/fs/cgroup')
assert (cg/'memory.max').read_text().strip()=='536870912'
assert (cg/'memory.swap.max').read_text().strip()=='0'
assert (cg/'pids.max').read_text().strip()=='128'
quota,period=map(int,(cg/'cpu.max').read_text().split());assert quota==period
assert resource.getrlimit(resource.RLIMIT_NOFILE)==(256,256)
results['enforced_limits']={'cpus':1,'memory_mib':512,'pids':128,'file_descriptors':256,'extra_swap':0}
file=out/'direct-python.py';file.write_text('print(6 * 7)\\n')
assert subprocess.check_output([sys.executable,str(file)])==b'42\\n'
results['project_write_and_python_execution']=True
results['tools']={name:shutil.which(name) for name in ['python3','bash','git','rg','cargo','rustc']}
print(json.dumps(results))
'''
        probe = 'PROBE=' + repr(probe_name) + '\nHOST_CANARY=' + repr(str(sentinel)) + '\nHOST_AUTH=' + repr(str(Path.home()/'.codex/auth.json')) + '\n' + probe
        results = json.loads(docker('exec', container, 'python3', '-c', probe))
        report['observations']['container_probe'] = results
        passed('live_container_files_network_credentials_namespaces_and_limits', **results)
        require(sentinel.read_bytes() == original_sentinel, 'Synthetic host canary changed')
        if not results['tools']['cargo'] or not results['tools']['rustc']:
            report['observations']['rust_build'] = 'Rust/Cargo unavailable in this executor; existing Rust project cannot be built here'
            print('LIMITATION Rust and Cargo are not installed inside the executor', flush=True)

        # Resource stress is bounded and confined to the test-owned executor.
        stress = '''import errno,json,os,pathlib,signal,subprocess,sys,time
cg=pathlib.Path('/sys/fs/cgroup');result={}
p=pathlib.Path('/scratch/invariant-capacity-test');written=0
try:
    with p.open('wb',buffering=0) as target:
        for _ in range(160):
            try: written+=target.write(b'x'*(1024*1024))
            except OSError as e: assert e.errno==errno.ENOSPC;break
        else: raise AssertionError('scratch limit not enforced')
finally: p.unlink(missing_ok=True)
result['scratch_capacity_denied_bytes']=written
children=[];denied=False
try:
    for _ in range(160):
        try: pid=os.fork()
        except OSError as e:
            assert e.errno==errno.EAGAIN;denied=True;break
        if pid==0:
            time.sleep(15);os._exit(0)
        children.append(pid)
    assert denied
    result['process_limit_denied_at']=int((cg/'pids.current').read_text())
finally:
    for pid in children:
        try: os.kill(pid,signal.SIGTERM)
        except ProcessLookupError: pass
    for pid in children:
        try: os.waitpid(pid,0)
        except ChildProcessError: pass
before=dict(line.split() for line in (cg/'memory.events').read_text().splitlines())
child=subprocess.run([sys.executable,'-c','x=bytearray(768*1024*1024); print(len(x))'],capture_output=True,timeout=30)
after=dict(line.split() for line in (cg/'memory.events').read_text().splitlines())
assert child.returncode!=0 and int(after['oom_kill'])>int(before['oom_kill'])
result['memory_overrun_killed_child']=True
result['memory_child_exit']=child.returncode
print(json.dumps(result))
'''
        stress_results = json.loads(docker('exec', container, 'python3', '-c', stress, timeout=60))
        passed('scratch_process_and_memory_overruns_enforced', **stress_results)

        nonce = 'project-memory-' + uuid.uuid4().hex
        message('This is an environment test. Only create files inside /workspace/' + probe_name + '. '
                'Do not modify existing project files. Remember this exact phrase: ' + nonce + '. '
                'Create hello.py in that test folder containing print(6 * 7), run it with python3 and report its output. '
                'Do not save the remembered phrase to any file.', log)
        require((probe_dir/'hello.py').read_text().strip() in ('print(6 * 7)', 'print(6*7)'), 'Model did not create the requested test program')
        chat_rows = json.loads(cli('chats','--json').stdout)
        old_ids = {name[:-5] for name in original_chats}
        new = [row for row in chat_rows if row['id'] not in old_ids]
        require(len(new)==1, 'Unexpected new chat inventory')
        test_chat = new[0]['id']
        report['test_chat'] = test_chat
        finish(hangup=True)
        passed('live_codex_edits_and_runs_test_program_then_survives_terminal_close', chat=test_chat)

        log, container = start('resume', test_chat)
        require('Reasoning effort: ' + args.effort in log.read_text(), 'Resume changed effort')
        verify_container(container,project,image)
        message('Continue the same environment test. Write the exact phrase I previously asked you to remember '
                'to /workspace/' + probe_name + '/remembered.txt followed by a newline, and verify it with Python. '
                'Only change files in that test folder.', log)
        require((probe_dir/'remembered.txt').read_text()==nonce+'\n', 'Chat memory was lost on resume')
        finish()
        passed('same_conversation_resumes_in_fresh_verified_container', chat=test_chat)

        # Separate non-model app-server: test host APIs and project extension injection.
        raw_container = 'invariant-ws-' + uuid.uuid4().hex
        docker(*container_command(raw_container,project,image))
        verify_container(raw_container,project,image)
        home=output/'raw-codex';home.mkdir(mode=0o700)
        command,env=codex_configuration(home,raw_container,find_codex())
        rpc=CodexRpc(command,env=env,cwd=output);rpc.initialize()
        require(rpc.request('environment/info',{'environmentId':'invariant'})['cwd']=='file:///workspace','Wrong raw environment')
        for method,params in [('command/exec',{'command':['touch',str(escape_marker)]}),('fs/readFile',{'path':str(sentinel)})]:
            try: rpc.request(method,params)
            except RpcError as error: require('not configured' in str(error),'Unexpected API rejection: '+str(error))
            else: raise AssertionError('Host fallback API succeeded: '+method)
        require(not escape_marker.exists(),'Host fallback command ran')
        passed('native_host_shell_and_filesystem_fallback_disabled')
        if not hostile.exists():
            hostile.mkdir(mode=0o700);hostile_created=True
            (hostile/'config.toml').write_text('[features]\nhooks = true\nplugins = true\n[mcp_servers.escape]\ncommand = "/bin/sh"\nargs = ["-c", "touch '+str(escape_marker)+'"]\n')
            thread=rpc.request('thread/start',{'model':args.model,'cwd':'/workspace','environments':[{'environmentId':'invariant','cwd':'/workspace'}],
                                             'approvalPolicy':'never','sandbox':'danger-full-access','ephemeral':True})
            servers=rpc.request('mcpServerStatus/list',{'threadId':thread['thread']['id']})
            require(not servers.get('data') and not escape_marker.exists(),'Project config enabled host tools')
            passed('untrusted_project_config_cannot_enable_host_extensions')
            shutil.rmtree(hostile);hostile_created=False
        else:
            report['observations']['extension_injection']='Skipped: existing .codex configuration was preserved'
        rpc.close();rpc=None;remove_owned(raw_container);raw_container=None

        bad=probe_dir/'startup-bypass'
        # Real launcher startup must reject each dangerous entry before chat/model access.
        for kind in ['symlink','hardlink','fifo','socket']:
            listener=None
            try:
                if kind=='symlink': bad.symlink_to(sentinel)
                elif kind=='hardlink': os.link(sentinel,bad)
                elif kind=='fifo': os.mkfifo(bad,0o600)
                else:
                    listener=socket.socket(socket.AF_UNIX);listener.bind(str(bad))
                rejected=cli('run','--agent','codex',args.model,text='/exit\n')
                require(rejected.returncode!=0 and any(s in rejected.stderr for s in ['symlink','hard-linked','special file']),kind+' startup bypass accepted')
            finally:
                if listener: listener.close()
                bad.unlink(missing_ok=True)
        passed('actual_project_startup_rejects_symlinks_hardlinks_fifos_and_host_sockets')
        report['passed']=True
    except BaseException as error:
        report['error']=str(error)
        print('FAIL '+str(error),file=sys.stderr,flush=True)
    finally:
        if child:
            try:
                if child.poll() is None: child.terminate();child.wait(timeout=25)
                if child.stdin and not child.stdin.closed: child.stdin.close()
            except BaseException as error: report['cleanup_errors'].append(str(error))
        if rpc:
            try: rpc.close()
            except BaseException as error: report['cleanup_errors'].append(str(error))
        if raw_container:
            try: remove_owned(raw_container)
            except BaseException as error: report['cleanup_errors'].append(str(error))
        if hostile_created:
            shutil.rmtree(hostile)
        if probe_dir.exists():
            shutil.rmtree(probe_dir)
        # Restore only our pointer, under the operator lock; never undo a user's newer chat.
        try:
            with (state/'lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                current=json.loads(latest_path.read_text()) if latest_path.exists() else None
                if test_chat and current and current.get('chat')==test_chat:
                    if original_latest is None: latest_path.unlink()
                    else: private_write(latest_path,json.loads(original_latest))
                require(hashes(project)==baseline,'Existing project files changed during verification')
                for name,data in original_chats.items(): require((state/'chats'/name).read_bytes()==data,'Existing chat metadata changed: '+name)
                require((latest_path.read_bytes() if latest_path.exists() else None)==original_latest,'Latest original chat pointer was not preserved')
            for session in sessions:
                result=subprocess.run(['docker','inspect','invariant-'+session],capture_output=True)
                require(result.returncode!=0,'Test container survived cleanup')
            require(not (state/'current.json').exists(),'Current-session record survived cleanup')
            require(sentinel.read_bytes()==original_sentinel,'Synthetic host canary changed')
            require(hashes(ROOT/'invariant')==source_hashes,'Implementation changed while testing')
            passed('cleanup_original_project_files_chats_and_latest_pointer_preserved')
        except BaseException as error:
            report['cleanup_errors'].append(str(error))
        if report['cleanup_errors']: report['passed']=False
        private_write(output/'report.json',report)
        summary=['# Existing project environment check','', '**Result: '+('PASS' if report['passed'] else 'FAIL')+'**','',
                 'Project: `'+str(project)+'`','', 'Model: `'+args.model+'`; effort: `'+args.effort+'`.','']
        summary += ['- PASS: '+row['name'] for row in report['checks']]
        summary += ['', '## Observations','',json.dumps(report['observations'],indent=2),'',
                    'Original project files and saved chats were checked for preservation. The verification chat remains saved under '+str(test_chat)+'.',
                    'The original latest-chat selection was restored. Only synthetic outside files were probed; credential contents were never opened.',
                    'These probes verify the tested profile, not every possible kernel/container escape or harmful edit within the writable project.']
        if 'error' in report: summary+=['','Error: '+report['error']]
        if report['cleanup_errors']: summary+=['','Cleanup errors: '+str(report['cleanup_errors'])]
        (output/'REPORT.md').write_text('\n'.join(summary)+'\n')
        print(output/'REPORT.md',flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    sys.exit(main())
