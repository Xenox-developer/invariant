"""Trusted evaluation reuse must freeze state, retain repair feedback and fail closed."""
import copy
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock,patch

from invariant import workspace
from invariant.codex_rpc import RpcError
from invariant.execution import ExecutionContext
from invariant.experiments import guided
from invariant.experiments.controller import Controller
from invariant.experiments.definitions import digest
from invariant.experiments.errors import ExperimentError
from invariant.experiments.files import materialize,prepare_inputs
from invariant.experiments.preflight import check_frozen
from invariant.experiments.snapshots import seal_tree
from invariant.experiments.validation import ValidationSession
from invariant.experiments.partition import EvaluationPartition
from invariant import resources
from invariant.tasks import LocalBackend
import test_experiments_guided as fixtures
import test_workspace as rpc_fixtures
import test_experiments_execution as execution_fixtures

IMAGE='sha256:'+'1'*64


class ValidationTests(unittest.TestCase):
    setUp=fixtures.GuidedTests.setUp

    def session(self,passed=True):
        info=guided.create(self.engine,task='Fix',test_command='python3 tests/check.py',test_paths=['tests'],efforts=['medium'])
        study=self.engine.submit(info['definition'],request_id='parent');trial=study['trials'][0]
        attempt=self.engine.store.claim(study['id'],trial['id'],'fixture');folder=self.engine.root/'attempts'/attempt['id'];folder.mkdir(parents=True)
        base=self.engine.root/'baselines'/study['baseline_id'];manifest=json.loads((base/'manifest.json').read_text())
        materialize(base/'workspace',folder/'workspace',manifest['entries'])
        inputs=folder/'input';prepare_inputs(base,study['frozen'],study['frozen']['definition']['variants'][0],study['frozen']['definition']['cases'][0],inputs);seal_tree(inputs)
        context=ExecutionContext.create(project=self.project,workspace=folder/'workspace',state=folder/'control',image=IMAGE,
            owner_token='invariant-fixture-runner',checkpoint_owner=attempt['id']+'-runner',policy=study['frozen']['effective_policy'])
        controller=Mock();controller.engine=self.engine
        def evaluate(study,attempt,folder,*args,**kwargs):
            target=folder/'evaluation';target.mkdir();(target/'artifacts').mkdir()
            (target/'artifacts/test-output.txt').write_text('frozen check diagnostic')
            return {'status':'completed','passed':passed,'metrics':{'tests_total':20,'tests_failures':0 if passed else 1,'tests_errors':0},'artifacts':{}}
        controller.evaluate.side_effect=evaluate
        self.info={'Id':'container-one','State':{'Running':False,'StartedAt':'a','FinishedAt':'b'}}
        def checkpoint(name,state,project,audit,*,owner):
            record={'schema':1,'project':str(project),'image':IMAGE,'owner':owner}
            workspace.private_write(state/'environment.json',record);return record
        for name,kwargs in [
            ('invariant.experiments.snapshot_store.stop_owned',{'return_value':True}),
            ('invariant.experiments.snapshot_store.reclaim_private',{}),
            ('invariant.experiments.snapshot_store.owned_info',{'side_effect':lambda c:copy.deepcopy(self.info)}),
            ('invariant.experiments.snapshot_store.environment.checkpoint',{'side_effect':checkpoint}),
            ('invariant.experiments.snapshot_store.environment.selected_image',{'return_value':IMAGE}),
            ('invariant.experiments.validation.workspace.docker',{}),
            ('invariant.experiments.validation.workspace.remove_owned',{}),
        ]:
            m=patch(name,**kwargs);m.start();self.addCleanup(m.stop)
        return ValidationSession(controller,study,attempt,context,inputs,Mock()),controller,context

    def test_pass_is_immutable_reused_and_same_call_does_not_repeat_evaluation(self):
        session,controller,context=self.session();cancel=threading.Event()
        result=session.run('call-1',cancel=cancel,timeout=10)
        self.assertTrue(result['passed']);self.assertEqual(session.run('call-1',cancel=cancel,timeout=10),result)
        accepted=session.consume()
        self.assertTrue(accepted['evaluation']['reused_validation']);self.assertEqual(controller.evaluate.call_count,1)
        self.assertTrue(context.checkpoint_path.exists())
        self.assertTrue((session.root.parent/'snapshot/workspace/solution.py').exists())

    def test_code_change_after_check_invalidates_reuse(self):
        session,controller,context=self.session();session.run('one',cancel=threading.Event(),timeout=10)
        (context.workspace/'solution.py').write_text('changed after validation')
        self.assertIsNone(session.consume());self.assertEqual(controller.evaluate.call_count,1)

    def test_resumed_environment_invalidates_reuse_even_if_stopped_again(self):
        session,_,_=self.session();session.run('one',cancel=threading.Event(),timeout=10)
        self.info['State']['StartedAt']='new-start';self.info['State']['FinishedAt']='new-stop'
        self.assertIsNone(session.consume())

    def test_changed_snapshot_content_is_rejected(self):
        session,_,_=self.session();session.run('one',cancel=threading.Event(),timeout=10)
        folder,record=session.accepted;file=folder/'snapshot/workspace/solution.py';file.chmod(0o600);file.write_text('tampered')
        with self.assertRaises(ExperimentError):session.consume()

    def test_red_result_retains_feedback_and_restarts_same_worker(self):
        session,_,context=self.session(passed=False)
        with patch('invariant.experiments.validation.workspace.docker') as docker:
            result=session.run('one',cancel=threading.Event(),timeout=10)
        self.assertFalse(result['passed']);self.assertIn('diagnostic',result['diagnostic'])
        docker.assert_called_once_with('start',context.owner_token);self.assertIsNone(session.consume())

    def test_incomplete_validation_is_not_replayed(self):
        session,controller,_=self.session();folder=session.root/digest('one');folder.mkdir(parents=True)
        workspace.private_write(folder/'receipt.json',{'binding':session.binding,'state':'evaluating'})
        with self.assertRaisesRegex(ExperimentError,'no automatic replay'):session.run('one',cancel=threading.Event(),timeout=10)
        controller.evaluate.assert_not_called()

    def test_unknown_evaluation_cannot_be_consumed(self):
        session,controller,_=self.session();controller.evaluate.side_effect=None
        controller.evaluate.return_value={'status':'completed','passed':None,'metrics':{}}
        self.assertIsNone(session.run('one',cancel=threading.Event(),timeout=10)['passed']);self.assertIsNone(session.consume())

    def test_changed_evaluation_binding_is_not_reused(self):
        session,_,_=self.session();session.run('one',cancel=threading.Event(),timeout=10)
        session.binding='different-command-or-tests-or-resources'
        self.assertIsNone(session.consume())


class BaselineTests(unittest.TestCase):
    setUp=fixtures.GuidedTests.setUp

    def test_direct_baseline_runs_full_evaluator_without_worker_or_commit(self):
        info=guided.create(self.engine,task='Fix',test_command='python3 tests/check.py',test_paths=['tests'],efforts=['medium'])
        study=self.engine.submit(info['definition']);captured=[]
        def execute(sid):captured.append(self.engine.inspect(sid))
        with patch.object(self.engine,'execute',side_effect=execute),patch.object(self.engine,'results',return_value={
            'trials':[{'runner':{'status':'completed'},'evaluation':{'status':'completed'},'passed':False}]}):
            pre=check_frozen(self.engine,study['id'],request_id='preflight')
        baseline=captured[0];self.assertTrue(baseline['frozen']['evaluation_only'])
        controller=Controller(self.engine);controller.evaluator_source=self.engine.root/'baseline-evaluator'
        attempt=self.engine.store.claim(baseline['id'],baseline['trials'][0]['id'],'fixture')
        seen=[]
        def evaluate(study,attempt,folder,inputs,image,saved,*args,**kwargs):
            seen.append((image,study['frozen']['definition']['evaluator']))
            return {'status':'completed','passed':False,'metrics':{'tests_total':20,'tests_errors':20}}
        with patch.object(controller,'evaluate',side_effect=evaluate),patch.object(controller.runner,'run') as runner,patch('invariant.environment.checkpoint') as checkpoint:
            result=controller.baseline_trial(baseline,baseline['trials'][0],attempt)
        runner.assert_not_called();checkpoint.assert_not_called();self.assertEqual(len(seen),1)
        self.assertFalse(result['evaluation']['passed']);self.assertEqual(result['reason'],'COMPLETED')
        self.assertFalse(result['checkpoint']['complete'])

    def test_prepared_snapshot_survives_backend_restart_without_recapture(self):
        info=guided.create(self.engine,task='Fix',test_command='python3 tests/check.py',test_paths=['tests'],efforts=['medium'])
        backend=LocalBackend(self.project);backend._engine=self.engine
        first=backend.validate(info['definition']);(self.project/'solution.py').write_text('later host edit')
        resumed=LocalBackend(self.project);resumed._engine=self.engine
        with patch.object(self.engine,'_capture',side_effect=AssertionError('must not recapture')):
            result=resumed.submit(info['definition'],'same-job')
        self.assertEqual(first['prepared_study_id'],result['id'])
        self.assertNotEqual((self.engine.root/'baselines'/self.engine.inspect(result['id'])['baseline_id']/'workspace/solution.py').read_text(),'later host edit')

    def test_identical_baseline_is_reused_for_new_observation_id(self):
        info=guided.create(self.engine,task='Fix',test_command='python3 tests/check.py',test_paths=['tests'],efforts=['medium'])
        study=self.engine.submit(info['definition'])
        def execute(sid):
            value=self.engine.inspect(sid)
            for trial in value['trials']:
                attempt=self.engine.store.claim(sid,trial['id'],'fixture')
                self.engine.store.update_attempt(attempt['id'],'succeeded',{'cleanup_confirmed':True},'COMPLETED')
            self.engine.store.finish_study(sid)
        with patch.object(self.engine,'execute',side_effect=execute) as run,patch.object(self.engine,'results',return_value={
                'trials':[{'runner':{'status':'completed'},'evaluation':{'status':'completed'},'passed':False}]}):
            first=check_frozen(self.engine,study['id'],request_id='first')
            second=check_frozen(self.engine,study['id'],request_id='observe-again')
        self.assertEqual(first['study_id'],second['study_id']);self.assertEqual(run.call_count,1)


class ToolTransportTests(rpc_fixtures.RpcTests):
    def test_dynamic_tool_has_explicit_host_handler_and_other_requests_stay_denied(self):
        rpc=self.rpc('''import json
req=json.loads(input())
print(json.dumps({'method':'item/tool/call','id':99,'params':{'tool':'invariant_validate'}}),flush=True)
assert json.loads(input())['result']['success'] is True
print(json.dumps({'method':'unknown/capability','id':100,'params':{}}),flush=True)
assert json.loads(input())['error']['code']==-32601
print(json.dumps({'id':req['id'],'result':{'ok':True}}),flush=True)
''')
        rpc.tool_handler=lambda value:{'success':True,'contentItems':[]}
        self.assertTrue(rpc.request('probe',{})['ok'])


class FinalizationTests(unittest.TestCase):
    setUp=execution_fixtures.ExecutionTests.setUp
    unseal=execution_fixtures.ExecutionTests.unseal
    change=execution_fixtures.ExecutionTests.change
    submit=execution_fixtures.ExecutionTests.submit
    configure=execution_fixtures.ExecutionTests.configure
    controller=execution_fixtures.ExecutionTests.controller

    def test_completed_preserve_is_not_finalized_again(self):
        import subprocess
        study=self.configure();controller,_=self.controller();released=set()
        def preserve(context,name):
            workspace.private_write(context.checkpoint_path,{'schema':1,'project':str(self.project),
                'image':execution_fixtures.IMAGE,'owner':context.checkpoint_owner})
            released.add(context.owner_token)
        def stop(context,**kwargs):
            if context.owner_token in released:
                raise subprocess.TimeoutExpired(['docker','inspect',context.owner_token],3)
            return True
        with patch('invariant.execution.ExecutionContext.preserve',preserve),patch('invariant.experiments.controller.stop_owned',side_effect=stop):
            result=controller.execute(study['id'])
        attempts=[a for t in result['trials'] for a in t['attempts']]
        self.assertTrue(all(a['record']['cleanup_confirmed'] for a in attempts))

    def test_unconfirmed_stop_remains_failure_with_diagnostic(self):
        study=self.configure();controller,_=self.controller()
        with patch('invariant.experiments.controller.stop_owned',side_effect=RuntimeError('transport failed')):
            result=controller.execute(study['id'])
        attempts=[a for t in result['trials'] for a in t['attempts']]
        failed=[a for a in attempts if not a['record']['cleanup_confirmed']]
        self.assertTrue(failed)
        self.assertTrue(all(a['record'].get('cleanup_errors') for a in failed))

    def test_numeric_protocol_error_is_durably_recorded_without_crashing_recovery(self):
        study=self.configure();controller,runner=self.controller()
        with patch.object(runner,'run',side_effect=RpcError('protocol unavailable',code=-32601)):
            result=controller.execute(study['id'])
        attempts=[a for t in result['trials'] for a in t['attempts']]
        self.assertTrue(attempts)
        self.assertTrue(all(a['record']['error_type']=='RpcError' and a['reason']=='RUNTIME_FAILED' for a in attempts))


class PartitionTests(unittest.TestCase):
    setUp=fixtures.GuidedTests.setUp

    def reserve(self,paused=True,pids='3'):
        context=Mock();context.owner_token='worker';context.policy=copy.deepcopy(workspace.load_project(self.project))
        context.policy['resource_mode']='manual';context.policy['limits'].update(memory_mib=2048,processes=512,extra_swap_mib=128)
        state={'HostConfig':resources.execution_contract(context.policy)['host_config'],'State':{'Paused':paused}}
        def docker(*args,**kwargs):
            if args[0]=='stats':return json.dumps({'MemUsage':'45MiB / 2GiB','PIDs':pids})
            if args[0]=='update':
                for arg in args:
                    for flag,key in [('--memory=','Memory'),('--memory-swap=','MemorySwap'),('--pids-limit=','PidsLimit')]:
                        if arg.startswith(flag):state['HostConfig'][key]=int(arg[len(flag):])
                return ''
            raise AssertionError(args)
        one=patch('invariant.experiments.partition.workspace.docker',side_effect=docker);two=patch('invariant.experiments.partition.owned_info',side_effect=lambda c:copy.deepcopy(state))
        one.start();two.start();self.addCleanup(one.stop);self.addCleanup(two.stop)
        part=EvaluationPartition(context,self.root,'evaluator')
        return part,state,context

    def test_reservation_and_restore_keep_aggregate_memory_swap_and_pids(self):
        part,state,context=self.reserve();child=part.reserve();record=json.loads((self.root/'partition.json').read_text())
        child_contract=resources.execution_contract(child)
        EvaluationPartition.verify(record,{'runtime':{'resource_contract':child_contract,'host_config':child_contract['host_config']}},resources.execution_contract(context.policy))
        self.assertEqual(record['worker_contract']['limits']['memory_bytes']+child_contract['limits']['memory_bytes'],2048*1024**2)
        self.assertEqual(record['worker_contract']['limits']['processes']+child_contract['limits']['processes'],512)
        part.restore();resources.verify_contract(state,resources.execution_contract(context.policy))

    def test_evaluator_evidence_cannot_claim_more_memory(self):
        part,_,context=self.reserve();child=part.reserve();record=json.loads((self.root/'partition.json').read_text())
        with self.assertRaises(ExperimentError):
            EvaluationPartition.verify(record,{'runtime':{'resource_contract':resources.execution_contract(context.policy)}},resources.execution_contract(context.policy))

    def test_active_worker_cannot_share_cpu_budget(self):
        part,_,_=self.reserve(paused=False)
        with self.assertRaisesRegex(ExperimentError,'paused'):part.reserve()

    def test_insufficient_pid_budget_is_not_expanded(self):
        part,_,_=self.reserve(pids='510')
        with self.assertRaisesRegex(ExperimentError,'insufficient'):part.reserve()
        self.assertFalse(part.changed)
