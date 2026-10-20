#!/usr/bin/env python3
"""Exercise per-project policies through real Codex/Docker sessions; no model turns."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from invariant.workspace import docker, init, private_write, remove_owned, state_for


def sources():
    paths = [p for p in (ROOT / 'invariant').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths += [Path(__file__), ROOT / 'docs/LEGACY_MVP_CONTRACT.md', ROOT / 'scripts/invariant']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    output = ROOT / '.invariant/configuration-checks' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
    project = output / 'project';project.mkdir(parents=True, mode=0o700)
    init(project, automatic=False)
    (project / 'canary.txt').write_text('preserve me')
    report = {'passed': False, 'checks': [], 'source_sha256': sources(), 'project': str(project), 'cleanup_errors': []}
    report['authentication'] = 'fixture-only; provider turns forbidden'
    launcher = [sys.executable, '-B', str(ROOT/'scripts/fixture_cli.py')]
    process = None
    containers = []

    def require(condition, message):
        if not condition: raise AssertionError(message)

    def passed(name):
        report['checks'].append(name);print('PASS ' + name, flush=True)

    def cli(*args):
        value = subprocess.run([*launcher, *args], cwd=project,
                               capture_output=True, text=True, timeout=30)
        require(value.returncode == 0, value.stderr + value.stdout)
        return value.stdout

    def start(label, *args):
        log = output / (label + '.log')
        with log.open('w') as stream:
            child = subprocess.Popen([*launcher, 'run', '--agent', 'codex', 'gpt-6.1-sol', *args],
                                     cwd=project, stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT, text=True)
        # Keep ownership for cleanup even if startup raises.
        nonlocal process
        process = child
        end = time.monotonic() + 80
        while time.monotonic() < end:
            if '\nYou> ' in log.read_text(): return child
            if child.poll() is not None: raise AssertionError('Launcher failed: ' + log.read_text()[-3000:])
            time.sleep(.2)
        raise AssertionError('Launcher readiness timed out')

    def inspect(label):
        value = json.loads(cli('guardrails', '--json'))
        require(value['verified'], str(value['issues']))
        name = 'invariant-' + value['session']
        if name not in containers: containers.append(name)
        private_write(output / (label + '.json'), value)
        return value, name

    def close():
        nonlocal process
        process.stdin.write('/exit\n');process.stdin.flush()
        require(process.wait(timeout=25) == 0, 'Workspace did not exit cleanly')
        process.stdin.close();process = None
        require(not (state_for(project) / 'current.json').exists(), 'Active metadata remained')

    try:
        for key, text in [('limits.cpus', '0.5'), ('limits.memory_mib', '768'), ('limits.extra_swap_mib', '64'),
                          ('limits.processes', '192'), ('limits.file_descriptors', '512'), ('limits.scratch_mib', '64'),
                          ('duration_seconds', '180'), ('workspace_access', 'read-only')]:
            cli('config', 'set', key, text)
        selected = json.loads(cli('config', '--json'))
        require(selected['limits']['cpus'] == .5 and selected['workspace_access'] == 'read-only', 'Saved policy differs')
        before = (project / '.invariant/config.json').read_bytes()
        cli('init')
        require((project / '.invariant/config.json').read_bytes() == before, 'Init replaced policy')
        passed('terminal_configuration_and_idempotent_init')

        start('read-only')
        value, name = inspect('read-only')
        kernel = value['runtime']['kernel']
        require(kernel['memory_bytes'] == 768*1024*1024 and kernel['extra_swap_bytes'] == 64*1024*1024
                and kernel['cpus'] == .5 and kernel['processes'] == 192
                and kernel['scratch_bytes'] == 64*1024*1024 and kernel['file_descriptors'] == [512,512], 'Custom resources not applied')
        require(value['runtime']['launcher']['duration_seconds'] == 180, 'Configured duration ignored')
        passed('all_custom_resources_and_default_duration_verified_in_kernel')
        probe = '''import errno,json,os,socket
for p in ['/workspace/canary.txt','/workspace/new.txt','/workspace/.invariant/config.json']:
    try: fd=os.open(p,os.O_WRONLY|os.O_CREAT,0o600)
    except OSError as e: assert e.errno==errno.EROFS,(p,e)
    else: os.close(fd);raise AssertionError('Writable: '+p)
for family in (socket.AF_INET,socket.AF_INET6):
    try: s=socket.socket(family,socket.SOCK_STREAM)
    except OSError as e: assert e.errno in (errno.EPERM,errno.EACCES)
    else: s.close();raise AssertionError('Network socket allowed')
open('/scratch/allowed.txt','w').write('scratch works')
print(json.dumps({'project_writes':'EROFS','policy_writes':'EROFS','network':'denied','scratch':'writable'}))'''
        report['read_only_probe'] = json.loads(docker('exec', name, '/usr/local/bin/python3', '-I', '-c', probe))
        passed('project_and_policy_writes_denied_network_denied_scratch_allowed')

        cli('config', 'set', 'limits.memory_mib', '1024')
        cli('config', 'set', 'workspace_access', 'read-write')
        cli('config', 'set', 'limits.cpus', '1.07')
        changed, _ = inspect('pending')
        require(changed['configuration_changed'] and changed['runtime']['kernel']['workspace_read_only']
                and changed['runtime']['kernel']['memory_bytes'] == 768*1024*1024
                and changed['configured']['memory_bytes'] == 1024*1024*1024, 'Pending config changed running authority or caused false mismatch')
        passed('running_policy_immutable_pending_configuration_reported')
        before = (project / '.invariant/config.json').read_bytes()
        for key, text in [('command_network','host'), ('limits.memory_mib','0'), ('limits.unknown','99')]:
            invalid = subprocess.run([*launcher, 'config', 'set', key, text], cwd=project, capture_output=True, text=True, timeout=10)
            require(invalid.returncode == 1 and (project / '.invariant/config.json').read_bytes() == before, 'Invalid config accepted or previous file changed')
        passed('unsafe_and_invalid_changes_rejected_without_replacing_policy')
        close()
        require((project / 'canary.txt').read_text() == 'preserve me' and not (project / 'new.txt').exists(), 'Readonly project changed')

        start('read-write', '--duration', '210')
        value, name = inspect('read-write')
        require(value['runtime']['kernel']['cpus'] == 1.07
                and value['runtime']['kernel']['memory_bytes'] == 1024*1024*1024
                and not value['runtime']['kernel']['workspace_read_only']
                and value['runtime']['launcher']['duration_seconds'] == 210
                and not value['configuration_changed'], 'Next launch failed to apply changed policy/explicit duration')
        docker('exec', name, '/usr/local/bin/python3', '-I', '-c', "from pathlib import Path;p=Path('/workspace/allowed.txt');p.write_text('editable');assert p.read_text()=='editable';p.unlink()")
        passed('next_launch_applies_new_limits_editable_access_and_duration_override')
        close()
        require(sources() == report['source_sha256'], 'Sources changed during verification')
        report['passed'] = True
    except BaseException as error:
        report['error'] = str(error);print('FAIL ' + str(error), file=sys.stderr, flush=True)
    finally:
        if process:
            if process.poll() is None: process.terminate();process.wait(timeout=25)
            process.stdin.close()
        # Include containers created before readiness failed, verifying ownership in remove_owned.
        current = state_for(project) / 'current.json'
        if current.exists():
            session = json.loads(current.read_text()).get('session', '')
            if session.startswith('ws-') and len(session) == 35: containers.append('invariant-' + session)
        for container in set(containers):
            try: remove_owned(container)
            except Exception as error: report['cleanup_errors'].append(str(error))
        if report['cleanup_errors']: report['passed'] = False
        private_write(output / 'report.json', report)
        print(output / 'report.json', flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__': sys.exit(main())
