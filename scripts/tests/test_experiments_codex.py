"""M4 native turns and manual/JSON selection use the actual shared interfaces."""
import concurrent.futures
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from invariant import workspace
from invariant.codex_rpc import CodexRpc, RpcCancelled
from invariant.turns import run_turn
from invariant.experiments import cli
from invariant.experiments.errors import ExperimentError
from invariant.experiments.native import CodexRunner, failure_code, usage_record
import test_experiments as base
import test_experiments_execution as execution

THREAD = '01a14bbc-41a3-7a82-a3fb-93379b72fe9b'
CATALOG = [{'id': 'alias', 'model': 'actual', 'isDefault': True,
            'defaultReasoningEffort': 'medium', 'inputModalities': ['text'],
            'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}]}]
USAGE = {'total': {'inputTokens': 10, 'outputTokens': 5, 'totalTokens': 15},
         'last': {'inputTokens': 10, 'outputTokens': 5, 'totalTokens': 15}}


class FakeRpc:
    def __init__(self, *, model='actual', effort='medium', failure=None, account=True, cancel=None):
        self.calls, self.messages = [], []
        self.model, self.effort, self.failure, self.account, self.cancel = model, effort, failure, account, cancel
        self.closed = False
    def initialize(self, **kwargs): pass
    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == 'account/read': return {'account': {'type': 'chatgpt'} if self.account else None}
        if method == 'model/list': return {'data': CATALOG, 'nextCursor': None}
        if method == 'environment/info': return {'cwd': '/workspace'}
        if method == 'thread/start':
            return {'thread': {'id': THREAD, 'ephemeral': False, 'environments': [
                    {**params['environments'][0], 'runtimeWorkspaceRoots': ['/workspace']}]},
                    'model': self.model, 'reasoningEffort': self.effort}
        if method == 'turn/start':
            if self.failure == 'cancel_before_ack': raise RpcCancelled('uncertain acknowledgement')
            if self.cancel is not None: self.cancel.set()
            if self.failure == 'reroute':
                self.messages.append({'method': 'model/rerouted', 'params': {'threadId': THREAD, 'toModel': 'other'}})
            elif self.failure == 'throttle':
                self.messages.append({'method': 'error', 'params': {'threadId': THREAD, 'willRetry': True,
                      'error': {'message': 'capacity', 'codexErrorInfo': 'rateLimitExceeded'}}})
            elif self.failure == 'limit':
                self.messages.append({'method': 'item/agentMessage/delta', 'params': {'threadId': THREAD, 'delta': 'x'*1024}})
            self.messages.extend([
                {'method': 'item/agentMessage/delta', 'params': {'threadId': THREAD, 'itemId': 'answer', 'delta': 'hello synthetic-secret'}},
                {'method': 'turn/completed', 'params': {'threadId': THREAD, 'turn': {'id': 'turn-1',
                  'status': 'failed' if self.failure == 'failed' else 'completed',
                  'error': {'message': 'context exceeded', 'codexErrorInfo': 'contextWindowExceeded'}}}},
                {'method': 'thread/tokenUsage/updated', 'params': {'threadId': THREAD, 'turnId': 'turn-1', 'tokenUsage': USAGE}}])
            return {'turn': {'id': 'turn-1', 'status': 'inProgress'}}
        if method == 'turn/interrupt': return {}
        raise AssertionError(method)
    def receive(self, **kwargs): return self.messages.pop(0) if self.messages else None
    def close(self): self.closed = True


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = SimpleNamespace(project=self.root/'project', image=base.IMAGE, owner_token='owned',
                                       policy=workspace.DEFAULT_CONFIG)
        self.cancel = threading.Event()
    def run_native(self, rpc=None, **options):
        rpc = rpc or FakeRpc()
        (self.root/'runner.log').unlink(missing_ok=True)
        factory = Mock(return_value=rpc)
        info = {'Image': base.IMAGE, 'Mounts': [], 'HostConfig': {'Memory': 1024, 'NanoCpus': 10,
                                                               'PidsLimit': 10, 'NetworkMode': 'none'}}
        native, intents = [], []
        with patch('invariant.experiments.native.workspace.codex_configuration', return_value=(['codex'], {})), \
             patch('invariant.experiments.native.workspace.container_command', return_value=['create']), \
             patch('invariant.experiments.native.workspace.docker'), \
             patch('invariant.experiments.native.owned_info', return_value=info), \
             patch('invariant.experiments.native.workspace.verify_container'), \
             patch('invariant.experiments.native.stop_owned', return_value=True):
            result = CodexRunner(rpc_factory=factory, probe=lambda: {'status': 'ready', 'path': 'codex', 'version': 'tested'}).run(
                self.context, variant={'id': 'v', 'model': 'actual', 'effort': 'medium'},
                prompt='frozen prompt', instructions='frozen instructions', home=self.root/'home',
                timeout=options.pop('timeout', 10), cancel=self.cancel, log_path=self.root/'runner.log',
                limit=options.pop('limit', 65536), secrets=[b'synthetic-secret'], on_native=native.append,
                on_intent=intents.append, **options)
        if factory.called:
            self.assertTrue(rpc.closed)
            self.assertTrue(factory.call_args.kwargs['guard_parent'])
        return result, rpc, native, intents
    def test_observed_identity_usage_frozen_prompt_and_private_remote_tools(self):
        result, rpc, native, intents = self.run_native()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['native']['thread_id'], THREAD)
        self.assertEqual(result['native']['turn_id'], 'turn-1')
        self.assertEqual(result['token_usage']['total']['totalTokens'], 15)
        self.assertIsNone(result['usage_unavailable_reason'])
        self.assertEqual(intents, ['create', 'start', 'model_turn'])
        calls = dict(rpc.calls)
        self.assertFalse(calls['thread/start']['ephemeral'])
        self.assertIn('frozen instructions', calls['thread/start']['developerInstructions'])
        self.assertEqual(calls['turn/start']['input'][0]['text'], 'frozen prompt')
        self.assertEqual(calls['turn/start']['environments'], [{'environmentId': 'invariant', 'cwd': '/workspace'}])
        self.assertNotIn('synthetic-secret', (self.root/'runner.log').read_text())
        self.assertEqual(native[-1]['turn_id'], 'turn-1')
    def test_model_mismatch_fails_before_turn_without_fallback(self):
        result, rpc, *_ = self.run_native(FakeRpc(model='substitute'))
        self.assertEqual(result['reason'], 'MODEL_MISMATCH')
        self.assertNotIn('turn/start', dict(rpc.calls))
    def test_auth_requires_host_sign_in_without_login(self):
        result, rpc, *_ = self.run_native(FakeRpc(account=False))
        self.assertEqual(result['reason'], 'AUTH_REQUIRED')
        self.assertEqual([c[0] for c in rpc.calls], ['account/read'])
    def test_native_errors_do_not_replay_model_turn(self):
        for failure, code in [('throttle', 'THROTTLED'), ('reroute', 'MODEL_MISMATCH'),
                              ('failed', 'MODEL_CONTEXT_LIMIT'), ('limit', 'OUTPUT_LIMIT')]:
            with self.subTest(failure=failure):
                result, rpc, *_ = self.run_native(FakeRpc(failure=failure), limit=256)
                self.assertEqual(result['reason'], code)
                self.assertEqual(sum(m == 'turn/start' for m, _ in rpc.calls), 1)
    def test_cancel_interrupts_accepted_turn_and_closes_backend(self):
        result, rpc, *_ = self.run_native(FakeRpc(cancel=self.cancel))
        self.assertEqual(result['status'], 'cancelled')
        self.assertIn('turn/interrupt', dict(rpc.calls))
    def test_cancellation_before_ack_retains_uncertain_attempt_no_second_turn(self):
        self.cancel.set()
        result, rpc, *_ = self.run_native()
        self.assertEqual(result['reason'], 'CANCELLED')
        self.assertEqual(rpc.calls, [])
    def test_deadline_prevents_dispatch(self):
        result, rpc, *_ = self.run_native(timeout=0)
        self.assertEqual(result['reason'], 'DEADLINE_EXCEEDED')
        self.assertEqual(rpc.calls, [])
    def test_absent_usage_is_unknown_not_zero(self):
        self.assertIsNone(usage_record(None))
        self.assertIsNone(usage_record({'total': {}, 'last': {}}))
        self.assertEqual(failure_code({'codexErrorInfo': 'unauthorized'}), 'AUTH_REQUIRED')
        self.assertEqual(failure_code({'codexErrorInfo': {'httpConnectionFailed': {'httpStatusCode': 429}}}), 'THROTTLED')


class SelectionTests(unittest.TestCase):
    setUp = base.EngineTests.setUp
    unseal = base.EngineTests.unseal
    change = base.EngineTests.change
    submit = base.EngineTests.submit
    configure = execution.ExecutionTests.configure
    controller = execution.ExecutionTests.controller
    def completed(self):
        study = self.configure(); controller, _ = self.controller(); return controller.execute(study['id'])
    def call(self, *args):
        stream = io.StringIO()
        with patch('invariant.experiments.cli.ExperimentEngine', return_value=self.engine), contextlib.redirect_stdout(stream):
            code = cli.main(list(args))
        return code, stream.getvalue()
    def test_human_and_json_clients_share_choice_and_clear_without_source_changes(self):
        study = self.completed(); trial = study['trials'][0]
        before = {str(p): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        code, _ = self.call('select', trial['id'], '--if-revision', '0', '--request-id', 'human-choice')
        self.assertEqual(code, 0)
        code, text = self.call('results', study['id'], '--json')
        selection = json.loads(text)['data']['selection']; self.assertEqual(selection['attempt_id'], trial['attempts'][0]['id'])
        self.assertEqual(selection['revision'], 1)
        code, text = self.call('select', '--clear', study['id'], '--if-revision', '1', '--json', '--request-id', 'assistant-clear')
        self.assertTrue(json.loads(text)['data']['cleared'])
        code, text = self.call('status', study['id']); self.assertIn('Selected: (none) revision: 2', text)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.project.rglob('*') if p.is_file()})
        self.assertIsNone(self.engine.list()['studies'][0]['selection']['attempt_id'])
    def test_selection_cas_race_only_one_wins_replay_does_not_increment(self):
        study = self.completed()
        def choose(i):
            try: return self.engine.select(study['trials'][i]['id'], revision=0, request_id='choose-'+str(i))
            except ExperimentError as error: return error.code
        with concurrent.futures.ThreadPoolExecutor(2) as pool: outcomes = list(pool.map(choose, (0, 1)))
        self.assertEqual(outcomes.count('SELECTION_CONFLICT'), 1)
        winner = next(i for i, outcome in enumerate(outcomes) if isinstance(outcome, dict))
        again = self.engine.select(study['trials'][winner]['id'], revision=0, request_id='choose-'+str(winner))
        self.assertEqual(again, outcomes[winner])
        self.assertEqual(self.engine.inspect(study['id'])['selection']['revision'], 1)
        with self.assertRaises(ExperimentError) as error:
            self.engine.select(study['trials'][winner]['id'], revision=1, request_id='choose-'+str(winner))
        self.assertEqual(error.exception.code, 'REQUEST_CONFLICT')
    def test_incomplete_selection_and_selected_deletion_refused(self):
        study = self.configure()
        with self.assertRaises(ExperimentError) as error: self.engine.select(study['trials'][0]['id'], revision=0)
        self.assertEqual(error.exception.code, 'INCOMPLETE_RESULT')
        controller, _ = self.controller(); study = controller.execute(study['id'])
        self.engine.select(study['trials'][0]['id'], revision=0)
        with self.assertRaises(ExperimentError) as error: self.engine.delete(study['id'], confirm=True)
        self.assertEqual(error.exception.code, 'SELECTED_STATE')
    def test_retry_requires_explicit_attempt_and_allows_quality_false(self):
        study = self.completed(); trial = study['trials'][0]
        self.assertFalse(self.engine.results(study['id'])['trials'][0]['passed'])
        self.engine.select(trial['id'], revision=0)
        self.engine.retry(trial['id'], dispatch=False)
        with self.assertRaises(ExperimentError) as error: self.engine.select(trial['id'], revision=1)
        self.assertEqual(error.exception.code, 'AMBIGUOUS_ATTEMPT')
        result = self.engine.select(trial['attempts'][0]['id'], revision=1)
        self.assertEqual(result['revision'], 2)
    def test_keyed_starter_and_execute_replay_with_no_duplicate_effect(self):
        created = self.engine.init_definition('starter.json', request_id='starter', runner='codex')
        self.assertEqual(created, self.engine.init_definition('starter.json', request_id='starter', runner='codex'))
        recipe = json.loads(Path(created['definition']).read_text())
        self.assertEqual(recipe['runner']['type'], 'codex')
        self.assertTrue((self.project/recipe['cases'][0]['prompt_file']).exists())
        with self.assertRaises(ExperimentError): self.engine.init_definition('different.json', request_id='starter')
        study = self.submit()
        with patch('invariant.experiments.lifecycle.wake') as wake:
            result = self.engine.launch(study['id'], request_id='launch')
            self.assertEqual(result, self.engine.launch(study['id'], request_id='launch'))
            self.assertEqual(wake.call_count, 1)

    def test_native_controller_uses_frozen_files_separate_homes_and_shared_evaluation(self):
        (self.project/'prompt.txt').write_text('captured prompt')
        (self.project/'instructions.txt').write_text('captured instructions')
        (self.project/'evaluate.py').write_text('trusted evaluator')
        self.change(lambda value:value.update(runner={'type':'codex'},
            variants=[{'id':name,'model':'actual','effort':'medium','instructions_file':'instructions.txt'} for name in ('single','planner')],
            cases=[{'id':'task','prompt_file':'prompt.txt'}],repetitions=1,
            evaluator={'argv':['python3','evaluate.py'],'source_paths':['evaluate.py'],'timeout_seconds':10}))
        study=self.submit()
        (self.project/'prompt.txt').write_text('live prompt')
        (self.project/'instructions.txt').write_text('live instructions')
        controller, runner=self.controller()
        homes=[]
        class NativeFixture:
            def run(inner, context, *, prompt, instructions, home, variant, on_native, **options):
                self.assertEqual(prompt,'captured prompt');self.assertEqual(instructions,'captured instructions')
                self.assertIsNone(context.command);homes.append(home)
                value=runner.run(context,**options)
                value.update(native={'thread_id':variant['id'],'home':str(home)},token_usage=USAGE)
                on_native(value['native'])
                return value
        controller.native_runner=NativeFixture()
        complete=controller.execute(study['id'])
        self.assertEqual(complete['state'],'completed')
        self.assertEqual(len(set(homes)),2)
        result=self.engine.results(study['id'])
        self.assertEqual(result['summary']['quality_passes'],1)
        self.assertEqual(result['trials'][0]['token_usage'],USAGE)


class GuardTests(unittest.TestCase):
    def test_guard_pipe_closure_kills_native_child(self):
        # Use a real child rather than a mocked process to prove the orphan guard.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'child.pid'
            code = 'import os,pathlib,time;pathlib.Path('+repr(str(path))+').write_text(str(os.getpid()));time.sleep(60)'
            rpc = CodexRpc([sys.executable, '-I', '-c', code], env=os.environ.copy(), cwd=directory, guard_parent=True)
            try:
                end = time.monotonic()+5
                while not path.exists() and time.monotonic()<end: time.sleep(.01)
                self.assertTrue(path.exists()); pid = int(path.read_text())
                # EOF is exactly what happens when the owner dies without finally.
                os.close(rpc.guard); rpc.guard = None
                rpc.process.wait(timeout=5)
                end = time.monotonic()+5
                while time.monotonic()<end:
                    process = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True)
                    if process.returncode or process.stdout.strip().startswith('Z'): break
                    time.sleep(.01)
                else: self.fail('Native child survived owner pipe closure')
            finally: rpc.close()
