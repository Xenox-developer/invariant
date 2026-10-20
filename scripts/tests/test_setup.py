import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from invariant import cli, dependencies as deps, setup


def report(codex='ready', docker='ready'):
    return {'ready': codex == docker == 'ready', 'supported': True, 'platform': 'darwin',
            'codex': {'status': codex, 'path': '/codex' if codex != 'missing' else None, 'message': codex},
            'docker': {'status': docker, 'path': '/docker' if docker != 'missing' else None, 'message': docker}}


class DiscoveryTests(unittest.TestCase):
    def test_detects_user_and_application_docker_outside_path(self):
        for candidate in (Path.home() / '.docker/bin/docker', Path('/Applications/Docker.app/Contents/Resources/bin/docker'),
                          Path.home() / 'Applications/Docker.app/Contents/Resources/bin/docker'):
            with self.subTest(candidate=candidate), patch.object(deps.shutil, 'which', return_value=None), patch.object(deps.sys, 'platform', 'darwin'), \
                 patch.object(Path, 'is_file', lambda path: path == candidate), patch.object(deps.os, 'access', return_value=True):
                self.assertEqual(deps.find_docker(), str(candidate))

    def test_managed_codex_is_selected_before_incompatible_global_installation(self):
        managed = deps.managed_codex() / 'bin/codex'
        with patch.object(deps, 'executable_file', side_effect=lambda p: p == managed), patch.object(deps.shutil, 'which', return_value='/global/codex'):
            self.assertEqual(deps.find_codex(), str(managed))

    def test_codex_versions_fail_closed(self):
        for output, status in [('codex-cli 0.163.0\n', 'ready'), ('codex-cli 0.163.0-alpha.10.2\n', 'ready'),
                               ('codex-cli 0.164.0\n', 'incompatible'), ('unrelated\n', 'incompatible')]:
            with self.subTest(output=output), patch.object(deps, 'find_codex', return_value='/codex'), \
                 patch.object(deps.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')):
                self.assertEqual(deps.probe_codex()['status'], status)
        with patch.object(deps, 'find_codex', return_value='/codex'), patch.object(deps.subprocess, 'run', side_effect=subprocess.TimeoutExpired('codex', 10)):
            self.assertEqual(deps.probe_codex()['status'], 'unavailable')

    def test_docker_requires_accessible_linux_engine_without_printing_diagnostics(self):
        for output, code, status in [('linux\n', 0, 'ready'), ('windows\n', 0, 'incompatible'), ('', 1, 'unavailable')]:
            with self.subTest(status=status), patch.object(deps, 'find_docker', return_value='/docker'), \
                 patch.object(deps.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, output, 'secret-diagnostic')):
                value = deps.probe_docker()
                self.assertEqual(value['status'], status)
                self.assertNotIn('secret-diagnostic', json.dumps(value))

    def test_noninteractive_run_never_prompts_or_installs(self):
        with patch.object(deps, 'inspect_dependencies', return_value=report('missing', 'missing')), \
             patch.object(deps, 'interactive_terminal', return_value=False), patch.object(setup, 'guided_setup') as guided:
            with self.assertRaisesRegex(RuntimeError, 'invariant setup'):
                deps.ensure_runtime()
            guided.assert_not_called()

    def test_run_offers_setup_and_reuses_verified_paths(self):
        with patch.object(deps, 'inspect_dependencies', return_value=report('missing', 'missing')), \
             patch.object(deps, 'interactive_terminal', return_value=True), patch.object(setup, 'guided_setup', return_value=report()) as guided:
            self.assertEqual(deps.ensure_runtime(), ('/codex', '/docker'))
            guided.assert_called_once()


class GuidedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        for mock in (patch.object(Path, 'home', return_value=self.home), contextlib.redirect_stdout(io.StringIO())):
            mock.__enter__()
            self.addCleanup(mock.__exit__, None, None, None)

    def test_ready_setup_is_idempotent_and_does_not_write_or_prompt(self):
        with patch.object(setup, 'confirm') as confirm, patch.object(setup, 'install_codex') as install:
            self.assertTrue(setup.guided_setup(report())['ready'])
            confirm.assert_not_called()
            install.assert_not_called()
            self.assertFalse((self.home / '.invariant').exists())

    def test_declining_both_downloads_installs_nothing(self):
        missing = report('missing', 'missing')
        with patch.object(deps, 'interactive_terminal', return_value=True), patch.object(deps.sys, 'platform', 'darwin'), \
             patch.object(deps, 'docker_app', return_value=None), patch.object(deps, 'probe_docker', return_value=missing['docker']), \
             patch.object(deps, 'inspect_dependencies', return_value=missing), patch.object(setup, 'confirm', return_value=False) as confirm, \
             patch.object(setup, 'install_codex') as codex, patch.object(setup, 'install_docker_mac') as docker:
            self.assertFalse(setup.guided_setup(missing)['ready'])
            self.assertEqual(confirm.call_count, 2)
            codex.assert_not_called()
            docker.assert_not_called()

    def test_missing_dependencies_offer_independent_installation_then_verify(self):
        missing = report('missing', 'missing')
        app = self.home / 'Applications/Docker.app'
        with patch.object(deps, 'interactive_terminal', return_value=True), patch.object(deps.sys, 'platform', 'darwin'), \
             patch.object(deps, 'docker_app', side_effect=[None, app]), patch.object(deps, 'probe_docker', return_value=report(docker='unavailable')['docker']), \
             patch.object(deps, 'inspect_dependencies', return_value=report()), patch.object(setup, 'confirm', return_value=True), \
             patch.object(setup, 'install_codex') as codex, patch.object(setup, 'install_docker_mac') as docker, \
             patch.object(setup, 'checked') as checked, patch.object(setup, 'wait_for_docker') as wait:
            self.assertTrue(setup.guided_setup(missing)['ready'])
            codex.assert_called_once()
            docker.assert_called_once()
            checked.assert_called_once_with(['/usr/bin/open', str(app)], timeout=15)
            wait.assert_called_once()

    def test_stopped_docker_is_opened_without_reinstallation(self):
        unavailable = report(docker='unavailable')
        with patch.object(deps, 'interactive_terminal', return_value=True), patch.object(deps, 'docker_app', return_value=self.home / 'Docker.app'), \
             patch.object(deps, 'probe_docker', return_value=unavailable['docker']), patch.object(deps, 'inspect_dependencies', return_value=report()), \
             patch.object(setup, 'confirm', return_value=True), patch.object(setup, 'checked'), patch.object(setup, 'wait_for_docker'), \
             patch.object(setup, 'install_docker_mac') as install:
            self.assertTrue(setup.guided_setup(unavailable)['ready'])
            install.assert_not_called()

    def test_json_and_check_modes_are_read_only_and_return_failure_when_missing(self):
        for check, machine in ((True, False), (False, True)):
            with patch.object(deps, 'inspect_dependencies', return_value=report('missing')), patch.object(setup, 'guided_setup') as guided:
                self.assertEqual(setup.main(check=check, machine=machine), 1)
                guided.assert_not_called()
                self.assertFalse((self.home / '.invariant').exists())

    def test_noninteractive_guided_setup_does_not_create_state(self):
        with patch.object(deps, 'interactive_terminal', return_value=False), patch.object(setup, 'confirm') as confirm:
            self.assertFalse(setup.guided_setup(report('missing'))['ready'])
            confirm.assert_not_called()
            self.assertFalse((self.home / '.invariant').exists())

    def test_linux_package_install_uses_os_package_manager(self):
        with patch.object(setup.shutil, 'which', return_value='/usr/bin/apt-get'), patch.object(setup, 'privileged') as privileged:
            setup.install_docker_linux()
            self.assertEqual([c.args[0] for c in privileged.call_args_list],
                             [['/usr/bin/apt-get', 'update'], ['/usr/bin/apt-get', 'install', '--yes', 'docker.io']])

    def test_waiting_for_docker_is_bounded(self):
        with patch.object(deps, 'probe_docker', return_value=report(docker='unavailable')['docker']), patch.object(setup.time, 'monotonic', side_effect=[0, 181]):
            with self.assertRaisesRegex(RuntimeError, 'not ready yet'):
                setup.wait_for_docker()

    def test_confirmation_requires_explicit_yes_and_handles_eof(self):
        with patch.object(deps, 'interactive_terminal', return_value=True):
            for answer, expected in (('', False), ('n', False), ('y', True), ('YES', True)):
                with patch('builtins.input', return_value=answer):
                    self.assertIs(setup.confirm('Install?'), expected)
            with patch('builtins.input', side_effect=EOFError):
                with self.assertRaises(setup.SetupCancelled):
                    setup.confirm('Install?')
            with patch('builtins.input', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    setup.confirm('Install?')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        mock = patch.object(Path, 'home', return_value=self.home)
        mock.start()
        self.addCleanup(mock.stop)

    def package(self, *, bad_entry=None, version='0.163.0'):
        package = self.home / 'package.tgz'
        with tarfile.open(package, 'w:gz') as archive:
            for name, content in [('bin/codex', '#!/bin/sh\necho codex-cli ' + version + '\n'), ('bin/codex-code-mode-host', '#!/bin/sh\nexit 0\n')]:
                entry = tarfile.TarInfo('package/vendor/test/' + name)
                data = content.encode()
                entry.size, entry.mode = len(data), 0o755
                archive.addfile(entry, io.BytesIO(data))
            if bad_entry:
                archive.addfile(bad_entry)
        return package

    def install(self, package, *, integrity=None):
        digest = integrity or base64.b64encode(hashlib.sha512(package.read_bytes()).digest()).decode()
        with patch.object(setup, 'PACKAGES', {(sys.platform, setup.platform.machine().lower()): ('test', 'test', digest)}), \
             patch.object(setup, 'download', side_effect=lambda url, path, limit: shutil.copyfile(package, path)), contextlib.redirect_stdout(io.StringIO()):
            setup.install_codex()

    def test_codex_download_is_verified_and_native_sidecar_is_installed(self):
        self.install(self.package())
        destination = deps.managed_codex()
        self.assertTrue((destination / 'bin/codex-code-mode-host').is_file())
        self.assertEqual(deps.find_codex(), str(destination / 'bin/codex'))
        self.assertEqual((destination / 'bin/codex').stat().st_mode & 0o777, 0o700)

    def test_integrity_or_wrong_version_keeps_previous_installation(self):
        destination = deps.managed_codex()
        destination.mkdir(parents=True)
        (destination / 'keep').write_text('existing')
        with self.assertRaisesRegex(RuntimeError, 'integrity'):
            self.install(self.package(), integrity='invalid')
        self.assertEqual((destination / 'keep').read_text(), 'existing')
        with self.assertRaisesRegex(RuntimeError, 'required version'):
            self.install(self.package(version='0.164.0'))
        self.assertEqual((destination / 'keep').read_text(), 'existing')

    def test_interrupted_download_preserves_installation_and_cleans_staging(self):
        destination = deps.managed_codex()
        destination.mkdir(parents=True)
        (destination / 'keep').write_text('existing')
        key = (sys.platform, setup.platform.machine().lower())
        with patch.object(setup, 'PACKAGES', {key: ('test', 'test', 'digest')}), \
             patch.object(setup, 'download', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                setup.install_codex()
        self.assertEqual((destination / 'keep').read_text(), 'existing')
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_links_and_traversal_in_codex_package_are_rejected(self):
        for entry in (tarfile.TarInfo('package/vendor/test/../../escape'), tarfile.TarInfo('package/vendor/test/link')):
            if entry.name.endswith('/link'):
                entry.type, entry.linkname = tarfile.SYMTYPE, '/tmp/escape'
            with self.subTest(entry=entry.name), self.assertRaisesRegex(RuntimeError, 'Unsafe path|Unsupported entry'):
                self.install(self.package(bad_entry=entry))
        self.assertFalse((self.home / 'escape').exists())

    def test_download_restricts_https_and_file_size(self):
        output = self.home / 'download'
        def write(command, **kwargs):
            output.write_bytes(b'test')
        with patch.object(setup.shutil, 'which', return_value='/curl'), patch.object(setup, 'checked', side_effect=write) as checked, contextlib.redirect_stdout(io.StringIO()):
            setup.download('https://example.test/file', output, 10)
            command = checked.call_args.args[0]
            self.assertIn('--max-filesize', command)
            self.assertEqual(command[command.index('--proto') + 1], '=https')
            self.assertEqual(command[command.index('--proto-redir') + 1], '=https')

    def test_docker_install_checks_signature_before_copying_and_never_accepts_terms(self):
        calls = []
        def command(argv, **kwargs):
            calls.append(argv)
            if 'attach' in argv:
                mount = Path(argv[argv.index('-mountpoint') + 1])
                (mount / 'Docker.app').mkdir()
                (mount / 'Docker.app/signed-test').write_text('fixture')
            if argv[0] == '/usr/bin/ditto':
                shutil.copytree(argv[1], argv[2])
        with patch.object(deps, 'docker_app', return_value=None), patch.object(setup.platform, 'machine', return_value='arm64'), \
             patch.object(setup, 'download'), patch.object(setup, 'checked', side_effect=command), \
             patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), contextlib.redirect_stdout(io.StringIO()):
            app = setup.install_docker_mac()
        self.assertEqual((app / 'signed-test').read_text(), 'fixture')
        self.assertLess(next(i for i, v in enumerate(calls) if v[0] == '/usr/bin/codesign'), next(i for i, v in enumerate(calls) if v[0] == '/usr/bin/ditto'))
        self.assertTrue(any(v[0] == '/usr/sbin/spctl' for v in calls))
        self.assertNotIn('--accept-license', str(calls))
        self.assertFalse((self.home / 'Applications').is_symlink())

    def test_bad_docker_signature_does_not_copy_and_unmounts_installer(self):
        def command(argv, **kwargs):
            if argv[0] == '/usr/bin/codesign':
                raise RuntimeError('signature rejected')
        with patch.object(deps, 'docker_app', return_value=None), patch.object(setup.platform, 'machine', return_value='arm64'), \
             patch.object(setup, 'download'), patch.object(setup, 'checked', side_effect=command), \
             patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            with self.assertRaisesRegex(RuntimeError, 'signature rejected'):
                setup.install_docker_mac()
            self.assertIn('detach', run.call_args.args[0])
        self.assertFalse((self.home / 'Applications/Docker.app').exists())


class CliTests(unittest.TestCase):
    def test_setup_routes_and_exit_status_propagate(self):
        with patch.object(sys, 'argv', ['invariant', 'setup', '--check', '--json']), patch.object(setup, 'main', return_value=1) as main:
            with self.assertRaises(SystemExit) as raised:
                cli.entrypoint()
            self.assertEqual(raised.exception.code, 1)
            main.assert_called_once_with(check=True, machine=True)

    def test_rejects_unrelated_setup_options(self):
        for args in (['setup', 'extra'], ['setup', '--agent', 'codex'], ['setup', '--effort', 'high'], ['run', '--check']):
            with self.subTest(args=args), patch.object(sys, 'argv', ['invariant', *args]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.main()


if __name__ == '__main__':
    unittest.main()
