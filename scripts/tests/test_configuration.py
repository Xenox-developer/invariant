import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from invariant import cli, configuration, guardrails, workspace
from invariant.policy import DEFAULT_CONFIG, NEW_PROJECT_CONFIG, LIMITS, PROFILE, normalize, resource_limits


class ProjectConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve() / 'project'
        self.project.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.init(self.project)
        self.path = self.project / '.invariant/config.json'

    def test_new_and_legacy_configs_have_identical_default_enforcement(self):
        self.assertEqual(json.loads(self.path.read_text()), NEW_PROJECT_CONFIG)
        self.path.write_text(json.dumps(PROFILE))
        before = self.path.read_bytes()
        self.assertEqual(workspace.load_project(self.project), DEFAULT_CONFIG)
        self.assertEqual(resource_limits(workspace.load_project(self.project)), LIMITS)
        self.assertEqual(self.path.read_bytes(), before)

    def test_each_project_has_independent_settings_and_partial_overrides(self):
        other = self.project.parent / 'other';other.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.init(other)
        self.path.write_text(json.dumps({**PROFILE, 'limits': {'memory_mib': 1024}}))
        selected = workspace.load_project(self.project)
        self.assertEqual(selected['limits']['memory_mib'], 1024)
        self.assertEqual(selected['limits']['cpus'], 1)
        self.assertEqual(workspace.load_project(other), normalize(NEW_PROJECT_CONFIG))
        self.assertEqual(DEFAULT_CONFIG['limits']['memory_mib'], 512)

    def test_supported_settings_are_saved_atomically_and_idempotent_init_preserves_them(self):
        for key, value in [('workspace_access', 'read-only'), ('duration_seconds', '3600'),
                           ('limits.cpus', '0.25'), ('limits.memory_mib', '1024'),
                           ('limits.extra_swap_mib', '128'), ('limits.processes', '256'),
                           ('limits.file_descriptors', '512'), ('limits.scratch_mib', '256')]:
            configuration.set_value(self.project, key, value)
        value = workspace.load_project(self.project)
        self.assertEqual(value['workspace_access'], 'read-only')
        self.assertEqual(value['duration_seconds'], 3600)
        self.assertEqual(value['limits'], {'cpus': .25, 'memory_mib': 1024, 'extra_swap_mib': 128,
                                        'processes': 256, 'file_descriptors': 512, 'scratch_mib': 256})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        before = self.path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()): workspace.init(self.project)
        self.assertEqual(self.path.read_bytes(), before)

    def test_internet_opt_in_is_per_project_and_uses_matching_docker_and_seccomp(self):
        from invariant.policy import seccomp_filename
        configuration.set_value(self.project, 'resource_mode', 'manual')
        selected = configuration.set_value(self.project, 'command_network', 'internet')
        args = workspace.container_command('owned', self.project, 'sha256:image')
        self.assertIn('--network=bridge', args)
        self.assertIn('--security-opt=seccomp=' + str(workspace.ASSETS / 'seccomp-environment-internet.json'), args)
        self.assertNotIn('--read-only', args)
        self.assertIn('--user=0:0', args)
        self.assertFalse(any(arg.startswith(('--publish', '--privileged', '--cap-add=SYS_ADMIN', '--cap-add=NET_RAW')) for arg in args))
        self.assertEqual(seccomp_filename(selected), 'seccomp-environment-internet.json')
        with patch.object(guardrails, 'state_for', return_value=self.project.parent / 'inactive'):
            report = guardrails.inspect(self.project)
        self.assertEqual(report['configured']['network'], 'internet')
        self.assertFalse(report['verified'])
        configuration.set_value(self.project, 'command_network', 'none')
        self.assertIn('--network=none', workspace.container_command('owned', self.project, 'sha256:image'))
        self.assertEqual(normalize(PROFILE)['command_network'], 'none')

    def test_network_modes_reject_unsafe_names_and_non_string_values(self):
        before = self.path.read_bytes()
        for text in ('host', 'bridge', 'container:other', 'true', 'null', '1', '[]', '{}'):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, 'command_network'):
                configuration.set_value(self.project, 'command_network', text)
            self.assertEqual(self.path.read_bytes(), before)

    def test_bad_values_never_replace_previous_policy(self):
        before = self.path.read_bytes()
        for key, text in [('limits.cpus', 'true'), ('limits.cpus', '0'), ('limits.cpus', '0.125'),
                          ('limits.cpus', '1e1000'), ('limits.cpus', '9'*128), ('limits.memory_mib', '-1'),
                          ('limits.memory_mib', '1024.0'), ('limits.memory_mib', '65537'),
                          ('limits.extra_swap_mib', '-1'), ('limits.processes', '0'),
                          ('limits.file_descriptors', 'null'), ('limits.scratch_mib', '[]'),
                          ('duration_seconds', 'true'), ('duration_seconds', '86401'),
                          ('workspace_access', 'host'), ('limits.unknown', '123'),
                          ('command_network', 'bridge'), ('model_access', 'anything')]:
            with self.subTest(key=key, text=text), self.assertRaises(RuntimeError):
                configuration.set_value(self.project, key, text)
            self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_fields_and_unsafe_authority_are_rejected_on_direct_edit(self):
        for changes in ({'schema': True}, {'network': 'host'}, {'command_network': 'bridge'},
                        {'limits': {'cpus': None}}, {'limits': []}, {'duration_seconds': False},
                        {'privileged': True}, {'mounts': ['/']}, {'workspace_access': []}):
            with self.subTest(changes=changes):
                self.path.write_text(json.dumps({**PROFILE, **changes}))
                with self.assertRaises(RuntimeError): workspace.load_project(self.project)

    def test_duplicate_keys_oversized_files_hardlinks_and_fifos_fail_closed(self):
        for text in ('{"schema":1,"schema":1}', '{"a":' + ' '*8192 + '1}'):
            self.path.write_text(text)
            with self.assertRaisesRegex(RuntimeError, 'Invalid'): workspace.load_project(self.project)
        self.path.write_text(json.dumps(PROFILE))
        link = self.project.parent / 'linked';os.link(self.path, link)
        with self.assertRaisesRegex(RuntimeError, 'Invalid'): workspace.load_project(self.project)
        link.unlink();self.path.unlink();os.mkfifo(self.path)
        with self.assertRaisesRegex(RuntimeError, 'Invalid'): workspace.load_project(self.project)

    def test_unsafe_config_lock_is_never_followed(self):
        outside = self.project.parent / 'outside';outside.write_text('untouched')
        (self.project / '.invariant/config.lock').symlink_to(outside)
        with self.assertRaises((RuntimeError, OSError)):
            configuration.set_value(self.project, 'duration_seconds', '3600')
        self.assertEqual(outside.read_text(), 'untouched')

    def test_container_command_and_inactive_display_use_selected_resources_and_read_only_mount(self):
        configuration.set_value(self.project, 'limits.cpus', '0.25')
        configuration.set_value(self.project, 'limits.memory_mib', '1024')
        configuration.set_value(self.project, 'limits.extra_swap_mib', '128')
        configuration.set_value(self.project, 'workspace_access', 'read-only')
        args = workspace.container_command('owned', self.project, 'sha256:image')
        self.assertIn('--cpus=0.25', args)
        self.assertIn('--memory=1073741824', args)
        self.assertIn('--memory-swap=1207959552', args)
        self.assertIn(f'type=bind,src={self.project},dst=/workspace,readonly', args)
        with patch.object(guardrails, 'state_for', return_value=self.project.parent / 'inactive'):
            report = guardrails.inspect(self.project)
        self.assertEqual(report['configured']['memory_bytes'], 1073741824)
        self.assertIsNone(report['configured']['writable_host_mount'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output): guardrails.render(report)
        self.assertIn('denied (read-only)', output.getvalue())

    def test_config_cli_show_set_json_and_invalid_options(self):
        with patch.object(Path, 'cwd', return_value=self.project):
            for args in (['config', 'set', 'limits.memory_mib', '1024'], ['config', '--json']):
                output = io.StringIO()
                with patch.object(sys, 'argv', ['invariant', *args]), contextlib.redirect_stdout(output):
                    self.assertEqual(cli.main(), 0)
                if '--json' in args:
                    self.assertEqual(json.loads(output.getvalue())['limits']['memory_mib'], 1024)
            for args in (['config', 'set'], ['config', 'set', 'x'], ['config', '--duration', '900'],
                         ['config', '--effort', 'high'], ['init', 'extra', 'args']):
                with self.subTest(args=args), patch.object(sys, 'argv', ['invariant', *args]), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit): cli.main()

    def test_cli_duration_override_is_explicit_and_omission_defers_to_project(self):
        for args, duration in ((['run', '--agent', 'codex', 'selected'], None),
                               (['run', '--agent', 'codex', 'selected', '--duration', '900'], 900),
                               (['resume', '--duration', '120'], 120)):
            with patch.object(sys, 'argv', ['invariant', *args]), patch.object(workspace, 'run') as run:
                cli.main()
            self.assertEqual(run.call_args.kwargs['duration'], duration)


if __name__ == '__main__': unittest.main()
