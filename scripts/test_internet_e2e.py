#!/usr/bin/env python3
"""Verify opt-in networking and package installs; --live also exercises real Codex turns."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from invariant.workspace import docker, init, private_write, remove_owned, state_for, container_command, verify_container, IMAGE, PROXY_VARIABLES

from invariant.dependencies import find_docker, docker_env
from invariant.policy import normalize, PROFILE
from invariant.packages import USER_BASE


def sources():
    paths = [p for p in (ROOT / 'invariant').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths += [Path(__file__), ROOT / 'docs/LEGACY_MVP_CONTRACT.md', ROOT / 'scripts/invariant']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', metavar='MODEL', help='use signed-in Codex allowance to install a package and resume offline')
    parser.add_argument('--effort', default='medium')
    args = parser.parse_args()
    output = ROOT / '.invariant/internet-checks' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
    project = output / 'project';project.mkdir(parents=True, mode=0o700)
    init(project, automatic=False)
    report = {'passed': False, 'checks': [], 'source_sha256': sources(), 'project': str(project),
              'live_model': args.live, 'cleanup_errors': []}
    child = None
    containers = []
    sequence = 0
    chat_id = None
    pip_args = ['python3', '-m', 'pip', 'install', '--no-cache-dir', '--disable-pip-version-check',
                '--index-url', 'https://pypi.org/simple']

    def require(condition, message):
        if not condition: raise AssertionError(message)

    def passed(name):
        report['checks'].append(name);print('PASS ' + name, flush=True)

    def cli(*commands):
        value = subprocess.run([str(ROOT / 'scripts/invariant'), *commands], cwd=project, capture_output=True, text=True, timeout=30)
        require(value.returncode == 0, value.stderr + value.stdout)
        return value.stdout

    def wait(condition, log, seconds=210):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if condition(): return
            if child.poll() is not None: raise AssertionError('Launcher exited: ' + log.read_text()[-4000:])
            time.sleep(.2)
        raise AssertionError('Launcher timed out: ' + log.read_text()[-4000:])

    def start(label, *, resume=False):
        nonlocal child, sequence
        sequence += 1
        log = output / (str(sequence) + '-' + label + '.log')
        command = ['resume', chat_id] if resume else ['run', '--agent', 'codex', args.live or 'gpt-6.1-sol']
        command += ['--duration', '240']
        if args.live: command += ['--effort', args.effort]
        with log.open('w') as stream:
            child = subprocess.Popen([str(ROOT / 'scripts/invariant'), *command], cwd=project,
                                     stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT, text=True)
        wait(lambda: '\nYou> ' in log.read_text(), log, seconds=80)
        return log

    def message(text, log):
        before = log.read_text().count('\nYou> ')
        child.stdin.write(text + '\n');child.stdin.flush()
        wait(lambda: log.read_text().count('\nYou> ') > before, log)

    def close():
        nonlocal child
        child.stdin.write('/exit\n');child.stdin.flush()
        require(child.wait(timeout=25) == 0, 'Launcher shutdown failed')
        child.stdin.close();child = None
        require(not (state_for(project) / 'current.json').exists(), 'Active metadata survived clean shutdown')

    def inspect(label):
        value = json.loads(cli('guardrails', '--json'))
        require(value['verified'], str(value['issues']))
        name = 'invariant-' + value['session']
        if name not in containers: containers.append(name)
        private_write(output / (label + '.json'), value)
        return value, name

    def user_install(name, label, *, temporary=False):
        prefix = ['env', 'PYTHONUSERBASE=/scratch/.local'] if temporary else []
        text = docker('exec', name, *prefix, *pip_args, '--user', 'packaging==24.2', timeout=90)
        (output / (label + '-pip.txt')).write_text(text)
        version = docker('exec', name, *prefix, 'python3', '-c', "import packaging;assert packaging.__version__=='24.2';print(packaging.__file__)")
        require(version.startswith('/scratch/.local/' if temporary else USER_BASE + '/'), 'Unexpected Python package location: ' + version)

    def use_saved_packages(name):
        result = docker('exec', name, 'bash', '-lc',
                       "cd /scratch && python3 -c \"import packaging,pyfiglet;assert packaging.__version__=='24.2';assert pyfiglet.__version__=='1.0.2'\" && pyfiglet --version")
        require(result == '1.0.2', 'Saved imports/console tool failed in a login shell: ' + result)

    try:
        # Only synthetic credentials are used. Never read the operator's config.
        with tempfile.TemporaryDirectory(dir=output) as folder:
            client = Path(folder)
            (client / 'config.json').write_text(json.dumps({'proxies': {'default': {
                'httpProxy': 'http://synthetic-user:synthetic-token@proxy.invalid:3128',
                'httpsProxy': 'http://synthetic-user:synthetic-token@proxy.invalid:3128',
                'allProxy': 'http://synthetic-user:synthetic-token@proxy.invalid:3128',
                'ftpProxy': 'http://synthetic-user:synthetic-token@proxy.invalid:3128', 'noProxy': 'synthetic.invalid'}}}))
            endpoint = docker('context', 'inspect', '--format', '{{.Endpoints.docker.Host}}')
            image = docker('image', 'inspect', IMAGE, '--format', '{{.Id}}')
            for mode in ('none', 'internet'):
                name = 'invariant-proxy-check-' + uuid.uuid4().hex
                policy = normalize({**PROFILE, 'command_network': mode})
                try:
                    command = container_command(name, project, image, policy=policy)
                    subprocess.run([find_docker(), '--config', str(client), '--host', endpoint, *command],
                                   env=docker_env(find_docker()), capture_output=True, text=True, check=True, timeout=30)
                    info = json.loads(docker('inspect', name))[0]
                    env = dict(row.split('=', 1) for row in info['Config']['Env'])
                    require(all(env.get(key) == '' for key in PROXY_VARIABLES), 'Saved Docker proxy settings leaked')
                    verify_container(name, project, image, info=info, policy=policy)
                finally: remove_owned(name)
        passed('docker_client_proxy_credentials_not_inherited_in_either_network_mode')
        start('offline')
        value, name = inspect('offline')
        require(value['runtime']['docker']['network'] == 'none'
                and value['runtime']['kernel']['network_sockets'] == {'ipv4': 'denied', 'ipv6': 'denied'}, 'Default network is not offline')
        cli('config', 'set', 'command_network', 'internet')
        value, _ = inspect('pending-internet')
        require(value['configuration_changed'] and value['runtime']['policy']['command_network'] == 'none', 'Setting changed active offline session')
        passed('offline_default_and_internet_change_waits_for_next_launch')
        close()

        log = start('internet')
        value, name = inspect('internet')
        require(value['runtime']['docker']['network'] == 'bridge'
                and value['runtime']['kernel']['network_sockets'] == {'ipv4': 'allowed', 'ipv6': 'allowed'}, 'Internet policy not applied')
        # pip --target moves from tmpfs to the project bind mount. Its copy2
        # fallback needs read-only extended-attribute queries, not xattr writes.
        docker('exec', name, 'python3', '-I', '-c', "from pathlib import Path;import shutil;s=Path('/scratch/copy-source');d=Path('/workspace/copy-target');s.write_text('copy');shutil.copy2(s,d);assert d.read_text()=='copy';s.unlink();d.unlink()")
        passed('package_copy_from_scratch_to_project_preserves_file_metadata')
        user_install(name, 'editable')
        passed('real_dns_https_pypi_user_install_uses_persistent_project_storage')
        if args.live:
            message('Install pyfiglet==1.0.2 using python3 -m pip install --disable-pip-version-check --index-url https://pypi.org/simple pyfiglet==1.0.2. '
                    'Use the default installation location; do not create or activate a virtual environment or override environment variables. '
                    'Then change to /scratch, import pyfiglet and packaging in Python, assert versions 1.0.2 and 24.2, run pyfiglet --version, '
                    'and write the pyfiglet version plus a newline to /workspace/installed-version.txt. '
                    'Run these commands in the isolated workspace. Do not install other packages or merely describe commands.', log)
            require((project / 'installed-version.txt').read_text() == '1.0.2\n', 'Codex did not install and import the package')
            chats = json.loads(cli('chats', '--json'))
            require(len(chats) == 1, 'Live chat not saved')
            chat_id = chats[0]['id'];report['chat'] = chat_id
        else:
            text = docker('exec', name, 'python3', '-m', 'pip', 'install', '--disable-pip-version-check',
                          '--index-url', 'https://pypi.org/simple', 'pyfiglet==1.0.2', timeout=90)
            (output / 'default-pip.txt').write_text(text)
        use_saved_packages(name)
        require((project / '.invariant-deps/cache/pip').is_dir(), 'Pip cache is not in the project')
        require(not (project / '.invariant-deps/cache/pip').is_symlink(), 'Cache is a symlink')
        passed('codex_default_install_import_and_console_tool' if args.live else 'default_install_import_and_console_tool')
        user_install(name, 'temporary', temporary=True)
        passed('explicit_scratch_override_keeps_temporary_installs_available')
        text = docker('exec', name, *pip_args, '--target', '/workspace/.packages', 'packaging==24.2', timeout=90)
        (output / 'legacy-target-pip.txt').write_text(text)
        version = docker('exec', name, 'env', 'PYTHONPATH=/workspace/.packages', 'python3', '-c', "import packaging;assert packaging.__version__=='24.2';print(packaging.__file__)")
        require(version.startswith('/workspace/.packages/'), 'Persistent import did not use project package')
        passed('legacy_explicit_target_packages_remain_usable')
        cli('config', 'set', 'command_network', 'none')
        value, _ = inspect('pending-offline')
        require(value['configuration_changed'] and value['runtime']['policy']['command_network'] == 'internet', 'Setting altered active internet session')
        passed('disabling_internet_is_pending_until_next_run_or_resume')
        close()

        log = start('offline-resume', resume=bool(args.live))
        if args.live: require('Resumed chat: ' + chat_id in log.read_text(), 'Resume changed chat identity')
        value, name = inspect('offline-resume')
        require(value['runtime']['kernel']['network_sockets'] == {'ipv4': 'denied', 'ipv6': 'denied'}, 'Offline policy not restored')
        docker('exec', name, 'env', 'PYTHONPATH=/workspace/.packages', 'python3', '-c', "import pathlib,packaging;assert packaging.__version__=='24.2';assert not pathlib.Path('/scratch/.local').exists()")
        use_saved_packages(name)
        if args.live:
            message('Without installing anything or overriding environment variables, change to /scratch and import the previously installed pyfiglet and packaging; assert versions 1.0.2 and 24.2. '
                    'Run the pyfiglet --version command and write the pyfiglet version plus a newline to /workspace/after-resume.txt. '
                    'Also run a Python check that both IPv4 and IPv6 socket creation fail with EPERM or EACCES. Networking is now disabled. Do not download or reinstall anything.', log)
            require((project / 'after-resume.txt').read_text() == '1.0.2\n', 'Resumed Codex did not use the persistent package offline')
        passed('offline_resume_preserves_project_packages_and_clears_session_installs')
        close()

        cli('config', 'set', 'command_network', 'internet')
        cli('config', 'set', 'workspace_access', 'read-only')
        start('read-only-internet')
        value, name = inspect('read-only-internet')
        require(value['runtime']['kernel']['workspace_read_only'], 'Project is not read-only')
        use_saved_packages(name)
        # An upgrade must fail at the existing read-only project mount.
        failed = False
        try:
            docker('exec', name, *pip_args, '--user', '--force-reinstall', 'packaging==24.2', timeout=90)
        except RuntimeError as error:
            require('Read-only file system' in str(error) or 'Permission denied' in str(error), 'Unexpected installation failure: ' + str(error))
            failed = True
        require(failed, 'Read-only session changed persistent dependencies')
        use_saved_packages(name)
        passed('read_only_session_uses_saved_dependencies_but_rejects_reinstallation')
        user_install(name, 'read-only', temporary=True)
        probe = '''import errno,json,os,socket
assert os.getuid()!=0
assert not os.path.exists('/var/run/docker.sock')
assert not os.path.exists('/Users/nikchin/.codex/auth.json')
assert not any(k in os.environ for k in ('OPENAI_API_KEY','AWS_SECRET_ACCESS_KEY'))
for p in ('/workspace/denied.txt','/workspace/.invariant/denied.txt','/usr/local/lib/denied.txt'):
    try: fd=os.open(p,os.O_CREAT|os.O_WRONLY,0o600)
    except OSError as e: assert e.errno in (errno.EROFS,errno.EACCES),(p,e)
    else: os.close(fd);raise AssertionError('Writable: '+p)
for family,kind in ((socket.AF_PACKET,socket.SOCK_RAW),(socket.AF_INET,socket.SOCK_RAW)):
    try: s=socket.socket(family,kind)
    except OSError as e: assert e.errno in (errno.EPERM,errno.EACCES)
    else: s.close();raise AssertionError('Raw or packet socket allowed')
print(json.dumps({'project_policy_system_writes':'denied','raw_packet_sockets':'denied','root_credentials_docker':'absent'}))'''
        report['boundary_probe'] = json.loads(docker('exec', name, 'python3', '-I', '-c', probe))
        passed('read_only_project_allows_scratch_packages_and_preserves_other_guardrails')
        close()
        # A separate initialized project sees neither installed package. It uses
        # the same image and default environment, but a different project mount.
        other = output / 'other-project';other.mkdir()
        init(other, automatic=False)
        other_name = 'invariant-packages-check-' + uuid.uuid4().hex
        try:
            docker(*container_command(other_name, other, image))
            verify_container(other_name, other, image)
            docker('start', other_name)
            docker('exec', other_name, 'python3', '-c', "import importlib.util;assert importlib.util.find_spec('pyfiglet') is None;assert importlib.util.find_spec('packaging') is None")
        finally:
            remove_owned(other_name)
        passed('separate_project_does_not_inherit_installed_dependencies')
        if args.live:
            records = [json.loads(line) for path in state_for(project).glob('ws-*.jsonl') for line in path.read_text().splitlines()]
            configurations = [r for r in records if r['event'] == 'session_configured' and r.get('chat') == chat_id]
            require(len(configurations) == 2 and sorted(r['resumed'] for r in configurations) == [False, True], 'Missing native resume evidence')
            require(sum(r['event'] == 'turn_completed' and r['status'] == 'completed' for r in records) == 2, 'Live turns incomplete')
            require(sum(r['event'] == 'tool_completed' for r in records) >= 2, 'Model-driven tool execution missing')
        require(sources() == report['source_sha256'], 'Sources changed during verification')
        report['passed'] = True
    except BaseException as error:
        report['error'] = str(error);print('FAIL ' + str(error), file=sys.stderr, flush=True)
    finally:
        if child:
            if child.poll() is None: child.terminate();child.wait(timeout=25)
            child.stdin.close()
        current = state_for(project) / 'current.json'
        if current.exists():
            session = json.loads(current.read_text()).get('session', '')
            if session.startswith('ws-') and len(session) == 35: containers.append('invariant-' + session)
        for name in set(containers):
            try: remove_owned(name)
            except Exception as error: report['cleanup_errors'].append(str(error))
        if report['cleanup_errors']: report['passed'] = False
        private_write(output / 'report.json', report)
        print(output / 'report.json', flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__': sys.exit(main())
