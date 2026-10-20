#!/usr/bin/env python3
"""Live guardrails display, drift detection and stale-session reporting; no model turns."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from invariant.workspace import docker, init, private_write, state_for


def sources():
    paths = [p for p in (ROOT / 'invariant').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths += [Path(__file__), ROOT / 'docs/LEGACY_MVP_CONTRACT.md', ROOT / 'scripts/invariant']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    output = ROOT / '.invariant/guardrails-checks' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
    project = output / 'project'
    project.mkdir(parents=True, mode=0o700)
    init(project, automatic=False)
    report = {'passed': False, 'checks': [], 'source_sha256': sources(), 'project': str(project), 'cleanup_errors': []}
    report['authentication'] = 'fixture-only; provider turns forbidden'
    launcher = [sys.executable, '-B', str(ROOT/'scripts/fixture_cli.py')]
    process = None
    container = None
    baseline = (project / '.invariant/config.json').read_bytes()

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def passed(name):
        report['checks'].append(name)
        print('PASS ' + name, flush=True)

    def cli(*args, text=None):
        return subprocess.run([*launcher, *args], cwd=project, input=text,
                              capture_output=True, text=True, timeout=60)

    try:
        inactive = cli('guardrails', '--json')
        require(inactive.returncode == 0, inactive.stderr)
        value = json.loads(inactive.stdout)
        require(value['status'] == 'inactive' and not value['verified'] and value['runtime'] is None, 'Inactive defaults claimed verification')
        passed('inactive_policy_explicitly_unverified')
        log = output / 'launcher.log'
        with log.open('w') as stream:
            process = subprocess.Popen([*launcher, 'run', '--agent', 'codex', 'gpt-6.1-sol', '--duration', '180'],
                                       cwd=project, stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT, text=True)
        end = time.monotonic() + 75
        while time.monotonic() < end:
            if '\nYou> ' in log.read_text():
                break
            if process.poll() is not None:
                raise AssertionError('Launcher failed: ' + log.read_text()[-3000:])
            time.sleep(.2)
        else:
            raise AssertionError('Launcher readiness timed out')
        actual = cli('guardrails', '--json')
        require(actual.returncode == 0, actual.stderr + actual.stdout)
        value = json.loads(actual.stdout)
        require(value['verified'] and value['status'] == 'active', str(value['issues']))
        require(value['runtime']['launcher']['duration_seconds'] == 180, 'Custom duration lost')
        require(value['runtime']['kernel']['network_sockets'] == {'ipv4': 'denied', 'ipv6': 'denied'}, 'Network guardrail not measured')
        private_write(output / 'active.json', value)
        container = 'invariant-' + value['session']
        text = cli('guardrails')
        (output / 'active.txt').write_text(text.stdout)
        require(text.returncode == 0 and '512 MiB' in text.stdout and 'verified now' in text.stdout, 'Human display failed: ' + text.stderr + text.stdout)
        passed('active_docker_kernel_and_custom_duration_verified')

        # Only this invocation's disposable executor is changed, never a user project.
        docker('update', '--memory', '768m', '--memory-swap', '768m', container)
        drift = cli('guardrails', '--json')
        require(drift.returncode == 1, 'Changed limit returned success')
        value = json.loads(drift.stdout)
        require(value['status'] == 'mismatch' and not value['verified'], 'Changed limit was called verified')
        require(value['runtime']['docker']['memory_bytes'] == 805306368 and value['runtime']['kernel']['memory_bytes'] == 805306368, 'Actual changed memory was hidden')
        private_write(output / 'mismatch.json', value)
        text = cli('guardrails')
        (output / 'mismatch.txt').write_text(text.stdout)
        require(text.returncode == 1 and '768 MiB' in text.stdout and 'NOT verified' in text.stdout, 'Human mismatch display hid actual limit: ' + text.stderr + text.stdout)
        passed('real_docker_limit_drift_detected_and_actual_value_shown')
        docker('update', '--memory', '512m', '--memory-swap', '512m', container)
        restored = cli('guardrails', '--json')
        require(restored.returncode == 0 and json.loads(restored.stdout)['verified'], 'Restored limit did not verify')
        passed('restored_profile_verifies_again')

        process.kill()
        process.wait(timeout=10)
        process.stdin.close()
        process = None
        current_path = state_for(project) / 'current.json'
        before = current_path.read_bytes()
        stale = cli('guardrails', '--json')
        require(stale.returncode == 1, 'Stale session returned success')
        value = json.loads(stale.stdout)
        require(value['status'] == 'unverified' and not value['verified'], 'Stale session claimed verification')
        require(current_path.read_bytes() == before, 'Inspection changed recovery metadata')
        private_write(output / 'stale.json', value)
        passed('killed_launcher_is_unverified_and_read_only')
        recovery = cli('run', '--agent', 'codex', 'gpt-6.1-sol', text='/exit\n')
        require(recovery.returncode == 0, recovery.stderr)
        require(not current_path.exists(), 'Recovery left active record')
        final = cli('guardrails', '--json')
        require(final.returncode == 0 and json.loads(final.stdout)['status'] == 'inactive', 'Final state not inactive')
        require((project / '.invariant/config.json').read_bytes() == baseline, 'Policy file changed')
        passed('normal_recovery_and_policy_preservation')
        require(sources() == report['source_sha256'], 'Source changed during verification')
        report['passed'] = True
    except BaseException as error:
        report['error'] = str(error)
        print('FAIL ' + str(error), file=sys.stderr, flush=True)
    finally:
        if process:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=25)
            process.stdin.close()
        if container:
            from invariant.workspace import remove_owned
            try:
                remove_owned(container)
            except Exception as error:
                report['cleanup_errors'].append(str(error))
        if report['cleanup_errors']:
            report['passed'] = False
        private_write(output / 'report.json', report)
        print(output / 'report.json', flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
