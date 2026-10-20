import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from invariant import cli, guardrails, workspace
from invariant.packages import ENVIRONMENT
from invariant.policy import LIMITS, PROFILE, scratch_options

SESSION = 'ws-' + '1' * 32
NAME = 'invariant-' + SESSION
IMAGE = 'sha256:expected'


class GuardrailsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve() / 'project'
        self.project.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.init(self.project, automatic=False)
        self.state = self.project.parent / 'state'
        self.state.mkdir()
        self.state_patch = patch.object(guardrails, 'state_for', return_value=self.state)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def active(self, duration=120):
        current = {'session': SESSION, 'pid': 123}
        if duration is not None:
            current['duration_seconds'] = duration
        workspace.private_write(self.state / 'current.json', current)
        record = {'event': 'session_start', 'session': SESSION, 'project': str(self.project), 'profile': PROFILE, 'image': IMAGE}
        (self.state / (SESSION + '.jsonl')).write_text(json.dumps(record) + '\n')
        home = self.state / 'codex'
        home.mkdir()
        (home / 'environments.toml').write_text('default="invariant"\ninclude_local=false\n[[environments]]\nid="invariant"\nprogram="/usr/bin/docker"\nargs=' + json.dumps(['start', '--attach', '--interactive', NAME]) + '\n')
        (home / 'config.toml').write_text('web_search="disabled"\n[shell_environment_policy.set]\n' +
                                        ''.join(key + '=' + json.dumps(value) + '\n' for key, value in ENVIRONMENT.items()))

    def info(self):
        return {'NetworkSettings': {'Networks': {'none': {}}}, 'Id': 'immutable-container-id', 'Image': IMAGE, 'State': {'Running': True},
                'Config': {'User': f'{os.getuid()}:{os.getgid()}', 'Env': [key + '=' + value for key, value in ENVIRONMENT.items()],
                           'Labels': {'io.invariant.role': 'codex-workspace', 'io.invariant.session': NAME}},
                'Mounts': [{'Source': str(self.project), 'Destination': '/workspace', 'Type': 'bind', 'RW': True},
                           {'Source': str(self.project / '.invariant'), 'Destination': '/workspace/.invariant', 'Type': 'bind', 'RW': False}],
                'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False, 'NanoCpus': 1000000000,
                               'Memory': LIMITS['memory_bytes'], 'MemorySwap': LIMITS['memory_bytes'], 'PidsLimit': LIMITS['processes'],
                               'PublishAllPorts': False, 'CapAdd': None, 'CapDrop': ['ALL'], 'Binds': None, 'Devices': [], 'PortBindings': {},
                               'PidMode': '', 'IpcMode': 'private', 'Init': True, 'Tmpfs': {'/scratch': scratch_options()},
                               'Ulimits': [{'Name': 'nofile', 'Hard': 256, 'Soft': 256}], 'RestartPolicy': {'Name': 'no'},
                               'SecurityOpt': ['no-new-privileges', 'seccomp=' + (workspace.ASSETS / 'seccomp.json').read_text()]}}

    def kernel(self):
        return {**LIMITS, 'file_descriptors': [256, 256], 'uid': os.getuid(), 'capabilities_effective': 0,
                'capabilities_bounding': 0, 'no_new_privileges': True, 'seccomp_filter': True, 'root_read_only': True,
                'policy_read_only': True, 'workspace_read_only': False, 'network_sockets': {'ipv4': 'denied', 'ipv6': 'denied'}}

    def inspect(self, info=None, kernel=None, after_exec=None):
        info = self.info() if info is None else info
        kernel = self.kernel() if kernel is None else kernel
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if args[0] == 'inspect':
                return json.dumps([info])
            self.assertEqual(args[:5], ('exec', info['Id'], '/usr/local/bin/python3', '-I', '-c'))
            if after_exec:
                after_exec()
            return json.dumps(kernel)
        with patch.object(guardrails, 'operator', return_value={'ready': True, 'running': True}), patch.object(guardrails, 'docker', side_effect=docker):
            report = guardrails.inspect(self.project)
        return report, calls

    def test_inactive_project_reports_configured_values_without_docker_or_writes(self):
        before = list(self.state.iterdir())
        with patch.object(guardrails, 'docker') as docker, patch.object(guardrails, 'operator') as operator:
            report = guardrails.inspect(self.project)
        self.assertEqual(report['status'], 'inactive')
        self.assertFalse(report['verified'])
        self.assertEqual(report['configured']['memory_bytes'], 536870912)
        docker.assert_not_called()
        operator.assert_not_called()
        self.assertEqual(list(self.state.iterdir()), before)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            guardrails.render(report)
        self.assertIn('not live measurements', output.getvalue())
        self.assertIn('Project disk quota: none', output.getvalue())

    def test_active_runtime_uses_docker_and_kernel_not_static_table(self):
        self.active()
        before = (self.state / 'current.json').read_bytes()
        report, calls = self.inspect()
        self.assertTrue(report['verified'], report['issues'])
        self.assertEqual(report['status'], 'active')
        self.assertEqual(report['runtime']['kernel']['file_descriptors'], [256, 256])
        self.assertEqual(report['runtime']['launcher']['duration_seconds'], 120)
        self.assertEqual(len(calls), 3)
        self.assertEqual((self.state / 'current.json').read_bytes(), before)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            guardrails.render(report)
        self.assertIn('120 seconds', output.getvalue())
        self.assertIn('verified now', output.getvalue())

    def test_changed_project_config_is_next_launch_policy_not_runtime_drift(self):
        from invariant.configuration import set_value
        self.active()
        set_value(self.project, 'limits.memory_mib', '1024')
        set_value(self.project, 'workspace_access', 'read-only')
        report, _ = self.inspect()
        self.assertTrue(report['verified'], report['issues'])
        self.assertTrue(report['configuration_changed'])
        self.assertEqual(report['configured']['memory_bytes'], 1073741824)
        self.assertEqual(report['runtime']['kernel']['memory_bytes'], LIMITS['memory_bytes'])
        self.assertEqual(report['runtime']['policy']['workspace_access'], 'read-write')
        output = io.StringIO()
        with contextlib.redirect_stdout(output): guardrails.render(report)
        self.assertIn('running session keeps its recorded policy', output.getvalue())

    def test_custom_recorded_policy_is_verified_and_mount_drift_is_detected(self):
        from invariant.policy import normalize
        self.active()
        selected = normalize({**PROFILE, 'workspace_access': 'read-only', 'limits': {'memory_mib': 768}})
        record = {'event': 'session_start', 'session': SESSION, 'project': str(self.project), 'profile': selected, 'image': IMAGE}
        (self.state / (SESSION + '.jsonl')).write_text(json.dumps(record) + '\n')
        info, kernel = self.info(), self.kernel()
        info['HostConfig']['Memory'] = info['HostConfig']['MemorySwap'] = 768 * 1024 * 1024
        info['Mounts'][0]['RW'] = False
        kernel.update(memory_bytes=768 * 1024 * 1024, workspace_read_only=True)
        report, _ = self.inspect(info, kernel)
        self.assertTrue(report['verified'], report['issues'])
        info['Mounts'][0]['RW'] = True
        kernel['workspace_read_only'] = False
        report, _ = self.inspect(info, kernel)
        self.assertEqual(report['status'], 'mismatch')
        self.assertIn('Kernel workspace_read_only', '\n'.join(report['issues']))

    def test_fractional_cpu_uses_exact_docker_nanocpus(self):
        from invariant.policy import normalize
        self.active()
        selected = normalize({**PROFILE, 'limits': {'cpus': 1.07}})
        record = {'event': 'session_start', 'session': SESSION, 'project': str(self.project), 'profile': selected, 'image': IMAGE}
        (self.state / (SESSION + '.jsonl')).write_text(json.dumps(record) + '\n')
        info, kernel = self.info(), self.kernel()
        info['HostConfig']['NanoCpus'] = 1070000000
        kernel['cpus'] = 1.07
        report, _ = self.inspect(info, kernel)
        self.assertTrue(report['verified'], report['issues'])

    def test_internet_policy_verifies_expected_network_and_flags_offline_seccomp_or_extra_networks(self):
        from invariant.configuration import set_value
        self.active()
        selected = set_value(self.project, 'command_network', 'internet')
        record = {'event': 'session_start', 'session': SESSION, 'project': str(self.project), 'profile': selected, 'image': IMAGE}
        (self.state / (SESSION + '.jsonl')).write_text(json.dumps(record) + '\n')
        info, kernel = self.info(), self.kernel()
        info['HostConfig']['NetworkMode'] = 'bridge'
        info['NetworkSettings']['Networks'] = {'bridge': {}}
        info['HostConfig']['SecurityOpt'][1] = 'seccomp=' + (workspace.ASSETS / 'seccomp-internet.json').read_text()
        kernel['network_sockets'] = {'ipv4': 'allowed', 'ipv6': 'allowed'}
        report, _ = self.inspect(info, kernel)
        self.assertTrue(report['verified'], report['issues'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output): guardrails.render(report)
        self.assertIn('reachability and package registries are not tested', output.getvalue())
        set_value(self.project, 'command_network', 'none')
        report, _ = self.inspect(info, kernel)
        self.assertTrue(report['verified'], report['issues'])
        self.assertTrue(report['configuration_changed'])
        for mutate in (lambda: info['NetworkSettings']['Networks'].update(extra={}),
                       lambda: info['HostConfig']['SecurityOpt'].__setitem__(1, 'seccomp=' + (workspace.ASSETS / 'seccomp.json').read_text())):
            mutate()
            report, _ = self.inspect(info, kernel)
            self.assertEqual(report['status'], 'mismatch')
        kernel['network_sockets'] = {'ipv4': 'denied', 'ipv6': 'denied'}
        report, _ = self.inspect(info, kernel)
        self.assertIn('Kernel network_sockets', '\n'.join(report['issues']))

    def test_extra_network_added_during_inspection_invalidates_snapshot(self):
        self.active()
        info = self.info()
        report, _ = self.inspect(info, after_exec=lambda: info['NetworkSettings']['Networks'].update(extra={}))
        self.assertFalse(report['verified'])
        self.assertIn('changed or stopped', '\n'.join(report['issues']))

    def test_proxy_credentials_are_rejected_and_never_echoed_in_report(self):
        self.active()
        info = self.info()
        info['Config']['Env'] = ['HTTP_PROXY=http://synthetic-user:synthetic-token@proxy.invalid']
        report, _ = self.inspect(info)
        self.assertEqual(report['status'], 'mismatch')
        self.assertEqual(report['runtime']['docker']['proxy_environment_nonempty'], ['HTTP_PROXY'])
        self.assertNotIn('synthetic-token', json.dumps(report))

    def test_mount_order_changes_are_not_configuration_changes(self):
        self.active()
        info = self.info()
        report, _ = self.inspect(info, after_exec=lambda: info['Mounts'].reverse())
        self.assertTrue(report['verified'], report['issues'])

    def test_dependency_storage_environment_must_match_in_container_and_launcher(self):
        self.active()
        info = self.info()
        info['Config']['Env'] = [row for row in info['Config']['Env'] if not row.startswith('PYTHONUSERBASE=')]
        report, _ = self.inspect(info)
        self.assertFalse(report['verified'])
        self.assertIn('dependency environment', '\n'.join(report['issues']))
        (self.state / 'codex/config.toml').write_text('web_search="disabled"\n')
        report, _ = self.inspect()
        self.assertFalse(report['verified'])
        self.assertFalse(report['runtime']['launcher']['project_dependencies_configured'])

    def test_modified_docker_and_kernel_limits_display_actual_mismatch(self):
        self.active()
        info, kernel = self.info(), self.kernel()
        info['HostConfig']['Memory'] = 1073741824
        info['HostConfig']['MemorySwap'] = 1073741824
        kernel['memory_bytes'] = 1073741824
        report, _ = self.inspect(info, kernel)
        self.assertEqual(report['status'], 'mismatch')
        self.assertFalse(report['verified'])
        self.assertEqual(report['runtime']['docker']['memory_bytes'], 1073741824)
        self.assertIn('Kernel memory_bytes', '\n'.join(report['issues']))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            guardrails.render(report)
        self.assertIn('Memory: 1024 MiB', output.getvalue())
        self.assertIn('NOT verified', output.getvalue())

    def test_altered_network_privileges_or_mount_policy_cannot_be_verified(self):
        self.active()
        for key, value in [('network_sockets', {'ipv4': 'allowed', 'ipv6': 'denied'}), ('no_new_privileges', False), ('policy_read_only', False)]:
            kernel = self.kernel();kernel[key] = value
            with self.subTest(key=key):
                report, _ = self.inspect(kernel=kernel)
                self.assertFalse(report['verified'])
                self.assertEqual(report['status'], 'mismatch')

    def test_foreign_container_is_never_probed(self):
        self.active()
        info = self.info();info['Mounts'][0]['Source'] = '/other/project'
        report, calls = self.inspect(info)
        self.assertFalse(report['verified'])
        self.assertEqual(len(calls), 1)
        self.assertIn('does not belong', report['issues'][0])

    def test_wrong_image_does_not_pass_using_its_own_image_as_expected(self):
        self.active()
        info = self.info();info['Image'] = 'sha256:different'
        report, _ = self.inspect(info)
        self.assertEqual(report['status'], 'mismatch')
        self.assertFalse(report['verified'])

    def test_failed_docker_or_stale_controller_never_claims_enforcement(self):
        self.active()
        for exception in (RuntimeError('Docker unavailable'), OSError('stale socket')):
            with self.subTest(exception=exception), patch.object(guardrails, 'operator', side_effect=exception):
                report = guardrails.inspect(self.project)
                self.assertFalse(report['verified'])
                self.assertEqual(report['status'], 'unverified')

    def test_session_change_during_inspection_invalidates_snapshot(self):
        self.active()
        def change():
            workspace.private_write(self.state / 'current.json', {'session': 'ws-' + '2' * 32, 'pid': 456})
        report, _ = self.inspect(after_exec=change)
        self.assertFalse(report['verified'])
        self.assertIn('changed during inspection', '\n'.join(report['issues']))

    def test_legacy_session_duration_is_unknown_instead_of_default_guess(self):
        self.active(duration=None)
        report, _ = self.inspect()
        self.assertTrue(report['verified'])
        self.assertIsNone(report['runtime']['launcher']['duration_seconds'])

    def test_no_fallback_or_web_access_from_changed_launcher_configuration(self):
        self.active()
        home = self.state / 'codex'
        (home / 'environments.toml').write_text('include_local=true\n')
        (home / 'config.toml').write_text('web_search="live"\n')
        report, _ = self.inspect()
        self.assertEqual(report['status'], 'mismatch')
        self.assertTrue(report['runtime']['launcher']['local_execution_fallback_configured'])

    def test_malformed_current_metadata_does_not_contact_container(self):
        (self.state / 'current.json').write_text('{invalid')
        with patch.object(guardrails, 'docker') as docker:
            report = guardrails.inspect(self.project)
        self.assertFalse(report['verified'])
        docker.assert_not_called()

    def test_cli_routes_json_and_rejects_options_for_other_commands(self):
        with patch.object(sys, 'argv', ['invariant', 'guardrails', '--json']), patch.object(guardrails, 'main', return_value=0) as main:
            self.assertEqual(cli.main(), 0)
            main.assert_called_once_with(machine=True)
        for args in [['guardrails', 'anything'], ['guardrails', '--agent', 'codex'], ['guardrails', '--effort', 'high'], ['guardrails', '--duration', '120']]:
            with self.subTest(args=args), patch.object(sys, 'argv', ['invariant', *args]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.main()

    def test_machine_output_and_exit_codes_match_verification_status(self):
        for status, expected in [('inactive', 0), ('active', 0), ('mismatch', 1), ('unverified', 1)]:
            value = {'status': status, 'verified': status == 'active'}
            output = io.StringIO()
            with self.subTest(status=status), patch.object(guardrails, 'inspect', return_value=value), contextlib.redirect_stdout(output):
                self.assertEqual(guardrails.main(machine=True), expected)
                self.assertEqual(json.loads(output.getvalue()), value)


if __name__ == '__main__':
    unittest.main()
