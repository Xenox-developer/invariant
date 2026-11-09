"""Storage boundaries, verified migration and host conflict recovery."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import Mock,patch

from invariant import docker_storage as storage, storage_migration as migration, storage_worker
from invariant.host_delivery import apply_bundle
from invariant.experiments import tree
from invariant.experiments.definitions import digest
from invariant.experiments.errors import ExperimentError


class StorageTests(unittest.TestCase):
    def test_mounts_cannot_escape_owned_volumes(self):
        mapping=[{'path':'/state','volume':'owned','daemon_path':'/volumes/owned/_data'}]
        with patch.dict(os.environ,{'INVARIANT_DOCKER_MANAGER':'1','INVARIANT_VOLUME_MAP':json.dumps(mapping)}):
            args=storage.translate_mounts(['create','--mount','type=bind,src=/state/work,dst=/workspace'])
            self.assertEqual(args[-1],'type=bind,src=/volumes/owned/_data/work,dst=/workspace')
            with self.assertRaises(RuntimeError):storage.translate_mounts(['create','--mount','type=bind,src=/Users/person,dst=/host'])
            with self.assertRaises(RuntimeError):storage.translate_mounts(['create','--mount','type=bind,src=/volumes/owned/_data/../../foreign,dst=/workspace'])
            self.assertEqual(storage.volume_ref('/state/work'),{'backend':'docker','volume':'owned','path':'work'})

    def test_inherited_desktop_mount_hints_are_cleared_before_start(self):
        info=[{'Config':{'Labels':{'desktop.docker.io/mounts/0/Source':'/host','io.invariant.role':'codex-workspace'}}}]
        image='sha256:'+'a'*64
        with patch.dict(os.environ,{'INVARIANT_DOCKER_MANAGER':'1'}),patch.object(storage,'docker',return_value=Mock(stdout=json.dumps(info).encode())):
            args=storage.clear_desktop_mount_hints(['create','--name','test',image])
        self.assertIn('desktop.docker.io/mounts/0/Source=',args)
        self.assertEqual(args[-1],image)

    def test_public_api_exposes_workspace_reference_and_project_id(self):
        manager=storage.Manager('/project',prepare=False)
        manager.map=[{'path':'/project','volume':'project'}]
        self.assertEqual(manager.public({'project':'/project','workspace':'/project/src'}),
                         {'project_ref':{'backend':'docker','volume':'project','path':'.'},'project_id':manager.key,'workspace_ref':{'backend':'docker','volume':'project','path':'src'}})

    def test_public_session_and_reviewer_default_to_the_container_client(self):
        from invariant import workspace
        from invariant.task_review import Reviewer
        marker=object()
        with patch.object(storage,'forward',return_value=0) as forward:
            self.assertEqual(workspace.run('chosen',effort='medium'),0)
            forward.assert_called_once_with(['run','--agent','codex','chosen','--effort','medium'])
        with patch.object(storage,'ContainerReviewer',return_value=marker) as reviewer:
            self.assertIs(Reviewer('/project'),marker)
            reviewer.assert_called_once_with('/project')

    def test_source_excludes_dependencies_and_agent_state(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            for folder in ('.invariant','node_modules','.venv','.cache'):
                (root/folder).mkdir();(root/folder/'private').write_text('cache')
            (root/'source.py').write_text('source')
            (root/'.invariant-import-interrupted').write_text('internal staging')
            self.assertEqual(set(storage.local_source_manifest(root)),{'source.py'})

    def test_historical_committed_plans_are_never_queued_for_host_delivery(self):
        with tempfile.TemporaryDirectory() as name:
            state=Path(name);folder=state/'experiments/apply-plans/plan-test';folder.mkdir(parents=True)
            (folder/'journal.json').write_text('{}')
            with patch.object(storage_worker.workspace,'state_for',return_value=state),patch('invariant.experiments.applying.load',return_value=({},folder)),patch('invariant.experiments.applying.read_journal',return_value={'state':'committed'}):
                self.assertEqual(storage_worker.pending_deliveries(state),[])
            with patch.object(storage_worker.workspace,'state_for',return_value=state),patch('invariant.experiments.applying.load',return_value=({'storage_layout':2},folder)),patch('invariant.experiments.applying.read_journal',return_value={'state':'committed'}):
                self.assertEqual(storage_worker.pending_deliveries(state),['plan-test'])

    def test_readiness_does_not_build_images_or_start_managers(self):
        with patch('invariant.dependencies.probe_docker',return_value={'status':'ready'}),patch.object(storage,'docker',return_value=Mock(returncode=0)),patch.object(storage,'ensure_image') as build,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(storage.readiness(['setup','--check','--json']),0)
            build.assert_not_called()

    def test_user_reset_after_delivery_is_not_reinterpreted_as_undelivered_work(self):
        with tempfile.TemporaryDirectory() as name:
            state=Path(name);folder=state/'experiments/apply-plans/plan-test';folder.mkdir(parents=True)
            (folder/'host-delivery.json').write_text(json.dumps({'state':'committed'}))
            before={'type':'file','sha256':'before'};after={'type':'file','sha256':'after'}
            mirror=state/'source-mirror.json';mirror.write_text(json.dumps({'main.py':before}))
            plan={'steps':[{'path':'main.py','before':before,'after':after}]}
            with patch.object(storage_worker.workspace,'state_for',return_value=state),patch('invariant.experiments.applying.load',return_value=(plan,folder)):
                storage_worker.reconcile_delivered_source(state)
                self.assertEqual(json.loads(mirror.read_text())['main.py'],after)
                # A later explicit user reset is a new source revision.
                mirror.write_text(json.dumps({'main.py':before}))
                storage_worker.reconcile_delivered_source(state)
                self.assertEqual(json.loads(mirror.read_text())['main.py'],before)

    def test_source_import_never_follows_an_agent_changed_parent(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name).resolve();project=root/'project';project.mkdir();outside=root/'private';outside.mkdir();state=root/'state';state.mkdir()
            (outside/'secret.txt').write_text('protected')
            (project/'safe').mkdir();baseline={'safe':tree.entry(project,'safe')}
            (state/'source-mirror.json').write_text(json.dumps(baseline))
            (project/'safe').rmdir();(project/'safe').symlink_to(outside,target_is_directory=True)
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w') as archive:
                directory=tarfile.TarInfo('safe');directory.type=tarfile.DIRTYPE;directory.mode=baseline['safe']['mode'];archive.addfile(directory)
                item=tarfile.TarInfo('safe/secret.txt');data=b'would-overwrite';item.size=len(data);archive.addfile(item,io.BytesIO(data))
            stream.seek(0)
            import sys
            with patch.object(storage_worker.workspace,'state_for',return_value=state),patch.object(sys,'stdin',io.TextIOWrapper(stream)),self.assertRaises((OSError,RuntimeError)):
                storage_worker.import_source(project)
            self.assertEqual((outside/'secret.txt').read_text(),'protected')

    def test_native_auth_homes_are_excluded_from_log_exports_regardless_of_name(self):
        with tempfile.TemporaryDirectory() as name:
            state=Path(name);home=state/'arbitrary-session-home';home.mkdir()
            (home/'models_cache.json').write_text('{}');(home/'auth.json').write_text('synthetic credential')
            (home/'trace.log').write_text('private runtime trace')
            (state/'runner.log').write_text('task output')
            with patch.object(storage_worker.workspace,'state_for',return_value=state):result=storage_worker.export_logs(state)
            self.assertEqual(result['files'],['runner.log'])
            self.assertFalse((Path(result['directory'])/'arbitrary-session-home').exists())

    def test_archive_cannot_follow_a_symlink_or_write_outside_destination(self):
        with tempfile.TemporaryDirectory() as name:
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w') as archive:
                item=tarfile.TarInfo('../escape');item.size=1;archive.addfile(item,io.BytesIO(b'x'))
            stream.seek(0)
            with tarfile.open(fileobj=stream) as archive,self.assertRaises(RuntimeError):storage_worker.unpack(archive,Path(name))


class MigrationTests(unittest.TestCase):
    def test_corrupt_copy_is_never_installed_and_verified_copy_is_recoverable(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source=root/'original';source.mkdir();(source/'file').write_text('history')
            (source/'auth.json').symlink_to('/personal/auth.json')
            expected=migration.inventory(source)
            with patch.object(Path,'home',return_value=root):
                payload={'key':'test','expected':expected}
                start=migration.worker(root,'migration_begin',payload)
                stage=Path(start['stage']);shutil.copytree(source,stage,symlinks=True)
                (stage/'file').write_text('broken')
                with self.assertRaises(RuntimeError):migration.worker(root,'migration_verify',{'key':'test'})
                self.assertEqual((source/'file').read_text(),'history')
                (stage/'file').write_text('history')
                verified=migration.worker(root,'migration_verify',{'key':'test'})
                self.assertTrue(verified['restore_verified'])
                result=migration.worker(root,'migration_install',{'key':'test','relative':'workspaces/project'})
                self.assertEqual(result['phase'],'installed')
                self.assertEqual(migration.inventory(Path(result['destination'])),expected)
                self.assertTrue((source/'file').exists())
                self.assertTrue((Path(result['destination'])/'auth.json').is_symlink())


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.project=self.root/'source';self.project.mkdir()
        self.folder=self.root/'result';(self.folder/'desired').mkdir(parents=True);(self.folder/'backup').mkdir()
        self.file=self.project/'main.py';self.file.write_text('before')
        before=tree.entry(self.project,'main.py');self.file.write_text('after');after=tree.entry(self.project,'main.py');self.file.write_text('before')
        (self.folder/'desired/0').write_text('after');(self.folder/'backup/0').write_text('before')
        self.plan={'project':str(self.project),'plan_id':'plan-test','guards':{'main.py':{'entry':before}},'protected_inputs':{},
                   'steps':[{'path':'main.py','before':before,'after':after,'temporary':'.apply-temp','undo_temporary':'.undo-temp'}]}
        self.manifest={**self.plan,'digest':digest(self.plan)}
        self.manager=Mock();self.manager.project=self.project
        self.write()
    def write(self): (self.folder/'manifest.json').write_text(json.dumps(self.manifest))
    def test_passed_delivery_is_verified_and_replay_is_idempotent(self):
        result=apply_bundle(self.manager,self.folder)
        self.assertEqual(self.file.read_text(),'after');self.assertTrue(result['verified'])
        self.manifest['journal']=result;self.write()
        self.assertEqual(apply_bundle(self.manager,self.folder)['state'],'committed')
    def test_external_edit_is_preserved_and_conflict_is_durable(self):
        self.file.write_text('external edit')
        with self.assertRaises(ExperimentError):apply_bundle(self.manager,self.folder)
        self.assertEqual(self.file.read_text(),'external edit')
        self.assertEqual(json.loads((self.folder/'host-delivery.json').read_text())['state'],'conflicted')
    def test_crash_after_write_rolls_back_then_rechecks_without_model_calls(self):
        self.file.write_text('after')
        self.manifest['journal']={'state':'applying','progress':['intent'],'root_identity':tree.identity(self.project),'plan_digest':self.manifest['digest']}
        self.write();result=apply_bundle(self.manager,self.folder)
        self.assertEqual(result['state'],'committed');self.assertEqual(self.file.read_text(),'after')
    def test_crash_during_staging_retains_partial_bytes_as_result_and_resumes(self):
        (self.project/'.apply-temp').write_text('part')
        self.manifest['journal']={'state':'applying','progress':['intent'],'root_identity':tree.identity(self.project),'plan_digest':self.manifest['digest']}
        self.write();result=apply_bundle(self.manager,self.folder)
        self.assertEqual(result['state'],'committed')
        self.assertFalse((self.project/'.apply-temp').exists())
        self.assertEqual(next((self.folder/'recovered-temporaries').iterdir()).read_text(),'part')

    def test_recovery_preserves_edits_made_after_crash(self):
        self.file.write_text('external')
        self.manifest['journal']={'state':'applying','progress':['intent'],'root_identity':tree.identity(self.project),'plan_digest':self.manifest['digest']}
        self.write()
        with self.assertRaises(ExperimentError):apply_bundle(self.manager,self.folder)
        self.assertEqual(self.file.read_text(),'external')


if __name__=='__main__':unittest.main()
