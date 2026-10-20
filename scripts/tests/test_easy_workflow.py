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
from unittest.mock import Mock, patch

from invariant import cli, workspace, preferences, resources, guardrails, configuration
from invariant.policy import DEFAULT_CONFIG, NEW_PROJECT_CONFIG, PROFILE, normalize


class AutomaticResourceTests(unittest.TestCase):
    def test_general_build_capacity_caps_unknown_and_tiny_engines(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            policy = normalize(NEW_PROJECT_CONFIG)
            selected, plan = resources.select(policy, project, {'cpus': 10, 'memory_bytes': 8*1024**3})
            self.assertEqual(selected['limits']['memory_mib'], 1024)
            self.assertEqual(selected['limits']['cpus'], 2)
            self.assertEqual(plan['memory_ceiling_mib'], 4096)
            (project / 'Cargo.toml').write_text('untrusted manifest, never executed')
            selected, plan = resources.select(policy, project, {'cpus': 10, 'memory_bytes': 8*1024**3}, 999999)
            self.assertEqual(selected['limits']['memory_mib'], 1024)
            self.assertEqual(selected['limits']['cpus'], 2)
            selected, plan = resources.select(policy, project, {'cpus': 1, 'memory_bytes': 256*1024**2}, 4096)
            self.assertEqual(selected['limits']['memory_mib'], 128)
            self.assertEqual(selected['limits']['cpus'], 1)
            selected, plan = resources.select(policy, project, {}, 4096)
            self.assertEqual(selected['limits']['memory_mib'], 512)
            self.assertTrue(plan['conservative_fallback'])
            with self.assertRaisesRegex(RuntimeError, 'too little memory'):
                resources.select(policy, project, {'memory_bytes': 64*1024**2})
            self.assertEqual(policy, normalize(NEW_PROJECT_CONFIG))

    def test_manual_settings_are_not_resized_and_explicit_edit_selects_manual_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            with contextlib.redirect_stdout(io.StringIO()): workspace.init(project)
            selected = configuration.set_value(project, 'limits.memory_mib', '768')
            self.assertEqual(selected['resource_mode'], 'manual')
            actual, plan = resources.select(selected, project, {'cpus': 1, 'memory_bytes': 64*1024**2}, 4096)
            self.assertEqual(actual['limits']['memory_mib'], 768)
            self.assertEqual(plan['mode'], 'manual')
            reset = configuration.set_value(project, 'resource_mode', 'auto')
            self.assertEqual(reset['limits'], DEFAULT_CONFIG['limits'])
            with self.assertRaisesRegex(RuntimeError, 'custom limits'):
                normalize({**reset, 'limits': {'memory_mib': 768}})

    def test_memory_advice_is_private_project_bound_and_clamped_by_measured_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            project, state = Path(folder)/'project', Path(folder)/'private'
            project.mkdir()
            policy, plan = resources.select(NEW_PROJECT_CONFIG, project, {'cpus': 4, 'memory_bytes': 8*1024**3})
            self.assertEqual(preferences.remember_pressure(state, project, plan), 2048)
            self.assertEqual((state/'resources.json').stat().st_mode & 0o777, 0o600)
            hint = preferences.memory_hint(state, project)
            selected, _ = resources.select(NEW_PROJECT_CONFIG, project, {'cpus': 2, 'memory_bytes': 2*1024**3}, hint)
            self.assertEqual(selected['limits']['memory_mib'], 1024)
            self.assertIsNone(preferences.memory_hint(state, project/'other'))
            self.assertIsNone(preferences.remember_pressure(state, project, {'mode':'manual'}))

    def test_probes_handle_timeouts_and_only_read_counters(self):
        docker = Mock(side_effect=subprocess.TimeoutExpired('docker', 1))
        self.assertEqual(resources.capacity(docker), {})
        self.assertIsNone(resources.pressure(docker, 'owned'))
        docker = Mock(return_value='{"oom_kill":2}')
        self.assertEqual(resources.pressure(docker, 'owned'), 2)
        self.assertIn('-I', docker.call_args.args)

    def test_inactive_auto_resources_are_not_claimed_as_selected_or_live(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            with contextlib.redirect_stdout(io.StringIO()): workspace.init(project)
            with patch.object(guardrails, 'state_for', return_value=project/'not-present'), patch.object(guardrails, 'docker') as docker:
                result = guardrails.inspect(project)
            self.assertEqual(result['configured']['resource_mode'], 'auto')
            self.assertIsNone(result['configured']['memory_bytes'])
            self.assertFalse(result['verified'])
            docker.assert_not_called()


class LaunchPreferencesTests(unittest.TestCase):
    def test_preferences_are_private_and_do_not_cross_project_or_follow_links(self):
        with tempfile.TemporaryDirectory() as folder:
            project, state = Path(folder)/'project', Path(folder)/'private'
            self.assertIsNone(preferences.load(state, project))
            preferences.save(state, project, 'selected', 'medium')
            self.assertEqual(preferences.load(state, project)['effort'], 'medium')
            self.assertEqual((state/'launch.json').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(RuntimeError): preferences.load(state, project/'other')
            source=state/'launch.json';outside=state/'outside';source.rename(outside);source.symlink_to(outside)
            with self.assertRaises(RuntimeError): preferences.load(state, project)
            for model, effort in [('bad model',None), ('selected','bad effort')]:
                with self.assertRaises(RuntimeError): preferences.save(state,project,model,effort)

    def test_default_model_uses_visible_catalog_default_across_pages(self):
        rpc=Mock()
        rpc.request.side_effect=[{'data':[{'model':'hidden','isDefault':True,'hidden':True}],'nextCursor':'p2'},
          {'data':[{'model':'account-default','isDefault':True,'defaultReasoningEffort':'medium',
                    'supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}]}]
        self.assertEqual(workspace.default_model(rpc), ('account-default','medium'))
        self.assertFalse(rpc.request.call_args_list[0].args[1]['includeHidden'])

    def test_missing_ambiguous_invalid_and_broken_catalogs_do_not_guess(self):
        for page in ({'data':[]}, {'data':[{'model':'one','isDefault':True},{'model':'two','isDefault':True}]},
                     {'data':[{'model':'bad model','isDefault':True}]}, {'data':[],'nextCursor':'repeated'},
                     {'data':[{'model':'selected','isDefault':True,'defaultReasoningEffort':'high','supportedReasoningEfforts':[]}]}):
            rpc=Mock();rpc.request.return_value=page
            with self.subTest(page=page), self.assertRaises(RuntimeError): workspace.default_model(rpc)

    def test_simple_and_advanced_cli_routes_and_python_compatibility(self):
        for args, model, effort in [(['run'],None,None),(['run','--agent','codex'],None,None),
                                    (['run','--model','selected','--effort','high'],'selected','high'),
                                    (['run','selected'],'selected',None),(['run','--effort','medium'],None,'medium')]:
            with patch.object(sys,'argv',['invariant',*args]), patch.object(workspace,'run') as run:
                cli.main();run.assert_called_once_with(model,duration=None,effort=effort)
        for args in (['resume','--model','selected'],['run','selected','--model','other'],['run','missing.py','--effort','high']):
            with patch.object(sys,'argv',['invariant',*args]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): cli.main()
        with tempfile.TemporaryDirectory() as folder:
            program=Path(folder)/'program.py';program.write_text('print(42)')
            with patch.object(sys,'argv',['invariant','run',str(program)]), patch.object(cli,'docker') as docker, patch.object(cli.os,'execvp') as execute:
                docker.return_value.stdout='[{"Mountpoint":"/trusted/state"}]'
                cli.main();self.assertIn('/program.py',execute.call_args.args[1]);self.assertIn('900',execute.call_args.args[1])


if __name__=='__main__': unittest.main()
