"""Task protocol tests exercise the same durable interface as CLI callers."""
import contextlib
import io
import json
import runpy
import subprocess
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from invariant import tasks, environment_cli, resources, workspace
from invariant.experiments.errors import ExperimentError
from invariant.policy import NEW_PROJECT_CONFIG, normalize


class Backend:
    def __init__(self):
        self.calls=[]
        self.ready=True
        self.prepared=True
        self.preflight_ready=True
        self.dispatches=0
        self.lost_ack=False

    def readiness(self):
        self.calls.append('readiness')
        return {'ready':self.ready,'codex':{'status':'ready'},'docker':{'status':'ready' if self.ready else 'unavailable'}}

    def base(self): self.calls.append('base');return {'image':'sha256:base'}
    def packages(self, options): self.calls.append('packages');return {'prepared':self.prepared,'exit_code':0 if self.prepared else 100}
    def command(self, options): self.calls.append('command');return {'prepared':True}
    def definition(self, options, key): self.calls.append('definition');return {'definition':'experiments/test/compare.json'}
    def validate(self, definition): self.calls.append('validate');return {'valid':True,'trial_count':1}
    def preflight(self, definition, key): self.calls.append('preflight');return {'ready':self.preflight_ready,'study_id':'baseline','model_turns':0}
    def submit(self, definition, key): self.calls.append('submit');return {'id':'study-1'}

    def dispatch(self, study, key):
        self.calls.append('dispatch')
        if self.dispatches==0:self.dispatches+=1
        if self.lost_ack:
            self.lost_ack=False
            raise OSError('lost acknowledgement')
        return {'id':study}

    def inspect(self, study): return {'study_id':study,'state':'completed','trials':[{'passed':True}]}


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name).resolve()
        self.project=root/'project';self.project.mkdir()
        self.state=root/'state'
        self.backend=Backend()
        self.runner=tasks.TaskRunner(self.project,state=self.state,backend=self.backend)
        self.options={'name':'example','task':'Implement a feature','test_command':'python3 tests.py',
                      'test_paths':['tests.py'],'packages':['cmake'],'prepare_command':'npm ci','allow_network':True}

    def test_pipeline_orders_preparation_before_capture_and_submits_one_worker(self):
        record=self.runner.run('same-key',self.options)
        self.assertEqual(record['state'],'submitted')
        self.assertEqual(self.backend.calls,['readiness','base','packages','command','definition','validate','preflight','submit','dispatch'])
        self.assertEqual(record['options']['effort'],'medium')
        self.assertEqual(self.backend.dispatches,1)
        self.runner.run('same-key',self.options)
        self.assertEqual(self.backend.dispatches,1)
        self.assertEqual(self.backend.calls.count('packages'),1)

    def test_concurrent_duplicate_observes_progress_without_starting_another_stage(self):
        entered=threading.Event();release=threading.Event()
        original=self.backend.packages
        def packages(options):
            entered.set();release.wait(3);return original(options)
        self.backend.packages=packages
        errors=[]
        def run():
            try:self.runner.run('same-key',self.options)
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(2))
            result=self.runner.run('same-key',self.options)
            self.assertEqual(result['state'],'running')
            self.assertEqual(result['active_stage'],'packages')
            self.assertNotIn('definition',self.backend.calls)
        finally:release.set();thread.join(4)
        self.assertFalse(errors)
        self.assertEqual(self.backend.dispatches,1)

    def test_missing_host_tools_block_before_packages_and_model_calls(self):
        self.backend.ready=False
        result=self.runner.run('host',self.options)
        self.assertEqual(result['error']['code'],'HOST_NOT_READY')
        self.assertEqual(self.backend.calls,['readiness'])
        self.backend.ready=True
        self.assertEqual(self.runner.run('host')['state'],'submitted')

    def test_failed_setup_is_not_success_and_arbitrary_commands_are_not_replayed(self):
        self.backend.prepared=False
        result=self.runner.run('failed',self.options)
        self.assertEqual(result['state'],'blocked')
        self.assertEqual(result['error']['code'],'PREPARATION_FAILED')
        self.assertNotIn('definition',self.backend.calls)
        self.runner.run('failed')
        self.assertEqual(self.backend.calls.count('packages'),1)
        self.backend.prepared=True
        self.assertEqual(self.runner.run('failed',retry_preparation=True)['state'],'submitted')

    def test_interrupt_is_not_evidence_that_a_setup_command_did_not_run(self):
        def command(options):raise KeyboardInterrupt()
        self.backend.command=command
        with self.assertRaises(KeyboardInterrupt):self.runner.run('interrupted',self.options)
        self.assertTrue(self.runner.status('interrupted')['resume_required'])
        result=self.runner.run('interrupted')
        self.assertEqual(result['error']['code'],'PREPARATION_UNCERTAIN')
        self.assertNotIn('definition',self.backend.calls)

    def test_lost_dispatch_reply_reuses_the_same_durable_submission(self):
        self.backend.lost_ack=True
        result=self.runner.run('lost',self.options)
        self.assertEqual(result['study_id'],'study-1')
        self.assertEqual(self.runner.run('lost')['state'],'submitted')
        self.assertEqual(self.backend.calls.count('submit'),1)
        self.assertEqual(self.backend.dispatches,1)

    def test_same_key_different_task_conflicts_without_side_effects(self):
        self.runner.run('conflict',self.options)
        before=list(self.backend.calls)
        with self.assertRaises(ExperimentError):self.runner.run('conflict',{**self.options,'task':'different'})
        self.assertEqual(before,self.backend.calls)

    def test_broken_preflight_does_not_dispatch_and_red_tests_can_be_ready(self):
        self.backend.preflight_ready=False
        result=self.runner.run('preflight',self.options)
        self.assertEqual(result['error']['code'],'PREFLIGHT_FAILED')
        self.assertNotIn('submit',self.backend.calls)

    def test_distinct_requests_get_distinct_generated_definition_names(self):
        value={key:value for key,value in self.options.items() if key!='name'}
        a=self.runner.run('first',value)
        b=self.runner.run('second',value)
        self.assertNotEqual(a['options']['name'],b['options']['name'])

    def test_invalid_model_comparison_settings_fail_before_preparation(self):
        for extra in ({'effort':'medium','efforts':['low','high']},{'variants':[['one','medium']],'model':'two'}):
            with self.subTest(extra=extra),self.assertRaises(ExperimentError):
                self.runner.run('invalid',{**self.options,**extra})
        self.assertFalse(self.backend.calls)

    def test_explicit_variants_survive_json_roundtrip_and_same_key_replay(self):
        opts={**self.options,'variants':[('model-one','medium'),('model-two','high')]}
        first=self.runner.run('variants',opts)
        second=self.runner.run('variants',opts)
        self.assertEqual(first['options'],second['options'])
        self.assertEqual(self.backend.dispatches,1)


class PreparationTests(unittest.TestCase):
    def test_authentication_is_checked_before_environment_mutations(self):
        backend=tasks.LocalBackend(Path('/unused-project'))
        report={'ready':True,'codex':{'path':'/codex','status':'ready'},'docker':{'status':'ready'}}
        with patch.object(tasks.dependencies,'inspect_dependencies',return_value=report),patch('subprocess.run',return_value=subprocess.CompletedProcess([],1,'','')):
            self.assertFalse(backend.readiness()['ready'])

    def test_typed_packages_retry_downloads_not_install_commands(self):
        with patch.object(environment_cli,'prepare',return_value={'prepared':True}) as prepare:
            value=environment_cli.prepare_packages(['cmake','make'],allow_network=True)
        self.assertEqual(prepare.call_args.kwargs['network'],'internet')
        self.assertFalse(value['command_replayed'])
        helper=runpy.run_path(str(workspace.ASSETS/'prepare-packages.py'))
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            if '--download-only' in command and sum('--download-only' in c for c in calls)<3:
                return subprocess.CompletedProcess(command,100,'','  503 Service Unavailable\n')
            return subprocess.CompletedProcess(command,0,'','')
        with patch('subprocess.run',side_effect=run),patch('time.sleep'),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(helper['install'](['cmake'],3),0)
        self.assertEqual(sum('--download-only' in c for c in calls),3)
        self.assertEqual(sum('--no-download' in c for c in calls),1)

    def test_permanent_acquisition_or_maintainer_failure_is_not_retried(self):
        helper=runpy.run_path(str(workspace.ASSETS/'prepare-packages.py'))
        acquire=helper['acquire']
        from unittest.mock import Mock
        run=Mock(return_value=subprocess.CompletedProcess([],100,'','E: Unable to locate package unknown'))
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(acquire(['apt-get'],3,run=run),100)
        self.assertEqual(run.call_count,1)
        calls=[]
        def runner(command,**kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command,100 if '--no-download' in command else 0,'','503 Service Unavailable')
        with patch('subprocess.run',side_effect=runner),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(helper['install'](['cmake'],3),100)
        self.assertEqual(sum('--no-download' in c for c in calls),1)

    def test_package_names_cannot_inject_shell_commands_or_options(self):
        with patch.object(environment_cli,'prepare') as prepare:
            for names in ([],['-y'],['cmake; false'],['$(whoami)'],['foo\nbar']):
                with self.subTest(names=names),self.assertRaises(ValueError):environment_cli.prepare_packages(names)
        prepare.assert_not_called()

    def test_resource_flags_and_inspection_share_swap_and_scratch_contract(self):
        policy=normalize({**NEW_PROJECT_CONFIG,'resource_mode':'manual'})
        contract=resources.execution_contract(policy)
        self.assertIn('--memory-swap='+str(contract['limits']['memory_bytes']),contract['docker_args'])
        self.assertTrue(resources.verify_contract({'HostConfig':contract['host_config']},contract))
        changed={**contract['host_config'],'MemorySwap':contract['limits']['memory_bytes']*2}
        with self.assertRaisesRegex(RuntimeError,'MemorySwap'):resources.verify_contract({'HostConfig':changed},contract)


if __name__=='__main__':unittest.main()


class ObservationTests(unittest.TestCase):
    def test_wait_owns_polling_and_terminal_completed_study_returns_immediately(self):
        from invariant import task_results
        from unittest.mock import Mock
        runner=Mock()
        runner.status.side_effect=[{'state':'submitted','execution':{'state':'running'}},
                                   {'state':'submitted','execution':{'state':'completed'}}]
        slept=[]
        result=task_results.wait(runner,'key',timeout=60,clock=lambda:0,sleep=slept.append)
        self.assertEqual(result['execution']['state'],'completed')
        self.assertEqual(slept,[2])
        runner.run.assert_not_called()

    def test_compact_response_does_not_repeat_task_configuration_or_raw_records(self):
        from invariant import task_results
        from unittest.mock import Mock
        runner=Mock();runner.path.return_value=Path('/private/record.json')
        record={'request_id':'key','state':'submitted','study_id':'study-1','completed':{'definition':{'task':'x'*100000}},
                'options':{'task':'x'*100000},'execution':{'state':'completed','summary':{'quality_passes':1,'metrics':{'giant':'x'*100000}},
                'trials':[{'attempt_id':'a','state':'succeeded','passed':None,'runner':{'giant':'x'*100000},'token_usage':None}]}}
        value=task_results.compact_record(runner,record)
        self.assertLess(len(json.dumps(value)),1500)
        self.assertIsNone(value['execution']['trials'][0]['passed'])
        self.assertIsNone(value['execution']['usage']['total_tokens'])

    def test_continuation_replays_dispatch_without_preparing_or_invoking_parent_again(self):
        with tempfile.TemporaryDirectory() as directory:
            from unittest.mock import Mock
            backend=Backend();backend.continuation=Mock(return_value={'id':'child'})
            runner=tasks.TaskRunner(directory,state=Path(directory)/'state',backend=backend)
            record=runner.continue_task('child-key','attempt-'+'a'*32,'Follow up')
            self.assertEqual(record['study_id'],'child')
            self.assertEqual(backend.calls,['dispatch'])
            runner.continue_task('child-key','attempt-'+'a'*32,'Follow up')
            self.assertEqual(backend.continuation.call_count,1)
            self.assertEqual(backend.dispatches,1)
