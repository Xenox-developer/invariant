"""M0/M1 behavior through execution contexts and the shared experiment engine."""
import concurrent.futures
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from invariant import environment, workspace
from invariant.execution import ExecutionContext, project_lock
from invariant.experiments import cli
from invariant.experiments.definitions import digest, expand, parse_json, validate
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.errors import ExperimentError
from invariant.experiments.runtime import allocations, resolve_models, secret_files
from invariant.experiments.snapshots import SourceCapture, glob_match, unseal_tree
from invariant.policy import normalize

IMAGE = 'sha256:' + '1' * 64


def recipe():
    return {'schema_version': 1, 'name': 'compare',
            'runner': {'type': 'command', 'argv': ['python3', 'agent.py']},
            'variants': [{'id': 'single', 'parameters': {'steps': 1}},
                         {'id': 'planner', 'parameters': {'steps': 3}}],
            'cases': [{'id': 'task', 'input_file': 'cases/task.json'}],
            'execution': {'max_parallel': 2}, 'repetitions': 2}


class FakeRuntime:
    def __init__(self):
        self.calls = 0

    def resolve(self, project, state, definition, policy):
        self.calls += 1
        effective = normalize({**policy, 'resource_mode': 'manual',
                               'limits': {**policy['limits'], 'cpus': 2, 'memory_mib': 1024}})
        return {'image': {'id': IMAGE, 'repo_digests': [], 'os': 'linux', 'architecture': 'arm64'},
                'runtime': {'adapter_version': 1}, 'capacity': {'cpus': 8, 'memory_bytes': 8 * 1024**3},
                'policy': effective, 'resource_sizing': {'mode': 'manual'},
                'variants': copy.deepcopy(definition['variants'])}


class DefinitionTests(unittest.TestCase):
    def test_expansion_is_deterministic_and_nonmutating(self):
        definition = validate(recipe())
        trials = expand(definition)
        self.assertEqual(len(trials), 4)
        self.assertEqual(trials, expand(definition))
        self.assertEqual(trials[0], {'variant_id': 'single', 'case_id': 'task', 'repetition': 1})
        self.assertEqual(digest(definition), digest(copy.deepcopy(definition)))

    def test_strict_invalid_definitions(self):
        mutations = [lambda value: value.update(schema_version=True),
                     lambda value: value.update(typo='ignored?'),
                     lambda value: value['runner'].update(type='claude'),
                     lambda value: value['variants'][0].update(model='wrong-adapter'),
                     lambda value: value['cases'][0].update(input_file='../outside'),
                     lambda value: value['variants'].append(value['variants'][0]),
                     lambda value: value.update(repetitions=1000),
                     lambda value: value['execution'].update(trial_memory_mib=True),
                     lambda value: value['execution'].update(cpu_budget=10**300),
                     lambda value: value['variants'][0]['parameters'].update(score=float('inf'))]
        for mutate in mutations:
            value = recipe(); mutate(value)
            with self.subTest(value=value), self.assertRaises(ExperimentError):
                validate(value)

    def test_nonfinite_and_duplicate_json_fail(self):
        for raw in ('{"schema_version":1,"schema_version":1}', '{"x":NaN}', '{"x":1e999}', '{"x":{"y":1,"y":2}}'):
            with self.subTest(raw=raw), self.assertRaises(ExperimentError):
                parse_json(raw)

    def test_portable_globs_include_root_files(self):
        self.assertTrue(glob_match('agent.py', '**/*'))
        self.assertTrue(glob_match('src/lib/agent.py', '**/*.py'))
        self.assertFalse(glob_match('src/lib/agent.py', 'src/*.py'))

    def test_models_use_catalog_defaults_and_reject_fallbacks(self):
        catalog = [{'id': 'alias', 'model': 'actual', 'isDefault': True,
                    'defaultReasoningEffort': 'medium', 'inputModalities': ['text'],
                    'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}, {'reasoningEffort': 'high'}]}]
        self.assertEqual(resolve_models([{'id': 'a'}], catalog)[0]['model'], 'actual')
        self.assertEqual(resolve_models([{'id': 'a'}], catalog)[0]['effort'], 'medium')
        self.assertEqual(resolve_models([{'id': 'a', 'model': 'alias', 'effort': 'high'}], catalog)[0]['model'], 'actual')
        for variant in ({'id': 'a', 'model': 'missing'}, {'id': 'a', 'effort': 'ultra'}):
            with self.assertRaises(ExperimentError):
                resolve_models([variant], catalog)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.state = self.root / 'state'
        self.runtime = FakeRuntime()
        with contextlib.redirect_stdout(io.StringIO()):
            workspace.init(self.project)
        (self.project / 'agent.py').write_text('print("original")\n')
        (self.project / 'cases').mkdir()
        (self.project / 'cases/task.json').write_text('{"task":"original"}\n')
        self.definition = self.project / 'experiments.json'
        self.definition.write_text(json.dumps(recipe()))
        self.engine = ExperimentEngine(self.project, runtime=self.runtime, state=self.state)
        self.addCleanup(self.unseal)

    def unseal(self):
        if self.state.exists():
            unseal_tree(self.state)

    def change(self, update):
        value = json.loads(self.definition.read_text())
        update(value)
        self.definition.write_text(json.dumps(value))

    def submit(self, **kwargs):
        return self.engine.submit('experiments.json', **kwargs)

    def test_validation_creates_no_study_or_database(self):
        result = self.engine.validate('experiments.json')
        self.assertTrue(result['valid'])
        self.assertEqual(result['trial_count'], 4)
        self.assertTrue(result['dispatch_available'])
        self.assertFalse(self.engine.store.path.exists())

    def test_study_is_durable_frozen_and_queued_without_git(self):
        (self.project / '.env').write_text('SYNTHETIC_SECRET=not-for-capture')
        result = self.submit(request_id='request-1')
        self.assertEqual(result['state'], 'queued')
        self.assertEqual(len(result['trials']), 4)
        self.assertTrue(result['dispatch_available'])
        baseline = self.engine.root / 'baselines' / result['baseline_id']
        self.assertEqual((baseline / 'workspace/agent.py').read_text(), 'print("original")\n')
        self.assertFalse((baseline / 'workspace/.invariant').exists())
        self.assertFalse((baseline / 'workspace/.env').exists())
        self.assertEqual(result['frozen']['image']['id'], IMAGE)
        self.assertEqual(result['frozen']['execution']['max_parallel'], 2)
        self.assertEqual((self.engine.store.path.stat().st_mode & 0o777), 0o600)
        self.assertFalse((self.state / 'environment.json').exists())
        self.assertFalse((self.state / 'launch.json').exists())
        (self.project / 'agent.py').write_text('changed after launch')
        (self.project / 'cases/task.json').write_text('{"task":"new"}')
        other = ExperimentEngine(self.project, runtime=FakeRuntime(), state=self.state)
        saved = other.inspect(request_id='request-1')
        self.assertEqual(saved['id'], result['id'])
        self.assertEqual(saved['frozen'], result['frozen'])
        self.assertEqual((baseline / 'inputs/cases/task.json').read_text(), '{"task":"original"}\n')
        self.assertEqual(other.inspect(saved['trials'][0]['id'])['kind'], 'trial')
        self.assertEqual(other.inspect(saved['trials'][0]['attempts'][0]['id'])['kind'], 'attempt')

    def test_idempotent_replay_does_not_reprepare_or_recapture(self):
        first = self.submit(request_id='once')
        (self.project / 'agent.py').write_text('different source')
        self.runtime.resolve = lambda *args: self.fail('idempotent replay prepared again')
        second = self.submit(request_id='once')
        self.assertEqual(first['id'], second['id'])
        self.assertTrue(second['idempotent_replay'])
        self.assertEqual(len(self.engine.list()['studies']), 1)
        self.change(lambda value: value['variants'][0]['parameters'].update(steps=10))
        with self.assertRaisesRegex(ExperimentError, 'different request content'):
            self.submit(request_id='once')

    def test_concurrent_same_request_creates_one_study(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.submit, request_id='simultaneous') for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(results[0]['id'], results[1]['id'])
        self.assertEqual(len(self.engine.list()['studies']), 1)
        self.assertEqual(len(list((self.engine.root / 'studies').iterdir())), 1)

    def test_active_chat_lock_and_stale_recovery_block_capture(self):
        lock = project_lock(self.state)
        try:
            with patch('invariant.experiments.engine.project_lock', side_effect=RuntimeError('active chat')):
                with self.assertRaisesRegex(ExperimentError, 'active chat'):
                    self.submit()
        finally:
            lock.close()
        workspace.private_write(self.state / 'current.json', {'session': 'ws-' + '0'*32})
        with self.assertRaisesRegex(ExperimentError, 'current/recovery record'):
            self.submit()
        self.assertEqual(self.engine.list()['studies'], [])

    def test_source_changed_during_capture_is_not_committed(self):
        scan = SourceCapture.scan
        calls = 0
        def changed(capture, *args, **kwargs):
            nonlocal calls
            result = scan(capture, *args, **kwargs)
            calls += 1
            if calls == 1:
                (self.project / 'agent.py').write_text('changed mid-capture')
            return result
        with patch.object(SourceCapture, 'scan', changed), self.assertRaisesRegex(ExperimentError, 'changed during capture'):
            self.submit()
        self.assertEqual(self.engine.list()['studies'], [])
        self.assertEqual(len(list((self.engine.root / 'preparation-failures').iterdir())), 1)

    def test_declared_inputs_are_captured_outside_workspace_selection(self):
        self.change(lambda value: value.update(workspace={'include': ['agent.py'], 'exclude': ['cases/**']}))
        result = self.submit()
        baseline = self.engine.root / 'baselines' / result['baseline_id']
        self.assertTrue((baseline / 'inputs/cases/task.json').is_file())
        self.assertFalse((baseline / 'workspace/cases/task.json').exists())

    def test_missing_inputs_unsafe_links_and_special_files_fail_before_commit(self):
        for setup, cleanup in (
            (lambda: (self.project/'cases/task.json').unlink(), lambda: (self.project/'cases/task.json').write_text('{}')),
            (lambda: (self.project/'escape').symlink_to('../outside'), lambda: (self.project/'escape').unlink()),
            (lambda: os.mkfifo(self.project/'fifo'), lambda: (self.project/'fifo').unlink())):
            setup()
            try:
                with self.assertRaises(ExperimentError): self.submit()
                self.assertEqual(self.engine.list()['studies'], [])
            finally: cleanup()

    def test_safe_internal_symlink_is_preserved_without_following_it(self):
        (self.project/'agent-link.py').symlink_to('agent.py')
        result = self.submit()
        link = self.engine.root/'baselines'/result['baseline_id']/'workspace/agent-link.py'
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), 'agent.py')

    def test_excluded_directory_stays_excluded_when_an_input_inside_is_captured(self):
        self.change(lambda value: value.update(workspace={'exclude':['cases']}))
        saved = self.submit()
        baseline = self.engine.root/'baselines'/saved['baseline_id']
        self.assertTrue((baseline/'inputs/cases/task.json').is_file())
        self.assertFalse((baseline/'workspace/cases/task.json').exists())

    def test_frozen_patch_is_checked_without_changing_source(self):
        patch_file = self.project/'variant.patch'
        patch_file.write_text('--- a/agent.py\n+++ b/agent.py\n@@ -1 +1 @@\n-print("original")\n+print("variant")\n')
        self.change(lambda value: value['variants'][0].update(patch_file='variant.patch'))
        result = self.submit()
        self.assertEqual(result['frozen']['input_checks']['variant_changed_paths']['single'], ['agent.py'])
        self.assertEqual((self.project/'agent.py').read_text(), 'print("original")\n')
        patch_file.write_text('--- a/agent.py\n+++ b/agent.py\n@@ -1 +1 @@\n-wrong context\n+new\n')
        with self.assertRaisesRegex(ExperimentError, 'context conflicts'):
            self.submit()

    def test_fixture_cannot_overlap_variant_patch_or_escape(self):
        (self.project/'variant.patch').write_text('--- a/agent.py\n+++ b/agent.py\n@@ -1 +1 @@\n-print("original")\n+new\n')
        self.change(lambda value: value['variants'][0].update(patch_file='variant.patch'))
        self.change(lambda value: value['cases'][0].update(fixtures=[{'source':'cases/task.json','destination':'agent.py'}]))
        with self.assertRaisesRegex(ExperimentError, 'conflicts with'):
            self.submit()

    def test_sql_failure_does_not_acknowledge_a_partial_study_and_retry_is_safe(self):
        with patch.object(self.engine.store, 'create', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                self.submit(request_id='recoverable')
        self.assertEqual(self.engine.list()['studies'], [])
        failures = list((self.engine.root/'preparation-failures').glob('*.json'))
        self.assertEqual(len(failures), 1)
        result = self.submit(request_id='recoverable')
        self.assertEqual(result['state'], 'queued')
        self.assertEqual(len(self.engine.list()['studies']), 1)

    def test_resource_request_cannot_expand_policy_or_ignore_capacity(self):
        self.change(lambda value: value['execution'].update(trial_memory_mib=2048))
        with self.assertRaisesRegex(ExperimentError, 'exceed'):
            self.submit()
        definition = validate(recipe())
        prepared = self.runtime.resolve(self.project, self.state, definition, workspace.load_project(self.project))
        definition['execution']['max_parallel'] = 128
        self.assertEqual(allocations(definition, prepared)['max_parallel'], 3)
        prepared['capacity']['memory_bytes'] = 128 * 1024**2
        with self.assertRaises(ExperimentError): allocations(definition, prepared)

    def test_secret_references_are_files_and_never_captured(self):
        secret = self.project/'private-key.txt'; secret.write_text('synthetic-token'); secret.chmod(0o600)
        self.change(lambda value: value.update(secret_refs={'provider': 'TEST'}))
        with patch.dict(os.environ, {'INVARIANT_SECRET_TEST': str(secret)}):
            result = self.submit()
        baseline = self.engine.root/'baselines'/result['baseline_id']
        self.assertFalse((baseline/'workspace/private-key.txt').exists())
        self.assertNotIn('synthetic-token', json.dumps(result))
        self.assertNotIn(str(secret), json.dumps(result))
        secret.chmod(0o644)
        with self.assertRaises(ExperimentError): secret_files({'secret_refs':{'p':'TEST'}}, {'INVARIANT_SECRET_TEST': str(secret)})

    def test_missing_or_changed_saved_metadata_is_not_reported_as_valid(self):
        saved = self.submit()
        path = self.engine.root/'studies'/saved['id']/'definition.json'
        path.chmod(0o600); path.write_text('{}')
        with self.assertRaisesRegex(ExperimentError, 'missing or differ'):
            self.engine.inspect(saved['id'])

    def test_foreign_or_incompatible_database_fails_explicitly(self):
        self.submit()
        other = self.root/'other'; other.mkdir()
        with contextlib.redirect_stdout(io.StringIO()): workspace.init(other)
        foreign = ExperimentEngine(other, runtime=self.runtime, state=self.state)
        with self.assertRaisesRegex(ExperimentError, 'another project'):
            foreign.list()
        with sqlite3.connect(self.engine.store.path) as connection:
            connection.execute('PRAGMA user_version=99')
        with self.assertRaisesRegex(ExperimentError, 'schema is not supported'):
            self.engine.list()

    def test_fixture_parent_file_is_rejected(self):
        self.change(lambda value: value['cases'][0].update(fixtures=[{'source':'cases/task.json','destination':'agent.py/input'}]))
        with self.assertRaisesRegex(ExperimentError, 'parent is not a directory'):
            self.submit()

    def test_init_is_shareable_and_does_not_overwrite(self):
        first = self.engine.init_definition('experiments/compare.json')
        self.assertTrue(Path(first['definition']).is_file())
        validate(parse_json(Path(first['definition']).read_text()))
        with self.assertRaisesRegex(ExperimentError, 'nothing was replaced'):
            self.engine.init_definition('experiments/compare.json')

    def test_cli_machine_readable_errors_and_cross_client_state(self):
        with patch('invariant.experiments.cli.ExperimentEngine', return_value=self.engine):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(['run','experiments.json','--request-id','mixed','--create-only','--json']), 0)
            study = json.loads(output.getvalue())['data']
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(['status',study['id']]), 0)
            self.assertIn(study['id'], output.getvalue())
            for arguments in (['unknown','--json'], ['run','--json'], ['status','--json'], ['run','experiments.json','--request-id','../bad','--json']):
                with self.subTest(arguments=arguments), contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertNotEqual(cli.main(arguments), 0)
                    self.assertFalse(json.loads(output.getvalue())['ok'])


class ContextTests(unittest.TestCase):
    setUp = EngineTests.setUp
    unseal = EngineTests.unseal

    def test_context_mounts_private_workspace_and_copies_policy(self):
        policy = workspace.load_project(self.project)
        writable = self.root/'trial-workspace'; writable.mkdir()
        policy_dir = self.root/'trial-policy'; policy_dir.mkdir()
        events = []
        context = ExecutionContext.create(project=self.project, workspace=writable,
                    state=self.root/'trial-state', image=IMAGE, owner_token='trial-container', policy=policy,
                    policy_directory=policy_dir, control_directory=self.root/'protected-control',
                    checkpoint_owner='attempt-123', event_sink=lambda event, **facts: events.append((event, facts)))
        policy['limits']['memory_mib'] = 999
        self.assertNotEqual(context.policy['limits']['memory_mib'], 999)
        arguments = workspace.container_command('trial-container', self.project, IMAGE, context=context)
        self.assertIn('type=bind,src='+str(writable)+',dst=/workspace', arguments)
        self.assertNotIn('type=bind,src='+str(self.project)+',dst=/workspace', arguments)
        self.assertIn('type=bind,src='+str(policy_dir)+',dst=/workspace/.invariant,readonly', arguments)
        self.assertIn('--label=io.invariant.owner=attempt-123', arguments)
        context.emit('created', image=IMAGE)
        self.assertEqual(events, [('created', {'image':IMAGE})])
        self.assertEqual(context.checkpoint_path, self.root/'trial-state/environment.json')
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            workspace.container_command('foreign', self.project, IMAGE, context=context)


if __name__ == '__main__':
    unittest.main()
