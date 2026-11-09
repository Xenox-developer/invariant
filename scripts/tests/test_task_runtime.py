"""The public coordinator owns waiting, evidence and application; models only review."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

from invariant import workspace
from invariant.usage import UsageLedger, from_rollout
from invariant.task_review import Reviewer
from invariant.task_runtime import Coordinator
from invariant.experiments import guided
from invariant.experiments.errors import ExperimentError
from invariant.experiments.files import materialize, capture_workspace
from invariant.experiments.snapshots import seal_tree
from invariant.task_evidence import test_contract
import test_experiments_guided as guide_base
import test_experiments_apply as apply_base


class UsageTests(unittest.TestCase):
    def counts(self,n):return dict(input_tokens=n*10,cached_input_tokens=n*5,output_tokens=n*2,reasoning_output_tokens=n,total_tokens=n*12)
    def test_duplicate_events_are_not_extra_responses_and_final_reconciles(self):
        ledger=UsageLedger();one=self.counts(1);two=self.counts(2)
        ledger.observe(one,one);ledger.observe(one,one);ledger.observe(two,one)
        self.assertEqual(len(ledger.rows),2);self.assertTrue(ledger.summary(two)['complete'])
    def test_cli_final_without_redundant_total_reconciles_exactly(self):
        ledger=UsageLedger();one=self.counts(1);ledger.observe(one,one)
        final={k:v for k,v in one.items() if k!='total_tokens'}
        self.assertTrue(ledger.summary(final)['complete'])

    def test_gap_is_not_invented_as_one_inference_or_zero_cost(self):
        ledger=UsageLedger();ledger.observe(self.counts(2),self.counts(1))
        self.assertEqual(ledger.rows[0]['attribution'],'unattributed_change')
        self.assertFalse(ledger.summary(self.counts(2))['complete'])
    def test_missing_final_or_reset_stays_unknown(self):
        ledger=UsageLedger();ledger.observe(self.counts(2),self.counts(2));ledger.observe(self.counts(1),self.counts(1))
        self.assertFalse(ledger.summary(None)['complete'])


class ReportTests(unittest.TestCase):
    def test_structured_counts_must_be_new_and_agree_with_command(self):
        helper=runpy.run_path(str(workspace.ASSETS/'experiment-check.py'))
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);candidate=root/'candidate';candidate.mkdir();source=root/'inputs';source.mkdir()
            def evaluate(command):
                return helper['evaluate']({'roots':[],'files':[],'timeout_seconds':5,'command':command,'report':'report.json'},source,candidate,root/'output')
            (candidate/'report.json').write_text(json.dumps({'tests':25,'failures':0,'errors':0,'passed':True}))
            self.assertIsNone(evaluate('true')['passed'])  # stale report cannot validate a command
            command="python3 -c \"import json;json.dump(dict(tests=25,failures=0,errors=0,passed=True),open('report.json','w'))\""
            result=evaluate(command);self.assertTrue(result['passed']);self.assertEqual(result['metrics']['tests_total'],25)
            self.assertIsNone(evaluate(command+'; exit 1')['passed'])
            subtests="python3 -c \"import json;json.dump(dict(tests=1,failures=3,errors=0,passed=False),open('report.json','w'))\"; exit 1"
            red=evaluate(subtests)
            self.assertFalse(red['passed']);self.assertEqual(red['metrics']['tests_failures'],3)


class ChildTests(unittest.TestCase):
    setUp=guide_base.GuidedTests.setUp
    def test_child_freezes_new_evaluation_preserving_parent_inputs_and_tools(self):
        info=guided.create(self.engine,task='Initial',test_command='python3 tests/check.py',test_paths=['tests'],efforts=['medium'])
        study=self.engine.submit(info['definition'],request_id='parent')
        trial=study['trials'][0];attempt=self.engine.store.claim(study['id'],trial['id'],'fixture')
        folder=self.engine.root/'attempts'/attempt['id'];folder.mkdir(parents=True)
        baseline=self.engine.root/'baselines'/study['baseline_id'];manifest=json.loads((baseline/'manifest.json').read_text())
        materialize(baseline/'workspace',folder/'workspace',manifest['entries'])
        (folder/'workspace/build').mkdir();(folder/'workspace/build/cache').write_text('preserve')
        snapshot=folder/'snapshot';snapshot.mkdir();saved=capture_workspace(folder/'workspace',snapshot/'workspace',study['frozen']['capture_limits'])
        workspace.private_write(snapshot/'manifest.json',saved);seal_tree(snapshot)
        workspace.private_write(folder/'control/environment.json',{'schema':1,'project':str(self.project),'image':apply_base.IMAGE,'owner':attempt['id']+'-runner'})
        self.engine.store.update_attempt(attempt['id'],'succeeded',{'cleanup_confirmed':True,
            'capture':{'complete':True,'content_digest':saved['content_digest']},'checkpoint':{'complete':True,'image':apply_base.IMAGE}},'COMPLETED')
        self.engine.store.stop(study['id']);self.engine.store.finish_study(study['id'])
        (self.project/'next.txt').write_text('Second stage')
        override={'test_command':'STAGE=2 python3 tests/check.py --output report.json','test_report':'report.json'}
        with patch('invariant.experiments.continuation.environment.selected_image',return_value=apply_base.IMAGE):
            child=self.engine.continue_result(attempt['id'],'next.txt',request_id='child',evaluation=override)
            same=self.engine.continue_result(attempt['id'],'next.txt',request_id='child',evaluation=override)
        self.assertEqual(child['id'],same['id'])
        self.assertEqual(test_contract(self.engine,study)['command'],'python3 tests/check.py')
        self.assertEqual(test_contract(self.engine,child)['command'],override['test_command'])
        self.assertNotEqual(test_contract(self.engine,study)['version'],test_contract(self.engine,child)['version'])
        self.assertEqual((self.engine.root/'baselines'/child['baseline_id']/'workspace/build/cache').read_text(),'preserve')
        from invariant.experiments.preflight import check_frozen
        with patch.object(self.engine,'execute'),patch.object(self.engine,'results',return_value={'trials':[{'runner':{'status':'completed'},'evaluation':{'status':'completed'},'passed':False}]}):
            preflight=check_frozen(self.engine,child['id'],request_id='baseline')
        self.assertTrue(preflight['ready'])
        self.assertEqual(self.engine.inspect(preflight['study_id'])['baseline_id'],child['baseline_id'])


class RuntimeTests(unittest.TestCase):
    def test_unknown_quality_never_invokes_reviewer_or_apply(self):
        runner=Mock();runner.root=Path('/private/unused-state');runner.run.return_value={'state':'submitted'}
        record={'state':'submitted','execution':{'state':'completed','trials':[{'passed':None}]}}
        reviewer=Mock();coordinator=Coordinator('/project',runner=runner,reviewer=reviewer)
        with patch('invariant.task_results.wait',return_value=record),patch('invariant.task_results.compact_record',return_value={}):
            result=coordinator.execute('key',{'options':{'task':'task'}})
        reviewer.decide.assert_not_called();self.assertIn('unknown',result['next'])
    def test_lost_review_response_is_recovered_without_a_new_turn(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name);reviewer=Reviewer(folder,state=folder/'private-state')
            payload={'review':{'review_digest':'a'*64}}
            from invariant.experiments.definitions import digest
            record={'binding':digest({'payload':payload,'model':'chosen','effort':'medium'}),'state':'uncertain','turn_requested_at':1}
            workspace.private_write(folder/'decision.json',record)
            recovered={**record,'state':'completed'}
            with patch.object(reviewer,'recover',return_value=recovered),patch('invariant.task_review.CodexRpc') as rpc:
                self.assertEqual(reviewer.decide(folder,payload,model='chosen',effort='medium')['state'],'completed')
            rpc.assert_not_called()

    def test_one_review_turn_records_response_usage_and_replays_saved_decision(self):
        from invariant.experiments.native import usage_record
        from invariant.task_review import read_decision
        class Rpc:
            turns=0
            def __init__(self,*args,**kwargs):pass
            def initialize(self):pass
            def receive(self,**kwargs):return None
            def close(self):pass
            def request(self,method,params,**kwargs):
                if method=='thread/start':return {'thread':{'id':'thread-1'},'model':'chosen','reasoningEffort':'medium'}
                if method=='turn/start':
                    Rpc.turns+=1
                    usage={'totalTokens':120,'inputTokens':100,'cachedInputTokens':50,'outputTokens':20,'reasoningOutputTokens':5}
                    callback=kwargs['event']
                    callback({'method':'thread/tokenUsage/updated','params':{'threadId':'thread-1','tokenUsage':{'total':usage,'last':usage}}})
                    callback({'method':'item/completed','params':{'item':{'id':'message','type':'agentMessage','text':json.dumps({'approved':True,'reason':'Checked patch'})}}})
                    return {'turn':{'id':'turn-1','status':'completed'}}
                raise AssertionError(method)
        with tempfile.TemporaryDirectory() as directory,patch('invariant.task_review.dependencies.probe_codex',return_value={'status':'ready','path':'/codex'}):
            reviewer=Reviewer(directory,rpc_factory=Rpc,state=Path(directory)/'private-state');payload={'task':'Implement feature',
                'review':{'review_digest':'a'*64,'attempt_id':'attempt-1','changed_paths':1,
                          'changes':[{'path':'main.py','text_diff':'-old\n+new\n'}]},
                'evidence':[{'attempt_id':'attempt-1','passed':True,'evaluation':{'status':'completed','command':'check','checks':{'tests':1}},
                             'resources':{'expected_digest':'same','observed_digest':'same'}}]}
            result=reviewer.decide(Path(directory)/'review',payload,model='chosen',effort='medium')
            self.assertTrue(result['inference_usage']['complete'])
            self.assertEqual(result['decision']['review_digest'],'a'*64)
            self.assertTrue(reviewer.decide(Path(directory)/'review',payload,model='chosen',effort='medium')['decision']['approved'])
            self.assertEqual(Rpc.turns,1)

    def test_concurrent_same_review_does_not_start_another_model(self):
        from invariant.execution import project_lock
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'review';folder.mkdir()
            with project_lock(folder,reconcile=False):
                with patch.object(Reviewer,'_decide') as decide:
                    with self.assertRaises(ExperimentError) as failure:
                        Reviewer(directory,state=Path(directory)/'private-state').decide(folder,{},model='chosen',effort='medium')
                self.assertEqual(failure.exception.code,'REVIEW_BUSY');decide.assert_not_called()

    def test_unavailable_reviewer_protocol_blocks_before_worker_dispatch(self):
        from invariant.codex_rpc import RpcError
        runner=Mock();runner.root=Path('/private/unused-state');reviewer=Mock()
        reviewer.ready.side_effect=RpcError('unsupported protocol')
        with self.assertRaises(RpcError):Coordinator('/project',runner=runner,reviewer=reviewer).execute('key',{'options':{'task':'task'}})
        runner.run.assert_not_called();reviewer.decide.assert_not_called()
