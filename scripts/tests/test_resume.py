import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from invariant import cli, workspace
from invariant.chats import ChatStore

FIRST = '00000000-0000-0000-0000-000000000001'
SECOND = '00000000-0000-0000-0000-000000000002'
ENVIRONMENT = [{'environmentId': 'invariant', 'cwd': '/workspace'}]


class ResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project, self.state = self.root / 'project', self.root / 'state'
        self.project.mkdir()
        self.state.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.init(self.project, automatic=False)
        self.store = ChatStore(self.state, self.project)

    def record(self, chat=FIRST, **changes):
        return dict(schema=1, id=chat, project=str(self.project), model='selected', effort='medium',
                    created_at=1, updated_at=2, **changes)

    def launch(self, *, resume=None, effort=None, response=None, messages=('/exit',), fail=None, interrupted=False, duration=None, model='selected', image_failure_once=False):
        calls = []
        rpc = Mock()
        image_attempts = 0

        def docker(*args, **kwargs):
            nonlocal image_attempts
            if args[:2] == ('image', 'inspect'):
                image_attempts += 1
                if image_failure_once and image_attempts == 1:
                    raise RuntimeError('transient image lookup failure')
            return 'sha256:image'

        def request(method, params, **kwargs):
            calls.append((method, params))
            if method == 'environment/info':
                return {'cwd': 'file:///workspace'}
            if method == 'account/read':
                return {'account': {'type': 'chatgpt'}}
            if method == 'model/list':
                return {'data': [{'model': 'selected', 'isDefault': True, 'defaultReasoningEffort': 'medium', 'supportedReasoningEfforts': [
                    {'reasoningEffort': level} for level in ('medium', 'high')]}]}
            if method in ('thread/start', 'thread/resume'):
                if fail:
                    raise workspace.RpcError(fail)
                return response or {'thread': {'id': FIRST, 'ephemeral': False, 'environments': ENVIRONMENT},
                                    'model': 'selected', 'reasoningEffort': params.get('config', {}).get('model_reasoning_effort', effort or 'medium')}
            if method == 'turn/start':
                if not interrupted:
                    kwargs['event']({'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}})
                return {'turn': {'id': 'turn'}}
            raise AssertionError(method)

        rpc.request.side_effect = request
        rpc.receive.side_effect = workspace.SessionStopped()
        output = io.StringIO()
        with (patch.object(Path, 'cwd', return_value=self.project),
              patch.object(workspace, 'state_for', return_value=self.state),
              patch.object(workspace, "ensure_runtime", return_value=("/usr/bin/codex", "/usr/bin/docker")),
              patch.object(workspace.shutil, 'which', return_value='/usr/bin/tool'),
              patch.object(workspace.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'codex-cli 0.163.0\n', '')),
              patch.object(workspace, 'docker', side_effect=docker),
              patch.object(workspace, 'verify_container'), patch.object(workspace, 'remove_owned'),
              patch.object(workspace, 'Control'),
              patch.object(workspace, 'codex_configuration', return_value=(['codex'], {})),
              patch.object(workspace, 'CodexRpc', return_value=rpc),
              patch('builtins.input', side_effect=messages), contextlib.redirect_stdout(output)):
            workspace.run(None if resume is not None else model, resume=resume, effort=effort, duration=duration)
        return calls, output.getvalue()

    def test_new_chat_is_persistent_and_metadata_private(self):
        calls, output = self.launch(messages=('hello', '/exit'))
        params = next(params for method, params in calls if method == 'thread/start')
        self.assertIs(params['ephemeral'], False)
        saved = self.store.load()
        self.assertEqual(saved['id'], FIRST)
        self.assertEqual(saved['effort'], 'medium')
        self.assertIn('invariant resume ' + FIRST, output)
        self.assertEqual((self.state / 'chats' / (FIRST + '.json')).stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.state / 'current.json').exists())

    def test_resume_uses_native_id_saved_model_effort_and_new_executor(self):
        self.store.save(self.record())
        calls, output = self.launch(resume=True, messages=('continue', '/exit'))
        self.assertFalse(any(method == 'thread/start' for method, _ in calls))
        params = next(params for method, params in calls if method == 'thread/resume')
        self.assertEqual(params['threadId'], FIRST)
        self.assertEqual(params['model'], 'selected')
        self.assertEqual(params['config'], {'model_reasoning_effort': 'medium'})
        self.assertTrue(params['excludeTurns'])
        self.assertNotIn('path', params)
        self.assertNotIn('history', params)
        turn = next(params for method, params in calls if method == 'turn/start')
        self.assertEqual(turn['environments'], ENVIRONMENT)
        self.assertEqual(turn['effort'], 'medium')
        self.assertIn('Resumed chat: ' + FIRST, output)
        self.assertEqual(self.store.load()['created_at'], 1)

    def test_resume_uses_current_project_policy_duration_and_explicit_override(self):
        from invariant.configuration import set_value
        self.store.save(self.record())
        set_value(self.project, 'duration_seconds', '3600')
        set_value(self.project, 'workspace_access', 'read-only')
        for override, expected in ((None, 3600), (900, 900)):
            with self.subTest(override=override):
                calls, output = self.launch(resume=True, duration=override)
                params = next(params for method, params in calls if method == 'thread/resume')
                self.assertIn('project is read-only', params['developerInstructions'])
                self.assertIn('Limit: ' + str(expected) + ' seconds', output)
        starts = [json.loads(line) for path in self.state.glob('ws-*.jsonl') for line in path.read_text().splitlines()
                  if json.loads(line)['event'] == 'session_start']
        self.assertEqual({row['duration_seconds'] for row in starts}, {900, 3600})
        self.assertTrue(all(row['profile']['workspace_access'] == 'read-only' for row in starts))

    def test_resume_uses_current_network_policy_and_package_instructions(self):
        from invariant.configuration import set_value
        self.store.save(self.record())
        set_value(self.project, 'command_network', 'internet')
        calls, output = self.launch(resume=True)
        params = next(params for method, params in calls if method == 'thread/resume')
        self.assertIn('Command internet access is enabled', params['developerInstructions'])
        self.assertIn('pip install PACKAGE', params['developerInstructions'])
        self.assertIn('/workspace/.invariant-deps/python', params['developerInstructions'])
        self.assertIn('persist automatically', params['developerInstructions'])
        self.assertIn('including reachable local-network services', output)
        set_value(self.project, 'command_network', 'none')
        calls, output = self.launch(resume=True)
        params = next(params for method, params in calls if method == 'thread/resume')
        self.assertIn('Command networking is disabled', params['developerInstructions'])
        self.assertNotIn('Command internet access is enabled', params['developerInstructions'])

    def test_bare_run_selects_catalog_default_then_remembers_confirmed_preferences(self):
        from invariant import preferences
        calls, output = self.launch(model=None)
        self.assertEqual(preferences.load(self.state, self.project)['model'], 'selected')
        self.assertEqual(preferences.load(self.state, self.project)['effort'], 'medium')
        self.assertFalse((self.state / 'latest-chat.json').exists())
        preferences.save(self.state, self.project, 'selected', 'high')
        calls, output = self.launch(model=None)
        params = next(params for method, params in calls if method == 'thread/start')
        self.assertEqual(params['config']['model_reasoning_effort'], 'high')
        self.assertIn('Reasoning effort: high', output)
        self.launch(model='selected')
        self.assertEqual(preferences.load(self.state, self.project)['effort'], 'medium')

    def test_legacy_latest_chat_supplies_preferences_for_a_new_bare_run(self):
        self.store.save(self.record())
        calls, output = self.launch(model=None)
        self.assertTrue(any(method == 'thread/start' for method, _ in calls))
        self.assertFalse(any(method == 'thread/resume' for method, _ in calls))
        self.assertEqual(self.store.load()['id'], FIRST)

    def test_failed_selection_does_not_replace_preferences_and_explicit_choice_recovers_corruption(self):
        from invariant import preferences
        preferences.save(self.state, self.project, 'selected', 'high')
        previous = (self.state / 'launch.json').read_bytes()
        bad = {'thread': {'id': FIRST, 'ephemeral': False, 'environments': ENVIRONMENT}, 'model': 'wrong', 'reasoningEffort': 'medium'}
        with self.assertRaises(RuntimeError): self.launch(model='selected', response=bad)
        self.assertEqual((self.state / 'launch.json').read_bytes(), previous)
        (self.state / 'launch.json').write_text('{broken')
        with self.assertRaisesRegex(RuntimeError, 'Saved launch settings'): self.launch(model=None)
        self.launch(model='selected')
        self.assertEqual(preferences.load(self.state, self.project)['model'], 'selected')

    def test_oom_advice_changes_only_next_auto_session_and_does_not_replay_turns(self):
        from invariant import resources, preferences
        from invariant.configuration import set_value
        set_value(self.project, 'resource_mode', 'auto')
        with patch.object(resources, 'capacity', return_value={'cpus': 8, 'memory_bytes': 8*1024**3}), patch.object(resources, 'pressure', return_value=1):
            calls, output = self.launch(messages=('task', '/exit'))
        self.assertEqual(sum(method == 'turn/start' for method, _ in calls), 1)
        self.assertEqual(preferences.memory_hint(self.state, self.project), 2048)
        self.assertIn('next session will use up to 2048 MiB', output)
        self.assertEqual(json.loads((self.project / '.invariant/config.json').read_text())['resource_mode'], 'auto')
        with patch.object(resources, 'capacity', return_value={'cpus': 8, 'memory_bytes': 8*1024**3}), patch.object(resources, 'pressure', return_value=0):
            calls, output = self.launch(resume=True)
        self.assertIn('2048 MiB memory', output)
        self.assertFalse(any(method == 'turn/start' for method, _ in calls))

    def test_failed_turn_keeps_chat_open_and_disconnect_has_actionable_recovery(self):
        with patch.object(workspace, 'chat_turn', side_effect=workspace.TurnFailed('download failed')):
            calls, output = self.launch(messages=('try', '/exit'))
        self.assertIn('send another message', output)
        self.assertIn('command_network internet', output)
        self.store.save(self.record())
        with patch.object(workspace, 'chat_turn', side_effect=workspace.RpcError('Codex connection closed')):
            captured = io.StringIO()
            # launch captures its own terminal output; native error type is retained.
            with self.assertRaises(workspace.RpcError): self.launch(resume=True, messages=('try',))
        self.assertEqual(self.store.load()['id'], FIRST)

    def test_transient_cache_lookup_failure_does_not_download_or_rebuild_environment(self):
        with patch.object(workspace, 'prepare', side_effect=AssertionError('unexpected network build')):
            self.launch(image_failure_once=True)

    def test_empty_new_chat_does_not_replace_last_saved_chat(self):
        self.store.save(self.record(SECOND))
        self.launch()
        self.assertEqual(self.store.load()['id'], SECOND)
        self.assertFalse((self.state / 'chats' / (FIRST + '.json')).exists())

    def test_first_accepted_turn_is_saved_even_if_interrupted_before_completion(self):
        self.launch(messages=('first message',), interrupted=True)
        self.assertEqual(self.store.load()['id'], FIRST)
        self.assertFalse((self.state / 'current.json').exists())

    def test_explicit_resume_of_older_chat_updates_latest_and_can_change_effort(self):
        self.store.save(self.record())
        self.store.save(self.record(SECOND))
        calls, _ = self.launch(resume=FIRST, effort='high')
        self.assertEqual(self.store.load()['id'], FIRST)
        self.assertEqual(self.store.load()['effort'], 'high')
        self.assertEqual(next(p for m, p in calls if m == 'thread/resume')['threadId'], FIRST)

    def test_missing_native_history_never_creates_replacement_chat(self):
        self.store.save(self.record())
        before = (self.state / 'latest-chat.json').read_bytes()
        with self.assertRaisesRegex(workspace.RpcError, 'not found'):
            self.launch(resume=True, fail='thread not found')
        self.assertEqual((self.state / 'latest-chat.json').read_bytes(), before)
        self.assertEqual(self.store.load()['updated_at'], 2)

    def test_wrong_id_model_environment_effort_or_ephemeral_response_cannot_save(self):
        good = {'thread': {'id': FIRST, 'ephemeral': False, 'environments': ENVIRONMENT},
                'model': 'selected', 'reasoningEffort': 'medium'}
        bad = [dict(good, model='other'), dict(good, reasoningEffort='high')]
        for changes in ({'id': SECOND}, {'ephemeral': True}, {'environments': []},
                        {'environments': [{'environmentId': 'local', 'cwd': '/workspace'}]}):
            bad.append(dict(good, thread={**good['thread'], **changes}))
        for response in bad:
            with self.subTest(response=response):
                self.store.save(self.record())
                with self.assertRaises(RuntimeError):
                    self.launch(resume=True, response=response)
                self.assertEqual(self.store.load()['updated_at'], 2)

    def test_hangup_cleans_container_and_retains_chat_and_restores_signal(self):
        self.store.save(self.record())
        previous = signal.getsignal(signal.SIGHUP)
        def hangup(_):
            os.kill(os.getpid(), signal.SIGHUP)
        calls, _ = self.launch(resume=True, messages=hangup)
        self.assertEqual(self.store.load()['id'], FIRST)
        self.assertEqual(signal.getsignal(signal.SIGHUP), previous)
        self.assertFalse((self.state / 'current.json').exists())

    def test_bad_ids_foreign_projects_and_symlinks_cannot_select_history(self):
        for bad in ('../other', '/tmp/x', 'not-an-id'):
            with self.assertRaisesRegex(RuntimeError, 'Invalid chat ID'):
                self.store.load(bad)
        self.store.save(self.record())
        path = self.state / 'chats' / (FIRST + '.json')
        value = json.loads(path.read_text())
        value['project'] = '/another/project'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError, 'does not belong'):
            self.store.load(FIRST)
        path.unlink()
        path.symlink_to(self.root / 'outside')
        with self.assertRaises(RuntimeError):
            self.store.load(FIRST)

    def test_no_saved_chat_has_actionable_error_and_listing_is_empty(self):
        self.assertEqual(self.store.list(), [])
        with self.assertRaisesRegex(RuntimeError, 'No saved chat'):
            self.store.load()

    def test_cli_resume_latest_explicit_and_chats_json(self):
        for arguments, expected in ((['resume'], True), (['resume', FIRST], FIRST),
                                    (['resume', '--agent', 'codex', FIRST, '--effort', 'high'], FIRST)):
            with patch.object(sys, 'argv', ['invariant', *arguments]), patch.object(workspace, 'run') as run:
                cli.main()
                run.assert_called_once_with(None, duration=None, effort='high' if '--effort' in arguments else None, resume=expected)
        with patch.object(sys, 'argv', ['invariant', 'chats', '--json']), patch('invariant.chats.list_chats') as listing:
            cli.main()
            listing.assert_called_once_with(machine=True)

    def test_cli_rejects_ambiguous_or_unrelated_options(self):
        for arguments in (['chats', 'extra'], ['resume', '--json'], ['resume', '--duration', '0'], ['chats', '--agent', 'codex']):
            with self.subTest(arguments=arguments), patch.object(sys, 'argv', ['invariant', *arguments]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.main()


if __name__ == '__main__':
    unittest.main()
