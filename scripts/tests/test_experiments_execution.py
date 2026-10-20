"""M2 scheduler, evaluator and evidence semantics; real Docker is tested separately."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch, Mock

from invariant import workspace
from invariant.experiments.controller import Controller
from invariant.experiments.errors import ExperimentError
from invariant.experiments.evaluation import parse_result
from invariant.experiments.runners import BoundedLog, Redactor, CommandRunner, reclaim_private
from types import SimpleNamespace
import tempfile
import test_experiments as base
IMAGE = base.IMAGE


class FixtureRunner:
    def __init__(self, *, fail=False):
        self.active = self.maximum = self.calls = 0
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(2)
        self.fail = fail

    def run(self, context, *, log_path, on_started=None, **kwargs):
        started = time.time()
        if on_started: on_started()
        if context.working_directory == '/workspace':
            with self.lock:
                self.active += 1; self.calls += 1
                self.maximum = max(self.maximum, self.active)
                call = self.calls
            if call <= 2: self.barrier.wait(timeout=5)
            inputs = dict((destination, source) for source, destination in context.readonly_mounts)['/__invariant_inputs/run']
            variant = json.loads((inputs/'variant.json').read_text())
            output = context.workspace/'outputs';output.mkdir()
            (output/'answer.json').write_text(json.dumps({'variant':variant['id']}))
            (context.workspace/'evaluate.py').write_text('candidate modified evaluator')
            time.sleep(0.02)
            with self.lock: self.active -= 1
            status = 'failed' if self.fail and variant['id']=='single' else 'completed'
            Path(log_path).write_text('command output\n')
        else:
            mounts = {destination: source for source, destination in context.readonly_mounts}
            assert 'trusted evaluator' in (mounts['/__invariant_inputs/source']/'evaluate.py').read_text()
            variant = json.loads((context.workspace/'outputs/answer.json').read_text())['variant']
            (context.workspace/'evaluation-mutated.txt').write_text('only in evaluation copy')
            Path(log_path).write_text(json.dumps({'schema_version':1,'passed':variant=='planner',
                                                 'metrics':{'score':1 if variant=='planner' else 0},'artifacts':[]}))
            status = 'completed'
        return {'status':status,'exit_code':0 if status=='completed' else 2,
                'reason':None if status=='completed' else 'PROCESS_EXIT',
                'started_at':started,'finished_at':time.time(),'duration_seconds':time.time()-started,
                'cleanup_confirmed':True,'log_truncated':False,'container':context.owner_token}


class ExecutionTests(unittest.TestCase):
    setUp = base.EngineTests.setUp
    unseal = base.EngineTests.unseal
    change = base.EngineTests.change
    submit = base.EngineTests.submit

    def configure(self):
        (self.project/'evaluate.py').write_text('trusted evaluator\n')
        self.change(lambda value:value.update(evaluator={'argv':['python3','evaluate.py'],
                                'source_paths':['evaluate.py'],'timeout_seconds':10}))
        return self.submit()

    def controller(self, *, fail=False):
        runner = FixtureRunner(fail=fail)
        controller = Controller(self.engine, runner=runner)
        def docker(*args, **kwargs):
            if args[0]=='info':
                return json.dumps({'OSType':'linux','NCPU':8,'MemTotal':8*1024**3})
            if args[:2]==('image','inspect'):
                return json.dumps([{'Id':IMAGE,'Os':'linux','Size':100}])
            raise AssertionError(args)
        def save(context, name):
            workspace.private_write(context.checkpoint_path,{'schema':1,'project':str(self.project),
                                      'image':IMAGE,'owner':context.checkpoint_owner})
        patches = [patch('invariant.experiments.controller.workspace.docker', side_effect=docker),
                   patch('invariant.experiments.controller.environment.prepare_control', side_effect=lambda project,image,*,path:path.parent.mkdir(parents=True,exist_ok=True)),
                   patch('invariant.experiments.controller.stop_owned', return_value=True),
                   patch('invariant.experiments.controller.workspace.remove_owned'),
                   patch('invariant.execution.ExecutionContext.preserve',save)]
        stack = contextlib.ExitStack()
        for item in patches: stack.enter_context(item)
        self.addCleanup(stack.close)
        return controller, runner

    def test_parallel_private_trials_evaluate_captured_sources_and_compare(self):
        study = self.configure()
        controller, runner = self.controller()
        complete = controller.execute(study['id'])
        self.assertEqual(complete['state'],'completed')
        self.assertEqual(runner.maximum,2)
        results = self.engine.results(study['id'])
        self.assertEqual(results['summary']['scheduled'],4)
        self.assertEqual(results['summary']['runner_successes'],4)
        self.assertEqual(results['summary']['quality_passes'],2)
        self.assertEqual(results['summary']['quality_failures'],2)
        self.assertEqual(results['summary']['failed'],0)
        self.assertEqual((self.project/'evaluate.py').read_text(),'trusted evaluator\n')
        self.assertFalse((self.project/'outputs').exists())
        for trial in results['trials']:
            self.assertEqual(trial['state'],'succeeded')
            snapshot=Path(trial['capture']['workspace'])
            self.assertFalse((snapshot/'evaluation-mutated.txt').exists())
            self.assertEqual(json.loads((snapshot/'outputs/answer.json').read_text())['variant'],trial['variant_id'])
            self.assertTrue(trial['checkpoint']['complete'])
        self.assertEqual(controller.execute(study['id'])['state'],'completed')
        self.assertEqual(runner.calls,4)  # completed execute is idempotent, never replayed
        self.assertEqual(len(self.engine.logs(study['id'])['logs']),8)

    def test_runner_failures_stay_in_denominators_and_do_not_stop_siblings(self):
        study=self.configure();controller,_=self.controller(fail=True)
        controller.execute(study['id'])
        results=self.engine.results(study['id'])
        self.assertEqual(results['summary']['failed'],2, [(t['state'],[(a['reason'],a['record'].get('error_type')) for a in t['attempts']]) for t in self.engine.inspect(study['id'])['trials']])
        self.assertEqual(results['summary']['evaluated'],2)
        self.assertEqual(results['summary']['missing_quality'],2)
        self.assertEqual(results['summary']['quality_pass_rate_over_scheduled'],0.5)
        failed=next(row for row in results['variants'] if row['variant_id']=='single')
        self.assertEqual(failed['quality_judged'],0)
        self.assertNotIn('score',failed['metrics'])

    def test_variant_patch_and_case_fixture_only_change_private_workspaces(self):
        (self.project/'variant.patch').write_text('--- /dev/null\n+++ b/variant.txt\n@@ -0,0 +1 @@\n+patched-marker\n')
        self.change(lambda value:value['variants'][0].update(patch_file='variant.patch'))
        self.change(lambda value:value['cases'][0].update(fixtures=[{'source':'cases/task.json','destination':'fixture.json'}]))
        study=self.configure();controller,_=self.controller();controller.execute(study['id'])
        for row in self.engine.results(study['id'])['trials']:
            snapshot=Path(row['capture']['workspace'])
            self.assertTrue((snapshot/'fixture.json').exists())
            self.assertEqual((snapshot/'variant.txt').exists(),row['variant_id']=='single')
        self.assertFalse((self.project/'variant.txt').exists())
        self.assertFalse((self.project/'fixture.json').exists())

    def test_unfinished_attempt_blocks_reexecution(self):
        study=self.configure();controller,_=self.controller()
        self.engine.store.claim(study['id'],study['trials'][0]['id'],'lost-controller')
        with self.assertRaisesRegex(ExperimentError,'unfinished'):
            controller.execute(study['id'])

    def test_m1_database_migrates_without_mutating_frozen_definition(self):
        study=self.configure()
        with sqlite3.connect(self.engine.store.path) as connection:
            connection.execute('ALTER TABLE attempts DROP COLUMN record')
            connection.execute('PRAGMA user_version=1')
        self.assertEqual(self.engine.inspect(study['id'])['frozen'],study['frozen'])
        controller,_=self.controller();controller.execute(study['id'])
        with sqlite3.connect(self.engine.store.path) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],5)
        self.assertEqual(self.engine.inspect(study['id'])['frozen'],study['frozen'])

    def test_cancel_during_docker_start_rechecks_until_process_stops(self):
        read_fd, write_fd = os.pipe()
        process = Mock()
        process.stdout = os.fdopen(read_fd, 'rb', buffering=0)
        process.returncode = 0
        done = threading.Event()
        def finish():
            if not done.is_set():
                done.set(); os.close(write_fd)
        process.poll.side_effect = lambda: 0 if done.is_set() else None
        process.wait.return_value = 0
        process.kill.side_effect = finish
        process.terminate.side_effect = finish
        calls = []
        def stop(context):
            calls.append('stop')
            # First inspect sees a created, not-yet-running container. Docker's
            # in-flight start is acknowledged later and needs another stop check.
            if len(calls) > 1: finish()
            return True
        timer = threading.Timer(.35, finish);timer.start()
        context = Mock();context.owner_token='owned';context.project=self.project;context.image=IMAGE
        context.workspace=self.project;context.output_directory=None
        cancel=threading.Event()
        info={'Image':IMAGE,'Mounts':[],'HostConfig':{'Memory':1,'NanoCpus':1,'PidsLimit':32,'NetworkMode':'none'},
              'State':{'Running':False,'ExitCode':0}}
        try:
            with (patch('invariant.experiments.runners.workspace.docker'),
                  patch('invariant.experiments.runners.workspace.container_command',return_value=['create']),
                  patch('invariant.experiments.runners.workspace.verify_container'),
                  patch('invariant.experiments.runners.owned_info',return_value=info),
                  patch('invariant.experiments.runners.stop_owned',side_effect=stop),
                  patch('invariant.experiments.runners.find_docker',return_value='/usr/bin/docker'),
                  patch('invariant.experiments.runners.subprocess.Popen',side_effect=lambda *a,**kw:(cancel.set(),process)[1])):
                result=CommandRunner().run(context,timeout=10,cancel=cancel,log_path=self.root/'cancel.log',limit=1024)
            self.assertEqual(result['status'],'cancelled')
            self.assertGreaterEqual(len(calls),2)
        finally:
            timer.cancel();finish()

    def test_log_bounds_and_secret_redaction_across_chunks(self):
        path=self.root/'bounded.log'
        log=BoundedLog(path,20,[b'super-secret'])
        log.write(b'prefix super-'); log.write(b'secret suffix extra data'); log.close()
        self.assertNotIn(b'super-secret',path.read_bytes())
        self.assertIn(b'[REDACTED]',path.read_bytes())
        self.assertLessEqual(path.stat().st_size,20)
        self.assertTrue(log.truncated)
        redactor=Redactor([b'abcd',b'ab'])
        actual=redactor.feed(b'xxa')+redactor.feed(b'bcdyy')+redactor.feed(b'',final=True)
        self.assertEqual(actual,b'xx[REDACTED]yy')


class EvaluationTests(unittest.TestCase):
    def test_result_validation(self):
        self.assertFalse(parse_result('{"schema_version":1,"passed":false,"metrics":{"score":0}}')['passed'])
        for output in ('not-json','{}','{"schema_version":1,"passed":"yes"}',
                       '{"schema_version":1,"metrics":{"score":true}}',
                       '{"schema_version":1,"metrics":{"score":NaN}}',
                       '{"schema_version":1,"artifacts":["../escape"]}',
                       '{"schema_version":1,"metrics":{"a":1,"a":2}}'):
            with self.subTest(output=output), self.assertRaises(ExperimentError):parse_result(output)


class LinuxOwnershipTests(unittest.TestCase):
    def test_foreign_private_files_use_only_a_stopped_scoped_static_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);private=root/'state';private.mkdir()
            writable=private/'workspace';writable.mkdir();(writable/'answer').write_text('42')
            control=private/'control';control.mkdir()
            context=SimpleNamespace(project=root/'project',workspace=writable,output_directory=None,
                checkpoint_owner='attempt-owned-runner',owner_token='owned',control_directory=control,image=IMAGE)
            def docker(*args,**kwargs):
                if args[0]=='inspect':raise RuntimeError('No such container')
                return '{"reclaimed":true}'
            with (patch('invariant.experiments.runners.sys.platform','linux'),
                  patch('invariant.experiments.runners.os.getuid',return_value=4242),
                  patch.object(workspace,'state_for',return_value=private),
                  patch('invariant.experiments.runners.owned_info',return_value={'State':{'Running':False}}) as info,
                  patch('invariant.environment.verify_control'),
                  patch.object(workspace,'docker',side_effect=docker) as commands):
                reclaim_private(context)
                args=commands.call_args_list[0].args
                self.assertIn('--network=none',args);self.assertIn('--read-only',args)
                self.assertIn('--entrypoint=/__invariant/inspect',args)
                self.assertEqual({a for a in args if a.startswith('--cap-add=')},{'--cap-add=CHOWN','--cap-add=DAC_OVERRIDE'})
                self.assertIn('type=bind,src='+str(writable)+',dst=/workspace',args)
                self.assertNotIn('type=bind,src='+str(context.project)+',dst=/workspace',args)
                info.return_value={'State':{'Running':True}}
                before=commands.call_count
                with self.assertRaisesRegex(RuntimeError,'Stop the private executor'):reclaim_private(context)
                self.assertEqual(commands.call_count,before)
                context.workspace=context.project;context.project.mkdir()
                with self.assertRaisesRegex(RuntimeError,'outside private'):reclaim_private(context)

    def test_owned_files_need_no_privileged_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            context=SimpleNamespace(workspace=Path(temporary),output_directory=None)
            with patch('invariant.experiments.runners.sys.platform','linux'),patch.object(workspace,'docker') as docker:
                reclaim_private(context)
                docker.assert_not_called()


if __name__=='__main__':unittest.main()
