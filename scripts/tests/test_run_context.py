"""Reusable execution: integrity, cache invalidation, observation and export safety."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock,patch

from invariant import catalog,docker_storage as storage,environment,host_delivery,run_context
from invariant.policy import NEW_PROJECT_CONFIG

IMAGE='sha256:'+'a'*64


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()

    def test_shared_runtime_copies_once_and_detects_tampering(self):
        copies=[]
        def docker(*args,**kwargs):
            if args[0]=='cp':
                folder=Path(args[-1]);(folder/'codex').write_bytes(b'codex');(folder/'inspect').write_bytes(b'inspector');copies.append(folder)
            return 'container'
        with patch.object(Path,'home',return_value=self.root),patch.object(storage,'managed',return_value=True),patch('invariant.workspace.docker',side_effect=docker),patch('invariant.workspace.remove_owned'):
            first=environment.prepare_control(self.root/'p1',IMAGE,path=self.root/'p1/control')
            second=environment.prepare_control(self.root/'p2',IMAGE,path=self.root/'p2/control')
            self.assertEqual(first,second);self.assertEqual(len(copies),1)
            (first/'codex').chmod(0o700)
            (first/'codex').write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError,'integrity'):environment.verify_control(self.root/'p1',path=first)

    def test_context_survives_reopen_and_keeps_study_membership(self):
        with patch('invariant.workspace.state_for',return_value=self.root/'state'),patch('invariant.workspace.load_project',return_value=NEW_PROJECT_CONFIG):
            first=run_context.operate(self.root,'run_prepare',{'key':'stable'})
            run_context.operate(self.root,'run_add',{'run_ref':first['run_ref'],'study_id':'study-a'})
            reopened=run_context.operate(self.root,'run_prepare',{'key':'stable'})
            self.assertEqual(reopened['studies'],['study-a'])
            self.assertEqual(first['run_ref'],reopened['run_ref'])
            with self.assertRaisesRegex(RuntimeError,'Invalid run'):run_context.record(self.root,'../escape')

    def test_resume_and_status_do_not_sync_or_export(self):
        manager=Mock();manager.project=self.root
        manager.request.return_value={'run_ref':'run-'+'a'*32,'studies':[]}
        with patch.object(storage,'Manager',return_value=manager),patch.object(storage,'ContainerEngine'):
            context=run_context.RunContext(run_ref='run-'+'a'*32)
            self.assertEqual(context.status()['studies'],[])
        manager.sync.assert_not_called()
        self.assertTrue(all(c.args[0] in ('run_status','run_view') for c in manager.request.call_args_list))

    def test_unchanged_source_does_not_stream_again(self):
        (self.root/'code.py').write_text('source')
        manager=storage.Manager(self.root,prepare=False);manager.request=Mock(return_value={'matches':True})
        with patch('invariant.host_delivery.deliver_pending',return_value=[]),patch.object(storage.subprocess,'Popen') as popen:
            manager.sync();manager.sync()
            self.assertEqual(manager.request.call_count,2);popen.assert_not_called()
            (self.root/'code.py').write_text('new source');manager.sync()
            self.assertEqual(manager.request.call_count,3)

    def test_exports_are_deduplicated_and_external_result_edits_survive(self):
        project=self.root/'project';project.mkdir();target=project/'.invariant/results/attempt'
        source=self.root/'bundle';source.mkdir();(source/'result').write_text('verified')
        rows={};manager=Mock();manager.project=project
        def request(operation,payload,**kwargs):
            self.assertEqual(operation,'export_cache')
            if 'value' in payload:rows[payload['key']]=payload['value']
            return rows.get(payload['key'])
        manager.request.side_effect=request
        def copy(_,__,destination):
            destination=Path(destination)
            if destination.exists():destination=destination.with_name(destination.name+'-fresh')
            shutil.copytree(source,destination);return destination
        with patch.object(host_delivery,'copy_bundle',side_effect=copy) as copier:
            first=host_delivery.export_once(manager,'artifact','version',source,target)
            self.assertEqual(host_delivery.export_once(manager,'artifact','version',source,target),first)
            self.assertEqual(copier.call_count,1)
            (Path(first)/'result').write_text('external edit')
            second=host_delivery.export_once(manager,'artifact','version',source,target)
            self.assertNotEqual(first,second)
            self.assertEqual((Path(first)/'result').read_text(),'external edit')
            self.assertEqual((Path(second)/'result').read_text(),'verified')

    def test_catalog_ttl_auth_invalidation_and_connection_reuse(self):
        account=self.root/'account';account.mkdir();(account/'auth.json').write_text('synthetic-one')
        rpc=Mock();rpc.agent_runtime=('fake',None,None);rpc.request.side_effect=lambda method,args: {'account':{'type':'chatgpt'}} if method=='account/read' else {'data':[{'model':'known'}]}
        with patch.object(Path,'home',return_value=self.root),patch.object(catalog,'account_home',return_value=account),patch.object(catalog,'probe_codex',return_value={'status':'ready','path':'codex'}),patch('invariant.workspace.codex_configuration',return_value=(['codex'],{})),patch.object(catalog,'CodexRpc',return_value=rpc) as factory:
            catalog._CLIENT=None;catalog._AUTH=None
            first=catalog.models(self.root,NEW_PROJECT_CONFIG,clock=lambda:1000)
            self.assertEqual(catalog.models(self.root,NEW_PROJECT_CONFIG,clock=lambda:1001),first)
            self.assertEqual(rpc.request.call_count,2)
            catalog.models(self.root,NEW_PROJECT_CONFIG,clock=lambda:1301)
            self.assertEqual(factory.call_count,1);self.assertEqual(rpc.request.call_count,4)
            (account/'auth.json').write_text('synthetic-two')
            catalog.models(self.root,NEW_PROJECT_CONFIG,clock=lambda:1302)
            self.assertEqual(factory.call_count,2)
            self.assertTrue(all(c.args[0] in ('account/read','model/list') for c in rpc.request.call_args_list))
            catalog.close()


if __name__=='__main__':unittest.main()

class ManagerServiceTests(unittest.TestCase):
    def test_private_socket_reuses_server_and_survives_operation_error(self):
        import os,socket,threading
        from invariant import manager_service as service
        with tempfile.TemporaryDirectory() as name:
            path=str(Path(name)/'socket');server=service.Server(path,service.Handler);server.project=Path('/project')
            counter=[]
            def reply(project,request):
                counter.append(request['n'])
                if request['n']==2:raise RuntimeError('expected operation failure')
                return {'pid':os.getpid(),'count':len(counter)}
            with patch.object(service,'reply',side_effect=reply):
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    replies=[]
                    for n in (1,2,3):
                        with socket.socket(socket.AF_UNIX) as connection:
                            connection.connect(path);connection.sendall((json.dumps({'n':n})+'\n').encode())
                            with connection.makefile('rb') as incoming:replies.append(json.loads(incoming.readline()))
                    self.assertTrue(replies[0]['ok']);self.assertFalse(replies[1]['ok']);self.assertTrue(replies[2]['ok'])
                    self.assertEqual(replies[0]['data']['pid'],replies[2]['data']['pid'])
                    self.assertEqual(replies[2]['data']['count'],3)
                finally:server.shutdown();server.server_close();thread.join()

    def test_observation_creates_no_trace_and_mutation_logs_only_numeric_metadata(self):
        import contextlib
        from invariant import manager_service as service
        with tempfile.TemporaryDirectory() as name:
            state=Path(name)
            with patch('invariant.storage_gc.gate',return_value=contextlib.nullcontext()),patch('invariant.workspace.state_for',return_value=state),patch.object(service.storage_worker,'operate',return_value={}):
                request={'project':'/project','operation':'engine','payload':{'method':'results'}}
                service.reply(Path('/project'),request)
                self.assertEqual(list((state/'traces').glob('*.jsonl')),[])
                request['payload']={'method':'submit','secret':'never-log-this'}
                service.reply(Path('/project'),request)
                output=(state/'traces/manager.jsonl').read_text()
                self.assertNotIn('never-log-this',output)
                self.assertEqual(json.loads(output)['stage'],'manager.engine')
