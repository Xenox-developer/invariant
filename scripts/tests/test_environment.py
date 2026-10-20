import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from invariant import environment, workspace, configuration
from invariant.policy import NEW_PROJECT_CONFIG, normalize

IMAGE = 'sha256:' + '1' * 64
NAME = 'invariant-ws-' + '2' * 32


class PersistentEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve() / 'project'
        self.project.mkdir()
        self.state = self.project.parent / 'state'
        self.state.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.init(self.project)

    def info(self, *, running=True, read_only=False):
        return {'Config': {'Labels': {'io.invariant.role': 'codex-workspace', 'io.invariant.session': NAME,
                'io.invariant.environment': environment.PROFILE, 'io.invariant.project': environment.project_key(self.project)}},
                'HostConfig': {'ReadonlyRootfs': read_only}, 'State': {'Running': running}}

    def test_new_projects_are_general_and_legacy_projects_upgrade_explicitly(self):
        self.assertTrue(environment.enabled(workspace.load_project(self.project)))
        configuration.set_value(self.project, 'profile', 'codex-workspace-v1')
        self.assertFalse(environment.enabled(workspace.load_project(self.project)))
        configuration.set_value(self.project, 'profile', environment.PROFILE)
        self.assertTrue(environment.enabled(workspace.load_project(self.project)))
        with self.assertRaises(RuntimeError): configuration.set_value(self.project, 'profile', 'unconfined')

    def test_general_environment_has_no_language_paths_and_has_protected_control(self):
        args = workspace.container_command(NAME, self.project, IMAGE)
        self.assertIn('--user=0:0', args)
        self.assertNotIn('--read-only', args)
        self.assertFalse(any('PYTHONUSERBASE' in s or 'PIP_CACHE_DIR' in s for s in args))
        self.assertIn('--entrypoint=/__invariant/codex', args)
        self.assertFalse(any(s.startswith(('--cap-add=SYS_ADMIN', '--privileged', '--publish')) for s in args))
        configuration.set_value(self.project, 'workspace_access', 'read-only')
        self.assertIn('--read-only', workspace.container_command(NAME, self.project, IMAGE))

    def test_toolchain_symlinks_are_supported_but_host_hardlinks_are_rejected(self):
        (self.project / 'venv-bin').symlink_to('/usr/bin/python3')
        (self.project / 'internal').write_text('build artifact')
        os.link(self.project / 'internal', self.project / 'internal-link')
        workspace.check_project_files(self.project, allow_symlinks=True)
        outside = self.project.parent / 'outside';outside.write_text('host')
        os.link(outside, self.project / 'host-link')
        with self.assertRaisesRegex(RuntimeError, 'hard-linked'):
            workspace.check_project_files(self.project, allow_symlinks=True)

    def test_checkpoint_stops_before_commit_saves_identity_then_removes_container(self):
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            return json.dumps([self.info()]) if args[0] == 'inspect' else IMAGE if args[0] == 'commit' else ''
        def remove(name):
            self.assertEqual(name, NAME)
            self.assertEqual(json.loads((self.state/'environment.json').read_text())['image'], IMAGE)
        with patch.object(workspace,'docker',side_effect=docker), patch.object(workspace,'remove_owned',side_effect=remove) as removed:
            environment.preserve(NAME, self.state, self.project, Mock())
        self.assertEqual([c[0] for c in calls], ['inspect','stop','commit'])
        removed.assert_called_once()
        self.assertEqual((self.state/'environment.json').stat().st_mode&0o777, 0o600)

    def test_failed_checkpoint_retains_stopped_container_and_previous_record(self):
        workspace.private_write(self.state/'environment.json',{'previous':'kept'})
        def docker(*args, **kwargs):
            if args[0] == 'inspect': return json.dumps([self.info()])
            if args[0] == 'commit': raise RuntimeError('disk full')
        with patch.object(workspace,'docker',side_effect=docker), patch.object(workspace,'remove_owned') as removed:
            with self.assertRaisesRegex(RuntimeError,'disk full'):
                environment.preserve(NAME,self.state,self.project,Mock())
        removed.assert_not_called()
        self.assertEqual(json.loads((self.state/'environment.json').read_text()),{'previous':'kept'})

    def test_read_only_and_foreign_containers_cannot_overwrite_environment(self):
        with patch.object(workspace,'docker',return_value=json.dumps([self.info(read_only=True)])) as docker, patch.object(workspace,'remove_owned'):
            environment.preserve(NAME,self.state,self.project,Mock())
            self.assertEqual(len(docker.call_args_list),2)  # inspect and stop, no commit
        info=self.info();info['Config']['Labels']['io.invariant.project']='foreign'
        with patch.object(workspace,'docker',return_value=json.dumps([info])) as docker, patch.object(workspace,'remove_owned') as removed:
            with self.assertRaisesRegex(RuntimeError,'another project'):
                environment.preserve(NAME,self.state,self.project,Mock())
            self.assertEqual(len(docker.call_args_list),1)
            removed.assert_not_called()

    def test_missing_or_foreign_image_never_silently_creates_fresh_environment(self):
        record={'schema':1,'project':str(self.project),'image':IMAGE}
        workspace.private_write(self.state/'environment.json',record)
        with patch.object(workspace,'docker',side_effect=RuntimeError('No such image')):
            with self.assertRaisesRegex(RuntimeError,'not substituted'):
                environment.selected_image(self.state,self.project,'base')
        info={'Id':IMAGE,'Config':{'Labels':{'io.invariant.checkpoint-project':'foreign'}}}
        with patch.object(workspace,'docker',return_value=json.dumps([info])):
            with self.assertRaisesRegex(RuntimeError,'ownership'):
                environment.selected_image(self.state,self.project,'base')
        record['project']='foreign';workspace.private_write(self.state/'environment.json',record)
        with patch.object(workspace,'docker') as docker:
            with self.assertRaisesRegex(RuntimeError,'belong'):
                environment.selected_image(self.state,self.project,'base')
            docker.assert_not_called()

    def test_general_scratch_can_execute_compiler_outputs_with_nosuid_and_nodev(self):
        from invariant.policy import scratch_options
        flags=scratch_options(normalize(NEW_PROJECT_CONFIG)).split(',')
        self.assertTrue({'exec','nosuid','nodev'}.issubset(flags))


if __name__ == '__main__': unittest.main()
