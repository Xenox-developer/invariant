"""Integrity, crash semantics and no-copy behavior of the reusable execution path."""
import copy,contextlib,hashlib,json,socket,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from invariant import host_delivery,storage_worker,workspace
from invariant.experiments.snapshots import SourceCapture
from invariant.experiments.files import capture_workspace
from invariant.experiments.result_inputs import normalize,resolve
from invariant.experiments.errors import ExperimentError
import test_experiments_execution as execution
import test_experiments_codex as native_fixtures

class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'source';self.source.mkdir()
        self.definition={'workspace':{'include':['**/*'],'exclude':[]},'variants':[], 'cases':[],
                         'evaluator':{'source_paths':['inputs/method/result.json']}}

    def test_virtual_inputs_are_frozen_in_workspace_and_evaluator_without_host_files(self):
        raw=b'{"verified":true}'
        capture=SourceCapture(self.source,self.definition,virtual_files={'inputs/method/result.json':raw})
        destination=self.root/'capture';destination.mkdir();manifest=capture.capture(destination)
        self.assertEqual((destination/'workspace/inputs/method/result.json').read_bytes(),raw)
        self.assertEqual((destination/'inputs/inputs/method/result.json').read_bytes(),raw)
        self.assertFalse((self.source/'inputs').exists())
        self.assertEqual(manifest['entries']['@input/inputs/method/result.json']['sha256'],hashlib.sha256(raw).hexdigest())

    def test_virtual_input_rejects_symlink_ancestry_and_protected_paths(self):
        elsewhere=self.root/'elsewhere';elsewhere.mkdir();(self.source/'inputs').symlink_to(elsewhere,target_is_directory=True)
        capture=SourceCapture(self.source,self.definition,virtual_files={'inputs/method/result.json':b'x'})
        with self.assertRaises(ExperimentError):capture.scan()
        with self.assertRaises(ExperimentError):SourceCapture(self.source,self.definition,virtual_files={'.env':b'secret'})

    def test_virtual_parent_directories_count_toward_the_capture_budget(self):
        definition=copy.deepcopy(self.definition);definition.pop('evaluator')
        capture=SourceCapture(self.source,definition,virtual_files={'a/b/c/d/result.json':b'x'},limits={'entries':2,'source_bytes':None})
        with self.assertRaises(ExperimentError):capture.scan()

    def test_manifest_only_scan_detects_extra_files_without_copying(self):
        (self.source/'code.py').write_bytes(b'accepted');limits={'entries':None,'source_bytes':None}
        original=capture_workspace(self.source,self.root/'saved',limits)
        with patch('invariant.experiments.files.write_file',side_effect=AssertionError('no copies')):
            self.assertEqual(capture_workspace(self.source,None,limits),original)
            (self.source/'extra').write_text('changed');self.assertNotEqual(capture_workspace(self.source,None,limits)['content_digest'],original['content_digest'])

class ReferenceTests(unittest.TestCase):
    setUp=execution.ExecutionTests.setUp
    unseal=execution.ExecutionTests.unseal
    change=execution.ExecutionTests.change
    submit=execution.ExecutionTests.submit
    configure=execution.ExecutionTests.configure
    controller=execution.ExecutionTests.controller

    def test_result_handoff_is_bound_to_exact_accepted_snapshot_and_request(self):
        study=self.configure();controller,_=self.controller();controller.execute(study['id'])
        rows=self.engine.results(study['id'])['trials'];passed=next(r for r in rows if r['passed'])
        ref={'attempt_id':passed['attempt_id'],'result_digest':passed['capture']['content_digest'],
             'files':{'outputs/answer.json':'inputs/answer.json'}}
        child=self.engine.submit('experiments.json',request_id='handoff',result_inputs=[ref])
        path=self.engine.root/'baselines'/child['baseline_id']/'workspace/inputs/answer.json'
        self.assertEqual(json.loads(path.read_text())['variant'],'planner');self.assertFalse((self.project/'inputs/answer.json').exists())
        with patch('invariant.experiments.result_inputs.resolve',side_effect=AssertionError('no re-import')):
            self.assertTrue(self.engine.submit('experiments.json',request_id='handoff',result_inputs=[ref])['idempotent_replay'])
        wrong={**ref,'result_digest':'0'*64}
        with self.assertRaises(ExperimentError):self.engine.submit('experiments.json',request_id='handoff',result_inputs=[wrong])
        with self.assertRaises(ExperimentError):resolve(self.engine,[wrong])
        bad=next(r for r in rows if r['passed'] is False)
        with self.assertRaises(ExperimentError):resolve(self.engine,[{**ref,'attempt_id':bad['attempt_id'],'result_digest':bad['capture']['content_digest']}])

class ExportTests(unittest.TestCase):
    def test_unchanged_collection_needs_one_manifest_request_and_no_copy(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);project=root/'p';path=project/'.invariant/results/a';path.mkdir(parents=True);(path/'result').write_text('valid')
            log=project/'.invariant/logs/old';log.mkdir(parents=True);(log/'run.log').write_text('log')
            manager=Mock();manager.project=project
            row={'attempt_id':'a','entries':{},'directory':'/docker/a'}
            cached=lambda p,r:{'path':str(p),'revision':r,'inventory':host_delivery.bundle_inventory(p)}
            manager.request.return_value={'results':[row],'logs':{'directory':'/docker/logs','revision':'revision','cursor':'revision','full':True,'deleted':[]},
              'cache':{'result:a':cached(path,host_delivery.digest({})),'logs:full':cached(log,'revision')}}
            with patch.object(host_delivery,'copy_bundle',side_effect=AssertionError('no copying')):
                result=host_delivery.collect_context(manager,'run-'+'a'*32)
            self.assertEqual(manager.request.call_count,1);self.assertEqual(result['exported_results'],[str(path)])

    def test_log_cursor_exports_only_changes_and_preserves_full_history(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);state=root/'state';state.mkdir();(state/'first.log').write_text('first')
            with patch.object(workspace,'state_for',return_value=state):
                first=storage_worker.export_logs(root);target=Path(first['directory'])/'first.log';version=target.stat().st_mtime_ns
                again=storage_worker.export_logs(root);self.assertEqual(version,target.stat().st_mtime_ns)
                (state/'second.log').write_text('second');delta=storage_worker.export_logs(root,cursor=first['cursor'])
                self.assertEqual(delta['files'],['second.log']);self.assertFalse(delta['full']);self.assertTrue(target.exists())
                unchanged=storage_worker.export_logs(root,cursor=delta['cursor']);self.assertEqual(unchanged['files'],[])

class StreamTests(unittest.TestCase):
    def test_same_connection_serves_multiple_requests_and_survives_errors(self):
        from invariant import manager_service as service
        with tempfile.TemporaryDirectory() as name:
            path=str(Path(name)/'socket');server=service.Server(path,service.Handler);server.project=Path('/project')
            with patch.object(service,'reply',side_effect=[{'n':1},RuntimeError('expected'),{'n':3}]):
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    with socket.socket(socket.AF_UNIX) as connection:
                        connection.connect(path)
                        with connection.makefile('rb') as stream:
                            for expected in (True,False,True):
                                connection.sendall(b'{}\n');self.assertEqual(json.loads(stream.readline())['ok'],expected)
                finally:server.shutdown();server.server_close();thread.join()

class CompletionTests(unittest.TestCase):
    setUp=native_fixtures.NativeTests.setUp
    run_native=native_fixtures.NativeTests.run_native

    def test_validated_completion_preserves_interrupted_server_status_and_reconciled_usage(self):
        fixture=native_fixtures
        class ValidatingRpc(fixture.FakeRpc):
            def __init__(self):super().__init__();self.called=False;self.sent=[]
            def send(self,message):self.sent.append(message)
            def receive(self,**kwargs):
                if self.messages and not self.called:
                    self.called=True
                    self.tool_handler({'tool':'invariant_validate','threadId':fixture.THREAD,'turnId':'turn-1','arguments':{},'callId':'validation-call'})
                    usage={**fixture.USAGE['total'],'cachedInputTokens':0,'reasoningOutputTokens':0}
                    self.messages=[{'method':'thread/tokenUsage/updated','params':{'threadId':fixture.THREAD,'turnId':'turn-1','tokenUsage':{'total':usage,'last':usage}}},
                     {'method':'turn/completed','params':{'threadId':fixture.THREAD,'turn':{'id':'turn-1','status':'interrupted'}}}]
                return super().receive(**kwargs)
        rpc=ValidatingRpc();validate=Mock(return_value={'passed':True,'frozen':True,'receipt_digest':'digest'})
        result,*_=self.run_native(rpc,validate=validate,complete_on_validation=True)
        self.assertEqual(result['status'],'completed');self.assertEqual(result['native']['turn_status'],'interrupted')
        self.assertEqual(result['completion_source'],'validation');self.assertTrue(result['inference_usage']['complete'])
        self.assertEqual(rpc.sent[0]['method'],'turn/interrupt');validate.assert_called_once()

    def test_baseline_gate_failure_never_starts_model_turn(self):
        def reject():raise ExperimentError('BASELINE_NOT_READY','baseline failed')
        result,rpc,*_=self.run_native(before_model=reject)
        self.assertEqual(result['reason'],'BASELINE_NOT_READY')
        self.assertFalse(any(method=='turn/start' for method,_ in rpc.calls))

class PublicationTests(unittest.TestCase):
    from test_validation import ValidationTests
    setUp=ValidationTests.setUp
    session=ValidationTests.session

    def test_publication_reuses_exact_snapshot_inode_and_records_intent(self):
        session,_,_=self.session();session.run('accept',cancel=threading.Event(),timeout=10)
        folder,record=session.accepted;original=(folder/'snapshot/workspace/solution.py').stat().st_ino
        session.consume();published=session.root.parent/'snapshot/workspace/solution.py'
        self.assertEqual(published.stat().st_ino,original)
        self.assertEqual(json.loads((folder/'publication.json').read_text())['state'],'published')

class CachedBundleTests(unittest.TestCase):
    setUp=ReferenceTests.setUp
    unseal=ReferenceTests.unseal
    change=ReferenceTests.change
    submit=ReferenceTests.submit
    configure=ReferenceTests.configure
    controller=ReferenceTests.controller

    def test_existing_verified_result_bundle_is_not_materialized_again(self):
        study=self.configure();controller,_=self.controller();controller.execute(study['id'])
        with patch.object(storage_worker,'engine',return_value=self.engine):
            first=storage_worker.operate(self.project,'result_bundles',{'studies':[study['id']]})
            with patch.object(storage_worker.shutil,'copyfile',side_effect=AssertionError('no staging copy')):
                second=storage_worker.operate(self.project,'result_bundles',{'studies':[study['id']]})
        self.assertEqual(first,second)

class DisconnectTests(unittest.TestCase):
    def test_lost_reply_is_reported_without_replaying_the_request(self):
        from invariant import docker_storage as storage
        with tempfile.TemporaryDirectory() as name:
            manager=storage.Manager(name,prepare=False);manager.enable_stream()
            process=Mock();process.stdout.readline.return_value=b''
            with patch('invariant.dependencies.find_docker',return_value='/docker'),patch('invariant.dependencies.docker_env',return_value={}),patch.object(storage.subprocess,'Popen',return_value=process) as factory:
                with self.assertRaisesRegex(RuntimeError,'not replayed'):manager.request('run_submit',{'request_id':'same'})
            self.assertEqual(factory.call_count,1);self.assertEqual(process.stdin.write.call_count,1)

class BrokenPipeTests(unittest.TestCase):
    def test_close_reaps_disconnected_stream_even_when_stdin_flush_fails(self):
        from invariant import docker_storage as storage
        with tempfile.TemporaryDirectory() as name:
            manager=storage.Manager(name,prepare=False);process=Mock();manager._stream=process
            process.stdin.close.side_effect=BrokenPipeError('manager stopped')
            manager.close();process.wait.assert_called_once();process.stdout.close.assert_called_once()

class ConcurrentCollectionTests(unittest.TestCase):
    def test_concurrent_collectors_share_one_publication_and_cached_path(self):
        import concurrent.futures,time
        with tempfile.TemporaryDirectory() as name:
            project=Path(name)/'project';project.mkdir();source=Path(name)/'source';source.mkdir();(source/'answer').write_text('accepted')
            manager=Mock();manager.project=project;index={};copies=[]
            def request(operation,payload,**kwargs):
                if 'value' in payload:index[payload['key']]=payload['value']
                return index.get(payload['key'])
            manager.request.side_effect=request
            def copy(*args):
                time.sleep(.02);path=project/'.invariant/results'/('copy-'+str(len(copies)));path.mkdir(parents=True);(path/'answer').write_text('accepted');copies.append(path);return path
            with patch.object(host_delivery,'copy_bundle',side_effect=copy):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    futures=[pool.submit(host_delivery.export_once,manager,'key','revision',source,project/'.invariant/results/result') for _ in range(2)]
                    results=[f.result() for f in futures]
            self.assertEqual(len(copies),1);self.assertEqual(results[0],results[1])
